# Ponytail audit — Valedia trading surface (Slice 20)

**Mode:** `/ponytail-audit` full intensity. **Report only** — nothing deleted, no refactors, no flag flips.
**Tree:** `~/.openclaw/workspace-v2` @ `51719ff` (pre-this-commit).
**Scope:** `scripts/integrations/**` (ex archive listed as a finding), cron/install helpers, `valedia_config`, live-trading recs (skim).
**Fence:** no `--live`, no venue orders, no wallet/spend, no key changes. Tags: `delete:` `stdlib:` `native:` `yagni:` `shrink:`.

## Findings (biggest cut first)

`delete:` `scripts/integrations/archive/` (21 bak/v1/v9 copies, 5613 lines). Git history is the backup. Live `stage_ere_safety` still imports v1 class names that only exist here. [scripts/integrations/archive/]

`yagni:` Etherion v2 10-class rewrite (1410) while live brain imports `SignalDecomposer`/`VarianceCascadeEngine`/`DrawdownGuard` — names that do not exist in the current file, so ERE always fail-closes to `{safe: True, multiplier: 1.0}`. Keep 3 live classes (or 3 aliases); drop unused Kolmogorov/LargeField/MassGap/Rosetta/alpha_risk_report (~700) plus dead `scipy.signal` Butterworth imports. [scripts/integrations/Etherion_Risk_Engine.py, unified_brain.py:354]

`yagni:` `latency_arb.py` (538) + `arb_run.py` (9). Dry-run scanner, not in `install_cron.sh`; only `settle_run` pokes `check_arb_settlements`. Nothing. [scripts/integrations/latency_arb.py, arb_run.py]

`yagni:` `quant_engine.py` (398) + `quant_run.py` (8). Second Kelly (`kelly_size`, docstring still says quarter-Kelly), second Kalshi scan, disabled `PolymarketAdapter`. Not on cron. Use `KellySizer` + `kalshi_executor`/`sports_engine`. [scripts/integrations/quant_engine.py]

