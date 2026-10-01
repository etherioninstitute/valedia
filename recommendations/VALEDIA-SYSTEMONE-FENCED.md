# VALEDIA-SYSTEMONE-FENCED — Nimble 2 typed decisions (shadow, opt-in)

**Updated:** 2026-10-01  
**Status:** shadow opt-in. Default **off** (`VALEDIA_SYSTEMONE` unset).  
**HTTP:** `127.0.0.1:11434` only. `POST /v1/systemone`.  
**Model pin:** Nimble 2 (`Bespoke-Nimble-9B-v2`, 8,192 ctx). Hugging Face id `bespokelabs/Bespoke-Nimble-9B-v2`. Default Ollama tag `nimble2`, override with `VALEDIA_SYSTEMONE_MODEL`.  
**Confirm the tag:** the Ollama tag is not verified from the sandbox. Run `ollama list` on Burton's Mac and set `VALEDIA_SYSTEMONE_MODEL` if the installed name differs.  
**Accepted tags (full match, case-insensitive):** `nimble2`, `nimble2:<tag>`, `nimble:2`, `nimble:v2`, `nimble:9b-v2` (each optionally `-<quant>`, e.g. `-q4_k_m`, `-q8_0`, `-fp16`), and the release name `bespoke-nimble-9b-v2[:<tag>]`, bare or under `bespokelabs/` or `hf.co/bespokelabs/`. Refused and never posted: Nimble v1 (`nimble`, `nimble:9b`, `nimble:latest`), anything containing `v1`, `tev` or `0.8`, loose forms like `nimble:2b` / `nimble:20b`, and any other namespace or host (no provenance).  
**Receipt:** `recommendations/NIMBLE-SYSTEMONE-REPLACE-LAYA-2026-09-30.md`  
**Replaced:** Laya (retired 2026-09-30). `VALEDIA-LAYA-FENCED.md` is retired.

## What it is

Local typed shadow on the paper movers scan. Same three judgments as pack `valedia-paper-v1`: `admit_paper`, `edge_quality`, `venue_class`. Fail-open. `mode=shadow`. `enforcement=none`. `judge=dont_trade`.

## What it is not

- Not live authority. Code owns the fee-true label and the would-have size. `create_order` stays false.
- Not Kelly / OMS. A high `admit_paper` does not size.
- Not the desk chat model. Desk stays LMS Ornith. Do not unload LMS to make room.
- Not Nimble v1 and not Tev1. v1 tags and pins containing `tev` or `0.8` are refused and are not posted. Tev1-4B is not the backup on this path.
- Not Mirofish Ollama. Do not set `OLLAMA_HOST` to `:11435`. Do not restart `com.burton.ollama-mirofish`.
- Not a bind on glance `:8765`.

## Fence

- Enable for one process with `VALEDIA_SYSTEMONE=1`. Leave it unset on crons so a scan does not cold-load 9 GB beside resident Ornith.
- Jev (`VALEDIA_JEV`) stays the optional cloud seasoning. This client does not call it.
- No `--live`. No venue flips. No wallet or keys in the state. Oil `NO_HARVEST`. Weather watch. ALLOWLIST unchanged.
- Health is `GET /api/version` and `GET /api/tags` only. `model_present` is true only when the configured Nimble 2 tag is listed; otherwise the scan fails open to the same fee-true label. Do not stack a decide in front of a scan.
- The scan row label is `label` (fee-true, code-owned). The scan JSON carries a `systemone` block (backend, model, health) and no per-backend retired block.
- Non-finite prints are null. Ollama rejects JSON `NaN`.

Keep Jev optional. Nimble stamps. It does not size and it does not send.
