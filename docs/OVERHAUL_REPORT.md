# Valedia Trader 0.75.0: overhaul report (2026-11-10)

Everything below was run on this machine. Numbers come from `artifacts/walkforward_report.json`, the test suites and the audit commands; nothing is estimated.

## 1. Repository

- Canonical tree: `/Volumes/NIMROD_SSD/Qwen-Weight-Space/valedia-trader` (the newest copy; the pre-migration `~/.openclaw/workspace-v3` survives only in `/Volumes/NIMROD_SSD/backups/2026-10-22/home/.openclaw`).
- Under git since this session. Baseline tag `v0.74.1-recovered`, safety branch `backup/pre-fable-5.2-overhaul`.
- New package `valedia_trader/` (7 domains), single `pyproject.toml` + `uv.lock`, Python 3.12 arm64, numpy and scipy linked to Accelerate.
- Legacy flat layer `src/` kept in place (201 modules mapped to target domains in `valedia_trader/core/legacy.py`). It was not physically split: modules are pinned by content hash, the halt file path derives from their location, and 75 tests read sources by path.

Commits:

- `7fad2ad` Baseline: recovered Valedia tree v0.74.1 before overhaul
- `4a6306c` Add unified pyproject, uv.lock and valedia_trader package skeleton (v0.75.0)
- `33779b3` Harden legacy tree, restore lost subpackages, add Level 5 time-series engine
- `bccbd83` Add quant engine Levels 6-7: stochastic calculus and derivatives
- `70cb42f` Add quant engine Levels 8-10: risk, microstructure, research and ML
- `071deba` Add venue fee/schedule registry and the-odds-api client with odds math
- `8288423` Add zero-look-ahead backtest engine, sleeves, Darwinian committee, autoresearch
- `ded6cb6` Add quant engine Levels 1-4 and the 50-formula registry
- `ab08400` Add MiroFish model switch, core event bus, legacy domain map, no-trade buffer

## 2. Tests

| Suite | Result |
|---|---|
| New package (`pytest tests`) | 459 passed |
| Legacy scripts and pytest (`tools/run_legacy_tests.sh`) | 684 tests passed (605 before this session); 330 fail, all failing before as well |
| Lint (`ruff check valedia_trader tests`) | clean |

Legacy failures are pre-existing: files expected at pre-recovery paths, the deliberate `recovery_state` paper-only guard, and nested sandboxing in the MiroFish native tests.

## 3. Fifty formulas

