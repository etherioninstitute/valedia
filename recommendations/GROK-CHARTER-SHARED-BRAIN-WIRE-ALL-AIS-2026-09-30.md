# GROK CHARTER — Wire MemPalace shared-brain to all AIs (2026-09-30 ~21:55 PT)

**Mode:** EXECUTE + STAMP. Paper/ops. One tight pass. Stamp `HERMES-SHARED-BRAIN-WIRE-ALL-AIS-2026-09-30.md` + one-pager `HOWTO-SHARED-BRAIN.md` with `GROK_EXIT`.
**Authority:** Valedia Trader executor. Launch via `~/bin/grok-valedia` / screen `valedia-grok`.
**SoT:** Live MemPalace at `~/.mempalace/palace` via MCP. Wing `shared-brain`. **Not** Cobrain SaaS. **Not** Shared Memory Stack. **Not** Hindsight coding-agent hooks.

## Fences (hard)
- No `--live`, no LMS unload, no trading bounce, no Cobrain SaaS, no Shared Memory Stack install, **no enabling Hindsight hooks** (SessionStart/UserPromptSubmit/Stop under `~/.hindsight/...` — leave OFF / do not trust for shared brain), no Paperclip dual-write.

## Executor already proved (do not re-break; cite in stamp)
- `tool_add_drawer(wing=shared-brain, room=sessions)` → `drawer_shared-brain_sessions_6d403b95d9bbbe89` success.
- `tool_search(query=MEMPALACE-SHARED-BRAIN-SMOKE-2026-09-30, wing=shared-brain)` → hit session drawer (marker present).
- `tool_status` → `total_drawers: 611651`, `palace_path: /Users/burtonchatelain/.mempalace/palace`.
- Hermes skill `~/.hermes/skills/mempalace-shared-brain/SKILL.md` — `hermes skills list` → local / enabled.
- MCP `mempalace` **enabled: true** on KEEP: root, valedia, valedia-cto, valedia-trading-scout, valedia-trading-decider, valedia-trading-auditor.
- Claude Desktop already has mempalace MCP in `~/Library/Application Support/Claude/claude_desktop_config.json`.
- Claude Code `~/.claude.json` / `~/.claude/mcp.json` did **not** yet list mempalace (wire it).
- Codex has `[[skills.config]]` pattern (sol-foreman / astra-foreman). Hindsight hooks exist on disk but are **wrong tool** — do **not** enable/trust for shared brain.

## Execute

### (1) HERMES — prove only
- Confirm skill enabled + MCP enabled on KEEP profiles. New-session note: agents should call `mempalace_search` / skill workflow on start when shared context matters. No profile rewrite unless a KEEP profile is missing `enabled: true` (they are not).

### (2) CLAUDE / Claude Code — Skill + MCP (not Account Instructions)
- Package zip: `$REC/packages/mempalace-shared-brain-skill.zip` containing folder `mempalace-shared-brain/SKILL.md` (same text as Hermes skill).
- Also copy skill folder to `~/.claude/skills/mempalace-shared-brain/` for Claude Code local skills.
- Add MemPalace to Claude Code local MCP `~/.claude/mcp.json` (merge, do not wipe tradingview):
  ```json
  "mempalace": {
    "command": "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
    "args": ["-m", "mempalace.mcp_server"],
    "env": {}
  }
  ```
- **Burton click (Claude Desktop app):** Customize → Skills → Upload/Create → upload the zip (or select folder). Optional one-line pointer in Instructions only if Skill upload unavailable — prefer Skill.
- Do **not** put the full skill body in Account Instructions.

### (3) CODEX — Plugin/skills path; Hindsight hooks stay OFF
- Copy skill to `~/.codex/skills/mempalace-shared-brain/SKILL.md`.
- Append to `~/.codex/config.toml` if missing:
  ```toml
  [[skills.config]]
  path = "/Users/burtonchatelain/.codex/skills/mempalace-shared-brain"
  enabled = true
  ```
- Optional: also symlink/copy under `~/.agents/skills/mempalace-shared-brain` if that tree is scanned.
- **Do not** edit `~/.codex/hooks.json` to add/enable Hindsight SessionStart/UserPromptSubmit/Stop. Do not run hindsight install. Shared brain = MemPalace skill + (optional later) mempalace MCP — not Hindsight.

### (4) GROK BOT (Cursor Valedia Trader on box) — document parent steps
- Parent/executor should already place SKILL under `/home/box/agent-data/workflows/mempalace-shared-brain/SKILL.md` and a memory fact on agent `3ac5a290-81c9-4718-9c71-64fc308aaa63` pointing at palace `~/.mempalace/palace` + wing `shared-brain`. Cite paths in stamp; do not invent box edits from Mac if unreachable.

### (5) Stamps
Write:
1. `$REC/HERMES-SHARED-BRAIN-WIRE-ALL-AIS-2026-09-30.md` — GROK_EXIT, smoke proof, per-AI install steps (copy-paste), Burton-only clicks, fences held.
2. `$REC/HOWTO-SHARED-BRAIN.md` — **one-pager** Burton can follow without Grok Bot (~95% tokens). Topsheet: what works now, clicks left, anti-patterns.

End with `GROK_EXIT:0` (or non-zero + blockers).
