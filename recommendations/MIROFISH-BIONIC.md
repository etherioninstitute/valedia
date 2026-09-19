# MIROFISH-BIONIC — migrate MiroFish inference to Bionic (Ollama fallback)

**Burton go:** 2026-09-19 — Migrate MiroFish to Bionic `:1234`; keep Ollama `:11435` as fallback until verified.

## Goal
All MiroFish LLM + embedding traffic prefers **Bionic (LM Studio) `http://127.0.0.1:1234`**. Dedicated Ollama MiroFish (`com.burton.ollama-mirofish` on `:11435`) stays installed as **fallback** until a smoke proves Bionic path stable. No trading venue flips. No deletes of Ollama model blobs.

## Current (Ollama) — do not break fallback
From `~/mirofish-offline/.env`:
- `LLM_BASE_URL=http://localhost:11435/v1`
- `LLM_MODEL_NAME=qwen3-8b-mf:latest`  (FAST sync/swarm — timeout-critical)
- `MIROFISH_REPORT_MODEL=ornith-1.5-mf:latest` (report-only, async)
- `EMBEDDING_MODEL=nomic-embed-text` + `EMBEDDING_BASE_URL=http://localhost:11435` (**Ollama `/api/embed`**, not OpenAI)
- `OPENAI_API_BASE_URL=http://localhost:11435/v1`
Backend: `~/mirofish-offline/backend` (Flask `:5001`). Valedia runner hits backend HTTP only.

## Bionic inventory (live 2026-09-19)
Chat IDs on `:1234/v1/models`:
- `qwen3.8-27b-obliterated-k` — interim FAST/sync (no dedicated 8B on Bionic yet)
- `ornith-1.5-k-m` — REPORT
- `text-embedding-nomic-embed-text-v1.5` — embeddings (**OpenAI `/v1/embeddings` works**, 768-d verified)

**Gap:** No `qwen3-8b-mf` on Bionic. Sync on 27B may need longer ontology timeouts — measure in smoke; if timeouts return, keep sync on Ollama via split env until a small/fast GGUF is in `lms-library`.

## Required deliverables
1. **`MIROFISH_INFERENCE=bionic|ollama`** (default after ship: `bionic`) in `~/mirofish-offline/.env` + documented restore to `ollama`.
2. When `bionic`:
   - `LLM_BASE_URL=http://127.0.0.1:1234/v1`
   - `OPENAI_API_BASE_URL=http://127.0.0.1:1234/v1`
   - `LLM_MODEL_NAME=qwen3.8-27b-obliterated-k` (or better snappy if present)
   - `MIROFISH_REPORT_MODEL=ornith-1.5-k-m`
   - `EMBEDDING_MODEL=text-embedding-nomic-embed-text-v1.5`
   - `EMBEDDING_BASE_URL=http://127.0.0.1:1234` (OpenAI-compatible)
   - `LLM_API_KEY` any non-empty (e.g. `lm-studio`)
3. **`embedding_service.py`**: support OpenAI `/v1/embeddings` when `EMBEDDING_API=openai` (or auto-detect from base URL containing `:1234` / `/v1`). Keep Ollama `/api/embed` path for fallback.
4. Update Valedia helpers (live + v2 as needed): `mirofish_healthcheck.py`, `valedia_models.sh` — probe Bionic primary; still allow Ollama fallback check; do not hard-require only `:11435`.
5. Backup `.env` before edit (`*.bak-mirofish-bionic-<ts>`).
6. Smoke (fenced): (a) Bionic embedding round-trip, (b) one chat completion on sync model id, (c) healthcheck PASS or documented WARNs, (d) optional lightweight backend ping. **No** live Kalshi/Poly orders. **Do not** stop `com.burton.ollama-mirofish` LaunchAgent.
7. Docs: this file + `GROK-STATUS.md` + queue note. Version bump Valedia only if workspace scripts change; mirofish-offline may be outside Valedia git — still commit/copy helpers into workspace-v2 and rsync live scripts carefully.
8. Tools: RuFlo + Ponytail + CBM on the coding path (Accuracy bar).

