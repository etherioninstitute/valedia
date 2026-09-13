---
description: >
  Valedia's full system architecture. Mac Studio M1 Ultra primary,
  Contabo VPS backup + Signal relay + Command Center dashboard.
  Updated March 30, 2026.
---

# valedia architecture

## Primary node: Mac Studio M1 Ultra ("NIMROD")

| Component | Details |
|-----------|---------|
| Hostname | burtons-mac-studio-m1 |
| Tailscale IP | 100.125.9.30 |
| RAM | 128GB unified memory |
| Storage | Internal 587GB + NIMROD_SSD 3.6TB |
| OS | macOS (M1 Ultra) |

**Running processes:**
- Ollama serving qwen3:32b (20GB), deepseek-r1:70b (42GB), qwen3:8b, glm-4.7-flash
- MiroFish backend (Python 3.11, port 5001)
- MiroFish frontend (Vite, port 3000)
- SOCKS tunnel (SSH to VPS, port 1080, for Coinbase + Mercury proxy)
- Heartbeat (pings VPS every 5 min)
- 24 cron jobs (all trading engines run from NIMROD)

**Trading engines (all on NIMROD):**
- Sports Engine v2.0 (483 lines) — 87.5% WR, every 2h
- Latency Arb Engine (538 lines) — BTC/ETH spot vs Kalshi strikes, every 30min
- Quant Engine (394 lines) — LMSR + EV + Kelly + Bayesian, every 6h
- Market Scanner (167 lines) — 600+ Kalshi markets, every 6h
- Settlement Checker (120 lines) — hourly, Signal on profit only
- Master Allocator (540 lines) — cross-strategy capital allocation, every 4h
- P&L Tracker (291 lines) — real Kalshi settlement + Ollama AI audit, nightly

## Backup node: Contabo VPS (Seattle)

| Component | Details |
|-----------|---------|
| IP | 85.239.234.32 |
| Tailscale IP | 100.99.195.47 |
| RAM | 12GB, 6 AMD EPYC cores |
| Storage | 193GB SSD |
| OS | Ubuntu 24.04 |

**VPS roles:**
- Signal relay (all messages from NIMROD route through VPS openclaw CLI)
- OpenClaw Command Center dashboard (port 3333, Tailscale-accessible)
- Hot backup (activates after 3 consecutive missed heartbeats = 15 min)
- 39 OpenClaw skills installed (7 new: kalshi-command-center, twelve-data, playwright, self-improving, capability-evolver, minara, kalshi-trading)
- Cerebro topics (5 active) + memory files (3 files, 2KB)

## OpenClaw Command Center

URL: http://100.99.195.47:3333 (Tailscale only)
Auth: none (behind WireGuard)
Shows: sessions (3), vitals, memory (3 files), Cerebro (5 topics)

## Models on NIMROD_SSD

```
/Volumes/NIMROD_SSD/ollama_models/
+-- deepseek-r1:70b  (42GB, deep reasoning, 10 tok/s)
+-- qwen3:32b        (20GB, fast trading brain, 19 tok/s)
+-- qwen3:8b         (5.2GB, nightly AI audit, zero API cost)
+-- glm-4.7-flash    (19GB, fallback)
```

## Security stack

- Unbound DNS-over-TLS: all DNS encrypted via Cloudflare (1.1.1.1) + Quad9 (9.9.9.9)
- SOCKS5 tunnel: source IP masking for API calls
- Tailscale WireGuard: encrypted mesh between NIMROD + VPS
- fail2ban + SSH key-only auth on VPS
- All API keys in ~/.openclaw/.env (chmod 600)
- Kraken: IP-locked to VPS (85.239.234.32)
- Mercury: IP-locked to VPS
- Coinbase: routed via SOCKS proxy
- Kalshi: RSA-PSS auth (kalshi_auth.py, 50+ live trades proven)

## Signal protocol

Exactly two message types reach Burton:
1. "Invested: {amount} on {N} games" — when bets placed
2. "Profit: {+/-amount} ({W}W/{L}L settled) / Total: {net} ({record})" — when bets settle

ALL other output goes to local log: ~/.openclaw/logs/valedia-cron.log
Cron runner should_send() always returns 1 (never send from cron).

## Related

- [[valedia/cron-schedule]] — all 24 scheduled jobs
- [[valedia/trading-engines]] — engine details
- [[valedia/memory]] — memory and Cerebro
