# Ponytail audit — FIX-BROKEN-CONNECTIONS

**Mode:** `/ponytail` full on this slice only.
**Fence:** no `POLYMARKET_US_LIVE` / writers / wallets / HALT / `WEATHER_LIVE` flips. Oil/weather stay `LIVE_EXEC_FENCED`. Screens `ot-valedia-desk` (783) and `valedia-glance` (755) not signaled. Constant `KALSHI_FEE_MAP_GATE = False`.

## Applied

`reuse:` copy `kalshi_fee_map.py` + GET artifact from workspace-v2 into live `~/.openclaw/workspace`. Cron stays on `workspace` (path A). No crontab rewrite.

`reuse:` live `_env_flag`. One helper `kalshi_fee_map_gate_enabled()`. Call sites match v2 allocator/executor/sports `allow()` drops.

`yagni:` no `kalshi_profit` on live (would fail-closed drop all scores). No GET wrappers on live `kalshi_auth`. No `--get` (artifact already `sports_unknown=0`). No version bump. No LiteLLM rewrite.

`yagni:` config.yaml `public_routes` is premium-gated. Localhost `/health` + `/v1/models` added to `LiteLLMRoutes.public_routes` so the unauthenticated proof curl lists aliases.

## Findings

`ponytail:` `current_map()` on live loads the GET artifact when healthy. PDF-only map would mark sports unknown and drop them with the gate on.

`ponytail:` executor `except` stays fail-open on live (v2 fail-closes). Import/artifact bugs do not zero every score.

**net:** prisma 0.15.0 in miniconda base + three public routes. Live gate honors env=1. 0 new deps in Valedia. 0 live lever flips. Lean enough. Ship.
