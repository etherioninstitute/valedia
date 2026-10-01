# GROK-CHARTER — Ornith 1.5 9B optional paper-tick seasoning

**Date:** 2026-09-30 ~05:18 PT  
**Worktree:** `~/.openclaw/workspace-v2`  
**Model id:** `ornith-1.5-9b` (LMS; already downloaded)

## Intent
Wire Ornith 1.5 **9B** as an **optional** paper-tick seasoning helper — same role as Jev/MiroFish seasoning: annotate/rank flavor only. **Never** OMS, Kelly, allowlist, or live authority.

## DO
1. Thin client/helper (e.g. `scripts/integrations/ornith_seasoning.py`) hitting local LMS `http://127.0.0.1:1234` with model `ornith-1.5-9b`.
2. Flag `VALEDIA_ORNITH_SEASON=1` (default **off**). Fail-open if model unloaded/timeout.
3. Hook into all-knowing movers or paper tick path as **seasoning_only** fields (like Jev: `changes_rank=false` unless you prove a safe annotate-only contract).
4. Do **not** unload/reload qwen or 35b-a3b. Read-only probe: if 9B not loaded, skip with clear label (or print suggested `lms load` — no auto apply unless already the pattern elsewhere with ORNITH_LOAD_APPLY).
5. Tests with mocked LMS + optional live probe gated.
6. Stamp `recommendations/ORNITH-9B-SEASONING-2026-09-30.md` + update GROK-STATUS. Ponytail. RuFlo + codebase-memory.
7. GROK_EXIT:0

## DO NOT
- Money path / create_order / --live / venue orders
- Replace desk Hermes model with 9B
- LMS auto-unload
- Oil harvest, ALLOWLIST widen, MIROFISH_AUTO_RAISE
