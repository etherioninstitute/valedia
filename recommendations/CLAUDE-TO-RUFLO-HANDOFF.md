# Claude Code → Ruflo handoff (Valedia)
Updated: 2026-09-13 PT (seed — Claude Code must fill/expand from its live session)

## How to use
1. Burton approves project MCP `claude-flow` via `/mcp` in this Code session (cwd = this workspace).
2. Claude Code expands this file with real paths, gaps, and priorities from its own transcript + codebase.
3. Ruflo reads this file + `RUFLO-MASTER-GOAL.md` + `RUFLO-STATUS.md` and executes **coding** milestones only.
4. After each material step, Ruflo updates `RUFLO-STATUS.md`. Claude Code can re-read that for check-ins.

## North star (profit, fenced)
**Goal:** maximize long-run profit **through a better Valedia system** — reliability, edge measurement, gates, execution quality.

**Ruflo does NOT place trades.** No `--live`, no Kalshi/Hyperliquid/Alpaca/Polymarket orders, no key/credential changes for venues. Valedia runtime remains sole live risk authority. Ruflo ships code, tests, audits, and docs so Valedia can earn under Burton’s locked rules.

### Locked operating rules Ruflo must respect
- Equity live-off until equity-class OOS Hit% under filter `votes≥8 + MOM + 5d_trend` beats ~51%.
- MiroFish ≤10% until accuracy ledger >55% over 2–4 weeks.
- Freqtrade/OpenBB/etc. = execution/research only; Valedia sole live risk authority.
- Fund rules: 4%/trade, 10%/day, fee-gate, ERE; Signal money pings = Invested / Profit only.
- Do not copy `kalshi-agent` code (UNLICENSED). Clean-room / MIT-BSD only.
- Fortress stealth browser parked on M1 Colima (qemu crash). Use native Playwright if browsing needed.
- Ponytail + **live-path proof** (cron / `master_allocator` callers) before claiming wiring done — not `/audit` alone.

## Orient Ruflo (workspace map)
| What | Path / note |
|---|---|
| Live Valedia root | `~/.openclaw/workspace` (this cwd) |
| Allocator / live path | `scripts/integrations/master_allocator.py` (and crons under `scripts/`) |
| Status board | `recommendations/RUFLO-STATUS.md` |
| Master goal seed | `recommendations/RUFLO-MASTER-GOAL.md` |
| This handoff | `recommendations/CLAUDE-TO-RUFLO-HANDOFF.md` |
| Vendor refs only | `vendors/` (HyperGrok MIT skills OK with attribution; kalshi-agent = do not merge) |

## Known issues — status after Claude's 2026-09-12/13 session (evidence-bound)
- **RESOLVED — live path now records.** True live Kalshi authority = `master_allocator.py` (via `allocator_run.py` → `run(dry_run=False)`, cron every 4h), NOT `kalshi_executor.run`. `record_entry` is now called on the live path: `master_allocator.py:473` (Kalshi EXECUTED). Verified structurally via codebase-memory `trace_path(record_entry, inbound)` → `master_allocator.execute_allocations`.
- **CONFIRMED — `kalshi_executor.run` is inert/legacy** (no cron caller); the session's earlier gate/advisory wiring there does NOT run live. Live logic lives in `master_allocator`. Do not re-wire `kalshi_executor.run`; treat it as dead unless a caller is added.
- **Daily messages mapped:** `kalshi_alert.py` = 7am morning brief (delivers; balances real, its "expected profit" is modeled EV). `pnl_run.py` = 11pm P&L (now SENDS a real broker-sourced message via `signal_send`). `portfolio_monitor.py` = 3pm EOD — still print-only (never sent; confirm intentional or wire).

