# VALEDIA-SELF-CONTINUATION — Flux self-continuation protocol (P0-3)

**Date:** 2026-09-28 ~23:36 PT  
**Brief:** `~/MemPalace/sources/shared-brain/VALEDIA-FLUX-IMPLEMENTATION-BRIEF.md` §D P0-3  
**Pack source:** ProtonDrive `AI/Flux-Growth-Collection/self-continuation-protocol.md` (Flux FREE)  
**Status:** mapping + drop-in landed (paper/ops only)

---

## Rubric (deterministic continue vs ask)

A pending decision belongs to **Burton** (stop + ask) **only** if it is one of these four gates.  
**Everything else → self-answer**, pick best option + **one-line why-safe**, and **continue**. Do not ping for reversible internal choices.

| Gate | Meaning | Valedia / Hermes / Paperclip mapping (MUST gate; Grok Build respects stops, does not self-continue) |
|------|---------|---------------------------------------------------------------|
| **Irreversible** | Destroys/overwrites data or not cheaply undoable | LMS **unload** / swap / kill loaded model; `docker compose down -v` / Neo4j volume wipe; force-push; `rm -rf` of workspace/data; permanent purge of Paperclip issues or memory drawers; **Paperclip/automation kill of exclusive Build screens** (`flux-grok`, `valedia-grok`) mid-task |
| **Outward-facing** | Sends to real people, publishes, spends, deploys, or live-risk | **`--live`** / venue **orders** (create/amend/cancel); **send / email** / Signal money pings beyond allowed formats; Slack/X/post/DM; **wallet** spend/transfer/deposit/sweep; deploy/go-live; Drive share outside notes |
| **Preference** | Pure taste — no derivable best | Brand voice, accent color, which witty phrasing, non-derivable naming when both options equal |
| **Resource-blocked** | Needs credential/access agent lacks | API keys, passwords, 2FA/OTP, missing Tailscale/login, Burton-owned pin not set (e.g. Ornith-only unload pin) |

**Default = motion.** Halting requires a named gate. Aligns with existing irreversible-actions / approval-gate / money four-pack fences.

---

## House examples (continue vs stop)

### CONTINUE (self-answer + one-line why-safe)
- Paper/docs edits under `workspace-v2/recommendations/` — reversible + internal
- Choosing among paper CE approaches / probe order / which silent-failure signal to wire next
- Writing dual-home stamps, ponytails, Paperclip **comments** (not destructive deletes)
- Safe remediates already chartered (e.g. `docker compose up -d neo4j` **no** `-v`)
- Reading probes, grepping feeds, drafting emails **without sending**

### STOP + ASK (user gate)
- Any `--live` flag or live order path
- Send/email/post to real recipients
- Wallet / spend / transfer / Mercury move
- LMS unload / model swap / kill loaded Bionic seat
- Killing exclusive `flux-grok` / `valedia-grok` (or other exclusive Build) without same-turn Burton yes
- Volume wipe / force-push / permanent data destruction

---

## Drop-in surfaces

| Piece | Path | Role |
|-------|------|------|
| Classifier + decide + onStop | `workspace-v2/scripts/self-continuation.js` | Pack engine, Valedia-tuned patterns |
| CLI | `workspace-v2/scripts/self-continuation.sh` | `classify` / `decide` / `on-stop` → JSON |
| Hermes skill (global) | `~/.hermes/skills/self-continuation/SKILL.md` | When to gate vs continue |
| Hermes Valedia skill | `~/.hermes/profiles/valedia/skills/self-continuation/SKILL.md` | Same text for Valedia profile |
| Workspace skill | `workspace-v2/skills/self-continuation/SKILL.md` | Valedia/RuFlo gate-check from Build jobs — **not** Grok self-continue |
| Profile instructions | `~/.hermes/profiles/valedia/SOUL.md` §Self-continuation | Always-on rubric for Hermes Valedia |
| This map | dual-home `recommendations/VALEDIA-SELF-CONTINUATION.md` | Operator reference |

### CLI usage (paper/ops)

```bash
~/.openclaw/workspace-v2/scripts/self-continuation.sh classify "Should I unload Ornith to free VRAM?"
~/.openclaw/workspace-v2/scripts/self-continuation.sh decide "Which silent-failure signal to wire next?" "10" "11" "14"
~/.openclaw/workspace-v2/scripts/self-continuation.sh on-stop true   # pending work → block stop
~/.openclaw/workspace-v2/scripts/self-continuation.sh on-stop false  # allow clean stop
```

Accountability: optional append of gate decisions to `workspace-v2/data/self-continuation-log.jsonl` via `SELF_CONTINUATION_LOG=1`.

---

## Wiring into loops

1. **Continuation lever (`onStop`)** — when about to end a turn with pending work, block stop and re-issue with rubric injected.
2. **Gate (`decide` / `classifyDecision`)** — before surfacing a question to Burton, classify; if `continue`, pick + one-line why-safe + proceed; if `user`, surface and halt.
3. **Do not** use this to bypass fences. Classifier is a **convenience**; human fences still win on conflict.

---

## Audience (Burton 2026-09-28 23:42 PT)

**Self-continuation (`onStop` / continue-by-default) is for Valedia only** — Hermes Valedia desk brain + Paperclip Valedia agents.

**Grok Build is NOT a self-continuation actor.** It does not loop itself via `onStop`. Grok is an executor: run the assigned slice, stamp, exit. It may *consult* the four gates to know when it must **stop and ask Burton** (live/orders, send, wallet, LMS unload, kill exclusive screens) — it must not use this protocol to keep itself running or auto-pick past preference gates without Valedia owning the loop.

Workspace skill path exists so Valedia/RuFlo can call `classify`/`decide` from Build jobs; that is **gate checking**, not Grok self-continuing.

## Success (P0-3)

- [x] Four gates mapped to Valedia/Hermes/Grok Build/Paperclip actions (incl. --live/orders, send/email, wallet, LMS unload, exclusive Build kill)
- [x] Drop-in `classifyDecision` / `decide` / `onStop` under workspace-v2 + Hermes Valedia skill/SOUL
- [ ] Ops: fewer needless “should I…?” stops on paper/docs (observe next sessions)
- [ ] Ops: zero continues through send/order/unload without gate hit (audit log if enabled)

---

## Dual-home paths

- Map: `~/.openclaw/workspace-v2/recommendations/VALEDIA-SELF-CONTINUATION.md`
- Mirror: `~/.openclaw/workspace/recommendations/VALEDIA-SELF-CONTINUATION.md`
- Status stamp: dual-home `GROK-STATUS.md` §SELF-CONTINUATION

## Fence

No `--live`, no venue orders, no wallet, no LMS unload, no auto-kill of exclusive Build screens. Paper/ops docs + helper only.
