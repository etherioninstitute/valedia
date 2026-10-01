# Kalshi CONVICTION lottery cancel — 2026-09-23

**When:** 2026-09-23 16:00 PT. **API:** `kalshi_auth.cancel_order(order_id)` → `DELETE /portfolio/events/orders/{id}`.
**Scope:** resting remainder only. Prior fills were left in place. No other tickers. No position flatten.

Account had 45 orders, **6 resting**, and those 6 were exactly the charter ids. After cancel: **0 resting**. Each of the six is `status=canceled`, `remaining_count_fp=0.00`.

| Ticker | Order id | Book | Reduced | Prior fill |
|---|---|---|---|---|
| `KXTRUMPPARDONS-29JAN21-BCJ` | `01a0cfb6-92e0-71d7-ab05-c067d882f8a5` | buy yes @ 0.16 | 3.00 | 2.00 |
| `KXGDP-27APR29-T4.0` | `01a0cedb-4640-7a75-aef5-ef2551c39214` | buy yes @ 0.17 | 2.00 | 3.00 |
| `KXINSURRECTION-29-27` | `01a0cc47-be68-7306-8551-a502a3768eb2` | buy yes @ 0.15 | 3.00 | 4.00 |
| `KXGDP-27JUL29-T0.5` | `01a0cc47-b2b0-7c3d-87a9-aa7b115e2b50` | sell yes @ 0.85 (NO) | 6.00 | 1.00 |
| `KXGDP-27JAN28-T3.5` | `01a0c56a-4840-7ec3-9727-9b58f42d3ddd` | buy yes @ 0.15 | 1.00 | 5.00 |
| `KXGDP-27APR29-T0.0` | `01a0bb54-7ec0-75ac-aea1-c17d20cf910d` | sell yes @ 0.85 (NO) | 13.00 | 1.00 |

Each DELETE returned `{order_id, reduced_by, ts_ms}` with `ok=true`. Post-list confirms `canceled` and remaining `0.00`.

Cancel omitted `?market_ticker=` because this helper signs the query string and Kalshi answers 401. Path-only DELETE succeeded.
