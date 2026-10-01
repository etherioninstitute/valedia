# VALEDIA-HUB

**Updated:** 2026-09-30 20:55 PT
**Updater:** this ops pass, from live GET against Paperclip `:3100` and `lms ps`. No watcher daemon. Hermes reads this file on session start. The next refresh is the next ops pass that re-reads those endpoints and rewrites this file.

Paperclip company `4fb233c2-27e9-4d0d-a217-2dc7205210a0` (prefix `VAL`) on `http://127.0.0.1:3100` is the source of truth for `VAL-*`. Hermes Kanban stays a glance, not a second copy of VAL issues.

MemPalace is an MCP server beside Built-in / holographic memory. Do not switch Memory Settings to MemPalace. Root, valedia, and CTO include the write tools `mempalace_add_drawer`, `mempalace_kg_add`, and `mempalace_diary_write`. Trading scout, decider, and auditor are recall-only (`mempalace_search` / `mempalace_status`). `mempalace_delete_drawer` stays off every profile. Palace: 611,649 drawers, 29 wings, 130 rooms at `~/.mempalace/palace`.

## Talk here

- Hermes **Bot → Valedia** for ops.
- Hermes **Bot → Valedia CTO** for coding.
- Board: http://127.0.0.1:3100/VAL/issues
- Preferred open ticket: http://127.0.0.1:3100/VAL/issues/VAL-9
- Sessions are not the hub.

## Model pin (20:55 PT)

| What | State |
|---|---|
| Desk `model.default` | `ornith-1.5-35b-a3b-mlx` via `custom:lms-local` at `http://127.0.0.1:1234/v1` |
| LMS Ornith | IDLE, 19.53 GB, ctx 65536, parallel 4. Still loaded. This pass did not unload it. |
| LMS fallback | Not in `lms ps` at 20:55 PT. This pass did not unload anything. |
| MoA | top-level `enabled: false` |
| Ollama `:11434` | HTTP 200 |
| Laya `:8766` | down. Retired. `VALEDIA_LAYA` unset. |
| Nimble System One | `VALEDIA_SYSTEMONE` unset. Default off. |

Root, valedia, CTO, scout, decider, and auditor `model.default` are all `ornith-1.5-35b-a3b-mlx`.

## Paperclip agents (GET 20:15 PT)

Issues HTTP 200 in 0.04s. Agents HTTP 200 in 0.004s. Heartbeat-runs HTTP 200 in 0.017s.

| Agent | Role | Status | Last heartbeat |
|---|---|---|---|
| Decider `16234474-029c-4852-abde-696dc25884e5` | ceo | idle | 2026-09-30 20:09 PT |
| Scout `277f3c57-9c78-401d-a7fe-0d7b8f5dce86` | researcher | idle | 2026-09-30 20:09 PT |
| CTO `53d25446-ac6c-4bda-b0c6-5c067b6ddf29` | cto | idle | 2026-09-30 19:40 PT |
| Auditor `1f360d86-444b-4c24-8c7f-35bb9c9cce50` | security | idle | 2026-09-28 23:09 PT |

Scout run `f1bdd984` and Decider run `ea27e9f5` were cancelled at 20:09 PT (they had been `running` since 19:09 PT). CTO run `b35e825c` was cancelled at 19:40 PT: Hermes gateway run cancelled after 900s without LLM/tool progress.

## Open VAL tips

