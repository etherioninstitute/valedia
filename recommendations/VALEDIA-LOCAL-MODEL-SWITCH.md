# Valedia local model switch (M1-only 2026-09-21)

**Goal:** one command to load/offload local models on the **M1 Ultra 128GB**. M2 is parked/standby — not required for day-to-day Hermes/Valedia/MiroFish.

Physical load/offload is separate from Hermes `model.default`.

## Two layers

| Layer | Tool | What it does |
|-------|------|----------------|
| **RAM load** | `~/bin/lms-thin-load.sh <lane>` | Unload/load GGUFs in Bionic/LMS (`lms`) on **M1 Local** |
| **Agent default** | `scripts/valedia_models.sh hermes <id>` | Sets Hermes `model.default` to match what you loaded |

Agents call `:1234` (or LiteLLM `:4000`) with a model id. Pick the **lane** first.

## Load lanes (`lms-thin-load.sh`) — M1 128GB

| Lane | Loads | PARALLEL / ctx | When |
|------|-------|----------------|------|
| `solo` / `ornith` / `mirofish` / `valedia` / `cos` / `cron` | Ornith **Q5** Local `ornith-1.5-k-m` | p4 / 131072 | MiroFish + Valedia CoS (shared) |
| `dual` | Ornith **Q5** + Local companion ``qwen/qwen3.8-27b` (Local; obliterated fallback)` | Ornith p4/131072 + Qwen p2/32768 | CoS + coding/vision together |
| `coding` / `hermes` / `companion` | Local Qwen obliterated | p2 / 65536 | Coding without Ornith |
| `turbo` / `ruvocal` | twin-turbo Local (fallback) | p2 / 65536 | RuVocal / fast coder |
| `flash` / `scout` | `ud` Flash-Next **alone** | p1 / 131072 | Hard Scout |
| `gptoss` | gpt-oss **alone** | p1 / 65536 | Big reasoner |
| `status` / `unload` / `roster` | ops | — | inspect / clear / print keys |

### Co-load rules (128GB)

- **YES:** Ornith Q5 + one ~16–22GB Local Qwen (`dual`).
- **NO:** Flash-Next or gpt-oss with anything else.
- **NO:** dependence on M2 / `qwen/qwen3.8-27b` (M2-only in catalog today). Import Unsloth UD-Q4_K_XL to Local when you want a clean Qwen key; until then companion = obliterated.

```bash
lms-thin-load.sh roster
lms-thin-load.sh mirofish   # Ornith Q5
lms-thin-load.sh dual       # Q5 + Local Qwen
lms-thin-load.sh coding
lms-thin-load.sh unload
```

## LiteLLM (`~/.litellm/config.yaml`) → `http://127.0.0.1:1234/v1`

| Alias | Bionic model |
|-------|----------------|
| `ornith-1.5`, `ornith-1.5-k-m`, `mirofish`, `valedia` | `ornith-1.5-k-m` |
| `qwen3.8-27b`, `coding`, `qwen3.8-27b-obliterated` | ``qwen/qwen3.8-27b` (Local; obliterated fallback)` |
| `qwen3.8-27b-uncensored` | twin-turbo Local |
| `flash-next` | `ud` |
| `gpt-oss-120b` / `gpt-oss-120b-local` | gpt-oss routes |

Listen: **`:4000`** (not OpenTerminal `:4010`).

## Config lanes (`valedia_models.sh`)

Unchanged surface: Hermes `model.default` should usually be `ornith-1.5-k-m` after `solo`/`mirofish`/`valedia`. After `dual`, default can stay Ornith; select companion by id for coding calls.

Trading flags remain **out of scope**.

## Rules

1. Backup configs before write.
2. Refuse unknown ids not in `lms ls` Local.
3. Never change trading fences / API keys here.
4. M2 = standby only; preferred device Local/M1 if Link stays on.
5. RAM: `solo` = Q5; `dual` = Q5 + companion on **128GB** (M2’s old Q5+17 refuse does not apply here).

## Success

- `lms-thin-load.sh mirofish|valedia|dual|coding` works without LMS UI.
- LiteLLM aliases resolve on M1 localhost only.
- M2 not required for profit-path day-to-day.
