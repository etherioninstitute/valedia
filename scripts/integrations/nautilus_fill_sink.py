"""nautilus_fill_sink.py — NT fills → forward_test_ledger (Phase 0).

Records fills onto the canonical ledger path. source="live" is DEFAULT DENY
unless Valedia flags explicitly allow (NAUTILUS_ENABLED + NAUTILUS_LIVE +
VENUE_WRITER[venue]==nautilus + intent live_flag). Sandbox rows are labeled
nautilus_sandbox so Kelly/real_track_record never treat them as edge.

No nautilus_trader. No venue I/O. No --live.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import valedia_config as vc
from nautilus_intent import (
    Intent, ere_ok, intent_from_dict, intent_to_dict, round_size_down, validate_intent,
)


class FillRefused(ValueError):
    """Fill was not booked. code: live_denied | ungated | overfill | bad_fill."""

    def __init__(self, reason, code="refused"):
        super().__init__(reason)
        self.reason = reason
        self.code = code


def _as_intent(intent) -> Intent:
    if isinstance(intent, Intent):
        return intent
    return intent_from_dict(intent)


def resolve_ledger_source(intent: Intent, fill: dict) -> str:
    raw = (fill or {}).get("source", intent.ledger_source)
    src = str(raw or "paper").strip().lower()
    if src in ("sandbox", "nautilus_sandbox"):
        return "nautilus_sandbox"
    if src == "live":
        return "live"
    if "paper" in src:
        return "paper"
    return src


def live_source_allowed(intent: Intent) -> bool:
    """Default deny. Every Valedia NT live flag must be on."""
    if not vc.nautilus_live_fills_allowed(intent.venue):
        return False
    g = intent.gates or {}
    if g.get("live_flag") is not True:
        return False
    if g.get("exec_dry_run") is True:
        return False
    if g.get("fee_ok") is not True or g.get("category_ok") is not True or g.get("blacklist_ok") is not True:
        return False
    if not ere_ok(g):
        return False
    return True


def apply_fill(intent, fill: dict, recorder: Optional[Callable[..., Any]] = None,
               lot_size: float = 0.0) -> dict:
    """Book a fill to forward_test_ledger.record_entry (or injected recorder).

    Raises FillRefused if source would be live without flags, if the Intent
    is ungated, or if fill qty exceeds the Intent cap (never round up).
    """
    intent = _as_intent(intent)
    fill = dict(fill or {})
    decision = validate_intent(intent, dispatch=False)
    if not decision.ok:
        raise FillRefused(f"ungated intent: {decision.reason}", code="ungated")

    source = resolve_ledger_source(intent, fill)
    inferred = bool(fill.get("inferred"))
    if inferred and source == "live":
        raise FillRefused("inferred fill cannot be source=live", code="inferred_live")
    if source == "live" and not live_source_allowed(intent):
        raise FillRefused(
            "source=live refused (Valedia NT live flags default deny)",
            code="live_denied",
        )

    try:
        price = float(fill["price"])
        qty = float(fill["qty"])
    except (KeyError, TypeError, ValueError) as e:
        raise FillRefused(f"fill needs numeric price and qty ({e})", code="bad_fill")
    if price <= 0 or qty <= 0:
        raise FillRefused("fill price and qty must be > 0", code="bad_fill")

    if lot_size:
        rounded = round_size_down(qty, lot_size)
        if rounded <= 0:
            raise FillRefused("lot rounding reduced qty to 0", code="overfill")
        qty = rounded

    max_q = float(intent.max_qty)
    if qty > max_q:
        raise FillRefused("fill qty exceeds intent max_qty (no round-up)", code="overfill")
    if intent.qty is not None and qty > float(intent.qty):
        raise FillRefused("fill qty exceeds intent qty (no round-up)", code="overfill")

    rec = recorder
    if rec is None:
        import forward_test_ledger as ftl
        rec = ftl.record_entry
    kw = {"venue": intent.venue, "source": source}
    if fill.get("fee") is not None and fill.get("fee") != "":
        kw["entry_fee"] = float(fill["fee"])
    tid = fill.get("trade_id") or fill.get("order_id")
    if tid:
        kw["order_id"] = tid
    eid = rec(
        intent.strategy,
        intent.instrument,
        intent.side,
        price,
        qty,
        **kw,
    )
    return {"recorded": True, "id": eid, "source": source, "qty": qty, "price": price,
            "trade_id": tid or None, "inferred": inferred}


def apply_fill_event(intent, event: dict, recorder: Optional[Callable[..., Any]] = None,
                     lot_size: float = 0.0) -> dict:
    """Book a fill event only after accepted/partially_filled. submitted ≠ fill."""
    from nautilus_shadow_events import EventRefused, apply, make_order
    intent = _as_intent(intent)
    event = dict(event or {})
    order = make_order(intent_to_dict(intent))
    try:
        order = apply(order, event)
    except EventRefused as e:
        raise FillRefused(e.reason, code=e.code)
    if str(event.get("type") or "").lower() != "fill":
        return {"recorded": False, "id": None, "source": None, "state": order["state"],
                "qty": order["filled_qty"]}
    fill = {
        "price": event["price"], "qty": event["qty"],
        "source": event.get("source") or "paper",
        "inferred": event.get("inferred"),
        "trade_id": event.get("trade_id"),
        "fee": event.get("fee"),
    }
    out = apply_fill(intent, fill, recorder=recorder, lot_size=lot_size)
    out["state"] = order["state"]
    return out
