# Ponytail audit — AUTONOMY-1 FEE-MAP-REFRESH (0.46.0)

**Mode:** `/ponytail` full on this slice only.
**Fence:** no POLYMARKET_US_LIVE, no CB/Alpaca writers, no weather/oil unfence, no live order, no halt bypass, `KALSHI_FEE_MAP_GATE` stays False in config.

## Applied

`yagni:` new client. Reused `kalshi_auth.get_series_fee_changes` + `get_series`.

`yagni:` import `kalshi_executor` (binds writers). Parse `SERIES_*` source text instead.

`yagni:` event-ticker GET. Sports keys series; add when an event override bites.

`yagni:` retry/backoff on the five HTTPError tickers. Fail-closed unknown; checklist names them.

`yagni:` auto-load artifact in `current_map`. CLI persist is the proof; process `refresh()` stays opt-in.

`yagni:` committed `KALSHI_FEE_MAP_GATE=True`. Checklist only.

## Findings

`shrink:` bulk `/series/fee_changes` returns extra series (artifact map n=169 vs 79 targets). Harmless overlay; classify still counts targets.

Sports are `quadratic_with_maker_fees` (not maker-free). Gate drop rule is unknown + taker>1, so sports still pass.

**net:** helpers on existing module + `__main__` CLI + JSON artifact + checklist. 0 deps. 0 live flag flips. Lean enough. Ship.
