# Grok Build status

**Updated:** 2026-09-18 PT

**Mode:** RuFlo MCP (`:8810` filtered) + Ponytail. Hard fence held.

**Current:** STOP — Arc G NT-0 + NT-1 shipped. NT-3 OpenTerminal UI not this session.

**Shipped:** Slices 1–23 + S24 live messaging; model switcher; **NT-0** pin; **NT-1** MiroFish neural enrichment JSON.

| Slice | Tip | Version | Tag |
|-------|-----|---------|-----|
| NT-0 | `6f594d1` | 0.23.0 (no bump) | — |
| NT-1 | `bf798eb` | **0.24.0** | `valedia-v0.24.0` |

**Fence:** No trader live, no broker writers, no VENUE_WRITER / REBALANCER_AUTOEXEC / SPOT_RECONCILE_HTTP / NAUTILUS_LIVE flips. CLI-first neural-trader; no opus/sonnet agents. MiroFish weight 0.10 / `MIROFISH_AUTO_RAISE=False`. Filtered MCP 57 tools, 0 neural/live/broker.

**NT-0:** `tools/neural-trader` pin `2.8.11` `--ignore-scripts`. Allowlist research verbs only. Smoke: `recommendations/NT-0-SMOKE.md`. `test_nt0.py` PASS.

**NT-1:** `mirofish_neural_enrich.py` → `models/mirofish_neural_enrichment.json` (regime + signal quality). No fusion weight change. `test_nt1.py` PASS. Tests mocked, no network orders.

**Next:** STOP for Burton. NT-2 = wire enrichments into MiroFish seed/extractor + accuracy `neural_enriched` tag.
