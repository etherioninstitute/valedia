"""Alpaca Trading Client - Abundance Agent
Paper + live stock/options trading. Free API, low fees.
Ideal for Pelosi-edge swing trades and diversification.
"""
import os
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetAssetsRequest
from alpaca.trading.enums import OrderSide, TimeInForce, AssetClass
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

ALPACA_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY", "")
PAPER = os.environ.get("ALPACA_PAPER", "true").lower() == "true"

def get_client():
    return TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=PAPER)

def get_account():
    client = get_client()
    acct = client.get_account()
    return {
        "equity": float(acct.equity),
        "cash": float(acct.cash),
        "buying_power": float(acct.buying_power),
        "portfolio_value": float(acct.portfolio_value),
        "status": acct.status,
    }

def get_positions():
    client = get_client()
    positions = client.get_all_positions()
    return [{
        "symbol": p.symbol,
        "qty": float(p.qty),
        "avg_entry": float(p.avg_entry_price),
        "current": float(p.current_price),
        "pnl": float(p.unrealized_pl),
        "pnl_pct": float(p.unrealized_plpc) * 100,
    } for p in positions]

def get_orders(status="open"):
    client = get_client()
    return client.get_orders(filter={"status": status})

def paper_buy(symbol, qty=1, notional=None):
    """Paper trade buy order."""
    client = get_client()
    if notional:
        req = MarketOrderRequest(symbol=symbol, notional=notional, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
    else:
        req = MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
    return client.submit_order(req)

def portfolio_summary():
    lines = ["Alpaca Portfolio"]
    try:
        acct = get_account()
        lines.append(f"  Equity: ${acct['equity']:,.2f}")
        lines.append(f"  Cash: ${acct['cash']:,.2f}")
        lines.append(f"  Buying Power: ${acct['buying_power']:,.2f}")
        lines.append(f"  Status: {acct['status']}")
        positions = get_positions()
        if positions:
            lines.append(f"  Positions ({len(positions)}):")
            for p in positions:
                arrow = "+" if p["pnl"] > 0 else ""
                lines.append(f"    {p['symbol']}: {p['qty']} @ ${p['avg_entry']:.2f} -> ${p['current']:.2f} ({arrow}{p['pnl_pct']:.1f}%)")
        else:
            lines.append("  No open positions")
    except Exception as e:
        lines.append(f"  Error: {e}")
    return "\n".join(lines)

if __name__ == "__main__":
    if not ALPACA_KEY:
        print("Set ALPACA_API_KEY and ALPACA_SECRET_KEY env vars")
        print("Get free keys at: https://app.alpaca.markets/signup")
    else:
        print(portfolio_summary())
