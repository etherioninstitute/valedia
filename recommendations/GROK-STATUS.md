# Grok Build status

**Updated:** 2026-09-17 15:40 PT

**Mode:** RuFlo MCP required (`:8810` filtered).

**Current:** SHIPPED — Slice 11: HL shorts + Polymarket arb shadow Intents (`valedia-v2-grok-build`)

**Shipped:** Slices 1–11 (Slice 10 `b5600e0` + Slice 11 `17bf2c1`)

## Slice 11 (venue expansion shadow)

1. **Hyperliquid shorts** `hyperliquid_order_body.py` + `nautilus_shadow_venues.emit_hl_short_shadow`. Never-dispatchable Slice-1 Intents (`shadow` + `live_flag=False` + `ledger_source=shadow`). Side=sell opens a short; flatten is buy + `reduce_only`. Leverage pinned at 0.
2. **POST/shape diff** vs intended `POST /exchange` action (Ioc limit — HL has no native market). `LIVE_CLIENT_HOOK=None`; `send_via_live_hook` always `{sent: False}`. Archived `hyperliquid_client.py` stays Info-only. No SDK, no keys, no un-archive.
3. **Polymarket arb** `polymarket_order_body.py` + `emit_polymarket_arb_shadow`. Same never-dispatchable pattern. Diff vs unsigned CLOB `POST /order` (FOK OrderArgs). Scan-only client / `PolymarketAdapter.ENABLED` untouched. Kalshi-only arb rows stay Slice 10.
4. **Writer lock:** `VENUE_WRITER` unchanged (`coinbase/kalshi/alpaca=valedia`, `hyperliquid/polymarket/kraken=none`). `VENUE_NT_CUTOVER_ORDER = (coinbase, hyperliquid, polymarket, kalshi)` aligns SUBSUME. NT flags flipped in tests still cannot dispatch. Allocator fail-open emit is **not** mixed into live gather.

**Tests:** `test_slice11.py` + `test_slice10.py` + `test_slice9.py` + `test_slice8.py` + `test_nautilus_intent.py` + `test_nautilus_shadow_coinbase.py` + `test_rebalancer_shadow.py` + `test_category_gate.py` PASS. No network.

**Next:** STOP for Burton. Do not flip `VENUE_WRITER` / `--live` / `SPOT_RECONCILE_HTTP` / `REBALANCER_AUTOEXEC`. NT venv / Phase 1 sandbox only if Burton says go.

**Fence:** No --live orders, no SPOT_RECONCILE_HTTP=True, no VENUE_WRITER flip, no NT install. REBALANCER_AUTOEXEC stays False. Shadow/gated Intents only.
