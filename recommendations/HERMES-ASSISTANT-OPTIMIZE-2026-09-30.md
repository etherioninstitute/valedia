# Hermes Assistant optimize stamp (2026-09-30)

**Updated:** 2026-09-30 ~14:48 PT
**Charter:** `recommendations/GROK-CHARTER-HERMES-ASSISTANT-OPTIMIZE-2026-09-30.md` (revised; hard fences honored)
**Gap plan:** `recommendations/HERMES-VALEDIA-OPTIMIZE-PLAN.md` (Trader CHANGE verify-only)
**GROK_EXIT:0**

Charter VETO wins over the older MoA-off / root model-retarget list. Applied the charter APPLY set. Root MoA and root model id were not edited.

## Applied

| Surface | Before | After |
|---------|--------|-------|
| Root `~/.hermes/config.yaml` `tools.tool_search.enabled` | `auto` | `false` |
| Root `moa.enabled` | `true` | `true` (untouched) |
| Root `model.default` | `ornith-1.5-35b-a3b` | same (untouched) |
| Profile `valedia` `tools.tool_search.enabled` | `auto` | `false` |
| Profile `valedia` ponytail | `plugins.enabled` | `plugins.disabled` |
| Profile `valedia` MoA / model id / MemPalace / Matrix | MoA `false`, model `ornith-1.5-35b-a3b-mlx` | untouched |
| Profile `valedia-cto` delegation | `omni-qwen-122b:latest` @ `custom:ollama-local` `:11434` | `ornith-1.5-35b-a3b-mlx` @ `custom:lms-local` `:1234` |
| Profile `valedia-cto` cron | same ollama id / provider | `ornith-1.5-35b-a3b-mlx` / `custom:lms-local` |
| Profile `valedia-cto` `tools.tool_search.enabled` | `auto` | `false` |
| Profile `valedia-cto` desk `model.default` | `ornith-1.5-35b-a3b-mlx` | same |
| Profile `valedia-cto` MoA | `enabled: false` (preset still names ollama models) | block untouched |
| TCC fence (CTO + trading) | hook `block-tcc-appdata.py` `fail_closed: true`, `terminal.cwd` workspace-v2, `approvals.deny` | byte-identical (not in the diff) |

Profile `valedia` delegation/cron still point at `custom:ollama-local` / `ornith-1.5:latest`. Charter section B says leave that profile's model ids alone. Scrub was CTO-only.

## Trading verify only (no edit, no bounce)

| Key | scout | decider | auditor |
|-----|-------|---------|---------|
| `tool_search.enabled` | false | false | false |
| `model.context_length` | 65536 | 65536 | 65536 |
| delegation | `custom:lms-local` / `ornith-1.5-35b-a3b-mlx` | same | same |
| cron | `custom:lms-local` / `ornith-1.5-35b-a3b-mlx` | same | same |
| TCC | hook + `fail_closed: true` + cwd workspace-v2 | same | same |

SHA-256 prefix unchanged: scout `e9aa6853f346ba8b`, decider `d9a3bb21110ad4a2`, auditor `a332842c36013d88`.

MoA blocks on the trading profiles still name `custom:ollama-local` models and stay `enabled: false`. Charter forbids editing `moa`. Not restored.

## Backups

- `~/.hermes/config.yaml.bak-20260930-assistant-optimize`
- `~/.hermes/profiles/valedia/config.yaml.bak-20260930-assistant-optimize`
- `~/.hermes/profiles/valedia-cto/config.yaml.bak-20260930-assistant-optimize`

Unified diff vs those backups is only the rows in Applied.

## Bounce

