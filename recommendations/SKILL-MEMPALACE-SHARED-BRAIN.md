# Proposal — MemPalace Shared Brain skill (Cobrain-like UX, local, no SaaS)

**Status:** PROPOSAL adopted 2026-09-30 PT as a docs-only Hermes skill. Cobrain SaaS rejected (1000-note free / $9 unlimited). Cobrain vault CLI = UX reference only.
**SoT:** Live MemPalace MCP + palace at `~/.mempalace/palace` (~611k drawers). Do not install Shared Memory Stack (fights this palace; forces Obsidian hub).
**Installed:** `~/.hermes/skills/mempalace-shared-brain/SKILL.md` (docs/workflow skill; no new MCP server, no hub lock row, no palace retarget).

## Goal
One shared page/workflow so Claude, Claude Code, Codex, Grok Bot, and Hermes start a session, load context, search, and append decisions against the **same** MemPalace wing — not five siloed memories.

## Cobrain UX → MemPalace mapping

| Cobrain-style step | MemPalace action (already live MCP) | Notes |
|---|---|---|
| `start_session(topic)` | `mempalace_diary_write` or `mempalace_add_drawer` into wing `shared-brain`, room `sessions`, body = session header + agent id + topic | Return session_id (date+slug) |
| `load_context(topic\|session_id)` | `mempalace_search` query + optional kg lookup | Prefer wing filter `shared-brain` then broaden |
| `search(query)` | `mempalace_search` | Same tool Hermes SOUL already cites |
| `append_decision(session_id, decision, rationale)` | `mempalace_add_drawer` room `decisions` + `mempalace_kg_add` edge session→decision | No note ceiling |
| `append_note(...)` | `mempalace_add_drawer` room `notes` | |
| health / status | `mempalace_status` | |

## Conventions
- **Wing:** `shared-brain` (already has drawers from prior handoffs).
- **Rooms:** `sessions`, `decisions`, `notes`, `context`, `general`.
- **Agents tag:** frontmatter-ish first line `agent: hermes|claude-code|codex|grok|claude` so search can filter.
- **No Obsidian required.** Optional later: mine a vault *into* this palace; never retarget `--palace`.
- **No Cobrain pip / no brn init / no SaaS account.**

## Why not Shared Memory Stack
Hardcoded `/Volumes/2TB_APFS/.../memory/palace` (absent here), Obsidian-as-hub, OpenClaw:18789 bridge, no Hermes/Paperclip. Installing it would teach agents the wrong paths beside live MCP.

## Why not Cobrain SaaS / cobrain-vault
Paid ceiling; second store parallel to 611k-drawer palace; dual-write risk.

## Safe install (done this pass)
Copied `recommendations/skills/mempalace-shared-brain/SKILL.md` → `~/.hermes/skills/mempalace-shared-brain/`. Hub scan not required for this flat local skill. No MCP added. Holographic / Built-in providers left as they were.

## Burton-only later
- Confirm agents other than Hermes (Claude Code / Codex) load the same SKILL.md or equivalent MCP mempalace config pointing at `~/.mempalace`.
- Optional: promote wing `shared-brain` hygiene cron via existing MemPalace tools only (no new daemons).
