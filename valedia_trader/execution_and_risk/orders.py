"""The order ledger: every real order is written down before it is sent, sent at most once, and settled from the venue.

Three things this module makes true, whatever broker sits behind it:

* at most once     `submit_once` writes `sent` and commits it to disk before it calls the venue, and only an
                   order still in `intended` is ever sent. Nothing here retries.
* never assumed    an order whose fate is not known (the venue raised, gave an answer that is not "accepted",
                   or the process died mid-call and left `sent`) is `unknown`. It is never taken as sent or as
                   not sent, and while any order is `sent` or `unknown` no further order may be submitted.
* venue is truth   `reconcile` asks the venue about every order still open on the ledger, records what it
                   says, reports each new fill exactly once and compares the venue's positions with the book.
                   A ledger file left by a crashed process needs no repair: an order found in `sent` is simply
                   treated as unknown until a reconcile settles it.

Order states: intended, sent, accepted, partly_filled, filled, cancelled, rejected, expired, refused, unknown,
never_arrived. Terminal: filled, cancelled, rejected, expired, refused, never_arrived.
`refused` means the order was stopped on this machine before any network call (an exception listed in
`not_sent`); `never_arrived` means the venue still had no order with the tag `settle_seconds` after it was
marked `sent`. Every state change is appended to an events table that cannot be updated or deleted.

Strategies that share one account trade through one netted order per symbol: `net_orders` builds it and
`allocate` shares a venue fill back out to the strategies' parts.

Nothing here talks to a network; the venue is whatever object is passed in.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Callable, Iterable, Protocol

__all__ = ["LiveVenue", "OrderLedger", "OrderError", "OrdersBlocked", "net_orders", "allocate", "STATES", "TERMINAL"]

STATES = ("intended", "sent", "accepted", "partly_filled", "filled", "cancelled", "rejected", "expired", "refused",
          "unknown", "never_arrived")
TERMINAL = frozenset({"filled", "cancelled", "rejected", "expired", "refused", "never_arrived"})
UNSETTLED = ("sent", "unknown")                           # fate not known: blocks every further submit
CHECKED = ("sent", "unknown", "accepted", "partly_filled")  # what a reconcile asks the venue about
VENUE_STATES = {"open": "accepted", "partly_filled": "partly_filled", "filled": "filled", "cancelled": "cancelled",
                "rejected": "rejected", "expired": "expired"}
EPS = 1e-9                                                # units this close are the same amount
MAX_TEXT = 200


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
    partly_filled, filled, cancelled, rejected, expired or unknown_status; filled_units is unsigned, or None
    when the venue did not say (never read as 0); avg_price and fee may be None. `positions()` returns units
    held per symbol."""
    name: str

    def submit(self, symbol: str, units: float, tag: str, price: float | None = None,
               limit_price: float | None = None) -> dict: ...

    def order_status(self, tag: str) -> dict | None: ...

    def positions(self) -> dict[str, float]: ...


def _describe(exc: BaseException) -> str:
    """The exception's class name and at most 200 characters of its message."""
    return f"{type(exc).__name__}: {str(exc)[:MAX_TEXT]}"


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _same_parts(a, b) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


SCHEMA = (
    "CREATE TABLE IF NOT EXISTS orders (tag TEXT PRIMARY KEY, symbol TEXT NOT NULL, units REAL NOT NULL, "
    "reference_price REAL NOT NULL, limit_price REAL, batch TEXT, parts TEXT, state TEXT NOT NULL, created REAL NOT NULL, "
    "sent_at REAL, updated REAL NOT NULL, venue TEXT, venue_order_id TEXT, filled_units REAL NOT NULL DEFAULT 0, "
    "avg_price REAL, fee REAL, raw_status TEXT, note TEXT);"
    "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT NOT NULL, time REAL NOT NULL, "
    "from_state TEXT, to_state TEXT NOT NULL, note TEXT);"
    "CREATE TABLE IF NOT EXISTS reconciles (id INTEGER PRIMARY KEY AUTOINCREMENT, time REAL NOT NULL, "
    "clean INTEGER NOT NULL, summary TEXT NOT NULL);"
    "CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events "
    "BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;"
    "CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events "
    "BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;"
)


