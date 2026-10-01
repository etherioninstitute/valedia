# WEATHER-MORNING-DIAGNOSIS

ts: 2026-10-01T07:08:58.141603-07:00
owner: valedia-runtime
preflight: PASS flip_allowed=True
WEATHER_LIVE_env: '1'
weather_live_enabled: True
n_markets: 360 n_alert: 1 admitted: 1 skipped_non_nws: 0
admit_reason_counts: {}
n_price: 360 n_price_null: 0 n_official_price: 12 n_fee_true_ev: 2
tags: {'wrong_date': 276, 'official': 12, 'station_unconfigured': 72}

## Why
- 1 fee-true official alerts (shadow file; create_order false).
- Admitted 1 NWS-settled official creates.
- Timing note: free NWS CLI settlement FV for calendar day D usually posts the next morning as the YESTERDAY block. Same-day open books at 7:04 AM PT often cannot be INFORMED-final yet; evening CLI-final rescan is the free-stack edge window.

