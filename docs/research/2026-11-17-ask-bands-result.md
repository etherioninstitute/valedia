# Buying one side of a Kalshi market and holding it to the result, at Webull's cost: result (2026-11-17)

Prediction: `docs/research/2026-11-17-ask-bands-prediction.md` (locked in `1431132` before any program existed).
Program: `valedia_trader/research_loop/ask_bands.py` (built on synthetic data only, four blind checks, merged in
`ce0ffd7`). Run by the lead on 2026-11-17, ended 09:01 UTC, development periods only. Results:
`artifacts/ask_bands/develop-W.json` and `develop-S.json`.

## Verdict: failed, in both universes, at the development screen

Neither sealed period was opened (no marker exists). They stay unspent.

## The numbers

| | New York daily-high markets | Single-game sports markets |
|---|---|---|
| Development period | closes before 2025-01-01 | purchase moment up to 2026-01-21 |
| Events / markets | 1,239 / 5,824 | 825 / 1,650 |
| Trades (one contract a side, 5 to 95 cents) | 8,520 | 3,251 |
| Best of the 153 bands | 25 to 34 cents | 15 to 29 cents |
| Its trades / events | 823 / 621 | 424 / 217 |
| Its mean result a contract, after 2 cents | **minus 5.97 cents** | **plus 0.33 cents** |
| Its p-value (2,000 resamples of whole events) | 1.000 | 0.454 |
| Needed | above zero with p at most 0.025 | above zero with p at most 0.025 |
| Every trade together, after 2 cents | minus 8.33 cents | minus 2.63 cents |

Weather: not one of the eighteen 5-cent cells has a positive mean; they run from minus 4.2 to minus 11.4 cents a
contract. Sports: two cells are positive (15 to 19 cents, 45 to 49 cents) among sixteen negative ones, the pattern
chance gives.

Left out, counted: weather, 28 markets with no candle a day before the close, 3,072 sides priced outside 5 to 95
cents or without a usable two-sided quote; sports, 7,706 markets with no stored candle (the store holds pre-game
prices for 5 January to 2 February 2026 only), 2 without a yes-or-no result, 45 sides outside 5 to 95 cents. No
ticker was refused for its shape and none was repeated.

## Against what I expected

I expected a fail at the screen in both universes, with the best band between minus 3 cents and plus half a cent.
Sports landed inside that (plus 0.33). Weather is worse than I said (minus 5.97): I expected a small bias in
favour of the expensive side and found that a day before the close both sides' asks stand well above what the
contracts turn out to be worth.

## What it shows (for information; nothing here changes the verdict)

- **Sports, before the game:** take the 2 cents away and the mean is minus 0.63 cents, about half a typical
  spread. Kalshi's price there is fair to within the spread, as the sportsbook comparison of 13 November found.
  Buying and waiting has nothing to earn, and Webull's 2 cents make it a steady small loss.
- **Weather, a day ahead:** take the 2 cents away and the mean is still minus 6.33 cents; at 12 hours before the
  close it is worse (minus 9.97 after cost). The posted asks at that hour are wide, on both sides at once. A buyer
  at those asks overpays by several cents whichever side is chosen.
- The cost was not what sank it: at 1 cent, and at Kalshi's own fee, weather has no positive cell and sports'
  mean stays negative (minus 1.63 and minus 2.49 cents).

## What the test could and could not see

Measured by the blind checker on synthetic universes of these sizes before the real run: the whole two-step rule
passed 0 of 400 honest universes; on the weather-sized one it finds an edge of 6 cents after cost 88% of the
time, 4 cents 46%, 2 cents 11%; on the sports-sized one 23%, 4% and 1%. So for sports this result says "no large
edge", not "no edge". For weather the question of power does not arise: the result is far below zero everywhere.

## What it means

Buying a posted price in Webull's prediction markets and holding to the result does not pay, in the two kinds of
market tested. That closes the simplest route to "start with Webull's prediction market". What remains open, each
needing its own locked test: knowing more than the price (a forecaster, or a leader's position as a signal: the
copying test locked the same day); and being the one who posts the price where quotes are wide (the weather result
points at the other side of those asks, which is a hindsight observation here and not evidence: a resting offer
is filled mostly when it is wrong, and each fill through Webull costs 2 cents).

## Limits, as stated before the test

One contract a trade; a candle's closing ask taken as a price that could be had; one city's weather series; four
weeks of one winter for sports; the weather data used once before for another rule.

## To reproduce

`python -m valedia_trader.research_loop.ask_bands develop --universe W --out <file>` and `--universe S`, on main
`ce0ffd7` with the stores as they stood (sizes and times are in each result's `provenance`).
