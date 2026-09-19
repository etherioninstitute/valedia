# Ponytail audit — H2 persona lenses

**Mode:** `/ponytail-audit` on the H2 diff (touched modules), not the whole trading surface. Report-first; one safe skip applied (empty JSON write).
**Tree:** `~/.openclaw/workspace-v2` (this slice, pre-commit).
**Scope:** `persona_lenses.py`, extractor/seed attach, `test_h2.py`, version bump. Not archive/, not ERE, not NT-2 redo.
**Fence:** no `--live`, no writers, no `AUTO_RAISE`, no second Kelly.

## Findings (biggest cut first)

`yagni:` 45-agent Claude Code desk / MCP connectors / Thorp Kelly / `place_order`. Replacement: three deterministic checklists. **Not shipped.** [ai-fund-inspect, council/ is reference only]

`yagni:` LLM Ornith-CIO convening `council/advisors/*.md` at runtime. Replacement: `buffett()` / `lynch()` / `hayes()` functions. **Not shipped.** [persona_lenses.py]

`yagni:` `PERSONA_LENS_WEIGHT` / `FUSION_WEIGHTS["buffett"]` / accuracy-ledger `persona` tag. Replacement: nothing this slice — attach only, like NT-1 JSON. **Not shipped.** Add when fee-true lens accuracy exists.

`yagni:` `collect_persona_lenses` in `intel_opportunity_gatherer`. Replacement: nested `persona` on MiroFish blobs already copies into `raw`. **Not shipped.**

`yagni:` DCF / PEG calculators from `council/lib/valuation.ts`. Replacement: `INSUFFICIENT_DATA` when FCF/PEG missing. **Not shipped.** Inventing owner earnings from sentiment is a lie.

`shrink:` duplicate `EQUITY_HINTS` vs `intel_opportunity_gatherer.EQUITY_HINTS`. Replacement: import `_is_crypto` — **not applied** (would pull `nautilus_intent` into a scoring module). Keep 8-name frozenset.

`shrink:` extractor wrote `persona_lens_scores.json` even with empty `symbols`. Replacement: write only if symbols. **Applied.**

`yagni:` CLI / argparse on `persona_lenses.py`. Replacement: tests inject dicts. **Not shipped.**

`yagni:` new cron. Replacement: piggyback extractor + seed (same as NT-2). **Not shipped.**

## Not in scope (correctness / fence — left to normal review)

Hayes `STRUCTURAL_LONG` is a *lens verdict*, not a size. Gatherer `_score_of` ignores nested `persona`. Version pins in `test_nt1`/`test_nt2`/`test_slice23` updated to `0.26.0`.

## Net

`net: ~-12000 lines vs shipping ai-fund runtime (45 skills + MCP), -0 deps (stdlib only). H2 add: ~250 lines persona_lenses + ~140 test. Lean enough. Ship.`
