# BB-Terminal INTEL — display only

**Slice:** VAUGHAN-SUBSUME-2. Working tree `VALEDIA_VERSION` **0.68.0**. Not committed. Not tagged.

Adapted from `vaughanf1-BB-Terminal` `app/src/lib/signals.ts` (scorecard tally only). Equity P/E, moving-average, and analyst checks stayed in that clone. They need OpenBB.

## Where

- `OpenTerminal-Valedia/web/lib/intelSignals.ts` — FOMO tags (`burst`, `exit`, `seeded`) with the same thresholds as `fomo_radar_enrich.py`. CE ladder labels: `PAPER/SHADOW+ORNITH`, Qwen ranks only, fee-true `crypto_paper`.
- `web/app/api/valedia/intel/route.ts` — GET only. Reads the last rows of `data/crypto-paper/decisions.jsonl`, `data/etoro-paper/decisions.jsonl`, `data/vaughan-arb/decisions.jsonl`, plus the FOMO fixture. POST/PUT/PATCH/DELETE return 405.
- Desk widget `INTEL`. Sidebar and command palette (`INTEL` / `SIGNALS`) add it. Persisted desk version 2 appends it on reload.

## Fence

`create_order` false. `start_copy` false. `live` false. `wallet` false. CRYPTO/FX rows are labels, not tickets. No copy button. No wallet connect. No new vendor key.

Glance on `:8765` was not restarted. The desk on `:3001` / screen `ot-valedia-desk` was not killed.
