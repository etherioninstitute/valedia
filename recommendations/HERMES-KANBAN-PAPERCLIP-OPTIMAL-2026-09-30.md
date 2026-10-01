# HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30

**Updated:** 2026-09-30 ~17:13 PT
**Authority:** Burton. Grok Build executed the charter `GROK-CHARTER-HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30.md` plus the cron-scrub, all-errors, and bare-resolver addenda.
**Home:** `~/.hermes` (life/root). Trading and CTO yaml were not edited.

**Bounce: none.** Life gateway stayed pid **30269** (wrapper) / **34663** (gateway python, up since 16:24 PT). Signal **908**, matrix tunnel **925**, LiteLLM **943** `:4000`, Paperclip **55745** `:3100`, Bionic **604** unchanged. No `hermes gateway --replace`. No LMS load or unload. No trading LaunchAgent bounce.

**GROK_EXIT:0**

---

## Fences held

- Paperclip stayed the source of truth. Hermes cards deep-link `VAL-*` only. No PATCH, POST, checkout, or comment. No Paperclip-as-Kanban profile, no dual-write, no UI fold.
- Trading CHANGE-set, TCC, context, and `tool_search` untouched. Trading yaml mtime still **2026-09-30 16:06:12** (yes-pack). No `profiles/*/cron/jobs.json` on scout, CTO, or the archived scout folder.
- TCC anchor still active (`hermes doctor`: macOS TCC anchor active, FDA granted). Hook, approvals, and SOUL hard rule not edited.
- MoA top-level `enabled: false`. Fallback is LMS `qwen/qwen3.8-27b`. It was not invoked (`lms ps` does not show it). No Ollama aux. No `Ornith:latest`.
- Ornith mlx stayed loaded the whole time (19.53 GB, ctx 65536, parallel 4). Ended **IDLE**. `qwen/qwen3.6-35b-a3b` stayed loaded (20.43 GB, ctx 262144). No unload.
- Seeded Kanban cards are `blocked`, so the dispatcher does not spawn KEEP-profile workers. Archived IdeaInbox cards stayed archived. Ghost assignees were not put on new cards.
- No `--live`, no venue orders, no wallets, no oil/weather unfence, no screen kills (`ot-valedia-desk`, `valedia-glance`).

---

## APPLY before / after

### (1) Default Kanban board

| | Before | After |
|---|---|---|
| `agent.disabled_toolsets` | `kanban` denied (backup) | `kanban` removed. `cronjob` still denied. `kanban_denied=False`. 18 toolsets still denied. |
| Active board | 17 archived IdeaInbox cards, no new work | Same 17 archived. **5 new `blocked` cards**, `created_by=grok-charter`. |
| Assignees on new cards | — | KEEP only: `default` ×2, `valedia`, `valedia-cto`, `valedia-trading-auditor`. Scout/decider/auditor ghosts are only on archived rows. |

Seed (all `blocked`, workspace scratch, idempotency keys so a retry does not duplicate):

| id | assignee | key | title |
|---|---|---|---|
| `t_12af3f1f` | default | `desk-health-20260930` | Desk health — Ornith mlx loaded and Paperclip :3100 |
| `t_96e95651` | valedia-cto | `paperclip-val-glance-20260930` | Paperclip open VAL glance (read-only links) |
| `t_30dedc05` | valedia-trading-auditor | `trading-paper-lane-20260930` | Trading paper lane status — glance only |
| `t_d4cf812c` | default | `next-cron-mlx-proof-20260930` | Confirm Important-mail and inbox triage stay on ornith mlx |
| `t_83d0b4a8` | valedia | `next-assignee-allowlist-20260930` | Refresh Desktop and confirm assignee list is KEEP profiles only |

`kanban.db` task counts after seed: archived 17 (14 unassigned, plus one each archived scout/decider/auditor), blocked 5. No `ready` tasks. `kanban.default_assignee` unset, so unassigned ready work would not dispatch anyway.

Doctor lists kanban as available and runtime-gated (loaded only for dispatcher-spawned workers). Visual Kanban reads the db from disk, so this did not need a gateway bounce.

### (2) Paperclip read-mostly mirror

**APPROVE** read-mostly mirror. **VETO** Paperclip as a Hermes Kanban profile, dual-write sync, and folding the Paperclip UI into Hermes.

