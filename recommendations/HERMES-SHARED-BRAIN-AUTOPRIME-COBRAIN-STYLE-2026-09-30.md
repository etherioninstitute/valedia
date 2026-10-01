# Shared-brain auto-prime — Cobrain-style — 2026-09-30

**GROK_EXIT:0**

Charter: `recommendations/GROK-CHARTER-SHARED-BRAIN-AUTOPRIME-COBRAIN-STYLE-2026-09-30.md`.
Executed **2026-09-30 22:32:24 PDT**. Prior wire stamp left intact: `HERMES-SHARED-BRAIN-WIRE-ALL-AIS-2026-09-30.md`.
One-pager refreshed: `HOWTO-SHARED-BRAIN.md`.

**SoT:** `~/.mempalace/palace` via MemPalace MCP. Wing `shared-brain`.
**Not** Cobrain SaaS. **Not** Shared Memory Stack. **Not** Hindsight hooks.

## How Cobrain achieves "just works"

Official Cobrain (igoakulov/cobrain vault CLI / `cobrain-vault` skill):

1. Install the skill (`npx skills add …`); **human just talks** — agent manages vault autonomously.
2. Claude Code / Codex discover skills by `name` + **`description`**; full `SKILL.md` loads on **implicit invocation** when the user prompt matches the description (not Account Instructions; not a paid SaaS note store).
3. Session order inside the skill: read skill → load context → work → write decisions / AGENTS.md as you go.
4. Rejected here: Cobrain SaaS note ceiling / $9 unlimited; `pip install cobrain` / `brn init` as a second store beside the 611k-drawer palace.

## What we mirrored

| Surface | Cobrain UX equivalent | This pass |
|---|---|---|
| Hermes | Always-on identity / session-start (SOUL) | Auto-prime block in SOUL: on `continue Valedia` / Valedia cues → `mempalace_search` wing `shared-brain` → start_session → append_decision |
| Claude Code / Desktop | Skill `description` implicit invoke | Rewrote description with Valedia cues; re-zipped for Desktop upload |
| Codex | Skill description (+ optional AGENTS.md) | Same skill + `~/.codex/AGENTS.md` one-liner; **hooks.json untouched** |
| Grok Bot | Skill description / memory | Box workflow skill + agent `3ac5a290-…` memory fact |

## Files changed

- `~/.hermes/SOUL.md` (+ `.bak-autoprim-*`)
- `~/.hermes/profiles/valedia/SOUL.md`
- `~/.hermes/profiles/valedia-cto/SOUL.md`
- `recommendations/skills/mempalace-shared-brain/SKILL.md` and copies:
  - `~/.hermes/skills/mempalace-shared-brain/SKILL.md`
  - `~/.claude/skills/mempalace-shared-brain/SKILL.md`
  - `~/.codex/skills/mempalace-shared-brain/SKILL.md`
  - `~/.agents/skills/mempalace-shared-brain/SKILL.md`
  - `recommendations/packages/mempalace-shared-brain-skill.zip`
- `~/.codex/AGENTS.md` (was empty → auto-prime one-liner)
- Box: `/home/box/agent-data/workflows/mempalace-shared-brain/SKILL.md` (+ `-2` copy)
- Box memory: `/home/box/agent-data/agents/3ac5a290-81c9-4718-9c71-64fc308aaa63/memory/profile.md`
- Docs: this stamp, charter, `HOWTO-SHARED-BRAIN.md`

Skill sha256: `9d1df9d6121a9cc8fa7b3f8c38c63fb65d1a8781e6b3b0566c0a48902df2c6ce`

## Proof

### Wing search (no Desktop)
`mempalace.searcher.search("MEMPALACE-SHARED-BRAIN-SMOKE-2026-09-30", palace_path=~/.mempalace/palace, wing=shared-brain)` → hit session `2026-09-30-wire-all-ais-smoke` with marker present (room `sessions`). SEARCH_OK.

### Hermes SOUL session-start (feasible without flaky Desktop / hung ornith oneshot)
- Auto-prime section present in root + valedia + valedia-cto SOUL.md (grep: `Shared-brain auto-prime`, `continue Valedia`, `mempalace_search` + wing `shared-brain`).
- Hermes loads `HERMES_HOME/SOUL.md` as identity slot #1 (`agent/prompt_builder.py` `load_soul_md` / `context_file_sources.py`). For `-p valedia`, that is `~/.hermes/profiles/valedia/SOUL.md`.
- Valedia MCP `mempalace` remains `enabled: true` (`config.yaml:432`).
- Live `hermes -p valedia chat --oneshot` against model `ornith-1.5-35b-a3b-mlx` **stalled after init** (only system init JSONL; no tool events in >4m despite `--run-budget 150`). Process killed. Not used as failure of auto-prime wiring — model/runtime stall. Burton: open a fresh Hermes Valedia chat and say `continue Valedia` once ornith is warm.

### Hooks fence
`~/.codex/hooks.json` sha256 `72af4e0be33bed08b09e0249e83404798bb4c66209ef3e166f0589cdb4352278`, mtime **2026-09-28 14:22:37 PDT** (unchanged since 2026-09-28). Zero edits this pass.

## Fences held

- No `--live`, no LMS unload, no trading bounce
- No Cobrain SaaS / `pip install cobrain` / `brn init`
- No Shared Memory Stack / `/Volumes/2TB_APFS/`
- No Hindsight hook enable / no `hooks.json` edit
- No Paperclip dual-write; no second palace root

## Burton clicks left

1. **Claude Desktop** — Customize → Skills → Upload/Create → `~/.openclaw/workspace-v2/recommendations/packages/mempalace-shared-brain-skill.zip` (re-upload for new description). Restart. Leave Account Instructions alone.
2. **Claude Code / Codex** — quit + reopen so skill description + Codex AGENTS one-liner load.
3. **Hermes** — new Valedia chat (or gateway reload) so SOUL auto-prime is in context; when ornith is responsive, say `continue Valedia` and confirm a `mempalace_search` on wing `shared-brain`.

**GROK_EXIT:0**

## VERIFY PASS — 2026-09-30 22:38:55 PDT

Read-only recheck. No `add_drawer`, no chroma client, no hooks edit, no `--live`, no LMS unload, no trading bounce.

1. **SOUL auto-prime** — present in `~/.hermes/SOUL.md`, `profiles/valedia/SOUL.md`, and `profiles/valedia-cto/SOUL.md` (`Shared-brain auto-prime`, `continue Valedia`, `mempalace_search`, wing `shared-brain`).
2. **Skill description** — `continue Valedia` in the description of all five copies; sha256 still `9d1df9d6121a9cc8fa7b3f8c38c63fb65d1a8781e6b3b0566c0a48902df2c6ce`.
3. **Codex AGENTS** — `~/.codex/AGENTS.md` is the auto-prime one-liner (cue → skill `mempalace-shared-brain`, wing `shared-brain`).
4. **hooks.json** — sha256 `72af4e0be33bed08b09e0249e83404798bb4c66209ef3e166f0589cdb4352278`, mtime `2026-09-28 14:22:37 PDT`. Unchanged. Pre-existing Hindsight SessionStart / UserPromptSubmit / Stop commands remain in that frozen file; this pass did not enable or edit them.
5. **Marker** — literal hit in `~/.mempalace/palace/chroma.sqlite3` (2.29s mmap, no embed). Document is session `2026-09-30-wire-all-ais-smoke`, marker `MEMPALACE-SHARED-BRAIN-SMOKE-2026-09-30`.

**GROK_EXIT:0**
