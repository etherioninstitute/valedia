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
_(Build fills)_
