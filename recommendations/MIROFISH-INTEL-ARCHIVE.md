# MIROFISH-INTEL-ARCHIVE — live overwrite + dated archive

**Burton go:** 2026-09-22 ~21:23 PT (Finance Scout recommend; Burton yes small Build slice)  
**Queue position:** after CE-3

## Goal
On every successful MiroFish swarm:
1. Write dated archive `data/mirofish/YYYY-MM-DD.json` (or run-id sibling) then **atomic replace** the live intel path.
2. On FAIL: do **not** replace live; keep last-good; emit stale/age markers.
3. Allocators read **live only** if `!stale && age < T && usable_for_fv`.
4. Archive ≠ create fuel. Retention default 30–90 nights (prune helper OK).

## Scope (do)
1. Find current live intel write path (post MiroFish-100: `source=mirofish_swarm` overwrite of prior `intel_refresh`).
2. Implement PASS → archive then atomic live replace; FAIL → stale, no live wipe.
3. Expose/consume freshness fields for allocators (`as_of`, stale, `usable_for_fv`).
4. Tests for PASS/FAIL publish; fail keeps last-good; no venue network.
5. Brief doc note; Ponytail; `GROK-STATUS.md`. **Next=Burton stop.**

## Hard fence
MiroFish seasons only. No Poly LIVE / writers / wallets / oil-weather unfence. Near-neutral bias must not become create fuel.

## Done when
- PASS/FAIL paths proven; tests green; `GROK_EXIT:0`; Next=Burton

## Shipped (2026-09-22)

`scripts/integrations/mirofish_intel_archive.py`. PASS writes `data/mirofish/YYYY-MM-DD.json` and `YYYY-MM-DD.<run_id>.json`, then atomic-replaces `~/.openclaw/workspace/models/mirofish_signal.json`. FAIL writes `mirofish_freshness.json` beside live and does not open the signal for write. Allocators use `load_for_fv` (`as_of`, `stale`, age < 24h, `usable_for_fv`). `usable_for_fv` requires `source=mirofish_swarm` and `|overall_bias-50| > 5`. Archive copies are `usable_for_fv: false`. Prune drops nights older than 60. VALEDIA_VERSION **0.65.0**, not tagged. Live bias 51.5 is not fuel and was not rewritten.