class OrderLedger:
    """Every real order and every change of its state, in one sqlite file (":memory:" for tests).

    `not_sent` lists exception types that guarantee nothing was transmitted; any other failure of a submit
    leaves the order `unknown`. `clock` gives the time in seconds."""

    def __init__(self, path: str | Path = ":memory:", not_sent: tuple = (), clock: Callable[[], float] = time.time) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.not_sent = tuple(not_sent)
        self.clock = clock
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA synchronous = FULL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    # ---- reading

    @staticmethod
    def _record(row: sqlite3.Row) -> dict:
        rec = dict(row)
        rec["parts"] = json.loads(rec["parts"]) if rec["parts"] is not None else None
        return rec

    def order(self, tag: str) -> dict | None:
        row = self._db.execute("SELECT * FROM orders WHERE tag = ?", (tag,)).fetchone()
        return self._record(row) if row is not None else None

    def orders(self, states: Iterable[str] | None = None) -> list[dict]:
        """Orders in the order they were intended; only those in `states` when given."""
        if states is None:
            rows = self._db.execute("SELECT * FROM orders ORDER BY rowid")
        else:
            states = list(states)
            marks = ",".join("?" * len(states))
            rows = self._db.execute(f"SELECT * FROM orders WHERE state IN ({marks}) ORDER BY rowid", states)
        return [self._record(r) for r in rows]

    def events(self, tag: str | None = None) -> list[dict]:
        sql, args = "SELECT tag, time, from_state, to_state, note FROM events", ()
        if tag is not None:
            sql, args = sql + " WHERE tag = ?", (tag,)
        return [{"tag": r["tag"], "time": r["time"], "from": r["from_state"], "to": r["to_state"], "note": r["note"]}
                for r in self._db.execute(sql + " ORDER BY id", args)]

    def last_reconcile(self) -> dict | None:
        row = self._db.execute("SELECT time, clean, summary FROM reconciles ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return {"time": row["time"], "clean": bool(row["clean"]), "summary": json.loads(row["summary"])}

    def close(self) -> None:
        self._db.close()

    # ---- writing (each call commits; callers group writes inside `with self._db`)

    def _move(self, tag: str, old: str | None, new: str, why: str | None = None, **fields) -> None:
        """Set the state and any other fields; a change of state is also appended to the events, with `why`."""
        now = self.clock()
        sets = ", ".join(f"{k} = ?" for k in ("state", "updated", *fields))
        self._db.execute(f"UPDATE orders SET {sets} WHERE tag = ?", (new, now, *fields.values(), tag))
        if old != new:
            self._db.execute("INSERT INTO events (tag, time, from_state, to_state, note) VALUES (?,?,?,?,?)",
                             (tag, now, old, new, why))

    def intend(self, tag: str, symbol: str, units: float, reference_price: float, limit_price: float | None = None,
               batch: str | None = None, parts: list | None = None) -> dict:
        """Record an order in `intended`. The same tag again returns the record when every field matches, else raises."""
        if not isinstance(tag, str) or not tag:
            raise OrderError("an order needs a tag")
        if not isinstance(symbol, str) or not symbol:
            raise OrderError("an order needs a symbol")
        if not _finite(units) or units == 0:
            raise OrderError(f"units must be a non-zero number, not {units!r}")
        if not _finite(reference_price) or reference_price <= 0:
            raise OrderError(f"the reference price must be above zero, not {reference_price!r}")
        if limit_price is not None and (not _finite(limit_price) or limit_price <= 0):
            raise OrderError(f"a limit price must be above zero, not {limit_price!r}")
        try:
            parts_json = json.dumps(parts) if parts is not None else None
        except (TypeError, ValueError) as exc:
            raise OrderError(f"the parts cannot be stored: {_describe(exc)}") from exc
        units, reference_price = float(units), float(reference_price)
        limit_price = float(limit_price) if limit_price is not None else None
        existing = self.order(tag)
        if existing is not None:
            mine = {"symbol": symbol, "units": units, "reference_price": reference_price, "limit_price": limit_price,
                    "batch": batch}
            differ = [k for k, v in mine.items() if existing[k] != v]
            if not _same_parts(existing["parts"], parts):
                differ.append("parts")
            if differ:
                raise OrderError(f"order {tag} is already on file with different {', '.join(differ)}")
            return existing
        now = self.clock()
        with self._db:
            self._db.execute("INSERT INTO orders (tag, symbol, units, reference_price, limit_price, batch, parts, state, "
                             "created, updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (tag, symbol, units, reference_price, limit_price, batch, parts_json, "intended", now, now))
            self._db.execute("INSERT INTO events (tag, time, from_state, to_state, note) VALUES (?,?,?,?,?)",
                             (tag, now, None, "intended", None))
        return self.order(tag)

    # ---- the gate

    def blocked(self, max_age_seconds: float = 21600) -> list[str]:
        """Why no order may be submitted now; empty when nothing stands in the way."""
        reasons = [f"order {o['tag']} is {o['state']}: its fate is not known until a reconcile settles it"
                   for o in self.orders(UNSETTLED)]
        last = self.last_reconcile()
        if last is None:
            reasons.append("no reconcile recorded yet")
        else:
            if not last["clean"]:
                reasons.append("the latest reconcile was not clean")
            age = self.clock() - last["time"]
            if age > max_age_seconds:
                reasons.append(f"the latest reconcile is {age:.0f} s old, more than {max_age_seconds:.0f} s")
        return reasons

    def submit_once(self, venue: LiveVenue, tag: str, max_age_seconds: float = 21600) -> dict:
        """Send the order if it is still `intended`, at most once. Never retries; a venue failure is in the record.

        `max_age_seconds` is passed to `blocked`, so a caller can require a fresh reconcile before a batch."""
        reasons = self.blocked(max_age_seconds)
        if reasons:
            raise OrdersBlocked(reasons)
        rec = self.order(tag)
        if rec is None:
            raise OrderError(f"no order with tag {tag} is on file")
        if rec["state"] != "intended":
            return rec
        with self._db:                                    # on disk before the venue hears anything
            self._move(tag, "intended", "sent", f"to {getattr(venue, 'name', '?')}", sent_at=self.clock(),
                       venue=str(getattr(venue, "name", "")))
        try:
            result = venue.submit(rec["symbol"], rec["units"], tag, price=rec["reference_price"], limit_price=rec["limit_price"])
        except Exception as exc:                          # a KeyboardInterrupt or a death leaves `sent`
            if self.not_sent and isinstance(exc, self.not_sent):
                new, note = "refused", _describe(exc)
            else:
                new, note = "unknown", _describe(exc)
            with self._db:
                self._move(tag, "sent", new, note, note=note)
            return self.order(tag)
        if isinstance(result, dict) and result.get("status") == "accepted":
            vid = result.get("venue_order_id")
            with self._db:
                self._move(tag, "sent", "accepted", None, venue_order_id=str(vid) if vid is not None else None)
        else:
            note = f"the venue did not say accepted: {repr(result)[:MAX_TEXT]}"
            with self._db:
                self._move(tag, "sent", "unknown", note, note=note)
        return self.order(tag)

    # ---- reconcile

    def reconcile(self, venue: LiveVenue, book_positions: dict[str, float], tolerance: float = 1e-6,
                  settle_seconds: float = 120) -> dict:
        """Ask the venue about every order still open on the ledger, report new fills once, compare positions."""
        now = self.clock()
        out = {"clean": False, "orders_checked": 0, "updated": [], "fills": [], "unresolved": [], "anomalies": [],
               "position_differences": {}, "positions_error": None, "time": now}
        for rec in self.orders(CHECKED):
            out["orders_checked"] += 1
            self._reconcile_one(venue, rec, now, settle_seconds, out)
        try:
            held = venue.positions()
            book = book_positions or {}
            for sym in sorted(set(held) | set(book)):
                b, v = float(book.get(sym, 0.0)), float(held.get(sym, 0.0))
                if abs(b - v) > tolerance:
                    out["position_differences"][sym] = {"book": b, "venue": v}
        except Exception as exc:
            out["positions_error"] = f"the venue's positions could not be read: {_describe(exc)}"
        still = self.orders(UNSETTLED)
        out["clean"] = not (out["unresolved"] or still or out["anomalies"] or out["position_differences"]
                            or out["positions_error"])
        with self._db:
            self._db.execute("INSERT INTO reconciles (time, clean, summary) VALUES (?,?,?)",
                             (now, int(out["clean"]), json.dumps(out, default=str)))
        return out

    def _reconcile_one(self, venue: LiveVenue, rec: dict, now: float, settle_seconds: float, out: dict) -> None:
        tag, state = rec["tag"], rec["state"]
        try:
            status = venue.order_status(tag)
        except Exception as exc:
            out["unresolved"].append({"tag": tag, "reason": f"status could not be read: {_describe(exc)}"})
            return
        if status is None:
            if state in UNSETTLED:
                sent_at = rec["sent_at"] if rec["sent_at"] is not None else rec["created"]
                if now - sent_at > settle_seconds:
                    with self._db:
                        self._move(tag, state, "never_arrived",
                                   f"the venue had no order with this tag {now - sent_at:.0f} s after it was sent")
                    out["updated"].append({"tag": tag, "from": state, "to": "never_arrived"})
                else:
                    out["unresolved"].append({"tag": tag, "reason": "not found at the venue yet; too early to say it never arrived"})
            else:
                out["anomalies"].append({"tag": tag, "reason": f"the ledger has it {state} but the venue has no order with this tag"})
            return
        if not isinstance(status, dict):
            out["unresolved"].append({"tag": tag, "reason": f"the venue's status is not a dict: {repr(status)[:MAX_TEXT]}"})
            return
        vstate = status.get("state")
        if vstate not in VENUE_STATES:
            out["unresolved"].append({"tag": tag, "reason": f"the venue's state is {vstate!r} (raw {status.get('raw_status')!r})"})
            return
        old = rec["filled_units"]
        filled = status.get("filled_units")
        said = filled is not None                         # None: the venue did not say; never 0, never a decrease
        if not said:
            filled = old
        if not _finite(filled) or filled < -EPS:
            out["anomalies"].append({"tag": tag, "reason": f"the venue reports filled_units {filled!r}"})
            return
        filled = max(float(filled), 0.0)
        if filled < old - EPS:
            out["anomalies"].append({"tag": tag, "reason": f"the venue reports {filled} filled, less than the {old} on the ledger"})
            return
        if filled > abs(rec["units"]) + EPS:
            out["anomalies"].append({"tag": tag, "reason": f"the venue reports {filled} filled, more than the order's {abs(rec['units'])}"})
            return
        new = VENUE_STATES[vstate]
        short = vstate == "filled" and filled < abs(rec["units"]) - EPS
        if short:                                         # filled is only believed when the whole size is accounted for
            new = state
            out["unresolved"].append({"tag": tag, "reason": f"the venue calls it filled without accounting for the whole "
                                      f"size: {filled if said else 'no amount given'} of {abs(rec['units'])}"
                                      + ("" if said else f" ({old} on the ledger)")})
        fields = {"raw_status": None if status.get("raw_status") is None else str(status.get("raw_status"))}
        if status.get("venue_order_id") is not None:
            fields["venue_order_id"] = str(status["venue_order_id"])
        if said and filled > old + EPS:
            out["fills"].append(self._fill(rec, filled, status.get("avg_price"), status.get("fee")))
            fields.update(filled_units=filled, avg_price=status.get("avg_price"), fee=status.get("fee"))
        if new == state and all(rec[k] == v for k, v in fields.items()):
            return                                        # nothing new: the record is left exactly as it was
        with self._db:
            self._move(tag, state, new, f"venue says {vstate}", **fields)
        if new != state or "filled_units" in fields:
            out["updated"].append({"tag": tag, "from": state, "to": new})

    @staticmethod
    def _fill(rec: dict, filled: float, avg, fee) -> dict:
        """The newly filled amount only: signed like the order, at the price implied by the old and new averages."""
        old, old_avg, old_fee = rec["filled_units"], rec["avg_price"], rec["fee"]
        delta = filled - old
        price = None
        if avg is not None and (old <= EPS or old_avg is not None):
            price = (float(avg) * filled - (float(old_avg) * old if old > EPS else 0.0)) / delta
        fee_up = None if fee is None else float(fee) - (float(old_fee) if old_fee is not None else 0.0)
        sign = 1.0 if rec["units"] > 0 else -1.0
        return {"tag": rec["tag"], "symbol": rec["symbol"], "units": sign * delta, "price": price, "fee": fee_up}


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
