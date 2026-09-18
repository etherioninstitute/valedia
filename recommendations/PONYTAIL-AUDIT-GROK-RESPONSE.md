# Grok Build response — Slice 20 Ponytail audit (Slice 21 Part A)

**Verifier:** Grok Build, worktree `~/.openclaw/workspace-v2` @ pre-fix `9478e69`, then Slice 21 label patches.
**Method:** Read `PONYTAIL-AUDIT-VALEDIA.md` end-to-end; independently grepped/read imports, `install_cron.sh`, **live crontab**, ERE aliases, and live-brain call sites. Did not delete anything. Fence held.
**Overall:** The audit is directionally right on bloat and on “ERE is a no-op.” It is **wrong on the ERE root cause** and it **scoped the installer instead of live crontab**, so several “not on cron / will 404” claims do not match what actually runs.

Live crontab (this machine, 2026-09-18) is the profit path. `install_cron.sh` is a footgun, not the schedule.

---

## 1. Verdict per major finding

| Finding | Verdict | One-line evidence |
|---|---|---|
| `delete:` `scripts/integrations/archive/` (5613 lines) | **PARTIAL** | `wc -l archive/*` = 5613 **AGREE**. “v1 names only exist here” **DISAGREE** — aliases `Etherion_Risk_Engine.py:1404` `SignalDecomposer = RpsiDecomposer`. Archive is the **only** copy of `cumulative_extremes` (`archive/Etherion_Risk_Engine_v1_backup.py:176`). Do not delete until that method is ported. |
| ERE v2 unused; live brain always `{safe: True, multiplier: 1.0}` | **PARTIAL** | Symptom **AGREE**: `unified_brain.stage_ere_safety` `except` → `_safe`. Cause **DISAGREE**: v2 classes **do** exist via aliases; fail-closed is `cascade.cumulative_extremes(matrix)` (`unified_brain.py:360`) — method missing on `EnergyLadderCascade`. `energy_ladder_check` / `check_guard` / `signal_strength` **are** on v2. |
| `latency_arb` + `arb_run` not on cron | **DISAGREE** (live) / **AGREE** (installer) | Not in `install_cron.sh`. **Live crontab:** `*/30 * * * * … arb_run.py "LATENCY ARB"`. `arb_run.py:9` `run(mode='dry-run')`. Watchdog “30 min from latency arb” is true on live. |
| `quant_engine` + `quant_run` not on cron | **DISAGREE** (live) | Live: `0 */6 * * * … quant_run.py "QUANT ENGINE"`. Banner lied “Kelly: quarter” / “Kalshi: ACTIVE” (Slice 21 relabeled SCAN-ONLY). |
| `*.DEPRECATED` scanners + installer 404 | **PARTIAL** | Installer **AGREE** `install_cron.sh:21-24` names `emerging_crypto_scanner.py` / `sentiment_scanner.py` / `alpha_intelligence.py`. Live files **MISSING** those names (only `*.DEPRECATED`). **Live crontab does not schedule them.** Running the installer would 404 **and** wipe other live jobs (`grep -v cron_runner_mac`). |
| `valedia_memory` Memori/openai | **AGREE** | `unified_brain.py:37` try/except sets `MEMORY_AVAILABLE`; no-op on fail. Local JSON already exists. |
| Delete `weather_arb` (forced dry) | **PARTIAL** | `weather_arb.execute_weather_trades` forces `dry_run=True`; not on live crontab; allocator `gather_weather_opportunities()==[]`. Fence lives in `valedia_config.LIVE_EXEC_FENCED` / `weather_live_enabled()` — **do not delete those**. Scanner is hygiene, not fence. |
| Delete `UNIFIED_STACK_ANALYSIS.py` | **AGREE** | Prose in a `.py`; zero importers. |
| `signal_fusion` `LIVE_PATH=False` | **AGREE** | `signal_fusion.py:11`. Live fusion is `FUSION_WEIGHTS` → `stage_fusion`. |
| `strategy_modules` unused products | **AGREE** (trim) | Brain uses `DCAEngine` + report-only `MicroScalper` (`unified_brain.py:966+`). Keep those two; Rebalancer/WhaleCopyTrader/Grid dupes are yagni. |
| `kalshi_scanner` + `scanner_run` not in installer | **DISAGREE** (live) | Live: `30 */6 * * * … scanner_run.py "MARKET SCANNER"`. |
| Delete `combinatorial_arb.py` | **AGREE** | Zero importers (grep). Detection-only `__main__`. |
| Delete `intel_sentiment.py` | **DISAGREE** | Live: `0 2 * * * … intel_sentiment.py "INTEL+SENTIMENT"`. File exists on live. |
| `ml_retrain` vs `ml_kernel_retrain` | **PARTIAL** | Installer schedules `ml_kernel_retrain.py`. **Live crontab schedules `ml_retrain.py` at 01:00**, not `ml_kernel_retrain`. |
| Dual DCA (`dca_module` vs `DCAEngine`) | **AGREE** | Cron Sunday `dca_module.py`; brain instantiates `DCAEngine` every cycle and can `cb_buy`. Two writers, two clocks. |
| Delete `market_data.py` | **AGREE** | Zero importers. |
| `KellySizer.RegimeDetector` yfinance SPY | **AGREE** | `kelly_sizing.py:33` `RegimeDetector.detect` pulls 6mo SPY. `dynamic_per_trade_cap` already takes a regime string. |
| Dead wrappers `kalshi_trade` / `alert` / `audit` | **DISAGREE** for alert+audit | Live: `kalshi_alert.py "MORNING BRIEF"` 07:00; `kalshi_audit.py` 23:30. `kalshi_trade.py` not on live cron (**AGREE** that one). |
| `whale_copier` + `WhaleCopyTrader` | **AGREE** | Live cron every 6h `whale_copier.py`. Keep one reporter. |
| `fee_engine.fetch_kraken_fees` HMAC reimplementation | **AGREE** | `fee_engine.py:69`. Brain fee gate `use_static=True` — live-fetch unused on exec path. |
| Duplicate `shadow_gates` | **AGREE** | `nautilus_shadow_coinbase.py` + `nautilus_shadow_venues.py`. Shrink helper; **do not** merge away `live_flag=False`. |
| Fear & Greed GET copied | **AGREE** | `intel_layer` / `intel_refresh` / `intel_sentiment` / `mirofish_runner` / `unified_brain.stage_intel` / `sports_engine.get_fear_greed`. |
| Leftover 0.02 / 0.007 fee constants | **AGREE** | `kalshi_executor.KALSHI_FEE_RATE=0.02` (`:63`); `weather_arb.KALSHI_FEE=0.02`; `quant_engine.KalshiAdapter.FEES=0.007`. Canonical is `kalshi_fee_per_contract`. |
| `KILLED_STRATEGIES` alias | **AGREE** | `valedia_config.py:47-48,148`. Tests still call alias. |
| `cron_runner_mac.sh` `should_send` always 1 | **AGREE** | `cron_runner_mac.sh:153-154`. Dead Signal path. Scripts send themselves. |
| cron self-heal `pip3` opencv/talib | **AGREE** | `cron_runner_mac.sh:36-41`. Supply-chain footgun. |
| v2 `sys.path.insert` → live tree | **AGREE** | e.g. `kelly_sizing.py:30`, `hedge_fund_intel.py:8`, `strategy_modules.py:9`, `portfolio_monitor.py:9`, `fund_pipeline.py:8`. Running v2 can import live. |
| ERE unused `pandas` + `scipy.signal` | **PARTIAL** | `pandas as pd` never used (**AGREE**). `butter/filtfilt/lfilter` never called (**AGREE**). `scipy.stats.skew/kurtosis` **used** by `QEIDrawdownGuard.compute_risk_functional` (`Etherion_Risk_Engine.py:697-698`). Do **not** drop scipy if ERE is bound. |
| GET-only `requests` → `urllib` | **AGREE** (optional) | Keep `requests` for signed venue HTTP. |
| `EXEC_DRY_RUN` docstring vs constant | **AGREE** | Docstring said default True; `valedia_config.EXEC_DRY_RUN = False` (`:107`). Slice 21 pointed the docstring at the constant. Do not delete `MakerOrderManager`. |
| Dual settlement book `settle_run` vs FTL | **AGREE** | `settle_run.settle_sports` writes `sports_engine_log.json`; Signal was `Profit:` from that book. Slice 21 labeled `sports-log not FTL` (prefix kept per `signal-protocol.md`). |
| `intel_refresh` second MiroFish writer | **PARTIAL** | Live cron runs **both** `intel_refresh.py` every 4h **and** `mirofish_primary.sh` 03:00. |
| Brain MicroScalper + yfinance 1mo | **AGREE** | `unified_brain.py` scalper block report-only; `scalper_place_buy` dead. |
| `compare_platforms` / `fee_report` | **AGREE** | Live gate is `roundtrip_cost(..., use_static=True)`. |
| Identical `send_via_live_hook` refuse | **AGREE** | HL/PM stubs; do not bind a client. |
| `VENUE_NT_CUTOVER_ORDER` yagni | **PARTIAL** | Three-line tuple is documentation-as-code. Cutting it does not help P&L. Fence scaffolding — leave it. |
| Installer still schedules missing scanners + overlap | **AGREE** (and worse) | `install_cron.sh` `grep -v cron_runner_mac` would drop live arb/quant/pnl/alert/scanner/universe. Do **not** run it on live. |
| Dual cash helpers | **AGREE** | Live cron only `fund_pipeline.py`. |
| `claude_bridge*` | **AGREE** | Not on trading cron. |
| `backtest_*.py` research CLIs | **AGREE** | Not cron. |
| `validate_tail_bracket.py` | **AGREE** | One-shot `/tmp`. |
| Duplicated `KALSHI_API_KEY_ID` setdefault | **AGREE** | `settle_run.py:12`, `quant_engine.py:224`. Auth belongs in `kalshi_auth` only. |
| `insider_mirror` + Form 4 in intel_sentiment | **AGREE** | Both on live cron. |
| `dynamic_universe` speculative | **DISAGREE** “not in loop” | Live: `30 */6 * * * … dynamic_universe.py "DISCOVER"`. |
| Two MiroFish seeders | **AGREE** | `mirofish_seed_accurate.py` vs `mirofish_runner.generate_financial_seed`. |
| `*_run.py` shims disposable | **DISAGREE** as a cut | Live crontab **is** those wrappers (`sports_run`, `allocator_run`, `quant_run`, `arb_run`, `pnl_run`, `settle_run`, …). Keep until crontab strings change. |
| Do-not-cut list (Intent, VENUE_WRITER, NAUTILUS_*, fill-sink, LIVE_CLIENT_HOOK=None, SPOT GET off, AUTOEXEC off, LIVE_EXEC_FENCED, KellySizer+FTL, STATIC_FEES, MIROFISH_AUTO_RAISE, missing `place_market_order`, MakerOrderManager, pause TTL, sports/allocator stagger in **installer**, ERE **gate**) | **AGREE** | Do not cut. Stagger is in installer only — **not live** (see missed). |

