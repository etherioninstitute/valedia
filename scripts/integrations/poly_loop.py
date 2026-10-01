"""POLY-LOOP: Polymarket US paper desk loop on POLY-LEARN.

Always runnable. Execution stays behind POLYMARKET_US_LIVE (default off).
This module never calls create/cancel. Proposals are DRY/SHADOW or BLOCKED.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone

import polymarket_us_client as pmu
import valedia_config as vc
from poly_learn import _load_json, _market_rows, cheap_tags

STATUS_DRY = "DRY/SHADOW"
STATUS_BLOCKED = "BLOCKED"
LEDGER_KEYS = (
    "ts", "market_id", "slug", "fv", "mid", "edge_pts", "size",
    "exit_condition", "reason", "status",
)
MIN_LIQUIDITY_USD = 5000.0
MIN_HOURS = 2.0
MAX_SLIPPAGE = 0.03
BANKROLL_CAP = 0.06
DEFAULT_BANKROLL = 100.0
SUBSCRIPTION_USD = 20.0
CRON_FLAG = "INSTALL_CRON"
FV_OVERRIDE_NAME = "fv_overrides.json"
# Mid-anchored favorite-longshot shrink: FV = (1-w)*mid + w*0.5.
# |FV-mid| = w*|mid-0.5|; w=0.5 → 8pt edge at mid 0.34/0.66.
TAG_PRIOR_W = 0.50
TAG_PRIOR_MU = 0.50

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
DEFAULT_DIR = os.path.join(_ROOT, "data", "poly-loop")
_MODELS = os.path.join(_ROOT, "models")
_FV_KEYS = ("fairValue", "fair_value", "fv", "p_yes", "modeled_prob")
_ID_KEYS = ("slug", "marketSlug", "ticker", "id", "conditionId",
            "eventSlug", "eventId", "event_id")
_CACHE_SKIP = frozenset({
    "regime", "ts", "version", "source", "fusion_touched", "auto_raise",
    "symbols", "markets", "events", "by_slug", "overrides", "schema",
    "note", "command",
})
# Official resolution label → nav bucket when classify returns OTHER.
_SRC_NAV = {
    "NWS": "WEATHER", "NOAA": "WEATHER",
    "MLB": "SPORTS", "NFL": "SPORTS", "NBA": "SPORTS", "NHL": "SPORTS",
    "NCAA": "SPORTS", "FIFA": "SPORTS",
    "Fed": "ECON", "FOMC": "ECON", "BLS": "ECON", "BEA": "ECON", "EIA": "ECON",
    "CDC": "WEATHER",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _clip01(x):
    return max(0.0, min(1.0, float(x)))


def _f(x):
    """Float or None. Unwraps US {value, currency} quotes."""
    if isinstance(x, dict):
        for k in ("value", "amount", "usd", "liquidity"):
            if x.get(k) not in (None, ""):
                x = x.get(k)
                break
        else:
            return None
        if isinstance(x, dict):
            x = x.get("value")
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _named(v):
    if isinstance(v, str) and v.strip():
        return v.strip()
    if isinstance(v, dict):
        s = v.get("url") or v.get("name") or v.get("source") or v.get("label")
        if s not in (None, ""):
            return str(s).strip()
    return None


# Explicit source fields (short name or URL). Long criteria text is not a name.
_NAMED_KEYS = (
    "resolutionSource", "resolution_source", "resolvedBy", "resolved_by",
    "resolver", "oracle", "resolutionURL", "resolution_url",
)
# US list/get_market + event: rules live in description (docs.polymarket.us).
_TEXT_KEYS = (
    "rules", "resolutionCriteria", "resolution_criteria", "description",
    "resolutionDescription", "rulesText", "rules_text", "resolutionRules",
    "settlementDescription", "rulesDisclaimer",
)
# ponytail: known-label scan, not an NLP resolver. Add a row if live text
# names a source we miss. Unknown prose → rules_text hash, not a vibe FV.
_OFFICIAL = (
    ("national weather service", "NWS"),
    ("associated press", "AP"),
    ("federal reserve", "Fed"),
    ("bureau of labor statistics", "BLS"),
    ("new york times", "NYT"),
    ("wall street journal", "WSJ"),
    ("white house", "White House"),
    ("supreme court", "SCOTUS"),
    ("sportradar", "Sportradar"),
    ("reuters", "Reuters"),
    ("bloomberg", "Bloomberg"),
    ("noaa", "NOAA"), ("nws", "NWS"), ("fomc", "FOMC"), ("bls", "BLS"),
    ("bea", "BEA"), ("cdc", "CDC"), ("eia", "EIA"), ("fed", "Fed"),
    ("ap", "AP"), ("nyt", "NYT"), ("wsj", "WSJ"), ("bbc", "BBC"),
    ("nfl", "NFL"), ("nba", "NBA"), ("mlb", "MLB"), ("nhl", "NHL"),
    ("ncaa", "NCAA"), ("fifa", "FIFA"), ("cme", "CME"),
)
_LAB = {k: v for k, v in _OFFICIAL}
_OFFICIAL_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k, _ in _OFFICIAL) + r")\b", re.I)
_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.I)


def _res_blobs(market):
    m = market if isinstance(market, dict) else {}
    out = [m]
    for nest in ("metadata", "event", "market"):
        inner = m.get(nest)
        if isinstance(inner, dict):
            out.append(inner)
    evs = m.get("events")
    if isinstance(evs, list):
        out.extend(e for e in evs[:4] if isinstance(e, dict))
    return out


def _short_named(v):
    s = _named(v)
    if not s:
        return None
    if s.lower().startswith("http"):
        return s.split()[0]
    if len(s) > 80 or s.count(" ") > 8:
        return None
    return s


def _plain(s):
    t = re.sub(r"<[^>]+>", " ", s)
    return " ".join(t.split())


def _text_chunks(market):
    chunks = []
    for blob in _res_blobs(market):
        for k in _TEXT_KEYS:
            v = blob.get(k)
            if isinstance(v, str) and v.strip():
                chunks.append(v.strip())
            elif isinstance(v, dict):
                s = v.get("text") or v.get("body") or v.get("html")
                if isinstance(s, str) and s.strip():
                    chunks.append(s.strip())
        res = blob.get("resolution")
        if isinstance(res, str) and (
                len(res.strip()) > 80 or res.count(" ") > 8):
            chunks.append(res.strip())
    return chunks


def _label_from_text(text):
    m = _OFFICIAL_RE.search(text)
    if m:
        return _LAB.get(m.group(0).lower())
    u = _URL_RE.search(text)
    if not u:
        return None
    host = u.group(0).split("://", 1)[-1].split("/")[0].split(":")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _rules_token(text):
    compact = _plain(text)
    h = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]
    return f"rules_text:{h}:{compact[:80]}"


def resolution_source(market):
    """Named source, extracted label, or rules_text hash. None if no text.

    Fail closed only when there is zero rules/description/resolution text.
    Question/title/slug alone is vibes — not a source.
    """
    m = market if isinstance(market, dict) else {}
    for blob in _res_blobs(m):
        for k in _NAMED_KEYS:
            s = _short_named(blob.get(k))
            if s:
                return s
        s = _short_named(blob.get("resolution"))
        if s:
            return s
    for t in m.get("tags") or []:
        if not isinstance(t, dict):
            continue
        league = t.get("league") if isinstance(t.get("league"), dict) else {}
        s = (_short_named(league.get("resolution"))
             or _short_named(t.get("resolution")))
        if s:
            return s
    chunks = _text_chunks(m)
    if not chunks:
        return None
    text = "\n".join(chunks)
    return _label_from_text(text) or _rules_token(text)


_LIQ_KEYS = ("liquidity", "liquidityNum", "liquidityClob", "liquidityUsd",
             "liquidityClobNum", "clobLiquidity", "openInterest")


def _liq_blobs(market):
    m = market if isinstance(market, dict) else {}
    blobs = [m]
    if isinstance(m.get("metadata"), dict):
        blobs.append(m["metadata"])
    inner = m.get("market") if isinstance(m.get("market"), dict) else None
    if inner:
        blobs.append(inner)
    return blobs


def liquidity_usd(market):
    best = 0.0
    for blob in _liq_blobs(market):
        for k in _LIQ_KEYS:
            v = _f(blob.get(k))
            if v is not None and v > best:
                best = v
    return best


def _liq_present(market):
    for blob in _liq_blobs(market):
        for k in _LIQ_KEYS:
            if blob.get(k) not in (None, ""):
                return True
    return False


def hours_left(market, now=None):
    m = market if isinstance(market, dict) else {}
    raw = None
    blobs = [m]
    for nest in ("event", "market"):
        if isinstance(m.get(nest), dict):
            blobs.append(m[nest])
    keys = ("endDate", "end_date", "endDateIso", "end_date_iso",
            "expiration", "expirationDate", "expiry", "closedTime",
            "closeTime", "closesAt", "endTime")
    for blob in blobs:
        for k in keys:
            if blob.get(k) not in (None, ""):
                raw = blob.get(k)
                break
        if raw is not None:
            break
    if raw is None:
        return None
    now = now or datetime.now(timezone.utc)
    if isinstance(raw, (int, float)):
        v = float(raw)
        if v > 1e12:
            v /= 1000.0
        dt = datetime.fromtimestamp(v, tz=timezone.utc)
    else:
        s = str(raw).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    return (dt - now).total_seconds() / 3600.0


def _dead(market):
    m = market if isinstance(market, dict) else {}
    for k in ("closed", "archived", "hidden"):
        v = m.get(k)
        if v in (True, 1, "1", "true", "True"):
            return True
    if m.get("active") in (False, 0, "0", "false", "False"):
        return True
    state = str(m.get("state") or m.get("ep3Status") or "").upper()
    return any(s in state for s in ("CLOSED", "EXPIRED", "TERMINATED", "ARCHIVED"))


def _fv_num(blob):
    if not isinstance(blob, dict):
        return None
    for k in _FV_KEYS:
        v = _f(blob.get(k))
        if v is not None:
            return _clip01(v)
    return None


def _market_ids(market):
    m = market if isinstance(market, dict) else {}
    blobs = [m]
    for nest in ("event", "market"):
        if isinstance(m.get(nest), dict):
            blobs.append(m[nest])
    out, seen = [], set()
    for blob in blobs:
        for k in _ID_KEYS:
            v = blob.get(k)
            if v in (None, ""):
                continue
            s = str(v)
            if s not in seen:
                seen.add(s)
                out.append(s)
    return out


def _norm_key(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def _fuzzy_fv(bag, ids):
    """Exact, normalized, then unique contains (len>=8). Scalar values ok."""
    if not isinstance(bag, dict) or not ids:
        return None
    want = [_norm_key(i) for i in ids]
    want_set = {w for w in want if w}
    index = {_norm_key(k): v for k, v in bag.items()}
    for raw, nk in zip(ids, want):
        hit = bag.get(raw)
        if hit is None:
            hit = index.get(nk)
        v = _fv_num(hit) if isinstance(hit, dict) else _f(hit)
        if v is not None:
            return _clip01(v)
    hits = []
    for nk, hit in index.items():
        if not nk or len(nk) < 8:
            continue
        if any(nk == w or (len(w) >= 8 and (nk in w or w in nk))
               for w in want_set):
            v = _fv_num(hit) if isinstance(hit, dict) else _f(hit)
            if v is not None:
                hits.append(round(v, 6))
    if len(set(hits)) == 1:
        return hits[0]
    return None


def _walk_cache(doc, ids):
    if not isinstance(doc, dict):
        return None
    for key in ("symbols", "markets", "events", "by_slug", "overrides"):
        bag = doc.get(key)
        if isinstance(bag, dict):
            v = _fuzzy_fv(bag, ids)
            if v is not None:
                return v
        elif isinstance(bag, list):
            want = {_norm_key(i) for i in ids}
            for row in bag:
                if not isinstance(row, dict):
                    continue
                row_ids = {_norm_key(x) for x in _market_ids(row)}
                if not (row_ids & want):
                    continue
                v = _fv_num(row)
                if v is not None:
                    return v
    slim = {k: v for k, v in doc.items() if k not in _CACHE_SKIP}
    return _fuzzy_fv(slim, ids)


def load_fv_overrides(out_dir=None):
    p = os.path.join(_dir(out_dir), FV_OVERRIDE_NAME)
    doc = _load_json(p)
    return doc if isinstance(doc, dict) else {}


def _override_fv(market, out_dir=None):
    doc = load_fv_overrides(out_dir)
    bag = doc.get("overrides") if isinstance(doc.get("overrides"), dict) else doc
    return _fuzzy_fv(bag, _market_ids(market)) if isinstance(bag, dict) else None


def _price_series(market):
    m = market if isinstance(market, dict) else {}
    for k in ("priceHistory", "price_history", "midHistory", "mids"):
        raw = m.get(k)
        if not isinstance(raw, list):
            continue
        out = []
        for x in raw:
            v = _f(x)
            if v is None and isinstance(x, dict):
                v = _f(x.get("p") or x.get("price") or x.get("mid")
                       or x.get("yes"))
            if v is not None:
                out.append(v)
        if len(out) >= 20:
            return out
    return []


def _modeled_fv(market):
    """EMA fair of a YES series via modeled_prob.ema_fair. Not P(up)."""
    prices = _price_series(market)
    if len(prices) < 20:
        return None
    try:
        from modeled_prob import ema_fair
        v = ema_fair(prices)
    except Exception:
        return None
    return _clip01(v) if v is not None else None


def _nav(market, tags=None):
    if isinstance(tags, dict):
        cat = tags.get("nav")
        if cat and cat != "OTHER":
            return cat
    try:
        import polymarket_entertainment as pe
        cat = pe.classify_market(market).get("category")
        if cat and cat != "OTHER":
            return cat
    except Exception:
        pass
    src = resolution_source(market) or ""
    if src.startswith("rules_text:"):
        return None
    return _SRC_NAV.get(src)


def _tag_prior(market, tags, mid):
    if mid is None:
        return None
    if not _nav(market, tags):
        return None
    return _clip01(
        (1.0 - TAG_PRIOR_W) * float(mid) + TAG_PRIOR_W * TAG_PRIOR_MU)


def estimate_fv_src(market, tags=None, mid=None, models_dir=None, out_dir=None):
    """(fv, source) with source in override|market|cache|modeled|tag_prior."""
    if not resolution_source(market):
        return None, None
    m = market if isinstance(market, dict) else {}
    v = _override_fv(m, out_dir=out_dir)
    if v is not None:
        return v, "override"
    v = _fv_num(m)
    if v is None and isinstance(tags, dict):
        v = _fv_num(tags)
    if v is not None:
        return v, "market"
    ids = _market_ids(m)
    if models_dir:
        for name in ("mirofish_neural_enrichment.json",
                     "persona_lens_scores.json"):
            v = _walk_cache(_load_json(os.path.join(models_dir, name)), ids)
            if v is not None:
                return v, "cache"
    v = _modeled_fv(m)
    if v is not None:
        return v, "modeled"
    v = _tag_prior(m, tags, mid)
    if v is not None:
        return v, "tag_prior"
    return None, None


def estimate_fv(market, tags=None, mid=None, models_dir=None, out_dir=None):
    """Require a resolution source. Fail closed if no numeric FV."""
    fv, _src = estimate_fv_src(
        market, tags=tags, mid=mid, models_dir=models_dir, out_dir=out_dir)
    return fv


def universe_skip(market, now=None):
    """Pull-time junk: closed/expired/low-liq. None = keep."""
    m = market if isinstance(market, dict) else {}
    if _dead(m):
        return "expired"
    hrs = hours_left(m, now=now)
    if hrs is None or hrs < MIN_HOURS:
        return "expired"
    if _liq_present(m) and liquidity_usd(m) < MIN_LIQUIDITY_USD:
        return "low_liq"
    return None


def skip_reason(market, *, book=None, bbo=None, tags=None, now=None,
                models_dir=None, out_dir=None):
    """Named skip or None if sizing may proceed. No vibes FV."""
    why = universe_skip(market, now=now)
    if why:
        return why
    if not resolution_source(market):
        return "no_resolution"
    tags = tags if tags is not None else cheap_tags(market, models_dir=models_dir)
    mid, _ = _mid_from(bbo or book or {})
    if mid is None and book is not None:
        mid, _ = _mid_from(book)
    fv, _src = estimate_fv_src(
        market, tags=tags, mid=mid, models_dir=models_dir, out_dir=out_dir)
    if fv is None:
        return "no_fv"
    if mid is None:
        return "no_mid"
    if not passes_edge(fv, mid):
        return "edge_lt_8"
    return None


def _block_key(reason):
    r = str(reason or "")
    if r in ("insufficient depth", "no book depth", "slippage", "no mid/size"):
        return "slip"
    return r or "blocked"


def edge_pts(fv, mid):
    if fv is None or mid is None:
        return 0.0
    return abs(float(fv) - float(mid)) * 100.0


def passes_edge(fv, mid, thresh=None):
    t = vc.POLY_LOOP_EDGE_PTS if thresh is None else thresh
    return edge_pts(fv, mid) >= float(t) - 1e-12


def _levels(doc, flatten_side):
    raw = doc if isinstance(doc, dict) else {}
    md = raw.get("marketData") if isinstance(raw.get("marketData"), dict) else raw
    key = "bids" if flatten_side == "sell" else (
        "offers" if md.get("offers") is not None else "asks")
    out = []
    for r in md.get(key) or []:
        if not isinstance(r, dict):
            continue
        px = pmu._amount_value(r.get("px") or r)
        qty = pmu._amount_value(
            r.get("qty") if "qty" in r else
            r.get("size") if "size" in r else
            r.get("quantity") if "quantity" in r else r.get("sz"))
        if px is not None and qty is not None and px > 0 and qty > 0:
            out.append((px, qty))
    return out


def flatten_ok(book, flatten_side, size_usd, mid, max_slip=MAX_SLIPPAGE):
    """True iff full size fills on the exit side at <= max_slip vs mid."""
    if mid is None or mid <= 0 or size_usd is None or size_usd <= 0:
        return False, None, "no mid/size"
    levels = _levels(book, flatten_side)
    if not levels:
        return False, None, "no book depth"
    need, filled, shares = float(size_usd), 0.0, 0.0
    for px, qty in levels:
        take = min(need - filled, px * qty)
        filled += take
        shares += take / px
        if filled >= need - 1e-9:
            break
    if filled < need - 1e-6:
        return False, None, "insufficient depth"
    vwap = filled / shares
    slip = abs(vwap - float(mid)) / float(mid)
    if slip > float(max_slip) + 1e-12:
        return False, slip, "slippage"
    return True, slip, "ok"


def kelly_capped(fv, mid, bankroll, cap_frac=BANKROLL_CAP):
    """Half-Kelly via quant_engine.kelly_size, then hard 6% bankroll cap."""
    from quant_engine import kelly_size
    br = float(bankroll)
    if br <= 0 or fv is None or mid is None:
        return 0.0, 0, 0.0, "buy"
    fv, mid = float(fv), float(mid)
    if fv >= mid:
        bet, n, f = kelly_size(fv, mid, br, fraction=vc.KELLY_SAFETY_FRACTION)
        side = "buy"
        px = mid
    else:
        bet, n, f = kelly_size(1.0 - fv, 1.0 - mid, br,
                               fraction=vc.KELLY_SAFETY_FRACTION)
        side = "sell"
        px = 1.0 - mid
    cap = br * float(cap_frac)
    if bet > cap:
        bet = round(cap, 2)
        n = max(1, int(bet / px)) if px > 0 else 0
    return float(bet or 0), int(n or 0), float(f or 0), side


def drawdown_halted(equity, peak, stop=None):
    stop = vc.POLY_LOOP_DD_STOP if stop is None else stop
    if not vc.POLY_LOOP_DD_STOP_ARMED:
        return False
    pk = float(peak or 0)
    if pk <= 0:
        return False
    return (pk - float(equity or 0)) / pk >= float(stop) - 1e-12


def self_fund(equity):
    sub = float(SUBSCRIPTION_USD)
    res = float(vc.TREASURY_FLOOR_POLYMARKET_US)
    eq = float(equity or 0)
    return {
        "paper_balance": round(eq, 2),
        "subscription": sub,
        "reserve": res,
        "need": round(sub + res, 2),
        "covers": eq >= (sub + res) - 1e-9,
        "withdraw": False,
        "note": "report only; no withdraw path",
    }


def _dir(out_dir=None, mkdir=False):
    d = out_dir or DEFAULT_DIR
    if mkdir:
        os.makedirs(d, exist_ok=True)
    return d


def state_path(out_dir=None):
    return os.path.join(_dir(out_dir), "state.json")


def ledger_path(out_dir=None):
    return os.path.join(_dir(out_dir), "ledger.jsonl")


def load_state(out_dir=None):
    p = state_path(out_dir)
    if not os.path.isfile(p):
        return {
            "equity": DEFAULT_BANKROLL, "peak": DEFAULT_BANKROLL,
            "open": [], "halted": False, "halt_reason": None,
        }
    try:
        with open(p) as f:
            st = json.load(f) or {}
    except Exception:
        st = {}
    st.setdefault("equity", DEFAULT_BANKROLL)
    st.setdefault("peak", st["equity"])
    st.setdefault("open", [])
    st.setdefault("halted", False)
    return st


def save_state(st, out_dir=None):
    _dir(out_dir, mkdir=True)
    p = state_path(out_dir)
    with open(p, "w") as f:
        json.dump(st, f, indent=2, default=str)
    return p


def append_ledger(row, out_dir=None):
    _dir(out_dir, mkdir=True)
    p = ledger_path(out_dir)
    with open(p, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")
    return p


def ledger_row(**kw):
    row = {k: kw.get(k) for k in LEDGER_KEYS}
    row["ts"] = row["ts"] or _now()
    return row


def write_post_mortem(st, out_dir=None):
    d = _dir(out_dir, mkdir=True)
    path = os.path.join(d, "post-mortem.json")
    eq, pk = float(st.get("equity") or 0), float(st.get("peak") or 0)
    dd = ((pk - eq) / pk) if pk else 0.0
    doc = {
        "ts": _now(),
        "slice": "POLY-LOOP",
        "peak": pk,
        "equity": eq,
        "drawdown": round(dd, 4),
        "stop": vc.POLY_LOOP_DD_STOP,
        "category_flip": False,
        "note": "stop proposing; no auto category flip",
    }
    with open(path, "w") as f:
        json.dump(doc, f, indent=2, default=str)
    return path, doc


def status_line(out_dir=None):
    st = load_state(out_dir)
    sf = self_fund(st.get("equity"))
    n_open = len(st.get("open") or [])
    live = bool(vc.polymarket_us_live_enabled())
    flag = os.path.isfile(os.path.join(_dir(out_dir), CRON_FLAG))
    return (
        f"open={n_open}/{vc.POLY_LOOP_MAX_OPEN} "
        f"dd_halted={int(bool(st.get('halted')))} "
        f"paper=${sf['paper_balance']:.2f} vs sub+res=${sf['need']:.2f} "
        f"covers={sf['covers']} live={int(live)} create=0 "
        f"cron_flag={int(flag)}"
    )


def _mid_from(doc):
    sized = pmu.sizing_from_bbo(doc if isinstance(doc, dict) else {})
    return sized.get("mid"), sized


def _loop_fee(market):
    """Known fee probability, or None. Missing is not zero."""
    m = market if isinstance(market, dict) else {}
    for k in ("fee_prob", "takerFee", "fee"):
        if k in m and m.get(k) is not None:
            try:
                return float(m.get(k))
            except (TypeError, ValueError):
                return None
    return None


def _create_eligible(rec):
    import informed_before_create as ibc
    return bool(ibc.create_eligible(rec))


def evaluate_one(market, *, book=None, bbo=None, tags=None, bankroll=100.0,
                 n_open=0, now=None, models_dir=None, out_dir=None):
    """Skip (None), BLOCKED row, or DRY/SHADOW row. Never places."""
    m = market if isinstance(market, dict) else {}
    slug = m.get("slug") or m.get("marketSlug") or m.get("ticker")
    mid_src = bbo or book or {}
    mid, sized = _mid_from(mid_src)
    if mid is None and book is not None:
        mid, sized = _mid_from(book)
    tags = tags if tags is not None else cheap_tags(m, models_dir=models_dir)
    if skip_reason(m, book=book, bbo=bbo, tags=tags, now=now,
                   models_dir=models_dir, out_dir=out_dir):
        return None
    fv, fv_src = estimate_fv_src(
        m, tags=tags, mid=mid, models_dir=models_dir, out_dir=out_dir)
    if fv is None or mid is None:
        return None
    bet, _n, _f, side = kelly_capped(fv, mid, bankroll)
    flatten_side = "sell" if side == "buy" else "buy"
    exit_c = f"flatten {flatten_side} @ <= {MAX_SLIPPAGE:.0%} slip or hrs<{MIN_HOURS:g}"
    base = dict(
        ts=_now(), market_id=m.get("id") or slug, slug=slug, fv=round(fv, 4),
        mid=round(float(mid), 4), edge_pts=round(edge_pts(fv, mid), 2),
        size=round(float(bet), 2), exit_condition=exit_c, reason="",
        status=STATUS_BLOCKED, side=side, tags=tags, fv_source=fv_src,
        spread=sized.get("spread"), create_order=False, create_eligible=False,
        fee_prob=_loop_fee(m), fv_confidence=fv_src in (
            "override", "market", "cache", "modeled"),
        mirofish_score=m.get("mirofish_score"),
    )
    if vc.trading_halted():
        base["reason"] = "halt"
        return base
    if n_open >= int(vc.POLY_LOOP_MAX_OPEN):
        base["reason"] = "max_open"
        return base
    if bet <= 0:
        base["reason"] = "kelly_zero"
        return base
    ok, slip, why = flatten_ok(book or bbo or {}, flatten_side, bet, mid)
    if not ok:
        base["reason"] = why
        return base
    base["status"] = STATUS_DRY
    base["slip"] = None if slip is None else round(slip, 4)
    base["create_order"] = False
    base["create_eligible"] = _create_eligible(base)
    base["reason"] = (
        f"edge {base['edge_pts']}pts fv={fv_src} src={resolution_source(m)!r} "
        f"nav={(tags or {}).get('nav')} slip={base['slip']}"
    )
    import nimble_wiring as _nw  # Nimble 2: last gate on a code-approved paper candidate
    base["nimble"] = nv = _nw.poly_gate(base, official=resolution_source(m) is not None)
    if not nv["allow"]:  # veto: paper row marked skipped; shadow never lands here
        base["status"], base["reason"], base["create_eligible"] = "SKIP_NIMBLE", _nw.why(nv), False
    return base


def _unwrap_market(doc):
    """US GET /v1/market/slug/{slug} wraps the row in {market: {...}}."""
    if not isinstance(doc, dict):
        return {}
    if isinstance(doc.get("status"), int) and doc["status"] >= 400:
        return {}
    inner = doc.get("market")
    if isinstance(inner, dict) and (
            inner.get("slug") or inner.get("question") or inner.get("endDate")):
        return inner
    return doc


def pull_markets(client, limit=20, now=None, stats=None):
    """Active+not-closed pull. Drops expired/zero-liq before the batch fills.

    Do not send endDateMin: gateway then ignores closed=false and returns
    stale NFL. closed/archived + client-side hours/liq filters are the drop.
    """
    now = now or datetime.now(timezone.utc)
    fetch = max(int(limit) * 5, 40)
    extra = {"closed": False, "archived": False}
    try:
        mkts = client.list_markets(limit=fetch, active=True, **extra)
    except Exception:
        mkts = None
    if not isinstance(mkts, dict) or (
            isinstance(mkts.get("status"), int) and mkts["status"] >= 400):
        mkts = client.list_markets(limit=fetch, active=True)
    dropped = {}
    out = []
    fetched = 0
    for row in _market_rows(mkts):
        fetched += 1
        slug = row.get("slug") or row.get("marketSlug")
        if not slug:
            dropped["no_slug"] = dropped.get("no_slug", 0) + 1
            continue
        why = universe_skip(row, now=now)
        if why:
            dropped[why] = dropped.get(why, 0) + 1
            continue
        try:
            mkt = _unwrap_market(client.get_market(slug))
        except Exception:
            mkt = {}
        blob = dict(row)
        if mkt:
            blob.update(mkt)
        blob.setdefault("slug", slug)
        why = universe_skip(blob, now=now)
        if why:
            dropped[why] = dropped.get(why, 0) + 1
            continue
        out.append(blob)
        if len(out) >= int(limit):
            break
    if isinstance(stats, dict):
        stats["dropped"] = dropped
        stats["fetched"] = fetched
        stats["query"] = extra
    return out


def _book_for(client, slug, books, bbos):
    if books and slug in books:
        return books[slug], bbos.get(slug) if bbos else None
    bbo = (bbos or {}).get(slug) if bbos else None
    book = None
    if client is not None:
        try:
            bbo = bbo or client.get_bbo(slug)
        except Exception:
            bbo = bbo or {}
        try:
            book = client.get_book(slug)
        except Exception:
            book = {}
    return book, bbo


def run(client=None, markets=None, books=None, bbos=None, out_dir=None,
        bankroll=None, models_dir=None, limit=20, now=None, state=None):
    """Pull → filter → size → ledger then propose. Never create."""
    d = _dir(out_dir, mkdir=True)
    st = state if state is not None else load_state(d)
    eq = float(st.get("equity") if bankroll is None else bankroll)
    pk = max(float(st.get("peak") or eq), eq)
    st["equity"], st["peak"] = eq, pk
    live = bool(vc.polymarket_us_live_enabled())
    cron_flag = os.path.isfile(os.path.join(d, CRON_FLAG))
    halted = bool(st.get("halted")) or drawdown_halted(eq, pk)
    proposals, blocked, skipped, err = [], [], 0, None
    skip_reasons = {}
    if halted:
        st["halted"] = True
        st["halt_reason"] = st.get("halt_reason") or "peak_drawdown"
        pm_path, pm = write_post_mortem(st, d)
        save_state(st, d)
        return {
            "ts": _now(), "slice": "POLY-LOOP",
            "valedia_version": vc.VALEDIA_VERSION,
            "status": STATUS_BLOCKED, "halted": True,
            "create_order": False, "live": live,
            "polymarket_us_live": live,
            "n": 0, "proposals": [], "blocked": [],
            "skipped": 0, "skip_reasons": {},
            "self_fund": self_fund(eq),
            "status_line": status_line(d),
            "post_mortem": pm_path, "post_mortem_doc": pm,
            "cron_flag": cron_flag,
            "cron_note": "flag present; this slice does not install cron"
            if cron_flag else "cron not installed",
        }
    rows = markets
    pull_stats = {}
    try:
        if rows is None:
            c = client or pmu.PolymarketUSClient(live=False)
            rows = pull_markets(c, limit=limit, now=now, stats=pull_stats)
            client = c
            for k, n in (pull_stats.get("dropped") or {}).items():
                skip_reasons[k] = skip_reasons.get(k, 0) + int(n)
                skipped += int(n)
    except Exception as e:
        err = str(e)[:200]
        rows = []
    open_pos = list(st.get("open") or [])
    for m in rows or []:
        slug = (m or {}).get("slug") or (m or {}).get("marketSlug")
        book, bbo = _book_for(client, slug, books, bbos)
        rec = evaluate_one(
            m, book=book, bbo=bbo, bankroll=eq, n_open=len(open_pos),
            now=now, models_dir=models_dir, out_dir=d,
        )
        if rec is None:
            why = skip_reason(
                m, book=book, bbo=bbo, now=now, models_dir=models_dir,
                out_dir=d,
            ) or "other"
            skip_reasons[why] = skip_reasons.get(why, 0) + 1
            skipped += 1
            continue
        # Ledger BEFORE propose (or BLOCKED).
        row = ledger_row(**rec)
        append_ledger(row, d)
        if rec["status"] == STATUS_DRY:
            proposals.append(rec)
            open_pos.append({
                "slug": rec["slug"], "size": rec["size"], "side": rec.get("side"),
                "fv": rec["fv"], "mid": rec["mid"],
            })
        else:
            blocked.append(rec)
            key = _block_key(rec.get("reason"))
            skip_reasons[key] = skip_reasons.get(key, 0) + 1
    st["open"] = open_pos[: int(vc.POLY_LOOP_MAX_OPEN)]
    save_state(st, d)
    doc = {
        "ts": _now(), "slice": "POLY-LOOP",
        "valedia_version": vc.VALEDIA_VERSION,
        "status": STATUS_DRY, "halted": False,
        "create_order": False, "live": live,
        "polymarket_us_live": live,
        "n": len(proposals), "proposals": proposals, "blocked": blocked,
        "skipped": skipped, "skip_reasons": skip_reasons, "error": err,
        "pull": {k: pull_stats.get(k) for k in ("fetched", "dropped", "query")
                 if k in pull_stats} or None,
        "self_fund": self_fund(eq),
        "status_line": status_line(d),
        "ledger": ledger_path(d), "state": state_path(d),
        "cron_flag": cron_flag,
        "cron_note": "flag present; this slice does not install cron"
        if cron_flag else "cron not installed",
        "note": "POLY-LOOP paper desk; create stays on POLYMARKET_US_LIVE + US client",
    }
    summary = os.path.join(d, "last_run.json")
    with open(summary, "w") as f:
        json.dump(doc, f, indent=2, default=str)
    doc["path"] = summary
    return doc


if __name__ == "__main__":
    print(json.dumps(run(models_dir=_MODELS), indent=2, default=str))
