# VIBE-TRADING-SUBSUME-1 — HKUDS Vibe-Trading → Valedia research / paper-shadow / MCP surface

**Burton:** 2026-09-23 ~17:51 PT — subsume official HKUDS/Vibe-Trading into Valedia (Build executes end-to-end; Valedia Trader launched).  
**Depends on:** queue was idle after KALSHI-INFORMED-LIVE; Active may be this slice.  
**Reference clones:**
- Official: `~/MemPalace/sources/repos/github/HKUDS-Vibe-Trading` → `https://github.com/HKUDS/Vibe-Trading.git` (main) HEAD `a65427a3d587e43cd5c7541948b36524e8fd2f12` (shallow depth 1)
- Related but **do not overwrite**: `~/MemPalace/sources/repos/github/vaughanf1-vibe-trader` → `https://github.com/vaughanf1/vibe-trader.git` HEAD `f9dbdae8766d48589c46f12730f0d3916eb61b16` — content-similar / fork lineage; treat as separate inventory, not a merge target
**Prior style:** `ETORO-SUBSUME-1.md`, `VAUGHAN-SUBSUME-1.md`, `SUBSUME-STACK-MASTER.md`. Ponytail: `PONYTAIL-AUDIT-*.md`.

## What it is (accurate skim)
HKUDS **Vibe-Trading** (`vibe-trading-ai` on PyPI) is an **AI trading / quant research agent**, not a Valedia live OMS:
- Backtest engines + loaders; **Alpha Zoo** factor inventory (~460+ alphas) under `agent/src/factors/`
- **Shadow Account** loop (`agent/src/shadow_account/`) — paper replay / rule extraction / codegen
- **MCP server** `vibe-trading-mcp` (`agent/mcp_server.py`) + FastAPI serve (`agent/api_server.py`)
- Live/broker layer exists (`agent/src/live/`, connectors: IBKR/MT5/futu/Alpaca/eToro/etc.) — **Valedia must not wire those write paths**
- SECURITY.md: generated backtest code runs locally in a narrowed subprocess; still network-capable; treat untrusted strategies carefully; wallet/airdrop impersonation scams called out
- Package scripts: `vibe-trading`, `vibe-trading-mcp`

## Goal
Analyze HKUDS-Vibe-Trading, **scrub security concerns**, and absorb **patterns/modules Valedia can use** into the **right place**:
1. Research / factor inventory (Alpha-Zoo-style catalog of ideas → Valedia docs or inventory-only module)
2. Paper/shadow decision loops (Shadow Account spirit beside crypto-paper / vaughan_arb / etoro_copy_paper)
3. Optional isolated research MCP surface (read-only tools) — **never** on Kalshi/Poly live create path
4. Quantlib / fee-true / fail-closed grounding patterns where they strengthen Valedia gates

**Do NOT** replace Kalshi/Poly live path or become a second live risk brain. Valedia runtime remains sole live risk.

## Scope (do)
1. **Security scrub REQUIRED** before any wiring. Document findings in `recommendations/VIBE-TRADING-SECURITY-2026-09-23.md`:
   - Secrets in repo / `.env.example` default-key smells / broker json configs
   - Unsafe `eval`/`exec` of LLM-generated code; sandbox gaps; `subprocess`/`shell=True`
   - Broker write paths (IBKR/MT5/futu/Alpaca/eToro POSTs, `sdk_order_gate`, live runner)
   - Shell injection; unprotected MCP/FastAPI surfaces; telemetry/exfil
   - Dependency supply-chain red flags (lockfiles huge; prefer inventory over wholesale install)
   - **Remove or fence** anything dangerous before Valedia import; prefer pattern reimplementation over vendoring
2. Map symbols → Valedia modules via **CBM + RuFlo** (`http://127.0.0.1:8810/mcp` filtered ADR-035). Prefer absorb into `scripts/integrations/vibe_*` (mirror `etoro_*` / `vaughan_*`) over vendoring the whole app.
3. Implement **only** fenced research/paper pieces that earn their keep this slice, e.g.:
   - Inventory note of Alpha Zoo / Shadow Account / MCP tool surface Valedia might reuse later
   - Optional thin Valedia paper/shadow helper inspired by Shadow Account decision logging (jsonl, `source` contains `paper`, `create_order` false)
   - Optional: install `vibe-trading-ai` in an **isolated venv** for research MCP only — never on live create path, never with broker write env
4. Tests for any new Valedia code: offline fixtures; prove no venue create; live broker stubs refuse.
5. Docs: short desk note if code lands; **Ponytail** `PONYTAIL-AUDIT-VIBE-TRADING-SUBSUME-1.md`; bump working-tree `VALEDIA_VERSION`.
6. Fold via RuFlo hooks where natural; prove tool use in `GROK-STATUS.md`.

## Autonomy
If a design fork would normally ask Burton, **DECIDE yourself** with a short rationale in STATUS / Results.
Prefer **safer, narrower, research/paper first**. Default decisions:
- Do **not** vendor the full React/FastAPI app into Valedia
- Do **not** enable any HKUDS live/broker connector
- Prefer inventory-only + one small Valedia integration module over installing the full PyPI package unless MCP research clearly needs it and stays isolated
- If Alpha Zoo code copy is license-unclear for wholesale import, inventory + reimplement thin patterns only (repo is MIT per LICENSE — still prefer thin absorb)

