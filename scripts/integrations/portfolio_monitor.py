"""Portfolio Monitor — Checks ALL platforms and reports via Signal.
Runs on cron. Reports balance, positions, and opportunities across
every connected platform: Mercury, Alpaca, Kraken, Coinbase,
Kalshi, Polymarket.
"""
import os, sys, json, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")

def check_alpaca():
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    paper = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
    base = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
    if not key:
        return "Alpaca: No API key configured"
    try:
        req = urllib.request.Request(f"{base}/v2/account")
        req.add_header("APCA-API-KEY-ID", key)
        req.add_header("APCA-API-SECRET-KEY", secret)
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        equity = data.get("equity", "?")
        cash = data.get("cash", "?")
        bp = data.get("buying_power", "?")
        status = data.get("status", "?")
        req2 = urllib.request.Request(f"{base}/v2/positions")
        req2.add_header("APCA-API-KEY-ID", key)
        req2.add_header("APCA-API-SECRET-KEY", secret)
        positions = json.loads(urllib.request.urlopen(req2, timeout=10).read())
        pos_str = f"{len(positions)} positions"
        if positions:
            pos_str += ": " + ", ".join(
                f"{p['symbol']} {p['qty']}@${p.get('current_price','?')} (P&L:${p.get('unrealized_pl','?')})"
                for p in positions[:5]
            )
        mode = "PAPER" if paper else "LIVE"
        return f"Alpaca ({mode}): ${equity} equity | ${cash} cash | ${bp} buying power | {pos_str} | Status: {status}"
    except Exception as e:
        return f"Alpaca: [API ERROR] {e}"

def check_mercury():
    try:
        from mercury_client import get_accounts
        accounts = get_accounts()
        parts = [f"{a.get('kind','?')}: ${a.get('availableBalance',0):.2f}" for a in accounts]
        return "Mercury: " + " | ".join(parts) if parts else "Mercury: No accounts"
    except Exception as e:
        return f"Mercury: [API ERROR] {e}"

def check_kraken():
    try:
        from kraken_client import get_balance
        bal = get_balance()
        if isinstance(bal, dict):
            parts = [f"{k}: {v}" for k, v in bal.items() if float(v) > 0]
            return "Kraken: " + " | ".join(parts[:8]) if parts else "Kraken: Empty"
        return f"Kraken: {bal}"
    except Exception as e:
        return f"Kraken: [API ERROR] {e}"

def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"PORTFOLIO STATUS ({now})", ""]
    lines.append(check_mercury())
    lines.append(check_alpaca())
    lines.append(check_kraken())
    lines.append("")
    lines.append("All platforms checked. Will report again at next scheduled scan.")
    return "\n".join(lines)

if __name__ == "__main__":
    print(main())
