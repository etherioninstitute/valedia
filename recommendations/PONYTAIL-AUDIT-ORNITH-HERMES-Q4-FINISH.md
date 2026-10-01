# Ponytail audit — ORNITH-HERMES-Q4-FINISH

**Mode:** short. Config and inference only.
**Fence:** no `--live`, no wallets, no Poly/Kalshi writes, no oil/weather unfence. Screens `ot-valedia-desk` / `valedia-glance` not killed. Q5 and mmproj files kept.

## Applied

`reuse:` LiteLLM alias `ornith-1.5-k-m` already pointed at `openai/ornith-1.5-35b-a3b@q4_k_m`. Hermes cron and delegation now call that alias on `:4000` instead of a new model id. **Shipped.**

`reuse:` the existing `providers` + `custom_providers` pair. Added `custom:litellm` the same way `custom:lms-local` is listed twice. Key is `${LITELLM_MASTER_KEY}` / `key_env`, not a pasted secret. **Shipped.**

`reuse:` `lms unload` + `lms load` of the library key. Second load pinned identifier `ornith-1.5-35b-a3b@q4_k_m` onto today's NIMROD file (21713463040 bytes). Context 131072, parallel 4, no TTL. **Shipped.**

`reuse:` cron already re-reads `config.yaml` and `~/.hermes/.env` each tick. No gateway restart. **Shipped.**

## Skipped

`yagni:` editing LiteLLM aliases, `model.default`, MiroFish, or CE-1 callers. **Not shipped.**

`yagni:` deleting the Q5 GGUF or either mmproj file. **Not deleted.** Local Q5 unloaded from the GPU only.

`yagni:` copying the new GGUF onto Mac-Studio-M2. The M2 catalog entry is still the old 22616285280-byte object. Serving path is the NIMROD file on M1. **Not copied.**

`yagni:` a Hermes gateway kickstart. Pid 2453 stayed up. **Not restarted.**

## Findings

`shrink:` `lms ps` size for this Q4 id still prints 22616285280. The open file is 21713463040. Trust `lsof` / `stat`, not the catalog size, until LMS reindexes.

`shrink:` direct `:1234` id `ornith-1.5-k-m` is still the Q5 file. Hermes cron and delegation no longer call it. Anything else that posts that id to `:1234` will JIT-load Q5 again.

**net:** one Hermes provider block, two field retargets, one `.env` key name, one unload of Local Q5, one load of today's Q4 file. No new service.
