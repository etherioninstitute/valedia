# OIL-WEATHER-SMART-1 — Oil + weather research/shadow intelligence + self-correct plumbing

**Burton go:** 2026-09-23 ~18:16 PT — work on oil + weather; make them smarter with all Valedia parts that help; self-improve so trades are best possible.  
**North star:** INFORMED FV + fee-true EV before create; FTL self-correct; MiroFish narrative/season only when near-neutral; max profit under gates.  
**This slice:** research/shadow intelligence + self-correct plumbing. **Do NOT unfence live oil/weather creates.**

**Cite / map:** `valedia_config.LIVE_EXEC_FENCED` / `LIVE_EXEC_FENCED_SERIES` / `WEATHER_LIVE`; `scripts/integrations/weather_shadow.py`; `weather_trade.py`; `weather_arb.py` (create_order already removed); `informed_before_create.py` (`live_fence`, `oil_no_harvest`); `session_clocks.py` (`fence_gate`, `CLOCK_OIL_WEATHER`, `WEATHER_SHADOW`); `forward_test_ledger.py`; `PHASE-3-WEATHER-RESEARCH-SHADOW.md`; prior style `ETORO-SUBSUME-1.md`.

**Depends on:** queue idle after VIBE-TRADING-SUBSUME-1 (`GROK_EXIT:0`, build tree 0.71.0). Active becomes this slice.

## Goals

1. **Oil — shadow desk**
   - Add `scripts/integrations/oil_shadow.py`.
   - Official/print FV only (settlement-relevant WTI / documented Kalshi KXWTI·KXOIL series rules). **No** Muse-alone or MiroFish-as-FV.
   - Fee-true would-have EV + size; force `dispatchable=0`; `create_order` never true.
   - Persist `models/oil_shadow_ledger.json` (build tree; mirror/copy path for live tree when possible).
   - Wire through `session_clocks.fence_gate` + INFORMED `live_fence` / `oil_no_harvest` (already refuse create). Do not weaken those reasons.

2. **Weather — operationalize Phase 3**
   - Fresh official NWS/NOAA into `weather_shadow.py` (or thin helper it calls). Cache/observe-day honesty stays: stale March titles are not today's FV.
   - MiroFish **season/narrative only** (`season_only`); refuse default-0.50 / near-neutral as FV / size fuel.
   - Append living `models/weather_shadow_ledger.json` on **both** trees when possible (at least v2 + documented copy path for live `~/.openclaw/workspace/models/`).
   - Optional FTL-**shaped** would-have rows tagged `paper`/`shadow` (never live fill rows; never call `reconcile` against real Kalshi fills for fenced desks).

3. **Self-improve under fence**
   - Shadow accuracy rollups and/or category/stats hooks from shadow outcomes that can **tighten thresholds** later.
   - Do **not** unfence; do not set `WEATHER_LIVE`; do not restore `create_order` in `weather_arb`; do not clear `LIVE_EXEC_FENCED` / series fences (`KXWTI`/`KXOIL`).

4. **Controls honesty**
   - `CLOCK_OIL_WEATHER` / `WEATHER_SHADOW` / new `OIL_SHADOW` (or equivalent) show **RESEARCH/SHADOW**, never live-eligible.
   - `dispatchable=0`, `live_exec=false`, fence=`LIVE_EXEC_FENCED` in controls snapshot rows.

5. **Schedule shadow-only tick (optional)**
   - If house pattern supports (cron or allocator hook that **only scores**), add dry wrapper or commented cron.
   - Must **never** create. Prefer documenting + optional cron comment / dry wrapper over flipping live.

6. **Tests + audit + version**
   - Offline unit tests (fixtures; no network required for PASS; optional live NWS smoke separate and non-blocking).
   - Prove `dispatchable=0` and INFORMED / allocator `SKIP_LIVE_FENCE` (or equivalent) for oil+weather paths.
   - Ponytail `PONYTAIL-AUDIT-OIL-WEATHER-SMART-1.md`.
   - Bump working-tree `VALEDIA_VERSION` (uncommitted OK; live stays 0.44.0).
   - RuFlo `:8810` + CBM noted in `GROK-STATUS.md`.

