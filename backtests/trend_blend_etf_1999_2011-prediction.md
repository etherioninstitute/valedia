# Prediction for trend_blend_etf_1999_2011 (hypothesis 23)

Idea: On US exchange-traded funds from 1999 to 2011, hold long each fund whose trend, read at three speeds and scaled by its volatility, is clearly up, sized by inverse volatility, and hold cash otherwise

Written before any backtest of this idea.

- Expected net annual return: +3.50%
- Expected share of winning trades: 40%
- Expected Sharpe ratio: 0.55
- It has failed if the Sharpe ratio is below 0.30
- It has failed if the drawdown exceeds 20%

Notes: Parameters are the conventional ones (speeds 16/64, 32/128, 64/256; entry at half a typical trend; exit at zero) and were fixed before any run.
