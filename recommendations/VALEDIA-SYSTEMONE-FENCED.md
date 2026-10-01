# VALEDIA-SYSTEMONE-FENCED — Nimble typed decisions (shadow, opt-in)

**Updated:** 2026-09-30 ~17:58 PT  
**Status:** shadow opt-in. Default **off** (`VALEDIA_SYSTEMONE` unset).  
**HTTP:** `127.0.0.1:11434` only. `POST /v1/systemone`. Model pin `nimble` (`nimble:9b` / `nimble:latest`).  
**Receipt:** `recommendations/NIMBLE-SYSTEMONE-REPLACE-LAYA-2026-09-30.md`  
**Replaces:** Laya movers-scan shadow. `VALEDIA-LAYA-FENCED.md` is retired. `:8766` is down.

## What it is

Local typed shadow on the paper movers scan. Same three judgments as pack `valedia-paper-v1`: `admit_paper`, `edge_quality`, `venue_class`. Fail-open. `mode=shadow`. `enforcement=none`. `judge=dont_trade`.

## What it is not

- Not live authority. Code owns the fee-true label and the would-have size. `create_order` stays false.
- Not Kelly / OMS. A high `admit_paper` does not size.
- Not the desk chat model. Desk stays LMS Ornith. Do not unload LMS to make room.
- Not Tev1. Pins containing `tev` or `0.8` are refused and are not posted. Tev1-4B is not the backup on this path.
- Not Mirofish Ollama. Do not set `OLLAMA_HOST` to `:11435`. Do not restart `com.burton.ollama-mirofish`.
- Not a bind on glance `:8765` or the retired Laya port `:8766`.

## Fence

- Enable for one process with `VALEDIA_SYSTEMONE=1`. Leave it unset on crons so a scan does not cold-load 9 GB beside resident Ornith.
- Jev (`VALEDIA_JEV`) stays the optional cloud seasoning. This client does not call it.
- No `--live`. No venue flips. No wallet or keys in the state. Oil `NO_HARVEST`. Weather watch. ALLOWLIST unchanged.
- Health is `GET /api/version` and `GET /api/tags` only. Do not stack a decide in front of a scan.
- Non-finite prints are null. Ollama rejects JSON `NaN`.

Keep Jev optional. Nimble stamps. It does not size and it does not send.
