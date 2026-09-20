# Profit-path inventory (2026-09-19 / 0.43.0)

Honest matrix of ways Valedia can (or cannot) make money. Status is the **Build tree** (`workspace-v2`) unless noted. Live runtime (`~/.openclaw/workspace`) is the sole live-risk authority; Kalshi-only stage is documented in `STAGED-ON-KALSHI.md`.

Labels: **LIVE** = may POST orders on this tree’s writer · **DRY** = code can size but cron/default does not POST · **SHADOW** = Intents/proposals never-dispatchable · **FENCED** = live exec refused by flag/name · **MISSING** = not wired.

This lane does **not** unfence weather/oil, does **not** invent live shorts, does **not** flip Poly/CB/Alpaca.

## Matrix

| Path | Module(s) | Status | Venue | Why fenced / why not live | Next unlock (Burton) |
|------|-----------|--------|-------|---------------------------|----------------------|
| Kalshi sports | `sports_engine.py`, `kalshi_executor.py` (`SPORTS`), `master_allocator` | **LIVE** | Kalshi | Writer `valedia`. Create/Cancel Order V2 (`0.43.0`). Fill-true GET. Halt may stay off. | Kalshi deployable is per-venue (0.42.0); CB/Alpaca writer none must not zero it. |
| CRYPTO_BRACKET | `intel_opportunity_gatherer` (shadow tag); Kalshi `SERIES_CRYPTO` `KXBTC`/`KXETH` via executor as CONVICTION_* | **LIVE** (Kalshi scan) / **SHADOW** (intel JSON) | Kalshi | FTL category exists; allocator executes Kalshi types, not the intel-shadow name. | Keep Kalshi-only. Do not route intel shadows to POST. |
| WEATHER_ARB | `weather_arb.py`, `master_allocator.gather_weather_opportunities` → `[]`, `LIVE_EXEC_FENCED` | **FENCED** | Kalshi | Slice 8: `execute_weather_trades` forces DRY, never `create_order`. `WEATHER_LIVE` False. | Evaluation/sizing if pause TTL expires. **Do not** live unfence. |
| OIL_BRACKET | `LIVE_EXEC_FENCED` + `LIVE_EXEC_FENCED_SERIES` (`KXWTI`/`KXOIL`/`WTI`) | **FENCED** (FIXED 0.42.0) | Kalshi | Name **or** ticker family blocks live exec (`SKIP_LIVE_FENCE`) even if scanner tags `NO_HARVEST`. Report may still list as FENCED. | Stay fenced. **Do not** live unfence. |
| latency_arb | `latency_arb.py`, `arb_run.py` | **DRY** | Kalshi | Cron wrapper `mode='dry-run'`. `execute_arb` can POST if called execute. Intel gatherer copies **candidates** to shadow only. | Burton go to execute; fill-true already UNRECONCILED-until-GET. |
| combinatorial_arb | `combinatorial_arb.py` | **MISSING** (detect-only) | Kalshi | `scan()` / mutual-exclusion. **Not** in `gather_all_opportunities` or execute. | Wire candidates → shadow first, then Burton. |
| Coinbase crypto | `coinbase_trader.py`, allocator `platform==coinbase` | **FENCED** (stage) | Coinbase | Kalshi-only stage: live docs say writer `none` + SKIP_WRITER. **Build** `VENUE_WRITER["coinbase"]` still `valedia` — this lane does **not** restore/flip. `place_market_order` still missing → FAILED. | Burton writer restore after Kalshi cash works. |
| Alpaca equity | `alpaca_executor.py`, allocator `platform==alpaca` | **FENCED** (stage) | Alpaca | Same Kalshi-only stage. Build writer still `valedia`. Paper flag separate. | Burton. No scalper `alp_buy`. |
| Poly US (DCM) | `polymarket_us_client.py`, `poly_learn.py`, `poly_us_smoke.py` | **DRY/SHADOW** | Polymarket US | `POLYMARKET_US_LIVE` False. Allocator execute → `DRY/SHADOW`, never create. Preview ok. | Ladder in `POLY-LEARN.md`. Burton LIVE=1 for qty=1 **without** writer flip. |
| Poly entertainment | `polymarket_entertainment.py`, `nautilus_shadow_venues.emit_polymarket_entertainment_shadow` | **SHADOW** | Polymarket Int'l CLOB | Writer `none`. `LIVE_CLIENT_HOOK` None. Never dispatchable. | Stay shadow. US classroom is the learn path. |
| Hyperliquid shorts / desk | `nautilus_shadow_venues.emit_hl_short_shadow`, `hl_public_info.py`, `desk_roles.py` | **SHADOW** | Hyperliquid | Writer `none`. Public `/info` HTTP off unless `HL_PUBLIC_HTTP`. No live shorts. | Public info opt-in, then Burton writer. Do not invent live shorts. |
| Fortress intel | `intel_fetch_fallback.py` | **FENCED** | n/a (intel) | `FORTRESS_INTEL_FALLBACK` False. Engine PARKED. Classify only. | Flag + parked engine later. No venue CDP. |
| Arena paper stress | `arena_stress.py`, `adversarial_paper_fight.py` | **SHADOW** (paper) | n/a | `ARENA_MODE=paper`. LN/faucet refused. No crontab. | Stay paper until Burton Arena LN. |
| Rebalancer | `rebalancer.py` | **SHADOW** | Coinbase (target) | `REBALANCER_AUTOEXEC` False. Propose only. | Burton AUTOEXEC after CB writer is intentional. |
| Kalshi scanner | `kalshi_scanner.py`, `scanner_run.py` → `scanner_results.json` | **DRY** → allocator gather | Kalshi | SOURCE 5 if scan <12h. Execute is Kalshi writer. | Already on. |
| Quant engine | `quant_engine.py` | **DRY** (SCAN-ONLY) | Kalshi / Poly notes | Does not place. Poly “built but disabled”. | Keep scan. |
| Int'l Poly CLOB arb | `polymarket_client.find_arb_vs_kalshi`, `polymarket_order_body` | **SHADOW / FENCED** | Polymarket Int'l | Writer none. Hook None. | Not this stage. |
| DCA / strategy_modules | `dca_module.py`, `strategy_modules.py` | **MISSING** (allocator) | Coinbase/Alpaca | Not in `gather_all_opportunities`. Absolute live-workspace paths. | Ignore or rewrite; do not auto-buy. |
| Whale / insider / asi_scan | `whale_copier.py`, `insider_mirror.py`, `asi_scan.py` | **DRY / SHADOW** | mixed | Not allocator execute. | Ears on; no live. |
| Treasury auto | `treasury_status.py` | **FENCED** | bank/venues | `VALEDIA_TREASURY_AUTO` False; auto refused even if env=1. Report-only. | Human fund. No ACH from here. |
| Nautilus live / NT | `valedia_config.NAUTILUS_*`, `neural_trader_fence.py` | **FENCED** | sidecar | `NAUTILUS_LIVE` False. NT research pin only. | Stay fenced. |
| Kill switch | `VALEDIA_TRADING_HALT` | **LIVE-gate** (default off) | all writers | Halt may stay off. GET/preview/dry allowed. Ears gather. | Manual `on` if needed. |
| Crypto paper desk | `crypto_paper.py`, `crypto_paper_run.py` | **SHADOW** (paper sim) | none | Fixture or public klines; simulated mid±slip fills; would-have fee-true PnL. No wallets, no `create_order`. | Burton go later for real wallets. Do not restore CB/Alpaca writers. |
| Kalshi EV preflight / maker-first / fill ledger | `kalshi_profit.py` | **SHADOW** (annotate) | Kalshi | `KALSHI_EV_PREFLIGHT_GATE` / `KALSHI_MAKER_FIRST` / `KALSHI_FILL_LEDGER_DEMOTE` default False. Annotate suggest/rest/size. Persist `data/kalshi-fill-quality.json`. | Burton flag flip. Do not auto-flip `KALSHI_FEE_MAP_GATE`. |

