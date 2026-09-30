# TC auto-trading paper smoke

**When:** 2026-09-30T06:02:50.676345+00:00
**Bankroll:** $504.00 paper (Robinhood freely tradeable cash, sizing reference only).
**Path:** http://127.0.0.1:51898 `POST /valedia-tv-alert` on 127.0.0.1.

Flat mids (BTC 83360.03, AAPL 329.4) so P&L is fee and slippage drag, not a price story.
Size is 10% of paper equity, the SuperTrend pine `default_qty_value`. Not Kelly.

| | |
|---|---|
| Cash left | 503.8387246 |
| Fees paid | 0.10079895 |
| Realized P&L | -0.1612754 |
| P&L vs $504 | -0.1612754 |
| Fills | 4 |
| Open positions | {} |
| create_order | false |
| live | false |

## Fills

| ticker | side | qty | mid | px | fee | book | realized |
|---|---|---:|---:|---:|---:|---|---:|
| COINBASE:BTCUSD | buy | 0.0006046 | 83360.03 | 83401.710015 | 0.05042467 | CE-3 | None |
| COINBASE:BTCUSD | sell | 0.0006046 | 83360.03 | 83318.349985 | 0.05037427 | CE-3 | -0.15119842 |
| AAPL | buy | 0.15295956 | 329.4 | 329.43294 | 0.0 | alpaca_static | None |
| AAPL | sell | 0.15295956 | 329.4 | 329.36706 | 0.0 | alpaca_static | -0.01007698 |

Crypto book is CE-3 (`CRYPTO_FEE_PCT` 0.001 + `CRYPTO_SLIPPAGE_PCT` 0.0005).
Equity book is `fee_engine.STATIC_FEES['alpaca'].taker` (0) + 1 bp slippage.
No venue order. Oil stayed NO_HARVEST. No public tunnel was started.

