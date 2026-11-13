"""Event-contract practice: one capped order through the order ledger in Webull's paper Events account. Test only.

    python -m valedia_trader.execution_and_risk.event_practice --ticker T --outcome yes|no --contracts N --side buy|sell [--limit P]
    python -m valedia_trader.execution_and_risk.event_practice --reconcile-only

Design: docs/specs/2026-11-13-event-orders-design.md, part 2 (roadmap 4.7). No strategy is wired to it: it proves the
order path for event contracts with the guarantees the order step gives stocks (an order is sent at most once, an
unknown outcome blocks, every fill is journaled once, positions reconcile). `--reconcile-only` sends nothing: it
reconciles, asks Webull to cancel this ledger's open orders, reconciles again and journals the fills not yet journaled.

One run, in order. A failure at any step sends nothing; the reason is returned (and journaled once the lock is held).

 1. environment  the venue's name a plain word ending in "-events" (an Events venue: its order ledger, state and lock
                 files are its own, never the Cash venue's); its `environment` a plain str equal to "sandbox" by a
                 static and by an ordinary lookup, and its `host` present and, by both lookups, a plain str with
                 "sandbox" as a whole dot-separated label (the order step's rule, order_step._sandbox_host); the ledger
                 treating exactly NotTransmitted and OrderRejected as not sent. Read before anything else and again
                 before the order is intended and immediately before submit_once. The adapter refuses event orders in
                 production by itself as well.
    input        the request within the caps fixed here, whatever is passed: a ticker and outcome forming an event
                 name, 1 to MAX_CONTRACTS whole contracts (an int), side buy or sell, a limit given as text or a
                 Decimal with at most two decimals from 0.01 to 0.99 (or none). Checked before anything is read.
    lock         one run at a time per state file; everything below runs under it and journals a summary.
    journal      every journal file is read line by line; a damaged line the order step has not recorded as torn, or a
                 last file not ending in a newline, stops the run (a person must look).
 2. baseline     at the first run (no baseline, no orders on the ledger) the account's event positions are recorded;
                 they are never sold. With orders on the ledger and no baseline, the run stops.
 3. reconcile    reconcile (positions given: baseline plus the fills applied), ask Webull to cancel every order of this
                 ledger still open, reconcile again; each new fill journaled once (keyed by its fill id), the
                 journal file synced to disk, then marked applied. A blocked ledger, an order still open, or a holding
                 that differs from baseline plus fills stops the run; an event holding the ledger expects that is gone
                 from the account (settled?) is not guessed: settlement is not built, and a person must look.
 4. cap          at most MAX_ORDERS_PER_DAY orders handed to the ledger in one New York day, across runs.
 5. sale         a sale is only of contracts this program bought (the ledger's fills of that name), never the baseline;
                 the adapter checks the account's holdings of that ticker and outcome less its open sales as well.
 6. quote        Kalshi's public book (KalshiData.orderbook) for the ticker: two-sided, prices on the cent from 0.01 to
                 0.99, the ask above the bid and at most MAX_SPREAD above it. For `no` the bid is the best no bid and the
                 ask one less the best yes bid. The quote's time is taken before the book is asked for.
 7. limit        by default the ask plus one cent for a purchase, the bid less one cent for a sale, never above 0.99 or
                 below 0.01; a given limit may not cross the book by more than MAX_CROSS (above the ask for a purchase,
                 below the bid for a sale). At most MAX_ORDER_DOLLARS (contracts times limit) an order.
 8. send         the quote at most QUOTE_MAX_AGE_SECONDS old on the clock before ledger.intend and again immediately
                 before ledger.submit_once (with the environment read between); an order failing the second look stays
                 `intended`, never sent. A refusal or an unknown outcome stops the run; an unknown outcome blocks every
                 later run until a reconcile settles it.
 9. after        reconcile once more and journal the fills.

Only the command's own words reach the journal and the summary: from an error its class name and plain code
(order_step._venue_error), from the venue only words and numbers this module checks. The ledger's own file keeps
the venue's text. Nothing here builds a broker client except main(), which reads the secrets file the way the
order step's command does and refuses any configured environment other than the test environment before building it.
"""
from __future__ import annotations

import argparse
import inspect
import json
import math
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

import pandas as pd

from valedia_trader.execution_and_risk.order_step import (CANCEL_WORDS, NOT_SENT, OPEN_STATES, TEST_ENVIRONMENT, _RunLock,
                                                          _sandbox_host, _sync_file, _venue_error, ledger_path_for,
                                                          open_ledger, wall_clock)
from valedia_trader.execution_and_risk.orders import STATES, OrderError, OrderLedger, OrdersBlocked
from valedia_trader.venues.webull import EVENTS_CLASS, parse_event_name

