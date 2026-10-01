# Hermes full audit — DRAFT ONLY (2026-09-30)

**Read:** 2026-09-30 ~14:56 PT. **Apply:** none.
**This audit is not 100% applied.** Zero `config.yaml` / plist / `.env` edits, zero LaunchAgent kickstarts, zero `lms load` / `lms unload`, zero SOUL rewrites. Every CHANGE below waits for Burton’s yes.

**GROK_EXIT:0** — docs on disk, all six trees walked, draft written. Nothing blocked.

Charter: `recommendations/GROK-CHARTER-HERMES-FULL-AUDIT-DRAFT-2026-09-30.md`.
Gap plan (stale vs this read where noted): `recommendations/HERMES-VALEDIA-OPTIMIZE-PLAN.md`.
Prior stamps: `HERMES-ASSISTANT-OPTIMIZE-2026-09-30.md` (`GROK_EXIT:0`), `HERMES-TCC-APPDATA-FIX-2026-09-30.md` (`GROK_EXIT:0`).
Docs mine: `recommendations/HERMES-DOCS-REFERENCE-Valedia.md` (1,234,915 bytes, generated 2026-09-30 from hermes-agent.nousresearch.com/docs). User Stories excluded.

Labels: **FACT** = this read. **DOCS** = the mine. **CODE** = Hermes v0.21.0 tree `~/.hermes/hermes-agent` (supplement where the mine indexes a page and does not inline the knob). **Goal** = Assistant life (root gateway + Hermex) vs CTO desk vs Trader scout/decider/auditor.

---

## Not 100% applied (read this first)

Disk hygiene from the infra stamp is real. The live life path is still the pre-reload process, and the Assistant-owned model/MoA gaps are still open.

| Item | Disk now | Live now |
|------|----------|----------|
| `tools.tool_search.enabled: false` | All six yaml files | CTO pid 51268 and trading pids 79797 / 79891 / 79929 restarted after the write. Life gateway pid **66704** started **08:35 PT**, yaml mtime **14:47 PT**. DOCS: tool/skill config needs a reload; only `model.context_length` and `compression.*` hot-reload. Inference: life process still has the 08:35 tool surface. |
| Root MoA | `moa.enabled: true`, reference `qwen3.8-27b`, aggregator bare `ornith-1.5-35b-a3b` | Same. Reference id is not the loaded LMS model. |
| Root model id | `model.default` `ornith-1.5-35b-a3b`, `context_length` **131072** | LMS has `ornith-1.5-35b-a3b-mlx` loaded at context **65536**. Catalog has no bare id. |
| Root cron | `cron.model` bare id; 8 of 9 enabled jobs pin that same bare id | Gateway cron ticker is independent of the disabled `cronjob` toolset (DOCS). Jobs still name the bare id. |
| Profile `valedia` | tool_search false, ponytail disabled, **delegation/cron still Ollama** `ornith-1.5:latest` @ `:11434` | Gateway **stopped** since 2026-09-28 (pid 18290 dead). Not the life process. |
| CTO context | `model.default` mlx, **no** top-level `context_length`; provider map says **131072** | LMS Ornith load is **65536**. |
| Matrix | root `platforms.matrix.enabled: true` | `state=retrying`, `failed to reconnect`, `needs_attention=true`, `retrying_since` 2026-09-30T15:35:52Z (08:35 PT). Signal `connected`. |
| Trading three | tool_search false, ctx 65536, delegation/cron lms mlx, TCC hook. SHA prefixes match the infra stamp | Listeners 8642 / 8643 / 8644. MoA blocks still **name** Ollama and stay `enabled: false` (Trader VETO: do not edit `moa`). |
| omlx `:8000` | not configured | not a cutover. Option section only. |

---

## Coverage checklist

### Docs mine walked

| Mine section | Used for |
|--------------|----------|
| §1 TOC | Page inventory. User Stories line is the exclusion. Tool-search, MoA, memory, MCP, gateway, local-Mac, security pages are indexed. |
| §2 Architecture / profiles / gateway | Separate HERMES_HOME per profile. One gateway per profile today (no multiplexer). Signal token lock. Reconnect retries forever; `needs_attention` after `agent.reconnect_attention_after` default **7200s**. |
| §2.5 Tools / MCP | `agent.disabled_toolsets` wins after `platform_toolsets`. MCP `enabled: false` tears down within ~1 min on a running gateway. Include-lists are the allow surface. |
| §2.6 + §3 providers / config | Precedence: CLI > `config.yaml` > `.env` > defaults. Custom endpoint via `model.provider`. `providers.<id>.stale_timeout_seconds` explicit value wins, including on local. Min context **64,000** or Hermes rejects the model. |
| §3 compression / timeouts | Threshold default **0.50**. `threshold_tokens` fires at the **lower** of ratio and absolute. Local stale-stream ceiling default 900s unless explicit. Gateway hot-reload covers context length + `compression.*` only. |
| §4 Local Mac / omlx | llama.cpp vs omlx. omlx default `http://127.0.0.1:8000`. MLX is manual, not the managed llama.cpp runtime. Option only. |
| §5 Optimization | Prompt-cache stability, `/compress`, delegation cost, local timeout table. |
| §6 Security | `approvals.mode` default `smart`. User deny beats yolo. Hardline blocklist always on. `destructive_slash_confirm` default true. |
| §7 Gaps | No single DEFAULT_CONFIG dump. omlx CLI flags incomplete. `HERMES_STREAM_STALE_TIMEOUT` vs local 900s ceiling wording drifts. Per-platform bot-token pages out of scope. |

**Mine gap (honest):** the TOC lists `user-guide/features/tool-search` (~16 KB) and `mixture-of-agents`, and §3 quotes MoA reasoning slots, but the inlined mine has **no** `tools.tool_search` schema table. Knob behavior below is **CODE** from `hermes-agent/tools/tool_search.py` on this machine (v0.21.0): `enabled: false` normalizes to `"off"`; `"auto"` is an alias of `"on"` today (not a budget gate); default if unset is `"auto"`. `listing: off` skips the embedded manifest. `threshold_pct` default 5 only bounds that manifest.

