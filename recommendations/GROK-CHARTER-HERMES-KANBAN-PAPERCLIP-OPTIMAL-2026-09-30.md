# HERMES KANBAN + PAPERCLIP OPTIMAL (Burton authorized; Valedia Trader APPROVE pack)

**Authority:** Burton already authorized Grok to handle. Valedia Trader APPROVE pack below. Grok Build executes end-to-end. Valedia executor does **not** apply — only charters and launches.

**Date:** 2026-09-30
**Deliverable stamp:** `~/.openclaw/workspace-v2/recommendations/HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30.md` with **GROK_EXIT:0|1** and proof of each item below.
**Also update:** `recommendations/GROK-STATUS.md` briefly (top section for this pack).

**Context / reference (read-only):**
- `recommendations/HERMES-CLEANUP-YES-PACK-2026-09-30.md`
- `recommendations/HERMES-LIFE-GATEWAY-RELOAD-2026-09-30.md`
- `recommendations/HERMES-FULL-AUDIT-DRAFT-2026-09-30.md`
- `recommendations/HERMES-DOCS-REFERENCE-Valedia.md`
- Preserve TCC fence from prior stamps / CTO+trading SOUL hard rule

---

## Soften prior Kanban line (Trader APPROVE)

### Still VETO
- Paperclip as a Hermes Kanban *profile*
- Dual-write sync between Hermes Kanban ↔ Paperclip
- Replacing the Paperclip UI / folding Paperclip ownership into Hermes

### APPROVE
- Hermes Kanban stays the visual ops board Burton likes
- Optional **read-mostly** Paperclip mirror: Hermes Kanban cards that deep-link `VAL-*` on Paperclip `:3100`
- Paperclip remains **source of truth**; write path remains `skills/paperclip` on CTO / trading — not Kanban dual-write

---

## VETO (hard — do not do these)

- Do **NOT** make Paperclip a Hermes Kanban profile, dual-write sync, or replace Paperclip UI.
- Do **NOT** rewrite trading CHANGE-set yaml / tool_search / context / TCC fence on `valedia-trading-{scout,decider,auditor}` beyond a **kanban allowlist** touch if strictly required (prefer **zero** trading yaml edits).
- Do **NOT** strip or weaken TCC AppData fence (`block-tcc-appdata` hook, approvals.deny find~/Containers, terminal.cwd→workspace-v2, SOUL hard rule on CTO+trading).
- Do **NOT** unload/load LMS models; if Ornith MLX is already loaded (`lms ps`), only retarget yaml — no unload/swap.
- Do **NOT** stop / kill LiteLLM `:4000`.
- Do **NOT** bounce life Matrix/Signal LaunchAgents; do **NOT** `hermes gateway --replace`.
- Do **NOT** `--live` / venue orders / wallets / money path / OKX / VAL-12.
- Do **NOT** delete profile folders — hide/allowlist/archive only; folders stay.
- Do **NOT** revive archived ghost assignees (`scout` / `decider` / `auditor` bare profiles, or other non-KEEP) onto Default board cards.
- Do **NOT** turn MoA back on; do **NOT** add Ollama as auxiliary/fallback.
- Do **NOT** bump trading context_length or rewrite trading CHANGE-set beyond allowlist-if-needed.
- Do **NOT** set Fallback to obliterated kits or `Ornith:latest` via Ollama.

---

## KEEP bots/profiles (desk-visible / assignee allowlist)

- Hermes **root** / default / Hermes
- `valedia`
- `valedia-cto`
- `valedia-trading-scout`
- `valedia-trading-decider`
- `valedia-trading-auditor`

**Out of allowlist** (already archived/hidden in yes-pack — leave out unless config *requires* listing to keep hide working): `scout`, `decider`, `auditor`, `uncensored`, `ugft`, `code-cloud`, `omni-code`. Do **not** delete their folders.

---

## APPLY

### (1) Empty Default board — restore useful feel

Cleanup of archived 17 dead IdeaInbox cards was **correct** — do not un-archive those unless a card is still valuable **AND** assignee is a KEEP profile. Do **not** revive scout/decider/auditor ghosts.

**Seed a small useful Default set** (assignees ONLY KEEP profiles above):

