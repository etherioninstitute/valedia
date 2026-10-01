# GROK CHARTER — Hermes MemPalace LIVE + Kanban unblock + Paperclip skill rank (2026-09-30 ~20:40 PT)

**Mode:** EXECUTE. Paper/ops. Stamp `HERMES-MEMPALACE-KANBAN-PAPERCLIP-2026-09-30.md` with `GROK_EXIT` + proof.
**Authority:** Valedia Trader executor chartered this. One Grok Build pass via `grok-valedia` / screen `valedia-grok`.
**Why:** MemPalace is listed/partially wired but not fully LIVE across Hermes Desktop profiles. Five Kanban BLOCKED glance cards still sit blocked after hub stamp; clear those that can be auto-proved. Rank Hermes catalog Paperclip skills for Valedia (local company + OpenClaw/Hermes control plane) and recommend ONE primary — do not install unless already partially present and safe.

## Facts (do not invent — verify live)

### MemPalace
- Install: `~/MemPalace` + `~/.mempalace` (palace chroma + `knowledge_graph.sqlite3`). CLI: `mempalace`. MCP module: `python3 -m mempalace.mcp_server` via `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`.
- Hermes expects MemPalace as an **MCP server** (`mcp_servers.mempalace`), **not** as a Memory Settings provider plugin. Available memory *providers* are holographic/honcho/mem0/etc. Root `memory.provider` is `""` (Built-in). Valedia uses `holographic`. That is fine — MemPalace sits alongside Built-in/holographic as MCP tools.
- Enabled state at charter time:
  - **default/root** `~/.hermes/config.yaml`: `mcp_servers.mempalace.enabled: false` (12 read tools in include)
  - **valedia**, **valedia-cto**: `enabled: true`
  - **valedia-trading-{scout,decider,auditor}**: no `mempalace` block
- SOUL (root) already instructs `mempalace_search` / `mempalace_add_drawer` / `kg_add`, but the MCP **include** list is read-only (no `mempalace_add_drawer`, `mempalace_kg_add`, `mempalace_diary_write`). Module exposes those write tools.
- Prior QuantDinger→MemPalace work: `QUANTDINGER-SUBSUME-2026-09-30.md`; clone under `~/MemPalace/sources/repos/github/QuantDinger`.

### Kanban (glance; Paperclip = truth for VAL-*)
- Prior stamps: `HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30.md`, `HERMES-HUB-PAPERCLIP-OPTIMAL-2026-09-30.md`.
- Five BLOCKED cards (`created_by=grok-charter`), all glance by design:
  | id | assignee | key | title |
  |---|---|---|---|
  | `t_12af3f1f` | default | `desk-health-20260930` | Desk health — Ornith mlx + Paperclip :3100 |
  | `t_96e95651` | valedia-cto | `paperclip-val-glance-20260930` | Paperclip open VAL glance (read-only links) |
  | `t_30dedc05` | valedia-trading-auditor | `trading-paper-lane-20260930` | Trading paper lane status — glance only |
  | `t_d4cf812c` | default | `next-cron-mlx-proof-20260930` | Confirm Important-mail / inbox triage on ornith mlx |
  | `t_83d0b4a8` | valedia | `next-assignee-allowlist-20260930` | Refresh Desktop; assignee list KEEP profiles only |
- Desktop `hermes serve` port: read latest `HERMES_BACKEND_READY` from `~/.hermes/logs/desktop.log` (was `58677` at 20:14 PT). Prior hub stamp got `GET /api/plugins/kanban/board` HTTP 200 without cookie on localhost. If 401, use same pattern as hub stamp / Hermes CLI / kanban.db — do not invent auth scraping.
- Company Paperclip: `4fb233c2-27e9-4d0d-a217-2dc7205210a0` on `http://127.0.0.1:3100`. VAL-* truth lives there.

### Paperclip skills (catalog search `hermes skills search paperclip` — 11 hits)
Relevant for Valedia (local company + OpenClaw/Hermes control plane):
1. **clawhub `paperclip`** — Run Paperclip locally for agent orchestration / AI company
2. **clawhub `paperclip-orchestration`** (Paperclip Orchestration) — Connect OpenClaw Gateway to Paperclip; diagnose onboarding
3. **clawhub `paperclip-resilience`** (Paperclip Resilience) — Production resilience patterns for Paperclip agent orchestration
4. skills.sh `paperclipai/paperclip/paperclip` + create-agent/create-plugin / para-memory-files — adjacent
Already installed locally (profile skills, not hub): `~/.hermes/profiles/{valedia-cto,valedia-trading-scout,decider,auditor}/skills/paperclip` (heartbeat/API skill for company agents).
**VETO / not for Valedia desk:** scientific/biomedical `paperclip` (k-dense / FDA papers), **Deadpost**.

## Execute

