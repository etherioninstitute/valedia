# GROK CHARTER — Ollama System One / Nimble vs Laya (research + optional replace plan)

**When:** 2026-09-30 ~16:55 PT  
**Mode:** RESEARCH first. Paper/ops only. **Do NOT replace Laya** until stamp recommends and Burton/Trader yes.  
**Expected stamp:** `recommendations/OLLAMA-SYSTEMONE-VS-LAYA-2026-09-30.md` ending with `GROK_EXIT:0|1`

## Why

Burton excerpt (Ollama 0.35+): System One API based on TypeSafe’s Jev API — `POST /v1/systemone` for classify / yes-no / rubric score. Models: Nimble 9B (Bespoke), Tev1 4B / 0.8B (Together). Local, no API key. He is **not** returning the desk to Ollama as primary LLM, but wants the **best part** of this API if it beats **Laya** — and if so, a **clean** replace path via Grok Build.

## Current Valedia facts (do not invent)

| Piece | Role | Notes |
|--|--|--|
| **Jev** (TypeSafe cloud) | Optional seasoning `VALEDIA_JEV=1` | Wired; not size/authority |
| **Laya** (laya-coreml ANE) | Optional shadow `VALEDIA_LAYA` default off | HTTP `127.0.0.1:8766`, `enforcement=none`, pack `valedia-paper-v1`; decide latency measured **~1.9–7.3 s** on paper tape |
| Desk LLM | LMS Ornith MLX / Qwen via `:1234` | Ollama **retired** as desk default (PHASE5/6); Ollama.app may still exist on disk |

Fences: `VALEDIA-LAYA-FENCED.md`, `LAYA-ANE-HFT-HOTPATH-2026-09-30.md`, `JEV-ON-PROOF-2026-09-30.md`. Client: `scripts/integrations/laya_hotpath_client.py`. Scan: `all_knowing_movers_scan.py`.

## Research tasks

1. **Pull primary sources** (WebFetch/search): Ollama 0.35 System One blog + API docs; Nimble / Tev1 model cards; how `/v1/systemone` maps to Jev-like questions/labels/probabilities.
2. **Compare apples-to-apples** vs Laya-CoreML on Valedia’s actual use (typed shadow stamps: admit_paper / edge_quality / venue_class — not chat LLM):
   - Latency (target: sub-second ideal for hot path; Laya is multi-second today)
   - Hardware path (ANE/CoreML vs Ollama Metal/CPU)
   - API shape vs our `laya_hotpath_client` / Jev shadow
   - Local-only / no key vs cloud Jev
   - Footprint, pull size, M1 Studio fit next to Ornith LMS
   - Quality for triage/routing/classification (honest limits)
3. **Check machine reality:** Is Ollama 0.35+ installed/runnable (`/usr/local/bin/ollama`, `/Applications/Ollama.app`, NIMROD models)? Do **not** `ollama pull` huge weights unless needed for a tiny proof and disk allows — prefer docs + optional tiny Tev1 0.8B proof if safe. Do **not** unload LMS Ornith. Do **not** bounce Hermes trading LaunchAgents.
4. **Verdict table:** KEEP Laya / ADOPT System One beside Laya / REPLACE Laya (clean). Must answer: is System One better than Laya *for Valedia’s shadow decide path*, or only better as a general Jev-local twin / Hermes triage helper?
5. **If REPLACE or ADOPT:** write a **clean migrate plan only** (interfaces, env flags, fail-open, bind port, tests). Do **not** execute replace in this run. Keep Jev cloud seasoning unless evidence says otherwise. No `--live`, no money path, no Paperclip dual-write.

## Out of scope / fences

- No Hermes trading CHANGE-set / TCC strip / LMS unload / life Matrix-Signal bounce
- Do not make Ollama the desk chat model again
- Do not kill Laya service on `:8766` in this research pass
- Do not interrupt other `valedia-grok` work if already mid-stamp — if screen busy, wait or use a queued follow-on; never clobber an in-flight GROK_EXIT stamp

## Stamp must include

- Sources + version claims verified
- Comparison table (Laya vs System One/Nimble vs Jev)
- Measured or cited latency/footprint
- Clear **KEEP / ADOPT-alongside / REPLACE** recommendation + why
- If replace: step list for a later Grok Build (not done here)
- `GROK_EXIT:0` on solid research; `1` with blockers

## Burton addendum — Safari Grok verdict (2026-09-30 ~17:04 PT)

Treat this as the working hypothesis to **validate + stamp**, not as already shipped:

- **Default local decision model: Nimble 9B** (System One) — only open weight that nearly matches Jev on the shared suite; official Apple Silicon path; 8k enough for structured snapshot (regime, spread, inventory, last N prints, one headline); cheap next to resident 35B.
- **Not raw Laya** as main trading judge unless every call is a tiny checklist; recalibrate or typed-decisions checkpoint; raw Laya ≠ zero-shot engine.
- **Not Tev1-0.8B** for money-adjacent gates.
- **Tev1-4B** = backup only if fine-tuned on journal ($17 recipe); OOTB support/policy classifier, not tape reader.
- **Official Jev** = keep as optional cloud seasoning if he accepts cloud + “judge, don’t trade.”

This run: prove/refute with sources + machine fit; recommend **ADOPT Nimble shadow beside Laya** (or REPLACE plan). **Do not execute Laya teardown** until Burton/Trader yes. Prefer clean dual-flag design (`VALEDIA_NIMBLE` / System One client) mirroring Laya fail-open shadow.

## Burton YES — execute replace (2026-09-30 ~17:05 PT)

**Approved:** Laya teardown + Nimble 9B System One as default local decision model. Must work perfectly (proof required).

### Execute (not plan-only)
1. Install/verify Ollama 0.35+ System One; `ollama pull nimble` (or document block).
2. Implement Valedia client mirroring Laya shadow shape: fail-open, `enforcement=none`, paper/ops only, judge-don’t-trade. Env e.g. `VALEDIA_SYSTEMONE=1` / Nimble model pin.
3. Wire into movers/hotpath where Laya stamped; keep code owning size/label; `create_order=false`.
4. Proof: health + fixture/live annotate latency + tests green.
5. Then **teardown Laya**: stop `:8766` service, default-off/remove `VALEDIA_LAYA` path, update fences (`VALEDIA-LAYA-FENCED` → retired; new Nimble fence stamp).
6. Keep cloud Jev optional. No Tev1-0.8B on money-adjacent gates. No LMS unload, no trading LA bounce, no `--live`.

Stamp: `OLLAMA-SYSTEMONE-VS-LAYA-2026-09-30.md` **or** `NIMBLE-SYSTEMONE-REPLACE-LAYA-2026-09-30.md` with GROK_EXIT + proof commands.
