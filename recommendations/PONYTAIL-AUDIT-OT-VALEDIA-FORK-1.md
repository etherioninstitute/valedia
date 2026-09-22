# Ponytail audit — OT-VALEDIA-FORK-1

**Fence:** no `POLYMARKET_US_LIVE` flip, no `VENUE_WRITER` edit, no halt bypass, no wallet/ACH button, no treasury auto, no `create_order`. Valedia package stays **0.57.0** (no tag). Stock `:3000` PID 18694 not signaled. Stock API `:4010` still HTTP 200. FORK-2 not started.

**Ports:** fork web `127.0.0.1:3001`, fork market API `127.0.0.1:4011`, glance `GET http://127.0.0.1:8765/status`. Stock web `:3000` / API `:4010` left bound. LiteLLM stays `:4000`.

`reuse:` OpenTerminal widget shell (zustand desk, Workspace, Sidebar). `apiGet` + react-query. Glance fields as returned: `controls.items`, `ftl`, `broker`, `venues`.

`yagni:` POLY-LOOP, Connections, FTL charts (FORK-2). Per-fill rows (`/status` has counts only). Valedia version bump. New dependencies.

`ponytail:` one GET route, one hook, one widget file. 10s poll. Upgrade path: render a fill list when `/status` grows one. Direct browser fetch skipped so POST cannot fall through the market proxy (this route is 405).

**Proof (2026-09-21):** proxy JSON matched glance for `ftl`, `broker`, `venues`, `controls` (35 items). `POST /api/valedia/status` → 405 `GET only`. Headless DOM: v0.57.0, mode LIVE / why `writer`, HALT OFF, FTL n 0 net 0 unreconciled 3, broker net -12.62, Kalshi cash 76.66 status BROKER (pnl_tracker, not a treasury GET), Polymarket US UNREAD, `POLYMARKET_US_HTTP off`, `POLYMARKET_US_LIVE` OFF, `VALEDIA_TREASURY_AUTO` OFF. The word ACH is the treasury control note, not a button. `create_order` absent.

**Glance honesty:** mode LIVE is the existing `cockpit_mode` result (writer is `valedia` on Kalshi/Alpaca/Coinbase). This slice did not change writers or live flags. Poly writer stays `none`.
