"""The order ledger: every real order is written down before it is sent, sent at most once, and settled from the venue.

Three things this module makes true, whatever broker sits behind it, and however many `OrderLedger` objects or
processes share one file:

* at most once     only the caller that wins the change `intended -> sent` inside the database (an IMMEDIATE
                   write transaction, so two callers cannot both win) calls the venue, and the change is on disk
                   before the venue hears anything. Nothing here retries.
* never assumed    an order whose fate is not known (the venue raised, gave an answer that is not "accepted",
                   or the process died mid-call and left `sent`) is `unknown`. It is never taken as sent or as
                   not sent, and while any order is `sent` or `unknown` no further order may be submitted.
* venue is truth   `reconcile` asks the venue about every order still open on the ledger, records what it
                   says, writes each new fill once as a durable row, and compares the venue's positions with
                   the book plus the fills the book has not applied yet. A ledger file left by a crashed process
                   needs no repair: an order found in `sent` is simply treated as unknown until a reconcile
                   settles it.

Order states: intended, sent, accepted, partly_filled, filled, cancelled, rejected, expired, refused, unknown,
never_arrived. Terminal (never sent again, never change state again): filled, cancelled, rejected, expired,
refused; a filled, cancelled or expired order with something filled (or finished on a report that gave no amount) is
still asked about for `recheck_seconds`, because brokers often post fees, averages and amounts after the fact;
a larger amount is taken (a notice when the venue still names the order's own finished state, an anomaly when it
names another), a smaller amount or a different state is an anomaly that changes nothing. `refused` means the order was stopped on this machine before any network call (an exception listed
in `not_sent`). `never_arrived` means the venue still had no order with the tag `settle_seconds` after it was
marked `sent`, or a person said so with `settle_by_hand`; it is never sent again, but for `recheck_seconds`
after it was so marked each reconcile asks the venue again, and an order that turns up after all is an
anomaly that moves to the state the venue reports, with its fills.

Every state change is a compare-and-swap: it applies only if the order is still in the state it was computed
from, and only forward (the allowed moves are in FORWARD). A change that loses does nothing and writes no
event. Every state change is appended to an events table; fills are rows in a fills table that may only be
marked applied. Triggers refuse an ordinary UPDATE or DELETE of either; they guard against this module's own
code and ordinary statements, not against a program that opens the file to rewrite it (an INSERT OR REPLACE,
or dropping the triggers, gets past them).

Fills: each increase of the venue's filled amount is one row (units signed like the order, the increment
only). Fee and price are taken from every report, so a report that raises the fee total, or gives an average
for filled units not yet priced, without a rise in units makes an adjustment row with units 0.0. A row's fee
is what the venue's fee total exceeds the sum of the order's fee rows by (T34b-3 M3; never negative, None when
it does not exceed it); its price is the price of all units since the last report that gave an average with an
amount (`price_covers_units` of them). Over any sequence the units add up to the stored total; the fees never sum
above the highest fee total the venue reported (and equal the last one while it never fell and never went below
zero) and, when the last report with an average left nothing unpriced, the priced rows to that average times that
total, until the venue restates one of them (below). A fee total recorded below zero is kept as the order's total
and opens one notice per order (an attention item of a kind in NOTICE_KINDS, which does not block).

Every entry a reconcile lists carries a `kind` from a closed set (ANOMALY_KINDS, UNRESOLVED_KINDS, RECHECK_KINDS;
`positions_error_kind` from POSITIONS_ERROR_KINDS beside `positions_error`). T34b-3: three things honest brokers do
after the fact are NOTICES (`notices`, kinds in RECONCILE_NOTICE_KINDS): they neither make the reconcile unclean nor
block, and the stored figure follows the venue's:
  avg_restated    an average restated with no new units, by at most RESTATE_BOUND (a cent) plus REL_TOL: the order's
                  avg_price and priced_avg take the venue's figure. More than that is the anomaly
                  avg_restated_beyond, stored figure kept: this blocks on purpose an honest trade price corrected by
                  more than a cent, which a person must look at;
  late_amount     a larger amount, within the order's size, on a finished order the venue still reports in that same
                  state: the units are taken as a fill (another state with more units: late_amount_other_state);
  fee_fell        a fee total that fell and is still zero or more: the order's stored total takes it and NO row is
                  written, so the rows' fees then sum to the earlier, higher total, and a later rise writes a fee only
                  for what goes beyond that sum (M3). A fall to below zero is
                  the anomaly fee_fell_below_zero, stored total kept: deliberately unlike a first total below zero,
                  which is kept with its negative_fee notice;
  piece_unpriced  an average, finite and above zero, that implies a negative or non-finite price for the units since
                  the last priced point while a cent of rounding on each average explains it
                  ((avg + 0.01) x total - (priced_avg - 0.01) x priced_units >= 0): the priced point moves to the
                  venue's average and total, the piece's row carries price None. Beyond that bound:
                  piece_price_unexplained, the priced point kept and the units still taken with price None.
After a restatement the rows keep the prices first worked out, and the order's average is the venue's: the rows no
longer sum to it. A notice is recorded in the attention table, in the same write that follows the figure, with
`after_fill` (the largest fill-row id of the order once that write's own row is in) and `row` (that row's id, or
None), so `average_writes` can replay the order's averages from these records alone; an earlier open notice of the
same order and kind is first closed as superseded (by LEDGER_CLOSER). A figure is never followed without its
record: a notice whose insert is refused (it did not take) rolls the write back and the entry is the anomaly
notice_not_recorded; an insert that aborts (a database error) rolls it back too and the order is unresolved,
not_written. The reconcile is unclean and the stored figure kept either way. A non-finite average or fee stays the anomaly value_not_finite. A reconcile reports
a fill or an update only once the write that made it true is committed; an order whose write failed is listed as unresolved. The book must apply every row (`unapplied_fills`, then `mark_applied`)
before another order may be submitted, because orders are sized from the book.

Strategies that share one account trade through one netted order per symbol: `net_orders` builds it and
`allocate` shares a venue fill back out to the strategies' parts.

Nothing here talks to a network; the venue is whatever object is passed in.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import stat
import time
import urllib.parse
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable, Protocol

__all__ = ["LiveVenue", "OrderLedger", "OrderError", "OrdersBlocked", "net_orders", "allocate", "STATES", "TERMINAL",
           "FORWARD", "NOTICE_KINDS", "RECONCILE_NOTICE_KINDS", "ANOMALY_KINDS", "UNRESOLVED_KINDS", "RECHECK_KINDS",
           "POSITIONS_ERROR_KINDS", "ORDER_TYPES", "READABLE_LAYOUTS", "open_read_only",
           "unreadable_layout"]

STATES = ("intended", "sent", "accepted", "partly_filled", "filled", "cancelled", "rejected", "expired", "refused",
          "unknown", "never_arrived")
TERMINAL = frozenset({"filled", "cancelled", "rejected", "expired", "refused"})
UNSETTLED = ("sent", "unknown")                           # fate not known: blocks every further submit
CHECKED = ("sent", "unknown", "accepted", "partly_filled")  # what a reconcile asks the venue about (plus rechecks)
FINISHED = ("filled", "cancelled", "expired")             # asked again for late fees and averages
STATUS_KEYS = ("state", "filled_units", "avg_price", "fee", "venue_order_id", "raw_status")
VENUE_STATES = {"open": "accepted", "partly_filled": "partly_filled", "filled": "filled", "cancelled": "cancelled",
                "rejected": "rejected", "expired": "expired"}
_SETTLED_BY_VENUE = {"accepted", "partly_filled", "filled", "cancelled", "rejected", "expired"}
FORWARD = {                                               # the only moves any code here makes
    "intended": frozenset({"sent"}),
    "sent": frozenset(_SETTLED_BY_VENUE | {"refused", "unknown", "never_arrived"}),
    "unknown": frozenset(_SETTLED_BY_VENUE | {"never_arrived"}),
    "accepted": frozenset({"partly_filled", "filled", "cancelled", "rejected", "expired"}),
    "partly_filled": frozenset({"filled", "cancelled", "rejected", "expired"}),
    "never_arrived": frozenset(_SETTLED_BY_VENUE),
}
EPS = 1e-9                                                # netting and allocation: units this close are the same
MIN_UNITS = 1e-8                                          # the smallest order `intend` accepts
REL_TOL = 1e-9                                            # filled amounts compare within REL_TOL x max(1, |units|)
MAX_TEXT = 200
BUSY_SECONDS = 30.0                                       # how long a connection waits for another's write to end
# T34b-3: what a broker restates after the fact within bounds an honest broker keeps: told, followed, never blocking
RECONCILE_NOTICE_KINDS = frozenset({"avg_restated", "late_amount", "fee_fell", "piece_unpriced"})
NOTICE_KINDS = frozenset({"negative_fee"}) | RECONCILE_NOTICE_KINDS   # attention kinds that tell a person, never block
# T34b-3: the kind every entry of a reconcile carries beside its tag and reason (closed sets)
ANOMALY_KINDS = frozenset({"reconcile_failed", "not_storable", "status_not_dict", "units_not_finite", "turned_up",
                           "missing_at_venue", "value_not_finite", "units_negative", "units_fell", "overfill",
                           "late_amount_other_state", "state_changed", "fee_fell_below_zero", "piece_price_unexplained",
                           "avg_restated_beyond", "notice_not_recorded"})
UNRESOLVED_KINDS = frozenset({"status_unread", "answer_unusable", "status_not_dict", "status_unreadable", "not_written",
                              "not_found_yet", "state_unknown", "filled_short"})
RECHECK_KINDS = frozenset({"status_unread", "gone", "state_unknown"})
POSITIONS_ERROR_KINDS = frozenset({"positions_unread", "positions_malformed"})
RESTATE_BOUND = 0.01                                      # one cent: what a broker's rounding can move an average by
LEDGER_CLOSER = "order ledger"                            # who closes a notice superseded by a later one of its kind


class OrderError(RuntimeError):
    pass


class _NoticeNotRecorded(OrderError):
    """Inside a reconcile's write: a notice did not take, so nothing of that venue answer may be followed."""


