# Grok Build next queue (Burton 2026-09-18 ~23:31 PT)

## DONE
- **MIROFISH-SMOKE (0.37.0)** — minutes-scale wiring gate. Fixture PASS 6ms; live-lite PASS 7.4s on `:11435`. `test_mirofish_smoke.py` PASS. Overnight swarm still the corpus. **STOP — do not flip live.**

## After MIROFISH-SMOKE (Burton go; not auto-started)
1. **PONYTAIL-FULL** — full Ponytail audit of Valedia trading repo (report-only; like Slice 20).
   - Scope: `scripts/integrations/`, config, crons docs, Poly US, treasury, MiroFish path, fences
   - Output: `recommendations/PONYTAIL-AUDIT-VALEDIA-FULL.md` (ranked delete/yagni/shrink)
   - Apply only safe tiny cuts if obvious; otherwise report-only
   - NO live flips

2. **VALEDIA-FULL-SMOKE** — one CLI, minutes-scale, fenced (after Ponytail):
   - fence stamps, treasury, MiroFish smoke, Kalshi read, Poly preview (create blocked),
     shadow/adversarial/cockpit status → `recommendations/VALEDIA-FULL-SMOKE.md`
   - NO live flips

3. **KILL-SWITCH** — `VALEDIA_TRADING_HALT` before staged ON:
   - Halt blocks all venue writers; models/trading_halt.json; self-heal after re-smoke or escalate ping
   - Manual Burton OFF always works

4. **Staged ON** — only after Burton sees smoke + Ponytail + kill-switch

## Order (do not reorder)
MIROFISH-SMOKE → PONYTAIL-FULL → FULL-SMOKE → KILL-SWITCH → staged ON
