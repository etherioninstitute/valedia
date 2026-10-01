# Hermex remote access (Active)

**Dated:** 2026-09-25  
**Audience:** Valedia AI Assistant + Valedia Trader (shared-brain)

## Active endpoint

- **URL:** `http://100.125.9.30:8787`
- **Bind (FACT):** `HERMES_WEBUI_HOST=0.0.0.0` `HERMES_WEBUI_PORT=8787` in `~/hermes-webui/.env`
- **Tailscale IP:** `100.125.9.30` — **must stay static** for Valedia APIs. Do **not** change Tailscale.

## Access model

- Primary: **Tailscale mesh** to `100.125.9.30:8787`
- Backup: LAN (bind is `0.0.0.0`)
- **No cloudflared** on this path
- App “tunnel” wording = Tailscale (not Cloudflare)

## Bring-up

```bash
# ctl
~/hermes-webui/ctl.sh start|stop|restart|status|logs

# or LaunchAgent
# label: com.parantoux.hermes-webui
# runs: ~/hermes-webui/launchd-run.sh
```

## Logs

- `~/.hermes/webui.launchd.log`
- `~/.hermes/webui.log`

## Fences

- **NEVER** `hermes gateway --replace` for Hermex / webui fixes — that protects the Matrix/Signal life-channel gateway.
- Do not rotate Tailscale IP; Valedia APIs depend on `100.125.9.30`.

## Contabo failover (not Hermex)

**Hermex unrelated.** Phone → Mac `hermes-webui` only (`http://100.125.9.30:8787`). Contabo is Signal / SOCKS / hot-backup after missed Mac heartbeats.

**Owner:** Valedia Trader (do not edit these scripts from the Hermex lane).

**Full map:** [`VALEDIA-FULL-SETUP-MAP.md`](./VALEDIA-FULL-SETUP-MAP.md) (mirrored under `~/.openclaw/workspace{,-v2}/recommendations/`) · architecture skill `~/.openclaw/workspace{,-v2}/skills/valedia/architecture.md` (NIMROD primary; Contabo hot backup after ~3 missed heartbeats / ~15m).

### Mac (primary heartbeat)
- `~/.openclaw/workspace/scripts/heartbeat_send.sh` (crontab `*/5`; mirror `workspace-v2/scripts/`)
- `~/.openclaw/workspace/scripts/integrations/watchdog.py` (crontab `*/30` — cron health only)
- LaunchAgent `com.goatworthy.valedia.vpstunnel` → `ssh -D 1080 root@85.239.234.32` (key `~/.ssh/openclaw_vps`)

### Contabo `85.239.234.32` / Tailscale `100.99.195.47` (`vmi3134026`)
- `/root/.openclaw/workspace/scripts/heartbeat_watchdog.py` (crontab `*/5` — activate after 3 missed beats)
- `/root/.openclaw/workspace/scripts/failover_guard.sh` (wraps VPS jobs; standby while Mac primary)