## Not wired to allocator gather / execute

- `combinatorial_arb.scan` — detect only.
- `poly_learn` proposals — hooked in `run()` as DRY/SHADOW, **excluded** from live `gather_all_opportunities`.
- Poly entertainment / HL shorts — shadow Intents in `run()`, never execute.
- latency_arb candidates — intel gatherer shadow; cron dry.
- Weather gather returns `[]` (HTTP-off). Oil not a named gather source.
- Coinbase/Alpaca execute still in Build allocator; Kalshi-only **live** stage must keep them skipped. This lane does not restore writers.
- DCA, whale, quant, fortress, Arena — not `execute_allocations` platforms.

## Discovering new edges (without auto-unfencing losers)

Already in tree: repo-adaptation charter (`VALEDIA-REPO-ADAPTATION.md`), persona lenses (advisory), MiroFish + neural enrich (caps, `AUTO_RAISE` False), scanner JSON, intel gatherer, Arena paper fights.

Needed to find **new** edges without promoting losers:

1. Keep **ears on** (MiroFish/data/scans) even when halt or live-exec fence is set.
2. New scanners write **candidates** → shadow / POLY-LEARN JSON / FTL paper — never `create_order`.
3. Promote only via Burton flip + fee-true FTL sample. Pause is TTL, not a grave; **unpause ≠ live writer**.
4. Do not auto-unfence `WEATHER_ARB` / `OIL_BRACKET` / Poly LIVE / CB/Alpaca because a scan looks good.
5. Continuous charter = dossiers + shadow first (Arc H pattern). Adaptation ≠ `--live`.
