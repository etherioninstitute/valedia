"""Alpaca Execution Engine — Valedia ASI
Full buy/sell/fractional for LIVE trading.
Driven by Kernel Predictor v9 signals.
(c) GOATWORTHY Post LLC.
"""
import os, json, sys
from datetime import datetime, timezone

ALPACA_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY", "")

def alpaca_is_live():
    """Single env flag: ALPACA_PAPER. Unset → live (executor default). No key edits."""
    env = os.environ.get("ALPACA_PAPER")
    if env is None or str(env).strip() == "":
        return True
    return str(env).strip().lower() not in ("1", "true", "yes", "on")

def __getattr__(name):
    if name == "IS_LIVE":
        return alpaca_is_live()
    raise AttributeError(name)

def _headers():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

def _base():
    return "https://api.alpaca.markets" if alpaca_is_live() else "https://paper-api.alpaca.markets"

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

def get_order(order_id):
    """Read-only GET of one order by id. Not a fill until qty+price parse complete.

    Path: GET /v2/orders/{order_id}. Fail-closed → None.
    Slice 7: called from spot_order_get only when SPOT_RECONCILE_HTTP is on.
    """
    if not order_id:
        return None
    import requests
    try:
        r = requests.get(f"{_base()}/v2/orders/{order_id}", headers=_headers(), timeout=10)
        if not r.ok:
            return None
        body = r.json()
        return body if isinstance(body, dict) else None
    except Exception:
        return None

def recent_buy_orders(symbol, since_iso, get=None):
    """Read-only: buy orders for `symbol` submitted after since_iso that may have executed
    (rejected, and zero-fill canceled/expired, dropped). [] = venue says none.
    None on ANY error = unknown (callers must never treat it as "none").
    Used by strategy_modules.DCAEngine before retrying an ambiguous buy. `get` is a test seam.
    """
    try:
        if get is None:
            import requests
            get = requests.get
        r = get(f"{_base()}/v2/orders", headers=_headers(), timeout=10,
                params={"status": "all", "after": since_iso, "symbols": symbol,
                        "side": "buy", "limit": 50})
        if not r.ok:
            return None
        out = []
        for o in r.json() or []:
            st = str(o.get("status") or "").lower()
            filled = float(o.get("filled_qty") or 0)
            if o.get("symbol") not in (None, symbol) or str(o.get("side") or "buy").lower() != "buy":
                continue
            if st == "rejected" or (st in ("canceled", "expired") and filled <= 0):
                continue
            out.append({"order_id": o.get("id"), "client_order_id": o.get("client_order_id"),
                        "status": st, "filled_qty": o.get("filled_qty"),
                        "filled_avg_price": o.get("filled_avg_price"),
                        "notional": o.get("notional")})
        return out
    except Exception:
        return None

def market_buy(symbol, notional=None, qty=None):
    """Buy stock by dollar amount (fractional) or quantity."""
    import valedia_config as vc
    vc.require_not_halted("alpaca.market_buy")
    import requests
    order = {"symbol": symbol, "side": "buy", "type": "market", "time_in_force": "day"}
    if notional:
        order["notional"] = str(round(notional, 2))
    elif qty:
        order["qty"] = str(qty)
    else:
        return {"error": "Need notional or qty"}
    r = requests.post(f"{_base()}/v2/orders", headers=_headers(), json=order, timeout=10)
    if r.status_code >= 500 or r.status_code == 408:  # order may exist → ambiguous, look it up
        return {"error": r.text[:300], "http_status": r.status_code, "ambiguous": True}
    body = r.json()
    if not r.ok and isinstance(body, dict):  # 4xx with a body: explicit Alpaca rejection
        body.update(http_status=r.status_code, rejected=True)
    return body

def market_sell(symbol, qty=None, pct=100):
    """Sell stock. qty=None sells all. pct=50 sells half."""
    import valedia_config as vc
    vc.require_not_halted("alpaca.market_sell")
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
    lines = [f"Alpaca {'LIVE' if alpaca_is_live() else 'PAPER'}"]
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
