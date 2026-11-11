"""Tests for valedia_trader.quant_engine.level2_statistics (formulas 2.1 to 2.5)."""

from __future__ import annotations

import dataclasses
import warnings

import numpy as np
import pytest
import statsmodels.api as sm
from scipy import stats
from statsmodels.stats.multitest import multipletests

from valedia_trader.quant_engine import level2_statistics as l2


# --------------------------------------------------------------------------- registry
def test_formulas_registry_is_complete_and_resolvable():
    assert set(l2.FORMULAS) == {"2.1", "2.2", "2.3", "2.4", "2.5"}
    for formula_id, names in l2.FORMULAS.items():
        assert names, formula_id
        for name in names:
            func = getattr(l2, name)
            assert callable(func)
            assert name in l2.__all__
            assert func.__doc__.strip().startswith(formula_id), (name, func.__doc__)


def test_all_names_exist():
    for name in l2.__all__:
        assert hasattr(l2, name), name


# --------------------------------------------------------------------------- 2.1
def test_quadrupling_n_halves_the_standard_error():
    se_n = l2.standard_error(0.02, 250)
    se_4n = l2.standard_error(0.02, 1000)
    assert se_4n == pytest.approx(se_n / 2.0, rel=1e-12)
    assert se_n == pytest.approx(0.02 / np.sqrt(250))
    n = np.array([25, 100, 400, 1600])
    np.testing.assert_allclose(l2.standard_error(1.0, n), [0.2, 0.1, 0.05, 0.025])


def test_standard_error_of_mean_matches_scipy_and_scales_with_n():
    rng = np.random.default_rng(1)
    x = rng.normal(0.001, 0.02, size=400)
    assert l2.standard_error_of_mean(x) == pytest.approx(stats.sem(x), rel=1e-12)
    assert l2.standard_error_of_mean(x, ddof=0) == pytest.approx(x.std() / 20.0, rel=1e-12)
    # replicating the sample four times keeps s (ddof=0) and so exactly halves the SE
    assert l2.standard_error_of_mean(np.tile(x, 4), ddof=0) == pytest.approx(
        l2.standard_error_of_mean(x, ddof=0) / 2.0, rel=1e-12
    )


def test_standard_error_validation():
    with pytest.raises(ValueError, match=">= 0"):
        l2.standard_error(-1.0, 10)
    with pytest.raises(ValueError, match=">= 1"):
        l2.standard_error(1.0, 0)
    with pytest.raises(ValueError):
        l2.standard_error(np.nan, 10)
    with pytest.raises(ValueError, match="at least 2"):
        l2.standard_error_of_mean([1.0])
    with pytest.raises(ValueError, match="1-D"):
        l2.standard_error_of_mean(np.ones((3, 3)))
    with pytest.raises(ValueError, match="ddof"):
        l2.standard_error_of_mean([1.0, 2.0], ddof=2)


def test_mean_confidence_interval_matches_scipy():
    rng = np.random.default_rng(2)
    pnl = rng.normal(0.4, 3.0, size=60)
    est = l2.mean_confidence_interval(pnl, confidence=0.95)
    lo, hi = stats.t.interval(0.95, 59, loc=pnl.mean(), scale=stats.sem(pnl))
    assert (est.ci_low, est.ci_high) == pytest.approx((lo, hi), rel=1e-12)
    ref = stats.ttest_1samp(pnl, 0.0)
    assert est.t_stat == pytest.approx(ref.statistic, rel=1e-12)
    assert est.p_value == pytest.approx(ref.pvalue, rel=1e-10)
    assert est.n == 60 and est.dof == 59
    assert est.std_err == pytest.approx(est.std / np.sqrt(60))
    wider = l2.mean_confidence_interval(pnl, confidence=0.99)
    assert wider.ci_high - wider.ci_low > est.ci_high - est.ci_low
    with pytest.raises(dataclasses.FrozenInstanceError):
        est.mean = 0.0  # type: ignore[misc]


def test_mean_confidence_interval_coverage():
    rng = np.random.default_rng(3)
    true_edge = 0.25
    hits = 0
    trials = 400
    for _ in range(trials):
        est = l2.mean_confidence_interval(rng.normal(true_edge, 1.0, size=30), 0.90)
        hits += est.ci_low <= true_edge <= est.ci_high
    assert hits / trials == pytest.approx(0.90, abs=0.05)


def test_mean_confidence_interval_zero_variance_is_explicit():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        const = l2.mean_confidence_interval([0.5, 0.5, 0.5, 0.5])
        zero = l2.mean_confidence_interval([0.0, 0.0, 0.0])
        neg = l2.mean_confidence_interval([-2.0, -2.0])
    assert const.std == 0.0 and const.std_err == 0.0
    assert const.ci_low == const.ci_high == 0.5
    assert const.t_stat == np.inf and const.p_value == 0.0
    assert zero.t_stat == 0.0 and zero.p_value == 1.0
    assert neg.t_stat == -np.inf


@pytest.mark.parametrize("bad", [[], [1.0], [1.0, np.nan], [[1.0, 2.0]], "abc"])
def test_mean_confidence_interval_validation(bad):
    with pytest.raises(ValueError):
        l2.mean_confidence_interval(bad)


def test_mean_confidence_interval_rejects_bad_confidence():
    for conf in (0.0, 1.0, 1.5):
        with pytest.raises(ValueError, match="confidence"):
            l2.mean_confidence_interval([1.0, 2.0, 3.0], conf)