**Unknown precedence:** when `model.context_length` and `providers.*.models.<id>.context_length` disagree, this mine does not say which number the prompt budget uses. Trader profiles set top-level **65536** and the provider row **131072**. Flagged, not “fixed.”

### Configs walked (every top-level key)

| Tree | Keys | SHA-256 prefix | mtime | Bytes |
|------|------|----------------|-------|-------|
| `~/.hermes/config.yaml` (root / life) | **48** | `3a465cf7a1590400` | 14:47:23 PT | 19835 |
| `profiles/valedia/config.yaml` | **45** | `ed15c13755257f21` | 14:47:23 PT | 14906 |
| `profiles/valedia-cto/config.yaml` | **43** | `8ea161b8b192a08f` | 14:47:23 PT | 10759 |
| `profiles/valedia-trading-scout/config.yaml` | **37** | `e9aa6853f346ba8b` | 14:07:36 PT | 8886 |
| `profiles/valedia-trading-decider/config.yaml` | **37** | `d9a3bb21110ad4a2` | 14:07:36 PT | 8759 |
| `profiles/valedia-trading-auditor/config.yaml` | **40** | `a332842c36013d88` | 14:07:36 PT | 8325 |

PyYAML `safe_load` succeeded on all six. Secrets in this stamp are counts and names only.

### Runtime (read-only)

Hermes **v0.21.0**, code sha `245e48008fa8`, on every gateway_state.

| LaunchAgent | launchd pid | Gateway pid | Listen | State |
|-------------|-------------|-------------|--------|-------|
| `ai.hermes.gateway` | 66534 (stderr wrapper, elapsed ~6h21m) | **66704** | life (Signal via signal-cli, Matrix via tunnel) | running since 08:35 PT. Home `~/.hermes` |
| `ai.hermes.gateway-valedia-cto` | 51261 | **51268** | `127.0.0.1:8645` | running since 14:47 PT. `api_server` connected. Matrix yaml disabled |
| `ai.hermes.gateway-valedia-trading-scout` | supervisor | **79797** | `:8642` | running since 14:09 PT. `api_server` connected |
| `ai.hermes.gateway-valedia-trading-decider` | supervisor | **79891** | `:8643` | same |
| `ai.hermes.gateway-valedia-trading-auditor` | supervisor | **79929** | `:8644` | same |
| profile `valedia` | no LaunchAgent | state file pid 18290 **dead** | — | `gateway_state=stopped` since 2026-09-28. Last Signal error: phone lock, default profile already holds the account |
| `com.burton.hermes-signal` | 908 | signal-cli `--http 127.0.0.1:8081` | **:8081** | up ~24h |
| `com.burton.matrix-tunnel` | 925 | ssh `-L 127.0.0.1:6167` | **:6167** | up ~24h |
| `com.parantoux.hermes-webui` | 913 | webui | — | up, not bounced |

`lms ps` (no load, no unload):

| Identifier | Status | Size | Context | Parallel | TTL |
|------------|--------|------|---------|----------|-----|
| `ornith-1.5-35b-a3b-mlx` | IDLE | 19.53 GB | **65536** | 4 | 17m / 30m |
| `qwen/qwen3.6-35b-a3b` | IDLE | 20.43 GB | **262144** | 4 | — |

`GET http://127.0.0.1:1234/api/v0/models` catalog ids (6): `ornith-1.5-35b-a3b-mlx`, `qwen/qwen3.6-35b-a3b`, `ornith-1.5-9b-mlx`, `qwen/qwen3.8-27b`, `ulam-1`, `text-embedding-nomic-embed-text-v1.5`. Bare `ornith-1.5-35b-a3b` and bare `qwen3.8-27b` are **absent**. 9B and Ulam are catalog-only, not loaded.

Also listening, untouched: LiteLLM python on `:4000`, RuFlo node on `:8810`, Laya on `:8766`. No `rg` binary in `/opt/homebrew/bin` or `/usr/local/bin` (TCC find-fallback condition still true).

### `.env` names only

| File | Count | Names |
|------|-------|-------|
| root | 39 | TERMINAL_* , BROWSERBASE_* , BROWSER_* , WEB/VISION/MOA/IMAGE_TOOLS_DEBUG , MATRIX_* (homeserver, user, allowed, device, recovery, password, e2ee, home room, access token) , BRAVE_SEARCH_API_KEY , BRAVE_API_KEY , EXA_API_KEY , HERMES_LANGFUSE_* , SHOPIFY_* , HERMES_STREAM_STALE_TIMEOUT , HERMES_STREAM_STALE_GIVEUP , SIGNAL_HTTP_URL , SIGNAL_ACCOUNT , SIGNAL_ALLOWED_USERS , SIGNAL_HOME_CHANNEL , TYPESAFE_AI_API_KEY , TYPESAFE_API_KEY , LITELLM_MASTER_KEY |
| valedia | 36 | same terminal/browser/matrix/langfuse/shopify/signal/stale set, plus OLLAMA_API_KEY , GITHUB_TOKEN , BRAVE_SEARCH_API_KEY . No TYPESAFE / LITELLM / EXA |
| valedia-cto | 28 | terminal/browser/matrix/debug set, plus GITHUB_TOKEN , VALEDIA_CTO_BUILDER , API_SERVER_ENABLED , API_SERVER_PORT , API_SERVER_HOST , API_SERVER_KEY , API_SERVER_MODEL_NAME |
| trading scout | 17 | terminal/browser/debug set, plus BRAVE_SEARCH_API_KEY and the five API_SERVER_* names |
| trading decider | 16 | terminal/browser/debug + five API_SERVER_* |
| trading auditor | 16 | same as decider |

No venue-order key names in these six files. Values not copied.

Root `cron/jobs.json`: **10** jobs, **9** enabled, **1** paused (`brain-night-shift`). Enabled names: `weekly-memory-review`, `monthly-tool-audit`, `chief-of-staff-daily`, `Important-mail monitor`, `morning-brief`, `valedia-inbox-triage`, `idea-inbox-miner`, `commerce-self-evolve`, `ftc-rules-watch`. Eight enabled jobs pin `model: ornith-1.5-35b-a3b`. `chief-of-staff-daily` has no per-job model (inherits `cron.model`, also the bare id). Profile cron dirs have **0** job json files. The root `cron/` directory has 231 files total; that is not 231 jobs.

