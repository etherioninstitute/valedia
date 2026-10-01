# Hermes Valedia Optimize Plan (READ-ONLY gap)

> Generated: 2026-09-30 ~13:15 PT  
> Machine: Burtons-Mac-Studio-M1 (`b3f456da-e4fc-42d6-8147-a47ad76a8982`)  
> Reference: `/workspace/HERMES-DOCS-REFERENCE-Valedia.md` (box)  
> Scope: plan only — **no** config edits, service restarts, LMS load/unload, LM Link, or `--live`

**Label legend:** **FACT** = read from live configs / gateway / `lms` / known Burton constraints. **RESEARCH** = Hermes docs reference. **RECOMMENDATION** = proposed next move (needs Burton yes before apply).

## Trader veto/keep (2026-09-30)

**FACT corrections:** desk LMS ids are now MLX catalog — use `ornith-1.5-35b-a3b-mlx` (not bare `ornith-1.5-35b-a3b`), seasoning `ornith-1.5-9b-mlx`, Scout/tick assist prefer `qwen/qwen3.8-27b`, catalog-only `ulam-1` + `qwen/qwen3.6-35b-a3b`. Trader mid-retargeting live pins; do not re-pin old non-mlx Ornith ids.

**KEEP (trader):**
- Ornith-solo on scout/decider/auditor (no MoA or Qwen dual-path on trader serves)
- Decider/auditor web/search/clarify off; scout web ok; empty MCP; MoA/streaming off; stale 1800; reasoning high leave alone
- No --live / OKX / LMS auto-unload / omlx cutover / MemPalace on trader profiles
- Root MoA is Assistant-only; must not leak into trader gateway serves; trader MoA false wall

**CHANGE (Trader endorses; needs Burton yes):**
1) tool_search → false on all three trading profiles
2) model.context_length 65536 on decider + auditor (match scout); provider ceiling 131072 ok
3) Scrub delegation/cron ollama ornith-1.5:latest leftovers → lms-local Ornith mlx id, or clear if toolset disables delegation
4) Load Ornith in LMS is Burton-owned — load ornith-1.5-35b-a3b-mlx only; no scripted load/unload
5) Optional scout reasoning down → DEFER

**VETO:**
- MoA/Qwen reference on trader profiles or sharing root MoA into trader ticks
- Auto LMS load/unload or dual-resident Qwen+Ornith for Paperclip
- omlx/:8000 as this hygiene pass
- Gateway --replace, Signal/Matrix churn for trader reasons
- Making 9B / Ulam / Qwen3.6 the desk money default

**Prioritized apply:** Burton loads mlx Ornith → then yaml hygiene (tool_search + ctx + ollama scrub + mlx id pins) as one approved set. Matrix = Assistant ops.

---

## 1. FACT — current state (short)

### Runtime / gateway
| Item | Value |
|------|--------|
| Hermes | v0.21.0 gateway running (`gateway_state: running`, PID 66704) |
| Platforms | **Signal: connected**. **Matrix: retrying** (`failed to reconnect`, needs_attention) since ~08:35 PT |
| LMS (`lms ps` @ plan time) | **No models currently loaded** (library still holds Ornith/Qwen/MLX/ULAM per Burton 2026-09-30 screenshot — not re-inventoried here) |
| Primary provider | `custom:lms-local` → `http://127.0.0.1:1234/v1` (LM Studio) |
| Money lane default | `ornith-1.5-35b-a3b-mlx` on scout/decider/auditor (**Ornith-solo** as of 2026-09-28; bare `ornith-1.5-35b-a3b` superseded by MLX id) |

### Root `~/.hermes/config.yaml` (mtime 2026-09-29 00:22 PT) — life / Hermex host
| Knob | Current |
|------|---------|
| `model.default` | `ornith-1.5-35b-a3b` |
| `model.context_length` | **131072** |
| `model.provider` | `custom:lms-local` |
| `agent.reasoning_effort` | **low** (not `none`) |
| `toolsets` | `hermes-cli`, `web` |
| `agent.disabled_toolsets` | thick disable list (tts/browser/delegation/kanban/cronjob/code_execution/vision/…) — **clarify NOT disabled** |
| `tools.tool_search.enabled` | **auto** |
| `plugins.disabled` | includes **ponytail** |
| `mcp_servers` | **all 14 disabled** (incl. mempalace) |
| `memory.provider` | **holographic** |
| `moa.enabled` | **true** — reference `qwen3.8-27b`, aggregator Ornith |
| `providers…stale_timeout_seconds` | **1800** |
| `platforms` in yaml | matrix enabled; Signal via `.env` (running in gateway) |

### Profile `valedia` (life assistant; mtime 2026-09-28 23:08 PT)
| Knob | Current |
|------|---------|
| Model | Ornith @ lms-local, **ctx 65536** |
| Reasoning | **low** |
| Toolsets | `hermes-cli`, `web` + same thick disabled list |
| `tool_search` | **auto** |
| MemPalace MCP | **enabled** (read-ish include list) |
| memory | holographic |
| **ponytail** | **enabled** in plugins |
| MoA | **false** |
| Matrix | enabled in profile yaml |

