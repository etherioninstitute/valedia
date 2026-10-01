# Ponytail audit — VIBE-TRADING-SUBSUME-1

**Mode:** `/ponytail` on the absorb, not a vendored copy of HKUDS. Paper/shadow only.
**Fence:** `VIBE_SHADOW_LIVE` const false. `place_order` / `create_order` / `exec_generated` / `shell` raise before a socket. Oil/weather still fenced. Screens `ot-valedia-desk` and `valedia-glance` not killed.

## Applied

`reuse:` paper decision shape from `etoro_copy_paper` / `vaughan_arb_paper`. **Shipped** `data/vibe-shadow/decisions.jsonl`, source `vibe_shadow_paper`, `create_order` false, `broker_write` false. `forward_test_ledger._is_non_real_source` already ignores `paper`.

`reuse:` one price feature from Shadow Account (`prior_5d_return`) and a fee-true refuse. **Shipped** as arithmetic in `decide`. No Jinja, no generated `signal_engine.py`.

`reuse:` controls file rows. **Shipped** `VIBE_SHADOW_MODE`, `VIBE_SHADOW_LIVE`, `VIBE_BROKER_WRITE`, tick age, last decision.

## Skipped

`delete:` the HKUDS tree. React, FastAPI, Electron, 18 broker SDKs, MCP server, 462 alpha modules. **Not imported.** Replacement: `inventory()` paths and counts.

`yagni:` `pip install vibe-trading-ai` and an isolated research venv. The MCP surface includes an opt-in shell. **Not installed.**

`yagni:` a second live mandate gate. HKUDS `sdk_order_gate` already exists upstream and is the wrong process. Valedia stubs raise.

`yagni:` Ornith on this desk. The rule is one threshold. Add a model gate only if a shadow row is ever allowed to size a Kalshi or crypto-paper trade.

`yagni:` cron, OpenTerminal panel, copy buttons. **Not added.**

`yagni:` a partial git commit. **Not committed. Not tagged.**

## Findings

`shrink:` one JSONL plus one flock. Retention is a tail rewrite at 400 rows (`ponytail:` on `DECISION_KEEP`).

`shrink:` `live_enabled` returns False. No unlock env, no key loader.

`stdlib:` fee check is subtraction. No quantlib import.

**net:** -the HKUDS app (not vendored), -0 deps. 277 lines in `vibe_shadow_paper.py`. Version `0.71.0` working tree, not tagged. `VIBE_SHADOW_MODE=PAPER/SHADOW`.
