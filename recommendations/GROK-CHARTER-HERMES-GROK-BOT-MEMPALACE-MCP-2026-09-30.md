# GROK CHARTER — Grok Bot MemPalace MCP / bridge (2026-09-30)

**Mode:** VERIFY + STAMP. Stamp `HERMES-GROK-BOT-MEMPALACE-MCP-2026-09-30.md` with `GROK_EXIT`.
**Authority:** Valedia Trader / grok-valedia. Paper/ops only.
**SoT:** `~/.mempalace/palace` wing `shared-brain`. **Not** Cobrain SaaS. **Not** Shared Memory Stack. **Not** Hindsight.

## Fences (hard)
No Cobrain SaaS, no Shared Memory Stack, no Hindsight hooks, no `--live`, no LMS unload, no trading bounce.

## Investigation (executor already proved — cite, do not re-break)

### Can Cursor/Grok Bot install MemPalace as MCP connector?
**For the Linux box agent (Valedia Trader): NO durable stdio MCP.** MemPalace `mcp_server.py` is **stdio-only**. Palace (~3GB chroma) is on Mac `~/.mempalace/palace`. Box cannot run `python -m mempalace.mcp_server` against that path. Box egress probe: no `host.docker.internal`, no reach to Mac :8810. Cursor cloud MCP catalog has no MemPalace entry.

**For Cursor Desktop on Mac (local IDE agents): YES** — same stdio install Hermes/Claude Code use:
```json
"mempalace": {
  "command": "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
  "args": ["-m", "mempalace.mcp_server"],
  "env": {}
}
```
File: `~/.cursor/mcp.json`. **Burton click:** Cursor Settings → MCP → confirm `mempalace` enabled → restart Cursor if needed. This does **not** give the box agent MCP tools.

### Options
| Opt | Idea | Verdict |
|---|---|---|
| A | MCP HTTP/SSE on Mac exposed to box | **Blocked** — no wrapper installed (`mcp-proxy`/`supergateway` absent), no box→Mac tunnel |
| **B** | Thin CLI on Mac + Shell machineId | **RECOMMENDED / INSTALLED** `~/bin/mempalace-sb` |
| C | Hermes as proxy | Works (KEEP profiles already have mempalace MCP) but heavier than B |

### Proof
- `mempalace-sb search MEMPALACE-SHARED-BRAIN-SMOKE --wing shared-brain` → hit
- `mempalace-sb add` sessions → `drawer_shared-brain_sessions_3233f9843fe8cf79`
- decisions handoff → `drawer_shared-brain_decisions_791d74134caee54d`
- search marker `HERMES-GROK-BOT-MEMPALACE-MCP-2026-09-30` → hit

## Execute (Grok Build)
1. Confirm `~/bin/mempalace-sb` executable; one-line `status` JSON has `palace_path`.
2. Confirm skill files mention `mempalace-sb` (agents/claude/hermes/codex).
3. Write stamp `$REC/HERMES-GROK-BOT-MEMPALACE-MCP-2026-09-30.md` with recommended path B, installed artifacts, Burton clicks, fences, GROK_EXIT.
4. Do **not** install Cobrain, Shared Memory Stack, Hindsight, HTTP tunnels, or trading/LMS changes.

End `GROK_EXIT:0` or non-zero + blockers.