# --------------------------------------------------------------------------- 2.2
def test_monthly_sharpe_point_two_over_36_months_gives_t_of_1_2():
    assert l2.sharpe_t_stat_per_period(0.2, 36) == pytest.approx(1.2, rel=1e-12)
    # same thing annualised: SR_annual = 0.2 * sqrt(12) over 3 years
    assert l2.sharpe_t_stat(0.2 * np.sqrt(12.0), 3.0) == pytest.approx(1.2, rel=1e-12)


def test_annual_sharpe_one_over_four_years_gives_t_of_2_and_fails_gate():
    assert l2.sharpe_t_stat(1.0, 4.0) == pytest.approx(2.0, rel=1e-12)
    gate = l2.sharpe_promotion_gate(1.0, 4.0)
    assert gate.t_stat == pytest.approx(2.0)
    assert gate.threshold == 2.5
    assert gate.passed is False
    assert gate.years_required == pytest.approx(6.25)  # (2.5 / 1)^2


def test_promotion_gate_threshold_is_a_parameter_and_boundary_is_inclusive():
    assert l2.sharpe_promotion_gate(1.0, 4.0, threshold=2.0).passed is True
    assert l2.sharpe_promotion_gate(1.25, 4.0).passed is True  # t = 2.5 exactly
    assert l2.sharpe_promotion_gate(1.0, 6.25).passed is True
    assert l2.sharpe_promotion_gate(1.0, 6.2).passed is False
    loser = l2.sharpe_promotion_gate(-0.5, 10.0)
    assert loser.passed is False and loser.years_required == np.inf
    assert l2.sharpe_promotion_gate(0.0, 10.0).years_required == np.inf


def test_promotion_gate_is_vectorised():
    gate = l2.sharpe_promotion_gate([0.5, 1.0, 2.0, 3.0], 2.0)
    np.testing.assert_allclose(gate.t_stat, np.array([0.5, 1.0, 2.0, 3.0]) * np.sqrt(2.0))
    np.testing.assert_array_equal(gate.passed, [False, False, True, True])
    np.testing.assert_allclose(gate.years_required, [25.0, 6.25, 1.5625, 2.5**2 / 9.0])


def test_sharpe_t_stat_validation():
    with pytest.raises(ValueError, match="years"):
        l2.sharpe_t_stat(1.0, -1.0)
    with pytest.raises(ValueError, match="n_periods"):
        l2.sharpe_t_stat_per_period(0.2, -3)
    with pytest.raises(ValueError):
        l2.sharpe_t_stat(np.nan, 1.0)
    for bad in (0.0, -1.0, np.inf):
        with pytest.raises(ValueError, match="threshold"):
            l2.sharpe_promotion_gate(1.0, 4.0, threshold=bad)
    assert l2.sharpe_t_stat(1.0, 0.0) == 0.0


def test_sharpe_ratio_and_t_stat_from_returns():
    rng = np.random.default_rng(4)
    r = rng.normal(0.0006, 0.01, size=756)
    sr_daily = l2.sharpe_ratio(r)
    assert sr_daily == pytest.approx(r.mean() / r.std(ddof=1), rel=1e-12)
    sr_annual = l2.sharpe_ratio(r, periods_per_year=252)
    assert sr_annual == pytest.approx(sr_daily * np.sqrt(252.0), rel=1e-12)
    # the two forms of the t-stat agree, and equal the one-sample t-test statistic
    t_periods = l2.sharpe_t_stat_per_period(sr_daily, r.size)
    t_years = l2.sharpe_t_stat(sr_annual, r.size / 252.0)
    assert t_periods == pytest.approx(t_years, rel=1e-12)
    assert l2.sharpe_t_stat_from_returns(r) == pytest.approx(t_periods, rel=1e-12)
    assert t_periods == pytest.approx(stats.ttest_1samp(r, 0.0).statistic, rel=1e-10)
    assert l2.sharpe_ratio(r, risk_free=0.0006) == pytest.approx(
        (r.mean() - 0.0006) / r.std(ddof=1), rel=1e-9
    )


def test_sharpe_ratio_validation():
    with pytest.raises(ValueError, match="zero volatility"):
        l2.sharpe_ratio([0.01, 0.01, 0.01])
    with pytest.raises(ValueError, match="periods_per_year"):
        l2.sharpe_ratio([0.01, 0.02], periods_per_year=0)
    with pytest.raises(ValueError, match="at least 2"):
        l2.sharpe_ratio([0.01])
    with pytest.raises(ValueError):
        l2.sharpe_ratio([0.01, np.inf, 0.02])


# --------------------------------------------------------------------------- 2.3
def test_ols_recovers_known_coefficients():
    rng = np.random.default_rng(10)
    x = rng.normal(size=(5_000, 3))
    true = np.array([0.5, 2.0, -1.0, 0.25])
    y = true[0] + x @ true[1:] + rng.normal(0.0, 0.1, size=5_000)
    fit = l2.ols(x, y)
    np.testing.assert_allclose(fit.coef, true, atol=0.01)
    assert fit.intercept == pytest.approx(0.5, abs=0.01)
    np.testing.assert_allclose(fit.slopes, true[1:], atol=0.01)
    assert fit.r_squared > 0.99
    assert np.all(np.abs(fit.coef - true) < 4.0 * fit.std_err)
    assert fit.sigma2 == pytest.approx(0.01, rel=0.1)
    # noiseless data is recovered to machine precision
    exact = l2.ols(x, true[0] + x @ true[1:])
    np.testing.assert_allclose(exact.coef, true, atol=1e-12)


