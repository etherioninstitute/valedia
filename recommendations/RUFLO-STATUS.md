# Ruflo status board (Claude Code check-in)
Updated: 2026-09-17 PT — **Grok Build Slice 16 SHIPPED** (shadow parity ops, report-only)

## Slice 16 (Grok Build executor, `valedia-v2-grok-build`)
- `parity_run.py` + `nautilus_shadow_parity.py --out` default `models/shadow_parity_report.json`. Window `7d` / `SHADOW_PARITY_WINDOW`.
- Report-only: `ok` / `diffs` / `dangerous` / `cutover_ready`. Cron wrapper always exits 0. Live crontab not rewritten.
- Tests: `test_slice16.py` PASS, no network. No --live / VENUE_WRITER / AUTOEXEC / GET HTTP / NT.

---

Updated: 2026-09-17 PT — **Grok Build Slice 15 SHIPPED** (fenced merge/deploy into live)

## Slice 15 (Grok Build executor, `valedia-v2-grok-build`)
- Live `~/.openclaw/workspace` received Slices 1–14 fenced code (content-diff rsync, no `--delete`).
- Backup `~/.openclaw/backups/live-scripts-pre-s15-20260917_224401.tar.gz`. Tags: live `pre-arc-f-s15` / `fenced-deploy-s15`.
- `.env` / models / crontab installer **not** touched. Flags still off. Smoke PASS.
- Doc: `recommendations/VALEDIA-DEPLOY-FENCED.md`.

---

Updated: 2026-09-17 PT — **Grok Build Slice 14 SHIPPED** (Burton-gated spot GET, default OFF)

## Slice 14 (Grok Build executor, `valedia-v2-grok-build` `5996181`)
- Valedia-runtime getter: `reconcile_run.get_spot_order_getter()` → `live_spot_order_getter` only when `SPOT_RECONCILE_HTTP` is on. Default/cron = mock, no HTTP.
- Single flip (this lane never True): `export SPOT_RECONCILE_HTTP=1` or `valedia_config.SPOT_RECONCILE_HTTP = True`. Env wins. No extra `set_spot_order_getter`.
- Tests: `set_spot_http_transport` mocked path only; module inject never falls through to live `get_order`. `test_slice14.py` PASS, no network. No --live / VENUE_WRITER / AUTOEXEC / NT.

---

Updated: 2026-09-17 PT — **Grok Build Slice 13 SHIPPED** (MiroFish accuracy closed loop)

## Slice 13 (Grok Build executor, `valedia-v2-grok-build` `f036beb`)
- Fee-true outcomes from `forward_test_ledger` settlements → `mirofish_accuracy_ledger.jsonl`. Paper/sandbox/backfill/shadow never count. No yfinance/HTTP.
- Weight stays ≤ `MIROFISH_UNPROVEN_CAP` (0.10) until proven (`>` 55% directional on ≥20 fee-true results). `MIROFISH_AUTO_RAISE=False` — no auto-raise past cap. Proven cap 15% only if Burton flips AUTO_RAISE.
- `fusion_weights_for` / intel gather+blend read `effective_mirofish_weight()` + `mirofish_accuracy_summary.json`.
- Tests: `test_slice13.py` PASS, no network. No --live / VENUE_WRITER / AUTOEXEC / GET HTTP / NT.

---

Updated: 2026-09-17 PT — **Grok Build Slice 12 SHIPPED** (shadow parity harness, 7d compare.ok)

## Slice 12 (Grok Build executor, `valedia-v2-grok-build` `d57f7ed`)
- `nautilus_shadow_parity.py`: mockable `7d` window. Compares shadow Intents vs Valedia POST bodies for brain Coinbase + HL short + Polymarket arb + intel Coinbase. Records `compare.ok` / diffs. Allocator Coinbase is not cutover evidence.
- Fail-closed on side / instrument / size / venue / signed / live hook / leverage / dispatchable / live_flag / writer=nautilus. `cutover_ready` needs 7 calendar days of matching brain rows and does **not** flip flags.
- Tests: `test_slice12.py` PASS, no network. No --live / VENUE_WRITER / AUTOEXEC / GET HTTP / NT.

---

