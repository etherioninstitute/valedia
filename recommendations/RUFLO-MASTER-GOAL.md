# Ruflo master goal (filled by Claude Code → executed by Ruflo)

## Goal (one sentence)
_Maximize Valedia’s long-run profit by hardening the coding harness — live-path correctness, fee-true ledger, category gates, and Claude-checkable progress — without Ruflo placing any live trades._

## Ownership
**RuFlo is the primary coder.** Claude Code is optional Burton review only. Goal UI `/agents` is not the executor. Prior wiring is not sacred — improve or replace with proof.

## North star
**Maximum profit through a better Valedia system** (reliability, edge measurement, gates, execution quality). Ruflo ships code, tests, audits, and docs. **Valedia runtime stays sole live risk authority.** Ruflo does **not** run `--live`, place venue orders, or change trading API keys.

## Workspace
Live Valedia root: `~/.openclaw/workspace` (this tree). Read first: `recommendations/CLAUDE-TO-RUFLO-HANDOFF.md`, this file, `recommendations/RUFLO-STATUS.md`.

## Success criteria
1. `recommendations/RUFLO-STATUS.md` stays current after each material step.
2. Any code change that claims “wired” includes **live-path proof** (who calls it on cron / `master_allocator`) + ponytail — not `/audit` alone.
3. `should_trade_category` (or equivalent) built **clean-room** on Valedia’s own ledger; no kalshi-agent code copied (UNLICENSED).
4. No `--live`, no order placement, no API key changes for trading venues.
5. Work prioritizes the highest-leverage profit levers in the handoff (ledger truth → gates → sizing → edge quality), not busywork.

## In scope
- Inventory/adopt/park for MIT/BSD refs already cloned (OpenTerminal patterns, HyperGrok skills attribution only).
- Fix ledger/`record_entry` live-path gaps Claude already found.
- Status board + small safe refactors / tests that improve measured edge or reduce silent P&L lies.
- Sprint backlog B1+ from handoff (sports_engine, fusion unify, per-strategy sizing, ledger completeness, …) only after First milestone review.

## Out of scope
- Live Kalshi / Hyperliquid / Alpaca / Polymarket orders
- Fortress stealth browser on Colima (parked — M1 qemu crash)
- Ruflo neural-trader / market-data plugins
- Copying proprietary `kalshi-agent` modules into Valedia
- Acting as Valedia’s live risk brain

## Continuous mode (2026-09-13)
RuFlo keeps coding day/night. Status + profit-lever notes go to Burton. Goal UI `/agents` is not the executor.

## First milestone (do this first — mirrors HANDOFF section D)
> Note: the original "trace/fix `record_entry`" milestone is **DONE** (Claude wired it into the true live path — `master_allocator.py:473`, gate `:391`, cap `:296`; verified via codebase-memory `trace_path`). Ruflo's first job is to independently VERIFY it, then take the first coding task.
1. **Audit-verify (HOLDS/FAILS, file:line + live-path proof)** the live path: `record_entry` / `should_trade_category` / `dynamic_per_trade_cap` / `fee_engine` on `master_allocator`; confirm `kalshi_executor.run` is inert; drift detector `valedia_consistency_check.py` PASS.
2. **First coding task — B1: `sports_engine.py` hardening** (clean-room + tests + ponytail + live-path proof): fees→`fee_engine`, `record_entry`→real ledger, `should_trade_category` gate, and never log a DRY_RUN bet as executed. No `--live`, no orders, no key changes.
3. Update `RUFLO-STATUS.md` with evidence (file:line, test output, detector result).
4. Continue into B2+ under continuous coding mode; Burton reviews via Claude Code whenever — do not block on Goal UI Approve. Still no live trading.

## Handoff
Claude Code: refine this goal if needed, then tell Ruflo (via MCP `claude-flow` / guidance tools) to execute **First milestone** only.
