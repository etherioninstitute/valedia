# OPENTERMINAL-VALEDIA-WORKSPACE — duplicate OpenTerminal, fully adapt for Valedia

**Updated:** 2026-09-21 PT  
**Burton decision:** Duplicate OpenTerminal and fully adapt Valedia to it (do not merely inject a few widgets into the stock `:3000` app).

## Trees

| Path | Role |
|------|------|
| `~/MemPalace/sources/repos/github/OpenTerminal` | Upstream / stock market UI — keep on **:3000** |
| `~/MemPalace/sources/repos/github/OpenTerminal-Valedia` | **Valedia fork** — widget shell + Valedia-first modules |
| `~/.openclaw/workspace-v2` `valedia_cockpit.py` **:8765** | JSON source of truth + thin glance during transition |

## Goal UX

Same OpenTerminal capabilities: Add Widget, resize/enable/disable windows, charts, news, watchlist, etc. Plus Valedia-native widgets:

- Controls (`controls_snapshot`, read-only)
- All Trades (fee-true FTL + UNRECONCILED)
- Venues / cash (Kalshi, Poly US, …)
- POLY-LOOP desk
- Connections health
- FTL / equity graphs

Default layout preset: **Valedia trading desk** (not AAPL demo).

## Fence

GET-only for gates/orders. No create_order / ACH / wallet / live flips from UI. Valedia runtime remains sole live risk. Ponytail + RuFlo on Builds.

## Build sequence (do not kill in-flight FOMO-RADAR-DOSSIER)

1. **FOMO-RADAR-DOSSIER** (in flight) → finish
2. **OPENTERMINAL-ACCURACY** — honest `:8765` `/status` (feeds widgets)
3. **OT-VALEDIA-FORK-1** — boot OpenTerminal-Valedia on a free port (e.g. **8766** or **3001**), rebrand, Valedia preset, Controls + All Trades + Venues widgets wired to Valedia `/status`
4. **OT-VALEDIA-FORK-2** — POLY-LOOP, Connections, FTL charts, keep all OT add-widget catalog working

## Success

Burton opens Valedia’s OpenTerminal fork, gets full OT widget UX, and every Valedia control/trade/venue is accurate — stock OpenTerminal on `:3000` still works unchanged.
