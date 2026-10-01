# PAPERCLIP × VALEDIA ORG — 2026-09-24

Control plane: Paperclip over **existing** Hermes Valedia trading profiles.  
**No duplicate trading agents. No --live / OMS / venue keys in Paperclip.**

## Status (PT)

- Paperclip: `http://127.0.0.1:3100` (`local_trusted`, isolated data-dir `~/.paperclip-valedia`)
- Health: `http://127.0.0.1:3100/api/health`
- Company **Valedia** created (issue prefix `VAL`)
- Pilot goal created (exact title below)
- Three trading `hermes_gateway` employees (Scout/Decider/Auditor) + **CTO** coding-only `valedia-cto` (:8645)
- Default Hermes messaging gateway (matrix+signal, PID was 37790) **left untouched** (never `gateway run --replace` on default)
- CoS / life profiles `scout|decider|auditor` **untouched** (distinct from `valedia-trading-*`)
- Screens left alone: `ot-valedia-desk`, `valedia-glance`, `valedia-grok`
- Ports left alone: glance `8765`, Laya `8766`, Bionic `1234`, LiteLLM `4000`, hermes-webui `8787`

## Org chart

```
Board = Burton (human)
├── CIO / Decider  → Paperclip employee "Decider"  → ~/.hermes/profiles/valedia-trading-decider  (:8643)
├── Scout          → Paperclip employee "Scout"    → ~/.hermes/profiles/valedia-trading-scout    (:8642)
├── Auditor        → Paperclip employee "Auditor"  → ~/.hermes/profiles/valedia-trading-auditor  (:8644)
└── CTO (hired)  → Paperclip employee "CTO" → ~/.hermes/profiles/valedia-cto (:8645)
    ├── Default builder: **Ornith** (Bionic :1234) + **RuFlo** orchestration
    ├── OPT-IN builder: **Grok Build** (`~/bin/grok-valedia`) via VALEDIA_CTO_BUILDER=grok-build
    └── Board approve required BEFORE any env flips / live risk / Oil / WEATHER runtime changes
```

Oil stays fenced. **WEATHER live risk stays Valedia runtime only** (not Paperclip adapters/env).

## Mapping table (Paperclip employee → existing Hermes profile)

| Paperclip employee | Role (Paperclip) | Hermes profile (EXISTING) | API port | Adapter |
|---|---|---|---|---|
| Decider | ceo (title: CIO / Valedia Trading Decider) | `valedia-trading-decider` | 8643 | `hermes_gateway` |
| Scout | researcher | `valedia-trading-scout` | 8642 | `hermes_gateway` |
| Auditor | security | `valedia-trading-auditor` | 8644 | `hermes_gateway` |
| CTO | cto (title: Valedia CTO coding-only) | `valedia-cto` (**NOT trading**) | 8645 | `hermes_gateway` |

**Do NOT map** to CoS/life short aliases `scout|decider|auditor` — those are distinct profiles and must not be hired into Valedia trading.

Short aliases `scout/decider/auditor` are **NOT** the same as `valedia-trading-*` (different inode + SOUL). Ignore them for this company.

## Pilot goal (exact)

> INFORMED weather fee-true only — improve NWS CLI + refuse lottery

## CTO hire (2026-09-24 PT) + builder switch

- **CTO hired** — Paperclip agent id `53d25446-ac6c-4bda-b0c6-5c067b6ddf29`, role `cto`, reportsTo Decider.
- Adapter: `hermes_gateway` → `http://127.0.0.1:8645` (Hermes profile `valedia-cto`, coding-only).
- Screen: `pc-valedia-cto` (`hermes -p valedia-cto gateway run --accept-hooks --external-supervisor`). **Never** `gateway run --replace` on default (PID ~37790).
- **Default builder = Ornith + RuFlo** (NOT Grok Build).
  - Model: `ornith-1.5-35b-a3b@q4_k_m` via Bionic `http://127.0.0.1:1234/v1` (`custom:lms-local`).
  - RuFlo orchestration: CLI `~/.npm-global/bin/ruflo`; MCP HTTP `:8810` (`~/bin/ruflo-mcp-http.sh`) when up.
- **Grok Build is OPT-IN only** (`~/bin/grok-valedia`).

