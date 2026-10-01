# Ponytail audit — WEATHER-INFORMED-ADMIT-BRIDGE-1

**Mode:** `/ponytail` on this slice only.
**Fence:** `OIL_BRACKET` and series `KXWTI` / `KXOIL` stay fenced. Lottery `0.05` / late `0.90` / `ALLOWED_FV` untouched. `WEATHER_LIVE` not flipped. Screens `ot-valedia-desk` and `valedia-glance` left running.

## Applied

`reuse:` `kalshi_quote.quote`, `weather_shadow._alertable`, `informed_before_create.kalshi_reasons` / `kelly_contracts`. One helper `weather_live.nws_informed` remaps `nws|nws_cli` → `rules_clock` on the admit payload. Global `ALLOWED_FV` not widened.

`yagni:` no new module, no fee-map edit, no oil path, no version bump, no second market GET inside the helper. Morning-go already holds the book. Missing bid stays missing.

`ponytail:` process counter `_creates` caps sends at 1. `pick_admit` attempts one row (best `fee_true_ev`).

## Findings

`ponytail:` the five refuses were unmapped shadow fields, not a bad FV. Live book at proof time was one-sided at 1c — inventing a bid would have been the bug.

**net:** helper in `weather_live.py`, cap + reason counts in `weather_morning_go.py`, three ledger keys, one test. 0 new dependencies. 0 orders. Lean enough. Ship.