| ID | Status | Assignee | Title |
|---|---|---|---|
| [VAL-9](http://127.0.0.1:3100/VAL/issues/VAL-9) | in_progress | CTO | First coding lever — INFORMED weather fee-true admit |
| [VAL-6](http://127.0.0.1:3100/VAL/issues/VAL-6) | in_progress | CTO | Continuous Ornith-solo check-in |
| [VAL-1](http://127.0.0.1:3100/VAL/issues/VAL-1) | todo | Decider | PILOT fee-true NWS CLI |
| [VAL-7](http://127.0.0.1:3100/VAL/issues/VAL-7) | todo | Scout | Rank INFORMED profit levers |
| [VAL-10](http://127.0.0.1:3100/VAL/issues/VAL-10) | todo | Scout | WEATHER-TWO-SIDED-FEE-TRUE-1 |
| [VAL-11](http://127.0.0.1:3100/VAL/issues/VAL-11) | todo | CTO | SELF-HEAL |
| [VAL-8](http://127.0.0.1:3100/VAL/issues/VAL-8) | todo | Auditor | HOLD fence — no `--live` / oil / eToro `start_copy` |
| [VAL-12](http://127.0.0.1:3100/VAL/issues/VAL-12) | todo | CTO | MONEY FOUR-PACK. Fenced. Do not drive from Grok. |
| [VAL-5](http://127.0.0.1:3100/VAL/issues/VAL-5) | in_review | Auditor | ACCEL PILOT queue |
| [VAL-3](http://127.0.0.1:3100/VAL/issues/VAL-3) | todo | Scout | NWS CLI research |
| [VAL-4](http://127.0.0.1:3100/VAL/issues/VAL-4) | backlog | Scout | eToro CopyTrader intel-only |

Checkout and execution run were empty on every open tip at 20:15 PT.

## CTO wake this pass

20:16:18 PT: one `POST /api/agents/53d25446-ac6c-4bda-b0c6-5c067b6ddf29/heartbeat/invoke` scoped to VAL-9 (`idempotencyKey` `grok-hub-val9-20260930`). HTTP 202 body `{"status":"skipped"}`. Wakeup row reason `execution_reconciliation_required`. Error text: automatic recovery stopped; recorded work is preserved; unverified outcomes are not repeated.

VAL-9 recovery actions are `resolved`, and `evidence.automaticRecovery.replay` is `blocked` (newest such row updated 2026-09-26). That hold was left in place. No second wake. Scout and Decider were not invoked.

## How Hermes reads status

On session start, root `~/.hermes/SOUL.md` plus profiles `valedia`, `valedia-cto`, `valedia-trading-scout`, and `valedia-trading-decider` read this file, then refresh with GET only:

- `GET /api/companies/4fb233c2-27e9-4d0d-a217-2dc7205210a0/issues`
- `GET /api/companies/4fb233c2-27e9-4d0d-a217-2dc7205210a0/agents`
- `GET /api/companies/4fb233c2-27e9-4d0d-a217-2dc7205210a0/heartbeat-runs`

Do not PATCH or POST Paperclip issues. Do not copy VAL rows into Hermes Kanban.

## Hermes Desktop (20:21 PT)

- App pid **11067**. Serve pid **11763**. Loopback port **58677** since 20:14:13 PT.
- Life gateway python **34663** (wrapper **30269**), up since 16:24 PT. `/api/status` says gateway running, pid 34663. Platforms degraded, 1 connected. Signal **908** left up.
- Kanban board: those 5 glance cards are `done` (`t_12af3f1f`, `t_96e95651`, `t_30dedc05`, `t_d4cf812c`, `t_83d0b4a8`). Blocked count is 0. Archived stays 17. Paperclip was not written.
- Active sessions on the life gateway: 0.

## Open blockers

- VAL-9 no-replay hold. A wake that names that issue is skipped until the hold is cleared through Paperclip, not by a raw database edit.
- VAL-12 stays fenced. VAL-8 stays the HOLD fence.
- Kanban Estimate returns `{"ok":false,"reason":"LLM error: APITimeoutError"}` in about 16s when both LMS models miss the 8s auxiliary budget. The Desktop socket budget is 30s, so the drawer gets that JSON instead of a connect timeout. Both models stayed loaded.
- The 20:14 proof marked `custom:lms-local` unhealthy on the Desktop serve process until 20:24:47 PT. That mark expires on its own.
