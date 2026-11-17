# Copying the leaders of a prediction market: prediction written before any look (2026-11-17)

Written 2026-11-17 and committed at 08:46 UTC, before any program for this test exists. The owner asked (2026-11-17)
whether Valedia could copy the positions of the top five or ten traders on Polymarket's leaderboard, find the
same questions on Webull, and have Elephant-1 judge them. Before any of that is built, this asks the question
it all rests on, on history, cheaply: **after a leader's new position has become visible, is the price still
wrong by more than it costs to follow?** If it is not, on the leaders' own venue and with hindsight in their
favour, then matching markets across venues and judging them with a model cannot rescue it.

## What was looked at before writing this (shapes and totals only; no entry was set beside an outcome)

Polymarket's public data service (no key, a plain reader with a generic name): the month's and the all-time
profit boards; the open positions of the month's top ten; one wallet's first five trades, activity rows and closed
positions, for field names. Seen: the month's top ten each show 1.5 to 3.7 million dollars of profit; only 2 of
those ten are among the all-time top hundred; nine of the ten hold more than a hundred open positions; of 942
positions read, 500 sit under 5 cents or over 95 cents, the middle position is worth 81 dollars, and about six in
ten are sports. Five of the ten trade about 50 shares for every dollar of profit, the mark of a market maker.

## The data

For each wallet in the candidate set: its trades (side, price, size, time, outcome token, market, event), from
the public trades feed; each market's resolution from the public resolutions route; each outcome token's price
history from the public price-history route.

**Candidate set (fixed now):** the wallets on the all-time profit board's first 100 places and the month's first
100, read once, on the day the data is pulled. This set is chosen with hindsight (these wallets are known to have
ended rich), which favours copying. That is deliberate and stated: the test can sink the idea, it cannot prove it.

## Who counts as a leader in a given month (no look ahead inside the set)

For calendar month M, a wallet's standing is its realized profit over the three calendar months before M, computed
from its own trades and the resolutions known before M began (a position still open at the start of M counts at
its cost, neither gain nor loss). The LEADERS of M are the ten wallets of the candidate set with the highest such
profit, among those with at least 30 resolved positions in those three months and fewer than 20 shares traded per
dollar of profit over them (which leaves the market makers out). Fewer than ten qualifying: those there are;
fewer than three: the month is left out.

## The trade that is copied

A leader's NEW position in month M: the first purchase of an outcome token by that wallet (no holding of that
token, or of the opposite outcome, in the 30 days before), where
- the wallet's purchases of that token within 24 hours of the first cost at least 1,000 dollars in all;
- the first purchase's price is from 0.10 to 0.90;
- the market resolves within 45 days of the first purchase, and its resolution is known and is one of its two
  outcomes (a market resolved otherwise is left out and counted).

The follower buys ONE contract of the same outcome at the token's price ONE HOUR after the leader's first
purchase (the last price in the public price history at or before that moment; no price within the six hours
before it: left out and counted), provided that price is still from 0.05 to 0.95, and holds it to the resolution.

## Cost and result

Cost per contract: 0.03 dollars: Webull's 0.02 (0.01 commission and 0.01 exchange fee on a purchase, nothing at
settlement, from its own help page) plus 0.01 for buying on another venue at another moment. Result of one trade:
one dollar if the outcome wins, else nothing, less the price, less 0.03.

One event (Polymarket's event) may hold several copied trades, from one leader or from several. The event is the
unit: results are added up within an event and every test below resamples whole events.

## Periods

Development: months M up to and including June 2026 (as far back as the feeds reach, three years). Sealed: July to
October 2026. A trade belongs to the month of the leader's first purchase. The program refuses to read a sealed
month without an explicit flag, and the sealed period is run once (a marker written before the data is read).

## The decision rule

There is ONE rule, stated above, so there is nothing to choose on development data; development exists to find
faults in the program and to see whether the sealed run is worth spending.
1. **Development.** The mean result per contract over all copied trades, with a one-sided p-value from 2,000
   resamples of whole events (fixed seed; the share of resamples whose mean is not above zero). If the mean is not
   above zero or the p-value is above 0.05, the idea FAILS here and the sealed period is not opened.
2. **Sealed, once.** The same rule on the sealed months. PASS only if the mean result per contract is above zero
   with a p-value at most 0.05, on at least 200 trades from at least 100 events.

## What I expect

That it fails at step 1. A leader's board profit is mostly not skill that repeats (2 of this month's ten are
all-time leaders), an hour is long enough for a price to take in a visible million-dollar buyer, and three cents
is a high bar. My figure for the development mean: between minus 4 cents and plus 1 cent a contract.

## Reported for information, never a way to pass

The same figure with the purchase 10 minutes, 6 hours and 24 hours after the leader's; at a cost of 0.02 and of
0.00; for sports and for everything else; for first purchases over 10,000 dollars; and the leaders' own result on
the same trades at their own prices (did they have an edge at all, before any delay).

