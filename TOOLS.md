# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.

## Agent inventory (always check)

Canonical list of repos, MCPs, CLIs, and model lanes so you don’t ask Burton what he already has:

→ `~/MemPalace/sources/shared-brain/AGENT-INVENTORY.md`

Also mirrored: `recommendations/AGENT-INVENTORY.md`

**Prefer:** codebase-memory-mcp for structural code Qs; Repomix for whole-repo packs; RuFlo MCP `:8810` (filtered) for orchestration.
