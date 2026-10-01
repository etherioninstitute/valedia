# Shared brain — one pager (2026-09-30 auto-prime)

**GROK_EXIT:0.** Palace `~/.mempalace/palace`. Wing `shared-brain`. Auto-prime stamp: `recommendations/HERMES-SHARED-BRAIN-AUTOPRIME-COBRAIN-STYLE-2026-09-30.md`. Prior wire: `HERMES-SHARED-BRAIN-WIRE-ALL-AIS-2026-09-30.md`.

You can do the clicks below without Grok Bot.

## Just works (Cobrain-style)

Say **"continue Valedia"** (or name Valedia / shared-brain / a tracked Valedia topic). The AI should auto-load wing `shared-brain` — you do **not** name the skill.

How Cobrain does it officially: skill `description` → implicit invocation when the prompt matches; human just talks. We mirror that with MemPalace (local), not Cobrain SaaS.

## What works now

| Surface | Auto-prime path |
|---|---|
| Hermes | SOUL session-start block (root + valedia + valedia-cto) + skill playbook |
| Claude Code | Skill description fires on Valedia cues; MCP `mempalace` in `~/.claude/mcp.json` |
| Claude Desktop | MCP already configured; **re-upload zip** so description updates |
| Codex | Skill description + `~/.codex/AGENTS.md` one-liner; hooks left alone |
| Grok Bot | Workflow skill description + memory fact on agent `3ac5a290-...` |

Smoke (do not re-run chroma): marker `MEMPALACE-SHARED-BRAIN-SMOKE-2026-09-30` on wing `shared-brain` (session drawer from wire pass).

## Clicks left

1. **Claude Desktop.** Customize → Skills → Upload/Create → `recommendations/packages/mempalace-shared-brain-skill.zip` (re-upload for auto-prime description). Restart. Leave Account Instructions alone.
2. **Claude Code / Codex.** Quit and reopen so skill description + AGENTS one-liner load.
3. **Hermes.** New chat / gateway reload so SOUL auto-prime is live (files already on disk).

## Anti-patterns

- Cobrain SaaS, `pip install cobrain`, or `brn init`
- Shared Memory Stack / `/Volumes/2TB_APFS/`
- Hindsight SessionStart / UserPromptSubmit / Stop as the shared brain
- Second palace path; `--live`; LMS unload; trading bounce

**GROK_EXIT:0**
