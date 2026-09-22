# Ponytail audit — POLY-LOOP-FV (0.54.0)

**Mode:** `/ponytail` full on this slice only.
**Fence:** `POLYMARKET_US_LIVE` stays False. Writer polymarket `none`. No weather/oil/CB/Alpaca/treasury/nautilus live. No crontab. No wallets.

## Applied

`reuse:` `resolution_source` gate, `_load_json` / `cheap_tags` / `classify_market`, `modeled_prob.ema_fair` (same EMA as `modeled_prob_up`). Same skip/size/ledger path.

`yagni:` Ollama/TypeSafe FV. New HTTP host. Per-question invented priors. Using `modeled_prob_up` P(up) as a yes-prob. Top-level cache `p_yes` applied to every row.

`yagni:` numpy in `poly_loop` — optional import of `ema_fair` only when a ≥20-point series exists.

## Findings

`ponytail:` US payloads omit `fairValue`; caches are crypto-regime files, not per-slug. Fuzzy key match + 50/50 mid-anchored tag_prior (classified or NWS/MLB/Fed label) is the desk path. Unclassified `rules_text` still `no_fv`.

**net:** one helper stack + blotter JSON + tests + doc. 0 deps. 0 live flag flips. Lean enough. Ship.
