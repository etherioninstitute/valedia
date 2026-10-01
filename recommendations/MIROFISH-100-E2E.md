# MIROFISH-100-E2E — overnight real swarm must finish clean

## BURTON STEER (~16:11 PT)
Run full primary **now** (out of schedule) after the timeout fix — see `MIROFISH-STEER.md`. Do not wait for 03:00.


**Burton 2026-09-22 ~16:07 PT:** “We need to make sure MiroFish works 100%.”

Trading smoke / Bionic ontology E2E did **not** cover overnight primary. Cron still fails and falls back to lightweight `intel_refresh`. Close that gap.

## Goal
Scheduled `mirofish_primary` (and an equivalent manual full run in this slice) completes **ontology → graph → agents → simulation → report** with **exit 0**, and writes a real swarm signal that **overwrites** `intel_refresh` — not stuck on bias-50 lightweight.

## Current failure evidence (do not re-diagnose forever — fix)
- Cron: `0 3 * * * ~/.openclaw/workspace/scripts/mirofish_primary.sh` (03:00 PT).
- **2026-09-22 10:00 UTC / 03:00 PT:** seed OK (~5605 chars) → ontology **Request timed out** → swarm exit=1 → FALLBACK `intel_refresh`. `rc=1`. Log: `~/.openclaw/logs/mirofish_primary.log`.
- **2026-09-21 10:00 UTC:** ontology 400 `response_format.type` must be `json_schema` or `text` (pre-fix). That path was fixed for Bionic (`normalize_response_format`); overnight still times out on 27B sync.
- Manual E2E ~02:00 PT same night: ontology `proj_c29e60ca7611` PASS, then **0 NER** on tiny seed → prepare stalled; signal remained `intel_refresh`.
- Live signal now: `source=intel_refresh (lightweight)`, `overall_bias=50`, Fear/Greed 78.
- Inference: `MIROFISH_INFERENCE=bionic`, sync `qwen3.8-27b-obliterated-k`, report `ornith-1.5-k-m`. `.env` already notes: sync on 27B is interim; ontology timeouts → consider Ollama sync (`qwen3-8b-mf:latest` on `:11435`) until a snappy Bionic GGUF exists. Ollama LaunchAgent `com.burton.ollama-mirofish` must stay available.

## Scope (do)
1. **Root-cause + fix ontology wall for overnight-size seeds** (~5k+ chars, not 245-char smoke): timeouts, `max_tokens`, retries, GPU lease / LMS contention, and/or **split inference** (fast sync on Ollama 8B for ontology/graph stages; Bionic Ornith for report) — document chosen path. Prefer durable config over one-off manual luck.
2. **NER / graph density:** full live seed must produce usable entities (not 0 NER). Fix seed→graph path or seed size/structure if that is the stall.
3. **Complete swarm:** agents prepare → simulation → report without kill -9 / prepare stall. Record accuracy from **real** sim only (existing rule: do not record lightweight).
4. **Signal overwrite:** after PASS, `models/mirofish_signal.json` (live tree cron uses) has `source` containing real swarm (e.g. `mirofish_swarm`), not `intel_refresh (lightweight)`. Bias must not be the neutral-50 fallback unless the sim honestly says so.
5. **Prove in this slice:** run one full primary-equivalent (respect gpu-steward lease). Prefer quiet window if steward requires; do not strand a hung runner. Capture wall times per stage in Results.
6. **Deploy to the tree cron runs:** live `~/.openclaw/workspace` scripts + `~/mirofish-offline` env/backend as needed (same lesson as fee-map — v2-only fixes do not help 03:00 cron). Keep bak of `.env` before edits.
7. **Healthcheck:** MiroFish healthcheck ≥ prior bar (Bionic path green; Neo4j up if required for graph). Ollama fallback still one flip away unless Burton later says otherwise.
8. Ponytail audit + GROK-STATUS. Version bump Valedia only if workspace scripts change.

## Explicit non-goals
- Mass or any venue `create_order` / spend
- Flip `POLYMARKET_US_LIVE` / writers / wallets / HALT / `WEATHER_LIVE`
- Unfence oil/weather
- Delete Ollama models or stop `com.burton.ollama-mirofish` permanently
- Treat `intel_refresh` success as Done
- Kill screens `ot-valedia-desk` / `valedia-glance`

## Hard fence
Use screen `valedia-grok` only. No live trading lever flips. MiroFish stays seasoning for create gates (informed-before-create still refuses default-50) — this slice makes the **seasoning source real**, not a create unfence.

## Tools
RuFlo MCP `http://127.0.0.1:8810/mcp` filtered. CBM for navigation. Ponytail after code.