| Id | Module | Implemented by |
|---|---|---|
| 1.1 | `level1_probability` | `bayes_posterior`, `bayes_binary_posterior`, `sequential_bayes_update`, `beta_binomial_update` |
| 1.2 | `level1_probability` | `expected_value`, `expected_pnl`, `expected_pnl_contributions`, `expected_count` |
| 1.3 | `level1_probability` | `variance`, `variance_of_sum`, `variance_decomposition` |
| 1.4 | `level1_probability` | `total_expectation`, `total_variance` |
| 1.5 | `level1_probability` | `binomial_pmf`, `binomial_cdf`, `poisson_pmf`, `poisson_cdf` … |
| 2.1 | `level2_statistics` | `standard_error`, `standard_error_of_mean`, `mean_confidence_interval` |
| 2.2 | `level2_statistics` | `sharpe_ratio`, `sharpe_t_stat`, `sharpe_t_stat_per_period`, `sharpe_t_stat_from_returns` … |
| 2.3 | `level2_statistics` | `ols`, `ols_slope`, `robust_regression`, `rolling_hedge_ratio` |
| 2.4 | `level2_statistics` | `correlation`, `r_squared`, `lead_lag_cross_correlation` |
| 2.5 | `level2_statistics` | `family_wise_error_rate`, `bonferroni`, `holm_bonferroni`, `benjamini_hochberg` … |
| 3.1 | `level3_linalg` | `covariance_matrix`, `is_psd`, `nearest_psd` |
| 3.2 | `level3_linalg` | `portfolio_variance`, `portfolio_volatility` |
| 3.3 | `level3_linalg` | `eigen_decomposition`, `pca`, `residualize_returns` |
| 3.4 | `level3_linalg` | `condition_number`, `inversion_guard`, `stable_solve` |
| 3.5 | `level3_linalg` | `ledoit_wolf_shrinkage`, `oas_shrinkage`, `shrinkage_target`, `shrink_covariance` |
| 4.1 | `level4_optimization` | `taylor_expansion`, `taylor_expansion_numeric`, `option_pnl_approximation`, `option_pnl_attribution` |
| 4.2 | `level4_optimization` | `gradient_descent`, `minimize_loss` |
| 4.3 | `level4_optimization` | `solve_equality_constrained`, `solve_equality_constrained_quadratic`, `optimize_constrained_portfolio` |
| 4.4 | `level4_optimization` | `markowitz_closed_form`, `mean_variance_optimize` |
| 4.5 | `level4_optimization` | `jensen_gap`, `volatility_drag`, `geometric_return_approx`, `compound_returns` … |
| 5.1 | `level5_timeseries` | `log_returns`, `simple_returns`, `log_to_simple`, `simple_to_log` … |
| 5.2 | `level5_timeseries` | `autocorrelation`, `partial_autocorrelation`, `ljung_box`, `classify_serial_dependence` |
| 5.3 | `level5_timeseries` | `adf_test`, `kpss_test`, `stationarity_verdict` |
| 5.4 | `level5_timeseries` | `hedge_ratio_ols`, `engle_granger`, `johansen_rank`, `fit_ou` … |
| 5.5 | `level5_timeseries` | `fit_garch`, `garch11_filter`, `garch11_update`, `garch_long_run_variance` … |
| 6.1 | `level6_stochastic` | `random_walk_paths`, `random_walk_sd`, `random_walk_expected_abs_distance` |
| 6.2 | `level6_stochastic` | `brownian_paths`, `correlated_brownian_increments`, `nearest_psd_correlation`, `quadratic_variation` |
| 6.3 | `level6_stochastic` | `gbm_terminal`, `gbm_paths`, `merton_jump_paths`, `mc_european_option` |
| 6.4 | `level6_stochastic` | `ito_coefficients`, `ito_log_price`, `binary_call_value`, `binary_contract_dynamics` |
| 6.5 | `level6_stochastic` | `gambler_ruin_win_probability`, `gambler_ruin_expected_duration`, `brownian_hit_upper_first_probability`, `gbm_take_profit_before_stop_probability` … |
| 7.1 | `level7_derivatives` | `bsm_price`, `digital_price`, `american_option_price`, `barrier_option_price` … |
| 7.2 | `level7_derivatives` | `put_call_parity_residual`, `synthetic_forward`, `parity_arbitrage_scan`, `box_spread_implied_rate` |
| 7.3 | `level7_derivatives` | `bsm_greeks`, `numerical_greeks`, `delta_neutral_hedge`, `delta_gamma_neutral_hedge` |
| 7.4 | `level7_derivatives` | `implied_volatility`, `svi_total_variance`, `calibrate_svi`, `svi_butterfly_density` … |
| 7.5 | `level7_derivatives` | `gamma_scalping_pnl_rate`, `simulate_delta_hedged_pnl`, `gamma_scalp_decision` |
| 8.1 | `level8_portfolio_risk` | `capm_beta`, `beta_hedged_returns` |
| 8.2 | `level8_portfolio_risk` | `factor_regression`, `factor_neutralizing_hedge`, `factor_risk_decomposition` |
| 8.3 | `level8_portfolio_risk` | `sharpe_ratio`, `annualize_sharpe`, `sortino_ratio`, `calmar_ratio` … |
| 8.4 | `level8_portfolio_risk` | `kelly_fraction`, `kelly_multi_event`, `kelly_continuous`, `kelly_continuous_from_returns` … |
| 8.5 | `level8_portfolio_risk` | `var_parametric`, `es_parametric`, `var_historical`, `es_historical` … |
| 9.1 | `level9_microstructure` | `mid_price`, `quoted_spread`, `spread_bps`, `effective_spread` … |
| 9.2 | `level9_microstructure` | `order_book_imbalance`, `micro_price`, `depth_weighted_imbalance` |
| 9.3 | `level9_microstructure` | `sqrt_impact`, `impact_cost`, `max_order_for_impact` |
| 9.4 | `level9_microstructure` | `vwap`, `twap_schedule`, `vwap_schedule`, `implementation_shortfall` … |
| 9.5 | `level9_microstructure` | `as_reservation_price`, `as_optimal_spread`, `as_quotes`, `simulate_market_maker` |
| 10.1 | `level10_ml_research` | `PointInTimeStore`, `universe_as_of`, `detect_lookahead`, `check_cost_completeness` … |
| 10.2 | `level10_ml_research` | `walk_forward_splits`, `PurgedKFold` |
| 10.3 | `level10_ml_research` | `deflated_sharpe_ratio`, `expected_max_sharpe`, `probabilistic_sharpe_ratio`, `sharpe_moments` … |
| 10.4 | `level10_ml_research` | `precision_recall_f1`, `triple_barrier_labels`, `meta_label_fit`, `bet_size_from_probability` |
| 10.5 | `level10_ml_research` | `fit_hmm`, `filtered_probabilities`, `causal_regime_probabilities`, `regime_capital_multipliers` |

