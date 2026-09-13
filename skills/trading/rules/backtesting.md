---
description: >
  OOS backtesting results for kernel versions. Hit rates, alpha by asset class,
  and comparison to the RenTech 51% baseline.
---

# backtesting

All results are out-of-sample (OOS) — the kernel never saw these dates during training.
RenTech (Renaissance Technologies) benchmark: ~51% hit rate.

## v16e OOS Results (2024-2026)

| Asset | Return | B&H | Alpha | Hit% | Regime |
|-------|--------|-----|-------|------|--------|
| ADA | +57.9% | -40.1% | **+98.1%** | 52.6% | REV |
| DOGE | +55.9% | -29.7% | **+85.6%** | 51.3% | REV |
| XRP | +55.9% | +133.1% | -77.2% | 48.7% | REV |
| LINK | +33.5% | -37.0% | **+70.6%** | 48.7% | REV |
| ETH | +10.8% | -42.1% | **+52.9%** | 50.2% | REV |
| GLD | +25.6% | +108.3% | -82.8% | 60.0% | VOL |
| TSLA | +20.6% | +89.5% | -69.0% | 50.7% | VOL |

**Crypto summary: Alpha +40.7%, W=6/8** ← the settled result
Equity summary: Alpha -37.8%, W=1/10 (kernel optimized for crypto)

## 2025 YTD narrow window tests

Best filter: `votes≥8 + MOM + trend5`
- Top performers: DOGE 75%, TSLA 69.2%, ADA 62.5%
- Best rule: `votes≥8 + MOM + trend5` → **81.8% hit rate** on 11 trades
- Best training window: **4 years** → 52.1% hit rate (beats 51% baseline)

## Day-of-week empirical data (2025)

| Day | Hit rate | Trades |
|-----|----------|--------|
| Monday | 44.4% | 18 |
| Tuesday | 30.4% | 23 |
| **Wednesday** | **86.7%** | **15** |
| **Thursday** | **66.7%** | 9 |
| Friday | 43.8% | 16 |

## Version comparison (OOS 2024-2026)

| Version | Crypto Alpha | Equity Alpha | Total Alpha |
|---------|-------------|-------------|-------------|
| v16e | **+40.7%** | -37.8% | -2.9% |
| v16f | +33.9% | -38.6% | -6.4% |

**v16e is the settled version.** v16f discarded.

## Related

- [[core/kernel-v16e]] — the engine being tested
- [[core/filter-v3]] — filter derived from these tests
- [[rules/day-of-week]] — full day-of-week analysis
