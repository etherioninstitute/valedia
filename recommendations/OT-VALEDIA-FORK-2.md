# OT-VALEDIA-FORK-2

**Launched:** 2026-09-21 PT (after FORK-1 desk restore)
**Tree:** `~/MemPalace/sources/repos/github/OpenTerminal-Valedia` (tip `8de945b` at start)
**Desk already up:** screen `ot-valedia-desk` → web `127.0.0.1:3001`, API `127.0.0.1:4011`
**Glance:** screen `valedia-glance` → `python3 scripts/integrations/valedia_cockpit.py --listen` on `:8765`

## Scope
POLY-LOOP desk widget, Connections health, FTL/equity charts, keep full OpenTerminal Add Widget catalog working. Build on FORK-1 GET-only Valedia proxy pattern.

## Fence
- NO POLYMARKET_US_LIVE flip
- NO VENUE_WRITER edit
- NO halt bypass / wallet / ACH / treasury auto / create_order from UI
- GET-only for Valedia status/proxy routes (POST → 405)
- Do not kill stock OpenTerminal `:3000`/`:4010`
- Do not kill screens `ot-valedia-desk` or `valedia-glance` (desk/glance already restored outside Build)
- No Valedia package version bump unless genuinely needed; prefer fork-only commits

## Proof
Ponytail audit + proxy/DOM match glance; document ports and fence in GROK-STATUS.
