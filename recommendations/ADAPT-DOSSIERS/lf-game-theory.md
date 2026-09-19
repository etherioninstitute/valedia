# H1 dossier — lf-game-theory (Arena)

**Path:** `/Users/burtonchatelain/MemPalace/sources/repos/github/lf-game-theory`  
**Upstream:** Paul Ferguson / Lightning Faucet (`pfergi42/lf-game-theory`)  
**License:** README claims **MIT**; **no `LICENSE` file**, `package.json` has no `license` field. Attribute on rewrite.  
**Slice:** H7 — offline strategy stress. **No funded sats unless Burton.**  
**Prior docs:** `SEBAI-ARENA-*.md`, `BURTON-PILOT-RUN.md`, `BURTON-ARENA-SECURITY-AUDIT-GROK.md`. Clone already has `experiments/arena-valedia-micro-pilot.yaml` (4×5×100 sats).

---

## Identity

Research CLI: LLM agents play classic games **and** a free-form **AI Economic Arena** with **real Lightning sats**. Question: does **behavioral priming** change outcomes? Not a trader. No venues, no Kelly, no OMS.

Headline (one Jan 2026 run, **not a law**): strategic primes **+100%** sats; cooperative **−41%**; largest flow Cooperative→Strategic.

Stack: Node ≥20 (Burton: **24.20.0**; 26 broke `better-sqlite3`). TypeScript. Cloud Anthropic + OpenAI **eagerly constructed**. Faucet `https://lightningfaucet.com/api/` + `LF_OPERATOR_KEY`.

---

## Architecture

Two runtimes:

| Path | Entry | Money |
|------|-------|-------|
| Classic games | `src/coordinator.ts` + `src/games/*` | PD, ultimatum, trust, public goods, dictator; then LN fund/sweep |
| Arena | `src/arena/coordinator.ts` | N agents, 100 rounds, SEND/MESSAGE/BROADCAST/PASS; **real LN** |

Parser allowlist (`src/arena/action-parser.ts`). Analyzer: Gini, transfer graph, factions, crude deception. **No paper/sim mode** in upstream — a successful Arena run is faucet + API. SQLite stores prompts, actions, **agent keys** (audit F1).

Valedia overlay in-tree: `npm run arena:valedia-micro`. Grok Build does **not** start it.

---

## STEAL

1. **Four primes as strategy opponents** — strategic / competitive / cooperative / neutral. Use as *policies that attack Valedia strategies*, not extra traders.  
2. **Allowlisted action grammar + rate limits** — models emit text; **code** moves balances (same spirit as Intent gates).  
3. **Paper-sats closed loop** (missing upstream) — integer ledger, no HTTP. **This is the only H7 default.**  
4. **Classic 5-game battery** — cheap unit tests of persona/strategy prompts (H2 Buffett vs extractive peer).  
5. **Gini / exploitation / deception metrics** — robustness tags, not size.  
6. **Append-only run log** — FTL-shaped JSONL.  
7. **YAML micro matrix** — ≤4 agents, ≤10 rounds, local models.  
8. **Research hygiene** — one-run README results are not fusion alpha.

---

## LEAVE

| Leave | Why |
|-------|-----|
| Lightning Faucet / `LF_OPERATOR_KEY` / funded sats | Third-party custody; crash-before-sweep strands wallets; 1% fee >100 sats |
| Eager Anthropic+OpenAI clients | Local Qwen/LMS; missing OpenAI key crashes before funding |
| Agent keys in SQLite | Theft surface |
| Arena wealth → orders / `FUSION_WEIGHTS["arena"]` | Second trader |
| Vendoring `src/` + `node_modules` | Duplicate coordinator |
| Full 16×1000×100 | Audit NO-GO until Burton |
| `npm run arena:*` from this lane | Burton-only after paper green |

---

## Valedia touchpoints

| Surface | Use |
|---------|-----|
| MiroFish accuracy | **Do not** score Arena rounds as fee-true hits. Optional seed *tag* “lens is cooperative-exploitable” (same class as `neural_enriched`: context, not weight) |
| Shadow Intents | If any: `dispatchable=False`, `ledger_source=paper` |
| H2 personas | Primes in a paper game, scored by robustness |
| Kelly / FTL / allocator | **No hook** |
| OpenTerminal (H4) | Later read-only `arena_stress` summary |

`MIROFISH_AUTO_RAISE` stays False. Arena Gini never raises weight.

---

## Risks

- Upstream **has no sim mode** — easy to accidentally spend sats.  
- Local `arena-valedia-micro-pilot.db` may hold key-shaped fields — don’t commit/sync.  
- Treating “strategic doubles” as a trading edge.  
- License file missing.  
- Second brain if someone crons Arena into the allocator.

---

## Seamless hook

Arena is a **lab leaf**, not a trunk:

```
Valedia brain  ←  no arena weight
MiroFish       ←  optional robustness tags on seed
        └── arena_stress.py (paper default, local LLM or fixtures)
              → models/arena_stress.json
              → never dispatchable
```

---

## H7-ready

`scripts/integrations/arena_stress.py`: `ARENA_MODE=paper` default; refuse Lightning unless a **named Burton flag**. Fixture test: cooperative policy loses to strategic. Zero network in CI. Do not start `npm run arena:*`.
