# HERMES-HUB-PAPERCLIP-OPTIMAL-2026-09-30

**Updated:** 2026-09-30 20:21 PT
**Authority:** Burton. Grok Build executed `GROK-CHARTER-HERMES-HUB-PAPERCLIP-OPTIMAL-2026-09-30.md`.
**Hub:** `recommendations/VALEDIA-HUB.md`

**GROK_EXIT:0**

---

## Timeout root cause

The 30s error is the Desktop IPC socket, not a dead life gateway. Life gateway python **34663** (wrapper **30269**, up since 16:24 PT) was never the `hermes:api` target. Hermes.app owns its own `hermes serve` child. Electron aborts `hermes:api` at 30000ms (`Timed out connecting to Hermes backend after 30000ms`).

The click Burton saw was Kanban effort estimate, `POST /api/plugins/kanban/tasks/t_12af3f1f/estimate`. That route calls `call_llm(task="kanban_estimator")`. An explicit timeout is repeated on every same-provider retry and on the fallback. The caller timeout was 60s, `auxiliary.transient_retries` defaults to 2, and the fallback is the same LMS endpoint (`qwen/qwen3.8-27b`). A busy Ornith therefore became 60+60 and the UI died at 30s while the backend kept waiting.

Timeline, PT 2026-09-30:

| Time | What |
|---|---|
| 19:38:02 | `desktop.log`: `hermes:api POST .../t_12af3f1f/estimate` timed out after 30000ms. |
| 19:40:37 | `agent.log`: primary `kanban_estimator` timed out after 60s, then fell back to `qwen/qwen3.8-27b`. |
| 19:41:38 | Fallback timed out. `custom:lms-local` marked unhealthy until 19:51:38. |
| 19:46–19:47 | Event loop stalls, `commands.catalog` send failed, three `GET /api/status` 30s timeouts. Separate from the estimate, same socket budget. |

A second class is any inline TUI RPC that walks the skill tree on the asyncio thread. Cold `_find_all_skills()` on `~/.hermes/skills` was multi-second. `commands.catalog` was not in `_LONG_HANDLERS`.

## Fix

Three Python edits under `~/.hermes/hermes-agent`. They load on the next Desktop serve start. The packaged `app.asar` was not rebuilt.

1. `plugins/kanban/dashboard/plugin_api.py` — `kanban_estimator` caller timeout **60 → 8**. The endpoint already turns LLM errors into `{"ok": false, "reason": ...}` so the drawer can render them.
2. `agent/auxiliary_client.py` — added `kanban_estimator` to `_TIMEOUT_NO_RETRY_TASKS`, next to `compression`, `vision`, and `title_generation`. A full-budget timeout skips the same-provider retries and goes straight to one fallback. Fast blips still retry.
3. `tui_gateway/server.py` — `commands.catalog` added to `_LONG_HANDLERS` so the skill walk does not freeze other HTTP handlers.

`py_compile` of those three files returned COMPILE_OK.

Why a relaunch was required: the running serve imports `hermes-agent` from disk at start. SIGTERM of serve child **34950** at 20:07 did not stay a child reconnect. The supervisor respawned pid 95123 on port 51176, then desktop.log recorded `Hermes Desktop is quitting` and the Electron parent exited. Life, Signal, Paperclip, and the trading gateways stayed up. This pass quit only Electron parent **99385** (gone in 2s) and relaunched with `open -a` the packaged Hermes.app. New parent **11067**, serve **11763**, `HERMES_BACKEND_READY port=58677` at 20:14:13 PT.

## Proof

Before, on the 20:09 process (timeout=8 already loaded, retries still on): `POST .../estimate` produced no HTTP body at 25.003s. Backend log shows attempt 1/2, attempt 2/2, then an 8s fallback, finishing at 20:10:29 (~35s). That still misses the 30s socket.

After, on port 58677 (pid 11763), 20:14 PT:

| Call | Result |
|---|---|
| `GET /api/status` | HTTP 200 in 0.099s. Version 0.21.5. Gateway running, pid 34663. |
| `GET /api/plugins/kanban/board` | HTTP 200 in 0.007s, 7910 bytes, **5 blocked cards**. |
| `POST /api/plugins/kanban/tasks/t_12af3f1f/estimate` | HTTP 200 in **16.252s**. Body `{"ok":false,"reason":"LLM error: APITimeoutError"}`. |

`agent.log` at 20:14:39: `timeout on the critical path; skipping same-provider retry and falling back`, then one 8s fallback. Zero `Timed out connecting` lines in `desktop.log` since 20:14. App 11067 and serve 11763 still alive at 20:21. The model did not return an `est_tokens` figure: both LMS models missed the 8s budget and stayed loaded (Ornith IDLE 19.53 GB, qwen IDLE 16.08 GB). The drawer now receives the JSON error inside the socket budget.