### How to switch Grok Build vs Ornith (exact)

Paperclip-native field + matching env (keep both in sync):

1. **Default (Ornith)** — already set:
   - Profile `.env`: `VALEDIA_CTO_BUILDER=ornith`
   - Paperclip `adapterConfig.builder`: `"ornith"`
2. **Flip to Grok Build (OPT-IN)**:
   ```bash
   ~/bin/valedia-cto-builder grok-build
   # Then PATCH Paperclip agent adapterConfig.builder to "grok-build"
   curl -sS -X PATCH http://127.0.0.1:3100/api/agents/53d25446-ac6c-4bda-b0c6-5c067b6ddf29 \
     -H 'Content-Type: application/json' \
     -d '{"adapterConfig":{"builder":"grok-build"}}'
   # One-shot without persisting:
   VALEDIA_CTO_BUILDER=grok-build ~/bin/valedia-cto-builder run "TASK…"
   # or: ~/bin/valedia-cto-builder grok-build "TASK…"
   ```
3. **Flip back to Ornith**:
   ```bash
   ~/bin/valedia-cto-builder ornith
   curl -sS -X PATCH http://127.0.0.1:3100/api/agents/53d25446-ac6c-4bda-b0c6-5c067b6ddf29 \
     -H 'Content-Type: application/json' \
     -d '{"adapterConfig":{"builder":"ornith"}}'
   ```

Helper: `~/bin/valedia-cto-builder` (`status|ornith|grok-build|run`).

### Board gate

- **Board (Burton) must approve** before any env flips (API keys, `--live`, venue/wallet, Oil unfence, WEATHER live risk toggles).
- Paperclip adapters/env must **never** hold venue/wallet/API trading keys.
- Do **not** auto-chain Grok Build; it is never the silent default.

## Ports used

| Service | Port | Notes |
|---|---|---|
| Paperclip UI/API | **3100** | Preferred; was free. Health `/api/health` |
| Embedded Postgres (Paperclip) | 54329 | Inside `~/.paperclip-valedia` |
| Hermes API — Scout | **8642** | `API_SERVER_*` in trading profile `.env` only |
| Hermes API — Decider | **8643** | same |
| Hermes API — Auditor | **8644** | same |
| Hermes API — CTO (coding-only) | **8645** | `valedia-cto` profile; not trading |
| Reserved / do not bind | 8765 glance, 8766 Laya | |

Data dir isolation: `--data-dir /Users/burtonchatelain/.paperclip-valedia` (not default `~/.paperclip`).

## How to hire via `hermes_gateway` without creating new agents

Already done for the three trading profiles. Pattern for re-hire / recovery:

1. Ensure `API_SERVER_ENABLED=true`, unique `API_SERVER_PORT`, and `API_SERVER_KEY` exist **only** in:
   - `~/.hermes/profiles/valedia-trading-scout/.env` (port 8642)
   - `~/.hermes/profiles/valedia-trading-decider/.env` (port 8643)
   - `~/.hermes/profiles/valedia-trading-auditor/.env` (port 8644)
2. Start API servers on **those profiles only** (never default, never CoS):

```bash
# Dedicated screens (do not touch ot-valedia-desk / valedia-glance / valedia-grok)
screen -dmS pc-valedia-trading-scout   bash -lc 'hermes -p valedia-trading-scout   gateway run --accept-hooks --external-supervisor'
screen -dmS pc-valedia-trading-decider bash -lc 'hermes -p valedia-trading-decider gateway run --accept-hooks --external-supervisor'
screen -dmS pc-valedia-trading-auditor bash -lc 'hermes -p valedia-trading-auditor gateway run --accept-hooks --external-supervisor'
# NEVER: hermes gateway run --replace   # would hit default messaging gateway
```

3. Register Paperclip employees pointing at existing APIs (board/API), e.g.:

```bash
# Replace KEY with value from that profile's .env (API_SERVER_KEY) — do not commit keys
curl -sS -X POST "http://127.0.0.1:3100/api/companies/<COMPANY_ID>/agents" \
  -H 'Content-Type: application/json' \
  -d '{
    "name":"Scout","role":"researcher","title":"Valedia Trading Scout",
    "adapterType":"hermes_gateway",
    "adapterConfig":{
      "apiBaseUrl":"http://127.0.0.1:8642",
      "apiKey":"<API_SERVER_KEY from valedia-trading-scout .env>",
      "paperclipApiUrl":"http://127.0.0.1:3100",
      "sessionKeyStrategy":"issue"
    }
  }'
# Repeat for Decider :8643 and Auditor :8644
```

