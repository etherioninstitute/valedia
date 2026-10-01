# VAUGHAN-SUBSUME-2 — BB-Terminal INTEL/signals → OpenTerminal-Valedia (FORK-UI)

**Burton:** 2026-09-23 ~00:51 PT — continue queue after ETORO-SUBSUME-1.  
**Depends on:** VAUGHAN-SUBSUME-1 (0.66.0) + ETORO-SUBSUME-1 (0.67.0) working tree.  
**Clone:** `~/MemPalace/sources/repos/github/vaughanf1-BB-Terminal`  
**Target UI:** `~/MemPalace/sources/repos/github/OpenTerminal-Valedia` (desk on :3001 / screen `ot-valedia-desk`)  
**Dossier:** `VAUGHAN-SUBSUME-DOSSIER.md`

## Goal
FORK-UI only: port BB-Terminal **INTEL / CC scorecard patterns** and `app/src/lib/signals.ts` into OpenTerminal-Valedia as **read-only display + offline tags** that rhyme with FOMO enrich / CE ladder. No order buttons, wallet connect, or LLM auto-size. No live venue writes.

## Steal (reference — adapt, don’t dump OpenBB wholesale)
- `app/src/lib/signals.ts` — rule/scorecard helpers
- `app/src/functions/INTEL.tsx` + `CC.tsx` — panel UX ideas
- Optional taste: `CommandBar.tsx` command affordances (Valedia-named, not Bloomberg clone spam)

## Scope (do)
1. Read BB clone + current OT-Valedia widgets/Controls/FOMO tags (CBM + RuFlo). Prefer TypeScript in `OpenTerminal-Valedia/web`.
2. Add a Valedia-native INTEL/signals panel or Controls section that:
   - shows offline/read-only signal tags compatible with existing FOMO enrich + CE ladder language
   - can optionally display **cached** eToro paper decisions / Vaughan arb paper rows as intel (no eToro/Kalshi/Poly write)
   - CRYPTO/FX display-only — never wire wallets or copy buttons
3. Keep OpenBB (if used) as research/data only; prefer existing Valedia APIs/`web/app/api/valedia` over new vendor keys when possible.
4. Tests or smoke: panel renders / build typecheck; no `create_order` / `start_copy` / Poly LIVE flips.
5. Docs: short `BB-TERMINAL-INTEL.md`. Ponytail audit for this slice. Bump working-tree version.
6. Preserve screens: do not kill `ot-valedia-desk` / `valedia-glance`; use `valedia-grok` only. If desk needs restart, bring `:3001` back up.

## Explicit non-goals
- Absorbing GoldQuant / vibe-trader engine / HedgeFund as live brains
- eToro `ETORO_LIVE` unlock or copy POSTs
- Poly LIVE writers, wallets, oil/weather unfence
- Order entry / wallet connect / LLM auto-sizing trades

## Hard fence
Display + offline tags only. No `--live`, no venue orders, no eToro copy.

## Tools
RuFlo `:8810` filtered. CBM. Prove in `GROK-STATUS.md`.

## Done when
- INTEL/signals (or equivalent) visible in OT-Valedia without order affordances
- Tests/typecheck green enough to ship working tree
- Slice Ponytail written; version bumped
- `GROK_EXIT:0` for this slice, then **immediately execute** `recommendations/RUFLO-PONYTAIL-AUDIT.md` in the same Build (do not wait for parent)

## Results

**2026-09-23 ~01:10 PT — DONE.** `GROK_EXIT:0`. Working tree `VALEDIA_VERSION` **0.68.0** (not committed, not tagged).

INTEL is on the OpenTerminal desk: `web/lib/intelSignals.ts`, `web/components/widgets/IntelWidget.tsx`, GET `/api/valedia/intel`. FOMO fixture tags BTC `burst` 1.92, ETH `seeded`, SOL `exit` 1.28. CE ladder reads `PAPER/SHADOW+ORNITH`. Cached eToro and Vaughan rows render with `create_order` false and `start_copy` false. POST returns 405.

`intelSignals.check.ts` PASS (including static markup: no button). `tsc --noEmit` PASS. Python PASS: `test_fomo_radar`, `test_etoro_copy_paper`, `test_vaughan_arb_paper`, `test_crypto_paper`, `test_crypto_paper_safety`, `test_slice23`, `test_controls_readout`. `test_openterminal_valedia` reached the 0.68.0 assert, then failed on a pre-existing 500-character badge window around `INFORMED_BEFORE_CREATE` notes. Not patched.

Screens 783 `ot-valedia-desk`, 755 `valedia-glance`, 98551 `valedia-grok` still detached. This job did not signal them. `:3001` stayed up. Glance was not reloaded, so `:8765` can still report the previous process version.

Parent shell still has `POLYMARKET_US_LIVE=1` and `VALEDIA_JEV=1`. This Build did not set them. Code constants stay False. `ETORO_LIVE` unset. Oil/weather fence held.

Docs: `BB-TERMINAL-INTEL.md`, `PONYTAIL-AUDIT-VAUGHAN-SUBSUME-2.md`. RuFlo: `hooks_route`, `memory_search`, `hooks_post-task`. CBM: OpenTerminal-Valedia and workspace-v2 graphs. BB-Terminal was not in the CBM project list; `signals.ts`, `INTEL.tsx`, and `CC.tsx` were read from the clone.

Headless Chrome did not finish hydration, so the open desk tab was not clicked. A fresh render of `IntelBody` was checked in the unit test. Reload the desk tab once to migrate localStorage to version 2.

Next = `RUFLO-PONYTAIL-AUDIT` in this same Build.
