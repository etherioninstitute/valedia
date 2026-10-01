# ADAPT: freqtrade

**Source:** `~/MemPalace/sources/repos/github/freqtrade` @ `0683152`  
**Upstream:** https://github.com/freqtrade/freqtrade  
**License:** GPL-3.0  
**Stack:** Python 3.11+, SQLite persistence, Telegram/WebUI, CCXT exchanges, FreqAI, hyperopt  
**Audited:** 2026-09-19 (docs-only)

## 1) Summary
Mature open-source crypto bot: dry-run, backtesting, hyperopt, FreqAI adaptive ML, whitelists, WebUI/Telegram.

## 2) Pipeline fit
`data → strategy → backtest → risk` (strong). Execution is exchange-live — **out of scope** for Valedia subsume.

## 3) Overlap
Valedia has allocator, sports engine, Kalshi fee map, smoke harnesses — but not a general OHLCV strategy DSL like freqtrade.

## 4) Steal (research/paper)
- Strategy class + hyperopt loop patterns
- Dry-run / lookahead-analysis discipline
- Pairlist whitelist/blacklist as series filters (map to Kalshi series fee map)
- Backtest report structure

## 5) Hard exclusions
- **GPL-3.0** — do not vend/link freqtrade code into Valedia proprietary tree without legal review; prefer **ideas/reimplementation**
- No `trade` live mode, no exchange keys into Valedia writers
- No Telegram control plane for live Kalshi

## 6) Valedia shape
**research / paper** — strategy+backtest patterns for crypto shadow desk later; Kalshi remains primary live venue.

## 7) Effort / deps
**L** (GPL + crypto-vs-PM mismatch). Deps: fee-true backtest harness; optional CCXT read-only later.
