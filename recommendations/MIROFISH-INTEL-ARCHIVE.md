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