## Fallback (must stay one flip away)
Set `MIROFISH_INFERENCE=ollama` (or restore bak `.env`) → previous `:11435` URLs + `qwen3-8b-mf` / `ornith-1.5-mf` / ollama embed. Restart MiroFish backend after env change.

## Hard fence
- No `--live` / venue writers / wallet / ACH
- No `POLYMARKET_US_LIVE`
- No deleting Ollama models or detaching Bionic library
- No unloading Burton’s currently loaded Bionic chat model without note
- MiroFish data streams must keep working under `VALEDIA_TRADING_HALT`

## Done when
- Primary `.env` points at Bionic; Ollama fallback documented + bak present
- Embedding OpenAI path implemented + tested
- Healthcheck understands both backends
- Smoke evidence in `recommendations/MIROFISH-BIONIC.md` (append Results)
- STATUS/queue updated; Ponytail thin audit

## Results (2026-09-19)

**Version:** Valedia `0.44.0`. Bak: `~/mirofish-offline/.env.bak-mirofish-bionic-20260919-161953`.

**Primary `.env`:** `MIROFISH_INFERENCE=bionic`. `LLM_BASE_URL`/`OPENAI_API_BASE_URL` = `http://127.0.0.1:1234/v1`. Sync `qwen3.8-27b-obliterated-k`. Report `ornith-1.5-k-m`. Embed `text-embedding-nomic-embed-text-v1.5` + `EMBEDDING_API=openai` + `EMBEDDING_BASE_URL=http://127.0.0.1:1234`. One-flip: `valedia_models.sh mirofish-inference ollama --write` then restart `:5001`. Do not stop `com.burton.ollama-mirofish`.

**embedding_service.py:** OpenAI `/v1/embeddings` when `EMBEDDING_API=openai` or URL contains `:1234`/`/v1`. Ollama `/api/embed` kept.

**Smoke (fenced):**
- (a) Bionic embed: dim **768**, curl **0.217s**; `EmbeddingService` kind=openai **0.028s**. After 27B chat, embed still 768-d (**46.744s** — LMS JIT; `loaded` list then showed only the 27B).
- (b) Chat `qwen3.8-27b-obliterated-k`: first call **56.572s**, `content=''`, `finish=length`, all 16 tokens reasoning. Retry `reasoning_effort=none`: **27.679s**, `content='ok'`, `reasoning_tokens=0`. No ontology timeout in this short prompt. 27B vs 8B timeout risk remains for sync swarm stages.
- (c) Healthcheck: **19 PASS · 2 WARN · 1 FAIL**. FAIL = Neo4j `:7687` closed (pre-existing; this slice did not start/stop Neo4j). WARNs = lightweight `intel_refresh` signal age 0h; 7 ledger preds, 0 scored. Serving: Bionic 10 models + Ollama 33 tags all required ids/tags present. Sync chat PASS `reply='ok'`.
- (d) Backend `:5001` HTTP **404** on `/api` (expected; server up). Restarted Flask (killed stuck `mirofish_primary.sh` PID 73025 first so it could not continue into `mirofish_runner.py`; new `run.py` PID 63362). **Did not** start a swarm.

**Loaded-model note:** At start, Bionic had **only** `text-embedding-nomic-embed-text-v1.5` loaded (no chat model). Smoke JIT-loaded `qwen3.8-27b-obliterated-k`. No previously loaded chat model was unloaded.

**Fence:** `com.burton.ollama-mirofish` stayed running (launchctl pid **1893**, list `18930`). No Ollama model deletes. No `--live`, no `POLYMARKET_US_LIVE`, no writer/venue flips. Live `valedia_config.py` **not** wholesale-copied (live writers stay Kalshi-only `coinbase`/`alpaca`=`none`).

**Tests:** `test_mirofish_bionic.py` PASS. `test_mirofish_smoke.py` PASS. `test_slice23.py` PASS. `test_kill_switch.py` PASS.

**Ponytail:** `recommendations/PONYTAIL-AUDIT-MIROFISH-BIONIC.md`.
