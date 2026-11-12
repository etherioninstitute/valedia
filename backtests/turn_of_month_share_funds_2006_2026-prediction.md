# Prediction for turn_of_month_share_funds_2006_2026 (hypothesis 25)

Idea: Hold five broad share-index funds (SPY, QQQ, IWM, EFA, EEM) in equal parts only over the turn of each month, from the close before the last trading day to the close of the third trading day of the new month, and hold cash the rest of the time. Tested from 2006, the first year after the published 1926-2005 study of this effect ended.

Written before any backtest of this idea.

- Expected net annual return: +2.70%
- Expected share of winning trades: 58%
- Expected Sharpe ratio: 0.30
- It has failed if the Sharpe ratio is below 0.20
- It has failed if the drawdown exceeds 20%

Notes: At its published strength (about 0.15% a day) the four days would earn about 7% a year at a Sharpe near 1.0. I do not expect that. Published effects lose half or more of their size once known, the first-day-of-month effect in US shares has looked weak since about 2014, and holding shares on any random fifth of days earned a Sharpe of about 0.2 in this period from the market's rise alone. My estimate is 0.06% a day, about 2.9% a year before costs and 2.7% after (twelve round trips a year), on about 9% volatility: a Sharpe of 0.30. That is below what the gate needs over fifteen years (Deflated Sharpe 0.95), so I expect this to fail the gate, and I expect the random-timing check not to be significant. It is tested because it is the first idea in the ledger that does not read prices, the test costs one backtest, and a pass would be a second, unrelated strategy.
