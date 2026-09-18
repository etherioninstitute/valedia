# Valedia local model switch (Burton 2026-09-18)

**Goal:** one command to see and switch the local models Valedia depends on — without touching trading fences or venue keys.

## Lanes

| Lane | Config surface | Endpoint |
|------|----------------|----------|
| **Hermes / Valedia chat** | `~/.hermes/config.yaml` → `model.default` + `custom:lms-local` | LMS `:1234` |
| **RuVocal** | RuVocal / LMS loaded model (turbo-coder) | LMS `:1234` |
| **MiroFish** | env / runner: sync `LLM_MODEL_NAME`, report `MIROFISH_REPORT_MODEL` | Ollama `:11435` |
| **neural-trader** | CLI only (no LLM agent personas) | native NAPI; optional later LMS remap |

Trading flags (`SPOT_RECONCILE_HTTP`, writers, etc.) are **out of scope** for this switcher.

## CLI

```bash
scripts/valedia_models.sh list          # LMS + MiroFish tags
scripts/valedia_models.sh show          # current Hermes default + MiroFish models
scripts/valedia_models.sh hermes <id>   # set Hermes model.default (backup first)
scripts/valedia_models.sh mirofish-sync <tag> [--write]    # print export; --write backups then sets LLM_MODEL_NAME
scripts/valedia_models.sh mirofish-report <tag> [--write]  # print export; --write backups then sets MIROFISH_REPORT_MODEL
```

Presets (names only — resolve to real LMS/Ollama ids at runtime):

| Preset | Hermes (LMS) | MiroFish sync | MiroFish report |
|--------|--------------|---------------|-----------------|
| `chat` | `qwen3.8-27b-obliterated-k` | `qwen3-8b-mf:latest` | `ornith-1.5-mf:latest` |
| `reason` | `ornith-1.5-k-m` | unchanged | `ornith-1.5-mf:latest` |
| `turbo` | twin-turbo / coder id if loaded | unchanged | unchanged |

## Rules

1. Always backup config before write (`*.bak-modelswitch-<ts>`).
2. Refuse unknown ids not returned by `list`.
3. Never change `EXEC_DRY_RUN`, writers, or API keys.
4. RuVocal: document “load this in LMS” if API can’t hot-swap; don’t fight LMS UI.
5. Grok Build may harden presets + RuVocal path in a later slice; v1 = Hermes + MiroFish show; NT-1 leftover = `mirofish-sync` / `mirofish-report` (`--write` backups `$MIROFISH_ENV`, default `~/mirofish-offline/.env`).

## Success

Burton can switch Hermes default in one command and see MiroFish + LMS inventory without opening three apps.
