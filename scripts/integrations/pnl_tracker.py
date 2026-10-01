"""Valedia P&L Tracker — The Missing Feedback Loop
═══════════════════════════════════════════════════════════════════════════
Tracks ALL trades across ALL platforms with actual settlement results.
This is what makes Valedia learn from her mistakes.

Without this, Valedia reports "100% win rate" while losing money.

(c) GOATWORTHY Post LLC. All rights reserved.
"""
import os, sys, json, time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
MODELS = os.path.expanduser("~/.openclaw/workspace/models")
PNL_PATH = os.path.join(MODELS, "pnl_tracker.json")
LESSONS = os.path.expanduser("~/.openclaw/workspace/tasks/lessons.md")

def _load_env():
    env_path = os.path.expanduser("~/.openclaw/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())
_load_env()

def load_pnl():
    if os.path.exists(PNL_PATH):
        try: return json.load(open(PNL_PATH))
        except: pass
    return {"trades": [], "summary": {"total_deposited": 170, "wins": 0, "losses": 0,
            "total_won": 0, "total_lost": 0, "strategies": {}}}

def save_pnl(data):
    with open(PNL_PATH, "w") as f:
        json.dump(data, f, indent=2)

def track_kalshi_pnl():
    """Pull ALL Kalshi positions, detect settled ones, compute real P&L."""
    from kalshi_auth import get, get_balance, get_positions
    
    pnl = load_pnl()
    tracked_tickers = {t["ticker"] for t in pnl["trades"]}
    
    # Get all positions including settled (qty=0)
    pos = get_positions()
    bal = get_balance()
    
    new_settlements = []
    for p in pos.get("market_positions", []):
        ticker = p.get("ticker", "")
        qty = float(p.get("position_fp", 0))
        exp = float(p.get("market_exposure_dollars", 0))
        rpnl = float(p.get("realized_pnl_dollars", 0))
        
        # Check if market is settled
        try:
            m = get(f"/markets/{ticker}")
            mkt = m.get("market", m)
            status = mkt.get("status", "")
            result = mkt.get("result", "")
        except:
            status = "unknown"; result = ""
        
        if status == "finalized" and ticker not in tracked_tickers:
            # Determine our side from qty sign or order history
            our_side = "no" if qty < 0 or (qty == 0 and exp == 0) else "yes"
            
            # Did we win?
            won = (our_side == result) if result else None
            profit = rpnl if rpnl != 0 else (exp if won else -exp)
            
            trade = {
                "ticker": ticker, "side": our_side, "result": result,
                "status": "WON" if won else "LOST" if won is not None else "UNKNOWN",
                "cost": exp, "profit": round(profit, 2),
                "settled_at": datetime.now(timezone.utc).isoformat(),
                "market_status": status,
            }
            # Detect strategy from ticker
            if "KXHIGH" in ticker or "KXLOW" in ticker:
                trade["strategy"] = "WEATHER_ARB"
            elif "KXETH" in ticker or "KXBTC" in ticker:
                trade["strategy"] = "CRYPTO_BRACKET"
            elif "KXWTI" in ticker:
                trade["strategy"] = "OIL_BRACKET"
            elif "KXNBA" in ticker or "KXNHL" in ticker:
                trade["strategy"] = "SPORTS"
            elif "KXCPI" in ticker or "KXGDP" in ticker or "KXFED" in ticker:
                trade["strategy"] = "MACRO"
            else:
                trade["strategy"] = "OTHER"
            
            pnl["trades"].append(trade)
            new_settlements.append(trade)
            tracked_tickers.add(ticker)
    
    # Recompute summary
    wins = sum(1 for t in pnl["trades"] if t["status"] == "WON")
    losses = sum(1 for t in pnl["trades"] if t["status"] == "LOST")
    total_won = sum(t["profit"] for t in pnl["trades"] if t["status"] == "WON")
    total_lost = sum(abs(t["profit"]) for t in pnl["trades"] if t["status"] == "LOST")
    
    # Strategy breakdown
    strategies = {}
    for t in pnl["trades"]:
        s = t.get("strategy", "OTHER")
        if s not in strategies:
            strategies[s] = {"wins": 0, "losses": 0, "profit": 0, "cost": 0}
        if t["status"] == "WON":
            strategies[s]["wins"] += 1
            strategies[s]["profit"] += t["profit"]
        elif t["status"] == "LOST":
            strategies[s]["losses"] += 1
            strategies[s]["cost"] += abs(t["profit"])
    
    pnl["summary"] = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "total_trades": len(pnl["trades"]),
        "wins": wins, "losses": losses,
        "win_rate": round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0,
        "total_won": round(total_won, 2),
        "total_lost": round(total_lost, 2),
        "net_pnl": round(total_won - total_lost, 2),
        "current_balance": round(bal.get("balance", 0) / 100, 2),
        "current_deployed": round(bal.get("portfolio_value", 0) / 100, 2),
        "strategies": strategies,
    }
    
    # Write lessons for any losing strategies
    if new_settlements:
        with open(LESSONS, "a") as f:
            for t in new_settlements:
                if t["status"] == "LOST":
                    f.write(f'{datetime.now().strftime("%Y-%m-%d")} | {t["strategy"]} LOSS: {t["ticker"]} '
                            f'cost=${t["cost"]:.2f} result={t["result"]} | '
                            f'Strategy {t["strategy"]} lost — review and possibly disable\n')
    
    save_pnl(pnl)
    return pnl["summary"], new_settlements

