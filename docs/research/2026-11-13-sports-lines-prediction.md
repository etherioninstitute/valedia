# Prediction, written before any data is scored: sportsbook lines against Kalshi's price for the same game

2026-11-13. Roadmap 1.18, at the owner's request. Locked by the commit that adds this file. Nothing below has
been looked at: no odds snapshot has been bought and no Kalshi game price has been compared with a result.

## The idea

Sportsbooks price a game with a margin. Remove the margin and the middle of several books is a probability.
Kalshi lists the same game as a contract paying 1 dollar. If the books' probability is better than Kalshi's
price by more than the fee, buying the cheap side should pay.

## Data

- **Sportsbook lines:** The Odds API, historical snapshots of the moneyline (`h2h`), ten named books in one
  request (one region's cost): pinnacle, draftkings, fanduel, betmgm, williamhill_us, betrivers, espnbet,
  fanatics, lowvig, betonlineag. A book missing from a snapshot is simply missing.
- **Kalshi:** the public history of the single-game series KXNFLGAME, KXNBAGAME, KXNHLGAME, KXNCAAFGAME,
  KXNCAAMBGAME: one-minute candles with bid and ask, and each market's result. No key is used.
- **Leagues:** NFL, NBA, NHL, college football, men's college basketball.
- **Pairing:** a Kalshi event and an Odds API event are the same game when the league matches, both team
  names match and the start is on the same US-Eastern date. Names are matched by code; what code cannot match
  is proposed by the local 27B model and accepted only if the date and both teams then agree. An unmatched
  game is left out and counted.

## Snapshots (fixed by rule, so the credit cost is known in advance)

For each league and each day with a game: one snapshot at 16:00 UTC, which also gives the start times; then one
snapshot 30 minutes before each wave of starts (starts within 90 minutes of each other form a wave), for at
most the three waves with the most games. At most 4 snapshots a league-day, 10 credits each. The spend on
history is capped at 9,000 credits; if the cap would be passed, whole days are dropped from the end of the
development period first, never from the test period, and the drop is reported.

## The numbers compared, at one moment

For a game in a wave, the moment is that wave's snapshot time as The Odds API reports it (30 to 120 minutes
before the start). At that moment:

- **Books' probability** `p`: for each book with both prices, divide each implied probability by their sum;
  take the median across books; at least 3 books are needed. Second version, reported beside it: Pinnacle alone.
- **Kalshi's price:** the ask and the bid of each team's contract from the candle of that minute. A game counts
  only when both contracts have a bid and an ask and the spread is at most 5 cents.

## Two questions

**1. Judgment.** Over every paired game, is `p` a better probability than Kalshi's mid? Measured by the Brier
score on the home team winning, with the difference tested by resampling whole weeks.

**2. Trading rule.** For each game, on the team where `p - ask - fee` is larger: buy one contract at the ask
when `p - ask - fee >= margin`, hold to the result. Fee: Kalshi's taker fee for these series, 0.07 x price x
(1 - price) a contract, rounded up to the cent on an order of 100 contracts. Profit a contract: result minus
ask minus fee. At most one trade a game. No selling, no averaging, no second look later.

## Periods

- **Development:** games from 2026-01-05 to 2026-03-01. Here, and only here, two things may be chosen: the
  margin (2, 3 or 5 cents) and the books' probability (the median of all, or Pinnacle alone). The choice is
  committed in a note under this file before any test-period snapshot is bought.
- **Test:** games from 2026-09-20 to 2026-11-08, scored once with the committed choice.
- **Forward check:** if the test passes, four weeks of live snapshots from the day the collector starts, same
  rule, scored once. Only after that would the rule go to paper trading.

## What counts as a pass (test period)

Both must hold:

- Judgment: the books' Brier score is lower than Kalshi's, and the week-resampled 95% interval of the
  difference excludes zero.
- Trading: at least 150 trades, a mean profit above zero after fees, and a t statistic of the weekly mean
  profits of at least 2.0.

Anything else is a fail, written up as such. A pass on one league only, found afterwards, is not a pass.

## What I expect

