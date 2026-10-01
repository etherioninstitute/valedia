# Shared brain wire — all AIs — 2026-09-30

**GROK_EXIT:0**

Charter `recommendations/GROK-CHARTER-SHARED-BRAIN-WIRE-ALL-AIS-2026-09-30.md`. This pass is **VERIFY + STAMP** only. Executor wired the files at **2026-09-30 21:55:35 PDT**. Verify ran **22:00 PDT**. No MemPalace smoke (chroma left alone). No Hindsight hook edit. No Cobrain. No Shared Memory Stack. No `--live`. No LMS unload. No trading bounce. No Paperclip dual-write.

**SoT:** `~/.mempalace/palace` via MemPalace MCP. Wing `shared-brain`.

Skill bytes are one file everywhere they were copied. sha256 prefix `e023921e649554df`, length **2450**:

- `~/.hermes/skills/mempalace-shared-brain/SKILL.md`
- `~/.claude/skills/mempalace-shared-brain/SKILL.md`
- `~/.codex/skills/mempalace-shared-brain/SKILL.md`
- `~/.agents/skills/mempalace-shared-brain/SKILL.md`
- zip member `mempalace-shared-brain/SKILL.md`

## Verify (this pass)

| # | Check | Result |
|---|---|---|
| 1 | Hermes skill on disk + `hermes skills list` | **PASS.** File exists. Wide list: `mempalace-shared-brain` source `local`, trust `local`, status `enabled`. |
| 2 | `enabled: true` on KEEP profiles | **PASS.** root, valedia, valedia-cto, scout, decider, auditor. awk only. No rewrite. |
| 3 | Codex skill + `config.toml` | **PASS.** File exists. `config.toml:158` path, `:159` `enabled = true`. |
| 4 | Claude Code skill + zip | **PASS.** Both files exist. Zip is 1415 bytes, one member, mtime 21:55. |
| 5 | `~/.claude/mcp.json` `mcpServers.mempalace` | **PASS.** Command, args, and empty env match the charter. `tradingview` still present. Keys: `mempalace`, `tradingview`. |
| 6 | `~/.codex/hooks.json` untouched this pass | **PASS.** mtime **2026-09-28 14:22:37 PDT** (before 21:55 wiring). size 807. sha256 `72af4e0be33bed08b09e0249e83404798bb4c66209ef3e166f0589cdb4352278`. Zero `mempalace` strings. This pass did not write the file. |
| 7 | Smoke | **CITED, NOT RE-RUN.** See below. |

### 1 — Hermes skill

```
test -f ~/.hermes/skills/mempalace-shared-brain/SKILL.md   # yes
COLUMNS=240 hermes skills list | grep -F mempalace-shared-brain
```

```
│ mempalace-shared-brain            │                      │ local    │ local     │ enabled                                                                   │
```

A default-width table ellipsizes the name to `mempalace-share…`, so a narrow `grep mempalace-shared-brain` misses. The wide row is the same skill. Hermes Agent v0.21.5+4972.g7239625.

### 2 — KEEP `enabled: true`

| Profile | File | `enabled` | include tools | write tools |
|---|---|---|---|---|
| root | `~/.hermes/config.yaml:475` | `true` | 15 | 3 (`add_drawer`, `kg_add`, `diary_write`) |
| valedia | `~/.hermes/profiles/valedia/config.yaml:432` | `true` | 15 | 3 |
| valedia-cto | `~/.hermes/profiles/valedia-cto/config.yaml:334` | `true` | 15 | 3 |
| scout | `~/.hermes/profiles/valedia-trading-scout/config.yaml:254` | `true` | 12 | 0 |
| decider | `~/.hermes/profiles/valedia-trading-decider/config.yaml:272` | `true` | 12 | 0 |
| auditor | `~/.hermes/profiles/valedia-trading-auditor/config.yaml:274` | `true` | 12 | 0 |

Command is `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3` with args `-m mempalace.mcp_server` on all six. Scout, decider, and auditor stay on the read set. A write-capable profile appends their decisions.

