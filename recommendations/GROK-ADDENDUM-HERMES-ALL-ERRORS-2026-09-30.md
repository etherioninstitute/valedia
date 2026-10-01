# P0 ADDENDUM — HERMES ALL ERRORS (Burton directive 2026-09-30)

**Issued:** 2026-09-30 16:47:45 PT
**Priority:** P0 over / alongside Kanban-Paperclip charter. Grok MUST see ALL errors and fix them — real optimization, not partial.
**Authority:** Burton. Valedia executor only charters/launches; Grok Build executes.
**Complete in:** same stamp `HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30.md` **OR** follow-on stamp `HERMES-ALL-ERRORS-FIX-2026-09-30.md` if separate. Either way end with **GROK_EXIT**.

**Parent charter (still required):** `GROK-CHARTER-HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30.md` APPLY (1)–(4) unchanged. This addendum **extends** it; does not replace fences.

---

## Fences (unchanged + reinforce)

- No `--live` / money / venue orders / OKX / VAL-12
- No LMS unload/load without Burton
- No Matrix/Signal LaunchAgent bounce unless required **and documented** in stamp
- No dual-write Paperclip; no Paperclip-as-Kanban-profile
- No rewrite trading CHANGE-set / TCC / LMS unload
- MoA stays **OFF**
- Prefer soft Desktop refresh; life bounce only if required for pin/allowlist and document why

---

## REQUIRED — Inventory ALL current Hermes errors (dump evidence into stamp)

Collect before claiming fixed. Sources:

1. **Desktop / Hermes scheduled jobs last-run failures** — `hermes cron list` + `~/.hermes/cron/jobs.json`
2. **Gateway logs** — `~/.hermes/logs/errors.log`, `gateway.error.log`, `gateway.log`, `agent.log`, `desktop.log`, `gui.log`
3. **BOTS interrupted sessions** — lifecycle=`interrupted` / `cron_incomplete_no_output` lines
4. **Unknown provider** / ghost providers in Desktop picker + config
5. **HTTP 400 model ids** — bare `ornith-1.5-35b-a3b` vs live LMS `ornith-1.5-35b-a3b-mlx`
6. **Kanban** — empty Default board / ghost assignees (parent charter)
7. **`hermes doctor`** output (paste relevant sections)
8. **LaunchAgent crashes / non-zero** — `launchctl list | grep hermes`, DiagnosticReports

### Evidence already observed at issue time (2026-09-30 16:47:45 PT) — Grok must re-verify live

| Error | Evidence | In-scope? |
|---|---|---|
| Important-mail monitor HTTP 400 bare `ornith-1.5-35b-a3b` | `hermes cron list` last run 15:00 PT; catch-up 16:45 PT still **failed**; errors.log 12:30–16:02 bare id to `:1234` | YES — life/root cron |
| valedia-inbox-triage HTTP 400 bare ornith | errors.log 14:37, 15:00, 16:02; jobs.json `model` field already says `…-mlx` but **runtime still sent bare** | YES — find why pin not applied at dispatch |
| weekly-memory-review ValueError ctx 32k bare ornith | last run 09-27; jobs.json now mlx — verify next path / clear stale failure | YES |
| commerce-self-evolve ValueError ctx 32k bare ornith | jobs.json last_error stale bare — verify pin + re-run if safe | YES |
| Session lifecycle=interrupted / cron_incomplete_no_output | paired with Important-mail + inbox-triage failures | YES — should clear after pin fix |
| Matrix delivery failed (mautrix / connect) on Important-mail, morning-brief, chief-of-staff-daily | delivery error; charter fence: **do not bounce Matrix/Signal** unless required+documented | DEFER unless blocking pin verify; document for Burton |
| ai.hermes.gateway launchctl status `-15` | list showed `30269 -15 ai.hermes.gateway` — diagnose; do not cascade Signal/Matrix | YES diagnose; bounce life only if required |
| Trading LaunchAgents exit status `143` in launchctl list | CTO/scout/decider/auditor — diagnose; **FIX trading bare model id only if safe**; no CHANGE-set/TCC/LMS unload | trading: safe pin only or document for Trader |
| Ghost / Unknown providers; Flash-Next `:8081`; Ollama clutter | prior audit backlog | YES catalog hygiene (parent §4) |
| Fallback empty / same as main | parent §4 → set `qwen/qwen3.8-27b` | YES |
| Kanban empty Default + orchestrator ghost assignees | parent §1–3 | YES |
| MoA | must stay OFF | verify only |

**Critical discrepancy for Grok to resolve:** `jobs.json` top-level `model` already `ornith-1.5-35b-a3b-mlx` on all 9/10 enabled pins, yet cron dispatch through ~16:02 PT (and reportedly 16:45 catch-up) still called LMS with bare `ornith-1.5-35b-a3b`. Find the **other** path (payload embed, default model override, Desktop schedule mirror, stale in-memory scheduler, provider rewrite) and fix it. Re-run Important-mail + sibling failing jobs after pin is actually live.

---

## REQUIRED — Fix every in-scope life/root/Desktop issue

