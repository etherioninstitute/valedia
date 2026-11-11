"""Kalshi series fee map — maker-viable vs taker-taxed.

PDF (effective 2026-07-07): https://kalshi.com/docs/kalshi-fee-schedule.pdf
  Taker: round_up(M × 0.07 × C × P × (1−P))   # next cent
  Maker: round_up(M × 0.0175 × C × P × (1−P))
P is executable (ask+half-spread/slip for a buy, bid−slip for a sell).
Never last trade alone.

GET-only. No orders. Blocking gate default OFF (annotate only).

CLI (never flips KALSHI_FEE_MAP_GATE):
  python3 kalshi_fee_map.py --dry
  python3 kalshi_fee_map.py --get
"""
import json
import math
import os
import sys
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

# Sports-relevant (executor SERIES_SPORTS + sports_engine.SERIES). MLB listed
# for fee lookup even though sports_engine killed it on net.
SPORTS_SERIES = ("KXNBAGAME", "KXNCAAMBGAME", "KXNHLGAME", "KXMLBGAME")

_MAP = None
refresh_last_errors = []


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


def refresh(get_series_fee_changes=None, get_event_fee_changes=None,
            get_series=None, series_tickers=None):
    """GET overlay. Inject getters in tests. No orders.

    Bulk fee_changes first; GET /series/{ticker} fills targets still unknown
    (sports PDF pages are garbled — this is how multipliers get populated).
    """
    fc = {}
    ev = {}
    if get_series_fee_changes:
        fc = get_series_fee_changes(None, True) or {}
    if get_event_fee_changes:
        ev = get_event_fee_changes() or {}
    tickers = list(series_tickers) if series_tickers is not None else target_series()
    extra, errors = [], []
    if get_series:
        preview = build_map(fc, ev)
        for s in tickers:
            row = preview.get(s) or {}
            if row.get("source") in ("api", "event"):
                continue
            try:
                payload = get_series(s)
            except Exception as e:
                errors.append({"series": s, "error": type(e).__name__})
                continue
            ch = series_payload_to_change(s, payload)
            if ch:
                extra.append(ch)
        if extra:
            arr = list(_fee_change_arr(fc))
            arr.extend(extra)
            fc = {"series_fee_change_arr": arr}
    global _MAP, refresh_last_errors
    _MAP = build_map(fc, ev)
    refresh_last_errors = errors
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


def _fee_change_arr(payload):
    payload = payload or {}
    arr = payload.get("series_fee_change_arr") or payload.get("fee_changes") or []
    return arr if isinstance(arr, list) else []


def series_payload_to_change(ticker, payload):
    """Normalize GET /series/{ticker} into a fee_changes row."""
    doc = payload if isinstance(payload, dict) else {}
    s = doc.get("series") if isinstance(doc.get("series"), dict) else doc
    if not isinstance(s, dict) or not s:
        return None
    ft = s.get("fee_type")
    fm = s.get("fee_multiplier")
    if ft is None and fm is None:
        return None
    ts = s.get("fee_change_ts") or s.get("scheduled_ts") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "series_ticker": str(s.get("ticker") or ticker or "").upper(),
        "fee_type": ft or "quadratic",
        "fee_multiplier": 1 if fm is None else fm,
        "scheduled_ts": ts,
    }


def target_series():
    """PDF keys + executor ALL_SERIES + sports. Parse source, skip executor import."""
    seen = []

    def add(xs):
        for x in xs or []:
            s = str(x).upper().strip()
            if s and s not in seen:
                seen.append(s)

    add(PDF_NONSTANDARD)
    add(SPORTS_SERIES)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kalshi_executor.py")
    try:
        text = open(path).read()
    except OSError:
        text = ""
    # Quoted tickers in SERIES_* lists (continuation lines included). Parse
    # source text so this module never imports the executor writer.
    in_list = False
    for line in text.splitlines():
        if "SERIES_" in line or "ALL_SERIES" in line:
            in_list = True
        if not in_list:
            continue
        for tok in line.replace(",", " ").replace("[", " ").replace("]", " ").replace("'", " ").replace('"', " ").split():
            if tok.startswith("KX") and tok.isalnum():
                add([tok])
        if "]" in line:
            in_list = False
    return seen


