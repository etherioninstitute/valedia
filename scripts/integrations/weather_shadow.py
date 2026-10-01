"""Weather RESEARCH/SHADOW scorer. Fee-true would-have only.

No Kalshi client. No orders. dispatchable is always 0.
Gridpoint JSON must match the observe-day. CLI fixtures go through
weather_lookup. Research tags are not FV. MiroFish is season_only.
"""
import json
import os
import re
from datetime import datetime, timezone
from math import erf, sqrt
from zoneinfo import ZoneInfo

import shadow_common as desk
import valedia_config as vc
import weather_lookup as wl
import weather_nws as wn

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
LEDGER_PATH = os.path.join(ROOT, "models", "weather_shadow_ledger.json")
CACHE_PATH = os.path.join(ROOT, "models", "weather_city_cache.json")
# Live create rows. Shadow morning alerts. Jev never writes these alone.
LIVE_LEDGER_PATH = os.path.join(ROOT, "models", "weather_live_ledger.jsonl")
ALERT_MD = os.path.join(ROOT, "recommendations", "WEATHER-MORNING-ALERT.md")
ALERT_EV = 0.0
LOTTERY_PX = 0.05
LATE_PX = 0.90

# Longest first. City suffix is whatever remains (KXHIGHNY → NY).
PREFIXES = (
    "KXHURRICANE", "KXWEATH", "KXHIGH", "KXLOW", "KXRAIN",
    "KXSNOW", "KXTEMP", "KXWIND",
)
# Non-city series. A missing suffix is not a city-TZ gap.
NON_CITY = frozenset({"KXHURRICANE", "KXWEATH", "KXTEMP"})
OFFICIAL = frozenset({"nws", "noaa", "climate", "climate_data", "nws_climate"})

# City zones we actually know. Anything else is a gap, not a guess.
CITY_TZ = {
    "NY": "America/New_York",
    "CHI": "America/Chicago",
    "MIA": "America/New_York",
    "LA": "America/Los_Angeles",
    "ATL": "America/New_York",
    "DAL": "America/Chicago",
    "SEA": "America/Los_Angeles",
    "DEN": "America/Denver",
    "AUS": "America/Chicago",
    "HOU": "America/Chicago",
    "PHX": "America/Phoenix",
    "SF": "America/Los_Angeles",
    "BOS": "America/New_York",
    "DC": "America/New_York",
    # Kalshi Vegas high is KXHIGHTLV and the low is KXLOWTLV. Both are Pacific.
    "LAS": "America/Los_Angeles",
    "TLV": "America/Los_Angeles",
}
TEXT_TZ = {
    "ET": "America/New_York", "EST": "America/New_York", "EDT": "America/New_York",
    "CT": "America/Chicago", "CST": "America/Chicago", "CDT": "America/Chicago",
    "MT": "America/Denver", "MST": "America/Denver", "MDT": "America/Denver",
    "PT": "America/Los_Angeles", "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles",
    "UTC": "UTC", "GMT": "UTC",
}
_MON = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_DATE = re.compile(
    r"\b(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june|july|"
    r"jun|jul|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+(?P<d>\d{1,2}),?\s+(?P<y>\d{4})",
    re.I,
)
_ISO = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_LTT = re.compile(
    r"(?:last trading time|trading closes|closes)\s*(?:is|:|at|will\s+be)?\s*"
    r"(?P<h>\d{1,2}):(?P<m>\d{2})\s*(?P<ap>a\.?m\.?|p\.?m\.?)?"
    r"(?:\s*(?P<tz>ET|EST|EDT|CT|CST|CDT|MT|MST|MDT|PT|PST|PDT|UTC|GMT))?",
    re.I,
)
_RANGE = re.compile(r"(\d+)\s*-\s*(\d+)\s*°")
_ABOVE = re.compile(r">\s*(\d+)")
_BELOW = re.compile(r"<\s*(\d+)")


def is_weather_series(ticker):
    head = str(ticker or "").upper().split("-", 1)[0]
    return any(head.startswith(p) for p in PREFIXES)


def split_series(ticker):
    head = str(ticker or "").upper().split("-", 1)[0]
    for p in PREFIXES:
        if head.startswith(p):
            city = head[len(p):] or None
            return p, city
    return None, None


