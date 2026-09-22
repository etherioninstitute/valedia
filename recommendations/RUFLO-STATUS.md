# Ruflo status board (Claude Code check-in)
Updated: 2026-09-21 PT — **FOMO-RADAR-DOSSIER SHIPPED**. Version `0.55.0` tip `8e4c86b` tag `valedia-v0.55.0`. Offline enrich tags for CRYPTO-PAPER / MiroFish (`wallet_conviction`, `provenance_ok`, `burst`, `exit`). Annotate only. No FOMOAPI. No browser scrape. No order path. Ponytail: `PONYTAIL-AUDIT-FOMO-RADAR.md`. Dossier: `ADAPT-DOSSIERS/fomo-robinhood-radar.md`. **GROK-STATUS: FOMO-RADAR-DOSSIER SHIPPED.** Prior `0.54.0` POLY-LOOP-FV. No `--live` / writers / SPOT GET / NAUTILUS_LIVE / LN / crontab / live Polymarket / funding / weather-oil unfence / CB-Alpaca restore / Jev enable in git. `VALEDIA_TREASURY_AUTO` stays False. `REBALANCER_AUTOEXEC` stays False. `POLYMARKET_US_LIVE` stays False. `VALEDIA_SELF_ADJUST` stays False. `MIROFISH_AUTO_RAISE` stays False. `FORTRESS_INTEL_FALLBACK` stays False. `VALEDIA_TRADING_HALT` default False. `KALSHI_FEE_MAP_GATE` stays False. `VALEDIA_JEV` stays False.

## FOMO-RADAR-DOSSIER (Grok Build executor, `valedia-v2-grok-build`)
- Dossier + `fomo_radar_enrich.py`. Fixture tags only. `stamp_paper` / `stamp_signal` do not change qty or score. `test_fomo_radar.py`.
- `POLYMARKET_US_LIVE` stays False. No `VENUE_WRITER` edit. No cron. No wallets.
- **Next = Burton. Do not size from radar conviction. Do not auto-flip POLYMARKET_US_LIVE.**

## POLY-LOOP-FV (Grok Build executor, `valedia-v2-grok-build`)
- Resolution gate kept. Numeric FV from blotter / `fairValue|fv|p_yes|modeled_prob` / fuzzy cache / EMA series / 50/50 mid-anchored tag_prior (classified or NWS/MLB/Fed label). Ledger `fv=cache|modeled|tag_prior|market|override`.
- `test_poly_loop.py`. LIVE stays False. Writer `none`. No crontab.
- **Next = Burton. Do not auto-flip POLYMARKET_US_LIVE.**

## POLY-LOOP-RESOLUTION (Grok Build executor, `valedia-v2-grok-build`)
- Explicit `resolutionSource`/`resolvedBy` first. Else extract NWS/AP/Fed/… or URL host from `description`/`rules`/`event.rules`. Else `rules_text:<hash>:<snippet>` if text exists. Zero text → `no_resolution`.
- `test_poly_loop.py`. LIVE stays False. Writer `none`. No crontab.
- **Next = Burton. Do not auto-flip POLYMARKET_US_LIVE.**

## POLY-LOOP-UNIVERSE (Grok Build executor, `valedia-v2-grok-build`)
- Pull `active=true&closed=false&archived=false` (no `endDateMin` — it breaks `closed`). Over-fetch, drop expired / present-zero-liq. Unwrap `{market:…}`. US/Gamma aliases for end/liq/resolution. FV from market/tags or MiroFish/persona cache; named resolution source required; skip `no_fv` otherwise. `skip_reasons` on `last_run.json`.
- `test_poly_loop.py`. LIVE stays False. Writer `none`. No crontab.
- **Next = Burton. Do not auto-flip POLYMARKET_US_LIVE.**

## POLY-LOOP (Grok Build executor, `valedia-v2-grok-build`)
- Paper desk on POLY-LEARN. Folded into 0.52.0 commit. 8pt edge, 3% flatten, half-Kelly 6% cap, max 5 open, 40% DD stop + post-mortem (no category flip).
- `test_poly_loop.py`. LIVE stays False. Writer `none`. No crontab.
- **Next = Burton. Do not auto-flip POLYMARKET_US_LIVE.**

