---
description: >
  Filter v3 — empirically derived high-conviction gate. Two-filter system for
  equity vs crypto, day-of-week gate, booster multipliers. Target hit rate 55-60%.
---

# filter-v3

The gating logic that decides whether a kernel signal becomes a trade.
Derived from 2025 YTD backtests. RenTech baseline = 51% hit rate.

## Two-filter system

### Equity filter (60% hit rate)
```
votes ≥ 8   AND   dom == "MOM"   AND   5d_trend_confirms_direction
```
- All three conditions must be true
- Covers: TSLA, DOGE, ADA, SPY, QQQ, NVDA, META, GOOGL, etc.

### Crypto filter (57% hit rate)
```
dom == "MOM"   AND   trend_dir (5d + 3d both confirm direction)
```
- Two-timeframe trend confirmation catches clean directional moves
- Jan-Mar 2025: crypto was in downtrend, so SELL signals dominated

## Day-of-week gate

Empirically discovered from 2025 data — the most powerful single filter:

| Day | Hit rate | Action |
|-----|----------|--------|
| Monday | 44.4% | **SKIP** — worse than random |
| Tuesday | 30.4% | **SKIP** — far below baseline |
| Wednesday | 86.7% | **FULL SIZE** — week's trend is established |
| Thursday | 66.7% | **FULL SIZE** — momentum continues |
| Friday | 43.8% | Standard — end-of-week positioning noise |

The kernel still runs on Mon/Tue. Signals are logged but not executed
(except fused score ≥ 72 — very high conviction overrides the gate).

## Booster multipliers

When additional conditions stack, position size scales up to 1.45×:

| Booster | Lift | Logic |
|---------|------|-------|
| Wed/Thu | +31%/+16% | Day-of-week gate fires |
| Low vol day | +3% | Today calmer than 30d avg |
| Weekly trend | +1% | 10d price direction agrees |
| RSI aligned | +1% | RSI direction matches kernel |

Max multiplier: `1.0 + 0.15 × min(boost_score, 3)` = **1.45×**

## Discovered assets (tighter rules)

Assets from [[intelligence/discovery]] use stricter limits:
- Max 2% per trade (vs 4% for core assets)
- 50% Kelly (vs full Kelly)
- Sell when score < 50 (vs < 40 for core assets)

## Related

- [[core/kernel-v16e]] — provides the input signals
- [[rules/day-of-week]] — full empirical data behind the day gate
- [[core/kelly-sizing]] — uses pos_multiplier output from this filter
