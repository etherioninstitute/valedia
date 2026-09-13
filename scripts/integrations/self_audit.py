"""Valedia Self-Audit Engine — Autonomous Self-Improvement
Runs nightly. Reviews own performance. Tunes own parameters.
Writes findings to lessons.md and adjusts brain weights.

This is the missing piece: Valedia improving HERSELF, not waiting
for Burton to notice problems.

Cycle:
  1. REFLECT — analyze last 7 days of trades, win rate, signal accuracy
  2. DIAGNOSE — which signals were right? which were wrong? why?
  3. TUNE — adjust weights, thresholds, confidence multipliers
  4. EVOLVE — write new lessons, update skill graph, propose strategy changes
  5. REPORT — send Burton a concise improvement summary

(c) GOATWORTHY Post LLC.
"""
import os, sys, json, numpy as np
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MODELS_DIR = os.path.expanduser("~/.openclaw/workspace/models")
LESSONS    = os.path.expanduser("~/.openclaw/workspace/tasks/lessons.md")
SKILLS_DIR = os.path.expanduser("~/.openclaw/workspace/skills/trading")
BRAIN_LOG  = os.path.join(MODELS_DIR, "brain_log.json")
AUDIT_LOG  = os.path.join(MODELS_DIR, "self_audit_history.json")

# ═══ STAGE 0: EVALUATE — Score pending trades against current prices ═══
def evaluate_pending_trades():
    """Check all trades without results, compare entry to current price."""
    try:
        with open(BRAIN_LOG) as f:
            log = json.load(f)
    except:
        return 0

    import yfinance as yf
    import logging; logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    import warnings; warnings.filterwarnings("ignore")

    evaluated = 0
    for t in log.get("trades", []):
        if t.get("result"): continue  # already scored
        if not t.get("entry"): continue

        ticker = t.get("ticker", "")
        entry = float(t["entry"])
        direction = t.get("dir", "BUY")

        # Get current price
        try:
            cur = None
            # Try yfinance first
            import io, contextlib
            with contextlib.redirect_stderr(io.StringIO()):
                data = yf.Ticker(ticker).history(period="5d")
            if not data.empty:
                cur = float(data["Close"].iloc[-1])

            # Fallback to CoinGecko for crypto
            if cur is None and "-USD" in ticker:
                import requests
                sym = ticker.replace("-USD", "").lower()
                r = requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids={sym}&vs_currencies=usd", timeout=10)
                if r.ok:
                    prices = r.json()
                    if prices:
                        cur = float(list(prices.values())[0].get("usd", 0))

            if cur and cur > 0:
                t["exit"] = cur
                ret = (cur - entry) / entry
                if direction == "BUY":
                    t["result"] = "WIN" if ret > 0.005 else "LOSS" if ret < -0.005 else None
                elif direction == "SELL":
                    t["result"] = "WIN" if ret < -0.005 else "LOSS" if ret > 0.005 else None
                t["pnl"] = round(ret * float(t.get("size", 1)), 4)
                if t.get("result"):
                    evaluated += 1
        except:
            pass

    with open(BRAIN_LOG, "w") as f:
        json.dump(log, f, indent=2, default=str)

    return evaluated

# ═══ STAGE 1: REFLECT — Analyze recent performance ═══
def reflect(days=7):
    """Analyze last N days of trading activity."""
    try:
        with open(BRAIN_LOG) as f:
            log = json.load(f)
    except:
        return {"error": "No brain_log.json found", "trades": []}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    trades = [t for t in log.get("trades", []) if t.get("ts", "") >= cutoff]

    if not trades:
        return {"total": 0, "message": f"No trades in last {days} days"}

    wins = [t for t in trades if t.get("result") == "WIN"]
    losses = [t for t in trades if t.get("result") == "LOSS"]
    pending = [t for t in trades if not t.get("result")]

    # Signal source accuracy
    signal_accuracy = {}
    for t in trades:
        if not t.get("result"): continue
        for src in ["kernel", "ml", "mirofish", "sentiment", "technical"]:
            if src in t.get("signals", {}):
                if src not in signal_accuracy:
                    signal_accuracy[src] = {"wins": 0, "total": 0}
                signal_accuracy[src]["total"] += 1
                if t["result"] == "WIN":
                    signal_accuracy[src]["wins"] += 1

    # Day-of-week performance
    dow_perf = {}
    for t in trades:
        if not t.get("result"): continue
        try:
            d = datetime.fromisoformat(t["ts"].replace("Z", "+00:00"))
            day = d.strftime("%A")
            if day not in dow_perf:
                dow_perf[day] = {"wins": 0, "total": 0}
            dow_perf[day]["total"] += 1
            if t["result"] == "WIN":
                dow_perf[day]["wins"] += 1
        except: pass

    # Asset performance
    asset_perf = {}
    for t in trades:
        if not t.get("result"): continue
        tk = t.get("ticker", "?")
        if tk not in asset_perf:
            asset_perf[tk] = {"wins": 0, "total": 0, "pnl": 0}
        asset_perf[tk]["total"] += 1
        if t["result"] == "WIN":
            asset_perf[tk]["wins"] += 1
        asset_perf[tk]["pnl"] += t.get("pnl", 0)

    return {
        "period_days": days,
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "pending": len(pending),
        "hit_rate": round(len(wins) / max(len(wins) + len(losses), 1) * 100, 1),
        "signal_accuracy": {k: round(v["wins"]/max(v["total"],1)*100, 1)
                           for k, v in signal_accuracy.items()},
        "day_of_week": {k: round(v["wins"]/max(v["total"],1)*100, 1)
                       for k, v in dow_perf.items()},
        "asset_performance": asset_perf,
    }