def _dates(text):
    found = []
    for m in _DATE.finditer(text or ""):
        mon = _MON.get(m.group("mon").lower())
        if not mon:
            continue
        try:
            found.append((m.start(), datetime(int(m.group("y")), mon, int(m.group("d"))).date()))
        except ValueError:
            continue
    for m in _ISO.finditer(text or ""):
        try:
            found.append((m.start(), datetime.fromisoformat(m.group(1)).date()))
        except ValueError:
            continue
    found.sort(key=lambda t: t[0])
    return found


def _clock_hour(h, ap):
    h = int(h)
    if ap:
        ap = ap.lower().replace(".", "")
        if ap == "pm" and h != 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
    return h


def parse_clock(market, ticker=None):
    """Typed observe-day / Last Trading Time. Missing TZ stays missing."""
    m = market if isinstance(market, dict) else {}
    ticker = ticker or m.get("ticker") or m.get("series_ticker") or m.get("series")
    prefix, city = split_series(ticker)
    if city is None and m.get("city"):
        city = str(m.get("city")).upper()
    text = " ".join(
        str(m.get(k) or "")
        for k in ("title", "subtitle", "rules_primary", "rules",
                  "early_close_condition", "early_close", "yes_sub_title")
    )
    dates = _dates(text)
    obs_at = None
    low = text.lower()
    mark = low.find("observation day")
    if mark >= 0:
        for pos, day in dates:
            if pos >= mark:
                obs_at = day
                break
    if obs_at is None and dates:
        obs_at = dates[0][1]
    ltt = _LTT.search(text)
    gaps = []
    if obs_at is None:
        gaps.append("observe_date_missing")
    tz_name = None
    tz_source = None
    if city and city in CITY_TZ:
        city_tz = CITY_TZ[city]
    elif prefix in NON_CITY and not city:
        city_tz = None
        gaps.append("city_absent")
    elif city or (prefix and prefix not in NON_CITY):
        city_tz = None
        gaps.append("city_tz_unmapped")
    else:
        city_tz = None
    last_at = None
    local_hm = None
    if ltt is None:
        gaps.append("ltt_missing")
    else:
        local_hm = f"{int(ltt.group('h')):02d}:{ltt.group('m')}"
        tok = (ltt.group("tz") or "").upper()
        if tok in TEXT_TZ:
            tz_name, tz_source = TEXT_TZ[tok], "text"
        elif city_tz:
            tz_name, tz_source = city_tz, "city_map"
        else:
            gaps.append("ltt_tz_missing")
        window = low[ltt.end():ltt.end() + 48].lstrip()
        if window.startswith("on the observation day"):
            day = obs_at
        else:
            after = [d for pos, d in dates if pos >= ltt.end()]
            day = after[0] if after else None
        if day is None:
            gaps.append("ltt_date_missing")
        if tz_name and day is not None:
            try:
                local = datetime(
                    day.year, day.month, day.day,
                    _clock_hour(ltt.group("h"), ltt.group("ap")),
                    int(ltt.group("m")),
                    tzinfo=ZoneInfo(tz_name),
                )
                last_at = local.astimezone(timezone.utc).isoformat()
            except Exception:
                gaps.append("tz_convert_failed")
                last_at = None
    return {
        "series_prefix": prefix,
        "city": city,
        "city_tz": city_tz,
        "observe_date": None if obs_at is None else obs_at.isoformat(),
        "last_trading_at": last_at,
        "last_trading_local": local_hm,
        "tz": tz_name,
        "tz_source": tz_source,
        "tz_gaps": gaps,
        "text_present": bool(text.strip()),
    }


def parse_bracket(title, ticker=None):
    title = title or ""
    m = _RANGE.search(title)
    if m:
        return "range", int(m.group(1)), int(m.group(2))
    m = _ABOVE.search(title)
    if m:
        return "above", int(m.group(1)), None
    m = _BELOW.search(title)
    if m:
        return "below", None, int(m.group(1))
    t = str(ticker or "")
    m = re.search(r"-T(\d+(?:\.\d+)?)$", t, re.I)
    if m:
        return "above", float(m.group(1)), None
    return None, None, None


def bracket_prob(forecast, kind, lo, hi, sigma=3.0):
    """Same Gaussian as weather_arb.estimate_prob. None when the bracket is unknown.

    A computed 0.50 from a real forecast is a model value. Callers must not
    substitute 0.50 when the bracket or forecast is missing.
    """
    if forecast is None or kind is None:
        return None
    z = sigma * sqrt(2.0)
    if kind == "range":
        p = 0.5 * (erf((hi - forecast) / z) - erf((lo - forecast) / z))
    elif kind == "above":
        p = 0.5 * (1.0 - erf((lo - forecast) / z))
    elif kind == "below":
        p = 0.5 * (1.0 + erf((hi - forecast) / z))
    else:
        return None
    return max(0.01, min(0.99, p))


