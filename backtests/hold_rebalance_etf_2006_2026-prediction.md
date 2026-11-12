# Prediction for hold_rebalance_etf_2006_2026 (hypothesis 26)

Idea: Hold the fifteen funds of hypothesis 23 at all times, with no timing rule at all: each fund sized by the same inverse-volatility rule as hypothesis 23 and reset to that size every 21 sessions, at most 20% in one fund and never more than 100% invested. Tested from 2006 to 2026, the period in which all fifteen funds exist.

Written before any backtest of this idea.

- Expected net annual return: +6.50%
- Expected share of winning trades: 50%
- Expected Sharpe ratio: 0.65
- It has failed if the Sharpe ratio is below 0.40
- It has failed if the drawdown exceeds 35%

Notes: What I expect, from the funds' long-run returns: about 56% in shares, 30% in bonds and 14% in gold and commodities once the sizes are scaled to 100%, earning about 6.5% a year on about 10% volatility, a Sharpe of 0.65, with a fall of about 28% in 2008. I expect it to pass the gate in development. Three things the reader should know. First, the share of winning trades does not apply: nothing is ever sold, so no trade closes; the 50% above is a placeholder. Second, I am not blind to this period: I have seen an equal-weight holding of these funds over 2012-2026 (hypothesis 23's comparison) and of five of them over 2006-2021 (hypothesis 25's), and I know the broad history of these markets. Third, the gate will count this as the first try on this market, but the same family was tried before (idea 4, a volatility-sized holding on 2012-2026: holdout Sharpe 0.84, failed on the deflated threshold among 15 seeded variations), and I chose to test it because holding kept beating the rules. So the Deflated Sharpe the gate reports is generous, and I will also report it counted against every try in the ledger.
