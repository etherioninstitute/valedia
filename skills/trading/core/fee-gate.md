---
description: >
  Fee gate — every trade must be profitable after all fees. Hard reject
  if expected P&L ≤ round-trip fees. Fee schedules per platform.
---

# fee-gate

No trade executes unless expected profit exceeds round-trip fees.
This is a hard gate — not a soft suggestion.

## Fee schedules

| Platform | Rate | Notes |
|----------|------|-------|
| Alpaca | 0.0% | Zero commission stocks/ETFs |
| Coinbase | 0.6% taker | + 0.5% spread = 1.1% effective |
| Kraken | 0.16% maker / 0.26% taker | IP-locked to VPS |
| Kalshi | $0.02/contract | Prediction markets |
| Polymarket | 0.0% | Gas ~$0.01 on Polygon |

## Round-trip cost calculation

```
round_trip_fee = position_size × fee_rate × 2   # buy + sell
expected_profit = position_size × expected_return_pct / 100
verdict = "EXECUTE" if expected_profit > round_trip_fee else "REJECT"
```

## Best platforms by trade size

From fee_engine.py analysis (at 2% expected return):
- Polymarket: net 2.00% (zero fees — prediction markets)
- Kraken: net 1.48% (0.52% round-trip)
- Alpaca: net 1.40% (0.60% effective)
- Coinbase: needs >2.20% return to profit

## Related

- [[core/kelly-sizing]] — computes position_size that feeds fee gate
- [[platforms/coinbase]] — why Coinbase needs SOCKS proxy
- [[platforms/alpaca]] — zero-fee execution for equities
