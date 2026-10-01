# PHASE-2 — session_clock + resolve_clock + fence

**Burton 2026-09-22.** Queue after Phase 1. From Finance Scout enter/resolve briefing.

**NORTH STAR:** informed-before-create consumes **session_clock** + **resolve_clock** + **fence_gate**. One “expiry” field is wrong.

## Goal
First-class adapters so desks know **when to enter** and **when the contract ends/resolves** (and whether live-fenced).

## Priority desks (ship first)
1. Kalshi sports — schedule∩active enter; early_close/settlement resolve (≠ close_time alone)
2. Kalshi 15m crypto/gold (KXBTC15M/KXETH15M/KXGOLD15M) — window open→close; poll series for next ticker
3. Poly US — acceptingOrders / gameStart+delay enter; **UMA propose→challenge≠endDate** resolve

## Also wire (shadow/fenced ok)
- Oil/weather: clocks + fence stay **LIVE_EXEC_FENCED** (RESEARCH only)
- latency_arb / combinatorial_arb: per-leg clocks + resolve vector stubs
- Combos/MVE: collection clocks if APIs present — no live RFQ unless already wired

## Scope (do)
1. Add `session_clock(desk)`, `resolve_clock(desk)`, `fence_gate` (or equivalent modules) with typed fields from Scout matrix.
2. Informed-before-create / allocator consult both clocks before create.
3. Tests with fixtures (sports early_close, 15m window, Poly UMA vs endDate).
4. Controls/cockpit honesty: show session vs resolve vs fence per venue class.
5. Ponytail + GROK-STATUS. Version bump if package changes.

## Out of scope
- Unfencing oil/weather live
- Restoring CB/Alpaca writers
- Omni-Brain Laya firehose / GLM China ADR
- Paid Polygon/Benzinga as mandatory
- Second OMS from Hummingbot forks

## Hard fence
Same as Phase 1. No mass create_order. No Poly writer. Next=Burton when done (do not auto-chain further).

## Tools
RuFlo + CBM + Ponytail. Prefer Scout matrix in prior Valedia chat + Kalshi/Poly docs already in corpus.

## Deliverables
1. Adapters + tests + gate wiring
2. `PONYTAIL-AUDIT-PHASE-2-SESSION-RESOLVE-CLOCKS.md`
3. Results here + GROK-STATUS; **Next=Phase 3** (parent launches weather RESEARCH/shadow; this session does not)

## Done when
Sports / 15m / Poly UMA clocks are first-class; informed refuses when session inactive or resolve unknown where required; fences visible; audit written.

## Results (2026-09-22 PT)

**Status:** DONE. **Next = Phase 3.** Parent launches `PHASE-3-WEATHER-RESEARCH-SHADOW.md` (weather RESEARCH/shadow only). This session did not start it.

Version `0.59.0` (git constant bumped; not committed, not tagged). Tip before the edit `10b24aa`.

`scripts/integrations/session_clocks.py`:

- `session_clock` / `resolve_clock` / `fence_gate`. Sports enter is status active/open inside `[open_time, close_time)`. Resolve is `early_close` or `settlement`. `close_time` alone is `resolve_unknown`.
- 15m (`KXBTC15M` / `KXETH15M` / `KXGOLD15M`) enter is the open→close window. Resolve kind is `window_close`. `next_window` reads a series payload the caller already has. No network poll, no order.
- Poly US enter is `acceptingOrders`, or `gameStart` plus delay. Resolve is UMA proposal plus challenge (`uma_challenge`). `endDate` alone is `endDate_not_resolve`.
- Oil and weather clocks parse the same early-close fields and stay `mode=RESEARCH`, `live_exec=False`, reason `LIVE_EXEC_FENCED`. `WEATHER_LIVE` stays hard-off.
- `leg_clocks` is the latency/combinatorial per-leg vector. Those desks and MVE return `SKIP_CLOCK_STUB` / `no_live_rfq`. No live RFQ was added.

Informed (`kalshi_reasons` / `poly_reasons`) appends `session_inactive` or `resolve_unknown` on the three required desks, and `clock_stub` on arb/MVE. Allocator calls `before_create` after the EV preflight and before maker or taker place. A clock skip does not record a Kalshi failure.

Controls rows: `CLOCK_KALSHI_SPORTS`, `CLOCK_KALSHI_15M`, `CLOCK_POLY_US`, `CLOCK_OIL_WEATHER`, `CLOCK_ARB_MVE`. Source `const`. Running `ot-valedia-desk` and `valedia-glance` were not restarted, so the live panel still has the previous process.

Tests PASS: `test_session_clocks.py`, informed, controls, kill switch, orders v2, poly US, profit, fee-map. No live create. Oil/weather fence unchanged. Screens not killed.

Audit: `PONYTAIL-AUDIT-PHASE-2-SESSION-RESOLVE-CLOCKS.md`.
