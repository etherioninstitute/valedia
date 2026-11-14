# Does r/sportsbook chatter come before the Kalshi price? Prediction written before any data is collected (2026-11-13)

Written by the lead on the evening of 2026-11-13, before the collector for this test exists. Nothing has been
collected, read or scored. The owner asked for a minute-level reader of r/sportsbook; this is the test that says
whether what it reads is worth anything. It has its own store and does not touch the pick-list test or the
order-book test.

## The idea being tested

A comment in r/sportsbook that names one team of an upcoming game together with a word of news about that team
(a player out, a player back) is posted before the Kalshi price of that game has finished moving. If so, buying
or selling the team's "wins" contract when Valedia first sees such a comment would make money after fees.

I expect it to fail. Sportsbooks and market makers get injury news from faster feeds than a forum, and Valedia
sees a comment up to several minutes after it is posted.

## What is recorded (by a collector to be built; nothing below depends on a model)

- **Comments:** the newest comments of r/sportsbook, read about every two minutes from the public feed (or
  every minute from Reddit's official interface if the owner later supplies an app), every version kept, each
  with the time Valedia first saw it on its own clock. That first-seen time is the only comment time the test
  uses: Valedia could not have acted earlier.
- **Prices:** once a minute, for every open game market of Kalshi's five game series (professional football,
  basketball and hockey; college football and men's basketball), the best yes bid, the best yes ask and the
  last price, from Kalshi's public interface.

## The test

**An event** is the first version of a comment that (a) names exactly one team, judged against every team with
a game that day in those five leagues by the same name rules the pick grading uses, and no other team; (b) for
a game that starts between 10 minutes and 6 hours after the comment was first seen; (c) holds one of the news
words below, as whole words, case ignored; and (d) is the first such event for that game, team and direction in
30 minutes. A comment holding words of both directions is no event.

- **Bad news for the team (direction -1):** out, ruled out, scratched, scratch, inactive, injured, injury,
  doubtful, downgraded, suspended, benched, sidelined, "not playing", "won't play", "will not play", "sitting out".
- **Good news for the team (direction +1):** returns, returning, "will play", "is playing", active, activated,
  upgraded, cleared, "back tonight", "good to go".

**Entry** is the first price snapshot of that team's "wins" market taken at least 60 seconds after the event's
first-seen time, if it is taken within 5 minutes of it and shows both a bid and an ask with a spread of at most
10 cents; otherwise the event is left out and counted. **Exit** is the first snapshot at least 10 minutes after
entry, taken within 15 minutes of it, with both sides shown; otherwise left out and counted.

- **Question 1, drift:** direction x (mid at exit - mid at entry), in cents, averaged over events.
- **Question 2, a trade:** for direction +1 buy one contract at the ask at entry and sell it at the bid at exit;
  for direction -1 buy one "no" contract at (100 - bid) at entry and sell it at (100 - ask) at exit; less
  Kalshi's taker fee on both trades, each 0.07 x price x (1 - price) rounded up to a whole cent. The result in
  cents per event.

**Periods.** The first 7 full UTC days after the collector starts are for checking that it works and for a hand
audit only: 40 events drawn at random are read by a person as untrusted text, and the matching passes if at
most 2 name the wrong team, the wrong game or the wrong direction; if it fails, the idea stops here and the
rules are not repaired after the fact. No price of those 7 days is ever scored. The **test** is the next 28
full UTC days. A day counts if both recorders covered at least 80% of it.

## The rule

The idea passes only if, over the test period, there are at least 150 events on at least 15 counted days, and
BOTH: the mean of Question 1 is above zero with a by-day t statistic of 2 or more (each day's mean one
observation), and the mean of Question 2 is above zero with a by-day t statistic of 2 or more. It is scored
once, by code written and checked before the test period ends, and the result is reported whatever it is.
Passing earns a paper-trading trial under the same limits as every other idea; it earns no real order.

## What I expect

- Fewer events than hoped: perhaps 5 to 20 a day.
- Question 1: a small positive drift is possible (about one chance in five that it is distinguishable from zero).
- Question 2: negative, because the spread and two fees cost 3 to 6 cents a round trip. About one chance in
  twenty that the idea passes both.

## Known limits, stated now

The public feed returns at most about 100 comments a request and allows about one request a minute for this
connection, shared with the hourly pick reader, so the comment reader stays silent for some minutes each hour
and can miss comments on the busiest days; gaps are recorded and days under 80% do not count. First-seen time
runs behind posting time by the polling interval, so this tests what Valedia could trade, not whether the forum
was first. Word lists miss sarcasm, questions and quotes of old news; the audit of 40 measures that. One
contract says nothing about size.

## Clarification 1 (2026-11-14, 03:14 UTC: before the collector has run; nothing has been collected, read or scored)

**A third way of reading the same comments.** The owner has taken an account with a paid reader service
(ScrapeBadger), which returns the newest comments of one thread at a time, with each comment's own posting time
and edit mark. It may be used beside the public feed: to read the day's busy threads during the minutes the
public feed must stay silent, and after a mark that comments may have been missed. It changes nothing in the
test: an event's time is still the time Valedia first saw that version of the comment on its own clock, whatever
brought it; a comment already seen through one way is not a new version when it arrives through another; the
posting time the service gives is stored as information and is not used to move an event earlier.

## Clarification 2 (2026-11-14, 03:17 UTC: before the collector has run; nothing has been collected, read or scored)

**The paid reader service will not be used.** The owner decided to keep the plan as first written: comments are
read from the public feed, or from Reddit's official interface if he later supplies an app. Clarification 1
therefore has no effect; nothing else changes.
