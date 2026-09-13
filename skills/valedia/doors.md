# Valedia — the doors (one persona, many entrances)

Burton talks to ONE Valedia. Same identity everywhere: `~/.hermes/SOUL.md` (assistant + trader, one risk brain). Last updated 2026-09-09.

## Door → brain map

| Door (app) | Path | Brain | Same Valedia? |
|---|---|---|---|
| **Element X** (Matrix) | Matrix home_channel `!L1H9…` @ `@obs10:openclaw-vps.tail4aeaf.ts.net` | **Hermes** (SOUL.md) | ✅ yes |
| **Hermex** (iPhone) | hermes-webui `http://100.125.9.30:8787` (Tailscale) | **Hermes** (SOUL.md) | ✅ yes |
| **Hermes Mac** (desktop/CLI) | Hermes directly | **Hermes** (SOUL.md) | ✅ yes |
| **Signal** (`+13234973198`, Burton UUID `47bd6dd5…`) | **currently OpenClaw** gateway (local `signal-cli` device "Valedia-MacStudio", model qwen3:32b, `~/.openclaw/workspace/SOUL.md`) | OpenClaw (separate brain today) | ⚠️ **PENDING** — see below |

Element X + Hermex + Hermes Mac already hit the **same** Hermes Valedia (shared SOUL + memory). Signal is the outlier.

## Signal unification (pending a manual step)
`signal-cli` is a **single-owner** resource per linked device. To make Hermes the Signal *brain* (same session family as Matrix), Hermes needs its **own linked device** on the account:
1. Burton links a Hermes signal-cli device via **QR scan** from the phone (only Burton can do this).
2. Enable a Hermes Signal chat door (allowlist `uuid:47bd6dd5…` only).
3. **Disable OpenClaw's inbound Signal chat** so there aren't two bots (OpenClaw keeps OUTBOUND money pings only).
Until then: Signal chat = OpenClaw's Valedia (same persona/rules via its SOUL.md), NOT the same Hermes session.

## Money pings (unchanged, always)
Outbound-only, exactly TWO formats from `signal-protocol.md`: `Invested: …` and `Profit: …`. Sent via `scripts/.../signal_send.py` (VPS relay: `ssh openclaw → openclaw message send`) and cron `openclaw message send`. Everything else → `~/.openclaw/logs/valedia-cron.log`. No status spam. These stay even after Hermes takes the chat door (outbound from any linked device is fine).

## Freeze / kill switches
- **Money:** freeze the Mercury agent card in the Mercury app (agent can't reverse). Transfers ≥$100/tx or ≥$200/day already require Burton's yes.
- **Chat (Hermes):** `hermes stop` (or stop the Hermes service) halts the assistant/trader chat brain.
- **Signal transport:** stop the OpenClaw gateway (`launchctl … ai.openclaw.gateway`) or the signal-cli device; unlink a device in Signal app.
- **Trading engines:** cron (`crontab -e`) + `strategy_blacklist.json` auto-kill (<25% WR).

## Non-negotiables
One risk brain. Approval gate on spend/send/transfer. Fee-gate. Crypto-first; $1000 undeployed until the forward-test ledger earns it. No WhatsApp / extra channels. MiroFish weight stays ≤10%.