Updated: 2026-09-17 PT — **Grok Build Slice 9 SHIPPED** (pnl_tracker report-only + net pause + learning re-enable)

## Slice 9 (Grok Build executor, `valedia-v2-grok-build` `a0f3572`)
- Blacklist is a pause with TTL (7d) + probation, not a grave. Legacy `blacklisted[]` with no `until` is expired on read.
- Pause on fee-true `category_stats` net (`n>=4`, `net<0`). WR after 3 trades is report-only and cannot kill a fat-tail winner.
- Evidence `net>=0` unpauses immediately. Allocator sizes unpaused names; execute still `SKIP_LIVE_FENCE` for weather/oil (Slice 8 create_order / alp_buy stay dead).
- `should_trade_category` reason is `paused until … (not permanent)`. Tests: `test_slice9.py` PASS, no network. No --live / AUTOEXEC / GET HTTP / NT.

---

Updated: 2026-09-17 PT — **Grok Build Slice 8 SHIPPED** (one fusion + one fee table; scalper/weather dead; REBALANCER_AUTOEXEC=False)

## Slice 8 (Grok Build executor, `valedia-v2-grok-build` `02987c3`)
- Canonical live fusion: `valedia_config.FUSION_WEIGHTS` (kernel-first) → `unified_brain.stage_fusion`. `signal_fusion.LIVE_PATH=False` (report scanner only).
- Canonical fees: `fee_engine.STATIC_FEES`. Brain `FEES` dict deleted. `stage_fee_gate` uses `roundtrip_cost(..., use_static=True)` so Coinbase spread is not silently dropped. `stage_kelly` delegates to `KellySizer`.
- MicroScalper live buys dead (`scalper_place_buy` never calls `alp_buy`). Allocator hard-skips `WEATHER_ARB`/`OIL_BRACKET` even if blacklist file missing. `weather_arb.execute_weather_trades` never `create_order`; dry forced.
- `REBALANCER_AUTOEXEC=False`. Shadow/propose only. Flag+NT still cannot dispatch. This lane did not set True.
- Tests: `test_slice8.py` + prior slices PASS. Drift detector 10 PASS · 0 WARN · 0 FAIL. No network, no --live, no NT cutover.

---

Updated: 2026-09-17 PT — **Grok Build Slice 7 SHIPPED** (spot GET OFF + per-strategy cap + cron stagger)

## Slice 7 (Grok Build executor, `valedia-v2-grok-build`)
- Spot GET helpers exist (`spot_order_get.py`, Coinbase/Alpaca `get_order`) behind `SPOT_RECONCILE_HTTP=False`. Default cron = mock/no HTTP. Burton flip later: `export SPOT_RECONCILE_HTTP=1`.
- Per-strategy cap from `category_stats` via `category_track_record_mult` → allocator + sports + KellySizer. Paper/unreconciled/backfill cannot lift.
- Worktree crons staggered: sports `:00` / 2h, allocator `:20` / 4h. **Live crontab not rewritten by this slice** (still collides until merge/install).
- Fence intact: no --live, no orders, no NT, no GET HTTP on.

---

Updated: 2026-09-13 PT — **SPRINT D: audit ✅ + B1 shipped to worktree (STOP for review)**

## Role
- **Ruflo = main coder / auditor.** Valedia runtime = sole live risk authority.
- Ruflo codes/audits; never `--live`, orders, or venue keys.

## Where the B1 code lives (IMPORTANT — not live yet)
- All B1 edits are in worktree branch `claude/valedia-continuous-improvement-c759ee` at
  `.claude/worktrees/valedia-trading-improvement-b7d855/`. **The live cron runs the MAIN
  checkout** `~/.openclaw/workspace/scripts/integrations/sports_engine.py` — unchanged.
  Nothing here affects live trading until Burton merges. Rollback = don't merge / `git checkout`.

## Sprint D — Step 1: Audit-verify (DONE, evidence-bound)
- **HOLDS** — live Kalshi authority = `master_allocator.execute_allocations → record_entry`
  (codebase-memory `trace_path`, valedia-scripts graph). Gate `:391`, cap `:296`, fees present.