| LaunchAgent | Action | Proof |
|-------------|--------|-------|
| `ai.hermes.gateway-valedia-cto` | `launchctl kickstart -k gui/501/…` at 14:47 PT | old pid 80416 → supervisor 51261 / gateway 51268. `state=running`. Listening `127.0.0.1:8645`. Log: `Active profile: valedia-cto`, api_server connected. Matrix stays explicitly disabled on this profile. |
| `ai.hermes.gateway-valedia-trading-{scout,decider,auditor}` | not bounced | still listen 8642 / 8643 / 8644 (pids 79797 / 79891 / 79929) |
| `ai.hermes.gateway` (life, `HERMES_HOME=~/.hermes`) | **not** bounced | pid 66534, elapsed 6h+, last start `2026-09-30 08:35:50`. This process is the life gateway. Kickstart would drop its Signal/Matrix adapters. Reload left to Assistant. |
| `com.burton.hermes-signal` | not touched | pid 908, signal-cli daemon |
| `com.burton.matrix-tunnel` | not touched | pid 925, ssh `-L 127.0.0.1:6167` |
| `com.parantoux.hermes-webui` | not touched | pid 913 |

No `hermes gateway --replace`. No valedia-profile LaunchAgent exists, so the `valedia` yaml change is on disk until that profile is next started.

CTO warm after restart materialized **47** tool schemas (previous process, with `tool_search: auto`, logged 20 at 14:09). Expected once search is off and the full tool list is built. Not reverted.

## `lms ps` (no unload)

Before and after both showed the same two residents, both IDLE:

| Identifier | Size | Context | Parallel |
|------------|------|---------|----------|
| `ornith-1.5-35b-a3b-mlx` | 19.53 GB | 65536 | 4 |
| `qwen/qwen3.6-35b-a3b` | 20.43 GB | 262144 | 4 |

TTL on Ornith ticked 27m → 25m of 30m on its own idle clock. No load, no unload, no model swap.

## Verify greps

```
tools.tool_search.enabled: root false, valedia false, valedia-cto false
moa.enabled: root true, valedia false, valedia-cto false
valedia plugins.disabled contains ponytail; plugins.enabled does not
cto delegation/cron provider custom:lms-local model ornith-1.5-35b-a3b-mlx base_url :1234
cto hooks.pre_tool_call[0] block-tcc-appdata.py fail_closed true
cto terminal.cwd /Users/burtonchatelain/.openclaw/workspace-v2
cto approvals.deny count 17
trading three: tool_search false, ctx 65536, delegation+cron lms-local mlx, fail_closed true
```

PyYAML `safe_load` succeeded on root, valedia, valedia-cto, and the three trading configs.

## DEFERRED to Assistant

1. **Root MoA off.** `moa.enabled` is still `true`. Reference model is still `qwen3.8-27b`, aggregator `ornith-1.5-35b-a3b`. Charter VETO: do not flip or edit `moa`.
2. **Model mlx id align.** Root `model.default` is still `ornith-1.5-35b-a3b` (not the loaded LMS id `ornith-1.5-35b-a3b-mlx`). Profile `valedia` and CTO desk defaults are already the mlx id. Charter VETO: no `model.default` retarget.
3. **Root/life reload.** Disk has `tool_search: false`. Running `ai.hermes.gateway` pid 66534 has not re-read it. Do not kickstart that agent if the goal is to keep Signal and Matrix sessions up.
4. **Matrix reconnect.** Tunnel pid 925 is up. No homeserver diagnosis and no gateway bounce in this pass.
5. **SOUL voice.** No SOUL rewrite.
6. **Profile `valedia` delegation/cron** still on Ollama `ornith-1.5:latest`. Out of the CTO scrub. MoA on that profile is already `false` and was not edited.

## Fence

No `--live`, no venue orders, no OKX, no VAL-12, no MemPalace-on-root, no root MCP enable, no LMS load/unload, no catalog rewrite, no TCC weaken, no life Matrix/Signal bounce, no trading yaml rewrite. Screens `ot-valedia-desk` and `valedia-glance` not touched.

## Tools

RuFlo `:8810`. `hooks_route` (semantic, memory-task 0.44; pre-task routed grok-build 0.85). `hooks_pre-task` `hermes-assistant-optimize-2026-09-30`. `memory_search` hit `patterns/hermes-tcc-appdata-fence-2026-09-30`. Pattern stored `patterns/hermes-assistant-optimize-2026-09-30`.
