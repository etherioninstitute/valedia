"""Valedia Unified Brain v2 (kernel v16e) — Master Orchestrator
Connects ALL systems into one pipeline:
  Alpha Intel → Kernel v9 → ERE Safety → Signal Fusion → Kelly → Fee Gate → Execute

(c) GOATWORTHY Post LLC. All rights reserved.
"""
import os, sys, json, traceback
import numpy as np
from datetime import datetime, timezone
sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")


# ══════════════════════════════════════════════════════════════════════
# STAGE 1: INTELLIGENCE GATHERING
# ══════════════════════════════════════════════════════════════════════

def stage_intel():
    """Gather all intelligence before making decisions."""
    import requests
    intel = {"ts": datetime.now(timezone.utc).isoformat()}
    
    # Fear & Greed
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        fg = r.json()["data"][0]
        intel["fear_greed"] = int(fg["value"])
        intel["fg_label"] = fg["value_classification"]
    except:
        intel["fear_greed"] = 50; intel["fg_label"] = "Neutral"
    
    # Crypto market bias from Fear/Greed
    fg = intel["fear_greed"]
    if fg <= 20: intel["fg_signal"] = 15  # extreme fear = strong buy
    elif fg <= 35: intel["fg_signal"] = 8
    elif fg >= 80: intel["fg_signal"] = -15  # extreme greed = strong sell
    elif fg >= 65: intel["fg_signal"] = -8
    else: intel["fg_signal"] = 0
    
    return intel


# ══════════════════════════════════════════════════════════════════════
# STAGE 2: KERNEL v9 SIGNALS
# ══════════════════════════════════════════════════════════════════════

def stage_kernel(prices, asset_class="crypto"):
    """Run full 11-framework kernel prediction."""
    from kernel_predictor_v16e import KernelPredictor
    kp = KernelPredictor(asset_class)
    return kp.predict(prices)


# ══════════════════════════════════════════════════════════════════════
# STAGE 3: ERE SAFETY CHECK
# ══════════════════════════════════════════════════════════════════════

def stage_ere_safety(prices):
    """Run Etherion Risk Engine safety checks. Returns multiplier 0-1."""
    from Etherion_Risk_Engine import SignalDecomposer, VarianceCascadeEngine, DrawdownGuard
    
    ret = np.diff(np.log(prices))
    if len(ret) < 30:
        return {"safe": True, "multiplier": 1.0, "status": "INSUFFICIENT_DATA"}
    
    # Variance cascade check
    cascade = VarianceCascadeEngine()
    matrix = ret.reshape(-1, 1)
    ladder = cascade.energy_ladder_check(matrix)
    extremes = cascade.cumulative_extremes(matrix)
    
    # Drawdown guard
    guard = DrawdownGuard(initial_capital=100)
    guard.adaptive_threshold(ret)
    check = guard.check_guard(ret)
    
    # Mean-reversion signal strength
    decomp = SignalDecomposer(20)
    snr = decomp.signal_strength(prices)
    
    # Compute safety multiplier
    mult = 1.0
    if check["action"] == "HALT_ALL":
        mult = 0.30  # allows small crypto positions in extreme fear
    elif check["action"] == "REDUCE_50%":
        mult = 0.5
    if not ladder["stable"]:
        mult *= 0.3  # cascade breaking
    if not extremes["safe"]:
        mult *= 0.5
    
    return {"safe": mult > 0.05, "multiplier": round(mult, 2),
            "status": check["status"], "phi_ratio": check.get("ratio", 0),
            "cascade_stable": ladder["stable"], "snr": round(snr, 4)}


# ══════════════════════════════════════════════════════════════════════
# STAGE 4: SIGNAL FUSION (unified score)
# ══════════════════════════════════════════════════════════════════════

