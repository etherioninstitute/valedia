# LOUD P0 — Cron bare-resolver (Trader deeper findings) — SAME STAMP

**Injected:** 2026-09-30 16:48:37 PDT
**Authority:** Valedia Trader P0 deeper findings → Grok Build MUST act.
**Same stamp:** `recommendations/HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30.md`
**Also see:** `GROK-URGENT-ADDENDUM-CRON-MODEL-SCRUB-2026-09-30.md`, `GROK-ADDENDUM-HERMES-ALL-ERRORS-2026-09-30.md`
**Do NOT exit without Cron bare-resolver subsection + fresh-run proof.**

---

## !!!!! MUST-DO (read before any more Kanban seed work) !!!!!

Rewriting `~/.hermes/cron/jobs.json` alone is **NOT enough**. Jobs already pin mlx; runtime still requests **bare** `ornith-1.5-35b-a3b` (or last_error is stale — must prove with **fresh run after fix**).

### Trader findings Grok must act on

1. **jobs.json ALREADY mlx** — Important-mail / valedia-inbox-triage / weekly-memory-review / commerce-self-evolve already `model: ornith-1.5-35b-a3b-mlx` + `provider: custom:lms-local`. Rewriting jobs.json alone is NOT enough.
2. **last_error still HTTP 400 bare** `ornith-1.5-35b-a3b` — runtime still requests bare **OR** errors are stale. **MUST prove with fresh run after fix** (Important-mail + valedia-inbox at minimum). Stamp proof (timestamp + log line / cron list showing mlx + non-400).
3. **Root already mlx** — `model.default`, `providers.custom:lms-local.model`, `cron.model` already `ornith-1.5-35b-a3b-mlx`. Do not stop at grepping those.
4. **Bare sibling still in provider catalog** — `providers.custom:lms-local.models` still lists bare key `ornith-1.5-35b-a3b` (with `context_length: 131072`) **beside** `ornith-1.5-35b-a3b-mlx: {}` (+ stale obliterated kits e.g. `qwen3.8-27b-obliterated-k`). **Remove/hide bare** so resolver cannot land on it. Set mlx entry `context_length` explicitly ≥65536 if needed.
5. **Find where request still sends bare** — audit: alias strip (`-mlx` → bare), `model_snapshot`, gateway cache / in-memory scheduler, `providers.models` bare sibling preference, auth / Desktop schedule mirror, payload embed. Prove actual id wired to LMS `:1234`.
6. **Eliminate bare Ornith as selectable LMS id on life/root** — bare must not appear as a live selectable / resolvable id.
7. **Clear/retry Important-mail + valedia-inbox after fix; stamp proof.**
8. **Fences:** MoA OFF; no dual-write; no trading CHANGE/TCC/LMS unload; no Matrix/Signal bounce. **No trading `profiles/*/cron/jobs.json` found** — trading LAs stay fenced. Prefer zero trading yaml edits / zero trading bounces.

### Evidence at inject (2026-09-30 16:48:37 PDT) — re-verify live

| Job | jobs.json model | last_status | last_error (abbrev) |
|---|---|---|---|
| Important-mail monitor | ornith-1.5-35b-a3b-mlx @ custom:lms-local | error | HTTP 400 Invalid model identifier "ornith-1.5-35b-a3b" |
| valedia-inbox-triage | ornith-1.5-35b-a3b-mlx @ custom:lms-local | error | HTTP 400 bare ornith |
| weekly-memory-review | ornith-1.5-35b-a3b-mlx @ custom:lms-local | error | ValueError bare ornith ctx 32768 < 64000 |
| commerce-self-evolve | ornith-1.5-35b-a3b-mlx @ custom:lms-local | error | ValueError bare ornith ctx 32768 < 64000 |

- Root `model.default` / `cron.model` / `providers.custom:lms-local.model` = mlx
- `providers.custom:lms-local.models` keys include: `ornith-1.5-35b-a3b` (bare, ctx 131072), `ornith-1.5-35b-a3b-mlx` ({}), `qwen3.8-27b-obliterated-k`, `ornith-1.5-mmproj`, `ornith-1.5-9b-mlx`
- MoA `enabled: false`
- `find ~/.hermes/profiles -name jobs.json` → **none** (trading LAs fenced)

### Stamp subsection REQUIRED before GROK_EXIT

In `HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30.md` include:

### Cron bare-resolver
- Root-cause: which path still sent bare (alias strip / model_snapshot / gateway cache / providers.models bare sibling / auth / other)
- providers.models before→after (bare removed/hidden; obliterated kits hidden; mlx ctx set)
- Fresh-run proof: Important-mail + valedia-inbox (time PT, status, model id on wire, last_error cleared or success)
- Confirmation: MoA OFF; no trading CHANGE/TCC/LMS unload; no Matrix/Signal bounce; no trading jobs.json touched
- Bounce list (expect none for trading/Matrix/Signal/LMS)

Elevate this **above** board-seed niceties if timeboxed. Do not claim cron fixed from jobs.json mlx pins alone.
