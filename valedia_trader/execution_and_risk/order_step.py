"""The order step: from the paper book's decisions to orders at the broker. Test environment only.

Once per trading day, inside the regular session, `run_order_step` compares what the paper book wants to hold
with what this program owns at the broker and sends one order per symbol for the difference, through the order
ledger (execution_and_risk/orders.py). It decides nothing itself. Design: docs/specs/2026-11-13-order-step-design.md.

Three books are kept apart: the paper book (simulated, the research record, only read here); the broker book (units
per symbol this program owns at the broker: never stored, always the signed sum of the order ledger's fills); the
baseline (what the account held before the first order step: recorded once, never sold, never counted as owned).

One run, in order. A failure in steps 1 to 6 sends nothing; the reason is journaled and returned.

 1. environment  the venue must be the test environment (TEST_ENVIRONMENT) and the ledger must treat the adapter's
                 NotTransmitted and OrderRejected as not sent; checked before any venue call.
 2. session      inside the regular session of a trading day, SESSION_AFTER_OPEN after the open to
                 SESSION_BEFORE_CLOSE before the close (09:35 to 15:45 New York time on a full day; earlier on a
                 half-day), from the exchange calendar. Outside it nothing is read from the venue.
 3. baseline     recorded from the venue's positions when none is recorded and the ledger has no orders; with no
                 baseline and orders on the ledger, the run stops.
 4. reconcile    reconcile (positions given: baseline plus the fills already applied; the ledger adds the rest);
                 ask the venue to cancel every order of this ledger still open; reconcile again. Every new fill is
                 journaled once (keyed by its fill id) and marked applied. A blocked ledger, or an order still open
                 at the broker, stops the run.
 5. targets      per symbol, each strategy's paper units held plus pending, summed, times capital / paper equity.
 6. prices       one quote per symbol with its time; older than QUOTE_MAX_AGE_SECONDS, missing or not a positive
                 finite number: no order for that symbol.
 7. differences  target minus broker book, rounded toward zero (whole shares, or FRACTIONAL_DECIMALS decimals with
                 `fractional`); a sale never exceeds the broker book; less than the paper trader's smallest trade
                 is dropped.
 8. risk gate    risk_gate.check_order against the broker account as it is now (equity = the venue's cash plus
                 holdings at the quotes; the day's loss against the equity recorded at the last run on an earlier
                 day; the fall from the recorded peak; positions held), latch and halt files honoured.
 9. caps         sales first, then purchases, largest value first; purchases stop once the cash the venue reports,
                 less CASH_KEPT, would be exceeded at the limit prices; at most max_orders_per_day sent in one New
                 York day across runs. What is not sent is journaled, not queued.
10. send         a day limit order in the regular session through ledger.intend and ledger.submit_once. A refusal
                 is journaled and the run goes on; an unknown outcome stops the run at once.
11. after        reconcile once more, journal the fills, return and journal a summary.

The order step keeps a small sqlite state file (OrderStepState) beside the ledger, one per venue name: the baseline,
the equity of each run, and the orders sent each day. Every write is one transaction. Everything a run needs is
passed in; nothing here builds a broker client.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Callable

import pandas as pd

from valedia_trader.execution_and_risk.orders import OrderError, OrderLedger, OrdersBlocked, allocate, net_orders
from valedia_trader.research_loop import risk_gate
from valedia_trader.venues.webull import NotTransmitted, OrderRejected

__all__ = ["TEST_ENVIRONMENT", "NOT_SENT", "PaperTargets", "OrderStepState", "OrderStepError", "run_order_step",
           "paper_targets", "open_ledger", "ledger_path_for", "state_path_for", "broker_book", "targets", "strategy_holdings",
           "round_units", "limit_price", "market_window", "max_orders_per_day", "minute_quotes", "wall_clock"]

TEST_ENVIRONMENT = "sandbox"                      # the only environment a run accepts (WebullVenue.environment)
NOT_SENT = (NotTransmitted, OrderRejected)        # the adapter's answers that mean no order exists
NEW_YORK = "America/New_York"
SESSION_AFTER_OPEN = pd.Timedelta(minutes=5)      # 09:35 on a 09:30 open
SESSION_BEFORE_CLOSE = pd.Timedelta(minutes=15)   # 15:45 on a 16:00 close, 12:45 on a 13:00 half-day
QUOTE_MAX_AGE_SECONDS = 20 * 60
QUOTE_MAX_AHEAD_SECONDS = 60                      # a quote stamped later than this after now is not believed
DEFAULT_MAX_ORDERS_PER_DAY = 40
BUY_ABOVE = Decimal("0.01")                       # purchase limit: quote plus 1%, rounded down to the cent
SELL_BELOW = Decimal("0.01")                      # sale limit: quote less 1%, rounded up to the cent
SELL_BELOW_REDUCING = Decimal("0.02")             # ... less 2% when the sale reduces risk
CASH_KEPT = 0.01                                  # purchases may use the venue's cash less 1%
FRACTIONAL_DECIMALS = 5
OPEN_STATES = ("accepted", "partly_filled", "sent", "unknown")   # orders that may still be open at the broker
UNITS_EPS = 1e-12
CENT = Decimal("0.01")
MAX_TEXT = 200


class OrderStepError(RuntimeError):
    pass


def wall_clock() -> float:
    """The time in seconds; the command line passes this to a run (tests pass their own clock)."""
    return time.time()


def _describe(exc: BaseException) -> str:
    try:
        text = str(exc)
    except Exception:
        text = ""
    return f"{type(exc).__name__}: {text[:MAX_TEXT]}" if text else type(exc).__name__


def _finite(x) -> bool:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    try:
        return math.isfinite(float(x))
    except (OverflowError, ValueError):
        return False


def _is_crypto(symbol: str) -> bool:
    return symbol.upper().endswith("-USD")


# ---- pure pieces


@dataclass(frozen=True)
class PaperTargets:
    """What the paper book wants: per strategy and symbol, units held plus pending (PaperTrader.wanted_units); the
    paper equity those units were sized against; the capital per strategy (the paper step's `share`); and the paper
    trader's `min_trade_pct`."""
    wanted: dict
    equity: float
    share: float
    min_trade_pct: float


def paper_targets(trader) -> PaperTargets:
    """The paper targets of a PaperTrader, as of its latest day."""
    history = trader.state.get("history") or []
    if not history:
        raise OrderStepError("the paper book has not run a day yet; there is nothing to follow")
    last = history[-1]
    return PaperTargets(wanted=trader.wanted_units(), equity=float(last["equity"]),
                        share=float(last.get("capital_per_strategy") or last["equity"]),
                        min_trade_pct=float(trader.min_trade_pct))


def _check_paper(p) -> PaperTargets:
    if not isinstance(p, PaperTargets):
        raise OrderStepError(f"the paper targets must be PaperTargets, not {type(p).__name__}")
    for name in ("equity", "share"):
        v = getattr(p, name)
        if not _finite(v) or v <= 0:
            raise OrderStepError(f"the paper {name} must be a positive finite number, not {v!r}")
    if not _finite(p.min_trade_pct) or p.min_trade_pct < 0:
        raise OrderStepError(f"min_trade_pct must be a finite number at least 0, not {p.min_trade_pct!r}")
    if not isinstance(p.wanted, dict):
        raise OrderStepError("the paper's wanted units must be a dict of strategies")
    for strategy, book in p.wanted.items():
        if not isinstance(strategy, str) or not isinstance(book, dict):
            raise OrderStepError(f"the paper's wanted units for {strategy!r} are not a dict of symbols")
        for sym, units in book.items():
            if not isinstance(sym, str) or not sym or not _finite(units):
                raise OrderStepError(f"the paper wants {units!r} of {sym!r} for {strategy}: not a symbol and a finite number")
    return p


def targets(paper: PaperTargets, capital: float) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """(per symbol, per strategy and symbol) target units at the broker: the paper's wanted units times
    capital / paper equity."""
    scale = float(capital) / float(paper.equity)
    per_strategy = {s: {sym: float(u) * scale for sym, u in book.items()} for s, book in paper.wanted.items()}
    per_symbol: dict[str, list[float]] = {}
    for book in per_strategy.values():
        for sym, u in book.items():
            per_symbol.setdefault(sym, []).append(u)
    return {sym: math.fsum(us) for sym, us in per_symbol.items()}, per_strategy


def broker_book(fills) -> dict[str, float]:
    """Units per symbol this program owns at the broker: the signed sum of the given ledger fill rows."""
    by: dict[str, list[float]] = {}
    for f in fills:
        by.setdefault(f["symbol"], []).append(float(f["units"]))
    return {sym: math.fsum(us) for sym, us in by.items()}


UNATTRIBUTED = "(unattributed)"


def strategy_holdings(orders) -> dict[str, dict[str, float]]:
    """Per strategy and symbol, the units the broker filled for it: each ledger order's filled amount shared out to
    its parts with orders.allocate. An order without usable parts counts for UNATTRIBUTED. Summed over strategies,
    a symbol's holdings are its broker book."""
    out: dict[str, dict[str, list[float]]] = {}
    for o in orders:
        filled = float(o.get("filled_units") or 0.0)
        if filled <= 0:
            continue
        signed = math.copysign(filled, o["units"])
        try:
            shares = allocate(o["parts"], signed) if o.get("parts") else {UNATTRIBUTED: signed}
        except (OrderError, KeyError, TypeError):
            shares = {UNATTRIBUTED: signed}
        for strategy, units in shares.items():
            out.setdefault(strategy, {}).setdefault(o["symbol"], []).append(float(units))
    return {s: {sym: math.fsum(us) for sym, us in book.items()} for s, book in out.items()}


def round_units(units: float, fractional: bool = False) -> float:
    """Toward zero: whole shares, or FRACTIONAL_DECIMALS decimals when `fractional`. Refuses a non-finite number."""
    if not _finite(units):
        raise OrderStepError(f"units must be a finite number, not {units!r}")
    if not fractional:
        return float(math.trunc(float(units)))
    step = Decimal(1).scaleb(-FRACTIONAL_DECIMALS)
    return float(Decimal(repr(float(units))).quantize(step, rounding=ROUND_DOWN))


def limit_price(units: float, quote: float, reduces_risk: bool = False) -> float | None:
    """A purchase at the quote plus 1% rounded down to the cent; a sale at the quote less 1% (less 2% when it reduces
    risk) rounded up to the cent. None when the result is not a positive price."""
    if not _finite(quote) or quote <= 0 or not _finite(units) or units == 0:
        return None
    q = Decimal(repr(float(quote)))
    if units > 0:
        price = (q * (1 + BUY_ABOVE)).quantize(CENT, rounding=ROUND_FLOOR)
    else:
        price = (q * (1 - (SELL_BELOW_REDUCING if reduces_risk else SELL_BELOW))).quantize(CENT, rounding=ROUND_CEILING)
    return float(price) if price > 0 else None


def max_orders_per_day(limits: dict | None) -> int:
    """The limits file's optional max_orders_per_day, else DEFAULT_MAX_ORDERS_PER_DAY."""
    if limits and "max_orders_per_day" in limits:
        return int(limits["max_orders_per_day"])
    return DEFAULT_MAX_ORDERS_PER_DAY


def _default_calendar():
    import exchange_calendars
    return exchange_calendars.get_calendar("XNYS")


def market_window(calendar, now: float) -> tuple[float | None, float | None, str | None]:
    """(start, end, None) in seconds when `now` falls on a trading day of `calendar`; (None, None, why) when it does
    not. The window runs SESSION_AFTER_OPEN after the open to SESSION_BEFORE_CLOSE before the close."""
    ny = pd.Timestamp(now, unit="s", tz="UTC").tz_convert(NEW_YORK)
    day = str(ny.date())
    try:
        if not calendar.is_session(day):
            return None, None, f"{day} ({ny.day_name()}) is not a trading day"
        start = pd.Timestamp(calendar.session_open(day)) + SESSION_AFTER_OPEN
        end = pd.Timestamp(calendar.session_close(day)) - SESSION_BEFORE_CLOSE
    except Exception as exc:
        return None, None, f"the exchange calendar could not answer for {day}: {_describe(exc)}"
    return start.timestamp(), end.timestamp(), None


def _hhmm(seconds: float) -> str:
    return pd.Timestamp(seconds, unit="s", tz="UTC").tz_convert(NEW_YORK).strftime("%H:%M")


def _quote_time(t) -> float | None:
    if _finite(t):
        return float(t)
    if isinstance(t, datetime):                       # pandas Timestamps are datetimes too
        if t.tzinfo is None:
            return None                               # a time without a zone cannot be compared with the clock
        return float(t.timestamp())
    return None


def usable_quote(raw, now: float) -> tuple[float | None, str | None]:
    """(price, None) for a usable quote, else (None, why). A quote is (price, time) or {"price", "time"}; the time
    is seconds or a datetime with a zone."""
    if raw is None:
        return None, "no quote"
    try:
        price, when = (raw["price"], raw["time"]) if isinstance(raw, dict) else (raw[0], raw[1])
    except (KeyError, IndexError, TypeError):
        return None, "the quote is not a price and a time"
    if not _finite(price) or price <= 0:
        return None, f"the quote {price!r} is not a positive finite number"
    t = _quote_time(when)
    if t is None:
        return None, "the quote's time is not usable"
    age = now - t
    if age > QUOTE_MAX_AGE_SECONDS:
        return None, f"the quote is {age / 60:.1f} minutes old, more than {QUOTE_MAX_AGE_SECONDS // 60}"
    if age < -QUOTE_MAX_AHEAD_SECONDS:
        return None, f"the quote is stamped {-age:.0f} s after now"
    return float(price), None


def minute_quotes(symbols) -> dict:
    """The default quote function: the last one-minute bar of today from the price source the repository already
    uses (yfinance), as {symbol: (close, time of the bar in seconds)}. A symbol without a bar is left out. No test
    calls this; it reaches the network."""
    import yfinance as yf
    symbols = list(symbols)
    if not symbols:
        return {}
    raw = yf.download(symbols, period="1d", interval="1m", auto_adjust=False, progress=False, threads=False)
    close = raw["Close"]
    if isinstance(close, pd.Series):
        close = close.to_frame(symbols[0])
    out = {}
    for sym in symbols:
        if sym not in close.columns:
            continue
        s = close[sym].dropna()
        if s.empty:
            continue
        ts = pd.Timestamp(s.index[-1])
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
        out[sym] = (float(s.iloc[-1]), float(ts.timestamp()))
    return out


# ---- files


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(name)) or "venue"


