# Weather scrape endpoint map

Captured with Chrome DevTools Network (Preserve log enabled). API keys in captured Weather.com URLs are intentionally redacted here; use the key already present in the HAR only within the permitted browser/session context.

## 1. NWS NYC CLI — settlement
- **Page:** `https://forecast.weather.gov/product.php?site=OKX&issuedby=NYC&product=CLI&format=CI&version=1&glossary=0`
- **Best endpoint:** `GET` the page URL, **200**, `text/html`. The HTML contains the official NYC Central Park climate report: observed max/min temperature, precipitation, snowfall, degree days, normals, records, and report issue metadata.
- **Client:** `httpx` for the stable public HTML; parse the report table/text. Playwright is unnecessary unless the page presentation changes.
- **Tag:** settlement primary.
- **Blockers:** none observed.

## 2. NWS CLI index
- **Page:** `https://forecast.weather.gov/product_sites.php?product=CLI&site=NWS`
- **Best endpoint:** `GET` the page URL, **200**, `text/html`; lists CLI product sites/links and is useful for discovering station/product URLs. Supporting JS was `GET /build/runtime.5332280c.js`, `/build/714.d68d38be.js`, `/build/app.d4994ae4.js` (200).
- **Client:** `httpx` + HTML parser; follow the linked product pages.
- **Tag:** settlement discovery.
- **Blockers:** none observed.

## 3. Weather.com — New York research
- **Page:** `https://weather.com/us/new-york/city/new-york-city/tenday`
- **Best endpoints (all observed `GET`, **200**, `application/json`):**
  - `https://api.weather.com/v3/wx/forecast/daily/15day?geocode=40.713%2C-74.006&units=e&language=en-US&format=json&apiKey=<redacted>` — 15 daily periods; high/low, narrative, precip probability, wind, sunrise/sunset and conditions.
  - `https://api.weather.com/v3/wx/forecast/daily/7day?...` and `/daily/3day?...` — shorter daily windows.
  - `https://api.weather.com/v3/wx/forecast/hourly/2day?...` — hourly temperature, conditions, precip probability, wind.
  - `https://api.weather.com/v3/wx/forecast/fifteenminute?...` — near-term precipitation.
  - `https://api.weather.com/v1/geocode/40.713/-74.006/forecast/wwir.json?units=e&language=en-US&apiKey=<redacted>` — page weather bundle.
- **Client:** `httpx` can call the JSON endpoints if the public key remains accepted; otherwise Playwright for page JS and key/cookie behavior. Treat key as configuration, not hard-coded in reports.
- **Tag:** research.
- **Blockers:** no login or bot wall observed; API key is query-visible and may rotate/rate-limit.

## 4. AccuWeather — New York research
- **Page:** `https://www.accuweather.com/en/us/new-york/10021/10-day-weather-forecast/349727`
- **Best captured endpoint:** `GET` the page URL, **200**, `text/html`. The 10-day forecast cards/data are delivered in the rendered HTML/embedded page payload (including dates, high/low, icons/conditions and precipitation probabilities).
- **Client:** Playwright recommended for the rendered 10-day cards and any JS-generated requests; `httpx` + HTML/embedded-data parsing may work but is less stable.
- **Tag:** research.
- **Blockers:** no login/bot wall observed; no separate forecast JSON request was captured in the saved HAR (mostly the page document plus advertising requests).

## 5. ECMWF Charts — research
- **Page:** `https://charts.ecmwf.int/`
- **Best captured requests:**
  - `GET https://charts.ecmwf.int/opencharts-api/v1/packages/0.../products/` (observed as `products/` in DevTools), **200**, `application/json`; product catalogue metadata used to populate chart cards.
  - `GET .../config` (multiple **200** Fetch requests), small JSON configuration payloads.
  - Chart assets are then loaded for the selected product; the catalogue visibly included mean sea-level pressure/850-hPa wind and 500-hPa geopotential/850-hPa temperature products.
- **Client:** Playwright for catalogue interaction and chart selection; use `httpx` only after pinning the exact product/asset URL from a fresh capture.
- **Tag:** research.
- **Blockers:** no login/bot wall observed; chart data is product/run dependent. ECMWF Network screenshot captured, but no HAR export was needed/available in this quick pass.

## 6. CPC
- **Pages attempted:** `https://www.cpc.ncep.noaa.gov/`, `/products/predictions/610day/`, `/products/predictions/814day/`
- **Result:** CPC was still returning a 403 in the existing browser pass, so no usable forecast request was recorded and capture was skipped.
- **Client:** Playwright or `httpx` only after access is permitted; do not bypass the 403.
- **Tag:** research.
- **Blockers:** 403 access denial.

## Captured files
- `nws-cli-net.png`, `nws-cli.har`
- `nws-cli-index-net.png`, `nws-cli-index.har`
- `weather-com-net.png`, `weather-com.har`
- `accuweather-net.png`, `accuweather.har`
- `ecmwf-net.png`
