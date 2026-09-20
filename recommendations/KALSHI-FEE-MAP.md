# KALSHI-FEE-MAP (2026-09-19)

Series fee map for Valedia's Kalshi path. Allocator/strategies can gate **maker-viable** vs **taker-taxed**. GET-only. No live flip, no orders, no wallet/key changes, no `VALEDIA_TRADING_HALT` bypass.

**Version:** `0.46.0` (refresh CLI + artifact; gate still False)

## Discovery (CBM)

| What | Where |
|------|--------|
| V2 client | `kalshi_auth.get` → `BASE=/trade-api/v2` (KALSHI-ORDERS-V2) |
| Active series list | `kalshi_executor.ALL_SERIES` + `get('/markets?series_ticker=&status=open')` |
| Weather series | `weather_arb.discover_weather_series` (HTTP-off / fenced) |
| Fee helpers | `fee_engine.kalshi_fee_per_contract` (0.07·P·(1−P), no M); `forward_test_ledger.kalshi_fee` (ceil-cent, no M) |
| `fee_changes` | **not present** before this slice |

## Client (existing V2)

```
GET /series/fee_changes?series_ticker=…&show_historical=true
GET /events/fee_changes?event_ticker=…
GET /series/{series_ticker}
```

Wrappers on `kalshi_auth.get`: `get_series_fee_changes`, `get_event_fee_changes`, `get_series`. Same RSA-PSS path as orders V2. Tests mock `get` — no network.

## Map

`scripts/integrations/kalshi_fee_map.py`

`series_ticker → maker_multiplier, taker_multiplier, maker_viable, taker_taxed, notes, as_of, source, status`

- **PDF join:** first non-standard page of [kalshi-fee-schedule.pdf](https://kalshi.com/docs/kalshi-fee-schedule.pdf) effective **2026-07-07** (14 rows). Later pages are two-column garbled — not scraped.
- **API overlay:** latest `scheduled_ts` ≤ now wins. Future rows ignored.
- **Unknown:** `status=unknown`, `maker_viable=False`, `taker_taxed=True` (fail-closed fields).
- **maker_viable:** `maker_multiplier == 0` (PDF default: resting free).
- **taker_taxed:** `taker_multiplier > 1` (above-standard tax). Standard taker M=1 is **not** extra-taxed.

`fee_type` → multipliers: `quadratic` maker 0 / taker fm; `quadratic_with_maker_fees` both fm; `quadratic_with_combo_maker_fees` maker 2·fm / taker fm.

## Formula

```
taker = round_up_cent(M × 0.07 × C × P × (1−P))
maker = round_up_cent(M × 0.0175 × C × P × (1−P))
```

PDF prose says fee+positionCost to a **centicent**; the published 1-contract / 100-contract table matches **ceil to the next cent** of the fee (same as existing `kalshi_fee`). `fee(P,C,M)` pins that table.

**P is executable.** `p_exec(bid, ask, action, slip)` = ask+slip (buy) or bid−slip (sell). Never last trade alone. Half-spread is `ask-bid`; pass it as `slip` if you want mid→touch+buffer.

Live `forward_test_ledger.kalshi_fee` / `fee_engine.kalshi_fee_per_contract` **unchanged** (no M). New helper is additive.

## Gate

`KALSHI_FEE_MAP_GATE` default **False** (this lane never sets True). Env `KALSHI_FEE_MAP_GATE=1` to drop unknown + taker_taxed at select/size.

Consult always annotates `fee_map` on the opp.

Hooked (no new live strategies):

- `kalshi_executor._score_market` (select) + `_size_contracts` (size)
- `master_allocator.allocate` (Kalshi / kalshi_scanner)
- `sports_engine.size_bets` (size; `series` stored on the game)

Gate off: annotate, never drop. Gate on: fail-closed unknown + extra-taxed.

## Refresh (AUTONOMY-1)

```bash
python3 scripts/integrations/kalshi_fee_map.py --dry
python3 scripts/integrations/kalshi_fee_map.py --get   # GET only; writes recommendations/kalshi-fee-map.json
```

`--dry` = PDF join, no network. `--get` = bulk `fee_changes` then `GET /series/{ticker}` for remaining targets (Valedia `SERIES_*` + sports). Never sets the gate.

Flip steps: `recommendations/KALSHI-FEE-MAP-GATE-CHECKLIST.md`.

## Out of scope (held)

Poly live, oil unfence, sports geo bypass, ACH, writer changes, halt bypass.