def ledger_path_for(directory: str | Path, venue_name: str) -> Path:
    """The order ledger's file for a venue in `directory`: one ledger per venue name (Webull's carry the environment)."""
    return Path(directory) / f"orders-{_safe_name(venue_name)}.sqlite"


def state_path_for(ledger_path: str | Path, venue_name: str) -> Path:
    """The order step's state file for a venue: beside the order ledger's file."""
    return Path(ledger_path).with_name(f"order_step-{_safe_name(venue_name)}.sqlite")


def open_ledger(path: str | Path, clock: Callable[[], float] = time.time) -> OrderLedger:
    """An order ledger that treats the adapter's NotTransmitted and OrderRejected as not sent."""
    return OrderLedger(path, not_sent=NOT_SENT, clock=clock)


STATE_LAYOUT = 1
STATE_SCHEMA = [
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS baseline (symbol TEXT PRIMARY KEY, units REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS equity (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, time REAL NOT NULL, "
    "equity REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS sends (tag TEXT PRIMARY KEY, day TEXT NOT NULL, time REAL NOT NULL, outcome TEXT)",
]
NOT_COUNTED = ("refused", "not sent")             # outcomes that are not an order sent


class OrderStepState:
    """The order step's own small file: the baseline (recorded once), the equity at each run, and each order handed
    to the ledger for sending, with its outcome. Every write is one IMMEDIATE transaction, so a crash at any point
    leaves the file as it was before that write or after it."""

    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time) -> None:
        self.path, self.clock = str(path), clock
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        try:
            self._db.execute("PRAGMA synchronous = FULL")
            with self._write():
                version = self._db.execute("PRAGMA user_version").fetchone()[0]
                tables = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                tables.discard("sqlite_sequence")
                if tables and (version != STATE_LAYOUT or not tables <= {"meta", "baseline", "equity", "sends"}):
                    raise OrderStepError(f"{path} is not an order-step state file of layout {STATE_LAYOUT}; it is not opened")
                if not tables:
                    if version != 0:
                        raise OrderStepError(f"{path} is a database of another kind; it is not opened")
                    for statement in STATE_SCHEMA:
                        self._db.execute(statement)
                    self._db.execute(f"PRAGMA user_version = {STATE_LAYOUT}")
        except BaseException:
            self._db.close()
            raise

    @contextmanager
    def _write(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            try:
                self._db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        self._db.execute("COMMIT")

    def close(self) -> None:
        self._db.close()

    def baseline(self) -> dict[str, float] | None:
        if not self._db.execute("SELECT 1 FROM meta WHERE key = 'baseline_recorded'").fetchone():
            return None
        return {sym: float(u) for sym, u in self._db.execute("SELECT symbol, units FROM baseline ORDER BY symbol")}

    def record_baseline(self, positions: dict) -> dict[str, float]:
        clean = {}
        for sym, units in positions.items():
            if not isinstance(sym, str) or not sym or not _finite(units) or units < 0:
                raise OrderStepError(f"the venue holds {units!r} of {sym!r}: not a symbol and a finite number at least 0")
            if units > 0:
                clean[sym] = float(units)
        with self._write():
            if self._db.execute("SELECT 1 FROM meta WHERE key = 'baseline_recorded'").fetchone():
                raise OrderStepError("a baseline is already recorded; it is recorded once")
            self._db.executemany("INSERT INTO baseline (symbol, units) VALUES (?, ?)", sorted(clean.items()))
            self._db.execute("INSERT INTO meta (key, value) VALUES ('baseline_recorded', ?)", (repr(float(self.clock())),))
        return clean

    def previous_equity(self, day: str) -> float | None:
        """The equity recorded at the latest run on a day before `day`."""
        row = self._db.execute("SELECT equity FROM equity WHERE day < ? ORDER BY day DESC, id DESC LIMIT 1", (day,)).fetchone()
        return float(row[0]) if row else None

    def peak(self) -> float | None:
        row = self._db.execute("SELECT MAX(equity) FROM equity").fetchone()
        return float(row[0]) if row and row[0] is not None else None

    def record_equity(self, day: str, equity: float) -> None:
        if not _finite(equity):
            raise OrderStepError(f"equity must be a finite number, not {equity!r}")
        with self._write():
            self._db.execute("INSERT INTO equity (day, time, equity) VALUES (?, ?, ?)", (day, float(self.clock()), float(equity)))

    def sent_on(self, day: str) -> int:
        """Orders handed to the ledger for sending on `day` and not known to have stayed unsent. An order whose outcome
        was never written (a crash during the call) counts until settle_sends says otherwise."""
        marks = ",".join("?" * len(NOT_COUNTED))
        return self._db.execute(f"SELECT COUNT(*) FROM sends WHERE day = ? AND (outcome IS NULL OR outcome NOT IN ({marks}))",
                                (day, *NOT_COUNTED)).fetchone()[0]

    def note_sending(self, tag: str, day: str) -> None:
        with self._write():
            self._db.execute("INSERT INTO sends (tag, day, time, outcome) VALUES (?, ?, ?, NULL)", (tag, day, float(self.clock())))

    def note_outcome(self, tag: str, outcome: str) -> None:
        with self._write():
            self._db.execute("UPDATE sends SET outcome = ? WHERE tag = ?", (outcome, tag))

    def settle_sends(self, ledger: OrderLedger) -> int:
        """Give every send whose outcome was never written the outcome the ledger now shows: an order still `intended`
        was never handed to the venue ("not sent"). Returns how many were settled."""
        rows = [r[0] for r in self._db.execute("SELECT tag FROM sends WHERE outcome IS NULL")]
        n = 0
        for tag in rows:
            rec = ledger.order(tag)
            outcome = "not sent" if rec is None or rec["state"] == "intended" else rec["state"]
            self.note_outcome(tag, outcome)
            n += 1
        return n


class _RunLock:
    """One run at a time per state file: an exclusive lock on a file beside it, released when the process ends."""

    def __init__(self, state_path: str) -> None:
        self.fh = None
        self.path = None if state_path == ":memory:" else state_path + ".lock"

    def acquire(self) -> bool:
        if self.path is None:
            return True
        import fcntl
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self.fh = fh
        return True

    def release(self) -> None:
        if self.fh is not None:
            import fcntl
            try:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            finally:
                self.fh.close()
                self.fh = None


# ---- one run


class _Stop(Exception):
    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(reason)
        self.stage, self.reason = stage, reason


def run_order_step(*, ledger: OrderLedger, venue, paper, quotes: Callable, journal, limits_file, state,
                   clock: Callable[[], float] = wall_clock, calendar=None, halt_file=risk_gate.HALT_FILE,
                   latch_file=None, capital: float | None = None, fractional: bool = False) -> dict:
    """One order step (see the module notes). `paper` is PaperTargets or a callable giving them; `quotes(symbols)`
    gives {symbol: (price, time)}; `state` is an OrderStepState or the path of its file; `calendar` answers
    is_session, session_open and session_close (default: the XNYS calendar). Returns the summary, also journaled.
    Never raises because of the venue, the quotes or the paper book; a crash of the process mid-run is safe."""
    return _Run(ledger, venue, paper, quotes, journal, limits_file, state, clock, calendar, halt_file, latch_file,
                capital, fractional).go()


class _Run:
    def __init__(self, ledger, venue, paper, quotes, journal, limits_file, state, clock, calendar, halt_file,
                 latch_file, capital, fractional) -> None:
        self.ledger, self.venue, self.paper, self.quotes, self.journal = ledger, venue, paper, quotes, journal
        self.limits_file, self.state_arg, self.clock, self.calendar = limits_file, state, clock, calendar
        self.halt_file, self.latch_file, self.capital, self.fractional = halt_file, latch_file, capital, bool(fractional)
        self.name = "?"
        self.batch = None
        self.day = None
        self.state: OrderStepState | None = None
        self.sent: list[dict] = []
        self.refused: list[dict] = []
        self.left_over: list[dict] = []
        self.skipped: list[dict] = []
        self.cancels: list[dict] = []
        self.fills_journaled = 0
        self.target_units: dict[str, float] = {}
        self.broker_equity = None
        self.paper_equity = None
        self.window_end = None
        self._seen_fill_ids: set | None = None

    # -- helpers
    def _write(self, entry: dict) -> None:
        self.journal.write(self.day, {"type": "order_step", "venue": self.name, "batch": self.batch, **entry})

    def _now(self) -> float:
        now = self.clock()
        if not _finite(now):
            raise _Stop("clock", f"the clock gave {now!r}, not a finite number of seconds")
        return float(now)

    def go(self) -> dict:
        try:
            now = self._now()
            self.day = str(pd.Timestamp(now, unit="s", tz="UTC").tz_convert(NEW_YORK).date())
        except _Stop as stop:
            self.day = str(pd.Timestamp.now(tz=NEW_YORK).date())
            return self._finish("stopped", stop.stage, stop.reason)
        stamp = pd.Timestamp(now, unit="s", tz="UTC").tz_convert(NEW_YORK)
        self.batch = f"os-{stamp:%Y%m%d-%H%M%S}-{os.urandom(3).hex()}"
        try:
            self._environment()                          # 1
            self._session(now)                           # 2
        except _Stop as stop:
            return self._finish("stopped", stop.stage, stop.reason)
        path = self.state_arg.path if isinstance(self.state_arg, OrderStepState) else str(self.state_arg)
        lock = _RunLock(path)
        if not lock.acquire():
            return self._finish("stopped", "session", "another order step is running on this venue's state file")
        try:
            try:
                self.state = self.state_arg if isinstance(self.state_arg, OrderStepState) else OrderStepState(path, self.clock)
            except (OrderStepError, sqlite3.Error, OSError) as exc:
                return self._finish("stopped", "baseline", f"the order step's state file could not be opened: {_describe(exc)}")
            try:
                return self._rest(now)
            finally:
                if not isinstance(self.state_arg, OrderStepState):
                    self.state.close()
        finally:
            lock.release()

    def _rest(self, now: float) -> dict:
        try:
            baseline = self._baseline()                  # 3
            self._reconcile_and_cancel(baseline)         # 4
            paper = self._paper()                        # 5
            book = broker_book(self.ledger.fills())
            capital = paper.equity if self.capital is None else self.capital
            if not _finite(capital) or capital <= 0:
                raise _Stop("targets", f"the capital must be a positive finite number, not {capital!r}")
            self.paper_equity = paper.equity
            self.target_units, per_strategy = targets(paper, float(capital))
            min_value = paper.min_trade_pct * paper.share * float(capital) / paper.equity
            prices, why_not = self._prices(sorted(set(self.target_units) | set(book) | set(baseline)), now)   # 6
            cash = self._cash()
        except _Stop as stop:
            return self._finish("stopped", stop.stage, stop.reason)
        except (OrderError, OrderStepError, sqlite3.Error, OSError) as exc:
            return self._finish("stopped", "reconcile", f"the ledger, the state file or the journal failed: {_describe(exc)}")
        try:
            candidates = self._differences(book, per_strategy, prices, why_not, min_value)   # 7
            self._send(candidates, baseline, book, prices, cash)                            # 8, 9, 10
        except _Stop as stop:
            return self._finish("stopped", stop.stage, stop.reason)
        except (OrderError, OrderStepError, sqlite3.Error, OSError) as exc:   # nothing more is sent
            return self._finish("stopped", "send", f"the ledger, the state file or the journal failed: {_describe(exc)}; "
                                                   "nothing more is sent and the next run's reconcile settles what was")
        try:
            self._reconcile(baseline, "after")          # 11
        except (OrderError, sqlite3.Error, OSError) as exc:
            return self._finish("stopped", "after", f"the last reconcile could not be completed: {_describe(exc)}")
        return self._finish("done", None, None)

    # -- 1
    def _environment(self) -> None:
        try:
            name = self.venue.name
        except Exception:
            name = None
        if not isinstance(name, str) or not name:
            raise _Stop("environment", "the venue has no name")
        self.name = name
        try:
            env = self.venue.environment
        except Exception:
            env = None
        if env != TEST_ENVIRONMENT:
            raise _Stop("environment", f"the order step runs against the test environment ({TEST_ENVIRONMENT}) only; "
                                       f"this venue is {env!r}. Live trading is not built")
        not_sent = tuple(getattr(self.ledger, "not_sent", ()) or ())
        missing = [c.__name__ for c in NOT_SENT if c not in not_sent]
        broader = [getattr(t, "__name__", repr(t)) for t in not_sent if not (isinstance(t, type) and issubclass(t, NOT_SENT))]
        if missing or broader:                           # anything broader would read an unknown outcome as "not sent"
            raise _Stop("environment", "the order ledger must treat exactly NotTransmitted and OrderRejected as not sent "
                                       f"(missing: {missing or 'none'}; not allowed: {broader or 'none'}); open it with open_ledger")

    # -- 2
    def _session(self, now: float) -> None:
        if self.calendar is None:
            try:
                self.calendar = _default_calendar()
            except Exception as exc:
                raise _Stop("session", f"the exchange calendar could not be loaded: {_describe(exc)}") from None
        start, end, why = market_window(self.calendar, now)
        if why:
            raise _Stop("session", why)
        if not start <= now <= end:
            raise _Stop("session", f"outside the regular session window {_hhmm(start)} to {_hhmm(end)} New York time "
                                   f"(it is {_hhmm(now)})")
        self.window_end = end

    # -- 3
    def _baseline(self) -> dict[str, float]:
        base = self.state.baseline()
        if base is not None:
            return base
        if self.ledger.orders():
            raise _Stop("baseline", "no baseline is recorded but the order ledger already has orders; the account's "
                                    "positions can no longer tell the owner's holdings from this program's. A person must look")
        try:
            held = dict(self.venue.positions())
        except Exception as exc:
            raise _Stop("baseline", f"the venue's positions could not be read: {_describe(exc)}") from None
        try:
            base = self.state.record_baseline(held)
        except OrderStepError as exc:
            raise _Stop("baseline", str(exc)) from None
        self._write({"event": "baseline", "positions": base})
        return base

    # -- 4
    def _applied_positions(self, baseline: dict) -> dict[str, float]:
        applied = broker_book(f for f in self.ledger.fills() if f["applied"])
        out = dict(baseline)
        for sym, u in applied.items():
            out[sym] = math.fsum((out.get(sym, 0.0), u))
        return out

    def _reconcile(self, baseline: dict, when: str) -> dict:
        result = self.ledger.reconcile(self.venue, self._applied_positions(baseline))
        self._journal_fills()
        return result

    def _journal_fills(self) -> None:
        if self._seen_fill_ids is None:
            try:
                self._seen_fill_ids = {e.get("fill_id") for e in self.journal.read()
                                       if e.get("type") == "order_step" and e.get("event") == "fill" and e.get("venue") == self.name}
            except Exception as exc:
                raise _Stop("reconcile", f"the journal could not be read to find the fills already written: {_describe(exc)}") from None
        for f in self.ledger.unapplied_fills():
            if f["id"] not in self._seen_fill_ids:
                self._write({"event": "fill", "fill_id": f["id"], "tag": f["tag"], "symbol": f["symbol"], "units": f["units"],
                             "price": f["price"], "price_covers_units": f["price_covers_units"], "fee": f["fee"]})
                self._seen_fill_ids.add(f["id"])
                self.fills_journaled += 1
            self.ledger.mark_applied([f["id"]])

    def _reconcile_and_cancel(self, baseline: dict) -> None:
        self.state.settle_sends(self.ledger)
        self._reconcile(baseline, "first")
        for o in self.ledger.orders(OPEN_STATES):
            try:
                answer = self.venue.cancel(o["tag"])
                word = answer.get("status") if isinstance(answer, dict) else None
                word = word if isinstance(word, str) else "an answer that is not a cancel word"
            except Exception as exc:
                word = f"error: {_describe(exc)}"
            self.cancels.append({"tag": o["tag"], "symbol": o["symbol"], "answer": word[:MAX_TEXT]})
            self._write({"event": "cancel", "tag": o["tag"], "symbol": o["symbol"], "answer": word[:MAX_TEXT]})
        second = self._reconcile(baseline, "second")
        still = self.ledger.orders(OPEN_STATES)
        blocked = self.ledger.blocked()
        if still or blocked:
            reasons = []
            if still:
                reasons.append(f"{len(still)} order{'' if len(still) == 1 else 's'} of this ledger "
                               f"{'is' if len(still) == 1 else 'are'} still open at the broker after the cancel requests "
                               f"({', '.join(o['tag'] + ' ' + o['state'] for o in still)}); the next run asks again")
            reasons += [r for r in blocked if not any(r.startswith(f"order {o['tag']} ") for o in still)]
            if second.get("position_differences"):
                reasons.append(f"positions differ from baseline plus broker book: {json.dumps(second['position_differences'], default=str)}")
            raise _Stop("reconcile", "; ".join(reasons)[:2000])

    # -- 5
    def _paper(self) -> PaperTargets:
        try:
            p = self.paper() if callable(self.paper) else self.paper
            return _check_paper(p)
        except Exception as exc:
            raise _Stop("targets", f"the paper targets could not be read: {_describe(exc)}") from None

    # -- 6
    def _prices(self, symbols: list[str], now: float) -> tuple[dict[str, float], dict[str, str]]:
        if not symbols:
            return {}, {}
        try:
            raw = self.quotes(symbols)
            raw = dict(raw or {})
        except Exception as exc:
            raise _Stop("prices", f"the quotes could not be read: {_describe(exc)}") from None
        prices, why_not = {}, {}
        now = self._now()
        for sym in symbols:
            price, why = usable_quote(raw.get(sym), now)
            if price is None:
                why_not[sym] = why
            else:
                prices[sym] = price
        return prices, why_not

    def _cash(self) -> float | None:
        try:
            answer = self.venue.cash()
        except Exception as exc:
            raise _Stop("prices", f"the venue's cash could not be read: {_describe(exc)}") from None
        cash = answer.get("cash") if isinstance(answer, dict) else None
        if isinstance(cash, str):
            try:
                cash = float(cash)
            except ValueError:
                cash = None
        return float(cash) if _finite(cash) else None

    # -- 7
    def _differences(self, book, per_strategy, prices, why_not, min_value) -> list[dict]:
        holdings = strategy_holdings(self.ledger.orders())
        out = []
        for sym in sorted(set(self.target_units) | set(book)):
            target, owned = self.target_units.get(sym, 0.0), book.get(sym, 0.0)
            delta = target - owned
            if abs(delta) <= UNITS_EPS:
                continue
            if _is_crypto(sym):
                self.skipped.append({"symbol": sym, "reason": "crypto is not traded by the order step"})
                continue
            units = round_units(delta, self.fractional)
            if units < 0:
                units = round_units(max(units, -max(owned, 0.0)), self.fractional)   # a sale never exceeds the book
            if abs(units) <= UNITS_EPS:
                continue
            quote = prices.get(sym)
            if quote is None:
                reason = why_not.get(sym, "no quote")
                self.skipped.append({"symbol": sym, "units": units, "reason": reason})
                self._write({"event": "no_quote", "symbol": sym, "units": units, "reason": reason})
                continue
            value = abs(units) * quote
            if value < min_value:
                self.skipped.append({"symbol": sym, "units": units, "reason": f"worth {value:.2f}, less than the smallest "
                                                                              f"trade of {min_value:.2f}"})
                continue
            deltas = [{"strategy": s, "symbol": sym, "units": book_s.get(sym, 0.0) - holdings.get(s, {}).get(sym, 0.0)}
                      for s, book_s in per_strategy.items()]
            deltas += [{"strategy": s, "symbol": sym, "units": -h[sym]} for s, h in holdings.items()
                       if s not in per_strategy and sym in h]
            netted = net_orders(deltas)
            try:
                if not netted:
                    raise OrderError("no strategy has a part in this order")
                parts = [{"strategy": s, "units": u} for s, u in sorted(allocate(netted[0]["parts"], units).items())]
            except OrderError as exc:
                self.refused.append({"symbol": sym, "units": units, "by": "order step",
                                     "reason": f"the strategies' parts could not be worked out: {_describe(exc)}"})
                self._write({"event": "refused", "symbol": sym, "units": units, "by": "order step",
                             "reason": self.refused[-1]["reason"]})
                continue
            out.append({"symbol": sym, "units": units, "quote": quote, "value": value, "parts": parts, "owned": owned})
        return out

    # -- 8, 9, 10
    def _send(self, candidates, baseline, book, prices, cash) -> None:
        held = {sym: math.fsum((baseline.get(sym, 0.0), book.get(sym, 0.0))) for sym in set(baseline) | set(book)}
        unpriced = [sym for sym, u in held.items() if u > UNITS_EPS and sym not in prices]
        value = math.fsum(u * prices[sym] for sym, u in held.items() if u > UNITS_EPS and sym in prices)
        equity = (cash + value) if cash is not None and not unpriced else float("nan")
        previous, peak = self.state.previous_equity(self.day), self.state.peak()
        if _finite(equity):
            self.state.record_equity(self.day, equity)
            self.broker_equity = equity
            peak = equity if peak is None else max(peak, equity)
            daily = max(0.0, (previous - equity) / previous * 100.0) if previous else 0.0
            drawdown = max(0.0, (peak - equity) / peak * 100.0) if peak else 0.0
        else:
            daily = drawdown = float("nan")
        account = {"equity": equity, "daily_loss_pct": daily, "drawdown_pct": drawdown,
                   "open_positions": sum(1 for u in held.values() if u > UNITS_EPS)}
        if not _finite(equity):
            why = (f"no usable quote for {', '.join(sorted(unpriced))}" if unpriced else "the venue's cash is not a usable number")
            self._write({"event": "account", "equity": None, "reason": f"the broker equity cannot be worked out: {why}"})
        try:
            limits = risk_gate.load_limits(self.limits_file)
        except risk_gate.RiskConfigError:
            limits = None                                # check_order then refuses with the file's own error
        cap = max_orders_per_day(limits)
        sent_today = self.state.sent_on(self.day)
        budget = cash * (1.0 - CASH_KEPT) if cash is not None and cash > 0 else 0.0
        spent = 0.0
        purchases_stopped = None
        sales = sorted((c for c in candidates if c["units"] < 0), key=lambda c: (-c["value"], c["symbol"]))
        buys = sorted((c for c in candidates if c["units"] > 0), key=lambda c: (-c["value"], c["symbol"]))
        for c in sales + buys:
            sym, units, quote = c["symbol"], c["units"], c["quote"]
            owned_here = held.get(sym, 0.0)
            reduces = units < 0 and owned_here > 0 and abs(owned_here + units) < abs(owned_here)
            limit = limit_price(units, quote, reduces)
            if self._now() > self.window_end:
                self._leave(c, "the session window closed during the run")
                continue
            if sent_today >= cap:
                self._leave(c, f"the day's cap of {cap} orders is reached")
                continue
            if units > 0 and purchases_stopped:
                self._leave(c, purchases_stopped)
                continue
            order = {"symbol": sym, "side": "buy" if units > 0 else "sell", "value": abs(units) * quote, "reduces_risk": bool(reduces),
                     "adds_to_existing": owned_here > UNITS_EPS, "position_value_after": abs(owned_here + units) * quote}
            ok, why = risk_gate.check_order(order, dict(account), limits, limits_file=None if limits else self.limits_file,
                                            latch_file=self.latch_file, halt_file=self.halt_file)
            if not ok:
                self._refuse(c, "risk gate", why, order=order, account=account)
                continue
            if limit is None:
                self._refuse(c, "order step", f"no positive limit price comes from the quote {quote}")
                continue
            cost = units * limit if units > 0 else 0.0
            if units > 0 and spent + cost > budget:
                purchases_stopped = (f"the purchases would exceed the cash the venue reports less {CASH_KEPT:.0%} "
                                     f"({budget:.2f})" if cash is not None else "the venue's cash is not a usable number")
                self._leave(c, purchases_stopped)
                continue
            tag = f"{self.batch}-{sym}"
            try:
                self.ledger.intend(tag, sym, units, quote, limit_price=limit, batch=self.batch, parts=c["parts"])
                self.state.note_sending(tag, self.day)
                rec = self.ledger.submit_once(self.venue, tag)
            except OrdersBlocked as exc:
                raise _Stop("send", f"the ledger refused to send {tag}: {'; '.join(exc.reasons)[:1000]}") from None
            except (OrderError, sqlite3.Error) as exc:
                raise _Stop("send", f"order {tag} could not be recorded or sent: {_describe(exc)}; the next run's "
                                    "reconcile settles it") from None
            outcome = rec["state"]
            self.state.note_outcome(tag, outcome)
            if outcome == "refused":
                self._refuse(c, "venue", rec.get("note") or "refused", tag=tag)
                continue
            if outcome != "accepted":
                self._write({"event": "unknown", "tag": tag, "symbol": sym, "units": units, "state": outcome,
                             "note": rec.get("note")})
                raise _Stop("send", f"the outcome of order {tag} ({sym} {units:+g}) is {outcome}: "
                                    f"{(rec.get('note') or 'no answer')[:MAX_TEXT]}; nothing more is sent until a reconcile "
                                    "settles it")
            sent_today += 1
            spent += cost
            if units > 0 and owned_here <= UNITS_EPS:
                account["open_positions"] += 1
            entry = {"symbol": sym, "units": units, "side": order["side"], "limit_price": limit, "quote": quote,
                     "value": abs(units) * quote, "tag": tag, "parts": c["parts"], "reduces_risk": bool(reduces)}
            self.sent.append(entry)
            self._write({"event": "order", **entry, "state": outcome})

    def _leave(self, c: dict, reason: str) -> None:
        item = {"symbol": c["symbol"], "units": c["units"], "reason": reason}
        self.left_over.append(item)
        self._write({"event": "left_over", **item})

    def _refuse(self, c: dict, by: str, reason: str, **extra) -> None:
        item = {"symbol": c["symbol"], "units": c["units"], "by": by, "reason": str(reason)[:MAX_TEXT * 2]}
        self.refused.append(item)
        self._write({"event": "refused", **item, **extra})

    # -- 11
    def _finish(self, status: str, stage: str | None, reason: str | None) -> dict:
        try:
            book = broker_book(self.ledger.fills())
            mine = broker_book(f for f in self.ledger.fills() if self.batch and f["tag"].startswith(self.batch + "-"))
        except Exception:
            book, mine = {}, {}
        gaps = {sym: {"broker_book": book.get(sym, 0.0), "target": t, "gap": t - book.get(sym, 0.0)}
                for sym, t in sorted(self.target_units.items())}
        for sym in sorted(set(book) - set(self.target_units)):
            gaps[sym] = {"broker_book": book[sym], "target": 0.0, "gap": -book[sym]}
        summary = {"status": status, "stage": stage, "reason": reason, "venue": self.name, "batch": self.batch,
                   "orders_sent": len(self.sent), "sent": self.sent, "refused": self.refused, "left_over": self.left_over,
                   "skipped": self.skipped, "cancels": self.cancels, "filled_so_far": mine, "fills_journaled": self.fills_journaled,
                   "broker_equity": self.broker_equity if _finite(self.broker_equity) else None,
                   "paper_equity": self.paper_equity, "gaps": gaps}
        try:
            if status == "stopped":
                self._write({"event": "stopped", "stage": stage, "reason": reason})
            self._write({"event": "summary", **summary})
        except Exception as exc:                         # the summary is still returned
            summary["journal_error"] = _describe(exc)
        return summary