class OrdersBlocked(OrderError):
    """No order may be submitted now; `.reasons` says why."""

    def __init__(self, reasons: Iterable[str]) -> None:
        self.reasons = list(reasons)
        super().__init__("orders are blocked: " + "; ".join(self.reasons))


class LiveVenue(Protocol):
    """What a real venue must offer the ledger.

    `submit` returns a dict whose "status" is "accepted" when the venue took the order (it may carry
    "venue_order_id"). `order_status(tag)` returns None when the venue has no order with that tag, else
    {"state", "filled_units", "avg_price", "fee", "venue_order_id", "raw_status"} with state one of open,
    partly_filled, filled, cancelled, rejected, expired or unknown_status. filled_units is unsigned, or None
    when the venue did not say (never read as 0); avg_price is the average over everything filled so far and
    fee the fee total so far, each None when the venue did not say. A value that is present but not a finite
    number is an anomaly. `positions()` returns units held per symbol. A venue that cannot tell "no such order"
    from a failure should raise rather than return None; the order then stays unknown until `settle_by_hand`."""
    name: str

    def submit(self, symbol: str, units: float, tag: str, price: float | None = None,
               limit_price: float | None = None) -> dict: ...

    def order_status(self, tag: str) -> dict | None: ...

    def positions(self) -> dict[str, float]: ...


def _describe(exc: BaseException) -> str:
    """The exception's class name and at most 200 characters of its message; never raises."""
    try:
        name = type(exc).__name__
    except Exception:
        name = "Exception"
    try:
        text = str(exc)
    except Exception:
        return _storable(name)
    return _storable(f"{name}: {text[:MAX_TEXT]}")


def _safe_repr(x) -> str:
    try:
        return _storable(repr(x)[:MAX_TEXT])
    except Exception:
        return f"<{type(x).__name__} that cannot be shown>"


def _storable(text: str) -> str:
    """Text from outside made safe to store: anything UTF-8 cannot encode (a lone surrogate) becomes '?'."""
    return str(text).encode("utf-8", "replace").decode("utf-8")


def _one_line(text) -> str:
    """A path or name made safe to show on one line of a message."""
    return _storable(str(text)).replace("\r", "\\r").replace("\n", "\\n")


def _finite(x) -> bool:
    """A number that reads as a finite float (an int too large for a float is not)."""
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return False
    try:
        return math.isfinite(float(x))
    except (OverflowError, ValueError):
        return False


def _utf8(text) -> bool:
    try:
        text.encode("utf-8")
        return True
    except UnicodeError:
        return False


def _same_parts(a, b) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _tol(units: float) -> float:
    return REL_TOL * max(1.0, abs(units))


LAYOUT_VERSION = 4                                        # PRAGMA user_version of a file this module writes
READABLE_LAYOUTS = (3, 4)                                 # what `open_read_only` reads; a write-open upgrades 3 to 4
ORDER_TYPES = ("LIMIT", "MARKET")                         # T51 R7: an order's type, fixed when it is intended
_LAYOUT3_SCHEMA = [
    "CREATE TABLE IF NOT EXISTS orders (tag TEXT PRIMARY KEY, symbol TEXT NOT NULL, units REAL NOT NULL, "
    "reference_price REAL NOT NULL, limit_price REAL, batch TEXT, parts TEXT, state TEXT NOT NULL, created REAL NOT NULL, "
    "sent_at REAL, updated REAL NOT NULL, venue TEXT, venue_order_id TEXT, filled_units REAL NOT NULL DEFAULT 0, "
    "avg_price REAL, fee REAL, raw_status TEXT, note TEXT, priced_units REAL NOT NULL DEFAULT 0, priced_avg REAL, "
    "never_arrived_at REAL, finished_at REAL, amount_unconfirmed INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT NOT NULL, time REAL NOT NULL, "
    "from_state TEXT, to_state TEXT NOT NULL, note TEXT)",
    "CREATE TABLE IF NOT EXISTS reconciles (id INTEGER PRIMARY KEY AUTOINCREMENT, time REAL NOT NULL, "
    "clean INTEGER NOT NULL, summary TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS fills (id INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT NOT NULL, symbol TEXT NOT NULL, "
    "units REAL NOT NULL, price REAL, price_covers_units REAL, fee REAL, time REAL NOT NULL, "
    "applied INTEGER NOT NULL DEFAULT 0, applied_time REAL)",
    "CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events "
    "BEGIN SELECT RAISE(ABORT, 'events are append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events "
    "BEGIN SELECT RAISE(ABORT, 'events are append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS fills_only_mark_applied BEFORE UPDATE ON fills "
    "WHEN NOT (OLD.applied = 0 AND NEW.applied = 1 AND OLD.applied_time IS NULL AND NEW.applied_time IS NOT NULL "
    "AND NEW.id IS OLD.id AND NEW.tag IS OLD.tag AND NEW.symbol IS OLD.symbol AND NEW.units IS OLD.units "
    "AND NEW.price IS OLD.price AND NEW.price_covers_units IS OLD.price_covers_units AND NEW.fee IS OLD.fee "
    "AND NEW.time IS OLD.time) "
    "BEGIN SELECT RAISE(ABORT, 'a fill row may only be marked applied'); END",
    "CREATE TRIGGER IF NOT EXISTS fills_no_delete BEFORE DELETE ON fills "
    "BEGIN SELECT RAISE(ABORT, 'fill rows are never deleted'); END",
    "CREATE TABLE IF NOT EXISTS attention (id INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT NOT NULL, kind TEXT NOT NULL, "
    "time REAL NOT NULL, reason TEXT NOT NULL, closed_by TEXT, closed_note TEXT, closed_time REAL)",
    "CREATE UNIQUE INDEX IF NOT EXISTS attention_one_open ON attention (tag, kind) WHERE closed_time IS NULL",
    "CREATE TRIGGER IF NOT EXISTS attention_only_close BEFORE UPDATE ON attention "
    "WHEN NOT (OLD.closed_time IS NULL AND NEW.closed_time IS NOT NULL AND NEW.closed_by IS NOT NULL "
    "AND NEW.closed_note IS NOT NULL AND NEW.id IS OLD.id AND NEW.tag IS OLD.tag AND NEW.kind IS OLD.kind "
    "AND NEW.time IS OLD.time AND NEW.reason IS OLD.reason) "
    "BEGIN SELECT RAISE(ABORT, 'an attention item may only be closed'); END",
    "CREATE TRIGGER IF NOT EXISTS attention_no_delete BEFORE DELETE ON attention "
    "BEGIN SELECT RAISE(ABORT, 'attention items are never deleted'); END",
]
# T51 R7: layout 4 = layout 3 and these, run in that one write transaction whether the file is new or upgraded, so a
# new file and an upgraded one have the same schema. Every layout-3 row reads as a LIMIT order with no collar price.
_UPGRADE_3_TO_4 = [
    "ALTER TABLE orders ADD COLUMN order_type TEXT NOT NULL DEFAULT 'LIMIT' CHECK (order_type IN ('LIMIT', 'MARKET'))",
    "ALTER TABLE orders ADD COLUMN collar_price REAL CHECK ((order_type = 'LIMIT' AND collar_price IS NULL) OR "
    "(order_type = 'MARKET' AND collar_price IS NOT NULL AND collar_price > 0 AND limit_price IS NULL "
    "AND abs(units) < 1))",                                # a CHECK that is NULL passes: hence IS NOT NULL
    "CREATE TRIGGER orders_type_fixed BEFORE UPDATE OF order_type, collar_price ON orders "
    "WHEN NEW.order_type IS NOT OLD.order_type OR NEW.collar_price IS NOT OLD.collar_price "
    "BEGIN SELECT RAISE(ABORT, 'an order''s type and collar price never change'); END",
]
SCHEMA = _LAYOUT3_SCHEMA + _UPGRADE_3_TO_4
SQLITE_INT = (-(2 ** 63), 2 ** 63 - 1)