7. **Autonomy**
   - If a design fork would ask Burton, decide **safer/narrower research-first** yourself and log rationale in Results / STATUS.
   - Defaults: official FV only; shadow ledgers; no live cron that can create; MiroFish season_only; FTL-shaped paper rows only.

## Explicit non-goals

- Unfence `WEATHER_ARB` / `OIL_BRACKET` / `KXWTI` / `KXOIL`
- `WEATHER_LIVE=True` / env enable
- Restore `create_order` in `weather_arb` or any oil writer
- Mass Poly create; wallet/treasury ACH; eToro write/copy
- Muse/GDELT/MiroFish as required FV
- Jev-as-OMS; weakening INFORMED fail-closed
- Auto-chaining an unfence slice

## Hard fence

- Do **not** unfence `WEATHER_ARB` / `OIL_BRACKET` / `KXWTI`/`KXOIL`; `WEATHER_LIVE` stays False/0.
- No `create_order` restore; no mass Poly; no wallet/treasury ACH.
- INFORMED fail-closed; Jev fail-open non-OMS.
- Preserve screens `ot-valedia-desk` + `valedia-glance`; only recycle `valedia-grok`.
- No HALT bypass; no `--live` oil/weather path.

## Tools

RuFlo MCP `http://127.0.0.1:8810/mcp` filtered (ADR-035). CBM for Valedia navigation. Prefer Grok 4.7. Prove tool use in STATUS.

## Deliverables

1. `scripts/integrations/oil_shadow.py` + `models/oil_shadow_ledger.json`
2. Weather shadow upgrades (NWS/NOAA feed path + living ledger append) under fence
3. Controls rows RESEARCH/SHADOW (`OIL_SHADOW` + existing weather/oil clocks)
4. Offline tests proving `dispatchable=0` / live-fence refuse / no create
5. Optional shadow-only cron comment or dry wrapper (never creates)
6. `PONYTAIL-AUDIT-OIL-WEATHER-SMART-1.md`
7. Results here + both-tree `GROK-STATUS.md` / queue; **Next = Burton stop**

## Done when

- Oil + weather shadow-scorable with INFORMED-shaped FV + fee-true EV
- `dispatchable=0` / `SKIP_LIVE_FENCE` (or INFORMED `live_fence`/`oil_no_harvest`) proven by test
- Shadow ledgers on disk; controls RESEARCH/SHADOW
- Ponytail written; working-tree version bumped
- Zero live oil/weather creates from this path
- `GROK_EXIT:0`
- **Next = Burton stop** (unfence is a separate Burton decision — do not auto-chain)

## Results

**Status:** DONE. `GROK_EXIT:0`. **Next = Burton stop.** Do not auto-chain. Do not unfence.

Working tree `VALEDIA_VERSION` **0.72.0** (not committed, not tagged). Live cron tree stays **0.44.0**. `WEATHER_LIVE` is False. `LIVE_EXEC_FENCED` is still `WEATHER_ARB` and `OIL_BRACKET`. Series fence is still `KXWTI` and `KXOIL`. `weather_arb.py` still has no `create_order(` call.

**Oil.** `scripts/integrations/oil_shadow.py` writes `models/oil_shadow_ledger.json` and the same file under `~/.openclaw/workspace/models/`. Universe n=2 (`KXWTI`, `KXOIL`), both `no_fv` until a print arrives. Worked fixture, `book=fixture`, not a live print: NYMEX settlement 80 vs above $72, `rules_cite=nymex_cl`, fv 0.99, maker fee 0, size 20, fee-true EV 11.8, settlement-yes would-have P&L 12.0. `dispatchable=0`. `create_order` false. Muse and MiroFish stay off FV. A print without `rules_cite` is `no_fv`. A forecast that lands within 5 points of 0.50 is `near_neutral` and size 0.