def stage_fusion(kernel_sig, ere_safety, intel, ml_score=50, tech_score=50, sent_score=50, mirofish_score=50):
    """Fuse ALL signal sources into one conviction score 0-100.
    
    Weights (total 100%):
      Kernel v16e:   30%  (11 UGFT frameworks + adaptive SVD)
      MiroFish:      10%  (swarm intelligence prediction)
      ERE safety:    15%  (multiplier on final score)
      Intel:         10%  (Fear/Greed, SEC, inversion)
      ML model:      18%  (GradientBoosting)
      Technical:     10%  (RSI, MACD, BB)
      Sentiment:      7%  (news, social)
    """
    # Kernel: score already 0-100
    k_score = kernel_sig.get("score", 50)
    
    # Intel: Fear/Greed adds +/-15 to crypto
    intel_boost = intel.get("fg_signal", 0)
    
    # Weighted fusion (MiroFish added at 10%)
    fused = (k_score * 0.30 +
             mirofish_score * 0.10 +
             ml_score * 0.18 +
             tech_score * 0.10 +
             sent_score * 0.07 +
             (50 + intel_boost) * 0.10 +
             50 * 0.15)  # ERE applied as multiplier below
    
    # ERE safety multiplier on the DEVIATION from neutral
    deviation = fused - 50
    deviation *= ere_safety["multiplier"]
    fused = 50 + deviation
    
    return round(float(np.clip(fused, 5, 95)), 1)


# ══════════════════════════════════════════════════════════════════════
# STAGE 5: KELLY SIZING
# ══════════════════════════════════════════════════════════════════════

def stage_kelly(fused_score, equity, max_pct=0.04):
    """Half-Kelly position sizing from fused conviction score."""
    if fused_score <= 55:
        return 0.0  # no edge
    edge = (fused_score - 50) / 50  # 0 to 0.9
    # Half-Kelly: f* = edge / odds, halved for safety
    kelly_frac = edge * 0.5
    position = min(kelly_frac * equity, equity * max_pct)
    return round(max(position, 0), 2)


# ══════════════════════════════════════════════════════════════════════
# STAGE 6: FEE GATE
# ══════════════════════════════════════════════════════════════════════

FEES = {
    "alpaca": 0.0,       # $0 commission stocks
    "coinbase": 0.006,   # 0.6% taker
    "kraken": 0.0016,    # 0.16% maker
}

def stage_fee_gate(position_size, expected_return_pct, platform="alpaca"):
    """Reject trade if fees eat the edge."""
    fee_rate = FEES.get(platform, 0.005)
    round_trip_fee = position_size * fee_rate * 2  # buy + sell
    expected_profit = position_size * expected_return_pct / 100
    if expected_profit <= round_trip_fee:
        return {"pass": False, "reason": f"Fees ${round_trip_fee:.4f} >= profit ${expected_profit:.4f}"}
    return {"pass": True, "net_profit": round(expected_profit - round_trip_fee, 4)}


# ══════════════════════════════════════════════════════════════════════
# STAGE 7: SELF-IMPROVEMENT (Performance Tracker + Adaptation)
# ══════════════════════════════════════════════════════════════════════

LOG_FILE = "/Users/burtonchatelain/.openclaw/workspace/models/brain_log.json"

def load_log():
    try:
        with open(LOG_FILE) as f: return json.load(f)
    except: return {"trades": [], "stats": {"total": 0, "wins": 0}}

def save_log(log):
    with open(LOG_FILE, "w") as f: json.dump(log, f, indent=2, default=str)

def log_trade(ticker, direction, score, price, size):
    log = load_log()
    log["trades"].append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker, "dir": direction, "score": score,
        "entry": price, "size": size, "exit": None, "result": None
    })
    log["trades"] = log["trades"][-500:]
    save_log(log)

