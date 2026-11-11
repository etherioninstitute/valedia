# Price Analyst memo, 2026-11-10

Market: us-etf+crypto. Data as of 2023-02-17 (development period only).

Ledger check: 15 ideas were already on record for this market: #15 failed; #14 failed; #13 failed; #12 failed; #11 failed; #10 failed; #9 failed; #8 failed; #7 failed; #6 failed; #5 failed; #4 failed.

## Idea 1: Go long crypto (BTC-USD, ETH-USD) only when a short-term 50-day vs 200-day moving-average cross turns positive, after a deep drawdown, and hold until the cross reverses.

- Why it might work: Both coins sit 48-52% below their 1-year highs but are above the 200-day average (+14%, +13%) with 3-month returns of +39-47%. That is a possible regime turn out of a bear market. Crypto trends tend to persist once the average cross flips, and this rule is slower than the failed 6-month momentum sleeve. Volume is below its 1-year average, so the move is not yet crowded.
- Conditions it needs: Price above the 200-day average, 3-month return strongly positive, 12-month return deeply negative, and 20-day volatility below 1-year volatility (about 0.48 vs 0.63 for BTC). The edge needs the 50-day average to stay above the 200-day average after a fresh cross.
- What would prove it wrong: Sharpe below 0.2 or drawdown above 35% in a backtest net of costs, or the cross whipsawing more than about 4 times a year with a win rate under 40%.
- Recorded as hypothesis #16, strategy `strategies/pa_long_crypto_only_h16.json`
- Prediction (now locked): +15.0% a year, 42% winning trades, Sharpe 0.60; failed if Sharpe is below 0.20 or drawdown exceeds 35%

## Idea 2: Buy sector and equity ETFs after a 20-day breakout to a new high inside a rising market, and exit when price falls back to the middle of the range.

- Why it might work: Equities are in a broad recovery: QQQ +9.5% and XLK +9.3% over 1 month, with SPY, IWM and XLF also up. Breakouts to new highs on rising prices often attract more buyers while volatility is moderate. A breakout rule is a different signal from the failed momentum, trend and reversal sleeves. Exiting at the middle of the range limits give-back.
- Conditions it needs: Most equity ETFs above the 200-day average (SPY +4%, IWM +7.5%, XLF +8.4%), 20-day volatility below 1-year volatility, and no sharp one-way shocks (5-day moves within about 1 standard deviation). The edge fails if the market whipsaws sideways inside its range.
- What would prove it wrong: Sharpe below 0.25, or a win rate under 45% with an average loss larger than the average win, over a backtest net of costs. Also wrong if returns come only from the 2020-2021 rebound.
- Recorded as hypothesis #17, strategy `strategies/pa_sector_equity_etfs_h17.json`
- Prediction (now locked): +5.0% a year, 50% winning trades, Sharpe 0.45; failed if Sharpe is below 0.25 or drawdown exceeds 15%

## Idea 3: Buy long-bond and credit ETFs (TLT, IEF, LQD) after a 10-day fall that is large relative to their 1-year volatility, and exit once the z-score returns toward zero.

- Why it might work: Bond ETFs are down 3-5% over 1 month and TLT and IEF are 13-26% below their 1-year highs, yet they trade near their 200-day averages and show only mild 5-day moves. Bonds are mean-reverting more than equities and crypto, and a long-only reversion trade with a slow volatility window is a different signal from the failed 5-day fade. Volume is near normal for IEF and LQD, so there is no capitulation to wait for.
- Conditions it needs: Bonds fell more than 1.5 standard deviations over 10 days, while price stays within about 5% of the 200-day average and 20-day volatility is below 1-year volatility. It needs rate volatility to be calming, not rising.
- What would prove it wrong: Sharpe below 0.2 or a drawdown above 12% net of costs, or a majority of trades being losers that keep falling after entry (a trending rate market).
- Recorded as hypothesis #18, strategy `strategies/pa_long_bond_credit_h18.json`
- Prediction (now locked): +3.0% a year, 55% winning trades, Sharpe 0.40; failed if Sharpe is below 0.20 or drawdown exceeds 12%