def classify(fmap, series_list=None):
    """Counts over targets. maker_viable and taker_taxed can overlap (M_maker=0, M_taker>1)."""
    series_list = list(series_list) if series_list is not None else target_series()
    fmap = fmap or {}
    maker_viable, taker_taxed, unknown, ok = [], [], [], []
    for s in series_list:
        row = fmap.get(s) or unknown_row(s)
        if row.get("status") != "ok":
            unknown.append(s)
            continue
        ok.append(s)
        if row.get("maker_viable"):
            maker_viable.append(s)
        if row.get("taker_taxed"):
            taker_taxed.append(s)
    sports = list(SPORTS_SERIES)
    sports_unknown = [s for s in sports if (fmap.get(s) or unknown_row(s)).get("status") != "ok"]
    sports_viable = [s for s in sports if (fmap.get(s) or {}).get("maker_viable")]
    sports_taxed = [s for s in sports if (fmap.get(s) or {}).get("taker_taxed")]
    return {
        "n_series": len(series_list),
        "ok": len(ok),
        "maker_viable": len(maker_viable),
        "taker_taxed": len(taker_taxed),
        "unknown": len(unknown),
        "maker_viable_series": maker_viable,
        "taker_taxed_series": taker_taxed,
        "unknown_series": unknown,
        "sports_n": len(sports),
        "sports_maker_viable": len(sports_viable),
        "sports_taker_taxed": len(sports_taxed),
        "sports_unknown": len(sports_unknown),
        "sports_unknown_series": sports_unknown,
    }


def default_artifact_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "recommendations", "kalshi-fee-map.json"))

def artifact_status(path=None):
    out = {"healthy": False, "reason": "missing", "series": {}, "mode": None, "as_of": None}
    try:
        doc = json.load(open(path or default_artifact_path()))
    except (OSError, ValueError, TypeError):
        return out
    if not isinstance(doc, dict):
        out["reason"] = "shape"
        return out
    series = doc.get("series") if isinstance(doc.get("series"), dict) else {}
    mode, as_of = doc.get("mode"), doc.get("as_of")
    ok = mode == "get" and bool(series) and bool(as_of)
    out.update(healthy=ok, reason="ok" if ok else "unhealthy", series=series, mode=mode, as_of=as_of)
    return out

def save_artifact(fmap, counts, mode, path=None, as_of=None, errors=None):
    path = path or default_artifact_path()
    as_of = as_of or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        import valedia_config as vc
        ver = vc.VALEDIA_VERSION
        gate_const = bool(vc.KALSHI_FEE_MAP_GATE)
        gate_env = vc.kalshi_fee_map_gate_enabled()
    except Exception:
        ver, gate_const, gate_env = "unknown", False, False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    summary = {k: counts[k] for k in (
        "n_series", "ok", "maker_viable", "taker_taxed", "unknown",
        "sports_n", "sports_maker_viable", "sports_taker_taxed", "sports_unknown",
    ) if k in counts}
    doc = {
        "as_of": as_of,
        "valedia_version": ver,
        "mode": mode,
        "kalshi_fee_map_gate_constant": gate_const,
        "kalshi_fee_map_gate_enabled": gate_env,
        "counts": summary,
        "sports_unknown_series": counts.get("sports_unknown_series") or [],
        "errors": errors or [],
        "series": fmap or {},
    }
    with open(path, "w") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
        f.write("\n")
    return path


def _load_env():
    if str(os.environ.get("OPENCLAW_SKIP_ENV", "")).strip().lower() in ("1", "true", "yes", "on"):
        return
    env_path = os.path.expanduser("~/.openclaw/.env")
    if not os.path.isfile(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def run_refresh(dry=True, get_series_fee_changes=None, get_event_fee_changes=None,
                get_series=None, out_path=None, series_tickers=None):
    """Dry = PDF only, no network. --get uses V2 GET wrappers. Never sets the gate."""
    if dry:
        fmap = refresh(series_tickers=series_tickers)
        mode = "dry"
        errors = []
    else:
        if get_series_fee_changes is None or get_series is None:
            _load_env()
            import kalshi_auth as ka
            if get_series_fee_changes is None:
                get_series_fee_changes = ka.get_series_fee_changes
            if get_series is None:
                get_series = ka.get_series
        fmap = refresh(
            get_series_fee_changes=get_series_fee_changes,
            get_event_fee_changes=get_event_fee_changes,
            get_series=get_series,
            series_tickers=series_tickers,
        )
        mode = "get"
        errors = list(refresh_last_errors)
    tickers = series_tickers if series_tickers is not None else target_series()
    counts = classify(fmap, tickers)
    path = save_artifact(fmap, counts, mode, out_path, errors=errors)
    return {"path": path, "counts": counts, "mode": mode, "errors": errors, "n_map": len(fmap)}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    dry = "--get" not in argv
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1] if i + 1 < len(argv) else None
    res = run_refresh(dry=dry, out_path=out)
    c = res["counts"]
    print(json.dumps({
        "ok": True,
        "mode": res["mode"],
        "path": res["path"],
        "as_of_file": res["path"],
        "maker_viable": c["maker_viable"],
        "taker_taxed": c["taker_taxed"],
        "unknown": c["unknown"],
        "n_series": c["n_series"],
        "sports_unknown": c["sports_unknown"],
        "gate_constant": False,
        "errors": res["errors"],
    }, indent=2))
    print(
        f"maker_viable={c['maker_viable']} taker_taxed={c['taker_taxed']} "
        f"unknown={c['unknown']} sports_unknown={c['sports_unknown']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
