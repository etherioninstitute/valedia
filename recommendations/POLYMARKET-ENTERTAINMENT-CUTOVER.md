# Polymarket entertainment / Oscars — live cutover checklist

**This lane does not enable anything.** Path only. No `VENUE_WRITER["polymarket"]` flip. No `LIVE_CLIENT_HOOK`. No CLOB POST. No wallet/spend. No category un-park that places orders. Burton flips later; Valedia runtime is the sole live risk.

Scan + shadow Intents ship in `0.33.0` (`polymarket_entertainment.py`). Nav buckets: Sports, Politics, Crypto, Weather, Tech, Culture/Oscars, Econ. Gatherer tags + category aliases. Writer stays `none`.

## Current fence (must still be true)

| Control | Now | Live would need (Burton only) |
|---------|-----|-------------------------------|
| `VENUE_WRITER["polymarket"]` | `none` | `nautilus` (never `valedia` — no PM writer today) |
| `LIVE_CLIENT_HOOK` (PM) | `None` | NT / py-clob-client adapter object |
| `NAUTILUS_ENABLED` | False | True **and** writer=nautilus |
| `NAUTILUS_LIVE` | False | True before `source=live` NT fills |
| Category `ENTERTAINMENT` | shadow / unproven | not in `category_overrides.json` parked; fee-true sample before size-up |
| Caps | Slice-10 `SHADOW_NOTIONAL` $5 | Kelly/daily fuses + a PM per-trade cap Burton names |
| `SPOT_RECONCILE_HTTP` | False | unrelated; leave False |
| `MIROFISH_AUTO_RAISE` | False | leave False |
| `VALEDIA_SELF_ADJUST` | False | leave False |
| `FORTRESS_INTEL_FALLBACK` | False | unrelated hygiene; leave False |

`nautilus_may_dispatch("polymarket")` is False unless ENABLED **and** writer==nautilus.

## Fill-true (H3 habit)

POST `/order` ack is **not** a fill (`status=matched` included). Reconcile with GET trades/orders **by `order_id` / `trade_id`**. No id → do not poll (would steal others). Empty/raising GET stays `UNRECONCILED`. Helpers: `extract_polymarket_fill` / `resolve_polymarket_fill` / `book_polymarket_fill`.

## What Burton must flip, in order

1. Watch shadow queue: `kind=polymarket_entertainment`, `dispatchable=false`, unsigned CLOB FOK body matches Intent.
2. Optional: local Gamma scan into `models/polymarket_entertainment.json` (inject `get_fn`; this lane has no default HTTP).
3. Isolated NT / CLOB adapter. No keys in the coding worktree.
4. Wallet + CLOB credentials on **Valedia runtime only**.
5. Prove GET-by-id upgrades UNRECONCILED → FILLED on paper/sandbox first.
6. **Do not flip** `VENUE_WRITER["polymarket"]` until Coinbase→HL order in `VENUE_NT_CUTOVER_ORDER` is respected, or Burton explicitly overrides.
7. One writer. Never two. Leverage stays 0. No second Kelly.

## Verify (read-only)

```
python3 -c "import valedia_config as vc, polymarket_order_body as p; print(vc.VALEDIA_VERSION, vc.VENUE_WRITER['polymarket'], vc.NAUTILUS_LIVE, p.LIVE_CLIENT_HOOK, vc.MIROFISH_AUTO_RAISE)"
```

Expect: `0.33.0 none False None False`