All 50 are implemented and tested (398 quant-engine tests). `py_vollib_vectorized` is not used: it does not compile under current numba; vectorized pricing is native and checked against py_vollib and QuantLib.

## 4. Walk-forward backtest

Data 2012-01-03 to 2026-11-09, 17 instruments (15 ETFs, BTC, ETH), daily bars, one-bar execution lag, $1M notional. Selection period to 2019-12-31, holdout from 2020-01-01. Commission from the venue registry (ETFs 0, crypto 26 bps), assumed half-spread 1 bp ETFs / 5 bps crypto, 2% borrow, square-root impact. 27 configurations recorded as trials.

| Holdout 2020-01 to 2026-11 | Return | CAGR | Vol | Sharpe | t-stat | DSR | Max DD | Win | PF | Turnover | Cost bps |
|---|---|---|---|---|---|---|---|---|---|---|---|
| darwinian+buffer | +76.5% | +8.7% | 8.0% | 1.08 | 2.83 | 0.878 | 13.0% | 55.4% | 1.22 | 50x | 3.6 |
| darwinian | +73.6% | +8.4% | 8.1% | 1.04 | 2.72 | 0.856 | 13.6% | 55.1% | 1.21 | 58x | 3.5 |
| equal_weight_sleeves | +44.5% | +5.5% | 7.8% | 0.73 | 1.90 | 0.604 | 12.7% | 52.7% | 1.15 | 73x | 3.2 |
| darwinian+kelly+cro | +81.3% | +9.1% | 12.5% | 0.76 | 1.98 | 0.637 | 19.1% | 47.8% | 1.15 | 108x | 4.1 |
| darwinian+kelly+cro+regimes | +83.8% | +9.3% | 12.8% | 0.76 | 1.98 | 0.635 | 19.6% | 47.8% | 1.15 | 116x | 4.1 |
| darwinian+kelly+cro+reflexivity | +80.8% | +9.0% | 12.4% | 0.76 | 1.98 | 0.636 | 19.1% | 47.8% | 1.15 | 107x | 4.1 |
| sleeve:risk_parity | +85.1% | +9.4% | 11.5% | 0.84 | 2.19 | 0.709 | 20.8% | 56.1% | 1.17 | 4x | 3.5 |
| sleeve:xs_momentum | +33.9% | +4.4% | 10.7% | 0.45 | 1.18 | 0.328 | 21.0% | 52.6% | 1.08 | 35x | 4.2 |
| sleeve:trend | +13.4% | +1.9% | 7.7% | 0.28 | 0.73 | 0.184 | 15.1% | 53.3% | 1.05 | 28x | 2.6 |
| sleeve:reversal | +13.6% | +1.9% | 17.7% | 0.20 | 0.51 | 0.135 | 25.3% | 43.7% | 1.04 | 245x | 3.4 |
| SPY buy-and-hold (benchmark) | +157.2% | +14.8% | 20.0% | 0.79 | 2.07 | n/a | 33.7% | 54.7% | 1.16 | 0 | 0 |
| BTC buy-and-hold (benchmark) | +945.7% | +41.0% | 59.5% | 0.88 | 2.30 | n/a | 76.6% | 51.0% | 1.18 | 0 | 0 |
| equal-weight universe (benchmark) | +205.4% | +17.7% | 17.2% | 1.04 | 2.71 | n/a | 28.5% | 55.4% | 1.21 | 0 | 0 |

