# GROK CHARTER — Hermes hub + Paperclip status + Desktop timeout (2026-09-30 ~19:50 PT)

**Mode:** EXECUTE. Paper/ops. Stamp `HERMES-HUB-PAPERCLIP-OPTIMAL-2026-09-30.md` with `GROK_EXIT` + proof.
**Why:** Burton must run Valedia without Grok Bot. Hermes Desktop Kanban shows `Error invoking remote method 'hermes:api': Timed out connecting to Hermes backend after 30000ms`. He wants one central regularly updated hub so Hermes/Paperclip/Claude/Grok all know where work left off. Hermes still feels unoptimized.

## Facts (do not invent)
- Paperclip Valedia company `4fb233c2-27e9-4d0d-a217-2dc7205210a0` on `:3100` = source of truth for VAL-*.
- Hermes Kanban Default has 5 BLOCKED glance cards (desk health, Paperclip read-mirror, trading paper lane, Important-mail mlx, assignee KEEP) — read-only by design; do not dual-write Paperclip into Kanban profiles.
- Desk default model: `ornith-1.5-35b-a3b-mlx` on LMS `:1234`. MoA OFF. Laya retired; Nimble System One opt-in `VALEDIA_SYSTEMONE=1`.
- Talk path for Burton: Hermes **Bot → Valedia** (ops) / **Valedia CTO** (coding). Sessions are not the hub.
- Prior stamps: `HERMES-KANBAN-PAPERCLIP-OPTIMAL-2026-09-30.md`, `HERMES-MATRIX-MAUTRIX-FIX-2026-09-30.md`, `NIMBLE-SYSTEMONE-REPLACE-LAYA-2026-09-30.md`.

## Execute
1. **Fix Desktop `hermes:api` 30s timeout** — diagnose life gateway vs Desktop IPC; softest repair (config/timeout/reconnect). Prefer no LaunchAgent bounce; if bounce needed, only life/root; document proof before/after. Prove Kanban/API call succeeds.
2. **Central hub topsheet** — write/refresh a single always-current file `recommendations/VALEDIA-HUB.md` that Hermes + humans read: live Paperclip VAL tip statuses, agent idle/running, last heartbeat, Ornith/LMS pin, Nimble/Laya state, open blockers, “talk here” (Valedia bot / CTO / Paperclip URL). Update from live API reads. Point Hermes valedia + CTO profile instructions (or a small skill/hook) to read this hub on session start — no dual-write to Paperclip.
3. **Hermes↔Paperclip status awareness** — ensure Valedia/CTO/Scout/Decider can `GET` Paperclip company issues/agents/heartbeat-runs (read-only) and surface status in hub / Findings. Keep earlier APPROVE: read-mostly mirror + deep links; VETO fold Paperclip into Kanban profile.
4. **Hermes optimize pass (safe lane)** — inventory remaining Desktop/gateway flakes (timeouts, empty Sessions, assignee list, tool clutter). Apply only reversible hygiene that improves “know where we left off” + reliability. Document trading/CTO leftover catalog keys — do not rewrite trading CHANGE-set / TCC / LMS unload / LA bounce / `--live`.
5. **Unblock continuous coding** — if Paperclip agents idle with open VAL-9/6/7/10 tips, native `POST /api/agents/{id}/heartbeat/invoke` once (no duct-tape Python daemons). Prefer fee-true weather admit path.

## Fences
- No `--live`, oil unfence, eToro `start_copy`, VAL-12 money four-pack via Grok, LMS unload, trading LaunchAgent bounce, MoA ON, Paperclip dual-write, inventing watcher daemons.
- Coding features → Grok; runtime ability → Ornith/Hermes.

## Stamp must include
- Timeout root cause + fix + proof (Kanban/API ok)
- Path to `VALEDIA-HUB.md` + what updates it
- How Hermes knows Paperclip status (read path)
- Optimize changes list + explicit non-touches
- `GROK_EXIT:0|1`
