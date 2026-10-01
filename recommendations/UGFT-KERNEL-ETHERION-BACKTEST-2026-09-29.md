# UGFT kernel + Etherion gates — paper backtest

Updated: 2026-09-29 21:43:28 PDT

**PAPER / fee-true / not-live.** No orders. No `--live`. No venue sends. No Kelly.

AUTONOMY-2 `jev_shadow` is MiroFish annotate-only. It is not a bar bulk backtester. Jev did not run these bars. Numbers below are a Python causal replay of `kernel_predictor_v16e` with Etherion risk gates.

This is not the TradingView SuperTrend test. SuperTrend Pine stays `//@version=6` (no bump): Desktop `tv-pine-profiler-2026-09-29/GROK-SuperTrend-Strategy-paper.pine`, `recommendations/SUPERTREND-STRATEGY-PAPER.pine`, and the accel-ornith twin.

## Book

| Item | Value |
| --- | --- |
| Name | UGFT = kernel_predictor_v16e + Etherion risk gates |
| Alpha | `KernelPredictor.predict` size only |
| Gates | `QEIDrawdownGuard` finite HALT_ALL→flat, REDUCE_50%→×0.5. A NaN ratio (flat window) is not a halt. `VarianceCascade` univariate `compute_sobolev_ladder` Gronwall_ratio≥2→×0.5 (KP crashes on one column). `ExecutionErrorEstimator` vol-bound ≥8%→flat, ≥5%→×0.5 |
| Not used | Etherion as alpha, MeanReversionEngine, Kelly, kernel 8 bps, SuperTrend |
| Fees | `CRYPTO_FEE_PCT` 0.001 + `CRYPTO_SLIPPAGE_PCT` 0.0005 = 0.0015 one-way on \|Δweight\| when \|Δ\|>0.05 |
| Clock | Daily close. Signal at t earns t→t+1. Warmup 80. No lookahead, no fit. |
| QEI arm | φ₀ from the prior 120 asset log-returns; checked on the last 60 asset log-returns. Not strategy pnl (a flat book blows up kurtosis and the class else-branch halts). Non-finite ratio is not a halt. |
| Equities | SPY and AAPL pay the same crypto book (conservative versus cash equities). |
| maxDD% | Peak-to-trough on the window. Not the kernel helper's max-drop / global-max. |
| fees_paid | Sum of turnover costs in equity units with start equity 1, shown as % of start. |

## Full sample

| Asset | Book | ret% | B&H% | alpha | Sharpe | maxDD% | hit% | trades | fees_paid% | qei_cut | qei_halt | qei_nan | cascade_cut | eee_cut | eee_halt | kernel_halt |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ETH-USD | UGFT | 87.13 | 1911.67 | -1824.54 | 0.456 | 23.07 | 51.4 | 489 | 15.904 | 251 | 513 | 0 | 574 | 286 | 69 | 0 |
| ETH-USD | kernel same fees | 114.77 | 1911.67 | -1796.91 | 0.346 | 32.57 | 50.9 | 517 | 24.900 | — | — | — | — | — | — | 0 |
| ETH-USD | span | 2020-01-01 → 2026-09-30 (2464 closes, crypto) | | | | | | | | | | | | | | |
| BTC-USD | UGFT | 5.42 | 1246.32 | -1240.90 | 0.051 | 35.48 | 50.3 | 437 | 12.160 | 311 | 437 | 0 | 569 | 100 | 0 | 0 |
| BTC-USD | kernel same fees | 71.65 | 1246.32 | -1174.67 | 0.377 | 25.54 | 50.5 | 453 | 20.987 | — | — | — | — | — | — | 0 |
| BTC-USD | span | 2020-01-01 → 2026-09-30 (2464 closes, crypto) | | | | | | | | | | | | | | |
| SPY | UGFT | -3.56 | 192.43 | -195.98 | -0.107 | 13.08 | 53.1 | 479 | 13.975 | 118 | 154 | 0 | 185 | 0 | 0 | 0 |
| SPY | kernel same fees | -0.47 | 192.43 | -192.90 | -0.012 | 14.11 | 54.2 | 491 | 15.395 | — | — | — | — | — | — | 0 |
| SPY | span | 2020-01-02 → 2026-09-29 (1694 closes, equity) | | | | | | | | | | | | | | |
| AAPL | UGFT | 26.75 | 390.24 | -363.49 | 0.517 | 16.20 | 54.3 | 348 | 9.350 | 171 | 134 | 0 | 223 | 0 | 0 | 1 |
| AAPL | kernel same fees | 31.19 | 390.24 | -359.04 | 0.508 | 17.20 | 53.9 | 312 | 9.436 | — | — | — | — | — | — | 1 |
| AAPL | span | 2020-01-02 → 2026-09-29 (1694 closes, equity) | | | | | | | | | | | | | | |

UGFT alpha minus kernel-only alpha is the fee-true effect of the gates on the same path. Kernel-only still uses CE-3 costs, not 8 bps.

## Walk-forward years (UGFT)

Calendar slices of the one causal path. The kernel has no fitted weights to roll.

