# OPENTERMINAL-VALEDIA — read-only Valedia glance (0.40.0)

**Updated:** 2026-09-19 PT
**Fence:** code/audit only. No `--live` orders, no ACH/wire/card, no wallet/venue transfer, no API-key edits, no writer / POLYMARKET_US_LIVE / SELF_ADJUST / MIROFISH_AUTO_RAISE / FORTRESS / REBALANCER_AUTOEXEC / TREASURY_AUTO / SPOT / NAUTILUS / LIVE_CLIENT flips. No staged ON.

HIG: macOS glance panel (system type, grouped cards, inline status). Not a Bloomberg grid. Not a fork of OpenTerminal Next.js.

## What it is

Same stdlib GET server as H4 (`valedia_cockpit.py`):

| Path | Body |
|------|------|
| `GET /` `GET /ui` | HIG HTML glance (polls `/status` every 5s) |
| `GET /status` `GET /api/valedia/status` | JSON panel |
| any non-GET | **405** |

No send / buy / create_order controls. Refresh is GET only.

## Start (opt-in — not in production cron)

```bash
cd ~/.openclaw/workspace-v2
python3 scripts/integrations/valedia_cockpit.py --listen
# UI:  http://127.0.0.1:8765/
# JSON: http://127.0.0.1:8765/status
```

Default bind **127.0.0.1:8765**. Do not add this to `install_cron.sh` unless Burton asks.

One-shot JSON (no server): `python3 scripts/integrations/valedia_cockpit.py`

## Ports (non-colliding)

| Surface | Port | Notes |
|---------|------|--------|
| **Valedia glance** | **8765** | this panel + `/status` |
| OpenTerminal web | 3000 | stock market UI — leave it |
| OpenTerminal API | 4010 | `API_PORT=4010` in their `.env` |
| LiteLLM | 4000 | do not steal |

Valedia does **not** bind 3000 / 4010 / 4000.

## Tailscale (100.125.9.30)

Loopback is not reachable over Tailscale. Bind the Tailscale IP (preferred) or all interfaces:

```bash
python3 scripts/integrations/valedia_cockpit.py --listen --host 100.125.9.30 --port 8765
# then: http://100.125.9.30:8765/
```

`--host 0.0.0.0` also works but exposes read-only P&L on every iface. JSON GET allows CORS `*` so a later OpenTerminal widget on :3000 can poll :8765; default bind is still loopback.

## Panel fields

- **mode** — exactly one of `LIVE` / `DRY` / `FENCED` (LIVE only if NAUTILUS_LIVE / SCALPER_LIVE / WEATHER_LIVE / REBALANCER_AUTOEXEC)
- **trading_halt** — `VALEDIA_TRADING_HALT`; banner says **new orders** halted
- **ears.gathering** — always `true`. Halt does not stop MiroFish / data streams (`KILL-SWITCH.md`)
- **broker** — `pnl_tracker.json` (label *broker ≠ FTL*); cash + deployed
- **ftl** — fee-true ledger; if `n=0` the UI says so plainly (do not treat broker net as FTL)
- **venues** — Kalshi / Poly US from **cached** `treasury_status.json` (no live GET from the cockpit). If that file is missing, Kalshi cash falls back to `pnl_tracker.json` (`status=BROKER`); Poly stays UNREAD until a treasury cache exists.
- **mirofish** — weight, `overall_bias`, signal `ts`
- **shadow** — intent counts; dispatchable stays 0 in this lane

Refresh treasury cache separately if you want new venue numbers: `python3 scripts/integrations/treasury_status.py` (GET balances, never moves money). Not wired into this UI poll.

## Tests / smoke

```bash
python3 scripts/integrations/test_openterminal_valedia.py
python3 scripts/integrations/test_h4.py
```

HTTP smoke is in the first file (ephemeral port, POST 405).

## Not shipped

- OpenTerminal Next.js vendor / `ValediaWidget.tsx` / AiWidget
- Production cron for `--listen`
- Order buttons, OMS, staged ON
