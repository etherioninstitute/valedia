# Vaughan → Valedia subsume dossier

**Date:** 2026-09-22 PT  
**Clones (MemPalace):**
- `~/MemPalace/sources/repos/github/vaughanf1-ArbitrageBot`
- `~/MemPalace/sources/repos/github/vaughanf1-BB-Terminal`
- `~/MemPalace/sources/repos/github/vaughanf1-HedgeFund-V2`
- `~/MemPalace/sources/repos/github/vaughanf1-GoldQuant`
- `~/MemPalace/sources/repos/github/vaughanf1-vibe-trader` (branch `neon-apple-theme`)

## Recommendations
| Repo | Rec | Why |
|---|---|---|
| ArbitrageBot | **ABSORB first** | Poly↔Kalshi paper arb, allowlist, kill switch, fee/risk caps — highest Valedia overlap |
| BB-Terminal | FORK-UI / patterns | OpenBB INTEL/`signals.ts` → OpenTerminal-Valedia enrich (Slice 2) |
| HedgeFund-V2 | INVENTORY + light patterns | Committee/signal gate; no brokerage; optional React Flow later |
| vibe-trader | INVENTORY-ONLY | Huge live-capable agent; shadow/order-guard patterns only |
| GoldQuant | INVENTORY-ONLY | XAU/metals — fenced with commodities until Burton opens |

**eToro:** out of this Build — eggbot / eToro Scout (browser→network→skill).

## Steal paths (ArbitrageBot)
- `packages/risk/` — size / daily exposure / daily-loss kill / min edge
- `packages/scanners/` — prediction-pairs allowlist + review-only
- `packages/executors/` paper path + triple gate (`TRADING_MODE` / `LIVE_VENUES` / `EXECUTION_DRY_RUN`)
- `GOING-LIVE-CHECKLIST.md`, `OPERATING.md`, `HANDOVER.md` — ops discipline only; do **not** enable their live Poly writers
