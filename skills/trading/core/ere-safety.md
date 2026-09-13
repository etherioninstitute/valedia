---
description: >
  Etherion Risk Engine — Burton's NS-paper mapped to financial risk.
  Safety multiplier 0-1 applied to all positions. Halts trading in extreme conditions.
---

# ere-safety

The Etherion Risk Engine maps Burton's Navier-Stokes mathematical framework
to financial risk. Every trade is gated by ERE before execution.

## What it does

Computes a safety multiplier (0.0–1.0) that scales position sizes:
- multiplier = 1.0 → full position allowed
- multiplier = 0.5 → half position
- multiplier = 0.30 → extreme fear mode (small crypto DCA only)
- multiplier = 0.0 → full halt (never reached in practice)

## The physics mappings

| Physics concept | Financial equivalent |
|-----------------|---------------------|
| Littlewood-Paley decomposition | Trend/noise separation (Butterworth filter) |
| Spectral gap (Lemma 3.3) | Noise mean-reversion guarantee |
| Enstrophy | Portfolio variance |
| Palinstrophy | Volatility-of-volatility |
| Vortex stretching | Correlation contagion detection |
| Bootstrap Φ(t) | Composite risk score (drawdown + vol + VaR + skew + kurtosis) |
| BKM criterion | Cumulative extreme loss detector |

## Halt conditions

```
HALT_ALL     → multiplier = 0.30  (allows small crypto in extreme fear)
REDUCE_50%   → multiplier = 0.50
cascade_unstable → multiply by 0.30 additionally
extremes_unsafe  → multiply by 0.50 additionally
```

Composite risk Φ(t) > 2Φ₀ triggers HALT_ALL.

## Status outputs

| Status | Meaning |
|--------|---------|
| `OK` | All systems nominal |
| `WARNING` | Elevated risk, reduced positions |
| `CRITICAL` | High risk detected, significant reduction |
| `STABLE` | Risk within normal bounds |
| `INSUFFICIENT_DATA` | Less than 30 days of data (safe default) |

## Related

- [[core/filter-v3]] — ERE runs after filter, applies multiplier to final position
- [[core/kelly-sizing]] — ERE multiplier feeds into final position size
- [[rules/fund-rules]] — ERE is one layer of the fund's risk management
