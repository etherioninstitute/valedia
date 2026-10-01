# HERMES-MEMPALACE-KANBAN-PAPERCLIP — 2026-09-30 20:55 PT

**GROK_EXIT:0**

MemPalace MCP is enabled on all six KEEP profiles. The five Kanban glance cards are `done`. One Paperclip skill is recommended and was not installed.

## MemPalace

| Proof | Result |
|---|---|
| Chroma count | **611,649** drawers at `~/.mempalace/palace` (`embeddings` count matches `tool_status().total_drawers`) |
| Wings / rooms | **29** distinct wings, **130** distinct room names. Largest wing: `repos` 583,291. `shared-brain` 885. `valedia-trading` 66. |
| CLI `mempalace status` | Header printed `10000 drawers` and 5 wings / 33 rooms. That listing is truncated. SQLite counts above are the palace. |
| CLI search | `mempalace search "valedia paperclip"` returned drawers in `shared-brain / general` and `shared-brain / ruflo`, source `VALEDIA-TRADER-HANDOFF-2026-09-26.md` and `...-09-27.md`. |
| Hermes probe, root | `hermes mcp test mempalace` connected in **2773ms**. Server exposes **19** tools. |
| Hermes probe, scout | `hermes --profile valedia-trading-scout mcp test mempalace` connected in **3734ms**. Same 19 server tools. |
| Desktop relaunch | **No.** Serve pid **11763** on `127.0.0.1:58677` stayed up. Life **34663** / wrapper **30269**, Signal **908**, matrix tunnel **925**, trading **76366 / 76367 / 76350**, CTO **76351**, Paperclip **55745** stayed up. |

`hermes mcp test` lists every tool the server process exposes. Hermes registration whitelists `tools.include`. `mempalace_delete_drawer` is on the server and on **no** profile include list.

| Profile | `mcp_servers.mempalace.enabled` | Include |
|---|---|---|
| default / root `~/.hermes/config.yaml` | was `false`, now **true** | 12 read tools + `mempalace_add_drawer`, `mempalace_kg_add`, `mempalace_diary_write` |
| valedia | already true | same write tools added |
| valedia-cto | already true | same write tools added |
| valedia-trading-scout | block was missing, now **true** | CTO baseline, 12 read tools, no write tools |
| valedia-trading-decider | same | same read-only include |
| valedia-trading-auditor | same | same read-only include |

Memory Settings were not switched. Root `memory.provider` is still `''` (Built-in). Valedia is still `holographic`. CTO and the three trading profiles have no `memory.provider` key and were left that way. MemPalace is MCP beside those providers.

Trading SOUL files gained a short recall pointer (`mempalace_search` / `mempalace_status`, writes stay on root `~/.hermes/SOUL.md`). Root and valedia SOUL already said to write back. They were not rewritten.

Backups: `config.yaml.bak-20260930-mempalace-live` next to each edited config. Running trading gateways were not bounced, so their in-memory tool list is the pre-edit set until the next start. Disk config is what that next start reads. The one-shot Hermes probes above loaded the new config without a bounce.

## Kanban

`GET http://127.0.0.1:58677/api/plugins/kanban/board` returned **401**. No cookie scrape. Completions went through `hermes kanban complete` into `~/.hermes/kanban.db`.

| Card | Result |
|---|---|
| `t_12af3f1f` desk-health | **done.** `lms ps`: Ornith mlx IDLE 19.53 GB, ctx 65536. Paperclip `:3100` pid 55745, HTTP 200. |
| `t_96e95651` Paperclip glance | **done.** `GET /api/companies/4fb233c2-27e9-4d0d-a217-2dc7205210a0/issues` HTTP 200, 13 issues. Pages VAL-9, VAL-6, VAL-5, VAL-8, VAL-11, VAL-12 and `/VAL/issues` HTTP 200. No issue PATCH. |
| `t_30dedc05` paper lane | **done.** Scout 76366 `:8642`, decider 76367 `:8643`, auditor 76350 `:8644`, `gateway_state=running`. `model.default` is `ornith-1.5-35b-a3b-mlx` ctx 65536. Argv has no `--live`. |
| `t_d4cf812c` Important-mail mlx | **done.** Job `8bbbc09fff56` model `ornith-1.5-35b-a3b-mlx`, provider `custom:lms-local`, `last_status=ok`, `last_error=null`, `last_run_at=2026-09-30T20:37:47.130119-07:00`. Inbox triage `0e7617883d30` same model, `ok`, `last_error=null`, `20:30:28-07:00`. Not a Gmail / himalaya secret. |
| `t_83d0b4a8` assignee allowlist | **done.** `hermes kanban assignees` lists only default, valedia, valedia-cto, and the three trading profiles. Ghosts remain under `profiles/.deleted/` (`auditor`, `code-cloud`, `decider`, `omni-code`, `scout`, `ugft`, `uncensored`). |

After: **blocked 0, done 5, archived 17**. No card left blocked. No VAL issue was copied into Kanban.

## Paperclip skill

Catalog `hermes skills search paperclip`: 11 hits. Excluded from the rank: skills.sh / GitHub biomedical `paperclip` (k-dense, FDA papers) and clawhub **Deadpost**.

Live company shape: 4 agents (Decider, Auditor, CTO, Scout), all `adapter=hermes_gateway`, all `idle`. Listeners `:8642`–`:8645` up. Last 8 heartbeat runs were not healthy: two cancelled at 3600s (`Hermes gateway run timed out`), one cancelled at 900s with no LLM/tool progress, three cancelled by a board operator, one older 3600s timeout, one `failed` with `ECONNREFUSED 127.0.0.1:8645` on 2026-09-29. Profile skill `skills/paperclip` is already the heartbeat write path on CTO, scout, decider, and auditor. Root `~/.hermes/skills` and the valedia profile have no `paperclip` skill directory.

| Rank | Skill | Identifier | Why |
|---|---|---|---|
| 1 primary | Paperclip Resilience | clawhub `paperclip-resilience` | Heartbeat recovery is the live pain (timeouts and a refused gateway connect). |
| 2 | Paperclip Orchestration | clawhub `paperclip-orchestration` | OpenClaw-gateway onboarding. This company is already on `hermes_gateway`. A second control plane is the wrong next install. |
| 3 | Paperclip | clawhub `paperclip` | Hub copy is missing, but the four company profiles already have the heartbeat skill. A root install widens the write surface. |

**Install this pass: no.** `paperclip-resilience` is not partially installed. Turning it on was not clearly safe next to the existing heartbeat writer. Profile `skills/paperclip` stays the write path. Kanban stays glance.

## Burton-only

None of the five cards needed a Gmail or himalaya login. Important-mail's latest run is `ok` with an empty `last_error`.

Later, not this pass: install clawhub `paperclip-resilience` only when a heartbeat-recovery change is explicitly wanted. Do not install it beside a second Paperclip writer.

## Fences held

No `--live`. No venue orders. No money path. No LMS unload (`lms ps` still shows Ornith mlx only; this pass did not unload it). No LiteLLM kill. No trading or life gateway bounce. No Desktop Electron relaunch. No Paperclip issue PATCH or POST. No VAL dual-write into Kanban. No watcher loop. MoA left off. Oil and weather fences untouched. No TCC change.

Hub one-liner: `recommendations/VALEDIA-HUB.md`.

**GROK_EXIT:0**
