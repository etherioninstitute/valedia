# Macro Watcher memo, 2026-11-10

Market: us-etf+crypto. Data as of 2023-02-17 (development period only).

Ledger check: 15 ideas were already on record for this market: #15 failed; #14 failed; #13 failed; #12 failed; #11 failed; #10 failed; #9 failed; #8 failed; #7 failed; #6 failed; #5 failed; #4 failed.

## Idea 1: Go long energy (XLE) and commodities (DBC) on a 12-month momentum signal that skips the latest month, using inverse-volatility sizing, as the energy-vs-market and commodity trend persists.

- Why it might work: XLE/SPY is up 38.6% over 12 months and still sits near its 200-day average, which suggests a commodity-led trend that is intact but not yet extended; real-asset momentum tends to persist for months after inflation shocks, and skipping the last month avoids short-term reversal noise.
- Conditions it needs: XLE/SPY 12m change stays strongly positive (>0.2) while its 3m change is negative but ratio stays within about 2% of its 200d average (a pullback inside an uptrend); DBC/GLD below its 200d average does not matter because the trade is on absolute DBC/XLE price momentum, which must remain positive over 252 days.
- What would prove it wrong: Out-of-sample Sharpe below 0.2 or max drawdown above 20%, or the energy sleeve losing money in the 3m periods after the ratio turns down, would show the pullback is a regime change rather than a pause.
- Recorded as hypothesis #19, strategy `strategies/mw_long_energy_commodities_h19.json`
- Prediction (now locked): +6.0% a year, 50% winning trades, Sharpe 0.45; failed if Sharpe is below 0.20 or drawdown exceeds 20%

## Idea 2: Buy long-duration bonds (TLT) and credit (LQD) when they have fallen sharply over a short window and the stock-bond correlation is only mildly positive, betting on mean reversion in rates.

- Why it might work: TLT/IEF is down 13% over 12 months but up 1.9% over 3 months and is 2% under its 200-day average, so long bonds are depressed but stabilising; sharp short-run sell-offs in a high-quality bond are often partly reversed, and credit spreads (LQD/IEF above its 200d average) show no stress that would make a bond sell-off a credit event.
- Conditions it needs: LQD/IEF stays above its 200d average (credit calm), SPY-TLT 60d correlation remains positive but below about 0.3 (bonds not yet moving in lockstep with stocks), and TLT or LQD has had a 10-to-20 day decline of more than one standard deviation.
- What would prove it wrong: Sharpe below 0.2 or drawdown above 15% in a backtest, especially losses clustered when the SPY-TLT correlation rises above 0.4, would show rate shocks trend instead of reverting in this regime.
- Recorded as hypothesis #20, strategy `strategies/mw_long_duration_bonds_h20.json`
- Prediction (now locked): +3.0% a year, 55% winning trades, Sharpe 0.40; failed if Sharpe is below 0.20 or drawdown exceeds 15%

## Idea 3: Hold tech and cyclical risk assets (XLK, QQQ, XLF) when a fast moving average is above a slow one, using the recovery in tech-vs-utilities as the risk-on confirmation.

- Why it might work: XLK/XLU is up 5.8% over 3 months and 7.7% above its 200-day average even though it is down 16% over 12 months, which looks like a rebound from a bear-market low; a moving-average cross is a slow, low-turnover way to ride a risk-on recovery without trading noise, and it differs from the six-month return trend rule already tested.
- Conditions it needs: XLK/XLU above its 200d average and rising over 3 months, credit (LQD/IEF) above its 200d average, and a 50-day average of XLK, QQQ or XLF above its 200-day average.
- What would prove it wrong: A Sharpe below 0.3 or drawdown above 25% in a backtest, or frequent whipsaw losses (win rate under 35%), would show the rebound is a bear-market rally not a trend.
- Recorded as hypothesis #21, strategy `strategies/mw_hold_tech_cyclical_h21.json`
- Prediction (now locked): +7.0% a year, 42% winning trades, Sharpe 0.50; failed if Sharpe is below 0.30 or drawdown exceeds 25%
