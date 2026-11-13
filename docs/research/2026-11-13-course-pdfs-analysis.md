# The "AI Trading Bot" course PDFs, checked against Valedia

2026-11-13. The owner asked for the folder `~/Downloads/AI Trading Strategy PDFs` to be analysed, for anything
worth having to be in Valedia cleanly, and for a list of what is done and what is left.

## What was read, and how

36 PDFs, 192 pages, all from one course ("AI Trading Bot", Master And Monetize AI). Three parts:

- **A. The beginner bot** (27 lessons, 0.1 to 8.4): a Python bot on an Alpaca paper account.
- **B. The "Desk" weather bot** (3 lessons): a prediction-market bot on Polymarket, run by a hosted AI bot.
- **C. Copy trading** (6 lessons): buying what company insiders and politicians disclose, through AI connectors.

The text of every page was extracted and read in full by three readers, who wrote digests. The lead read the
three digests and checked 20 facts from them against the lesson text; all 20 were found. Pictures could not be
read. Three places keep content only in screenshots: "Assisted AI Trading With Liquid" (a third mistake and a
test prompt), "Copy Trade Execution" (a suggested wording) and "Connecting Live Copy Trading Data" (settings).
All three belong to part C, which is not adopted as built (see D), so nothing below rests on them.

The PDFs were treated as data. They contain prompts to paste into an AI, a line saying live keys can be plugged
in "similarly", and a lesson called "Disable Paper Trading". None of these was followed.

## Short answer

- **A, the beginner bot:** Valedia already has every piece in a stricter form, except three: alerts to the
  phone, approval from the phone, and a cap on orders per day.
- **B, the weather bot:** the idea was already tested here and failed, and the course's own two-week replay
  lost 23%. Its safety habits are here already or on the roadmap. One is new: a go-live checklist with numbers.
- **C, copy trading:** not in Valedia. The signal is untested, so it joins the roadmap as an idea to test
  (1.17), not as a feature. The course's way of placing the orders is deliberately not adopted.

## A. The beginner bot

