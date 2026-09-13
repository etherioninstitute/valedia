---
description: >
  Signal fusion — how kernel v16e, MiroFish swarm, ML retrain, and Fear/Greed
  combine into a single fused conviction score (0-100).
---

# signal-fusion

The weighted blend that turns raw signals into a single actionable score.

## Weights

| Signal | Weight | Notes |
|--------|--------|-------|
| Kernel v16e | 30-35% | Higher for equity (35%), lower for crypto (30%) |
| ML retrain | 18% | 8 GradientBoosting models, weekly retraining |
| MiroFish | 10% | Swarm simulation (unproven — earning weight over time) |
| Intel (Fear/Greed) | 10-15% | Higher for crypto (15%), lower for equity (10%) |
| ERE safety | 15% | Applied as multiplier on deviation from 50, not additive |
| Technical (placeholder) | 17% | RSI/MACD not yet wired into fusion directly |

## Formula

```python
fused = (
    kernel_score      * 0.30 +
    mirofish_score    * 0.10 +
    ml_score          * 0.18 +
    (50 + fg_signal)  * 0.10 +   # Fear/Greed
    50                * 0.32      # placeholder
)
# Then apply ERE multiplier to the deviation from neutral:
deviation = (fused - 50) * ere_multiplier
fused = 50 + deviation
```

## Fear/Greed signal

The Fear/Greed index (0-100) adds a directional bias for crypto:
- ≤ 20 (extreme fear) → +15 signal (contrarian BUY bias)
- ≤ 35 (fear) → +8
- ≥ 65 (greed) → -8 (caution)
- ≥ 80 (extreme greed) → -15 (strong caution)

Current market context: Fear/Greed = 15 (Extreme Fear) as of Mar 2026.

## Action thresholds

| Fused score | Action |
|-------------|--------|
| > 55 + filter passes | BUY |
| ≥ 72 (any day, overrides day gate) | BUY (very high conviction) |
| 40–55 | HOLD existing positions |
| < 40 (core) / < 50 (discovered) | SELL existing position |

## Related

- [[core/kernel-v16e]] — primary input (30-35%)
- [[intelligence/mirofish]] — swarm signal (10%)
- [[intelligence/intel-sources]] — Fear/Greed source
- [[core/ere-safety]] — safety multiplier
