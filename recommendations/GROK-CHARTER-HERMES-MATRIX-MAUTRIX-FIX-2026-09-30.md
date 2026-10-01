# Grok Build charter — Hermes Matrix / mautrix delivery fix (2026-09-30)

**Authority:** Burton — "Grok always" (Grok Build lane, not Assistant inline). Valedia executor charters + launches only; does **not** apply the fix.

**Date:** 2026-09-30 (~17:20 PT)
**Machine:** M1 (Burtons-Mac-Studio-M1)
**Deliverable stamp:** `~/.openclaw/workspace-v2/recommendations/HERMES-MATRIX-MAUTRIX-FIX-2026-09-30.md` with **GROK_EXIT:0|1** and proof below.
**Also update:** `recommendations/GROK-STATUS.md` briefly (top section for this pack).

**Context / reference (read-only — do not re-litigate):**
- `recommendations/HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30.md` — **GROK_EXIT:0**; model path fixed (mlx). Important-mail `last_status=delivery_failed` because `No module named 'mautrix'` (delivery, not model). morning-brief / chief-of-staff similarly `delivery_failed` on Matrix. Doctor: default + valedia share one Matrix credential.
- `recommendations/HERMES-LIFE-GATEWAY-RELOAD-2026-09-30.md` — life gateway already reloaded today; currently up (wrapper ~30269 / gateway python ~34663 as of Kanban stamp). Prefer **no** bounce.
- `recommendations/GROK-ADDENDUM-HERMES-ALL-ERRORS-2026-09-30.md` — Matrix/mautrix deferred from all-errors pack.
- Preserve TCC fence / standing Valedia fences from prior stamps + CTO+trading SOUL hard rule.

---

## Problem (executor probe — verify, then fix)

1. **Missing module on the live runtime:** Life/root gateway runs Hermes via tools **Python 3.14** (`~/.hermes/tools/python-3.14.7+…`) with `PYTHONPATH` including the installs env:
   `~/.hermes/installs/e9a8c1e0488a29ad/environments/5d8db6e4843647a885bd7efdd16a4f58/venv/lib/python3.14/site-packages`
   That env **cannot** `import mautrix` (`ModuleNotFoundError`).
2. **mautrix exists elsewhere:** `~/.hermes/hermes-agent/venv` has `mautrix==0.21.1` under **python3.11** site-packages — wrong interpreter / not on the life gateway import path.
3. **Extras known to Hermes:** `pm.extras` maps `"matrix": ("mautrix", "asyncpg", "aiosqlite", "markdown", "aiohttp_socks")`. Adapter has `ensure_matrix_deps()` / rebind tests (`tests/gateway/test_matrix_deps_rebind.py`). Prefer the **sanctioned Hermes install/rebind path** over ad-hoc `pip` into a random venv — but the goal is: **the same Python the life gateway uses** can `import mautrix`.
4. **Cron delivery targets (Matrix):**
   - Important-mail monitor → `local,matrix` — last_run 2026-09-30T17:12:16 PT, `delivery_failed`, model mlx OK, error was missing mautrix.
   - morning-brief → `matrix:!L1H9bZOwdqKwBWt_mnD7dlbMG_bruf7rQWW9JVRqgf4`
   - chief-of-staff-daily → same Matrix room
5. **Doctor:** Duplicate Matrix credential across profiles `default` and `valedia` (same `MATRIX_ACCESS_TOKEN`/`MATRIX_PASSWORD`). One token → one gateway; the other adapter parks. Doctor suggests own bot token for valedia, **or** remove token from the non-owner profile / use `gateway.profile_routes`, then optionally `hermes gateway migrate --multiplex`.

---

## Goal

Make Matrix cron **delivery** work again for life/root (Important-mail + siblings) by installing/rebinding **mautrix** (and required matrix extras) into the **runtime the life gateway actually imports**, without colliding with Trader’s Nimble System One → Laya teardown, and without bouncing Matrix/Signal LaunchAgents unless truly unavoidable.

---

## VETO (hard — do not do these)

- Do **NOT** rewrite trading CHANGE-set yaml / tool_search / context / TCC fence on `valedia-trading-{scout,decider,auditor}`.
- Do **NOT** unload/load LMS models; do **NOT** touch `lms` load state while Trader may be using LMS/Ollama screens.
- Do **NOT** stop / kill LiteLLM `:4000`.
- Do **NOT** `--live` / venue orders / wallets / money path / OKX / VAL-12.
- Do **NOT** Paperclip dual-write / Paperclip-as-Kanban-profile / fold Paperclip UI.
- Do **NOT** bounce trading LaunchAgents; do **NOT** kill screens `ot-valedia-desk` / `valedia-glance` / Trader NIMBLE/SystemOne/Laya work.
- Do **NOT** run `hermes -p valedia gateway` (valedia = Desktop serve only; life hosts Matrix/Signal).
- Do **NOT** `hermes gateway --replace` cascades.
- Do **NOT** bounce Matrix/Signal LaunchAgents (`com.burton.hermes-signal`, `com.burton.matrix-tunnel`, signal-cli ~908, matrix-tunnel ~925) **unless literally required** after install for the new module to load — and then only the **softest** documented path (prefer life gateway soft respawn alone; see Bounce policy).
- Do **NOT** invent a second Matrix bot token or rotate credentials without documenting a Burton decision point; prefer fixing the **import path** first. Shared-credential doctor warning may stay **deferred** if fixing it needs migrate/--multiplex + bounce — document and leave for Burton unless a no-bounce config-only cleanup is safe (e.g. remove duplicate token from valedia profile env **only if** that does not break Desktop and does not require LA bounce).

