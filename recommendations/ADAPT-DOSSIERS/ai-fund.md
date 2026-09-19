# H1 dossier — ai-fund (Buffett + personas)

**Path:** `/Users/burtonchatelain/ai-fund-inspect`  
**Upstream:** `github.com/cubexch/ai-fund` (Cube Exchange)  
**License:** MIT claimed (`package.json`); **no `LICENSE` file** in this inspect clone. Council harvest cites commit `bb9a243`. Attribute `cubexch/ai-fund` / @seb.ai; confirm upstream LICENSE before any public paste.  
**Slice:** H2 first (personas → MiroFish/intel lenses). Later slices do **not** vendor the runtime.

---

## Identity

Open-source “hire/fire” AI hedge fund for **Claude Code**: 45 agents (21 named personas + 24 roles), 147 MCP tools (Cube 31 / CCXT 92 / Alpaca 24), 110 CCXT venues. Paper by default. TypeScript `lib/` + MCP `connectors/`. Version `0.1.0`. Built to send market data **off-device** to cloud Claude — that violates Valedia’s local-only rule.

Valedia already harvested prompts into `council/advisors/` (45 `.md`) and TS maths into `council/lib/`. **The missing piece is a wired lens, not another copy of the skills.**

---

## Architecture (what they actually run)

```
Claude Code  →  hire agent skills  →  MCP connectors (Cube/CCXT/Alpaca)
                      │
                      ▼
              lib/ (Kelly, DCF, regime, backtester, MM)
                      │
                      ▼
              place_order / get_fills on connected venues
```

Personas are SKILL.md files (`skills/warren-buffett/SKILL.md`, `peter-lynch/`, `arthur-hayes/`, `ed-thorp/`, `jim-simons/`, `risk-manager/`, …). Each has philosophy, checklists, KPI “when to fire me,” and **exchange API recipes including `place_order`**. Risk-manager is supposed to approve; that is a second desk, not Valedia’s 7 gates.

---

## STEAL (rewrite; ranked)

1. **Buffett 7-point buy checklist** (`council/advisors/warren-buffett.md:121–131`): understandable, moat, management, ROE/FCF/debt, >25% margin of safety, 10y hold, downside. Verdicts BUY/HOLD/TOO EXPENSIVE/OUTSIDE CIRCLE. Crypto = **outside circle** (feature, not a bug).
2. **Lynch GARP / six categories / PEG<1** (`peter-lynch.md`) — equity story vs price; 10-bagger hunt as *flag*, not size.
3. **Hayes macro-to-crypto thesis** (`arthur-hayes.md`) — liquidity/DXY/real-yield *lens* for BTC/ETH. **Leave his 2–5x leverage.**
4. **Risk-manager “what I refuse to fund”** as a *comment* on shadow Intents — maps onto fee-gate + fund-rules, does not replace them.
5. **Overfit red-flags** from `council/lib/backtester.ts`: Sharpe>3, WR>80%, n<30, >50% OOS degradation — later FTL hygiene, not H2.
6. **Hire/fire KPIs** as accuracy-ledger *labels* (persona was used; did fee-true settle?). Never auto-fire a second Kelly.
7. **`valuation.ts` `marginOfSafety(fair, market)`** — one function, only when real fundamentals exist. Do **not** invent DCF from MiroFish sentiment.

Do **not** steal: 45 concurrent agents, MCP connectors, `place_order` blocks, Thorp Kelly (Valedia `KellySizer` already has half-Kelly + Thorp `f/√N` + Bayesian shrink, noted as adapted from kalshi-agent).

---

## LEAVE

| Leave | Why |
|-------|-----|
| Cloud Claude Code runtime + 147 MCP tools | Data leaves the Mac; dump vs ADR-035 filter |
| Cube / CCXT / Alpaca connectors | Venue writers. Alpaca already Valedia-owned |
| `ed-thorp` Kelly / `portfolio-optimizer` as live sizer | Second Kelly / second allocator |
| 45-agent swarm cron | Token burn; Ornith-CIO Phase 2 is still a proposal |
| `test:live` npm scripts | Live venue tests |
| DuckDB / yahoo-finance2 stack | Seed already uses CoinGecko + Yahoo **chart** API |
| Persona `place_order` / `get_account` sections | Venue keys |

---

## Valedia touchpoints

| Valedia | Hook |
|---------|------|
| `council/advisors/*.md` | **Already harvested.** H2 reads checklists as data, does not re-clone skills |
| `mirofish_seed_accurate.py` | Advisory markdown section (same pattern as NT-2 `apply_seed_text`) |
| `mirofish_signal_extractor.py` | Stamp per-symbol `persona` without changing score/bias/weight |
| `mirofish_neural_enrich.py` | Sibling JSON, not a replacement |
| `intel_opportunity_gatherer.py` | Fields ride in `raw` via MiroFish blob; gatherer stays local-JSON |
| `kelly_sizing.py` / `FUSION_WEIGHTS` | **Untouched** |
| `mirofish_accuracy_ledger.py` | Optional later tag `persona_lenses`; **not** H2 unless cheap |

---

## Risks

- **Second brain:** 45 agents that approve their own trades. Fence: lenses **score**, they never size or send.
- **Fake fundamentals:** MiroFish JSON has score/direction/confidence, not FCF/ROE/PEG. Honest verdict is `INSUFFICIENT_DATA` / `OUTSIDE_CIRCLE`, not a made-up DCF.
- **Buffett vs crypto-first book:** Buffett abstains on BTC. That is useful (don’t treat crypto as value). Hayes covers crypto macro. Do not force Buffett to “like” SOL.
- **License:** MIT — attribute Cube/seb.ai on rewrite; already in `council/README.md`.
- **IP / likeness:** Simulations of public *styles*, not the real people. Never present as advice.

---

## Seamless hook

Personas are **MiroFish/intel ears**, same class as neural-trader enrich:

```
opportunity JSON
    → buffett / lynch / hayes checklists (no keys, no LLM required)
    → models/persona_lens_scores.json
    → attach onto mirofish_signal (scores unchanged)
    → seed section for the swarm
    → gatherer shadow Intents (paper, live_flag=False)
    → Kelly / FTL / allocator unchanged
```

When Burton flips `VALEDIA_SELF_ADJUST`, lenses can start contributing to *which* intel the swarm reads — still inside MiroFish band, still not a writer.

---

## H2-ready

**Shipped H2 (0.26.0):** `persona_lenses.py` Buffett/Lynch/Hayes attach on MiroFish (no DCF invention).  
**Shipped paper fight (0.27.0):** `adversarial_paper_fight.py` reuses H2 Buffett vs momentum/MiroFish/risk-veto. No keys, no LLM.

**Still later:** Lynch (GARP) + Hayes (crypto macro) as extra lenses. Skip Thorp (Kelly already ours). Simons *scoring* is optional — **not** his `0.5 × Kelly`. Lenses **propose JSON, never execute**. Do not `npm i` connectors.

**Adapter:** `scripts/integrations/adversarial_paper_fight.py`, deterministic, stdlib. Schema per symbol:

```json
{
  "id": "buffett",
  "verdict": "OUTSIDE_CIRCLE|INSUFFICIENT_DATA|GREEDY_WHEN_FEARFUL|FEARFUL_WHEN_GREEDY|HOLD",
  "score": 50,
  "abstain": true,
  "confidence": 0.9,
  "reasons": ["crypto is not productive capital"]
}
```

`fusion_touched=False`, `auto_raise=False`, no `requests`, no Intent enqueue from this module.
