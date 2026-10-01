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

**2026-09-22 ~23:43 PT. VALEDIA_VERSION 0.66.0** (working tree, not committed, not tagged). Branch still has a large uncommitted set (158 paths). A slice-only commit would not check out clean.

`scripts/integrations/vaughan_arb_paper.py` scans Poly↔Kalshi quotes. Allowlisted pairs can paper-fill. Heuristic title matches are `review_only` and do not fill. Risk caps: $25 trade, $100 daily exposure, 5% daily-loss kill, 1% min fee-true edge, $5 size floor, explicit kill switch. Oil series (`KXWTI` / `KXOIL`) refuse `fenced_series`. Triple gate refuses `poly_live`, `half_live`, `not_paper`, `dry_run_off`, and `halt`. Ornith uses the CE-1 shape (`crypto_paper_safety._ornith_shape`). Kalshi fee is `kalshi_fee_map.fee` taker. Poly paper rate is 0. Global CLOB. Slip 5 bps.

Decisions append to `data/vaughan-arb/decisions.jsonl`. `source=vaughan_arb_paper` (contains `paper`). `create_order` false. Live `forward_test_ledger` is not written. Kelly / `real_track_record` ignore the source. Tail retention 400.

Echo allowlist (Judy Shelton YES 0.30 + Kalshi NO 0.40, $25): `pass`, fee 0.61, edge 40.346969%, would-have pnl 10.091786. Paper only. Controls do not count it as realized.

Fixture dry-run with `POLYMARKET_US_LIVE` hidden in the child only: heuristic Alice Chen `review_only` (edge 89.253066%, no fill); allowlisted Judy Shelton `malformed` from live `ornith-1.5-k-m` (model name present, CE-1 JSON shape failed, no fill). Parent process still has `POLYMARKET_US_LIVE=1` and that invocation refused `poly_live` with `create_order` false. The constant `POLYMARKET_US_LIVE` stays False. This job did not unset the parent flag.

`--read` was not run. It would GET Gamma and a signed Kalshi market list. The fixture book was enough, and this process is Poly-live at the env.

Tests `test_vaughan_arb_paper.py` PASS. `test_controls_readout.py`, `test_slice23.py`, `test_crypto_paper.py`, `test_crypto_paper_safety.py` PASS in a child that hid inherited `VALEDIA_JEV` / fee-map / Poly flags. Parent env unchanged. `WEATHER_LIVE` false. `OIL_BRACKET` and `WEATHER_ARB` still fenced.

Screens 783 `ot-valedia-desk`, 755 `valedia-glance`, 44478 `valedia-grok` still detached. This job did not signal them. No writer, no wallet, no venue order. BB-Terminal Slice 2 was not started.

**Tools:** RuFlo `hooks_route`, `hooks_pre-task`, `hooks_pre-edit`, `memory_search_unified`, `memory_retrieve` (filtered `:8810`). CBM `list_projects`, `search_graph`, `get_code_snippet` (`kalshi_fee_map.fee`), `check_index_coverage`. New module freshness is `metadata_changed` (best-effort; source + tests are the proof; no full reindex this turn). Ponytail `PONYTAIL-AUDIT-VAUGHAN-SUBSUME-1.md`. Desk note `VAUGHAN-ARB-PAPER.md`.

**Next = Burton stop.** Parent launches anything further. This job did not. Do not unfence oil/weather. Do not treat the paper edge as realized profit. Do not auto-start Slice 2.

`GROK_EXIT:0`
