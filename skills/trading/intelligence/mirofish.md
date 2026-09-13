---
description: >
  MiroFish — swarm intelligence simulation. 8 agent types simulate market
  participants. Provides 10% weight in signal fusion. Currently unproven.
---

# mirofish

MiroFish spawns AI agents representing different market participants and
simulates their emergent behavior to generate a collective market prediction.

## What it is

- Backend: Python 3.11, Flask, port 5001
- Frontend: Vite dev server, port 3000 (http://localhost:3000)
- Framework: CAMEL-AI's OASIS social simulation
- Backed by: Shanda Group (Chinese tech)
- License: AGPL-3.0

## Agent types (8)

Institutional traders, retail investors, quants, whale wallets,
market makers, short sellers, momentum traders, macro analysts.

## Current weight: 10% (unproven)

MiroFish earns weight through track record:
- Run daily for 2-4 weeks
- If directional accuracy > 55% → increase to 15-20%
- If consistently identifies contrarian opportunities → up to 25%
- Currently: first real simulations completed (bias=68, 7 assets)

## Signal format

```json
{
  "status": "active",
  "overall_bias": 68,
  "signals": {
    "BTC-USD": {"score": 65, "direction": "BUY"},
    "ETH-USD": {"score": 42, "direction": "SELL"}
  }
}
```
Stored at: `~/.openclaw/workspace/models/mirofish_signal.json`

## Speed optimizations

- Qwen3 8B for agents (fast), Qwen3 32B for final report (deep)
- `OLLAMA_NUM_PARALLEL=4` — 4 simultaneous agent calls
- 30 agents, 8 rounds (not 1000+) — ~8 min per simulation
- Zero data leakage: all inference local on NIMROD_SSD

## Related

- [[intelligence/signal-fusion]] — MiroFish feeds 10% weight
- [[valedia/architecture]] — MiroFish runs as background process
