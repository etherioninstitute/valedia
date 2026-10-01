"""PROFIT-1: fee-true EV preflight + maker-first + fill-quality ledger.

Reuses kalshi_fee_map.fee / p_exec / consult (PDF Jul 2026). Annotate always.
Block/skip only if new flags (default OFF). Never flips KALSHI_FEE_MAP_GATE.
No orders. Research helpers, not a live bot.
"""
import json
import os
from datetime import datetime, timezone

import kalshi_fee_map as kfm

TAKER_HURDLE = 0.02
# Quadratic fee peaks at 50¢. Extra floor inside ±10¢, full size at the peak.
# ponytail: flat 2¢ tent. Swap for fee(P)-fee(wing) if the PDF coef changes.
DOME_CENTER = 0.50
DOME_BAND = 0.10
DOME_PEAK = 0.02
DEMOTE_THRESH = 0.03
DEMOTE_MIN_N = 5
EMA = 0.3

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
DEFAULT_LEDGER = os.path.join(_ROOT, "data", "kalshi-fill-quality.json")


def _flag(name, default=False):
    try:
        import valedia_config as vc
        return bool(vc._env_flag(name, getattr(vc, name, default)))
    except Exception:
        env = os.environ.get(name)
        if env is not None and str(env).strip() != "":
            return str(env).strip().lower() in ("1", "true", "yes", "on")
        return bool(default)


def ev_gate_on():
    return _flag("KALSHI_EV_PREFLIGHT_GATE", False)


def maker_first_on():
    return _flag("KALSHI_MAKER_FIRST", False)


def fill_demote_on():
    return _flag("KALSHI_FILL_LEDGER_DEMOTE", False)


def taker_hurdle():
    env = os.environ.get("KALSHI_TAKER_HURDLE")
    if env:
        try:
            return float(env)
        except (TypeError, ValueError):
            pass
    return TAKER_HURDLE


def dome_extra(P):
    """Extra $/contract near the 50¢ fee dome. Zero outside the band."""
    try:
        dist = abs(float(P) - DOME_CENTER)
    except (TypeError, ValueError):
        return 0.0
    if dist >= DOME_BAND:
        return 0.0
    return DOME_PEAK * (1.0 - dist / DOME_BAND)


def half_spread(bid, ask):
    try:
        b, a = float(bid or 0), float(ask or 0)
    except (TypeError, ValueError):
        return 0.0
    if b <= 0 or a <= 0 or a < b:
        return 0.0
    return (a - b) / 2.0


def ev_preflight(edge, P, C, M, half_spread=0.0, slip=0.0, role="taker"):
    """net = edge − fee(P,C,M)/C − half_spread − slip. Per-contract dollars."""
    c = max(1.0, float(C or 1))
    fee_total = kfm.fee(P, c, M, role)
    fee_per = fee_total / c
    hs = max(0.0, float(half_spread or 0))
    sl = max(0.0, float(slip or 0))
    ed = float(edge or 0)
    net = ed - fee_per - hs - sl
    return {
        "edge": ed,
        "P": float(P),
        "C": c,
        "M": float(M or 0),
        "role": str(role),
        "fee_total": fee_total,
        "fee_per": fee_per,
        "half_spread": hs,
        "slip": sl,
        "net": net,
        "ok": net > 0,
    }


def _px(v):
    """Dollars. Cent integers (>=1) are converted; junk is 0."""
    try:
        x = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    return x / 100.0 if x >= 1 else max(0.0, x)


def _book(opp):
    """Traded-side bid/ask in dollars: top-level, then raw (allocator rows), then bid/ask."""
    side = str(opp.get("side") or "yes").lower()
    price = _px(opp.get("price"))
    raw = opp.get("raw") if isinstance(opp.get("raw"), dict) else {}
    k = "no" if side in ("no", "sell") else "yes"

    def first(*vals):
        for v in vals:
            x = _px(v)
            if x > 0:
                return x
        return 0.0
    bid = first(opp.get(k + "_bid"), raw.get(k + "_bid"), opp.get("bid"))
    ask = first(opp.get(k + "_ask"), raw.get(k + "_ask"), opp.get("ask"), price)
    return bid, ask, side


def _edge(opp):
    if opp.get("edge") is not None:
        return float(opp.get("edge") or 0)
    try:
        return float(opp.get("ev_per_dollar") or 0) * float(opp.get("price") or 0)
    except (TypeError, ValueError):
        return 0.0


