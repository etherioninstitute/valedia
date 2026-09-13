---
description: >
  Kernel Predictor v16e — Valedia's core prediction engine. 11 mathematical
  frameworks, adaptive SVD, regime detection. The settled version (+40.7% crypto alpha OOS).
---

# kernel-v16e

The mathematical heart of every trade signal. Built on Burton's UGFT framework
mapping Navier-Stokes physics to financial markets.

## What it outputs

```python
{
  "score":     62.4,      # conviction 0-100 (50 = neutral)
  "direction": "BUY",     # BUY | SELL | HOLD
  "votes":     9,         # frameworks in agreement (0-11)
  "dom":       "MOM",     # dominant regime: MOM | REV | VOL
  "conf":      0.42,      # internal confidence
  "snr":       1.8,       # signal-to-noise ratio (SVD)
  "rsi":       38.2,      # 14-period RSI
  "D":         2.1,       # D-functional (spectral energy distribution)
}
```

## The 11 frameworks

1. **Adaptive SVD** — Wedderburn decomposition, separates trend from noise
2. **Lyapunov channels** — stability detection, identifies trending vs chaotic regimes
3. **Rosetta stationarity** — mean-reversion guarantee via spectral gap
4. **RH spectral** — Riemann-Hilbert spectral analysis
5. **BSD entropy** — Birch-Swinnerton-Dyer entropy measure
6. **YM stability** — Yang-Mills stability indicator
7. **Hodge coherence** — directional coherence across timeframes
8. **NS trend/noise** — Navier-Stokes trend/noise decomposition
9. **Momentum multi-timeframe** — 5d/20d/50d composite momentum
10. **Volume regime** — volume vs 20d average, directional confirmation
11. **Clustering horizon** — adaptive lookback period via autocorrelation

## Regime detection (dom field)

| Regime | Meaning | Filter behavior |
|--------|---------|-----------------|
| `MOM`  | Momentum trending — directional move with structure | Filter OPENS: best for trading |
| `REV`  | Mean-reverting — price oscillating around mean | Filter CLOSES: skip these signals |
| `VOL`  | Volatility-driven — news/event dominated | Filter CLOSES: unpredictable |

## Votes = internal consensus

Higher votes = more of the 11 frameworks agree.
- votes ≥ 8: maximum consensus → equity filter activates
- votes 7-8: strong → see [[rules/day-of-week]] for Wed/Thu booster
- votes < 6: low consensus → HOLD, no trade

## Version history

- v16e: settled version. Crypto alpha +40.7%, crypto wins 6/8. Equity alpha -37.8%.
- v16f: fewer trades, lower alpha. Discarded.
- See [[rules/backtesting]] for full OOS comparison.

## Related

- [[core/filter-v3]] — uses kernel output as input
- [[core/ere-safety]] — runs in parallel, applies multiplier to kernel score
- [[intelligence/signal-fusion]] — kernel is 30-35% of fused score
