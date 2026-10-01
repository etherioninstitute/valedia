# WEATHER-TWO-SIDED-FEE-TRUE-1 — prefer two-sided fee-true books; refuse one-sided 1¢ lottery

**Date:** 2026-09-26 ~04:00 PT  
**Status:** todo (filed)  
**Paperclip:** VAL-10 `c6778eec-b1fa-4f6a-9c2a-aec2f8620ee7`  
**Goal id:** `3b85af61-87e4-4e9a-825d-1994546cb6a3` — INFORMED weather fee-true only — improve NWS CLI + refuse lottery  
**Company:** `4fb233c2-27e9-4d0d-a217-2dc7205210a0` (Valedia)  
**Labels:** `LOCAL-CHARTER` (coding + research under INFORMED) · `UPSTREAM` parent = INFORMED weather fee-true only  
**Live risk:** **Valedia runtime sole live risk** — never Paperclip / Hermes / Grok Build

---

## Context (ops proof)

WEATHER-FIRST-LIVE-ADMIT-1 Grok Build exit **PASS** ~2026-09-25 **20:14–20:16 PT** was **ops-only no-trade**:

- ~79 fee-true rows were **1¢ one-sided asks** (lottery-shaped books).
- Nearest two-sided candidate `KXLOWTSEA…B53.5` had YES FV below mid → admit correctly refused.
- Fee-true path is drowning in one-sided 1¢ lottery; two-sided depth that could clear INFORMED gates is scarce / mis-ranked.

Related charters: `WEATHER-FIRST-LIVE-ADMIT-1.md`, `WEATHER-INFORMED-ADMIT-BRIDGE-1.md`, `ACCELERATE-1.md`.

---

## Mission

Make the INFORMED weather **fee-true** path **prefer two-sided depth** and **refuse one-sided 1¢ lottery**, without loosening the hard **0.05 / 0.90** INFORMED band (`LOTTERY_PX` / `LATE_PX`).

---

## Roles

### Scout (`LOCAL-CHARTER` research)
Rank why one-sided 1¢ books dominate fee-true weather rows and where **FV vs mid** fails admit on the rare two-sided books. Written ranking + concrete fail modes (no code). Tie to VAL-1 / VAL-3 NWS CLI research.

### CTO — Ornith / local LMS only (`LOCAL-CHARTER` code)
Code in ACCEL worktree:

| Role | Path | Branch |
|------|------|--------|
| **ACCEL Ornith** | `~/.openclaw/workspace-accel-ornith` | `accel-ornith` |
| Live runtime (do not code here) | `~/.openclaw/workspace` | `master` |

- Builder: Ornith continuous via RuFlo; LOCAL LMS only (`http://127.0.0.1:1234/v1`). No anthropic / openrouter / xAI.
- **No Grok Build unless Burton opts in** (`VALEDIA_CTO_BUILDER=grok-build`).
- Do **not** loosen 0.05 / 0.90; prefer ranking / filter so two-sided books surface and one-sided lottery is refused earlier.

Slice DONE (all required before next Ornith turn):
1. Code + tests + live-path proof + ponytail
2. Qwen3.8 check-in PASS/HOLD (LMS local)
3. RUFLO-STATUS one-liner (how this increases fee-true $)
4. CBM index/update for changed paths

### Auditor HOLD fence (`UPSTREAM` risk fence)
- No `--live`, no oil unfence, no eToro `start_copy` from Paperclip / Hermes.
- **Live admit = Valedia runtime only.**
- HOLD/FAIL any patch that loosens 0.05/0.90, mass-creates, or routes live risk through Paperclip / Grok Build.

---

## Acceptance criteria

1. Scout note: ranked causes of one-sided 1¢ dominance + FV-vs-mid admit fails on two-sided examples.
2. Fee-true path (worktree) prefers two-sided depth; one-sided 1¢ lottery is refused without changing LOTTERY_PX=0.05 or LATE_PX=0.90.
3. Tests + live-path proof (stub / dry) show refuse on one-sided lottery and non-regress on prior bridge/admit gates.
4. No `--live` / oil / eToro start_copy from this ticket; Valedia remains sole live risk.
5. No Grok Build unless Burton explicitly opts in.
6. Paperclip issue stays under INFORMED weather goal `3b85af61-87e4-4e9a-825d-1994546cb6a3` (not ACCELERATE alone).

---

## Out of scope

Oil unfence, mass creates, loosening lottery/late band, flipping `WEATHER_LIVE` from Paperclip/Build, waking Scout heartbeat unsolicited, restarting Hermes/Paperclip, placing orders.

---

## Related Paperclip

- VAL-1 / VAL-3 — NWS CLI fee-true + refuse-lottery research (same INFORMED weather goal)
- VAL-9 — ACCEL unblock INFORMED weather fee-true admit (ACCELERATE child goal)
- VAL-8 — Auditor HOLD fence (ACCELERATE)
- This issue: **VAL-10** WEATHER-TWO-SIDED-FEE-TRUE-1

---

## Done when

Scout ranking delivered; CTO worktree patch prefers two-sided / refuses one-sided lottery with tests + proof; Auditor HOLD not tripped; 0.05/0.90 band unchanged; no live orders from Paperclip/Hermes/Grok Build.
