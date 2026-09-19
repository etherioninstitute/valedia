# Grok Build next queue (Burton 2026-09-18 ~23:47 PT)

## Order (do not reorder)
1. **MIROFISH-SMOKE** — done `0.37.0` / `5b49cc1`
2. **PONYTAIL-FULL** — done (report-only, `PONYTAIL-AUDIT-VALEDIA-FULL.md`, version stays 0.37.0)
3. **VALEDIA-FULL-SMOKE** — done `0.38.0` (`VALEDIA-FULL-SMOKE.md` PASS, wall 3.763s)
4. **KILL-SWITCH** — done `0.39.0` / `4b7b6d3` (`KILL-SWITCH.md`, `VALEDIA_TRADING_HALT`)
5. **OPENTERMINAL-VALEDIA (Grok Build only)** — done `0.40.0` (`OPENTERMINAL-VALEDIA.md`, read-only glance :8765)
6. **Staged ON** — Burton go after seeing results. STOP — do not auto-flip live.

## OPENTERMINAL-VALEDIA (done 0.40.0)
- Glance: `python3 scripts/integrations/valedia_cockpit.py --listen` → `http://127.0.0.1:8765/` (JSON `/status`)
- Did **not** vendor OpenTerminal Next (`:3000` / API `:4010` left alone). Docs: `OPENTERMINAL-VALEDIA.md`
- Broker vs fee-true FTL (n=0 said plainly). Halt visible; ears stay gathering. No send/buy/create_order buttons.

## Notes
- Live OpenTerminal market terminal may already be at localhost:3000 (API :4010); Valedia widgets are the Build deliverable.
- Signal P&L remains truthful broker tracker; UI must not pretend FTL when n=0.
