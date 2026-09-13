---
description: >
  Hard fund rules — non-negotiable risk limits. Daily cap, per-trade cap,
  profit lock, leverage restrictions, simulation requirement.
---

# fund-rules

These rules never bend. Not for a great signal. Not for a hot trend. Never.

## Position limits

| Rule | Value |
|------|-------|
| Max risk per trade | 4% of account equity |
| Max risk per day | 10% of account equity (total spend) |
| Discovered assets max | 2% per trade |
| Minimum trade size | $0.40 |
| Leverage | NEVER without Burton's explicit approval |

## Profit protection

- **Profit lock**: 70% of profits are locked. Only 30% is re-risked.
- **No withdrawals** without Burton's explicit approval.
- Self-improvement multiplier: scales DOWN aggressively when losing (0.3× at ≤35% win rate).

## Simulation requirement

New strategies must run in simulation for 3 days before live execution.
(Currently waived for strategies derived from backtested kernel signals.)

## Fee gate: mandatory

Every trade must clear [[core/fee-gate]] before execution.
If `expected_profit ≤ round_trip_fees` → reject, no exceptions.

## ERE gate: mandatory

[[core/ere-safety]] multiplier must be > 0.05 for any trade.
Below this threshold the market is too dangerous to trade.

## Minimum conviction: composite > 55

Fused score must be > 55 for BUY (not just the kernel score).
High-conviction override at fused ≥ 72 bypasses the day-of-week gate only —
all other rules still apply.

## Related

- [[core/kelly-sizing]] — implements the per-trade and daily caps
- [[core/fee-gate]] — implements the fee gate rule
- [[core/ere-safety]] — implements the safety multiplier rule
