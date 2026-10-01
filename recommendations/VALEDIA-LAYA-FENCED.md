# VALEDIA-LAYA-FENCED — Laya typed-decisions (RETIRED)

> **RETIRED 2026-09-30 ~17:58 PT.** The movers scan ignores `VALEDIA_LAYA`. HTTP `:8766` is stopped (pid 25133 via `stop.sh`). Local decide is Nimble System One. Receipt `recommendations/NIMBLE-SYSTEMONE-REPLACE-LAYA-2026-09-30.md`. Standing fence `recommendations/VALEDIA-SYSTEMONE-FENCED.md`. The body below is the historical record. Do not turn `:8766` back on for the scan.

**Updated:** 2026-09-30 ~17:58 PT  
**Status:** RETIRED from the movers scan. Not live authority.  
**HTTP:** was `127.0.0.1:8766`. Port empty after stop. `mode` was `shadow`. `enforcement` was `none`. Pack `valedia-paper-v1`.  
**Receipt:** `recommendations/LAYA-ANE-HFT-HOTPATH-2026-09-30.md` (historical). Replace receipt above.  
**Clone (current):** `~/MemPalace/sources/repos/github/laya-coreml` @ `12b7501`  
**Upstream:** https://github.com/mizorewww/laya-coreml (Core ML / Apple Silicon)  
**Prior clone (archived):** `~/MemPalace/sources/repos/github/laya-NandhaKishorM-archived` @ `42626c3` (https://github.com/NandhaKishorM/laya)

## What it is

Open-weight typed decisions on Apple Silicon via Core ML + Neural Engine (`laya-coreml`). Zero generated tokens for a decision; PyPI package `laya-coreml`; HF weights `aac6fef/laya-multilingual-coreml-ane`. Optional local twin to TypeSafe Jev — tuned for M-series, no cloud API fee.

## What it is NOT

- Not a replacement for `VALEDIA_JEV` / TypeSafe Jev (Jev stays the optional cloud seasoning).
- Not on the Valedia critical path. Default off. Fail-open. Valedia still runs with Laya down.
- Not Kelly / FTL / OMS. Not live order authority. Code owns size.
- Not an auto-install of `laya-coreml` into the Valedia runtime. The scan speaks HTTP only.

## Fence

- Retired 2026-09-30 ~17:58 PT. Do not enable `VALEDIA_LAYA` for the movers scan. The scan does not call this client.
- `com.burton.laya-decide` stays loaded with `KeepAlive` false and `RunAtLoad` false. Do not bootout as part of a trading bounce. Do not kill `laya_decide mcp` processes; those are Claude MCP sessions.
- Historical run was annotate-only, fail-open, `LAYA_DECIDE_ENFORCEMENT=none` on `127.0.0.1:8766`.
- No live venue flips. No wallet/keys. No halt bypass. `create_order` stays false.
- Do not `pip install laya-coreml` into the Valedia runtime. Do not bind `:8765`.

## Vs Jev (standing decision 2026-09-20; clone swapped 2026-09-21)

| | Jev (TypeSafe API) | Laya-CoreML (local) |
|--|--|--|
| Status | Wired optional (`VALEDIA_JEV`) | RETIRED from the movers scan (`VALEDIA_LAYA` ignored) |
| Cost | API | Self-host + ANE on `:8766` |
| Role | Cloud seasoning | Was the local typed shadow. Retired. Nimble holds that seat. |

Keep Jev. Laya no longer stamps the movers scan.

## Inventory

Listed in `~/MemPalace/sources/shared-brain/AGENT-INVENTORY.md`.
