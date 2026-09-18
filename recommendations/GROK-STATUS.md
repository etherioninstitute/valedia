# Grok Build status

**NORTH STAR:** world's smartest, highest-profiting auto-trader (seamless; self-adjust when flipped; gates until Burton widens).
**Updated:** 2026-09-18 PT

**Mode:** RuFlo MCP (`:8810` filtered) + Ponytail. Hard fence held.

**Current:** SHIPPED — Arc G **NT-2**. `VALEDIA_VERSION` **0.25.0**. Tip `71a39f9`. Tag `valedia-v0.25.0`.

**What shipped**
- NT-1 JSON (`mirofish_neural_enrichment.json`) attached onto MiroFish seed (advisory markdown) and extractor (`mirofish_signal.json` per-symbol `neural` + top-level `neural_enriched`). Scores/bias/weight unchanged.
- Accuracy ledger tags `neural_enriched` true/false on predictions and **fee-true** results. Stats expose `n_scored_enriched` / `n_scored_plain` only — **no weight math change**.
- Tests: `test_nt2.py` PASS (mocked). Related: test_nt1, test_slice13, test_slice18, test_slice23, test_event_signs PASS.

**Fence (unchanged this slice)**
- `MIROFISH_AUTO_RAISE` = **False**
- Caps still **0.10** unproven / **0.15** proven
- No `VALEDIA_SELF_ADJUST` implementation (comment only in `valedia_config`)
- No trader live / broker / writers / SPOT_RECONCILE / NAUTILUS_LIVE / Intents

**Next:** STOP for Burton. Dynamic band + master flip (`VALEDIA_SELF_ADJUST`) = next named slice **after approval**. Do not start NT-3 OpenTerminal this stop.

**Shipped prior:** NT-0 `6f594d1`, NT-1 `bf798eb` / v0.24.0

---

**Standing (not this slice):** After Burton go — H1 dossiers / H2 ai-fund→MiroFish per addendum. Live writers still gated.
