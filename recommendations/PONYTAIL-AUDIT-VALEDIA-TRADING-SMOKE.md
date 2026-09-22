# Ponytail audit — VALEDIA-TRADING-SMOKE

**Mode:** `/ponytail` full on the smoke-harness diff only.
**Tree:** `~/.openclaw/workspace-v2`
**Scope:** `valedia_full_smoke.py`, `test_valedia_full_smoke.py`, `poly_us_smoke.py`, `mirofish_smoke.py`.
**Fence:** no mass create, no oil/weather unfence, no writer restore, `POLYMARKET_US_LIVE` env-only.

## Applied this session

`yagni:` a second Poly client. `poly_us_smoke.run_smoke(force_live_off=...)` — preview always; create only when LIVE off. **Shipped.**

`yagni:` failing the fence because Burton already set `POLYMARKET_US_LIVE=1`. Report it. **Shipped.**

`yagni:` live qty=1 create in smoke. Charter default skip. **Not shipped.**

`reuse:` `kalshi_fee_map.consult("KXBTCY")` + existing `get_balance`. No second fee formula.

`reuse:` Bionic live-lite first in `mirofish_smoke.run_live_lite`. Ollama leftover as SKIP.

## Findings

`yagni:` version bump. Harness-only; `VALEDIA_VERSION` stays 0.57.0. **Not shipped.**

`yagni:` flipping git `KALSHI_EV_PREFLIGHT_GATE` / `KALSHI_MAKER_FIRST`. Env already 1; smoke reports. **Not shipped.**

# ponytail: `valedia_full_smoke.py` is 495 lines — split health probe if it grows.

**net:** 4 scripts, 0 deps, 0 cron, 0 live creates. Lean enough. Ship.
