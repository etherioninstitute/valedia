"""INFORMED-BEFORE-CREATE. Fail-closed quality bar before any create.

Reuses kalshi_fee_map.consult / allow and kalshi_profit.route (fee-true EV).
Env cannot disable this gate. Does not flip POLYMARKET_US_LIVE or writers.
"""
from datetime import timezone

import valedia_config as vc
import kalshi_fee_map as kfm
import kalshi_profit as kp

ALLOWED_FV = frozenset({"model", "rules_clock", "researched"})
# POLY-LOOP ladder names that are not tag_prior. tag_prior is never eligible.
POLY_FV_OK = ALLOWED_FV | frozenset({"override", "market", "cache", "modeled"})
MIROFISH_DEFAULT = 50.0
MIN_DEPTH = 1.0
EDGE_PTS = 8.0
REFUSE_FLAGS = (
    "copy_mirror", "whale_mirror", "private_key_bot", "wallet_connect",
    "taker_longshot", "fee_blind", "keyword_arb", "oil_harvest",
)


class InformedCreateRefused(RuntimeError):
    def __init__(self, reasons):
        self.reasons = tuple(reasons)
        super().__init__("informed-before-create refused: " + ",".join(self.reasons))


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dedupe(reasons):
    out = []
    for r in reasons:
        if r not in out:
            out.append(r)
    return out


def _sports(ticker, series, strategy):
    s = kfm.series_from_ticker(ticker, series)
    if s in kfm.SPORTS_SERIES:
        return True
    return "SPORT" in str(strategy or "").upper()


def _attestation_ok(info):
    if str(info.get("sports_geo") or "").lower() == "fail":
        return False
    att = info.get("sports_attestation")
    if att is True:
        return True
    if isinstance(att, dict):
        if str(att.get("geo") or "").lower() == "fail":
            return False
        return bool(att.get("valid"))
    return False


def _fv_reasons(info, *, poly=False):
    src = str(info.get("fv_source") or "").strip().lower()
    allowed = POLY_FV_OK if poly else ALLOWED_FV
    reasons = []
    if src == "tag_prior" or info.get("tag_prior_alone"):
        reasons.append("tag_prior")
    if src not in allowed:
        reasons.append("fv_source")
    score = _num(info.get("mirofish_score"))
    default_score = score is None or abs(score - MIROFISH_DEFAULT) < 1e-9
    # Fusion with the omitted/default 50 is not a model. A side score of 50
    # next to a real model FV is not, by itself, a fusion create.
    if src == "mirofish":
        reasons.append("mirofish_default_50")
    elif info.get("mirofish_fusion") and default_score:
        reasons.append("mirofish_default_50")
    elif score is not None and abs(score - MIROFISH_DEFAULT) < 1e-9 and src in ("", "default"):
        reasons.append("mirofish_default_50")
    if not info.get("fv_confidence"):
        reasons.append("fv_confidence")
    fv = _num(info.get("fv"))
    if fv is None or fv <= 0 or fv >= 1:
        reasons.append("fv_missing")
    return reasons


def _flag_reasons(info):
    reasons = []
    for flag in REFUSE_FLAGS:
        if info.get(flag):
            reasons.append(flag)
    if str(info.get("sports_geo") or "").lower() == "fail":
        reasons.append("sports_geo")
    cat = str(info.get("category") or info.get("strategy") or info.get("type") or "").upper()
    if cat in ("OIL", "OIL_BRACKET"):
        reasons.append("live_fence")
    elif cat == "WEATHER_ARB" and not _weather_live_on():
        reasons.append("live_fence")
    return reasons


def _weather_live_on():
    try:
        return bool(vc.weather_live_enabled())
    except Exception:
        return False


LOTTERY_PX = 0.05
LATE_PX = 0.90
CONVICTION_LOTTERY_ROI = 19.0


def _weather_order(ticker, info):
    strat = str(info.get("strategy") or info.get("type") or info.get("category") or "").upper()
    if strat in ("WEATHER_ARB", "WEATHER"):
        return True
    import session_clocks as sc
    return sc.classify(None, ticker=ticker) == "weather"