### (1) MemPalace fully connected + LIVE
1. Prove package + palace: `mempalace status` (or MCP `mempalace_status`) + one `mempalace_search` (or CLI search) that returns real drawers/wings. Record wing/room counts.
2. Wire Hermes MCP so it is **usable**, not just listed:
   - Set `mcp_servers.mempalace.enabled: true` on **default/root**.
   - Add the same mempalace MCP block (`enabled: true`, same command/args/connect_timeout) to **valedia-trading-scout**, **valedia-trading-decider**, **valedia-trading-auditor** if missing (mirror valedia-cto include list as baseline).
   - Expand **include** on profiles that already have MemPalace (and root) to add write tools SOUL expects if missing: `mempalace_add_drawer`, `mempalace_kg_add`, `mempalace_diary_write` (and `mempalace_delete_drawer` only if you need symmetry — prefer not). Do **not** replace Built-in/holographic memory provider with MemPalace; document clearly that Memory Settings "Built-in" is expected and MemPalace is MCP alongside.
3. Prove LIVE from Hermes side: after config, either (a) Desktop/gateway already picks up MCP on next session without bounce — prove via MCP schema cache / tool registration / a one-shot hermes tool invoke if available, OR (b) minimal Desktop Electron relaunch **only if** MemPalace plugin/MCP cannot load otherwise (same soft pattern as hub stamp: quit Electron parent only, `open -a` Hermes.app; never bounce life/trading/LMS). Record before/after proof lines.
4. Touch SOUL only if needed for trading profiles to mention mempalace recall (prefer pointer to existing root SOUL / hub). No duct-tape watchers.

### (2) Clear Kanban BLOCKED cards that do NOT need Burton
Auto-prove and move each card out of `blocked` (to `done` / archived / completed — use the board's normal terminal state; do **not** dual-write VAL issues into Kanban as SoT):

| Card | Auto-prove how | Action if proved |
|---|---|---|
| desk-health `t_12af3f1f` | `lms ps` shows Ornith mlx loaded; `curl :3100` Paperclip up | unblock → done |
| Paperclip glance `t_96e95651` | GET Paperclip `/api/.../issues` or company issues; glance links still valid | unblock → done (glance satisfied) |
| trading paper lane `t_30dedc05` | trading gateway pids present; paper lane status readable without `--live` | unblock → done |
| Important-mail mlx `t_d4cf812c` | cron job `8bbbc09fff56` model is ornith mlx; last run evidence from prior stamp or fresh cron read | unblock → done if mlx-proven; if last_error is Gmail auth / himalaya login secret → **leave blocked** and document Burton-only |
| assignee allowlist `t_83d0b4a8` | `list_profiles` / profiles dir shows KEEP only (default+valedia+cto+3 trading); ghosts still in `.deleted` | unblock → done |

**Leave blocked ONLY** if truly needs Burton Gmail login or similar secret. Document which + why.

Kanban API: prefer `GET/PATCH` on Desktop serve port from `desktop.log`. Fallback: direct `kanban.db` sqlite if HTTP blocked — document which path. No Paperclip issue PATCH from this pass except read.

### (3) Rank Paperclip catalog skills → ONE primary recommendation
Rank for Valedia goals (local Paperclip company on `:3100` + OpenClaw/Hermes control plane). Exclude science Paperclip CLI and Deadpost.

Recommend **ONE** primary to install/toggle ON later:
- Likely **Paperclip Orchestration** (`paperclip-orchestration` / clawhub) if OpenClaw↔Paperclip gateway glue is the gap, OR **Paperclip Resilience** if company heartbeats/recovery are the pain, OR base clawhub **paperclip** if hub skill is missing while only profile copies exist.
- **Do NOT install** in this pass unless that skill is already partially installed and enabling it is clearly safe (no dual-write, no money path). Document recommendation + why + install yes/no.
- Note existing profile `skills/paperclip` remains the heartbeat write path; Kanban stays glance.

### (4) Stamp
Write `~/.openclaw/workspace-v2/recommendations/HERMES-MEMPALACE-KANBAN-PAPERCLIP-2026-09-30.md` with:
- MemPalace proof lines (status + search + which profiles enabled + write-tool include yes/no + Desktop relaunch yes/no)
- Kanban: which cards moved vs left blocked + why
- Paperclip skill recommendation (name + identifier + install yes/no + why)
- Burton-only remaining actions (e.g. Gmail)
- Fences held
- `GROK_EXIT:0|1`

Optionally refresh `VALEDIA-HUB.md` MemPalace one-liner if you touch the hub; no watcher daemon.

## Fences (hard)
- No `--live`, no venue orders, no money path, no LMS auto-unload, no LiteLLM kill, no trading gateway bounce, no Paperclip dual-write of VAL issues into Kanban as source of truth.
- No duct-tape Python watcher loops.
- Paperclip company stays truth for VAL-*; Kanban is glance.
- Do not collide with other trading screens; reuse `valedia-grok` via this launch only.
- Prefer read/fix/prove; minimal Desktop relaunch only if MemPalace MCP needs it.
- No MoA ON. No oil/weather unfence. No TCC weaken.

## Prior context (read-only)
- `HERMES-HUB-PAPERCLIP-OPTIMAL-2026-09-30.md`
- `HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30.md`
- `VALEDIA-HUB.md`
- `QUANTDINGER-SUBSUME-2026-09-30.md`