__all__ = ["MAX_CONTRACTS", "MAX_ORDER_DOLLARS", "MAX_ORDERS_PER_DAY", "QUOTE_MAX_AGE_SECONDS", "MAX_SPREAD", "MAX_CROSS",
           "PracticeState", "PracticeError", "run_event_practice", "quote_from_book", "choose_limit", "kalshi_book",
           "build_events_venue", "state_path_for", "main"]

MAX_CONTRACTS = 5
MAX_ORDER_DOLLARS = Decimal("5.00")
MAX_ORDERS_PER_DAY = 10
QUOTE_MAX_AGE_SECONDS = 20.0
MAX_SPREAD = Decimal("0.10")
MAX_CROSS = Decimal("0.02")
CENT = Decimal("0.01")
LOWEST, HIGHEST = Decimal("0.01"), Decimal("0.99")
NEW_YORK = "America/New_York"
EVENTS_SUFFIX = "-events"
JOURNAL_TYPE = "event_practice"
MAX_UNITS = 1e12
SIDES = ("buy", "sell")
NOT_COUNTED = ("refused", "not sent")              # outcomes that are not an order handed to the venue
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_TAG = re.compile(r"[A-Za-z0-9_.-]{1,128}")
_LIMIT_TEXT = re.compile(r"[0-9]{1,3}(?:\.[0-9]{1,2})?")
_ABSENT = object()


class PracticeError(RuntimeError):
    pass


class _Stop(Exception):
    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(reason)
        self.stage, self.reason = stage, reason


def _plain(pattern: re.Pattern, x) -> bool:
    return type(x) is str and pattern.fullmatch(x) is not None


def _as_float(x) -> float | None:
    try:
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return None
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


# ---- pure pieces


def _limit_given(limit) -> Decimal | None:
    """The limit asked for, as a Decimal on the cent from 0.01 to 0.99, or None for none; _Stop("input") otherwise. Text
    of digits with at most two decimals, or a finite Decimal with at most two decimals; nothing else (not a float)."""
    if limit is None:
        return None
    if type(limit) is str:
        if not _LIMIT_TEXT.fullmatch(limit):
            raise _Stop("input", "the limit must be written as a price with at most two decimals, such as 0.45")
        d = Decimal(limit)
    elif isinstance(limit, Decimal):
        d = limit
    else:
        raise _Stop("input", "the limit must be given as text (such as 0.45) or a Decimal")
    try:
        on_cent = d.is_finite() and d == d.quantize(CENT)
    except InvalidOperation:
        on_cent = False
    if not on_cent or not LOWEST <= d <= HIGHEST:
        raise _Stop("input", "the limit must have at most two decimals and be from 0.01 to 0.99")
    return d.quantize(CENT)


def check_request(ticker, outcome, contracts, side, limit) -> dict:
    """The request within the caps, or _Stop("input"). Never adjusts anything."""
    if type(ticker) is not str or type(outcome) is not str or parse_event_name(f"{ticker}:{outcome}") != (ticker, outcome):
        raise _Stop("input", "the ticker must be in Kalshi's shape (a capital letter first, then capitals, digits, dots and "
                             "hyphens, at most 80) and the outcome exactly yes or no")
    if type(contracts) is not int or not 1 <= contracts <= MAX_CONTRACTS:
        raise _Stop("input", f"the order must be for 1 to {MAX_CONTRACTS} whole contracts, given as an int")
    if type(side) is not str or side not in SIDES:
        raise _Stop("input", "the side must be buy or sell")
    return {"ticker": ticker, "outcome": outcome, "name": f"{ticker}:{outcome}", "contracts": contracts, "side": side,
            "limit": _limit_given(limit)}


def _cents(x) -> Decimal | None:
    """A book price as a Decimal when it is a finite number on the cent from 0.01 to 0.99, else None."""
    v = _as_float(x)
    if v is None:
        return None
    d = Decimal(repr(v))
    return d if d == d.quantize(CENT) and LOWEST <= d <= HIGHEST else None


def _bids(levels) -> list:
    """The prices of one side's levels that carry a finite positive size; anything unreadable is left out."""
    out = []
    if not isinstance(levels, (list, tuple)):
        return out
    for level in levels:
        if isinstance(level, (list, tuple)) and len(level) == 2:
            size = _as_float(level[1])
            if size is not None and size > 0 and _as_float(level[0]) is not None:
                out.append(level[0])
    return out


