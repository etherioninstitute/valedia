

## STEER (2026-09-23 ~19:01 PT) — Burton Climate test + settle-source correction

Burton shadowed Kalshi Climate cards (Miami high, Vegas low, rain, hurricane names). Late-day = still tradable but SKIP_LATE_COLLAPSE / SKIP_LOTTERY for near-99¢ favorites and tails.

**CRITICAL:** Kalshi daily high/low for these series settles on **The Weather Company** (rules: CLIMIA, CLILAS), NOT NWS CLI. NWS CLI = research/proxy only for those markets. Hourly already TWC. Wire `weather_lookup` so:
- settlement_fv for KXHIGH* / KXLOW* daily city = Weather Company / weather.com path (or documented CLIxxx source in rules)
- NWS CLI = research_tags / cross-check
- Research tags never alone-pass INFORMED
- Add station map entries for MIA (CLIMIA) and TLV/LAS (CLILAS); CITY_TZ for TLV
- Fix LTT parse for "Last Trading Time will be …"
- Rain/hurricane: document NEED_FV gaps (precip CLI / NHC) — stub refuse with clear code, do not fake FV
- Fee-map note: KXHIGHMIA / KXLOWTLV / KXRAIN not in map yet — lookup may still score shadow; live create stays fenced

Preserve fence. Do not unfence.


## STEER (2026-09-23 ~19:06 PT) — Jev morning parse + WEATHER go-live PREFLIGHT (do NOT flip tonight)

Burton plan change: **tomorrow** unfence `WEATHER_LIVE` for **INFORMED fee-true WEATHER ONLY** (refuse lotteries + late-collapse). **Oil stays fenced.** Do **not** set `WEATHER_LIVE=1` / do not change `weather_live_enabled()` to env-on tonight.

### Absorb if still mid-flight (thin, prefer stub over unfinished live)
1. **TWC settle for daily city high/low** (already in prior STEER) — `weather_lookup.resolve_source`: KXHIGH*/KXLOW* daily with Weather Company / CLIxxx in rules → `weather_com` settlement_fv (observation/finality honesty). NWS CLI → research_tags / cross-check only for those markets. INFORMED fail-closes without settlement_fv.
2. **Stations + TZ:** map MIA (CLIMIA), LAS/TLV (CLILAS); `CITY_TZ` for TLV/LAS; keep existing NY etc.
3. **Fee-map:** ensure KXHIGH* / KXLOW* city series present (tonight artifact only has KXHURRICANE + KXTEMP) so fee-true EV is real.
4. **Jev (fail-open, shadow only, not OMS):** wire `weather_shadow.score` / morning bundle → `jev_shadow.annotate` (or weather-typed questions) over settle FV + research tags + book mids. Annotate agree/disagree/confidence/status-honesty. **Never alone-pass create.** Fail-open on timeout/error.
5. **Do not** restore `create_order` or lift `LIVE_EXEC_FENCED` / `weather_live_enabled` in this scrape slice. Leave live for morning preflight.

### After scrape exits → Next charter `WEATHER-JEV-MORNING-1`
Morning path (Build implements; parent notifies Burton):
- On each new Kalshi daily weather market: pull settle FV (TWC) + NWS research + Accu/TWC forecast tags + book mids
- Run Jev typed shadow FAST → annotate; shadow ledger append
- If fee-true edge ≥ threshold under INFORMED shape → write `recommendations/WEATHER-MORNING-ALERT.md` or jsonl (parent alerts)
- Refuse lottery (tail ≤~0.05) / late-collapse (favorite ≥~0.90 late observe-day) — **in create path**, not shadow-only
- Ornith optional only if already wired; do not block morning path
- **Go-live preflight** (`scripts/integrations/weather_live_preflight.py`): PASS all checks before anyone flips `WEATHER_LIVE`. Else stay 0 and name blocker. Oil untouched.

Preserve fence tonight. Next after this slice (scrape) = **WEATHER-JEV-MORNING-1** then Burton stop. Do not steal Active; do not launch another grok.
