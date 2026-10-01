# ORNITH-HERMES-Q4-FINISH — 2026-09-23

Finish slice after **ORNITH-BIONIC-Q4-CUTOVER** (DONE 2026-09-22). LiteLLM aliases already point at Q4. Hermes cron + delegation still hit Q5 via direct `:1234`.

## Goal
Hermes `cron.model` + `delegation.model` leave Q5 and resolve through **LiteLLM `:4000` alias `ornith-1.5-k-m`** → backend `openai/ornith-1.5-35b-a3b@q4_k_m` on Bionic `:1234`. Prove next Hermes/LiteLLM chat fingerprint is Q4, not Q5. Reload LMS Q4 if in-memory copy is stale vs today's GGUF.

## Map facts (2026-09-23 ~14:35–14:40 PT) — do not re-discover unless drift
- **Host running this shell:** Burtons-Mac-Studio-**M1**. Bionic pid owns `127.0.0.1:1234`. LiteLLM pid owns `:4000`.
- **mmproj NOT used in Valedia** — DO NOT change mmproj paths/ids/loads.
- **New Q4 GGUF:** `/Volumes/NIMROD_SSD/models/lms-library/ornith-ai/Ornith-1.5-35B-A3B-GGUF/Ornith-1.5-35B-Q4_K_M.gguf` (~20.22 GiB / 21713463040 bytes, mtime **14:06 PT today**).
- **LMS at map time:** `ornith-1.5-35b-a3b@q4_k_m` LOADED on **Mac-Studio-M2** (LM Link, 22.62 GB); `ornith-1.5-k-m` LOADED **Local/M1** from `.../local/ornith-1.5-q5-k-m/Ornith-1.5-Q5_K_M.gguf` (25.35 GB) and was **GENERATING** — do not kill mid-token; wait idle or TTL.
- **LiteLLM `~/.litellm/config.yaml`:** aliases `ornith-1.5`, `ornith-1.5-k-m`, `mirofish`, `valedia` → `openai/ornith-1.5-35b-a3b@q4_k_m` @ `:1234`. **PRESERVE alias names.** Only fix if backend wrong.
- **REAL GAP — Hermes `~/.hermes/config.yaml`:**
  - `cron.model: ornith-1.5-k-m` + `cron.model_provider: custom:lms-local` → direct `:1234` → **Q5** GGUF `local/ornith-1.5-q5-k-m`
  - `delegation.model: ornith-1.5-k-m` + `provider: custom:lms-local` + `base_url: http://127.0.0.1:1234/v1` → same Q5 path
  - Hermes has **no** LiteLLM `:4000` custom provider yet (only a `threshold_tokens: 40000` hit for "4000").
- Prior charter `ORNITH-BIONIC-Q4-CUTOVER.md` Results already note Hermes left on Q5 intentionally during that slice.

## Prefer target
Retarget Hermes cron + delegation to **LiteLLM `:4000`** alias `ornith-1.5-k-m` (same name CE-1 / Vaughan / MiroFish use). Do **not** hardcode Q4 LMS id into Hermes callers if the alias works — keeps one cutover surface.

Fallback if LiteLLM auth/provider wiring is blocked: point cron/delegation model id at `ornith-1.5-35b-a3b@q4_k_m` on `custom:lms-local` / `:1234` (still leave LiteLLM aliases alone). Document which path in Results.

## Steps
1. **Idle check:** `lms ps`. If `ornith-1.5-k-m` (Local) is GENERATING, wait until IDLE (or TTL unload). Do not unload under an active Hermes generation.
2. **Bak:** `cp -a ~/.hermes/config.yaml ~/.hermes/config.yaml.bak-ornith-hermes-q4-YYYYMMDD-HHMMSS` (and LiteLLM only if you must touch it).
3. **Hermes config (primary path):**
   - Add a `custom:litellm` (or equivalent) provider: `base_url: http://127.0.0.1:4000/v1`, api_key from env/`LITELLM_MASTER_KEY` pattern Hermes already uses for secrets, `discover_models: true`, model alias `ornith-1.5-k-m`.
   - Set `cron.model: ornith-1.5-k-m` + `cron.model_provider: custom:litellm` (name as implemented).
   - Set `delegation.model: ornith-1.5-k-m`, `delegation.provider: custom:litellm`, `delegation.base_url: http://127.0.0.1:4000/v1`.
   - Leave `model.default` / main chat default alone unless it also forces Q5 (today default is `qwen3.8-27b-obliterated-k` — do not change).
   - Mirror the same provider block under `custom_providers:` if Hermes requires both maps (match existing `custom:lms-local` dual-listing pattern).