Card `t_96e95651` body links, read-only:

- http://127.0.0.1:3100/VAL/issues/VAL-9
- http://127.0.0.1:3100/VAL/issues/VAL-6
- http://127.0.0.1:3100/VAL/issues/VAL-5
- http://127.0.0.1:3100/VAL/issues/VAL-8
- http://127.0.0.1:3100/VAL/issues/VAL-11
- http://127.0.0.1:3100/VAL/issues/VAL-12
- Board: http://127.0.0.1:3100/VAL/issues

VAL-9 / VAL-1 / VAL-10 weather and VAL-12 money stay glance links. Company `4fb233c2-27e9-4d0d-a217-2dc7205210a0` (Valedia, prefix VAL) was read with GET. Paperclip pid 55745 still on `:3100`. Writes stay on `skills/paperclip`.

### (3) Assignee / orchestrator allowlist

`list_profiles_on_disk()` is every direct child of `~/.hermes/profiles` that has `config.yaml`, plus implicit `default`. It does not honor `ui_meta.hermes-bots.hidden`, so the yes-pack hide flag was not enough.

Moved (not deleted) out of the scan path into `~/.hermes/profiles/.deleted/`:

`auditor`, `code-cloud`, `decider`, `omni-code`, `scout`, `ugft`, `uncensored`

CLI wrappers `~/.local/bin/{scout,decider,auditor,omni-code,ugft,code-cloud}` moved to `~/.hermes/profiles/.deleted/aliases/`. No `uncensored` wrapper existed. `~/.local/bin` no longer has those six names.

Scan path now: `default` (root `~/.hermes`) plus `valedia`, `valedia-cto`, `valedia-trading-scout`, `valedia-trading-decider`, `valedia-trading-auditor`. Doctor: **5 profile(s)**, each `ornith-1.5-35b-a3b-mlx`. Orphan-alias warnings from the first doctor are gone on the second doctor.

Out of allowlist and left out: scout, decider, auditor, uncensored, ugft, code-cloud, omni-code.

Desktop may cache the old dropdown until a soft refresh. No Desktop or gateway bounce for that cache.

### (4) Model optimality

| Knob | Before (backup `config.yaml.bak-20260930-kanban-optimal`, 16:06) | After |
|---|---|---|
| Main | `ornith-1.5-35b-a3b-mlx` @ `custom:lms-local` `:1234` | unchanged |
| MoA | `enabled: false` | unchanged `false` |
| Aux | `free_only: true`. `compression.provider: main`, model empty (doctor: Unknown provider `main`) | `free_only: true`. compression and approval are `provider: custom`, model `ornith-1.5-35b-a3b-mlx`, `base_url: http://127.0.0.1:1234/v1` |
| Fallback | unset | `custom:lms-local` / `qwen/qwen3.8-27b` / `:1234` |
| `cron.model` | already `ornith-1.5-35b-a3b-mlx` | unchanged |
| Live `providers` keys | `custom:lms-local` (bare sibling lived inside its `models:` map) | still only `custom:lms-local` |
| `only_configured` | true | true |
| `discover_models` | true | true (left on: live LMS catalog matches the six ids below and does not include the bare id) |
| mlx `context_length` | key present, **ctx unset**, beside bare id at **131072** | **65536** explicit |
| Fallback model ctx | `qwen/qwen3.8-27b` present, ctx unset | **65536** |
| `qwen/qwen3.6-35b-a3b` ctx | present | **262144** (matches `lms ps`) |

Live `providers.custom:lms-local.models` keys now:

`ornith-1.5-35b-a3b-mlx`, `qwen/qwen3.8-27b`, `qwen/qwen3.6-35b-a3b`, `ornith-1.5-9b-mlx`, `ulam-1`, `text-embedding-nomic-embed-text-v1.5`

Removed from that map: bare `ornith-1.5-35b-a3b`, unslashed `qwen3.8-27b`, `ud`, the two long obliterated/heretic kit ids, `openai/gpt-oss-120b`, `local/gpt-oss-120b@mxfp4`, `qwen3.8-27b-obliterated-k`, `ornith-1.5-mmproj`.

Second `hermes doctor` (no `--fix`, no `--live`): `auxiliary task routing resolves: approval→custom@127.0.0.1, compression→custom@127.0.0.1`. Unknown provider `main` is gone.