### 3 — Codex

`~/.codex/config.toml` lines 157–159 (file mtime 21:55:35, same minute as the skill copy):

```toml
[[skills.config]]
path = "/Users/burtonchatelain/.codex/skills/mempalace-shared-brain"
enabled = true
```

`sol-foreman` and `astra-foreman` `[[skills.config]]` blocks are still above it (lines 121 and 125).

`[features] hooks = true` (line 150) and `[mcp_servers.hindsight]` (line 152) are in the same file. This verify pass did not edit `config.toml`. Shared brain does not use that Hindsight server.

### 4 — Claude skill + zip

- `~/.claude/skills/mempalace-shared-brain/SKILL.md` mtime 21:55:35
- `~/.openclaw/workspace-v2/recommendations/packages/mempalace-shared-brain-skill.zip`
- Zip listing: `mempalace-shared-brain/SKILL.md` only

Claude Desktop MCP already lists `mempalace` in `~/Library/Application Support/Claude/claude_desktop_config.json` (file mtime 19:10, keys `gitlab`, `graft`, `laya-decide`, `mempalace`). Desktop **skill upload** is still a Burton click. `~/.claude.json` has no `mempalace` string (76029 bytes). The charter target was `~/.claude/mcp.json`, which is wired. `claude mcp list` was not finished: it connects servers and would pull chroma.

### 5 — mcp.json object

```
{'command': '/Library/Frameworks/Python.framework/Versions/3.13/bin/python3', 'args': ['-m', 'mempalace.mcp_server'], 'env': {}}
```

### 6 — hooks.json left alone

Pre-existing events, unchanged since 2026-09-28:

- `SessionStart` → `~/.hindsight/coding-agents/dist/codex-sessionstart-hook.js`
- `UserPromptSubmit` → `~/.hindsight/coding-agents/dist/codex-hook.js`
- `Stop` → `~/.hindsight/coding-agents/dist/codex-stop-hook.js`

Those commands are the wrong tool for this wing. This pass did not add, remove, or enable them.

## Smoke (executor receipt — not re-run)

Cited from the charter. This pass did not call `tool_add_drawer`, `tool_search`, or `tool_status`.

- `tool_add_drawer(wing=shared-brain, room=sessions)` → `drawer_shared-brain_sessions_6d403b95d9bbbe89` success
- `tool_search(query=MEMPALACE-SHARED-BRAIN-SMOKE-2026-09-30, wing=shared-brain)` → hit on that session drawer (marker present)
- `tool_status` → `total_drawers: 611651`, `palace_path: /Users/burtonchatelain/.mempalace/palace`

## Fences held

- No `--live`
- No LMS unload
- No trading bounce
- No Cobrain SaaS (`pip install cobrain`, `brn init`)
- No Shared Memory Stack and no `/Volumes/2TB_APFS/` path
- No edit to `~/.codex/hooks.json`, no Hindsight install
- No Paperclip dual-write
- No second palace root
- No profile rewrite (every KEEP profile already had `enabled: true`)

## Burton-only clicks

1. **Claude Desktop:** Customize → Skills → Upload/Create → select the zip below. Leave Account Instructions empty of the skill body.
2. **Claude Code:** quit and reopen so `~/.claude/mcp.json` and the skill folder load.
3. **Codex:** quit and reopen so `[[skills.config]]` loads. Leave hooks as they are.
4. **Grok Bot parent:** confirm the workflow skill description is visible (path below). This Mac has no SSH host for `/home/box`.

## Per-AI install (copy-paste)

On a new session that needs shared context, follow the skill: `mempalace_search` / start_session → load_context → search → append_decision. Wing `shared-brain`. Rooms `sessions` and `decisions`. Palace stays `~/.mempalace/palace`.

### Hermes — already done

