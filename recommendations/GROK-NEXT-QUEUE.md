# Grok Build next queue (Burton 2026-09-18 ~23:47 PT)

## Order (do not reorder)
1. **MIROFISH-SMOKE** — done `0.37.0` / `5b49cc1`
2. **PONYTAIL-FULL** — done (report-only, `PONYTAIL-AUDIT-VALEDIA-FULL.md`, version stays 0.37.0)
3. **VALEDIA-FULL-SMOKE** — done `0.38.0` (`VALEDIA-FULL-SMOKE.md` PASS, wall 3.763s)
4. **KILL-SWITCH** — done `0.39.0` / `4b7b6d3` (`KILL-SWITCH.md`, `VALEDIA_TRADING_HALT`)
5. **OPENTERMINAL-VALEDIA (Grok Build only)** — Valedia trades/P&L UI
6. **Staged ON** — Burton go after seeing results

## OPENTERMINAL-VALEDIA (Grok Build — not manual ad-hoc)
- Owner: **Grok Build** via `grok-valedia` (do not hand-hack in Valedia chat)
- Repo: `~/MemPalace/sources/repos/github/OpenTerminal` and/or `workspace-v2/vendors/OpenTerminal`
- Wire read-only Valedia panels that poll `valedia_cockpit` `http://127.0.0.1:8765/status` + FTL / pnl_tracker (label broker vs fee-true)
- Show: version/mode, cash+deployed, Kalshi/Poly balances, fee-true FTL, shadow counts, MiroFish weight, recent P&L
- **No** venue send / buy / create_order buttons
- **Design:** follow Apple HIG skills under `~/.claude/skills/hig-*` (hig-platforms, hig-components-layout, hig-components-dialogs, hig-components-status) + workspace `skills/ui-ux-pro` as secondary. Prefer macOS-native clarity: SF-style hierarchy, spacing, status colors, accessibility — not denser “Bloomberg cosplay” unless HIG still fits.
- Ponytail after code. Fence held until staged ON.

## Notes
- Live OpenTerminal market terminal may already be at localhost:3000 (API :4010); Valedia widgets are the Build deliverable.
- Signal P&L remains truthful broker tracker; UI must not pretend FTL when n=0.
