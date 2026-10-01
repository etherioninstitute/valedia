# Max env flip — 2026-09-23 ~11:40 PT (Burton widget)

## Flipped (runtime env in `~/.openclaw/.env`)
POLYMARKET_US_HTTP=1, POLYMARKET_US_LIVE=1, VALEDIA_JEV=1,
KALSHI_FEE_MAP_GATE=1, KALSHI_EV_PREFLIGHT_GATE=1, KALSHI_MAKER_FIRST=1,
KALSHI_FILL_LEDGER_DEMOTE=1, VALEDIA_SELF_ADJUST=1,
SPOT_RECONCILE_HTTP=1, FORTRESS_INTEL_FALLBACK=1, REBALANCER_AUTOEXEC=1,
ETORO_LIVE=1, ETORO_UNLOCK=burton-documented, ETORO_MODE=live.

## Working-tree only (not committed)
`VENUE_WRITER["polymarket"] = "valedia"` in `valedia_config.py`.

## Explicitly refused
WEATHER_LIVE=0 / const False, LIVE_EXEC_FENCED oil+weather held,
VALEDIA_TREASURY_AUTO=0, kraken/HL writers still none,
NAUTILUS_ENABLED/LIVE still False (const, not env-reachable),
MIROFISH_AUTO_RAISE still False const (SELF_ADJUST wakes in-band only).

## eToro honesty
Gate returns `write_endpoint`. `start_copy` / `create_order` still raise
(no sender). Controls row `ETORO_MODE` still labels PAPER/SHADOW (display).

## Backup
`~/.openclaw/.env.bak-pre-maxflip-*`
