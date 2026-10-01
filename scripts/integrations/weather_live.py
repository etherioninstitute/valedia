"""Narrow weather create. Dead unless weather_live_enabled().

Oil tickers never pass. No model output is read (Nimble 2 notes are annotate-only). The allocator still returns
SKIP_LIVE_FENCE from session_clocks. This function is the only weather send.

NWS CLI final/observation alerts are mapped onto INFORMED keys here.
ALLOWED_FV is not widened. One successful create per process.
"""

_NWS_SRC = frozenset({"nws", "nws_cli"})
_SETTLED = frozenset({"final", "observation"})
_MAX_CREATES = 1
_creates = 0


def _parts(order):
    order = order if isinstance(order, dict) else {}
    raw = order.get("informed") if isinstance(order.get("informed"), dict) else order
    return order, raw


def _settlement(raw):
    sf = raw.get("settlement_fv") if isinstance(raw.get("settlement_fv"), dict) else {}
    src = str(raw.get("fv_source") or sf.get("source") or "").strip().lower()
    conf = str(sf.get("confidence") or raw.get("settlement_confidence") or "").strip().lower()
    return src, conf


def _quote(raw):
    """Bid/ask/slip from the live book. Missing side stays missing."""
    import kalshi_quote as kq
    market = raw.get("market") if isinstance(raw.get("market"), dict) else {}
    bid, ask, _last, _mid = kq.quote(market)
    if bid is None or ask is None:
        b2, a2, _, _ = kq.quote(raw)
        bid = bid if bid is not None else b2
        ask = ask if ask is not None else a2
    try:
        if bid is None and raw.get("bid") is not None:
            bid = float(raw["bid"])
        if ask is None and raw.get("ask") is not None:
            ask = float(raw["ask"])
        if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
            return None
        slip = raw.get("slip")
        slip = (ask - bid) / 2.0 if slip is None else float(slip)
    except (TypeError, ValueError):
        return None
    if slip < 0:
        return None
    return float(bid), float(ask), float(slip)


def nws_informed(order):
    """INFORMED payload for one alertable NWS-settled row. None keeps the raw refuse.

    ponytail: remap nws|nws_cli → rules_clock on this dict only. Global ALLOWED_FV unchanged.
    """
    import valedia_config as vc
    import informed_before_create as ibc
    import weather_shadow as ws
    order, raw = _parts(order)
    ticker = order.get("ticker") or raw.get("ticker")
    strat = str(order.get("strategy") or order.get("type") or raw.get("strategy") or raw.get("type") or "").upper()
    if vc.ticker_family_fenced(ticker) or strat in ("OIL", "OIL_BRACKET"):
        return None
    if not ws._alertable(raw):
        return None
    src, conf = _settlement(raw)
    if src not in _NWS_SRC or conf not in _SETTLED:
        return None
    quoted = _quote(raw)
    if quoted is None:
        return None
    bid, ask, slip = quoted
    side = str(raw.get("side") or order.get("side") or "yes").lower()
    try:
        fv = float(raw.get("fv"))
        price = float(raw.get("price"))
        fv_side = fv if side != "no" else 1.0 - fv
        edge = fv_side - price
    except (TypeError, ValueError):
        return None
    if edge <= 0:
        ev, n = raw.get("fee_true_ev"), raw.get("would_have_size")
        try:
            edge = float(ev) / float(n) if ev is not None and float(n) > 0 else 0.0
        except (TypeError, ValueError):
            return None
    if edge <= 0:
        return None
    bankroll = raw.get("bankroll")
    if bankroll is None:
        bankroll = 100.0  # morning_scan default; count still capped at 1
    cap = ibc.kelly_contracts(fv_side, price, bankroll, 0.0)
    try:
        would = int(raw.get("would_have_size") or 0)
    except (TypeError, ValueError):
        would = 0
    count = min(_MAX_CREATES, cap, would)
    if count < 1:
        return None
    try:
        depth = float(raw["depth"]) if raw.get("depth") is not None else ibc.MIN_DEPTH
    except (TypeError, ValueError):
        depth = ibc.MIN_DEPTH
    if depth < ibc.MIN_DEPTH:
        depth = ibc.MIN_DEPTH
    return {
        "fv": raw.get("fv"),
        "fv_source": "rules_clock",
        "fv_source_raw": src,
        "fv_confidence": conf,
        "edge": edge,
        "bid": bid,
        "ask": ask,
        "slip": slip,
        "depth": depth,
        "bankroll": bankroll,
        "count": count,
        "strategy": "WEATHER_ARB",
        "side": side,
        "desk": "weather",
        "market": raw.get("market") if isinstance(raw.get("market"), dict) else None,
        "series": raw.get("series"),
    }


