# Ponytail audit — WEATHER-KALSHI-DOLLARS-1 (0.74.1)

**Mode:** `/ponytail` on this slice only.
**Fence:** `OIL_BRACKET` and series `KXWTI` / `KXOIL` stay fenced. `WEATHER_ARB` stays in `LIVE_EXEC_FENCED`. Lottery `0.05` / late `0.90` / INFORMED untouched. Screens `ot-valedia-desk` (783) and `valedia-glance` (17171) left running. `valedia-grok` (15022) not recycled. Live `VALEDIA_VERSION` stays `0.44.0`.

## Applied

`reuse:` Kalshi already emits `yes_bid_dollars` / `yes_ask_dollars` / `last_price_dollars` (scanner + latency_arb). One helper `kalshi_quote.yes_price` — mid when both sides quote, else last, else the one side. Zero is no quote.

`yagni:` did not rewrite `kalshi_scanner.py` or `latency_arb.py`. `weather_arb.py` already reads `yes_ask_dollars`. Oil path untouched. Live `valedia_config.py` not copied.

`yagni:` `--dry-decide` for the proof run. 19:34 cron still owns admit.

## Findings

`ponytail:` afternoon 0 alerts was a missing field name, not a missing edge.

**net:** one 50-line helper, one assignment in `_open_weather_markets`, tests, promote two files. 0 new dependencies. 0 orders. Lean enough. Ship.
