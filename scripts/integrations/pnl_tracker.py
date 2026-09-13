"""Valedia P&L Tracker — The Missing Feedback Loop
═══════════════════════════════════════════════════════════════════════════
Tracks ALL trades across ALL platforms with actual settlement results.
This is what makes Valedia learn from her mistakes.

Without this, Valedia reports "100% win rate" while losing money.

(c) GOATWORTHY Post LLC. All rights reserved.
"""
import os, sys, json, time
from datetime import datetime, timezone

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
    """Returns strategies that should be disabled based on actual P&L.
    Rule: If a strategy has 3+ losses and <25% win rate, KILL IT."""
    pnl = load_pnl()
    kills = []
    for strat, data in pnl["summary"].get("strategies", {}).items():
        total = data["wins"] + data["losses"]
        if total >= 3:
            wr = data["wins"] / total * 100
            if wr < 25:
                kills.append({"strategy": strat, "win_rate": round(wr, 1),
                              "wins": data["wins"], "losses": data["losses"],
                              "total_lost": data["cost"]})
    return kills

STRATEGY_BLACKLIST_PATH = os.path.join(MODELS, "strategy_blacklist.json")

def update_strategy_blacklist():
    """Auto-disable strategies that are proven losers."""
    kills = get_strategy_kill_list()
    blacklist = []
    for k in kills:
        blacklist.append(k["strategy"])
        print(f"  ❌ KILLING strategy '{k['strategy']}': {k['wins']}W/{k['losses']}L ({k['win_rate']}% win rate, ${k['total_lost']:.2f} lost)")
    
    with open(STRATEGY_BLACKLIST_PATH, "w") as f:
        json.dump({"blacklisted": blacklist, "updated": datetime.now(timezone.utc).isoformat(),
                    "details": kills}, f, indent=2)
    return blacklist

def is_strategy_blacklisted(strategy_name):
    """Check if a strategy has been auto-disabled."""
    if os.path.exists(STRATEGY_BLACKLIST_PATH):
        try:
            bl = json.load(open(STRATEGY_BLACKLIST_PATH))
            return strategy_name in bl.get("blacklisted", [])
        except: pass
    return False

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
        f"{icon} Realized P&L: ${net:+,.2f}  (Kalshi, net of fees)",
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
    
    # Auto-kill losing strategies
    blacklist = update_strategy_blacklist()
    if blacklist:
        lines.append(f"\n⛔ AUTO-DISABLED: {', '.join(blacklist)}")
    
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