---

## Shared FACT (all six, unless a file table overrides)

| Knob | Value | DOCS | Verdict |
|------|-------|------|---------|
| `model.provider` / `base_url` | `custom:lms-local` → `http://127.0.0.1:1234/v1` | Custom OpenAI-compatible endpoint | **KEEP** |
| `agent.max_turns` | 500 | Iteration budget exists; 500 is the configured cap | **KEEP** |
| `agent.verbose` | false | — | **KEEP** |
| `tools.tool_search` | enabled **false**, threshold_pct 5, search_default_limit 5, max_search_limit 10, listing **off**, listing_max_tokens 400 | CODE: false → off. Mine has no schema table | **KEEP** on disk. Life **process** still needs a reload (see backlog) |
| `database.journal_mode` | wal | SQLite WAL is the documented mode | **KEEP** |
| `runtime.nofile_soft_limit` | 4096 | Runtime limits section | **KEEP** |
| `terminal.backend/timeout/home_mode/lifetime` | local / 180 / auto / 300 | Local backend; container keys are latent | **KEEP** |
| `browser` | inactivity 120, extension_control false | Browser toolset is disabled on these trees | **KEEP** |
| `checkpoints.enabled` | true | Checkpoints & rollback | **KEEP** |
| `tool_loop_guardrails.warnings_enabled` | true | Guardrails section | **KEEP** |
| `compression.enabled/target_ratio/protect_first_n` | true / 0.2 / 3 | Defaults in that neighborhood | **KEEP** |
| `prompt_caching.cache_ttl` | 5m | Prompt caching; local LMS benefit is smaller than Anthropic | **KEEP** |
| `memory.memory_enabled` / char limits | true / 2200 / 1375 | DOCS quotes those exact caps | **KEEP** |
| `delegation.max_iterations/children/depth` | 250 / 3 / 1 | Delegation page; toolset is disabled so this block is latent | **KEEP** numbers |
| `moa.fanout` / `degraded_reference_policy` | user_turn / loud | MoA slot docs | Policy **KEEP**; enabled flag is per file |
| `skills.creation_nudge_interval` | 15 | — | **KEEP** |
| `telemetry.shared_metrics` | false | Privacy | **KEEP** |
| `updates` | pre_update_backup false, backup_keep 5, non_interactive stash | Update behavior | **KEEP** |
| `_config_version` | 40 | — | **KEEP** |
| `group_sessions_per_user` | true | Group chat isolation | **KEEP** |
| `session_reset` | mode none, idle_minutes 1440, at_hour 4 | Messaging sessions persist until `/new`; idle does not finalize memory | **KEEP** |
| `kanban.review_dispatch` | true | Kanban toolset disabled, so latent | **KEEP** |
| `code_execution` | timeout 300, max_tool_calls 50 | Toolset disabled | **KEEP** |
| `display.show_reasoning` / `display.streaming` | false / true | CLI display streaming, separate from gateway `streaming.enabled` | **KEEP** |
| `stt` / `tts` | stt on, edge voice en-US-AriaNeural | Both toolsets disabled | **KEEP** |
| `providers.*.stale_timeout_seconds` / `request_timeout_seconds` | 1800 / 2400 on the lms-local entry | Explicit stale timeout wins over the 90s/900s implicit local defaults | **KEEP** (Trader already endorsed 1800) |

`agent.disabled_toolsets` is the real tool fence (DOCS: applied after `platform_toolsets`).

---

## Root `~/.hermes/config.yaml` — 48 keys

Life gateway. Goal: Hermex + Signal/Matrix, Ornith on LMS, MoA is Assistant-owned, MemPalace stays off this file.

