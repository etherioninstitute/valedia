# Grok Build status

**NORTH STAR:** world's smartest, highest-profiting DYNAMIC auto-trader.

**Updated:** 2026-09-19 PT

**Mode:** RuFlo + Ponytail. Hard fence held.

**Current:** KALSHI-ORDERS-V2 SHIPPED. Next=Burton re-run live allocator.

**Version:** `0.43.0` tag `valedia-v0.43.0` tip `db21c91`. Prior: `0.42.0` `02298ad`.

**Tests:** `test_kalshi_orders_v2.py` PASS (yes→bid, no→ask at 1-price, sell maps, cancel V2, V2 ack fill_count 0 ≠ fill). Also h3, kill-switch, scan_accuracy, slice8/9/23, execution_policy, poly_learn.

**Ponytail:** `recommendations/PONYTAIL-AUDIT-KALSHI-ORDERS-V2.md` (thin). No new client; mapped onto existing create/sell.

**Tool use:** RuFlo `hooks_route`/`hooks_pre-task`/`hooks_pre-edit` (architect/coder/tester). CBM `list_projects` + `search_graph` `create_order`/`sell_position` + `trace_path` inbound + `get_code_snippet` + `check_index_coverage`. Ponytail after code (no batch/amend, no client_order_id).

**Fence:** No live orders in Build. No ACH/wire/card. No `POLYMARKET_US_LIVE`. No CB/Alpaca writer restore (live `valedia_config` not wholesale-copied). No weather/oil unfence. MiroFish ears on. FENCED.

**Next:** Burton re-run live allocator. Do not auto-flip `POLYMARKET_US_LIVE`. Do not unfence weather/oil.
