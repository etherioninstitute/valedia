#!/usr/bin/env python3
"""Prediction-market wiring for the Nimble 2 decision layer (nimble_decide).

Every PREDICTION-MARKET decision point (master_allocator live Kalshi,
sports_evidence, weather_live, poly_loop paper, vaughan_arb_paper) calls
`gate(...)` at the LAST step before an order / paper fill, after every code
gate has passed, and stamps the verdict on its result row (`row["nimble"]`).
A blocked verdict (`allow` False) means: status SKIP_NIMBLE, no order.

Mode (VALEDIA_NIMBLE_MODE_PREDICTION, default shadow):
- shadow: log only, allow ALWAYS (an unexpected error also allows, with the error logged).
- veto:   only the model saying buy at >= VALEDIA_NIMBLE_MIN_CONF allows. An
          engine that is off / down / erroring, or an unexpected error here, blocks.
- off:    no call, allow.
A call happens only when VALEDIA_SYSTEMONE=1. Veto can only REMOVE an entry; it
never adds, sizes, flips or reprices. Exits are never blocked (nimble_decide).

kalshi_executor.py is legacy/inert and is deliberately NOT wired.
This module never places an order and never raises.
"""
from __future__ import annotations

import os

_ASSET = "prediction"


def _mode():
    try:
        import nimble_decide as nd
        return nd.mode_for(_ASSET)
    except Exception:
        return "shadow"


def _fallback(mode, exc):
    """Wiring failed unexpectedly: shadow allows (error logged in the stamp), veto blocks."""
    err = "wiring_" + type(exc).__name__
    return {"action": None, "confidence": None, "risk": None, "mode": mode,
            "allow": mode != "veto", "reason": ("veto_" if mode == "veto" else "shadow_") + err,
            "latency_ms": None, "within_budget": False, "error": err}


def warm_for_run(allocations=None, dry_run=False):
    """Warm the engine once per run, only when a call can happen. Never raises."""
    try:
        if dry_run or not allocations:
            return None
        import nimble_decide as nd
        if nd.mode_for(_ASSET) == "off":
            return None
        return nd.warm()
    except Exception:
        return None


def direction_for(opp, side=None):
    """Underlying direction ('up'/'down') for informed facts, or None.

    Documented rule: a yes/no contract side has no underlying direction by itself,
    so only an explicit `direction` / `underlying_direction` on the opportunity
    (value up/down) is used; buy/long/sell/short sides map through informed_facts.
    Anything else is None (the informed conflict facts then stay False).
    """
    o = opp if isinstance(opp, dict) else {}
    for k in ("direction", "underlying_direction"):
        d = str(o.get(k) or "").strip().lower()
        if d in ("up", "down"):
            return d
    return None


def _symbol(opp):
    for k in ("underlying", "symbol", "ticker", "slug", "market_id"):
        v = opp.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _ev_net(opp):
    ev = opp.get("ev_preflight")
    net = ev.get("net") if isinstance(ev, dict) else None
    return net if isinstance(net, (int, float)) and not isinstance(net, bool) else None


def gate(opp, informed=None, *, source, side=None, poly=False, label=None, news=None,
         extra=None, post=None, get=None):
    """Verdict dict (see nimble_decide.decide_candidate). Never raises.

    opp: the opportunity the code already admitted (allocator opp with `raw`, or a
    Poly row). informed: create_preflight's `informed` dict (merged into the code
    facts). extra: facts the caller's own gate has just established (e.g. clock_ok
    after session_clocks.before_create, fee_true_edge). Offline fact building is
    skipped when no call can happen.
    """
    mode = _mode()
    try:
        import nimble_decide as nd
        import systemone_hotpath_client as s1
        opp = opp if isinstance(opp, dict) else {}
        side = str(side or opp.get("side") or "").strip().lower() or None
        cand = {"ticker": _symbol(opp), "venue": opp.get("platform") or opp.get("desk"),
                "side": side}
        if mode == "off" or not s1.enabled():  # no call possible: skip building facts
            return nd.decide_candidate(_ASSET, cand, side=side, code_facts={}, informed={},
                                       source=source, label=label, post=post, get=get)
        merged = dict(opp)
        if isinstance(informed, dict):
            merged["informed"] = informed
        if _ev_net(opp) is not None and merged.get("fee_true_edge") is None:
            merged["fee_true_edge"] = _ev_net(opp)  # kalshi_profit.annotate fee-true net
        for k, v in (extra or {}).items():  # facts the caller's own gate just established
            if v is not None:
                merged[k] = v
        facts = nd.prediction_facts(merged, poly=poly)
        import informed_facts as inf
        inf_facts = inf.informed_facts(_symbol(opp), side, direction=direction_for(opp, side),
                                       news=news)
        code_label = label if label is not None else {"status": "admitted_by_code", "source": source}
        return nd.decide_candidate(_ASSET, cand, side=side, code_facts=facts, informed=inf_facts,
                                   source=source, label=code_label, post=post, get=get)
    except Exception as e:
        return _fallback(mode, e)


def why(verdict, prefix="nimble"):
    """Short reason string for a blocked row."""
    v = verdict if isinstance(verdict, dict) else {}
    return "%s:%s" % (prefix, v.get("reason") or "blocked")


def poly_gate(row, *, official=None, post=None, get=None):
    """Poly paper row → verdict. Adds the facts the row implies (poly FV allow list)."""
    try:
        import informed_before_create as ibc
        opp = dict(row)
        opp["ticker"] = row.get("slug")
        opp["edge"] = row.get("edge_pts")
        fee_true = ibc.edge_after_fees_pts(row.get("fv"), row.get("mid"), row.get("fee_prob"))
        if fee_true is not None:
            opp["fee_true_edge"] = fee_true
        if official is not None:
            opp["official_resolution"] = bool(official)
        # buy/sell on a Poly row is a paper direction, not a yes/no side, and a "sell"
        # would read as an exit (never sent): pass no side at all.
        opp.pop("side", None)
        return gate(opp, None, source="poly_loop", side=None, poly=True,
                    label={"status": row.get("status"), "reason": row.get("reason")},
                    post=post, get=get)
    except Exception as e:
        return _fallback(_mode(), e)


def vaughan_gate(cand, quote, *, post=None, get=None):
    """Cross-venue pair candidate (already through triple gate / risk / Ornith) → verdict."""
    try:
        q = quote if isinstance(quote, dict) else {}
        opp = {"ticker": "%s|%s" % (cand.get("poly_slug") or "", cand.get("kalshi_ticker") or ""),
               "platform": "poly+kalshi", "edge": q.get("edge_pct"),
               "fee_true_edge": q.get("edge_pct")}
        return gate(opp, None, source="vaughan_arb_paper", side=None,
                    label={"status": "ornith_pass", "edge_pct": q.get("edge_pct")},
                    post=post, get=get)
    except Exception as e:
        return _fallback(_mode(), e)