| Key | FACT | Verdict |
|-----|------|---------|
| `model` | default **`ornith-1.5-35b-a3b`**, provider lms-local, **context_length 131072** | **CHANGE** (draft). Loaded id is `ornith-1.5-35b-a3b-mlx` at 65536. DOCS min context is 64k, so 65536 is legal. 131072 describes a window LMS is not serving. Prior infra charter VETO’d the retarget; this draft puts it back on Burton’s list |
| `providers` | `only_configured: true`. `custom:lms-local.model` is the **bare** id. Model map: bare ornith 131072, bare `qwen3.8-27b` 65536, obliterated-k, gpt-oss, ud, mmproj, nomic embed. **No mlx ids** | **CHANGE** (draft) with the model align. Timeouts **KEEP** |
| `toolsets` | `hermes-cli`, `web` | **KEEP**. Re-thinning to clarify/memory/todo is a behavior cut |
| `database` | wal | **KEEP** |
| `runtime` | nofile 4096 | **KEEP** |
| `agent` | reasoning **low**, max_turns 500, verbose false. disabled: tts, bfl, image_gen, computer_use, homeassistant, discord, discord_admin, spotify, yuanbao, stt, delegation, kanban, cronjob, code_execution, browser, a2a, video, vision, x_search. **clarify stays on** | **KEEP**. `gateway_turn_lease_timeout`, `session_stall_timeout`, `reconnect_attention_after` unset → DOCS defaults 5s / 300s / 7200s. Matrix `needs_attention` matches the 2h default (retry started 08:35 PT). **DEFER** tuning those |
| `terminal` | backend local, **cwd `.`**, home_mode auto, timeout 180. No TCC deny | **CHANGE** (draft, low): same venv python as the CTO TCC bug. Life has no `approvals.deny` and no `block-tcc-appdata` hook. Extend the fence only with Burton’s yes |
| `browser` | inactivity 120, extension off | **KEEP** (toolset off) |
| `checkpoints` | enabled | **KEEP** |
| `tool_loop_guardrails` | warnings on, **hard_stop on** (2/3/2 warn, 5/8/5 stop) | **KEEP** for life chat |
| `compression` | enabled, threshold **0.7**, **threshold_tokens 40000**, target 0.2, protect_last_n 20, proactive_prune_tokens 10000, idle_compact 0 | **DEFER**. DOCS: fires at the lower of 0.7×window and 40000, so life compacts at **40k tokens** even if the window is 128k or 64k. That is tighter than the 0.50 default. Hot-reload would apply a future edit without a bounce |
| `prompt_caching` | 5m | **KEEP** |
| `auxiliary` | free_only true. compression provider main, reasoning **none**. approval model **bare ornith** @ :1234, timeout 60. title/memory_query/triage/kanban reasoning none | **CHANGE** (draft) approval model id with the root align. Compression effort **KEEP** (DOCS: side tasks at none/low) |
| `display` | tool_progress **off**, pet enabled false, skin default, matrix tool_progress all, show_reasoning false | **KEEP** |
| `tts` | edge Aria | **KEEP** |
| `stt` | enabled, local base, openai whisper-1 | **KEEP** (toolset off) |
| `memory` | enabled, user profile on, write_approval **false**, provider **holographic**, caps 2200/1375, nudge 10 | **KEEP**. Matches DOCS caps and the life goal (holographic short memory, MemPalace not on root) |
| `delegation` | model bare ornith, provider lms-local, :1234, iterations 250, reasoning empty, children 3, depth 1 | Toolset disabled → latent. **CHANGE** id only inside the root align so a future enable cannot call a missing model |
| `moa` | **enabled true**. Reference `qwen3.8-27b` on lms-local enabled. Aggregator bare ornith on lms-local. max_tokens 4096, reference_max_tokens 600, fanout user_turn, degraded **loud**. Preset `default` mirrors that | **CHANGE** (draft, Assistant). Loaded neighbor is `qwen/qwen3.6-35b-a3b`, not `qwen3.8-27b`. Loud degraded policy means a missing reference is noisy on user turns. Draft recommendation: `enabled: false`. Burton may instead point the reference at the loaded `qwen/qwen3.6-35b-a3b`. Infra stamp VETO’d editing `moa`; this line does not apply it |
| `skills` | write_approval true, nudge 15, disabled apple-reminders, claude-code, codex, findmy, imessage | **KEEP** |
| `timezone` | America/Los_Angeles | **KEEP** |
| `approvals` | only `destructive_slash_confirm: false`. No `mode` → DOCS default **smart**. No deny list | **DEFER** the slash-confirm flip (docs default is true). TCC deny is the separate draft on `terminal` |
| `plugins` | disabled **ponytail**. enabled `observability/langfuse` | **KEEP** |
| `security` | allow_private_urls **false** | **KEEP** for life |
| `cron` | model bare ornith, provider lms-local, mirror_delivery true | **CHANGE** (draft) with the id align. Toolset `cronjob` is disabled; the gateway ticker still runs `jobs.json` (9 enabled). Per-job model pins need the same id or they override `cron.model` |
| `kanban` | review_dispatch true, max_in_progress 2, per_profile 1 | **KEEP** (toolset off) |
| `code_execution` | 50 calls / 300s | **KEEP** (toolset off) |
| `tools` | tool_search false as above | Disk **KEEP**. Runtime reload is backlog item 1 |
| `streaming` | enabled **true** | **KEEP**. DOCS default is false; Signal does not progressive-edit (docs auto-disable that platform). Life already chose on |
| `onboarding` | seen openclaw_residue_cleanup, busy_input_prompt | **KEEP** |
| `telemetry` | shared metrics off | **KEEP** |
| `updates` | stash / keep 5 | **KEEP** |
| `computer_use` | backend cua | **KEEP** (toolset off) |
| `_config_version` | 40 | **KEEP** |
| `custom_providers` | (1) name lms-local @ :1234, **model `qwen3.8-27b-obliterated-k`**, 10 model stubs. (2) litellm @ `:4000`, model bare ornith, key via env name. `:4000` is listening | **DEFER** picker-model hygiene. **VETO** retargeting the desk onto LiteLLM or obliterated-k. Active route is `providers.custom:lms-local`, not this list’s model field |
| `group_sessions_per_user` | true | **KEEP** |
| `image_gen` | provider nous | **KEEP** (toolset off) |
| `known_builtin_toolsets` | cli list, 26 names | **KEEP** inventory |
| `known_plugin_toolsets` | cli: a2a, spotify | **KEEP** |
| `mcp_servers` | **14 servers, all enabled false**: tradingview, laya-decide, ddg-search, brave-search, exa-search, claude-flow, fortress, codebase-memory, gitlab, kive, mempalace, comfyui, wolfram, graft. MemPalace include-list is read-ish (search/status/list/kg/graph/diary_read/taxonomy/check_duplicate) | **KEEP** all off. **VETO** MemPalace-on-root and any root MCP enable |
| `model_aliases` | `ornith` → `ornith:9b` **ollama-local**. `qwen-uncensored` and `qwen-27b` → `orcarouter/Qwen3.8-27B-Uncensored:q6_K` ollama-local | **CHANGE** (draft). `/model ornith` on the life gateway aims at Ollama `:11434`, not LMS |
| `platform_toolsets` | present | **KEEP**. DOCS: `disabled_toolsets` still strips after this map |
| `platforms` | matrix **enabled**, home user `@obs10:openclaw-vps.tail4aeaf.ts.net` (room id redacted). Signal is `.env`, not this block | **KEEP** the flag. Runtime Matrix is the reconnect draft. **VETO** `gateway --replace` and any Signal bounce to “fix” Matrix |
| `session_reset` | none / 1440 | **KEEP** |
| `video_gen` | provider nous | **KEEP** (toolset off) |
| `phase6_ollama` | main_11434 **disabled**, mirofish_11435 kept, lms_1234 = Hermes + RuVocal, note dated 2026-09-14 | **KEEP**. **VETO** reviving `:11434` as the desk |
| `_hermex_picker_hidden_providers` | ollama-local models parked out of `providers` (ornith:9b, ornith-1.5:latest, omni-qwen-122b, gpt-oss, glm cloud, …). Note: not read by the model picker | **KEEP** hidden |
| `_hermex_picker_hidden_custom_providers` | non-LMS custom entries parked 2026-09-25. Note says LiteLLM `:4000` was left in the live list on purpose | **KEEP** hidden |

---

## Profile `valedia` — 45 keys

Second tree. **Not the running life gateway.** Stopped 2026-09-28 after Signal phone-lock against the default profile. Goal if it is ever started: life assistant with MemPalace, MoA off, ponytail off. **VETO** starting it while root holds Signal.