def _master(db: sqlite3.Connection) -> list:
    return sorted(tuple(r) for r in db.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"))


def _layout3_master() -> list:
    """The schema a layout-3 file has, as sqlite records it (built from the layout-3 statements in memory)."""
    db = sqlite3.connect(":memory:")
    try:
        for statement in _LAYOUT3_SCHEMA:
            db.execute(statement)
        return _master(db)
    finally:
        db.close()


def unreadable_layout(db: sqlite3.Connection) -> str | None:
    """T51: why a database open for reading cannot be read as an order ledger (no orders table, a layout outside
    READABLE_LAYOUTS, or layout 3 with a schema that is not layout 3's), in words; None when it can. Reads only."""
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if not db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'orders'").fetchone()[0]:
        return f"it has no orders table (layout {version}): it is not an order ledger"
    if version not in READABLE_LAYOUTS:
        return f"it has layout {version}, and this code reads layouts {' and '.join(map(str, READABLE_LAYOUTS))} only"
    if version == 3 and _master(db) != _layout3_master():
        return "it says layout 3, but its tables are not layout 3's"
    return None


def _not_layout3(path) -> OrderError:
    return OrderError(f"the ledger file {_one_line(path)} says layout 3, but its tables are not the layout 3 this module "
                      "upgrades; it is not opened, and nothing in it was changed")


class OrderLedger:
    """Every real order, every change of its state and every fill, in one sqlite file (":memory:" for tests).

    Any number of objects and processes may share the file. Each write is an IMMEDIATE transaction (it takes the
    write lock when it begins, so two writers cannot deadlock upgrading a read lock), and a connection waits up
    to BUSY_SECONDS for another's write to finish. A write that fails (even at COMMIT) is rolled back and raised
    as OrderError; the object is outside any transaction afterwards and works again once the cause is gone.

    The file carries a layout version (`PRAGMA user_version` = LAYOUT_VERSION). A new file is created at it. A file
    at layout 3 whose schema is exactly layout 3's is upgraded to layout 4 inside the open's own write transaction
    (T51 R7: `order_type` and `collar_price` added, every existing order a LIMIT order with no collar price), so it is
    either upgraded completely or left as it was, and of several openers exactly one upgrades it. A file written with
    any other layout, or at layout 3 with another schema, is refused, never repaired. `open_read_only` reads a file
    at layout 3 or 4 and never writes it. `not_sent` lists exception classes that
    guarantee nothing was transmitted; any other failure of a submit leaves the order `unknown`. `clock` gives
    the time in seconds.

    Where a ledger is opened: a path that is a symbolic link, and a ledger file group or others may write, are
    refused in every mode, before anything is created or written (a link in a folder above the file is followed, as
    the system's own folders are often links). With `must_exist` (default False: a missing file, and its folder, are
    created) a path with no ledger file is refused the same way. The path is checked again once the write lock is
    held, so a file replaced while it was being opened is refused too."""

    def __init__(self, path: str | Path = ":memory:", not_sent: tuple = (), clock: Callable[[], float] = time.time,
                 *, must_exist: bool = False) -> None:
        if type(must_exist) is not bool:
            raise OrderError(f"must_exist must be True or False, not {_safe_repr(must_exist)}")
        try:
            not_sent = tuple(not_sent)
        except TypeError:
            raise OrderError(f"not_sent must be a tuple of exception classes, not {_safe_repr(not_sent)}") from None
        wrong = [t for t in not_sent if not (isinstance(t, type) and issubclass(t, BaseException))]
        if wrong:
            raise OrderError(f"not_sent must hold only exception classes, not {_safe_repr(wrong)}")
        self.not_sent = not_sent
        self.clock = clock
        self._path = str(path)
        private = self._path in (":memory:", "")              # sqlite's own databases, never a file at a path
        if private and must_exist:
            raise OrderError(f"must_exist asks for a ledger file, and {_one_line(self._path)!r} names none; it is not opened")
        seen = None if private else self._check_file(must_exist)   # raises before anything is created
        try:
            if self._path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._db = self._connect()
        except (OSError, sqlite3.Error) as exc:
            raise OrderError(f"the order ledger {path} could not be opened: {_describe(exc)}") from exc
        try:
            with self._write():
                if not private:                               # the write lock is held: nothing can be written yet
                    now_seen = self._check_file(True)
                    if seen is not None and now_seen != seen:
                        raise OrderError(f"the order ledger {_one_line(self._path)} was replaced while it was being "
                                         "opened; it is not opened")
                version = self._db.execute("PRAGMA user_version").fetchone()[0]
                ours = self._db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'orders'").fetchone()[0]
                anything = self._db.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
                if ours and version == 3:                     # T51 R7: in this transaction, or not at all
                    if _master(self._db) != _layout3_master():
                        raise _not_layout3(path)
                    for statement in _UPGRADE_3_TO_4:
                        self._db.execute(statement)
                    self._db.execute(f"PRAGMA user_version = {LAYOUT_VERSION}")
                elif ours and version != LAYOUT_VERSION:
                    raise OrderError(f"the ledger file {path} was written by a different version of this module "
                                     f"(layout {version}, this module writes layout {LAYOUT_VERSION}); it is not opened")
                if not ours and (anything or version != 0):   # only a new or empty database becomes a ledger
                    raise OrderError(f"the file {path} is a database but not an order ledger (it has other tables or "
                                     f"user_version {version}); it is not opened")
                if not ours:
                    for statement in SCHEMA:
                        self._db.execute(statement)
                    self._db.execute(f"PRAGMA user_version = {LAYOUT_VERSION}")
        except BaseException:
            self._close_quietly()
            raise

    def _check_file(self, must_exist: bool) -> tuple | None:
        """Refuse the ledger's path, in one line naming it, when it is a symbolic link or a regular file group or
        others may write, and with `must_exist` when no regular file is there. Returns the file's (device, inode),
        or None when nothing is there yet. Reads only: it never creates, opens or changes anything."""
        where = _one_line(self._path)
        try:
            st = os.lstat(self._path)
        except (FileNotFoundError, NotADirectoryError):
            st = None
        except OSError as exc:
            raise OrderError(f"the order ledger {where} could not be checked: {_describe(exc)}") from exc
        if st is not None and stat.S_ISLNK(st.st_mode):
            raise OrderError(f"the order ledger {where} is a symbolic link; it is not opened")
        if st is None or not stat.S_ISREG(st.st_mode):
            if must_exist:
                raise OrderError(f"no order ledger file is at {where}, and one was expected there; nothing was created")
            return None
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise OrderError(f"the order ledger {where} is writable by group or others (mode "
                             f"{stat.S_IMODE(st.st_mode):o}); it is not opened")
        return (st.st_dev, st.st_ino)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, timeout=BUSY_SECONDS, isolation_level=None)   # transactions are explicit
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous = FULL")
        except BaseException:
            db.close()
            raise
        return db

    def _close_quietly(self) -> None:
        try:
            self._db.close()
        except Exception:
            pass

    def _rollback(self, cause: str = "") -> None:
        """Leave the transaction, whatever state sqlite is in; a failing ROLLBACK is swallowed. If the connection is
        still inside a transaction after that, a ledger on a file drops it and opens a new one (so its locks are
        released); a ":memory:" ledger cannot be reopened and raises OrderError saying it must be discarded."""
        try:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        try:
            stuck = self._db.in_transaction
        except sqlite3.Error:
            stuck = True
        if not stuck:
            return
        self._close_quietly()
        if self._path == ":memory:":
            raise OrderError(f"the in-memory order ledger could not roll back ({cause or 'after a failed write'}) and "
                             "must be discarded")
        try:
            self._db = self._connect()
        except sqlite3.Error as exc:
            raise OrderError(f"the order ledger could not roll back ({cause}) and could not be reopened: "
                             f"{_describe(exc)}") from exc

    def _read(self, sql: str, args: tuple = ()) -> list:
        """Run one query and fetch every row; a database error is raised as OrderError naming it."""
        try:
            return self._db.execute(sql, args).fetchall()
        except sqlite3.Error as exc:
            raise OrderError(f"the order ledger could not be read: {_describe(exc)}") from exc

    @contextmanager
    def _write(self):
        """One write transaction, holding the write lock from its first statement. Always leaves the connection
        outside a transaction: on any failure, COMMIT's included, it rolls back, and a database error is raised
        as OrderError naming it."""
        try:
            self._db.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            self._rollback(_describe(exc))
            raise OrderError(f"the order ledger could not be written: {_describe(exc)}") from exc
        try:
            yield
        except sqlite3.Error as exc:
            self._rollback(_describe(exc))
            raise OrderError(f"the order ledger could not be written: {_describe(exc)}") from exc
        except BaseException as exc:
            self._rollback(_describe(exc))
            raise
        try:
            self._db.execute("COMMIT")
        except sqlite3.Error as exc:
            self._rollback(_describe(exc))
            raise OrderError(f"the order ledger could not be written: {_describe(exc)}") from exc

    # ---- reading

    @staticmethod
    def _record(row: sqlite3.Row) -> dict:
        rec = dict(row)
        rec["parts"] = json.loads(rec["parts"]) if rec["parts"] is not None else None
        rec.setdefault("order_type", "LIMIT")             # a layout-3 file read by `open_read_only`
        rec.setdefault("collar_price", None)
        return rec

    def order(self, tag: str) -> dict | None:
        if isinstance(tag, str) and not _utf8(tag):
            return None                                   # no tag on file can be one that UTF-8 cannot encode
        rows = self._read("SELECT * FROM orders WHERE tag = ?", (tag,))
        return self._record(rows[0]) if rows else None

    def orders(self, states: Iterable[str] | None = None) -> list[dict]:
        """Orders in the order they were intended; only those in `states` when given."""
        if states is None:
            rows = self._read("SELECT * FROM orders ORDER BY rowid")
        else:
            states = tuple(states)
            marks = ",".join("?" * len(states))
            rows = self._read(f"SELECT * FROM orders WHERE state IN ({marks}) ORDER BY rowid", states)
        return [self._record(r) for r in rows]

    def events(self, tag: str | None = None) -> list[dict]:
        sql, args = "SELECT tag, time, from_state, to_state, note FROM events", ()
        if isinstance(tag, str) and not _utf8(tag):
            return []
        if tag is not None:
            sql, args = sql + " WHERE tag = ?", (tag,)
        return [{"tag": r["tag"], "time": r["time"], "from": r["from_state"], "to": r["to_state"], "note": r["note"]}
                for r in self._read(sql + " ORDER BY id", args)]

    def fills(self, tag: str | None = None) -> list[dict]:
        """Every fill row, oldest first; only the order's when `tag` is given."""
        sql, args = "SELECT * FROM fills", ()
        if isinstance(tag, str) and not _utf8(tag):
            return []
        if tag is not None:
            sql, args = sql + " WHERE tag = ?", (tag,)
        return [dict(r) for r in self._read(sql + " ORDER BY id", args)]

    def unapplied_fills(self) -> list[dict]:
        """The fill rows the book has not applied yet, oldest first."""
        return [dict(r) for r in self._read("SELECT * FROM fills WHERE applied = 0 ORDER BY id")]

    def mark_applied(self, ids: Iterable[int]) -> int:
        """Mark fill rows applied to the book. Returns how many changed. Only plain ints (not bools) in sqlite's
        integer range are ids; anything else, and an applied or unknown id, changes nothing and does not raise."""
        try:
            ids = list(ids)
        except TypeError:
            raise OrderError(f"mark_applied takes an iterable of fill ids, not {_safe_repr(ids)}") from None
        wanted = [i for i in ids if type(i) is int and SQLITE_INT[0] <= i <= SQLITE_INT[1]]
        if not wanted:
            return 0
        now = self.clock()
        changed = 0
        with self._write():
            for i in wanted:
                changed += self._db.execute("UPDATE fills SET applied = 1, applied_time = ? WHERE id = ? AND applied = 0",
                                            (now, i)).rowcount
        return changed

    def last_reconcile(self) -> dict | None:
        rows = self._read("SELECT time, clean, summary FROM reconciles ORDER BY id DESC LIMIT 1")
        if not rows:
            return None
        return {"time": rows[0]["time"], "clean": bool(rows[0]["clean"]), "summary": json.loads(rows[0]["summary"])}

    def attention(self, open_only: bool = True) -> list[dict]:
        """Items a person must look at (an order found at the venue after it was declared never arrived, a submit
        outcome that came back after that, a fee total below zero), oldest first; only the open ones unless
        `open_only` is False. Every open item blocks further orders, except a notice (its kind is in NOTICE_KINDS),
        which only tells a person."""
        sql = "SELECT * FROM attention" + (" WHERE closed_time IS NULL" if open_only else "") + " ORDER BY id"
        return [dict(r) for r in self._read(sql)]

    def reconcile_notices(self, after_id: int = 0) -> list[dict]:
        """T34b-3: every notice of RECONCILE_NOTICE_KINDS with an id above `after_id`, open, closed or superseded,
        oldest first, each with `words`, `old`, `new`, `after_fill` and `row` read from the JSON its reason holds
        (None where it cannot be read). A notice never blocks; this read is for whoever journals and judges each one
        once."""
        marks = ",".join("?" * len(RECONCILE_NOTICE_KINDS))
        out = []
        for r in self._read(f"SELECT * FROM attention WHERE kind IN ({marks}) AND id > ? ORDER BY id",
                            (*sorted(RECONCILE_NOTICE_KINDS), int(after_id))):
            item = dict(r)
            try:
                said = json.loads(item["reason"])
            except (TypeError, ValueError):
                said = None
            said = said if isinstance(said, dict) else {}
            item.update(words=said.get("words"), old=said.get("old"), new=said.get("new"),
                        after_fill=said.get("after_fill"), row=said.get("row"))
            out.append(item)
        return out

    def average_writes(self, tag: str) -> dict | None:
        """T34b-3 (addendum 4): the average the order held after each write that touched it, REPLAYED from its
        immutable records, read in one snapshot: the fill rows in id order and the average-changing notices
        (`avg_restated`, `piece_unpriced`) placed after the row their `after_fill` names, then by id. The ledger's
        own arithmetic: a priced row moves the priced point (a, u) to ((a x u + price x covers) / (u + covers),
        u + covers), or to (price, covers) when there is none; an `avg_restated` notice sets a to its `new`; a
        `piece_unpriced` notice sets a to its `new` and u to the units filled up to its row; a row without a price
        moves nothing. None when the ledger has no such order. Else {"fills": {id: {"avg", "sets", "units", "waits",
        "covered_by"}}, "notices": {id: {"kind", "avg"}}, "avg", "units", "trusted", "why"}: `avg` is a after the
        write (None before any priced point); `sets` is true for a priced row and for the row of its own
        `piece_unpriced` notice (the figure is the notice's); `waits` is true for a row with units (beyond the
        ledger's tolerance) that sets nothing, and `covered_by` names, for it, the first later write whose priced
        point takes in its units (("fill", id), or ("notice", id) for a
        `piece_unpriced` notice that came without a row of its own). `trusted` is false, with `why`, when a record
        cannot be read as the replay needs it or the replayed priced point is not the stored one (avg_price and
        priced_units within REL_TOL x max(1, |x|))."""
        kinds = ("avg_restated", "piece_unpriced")
        try:
            snapshot = not self._db.in_transaction
            if snapshot:
                self._db.execute("BEGIN")
            try:
                rec = self._db.execute("SELECT * FROM orders WHERE tag = ?", (tag,)).fetchone()
                rows = self._db.execute("SELECT id, units, price, price_covers_units FROM fills WHERE tag = ? "
                                        "ORDER BY id", (tag,)).fetchall()
                notes = self._db.execute("SELECT id, kind, reason FROM attention WHERE tag = ? AND kind IN (?, ?) "
                                         "ORDER BY id", (tag, *kinds)).fetchall()
            finally:
                if snapshot:
                    self._db.execute("COMMIT")
        except sqlite3.Error as exc:
            raise OrderError(f"the order ledger could not be read: {_describe(exc)}") from exc
        if rec is None:
            return None
        out = {"fills": {}, "notices": {}, "avg": None, "units": 0.0, "trusted": True, "why": None}

        def untrusted(why: str) -> dict:
            out.update(trusted=False, why=why)
            return out
        ids = [int(r["id"]) for r in rows]
        placed: dict[int, list] = {}
        for n in notes:
            try:
                said = json.loads(n["reason"])
            except (TypeError, ValueError):
                said = None
            said = said if isinstance(said, dict) else {}
            new, after, own = said.get("new"), said.get("after_fill"), said.get("row")
            if not (_finite(new) and new > 0) or type(after) is not int or after not in ids \
                    or not (own is None or own == after):
                return untrusted(f"notice {n['id']} ({n['kind']}) cannot be placed among the rows")
            placed.setdefault(after, []).append((int(n["id"]), n["kind"], float(new), own))
        a, u, total, waiting = None, 0.0, 0.0, []        # waiting: rows with units that set nothing, not yet covered

        def cover(ref) -> None:
            for fid, through in list(waiting):
                if u >= through - _tol(through):
                    out["fills"][fid]["covered_by"] = ref
                    waiting.remove((fid, through))
        for r in rows:
            fid, units, price, covers = int(r["id"]), r["units"], r["price"], r["price_covers_units"]
            if not _finite(units):
                return untrusted(f"fill row {fid} has no usable units")
            total += abs(units)
            sets = price is not None
            if sets:
                if not (_finite(price) and price >= 0 and _finite(covers) and covers > 0):
                    return untrusted(f"fill row {fid} has a price without a usable amount it covers")
                a = price if not (u > 0 and a is not None) else (a * u + price * covers) / (u + covers)
                u += covers
            entry = {"avg": a, "sets": sets, "units": abs(units), "waits": not sets and abs(units) > _tol(total),
                     "covered_by": None}
            out["fills"][fid] = entry
            if entry["waits"]:
                waiting.append((fid, total))
            if sets:
                cover(("fill", fid))
            for nid, kind, new, own in placed.get(fid, []):
                a = new
                if kind == "piece_unpriced":
                    u = total
                    if own == fid:                        # its own row: the row's figure is the notice's
                        entry.update(avg=a, sets=True, waits=False)
                        waiting[:] = [w for w in waiting if w[0] != fid]
                        cover(("fill", fid))
                    else:
                        cover(("notice", nid))
                out["notices"][nid] = {"kind": kind, "avg": a}
        out.update(avg=a, units=u)
        stored_avg, stored_units = rec["avg_price"], rec["priced_units"] or 0.0
        if (a is None) != (stored_avg is None) or (a is not None and abs(a - stored_avg) > _tol(stored_avg)):
            return untrusted(f"the replayed average {a!r} is not the stored {stored_avg!r}")
        if abs(u - stored_units) > _tol(stored_units):
            return untrusted(f"the replayed priced units {u!r} are not the stored {stored_units!r}")
        return out

    def close_attention(self, id: int, who: str, note: str) -> dict:
        """Close an open attention item, saying who looked and what they found."""
        if not isinstance(who, str) or not who.strip() or not isinstance(note, str) or not note.strip() \
                or not _utf8(who) or not _utf8(note):
            raise OrderError("closing an attention item needs who did it and a note saying what they found")
        if type(id) is not int or not SQLITE_INT[0] <= id <= SQLITE_INT[1]:
            raise OrderError(f"no attention item has id {_safe_repr(id)}")
        with self._write():
            rows = self._read("SELECT * FROM attention WHERE id = ?", (id,))
            if not rows:
                raise OrderError(f"no attention item has id {id}")
            if rows[0]["closed_time"] is not None:
                raise OrderError(f"attention item {id} is already closed")
            self._db.execute("UPDATE attention SET closed_by = ?, closed_note = ?, closed_time = ? WHERE id = ?",
                             (who.strip(), note.strip(), self.clock(), id))
        return dict(self._read("SELECT * FROM attention WHERE id = ?", (id,))[0])

    def _flag(self, tag: str, kind: str, reason: str) -> None:
        """Open an attention item (inside the caller's write transaction); one open item per tag and kind."""
        self._db.execute("INSERT OR IGNORE INTO attention (tag, kind, time, reason) VALUES (?,?,?,?)",
                         (tag, kind, self.clock(), reason))

    def close(self) -> None:
        try:
            self._db.close()
        except sqlite3.Error as exc:
            raise OrderError(f"the order ledger could not be closed: {_describe(exc)}") from exc

    # ---- writing: every change is made inside `self._write()`

    def _cas(self, tag: str, old: str, new: str, why: str | None = None, **fields) -> bool:
        """Move the order from `old` to `new` (and set `fields`) only if it is still in `old` and the move is forward.

        Returns whether it applied. A change of state is appended to the events with `why`; a lost change writes
        nothing. Reaching filled, cancelled or expired records the time, for the late-report recheck."""
        if new != old and new not in FORWARD.get(old, ()):
            return False
        now = self.clock()
        if new != old and new in FINISHED:
            fields = {**fields, "finished_at": now}
        sets = ", ".join(f"{k} = ?" for k in ("state", "updated", *fields))
        cur = self._db.execute(f"UPDATE orders SET {sets} WHERE tag = ? AND state = ?", (new, now, *fields.values(), tag, old))
        if cur.rowcount != 1:
            return False
        if old != new:
            self._db.execute("INSERT INTO events (tag, time, from_state, to_state, note) VALUES (?,?,?,?,?)",
                             (tag, now, old, new, why))
        return True

    def intend(self, tag: str, symbol: str, units: float, reference_price: float, limit_price: float | None = None,
               batch: str | None = None, parts: list | None = None, *, order_type: str = "LIMIT",
               collar_price: float | None = None) -> dict:
        """Record an order in `intended`. The same tag again returns the record when every field matches, else raises.

        T51 R7: `order_type` is LIMIT (the default; no collar price) or MARKET (under one unit in size, no limit price,
        and a `collar_price`, a finite number above zero: the worst price it may fill at). Anything else is refused
        and nothing is written."""
        if not isinstance(tag, str) or not tag or not _utf8(tag):
            raise OrderError(f"an order needs a tag that is text encodable as UTF-8, not {_safe_repr(tag)}")
        if not isinstance(symbol, str) or not symbol or not _utf8(symbol):
            raise OrderError(f"an order needs a symbol that is text encodable as UTF-8, not {_safe_repr(symbol)}")
        if batch is not None and (not isinstance(batch, str) or not _utf8(batch)):
            raise OrderError(f"the batch must be None or text encodable as UTF-8, not {_safe_repr(batch)}")
        if not _finite(units) or abs(units) < MIN_UNITS:
            raise OrderError(f"units must be a finite number at least {MIN_UNITS} in size, not {_safe_repr(units)}")
        if not _finite(reference_price) or reference_price <= 0:
            raise OrderError(f"the reference price must be a finite number above zero, not {_safe_repr(reference_price)}")
        if limit_price is not None and (not _finite(limit_price) or limit_price <= 0):
            raise OrderError(f"a limit price must be a finite number above zero, not {_safe_repr(limit_price)}")
        if type(order_type) is not str or order_type not in ORDER_TYPES:
            raise OrderError(f"an order's order_type must be one of {', '.join(ORDER_TYPES)}, not {_safe_repr(order_type)}")
        if collar_price is not None and (not _finite(collar_price) or collar_price <= 0):
            raise OrderError(f"a collar price must be a finite number above zero, not {_safe_repr(collar_price)}")
        if order_type == "LIMIT" and collar_price is not None:
            raise OrderError("a LIMIT order carries no collar price: its limit is its bound")
        if order_type == "MARKET":
            if collar_price is None:
                raise OrderError("a MARKET order needs a collar price: the worst price it may fill at")
            if limit_price is not None:
                raise OrderError("a MARKET order carries no limit price (a venue would send it as a LIMIT order)")
            if abs(units) >= 1:
                raise OrderError(f"a MARKET order must be under one unit in size, not {_safe_repr(units)}")
        try:
            parts_json = json.dumps(parts) if parts is not None else None
        except (TypeError, ValueError) as exc:
            raise OrderError(f"the parts cannot be stored: {_describe(exc)}") from exc
        units, reference_price = float(units), float(reference_price)
        limit_price = float(limit_price) if limit_price is not None else None
        collar_price = float(collar_price) if collar_price is not None else None
        with self._write():
            existing = self.order(tag)
            if existing is None:
                now = self.clock()
                self._db.execute("INSERT INTO orders (tag, symbol, units, reference_price, limit_price, batch, parts, state, "
                                 "created, updated, order_type, collar_price) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (tag, symbol, units, reference_price, limit_price, batch, parts_json, "intended", now, now,
                                  order_type, collar_price))
                self._db.execute("INSERT INTO events (tag, time, from_state, to_state, note) VALUES (?,?,?,?,?)",
                                 (tag, now, None, "intended", None))
        if existing is not None:
            mine = {"symbol": symbol, "units": units, "reference_price": reference_price, "limit_price": limit_price,
                    "batch": batch, "order_type": order_type, "collar_price": collar_price}
            differ = [k for k, v in mine.items() if existing[k] != v]
            if not _same_parts(existing["parts"], parts):
                differ.append("parts")
            if differ:
                raise OrderError(f"order {tag} is already on file with different {', '.join(differ)}")
            return existing
        return self.order(tag)

    def settle_by_hand(self, tag: str, who: str, note: str) -> dict:
        """A person's ruling that an order in `sent` or `unknown` never reached the venue (checked in its own app).

        The only way a person changes an order's state. The order becomes `never_arrived`, so the recheck in
        `reconcile` still catches it if it turns up at the venue after all."""
        if not isinstance(who, str) or not who.strip() or not isinstance(note, str) or not note.strip() \
                or not _utf8(who) or not _utf8(note):
            raise OrderError("settling an order by hand needs who did it and a note saying what they saw")
        with self._write():
            rec = self.order(tag)
            if rec is None:
                raise OrderError(f"no order with tag {tag} is on file")
            if rec["state"] not in UNSETTLED:
                raise OrderError(f"order {tag} is {rec['state']}; only an order in sent or unknown can be settled by hand")
            self._cas(tag, rec["state"], "never_arrived", f"settled by hand by {who.strip()}: {note.strip()}",
                      never_arrived_at=self.clock())
        return self.order(tag)

    # ---- the gate

    def blocked(self, max_age_seconds: float = 21600) -> list[str]:
        """Why no order may be submitted now; empty when nothing stands in the way."""
        reasons = [f"order {o['tag']} is {o['state']}: its fate is not known until a reconcile settles it"
                   for o in self.orders(UNSETTLED)]
        waiting = self._read("SELECT COUNT(*) FROM fills WHERE applied = 0")[0][0]
        if waiting:
            reasons.append(f"{waiting} fill{'' if waiting == 1 else 's'} not yet applied to the book")
        for item in self.attention():
            if item["kind"] in NOTICE_KINDS:                  # tells a person; does not stand in the way
                continue
            reasons.append(f"attention item {item['id']} for order {item['tag']} is open: {item['reason']} "
                           "(close it with close_attention once a person has looked)")
        last = self.last_reconcile()
        if last is None:
            reasons.append("no reconcile recorded yet")
        else:
            if not last["clean"]:
                reasons.append("the latest reconcile was not clean")
            age = self.clock() - last["time"]
            if age < 0:
                reasons.append(f"the clock is behind the latest reconcile by {-age:.0f} s")
            elif age > max_age_seconds:
                reasons.append(f"the latest reconcile is {age:.0f} s old, more than {max_age_seconds:.0f} s")
        return reasons

    def submit_once(self, venue: LiveVenue, tag: str, max_age_seconds: float = 21600) -> dict:
        """Send the order if it is still `intended`, at most once across every ledger on the file. Never retries;
        a venue failure is in the returned record. If the venue's answer cannot be written down (the ledger file
        is locked or full), the order is left `sent` on disk, which blocks until a reconcile settles it, and
        OrderError is raised.

        An acceptance always wins: if another ledger moved the order to unknown or never_arrived while the venue
        call was in flight, the acceptance moves it on to accepted, with a note that it arrived late. Any outcome
        that finds the order declared never arrived meanwhile (an acceptance, an error, a refusal, an answer that is
        not an acceptance) opens an attention item, which blocks every further order until a person closes it.

        The check of `blocked(max_age_seconds)` and the change `intended -> sent` are one write transaction: the
        caller that makes the change is the only one that calls the venue. Another caller gets OrdersBlocked while
        that order is in flight, or the record once it is settled; neither calls the venue."""
        try:
            name = _storable(getattr(venue, "name", "") or "")
        except Exception:
            name = "?"
        with self._write():
            reasons = self.blocked(max_age_seconds)
            if reasons:
                raise OrdersBlocked(reasons)
            rec = self.order(tag)
            if rec is None:
                raise OrderError(f"no order with tag {tag} is on file")
            won = rec["state"] == "intended" and self._cas(tag, "intended", "sent", f"to {name or '?'}",
                                                            sent_at=self.clock(), venue=name)
        if not won:                                       # committed: nothing was written
            return self.order(tag)
        try:
            result = venue.submit(rec["symbol"], rec["units"], tag, price=rec["reference_price"], limit_price=rec["limit_price"])
        except Exception as exc:                          # a KeyboardInterrupt or a death leaves `sent`
            note = _describe(exc)
            new = "refused" if self.not_sent and isinstance(exc, self.not_sent) else "unknown"
            with self._write():
                if not self._cas(tag, "sent", new, note, note=note):
                    self._late_outcome(tag, "a refusal" if new == "refused" else "an error")
            return self.order(tag)
        accepted = False
        try:
            accepted = isinstance(result, dict) and result.get("status") == "accepted"
            vid = result.get("venue_order_id") if accepted else None
            vid = _storable(vid) if vid is not None else None
        except Exception:
            vid = None
        with self._write():
            if accepted and not self._cas(tag, "sent", "accepted", None, venue_order_id=vid):
                now_state = self.order(tag)["state"]
                if now_state in ("unknown", "never_arrived"):
                    late = self._cas(tag, now_state, "accepted", f"the venue's acceptance arrived late, after the order "
                                     f"was marked {now_state}", venue_order_id=vid)
                    if late and now_state == "never_arrived":
                        self._flag(tag, "late_acceptance", "the venue's acceptance arrived late, after the order had "
                                   "been declared never arrived; check that no replacement was sent")
            elif not accepted:
                note = f"the venue did not say accepted: {_safe_repr(result)}"
                if not self._cas(tag, "sent", "unknown", note, note=note):
                    self._late_outcome(tag, "an answer that was not an acceptance")
        return self.order(tag)

    def _late_outcome(self, tag: str, what: str) -> None:
        """Inside the caller's write: a submit's outcome found its order no longer sent. If it was declared never
        arrived meanwhile, open an attention item (it blocks, as a late acceptance does). The reason is in this
        module's own words only, never the venue's text, as blocked() shows it."""
        rec = self.order(tag)
        if rec is not None and rec["state"] == "never_arrived":
            self._flag(tag, "late_outcome", f"the submit came back with {what} after the order had been declared "
                       "never arrived; check at the venue that it is not there and that no replacement was sent")

    # ---- reconcile

    def reconcile(self, venue: LiveVenue, book_positions: dict[str, float], tolerance: float = 1e-6,
                  settle_seconds: float = 120, recheck_seconds: float = 259200, *, one_sided: bool = False) -> dict:
        """Ask the venue about every order still open on the ledger, write each new fill once, compare positions.

        T51c S1 (R2): with `one_sided` (an account the owner also trades) a symbol is compared one way only: the
        account holding the book plus its unapplied fills, or more, within `tolerance`, is clean, and what it holds
        beyond that is listed under `owner_surplus` (the owner's, never a difference); the account holding less is
        listed under `position_shortfalls`, in words naming the symbol, both figures and the two ordinary causes, and
        makes the reconcile unclean (the ledger is then blocked as for any unclean reconcile). A figure that is not
        finite stays a position difference. Without it (the default) nothing differs from before: no new key in the
        record or the stored summary. `one_sided` must be True or False, or OrderError is raised and nothing is
        recorded.

        Also asked, for `recheck_seconds` after they reached that state: every order marked never_arrived (one the
        venue now knows has turned up: an anomaly, moved to the venue's state, with its fills), and every order
        that reached filled, cancelled or expired with something filled or without an amount (late fees, averages
        and a larger amount are taken: the larger amount is the notice `late_amount` while the venue still names the
        order's own finished state, else the anomaly `late_amount_other_state`; a smaller amount or a different state
        is an anomaly that changes nothing). An exception from the venue on one of these goes
        to `recheck_errors` and does not make the reconcile unclean, so for an order wrongly declared never
        arrived the position comparison is then the remaining safeguard.

        `settle_seconds` must be longer than the longest time a submit call can take: an order still absent at the
        venue that long after it was sent is declared never arrived. Never raises because of one order, one venue
        call or anything the venue returns; the reconcile is always recorded, and lists a fill or an update only
        when the write behind it was committed (an order whose write failed is unresolved). An order the venue
        reported cancelled or rejected with nothing filled is not asked again; if the venue later contradicts
        that, only the position comparison shows it."""
        if type(one_sided) is not bool:
            raise OrderError(f"one_sided must be True or False, not {_safe_repr(one_sided)}; nothing was recorded")
        now = self.clock()
        out = {"clean": False, "orders_checked": 0, "updated": [], "fills": [], "unresolved": [], "anomalies": [],
               "position_differences": {}, "positions_error": None, "positions_error_kind": None, "recheck_errors": [],
               "notices": [], "time": now}
        if one_sided:                                     # T51c S1: these keys only in a one-sided record
            out["owner_surplus"], out["position_shortfalls"] = {}, {}
        try:
            since = now - recheck_seconds
            todo = [(r["tag"], False) for r in self.orders(CHECKED)]
            todo += [(r["tag"], True) for r in self._read(
                "SELECT tag FROM orders WHERE (state = 'never_arrived' AND never_arrived_at >= ?) OR "
                "(state IN ('filled', 'cancelled', 'expired') AND (filled_units > 0 OR amount_unconfirmed = 1) "
                "AND finished_at >= ?) ORDER BY rowid", (since, since))]
            for tag, recheck in todo:
                out["orders_checked"] += 1
                self._reconcile_one(venue, tag, recheck, now, settle_seconds, out)
            self._compare_positions(venue, book_positions, tolerance, out, one_sided)
            still = self.orders(UNSETTLED)
            out["clean"] = not (out["unresolved"] or still or out["anomalies"] or out["position_differences"]
                                or out["positions_error"] or out.get("position_shortfalls"))
        except Exception as exc:                          # the ledger's own failure: recorded, not clean
            out["clean"] = False
            out["anomalies"].append({"tag": None, "kind": "reconcile_failed",
                                     "reason": f"the reconcile stopped: {_describe(exc)}"})
        try:
            summary = json.dumps(out, default=str)
        except Exception as exc:                          # never lose the record over what the venue sent
            out["clean"] = False
            out["anomalies"].append({"tag": None, "kind": "not_storable",
                                     "reason": f"the result could not be stored in full: {_describe(exc)}"})
            summary = json.dumps({"time": now, "clean": False, "error": _describe(exc)})
        with self._write():
            self._db.execute("INSERT INTO reconciles (time, clean, summary) VALUES (?,?,?)", (now, int(out["clean"]), summary))
        return out

    def _compare_positions(self, venue: LiveVenue, book_positions, tolerance: float, out: dict,
                           one_sided: bool = False) -> None:
        try:
            held = dict(venue.positions())
        except Exception as exc:
            out["positions_error"] = f"the venue's positions could not be read: {_describe(exc)}"
            out["positions_error_kind"] = "positions_unread"
            return
        try:
            book = dict(book_positions or {})
            odd = [k for k in (*held, *book) if not isinstance(k, str)]
        except Exception as exc:                          # what was given as the book, or the venue's keys
            out["positions_error"] = f"the positions could not be compared: {_describe(exc)}"
            out["positions_error_kind"] = "positions_malformed"
            return
        if odd:
            out["positions_error"] = f"a position key is not a symbol string: {_safe_repr(odd)}"
            out["positions_error_kind"] = "positions_malformed"
            return
        unapplied: dict[str, float] = {}
        for f in self.unapplied_fills():
            unapplied[f["symbol"]] = unapplied.get(f["symbol"], 0.0) + f["units"]
        for sym in sorted(set(held) | set(book) | set(unapplied)):
            b, v, u = book.get(sym, 0.0), held.get(sym, 0.0), unapplied.get(sym, 0.0)
            if one_sided and _finite(b) and _finite(v):   # T51c S1: the account may hold more; never less
                gap = float(v) - (float(b) + u)
                if gap > tolerance:
                    out["owner_surplus"][sym] = {"book": b, "venue": v, "unapplied": u, "surplus": gap}
                elif -gap > tolerance:
                    out["position_shortfalls"][sym] = {
                        "book": b, "venue": v, "unapplied": u, "short": -gap,
                        "reason": f"the account holds {float(v)!r} of {_one_line(sym)}, under the {float(b) + u!r} the "
                                  f"book counts as its own (book {float(b)!r}, unapplied {float(u)!r}); the ordinary causes are "
                                  "that the owner sold shares the book counts as its own, or a split; the whole book "
                                  "stops until a person has looked"}
                continue
            if not (_finite(b) and _finite(v)) or abs(float(b) + u - float(v)) > tolerance:
                out["position_differences"][sym] = {"book": b, "venue": v, "unapplied": u}

    def _reconcile_one(self, venue: LiveVenue, tag: str, recheck: bool, now: float, settle_seconds: float, out: dict) -> None:
        try:
            status = venue.order_status(tag)
        except Exception as exc:
            item = {"tag": tag, "kind": "status_unread", "reason": f"status could not be read: {_describe(exc)}"}
            if recheck:                                   # a venue that raises for "no such order" must not block
                out["recheck_errors"].append(item)
            else:
                out["unresolved"].append(item)
            return
        try:
            self._apply(tag, status, recheck, now, settle_seconds, out)
        except Exception as exc:                          # nothing a venue returns may stop the loop
            out["unresolved"].append({"tag": tag, "kind": "answer_unusable",
                                      "reason": f"the venue's answer could not be used: {_describe(exc)}"})

    def _apply(self, tag: str, status, recheck: bool, now: float, settle_seconds: float, out: dict) -> None:
        """Use one venue answer. What to do is decided from the order as re-read inside the write transaction, so an
        order another ledger moved meanwhile (to never_arrived, say) is handled as it now is."""
        if status is not None and not isinstance(status, dict):
            (out["anomalies"] if recheck else out["unresolved"]).append(
                {"tag": tag, "kind": "status_not_dict", "reason": f"the venue's status is not a dict: {_safe_repr(status)}"})
            return
        if status is not None:
            try:                                          # a plain copy: an odd mapping cannot raise later
                status = {k: status.get(k) for k in STATUS_KEYS}
            except Exception as exc:
                out["unresolved"].append({"tag": tag, "kind": "status_unreadable",
                                          "reason": f"the venue's status could not be read: {_describe(exc)}"})
                return
            if status["filled_units"] is not None and not _finite(status["filled_units"]):
                out["anomalies"].append({"tag": tag, "kind": "units_not_finite",
                                         "reason": "the venue's filled_units is not a finite number: "
                                                   f"{_safe_repr(status['filled_units'])}"})
                return
            status["bad"] = [k for k in ("avg_price", "fee") if status[k] is not None and not _finite(status[k])]
            for k in status["bad"]:                       # that piece only: ignored, the units are still taken
                status[k] = None
            for k in ("venue_order_id", "raw_status"):
                if status[k] is not None:
                    status[k] = _storable(status[k])
        local: dict[str, list] = {k: [] for k in ("fills", "updated", "unresolved", "anomalies", "recheck_errors",
                                                  "notices")}
        try:
            with self._write():
                rec = self.order(tag)
                if rec is None:
                    return
                state = rec["state"]
                if state in CHECKED:
                    if status is None:
                        self._apply_missing(rec, now, settle_seconds, local)
                    else:
                        self._apply_status(rec, status, now, local, "open")
                elif state == "never_arrived":
                    if status is not None:                # proof the order exists wins over never_arrived
                        reason = (f"declared never arrived, but the venue has an order with this tag "
                                  f"({_safe_repr(status.get('state'))})")
                        local["anomalies"].append({"tag": tag, "kind": "turned_up", "reason": reason})
                        self._flag(tag, "turned_up", reason + "; check that no replacement was sent")
                        self._apply_status(rec, status, now, local, "turned_up")
                elif state in FINISHED:
                    if status is None:
                        local["recheck_errors"].append({"tag": tag, "kind": "gone",
                                                        "reason": f"the venue no longer has this {state} order"})
                    else:
                        self._apply_status(rec, status, now, local, "finished")
        except _NoticeNotRecorded as exc:                 # rolled back: the figure is not followed without its record
            out["anomalies"].append({"tag": tag, "kind": "notice_not_recorded",
                                     "reason": f"a notice could not be recorded, so nothing of the venue's answer is "
                                               f"followed: {exc}"})
            return
        except Exception as exc:                          # rolled back: nothing of it is reported, it stays unresolved
            out["unresolved"].append({"tag": tag, "kind": "not_written",
                                      "reason": f"the venue's answer could not be written or used: {_describe(exc)}"})
            return
        for k, items in local.items():                    # committed: now it may be reported
            out[k].extend(items)

    def _notice_negative_fee(self, tag: str, fee: float) -> None:
        """Inside the caller's write: the fee total recorded for the order is below zero. It is kept as reported (it
        may be a rebate) and a notice is opened for a person: once per order, open or closed, and it does not block."""
        if self._db.execute("SELECT COUNT(*) FROM attention WHERE tag = ? AND kind = 'negative_fee'", (tag,)).fetchone()[0]:
            return
        self._flag(tag, "negative_fee", f"the venue reports a fee total of {fee!r} for order {tag}, below zero; it is "
                   "kept as reported: check whether it is a rebate")

    def _record_notice(self, tag: str, kind: str, words: str, old, new, after_fill, row) -> int:
        """Inside the caller's write (T34b-3): record one notice of RECONCILE_NOTICE_KINDS as an attention item whose
        reason is a JSON object {"words", "old", "new", "after_fill", "row"}: `after_fill` is the largest fill-row id
        of the order once this write's own row is in, `row` that row's id (None when the write adds none), so a
        reader can place the notice among the rows (`average_writes`). An earlier OPEN notice of the same order and
        kind is first closed as superseded (one open item per tag and kind is the table's rule), so every restatement
        keeps its own id. Returns the new item's id; raises _NoticeNotRecorded when the insert did not take, which
        rolls the whole write back."""
        self._db.execute("UPDATE attention SET closed_by = ?, closed_note = ?, closed_time = ? "
                         "WHERE tag = ? AND kind = ? AND closed_time IS NULL",
                         (LEDGER_CLOSER, "superseded by a later notice of the same kind", self.clock(), tag, kind))
        reason = json.dumps({"words": words, "old": old, "new": new, "after_fill": after_fill, "row": row})
        cur = self._db.execute("INSERT INTO attention (tag, kind, time, reason) VALUES (?,?,?,?)",
                               (tag, kind, self.clock(), reason))
        nid = cur.lastrowid if cur.rowcount == 1 else None
        got = None if nid is None else self._db.execute(
            "SELECT id FROM attention WHERE id = ? AND tag = ? AND kind = ? AND reason = ? AND closed_time IS NULL",
            (nid, tag, kind, reason)).fetchone()
        if got is None:
            raise _NoticeNotRecorded(f"the {kind} notice for order {tag} did not take")
        return int(got[0])

    def _apply_missing(self, rec: dict, now: float, settle_seconds: float, out: dict) -> None:
        tag, state = rec["tag"], rec["state"]
        if state in UNSETTLED:
            sent_at = rec["sent_at"] if rec["sent_at"] is not None else rec["created"]
            if now - sent_at > settle_seconds:
                if self._cas(tag, state, "never_arrived",
                             f"the venue had no order with this tag {now - sent_at:.0f} s after it was sent",
                             never_arrived_at=now):
                    out["updated"].append({"tag": tag, "from": state, "to": "never_arrived"})
            else:
                out["unresolved"].append({"tag": tag, "kind": "not_found_yet",
                                          "reason": "not found at the venue yet; too early to say it never arrived"})
        else:
            out["anomalies"].append({"tag": tag, "kind": "missing_at_venue",
                                     "reason": f"the ledger has it {state} but the venue has no order with this tag"})

    def _apply_status(self, rec: dict, status: dict, now: float, out: dict, mode: str) -> None:
        """Apply one venue status. `mode` is open (an order still open), turned_up (it had been declared never
        arrived) or finished (filled, cancelled or expired: fees, averages and a larger amount are taken; a larger
        amount in the order's own state is a notice, in another state an anomaly; a smaller amount or another state
        with no more units is an anomaly that changes nothing). The four notices (module docstring) are recorded
        after the order's change, in the same write; a notice that does not take rolls the write back."""
        tag, state, size = rec["tag"], rec["state"], abs(rec["units"])
        tol = _tol(rec["units"])
        vstate = status.get("state")
        notices: list[tuple[str, str, float, float]] = []

        def anomaly(kind: str, reason: str) -> None:
            out["anomalies"].append({"tag": tag, "kind": kind, "reason": reason})

        for k in status.get("bad", ()):
            anomaly("value_not_finite", f"the venue's {k} is not a finite number; it is ignored")
        if vstate not in VENUE_STATES:
            where = out["recheck_errors"] if mode == "finished" else out["unresolved"]
            where.append({"tag": tag, "kind": "state_unknown",
                          "reason": f"the venue's state is {vstate!r} (raw {_safe_repr(status.get('raw_status'))})"})
            return
        old = rec["filled_units"]
        given = status.get("filled_units")
        said = given is not None                          # None: the venue did not say; never 0, never a decrease
        filled = float(given) if said else old
        if filled < -tol:
            return anomaly("units_negative", f"the venue reports filled_units {given!r}")
        filled = max(filled, 0.0)
        if filled < old - tol:
            return anomaly("units_fell", f"the venue reports {filled} filled, less than the {old} on the ledger")
        if filled > size + tol:
            return anomaly("overfill", f"the venue reports {filled} filled, more than the order's {size}")
        new = VENUE_STATES[vstate]
        if mode == "finished":
            if said and filled > old + tol:               # taken whatever state it carries
                if new == state:                          # A2: a late amount in the order's own finished state
                    notices.append(("late_amount", f"the order is {state} with {old} filled and the venue now says "
                                    f"{filled} filled; the units are taken", old, filled))
                else:
                    anomaly("late_amount_other_state", f"the order is {state} with {old} filled, but the venue now "
                            f"says {vstate} with {filled} filled")
                new = state
            elif new != state:
                return anomaly("state_changed", f"the order is {state} with {old} filled, but the venue now says "
                               f"{vstate} with {filled if said else 'no amount'}")
        else:
            if vstate == "filled" and filled < size - tol:    # filled is only believed when the whole size is accounted for
                new = state
                out["unresolved"].append({"tag": tag, "kind": "filled_short",
                                          "reason": f"the venue calls it filled without accounting for the "
                                                    f"whole size: {filled if said else 'no amount given'} of {size}"
                                                    + ("" if said else f" ({old} on the ledger)")})
            if new != state and new not in FORWARD.get(state, ()):
                new = state                               # never backwards (a partly filled order the venue calls open)
        rise = said and filled > old + tol
        total = filled if rise else old
        fields = {"raw_status": None if status.get("raw_status") is None else str(status.get("raw_status"))}
        if status.get("venue_order_id") is not None:
            fields["venue_order_id"] = str(status["venue_order_id"])
        if rise:
            fields["filled_units"] = total
        if new in ("cancelled", "expired") and (new != state or said):
            fields["amount_unconfirmed"] = 0 if said else 1   # finished without an amount: asked again for it
        row = {"units": (1.0 if rec["units"] > 0 else -1.0) * (total - old), "price": None, "price_covers_units": None,
               "fee": None}
        wanted = rise
        fee = status.get("fee")                           # a fee total, taken from every report that gives one
        if fee is not None:
            fee, base = float(fee), rec["fee"]
            ftol = REL_TOL * max(1.0, abs(base or 0.0))
            if base is not None and fee < base - ftol:    # a fall: no row; the units are still taken
                if fee >= 0:                              # A3: the order's own total follows, a notice says so
                    fields["fee"] = fee
                    notices.append(("fee_fell", f"the venue's fee total fell from {base} to {fee}; the order's total "
                                    "follows it and no row is written", base, fee))
                else:                                     # below zero: kept, deliberately unlike a first negative total
                    anomaly("fee_fell_below_zero", f"the venue's fee total fell from {base} to {fee}, below zero; "
                            f"the stored {base} is kept")
            else:
                up = fee - (base or 0.0)
                if up > ftol or rise:
                    fields["fee"] = fee                   # the order's own total follows the venue's
                    rows_fee = math.fsum(r[0] for r in self._db.execute(
                        "SELECT fee FROM fills WHERE tag = ? AND fee IS NOT NULL", (tag,)))
                    extra = fee - rows_fee                # M3: a row's fee is only what the total exceeds the rows by
                    if extra > ftol:
                        row["fee"], wanted = extra, True
                    elif rise and extra >= -ftol:         # the total is the rows' sum: a zero fee, as before
                        row["fee"] = extra
        avg = status.get("avg_price")                     # an average, used only with the amount it covers
        if avg is not None and said:
            avg, p_units, p_avg = float(avg), rec["priced_units"] or 0.0, rec["priced_avg"]
            covers = total - p_units
            if covers > tol:
                price = (avg * total - (p_avg * p_units if p_units > 0 and p_avg is not None else 0.0)) / covers
                if not math.isfinite(price) or price < 0:  # the piece stays unpriced; does rounding explain it?
                    if avg > 0 and _rounding_explains(avg, total, p_avg, p_units):   # A4
                        fields.update(avg_price=avg, priced_units=total, priced_avg=avg)
                        notices.append(("piece_unpriced", f"the venue's average {avg} at {total} filled implies a "
                                        f"price of {price} for the {covers} units since the last priced point, which "
                                        "a cent of rounding explains; the piece is left unpriced and the order's "
                                        "average follows", p_avg, avg))
                    else:
                        anomaly("piece_price_unexplained", f"the venue's average {avg} at {total} filled implies a "
                                f"price of {price} for the {covers} units since the last priced point; they are left "
                                "unpriced")
                else:
                    row["price"], row["price_covers_units"], wanted = price, covers, True
                    fields.update(avg_price=avg, priced_units=total, priced_avg=avg)
            elif p_avg is not None and abs(avg - p_avg) > REL_TOL * max(1.0, abs(p_avg)):
                if avg > 0 and abs(avg - p_avg) <= RESTATE_BOUND + REL_TOL * max(1.0, abs(p_avg)):   # A1
                    fields.update(avg_price=avg, priced_avg=avg)
                    notices.append(("avg_restated", f"the venue's average changed from {p_avg} to {avg} with no new "
                                    "units; the order's average follows it", p_avg, avg))
                else:
                    anomaly("avg_restated_beyond", f"the venue's average changed from {p_avg} to {avg} with no new "
                            "units to price, by more than a cent; the stored average is kept")
        if new == state and not wanted and all(rec[k] == v for k, v in fields.items()):
            return                                        # nothing new: the record is left exactly as it was
        why = f"venue says {vstate}" + ("; it had been declared never arrived" if mode == "turned_up" else "")
        if not self._cas(tag, state, new, why, **fields):
            return
        own = None
        if wanted:                                        # the row first, so a notice can name it (after_fill, row)
            cur = self._db.execute("INSERT INTO fills (tag, symbol, units, price, price_covers_units, fee, time, applied) "
                                   "VALUES (?,?,?,?,?,?,?,0)", (tag, rec["symbol"], row["units"], row["price"],
                                                                row["price_covers_units"], row["fee"], now))
            own = cur.lastrowid
            out["fills"].append(dict(self._db.execute("SELECT * FROM fills WHERE id = ?", (own,)).fetchone()))
        if notices:
            after = self._db.execute("SELECT MAX(id) FROM fills WHERE tag = ?", (tag,)).fetchone()[0]
            for kind, words, was, now_is in notices:      # in the same write: a figure never followed without it
                nid = self._record_notice(tag, kind, words, was, now_is, after, own)
                out["notices"].append({"tag": tag, "kind": kind, "old": was, "new": now_is, "id": nid})
        if fields.get("fee") is not None and fields["fee"] < 0:
            self._notice_negative_fee(tag, fields["fee"])
        if new != state or wanted:
            out["updated"].append({"tag": tag, "from": state, "to": new})


