# HERMES CLEANUP YES-PACK — 2026-09-30

**GROK_EXIT:0**

Charter `recommendations/GROK-CHARTER-HERMES-CLEANUP-YES-PACK-2026-09-30.md` applied on disk at ~16:06 PT. Profile folders were not deleted. LiteLLM `:4000` was left running. LMS was not loaded or unloaded. Life Matrix/Signal LaunchAgents were not bounced.

The running life gateway has not re-read this write. `ai.hermes.gateway` pid **74698** started **14:57:50 PT**, before the 16:06 yaml. MoA-off and the mlx cron pins are on disk for the next authorized life reload. This pack did not bounce that agent.

## A. Unknown provider (`custom:ollama-local`)

KEEP desk defaults were already `custom:lms-local` / `ornith-1.5-35b-a3b-mlx`. No default-provider edit.

| Profile | Provider | Default |
|---|---|---|
| root | `custom:lms-local` | `ornith-1.5-35b-a3b-mlx` |
| valedia | `custom:lms-local` | `ornith-1.5-35b-a3b-mlx` |
| valedia-cto | `custom:lms-local` | `ornith-1.5-35b-a3b-mlx` |
| valedia-trading-scout | `custom:lms-local` | `ornith-1.5-35b-a3b-mlx` |
| valedia-trading-decider | `custom:lms-local` | `ornith-1.5-35b-a3b-mlx` |
| valedia-trading-auditor | `custom:lms-local` | `ornith-1.5-35b-a3b-mlx` |

ARCHIVED profiles stayed on `custom:ollama-local` (charter: leave archived as-is). They are hidden from the BOTS roster via `profile.yaml`, so the unregistered provider is not a desk default.

| Profile | Still on disk | Hidden |
|---|---|---|
| scout | `custom:ollama-local` / `qwen3.8-27b-obliterated:q6_K` | true |
| decider | `custom:ollama-local` / `ornith:9b` | true |
| auditor | `custom:ollama-local` / `deepseek-r1:32b` | true |
| uncensored | `custom:ollama-local` / `orcarouter/Qwen3.8-27B-Uncensored:q6_K` | true |
| ugft | `custom:ollama-local` / `ornith-ugft:latest` | true |
| code-cloud | `custom:ollama-local` / `glm-5.3:cloud` | true |
| omni-code | `custom:ollama-local` / `omni-qwen-122b:latest` | true |

Root `model_aliases` (`ornith`, `qwen-uncensored`, `qwen-27b`) still name `custom:ollama-local`. They are aliases, not `model.provider`. Left as they were.

## B. Ollama ghost picker entries

Root live `custom_providers` was already `[]`. The 2026-09-25/09-30 stash stayed in place (two `:11434` entries, one parked LMS stub, litellm `:4000`).

KEEP profiles: every `custom_providers` entry whose `base_url` is `127.0.0.1:11434` moved into `_hermex_picker_hidden_custom_providers`. `:1234` LMS rows and `:8081` Flash-Next rows stayed in the live list. `:11435` was not matched.

| Profile | Live after | Stashed `:11434` |
|---|---|---|
| valedia | LM Studio Bionic `:1234` | Local, Flash-Next (ollama daemon) |
| valedia-cto | Flash-Next `:8081` | Local |
| valedia-trading-scout | Flash-Next `:8081` | Local |
| valedia-trading-decider | `[]` | Local |
| valedia-trading-auditor | `[]` | Local |

Trading yaml change is this stash only. Compared before/after, these sections matched: `model`, `moa`, `approvals`, `hooks`, `terminal`, `tools`, `agent`, `delegation`, `cron`, `providers`, `plugins`. `block-tcc-appdata.py` and `terminal.cwd` → workspace-v2 still present on CTO and the three trading profiles. No trading LaunchAgent bounce.

## C. Root MoA

| Field | Before | After |
|---|---|---|
| `moa.enabled` | true | **false** |
| aggregator model (top + `presets.default`) | `ornith-1.5-35b-a3b` | **`ornith-1.5-35b-a3b-mlx`** |
| reference model | `qwen/qwen3.6-35b-a3b` | unchanged |

Profile-scope: root `config.yaml` only. Trading MoA blocks were not edited.

## D. Root main model

`model.default` was already `ornith-1.5-35b-a3b-mlx` under `custom:lms-local` when this pack started (the 14:56 status note was stale). Selectors that still named the bare id, and that override or accompany that default, were retargeted:

| Selector | Before | After |
|---|---|---|
| `model.default` | `ornith-1.5-35b-a3b-mlx` | unchanged |
| `providers.custom:lms-local.model` | bare | **mlx** |
| `auxiliary.approval.model` | bare | **mlx** |
| `delegation.model` | bare | **mlx** |
| `cron.model` | bare | **mlx** |
| `cron/jobs.json` model pins | 9 × bare | **9 × mlx** |