- **HOLDS** — `kalshi_executor.run` **INERT**: trace_path inbound = self `__main__` + `kalshi_trade` only; absent from live crontab. Do not rewire.
- **LIVE PROOF (actual `crontab -l`):** `0 */2 * * * sports_run.py` (sports every 2h),
  `0 */4 * * * allocator_run.py` (allocator every 4h), `30 * * * * settle_run.py` (hourly),
  `0 */6 * * * reconcile_run.py` (ledger reconcile every 6h).
- Drift detector `valedia_consistency_check.py` = **7 PASS · 0 WARN · 0 FAIL** (before + after B1).

## Sprint D — Step 2: B1 `sports_engine.py` hardening (code SHIPPED to worktree)
Coder = Claude Code; **tester** (ruflo-testgen:tester) + **reviewer** (ruflo-core:reviewer) agents ran under the open swarm.
- ✅ Fee-true sizing via canonical `ftl.kalshi_fee` (`profit_if_win` net of fee; `fee` recorded); loss P&L now `-(cost+fee)`.
- ✅ `record_entry(source="live")` on EXECUTED fills → the ONE fee-inclusive ledger (mirrors master_allocator).
- ✅ `should_trade_category("SPORTS")` gate before execute (fail-open, correct placement — a block prevents `create_order`).
- ✅ DRY_RUN never persisted and never settled as real.
- ✅ `record_entry` failures now log to stderr (was silent `pass`) — from review.
- Tests: `test_sports_engine.py` + `test_sports_engine_b1_extra.py` (7 behaviors, adversarial) = **PASS**; `test_category_gate.py` regression = PASS.

## ⚠️ HONEST CAVEAT — B1 gate is wired but currently INERT
- Live `forward_test_ledger.jsonl` = 76 recs, **100% `backfill:pnl_tracker`, 0 live-sourced**; SPORTS rows dated 2026-03-23 (~174d, outside the 45d window). So `should_trade_category("SPORTS")` returns "unproven/thin → not blocked" until **≥4 live SPORTS trades settle within 45 days** (this diff is the first code to feed them; `reconcile_run.py` @6h will settle them).
- Meanwhile sports is a **real current loser: 69.4% win, net −$42.70 / 232 bets** (in `sports_engine_log.json`) — a number the ftl-based gate does not yet see.
- Reviewer initially claimed reconcile "not scheduled" — **corrected**: it IS scheduled (6h) in the live crontab; reviewer only checked the stale installer.

## Backlog from B1 review (each = its own swarm slice; NOT done)
1. **[HIGH] acceptance ≠ fill** — `create_order` return ignored; a resting/unfilled order is marked EXECUTED, recorded, and inflates `open_position_count` → throttles Kelly sizing for ALL strategies. Pre-existing in master_allocator too. Verify fill before EXECUTED/record.
2. **[HIGH] gate coverage gap** — `master_allocator`/`kalshi_executor` trade the SAME sports series under category `"SPORTS_TONIGHT"`, not `"SPORTS"`; the new gate doesn't cover the allocator path. Unify the label or add the gate there.
3. **[MED] cron collision** — sports (2h @:00) + allocator (4h @:00) fire same minute on 4h boundaries; both can see "not held" and double-order same ticker. Stagger or per-ticker lock.
4. **[repro] stale cron docs** — `install_cron.sh` + `skills/valedia/cron-schedule.md` omit `sports_run`/`allocator_run`/`reconcile_run`/`settle_run` that the live crontab actually runs.

## Next lever (B2, per Burton's standing rule = full coder+tester+reviewer swarm)
- **B2 — unify fusion weights** (`signal_fusion.py:34 WEIGHTS` vs `unified_brain` inline) → one source in `valedia_config`. (Or Burton may prioritize B1 backlog #1/#2 first — both are live-money HIGHs.)

## Log
- 2026-09-13: Sprint D. Audit-verify HOLDS (trace_path + live crontab proof). B1 sports_engine hardening shipped to worktree with tester+reviewer swarm; drift 7 PASS. Review found gate currently inert (ftl live-SPORTS data starved) + 3 backlog HIGH/MED items. **STOP for Burton/Claude review before merge or B2.** Fence intact: no --live, no orders, no keys; changes isolated to worktree.
