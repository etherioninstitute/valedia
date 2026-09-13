"""Coinbase Advanced Trade Executor — Valedia ASI
Executes market buys/sells on Coinbase using v3 brokerage API.
(c) GOATWORTHY Post LLC.
"""
import json, jwt, time, secrets, requests, os
import platform

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

def market_buy(pair, usd_amount):
    """Market buy $usd_amount worth of crypto. Returns order result."""
    body = {
        "client_order_id": secrets.token_hex(16),
        "product_id": pair,
        "side": "BUY",
        "order_configuration": {"market_market_ioc": {"quote_size": f"{usd_amount:.2f}"}}
    }
    r = _request("POST", "/api/v3/brokerage/orders", body)
    if r.ok:
        d = r.json()
        success = d.get("success", False)
        oid = d.get("success_response", {}).get("order_id", "?")
        return {"success": success, "order_id": oid, "pair": pair, "usd": usd_amount}
    return {"success": False, "error": r.text[:300]}

def market_sell(pair, crypto_amount):
    """Market sell crypto_amount of base currency."""
    body = {
        "client_order_id": secrets.token_hex(16),
        "product_id": pair,
        "side": "SELL",
        "order_configuration": {"market_market_ioc": {"base_size": f"{crypto_amount}"}}
    }
    r = _request("POST", "/api/v3/brokerage/orders", body)
    if r.ok:
        d = r.json()
        success = d.get("success", False)
        oid = d.get("success_response", {}).get("order_id", "?")
        return {"success": success, "order_id": oid, "pair": pair, "qty": crypto_amount}
    return {"success": False, "error": r.text[:300]}

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