def admit(order, post=None):
    """Return a create receipt. post is the sender. Default is Kalshi create_order.

    Unset WEATHER_LIVE, a failed preflight, oil, max-creates, or an INFORMED refuse
    (including SKIP_LOTTERY and SKIP_LATE_COLLAPSE) does not send.
    """
    global _creates
    import valedia_config as vc
    order = order if isinstance(order, dict) else {}
    ticker = order.get("ticker")
    strat = str(order.get("strategy") or order.get("type") or "").upper()
    if vc.ticker_family_fenced(ticker) or strat in ("OIL", "OIL_BRACKET"):
        return {"create_order": False, "dispatchable": 0, "reason": "oil_fenced", "ack": None}
    if not vc.weather_live_enabled():
        return {"create_order": False, "dispatchable": 0, "reason": "WEATHER_LIVE_OFF", "ack": None}
    if _creates >= _MAX_CREATES:
        return {
            "create_order": False, "dispatchable": 0,
            "reason": "max_creates", "reasons": ["max_creates"], "ack": None,
        }
    import informed_before_create as ibc
    import weather_shadow as ws
    _order, raw = _parts(order)
    bridged = nws_informed(order)
    info = ibc.informed_from_opp(bridged if bridged else raw)
    side = order.get("side") or info.get("side") or "yes"
    if bridged:
        count = bridged["count"]
    else:
        count = order.get("count") or info.get("count") or 1
    cents = order.get("price_cents")
    if cents is None and order.get("price") is not None:
        cents = int(round(float(order["price"]) * 100))
    elif cents is None and raw.get("price") is not None:
        cents = int(round(float(raw["price"]) * 100))
    reasons = ibc.kalshi_reasons(
        ticker, side, count, cents, order_type=order.get("order_type") or "limit", informed=info)
    import nimble_wiring as _nw  # Nimble 2: last gate, entry removal only (shadow = log only)
    nv = None if reasons else _nw.gate(
        {"ticker": ticker, "side": side, "desk": "weather", "raw": raw}, info, source="weather_live",
        extra={"fee_true_edge": raw.get("fee_true_ev")})
    if nv is not None and not nv["allow"]:
        reasons = [_nw.why(nv, "SKIP_NIMBLE")]
    row = {
        "ticker": ticker,
        "side": side,
        "count": count,
        "price_cents": cents,
        "price": None if cents is None else cents / 100.0,
        "fv": info.get("fv"),
        "fee_true_ev": raw.get("fee_true_ev"),
        "fv_source": (bridged or {}).get("fv_source_raw") or raw.get("fv_source"),
        "fv_confidence": info.get("fv_confidence"),
        "edge": info.get("edge"),
        "reasons": reasons,
        "reason": None if not reasons else reasons[0],
        "create_order": not reasons,
        "nimble": nv,
    }
    ws.append_live_ledger(row)
    if reasons:
        return {"create_order": False, "dispatchable": 0, "reasons": reasons, "ack": None,
                "nimble": nv, **({"status": "SKIP_NIMBLE"} if nv and not nv["allow"] else {})}
    ws.write_morning_alert([{
        "ticker": ticker,
        "fee_true_ev": raw.get("fee_true_ev") if raw.get("fee_true_ev") is not None else info.get("edge"),
        "price": None if cents is None else cents / 100.0,
        "fv": info.get("fv"),
        "tag": "live",
        "side": side,
    }])
    _creates += 1
    if post is None:
        import kalshi_auth as ka
        ack = ka.create_order(ticker, side, count, cents, informed=info)
    else:
        ack = post(ticker, side, count, cents, info)
    return {"create_order": True, "dispatchable": 1, "ack": ack, "reasons": [], "nimble": nv}
