# The research loop

One cycle, repeated: research, code, backtest, paper trade, post-mortem, remember. It is built from
the "Self-Improving AI Trading Research Loop" guide, and its rules live in code, not in prompts.

Paper trading only. Nothing in `valedia_trader/research_loop` can reach a live-money account.

## Commands

```bash
python -m valedia_trader.research_loop init        # folders, config/risk.json, ledger; records what was already tested
python -m valedia_trader.research_loop status      # the ledger at a glance
python -m valedia_trader.research_loop research    # agents propose ideas (they see the development period only)
python -m valedia_trader.research_loop backtest 16 # development test of idea 16; counted in the ledger
python -m valedia_trader.research_loop revise 16   # repair a failed idea within the revision cap
python -m valedia_trader.research_loop seal 16     # its single sealed test
python -m valedia_trader.research_loop paper-step --refresh   # paper trading: every trading day not yet processed
python -m valedia_trader.research_loop critic      # weekly post-mortem
python -m valedia_trader.research_loop cycle --refresh        # all of the above, in order, once
```

## Schedule

A launchd job, `com.valedia.research-loop.weekly`, runs `cycle --refresh --critic --agent-timeout 1800` every Sunday at
18:00 local time (at the next wake if the Mac is asleep). The job file is `tools/launchd/com.valedia.research-loop.weekly.plist`;
its header has the install, remove and run-now commands. Output goes to `~/Library/Logs/valedia-research-loop.log`. If the SSD
is not mounted the job says so in that log and does nothing. `--agent-timeout` is the one knob for how long a research agent
may take (30 minutes here, against about 30 seconds typical); an agent that does not answer is noted in its memo and the cycle
goes on. Each run adds at most six ideas, and every backtest it makes is counted.

## Where things are

| Folder | Holds |
| --- | --- |
| `research/` | one memo per agent per day, plus the skeptic's |
| `strategies/` | one file per strategy, version inside (`.json` from the rule library, or `.py`) |
| `backtests/` | locked predictions (`*-prediction.md`, read-only), test reports, revision histories |
| `journal/` | every paper decision with its reasoning (`YYYY-MM-DD.jsonl`), weekly reviews |
| `memory/` | `ledger.db` (the hypothesis ledger; not in git, back it up) and `edit_library.json` |
| `config/risk.json` | the kill switch's limits. The numbers are the guide's examples: set your own |
| `artifacts/data/` | price history (the guide's `data/`) |

## The rules and where each is enforced

| Rule | Enforced by |
| --- | --- |
| Check the ledger before proposing | `ledger.propose` refuses an idea that matches a failed one on the same market unless the agent states conditions changed |
| Agents work separately | `research.run_research` asks every proposer before any memo is written; each prompt holds one angle only |
| Prediction first, never edited | `ledger.register_prediction` refuses once an attempt exists; database triggers refuse UPDATE and DELETE; the file is read-only and its hash is checked before every test |
| Sealed period, used once | `Gauntlet` hands out only the first 75% of history; `sealed_uses` has the hypothesis as primary key; the use is claimed before the data is touched |
| Walk-forward | results are read on five consecutive out-of-sample windows; with variants, each window trades the one that was best before it |
| Count every variation | every backtest, probe and variant is a row in `attempts`; the Deflated Sharpe uses the count for the idea and for the market, whichever is stricter, against 0.95 |
| Breaker | twice the trading costs, fills a day late, the market's five worst 63-day stretches |
| Cap rewrites at 3 to 5 | `Criteria.max_revisions` (default 5, must be 3 to 5); a changed strategy must carry a new version or the test refuses to run |
| Kill switch in code | `risk_gate.check_order` runs before every order in `PaperTrader.step`; a missing or broken limits file blocks everything; the critic re-runs the check on every placed order |
| Log every decision | `Journal` writes timestamp, strategy and version, market conditions, decision and reasoning, including "no trade" |
| Keep every failure | hypotheses and attempts cannot be deleted (database triggers) |

## How an idea moves

`untested` -> development test -> (`failed`, may be revised within the cap) or pass -> sealed test once ->
`passed backtest only` or `failed` -> paper trading -> the critic sets `passed paper` after at least
20 trading days that are not materially below the backtest, or `paused`.

## Revisions

A failed idea is revised by `revision.RevisionEngine`. Each round is a Harness2 step: propose edits
on two dimensions (INV, how positions are managed; DOM, the signal and where it is applied), probe
each on the development period, and compose. KEEP_BASE is a valid outcome. The versions form a
Meta-N archive: a rewrite that scores below its parent is kept on record but never built on, and the
strategy file ends on the best version. Every probe is a counted variation, so revising raises the
bar it has to clear. What worked and what did not goes to `memory/edit_library.json` and orders the
proposals for later ideas.

## Agents

| Role | Sees | Status |
| --- | --- | --- |
| Price Analyst | returns, volatility, distance from highs, volume trend per symbol | runs |
| Macro Watcher | cross-asset ratios (curve, credit, sectors, commodities) | runs; no economic data feed is connected |
| Fundamentals Reader | earnings and filings | skipped: no source is connected and the universe is funds and crypto |
| Skeptic | the accepted ideas and the ledger's failures | runs last |

Agents answer through a Hermes profile (`--backend hermes:<profile>`) or a local model
(`--backend chat:<model>@<base-url>`). They may only return strategies built from the rule library
(`strategy.SCORES`, `strategy.SIZING`), so nothing a model writes is executed.

## First round, 2026-11-10

The ledger was seeded with the 15 ideas the earlier walk-forward had tested (all failed the 0.95
gate; their holdout was looked at repeatedly, so they have no sealed test left). Two agents then
proposed six ideas from data up to 2023-02-17. All six failed the development test on the Deflated
Sharpe:

| # | Idea | Sharpe | Deflated Sharpe |
| --- | --- | --- | --- |
| 16 | Long crypto on a 50/200-day average cross | 1.04 | 0.888 |
| 17 | Equity ETF 60-day breakouts | 0.47 | 0.290 |
| 18 | Bond ETF 10-day reversion | -0.11 | 0.004 |
| 19 | Energy and commodities 12-month momentum | 0.27 | 0.077 |
| 20 | Long-bond and credit reversion | -0.22 | 0.001 |
| 21 | Tech and financials 50/200-day cross | 0.77 | 0.485 |

Ideas 16 and 21 were revised. Idea 16: three rounds, no edit supported by the evidence. Idea 21: one
edit adopted (hold longer), Sharpe 0.90, Deflated Sharpe 0.618, still failed. No idea reached the
sealed period, so nothing is in paper trading and the sealed period is unspent for all six.

## Not done

- No strategy has passed, so there is no paper-trading record. The paper trader and critic were
  checked by replaying 45 real trading days in a sandbox with a strategy marked as passed there only.
- `AlpacaPaperVenue` is tested against a fake client. It has not been run against Alpaca: the key on
  this machine is not a paper key, and the venue refuses anything else.
- The research agents' prose often names filters the rule library cannot express (for example a
  correlation threshold). The strategy that gets tested is the library version, which is simpler.
- Paper trading is a simulated book. A weekly run steps it through each trading day it missed, in order, which gives
  the same book as running daily; a broker mirror cannot do that and would need a weekday run.