Pinned cron jobs do not walk `fallback_model`. The fallback row is for an Ornith-unload case and was not called.

`lms ps` at 17:12 PT: Ornith mlx **IDLE** 19.53 GB ctx 65536; `qwen/qwen3.6-35b-a3b` **IDLE** 20.43 GB ctx 262144. `qwen/qwen3.8-27b` not loaded.

---

## Cron model scrub

Source: `~/.hermes/cron/jobs.json` (10 jobs). Backup `jobs.json.bak-20260930-kanban-optimal` taken before the chief-of-staff pin. No separate Desktop jobs file. No trading `cron/jobs.json`.

| Job | id | model before → after | provider | last status after this pass | last_run_at |
|---|---|---|---|---|---|
| weekly-memory-review | `db3feded04bd` | mlx → mlx | custom:lms-local | error (stale ValueError, 2026-09-27) | 2026-09-27T21:00:47-07:00 |
| monthly-tool-audit | `d90bc3b6939f` | mlx → mlx | custom:lms-local | ok | 2026-09-01T09:57:28-07:00 |
| chief-of-staff-daily | `b0f826c750c9` | **null/null → mlx / custom:lms-local** | custom:lms-local | delivery_failed (Matrix connect, unchanged) | 2026-09-29T20:02:53-07:00 |
| Important-mail monitor | `8bbbc09fff56` | mlx → mlx | custom:lms-local | **delivery_failed, last_error cleared** | **2026-09-30T17:12:16.050112-07:00** |
| morning-brief | `72197d884561` | mlx → mlx | custom:lms-local | delivery_failed (Matrix connect) | 2026-09-30T06:38:16-07:00 |
| brain-night-shift | `b10ed09d836a` | mlx → mlx | custom:lms-local | paused (`enabled: false`) | never |
| valedia-inbox-triage | `0e7617883d30` | mlx → mlx | custom:lms-local | **ok, last_error cleared** | **2026-09-30T17:02:01.671442-07:00** |
| idea-inbox-miner | `478ad2268cea` | mlx → mlx | custom:lms-local | ok | 2026-09-30T08:02:04-07:00 |
| commerce-self-evolve | `06b12a6f69d3` | mlx → mlx | custom:lms-local | error (stale ValueError, 2026-09-28) | 2026-09-28T09:00:36-07:00 |
| ftc-rules-watch | `9a06d07d5886` | mlx → mlx | custom:lms-local | never run | next 2027-01-01 |

chief-of-staff pin was still mlx after the scheduler rewrote `jobs.json` through 17:12. Next fire 20:00 PT. Left paused: brain-night-shift.

---

## Cron bare-resolver

### Root cause

The 400 was LMS `:1234` rejecting the bare id `ornith-1.5-35b-a3b`. That id is not in the live LMS catalog. Two layers stacked:

1. **Gateway memory.** Before the 16:22 life reload, the running scheduler still sent the pre-yes-pack bare id. Last bare failures: Important-mail **15:00:23 PT** and valedia-inbox **16:02:16 PT**. `errors.log` has **20** `Invalid model identifier "ornith-1.5-35b-a3b"` lines today and **0 after 16:22**. Twelve earlier lines were `No models loaded` on the same bare id.
2. **Static sibling.** After the pin, `providers.custom:lms-local.models` still listed bare `ornith-1.5-35b-a3b` at ctx 131072 next to an mlx entry whose `context_length` was empty, plus obliterated kits. A resolver that landed on the bare sibling would 400 again, and the context floor (64,000) would raise `ValueError` naming the bare id at 32,768 when the snapshot ctx was unknown. That is the weekly-memory-review and commerce-self-evolve last_error text from Sep 27 and Sep 28.

Checked and ruled out as the live wire after the reload:

- No `-mlx` suffix strip. Post-reload `agent.log` client-create and API-call lines name `ornith-1.5-35b-a3b-mlx` at `http://127.0.0.1:1234/v1`.
- `model_snapshot` was not rewriting the pin.
- Pinned jobs do not walk `fallback_model`.
- `_hermex_picker_hidden_*` is not referenced by hermes-agent. Those blocks still contain a bare ornith string under the hidden LiteLLM row. Live `providers` keys are only `custom:lms-local`. Hidden blocks were left in place. LiteLLM `:4000` was not stopped.

