# Does Qwen 8B's ranking hold up? Prediction written before the fresh batch

Written 2026-11-13, before any fresh question was drawn or asked.

## What was seen
On the first 293 blind fund questions ("does this asset close higher in 5 trading days?", name and date
hidden), scored with the new measures:

| Forecaster | Brier | Base rate's Brier | Ranking (95% range) |
| --- | --- | --- | --- |
| Qwen 8B | 0.276 | 0.243 | 0.586 (0.532 to 0.650) |
| Nimble v3 | 0.444 | 0.243 | 0.534 (0.469 to 0.601) |
| Rule: above the 200-day average | 0.248 | 0.243 | 0.530 (0.479 to 0.581) |
| Rule: 20-day momentum up | 0.262 | 0.243 | 0.482 (0.426 to 0.544) |

Qwen 8B's ranking is the only one whose range clears 0.5. It was noticed after looking at four forecasters
and several measures on one batch, so by itself it proves nothing.

## The test
Three fresh blind batches, drawn with seed 20261113, one question a week at most: stocks (103 large US
stocks from 2015), crypto (23 coins from 2017) and funds again (a different draw). Asked of Qwen 8B, Nimble v3
and the two rules.

## What I expect
- Qwen 8B's ranking shrinks toward 0.5: about 0.54 on each batch.
- It counts as replicated only if its 95% range clears 0.5 on at least two of the three fresh batches. I put
  that at about one chance in three.
- Nimble v3 and both rules stay indistinguishable from chance on all three.
- Even if it replicates, a ranking of 0.54 to 0.59 on 5-day direction is a weak signal. Whether it survives
  trading costs is a separate test, through the gate like any strategy.