def open_read_only(path: str | Path, clock: Callable[[], float] = time.time) -> OrderLedger:
    """T51 R7: an existing ledger file at layout 3 or 4 (READABLE_LAYOUTS) on a read-only connection (`mode=ro` and
    `query_only`), read by the ledger's own methods. It never writes, upgrades or rolls back the file: a layout-3
    file stays layout 3 and its orders read as LIMIT orders with no collar price. Every write through it raises
    OrderError. Refused in words, like an open for writing: a path that is a symbolic link, a file group or others
    may write, no file at the path, a database that is not an order ledger, another layout, and a file at layout 3
    whose schema is not layout 3's."""
    probe = object.__new__(OrderLedger)
    probe._path = str(path)
    probe._check_file(True)                                   # raises before anything is opened
    where = _one_line(path)
    try:
        db = sqlite3.connect("file:" + urllib.parse.quote(str(Path(path).resolve())) + "?mode=ro", uri=True,
                             timeout=BUSY_SECONDS, isolation_level=None)
    except (OSError, sqlite3.Error) as exc:
        raise OrderError(f"the order ledger {where} could not be opened to read: {_describe(exc)}") from exc
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only = ON")
        why = unreadable_layout(db)
        if why is not None:
            raise OrderError(f"the file {where} is not opened: {why}")
    except sqlite3.Error as exc:
        db.close()
        raise OrderError(f"the order ledger {where} could not be read: {_describe(exc)}") from exc
    except BaseException:
        db.close()
        raise
    led = object.__new__(OrderLedger)
    led.not_sent, led.clock, led._path, led._db = (), clock, str(path), db
    return led


