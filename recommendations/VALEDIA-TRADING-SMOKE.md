# VALEDIA-TRADING-SMOKE — 2026-09-22 PT

**Burton:** MiroFish must work 100% Bionic-only (no Ollama). Full Valedia trading smoke via Grok Build; debug failures.

**NORTH STAR:** most-informed → fee-true EV → size → trade → FTL self-correct. MiroFish seasons; never replace official FV. Never fire on neutral-50.

## Already done by parent (do not undo)

- `~/mirofish-offline/backend/app/utils/llm_client.py` patched: Bionic drops `response_format.json_object` (Bionic only allows `json_schema`|`text`); `_is_ollama` only `:11434`/`:11435` (not all localhost); Bionic gets `reasoning_effort=none`.
- `oasis_profile_generator.py` + `simulation_config_generator.py` use `normalize_response_format`.
- `~/mirofish-offline/.env` already `MIROFISH_INFERENCE=bionic` → `:1234`.
- Backend restarted on patched code (`:5001/health` ok).
- Runtime env (Burton best-for-profit): `POLYMARKET_US_HTTP=1`, `POLYMARKET_US_LIVE=1`, `KALSHI_EV_PREFLIGHT_GATE=1`, `KALSHI_MAKER_FIRST=1`. Writer `polymarket=none`. Informed-before-create const True.

## Part A — MiroFish end-to-end (Bionic only)

1. Confirm no MiroFish path points at `:11435` / Ollama. Healthcheck: Bionic required; Ollama WARN/skip only (do not fail smoke if Ollama down). Do **not** set `MIROFISH_INFERENCE=ollama`.
2. Neo4j `bolt://localhost:7687` was **DOWN**. Docker CLI was unavailable until Colima. Start Colima if needed, then Neo4j 5.15 with auth from `~/mirofish-offline/.env` (`NEO4J_USER`/`NEO4J_PASSWORD`). Wait until bolt accepts.
3. Smoke `LLMClient.chat_json` tiny ontology JSON against Bionic (must not 400 on json_object).
4. Run MiroFish Step 1 ontology generate (seed upload + `/graph/ontology/generate`) to completion — this is the failure from Sep 20–21.
5. If ontology PASS, continue runner through graph build → prepare → sim as far as wall allows; record where it stops. Prefer real swarm success over intel_refresh fallback.
6. On success: write `models/mirofish_signal.json` from real swarm (not `intel_refresh (lightweight)`); update accuracy ledger record if applicable.
7. Docs: append Results to `recommendations/MIROFISH-BIONIC.md` + new `PONYTAIL-AUDIT-MIROFISH-E2E.md`.

## Part B — Full Valedia trading smoke

Harness: `python3 scripts/integrations/valedia_full_smoke.py` (+ `test_valedia_full_smoke.py`).

**Update harness if needed** so current truth does not false-fail:
- `POLYMARKET_US_LIVE` may be **True** (env). Fence section must report LIVE on honestly; do **not** force LIVE off.
- Expect `KALSHI_EV_PREFLIGHT_GATE` / `KALSHI_MAKER_FIRST` True.
- Poly: balances + preview required. **Do not** mass `create_order`. Optional single qty=1 create only if informed gate would pass AND charter comment says so — default **no live create** in smoke (preview + LiveWriteBlocked inverted check: when LIVE on, create may succeed — prefer mock/unit for create path; live create = Burton-only unless you document one tiny informed fill).
- Kalshi: GET balance + fee-map gate consult path; no oil/weather unfence.
- MiroFish section: prefer live Bionic ontology/signal health over fixture-only.
- Cockpit `:8765` should PASS if glance screen up.
- Shadow: intents dispatchable=0 still.
- P&L: broker ≠ FTL honesty.

Run smoke; on FAIL debug and re-run until PASS or document irreducible blocker with fix PR.

## Tools

RuFlo MCP `http://127.0.0.1:8810/mcp` filtered ADR-035. CBM for navigation. Ponytail after code. codebase-memory-mcp when useful.

## Hard fence

- Do **not** unfence oil/weather.
- Do **not** restore CB/Alpaca writers for live.
- Do **not** flip `VALEDIA_TREASURY_AUTO` / wallets / ACH.
- Do **not** commit `POLYMARKET_US_LIVE=True` into git (env only).
- Do **not** point MiroFish back at Ollama.
- Do **not** kill screens `ot-valedia-desk` / `valedia-glance` (only use `valedia-grok` for this Build).

## Deliverables

1. MiroFish ontology PASS evidence + Neo4j up (or blocked with exact reason).
2. `valedia_full_smoke` report JSON + `recommendations/VALEDIA-TRADING-SMOKE.md` Results.
3. Harness/code fixes committed on `valedia-v2-grok-build` if needed; version bump only if Valedia package changes.
4. `GROK-STATUS.md` updated; Next=Burton.
5. `PONYTAIL-AUDIT-VALEDIA-TRADING-SMOKE.md` (+ MiroFish E2E audit).

## Done when

- Ontology no longer dies on `json_object` 400.
- MiroFish primary path is Bionic-only.
- Full smoke PASS (or FAIL with fixed root cause + re-PASS).
- STATUS written; no auto next slice.

## Results (2026-09-22 PT)

**Verdict:** VALEDIA-FULL-SMOKE **PASS** wall 41.004s / 600s. Version stays `0.57.0` (harness-only; no `valedia_config` bump).

**MiroFish Bionic-only**
- Active `.env`: `MIROFISH_INFERENCE=bionic`, `LLM_BASE_URL=http://127.0.0.1:1234/v1`. Never flipped to ollama.
- Colima was stopped with a stale lima `in_use_by` lock. Cleared lock; Colima started. Neo4j container `mirofish-neo4j` **5.18.1** (compose image; rec asked 5.15 — existing volume/auth already matched `~/mirofish-offline/.env`; bolt `:7687` open). Did not start the compose Ollama service.
- `normalize_response_format({"type":"json_object"})` → `None` on Bionic. `chat_json` tiny ontology: **PASS** 129.2s, keys `entity_types`/`edge_types`/`analysis_summary`. No 400.
- Step 1 `/api/graph/ontology/generate`: first attempt 20min HTTP timeout at `max_tokens=4096`. Patched Bionic ontology `max_tokens=1500`, LLM timeout 900s, `max_retries=0`. Retry **ONTOLOGY_PASS** `proj_c29e60ca7611` 10 entity types / 6 edges / 655.8s.
- Graph build **PASS** 141s `graph_id=866df66c-8bc4-47e6-b5dc-d9abe0655ff1`. NER on the 245-char smoke seed extracted **0 nodes**. Prepare `sim_665917080fca` stalled (`expected_entities_count=0`). Stopped there. Did not write `mirofish_signal.json` from intel_refresh. Healthcheck: **20 PASS · 2 WARN · 0 FAIL**. Signal still `intel_refresh (lightweight)` age 2h.

**Full smoke (runtime env from `~/.openclaw/.env`, not git)**
- Harness no longer false-fails `POLYMARKET_US_LIVE=1`. Fence reports LIVE on. Poly: balances+preview; create skipped. Kalshi: GET balance $66.76 + fee-map consult `KXBTCY` status ok. EV preflight True, maker-first True.
- 8/8 PASS. Cockpit GET `:8765` PASS. Shadow dispatchable=0. Broker net -12.62 ≠ FTL n=1. Report: `models/valedia_full_smoke_report.json`.

**Fence held:** no oil/weather unfence, no mass `create_order`, no treasury auto, screens `ot-valedia-desk` / `valedia-glance` left running.

**Next = Burton.** Do not auto-start another slice.
