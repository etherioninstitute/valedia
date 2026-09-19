# POLY-LEARN — Polymarket US paper classroom (0.41.0)

**This slice does not enable live Poly.** `POLYMARKET_US_LIVE` stays False. `VENUE_WRITER["polymarket"]` stays `none`. No ACH/wire/card, no wallet, no keys in git.

Classroom: read-only market/book → **DRY/SHADOW** proposals → JSON. Preview still works. Create still blocked.

## What shipped

| Piece | Where | Live? |
|-------|-------|-------|
| Market by slug / id | `PolymarketUSClient.get_market` / `get_market_by_id` | public GET only |
| BBO / book | `get_bbo` / `get_book` + `sizing_from_bbo` | public GET only |
| Paper proposals | `poly_learn.py` → `models/poly_us_proposals.json` | **DRY/SHADOW** |
| Allocator hook | `master_allocator.run` prints POLY-LEARN; `execute_allocations` status `DRY/SHADOW` | never `create_order` |
| Cheap ears | nav classify + cached MiroFish/persona JSON (no Ollama) | gather always |
| Smoke | `poly_us_smoke.py` preview + `LiveWriteBlocked` | LIVE=0 |

HTTP reads need `POLYMARKET_US_HTTP=1` (or an injected transport in tests). Writes still need `POLYMARKET_US_LIVE=1` **and** this lane never sets that.

## Ladder to a tiny live fill (Burton only, later)

1. **Now (this slice):** classroom. Proposals file. Preview dry-run. Create raises `LiveWriteBlocked`.
2. **Keys on runtime** `~/.openclaw/.env` (`POLYMARKET_KEY_ID` / `POLYMARKET_SECRET_KEY`). Never commit.
3. **Read-only HTTP:** `POLYMARKET_US_HTTP=1`, LIVE unset. Balances + markets + BBO.
4. **Preview** (`poly_us_smoke.py`): confirm preview ≠ create.
5. **Burton flip on runtime only:** `POLYMARKET_US_LIVE=1`. One tiny qty=1 limit. **Do not flip `VENUE_WRITER["polymarket"]`.**
6. **Fill-true:** GET order/trade id. Ack ≠ fill. Empty GET stays `UNRECONCILED`.
7. **Then stop.** Writer stays `none` until a later Burton go.

Do **not** set `POLYMARKET_US_LIVE` in this tree. Do not mix Poly proposals into Kalshi live execute.

## Run (mocked / paper)

```
python3 scripts/integrations/test_poly_learn.py
python3 scripts/integrations/poly_us_smoke.py   # HTTP=1 LIVE=0; needs keys
```

## Fence (must still be true)

`POLYMARKET_US_LIVE` False · `VENUE_WRITER["polymarket"]` none · `LIVE_CLIENT_HOOK` None · no weather/oil live unfence · no CB/Alpaca writer restore.