def get_strategy_kill_list():
    """REPORT-ONLY. Win-rate is not a live fuse (Slice 9 / P1.4).

    Fat-tailed winners (low WR, fee-true net>0) must not be killed — that fight
    the category gate. Returned for the daily report / wr_report field only.
    """
    pnl = load_pnl()
    kills = []
    for strat, data in pnl["summary"].get("strategies", {}).items():
        total = data["wins"] + data["losses"]
        if total >= 3:
            wr = data["wins"] / total * 100
            net = data.get("profit", 0) - data.get("cost", 0)
            if wr < 25:
                kills.append({"strategy": strat, "win_rate": round(wr, 1),
                              "wins": data["wins"], "losses": data["losses"],
                              "total_lost": data.get("cost", 0),
                              "net": round(net, 2),
                              "fuse": "report_only"})
    return kills

STRATEGY_BLACKLIST_PATH = os.path.join(MODELS, "strategy_blacklist.json")


# ── Slice 9 learning pause ──────────────────────────────────────────────
# Blacklist file is a PAUSE with TTL, not a life sentence.
# Pause on fee-true NET (category_stats when available), never raw WR after 3 trades.
# TTL expiry → probation (eligible for evaluation/sizing again).
# Evidence: category_stats n>=min and net>=0 → unpause immediately.
# Legacy blacklisted[] with no `until` is expired (a grave is invalid).
# Live-exec fences (weather create_order / scalper alp_buy) stay in valedia_config.


def _as_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_iso(s):
    if not s:
        return None
    try:
        return _as_utc(datetime.fromisoformat(str(s).replace("Z", "+00:00")))
    except Exception:
        return None


def _canon(name):
    n = str(name or "").strip()
    try:
        import forward_test_ledger as _ftl
        return _ftl.canonical_category(n)
    except Exception:
        return n


def _pause_ttl_days():
    try:
        import valedia_config as _c
        return int(_c.PAUSE_TTL_DAYS)
    except Exception:
        return 7


def _pause_min_n_block_net():
    try:
        import valedia_config as _c
        return int(_c.CATEGORY_MIN_TRADES), float(_c.CATEGORY_BLOCK_NET)
    except Exception:
        return 4, 0.0


def _fee_true_stats():
    try:
        import forward_test_ledger as _ftl
        return _ftl.category_stats() or {}
    except Exception:
        return {}


def _read_raw_registry(path=None):
    p = path or STRATEGY_BLACKLIST_PATH
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except Exception:
            return {}
    return {}


def _pnl_net_stats():
    """Fee-BLIND net from pnl_tracker.json (profit-cost). REPORT-ONLY.

    Never feeds pause/unpause or should_trade_category: pnl_tracker.json has no
    verified fees, so it cannot prove a category loses (or wins) real money.
    """
    out = {}
    for strat, data in (load_pnl().get("summary") or {}).get("strategies", {}).items():
        n = int(data.get("wins", 0) or 0) + int(data.get("losses", 0) or 0)
        net = float(data.get("profit", 0) or 0) - float(data.get("cost", 0) or 0)
        out[_canon(strat)] = {
            "n": n, "wins": int(data.get("wins", 0) or 0),
            "net": round(net, 2), "source": "fee_blind", "report_only": True,
        }
    return out


def _fee_blind_report():
    """Labeled fee-blind figures for the registry/report. Fail-soft: [] on error."""
    try:
        return [dict(v, strategy=k) for k, v in sorted(_pnl_net_stats().items())]
    except Exception:
        return []