## What a pass would and would not mean

A pass (on a set chosen with hindsight) would justify only the next steps: matching the leaders' markets to the
contracts Webull lists (teams and dates for games; Elephant-1 as the judge of "is this the same question" for the
rest), then a forward paper record, started from a leader set chosen on the day with no hindsight at all. A fail
means following the board does not pay at this cost; it says nothing about using a leader's position as one input
among others.

## Limits, stated now

- The candidate set is chosen with hindsight; the bias runs in favour of the idea.
- Prices are Polymarket's, not Kalshi's; the extra cent is a stand-in for that difference, not a measurement.
- A leader's visible position may be one leg of a hedge held elsewhere; copying a leg is not copying the trade.
- One contract a trade; nothing here speaks about size.
- Polymarket's terms (effective 11 August 2026), as the lead reads them: a person in the United States may use
  its information features and may not trade there. Nothing here trades there. This is the lead's reading, not
  legal advice; the owner can stop the reading of that data at any time.

## Amendment of 2026-11-17, 09:11 UTC (before any data for this test has been pulled; no program has run on real data)

**What changes: where the follower's price comes from.** The text above takes it from the public price history.
Building the reader showed that this history keeps fine detail only for recent weeks (one-minute points for about
7 days, five-minute for about 60, thirty-minute for about 90) and, for anything older, only three-hour and
twelve-hour points. For most of the three years, "the last price at or before one hour after the leader's
purchase" would then be a three-hour figure formed mostly BEFORE that purchase: the follower would be given the
price from before the leader moved it. That favours copying and is not a price a follower could have paid.

**The follower's price is now:** the price of the FIRST trade in that market, at or after one hour after the
leader's first purchase, in which a buyer took that same outcome token (a taker's purchase, from the market's own
public trade feed, which reaches back three years). No such trade within six hours after that moment: the copied
trade is left out and counted. The price must still be from 0.05 to 0.95. This is a price somebody demonstrably
paid for that outcome after the hour had passed. Everything else stands: the leaders, what a new position is, the
1,000 dollars, the 0.10 to 0.90 first price, the 45 days, the cost of 0.03, the event as the unit, the periods,
the one rule and its p-value. The information figures at 10 minutes, 6 hours and 24 hours use the same source.

**What does not change and why this is not a second look:** no trade, price or resolution of any wallet has been
read for this test; the only data read are the boards and shapes listed at the top. The direction of the change is
against the idea (a later, paid price instead of an earlier, stale one).

**Scope of the pull, stated for the record:** trades and resolutions are read for every candidate wallet (they
decide who the leaders are); the market trade feed around a purchase is read only for positions of a month's
leaders in that month, since no other position enters the rule.

## Second amendment of 2026-11-17, 09:35 UTC (still before any data for this test has been pulled)

**Why the first amendment cannot be carried out.** The market's trade feed serves a fixed three-year window,
newest first, and takes no time bounds for a market. To find the first purchase after a moment in the past the
whole feed has to be walked back to it. Measured on 14 markets taken from leaders' closed positions (counts of
rows only, no price and no outcome): 8 could be walked in 1 to 14 pages of a thousand prints; 6 were cut off at
25 pages, having 25,000 purchases inside a fraction of a day to eleven days of trading. For the busy markets,
which are where leaders trade most, the walk is not feasible at any polite rate.

**The follower's price is now:** the price of the first point of the public price history for that outcome token
whose time is at or after one hour after the leader's first purchase, asked for over the twelve hours from that
moment, at a grain of three hours or finer. No such point: the copied trade is left out and counted. The price
must still be from 0.05 to 0.95. Measured on nine windows from 100 to over 700 days old (shapes only): such
windows are served; six held a point, the first of them 2.7 to 6 hours after the hour; three held none.

**What this means for the test, stated plainly.** (1) There is no look back: a point's time is the start of the
span it was observed in, so a point at or after the hour was observed after it. (2) For anything older than about
ninety days the follower is priced three to six hours after the hour, not at it: later than the label "one
hour", which works against copying where prices move toward the leader, and is the honest reading of what this
data can show. (3) The figure is a price the market recorded, not an ask a buyer paid; the cost of 0.03 already
carries one cent for that kind of difference, and the result is also reported at 0.04. (4) Trades whose market
recorded nothing in those twelve hours drop out, which removes the quietest markets.

Everything else stands as written and as first amended: the leaders, what a new position is, the 1,000 dollars,
the 0.10 to 0.90 first price, the 45 days, the cost, the event as the unit, the periods, the one rule, its
p-value, the scope of the pull. The information figures are given at 6 hours and 24 hours with the same source;
the 10-minute figure is given only for the months where a grain of five minutes or finer exists.

**What has been read by now, in all:** the boards; position lists for shapes and totals; for the two feasibility
measurements above, counts of purchase prints in 14 markets and the shape of nine price windows. No entry has
been set beside an outcome.
