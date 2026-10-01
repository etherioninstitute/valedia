---
name: mempalace-shared-brain
description: >
  Auto-prime when the user says continue Valedia, Valedia, shared brain, where we left
  off on Valedia, or names a tracked Valedia topic/decision — no skill name required.
  Cobrain-style: start_session when they name a tracked thing; load_context from local
  MemPalace wing shared-brain; write decisions as you go via append_decision. Palace SoT
  ~/.mempalace/palace only. Not Cobrain SaaS, Shared Memory Stack, or Hindsight hooks.
  Use across Hermes, Claude, Claude Code, Codex, and Grok Bot.
metadata:
  hermes:
    emoji: "🧠"
---

# mempalace-shared-brain

Local shared brain for Valedia. **Palace SoT:** `~/.mempalace/palace` via MemPalace MCP tools already enabled on KEEP profiles. Do **not** install Cobrain SaaS. Do **not** follow Shared Memory Stack paths under `/Volumes/2TB_APFS/`. Do **not** use Hindsight hooks as the shared-brain path.

## Just works (Cobrain UX mirror)

Official Cobrain achieves "human just talks" via **skill `description` implicit invocation** (Claude Code / Codex load the skill when the prompt matches) — not Account Instructions, not SaaS, not SessionStart hooks. We mirror that:

1. **Cue** — user says `continue Valedia` / `Valedia` / shared-brain / a tracked Valedia topic.
2. **Auto-prime** — load this skill and run `load_context` (search wing `shared-brain`) before answering.
3. **start_session** — open/refresh a sessions drawer when they name a tracked thing.
4. **Write as you go** — `append_decision` after real decisions (room `decisions`).

Burton must not need to type `/mempalace-shared-brain` or `$mempalace-shared-brain`.

## When to use
- Any Valedia continuation or multi-agent handoff
- Recording decisions others must load later
- Searching prior shared-brain context before acting

## Workflow

### 1. start_session
Call `mempalace_diary_write` or `mempalace_add_drawer`:
- wing: `shared-brain`
- room: `sessions`
- content: `# session <YYYY-MM-DD>-<slug>\nagent: <name>\ntopic: <topic>\nstatus: open\n`

Remember the session slug for later appends.

### 2. load_context
`mempalace_search` with the topic and/or session slug. Prefer hits in wing `shared-brain`. Summarize decisions + open questions before new work.

### 3. search
`mempalace_search "<query>"`. If empty in shared-brain, broaden (palace-wide). Do not switch palace roots.

### 4. append_decision
`mempalace_add_drawer` wing `shared-brain` room `decisions` with session slug + decision + rationale.
Also `mempalace_kg_add` when a durable entity/edge helps (session → decision → owner).

### 5. status
`mempalace_status` if drawer counts look wrong; never point `--palace` elsewhere.

## Anti-patterns
- Installing or following `shared-memory-stack` Obsidian/`2TB_APFS` paths
- Cobrain SaaS / `pip install cobrain` / `brn init` as a second store
- Dual-writing VAL Paperclip issues into Kanban as memory SoT
- Enabling or trusting Hindsight SessionStart / UserPromptSubmit / Stop for this wing
- Adding watcher daemons for "recovery"

## Tools (Hermes MCP include)
`mempalace_search`, `mempalace_status`, `mempalace_add_drawer`, `mempalace_kg_add`, `mempalace_diary_write` (+ existing read tools).

Write tools are on the include list for root (`~/.hermes/config.yaml`), valedia, and valedia-cto. Trading scout, decider, and auditor stay on the 12-tool read set. On those profiles, record the decision in the session you already have and let a write-capable profile append it. Do not open a second store.
