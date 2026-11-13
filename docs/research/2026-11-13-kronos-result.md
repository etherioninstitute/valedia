# Kronos as a forecaster: result (2026-11-13)

Prediction and every setting: `2026-11-13-kronos-and-kalshi-weather-predictions.md`, committed before any
answer was scored (commit 476ffb6). The run used commit b427529, whose no-look-ahead, cut-off and
separate-paths properties passed a blind verification. It was scored once, after the last answer.

## Verdict: failed

The rule: Kronos counts as having ranking skill only if its 95% range clears 0.5 in at least four of the
six groups.

**It cleared 0.5 in none of the six.** 24,126 questions dated 2025-07-01 or later, every one answered,
no errors.

## The numbers

Ranking is the chance that a case which came true got a higher forecast than one that did not; 0.5 is a
coin. The range is 95%, from resampling whole weeks.

| Group | Questions | Came true | Kronos mean forecast | Kronos ranking | Range | Brier, Kronos | Brier, base rate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Funds, 5 days | 1,250 | 55% | 25% | 0.511 | 0.466 to 0.556 | 0.419 | 0.247 |
| Stocks, 5 days | 7,166 | 53% | 34% | 0.508 | 0.486 to 0.531 | 0.390 | 0.251 |
| Crypto, 5 days | 2,231 | 46% | 46% | 0.521 | 0.454 to 0.577 | 0.335 | 0.249 |
| Funds, 1 day | 2,760 | 51% | 31% | 0.516 | 0.487 to 0.542 | 0.357 | 0.250 |
| Stocks, 1 day | 6,900 | 51% | 38% | 0.494 | 0.479 to 0.507 | 0.349 | 0.251 |
| Crypto, 1 day | 3,819 | 47% | 47% | 0.496 | 0.472 to 0.524 | 0.337 | 0.250 |

- **No ranking skill.** The six rankings run from 0.494 to 0.521 and every range contains 0.5.
- **Far worse than the base rate as it stands** (a lower Brier score is better), in every group, by-week
  t from 5 to 22. On funds and stocks the model says "down" far too often: it gave funds a 25% chance of
  rising over five days when 55% rose. Its paths drift toward a fall after a rise, which fits what a
  made-up price series showed before the test (steady rise: 23% chance of going higher).
- **No better after rescaling.** With its answers rescaled on the earlier half of each group and scored on
  the later half, Kronos is indistinguishable from the base rate (skill between -0.02 and +0.00), and
  worse on five-day funds (t 2.4).
- The two one-line rules asked the same questions (twelve-month momentum; price above its 200-day
  average) also rank at chance in all six groups.

## Against what I expected

- A ranking of about 0.51 in each group: **yes** (0.494 to 0.521).
- At most one group clearing 0.5: **yes**, none did.
- Brier worse than the base rate as it stands and no better after rescaling: **yes**.

## What this means

On daily bars, the staged Kronos-small model does not tell which of two assets is more likely to rise over
the next one or five trading days, in funds, stocks or crypto, on dates it cannot have trained on. With
the earlier tournament results (two language models and two price rules, 1,793 questions), that is now
five forecasters and about 26,000 questions saying the same thing: **direction over a few days cannot be
read from price history alone**, at least not by these methods. Information beyond prices is still
untested and is where the roadmap goes next (items 1.3 and 1.4).

Not tested, and not claimed either way: the larger Kronos models, minute bars, horizons beyond five days,
forecasting volatility instead of direction, or a model fine-tuned on these assets. Each would need its
own prediction first. Volatility is the most plausible of these: it is the one thing price history is
known to forecast.

## Limits

One model size, one seed, 30 paths a question, so each answer moves in steps of about 3 points. The stock
list is today's large companies, which flatters "came true" slightly and does not change a ranking. The
price downloader does not check volume (a fault found in verification, repair queued); no symbol was
affected in this run: every question was answered.

## To reproduce

```
python -m valedia_trader.research_loop.price_models refresh-ohlc --universe stocks   # and crypto, funds
python -m valedia_trader.research_loop.price_models ask-kronos --universe funds --horizon 5 \
    --not-before 2025-07-01 --seed 20261113 --paths 30 --with-rules
# stocks and crypto the same; then --horizon 1 with --per-day 8 (funds), 20 (stocks), 8 (crypto)
```

The six groups are scored by universe and horizon on questions dated 2025-07-01 or later, with the
tournament's own ranking, week resampling and rescaling.