4. **LMS Q4 reload (stale in-memory risk):** Because GGUF mtime is **today 14:06 PT**, unload + reload `ornith-1.5-35b-a3b@q4_k_m` so Bionic picks up the new file. Prefer the device that actually serves Valedia traffic (M1 Bionic `:1234` via LM Link to M2 is fine if that is how Q4 is served today). Do **not** touch `ornith-1.5-mmproj`. Do **not** delete Q5 or mmproj from disk. After Hermes is retargeted, unload Local Q5 `ornith-1.5-k-m` if still loaded and idle (VRAM reclaim) — keep files on disk.
5. **Restart / pick-up:** If Hermes gateway caches config, use the least-invasive reload Hermes already supports (document command). Prefer not killing `ot-valedia-desk` / `valedia-glance`. Do not restart Bionic unless required for reload.
6. **LiteLLM:** Leave alias map as-is unless a proof fails; if restart needed: `launchctl kickstart -k gui/$(id -u)/com.burton.litellm-gateway`.
7. **Ponytail** short audit + update `GROK-STATUS.md` / `GROK-NEXT-QUEUE.md`.

## Proof (all required)
1. `grep -nE 'cron:|delegation:|model_provider|base_url|ornith-1.5' ~/.hermes/config.yaml` shows cron+delegation → `:4000` / litellm provider (or documented Q4 LMS fallback).
2. `lms ps` after reload: Q4 loaded; Local Q5 either unloaded or clearly unused by Hermes.
3. Tiny chat:
   - `POST http://127.0.0.1:4000/v1/chat/completions` model `ornith-1.5-k-m` → HTTP 200; Bionic/log `system_fingerprint` / model field = `ornith-1.5-35b-a3b@q4_k_m` (not `ornith-1.5-k-m` Q5 path).
   - Optional: Hermes cron/delegation smoke if there is a safe one-shot (no trading tools).
4. Confirm next llama-server / Bionic log path for that completion is **not** `.../local/ornith-1.5-q5-k-m/Ornith-1.5-Q5_K_M.gguf`.
5. Results + `GROK_EXIT:0` (or non-zero with blocker named).

## Cannot-do / Hard fence
- No `--live`. No wallets. No Poly / Kalshi mutations / `create_order`.
- No oil / weather unfence. No treasury / venue money POSTs.
- **No mmproj** path/id/load changes.
- **Do not delete** Q5 GGUF or mmproj from disk (unload from GPU only).
- Do not kill screens `ot-valedia-desk` / `valedia-glance` except if charter explicitly required (it is not).
- Do not mass-edit MiroFish / CE-1 callers that already use LiteLLM aliases.
- Config / inference cutover only — prefer no app code unless Hermes provider registration requires a tiny script already in-tree.
- This shell may inherit `POLYMARKET_US_LIVE=1` / `ETORO_LIVE=1` — do **not** flip them.

## Tools
RuFlo filtered MCP. CBM optional. Shell + `lms` + curl. Screen `valedia-grok` only for this Build.

## Deliverables
1. Hermes config bak + edit (LiteLLM `:4000` preferred)
2. Q4 unload+reload proof; Q5 unload if idle
3. Proof curls / fingerprint / log path ≠ q5
4. `PONYTAIL-AUDIT-ORNITH-HERMES-Q4-FINISH.md` (short)
5. Results section below + GROK-STATUS Next=Burton stop (unless a listed follow-up)

## Done when
Hermes cron + delegation no longer JIT-load Q5 via direct `:1234` `ornith-1.5-k-m`; traffic goes LiteLLM `:4000` → Q4 (or documented LMS Q4 fallback); proof fingerprint is Q4; `GROK_EXIT:0`.

## GROK_EXIT format
End the live log with exactly:
```
GROK_EXIT:0
```
or `GROK_EXIT:1` with a one-line blocker (e.g. Hermes still GENERATING after wait, M2 LM Link down, LiteLLM auth refused). Parent relays via exit-ping — do not wait for parent.

## Results

**2026-09-23 14:56 PT. GROK_EXIT:0.** Hermes cron and delegation resolve `ornith-1.5-k-m` through LiteLLM `:4000` → `ornith-1.5-35b-a3b@q4_k_m`. The proof completion's `system_fingerprint` is that Q4 id. The llama-server process has today's NIMROD Q4 GGUF open, not the Q5 file. Local Q5 is unloaded. Q5 and both mmproj files are still on disk.

### Wait
`lms ps` at 14:40 PT: Local `ornith-1.5-k-m` was GENERATING, then PROCESSINGPROMPT. Idle at 14:47 PT. No unload during that run.

### Bak
- `~/.hermes/config.yaml.bak-ornith-hermes-q4-20260923-144832`
- `~/.hermes/.env.bak-ornith-hermes-q4-20260923-144832`
- LiteLLM config not edited. Aliases `ornith-1.5`, `ornith-1.5-k-m`, `mirofish`, `valedia` still `openai/ornith-1.5-35b-a3b@q4_k_m` at `http://127.0.0.1:1234/v1`.

