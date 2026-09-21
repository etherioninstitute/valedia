# Ponytail audit — POLY-LOOP (0.51.0)

**Mode:** `/ponytail` full on this slice only.
**Fence:** `POLYMARKET_US_LIVE` stays False. Writer polymarket `none`. No weather/oil/CB/Alpaca/treasury/nautilus live. No crontab.

## Applied

`reuse:` `poly_learn.cheap_tags` / `_market_rows`, `sizing_from_bbo`, `get_book`/`get_bbo`, `quant_engine.kelly_size` + `KELLY_SAFETY_FRACTION`, `controls_snapshot` / `_env_flag`.

`yagni:` second writer / OMS / Ollama FV. Numeric FV only if market/tags already carry it; resolution source is a gate.

`yagni:` mixing into `gather_all_opportunities`. Status line on allocator + poly_learn print.

`yagni:` crontab. Flag file noted; not installed.

`yagni:` withdraw path. Self-fund JSON only.

## Findings

`ponytail:` flatten walks L2 qty; BBO-only with no depth → BLOCKED (fail closed). Upgrade if US BBO grows size fields.

**net:** one module + tests + doc. 0 deps. 0 live flag flips. Lean enough. Ship.
