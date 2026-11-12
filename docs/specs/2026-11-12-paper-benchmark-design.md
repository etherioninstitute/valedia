# A "hold the funds" benchmark in paper trading

Date: 2026-11-12. Asked for by the owner ("Yes, add it") after two tests showed the trend rule's timing adding
nothing over simply holding its funds.

## What it is for

Every strategy in paper trading is shown, each day, next to what holding its own funds would have made over the
same days. An idea that cannot beat that is not earning its place.

## Definition

The benchmark is the same comparator the research gate already uses (`backtest.engine.buy_and_hold`): equal weight
in every fund of the strategy's `market` that has a price, rebalanced daily, no costs.

- It starts with the capital the strategy had on its first paper day.
- It starts earning on the day the strategy's first orders fill (the trading day after its first decision), so both
  are measured from the same close.
- It is a yardstick, not a book: it places no orders, passes through no risk check, holds no cash and pays no costs.
  The account's position limits do not apply to it. Paying no costs flatters it by a few basis points once.

## Alternatives considered

| Option | Why not |
|---|---|
| Buy once and never rebalance | Differs from the gate's comparator, so paper and backtest would be measured against two different things |
| Inverse-volatility weights | A second design choice to argue about; equal weight is the plain reading of "just hold the funds" |
| A real second paper book with orders and limits | It would compete for the account's position limits and stop being a pure yardstick |

## Where it lives

- `research_loop/paper.py`: `PaperTrader.step` records `benchmark_values` (profit and loss in dollars, the same unit
  as `strategy_values`) in the day's summary and journal. State keeps only each strategy's start day and starting
  capital; the value is recomputed from prices, so it cannot drift from the definition.
- A paper book that started before this change gets its benchmark from its own history (first paper day and that
  day's capital), not from the day the code changed.
- `research_loop/critic.py`: the weekly review says how far each strategy is ahead of or behind its benchmark, with
  the number of days, and says plainly when that is too few days to mean anything.

## Tests

Written first: the value equals the gate's function on the same prices; nothing is earned before the first fill;
the book's cash, positions and orders are unchanged by it; an older saved state is picked up from its history; the
starting capital does not change when another strategy joins; the weekly review reports both numbers and the day count.

## Not included

Costs on the benchmark, a benchmark for the account as a whole, charts.
