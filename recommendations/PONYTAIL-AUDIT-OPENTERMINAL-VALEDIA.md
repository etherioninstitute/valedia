# Ponytail audit — OPENTERMINAL-VALEDIA (0.40.0)

**Mode:** `/ponytail` full on the OpenTerminal glance diff only.
**Tree:** `~/.openclaw/workspace-v2`
**Scope:** `valedia_cockpit.py` + `.html`, `test_openterminal_valedia.py`.
**Fence:** no live orders, no writer/AUTOEXEC/US_LIVE/SPOT/NT/SELF_ADJUST/AUTO_RAISE/treasury flips, no crontab.

## Applied this session

`reuse:` H4 GET server on :8765. HTML at `GET /`, JSON stays `/status`. Same handler.

`yagni:` vendoring OpenTerminal Next.js / `web/components/widgets/ValediaWidget.tsx` / react-grid-layout / AiWidget / portfolio buy. Replacement: one static HTML + stdlib `http.server`. **Not shipped.**

`yagni:` new port (3010/3001) or Next app under `vendors/OpenTerminal`. :8765 does not collide with OT :3000 / API :4010 / LiteLLM :4000.

`yagni:` live Kalshi/Poly GET on each UI poll. Cockpit reads `treasury_status.json` + `pnl_tracker.json`. **Not shipped.**

`yagni:` production cron for `--listen`. Opt-in CLI. **Not shipped.**

`yagni:` send/buy/create_order buttons. Refresh is GET.

## Findings

`shrink:` CORS `*` on JSON GET only (OT :3000 may poll later). Default bind remains 127.0.0.1.

**net:** 1 HTML file, panel fields on existing module, 1 test, 1 rec. 0 npm, 0 cron, 0 live flags. Lean enough. Ship.
