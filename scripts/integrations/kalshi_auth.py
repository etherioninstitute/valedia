"""Kalshi Authenticated API Client - Valedia
RSA-PSS signing with read/write access.
Create/sell/cancel use Trade API V2 (POST/DELETE /portfolio/events/orders).
"""
import os, time, json, base64, requests
from urllib.parse import quote
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


def get_series(series_ticker):
    """GET /series/{ticker} — current fee_type + fee_multiplier (read)."""
    return get(f"/series/{quote(str(series_ticker), safe='')}")


def get_series_fee_changes(series_ticker=None, show_historical=True):
    """GET /series/fee_changes?series_ticker=&show_historical= (read)."""
    q = [f"show_historical={'true' if show_historical else 'false'}"]
    if series_ticker:
        q.insert(0, f"series_ticker={quote(str(series_ticker), safe='')}")
    return get("/series/fee_changes?" + "&".join(q))


def get_event_fee_changes(event_ticker=None, limit=100, cursor=None):
    """GET /events/fee_changes — event override on parent series (read)."""
    q = [f"limit={int(limit)}"]
    if event_ticker:
        q.append(f"event_ticker={quote(str(event_ticker), safe='')}")
    if cursor:
        q.append(f"cursor={quote(str(cursor), safe='')}")
    return get("/events/fee_changes?" + "&".join(q))

# Write endpoints — Create/Cancel Order V2 (legacy POST /portfolio/orders 410s)
CREATE_ORDER_V2 = "/portfolio/events/orders"


def _fp_count(count):
    return f"{float(count):.2f}"


def _fp_dollars_from_cents(cents):
    return f"{int(cents) / 100.0:.4f}"


def _tif(order_type):
    t = str(order_type or "limit").lower().replace("-", "_")
    if t in ("fok", "fill_or_kill"):
        return "fill_or_kill"
    if t in ("ioc", "immediate_or_cancel", "market"):
        return "immediate_or_cancel"
    return "good_till_canceled"


def yes_leg_v2(action, side, price_cents):
    """Map legacy buy/sell + yes/no to V2 YES-leg bid/ask + YES cents.

    V2 BookSide is YES-only: bid = buy YES, ask = sell YES.
    Buy NO @ C ≡ ask YES @ (100-C). Sell NO @ C ≡ bid YES @ (100-C).
    """
    a = str(action or "").strip().lower()
    s = str(side or "").strip().lower()
    try:
        c = int(price_cents)
    except (TypeError, ValueError):
        raise ValueError(f"price_cents must be int 1-99, got {price_cents!r}")
    if a not in ("buy", "sell"):
        raise ValueError(f"action must be buy or sell, got {action!r}")
    if s not in ("yes", "no"):
        raise ValueError(f"side must be yes or no, got {side!r}")
    if c < 1 or c > 99:
        raise ValueError(f"price_cents must be 1-99, got {c}")
    if a == "buy" and s == "yes":
        return "bid", c
    if a == "buy" and s == "no":
        return "ask", 100 - c
    if a == "sell" and s == "yes":
        return "ask", c
    return "bid", 100 - c


def create_order_v2_body(ticker, side, count, price_cents, order_type="limit",
                         action="buy"):
    book_side, yes_cents = yes_leg_v2(action, side, price_cents)
    return {
        "ticker": ticker,
        "side": book_side,
        "count": _fp_count(count),
        "price": _fp_dollars_from_cents(yes_cents),
        "time_in_force": _tif(order_type),
        "self_trade_prevention_type": "taker_at_cross",
    }


def _wrap_v2_ack(raw):
    """Keep nested order.order_id for maker/sports callers; V2 ack is flat."""
    raw = dict(raw) if isinstance(raw, dict) else {}
    if raw.get("order_id") and not isinstance(raw.get("order"), dict):
        raw["order"] = {k: v for k, v in raw.items() if k != "order"}
    return raw


def get_order(order_id):
    """GET /portfolio/orders/{id} — read path (not the deprecated POST create)."""
    return get(f"/portfolio/orders/{order_id}")


def create_order(ticker, side, count, price_cents, order_type="limit", informed=None):
    """Place a buy. side='yes' or 'no'. price in cents (1-99).

    Halt is necessary, not sufficient. informed-before-create must pass.
    """
    import valedia_config as vc
    import informed_before_create as ibc
    vc.require_not_halted("kalshi.create_order")
    ibc.require_kalshi(
        ticker, side, count, price_cents, order_type, informed, action="buy")
    body = create_order_v2_body(ticker, side, count, price_cents, order_type, "buy")
    return _wrap_v2_ack(post(CREATE_ORDER_V2, body))


def cancel_order(order_id, ticker=None):
    """Cancel via DELETE /portfolio/events/orders/{id}. ticker enables auto-route."""
    path = f"/portfolio/events/orders/{order_id}"
    if ticker:
        path += f"?market_ticker={quote(str(ticker), safe='')}"
    return delete(path)


def sell_position(ticker, side, count, price_cents, order_type="limit", informed=None):
    """Sell an existing position. side='yes' or 'no'. price in cents (1-99)."""
    import valedia_config as vc
    import informed_before_create as ibc
    vc.require_not_halted("kalshi.sell_position")
    ibc.require_kalshi(
        ticker, side, count, price_cents, order_type, informed, action="sell")
    body = create_order_v2_body(ticker, side, count, price_cents, order_type, "sell")
    body["reduce_only"] = True
    return _wrap_v2_ack(post(CREATE_ORDER_V2, body))

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
