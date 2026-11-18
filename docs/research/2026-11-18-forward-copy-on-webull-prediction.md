# The owner's copy method on Webull, forward and on paper: rule written before the first copy (2026-11-18)

Written 2026-11-18 by the lead, before the screen, the matcher or the copier of this test exists (only the
leaders' record does) and before any wallet's past results have been set beside any of the measures below. The owner's method (his video of 2026-11-18, analysed in
`~/Downloads/Copytrade_analysis.md`): from Polymarket's leaderboard pick CONSISTENT, low-volatility leaders
(not the top by profit), check their closed positions for wins, copy what they hold NOW onto his own venue,
repeat daily. His venue is Webull, whose event contracts are Kalshi's. This is a FORWARD test on paper: no
order is sent anywhere, and nothing here is a result yet.

It stands beside the historical test of 2026-11-17 (`2026-11-17-copying-leaders-prediction.md`, called T47 below)
and does not wait for it to pass: the owner asked for the method as shown, so it is tested as shown, forward,
with no hindsight after the day the wallets are fixed.

## What was looked at before writing this

- The study of one day, 2026-11-18 (T53, `.foreman/tickets/T53-step0-owner-copy-method-on-webull.md`): what the
  ten leaders fixed on 2026-11-17 held, which holdings have a Kalshi market, Kalshi's prices that afternoon
  against the leaders' entry prices (the ask was at or below the leader's own entry for 37 of 91), that leaders
  sit on opposite sides of 45 of the 69 markets more than one of them holds, that three price-level pairs had
  already resolved differently on the two venues, and Webull's fee.
- T47's first development run: no trade at all (a reading fault, since ruled on), so no result of any kind.
- The leaders' record's two passes: counts of new, grown, shrunk and closed positions only.
- NOT looked at: any wallet's realized profit by month, its share of wins, its drawdown, or any other measure
  the screen below uses; any result of a copied position.

## The wallets (the screen), fixed once

**Candidates:** the 189 wallets whose full trade histories are in the copy-leaders store (the all-time and the
month's first hundred on Polymarket's profit boards on 2026-11-17, less three with more than five million trades
each). Chosen with hindsight, as T47 says of itself; from the screen day on, nothing looks back.

**The screen day and its window.** The screen is run ONCE, when its program has passed its blind check AND T47
is closed (its development stage has failed again, or its sealed stage has been run); it then reads resolutions
up to the end of the last complete calendar month. If T47 is still open on 2026-11-24, the screen does not wait
longer: it is run as soon as its program is ready and reads NO resolution after 2026-06-30 (T47 seals July to
October 2026). The window W is the twelve complete calendar months ending at that cutoff. The set is never
re-ranked during the test.

**A wallet's realized profit in a month:** over its sales in that month, (price less the average cost of the
shares sold) times the shares; plus, over the positions whose market's known resolution falls in that month,
(payout a share less average cost) times the shares held then. A resolution is known as T47's document and its
five amendments read it. A position not sold and not resolved counts at its cost. A CLOSED position is a wallet's
holding of one outcome token that went to nothing by sale or by resolution. A month COUNTS when the wallet closed
at least 5 positions in it.

