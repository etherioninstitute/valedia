"""Latency arb evidence + pre-create chain (moved out of latency_arb.py, 500-line rule).

Behavior unchanged: latency_arb re-exports these names. Pure helpers; no orders here.
Chain (master_allocator discipline): kalshi_profit EV preflight -> session_clocks.before_create
-> informed create_preflight -> Nimble 2 last gate (nimble_wiring.gate; shadow default, veto
only removes). The caller places the order only when arb_pre_create returns a preflight.
"""


def heuristic_fair_value(distance_pct):
    """Rough strike-distance fair value (probability our side wins). NOT a model."""
    return max(0.5, 1.0 - distance_pct / 5)


ARB_EVIDENCE_VERSION = "latency-arb-evidence-1"
# Honest label for the only FV latency_arb has: a hand-coded strike-distance estimate.
# It is not model / rules_clock / researched, so informed_before_create refuses it
# ("fv_source") until a real FV exists. Never relabel it to pass the gate.
ARB_FV_SOURCE = "distance_heuristic"


def _arb_skip(result, status, reasons):
    result["status"] = status
    result["reasons"] = list(reasons)
    result["error"] = ",".join(str(x) for x in reasons)[:160]


def build_arb_evidence(contract, n, price_cents):
    """(opp, evidence) from the signal + top-of-book. Absent evidence stays absent."""
    sig = contract.get("signal") if isinstance(contract.get("signal"), dict) else {}
    side = contract["side"]
    ya, yb = float(contract["yes_ask"]), float(contract["yes_bid"])
    bid, ask = (yb, ya) if side == "yes" else (round(1.0 - ya, 4), round(1.0 - yb, 4))
    fv = contract.get("fair_value")
    if fv is None and contract.get("distance_pct") is not None:
        fv = round(heuristic_fair_value(float(contract["distance_pct"])), 4)
    price = price_cents / 100.0
    edge = round(fv - price, 4) if fv is not None else None
    ev = {
        "version": ARB_EVIDENCE_VERSION, "ticker": contract["ticker"], "side": side,
        "fv": fv, "fv_source": ARB_FV_SOURCE, "fv_confidence": None,
        "fv_method": "max(0.5, 1 - strike_distance_pct/5); rough heuristic, not a model",
        "edge": edge, "edge_cents": contract.get("edge_cents"),
        "bid": bid, "ask": ask, "spread": contract.get("spread"),
        "depth": contract.get("depth"), "bankroll": contract.get("bankroll"),
        "slip": 0.0, "slip_basis": "limit_at_best_ask",
        "strike": contract.get("strike"), "distance_pct": contract.get("distance_pct"),
        "signal": {k: sig.get(k) for k in ("pair", "direction", "delta_pct", "spot_price",
                                           "confidence", "timestamp")},
        "order_price_cents": price_cents, "contracts": n,
        "gaps": [k for k, v in (("fv_confidence", None), ("depth", contract.get("depth")),
                                ("bankroll", contract.get("bankroll"))) if not v],
    }
    opp = {
        "platform": "kalshi", "type": "LATENCY_ARB", "strategy": "LATENCY_ARB",
        "desk": "latency_arb", "ticker": contract["ticker"], "side": side, "price": price,
        "fv": fv, "fv_source": ARB_FV_SOURCE, "edge": edge, "slip": 0.0,
        "bid": bid, "ask": ask, "yes_bid": yb, "yes_ask": ya,
        "no_bid": round(1.0 - ya, 4), "no_ask": round(1.0 - yb, 4),
        "depth": ev["depth"], "bankroll": ev["bankroll"],
    }
    return {k: v for k, v in opp.items() if v is not None}, ev


def arb_pre_create(contract, n, price_cents, result):
    """Allocator pre-create discipline (master_allocator.execute_allocations):
    EV preflight -> session_clocks.before_create -> informed create_preflight.
    Returns the preflight dict on admit, else None with result marked SKIP_*. Never raises."""
    try:
        opp, evidence = build_arb_evidence(contract, n, price_cents)
        result["evidence"], result["evidence_version"] = evidence, ARB_EVIDENCE_VERSION
    except Exception as e:
        _arb_skip(result, "SKIP_EVIDENCE", [f"evidence_error:{type(e).__name__}"])
        return None
    try:
        import kalshi_profit as _kp
        proceed, opp = _kp.annotate(opp, contracts=n)
        result["ev_preflight"] = opp.get("ev_preflight")
        if not proceed:
            _arb_skip(result, "SKIP_EV_PREFLIGHT", ["ev_preflight"])
            return None
        mf = opp.get("maker_first") or {}
        # No maker manager on this lane: a maker-preferred book is skipped, never upgraded.
        if _kp.maker_first_on() and mf.get("prefer") == "maker" and _kp.should_skip_taker(mf):
            _arb_skip(result, "SKIP_MAKER_FIRST", ["maker_first"])
            return None
    except Exception as e:
        _arb_skip(result, "SKIP_EV_PREFLIGHT", [f"ev preflight failed closed: {str(e)[:60]}"])
        return None
    try:
        import session_clocks as _sc
        blocked = _sc.before_create(opp)
        if blocked:
            _arb_skip(result, blocked[0], [blocked[1]])
            return None
        import informed_before_create as _ibc
        pre = _ibc.create_preflight(opp, n, price_cents)
        if not pre["ok"]:
            _arb_skip(result, "SKIP_INFORMED", pre["reasons"])
            return None
        # Nimble 2: last gate, only for candidates the code gates admitted. Shadow = stamp only;
        # veto can only remove this entry.
        import nimble_wiring as _nw
        result["nimble"] = nv = _nw.gate(
            opp, pre["informed"], source="latency_arb", extra={"clock_ok": True})
        if not nv["allow"]:
            _arb_skip(result, "SKIP_NIMBLE", [_nw.why(nv)])
            return None
        return pre
    except Exception as e:
        _arb_skip(result, "SKIP_PRECREATE_ERROR", [f"{type(e).__name__}: {str(e)[:80]}"])
        return None
