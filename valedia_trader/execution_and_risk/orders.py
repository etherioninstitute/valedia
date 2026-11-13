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
a larger amount is taken and also listed as an anomaly, a smaller amount or a different state is an anomaly
that changes nothing. `refused` means the order was stopped on this machine before any network call (an exception listed
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
is the increase of the venue's fee total since the last total it gave (None when there is none); its price is
the price of all units since the last report that gave an average with an amount (`price_covers_units` of
them). Over any sequence the units add up to the stored total, the fees to the last fee total, and, when the
last report with an average left nothing unpriced, the priced rows to that average times that total. A fee
total that falls, or an average that implies a negative or non-finite price, is an anomaly for that piece of
information only: the stored fee total (or priced point) is kept, and a rise in units in the same report is
still taken, its row carrying fee (or price) None. A reconcile reports a fill or an update only once the write
that made it true is committed; an order whose write failed is listed as unresolved. The book must apply every row (`unapplied_fills`, then `mark_applied`)
before another order may be submitted, because orders are sized from the book.

Strategies that share one account trade through one netted order per symbol: `net_orders` builds it and
`allocate` shares a venue fill back out to the strategies' parts.

Nothing here talks to a network; the venue is whatever object is passed in.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable, Protocol

__all__ = ["LiveVenue", "OrderLedger", "OrderError", "OrdersBlocked", "net_orders", "allocate", "STATES", "TERMINAL",
           "FORWARD"]

STATES = ("intended", "sent", "accepted", "partly_filled", "filled", "cancelled", "rejected", "expired", "refused",
          "unknown", "never_arrived")
TERMINAL = frozenset({"filled", "cancelled", "rejected", "expired", "refused"})
UNSETTLED = ("sent", "unknown")                           # fate not known: blocks every further submit
CHECKED = ("sent", "unknown", "accepted", "partly_filled")  # what a reconcile asks the venue about (plus rechecks)
FINISHED = ("filled", "cancelled", "expired")             # asked again for late fees and averages
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


class OrderError(RuntimeError):
    pass


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
        return name
    return f"{name}: {text[:MAX_TEXT]}"


def _safe_repr(x) -> str:
    try:
        return repr(x)[:MAX_TEXT]
    except Exception:
        return f"<{type(x).__name__} that cannot be shown>"


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


LAYOUT_VERSION = 2                                        # PRAGMA user_version of a file this module writes
SCHEMA = [
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
]
SQLITE_INT = (-(2 ** 63), 2 ** 63 - 1)


class OrderLedger:
    """Every real order, every change of its state and every fill, in one sqlite file (":memory:" for tests).

    Any number of objects and processes may share the file. Each write is an IMMEDIATE transaction (it takes the
    write lock when it begins, so two writers cannot deadlock upgrading a read lock), and a connection waits up
    to BUSY_SECONDS for another's write to finish. A write that fails (even at COMMIT) is rolled back and raised
    as OrderError; the object is outside any transaction afterwards and works again once the cause is gone.

    The file carries a layout version (`PRAGMA user_version` = LAYOUT_VERSION). A new file is created at it; a
    file written with any other layout is refused, never repaired. `not_sent` lists exception classes that
    guarantee nothing was transmitted; any other failure of a submit leaves the order `unknown`. `clock` gives
    the time in seconds."""

    def __init__(self, path: str | Path = ":memory:", not_sent: tuple = (), clock: Callable[[], float] = time.time) -> None:
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
        try:
            if self._path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._db = self._connect()
        except (OSError, sqlite3.Error) as exc:
            raise OrderError(f"the order ledger {path} could not be opened: {_describe(exc)}") from exc
        try:
            with self._write():
                version = self._db.execute("PRAGMA user_version").fetchone()[0]
                ours = self._db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'orders'").fetchone()[0]
                if ours and version != LAYOUT_VERSION:
                    raise OrderError(f"the ledger file {path} was written by a different version of this module "
                                     f"(layout {version}, this module writes layout {LAYOUT_VERSION}); it is not opened")
                if not ours:
                    for statement in SCHEMA:
                        self._db.execute(statement)
                    self._db.execute(f"PRAGMA user_version = {LAYOUT_VERSION}")
        except BaseException:
            self._close_quietly()
            raise

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
        return rec

    def order(self, tag: str) -> dict | None:
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
        if tag is not None:
            sql, args = sql + " WHERE tag = ?", (tag,)
        return [{"tag": r["tag"], "time": r["time"], "from": r["from_state"], "to": r["to_state"], "note": r["note"]}
                for r in self._read(sql + " ORDER BY id", args)]

    def fills(self, tag: str | None = None) -> list[dict]:
        """Every fill row, oldest first; only the order's when `tag` is given."""
        sql, args = "SELECT * FROM fills", ()
        if tag is not None:
            sql, args = sql + " WHERE tag = ?", (tag,)
        return [dict(r) for r in self._read(sql + " ORDER BY id", args)]

    def unapplied_fills(self) -> list[dict]:
        """The fill rows the book has not applied yet, oldest first."""
        return [dict(r) for r in self._read("SELECT * FROM fills WHERE applied = 0 ORDER BY id")]

    def mark_applied(self, ids: Iterable[int]) -> int:
        """Mark fill rows applied to the book. Returns how many changed. Only plain ints (not bools) in sqlite's
        integer range are ids; anything else, and an applied or unknown id, changes nothing and does not raise."""
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
               batch: str | None = None, parts: list | None = None) -> dict:
        """Record an order in `intended`. The same tag again returns the record when every field matches, else raises."""
        if not isinstance(tag, str) or not tag or not _utf8(tag):
            raise OrderError(f"an order needs a tag that is text encodable as UTF-8, not {_safe_repr(tag)}")
        if not isinstance(symbol, str) or not symbol or not _utf8(symbol):
            raise OrderError(f"an order needs a symbol that is text encodable as UTF-8, not {_safe_repr(symbol)}")
        if isinstance(batch, str) and not _utf8(batch):
            raise OrderError(f"the batch must be text encodable as UTF-8, not {_safe_repr(batch)}")
        if not _finite(units) or abs(units) < MIN_UNITS:
            raise OrderError(f"units must be a finite number at least {MIN_UNITS} in size, not {_safe_repr(units)}")
        if not _finite(reference_price) or reference_price <= 0:
            raise OrderError(f"the reference price must be a finite number above zero, not {_safe_repr(reference_price)}")
        if limit_price is not None and (not _finite(limit_price) or limit_price <= 0):
            raise OrderError(f"a limit price must be a finite number above zero, not {_safe_repr(limit_price)}")
        try:
            parts_json = json.dumps(parts) if parts is not None else None
        except (TypeError, ValueError) as exc:
            raise OrderError(f"the parts cannot be stored: {_describe(exc)}") from exc
        units, reference_price = float(units), float(reference_price)
        limit_price = float(limit_price) if limit_price is not None else None
        with self._write():
            existing = self.order(tag)
            if existing is None:
                now = self.clock()
                self._db.execute("INSERT INTO orders (tag, symbol, units, reference_price, limit_price, batch, parts, state, "
                                 "created, updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                 (tag, symbol, units, reference_price, limit_price, batch, parts_json, "intended", now, now))
                self._db.execute("INSERT INTO events (tag, time, from_state, to_state, note) VALUES (?,?,?,?,?)",
                                 (tag, now, None, "intended", None))
        if existing is not None:
            mine = {"symbol": symbol, "units": units, "reference_price": reference_price, "limit_price": limit_price,
                    "batch": batch}
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
        if not isinstance(who, str) or not who.strip() or not isinstance(note, str) or not note.strip():
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
        call was in flight, the acceptance moves it on to accepted, with a note that it arrived late.

        The check of `blocked(max_age_seconds)` and the change `intended -> sent` are one write transaction: the
        caller that makes the change is the only one that calls the venue. Another caller gets OrdersBlocked while
        that order is in flight, or the record once it is settled; neither calls the venue."""
        name = str(getattr(venue, "name", "") or "")
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
                self._cas(tag, "sent", new, note, note=note)
            return self.order(tag)
        accepted = False
        try:
            accepted = isinstance(result, dict) and result.get("status") == "accepted"
            vid = result.get("venue_order_id") if accepted else None
            vid = str(vid) if vid is not None else None
        except Exception:
            vid = None
        with self._write():
            if accepted and not self._cas(tag, "sent", "accepted", None, venue_order_id=vid):
                now_state = self.order(tag)["state"]
                if now_state in ("unknown", "never_arrived"):
                    self._cas(tag, now_state, "accepted", f"the venue's acceptance arrived late, after the order was "
                              f"marked {now_state}", venue_order_id=vid)
            elif not accepted:
                note = f"the venue did not say accepted: {_safe_repr(result)}"
                self._cas(tag, "sent", "unknown", note, note=note)
        return self.order(tag)

    # ---- reconcile

    def reconcile(self, venue: LiveVenue, book_positions: dict[str, float], tolerance: float = 1e-6,
                  settle_seconds: float = 120, recheck_seconds: float = 259200) -> dict:
        """Ask the venue about every order still open on the ledger, write each new fill once, compare positions.

        Also asked, for `recheck_seconds` after they reached that state: every order marked never_arrived (one the
        venue now knows has turned up: an anomaly, moved to the venue's state, with its fills), and every order
        that reached filled, cancelled or expired with something filled or without an amount (late fees, averages
        and a larger amount are taken, the larger amount also as an anomaly; a smaller amount or a different state
        is an anomaly that changes nothing). An exception from the venue on one of these goes
        to `recheck_errors` and does not make the reconcile unclean, so for an order wrongly declared never
        arrived the position comparison is then the remaining safeguard.

        `settle_seconds` must be longer than the longest time a submit call can take: an order still absent at the
        venue that long after it was sent is declared never arrived. Never raises because of one order, one venue
        call or anything the venue returns; the reconcile is always recorded, and lists a fill or an update only
        when the write behind it was committed (an order whose write failed is unresolved)."""
        now = self.clock()
        out = {"clean": False, "orders_checked": 0, "updated": [], "fills": [], "unresolved": [], "anomalies": [],
               "position_differences": {}, "positions_error": None, "recheck_errors": [], "time": now}
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
            self._compare_positions(venue, book_positions, tolerance, out)
            still = self.orders(UNSETTLED)
            out["clean"] = not (out["unresolved"] or still or out["anomalies"] or out["position_differences"]
                                or out["positions_error"])
        except Exception as exc:                          # the ledger's own failure: recorded, not clean
            out["clean"] = False
            out["anomalies"].append({"tag": None, "reason": f"the reconcile stopped: {_describe(exc)}"})
        try:
            summary = json.dumps(out, default=str)
        except Exception as exc:                          # never lose the record over what the venue sent
            out["clean"] = False
            out["anomalies"].append({"tag": None, "reason": f"the result could not be stored in full: {_describe(exc)}"})
            summary = json.dumps({"time": now, "clean": False, "error": _describe(exc)})
        with self._write():
            self._db.execute("INSERT INTO reconciles (time, clean, summary) VALUES (?,?,?)", (now, int(out["clean"]), summary))
        return out

    def _compare_positions(self, venue: LiveVenue, book_positions, tolerance: float, out: dict) -> None:
        try:
            held = dict(venue.positions())
            book = dict(book_positions or {})
        except Exception as exc:
            out["positions_error"] = f"the venue's positions could not be read: {_describe(exc)}"
            return
        odd = [k for k in (*held, *book) if not isinstance(k, str)]
        if odd:
            out["positions_error"] = f"a position key is not a symbol string: {_safe_repr(odd)}"
            return
        unapplied: dict[str, float] = {}
        for f in self.unapplied_fills():
            unapplied[f["symbol"]] = unapplied.get(f["symbol"], 0.0) + f["units"]
        for sym in sorted(set(held) | set(book) | set(unapplied)):
            b, v, u = book.get(sym, 0.0), held.get(sym, 0.0), unapplied.get(sym, 0.0)
            if not (_finite(b) and _finite(v)) or abs(float(b) + u - float(v)) > tolerance:
                out["position_differences"][sym] = {"book": b, "venue": v, "unapplied": u}

    def _reconcile_one(self, venue: LiveVenue, tag: str, recheck: bool, now: float, settle_seconds: float, out: dict) -> None:
        try:
            status = venue.order_status(tag)
        except Exception as exc:
            if recheck:                                   # a venue that raises for "no such order" must not block
                out["recheck_errors"].append({"tag": tag, "reason": f"status could not be read: {_describe(exc)}"})
            else:
                out["unresolved"].append({"tag": tag, "reason": f"status could not be read: {_describe(exc)}"})
            return
        self._apply(tag, status, recheck, now, settle_seconds, out)

    def _apply(self, tag: str, status, recheck: bool, now: float, settle_seconds: float, out: dict) -> None:
        """Use one venue answer. What to do is decided from the order as re-read inside the write transaction, so an
        order another ledger moved meanwhile (to never_arrived, say) is handled as it now is."""
        if status is not None and not isinstance(status, dict):
            (out["anomalies"] if recheck else out["unresolved"]).append(
                {"tag": tag, "reason": f"the venue's status is not a dict: {_safe_repr(status)}"})
            return
        if status is not None:
            bad = [k for k in ("filled_units", "avg_price", "fee") if status.get(k) is not None and not _finite(status.get(k))]
            if bad:
                out["anomalies"].append({"tag": tag, "reason": "not a finite number: " + ", ".join(
                    f"{k}={_safe_repr(status.get(k))}" for k in bad)})
                return
        local: dict[str, list] = {k: [] for k in ("fills", "updated", "unresolved", "anomalies", "recheck_errors")}
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
                        local["anomalies"].append({"tag": tag, "reason": "declared never arrived, but the venue has an "
                                                   f"order with this tag ({_safe_repr(status.get('state'))})"})
                        self._apply_status(rec, status, now, local, "turned_up")
                elif state in FINISHED:
                    if status is None:
                        local["recheck_errors"].append({"tag": tag, "reason": f"the venue no longer has this {state} order"})
                    else:
                        self._apply_status(rec, status, now, local, "finished")
        except Exception as exc:                          # rolled back: nothing of it is reported, it stays unresolved
            out["unresolved"].append({"tag": tag, "reason": f"the venue's answer could not be written or used: "
                                      f"{_describe(exc)}"})
            return
        for k, items in local.items():                    # committed: now it may be reported
            out[k].extend(items)

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
                out["unresolved"].append({"tag": tag, "reason": "not found at the venue yet; too early to say it never arrived"})
        else:
            out["anomalies"].append({"tag": tag, "reason": f"the ledger has it {state} but the venue has no order with this tag"})

    def _apply_status(self, rec: dict, status: dict, now: float, out: dict, mode: str) -> None:
        """Apply one venue status. `mode` is open (an order still open), turned_up (it had been declared never
        arrived) or finished (filled, cancelled or expired: fees, averages and a larger amount are taken, the
        larger amount also as an anomaly; a smaller amount or another state is an anomaly that changes nothing)."""
        tag, state, size = rec["tag"], rec["state"], abs(rec["units"])
        tol = _tol(rec["units"])
        vstate = status.get("state")

        def anomaly(reason: str) -> None:
            out["anomalies"].append({"tag": tag, "reason": reason})

        if vstate not in VENUE_STATES:
            where = out["recheck_errors"] if mode == "finished" else out["unresolved"]
            where.append({"tag": tag, "reason": f"the venue's state is {vstate!r} (raw {_safe_repr(status.get('raw_status'))})"})
            return
        old = rec["filled_units"]
        given = status.get("filled_units")
        said = given is not None                          # None: the venue did not say; never 0, never a decrease
        filled = float(given) if said else old
        if filled < -tol:
            return anomaly(f"the venue reports filled_units {given!r}")
        filled = max(filled, 0.0)
        if filled < old - tol:
            return anomaly(f"the venue reports {filled} filled, less than the {old} on the ledger")
        if filled > size + tol:
            return anomaly(f"the venue reports {filled} filled, more than the order's {size}")
        new = VENUE_STATES[vstate]
        if mode == "finished":
            if new != state:
                return anomaly(f"the order is {state} with {old} filled, but the venue now says {vstate} with "
                               f"{filled if said else 'no amount'}")
            if said and filled > old + tol:               # taken, and a person should look
                anomaly(f"the order is {state} with {old} filled, but the venue now says {filled} filled")
        else:
            if vstate == "filled" and filled < size - tol:    # filled is only believed when the whole size is accounted for
                new = state
                out["unresolved"].append({"tag": tag, "reason": f"the venue calls it filled without accounting for the "
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
            if base is not None and fee < base - ftol:    # this piece only: the fee is left, units are still taken
                anomaly(f"the venue's fee total fell from {base} to {fee}; the stored {base} is kept")
            else:
                up = fee - (base or 0.0)
                if up > ftol or rise:
                    fields["fee"], row["fee"], wanted = fee, up, True
        avg = status.get("avg_price")                     # an average, used only with the amount it covers
        if avg is not None and said:
            avg, p_units, p_avg = float(avg), rec["priced_units"] or 0.0, rec["priced_avg"]
            covers = total - p_units
            if covers > tol:
                price = (avg * total - (p_avg * p_units if p_units > 0 and p_avg is not None else 0.0)) / covers
                if not math.isfinite(price) or price < 0:  # this piece only: the units stay unpriced
                    anomaly(f"the venue's average {avg} at {total} filled implies a price of {price} for the "
                            f"{covers} units since the last priced point; they are left unpriced")
                else:
                    row["price"], row["price_covers_units"], wanted = price, covers, True
                    fields.update(avg_price=avg, priced_units=total, priced_avg=avg)
            elif p_avg is not None and abs(avg - p_avg) > REL_TOL * max(1.0, abs(p_avg)):
                anomaly(f"the venue's average changed from {p_avg} to {avg} with no new units to price")
        if new == state and not wanted and all(rec[k] == v for k, v in fields.items()):
            return                                        # nothing new: the record is left exactly as it was
        why = f"venue says {vstate}" + ("; it had been declared never arrived" if mode == "turned_up" else "")
        if not self._cas(tag, state, new, why, **fields):
            return
        if wanted:
            cur = self._db.execute("INSERT INTO fills (tag, symbol, units, price, price_covers_units, fee, time, applied) "
                                   "VALUES (?,?,?,?,?,?,?,0)", (tag, rec["symbol"], row["units"], row["price"],
                                                                row["price_covers_units"], row["fee"], now))
            out["fills"].append(dict(self._db.execute("SELECT * FROM fills WHERE id = ?", (cur.lastrowid,)).fetchone()))
        if new != state or wanted:
            out["updated"].append({"tag": tag, "from": state, "to": new})


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