def _fee_quote(ticker, price, contracts, fmap):
    import kalshi_fee_map as kfm
    series = kfm.series_from_ticker(ticker)
    row = (fmap or {}).get(series)
    if not row or row.get("status") != "ok":
        role, mult, status = "taker", 1.0, "unknown"
    elif row.get("maker_viable"):
        role, mult, status = "maker", float(row.get("maker_multiplier") or 0.0), "ok"
    else:
        role, mult, status = "taker", float(row.get("taker_multiplier") or 1.0), "ok"
    return kfm.fee(price, contracts, mult, role), role, mult, status, series


def shadow_contracts(fv, price, bankroll, fee_per):
    """Half-Kelly contracts capped at KELLY_BASE_CAP.

    ponytail: not dynamic_per_trade_cap — that reads the live track-record files.
    """
    p = float(price)
    bnk = float(bankroll or 0)
    if not (0 < p < 1) or bnk <= 0 or fv is None:
        return 0
    f = min(0.99, max(0.01, float(fv) - float(fee_per or 0)))
    b = (1.0 - p) / p
    full = (f * b - (1.0 - f)) / b
    if full <= 0:
        return 0
    frac = min(full * float(vc.KELLY_SAFETY_FRACTION), float(vc.KELLY_BASE_CAP))
    return int((bnk * frac) / p)


def _side_yes(settlement):
    return settlement in (1, True, "yes", "YES", "y")


def score(candidate, fmap=None, bankroll=100.0, now=None):
    """One would-have row. dispatchable is 0 even if the caller says otherwise."""
    import session_clocks as sc
    c = candidate if isinstance(candidate, dict) else {}
    ticker = c.get("ticker") or c.get("series")
    market = c.get("market") if isinstance(c.get("market"), dict) else dict(c)
    if ticker and "ticker" not in market:
        market = dict(market)
        market["ticker"] = ticker
    parsed = parse_clock(market, ticker)
    sess = sc.session_clock(
        "weather", ticker=ticker, strategy=c.get("strategy") or "WEATHER_ARB",
        market=market, now=c.get("now", now))
    res = sc.resolve_clock(
        "weather", ticker=ticker, strategy=c.get("strategy") or "WEATHER_ARB",
        market=market, now=c.get("now", now))
    fence = sc.fence_gate("weather", ticker=ticker, strategy="WEATHER_ARB")
    source = str(c.get("fv_source") or "").strip().lower()
    raw_fv = c.get("fv")
    forecast = c.get("forecast_temp")
    forecast, source, blocked, looked = wl.shadow_forecast(
        c, parsed, source, forecast, wn.official_from)
    model = None
    if blocked:
        fv = None
        tag = blocked
    elif forecast is not None and (source in OFFICIAL or (
            looked is not None and source in ("nws_cli", "weather_com"))):
        kind, lo, hi = parse_bracket(c.get("title") or market.get("title"), ticker)
        model = bracket_prob(float(forecast), kind, lo, hi)
        fv = model
        tag = "official" if fv is not None else "no_fv"
    elif source in OFFICIAL and raw_fv is not None and abs(float(raw_fv) - 0.5) > 1e-9:
        fv = float(raw_fv)
        tag = "official"
    elif c.get("season") or c.get("mirofish_season"):
        fv = None
        tag = "season_only"
    elif source in ("default", "default_50", "default-50") or (
            raw_fv is not None and abs(float(raw_fv) - 0.5) <= 1e-9):
        fv = None
        tag = "refuse_default_50"
    elif source in ("muse", "gdelt", "mirofish", "mirofish_swarm") or raw_fv is not None:
        fv = None
        tag = "unofficial"
    else:
        fv = None
        tag = "no_fv"
    tag, fv = desk.drop_near_neutral(tag, fv)
    side = str(c.get("side") or "yes").lower()
    price = c.get("price")
    if price is None and c.get("price_cents") is not None:
        price = float(c.get("price_cents")) / 100.0
    price = None if price is None else float(price)
    fv_side = None if fv is None else (fv if side == "yes" else 1.0 - fv)
    size = 0
    fee = None
    ev = None
    role = None
    fee_status = None
    if tag == "official" and fv_side is not None and price is not None and 0 < price < 1:
        fee1, role, _mult, fee_status, _series = _fee_quote(ticker, price, 1, fmap)
        if fv_side - price - fee1 > 0:
            size = shadow_contracts(fv_side, price, bankroll, fee1)
            if size > 0:
                fee, role, _mult, fee_status, _series = _fee_quote(ticker, price, size, fmap)
                ev = (fv_side - price) * size - fee
                if ev <= 0:
                    size, ev, fee = 0, None, None
    elif tag == "official":
        fee_status = "no_book" if price is None else fee_status
    settlement = c.get("settlement")
    pnl = None
    if settlement is not None and size > 0 and price is not None and fee is not None:
        payout = 1.0 if _side_yes(settlement) == (side == "yes") else 0.0
        pnl = (payout - price) * size - fee
    elif settlement is not None:
        pnl = 0.0
    row = {
        "ticker": ticker,
        "mode": "RESEARCH/SHADOW",
        "dispatchable": 0,
        "live_exec": False,
        "writer": None,
        "create_order": False,
        "fence": fence.get("reason"),
        "tag": tag,
        "fv_source": source or None,
        "fv": None if fv is None else round(float(fv), 6),
        "model_fv": None if model is None else round(float(model), 6),
        "side": side,
        "price": price,
        "fee": None if fee is None else fee,
        "fee_role": role,
        "fee_status": fee_status,
        "fee_true_ev": None if ev is None else round(float(ev), 4),
        "would_have_size": size,
        "would_have_pnl": None if pnl is None else round(float(pnl), 4),
        "settlement": settlement,
        "enter_window": {
            "open_at": sess.get("open_at"),
            "close_at": sess.get("close_at") or parsed.get("last_trading_at"),
            "active": False,
            "reason": sess.get("reason"),
        },
        "resolve": {
            "known": res.get("known"),
            "kind": res.get("kind"),
            "reason": res.get("reason"),
            "resolve_at": res.get("resolve_at"),
        },
        "parsed": parsed,
        "book": c.get("book") or ("fixture" if c.get("fixture") else "absent"),
    }
    row = wl.attach(row, looked)
    row["ftl_shaped"] = desk.ftl_shaped(row, "WEATHER_ARB")
    return _jev_weather(row)


