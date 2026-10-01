# Ponytail audit — H4 OpenTerminal GET panel (0.29.0)

**Mode:** `/ponytail-audit` on the H4 diff only.
**Tree:** `~/.openclaw/workspace-v2`
**Scope:** `valedia_cockpit.py`, `test_h4.py`, version 0.29.0.
**Fence:** no `--live`, no OMS, no order send, no `AUTO_RAISE`, no crontab.

## Applied this session

`delete:` DRY-branch in `cockpit_mode` (EXEC_DRY_RUN=False + shadow flags made it dead). Mode is LIVE only on real live-escalator flags, else FENCED. [valedia_cockpit.py]

`delete:` PUT/PATCH/DELETE HTTP methods. `handle()` already 405s non-GET; POST kept so a proxy 405s instead of 501. [valedia_cockpit.py]

`delete:` `create_order` mention in the module docstring so hygiene stays a string ban. [valedia_cockpit.py]

## Findings (biggest cut first)

`yagni:` Vendoring OpenTerminal Next.js / `ValediaWidget.tsx` / react-grid-layout / AiWidget / portfolio buy. Replacement: one Python GET JSON panel. **Not shipped.**

`yagni:` Always-on HTTP server / new cron. Replacement: `python3 valedia_cockpit.py` prints JSON; `--listen` is opt-in loopback. **Not shipped as cron.**

`yagni:` Second ledger / write `models/valedia_cockpit.json`. Panel reads FTL + existing JSON. **Not shipped.**

`yagni:` Command palette, flash-on-change UI, charts, Yahoo quotes. Dossier steal for later UI. **Not this slice.**

## Not cut (spec)

- Shadow Intent counts (dispatchable must stay 0).
- Neural / persona file presence (H1 panel table).
- `GET /api/valedia/status` alias for an OpenTerminal proxy later.

**net:** ~170-line stdlib module, 0 deps, 0 new JSON writers. Lean enough. Ship.