| Key | FACT | Verdict |
|-----|------|---------|
| `model` | default **`ornith-1.5-35b-a3b-mlx`**, ctx **65536**, lms-local | **KEEP** (matches `lms ps`) |
| `providers` | keys include **`custom:ollama-local`**, `ollama-launch`, and `custom:lms-local`. LMS model is the mlx id, stale 1800, request 2400, mlx catalog rows (ornith mlx 131072, 9b-mlx 65536, qwen3.8-27b, qwen3.6, ulam-1) | LMS row **KEEP**. Ollama provider still **in** the map (root hid its copy). **CHANGE** (draft, latent): hide ollama-local the way root already did |
| `toolsets` | hermes-cli, web | **KEEP** |
| `database` | wal | **KEEP** |
| `runtime` | 4096 | **KEEP** |
| `agent` | reasoning **low**. Same thick disable list as root (clarify stays on; delegation/cronjob/browser off) | **KEEP** |
| `terminal` | cwd `.`, no TCC hook | **DEFER** until anyone starts this profile. Then copy the CTO fence first |
| `browser` | same as root | **KEEP** |
| `checkpoints` | on | **KEEP** |
| `tool_loop_guardrails` | hard_stop **true** | **KEEP** |
| `compression` | same as root: 0.7 and threshold_tokens 40000 | **DEFER** (same 40k cap) |
| `prompt_caching` | 5m | **KEEP** |
| `auxiliary` | free_only, compression none, title/triage/kanban none. **No approval block** | **DEFER** |
| `display` | tool_progress off, pet off | **KEEP** |
| `tts` / `stt` | same as root | **KEEP** |
| `memory` | holographic, user profile on, write_approval false, caps match docs | **KEEP** |
| `delegation` | **`ornith-1.5:latest` @ `custom:ollama-local` `:11434`**, iterations 250 | Toolset off, gateway stopped. **CHANGE** (draft, latent) to lms mlx or clear |
| `moa` | **enabled false**. Aggregator still `ornith:9b` @ ollama-local. max_tokens 4096, reference_max_tokens 600 | Enabled flag **KEEP**. Dead preset **DEFER**. **VETO** turning MoA on |
| `skills` | same disable list as root, write_approval true | **KEEP** |
| `timezone` | America/Los_Angeles | **KEEP** |
| `approvals` | destructive_slash_confirm false only | **DEFER** |
| `plugins` | ponytail in **disabled**, langfuse in enabled | **KEEP** (infra stamp applied) |
| `security` | allow_private_urls false | **KEEP** |
| `cron` | model **`ornith-1.5:latest`**, provider **ollama-local**, mirror_delivery true. 0 job files | **CHANGE** (draft, latent) id/provider. Nothing fires while stopped |
| `kanban` | max_in_progress 2 / per_profile 1 | **KEEP** |
| `code_execution` | 50 / 300 | **KEEP** |
| `tools` | tool_search false | **KEEP** |
| `streaming` | true | **KEEP** |
| `onboarding` | same seen flags as root | **KEEP** |
| `telemetry` / `updates` | off / stash | **KEEP** |
| `computer_use` | cua | **KEEP** |
| `_config_version` | 40 | **KEEP** |
| `custom_providers` | Ollama `:11434` model `ornith:9b` (13 models); Flash-Next via `:11434` model `flash-next` (33); LMS `:1234` model mlx (6) | **DEFER** prune. Active `model.default` is already LMS |
| `group_sessions_per_user` | true | **KEEP** |
| `image_gen` / `video_gen` | nous | **KEEP** |
| `known_builtin_toolsets` / `known_plugin_toolsets` | present | **KEEP** |
| `mcp_servers` | **enabled:** claude-flow, codebase-memory, kive, **mempalace**, comfyui, graft. **disabled:** fortress, gitlab, wolfram | MemPalace **KEEP** (life-profile goal, and this file is not root). The other five **DEFER** — they do not cost anything while the gateway is stopped, and they will spawn if the profile starts |
| `model_aliases` | same Ollama `ornith:9b` / qwen q6 aliases as root | **CHANGE** (draft, latent) |
| `platform_toolsets` | present | **KEEP** |
| `platforms` | matrix enabled (same home user) | **KEEP** the flag. **VETO** starting this gateway (Signal lock) |
| `session_reset` | none / 1440 | **KEEP** |

Keys root has that this file does not: `phase6_ollama`, both `_hermex_picker_hidden_*`. Keys this file has that traders add later (`hooks`) are absent. No `hooks` / `hooks_auto_accept`.

---

## Profile `valedia-cto` — 43 keys

Coding desk. Running pid 51268 since the 14:47 bounce, so this yaml **is** what that process loaded. Goal: Ornith mlx, MemPalace read-ish, TCC fence, Matrix off, MoA off.