---

## APPLY (preferred order)

### (1) Prove the import gap on the live interpreter

- Identify the exact life gateway python + `sys.path` / `PYTHONPATH` (pid ~34663 pattern: tools python3.14 + installs env site-packages + hermes-agent on path).
- Show `import mautrix` **fails** on that interpreter before fix; succeeds on hermes-agent venv (3.11) for contrast.
- Confirm `pm.extras` matrix extras list.

### (2) Install / rebind mautrix into the **gateway runtime** (no LA bounce if possible)

Prefer, in order:
1. Hermes-sanctioned path: `ensure_matrix_deps()` / `hermes` pm extras install for **matrix** targeting the **active life/runtime env** (the 3.14 installs env the gateway uses) — not only the stale 3.11 `hermes-agent/venv`.
2. If CLI requires it: install `mautrix` (+ `asyncpg`, `aiosqlite`, `markdown`, `aiohttp_socks` as needed) into that same 3.14 env’s site-packages with the env’s own pip/python.
3. Avoid polluting system Homebrew/Framework Pythons.

**Success criterion for this step:** the **same** python binary + path the life gateway process uses can `import mautrix` and report a version/file path.

### (3) Softest activation — prefer no bounce

Life gateway is **currently up**. Prefer install/rebind that is visible to **new** delivery imports without restart.

- If Matrix delivery is imported per-job / re-checks deps each send: trigger a **safe** delivery proof (Important-mail `hermes cron run` or equivalent once idle) and show no `No module named 'mautrix'`.
- If the running process cached a failed import and still cannot see the new module **without** reload: soft-respawn **life/root only** (`ai.hermes.gateway`) — SIGTERM child / LaunchAgent kickstart of **life label only** — same soft path as `HERMES-LIFE-GATEWAY-RELOAD`. Document why bounce was required.
- **Still prefer NOT** bouncing Signal/Matrix LaunchAgents. If somehow unavoidable, stop and document blockers before doing it; softest path = life gateway only.

### (4) Shared Matrix credential (doctor) — secondary

- Do **not** block GROK_EXIT:0 on credential split if mautrix import + Important-mail delivery module error is fixed.
- Document doctor Matrix status after fix.
- If a **no-bounce** cleanup is clearly safe (e.g. valedia should not own Matrix; remove duplicate from valedia profile only; life/default keeps it), do it and note. If it needs `hermes gateway migrate --multiplex` or LA bounce → **defer + needs-Burton** with exact next step.

### (5) Proof runs

1. `import mautrix` on gateway runtime — OK.
2. Important-mail delivery path no longer fails on missing module:
   - Prefer a fresh cron fire / delivery attempt after fix.
   - Stamp must show `last_error` does **not** contain `No module named 'mautrix'`; ideally `last_status` is `ok` or a **non-module** delivery outcome.
   - If Matrix homeserver/auth/room errors remain, distinguish them from the module fix.
3. Note morning-brief / chief-of-staff (same Matrix deliver) disposition — fixed, scheduled-next, or deferred.
4. `hermes doctor` Matrix-related lines (shared credential + any matrix/mautrix notes).

---

## Bounce policy (minimal)

| Action | Default |
|---|---|
| Install into 3.14 env site-packages | **yes** (preferred) |
| Life `ai.hermes.gateway` soft respawn | **only if** import still fails in-process after install; document |
| Signal / matrix-tunnel LaunchAgents | **no** unless impossible otherwise — document + softest path; prefer abort-to-Burton |
| Trading / CTO LAs | **never** |
| LMS unload / LiteLLM kill | **never** |
| `hermes gateway --replace` | **never** |

Trader is mid Nimble System One → Laya teardown — **do not collide** with trading screens / LMS / those screens.

---

## Deliverables

1. Stamp `~/.openclaw/workspace-v2/recommendations/HERMES-MATRIX-MAUTRIX-FIX-2026-09-30.md` with:
   - Root-cause (which python/env lacked mautrix; where it was already installed)
   - Before/after `import mautrix` on the **gateway** runtime
   - Install/rebind method used
   - Bounce list (or explicit **none**) + why if life bounced; Signal/Matrix LA status
   - Important-mail (and sibling) delivery proof — no missing-module failure
   - `hermes doctor` Matrix status (shared credential fixed | deferred+why)
   - Fences held (trading/TCC/LMS/LiteLLM/Paperclip/--live)
   - Backups if any config/env files edited
   - **GROK_EXIT:0** or **GROK_EXIT:1** + blockers
2. Brief update at top of `recommendations/GROK-STATUS.md`
3. End with **GROK_EXIT** and the stamp path

Execute fully. End GROK_EXIT:N.
