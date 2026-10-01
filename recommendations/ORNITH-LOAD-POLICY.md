# ORNITH-LOAD-POLICY — fluid ctx (not hard 131k forever)

**Updated:** 2026-09-29 ~00:57 PT  
**Homes:** `workspace-v2` + `workspace-accel-ornith` (dual-home)  
**Scripts:**
- `scripts/integrations/ornith_load_policy.py` — canonical fluid policy
- `scripts/integrations/ornith_ensure_gate.py` — Option A thin admission gate (read-only)

## Why

Hermes needs **floor ≥ 65536**. Desk/Paperclip often wants **131072**. Heavy work may want up to **min(262144, model max_ctx)**.  
A single hard-coded 131k forever is wrong: under-load wastes VRAM for light jobs; over-constraint breaks Hermes when someone loads 32k.

## Option A owners (CoS)

| Piece | Owner | Notes |
|-------|-------|-------|
| Fluid policy (`ornith_load_policy.py`) | Ops / Grok executor | status + recommend; print `lms load`; apply only via `ORNITH_LOAD_APPLY=1` |
| Ensure-gate (`ornith_ensure_gate.py`) | **Paperclip admission** | read-only preflight before runs; exit 0/2; never unload/reload |
| Probe `#ornith_ctx` | `valedia_silent_failure_probes.py` | WARN if loaded && ctx < floor; PASS if ≥ floor or not loaded |
| Actual `lms load` | Human / ops (or policy `--apply` + env) | Gate prints suggested cmd only |

**Out of scope (Option A):** VAL-12, auto-unload, Paperclip DB/agent edits, `--live`, oil, eToro.

## Constants

| Name | Value | Role |
|------|------:|------|
| `FLOOR_CTX` | 65536 | Hermes minimum — below this while loaded ⇒ bump |
| `DEFAULT_CTX` | 131072 | desk / paperclip **target** (not a global forever lock) |
| `PARALLEL_DEFAULT` | 2 | preferred parallel; info-only if ≠2 (does not FAIL probes alone) |

## `choose_ctx(purpose)` — never one global number

| purpose | ctx |
|---------|----:|
| `light` | 65536 |
| `desk` / `paperclip` | 131072 |
| `heavy` | `min(262144, max_ctx)` |

## `status()` fields

`loaded?`, `loaded_ctx`, `parallel`, `ttl`, `ok_vs_floor`, `recommendation` ∈ `{ok, bump_to_floor, align_hermes, leave_alone}`.

Live sources: LMS `GET http://127.0.0.1:1234/api/v0/models` and/or `lms ps`.

## Fence — no auto-unload (AGENT-INVENTORY)

- Default path is **read + recommend + print**.
- `--apply` prints the exact `lms load …` command.
- Execute only when `ORNITH_LOAD_APPLY=1` **and** `--purpose` is set.
- Still prefer print-only for safety. **Never unload** another model from this policy.
- Ensure-gate **never** executes load/unload; it only returns JSON + exit code.
- Probe `#ornith_ctx` in `valedia_silent_failure_probes.py`: **WARN** if loaded and `loaded_ctx < 65536`; **PASS** if ≥ floor or not loaded (INFO). `parallel≠2` never FAILs the suite alone.

## Paperclip → ensure-gate (read-only)

Paperclip admission should call **preflight** before starting a desk/Hermes run. Do **not** set `ORNITH_LOAD_APPLY` from Paperclip.

```bash
# Desk / Paperclip admission (exit 0 = ok; 2 = drift; 1 = tool error)
python3 ~/.openclaw/workspace-v2/scripts/integrations/ornith_ensure_gate.py \
  preflight --purpose desk
# accel symlink also OK:
# python3 ~/.openclaw/workspace-accel-ornith/scripts/integrations/ornith_ensure_gate.py \
#   preflight --purpose desk

# Floor-only (no purpose target)
python3 .../ornith_ensure_gate.py status
```

**Exit codes**

| Code | Meaning |
|-----:|---------|
| 0 | ok vs floor; if `--purpose` given, also `loaded_ctx >= target_ctx` |
| 2 | drift — `loaded_ctx < floor` and/or purpose target mismatch (or not loaded when purpose required) |
| 1 | tool / probe error |

**Drift JSON** (also printed on ok): `{ok, loaded, loaded_ctx, parallel, purpose, target_ctx, recommendation, lms_load_cmd}` (+ floor/source extras).

On exit 2, ops (not Paperclip) may run the printed `lms_load_cmd`, or:

```bash
python3 .../ornith_load_policy.py recommend --purpose desk --apply
# executes only if ORNITH_LOAD_APPLY=1; else print-only
```

## Policy CLI

```bash
python3 ~/.openclaw/workspace-v2/scripts/integrations/ornith_load_policy.py status
python3 ~/.openclaw/workspace-v2/scripts/integrations/ornith_load_policy.py recommend --purpose desk|light|heavy
python3 .../ornith_load_policy.py recommend --purpose desk --apply   # print-only unless ORNITH_LOAD_APPLY=1
```

Accel symlinks: `~/.openclaw/workspace-accel-ornith/scripts/integrations/ornith_{load_policy,ensure_gate}.py` → v2 canonical.
