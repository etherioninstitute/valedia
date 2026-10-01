# URGENT P0 ADDENDUM — Cron model scrub (same stamp: HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30)

**Injected:** 2026-09-30 16:46:28 PDT
**Authority:** Burton screenshot — Hermes Scheduled jobs Important-mail monitor Last run failed: HTTP 400 Invalid model identifier `ornith-1.5-35b-a3b`. UI shows `ornith-1.5-35b-a3b-mlx` but runtime still hits bare id. Burton: scheduled jobs are all broken.
**MUST be done in the SAME stamp** `recommendations/HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30.md` (section **Cron model scrub** with before/after ids). Do not defer to a follow-up charter.

## P0 APPLY (life/root only unless trading also bare-broken)

1. **Audit ALL Hermes cron jobs** — root `~/.hermes/cron/jobs.json` (and any Desktop scheduled-jobs mirror if present). Find model fields still bare `ornith-1.5-35b-a3b` **or** other dead ids. Also audit **runtime path**: jobs.json may already show `-mlx` while the request still sends bare (provider catalog alias, `providers.custom:lms-local.models` still listing bare key with context_length, cron resolve fallback, gateway cache). Prove the actual id sent to LMS.
2. **Retarget every life/root cron** to `ornith-1.5-35b-a3b-mlx` (or explicit Fallback `qwen/qwen3.8-27b` only where intended). Fix root cause if UI pin ≠ wire id (catalog hygiene / remove stale bare `ornith-1.5-35b-a3b` from live LMS provider models map so resolve cannot prefer bare; set `ornith-1.5-35b-a3b-mlx` context_length explicitly ≥64K if needed).
3. **Prove** Important-mail monitor (`8bbbc09fff56`) and siblings (at least `valedia-inbox-triage`) no longer 400 on bare id — trigger one run **or** verify jobs.json + resolve path + last_error cleared / successful fire. Record before/after model ids in stamp.
4. **Fences (unchanged + this addendum):**
   - Do **NOT** bounce trading LaunchAgents
   - No LMS unload/load
   - No Matrix/Signal bounce
   - Trading crons only if they still have bare id **and** Trader lane allows — **prefer life/root jobs only** unless trading jobs also broken on bare id
   - Do **NOT** touch TCC / money path / LiteLLM kill

## Known evidence at inject time (do not trust blindly — re-verify)

- `~/.hermes/cron/jobs.json` job `model` fields already mostly `ornith-1.5-35b-a3b-mlx`, but last_error still HTTP 400 bare id (Important-mail 15:00 PT; valedia-inbox-triage 16:02 PT) and weekly-memory-review/commerce-self-evolve ValueError naming bare id + 32k context.
- Root `config.yaml` `model.default` / `cron.model` already mlx; live `providers.custom:lms-local.models` still has key `ornith-1.5-35b-a3b` with `context_length: 131072` beside `ornith-1.5-35b-a3b-mlx: {}`.
- `chief-of-staff-daily` has `model: null` — pin it.
- Root `agent.disabled_toolsets` still includes `kanban` (charter item 1) and also `cronjob` (out of scope unless needed for proof trigger via CLI).

## Stamp requirement

`HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30.md` **MUST** include section:

### Cron model scrub
- Table of every root/life cron job: name, id, model before → after, last_status/last_error before → after
- Root-cause note (why UI mlx still wired bare, if applicable)
- Proof trigger / verify command output
- Bounce list for this scrub (expect **none** for trading/Matrix/Signal/LMS)


## Live update @ ~16:49 PT (executor inject follow-up)

- Post life-gateway-reload, Important-mail run `cron_8bbbc09fff56_20260930_162552` logged **`model=ornith-1.5-35b-a3b-mlx`** (API call #3 at 16:45:14 PT) — wire id is correct after yes-pack pins.
- `jobs.json` `last_status`/`last_error` still show the **15:00 PT bare-id 400** until the in-flight/missed run completes and overwrites (run is mid-tooling; Robinhood terminal TLS noise, not model 400).
- Still required for stamp **Cron model scrub**:
  - Full audit table of all root jobs (before=stale last_error / after=prove mlx on wire + clear or replace last_error)
  - Pin `chief-of-staff-daily` (`model: null`)
  - Provider catalog hygiene: live `providers.custom:lms-local.models` still has bare key `ornith-1.5-35b-a3b` alongside mlx
  - Confirm siblings (`valedia-inbox-triage` etc.) next fire use mlx / no new bare 400
  - Do not bounce trading/LMS/Matrix/Signal