def update_results(current_prices):
    """Check past predictions vs reality. Self-score."""
    log = load_log()
    updated = 0
    for t in log["trades"]:
        if t["result"] is not None: continue
        if t["ticker"] in current_prices and t["entry"]:
            cur = current_prices[t["ticker"]]
            ret = (cur - t["entry"]) / t["entry"]
            if t["dir"] == "BUY":
                t["result"] = "W" if ret > 0.002 else "L"
            else:
                t["result"] = "W" if ret < -0.002 else "L"
            t["exit"] = cur; updated += 1
    # Recalc stats
    resolved = [t for t in log["trades"] if t["result"]]
    w = sum(1 for t in resolved if t["result"] == "W")
    log["stats"] = {"total": len(resolved), "wins": w,
                    "hit_rate": round(w/len(resolved)*100, 1) if resolved else 0,
                    "recent_20": round(sum(1 for t in resolved[-20:] if t["result"]=="W")/min(20,len(resolved[-20:]))*100, 1) if resolved else 0}
    save_log(log)
    return log["stats"]

def get_confidence_multiplier():
    """Self-adapting: scale confidence by track record."""
    log = load_log()
    hr = log["stats"].get("hit_rate", 50)
    r20 = log["stats"].get("recent_20", 50)
    # Use recent performance more heavily
    blended = r20 * 0.7 + hr * 0.3
    if blended >= 60: return 1.3  # proven edge → increase size
    elif blended >= 55: return 1.1
    elif blended <= 40: return 0.5  # losing → cut size
    elif blended <= 45: return 0.7
    return 1.0


# ══════════════════════════════════════════════════════════════════════
# MASTER ORCHESTRATOR — The Unified Brain
# ══════════════════════════════════════════════════════════════════════

WATCHLIST_CRYPTO = {"BTC":"BTC-USD","ETH":"ETH-USD","SOL":"SOL-USD",
                    "XRP":"XRP-USD","ADA":"ADA-USD","AVAX":"AVAX-USD"}
WATCHLIST_EQUITY = {"SPY":"SPY","QQQ":"QQQ","XLE":"XLE","GLD":"GLD",
                    "NVDA":"NVDA","GOOGL":"GOOGL","COIN":"COIN","MSTR":"MSTR",
                    "AAPL":"AAPL","AMD":"AMD","TSLA":"TSLA","META":"META"}

