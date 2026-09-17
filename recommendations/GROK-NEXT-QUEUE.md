# Grok Build next queue (Burton 2026-09-17)

After Slice 11 ships, STOP unless Burton queues more (still HARD FENCED — no --live, no SPOT_RECONCILE_HTTP, no VENUE_WRITER flip, no REBALANCER_AUTOEXEC, no NT install):

## Slice 10 — Intel → opportunity → gated Intents
SHIPPED. MiroFish/fusion/hot_list/hedge/scanner/arb → never-dispatchable gated Intents. Paper/sandbox/backfill/shadow cannot lift Kelly.

## Slice 11 — Venue expansion shadow (HL + Polymarket)
SHIPPED. Never-dispatchable shadow Intents + POST/shape diffs for Hyperliquid shorts and Polymarket arb. `VENUE_NT_CUTOVER_ORDER = (coinbase, hyperliquid, polymarket, kalshi)`. Writer stays valedia/none. Live client hooks are stubs (`sent=False`). Tests mocked.

## Next (not started)
- NT venv / Phase 1 sandbox **only if Burton says go**.
- Do not enable live. Do not flip `VENUE_WRITER`. Do not `pip install nautilus_trader`.
