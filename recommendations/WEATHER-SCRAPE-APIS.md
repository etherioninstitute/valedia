# Weather scrape APIs

Shadow lookup only. `WEATHER_LIVE` stays false. Nothing here places an order.

## What settles

Daily Kalshi climate series settle on the NWS CLI report.

| Series | Field | Unit |
|---|---|---|
| `KXHIGH` | `MAXIMUM` under `TODAY` | F |
| `KXLOW` | `MINIMUM` under `TODAY` | F |
| `KXRAIN` | `TODAY` under `PRECIPITATION (IN)` | in |

Hourly markets settle on Weather.com only when the rules name The Weather Company and the payload is marked `observation: true`. A forecast array is a research tag.

The only configured station is Kalshi city `NY` → WFO `OKX`, `issuedby` `NYC` (Central Park). Other cities return `station_unconfigured`. Pass `wfo` and `issuedby` explicitly if you have them. A city name is not a guess.

## Call

```python
from weather_lookup import lookup

result = lookup(
    "kalshi",
    "KXHIGHNY-26SEP23-T72",
    observe_date="2026-09-23",
    rules="Daily high settles on the NWS climate report.",
    fixtures={"cli_html": open("fixtures/weather/nws_cli_nyc_2026-09-23.html").read()},
)
```

`result["settlement_fv"]` is `{value, unit, metric, observe_date, observed_at, source, source_url, station_or_geocode, confidence, raw_fixture_ref}` or missing. `result["research_tags"]` is a list. `informed_pass`, `create_order`, and `dispatchable` stay false / 0. A missing settlement sets `unresolved` (`missing_settlement_fv`, `wrong_date`, `preliminary`, `station_unconfigured`, `forecast_not_final`, `research_not_settlement`).

`weather_shadow.score` uses this path when the candidate has `use_lookup` or a fixture (`cli_html`, `wx_json`, `accu_html`, `ecmwf_products`). A caller-supplied `forecast_temp` does not fill in for a failed lookup.

## Clients

- `weather_sources/nws_cli.py` — GET `forecast.weather.gov/product.php` and parse `<pre class="glossaryProduct">`. `httpx` when installed, `urllib` otherwise. No browser.
- `weather_sources/weather_com.py` — aligned JSON (`validTimeLocal` plus the value columns). Key is `WEATHER_COM_API_KEY` in the environment. Returned URLs say `apiKey={redacted}`. No key in the repo. No browser.
- `weather_sources/accuweather.py` — research only. Reads `<script id="accu-daily">`. A page without that island returns `embedded_unstable` and no number.
- `weather_sources/ecmwf.py` — catalogue names only. `value_summary` is null. CPC is `CPC_BLOCKED` HTTP 403. There is no CPC request.

## Smoke

`python3 scripts/integrations/oil_weather_shadow_tick.py --dry --cli-smoke` does one read-only NYC CLI GET. `--live` exits 2.

2026-09-23 smoke: tag `official`, maximum 67 F, model fv 0.04779, size 0, `dispatchable=0`, `create_order` false.
