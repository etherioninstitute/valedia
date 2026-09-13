---
description: >
  All intelligence sources feeding Valedia's brain: Fear/Greed, insider trades,
  hedge fund 13F filings, whale monitoring, and macro signals.
---

# intel-sources

Pre-market intelligence gathered every night. Feeds signal fusion and
shapes the context before kernel runs.

## Cron schedule (Pacific Time)

| Time | Script | What it gathers |
|------|--------|-----------------|
| 1:00 AM | `ml_kernel_retrain.py` | ML model retraining on latest data |
| 1:30 AM | `emerging_crypto_scanner.py` | CoinGecko trending + gainers |
| 2:00 AM | `asi_scan.py` | Full ASI pipeline scan |
| 2:00 AM | `sentiment_scanner.py` | Finnhub social + news sentiment |
| 2:00 AM | `alpha_intelligence.py` | Multi-source alpha signals |
| 2:15 AM | `insider_mirror.py` | SEC Form 4 insider purchases (14-day window) |
| 2:30 AM | `hedge_fund_intel.py` | 13F filings, institutional positioning |
| 3:00 AM | `fund_pipeline.py` | Mercury fund status check |
| Every 4h | `whale_copier.py` | On-chain whale wallet monitoring |
| Every 4h | `dynamic_universe.py` | Discovery scan (CoinGecko + Reddit + StockTwits) |

## Fear/Greed Index

API: `https://api.alternative.me/fng/?limit=1` (free, no key)
- 0-20: Extreme Fear → strong buy signal (+15 to crypto score)
- 21-35: Fear → moderate buy (+8)
- 36-64: Neutral → no bias (0)
- 65-79: Greed → caution (-8)
- 80-100: Extreme Greed → strong caution (-15)

Current (Mar 2026): 15 (Extreme Fear) — contrarian bullish for long-term.

## Insider trades (Form 4)

`insider_mirror.py` scans SEC EDGAR for Form 4 filings (executive purchases).
Window: 14 days. Large purchases by insiders are strong forward signals.
Output in Signal reports: `[INSIDER] 17 filings` format.

## Hedge fund intel (13F)

`hedge_fund_intel.py` tracks institutional 13F filings quarterly.
Flags: new positions, additions > 10%, complete exits.

## Whale monitoring

`whale_copier.py` monitors on-chain large wallet movements.
Coinbase order book: bid/ask walls > $100K flagged as BUY/SELL pressure.

## Related

- [[intelligence/signal-fusion]] — intel adds 10-15% to fused score
- [[intelligence/discovery]] — separate from intel, runs every 4h
