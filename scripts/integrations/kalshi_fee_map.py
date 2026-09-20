"""Kalshi series fee map — maker-viable vs taker-taxed.

PDF (effective 2026-07-07): https://kalshi.com/docs/kalshi-fee-schedule.pdf
  Taker: round_up(M × 0.07 × C × P × (1−P))   # next cent
  Maker: round_up(M × 0.0175 × C × P × (1−P))
P is executable (ask+half-spread/slip for a buy, bid−slip for a sell).
Never last trade alone.

GET-only. No orders. Blocking gate default OFF (annotate only).
"""
import math
from datetime import datetime, timezone

PDF_AS_OF = "2026-07-07"
TAKER_COEF = 0.07
MAKER_COEF = 0.0175

# First non-standard page of the Jul 7 2026 PDF (maker, taker, note).
# Later pages are two-column garbled — API fee_changes wins when present.
PDF_NONSTANDARD = {
    "KXAAAGASM": (1.0, 1.0, "US gas price"),
    "KXATPMATCH": (1.0, 1.0, "ATP Tennis Match"),
    "KXBALLONDOR": (1.0, 1.0, "Ballon d'Or"),
    "KXBTCMAX150": (1.0, 1.0, "When will bitcoin hit 150k?"),
    "KXBTCY": (0.0, 0.0, "BTC price range EOY"),
    "KXCITRINI": (0.0, 0.0, "Citrini scenario"),
    "KXCPI": (1.0, 1.0, "CPI"),
    "KXCPIYOY": (1.0, 1.0, "Inflation"),
    "KXDOED": (0.0, 0.0, "DOE eliminated"),
    "KXEGGS": (1.0, 1.0, "Egg prices"),
    "KXELECTIRAN": (0.0, 0.0, "Iran presidential election"),
    "KXEMMYCACTO": (1.0, 1.0, "Emmys Lead Comedy Actor"),
    "KXEMMYCACTR": (1.0, 1.0, "Emmys Lead Comedy Actress"),
    "KXEMMYCSERIES": (1.0, 1.0, "Emmys Comedy Series"),
}

_MAP = None


def round_up_cent(x):
    if x <= 0:
        return 0.0
    return math.ceil(x * 100.0 - 1e-12) / 100.0


def fee(P, C, M, role="taker"):
    """fee(P, C, M). P = executable dollars in (0,1). role taker|maker."""
    p = max(0.0, min(1.0, float(P)))
    coef = TAKER_COEF if str(role).lower() != "maker" else MAKER_COEF
    return round_up_cent(float(M) * coef * float(C) * p * (1.0 - p))


def p_exec(bid, ask, action="buy", slip=0.0):
    """Executable P: buy=ask+slip, sell=bid-slip. Not last trade."""
    a = str(action or "buy").lower()
    s = float(slip or 0)
    if a in ("buy", "yes"):
        p = float(ask or 0) + s
    else:
        p = float(bid or 0) - s
    return max(0.01, min(0.99, round(p, 4)))


def series_from_ticker(ticker, series=None):
    if series:
        return str(series).upper().strip()
    t = str(ticker or "").upper().strip()
    if not t:
        return ""
    known = sorted(PDF_NONSTANDARD, key=len, reverse=True)
    for s in known:
        if t == s or t.startswith(s + "-"):
            return s
    return t.split("-", 1)[0]


def _row(series, maker_m, taker_m, notes="", as_of=PDF_AS_OF, source="pdf"):
    mm = float(maker_m)
    tm = float(taker_m)
    return {
        "series_ticker": series,
        "maker_multiplier": mm,
        "taker_multiplier": tm,
        "maker_viable": mm == 0.0,
        "taker_taxed": tm > 1.0,
        "notes": notes,
        "as_of": as_of,
        "source": source,
        "status": "ok",
    }


def unknown_row(series):
    return {
        "series_ticker": series,
        "maker_multiplier": None,
        "taker_multiplier": None,
        "maker_viable": False,
        "taker_taxed": True,
        "notes": "unknown — fail-closed",
        "as_of": None,
        "source": "unknown",
        "status": "unknown",
    }