def _rounding_explains(avg: float, total: float, p_avg: float | None, p_units: float) -> bool:
    """T34b-3 (A4): one cent of rounding on each of the two averages explains a negative worked-out piece:
    (avg + 0.01) x total - (p_avg - 0.01) x p_units >= 0, every term finite."""
    before = (p_avg - RESTATE_BOUND) * p_units if p_units > 0 and p_avg is not None else 0.0
    lhs = (avg + RESTATE_BOUND) * total - before
    return math.isfinite(before) and math.isfinite(lhs) and lhs >= 0


# ---- strategies sharing one account


def net_orders(deltas: Iterable[dict]) -> list[dict]:
    """One netted order per symbol, sorted by symbol, from each strategy's signed change.

    A strategy that appears twice for a symbol is summed first and a part that comes to zero is dropped. A
    symbol whose parts cancel exactly is still returned, with units 0.0: its parts still change hands
    inside the account. A symbol with no part left is not returned."""
    by_symbol: dict[str, dict[str, list[float]]] = {}
    for d in deltas:
        try:
            strategy, symbol, units = d["strategy"], d["symbol"], d["units"]
        except (KeyError, TypeError) as exc:
            raise OrderError(f"a delta needs strategy, symbol and units: {d!r}") from exc
        if not _finite(units):
            raise OrderError(f"units must be a number, not {units!r}")
        by_symbol.setdefault(symbol, {}).setdefault(strategy, []).append(float(units))
    out = []
    for symbol in sorted(by_symbol):
        parts = []
        for strategy in sorted(by_symbol[symbol]):
            u = math.fsum(by_symbol[symbol][strategy])
            if abs(u) > EPS:
                parts.append({"strategy": strategy, "units": u})
        if not parts:
            continue
        net = math.fsum(p["units"] for p in parts)
        out.append({"symbol": symbol, "units": 0.0 if abs(net) <= EPS else net, "parts": parts})
    return out


