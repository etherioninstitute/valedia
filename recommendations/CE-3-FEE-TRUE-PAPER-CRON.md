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
