# Valedia × TypeSafe Jev (fenced) — Burton 2026-09-19

## What “Jev” is

**TypeSafe Jev** is a hosted System-One model (typed decisions + confidence), used via TypeSafe AI API — not a local GGUF you load in Bionic. Typical client: `@ai-sdk/typesafe-ai` + `TYPESAFE_AI_API_KEY`. Pin model id when integrating (e.g. `jev-1.13.0` from architecture notes).

**Not the same as** `jev-trader` (Monad/Kuru block-loop demo).

## MemPalace clones

| Path | Repo | Role |
|------|------|------|
| `~/MemPalace/sources/repos/github/awesome-jev-by-typesafe` @ `afd223f` | Anil-matcha/awesome-jev-by-typesafe | Official-leaning patterns, prompts, starter code |
| `~/MemPalace/sources/repos/github/awesome-jev` @ `f2f25e4` | yibie/awesome-jev | Curated public Jev projects |
| `~/MemPalace/sources/repos/github/jev-trader` @ `b587759` | jarrodwatts/jev-trader | Reference only: evaluate API + dry-run loop (wrong venue for Valedia OMS) |

Burton chat export: `~/Downloads/Jev.md` (MiroFish dual-world + Jev CIO pack architecture).

## NOT

- NOT wiring jev-trader (or any Jev call) to place Kalshi/Poly/CB/Alpaca orders.
- NOT letting Jev compute Kelly / size arithmetic (code owns math).
- NOT dumping full MiroFish GraphRAG into Jev state.
- NOT swarm-every-candle; Gate 0 first.
- NOT auto-enabling TypeSafe in allocator until Burton flips a shadow/live flag.

## IS

- IS MemPalace reference for typed gates (CIO pack: action, we_are_the_exit, worlds_disagree, report_usable, …).
- IS candidate for **MIROFISH-JEV-SHADOW** after fee-map: Gate 0 + compressor → Jev → log only; code sizes; MiroFish never OMS.
- IS compatible with standing reminder: TypeSafe experiment after trading is live — Kalshi staged ON makes **shadow** appropriate; live arm still Burton go.

## Install (API access, not weights)

1. Keep login at typesafe.ai (Burton has one).
2. Put `TYPESAFE_AI_API_KEY` in env when a shadow slice needs it (do not commit).
3. App dependency when Build implements shadow: `@ai-sdk/typesafe-ai` (see jev-trader `package.json`) — Valedia Python path may use HTTP/SDK equivalent; prefer one client.
4. Do **not** `bun run start` jev-trader live with `PRIVATE_KEY` on Valedia machines.

## Related

- Fee map: `KALSHI-FEE-MAP` / `KALSHI_FEE_MAP_GATE` (default False)
- Architecture intent: `~/Downloads/Jev.md`
- Routine: TypeSafe after live trading (weekday remind)

## TypeSafe skill (installed 2026-09-19)

Official skill: `typesafe-ai` from https://github.com/typesafe-ai/skills

| Where | Path |
|-------|------|
| Global agents | `~/.agents/skills/typesafe-ai` |
| Grok Build / project | `~/.openclaw/workspace-v2/.agents/skills/typesafe-ai` |
| Claude Code plugin | `typesafe@typesafe-ai` (marketplace `typesafe-ai`, user scope) |
| MemPalace clone | `~/MemPalace/sources/repos/github/typesafe-skills` @ 65a39f3 |

Agents should read live docs via the skill (`https://docs.typesafe.ai/llms.txt`) when building Jev/System One features. Fence unchanged: no OMS from Jev.

## API key (wired 2026-09-19)

`TYPESAFE_AI_API_KEY` is in `~/.openclaw/.env` (mode 600). Source of truth for the secret remains Proton Drive `VPS/API Keys/Typsafe Jev API Key.txt`. Never commit. Grok Build / Valedia scripts should read from that env; do not paste into chat or status files.

## Availability (2026-09-19 evening)

| Surface | Status |
|---------|--------|
| Claude Code | Plugin `typesafe@typesafe-ai` + skill `~/.claude/skills/typesafe-ai` |
| Grok Build / Cursor / Codex | `workspace-v2/.agents/skills/typesafe-ai` + global `~/.agents/skills` |
| Hermes | Skill symlink `~/.hermes/skills/typesafe-ai`; keys in `~/.hermes/.env` |
| OpenClaw / Valedia | Keys in `~/.openclaw/.env` |
| LiteLLM `:4000` | Keys in `~/.litellm/env` for scripts — **Jev is NOT an OpenAI chat model** on the gateway (System One `/v1/systemone`, not `/v1/chat/completions`). Do not add a fake `model_list` entry. |
| Bionic / local GGUF | N/A |

Smoke: `jev-smoke` → `POST https://api.typesafe.ai/v1/systemone` (verified `jev-1.13.0`).
Env aliases: both `TYPESAFE_API_KEY` and `TYPESAFE_AI_API_KEY`.

## Shipped (AUTONOMY-2, 0.50.0)

`scripts/integrations/jev_shadow.py` — optional shadow only. Flag `VALEDIA_JEV` default **False**. How to flip / fail-open: `AUTONOMY-2-JEV-SHADOW.md`. Do not default True.
