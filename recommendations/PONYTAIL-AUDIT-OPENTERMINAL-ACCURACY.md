# Ponytail audit — OPENTERMINAL-ACCURACY (0.56.0)

**Fence:** no `POLYMARKET_US_LIVE`, no `VENUE_WRITER` edit, no halt bypass, no orders, no ACH. `KALSHI_FEE_MAP_GATE` not flipped in git.

`reuse:` `controls_snapshot()`, `treasury_status._live_reader` / `_parse_injected` (GET only), `version_line()`, `data/poly-loop/last_run.json`. Same `:8765` GET server.

`yagni:` new HTTP flag for Kalshi (key presence is the existing read gate). Poly stays behind `POLYMARKET_US_HTTP`. No browser auto-heal. No OpenTerminal fork.

`ponytail:` 60s balance memo and 4s socket cap. Per-poll GET if the glance must be sub-minute. 15 min cache age before `STALE`.
