# ACCELERATE-1 — Ornith continuous + Qwen3.8 check-in (LOCAL ONLY)

**Date:** 2026-09-26 ~00:25 PT  
**Status:** active pilot setup  
**Goal id:** `8757cfb0-fd8c-4e25-88a4-773cabbce7ed` (ACCELERATE under INFORMED)  
**Parent issue:** VAL-5 `58b1a2ee-2a18-4d98-afb3-451ce0270de8`  
**Company:** `4fb233c2-27e9-4d0d-a217-2dc7205210a0`

---

## A) Paperclip → RuFlo link (verified)

There is **no** native Paperclip adapter that speaks RuFlo protocol. The working link is:

**Paperclip issue assigned to CTO** → Paperclip `hermes_gateway` adapter (`adapterType=hermes_gateway`, `apiBaseUrl=http://127.0.0.1:8645`, profile `valedia-cto`) → Hermes CTO runs with **local Ornith** (`custom:lms-local` → Bionic LMS `http://127.0.0.1:1234/v1`, model `ornith-1.5-35b-a3b@q4_k_m`) and is instructed to orchestrate coding via **RuFlo CLI / MCP `:8810`** (`ruflo` / `http://127.0.0.1:8810/mcp`).

So: **Paperclip → Hermes CTO (:8645) → Ornith local + RuFlo orchestration (:8810)** — not Paperclip↔RuFlo binary protocol.

CTO `adapterConfig.builder` = `ornith` (default). Grok Build is OPT-IN only (`VALEDIA_CTO_BUILDER=grok-build` / `~/bin/grok-valedia`). Do **not** launch Grok Build for ACCELERATE continuous.

---

## B) RuFlo providers — force LOCAL (save paid tokens)

### Flags discovered (`ruflo config providers --help`)

```
-a, --add <name>       Add provider
-r, --remove <name>    Remove provider
    --enable <name>    Enable provider
    --disable <name>   Disable provider
```

Plus: `ruflo providers configure -p <name> [-m model] [-e endpoint] [-k key]`  
(stores under `agents.providers[]` with `baseUrl`).

Config files (workspace cwd):
- `~/.openclaw/workspace/claude-flow.config.json` (authoritative for `ruflo config providers`)
- `~/.openclaw/workspace/.claude-flow/config.yaml` (runtime providers block; updated to LMS)

### BEFORE (paid anthropic Active)

```
AI Providers
+------------+---------------------------+----------+----------+
| Provider   | Model                     | Priority | Status   |
+------------+---------------------------+----------+----------+
| anthropic  | claude-3-5-sonnet-2024... |        1 | Active   |
| openrouter | claude-3.5-sonnet         |        2 | Disabled |
| ollama     | llama3.2                  |        3 | Disabled |
| gemini     | gemini-2.0-flash          |        4 | Disabled |
+------------+---------------------------+----------+----------+
```

### AFTER (LMS Bionic only)

```
AI Providers
+-----------+--------------------+----------+--------+
| Provider  | Model              | Priority | Status |
+-----------+--------------------+----------+--------+
| lms-local | ornith-1.5-35b-a3b |        1 | Active |
| ollama    | ornith-1.5-35b-a3b |        2 | Active |
+-----------+--------------------+----------+--------+
```

**Note:** RuFlo’s built-in local provider name is `ollama`, but both `lms-local` and `ollama` entries are **OpenAI-compatible → LMS `http://127.0.0.1:1234/v1`** (not Ollama `:11434`). Prefer **LMS** for coding (Burton standing pref: Hermes `discover_models` LMS-only). Check-in models on same LMS: `qwen3.8-27b`, `qwen/qwen3.8-27b`.

Anthropic / openrouter / gemini removed from active config. Do not re-enable.

### Paid-token residual risks

- RuFlo MCP process (PID was listening `:8810`) **inherited `XAI_API_KEY`** in env (not in LaunchAgent plist). Scrub on restart (see §F). Avoid any RuFlo path that would call xAI.
- Hermes CTO profile still lists some `custom:ollama-local` aliases in config; default coding model remains LMS Ornith — keep it.
- Do not set ANTHROPIC_API_KEY / OPENROUTER keys into RuFlo MCP env.

---

## C) Git worktree (house style)

| Role | Path | Branch | Base |
|------|------|--------|------|
| **Live runtime** | `~/.openclaw/workspace` | `master` | sole live risk |
| **Grok Build** | `~/.openclaw/workspace-v2` | `valedia-v2-grok-build` | coding (Grok OPT-IN) |
| **ACCELERATE Ornith** | `~/.openclaw/workspace-accel-ornith` | `accel-ornith` | from `valedia-v2-grok-build` @ `10b24aa` |

Created with:  
`git -C ~/.openclaw/workspace worktree add -b accel-ornith ~/.openclaw/workspace-accel-ornith valedia-v2-grok-build`

Do **not** force-push; do not destroy other worktrees. CTO codes in **`workspace-accel-ornith`**.

---

## D) Slice DONE definition (mandatory — every CTO slice)

ALL four required before next Ornith turn:

1. **Code + tests + live-path proof + ponytail**
2. **Qwen3.8 check-in** PASS/HOLD note (local LMS Qwen3.8 — not cloud)
3. **RUFLO-STATUS one-liner** — how this increases fee-true $
4. **Codebase-memory (CBM) index/update for changed paths**

If CBM update fails → slice is **not DONE** → **HOLD** and retry CBM before starting next lever.

### Qwen3.8 check-in recipe (local)

