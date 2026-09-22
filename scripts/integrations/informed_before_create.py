"""INFORMED-BEFORE-CREATE. Fail-closed quality bar before any create.

Reuses kalshi_fee_map.consult / allow and kalshi_profit.route (fee-true EV).
Env cannot disable this gate. Does not flip POLYMARKET_US_LIVE or writers.
"""
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
    fusion = src in ("", "mirofish", "default") or bool(info.get("mirofish_fusion"))
    if score is not None and abs(score - MIROFISH_DEFAULT) < 1e-9 and fusion:
        reasons.append("mirofish_default_50")
    if src == "mirofish":
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
    if cat in ("OIL", "OIL_BRACKET", "WEATHER_ARB"):
        reasons.append("live_fence")
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


def _role(order_type, info, route_d):
    forced = str(info.get("role") or "").lower()
    if forced in ("maker", "taker"):
        return forced
    t = str(order_type or "limit").lower()
    if t in ("market", "ioc", "fok", "immediate_or_cancel", "fill_or_kill"):
        return "taker"
    return route_d["prefer"]


def kalshi_reasons(ticker, side, count, price_cents, order_type="limit",
                   informed=None, action="buy"):
    """Empty list = admit. Halt off is necessary, not sufficient."""
    info = informed if isinstance(informed, dict) else {}
    reasons = []
    if vc.trading_halted():
        reasons.append("halt")
    strategy = info.get("strategy") or info.get("type") or ""
    if vc.live_exec_fenced(strategy, ticker=ticker):
        reasons.append("live_fence")
    if str(strategy).upper() == "NO_HARVEST" and vc.ticker_family_fenced(ticker):
        reasons.append("oil_no_harvest")
    if _sports(ticker, info.get("series"), strategy) and not _attestation_ok(info):
        reasons.append("sports_attestation")
    reasons.extend(_flag_reasons(info))

    row = kfm.consult(ticker, series=info.get("series"), fmap=info.get("fmap"))
    if row.get("status") != "ok":
        reasons.append("fee_map_missing")
    # Gate ON still drops unknown + taker_taxed. OFF does not weaken that path.
    if kfm.gate_on():
        ok, _row = kfm.allow(ticker, series=info.get("series"), fmap=info.get("fmap"))
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

    edge = _num(info.get("edge")) if info.get("edge") is not None else None
    ev = None
    if edge is None:
        reasons.append("edge_missing")
    elif price is not None and "fee_map_missing" not in reasons:
        bid = _num(info.get("bid"))
        ask = _num(info.get("ask"))
        if bid is None:
            bid = price
        if ask is None:
            ask = price
        slip = _num(info.get("slip")) or 0.0
        rt = kp.route(
            ticker, series=info.get("series"), edge=edge, bid=bid, ask=ask,
            C=count or 1, slip=slip, fmap=info.get("fmap"), action=action,
        )
        role = _role(order_type, info, rt)
        ev = rt["maker_ev"] if role == "maker" else rt["taker_ev"]
        if role == "taker" and rt["maker_viable"]:
            clears = (
                rt["taker_ev"]["net"] >= rt["maker_ev"]["net"] + rt["hurdle"]
                and rt["taker_ev"]["ok"]
            )
            if not clears:
                reasons.append("taker_hurdle")
        if role == "maker":
            if not (ev["net"] > 0):
                reasons.append("ev_below_floor")
        elif ev["net"] < float(rt["hurdle"]):
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

    if kp.demote_blocks(
            ticker, strategy or None, ledger=info.get("ledger"),
            path=info.get("ledger_path")):
        reasons.append("fill_ledger_demote")
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
    return _dedupe(reasons)


def require_poly(body):
    reasons = poly_reasons(body)
    if reasons:
        raise InformedCreateRefused(reasons)
    return True
