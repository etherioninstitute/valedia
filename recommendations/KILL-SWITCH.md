# VALEDIA_TRADING_HALT — master kill-switch (0.39.0)

**Updated:** 2026-09-19 PT
**Fence:** code/audit only. No `--live`, no ACH/wire/card, no wallet/venue transfer, no API-key edits, no writer/AUTOEXEC/US_LIVE/SPOT/NT/SELF_ADJUST/AUTO_RAISE/treasury flips. Valedia runtime remains sole live risk.

## Where it sits

One helper: `valedia_config.require_not_halted(action)` raises `HaltedError`.

Callers (do not add ad-hoc checks):

- **Writers:** `kalshi_auth.create_order` / `sell_position`; `polymarket_us_client._require_live` (create/cancel); `coinbase_trader.market_buy` / `market_sell`; `alpaca_executor.market_buy` / `market_sell`
- **Layers:** `execution_policy.MakerOrderManager._place`; `master_allocator.execute_allocations` (status `HALTED`, no blacklist); `rebalancer.rebalancer_may_exec` → False
- **Readout:** `valedia_cockpit.panel()` fields `trading_halt` / `trading_halt_reason`; full-smoke fence extra `trading_halt`

`trading_halted()` reads **env first**, then `models/valedia_trading_halt.json`, then the config constant (default False).

## Arm

```bash
export VALEDIA_TRADING_HALT=1
# or persist for cron/other processes (no crontab edit):
python3 scripts/integrations/valedia_trading_halt.py on "chain sick"
```

Truthy env: `1` / `true` / `yes` / `on`.

## Disarm (manual OFF always works)

```bash
unset VALEDIA_TRADING_HALT
# or explicit:
export VALEDIA_TRADING_HALT=0
python3 scripts/integrations/valedia_trading_halt.py off
```

`0` / `false` / `off` **wins over the halt file**. No auto-spend to clear. No Signal send.

Status: `python3 scripts/integrations/valedia_trading_halt.py status`

## What it blocks

New venue **orders**: Kalshi create/sell, Poly US create/cancel (live path), Coinbase/Alpaca market buy/sell, maker `_place`, allocator live execute, rebalancer POST.

## What it does NOT block

GET/balances/positions/fills/orders, Poly `preview_order`, cockpit GET, treasury report, shadow Intents, dry-run allocator, cancel of Kalshi resting orders, reads used by smoke/P&L.

## Self-heal (report-only this slice)

`record_halt_event(reason, source, arm=False)` writes `models/valedia_trading_halt.json` + `recommendations/KILL-SWITCH-STATUS.md` ping payload (`send: false`). Valedia/Grok Bot deliver Signal — this lane does not. `arm=True` sets halt; default does not. `panel_looks_sick` / `note_writer_exception` are hooks; fail-closed on unreadable halt file **unless** env is an explicit OFF.

## Tests

```bash
python3 scripts/integrations/test_kill_switch.py
```

**Ears always on (Burton 2026-09-19):** MiroFish (signal/swarm/ledger/neural enrich), persona lenses, fortress intel reads, NT research tags, and any other *data* streams must keep gathering whether `VALEDIA_TRADING_HALT` is on or off. Halt blocks **new orders only** — never intel collection. The OpenTerminal glance (`OPENTERMINAL-VALEDIA.md`) shows `trading_halt` but `ears.gathering` stays true — never imply ears are off.
