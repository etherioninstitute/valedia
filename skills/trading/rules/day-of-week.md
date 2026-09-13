---
description: >
  Day-of-week empirical gate. Wednesday 86.7%, Thursday 66.7%. Monday 44.4%,
  Tuesday 30.4%. Hard skip on Mon/Tue. Full position on Wed/Thu.
---

# day-of-week

The single most powerful filter discovered in 2025 backtesting.
Empirical data from Jan-Mar 2025 across 12 assets.

## Why it works

**Monday**: Weekend gap risk. News breaks over the weekend, markets
open chaotic with positioning adjustments. Technical signals have no
edge when the open is dominated by overnight news gaps.

**Tuesday**: Still catching up to the week's narrative. Institutional
flows from Monday positioning are still settling. The week's trend
isn't established yet.

**Wednesday**: The week's dominant trend is established. Economic data
from Mon-Tue has been digested. Institutional positioning is set.
Technical signals have clean directional backdrop to work against.

**Thursday**: Momentum from Wednesday continues. Pre-Friday positioning
hasn't started yet.

**Friday**: End-of-week profit-taking and position squaring introduces
noise. Signals are less clean. Standard treatment.

## Implementation in filter-v3

```python
q["is_mon_tue"]  = dow in [0, 1]   # hard skip
q["is_wed_thu"]  = dow in [2, 3]   # position booster
```

Hard skip: Mon/Tue signals are logged but NOT executed.
Exception: `fused_score ≥ 72` overrides the skip (very high conviction only).

## Booster on Wed/Thu

Position multiplier increases for Wed/Thu signals:
- `is_wed_thu` counts as 1 booster point
- Each booster adds 0.15× to position multiplier
- 3 boosters = 1.45× maximum

This means the same signal on Wednesday gets **45% larger position** than on Friday.

## Related

- [[core/filter-v3]] — implements this gate
- [[rules/backtesting]] — raw empirical data
