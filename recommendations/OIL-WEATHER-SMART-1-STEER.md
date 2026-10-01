

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