## A. Current Valedia state (evidence-bound, from this session + live tree)
- **Live Kalshi trader:** `scripts/integrations/master_allocator.py` — now uses the single sources: `record_entry` (`:473`), loss-based `should_trade_category` gate (`:391`), `dynamic_per_trade_cap` sizing (`:296`), fees via `fee_engine`. `kalshi_executor.run` is the inert legacy path.
- **Real track record** = `models/forward_test_ledger.jsonl` (fee-inclusive, `forward_test_ledger.py`). Feeds the Kelly cap (`valedia_config._track_record_mult`) + confidence (`unified_brain.get_confidence_multiplier`). Currently 38 backfilled + now live-recording; **26.3% win / −$12.62 net (thin, UNPROVEN)** → cap honestly throttled to ~2.4%.
- **Category gate** (`forward_test_ledger.should_trade_category`): clean-room on Valedia's own ledger; **fail-open** (errors never block), **net-loss based** (protects fat-tailed low-win winners), **never permanent** (45d recency window + manual `models/category_overrides.json`). Tests: `test_category_gate.py`.
- **Sizing:** half-Kelly + `dynamic_per_trade_cap` (regime × real track-record × Etherion, fused 15%/daily 20%) + Thorp concurrent-bet reduction `f/√N` + Bayesian shrink toward the category's real record (`kelly_sizing.py`; tests `test_concurrent_kelly.py`).
- **Fees unified:** `fee_engine.kalshi_fee_per_contract` (real nonlinear `0.07·p·(1−p)`) everywhere; kalshi-agent's flat 0.07 rejected. Drift detector `scripts/valedia_consistency_check.py` = 7 PASS.
- **Execution quality:** `execution_policy.py` (`build_execution_plan` taker/maker/no_trade + `MakerOrderManager` — persistent across cron, records on ACTUAL fill) wired into `master_allocator`; `market_state.py` book microstructure. `EXEC_DRY_RUN=False` (live maker on, but every order still passes intel→held→blacklist→category gate→dynamic cap→fuses).
- **MiroFish:** prep wall FIXED (3.5h→21min: `PREP_LLM_TIMEOUT=45`, `max_retries=0` on prep generators, keep-alive 30m); full pipeline runs end-to-end; signal-extraction path bug FIXED (`mirofish_runner.py:341`); RC3 event layer + **sign-anchor fix** (war→RISK_OFF) in `mirofish_signal_extractor.py` (tests `test_event_signs.py`); accuracy ledger cronned (`mirofish_ledger_run.py`, 8am). Current signal `mirofish_swarm`/49.1. 03:00 quiet run = pending LLM-rich validation.
- **codebase-memory indexes for audits:** `valedia-scripts` (1307 nodes, the trading code) + `mirofish-backend` (2398 nodes). **Do NOT use `valedia-workspace`** — it excludes `scripts/` (has none of the trading code; that gap is why the live-path miss wasn't caught structurally).
- **Known LIVE gaps (Ruflo backlog below).**

## B. Highest-leverage coding work for profit (ranked; ledger-truth / gates / edge / execution)
1. **`sports_engine.py` ledger-truth + fees + gate** — LIVE every 2h (`create_order` `:285`) but **no fee accounting** (`profit_if_win = n*(1-price)`, `:246`), writes its **own** `sports_log.json` (not the real ledger), **no** `should_trade_category`, and its PnL is fee-exclusive (`:326`). Also `execute_bets(dry_run)` can log a DRY_RUN bet as real (`:278`). Fix: fees→`fee_engine`, `record_entry`→real ledger, gate→`should_trade_category`, and never persist DRY_RUN as executed. *(A live trader flying blind on fees + no gate = biggest ledger-truth hole.)*
2. **Unify fusion weights** — two parallel brains: `signal_fusion.py:34 WEIGHTS` vs `unified_brain.py` inline scheme (`~:386`). Consolidate to one source (extend `valedia_config`) so the edge is consistent and drift-guarded.
3. **Per-strategy sizing** — the aggregate `dynamic_per_trade_cap` throttles crypto/sports for oil/weather's losses. Size each category by its **own** real settled record (from the ledger), so proven winners aren't starved. *(Direct profit lever once the ledger accumulates.)*
4. **Ledger completeness for crypto/equity** — `master_allocator` records only Kalshi; coinbase/alpaca fills use a notional placeholder price and are NOT recorded (`:435-451`). Capture real fill prices → `record_entry`, so non-Kalshi edge is measured too.
5. **`pnl_tracker.py` fee-inclusive** — `:73` mixes broker realized PnL with a fee-exclusive exposure fallback. Route through `fee_engine` / reconcile with the ledger so the daily P&L + track record are 100% fee-inclusive.
6. **MiroFish agent RICHNESS (persona-gen latency)** — runs complete end-to-end but agents fall back to **rule-based** (thin signal: ~26 actions, 1 asset) because concurrent persona/config LLM calls time out. **Measured 2026-09-13 (quiet GPU): 5 concurrent calls on `:11435` = 156–180s each** (single-call is 18s) — NUM_PARALLEL=4 splits the GPU ~9x. The 45s prep cap then forces rule-based. Fix is throughput, not timeout: cut persona `max_tokens` (currently 2000), match the backend's persona-gen concurrency to NUM_PARALLEL (don't fire 5+ at once), or use a smaller persona model. Also: validate RC3 event signs vs realized outcomes; `mirofish_runner.py` report_dir picks the FIRST `full_report.md` (should match the run's `sim_id`); grow the accuracy ledger so MiroFish earns >10% weight honestly. *(Low downside while capped at 10% — but this is the lever from thin→rich signal.)*
7. **`portfolio_monitor.py` (3pm)** — wire the Signal send or confirm it's intentionally log-only.
8. **Backtest-first harness** — any edge/threshold change must produce a fee-inclusive backtest (Monte-Carlo / forward) before it ships. Codify as a required step.

## C. Audit checklist Ruflo runs (HOLDS/FAILS, auditor mode = verify only, no "improvements")
- [ ] Live cron → `master_allocator` → `record_entry` writes to `forward_test_ledger.jsonl` (file:line + trace, not `/audit` alone)
- [ ] `kalshi_executor.run` confirmed inert (no live caller); no live logic depends on it
- [ ] `sports_engine` live path: fees, ledger, gate present (currently NOT — expected FAIL until B1)
- [ ] Dry-run / `EXEC_DRY_RUN` / `--live` flags cannot trip by accident; every order passes gate→cap→fuses
- [ ] Category / strategy kill gates read the REAL ledger; fail-open; never permanent
- [ ] Fee / Kelly / risk limits match locked rules (4%/trade→dynamic, 10-20% day, fee-gate, ERE)
- [ ] No proprietary third-party code merged (kalshi-agent UNLICENSED); MIT/BSD + attribution only
- [ ] Drift detector `valedia_consistency_check.py` = PASS after any change

## D. First Ruflo sprint (ONLY this until STATUS says done)
1. **Audit-verify (HOLDS/FAILS, file:line + live-path proof)** Claude's session wiring on the TRUE live path: `record_entry` (`master_allocator:473`), gate (`:391`), cap (`:296`), fees; and that `kalshi_executor.run` is inert. Confirm the drift detector still passes. *(The old "prove/fix record_entry" milestone is DONE — Ruflo's job is to independently verify it, not re-do it.)*
2. **First coding task — B1: `sports_engine.py` hardening** (clean-room, tests, ponytail, live-path proof): route fees through `fee_engine`, `record_entry` real Kalshi fills to the ledger, apply `should_trade_category`, and stop DRY_RUN bets from being logged as executed. **No `--live` change, no order placement, no key changes.**
3. Update `RUFLO-STATUS.md` with evidence (file:line, test output, detector result).
4. **Stop for Burton/Claude review** before B2+.

## E. Explicit non-goals
- Placing trades / flipping `--live` / touching venue API keys (Valedia runtime is sole live risk authority)
- Ruflo neural-trader / market-data plugins for money
- Merging UNLICENSED repos (`kalshi-agent`) into Valedia
- Re-wiring the inert `kalshi_executor.run` as if it were the live path

## Sign-off
- Claude Code session: 2026-09-12/13 — Valedia connect + ledger/gate/cap/fees on `master_allocator`, MiroFish end-to-end fix, execution policy + maker, daily P&L message.
- Handoff complete: **yes** (A–E filled, evidence-bound).
- Ruflo may start sprint D: **yes** (MCP `claude-flow` approved) — sprint D only, then stop for review.