**Weather.** `weather_nws.py` is the official feed. A period counts only when its calendar date equals the observe-day. The March city-cache titles stay off FV. Ledger on both trees. Fixture row (same shape as Phase 3): NWS 80° vs >72°, fv 0.99, taker fee 0.34, size 20, fee-true EV 11.46. Live NWS smoke for NYC matched today's forecast period, tag `official`, fv 0.091211, size 0 because the smoke has no book. `dispatchable=0`. `create_order` false. MiroFish stays `season_only`. Default 0.50 and near-neutral are not size fuel.

**Self-correct, not a gate.** Each ledger carries `accuracy` and `threshold_hint`. `apply` is false. The rows are FTL-shaped with `source=shadow`, `fill_status=PAPER`, `reconcile=false`. They are not written to the production forward-test ledger and `reconcile` is not called. `category_stats` already drops `shadow` and `paper`. Kelly is unchanged.

**Controls.** `CLOCK_OIL_WEATHER`, `WEATHER_SHADOW`, and `OIL_SHADOW` read `RESEARCH/SHADOW`, `LIVE_EXEC_FENCED`, `dispatchable=0`, `live_exec=false`. Oil `fence_gate` mode is `RESEARCH/SHADOW` with the same refuse reason. Allocator `before_create` returns `SKIP_LIVE_FENCE` for `KXWTI`, `KXOIL`, and city weather. INFORMED still adds `live_fence` and, for `NO_HARVEST` on an oil series, `oil_no_harvest`.

**Schedule.** `oil_weather_shadow_tick.py --dry` scores only. `--live` exits 2. The crontab line in that file is a comment. It is not installed.

**Tests.** `test_oil_shadow.py` PASS. `test_weather_shadow.py` PASS. `test_informed_before_create.py` PASS. `test_slice23.py` PASS. Run in a child that hid the inherited live flags. Parent flags were left set. `test_controls_readout.py` and `test_session_clocks.py` reach the new oil/weather rows, then fail on the pre-existing working-tree `VENUE_WRITER.polymarket=valedia` (those tests still expect `none`). This slice did not change that writer.

**Screens.** `ot-valedia-desk` (783) and `valedia-glance` (17171) left running. `valedia-grok` (35929) is this session and was not restarted.

**Tools.** RuFlo `:8810` `hooks_route` keyword → coder, confidence 0.85, no swarm. `swarm_status` `swarm-1789281243984-xo1j5g` terminated, 0 agents. `memory_search` hit Phase 3 weather shadow at similarity 0.65 (HNSW, 117ms). CBM project `Users-burtonchatelain-.openclaw-workspace-v2` is indexed. `search_graph` returned `weather_shadow.score` and `session_clocks.fence_gate`.

**Goal 7.** Safer path taken: official print or NWS only; shadow ledgers; no production FTL append; hint does not apply; no installed cron; no unfence.

Audit: `PONYTAIL-AUDIT-OIL-WEATHER-SMART-1.md`.


## STEER (2026-09-23 ~18:22 PT) — Burton: eToro-style weather web pulls

Burton asked for farthest/most-accurate predictions via website pulls (like eToro without/beyond rate-limited API).

**Primary FV (INFORMED / create gate shape):** contract settlement source only.
- Daily high/low Kalshi city weather → **NWS Daily Climate Report (CLI)** HTML on weather.gov (station in market rules). Prefer page/HAR pull over depending solely on api.weather.gov rate limits.
- Hourly Kalshi weather → **The Weather Company** when rules say so (not NWS CLI).
- AccuWeather / iOS / Google / Weather Channel are **not** settlement for daily NWS markets (Kalshi help). Never let them alone pass INFORMED create.

**Secondary research intel (tags only, not size/FV fuel):**
- weather.com (The Weather Channel) — ForecastWatch 2021–2024 global leader ~1–14d
- AccuWeather — competitive short-range
- ECMWF charts/open data — best underlying global model; CPC (cpc.ncep.noaa.gov) for weeks–seasons farthest official U.S. outlooks
- MiroFish stays season/narrative only when near-neutral

Wire: primary CLI/web settlement fetch in weather_shadow; optional secondary site pulls as research tags / disagreement signals; `dispatchable=0` still. Document sources in Results + security note if scraping.

