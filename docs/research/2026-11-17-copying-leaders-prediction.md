# Copying the leaders of a prediction market: prediction written before any look (2026-11-17)

Written 2026-11-17, about 08:50 UTC, before any program for this test exists. The owner asked (2026-11-17)
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
