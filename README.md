# Valedia Trader

Multi-venue quantitative research, risk and execution engine. Version 0.75.0.

## Quick start

```bash
uv sync
.venv/bin/python -m pytest tests                      # new package
tools/run_legacy_tests.sh                              # legacy flat layer, sandboxed
.venv/bin/python -m valedia_trader.backtest.walkforward
.venv/bin/python -m valedia_trader.agents.mirofish_models status
```

## Layout

| Path | Purpose |
|---|---|
| `valedia_trader/core` | event bus, clocks, legacy domain map |
| `valedia_trader/quant_engine` | ten levels, fifty formulas (`formula_registry()`) |
| `valedia_trader/agents` | sleeves, Darwinian weights, risk officer, autoresearch, MiroFish model switch |
| `valedia_trader/venues` | the-odds-api client and odds mathematics |
| `valedia_trader/data_and_fees` | point-in-time fee, funding and session registry |
| `valedia_trader/execution_and_risk` | sizing, kill switch, schedules, no-trade buffer |
| `valedia_trader/backtest` | zero-look-ahead engine and walk-forward runner |
| `src/` | legacy flat modules (kept stable; see AGENTS.md) |

Nothing here sends live orders on its own. Provenance of third-party ideas is in THIRD_PARTY.md.