def _merged_stats(stats=None):
    """Fee-true category_stats ONLY — the sole input to pause/unpause.

    Fee-blind net is never merged in (no gap-fill): a category with no fee-true
    stats is "unproven" -> not paused (fail-open, like
    forward_test_ledger.should_trade_category). Name kept for back-compat.
    """
    fee = dict(stats) if stats is not None else _fee_true_stats()
    merged = {}
    for k, v in fee.items():
        merged[_canon(k)] = {
            "n": int(v.get("n") or 0), "wins": int(v.get("wins") or 0),
            "net": float(v.get("net") or 0), "source": "category_stats",
        }
    return merged


def load_pause_registry(path=None, now=None):
    """In-memory pause view. Expired / legacy-no-until are NOT paused."""
    now = _as_utc(now or datetime.now(timezone.utc))
    raw = _read_raw_registry(path)
    live = []
    for p in raw.get("paused") or []:
        until = _parse_iso(p.get("until"))
        if until is None:
            continue  # no until = not a life sentence
        if now < until:
            live.append(dict(p))
    probation = []
    for x in raw.get("probation") or []:
        until = _parse_iso(x.get("until"))
        if until is not None and now < until:
            probation.append(dict(x))
    return {
        "paused": live,
        "probation": probation,
        "history": list(raw.get("history") or []),
        "updated": raw.get("updated"),
        "wr_report": list(raw.get("wr_report") or raw.get("details") or []),
        "fee_blind_report": list(raw.get("fee_blind_report") or []),
        "blacklisted": [p.get("strategy") for p in live],
    }


def paused_until(strategy_name, now=None, path=None):
    """Datetime (UTC) when pause lifts, or None if not currently paused."""
    cat = _canon(strategy_name)
    for p in load_pause_registry(path=path, now=now).get("paused") or []:
        if _canon(p.get("strategy")) == cat:
            return _parse_iso(p.get("until"))
    return None


def is_strategy_paused(strategy_name, now=None, path=None):
    """True only while a learning pause is unexpired. Fail-open on errors."""
    try:
        return paused_until(strategy_name, now=now, path=path) is not None
    except Exception:
        return False


def is_strategy_blacklisted(strategy_name):
    """Back-compat reader. Delegates to is_strategy_paused (TTL-aware)."""
    return is_strategy_paused(strategy_name)