### providers.models before / after

Before (backup): 15 keys, including bare `ornith-1.5-35b-a3b` ctx 131072 and mlx ctx unset, plus the obliterated kits listed above.

After: the six live catalog ids. mlx `context_length: 65536`. Fallback id `qwen/qwen3.8-27b` ctx 65536. Bare ornith is not a selectable id on live `custom:lms-local`.

### Fresh-run proof

These are log lines and a cleared `last_error`, not the jobs.json pin by itself.

**valedia-inbox-triage** `0e7617883d30`

- `2026-09-30 17:02:01` API call #4 `model=ornith-1.5-35b-a3b-mlx` provider=custom, then `Turn ended ... model=ornith-1.5-35b-a3b-mlx`, `Job 'valedia-inbox-triage' completed successfully`, agent returned `[SILENT]`.
- `run_agent` closed `base_url=http://127.0.0.1:1234/v1 model=ornith-1.5-35b-a3b-mlx`.
- `jobs.json`: `last_status=ok`, `last_error` empty, `last_run_at=2026-09-30T17:02:01.671442-07:00`.

**Important-mail monitor** `8bbbc09fff56`

- Session `cron_8bbbc09fff56_20260930_162552` opened 16:25:56 already on mlx (first line: client created `model=ornith-1.5-35b-a3b-mlx`). That is the post-reload gateway, before this pass's catalog edit.
- Catalog edit is on disk (config mtime **17:04:57**). Completing call is after that mtime:
  - `2026-09-30 17:12:14` API call #7 `model=ornith-1.5-35b-a3b-mlx` provider=custom in=27284 out=874 latency=361.9s `id=chatcmpl-1jqtz3uq10lrsbxyt3gwc`
  - `2026-09-30 17:12:15` `Turn ended: reason=text_response(finish_reason=stop) model=ornith-1.5-35b-a3b-mlx api_calls=7/500`
  - `2026-09-30 17:12:16` client closed `base_url=http://127.0.0.1:1234/v1 model=ornith-1.5-35b-a3b-mlx`
- `jobs.json`: `last_run_at=2026-09-30T17:12:16.050112-07:00`. **`last_error` empty.** The 15:00:23 HTTP 400 is overwritten.
- `last_status=delivery_failed` because Matrix send failed: `No module named 'mautrix'`. That is delivery, not a model 400. See error table.

`hermes cron run` on both ids while the scheduler held the fire returned "already being fired" and was not stacked. The 17:02 and 17:12 completions are the fresh overwrites.

Weekly-memory-review and commerce-self-evolve still show the Sep 27 / Sep 28 ValueError until their next schedule (Oct 4 21:00, Oct 5 09:00). They were not fired early. The same provider path is what inbox and Important-mail just used, and that path sent mlx and got `finish_reason=stop`.

While Important-mail was in its long tool loop, `cron.jobs` logged about once a minute that occurrence `2026-09-30T16:30:00` was "taken off the schedule but never claimed". One session only (`162552`). Inbox at 17:00 logged "already running — skipping". The loop stopped when the 17:12 turn finished. No second agent. No bounce to silence it.

---

## Error disposition

Today's `errors.log`: 551 lines. Buckets that mattered:

