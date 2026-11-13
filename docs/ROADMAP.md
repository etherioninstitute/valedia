# What is left to build

Written 2026-11-12 at the owner's request. The goal: Valedia is the smartest and most profitable autonomous
trader it can honestly be, across every market it can reach, learning from every decision, inside each
platform's rules and the tax law. One rule does not bend: nothing trades real money until it has passed
tests on data it had not seen, tracked in paper trading, and the owner has said go.

Status words: **done**, **started**, **not started**, **waiting on the owner**.

## 1. Find what actually makes money

| # | Item | Status |
| --- | --- | --- |
| 1.1 | Forecast tournament: every forecaster (Nimble v3, local models, Hermes bots) asked the same questions and scored against a no-intelligence baseline | started: built and blind-verified; 1,793 blind questions on funds, stocks and crypto scored. Nimble v3 and Qwen 8B both score worse than the base rate, and neither ranks outcomes better than chance from price facts (a first-batch hint for Qwen 8B failed its locked replication) |
| 1.2 | Forward questions every trading day on funds, stocks and crypto, settled and scored automatically | not started (the command exists; it is not scheduled) |
| 1.3 | Prediction-market questions in the tournament, scored against the market's own price (Kalshi and Polymarket history are free to read) | not started |
| 1.4 | MiroFish swarm and the web-reading Scout as tournament forecasters | not started (no swarm run has completed yet) |
| 1.5 | A forecaster that beats its baseline earns weight in trading decisions; one that does not, gets none | not started |
| 1.6 | Stock data that includes companies that died, with index membership by date. Without it a stock-picking backtest flatters itself | waiting on the owner (a paid source: about 20 to 55 dollars a month) |
| 1.7 | Crypto history from the exchanges, dead coins included, and the top coins by date | not started (free sources verified) |
| 1.8 | Rule library beyond one score per fund: rank many assets and hold the best, rules that read outside data (forecasts, yields, market prices), short positions in stocks | not started |
| 1.9 | New idea families through the gate: carry, option premium, earnings and other events, pairs, intraday | not started (each needs its data first) |
| 1.10 | Split of capital between passed strategies, tested out of sample (today: equal shares) | not started |
| 1.11 | Costs by venue: Webull crypto spreads, Kalshi's fee formula, stock borrow fees, slippage by liquidity | not started |
| 1.12 | Kronos, an open price-forecast model, as a tournament forecaster (from the owner's notes) | done 2026-11-13: tested on 24,126 questions dated after its training and failed. No ranking skill in any of six groups (funds, stocks, crypto; one and five days), and far worse than the base rate as it stands (`docs/research/2026-11-13-kronos-result.md`). The forecaster stays in the tournament; its price downloader was repaired and blind-verified the same day |
| 1.13 | Kalshi weather markets against official forecast releases, tested on free history first (from the owner's notes) | done 2026-11-13: tested on all 9,910 settled New York markets and failed. The market's price beats the public forecast; the trading rule lost 4.3 cents a contract in the test period (`docs/research/2026-11-13-kalshi-weather-result.md`). The history reader and evaluation stay for 1.3 and 1.14. The owner's course PDFs describe the same idea; their own two-week replay lost 23%. Their same-day "lock" rule and four other cities were not tested here (`docs/research/2026-11-13-course-pdfs-analysis.md`) |
| 1.14 | Closing-line value as a fast skill measure for prediction-market forecasts (from the owner's notes) | not started |
| 1.16 | The order-book loop from a newsletter the owner follows, with Nimble v3 in the decision seat: snapshots of prediction-market order books, six typed questions every five minutes, paper quoting rules; paper only | started 2026-11-13 (owner's go): prediction locked (`docs/research/2026-11-13-orderbook-loop-prediction.md`); the collector is being built; 5 days of development data, then a 14-day test scored once |
| 1.15 | Net the strategies' orders in one fund before placing them: in a 40-day replay about 45% of realised losses were deferred as wash sales, much of it one strategy trimming a fund the other holds or buys | not started |
| 1.17 | Copy-trading signals from the owner's course PDFs: company insiders' open-market purchases and politicians' disclosed trades. An idea to test with the prediction written first, not a feature to build. Insider filings are free from the SEC; politicians may file up to 45 days after a trade | not started (a fair test needs 1.6, stock data with dead companies) |
| 1.18 | Sports lines against prediction-market prices (owner's request 2026-11-13): sportsbook odds from The Odds API (the owner's paid plan, 20,000 credits a month, plus a free key), with the bookmaker's margin removed, compared with the Kalshi price for the same game; tested on history first with the prediction written before any scoring, then collected live inside the credit budget | started 2026-11-13: prediction locked (`docs/research/2026-11-13-sports-lines-prediction.md`: five leagues, development Jan to Mar 2026, test Sep to Nov 2026 scored once, history spend capped at 9,000 credits); the history fetcher, pairing and evaluation are being built on the existing odds client; the local 27B model resolves team names code cannot match, each proposal checked. Both keys are in the secrets file and answer (20,000 and 500 credits, none used) |
| 1.19 | Public pick lists as a forecaster (owner's request 2026-11-13): the daily pick-of-the-day lists a Reddit bot compiles, mined and scored like any other forecaster (against the closing line and the result) before they earn any weight | started 2026-11-13: Hermes' own Reddit skill reads Reddit without a key (slowly) or with an app's credentials; the store, the parser (the local 27B model, every field checked against the comment) and a wiki written from the store for Valedia are being built. Grading against closing lines and results follows the sports-lines work. No pick earns weight before it is graded |

## 2. Get smarter every day

| # | Item | Status |
| --- | --- | --- |
| 2.1 | One memory of every decision and how it turned out: orders, forecasts, ideas, refusals, in one place the agents can query | started: the paper journal, the idea ledger and the tournament each keep their own; they are not joined |
| 2.2 | Daily self-review: each closed trade and each resolved forecast compared with what was expected, lessons written back | not started (a weekly critic exists and has not yet run on real days) |
| 2.3 | The research agents read those lessons before proposing, so a family that keeps failing stops being proposed | started: they see the ledger's failures; family-level lessons are not summarised for them |
| 2.4 | Schedules: weekday paper step, daily tournament, weekly research and review | started: weekly loaded; the weekday job waits on two commands from the owner |
| 2.5 | A check on the critic itself: do changes made after a review actually help? | not started |
| 2.6 | New models tried automatically: add a model to the roster and the tournament ranks it | started: the roster is read; nothing adds models on its own |
| 2.7 | Live decay monitor: halt a strategy when its live Sharpe falls under half the backtest's or its fall exceeds 1.5 times the backtest's (from the owner's notes) | not started |

## 3. Rules and tax

| # | Item | Status |
| --- | --- | --- |
| 3.1 | Tax-lot ledger: gains by day, month and year, short and long term, wash sales found and carried into the new lot | done 2026-11-12 (`execution_and_risk/tax.py`, blind-verified); its four legal parameters are still marked unverified |
| 3.2 | Sourced reference of tax and platform rules, each with its link and date | done 2026-11-12 (`docs/RULES_AND_TAX.md`); open questions are named in it |
| 3.3 | Every order checked for its tax effect before it is placed, and wash sales avoided or flagged by a policy the owner sets | started 2026-11-13: every paper order and day summary carries a tax note (blind-verified); notes only, no avoidance policy yet; notes do not yet see other orders decided the same day |
| 3.4 | Valedia answers tax questions on request from the ledger: today, this month, this year, what this trade would do | not started |
| 3.5 | Platform rule checks before each order: day-trade counts, settlement in cash accounts, order types and hours, rate limits, position limits | not started |
| 3.6 | Weekly re-read of the sources; any change in a rule is shown to the owner before the trader uses it | not started |
| 3.7 | Tax-aware choices: wait for long-term treatment when the signal is weak, harvest a loss into a similar but not identical fund | not started (needs 3.1 to 3.3) |
| 3.8 | Year-end reconciliation against the broker's own tax forms | not started |

Nothing here is tax or legal advice; the ledger computes from the rules file and names any rule not yet verified.

## 4. Ready for the Webull account

| # | Item | Status |
| --- | --- | --- |
| 4.1 | Webull API keys and the API application | done 2026-11-13 for the paper side: the owner's paper keys are in and working. The live keys exist and stay out of the secrets file until the paper run has proven the order path |
| 4.2 | Whole order path proven in Webull's test environment, then one small live order the owner confirms | started 2026-11-13: first contact made and practice orders placed and cancelled in the paper accounts; what was seen is in `docs/WEBULL.md` and is going into the adapter. Still to see: a fill, fees, fractional steps (waits for the market's open), and the live side. To observe there, because Webull does not document them: how it answers for an order that does not exist; the quantity step for stocks and the price steps; whether one holding is ever split over several rows; how far its open-order and position answers lag behind fills; whether any answer repeats a key |
| 4.3 | Daily reconciliation of the book with the broker: positions, cash, real fills, partial fills, refusals | started 2026-11-13: the order ledger (`execution_and_risk/orders.py`, blind-verified by five fresh verifiers) checks every open order against the broker, records each fill and late fee once, and compares positions. Not yet connected to the trader's book; cash is not compared yet |
| 4.4 | Splits, dividends, half-days and holidays handled | started: the exchange calendar is in; corporate actions are not |
| 4.5 | Kill switch tested against the venue; alerts and a daily summary to the owner's phone | not started (Signal waits on the owner's login job) |
| 4.6 | A longer paper record: the hold strategy joins at the next paper step | started |
| 4.7 | Prediction-market orders through the owner's Webull Events account (the owner's choice 2026-11-13; stocks and funds go through the Cash account) | not started: the adapter reads the Events account and refuses event orders; Webull documents event orders as limit orders with a yes or no outcome on tickers of Kalshi's shape, in the Events Cash account; it does not name the exchange, so the link to Kalshi's markets has to be confirmed in the test environment before anything is built on it. No prediction-market strategy has passed a test yet |
| 4.8 | Order handling for live trading: submit once, a timeout is unknown until reconciled, reconcile on restart, live orders approved by ticket number (from the owner's notes) | started 2026-11-13: the order ledger is built and accepted for test use (an order is sent at most once however many copies of the program run; an unknown outcome blocks further orders until the broker confirms or a person settles it; an order that turns up after being declared lost stays flagged until a person closes it). The Webull adapter (order status, open orders, cash, cancel-all, exact sending, the long-only check) was accepted for test use the same day after seven fresh verifications. Its next version (account by class, refusals told apart from unknown outcomes, sessions, pacing under Webull's call limits, fresh holdings at every sale) was accepted for test use on 2026-11-13 after two more blind checks and a run against the paper account. The piece that turns the strategies' decisions into ledger orders (one netted order per fund a day, inside market hours, the risk gate run again) is designed (`docs/specs/2026-11-13-order-step-design.md`) and being built for the test environment only. Not built: approval by ticket number (at approval the price is checked again and the risk gate runs again; a request nobody answers expires), and an expiry for an order that was decided and never sent. Not cleared for real money until a reviewer from another model family has looked or the owner waives that (5.4) |
| 4.9 | A written go-live checklist with numbers, agreed before the live keys go in: how long a paper record, how many filled paper orders reconciled without a difference, how many checked by hand (from the owner's course PDFs) | not started (the numbers are the owner's to set) |

## 5. Keeping it safe and visible

| # | Item | Status |
| --- | --- | --- |
| 5.1 | Backups of the idea ledger, paper journal and tournament file. They are the trader's memory and are not in git | not started |
| 5.2 | A second price source to cross-check the first; stale and missing data flagged | not started |
| 5.3 | One page for the owner: strategies, paper results against holding, forecaster leaderboard, tax so far, limits | not started |
| 5.4 | An independent reviewer from another model family for code that can move money (needs the Codex or Grok command-line tool on this Mac) | waiting on the owner |
| 5.5 | The 2% daily stop is still the guide's example number | waiting on the owner |
| 5.6 | Keys can never reach a file or the screen: the broker adapter passes on only text of a harmless shape and no raw broker answer; a guard on the journal and on everything printed replaces any piece of any loaded key | done 2026-11-13 (owner's request), both blind-verified: the adapter's gate and last check, and the guard in `core/secrets.py` on the screen, the logs and the journal. Programs the trader starts (the Hermes panel members, the desk, the forecast workers) no longer inherit the trading keys, and no test can open the real secrets file. A follow-up is in progress for secrets made of separate words (a wallet recovery phrase) and two more launch sites |
| 5.7 | Risk limits the newsletter's design lists and the trader lacks: data too old, repeated connection errors, a slow model answer, a cap on orders per day (also in the owner's course PDFs; the number is the owner's to set); a kill switch that closes positions and alerts the owner's phone | not started |
| 5.8 | Webull is used for orders and account reads only. Prices and all other market data come from elsewhere (stocks and funds: the price source in use; prediction markets: Kalshi's public data; sports lines: The Odds API), because Webull limits its calls (owner's decision 2026-11-13) | done as a rule: nothing in the trader reads market data from Webull, and the order step's design takes its quotes from the price source |