Fence pids still present at 20:21: Signal **908**, life **30269/34663**, Paperclip **55745**, trading **76348/76349/76364/76365**.

## Hub

Path: `recommendations/VALEDIA-HUB.md`.

What updates it: an ops pass that GETs Paperclip issues, agents, and heartbeat-runs, plus `lms ps`, then rewrites the file. This pass did that at 20:15–20:21 PT. No watcher daemon writes it. Hermes is instructed to read it, not to copy VAL rows into Kanban.

## How Hermes knows Paperclip status

Read-only. Company `4fb233c2-27e9-4d0d-a217-2dc7205210a0` on `http://127.0.0.1:3100`.

SOUL block **Status topsheet (session start)** appended to:

- `~/.hermes/SOUL.md`
- `~/.hermes/profiles/valedia/SOUL.md`
- `~/.hermes/profiles/valedia-cto/SOUL.md` (also: prefer the open fee-true weather admit ticket)
- `~/.hermes/profiles/valedia-trading-scout/SOUL.md`
- `~/.hermes/profiles/valedia-trading-decider/SOUL.md`

Those profiles GET `/issues`, `/agents`, and `/heartbeat-runs`. They do not PATCH or POST issues. Auditor SOUL was left unchanged. No skill file was added. Kanban cards stay blocked glance links. Prior APPROVE (read-mostly mirror + deep links) and VETO (do not fold Paperclip into a Kanban profile) still hold.

## CTO wake

One native call, no daemon:

`POST /api/agents/53d25446-ac6c-4bda-b0c6-5c067b6ddf29/heartbeat/invoke`

Body named VAL-9 only (`29a89019-e2a2-4fbe-82db-b18b568a9da8`), `triggerDetail=manual`, `idempotencyKey=grok-hub-val9-20260930`. HTTP **202** at 20:16:18 PT, body `{"status":"skipped"}`.

Wakeup row: `execution_reconciliation_required`. Paperclip text: automatic recovery stopped; recorded work is preserved; unverified outcomes are not repeated. VAL-9’s recovery actions are `resolved`, and `evidence.automaticRecovery.replay` is `blocked` (newest matching row updated 2026-09-26). The hold was not cleared and the database was not edited. Scout and Decider were idle by 20:15 and were not invoked. CTO stayed idle, last heartbeat 19:40 PT.

## Optimize changes

- Estimator timeout and no-retry task set, above.
- `commands.catalog` moved to the long-handler set.
- Topsheet pointers in the five SOUL files.
- `recommendations/VALEDIA-HUB.md` written from the live snapshot.

## Left unchanged

- No `--live`, no oil unfence, no eToro `start_copy`, no VAL-12 drive, no wallet or Poly LIVE flip.
- No LMS unload. Both models still loaded at 20:21 PT. qwen TTL was 24m/30m.
- No trading LaunchAgent bounce. Trading pids 76348, 76349, 76364, 76365 unchanged. Trading and CTO yaml not rewritten.
- No life LaunchAgent bounce. Pids 30269 and 34663 unchanged.
- MoA top-level `enabled: false`. The enabled flags inside the MoA preset block were left as they were.
- No Paperclip issue PATCH/POST. No Kanban dual-write. The five glance cards stayed `blocked`.
- Signal 908 left up. Platforms status was degraded with 1 connected. No matrix or tunnel bounce.
- Screens `ot-valedia-desk` and `valedia-glance` were not killed. `screen -ls` showed only `59706.valedia-grok`.
- Desktop cron still logs six profiles (`default`, `valedia`, `valedia-cto`, and the three trading profiles). Trading cron stores were not disabled.
- Catalog leftovers documented only. Live `model.default` is mlx on root and on valedia, CTO, scout, decider, and auditor. Profile yaml still names `ornith-1.5:latest`, `ornith-1.5-35b-a3b@q4_k_m`, and `orcarouter/Qwen3.8-27B-Uncensored:q6_K` inside MoA and catalog sections. Root `config.yaml` still names `ornith-1.5:latest` in the hidden restore blob. Those keys were not rewritten.
- Assignee allowlist from the earlier Kanban stamp was left as-is.
- Known flakes left in place: session timeline 404s, `init_session` exit 124, fleet-roster 8s timeout, skills-hub 30s timeout at 20:06 on the old process, event-loop stalls of 7.1s and 10.7s before the relaunch. The unhealthy-provider mark on the Desktop serve process from this proof expires at 20:24:47 PT.

**GROK_EXIT:0**