| Error | Evidence | Disposition |
|---|---|---|
| HTTP 400 bare `ornith-1.5-35b-a3b` | 20 lines, last before 16:22. 0 after 16:22. Important-mail last_error cleared 17:12. | **fixed** |
| HTTP 400 no models loaded (same bare id) | 12 lines, earlier today, pre-reload | **fixed** (same wire; Ornith mlx stayed loaded) |
| ValueError bare ornith ctx 32,768 | weekly 2026-09-27, commerce 2026-09-28 | **fixed on the resolver** (pin + mlx ctx 65536 + bare key removed). Stale `last_error` remains until the next scheduled run. Not re-fired. |
| Unknown provider `main` | first doctor, `auxiliary.compression` | **fixed.** Second doctor resolves compression to custom @ 127.0.0.1 |
| Orphan CLI aliases | first doctor, six wrappers | **fixed.** Wrappers moved to `.deleted/aliases/`. Second doctor has no orphan-alias lines. |
| Kanban denied | `disabled_toolsets` | **fixed.** `kanban` removed only. |
| Ghost profiles in the assignee dropdown | scan path listed archived names | **fixed** by moving folders out of the direct-child scan. Folders kept. |
| Matrix / `mautrix` / shared token | 223 matrix lines today. Important-mail, morning-brief, chief-of-staff `delivery_failed`. Doctor: default and valedia share one Matrix credential. | **deferred + needs-Burton.** Delivery module missing, and splitting the token wants Burton's call. A fix that reloads the life gateway or runs `hermes gateway migrate --multiplex` is outside the bounce fence. Model path is separate and proven. |
| langfuse / plugin load timeout | ~12 lines, including 16:25:50 (10s timeout on gateway start) | **deferred.** Not the model path. |
| otel / span export | 62 lines | **deferred.** Noise. |
| Interrupted cron mark ~08:35 | 6 lines, morning restart | **deferred.** Historical. Current gateway pid 34663 is the 16:24 process and is running. |
| LaunchAgent last-exit -15 / 143 | prior SIGTERM codes | **deferred.** Processes are running (life 30269, signal 908, trading/CTO listeners still up). Not a current crash. No bounce. |
| npm advisories | doctor issues 1–3: agent-browser 3, web 7, ui-tui 5 | **deferred.** Upstream lockfile. A local npm fix does not survive `hermes update`. No `--fix`. |
| Optional packs / OAuth / xAI connectivity HTTP 400 | doctor warnings (telegram, discord, Codex, MiniMax, xAI OAuth, OpenRouter, missing XAI key, a2a/browser-cdp/feishu/ha/spotify/yuanbao) | **deferred.** `x_search` is in `disabled_toolsets`. Not the LMS path. |
| Context-length probe >5s at 16:30 | one gateway warning, returned unknown | **deferred.** Probe timeout while Ornith was generating. Catalog ctx is now explicit 65536. |
| tui ws receive failed | `gui.log`, 2 lines | **deferred.** |
| himalaya TLS EOF on one mailbox during the mail run | tool warning 16:48 inside the mail session | **deferred.** The run still finished on mlx at 17:12. |
| nous portal base URL unreadable, mau.db slow queries, environment snapshot init failures | scattered warnings | **deferred.** Not this charter's model or Kanban path. |
| Scheduler "never claimed" loop | see bare-resolver | **deferred.** One session, completed, no double fire. |
| Desktop dropdown cache | filesystem allowlist is already the new set | **deferred.** Soft refresh when Burton next opens Desktop. No bounce. |
| Trading leftover catalog keys | scout/decider/auditor/CTO/valedia still mention `ornith-1.5-35b-a3b@q4_k_m` and, on trading, `custom:ollama-local` / `ornith-1.5:latest` under a MoA block whose top-level `enabled` is false. **Zero** lines of the bare LMS id. Active `model.default` is already mlx. | **document for Trader.** Yaml not edited. No trading cron files. |

Second doctor still exits with **4 issues**, all deferred above: three npm lockfile notes and the shared Matrix credential. No `--fix`.

---

## Backups

- `~/.hermes/config.yaml.bak-20260930-kanban-optimal` (19910 bytes, source mtime 16:06)
- `~/.hermes/cron/jobs.json.bak-20260930-kanban-optimal` (23040 bytes)

Profile moves are reversible: move the seven directories and six wrappers back. They were not deleted.

---

## Verify

- `kanban` absent from root `disabled_toolsets`.
- MoA `enabled: false`. Fallback `qwen/qwen3.8-27b`. Aux `free_only: true`. Compression provider `custom`.
- Live LMS model keys are the six catalog ids. mlx ctx 65536. Bare id absent from live `providers`.
- `lms ps`: both resident models still loaded. Fallback not loaded.
- Cron: 10/10 life jobs pin mlx + `custom:lms-local`. chief-of-staff was the null pin.
- Important-mail 17:12:16 and valedia-inbox 17:02:01 wire lines above. Bare-400 count after 16:22 is 0.
- Doctor profiles: 5, all mlx. Ghosts only under `.deleted/`.
- Pids unchanged: 30269, 34663, 908, 925, 943, 55745.
- Trading config mtime 16:06:12.

---

**GROK_EXIT:0**
