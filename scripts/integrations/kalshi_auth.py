"""Kalshi Authenticated API Client - Valedia
RSA-PSS signing with read/write access.
"""
import os, time, json, base64, requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

API_KEY = os.environ.get("KALSHI_API_KEY_ID", "")
PEM_PATH = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "/Users/burtonchatelain/.openclaw/kalshi_private_key.pem")
BASE = "https://api.elections.kalshi.com/trade-api/v2"

_private_key = None
def _get_key():
    global _private_key
    if _private_key is None:
        with open(PEM_PATH, "rb") as f:
            _private_key = serialization.load_pem_private_key(f.read(), password=None)
    return _private_key

def _sign(method, path, timestamp):
    key = _get_key()
    msg = f"{timestamp}{method}{path}".encode()
    sig = key.sign(msg, asym_padding.PSS(mgf=asym_padding.MGF1(hashes.SHA256()), salt_length=asym_padding.PSS.MAX_LENGTH), hashes.SHA256())
    return base64.b64encode(sig).decode()

def _headers(method, path):
    ts = str(int(time.time() * 1000))
    return {"KALSHI-ACCESS-KEY": API_KEY, "KALSHI-ACCESS-SIGNATURE": _sign(method, path, ts), "KALSHI-ACCESS-TIMESTAMP": ts, "Content-Type": "application/json"}

def get(endpoint):
    path = f"/trade-api/v2{endpoint}"
    r = requests.get(f"{BASE}{endpoint}", headers=_headers("GET", path))
    r.raise_for_status()
    return r.json()

def post(endpoint, body=None):
    path = f"/trade-api/v2{endpoint}"
    r = requests.post(f"{BASE}{endpoint}", headers=_headers("POST", path), json=body or {})
    r.raise_for_status()
    return r.json()

def delete(endpoint):
    path = f"/trade-api/v2{endpoint}"
    r = requests.delete(f"{BASE}{endpoint}", headers=_headers("DELETE", path))
    r.raise_for_status()
    return r.json() if r.text else {}

# Read endpoints
def get_balance():
    return get("/portfolio/balance")

def get_positions():
    return get("/portfolio/positions")

def get_fills():
    return get("/portfolio/fills")

def get_orders():
    return get("/portfolio/orders")

def get_markets(limit=200, status="open"):
    return get(f"/markets?limit={limit}&status={status}")

# Write endpoints (use with caution)
def create_order(ticker, side, count, price_cents, order_type="limit"):
    """Place an order. side='yes' or 'no'. price in cents (1-99)."""
    import valedia_config as vc
    vc.require_not_halted("kalshi.create_order")
    body = {
        "ticker": ticker,
        "action": "buy",
        "side": side,
        "count": count,
        "type": order_type,
        "yes_price": price_cents if side == "yes" else None,
        "no_price": price_cents if side == "no" else None,
    }
    # Remove None values
    body = {k: v for k, v in body.items() if v is not None}
    return post("/portfolio/orders", body)

def cancel_order(order_id):
    """Cancel a resting order by id (DELETE /portfolio/orders/{id}). Needed for maker-order
    management — never place resting limits you can't pull."""
    return delete(f"/portfolio/orders/{order_id}")

def sell_position(ticker, side, count, price_cents, order_type="limit"):
    """Sell an existing position. side='yes' or 'no'. price in cents (1-99)."""
    import valedia_config as vc
    vc.require_not_halted("kalshi.sell_position")
    body = {
        "ticker": ticker,
        "action": "sell",
        "side": side,
        "count": count,
        "type": order_type,
        "yes_price": price_cents if side == "yes" else None,
        "no_price": price_cents if side == "no" else None,
    }
    body = {k: v for k, v in body.items() if v is not None}
    return post("/portfolio/orders", body)

def portfolio_summary():
    lines = ["Kalshi Portfolio"]
    try:
        bal = get_balance()
        balance_usd = bal.get("balance", 0) / 100
        portfolio_usd = bal.get("portfolio_value", 0) / 100
        lines.append(f"  Balance: ${balance_usd:,.2f}")
        lines.append(f"  Portfolio: ${portfolio_usd:,.2f}")
        
        pos = get_positions()
        positions = pos.get("market_positions", [])
        if positions:
            lines.append(f"  Positions: {len(positions)}")
            for p in positions[:5]:
                lines.append(f"    {p.get('ticker','?')}: {p.get('position',0)} contracts")
        else:
            lines.append("  No positions")
        lines.append("  Auth: OK (read/write)")
    except Exception as e:
        lines.append(f"  Error: {e}")
    return "\n".join(lines)

if __name__ == "__main__":
    if not API_KEY:
        print("Set KALSHI_API_KEY_ID env var")
    else:
        print(portfolio_summary())
