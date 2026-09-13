"""Coinbase WebSocket — Valedia ASI
Real-time price feeds from Coinbase. Free, no auth needed.
Collects tick data for sub-daily kernel signals.
(c) GOATWORTHY Post LLC.
"""
import json, time, os
from datetime import datetime, timezone

TICK_DIR = "/Users/burtonchatelain/.openclaw/workspace/models/ticks"

def ensure_dir():
    os.makedirs(TICK_DIR, exist_ok=True)

def collect_ticks(pairs=["BTC-USD","ETH-USD","SOL-USD"], duration_sec=60):
    """Collect real-time ticks via Coinbase REST (WebSocket needs asyncio).
    Polls every 2 seconds for duration_sec."""
    import requests
    ensure_dir()
    ticks = {p: [] for p in pairs}
    start = time.time()
    while time.time() - start < duration_sec:
        for pair in pairs:
            try:
                r = requests.get(f"https://api.exchange.coinbase.com/products/{pair}/ticker", timeout=5)
                if r.ok:
                    d = r.json()
                    ticks[pair].append({
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "price": float(d["price"]),
                        "volume": float(d.get("volume", 0)),
                        "bid": float(d.get("bid", 0)),
                        "ask": float(d.get("ask", 0)),
                    })
            except: pass
        time.sleep(2)
    # Save
    for pair, data in ticks.items():
        if data:
            fname = os.path.join(TICK_DIR, f"{pair.replace('-','_')}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.json")
            with open(fname, "w") as f: json.dump(data, f)
    return {p: len(d) for p, d in ticks.items()}

def get_intraday_prices(pair="BTC-USD", granularity=3600):
    """Get hourly candles from Coinbase (free, no auth)."""
    import requests
    r = requests.get(f"https://api.exchange.coinbase.com/products/{pair}/candles?granularity={granularity}", timeout=10)
    if r.ok:
        candles = r.json()  # [[time, low, high, open, close, volume], ...]
        return [{"ts": c[0], "open": c[3], "high": c[2], "low": c[1], "close": c[4], "vol": c[5]} for c in candles]
    return []

def hourly_prices_array(pair="BTC-USD"):
    """Return close prices as numpy array for kernel input."""
    import numpy as np
    candles = get_intraday_prices(pair, 3600)
    if not candles: return None
    candles.sort(key=lambda x: x["ts"])
    return np.array([c["close"] for c in candles])

if __name__ == "__main__":
    print("Collecting 30s of ticks...")
    result = collect_ticks(duration_sec=30)
    print(f"Ticks collected: {result}")
    for pair in ["BTC-USD","ETH-USD"]:
        prices = hourly_prices_array(pair)
        if prices is not None:
            print(f"{pair}: {len(prices)} hourly candles, latest=${prices[-1]:.2f}")