def quote_from_book(book, outcome: str) -> tuple[Decimal, Decimal]:
    """(bid, ask) of `outcome` from Kalshi's book ({"yes": [(price, size), ...], "no": [...]}: each side's bids), or
    _Stop("quote"). The bid is the outcome's best bid; the ask is one less the other side's best bid. Two-sided, on the
    cent, the ask above the bid by at most MAX_SPREAD."""
    if not isinstance(book, dict):
        raise _Stop("quote", "Kalshi's book is not an object; nothing is sent")
    own, other = (book.get("yes"), book.get("no")) if outcome == "yes" else (book.get("no"), book.get("yes"))
    own, other = _bids(own), _bids(other)
    if not own or not other:
        raise _Stop("quote", f"Kalshi's book for the {outcome} side is not two-sided; nothing is sent")
    bid, other_bid = _cents(max(own, key=float)), _cents(max(other, key=float))
    if bid is None or other_bid is None:
        raise _Stop("quote", "Kalshi's best prices are not on the cent from 0.01 to 0.99; nothing is sent")
    ask = Decimal(1) - other_bid
    if not ask > bid:
        raise _Stop("quote", f"Kalshi's spread is not positive (bid {bid}, ask {ask}); nothing is sent")
    if ask - bid > MAX_SPREAD:
        raise _Stop("quote", f"Kalshi's spread of {ask - bid} (bid {bid}, ask {ask}) is above {MAX_SPREAD}; nothing is sent")
    return bid, ask


def choose_limit(side: str, bid: Decimal, ask: Decimal, given: Decimal | None) -> Decimal:
    """The limit sent: the given one if it does not cross the book by more than MAX_CROSS, else _Stop("limit"); by
    default the ask plus a cent (a purchase) or the bid less a cent (a sale), kept within 0.01 to 0.99."""
    if side == "buy":
        limit = given if given is not None else min(ask + CENT, HIGHEST)
        if limit > ask + MAX_CROSS:
            raise _Stop("limit", f"the limit {limit} is above the ask {ask} by more than {MAX_CROSS}; nothing is sent")
    else:
        limit = given if given is not None else max(bid - CENT, LOWEST)
        if limit < bid - MAX_CROSS:
            raise _Stop("limit", f"the limit {limit} is below the bid {bid} by more than {MAX_CROSS}; nothing is sent")
    if not LOWEST <= limit <= HIGHEST:
        raise _Stop("limit", "the limit is not from 0.01 to 0.99; nothing is sent")
    return limit


def kalshi_book(ticker: str) -> dict:
    """Kalshi's public book for `ticker`, best level of each side (keyless; reaches the network). No test calls this."""
    from valedia_trader.venues.kalshi_data import KalshiData
    return KalshiData().orderbook(ticker, depth=1)


def state_path_for(ledger_path: str | Path, venue_name: str) -> Path:
    """The practice command's state file: beside the order ledger, named for the venue."""
    return Path(ledger_path).with_name(f"event_practice-{re.sub(r'[^A-Za-z0-9_.-]', '_', str(venue_name))}.sqlite")


# ---- the state file