| Key | FACT | Verdict |
|-----|------|---------|
| `model` | default **mlx**, provider lms-local, **no context_length** | **CHANGE** (draft): set `context_length: 65536` to match the loaded server. Provider row still says 131072. DOCS: configure the effective window (Ollama `num_ctx` analogy). Infra stamp did not retarget `model.default`; adding the missing length is the gap |
| `providers` | only `custom:lms-local`, model mlx, stale 1800, request 2400, catalog rows include mlx / 9b-mlx / qwen3.8-27b / qwen3.6 / ulam at 65536 or 131072 as above | Timeouts **KEEP**. Per-model 131072 vs top-level (unset) is the same unknown precedence |
| `database` / `runtime` | wal / 4096 | **KEEP** |
| `agent` | reasoning **high**, max_turns 500. disabled: computer_use, video, tts, bfl, image_gen, x_search, kanban, cronjob, browser, homeassistant, discord, discord_admin, spotify, yuanbao, stt, a2a, delegation, code_execution, vision. **web and clarify stay on** | **KEEP** (coding desk, not the trader purity list) |
| `terminal` | cwd **`/Users/burtonchatelain/.openclaw/workspace-v2`**, home_mode auto, timeout 180 | **KEEP**. **VETO** weakening |
| `browser` | extension off | **KEEP** |
| `checkpoints` | on | **KEEP** |
| `tool_loop_guardrails` | warnings on, **hard_stop false** | **KEEP** (warn, don’t kill a long code turn) |
| `compression` | threshold **0.5** (docs default), no threshold_tokens, protect_last_n **40**, proactive_prune **0** | **KEEP** ratios. They only mean ~32k once context_length is 65536 |
| `prompt_caching` | 5m | **KEEP** |
| `auxiliary` | approval only: mlx @ :1234, timeout 60 | **KEEP** |
| `display` | tool_progress **none**, show_reasoning false, display.streaming true | **KEEP** |
| `tts` / `stt` | present, toolsets off | **KEEP** |
| `memory` | enabled, user profile **on**, write_approval **true**, caps 2200/1375. **No `provider` key** (built-in file memory, not holographic) | **KEEP**. Long graph is MemPalace MCP, not a second provider. **VETO** Honcho / a third store |
| `delegation` | **mlx @ lms-local :1234**, reasoning **high**, 250/3/1 | **KEEP** (infra stamp). Toolset still disabled, so latent |
| `moa` | **enabled false**. Preset still aggregator `ornith-1.5:latest` @ ollama-local, second reference openrouter deepseek **enabled false**, max_tokens 16000 | Flag **KEEP**. **VETO** enabling. Dead names **DEFER** (editing `moa` was vetoed on the infra pass) |
| `skills` | write_approval true. disabled adds apple-notes vs root | **KEEP** |
| `approvals` | mode **smart**, smart_policy text forbids find over `~` / Library / Containers, **17** deny globs (`find ~`, `/usr/bin/find`, Containers, Group Containers, `$HOME`), command_allowlist Paperclip `127.0.0.1:3100` curls plus `curl -sS *` | **KEEP**. **VETO** weakening. DOCS: user deny is below yolo and above the hardline floor |
| `hooks_auto_accept` | true | **KEEP** (hook is fail_closed; auto-accept is how the block registers) |
| `hooks` | pre_tool_call matcher `terminal\|search_files\|read_file\|write_file\|patch`, command `~/.hermes/agent-hooks/block-tcc-appdata.py`, timeout 5, **fail_closed true** | **KEEP**. **VETO** weakening |
| `security` | allow_private_urls **true** | **KEEP** (localhost Paperclip / API). DOCS SSRF default is closed; this is an explicit local exception |
| `cron` | model mlx, provider lms-local. 0 job files. cronjob toolset off | **KEEP** |
| `kanban` | review_dispatch true, no max keys | **KEEP** |
| `code_execution` | 300/50, toolset off | **KEEP** |
| `tools` | tool_search false | **KEEP** (process reloaded) |
| `streaming` | **false** | **KEEP**. DOCS master default is false. `display.streaming: true` is the CLI pane, not gateway progressive edit |
| `onboarding` | tool_progress_prompt seen | **KEEP** |
| `telemetry` / `updates` | off / stash | **KEEP** |
| `computer_use` | cua, toolset off | **KEEP** |
| `_config_version` | 40 | **KEEP** |
| `model_aliases` | ornith → mlx lms-local. qwen-27b → `qwen/qwen3.8-27b` (in catalog, not loaded). qwen-uncensored → `qwen3.8-27b-obliterated-k` (**not in today’s catalog**) | ornith alias **KEEP**. obliterated alias **DEFER** |
| `session_reset` | none / 1440 | **KEEP** |
| `group_sessions_per_user` | true | **KEEP** |
| `platform_toolsets` | present | **KEEP** |
| `custom_providers` | Ollama `:11434` model `ornith-1.5:latest` (5 models). **Flash-Next `http://127.0.0.1:8081/v1` model `flash-next`** | **CHANGE** (draft, hygiene): `:8081` is signal-cli’s HTTP port (pid 908). Selecting that provider would hit Signal’s daemon, not a model. **VETO** moving signal-cli. Active model is LMS, so this is latent |
| `mcp_servers` | **only mempalace, enabled true**, same read-ish include list | **KEEP**. **VETO** adding trader MCPs or root’s 14 |
| `known_plugin_toolsets` / `known_builtin_toolsets` | present (cli builtin list 25) | **KEEP** |
| `image_gen` | nous, toolset off | **KEEP** |
| `platforms` | matrix **enabled false** (home user still stored) | **KEEP** |

Absent vs root (intentional): `toolsets`, `timezone`, `plugins`, `video_gen`, `phase6_ollama`, hermex-hidden keys. No `plugins.disabled: ponytail`. **UNKNOWN** whether an installed ponytail plugin defaults on when the key is missing. Do not assume it is off.

---

## Trading profiles — scout / decider / auditor

Trader goal (plan + infra stamp): Ornith-solo, MoA off, empty MCP, web only on scout, clarify off, TCC fence, no `--live`. Hashes **unchanged** since 14:07 PT (`e9aa6853f346ba8b`, `d9a3bb21110ad4a2`, `a332842c36013d88`). Processes up since 14:09 PT, so they loaded this yaml. **No trader yaml CHANGE in this draft.**

### Identical across the three

