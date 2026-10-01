# Ponytail audit — WEATHER-JEV-MORNING-1 (0.74.0)

**Mode:** `/ponytail` on this slice only.
**Fence:** `OIL_BRACKET` and series `KXWTI` / `KXOIL` stay fenced. `WEATHER_ARB` stays in `LIVE_EXEC_FENCED`. `WEATHER_LIVE` constant stays false. Env was not set, so `weather_live_enabled()` is false. Screens `ot-valedia-desk` (783) and `valedia-glance` (17171) not signaled. `valedia-grok` (74464) not recycled.

## Applied

`reuse:` `weather_shadow.score` still prices the bracket and the half-Kelly cap. Lookup only names the temperature source. `informed_before_create.kalshi_reasons` is the create refuse. `jev_shadow` stamps the row and does not size it.

`reuse:` fee rows are the existing artifact shape. City series were appended from Kalshi `GET /series/{ticker}` (`quadratic`, multiplier 1). No second fee formula.

`yagni:` no new dependency, no cron, no Playwright, no Weather.com key in the repo. NWS stations are the two CLI pages that returned 200 tonight (`MFL/MIA`, `VEF/LAS`) plus the existing NYC row. Chicago is still unmapped.

`yagni:` the mass allocator still returns `SKIP_LIVE_FENCE`. The only sender is `weather_live.admit`, and it returns `WEATHER_LIVE_OFF` while the env is unset.

## Findings

`ponytail:` a daily Weather.com forecast is still not a settlement. Observation or finality is required. That is the whole TWC honesty check.

`ponytail:` Jev agree/disagree is a stamp. The create gate does not read it.

**net:** one lookup change, two station rows, fee-artifact append, create refuses, a shadow alert writer. 0 new dependencies. 0 orders sent. Lean enough. Ship.
