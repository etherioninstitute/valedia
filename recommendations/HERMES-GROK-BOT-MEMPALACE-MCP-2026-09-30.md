# Grok Bot MemPalace MCP / bridge — 2026-09-30

Charter: `recommendations/GROK-CHARTER-HERMES-GROK-BOT-MEMPALACE-MCP-2026-09-30.md`.
Verified **2026-09-30 22:54:10 PDT**. Mode: VERIFY+STAMP. Palace not rewritten. No tunnels.

## Recommended path
**B — `~/bin/mempalace-sb` + Shell machineId `b3f456da-e4fc-42d6-8147-a47ad76a8982`.**

Box Grok Bot cannot attach MemPalace as catalog MCP (stdio-only; palace is on the Mac; no box→Mac egress). Option A (HTTP/SSE) stays blocked. Option C (Hermes proxy) works on KEEP profiles and is heavier than B.

## Installed artifacts (this verify)
- `test -x ~/bin/mempalace-sb` → exit 0.
- `mempalace-sb status | head -c 400`: `total_drawers` 611653, `palace_path` `/Users/burtonchatelain/.mempalace/palace`. Wings truncated; not dumped.
- `mempalace-sb` present in:
  - `~/.agents/skills/mempalace-shared-brain/SKILL.md`
  - `~/.claude/skills/mempalace-shared-brain/SKILL.md`
  - `~/.hermes/skills/mempalace-shared-brain/SKILL.md`
  - `~/.codex/skills/mempalace-shared-brain/SKILL.md`
- `~/.cursor/mcp.json` key `mempalace`: command `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`, args `["-m", "mempalace.mcp_server"]` (local Cursor IDE only).

## Drawers (charter proof; not re-written)
- sessions: `drawer_shared-brain_sessions_3233f9843fe8cf79`
- decisions: `drawer_shared-brain_decisions_791d74134caee54d`

## Burton clicks
1. Cursor Desktop (Mac, optional): Settings → MCP → enable `mempalace` → restart if tools are missing. Local IDE only.
2. Box agent: no MCP click. Parent Shell `mempalace-sb` on machineId `b3f456da-e4fc-42d6-8147-a47ad76a8982`.
3. No Cobrain / Shared Memory Stack / Hindsight install.

## Fences held
No Cobrain SaaS, no Shared Memory Stack, no Hindsight hooks, no `--live`, no LMS unload, no trading bounce. Palace not rewritten. No tunnels installed.

GROK_EXIT:0
