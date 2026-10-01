# Ponytail audit — PHASE-1 Kalshi create quality (0.58.0)

**Mode:** `/ponytail` full on this slice only.
**Fence:** no oil/weather unfence. No mass `create_order`. No `POLYMARKET_US_LIVE=True` in git. No MiroFish→Ollama. Screens `ot-valedia-desk` / `valedia-glance` not touched. Writer polymarket stays `none`.

## Applied

`reuse:` `kalshi_fee_map.fee` / `consult` / `allow` and `kalshi_profit.route`. No second fee formula. Dome is a floor add-on (`dome_extra`), not a new pricer.

`reuse:` informed-before-create remains the only create gate. Callers still do not invent FV. A missing book or slip refuses. It does not zero them.

`reuse:` fill ledger. `ledger_view` is the create check. `demote_blocks` stays the annotate skip and stays flag-gated. Empty or missing file is `empty` with `score=None`, not a fake zero.

`reuse:` GET artifact `recommendations/kalshi-fee-map.json` when the caller does not inject `fmap`. Unhealthy or missing artifact is `fee_map_refresh`. Injected maps (tests) skip that read.

`yagni:` no new env that can turn informed-before-create off. `KALSHI_FILL_LEDGER_DEMOTE` still does not gate the create refuse. Evidence does.

`yagni:` no live qty=1. No Phase 2 launch.

## Findings

`ponytail:` dome is a 2¢ tent inside 40–60¢ (`DOME_PEAK`). Upgrade path is in the comment on the constant.

`shrink:` executor/allocator `except: pass` on the fee and EV checks now fail closed (drop the opp, size 0, or `SKIP_EV_PREFLIGHT`). A refused maker place is `FAILED`, not `MAKER_PLACED`.

`shrink:` controls gained `KALSHI_FEE_MAP_REFRESH` (`source=file`) and `POLYMARKET_US_HTTP` (`env` vs `const`). Git constants for the Kalshi gates stay False.

**net:** same modules, tighter refuse. 0 deps. 0 live creates from this run. Lean enough. Ship.