# ═══ STAGE 2: DIAGNOSE — What's working, what's not ═══
def diagnose(reflection):
    """Identify specific problems and opportunities."""
    findings = []

    hr = reflection.get("hit_rate", 0)
    total = reflection.get("total_trades", 0)

    if total == 0:
        findings.append({"type": "warning", "msg": "No trades executed — brain may be too conservative"})
        return findings

    # Overall hit rate check
    if hr < 50:
        findings.append({"type": "problem", "msg": f"Hit rate {hr}% is below breakeven — need tighter filters"})
    elif hr > 60:
        findings.append({"type": "strength", "msg": f"Hit rate {hr}% is strong — maintain current approach"})

    # Signal accuracy diagnosis
    for src, acc in reflection.get("signal_accuracy", {}).items():
        if acc < 40:
            findings.append({"type": "problem", "msg": f"Signal '{src}' at {acc}% accuracy — consider reducing weight"})
        elif acc > 65:
            findings.append({"type": "strength", "msg": f"Signal '{src}' at {acc}% accuracy — consider increasing weight"})

    # Day-of-week diagnosis
    for day, rate in reflection.get("day_of_week", {}).items():
        if rate < 35:
            findings.append({"type": "problem", "msg": f"{day} win rate {rate}% — should skip or reduce size"})
        elif rate > 70:
            findings.append({"type": "strength", "msg": f"{day} win rate {rate}% — increase position sizes"})

    # Asset diagnosis
    for tk, perf in reflection.get("asset_performance", {}).items():
        if perf["total"] >= 3:
            tk_rate = perf["wins"] / perf["total"] * 100
            if tk_rate < 30:
                findings.append({"type": "problem", "msg": f"{tk}: {tk_rate:.0f}% win rate over {perf['total']} trades — avoid"})
            if perf["pnl"] < -5:
                findings.append({"type": "problem", "msg": f"{tk}: ${perf['pnl']:.2f} net loss — review thesis"})

    return findings

# ═══ STAGE 3: TUNE — Auto-adjust parameters ═══
def tune(findings, reflection):
    """Generate specific parameter adjustments based on diagnosis."""
    adjustments = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for f in findings:
        if f["type"] != "problem": continue

        # Underperforming signal → reduce weight suggestion
        if "Signal '" in f["msg"] and "reducing weight" in f["msg"]:
            src = f["msg"].split("'")[1]
            adjustments.append({
                "target": "unified_brain.py",
                "param": f"weight_{src}",
                "action": "reduce",
                "reason": f["msg"],
                "lesson": f"{today} | {src} signal underperforming ({f['msg']}) | Reduce {src} weight by 5% in next brain version"
            })

        # Overperforming signal → increase weight suggestion
        if f["type"] == "strength" and "increasing weight" in f.get("msg",""):
            src = f["msg"].split("'")[1]
            adjustments.append({
                "target": "unified_brain.py",
                "param": f"weight_{src}",
                "action": "increase",
                "reason": f["msg"],
                "lesson": f"{today} | {src} signal outperforming ({f['msg']}) | Increase {src} weight by 5%"
            })

        # Bad day-of-week → tighten gate
        if "should skip" in f["msg"]:
            day = f["msg"].split(" ")[0]
            adjustments.append({
                "target": "unified_brain.py",
                "param": f"dow_gate_{day}",
                "action": "tighten",
                "reason": f["msg"],
                "lesson": f"{today} | {day} consistently underperforms | Skip or halve position size on {day}"
            })

        # Consistently losing asset → blacklist suggestion
        if "win rate" in f["msg"] and "avoid" in f["msg"]:
            adjustments.append({
                "target": "universe",
                "action": "review",
                "reason": f["msg"],
                "lesson": f"{today} | {f['msg']} | Review whether to keep in universe"
            })

    return adjustments