### Profile `valedia-cto`
| Knob | Current |
|------|---------|
| Model | Ornith @ lms-local (no top-level `context_length`) |
| Reasoning | **high** |
| MemPalace | **enabled** |
| MoA | false; streaming false |
| Matrix | disabled |
| Delegation/cron still point at **ollama** `omni-qwen-122b` (stale path) |

### Trader profiles `valedia-trading-{scout,decider,auditor}` (Ornith-solo)
| Knob | scout | decider | auditor |
|------|-------|---------|---------|
| default model | Ornith / lms-local | same | same |
| `model.context_length` | **65536** | **unset** | **unset** |
| provider ornith ctx | 131072 | 131072 | 131072 |
| `reasoning_effort` | **high** | **high** | **high** |
| `tool_search` | **auto** | **auto** | **auto** |
| MoA / streaming | false | false | false |
| MCP | empty `{}` | empty | empty |
| clarify | **disabled** | disabled | disabled |
| web/search | kept | **disabled** | **disabled** |
| delegation/cron | still **ollama** `ornith-1.5:latest` | same | same |
| paperclip / `--live` / OKX strings in yaml | none | none | none |

### Drift vs prior Option B notes (**FACT** gap, not contradiction of goals)
- Option B root “thin toolsets clarify/memory/todo + reasoning none + qwen 16k” is **not** what root yaml shows now (hermes-cli+web, reasoning low, Ornith 128k, MoA on).
- Option B profile “tool_search off” is **not** current — all target profiles still have `tool_search.enabled: auto`.
- Ornith-solo trading model defaults **are** in place; LMS just has **nothing loaded** at this check.

---

## 2. Keep / change / defer

### 2A. Valedia AI Assistant (life Hermes, Hermex, M1, MemPalace, no CoS duplication)

#### KEEP (**RECOMMENDATION**)
- `custom:lms-local` @ `:1234` as sole live backend for life + money (aligns with M1 cutover; Ollama kept only as retired/hidden picker baggage).
- MemPalace MCP **on `valedia` / `valedia-cto`**, **off on root** — good split; avoid enabling MemPalace on root gateway yaml.
- Root ponytail **off**; holographic memory as primary short memory (MemPalace = long graph, not a second CoS).
- Thick `disabled_toolsets` for chat hygiene (no browser/computer_use/delegation spam on life chat).
- Never rewrite life Matrix/Signal via `hermes gateway --replace`.

#### CHANGE (approve before apply) (**RECOMMENDATION**)
1. **Root MoA → off** (or force reference = Ornith only). MoA fanout to `qwen3.8-27b` fights Ornith-solo / empty LMS and adds latency. Valedia profile already has MoA false — root is the outlier.
2. **`tools.tool_search.enabled: false`** on root + `valedia` (restore Option B speed intent; hermes-cli+web surface is already bounded).
3. **`valedia` ponytail → off** (or leave only if Hermex UI pet is intentional — root already disables it; profile re-enables).
4. **Align ctx story:** either set life `model.context_length` to what LMS actually serves for Ornith (docs: Hermes wants ≥64k; RESEARCH) **or** keep 64k on profile / 128k only if LMS load matches — mismatch wastes prefill or truncates.
5. **Matrix reconnect** as a *careful* ops item (Signal is healthy). Diagnose Matrix without gateway replace — see fences.

#### DEFER
- Re-thinning root toolsets down to only `clarify,memory,todo` (would drop web/file/terminal from hermes-cli — big behavior change; only if Burton wants ultra-minimal chat again).
- Re-enabling any of the 14 root MCPs except a deliberate MemPalace/life exception.
- Honcho / second memory provider (would risk CoS-style duplication alongside holographic + MemPalace).
- Switching Hermex away from `custom:lms-local` or enabling LM Link.
- Loading Qwen alongside Ornith “just in case” on life lane.

### 2B. Valedia Trader (scout / decider / auditor + Paperclip fences)

#### KEEP (**RECOMMENDATION**)
- Ornith-solo defaults on all three trading profiles.
- Decider/auditor: web/search/clarify/delegation **off** (decision purity).
- Scout: clarify/delegation/browser off; web available for research (current disabled list keeps web).
- Empty `mcp_servers` on trading profiles (Paperclip/fences stay outside Hermes MCP sprawl).
- MoA **off**, streaming **off** on trading profiles.
- `stale_timeout_seconds: 1800` on lms-local.
- Reasoning **high** left alone (Option B: thinking not forced off on profiles).

