"""Paper trading: fake money on real market data, every order through the kill switch, every
decision written to the journal with its reasoning.

`PaperTrader.step` is one trading day. It fills yesterday's accepted orders at today's close (so a
decision made at a close never trades at that close), marks the book, and decides today's orders
from each strategy's target weights. Each order is offered to `check_order`; a refused order goes
nowhere and the refusal is journaled.

The book kept here is the record used for attribution by strategy. Orders can also be mirrored to
an Alpaca paper account (`AlpacaPaperVenue`), which refuses to be built on anything but paper keys
and the paper endpoint. There is no live-money path in this module.

Given tax rules (`tax_rules`), the trader also keeps the account's tax ledger (execution_and_risk/tax.py)
and writes beside every order what it would do for tax, estimated at the decision price, and in each day's
summary the year's realised result so far. These are notes only: they never block, delay, resize or
reorder an order. A ledger that cannot take a fill (a short position, for one) stops, and from then on
every note says it is unavailable and why; trading carries on exactly as with tax off.

One night at a time on one book: a run that writes the book (`step`, `reset_latch`) first takes an operating-system
lock on `paper_state.lock` beside the state file (`fcntl.flock`, released with the process however it ends). A second
run that finds it held writes nothing and raises `PaperBookBusy`, naming the lock. A program that only reads the book
(the order step, the owner page, the decay monitor) never takes it and never waits for it; one on a folder where the
lock cannot be made or opened reads the book, leaves any outbox for a run that can take the lock and writes nothing,
while a run that writes the book there still raises.

Rolling the code back: a day's journal entries are saved in the state (`journal_outbox`) before they are appended, and
a revision without the outbox does not know to append them. Before rolling the code back to such a revision, start this
revision once on the book (building a `PaperTrader` on it is enough, when no other run holds the lock): its start
appends any entries a killed run left in the outbox and removes it. Otherwise the older code runs on with that day's
fills, decisions and summary missing from the journal, and its tax notes, read from the journal's fills, are wrong.
The state's `last_valid_prices` (the last valid close the book valued each held fund at) is ignored by an older
revision, and filled from the price history on the first night of this one when it is missing.
"""
from __future__ import annotations

import fcntl
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd

from valedia_trader.backtest.engine import buy_and_hold
from valedia_trader.research_loop import diagnostics, risk_gate
from valedia_trader.research_loop.ledger import market_regime
from valedia_trader.research_loop.strategy import Strategy, _exchange_sessions, run_rules

__all__ = ["Journal", "PaperTrader", "Venue", "AlpacaPaperVenue", "VenueError", "market_conditions"]


_SESSIONS: list = []


def next_session(day) -> str:
    """The exchange session after `day`, when an order decided at its close fills; `day` itself if the calendar
    cannot answer."""
    d = pd.Timestamp(day).normalize()
    try:
        if not _SESSIONS:
            _SESSIONS.append(_exchange_sessions())
        sessions = _SESSIONS[0]
        i = sessions.searchsorted(d, side="right")
        if i < len(sessions):
            return str(sessions[i].date())
    except Exception:
        pass
    return str(d.date())


def tax_sell_note(c: dict, px: float, day: str) -> dict:
    """The compact note for a sale, from `TaxLedger.check_sell`."""
    st, lt = c["gain_loss"]["short_term"], c["gain_loss"]["long_term"]
    parts = [f"{term} {'gain' if v >= 0 else 'loss'} of {abs(v):.2f}" for term, v in (("short-term", st), ("long-term", lt)) if abs(v) >= 0.005]
    effect = "would realise " + (" and ".join(f"a {p}" for p in parts) if parts else "no gain or loss")
    if c["disallowed_loss"] > 0:
        bought = sorted({m["purchase_date"] for m in c["wash_sale"]["matches"]})
        effect += f"; {c['disallowed_loss']:.2f} of it would be disallowed as a wash sale against the purchase of {', '.join(bought)}"
    if c["wash_sale"]["loss_at_risk"] > 0:
        effect += f"; buying {c['symbol']} again before {c['wash_sale']['clean_from']} would make it a wash sale"
    return {"side": "sell", "estimated_at_price": float(px), "estimated_for_day": str(day), "gain_loss": {"short_term": st, "long_term": lt},
            "disallowed_loss": c["disallowed_loss"], "loss_at_risk": c["wash_sale"]["loss_at_risk"],
            "clean_from": c["wash_sale"]["clean_from"], "effect": effect + ".", "unverified_rules": c["unverified_rules"], "note": c["note"]}


def tax_buy_note(c: dict, day: str) -> dict:
    """The compact note for a purchase, from `TaxLedger.check_buy`; the sentence names each sale date once."""
    washes = [{"sale_date": m["sale_date"], "units": m["units"], "disallowed_loss": m["disallowed_loss"]} for m in c["matches"]]
    by_date: dict[str, float] = {}
    for w in washes:
        by_date[w["sale_date"]] = by_date.get(w["sale_date"], 0.0) + w["disallowed_loss"]
    if by_date:
        effect = ("would turn " + " and ".join(f"{v:.2f} of the loss sold on {k}" for k, v in by_date.items())
                  + " into a wash sale, carried into this lot")
    else:
        effect = f"would wash no earlier loss of {c['symbol']}"
    return {"side": "buy", "estimated_for_day": str(day), "disallowed_loss": c["disallowed_loss"], "washes": washes,
            "clean_from": c["clean_from"], "effect": effect + ".", "unverified_rules": c["unverified_rules"], "note": c["note"]}


DAMAGE_WINDOW_DAYS = 30                               # how far back the owner is told about damaged journal lines


class VenueError(RuntimeError):
    pass


class PaperBookBusy(RuntimeError):
    """Another run holds the paper book's lock: this run did nothing to the book."""


class _LockCannotOpen(PaperBookBusy):
    """The book's lock file cannot be opened (a folder that cannot be written, a link at its path): nothing was done."""


class LatchResetError(RuntimeError):
    """The drawdown latch is there and cannot be removed (a directory, no permission, ...)."""


def describe_skipped(skipped: list[dict], limit: int = 8) -> str:
    """`2026-03-02.jsonl line 2, 2026-03-03.jsonl line 1`: where the skipped lines are, never what is on them."""
    text = ", ".join(f"{s['file']} line {s['line']}" for s in skipped[:limit])
    return text + (f", and {len(skipped) - limit} more" if len(skipped) > limit else "")


class JournalDamaged(ValueError):
    """A journal has lines that are not complete entries, and the caller needs every entry for what it computes."""

    def __init__(self, skipped: list[dict]) -> None:
        self.skipped = [dict(s) for s in skipped]
        n = len(self.skipped)
        super().__init__(f"the journal has {n} line{'s' if n != 1 else ''} that {'are' if n != 1 else 'is'} not a complete entry "
                         f"({describe_skipped(self.skipped)}); nothing that needs every entry is computed from it")


