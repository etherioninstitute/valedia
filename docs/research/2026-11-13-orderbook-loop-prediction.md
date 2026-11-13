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

## Clarifications for the scoring (2026-11-13, written before the first development day began; no answer has been compared with an outcome)

Raised by the builder of the evaluation code. Where the text above could be read two ways, the stricter reading
is taken. None was chosen with data in sight.

1. **What is scored.** Only an ordinary decision with answers under the battery in force, the first for its
   market and slot. A late or forced row is not scored.
2. **A day counts** on the collector's own count of slots (at least 80% collected, compared in whole numbers),
   less any answered slot under another battery, and only once the day has ended. The yardsticks are fitted
   on counted development days only.
3. **Periods.** The first full day is the first UTC day that starts at or after the first start of
   collection. A period is complete one hour after its last day ends.
4. **The end of the test.** The first day from the 14th on at which 10 days have counted, at the latest the
   21st. Without 10 counted days the idea cannot pass.
5. **"Now" and "later".** Now is the mid and spread of the state the model was shown. A later mid is the
   nearest two-sided snapshot that began after the decision, within 30 seconds of the target time; a
   one-sided book there leaves the outcome unknown and the slot out of that question.
6. **Paths** (`move_15m`, `regime`) need a two-sided book in every minute of the window. "At least half the
   spread" includes exactly half.
7. **`toxic_15m`.** The window is the 15 minutes after the decision. Two first trades of opposite sides at the
   same moment leave it unknown. The mid just before the trade is the latest two-sided snapshot ended before
   it, at most 90 seconds old. A mid that does not move counts as false. The trade tape must be covered for
   the whole 15 minutes.
8. **`regime`** quiet: the end within one cent and the whole path within two cents either way. It is reported
   as accuracy and is not part of the pass line.
9. **Quotes** come from the state the model was shown. With a spread under 3 cents or a one-sided book no rule
   can quote: the slot earns zero for all three and stays in the averages.
10. **Fills.** A resting buy fills on a trade whose taker sold at or below its price, a resting sell on one
    whose taker bought at or above; each side at most once, at our own price; valued from the first such
    trade's time. A fill that cannot be valued leaves the slot out for all three rules, and is counted.
11. **The plain rule** takes an imbalance from 0.3 to 0.7 inclusive; a missing input means no quote. **The
    model rule** reads `quote_env` as its expected score and `move_15m` as its probability.
12. **The maker fee**, where a series has one: 0.0175 x price x (1 - price) a contract times the series'
    multiplier, rounded up to a whole cent, because the paper rule rests one contract and a one-contract order
    pays a whole cent. This is the strictest reading and the one that most lowers the quoting result; the
    result with no maker fee and with the fee everywhere is reported beside it, as said above. A series whose
    fee type is unknown is charged.
13. **Yardstick splits** use the state the model was shown (the sign of its 15-minute mid change; whether the
    mid moved half the spread in the 15 minutes before; the top-of-book imbalance against the first trade's
    side). A group never seen in development takes the plain rate.
14. **Judgment.** For each question a by-day t statistic on each day's mean difference (fewer than two days or
    no spread between days: no statistic, which fails); the pooled Brier score must also be lower. Ranking
    counts ties as half. Ranges come from 2,000 resamples of whole days with a fixed seed.
15. **Policy.** The mean profit per slot over all slots; the model rule must be above zero, above the always
    rule and above the plain rule, each difference with a by-day t statistic of 2 or more, judged on the
    result with each series' own fee.
16. **Trade-tape cover** (note 3 above) is the union of the stretches that successful trade reads covered,
    less the 60 seconds by which the tape may lag; a read stored with a snapshot whose book was invalid still
    counts as a read.

## Further clarifications for the scoring (2026-11-13, 22:37 UTC, before the first development day began; no answer has been compared with an outcome)

Raised by the blind check of the evaluation code, which ran on generated records only. Where that code differed
from clarifications 7 and 13, the code is being changed to the written text, not the text to the code. The rest
are readings the text left open; each is fixed here before any real slot can be scored.

17. **The clock of "later".** The target of "15 minutes later" and "30 minutes later", and the start of the
    paths, is the end time of the book snapshot the state was built on (the state's own "now", from which the
    collector aimed every look-back) plus 15 or 30 minutes. The snapshots used must still have begun after the
    decision (clarification 5). `toxic_15m` and the fills run from the decision time, as written.
18. **The later mid** (clarification 5) comes from the nearest valid snapshot within 30 seconds of the target;
    at equal distance the earlier one. If that snapshot's book is one-sided the outcome is unknown, even when a
    two-sided snapshot lies a little further off: no snapshot is preferred for the state of its book.
19. **Trade-tape cover** (replacing the 60 seconds of clarification 16): each covered stretch is shortened by 90
    seconds, 60 for the lag of the tape and 30 for the difference the collector tolerates between its clock and
    Kalshi's.
20. **The maker fee** (clarification 12): a series goes uncharged only when its fee type is one known to carry
    no maker fee. A missing, empty or unrecognised fee type is charged. A multiplier that is missing, not a
    number or not above zero is taken as 1.
21. **Membership of the policy.** A slot is in the policy averages only if the trade tape covers its fill
    window, whether or not any rule could quote in it: cover is decided before anything else, so the set of
    slots does not depend on any rule. Within covered slots clarification 9 holds.
22. **Edges.** The fill window is the 300 seconds after the decision, the decision instant excluded and the
    300th second included. "Ended before" a trade is judged by a snapshot's book end time. An imbalance of
    exactly 0.5 leans to neither side.
23. **Yardstick inputs from the state** (clarification 13): for `move_15m` the split is whether the absolute
    15-minute mid change in the state is at least half the state's spread; the previous 30-minute move for
    `regime` is the state's own 30-minute mid change. A state lacking the figure forms a group of its own.
24. **Resampling.** The seed is the pair (20261113, the question's position in the battery); percentiles by
    linear interpolation.
25. **A day's count** (clarification 2) is reduced by every answered slot under another battery, including one
    the collector itself did not count.
