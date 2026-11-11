import math

import numpy as np
import pytest
from arch import arch_model

from valedia_trader.quant_engine import level5_timeseries as L5


def ar1(phi, n, rng, sigma=1.0):
    x = np.zeros(n)
    e = rng.standard_normal(n) * sigma
    for t in range(1, n):
        x[t] = phi * x[t - 1] + e[t]
    return x


def simulate_garch(omega, alpha, beta, n, rng):
    r, v = np.zeros(n), np.zeros(n)
    v[0] = omega / (1 - alpha - beta)
    r[0] = math.sqrt(v[0]) * rng.standard_normal()
    for t in range(1, n):
        v[t] = omega + alpha * r[t - 1] ** 2 + beta * v[t - 1]
        r[t] = math.sqrt(v[t]) * rng.standard_normal()
    return r, v


def test_formula_registry_complete():
    assert set(L5.FORMULAS) == {"5.1", "5.2", "5.3", "5.4", "5.5"}
    for names in L5.FORMULAS.values():
        for n in names:
            assert callable(getattr(L5, n))


# 5.1
def test_log_returns_add_across_horizons_and_round_trip():
    prices = np.array([100.0, 101.0, 99.0, 102.0, 104.0, 103.0, 105.0])
    r = L5.log_returns(prices)
    assert r.sum() == pytest.approx(math.log(105 / 100))
    assert L5.aggregate_log_returns(r, 3) == pytest.approx([math.log(102 / 100), math.log(105 / 102)])
    assert L5.log_to_simple(r) == pytest.approx(L5.simple_returns(prices))
    assert L5.simple_to_log(L5.simple_returns(prices)) == pytest.approx(r)


def test_daily_one_percent_vol_is_15_87_annual():
    assert L5.annualize_volatility(0.01) == pytest.approx(0.1587, abs=1e-4)


def test_returns_validation():
    with pytest.raises(ValueError):
        L5.log_returns([100.0, 0.0, 101.0])
    with pytest.raises(ValueError):
        L5.log_returns([100.0])
    with pytest.raises(ValueError):
        L5.simple_to_log([-1.0])
    with pytest.raises(ValueError):
        L5.log_returns([100.0, float("nan")])


# 5.2
def test_serial_dependence_classification():
    rng = np.random.default_rng(7)
    assert L5.classify_serial_dependence(ar1(0.3, 2000, rng)).label == "momentum"
    assert L5.classify_serial_dependence(ar1(-0.3, 2000, rng)).label == "mean_reversion"
    assert L5.classify_serial_dependence(rng.standard_normal(2000)).label == "noise"


def test_acf_matches_ar1_theory_and_ljung_box():
    rng = np.random.default_rng(1)
    x = ar1(0.5, 20000, rng)
    rho = L5.autocorrelation(x, 3)
    assert rho[0] == pytest.approx(1.0)
    assert rho[1] == pytest.approx(0.5, abs=0.03)
    assert rho[2] == pytest.approx(0.25, abs=0.03)
    assert L5.partial_autocorrelation(x, 3)[2] == pytest.approx(0.0, abs=0.03)
    assert L5.ljung_box(x, 5)[1] < 1e-6
    with pytest.raises(ValueError):
        L5.autocorrelation(np.ones(50))


# 5.3
def test_random_walk_is_unit_root_and_its_difference_is_i0():
    rng = np.random.default_rng(3)
    walk = np.cumsum(rng.standard_normal(1500))
    assert L5.stationarity_verdict(walk).is_stationary is False
    v = L5.stationarity_verdict(np.diff(walk))
    assert v.is_stationary and v.label == "I(0)"
    with pytest.raises(ValueError):
        L5.adf_test(np.ones(100))


# 5.4
def test_half_life_of_lambda_minus_0_05_is_13_86_days():
    assert L5.half_life(0.05) == pytest.approx(13.86, abs=0.01)
    assert L5.half_life(-0.1) == math.inf


def test_ou_fit_recovers_parameters():
    rng = np.random.default_rng(11)
    theta, mu, sigma, n = 0.05, 2.0, 0.3, 20000
    s = np.empty(n)
    s[0] = mu
    for t in range(1, n):
        s[t] = s[t - 1] - theta * (s[t - 1] - mu) + sigma * rng.standard_normal()
    fit = L5.fit_ou(s)
    assert fit.theta == pytest.approx(theta, rel=0.2)
    assert fit.mu == pytest.approx(mu, abs=0.3)
    assert fit.sigma == pytest.approx(sigma, rel=0.05)
    assert fit.half_life == pytest.approx(math.log(2) / fit.theta)


