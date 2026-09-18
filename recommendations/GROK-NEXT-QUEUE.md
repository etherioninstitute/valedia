# Grok Build next queue (Burton 2026-09-17 evening)

Run in order. HARD FENCE throughout: no --live orders, no VENUE_WRITER flip, no REBALANCER_AUTOEXEC=True, no NT install. SPOT_RECONCILE_HTTP stays default False (S14 wires the gated path only).

## Slice 12 — Shadow parity harness — SHIPPED
7d (or mockable window) compare.ok report for brain Coinbase shadow + HL/Poly/intel shadows. No flag flips. Tests mocked. Harness: `nautilus_shadow_parity.py`. Fail-closed on side/instrument/size/venue/signed/hook/leverage/dispatchable. Allocator Coinbase ignored as cutover evidence. `cutover_ready` does not flip flags.

## Slice 13 — MiroFish accuracy closed loop (local) — SHIPPED (`f036beb`)
Fee-true FTL settlements → `mirofish_accuracy_ledger` → `effective_mirofish_weight()` capped at 10% until proven (>55% on ≥20 fee-true, or Burton bar). `MIROFISH_AUTO_RAISE=False`. Fusion/intel read ledger. Tests mocked. Local JSON only.

## Slice 14 — Burton-gated spot GET
Wire/complete Valedia-runtime getter behind SPOT_RECONCILE_HTTP default False + tests. Document single flip. Still no --live / VENUE_WRITER / REBALANCER_AUTOEXEC.
