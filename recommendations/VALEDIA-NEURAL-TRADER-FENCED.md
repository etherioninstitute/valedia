# Valedia × ruflo-neural-trader (fenced) — Burton 2026-09-18

**North star:** maximum profit under gates. Valedia runtime = sole live risk.  
**This charter:** `ruflo-neural-trader` / `neural-trader` exists **only to strengthen MiroFish** (better features, regimes, backtested context, scored signals) so Valedia’s fusion + accuracy loop gets smarter. It is **not** a second trader.

Grok Build owns implementation under this document. OpenTerminal UI work is a sibling read-only cockpit (separate slice); it must display MiroFish + neural-enriched intel, never place orders.

---

## IS

1. **Intel substrate for MiroFish** — regime labels, walk-forward / Monte Carlo quality scores, feature attribution, strategy backtest summaries, portfolio risk *metrics* as *inputs* MiroFish (and Valedia fusion) can consume.
2. **Shadow-only outputs** — files / RuFlo memory under `trading-*` namespaces and/or JSON next to `mirofish_signal.json` / accuracy ledger. Downstream = existing Valedia paths (`intel_opportunity_gatherer`, `mirofish_accuracy_ledger`, `effective_mirofish_weight`, shadow Intents).
3. **Research CLI** — `trader backtest|signal|regime|risk|portfolio|train|explain` with pin `neural-trader@^2.8.11` and `--ignore-scripts` on install.
4. **Filtered MCP** — advertise ~10–20 tools max to Grok Build / RuVocal (ADR-035). Never dump 112+ tools every turn.
5. **Accuracy-gated weight** — neural-enriched MiroFish still capped: unproven ≤10%, proven ceiling ≤15%, `MIROFISH_AUTO_RAISE=False` until Burton says otherwise. Fee-true ledger remains the judge.

## IS NOT

1. **Not an OMS** — no `trader live`, no broker adapters writing Coinbase/Kalshi/Alpaca/HL/Poly orders.
2. **Not a parallel Kelly / allocator** — Valedia `KellySizer` + fee-true FTL + master_allocator stay canonical. Neural “Kelly” / portfolio optimize = advisory numbers into MiroFish context only.
3. **Not a VENUE_WRITER / NAUTILUS_LIVE / REBALANCER_AUTOEXEC / SPOT_RECONCILE_HTTP flip.**
4. **Not a replacement for MiroFish** — MiroFish remains the social/news/prediction intel process; neural-trader *feeds* it (features + scored priors), does not swallow it.
5. **Not OpenTerminal order buttons** — UI is read-only cockpit.

## Data flow (only allowed shape)

```
neural-trader (backtest/regime/signal/risk)
        │
        ▼
  enrich / feature pack  ──►  MiroFish seed / signal JSON
        │                            │
        │                            ▼
        │                   mirofish_accuracy_ledger (fee-true)
        │                            │
        └──────────────►  fusion weight (capped) + shadow Intents
                                   │
                                   ▼
                         Valedia runtime (sole live risk)
```

Hard rule: **no arrow from neural-trader to venue writers.** If a skill or MCP tool can place or imply live orders, it stays off the advertised tool list and off cron.

## Pinning & safety

| Item | Rule |
|------|------|
| Package | `neural-trader@^2.8.11` (darwin-arm64 OK). Older = fork-bomb risk on Mac — refuse. |
| Install | `npm install --ignore-scripts neural-trader@^2.8.11` |
| RuFlo plugin | `plugins/ruflo-neural-trader` from clone; skills for research only |
| MCP | Separate filtered server or `--tools` allowlist; deny `*live*`, broker, order tools |
| Audit | Keep/adapt `audit-neural-trader-safety.mjs` in CI or pre-slice smoke |

## MiroFish power-ups (what “adds to MiroFish” means)

Concrete enrichments Grok Build may implement (shadow/local JSON first):

1. **Regime prior** — `trader regime` → field on MiroFish signal (`regime`, `confidence`) for fusion context.
2. **Signal quality score** — walk-forward / Monte Carlo summary attached to a MiroFish opportunity id before weight influence.
3. **Feature pack** — explain/top-k features for the symbol MiroFish is talking about (not a separate trade).
4. **Accuracy linkage** — when fee-true outcome lands, record whether neural-enriched vs plain MiroFish was used (for later Burton-gated weight decisions).
5. **Never** raise `MIROFISH_WEIGHT` automatically from neural alone.

## OpenTerminal (sibling)

- Read-only panels: Valedia version/sha, FTL / shadow counts, MiroFish weight + accuracy, neural enrichment status (“fed MiroFish / idle”).
- No buy/sell from the UI into Valedia writers.

## Grok Build slice order (when Burton says go)

| Slice | Goal |
|-------|------|
| **NT-0** | Charter locked (this file). Pin package. Tool allowlist. Smoke that `trader live` is unreachable from Valedia configs. |
| **NT-1** | Regime + signal scan → MiroFish-adjacent JSON only; no fusion weight change. |
| **NT-2** | Wire enrichments into MiroFish seed/extractor; accuracy ledger tags “neural_enriched”. |
| **NT-3** | OpenTerminal Valedia+MiroFish read-only panel. |
| **NT-4** | Optional: RuFlo `trading-*` memory namespaces for research artifacts (still no live). |

STOP between slices for Burton. Fenced live deploy of any script that cron touches still needs explicit yes.

## Success criteria

- MiroFish signals are *measurably* richer (regime/features/quality fields present) without any new live order path.
- Fee-true accuracy ledger still controls weight caps.
- Grok Build / RuFlo sessions never advertise neural live/broker tools.
- `valedia_config` fence constants unchanged by NT slices unless Burton orders a flip in a separate, named go.

## Local models (Burton 2026-09-18)

Valedia’s local stack stays authoritative:

| Lane | Endpoint | Role |
|------|----------|------|
| LMS / Bionic | `http://127.0.0.1:1234/v1` | Hermes, RuVocal, Valedia chat-adjacent |
| MiroFish Ollama | `http://127.0.0.1:11435` | MiroFish swarm/report only |

**CLI-first for neural-trader.** Drive `npx neural-trader …` (regime/signal/backtest/risk/portfolio/train/explain). Do **not** run the plugin’s four agents as written (`model: opus` / `model: sonnet`). Those are Claude Code personas, not Valedia LMS. If agents are ever used, remap explicitly to LMS ids via Hermes/`custom:lms-local` — never silent Anthropic.

Neural math (Rust/NAPI) runs on-box; enrichment is JSON into MiroFish. MiroFish keeps using `:11435`. OpenTerminal optional Claude assistant = **off**.

Easy lane switching: see `VALEDIA-LOCAL-MODEL-SWITCH.md` + `scripts/valedia_models.sh`.

## References

- RuFlo plugin README: `plugins/ruflo-neural-trader/README.md`
- Plugin contract: `plugins/ruflo-neural-trader/docs/adrs/0001-neural-trader-contract.md`
- ADR-126 substrate (memory/CG/signing) — research only under this fence
- Valedia: `VALEDIA-MAX-PROFIT-AUTOTRADER.md`, `VALEDIA-VERSIONING.md`, `mirofish_accuracy_ledger.py`, hard fence in `GROK-BUILD-LANE.md`
