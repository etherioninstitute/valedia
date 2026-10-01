# VALEDIA STALL AUDIT — Why the loop keeps happening & why Valedia isn’t getting smarter / profiting

**When:** 2026-09-29 13:14 PT  
**Actor:** Grok Build (executor on M1 `b3f456da-e4fc-42d6-8147-a47ad76a8982`)  
**Company:** Valedia `4fb233c2-27e9-4d0d-a217-2dc7205210a0` @ Paperclip `:3100`  
**Homes stamped:** `workspace-v2` + `workspace-accel-ornith`  
**Fences held:** no LMS unload/reload; no `--live` / VAL-12 money pack; no Grok Bot keepalive routines; no custom Ornith-alive Python.

**Companion:** this file + dual-home `GROK-STATUS.md` §VALEDIA-STALL-AUDIT.

---

## Executive one-liner

Paperclip wakes Hermes → Ornith is loaded but emits no tracked LLM/tool progress within **240s idleTimeout** → run cancelled (`hermes_gateway` idle) → agent returns idle while board tips stay cosmetic `in_progress` → no tip advancement, no Findings→admit learning loop, profit levers (VAL-6/7/9/10/11/12) stall behind fences + dead wakes. **RuFlo MCP `:8810` is alive but never reached** because CTO heartbeats die before orchestration tools fire.

---

## Immediate fix applied this turn (proof)

| Agent | Before | After (GET) | Notes |
|-------|--------|-------------|-------|
| Scout `277f3c57-…` | `idleTimeoutSec=240` | **900** | PATCHed; keys preserved |
| Decider `16234474-…` | `idleTimeoutSec=240` | **900** | PATCHed; keys preserved |
| Auditor `1f360d86-…` | 900 | **900** | already matched CTO; untouched |
| CTO `53d25446-…` | 900 | **900** | reference; untouched |

GET proof (2026-09-29 ~13:13 PT): all four `idleTimeoutSec=900`, `timeoutSec=3600`, `maxConcurrentRuns=1`.  
Heartbeat evidence of the bug: **7 recent runs** with exact error  
`Hermes gateway run cancelled after 240s without LLM/tool progress.`  
(plus 18 older wall-timeouts at 1800s, 1 at 3600s). Run ledger: 59 runs → timed_out=27, failed=12, succeeded=10, cancelled=9, running=1.

---

## Root causes ranked

### RC-1 — Idle-cancel / heartbeat death loop (PRIMARY, recurring)

**What:** Hang fix (2026-09-28 ~22:56 PT) introduced `idleTimeoutSec` default **240** and patched all four agents to 240. CTO (then Auditor) were later raised to **900**; Scout + Decider stayed at 240 → asymmetric death.

**Mechanism:**
1. Paperclip heartbeat invokes Hermes gateway (`:8642–8645`).
2. Ornith is **loaded** on LMS (`state=loaded`) but often sits with **no active predictions** / no SSE events that refresh `lastProgressAt`.
3. Status polls alone do **not** count as progress (by design in `execute.ts`).
4. After `idleTimeoutSec` silence → POST `/stop` → `timed_out` + error text `…cancelled after 240s without LLM/tool progress.`
5. `finalizeAgentStatus` maps timed_out → **idle** (good — no sticky error), checkout released; board tip often left cosmetic `in_progress`.
6. Next wake repeats. Ornith looks “alive but useless.”

**Evidence:** company heartbeat-runs; CTO 11 timed_out / 4 succeeded; Scout 6 timed_out / 1 succeeded; Decider also `Context length exceeded (174 tokens). Cannot compress further.` on a failed wake (session rot).

**Why it keeps happening:** operators raised CTO only; Scout/Decider remained on hang-fix default; no Paperclip-native policy enforcing company-wide idle floor = CTO.

### RC-2 — Board drift (tips ≠ actual work)

**What:** Cosmetic `in_progress` tips without `executionRunId` / `activeRun`.