class Journal:
    """Append-only decision log: one JSON line per decision in `journal/YYYY-MM-DD.jsonl`."""

    def __init__(self, directory: str | Path) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.skipped_lines: list[dict] = []              # the lines the last `read` skipped: file name and line number only

    def write(self, day, entry: dict) -> dict:
        """Append one record. Any piece of a remembered secret is replaced first, and the record
        then says how many replacements were made (`secret_redacted`)."""
        record = self.record(day, entry)
        self.append(self.line(record))
        return record

    @staticmethod
    def record(day, entry: dict) -> dict:
        """The record `write` would append for `entry` today, without appending it: stamped, dated and redacted."""
        from valedia_trader.core.secrets import redact_json
        record = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), "day": str(pd.Timestamp(day).date()), **entry}
        record, replaced = redact_json(record)
        if replaced:
            record["secret_redacted"] = replaced
        return record

    @staticmethod
    def line(record: dict) -> str:
        """The journal line of a record, as `write` appends it (without its newline)."""
        return json.dumps(record, default=str)

    def append(self, line: str) -> None:
        """Append one journal line (from `line`) to the file of the day it names."""
        day = json.loads(line)["day"]
        data = (line + "\n").encode("utf-8")
        with (self.dir / f"{day}.jsonl").open("a+b") as fh:
            if fh.seek(0, os.SEEK_END):                  # a file whose last line has no newline ends in a torn line (a kill
                fh.seek(-1, os.SEEK_END)                 # in the middle of a write): the new entry starts on its own line
                if fh.read(1) != b"\n":
                    data = b"\n" + data
            fh.write(data)                               # one write, so a kill leaves at most one torn line

    def read(self, since=None, until=None) -> list[dict]:
        """The entries in date order, each file in the order written. A line that is not a complete JSON object (one cut
        short by a kill, or damaged) is skipped and never raises; `skipped_lines` then holds `{"file", "line"}` for each
        such line of this read (never any of its text), so a caller can report it."""
        lo = str(pd.Timestamp(since).date()) if since is not None else ""
        hi = str(pd.Timestamp(until).date()) if until is not None else "9999"
        out: list[dict] = []
        skipped: list[dict] = []
        for path in sorted(self.dir.glob("????-??-??.jsonl")):
            if lo <= path.stem <= hi:
                for number, raw in enumerate(path.read_bytes().split(b"\n"), 1):
                    if not raw.strip():
                        continue
                    try:
                        entry = json.loads(raw)
                    except (ValueError, RecursionError):
                        entry = None
                    if isinstance(entry, dict):
                        out.append(entry)
                    else:
                        skipped.append({"file": path.name, "line": number})
        self.skipped_lines = skipped
        return out

    def read_complete(self, since=None, until=None) -> list[dict]:
        """`read`, for a caller whose figures need every entry: raises JournalDamaged (a ValueError naming each skipped
        line's file and number) when `read` skipped any line."""
        out = self.read(since=since, until=until)
        if self.skipped_lines:
            raise JournalDamaged(self.skipped_lines)
        return out


def market_conditions(prices: pd.DataFrame, market: tuple[str, ...] | list[str], window: int = 63) -> dict:
    """What the market looked like at the decision: regime of the last `window` days against the
    whole history given, 20-day annualised volatility and 20-day return of the equal-weight basket."""
    cols = [m for m in market if m in prices.columns]
    basket = np.log(prices[cols].where(prices[cols] > 0)).diff().mean(axis=1).dropna()
    recent = basket.tail(20)
    return {"regime": market_regime(prices[cols].tail(window + 1), prices[cols]),
            "volatility_20d": float(recent.std() * np.sqrt(252.0)) if len(recent) > 2 else float("nan"),
            "return_20d": float(np.expm1(recent.sum())) if len(recent) else float("nan")}


PRICE_MIN, PRICE_MAX = 0.0001, 10_000_000.0          # a close outside this range is no price
JUMP_FACTOR = 5.0                                     # a close this many times above or below the last valid one is suspect


def _is_price(px: float) -> bool:
    """A price is a finite number from PRICE_MIN to PRICE_MAX; anything else (missing, not a number, infinite, zero,
    negative, too small or too large) is none. (Whether a close is a believable move is `_clean_prices`'s question.)"""
    return bool(np.isfinite(px) and PRICE_MIN <= px <= PRICE_MAX)


def _close_enough(a: float, b: float) -> bool:
    return bool(a <= JUMP_FACTOR * b and JUMP_FACTOR * a >= b)