Net line-count / dep-drop: plausible **if** live crontab is reconciled first. Blindly deleting “not on cron” modules from the audit list would delete code live still invokes.

---

## 2. What the audit MISSED (max-profit under the fence)

1. **Live crontab ≠ `install_cron.sh`.** Highest ops/P&L miss. Live still has **allocator `0 */4` and sports `0 */2`** — same-minute collision on 4h boundaries. Slice 7 stagger (`ALLOCATOR_CRON_MINUTE=20`) is installer-only. Double-bet race is still live.
2. **ERE root cause is `cumulative_extremes`, not missing class names.** Aliases already exist. One method port from v1 would actually bind the safety multiplier (risk + size). Deleting archive first loses the only implementation.
3. **Jobs the audit called idle are live:** latency arb 30m (dry), quant 6h, `intel_sentiment` 02:00, `scanner_run` 6h, `dynamic_universe` 6h, `kalshi_alert` morning brief, `kalshi_audit` 23:30, `ml_retrain` 01:00, `pnl_run` 23:00, `intel_refresh` 4h.
4. **Alpaca LIVE vs PAPER split-brain.** `alpaca_executor.IS_LIVE = True` (hardcoded) vs `portfolio_monitor` `ALPACA_PAPER` default `"true"`. Brain banner can say LIVE while EOD monitor says PAPER. Cash/P&L truth.
5. **`fund_pipeline` marks BTC at `* 70000`.** Stale notional in a live 03:00 job (`fund_pipeline.py:35`).
6. **Signal protocol already broken on live.** Protocol = only `Invested:` / `Profit:`. Live also sends morning-brief EV scan, `pnl_run` broker P&L, watchdog, arb settle. Accuracy work has to name the extra senders, not pretend protocol is intact.
7. **Allocator `[LIVE]` includes Coinbase allocations that `import place_market_order` and FAIL** (intentional missing symbol). Banner is run-mode, not fill-true.
8. **Brain labeled POST-ack as `EXECUTED: N trades`.** `spot_ack_ok` is accept, not fill (`forward_test_ledger.py:137`). Slice 21 relabeled ACKED / POST-ack in v2.

