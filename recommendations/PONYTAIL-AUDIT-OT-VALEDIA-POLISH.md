# Ponytail audit — OT-VALEDIA-POLISH

**Fence:** no `POLYMARKET_US_LIVE` flip, no `VENUE_WRITER` edit, no halt bypass, no wallet/ACH button, no treasury auto, no `create_order`. Valedia package stays **0.57.0** (no tag, no package diff). Stock `:3000` PID 18694 not signaled. Stock API `:4010` PID 18692 still open. Screens `ot-valedia-desk` and `valedia-glance` not killed. No next slice started.

**Ports:** fork web `127.0.0.1:3001` PID 1639 (`next dev`, hot reload), fork market API `127.0.0.1:4011` PID 1633, glance `GET http://127.0.0.1:8765/status` PID 764. `POST /api/valedia/status` → 405 `GET only`.

`reuse:` FORK-2 widgets, sidebar catalog, GET proxy, `data-table`. Grouping is a pure function over the glance `category` field. Extra categories (writer, nautilus, treasury, exec, …) fold into fence. Native `<details>` for the const rows. No new dependency.

`yagni:` no Valedia version bump, no second fetch, no layout resize, no color system for the stock OpenTerminal widgets beyond the shared desk tokens. No mobile breakpoint. The desk is the existing 12-column grid.

`ponytail:` quiet Poly unread matches the glance phrase `polymarket_us_http off`. If that phrase changes, the row goes red again. Scan rows that stay open when `source=const`: version, halt, `POLYMARKET_US_LIVE`, `INFORMED_BEFORE_CREATE`, `NAUTILUS_LIVE`, `VALEDIA_TREASURY_AUTO`, and every `VENUE_WRITER.*`. Other const rows start closed.

**Commit:** `f33c6e6` on OpenTerminal-Valedia `main` (not pushed; parent `2f8b412`).

**Proof (2026-09-21):** `tsx web/lib/valediaPolish.check.ts` ok. `tsc --noEmit` clean. Headless DOM before: 35 flat control rows, Polymarket US `UNREAD · STALE` plus `file missing`. After, same PIDs, five closed `<details>` (`const 1`, `const 3`, `const 5`, `const 2`, `const 10`), group labels identity / halt / kalshi / poly / autonomy / fence. Polymarket line is `<div class="v-quiet">UNREAD · HTTP off</div>`. Text counts: `STALE` 0, `file missing` 0, `VENUE_WRITER.polymarket` 1, `POLYMARKET_US_LIVE` 1, `DRY/SHADOW` 1, `4/4 OK` 1, catalog `CHART` through `MARKET RECAP`. The only `class="down"` left on the page is `NYSE CLOSED`. `create_order` absent. ACH absent. Figures: FTL net 0.00, broker net -12.62, cash 76.66, Kalshi cash 70.53. Contrast: body text 14.88:1 → 18.93:1 (`#f3f3f3` on black); dim 5.32:1 → 7.43:1 (`#a3a3a3` on `#121212`); figures `#fff` at 15px, 18.73:1. Desktop screenshot 1440×1200 on the running desk.

**Glance honesty:** mode LIVE is still the existing writer readout. This slice did not change writers or live flags.
