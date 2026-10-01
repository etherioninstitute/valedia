# Grok Build charter — Hermes infra/reliability optimize (2026-09-30) REVISED

**Authority:** Burton asked Grok Build to handle Hermes setup/optimize. **Valedia Trader APPROVE 2026-09-30:** infra/reliability/tooling only (Grok Bot tokens OK). This file supersedes the earlier MoA-inclusive draft.

## VETO (hard — do not do these)
- Do **NOT** flip or edit `moa` on root or any profile
- Do **NOT** MemPalace-on-root edits; do not enable root MCPs
- Do **NOT** rewrite trading `valedia-trading-{scout,decider,auditor}` model/context/tool_search/delegation (CHANGE set already applied ~13:24 PT)
- Do **NOT** strip or weaken TCC App Data fence (`block-tcc-appdata` hook, approvals.deny find~/Containers, terminal.cwd→workspace-v2, SOUL hard rule on CTO+trading)
- Do **NOT** LMS load/unload, Ornith swap, model.default retarget, catalog rewrite (pins in LMS-CATALOG-VALEDIA-2026-09-30)
- Do **NOT** `--live` / money path / VAL-12 / OKX
- Do **NOT** `hermes gateway --replace` or bounce life Matrix/Signal LaunchAgents
- Personality / SOUL voice beyond TCC hard rule: **Assistant-owned** — do not rewrite SOUL for style

## APPLY (infra / reliability / tooling only)

### A. Root `~/.hermes/config.yaml`
1. `tools.tool_search.enabled` → **false** (tooling hygiene; Option B speed)
2. Leave **moa** untouched (still enabled — document as Assistant-owned follow-up)
3. Leave `model.default` / provider / MemPalace / platforms untouched

### B. Profile `valedia`
1. `tools.tool_search.enabled` → **false**
2. Disable **ponytail** plugin (move off enabled / onto disabled) — tooling; root already has it off
3. Leave MoA, MemPalace MCP, model ids, Matrix blocks untouched

### C. Profile `valedia-cto`
1. Scrub `delegation`/`cron` leftovers on `custom:ollama-local` / stale ollama model ids → `custom:lms-local` / `ornith-1.5-35b-a3b-mlx` **or clear** those fields if unused (reliability: dead ollama wake risk)
2. If `tool_search.enabled` is `auto` → **false**
3. **Preserve** TCC fence exactly
4. No MoA on; no model retarget of desk default beyond the ollama→lms scrub above for delegation/cron only

### D. Trading profiles
- **Verify only:** tool_search false, ctx 65536 decider/auditor, delegation/cron on lms-local mlx Ornith, TCC fence present
- If verify fails on those keys only, restore them; do not widen edits

## Bounce (minimal)
- Bounce **only** LaunchAgents whose yaml you changed among: `valedia-cto` and (if you had to restore) trading three
- Prefer `launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway-valedia-cto` (and trading labels only if restore)
- **NEVER** bounce life Matrix/Signal; **NEVER** `--replace`
- Root/life gateway: if root yaml tool_search-only change needs reload, prefer kickstart of the **life** Hermes agent LaunchAgent **only if** you can identify it without touching Matrix/Signal; if unclear, leave root reload to Assistant and document in stamp

## Deliverables
1. Stamp `recommendations/HERMES-ASSISTANT-OPTIMIZE-2026-09-30.md` — before/after, backups `*.bak-20260930-assistant-optimize`, bounce list, verify greps, `lms ps` unchanged (no unload)
2. Explicit section **DEFERRED to Assistant:** root MoA off, model mlx id align, Matrix reconnect, SOUL voice
3. Update top of `recommendations/GROK-STATUS.md`
4. End **GROK_EXIT:0** or **GROK_EXIT:1** + blockers

## Context
- Plan: `recommendations/HERMES-VALEDIA-OPTIMIZE-PLAN.md`
- Prior TCC stamp: `recommendations/HERMES-TCC-APPDATA-FIX-2026-09-30.md` (do not re-charter; fold around fence)
- RuFlo MCP `http://127.0.0.1:8810/mcp` required

Execute fully. End GROK_EXIT:N.