def _mult(info, role):
    key = "maker_multiplier" if str(role) == "maker" else "taker_multiplier"
    m = info.get(key)
    return 1.0 if m is None else float(m)


def route(ticker, series=None, edge=0.0, bid=0.0, ask=0.0, C=1, slip=0.0,
          fmap=None, action="buy"):
    """Prefer rest on maker_viable. Taker only if EV clears maker+hurdle."""
    info = kfm.consult(ticker, series=series, fmap=fmap)
    hs = half_spread(bid, ask)
    mm, tm = _mult(info, "maker"), _mult(info, "taker")
    buy = str(action or "buy").lower() in ("buy", "yes")
    p_taker = kfm.p_exec(bid, ask, "buy" if buy else "sell", slip)
    try:
        rest = float(bid if buy else ask)
    except (TypeError, ValueError):
        rest = 0.0
    # Unknown resting side: price the maker leg at the executable P (conservative), never 0.01.
    maker_price_known = rest > 0
    p_maker = max(0.01, min(0.99, rest)) if maker_price_known else p_taker
    ev_m = ev_preflight(edge, p_maker, C, mm, 0.0, 0.0, "maker")
    ev_t = ev_preflight(edge, p_taker, C, tm, hs, slip, "taker")
    hurdle = taker_hurdle()
    viable = bool(info.get("maker_viable"))
    taker_clears = ev_t["net"] >= ev_m["net"] + hurdle and ev_t["ok"]
    if viable:
        prefer = "taker" if taker_clears else "maker"
    else:
        prefer = "taker"  # route label only; taker_clears now reports the real hurdle check
        taker_clears = bool(ev_t["ok"] and ev_t["net"] >= hurdle)
    return {
        "prefer": prefer,
        "maker_viable": viable,
        "maker_price_known": maker_price_known,
        "taker_clears": bool(taker_clears),
        "hurdle": hurdle,
        "maker_ev": ev_m,
        "taker_ev": ev_t,
        "fee_map": info,
        "soft": not maker_first_on(),
    }


def taker_clears_floor(route_d, P):
    """Any order placed AS TAKER: taker_ev.net >= hurdle + dome_extra(P), and on a
    maker_viable series also >= maker_ev.net + hurdle. Same rule informed_before_create
    applies to role=taker (no new threshold). Unknown EV → False."""
    r = route_d or {}
    try:
        t_net = float((r.get("taker_ev") or {}).get("net"))
    except (TypeError, ValueError):
        return False
    hurdle = float(r.get("hurdle", taker_hurdle()))
    if t_net < hurdle + dome_extra(P):
        return False
    if r.get("maker_viable"):
        try:
            m_net = float((r.get("maker_ev") or {}).get("net"))
        except (TypeError, ValueError):
            return True
        return t_net >= m_net + hurdle
    return True


def should_skip_taker(route_d=None):
    if not maker_first_on():
        return False
    r = route_d or {}
    return r.get("prefer") == "maker" and not r.get("taker_clears")


def load_ledger(path=None):
    p = path or DEFAULT_LEDGER
    try:
        with open(p) as f:
            doc = json.load(f)
        if isinstance(doc, dict):
            return doc
    except (OSError, ValueError, TypeError):
        pass
    return {"series": {}, "strategy": {}}


