# WEATHER-MORNING-DIAGNOSIS

ts: 2026-09-24T15:24:06.304547-07:00
owner: valedia-runtime
preflight: PASS flip_allowed=True
WEATHER_LIVE_env: '1'
weather_live_enabled: True
n_markets: 360 n_alert: 0 admitted: 0 skipped_non_nws: 0
tags: {'wrong_date': 198, 'official': 87, 'near_neutral': 3, 'station_unconfigured': 72}

## Why
- No fee-true official alerts. INFORMED will not create without settlement FV + edge.
- 198 books wrong_date — live CLI report date ≠ market observe-date (morning CLI is usually yesterday’s final; today’s high is not final yet).
- 87 official FV rows existed but none cleared fee-true alert (edge/lottery/late filters).
- Timing note: free NWS CLI settlement FV for calendar day D usually posts the next morning as the YESTERDAY block. Same-day open books at 7:04 AM PT often cannot be INFORMED-final yet; evening CLI-final rescan is the free-stack edge window.