`delete:` `*.DEPRECATED` scanners (374) that cron still names: `emerging_crypto_scanner.py`, `sentiment_scanner.py`, `alpha_intelligence.py`. Those files are gone from the live name; installer will 404. Drop files + three `install_cron.sh` lines. [scripts/integrations/*.DEPRECATED, scripts/install_cron.sh:21-24]

`yagni:` `valedia_memory.py` (345) Memori Labs + `openai` client. Brain already no-ops when import/key fails. Local JSON logs already exist. Nothing. [scripts/integrations/valedia_memory.py]

`yagni:` `weather_arb.py` (289) + `weather_trade.py` (7). Slice 8 forces `dry_run=True` and never `create_order`; not in current cron (allocator replaced it); intel gatherer does not read it. Keep the live-exec fence constant; delete the scanner. [scripts/integrations/weather_arb.py, weather_trade.py, valedia_config.py LIVE_EXEC_FENCED]

`delete:` `UNIFIED_STACK_ANALYSIS.py` (236). March 2026 skill-inventory prose in a `.py` nobody imports. A markdown rec if you still want the inventory. [scripts/integrations/UNIFIED_STACK_ANALYSIS.py]

`yagni:` `signal_fusion.py` (214) non-live scanner (`LIVE_PATH=False`) with its own `WEIGHTS`, pandas/talib/yfinance. Live fusion is `FUSION_WEIGHTS` → `stage_fusion`. Nothing. [scripts/integrations/signal_fusion.py]

`yagni:` `strategy_modules.py` unused products: `Rebalancer` (dup of `rebalancer.py`), `WhaleCopyTrader` (dup of `whale_copier.py`), `EmergingCryptoScanner`, `GridTrader`, `strategy_report`. Brain only uses `DCAEngine` + report-only `MicroScalper`. ~250 lines. [scripts/integrations/strategy_modules.py]

`yagni:` `kalshi_scanner.py` (167) + `scanner_run.py` (48). Heuristic 600-market scan, not in installer. Executor/allocator already scan. Nothing. [scripts/integrations/kalshi_scanner.py, scanner_run.py]

`delete:` `combinatorial_arb.py` (180). Detection-only, zero importers, self-check `__main__` only. Never routed into executor. Nothing. [scripts/integrations/combinatorial_arb.py]

`delete:` `intel_sentiment.py` (148). Claimed merge of the DEPRECATED scanners; no sibling importer, not on cron. `intel_layer` already reads JSON. Nothing. [scripts/integrations/intel_sentiment.py]

`delete:` `ml_retrain.py` (129) sklearn+talib+pickle. Cron runs `ml_kernel_retrain.py`. Nothing. [scripts/integrations/ml_retrain.py]

`yagni:` `dca_module.py` (111) vs `strategy_modules.DCAEngine`. Cron has `dca_module`; brain instantiates `DCAEngine`. One DCA, one state file. [scripts/integrations/dca_module.py, strategy_modules.py, unified_brain.py:966]

`delete:` `market_data.py` (88). yfinance/pandas/talib hub, zero importers. Nothing. [scripts/integrations/market_data.py]

`yagni:` `KellySizer.RegimeDetector` (~80) pulls 6mo SPY via yfinance (SMA/ADX/vol) on size. `valedia_config.dynamic_per_trade_cap` already takes a regime string; pass ERE/json regime. [scripts/integrations/kelly_sizing.py]

`yagni:` dead cron wrappers `kalshi_trade.py` / `kalshi_alert.py` / `kalshi_audit.py` (7 each). `allocator_run.py` docstring says it replaced them; installer no longer calls them. Nothing. [scripts/integrations/kalshi_trade.py, kalshi_alert.py, kalshi_audit.py]

`yagni:` `whale_copier.py` (60) on cron every 4h is a report; `strategy_modules.WhaleCopyTrader` is a second copy. Keep one reporter. [scripts/integrations/whale_copier.py]

`native:` `fee_engine.fetch_kraken_fees` re-implements HMAC sign (~30). Call `kraken_client._private("TradeVolume")`. Live brain fee gate already uses `use_static=True`, so the live-fetch path is unused on the exec brain. [scripts/integrations/fee_engine.py:69, kraken_client.py:26]

`shrink:` `shadow_gates` + `_ere_level` copied in `nautilus_shadow_coinbase.py` and `nautilus_shadow_venues.py` (~40). One helper next to `nautilus_intent`. Do not merge away `live_flag=False`. [scripts/integrations/nautilus_shadow_coinbase.py:172, nautilus_shadow_venues.py:34]

`native:` Fear & Greed GET copied in `intel_layer`, `intel_refresh`, `intel_sentiment`, `mirofish_runner`, `unified_brain.stage_intel`. One function, one JSON file. ~60. [those files]

`yagni:` leftover flat-fee constants after `kalshi_fee_per_contract` became canonical: `kalshi_executor.KALSHI_FEE_RATE=0.02`, `weather_arb.KALSHI_FEE=0.02`, `quant_engine.KalshiAdapter.FEES=0.007` (passed into `expected_value` then ignored). Delete the constants. [kalshi_executor.py:63, weather_arb.py:30, quant_engine.py:219]

`yagni:` `KILLED_STRATEGIES` + `is_killed_strategy` aliases of `LIVE_EXEC_FENCED` / `live_exec_fenced`. Tests and allocator still call the alias — rename in tests, drop alias (~8). [scripts/integrations/valedia_config.py:47,148]

`yagni:` `cron_runner_mac.sh` `should_send` always `return 1` plus `_send_signal` (~30). Dead Signal path. Scripts send themselves. [scripts/cron_runner_mac.sh:153]

`yagni:` `cron_runner_mac.sh` self-heal `pip3 install` map includes `cv2`→opencv and `talib`. Trading surface does not need opencv; pip-from-cron is a supply-chain footgun. Drop the map, log the miss. [scripts/cron_runner_mac.sh:36]

`yagni:` ~16 modules `sys.path.insert(0, "~/.openclaw/workspace/scripts/integrations")` from this v2 tree. Running v2 code silently imports live. `dirname(__file__)` only. [kelly_sizing.py:30 and siblings]

`stdlib:` ERE imports `pandas` (never used as `pd`) and `scipy.signal.butter/filtfilt/lfilter` (never called). `scipy.stats.skew/kurtosis` (TopologicalRegimeProtector only, unused by live) → `numpy` moments. Drop pandas+scipy from ERE. [scripts/integrations/Etherion_Risk_Engine.py:34]

`native:` GET-only `requests` (FNG, CoinGecko, NOAA, ESPN) → `urllib.request`. Keep `requests` only where JWT/HMAC bodies already use it (`kalshi_auth`, `coinbase_trader`, `alpaca_executor`). [intel_refresh.py, weather_arb.py, whale_copier.py, hedge_fund_intel.py]

`yagni:` `execution_policy.py` docstring still says `EXEC_DRY_RUN` default True; `valedia_config.EXEC_DRY_RUN = False`. Not a cut — do not “fix” by deleting `MakerOrderManager`. Point the docstring at the constant. [execution_policy.py:6, valedia_config.py:107]

`yagni:` `settle_run.py` maintains a parallel sports JSON P&L (`sports_engine_log.json`) next to `forward_test_ledger`. Settlement Signal can read FTL. Keep one fee-true ledger. [scripts/integrations/settle_run.py, forward_test_ledger.py]

`yagni:` `intel_refresh.py` (141) exists because “MiroFish swarm times out at 120s”. If runner is the source of `mirofish_signal.json`, this is a second writer. One writer. [scripts/integrations/intel_refresh.py, mirofish_runner.py]

`shrink:` `unified_brain.py` (1036) still constructs `MicroScalper` + yfinance 1mo histories every cycle for a report that cannot buy (`scalper_place_buy` dead). Drop the scalp block (~20). DCA belongs in `dca_module` cron, not inside the brain cycle. [scripts/integrations/unified_brain.py:964]

`yagni:` `compare_platforms` / `fee_report` shop every venue including polymarket/kraken. Live gate is `roundtrip_cost(..., use_static=True)` on the chosen platform. Keep `STATIC_FEES` + `kalshi_fee_per_contract`; drop the comparison printer unless someone runs `__main__`. [scripts/integrations/fee_engine.py:203, kelly_sizing.py:269]

`shrink:` `hyperliquid_order_body.send_via_live_hook` and `polymarket_order_body.send_via_live_hook` are identical “HOOK is None → refuse”. One function. Do not bind a client. [hyperliquid_order_body.py:90, polymarket_order_body.py:62]

`yagni:` `VENUE_NT_CUTOVER_ORDER` tuple is documentation-as-code (Slice 19 checklist already exists). A comment in `VALEDIA-LIVE-CUTOVER-PREP.md` is enough until a writer flips. ~3. [scripts/integrations/valedia_config.py:323]

`yagni:` `install_cron.sh` still schedules the three missing DEPRECATED scanners plus `whale_copier` / `dca_module` / `asi_scan` as separate intel jobs that overlap `hedge_fund_intel` + brain. Installer is not live crontab (Slice 16 note) — still a footgun if someone runs it. [scripts/install_cron.sh]

`native:` `kelly_sizing` numpy SMA/std on SPY → `statistics.fmean` / `stdev` if RegimeDetector stays at all. [scripts/integrations/kelly_sizing.py:48]

`yagni:` `fund_manager.py` (120) + `fund_pipeline.py` (80) + `mercury_client.py` (69). Three cash/check helpers; cron only runs `fund_pipeline`. One. [those files]

`yagni:` `claude_bridge.py` (173) + `claude_bridge_server.py` (91). HTTP sidecar for portfolio context, not on trading cron. Out of the live path. Nothing unless Burton still hits it. [scripts/integrations/claude_bridge.py]

`yagni:` `backtest_etherion.py` (304) + `backtest_modeled_prob.py` (86) are one-off research CLIs, not cron. Move to `archive/` or a research folder; do not keep next to the live writer. [scripts/integrations/backtest_*.py]

`yagni:` `validate_tail_bracket.py` (105) writes `/tmp/kalshi_settled.json`. One-shot. Nothing in the trading loop. [scripts/integrations/validate_tail_bracket.py]

`shrink:` duplicated `os.environ.setdefault('KALSHI_API_KEY_ID', …)` in `settle_run` / `quant_engine`. Auth belongs only in `kalshi_auth` env load. Do not copy key material into more files. [settle_run.py:12, quant_engine.py:224]

`yagni:` `insider_mirror.py` (78) on installer cron; `intel_sentiment` also has Form 4. One insider source. [scripts/integrations/insider_mirror.py]

`yagni:` `dynamic_universe.py` (648) CoinGecko/Reddit/Stocktwits/Finnhub discovery. Brain universe can stay a static watchlist + hot_list JSON. Speculative crawl. [scripts/integrations/dynamic_universe.py]

`yagni:` `mirofish_seed_accurate.py` (209) beside `mirofish_runner.generate_financial_seed`. One seeder. [scripts/integrations/mirofish_seed_accurate.py, mirofish_runner.py:20]

`shrink:` `allocator_run.py` / `sports_run.py` / `pnl_run.py` / `parity_run.py` / `mirofish_ledger_run.py` are 9–52 line `__main__` shims. Cron can `python3 -c 'from sports_engine import run; print(run())'` or `python3 sports_engine.py`. Keep if crontab strings must stay stable; otherwise in-file `if __name__`. [those `*_run.py`]

net: -10200 lines, -6 deps possible.

Deps that become droppable if the cuts above land: `scipy` (ERE only), `pandas` (ml_retrain/signal_fusion/market_data/backtest), `talib` (same), `sklearn` (ml_retrain), `memori` + `openai` (valedia_memory). `yfinance` shrinks but stays until CoinGecko covers remaining callers. `requests` stays for signed venue HTTP.

## Do not cut

These look heavy and must stay (fence / realized-P&L truth / single writer):

- `nautilus_intent.py` Intent schema, `enqueue_intent`, never-dispatchable default.
- `valedia_config.VENUE_WRITER` + `assert_exclusive_writer` / `ExclusiveWriterError` (dual-write is -EV).
- `NAUTILUS_ENABLED=False`, `NAUTILUS_LIVE=False`, `nautilus_may_dispatch` / `nautilus_live_fills_allowed`.
- `nautilus_fill_sink.live_source_allowed` — fill-true; paper/sandbox/backfill cannot write `source=live`.
- `LIVE_CLIENT_HOOK = None` and `send_via_live_hook` refuse (HL/PM stubs).
- Shadow emitters forcing `dispatchable=False` / `live_flag=False` (coinbase, venues, intel gatherer).
- `SPOT_RECONCILE_HTTP` default False; `spot_order_get` inject-only tests; no live GET until Burton flips.
- `REBALANCER_AUTOEXEC` default False; AUTOEXEC still cannot NT-dispatch.
- `LIVE_EXEC_FENCED` / `scalper_live_enabled()` / `weather_live_enabled()` hard-False (env cannot enable).
- `KellySizer` + `forward_test_ledger` real-only track record / `category_stats` / `KELLY_UNPROVEN_MULT` / `KELLY_MIN_PROVEN_TRADES`.
- `fee_engine.STATIC_FEES` + `kalshi_fee_per_contract` (nonlinear). Brain `stage_fee_gate` `use_static=True`.
- `MIROFISH_AUTO_RAISE=False`, unproven cap 10%, `effective_mirofish_weight()`.
- Intentionally missing `coinbase_trader.place_market_order` (allocator Coinbase is a dead writer on purpose).
- `MakerOrderManager` cancel-safety; do not delete because dry-run comments drifted.
- `pause_ttl` / learning pause (not a grave) vs live-exec fence (independent).
- `install_cron.sh` sports `:00` / allocator `:20` stagger (Slice 7 double-bet race).
- Kernel v16e + ERE *gate* (the fail-closed call). Cut unused v2 modules, not the safety seam.

## Top 10 worth a follow-up slice (profit/ops, not aesthetics)

1. **ERE actually binds.** Live `stage_ere_safety` imports v1 names → always multiplier 1.0. Alias or retarget 3 classes. Highest risk/P&L impact; no flag flip.
2. **Stop installer 404s.** Remove DEPRECATED cron lines (`emerging` / `sentiment` / `alpha_intel`) so a future `install_cron.sh` run does not spawn three failing jobs.
3. **One Kelly.** Delete `quant_engine.kelly_size` (and the module if still unwired). Drift here sizes Kalshi wrong vs sports/brain.
4. **One fee number.** Delete leftover 0.02 / 0.007 constants. Canonical is `kalshi_fee_per_contract`.
5. **One settlement ledger.** Point `settle_run` Signal at `forward_test_ledger` (fee-true). Sports JSON log becomes a view, not a second book.
6. **One FNG / price fetch.** Rate-limit and yfinance 404s already burned Signal (lessons.md). CoinGecko helper that exists in `intel_refresh` / `dynamic_universe`.
7. **Delete archive + UNIFIED_STACK + combinatorial_arb + unused scanners.** Pure ops hygiene; unblocks (1) by removing the v1 lure.
8. **Drop sklearn/talib ML cron-adjacent code** (`ml_retrain`, `signal_fusion` scanner, `market_data`). Live edge is kernel + FTL, not pickle RF.
9. **One DCA / one rebalance / one whale reporter.** Brain+cron currently double-implement; DCA inside `run_brain` can place Coinbase buys on a different schedule than Sunday cron.
10. **Do not grow NT stubs.** Shadow DRY (`shadow_gates`) only. Next profit on Coinbase is a Burton-gated writer, not more POST-diff JSON. HL/PM stay `VENUE_WRITER=none`.

Not on this list (and not a coding-agent go): flipping `SPOT_RECONCILE_HTTP`, `REBALANCER_AUTOEXEC`, `VENUE_WRITER`, `--live`, `LIVE_CLIENT_HOOK`, or adding `place_market_order`.