def allocate(parts: list[dict], venue_filled: float) -> dict[str, float]:
    """How much of each strategy's part is filled when the venue has filled `venue_filled` of the net.

    The side opposite the net is filled in full (it crosses inside the account); the parts on the net's side
    share the opposite side's size plus |venue_filled| in proportion to their sizes. The result's signed sum
    is `venue_filled`."""
    sizes: dict[str, float] = {}
    for p in parts:
        strategy, units = p["strategy"], p["units"]
        if strategy in sizes:
            raise OrderError(f"strategy {strategy} appears twice in the parts")
        if not _finite(units):
            raise OrderError(f"units must be a number, not {units!r}")
        sizes[strategy] = float(units)
    if not _finite(venue_filled):
        raise OrderError(f"the venue fill must be a number, not {venue_filled!r}")
    net = math.fsum(sizes.values())
    if abs(net) <= EPS:
        if abs(venue_filled) > EPS:
            raise OrderError(f"the net is 0, so the venue cannot have filled {venue_filled}")
        return dict(sizes)
    sign = 1.0 if net > 0 else -1.0
    if venue_filled * sign < -EPS:
        raise OrderError(f"the venue fill {venue_filled} is in the opposite direction to the net {net}")
    if abs(venue_filled) > abs(net) + EPS:
        raise OrderError(f"the venue fill {venue_filled} is larger than the net {net}")
    filled = min(max(venue_filled * sign, 0.0), abs(net))       # magnitude, inside [0, |net|]
    opposite = math.fsum(-u * sign for u in sizes.values() if u * sign < 0)
    same = math.fsum(u * sign for u in sizes.values() if u * sign > 0)   # = |net| + opposite
    if filled >= abs(net):
        return dict(sizes)                                        # the whole net is filled: every part in full
    share = (opposite + filled) / same
    return {strategy: (u if u * sign <= 0 else u * share) for strategy, u in sizes.items()}
