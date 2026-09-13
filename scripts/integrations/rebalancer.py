"""Portfolio Rebalancer — Valedia ASI
Maintains target allocation across assets.
(c) GOATWORTHY Post LLC.
"""
import os, sys, json
from datetime import datetime, timezone
sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")

TARGET_ALLOC = {
    "BTC-USD": 0.35,
    "ETH-USD": 0.25,
    "SOL-USD": 0.20,
    "XRP-USD": 0.10,
    "ADA-USD": 0.05,
    "AVAX-USD": 0.05,
}
REBAL_THRESHOLD = 0.05  # rebalance if >5% off target

def run_rebalance():
    from coinbase_trader import get_balances, get_price, market_buy, market_sell
    ts = datetime.now(timezone.utc).isoformat()
    lines = [f"[REBAL] {ts}"]
    bal = get_balances()
    usd = bal.get("USD", {}).get("balance", 0)
    # Compute current values
    total = usd
    holdings = {}
    for ticker, target_pct in TARGET_ALLOC.items():
        base = ticker.replace("-USD","")
        amt = bal.get(base, {}).get("balance", 0)
        price = get_price(ticker) if amt > 0 else 0
        val = amt * price
        holdings[ticker] = {"amount": amt, "price": price, "value": val}
        total += val
    if total < 5:
        return "[REBAL] Total portfolio < $5, skipping"
    lines.append(f"  Total: ${total:.2f} (${usd:.2f} USD)")
    # Check each asset
    actions = []
    for ticker, target_pct in TARGET_ALLOC.items():
        h = holdings[ticker]
        current_pct = h["value"] / total if total > 0 else 0
        diff = current_pct - target_pct
        lines.append(f"  {ticker:10s}: ${h['value']:.2f} ({current_pct*100:.1f}% vs {target_pct*100:.0f}% target) diff={diff*100:+.1f}%")
        if abs(diff) > REBAL_THRESHOLD:
            trade_val = abs(diff) * total
            if diff > 0 and h["amount"] > 0:
                sell_amt = trade_val / h["price"] if h["price"] > 0 else 0
                if sell_amt > 0.0001:
                    actions.append(f"SELL {sell_amt:.6f} {ticker} (${trade_val:.2f})")
            elif diff < 0 and usd >= 1:
                buy_val = min(trade_val, usd * 0.9)
                if buy_val >= 1:
                    actions.append(f"BUY ${buy_val:.2f} {ticker}")
    if actions:
        lines.append(f"  Rebalance needed: {len(actions)} trades")
        for a in actions: lines.append(f"    >> {a}")
        lines.append("  (Execute manually or enable auto-rebalance)")
    else:
        lines.append("  Portfolio within tolerance")
    return "\n".join(lines)

if __name__ == "__main__":
    print(run_rebalance())
