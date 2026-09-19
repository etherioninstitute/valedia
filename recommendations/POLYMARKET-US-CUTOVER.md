# Polymarket US — live cutover checklist

**This lane does not enable anything.** Path only. No `VENUE_WRITER["polymarket"]` flip. No `LIVE_CLIENT_HOOK`. No US create/cancel on the wire. No wallet/spend. No trading API key changes in git. Burton flips later; Valedia runtime is the sole live risk.

Account label: `valedia.ai@gmail.com`. Docs: https://docs.polymarket.us/. SDK env names: `POLYMARKET_KEY_ID` + `POLYMARKET_SECRET_KEY`.

Int'l CLOB ≠ US DCM. Do not point this client at `clob.polymarket.com`.

## Current fence (must still be true)

| Control | Now | Live would need (Burton only) |
|---------|-----|-------------------------------|
| `VENUE_WRITER["polymarket"]` | `none` | later, one writer — **do not flip now** |
| `LIVE_CLIENT_HOOK` (CLOB) | `None` | leave None; US is not CLOB |
| `POLYMARKET_US_LIVE` | False / unset | `1` only after preview dry-run on runtime |
| `POLYMARKET_US_HTTP` | False / unset | `1` for real urllib (read or write) |
| Keys in git / worktree `.env` committed | never | keys in `~/.openclaw/.env` on **runtime only** |
| `NAUTILUS_LIVE` | False | unrelated; leave False |
| `SPOT_RECONCILE_HTTP` | False | leave False |
| `MIROFISH_AUTO_RAISE` | False | leave False |
| `VALEDIA_SELF_ADJUST` | False | leave False |

## Fill-true (H3 / POLY-E habit)

- `POST /v1/order/preview` is **not** a create and **not** a fill.
- `POST /v1/orders` ack `{id}` (even with `executions`) is **not** a fill.
- Reconcile with `GET /v1/order/{orderId}` and/or activities/trades **by exchange order id / trade id**.
- No id → do not poll (would steal others). Empty/raising GET stays `UNRECONCILED`.
- Helpers: `extract_polymarket_fill` / `resolve_polymarket_fill` / `polymarket_us_client.reconcile_us_fill`.

## What Burton must do, in order

1. KYC + API keys on US account at [polymarket.us/developer](https://polymarket.us/developer). Sign in with the **same** method as the app (Google for `valedia.ai@gmail.com`).
2. Put keys in **runtime** `~/.openclaw/.env` only:
   ```
   POLYMARKET_KEY_ID=...
   POLYMARKET_SECRET_KEY=...
   ```
   Never commit. Never paste into this worktree.
3. Fund the US account (debit / ACH / wire per docs). Funding is not a trade.
4. Read-only smoke: balances + markets (`POLYMARKET_US_HTTP=1`, `POLYMARKET_US_LIVE` unset).
5. Dry-run **preview** only (`POST /v1/order/preview`). Confirm preview ≠ create.
6. Prove GET-by-id upgrades `UNRECONCILED` → `FILLED` on a paper/sandbox or a single tiny fill **after** Burton explicitly sets `POLYMARKET_US_LIVE=1` on runtime — not in this coding lane.
7. **Do not flip** `VENUE_WRITER["polymarket"]`. One writer. Leverage stays 0. No second Kelly.

## Verify (read-only)

```
python3 -c "import valedia_config as vc, polymarket_order_body as p, polymarket_us_client as u; print(vc.VALEDIA_VERSION, vc.VENUE_WRITER['polymarket'], vc.NAUTILUS_LIVE, p.LIVE_CLIENT_HOOK, u.live_enabled())"
```

Expect: `0.34.0 none False None False`