@pytest.mark.parametrize("add_intercept", [True, False])
def test_ols_closed_form_matches_statsmodels_to_1e_8(add_intercept):
    rng = np.random.default_rng(11)
    x = rng.normal(size=(300, 4)) * np.array([1.0, 5.0, 0.2, 30.0])
    y = 0.7 + x @ np.array([1.0, -0.3, 4.0, 0.02]) + rng.standard_t(5, size=300)
    fit = l2.ols(x, y, add_intercept=add_intercept)
    ref = sm.OLS(y, sm.add_constant(x) if add_intercept else x).fit()
    np.testing.assert_allclose(fit.coef, ref.params, rtol=0, atol=1e-8)
    np.testing.assert_allclose(fit.std_err, ref.bse, rtol=0, atol=1e-8)
    np.testing.assert_allclose(fit.t_stats, ref.tvalues, rtol=1e-8, atol=1e-8)
    np.testing.assert_allclose(fit.p_values, ref.pvalues, rtol=1e-8, atol=1e-12)
    assert fit.r_squared == pytest.approx(ref.rsquared, abs=1e-8)
    assert fit.adj_r_squared == pytest.approx(ref.rsquared_adj, abs=1e-8)
    assert fit.sigma2 == pytest.approx(ref.mse_resid, rel=1e-8)
    np.testing.assert_allclose(fit.residuals, ref.resid, atol=1e-8)
    assert fit.dof_resid == int(ref.df_resid) and fit.rank == x.shape[1] + int(add_intercept)
    # the textbook normal equations (X'X)^-1 X'y and numpy's lstsq agree too
    design = sm.add_constant(x) if add_intercept else x
    np.testing.assert_allclose(fit.coef, np.linalg.lstsq(design, y, rcond=None)[0], atol=1e-10)
    np.testing.assert_allclose(fit.coef, np.linalg.solve(design.T @ design, design.T @ y), atol=1e-8)


def test_ols_slope_equals_cov_over_var_and_matches_ols():
    rng = np.random.default_rng(12)
    x = rng.normal(size=500)
    y = 1.0 + 1.7 * x + rng.normal(size=500)
    slope = l2.ols_slope(x, y)
    assert slope == pytest.approx(np.cov(x, y)[0, 1] / np.var(x, ddof=1), rel=1e-12)
    fit = l2.ols(x, y)
    assert slope == pytest.approx(fit.slopes[0], rel=1e-10)
    assert fit.intercept == pytest.approx(y.mean() - slope * x.mean(), rel=1e-10)
    assert fit.r_squared == pytest.approx(l2.correlation(x, y) ** 2, rel=1e-10)
    with pytest.raises(ValueError, match="zero variance"):
        l2.ols_slope(np.ones(10), y[:10])
    with pytest.raises(ValueError, match="shape mismatch"):
        l2.ols_slope(x, y[:-1])


def test_ols_exact_fit_and_constant_target_are_explicit():
    rng = np.random.default_rng(13)
    x = rng.normal(size=(50, 2))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        exact = l2.ols(x, 2.0 + x @ np.array([1.0, 0.0]))
        const = l2.ols(x, np.full(50, 3.0))
    assert exact.sigma2 == 0.0 and exact.r_squared == 1.0
    np.testing.assert_array_equal(exact.std_err, 0.0)
    np.testing.assert_array_equal(exact.t_stats, [np.inf, np.inf, 0.0])
    np.testing.assert_array_equal(exact.p_values, [0.0, 0.0, 1.0])
    np.testing.assert_allclose(const.coef, [3.0, 0.0, 0.0], atol=1e-12)
    assert const.r_squared == 1.0 and np.all(np.isfinite(const.coef))


def test_ols_collinear_design_reports_rank_deficiency():
    rng = np.random.default_rng(14)
    x1 = rng.normal(size=100)
    x = np.column_stack([x1, 2.0 * x1])
    y = 1.0 + 3.0 * x1 + rng.normal(0.0, 0.1, size=100)
    fit = l2.ols(x, y)
    assert fit.rank == 2 and fit.coef.size == 3
    assert np.all(np.isfinite(fit.coef)) and np.all(np.isfinite(fit.std_err))
    # the fitted combination is still identified: b1 + 2 b2 = 3
    assert fit.slopes[0] + 2.0 * fit.slopes[1] == pytest.approx(3.0, abs=0.05)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # statsmodels warns about the rank-deficient design
        ref = sm.OLS(y, sm.add_constant(x)).fit()
    np.testing.assert_allclose(fit.coef, ref.params, atol=1e-8)
    assert fit.condition_number > 1e12


@pytest.mark.parametrize(
    "x, y",
    [
        (np.ones((3, 2)), np.ones(4)),  # row mismatch
        (np.ones((2, 2)), np.ones(2)),  # n <= p
        (np.ones((5, 2, 2)), np.ones(5)),  # bad ndim
        (np.ones((5, 2)), np.ones((5, 1))),  # y not 1-D
        ([1.0, 2.0, np.nan, 4.0], [1.0, 2.0, 3.0, 4.0]),
        ([], []),
    ],
)
def test_ols_validation(x, y):
    with pytest.raises(ValueError):
        l2.ols(x, y)


def test_robust_regression_resists_outliers():
    rng = np.random.default_rng(15)
    x = rng.normal(size=400)
    y = 0.5 + 2.0 * x + rng.normal(0.0, 0.1, size=400)
    y_bad = y.copy()
    bad = np.argsort(x)[-20:]  # corrupt the high-leverage end
    y_bad[bad] -= 25.0
    plain = l2.ols(x, y_bad)
    robust = l2.robust_regression(x, y_bad)
    assert abs(robust.slopes[0] - 2.0) < 0.05
    assert abs(plain.slopes[0] - 2.0) > 10 * abs(robust.slopes[0] - 2.0)
    assert robust.intercept == pytest.approx(0.5, abs=0.05)
    assert robust.converged and robust.n_iter >= 1
    assert robust.weights[bad].max() < 0.1 and np.median(robust.weights) == 1.0
    assert robust.weights.shape == (400,)


