# WEATHER-MORNING-DIAGNOSIS

ts: 2026-09-25T20:13:53-07:00
owner: grok-build
charter: WEATHER-FIRST-LIVE-ADMIT-1
preflight: PASS flip_allowed=True
WEATHER_LIVE_env: '1' (file unchanged; Build did not write .env)
weather_live_enabled: True
trading_halted: False
n_markets: 360 n_alert: 0 admitted: 0 skipped_non_nws: 0
admit_reason_counts: {}
n_price: 360 n_price_null: 0 n_official_price: 137 n_fee_true_ev: 79
tags: {'wrong_date': 144, 'official': 137, 'near_neutral': 7, 'station_unconfigured': 72}
official_today_price_buckets: {'lottery<=0.05': 108, 'late>=0.90': 20, 'in_band': 9}
fee_true_ev_buckets: {'lottery<=0.05': 79}

## First live (WEATHER-FIRST-LIVE-ADMIT-1)

Result: no-trade. Kalshi POST: 0. Creates: 0. weather_live_ledger not appended (still 3 lines, all create_order false, mtime 2026-09-25 19:36:52 PDT).

Evening CLI-final is in. All 16 configured stations returned a non-PRELIMINARY climate summary for 2026-09-25 (issued 4:28 PM EDT through 8:35 PM EDT). Parse confidence is `final`, report date matches observe-date 2026-09-25. Yesterday's date parses `wrong_date` on those same pages. No code change: `nws_cli.parse` already accepts this evening product, and `_MAX_CREATES` is already 1 on the build tree and the live tree.

## Why no trade
- 0 fee-true official alerts inside (0.05, 0.90). `_alertable` is empty, so `nws_informed` has no row and `weather_live.admit` was not called.
- 79 fee-true rows are all YES price 0.01, bid absent, ask 0.01. That is `SKIP_LOTTERY` plus a one-sided book. Largest shadow EV is KXHIGHMIA-26SEP25-T92 (CLI high 91, fv 0.369441, px 0.01, fee_true_ev 286.9931, shadow size 800). Hard cap would be count=1 only after INFORMED clears. It does not. Same shape: KXHIGHTATL-26SEP25-T76, KXLOWTATL-26SEP25-T61, KXLOWTDAL-26SEP25-T72, KXLOWTHOU-26SEP25-T76, KXLOWTSEA-26SEP25-T54.
- 9 two-sided in-band books have YES fv below the mid, so fee_true_ev is null and size is 0. Nearest: KXLOWTSEA-26SEP25-B53.5 fv 0.130559, bid 0.11, ask 0.16, mid 0.135, CLI min 53, confidence final, observe 2026-09-25. Projected count 0.
- 20 official books are priced >= 0.90. None of them have fee_true_ev. Late gate stayed 0.90.
- 144 books are wrong_date (CLI report 2026-09-25 vs another observe-date). Not forced.
- 19:36 PDT runtime attempt KXHIGHTBOS-26SEP25-B65.5 at px 0.07 / ev 1.1846 refused `fv_source, fv_confidence, edge_missing, depth, size` (ledger line, create_order false). Rescan: same ticker is now bid 0.12 / ask 0.17 / mid 0.145, fv 0.116949, fee_true_ev null. The 7c edge is gone. Not re-sent.

## Stub regression (bridge)
KXHIGHDEN-26SEP25-T72 fixture, two-sided 0.07/0.08, `post=` stub, temp ledger. No Kalshi POST.

| | reason codes | create_order |
|---|---|---|
| Before | `fv_source`, `fv_confidence`, `edge_missing`, `depth`, `size` | false |
| After `weather_live.admit` | `[]` | True, count=1 |

Second stub call in the same process: `max_creates`, still one stub post. `test_weather_informed_admit.py` PASS. Oil `KXWTI` / `KXOIL` / `OIL_BRACKET` still `oil_fenced`. Lottery 0.05 / late 0.90 unchanged. `WEATHER_LIVE` constant stays False.

## Note
Final CLI temperatures are known integers. The scorer still uses the sigma=3 Gaussian, so a bracket the book marks at 0.835 (KXHIGHTBOS-26SEP25-B63.5, CLI high 64, fv 0.130559) does not become fee-true. This charter does not retune that model.