#### CHANGE (approve before apply) (**RECOMMENDATION**)
1. **`tools.tool_search.enabled: false`** on all three trading profiles (Option B speed; unused dynamic tool search).
2. **Set `model.context_length: 65536`** on decider + auditor (scout already has it) for consistent compression thresholds — provider map listing 131072 is fine as capability ceiling, but unset top-level is a footgun.
3. **Retarget delegation/cron** off Ollama (`ornith-1.5:latest` @ `:11434`) → `custom:lms-local` / Ornith (or clear delegation entirely since toolset disables it). Prevents surprise ollama wake if something calls those paths.
4. **Before Paperclip ticks:** ensure LMS has Ornith loaded (currently **zero** models loaded — trader will fail cold). Load is a Burton/LMS action, not auto-unload/reload scripting.
5. Optional: scout-only lower reasoning (`medium`/`low`) if scout latency dominates Paperclip cycle — **defer** unless measured.

#### DEFER
- Adding MemPalace or trading MCPs to trader profiles.
- Dual-model MoA (Ornith+Qwen) for auditor “second opinion” — contradicts Ornith-solo until Burton reopens it.
- Any `--live`, OKX unlock, or LMS auto-unload automation.
- Migrating trader inference to omlx port `:8000` (see §4).

---

## 3. Explicit “do not touch” fences (**FACT** constraints)

| Fence | Why |
|-------|-----|
| No `hermes gateway --replace` on life Matrix/Signal | Signal is connected; replace risks killing the healthy path |
| No LM Link enable | Force-disabled by design; stay on `custom:lms-local` |
| No LMS auto-unload / scripted unload of loaded models | Burton must approve any unload |
| No `--live` on trader / Paperclip | Paper trading only |
| No OKX unlock flip | Money fence |
| No editing configs in *this* pass | Plan-only; apply in a later approved change set |
| No CoS reintroduction / dual personality stacks | SOUL + holographic + MemPalace only; `SOUL.cos-backup.md` stays archive |
| Money four-pack stays Hermes+LMS local | Not Grok Bot |
| Do not “fix” Matrix by restarting Signal or wholesale gateway replace | Isolate Matrix reconnect |

---

## 4. MLX / omlx — option only (not a switch project)

**RESEARCH** (Hermes docs §4 in reference): omlx is an MLX backend, default `http://127.0.0.1:8000`, macOS-native; docs say MLX often wins tok/s, llama.cpp/LMS often wins TTFT + quantized KV; Hermes min context **64k**.

**FACT:** LMS library already includes MLX builds (`ornith-*-mlx`, `qwen/qwen3.8-27b` MLX, `qwen/qwen3.6-35b-a3b` MLX) *and* GGUF paths — so “try MLX” can mean **load the MLX variant inside LM Studio**, not necessarily install omlx.

| | Stay LMS (GGUF or LMS-MLX) @ `:1234` | Add omlx @ `:8000` |
|--|--------------------------------------|---------------------|
| Pros | One server Hermex already uses; no second provider; Ornith-solo unchanged | Docs claim strong tok/s; multi-model serve story |
| Cons | Depends on LMS MLX quality for these weights | Second daemon, new `custom:` provider, Hermex/picker churn, port clash risk with other local services |
| Verdict | **Preferred default** | **Evaluate later** with a side-by-side TTFT/tok/s on *same* Ornith prompt — only if LMS-MLX underwhelms |

**RECOMMENDATION:** Do **not** plan a cutover. If curious: one Burton-supervised A/B inside LMS (GGUF Ornith vs MLX Ornith) first; omlx only if LMS cannot serve the MLX weights well.

---

## 5. Prioritized next 5 actions Burton would approve

1. **Load Ornith in LMS** (manual) so life + trader stop sitting on an empty `lms ps` — no unload of anything else needed if nothing is loaded.  
2. **Disable root MoA** (or Ornith-only reference) — biggest silent latency/fail risk while Qwen isn’t loaded.  
3. **Turn `tool_search` off** on root + `valedia` + three trading profiles (Option B speed restore; low risk).  
4. **Normalize trader ctx + kill ollama delegation/cron leftovers** on scout/decider/auditor (config hygiene; no gateway touch).  
5. **Matrix reconnect diagnosis** (logs / homeserver `127.0.0.1:6167`) **without** gateway `--replace`; leave Signal alone.

*(Apply order: 1 operational now → 2–4 as a single approved yaml patch set → 5 ops.)*

---

## 6. Sources

- Live read: `~/.hermes/config.yaml`, `profiles/valedia{,-cto,-trading-scout,-trading-decider,-trading-auditor}/config.yaml`, `gateway_state.json`, `lms ps`, Signal/Matrix `.env` keys (names only).  
- Docs mine: `/workspace/HERMES-DOCS-REFERENCE-Valedia.md` §§3–5 (config knobs, local Mac / omlx, optimization).  
- Known Burton constraints preserved (M1 cutover, LM Link off, Ornith-solo trader, Paperclip fences, no CoS dup, no gateway replace).