def _judge(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(valid, suspect) for each close of one fund, in order. A close is valid when it is in range and either the fund
    has no valid close before it, or it is within JUMP_FACTOR of the last valid close, or it is within JUMP_FACTOR of the
    fund's last priced close (the last in-range close before it, valid or suspect, however many rows without one lie
    between: a move confirmed by the fund's next priced close). An in-range close that is none of these is suspect: it is
    treated as missing."""
    with np.errstate(invalid="ignore"):
        ok = np.isfinite(values) & (values >= PRICE_MIN) & (values <= PRICE_MAX)
    valid, suspect, last, priced = np.zeros(len(values), bool), np.zeros(len(values), bool), None, None
    for t in np.flatnonzero(ok):
        v = float(values[t])
        if last is None or _close_enough(v, last) or (priced is not None and _close_enough(v, priced)):
            valid[t], last = True, v
        else:
            suspect[t] = True
        priced = v
    return valid, suspect


def _as_numbers(col: pd.Series) -> pd.Series | None:
    """`col` as floats (a missing value, pd.NA included, as NaN), or None when it is not a column of numbers: booleans,
    dates, times, categories, or objects other than numbers and text that reads as a number."""
    types = pd.api.types
    if types.is_bool_dtype(col.dtype) or types.is_complex_dtype(col.dtype):
        return None
    if types.is_integer_dtype(col.dtype) or types.is_float_dtype(col.dtype):
        return pd.Series(col.to_numpy(dtype=float, na_value=np.nan), index=col.index)
    if not (types.is_object_dtype(col.dtype) or types.is_string_dtype(col.dtype)):
        return None                                                   # dates, times, periods, intervals, categories
    values = []
    for v in col.to_numpy(dtype=object):
        if isinstance(v, (bool, np.bool_)):
            return None
        if v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v)):
            values.append(np.nan)
        elif isinstance(v, (int, float, np.integer, np.floating, str)):
            values.append(v)
        else:
            return None
    try:
        return pd.to_numeric(pd.Series(values, index=col.index, dtype=object), errors="raise").astype(float)
    except (ValueError, TypeError):
        return None


def _clean_prices(prices: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float], list[str]]:
    """(prices with every close that is no valid price made missing, today's suspect closes by fund, the columns no
    price can be read from). A column that is not numbers (`_as_numbers`) has no valid price on any day; a column with
    no value at all is missing on every day; both are named. A column of ordinary floats or integers is not converted,
    and when every close is valid or missing, `prices` itself comes back, untouched."""
    unreadable, columns = [], {}
    for name, dtype in prices.dtypes.items():
        if isinstance(dtype, np.dtype) and dtype.kind in "iuf":
            continue                                                       # ordinary floats or integers: as they are
        col = _as_numbers(prices[name])
        if col is None:
            col = pd.Series(np.nan, index=prices.index)
            unreadable.append(str(name))
        columns[name] = col
    frame = prices
    if columns:
        frame = prices.copy()
        for name, col in columns.items():
            frame[name] = col
    values = frame.to_numpy(dtype=float)
    for j, name in enumerate(frame.columns):
        if len(values) and np.isnan(values[:, j]).all() and str(name) not in unreadable:
            unreadable.append(str(name))                                   # no value at all: no price can be read
    with np.errstate(invalid="ignore"):
        ok = np.isfinite(values) & (values >= PRICE_MIN) & (values <= PRICE_MAX)
    suspect_today: dict[str, float] = {}
    clean = values.copy() if not (ok | np.isnan(values)).all() else None
    for j, name in enumerate(frame.columns):
        col = values[:, j]
        kept = col[ok[:, j]]
        if (ok[:, j] | np.isnan(col)).all() and (kept.size < 2 or (np.all(kept[1:] <= JUMP_FACTOR * kept[:-1])
                                                                   and np.all(JUMP_FACTOR * kept[1:] >= kept[:-1]))):
            continue                                                       # every close valid or missing: nothing to judge
        valid, suspect = _judge(col)
        if clean is None:
            clean = values.copy()
        clean[~valid, j] = np.nan
        if suspect[-1]:
            suspect_today[str(name)] = float(col[-1])
    if clean is None:
        return frame, {}, sorted(unreadable)
    return pd.DataFrame(clean, index=frame.index, columns=frame.columns), dict(sorted(suspect_today.items())), sorted(unreadable)


def _finite_only(x):
    """`x` with every number that is not finite replaced by None (null in JSON); everything else as it was."""
    if isinstance(x, float):
        return x if np.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _finite_only(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_finite_only(v) for v in x]
    return x


class Venue(Protocol):
    name: str

    def submit(self, symbol: str, units: float, tag: str) -> dict: ...


class AlpacaPaperVenue:
    """Mirror of accepted orders to an Alpaca paper account.

    Built only when the key is a paper key (Alpaca paper key ids start with "PK") and the client
    reports the paper endpoint; anything else raises. Fractional orders cannot open shorts on Alpaca,
    so sells are sent as whole shares and the remainder is reported back."""
    name = "alpaca-paper"
    PAPER_HOST = "paper-api.alpaca.markets"

    def __init__(self, api_key: str, secret_key: str, client: Any | None = None) -> None:
        if not str(api_key).startswith("PK"):
            raise VenueError("this is not an Alpaca paper key (paper key ids start with PK); refusing to connect")
        if client is None:
            from alpaca.trading.client import TradingClient
            client = TradingClient(api_key, secret_key, paper=True)
        base = getattr(client, "_base_url", "")
        if self.PAPER_HOST not in str(getattr(base, "value", base)):
            raise VenueError("the Alpaca client is not pointed at the paper endpoint; refusing to trade")
        self._client = client

    def account(self) -> dict:
        a = self._client.get_account()
        return {"equity": float(a.equity), "last_equity": float(a.last_equity), "account_number": str(a.account_number)}

    def positions(self) -> dict[str, float]:
        return {p.symbol: float(p.qty) for p in self._client.get_all_positions()}

    def submit(self, symbol: str, units: float, tag: str) -> dict:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
        crypto = symbol.endswith("-USD")
        qty = abs(float(units))
        if units < 0 and not crypto:
            qty = float(int(qty))                       # whole shares for sells: fractional shorts are not allowed
        if qty <= 0:
            return {"status": "skipped", "reason": "rounds to zero shares"}
        order = self._client.submit_order(MarketOrderRequest(
            symbol=symbol.replace("-USD", "/USD") if crypto else symbol, qty=qty,
            side=OrderSide.BUY if units > 0 else OrderSide.SELL,
            time_in_force=TimeInForce.GTC if crypto else TimeInForce.DAY, client_order_id=tag[:48]))
        return {"status": "accepted", "id": str(order.id), "qty": qty}


class PaperTrader:
    """One fake-money account shared by the strategies that reached paper trading."""

    PANEL_MODES = ("off", "shadow", "veto")

    def __init__(self, directory: str | Path, limits_file: str | Path, starting_cash: float = 100_000.0,
                 fee_bps: float = 1.0, slippage_bps: float = 2.0, venue: Venue | None = None,
                 halt_file: str | Path | None = risk_gate.HALT_FILE, min_trade_pct: float = 0.002,
                 panel=None, panel_mode: str = "off", panel_horizon_days: int = 5, facts=None, tax_rules=None) -> None:
        if panel_mode not in self.PANEL_MODES:
            raise ValueError(f"panel_mode must be one of {self.PANEL_MODES}")
        if panel_mode != "off" and panel is None:
            raise ValueError("a panel is needed for shadow or veto mode")
        # The decision panel sees every order that adds risk. In shadow mode its verdict is only written
        # down; in veto mode a refusal (or an unreachable panel) blocks the order. It can never create one.
        self.panel, self.panel_mode, self.panel_horizon = panel, panel_mode, int(panel_horizon_days)
        self._facts = facts
        self.dir = Path(directory)
        self.journal = Journal(self.dir)
        self.limits_file, self.halt_file, self.venue = Path(limits_file), halt_file, venue
        self.fee_bps, self.slippage_bps, self.min_trade_pct = fee_bps, slippage_bps, min_trade_pct
        self.state_file, self.latch_file = self.dir / "paper_state.json", self.dir / "kill_switch.json"
        self.lock_file = self.dir / "paper_state.lock"
        self._starting_cash = starting_cash
        self._outbox: list[str] = []                         # the journal lines of the day being run, written with the day
        self.tax_rules = tax_rules
        self._tax_today, self._tax_fill_day = [], None        # in memory only: the orders placed so far this step
        self._load()
        if "journal_outbox" in self.state:                   # a day saved by a run killed while appending its entries:
            try:                                             # finished only under the lock, and never waited for
                fd = self._try_lock()
            except _LockCannotOpen:                          # a reader on a folder it cannot write: it reads, and leaves
                fd = None                                    # the outbox to a run that can take the lock
            if fd is not None:
                try:
                    self._catch_up()
                finally:
                    os.close(fd)

    def _load(self) -> None:
        """Read the state file (or start a book) and build the tax ledger from the journal."""
        self._seen = self._state_bytes()
        if self._seen is not None:
            self.state = json.loads(self._seen)
            self.state.setdefault("panel_cases", [])
        else:
            c = self._starting_cash
            self.state = {"starting_cash": c, "cash": c, "positions": {}, "pending": [],
                          "history": [], "peak_equity": c, "last_day": None, "realized": {}, "panel_cases": []}
        self._load_tax()

    def _load_tax(self) -> None:
        # the account's tax ledger: built once from the journal's fills, then fed each new fill. Notes only.
        self.tax_ledger, self.tax_unavailable = None, None
        if self.tax_rules is not None:
            try:
                from valedia_trader.execution_and_risk.tax import TaxLedger
                self.tax_ledger = TaxLedger.from_fills(self.journal.read_complete(), self.tax_rules)
            except Exception as exc:
                self.tax_unavailable = f"the tax ledger could not take a fill: {exc}"

    def _state_bytes(self) -> bytes | None:
        try:
            return self.state_file.read_bytes()
        except FileNotFoundError:
            return None

    def _try_lock(self) -> int | None:
        """The book's lock, taken without waiting: its descriptor, or None when another holds it."""
        try:
            fd = os.open(self.lock_file, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o644)
        except FileNotFoundError:
            return None                                       # no book folder yet: nothing to finish
        except OSError as exc:
            raise _LockCannotOpen(f"the paper book's lock {self.lock_file} cannot be opened ({type(exc).__name__}); "
                                "this run did nothing to the book") from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return None
        return fd

    def _locked(self):
        """Take the book's lock for one write (a day, a latch reset), or refuse at once; then make sure the state in
        memory is the state on disk: when another run changed the file since this trader read it, read it again (and
        finish an outbox it left)."""
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = self._try_lock()
        if fd is None:
            raise PaperBookBusy(f"another run holds the paper book's lock {self.lock_file}: this run did nothing to the "
                                "book (one night at a time on one book)")
        try:
            self._catch_up()
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _catch_up(self) -> None:
        """Under the lock: read the state again if another run changed it since this trader read it, and finish an outbox
        a killed run left (then rebuild the tax ledger from the journal it completed)."""
        if self._state_bytes() != self._seen:
            self._load()
        if "journal_outbox" in self.state:
            self._flush_outbox()
            self._load_tax()

    # ---- book
    def _lot(self, strategy: str, symbol: str) -> dict:
        return self.state["positions"].setdefault(strategy, {}).setdefault(symbol, {"units": 0.0, "cost": 0.0})

    @staticmethod
    def _mark(sym: str, lot: dict, row: pd.Series, carried: dict[str, float] | None) -> float:
        """The price a held lot is valued at: today's when it is a price (finite, PRICE_MIN to PRICE_MAX; `step` has already
        removed a suspect jump), else the last valid close the book has seen (`carried`, from `_carried_marks`), else the lot's cost. A held position is
        never valued at nothing."""
        px = float(row.get(sym, np.nan))
        if _is_price(px):
            return px
        if carried and sym in carried:
            return carried[sym]
        return float(lot["cost"])

    def _carried_marks(self, prices: pd.DataFrame) -> dict[str, tuple[float, str]]:
        """For each fund the book holds that has no valid price today: the price it is valued at and that close's day.
        That is the later of two closes: the fund's last valid close before today in `prices`, and the last valid close
        the book itself valued it at (`last_valid_prices` in the state). In a book run every night the two are the same
        close; the frame's is later only when the book did not run on that day, the book's own is the only one when the
        frame holds no valid price of the fund (its column unreadable, all missing or absent). Left out when there is
        neither: the lot is then valued at its cost."""
        row, out = prices.iloc[-1], {}
        seen = self.state.get("last_valid_prices") or {}
        for sym in sorted({sym for book in self.state["positions"].values() for sym, lot in book.items() if lot["units"]}):
            if _is_price(float(row.get(sym, np.nan))):
                continue
            best = None
            mark = seen.get(sym)
            if (isinstance(mark, dict) and isinstance(mark.get("day"), str) and isinstance(mark.get("price"), (int, float))
                    and not isinstance(mark.get("price"), bool) and _is_price(float(mark["price"]))):
                best = (float(mark["price"]), mark["day"])
            if sym in prices.columns:
                earlier = prices[sym].iloc[:-1].to_numpy(dtype=float)
                with np.errstate(invalid="ignore"):
                    good = np.flatnonzero(np.isfinite(earlier) & (earlier >= PRICE_MIN) & (earlier <= PRICE_MAX))
                if good.size:
                    on = str(prices.index[good[-1]].date())
                    if best is None or on >= best[1]:
                        best = (float(earlier[good[-1]]), on)
            if best is not None:
                out[sym] = best
        return out

    def _remember_prices(self, row: pd.Series, day, marks: dict[str, tuple[float, str]]) -> None:
        """Keep, for every fund the book holds, the last valid close it was valued at and that close's day: today's when
        it is valid, else the carried one (`_carried_marks`); a fund valued at its cost has none."""
        memo = {}
        for sym in sorted({sym for book in self.state["positions"].values() for sym, lot in book.items() if lot["units"]}):
            px = float(row.get(sym, np.nan))
            if _is_price(px):
                memo[sym] = {"price": px, "day": str(pd.Timestamp(day).date())}
            elif sym in marks:
                memo[sym] = {"price": marks[sym][0], "day": marks[sym][1]}
        if memo or "last_valid_prices" in self.state:
            self.state["last_valid_prices"] = memo

    def _stale_funds(self, prices: pd.DataFrame) -> dict[str, int]:
        """The funds the book holds that have no price on the last row of `prices`, by name, each with the number of
        consecutive rows, the last included, on which it has had none (every row, for a fund without a column)."""
        row, out = prices.iloc[-1], {}
        for sym in sorted({sym for book in self.state["positions"].values() for sym, lot in book.items() if lot["units"]}):
            if _is_price(float(row.get(sym, np.nan))):
                continue
            if sym not in prices.columns:
                out[sym] = len(prices)
                continue
            values = prices[sym].to_numpy(dtype=float)
            with np.errstate(invalid="ignore"):
                good = np.flatnonzero(np.isfinite(values) & (values >= PRICE_MIN) & (values <= PRICE_MAX))
            out[sym] = int(len(values) - (good[-1] + 1 if good.size else 0))
        return out

    def equity(self, prices_row: pd.Series, carried: dict[str, float] | None = None) -> float:
        """Cash plus every held lot at `_mark`: a fund with no valid price in `prices_row` (see `_mark`) is valued at its
        `carried` price, or at the lot's cost."""
        value = sum(lot["units"] * self._mark(sym, lot, prices_row, carried) for book in self.state["positions"].values()
                    for sym, lot in book.items() if lot["units"])
        return float(self.state["cash"] + value)

    def funds_held(self) -> set[str]:
        """Funds the account holds, or has an order in to open or add to."""
        held = {sym for book in self.state["positions"].values() for sym, lot in book.items() if abs(lot["units"]) > 1e-12}
        return held | {o["symbol"] for o in self.state["pending"] if not o["reduces_risk"]}

    def open_positions(self) -> int:
        """Positions of the account. A fund two strategies both hold is one position: the limits are the
        account's, and one position is what the broker would show."""
        return len(self.funds_held())

    def wanted_units(self) -> dict[str, dict[str, float]]:
        """Per strategy and fund, the units held plus the units of its pending orders: what the book will hold once
        its orders fill. Read only (nothing in the state is created or changed); entries that come to zero are left
        out. The order step sizes the broker's orders from this."""
        out: dict[str, dict[str, float]] = {}
        for strategy, book in self.state["positions"].items():
            for sym, lot in book.items():
                out.setdefault(strategy, {})[sym] = out.get(strategy, {}).get(sym, 0.0) + float(lot["units"])
        for o in self.state["pending"]:
            s, sym = o["strategy"], o["symbol"]
            out.setdefault(s, {})[sym] = out.get(s, {}).get(sym, 0.0) + float(o["units"])
        cleaned = {s: {sym: u for sym, u in book.items() if abs(u) > 1e-12} for s, book in out.items()}
        return {s: book for s, book in cleaned.items() if book}

    def _units_elsewhere(self, strategy: str, symbol: str) -> float:
        """Units of the fund that the account holds or has ordered through its other strategies."""
        lots = sum(book.get(symbol, {}).get("units", 0.0) for name, book in self.state["positions"].items() if name != strategy)
        return float(lots + sum(o["units"] for o in self.state["pending"] if o["symbol"] == symbol and o["strategy"] != strategy))

    def _fill_pending(self, day, row: pd.Series) -> None:
        still = []
        for o in self.state["pending"]:
            px = float(row.get(o["symbol"], np.nan))
            if not _is_price(px):
                still.append(o)                           # no valid price today: the order waits
                continue
            fill = px * (1.0 + np.sign(o["units"]) * self.slippage_bps * 1e-4)
            fee = abs(o["units"]) * fill * self.fee_bps * 1e-4
            lot = self._lot(o["strategy"], o["symbol"])
            before = lot["units"]
            after = before + o["units"]
            realized = None
            if before and np.sign(o["units"]) != np.sign(before):          # closing all or part of a position
                closed = min(abs(o["units"]), abs(before))
                realized = float(closed * (fill - lot["cost"]) * np.sign(before))
            # a strategy's realised result carries every fee it paid, on entries as well as exits
            self.state["realized"][o["strategy"]] = self.state["realized"].get(o["strategy"], 0.0) + (realized or 0.0) - fee
            if abs(after) < 1e-12:
                lot["units"], lot["cost"] = 0.0, 0.0
            elif before == 0 or np.sign(after) != np.sign(before):
                lot["units"], lot["cost"] = after, fill
            elif np.sign(o["units"]) == np.sign(before):
                lot["cost"] = (lot["cost"] * abs(before) + fill * abs(o["units"])) / abs(after)
                lot["units"] = after
            else:
                lot["units"] = after
            self.state["cash"] -= o["units"] * fill + fee
            self._note(day, {"type": "fill", "strategy": o["strategy"], "version": o["version"], "symbol": o["symbol"],
                                     "units": o["units"], "price": fill, "fee": fee, "decided_on": o["decided_on"],
                                     "realized_pnl": realized, "entry_conditions": o.get("conditions"), "reason": o["reasoning"]})
            self._tax_take(day, o["symbol"], o["units"], fill, fee)
        self.state["pending"] = still

    # ---- tax notes (never change an order, a fill or the book)
    def _tax_take(self, day, symbol: str, units: float, price: float, fee: float) -> None:
        if self.tax_ledger is None or self.tax_unavailable is not None:
            return
        try:
            self.tax_ledger.add_fill(str(pd.Timestamp(day).date()), symbol, float(units), float(price), float(fee))
        except Exception as exc:
            self.tax_unavailable = f"the tax ledger could not take a fill: {exc}"

    def _tax_note(self, day, symbol: str, units: float, px: float) -> dict:
        """What this order would do for tax at today's price, as of the session it fills, taking the orders placed
        earlier today as filled first. Never raises."""
        if self.tax_unavailable is not None:
            return {"unavailable": self.tax_unavailable}
        try:
            d = self._tax_fill_day
            before = [(d, u, p) for sym, u, p in self._tax_today if sym == symbol]
            if units < 0:
                return tax_sell_note(self.tax_ledger.check_sell(symbol, -units, px, d, before=before), px, d)
            return tax_buy_note(self.tax_ledger.check_buy(symbol, units, d, before=before), d)
        except Exception as exc:
            return {"unavailable": f"{type(exc).__name__}: {str(exc)[:200]}"}

    def _tax_block(self, day) -> dict:
        """The year's realised tax result so far, for the day's summary, and what today's placed orders would do to
        each other once filled. Never raises."""
        if self.tax_unavailable is not None:
            return {"unavailable": self.tax_unavailable}
        d = str(pd.Timestamp(day).date())
        try:
            s = self.tax_ledger.summary(d[:4], as_of=d)
            last = self.tax_ledger.summary(str(int(d[:4]) - 1), as_of=d)
            among = []
            for sym in dict.fromkeys(sym for sym, _, _ in self._tax_today):
                try:
                    among += self.tax_ledger.same_day_interactions([(self._tax_fill_day, x, u, p) for x, u, p in self._tax_today if x == sym])
                except Exception as exc:
                    among.append({"symbol": sym, "unavailable": f"{type(exc).__name__}: {str(exc)[:200]}"})
            return {"year": d[:4], "short_term_net": s["short_term"]["net_gain_loss"], "long_term_net": s["long_term"]["net_gain_loss"],
                    "disallowed_loss": s["total"]["disallowed_loss"], "provisional_loss": s["total"]["provisional_loss"],
                    "provisional_loss_from_last_year": last["total"]["provisional_loss"], "among_todays_orders": among,
                    "as_of": s["as_of"], "unverified_rules": s["unverified_rules"], "note": s["note"]}
        except Exception as exc:
            return {"unavailable": f"{type(exc).__name__}: {str(exc)[:200]}"}

    # ---- decision panel
    def _settle_panel(self, prices: pd.DataFrame) -> None:
        """Score each open panel case whose horizon has passed: was the asset higher (for a buy) than when it was judged?"""
        if self.panel is None:
            return
        still = []
        for case in self.state.get("panel_cases", []):
            later = prices.index[prices.index > pd.Timestamp(case["decided_on"])]
            px = float(prices[case["symbol"]].iloc[-1]) if case["symbol"] in prices.columns else float("nan")
            if len(later) < case["horizon_days"] or not _is_price(px):
                still.append(case)
                continue
            outcome = int((px > case["price"]) if case["side"] == "buy" else (px < case["price"]))
            try:
                self.panel.settle(case["case_id"], outcome)
                self._note(prices.index[-1], {"type": "panel_settled", "case_id": case["case_id"], "symbol": case["symbol"],
                                                      "thesis_right": bool(outcome), "price_then": case["price"], "price_now": px})
            except Exception as exc:
                self._note(prices.index[-1], {"type": "panel_settled", "case_id": case["case_id"], "error": f"{type(exc).__name__}: {exc}"})
        self.state["panel_cases"] = still

    def _consult_panel(self, day, strategy: Strategy, symbol: str, prices: pd.DataFrame, order: dict, account: dict, px: float) -> dict:
        """Put one risk-adding order to the panel with facts computed in code. Never raises."""
        direction = "higher" if order["side"] == "buy" else "lower"
        try:
            facts = dict((self._facts or diagnostics.order_facts)(prices, symbol))
        except Exception as exc:
            facts = {"symbol": symbol, "facts_unavailable": f"{type(exc).__name__}: {exc}"}
        facts.update({"strategy": strategy.name, "order_side": order["side"], "order_value_pct_of_equity": order["value"] / account["equity"] * 100.0,
                      "account_drawdown_pct": account["drawdown_pct"], "open_positions": account["open_positions"], "risk_limits_breached": False})
        case_id = f"{strategy.name}:{symbol}:{pd.Timestamp(day).date()}"
        case = {"thesis": f"{symbol} closes {direction} over the next {self.panel_horizon} trading days", "facts": facts}
        try:
            d = self.panel.evaluate(case_id, case)
        except Exception as exc:
            return {"available": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}", "mode": self.panel_mode}
        answered = [v for v in d.verdicts if v.error is None]
        if not answered:
            return {"available": False, "error": "no panel member answered", "mode": self.panel_mode}
        self.state["panel_cases"].append({"case_id": case_id, "symbol": symbol, "side": order["side"], "price": px,
                                          "decided_on": str(pd.Timestamp(day).date()), "horizon_days": self.panel_horizon})
        return {"available": True, "mode": self.panel_mode, "approve": bool(d.approve), "probability": d.probability, "vetoed": bool(d.vetoed),
                "dissent": d.dissent, "reasons": list(d.reasons)[:4],
                "members": {v.member: (v.probability if v.error is None else f"error: {v.error[:60]}") for v in d.verdicts}}

    # ---- one trading day
    def step(self, prices: pd.DataFrame, strategies: dict[int, Strategy]) -> dict:
        """Run one day using `prices` up to and including today (its last row). `strategies` maps
        the ledger's hypothesis id to the strategy. Returns the day's summary.

        A price is valid when it is a finite number from PRICE_MIN to PRICE_MAX and is no wild jump: a close more than
        JUMP_FACTOR times above or below the fund's last valid close is suspect, unless the close of the row before it was
        in range and within JUMP_FACTOR of it (so a real move is taken one priced row late and a one-day glitch never; a
        fund's first close is judged on the range alone). A column that is not numbers (booleans, dates, text that does
        not read as a number, other objects) has no valid price on any day; a nullable column's missing values are
        missing prices. "The close of the row before" is the fund's last priced close: its last in-range close before
        this one, however many rows without one lie between. Every close that is not valid is read as missing
        everywhere the day reads prices: valuation, the benchmark, fills, decisions, the strategies' rules, the market conditions and the panel. A fund with no price today is not
        traded: its pending orders wait and no new order is decided for it. If the book holds it, it is valued at the
        last valid close the book has seen of it (`_carried_marks`: the later of its last valid close before today in
        `prices` and the one kept in the state's `last_valid_prices`) in the equity, the strategy values and the capital
        per strategy alike, so a night whose data for a held fund is bad cannot by itself move the equity or trip the
        latch; the benchmarks read `prices` alone. The state's `last_valid_prices` keeps, for every fund the book holds,
        the valid close it was last valued at and that close's day; a state saved without it (an earlier revision) is
        filled on its first night from `prices`. Cost is used only for a fund with no valid close the book has seen. The summary's `stale_prices` maps each such held fund to the number of consecutive days, today
        included, it has had no valid price in `prices`; `suspect_prices` maps each fund whose close today is suspect to
        the close seen; `unreadable_prices` names the columns no price can be read from: those that are not numbers, and
        those with no value at all (missing on every row given). Each is present only when not empty, and all are derived from `prices`: nothing about them is kept in the state file. No number that is not finite is
        written to the journal or the summary (it is written as null).

        A day is run under the book's lock (see the module docstring): a run that finds it held raises `PaperBookBusy`
        before it reads or writes anything of the book.

        The day's journal entries are written once the day is decided: the state is saved first with those entries in it
        (`journal_outbox`), then they are appended, then the state is saved without them. A run killed anywhere in a day
        leaves either nothing of the day (the day is run again from the start) or a saved day whose entries the next
        start appends where they are missing: a day run again never books a fill, a tax lot or a result twice, and never
        writes a second summary. An ordinary day writes the same journal lines and the same final state as before.

        Not handled: a fund bought by a book with no valid close of it since (in the state or in `prices`) is valued
        at its cost; a run given a price history that lacks the fund's column counts every row of the history given as
        carried; a strategy whose every
        fund is missing from `prices` makes the rules raise, as before; a kill before the day's state is saved does not
        undo what reached the outside (the venue mirror's orders, the panel's settlements), which a rerun repeats."""
        fd = self._locked()
        try:
            return self._step(prices, strategies)
        finally:
            os.close(fd)

    def _step(self, prices: pd.DataFrame, strategies: dict[int, Strategy]) -> dict:
        prices, suspect, unreadable = _clean_prices(prices)
        day = prices.index[-1]
        if self.state["last_day"] is not None and str(day.date()) <= self.state["last_day"]:
            raise ValueError(f"{day.date()} was already processed; paper trading only moves forward")
        row = prices.iloc[-1]
        previous = self.state["history"][-1]["equity"] if self.state["history"] else self.state["starting_cash"]
        before = self.state["history"][-1]["strategy_values"] if self.state["history"] else {}
        self._fill_pending(day, row)
        self._settle_panel(prices)
        stale = self._stale_funds(prices)
        marks = self._carried_marks(prices) if stale else {}
        carried = {sym: px for sym, (px, _) in marks.items()} if stale else None
        equity = self.equity(row, carried)
        self.state["peak_equity"] = max(self.state["peak_equity"], equity)
        account = {"equity": equity, "daily_loss_pct": max(0.0, (previous - equity) / previous * 100.0),
                   "drawdown_pct": max(0.0, (self.state["peak_equity"] - equity) / self.state["peak_equity"] * 100.0),
                   "open_positions": self.open_positions()}
        try:
            limits = risk_gate.load_limits(self.limits_file)
        except risk_gate.RiskConfigError:
            limits = None                                  # check_order will then block every order
        if limits and account["drawdown_pct"] >= limits["max_drawdown_pct"] and (
                (not risk_gate.file_is_there(self.latch_file)
                 and self._trip_latch({"tripped_on": str(day.date()), "drawdown_pct": account["drawdown_pct"]}))
                or self._latched_on(day)):                 # tripped today by a run of this day that was killed before saving
            reasoning = f"drawdown {account['drawdown_pct']:.2f}% reached the limit; new risk is blocked until the latch is reset"
            if self._latch_day() == "":
                reasoning += " (the latch file cannot be read or holds no day: it counts as set)"
            self._note(day, {"type": "kill_switch", "decision": "latched", "reasoning": reasoning})

        placed = blocked = 0
        if self.tax_rules is not None:
            self._tax_today, self._tax_fill_day = [], next_session(day)
        share = equity / max(len(strategies), 1)
        for hid, s in strategies.items():
            weights, events = run_rules(s, prices)
            latest = {e["symbol"]: e for e in events}          # events are in time order: the last one per symbol wins
            cond = _finite_only(market_conditions(prices, s.market))
            acted = False
            for sym in s.market:
                px = float(row.get(sym, np.nan))
                if not _is_price(px):
                    continue
                lot = self._lot(s.name, sym)
                target = float(weights[sym].iloc[-1]) * share / px
                delta = target - lot["units"]
                value = abs(delta) * px
                if value < self.min_trade_pct * share:
                    continue
                acted = True
                reduces = abs(target) < abs(lot["units"]) and (target == 0 or np.sign(target) == np.sign(lot["units"]))
                # the position limit and the position count are the account's: what this strategy and the others hold together
                order = {"symbol": sym, "side": "buy" if delta > 0 else "sell", "value": value, "reduces_risk": bool(reduces),
                         "adds_to_existing": sym in self.funds_held(),
                         "position_value_after": abs(self._units_elsewhere(s.name, sym) + target) * px}
                ok, why = risk_gate.check_order(order, account, limits, latch_file=self.latch_file, halt_file=self.halt_file)
                panel_view = None
                if ok and not reduces and self.panel_mode != "off":
                    panel_view = self._consult_panel(day, s, sym, prices, order, account, px)
                    if self.panel_mode == "veto" and not (panel_view["available"] and panel_view["approve"]):
                        ok = False
                        why = ("the panel could not be reached; in veto mode that blocks new risk" if not panel_view["available"]
                               else "the panel refused: " + "; ".join(panel_view["reasons"] or ["below its approval threshold"]))
                ev = latest.get(sym)
                if ev is not None and ev["date"] == day:
                    why_now = f"{ev['action']}: {ev['reason']}"
                elif ev is not None:
                    why_now = f"catching up with the strategy's book ({ev['action']} on {ev['date'].date()}: {ev['reason']})"
                else:
                    why_now = "rebalance to the strategy's target weight"
                reasoning = f"{why_now}; target weight {float(weights[sym].iloc[-1]):+.3f} of the strategy's capital"
                entry = {"type": "decision", "hypothesis_id": hid, "strategy": s.name, "version": s.version, "symbol": sym,
                         "market_conditions": cond, "order": order, "account": dict(account),
                         "decision": "placed" if ok else "blocked", "risk_check": why, "reasoning": reasoning}
                if panel_view is not None:
                    entry["panel"] = panel_view
                if self.tax_rules is not None:
                    entry["tax"] = self._tax_note(day, sym, delta, px)
                    if ok:
                        self._tax_today.append((sym, float(delta), float(px)))
                if ok:
                    placed += 1
                    self.state["pending"].append({"strategy": s.name, "version": s.version, "symbol": sym, "units": delta,
                                                  "decided_on": str(day.date()), "reduces_risk": bool(reduces),
                                                  "reasoning": reasoning, "conditions": cond})
                    if not reduces:
                        account["open_positions"] = self.open_positions()
                    if self.venue is not None:
                        try:
                            entry["venue"] = {"name": self.venue.name, **self.venue.submit(sym, delta, f"{s.name}-{sym}-{day.date()}")}
                        except Exception as exc:  # the journal keeps the truth; the book stays the record
                            entry["venue"] = {"name": self.venue.name, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
                else:
                    blocked += 1
                self._note(day, entry)
            if not acted:
                self._note(day, {"type": "decision", "hypothesis_id": hid, "strategy": s.name, "version": s.version,
                                         "market_conditions": cond, "decision": "no trade",
                                         "reasoning": "positions already match the strategy's targets"})
        by_strategy = {s: self._strategy_value(s, row, carried) - before.get(s, 0.0) for s in self.state["positions"]}
        summary = {"day": str(day.date()), "equity": equity, "daily_loss_pct": account["daily_loss_pct"],
                   "drawdown_pct": account["drawdown_pct"], "open_positions": account["open_positions"],
                   "orders_placed": placed, "orders_blocked": blocked,
                   "strategy_values": {s: self._strategy_value(s, row, carried) for s in self.state["positions"]},
                   "strategy_value_change": by_strategy, "capital_per_strategy": share,
                   "benchmark_values": self._benchmark_values(prices, strategies, share)}
        if stale:
            summary["stale_prices"] = stale
        if suspect:
            summary["suspect_prices"] = suspect
        if unreadable:
            summary["unreadable_prices"] = unreadable
        if self.tax_rules is not None:
            summary["tax"] = self._tax_block(day)
        self.journal.read(since=day - pd.Timedelta(days=DAMAGE_WINDOW_DAYS))      # only to learn what it cannot read
        if self.journal.skipped_lines:                    # visible to the owner: lines the journal could not read
            summary["skipped_journal_lines"] = len(self.journal.skipped_lines)
        summary = _finite_only(summary)
        self.state["history"].append(summary)
        self._remember_prices(row, day, marks)
        self.state["last_day"] = str(day.date())
        self._note(day, {"type": "summary", **summary})
        lines, self._outbox = self._outbox, []
        self.state["journal_outbox"] = lines              # the day and its entries are saved as one step ...
        self._save()
        self._flush_outbox()                              # ... then the entries are appended, and the state saved without them
        return summary

    def _note(self, day, entry: dict) -> None:
        """Keep one journal entry of the day being run, to be written with the day (`step`)."""
        self._outbox.append(Journal.line(Journal.record(day, _finite_only(entry))))

    def _flush_outbox(self) -> None:
        """Append the saved day's journal entries that are not in the journal yet (all of them, unless a kill came while
        they were being appended), then save the state without them."""
        lines = self.state.get("journal_outbox")
        if lines is None:
            return
        present: dict[str, int] = {}
        for day in sorted({json.loads(line)["day"] for line in lines}):
            path = self.journal.dir / f"{day}.jsonl"
            if path.exists():
                for raw in path.read_bytes().decode("utf-8", errors="replace").split("\n"):
                    present[raw] = present.get(raw, 0) + 1
        for line in lines:
            if present.get(line, 0) > 0:
                present[line] -= 1                        # appended before the kill
            else:
                self.journal.append(line)
        del self.state["journal_outbox"]
        self._save()

    def _latch_day(self) -> str | None:
        """The day the latch file says it was tripped on; "" for a file that cannot be opened, cannot be read or holds no
        day; None when there is no file (nothing, a link, which is never followed, or a directory)."""
        try:
            fd = os.open(self.latch_file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            return None
        except OSError:                                           # a link (never followed), or a file it cannot open
            try:
                return "" if stat.S_ISREG(os.lstat(self.latch_file).st_mode) else None
            except OSError:
                return None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            body = json.loads(os.read(fd, 10_000).decode("utf-8"))
        except (OSError, ValueError):
            return ""
        finally:
            os.close(fd)
        return body["tripped_on"] if isinstance(body, dict) and isinstance(body.get("tripped_on"), str) and body["tripped_on"] else ""

    def _latched_on(self, day) -> bool:
        """Whether the latch file counts as tripped on `day`: it says so, or it is a file that cannot be read or holds no
        day (the careful direction: such a file is journaled as the latch on every day the drawdown is at the limit)."""
        tripped = self._latch_day()
        return tripped is not None and tripped in ("", str(pd.Timestamp(day).date()))

    def _benchmark_values(self, prices: pd.DataFrame, strategies: dict[int, Strategy], share: float) -> dict[str, float]:
        """What simply holding each strategy's funds would have made, in dollars, since its first orders filled.

        The same comparator the research gate uses (`buy_and_hold`: equal weight in every fund with a price, rebalanced
        daily, no costs), started with the capital the strategy had on its first paper day. It is a yardstick, not a book:
        it places no orders, meets no risk check and pays no costs. Only the start day and capital are stored; the value
        is recomputed from prices each day. A book saved before this existed takes both from its own history. A day on
        which a fund has no valid price carries its last valid price, as the book does."""
        books, out = self.state.setdefault("benchmarks", {}), {}
        for s in strategies.values():
            if s.name not in books:
                began = next((h for h in self.state["history"] if s.name in h.get("strategy_values", {})), None)
                books[s.name] = {"start_day": began["day"] if began else str(prices.index[-1].date()),
                                 "capital": float(began["capital_per_strategy"] if began else share)}
            funds = [m for m in s.market if m in prices.columns]
            since = prices.index[prices.index > pd.Timestamp(books[s.name]["start_day"])]   # since[0] is the day its first orders filled
            if len(since) < 2 or not funds:
                out[s.name] = 0.0
                continue
            frame = prices[funds]                                          # cleaned by `step`: only valid prices
            if frame.isna().to_numpy().any():                              # a gap: carry the last price
                frame = frame.ffill()
            held = buy_and_hold(frame.loc[since[0]:])
            out[s.name] = float(books[s.name]["capital"] * (np.prod(1.0 + held.iloc[1:]) - 1.0))
        return out

    def _strategy_value(self, strategy: str, row: pd.Series, carried: dict[str, float] | None = None) -> float:
        """Open market value of a strategy's positions plus what it has realised so far, each lot at the same `_mark`
        as in `equity`, so the strategies' values always add up to the book's."""
        open_value = sum(lot["units"] * (self._mark(sym, lot, row, carried) - lot["cost"])
                         for sym, lot in self.state["positions"].get(strategy, {}).items() if lot["units"])
        return float(open_value + self.state["realized"].get(strategy, 0.0))

    def _trip_latch(self, body: dict) -> bool:
        """Create the latch file in one step, only when nothing is at its path: written whole to a temporary file beside
        it, then renamed into place (a kill leaves no latch or a whole one, never an empty one). Never through a link,
        never over what is there: False when something (a file, a link of any kind, a directory) turned up first, and the
        latch then counts as set. Called under the book's lock, which every writer of the latch holds."""
        tmp = self.latch_file.with_name(self.latch_file.name + ".tmp")
        try:
            tmp.unlink()                                          # a temporary file a killed run left (a link: as a link)
        except FileNotFoundError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(body))
            fh.flush()
            os.fsync(fh.fileno())
        if risk_gate.file_is_there(self.latch_file):
            tmp.unlink()
            return False
        try:
            os.rename(tmp, self.latch_file)
        except OSError:                                           # a directory turned up at the path
            tmp.unlink()
            return False
        return True

    def reset_latch(self, who: str, day) -> bool:
        """A person clears the drawdown latch. The reset is journaled; the high-water mark restarts. Under the book's
        lock: refused (`PaperBookBusy`) while another run holds it."""
        try:
            fd = self._locked()
        except _LockCannotOpen as exc:
            raise LatchResetError(f"the drawdown latch {self.latch_file} cannot be cleared: {exc}") from exc
        try:
            return self._reset_latch(who, day)
        finally:
            os.close(fd)

    def _reset_latch(self, who: str, day) -> bool:
        if not risk_gate.file_is_there(self.latch_file):         # as the risk gate sees it: a link of any kind is there
            return False
        try:
            self.latch_file.unlink()                             # a link is removed as a link, never followed
        except FileNotFoundError:
            return False                                         # gone between the check and the removal
        except (OSError, ValueError) as exc:
            if self.latch_file.is_dir() and not self.latch_file.is_symlink():      # macOS reports this one as a permission error
                raise LatchResetError(f"the drawdown latch {self.latch_file} is a directory, not a latch file; "
                                      "nothing was cleared; remove it by hand") from exc
            raise LatchResetError(f"the drawdown latch {self.latch_file} is there and cannot be removed "
                                  f"({type(exc).__name__}); remove it by hand") from exc
        self.state["peak_equity"] = self.state["history"][-1]["equity"] if self.state["history"] else self.state["starting_cash"]
        self.journal.write(day, {"type": "kill_switch", "decision": "reset", "reasoning": f"latch cleared by {who}"})
        self._save()
        return True

    def _save(self) -> None:
        tmp = self.state_file.with_suffix(".json.tmp")
        body = json.dumps(self.state, indent=1, default=str)
        tmp.write_text(body)
        tmp.replace(self.state_file)
        self._seen = body.encode()
