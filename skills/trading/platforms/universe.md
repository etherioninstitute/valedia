---
description: >
  Full asset universe — 37 core + extended assets across crypto, equity, forex.
  Plus dynamic discovery for emerging assets.
---

# universe

All assets Valedia can trade, organized by class and platform.

## Core universe (27 assets — tested and validated)

### Crypto (Coinbase)
BTC, ETH, SOL, XRP, ADA, DOGE, AVAX, LINK

### Equity (Alpaca)
SPY, QQQ, TSLA, NVDA, META, GOOGL, AMZN, AAPL, AMD, MSFT, COIN, MSTR, GLD, XLE, TLT

### Forex via ETFs (Alpaca)
UUP (USD), FXE (EUR/USD), FXY (JPY/USD), FXB (GBP/USD), FXA (AUD/USD)

## Extended universe (10 assets)

PLTR, HOOD, RIOT, MARA (crypto-correlated equities),
IWM (small caps), ARKK (innovation),
DOT, UNI, ATOM (crypto)

## Dynamic universe (hot_list.json)

Discovered every 4 hours by [[intelligence/discovery]].
Today's examples: TAO (Bittensor), HYPE (Hyperliquid), PI (Pi Network),
TRUMP, PENGU — trending on CoinGecko or Reddit.

These trade with tighter limits (2% max, 50% Kelly).
Must pass kernel validation before entering watchlist.

## Adding a new asset

One line in `unified_brain.py`:
```python
"PEPE": ("PEPE-USD", "crypto", "coinbase"),
```
Class tag auto-selects the right filter (equity or crypto).
Platform tag auto-routes to the right exchange.

## Related

- [[intelligence/discovery]] — how discovered assets are added dynamically
- [[core/filter-v3]] — equity vs crypto filter selection based on class tag
