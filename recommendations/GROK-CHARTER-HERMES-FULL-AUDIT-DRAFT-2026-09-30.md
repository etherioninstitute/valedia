# Grok Build charter — Hermes FULL line-by-line audit (DRAFT ONLY) 2026-09-30

**Goal:** Exhaustive audit of Burton’s live Hermes setup vs the mined Hermes Agent docs. **DRAFT ONLY — zero config applies, zero LaunchAgent bounces, zero LMS load/unload.**

**Inputs (required):**
1. `recommendations/HERMES-VALEDIA-OPTIMIZE-PLAN.md` (gap plan)
2. Mined docs: if present on disk use it; else Valedia Assistant staged `/workspace/HERMES-DOCS-REFERENCE-Valedia.md` may be missing on Mac — if missing, fetch https://hermes-agent.nousresearch.com/docs/llms-full.txt (or site llms.txt index) and treat as the authority mine. Exclude User Stories & Use Cases.
3. Live configs: `~/.hermes/config.yaml` + profiles `valedia`, `valedia-cto`, `valedia-trading-{scout,decider,auditor}`, relevant `.env` **names only** (no secrets in stamp), gateway_state, LaunchAgents list, `lms ps` / library listing if available
4. Prior stamps: HERMES-TCC-APPDATA-FIX, HERMES-ASSISTANT-OPTIMIZE (infra), Trader CHANGE notes

**Method:**
- Walk docs by section (config knobs, gateway, tools, MCP, memory, MoA, providers, Mac/local LLM, optimization). For each relevant knob/behavior: **FACT** (live value) vs **DOCS** vs **Valedia goal** (Assistant life / Trader desk) → KEEP / CHANGE (draft) / DEFER / VETO.
- Cover **every** top-level key in root + each profile yaml (not a hurried sample). Call out unknowns honestly.
- Respect fences: no --live, no LMS unload, no gateway --replace, trading CHANGE already applied, TCC fence keep, Trader vetoes on MoA-from-Grok / MemPalace-root / LMS retarget from infra pass — list MoA etc. as Assistant-owned draft CHANGE if docs+goals warrant.
- MLX/omlx: option section only, no cutover plan as required work.

**Deliverable:** `recommendations/HERMES-FULL-AUDIT-DRAFT-2026-09-30.md` with:
- Coverage checklist (docs sections mined + configs walked)
- Line-by-line / key-by-key tables per file
- Prioritized CHANGE backlog for Burton yes (Assistant vs Trader lanes)
- Explicit “not 100% applied” statement
- Update GROK-STATUS.md top
- End **GROK_EXIT:0** (audit complete) or **1** if blocked (e.g. docs unreachable)

**Forbidden:** any yaml/plist/.env edit; any kickstart; any lms load/unload; any SOUL rewrite.