Reading it honestly:

- The stack does not beat buy-and-hold on raw return. Its best variant matches the equal-weight universe on Sharpe (1.08 vs 1.04) at under half the volatility and drawdown.
- Darwinian weighting improved on static equal weighting of the same sleeves out of sample (Sharpe 0.73 to 1.04); the no-trade buffer added a little more (1.08) and cut turnover.
- The Kelly and risk-officer overlay made it worse (Sharpe 0.76, deeper drawdown): Kelly leverage from noisy trailing means adds turnover, and the breaker sat latched for about 200 days.
- The autoresearch cycle tried 3 mutations and rejected all 3, so it changed nothing.
- Promotion gates: t-stat 2.83 passes the 2.5 bar; Deflated Sharpe 0.878 (holdout) / 0.932 (full) fails the 0.95 bar. By the system's own rule this is not promotable to live.
- Not covered: options, prediction markets and sports odds have no historical data here, so those modules are unit-tested only. No broker paper session was run (no keys in `~/.hermes/.env`); the report contains a dry-run order plan instead.

## 5. Venue fee registry

Verified rows are transcribed from the legacy fee constants (Kraken, Coinbase, Alpaca equity and options, Polymarket, Kalshi, Uniswap) and are 125 to 241 days old. Lookups for unverified venues raise instead of returning zero:

`alpaca/equity_regulatory`, `binance/perp`, `binance/spot`, `bybit/perp`, `bybit/spot`, `hyperliquid/perp`, `interactive_brokers/equity`, `interactive_brokers/option`, `kraken/perp`, `okx/perp`, `okx/spot`, `the_odds_api/sportsbook_bet`, `tradier/equity`, `tradier/option`.


## Addendum, 2026-11-10 (later the same day)

- Credentials moved to `~/.hermes/secrets/` (owner-only). Nimble v3 runs through Ollama and is pinned by digest.
- `agents/panel.py`: deciders and vetters (Hermes profiles, Nimble v3, local Qwen) pooled in log-odds with veto and quorum.
- TimesFM 2.5 (Apache weights) is installed and runs in its own process. Walk-forward verdict: no directional skill at a
  5-day horizon (rank IC about 0; sleeve Sharpe -0.06 on the holdout; adding it lowered the blend from 1.08 to 0.96). Its
  80% band was calibrated (80 to 82% coverage). TimesFM 3.0 weights were not used: their licence bars production use.
- Agent-G2's guidance schedule is ported and used to measure models, not in the decision path. Measured: `qwen3:8b` with
  thinking off solved 37% of the engine's sizing problems over 120 attempts with about two thirds of the worked steps
  given (mostly two or more steps left to do), and 8 of 9 in a separate check with only the last step left. Small samples.
- The Deflated Sharpe now counts distinct configurations (17 at the time, not 27 rows). The best blend's figures became
  0.869 on the holdout and 0.923 on the full period, still under 0.95.
- Meta-N and Harness2 are ported as the revision machinery of the research loop. See `docs/RESEARCH_LOOP.md` for the loop,
  its rules, and the first round's results (six new ideas, none passed).
