# VALEDIA-LAYA-FENCED — Laya typed-decisions (reference only)

**Updated:** 2026-09-21 PT  
**Clone (current):** `~/MemPalace/sources/repos/github/laya-coreml` @ `12b7501`  
**Upstream:** https://github.com/mizorewww/laya-coreml (Core ML / Apple Silicon)  
**Prior clone (archived):** `~/MemPalace/sources/repos/github/laya-NandhaKishorM-archived` @ `42626c3` (https://github.com/NandhaKishorM/laya)

## What it is

Open-weight typed decisions on Apple Silicon via Core ML + Neural Engine (`laya-coreml`). Zero generated tokens for a decision; PyPI package `laya-coreml`; HF weights `aac6fef/laya-multilingual-coreml-ane`. Optional local twin to TypeSafe Jev — tuned for M-series, no cloud API fee.

## What it is NOT

- Not a replacement for `VALEDIA_JEV` / TypeSafe Jev (Jev stays the optional cloud seasoning).
- Not on Valedia critical path. Valedia fully works with Laya absent.
- Not Kelly / FTL / OMS. Not live order authority.
- Not an auto-install into Valedia runtime (weights + ANE init cost).

## Fence

- Code/audit / MemPalace corpus only until Burton asks for a shadow experiment.
- Any future wire = same shape as MIROFISH-JEV-SHADOW: annotate-only, fail-open, default **off**.
- No live venue flips. No wallet/keys. No halt bypass.
- Do not `pip install laya-coreml` into Valedia runtime without an explicit go (ANE/RAM budget).

## Vs Jev (standing decision 2026-09-20; clone swapped 2026-09-21)

| | Jev (TypeSafe API) | Laya-CoreML (local) |
|--|--|--|
| Status | Wired optional (`VALEDIA_JEV`) | MemPalace clone only |
| Cost | API | Self-host + ANE |
| Role | Cloud seasoning | Future local experiment on Apple Silicon |

Keep Jev. Laya-CoreML is research inventory.

## Inventory

Listed in `~/MemPalace/sources/shared-brain/AGENT-INVENTORY.md`.