| Key | FACT | Verdict |
|-----|------|---------|
| `model` | mlx, lms-local, **context_length 65536** | **KEEP** |
| `providers` | only lms-local, model mlx, stale 1800, request 2400. Per-model `ornith-1.5-35b-a3b-mlx.context_length` **131072** (9b-mlx, qwen3.8-27b, qwen3.6, ulam-1 at 65536) | Timeouts and top-level 65536 **KEEP**. Precedence vs 131072 is **UNKNOWN**. Trader note already accepted 131072 as the capability ceiling. **VETO** an omlx move and **VETO** making 9B / Ulam / Qwen3.6 the money default |
| `database` / `runtime` | wal / 4096 | **KEEP** |
| `terminal` | cwd workspace-v2, local, home auto, 180 | **KEEP**. **VETO** weaken |
| `browser` | extension off, toolset disabled | **KEEP** |
| `checkpoints` | on | **KEEP** |
| `tool_loop_guardrails` | warnings on, hard_stop **false** | **KEEP** |
| `compression` | 0.50, protect_last_n 40, prune 0, no threshold_tokens | **KEEP** |
| `prompt_caching` | 5m | **KEEP** |
| `auxiliary` | approval mlx @ :1234 timeout 60 | **KEEP** |
| `display` | tool_progress **all**, show_reasoning false | **DEFER** (log noise only) |
| `tts` / `stt` | present, toolsets off | **KEEP** |
| `memory` | enabled, **user_profile false**, caps match docs, **no provider key** | **KEEP** (no holographic, no MemPalace) |
| `delegation` | mlx @ lms-local, 250/3/1. Toolset disabled | **KEEP** |
| `moa` | **enabled false**. Names still `ornith-1.5:latest` @ ollama-local plus a disabled openrouter deepseek reference. max_tokens 16000 | **KEEP** off. **VETO** enabling or pointing trader MoA at Grok/Qwen. Dead names **DEFER** under the moa-edit veto |
| `skills` | disabled apple-notes, apple-reminders, findmy, imessage, claude-code, codex | **KEEP** the list |
| `security` | allow_private_urls **true** | **KEEP** (local API). Paper only; this flag is not `--live` |
| `cron` | mlx @ lms-local. 0 jobs. cronjob toolset off | **KEEP** |
| `kanban` | review_dispatch true, toolset off | **KEEP** |
| `code_execution` | 300/50, toolset off | **KEEP** |
| `streaming` | **false** | **KEEP** |
| `telemetry` / `updates` | off / stash | **KEEP** |
| `_config_version` | 40 | **KEEP** |
| `model_aliases` | ornith → mlx. qwen-27b → `qwen/qwen3.8-27b`. qwen-uncensored → obliterated-k (not in catalog) | Desk alias **KEEP**. obliterated **DEFER** |
| `session_reset` | none / 1440 | **KEEP** |
| `group_sessions_per_user` | true | **KEEP** |
| `platform_toolsets` | cli/webui/desktop = clarify, memory, todo; messaging rows are hermes-* packs | **KEEP**. `disabled_toolsets` still strips clarify on all three |
| `mcp_servers` | `{}` | **KEEP**. **VETO** MemPalace or any MCP on trader profiles |
| `approvals` | mode smart, same 17 deny globs, same Paperclip curl allowlist, same smart_policy | **KEEP**. **VETO** weaken |
| `hooks_auto_accept` | true | **KEEP** |
| `hooks` | same `block-tcc-appdata.py`, fail_closed true | **KEEP**. **VETO** weaken |
| `tools` | tool_search false | **KEEP** |

No `platforms`, `plugins`, `timezone`, `toolsets`, `image_gen`, `video_gen`, `computer_use`, `phase6_ollama` on scout or decider. No paperclip / `--live` / OKX strings in the yaml (plan FACT, still true: those words are not keys here).

### Scout only (37 keys)

`agent.disabled_toolsets` omits `web` and `search`, includes clarify, messaging, cronjob, browser, delegation, and the media/chat packs. **KEEP** (scout may research; it may not clarify-loop or delegate).

`memory.write_approval` **true**. `skills.write_approval` **true**. **KEEP**.

`custom_providers`: Ollama `:11434` `ornith-1.5:latest` (33 models) and **Flash-Next `:8081`**. **DEFER** hide `:8081` (signal-cli collision). Not the selected model.

`onboarding`, `known_builtin_toolsets`, `known_plugin_toolsets`: **absent**. **DEFER** (inventory only).

### Decider only (37 keys)

`agent.disabled_toolsets` adds **web, search, clarify**. **KEEP** (decision purity).

`memory.write_approval` **false** (scout and auditor are true). `skills.write_approval` **false** (the others are true). **DEFER** — inconsistency, not a money-path bug. Do not flip inside a trader “verify only” pass.

`custom_providers`: Ollama `:11434` only (no Flash-Next). **DEFER**.

`onboarding` / `known_*`: absent. **DEFER**.

### Auditor only (40 keys)

Same disable list as decider (web, search, clarify off). **KEEP**.

`memory.write_approval` **true**. `skills.write_approval` **true**. **KEEP**.

`onboarding` seen `tool_progress_prompt`. `known_plugin_toolsets` cli a2a+spotify. `known_builtin_toolsets` cli list length 25. **KEEP** as inventory.

`custom_providers`: Ollama `:11434` `ornith-1.5:latest` (12 models). **DEFER**.

`agent.personalities` `{}` on auditor (also observed on the scout/decider agent objects where present). Empty. **KEEP**.

---

## Cross-cutting verdicts

| Topic | FACT | DOCS / goal | Verdict |
|-------|------|-------------|---------|
| Tool search | Disk false on all six. CODE: false is off; auto means on | Infra APPLY already done | **KEEP** disk. Life reload is the leftover |
| Root MoA | On, reference not loaded, aggregator bare id | Assistant lane. Trader MoA stays false | **CHANGE** draft, Assistant only. **VETO** copying MoA onto traders |
| Model id align | Profiles + CTO + traders use mlx. Root default, root provider, root cron, 8 cron jobs, root approval, root delegation, root MoA aggregator use the bare id | LMS catalog + `lms ps` | **CHANGE** draft, Assistant. **VETO** unloading Qwen3.6 to “make room,” **VETO** swapping the desk to 9B/Ulam/Qwen3.6 |
| Context vs load | Life yaml 131072, CTO unset, traders/valedia top-level 65536, LMS Ornith 65536, provider row often 131072 | Min 64k. Effective window should be what the server serves | **CHANGE** draft: root 65536 + CTO set 65536. Trader top-level **KEEP** |
| MemPalace | Off root (14 MCPs off). On valedia + CTO, read-ish include. Off traders | Plan split | **KEEP**. **VETO** root enable |
| Ponytail | Disabled root + valedia. Key absent CTO + traders | Infra APPLY for valedia | **KEEP** where set. **UNKNOWN** if missing key loads it |
| Holographic | Root + valedia only | Life short memory | **KEEP**. Traders/CTO built-in file memory **KEEP** |
| TCC | Hook + deny + cwd on CTO + 3 traders. Proof was the prior stamp; this pass did not re-run `find` | Same venv python. No `rg` on default PATH | **KEEP**. **VETO** weaken. Optional draft: extend to root |
| Delegation/cron ollama | Valedia profile only, and it is stopped. CTO + traders already lms mlx. Root is lms but bare id | Toolsets disabled; root cron jobs are **not** disabled | **CHANGE** draft for root ids (jobs actually run) and latent valedia profile |
| Matrix | Tunnel up, adapter retrying 6h+, needs_attention, error `failed to reconnect` (not a typed fatal token class, so DOCS say retries continue) | Signal connected on the same process | **DEFER** diagnose. **VETO** `--replace` and **VETO** bouncing Signal to refresh Matrix |
| Signal phone lock | Profile valedia stopped because default already owns the account | DOCS token lock | **VETO** starting profile valedia |
| `:8081` Flash-Next | CTO + scout custom provider. signal-cli listens there | Latent unless selected | **CHANGE** draft hygiene. **VETO** retargeting signal-cli |
| LiteLLM `:4000` | Root custom provider, port open, not `model.provider` | phase6 says LMS is Hermes | **KEEP** off the desk. **VETO** cutover |
| Streaming | Life/valedia true. CTO + traders false | DOCS default false. Local prefill + stale detectors are why traders are off | **KEEP** |
| Reasoning | Life low. CTO + traders high | Plan: leave trader high | **KEEP**. Scout-lower is **DEFER** until a measured Paperclip cycle says so |
| Approvals mode | CTO/traders explicit smart. Life/valedia implicit smart, slash confirm off | DOCS | **KEEP** smart. Slash confirm **DEFER** |
| CoS cron name | `chief-of-staff-daily` enabled on root, no per-job model | Plan said no second CoS personality stack | **DEFER** to Assistant. This draft does not delete jobs |
| MLX / omlx | LMS is already serving the MLX Ornith weights on `:1234` | §4: omlx is a second server on `:8000`; MLX often wins tok/s, llama.cpp/LMS often wins TTFT. Not mandatory | **Option only. No cutover plan.** Stay on LMS. A later Burton-supervised A/B is the only next measurement |

