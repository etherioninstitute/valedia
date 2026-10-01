# ADAPT: ccxt

**Source:** `~/MemPalace/sources/repos/github/ccxt` @ `4cda4ed`  
**Upstream:** https://github.com/ccxt/ccxt  
**License:** MIT  
**Stack:** Multi-language (JS/Python/PHP/…), unified exchange API, MCP/docs  
**Audited:** 2026-09-19 (docs-only)

## 1) Summary
Infrastructure layer: one API surface across many CEX/DEX. Used by freqtrade et al.

## 2) Pipeline fit
`data` + future `execution` adapter — Valedia today uses venue-specific clients (Kalshi V2, Poly).

## 3) Overlap
Coinbase/Alpaca writers currently `none`; Poly/Kalshi custom. CCXT would standardize crypto reads.

## 4) Steal
- Unified market/order book types for **read-only** market data
- Exchange capability matrix thinking (what each venue supports)

## 5) Hard exclusions
- No restoring CB/Alpaca writers via CCXT without Burton go
- No live create_order through CCXT in Valedia
- Keep secrets out of repo `keys.json` patterns
- **OKX exception (2026-09-27):** Burton unlocked OKX for later Valedia live risk. Phase 1 is native OKX REST v5 (`scripts/integrations/okx_client.py` on `us.okx.com`), not the ccxt package. `OKX_LIVE=0` until a later runtime flip. Other CCXT venues stay research-only.

## 6) Valedia shape
**research / shadow data** — optional read-only crypto tape for MiroFish enrichment; not OMS.

## 7) Effort / deps
**M**. After Kalshi fee-true path is solid; before any crypto live.
