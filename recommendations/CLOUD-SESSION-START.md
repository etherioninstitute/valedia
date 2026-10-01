# Cloud session start (Claude Code on the web)

You are in a cloud sandbox with a copy of this private repo. Nothing on Burton's Mac is reachable from here:
no Ollama, MiroFish/Neo4j, MemPalace, codebase-memory, Signal, crons, `.env`, or exchange keys.
So this session codes, tests, and audits. It never trades.

## Read first (in order)
1. `recommendations/RUFLO-STATUS.md` — newest entries at top; what was done and what is next.
2. `recommendations/CLAUDE-TO-RUFLO-HANDOFF.md` — sections A–E: state, backlog, fence.
3. `recommendations/RUFLO-MASTER-GOAL.md` — first milestone.
4. `VALEDIA-FULL-SETUP-MAP.md` — where everything lives.

## North star
Maximum profit via a better Valedia: ledger truth, gates, edge, execution quality.

## Fence (hard)
- No `--live`, no venue orders, no wallet or spend actions, no venue API key changes.
- Never add secrets or `.env` files to the repo. Never put live keys into cloud env vars.
- No `kalshi-agent` (UNLICENSED) code copied into this repo.
- Live Kalshi authority is `scripts/integrations/master_allocator.py` (cron `allocator_run`), not `kalshi_executor.run`.
- Track record = the real settlement ledger (`forward_test_ledger.jsonl` via `forward_test_ledger.real_track_record()`), never `brain_log.json`.

## Shared memory (MemPalace)
The palace lives at `~/.mempalace/palace` on Burton's Mac and is NOT reachable here.
Context from it for this repo: session `2026-10-01-valedia-github-cloud` in wing `shared-brain`
(rooms `sessions`, `decisions`). This file and the docs above carry what you need.
Record decisions in `RUFLO-STATUS.md`; a local agent copies them into MemPalace.

## Orchestration
`/opus-foreman` is available (`.claude/skills/opus-foreman` + `.claude/agents/opus-foreman-*`).
In the cloud only Claude seats exist: Codex/Grok CLIs, `~/.foreman` consent flags, and Jev are absent, so route to Claude workers.

## Working rules
- Work on a branch, open a PR against `valedia-v2-grok-build`. Burton merges.
- Tests: plain `python3 scripts/integrations/test_*.py` (assert-based, no network).
- Code that needs a live API: write it with a dry-run/offline path and leave the live call for Burton's Mac.
