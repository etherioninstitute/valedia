# ADAPT: nautilus_trader (gap update)

**Source:** `~/MemPalace/sources/repos/github/nautilus_trader` @ `e91692d`  
**Upstream:** nautilus_trader  
**License:** LGPL-3.0  
**Stack:** Rust core + Python, adapters, backtest/live engine  
**Audited:** 2026-09-19 gap pass (prior H6 done)

## 1) Summary
Professional-grade research/backtest/live trading infrastructure. Already partially subsumed as Valedia execution sidecar concepts.

## 2) Pipeline fit
`data → backtest → risk → execution infrastructure` — Valedia keeps live risk; Nautilus remains shadow/event model.

## 3) Overlap
H6 shadow parity / event model already shipped; Nautilus is known quantity.

## 4) Steal next (gaps only)
- Event-model parity tests still thin vs production Kalshi fills
- Adapter patterns for PM venues (research) without NAUTILUS_LIVE
- Performance/benchmark harness ideas from upstream BENCHMARKING.md

## 5) Hard exclusions
- No `NAUTILUS_LIVE`
- LGPL: careful linking; prefer process boundary / sidecar (already the Valedia approach)

## 6) Valedia shape
**shadow** — deepen parity only; Valedia runtime sole live.

## 7) Effort / deps
**M** incremental. Deps: fee map, fill-true Kalshi path.
