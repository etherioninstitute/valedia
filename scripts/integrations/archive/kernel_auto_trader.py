"""Kernel Auto-Trader — Valedia ASI
Runs kernel predictor v9, generates signals, executes on Coinbase.
Respects fund rules: 4% max per trade, 5% daily risk, fee gate.
(c) GOATWORTHY Post LLC.
"""
import sys, json, os
from datetime import datetime, timezone
sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")

import yfinance as yf
from coinbase_trader import get_balances, get_price, market_buy, market_sell
from kernel_predictor import KernelPredictor

PAIRS = {"SOL":"SOL-USD","XRP":"XRP-USD","ETH":"ETH-USD","BTC":"BTC-USD",
         "ADA":"ADA-USD","AVAX":"AVAX-USD","DOT":"DOT-USD","LINK":"LINK-USD"}
MAX_PER_TRADE_PCT = 0.04   # 4% of portfolio
MAX_DAILY_RISK_PCT = 0.05  # 5% total
MIN_CONFIDENCE = 5
MIN_USD_TRADE = 1.50       # Coinbase minimum ~$1

def run():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"KERNEL AUTO-TRADER ({ts})", ""]
    
    bal = get_balances()
    usd = bal.get("USD", {}).get("balance", 0)
    # Total portfolio value
    total = usd
    holdings = {}
    for sym, pair in PAIRS.items():
        crypto_bal = bal.get(sym, {}).get("balance", 0)
        if crypto_bal > 0:
            price = get_price(pair)
            val = crypto_bal * price
            total += val
            holdings[sym] = {"qty": crypto_bal, "value": val, "price": price}
    
    lines.append(f"Portfolio: ${total:.2f} (USD: ${usd:.2f})")
    for sym, h in holdings.items():
        lines.append(f"  {sym}: {h['qty']:.6f} (${h['value']:.2f})")
    lines.append("")
    
    max_trade = total * MAX_PER_TRADE_PCT
    daily_budget = total * MAX_DAILY_RISK_PCT
    trades_done = 0
    spent = 0
    
    # Generate signals
    kp = KernelPredictor("crypto")
    signals = []
    for sym, pair in PAIRS.items():
        try:
            data = yf.Ticker(pair).history(period="6mo")
            prices = data["Close"].values
            if len(prices) < 60: continue
            sig = kp.predict(prices)
            sig["sym"] = sym
            sig["pair"] = pair
            signals.append(sig)
            lines.append(f"  {sym:5s} {sig['direction']:4s} sz={sig['size']:+.3f} "
                        f"conf={sig['confidence']:.0f}% mom={sig['mom']:+.3f}")
        except Exception as e:
            lines.append(f"  {sym}: error {e}")
    
    lines.append("")
    
    # Sort by confidence, execute top signals
    buy_signals = sorted([s for s in signals if s["direction"]=="BUY" and s["confidence"]>=MIN_CONFIDENCE],
                         key=lambda x: -x["confidence"])
    sell_signals = [s for s in signals if s["direction"]=="SELL" and s["confidence"]>=MIN_CONFIDENCE]
    
    # SELL first (free up capital)
    for sig in sell_signals:
        sym = sig["sym"]
        if sym in holdings and holdings[sym]["value"] > MIN_USD_TRADE:
            qty = holdings[sym]["qty"] * min(abs(sig["size"]), 0.5)  # sell up to 50%
            if qty > 0:
                result = market_sell(sig["pair"], f"{qty:.8f}")
                lines.append(f"  SELL {sym}: qty={qty:.6f} -> {result}")
                trades_done += 1
    
    # BUY with available USD
    for sig in buy_signals:
        if spent >= daily_budget:
            lines.append("  Daily budget reached, stopping.")
            break
        trade_usd = min(max_trade, usd * abs(sig["size"]), usd * 0.25)  # max 25% of cash per trade
        trade_usd = min(trade_usd, daily_budget - spent)
        if trade_usd < MIN_USD_TRADE:
            continue
        result = market_buy(sig["pair"], trade_usd)
        lines.append(f"  BUY {sig['sym']}: ${trade_usd:.2f} -> {result}")
        if result.get("success"):
            spent += trade_usd
            usd -= trade_usd
            trades_done += 1
    
    lines.append("")
    lines.append(f"Trades executed: {trades_done}, Spent: ${spent:.2f}")
    
    # Save results
    report = "\n".join(lines)
    with open("/Users/burtonchatelain/.openclaw/workspace/models/latest_kernel_trade.json", "w") as f:
        json.dump({"timestamp":ts, "trades":trades_done, "spent":spent,
                   "signals":[{"sym":s["sym"],"dir":s["direction"],"conf":s["confidence"],
                              "size":s["size"]} for s in signals]}, f, indent=2)
    
    return report

if __name__ == "__main__":
    print(run())
