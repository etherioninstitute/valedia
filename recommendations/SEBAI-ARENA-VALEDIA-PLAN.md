# SebAI Economic Arena → Valedia plan (2026-09-14)

## What it is
Upstream: `pfergi42/lf-game-theory` — 16 LLM agents, N rounds, **real Lightning sats** via Lightning Faucet.
Guide (markdown): `recommendations/SEBAI-AI-ECONOMIC-ARENA-GUIDE.md`
Catalog clone: `~/MemPalace/sources/repos/github/lf-game-theory`

## Hard fence (non-negotiable)
| Actor | Allowed | Forbidden |
|-------|---------|-----------|
| RuFlo / Grok Build / Valedia (this chat) | Code, config YAML, paper/sim harness, analysis, docs | Hold `LF_OPERATOR_KEY`, fund wallets, sweep sats, `--live` venue orders |
| **Burton / Valedia runtime** | Fund operator wallet, start funded runs, decide risk | — |

Upstream has **no simulation mode**. Any real run spends real sats + API credits.

## Why Valedia cares
Arena produces: balances, Gini, transfer network, message/faction graphs — a **behavior / negotiation signal**, not a trading venue.
Fit: optional intelligence input (like MiroFish weight), plus a lab to stress-test agent strategy prompts under scarcity.
Does **not** replace `master_allocator` / fee ledger / category gates.

## Phased implementation

### Phase A — Lab scaffold (no sats) ✅ docs / next code
1. Keep quarantine clone in MemPalace (license MIT — Ferguson).
2. Document env template **without secrets** (`.env.example` only).
3. Add Valedia-facing README: how Burton starts a pilot vs full run.
4. Optional: Grok Build task to add a **paper ledger** mode (fake balances, same actions) — upstream gap; valuable before funding.

### Phase B — Local models (still no Lightning)
1. Extend `src/llm/router.ts` (or fork) for OpenAI-compatible LMS `:1234` / Ornith / turbo / obliterated.
2. YAML: mix of local models instead of Claude+GPT (cuts API burn; keeps experiment local).
3. Smoke coordinator against paper mode or dry coordinator boot (stops at missing keys today — expected).

### Phase C — Burton-funded Lightning (Valedia runtime only)
1. Burton registers operator at lightningfaucet.com/ai-agents → `LF_OPERATOR_KEY` in **local .env only** (never chat).
2. Fund pilot first: `npm run arena:mixed-pilot` (8 agents × 10 rounds, lower `startingBalance` e.g. 100).
3. Then full / mixed if pilot OK.
4. Results DB → copy summary JSON into `~/.openclaw/workspace/models/arena_signal.json` for fusion (optional weight, start low like MiroFish 10%).

### Phase D — Fusion (after ≥1 clean pilot)
1. Schema for arena signal (bias / faction / gini / top strategist tags).
2. Wire read-only into signal fusion docs; **no auto-spend** from arena outcomes.
3. Update `RUFLO-STATUS.md` with evidence.

## First coding task for Grok Build (v2 worktree)
> Add **paper mode** + **LMS OpenAI-compatible provider** to a Valedia fork of lf-game-theory (or thin wrapper), with tests. Zero Lightning calls when `ARENA_MODE=paper`. Document Burton-only funded path.

## Explicit non-goals
- RuFlo placing Lightning payments
- Copying UNLICENSED kalshi-agent
- Treating arena “winner” as a live trade signal without human review