| Tip | Status | Assignee | Live agent | Actual work |
|-----|--------|----------|------------|-------------|
| VAL-1 | in_progress | Decider | idle (last HB ~06:56Z) | no checkout |
| VAL-8 | in_progress | Auditor | idle (last HB ~06:09Z) | HOLD fence parked |
| VAL-11 | in_progress | CTO | running (new run 20:09Z) | self-heal; thrashing history |
| VAL-10 | **todo** | Scout | idle | was prior Scout tip; drifted off |
| VAL-6/7/9/12 | todo | CTO/Scout | — | never advanced |
| VAL-5 | in_review | Auditor | — | ACCEL pilot not driving continuous profit |
| VAL-3 | todo | Scout | — | parked behind VAL-10 |

Dashboard: open=11, inProgress=3, blocked=0, done=2; agents running=1 error=0.  
Attention totalCount=0 — desk thinks calm while tips lie.

**Effect:** humans and agents trust the board; heartbeats wake into wrong/empty work; WIP=1 policy on paper, thrash in practice.

### RC-3 — Hermes/Ornith admission ≠ productive wakes

**Healthy layer:** Hermes `:8642–8645` `/health` 200; LMS Ornith-only loaded; RuFlo MCP `:8810` initialize 200 (`Claude-Flow MCP Server V3`).

**Broken layer:** wakes rarely produce `livenessState=advanced` / `lastUsefulActionAt`. Only one recent success (`e1972f9a` CTO) shows “1 workspace operation + 1 activity event.” Most runs end with `liveness=failed` timed_out and `lastUseful=None`.

**Admission gap:** model seat is admitted (loaded); **tool/progress admission** into Paperclip’s idle watchdog is not. Ornith “No active predictions” is the symptom of cancelled/never-started generation, not a separate LMS bug.

### RC-4 — ACCEL / profit path blockers (paper vs live fences + VAL stack)

Hard fences (correct, keep): no `--live` / oil / eToro `start_copy`; INFORMED + fee-true; VAL-12 Hermes+LMS only (no grok-valedia money pack); WEATHER live = Valedia runtime only.

Profit stallers:
- **VAL-9** (unblock INFORMED weather fee-true admit) still **todo** — primary coding lever not shipped.
- **VAL-10** (two-sided fee-true books) **todo** — Scout not completing evidence.
- **VAL-7** (rank INFORMED profit levers) **todo** — Decider has nothing ranked to decide.
- **VAL-6** (continuous Ornith-solo check-in) **todo** — no continuous ACCEL motion.
- **VAL-11** self-heal mapped + probes wired, but CTO can’t finish under idle thrash.
- **VAL-12** deliberately blocked from this lane (money four-pack) — correct; not a bug.
- **VAL-5** in_review without a closed feedback loop into live fee-true creates.

Paper/shadow path produces docs & probes; **fee-true admit → create path stays gated** by unfinished VAL-9/10 + intentional live fences. “Not profiting” = expected until admit lever lands and wakes complete.

### RC-5 — Self-improvement missing (no continuous learning / tip advancement)

- Self-continuation protocol mapped (`VALEDIA-SELF-CONTINUATION.md`) for Hermes Valedia — **not hooked into Paperclip heartbeat success → tip promote / Findings append**.
- No Paperclip-native “on succeeded run: advance tip / open next VAL / write Findings List” automation; routines=`[]` by design, but nothing replaced it.
- Hindsight / MiroFish / shadow ledgers exist as artifacts; they do not feed Decider create/skip without a Findings List + one-line decision request (adapter instructions already say this — tooling never produces it because wakes die).
- Success rate ~17% (10/59) → almost no durable tip advancement.

### RC-6 — RuFlo role (victim, not root; also underused fix path)

**Not the idle-cancel root.** Failure chain is Paperclip Hermes adapter idle watchdog ↔ missing progress SSE.

