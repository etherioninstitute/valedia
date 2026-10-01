# Valedia full setup map (shared — Trader + AI Assistant)

**Updated:** 2026-09-25 04:09 PDT  
**Owners:** Valedia Trader = trading / Contabo failover / trading-repo CBM. Valedia AI Assistant = life Hermes / Hermex / agent-inventory / life-repo CBM.  
**Lane fence:** `recommendations/VALEDIA-LANE-FENCE-2026-09-23.md`  
**Architecture skill (partly stale models):** `~/.openclaw/workspace{,-v2}/skills/valedia/architecture.md`

Hard fence: live venue risk = Valedia runtime only. No `--live` / wallet / spend from Grok Bot agents.

---

## Two machines (do not conflate)

| Role | Host | Addresses | Job |
|------|------|-----------|-----|
| **Primary (NIMROD)** | Mac Studio M1 Ultra | LAN `192.168.1.51` · Tailscale **`100.125.9.30`** | Trading crons, Hermes, hermes-webui/Hermex, Bionic/LMS :1234, LiteLLM :4000, MiroFish, RuFlo/Ornith |
| **Hot backup / relay (Contabo)** | Contabo VPS (`vmi3134026`) | Public **`85.239.234.32`** · Tailscale **`100.99.195.47`** (`openclaw-vps`) | Signal relay when activated, OpenClaw gateway, SOCKS egress target, cron jobs wrapped by failover guard |

**Leave Mac Tailscale `100.125.9.30` unchanged** — Hermex + many Valedia APIs use it.

SSH Mac→VPS: `ssh -i ~/.ssh/openclaw_vps root@85.239.234.32` (or `root@100.99.195.47` on tailnet).

---

## Contabo failover (Trader-owned) — not Hermex

Hermex talks only to Mac hermes-webui. Contabo is unrelated to the phone UI path.

| Piece | Path | Behavior |
|-------|------|----------|
| Mac heartbeat | `~/.openclaw/workspace/scripts/heartbeat_send.sh` (mirror in workspace-v2) | Cron `*/5`. SSH to `85.239.234.32`, writes `/tmp/valedia_mac_heartbeat`, resets `/tmp/valedia_fail_count`. Mac logs `/tmp/valedia_heartbeat`, local fails `/tmp/valedia_heartbeat_fails`. |
| VPS watchdog | `/root/.openclaw/workspace/scripts/heartbeat_watchdog.py` | Cron `*/5` on VPS. Activates only after **3 consecutive missed** Mac beats (~15 min) **or** Mac-reported 3+ fails. Sets `/tmp/valedia_vps_active`, can enable Signal DMs + restart `openclaw-gateway`. |
| VPS job guard | `/root/.openclaw/workspace/scripts/failover_guard.sh` | Wraps VPS cron scripts; **no-op while Mac primary** (no `VPS_ACTIVE` flag). |
| Mac cron watchdog | `~/.openclaw/workspace/scripts/integrations/watchdog.py` | Cron `*/30`. Fixes `+x` / stale cron log; Signal via `ssh openclaw`. **Not** Contabo failover. |
| SOCKS tunnel | LaunchAgent `com.goatworthy.valedia.vpstunnel` | `ssh -D 1080` → `root@85.239.234.32` key `~/.ssh/openclaw_vps`. IP-locked venue egress (e.g. Kraken allowlist on Contabo public IP). Logs `~/.openclaw/logs/vpstunnel.*`. |

As of 2026-09-25 ~04:05 PT: Mac heartbeats OK (`fails=0`); VPS standby.

---

## Hermex / Hermes UI (Assistant-owned)

| Item | Value |
|------|--------|
| Phone URL | `http://100.125.9.30:8787` |
| Bind | `HERMES_WEBUI_HOST=0.0.0.0` `HERMES_WEBUI_PORT=8787` in `~/hermes-webui/.env` |
| LaunchAgent | `com.parantoux.hermes-webui` → `~/hermes-webui/launchd-run.sh` |
| Doc | `~/MemPalace/sources/shared-brain/HERMEX-REMOTE-ACCESS.md` |

**Model picker madness (2026-09-25):** Hermex discovers **all** wired backends — LMS Bionic (:1234, 11 ids incl. junk names), LiteLLM (:4000), Ollama :11434, plus Hermex-native Copilot / Ollama Cloud / xAI / Bedrock / Flash-Next. Valedia day lane is only Obliterated Qwen + Ornith. Trim pending Burton’s choice; provider hide = Assistant lane.

---

## Current local model endpoints

| Layer | URL | Role |
|-------|-----|------|
| Bionic / LMS | `http://127.0.0.1:1234/v1` | Model server |
| LiteLLM | `http://127.0.0.1:4000/v1` | Aliases into LMS |
| Ollama (legacy tags) | `http://127.0.0.1:11434` | Old local tags; not Valedia phone default |
| Hermes default | `custom:lms-local` → obliterated Qwen (see `~/.hermes/config.yaml`) | Life/coding client |

Builder default: Ornith/RuFlo. Grok Build opt-in only.

---

## Knowledge refresh

| Surface | Command / path | Owner |
|---------|----------------|-------|
| Agent inventory | `~/bin/refresh-agent-inventory.sh` → mirror `AGENT-INVENTORY.md` | Assistant |
| CBM CLI | `~/.openclaw/tools/cbm/codebase-memory-mcp cli --json …` | Split by repo |
| Trading CBM | index `workspace-v2` / trading trees after material changes | Trader |
| Life CBM | e.g. `~/hermes-webui` when CBM unlocked | Assistant |

CBM: one indexer at a time. If CLI says pre-coordination / unverified generation active, close other CBM MCP sessions first.

---

## Quick IP cheat sheet

- Hermex / Mac Tailscale APIs → **`100.125.9.30`**
- Contabo Tailscale → **`100.99.195.47`**
- Contabo public (SSH, IP-locked venues) → **`85.239.234.32`**
