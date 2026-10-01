# HERMES CLEANUP YES-PACK (Burton approved via Valedia AI Assistant after Trader draft)

**Authority:** Burton approved this yes-pack via Valedia AI Assistant after the Trader draft / full audit. Grok Build executes end-to-end. Valedia executor does **not** apply the cleanup — only charters and launches.

**Date:** 2026-09-30
**Deliverable stamp:** `~/.openclaw/workspace-v2/recommendations/HERMES-CLEANUP-YES-PACK-2026-09-30.md` with **GROK_EXIT:0|1** and proof of each item below.
**Also update:** `recommendations/GROK-STATUS.md` briefly (top section for this pack).

**Context / reference (read-only):**
- `recommendations/HERMES-FULL-AUDIT-DRAFT-2026-09-30.md`
- `recommendations/HERMES-DOCS-REFERENCE-Valedia.md`
- Preserve TCC fence from `recommendations/HERMES-TCC-APPDATA-FIX-2026-09-30.md`

---

## VETO (hard — do not do these)

- Do **NOT** unload/load LMS models without explicit need; if Ornith MLX is already loaded (`lms ps`), only retarget yaml ids — no unload/swap.
- Do **NOT** rewrite trading CHANGE-set yaml / tool_search / context / TCC fence on `valedia-trading-{scout,decider,auditor}` beyond what this charter explicitly requires (prefer **zero** trading yaml edits; trading profiles stay on LMS pins).
- Do **NOT** bounce life Matrix/Signal LaunchAgents; do **NOT** `hermes gateway --replace`.
- Do **NOT** `--live` / venue orders / wallets / money path / OKX / VAL-12.
- Do **NOT** delete profile folders — ARCHIVE/hide only from BOTS sidebar.
- Do **NOT** fold Paperclip into Hermes Kanban (Paperclip stays external `:3100` / VAL-*).
- Do **NOT** stop LiteLLM `:4000` (leave running for scripts; already hidden from Desktop picker 2026-09-30).
- Do **NOT** blanket-apply Settings “Applies to” all profiles — profile-scope only.
- Do **NOT** weaken or strip TCC AppData fence (`block-tcc-appdata` hook, approvals.deny find~/Containers, terminal.cwd→workspace-v2, SOUL hard rule on CTO+trading).

---

## KEEP bots/profiles (desk-visible)

- Hermes **root** (Desktop)
- `valedia`
- `valedia-cto`
- `valedia-trading-scout`
- `valedia-trading-decider`
- `valedia-trading-auditor`

## ARCHIVE / hide from BOTS sidebar (do NOT delete profile folders)

Prefer Desktop first-class hide/disable over `rm`. Archive/hide:

- `scout`
- `decider`
- `auditor`
- `uncensored`
- `ugft`
- `code-cloud`
- `omni-code`

---

## APPLY

### A. Auditor-class break pattern (Unknown provider)

Profiles still on provider `custom:ollama-local` with provider unregistered → “Unknown provider”.

- For **ARCHIVED** profiles: either leave archived as-is, or retarget only if needed so hide/disable works cleanly.
- For **KEEP** desk profiles: ensure **no** `custom:ollama-local` as default provider.

### B. Ghost models (Assistant lane)

Hide/strip ollama `custom_providers` from desk-visible **root** / **valedia** (and any KEEP profile that still lists localhost:11434 ghosts) using the `_hermex_picker_hidden_custom_providers` stash pattern.

- LiteLLM already hidden 2026-09-30; backup `config.yaml.bak-20260930-hide-litellm-picker` exists — follow same pattern for ollama ghosts.
- Prefer `only_configured` / no discover dump for ollama.
- Do **NOT** rewrite trading CHANGE-set / TCC fence / LMS pins.

### C. MoA (root / day-to-day desk)

- Set root `moa.enabled` → **false** (day-to-day desk).
- If any MoA block remains configured, retarget aggregator model id to **`ornith-1.5-35b-a3b-mlx`** (live LMS id) rather than bare `ornith-1.5-35b-a3b`.
- **Profile-scope only** — NEVER blanket-apply Settings “Applies to” all profiles.

### D. Root main model default

- Align root main model default to live LMS id **`ornith-1.5-35b-a3b-mlx`** under `custom:lms-local` if currently bare `ornith-1.5-35b-a3b`.
- Verify with `lms ps`; Ornith MLX should already be loaded — do not unload/load without explicit need; if already loaded just retarget yaml id.

### E. Hermes Kanban IdeaInbox clutter

- Clear/archive dead Hermes Kanban IdeaInbox “Default 17” clutter (won't-run / old DONE cards).
- Do **NOT** fold Paperclip into Hermes Kanban. Paperclip stays external `:3100` / VAL-*.

### F. LiteLLM

- Leave `:4000` process running for scripts; already hidden from Desktop picker — do not reverse that hide.

---

## Bounce policy (minimal)

- Bounce **only** profiles you actually changed **IF** a gateway reload is required.
- Prefer Desktop Refresh / soft restart over LaunchAgent kill.
- **NEVER** bounce life Matrix/Signal LaunchAgents.
- Trading LaunchAgents only if a trading profile file you were forced to touch (prefer **zero** trading yaml edits → prefer **zero** trading bounces).

---

## Deliverables

1. Stamp `~/.openclaw/workspace-v2/recommendations/HERMES-CLEANUP-YES-PACK-2026-09-30.md` with:
   - Before/after for each APPLY item
   - Backups created
   - Hide/archive proof for each ARCHIVE profile
   - `lms ps` proof (no unload)
   - Bounce list (or explicit “none”)
   - Verify greps / proofs per item
   - **GROK_EXIT:0** or **GROK_EXIT:1** + blockers
2. Brief update at top of `recommendations/GROK-STATUS.md`
3. End with **GROK_EXIT** and the stamp path

Execute fully. End GROK_EXIT:N.
