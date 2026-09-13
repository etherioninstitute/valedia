---
description: >
  Kelly position sizing — half-Kelly with confidence multiplier and booster
  scaling. Self-improves based on Valedia's live track record.
---

# kelly-sizing

Position sizing using the Kelly Criterion, halved for safety, then scaled
by a real-time confidence multiplier derived from Valedia's trade history.

## Formula

```
edge        = (fused_score - 50) / 50        # 0 to 0.9
kelly_raw   = edge × 0.5 × account_equity    # half-Kelly
position    = kelly_raw × pos_multiplier × conf_mult
position    = min(position, equity × max_pct)  # hard cap
```

## Hard caps

| Parameter | Value |
|-----------|-------|
| Max per trade | 4% of account equity |
| Max daily | 10% of account equity |
| Discovered assets | 2% max per trade |
| Minimum to execute | $0.40 |

## Confidence multiplier (self-improvement)

Valedia tracks every trade in `brain_log.json` and adjusts size dynamically:

| Recent win rate | Multiplier | Meaning |
|-----------------|------------|---------|
| ≥ 65% | 1.5× | Proven edge → press it |
| ≥ 60% | 1.3× | Strong edge |
| ≥ 55% | 1.1× | Above baseline |
| 50% | 1.0× | Neutral |
| ≤ 45% | 0.7× | Below baseline → cautious |
| ≤ 40% | 0.5× | Losing → cut size |
| ≤ 35% | 0.3× | Very poor → minimal |

Uses blend of recent 20 trades (70%) + all-time (30%):
`blended = recent_20_hit_rate × 0.7 + all_time × 0.3`

## Booster multiplier (from filter)

[[core/filter-v3]] passes a `pos_multiplier` (1.0–1.45) based on:
- Day of week (Wed/Thu = higher)
- Low vol day (+)
- Weekly trend alignment (+)
- RSI alignment (+)

Final position = `kelly_raw × pos_multiplier × conf_mult`

## Related

- [[core/filter-v3]] — provides pos_multiplier
- [[core/fee-gate]] — verifies position is profitable after fees
- [[rules/fund-rules]] — sets the hard caps
