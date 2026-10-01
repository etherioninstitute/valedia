# Hermes life gateway reload — 2026-09-30

**GROK_EXIT:0**

Charter `recommendations/GROK-CHARTER-HERMES-LIFE-GATEWAY-RELOAD-2026-09-30.md`. Life/root LaunchAgent only. No yaml rewrite. Disk already matched the cleanup and assistant-optimize stamps before the bounce.

## Method

`launchctl kickstart -k gui/501/ai.hermes.gateway` at **16:22:29 PT**.

A lone SIGTERM would park this job. The plist sets `KeepAlive` `SuccessfulExit=false`, so a clean exit 0 does not respawn. Kickstart kills that one label and starts it again. No `hermes gateway stop`, no `hermes gateway --replace`, no `hermes -p valedia gateway`.

The old python drained under load average ~300–420 (SIGTERM logged 16:23:42 and 16:24:31, parent pid 74707). The new gateway python came up after that drain.

## Before → after

| | Before | After |
|---|---|---|
| `ai.hermes.gateway` | osascript **74698**, started 14:57:50 PT, `runs=1`, never exited | osascript **30269**, started **16:22:29 PT**, `state=running`, `runs=2` |
| wrapper python | 74707 (14:57:51) | **32605** (16:23:45) |
| `gateway run --external-supervisor` | 74840 (14:57:52) | **34663** (16:24:53) |
| `HERMES_HOME` | `~/.hermes` | `~/.hermes` (no `--profile`) |
| root `config.yaml` mtime | 16:06:12 PT | 16:06:12 PT (unchanged) |

Old pids 74698 / 74707 / 74840 are gone. `launchctl print gui/501/ai.hermes.gateway` shows `state=running`, pid 30269.

## Config is live

Process start (16:22:29 PT, gateway python 16:24:53) is after the 16:06:12 yaml mtime.

Gateway log, this boot:

- 16:26:11 `Session storage: ~/.hermes/sessions`
- 16:26:12 `Agent budget: max_iterations=500 (agent.max_turns from config.yaml…)`
- 16:26:12 `✓ signal connected` and 16:26:13 `Signal SSE: connected`
- 16:26:14 `Gateway running with 1 platform(s)`
- 16:26:39 `Model context warmed: ornith-1.5-35b-a3b-mlx -> 65536 tokens (detected)`
- 16:26:39 `Turn machinery warmed in 27.6s (41 tool schema(s) materialized)`

The 14:58 boot of pid 74698 had logged `ornith-1.5-35b-a3b -> 131072 tokens (config)`. This boot logged the mlx id at 65536.

Disk keys still match the stamps (read after the reload, yaml not rewritten):

- `moa.enabled` false; aggregator `ornith-1.5-35b-a3b-mlx`; reference `qwen/qwen3.6-35b-a3b`
- `model.default`, `providers.custom:lms-local.model`, `auxiliary.approval.model`, `delegation.model`, `cron.model` are the mlx id
- `tools.tool_search.enabled` false
- `cron/jobs.json`: 9 model pins mlx, `chief-of-staff-daily` inherits. The scheduler rewrote `updated_at` at 16:26:35 PT; model pins stayed mlx
- seven archived `profile.yaml` files still `hermes-bots.hidden: true`; KEEP profiles are not hidden

Root profile is `default`, so this boot does not emit `Active profile:` (that line is only logged for a non-default profile).

## Fences (same pids)

| Surface | pid | Since |
|---|---|---|
| trading scout `:8642` | 76366 (supervisor 76364) | 14:58:21 PT |
| trading decider `:8643` | 76367 (supervisor 76365) | 14:58:21 PT |
| trading auditor `:8644` | 76350 (supervisor 76348) | 14:58:20 PT |
| CTO `:8645` | 76351 (supervisor 76349) | 14:58:20 PT |
| `com.burton.hermes-signal` | 908 | 2026-09-29 14:48:36 PT |
| `com.burton.matrix-tunnel` | 925 | 2026-09-29 14:48:36 PT |
| LiteLLM `:4000` | 943 | 2026-09-29 14:48:36 PT |
| Paperclip `:3100` | 55745 | 2026-09-29 16:19:21 PT |

TCC fence strings still in CTO and trading scout/decider/auditor yaml: `block-tcc-appdata.py`, `fail_closed: true`, `terminal.cwd` workspace-v2. Those file mtimes are still 16:06:12 PT. Hook file mtime 14:06:47 PT.

`lms ps`: both residents still loaded. `ornith-1.5-35b-a3b-mlx` 19.53 GB ctx 65536 parallel 4. `qwen/qwen3.6-35b-a3b` 20.43 GB ctx 262144 parallel 4. No load, no unload. Status column moved with live inference during warm-up (ornith `PROCESSINGPROMPT`, qwen `IDLE`).

Screens `ot-valedia-desk` and `valedia-glance` were not in `screen -ls`. The only session, `valedia-grok`, was left alone.

Signal on the life gateway dropped and reconnected to the existing signal-cli on `127.0.0.1:8081`. The Signal LaunchAgent was not bounced. Matrix tunnel was not bounced. The life log still reports Matrix adapter creation failed (`mautrix` missing) and `platforms.matrix.enabled: false`. Same class of Matrix miss as the 14:58 boot.

No `--live`, no venue orders, no wallets, no OKX, no VAL-12.

**GROK_EXIT:0**