def test_robust_regression_matches_statsmodels_and_ols_on_clean_data():
    rng = np.random.default_rng(16)
    x = rng.normal(size=(250, 2))
    y = 1.0 + x @ np.array([0.8, -1.2]) + rng.normal(0.0, 0.5, size=250)
    robust = l2.robust_regression(x, y)
    ref = sm.RLM(y, sm.add_constant(x), M=sm.robust.norms.HuberT()).fit(maxiter=100, tol=1e-8)
    np.testing.assert_allclose(robust.coef, ref.params, atol=1e-10)
    np.testing.assert_allclose(robust.std_err, ref.bse, atol=1e-10)
    np.testing.assert_allclose(robust.t_stats, ref.tvalues, rtol=1e-8)
    assert robust.scale == pytest.approx(ref.scale)
    np.testing.assert_allclose(robust.coef, l2.ols(x, y).coef, atol=0.05)
    no_const = l2.robust_regression(x, y - 1.0, add_intercept=False)
    assert no_const.coef.shape == (2,) and no_const.intercept == 0.0


def test_robust_regression_exact_fit_and_validation():
    x = np.linspace(-1.0, 1.0, 30)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        exact = l2.robust_regression(x, 1.0 + 2.0 * x)
    np.testing.assert_allclose(exact.coef, [1.0, 2.0], atol=1e-12)
    assert exact.scale == 0.0 and exact.converged and exact.n_iter == 0
    np.testing.assert_array_equal(exact.weights, 1.0)
    with pytest.raises(ValueError, match="huber_t"):
        l2.robust_regression(x, x, huber_t=0.0)
    with pytest.raises(ValueError, match="max_iter"):
        l2.robust_regression(x, x, max_iter=0)
    with pytest.raises(ValueError, match="tol"):
        l2.robust_regression(x, x, tol=0.0)
    with pytest.raises(ValueError, match="shape mismatch"):
        l2.robust_regression(x, x[:-1])


def test_robust_regression_reports_non_convergence():
    rng = np.random.default_rng(17)
    x = rng.normal(size=200)
    y = x + rng.standard_t(2, size=200)
    assert l2.robust_regression(x, y, max_iter=1, tol=1e-14).converged is False


def test_rolling_hedge_ratio_matches_per_window_ols():
    rng = np.random.default_rng(18)
    n, window = 300, 60
    x = rng.normal(0.0, 0.01, size=n)
    beta_true = np.where(np.arange(n) < 150, 0.8, 1.6)  # hedge ratio regime shift
    y = 0.0002 + beta_true * x + rng.normal(0.0, 0.002, size=n)
    roll = l2.rolling_hedge_ratio(y, x, window)
    assert roll.beta.shape == roll.alpha.shape == roll.valid.shape == (n,)
    assert not roll.valid[: window - 1].any() and roll.valid[window - 1 :].all()
    assert np.isnan(roll.beta[: window - 1]).all()
    for t in (window - 1, 120, 200, n - 1):
        ref = l2.ols(x[t - window + 1 : t + 1], y[t - window + 1 : t + 1])
        assert roll.beta[t] == pytest.approx(ref.slopes[0], rel=1e-9)
        assert roll.alpha[t] == pytest.approx(ref.intercept, abs=1e-12)
    assert roll.beta[149] == pytest.approx(0.8, abs=0.1)
    assert roll.beta[-1] == pytest.approx(1.6, abs=0.1)


def test_rolling_hedge_ratio_through_origin_and_degenerate_windows():
    rng = np.random.default_rng(19)
    x = rng.normal(size=40)
    y = 3.0 * x
    origin = l2.rolling_hedge_ratio(y, x, 5, add_intercept=False)
    np.testing.assert_allclose(origin.beta[4:], 3.0, rtol=1e-12)
    np.testing.assert_array_equal(origin.alpha[4:], 0.0)
    # a stale (constant) x window has no defined hedge ratio: flagged, not silently NaN
    x_stale = x.copy()
    x_stale[10:20] = 0.37
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        roll = l2.rolling_hedge_ratio(3.0 * x_stale + 1.0, x_stale, 5)
    stale_windows = np.arange(14, 20)
    assert not roll.valid[stale_windows].any()
    assert np.isnan(roll.beta[stale_windows]).all()
    ok = roll.valid
    np.testing.assert_allclose(roll.beta[ok], 3.0, rtol=1e-9)
    np.testing.assert_allclose(roll.alpha[ok], 1.0, rtol=1e-9)
    assert np.all(np.isfinite(roll.beta[ok]))
    full = l2.rolling_hedge_ratio(y, x, 40)
    assert full.valid.sum() == 1 and full.beta[-1] == pytest.approx(3.0)


def test_rolling_hedge_ratio_block_processing_is_seamless(monkeypatch):
    rng = np.random.default_rng(190)
    x = rng.normal(size=500)
    x[200:230] = 1.5  # includes degenerate windows that straddle block boundaries
    y = 0.3 + 1.2 * x + rng.normal(0.0, 0.1, size=500)
    for intercept in (True, False):
        whole = l2.rolling_hedge_ratio(y, x, 20, add_intercept=intercept)
        monkeypatch.setattr(l2, "_ROLLING_BLOCK_ELEMENTS", 20 * 7)  # 7 windows per block
        blocked = l2.rolling_hedge_ratio(y, x, 20, add_intercept=intercept)
        monkeypatch.undo()
        np.testing.assert_array_equal(blocked.beta, whole.beta)
        np.testing.assert_array_equal(blocked.alpha, whole.alpha)
        np.testing.assert_array_equal(blocked.valid, whole.valid)
        assert whole.valid.sum() == (470 if intercept else 481)


