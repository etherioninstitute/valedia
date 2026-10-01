# WEATHER-SCRAPE-APIS-1 — Per-site scrape clients + unified weather lookup (shadow only)

**Burton go:** 2026-09-23 ~18:48 PT — keep going; weather accuracy for maximum (quality) weather trades. Build per-site scrape “APIs” from DevTools/HAR so Valedia can accurately lookup whatever each Kalshi/Poly weather trade needs — without rate-limited official APIs where web/HAR endpoints work.  
**North star:** INFORMED FV + fee-true EV; weather accuracy so Valedia can take the *best* weather trades (not lottery). Settlement source wins create; research tags never alone-pass INFORMED.  
**This slice:** implement scrape clients + unified lookup + wire into `weather_shadow` under fence. **Do NOT unfence live oil/weather creates.**

**Cite / map (read these first):**
- `recommendations/VALEDIA-WEATHER-SCRAPE-APIS.md` — richer HAR-parse contract (settlement_fv shape, endpoint families, NWS `<pre class="glossaryProduct">`, weather.com aligned arrays, Accu/ECMWF/CPC notes)
- `recommendations/WEATHER-ENDPOINT-MAP.md` — DevTools capture map (NWS CLI, weather.com JSON, Accu HTML, ECMWF catalogue, CPC 403)
- `recommendations/WEATHER-SCRAPE-SAMPLE.md` — Kalshi help (daily → NWS CLI; hourly → TWC) + NYC CLI sample (max 67 / min 55 on 2026-09-23)
- Prior: `OIL-WEATHER-SMART-1.md` DONE (build 0.72.0); `weather_shadow.py` + `weather_nws.py` observe-day gate; `WEATHER_LIVE` False; fence held

**Depends on:** OIL-WEATHER-SMART-1 `GROK_EXIT:0`. Active becomes this slice.

## Goals

1. **`scripts/integrations/weather_sources/` clients** from ENDPOINT-MAP + VALEDIA-WEATHER-SCRAPE-APIS:
   - `nws_cli.py` — `httpx` GET `product.php` CLI HTML; parse `<pre class="glossaryProduct">` Maximum/Minimum (and precip TODAY when rules match) for station/WFO; **settlement primary**. Explicit WFO/`issuedby` config (e.g. NYC → OKX/NYC); do not guess city→office. Prefer version/date honesty (refuse wrong-date/preliminary silently).
   - `weather_com.py` — `httpx` to `api.weather.com/v3/wx/forecast/daily/15day` (and hourly/2day, 7day, fifteenminute, wwir as research). Key from env `WEATHER_COM_API_KEY` or session config — **do NOT commit keys from HAR**. If no key, Playwright page-scrape fallback. TWC is settlement only for hourly markets when rules say so; captured forecast routes need observation/finality check before `settlement_fv`.
   - `accuweather.py` — Playwright preferred (HAR had HTML Document only); `httpx`+HTML only if embedded data stable. 10-day research tags only.
   - optional `ecmwf.py` catalogue stub (research tags; no guessed API). CPC: skip / document 403 — no client dependency.

2. **Unified `weather_lookup.py`:** `lookup(venue, ticker_or_slug, …)` → resolve settle source from market rules (Kalshi help pattern / Poly rules) → `settlement_fv` from NWS CLI (or Weather Company when hourly rules say so) → `research_tags` from TWC/Accu/ECMWF → **refuse if settlement FV missing**. Match the normalized contract in VALEDIA-WEATHER-SCRAPE-APIS.md (`value, unit, metric, observe_date, source, source_url, station_or_geocode, confidence, raw_fixture_ref` + research tag list).

3. **Wire into `weather_shadow.py`** so shadow scoring uses the real lookup path (fixtures offline; optional live smoke read-only). Keep `dispatchable=0`, `create_order` never true, MiroFish `season_only`.

4. **Tests** with HTML/JSON fixtures (redact keys). Prove:
   - NWS CLI `<pre>` parse for at least one Kalshi-style city (NYC OKX/NYC)
   - weather.com aligned-array parse when fixture present
   - research tags **cannot alone** set create/dispatchable / alone-pass INFORMED
   - missing settlement FV → refuse / unresolved (fail-closed for create-shaped gates)

5. **Docs + version + STATUS**
   - Short `WEATHER-SCRAPE-APIS.md` (how to use clients + lookup; no secrets)
   - `PONYTAIL-AUDIT-WEATHER-SCRAPE-APIS-1.md`
   - Bump working-tree `VALEDIA_VERSION` (uncommitted OK; live stays 0.44.0)
   - RuFlo `:8810` + CBM noted in `GROK-STATUS.md`

6. **Autonomy:** decide safer defaults yourself (httpx first for NWS; Playwright only where needed; no key commit; refuse guess on station/date). Log rationale in Results / STATUS.

## Explicit non-goals

- Unfence `WEATHER_ARB` / `OIL_BRACKET` / `KXWTI` / `KXOIL`
- `WEATHER_LIVE=True` / env enable
- Restore `create_order` in `weather_arb` or any oil/weather writer
- Mass Poly create; wallet/treasury ACH; eToro write/copy
- Research-alone INFORMED pass; Muse/GDELT/MiroFish as required FV
- Committing Weather.com (or any) API keys from HAR
- Bypassing CPC 403; inventing ECMWF APIs without HAR
- Auto-chaining an unfence slice

## Hard fence

- Do **not** unfence `WEATHER_ARB` / `OIL_BRACKET` / `KXWTI`/`KXOIL`; `WEATHER_LIVE` stays False/0.
- No `create_order` restore; no mass Poly; no wallet/treasury ACH.
- INFORMED fail-closed; Jev fail-open non-OMS.
- Preserve screens `ot-valedia-desk` + `valedia-glance`; only recycle `valedia-grok`.
- No HALT bypass; no `--live` weather create path.
- Optional live smoke = read-only GET; never create.

## Tools

RuFlo MCP `http://127.0.0.1:8810/mcp` filtered (ADR-035). CBM for Valedia navigation. Prefer Grok 4.7. Prove tool use in STATUS.

## Deliverables

1. `scripts/integrations/weather_sources/{nws_cli,weather_com,accuweather}.py` (+ optional `ecmwf.py`, `__init__.py`)
2. `scripts/integrations/weather_lookup.py` (unified contract)
3. `weather_shadow.py` wired to lookup (fixtures offline; optional read-only smoke)
4. Fixtures under tests (HTML/JSON; keys redacted) + tests green
5. `recommendations/WEATHER-SCRAPE-APIS.md` + `PONYTAIL-AUDIT-WEATHER-SCRAPE-APIS-1.md`
6. Working-tree version bump; both-tree `GROK-STATUS.md` / queue; **Next = Burton stop**

## Done when

- Lookup works for at least one Kalshi-style city CLI station (NYC OKX/NYC) + research tags attachable
- Tests green; research tags cannot alone set create/dispatchable
- Settlement FV missing → refuse
- `WEATHER_LIVE` still False; no create_order restore; fences held
- Ponytail written; working-tree version bumped; RuFlo+CBM in STATUS
- `GROK_EXIT:0`
- **Next = Burton stop** (unfence is a separate Burton decision — do not auto-chain)

## Results

_(fill on exit)_


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

