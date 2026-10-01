# Ponytail audit — Valedia FULL (post–Slice 20 / Arc H / Poly US / treasury / MiroFish)

**Mode:** `/ponytail-audit` full intensity. **Report only** — no deletes applied, no flag flips, no version bump.
**Tree:** `~/.openclaw/workspace-v2` @ `ee6da91` (MIROFISH-SMOKE status on `0.37.0` / tag `valedia-v0.37.0` `5b49cc1`).
**Scope:** `scripts/integrations/**` (archive listed as a finding), `valedia_config.py` fences, runtime-affecting recs; crons/docs only where they over-engineer or 404.
**Fence held:** no `--live`, no `VENUE_WRITER`, no `POLYMARKET_US_LIVE`, no `REBALANCER_AUTOEXEC`, no `NAUTILUS_LIVE`, no `VALEDIA_SELF_ADJUST` / `MIROFISH_AUTO_RAISE` True, no treasury auto, no `VALEDIA_TRADING_HALT` invent-or-flip.
**Graph:** codebase-memory project `Users-burtonchatelain-.openclaw-workspace-v2` (full index). Cited paths: `indexed_no_recorded_gap` (best-effort, not completeness).
**Tiny cuts this session:** none. Unused-import noise is listed; landing it would not change behavior and is not worth a version tag.

Tags: `delete:` `yagni:` `shrink:` `native:` `stdlib:` plus **safe-cut** vs **do-not-cut**.

---

## vs Slice 20 (`PONYTAIL-AUDIT-VALEDIA.md`)

Slice 20 (`51719ff`, report-only) ranked **net ~−10200 lines, −6 deps**. **None of those deletes landed** (by design). The files are still on disk at the same sizes.

What changed since then is **additive**, not leaner:

| | Slice 20 | Now (0.37.0) |
|---|---|---|
| Live ERE names | brain imported v1 classes that only lived in `archive/` | aliases exist (`SignalDecomposer = RpsiDecomposer` etc. at `Etherion_Risk_Engine.py:1404`) — Slice 21 |
| ERE gate | always `{safe: True, multiplier: 1.0}` | **still FAIL_CLOSED** — `EnergyLadderCascade` has `energy_ladder_check`, **no** `cumulative_extremes` (`unified_brain.stage_ere_safety` excepts) |
| New runtime modules | — | ~3775 lines: personas, adversarial, cockpit, desk/HL public, shadow events, arena, fortress fallback, Poly-E, Poly US + smoke, treasury, MiroFish smoke, NT fence/enrich |
| New fences | SPOT / AUTOEXEC / NT / LIVE_EXEC | **plus** `VALEDIA_SELF_ADJUST`, band 0.05–0.25/0.30, `FORTRESS_INTEL_FALLBACK`, `POLYMARKET_US_LIVE`, `VALEDIA_TREASURY_AUTO`, `TREASURY_FLOOR_*` |
| Recs | Slice 20 audit + cutover prep | **16** `PONYTAIL-AUDIT-*.md` + Poly/treasury/MiroFish charters. Per-slice audits were the right size; do not merge them into this file. |
| Installer 404s | DEPRECATED scanners still cron'd | **unchanged** (`install_cron.sh:21-24`) |

Biggest new smell is not the new harnesses (those were ponytail-scoped per slice). It is **the Slice 20 graveyard still sitting next to a second graveyard of smoke CLIs**, while ERE still no-ops sizing.

---

## Findings (biggest cut first)

`delete:` `scripts/integrations/archive/` (21 bak/v1/v9 copies, **5613**). Git is the backup. Still the largest pile. Do not restore v1 `cumulative_extremes` from here without a behavioral pass — bind it on v2 instead. [scripts/integrations/archive/]

`yagni:` Etherion v2 10-class rewrite (**1410**) while the live gate only needs 3 aliased classes **and** still excepts: `EnergyLadderCascade.cumulative_extremes` is missing (only in archive v1). Keep 3 live classes + bind extremes; drop unused Kolmogorov/LargeField/MassGap/Rosetta/alpha_risk_report (~700). Unused imports: `pandas as pd`, `scipy.signal.butter/filtfilt/lfilter/lfilter_zi`. `scipy.stats` + `yfinance` are used. [scripts/integrations/Etherion_Risk_Engine.py:34-37, :279; unified_brain.py:351]

