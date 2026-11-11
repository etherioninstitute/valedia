"""Kraken Exchange Client - Abundance Agent
Query balances, staking/Earn positions, open orders, trade history, ledger.
Permissions: Read-only (Query funds, Earn, orders, ledger). IP-locked to VPS.
NO withdraw/deposit/create-order permissions - safety first.
"""
import os, json, time, hashlib, hmac, base64, urllib.parse
import requests

# Load from secure JSON (env vars mangle +/= chars)
_kraken_keys_path = "/Users/burtonchatelain/.openclaw/kraken_keys.json"
try:
    with open(_kraken_keys_path) as _kf:
        _kk = json.load(_kf)
    KRAKEN_API_KEY = _kk["api_key"]
    KRAKEN_PRIVATE_KEY = _kk["private_key"]
except:
    KRAKEN_API_KEY = os.environ.get("KRAKEN_API_KEY", "")
    KRAKEN_PRIVATE_KEY = os.environ.get("KRAKEN_PRIVATE_KEY", "")
KRAKEN_BASE = "https://api.kraken.com"

# Kraken keys are IP-locked to the VPS (85.239.234.32). Valedia now runs on the Mac,
# so route Kraken calls through the VPS SOCKS proxy to present the trusted egress IP.
KRAKEN_PROXY = os.environ.get("KRAKEN_SOCKS_PROXY", "socks5h://127.0.0.1:1080")
_PROXIES = {"http": KRAKEN_PROXY, "https": KRAKEN_PROXY} if KRAKEN_PROXY else None

def _sign(urlpath, data):
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data["nonce"]) + postdata).encode()
    message = urlpath.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(KRAKEN_PRIVATE_KEY), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()

def _private(endpoint, extra=None):
    urlpath = f"/0/private/{endpoint}"
    data = {"nonce": str(int(time.time() * 1000))}
    if extra:
        data.update(extra)
    headers = {"API-Key": KRAKEN_API_KEY, "API-Sign": _sign(urlpath, data)}
    r = requests.post(f"{KRAKEN_BASE}{urlpath}", headers=headers, data=data, proxies=_PROXIES, timeout=30)
    r.raise_for_status()
    result = r.json()
    if result.get("error"):
        raise Exception(f"Kraken API error: {result['error']}")
    return result.get("result", {})

def _public(endpoint, params=None):
    r = requests.get(f"{KRAKEN_BASE}/0/public/{endpoint}", params=params or {}, proxies=_PROXIES, timeout=30)
    r.raise_for_status()
    result = r.json()
    return result.get("result", {})

def get_balance():
    return _private("Balance")

def get_trade_balance(asset="ZUSD"):
    return _private("TradeBalance", {"asset": asset})

def get_open_orders():
    return _private("OpenOrders")

def get_closed_orders():
    return _private("ClosedOrders")

def get_trades_history():
    return _private("TradesHistory")

def get_ledger():
    return _private("Ledgers")

def get_earn_strategies():
    return _private("Earn/Strategies")

def get_earn_allocations():
    return _private("Earn/Allocations")

def get_ticker(pair="XBTUSD"):
    return _public("Ticker", {"pair": pair})

def get_top_movers():
    pairs = "XBTUSD,ETHUSD,XRPUSD,SOLUSD,ADAUSD,DOTUSD,AVAXUSD,LINKUSD"
    return _public("Ticker", {"pair": pairs})

def portfolio_summary():
    lines = ["Kraken Portfolio Summary"]
    try:
        bal = get_balance()
        if bal:
            for asset, amount in sorted(bal.items()):
                amt = float(amount)
                if amt > 0.001:
                    lines.append(f"  {asset}: {amt:.6f}")
        tb = get_trade_balance()
        if tb:
            equity = float(tb.get("eb", 0))
            lines.append(f"  Equity (USD): ${equity:,.2f}")
        try:
            earn = get_earn_allocations()
            if earn and earn.get("items"):
                lines.append("  Staking/Earn:")
                for item in earn["items"]:
                    native = item.get("amount_allocated", {}).get("native", {})
                    sid = item.get("strategy_id", "?")
                    total = native.get("total", "0")
                    pending = native.get("pending", "0")
                    lines.append(f"    {sid}: {total} (pending: {pending})")
        except:
            lines.append("  Staking: No positions")
    except Exception as e:
        lines.append(f"  Error: {e}")
    return "\n".join(lines)

def staking_opportunities():
    lines = ["Kraken Earn Opportunities"]
    try:
        strategies = get_earn_strategies()
        if strategies and strategies.get("items"):
            for s in strategies["items"][:10]:
                apy = s.get("yield_source", {}).get("value", "?")
                asset = s.get("asset", "?")
                lock = s.get("lock_type", {}).get("type", "?")
                lines.append(f"  {asset}: {apy}% APY | Lock: {lock}")
    except Exception as e:
        lines.append(f"  Error: {e}")
    return "\n".join(lines)

if __name__ == "__main__":
    if not KRAKEN_API_KEY:
        print("Set KRAKEN_API_KEY and KRAKEN_PRIVATE_KEY env vars")
    else:
        print(portfolio_summary())
        print()
        print(staking_opportunities())
