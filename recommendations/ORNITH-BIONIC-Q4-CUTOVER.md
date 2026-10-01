# ORNITH-BIONIC-Q4-CUTOVER — Valedia Ornith → Bionic-optimized Q4_K_M

**Burton 2026-09-22 ~17:35 PT:** After MiroFish run, Valedia must use the downloading **Ornith-1.5-35B A3B GGUF Q4_K_M** (Bionic-optimized) instead of current Ornith.

## Current → target

| Surface | Today | After cutover |
|---------|--------|----------------|
| Bionic/LMS id | `ornith-1.5-k-m` (Q5 lane) | `ornith-1.5-35b-a3b@q4_k_m` |
| LiteLLM `~/.litellm/config.yaml` | `ornith-1.5`, `ornith-1.5-k-m`, `mirofish`, `valedia` → `openai/ornith-1.5-k-m` | same **alias names**, backend `openai/ornith-1.5-35b-a3b@q4_k_m` |
| MiroFish report | `MIROFISH_REPORT_MODEL=ornith-1.5-k-m` in `~/mirofish-offline/.env` | `ornith-1.5-35b-a3b@q4_k_m` |
| `valedia_models.sh` | hermes `reason` + bionic/split templates → `ornith-1.5-k-m` | templates → Q4 id |
| CE-1 safety | charter names `ornith-1.5-k-m` | use LiteLLM alias `ornith-1.5-k-m` (still valid) or document Q4 backend |

Keep LiteLLM **alias** `ornith-1.5-k-m` so callers (MiroFish report via gateway, CE-1, Hermes reason) do not break — only the upstream Bionic model id changes.

## Preconditions (do not start until all true)
1. **MIROFISH-100-E2E** has `GROK_EXIT` (do not unload Ornith mid-swarm/report).
2. Q4_K_M download finished (~22.6 GB) and appears complete under LM Studio / Bionic models.
3. `curl -sS http://127.0.0.1:1234/v1/models` lists `ornith-1.5-35b-a3b@q4_k_m`.
4. Load Q4 in Bionic (and unload old Q5 `ornith-1.5-k-m` if VRAM tight — dual-load with Qwen still needs headroom).

## Scope (do)
1. Bak `~/.litellm/config.yaml` + `~/mirofish-offline/.env`.
2. Point LiteLLM backends for `ornith-1.5`, `ornith-1.5-k-m`, `mirofish`, `valedia` at `openai/ornith-1.5-35b-a3b@q4_k_m` (`api_base` stays `http://127.0.0.1:1234/v1`).
3. `launchctl kickstart -k gui/$(id -u)/com.burton.litellm-gateway` (or equivalent).
4. Set `MIROFISH_REPORT_MODEL=ornith-1.5-35b-a3b@q4_k_m` via `valedia_models.sh mf-set` / `--write` (or edit with bak). Update `valedia_models.sh` bionic+split template strings so future flips stay correct.
5. Restart MiroFish backend `:5001` only (do **not** stop `com.burton.ollama-mirofish`).
6. Prove:
   - `curl -sS http://127.0.0.1:4000/v1/models` still lists aliases
   - Tiny chat completion via LiteLLM alias `ornith-1.5-k-m` returns (proves alias→Q4)
   - `grep MIROFISH_REPORT_MODEL ~/mirofish-offline/.env` shows Q4 id
7. Note in Results: old Q5 keep on disk or delete? **Keep on disk** unless Burton says reclaim space — just unload from GPU.
8. Ponytail + GROK-STATUS. **Next=CE-1** (already Burton-go queued) unless CE-1 already running.

## Explicit non-goals
- Do not change Qwen sync model / split ontology path
- Do not flip trading levers / wallets / Poly LIVE
- Do not kill `ot-valedia-desk` / `valedia-glance`
- Do not start CE-1 inside this slice if parent already launched it — coordinate via GROK-NEXT-QUEUE

## Hard fence
Config/inference only. No venue orders. Screen `valedia-grok` only.

## Tools
RuFlo filtered MCP. CBM optional. Prove curls in Results.

## Deliverables
1. LiteLLM + MiroFish env + valedia_models.sh updated
2. Proof curls / tiny completion
3. `PONYTAIL-AUDIT-ORNITH-BIONIC-Q4-CUTOVER.md`
4. Results here + GROK-STATUS; Next=CE-1 (or Burton if CE-1 already done)

## Done when
Valedia/MiroFish report path resolves to Q4_K_M via existing aliases; Q5 not required on GPU; `GROK_EXIT:0`.

## Results

