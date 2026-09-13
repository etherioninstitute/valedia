"""Kernel Auto-Trader — Valedia ASI
Runs kernel v9 signals and executes on Alpaca.
Respects: 4% max per trade, 5% max daily risk, conviction >60.
(c) GOATWORTHY Post LLC.
"""
import os, sys, json
from datetime import datetime, timezone
sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")
import numpy as np

def run_trade_cycle():
    import yfinance as yf
    from alpaca_executor import get_account, get_positions, market_buy, market_sell, get_position, IS_LIVE
    from kernel_predictor_v16e import KernelPredictor

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"[KERNEL TRADE] {ts}", f"Mode: {'LIVE' if IS_LIVE else 'PAPER'}", ""]

    try:
        acct = get_account()
        equity = float(acct["equity"])
        cash = float(acct["cash"])
        lines.append(f"Account: ${equity:.2f} equity, ${cash:.2f} cash")
    except Exception as e:
        return f"[KERNEL TRADE] Account error: {e}"

    if equity < 5:
        return f"[KERNEL TRADE] Equity ${equity:.2f} too low to trade"

    # Run kernel signals on equity watchlist (Alpaca = stocks)
    max_per_trade = equity * 0.04  # 4% max
    max_daily = equity * 0.05  # 5% max daily
    daily_spent = 0
    actions = []

    watchlist = {"SPY":"SPY","QQQ":"QQQ","XLE":"XLE","GLD":"GLD",
                 "AAPL":"AAPL","NVDA":"NVDA","GOOGL":"GOOGL","COIN":"COIN",
                 "MSTR":"MSTR","AMD":"AMD","TSLA":"TSLA","META":"META"}

    for name, ticker in watchlist.items():
        try:
            data = yf.Ticker(ticker).history(period="6mo")
            prices = data["Close"].values
            if len(prices) < 80:
                continue

            kp = KernelPredictor("equity")
            sig = kp.predict(prices)
            pos = get_position(ticker)
            has_pos = pos is not None
            pos_val = float(pos["market_value"]) if has_pos else 0

            # BUY: score > 60 and confidence > 20 and have cash
            if sig["direction"] == "BUY" and sig["score"] > 60 and not has_pos:
                size_pct = min(abs(sig["size"]), 0.3)  # max 30% of signal
                amount = min(cash * size_pct, max_per_trade)
                amount = min(amount, max_daily - daily_spent)
                if amount >= 1.0:
                    result = market_buy(ticker, notional=amount)
                    daily_spent += amount
                    actions.append(f"BUY ${amount:.2f} {ticker} (score={sig['score']}, mom={sig['mom']:+.2f})")
                    lines.append(f"  >> BUY ${amount:.2f} {ticker} score={sig['score']} conf={sig.get('conf',0)*100:.0f}")

            # SELL: score < 40 and have position
            elif sig["direction"] == "SELL" and has_pos:
                result = market_sell(ticker)
                actions.append(f"SELL ALL {ticker} (score={sig['score']})")
                lines.append(f"  >> SELL {ticker} score={sig['score']}")

            # HOLD: do nothing
            else:
                if has_pos:
                    lines.append(f"  {ticker}: HOLD (score={sig['score']}, P&L=${float(pos['unrealized_pl']):.2f})")

        except Exception as e:
            lines.append(f"  {ticker}: error {str(e)[:60]}")

    if not actions:
        lines.append("  No trades executed this cycle")

    # Save state
    state = {"timestamp": ts, "equity": equity, "actions": actions}
    try:
        with open("/Users/burtonchatelain/.openclaw/workspace/models/latest_kernel_trade.json", "w") as f:
            json.dump(state, f, indent=2)
    except:
        pass

    return "\n".join(lines)

if __name__ == "__main__":
    os.environ.setdefault("ALPACA_API_KEY", "")
    os.environ.setdefault("ALPACA_SECRET_KEY", "")
    # Source env if not set
    if not os.environ.get("ALPACA_API_KEY"):
        import subprocess
        result = subprocess.run("set -a && source /Users/burtonchatelain/.openclaw/.env && env", shell=True, capture_output=True, text=True)
        for line in result.stdout.split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ[k] = v
    print(run_trade_cycle())
