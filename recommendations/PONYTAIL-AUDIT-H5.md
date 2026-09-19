# Ponytail audit — H5 hypergrok desk labels + HL public /info (0.30.0)

**Mode:** `/ponytail-audit` on the H5 diff only.
**Tree:** `~/.openclaw/workspace-v2`
**Scope:** `desk_roles.py`, `hl_public_info.py`, gatherer/shadow wire, `test_h5.py`.
**Fence:** no `--live`, no Exchange, no `/exchange`, no `VENUE_WRITER` flip, no `AUTO_RAISE`, no crontab.

## Applied this session

`shrink:` `collect_hl_public` body moved to `hl_public_info.opportunities`; gatherer is a 3-line wrapper. [intel_opportunity_gatherer.py]

`delete:` nSigFigs multi-page book walk from Galleon Labs `opening_bell.py`. One l2Book page; incomplete bands marked `*_complete=false`. [hl_public_info.py]

## Findings (biggest cut first)

`yagni:` Seven Grok Bots / Desk Lead chat persona / Grok Bot template. Replacement: four labels on shadow Intents (`research|risk|exec|review`). **Not shipped.**

`yagni:` `hyperliquid-orders` / `Exchange.order` / signed `/exchange`. Replacement: public `/info` only; `VENUE_WRITER["hyperliquid"]` stays `none`. **Not shipped.**

`yagni:` Risk Manager live `clearinghouseState` stop-% sizer. Replacement: ticket `stop=None`; KellySizer remains canonical. **Not shipped.**

`yagni:` Chat “approve HG-” send path / mainnet OMS. Replacement: `parse_approve` grammar only (`yes` is not approval). **Not shipped.**

`yagni:` Always-on HTTP / new cron for opening bell. Replacement: `HL_PUBLIC_HTTP` default off; tests inject `post_fn`. **Not shipped as cron.**

`yagni:` Watching WS to auto-trade. **Not shipped.**

## Not cut (spec)

- `desk_stage` on intel + HL-short shadow extras (lifecycle is the steal).
- Ticket twin of Intent (thin; no second sizer).
- Galleon Labs attribution on snapshot + module docs.

**net:** ~70-line desk_roles + ~220-line hl_public_info, 0 deps, writer unchanged. Lean enough. Ship.