def test_cointegration_detected_only_for_related_pair():
    rng = np.random.default_rng(5)
    x = 50 + np.cumsum(rng.standard_normal(1500))
    y = 3.0 + 1.5 * x + ar1(0.7, 1500, rng, 0.5)
    res = L5.engle_granger(y, x)
    assert res.cointegrated and res.beta == pytest.approx(1.5, abs=0.05)
    assert L5.stationarity_verdict(res.spread).is_stationary
    other = np.cumsum(rng.standard_normal(1500))
    assert not L5.engle_granger(other, x).cointegrated
    assert L5.johansen_rank(np.column_stack([y, x])).rank == 1
    assert L5.johansen_rank(np.column_stack([other, x])).rank == 0


def test_rolling_zscore_and_signals_are_causal():
    rng = np.random.default_rng(9)
    s = ar1(0.9, 600, rng)
    z = L5.rolling_zscore(s, 50)
    assert np.all(np.isnan(z[:49])) and np.isfinite(z[49:]).all()
    assert z[200] == pytest.approx((s[200] - s[151:201].mean()) / s[151:201].std(ddof=1))
    pos = L5.zscore_signals(z)
    tampered = s.copy()
    tampered[400:] += 25.0                      # change the future only
    pos2 = L5.zscore_signals(L5.rolling_zscore(tampered, 50))
    assert np.array_equal(pos[:400], pos2[:400])  # the past must not change


def test_zscore_signal_state_machine():
    z = [0.0, 2.5, 1.0, 0.4, -2.2, -1.0, -0.3, 2.1, 4.5, 3.0, np.nan]
    assert L5.zscore_signals(z).tolist() == [0, -1, -1, 0, 1, 1, 0, -1, 0, -1, 0]
    with pytest.raises(ValueError):
        L5.zscore_signals(z, entry_z=1.0, exit_z=2.0)


# 5.5
def test_garch_fit_recovers_simulated_parameters():
    rng = np.random.default_rng(21)
    omega, alpha, beta = 2e-6, 0.08, 0.90
    r, _ = simulate_garch(omega, alpha, beta, 12000, rng)
    fit = L5.fit_garch(r, "garch", horizon=5, mean="Zero")
    assert fit.alpha == pytest.approx(alpha, abs=0.025)
    assert fit.beta == pytest.approx(beta, abs=0.03)
    assert fit.long_run_variance == pytest.approx(fit.omega / (1 - fit.alpha - fit.beta))
    assert fit.long_run_variance == pytest.approx(omega / (1 - alpha - beta), rel=0.3)
    assert fit.forecast_variance.shape == (5,) and np.all(fit.forecast_variance > 0)
    assert fit.conditional_volatility.shape == r.shape


def test_recursive_filter_matches_arch_conditional_variance():
    rng = np.random.default_rng(22)
    r, _ = simulate_garch(2e-6, 0.08, 0.90, 3000, rng)
    res = arch_model(r * 100, mean="Zero", vol="GARCH", p=1, q=1, rescale=False).fit(disp="off")
    w, a, b = res.params["omega"], res.params["alpha[1]"], res.params["beta[1]"]
    ours = L5.garch11_filter(r * 100, w, a, b, initial_variance=res.conditional_volatility[0] ** 2)
    assert ours == pytest.approx(res.conditional_volatility**2, rel=1e-8)
    assert L5.garch11_update(ours[9], r[9] * 100, w, a, b) == pytest.approx(ours[10])


def test_gjr_and_egarch_variants_fit():
    rng = np.random.default_rng(23)
    r, _ = simulate_garch(2e-6, 0.08, 0.90, 4000, rng)
    gjr = L5.fit_garch(r, "gjr", horizon=3)
    eg = L5.fit_garch(r, "egarch", horizon=3)
    assert gjr.persistence < 1 and math.isfinite(gjr.long_run_variance)
    assert eg.forecast_variance.shape == (3,) and np.all(eg.forecast_variance > 0)
    assert eg.long_run_variance == pytest.approx(np.var(r), rel=0.5)
    with pytest.raises(ValueError):
        L5.fit_garch(r, "tgarch")


def test_long_run_variance_and_sizing_helpers():
    assert L5.garch_long_run_variance(1e-6, 0.05, 0.90) == pytest.approx(2e-5)
    assert L5.garch_long_run_variance(1e-6, 0.2, 0.85) == math.inf
    assert L5.vol_scaled_stop(100.0, 0.02, multiple=2.0, horizon=4.0) == pytest.approx(8.0)
    assert L5.vol_target_scale(0.20, 0.10) == pytest.approx(0.5)
    assert L5.vol_target_scale(0.05, 0.10, max_leverage=1.5) == 1.5
    assert L5.vol_target_scale(float("nan"), 0.10) == 0.0
    assert L5.vol_target_scale(0.0, 0.10) == 0.0
