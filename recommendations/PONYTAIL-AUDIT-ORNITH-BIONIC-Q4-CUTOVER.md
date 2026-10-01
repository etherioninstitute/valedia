# Ponytail audit — ORNITH-BIONIC-Q4-CUTOVER

**Mode:** `/ponytail` full. Config and load only.
**Fence:** no venue writers, no oil/weather unfence, no `create_order`. Screens `ot-valedia-desk` / `valedia-glance` not killed. `com.burton.ollama-mirofish` left at pid 1893.

## Applied

`reuse:` LiteLLM alias names. Four backends (`ornith-1.5`, `ornith-1.5-k-m`, `mirofish`, `valedia`) now `openai/ornith-1.5-35b-a3b@q4_k_m`. `api_base` stayed `http://127.0.0.1:1234/v1`. **Shipped.**

`reuse:` `valedia_models.sh mirofish-report --write` for the live env. Bionic and split templates plus the Hermes `reason` preset string now name the Q4 id. Ollama template stays `ornith-1.5-mf:latest`. Sync model stays `qwen3-8b-mf:latest`. **Shipped.**

`reuse:` `lms load` of the id already in the library. Weights are on Mac-Studio-M2 (LM Link), 22.62 GB, ctx 131072, parallel 4, no TTL. **Shipped.** Did not copy 22 GB onto Local.

`reuse:` `~/bin/lms-thin-load.sh` `ORNITH_KEY` default, so the next `mirofish` lane loads Q4 instead of putting Q5 back. **Shipped.** One default, not a new lane.

`reuse:` healthcheck report-id expectation, so the existing checker does not false-FAIL the new env. **Shipped.**

## Skipped

`yagni:` deleting the Q5 GGUF. Charter says keep it on disk. **Not deleted.** Local file `local/ornith-1.5-q5-k-m/Ornith-1.5-Q5_K_M.gguf` (25.35 GB). M2 also still has `ornith-1.5-35b-a3b@q5_k_m` (26.25 GB).

`yagni:` changing Hermes `model.default` (still `qwen3.8-27b-obliterated-k` at `:1234`) or the Qwen sync path. **Not shipped.**

`yagni:` a new proxy, alias table, or model-copy onto M1. **Not shipped.**

## Findings

`shrink:` Hermes gateway calls Bionic `:1234` with the raw id `ornith-1.5-k-m`. That is the Q5 file, not the LiteLLM alias. The alias path is proven. A direct `:1234` call of the old id still JIT-loads Q5. Point that caller at `:4000` alias `ornith-1.5-k-m` or at the Q4 id when that session is free.

**net:** four alias strings, two template strings, one env key, one loader default, one load. No new service.