def _early_soft(info):
    if info.get("early_book_soft") is True:
        return True
    for key in ("early_mid", "early_price", "early_book"):
        v = _num(info.get(key))
        if v is not None and LOTTERY_PX < v < LATE_PX:
            return True
    return False


def _late_observe(info, ticker):
    """True late, False early, None unknown. Unknown fails closed on a favorite."""
    if "late_observe" in info and info.get("late_observe") is not None:
        return bool(info.get("late_observe"))
    now = info.get("now")
    if not now:
        return None
    import weather_shadow as ws
    from datetime import datetime
    from zoneinfo import ZoneInfo
    market = info.get("market") if isinstance(info.get("market"), dict) else {}
    parsed = ws.parse_clock(market, ticker)
    obs = parsed.get("observe_date")
    tz = parsed.get("tz") or parsed.get("city_tz")
    if not obs or not tz:
        return None
    if isinstance(now, datetime):
        dt = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    else:
        s = str(now).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(ZoneInfo(tz))
    if local.date().isoformat() != obs:
        return False
    return local.hour >= 16


def _weather_shape(ticker, price, info):
    """Create-path lottery and late-collapse. Not a shadow-only note.

    SKIP_LOTTERY: tail at or under 5c, or a CONVICTION ROI lottery on a weather book.
    SKIP_LATE_COLLAPSE: favorite at or over 90c late in the observe-day without an
    early-day soft book. Unknown lateness fails closed.
    """
    if not _weather_order(ticker, info):
        return []
    reasons = []
    if price is not None and price <= LOTTERY_PX + 1e-12:
        reasons.append("SKIP_LOTTERY")
    else:
        roi = _num(info.get("roi_if_right"))
        if roi is None:
            roi = _num(info.get("conviction_roi"))
        if (is_conviction(info.get("strategy")) and roi is not None
                and roi >= CONVICTION_LOTTERY_ROI and price is not None
                and price <= 0.10 + 1e-12):
            reasons.append("SKIP_LOTTERY")
    if price is not None and price >= LATE_PX - 1e-12:
        if not _early_soft(info) and _late_observe(info, ticker) is not False:
            reasons.append("SKIP_LATE_COLLAPSE")
    return reasons


def kelly_contracts(fv, price, bankroll, fee_per=0.0):
    """Fee-adjusted half-Kelly contract cap via quant_engine.kelly_size.

    Floor of dollars/price. Does not use kelly_size's max(1, ...) bump.
    FTL track-record is inside dynamic_per_trade_cap (Valedia-owned).
    """
    import quant_engine as qe
    p = _num(price)
    f = _num(fv)
    b = _num(bankroll)
    if p is None or f is None or b is None or p <= 0 or p >= 1 or b <= 0:
        return 0
    fee = max(0.0, float(fee_per or 0))
    f_adj = min(0.99, max(0.01, f - fee))
    bet, _n, _frac = qe.kelly_size(f_adj, p, b, fraction=vc.KELLY_SAFETY_FRACTION)
    if not bet or float(bet) <= 0:
        return 0
    return int(float(bet) / p)


def _clock_reasons(desk, ticker, info):
    """Refuse required desks with a dead session or an unknown resolve.

    Oil/weather stay on the live_fence path. Arb and MVE are stubs.
    """
    import session_clocks as sc
    info = info if isinstance(info, dict) else {}
    market = info.get("market") if isinstance(info.get("market"), dict) else info
    strat = info.get("strategy") or info.get("type") or info.get("category")
    venue = sc.classify(
        desk, ticker=ticker, series=info.get("series"), strategy=strat, market=market)
    if venue in ("latency_arb", "combinatorial_arb", "mve"):
        return ["clock_stub"]
    if venue not in sc.REQUIRED:
        return []
    sess = sc.session_clock(
        venue, ticker=ticker, series=info.get("series"), strategy=strat,
        market=market, now=info.get("now"))
    res = sc.resolve_clock(
        venue, ticker=ticker, series=info.get("series"), strategy=strat,
        market=market, now=info.get("now"))
    reasons = []
    if not sess.get("active"):
        reasons.append("session_inactive")
    if not res.get("known"):
        reasons.append("resolve_unknown")
    return reasons