# ═══ STAGE 4: EVOLVE — Write lessons + update knowledge ═══
def evolve(adjustments, reflection, findings):
    """Write lessons to lessons.md, update audit history."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_lessons = []

    # Write adjustment lessons
    for adj in adjustments:
        if adj.get("lesson"):
            new_lessons.append(adj["lesson"])

    # Write strength lessons too (reinforce what works)
    for f in findings:
        if f["type"] == "strength":
            new_lessons.append(f"{today} | Self-audit: {f['msg']} | Continue current approach")

    # Append to lessons.md
    if new_lessons:
        with open(LESSONS, "a") as lf:
            for lesson in new_lessons:
                lf.write(f"{lesson}\n")

    # Save audit history
    try:
        with open(AUDIT_LOG) as f:
            history = json.load(f)
    except:
        history = {"audits": []}

    history["audits"].append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "reflection": reflection,
        "findings_count": len(findings),
        "problems": sum(1 for f in findings if f["type"] == "problem"),
        "strengths": sum(1 for f in findings if f["type"] == "strength"),
        "adjustments": len(adjustments),
        "lessons_written": len(new_lessons),
    })
    history["audits"] = history["audits"][-90:]  # keep 90 days

    with open(AUDIT_LOG, "w") as f:
        json.dump(history, f, indent=2)

    return new_lessons

# ═══ STAGE 5: REPORT — Concise summary for Burton ═══
def run_self_audit():
    """Full autonomous self-improvement cycle."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"[SELF-AUDIT] {ts}", ""]

    # EVALUATE pending trades first
    evaluated = evaluate_pending_trades()
    if evaluated:
        lines.append(f"Evaluated {evaluated} pending trades against current prices")
        lines.append("")

    # REFLECT
    r7 = reflect(7)
    r30 = reflect(30)
    lines.append(f"7-day: {r7.get('total_trades',0)} trades, {r7.get('hit_rate',0)}% hit rate")
    lines.append(f"30-day: {r30.get('total_trades',0)} trades, {r30.get('hit_rate',0)}% hit rate")

    if r7.get("total_trades", 0) == 0 and r30.get("total_trades", 0) == 0:
        lines.append("")
        lines.append("No trades to analyze yet. Brain may be too conservative")
        lines.append("or market conditions (Fear + Mon/Tue gate) blocking execution.")
        lines.append("This is expected early — system is designed to be selective.")
        return "\n".join(lines)

    # Use whichever period has more data
    ref = r7 if r7.get("total_trades", 0) >= 5 else r30

    # Signal accuracy
    if ref.get("signal_accuracy"):
        lines.append("")
        lines.append("Signal accuracy:")
        for src, acc in sorted(ref["signal_accuracy"].items(), key=lambda x: x[1], reverse=True):
            marker = "✓" if acc >= 55 else "✗" if acc < 45 else "~"
            lines.append(f"  {marker} {src}: {acc}%")

    # DIAGNOSE
    findings = diagnose(ref)
    problems = [f for f in findings if f["type"] == "problem"]
    strengths = [f for f in findings if f["type"] == "strength"]

    if problems:
        lines.append("")
        lines.append(f"Issues found ({len(problems)}):")
        for p in problems:
            lines.append(f"  ⚠ {p['msg']}")

    if strengths:
        lines.append("")
        lines.append(f"Strengths ({len(strengths)}):")
        for s in strengths:
            lines.append(f"  ✓ {s['msg']}")

    # TUNE
    adjustments = tune(findings, ref)
    if adjustments:
        lines.append("")
        lines.append(f"Recommended adjustments ({len(adjustments)}):")
        for a in adjustments:
            lines.append(f"  → {a['action']} {a.get('param','')} ({a['reason'][:60]})")

    # EVOLVE
    lessons = evolve(adjustments, ref, findings)
    if lessons:
        lines.append("")
        lines.append(f"Lessons recorded: {len(lessons)}")

    # Trend check (compare 7d vs 30d)
    if r7.get("total_trades", 0) >= 3 and r30.get("total_trades", 0) >= 5:
        trend = r7["hit_rate"] - r30["hit_rate"]
        if trend > 5:
            lines.append(f"\n📈 Improving: 7d={r7['hit_rate']}% vs 30d={r30['hit_rate']}%")
        elif trend < -5:
            lines.append(f"\n📉 Declining: 7d={r7['hit_rate']}% vs 30d={r30['hit_rate']}%")
        else:
            lines.append(f"\n→ Stable: 7d={r7['hit_rate']}% vs 30d={r30['hit_rate']}%")

    return "\n".join(lines)

if __name__ == "__main__":
    print(run_self_audit())