Alternate invite/join path (same adapter, still no new Hermes profiles):

```bash
# From paperclip clone with --data-dir ~/.paperclip-valedia
node cli/node_modules/tsx/dist/cli.mjs cli/src/index.ts invite create \
  --data-dir /Users/burtonchatelain/.paperclip-valedia \
  --company-id <COMPANY_ID> \
  --payload-json '{"requestType":"agent"}'
# Accept join with adapterType hermes_gateway + agentDefaultsPayload.apiBaseUrl/apiKey
# Approve + claim-key — still points at existing profile API servers
```

**Do not** use `hermes_local` for these three if that would spawn/duplicate child Hermes agents. Prefer `hermes_gateway` → already-running profile API servers.

## What was NOT started / pending Assistant ping

- No edits to root `~/.hermes/config.yaml`
- No `--live`, no venue/wallet/trading keys in Paperclip
- No new Hermes profiles/agents for Scout/Decider/Auditor
- CoS/life `scout|decider|auditor`, `profiles/valedia`, life SOUL.md untouched
- Bionic unload / openclaw-gateway / hermes-webui / signal-cli untouched
- CTO hired (coding-only `valedia-cto`); Grok Build **not** silent default / not auto-chained
- Oil remains fenced; WEATHER live risk remains Valedia runtime only
- Paperclip background **service** not installed (`onboard --no-install-service`)

## Instance IDs (this machine)

- Company id: `4fb233c2-27e9-4d0d-a217-2dc7205210a0`
- Goal id: `3b85af61-87e4-4e9a-825d-1994546cb6a3`
- Agents: Scout `277f3c57-9c78-401d-a7fe-0d7b8f5dce86`, Decider `16234474-029c-4852-abde-696dc25884e5`, Auditor `1f360d86-444b-4c24-8c7f-35bb9c9cce50`, CTO `53d25446-ac6c-4bda-b0c6-5c067b6ddf29`
- Pilot ticket: **VAL-1** id `f1b0a109-0fce-49ae-a8bb-2511538e536c` — assignee Decider (decompose & assign). UI: `http://127.0.0.1:3100/VAL/issues/VAL-1` (or `/issues/f1b0a109-0fce-49ae-a8bb-2511538e536c`)

## Pilot ticket + assignment policy

- Parent under INFORMED weather goal; fee-true NWS CLI + refuse-lottery only.
- **Assignee = Decider (CIO)** so Decider owns decompose & assign on heartbeats.
- Expected routing: Scout (research-first) → CTO (code if needed, Ornith+RuFlo default) → Auditor (HOLD/FAIL).
- Comment on ticket documents the same policy.



## CTO coding-only profile (do not confuse with trading)

```bash
# Already created. Recovery start only:
screen -dmS pc-valedia-cto bash -lc 'hermes -p valedia-cto gateway run --accept-hooks --external-supervisor'
# NEVER: hermes gateway run --replace
# Builder switch: ~/bin/valedia-cto-builder ornith|grok-build
```

## Start Paperclip again

```bash
export PATH="$HOME/.npm-global/bin:$PATH"
cd /Users/burtonchatelain/MemPalace/sources/repos/github/paperclip
node cli/node_modules/tsx/dist/cli.mjs cli/src/index.ts onboard --yes --no-install-service \
  --data-dir /Users/burtonchatelain/.paperclip-valedia --bind loopback
# (onboard --yes starts the server when config is ready; or:)
node cli/node_modules/tsx/dist/cli.mjs cli/src/index.ts run \
  --data-dir /Users/burtonchatelain/.paperclip-valedia --bind loopback
```

Prove health:

```bash
curl -sS http://127.0.0.1:3100/api/health
curl -sS http://127.0.0.1:8642/health
curl -sS http://127.0.0.1:8643/health
curl -sS http://127.0.0.1:8644/health
curl -sS http://127.0.0.1:8645/health
screen -ls
hermes gateway list
```
