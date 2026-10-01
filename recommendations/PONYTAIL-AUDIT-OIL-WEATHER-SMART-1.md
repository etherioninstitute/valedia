# Ponytail audit — OIL-WEATHER-SMART-1 (0.72.0)

**Mode:** `/ponytail` full on this slice only.
**Fence:** `WEATHER_ARB` and `OIL_BRACKET` stay `LIVE_EXEC_FENCED`. Series `KXWTI` and `KXOIL` stay fenced. `WEATHER_LIVE` stays False. `dispatchable=0`. No `create_order` restore. Screens `ot-valedia-desk` (783) and `valedia-glance` (17171) not signaled. `valedia-grok` (35929) is this session.

## Applied

`reuse:` `weather_shadow` fee quote, half-Kelly cap, and the Gaussian bracket. Oil does not grow a second pricer.

`reuse:` `session_clocks.fence_gate` and INFORMED `live_fence` / `oil_no_harvest`. Oil's mode label is now `RESEARCH/SHADOW`. The refuse reason is still `LIVE_EXEC_FENCED`.

`reuse:` `forward_test_ledger._is_non_real_source`. Would-have rows are `source=shadow` on the desk ledger. They are not appended to the production ledger. `reconcile` is not called.

`yagni:` no cron install. One commented line in `oil_weather_shadow_tick.py`. `--live` exits 2.

`yagni:` `threshold_hint.apply` is always false. Shadow hit-rate does not move Kelly or `category_stats`.

`yagni:` no CME scrape and no Kalshi client import. Oil FV is a caller-supplied print plus `rules_cite`.

## Findings

`ponytail:` forecast oil uses a fixed $1.50 sigma. Upgrade when a cited contract rule names the tolerance. A `print_kind=settlement` row is a step at the strike, clamped to 0.01/0.99.

`ponytail:` shadow history is a 200-row ring. Upgrade to a real store when the file is no longer the log.

**net:** one oil scorer, a thin NWS extract, a shared ledger append. 0 new dependencies. 0 live oil or weather creates from this run. Lean enough. Ship.
