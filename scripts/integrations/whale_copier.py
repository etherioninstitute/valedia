"""Whale Copy Trader — Valedia ASI
Monitors large crypto wallet movements and mirrors trades.
Uses Etherscan/blockchain APIs for on-chain whale detection.
(c) GOATWORTHY Post LLC.
"""
import requests, json
from datetime import datetime, timezone

# Known whale wallets to track (top holders, funds, smart money)
TRACKED_WALLETS = {
    "Binance Hot": "0x28C6c06298d514Db089934071355E5743bf21d60",
    "Jump Trading": "0xf584F8728B874a6a5c7A8d4d387C9aae9172D621",
    "Wintermute": "0x0000006daea1723962647b7e189d311d757Fb793",
}

def check_whale_moves():
    """Check recent large ETH transfers from tracked wallets."""
    lines = [f"[WHALE] {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"]
    for name, addr in TRACKED_WALLETS.items():
        try:
            url = f"https://api.etherscan.io/api?module=account&action=txlist&address={addr}&startblock=0&endblock=99999999&page=1&offset=5&sort=desc"
            r = requests.get(url, timeout=15)
            if r.ok:
                txs = r.json().get("result", [])
                if isinstance(txs, list):
                    for tx in txs[:3]:
                        val_eth = int(tx.get("value", 0)) / 1e18
                        if val_eth > 10:
                            direction = "OUT" if tx["from"].lower() == addr.lower() else "IN"
                            lines.append(f"  {name}: {direction} {val_eth:.2f} ETH")
        except Exception as e:
            lines.append(f"  {name}: error {str(e)[:40]}")
    if len(lines) == 1:
        lines.append("  No significant whale moves detected")
    return "\n".join(lines)

def check_coinbase_large_orders():
    """Check Coinbase orderbook for large resting orders (smart money signals)."""
    lines = ["  Large orders on Coinbase:"]
    for pair in ["BTC-USD", "ETH-USD"]:
        try:
            r = requests.get(f"https://api.exchange.coinbase.com/products/{pair}/book?level=2", timeout=10)
            if r.ok:
                book = r.json()
                bids = book.get("bids", [])
                asks = book.get("asks", [])
                big_bids = [b for b in bids[:50] if float(b[1]) * float(b[0]) > 50000]
                big_asks = [a for a in asks[:50] if float(a[1]) * float(a[0]) > 50000]
                if big_bids or big_asks:
                    lines.append(f"    {pair}: {len(big_bids)} big bids, {len(big_asks)} big asks")
                    bid_wall = sum(float(b[1])*float(b[0]) for b in big_bids)
                    ask_wall = sum(float(a[1])*float(a[0]) for a in big_asks)
                    bias = "BUY PRESSURE" if bid_wall > ask_wall*1.5 else "SELL PRESSURE" if ask_wall > bid_wall*1.5 else "BALANCED"
                    lines.append(f"      Bid wall: ${bid_wall/1e3:.0f}K Ask wall: ${ask_wall/1e3:.0f}K -> {bias}")
        except: pass
    return "\n".join(lines)

if __name__ == "__main__":
    print(check_whale_moves())
    print(check_coinbase_large_orders())
