# VALEDIA-SELF-HEAL-1 — health probes → safe remediates → else Paperclip ticket

**Date:** 2026-09-26 ~14:32 PT  
**Status:** todo (filed)  
**Paperclip:** VAL-11 `a404190c-6435-4134-9c1f-130c56113c22`  
**Goal id:** `0e57493f-c5fb-4030-94c9-3936cd9e8ebb` — Valedia ops self-heal — detect breaks, remediate safe, else Paperclip ticket  
**Company:** `4fb233c2-27e9-4d0d-a217-2dc7205210a0` (Valedia)  
**Labels:** `LOCAL-CHARTER` (ops monitors + safe remediates) · company-level goal (**not** under INFORMED weather or ACCELERATE)  
**Live risk:** **Valedia runtime sole live risk** — never Paperclip / Hermes / Grok Build

---

## Context (ops proof)

Current break that motivated this ticket (~2026-09-26 PT):

- **Neo4j :7687** — connection refused; MiroFish `reportgeneratefailed` / wait-mode sims blocked.
- Safe start method (house): Docker Desktop up → `cd ~/mirofish-offline && docker compose up -d neo4j` (named volumes `mirofish-offline_neo4j_data` / `_neo4j_logs`; **never** `docker compose down -v` / volume wipe).
- Verify: `lsof -iTCP:7687 -sTCP:LISTEN` + `docker exec mirofish-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" 'RETURN 1 AS ok;'` (password from `~/mirofish-offline/.env` or `.neo4j_pass`).

Related charters: `ACCELERATE-1.md`, `WEATHER-TWO-SIDED-FEE-TRUE-1.md`, `KILL-SWITCH.md`, `PAPERCLIP-INFORMED-RISK-LOOP-1.md`.

---

## Mission

When any Valedia dependency breaks, **detect** via health probes, **auto-remediate when safe**, else **open a Paperclip issue with evidence**. Wire into the existing Valedia connection-health routine pattern.

### Probe / remediate surface

| Dependency | Probe | Safe remediate (examples) | Else |
|------------|-------|---------------------------|------|
| Neo4j / MiroFish | `:7687` LISTEN + bolt ping; MiroFish wait-mode | `docker compose -f ~/mirofish-offline/docker-compose.yml up -d neo4j` (no volume wipe) | Paperclip issue + evidence |
| Hermes gateways | `:8642`–`:8645` | Known restart recipe only | Ticket |
| Paperclip `recovery_needed` | recovery-actions API | Reconcile with `actionId` + `executionReconciliation` | Ticket |
| LMS model load | `:1234/v1` models | Propose-only; never unload/swap/kill without same-turn chat yes | Ticket |
| Tailscale Serve | serve status | Documented serve rebind only | Ticket |
| Desk screens | Hermes profile/screen health | Known screen recipe only | Ticket |

**Fence (hard):** no `--live`, no venue orders, no wallet/spend, oil fenced, eToro intel-only. Live trading risk stays Valedia runtime only.

## LMS / Bionic — no auto-unload

- LMS/Bionic models are Burton-owned.
- Automation (routines, self-heal, Grok Build, Hermes cron, Valedia Trader) may PROPOSE unloading/consolidating (e.g. Ornith-solo) but MUST NEVER unload, swap, or kill a loaded LMS model without explicit yes in chat that same turn.
- Interactive Hermes/Qwen sessions beat automation.
- Optional pin: only act on unload when Burton sets an explicit pin like "Ornith-only until I clear".

---

## Roles

### Scout (`LOCAL-CHARTER` research — optional)
Map existing Valedia connection-health routines and gaps vs the probe table above. Written inventory (no code) if Decider asks. **Do not wake Scout heartbeats** solely for Neo4j restarts.

### CTO — Ornith / local LMS only (`LOCAL-CHARTER` code)
Code monitors + safe remediates in:

| Role | Path | Branch |
|------|------|--------|
| **ACCEL Ornith** (preferred) | `~/.openclaw/workspace-accel-ornith` | `accel-ornith` |
| Ops worktree (if Decider prefers) | ops worktree as assigned | as assigned |
| Live runtime (do not code here) | `~/.openclaw/workspace` | `master` |

- Builder: Ornith continuous via RuFlo; LOCAL LMS only (`http://127.0.0.1:1234/v1`). No anthropic / openrouter / xAI.
- **No Grok Build unless Burton opts in** (`VALEDIA_CTO_BUILDER=grok-build`).
- Remediates must be **idempotent**, **non-destructive** (no DB wipe, no `--live`, no wallet).

Slice DONE (all required before next Ornith turn):
1. Code + tests + live-path proof + ponytail
2. Qwen3.8 check-in PASS/HOLD (LMS local)
3. RUFLO-STATUS one-liner (how this reduces dependency downtime without live risk)
4. CBM index/update for changed paths

### Auditor HOLD fence (`UPSTREAM` risk fence)
- No `--live`, no oil unfence, no eToro `start_copy`, no wallet/spend from Paperclip / Hermes / self-heal.
- **Live admit / orders = Valedia runtime only.**
- HOLD/FAIL any patch that remediates by placing orders, spending, or unfencing Oil.

---

## Acceptance criteria

1. Health probes cover Neo4j/MiroFish, Hermes `:8642`–`:8645`, Paperclip `recovery_needed`, LMS, Tailscale Serve, desk screens.
2. Safe remediates implemented for known recipes (Neo4j restart without volume wipe; clear ECONNREFUSED with documented restart; reconcile `recovery_needed` with `actionId` + `executionReconciliation`).
3. Unsafe / unknown failure → open Paperclip issue with evidence (no silent skip).
4. Fence never crossed from the self-heal path.
5. This charter exists at `~/.openclaw/workspace/recommendations/VALEDIA-SELF-HEAL-1.md`.
6. Paperclip issue stays under company-level self-heal goal `0e57493f-c5fb-4030-94c9-3936cd9e8ebb` (**not** INFORMED weather / ACCELERATE).

---

## Out of scope

`--live` / venue orders, oil unfence, eToro `start_copy`, wallet/spend, wiping Neo4j volumes, waking Scout heartbeats unsolicited for Neo4j-only, Grok Build without opt-in, mass Hermes/Paperclip restarts without a known recipe, unloading/swapping/killing a loaded LMS model without explicit yes in chat that same turn.

---

## Related Paperclip

- **Goal (new, company-level):** `0e57493f-c5fb-4030-94c9-3936cd9e8ebb` Valedia ops self-heal
- **This issue:** **VAL-11** VALEDIA-SELF-HEAL-1
- VAL-8 — Auditor HOLD fence (ACCELERATE; fence pattern reference only)
- VAL-10 — WEATHER-TWO-SIDED (charter style reference; different goal)

---

## Neo4j restart recipe (ops — used 2026-09-26 ~14:31 PT)

```bash
# Ensure Docker Desktop is running, then:
cd ~/mirofish-offline
docker compose up -d neo4j
# Verify (do NOT wipe volumes):
lsof -iTCP:7687 -sTCP:LISTEN
docker exec mirofish-neo4j cypher-shell -u neo4j -p "$(tr -d '\n' < .neo4j_pass)" 'RETURN 1 AS ok;'
```

---

## Done when

Probes + safe remediates land in ACCEL/ops worktree with tests + proof; unknown breaks open Paperclip tickets with evidence; Auditor HOLD not tripped; no live orders / wallet / oil unfence from self-heal; Neo4j recipe documented and verified without DB wipe.