`yagni:` `latency_arb.py` (**547**) + `arb_run.py` (9). Dry-run scanner; not in `install_cron.sh`; `settle_run` pokes `check_arb_settlements`. Nothing. [scripts/integrations/latency_arb.py, arb_run.py]

`yagni:` `quant_engine.py` (**398**) + `quant_run.py` (8). Second Kelly (`kelly_size`), second Kalshi scan, disabled `PolymarketAdapter`. Config still documents quarter-Kelly as a known divergence (`valedia_config.py:413`). Use `KellySizer` + executor/sports. [scripts/integrations/quant_engine.py]

`delete:` `*.DEPRECATED` scanners (**374**) that installer still names: `emerging_crypto_scanner.py`, `sentiment_scanner.py`, `alpha_intelligence.py`. Drop files + three `install_cron.sh` lines. **Do not run the installer.** [scripts/integrations/*.DEPRECATED, scripts/install_cron.sh:21-24]

`yagni:` `valedia_memory.py` (**345**) Memori + `openai`. Brain already no-ops. Local JSON logs exist. [scripts/integrations/valedia_memory.py, unified_brain.py:38]

`yagni:` `weather_arb.py` (**289**) + `weather_trade.py` (7). Slice 8 `dry_run=True`, never `create_order`; not on current cron. Keep `LIVE_EXEC_FENCED`; delete the scanner. [scripts/integrations/weather_arb.py, valedia_config.py:62]

`delete:` `UNIFIED_STACK_ANALYSIS.py` (**236**). March 2026 skill-inventory prose, zero importers. [scripts/integrations/UNIFIED_STACK_ANALYSIS.py]

`yagni:` `signal_fusion.py` (**214**) non-live scanner (`LIVE_PATH=False`) with its own `WEIGHTS`, pandas/talib/yfinance. Live fusion is `FUSION_WEIGHTS` → `stage_fusion`. [scripts/integrations/signal_fusion.py]

`yagni:` `dynamic_universe.py` (**648**) CoinGecko/Reddit/Stocktwits/Finnhub crawl. Brain can keep a static watchlist + hot_list JSON. Speculative. [scripts/integrations/dynamic_universe.py]

`yagni:` `strategy_modules.py` unused products: `Rebalancer` / `WhaleCopyTrader` / `EmergingCryptoScanner` / `GridTrader` / `strategy_report`. Brain uses `DCAEngine` + report-only `MicroScalper`. ~250. [scripts/integrations/strategy_modules.py]

`yagni:` `kalshi_scanner.py` (**167**) + `scanner_run.py` (48). Heuristic scan, not in installer. Executor/allocator already scan. [scripts/integrations/kalshi_scanner.py]

`delete:` `combinatorial_arb.py` (**180**). Detection-only, zero importers. [scripts/integrations/combinatorial_arb.py]

`delete:` `intel_sentiment.py` (**148**). No sibling importer, not on cron. `intel_layer` already reads JSON. [scripts/integrations/intel_sentiment.py]

`delete:` `ml_retrain.py` (**129**) sklearn+talib+pickle. Cron runs `ml_kernel_retrain.py`. [scripts/integrations/ml_retrain.py]

`yagni:` `dca_module.py` (**112**) vs `strategy_modules.DCAEngine`. One DCA, one state file. [scripts/integrations/dca_module.py]

`delete:` `market_data.py` (**88**). yfinance/pandas/talib hub, zero importers. [scripts/integrations/market_data.py]

`yagni:` dead cron wrappers `kalshi_trade.py` / `kalshi_alert.py` / `kalshi_audit.py` (7 each). Allocator replaced them. [those files]

`yagni:` `backtest_etherion.py` (**304**) + `backtest_modeled_prob.py` (86) + `validate_tail_bracket.py` (105). Research CLIs, not cron. Move out of live writer dir. [scripts/integrations/backtest_*.py]

`yagni:` `claude_bridge.py` (**173**) + `claude_bridge_server.py` (91). HTTP sidecar, not on trading cron. [those files]

`yagni:` `fund_manager.py` (120) + `fund_pipeline.py` (80) + `mercury_client.py` (69). Three cash helpers; cron only `fund_pipeline`. Do not copy Mercury digits into treasury. [those files; treasury_status.py reuses venue readers]

`yagni:` `whale_copier.py` (60) + `insider_mirror.py` (78) on installer cron vs intel JSON. One reporter each. [scripts/integrations/whale_copier.py, insider_mirror.py]

`yagni:` `mirofish_seed_accurate.py` (**241**) beside `mirofish_runner.generate_financial_seed`. Smoke already refuses `build_seed` HTTP. One seeder for overnight; smoke stays fixture-only. **Do not gut the smoke.** [mirofish_seed_accurate.py, mirofish_smoke.py]

`yagni:` four Polymarket surfaces (**907**): Int'l `polymarket_client.py` (76, scan-only, no `import` callers — only hygiene reads), `polymarket_order_body.py` (66), `polymarket_entertainment.py` (321, Gamma tags), `polymarket_us_client.py` (359, US DCM). US vs Int'l split is a **fence** (do-not-cut). Entertainment vs US `NAV_PRIMARY_SLUG` is the shrink later — not this session. [those files]

`yagni:` three hand smokes + cockpit until FULL-SMOKE: `mirofish_smoke.py` (348), `poly_us_smoke.py` (85), `treasury_status.py` (265), `valedia_cockpit.py` (196 GET panel). Next queue item is one CLI. **Do not merge or delete them here.** [those files]

`yagni:` `arena_stress.py` (**296**) wraps `adversarial_paper_fight.py` (334) + `adversarial_run.py` (28). H7 spec; no crontab; no `FUSION_WEIGHTS["arena"]`. Fold persist-prefix if a third writer appears. [arena_stress.py]

`yagni:` `desk_roles.parse_approve` / `ticket_from_record` — grammar only, live callers use `label_shadow`. Keep labels; chat-approve send path stays unshipped. [desk_roles.py:45, intel_opportunity_gatherer.py, nautilus_shadow_venues.py]

`yagni:` `intel_refresh.py` second writer of intel JSON because “swarm times out”. One writer (`mirofish_runner`) if the runner is source of `mirofish_signal.json`. [intel_refresh.py]

`yagni:` leftover flat fees after `kalshi_fee_per_contract` became canonical: `kalshi_executor.KALSHI_FEE_RATE=0.02`, `weather_arb.KALSHI_FEE=0.02`, `quant_engine.KalshiAdapter.FEES=0.007`. [kalshi_executor.py:63, weather_arb.py:30, quant_engine.py:219]

`yagni:` `KILLED_STRATEGIES` alias of `LIVE_EXEC_FENCED`; `is_killed_strategy` alias of `live_exec_fenced`. Tests still call the alias. [valedia_config.py:62-63, :265]

`yagni:` `VENUE_NT_CUTOVER_ORDER` tuple is documentation-as-code. Checklist in `VALEDIA-LIVE-CUTOVER-PREP.md` is enough until a writer flips. [valedia_config.py:443]

`yagni:` `TREASURY_ALERT_WEBHOOK` unused stub; `treasury_status` never POSTs. Leave until a reader exists. [valedia_config.py:135]

`yagni:` `install_cron.sh` still points at **live** `~/.openclaw/workspace/scripts` (not this v2 tree) and still schedules missing DEPRECATED scanners + overlapping intel jobs. Footgun if someone runs it. Slice 16 note stands. [scripts/install_cron.sh:6-8, :21-24]

`yagni:` `cron_runner_mac.sh` `should_send` always `return 1` plus `_send_signal` (~30). Dead Signal path. `pip3 install` map still has `cv2`/`talib`. [scripts/cron_runner_mac.sh:153, :36]

`yagni:` ~16 modules `sys.path.insert(0, "~/.openclaw/workspace/scripts/integrations")` from this v2 tree — silent live import. `dirname(__file__)` only. [kelly_sizing.py:30 and siblings; graph files-list]

`yagni:` `KellySizer.RegimeDetector` (~80) pulls 6mo SPY via yfinance on size. Pass ERE/json regime into `dynamic_per_trade_cap`. [kelly_sizing.py]

`shrink:` `shadow_gates` + `_ere_level` copied in `nautilus_shadow_coinbase.py` and `nautilus_shadow_venues.py` (~40). One helper next to `nautilus_intent`. **Do not merge away `live_flag=False`.** [nautilus_shadow_coinbase.py:172, nautilus_shadow_venues.py:34]

`shrink:` `send_via_live_hook` identical refuse in HL + PM stubs. One function. Do not bind a client. [hyperliquid_order_body.py:90, polymarket_order_body.py:62]

`native:` Fear & Greed GET copied in `intel_layer`, `intel_refresh`, `intel_sentiment`, `mirofish_runner`, `unified_brain.stage_intel`, `sports_engine`, `hedge_fund_intel`, `mirofish_seed_accurate`. One function, one JSON. ~60. [those files]

`native:` `fee_engine.fetch_kraken_fees` re-HMAC (~30). Call `kraken_client._private("TradeVolume")`. Live brain fee gate uses `use_static=True`. [fee_engine.py, kraken_client.py]

`native:` GET-only `requests` (FNG, CoinGecko, NOAA) → `urllib.request`. Keep `requests` for signed venue HTTP. [intel_refresh.py, weather_arb.py, whale_copier.py]

`stdlib:` ERE unused `pandas` + `scipy.signal`. `scipy.stats.skew/kurtosis` used by unused-by-live `QEIDrawdownGuard` path. [Etherion_Risk_Engine.py:34-37]

`shrink:` `unified_brain.py` (**1047**) still constructs `MicroScalper` + yfinance histories for a report that cannot buy. Drop scalp block (~20). DCA belongs in `dca_module` cron. [unified_brain.py ~964]

`yagni:` recs that **do** affect runtime are the flip checklists, not the per-slice ponytail files. Keep: `VALEDIA-MIROFISH-DYNAMIC-WEIGHT.md`, `VALEDIA-TREASURY-FUNDING.md`, `POLYMARKET-US-CUTOVER.md` / `POLYMARKET-US-API-MATRIX.md`, `POLYMARKET-ENTERTAINMENT-CUTOVER.md`, `SPOT-RECONCILE-HTTP-FLIP.md`, `VALEDIA-LIVE-CUTOVER-PREP.md`, `VALEDIA-SUBSUME-NAUTILUS.md`, `VALEDIA-NEURAL-TRADER-FENCED.md`, `VALEDIA-SEAMLESS-ARCHITECTURE.md`. Do not execute their “flip” sections from this lane. Historical `PONYTAIL-AUDIT-H*.md` stay as receipts.

`yagni:` `VALEDIA_TRADING_HALT` / kill-switch **does not exist yet**. Next queue after FULL-SMOKE. Do not invent it in this audit. Do not skip it later.

---

## Safe-cut vs do-not-cut

### Safe-cut (later slice; not this session)

Hygiene only. No fence, no fill-true, no smoke harness:

- archive/ + UNIFIED_STACK + combinatorial_arb + market_data + ml_retrain + intel_sentiment + DEPRECATED files + dead `kalshi_*` wrappers
- installer lines for missing scanners (edit `install_cron.sh` **without running it**)
- leftover 0.02 / 0.007 fee constants
- unused ERE `pandas` / `scipy.signal` imports
- FNG helper collapse; `shadow_gates` DRY; identical `send_via_live_hook`
- `sys.path` live-tree inserts → `__file__`

### Do-not-cut (fences / fill-true / kill-switch-to-come)

Looks heavy; must stay:

- `valedia_config.VENUE_WRITER` + `assert_exclusive_writer` / `ExclusiveWriterError`
- `NAUTILUS_ENABLED=False`, `NAUTILUS_LIVE=False`, `nautilus_may_dispatch` / `nautilus_live_fills_allowed`
- `nautilus_fill_sink.live_source_allowed` — paper/sandbox/backfill cannot write `source=live`
- `LIVE_CLIENT_HOOK = None` + `send_via_live_hook` refuse
- Shadow emitters `dispatchable=False` / `live_flag=False` (coinbase, venues, intel gatherer, Poly-E)
- `SPOT_RECONCILE_HTTP` default False
- `REBALANCER_AUTOEXEC` default False (NT cannot dispatch)
- `LIVE_EXEC_FENCED` / `scalper_live_enabled()` / `weather_live_enabled()` hard-False
- `VALEDIA_SELF_ADJUST=False`, `MIROFISH_AUTO_RAISE=False`, caps 0.10/0.15, band 0.05–0.25, ceiling 0.30
- `FORTRESS_INTEL_FALLBACK=False`, engine PARKED, classify `{ok,empty,blocked,waf}`, no venue CDP
- `POLYMARKET_US_LIVE` default False; `LiveWriteBlocked`; preview ≠ create; ack ≠ fill; US module ≠ Int'l CLOB
- `VALEDIA_TREASURY_AUTO=False`; `attempt_auto_topup` raises even if env=1; no ACH/wire
- `KellySizer` + `forward_test_ledger` fee-true / `category_stats` / `KELLY_UNPROVEN_MULT`
- `fee_engine.STATIC_FEES` + `kalshi_fee_per_contract`; brain `use_static=True`
- H3 `resolve_kalshi_fill` GET poll — POST-ack never FILLED
- `nautilus_intent` schema + never-dispatchable default; H6 shadow event states + `inflight_unknown`
- `valedia_cockpit` GET-only (POST 405, no OMS)
- `hl_public_info` public `/info` only; `VENUE_WRITER["hyperliquid"]=none`
- `neural_trader_fence` pin/ignore-scripts; no live broker
- `persona_lenses` attach-only (no Kelly, no `FUSION_WEIGHTS["buffett"]`)
- MiroFish smoke / Poly US smoke / treasury_status / accuracy ledger — **do not gut**
- `install_cron.sh` sports `:00` / allocator `:20` stagger (double-bet race)
- ERE **gate call** (fail-closed). Cut unused v2 modules, not the seam.
- **Kill-switch-to-come:** `VALEDIA_TRADING_HALT` is the next *authoring* slice after FULL-SMOKE. Do not pre-build; do not stage ON without it.

Intentionally missing `coinbase_trader.place_market_order` stays missing.

---

## Top 10 follow-ups (profit/ops, not aesthetics)

1. **ERE actually binds.** Aliases exist; `cumulative_extremes` does not → multiplier 1.0 forever. Highest P&L impact; no flag flip.
2. **Stop installer 404s** (DEPRECATED cron lines). Do not `install_cron.sh` on live.
3. **One Kelly.** Delete `quant_engine.kelly_size`.
4. **One fee number.** Delete leftover 0.02 / 0.007.
5. **One settlement ledger.** `settle_run` Signal reads FTL (Slice 22 started this; sports JSON is a view).
6. **One FNG / price fetch.**
7. **Delete archive + UNIFIED_STACK + unused scanners.**
8. **Drop sklearn/talib ML cron-adjacent** (`ml_retrain`, `signal_fusion` scanner, `market_data`).
9. **One DCA / one rebalance / one whale reporter.**
10. **FULL-SMOKE (next queue) then KILL-SWITCH.** Do not grow NT stubs; do not flip writers.

Not on this list: flipping `SPOT_RECONCILE_HTTP`, `REBALANCER_AUTOEXEC`, `VENUE_WRITER`, `--live`, `POLYMARKET_US_LIVE`, `VALEDIA_SELF_ADJUST`, `MIROFISH_AUTO_RAISE`, `VALEDIA_TREASURY_AUTO`, `NAUTILUS_LIVE`, or adding `place_market_order`.

---

## Applied this session

Nothing. Report-only. Unused-import AST hits (~116) are mostly `from __future__ import annotations` false positives plus dead-file noise. Not worth a `0.37.1` tag.

## Tests (this session)

All PASS, no network, no production ledger, no live flags:

- `test_mirofish_smoke.py` PASS
- `test_treasury.py` PASS
- `test_poly_us.py` PASS
- `test_h3.py` PASS
- `test_slice23.py` PASS (`VALEDIA_VERSION` still `0.37.0`)

## Net

Slice 20 pile still on the table: **~−10200 lines, −6 deps** (`scipy` if ERE drops it after stats, `pandas`/`talib`/`sklearn` if scanners die, `memori`+`openai` if memory dies). `yfinance` shrinks, `requests` stays for signed HTTP.

Post-S20 add (~3775 runtime) is **mostly do-not-cut**. Extra shrink without gutting harnesses: ~200–400 (DRY gates, FNG, unused ERE imports, fee constants).

**Lean already? No — Slice 20 still unpaid. New brain is fenced, not bloated. Ship the report. Next = VALEDIA-FULL-SMOKE (parent launches; this session STOP).**