STATE_LAYOUT = 1
STATE_SCHEMA = [
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS baseline (symbol TEXT PRIMARY KEY, units REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS sends (tag TEXT PRIMARY KEY, day TEXT NOT NULL, time REAL NOT NULL, outcome TEXT)",
]


class PracticeState:
    """The command's own small file: the baseline (recorded once) and each order handed to the ledger, with its New
    York day and outcome. Every write is one IMMEDIATE transaction."""

    def __init__(self, path: str | Path, clock: Callable[[], float] = wall_clock) -> None:
        self.path, self.clock = str(path), clock
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        try:
            self._db.execute("PRAGMA synchronous = FULL")
            with self._write():
                version = self._db.execute("PRAGMA user_version").fetchone()[0]
                tables = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                if tables and (version != STATE_LAYOUT or not tables <= {"meta", "baseline", "sends"}):
                    raise PracticeError(f"{path} is not an event-practice state file of layout {STATE_LAYOUT}; it is not opened")
                if not tables:
                    if version != 0:
                        raise PracticeError(f"{path} is a database of another kind; it is not opened")
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
            u = _as_float(units)
            if parse_event_name(sym) is None or u is None or not 0 <= u <= MAX_UNITS:
                raise PracticeError("the baseline must be event names with finite numbers from 0")
            if u > 0:
                clean[sym] = u
        with self._write():
            if self._db.execute("SELECT 1 FROM meta WHERE key = 'baseline_recorded'").fetchone():
                raise PracticeError("a baseline is already recorded; it is recorded once")
            self._db.executemany("INSERT INTO baseline (symbol, units) VALUES (?, ?)", sorted(clean.items()))
            self._db.execute("INSERT INTO meta (key, value) VALUES ('baseline_recorded', ?)", (repr(float(self.clock())),))
        return clean

    def sent_on(self, day: str) -> int:
        """Orders handed to the ledger on `day` and not known to have stayed unsent (an outcome never written counts)."""
        marks = ",".join("?" * len(NOT_COUNTED))
        return self._db.execute(f"SELECT COUNT(*) FROM sends WHERE day = ? AND (outcome IS NULL OR outcome NOT IN ({marks}))",
                                (day, *NOT_COUNTED)).fetchone()[0]

    def note_sending(self, tag: str, day: str) -> None:
        with self._write():
            self._db.execute("INSERT INTO sends (tag, day, time, outcome) VALUES (?, ?, ?, NULL)", (tag, day, float(self.clock())))

    def note_outcome(self, tag: str, outcome: str) -> None:
        with self._write():
            self._db.execute("UPDATE sends SET outcome = ? WHERE tag = ?", (outcome, tag))

    def settle_sends(self, ledger: OrderLedger) -> None:
        """A send whose outcome was never written gets the ledger's state now; one still `intended` was never sent."""
        for (tag,) in list(self._db.execute("SELECT tag FROM sends WHERE outcome IS NULL")):
            rec = ledger.order(tag)
            self.note_outcome(tag, "not sent" if rec is None or rec["state"] == "intended" else rec["state"])


# ---- the venue as the ledger sees it


class _LedgerVenue:
    """The Events venue handed to the ledger: order_status passes the event name the ledger recorded for the tag, so
    the adapter checks the row read back against it (another ticker or outcome is an error, never a fill), and the
    submit call's exception or answer is remembered for the command's own words."""

    def __init__(self, venue, ledger: OrderLedger) -> None:
        self._venue, self._ledger = venue, ledger
        self.called, self.error, self.answer = False, None, None

    @property
    def name(self):
        return self._venue.name

    def submit(self, symbol, units, tag, price=None, limit_price=None):
        self.called = True
        try:
            self.answer = self._venue.submit(symbol, units, tag, price=price, limit_price=limit_price)
        except BaseException as exc:
            self.error = exc
            raise
        return self.answer

    def order_status(self, tag):
        rec = self._ledger.order(tag)
        if rec is None or parse_event_name(rec.get("symbol")) is None:
            raise PracticeError("the ledger has no event name for this tag")
        return self._venue.order_status(tag, expected=rec["symbol"])

    def positions(self):
        return self._venue.positions()


# ---- one run


def run_event_practice(*, ledger: OrderLedger, venue, journal, state, ticker=None, outcome=None, contracts=None, side=None,
                       limit=None, reconcile_only: bool = False, book: Callable | None = None,
                       clock: Callable[[], float] = wall_clock) -> dict:
    """One practice run (see the module notes). `state` is a PracticeState or the path of its file; `book(ticker)` gives
    Kalshi's book in KalshiData.orderbook's shape (default kalshi_book). Returns the summary, also journaled once the
    lock is held. Never raises because of the venue, the book or the request."""
    return _Run(ledger, venue, journal, state, dict(ticker=ticker, outcome=outcome, contracts=contracts, side=side, limit=limit),
                reconcile_only is True, book if book is not None else kalshi_book, clock).go()


class _Run:
    def __init__(self, ledger, venue, journal, state, request, reconcile_only, book, clock) -> None:
        self.ledger, self.venue, self.journal, self.state_arg = ledger, venue, journal, state
        self.request, self.reconcile_only, self.book, self.clock = request, reconcile_only, book, clock
        self.view = _LedgerVenue(venue, ledger)
        self.name, self.batch, self.day = "?", None, None
        self.state: PracticeState | None = None
        self.stage = "environment"
        self.cancels: list[dict] = []
        self.order: dict | None = None
        self.quote: dict | None = None
        self.fills_journaled = 0
        self._seen: set = set()
        self._locked = False

    # -- helpers
    def _now(self) -> float:
        try:
            now = self.clock()
        except Exception:
            now = None
        if _as_float(now) is None:
            raise _Stop("clock", "the clock did not give a finite number of seconds")
        return float(now)

    def _write(self, entry: dict) -> None:
        if not self._locked:
            raise RuntimeError("the practice command wrote to the journal without holding the lock")
        self.journal.write(self.day, {"type": JOURNAL_TYPE, "venue": self.name, "batch": self.batch, **entry})

    def _journal_file(self) -> Path | None:
        d = getattr(self.journal, "dir", None)
        return Path(d) / f"{self.day}.jsonl" if d is not None else None

    def go(self) -> dict:
        try:
            now = self._now()
        except _Stop as stop:
            self.day = str(pd.Timestamp.now(tz=NEW_YORK).date())
            return self._finish("stopped", stop.stage, stop.reason)
        stamp = pd.Timestamp(now, unit="s", tz="UTC").tz_convert(NEW_YORK)
        self.day = str(stamp.date())
        self.batch = f"ep-{stamp:%Y%m%d-%H%M%S}-{os.urandom(3).hex()}"
        try:
            self._environment()                          # the venue's name, environment and host, and the ledger
            self.stage = "input"
            req = None if self.reconcile_only else check_request(**self.request)
        except _Stop as stop:
            return self._finish("stopped", stop.stage, stop.reason)
        path = self.state_arg.path if isinstance(self.state_arg, PracticeState) else str(self.state_arg)
        lock = _RunLock(path)
        try:
            got = lock.acquire()
        except OSError as exc:
            return self._finish("stopped", "lock", f"the lock file could not be taken: {_venue_error(exc)}")
        if not got:
            return self._finish("stopped", "lock", "another practice run is running on this venue's state file")
        self._locked = True
        try:
            try:
                self.stage = "journal"
                self._scan_journal()
                self.stage = "state"
                self.state = self.state_arg if isinstance(self.state_arg, PracticeState) else PracticeState(path, self.clock)
            except _Stop as stop:
                return self._finish("stopped", stop.stage, stop.reason)
            except (PracticeError, sqlite3.Error, OSError) as exc:
                return self._finish("stopped", self.stage, f"the journal or the state file could not be read: {_venue_error(exc)}")
            try:
                return self._rest(req)
            finally:
                if not isinstance(self.state_arg, PracticeState):
                    self.state.close()
        except Exception as exc:                         # a fault of the command's own: still stopped and journaled
            return self._finish("stopped", "unexpected", f"the run failed unexpectedly: {_venue_error(exc)}")
        finally:
            lock.release()

    def _rest(self, req: dict | None) -> dict:
        try:
            self.stage = "baseline"
            baseline = self._baseline()
            self.stage = "reconcile"
            self._reconcile_and_cancel(baseline)
            if req is None:
                return self._finish("done", None, None)
            self._send(req)
        except _Stop as stop:
            return self._finish("stopped", stop.stage, stop.reason)
        except OrdersBlocked:
            return self._finish("stopped", self.stage, "the order ledger is blocked; nothing is sent")
        except (OrderError, PracticeError, sqlite3.Error, OSError) as exc:
            return self._finish("stopped", self.stage, f"the ledger, the state file or the journal failed: {_venue_error(exc)}; "
                                                       "nothing more is sent and the next run's reconcile settles what was")
        try:
            self.stage = "after"
            self._reconcile(baseline)
        except (OrderError, PracticeError, sqlite3.Error, OSError) as exc:
            return self._finish("stopped", "after", f"the last reconcile could not be completed: {_venue_error(exc)}")
        return self._finish("done", None, None)

    # -- 1
    def _check_name_and_environment(self) -> None:
        try:
            name = self.venue.name
        except Exception:
            name = None
        if not _plain(_NAME, name) or not name.endswith(EVENTS_SUFFIX):
            raise _Stop("environment", "the venue is not an Events venue: its name must be a plain word ending in -events (a "
                                       f"Webull venue built for the {EVENTS_CLASS} class), so that its ledger is its own")
        self.name = name
        self._check_environment()

    def _environment(self) -> None:
        self._check_name_and_environment()
        not_sent = tuple(getattr(self.ledger, "not_sent", ()) or ())
        missing = [c.__name__ for c in NOT_SENT if c not in not_sent]
        broader = [t for t in not_sent if not (isinstance(t, type) and issubclass(t, NOT_SENT))]
        if missing or broader:
            raise _Stop("environment", "the order ledger must treat exactly NotTransmitted and OrderRejected as not sent; open "
                                       "it with order_step.open_ledger")

    def _check_environment(self) -> None:
        try:
            static_env = inspect.getattr_static(self.venue, "environment", _ABSENT)
        except Exception:
            static_env = None
        try:
            env = self.venue.environment
        except Exception:
            env = None
        if not all(type(e) is str and e == TEST_ENVIRONMENT for e in (static_env, env)):
            raise _Stop("environment", f"event practice runs against Webull's test environment ({TEST_ENVIRONMENT}, a plain "
                                       "str) only; this venue's environment is not that. Live event orders are not built")
        try:
            static = inspect.getattr_static(self.venue, "host", _ABSENT)
        except Exception:
            static = None
        try:
            dynamic = getattr(self.venue, "host", _ABSENT)
        except Exception:
            dynamic = None
        if not all(_sandbox_host(h) for h in (static, dynamic)):
            raise _Stop("environment", "the venue's host is not a plain str attribute naming the sandbox")

    # -- the journal
    def _tag_word(self, tag):
        return tag if _plain(_TAG, tag) else None

    def _scan_journal(self) -> None:
        d = getattr(self.journal, "dir", None)
        if d is None:
            try:
                entries = list(self.journal.read())
            except Exception as exc:
                raise _Stop("journal", f"the journal could not be read: {_venue_error(exc)}") from None
        else:
            try:
                files = sorted(Path(d).glob("????-??-??.jsonl"))
                entries, torn = [], []
                for i, path in enumerate(files):
                    data = path.read_bytes()
                    if data and not data.endswith(b"\n"):
                        raise _Stop("journal", f"the journal file {path.name} does not end with a full line; a person must look")
                    for n, raw in enumerate(data.split(b"\n"), 1):
                        if not raw.strip():
                            continue
                        try:
                            obj = json.loads(raw)
                        except ValueError:
                            obj = None
                        if isinstance(obj, dict):
                            entries.append(obj)
                        else:
                            torn.append((path.name, n))
            except OSError as exc:
                raise _Stop("journal", f"the journal could not be read: {_venue_error(exc)}") from None
            known = {(e.get("file"), e.get("line")) for e in entries
                     if e.get("type") == "order_step" and e.get("event") == "torn_line"}
            bad = [t for t in torn if t not in known]
            if bad:
                raise _Stop("journal", "the journal has a damaged line ("
                                       + ", ".join(f"{name} line {n}" for name, n in bad[:5]) + "); a person must look")
        # keyed by the fill id alone, as the order step does: Journal.write may redact a piece of any text (a tag) that
        # happens to look like a remembered secret, and a key holding text would then never match again
        self._seen = {e.get("fill_id") for e in entries
                      if e.get("type") == JOURNAL_TYPE and e.get("event") == "fill" and e.get("venue") == self.name}

    # -- 2
    def _baseline(self) -> dict[str, float]:
        base = self.state.baseline()
        if base is not None:
            return base
        if self.ledger.orders():
            raise _Stop("baseline", "no baseline is recorded but the order ledger already has orders; a person must look")
        try:
            held = dict(self.venue.positions())
        except Exception as exc:
            raise _Stop("baseline", f"the venue's positions could not be read: {_venue_error(exc)}") from None
        bad = [k for k, u in held.items() if parse_event_name(k) is None or _as_float(u) is None or not 0 <= _as_float(u) <= MAX_UNITS]
        if bad:
            raise _Stop("baseline", f"the venue's positions hold {len(bad)} entries that are not an event name with a finite "
                                    "number from 0; no baseline is recorded")
        base = self.state.record_baseline(held)
        self._write({"event": "baseline", "positions": base})
        return base

    # -- 3
    def _applied_positions(self, baseline: dict) -> dict[str, float]:
        out = dict(baseline)
        for f in self.ledger.fills():
            if f["applied"]:
                out[f["symbol"]] = math.fsum((out.get(f["symbol"], 0.0), float(f["units"])))
        return out

    def _reconcile(self, baseline: dict) -> dict:
        result = self.ledger.reconcile(self.view, self._applied_positions(baseline))
        self._journal_fills()
        return result

    def _journal_fills(self) -> None:
        for f in self.ledger.unapplied_fills():
            key = f["id"]
            if key not in self._seen:
                self._write({"event": "fill", "fill_id": f["id"], "tag": self._tag_word(f["tag"]),
                             "symbol": f["symbol"] if parse_event_name(f["symbol"]) else None,
                             "units": _as_float(f["units"]), "price": _as_float(f["price"]),
                             "price_covers_units": _as_float(f["price_covers_units"]), "fee": _as_float(f["fee"])})
                path = self._journal_file()
                if path is not None:
                    _sync_file(path)                     # on disk before the fill counts as applied
                self._seen.add(key)
                self.fills_journaled += 1
            self.ledger.mark_applied([f["id"]])

    def _reconcile_and_cancel(self, baseline: dict) -> None:
        self.state.settle_sends(self.ledger)
        self._reconcile(baseline)
        for o in self.ledger.orders(OPEN_STATES):
            tag = self._tag_word(o["tag"]) or "an order"
            try:
                answer = self.venue.cancel(o["tag"])
                word = answer.get("status") if isinstance(answer, dict) else None
                word = word if type(word) is str and word in CANCEL_WORDS else "an answer that is not a cancel word"
            except Exception as exc:
                word = f"error: {_venue_error(exc)}"
            self.cancels.append({"tag": tag, "answer": word})
            self._write({"event": "cancel", "tag": tag, "answer": word})
        result = self._reconcile(baseline)
        still = self.ledger.orders(OPEN_STATES)
        if still or self.ledger.blocked():
            raise _Stop("reconcile", "; ".join(self._ledger_words(still, result))[:2000])

    def _ledger_words(self, still: list[dict], result: dict) -> list[str]:
        """Why the ledger stands in the way, in the command's own words (tags, states, names, numbers)."""
        reasons = []
        if still:
            reasons.append(f"{len(still)} order(s) of this ledger still open at the broker after the cancel requests ("
                           + ", ".join(f"{self._tag_word(o['tag']) or 'an order'} {o['state'] if o['state'] in STATES else '?'}"
                                       for o in still) + "); the next run asks again")
        unsettled = self.ledger.orders(["sent", "unknown"])
        if unsettled:
            reasons.append(f"{len(unsettled)} order(s) whose outcome is not known ("
                           + ", ".join(self._tag_word(o["tag"]) or "an order" for o in unsettled)
                           + "); nothing is sent until a reconcile settles them, and a person must look if it cannot")
        if self.ledger.attention():
            reasons.append(f"{len(self.ledger.attention())} attention item(s) open on the ledger; a person must look")
        diffs = result.get("position_differences") or {}
        if diffs:
            plain = {}
            for sym, d in diffs.items():
                if parse_event_name(sym) is not None and isinstance(d, dict):
                    plain[sym] = {k: _as_float(d.get(k)) for k in ("book", "venue", "unapplied")}
            vanished = sorted(sym for sym, d in plain.items()
                              if (d["book"] or 0.0) + (d["unapplied"] or 0.0) > 0 and not (d["venue"] or 0.0) > 0)
            if vanished:
                reasons.append(f"an event holding the ledger expects is not in the account ({', '.join(vanished)}); it may have "
                               "settled, and settlement is not built, so it is not guessed: a person must look")
            reasons.append(f"positions differ from baseline plus fills: {json.dumps(plain, sort_keys=True)}"
                           + (f" and {len(diffs) - len(plain)} more" if len(diffs) > len(plain) else "") + "; a person must look")
        if result.get("positions_error"):
            reasons.append("the venue's positions could not be read")
        if result.get("unresolved"):
            reasons.append(f"{len(result['unresolved'])} order(s) whose status could not be read or used")
        if result.get("anomalies"):
            reasons.append(f"{len(result['anomalies'])} anomaly(ies) in the reconcile; a person must look")
        return reasons or ["the order ledger is blocked"]

    # -- 4 to 9
    def _fresh(self, quote_at: float) -> None:
        age = self._now() - quote_at
        if not 0 <= age <= QUOTE_MAX_AGE_SECONDS:
            raise _Stop("quote", f"Kalshi's quote is {age:.0f} s old, more than {QUOTE_MAX_AGE_SECONDS:.0f} s; nothing is sent")

    def _send(self, req: dict) -> None:
        name, contracts, side = req["name"], req["contracts"], req["side"]
        self.stage = "cap"
        if self.state.sent_on(self.day) >= MAX_ORDERS_PER_DAY:
            raise _Stop("cap", f"the day's cap of {MAX_ORDERS_PER_DAY} practice orders ({self.day}, New York) is reached")
        if side == "sell":
            self.stage = "sale"
            owned = math.fsum(float(f["units"]) for f in self.ledger.fills() if f["symbol"] == name)
            if owned < contracts - 1e-9:
                raise _Stop("sale", f"a sale is only of contracts this program bought: the ledger holds {owned:g} of {name}; "
                                    "the baseline is never sold")
        self.stage = "quote"
        quote_at = self._now()
        try:
            raw = self.book(req["ticker"])
        except Exception as exc:
            raise _Stop("quote", f"Kalshi's book could not be read: {_venue_error(exc)}") from None
        bid, ask = quote_from_book(raw, req["outcome"])
        self.quote = {"bid": float(bid), "ask": float(ask), "time": quote_at}
        self.stage = "limit"
        limit = choose_limit(side, bid, ask, req["limit"])
        value = contracts * limit
        if value > MAX_ORDER_DOLLARS:
            raise _Stop("cap", f"the order's value of {value} is above {MAX_ORDER_DOLLARS} dollars; nothing is sent")
        self.stage = "send"
        units = float(contracts if side == "buy" else -contracts)
        self._check_environment()
        self._fresh(quote_at)
        tag = f"{self.batch}-{side}"
        self.ledger.intend(tag, name, units, float(ask if side == "buy" else bid), limit_price=float(limit), batch=self.batch)
        self.state.note_sending(tag, self.day)
        try:
            self._check_environment()                    # the last look: environment, then the quote's age
            self._fresh(quote_at)
        except _Stop:
            self.state.note_outcome(tag, "not sent")     # it stays intended, never sent
            raise
        try:
            rec = self.ledger.submit_once(self.view, tag)
        except OrdersBlocked:
            raise _Stop("send", f"the ledger refused to send {tag}: it is blocked; nothing is sent") from None
        state = rec["state"] if rec.get("state") in STATES else "unknown"
        self.state.note_outcome(tag, state)
        entry = {"tag": tag, "symbol": name, "units": units, "side": side, "limit_price": float(limit),
                 "bid": float(bid), "ask": float(ask), "state": state}
        self.order = entry
        if state == "refused":
            why = f"the venue did not take it: {_venue_error(self.view.error)}" if self.view.error is not None else "the venue did not take it"
            self._write({"event": "refused", **entry, "reason": why})
            raise _Stop("send", why)
        if state != "accepted":
            why = (f"the venue's call raised {_venue_error(self.view.error)}" if self.view.error is not None
                   else "the venue did not say accepted")
            self._write({"event": "unknown", **entry, "reason": why})
            raise _Stop("send", f"the outcome of order {tag} ({name} {units:+g}) is {state}: {why}; nothing more is sent until "
                                "a reconcile settles it")
        self._write({"event": "order", **entry})

    # -- the summary
    def _finish(self, status: str, stage: str | None, reason: str | None) -> dict:
        if self.order is not None and status == "done":
            try:
                rec = self.ledger.order(self.order["tag"])
                self.order = {**self.order, "state": rec["state"] if rec and rec.get("state") in STATES else "unknown",
                              "filled_units": _as_float(rec.get("filled_units")) if rec else None}
            except Exception:
                pass
        summary = {"status": status, "stage": stage, "reason": reason, "venue": self.name, "batch": self.batch, "day": self.day,
                   "order": self.order, "quote": self.quote, "cancels": self.cancels, "fills_journaled": self.fills_journaled,
                   "caps": {"contracts": MAX_CONTRACTS, "dollars": float(MAX_ORDER_DOLLARS), "orders_a_day": MAX_ORDERS_PER_DAY,
                            "quote_age_seconds": QUOTE_MAX_AGE_SECONDS, "spread": float(MAX_SPREAD), "cross": float(MAX_CROSS)}}
        try:
            if self._locked:
                if status == "stopped":
                    self._write({"event": "stopped", "stage": stage, "reason": reason})
                self._write({"event": "summary", **summary})
        except Exception as exc:
            summary["journal_error"] = _venue_error(exc)
        return summary


# ---- the command line


def events_environment(environ) -> dict:
    """A copy of the loaded settings with WEBULL_ACCOUNT_CLASS set to EVENTS_CASH and WEBULL_ACCOUNT_ID dropped (the
    account is chosen by its class), whatever the secrets file says."""
    chosen = {k: v for k, v in dict(environ).items() if k != "WEBULL_ACCOUNT_ID"}
    chosen["WEBULL_ACCOUNT_CLASS"] = EVENTS_CLASS
    return chosen


def build_events_venue(environ):
    """The Webull venue for the Events Cash account from the settings in `environ` (see events_environment)."""
    from valedia_trader.venues.webull import WebullVenue
    return WebullVenue.from_secrets(events_environment(environ))


def _loaded_environment():
    from valedia_trader.core.secrets import load_env
    load_env()
    return os.environ


def main(argv: list[str] | None = None, *, environ=None, build_venue: Callable | None = None, root: str | Path | None = None,
         book: Callable | None = None, clock: Callable[[], float] | None = None) -> int:
    """The command line (see the module notes). Prints the summary; 0 when the run went through, 2 when it stopped or
    was refused. A configured environment other than the test environment is refused before the venue is built, and the
    venue's name, environment and host are checked before any folder or file is made."""
    ap = argparse.ArgumentParser(prog="python -m valedia_trader.execution_and_risk.event_practice",
                                 description="One capped event-contract practice order in Webull's paper Events account.")
    ap.add_argument("--ticker")
    ap.add_argument("--outcome", choices=["yes", "no"])
    ap.add_argument("--contracts", type=int)
    ap.add_argument("--side", choices=list(SIDES))
    ap.add_argument("--limit", help="a price with at most two decimals, such as 0.45 (default: the ask plus a cent to buy, "
                                    "the bid less a cent to sell)")
    ap.add_argument("--reconcile-only", action="store_true", help="send nothing: reconcile, cancel this ledger's open "
                                                                   "orders, journal the fills")
    args = ap.parse_args(argv)
    if not args.reconcile_only and None in (args.ticker, args.outcome, args.contracts, args.side):
        ap.error("--ticker, --outcome, --contracts and --side are needed unless --reconcile-only is given")
    from valedia_trader.core.secrets import SecretError
    from valedia_trader.venues.webull import WebullError
    try:
        env = environ if environ is not None else _loaded_environment()
        configured = (env.get("WEBULL_ENV") or "sandbox").strip().lower()
    except Exception as exc:
        print(f"refused: the trading secrets file could not be loaded ({_venue_error(exc)})", file=sys.stderr)
        return 2
    if configured != TEST_ENVIRONMENT:
        print(f"refused: event practice runs against Webull's test environment ({TEST_ENVIRONMENT}) only and WEBULL_ENV names "
              "another; live event orders are not built", file=sys.stderr)
        return 2
    try:
        venue = (build_venue or build_events_venue)(events_environment(env))
    except (WebullError, SecretError, OSError) as exc:
        print(f"refused: the Webull Events venue could not be set up ({_venue_error(exc)})", file=sys.stderr)
        return 2
    if venue is None:
        return 2
    try:                                                 # before any folder or file is made
        _Run(None, venue, None, None, {}, True, None, clock or wall_clock)._check_name_and_environment()
    except _Stop as stop:
        print(f"refused: {stop.reason}", file=sys.stderr)
        return 2
    from valedia_trader.research_loop import LoopPaths
    from valedia_trader.research_loop.paper import Journal
    paths = LoopPaths(Path(root)) if root is not None else LoopPaths()
    clock = clock or wall_clock
    ledger_path = ledger_path_for(paths.memory, venue.name)
    ledger = open_ledger(ledger_path, clock=clock)
    try:
        out = run_event_practice(ledger=ledger, venue=venue, journal=Journal(paths.journal),
                                 state=state_path_for(ledger_path, venue.name), ticker=args.ticker, outcome=args.outcome,
                                 contracts=args.contracts, side=args.side, limit=args.limit,
                                 reconcile_only=args.reconcile_only, book=book, clock=clock)
    finally:
        ledger.close()
    print(json.dumps(out, indent=2, default=str))
    return 0 if out["status"] == "done" else 2


if __name__ == "__main__":
    sys.exit(main())
