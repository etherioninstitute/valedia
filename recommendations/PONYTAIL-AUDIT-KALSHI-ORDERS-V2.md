# Ponytail audit — KALSHI-ORDERS-V2 (0.43.0)

**Mode:** `/ponytail` full on the KALSHI-ORDERS-V2 diff only.
**Fence:** no POLYMARKET_US_LIVE, no VENUE_WRITER restore, no weather/oil unfence, no live order.

## Applied

`yagni:` new client wrapper. Mapped yes/no + buy/sell onto existing `create_order` / `sell_position`. Callers unchanged.

`yagni:` batch/amend. One POST `/portfolio/events/orders`. Cancel V2 same path DELETE. GET-by-id stays GET `/portfolio/orders/{id}`.

`yagni:` extra fill poll. V2 `fill_count==0` still uses existing `resolve_kalshi_fill` → GET fills. Nested `order.order_id` wrap keeps maker/sports.

## Findings

`shrink:` `client_order_id` not sent. Add when retries need idempotency.

**net:** one mapper + tests + latency_arb routed through `create_order`. 0 deps. 0 live flag flips. Lean enough. Ship.