### Hermes
`providers.custom:litellm` and a matching `custom_providers` entry named `litellm`: `base_url: http://127.0.0.1:4000/v1`, `key_env: LITELLM_MASTER_KEY`, `api_key: ${LITELLM_MASTER_KEY}`, model `ornith-1.5-k-m`. The raw key is not in `config.yaml` (ref count 2). `LITELLM_MASTER_KEY` appended to `~/.hermes/.env` (mode 600).

- `cron.model: ornith-1.5-k-m`, `cron.model_provider: custom:litellm`
- `delegation.model: ornith-1.5-k-m`, `delegation.provider: custom:litellm`, `delegation.base_url: http://127.0.0.1:4000/v1`
- `model.default` left `qwen3.8-27b-obliterated-k` on `custom:lms-local` / `:1234`

Fresh-process resolve: `custom:litellm` → `http://127.0.0.1:4000/v1`, key matches the shell master key (length 54). Cron re-reads `config.yaml` every tick and calls `load_hermes_dotenv` before the agent run, so the running gateway picks this up without a restart. Gateway pid **2453** left up. No `launchctl kickstart` of Hermes or LiteLLM.

### Q4 reload
Disk file `/Volumes/NIMROD_SSD/models/lms-library/ornith-ai/Ornith-1.5-35B-A3B-GGUF/Ornith-1.5-35B-Q4_K_M.gguf` is **21713463040** bytes, mtime **2026-09-23 14:06:35 PT**. The LMS catalog still prints **22616285280** (the pre-overwrite size). That catalog number is stale.

1. Unload + `lms load -y ornith-1.5-35b-a3b@q4_k_m -c 131072 --parallel 4 --gpu max` reloaded the **M2** copy in 6.08s and still reported 22616285280. That was the old resident, not today's file.
2. Unloaded that M2 instance. `lms load -y ornith-1.5-35b-a3b -c 131072 --parallel 4 --gpu max --identifier ornith-1.5-35b-a3b@q4_k_m` (2m 2s). Bionic log 14:52:02: `loading model '/Volumes/NIMROD_SSD/models/lms-library/ornith-ai/Ornith-1.5-35B-A3B-GGUF/Ornith-1.5-35B-Q4_K_M.gguf'`. `lsof` on llama-server pid 19367 holds that path. `stat` of the open file is **21713463040**. Identifier stays `ornith-1.5-35b-a3b@q4_k_m`. Context 131072, parallel 4, no TTL, device Local (M1). `lms ps` still displays the stale 22616285280 catalog size.

Local Q5 `ornith-1.5-k-m` unloaded at 14:55 PT after it was idle. Files kept: `local/ornith-1.5-q5-k-m/Ornith-1.5-Q5_K_M.gguf` and `gguf/ornith-1.5/Ornith-1.5-Q5_K_M.gguf` (both 25347532352). A later direct `:1234` call of id `ornith-1.5-k-m` can JIT-load that Q5 file again. Hermes cron and delegation no longer use that path.

mmproj: no id edit and no delete. `local/ornith-1.5-mmproj/Ornith-1.5-mmproj.gguf` (902822016) and the sibling `mmproj-Ornith-1.5-35B-BF16.gguf` (902822240) are still on disk. LMS attached the sibling projector on the Q4 llama-server command (`--mmproj` that sibling path). The separate `ornith-1.5-mmproj` id was not loaded. The previous resident Q4 was already `vision: true`.

### Proof
- `POST http://127.0.0.1:4000/v1/chat/completions` model `ornith-1.5-k-m` → HTTP **200** in 7.81s. Response `model` is the alias `ornith-1.5-k-m`. Response `system_fingerprint` is `ornith-1.5-35b-a3b@q4_k_m`.
- Bionic log 14:54:11: `[ornith-1.5-35b-a3b@q4_k_m] Generated prediction` with `model` and `system_fingerprint` `ornith-1.5-35b-a3b@q4_k_m`. Zero `Q5_K_M` lines in that slice. Visible `content` was empty: `finish_reason=length`, all 16 completion tokens were reasoning (`reasoning_content` starts "The user is asking me to reply with a single word \"pong\"").
- Second POST after Q5 unload: HTTP 200 in 9.66s, same fingerprint. `lms ps` stayed on the Q4 id only (Q5 did not JIT-load).
- Open model path is the Q4 GGUF above, not `local/ornith-1.5-q5-k-m/Ornith-1.5-Q5_K_M.gguf`.

### Fence
Screens `783.ot-valedia-desk` and `17171.valedia-glance` not killed. `12812.valedia-grok` left up. `POLYMARKET_US_LIVE` stayed 1. `ETORO_LIVE` stayed 1. No `--live` flip. No wallet, Poly, or Kalshi mutation. Oil and weather not unfenced. Bionic not restarted. LiteLLM pid 91774 left up.

### Tools
Shell, `lms`, curl. No RuFlo swarm. Ponytail `PONYTAIL-AUDIT-ORNITH-HERMES-Q4-FINISH.md`.

`GROK_EXIT:0`