1. **Desk health** — LMS Ornith mlx loaded? Paperclip `:3100` up?
2. **Paperclip open VAL-* glance** — read via skill/API; link out to `:3100` (read-mostly mirror, not dual-write)
3. **Trading paper lane status** — scout / decider / auditor (status glance only; assignees = KEEP trading profiles if needed)
4. **1–2 real next actions** — concrete, useful, KEEP assignees only

Also: if root `agent.disabled_toolsets` includes `kanban`, Grok may remove **ONLY** `kanban` from that deny list for root/life (so the visual board works). Do **not** touch trading CHANGE-set / TCC toolset denylists except the narrow allowlist case in (3) if required.

### (2) Plugin Hermes ↔ Paperclip — narrow

- Thin mirror / plugin **or** Kanban cards + deep links + existing `paperclip` skill is enough.
- VETO fold / bidirectional ownership / dual-write sync.
- Paperclip = SoT on `:3100`; Hermes Kanban cards deep-link `VAL-*`; writes stay on skills/paperclip (CTO/trading).

### (3) Orchestrator / Assignee ghosts

Sidebar hide ≠ Kanban scan. Archived profiles can still appear in orchestrator/assignee dropdowns.

- Allowlist orchestrator/assignee dropdowns to: `default`/`Hermes`, `valedia`, `valedia-cto`, `valedia-trading-scout`, `valedia-trading-decider`, `valedia-trading-auditor`.
- Prefer **config allowlist** or move archived profiles out of Hermes profile **scan path**.
- Do **not** delete folders yet.
- `omni-code` was archived in yes-pack — leave out of allowlist unless config requires it for hide to stick.

### (4) Model optimality

- **Main:** keep LMS Bionic `ornith-1.5-35b-a3b-mlx`
- **MoA:** stay **OFF** (`enabled: false`)
- **Auxiliary:** keep `free_only` / aux on main/mlx; **no Ollama**
- **Fallback:** if empty or same as main, set explicit Fallback = **`qwen/qwen3.8-27b`** (live LMS catalog) for Ornith-unload resilience; **NOT** obliterated kits; **NOT** Ollama `Ornith:latest`
- **Provider hygiene:** tighten discover so stale bare `ornith-1.5-35b-a3b` + obliterated kits don't clutter next to mlx — `only_configured` / current LMS catalog only
- Do **not** bump trading context / rewrite trading CHANGE-set

---

## Bounce policy (minimal)

- Prefer **soft Desktop refresh** over LaunchAgent cascade.
- Life gateway was already reloaded today (stamp `HERMES-LIFE-GATEWAY-RELOAD-2026-09-30.md`) — **only bounce if required** for allowlist / disabled_toolsets to take effect, and **document why** in the stamp.
- **NEVER** bounce life Matrix/Signal LaunchAgents.
- Trading LaunchAgents only if a trading profile file you were forced to touch for allowlist (prefer **zero** trading yaml edits → prefer **zero** trading bounces).
- No LMS unload/load. No LiteLLM kill.

---


---

## URGENT P0 ADDENDUM — Cron model scrub (Burton 2026-09-30 ~16:45 PT)

**MUST complete in THIS same stamp** `HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30.md` (new section **Cron model scrub** with before/after ids). Full note: `recommendations/GROK-URGENT-ADDENDUM-CRON-MODEL-SCRUB-2026-09-30.md`.

Burton screenshot: Important-mail monitor Last run failed HTTP 400 Invalid model identifier `ornith-1.5-35b-a3b`. UI shows `ornith-1.5-35b-a3b-mlx` but runtime still hits bare id. Scheduled jobs all broken.

### P0 tasks
1. Audit ALL Hermes root cron jobs (`~/.hermes/cron/jobs.json` + any Desktop scheduled mirror) for bare `ornith-1.5-35b-a3b` or other dead ids — including cases where jobs.json already says `-mlx` but the wire request is still bare.
2. Retarget every life/root cron to `ornith-1.5-35b-a3b-mlx` (or explicit Fallback `qwen/qwen3.8-27b` only where intended). Fix root cause if pin≠wire (provider catalog still listing bare key; null model on chief-of-staff-daily; context_length on mlx entry).
3. Prove Important-mail monitor + siblings no longer 400 on bare id (trigger one or verify jobs.json + resolve + last_error).
4. Do **NOT** bounce trading LaunchAgents; no LMS unload; no Matrix/Signal. Trading crons only if they still have bare id and Trader lane allows — prefer life/root only.

