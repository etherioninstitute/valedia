---
description: >
  Dynamic Universe Discovery — scans CoinGecko, Reddit, StockTwits, Finnhub
  every 4 hours. Prevents missing the next BTC-style explosion.
---

# discovery

The system Burton built specifically to avoid his 10-year-ago BTC mistake:
selling before the explosion. Discovers trending assets *before* they moon.

## Sources (all free, no X API needed)

| Source | What it finds | Refresh |
|--------|--------------|---------|
| CoinGecko trending | Top 15 daily trending coins | Every 4h |
| CoinGecko top gainers | >10% 24h pumps with real mcap | Every 4h |
| CoinGecko vol/mcap spikes | Unusual volume = institutional accumulation | Every 4h |
| Reddit hot posts | Ticker mentions in r/CryptoCurrency, r/wsb, r/investing | Every 4h |
| StockTwits trending | 20 trending tickers | Every 4h |
| Finnhub news | Headlines with buzz keywords (surge, breakout, ATH...) | Every 4h |
| Price breakout detector | Asset at/above 30-day high | Every 4h |

## Buzz Score threshold: 35

Assets must score ≥ 35 to enter the hot list. Score = sum of source weights.
CoinGecko trending #1 = 30 pts. Volume spike = 25 pts. Reddit hot = 15 pts.

## FOMO protection rules

Discovered assets trade with tighter limits than core universe:

| Parameter | Core asset | Discovered asset |
|-----------|-----------|-----------------|
| Max trade size | 4% | **2%** |
| Kelly multiplier | 1.0× | **0.5×** |
| Sell threshold | fused < 40 | **fused < 50** (exit faster) |

The kernel MUST also confirm before any trade: buzz alone ≠ trade.

## Hot list file

`~/.openclaw/workspace/models/hot_list.json`
Updated every 4 hours by cron. Valedia reads this every brain cycle.
Entries expire after 48 hours if buzz score drops.

## Price data: CoinGecko (not Yahoo Finance)

New coins like HYPE, PENGU, TRUMP aren't on Yahoo Finance yet.
CoinGecko has price history for ALL coins via coin ID resolution.
Example: `HYPE → hyperliquid → 365 days of data → kernel validates`.

## Related

- [[platforms/universe]] — how discovered assets integrate into the universe
- [[intelligence/signal-fusion]] — discovered assets still go through full fusion
- [[rules/fund-rules]] — tighter limits apply