def update_strategy_blacklist(now=None, stats=None, path=None):
    """Write pause registry from fee-true NET. Report-only WR is stored, not fused.

    ONLY fee-true category_stats may pause/unpause. Fee-blind pnl_tracker net is
    stored as `fee_blind_report` (report_only) and never gates; no fee-true
    stats (or a loader error) = unproven = not paused.

    Pause if n >= CATEGORY_MIN_TRADES and net < CATEGORY_BLOCK_NET.
    Do not extend existing until (cron must not turn TTL into a grave).
    TTL expiry → history + probation (eligible again; will not re-pause until
    probation ends). Evidence net>=0 unpauses immediately (no probation).
    Legacy blacklisted[] with no until → released to probation, not re-paused.
    """
    now = _as_utc(now or datetime.now(timezone.utc))
    ttl = _pause_ttl_days()
    min_n, block_net = _pause_min_n_block_net()
    dest = path or STRATEGY_BLACKLIST_PATH
    raw = _read_raw_registry(dest)
    merged = _merged_stats(stats)
    wr_report = get_strategy_kill_list()

    history = list(raw.get("history") or [])
    paused_in = list(raw.get("paused") or [])
    probation_in = list(raw.get("probation") or [])

    # Legacy graves (blacklisted names, no until) → probation, not paused.
    paused_names_in = {_canon(p.get("strategy")) for p in paused_in}
    for name in raw.get("blacklisted") or []:
        c = _canon(name)
        if c and c not in paused_names_in:
            history.append({
                "strategy": name, "ended_at": now.isoformat(),
                "end_reason": "legacy_released",
                "reason": "legacy blacklist had no until — not a life sentence",
            })
            probation_in.append({
                "strategy": name,
                "until": (now + timedelta(days=ttl)).isoformat(),
                "reason": "legacy grave released to probation (evaluation eligible)",
            })

    still_paused = []
    probation = []
    seen_prob = set()

    def _add_probation(name, reason):
        c = _canon(name)
        if c in seen_prob:
            return
        seen_prob.add(c)
        probation.append({
            "strategy": name,
            "until": (now + timedelta(days=ttl)).isoformat(),
            "reason": reason,
        })

    for x in probation_in:
        until = _parse_iso(x.get("until"))
        if until is not None and now < until:
            c = _canon(x.get("strategy"))
            if c not in seen_prob:
                seen_prob.add(c)
                probation.append(dict(x))

    for p in paused_in:
        name = p.get("strategy")
        c = _canon(name)
        until = _parse_iso(p.get("until"))
        st = merged.get(c)
        if st and st["n"] >= min_n and st["net"] >= block_net:
            history.append({
                "strategy": name, "ended_at": now.isoformat(),
                "end_reason": "evidence_net_recovered",
                "net": st["net"], "n": st["n"],
            })
            continue
        if until is None or now >= until:
            history.append({
                "strategy": name, "ended_at": now.isoformat(),
                "end_reason": "ttl",
                "until": p.get("until"),
            })
            _add_probation(name, "ttl expired — eligible for evaluation/sizing")
            continue
        still_paused.append(dict(p))

    paused_now = {_canon(p.get("strategy")) for p in still_paused}

    for name, st in merged.items():
        c = _canon(name)
        if c in paused_now or c in seen_prob:
            continue
        if st["n"] >= min_n and st["net"] < block_net:
            still_paused.append({
                "strategy": name,
                "until": (now + timedelta(days=ttl)).isoformat(),
                "paused_at": now.isoformat(),
                "reason": f"fee-true net ${st['net']:+.2f} over {st['n']} trades",
                "net": st["net"], "n": st["n"], "wins": st.get("wins", 0),
                "source": st.get("source", "category_stats"),
            })
            paused_now.add(c)
            print(f"  ⏸ PAUSE '{name}' until {(now + timedelta(days=ttl)).date()} "
                  f"net=${st['net']:+.2f} n={st['n']} ({st.get('source')})")

    live_names = [p.get("strategy") for p in still_paused]
    out = {
        "paused": still_paused,
        "probation": probation,
        "history": history[-50:],
        "blacklisted": live_names,  # currently paused only; expired omitted
        "updated": now.isoformat(),
        "wr_report": wr_report,  # informational — not a fuse
        "fee_blind_report": _fee_blind_report(),  # report_only — never gates
        "rule": ("pause on fee-true net (n>=min, net<0); TTL "
                 f"{ttl}d + probation; evidence net>=0 unpauses; never WR"),
    }
    with open(dest, "w") as f:
        json.dump(out, f, indent=2)
    return live_names

def opus_audit():
    """Use LOCAL deepseek-r1:70b via Ollama to analyze P&L.
    Zero API cost — runs on NIMROD's M1 Ultra 128GB.
    This is the 'RenTech brain' — deep reasoning about what went wrong."""
    pnl = load_pnl()
    summary = pnl["summary"]
    trades = pnl["trades"][-20:]
    
    prompt = f"""You are Valedia's Chief Investment Officer. Analyze this P&L data and provide:
1. ROOT CAUSE analysis of losses — what went wrong and why
2. STRATEGY CHANGES — what to keep, what to kill, what to add
3. CAPITAL ALLOCATION — how to deploy $300-500 for maximum short-term profit
4. SPECIFIC TRADES — what should Valedia bet on RIGHT NOW based on current conditions

Current P&L:
- Win rate: {summary.get('win_rate',0)}%
- Wins: {summary.get('wins',0)} Losses: {summary.get('losses',0)}
- Net P&L: ${summary.get('net_pnl',0):.2f}
- Total won: ${summary.get('total_won',0):.2f} | Total lost: ${summary.get('total_lost',0):.2f}

Strategy breakdown:
{json.dumps(summary.get('strategies',{}), indent=2)}

Recent trades:
{json.dumps(trades[-10:], indent=2)}

Platforms available: Kalshi (prediction markets), Coinbase (crypto), Alpaca (stocks)
Current market: Fear & Greed = 8 (Extreme Fear)

Be brutally honest. Numbers only. What specific changes will make money?
Respond in under 500 words."""

    try:
        import requests
        r = requests.post("http://localhost:11434/api/generate",
            json={"model": "qwen3:8b", "prompt": prompt,
                  "stream": False, "options": {"num_predict": 2000, "temperature": 0.3}},
            timeout=180)
        if r.ok:
            data = r.json()
            # qwen3 uses separate 'thinking' and 'response' fields
            response = data.get("response", "").strip()
            thinking = data.get("thinking", "").strip()
            # Use response if available, otherwise extract from thinking
            if not response and thinking:
                response = thinking
            audit_path = os.path.join(MODELS, "opus_audit.json")
            with open(audit_path, "w") as f:
                json.dump({"ts": datetime.now(timezone.utc).isoformat(),
                           "model": "qwen3:8b (local)",
                           "audit": response, "summary": summary}, f, indent=2)
            return response
        return f"Ollama error: {r.status_code}"
    except Exception as e:
        return f"Local audit failed: {e}"