---

## 3. What the audit OVERSTATED (false-delete / fence scaffolding)

- **“Class names do not exist → always fail-closed.”** Names exist. Deleting archive to “remove the v1 lure” before porting `cumulative_extremes` is a **false-delete risk**.
- **“Drop pandas+scipy from ERE.”** Drop pandas + `scipy.signal`. **Keep `scipy.stats`** if the guard will run.
- **Deleting `weather_arb` as if that is the fence.** Fence is `LIVE_EXEC_FENCED` / `weather_live_enabled()` hard-False. Scanner can go later; constants stay.
- **`VENUE_NT_CUTOVER_ORDER` as a cut.** Harmless documentation. Not profit.
- **`kalshi_alert` / `kalshi_audit` / `*_run.py` / `intel_sentiment` / `quant` / `scanner` / `dynamic_universe` as unused.** They are live crontab strings. Deleting them is an outage, not a cleanup.
- **“Installer will 404” as current live behavior.** Live does **not** currently announce the three DEPRECATED scanners. The 404 is a **future** `install_cron.sh` run. Do not treat it as today’s Signal lie.
- **Line-count as the north star.** Under the fence, the P&L leaks are ERE no-op, same-minute sports+allocator, and status lies — not 5613 lines of bak files.

---

## 4. Ranked Top 5 next cleanup slices (if Burton says go)