`chief-of-staff-daily` has no per-job model; it inherits `cron.model`. The models-map key `ornith-1.5-35b-a3b` (context 131072) was left as a catalog stub. The mlx map entry was already present. Context length was not rewritten.

Root diff is 14 lines: six `model:` retargets plus `moa.enabled: false`. No `api_key` line changed.

## E. Kanban IdeaInbox (Default board, 17 cards)

`hermes kanban archive` on all 17. Status after: `archived=17`, visible=0. Not purged (`--rm` not used).

Done (8, 2026-08-28 and 2026-08-30): Remotion, Grounded Insider TTS, analyze-video, Hormozi, UGC avatar, Scout email, Decider email, Auditor email.

Ready IdeaInbox PROPOSE (9): Meta Ads, daily trend scraper, Hermes+Box, Yang-Mills swarm, Open-Higgsfield, sol-foreman install, TypeSafe Jev, Magnitude, Ornith-1.5-9B distil.

Paperclip stayed external. `node` pid **55745** still listening on `127.0.0.1:3100`. No Paperclip rows were written into this board.

Backup: `~/.hermes/kanban.db.bak-20260930-cleanup-yes-pack`.

## F. LiteLLM

`python3.1` pid **943** still listening on `127.0.0.1:4000`. Root stash still contains the `litellm` → `:4000` entry. Hide not reversed.

## Hide proof (folders kept)

`ui_meta.hermes-bots.hidden: true` and `_ui_meta_revisions.hermes-bots: 1` on:

- `~/.hermes/profiles/scout/profile.yaml` (description kept)
- `decider`, `auditor`, `ugft`, `code-cloud`, `omni-code` (same)
- `uncensored/profile.yaml` created (that profile had no `profile.yaml`; the folder and `config.yaml` were already there)

KEEP profiles were checked and are not `hidden: true`.

Desktop reads this from `profiles.list` (`profile.yaml` signature). Hiding is roster display; the profile directories are still on disk.

## `lms ps` (no load, no unload)

| Identifier | Status | Size | Context | Parallel |
|---|---|---|---|---|
| `ornith-1.5-35b-a3b-mlx` | IDLE | 19.53 GB | 65536 | 4 |
| `qwen/qwen3.6-35b-a3b` | GENERATING | 20.43 GB | 262144 | 4 |

Same two identifiers as before the edit. Qwen was already generating; it was left alone.

## Bounce

**None.**

| LaunchAgent | pid | Started |
|---|---|---|
| `ai.hermes.gateway` | 74698 | 2026-09-30 14:57:50 PT |
| `ai.hermes.gateway-valedia-cto` | 76349 | 2026-09-30 14:58:20 PT |
| `ai.hermes.gateway-valedia-trading-scout` | 76364 | same window |
| `ai.hermes.gateway-valedia-trading-decider` | 76365 | same window |
| `ai.hermes.gateway-valedia-trading-auditor` | 76348 | same window |
| `com.burton.hermes-signal` | 908 | 2026-09-29 14:48:36 PT |
| `com.burton.matrix-tunnel` | 925 | 2026-09-29 14:48:36 PT |

Pids matched before and after the yaml write. `com.parantoux.hermes-webui` pid 913 was not touched. No `hermes gateway --replace`.

## Backups

- `~/.hermes/config.yaml.bak-20260930-cleanup-yes-pack`
- `~/.hermes/profiles/valedia/config.yaml.bak-20260930-cleanup-yes-pack`
- `~/.hermes/profiles/valedia-cto/config.yaml.bak-20260930-cleanup-yes-pack`
- `~/.hermes/profiles/valedia-trading-{scout,decider,auditor}/config.yaml.bak-20260930-cleanup-yes-pack`
- `~/.hermes/profiles/{scout,decider,auditor,ugft,code-cloud,omni-code}/profile.yaml.bak-20260930-cleanup-yes-pack`
- `~/.hermes/cron/jobs.json.bak-20260930-cleanup-yes-pack`
- `~/.hermes/kanban.db.bak-20260930-cleanup-yes-pack`

`uncensored/profile.yaml` was new; there is no bak for it.

## Grep / check proof

- Root `moa.enabled` false; aggregator and preset aggregator `ornith-1.5-35b-a3b-mlx`; reference `qwen/qwen3.6-35b-a3b`.
- Root `cron.model`, `delegation.model`, `auxiliary.approval.model`, `providers.custom:lms-local.model` are the mlx id.
- `jobs.json`: 9 model fields are the mlx id; 0 remain bare; `chief-of-staff-daily` inherits.
- KEEP live `custom_providers` contain no `:11434`.
- Archived `profile.yaml` `hidden: true`; seven profile directories still exist.
- Kanban `archived=17`.
- TCC hook string still in CTO and trading yaml.
- LiteLLM `:4000` listening; Paperclip `:3100` listening.

**GROK_EXIT:0**
