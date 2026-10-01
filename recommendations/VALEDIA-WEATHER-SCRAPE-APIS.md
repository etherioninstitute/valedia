# Valedia weather scrape API draft

**Capture reviewed:** 2026-09-23 18:29 PDT (PT)  
**Inputs:** `weather-scrape/devtools/*.har`, `SCRAPE.md`  
**HAR coverage:** `nws-cli-index.har`, `nws-cli.har`, `weather-com.har`, `accuweather.har`. No `ENDPOINT-MAP.md` was present.  
**Filtering:** retained Fetch/XHR/Document weather, climate, forecast, JSON, and source-report requests; omitted fonts, analytics, ad servers, tracking pixels, and unrelated telemetry. HAR query credentials are intentionally replaced by `{api_key}` below.

## Executive summary

- **Settlement source:** NWS CLI HTML product for daily climate settlements; The Weather Company (weather.com) is the stated official source for hourly temperature markets in the supplied scrape notes.
- **Research sources:** Weather.com forecast JSON, AccuWeather page data, and optionally ECMWF chart products. Research forecasts must not be treated as final settlement observations.
- **Best direct client:** `httpx` for NWS CLI and captured weather.com JSON. Use `Playwright` for AccuWeather (only a full HTML Document was captured, with page/embedded data and anti-automation concerns) and ECMWF chart UI.
- **Important limitation:** the weather.com HAR contains forecast endpoints, not a separately identified final hourly observation endpoint. Validate the official-market rule and observation cutoff before using an hourly response as `settlement_fv`.

## Site: National Weather Service CLI

**Role:** settlement (daily high/low and other daily climate products)  
**Client:** `httpx` first; parse the report's HTML `<pre class="glossaryProduct">` text. Playwright is only a fallback if the report page changes or blocks direct requests.

### Best endpoints

1. **CLI product index / station discovery**
   - `GET https://forecast.weather.gov/product_sites.php?product=CLI&site=NWS`
   - Response: HTML table of NWS office identifiers and links such as `product.php?site=NWS&product=CLI&issuedby=ABE`. Use it to maintain/configure WFO and `issuedby` mappings; it is not itself the settlement value.

2. **CLI report (primary settlement endpoint)**
   - `GET https://forecast.weather.gov/product.php?site={wfo}&issuedby={issuedby}&product=CLI&format=CI&version={version}&glossary=0`
   - Sample: `site=OKX`, `issuedby=NYC`, `product=CLI`, `format=CI`, `version=1`.
   - Response shape: HTML document with a report in `<pre class="glossaryProduct">`. Parse report date, valid-local cutoff, issue timestamp, station/location, and labeled fields. Daily temperature fields are tabular text: `TEMPERATURE (F)`, `TODAY`, `MAXIMUM`, `MINIMUM`, `AVERAGE`; precipitation is under `PRECIPITATION (IN)` and `TODAY`.
   - `settlement_fv` candidates: `MAXIMUM` for daily high, `MINIMUM` for daily low, and `TODAY` precipitation when the market rule matches. Preserve units, local date, issue time, and raw report text/hash.

### NWS parsing notes

- The sample report is Central Park NY for 2026-09-23 and reports max 67 F, min 55 F, average 61 F, and today precipitation 0.00 in.
- Do not infer a city-to-office mapping solely from a city name. Keep an explicit venue/station configuration (for example NYC → `OKX`/`NYC`) and verify the market rules' named observation station.
- Select the final/appropriate report version for the market's observe date; do not silently accept a preliminary or wrong-date CLI page.

## Site: The Weather Company / weather.com

**Role:** settlement for hourly markets when the market rules explicitly name The Weather Company; research for forecast context.  
**Client:** `httpx` for the captured JSON endpoints; use a configured API key from environment/secret storage, never a HAR value.  
**Observed sample geocodes:** Wheaton, IL `41.85,-88.12`; New York, NY `40.713,-74.006`.

### Best endpoints

