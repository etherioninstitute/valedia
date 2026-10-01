# CE-3 — Fee-true decision log + paper-first cron loop

**Burton go:** 2026-09-22 ~21:20 PT — “queue up CE-2 and the rest”  
**Depends on:** CE-2 DONE  
**Upstream pattern:** claude-execute decision/safety log → Valedia crypto paper + FTL (fee-true), paper-first only.

## Goal
Close the claude-execute subsume for **paper**:
1. Every CE tick decision (PASS/FAIL/refuse) appends a **fee-true decision log** into the existing crypto-paper / FTL-shaped ledger path (would-have fees, size, side, reason codes) — never silent.
2. Harden the **paper-first cron loop** so scheduled ticks are idempotent, log rotation/retention clear, and Controls show last tick age + last Ornith result.
3. Keep Ornith fail-closed; Qwen tick assist optional.

## Scope (do)
1. Map existing `forward_test_ledger` / crypto-paper equity logs; add or extend a decision log that records fee assumptions used for would-have PnL (STATIC_FEES or paper fee table) — **paper source only**; must not lift Kelly / `real_track_record`.
2. Wire CE-2 tick to always write decision rows (PASS fill, FAIL refuse, ornith_down, etc.).
3. Cron loop polish: lockfile / single-flight; stale tick alert fields; desk doc update.
4. Tests: fee fields present; paper decisions excluded from Kelly; no network create_order.
5. Ponytail + `GROK-STATUS.md`. **Next after exit:** MIROFISH-INTEL-ARCHIVE (not inside this job).

## Explicit non-goals
- Live venue orders, wallets, Poly LIVE, oil/weather unfence
- Counting paper fills toward Kelly / real_track_record
- CE-4 live execute (does not exist without Burton go)

## Hard fence
CRYPTO-PAPER only. Preserve OT desk / glance screens. No `--live`.

## Done when
- Decision log fee-true for paper path; tests PASS; audit; `GROK_EXIT:0`; Next=MIROFISH-INTEL-ARCHIVE

## Results

**2026-09-22. VALEDIA_VERSION 0.64.0** (working tree, not committed, not tagged).

`--tick` appends one fee-true decision to `data/crypto-paper/decisions.jsonl` on every result: pass fill, refuse, `ornith_down`, `duplicate_tick`, `tick_busy`. Fee table is the paper table `CRYPTO_FEE_PCT` (0.001) and `CRYPTO_SLIPPAGE_PCT` (0.0005). Source is `crypto_paper`. `type` is `decision`. The live `forward_test_ledger` file is not written. A settlement copied from that source stays out of `real_track_record`, `category_stats`, and `_track_record_mult`.

Single-flight lock `decisions.jsonl.lock`. Same pair+bar already filled → `duplicate_tick`, Ornith not called again, no second fill. Retention is the last 400 rows. Controls read the log: `CRYPTO_PAPER_TICK_AGE_S`, `CRYPTO_PAPER_LAST_ORNITH`, `CRYPTO_PAPER_TICK_STALE` (missing or older than 2h). Cron not installed. Command stays in `CRYPTO-PAPER-DESK.md`.

Tests `test_crypto_paper_safety.py` PASS (fee fields, pass/refuse/`ornith_down`, duplicate, lock busy, retention, Kelly exclusion, no `create_order(`). `test_crypto_paper.py` PASS. `test_controls_readout.py` PASS. `test_fomo_radar.py` PASS. `CRYPTO_PAPER_SAFETY` stays `PAPER/SHADOW+ORNITH`. `CRYPTO_PAPER_MODE` stays `PAPER/SHADOW`. `WEATHER_LIVE` false. `OIL_BRACKET` still fenced.

Dead-port `--tick --no-qwen`: `ornith_down`, buy qty 9.25388969, would-have fee 1.0005, `would_have_pnl` empty, `create_order` false, no equity file. Not realized.

Screens 783 `ot-valedia-desk`, 755 `valedia-glance`, 81222 `valedia-grok` still detached. Glance was already listening, so this job did not reload it. Oil/weather fence unchanged. No Poly writer, no wallet, no venue order.

Commit skipped. The branch has a large uncommitted set, including earlier rows in `valedia_config.py`. A CE-3-only commit would not check out clean.

**Tools:** RuFlo `hooks_route`, `hooks_pre-task`, `hooks_pre-edit`, `memory_search`, `memory_retrieve` (filtered `:8810`). CBM `list_projects`, `search_graph` (`run_tick`, `run_safety`, `apply_fill`, `real_track_record`), `get_code_snippet`, `check_index_coverage` on the paper modules and `forward_test_ledger.py` (no recorded gap; best-effort). Ponytail `PONYTAIL-AUDIT-CE-3-FEE-TRUE-PAPER-CRON.md`.

**Next = MIROFISH-INTEL-ARCHIVE.** Parent launches it. This job did not. Do not unfence oil/weather. Do not treat the paper fee quote as realized profit.

`GROK_EXIT:0`
