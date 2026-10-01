# Ponytail audit — OT-VALEDIA-FORK-2

**Fence:** no `POLYMARKET_US_LIVE` flip, no `VENUE_WRITER` edit, no halt bypass, no wallet/ACH button, no treasury auto, no `create_order`. Valedia package stays **0.57.0** (no tag, no package diff). Stock `:3000` PID 18694 not signaled. Stock API `:4010` still open. Screens `ot-valedia-desk` and `valedia-glance` not killed. FORK-3 not started.

**Ports:** fork web `127.0.0.1:3001`, fork market API `127.0.0.1:4011`, glance `GET http://127.0.0.1:8765/status`. Same GET proxy as FORK-1. `POST /api/valedia/status` → 405 `GET only`.

`reuse:` FORK-1 `useValediaGlance` + one status route. Sidebar catalog unchanged except three entries added above CHART. Zustand desk preset.

`yagni:` no new route, no lightweight-charts/recharts (both already installed; four bars are an SVG). No glance history series, so the sparkline is a 48-sample ring of polls. No per-fill rows. No Valedia version bump. No second fetch of `last_run.json` — the widget reads `poly_loop` on `/status`.

`ponytail:` session ring, not a ledger chart. Upgrade path: plot a server series when `/status` grows one. Versionless localStorage is stamped `version: 0` once so the three widgets append; a desk already at version 1 that removed them stays removed. `VALEDIA DESK` still resets to the full preset.

**Commit:** `2f8b412` on OpenTerminal-Valedia `main` (not pushed).

**Proof (2026-09-21):** proxy JSON matched glance for `poly_loop`, `connections`, `ftl`, `broker`, `cash`, `deployed`. Headless DOM on a fresh profile: POLY-LOOP `DRY/SHADOW` n 0 live OFF present yes `no_resolution` 20 path `data/poly-loop/last_run.json`; Connections `4/4 OK` litellm/bionic/ruflo/kalshi; FTL net 0.00, broker net -12.62, cash 76.66, deployed 57.76; SVG 4 bars + 3 polylines. Catalog still lists CHART through MARKET RECAP. `create_order` absent. ACH absent. Controls still show `POLYMARKET_US_LIVE` OFF, polymarket writer `none`, `VALEDIA_TREASURY_AUTO` OFF. `tsc --noEmit` clean. `glanceBarHeight(-12.62, 76.66, 30)` = 4.9387. Versionless persist migrate adds poly/connections/equity at y=18 and stamps version 1; a version-1 desk without them does not get them back.

**Glance honesty:** mode LIVE is still the existing writer readout. This slice did not change writers or live flags. Kalshi venue cash on the same page was a live GET (70.53 OK), separate from broker cash 76.66.