@pytest.mark.parametrize(
    "y, x, window, kwargs",
    [
        (np.ones(5), np.ones(4), 2, {}),
        (np.ones(5), np.ones(5), 6, {}),
        (np.ones(5), np.ones(5), 1, {}),
        (np.ones(5), np.ones(5), 0, {"add_intercept": False}),
        (np.ones(5), np.ones(5), 2.5, {}),
        (np.ones((5, 2)), np.ones(5), 2, {}),
        ([1.0, np.nan, 2.0], [1.0, 2.0, 3.0], 2, {}),
    ],
)
def test_rolling_hedge_ratio_validation(y, x, window, kwargs):
    with pytest.raises(ValueError):
        l2.rolling_hedge_ratio(y, x, window, **kwargs)


# --------------------------------------------------------------------------- 2.4
def test_correlation_of_symmetric_x_with_x_squared_is_zero_though_dependent():
    x = np.linspace(-3.0, 3.0, 601)  # exactly symmetric about 0
    y = x**2  # a deterministic function of x
    assert l2.correlation(x, y) == pytest.approx(0.0, abs=1e-12)
    assert l2.correlation(np.abs(x), y) > 0.95  # the dependence is real, just not linear
    rng = np.random.default_rng(20)
    sample = rng.normal(size=200_000)
    assert l2.correlation(sample, sample**2) == pytest.approx(0.0, abs=0.02)


def test_correlation_matches_numpy_and_bounds():
    rng = np.random.default_rng(21)
    x = rng.normal(size=300)
    y = 0.6 * x + rng.normal(size=300)
    assert l2.correlation(x, y) == pytest.approx(np.corrcoef(x, y)[0, 1], rel=1e-12)
    assert l2.correlation(x, y) == pytest.approx(
        np.cov(x, y)[0, 1] / (x.std(ddof=1) * y.std(ddof=1)), rel=1e-12
    )
    assert l2.correlation(x, 3.0 * x + 1.0) == pytest.approx(1.0, abs=1e-12)
    assert l2.correlation(x, -x) == pytest.approx(-1.0, abs=1e-12)
    assert l2.correlation(1e6 * x, 1e-6 * y) == pytest.approx(l2.correlation(x, y), rel=1e-10)


def test_correlation_validation():
    with pytest.raises(ValueError, match="zero variance"):
        l2.correlation([1.0, 1.0, 1.0], [1.0, 2.0, 3.0])
    with pytest.raises(ValueError, match="zero variance"):
        l2.correlation([1.0, 2.0, 3.0], [1e9, 1e9, 1e9])
    with pytest.raises(ValueError, match="shape mismatch"):
        l2.correlation([1.0, 2.0], [1.0, 2.0, 3.0])
    with pytest.raises(ValueError, match="at least 2"):
        l2.correlation([1.0], [2.0])
    with pytest.raises(ValueError):
        l2.correlation([1.0, np.nan], [1.0, 2.0])