A fail, most likely on the trading part. Kalshi's game markets are heavily traded by people who watch the same
books, so the gap after fees should be small: I expect the two Brier scores within 0.002 of each other, fewer
than 150 trades at a 3-cent margin, and a mean profit a contract between -1.5 and +0.5 cents. Chance of a
pass: about 1 in 8. The data is worth having either way: the same pairs give the closing-line measure the
roadmap wants (1.14) and a fair baseline for any sports forecaster, the pick lists (1.19) included.

## Not known when this was written

- Kalshi's fee is taken from the series' fee type and a secondary source; the exchange's own fee schedule
  could not be fetched. If the real fee differs, the fee is corrected before scoring and the correction noted.
- Whether a historical request with ten named books costs one region (as live requests do). The first call's
  usage header settles it; if it costs more, the book list is cut to keep the cap, Pinnacle kept.
- Pinnacle's prices come from its public site and may lag.

## Clarifications made on 2026-11-13, before any odds snapshot was bought or any price compared with a result

Building the fetcher showed three places where the words above did not settle what to do. Each is fixed here,
before any scoring, and none was chosen by looking at results.

1. **Kalshi's price at the moment.** Kalshi writes no candle for a minute in which nothing changed. The price
   used is the last candle that ends at or before the moment, within the hour before it; with no candle in that
   hour the game is left out and counted.
2. **Waves.** A wave starts at the earliest start not yet in a wave and takes every start within 90 minutes of
   it, formed from all the league's games that day in the odds data, so each wave's snapshot falls 30 to 120
   minutes before every start in it.
3. **The day.** A league's day is the US-Eastern date of the game. A game that Kalshi lists under another date
   is left out and counted.

The choice made after the development period (margin and version of the books' probability) will be written
under this section before any test-period snapshot is bought.

4. **Start times are schedule facts, not prices** (added 2026-11-13 after the fetcher's blind check, still before
   any odds snapshot was bought). A league's start times for the day are taken from its 16:00 UTC snapshot and
   are used to form the waves, also for a wave whose own snapshot falls before 16:00 UTC (a noon kickoff in
   the east). Start times are published days ahead, so this lets nothing about the game's outcome or its
   prices into a decision. Every price and every probability still comes only from the wave's own snapshot
   and from Kalshi candles at or before that snapshot's time.
5. **A snapshot that is not fresh is not used** (same date). If the time The Odds API reports for a snapshot is
   more than 10 minutes before the time asked for, the games of that wave are left out and counted.
6. **Pairing is strict** (same date). A pair needs both team names to match with their qualifying words (State,
   Tech and the like), a start within six hours before Kalshi's expected end of the game, and no second game
   that day that would also match; a game that is ambiguous is left out and counted, never guessed.

## Two more clarifications, 2026-11-13, after the first pairing run and before any price was compared with a result

The development-period snapshots were bought (326 snapshots, 3,260 credits, 5 January to 2 February 2026) and
paired by the strict rule. The pairing counts showed two things about how Kalshi writes its markets. Both
were found from team names and times only; no price, probability or result had been looked at.

7. **Hockey names.** Kalshi writes a hockey team as a code and a nickname ("VGK Golden Knights") and puts the
   cities in the market's title ("St. Louis at Vegas Winner?"), so the strict rule matched 2 of 231 hockey
   games. For a market written that way, a team matches when its nickname is the end of the odds name and the
   city in Kalshi's title is its start, both with their qualifying words; the rest of clarification 6 is
   unchanged (uniqueness both ways, the time check, ambiguous games left out).
8. **The time check.** Kalshi's "expected expiration" is not always the game's expected end: a football game
   started 30 minutes after it, and afternoon basketball games started nine hours before it. The window of
   clarification 6 (a start within six hours before that time) therefore refuses true games. It becomes: the
   start is no more than 12 hours before and no more than 3 hours after Kalshi's expected expiration, on the
   same US-Eastern date. Two games between the same teams on one date are both left out as ambiguous.

## Closed on 2026-11-13 after the development period

No choice of margin or version was stored and no test-period snapshot was bought. The development period
(1,407 games) gave 0 to 4 trades at every setting, so the 150 trades the pass line needs could not be reached;
the idea is recorded as failed. See `2026-11-13-sports-lines-result.md`.
