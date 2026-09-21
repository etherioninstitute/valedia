# Ponytail audit — CONTROLS-READOUT (0.49.0)

**Mode:** `/ponytail` full on this slice only.
**Fence:** no gate True in committed defaults. No Jev enable. No Poly live / writers / wallets. No halt bypass.

## Applied

`reuse:` existing `*_enabled()` / `_env_flag` / `version_identity` / `VENUE_WRITER` / cockpit `panel()`. No second config file.

`yagni:` control registry class, JSON schema, flip API, websocket. One function returns a list of dicts.

`yagni:` `--json` CLI flag. Table is the one-shot. Tests import `controls_snapshot()`.

`yagni:` wiring `jev_enabled()` into allocator. Constant + readout only.

## Findings

`shrink:` POLYMARKET_US_LIVE still env-gated in `polymarket_us_client`. Snapshot reads the same env + new False const. Do not dual-write the client this slice.

`ponytail:` halt source may be `file` (not only const/env). Lying as const would be wrong.

**net:** one function + CLI print + Controls card. 0 deps. 0 live flag flips. Lean enough. Ship.
