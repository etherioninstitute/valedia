# LMS Catalog → Valedia pins (2026-09-30)

Verified via `GET http://127.0.0.1:1234/api/v0/models` at write time.

## Catalog

| Display | LMS id | Role |
|---|---|---|
| Ulam 1 | `ulam-1` | available VLM (catalog only; do not make desk default) |
| Qwen3.8 27B | `qwen/qwen3.8-27b` | Scout/tick-assist preferred |
| Ornith 1.5 35B A3B | `ornith-1.5-35b-a3b-mlx` | desk default (CTO/Decider/Auditor/Valedia) |
| Ornith 1.5 9B | `ornith-1.5-9b-mlx` | paper-tick seasoning only |
| Qwen3.6 35B A3B | `qwen/qwen3.6-35b-a3b` | available heavy alternate (catalog; do not swap desk off Ornith) |
| embeddings | `text-embedding-nomic-embed-text-v1.5` | keep |

## Live verification

```
live ids: ['ornith-1.5-9b-mlx', 'ornith-1.5-35b-a3b-mlx', 'qwen/qwen3.6-35b-a3b', 'qwen/qwen3.8-27b', 'ulam-1', 'text-embedding-nomic-embed-text-v1.5']
missing_from_live: none
```

## Id retarget map (live pins)

- `ornith-1.5-35b-a3b` → `ornith-1.5-35b-a3b-mlx` (primary `model.default` / `providers.custom:lms-local.model` / approval)
- `ornith-1.5-35b-a3b@q4_k_m` → kept as Hermes models catalog alias key; primary is mlx
- `ornith-1.5-9b` → `ornith-1.5-9b-mlx` (seasoning)
- bare `qwen3.8-27b` (lms-local / tick-assist) → `qwen/qwen3.8-27b`

## Files touched

- `~/.hermes/profiles/valedia/config.yaml`
- `~/.hermes/profiles/valedia-trading-scout/config.yaml`
- `~/.hermes/profiles/valedia-trading-decider/config.yaml`
- `~/.hermes/profiles/valedia-trading-auditor/config.yaml`
- `~/.hermes/profiles/valedia-cto/config.yaml`
- `~/.openclaw/workspace-v2/scripts/integrations/ornith_load_policy.py`
- `~/.openclaw/workspace-v2/scripts/integrations/ornith_seasoning.py`
- `~/.openclaw/workspace-v2/scripts/integrations/test_ornith_seasoning.py`
- `~/.openclaw/workspace-v2/scripts/valedia_silent_failure_probes.py`
- `~/.openclaw/workspace-v2/scripts/integrations/crypto_paper_run.py`
- `~/.openclaw/workspace-v2/scripts/integrations/test_crypto_paper_safety.py`
- `~/.openclaw/workspace-v2/data/crypto-paper/rules.json`
- `~/.openclaw/workspace-v2/scripts/valedia_models.sh`
- `~/.openclaw/workspace-v2/scripts/mirofish_healthcheck.py`
- `~/.openclaw/workspace-v2/data/lms_catalog_current.json`
- `~/.openclaw/workspace-v2/recommendations/LMS-CATALOG-VALEDIA-2026-09-30.md`

## Fence

- Did **not** `lms load` / unload / restart LMS
- Did **not** restart Hermes gateways or Paperclip
- Did **not** place orders or widen live allowlist / `create_order`
- Did **not** set Ulam / Ornith 9B / Qwen3.6 as desk money/default
- Did **not** edit `.bak*` files (only created new timestamped backups for scripts)

## Intentionally left stale

- Ollama / `ornith-1.5:latest` / `ornith:9b` cron leftovers in Hermes configs (not active `model.default`)
- `model_aliases.qwen-uncensored` → `qwen3.8-27b-obliterated-k` (not in current LMS catalog; not desk default)
- Historical memory MD untouched
