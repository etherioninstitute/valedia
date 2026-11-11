"""eToro CopyTrader / Smart Portfolio paper desk. Intel only. No allocation.

  python3 etoro_copy_paper.py           # fixture book, no network
  python3 etoro_copy_paper.py --read    # GET rankings + smart-portfolio search
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from datetime import datetime, timezone

import etoro_client as ec
import valedia_config as vc

MODE = "PAPER/SHADOW"
SOURCE = "etoro_copy_paper"  # "paper" keeps Kelly / real_track_record off
MAX_TRADE = 25.0
MAX_DAILY = 100.0
MIN_SIZE = 5.0
MAX_DAILY_LOSS_PCT = 5.0
DECISION_KEEP = 400  # ponytail: tail rewrite. Dated archive when an audit needs the head.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DECISION_LOG = os.path.join(_ROOT, "data", "etoro-paper", "decisions.jsonl")

# gain is a decimal fraction (API). winRatio is 0-100. peakToValley > 1.5 is treated as percent.
GAIN_MIN = 0.10
RISK_LO, RISK_HI = 2, 6
COPIERS_MIN = 100
PTV_MAX = 0.25
WIN_MIN = 55.0
REFUSE_RISK = 8
REFUSE_PTV = 0.40
REFUSE_WIN = 45.0
REFUSE_COPIERS = 10


def _now():
    return datetime.now(timezone.utc).isoformat()


def ymd(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).date().isoformat()


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pick(row, *names):
    for n in names:
        if row.get(n) not in (None, ""):
            return row.get(n)
    return None


def _ptv(v):
    """Drawdown magnitude. API sends percent (often negative); fractions stay fractions."""
    x = _num(v)
    if x is None:
        return None
    if abs(x) > 1.5:
        x = x / 100.0
    return abs(x)


def _win(v):
    x = _num(v)
    if x is None:
        return None
    if 0 <= x <= 1:
        return x * 100.0
    return x


def normalize(row):
    """One ranking or search row. Missing stats stay None so they cannot allowlist."""
    if not isinstance(row, dict):
        return None
    kind = str(row.get("type") or row.get("Type") or "trader").strip().lower()
    if kind not in ("trader", "smart-portfolio"):
        kind = "trader"
    user = row.get("username") or row.get("userName") or ""
    if not user and row.get("cid") not in (None, ""):
        user = "cid:%s" % row.get("cid")
    if not user:
        return None
    return {
        "username": str(user),
        "type": kind,
        "gain": _num(_pick(row, "gain")),
        "risk_score": _num(_pick(row, "riskScore", "risk_score")),
        "copiers": _num(_pick(row, "copiers")),
        "peak_to_valley": _ptv(_pick(row, "peakToValley", "peak_to_valley")),
        "win_ratio": _win(_pick(row, "winRatio", "win_ratio")),
        "full_name": row.get("fullName") or "",
    }


def parse_rankings(body):
    rows = body.get("results") if isinstance(body, dict) else body
    return [n for n in (normalize(r) for r in rows or []) if n]


def parse_search(body):
    return parse_rankings(body)


def score(c):
    """allowlist / review_only / refuse from gain, risk, copiers, peak-to-valley, win ratio."""
    g, risk, cops = c.get("gain"), c.get("risk_score"), c.get("copiers")
    ptv, win = c.get("peak_to_valley"), c.get("win_ratio")
    if (g is not None and g < 0) or (risk is not None and risk >= REFUSE_RISK) or (
            ptv is not None and ptv > REFUSE_PTV) or (win is not None and win < REFUSE_WIN) or (
            cops is not None and cops < REFUSE_COPIERS):
        band = "refuse"
    elif (g is not None and g >= GAIN_MIN and risk is not None and RISK_LO <= risk <= RISK_HI
          and cops is not None and cops >= COPIERS_MIN and ptv is not None and ptv <= PTV_MAX
          and win is not None and win >= WIN_MIN):
        band = "allowlist"
    else:
        band = "review_only"
    parts = [
        0.0 if g is None else max(-1.0, min(1.0, g)),
        0.0 if risk is None else (RISK_HI - min(risk, 10)) / 10.0,
        0.0 if cops is None else min(cops, 1000.0) / 1000.0,
        0.0 if ptv is None else max(0.0, 1.0 - ptv),
        0.0 if win is None else max(0.0, min(win, 100.0)) / 100.0,
    ]
    c = dict(c)
    c["band"] = band
    c["score"] = round(sum(parts) / 5.0, 6)
    return c


def fresh_state(equity, now=None):
    return {"exposure_today_usd": 0.0, "realized_pnl_today_usd": 0.0,
            "starting_equity_usd": float(equity), "kill_switch": False, "date": ymd(now)}


def state_from_log(rows, equity, now=None, kill_switch=False):
    today, st = ymd(now), fresh_state(equity, now)
    for r in rows or []:
        if not str(r.get("ts") or "").startswith(today):
            continue
        if r.get("reason") == "would_copy":
            st["exposure_today_usd"] += float(r.get("size_usd") or 0)
        if r.get("realized_pnl_usd") is not None:
            st["realized_pnl_today_usd"] += float(r["realized_pnl_usd"])
    loss = (-st["realized_pnl_today_usd"] / max(float(equity), 1.0)) * 100.0
    st["loss_pct"] = loss
    st["kill_switch"] = bool(kill_switch) or loss >= MAX_DAILY_LOSS_PCT
    return st


def _limits(over=None):
    base = {"max_trade_size_usd": MAX_TRADE, "max_daily_notional_usd": MAX_DAILY,
            "min_size_usd": MIN_SIZE, "kill_switch": False}
    if over:
        base.update(over)
    return base


def _row(c, reason, size):
    c = c or {}
    return {
        "type": "decision", "source": SOURCE, "strategy": "SMART_PORTFOLIO" if c.get("type") == "smart-portfolio" else "COPYTRADER",
        "venue": "etoro-paper", "ticker": c.get("username") or "", "side": "would-copy",
        "band": c.get("band") or "", "reason": reason, "score": c.get("score"),
        "gain": c.get("gain"), "risk_score": c.get("risk_score"), "copiers": c.get("copiers"),
        "peak_to_valley": c.get("peak_to_valley"), "win_ratio": c.get("win_ratio"),
        "size_usd": round(float(size or 0), 6), "would_have_pnl": None,
        "would_copy": reason == "would_copy", "filled": False,
        "create_order": False, "start_copy": False, "live": False,
        "counts_toward_kelly": False, "paperTrading": True, "mode": MODE,
        "ts": _now(), "valedia_version": vc.VALEDIA_VERSION,
    }


def decide(c, state, limits=None, env=None):
    """Paper hypothetical only. create_order and start_copy stay false."""
    limits = _limits(limits)
    state = state or fresh_state(1000)
    env = os.environ if env is None else env
    if env.get("_attempt_copy"):
        return _row(c, ec.gate("copy_start", env), 0)
    if state.get("kill_switch") or limits.get("kill_switch"):
        return _row(c, "kill_switch", 0)
    band = c.get("band")
    if band == "refuse":
        return _row(c, "refuse", 0)
    if band != "allowlist":
        return _row(c, "review_only", 0)
    remaining = max(0.0, float(limits["max_daily_notional_usd"]) - float(state.get("exposure_today_usd") or 0))
    size = min(float(limits["max_trade_size_usd"]), remaining)
    if size <= 0:
        return _row(c, "daily_notional", 0)
    if size < float(limits["min_size_usd"]):
        return _row(c, "size_too_small", 0)
    return _row(c, "would_copy", size)


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
                "start_copy": False, "live": False, "counts_toward_kelly": False})
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


def run_scan(raw_rows, *, path=None, equity=1000.0, kill_switch=False, limits=None, env=None, now=None):
    path = path or DECISION_LOG
    state = state_from_log(read_decisions(path), equity, now=now, kill_switch=kill_switch)
    cands = [score(n) for n in (normalize(r) for r in (raw_rows or [])) if n]
    if not cands:
        row = _row({}, "refuse", 0)
        row["reason"] = "no_candidate"
        append_decision(row, path)
        return [row]
    rows = []
    for c in sorted(cands, key=lambda x: x.get("score") or 0, reverse=True):
        row = decide(c, state, limits=limits, env=env)
        append_decision(row, path)
        rows.append(row)
        if row.get("reason") == "would_copy":
            state["exposure_today_usd"] += float(row.get("size_usd") or 0)
    return rows


def intel_tags(rows):
    """Allowlist names other desks may read. Not a size and not an order."""
    out = []
    for r in rows or []:
        if r.get("band") == "allowlist" or r.get("reason") == "would_copy":
            out.append({"username": r.get("ticker") or r.get("username"),
                        "band": r.get("band") or "allowlist", "gain": r.get("gain"),
                        "risk_score": r.get("risk_score"), "source": SOURCE})
    return out


def tick_status(path=None, now=None, stale_s=7200):
    rows = read_decisions(path)
    last = rows[-1] if rows else None
    if not last:
        return {"last_tick_age_s": "none", "last_decision": "none", "stale": True, "n": 0}
    try:
        t = datetime.fromisoformat(str(last.get("ts")).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        age = max(0, int(((now or datetime.now(timezone.utc)) - t).total_seconds()))
    except (TypeError, ValueError):
        age = None
    return {"last_tick_age_s": "none" if age is None else age,
            "last_decision": str(last.get("reason") or "none"),
            "stale": age is None or age > int(stale_s), "n": len(rows)}


def live_enabled(env=None):
    return ec.live_flag(env)


def keys_present():
    return ec.keys_present()


def guarded_write(action, env=None, username="", amount=0):
    """Desk-facing fence. Returns the refuse code. Never calls the network."""
    code = ec.gate(action, env)
    return {"ok": False, "code": code, "username": username, "amount": amount,
            "create_order": False, "start_copy": False, "live": False}


def fixture_rows():
    return [
        {"username": "steady-pi", "type": "trader", "gain": 0.22, "riskScore": 4,
         "copiers": 800, "peakToValley": 0.12, "winRatio": 68},
        {"username": "mid-book", "type": "trader", "gain": 0.05, "riskScore": 5,
         "copiers": 40, "peakToValley": 0.18, "winRatio": 52},
        {"username": "blowup", "type": "trader", "gain": -0.15, "riskScore": 9,
         "copiers": 5, "peakToValley": 0.62, "winRatio": 30},
        {"username": "crypto-smart", "type": "smart-portfolio", "gain": 0.18, "riskScore": 3,
         "copiers": 1200, "peakToValley": 0.09, "winRatio": 71},
    ]


def _ok(client, fn):
    try:
        body = fn()
    except ec.EtoroError as exc:
        return exc.code
    return "ok" if body is not None else "empty"


def read_books(client):
    """GET rankings + smart-portfolio search. Search rows without stats stay review_only."""
    rank = client.rankings(period="OneYearAgo", sort="-copiers", page=1, page_size=5, popular_investor=True)
    rows = parse_rankings(rank)
    health = {
        "me": _ok(client, client.me),
        "instrument_types": _ok(client, client.instrument_types),
        "instrument_search": _ok(client, lambda: client.search_instruments("AAPL", limit=1)),
        "rankings": len(rows),
    }
    try:
        smart = client.search_portfolios("smart-portfolio", "crypto", limit=5)
        seen = {r["username"] for r in rows}
        added = 0
        for item in parse_search(smart):
            if item["username"] not in seen:
                rows.append(item)
                added += 1
        health["smart_portfolios"] = added
    except ec.EtoroError as exc:
        health["smart_portfolios"] = exc.code
    return rows, health


def _public(row):
    return {k: row.get(k) for k in (
        "ticker", "band", "reason", "size_usd", "create_order", "start_copy", "live", "score")}


def main(argv=None):
    p = argparse.ArgumentParser(description="eToro paper/shadow copy desk")
    p.add_argument("--read", action="store_true", help="GET rankings and smart portfolios. No writes.")
    p.add_argument("--out", default=DECISION_LOG)
    p.add_argument("--equity", type=float, default=1000.0)
    args = p.parse_args(argv)
    health = None
    if args.read:
        try:
            raw, health = read_books(ec.EtoroClient())
        except ec.EtoroError as exc:
            print(json.dumps({"ok": False, "code": exc.code}))
            return 2
        print(json.dumps({"ok": True, "health": health}))
    else:
        raw = fixture_rows()
    rows = run_scan(raw, path=args.out, equity=args.equity)
    for r in rows:
        print(json.dumps(_public(r)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
