"""H6 — shadow OMS event model (Nautilus-grade practice, no LiveNode).

Event-sourced states + CommandFailure. Never imports nautilus_trader.
Never POSTs. Never a second Kelly. Fills return to one FTL via fill_sink.
submitted is not a fill. Ambiguous never invents a terminal event.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

# NT OrderStatus reduced. inflight_unknown = unresolved CommandFailure.Ambiguous.
STATES = frozenset({
    "initialized", "submitted", "accepted", "partially_filled",
    "filled", "denied", "rejected", "canceled", "expired", "voided",
    "inflight_unknown",
})
# NT is_open: Accepted | PartiallyFilled (Triggered/Pending* omitted — no live node).
OPEN = frozenset({"accepted", "partially_filled"})
# NT is_inflight: Submitted. Ambiguous is also inflight until recon.
INFLIGHT = frozenset({"submitted", "inflight_unknown"})
# NT is_closed. "not open" is NOT this set (initialized/submitted are neither).
TERMINAL = frozenset({"filled", "denied", "rejected", "canceled", "expired", "voided"})

FAILURES = frozenset({"not_sent", "venue_rejected", "ambiguous"})

# OrderFilled → Valedia fill / extract_spot_fill. Concepts only; no NT import.
ORDER_FILLED_MAP = {
    "last_qty": "qty",
    "last_px": "price",
    "commission": "fee",
    "trade_id": "trade_id",
    "ts_event": "ts",
    "liquidity_side": "liquidity",
    "client_order_id": "client_order_id",
    "instrument_id": "instrument",
    "order_side": "side",
}

_CODE_CAT = {
    "overfill": "size",
    "side": "side",
    "venue": "venue",
    "submitted_not_fill": "size",
    "dup_trade": "size",
    "inferred_live": "live_flag",
    "bad_fill": "size",
}


class EventRefused(ValueError):
    """Shadow event was not applied. code is the fail-closed category (or raw)."""

    def __init__(self, reason, code="bad_event"):
        super().__init__(reason)
        self.reason = reason
        self.code = code


def _now():
    return datetime.now(timezone.utc).isoformat()


def is_open(state) -> bool:
    return str(state) in OPEN


def is_closed(state) -> bool:
    return str(state) in TERMINAL


def is_inflight(state) -> bool:
    return str(state) in INFLIGHT


def command_failure(kind, reason="") -> dict:
    k = str(kind or "").strip().lower().replace(" ", "_")
    k = {"notsent": "not_sent", "venuerejected": "venue_rejected"}.get(k, k)
    if k not in FAILURES:
        raise EventRefused(f"failure must be not_sent|venue_rejected|ambiguous, got {kind!r}",
                           "bad_event")
    return {"kind": k, "terminal_ok": k != "ambiguous", "reason": str(reason or "")}


def risk_floor_deny(gates) -> Optional[str]:
    """ERE deny-only. Reason or None. Never a qty / kelly_frac."""
    from nautilus_intent import ere_ok
    if ere_ok(gates or {}):
        return None
    return "ere_risk_floor"


def fill_event(*, qty, price, fee=0.0, trade_id="", ts=None, liquidity=None,
               inferred=False, source="paper", side=None, venue=None,
               instrument=None, client_order_id="") -> dict:
    src = str(source or "paper").strip().lower()
    if inferred and src == "live":
        src = "paper"
    return {
        "type": "fill",
        "qty": float(qty),
        "price": float(price),
        "fee": float(fee or 0),
        "trade_id": str(trade_id or ""),
        "ts": ts or _now(),
        "liquidity": liquidity,
        "inferred": bool(inferred),
        "source": src,
        "side": side,
        "venue": venue,
        "instrument": instrument,
        "client_order_id": client_order_id,
    }


def make_order(rec) -> dict:
    rec = rec or {}
    qty = rec.get("qty")
    max_q = rec.get("max_qty")
    if max_q is None:
        max_q = qty if qty is not None else 0.0
    ts = rec.get("ts") or rec.get("enqueued_at") or _now()
    lev = rec.get("leverage")
    return {
        "state": "initialized",
        "client_order_id": rec.get("client_order_id") or rec.get("intent_id") or "",
        "intent_id": rec.get("intent_id") or "",
        "venue": str(rec.get("venue") or "").strip().lower(),
        "instrument": rec.get("instrument") or "",
        "side": str(rec.get("side") or "").strip().lower(),
        "qty": float(qty) if qty is not None and qty != "" else None,
        "max_qty": float(max_q or 0),
        "filled_qty": 0.0,
        "avg_px": None,
        "fee": 0.0,
        "leverage": 0 if lev is None else lev,
        "dispatchable": bool(rec.get("dispatchable")),
        "inferred_fill": False,
        "source": str(rec.get("ledger_source") or rec.get("source") or "paper").lower(),
        "failure": None,
        "trade_ids": [],
        "events": [{"type": "initialized", "ts": ts, "state_after": "initialized"}],
        "live": False,
    }


def _copy(order: dict) -> dict:
    o = dict(order)
    o["trade_ids"] = list(order.get("trade_ids") or [])
    o["events"] = list(order.get("events") or [])
    return o


def apply(order: dict, event: dict) -> dict:
    """Apply one event. Ambiguous never becomes terminal. submitted is not a fill."""
    if not isinstance(event, dict):
        raise EventRefused("event must be an object", "bad_event")
    o = _copy(order)
    et = str(event.get("type") or "").strip().lower()
    st = o["state"]
    if st in TERMINAL and et != "voided":
        raise EventRefused(f"terminal {st} rejects {et}", "terminal")
    if et == "submitted":
        if st != "initialized":
            raise EventRefused("submitted only from initialized", "bad_transition")
        o["state"] = "submitted"
    elif et == "accepted":
        if st != "submitted":
            raise EventRefused("accepted only from submitted", "bad_transition")
        o["state"] = "accepted"
    elif et == "fill":
        _apply_fill(o, event, st)
    elif et == "canceled":
        if st not in ("submitted", "accepted", "partially_filled", "inflight_unknown"):
            raise EventRefused(f"cancel not valid from {st}", "bad_transition")
        o["state"] = "canceled"
    elif et == "expired":
        if st not in ("accepted", "partially_filled"):
            raise EventRefused(f"expire not valid from {st}", "bad_transition")
        o["state"] = "expired"
    elif et == "voided":
        if st not in ("filled", "partially_filled"):
            raise EventRefused(f"voided only after fill, not {st}", "bad_transition")
        o["state"] = "voided"
    elif et == "denied":
        if o.get("failure") == "ambiguous":
            raise EventRefused("ambiguous cannot invent denied", "ambiguous_terminal")
        if st not in ("initialized", "submitted"):
            raise EventRefused(f"denied not valid from {st}", "bad_transition")
        o["state"] = "denied"
        o["failure"] = o.get("failure") or "not_sent"
    elif et == "rejected":
        if st not in ("submitted", "accepted", "inflight_unknown"):
            raise EventRefused(f"rejected not valid from {st}", "bad_transition")
        o["state"] = "rejected"
        o["failure"] = "venue_rejected"
    elif et == "command_failure":
        _apply_failure(o, event, st)
    elif et == "initialized":
        pass
    else:
        raise EventRefused(f"unknown event type {et!r}", "bad_event")
    ev = dict(event)
    ev["type"] = et or "initialized"
    ev["state_after"] = o["state"]
    o["events"] = list(o["events"]) + [ev]
    o["live"] = False
    return o


def _apply_fill(o, event, st):
    if st == "submitted":
        raise EventRefused("submitted is not a fill", "submitted_not_fill")
    if st not in ("accepted", "partially_filled"):
        raise EventRefused(f"fill not valid from {st}", "bad_transition")
    src = str(event.get("source") or o.get("source") or "paper").lower()
    if event.get("inferred") and src == "live":
        raise EventRefused("inferred fill cannot be source=live", "inferred_live")
    try:
        qty = float(event["qty"])
        px = float(event["price"])
    except (KeyError, TypeError, ValueError) as e:
        raise EventRefused(f"fill needs numeric qty and price ({e})", "bad_fill")
    if qty <= 0 or px <= 0:
        raise EventRefused("fill qty and price must be > 0", "bad_fill")
    if event.get("side") and str(event["side"]).strip().lower() != o["side"]:
        raise EventRefused("fill side != intent side", "side")
    if event.get("venue") and str(event["venue"]).strip().lower() != o["venue"]:
        raise EventRefused("fill venue != intent venue", "venue")
    tid = str(event.get("trade_id") or "")
    if tid and tid in o["trade_ids"]:
        raise EventRefused("duplicate trade_id", "dup_trade")
    new_qty = o["filled_qty"] + qty
    if o["max_qty"] and new_qty > float(o["max_qty"]) + 1e-12:
        raise EventRefused("fill exceeds max_qty", "overfill")
    if o.get("qty") is not None and new_qty > float(o["qty"]) + 1e-12:
        raise EventRefused("fill exceeds intent qty", "overfill")
    prev, prev_px = o["filled_qty"], o["avg_px"] or 0.0
    o["filled_qty"] = new_qty
    o["avg_px"] = ((prev_px * prev) + (px * qty)) / new_qty if new_qty else None
    o["fee"] = float(o.get("fee") or 0) + float(event.get("fee") or 0)
    if tid:
        o["trade_ids"].append(tid)
    if event.get("inferred"):
        o["inferred_fill"] = True
        if o.get("source") == "live":
            o["source"] = "paper"
    cap = float(o["qty"] if o.get("qty") is not None else o["max_qty"] or 0)
    o["state"] = "filled" if cap and new_qty >= cap - 1e-12 else "partially_filled"


def _apply_failure(o, event, st):
    cf = command_failure(event.get("kind"), event.get("reason") or "")
    if cf["kind"] == "ambiguous":
        o["state"] = "inflight_unknown"
        o["failure"] = "ambiguous"
        return
    if cf["kind"] == "not_sent":
        if o.get("failure") == "ambiguous":
            raise EventRefused("ambiguous cannot invent not_sent", "ambiguous_terminal")
        if st not in ("initialized", "submitted"):
            raise EventRefused(f"not_sent not valid from {st}", "bad_transition")
        o["state"] = "denied"
        o["failure"] = "not_sent"
        return
    if st not in ("submitted", "accepted", "inflight_unknown"):
        raise EventRefused(f"venue_rejected not valid from {st}", "bad_transition")
    o["state"] = "rejected"
    o["failure"] = "venue_rejected"


def from_record(rec) -> dict:
    rec = rec or {}
    order = make_order(rec)
    for e in rec.get("events") or []:
        if not isinstance(e, dict):
            raise EventRefused("events must be objects", "bad_event")
        if str(e.get("type") or "").lower() == "initialized":
            continue
        order = apply(order, e)
    return order


def inspect_record(rec) -> tuple[list, dict]:
    """Fail-closed diffs + compact timeline. Report-only."""
    rec = rec or {}
    try:
        order = from_record(rec)
    except EventRefused as e:
        cat = _CODE_CAT.get(e.code, e.code)
        diff = {"category": cat, "field": "event",
                "intent": rec.get("side"), "post": str(e.reason)[:100]}
        tl = {"intent_id": rec.get("intent_id"), "state": "invalid",
              "failure": e.code, "filled_qty": 0, "steps": []}
        return [diff], tl
    diffs = []
    if order.get("dispatchable") is True:
        diffs.append({"category": "dispatchable", "field": "dispatchable",
                      "intent": False, "post": True})
    if order.get("leverage") not in (None, 0, 0.0):
        diffs.append({"category": "leverage", "field": "leverage",
                      "intent": 0, "post": order.get("leverage")})
    if order.get("failure") == "ambiguous" and order["state"] in TERMINAL:
        diffs.append({"category": "ambiguous_terminal", "field": "state",
                      "intent": "inflight_unknown", "post": order["state"]})
    if order.get("inferred_fill") and str(order.get("source") or "") == "live":
        diffs.append({"category": "live_flag", "field": "inferred",
                      "intent": "paper", "post": "live"})
    if rec.get("side") and order["side"] != str(rec.get("side") or "").strip().lower():
        diffs.append({"category": "side", "field": "side",
                      "intent": rec.get("side"), "post": order["side"]})
    if rec.get("venue") and order["venue"] != str(rec.get("venue") or "").strip().lower():
        diffs.append({"category": "venue", "field": "venue",
                      "intent": rec.get("venue"), "post": order["venue"]})
    tl = {
        "intent_id": order.get("intent_id"),
        "state": order["state"],
        "filled_qty": order["filled_qty"],
        "failure": order["failure"],
        "steps": [{"t": e.get("type"), "s": e.get("state_after")} for e in order["events"]],
    }
    return diffs, tl