def _role(order_type, info, route_d):
    forced = str(info.get("role") or "").lower()
    if forced in ("maker", "taker"):
        return forced
    t = str(order_type or "limit").lower()
    if t in ("market", "ioc", "fok", "immediate_or_cancel", "fill_or_kill"):
        return "taker"
    return route_d["prefer"]


def _weather_city(ticker):
    """City weather contracts stay on the live fence even without strategy WEATHER_ARB."""
    import session_clocks as sc
    return sc.classify(None, ticker=ticker) == "weather"


def kalshi_reasons(ticker, side, count, price_cents, order_type="limit",
                   informed=None, action="buy"):
    """Empty list = admit. Halt off is necessary, not sufficient."""
    info = informed if isinstance(informed, dict) else {}
    reasons = []
    if vc.trading_halted():
        reasons.append("halt")
    strategy = info.get("strategy") or info.get("type") or ""
    if vc.live_exec_fenced(strategy, ticker=ticker) or _weather_city(ticker):
        oilish = vc.ticker_family_fenced(ticker) or str(strategy).upper() in ("OIL", "OIL_BRACKET")
        if oilish or not _weather_live_on():
            reasons.append("live_fence")
    if str(strategy).upper() == "NO_HARVEST" and vc.ticker_family_fenced(ticker):
        reasons.append("oil_no_harvest")
    if _sports(ticker, info.get("series"), strategy) and not _attestation_ok(info):
        reasons.append("sports_attestation")
    reasons.extend(_flag_reasons(info))

    fmap = info.get("fmap") if isinstance(info.get("fmap"), dict) else None
    if fmap is None:
        art = kfm.artifact_status(info.get("fee_map_path"))
        if not art.get("healthy"):
            reasons.append("fee_map_refresh")
        else:
            fmap = art["series"]
    row = kfm.consult(ticker, series=info.get("series"), fmap=fmap)
    if row.get("status") != "ok":
        reasons.append("fee_map_missing")
    # Gate ON still drops unknown + taker_taxed. OFF does not weaken that path.
    if kfm.gate_on():
        ok, _row = kfm.allow(ticker, series=info.get("series"), fmap=fmap)
        if not ok:
            reasons.append("fee_map_gate")

    reasons.extend(_fv_reasons(info))

    try:
        cents = int(price_cents)
    except (TypeError, ValueError):
        cents = 0
    if cents < 1 or cents > 99:
        reasons.append("price")
    price = cents / 100.0 if 1 <= cents <= 99 else None
    reasons.extend(_weather_shape(ticker, price, info))

    edge = _num(info.get("edge")) if info.get("edge") is not None else None
    ev = None
    if edge is None:
        reasons.append("edge_missing")
    elif price is not None and "fee_map_missing" not in reasons and "fee_map_refresh" not in reasons:
        bid = _num(info.get("bid")) if "bid" in info else None
        ask = _num(info.get("ask")) if "ask" in info else None
        slip = _num(info.get("slip")) if "slip" in info else None
        if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
            reasons.append("spread_missing")
        elif slip is None or slip < 0:
            reasons.append("slip_missing")
        else:
            rt = kp.route(
                ticker, series=info.get("series"), edge=edge, bid=bid, ask=ask,
                C=count or 1, slip=slip, fmap=fmap, action=action,
            )
            role = _role(order_type, info, rt)
            ev = rt["maker_ev"] if role == "maker" else rt["taker_ev"]
            extra = kp.dome_extra(price)
            if role == "taker" and rt["maker_viable"]:
                clears = (
                    rt["taker_ev"]["net"] >= rt["maker_ev"]["net"] + rt["hurdle"]
                    and rt["taker_ev"]["ok"]
                )
                if not clears:
                    reasons.append("taker_hurdle")
            if role == "maker":
                if not (ev["net"] > extra):
                    reasons.append("ev_below_floor")
            elif ev["net"] < float(rt["hurdle"]) + extra:
                reasons.append("ev_below_floor")

    depth = _num(info.get("depth"))
    min_depth = _num(info.get("min_depth"))
    if min_depth is None:
        min_depth = MIN_DEPTH
    if depth is None or depth < min_depth:
        reasons.append("depth")

    fee_per = float(ev["fee_per"]) if isinstance(ev, dict) else 0.0
    cap = kelly_contracts(info.get("fv"), price, info.get("bankroll"), fee_per)
    try:
        n = int(float(count))
    except (TypeError, ValueError):
        n = 0
    if n < 1 or n > cap:
        reasons.append("size")

    view = kp.ledger_view(
        ticker, strategy or None, ledger=info.get("ledger"),
        path=info.get("ledger_path"))
    if view["demote"]:
        reasons.append("fill_ledger_demote")
    reasons.extend(_clock_reasons(info.get("desk"), ticker, info))
    return _dedupe(reasons)