| Asset | Year | ret% | B&H% | alpha | Sharpe | maxDD% | hit% | trades | fees_paid% | qei c/h/nan | cascade_cut | eee c/h |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ETH-USD | 2020 | 10.93 | 455.50 | -444.57 | 0.614 | 10.01 | 54.4 | 54 | 1.249 | 1/54/0 | 85 | 1/69 |
| ETH-USD | 2021 | 11.96 | 399.13 | -387.18 | 0.378 | 16.26 | 52.0 | 103 | 3.307 | 19/61/0 | 84 | 184/0 |
| ETH-USD | 2022 | -0.00 | -67.50 | 67.50 | -0.000 | 23.07 | 51.7 | 58 | 1.687 | 53/99/0 | 80 | 89/0 |
| ETH-USD | 2023 | 7.94 | 90.64 | -82.69 | 0.481 | 12.46 | 46.9 | 75 | 2.179 | 57/40/0 | 66 | 0/0 |
| ETH-USD | 2024 | 2.80 | 46.07 | -43.27 | 0.163 | 19.01 | 52.5 | 74 | 2.306 | 34/82/0 | 113 | 0/0 |
| ETH-USD | 2025 | 21.36 | -10.97 | 32.33 | 0.809 | 10.55 | 51.4 | 79 | 3.314 | 52/73/0 | 61 | 12/0 |
| ETH-USD | 2026 | 11.90 | -9.95 | 21.85 | 1.009 | 5.42 | 53.8 | 46 | 1.863 | 35/104/0 | 85 | 0/0 |
| BTC-USD | 2020 | 26.53 | 368.90 | -342.36 | 2.010 | 3.37 | 58.3 | 59 | 1.323 | 4/42/0 | 91 | 68/0 |
| BTC-USD | 2021 | -7.78 | 59.67 | -67.45 | -0.292 | 33.04 | 48.9 | 83 | 2.694 | 46/87/0 | 60 | 32/0 |
| BTC-USD | 2022 | -5.66 | -64.27 | 58.60 | -0.464 | 10.97 | 54.1 | 46 | 1.123 | 49/120/0 | 78 | 0/0 |
| BTC-USD | 2023 | 8.11 | 155.42 | -147.30 | 0.562 | 16.12 | 49.5 | 83 | 2.683 | 14/73/0 | 110 | 0/0 |
| BTC-USD | 2024 | -4.69 | 121.05 | -125.75 | -0.411 | 15.53 | 48.6 | 71 | 1.908 | 89/10/0 | 88 | 0/0 |
| BTC-USD | 2025 | -6.54 | -6.34 | -0.20 | -0.795 | 10.21 | 44.6 | 54 | 1.332 | 68/51/0 | 66 | 0/0 |
| BTC-USD | 2026 | -0.56 | -4.84 | 4.29 | -0.061 | 9.81 | 50.9 | 41 | 1.096 | 41/54/0 | 76 | 0/0 |
| SPY | 2020 | 5.17 | 32.52 | -27.35 | 0.630 | 8.12 | 59.2 | 83 | 2.501 | 0/0/0 | 16 | 0/0 |
| SPY | 2021 | 0.03 | 28.73 | -28.70 | 0.007 | 3.97 | 56.0 | 97 | 2.988 | 29/21/0 | 45 | 0/0 |
| SPY | 2022 | -5.31 | -18.18 | 12.87 | -1.432 | 6.27 | 36.2 | 46 | 0.958 | 19/12/0 | 22 | 0/0 |
| SPY | 2023 | 0.72 | 26.18 | -25.46 | 0.203 | 3.06 | 56.0 | 75 | 1.882 | 18/10/0 | 2 | 0/0 |
| SPY | 2024 | -3.45 | 24.89 | -28.33 | -0.869 | 4.88 | 56.0 | 73 | 2.233 | 24/50/0 | 37 | 0/0 |
| SPY | 2025 | -0.16 | 17.72 | -17.88 | -0.060 | 2.07 | 53.2 | 59 | 1.732 | 14/57/0 | 33 | 0/0 |
| SPY | 2026 | -0.27 | 12.94 | -13.21 | -0.075 | 4.11 | 48.2 | 46 | 1.681 | 14/4/0 | 30 | 0/0 |
| AAPL | 2020 | 14.58 | 91.71 | -77.14 | 1.225 | 14.43 | 57.4 | 76 | 2.367 | 7/0/0 | 36 | 0/0 |
| AAPL | 2021 | -0.05 | 34.65 | -34.69 | -0.008 | 5.43 | 54.2 | 58 | 1.415 | 28/16/0 | 19 | 0/0 |
| AAPL | 2022 | -5.47 | -26.40 | 20.94 | -1.694 | 5.66 | 43.0 | 20 | 0.343 | 17/17/0 | 17 | 0/0 |
| AAPL | 2023 | 6.46 | 49.01 | -42.55 | 1.370 | 2.97 | 53.2 | 64 | 1.389 | 28/25/0 | 21 | 0/0 |
| AAPL | 2024 | 5.09 | 30.71 | -25.61 | 0.878 | 3.92 | 63.0 | 48 | 1.608 | 22/2/0 | 14 | 0/0 |
| AAPL | 2025 | -0.21 | 9.05 | -9.26 | -0.041 | 4.52 | 51.7 | 43 | 1.188 | 15/25/0 | 55 | 0/0 |
| AAPL | 2026 | 4.86 | 21.50 | -16.64 | 1.104 | 3.17 | 51.2 | 39 | 1.040 | 54/49/0 | 61 | 0/0 |

## Kernel parity (same CE-3 cost, gates off)

- ETH-USD: PASS retΔ=0.0000 trades_match=True ref_ret=114.77 ref_trades=517
- BTC-USD: PASS retΔ=0.0000 trades_match=True ref_ret=71.65 ref_trades=453
- SPY: PASS retΔ=0.0000 trades_match=True ref_ret=-0.47 ref_trades=491
- AAPL: PASS retΔ=0.0000 trades_match=True ref_ret=31.19 ref_trades=312

## Fences

No orders. No `--live`. No venue sends. No `create_order`. No Kelly. Etherion did not supply a direction. Jev did not run bars.

Label: PAPER/fee-true/not-live