def multipliers_from_fee_type(fee_type, fee_multiplier):
    fm = 1.0 if fee_multiplier is None else float(fee_multiplier)
    ft = (fee_type or "quadratic").lower()
    if ft == "quadratic":
        return 0.0, fm
    if ft == "quadratic_with_maker_fees":
        return fm, fm
    if ft == "quadratic_with_combo_maker_fees":
        return fm * 2.0, fm
    if ft == "flat":
        return fm, fm
    return None, None


def _parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _active_by_series(items, now):
    """Latest scheduled_ts <= now per series_ticker."""
    best = {}
    for it in items or []:
        s = str(it.get("series_ticker") or "").upper()
        if not s:
            continue
        ts = _parse_ts(it.get("scheduled_ts"))
        if ts is not None and ts > now:
            continue
        prev = best.get(s)
        if prev is None or (ts is not None and (prev[0] is None or ts >= prev[0])):
            best[s] = (ts, it)
    return {s: v[1] for s, v in best.items()}


def build_map(fee_changes=None, event_fee_changes=None, now=None):
    """PDF rows, then overlay current series fee_changes (API wins)."""
    now = now or datetime.now(timezone.utc)
    out = {}
    for s, (mm, tm, note) in PDF_NONSTANDARD.items():
        out[s] = _row(s, mm, tm, note, PDF_AS_OF, "pdf")
    payload = fee_changes or {}
    arr = payload.get("series_fee_change_arr") or payload.get("fee_changes") or []
    if not isinstance(arr, list):
        arr = []
    for s, ch in _active_by_series(arr, now).items():
        mm, tm = multipliers_from_fee_type(ch.get("fee_type"), ch.get("fee_multiplier"))
        if mm is None:
            pdf = PDF_NONSTANDARD.get(s)
            if pdf:
                mm, tm = pdf[0], pdf[1]
            else:
                continue
        ts = ch.get("scheduled_ts") or PDF_AS_OF
        note = f"fee_type={ch.get('fee_type') or 'quadratic'}"
        pdf = PDF_NONSTANDARD.get(s)
        if pdf:
            note = f"{pdf[2]}; {note}"
        out[s] = _row(s, mm, tm, note, str(ts)[:10], "api")
    # Event overrides keyed as series until consult(event_ticker) is used.
    ev = (event_fee_changes or {}).get("event_fee_changes") or []
    for ch in ev:
        s = str(ch.get("series_ticker") or "").upper()
        if not s:
            continue
        ft = ch.get("fee_type_override")
        fm = ch.get("fee_multiplier_override")
        if ft is None and fm is None:
            continue
        parent = out.get(s)
        use_ft = ft if ft is not None else (None if parent is None else "quadratic")
        use_fm = fm if fm is not None else (parent or {}).get("taker_multiplier")
        mm, tm = multipliers_from_fee_type(use_ft or "quadratic", use_fm)
        if mm is None:
            continue
        out[s] = _row(s, mm, tm, f"event override {ch.get('event_ticker')}", str(ch.get("scheduled_ts") or "")[:10], "event")
    return out


def current_map(fmap=None):
    global _MAP
    if fmap is not None:
        return fmap
    if _MAP is None:
        _MAP = build_map()
    return _MAP


def reset_map():
    global _MAP
    _MAP = None


def refresh(get_series_fee_changes=None, get_event_fee_changes=None):
    """GET overlay. Inject getters in tests. No orders."""
    fc = {}
    ev = {}
    if get_series_fee_changes:
        fc = get_series_fee_changes(None, True) or {}
    if get_event_fee_changes:
        ev = get_event_fee_changes() or {}
    global _MAP
    _MAP = build_map(fc, ev)
    return _MAP


def consult(ticker, series=None, fmap=None):
    s = series_from_ticker(ticker, series)
    if not s:
        return unknown_row("")
    row = current_map(fmap).get(s)
    return dict(row) if row else unknown_row(s)


def gate_on():
    try:
        import valedia_config as vc
        return bool(vc.kalshi_fee_map_gate_enabled())
    except Exception:
        return False


def allow(ticker, series=None, fmap=None):
    """(ok, row). Gate off → always ok (annotated). Gate on → drop unknown + taker_taxed."""
    info = consult(ticker, series=series, fmap=fmap)
    if not gate_on():
        return True, info
    if info.get("status") != "ok":
        return False, info
    if info.get("taker_taxed"):
        return False, info
    return True, info
