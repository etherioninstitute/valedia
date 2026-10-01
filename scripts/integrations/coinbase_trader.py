"""Coinbase Advanced Trade Executor — Valedia ASI
Executes market buys/sells on Coinbase using v3 brokerage API.
(c) GOATWORTHY Post LLC.
"""
import json, jwt, time, secrets, requests, os
import platform
from coinbase_order_body import build_market_order_body

# Route through VPS SOCKS proxy on Mac (IP-locked API)
_USE_PROXY = platform.system() == "Darwin"
_PROXIES = None
if _USE_PROXY:
    try:
        from vps_proxy import get_proxies, ensure_tunnel
        ensure_tunnel()
        _PROXIES = get_proxies()
    except: pass
from cryptography.hazmat.primitives.serialization import load_pem_private_key

KEY_FILE = "/Users/burtonchatelain/.openclaw/coinbase_key.json"

def _load_key():
    with open(KEY_FILE) as f:
        k = json.load(f)
    return k["name"], load_pem_private_key(k["privateKey"].encode(), password=None)

def _request(method, path, body=None):
    key_name, private_key = _load_key()
    now = int(time.time())
    uri = f"{method} api.coinbase.com{path}"
    payload = {"sub":key_name,"iss":"cdp","nbf":now,"exp":now+120,"uri":uri}
    token = jwt.encode(payload, private_key, algorithm="ES256",
                       headers={"kid":key_name,"nonce":secrets.token_hex(16)})
    h = {"Authorization":f"Bearer {token}","Content-Type":"application/json"}
    url = f"https://api.coinbase.com{path}"
    if method == "GET":
        return requests.get(url, headers=h, timeout=15, proxies=_PROXIES)
    return requests.post(url, headers=h, json=body, timeout=15, proxies=_PROXIES)

def get_balances():
    """Get all non-zero balances."""
    r = _request("GET", "/api/v3/brokerage/accounts")
    if not r.ok: return {"error": r.text[:200]}
    out = {}
    for a in r.json().get("accounts", []):
        val = float(a.get("available_balance",{}).get("value",0))
        if val > 0:
            out[a["currency"]] = {"balance": val, "uuid": a["uuid"]}
    return out

def get_price(pair="BTC-USD"):
    r = _request("GET", f"/api/v3/brokerage/products/{pair}")
    return float(r.json()["price"]) if r.ok else 0

def _fill_fields(d):
    """Pass through venue fill fields if the POST body has them. Quote size is not a fill."""
    sr = d.get("success_response") if isinstance(d.get("success_response"), dict) else {}
    out = {}
    for k in ("filled_size", "average_filled_price", "total_fees", "completion_percentage"):
        if k in sr:
            out[k] = sr[k]
        elif k in d:
            out[k] = d[k]
    return out

def market_buy(pair, usd_amount):
    """Market buy $usd_amount worth of crypto. Returns order result (fill fields if present)."""
    import valedia_config as vc
    vc.require_not_halted("coinbase.market_buy")
    body = build_market_order_body(pair, "BUY", usd_amount=usd_amount)
    r = _request("POST", "/api/v3/brokerage/orders", body)
    if r.ok:
        d = r.json()
        success = d.get("success", False)
        oid = d.get("success_response", {}).get("order_id", "?")
        rec = {"success": success, "order_id": oid, "pair": pair, "usd": usd_amount}
        rec.update(_fill_fields(d))
        return rec
    return {"success": False, "error": r.text[:300]}

def market_sell(pair, crypto_amount):
    """Market sell crypto_amount of base currency."""
    import valedia_config as vc
    vc.require_not_halted("coinbase.market_sell")
    body = build_market_order_body(pair, "SELL", crypto_amount=crypto_amount)
    r = _request("POST", "/api/v3/brokerage/orders", body)
    if r.ok:
        d = r.json()
        success = d.get("success", False)
        oid = d.get("success_response", {}).get("order_id", "?")
        rec = {"success": success, "order_id": oid, "pair": pair, "qty": crypto_amount}
        rec.update(_fill_fields(d))
        return rec
    return {"success": False, "error": r.text[:300]}

def get_order(order_id):
    """Read-only GET of one order by id. Not a fill until qty+price parse complete.

    Path: /api/v3/brokerage/orders/historical/{order_id}. Fail-closed → None.
    Slice 7: called from spot_order_get only when SPOT_RECONCILE_HTTP is on.
    """
    if not order_id:
        return None
    try:
        r = _request("GET", f"/api/v3/brokerage/orders/historical/{order_id}")
        if not r.ok:
            return None
        body = r.json()
        return body if isinstance(body, dict) else None
    except Exception:
        return None


def recent_buy_orders(pair, since_iso, request=None):
    """Read-only: BUY orders for `pair` created at/after since_iso (RFC3339) that may have
    executed (FAILED, and zero-fill CANCELLED/EXPIRED, dropped). [] = venue says none.
    None on ANY error = unknown (callers must never treat it as "none").
    Used by strategy_modules.DCAEngine before retrying an ambiguous buy. `request` is a test seam.
    """
    from urllib.parse import urlencode
    try:
        q = urlencode({"product_ids": pair, "order_side": "BUY", "start_date": since_iso,
                       "limit": 50})
        r = (request or _request)("GET", f"/api/v3/brokerage/orders/historical/batch?{q}")
        if not r.ok:
            return None
        out = []
        for o in r.json().get("orders", []) or []:
            st = str(o.get("status") or "").upper()
            filled = float(o.get("filled_size") or 0)
            if o.get("product_id") not in (None, pair) or str(o.get("side") or "BUY").upper() != "BUY":
                continue
            if st == "FAILED" or (st in ("CANCELLED", "EXPIRED") and filled <= 0):
                continue
            out.append({k: o.get(k) for k in ("order_id", "client_order_id", "status",
                                              "created_time", "filled_size",
                                              "average_filled_price", "total_fees")})
        return out
    except Exception:
        return None


def get_orders(status="OPEN"):
    r = _request("GET", f"/api/v3/brokerage/orders/historical?order_status={status}&limit=10")
    if not r.ok: return []
    return [{"id":o["order_id"][:8],"pair":o["product_id"],"side":o["side"],
             "status":o["status"],"filled":o.get("filled_size","0")}
            for o in r.json().get("orders",[])]

if __name__ == "__main__":
    print("=== COINBASE TRADER TEST ===")
    bal = get_balances()
    print(f"Balances: {bal}")
    for pair in ["BTC-USD","ETH-USD","SOL-USD","XRP-USD"]:
        print(f"  {pair}: ${get_price(pair):.2f}")
    print(f"Open orders: {get_orders()}")