def require_kalshi(ticker, side, count, price_cents, order_type="limit",
                   informed=None, action="buy"):
    reasons = kalshi_reasons(
        ticker, side, count, price_cents, order_type, informed, action=action)
    if reasons:
        raise InformedCreateRefused(reasons)
    return True


def _fee_prob(info):
    if "fee_prob" in info and info.get("fee_prob") is not None:
        return _num(info.get("fee_prob"))
    for k in ("takerFee", "fee"):
        if k in info and info.get(k) is not None:
            return _num(info.get(k))
    return None


def edge_after_fees_pts(fv, mid, fee_prob):
    fvp, mp, fee = _num(fv), _num(mid), _num(fee_prob)
    if fvp is None or mp is None or fee is None:
        return None
    return abs(fvp - mp) * 100.0 - abs(fee) * 100.0


def create_eligible(rec):
    """Future Poly create flag. tag_prior-alone is never eligible.

    Paper DRY/SHADOW may still propose. create_order stays false here.
    Needs FV + confidence, ≥8pt after a known fee, and a book exit.
    """
    if not isinstance(rec, dict):
        return False
    if rec.get("create_order"):
        return False
    src = str(rec.get("fv_source") or "").lower()
    if src == "tag_prior" or src not in POLY_FV_OK:
        return False
    if not rec.get("fv_confidence"):
        return False
    if rec.get("status") not in (None, "", "DRY/SHADOW"):
        return False
    if not rec.get("exit_condition") or rec.get("slip") is None:
        return False
    score = _num(rec.get("mirofish_score"))
    if score is not None and abs(score - MIROFISH_DEFAULT) < 1e-9:
        return False
    if any(rec.get(f) for f in REFUSE_FLAGS):
        return False
    pts = edge_after_fees_pts(rec.get("fv"), rec.get("mid"), _fee_prob(rec))
    if pts is None or pts < EDGE_PTS - 1e-12:
        return False
    return True


def _poly_view(body):
    b = body if isinstance(body, dict) else {}
    inner = b.get("request") if isinstance(b.get("request"), dict) else None
    if not inner:
        return b
    merged = dict(inner)
    merged.update({k: v for k, v in b.items() if k != "request"})
    return merged


def poly_reasons(body):
    """US create bar. Caller still LiveWriteBlocked when POLYMARKET_US_LIVE is off."""
    info = _poly_view(body)
    reasons = []
    if vc.trading_halted():
        reasons.append("halt")
    reasons.extend(_fv_reasons(info, poly=True))
    reasons.extend(_flag_reasons(info))
    if str(info.get("category") or "").upper() == "SPORTS" and not _attestation_ok(info):
        reasons.append("sports_attestation")
    fee = _fee_prob(info)
    if fee is None:
        reasons.append("fee_unknown")
    mid = _num(info.get("mid"))
    if mid is None:
        px = info.get("price")
        if isinstance(px, dict):
            px = px.get("value")
        mid = _num(px)
    pts = edge_after_fees_pts(info.get("fv"), mid, fee)
    if pts is None or pts < EDGE_PTS - 1e-12:
        reasons.append("edge_after_fees")
    if not (info.get("exit_ok") or info.get("book_exit") or info.get("exit_condition")):
        reasons.append("book_exit")
    depth = _num(info.get("depth"))
    min_depth = _num(info.get("min_depth"))
    if min_depth is None:
        min_depth = MIN_DEPTH
    if depth is None or depth < min_depth:
        reasons.append("depth")
    qty = info.get("quantity", info.get("count"))
    cap = kelly_contracts(info.get("fv"), mid, info.get("bankroll"), fee or 0.0)
    try:
        n = int(float(qty))
    except (TypeError, ValueError):
        n = 0
    if n < 1 or n > cap:
        reasons.append("size")
    slug = info.get("marketSlug") or info.get("slug")
    reasons.extend(_clock_reasons("poly_us", slug, info))
    return _dedupe(reasons)