---

## Prioritized CHANGE backlog (Burton yes — not applied)

### Assistant lane

1. **Reload life gateway** `ai.hermes.gateway` so disk `tool_search: false` is actually in pid 66704’s successor. Cost: Signal drops for the restart; Matrix’s retry clock resets and keeps failing until the homeserver path is healthy. Do it as a normal kickstart of that one agent, never `hermes gateway --replace`. Leave signal-cli 908 and the matrix tunnel 925 up.
2. **Root MoA.** Set `moa.enabled: false`, or keep it and point the reference slot at loaded `qwen/qwen3.6-35b-a3b` **and** the aggregator at `ornith-1.5-35b-a3b-mlx`. Recommendation in this draft: off.
3. **One root id align** to `ornith-1.5-35b-a3b-mlx` plus `model.context_length: 65536`: `model.default`, `providers.custom:lms-local.model`, `cron.model`, the eight job pins in `cron/jobs.json`, `auxiliary.approval.model`, `delegation.model`. Aggregator too if MoA stays on. `model_aliases.ornith` off Ollama.
4. **CTO** `model.context_length: 65536` only. Desk id is already mlx. Bounce CTO after that edit; it does not own Signal.
5. **Profile valedia** delegation/cron/aliases/ollama provider hide — only as disk hygiene. Do not start the profile.
6. **Matrix** log diagnosis against `127.0.0.1:6167` with the tunnel left up. No gateway replace.
7. **Hide Flash-Next `:8081`** on CTO and scout custom_providers so a picker miss cannot open signal-cli.
8. **Optional:** copy the TCC hook + deny + cwd onto root. Same python identity, cwd is still `.`

### Trader lane

No required yaml change. Verified: tool_search false, ctx 65536, delegation/cron lms mlx, MoA false, MCP empty, TCC hook fail_closed, web on scout only, web/search/clarify off on decider and auditor, hashes match the infra stamp.

**DEFER only:** decider `memory.write_approval` / `skills.write_approval` false vs the other two true; dead MoA ollama names; obliterated-k alias; Flash-Next on scout; `display.tool_progress: all`.

**VETO (stands):** MoA or Qwen on the trader serves, MemPalace on traders, LMS load/unload, omlx cutover, 9B / Ulam / Qwen3.6 as money default, `--live` / OKX / venue orders, `gateway --replace`, weakening the TCC fence, SOUL voice rewrite.

---

## MLX / omlx — option, not work

DOCS §4: omlx serves MLX safetensors at `http://127.0.0.1:8000` by default; llama.cpp is the managed desktop runtime; Hermes rejects models under 64k context. FACT: LM Studio on `:1234` is already serving `ornith-1.5-35b-a3b-mlx` at 65536 and `qwen/qwen3.6-35b-a3b` at 262144 (~40 GB resident together). Catalog also holds `ornith-1.5-9b-mlx` and `ulam-1`, neither loaded. No omlx port is configured. A cutover would add a second daemon and a new `custom:` provider. This draft does not schedule that. If LMS-MLX tok/s is the question later, measure one prompt inside LMS under Burton’s supervision. Do not unload either resident to run the test.

---

## Unknowns

- Which key wins when `model.context_length` is 65536 and `providers.custom:lms-local.models.<id>.context_length` is 131072.
- Whether a missing `plugins` key enables ponytail from `~/.hermes/plugins/ponytail/`.
- In-memory tool_search of pid 66704 (inferred from start time vs mtime + the hot-reload rule, not from a process dump).
- Why Matrix says `failed to reconnect` while `:6167` accepts TCP. Not diagnosed.
- Whether LMS would 404 the bare ornith id or alias it. No completion was sent. Catalog list does not contain the bare id.
- Root `cron/` has 221 non-job files. Not classified.
- Profile `valedia` enabled MCPs (claude-flow, cbm, kive, comfyui, graft) have no live process. Behavior on a future start was not probed.
- Docs mine does not inline the tool-search page body or a full DEFAULT_CONFIG (§7 gap 2).

---

## Fences honored this pass

No `--live`, no venue orders, no OKX, no VAL-12, no yaml/plist/`.env` write, no kickstart, no `lms load`/`unload`, no `hermes gateway --replace`, no SOUL edit, no MemPalace-on-root, no trader yaml rewrite, no catalog rewrite. Screens `ot-valedia-desk` and `valedia-glance` not touched.

**This audit is not 100% applied.**

**GROK_EXIT:0**
