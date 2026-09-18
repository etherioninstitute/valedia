# Grok Build status

**Updated:** 2026-09-17 19:22 PT

**Mode:** RuFlo MCP required (`:8810` filtered).

**Current:** SHIPPED — Slice 12: shadow parity harness (brain Coinbase + HL/Poly/intel compare.ok)

**Shipped:** Slices 1–12

**Next:** Slice 13 — MiroFish accuracy closed loop (local JSON; weight still capped)

## Slice 12 (shadow parity harness)

1. **`nautilus_shadow_parity.py`** — mockable `7d` window (`--window 7d --now ISO`). Compares shadow Intents vs the POST Valedia would/did emit: unified_brain Coinbase, HL short, Polymarket arb, intel Coinbase. Records `compare.ok` / diffs. No HTTP.
2. **Fail-closed** on cutover-dangerous mismatches: side, instrument, size, venue, signed, live hook, leverage, dispatchable, live_flag, writer=nautilus, stale `compare.ok`, corrupt JSONL. Allocator Coinbase is **ignored** as cutover evidence (still fence-scanned). Intel Alpaca/Kalshi are advisory (not NT cutover surfaces).
3. **`cutover_ready.coinbase`** requires 7 UTC calendar days of matching brain rows **and** no dangerous diffs. Report-only — this lane does not flip `VENUE_WRITER`, `--live`, `SPOT_RECONCILE_HTTP`, `REBALANCER_AUTOEXEC`, or NT flags.

**Tests:** `test_slice12.py` PASS (fixtures, no network). Slice 11 still PASS.

**Fence:** No --live, no VENUE_WRITER flip, no REBALANCER_AUTOEXEC, no NT. SPOT_RECONCILE_HTTP default False.
