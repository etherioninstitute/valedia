"""nautilus_shadow_venues.py — Slice 11: HL shorts + Polymarket arb shadows.

Never-dispatchable Slice-1 Intents. Writer stays valedia (HL/PM remain none).
Diffs Intent fields vs the intended venue POST body (stubs — no live client).

No nautilus_trader. No venue I/O. No live orders. No keys.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import json
import os
from typing import Optional

import valedia_config as vc
from hyperliquid_order_body import (
    LIVE_CLIENT_HOOK as HL_HOOK,
    build_perp_order_action,
    cloid_from_client_order_id,
    hl_coin,
    send_via_live_hook as hl_send_via_live_hook,
)
from nautilus_intent import enqueue_intent, make_intent
from polymarket_order_body import (
    LIVE_CLIENT_HOOK as PM_HOOK,
    build_clob_order_body,
    send_via_live_hook as pm_send_via_live_hook,
    shares_from_notional,
)

_MODELS = os.path.expanduser("~/.openclaw/workspace/models")


def _ere_level(models_dir=None) -> str:
    try:
        p = os.path.join(models_dir or _MODELS, "latest_etherion_risk.json")
        if os.path.exists(p):
            return str(json.load(open(p)).get("risk_level", "UNKNOWN")).upper() or "UNKNOWN"
    except Exception:
        pass
    return "UNKNOWN"


def shadow_gates(over: Optional[dict] = None, note: Optional[str] = None,
                 models_dir=None) -> dict:
    g = {
        "ere_level": _ere_level(models_dir),
        "fee_ok": True,
        "category_ok": True,
        "blacklist_ok": True,
        "exec_dry_run": bool(getattr(vc, "EXEC_DRY_RUN", False)),
        "live_flag": False,
        "gate_note": note or "slice11_venue_expansion_shadow",
    }
    if over:
        g.update(over)
    g["live_flag"] = False
    return g


def _load_json(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f) or {}
    except Exception:
        return {}


# ── Hyperliquid shorts ────────────────────────────────────────────────

def intent_from_hl_short(candidate: dict, gates: Optional[dict] = None):
    """Map a short/flatten candidate → Slice-1 Intent. Never invents size.

    Shorts are side=sell (is_buy=False). Flatten uses reduce_only=True.
    HL market-style needs qty (base) + bound_px; notional-only is refused.
    """
    c = candidate or {}
    coin = hl_coin(c.get("coin") or c.get("instrument") or c.get("ticker") or "")
    qty = float(c["qty"])
    g = shadow_gates(gates, note="hl_short_never_dispatchable")
    return make_intent(
        strategy=str(c.get("strategy") or "HL_SHORT"),
        venue="hyperliquid",
        instrument=coin,
        side="sell" if str(c.get("side") or "sell").strip().lower() != "buy" else "buy",
        order_type="market",
        tif="IOC",
        qty=qty,
        notional=None,
        max_qty=qty,
        max_notional=max(float(c.get("notional") or 0), qty, 0.01),
        gates=g,
        ledger_source="shadow",
    )


def post_body_from_hl_intent(intent, bound_px, reduce_only=False, leverage=0):
    return build_perp_order_action(
        intent.instrument,
        is_buy=(intent.side == "buy"),
        sz=intent.qty,
        bound_px=bound_px,
        tif=intent.tif,
        reduce_only=reduce_only,
        cloid=intent.client_order_id,
        leverage=leverage,
    )


def compare_intent_to_hl_post(intent, post: dict, reduce_only=False) -> dict:
    """Structured diff: Intent vs intended HL /exchange action."""
    mismatches = []
    orders = ((post or {}).get("action") or {}).get("orders") or []
    o = orders[0] if orders else {}
    want_buy = intent.side == "buy"
    if bool(o.get("b")) != want_buy:
        mismatches.append({"field": "side/is_buy", "intent": intent.side, "post": o.get("b")})
    post_coin = o.get("a")
    if post_coin != intent.instrument:
        mismatches.append({"field": "instrument/coin", "intent": intent.instrument, "post": post_coin})
    try:
        post_sz = float(o.get("s"))
        if intent.qty is None or abs(float(intent.qty) - post_sz) > 1e-12:
            mismatches.append({"field": "qty/s", "intent": intent.qty, "post": o.get("s")})
    except (TypeError, ValueError):
        mismatches.append({"field": "qty/s", "intent": intent.qty, "post": o.get("s")})
    want_cloid = cloid_from_client_order_id(intent.client_order_id)
    if o.get("c") and o.get("c") != want_cloid:
        mismatches.append({"field": "cloid", "intent": want_cloid, "post": o.get("c")})
    tif_hl = ((o.get("t") or {}).get("limit") or {}).get("tif")
    if intent.tif == "IOC" and tif_hl != "Ioc":
        mismatches.append({"field": "tif", "intent": intent.tif, "post": tif_hl})
    if bool(o.get("r")) != bool(reduce_only):
        mismatches.append({"field": "reduce_only", "intent": reduce_only, "post": o.get("r")})
    if (post or {}).get("leverage") not in (0, 0.0):
        mismatches.append({"field": "leverage", "intent": 0, "post": (post or {}).get("leverage")})
    if intent.venue != "hyperliquid":
        mismatches.append({"field": "venue", "intent": intent.venue, "post": "hyperliquid"})
    if intent.order_type != "market":
        mismatches.append({"field": "order_type", "intent": intent.order_type, "post": "Ioc limit"})
    if (post or {}).get("signed") is True:
        mismatches.append({"field": "signed", "intent": False, "post": True})
    if (post or {}).get("live_client_hook") is not None:
        mismatches.append({"field": "live_client_hook", "intent": None,
                           "post": (post or {}).get("live_client_hook")})
    return {
        "ok": not mismatches,
        "mismatches": mismatches,
        "intent_view": {
            "instrument": intent.instrument, "side": intent.side,
            "qty": intent.qty, "tif": intent.tif, "venue": intent.venue,
            "client_order_id": intent.client_order_id,
        },
        "post_view": {"path": (post or {}).get("path"), "order": o,
                      "leverage": (post or {}).get("leverage"), "signed": (post or {}).get("signed")},
    }


def emit_hl_short_shadow(candidate: dict, queue_path: Optional[str] = None,
                         gates: Optional[dict] = None) -> dict:
    """Build, compare, enqueue a Hyperliquid short. Never dispatchable. Never POST."""
    if not getattr(vc, "NAUTILUS_SHADOW_VENUES", True):
        return {"shadow": False, "skipped": True, "dispatchable": False, "writer": "none"}
    c = candidate or {}
    try:
        qty = float(c.get("qty"))
        if qty <= 0:
            return {"shadow": False, "skipped": True, "reason": "no_qty", "dispatchable": False}
    except (TypeError, ValueError):
        return {"shadow": False, "skipped": True, "reason": "no_qty", "dispatchable": False}
    try:
        bound_px = float(c.get("bound_px") if c.get("bound_px") is not None else c.get("price"))
        if bound_px <= 0:
            return {"shadow": False, "skipped": True, "reason": "no_bound_px", "dispatchable": False}
    except (TypeError, ValueError):
        return {"shadow": False, "skipped": True, "reason": "no_bound_px", "dispatchable": False}
    if not hl_coin(c.get("coin") or c.get("instrument") or c.get("ticker") or ""):
        return {"shadow": False, "skipped": True, "reason": "no_coin", "dispatchable": False}
    reduce_only = bool(c.get("reduce_only"))
    intent = intent_from_hl_short(c, gates=gates)
    try:
        canonical = post_body_from_hl_intent(intent, bound_px, reduce_only=reduce_only, leverage=0)
        compare = compare_intent_to_hl_post(intent, canonical, reduce_only=reduce_only)
    except Exception as e:
        canonical = None
        compare = {"ok": False, "error": str(e)[:160], "mismatches": [
            {"field": "post_body", "intent": intent.side, "post": str(e)[:80]}]}
    extra = {
        "shadow": True,
        "writer": "none",
        "venue_writer": vc.venue_writer("hyperliquid"),
        "compare": compare,
        "canonical_post": canonical,
        "reduce_only": reduce_only,
        "leverage": 0,
        "live_client_hook": HL_HOOK,
        "hl_send": hl_send_via_live_hook(canonical),
        "kind": "hl_short",
    }
    rec = enqueue_intent(intent, dispatch=False, queue_path=queue_path, extra=extra)
    rec["dispatchable"] = False
    rec["dispatch_requested"] = False
    rec["shadow"] = True
    rec["kind"] = "hl_short"
    print(
        f"[shadow-hl-short] intent={rec.get('intent_id','')} "
        f"dispatchable=false canonical_ok={compare.get('ok')} hook={HL_HOOK}"
    )
    return rec


# ── Polymarket arb ────────────────────────────────────────────────────

def intent_from_pm_arb(candidate: dict, gates: Optional[dict] = None):
    """Map a Polymarket arb-leg candidate → Slice-1 Intent.

    Uses share qty when price is known (CLOB size). Never invents token_id.
    """
    c = candidate or {}
    token_id = str(c.get("token_id") or c.get("polymarket_token_id") or c.get("instrument") or "").strip()
    price = c.get("price")
    size = c.get("size")
    notional = c.get("notional")
    if size is None and notional is not None and price not in (None, ""):
        size = shares_from_notional(notional, price)
    g = shadow_gates(gates, note="polymarket_arb_never_dispatchable")
    side = str(c.get("side") or "buy").strip().lower()
    if side not in ("buy", "sell"):
        side = "buy"
    qty = float(size)
    max_n = float(notional) if notional not in (None, "") else max(qty * float(price or 1), 0.01)
    if max_n <= 0:
        max_n = max(qty, 0.01)
    kw = dict(
        strategy=str(c.get("strategy") or "POLYMARKET_ARB"),
        venue="polymarket",
        instrument=token_id,
        side=side,
        order_type="market",
        tif="FOK",
        qty=qty,
        notional=None,
        max_qty=qty,
        max_notional=max_n,
        gates=g,
        ledger_source="shadow",
    )
    if price not in (None, ""):
        kw["limit_price"] = float(price)
    return make_intent(**kw)


def post_body_from_pm_intent(intent):
    if intent.limit_price is None:
        raise ValueError("Polymarket CLOB body needs price")
    return build_clob_order_body(
        intent.instrument,
        intent.side,
        price=intent.limit_price,
        size=intent.qty,
        order_type=intent.tif or "FOK",
        client_order_id=intent.client_order_id,
    )


def compare_intent_to_pm_post(intent, post: dict) -> dict:
    mismatches = []
    if (post or {}).get("tokenID") != intent.instrument:
        mismatches.append({"field": "instrument/tokenID", "intent": intent.instrument,
                           "post": (post or {}).get("tokenID")})
    post_side = str((post or {}).get("side") or "").lower()
    if post_side != str(intent.side or "").lower():
        mismatches.append({"field": "side", "intent": intent.side, "post": (post or {}).get("side")})
    try:
        post_sz = float((post or {}).get("size"))
        if intent.qty is None or abs(float(intent.qty) - post_sz) > 1e-12:
            mismatches.append({"field": "qty/size", "intent": intent.qty, "post": (post or {}).get("size")})
    except (TypeError, ValueError):
        mismatches.append({"field": "qty/size", "intent": intent.qty, "post": (post or {}).get("size")})
    if intent.limit_price is not None:
        try:
            if abs(float(intent.limit_price) - float((post or {}).get("price"))) > 1e-6:
                mismatches.append({"field": "price", "intent": intent.limit_price,
                                   "post": (post or {}).get("price")})
        except (TypeError, ValueError):
            mismatches.append({"field": "price", "intent": intent.limit_price,
                               "post": (post or {}).get("price")})
    if (post or {}).get("orderType") != "FOK" and intent.tif == "FOK":
        mismatches.append({"field": "tif/orderType", "intent": intent.tif,
                           "post": (post or {}).get("orderType")})
    if (post or {}).get("client_order_id") and (post or {}).get("client_order_id") != intent.client_order_id:
        mismatches.append({"field": "client_order_id", "intent": intent.client_order_id,
                           "post": (post or {}).get("client_order_id")})
    if intent.venue != "polymarket":
        mismatches.append({"field": "venue", "intent": intent.venue, "post": "polymarket"})
    if (post or {}).get("signed") is True:
        mismatches.append({"field": "signed", "intent": False, "post": True})
    if (post or {}).get("live_client_hook") is not None:
        mismatches.append({"field": "live_client_hook", "intent": None,
                           "post": (post or {}).get("live_client_hook")})
    return {
        "ok": not mismatches,
        "mismatches": mismatches,
        "intent_view": {
            "instrument": intent.instrument, "side": intent.side, "qty": intent.qty,
            "tif": intent.tif, "limit_price": intent.limit_price, "venue": intent.venue,
        },
        "post_view": {
            "path": (post or {}).get("path"), "tokenID": (post or {}).get("tokenID"),
            "side": (post or {}).get("side"), "size": (post or {}).get("size"),
            "price": (post or {}).get("price"), "orderType": (post or {}).get("orderType"),
            "signed": (post or {}).get("signed"),
        },
    }


def emit_polymarket_arb_shadow(candidate: dict, queue_path: Optional[str] = None,
                               gates: Optional[dict] = None) -> dict:
    """Build, compare, enqueue a Polymarket arb-leg Intent. Never dispatchable."""
    if not getattr(vc, "NAUTILUS_SHADOW_VENUES", True):
        return {"shadow": False, "skipped": True, "dispatchable": False, "writer": "none"}
    c = candidate or {}
    token_id = str(c.get("token_id") or c.get("polymarket_token_id") or c.get("instrument") or "").strip()
    if not token_id:
        return {"shadow": False, "skipped": True, "reason": "no_token_id", "dispatchable": False}
    try:
        price = float(c.get("price"))
        if price <= 0:
            return {"shadow": False, "skipped": True, "reason": "no_price", "dispatchable": False}
    except (TypeError, ValueError):
        return {"shadow": False, "skipped": True, "reason": "no_price", "dispatchable": False}
    size = c.get("size")
    notional = c.get("notional")
    if size is None and notional is not None:
        try:
            size = shares_from_notional(notional, price)
        except (TypeError, ValueError):
            size = None
    try:
        if size is None or float(size) <= 0:
            return {"shadow": False, "skipped": True, "reason": "no_size", "dispatchable": False}
    except (TypeError, ValueError):
        return {"shadow": False, "skipped": True, "reason": "no_size", "dispatchable": False}
    payload = dict(c)
    payload["token_id"] = token_id
    payload["price"] = price
    payload["size"] = float(size)
    intent = intent_from_pm_arb(payload, gates=gates)
    try:
        canonical = post_body_from_pm_intent(intent)
        compare = compare_intent_to_pm_post(intent, canonical)
    except Exception as e:
        canonical = None
        compare = {"ok": False, "error": str(e)[:160], "mismatches": [
            {"field": "post_body", "intent": intent.side, "post": str(e)[:80]}]}
    extra = {
        "shadow": True,
        "writer": "none",
        "venue_writer": vc.venue_writer("polymarket"),
        "compare": compare,
        "canonical_post": canonical,
        "arb_candidate": True,
        "kalshi_ticker": c.get("kalshi_ticker") or c.get("ticker"),
        "live_client_hook": PM_HOOK,
        "pm_send": pm_send_via_live_hook(canonical),
        "kind": "polymarket_arb",
    }
    rec = enqueue_intent(intent, dispatch=False, queue_path=queue_path, extra=extra)
    rec["dispatchable"] = False
    rec["dispatch_requested"] = False
    rec["shadow"] = True
    rec["kind"] = "polymarket_arb"
    rec["arb_candidate"] = True
    print(
        f"[shadow-pm-arb] intent={rec.get('intent_id','')} "
        f"dispatchable=false canonical_ok={compare.get('ok')} hook={PM_HOOK}"
    )
    return rec


def polymarket_candidates_from_arb_log(doc) -> list:
    """Only rows with a Polymarket token id. Kalshi-only arb stays Slice 10."""
    if isinstance(doc, list):
        trades = doc
    else:
        trades = (doc or {}).get("trades") or (doc or {}).get("candidates") or []
    out = []
    for t in trades:
        if not isinstance(t, dict):
            continue
        token = t.get("polymarket_token_id") or t.get("poly_token_id") or t.get("token_id")
        if not token:
            continue
        # Kalshi tickers are not CLOB token ids.
        if str(token).startswith("KX"):
            continue
        out.append({
            "token_id": token,
            "side": t.get("poly_side") or t.get("side") or "buy",
            "price": t.get("poly_price") or t.get("price"),
            "notional": t.get("notional") or t.get("size_usd") or 5.0,
            "size": t.get("size") or t.get("poly_size"),
            "kalshi_ticker": t.get("ticker") or t.get("kalshi_ticker"),
            "strategy": t.get("strategy") or "POLYMARKET_ARB",
        })
    return out


def emit_venue_expansion_shadows(hl_candidates=None, pm_candidates=None,
                                 models_dir=None, queue_path=None,
                                 gates=None):
    """Batch emit. Default: local JSON only (no HTTP). Returns list of recs."""
    if not getattr(vc, "NAUTILUS_SHADOW_VENUES", True):
        return []
    md = models_dir or _MODELS
    if hl_candidates is None:
        raw = _load_json(os.path.join(md, "hl_short_candidates.json"))
        hl_candidates = raw.get("candidates") if isinstance(raw, dict) else raw
        if not isinstance(hl_candidates, list):
            hl_candidates = []
    if pm_candidates is None:
        pm_candidates = polymarket_candidates_from_arb_log(
            _load_json(os.path.join(md, "latency_arb_log.json")))
    recs = []
    for c in hl_candidates or []:
        recs.append(emit_hl_short_shadow(c, queue_path=queue_path, gates=gates))
    for c in pm_candidates or []:
        recs.append(emit_polymarket_arb_shadow(c, queue_path=queue_path, gates=gates))
    return recs
