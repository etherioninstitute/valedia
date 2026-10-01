# Grok Build Lane

## EXECUTOR CHARTER (Burton 2026-09-14 21:48 PT)

**Grok Build is the executor** for Valedia implementation.

- Valedia (Grok Bot chat) = architect / audit / relay.
- **Grok Build** = codes, tests, merges toward a perfectly operating Valedia.
- Worktree: `~/.openclaw/workspace-v2`
- North star: maximum profit (risk-gated). See `VALEDIA-SUBSUME-NAUTILUS.md`.
- Live risk: Valedia runtime only. Grok Build does not place live orders or change spend keys.

## RuFlo REQUIRED (2026-09-14 21:56 PT)

Grok Build **must use RuFlo MCP** on every coding session:
- URL: `http://127.0.0.1:8810/mcp` (LaunchAgent `com.burton.ruflo-mcp-http`, filtered ADR-035 tools)
- Config: project `.grok/config.toml` + user `~/.grok/config.toml` → `[mcp_servers.ruflo]`
- Prefer RuFlo memory/agent/hooks tools over ad-hoc sprawl; do not dump full ~357 tool catalog.
- If MCP is down: restart `com.burton.ruflo-mcp-http` / `~/bin/ruflo-mcp-http.sh` before coding.

## Progress relay

Write `recommendations/GROK-STATUS.md` at start/end of each slice (goal, done, blockers, next). Valedia relays that to Burton.

**2026-09-14 22:00 PT:** Unfiltered `claude-flow` (stdio `npx ruflo mcp start`, ~353 tools) disabled for Grok via `disabled_mcp_servers`. Use only `[mcp_servers.ruflo]` → `:8810`. codebase-memory + comfyui may still load from Claude compat — OK.

## Live readout (Burton 2026-09-14 22:10 PT)

**Chosen method:** streaming log + optional screen session.

- You: open Terminal → `watch-grok` (tails `recommendations/GROK-LIVE.log`)
- Valedia launches slices with: `grok-valedia "TASK"` (tees into that log; screen session `valedia-grok`)
- Optional full attach: `screen -r valedia-grok`


## Codebase Memory (Burton 2026-09-18)

Grok Build **must** use MCP `codebase-memory` (stdio → `~/.openclaw/tools/cbm/codebase-memory-mcp`) for structural navigation of Valedia + cloned finance repos — prefer `search_graph` / `trace_path` / `get_architecture` / `index_repository` over blind grep.

**Hooks:** Grok Build **does not** get useful CBM context hooks (stdout discarded on SessionStart/etc.). Auto-use = **MCP tools + agent discipline** (+ optional `auto_index true`). Not Claude-style PreToolUse injection.

Prefer Repomix only for rare whole-repo packs.
