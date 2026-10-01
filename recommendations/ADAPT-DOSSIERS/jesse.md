# ADAPT: jesse

**Source:** `~/MemPalace/sources/repos/github/jesse` @ `432cce8`  
**Upstream:** https://github.com/jesse-ai/jesse   
**License:** MIT  
**Stack:** Python strategy framework, backtesting, self-hosted  
**Audited:** 2026-09-19 (docs-only)

## 1) Summary
Clean Python framework for developing and backtesting crypto strategies (self-hosted).

## 2) Pipeline fit
`strategy → backtest` (strong, simpler than freqtrade).

## 3) Overlap
Similar niche to freqtrade; MIT friendlier than GPL for selective reimplementation.

## 4) Steal
- Strategy API ergonomics
- Backtest metrics/report layout
- Self-hosted research loop without Telegram bot surface

## 5) Hard exclusions
- No live exchange automation into Valedia
- Crypto-first — map to Kalshi carefully

## 6) Valedia shape
**research / paper** — prefer over freqtrade for MIT reuse of small helpers.

## 7) Effort / deps
**M**. Alternative to freqtrade patterns when GPL is a blocker.
