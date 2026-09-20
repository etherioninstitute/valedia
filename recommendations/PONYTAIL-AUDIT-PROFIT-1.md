# Ponytail audit — PROFIT-1 (0.48.0)

**Mode:** `/ponytail` full on this slice only.
**Fence:** no `KALSHI_FEE_MAP_GATE=True`, no `POLYMARKET_US_LIVE`, no CB/Alpaca writers, no weather/oil unfence, no halt bypass, no wallets, no crontab.

## Applied

`reuse:` `kalshi_fee_map.fee` / `p_exec` / `consult`. No second fee formula.

`yagni:` extra module per Scout item. One `kalshi_profit.py` (EV + route + ledger).

`yagni:` CLI, cron, live GET for mids. Tests inject fills. Persist JSON on record.

`yagni:` auto demote / auto maker POST. Flags default False; annotate/shadow only.

`yagni:` class/factory/OMS. Functions + dict ledger.

## Findings

`shrink:` executor `run()` already had a maker plan. PROFIT-1 only adds fee-map `maker_viable` preference behind `KALSHI_MAKER_FIRST`.

`ponytail:` same `edge` for maker and taker (costs differ). Upgrade if rest price should change gross edge.

**net:** one helper + tests + doc. 0 deps. 0 live flag flips. Lean enough. Ship.