```bash
test -f ~/.hermes/skills/mempalace-shared-brain/SKILL.md && echo SKILL_OK
COLUMNS=240 hermes skills list | grep -F mempalace-shared-brain
awk '
  FNR==1 { print "FILE " FILENAME }
  /mempalace:/ { hit=1; n=0 }
  hit { if ($0 ~ /enabled:/) print FILENAME ":" FNR ":" $0; n++; if (n>=12) hit=0 }
' ~/.hermes/config.yaml \
  ~/.hermes/profiles/valedia/config.yaml \
  ~/.hermes/profiles/valedia-cto/config.yaml \
  ~/.hermes/profiles/valedia-trading-scout/config.yaml \
  ~/.hermes/profiles/valedia-trading-decider/config.yaml \
  ~/.hermes/profiles/valedia-trading-auditor/config.yaml
```

Expected: `SKILL_OK`, a row ending in `enabled`, and six `enabled: true` lines. Open a new Hermes session after that. Scout / decider / auditor search and read; root, valedia, or valedia-cto append.

### Claude Desktop — Burton uploads the zip

Zip to select in Customize → Skills → Upload/Create:

```
/Users/burtonchatelain/.openclaw/workspace-v2/recommendations/packages/mempalace-shared-brain-skill.zip
```

Desktop MCP `mempalace` is already in `claude_desktop_config.json`. Restart the Claude app after the upload. Do not paste the skill into Account Instructions.

### Claude Code — files done, restart

```bash
test -f ~/.claude/skills/mempalace-shared-brain/SKILL.md && echo CLAUDE_SKILL_OK
python3 -c "import json,os; print(json.load(open(os.path.expanduser('~/.claude/mcp.json')))['mcpServers']['mempalace'])"
```

Already applied in `~/.claude/mcp.json` (merged; `tradingview` kept):

```json
"mempalace": {
  "command": "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
  "args": ["-m", "mempalace.mcp_server"],
  "env": {}
}
```

Quit Claude Code and open it again. Do not paste that block a second time.

### Codex — skills.config done, restart, hooks stay off

```bash
test -f ~/.codex/skills/mempalace-shared-brain/SKILL.md && echo CODEX_SKILL_OK
grep -n mempalace-shared-brain ~/.codex/config.toml
shasum -a 256 ~/.codex/hooks.json
stat -f "%Sm" -t "%Y-%m-%d %H:%M:%S" ~/.codex/hooks.json
```

Already applied:

```toml
[[skills.config]]
path = "/Users/burtonchatelain/.codex/skills/mempalace-shared-brain"
enabled = true
```

Quit Codex and open it again. Do not edit `~/.codex/hooks.json`. Do not run a Hindsight install. The optional agents-tree copy is already at `~/.agents/skills/mempalace-shared-brain/SKILL.md` (same sha256).

### Grok Bot — box workflow already written; parent confirms

Documented paths (this Mac did not SSH and did not edit the box):

- Skill: `/home/box/agent-data/workflows/mempalace-shared-brain/SKILL.md`
- Agent memory fact: `3ac5a290-81c9-4718-9c71-64fc308aaa63`
- Fact target: palace `~/.mempalace/palace`, wing `shared-brain`

`~/.ssh/config` hosts on this Mac are `openclaw` and `etherion` only. No host for user `box`. Parent confirms the skill description is visible:

> Cobrain-like shared session workflow backed by live MemPalace MCP (not Cobrain SaaS). Use start_session / load_context / search / append_decision so Hermes, Claude, Claude Code, Codex, and Grok Bot share one MemPalace wing (shared-brain). Never retarget the palace path.

## Residuals (not a failed Mac verify)

- Grok Bot skill visibility is a parent confirm. Unreachable from this Mac, so no box edit was invented.
- `~/.claude.json` still has no `mempalace` key. Load path for this charter is `~/.claude/mcp.json`.
- Hindsight hook commands remain on disk from 2026-09-28. They are not the shared brain.

One-pager: `recommendations/HOWTO-SHARED-BRAIN.md`.

**GROK_EXIT:0**
