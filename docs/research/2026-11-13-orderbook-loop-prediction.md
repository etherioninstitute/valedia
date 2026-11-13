# The order-book loop with Nimble v3: prediction written before any answer is scored (2026-11-13)

The owner approved a paper-only test of the design in a newsletter he follows ("How to Build a 24/7 AI
Trading System With Jev"), with the local Nimble v3 model in the place of Jev. Every setting below is fixed
now. Nothing is tuned on the results. No order of any kind is sent anywhere.

## The idea being tested

Code computes a numeric snapshot of a live order book. A fast typed model answers a fixed set of narrow
questions about it. Code turns the answers into a decision to quote or stand aside. The claim is that the
model's judgments are calibrated enough that standing aside when it says so beats quoting blindly and
beats plain hand-written rules.

## The test

**Venue.** Kalshi's public market data, read without an account: the order book and the trade tape of
each watched market. (Webull's own prediction-market order book needs the owner's keys; the same loop can
read it later.)

**Markets watched.** Twenty at a time, chosen by rule every six hours from all open markets: whole-cent
prices; best bid at least 10 cents and best ask at most 90; spread from 3 to 15 cents; at least 500
contracts traded in the last 24 hours; closing between 3 hours and 30 days from now; the most traded
first; at most four from one series. A market is watched for 12 hours, or until 30 minutes before it
closes, or until its book has been one-sided for 30 minutes.

**Snapshots.** Once a minute per market: the book's ten best levels a side and the trades since the last
read.

**Decisions.** Every five minutes per market, one call to Nimble v3 (tag `nimble-v3:9b-mxfp8`, checked on
every call) with a numeric state built only from what was known at that moment, and this fixed battery of
six questions (the wording is in the code and its hash is logged with every answer; it is never changed):

| Name | Type | Question | What counts as true afterwards |
| --- | --- | --- | --- |
| `up_15m` | yes/no | The mid price will be higher in 15 minutes | mid 15 minutes later is above the mid now |
| `down_15m` | yes/no | The mid price will be lower in 15 minutes | mid 15 minutes later is below the mid now |
| `move_15m` | yes/no | Within 15 minutes the mid will move by at least half the current spread | the largest distance of any one-minute mid from the mid now, over the next 15 minutes, is at least half the spread now |
| `toxic_15m` | yes/no | The next trade will be followed by the mid moving in that trade's direction | defined only when a trade happens in the next 15 minutes: the mid 15 minutes after the first such trade is beyond the mid just before it, on the taker's side |
| `regime` | one of three | trending, mean-reverting, or quiet over the next 30 minutes | quiet: the mid 30 minutes later is within one cent of the mid now and never left a two-cent band; trending: it moved a cent or more in the direction of the previous 30 minutes' move (two cents or more if that move was zero); mean-reverting: anything else |
| `quote_env` | rating 0 to 3 | how favourable this state is for resting quotes on both sides | not scored on its own; it drives the quoting rule below |

**Paper quoting.** At each decision a rule either stands aside or rests one contract to buy one cent above
the best bid and one contract to sell one cent below the best ask, for the next five minutes. A resting
buy counts as filled if, in those five minutes, a trade prints in which the taker sold at or below its
price; a resting sell, if a taker bought at or above its price. Each fill is valued against the mid 15
minutes after the fill, less the maker fee of that market's series where it has one. A slot with no quote
or no fill earns zero. Inventory is not carried: each fill is judged on its own. Three rules see the same
slots:
- **Always:** quote in every slot.
- **Plain rule:** quote only when the spread is at least 4 cents and the book's top-of-book imbalance is
  between 0.3 and 0.7 and fewer than three trades printed in the last five minutes.
- **Model:** quote only when `quote_env` is 2.0 or more and `move_15m` is below 0.5.

**Periods.** Development: the first 5 full UTC days of collection, used to find faults and to fit the two
yardsticks below; nothing else is tuned on it. Test: the next 14 full UTC days, scored once. A day counts
only if at least 80% of its decision slots were collected; the test needs at least 10 counted days and is
extended, to at most 21 calendar days, until it has them.

**Yardsticks**, fitted on the development period only: for each yes/no question, (a) the plain rate at
which it came true, and (b) that rate split by one obvious number computed in code (for `up_15m` and
`down_15m`, the sign of the last 15 minutes' mid change; for `move_15m`, whether the mid moved by half the
spread in the last 15 minutes; for `toxic_15m`, whether the book's imbalance leans to the taker's side).

## The rule

The idea passes only if both hold in the test period:

1. **Judgment.** For at least two of the four yes/no questions, Nimble v3's Brier score is better than
   both yardsticks, each with a by-day t statistic of 2 or more, and its ranking's 95% range (resampling
   whole days) is above 0.5.