## POLY-LEARN (Grok Build executor, `valedia-v2-grok-build`)
- US DCM market-by-slug + BBO/book helpers. `poly_learn` proposals labeled DRY/SHADOW. Allocator execute never `create_order`. Preview still works. Cheap MiroFish/persona tags from cache.
- `test_poly_learn.py` PASS. `POLYMARKET_US_LIVE` False. Writer polymarket `none`.
- **Next = Burton. Kalshi stays ON. Poly live still STOP.**

## OPENTERMINAL-VALEDIA (Grok Build executor, `valedia-v2-grok-build`)
- HIG HTML glance on existing GET server :8765 (not OT :3000 / API :4010 / LiteLLM :4000). JSON `/status` + FTL vs broker labels; halt shown; ears always gathering.
- No send/buy/create_order buttons. No OpenTerminal Next fork. No cron. `test_openterminal_valedia.py` PASS.
- **Next = staged ON. STOP. Do not auto-flip live.**

## KILL-SWITCH (Grok Build executor, `valedia-v2-grok-build`)
- Master halt: env/file `VALEDIA_TRADING_HALT`. Writers (Kalshi create/sell, Poly US create/cancel, Coinbase/Alpaca market buy/sell) + allocator/rebalancer/maker `_place` refuse with `HaltedError`. GET/preview/dry-run stay allowed.
- Manual OFF: `unset` / `=0` / `valedia_trading_halt.py off`. Self-heal report-only (`send: false` ping file). `test_kill_switch.py` PASS.
- Shipped 0.39.0. OpenTerminal glance is 0.40.0. **Next = staged ON (Burton). Do not auto-flip live.**

## FULL-SMOKE (Grok Build executor, `valedia-v2-grok-build`)
- Minutes-scale whole-brain smoke. One CLI wraps fence stamps, treasury_status, mirofish_smoke fixture (+ live-lite), Kalshi balance GET, Poly US whoami_retail/preview (create blocked), shadow queue + adversarial import, cockpit `panel()`, pnl_tracker broker≠FTL.
- Live run: all 8 PASS; cockpit GET SKIP (`:8765` down). `test_valedia_full_smoke.py` PASS offline. No cron. No live flips.
- **Next = KILL-SWITCH (parent launches). Do not auto-start here.**

## PONYTAIL-FULL (Grok Build executor, `valedia-v2-grok-build`)
- Report-only full audit after MIROFISH-SMOKE. `recommendations/PONYTAIL-AUDIT-VALEDIA-FULL.md`.
- Slice 20 ~−10200 still unpaid (archive 5613, ERE FAIL_CLOSED missing `cumulative_extremes`, DEPRECATED installer 404s). Post-S20 ~3775 mostly do-not-cut.
- No tiny cuts, no version bump, no live flags. Tests: mirofish_smoke / treasury / poly_us / h3 / slice23 PASS.
- **Next = VALEDIA-FULL-SMOKE (parent launches). Do not auto-start here.**

## MIROFISH-SMOKE (Grok Build executor, `valedia-v2-grok-build`)
- Minutes-scale wiring gate. `mirofish_smoke.py`: fixture always (synthetic seed→extractor→neural tag→mocked fee-true ledger→gatherer). Live-lite one Ollama/LMS ping ≤90s; SKIP if down.
- Measured: fixture PASS 6ms; live-lite PASS 7.4s `:11435` `qwen3-8b-mf:latest`; wall 7.44s / 300s budget.
- `test_mirofish_smoke.py` PASS offline. Ponytail: `PONYTAIL-AUDIT-MIROFISH-SMOKE.md`. Overnight swarm still the corpus. **Do not flip live.**

## TREASURY-1 (Grok Build executor, `valedia-v2-grok-build`)
- Report-only cash vs floors. Guide: `VALEDIA-TREASURY-FUNDING.md`. Script: `treasury_status.py` → `models/treasury_status.json`. Never ACH/wire/withdraw.
- Auto top-up refused (`attempt_auto_topup` raises even if env=1). Webhook stub unused. Rebalancer shadow-only.
- `test_treasury.py` PASS. Ponytail: `PONYTAIL-AUDIT-TREASURY-1.md`. Do not fund anything.