## Explicit non-goals
- Live broker orders (IBKR/MT5/futu/Alpaca/eToro/Binance write)
- Wallet / treasury ACH / oil-weather unfence / mass Poly create
- Committing secrets; echoing keys in STATUS/log
- Replacing Kalshi INFORMED create gate or using Jev as OMS
- Auto-chaining Slice 2 / GoldQuant / HedgeFund / eToro live unlock
- Overwriting `vaughanf1-vibe-trader`

## Hard fence
Valedia runtime = sole live risk. No `--live` venue orders, no wallet/spend, no treasury ACH, no oil/weather unfence, no mass Poly create, no eToro write/copy. INFORMED stays hard create gate; Jev fail-open seasoning only. Crypto remains paper/shadow. Screen `valedia-grok` only; preserve `ot-valedia-desk` / `valedia-glance`. No HALT bypass.

## Tools
RuFlo `:8810` filtered (ADR-035). CBM for Valedia navigation. Prefer **Grok 4.7**. Prove tool use in STATUS.

## Done when
- `VIBE-TRADING-SECURITY-2026-09-23.md` written with concrete findings + fences
- Useful modules/patterns folded into Valedia **or** explicitly inventoried-only with paths
- Tests for any new Valedia code green offline
- `PONYTAIL-AUDIT-VIBE-TRADING-SUBSUME-1.md` written
- Working-tree `VALEDIA_VERSION` bumped (uncommitted OK)
- `GROK_EXIT:0`
- **Next = Burton stop** (do not auto-chain Slice 2)

## Results

**2026-09-23 ~18:01 PT. VALEDIA_VERSION 0.71.0** (working tree, not committed, not tagged). `GROK_EXIT:0`.

**Decision (no Burton ping):** inventory plus one paper module. Safer and narrower than a PyPI install or a vendored app. The MCP server can opt into `shell=True` (`bash_tool.py:131`). Generated backtests still have a network-capable subprocess. Eighteen connectors implement `place_order`. Those stay in the clone.

`scripts/integrations/vibe_shadow_paper.py` logs a fixture journal to `data/vibe-shadow/decisions.jsonl`. Source `vibe_shadow_paper`. `create_order` false. `broker_write` false. `live` false. Kelly ignores the source (`forward_test_ledger._is_non_real_source`).

Fixture: BTC-USD `shadow_log` $25 (prior 5d −0.10, net +38 after fee 2). ETH-USD `fee_true` (net −1). SOL-USD `review_only` (prior +0.04). DOGE-USD `missing_feature`. Daily cap test: four $25 logs then `daily_notional`.

`place_order`, `create_order`, `exec_generated`, and `shell` raise `vibe_broker_write` / `vibe_codegen` / `vibe_shell`. `live_enabled()` stays false when `VIBE_TRADING_LIVE=1`. No socket.

Alpha Zoo inventory: academic 12, alpha101 101, fundamental 4, gtja191 191, qlib158 154 (462). Paths only. qlib158 NOTICE is Apache-2.0. Repo LICENSE is MIT.

Clone untouched at `a65427a3d587e43cd5c7541948b36524e8fd2f12`. `vaughanf1-vibe-trader` still `f9dbdae8766d48589c46f12730f0d3916eb61b16`.

Tests: `test_vibe_shadow_paper.py` PASS. `test_slice23.py` PASS. Controls snapshot in a child that hid inherited flags: `VIBE_SHADOW_MODE=PAPER/SHADOW`, `VIBE_SHADOW_LIVE` false, `VIBE_BROKER_WRITE` false, oil `OIL_BRACKET` / `KXWTI` still fenced, `INFORMED_BEFORE_CREATE` true, `VALEDIA_JEV` const false. `test_controls_readout.py` still expects `VENUE_WRITER.polymarket == none`; the working tree already has `valedia` (comment: Burton 2026-09-23 max-env). This slice did not change that writer.

Parent shell still has `POLYMARKET_US_LIVE`, `ETORO_LIVE`, `SPOT_RECONCILE_HTTP`, and `VALEDIA_JEV` set. Screens `ot-valedia-desk` (783), `valedia-glance` (17171), `valedia-grok` (15032) left running.

Docs: `VIBE-TRADING-SECURITY-2026-09-23.md`, `VIBE-SHADOW-PAPER.md`, `PONYTAIL-AUDIT-VIBE-TRADING-SUBSUME-1.md`.

RuFlo `hooks_route` (security-architect) + `memory_search` (vaughan-subsume-1 0.67, etoro routing 0.62) + `memory_store` + `hooks_post-task`. CBM `list_projects` (13; HKUDS clone not indexed) + `search_graph` on paper decision logs + `check_index_coverage` (etoro file not tracked, `valedia_config.py` metadata changed — source was read).

`GROK_EXIT:0`

**Next = Burton stop.** Do not auto-chain Slice 2, GoldQuant, HedgeFund, or an eToro live unlock. Do not unfence oil/weather. Do not mass create_order. Do not install `vibe-trading-ai`.