def _jev_weather(row):
    """Jev shadow on the scored bundle. Fail-open. Never a create."""
    try:
        import jev_shadow as jev
        stamped = jev.annotate_weather(row)
    except Exception:
        stamped = dict(row)
        stamped["jev"] = {"enabled": True, "fail_open": True, "oms": False, "error": "annotate_weather"}
    stamped["create_order"] = False
    stamped["dispatchable"] = 0
    return stamped


def universe_rows(fee_series, city_cache):
    """Series already on disk. No HTTP. No prices, so no invented EV."""
    rows = []
    seen = set()
    for series, row in sorted((fee_series or {}).items()):
        if not is_weather_series(series):
            continue
        seen.add(series)
        rows.append({
            "ticker": series,
            "origin": "fee_map",
            "title": (row or {}).get("notes") or "",
            "fee_status": (row or {}).get("status"),
            "maker_viable": bool((row or {}).get("maker_viable")),
            "book": "absent",
        })
    cache_series = (city_cache or {}).get("series") or {}
    for series, row in sorted(cache_series.items()):
        if series in seen or not is_weather_series(series):
            continue
        seen.add(series)
        rows.append({
            "ticker": series,
            "origin": "city_cache",
            "title": (row or {}).get("title") or "",
            "city": (row or {}).get("code"),
            "fee_status": "unknown",
            "book": "absent",
            "cache_ts": (city_cache or {}).get("ts"),
        })
    return rows


def build_ledger(candidates, fmap=None, bankroll=100.0, universe=None, note=""):
    scored = [score(c, fmap=fmap, bankroll=bankroll) for c in candidates or []]
    return {
        "desk": "weather",
        "mode": "RESEARCH/SHADOW",
        "dispatchable": 0,
        "live_exec": False,
        "live_writer": False,
        "fence": "LIVE_EXEC_FENCED",
        "create_order": False,
        "valedia_version": vc.VALEDIA_VERSION,
        "note": note,
        "n_universe": len(universe or []),
        "universe": universe or [],
        "n_scored": len(scored),
        "scored": scored,
    }


def load_disk():
    import kalshi_fee_map as kfm
    art = kfm.artifact_status()
    try:
        cache = json.load(open(CACHE_PATH))
    except (OSError, ValueError):
        cache = {}
    return art.get("series") or {}, cache