- Model: LMS `:1234` `qwen3.8-27b` / `qwen/qwen3.8-27b` (or profile alias)
- Output: PASS or HOLD + why the slice raises (or fails to raise) fee-true INFORMED $
- Attach note on the Paperclip issue comment before closing the slice

### CBM update recipe (house pattern)

MCP server: `codebase-memory`  
Binary: `/Users/burtonchatelain/.openclaw/tools/cbm/codebase-memory-mcp`  
Grok/Hermes config: `~/.grok/config.toml` → `[mcp_servers.codebase-memory]`

After each slice, from the agent with CBM MCP connected:

1. `detect_changes` with `project` = CBM project for this worktree (create/index if new):
   - New project path: `/Users/burtonchatelain/.openclaw/workspace-accel-ornith`
   - Name suggestion: `Users-burtonchatelain-.openclaw-workspace-accel-ornith`
   - Prior Grok Build project was `Users-burtonchatelain-.openclaw-workspace-v2`
2. `index_repository` with:
   - `repo_path`: `/Users/burtonchatelain/.openclaw/workspace-accel-ornith`
   - `mode`: `moderate` (or `fast` for tiny doc-only; prefer `moderate` after code)
   - optional `name` override as above

Pseudo / tool calls (not shell — MCP tools):

```
index_repository(
  repo_path="/Users/burtonchatelain/.openclaw/workspace-accel-ornith",
  mode="moderate",
  name="Users-burtonchatelain-.openclaw-workspace-accel-ornith"
)
detect_changes(
  project="Users-burtonchatelain-.openclaw-workspace-accel-ornith",
  scope="impact",
  base_branch="valedia-v2-grok-build"
)
```

If MCP missing / index fails / coverage empty → HOLD.

---

## E) Fences (do not violate)

- No `--live` from Paperclip / Grok / CTO
- No Oil unfence
- No eToro `start_copy`
- No Grok Build launch for this continuous lane
- Thin bankroll / max-1 admit; Valedia runtime = sole live risk
- Local models only for RuFlo + CTO coding + Qwen check-in

---

## F) Kick / ops

- `VALEDIA_CTO_BUILDER=ornith` confirmed in `~/.hermes/profiles/valedia-cto/.env` and Paperclip CTO `adapterConfig.builder=ornith`
- RuFlo MCP LaunchAgent: `com.burton.ruflo-mcp-http` → `~/bin/ruflo-mcp-http.sh` → `:8810`
- Scrub restart (preserve screens `ot-valedia-desk` / `valedia-glance` / `valedia-grok` / `pc-*`):

```bash
# unset XAI for child only; do not kill unrelated screens
launchctl kickstart -k gui/$(id -u)/com.burton.ruflo-mcp-http
# then verify: lsof -iTCP:8810 -sTCP:LISTEN
# verify env scrub: ps eww -p $(lsof -t -iTCP:8810) | tr ' ' '\n' | grep XAI || echo OK_no_XAI
```

If kickstart still inherits user XAI, restart via:

```bash
env -u XAI_API_KEY -u ANTHROPIC_API_KEY -u OPENROUTER_API_KEY \
  ~/bin/ruflo-mcp-http.sh
# (stop LaunchAgent-managed PID first, or temporarily unload)
```

Wake CTO: checkout coding child issue as CTO + optional `POST /api/agents/53d25446-ac6c-4bda-b0c6-5c067b6ddf29/heartbeat/invoke`.

---

## G) Related docs

`PAPERCLIP-INFORMED-RISK-LOOP-1.md`, `PAPERCLIP-VALEDIA-ORG-2026-09-24.md`, `GROK-BUILD-LANE.md`, `CBM-SMOKE.md`, `VALEDIA-VERSIONING.md`, `INFORMED-BEFORE-CREATE.md`, `WEATHER-INFORMED-ADMIT-BRIDGE-1.md`

---

## H) Paperclip issues created 2026-09-26 PT

| ID | UUID | Assignee | Status | Title |
|----|------|----------|--------|-------|
| VAL-5 | `58b1a2ee-2a18-4d98-afb3-451ce0270de8` | Decider | in_progress | ACCEL PILOT (parent) |
| VAL-6 | `3bfc644e-7ee0-4af1-8927-8dc7f0af895e` | CTO | in_progress | continuous Ornith + Qwen3.8 check-in |
| VAL-7 | `5279bfba-12bf-4c49-b349-69f46f5f43a1` | Scout | blocked | rank INFORMED profit levers |
| VAL-8 | `5f976afd-b2d4-481f-809c-5bdf4cc81b2a` | Auditor | in_progress | HOLD fence |
| VAL-9 | `29a89019-e2a2-4fbe-82db-b18b568a9da8` | CTO | in_progress | first coding lever — weather fee-true admit |

### Kick performed
- VAL-9 already checked out by CTO (`checkoutRunId` `4a2685cf-b1ac-4c52-8252-c2f45d4f87c3`) — re-checkout → 409 conflict (expected).
- CTO heartbeat invoke → **202 queued** run `92a0f7db-2a82-4720-a3df-d40f38d20300` (wakeup `68bb707f-0fe4-4bb3-850a-96b3624ed77c`).
- RuFlo MCP restarted PID `64675` on `:8810` with paid cloud keys unset in launcher; process env shows only local `LITELLM_API_KEY` / `LM_STUDIO_API_KEY` (no XAI/Anthropic/OpenRouter).
- Screens preserved: `ot-valedia-desk`, `valedia-glance`, `valedia-grok`, `pc-*`.
