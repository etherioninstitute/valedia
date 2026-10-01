# VAUGHAN-SUBSUME-1 — ArbitrageBot paper/risk → Valedia Poly↔Kalshi shadow

**Burton:** 2026-09-22 ~23:27 PT — continue Vaughan subsume (Build picker skipped; Valedia Trader proceeded with fenced default).  
**Depends on:** CE ladder + MIROFISH-INTEL-ARCHIVE DONE (Valedia 0.65.0 working tree).  
**Reference clones:** `~/MemPalace/sources/repos/github/vaughanf1-ArbitrageBot` (+ dossier `VAUGHAN-SUBSUME-DOSSIER.md`).  
**eToro / GoldQuant live / vibe-trader engine:** out of scope.

## Goal
Absorb **ArbitrageBot** paper-arb patterns into Valedia as a **fenced Poly↔Kalshi (and crypto-paper-adjacent) shadow/paper scanner**:
1. RiskGuard-style caps (per-trade size, daily exposure, daily-loss kill, min edge) beside existing Ornith + fee-true EV.
2. Allowlist-pair discipline: heuristic matches = review-only; never auto-fire unverified pairs.
3. Triple gate discipline: refuse half-live (no real leg + simulated hedge); paper/shadow only.
4. Surface opportunities + would-have paper fills only — **never** Poly LIVE writers, wallets, BitGet live, or their Railway going-live path.

## Scope (do)
1. Read MemPalace clone + dossier; map symbols onto Valedia modules (CBM + RuFlo). Prefer Python Valedia paths over copying the TS monorepo wholesale.
2. Implement a Valedia paper/shadow arb module (name fit existing layout, e.g. under prediction desk / crypto-paper sibling) that:
   - scans for Poly↔Kalshi pair mispricings using existing Valedia venue clients in **read/paper** mode
   - applies allowlist + review-only for non-allowlisted
   - applies risk caps + kill switch flag
   - fee-true edge before any would-have fill
   - Ornith fail-closed before any paper fill (reuse CE-1 safety where applicable)
3. Persist decisions to a jsonl/log (mirror CE-3 fee-true decisions shape if natural).
4. Tests: allowlist pass; review-only never fills; kill switch; Ornith FAIL; fee edge below min → refuse; `create_order` false; no Poly LIVE env flips.
5. Docs: short `VAUGHAN-ARB-PAPER.md` (or Controls line). Ponytail audit. Bump working-tree version.
6. Optional stretch (only if Slice 1 Done early): stub BB-Terminal `signals.ts` pattern note for Slice 2 — **do not** start Slice 2 coding unless time leftover and explicitly marked stretch.

## Explicit non-goals
- Enabling `POLYMARKET_US_LIVE`, writers, wallets, oil/weather unfence
- BitGet / Railway live; copying funded API keys from ArbitrageBot `.env`
- Running their dashboard as Valedia’s primary UI (OpenTerminal-Valedia stays)
- Absorbing GoldQuant, vibe-trader engine, or HedgeFund equity agents as live brains
- Treating paper PnL as realized; MiroFish bias as FV fuel

## Hard fence
Paper/shadow only. Screen `valedia-grok` only; preserve `ot-valedia-desk` / `valedia-glance`. No `--live`, no HALT bypass, no treasury auto.

## Tools
RuFlo `:8810` filtered. CBM for Valedia navigation. Ponytail after code. Prove tool use in `GROK-STATUS.md`.

## Done when
- Paper/shadow Poly↔Kalshi arb path green in tests with allowlist + Ornith + fee-true + kill caps
- One dry-run shows would-have or refuse without venue orders
- Audit written; `GROK_EXIT:0`; **Next = Burton stop** (no auto-chain Slice 2 unless parent queues it)

## Results
(pending)
