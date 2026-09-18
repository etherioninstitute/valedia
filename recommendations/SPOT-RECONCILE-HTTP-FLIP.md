# SPOT_RECONCILE_HTTP flip checklist (Slice 17)

**This lane does not flip the flag.** Default stays **False**. Read-only GET of *already placed* Coinbase/Alpaca orders. No POST. No `--live`. No `VENUE_WRITER`. No NT.

## What it does

When **on**, `reconcile_run.get_spot_order_getter()` returns `spot_order_get.live_spot_order_getter` instead of the mock (which always returns `None`). Cron `reconcile_run.py` (`0 */6`) then walks UNRECONCILED spot rows and GETs by `order_id`. Incomplete/error → stay UNRECONCILED. Paper/sandbox `intended_source` never upgrades to live.

## Env vs config (env wins)

| Mechanism | On | Off (shipped) |
|-----------|----|----------------|
| env | `SPOT_RECONCILE_HTTP=1` / `true` / `yes` / `on` | unset, empty, `0`, `false` |
| `valedia_config.SPOT_RECONCILE_HTTP` | `True` | **`False`** (do not edit) |

`spot_reconcile_http_enabled()` = env if set, else the constant. Cron sees env only via `cron_runner_mac.sh` sourcing `~/.openclaw/.env`. **Do not write `.env` from this lane.**

Manual one-shot (preferred first try, not cron):

```
cd ~/.openclaw/workspace/scripts/integrations
SPOT_RECONCILE_HTTP=1 python3 reconcile_run.py
```

Persistent cron flip (Burton only): add `SPOT_RECONCILE_HTTP=1` to `~/.openclaw/.env`, then the next `0 */6` job GETs. No extra `set_spot_order_getter`.

## What GET hits

| Venue | Method | Path | Caller |
|-------|--------|------|--------|
| Coinbase | GET | `/api/v3/brokerage/orders/historical/{order_id}` | `coinbase_trader.get_order` (JWT `_request`) |
| Alpaca | GET | `/v2/orders/{order_id}` | `alpaca_executor.get_order` |
| other | — | none | returns `None` |

No list-orders. No cancel. No create. Tests inject `set_spot_http_transport` and never fall through to live HTTP.

## Failure modes (fail closed → UNRECONCILED)

- Flag off / mock getter → `None` → `incomplete_fill`
- Missing `order_id` on the stub → `no_order_id`
- GET 4xx/5xx / timeout / exception → `None` → `incomplete_fill` or `getter_error:…`
- Body missing qty+price (`extract_spot_fill` incomplete) → `incomplete_fill`
- Payload `order_id` ≠ stub → `order_id_mismatch`
- Already FILLED same venue id → `already_filled`
- Unknown venue string → `None`
- `set_spot_http_transport` installed (tests) → never calls live `get_order`

Upgrade books fee-true `record_entry` (buy) or settle (sell) only when qty+price parse. Kelly still ignores `source=unreconciled` stubs.

## Revert

1. Shell: `unset SPOT_RECONCILE_HTTP`
2. `.env`: delete the `SPOT_RECONCILE_HTTP=` line (if Burton added one)
3. Config: keep `SPOT_RECONCILE_HTTP = False` in `valedia_config.py`
4. Next cron uses mock. No restart. No rollback of already-upgraded FILLED rows (append-only ledger)

Verify off:

```
python3 -c "import valedia_config as vc; assert vc.spot_reconcile_http_enabled() is False; print('OFF')"
```

## Not this flag

Does **not** enable NT, `VENUE_WRITER=nautilus`, `REBALANCER_AUTOEXEC`, weather/scalper live buys, or any order POST.
