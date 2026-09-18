# Grok Build next queue (Burton 2026-09-17 evening)

Run in order. HARD FENCE throughout: no --live orders, no VENUE_WRITER flip, no REBALANCER_AUTOEXEC=True, no NT install. SPOT_RECONCILE_HTTP stays default False (S14 wires the gated path only).

## Slice 12 — Shadow parity harness — SHIPPED
7d (or mockable window) compare.ok report for brain Coinbase shadow + HL/Poly/intel shadows. No flag flips. Tests mocked. Harness: `nautilus_shadow_parity.py`. Fail-closed on side/instrument/size/venue/signed/hook/leverage/dispatchable. Allocator Coinbase ignored as cutover evidence. `cutover_ready` does not flip flags.

## Slice 13 — MiroFish accuracy closed loop (local)
Fee-true outcomes → accuracy ledger → weight still capped (≤10–15%) until proven (>55% or Burton-set bar). No HTTP Intent path. Local JSON only.

## Slice 14 — Burton-gated spot GET
Wire/complete Valedia-runtime getter behind SPOT_RECONCILE_HTTP default False + tests. Document single flip. Still no --live / VENUE_WRITER / REBALANCER_AUTOEXEC.
