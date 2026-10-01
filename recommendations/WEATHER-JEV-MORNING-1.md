# WEATHER-JEV-MORNING-1 — Jev morning parse + WEATHER go-live preflight

**Queued:** 2026-09-23 ~19:06 PT (after WEATHER-SCRAPE-APIS-1).  
**Burton intent:** Tomorrow unfence `WEATHER_LIVE` for **INFORMED fee-true WEATHER ONLY** if preflight PASS. Refuse lotteries + late-collapse. **Oil stays fenced.** Do **not** flip `WEATHER_LIVE` until `weather_live_preflight.py` returns PASS.

**Depends on:** WEATHER-SCRAPE-APIS-1 exit (STEER folded: TWC daily settle + station map + Jev thin wire if absorbed). Cite STEER: `WEATHER-SCRAPE-APIS-1-STEER.md`.

## Goals

1. **Morning scan path (shadow first, then gated live)**  
   On each new Kalshi daily weather market:
   - Pull settle FV (**TWC** for daily city high/low per rules / CLIxxx) + NWS research + Accu/TWC forecast tags + book mids
   - Run **Jev** typed shadow over that bundle FAST → annotate agree/disagree/confidence/status-honesty
   - Append shadow ledger; **never alone-pass create** on Jev
   - If fee-true edge ≥ threshold under INFORMED shape → write `recommendations/WEATHER-MORNING-ALERT.md` (or jsonl) for parent glance
   - Ornith: optional only if already wired; do not block

2. **Create-path refuses (not shadow-only)**  
   - Lottery: refuse tail ≤~0.05 (or conviction-ROI lottery shape)
   - Late-collapse: refuse favorite ≥~0.90 late in observe-day without early-day soft book
   - Wire into `informed_before_create` / weather create path

3. **Go-live preflight (hard gate before flip)**  
   Implement/extend `scripts/integrations/weather_live_preflight.py` (stub already landed 2026-09-23).  
   Flip `WEATHER_LIVE=1` / enable `weather_live_enabled()` **only if ALL PASS**:
   1. Settle FV from contract’s own source (TWC for daily city high/low); INFORMED fail-closes without it
   2. Stations + TZ mapped for each series traded (MIA, LAS/TLV, existing)
   3. Fee-map has KXHIGH*/KXLOW* city series (fee-true EV real)
   4. Lottery + late-collapse enforced on **create** path
   5. Jev stays shadow-only fail-open; size caps = existing Kelly (conservative/half-Kelly)
   6. Live path writes ledger + alerts on each create  
   Else stay `WEATHER_LIVE=0` and name blockers. **Do not touch OIL_LIVE / oil fence.**

4. **Live enable (only after PASS)**  
   - Carefully lift weather-only: `weather_live_enabled()` may honor env when preflight PASS + Burton go; keep `OIL_BRACKET` + `KXWTI`/`KXOIL` fenced
   - Restore a **narrow** weather create path behind INFORMED + fee-true + lottery/late refuses + size caps
   - Each create → ledger row + alert artifact

5. **Docs**  
   - Results on this charter; Ponytail audit; both-tree STATUS/queue; Next = Burton stop

## Explicit non-goals

- Flipping WEATHER_LIVE without preflight PASS
- Oil / OIL_BRACKET / KXWTI / KXOIL unfence
- Jev-as-OMS / Jev alone-pass create
- Mass Poly create; wallet/treasury ACH; eToro write
- Blocking on Ornith swarm

## Hard fence (until preflight PASS)

- Tonight / until PASS: `WEATHER_LIVE` stays False/0; `weather_live_enabled()` dead-path OK
- Oil remains in `LIVE_EXEC_FENCED` / series fence
- INFORMED fail-closed; Jev fail-open non-OMS
- Preserve `ot-valedia-desk` + `valedia-glance`; only recycle `valedia-grok`

## Deliverables

1. Morning scan + Jev annotate wire (weather bundle → `jev_shadow`)
2. Create-path lottery/late refuses
3. `weather_live_preflight.py` green (or FAIL with named blockers — do not flip)
4. Alert + live ledger hooks
5. Fee-map refresh for KXHIGH*/KXLOW* city series; station/TZ for MIA + LAS/TLV
6. Only if PASS: env/code path for WEATHER_LIVE weather-only
7. STATUS/queue/Ponytail; Next = Burton stop

## Done when

- Preflight run documented (PASS → live path armed; FAIL → blockers listed, live stays 0)
- Morning alert path works in shadow even if live stays 0
- Oil still fenced; Jev never OMS

## Result — 2026-09-23 19:34 PT

`GROK_EXIT:0`. Preflight **PASS** (`flip_allowed=True`). `WEATHER_LIVE` env was **not** set. `weather_live_enabled()` is **False**. Constant stays `False`. Build tree **0.74.0** (not committed, not tagged). Live cron stays **0.44.0**.

Path is armed in this tree only: `weather_live_enabled()` returns true only when `WEATHER_LIVE=1` and `weather_live_preflight.py` is still PASS. `weather_live.admit` is the only weather sender. It refuses oil, lotteries (`SKIP_LOTTERY`, tail ≤ 0.05 or conviction ROI ≥ 19 at ≤ 10¢), and late favorites (`SKIP_LATE_COLLAPSE`, ≥ 90¢ late in the observe-day without an early soft book). The allocator still returns `SKIP_LIVE_FENCE`. Jev annotates the shadow bundle and cannot pass a create.

Closed tonight:
- Daily KXHIGH/KXLOW with Weather Company / CLIxxx rules resolve to `weather_com` (observation or final only). NWS rules stay `nws_cli`. NWS CLI is a cross-check tag on the TWC path. Hurricane → `need_fv_nhc`. TWC rain → `need_fv_precip`.
- Stations confirmed on forecast.weather.gov: MIA `MFL/MIA` (CLIMIA), LAS/TLV `VEF/LAS` (CLILAS). `CITY_TZ` LAS and TLV are `America/Los_Angeles`. "Last Trading Time will be …" parses.
- Fee artifact: 38 city series from Kalshi GET, `fee_type=quadratic`, multiplier 1 (maker 0, taker 1). Includes `KXHIGHMIA`, `KXLOWTLV`, `KXHIGHTLV`.
- Morning scan writes the shadow ledger and `recommendations/WEATHER-MORNING-ALERT.md` when fee-true EV is positive, the tag is official, and the price is not a tail or a late favorite. Live attempts append `models/weather_live_ledger.jsonl`.

Screens left running: `ot-valedia-desk` 783, `valedia-glance` 17171, `valedia-grok` 74464. No recycle. Oil stays fenced. No mass create.

Tests: `test_weather_jev_morning.py` PASS. `test_weather_scrape_apis.py` PASS. `test_weather_shadow.py` PASS. `test_oil_shadow.py` PASS. `test_informed_before_create.py` PASS. `test_jev_shadow.py` PASS. `test_slice23.py` PASS.

**Next = Burton stop.** Do not auto-chain. Set `WEATHER_LIVE=1` only on this build tree, and only while preflight still prints PASS. Do not unfence oil. Do not mass create.

## STEER (2026-09-23 ~19:16 PT) — self-knowledge / CBM
Burton: Valedia must know herself; CBM is **not** auto-updated every iteration. Before GROK_EXIT: run Mac CBM `index_repository` on workspace-v2 (fast OK) + `check_index_coverage` on files this slice touched (weather_live_preflight, weather_lookup, weather_sources, informed_before_create, jev_shadow, fee-map). Note coverage in Results. Do not flip oil. WEATHER_LIVE only after preflight PASS.