**In the fix path:** CTO `adapterConfig.builder=ornith` + instructions to orchestrate via RuFlo CLI / MCP `:8810`. MCP is up; `ruflo` CLI **not on PATH** in this shell (marketplace tree at `~/ruflo`, plugins present). Heartbeats die before RuFlo tools are invoked → RuFlo never appears in run outcomes.

**Do not** invent Paperclip↔RuFlo binary protocol. Keep: Paperclip → Hermes CTO → Ornith + RuFlo MCP.

---

## Concrete Paperclip-native fixes (NO Grok Bot keepalives, NO Ornith-alive Python)

1. **Company idle floor (done this turn for Scout/Decider):** keep all Valedia agents `idleTimeoutSec=900` (= CTO). Re-GET after any hang-fix redeploy — hang-fix default 240 will regress Scout/Decider again unless schema default or company template is raised.
2. **Raise adapter default:** in Paperclip hermes adapter `DEFAULT_IDLE_TIMEOUT_SEC` 240→900 (or company template override) so new agents don’t inherit death-by-default.
3. **Progress SSE contract:** Hermes gateway must emit progress events Paperclip counts (tool start/end, LLM chunk) during Ornith TTFT; if Ornith cold-starts >240s even at 900, consider first-token grace or counting “model generating” LMS signal **only via Hermes SSE**, not external Python keepalives.
4. **Board reconcile (Paperclip API only):** for each agent, at most one real tip with checkout; demote cosmetic `in_progress` without `executionRunId` → `todo` (VAL-1/8 candidates); restore Scout tip to VAL-10 or VAL-7 intentionally; do not spam empty on-demand wakes.
5. **Session hygiene:** Decider context-length failure → Paperclip/Hermes `sessionKeyStrategy=issue` rotate / clear stuck session for Decider before next wake.
6. **Tip advancement rule (native):** on heartbeat-run `succeeded` with `livenessState=advanced`, auto-comment Findings + promote or open next VAL per Decider policy — implement as Paperclip routine/hook **inside Paperclip**, not Grok Bot.
7. **ACCEL sequence (once wakes survive):** VAL-9 admit unblock → VAL-10 two-sided books → VAL-7 ranked levers → Decider Findings decision → Auditor HOLD stays on `--live` until Burton unfence. Leave VAL-12 alone.
8. **RuFlo PATH for CTO Hermes profile only:** ensure `ruflo` on Hermes CTO PATH or call MCP `:8810` exclusively; verify one CTO succeeded run shows RuFlo tool activity. Do not add Ornith ping daemons.
9. **Quiet-hours / wake policy:** stop operator mass-wakes; one wake per idle agent with a real tip; wait for progress or 900s cancel.

---

## Out of scope / fences

- LMS unload/reload — not touched  
- `--live` / VAL-12 money pack — not touched  
- Grok Bot keepalive routines — rejected  
- Custom Ornith-alive Python — rejected  

---

## Top 3 findings (for return)

1. **Scout+Decider were still on idleTimeoutSec=240** while CTO/Auditor were 900 → majority of recent deaths are exact `cancelled after 240s without LLM/tool progress`; patched to 900 and GET-proven.
2. **Board tips lie:** VAL-1/8/11 `in_progress` without live checkouts; VAL-10 (Scout’s profit lever) drifted to `todo`; agents idle/running mismatch → no tip advancement.
3. **Profit/self-improve blocked by dead wakes + unfinished VAL-9/10/7**, not by missing Ornith load; RuFlo MCP is up but never reached; paper fences correctly block live $ until admit lever ships via surviving CTO/Scout runs.

---

## Follow-up applied (Grok Build §VALEDIA-STALL-FIXES)

**When:** 2026-09-29 13:23 PT — see dual-home `GROK-STATUS.md` §VALEDIA-STALL-FIXES + `VALEDIA-STALL-FIXES-PROOF-2026-09-29.txt`.
