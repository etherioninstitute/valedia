# Kalshi New York daily-high markets against the official forecast: result (2026-11-13)

Prediction and every setting: `2026-11-13-kronos-and-kalshi-weather-predictions.md`, committed before any
answer was scored (commit 476ffb6). The test code was checked by a blind verifier before it ran (commit
a619863). The test period was run once.

## Verdict: failed

The rule: the idea passes only if, in the test period, the trading rule's mean profit per contract is above
zero with a by-week t statistic of 2 or more.

The test period gave **minus 4.3 cents a contract, t = -5.7**, on 1,852 trades. The market's price was
more accurate than the forecast model at every decision time.

## The numbers

Data: all 9,910 settled markets of the series (1,920 event days, 2021-08-06 to 2026-11-11), each with its
hourly bid and ask history; the forecast bulletins as issued; the observed highs. Each market's outcome is
Kalshi's own result. The weather record disagrees with that result on 4 of the 9,910.

| | Development (to 2025-06-30) | Test (2025-07-01 to 2026-11-11) |
| --- | --- | --- |
| Markets | 6,916 | 2,994 |
| Market-times scored (three decision times) | 9,991 | 7,233 |
| Brier score, forecast model | 0.171 | 0.147 |
| Brier score, market mid price | 0.157 | 0.120 |
| Model minus market (above zero: market better), t by week | +0.014, t 5.2 | +0.027, t 11.9 |
| Trades | 2,881 | 1,852 |
| Mean profit a contract after fees | -1.0 cent | **-4.3 cents** |
| t by week | +0.2 | **-5.7** |
| Share of trades that made money | 44% | 35% |

A lower Brier score is better. Always answering the plain yes rate would score 0.190 on the development
market-times, so both the model and the market carry real information; the market carries more.

## Against what I expected

- Market more accurate than the model in the test period: **yes**, by more than in development.
- Trading rule between minus 3 cents and plus 1 cent: **no, it was worse**, at minus 4.3 cents. I
  under-estimated how much the price knows that the bulletin does not.
- A paper profit before October 2024, when the market was thin: **no**. Minus 0.5 cents a contract
  (t 0.5) on 2,090 trades.

## What the trades show (descriptions made after the result; none decides the rule)

- **The loss is not the fee.** Before fees the test-period trades lost 3.2 cents a contract (t -4.3). The
  mean fee was 1.1 cents.
- **When the bulletin and the price disagree by 5 cents or more, the price is right.** Buys lost 4.7 cents
  a contract and sells lost 3.9.
- **Taking the other side is not an edge either.** The opposite of each test-period trade, paying the
  spread and the fee, earned plus 0.2 cents a contract (t 0.3). In development it lost 5.8 cents, because
  the spread was twice as wide.
- By decision time, test period: 21:00 UTC the day before, minus 5.6 cents (1,180 trades); 03:00 UTC,
  minus 0.7 (418); 09:00 UTC, minus 3.9 (254).
- With the fee rounded up to the cent, as Kalshi charges a one-contract order: minus 4.5 cents a contract,
  t -5.6, on 1,772 trades.
- After the official temperature source changed on 2026-08-14: minus 7.6 cents on 327 trades; before it,
  minus 3.5 on 1,525.

## Checks that the model itself works

Made on the development period before the test period was run. The bulletin's forecast high missed the
observed high by 1.9 to 2.0 degrees Fahrenheit on average, the usual size for this forecast. The model's
probabilities were close to the observed yes rates through the middle of the range (a stated 23% came true
25% of the time; 35% came true 35%) and over-confident at the ends. The model is doing what it was built
to do. The market is simply better.

## What this means

The idea in the notes, that these markets are slow to take in each new forecast, does not hold for this
series against this public forecast, with a two-hour delay after each bulletin. The price already contains
the bulletin and more: other weather models, and the day's own temperatures as they come in.

Not tested here, and not claimed either way:
- the first minutes after a bulletin is published (this test waits two hours, and the exact publication
  time of each bulletin is not on record);
- other cities;
- better forecasts than the public bulletin. Any of these would need its own prediction written first.

The tools stay: the Kalshi history reader, the forecast archive and the evaluation are reusable for other
prediction-market tests (roadmap 1.3 and 1.14).

## Limits, as stated before the test

No order-book depth history exists, so fills are assumed at the quoted bid or ask for one contract. The fee
formula comes from secondary sources. The two-hour delay is an assumption. Three markets from 2022 have
subtitles that contradict their own strike and parse one degree off; they are in the development period.

## To reproduce

```
python -m valedia_trader.research_loop.weather_markets fetch --series KXHIGHNY
python -m valedia_trader.research_loop.weather_markets check
python -m valedia_trader.research_loop.weather_markets evaluate --start 2021-08-06 --end 2025-06-30
python -m valedia_trader.research_loop.weather_markets evaluate --start 2025-07-01 --end 2026-11-11
```