def simple_pnl_message(summary):
    """The daily P&L message Burton gets: simple + informative + 100% real (broker-sourced).
    Shows what's invested (deployed) and realized settled P&L — no modeled/EV numbers."""
    s = summary or {}
    net = s.get("net_pnl", 0.0)
    inv = s.get("current_deployed", 0.0)
    cash = s.get("current_balance", 0.0)
    w, l = s.get("wins", 0), s.get("losses", 0)
    d = datetime.now(timezone.utc).astimezone().strftime("%b %-d")
    icon = "🟢" if net >= 0 else "🔴"
    lines = [
        f"💰 Valedia P&L — {d}",
        f"Invested: ${inv:,.2f} deployed · ${cash:,.2f} cash",
        f"{icon} Kalshi broker P&L: ${net:+,.2f}  (pnl_tracker.json, not FTL)",
    ]
    if w + l > 0:
        lines.append(f"Settled record: {w}W / {l}L ({s.get('win_rate', 0):.0f}% win)")
    else:
        lines.append("Settled record: none yet")
    return "\n".join(lines)


def run():
    """Full P&L tracking cycle: check settlements, update blacklist, run Opus audit."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"📊 VALEDIA P&L TRACKER ({ts})", ""]
    
    # Track settlements
    summary, new = track_kalshi_pnl()
    
    lines.append(f"Win Rate: {summary['win_rate']}% ({summary['wins']}W / {summary['losses']}L)")
    lines.append(f"Net P&L: ${summary['net_pnl']:+.2f}")
    lines.append(f"Total Won: ${summary['total_won']:.2f} | Lost: ${summary['total_lost']:.2f}")
    lines.append(f"Balance: ${summary['current_balance']:.2f} cash + ${summary['current_deployed']:.2f} deployed")
    
    if new:
        lines.append(f"\nNEW SETTLEMENTS ({len(new)}):")
        for t in new:
            icon = "✅" if t["status"] == "WON" else "❌"
            lines.append(f"  {icon} {t['strategy']:15} {t['ticker'][:30]} ${t['profit']:+.2f}")
    
    # Strategy breakdown
    lines.append(f"\nSTRATEGY P&L:")
    for s, d in summary.get("strategies", {}).items():
        total = d["wins"] + d["losses"]
        wr = d["wins"] / total * 100 if total > 0 else 0
        net = d.get("profit", 0) - d.get("cost", 0)
        lines.append(f"  {s:18} {d['wins']}W/{d['losses']}L ({wr:.0f}%) net:${net:+.2f}")
    
    # Learning pause (net-based, TTL). WR flags are report-only — not a Kelly fuse.
    paused = update_strategy_blacklist()
    if paused:
        lines.append(f"\n⏸ PAUSED (until TTL/evidence, not permanent): {', '.join(paused)}")
        for p in load_pause_registry().get("paused") or []:
            until = str(p.get("until") or "")[:10]
            lines.append(f"    {p.get('strategy')} until {until} {p.get('reason', '')}")
    wr_flags = get_strategy_kill_list()
    if wr_flags:
        names = ", ".join(k["strategy"] for k in wr_flags)
        lines.append(f"\n(report-only WR flags, not a fuse): {names}")
    
    # Run Opus audit weekly (or on first run)
    audit_path = os.path.join(MODELS, "opus_audit.json")
    run_opus = not os.path.exists(audit_path)
    if os.path.exists(audit_path):
        try:
            last = json.load(open(audit_path)).get("ts", "")
            if last[:10] != datetime.now(timezone.utc).strftime("%Y-%m-%d"):
                run_opus = True
        except: run_opus = True
    
    if run_opus:
        lines.append("\nRunning deepseek-r1:70b audit (local, zero cost)...")
        audit = opus_audit()
        lines.append(audit[:500])
    
    report = "\n".join(lines)
    print(report)
    return report

if __name__ == "__main__":
    run()