## Deliverables
1. Config/code fixes + live-tree deploy
2. One proven full run: exit 0 + real signal overwrite + stage timings
3. `PONYTAIL-AUDIT-MIROFISH-100-E2E.md`
4. Results appended here + `GROK-STATUS.md`; **Next=Burton** (stop chain)

## Done when
- Full primary-equivalent **exit 0**
- Signal `source` is real swarm (not intel_refresh lightweight)
- Ontology no longer failing overnight-size seeds for the documented reason
- Cron tree has the fix (not only workspace-v2)
- Audit written; `GROK_EXIT:0`

If a full sim cannot finish in one Build session for GPU/time, leave a **repro command + partial PASS checklist** and say BLOCKED with exact remaining stage — do not mark Done on ontology-only.

## Results (2026-09-22)

**Chosen path:** `MIROFISH_INFERENCE=split`. Sync ontology/NER/prepare/sim = Ollama `qwen3-8b-mf:latest` on `:11435`. Report = Bionic `ornith-1.5-k-m` on `:1234` (`LLM_REPORT_BASE_URL`). Embeddings = Ollama `nomic-embed-text` so an Ornith load cannot unload nomic. Bak: `~/mirofish-offline/.env.bak-mirofish-100-20260922-162249`. One flip back: `valedia_models.sh mirofish-inference ollama|bionic --write`, then restart `:5001`. `com.burton.ollama-mirofish` stayed up (pid 1893).

**Why not all-Bionic:** 2026-09-22 03:00 PT ontology on the 27B returned `Request timed out` for a 5605-char seed. 8B finished a 7141-char seed in 99s.

**Proof** (`sim_9b2e5c92e77d`, project `proj_b6a64f340f67`, screen `valedia-grok` only). Steward `batch_mirofish` is denied outside 01:00–07:00 PT and that acquire loads `qwen3:8b` on canonical `:11434`. This run did not take that lease and did not load `:11434`. Cron at 03:00 still acquires it. Inference stayed on the dedicated `:11435` / report `:1234`.

| Stage | Wall | Result |
|---|---|---|
| Seed | 5s | 7141 chars, crypto section present |
| Ontology | 99s | 10 entity types, 6 edges |
| Graph | 261s | 26 nodes, 16 edges |
| Prepare | 1159s | 19 agents. Persona/config LLM hit the old 45s cap and used the existing rule fallback |
| Sim | 2338s | 8 rounds, twitter 17 actions, reddit 15 |
| Report 1 | ~1960s | failed: Ornith call past 600s, plus Bionic embed `Model unloaded` |
| Report retry | 2639s | exit 0 after timeout 1800 and Ollama embeddings |

**Signal:** `~/.openclaw/workspace/models/mirofish_signal.json` `source=mirofish_swarm`, `overall_bias=51.5`, 6 assets (ETH SOL XRP ADA AVAX LINK), ts `2026-09-23T01:48:36Z`. Not `intel_refresh`. Ledger recorded 2 predictions from that real sim (`source=mirofish_swarm`). Lightweight was not recorded.

**Deploy:** live cron tree `~/.openclaw/workspace/scripts` (runner stage marks, seed guard, healthcheck, `valedia_models.sh`) and `~/mirofish-offline`. VALEDIA_VERSION **0.61.0** in workspace-v2 (scripts changed). Live `valedia_config.py` not wholesale-copied.

**Healthcheck:** before the run 21 PASS · 2 WARN · 0 FAIL (lightweight signal, 0 scored). After the overwrite: **22 PASS · 1 WARN · 0 FAIL**. The remaining WARN is 9 unscored predictions on the 5-day horizon. Neo4j bolt up. Bionic Ornith + Ollama 8B present.

**Prep knob for 03:00:** backend restarted onto `MIROFISH_PREP_LLM_TIMEOUT=120` after this prepare finished, so the next cron is not on the 45s cap. This proof's personas were the rule fallback; the sim actions themselves were 8B.

**Fence:** `POLYMARKET_US_LIVE` constant False. `WEATHER_LIVE` False. `MIROFISH_AUTO_RAISE` False. No writer/HALT/oil/weather edits. Screens 783 and 755 still detached. Shell env already had `POLYMARKET_US_LIVE=1`; it was not written or flipped.

**Ponytail:** `recommendations/PONYTAIL-AUDIT-MIROFISH-100-E2E.md`.

**Next:** Burton. Stop. Do not unfence oil/weather. Do not treat a future `intel_refresh` fallback as Done.

`GROK_EXIT:0`
