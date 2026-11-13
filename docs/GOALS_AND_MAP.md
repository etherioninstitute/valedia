# Valedia Trader: her goals, the map of the code, and how far each chain runs end to end

Written 2026-11-13 at the owner's request. Status words: **works** (runs today and has been checked),
**built** (code exists and is checked, not yet running on its own), **in check** (built, blind check not
finished), **being built**, **not built**. The detailed list of open work is `docs/ROADMAP.md`; numbers in
brackets point there.

## Her goals

| # | Goal | Where it stands |
| --- | --- | --- |
| 1 | **Make money after costs, for the risk taken.** Nothing counts until it has passed a test on data it had not seen | 26 ideas tested; 24 failed; 2 passed their backtest and sealed test. Paper record: 3 days. Not proven |
| 2 | **Trade every market she can reach:** stocks and funds (Webull Cash account), prediction markets (Webull Events account), crypto later | Stocks and funds: paper works, and the broker path ran end to end in Webull's paper Cash account on 2026-11-13 (two orders sent, filled, reconciled). Prediction markets: Webull's event contracts are Kalshi's markets under Kalshi's tickers; event orders are built and ran in the paper Events account (4.7); no strategy has passed, settlement is not built |
| 3 | **Run on her own:** on a schedule, without a person, and stop herself when a limit is hit | Five jobs run on their own: the weekday paper step, the weekly research and review, the order-book collector (kept alive, restarts after a reboot), the hourly pick-list collection, and the paper order step against Webull's test environment each weekday morning |
| 4 | **Keep learning (self-learning):** propose ideas, test them, remember every result, review her own trades and forecasts, and change only what the evidence supports | Weekly research and review work. Every idea and attempt is remembered. Daily self-review (2.2), a check on the reviewer (2.5) and lessons by idea family (2.3) are not built |
| 5 | **Use intelligence, weighted by track record:** Nimble v3, the local Qwen models, the Hermes bots, MiroFish, price-forecast models, sportsbook lines, public pick lists. Each is scored against a no-skill baseline and earns weight only by beating it | The tournament works. So far no forecaster beats its baseline (Nimble v3, Qwen 8B, Kronos all failed). Sportsbook lines: tested on 1,407 games and failed, Kalshi's price is as good as the books' (1.18). Pick lists: collecting works; the test is locked and its grading code is in check (1.19). Order-book loop with Nimble v3: collecting since 2026-11-13 (1.16). MiroFish: no completed run (1.4) |
| 6 | **Have the best data:** prices, order books, odds, pick lists, weather; checked against a second source; never from Webull | Daily prices, Kalshi history, weather archive, sportsbook odds (credit ledger, 16,740 credits left this month) and pick lists work. The order-book collector runs (accepted after nine blind checks). A second price source is not built (5.2) |
| 7 | **Be honest with herself:** a written prediction before every test, a sealed test period, every failure written down | Works, and is the rule for every new idea |
| 8 | **Stay safe:** risk limits, a kill switch only a person resets, each order sent at most once, keys that cannot leak, real money only on the owner's word | Risk gate, kill switch, order ledger, key guard: all checked. Limits for stale data, connection errors and a daily order cap not built (5.7). No independent reviewer from another model family yet (5.4) |
| 9 | **Know the rules and the tax:** tax lots, wash sales, platform rules, before each order | Tax-lot ledger and rules reference work; tax notes on every paper order. Platform rule checks before orders (3.5) and tax-aware choices (3.7) not built |
| 10 | **Be ready for the Webull account:** the order path proven in the test environment, a paper record, a written checklist, then one small live order the owner confirms | Adapter and order step accepted for test use; first fills, fees and a clean reconcile seen in the paper account on 2026-11-13; seven before-live repairs built (settled cash only, fresh prices for held funds, a last look before sending among them), the last five small ones in check; checklist numbers not agreed (4.9); live not wired on purpose |
| 11 | **Let the local models carry what they can:** any job a local model can do and that can be checked | The local 27B model has a working seat (extraction, parsing, name matching). Nimble v3 answers typed questions inside the trader |
| 12 | **Be visible to the owner:** alerts and a daily summary on the phone, one page showing everything | Day summary on disk works. Phone alerts (4.5) and the one page (5.3) not built |

## The map of the code

`valedia_trader/` (more than 3,100 tests passing on the last full run, 2026-11-13):