2. **Policy.** The model rule's mean profit per slot is above zero, and above both the always rule and the
   plain rule, each difference with a by-day t statistic of 2 or more.

## What I expect

- No skill on `up_15m`, `down_15m` or `toxic_15m`: ranking about 0.50 to 0.52, Brier no better than the
  plain rate. In two probes the model's direction answer did not change with the state.
- Some skill on `move_15m` against the plain rate, because quiet and busy spells persist and the state
  carries recent movement; none against the code yardstick, which reads the same thing.
- Quoting in every slot loses money per fill after the mid moves: fills come mostly when the price is
  about to move through the quote. The model rule stands aside more than the plain rule without doing
  better per slot.
- About one chance in fifteen that the idea passes both parts.

## Known limits, stated now

Fills are simulated from the public trade tape; a real resting order would change what other traders do
and would wait in a queue when it does not improve the price. One contract at a time says nothing about
size. The maker fee comes from each series' public fee type; the fee schedule itself could not be read
from the primary source (a secondary source was used), so results are also reported with no maker fee and
with the maker fee applied everywhere. Markets with spreads of 3 cents or more are the less traded ones.
The collector runs on one Mac; gaps are recorded and days below 80% coverage do not count. Kalshi may
limit unauthenticated reads without notice. A pass here would be a reason to test with real resting
orders in a paper account, not a reason to trade.

## Notes written before collection began (2026-11-13)

Nothing has been collected into the research record and no answer has been compared with an outcome.

1. **The battery in force.** The questions' wording was tightened while the collector was built, before any
   collection. The battery whose hash is logged with every answer is
   `3e07adb11ae25c23b334bb9f9576208854686cdb1177d0728026d2d9bd927730`; it does not change from here on.
2. **The collector in force** is commit 09ecec7. It was checked blind eight times. The last check confirmed
   the warm-up rule (no decision before a market has 30 unbroken minutes of snapshots), 2.3 million simulated
   trades with none missing across outages of 6 minutes to 25 hours, and no look-ahead; it left two known
   gaps: when the trade reads of one market keep failing for more than 24 hours, or across a drop and a
   re-choice of that market, some trades are neither stored nor marked as lost.
3. **A rule fixed now because of those gaps.** Anything that needs the trade tape (a paper fill in the five
   minutes after a decision; `toxic_15m`) counts only where the stored trade reads cover the whole stretch
   it needs, judged from the spans of the successful reads stored with the snapshots, never from the event
   rows. A slot without that cover is left out of the quoting comparison for all three rules alike, and the
   number left out is reported. Outcomes that need only mid prices are not affected.
4. **Replacing the collector.** A later revision that has passed a blind check may replace this one during the
   development period, at a restart recorded in the store. The test period runs on one revision.
5. A shakedown run of the previous revision against the live feed (about an hour, into a scratch file that is
   not part of the record: 20 markets, every decision slot collected, no failed request) was made first.
   Nothing from it is used.
6. **Collector replaced on 2026-11-13 at 16:32 UTC, before the first development day.** Commit 9332c6c took
   over the same record at a restart (a stop and a start in the same second, both in the store). It closes the
   two gaps of note 2: a blind check found no trade neither stored nor marked as lost in 200 generated
   sequences, and decisions identical to the previous revision's wherever there was no gap (4,658 compared).
   The rule of note 3 stays in force all the same.
