# What is left to build

Written 2026-11-12 at the owner's request. The goal: Valedia is the smartest and most profitable autonomous
trader it can honestly be, across every market it can reach, learning from every decision, inside each
platform's rules and the tax law. One rule does not bend: nothing trades real money until it has passed
tests on data it had not seen, tracked in paper trading, and the owner has said go.

Status words: **done**, **started**, **not started**, **waiting on the owner**.

## 1. Find what actually makes money

| # | Item | Status |
| --- | --- | --- |
| 1.1 | Forecast tournament: every forecaster (Nimble v3, local models, Hermes bots) asked the same questions and scored against a no-intelligence baseline | started: built; first blind batch (293 fund questions): Nimble v3 0.444, Qwen 8B 0.276, base rate 0.243, so both worse as they stand |
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
| 1.12 | Kronos, an open price-forecast model, as a tournament forecaster (from the owner's notes) | not started |
| 1.13 | Kalshi weather markets against official forecast releases, tested on free history first (from the owner's notes) | not started |
| 1.14 | Closing-line value as a fast skill measure for prediction-market forecasts (from the owner's notes) | not started |
| 1.15 | Net the strategies' orders in one fund before placing them: in a 40-day replay about 45% of realised losses were deferred as wash sales, much of it one strategy trimming a fund the other holds or buys | not started |

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
| 4.1 | Webull API keys and the API application | waiting on the owner |
| 4.2 | Whole order path proven in Webull's test environment, then one small live order the owner confirms | not started |
| 4.3 | Daily reconciliation of the book with the broker: positions, cash, real fills, partial fills, refusals | not started |
| 4.4 | Splits, dividends, half-days and holidays handled | started: the exchange calendar is in; corporate actions are not |
| 4.5 | Kill switch tested against the venue; alerts and a daily summary to the owner's phone | not started (Signal waits on the owner's login job) |
| 4.6 | A longer paper record: the hold strategy joins at the next paper step | started |
| 4.7 | A second venue for prediction markets (Kalshi's API, or event contracts through Webull) | not started |
| 4.8 | Order handling for live trading: submit once, a timeout is unknown until reconciled, reconcile on restart, live orders approved by ticket number (from the owner's notes) | not started |

## 5. Keeping it safe and visible

| # | Item | Status |
| --- | --- | --- |
| 5.1 | Backups of the idea ledger, paper journal and tournament file. They are the trader's memory and are not in git | not started |
| 5.2 | A second price source to cross-check the first; stale and missing data flagged | not started |
| 5.3 | One page for the owner: strategies, paper results against holding, forecaster leaderboard, tax so far, limits | not started |
| 5.4 | An independent reviewer from another model family for code that can move money (needs the Codex or Grok command-line tool on this Mac) | waiting on the owner |
| 5.5 | The 2% daily stop is still the guide's example number | waiting on the owner |