| Folder | What lives there | Key files |
| --- | --- | --- |
| `research_loop/` | Finding and testing ideas, paper trading, the kill switch, the experiments | `strategy.py` (rule library), `gauntlet.py` (sealed tests), `ledger.py` (every idea and attempt), `research.py` (the proposing agents), `revision.py`, `paper.py` (paper trader and journal), `risk_gate.py` (kill switch), `critic.py` (weekly review), `tournament.py` (forecaster scoring), `price_models.py`, `weather_markets.py`, `orderbook_loop.py` (collector, running), `sports_lines.py` (tested, failed; the code stays), `pick_grading.py` (grading of pick lists, in check), `cli.py` (the commands) |
| `execution_and_risk/` | Everything between a decision and the broker | `orders.py` (order ledger, checked), `order_step.py` (decisions to broker orders, accepted for test use), `event_practice.py` (one capped event-contract order through the ledger, test environment only), `tax.py` (tax lots, checked), `buffering.py` |
| `venues/` | The outside world | `webull.py` (orders and account reads only, stocks and event contracts, accepted for test use), `webull_events.py` (Webull's event-contract lists, read-only), `kalshi_data.py` and `kalshi_history.py` (public data, no key), `odds_api.py` (sportsbook odds) |
| `data_and_fees/` | Data stores and forecast workers | `prices.py`, `ohlc.py`, `weather_archive.py`, `venue_registry.py` (fees and schedules), `timesfm_forecaster.py`, `kronos_worker.py`, `pick_lists.py` (collects public pick lists through Hermes' Reddit skill; checked) |
| `agents/` | The models around the trader | `panel.py` (deciders and vetters, pooled by track record, shadow mode), `desk.py` (research desk of three Hermes bots), `team.py` (the list of every bot), `mirofish_models.py`, `darwinian.py`, `meta_n.py`, `harness2.py`, `autoresearch.py` |
| `backtest/` | Backtests with costs and no look-ahead | `engine.py`, `walkforward.py` |
| `quant_engine/` | The fifty formulas (probability to microstructure) the rest draws on | `level1_...` to `level10_...`, `wiring.py` (where each is used) |
| `core/` | Plumbing | `secrets.py` (key loading and the guard on screen, logs and journal), `events.py`, `legacy.py` (older flat modules under `src/`) |

Outside the package: `config/` (the owner's risk limits, panel mode, tax rules), `docs/` (roadmap, rules and
tax, Webull guide, research write-ups, designs), `journal/` (paper record), `memory/` (idea ledger, tournament
record, panel record), `tools/` (repo-map generator, MiroFish tools), `~/Library/LaunchAgents/` (two schedules).

## The chains, end to end

**A. Stocks and funds, every trading day**

| Step | Code | Status |
| --- | --- | --- |
| 1. Prices refreshed after the close | `prices.py` | works (scheduled, weekdays) |
| 2. Each passed strategy sets its target holdings | `strategy.py` | works (one strategy trading, the second joins at the next step) |
| 3. Every order through the risk gate | `risk_gate.py` | works |
| 4. The panel is consulted (shadow: recorded, not obeyed) | `panel.py` | works |
| 5. Paper book filled at the next session; journal and tax notes | `paper.py`, `tax.py` | works |
| 6. Next morning: one netted order per fund for the broker | `order_step.py` | works in the paper account (first run 2026-11-13); run by hand, not scheduled |
| 7. Order written down, sent once, reconciled | `orders.py` | built, checked |
| 8. Sent to Webull's paper account | `webull.py` | works: 146 XLE and 43 XLK sent and filled on 2026-11-13 |
| 9. Fills back into the record, day summary | `order_step.py` | works: each fill journaled once on the second run |
| 10. Alerts and summary on the phone | | not built (4.5) |

So today the chain runs on its own from step 1 to step 5. Steps 6 to 9 ran for the first time on 2026-11-13
in the paper account, by hand; a sale, a partial fill and a clean week are still to be seen. Step 10 does not
exist.

**B. Prediction markets**

| Step | Code | Status |
| --- | --- | --- |
| 1. Market data: Kalshi history, order books, odds, weather, pick lists | `kalshi_data.py`, `kalshi_history.py`, `orderbook_loop.py`, `odds_api.py`, `weather_archive.py`, `pick_lists.py` | all work; the order-book collector has been running since 2026-11-13 |
| 2. An idea with a written prediction, tested once | `weather_markets.py`, `sports_lines.py`, the order-book loop | weather: tested, failed. Sports lines: tested, failed. Order-book loop: locked, collecting (development 14 to 18 November, then a 14-day test). Pick lists: locked, grading in check |
| 3. A passed idea goes to paper trading | | nothing has passed |
| 4. Orders through the Events account | `webull.py`, `event_practice.py` | built for the paper account and run there (one contract bought and sold through the order ledger); settlement, sizing and risk limits for event contracts not built (4.7) |

This chain stops at step 2 until an idea passes; the order path beyond it is ready in paper form.

**C. Learning**

| Step | Code | Status |
| --- | --- | --- |
| 1. Agents propose ideas from different angles | `research.py`, `desk.py` | works (weekly) |
| 2. Each idea through the gauntlet, the result remembered | `gauntlet.py`, `ledger.py` | works |
| 3. A failed idea revised a limited number of times | `revision.py` | works |
| 4. Forecasters asked and scored against baselines | `tournament.py` | works; daily forward questions not scheduled (1.2) |
| 5. A forecaster that beats its baseline earns weight | `panel.py` | not built (1.5); none has beaten it |
| 6. A weekly review of what happened | `critic.py` | works (scheduled) |
| 7. Daily review of each closed trade and settled forecast, lessons fed back | | not built (2.2, 2.3) |
| 8. A live strategy halted when it decays | | not built (2.7) |

## What still stands between today and "works end to end"

1. The order step runs daily against the paper account for a clean week, including a sale and a partial fill (done so far: accepted for test use, first fills, fees and a clean reconcile seen on 2026-11-13) (4.2, 4.3, 4.8).
2. A paper record through the broker long enough to trust, by numbers the owner agrees (4.6, 4.9).
3. Phone alerts, the daily summary and the kill switch tested against the broker (4.5).
4. The missing limits: stale data, connection errors, the daily order cap (5.7), and platform rules before each order (3.5).
5. The daily self-review and the decay monitor, so learning closes its loop every day and not only weekly (2.2, 2.7).
6. One prediction-market idea that passes (two are being collected for: the order-book loop and the pick lists), and settlement of event contracts in the order path (1.16, 1.19, 4.7).
7. A reviewer from another model family, or the owner's waiver, before anything touches real money (5.4).