def save_ledger(doc, path=None):
    p = path or DEFAULT_LEDGER
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    try:
        import valedia_config as vc
        ver = vc.VALEDIA_VERSION
        demote = bool(vc.kalshi_fill_ledger_demote_enabled())
    except Exception:
        ver, demote = "unknown", False
    out = dict(doc or {})
    out["as_of"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out["valedia_version"] = ver
    out["kalshi_fill_ledger_demote"] = demote
    with open(p, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True)
        f.write("\n")
    return p


def adverse_drift(side, fill_px, mid_after):
    """Positive = mid moved against the fill (adverse selection)."""
    try:
        px, mid = float(fill_px), float(mid_after)
    except (TypeError, ValueError):
        return 0.0
    s = str(side or "yes").lower()
    if s in ("buy", "yes"):
        return px - mid
    return mid - px


def _bump(row, drift):
    n = int(row.get("n") or 0) + 1
    old = float(row.get("score") or 0)
    score = (1.0 - EMA) * old + EMA * max(0.0, float(drift))
    return {"n": n, "score": score, "last_drift": float(drift)}


def record_fill(ticker, side, fill_px, mid_after, strategy="", series=None,
                path=None, ledger=None):
    """Update demotion scores. Read-only vs live writers. Persist JSON."""
    doc = ledger if ledger is not None else load_ledger(path)
    doc.setdefault("series", {})
    doc.setdefault("strategy", {})
    drift = adverse_drift(side, fill_px, mid_after)
    ser = kfm.series_from_ticker(ticker, series)
    if ser:
        doc["series"][ser] = _bump(doc["series"].get(ser) or {}, drift)
        doc["series"][ser]["ticker"] = str(ticker or "")
    if strategy:
        doc["strategy"][str(strategy)] = _bump(doc["strategy"].get(str(strategy)) or {}, drift)
    if path is not None or ledger is None:
        save_ledger(doc, path)
    return drift, doc


def ingest_fills(rows, path=None, ledger=None):
    """rows: {ticker, side, fill_px, mid_after, strategy?, series?}."""
    doc = ledger if ledger is not None else load_ledger(path)
    n = 0
    for row in rows or []:
        if not row.get("ticker") or row.get("fill_px") is None or row.get("mid_after") is None:
            continue
        _, doc = record_fill(
            row.get("ticker"), row.get("side"), row.get("fill_px"),
            row.get("mid_after"), strategy=row.get("strategy") or "",
            series=row.get("series"), path=None, ledger=doc)
        n += 1
    if path is not None or ledger is None:
        save_ledger(doc, path)
    return n, doc


def ledger_view(ticker, strategy=None, ledger=None, path=None):
    """Read fill quality. Missing file or no rows is empty, not a zero score.

    demote is evidence (n and score), not the env flag. Empty does not demote.
    """
    missing = False
    if ledger is None:
        p = path or DEFAULT_LEDGER
        if not os.path.isfile(p):
            missing = True
            doc = {"series": {}, "strategy": {}}
        else:
            doc = load_ledger(p)
    else:
        doc = ledger if isinstance(ledger, dict) else {"series": {}, "strategy": {}}
    ser = kfm.series_from_ticker(ticker)
    best_n, best_score, saw, demote = 0, None, False, False
    srow = (doc.get("series") or {}).get(ser) if ser else None
    grow = (doc.get("strategy") or {}).get(strategy) if strategy else None
    for row in (srow, grow):
        if not isinstance(row, dict):
            continue
        n = int(row.get("n") or 0)
        if n <= 0:
            continue
        saw = True
        score = float(row.get("score") or 0)
        if n >= best_n:
            best_n, best_score = n, score
        if n >= DEMOTE_MIN_N and score >= DEMOTE_THRESH:
            demote = True
    return {
        "empty": not saw,
        "missing_file": missing,
        "series": ser,
        "n": best_n,
        "score": best_score,
        "demote": demote,
    }


def demote_blocks(ticker, strategy=None, ledger=None, path=None):
    """Annotate-skip. Still flag-gated. Create uses ledger_view, which is not."""
    if not fill_demote_on():
        return False
    return bool(ledger_view(ticker, strategy, ledger=ledger, path=path)["demote"])


def annotate(opp, contracts=1, slip=0.0, fmap=None, path=None):
    """Shadow-annotate opp. Skip only when a new gate is on. Fail-open."""
    ticker = opp.get("ticker")
    series = opp.get("series")
    raw = opp.get("raw") if isinstance(opp.get("raw"), dict) else {}
    series = series or raw.get("series")
    bid, ask, side = _book(opp)
    # _book returns the traded side's own bid/ask, so buying NO is a buy on the NO book.
    action = "sell" if side == "sell" else "buy"
    edge = _edge(opp)
    info = opp.get("fee_map") or kfm.consult(ticker, series=series, fmap=fmap)
    opp["fee_map"] = info
    rt = route(ticker, series=series, edge=edge, bid=bid, ask=ask, C=contracts,
               slip=slip, fmap=fmap or None, action=action)
    opp["maker_first"] = rt
    role = rt["prefer"]
    ev = rt["maker_ev"] if role == "maker" else rt["taker_ev"]
    opp["ev_preflight"] = ev
    view = ledger_view(
        ticker, opp.get("strategy") or opp.get("type"), path=path)
    opp["fill_quality"] = {
        "series": view["series"],
        "empty": view["empty"],
        "missing_file": view["missing_file"],
        "n": view["n"],
        "score": view["score"],
        "would_block": bool(fill_demote_on() and view["demote"]),
    }
    skip = False
    if ev_gate_on() and not ev.get("ok"):
        skip = True
    if view["demote"] and fill_demote_on():
        skip = True
    return (not skip), opp