## POLY-US-2 (Grok Build executor, `valedia-v2-grok-build`)
- Retail `whoami()` = balances (`whoami_retail`). Institutional `GET /v1/whoami` not called on `api.polymarket.us`.
- Preview dry-run: `{request: order}` on `POST /v1/order/preview`. `poly_us_smoke.py` LIVE=0; create raises `LiveWriteBlocked`.
- `test_poly_us.py` PASS. Ponytail: `PONYTAIL-AUDIT-POLY-US-2.md`. Cutover **do not flip**.

## POLY-US (Grok Build executor, `valedia-v2-grok-build`)
- US DCM client `polymarket_us_client.py` (not Int'l CLOB). Env `POLYMARKET_KEY_ID`/`POLYMARKET_SECRET_KEY`. create/cancel gated. Nav tag helpers. Fill-true GET-by-id. `test_poly_us.py` PASS. Ponytail: `PONYTAIL-AUDIT-POLY-US.md`.
- Cutover: `POLYMARKET-US-CUTOVER.md` — **do not flip** `VENUE_WRITER["polymarket"]` / `LIVE_CLIENT_HOOK` / `POLYMARKET_US_LIVE`.
- Fence held. Do not enable live Polymarket / NT live / Arena funding.

## H8 + POLY-E (Grok Build executor, `valedia-v2-grok-build`)
- H8: `intel_fetch_fallback.py` failure-gated `{ok,empty,blocked,waf}` after 403/429/empty/WAF. Engine PARKED. `test_h8.py` PASS. Ponytail: `PONYTAIL-AUDIT-H8.md`.
- POLY-E: Oscars/Culture + Sports/Politics/Crypto/Weather/Tech/Econ path. Shadow only. Fill-true GET-by-id. Cutover doc does not flip. `test_poly_e.py` PASS. Ponytail: `PONYTAIL-AUDIT-POLY-E.md`.
- Fence held. Do not enable live Polymarket / NT live / Arena funding.

---

Updated: 2026-09-18 PT — **H7 SHIPPED.** Version `0.32.0` tag `valedia-v0.32.0` tip `d0a29ab`. Prior H6 `0.31.0`. **STOP for Burton.** No `--live` / writers / SPOT GET / NAUTILUS_LIVE / LN / crontab / H8. `VALEDIA_SELF_ADJUST` stays False. `MIROFISH_AUTO_RAISE` stays False.

## H7 (Grok Build executor, `valedia-v2-grok-build`)
- `arena_stress.py` wraps `adversarial_paper_fight.py` (not rebuilt). Arena primes + fee-true FTL score. JSON `models/arena_stress_*.json`.
- Risk veto records disagreement; `intents=[]`. `ARENA_MODE=paper`. LN/faucet refused. Not crontab-installed.
- `test_h7.py` PASS. Ponytail: `PONYTAIL-AUDIT-H7.md`. Fence held. Do not start H8 / Arena funding / NT live.

---

Updated: 2026-09-18 PT — **H6 SHIPPED.** Version `0.31.0` tag `valedia-v0.31.0` tip `22a6cff`. Prior H5+DYNAMIC-BAND `0.30.0`. **STOP for Burton.** No `--live` / writers / SPOT GET / NAUTILUS_LIVE / LN / crontab / H7. `VALEDIA_SELF_ADJUST` stays False.

## H6 (Grok Build executor, `valedia-v2-grok-build`)
- Shadow OMS event model: `nautilus_shadow_events.py` (OrderStatus + CommandFailure rewrite, no `nautilus_trader`). submitted ≠ fill. Ambiguous ≠ denied.
- Fill sink: inferred fills cannot be `source=live`; trade_id/fee on FTL. One Kelly / one FTL.
- Parity: report-only event timeline + fail-closed side/size/venue/leverage/dispatchable. `test_h6.py` PASS. Ponytail: `PONYTAIL-AUDIT-H6.md`.
- Fence held. Do not start H7 / Arena funding / NT live.

---

Updated: 2026-09-18 PT — **H5 + DYNAMIC-BAND SHIPPED.** Version `0.30.0` tag `valedia-v0.30.0` tip `a7801d3`. Prior H4 `0.29.0`. **STOP for Burton.** No `--live` / writers / SPOT GET / NAUTILUS_LIVE / LN / crontab. `VALEDIA_SELF_ADJUST` stays False.

## H5 / DYNAMIC-BAND (Grok Build executor, `valedia-v2-grok-build`)
- H5: `desk_roles.py` labels on shadow Intents; `hl_public_info.py` public `/info` snapshot; gatherer `source_feed=hl_public`. No Exchange. `test_h5.py` PASS. Ponytail: `PONYTAIL-AUDIT-H5.md`.
- DYNAMIC-BAND: `VALEDIA_SELF_ADJUST=False`; band math in `mirofish_accuracy_ledger`. Seatbelt 10/15 until flip. `test_dynamic_band.py` PASS. Ponytail: `PONYTAIL-AUDIT-DYNAMIC-BAND.md`.
- Fence held. Do not start H6 / Arena funding / NT live.

---

Updated: 2026-09-18 PT — **H3+H4 SHIPPED.** H3 `0.28.0` tag `valedia-v0.28.0` tip `57bddba`. H4 `0.29.0` tag `valedia-v0.29.0` tip `16acf13`. CBM-SMOKE PASS. **STOP for Burton.** No AUTO_RAISE / writers / LN / crontab.

## H3 / H4 (Grok Build executor, `valedia-v2-grok-build`)
- H3: `resolve_kalshi_fill` — POST-ack is not a fill; GET `/portfolio/fills` may upgrade when `order_id` matches. sports / allocator / executor aligned. `test_h3.py` PASS. Ponytail: `PONYTAIL-AUDIT-H3.md`.
- H4: `valedia_cockpit.py` GET `/status` (POST 405). Reads FTL + shadow queue + MiroFish/neural/persona JSON. No OMS. `test_h4.py` PASS. Ponytail: `PONYTAIL-AUDIT-H4.md`.
- Fence held. No `--live` / VENUE_WRITER / NAUTILUS_LIVE / SPOT_RECONCILE_HTTP / REBALANCER_AUTOEXEC / MIROFISH_AUTO_RAISE.

---

Updated: 2026-09-18 PT — **Paper fights SHIPPED.** Version `0.27.0` tip `448410f` tag `valedia-v0.27.0`. H2 personas remain `0.26.0` (`ba71ab6`). **STOP for Burton.** No AUTO_RAISE / writers / LN / crontab.

## Paper fights (Grok Build executor, `valedia-v2-grok-build`)
- `adversarial_paper_fight.py` reuses H2 Buffett; momentum/kernel; MiroFish-enriched; risk veto. `adversarial_run.py` not crontab-installed.
- JSON `models/adversarial_round_*.json`. Tests: `test_adversarial_paper_fight.py` PASS. Ponytail: `PONYTAIL-AUDIT-ADV-FIGHT.md`.
- Arena LN left. Fight pattern only. Fence held.

---

Updated: 2026-09-18 PT — **Arc H1+H2 SHIPPED.** Version `0.26.0` tip `ba71ab6` tag `valedia-v0.26.0` on Build. **STOP for Burton.** H3 Kalshi fill-true / H4 OpenTerminal next named go. Dynamic band + master flip still later. No AUTO_RAISE / writers / LN.

## H1 / H2 (Grok Build executor, `valedia-v2-grok-build`)
- Dossiers: `recommendations/ADAPT-DOSSIERS/` (ai-fund, OpenTerminal, hypergrok, nautilus, kalshi-agent, lf-game-theory, fortress).
- One-brain map: `recommendations/VALEDIA-SEAMLESS-ARCHITECTURE.md`.
- H2: `persona_lenses.py` Buffett/Lynch/Hayes → MiroFish seed/extractor attach. Scores/bias/Kelly/weight unchanged. `test_h2.py` PASS. Ponytail: `PONYTAIL-AUDIT-H2.md`.
- Fence held. No `--live` / VENUE_WRITER / NAUTILUS_LIVE / SPOT_RECONCILE_HTTP / REBALANCER_AUTOEXEC / MIROFISH_AUTO_RAISE.

---

Updated: 2026-09-18 PT — **Arc G NT-2 SHIPPED.** Version `0.25.0` tip `71a39f9` tag `valedia-v0.25.0`. **STOP for Burton.** Dynamic band + master flip = later named slice after approval. NT-3 OpenTerminal not this stop.

## NT-2 (Grok Build executor, `valedia-v2-grok-build`)
- Seed + extractor carry NT-1 neural context when present (`neural` / `neural_enriched`). Scores/bias/weight unchanged.
- Accuracy ledger tags `neural_enriched` on predictions and fee-true results. `n_scored_enriched` / `n_scored_plain` in stats only.
- `MIROFISH_AUTO_RAISE=False`. Caps 0.10 / 0.15. No `VALEDIA_SELF_ADJUST` implementation (comment pointer only).
- Tests: `test_nt2.py` PASS. Fence held. No `--live` / VENUE_WRITER / NAUTILUS_LIVE / SPOT_RECONCILE_HTTP / REBALANCER_AUTOEXEC.

## NT-0 / NT-1 (Grok Build executor, `valedia-v2-grok-build`)
- NT-0 `6f594d1`: local `neural-trader@2.8.11` `--ignore-scripts`; research allowlist only; live/broker unwired (cron/config/MCP). `test_nt0.py` PASS.
- NT-1: `mirofish_neural_enrich.py` → MiroFish-adjacent JSON; no fusion weight / AUTO_RAISE / Intents. `test_nt1.py` PASS.
- Fence held. No `--live` venue, no VENUE_WRITER / NAUTILUS_LIVE / SPOT_RECONCILE_HTTP / REBALANCER_AUTOEXEC flips.

---

Updated: 2026-09-18 PT — **Grok Build Slices 22+23 SHIPPED.** Version `0.23.0` tag `valedia-v0.23.0`. **STOP for Burton.**

## Slice 22 (Grok Build executor, `valedia-v2-grok-build`)
- FTL `Profit:` body; sports JSON view-only; UNRECONCILED excluded. Allocator spot UNRECONCILED unless fill complete. Alpaca `ALPACA_PAPER` one flag. latency_arb execute → UNRECONCILED. Crontab patch documented, not installed. ERE still FAIL_CLOSED.
- Tests: `test_slice22.py` PASS. Fence held. No `install_cron.sh`.

## Slice 21 (Grok Build executor, `valedia-v2-grok-build`)
- Part A: `recommendations/PONYTAIL-AUDIT-GROK-RESPONSE.md` — ERE no-op cause is missing `cumulative_extremes`, not missing class names. Live crontab ≠ installer.
- Part B: `recommendations/VALEDIA-TRADE-STATUS-ACCURACY.md` + v2 label fixes (ERE `FAIL_CLOSED`, brain ACKED, settle `not FTL`, quant SCAN-ONLY). `test_slice21.py` PASS.
- Fence held. Live crontab/scripts not rewritten. Next = STOP or Slice 22 (FTL Signal + live stagger).

---

Updated: 2026-09-17 PT — **Grok Build Slice 19 SHIPPED** (cutover prep ONLY). **STOP for Burton.**

## Slice 19 (Grok Build executor, `valedia-v2-grok-build`)
- Checklist: `recommendations/VALEDIA-LIVE-CUTOVER-PREP.md`. No NT install, no NAUTILUS_LIVE, no VENUE_WRITER, no HL/PM live.
- Next: STOP for Burton.

---

Updated: 2026-09-17 PT — **Grok Build Slice 18 SHIPPED** (MiroFish weight rec, cap ≤10–15%)

## Slice 18 (Grok Build executor, `valedia-v2-grok-build`)
- `recommend_weight()` → local `mirofish_weight_recommendation.json`. Proven rec ≤15%, unproven 10%. Does not apply. AUTO_RAISE stays False.
- Learning re-enable: pause/probation, no permanent blacklist. Wired into `mirofish_ledger_run.py`.
- Tests: `test_slice18.py` PASS, no network. No HTTP Intent / --live / GET HTTP / NT.

---

Updated: 2026-09-17 PT — **Grok Build Slice 17 SHIPPED** (SPOT_RECONCILE_HTTP checklist; still False)

## Slice 17 (Grok Build executor, `valedia-v2-grok-build`)
- Checklist only: `recommendations/SPOT-RECONCILE-HTTP-FLIP.md`. Flag remains False. `.env` not written. No GET HTTP from this lane.

---

Updated: 2026-09-17 PT — **Grok Build Slice 16 SHIPPED** (shadow parity ops, report-only)

## Slice 16 (Grok Build executor, `valedia-v2-grok-build`)
- `parity_run.py` + `nautilus_shadow_parity.py --out` default `models/shadow_parity_report.json`. Window `7d` / `SHADOW_PARITY_WINDOW`.
- Report-only: `ok` / `diffs` / `dangerous` / `cutover_ready`. Cron wrapper always exits 0. Live crontab not rewritten.
- Tests: `test_slice16.py` PASS, no network. No --live / VENUE_WRITER / AUTOEXEC / GET HTTP / NT.

---

Updated: 2026-09-17 PT — **Grok Build Slice 15 SHIPPED** (fenced merge/deploy into live)

## Slice 15 (Grok Build executor, `valedia-v2-grok-build`)
- Live `~/.openclaw/workspace` received Slices 1–14 fenced code (content-diff rsync, no `--delete`).
- Backup `~/.openclaw/backups/live-scripts-pre-s15-20260917_224401.tar.gz`. Tags: live `pre-arc-f-s15` / `fenced-deploy-s15`.
- `.env` / models / crontab installer **not** touched. Flags still off. Smoke PASS.
- Doc: `recommendations/VALEDIA-DEPLOY-FENCED.md`.

---

Updated: 2026-09-17 PT — **Grok Build Slice 14 SHIPPED** (Burton-gated spot GET, default OFF)

## Slice 14 (Grok Build executor, `valedia-v2-grok-build` `5996181`)
- Valedia-runtime getter: `reconcile_run.get_spot_order_getter()` → `live_spot_order_getter` only when `SPOT_RECONCILE_HTTP` is on. Default/cron = mock, no HTTP.
- Single flip (this lane never True): `export SPOT_RECONCILE_HTTP=1` or `valedia_config.SPOT_RECONCILE_HTTP = True`. Env wins. No extra `set_spot_order_getter`.
- Tests: `set_spot_http_transport` mocked path only; module inject never falls through to live `get_order`. `test_slice14.py` PASS, no network. No --live / VENUE_WRITER / AUTOEXEC / NT.

---

Updated: 2026-09-17 PT — **Grok Build Slice 13 SHIPPED** (MiroFish accuracy closed loop)

## Slice 13 (Grok Build executor, `valedia-v2-grok-build` `f036beb`)
- Fee-true outcomes from `forward_test_ledger` settlements → `mirofish_accuracy_ledger.jsonl`. Paper/sandbox/backfill/shadow never count. No yfinance/HTTP.
- Weight stays ≤ `MIROFISH_UNPROVEN_CAP` (0.10) until proven (`>` 55% directional on ≥20 fee-true results). `MIROFISH_AUTO_RAISE=False` — no auto-raise past cap. Proven cap 15% only if Burton flips AUTO_RAISE.
- `fusion_weights_for` / intel gather+blend read `effective_mirofish_weight()` + `mirofish_accuracy_summary.json`.
- Tests: `test_slice13.py` PASS, no network. No --live / VENUE_WRITER / AUTOEXEC / GET HTTP / NT.

---

Updated: 2026-09-17 PT — **Grok Build Slice 12 SHIPPED** (shadow parity harness, 7d compare.ok)

## Slice 12 (Grok Build executor, `valedia-v2-grok-build` `d57f7ed`)
- `nautilus_shadow_parity.py`: mockable `7d` window. Compares shadow Intents vs Valedia POST bodies for brain Coinbase + HL short + Polymarket arb + intel Coinbase. Records `compare.ok` / diffs. Allocator Coinbase is not cutover evidence.
- Fail-closed on side / instrument / size / venue / signed / live hook / leverage / dispatchable / live_flag / writer=nautilus. `cutover_ready` needs 7 calendar days of matching brain rows and does **not** flip flags.
- Tests: `test_slice12.py` PASS, no network. No --live / VENUE_WRITER / AUTOEXEC / GET HTTP / NT.

---

Updated: 2026-09-17 PT — **Grok Build Slice 9 SHIPPED** (pnl_tracker report-only + net pause + learning re-enable)

## Slice 9 (Grok Build executor, `valedia-v2-grok-build` `a0f3572`)
- Blacklist is a pause with TTL (7d) + probation, not a grave. Legacy `blacklisted[]` with no `until` is expired on read.
- Pause on fee-true `category_stats` net (`n>=4`, `net<0`). WR after 3 trades is report-only and cannot kill a fat-tail winner.
- Evidence `net>=0` unpauses immediately. Allocator sizes unpaused names; execute still `SKIP_LIVE_FENCE` for weather/oil (Slice 8 create_order / alp_buy stay dead).
- `should_trade_category` reason is `paused until … (not permanent)`. Tests: `test_slice9.py` PASS, no network. No --live / AUTOEXEC / GET HTTP / NT.

---

Updated: 2026-09-17 PT — **Grok Build Slice 8 SHIPPED** (one fusion + one fee table; scalper/weather dead; REBALANCER_AUTOEXEC=False)

## Slice 8 (Grok Build executor, `valedia-v2-grok-build` `02987c3`)
- Canonical live fusion: `valedia_config.FUSION_WEIGHTS` (kernel-first) → `unified_brain.stage_fusion`. `signal_fusion.LIVE_PATH=False` (report scanner only).
- Canonical fees: `fee_engine.STATIC_FEES`. Brain `FEES` dict deleted. `stage_fee_gate` uses `roundtrip_cost(..., use_static=True)` so Coinbase spread is not silently dropped. `stage_kelly` delegates to `KellySizer`.
- MicroScalper live buys dead (`scalper_place_buy` never calls `alp_buy`). Allocator hard-skips `WEATHER_ARB`/`OIL_BRACKET` even if blacklist file missing. `weather_arb.execute_weather_trades` never `create_order`; dry forced.
- `REBALANCER_AUTOEXEC=False`. Shadow/propose only. Flag+NT still cannot dispatch. This lane did not set True.
- Tests: `test_slice8.py` + prior slices PASS. Drift detector 10 PASS · 0 WARN · 0 FAIL. No network, no --live, no NT cutover.

---

Updated: 2026-09-17 PT — **Grok Build Slice 7 SHIPPED** (spot GET OFF + per-strategy cap + cron stagger)

## Slice 7 (Grok Build executor, `valedia-v2-grok-build`)
- Spot GET helpers exist (`spot_order_get.py`, Coinbase/Alpaca `get_order`) behind `SPOT_RECONCILE_HTTP=False`. Default cron = mock/no HTTP. Burton flip later: `export SPOT_RECONCILE_HTTP=1`.
- Per-strategy cap from `category_stats` via `category_track_record_mult` → allocator + sports + KellySizer. Paper/unreconciled/backfill cannot lift.
- Worktree crons staggered: sports `:00` / 2h, allocator `:20` / 4h. **Live crontab not rewritten by this slice** (still collides until merge/install).
- Fence intact: no --live, no orders, no NT, no GET HTTP on.

---

Updated: 2026-09-13 PT — **SPRINT D: audit ✅ + B1 shipped to worktree (STOP for review)**

## Role
- **Ruflo = main coder / auditor.** Valedia runtime = sole live risk authority.
- Ruflo codes/audits; never `--live`, orders, or venue keys.

## Where the B1 code lives (IMPORTANT — not live yet)
- All B1 edits are in worktree branch `claude/valedia-continuous-improvement-c759ee` at
  `.claude/worktrees/valedia-trading-improvement-b7d855/`. **The live cron runs the MAIN
  checkout** `~/.openclaw/workspace/scripts/integrations/sports_engine.py` — unchanged.
  Nothing here affects live trading until Burton merges. Rollback = don't merge / `git checkout`.

## Sprint D — Step 1: Audit-verify (DONE, evidence-bound)
- **HOLDS** — live Kalshi authority = `master_allocator.execute_allocations → record_entry`
  (codebase-memory `trace_path`, valedia-scripts graph). Gate `:391`, cap `:296`, fees present.
- **HOLDS** — `kalshi_executor.run` **INERT**: trace_path inbound = self `__main__` + `kalshi_trade` only; absent from live crontab. Do not rewire.
- **LIVE PROOF (actual `crontab -l`):** `0 */2 * * * sports_run.py` (sports every 2h),
  `0 */4 * * * allocator_run.py` (allocator every 4h), `30 * * * * settle_run.py` (hourly),
  `0 */6 * * * reconcile_run.py` (ledger reconcile every 6h).
- Drift detector `valedia_consistency_check.py` = **7 PASS · 0 WARN · 0 FAIL** (before + after B1).

## Sprint D — Step 2: B1 `sports_engine.py` hardening (code SHIPPED to worktree)
Coder = Claude Code; **tester** (ruflo-testgen:tester) + **reviewer** (ruflo-core:reviewer) agents ran under the open swarm.
- ✅ Fee-true sizing via canonical `ftl.kalshi_fee` (`profit_if_win` net of fee; `fee` recorded); loss P&L now `-(cost+fee)`.
- ✅ `record_entry(source="live")` on EXECUTED fills → the ONE fee-inclusive ledger (mirrors master_allocator).
- ✅ `should_trade_category("SPORTS")` gate before execute (fail-open, correct placement — a block prevents `create_order`).
- ✅ DRY_RUN never persisted and never settled as real.
- ✅ `record_entry` failures now log to stderr (was silent `pass`) — from review.
- Tests: `test_sports_engine.py` + `test_sports_engine_b1_extra.py` (7 behaviors, adversarial) = **PASS**; `test_category_gate.py` regression = PASS.

## ⚠️ HONEST CAVEAT — B1 gate is wired but currently INERT
- Live `forward_test_ledger.jsonl` = 76 recs, **100% `backfill:pnl_tracker`, 0 live-sourced**; SPORTS rows dated 2026-03-23 (~174d, outside the 45d window). So `should_trade_category("SPORTS")` returns "unproven/thin → not blocked" until **≥4 live SPORTS trades settle within 45 days** (this diff is the first code to feed them; `reconcile_run.py` @6h will settle them).
- Meanwhile sports is a **real current loser: 69.4% win, net −$42.70 / 232 bets** (in `sports_engine_log.json`) — a number the ftl-based gate does not yet see.
- Reviewer initially claimed reconcile "not scheduled" — **corrected**: it IS scheduled (6h) in the live crontab; reviewer only checked the stale installer.

## Backlog from B1 review (each = its own swarm slice; NOT done)
1. **[HIGH] acceptance ≠ fill** — `create_order` return ignored; a resting/unfilled order is marked EXECUTED, recorded, and inflates `open_position_count` → throttles Kelly sizing for ALL strategies. Pre-existing in master_allocator too. Verify fill before EXECUTED/record.
2. **[HIGH] gate coverage gap** — `master_allocator`/`kalshi_executor` trade the SAME sports series under category `"SPORTS_TONIGHT"`, not `"SPORTS"`; the new gate doesn't cover the allocator path. Unify the label or add the gate there.
3. **[MED] cron collision** — sports (2h @:00) + allocator (4h @:00) fire same minute on 4h boundaries; both can see "not held" and double-order same ticker. Stagger or per-ticker lock.
4. **[repro] stale cron docs** — `install_cron.sh` + `skills/valedia/cron-schedule.md` omit `sports_run`/`allocator_run`/`reconcile_run`/`settle_run` that the live crontab actually runs.

## Next lever (B2, per Burton's standing rule = full coder+tester+reviewer swarm)
- **B2 — unify fusion weights** (`signal_fusion.py:34 WEIGHTS` vs `unified_brain` inline) → one source in `valedia_config`. (Or Burton may prioritize B1 backlog #1/#2 first — both are live-money HIGHs.)

## Log
- 2026-09-13: Sprint D. Audit-verify HOLDS (trace_path + live crontab proof). B1 sports_engine hardening shipped to worktree with tester+reviewer swarm; drift 7 PASS. Review found gate currently inert (ftl live-SPORTS data starved) + 3 backlog HIGH/MED items. **STOP for Burton/Claude review before merge or B2.** Fence intact: no --live, no orders, no keys; changes isolated to worktree.