Profit/ops + accuracy first. No flag flips.

1. **Bind ERE.** Port `cumulative_extremes` onto `EnergyLadderCascade` (from v1 backup). Prove `stage_ere_safety` can return `multiplier < 1.0` on a synthetic cascade. Then archive delete is safe. Highest risk/P&L; no `--live`.
2. **Live crontab truth (ops).** Apply Slice 7 stagger on **live** (`allocator :20`). Do **not** run `install_cron.sh` as written. Optionally drop or relabel morning-brief EV-as-profit Signal. Quietest high-EV ops fix.
3. **One settlement book.** `settle_run` Signal from `forward_test_ledger` fee-true rows; sports JSON becomes a view. `Profit:` prefix stays (protocol). Numbers must match FTL.
4. **Remaining fill-vs-ack.** Allocator Coinbase/Alpaca `status = "EXECUTED"` on import/POST without `extract_spot_fill.complete`; `latency_arb` POST-ack `"EXECUTED"`. Messaging-only unless a bookkeeping bug blocks truth.
5. **Cash/account labels.** Reconcile `alpaca_executor.IS_LIVE` vs `ALPACA_PAPER`; kill `BTC * 70000` in `fund_pipeline`. Wrong cash → wrong size even when fenced writers stay off.

Not on this list (and not a coding-agent go): flipping `SPOT_RECONCILE_HTTP`, `REBALANCER_AUTOEXEC`, `VENUE_WRITER`, `--live`, `LIVE_CLIENT_HOOK`, adding `place_market_order`.

**Honest disagreement worth keeping:** I would **not** start with archive/UNIFIED_STACK/combinatorial deletes. I would start with ERE bind + live crontab stagger. The audit’s Top 10 #1 (ERE binds) is correct; its stated *mechanism* is not.
