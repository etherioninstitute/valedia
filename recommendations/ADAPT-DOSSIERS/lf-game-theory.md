# H1 dossier — lf-game-theory (Arena)

**Path:** `/Users/burtonchatelain/MemPalace/sources/repos/github/lf-game-theory`  
**Upstream:** Paul Ferguson / Lightning Faucet (`pfergi42/lf-game-theory`)  
**License:** README claims **MIT**; **no `LICENSE` file**, `package.json` has no `license` field. Attribute on rewrite.  
**Slice:** H7 — offline strategy stress. **No funded sats unless Burton.**  
**Burton 2026-09-18:** Lightning / Arena sats are **out of profit scope**. Leave LN. Steal the **fight pattern only**.  
**Shipped (no LN):** `scripts/integrations/adversarial_paper_fight.py` — Buffett / momentum / MiroFish / risk-veto paper fight, JSON under `models/adversarial_round_*.json`, scored later vs fee-true FTL. Do not fund Arena. Do not `npm run arena:*`.  
**Prior docs:** `SEBAI-ARENA-*.md`, `BURTON-PILOT-RUN.md`, `BURTON-ARENA-SECURITY-AUDIT-GROK.md`. Clone already has `experiments/arena-valedia-micro-pilot.yaml` (4×5×100 sats) — **leave unfunded**.

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

1. **Adversarial multi-agent fight (SHIPPED, no LN)** — personas/strategies score the same opportunity set, disagreement recorded, risk can veto, later fee-true FTL scoring. Valedia module: `scripts/integrations/adversarial_paper_fight.py` (+ thin `adversarial_run.py`, **not** crontab-installed). This is the Arena energy without sats.  
2. **Four primes as strategy opponents** — strategic / competitive / cooperative / neutral. Optional later lenses; not extra traders.  
3. **Allowlisted action grammar + rate limits** — models emit text; **code** moves balances (same spirit as Intent gates).  
4. **Paper closed loop** — integer/JSON ledger, no HTTP. **Default. No faucet.**  
5. **Classic 5-game battery** — cheap unit tests of persona prompts (later).  
6. **Gini / exploitation / deception metrics** — robustness tags, not size. Do not add `FUSION_WEIGHTS["arena"]`.  
7. **Append-only run log** — FTL-shaped JSONL later; fight JSON is the first log.  
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

Arena LN is **left**. Fight pattern is a **lab leaf** on the one brain:

```
Valedia brain  ←  no arena weight, no LN
MiroFish / opps
        └── adversarial_paper_fight.py  (PAPER/SHADOW)
              → models/adversarial_round_*.json
              → later: fee-true FTL winner/loser
              → never dispatchable, never faucet
```

---

## H7-ready

**Done this session:** in-Valedia paper fight (no `lf-game-theory` process). Remaining H7 (optional, Burton go): fixture stress of primes; still `ARENA_MODE=paper`; refuse Lightning unless a **named Burton flag**. Do not start `npm run arena:*`. Do not fund.
