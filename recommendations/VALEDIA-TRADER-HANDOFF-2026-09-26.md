# Valedia Trader handoff — 2026-09-26 ~15:00 PT

For a **new chat on the same Valedia Trader agent** (or a renamed sibling). Scrollback of the prior long thread is NOT visible; use this + durable memory + Paperclip.

## Fences (unchanged)
- Live risk = Valedia runtime only. No `--live` / venue orders / wallet from Grok Bot or Grok Build.
- Oil fenced. eToro intel-only (no start_copy).
- INFORMED weather: fee-true only; refuse lottery (≤~0.05) + late-collapse (≥~0.90); max-1 / thin bankroll.
- Jev = TypeSafe shadow only; never OMS / never alone-pass create (`VALEDIA-JEV-FENCED.md`).

## Paperclip (http://127.0.0.1:3100, data ~/.paperclip-valedia, Tailscale Serve to Studio)
- Company Valedia `4fb233c2-27e9-4d0d-a217-2dc7205210a0`
- INFORMED weather goal `3b85af61-87e4-4e9a-825d-1994546cb6a3`
  - **VAL-10** `c6778eec-b1fa-4f6a-9c2a-aec2f8620ee7` — WEATHER-TWO-SIDED-FEE-TRUE-1 (todo) — prefer two-sided books; refuse one-sided 1¢ lottery. Charter: `WEATHER-TWO-SIDED-FEE-TRUE-1.md`
- Ops self-heal goal `0e57493f-c5fb-4030-94c9-3936cd9e8ebb` (parentId null)
  - **VAL-11** `a404190c-6435-4134-9c1f-130c56113c22` — VALEDIA-SELF-HEAL-1 (todo, high) — health probes → safe remediate → else Paperclip ticket. Charter: `VALEDIA-SELF-HEAL-1.md`
- ACCELERATE still active (VAL-5..VAL-9); worktree `~/.openclaw/workspace-accel-ornith` branch `accel-ornith`
- Hermes profiles valedia-{cto,trading-scout,trading-auditor,trading-decider} → Ornith/Qwen local LMS; ports :8642–:8645
- Inbox: cleared 3× Hermes 1800s timeout agent errors (VAL-1/3/5) via clear-error 2026-09-26; recovery_needed was 0

## Infra fixes this session
- Neo4j back: Docker Desktop was down → `open -a Docker` then `cd ~/mirofish-offline && docker compose up -d neo4j`. Container `mirofish-neo4j` healthy; :7687 bolt OK.
- MiroFish: 3 sims completed 8/8 in wait mode (error null); Neo4j-down had broken report generate.
- Docker optimize guidance given: AutoStart ON, Dashboard on start OFF, Docker Terminal OFF, Model Runner OFF, Scout OFF optional, CPUs 8, add /Users/burtonchatelain file share, VirtioFS, Apple VZ (not VMM until after 4.92 update + stable Neo4j). Data on NIMROD_SSD.
- Grok Build exit ping **deleted** (token burn). WEATHER-FIRST-LIVE-ADMIT-1 already handled GROK_EXIT:0 @ watermark 453681; Next=Burton stop; no auto-chain.

## Token / chat hygiene
- Burton at ~71% Grok usage. Prefer Paperclip + local Ornith for coding/ops. Fresh chat OK; durable memory + this file carry state. Jev does NOT reduce Grok tokens.

## Do next
1. Confirm Docker Apply (AutoStart, CPUs, Users share) + Neo4j still :7687
2. Drive VAL-10 two-sided fee-true coding via CTO/ACCEL worktree (local only)
3. Implement VAL-11 probes (Neo4j/Docker/Hermes/Paperclip recovery) so breaks open tickets without human poke
4. Do not resurrect Grok Build exit ping unless Burton queues a Build and asks
