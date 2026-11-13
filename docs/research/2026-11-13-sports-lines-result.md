# Result: sportsbook lines against Kalshi's price for the same game

2026-11-13. Roadmap 1.18. The prediction is `2026-11-13-sports-lines-prediction.md` (locked before any data
was bought, with eight clarifications, each written before any price was compared with a result).

## Verdict

**Failed, and stopped after the development period.** The rule almost never finds a trade, so it cannot reach
the 150 trades the prediction requires, and the books' probability is no better than Kalshi's price. The
test-period snapshots were not bought: the test could only have confirmed a failure, at a cost of about
5,680 credits.

This is what the prediction expected ("a fail, most likely on the trading part ... chance of a pass about 1
in 8"). The gap turned out even smaller than expected.

## What was measured

Development period, 5 January to 2 February 2026 (the part of the planned period the credit rule could buy
while keeping the test period reserved). 326 snapshots of ten books' moneylines, 3,260 credits. Kalshi's
minute prices came free from its public history.

- Kalshi game markets in the period: 3,424. Paired with an odds game by the strict rule: 1,600.
- Counted (in one of the day's three largest waves of starts, at least 3 books, both Kalshi contracts quoted
  within 5 cents): **1,407 games** over 5 weeks. NBA 212, hockey 210, men's college basketball 975, NFL 9,
  college football 1.

## Judgment: is the books' probability better than Kalshi's price?

Brier score on the home team winning, 30 to 120 minutes before the start (lower is better).

| Books' probability | Games | Books | Kalshi's mid | Difference (books minus Kalshi) | 95% interval, weeks resampled |
| --- | --- | --- | --- | --- | --- |
| Median of the books | 1,407 | 0.20736 | 0.20742 | -0.00007 | -0.00051 to +0.00038 |
| Pinnacle alone | 1,359 | 0.21269 | 0.21262 | +0.00007 | -0.00022 to +0.00047 |

No difference. The interval is about a twentieth of the 0.002 the prediction allowed for. By league the
differences are all inside their intervals: NBA +0.0003, hockey +0.0005, college basketball -0.0003.

## Trading: buy the cheap side when the gap beats the fee by a margin

| Books' probability | Margin 2 cents | Margin 3 cents | Margin 5 cents |
| --- | --- | --- | --- |
| Median of the books | 2 trades, both lost | 0 trades | 0 trades |
| Pinnacle alone | 4 trades, 1 won | 2 trades, 1 won | 1 trade, won |

Out of 1,407 games. The one large winner under "Pinnacle alone" was a single book 14 points away from
Kalshi and from the other books at that moment: one stale price, not a pattern. Nothing here can be tuned
into a rule: there is no choice of margin or version that gives enough trades to judge.

## Why it was stopped

The prediction's pass line needs at least 150 trades in the test period. The development period gives 0 to 4
trades in 1,407 games at every setting; the test period holds a similar number of games. No setting could
pass, so no choice was stored and no test-period snapshot was bought. Stopping a test that cannot pass does
not weaken the discipline: it can only turn a possible pass into a fail, never the reverse.

## What this says

Thirty minutes to two hours before a game, Kalshi's price for the winner **is** the sportsbooks' price with
the margin removed, to within a few hundredths of a cent in accuracy. Whoever sets Kalshi's prices watches
the same books. There is no edge in the moneyline from comparing the two.

## What is kept, and what it is good for

- The verified code: the odds reader with its credit ledger, Kalshi's game history, strict pairing, the
  evaluation (`research_loop/sports_lines.py`, `venues/kalshi_history.py`, `venues/odds_api.py`).
- A useful fact for grading public picks (roadmap 1.19): for a game's winner, **Kalshi's own price is as good
  a fair probability as the books' consensus, and it is free**. Picks on winners can be graded against it
  without spending odds credits. The books are still needed for what Kalshi does not list or lists thinly
  (spreads, totals, player props).
- 16,740 credits remain this month.

## Limits

- One month, five leagues, moneylines only, one moment per game. It says nothing about spreads, totals,
  props, live prices during a game, or the hours right after news breaks.
- 13% of paired games fell outside the three largest waves and were not priced.
- Kalshi's fee is taken from the series' fee type and a secondary source.
- The blind checks of the code were made by the same model family as the builders.
