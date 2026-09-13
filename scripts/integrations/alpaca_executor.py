"""Alpaca Execution Engine — Valedia ASI
Full buy/sell/fractional for LIVE trading.
Driven by Kernel Predictor v9 signals.
(c) GOATWORTHY Post LLC.
"""
import os, json, sys
from datetime import datetime, timezone

ALPACA_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY", "")
IS_LIVE = True  # LIVE account confirmed

def _headers():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

def _base():
    return "https://api.alpaca.markets" if IS_LIVE else "https://paper-api.alpaca.markets"

def get_account():
    import requests
    r = requests.get(f"{_base()}/v2/account", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def get_positions():
    import requests
    r = requests.get(f"{_base()}/v2/positions", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def get_position(symbol):
    import requests
    r = requests.get(f"{_base()}/v2/positions/{symbol}", headers=_headers(), timeout=10)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()

def market_buy(symbol, notional=None, qty=None):
    """Buy stock by dollar amount (fractional) or quantity."""
    import requests
    order = {"symbol": symbol, "side": "buy", "type": "market", "time_in_force": "day"}
    if notional:
        order["notional"] = str(round(notional, 2))
    elif qty:
        order["qty"] = str(qty)
    else:
        return {"error": "Need notional or qty"}
    r = requests.post(f"{_base()}/v2/orders", headers=_headers(), json=order, timeout=10)
    return r.json()

def market_sell(symbol, qty=None, pct=100):
    """Sell stock. qty=None sells all. pct=50 sells half."""
    import requests
    if qty is None:
        pos = get_position(symbol)
        if not pos:
            return {"error": f"No position in {symbol}"}
        qty = float(pos["qty"]) * pct / 100
    order = {"symbol": symbol, "side": "sell", "type": "market",
             "time_in_force": "day", "qty": str(qty)}
    r = requests.post(f"{_base()}/v2/orders", headers=_headers(), json=order, timeout=10)
    return r.json()

def close_all():
    """Liquidate all positions."""
    import requests
    r = requests.delete(f"{_base()}/v2/positions", headers=_headers(), timeout=10)
    return r.json() if r.ok else {"error": r.text}

def portfolio_summary():
    acct = get_account()
    positions = get_positions()
    lines = [f"Alpaca {'LIVE' if IS_LIVE else 'PAPER'}"]
    lines.append(f"  Equity: ${float(acct['equity']):,.2f}")
    lines.append(f"  Cash: ${float(acct['cash']):,.2f}")
    lines.append(f"  Buying Power: ${float(acct['buying_power']):,.2f}")
    if positions:
        lines.append(f"  Positions ({len(positions)}):")
        for p in positions:
            pnl = float(p["unrealized_pl"])
            pnl_pct = float(p["unrealized_plpc"]) * 100
            lines.append(f"    {p['symbol']}: {p['qty']} @ ${float(p['avg_entry_price']):.2f}"
                        f" -> ${float(p['current_price']):.2f} ({pnl_pct:+.1f}%)")
    else:
        lines.append("  No positions")
    return "\n".join(lines)

if __name__ == "__main__":
    print(portfolio_summary())
