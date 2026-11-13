# Two tests the owner approved: predictions written before either is scored (2026-11-13)

Both ideas came from the owner's saved notes. Each prediction below was committed before any of its
answers was scored. Every setting is fixed here; nothing is tuned on the results.

## 1. Kronos, a price-forecast model, as a forecaster

**The trap.** Kronos was trained on real market history. Its authors say the training data runs to June
2024, and the checkpoints were published on 30 June 2025. Only questions dated 1 July 2025 or later count:
those it has certainly not seen.

**The test.** Blind direction questions ("does this asset close higher over the next N trading days?") on
every question day from 2025-07-01 to the last day whose horizon has passed, seed 20261113, 30 sampled
paths a question, the staged Kronos-small model:
- five-day horizon: every fifth session, every symbol, for funds (17), stocks (103) and crypto (23);
- one-day horizon: every session, at most 8 funds, 20 stocks and 8 coins a day.

That is six groups (three universes, two horizons). Kronos's answer is the share of its sampled paths that
end above the last close. It is scored like every other forecaster: Brier against the asset's own base
rate, ranking with a 95% range from resampling weeks, and the rescaled later-half score.

**The rule.** Kronos counts as having ranking skill only if its 95% range clears 0.5 in at least four of
the six groups.

**What I expect.** A ranking of about 0.51 in each group, with at most one group clearing 0.5. About one
chance in ten that it meets the rule. Its Brier score worse than the base rate as it stands and no better
after rescaling. Reason: the same questions, asked of two language models and two one-line rules, showed
nothing, and five-day direction from price history alone is close to a coin flip.

## 2. Kalshi's New York daily-high markets against the official forecast

**The idea from the notes.** These markets are slow to take in each new weather forecast, so a probability
made from the latest forecast beats the market's price.

**The test.** Series KXHIGHNY, about 9,900 settled markets from August 2021 to November 2026.
- Model: the day's forecast high from the latest National Blend of Models station bulletin for Central
  Park issued at least 2 hours before the decision time; around it, the forecast's own past errors (the
  365 most recent days with the same issue cycle and lead, ending two days before the event day). The
  probability a market resolves yes is the share of those error-shifted highs that satisfy it, lightly
  smoothed. Nothing is fitted to market prices or outcomes.
- Decision times: 21:00 UTC the day before, and 03:00 and 09:00 UTC on the event day. (15:00 UTC is
  reported separately: by then the day's own temperatures inform the market and not the model.)
- Accuracy: Brier score of the model and of the market's mid price on the same market-times, where both
  sides are quoted and the spread is at most 10 cents.
- Trading: at the first decision time where the model's probability beats the ask by more than the fee plus
  5 cents, buy one contract at the ask; where the bid beats the model by the same, sell one at the bid; one
  trade a market, held to settlement. Fee 0.07 x price x (1 - price) a contract.
- Development period: event days to 2025-06-30, looked at first; nothing is tuned on it. Test period:
  2025-07-01 to 2026-11-11, run once.

**The rule.** The idea passes only if, in the test period, the trading rule's mean profit per contract is
above zero with a by-week t statistic of 2 or more.

**What I expect.** In the test period the market's price is more accurate than the model, and the trading
rule earns between minus 3 cents and plus 1 cent a contract after fees: no edge. About one chance in ten
that it passes. Before October 2024 the market was about twenty times thinner; the rule may show a paper
profit there, but at spreads and sizes too small to matter. Reason: since late 2024 these markets trade
hundreds of thousands of contracts a day, the forecast used here is public and free, and whoever sets the
price can see it too.

**Known limits, stated now.** No order-book depth history exists, so fills are assumed at the quoted bid or
ask for one contract. The fee formula comes from secondary sources (the official schedule refused the
request). The two-hour delay between a bulletin's issue time and its availability is an assumption. The
official source of the settled temperature changed on 2026-08-14.