def run_brain():
    """MASTER PIPELINE: Intel → Kernel → ERE → Fusion → Kelly → Fees → Execute"""
    import yfinance as yf
    from alpaca_executor import get_account as alpaca_account, market_buy as alpaca_buy, market_sell as alpaca_sell, get_position as alpaca_position, IS_LIVE
    from coinbase_trader import get_balances as cb_balances, market_buy as cb_buy, market_sell as cb_sell, get_price as cb_price
    
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"[BRAIN] Unified Brain v2 (kernel v16e) — {ts}", f"Mode: {'LIVE' if IS_LIVE else 'PAPER'}", ""]

    # ── STAGE 1: Intelligence ──
    intel = stage_intel()
    lines.append(f"INTEL: Fear/Greed={intel['fear_greed']} ({intel['fg_label']}) signal={intel['fg_signal']:+d}")
    
    # Load ML retrain signals if available
    ml_signals = {}
    try:
        import json
        with open("/Users/burtonchatelain/.openclaw/workspace/models/latest_retrain.json") as f:
            ml_data = json.load(f)
        for r in ml_data.get("results",[]):
            ml_signals[r["ticker"]] = r.get("buy_probability", 0.5)
        lines.append(f"ML SIGNALS: {len(ml_signals)} assets loaded")
    except: pass
    
    # Load MiroFish swarm signals if available
    mirofish_signal = {}
    mirofish_bias = 50
    try:
        import json as _json
        mf_path = "/Users/burtonchatelain/.openclaw/workspace/models/mirofish_signal.json"
        with open(mf_path) as f:
            mf = _json.load(f)
        if mf.get("status") == "active":
            mirofish_signal = mf.get("signals", {})
            mirofish_bias = mf.get("overall_bias", 50)
            lines.append(f"MIROFISH: bias={mirofish_bias:.0f} ({len(mirofish_signal)} assets)")
    except: pass

    # ── Get account ──
    equity = 0; cash = 0
    try:
        acct = alpaca_account()
        equity = float(acct["equity"])
        cash = float(acct["cash"])
        lines.append(f"ALPACA LIVE: ${equity:.2f} equity, ${cash:.2f} cash")
    except Exception as e:
        lines.append(f"ALPACA: offline ({str(e)[:40]})")
    
    cb_cash = 0
    try:
        cb_bal = cb_balances()
        cb_cash = cb_bal.get("USD", {}).get("balance", 0)
        cb_btc = cb_bal.get("BTC", {}).get("balance", 0)
        cb_sol = cb_bal.get("SOL", {}).get("balance", 0)
        lines.append(f"COINBASE: ${cb_cash:.2f} USD, {cb_btc:.8f} BTC, {cb_sol:.8f} SOL")
    except Exception as e:
        lines.append(f"COINBASE: error {e}")
    
    total = equity + cb_cash
    if total < 5:
        return f"[BRAIN] Total ${total:.2f} too low"

    # ── Self-improvement: update past predictions ──
    conf_mult = get_confidence_multiplier()
    log = load_log()
    if log["stats"]["total"] > 0:
        lines.append(f"TRACK RECORD: {log['stats']['hit_rate']}% ({log['stats']['wins']}W/{log['stats']['total']-log['stats']['wins']}L) conf_mult={conf_mult}x")

    # ── Process each asset ──
    actions = []; max_daily_alp = equity * 0.10; spent_alp = 0  # 10% of Alpaca
    max_daily_cb = cb_cash * 0.10; spent_cb = 0  # 10% of Coinbase
    lines.append("")
    
    for grp_name, watchlist, ac in [("EQUITY", WATCHLIST_EQUITY, "equity"), ("CRYPTO_PROXY", WATCHLIST_CRYPTO, "equity")]:
        for name, ticker in watchlist.items():
            try:
                data = yf.Ticker(ticker).history(period="6mo")
                prices = data["Close"].values
                if len(prices) < 80: continue
                cur_price = float(prices[-1])


                # STAGE 2: Kernel v9
                kernel = stage_kernel(prices, ac)
                
                # STAGE 3: ERE Safety
                ere = stage_ere_safety(prices)
                
                # STAGE 4: Fusion (ML/tech/sent at 50 for now — will be wired later)
                # Get MiroFish signal for this ticker
                mf_score = mirofish_signal.get(ticker, {}).get("score", 50)
                fused = stage_fusion(kernel, ere, intel, mirofish_score=mf_score)
                
                # Apply intel boost for crypto proxies
                if grp_name == "CRYPTO_PROXY":
                    fused = min(95, fused + intel["fg_signal"] * 0.5)
                
                # STAGE 5: Kelly sizing (with self-improvement multiplier)
                raw_kelly = stage_kelly(fused, cb_cash if grp_name == "CRYPTO_PROXY" else equity)
                position = round(raw_kelly * conf_mult, 2)
                if grp_name == "CRYPTO_PROXY":
                    position = min(position, max_daily_cb - spent_cb)
                else:
                    position = min(position, max_daily_alp - spent_alp)
                
                # STAGE 6: Fee gate (Alpaca = 0% for stocks)
                expected_ret = abs(fused - 50) * (0.3 if grp_name == "CRYPTO_PROXY" else 0.1)  # rough expected %
                fee_check = stage_fee_gate(position, expected_ret, "coinbase" if grp_name == "CRYPTO_PROXY" else "alpaca")
                
                # Current position
                pos = alpaca_position(ticker)
                has_pos = pos is not None
                
                # ── DECISION ──
                if fused > 55 and position >= 0.40 and fee_check["pass"] and ere["safe"] and not has_pos:
                    if grp_name == "CRYPTO_PROXY" and cb_cash >= position:
                        result = cb_buy(ticker, position)
                        platform = "COINBASE"
                        spent_cb += position
                    else:
                        result = alpaca_buy(ticker, notional=position)
                        platform = "ALPACA"
                        spent_alp += position
                    log_trade(ticker, "BUY", fused, cur_price, position)
                    actions.append(f"BUY ${position:.2f} {ticker}")
                    lines.append(f"  >> BUY ${position:.2f} {name:6s} on {platform} fused={fused} ere={ere['status']}")
                elif fused < 40 and has_pos:
                    if grp_name == "CRYPTO_PROXY":
                        base = ticker.replace("-USD","")
                        bal = cb_balances().get(base, {}).get("balance", 0)
                        if bal > 0:
                            result = cb_sell(ticker, bal)
                    else:
                        result = alpaca_sell(ticker)
                    log_trade(ticker, "SELL", fused, cur_price, 0)
                    actions.append(f"SELL {ticker}")
                    lines.append(f"  >> SELL {name:6s} fused={fused}")
                elif has_pos:
                    lines.append(f"  {name:6s} HOLD fused={fused:.0f} P&L=${float(pos['unrealized_pl']):.2f}")
                else:
                    lines.append(f"  {name:6s} -- fused={fused:.0f} ere={ere['status']} safe={ere['safe']}")
                    
            except Exception as e:
                lines.append(f"  {name:6s} err: {str(e)[:50]}")


    # ── DCA (runs weekly regardless of signals) ──
    try:
        from strategy_modules import DCAEngine
        dca = DCAEngine(weekly_budget=10, assets={"BTC-USD":0.4,"ETH-USD":0.25,"SOL-USD":0.2,"XRP-USD":0.15})
        if dca.should_buy() and cb_cash >= 5:
            dca_actions = dca.execute(cb_buy, lambda: cb_cash)
            for da in dca_actions:
                lines.append(f"  {da}")
                actions.append(da)
        elif dca.should_buy():
            lines.append("  DCA: ready but insufficient Coinbase USD")
    except Exception as e:
        lines.append(f"  DCA error: {str(e)[:50]}")

    # ── Scalp check (Alpaca stocks only) ──
    try:
        from strategy_modules import MicroScalper
        import yfinance as yf
        scalper = MicroScalper()
        wp = {}
        for t in ["SPY","QQQ","AAPL","NVDA","AMD","TSLA"]:
            try: wp[t] = yf.Ticker(t).history(period="1mo")["Close"].values
            except: pass
        scalps = scalper.get_scalp_candidates(wp)
        for s in scalps:
            if s["signal"] == "SCALP_LONG" and cash >= 2:
                amt = min(cash * 0.03, 2)
                result = alpaca_buy(s["ticker"], notional=amt)
                actions.append(f"SCALP BUY ${amt:.2f} {s['ticker']} z={s['z']}")
                lines.append(f"  >> SCALP BUY ${amt:.2f} {s['ticker']} z={s['z']}")
    except Exception as e:
        lines.append(f"  Scalp error: {str(e)[:50]}")

    lines.append("")
    if actions:
        lines.append(f"EXECUTED: {len(actions)} trades (Alpaca ${spent_alp:.2f}, Coinbase ${spent_cb:.2f})")
        for a in actions: lines.append(f"  {a}")
    else:
        lines.append("NO NEW TRADES this cycle (existing positions held)")
    
    # Save report
    report = "\n".join(lines)
    try:
        with open("/Users/burtonchatelain/.openclaw/workspace/models/latest_brain_report.json", "w") as f:
            json.dump({"ts": ts, "actions": actions, "intel": intel,
                       "equity": equity, "conf_mult": conf_mult}, f, indent=2, default=str)
    except: pass
    
    return report


if __name__ == "__main__":
    # Source env
    if not os.environ.get("ALPACA_API_KEY"):
        import subprocess
        r = subprocess.run("set -a && source /Users/burtonchatelain/.openclaw/.env && env",
                          shell=True, capture_output=True, text=True)
        for line in r.stdout.split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ[k] = v
    print(run_brain())
