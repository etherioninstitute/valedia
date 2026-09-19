# Ponytail audit — H6 Nautilus shadow event model (0.31.0)

**Mode:** `/ponytail` full on the H6 diff only.
**Tree:** `~/.openclaw/workspace-v2`
**Scope:** `nautilus_shadow_events.py`, fill_sink + parity wire, `test_h6.py`.
**Fence:** no `--live`, no LiveNode, no `pip install nautilus_trader`, no `VENUE_WRITER` flip, no `NAUTILUS_LIVE`, no second Kelly, no crontab.

## Applied this session

`delete:` unused `event_parity_diffs` / `compact_timeline` wrappers (inspect_record is the one call). [nautilus_shadow_events.py]

`delete:` no-op dispatchable rewrite in `apply()`. [nautilus_shadow_events.py]

`shrink:` parity event hook is `inspect_record` + `event_timelines` (~10 lines). POST compare stays Slice 12. [nautilus_shadow_parity.py]

## Findings (biggest cut first)

`yagni:` `pip install nautilus_trader` / `LiveNode` / official Coinbase-HL-PM adapters. Replacement: rewrite of OrderStatus + CommandFailure into `scripts/integrations/`. **Not shipped.**

`yagni:` NT `Strategy.on_bar` / `calculate_fixed_risk_position_size`. Replacement: `risk_floor_deny` returns a reason or None. KellySizer untouched. **Not shipped.**

`yagni:` child-order vocab (`post_only` / OCO / GTD expiry) on Intent. Default-off in the dossier; no caller. **Not shipped.**

`yagni:` book/WS fixtures for latency_arb. No sockets this slice. **Not shipped.**

`yagni:` cockpit event pane / new cron / queue rewrite that auto-emits fills. Existing rows stay initialized-only unless `events` is present. **Not shipped.**

## Not cut (spec)

- Event states including `inflight_unknown` (Ambiguous ≠ denied).
- Fail-closed side/size/venue/leverage/dispatchable on the shadow timeline.
- `ORDER_FILLED_MAP` next to fill_sink (`last_qty`/`last_px`/`commission`/`trade_id`).
- Inferred fills cannot be `source=live`.

**net:** ~320-line events module, +~40 fill_sink, +~10 parity, 0 deps, writer unchanged. Lean enough. Ship.
