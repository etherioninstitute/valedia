"""When to enter, when the contract ends, and whether live is fenced.

session_clock and resolve_clock are different fields. close_time is not a
sports resolve. endDate is not a Polymarket US resolve. Oil and weather stay
LIVE_EXEC_FENCED. This module does not place orders and does not unfence.
"""
from datetime import datetime, timedelta, timezone

FIFTEEN_M = frozenset({"KXBTC15M", "KXETH15M", "KXGOLD15M"})
REQUIRED = frozenset({"kalshi_sports", "kalshi_15m", "poly_us"})
_ACTIVE = frozenset({"active", "open"})
_STUB = frozenset({"latency_arb", "combinatorial_arb", "mve"})


def _now(now):
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    parsed = _parse(now)
    return parsed or datetime.now(timezone.utc)


def _parse(ts):
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if ts is None:
        return None
    s = str(ts).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt):
    return None if dt is None else dt.isoformat()


def _get(market, *keys):
    m = market if isinstance(market, dict) else {}
    for k in keys:
        if k in m and m.get(k) not in (None, ""):
            return m.get(k)
    return None


def _truthy(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes")


def _secs(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _weather_ticker(*names):
    import weather_shadow as ws
    return any(ws.is_weather_series(n) for n in names)


def classify(desk=None, ticker=None, series=None, strategy=None, market=None):
    """Venue class. Explicit desk wins over a ticker guess."""
    import kalshi_fee_map as kfm
    import valedia_config as vc
    d = str(desk or "").strip().lower().replace("-", "_")
    strat = str(strategy or "").strip().upper()
    ser = kfm.series_from_ticker(ticker, series)
    m = market if isinstance(market, dict) else {}
    if d in ("oil", "oil_bracket") or strat in ("OIL", "OIL_BRACKET") or vc.ticker_family_fenced(ticker) or vc.ticker_family_fenced(ser):
        return "oil"
    if d in ("weather", "weather_arb") or strat == "WEATHER_ARB":
        return "weather"
    if d in ("poly", "poly_us", "polymarket", "polymarket_us"):
        return "poly_us"
    if d in ("latency", "latency_arb"):
        return "latency_arb"
    if d in ("combinatorial", "combinatorial_arb"):
        return "combinatorial_arb"
    if (d in ("mve", "combo", "combos") or m.get("mve_collection_ticker")
            or m.get("collection_ticker") or ser.startswith("KXMVE")):
        return "mve"
    if _weather_ticker(ser, ticker, desk):
        return "weather"
    if d in ("15m", "kalshi_15m") or ser in FIFTEEN_M:
        return "kalshi_15m"
    if d in ("sports", "kalshi_sports") or ser in kfm.SPORTS_SERIES or "SPORT" in strat:
        return "kalshi_sports"
    if ticker is None and desk and d not in (
            "other", "model", "kalshi"):
        return classify(None, ticker=desk, series=series, strategy=strategy, market=market)
    return "other"


def _pack_session(venue, active, open_at, close_at, rule, reason):
    return {
        "desk": venue, "venue_class": venue, "required": venue in REQUIRED,
        "active": bool(active), "open_at": _iso(open_at), "close_at": _iso(close_at),
        "enter_rule": rule, "reason": reason,
    }


def _pack_resolve(venue, known, resolve_at, kind, ignored, reason):
    return {
        "desk": venue, "venue_class": venue, "required": venue in REQUIRED,
        "known": bool(known), "resolve_at": _iso(resolve_at), "kind": kind,
        "ignored_field": ignored, "reason": reason,
    }


def _window(market):
    m = market if isinstance(market, dict) else {}
    return (
        str(m.get("status") or "").lower(),
        _parse(_get(m, "open_time", "openTime")),
        _parse(_get(m, "close_time", "closeTime")),
    )


def _sports_session(market, now):
    status, open_at, close_at = _window(market)
    if status not in _ACTIVE or open_at is None or close_at is None:
        return False, open_at, close_at, "schedule_or_status_missing"
    if not (open_at <= now < close_at):
        return False, open_at, close_at, "outside_schedule"
    return True, open_at, close_at, "schedule_and_active"


def _distinct_exp(market, close_at):
    exp = _parse(_get(
        market, "expected_expiration_time", "expiration_time",
        "latest_expiration_time", "expectedExpirationTime"))
    if exp is None:
        return None
    if close_at is not None and exp == close_at:
        return None
    return exp


def _sports_resolve(market):
    """early_close or settlement. close_time alone is not a resolve."""
    m = market if isinstance(market, dict) else {}
    _status, _open, close_at = _window(m)
    exp = _distinct_exp(m, close_at)
    early = str(_get(m, "early_close_condition", "early_close") or "").strip()
    can_early = bool(m.get("can_close_early") or m.get("canCloseEarly") or early)
    if can_early and exp is not None:
        return True, exp, "early_close", "close_time", "early_close"
    timer = _secs(_get(m, "settlement_timer_seconds", "settlementTimerSeconds"))
    if timer is not None and exp is not None:
        return True, exp + timedelta(seconds=timer), "settlement", "close_time", "settlement"
    settled = _parse(_get(m, "settlement_ts", "settlement_time", "determined_time"))
    status = str(m.get("status") or "").lower()
    if status in ("determined", "settled", "finalized") and settled is not None:
        return True, settled, "settlement", "close_time", "settlement"
    return False, None, "unknown", "close_time", "close_time_alone"


def _15m_resolve(market):
    _status, open_at, close_at = _window(market)
    if open_at is not None and close_at is not None and close_at > open_at:
        return True, close_at, "window_close", None, "window_close"
    return False, None, "unknown", None, "window_missing"


def _poly_session(market, now):
    m = market if isinstance(market, dict) else {}
    if _truthy(m.get("closed")):
        return False, None, None, "not_accepting", "closed"
    acc = _get(m, "acceptingOrders", "accepting_orders")
    if acc is not None and _truthy(acc):
        return True, None, None, "accepting_orders", "accepting_orders"
    game = _parse(_get(m, "gameStartTime", "game_start_time", "gameStart"))
    delay = _secs(_get(m, "orderDelaySeconds", "delay_seconds", "secondsDelay", "seconds_delay"))
    if game is not None and delay is not None and now >= game + timedelta(seconds=delay):
        return True, game, game + timedelta(seconds=delay), "game_start_delay", "game_start_delay"
    return False, game, None, "not_accepting", "not_accepting"


def _poly_resolve(market):
    """UMA propose + challenge. endDate alone is not a resolve."""
    m = market if isinstance(market, dict) else {}
    prop = _parse(_get(m, "umaProposalTime", "proposalTime", "proposedAt", "uma_proposal_time"))
    chal = _secs(_get(m, "challengePeriodSeconds", "challenge_period_seconds", "liveness"))
    if prop is not None and chal is not None and chal >= 0:
        return True, prop + timedelta(seconds=chal), "uma_challenge", "endDate", "uma_challenge"
    status = str(_get(m, "umaResolutionStatus", "uma_status") or "").lower()
    settled = _parse(_get(m, "umaResolvedAt", "resolvedAt"))
    if status in ("resolved", "settled") and settled is not None:
        return True, settled, "uma_settled", "endDate", "uma_settled"
    return False, None, "unknown", "endDate", "endDate_not_resolve"


def _wx_parsed(market, ticker):
    try:
        import weather_shadow as ws
        return ws.parse_clock(market, ticker)
    except Exception:
        return {"tz_gaps": ["parse_error"], "observe_date": None, "last_trading_at": None}


def session_clock(desk, ticker=None, series=None, strategy=None, market=None, now=None):
    venue = classify(desk, ticker=ticker, series=series, strategy=strategy, market=market)
    now = _now(now)
    if venue == "oil":
        _ok, open_at, close_at, _why = _sports_session(market, now)
        row = _pack_session(venue, False, open_at, close_at, "fenced", "LIVE_EXEC_FENCED")
        row["dispatchable"] = 0
        row["mode"] = "RESEARCH/SHADOW"
        return row
    if venue == "weather":
        _ok, open_at, close_at, _why = _sports_session(market, now)
        parsed = _wx_parsed(market, ticker)
        ltt = _parse(parsed.get("last_trading_at"))
        if ltt is not None:
            close_at = ltt
        row = _pack_session(venue, False, open_at, close_at, "fenced", "LIVE_EXEC_FENCED")
        row["parsed"] = parsed
        row["dispatchable"] = 0
        return row
    if venue in _STUB:
        return _pack_session(venue, False, None, None, "stub", "stub")
    if venue == "kalshi_sports":
        ok, open_at, close_at, why = _sports_session(market, now)
        return _pack_session(venue, ok, open_at, close_at, "schedule_and_active", why)
    if venue == "kalshi_15m":
        ok, open_at, close_at, why = _sports_session(market, now)
        rule = "window_open_close"
        why = "window_open" if ok else ("window_closed" if why == "outside_schedule" else "window_missing")
        return _pack_session(venue, ok, open_at, close_at, rule, why)
    if venue == "poly_us":
        ok, open_at, close_at, rule, why = _poly_session(market, now)
        return _pack_session(venue, ok, open_at, close_at, rule, why)
    return _pack_session(venue, False, None, None, "not_required", "not_required")


def resolve_clock(desk, ticker=None, series=None, strategy=None, market=None, now=None):
    venue = classify(desk, ticker=ticker, series=series, strategy=strategy, market=market)
    if venue == "weather":
        parsed = _wx_parsed(market, ticker)
        known, at, kind, ignored, why = _sports_resolve(market)
        if not known:
            why = "observe_date_not_instant" if parsed.get("observe_date") else "fenced_unparsed"
            kind = "observe_day" if parsed.get("observe_date") else "unknown"
        row = _pack_resolve(venue, known, at, kind, ignored or "close_time", why)
        row["parsed"] = parsed
        row["dispatchable"] = 0
        return row
    if venue in ("kalshi_sports", "oil"):
        known, at, kind, ignored, why = _sports_resolve(market)
        if venue != "kalshi_sports" and not known:
            why = "fenced_unparsed"
        row = _pack_resolve(venue, known, at, kind, ignored, why)
        if venue == "oil":
            row["dispatchable"] = 0
        return row
    if venue == "kalshi_15m":
        known, at, kind, ignored, why = _15m_resolve(market)
        return _pack_resolve(venue, known, at, kind, ignored, why)
    if venue == "poly_us":
        known, at, kind, ignored, why = _poly_resolve(market)
        return _pack_resolve(venue, known, at, kind, ignored, why)
    if venue in _STUB:
        return _pack_resolve(venue, False, None, "stub", None, "stub")
    return _pack_resolve(venue, False, None, "not_required", None, "not_required")


def fence_gate(desk, ticker=None, series=None, strategy=None, market=None):
    """Read the live-exec fence. Does not clear it."""
    venue = classify(desk, ticker=ticker, series=series, strategy=strategy, market=market)
    if venue == "oil":
        return {"desk": venue, "venue_class": venue, "fenced": True,
                "mode": "RESEARCH/SHADOW", "live_exec": False, "dispatchable": 0,
                "reason": "LIVE_EXEC_FENCED"}
    if venue == "weather":
        return {"desk": venue, "venue_class": venue, "fenced": True,
                "mode": "RESEARCH/SHADOW", "live_exec": False, "dispatchable": 0,
                "reason": "LIVE_EXEC_FENCED"}
    if venue == "mve":
        return {"desk": venue, "venue_class": venue, "fenced": True,
                "mode": "SHADOW", "live_exec": False, "reason": "no_live_rfq"}
    if venue in ("latency_arb", "combinatorial_arb"):
        return {"desk": venue, "venue_class": venue, "fenced": True,
                "mode": "SHADOW", "live_exec": False, "reason": "per_leg_stub"}
    if venue == "poly_us":
        return {"desk": venue, "venue_class": venue, "fenced": False,
                "mode": "unfenced", "live_exec": True,
                "reason": "writer none; POLYMARKET_US_LIVE stays false in git"}
    return {"desk": venue, "venue_class": venue, "fenced": False,
            "mode": "unfenced", "live_exec": True, "reason": "not_fenced"}


def next_window(series, markets, now=None):
    """Next 15m ticker from a series payload the caller already polled. No network."""
    import kalshi_fee_map as kfm
    now = _now(now)
    ser = str(series or "").upper()
    best = None
    for m in markets or []:
        if not isinstance(m, dict):
            continue
        ts = kfm.series_from_ticker(m.get("ticker"), m.get("series_ticker") or ser)
        if ts != ser:
            continue
        op = _parse(_get(m, "open_time", "openTime"))
        if op is None or op < now:
            continue
        if best is None or op < best[0]:
            best = (op, m.get("ticker"))
    return {"series": ser, "next_ticker": None if best is None else best[1], "polled": True}


def leg_clocks(desk, legs, now=None):
    """Per-leg session + resolve. Stub vector. Does not place."""
    out = []
    for leg in legs or []:
        m = leg if isinstance(leg, dict) else {"ticker": leg}
        t = m.get("ticker") or m.get("id")
        out.append({
            "leg": t,
            "session": session_clock(None, ticker=t, market=m, now=now, strategy=m.get("strategy")),
            "resolve": resolve_clock(None, ticker=t, market=m, now=now, strategy=m.get("strategy")),
        })
    return out


def before_create(opp, now=None):
    """Skip tuple, or None to leave the existing create path alone. No orders."""
    try:
        return _before_create(opp, now)
    except Exception:
        return ("SKIP_RESOLVE", "clock_error")


def _before_create(opp, now):
    a = opp if isinstance(opp, dict) else {}
    market = a.get("market") if isinstance(a.get("market"), dict) else None
    if market is None and isinstance(a.get("raw"), dict):
        market = a["raw"]
    desk = a.get("desk") or a.get("type") or a.get("strategy")
    ticker = a.get("ticker") or a.get("marketSlug")
    strat = a.get("strategy") or a.get("type")
    when = a.get("now", now)
    venue = classify(desk, ticker=ticker, series=a.get("series"), strategy=strat, market=market)
    if venue in ("oil", "weather"):
        return ("SKIP_LIVE_FENCE", "LIVE_EXEC_FENCED")
    if venue in _STUB:
        return ("SKIP_CLOCK_STUB", fence_gate(venue, ticker=ticker, market=market)["reason"])
    if venue not in REQUIRED:
        return None
    sess = session_clock(venue, ticker=ticker, series=a.get("series"), strategy=strat, market=market, now=when)
    res = resolve_clock(venue, ticker=ticker, series=a.get("series"), strategy=strat, market=market, now=when)
    if not sess["active"]:
        return ("SKIP_SESSION", sess["reason"])
    if not res["known"]:
        return ("SKIP_RESOLVE", res["reason"])
    return None


def clock_control_rows():
    """Cockpit honesty. source stays const. Does not read env and does not unfence."""
    return (
        ("CLOCK_KALSHI_SPORTS",
         "session=schedule∩active | resolve=early_close|settlement | fence=unfenced",
         "const", "clock",
         "close_time alone is not resolve. Attestation still required."),
        ("CLOCK_KALSHI_15M",
         "session=open→close | resolve=window_close | fence=unfenced",
         "const", "clock",
         "KXBTC15M KXETH15M KXGOLD15M. Next ticker is a polled series, no order."),
        ("CLOCK_POLY_US",
         "session=acceptingOrders|gameStart+delay | resolve=uma_challenge | fence=writer none",
         "const", "clock",
         "endDate is not resolve. Git POLYMARKET_US_LIVE stays false. Writer polymarket none."),
        ("CLOCK_OIL_WEATHER",
         "oil=RESEARCH/SHADOW | weather=RESEARCH/SHADOW | fence=LIVE_EXEC_FENCED | "
         "dispatchable=0 | live_exec=false",
         "const", "fence",
         "Fenced. Env cannot enable WEATHER_LIVE. This row does not unfence."),
        ("WEATHER_SHADOW",
         "RESEARCH/SHADOW | fence=LIVE_EXEC_FENCED | dispatchable=0 | live_exec=false",
         "const", "fence",
         "Official FV is NWS/NOAA/Climate Data. Default-50 and near-neutral are not size fuel. "
         "MiroFish season tags season_only. No weather writer."),
        ("OIL_SHADOW",
         "RESEARCH/SHADOW | fence=LIVE_EXEC_FENCED | dispatchable=0 | live_exec=false",
         "const", "fence",
         "Official FV is the settlement print the KXWTI/KXOIL rules cite. "
         "Muse and MiroFish are not FV. No oil writer."),
        ("CLOCK_ARB_MVE",
         "session=per-leg | resolve=vector stub | fence=no live RFQ",
         "const", "fence",
         "latency_arb and combinatorial_arb are stubs. MVE collection has no live RFQ."),
    )