1. **15-day daily forecast**
   - `GET https://api.weather.com/v3/wx/forecast/daily/15day?geocode={lat},{lon}&units=e&language={locale}&format=json&apiKey={api_key}`
   - Response: object of aligned arrays including `calendarDayTemperatureMax`, `calendarDayTemperatureMin`, `temperatureMax`, `temperatureMin`, `validTimeLocal`, `validTimeUtc`, `dayOfWeek`, `qpf*`, `narrative`, and `daypart`. The sample has 15 daily slots.
   - Best research endpoint for date-aligned high/low forecast ranges; not a final observation by itself.

2. **2-day hourly forecast**
   - `GET https://api.weather.com/v3/wx/forecast/hourly/2day?geocode={lat},{lon}&units=e&language={locale}&format=json&apiKey={api_key}`
   - Response: aligned arrays (sample 48 slots) including `temperature`, `temperatureFeelsLike`, `validTimeLocal`, `validTimeUtc`, `precipChance`, `qpf`, `wind*`, `wxPhraseLong`, and `dayOrNight`.
   - Best captured endpoint for hourly temperature research. Treat as `settlement_fv` only after confirming that this route/value is the official TWC observation under the specific market rule and cutoff.

3. **7-day daily forecast**
   - `GET https://api.weather.com/v3/wx/forecast/daily/7day?geocode={lat},{lon}&units=e&language={locale}&format=json&apiKey={api_key}`
   - Response: same daily array family as 15-day; sample has 8 calendar slots (including a trailing slot). Useful for near-term research and fixture coverage.

4. **15-minute forecast**
   - `GET https://api.weather.com/v3/wx/forecast/fifteenminute?geocode={lat},{lon}&units=e&language={locale}&format=json&apiKey={api_key}`
   - Response: aligned short-horizon arrays including `temperature`, `temperatureFeelsLike`, `validTimeLocal`, `precipChance`, `precipRate`, `precipType`, `wind*`, and weather phrases. Sample has 28 slots.
   - Research-only unless a market explicitly defines this forecast as its source.

5. **Short-range “what will it be like” summary**
   - `GET https://api.weather.com/v1/geocode/{lat}/{lon}/forecast/wwir.json?units=e&language={locale}&apiKey={api_key}`
   - Response: `{metadata, forecast}`; `metadata` includes lat/lon, units, expiry, and status; `forecast` includes `fcst_valid`, `fcst_valid_local`, `phrase`, `terse_phrase`, and precipitation summary fields.
   - Useful research tag/summary, not a temperature settlement value.

### Weather.com implementation notes

- The captured routes are JSON and can be fixture-tested directly. Normalize all aligned arrays by index and match `observe_date` in the market's local timezone.
- Keep `source_url`, geocode, request timestamp, response expiry, and endpoint family in research tags.
- An API key was present in the HAR; it is not reproduced here and must not be committed. Use an environment-backed secret if this integration is later enabled.

## Site: AccuWeather

**Role:** research only; not a settlement source unless a future market rule explicitly says so.  
**Client:** `Playwright` preferred for the captured path; the HAR has a useful HTML Document but no weather Fetch/XHR API. `httpx` may be used only if the page's embedded data remains stable and access permits.

### Best captured endpoint

- `GET https://www.accuweather.com/en/{country}/{city_slug}/{postal}/10-day-weather-forecast/{location_key}`
- Captured sample: `/en/us/new-york/10021/10-day-weather-forecast/349727`.
- Response: full HTML page titled `New York, NY 10-Day Weather | AccuWeather`, with forecast page markup and embedded/runtime data consumed by the client bundle. The page advertises daily highs/lows, precipitation chance, and current/RealFeel values. The HAR did not capture a clean JSON response, so selectors/embedded-state parsing must be fixture-tested and treated as brittle.
- Required location fields: country, city slug, postal code, and AccuWeather location key. Resolve and cache these separately; do not use a ticker/slug as a substitute for the official settlement station.

## Site: ECMWF charts (optional)

**Role:** research only.  
**Client:** `Playwright` for the chart catalogue/UI; `ecmwf.py` is optional.  
**Observed source:** `https://charts.ecmwf.int/` from `SCRAPE.md`; no ECMWF HAR was present in the input directory.

