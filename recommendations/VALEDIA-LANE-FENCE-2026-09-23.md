# Valedia lane fence — Trader vs AI Assistant

**Date:** 2026-09-23  
**Owner:** Burton (via Valedia Trader + Valedia AI Assistant)  
**Status:** standing rule

## Bots
| Bot | Owns |
|---|---|
| **Valedia Trader** | RuFlo Expert; trading/finance desk; fee-true edges; POLY/Kalshi/crypto paper; venue env/gates/writers; trading crons; Grok Build charters that touch Valedia *trading* code; connection-health trading alerts |
| **Valedia AI Assistant** | Hermes-aligned proactive assistant (Grok twin of Hermes Valedia face); MemPalace / AGENT-INVENTORY; life/work/systems follow-through; non-trading architecture; model *requests* via Bionic/:1234 or LiteLLM/:4000; Grok Build relays that are *not* trading-desk slices |
| **Chief of Staff** | Personal CoS (Scout/Decider/Auditor, Sharp Attire, job search, morning brief) — not Valedia runtime |

## Hard fences (both)
- Live risk = **Valedia runtime only**. Neither Grok bot places/amends/cancels orders, touches wallets, or runs `--live`.
- Bionic = sole local model library. Neither stands up competing Ollama/llama-server stacks.
- Neither **owns** GPU load/unload; request slots; do not unload the other’s critical model without a ping.

## Collision surfaces — ping the other first
Before changing any of these, SendToAgent the other Valedia bot (priority true) or ask Burton:
1. `~/.hermes/config.yaml` or Hermes gateway / cron / delegation model routing
2. Bionic unload/reload / preferred LM Link device / LiteLLM model alias backends
3. Screen `valedia-grok` / `~/bin/grok-valedia` while the other may have a Build running
4. `recommendations/GROK-NEXT-QUEUE.md` or `GROK-STATUS.md` (append a dated note; don’t wipe Active without handoff)
5. `~/.openclaw/.env` trading flags (`POLYMARKET_*`, `ETORO_*`, writers, live unlocks)
6. Trading cron scripts / `cron_runner_mac.sh` / latency arb

## Default ownership
- Hermes assistant personality / Hermex / proactive life loops → **AI Assistant**
- Ornith/Qwen *for Valedia trading CE / MiroFish / paper safety* aliases → **Trader** coordinates; Assistant may request but not retarget trading aliases alone
- OpenTerminal-Valedia desk / glance trading controls → **Trader**
- AGENT-INVENTORY refresh → either may run; prefer one write; note date in file

## Handoff pattern
- Trading deep-dive or RuFlo → Assistant hands to Valedia Trader
- Life/Hermes/MemPalace/proactive ops → Trader hands to Valedia AI Assistant
- Ambiguous → ask Burton once; don’t both edit

## Do not
- Duplicate CoS morning-brief / personal email voice unless Burton routes it
- Second RuFlo Expert in Assistant; second life-CoS in Trader

Report back: path written + byte size or wc -l.
