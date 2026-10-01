# ADAPT: hummingbot

**Source:** `~/MemPalace/sources/repos/github/hummingbot` @ `2bfaccc`  
**Upstream:** https://github.com/hummingbot/hummingbot  
**License:** Apache-2.0  
**Stack:** Python, connectors, Scripts/Controllers/Executors (V2), paper trade, Gateway for DEX  
**Audited:** 2026-09-19 (docs-only)

## 1) Summary
Market making, arb, XEMM, grid/DCA/TWAP executors; paper trading vs live connectors; Condor agentic layer.

## 2) Pipeline fit
`strategy → risk → execution` patterns (MM/arb). Valedia PM markets ≠ CEX MM, but **executor / triple-barrier** ideas transfer.

## 3) Overlap
Allocator sizing, sports engine, fee map — no pure MM desk yet.

## 4) Steal (paper/shadow)
- Executor building blocks + triple-barrier risk
- Paper-trade against live public data without keys
- Controller config tunable while running (for shadow sims)
- Connector abstraction (read path)

## 5) Hard exclusions
- No encrypted exchange keystore into Valedia live
- No Gateway DEX wallet ops
- No Condor live agent spending

## 6) Valedia shape
**paper / shadow** — MM/arb simulators scored fee-true on Kalshi where structure fits; else crypto paper only.

## 7) Effort / deps
**L**. Apache OK to study; map carefully to PM microstructure.
