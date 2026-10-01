# Grok Build charter — Hermes life/root gateway reload (2026-09-30)

**Authority:** Burton authorized life/root Hermes gateway reload so on-disk yaml from HERMES-CLEANUP-YES-PACK + HERMES-ASSISTANT-OPTIMIZE is live. Grok Build owns the reload end-to-end.

**Context stamps (read first):**
- `recommendations/HERMES-CLEANUP-YES-PACK-2026-09-30.md` — root MoA off, mlx pins, picker hides; **bounce: none**; life gateway pid ~74698 (14:57 PT) has **not** re-read ~16:06 yaml
- `recommendations/HERMES-ASSISTANT-OPTIMIZE-2026-09-30.md` — tool_search false on root/valedia/CTO; life gateway not bounced
- `recommendations/GROK-STATUS.md` — current topsheet

## Goal
Reload/restart **ONLY** the life/root Hermes gateway (`HERMES_HOME=~/.hermes`, LaunchAgent `ai.hermes.gateway`) so it rereads updated root config already on disk:
- MoA off
- mlx model pins (aggregator / provider / approval / delegation / cron + job pins)
- `tools.tool_search.enabled: false`
- picker hides / profile hidden flags (Desktop roster)
- other CLEANUP-YES-PACK + ASSISTANT-OPTIMIZE root keys

**No yaml rewrite in this charter** unless a verify proves disk is not what the stamps claim — then stop and document; do not invent new edits.

## VETO (hard — do not do these)
- Do **NOT** run `hermes -p valedia gateway stop` (or start) — **valedia is Desktop serve only**; never run `hermes -p valedia gateway` alongside life Matrix/Signal
- Do **NOT** `hermes gateway stop` / `hermes gateway --replace` cascades that take down more than the life root process
- Do **NOT** bounce trading LaunchAgents `ai.hermes.gateway-valedia-trading-{scout,decider,auditor}` (ports **8642–8644**)
- Do **NOT** bounce `ai.hermes.gateway-valedia-cto` unless required for proof of life-only reload (prefer leave CTO alone)
- Do **NOT** bounce Matrix/Signal LaunchAgents (`com.burton.hermes-signal`, `com.burton.matrix-tunnel`) — **Trader VETO**; leave pids 908 / 925 alone unless literally impossible to reload life without them (document why; still prefer not)
- Do **NOT** LMS load/unload / Ornith swap
- Do **NOT** kill LiteLLM / Paperclip
- Do **NOT** rewrite trading yaml (or any profile yaml beyond what prior stamps already applied)
- Do **NOT** `--live` / venue orders / wallets / OKX / VAL-12
- Do **NOT** strip or weaken TCC App Data fence (`block-tcc-appdata`, approvals.deny, terminal.cwd→workspace-v2, SOUL hard rule)
- Do **NOT** touch screens `ot-valedia-desk` / `valedia-glance`

## Preferred reload method (soft → harder)

1. **Identify** the live life gateway: LaunchAgent `gui/$(id -u)/ai.hermes.gateway`, `HERMES_HOME=~/.hermes`, child python under the osascript wrapper (recently pid 74698 → child ~74707). Confirm it serves Desktop/life (root), not a profile LaunchAgent.
2. **Prefer soft reload:** SIGTERM the life gateway **python child** (or the supervised gateway process) and let the LaunchAgent/`--external-supervisor` bring it back — **or** `launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway` **only** if SIGTERM+respawn is unclear. Prefer SIGTERM/screen-quit style over `hermes gateway stop` cascades.
3. **Do not** kickstart trading / CTO / Signal / Matrix labels as part of this job.
4. Expect Signal session on the life gateway may drop briefly when the life process restarts; that is acceptable for this authorized reload. Still **do not** bounce the separate Signal/Matrix LaunchAgents.
5. Wait until `ai.hermes.gateway` is `state=running`, new pid ≠ old, and gateway log shows a fresh start / config load after the yaml mtime from CLEANUP (~16:06 PT).

## Proof (required in stamp)
1. Before/after pids for `ai.hermes.gateway` (and child python).
2. `launchctl print gui/$(id -u)/ai.hermes.gateway` → running; start time after reload.
3. Grep/proof that **live** process has re-read disk, e.g.:
   - gateway log lines after restart referencing config load / Active profile / MoA / tool_search as applicable
   - and/or a safe read of root `~/.hermes/config.yaml` keys matching stamps (`moa.enabled: false`, mlx aggregator/pins, `tool_search.enabled: false`) **plus** evidence the new process started after those file mtimes
4. Confirm **untouched**: trading 8642–8644 listening same pids (or document if kickstart elsewhere was forced — should be none); CTO 8645; Signal LA 908; matrix-tunnel 925; LiteLLM `:4000`; Paperclip `:3100`.
5. `lms ps` unchanged (no load/unload).
6. TCC fence strings still present on CTO + trading yaml.

## Deliverables
1. Stamp `recommendations/HERMES-LIFE-GATEWAY-RELOAD-2026-09-30.md` with before/after, method used, proof gateway up + config live, fence confirmation, and end **GROK_EXIT:0** or **GROK_EXIT:1** + blockers.
2. Update top of `recommendations/GROK-STATUS.md` briefly (life reload done / not; new life pid; Signal/Matrix LA untouched).
3. End **GROK_EXIT:N**.

## Memory / standing rules
- **valedia** profile = Desktop serve only — never `hermes -p valedia gateway` while life hosts Matrix/Signal.
- Prefer SIGTERM / LaunchAgent kickstart of **life only** over hermes CLI stop cascades.
- Preserve TCC fence. No money path. No LMS churn.

Execute fully. End GROK_EXIT:N.