### Stamp
Include **Cron model scrub** before/after table + proof + bounce=none (unless documented). Elevate this above board-seed niceties if timeboxed.

---

## Deliverables

1. Stamp `~/.openclaw/workspace-v2/recommendations/HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30.md` with:
   - Before/after for each APPLY item (1–4)
   - Soften-prior-Kanban proof (APPROVE read-mostly mirror; VETO dual-write/profile-fold)
   - Default board seed list + assignees (KEEP only)
   - Allowlist / scan-path proof for orchestrator+assignee
   - Model main / MoA / aux / fallback / provider-hygiene proofs (`lms ps`, config greps)
   - `disabled_toolsets` change (or explicit “kanban was not denied”)
   - Backups created
   - Bounce list (or explicit “none”) + why if life bounced
   - Verify greps / proofs per item
   - **GROK_EXIT:0** or **GROK_EXIT:1** + blockers
2. Brief update at top of `recommendations/GROK-STATUS.md`
3. End with **GROK_EXIT** and the stamp path

Execute fully. End GROK_EXIT:N.

## P0 ADDENDUM — HERMES ALL ERRORS (Burton 2026-09-30 16:47:45 PT)

**READ IMMEDIATELY:** `recommendations/GROK-ADDENDUM-HERMES-ALL-ERRORS-2026-09-30.md`

Burton P0: inventory **ALL** Hermes errors (Desktop scheduled last-run failures, gateway logs, BOTS interrupted, Unknown provider, HTTP 400 bare model ids, Kanban, `hermes doctor`, LaunchAgent crashes) → dump evidence into stamp → fix every in-scope life/root/Desktop issue (bare ornith→mlx on ALL cron + any config still sending bare id; ghost providers; orchestrator allowlist; Kanban seed + read-only Paperclip mirror; Fallback `qwen/qwen3.8-27b`; catalog hygiene; MoA OFF). Trading: fix bare model id only if safe; else document for Trader. Re-verify Important-mail + siblings after pin. Stamp each error → fixed | deferred+why | needs-Burton. Prefer same stamp `HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30.md` or follow-on `HERMES-ALL-ERRORS-FIX-2026-09-30.md`.

Original APPLY (1)–(4) + fences still stand. Do not exit without this P0.

---

# !!!!! LOUD MUST-DO — Cron bare-resolver (Trader P0 deeper) — 2026-09-30 16:48:37 PDT !!!!!

**READ NOW:** `recommendations/GROK-ADDENDUM-CRON-BARE-RESOLVER-2026-09-30.md`

**jobs.json mlx pins are NOT enough.** Important-mail / valedia-inbox-triage / weekly-memory-review / commerce-self-evolve already `ornith-1.5-35b-a3b-mlx` + `custom:lms-local`, yet last_error still HTTP 400 / ValueError naming **bare** `ornith-1.5-35b-a3b`. Root `model.default` / `providers.custom:lms-local.model` / `cron.model` already mlx.

**Grok MUST:**
1. Remove/hide bare `ornith-1.5-35b-a3b` (+ stale obliterated kits) from `providers.custom:lms-local.models` so resolver cannot land on bare sibling beside `-mlx`.
2. Find where request still sends bare (alias strip, model_snapshot, gateway cache, providers.models bare sibling, auth / Desktop mirror).
3. Eliminate bare Ornith as selectable LMS id on life/root.
4. Clear/retry **Important-mail** + **valedia-inbox** after fix; **stamp fresh-run proof** (not stale last_error).
5. Fences: MoA OFF; no dual-write; no trading CHANGE/TCC/LMS unload; no Matrix/Signal bounce. No trading `profiles/*/cron/jobs.json` found — trading LAs stay fenced.

**Stamp MUST include subsection `### Cron bare-resolver`** with root-cause + providers.models before/after + fresh-run proof. Elevate above Kanban seed if timeboxed. Do **not** GROK_EXIT claiming cron fixed from jobs.json alone.

