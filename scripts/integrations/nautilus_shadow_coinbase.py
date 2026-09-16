"""nautilus_shadow_coinbase.py — Slice 2: shadow Intents from Coinbase allocator.

Emits Slice-1 Intents alongside the existing Valedia Coinbase writer.
Never dispatchable. Never a LiveNode send. Writer stays valedia.

Also diffs Intent fields vs the POST body coinbase_trader would send, so
allocator/trader mismatches are visible before any cutover.

No nautilus_trader. No venue I/O. No --live.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import json
import os
from typing import Optional

import valedia_config as vc
from nautilus_intent import enqueue_intent, make_intent

_MODELS = os.path.expanduser("~/.openclaw/workspace/models")


def coinbase_product_id(ticker: str) -> str:
    """Canonical Coinbase product_id. Allocator currently strips -USD; we do not."""
    t = str(ticker or "").strip().upper()
    if not t:
        return t
    if "-" in t:
        return t
    return f"{t}-USD"


def allocator_coinbase_call(allocation: dict) -> dict:
    """How master_allocator currently invokes Coinbase (broken on purpose until cutover).

    It imports place_market_order (does not exist) and strips -USD from the ticker.
    Do NOT add place_market_order — that would open a live path that is currently dead.
    """
    raw = str((allocation or {}).get("ticker") or "")
    exists = False
    try:
        import coinbase_trader as ct
        exists = hasattr(ct, "place_market_order")
    except Exception:
        exists = False
    # Even if trader failed to import (jwt), the function is not on that module.
    return {
        "fn": "place_market_order",
        "exists_on_trader": bool(exists),
        "args": {
            "ticker": raw.replace("-USD", ""),
            "side": (allocation or {}).get("side"),
            "allocation": (allocation or {}).get("allocation"),
        },
        "note": "allocator strips -USD and calls a missing function; trader POSTs product_id + quote_size (buy) or base_size (sell)",
    }


def post_body_from_intent(intent) -> dict:
    """What coinbase_trader would POST if it were given this Intent's fields.

    Buy → quote_size from notional. Sell → base_size from qty. A sell with only
    notional cannot be expressed as a Coinbase market IOC (needs base_size).
    """
    from coinbase_order_body import build_market_order_body
    if intent.side == "buy":
        if intent.notional is None:
            raise ValueError("buy Intent needs notional (quote_size)")
        return build_market_order_body(
            intent.instrument, intent.side,
            usd_amount=intent.notional,
            client_order_id=intent.client_order_id,
        )
    if intent.qty is None:
        raise ValueError("sell Intent has no qty; trader POST needs base_size")
    return build_market_order_body(
        intent.instrument, intent.side,
        crypto_amount=intent.qty,
        client_order_id=intent.client_order_id,
    )


def post_body_from_allocator_call(allocation: dict, client_order_id: str) -> dict:
    """POST body if today's allocator args were forced through the trader builder.

    Buy: allocation as quote_size (units match). Sell: allocation stuffed into
    base_size (units do NOT match — that is the mismatch we want to catch).
    product_id is the stripped ticker (BTC not BTC-USD).
    """
    from coinbase_order_body import build_market_order_body
    call = allocator_coinbase_call(allocation)
    args = call["args"]
    side = str(args.get("side") or "").lower()
    if side == "buy":
        return build_market_order_body(
            args["ticker"], side, usd_amount=args["allocation"],
            client_order_id=client_order_id,
        )
    return build_market_order_body(
        args["ticker"], side, crypto_amount=args["allocation"],
        client_order_id=client_order_id,
    )


def _ioc(post: dict) -> dict:
    return (post or {}).get("order_configuration", {}).get("market_market_ioc") or {}


def compare_intent_to_post(intent, post: dict) -> dict:
    """Structured diff: Intent fields vs coinbase_trader POST body."""
    mismatches = []
    ioc = _ioc(post)
    post_side = str((post or {}).get("side") or "").lower()
    intent_side = str(intent.side or "").lower()
    if post_side != intent_side:
        mismatches.append({"field": "side", "intent": intent.side, "post": (post or {}).get("side")})

    post_pid = (post or {}).get("product_id")
    if post_pid != intent.instrument:
        mismatches.append({"field": "instrument/product_id", "intent": intent.instrument, "post": post_pid})

    post_cid = (post or {}).get("client_order_id")
    if post_cid and post_cid != intent.client_order_id:
        mismatches.append({"field": "client_order_id", "intent": intent.client_order_id, "post": post_cid})

    if "quote_size" in ioc:
        post_n = float(ioc["quote_size"])
        if intent.notional is None:
            mismatches.append({"field": "size", "intent": f"qty={intent.qty}", "post": f"quote_size={post_n}"})
        elif abs(float(intent.notional) - post_n) > 1e-9:
            mismatches.append({"field": "notional/quote_size", "intent": intent.notional, "post": post_n})
    elif "base_size" in ioc:
        post_q = float(ioc["base_size"])
        if intent.qty is None:
            mismatches.append({"field": "size", "intent": f"notional={intent.notional}", "post": f"base_size={post_q}"})
        elif abs(float(intent.qty) - post_q) > 1e-9:
            mismatches.append({"field": "qty/base_size", "intent": intent.qty, "post": post_q})
    else:
        mismatches.append({"field": "order_configuration", "intent": intent.order_type, "post": (post or {}).get("order_configuration")})

    if intent.order_type != "market":
        mismatches.append({"field": "order_type", "intent": intent.order_type, "post": "market_market_ioc"})
    if intent.tif != "IOC":
        mismatches.append({"field": "tif", "intent": intent.tif, "post": "IOC (market_market_ioc)"})
    if intent.venue != "coinbase":
        mismatches.append({"field": "venue", "intent": intent.venue, "post": "coinbase"})

    return {
        "ok": not mismatches,
        "mismatches": mismatches,
        "intent_view": {
            "instrument": intent.instrument,
            "side": intent.side,
            "order_type": intent.order_type,
            "tif": intent.tif,
            "qty": intent.qty,
            "notional": intent.notional,
            "client_order_id": intent.client_order_id,
            "venue": intent.venue,
        },
        "post_view": {
            "product_id": post_pid,
            "side": (post or {}).get("side"),
            "ioc": ioc,
            "client_order_id": post_cid,
            "path": "POST /api/v3/brokerage/orders",
        },
    }


def _ere_level() -> str:
    try:
        p = os.path.join(_MODELS, "latest_etherion_risk.json")
        if os.path.exists(p):
            return str(json.load(open(p)).get("risk_level", "UNKNOWN")).upper() or "UNKNOWN"
    except Exception:
        pass
    return "UNKNOWN"


def shadow_gates(over: Optional[dict] = None, note: Optional[str] = None) -> dict:
    """Shadow never sets live_flag. Allocator Coinbase does not re-check fee/category;
    we snapshot ERE level and fail-closed on HALT/DANGER via ere_ok()."""
    g = {
        "ere_level": _ere_level(),
        "fee_ok": True,
        "category_ok": True,
        "blacklist_ok": True,
        "exec_dry_run": bool(getattr(vc, "EXEC_DRY_RUN", False)),
        "live_flag": False,
        "gate_note": note or "allocator_coinbase_does_not_recheck_fee_category",
    }
    if over:
        g.update(over)
    g["live_flag"] = False  # never live, even if caller tries
    return g


def intent_from_allocation(allocation: dict, gates: Optional[dict] = None):
    """Map allocator Coinbase allocation → Slice-1 Intent (schema, not a SubmitOrder).

    Buys: notional = allocation (matches trader quote_size).
    Sells: allocator only has USD allocation, not base qty — Intent keeps notional
    so the POST compare flags sell base_size vs notional.
    """
    a = allocation or {}
    notional = float(a.get("allocation") or 0)
    if notional <= 0:
        notional = 0.01  # schema requires >0; compare will still show allocator value
    instrument = coinbase_product_id(a.get("ticker") or "")
    side = str(a.get("side") or "buy").strip().lower()
    return make_intent(
        strategy=str(a.get("type") or "ALLOCATOR_COINBASE"),
        venue="coinbase",
        instrument=instrument,
        side=side,
        order_type="market",
        tif="IOC",
        notional=notional,
        qty=None,
        max_notional=notional,
        max_qty=notional,  # unpriced qty cap; compare flags sell unit mismatch
        gates=shadow_gates(gates),
        ledger_source="shadow",
    )


def emit_allocator_shadow(allocation: dict, queue_path: Optional[str] = None,
                          gates: Optional[dict] = None) -> dict:
    """Build, compare, enqueue. dispatch is hardcoded False. Never a venue POST."""
    if not getattr(vc, "NAUTILUS_SHADOW_COINBASE", True):
        return {"shadow": False, "skipped": True, "dispatchable": False}
    intent = intent_from_allocation(allocation, gates=gates)
    try:
        canonical = post_body_from_intent(intent)
        compare = compare_intent_to_post(intent, canonical)
    except Exception as e:
        canonical = None
        compare = {
            "ok": False,
            "error": str(e)[:160],
            "mismatches": [{"field": "post_body", "intent": intent.side, "post": str(e)[:80]}],
            "intent_view": {"instrument": intent.instrument, "side": intent.side,
                            "notional": intent.notional, "qty": intent.qty},
            "post_view": None,
        }
    try:
        alloc_post = post_body_from_allocator_call(allocation, intent.client_order_id)
        alloc_compare = compare_intent_to_post(intent, alloc_post)
    except Exception as e:
        alloc_post = None
        alloc_compare = {"ok": False, "error": str(e)[:120], "mismatches": []}
    extra = {
        "shadow": True,
        "compare": compare,
        "allocator_call": allocator_coinbase_call(allocation),
        "allocator_post_compare": alloc_compare,
        "allocator_post": alloc_post,
    }
    rec = enqueue_intent(intent, dispatch=False, queue_path=queue_path, extra=extra)
    rec["dispatchable"] = False
    rec["dispatch_requested"] = False
    rec["shadow"] = True
    print(
        f"[shadow-coinbase] intent={rec.get('intent_id','')} status={rec.get('status')} "
        f"dispatchable=false canonical_ok={compare.get('ok')} "
        f"allocator_mismatches={len(alloc_compare.get('mismatches') or [])}"
    )
    return rec


def intent_from_brain(side, ticker, usd_amount=None, crypto_amount=None,
                      gates: Optional[dict] = None, strategy: str = "UNIFIED_BRAIN"):
    """Map unified_brain Coinbase buy/sell → Slice-1 Intent.

    Buy: notional = usd_amount (trader quote_size). Sell: qty = crypto_amount
    (trader base_size). Never invents a missing size.
    """
    side_l = str(side or "").strip().lower()
    instrument = coinbase_product_id(ticker)
    g = shadow_gates(gates, note="unified_brain_coinbase_shadow")
    if side_l == "buy":
        notional = float(usd_amount)
        return make_intent(
            strategy=strategy, venue="coinbase", instrument=instrument, side="buy",
            order_type="market", tif="IOC", notional=notional, qty=None,
            max_notional=notional, max_qty=notional, gates=g, ledger_source="shadow",
        )
    qty = float(crypto_amount)
    return make_intent(
        strategy=strategy, venue="coinbase", instrument=instrument, side="sell",
        order_type="market", tif="IOC", qty=qty, notional=None,
        max_qty=qty, max_notional=max(qty, 0.01), gates=g, ledger_source="shadow",
    )


def emit_brain_shadow(side, ticker, usd_amount=None, crypto_amount=None,
                      queue_path: Optional[str] = None, gates: Optional[dict] = None,
                      strategy: str = "UNIFIED_BRAIN") -> dict:
    """Shadow the REAL Coinbase writer (unified_brain buy/sell). Never dispatchable."""
    if not getattr(vc, "NAUTILUS_SHADOW_COINBASE", True):
        return {"shadow": False, "skipped": True, "dispatchable": False, "writer": "unified_brain"}
    side_l = str(side or "").strip().lower()
    if side_l == "buy":
        try:
            if usd_amount is None or float(usd_amount) <= 0:
                return {"shadow": False, "skipped": True, "reason": "no_notional",
                        "dispatchable": False}
        except (TypeError, ValueError):
            return {"shadow": False, "skipped": True, "reason": "no_notional",
                    "dispatchable": False}
    elif side_l == "sell":
        try:
            if crypto_amount is None or float(crypto_amount) <= 0:
                return {"shadow": False, "skipped": True, "reason": "no_qty",
                        "dispatchable": False}
        except (TypeError, ValueError):
            return {"shadow": False, "skipped": True, "reason": "no_qty",
                    "dispatchable": False}
    else:
        return {"shadow": False, "skipped": True, "reason": "bad_side", "dispatchable": False}
    intent = intent_from_brain(
        side_l, ticker, usd_amount=usd_amount, crypto_amount=crypto_amount,
        gates=gates, strategy=strategy)
    try:
        canonical = post_body_from_intent(intent)
        compare = compare_intent_to_post(intent, canonical)
    except Exception as e:
        canonical = None
        compare = {
            "ok": False, "error": str(e)[:160],
            "mismatches": [{"field": "post_body", "intent": intent.side, "post": str(e)[:80]}],
        }
    extra = {
        "shadow": True,
        "writer": "unified_brain",
        "compare": compare,
        "canonical_post": canonical,
    }
    rec = enqueue_intent(intent, dispatch=False, queue_path=queue_path, extra=extra)
    rec["dispatchable"] = False
    rec["dispatch_requested"] = False
    rec["shadow"] = True
    rec["writer"] = "unified_brain"
    print(
        f"[shadow-brain-coinbase] intent={rec.get('intent_id','')} "
        f"side={side_l} dispatchable=false canonical_ok={compare.get('ok')}"
    )
    return rec