def test_r_squared_definition_and_edge_cases():
    rng = np.random.default_rng(22)
    y = rng.normal(size=200)
    pred = y + rng.normal(0.0, 0.3, size=200)
    ss_res = np.sum((y - pred) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    assert l2.r_squared(y, pred) == pytest.approx(1.0 - ss_res / ss_tot, rel=1e-12)
    assert l2.r_squared(y, y) == 1.0
    assert l2.r_squared(y, np.full(200, y.mean())) == pytest.approx(0.0, abs=1e-12)
    assert l2.r_squared(y, -y) < 0.0  # worse than predicting the mean
    # with an intercept, R^2 of the OLS fit equals the squared correlation
    x = rng.normal(size=200)
    target = 2.0 * x + rng.normal(size=200)
    fit = l2.ols(x, target)
    assert l2.r_squared(target, target - fit.residuals) == pytest.approx(fit.r_squared, rel=1e-12)
    # SS_tot = 0 is handled explicitly instead of dividing by zero
    assert l2.r_squared([2.0, 2.0, 2.0], [2.0, 2.0, 2.0]) == 1.0
    assert l2.r_squared([2.0, 2.0, 2.0], [1.0, 2.0, 3.0]) == 0.0
    with pytest.raises(ValueError, match="shape mismatch"):
        l2.r_squared([1.0, 2.0], [1.0])
    with pytest.raises(ValueError):
        l2.r_squared([], [])


@pytest.mark.parametrize("true_lag", [3, -4, 0])
def test_lead_lag_scanner_finds_the_planted_lag(true_lag):
    rng = np.random.default_rng(23)
    n = 2_000
    leader = rng.normal(size=n + 20)
    x = leader[10 : n + 10]
    # y[t] = x[t - true_lag] + noise  <=>  x leads y by true_lag periods
    y = leader[10 - true_lag : n + 10 - true_lag] + rng.normal(0.0, 0.5, size=n)
    res = l2.lead_lag_cross_correlation(x, y, max_lag=8)
    assert res.best_lag == true_lag
    assert res.best_correlation == pytest.approx(1.0 / np.sqrt(1.25), abs=0.05)
    assert res.lags.tolist() == list(range(-8, 9))
    assert res.valid.all()
    np.testing.assert_array_equal(res.n_overlap, n - np.abs(res.lags))
    # swapping the series flips the sign of the lag
    assert l2.lead_lag_cross_correlation(y, x, max_lag=8).best_lag == -true_lag
    # every entry is the plain correlation of the aligned overlap
    k = 2
    assert res.correlations[res.lags == k][0] == pytest.approx(l2.correlation(x[:-k], y[k:]), rel=1e-12)
    assert res.correlations[res.lags == -k][0] == pytest.approx(l2.correlation(x[k:], y[:-k]), rel=1e-12)


def test_lead_lag_scanner_picks_negative_correlation_and_breaks_ties_toward_zero_lag():
    rng = np.random.default_rng(24)
    x = rng.normal(size=1_000)
    y = np.empty_like(x)
    y[2:] = -x[:-2]
    y[:2] = rng.normal(size=2)
    res = l2.lead_lag_cross_correlation(x, y + rng.normal(0.0, 0.2, size=1_000), max_lag=5)
    assert res.best_lag == 2 and res.best_correlation < -0.9
    # a perfectly periodic pair is equally correlated at lags -4, 0, 4: prefer lag 0
    t = np.arange(400)
    wave = np.sin(2.0 * np.pi * t / 4.0)
    tie = l2.lead_lag_cross_correlation(wave, wave, max_lag=4)
    assert tie.best_lag == 0 and tie.best_correlation == pytest.approx(1.0)
    zero_lag_only = l2.lead_lag_cross_correlation(x, 2.0 * x, max_lag=0)
    assert zero_lag_only.best_lag == 0 and zero_lag_only.lags.tolist() == [0]


def test_lead_lag_scanner_flags_undefined_lags():
    x = np.array([0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 0.5, 3.0])
    y = np.array([0.3, 1.0, 0.2, 0.9, 0.4, 0.8, 0.1, 0.7])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        res = l2.lead_lag_cross_correlation(x, y, max_lag=4)
    # at lag +4 the overlap of x is x[:4], which is constant -> undefined, flagged
    idx = int(np.flatnonzero(res.lags == 4)[0])
    assert not res.valid[idx] and np.isnan(res.correlations[idx])
    assert res.valid.sum() == res.lags.size - 1
    assert np.all(np.isfinite(res.correlations[res.valid]))
    assert res.valid[res.lags == res.best_lag][0]
    with pytest.raises(ValueError, match="undefined at every lag"):
        l2.lead_lag_cross_correlation(np.ones(10), y[[0, 1, 2, 3, 4, 5, 6, 7, 0, 1]], max_lag=2)


@pytest.mark.parametrize(
    "x, y, max_lag, kwargs",
    [
        (np.arange(10.0), np.arange(9.0), 2, {}),
        (np.arange(10.0), np.arange(10.0), -1, {}),
        (np.arange(10.0), np.arange(10.0), 1.5, {}),
        (np.arange(10.0), np.arange(10.0), 8, {}),  # overlap of 2 < 3
        (np.arange(10.0), np.arange(10.0), 2, {"min_overlap": 2}),
        (np.arange(10.0), np.arange(10.0), 2, {"min_overlap": 9}),
        (np.ones((5, 2)), np.ones((5, 2)), 1, {}),
    ],
)
def test_lead_lag_scanner_validation(x, y, max_lag, kwargs):
    with pytest.raises(ValueError):
        l2.lead_lag_cross_correlation(x, y, max_lag, **kwargs)


# --------------------------------------------------------------------------- 2.5
def test_twenty_tests_at_five_percent_gives_64_15_percent():
    assert l2.family_wise_error_rate(0.05, 20) == pytest.approx(0.6415, abs=5e-5)
    assert l2.family_wise_error_rate(0.05, 20) == pytest.approx(1.0 - 0.95**20, rel=1e-12)


def test_family_wise_error_rate_edges_and_vectorisation():
    assert l2.family_wise_error_rate(0.05, 1) == pytest.approx(0.05)
    assert l2.family_wise_error_rate(0.05, 0) == 0.0
    assert l2.family_wise_error_rate(0.0, 100) == 0.0
    assert l2.family_wise_error_rate(1.0, 3) == 1.0
    assert l2.family_wise_error_rate(1.0, 0) == 0.0
    # accurate for tiny alpha where 1 - (1 - alpha)^k would cancel
    assert l2.family_wise_error_rate(1e-18, 10) == pytest.approx(1e-17, rel=1e-10)
    k = np.array([1, 5, 20, 100])
    np.testing.assert_allclose(l2.family_wise_error_rate(0.05, k), 1.0 - 0.95**k, rtol=1e-12)
    assert np.all(np.diff(l2.family_wise_error_rate(0.05, k)) > 0)
    for alpha, n in ((-0.1, 5), (1.1, 5), (0.05, -1), (0.05, 2.5)):
        with pytest.raises(ValueError):
            l2.family_wise_error_rate(alpha, n)


def test_family_wise_error_rate_simulation():
    rng = np.random.default_rng(30)
    pvals = rng.uniform(size=(40_000, 20))  # 20 true nulls per family
    any_false_positive = (pvals < 0.05).any(axis=1).mean()
    assert any_false_positive == pytest.approx(l2.family_wise_error_rate(0.05, 20), abs=0.01)
    # Bonferroni and Holm pull the family-wise rate back under alpha
    fwer_bonf = np.mean([l2.bonferroni(p).reject.any() for p in pvals[:4_000]])
    fwer_holm = np.mean([l2.holm_bonferroni(p).reject.any() for p in pvals[:4_000]])
    assert fwer_bonf <= 0.065 and fwer_holm <= 0.065


_METHODS = [
    ("bonferroni", "bonferroni", l2.bonferroni),
    ("holm", "holm", l2.holm_bonferroni),
    ("fdr_bh", "fdr_bh", l2.benjamini_hochberg),
    ("fdr_by", "fdr_by", l2.benjamini_yekutieli),
]


@pytest.mark.parametrize("name, sm_name, func", _METHODS)
@pytest.mark.parametrize("alpha", [0.01, 0.05, 0.2])
def test_multiple_testing_matches_statsmodels(name, sm_name, func, alpha):
    rng = np.random.default_rng(31)
    for m in (1, 2, 7, 50, 400):
        signal = rng.uniform(0.0, 0.01, size=m // 4)
        pvals = rng.permutation(np.concatenate([signal, rng.uniform(size=m - signal.size)]))
        res = func(pvals, alpha)
        reject_ref, adj_ref, _, _ = multipletests(pvals, alpha=alpha, method=sm_name)
        np.testing.assert_allclose(res.pvalues_adjusted, adj_ref, rtol=1e-12, atol=1e-15)
        np.testing.assert_array_equal(res.reject, reject_ref)
        assert res.method == name and res.alpha == alpha
        assert res.n_rejected == int(reject_ref.sum())
        via_dispatch = l2.adjust_pvalues(pvals, name, alpha)
        np.testing.assert_array_equal(via_dispatch.pvalues_adjusted, res.pvalues_adjusted)


def test_multiple_testing_hand_worked_example():
    pvals = np.array([0.01, 0.04, 0.03, 0.005])
    np.testing.assert_allclose(l2.bonferroni(pvals).pvalues_adjusted, [0.04, 0.16, 0.12, 0.02])
    np.testing.assert_allclose(l2.holm_bonferroni(pvals).pvalues_adjusted, [0.03, 0.06, 0.06, 0.02])
    np.testing.assert_allclose(l2.benjamini_hochberg(pvals).pvalues_adjusted, [0.02, 0.04, 0.04, 0.02])
    c4 = 1.0 + 1.0 / 2.0 + 1.0 / 3.0 + 1.0 / 4.0
    np.testing.assert_allclose(
        l2.benjamini_yekutieli(pvals).pvalues_adjusted, np.array([0.02, 0.04, 0.04, 0.02]) * c4
    )
    np.testing.assert_array_equal(l2.bonferroni(pvals).reject, [True, False, False, True])
    np.testing.assert_array_equal(l2.holm_bonferroni(pvals).reject, [True, False, False, True])
    np.testing.assert_array_equal(l2.benjamini_hochberg(pvals).reject, [True, True, True, True])


def test_multiple_testing_ordering_of_power_and_invariants():
    rng = np.random.default_rng(32)
    pvals = np.concatenate([rng.uniform(0.0, 0.002, size=30), rng.uniform(size=170)])
    bonf = l2.bonferroni(pvals)
    holm = l2.holm_bonferroni(pvals)
    bh = l2.benjamini_hochberg(pvals)
    by = l2.benjamini_yekutieli(pvals)
    # adjusted p-values never fall below the raw ones and stay in [0, 1]
    for res in (bonf, holm, bh, by):
        assert np.all(res.pvalues_adjusted >= pvals - 1e-15)
        assert np.all((res.pvalues_adjusted >= 0.0) & (res.pvalues_adjusted <= 1.0))
        # monotone: a smaller raw p-value never gets a larger adjusted one
        order = np.argsort(pvals)
        assert np.all(np.diff(res.pvalues_adjusted[order]) >= -1e-15)
    # uniformly: holm <= bonferroni, bh <= holm, bh <= by
    assert np.all(holm.pvalues_adjusted <= bonf.pvalues_adjusted + 1e-15)
    assert np.all(bh.pvalues_adjusted <= holm.pvalues_adjusted + 1e-15)
    assert np.all(bh.pvalues_adjusted <= by.pvalues_adjusted + 1e-15)
    assert bonf.n_rejected <= holm.n_rejected <= bh.n_rejected
    assert by.n_rejected <= bh.n_rejected
    # order of the output follows the order of the input
    perm = rng.permutation(pvals.size)
    np.testing.assert_allclose(l2.benjamini_hochberg(pvals[perm]).pvalues_adjusted, bh.pvalues_adjusted[perm])


def test_benjamini_hochberg_controls_false_discovery_rate():
    rng = np.random.default_rng(33)
    fdp = []
    for _ in range(300):
        z_null = rng.normal(size=180)
        z_alt = rng.normal(3.5, 1.0, size=20)
        pvals = 2.0 * stats.norm.sf(np.abs(np.concatenate([z_null, z_alt])))
        reject = l2.benjamini_hochberg(pvals, 0.10).reject
        fdp.append(reject[:180].sum() / max(reject.sum(), 1))
    assert np.mean(fdp) <= 0.10 + 0.02  # FDR <= alpha * m0 / m = 0.09


def test_multiple_testing_empty_single_and_ties():
    for func in (l2.bonferroni, l2.holm_bonferroni, l2.benjamini_hochberg, l2.benjamini_yekutieli):
        empty = func([])
        assert empty.reject.shape == (0,) and empty.pvalues_adjusted.shape == (0,)
        assert empty.reject.dtype == bool and empty.n_rejected == 0
        single = func([0.03])
        assert single.pvalues_adjusted[0] == pytest.approx(0.03) and single.reject[0]
        tied = func([0.02, 0.02, 0.02])
        assert np.all(tied.pvalues_adjusted == tied.pvalues_adjusted[0])
        ones = func([1.0, 1.0])
        np.testing.assert_array_equal(ones.pvalues_adjusted, [1.0, 1.0])
        zeros = func([0.0, 0.5])
        assert zeros.pvalues_adjusted[0] == 0.0 and zeros.reject[0]


@pytest.mark.parametrize(
    "pvals, alpha",
    [
        ([0.1, 1.2], 0.05),
        ([0.1, -0.2], 0.05),
        ([0.1, np.nan], 0.05),
        ([[0.1, 0.2]], 0.05),
        ([0.1, 0.2], 0.0),
        ([0.1, 0.2], 1.0),
    ],
)
def test_multiple_testing_validation(pvals, alpha):
    for func in (l2.bonferroni, l2.holm_bonferroni, l2.benjamini_hochberg, l2.benjamini_yekutieli):
        with pytest.raises(ValueError):
            func(pvals, alpha)


def test_adjust_pvalues_rejects_unknown_method():
    with pytest.raises(ValueError, match="unknown method"):
        l2.adjust_pvalues([0.1, 0.2], "sidak")  # type: ignore[arg-type]
    default = l2.adjust_pvalues([0.01, 0.5])
    assert default.method == "fdr_bh"


@pytest.mark.parametrize(
    "x_unit, y_unit", [(1.0, 1.0), (1e-300, 1.0), (1.0, 1e-300), (1e150, 1e150), (1e-9, 1e150)]
)
def test_statistics_are_exact_in_extreme_units(x_unit, y_unit):
    # Squares of 1e-300 underflow to 0 and squares of 1e150 overflow; none of that may leak out.
    rng = np.random.default_rng(35)
    x = rng.normal(size=120)
    y = 0.4 + 1.5 * x + rng.normal(0.0, 0.5, size=120)
    base_rho = l2.correlation(x, y - 0.4)
    base_fit = l2.ols(x, y - 0.4, add_intercept=False)
    xs, ys = x_unit * x, y_unit * (y - 0.4)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert l2.correlation(xs, ys) == pytest.approx(base_rho, rel=1e-10)
        assert l2.lead_lag_cross_correlation(xs, ys, 3).best_lag == 0
        assert l2.ols_slope(xs, ys) == pytest.approx(l2.ols_slope(x, y) * y_unit / x_unit, rel=1e-10)
        fit = l2.ols(xs, ys, add_intercept=False)
        robust = l2.robust_regression(xs, ys, add_intercept=False)
        roll = l2.rolling_hedge_ratio(ys, xs, 30)
        sharpe = l2.sharpe_ratio(ys)
        est = l2.mean_confidence_interval(ys)
        r2 = l2.r_squared(ys, xs * fit.coef[0])
    np.testing.assert_allclose(fit.coef, base_fit.coef * y_unit / x_unit, rtol=1e-10)
    np.testing.assert_allclose(fit.t_stats, base_fit.t_stats, rtol=1e-8)
    assert fit.r_squared == pytest.approx(base_fit.r_squared, rel=1e-10) and fit.rank == 1
    assert r2 == pytest.approx(l2.r_squared(y - 0.4, x * base_fit.coef[0]), rel=1e-8)  # scale-free
    assert robust.coef[0] == pytest.approx(
        l2.robust_regression(x, y - 0.4, add_intercept=False).coef[0] * y_unit / x_unit, rel=1e-8
    )
    assert roll.valid[29:].all()
    np.testing.assert_allclose(
        roll.beta[29:], l2.rolling_hedge_ratio(y, x, 30).beta[29:] * y_unit / x_unit, rtol=1e-9
    )
    assert sharpe == pytest.approx(l2.sharpe_ratio(y - 0.4), rel=1e-10)
    assert est.t_stat == pytest.approx(l2.mean_confidence_interval(y - 0.4).t_stat, rel=1e-10)
    assert est.std > 0.0 and np.isfinite(est.std)


def test_unrepresentable_results_raise_instead_of_returning_inf():
    rng = np.random.default_rng(36)
    x = rng.normal(size=40)
    y = rng.normal(size=40)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(ValueError, match="overflow"):
            l2.ols_slope(1e-300 * x, 1e150 * y)  # the slope itself would be ~1e450
        with pytest.raises(ValueError, match="overflow"):
            l2.ols(1e-300 * x, 1e150 * y, add_intercept=False)
        with pytest.raises(ValueError, match="overflow"):
            l2.rolling_hedge_ratio(1e150 * y, 1e-300 * x, 10)
        # the correlation is still perfectly well defined
        assert l2.correlation(1e-300 * x, 1e150 * y) == pytest.approx(l2.correlation(x, y), rel=1e-10)


def test_robust_regression_reports_rank_deficiency_without_warning():
    rng = np.random.default_rng(37)
    x1 = rng.normal(size=80)
    y = 1.0 + 2.0 * x1 + rng.standard_t(3, size=80)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        collinear = l2.robust_regression(np.column_stack([x1, 3.0 * x1]), y)
        healthy = l2.robust_regression(x1, y)
    assert collinear.rank == 2 and collinear.coef.size == 3
    assert healthy.rank == 2 and healthy.coef.size == 2
    assert collinear.slopes[0] + 3.0 * collinear.slopes[1] == pytest.approx(healthy.slopes[0], rel=1e-6)


def test_inputs_are_not_mutated():
    rng = np.random.default_rng(34)
    x = rng.normal(size=(40, 2))
    y = rng.normal(size=40)
    pvals = rng.uniform(size=15)
    snapshots = [a.copy() for a in (x, y, pvals)]
    l2.ols(x, y)
    l2.robust_regression(x, y)
    l2.rolling_hedge_ratio(y, x[:, 0], 10)
    l2.lead_lag_cross_correlation(x[:, 0], y, 3)
    for func in (l2.bonferroni, l2.holm_bonferroni, l2.benjamini_hochberg, l2.benjamini_yekutieli):
        func(pvals)
    for original, snap in zip((x, y, pvals), snapshots, strict=True):
        np.testing.assert_array_equal(original, snap)