**Eligible** (every figure is the lead's choice, fixed here):
1. at least 9 counted months of the 12, and at least 100 closed positions in W;
2. a realized profit over W above zero, and fewer than 20 shares traded for each dollar of it (T47's line for
   market makers);
3. both outcomes of the same market bought in at most 10% of the markets it traded in W;
4. "wins on its closed positions", read so that a buyer of 95-cent favourites does not pass by arithmetic: among
   its positions held to a known resolution in W (at least 50 of them), the share that won (a half payout
   counts as half a win) is ABOVE the mean, over those positions, of the average price it paid a share.

**Rank** (the method's "consistent, low volatility"): the mean of the counted months' realized profit divided by
their standard deviation, highest first; ties by the larger margin in point 4. **The set is the first 20
eligible wallets** (those there are, if fewer; fewer than 5: the test does not start, and that is reported).
The leaders' record is then pointed at exactly that set.

## The copy

At each pass of the record after the set is fixed, for each wallet of the set with a good read:
- **What.** At the wallet's first good read (its baseline): each holding, which is the method's "what they hold
  now". At every later read: each NEW position that the record vouches for (seen to open between two clean
  reads). In both cases only where
  - the holding's value at that read is at least 1,000 dollars, and Polymarket's price for it is from 0.10 to
    0.90;
  - its market has a counterpart in the RULE MAP (below) of class SAME, in a series the LISTING TABLE (below)
    holds, open at the match read;
  - no other wallet of the set holds the opposite outcome of that market at that pass (then no copy is made:
    "the leaders disagree", counted);
  - no copy of that Kalshi market and side is already open (then the wallet is noted on the open copy).
- **The paper purchase.** ONE contract of that side at Kalshi's ask at the MATCH READ: the first read of
  Kalshi's public book for that market after the pass, stamped; never the leader's price, never Polymarket's.
  The match read must fall within 6 hours of the pass's read of that wallet and the ask must be from 0.05 to
  0.95; otherwise the copy is not made and is counted with its reason. The contracts on offer at that ask are
  recorded.
- **The close.** (a) Kalshi settles the market: the copy pays what one contract of its side is settled at.
  (b) Before that, a clean read shows that the wallet which opened the copy no longer holds the token (the
  record's EXIT): the copy is sold on paper at Kalshi's bid at the first Kalshi read after that pass. (c) The
  two venues part (one settles while the other still trades, or they settle differently): the copy is held to
  Kalshi's settlement and flagged; its result counts.
- **Cost.** Webull's real fee as its help page states it (read 2026-11-17, `docs/WEBULL.md`): 0.02 dollars a
  contract on every opening and every closing trade, nothing at settlement.
- **Result of one copy:** what it paid or was sold at, less the ask it was bought at, less 0.02, and less
  another 0.02 if it was sold. The unit is Kalshi's EVENT: results are added up within an event.

The pass runs as often as the owner allows (today: once a day). A change of that schedule is recorded with its
date and is not a change of this rule.

## The rule map and the listing table (two files kept beside this one, each change dated and committed)

- **Rule map**, by Kalshi series: SAME, NEAR or NONE, decided by the lead from BOTH venues' rule texts read in
  full for that kind of market. SAME: the same event and side, and the two texts pay alike in every outcome
  except rare exceptions the map lists by name (a game not played inside the venue's window, a result changed
  after the fact). NEAR: they can pay differently in ordinary outcomes (another price source, another moment of
  measure, a threshold that one includes and the other does not, an added condition). A series enters the map by
  a commit made BEFORE any copy in it is made; copies are never made backwards. The map starts EMPTY.
- **Listing table:** the Kalshi series that Webull is seen to list as tradable under the same tickers, with the
  date and the way each was seen. A series not in the table gets no copy.
- Positions whose counterpart is NEAR are recorded in a separate book for information and never enter the rule.

## The decision rule (ONE rule, ONE read)

When at least 200 copies from at least 100 events are closed, and not before: the mean result per contract over
all closed copies, with a one-sided p-value from 2,000 resamples of whole events (fixed seed; the share of
resamples whose mean is not above zero). **PASS** only if the mean is above zero and the p-value is at most 0.05.
Until that count is reached no program computes or prints a result of any copy: the copier records purchases,
prices and closes, and shows counts only. If the count is not reached within 120 days of the first copy, the test
ENDS with the finding "the method cannot be carried out on Webull at a scale that can be tested", which is not a
pass.

## What I expect

That it does not pass, and more likely by starving than by losing: most of what such wallets hold has no Kalshi
market, a game resolves inside the day, the price a follower pays is today's and not the leader's, and four
cents a round trip is a high bar. My figures: fewer than 200 closed copies in 120 days with one pass a day; a
mean result per contract between minus 4 cents and plus 1 cent.

## Reported for information, never a way to pass

The same figure for baseline holdings and for new positions apart; for copies bought only when the ask was no
more than 0.05 above the leader's own average price; with every copy held to settlement whatever the leader did;
at a cost of zero; by family of market; the NEAR book; and the leaders' own result on the same positions at
their own prices.

## What a pass would and would not mean

A pass would justify the next step only: the event order step (T45-2) wired to this rule at the smallest stake,
after the owner's limits for event contracts. It would not show a profit at any larger size (one contract a
copy; fills at the ask are assumed, never seen), nor that Webull's live account lists and fills what its test
environment lists.

## Limits, stated now

- The candidates are chosen with hindsight; the screen reads the past; only the copies are forward.
- A wallet's visible position may be one leg of a trade held elsewhere; realized profit hides losses still open.
- Kalshi's public data is read slowly here, beside a collector that uses the same host, so the match read can
  trail the pass by minutes to hours.
- Polymarket is read as public information only; nothing trades there. This is the lead's reading of its terms,
  not legal advice; the owner can stop the reading of that data at any time.
