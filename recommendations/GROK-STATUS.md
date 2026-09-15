# Grok Build status

**Updated:** 2026-09-15 01:18 PT

**Mode:** RuFlo MCP required (`:8810` filtered). Unfiltered claude-flow disabled.

**Current:** Slice 2 SHIPPED — Coinbase shadow Intents (writer stays `valedia`)

**Last done:** Slice 2 — allocator Coinbase emits never-dispatchable shadow Intents + POST-body diff; UNRECONCILED ledger hook; tests PASS. Slice 1 remains `e385c59`.

**Goal:** Maximum profit under gates. Shadow-compare allocator Coinbase path vs Intent shape — **do not dispatch**, do not cut over writer, do not install NT.

**Shipped (Slice 2):**
- `nautilus_shadow_coinbase.py` — `emit_allocator_shadow()` from allocator Coinbase branch; `dispatch=False`; `live_flag=False`; `ledger_source=shadow`; JSONL `shadow=true`
- Even if `NAUTILUS_ENABLED` + `VENUE_WRITER[coinbase]=nautilus`, shadow enqueue is `status=held`, `dispatchable=false`
- `compare_intent_to_post()` vs `coinbase_order_body.build_market_order_body` (same POST `coinbase_trader` would send)
- `master_allocator` Coinbase branch: shadow first, existing Valedia writer unchanged
- `record_unreconciled()` for Coinbase/Alpaca **after EXECUTED** — excluded from Kelly / `real_track_record`
- Tests: `test_nautilus_intent.py` PASS; `test_nautilus_shadow_coinbase.py` PASS
- Fence held: no `--live`, no venue POST from this slice, no NT wheel, `VENUE_WRITER[coinbase]=valedia`

**Findings (pre-cutover mismatches — do not “fix” by adding a live `place_market_order`):**
- Allocator calls `place_market_order` — **not on** `coinbase_trader` (`market_buy` / `market_sell` only). Coinbase allocator branch is currently dead (ImportError), not a second live writer.
- Allocator strips `-USD` (`BTC-USD` → `BTC`); trader POST needs `product_id=BTC-USD`
- Allocator always passes USD `allocation`; sells need `base_size` (qty), not quote notional
- Working Coinbase writer today is `unified_brain` (`market_buy` / `market_sell`), not the allocator

**Next (still no NT install, no writer cutover):**
- Shadow the **working** Coinbase path (`unified_brain` buy/sell) the same way — that is the live writer to compare before Phase 2
- Do **not** add `place_market_order` / do **not** unstick allocator POSTs from this lane
- Do **not** flip `VENUE_WRITER[coinbase]`
- Parallel Valedia P&L (not NT): sports B1 ledger-truth remains higher immediate compounding than NT Phase 1 sandbox

**Blockers:** none