def require_poly(body):
    reasons = poly_reasons(body)
    if reasons:
        raise InformedCreateRefused(reasons)
    return True


_SPEED_SERIES = frozenset({"KXBTC15M", "KXETH15M", "KXGOLD15M"})
_PASS_KEYS = (
    "fv", "fv_source", "fv_confidence", "edge", "slip", "depth", "min_depth",
    "bankroll", "series", "strategy", "desk", "role", "mirofish_score",
    "mirofish_fusion", "tag_prior_alone", "ledger", "ledger_path",
    "fee_map_path", "fmap", "market", "now", "bid", "ask",
    "sports_attestation", "sports_geo", "category",
)


def is_conviction(strategy):
    s = str(strategy or "").strip().upper()
    return s == "CONVICTION" or s.startswith("CONVICTION_")


def informed_from_opp(opp):
    """FV fields only. Cheap Yes/No ROI and a null predicted_prob are not FV."""
    src = opp if isinstance(opp, dict) else {}
    info = {}
    for k in _PASS_KEYS:
        if src.get(k) is not None:
            info[k] = src.get(k)
    if info.get("fv") is None and src.get("predicted_prob") is not None:
        info["fv"] = src.get("predicted_prob")
    if not info.get("strategy") and src.get("type"):
        info["strategy"] = src.get("type")
    if info.get("bid") is None and src.get("yes_bid") is not None:
        info["bid"] = src.get("yes_bid")
    if info.get("ask") is None:
        side = str(src.get("side") or "").lower()
        ask = src.get("no_ask") if side == "no" and src.get("no_ask") is not None else src.get("yes_ask")
        if ask is not None:
            info["ask"] = ask
    if not info.get("desk"):
        info["desk"] = "kalshi"
    return info


def _speed_path(info, ticker):
    """Arb and 15m stay off the CONVICTION annotation. INFORMED still applies at create."""
    info = info or {}
    strat = str(info.get("strategy") or "").upper()
    if "ARB" in strat:
        return True
    head = str(ticker or "").upper().split("-", 1)[0]
    ser = str(info.get("series") or "").upper()
    return head in _SPEED_SERIES or ser in _SPEED_SERIES


def conviction_note(opp, info, *, caller, post=None, get=None):
    """Nimble 2 annotation for an admitted CONVICTION create. Annotate only, never a veto.

    None when VALEDIA_SYSTEMONE is off, on arb / 15m, or for any other strategy.
    Only for call sites without nimble_wiring.gate (that verdict is the note there).
    Facts: prediction_facts on the opp + this `info`, informed_facts for the underlying.
    """
    opp = opp if isinstance(opp, dict) else {}
    if not is_conviction((info or {}).get("strategy")) or _speed_path(info, opp.get("ticker")):
        return None
    try:
        import nimble_annotate as na
        return na.conviction_note(opp, info, caller=caller, post=post, get=get)
    except Exception as e:
        return {"annotate_only": True, "oms": False, "kind": "conviction", "fail_open": True,
                "error": type(e).__name__}


def create_preflight(opp, count, price_cents, order_type="limit", action="buy"):
    """INFORMED fail-closed. Pure gate: no model call here.

    The Nimble 2 verdict on an admitted create comes from nimble_wiring.gate at the
    caller (last gate) or, at an unwired site, from conviction_note (annotate only).
    """
    opp = opp if isinstance(opp, dict) else {}
    ticker = opp.get("ticker")
    info = informed_from_opp(opp)
    reasons = kalshi_reasons(
        ticker, opp.get("side"), count, price_cents, order_type, info, action=action)
    if reasons:
        return {"ok": False, "reasons": list(reasons), "informed": info}
    return {"ok": True, "reasons": [], "informed": info}