def write_ledger(path=None, fmap=None, city_cache=None, fixtures=None, bankroll=100.0):
    """Write the shadow ledger. fixtures are labeled book=fixture. No network."""
    if fmap is None or city_cache is None:
        disk_fee, disk_cache = load_disk()
        fmap = disk_fee if fmap is None else fmap
        city_cache = disk_cache if city_cache is None else city_cache
    uni = []
    for row in universe_rows(fmap, city_cache):
        scored = score(row, fmap=fmap, bankroll=bankroll)
        uni.append(scored)
    body = build_ledger(
        fixtures or [], fmap=fmap, bankroll=bankroll, universe=uni,
        note=("Fee-map + city cache on disk. No Kalshi GET. No orders. "
              "NWS is FV only when the period date equals the observe-day. "
              "March cache titles are not today's FV. MiroFish is season_only. "
              "Near-neutral is not size fuel. book=fixture is not a live book."),
    )
    path = path or LEDGER_PATH
    body = desk.finish_ledger(path, body)
    if os.path.abspath(path) == os.path.abspath(LEDGER_PATH):
        desk.mirror(path, "weather_shadow_ledger.json")
    return body


def _alertable(row):
    """Fee-true official edge, not a lottery tail and not a late favorite."""
    ev = row.get("fee_true_ev")
    if ev is None or float(ev) <= ALERT_EV:
        return False
    if row.get("tag") != "official" or row.get("fv") is None:
        return False
    px = row.get("price")
    if px is not None and (float(px) <= LOTTERY_PX or float(px) >= LATE_PX):
        return False
    return True


def write_morning_alert(rows, path=None):
    """Parent glance file. create_order stays false on every line."""
    path = path or ALERT_MD
    lines = ["# WEATHER-MORNING-ALERT", "", "Shadow alert. Not an order.", ""]
    for r in rows or []:
        lines.append(
            "- %s ev=%s px=%s fv=%s tag=%s create_order=false"
            % (r.get("ticker"), r.get("fee_true_ev"), r.get("price"), r.get("fv"), r.get("tag"))
        )
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    jsonl = path[:-3] + ".jsonl" if str(path).endswith(".md") else str(path) + ".jsonl"
    with open(jsonl, "a") as f:
        for r in rows or []:
            slim = {k: r.get(k) for k in ("ticker", "fee_true_ev", "price", "fv", "tag", "side")}
            slim["create_order"] = False
            f.write(json.dumps(slim) + "\n")
    return path


def append_live_ledger(row, path=None):
    """One weather create attempt. Name is weather_live_ledger. Not a fill by itself."""
    path = path or LIVE_LEDGER_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    slim = {k: row.get(k) for k in (
        "ticker", "side", "count", "price_cents", "price", "fv", "fee_true_ev",
        "fv_source", "fv_confidence", "edge",
        "reasons", "reason", "create_order",
    )}
    slim["ledger"] = "weather_live_ledger"
    slim["jev_oms"] = False
    with open(path, "a") as f:
        f.write(json.dumps(slim, default=str) + "\n")
    return path


def morning_scan(markets, fmap=None, bankroll=100.0, alert_path=None, ledger_path=None):
    """Score each daily weather bundle, Jev-stamp, shadow-ledger, alert on edge.

    ledger_path False skips the shadow file. Never sets create_order.
    """
    rows = []
    for m in markets or []:
        row = score(m, fmap=fmap, bankroll=bankroll)
        row["create_order"] = False
        row["dispatchable"] = 0
        rows.append(row)
    if rows and ledger_path is not False:
        append_scored(rows, path=ledger_path)
    alerts = [r for r in rows if _alertable(r)]
    alert_file = write_morning_alert(alerts, path=alert_path) if alerts else None
    return {
        "n": len(rows),
        "n_alert": len(alerts),
        "rows": rows,
        "alerts": alerts,
        "alert": alert_file,
        "create_order": False,
        "dispatchable": 0,
    }


def append_scored(rows, path=None):
    """Append would-have rows. Still dispatchable 0. No reconcile."""
    path = path or LEDGER_PATH
    header = {
        "desk": "weather", "mode": "RESEARCH/SHADOW", "live_exec": False,
        "live_writer": False, "fence": "LIVE_EXEC_FENCED",
        "valedia_version": vc.VALEDIA_VERSION,
    }
    body = desk.append_rows(path, rows, header)
    if os.path.abspath(path) == os.path.abspath(LEDGER_PATH):
        desk.mirror(path, "weather_shadow_ledger.json")
    return body
