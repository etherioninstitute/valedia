"""Poly↔Kalshi paper/shadow arb. Allowlist fills; heuristic matches are review-only.

Ornith must PASS (CE-1 shape) after the Kalshi taker fee. No venue order here; the gated
live promotion is arb_live.py (VALEDIA_ARB_LIVE default OFF → paper only, unchanged).

  python3 vaughan_arb_paper.py          # fixture book, no network
  python3 vaughan_arb_paper.py --read   # GET via polymarket_client + kalshi_auth.get_markets
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import urllib.error
from datetime import datetime, timezone

import crypto_paper_safety as cps
import kalshi_fee_map as kfm
import nimble_wiring as _nw
import valedia_config as vc

MODE = "PAPER/SHADOW"
SOURCE = "vaughan_arb_paper"  # "paper" keeps Kelly / real_track_record off
FEE_TABLE = "kalshi_fee_map.taker+poly_rate"
POLY_FEE_RATE = 0.0  # global CLOB paper assumption. Kalshi still pays the taker fee.
SLIP_BPS = 5
MIN_LEG = 0.03
MIN_SIMILARITY = 0.55
MIN_VOLUME = 1000.0
DEFAULT_SIZE = 25.0
MAX_TRADE = 25.0
MAX_DAILY_EXPOSURE = 100.0
MAX_DAILY_LOSS_PCT = 5.0
MIN_EDGE_PCT = 1.0
MIN_SIZE = 5.0
DECISION_KEEP = 400  # ponytail: tail rewrite. Dated archive when an audit needs the head.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DECISION_LOG = os.path.join(_ROOT, "data", "vaughan-arb", "decisions.jsonl")

# ArbitrageBot prediction-pairs, verified there 2026-05-11. Not on this list → never a fill.
_PAIR_ROWS = (
    ("will-judy-shelton-be-confirmed-as-fed-chair", "KXFEDCHAIRCONFIRM", "KXFEDCHAIRCONFIRM-JSHE"),
    ("will-kevin-warsh-be-confirmed-as-fed-chair", "KXFEDCHAIRCONFIRM", "KXFEDCHAIRCONFIRM-KWAR"),
    ("will-ted-cruz-win-the-2028-republican-presidential-nomination", "KXPRESNOMR-28", "KXPRESNOMR-28-TC"),
    ("will-jd-vance-win-the-2028-republican-presidential-nomination", "KXPRESNOMR-28", "KXPRESNOMR-28-JDV"),
    ("will-marco-rubio-win-the-2028-republican-presidential-nomination", "KXPRESNOMR-28", "KXPRESNOMR-28-MR"),
    ("will-ron-desantis-win-the-2028-republican-presidential-nomination", "KXPRESNOMR-28", "KXPRESNOMR-28-RDS"),
    ("will-tucker-carlson-win-the-2028-republican-presidential-nomination", "KXPRESNOMR-28", "KXPRESNOMR-28-TCAR"),
    ("will-kamala-harris-win-the-2028-democratic-presidential-nomination-641", "KXPRESNOMD-28", "KXPRESNOMD-28-KH"),
    ("will-josh-shapiro-win-the-2028-democratic-presidential-nomination-977", "KXPRESNOMD-28", "KXPRESNOMD-28-JS"),
    ("will-mark-kelly-win-the-2028-democratic-presidential-nomination-479", "KXPRESNOMD-28", "KXPRESNOMD-28-MK"),
)
PAIRS = [{"polymarket_slug": s, "kalshi_event_ticker": e, "kalshi_ticker": t} for s, e, t in _PAIR_ROWS]
_STOP = set("a an the be will is are on at in of to for by with and or this that it its as do does did can could would should has have than before after who what when win wins won".split())
_GENERIC = set("election elections presidential primary general race vote votes voting poll polls champion championship final finals tournament winner season series".split())

def _now():
    return datetime.now(timezone.utc).isoformat()

def ymd(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).date().isoformat()

def _unit(v, cents=False):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if cents:
        x = x / 100.0
    return x if 0 < x < 1 else None

def _vol(m):
    for k in ("volume24h", "volume_24h", "volume"):
        if m.get(k) not in (None, ""):
            try:
                return float(m[k])
            except (TypeError, ValueError):
                return 0.0
    return 0.0

def poly_from_gamma(m):
    """Asks only. A Gamma mid is not a buy price, so a missing no_ask is not a quote."""
    if not isinstance(m, dict):
        return None
    yes_raw = m.get("yes_ask") if m.get("yes_ask") not in (None, "") else m.get("bestAsk")
    yes, no = _unit(yes_raw), _unit(m.get("no_ask")) if m.get("no_ask") not in (None, "") else None
    if yes is None or no is None:
        return None
    return {"slug": m.get("slug") or "", "question": m.get("question") or "",
            "yes_ask": yes, "no_ask": no, "volume24h": _vol(m)}

def kalshi_from_api(m):
    """Dollar fields win. Integer asks are cents."""
    if not isinstance(m, dict):
        return None

    def one(dollar_key, cent_key):
        if m.get(dollar_key) not in (None, ""):
            return _unit(m.get(dollar_key), cents=False)
        if m.get(cent_key) not in (None, ""):
            return _unit(m.get(cent_key), cents=True)
        return None

    yes, no = one("yes_ask_dollars", "yes_ask"), one("no_ask_dollars", "no_ask")
    if yes is None or no is None:
        return None
    return {"ticker": m.get("ticker") or "", "event_ticker": m.get("event_ticker") or "",
            "title": m.get("title") or "", "subtitle": m.get("subtitle") or "",
            "yes_ask": yes, "no_ask": no, "volume24h": _vol(m)}

def _tokens(s):
    return {t for t in re.findall(r"[a-z0-9]+", str(s).lower()) if len(t) > 2 and t not in _STOP}

def _entities(s):
    out = set()
    for w in re.findall(r"[A-Za-z0-9]+", str(s)):
        if w.isdigit():
            if len(w) >= 2:
                out.add(w)
            continue
        low = w.lower()
        if len(w) >= 3 and w[0].isupper() and low not in _STOP and low not in _GENERIC:
            out.add(low)
    return out

def _jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)

def fee_true(poly_px, kal_px, size_usd, poly_fee_rate=POLY_FEE_RATE, slip_bps=SLIP_BPS, m=1.0):
    """Edge if one leg pays $1. Kalshi taker fee + poly rate + slip."""
    try:
        poly_px, kal_px, size_usd = float(poly_px), float(kal_px), float(size_usd)
    except (TypeError, ValueError):
        return None
    gross = poly_px + kal_px
    if gross <= 0 or size_usd <= 0 or poly_px < MIN_LEG or kal_px < MIN_LEG:
        return None
    contracts = size_usd / gross
    slip = 1.0 + float(slip_bps) / 10000.0
    poly_fill, kal_fill = poly_px * slip, kal_px * slip
    notional = (poly_fill + kal_fill) * contracts
    k_fee = kfm.fee(min(kal_fill, 0.99), contracts, m, role="taker")
    p_fee = poly_fill * contracts * float(poly_fee_rate)
    if p_fee > 0:
        p_fee = kfm.round_up_cent(p_fee)
    edge_usd = contracts - notional - k_fee - p_fee
    return {
        "contracts": round(contracts, 8), "poly_px": round(poly_fill, 6), "kal_px": round(kal_fill, 6),
        "raw_poly": poly_px, "raw_kal": kal_px, "notional": round(notional, 6),
        "entry_fee": round(k_fee + p_fee, 6), "edge_usd": round(edge_usd, 6),
        "edge_pct": round((edge_usd / notional * 100.0) if notional else -100.0, 6),
        "size_usd": round(size_usd, 6),
    }

def _best_build(p, k):
    best = None
    for poly_side, kal_side, pp, kp in (
        ("YES", "NO", p.get("yes_ask"), k.get("no_ask")),
        ("NO", "YES", p.get("no_ask"), k.get("yes_ask")),
    ):
        quote = fee_true(pp, kp, DEFAULT_SIZE)
        if quote and (best is None or quote["edge_pct"] > best["edge_pct"]):
            best = dict(quote)
            best["poly_side"], best["kal_side"] = poly_side, kal_side
    return best

def _candidate(p, k, sim, source):
    quote = _best_build(p, k)
    if not quote:
        return None
    quote.update({
        "source": "allowlist" if source == "allowlist" else "heuristic",
        "requires_review": source != "allowlist", "similarity": sim,
        "poly_slug": p.get("slug") or "", "kalshi_ticker": k.get("ticker") or "",
        "question": p.get("question") or "", "title": k.get("title") or "",
    })
    return quote

def scan(poly, kalshi, pairs=None, min_similarity=MIN_SIMILARITY, min_volume=MIN_VOLUME):
    pairs = PAIRS if pairs is None else pairs
    poly = [m for m in (poly or []) if isinstance(m, dict)]
    kalshi = [m for m in (kalshi or []) if isinstance(m, dict)]
    by_slug = {m.get("slug"): m for m in poly if m.get("slug")}
    by_ticker = {m.get("ticker"): m for m in kalshi if m.get("ticker")}
    by_event = {}
    for m in kalshi:
        by_event.setdefault(m.get("event_ticker") or "", []).append(m)
    out, seen = [], set()
    for pair in pairs:
        p = by_slug.get(pair.get("polymarket_slug"))
        if not p:
            continue
        k = by_ticker.get(pair.get("kalshi_ticker")) if pair.get("kalshi_ticker") else None
        if k is None:
            ks = by_event.get(pair.get("kalshi_event_ticker") or "") or []
            k = ks[0] if ks else None
        cand = _candidate(p, k, 1.0, "allowlist") if k else None
        if not cand:
            continue
        seen.add((p.get("slug"), k.get("ticker")))
        out.append(cand)
    for p in poly:
        if _vol(p) < min_volume:
            continue
        pe, pt = _entities(p.get("question") or ""), _tokens(p.get("question") or "")
        best = None
        for k in kalshi:
            if (p.get("slug"), k.get("ticker")) in seen or _vol(k) < min_volume:
                continue
            text = f"{k.get('title') or ''} {k.get('subtitle') or ''}"
            ke = _entities(text)
            # both sides naming a different entity, or no shared entity → not a pair
            if (any(t not in ke for t in pe) and any(t not in pe for t in ke)) or len(pe & ke) < 1:
                continue
            sim = _jaccard(pt, _tokens(text))
            if sim >= min_similarity and (best is None or sim > best[0]):
                best = (sim, k)
        if best:
            cand = _candidate(p, best[1], best[0], "heuristic")
            if cand:
                out.append(cand)
    return out

def fresh_state(equity, now=None):
    return {"exposure_today_usd": 0.0, "realized_pnl_today_usd": 0.0,
            "starting_equity_usd": float(equity), "kill_switch": False, "date": ymd(now)}

def state_from_log(rows, equity, now=None, kill_switch=False):
    today, st = ymd(now), fresh_state(equity, now)
    for r in rows or []:
        if not str(r.get("ts") or "").startswith(today):
            continue
        if r.get("filled"):
            st["exposure_today_usd"] += float(r.get("size_usd") or 0)
        if r.get("realized_pnl_usd") is not None:
            st["realized_pnl_today_usd"] += float(r["realized_pnl_usd"])
    loss = (-st["realized_pnl_today_usd"] / max(float(equity), 1.0)) * 100.0
    st["loss_pct"] = loss
    st["kill_switch"] = bool(kill_switch) or loss >= MAX_DAILY_LOSS_PCT
    return st

def _on(env, name):
    return str(env.get(name) or "").strip().lower() in ("1", "true", "yes", "on")

def triple_gate(env=None):
    """Paper only. Does not write env or config. Omitted env reads the process."""
    env = os.environ if env is None else env
    if vc.POLYMARKET_US_LIVE or _on(env, "POLYMARKET_US_LIVE"):
        return "poly_live"
    if env is os.environ and vc.polymarket_us_live_enabled():
        return "poly_live"
    if vc.trading_halted():
        return "halt"
    mode = str(env.get("TRADING_MODE") or "paper").strip().lower()
    if mode not in ("paper", "shadow", "paper/shadow"):
        return "not_paper"
    if any(v.strip() for v in str(env.get("LIVE_VENUES") or "").split(",")):
        return "half_live"
    dry = str(env.get("EXECUTION_DRY_RUN") if env.get("EXECUTION_DRY_RUN") is not None else "true").strip().lower()
    if dry in ("false", "0", "no", "off"):
        return "dry_run_off"
    return ""

def _limits(over=None):
    base = {"max_trade_size_usd": MAX_TRADE, "max_daily_exposure_usd": MAX_DAILY_EXPOSURE,
            "max_daily_loss_pct": MAX_DAILY_LOSS_PCT, "min_edge_pct": MIN_EDGE_PCT,
            "min_size_usd": MIN_SIZE, "kill_switch": False}
    if over:
        base.update(over)
    return base

def _size_for(cand, state, limits):
    remaining = max(0.0, float(limits["max_daily_exposure_usd"]) - float(state.get("exposure_today_usd") or 0))
    requested = min(float(cand.get("size_usd") or DEFAULT_SIZE), float(limits["max_trade_size_usd"]))
    return min(requested, remaining)

def _risk(quote, state, limits):
    if state.get("kill_switch") or limits.get("kill_switch"):
        return "kill_switch"
    equity = max(float(state.get("starting_equity_usd") or 1), 1.0)
    loss = (-float(state.get("realized_pnl_today_usd") or 0) / equity) * 100.0
    if loss >= float(limits["max_daily_loss_pct"]):
        return "daily_loss"
    if float(state.get("exposure_today_usd") or 0) >= float(limits["max_daily_exposure_usd"]):
        return "daily_exposure"
    if quote is None or quote["size_usd"] < float(limits["min_size_usd"]):
        return "size_too_small"
    if quote["edge_pct"] < float(limits["min_edge_pct"]):
        return "fee_edge"
    return ""

def _prompt(conds):
    brief = [{"id": c["id"], "op": "eq", "actual": c["actual"], "threshold": True, "local_pass": c["pass"]}
             for c in conds]
    return (
        "Fail-closed PAPER safety check. Do not place an order. "
        "Reply with one JSON object only: pass, bias, failed_conditions, evidence, "
        "model exactly ornith-1.5-k-m. pass is true only when every required id passes.\n"
        + json.dumps({"required": [c["id"] for c in conds], "bias": "neutral", "conditions": brief})
    )

def _conds(quote, limits):
    rows = (("paper_mode", True), ("allowlisted", True),
            ("fee_edge", quote["edge_pct"] >= float(limits["min_edge_pct"])),
            ("risk_ok", True), ("not_half_live", True))
    return [{"id": i, "pass": bool(v), "actual": v, "threshold": True} for i, v in rows]

def ask_ornith(conds, post_fn=None, timeout=20):
    """CE-1 fail-closed shape. Down, bad JSON, Qwen, or a missing id refuses."""
    required = [c["id"] for c in conds]
    payload = {"model": cps.MODEL, "messages": [{"role": "user", "content": _prompt(conds)}],
               "max_tokens": 800, "temperature": 0, "reasoning_effort": "none"}
    try:
        raw = post_fn(payload) if post_fn else cps._http_post(cps.DEFAULT_URL, payload, timeout)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return "ornith_down", "down"
    except Exception as exc:
        if isinstance(exc, AssertionError):
            raise
        return "ornith_down", "down"
    obj = cps.extract_json(cps._content(raw))
    shape = cps._ornith_shape(obj, required) if obj else "malformed"
    model = str((obj or {}).get("model") or "")
    if shape:
        return shape, model or "malformed"
    if not obj.get("pass"):
        return "ornith_refuse", model
    return "pass", model

def _row(cand, reason, model, filled, quote):
    quote = quote or {}
    ok = bool(filled) and reason == "pass"
    return {
        "type": "decision", "source": SOURCE, "strategy": "PREDICTION_PAIR", "venue": "paper",
        "ticker": f"{cand.get('poly_slug') or ''}|{cand.get('kalshi_ticker') or ''}",
        "side": "buy", "contracts": quote.get("contracts") or 0, "qty": quote.get("contracts") or 0,
        "entry_price": None if not quote else round(quote["poly_px"] + quote["kal_px"], 6),
        "entry_fee": quote.get("entry_fee") or 0, "fee_table": FEE_TABLE,
        "fee_assumption": {"table": FEE_TABLE, "kalshi_role": "taker", "kalshi_coef": kfm.TAKER_COEF,
                           "poly_fee_rate": POLY_FEE_RATE, "slip_bps": SLIP_BPS, "m": 1},
        "reason": reason, "pass": reason == "pass", "filled": ok, "ornith": reason, "model": model,
        "would_have_pnl": quote.get("edge_usd") if ok else None, "edge_pct": quote.get("edge_pct"),
        "size_usd": quote.get("size_usd") or 0, "requires_review": bool(cand.get("requires_review")),
        "source_match": cand.get("source") or "", "create_order": False, "live": False,
        "inferred": True, "counts_toward_kelly": False, "paperTrading": True, "mode": MODE,
        "ts": _now(), "valedia_version": vc.VALEDIA_VERSION,
    }

def decide(cand, state, limits=None, env=None, post_fn=None, timeout=20):
    limits, state = _limits(limits), state or fresh_state(1000)
    quote = fee_true(cand.get("raw_poly"), cand.get("raw_kal"), _size_for(cand, state, limits))
    gate = triple_gate(env)
    if gate:
        return _row(cand, gate, "none", False, quote)
    if vc.ticker_family_fenced(cand.get("kalshi_ticker")):
        return _row(cand, "fenced_series", "none", False, quote)
    if cand.get("requires_review") or cand.get("source") != "allowlist":
        return _row(cand, "review_only", "none", False, quote)
    why = _risk(quote, state, limits)
    if why:
        return _row(cand, why, "none", False, quote)
    reason, model = ask_ornith(_conds(quote, limits), post_fn=post_fn, timeout=timeout)
    nv = _nw.vaughan_gate(cand, quote) if reason == "pass" else None  # Nimble 2: last gate
    row = _row(cand, reason, model, reason == "pass" and (nv is None or nv["allow"]), quote)
    if nv is not None:
        row["nimble"] = nv
        if not nv["allow"]:  # veto: not filled (shadow never lands here); ornith stays "pass"
            row["reason"], row["pass"] = "SKIP_NIMBLE", False
    return row

def read_decisions(path=None):
    path = path or DECISION_LOG
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out

def append_decision(row, path=None, keep=DECISION_KEEP):
    path = path or DECISION_LOG
    row = dict(row or {})
    row.update({"type": "decision", "source": SOURCE, "create_order": False,
                "live": False, "counts_toward_kelly": False})
    row.setdefault("fee_table", FEE_TABLE)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    line = json.dumps(row, default=str) + "\n"
    with open(path + ".write.lock", "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            with open(path, "a") as f:
                f.write(line)
            if keep and keep > 0:
                with open(path) as f:
                    lines = f.readlines()
                if len(lines) > keep:
                    with open(path, "w") as f:
                        f.writelines(lines[-keep:])
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return path

def run_scan(poly, kalshi, *, path=None, post_fn=None, equity=1000.0, env=None,
             limits=None, kill_switch=False, pairs=None, timeout=20, now=None):
    path = path or DECISION_LOG
    state = state_from_log(read_decisions(path), equity, now=now, kill_switch=kill_switch)
    cands = scan(poly, kalshi, pairs=pairs)
    if not cands:
        row = _row({}, triple_gate(env) or "no_pair", "none", False, None)
        append_decision(row, path)
        return [row]
    rows = []
    for cand in sorted(cands, key=lambda c: c.get("edge_pct") or 0, reverse=True):
        row = decide(cand, state, limits=limits, env=env, post_fn=post_fn, timeout=timeout)
        import arb_live  # gated live promotion (VALEDIA_ARB_LIVE default OFF → None, paper only)
        live = arb_live.maybe_promote(cand, env=env)
        if live is not None:
            row["arb_live"] = live
        append_decision(row, path)
        rows.append(row)
        if row.get("filled"):
            state["exposure_today_usd"] += float(row.get("size_usd") or 0)
    return rows

def tick_status(path=None, now=None, stale_s=7200):
    rows = read_decisions(path)
    last = rows[-1] if rows else None
    if not last:
        return {"last_tick_age_s": "none", "last_ornith": "none", "stale": True, "n": 0}
    try:
        t = datetime.fromisoformat(str(last.get("ts")).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        age = max(0, int(((now or datetime.now(timezone.utc)) - t).total_seconds()))
    except (TypeError, ValueError):
        age = None
    return {"last_tick_age_s": "none" if age is None else age,
            "last_ornith": str(last.get("ornith") or last.get("reason") or "none"),
            "stale": age is None or age > int(stale_s), "n": len(rows)}

def read_books():
    """GET-only. No order method is called."""
    import kalshi_auth as ka
    import polymarket_client as pc
    raw_poly = pc.get_active_markets(limit=100)
    raw_k = ka.get_markets(limit=200, status="open")
    markets = raw_k.get("markets") if isinstance(raw_k, dict) else []
    return ([q for q in (poly_from_gamma(m) for m in raw_poly) if q],
            [q for q in (kalshi_from_api(m) for m in markets) if q])

def fixture_books():
    """Offline book: one allowlisted fat edge, one heuristic review, one contradiction."""
    def P(slug, q, y, n):
        return {"slug": slug, "question": q, "yes_ask": y, "no_ask": n, "volume24h": 5000}
    def K(tk, ev, title, y, n):
        return {"ticker": tk, "event_ticker": ev, "title": title, "subtitle": "",
                "yes_ask": y, "no_ask": n, "volume24h": 5000}
    poly = [
        P("will-judy-shelton-be-confirmed-as-fed-chair", "Will Judy Shelton be confirmed as Fed Chair?", 0.30, 0.72),
        P("will-alice-chen-win-the-2028-nomination", "Will Alice Chen win the 2028 nomination?", 0.20, 0.82),
        P("will-bob-ng-win-the-2028-nomination", "Will Bob Ng win the 2028 nomination?", 0.20, 0.82),
    ]
    kalshi = [
        K("KXFEDCHAIRCONFIRM-JSHE", "KXFEDCHAIRCONFIRM", "Judy Shelton confirmed as Fed Chair", 0.62, 0.40),
        K("KXNOTONLIST-AC", "KXNOTONLIST", "Alice Chen wins the 2028 nomination", 0.70, 0.32),
        K("KXNOTONLIST-BN", "KXNOTONLIST", "Alice Chen wins the 2028 nomination", 0.70, 0.32),
    ]
    return poly, kalshi

def main(argv=None):
    p = argparse.ArgumentParser(description="Poly-Kalshi paper/shadow scan")
    p.add_argument("--read", action="store_true", help="GET venue books. Still no orders.")
    p.add_argument("--out", default=DECISION_LOG)
    p.add_argument("--equity", type=float, default=1000.0)
    args = p.parse_args(argv)
    poly, kalshi = read_books() if args.read else fixture_books()
    rows = run_scan(poly, kalshi, path=args.out, equity=args.equity)
    for r in rows:
        print(json.dumps({k: r.get(k) for k in (
            "ticker", "reason", "pass", "filled", "create_order", "edge_pct", "would_have_pnl", "live")}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