**2026-09-22 ~19:10 PT. GROK_EXIT:0.** Report path resolves to Q4_K_M through the existing aliases. Q5 file kept on disk. This session did not start CE-1.

### Preconditions
- MIROFISH-100-E2E already `GROK_EXIT:0`.
- Q4 file complete: `ornith-1.5-35b-a3b@q4_k_m`, 22,616,285,280 bytes (lms shows 22.62 GB), quant Q4_K_M, device Mac-Studio-M2 (`b5d4104b2292063288e2a5e7afe72e9b`), LM Link connected.
- `GET http://127.0.0.1:1234/v1/models` listed the id before load.

### What changed
- Bak: `~/.litellm/config.yaml.bak-ornith-q4-20260922-190336`, `~/mirofish-offline/.env.bak-ornith-q4-20260922-190336`, plus the switcher's `~/mirofish-offline/.env.bak-modelswitch-20260922-190454`.
- LiteLLM backends for `ornith-1.5`, `ornith-1.5-k-m`, `mirofish`, `valedia` → `openai/ornith-1.5-35b-a3b@q4_k_m`. `api_base` unchanged. Qwen and other aliases untouched. `launchctl kickstart -k gui/$(id -u)/com.burton.litellm-gateway` (new pid 91774).
- `MIROFISH_REPORT_MODEL=ornith-1.5-35b-a3b@q4_k_m`. Split sync stays `qwen3-8b-mf:latest` on `:11435`. `LLM_REPORT_BASE_URL` stays `:1234`.
- `scripts/valedia_models.sh` (workspace-v2 and the identical live copy): Hermes `reason` preset and bionic/split report templates → Q4 id. Ollama template still `ornith-1.5-mf:latest`.
- `scripts/mirofish_healthcheck.py` (both trees): report-id expectation matches Q4 so the checker does not false-FAIL.
- `~/bin/lms-thin-load.sh` `ORNITH_KEY` default → Q4 id.
- Loaded Q4 on Mac-Studio-M2 in 43.0s. `lms ps`: 22.62 GB, context 131072, parallel 4, no TTL.
- MiroFish Flask `:5001` restarted only (old pid 71639 → new pid 91859, cwd `~/mirofish-offline/backend`). `com.burton.ollama-mirofish` still pid 1893.

### Proof
- `GET http://127.0.0.1:4000/v1/models` HTTP 200. Aliases present: `coding flash-next gpt-oss-120b gpt-oss-120b-local mirofish ornith-1.5 ornith-1.5-k-m qwen3.8-27b qwen3.8-27b-obliterated qwen3.8-27b-uncensored valedia`.
- `POST http://127.0.0.1:4000/v1/chat/completions` model `ornith-1.5-k-m` HTTP 200 in 1.0s. Content `pong`. Finish `stop`.
- Bionic log 19:09:26: `[ornith-1.5-35b-a3b@q4_k_m] Generated prediction` with `model` and `system_fingerprint` `ornith-1.5-35b-a3b@q4_k_m`. Alias hit Q4.
- `grep ^MIROFISH_REPORT_MODEL= ~/mirofish-offline/.env` → `ornith-1.5-35b-a3b@q4_k_m`.

### Q5
Keep on disk. Do not delete. Local Q5 `ornith-1.5-k-m` (25.35 GB) was **left loaded**: Hermes gateway (pid 2453) was `GENERATING` on that direct `:1234` id at context 262144. Unloading would have cut the reply. TTL is 30m after it goes idle, which unloads it without a kill. The report path does not need it. A later direct `:1234` call of `ornith-1.5-k-m` will JIT-load that Q5 file again; the LiteLLM alias of the same name will not.

### Not done here
- Did not start CE-1. Queue already has Burton-go. Next = CE-1. Parent launches it.
- Did not change Qwen sync, Hermes live `model.default`, trading flags, wallets, or Poly LIVE.
- Screens `755.valedia-glance` and `783.ot-valedia-desk` not killed. `88557.valedia-grok` left up.
- Oil and weather stay fenced. No `create_order`.

### Tools
RuFlo `hooks_route` (grok-build, no swarm), `hooks_pre-task`, `hooks_pre-edit`, `hooks_pre-command`, `memory_search`, `hooks_post-edit`, `hooks_post-task`. CBM `list_projects`, `search_graph`, `check_index_coverage` (`scripts/valedia_models.sh` parse_partial lines 12–23, read directly). Ponytail `PONYTAIL-AUDIT-ORNITH-BIONIC-Q4-CUTOVER.md`.

`GROK_EXIT:0`