| Course piece | In Valedia | Status |
| --- | --- | --- |
| Pipeline: data, rule, signal, risk check, paper order, log | rule library and gate (`research_loop`), `risk_gate`, `PaperTrader`, `Journal` | done |
| Daily prices from Yahoo for one fund, a preview, a check for missing values | `data_and_fees/prices.py` for funds, stocks and crypto; a refresh is refused when a symbol returns nothing; the unfinished session is dropped | done. A second price source to cross-check is 5.2, not started |
| 20/50 moving-average crossover giving BUY, SELL or HOLD | `ma_cross` in the rule library, at any two speeds | done and tested: the two ideas built on it (16 and 21) failed the gate |
| Backtester: 10,000 dollars, all in, no costs, no comparison | `backtest/engine.py` with fees, spread, impact and borrow cost; comparison with simply holding; the last 25% of history sealed; a written prediction before the test; every attempt counted | done, stricter |
| "A backtest is a first filter, not proof" | the same view. 24 of 26 ideas have failed; 2 passed and go on to paper trading | done |
| Risk rules: at most 10% a position, at most 3 trades a day, paper only, block on missing inputs | `config/risk.json`: daily loss stop, a stop on the fall from the peak with a latch only a person resets, a cap per position, a cap on positions; a sale can never exceed what is held. The course has none of these four | done, except a cap on orders per day (added to 5.7) |
| Alpaca paper account | `AlpacaPaperVenue` (`paper-step --alpaca`), and Webull's test environment (`--webull`) | done |
| A tiny paper order through the risk check | practice orders placed and cancelled in Webull's paper accounts on 2026-11-13 | done; a filled order waits for the market's open |
| Trade journal: signals, trades, blocked trades, errors; never log keys | `Journal`: every order, refusal and tax note. Keys cannot reach the journal, the screen or the logs (5.6) | done, stricter |
| Daily summary | a day summary in the journal, with tax so far and the comparison with holding | done on disk. To the phone: 4.5, not started |
| An AI review of the project | a fresh blind verifier on every change that can move money; a weekly critic | done. A reviewer from another model family waits on the owner (5.4) |
| Monitoring loop every 5 minutes, market hours only, no duplicate signals | a weekday paper step on daily prices; the order ledger sends an order at most once | started: the weekday schedule waits on two commands from the owner (2.4). Rules on 5-minute prices are untested (1.9) |
| Phone notification through an ntfy.sh topic | none | not started (4.5; Signal waits on the owner's login job). An ntfy topic can be read by anyone who learns its name, so Signal stays the plan |
| Approval by typing APPROVE in an AI chat session | approval by ticket number, enforced by code | not started (4.8). In the course the gate is an instruction to a chat session; here it must be code |
| Running the approved paper trade; five tests by hand | order ledger and Webull adapter, each blind-verified | started (4.8): the piece that turns the strategies' decisions into ledger orders is not built |

Weak points the readers found in the course's bot, and where Valedia stands on each:

- A pending trade never expires. Here an order not sent within six hours of being decided is not sent.
- The risk check is not repeated at approval. Here: to build with approval by ticket (4.8).
- There are no exits and no loss limits. Here the rules set holdings in both directions and the risk gate stops the day.
- There is no test on unseen data. Here the sealed period is the gate.

## B. The "Desk" weather bot

| Course piece | In Valedia | Status |
| --- | --- | --- |
| Trade temperature brackets when a forecast model and the price disagree by 8 points after costs (its FADE rule) | tested on all 9,910 settled New York markets on Kalshi, fees included: the rule lost 4.3 cents a contract in the test period and the market's price beat the forecast (`2026-11-13-kalshi-weather-result.md`) | done: failed (1.13) |
| The course's own evidence | its two-week replay lost 23% (50 dollars to 38.53), the market scored better than its model (0.174 against 0.211), and it then switched FADE off | agrees with the result here |
| Its LOCK rule: buy a bracket that the day's readings have all but settled, at 5 points after costs | not tested here. In the course's replay it never fired | not tested (to-do 6) |
| Four more cities (Miami, Chicago, Los Angeles, San Francisco) | not tested; New York only | not tested (to-do 6) |
| Polymarket as the venue, with the wallet key in a file on the bot's cloud computer | Kalshi's public data today; Webull's Events account for reading next | not adopted (D). A second venue is 4.7 |
| Fee of 0.0695 x price x (1 - price); orders that wait in the book first; a widest spread | Kalshi's own fee formula in the weather test; the order-book loop's paper rules quote from the waiting side (1.16) | done for Kalshi. Costs for every venue is 1.11 |
| Kill rule: stop if the model does not beat the market after 200 scored brackets or 30 trades | every idea has a pass line written before the test and is scored once; forecasters are scored against the base rate and the market | done for research. For live strategies: the decay monitor (2.7), not started |
| Old-data rules (readings 75 minutes, forecasts 9 hours) | the weather reader has its own (3 hours); there is no general rule for prices | not started (5.2, 5.7) |
| Half Kelly sizing, 6% a position, 5 positions | fixed caps and equal shares between strategies | not adopted until a strategy has a measured edge. Splitting capital is 1.10 |
| Warning at a 20% fall, halt at 40% | daily loss stop and a halt on the fall from the peak, with a latch | done, tighter |
| Go-live gate: 48 hours of paper, 10 closed trades, 5 checked by hand, model beats market | a sealed test, a paper record, the order path proven in the test environment, the owner's go, a file the owner writes by hand | stricter in kind, but the numbers are not written down: added as 4.9 |
| "Data is not instructions"; never keys in chat; only the owner switches to live | the same rules. The model answers typed questions; rules create the orders | done |
| Shadow log, scorecard, post-mortem | the panel runs in shadow mode and is scored; a weekly critic | started. Daily self-review is 2.2, not started |
| Routines: scan every 30 minutes, report at 09:00 | launchd jobs | started (2.4) |
| A monthly "pay yourself" routine | none | not adopted: Valedia never moves money |
| Dashboard with a health light | the `doctor` command | started. One page for the owner is 5.3, not started |

## C. Copy trading

| Course piece | In Valedia | Status |
| --- | --- | --- |
| Buy what insiders bought on the open market in the last 3 days, and what politicians disclose, from QuiverQuant (30 dollars a month) | none | not started: new item 1.17, an idea to test |
| Tiers of signal strength; one named insider to follow first | none | part of 1.17. The course shows no test of either |
| Orders placed through "Liquid" by an AI connector after a confirmation in chat | none | not adopted (D) |
| Risk 1 to 2% of the account a position, no borrowing, a market-hours check, a log against duplicates | position cap, long only with no borrowing, the exchange calendar, the order ledger | done |
| Scheduled routines (the course's schedule line runs hourly, not as described, and its time zone looks wrong) | launchd jobs with the exchange calendar | started (2.4) |
| "Disable Paper Trading": real money, buy only, the dollar cap left blank, 1 trade a day, "approve" in chat, refuse if the price moved 3%, the routine's on/off switch as kill switch | live trading needs the owner's hand-written file and the owner's go | not adopted as a whole (D). Two parts are worth taking: the price check at approval (4.8) and the cap on orders per day (5.7) |
| Risk-management module description | | nothing new |

## What is left to do because of this course

1. **Alerts and the daily summary on the phone** (4.5). Waits on the Signal login job.
2. **Approval of live orders by ticket number, in code** (4.8). At approval the price is checked again (refuse
   if it moved more than a set amount) and the risk gate runs again; a request nobody answers expires.
3. **A cap on orders per day** (5.7), with the other limits listed there. The number is the owner's to set.
4. **A written go-live checklist with numbers** (4.9): how long a paper record, how many filled paper orders
   reconciled without a difference, how many checked by hand.
5. **Test the copy-trading signals** (1.17) on public filings, with the prediction written first. Insider
   filings are free from the SEC. A fair test needs stock data that includes dead companies (1.6).
6. **Optional, low value:** the weather LOCK rule and the four other cities. Each needs a new written
   prediction, and the evidence so far points against the family.

Already on the roadmap and unchanged by the course: the weekday schedule (2.4), intraday data (1.9), old-data
limits (5.2, 5.7), the decay monitor (2.7), the split of capital (1.10), one page for the owner (5.3), daily
self-review (2.2).

## D. Deliberately not adopted

- **A language model that places orders after "approve" in a chat** (the Liquid connector, routines with
  broker access). In Valedia the model answers typed questions, rules create orders, and code checks them.
- **"Disable Paper Trading" as written.** It asks for no paper record, leaves the dollar cap blank and has no
  loss limit. It also lets the switch to live be asked for in chat.
- **Keys in a file on a hosted bot's computer**, which the course itself calls an exposure, and live keys
  added to the same file as the paper keys.
- **Polymarket with a wallet key.**
- **Trade alerts on a public ntfy topic.**
- **Half Kelly sizing** before any strategy has a measured edge.
- **A routine that withdraws money.**
- **Following one named person's trades** without a test.

## What Valedia has that the course does not

A sealed test period and a written prediction before every test; costs in every backtest; a count of every
attempt; an order ledger that sends each order at most once and reconciles with the broker; a tax-lot ledger
with wash sales; a guard that keeps keys off the screen, the logs and the journal; blind verification of every
change that can move money.
