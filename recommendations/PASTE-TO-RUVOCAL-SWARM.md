# Paste into local RuVocal / Ruflo swarm goal UI

Source of truth on disk (Claude Code filled these):
- `recommendations/RUFLO-MASTER-GOAL.md`
- `recommendations/CLAUDE-TO-RUFLO-HANDOFF.md` (A–E, HANDOFF READY)
- `recommendations/RUFLO-STATUS.md`

## Ready-to-paste objective (sprint D only)

```text
HARD FENCE: Coder/auditor only. No --live, no venue orders, no trading, no kalshi-agent copy, no wallet/spend. Valedia stays sole risk.

Workspace: ~/.openclaw/workspace
Read first: recommendations/CLAUDE-TO-RUFLO-HANDOFF.md + RUFLO-MASTER-GOAL.md + RUFLO-STATUS.md

Sprint D ONLY:
1) Audit-verify (HOLDS/FAILS, file:line + live-path proof): record_entry/gate/cap/fees on master_allocator.py (:473/:391/:296); confirm kalshi_executor.run inert; valedia_consistency_check.py PASS. Do not redo Claude’s wiring — verify it.
2) B1 coding: harden sports_engine.py — fees→fee_engine, record_entry→real ledger, should_trade_category gate, never log DRY_RUN as executed. Tests + ponytail + live-path proof.
3) Update recommendations/RUFLO-STATUS.md with evidence.
4) STOP for Burton/Claude review before B2+.

Prefer local Ollama models (Ornith / qwen3.8-turbo-coder / Uncensored) when GPU steward allows.
```

Yes: Claude Code already created the goal/handoff text. Paste the block above (or point the swarm at those three files). Don’t paste the old PASTE-TO-CLAUDE prompt — that was for filling the handoff, which is done.
