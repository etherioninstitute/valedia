# Ponytail audit — WEATHER-SCRAPE-APIS-1 (0.73.0)

**Mode:** `/ponytail` on this slice only.
**Fence:** `WEATHER_ARB` and `OIL_BRACKET` stay `LIVE_EXEC_FENCED`. Series `KXWTI` and `KXOIL` stay fenced. `WEATHER_LIVE` stays false. `dispatchable=0`. No `create_order` restore. Screens `ot-valedia-desk` (783) and `valedia-glance` (17171) not signaled. `valedia-grok` (54578) not recycled.

## Applied

`reuse:` `weather_shadow.score` still owns the bracket, the fee quote, and the half-Kelly cap. The lookup only supplies a temperature or a refuse.

`reuse:` `weather_nws.py` still gates the gridpoint JSON path. CLI is a second reader, not a second pricer.

`reuse:` `informed_before_create.kalshi_reasons`. Research tags go in as `fv_source=research` with `fv=None`. That is not in `ALLOWED_FV`. Weather still adds `live_fence`.

`yagni:` no new dependency. `httpx` is preferred inside `weather_sources/_get.py` when it imports. This machine does not have it, so the live GET uses `urllib`. Playwright is not installed and is not called.

`yagni:` no CLI index crawler, no cron install, no CPC client, no guessed ECMWF product API.

`yagni:` AccuWeather accepts one embedded JSON island. Anything else yields no number.

## Findings

`ponytail:` the station table is one row, `NY` → `OKX`/`NYC`. Add a row only when a captured CLI product names that office. Do not infer one from a city string.

`ponytail:` Weather.com HAR routes are forecasts. Hourly settlement requires `observation: true` on the payload. A later HAR of the real observation route can replace that flag.

**net:** four small readers, one lookup, the existing shadow scorer. 0 new dependencies. 0 live weather creates from this run. Lean enough. Ship.
