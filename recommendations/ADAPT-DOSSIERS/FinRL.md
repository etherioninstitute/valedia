# ADAPT: FinRL

**Source:** `~/MemPalace/sources/repos/github/FinRL` @ `2334a5f`  
**Upstream:** https://github.com/AI4Finance-Foundation/FinRL  
**License:** MIT  
**Stack:** Python, RL for finance (ElegantRL/RLlib/Stable Baselines), examples/docker  
**Audited:** 2026-09-19 (docs-only)

## 1) Summary
Academic/industry RL library for trading research and tutorials — environments, agents, benchmarks.

## 2) Pipeline fit
`strategy / research` (RL policy learning) — feeds signals, not OMS.

## 3) Overlap
neural-trader fenced to strengthen MiroFish; FinRL is adjacent research.

## 4) Steal
- Env wrappers for fee-true reward (transaction costs)
- Benchmark protocol for paper policies
- Feature pipelines for regime detection → MiroFish tags

## 5) Hard exclusions
- Not a parallel OMS
- No live broker adapters from FinRL tutorials into Valedia writers

## 6) Valedia shape
**research** — RL/paper policies → MiroFish/neural enrichment only.

## 7) Effort / deps
**M**. Align with VALEDIA-NEURAL-TRADER-FENCED.md.
