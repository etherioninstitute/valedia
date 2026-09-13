"""Coinbase Advanced Trade Client - Abundance Agent
Crypto spot trading + staking. Complements Kraken for redundancy.
Uses JSON key file (EC private key).
"""
import os, json
from coinbase.rest import RESTClient

CB_KEY_FILE = "/Users/burtonchatelain/.openclaw/coinbase_key.json"

def get_client():
    with open(CB_KEY_FILE) as f:
        key_data = json.load(f)
    return RESTClient(api_key=key_data["name"], api_secret=key_data["privateKey"])

def get_accounts():
    client = get_client()
    resp = client.get_accounts()
    results = []
    if hasattr(resp, 'accounts'):
        for a in resp.accounts:
            bal = float(a.get("available_balance", {}).get("value", 0) if isinstance(a, dict) else getattr(getattr(a, 'available_balance', None), 'value', 0) or 0)
            curr = a.get("currency", "?") if isinstance(a, dict) else getattr(a, 'currency', '?')
            name = a.get("name", "?") if isinstance(a, dict) else getattr(a, 'name', '?')
            if bal > 0.0001:
                results.append({"name": name, "currency": curr, "balance": bal})
    return results

def get_product_price(product_id="BTC-USD"):
    client = get_client()
    try:
        resp = client.get_product(product_id)
        price = resp.get("price") if isinstance(resp, dict) else getattr(resp, 'price', None)
        return float(price) if price else None
    except:
        return None

def portfolio_summary():
    lines = ["Coinbase Portfolio"]
    try:
        accts = get_accounts()
        if accts:
            for a in accts:
                lines.append(f"  {a['currency']}: {a['balance']:.6f} ({a['name']})")
        else:
            lines.append("  No funded accounts (or new account)")
        
        for sym in ["BTC-USD", "ETH-USD", "XRP-USD", "SOL-USD"]:
            price = get_product_price(sym)
            if price:
                lines.append(f"  {sym}: ${price:,.2f}")
        lines.append("  Auth: OK")
    except Exception as e:
        lines.append(f"  Error: {e}")
    return "\n".join(lines)

if __name__ == "__main__":
    print(portfolio_summary())
