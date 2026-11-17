# Buying one side of a Kalshi market and holding it to the result, at Webull's cost: prediction written before any look (2026-11-17)

Written 2026-11-17, 07:37 UTC, before any program for this test exists and before anyone has set a price beside
a result in the stored data for this purpose. The owner wants live trading to start in Webull's prediction
markets (2026-11-16). This is the cheapest honest question about that venue: is there ANY band of prices where
simply buying one side and waiting for the result pays, after what Webull charges?

## What is tested

At one fixed moment in a market's life, for each side (yes, no): buy one contract at that side's ask and hold it
until the market settles. No exit before the result, no sizing, no model.

- **Price paid.** Yes: the candle's closing yes ask. No: one dollar less the candle's closing yes bid. A side is
  left out when its ask is missing, not above 0 or not below 1, or when the bid is not below the ask.
- **Cost.** 0.02 dollars a contract on the purchase and nothing at settlement. This is an assumption: Webull's
  own fee schedule for event contracts was not found; one broker-review page states 0.01 commission plus 0.01
  exchange fee a contract on every purchase and sale and no settlement fee; the paper account charged 0.01 a
  contract. The pass mark below uses 0.02.
- **Result of one trade.** One dollar if the side wins, else nothing, less the price paid, less 0.02.

## Where (two universes, judged separately)

- **W, New York daily-high markets (series KXHIGHNY).** The 9,910 settled markets already stored
  (`artifacts/kalshi_weather/`, closes from 2021-08-07 to 2026-11-12), with hourly candles carrying bid and ask.
  Webull lists this series (probed in its test environment today: all 6 open markets found under Kalshi's
  tickers). Moment of purchase: the end of the last hourly candle that ends at least 24 hours before the market's
  close. A market with no such candle is left out and counted.
- **S, single-game sports markets.** The 2,822 settled markets that have stored pre-game one-minute candles
  (`artifacts/sports_lines/kalshi.sqlite`: NBA, NHL, men's college basketball, a few NFL and college football;
  games from 5 January to 2 February 2026). Moment of purchase: the end of the last stored candle of the market's
  pre-game window. Webull lists these five series (roadmap 4.7).

One event (Kalshi's `event_ticker`: one day's temperature, one game) holds several markets whose results depend
on each other. The event is the unit for every count and every test below.

## Bands

The price paid is sorted into 5-cent cells from 0.05 to 0.95 (the last cell includes 0.95; prices outside are
left out). A band is any run of two or more neighbouring cells: 153 bands in each universe.

## Periods

- W: development is every event closing before 2025-01-01 (UTC); sealed is every event from 2025-01-01 on.
- S: development is every event whose purchase moment falls on or before 2026-01-21 (UTC); sealed is the rest.

The program refuses to read a sealed row without an explicit flag, and the sealed period of a universe is run
ONCE (a marker is written before the data is touched, as in the other locked tests).

## The decision rule

For each universe on its own:

1. **Development.** Among the bands with at least 300 development trades from at least 150 events, take the one
   with the highest mean result per contract. Its one-sided p-value comes from 2,000 resamples of whole events
   with a fixed seed (the share of resamples whose mean is not above zero). If that mean is not above zero or the
   p-value is above 0.025, the universe FAILS here and its sealed period is not opened.
2. **Sealed, once.** The same band, unchanged, on the sealed period. The universe PASSES only if the sealed mean
   result per contract is above zero, its p-value (same resampling) is at most 0.025, and there are at least 200
   sealed trades from at least 100 events.

The idea passes if either universe passes (0.025 each, 0.05 in all).

## What I expect

That it fails in both universes at step 1. Kalshi's prices at these moments have matched the best public
information in both earlier tests (the official forecast, the sportsbooks' consensus). The bias the literature
describes (cheap outcomes cost too much, so the other side is slightly underpriced) should show as a few tenths
of a cent to about a cent and a half a contract before cost on the expensive side, well under two cents. My
figure for the best development band in either universe: between minus 3 cents and plus half a cent a contract
after cost, with a p-value above 0.025.

## Reported for information, never a way to pass

The whole table by cell (trades, events, share won, mean price, mean result) for the development period, and for
the sealed period only if that universe's sealed run happened; the same development table at a cost of 0.01 and
at Kalshi's own taker fee (seven hundredths of price times one less price, rounded up to the cent); for W, the
same at 12 hours before the close. None of these chooses a band or changes a verdict.

## What a pass would and would not mean

A pass means one band is worth a paper record in Webull's paper Events account at one contract a trade. It says
nothing about size (the candles carry no depth), and it is not a go-live: the owner's limits, the paper record
and his go come after. A fail means that buying and holding at posted prices does not pay through Webull at this
cost; it says nothing about resting orders or about a forecaster that knows more than the market.

## Limits, stated now

- The cost is an assumption until Webull's own schedule or a real fill confirms it.
- The stored closing ask of an hourly candle is taken as a price at which one contract could be bought; a thin
  book may not have offered it.
- W's data was used once before, for a different rule (the official forecast against the market, 2026-11-13,
  failed). That result file has no table of results by price; nobody has computed one.
- S covers four weeks of one winter. Its sealed period is short: it may be unable to pass even where an edge
  exists.
- Two universes and 153 bands each is a search. The search happens only on development data; the sealed run tests
  one band chosen beforehand.

## Note added 2026-11-17, 07:39 UTC (no rule changed; no program for the test has run)

The cost is no longer an assumption. Webull's own help page "Event Contract Fees"
(webull.com/help/faq/11174-Event-Contract-Fees) states 0.01 dollars commission plus 0.01 dollars exchange fee a
contract when a position is opened, the same again when it is sold before expiration, no fee for holding to
settlement, and no fee for an order that is cancelled or expires unfilled. That is the 0.02 a contract on the
purchase and nothing at settlement used above. The paper account's 0.01 a contract is therefore not the real
charge.