- bare ornith → **mlx** on **ALL** cron jobs + any config still sending bare id (root config, Desktop, delegation, approval, aggregator already should be mlx — re-grep)
- ghost providers / Unknown provider cleanup
- orchestrator assignee allowlist (KEEP only — parent §3)
- Kanban useful seed + read-only Paperclip mirror (parent §1–2)
- Fallback = `qwen/qwen3.8-27b` (live LMS catalog)
- catalog hygiene (`only_configured` / current LMS only)
- MoA stays OFF

## Trading profile errors

- FIX **only if safe**: bare model id on trading cron / dispatch without rewriting Trader CHANGE-set / TCC / LMS unload
- If blocked: document for Trader with evidence — **do not skip silently**

## Verify

- Re-run / verify **Important-mail monitor** and sibling failing jobs after pin fix
- Prove with fresh `hermes cron list` / log lines showing **mlx** id and non-400
- `lms ps` unchanged (no unload)
- Stamp table: each error → **fixed** | **deferred+why** | **needs-Burton**

## Stamp requirements

In `HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30.md` (preferred) or `HERMES-ALL-ERRORS-FIX-2026-09-30.md`:

1. Full error inventory with evidence dumps
2. Parent APPLY (1)–(4) proofs
3. Per-error disposition table
4. Backups, bounce list (or none), verify greps
5. **GROK_EXIT:0|1**

If this session already read the charter without this addendum: **re-read this file now** and fold work into the same run. Do not exit partial.

---

## Live update during steer (2026-09-30 ~16:48 PT)

- Life gateway reload earlier today loaded mlx (`Model context warmed: ornith-1.5-35b-a3b-mlx -> 65536`).
- `jobs.json` already has `model: ornith-1.5-35b-a3b-mlx` + `provider: custom:lms-local` on all agent cron jobs.
- **Proof pin is live on wire:** agent.log `2026-09-30 16:45:14` Important-mail `API call #3: model=ornith-1.5-35b-a3b-mlx provider=custom` (latency ~473s) — NOT bare id.
- `last_status`/`last_error` on Important-mail + valedia-inbox-triage still show stale bare-400 from 15:00 / 16:02 — **must re-verify completion of 16:45–16:47 catch-up fire_claims** and refresh disposition (fixed vs still-failing for other reasons: Matrix `mautrix` missing, bash snapshot exit 124).
- Post-reload errors still open: `Platform 'matrix' adapter creation failed`; plugin load timeouts (langfuse/feishu); context-length probe timeouts for mlx; init_session snapshot bootstrap 124.
- `launchctl`: `ai.hermes.gateway` showed `-15`; trading/CTO showed `143` — diagnose in stamp.
- Grok session `valedia-grok` screen 64716 was already running Kanban charter at steer time; this addendum + charter P0 appended mid-flight. If that session exits without ALL-ERRORS coverage → relaunch with addendum+charter.


---

# !!!!! LOUD MUST-DO — Cron bare-resolver (Trader deeper) — 2026-09-30 16:48:37 PDT !!!!!

**Full note:** `recommendations/GROK-ADDENDUM-CRON-BARE-RESOLVER-2026-09-30.md`

Supercedes "just retarget jobs.json" for the HTTP 400 bare-id class:

- jobs.json Important-mail / valedia-inbox-triage / weekly-memory-review / commerce-self-evolve **ALREADY** mlx + custom:lms-local — rewriting jobs.json alone is **NOT** enough.
- last_error still HTTP 400 bare `ornith-1.5-35b-a3b` → runtime still bare **OR** errors stale → **MUST prove with fresh run after fix**.
- Root model.default / providers.custom:lms-local.model / cron.model already mlx.
- `providers.custom:lms-local.models` still lists bare sibling `ornith-1.5-35b-a3b` beside `-mlx` (+ stale obliterated kits) — **remove/hide bare** so resolver cannot land on it.
- Find wire path: alias strip, model_snapshot, gateway cache, providers.models bare sibling, auth.
- Eliminate bare Ornith as selectable LMS id on life/root.
- Clear/retry Important-mail + valedia-inbox; stamp proof.
- MoA OFF; no dual-write; no trading CHANGE/TCC/LMS unload; no Matrix/Signal bounce. No trading profiles/*/cron/jobs.json — trading LAs fenced.

Stamp subsection **Cron bare-resolver** required before GROK_EXIT for this error class.

---

## Trader APPROVE — all-Hermes error disposition contract

The completed stamp `recommendations/HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30.md` is the authoritative inventory and fix receipt. Every inventoried error is mapped there to exactly one of: **fixed**; **deferred-to-Trader/Burton with the reason and fence**; or **won't-fix with why**. No identified error was classified won't-fix; non-remediated rows are explicitly deferred with reasons.

Trader lane disposition: residual trading catalog mentions are **deferred-to-Trader** because the active trading model is already `ornith-1.5-35b-a3b-mlx` and changing disabled MoA/legacy catalog text would rewrite the Trader CHANGE-set or TCC-sensitive surface. No trading yaml, TCC, context, or LaunchAgent was changed. All other in-scope life/root/Desktop errors are fixed or individually dispositioned in the stamp.

Fences held: no trading CHANGE-set rewrite, TCC weakening, LMS load/unload, LiteLLM kill, Matrix/Signal bounce, `--live`/money/venue activity, or Paperclip dual-write/profile fold.