The catalogue showed 2 m temperature, 850 hPa temperature, forecasts, verification, medium-range, sub-seasonal, and seasonal products. No structured endpoint or stable response shape was captured here. Do not implement a guessed API from this scrape; add it only from a later HAR/fixture and keep outputs as research tags.

## Site: NOAA CPC (unusable in this capture)

**Role:** research only.  
**Observed source:** `https://www.cpc.ncep.noaa.gov/products/predictions/`; `SCRAPE.md` records HTTP 403 Forbidden and no usable prediction fields. No CPC HAR was present. No settlement client should depend on it from this capture.

## Unified Valedia lookup contract

Implement one boundary that accepts either market venue and returns normalized settlement plus research metadata:

```text
lookup(trade) -> settlement_fv + research_tags
```

Required `trade` fields:

- `venue`: `kalshi` or `poly`
- `ticker` / `slug`: native market identifier (`ticker` for Kalshi, `slug` for Polymarket)
- `city`: market city/location, resolved to configured station and geocode
- `settle_source`: explicit source if available; otherwise resolve from market rules
- `observe_date`: local market observation date, with timezone

Recommended normalized result:

```text
settlement_fv = {
  value, unit, metric, observe_date, observed_at,
  source: "nws_cli" | "weather_com",
  source_url, station_or_geocode, confidence, raw_fixture_ref
}
research_tags = [
  {source, endpoint, fetched_at, valid_time, forecast_or_observation,
   value_summary, provenance}
]
```

Resolution rules:

- Daily high/low or daily climate market → NWS CLI final climate report, with station/WFO selected from the rules/configuration.
- Hourly temperature market → The Weather Company official value only when the rules say so; the captured forecast endpoint is evidence for research and requires an observation/finality check.
- If the named source, station, date, metric, or report finality cannot be resolved, return an explicit unresolved/error state rather than guessing.
- Research tags can inform analysis but **must never alone pass `INFORMED`** or any equivalent trade-readiness gate.

## Charter bullets for WEATHER-SCRAPE-APIS-1

- Implement clients under Valedia `scripts/integrations/weather_sources/`: `nws_cli.py`, `weather_com.py`, `accuweather.py`, and optional `ecmwf.py`.
- Implement unified `weather_lookup.py`: given a Kalshi/Poly weather market, resolve settlement source from rules, fetch settlement FV via the scrape API, attach research tags, and never alone-pass `INFORMED` from research tags.
- Wire into `weather_shadow`; add tests with fixtures from these HAR samples, including NWS `<pre>` parsing and weather.com aligned-array parsing.
- Hard fence: `WEATHER_LIVE=false`; no `create_order`; no unfence.
- Goal: improve weather accuracy for maximum-quality weather trades under the north star.

## Top five endpoints found

1. `GET https://forecast.weather.gov/product.php?site={wfo}&issuedby={issuedby}&product=CLI&format=CI&version={version}&glossary=0` — NWS daily climate settlement report.
2. `GET https://api.weather.com/v3/wx/forecast/daily/15day?...` — date-aligned 15-day high/low forecast JSON.
3. `GET https://api.weather.com/v3/wx/forecast/hourly/2day?...` — 48-slot hourly temperature forecast JSON.
4. `GET https://api.weather.com/v3/wx/forecast/daily/7day?...` — near-term daily forecast JSON.
5. `GET https://api.weather.com/v1/geocode/{lat}/{lon}/forecast/wwir.json?...` — short-range weather summary JSON.

## Evidence and limitations

- `nws-cli-index.har` and `nws-cli.har` contain HTML documents plus site assets; no separate NWS JSON API call was observed.
- `weather-com.har` contains the five useful weather.com families above plus ad/analytics/tracking requests that were excluded.
- `accuweather.har` contains the AccuWeather document and unrelated ad/consent documents, but no clean AccuWeather weather API request.
- Inputs may still be growing. Re-run the parser when additional HARs arrive, especially for an actual TWC hourly observation/finality route and ECMWF structured data.
