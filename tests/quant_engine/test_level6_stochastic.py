import math

import numpy as np
import pytest
from scipy.stats import norm

from valedia_trader.quant_engine import level6_stochastic as L6


def bs_call(S, K, r, sigma, T):
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d1 - sigma * math.sqrt(T))


def test_formula_registry_complete():
    assert set(L6.FORMULAS) == {"6.1", "6.2", "6.3", "6.4", "6.5"}
    for names in L6.FORMULAS.values():
        for n in names:
            assert callable(getattr(L6, n))


# 6.1
def test_hundred_fair_steps_have_sd_ten():
    assert L6.random_walk_sd(100) == pytest.approx(10.0)
    paths = L6.random_walk_paths(100, 40000, np.random.default_rng(0))
    assert paths.shape == (40000, 101) and np.all(paths[:, 0] == 0)
    assert paths[:, -1].std() == pytest.approx(10.0, rel=0.02)
    assert np.abs(paths[:, -1]).mean() == pytest.approx(L6.random_walk_expected_abs_distance(100), rel=0.02)
    assert L6.random_walk_expected_abs_distance(2) == pytest.approx(1.0)
    assert L6.random_walk_expected_abs_distance(1) == pytest.approx(1.0)
    assert L6.random_walk_expected_abs_distance(10000) == pytest.approx(math.sqrt(2 * 10000 / math.pi), rel=1e-3)


def test_random_walk_validation():
    with pytest.raises(ValueError):
        L6.random_walk_paths(0, 10, np.random.default_rng(0))
    with pytest.raises(ValueError):
        L6.random_walk_paths(10, 10, np.random.default_rng(0), p_up=1.5)


# 6.2
def test_brownian_increment_variance_and_quadratic_variation():
    w = L6.brownian_paths(5000, 500, 2.0, np.random.default_rng(1))
    assert w[:, -1].var() == pytest.approx(2.0, rel=0.06)
    assert (w[:, 300] - w[:, 100]).var() == pytest.approx(0.8, rel=0.06)   # t - s = 200 * 0.004
    assert L6.quadratic_variation(w).mean() == pytest.approx(2.0, rel=0.01)


def test_correlated_increments_match_target_matrix():
    corr = np.array([[1.0, 0.6, -0.3], [0.6, 1.0, 0.2], [-0.3, 0.2, 1.0]])
    dw = L6.correlated_brownian_increments(corr, 2000, 250, 1 / 250, np.random.default_rng(2))
    flat = dw.reshape(-1, 3)
    assert np.corrcoef(flat.T) == pytest.approx(corr, abs=0.01)
    assert flat.var(axis=0) == pytest.approx(np.full(3, 1 / 250), rel=0.02)


def test_non_psd_correlation_is_repaired():
    bad = np.array([[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]])
    assert np.linalg.eigvalsh(bad).min() < 0
    fixed = L6.nearest_psd_correlation(bad)
    assert np.linalg.eigvalsh(fixed).min() > 0 and np.diag(fixed) == pytest.approx(np.ones(3))
    L6.correlated_brownian_increments(bad, 10, 10, 0.01, np.random.default_rng(0))
    with pytest.raises(ValueError):
        L6.nearest_psd_correlation(np.ones((2, 3)))


# 6.3
def test_gbm_mean_and_exact_paths():
    rng = np.random.default_rng(3)
    st = L6.gbm_terminal(100.0, 0.08, 0.2, 1.0, 400000, rng, antithetic=True)
    assert st.mean() == pytest.approx(100 * math.exp(0.08), rel=2e-3)
    paths = L6.gbm_paths(100.0, 0.08, 0.2, 1.0, 50, 100000, rng)
    assert paths.shape == (100000, 51) and np.all(paths > 0)
    assert paths[:, -1].mean() == pytest.approx(100 * math.exp(0.08), rel=5e-3)
    assert np.log(paths[:, -1] / 100).std() == pytest.approx(0.2, rel=0.01)


def test_jump_diffusion_keeps_expected_growth():
    rng = np.random.default_rng(4)
    paths = L6.merton_jump_paths(100.0, 0.05, 0.15, 1.0, 50, 300000, rng, jump_intensity=1.5,
                                 jump_mean=-0.05, jump_std=0.10)
    assert paths[:, -1].mean() == pytest.approx(100 * math.exp(0.05), rel=5e-3)
    plain = L6.gbm_paths(100.0, 0.05, 0.15, 1.0, 50, 300000, rng)
    assert np.log(paths[:, -1]).std() > np.log(plain[:, -1]).std()   # jumps add variance


def test_mc_option_price_and_variance_reduction():
    exact = bs_call(100, 100, 0.05, 0.2, 1.0)
    assert exact == pytest.approx(10.4506, abs=1e-4)
    n = 200000
    plain = L6.mc_european_option(100, 100, 0.05, 0.2, 1.0, n, np.random.default_rng(5))
    anti = L6.mc_european_option(100, 100, 0.05, 0.2, 1.0, n, np.random.default_rng(5), antithetic=True)
    cv = L6.mc_european_option(100, 100, 0.05, 0.2, 1.0, n, np.random.default_rng(5), control_variate=True)
    both = L6.mc_european_option(100, 100, 0.05, 0.2, 1.0, n, np.random.default_rng(5), antithetic=True,
                                 control_variate=True)
    for res in (plain, anti, cv, both):
        assert abs(res.price - exact) < 3 * res.std_error
    assert anti.std_error < plain.std_error and cv.std_error < plain.std_error
    assert both.std_error < plain.std_error
    put = L6.mc_european_option(100, 100, 0.05, 0.2, 1.0, n, np.random.default_rng(6), option="put",
                                control_variate=True)
    assert abs(put.price - (exact - 100 + 100 * math.exp(-0.05))) < 3 * put.std_error


# 6.4
def test_ito_on_log_price():
    mu, sigma = 0.10, 0.25
    num = L6.ito_coefficients(lambda s, t: math.log(s), 120.0, 0.5, mu, sigma)
    assert num.drift == pytest.approx(mu - 0.5 * sigma**2, abs=1e-6)
    assert num.diffusion == pytest.approx(sigma, abs=1e-8)
    assert L6.ito_log_price(mu, sigma) == L6.ItoResult(mu - 0.5 * sigma**2, sigma)
    exact = L6.ito_coefficients(lambda s, t: s**2, 3.0, 0.0, mu, sigma, f_t=lambda s, t: 0.0,
                                f_S=lambda s, t: 2 * s, f_SS=lambda s, t: 2.0)
    assert exact.drift == pytest.approx((2 * mu + sigma**2) * 9.0)
    assert exact.diffusion == pytest.approx(2 * sigma * 9.0)


def test_binary_contract_is_a_discounted_martingale_under_risk_neutral_drift():
    S, t, K, r, sigma, T = 100.0, 0.25, 105.0, 0.04, 0.3, 1.0
    value = L6.binary_call_value(S, t, K, r, sigma, T)
    assert 0 < value < 1
    dyn = L6.binary_contract_dynamics(S, t, K, r, sigma, T, mu=r)
    assert dyn.drift == pytest.approx(r * value, abs=1e-4)     # drift equals r * V when mu = r
    assert dyn.diffusion > 0
    with pytest.raises(ValueError):
        L6.binary_call_value(S, 1.0, K, r, sigma, T)


# 6.5
def test_gamblers_ruin_thirty_of_hundred():
    assert L6.gambler_ruin_win_probability(30, 100) == pytest.approx(0.30)
    assert L6.gambler_ruin_expected_duration(30, 100) == pytest.approx(2100)
    p = 0.48
    ratio = 0.52 / 0.48
    assert L6.gambler_ruin_win_probability(30, 100, p) == pytest.approx((1 - ratio**30) / (1 - ratio**100))
    assert L6.gambler_ruin_win_probability(30, 100, p) < 0.01     # a small edge against you is ruinous
    rng = np.random.default_rng(8)
    wins, total_steps, trials = 0, 0, 4000
    for _ in range(trials):
        x, steps = 3, 0
        while 0 < x < 10:
            x += 1 if rng.random() < 0.45 else -1
            steps += 1
        wins += x == 10
        total_steps += steps
    assert wins / trials == pytest.approx(L6.gambler_ruin_win_probability(3, 10, 0.45), abs=0.02)
    assert total_steps / trials == pytest.approx(L6.gambler_ruin_expected_duration(3, 10, 0.45), rel=0.05)
    with pytest.raises(ValueError):
        L6.gambler_ruin_win_probability(100, 30)


def test_first_passage_closed_form_matches_monte_carlo():
    assert L6.brownian_hit_upper_first_probability(0.3, 0.0, 1.0) == pytest.approx(0.3)
    exact = L6.brownian_hit_upper_first_probability(0.0, -1.0, 1.0, mu=0.5, sigma=1.0)
    assert exact == pytest.approx(1 / (1 + math.exp(-1.0)))
    est, se = L6.mc_first_passage_probability(0.0, -1.0, 1.0, 0.5, 1.0, 1e-3, 20000, np.random.default_rng(9))
    assert abs(est - exact) < 4 * se + 0.01     # small allowance for discrete-monitoring bias
    tp = L6.gbm_take_profit_before_stop_probability(100, 110, 95, mu=0.0, sigma=0.2)
    assert 0.3 < tp < 0.34                       # slightly below 1/3 because of the -sigma^2/2 drift
    with pytest.raises(ValueError):
        L6.gbm_take_profit_before_stop_probability(100, 90, 95, 0.0, 0.2)


def test_martingale_check_separates_fair_from_biased_and_predictable():
    rng = np.random.default_rng(10)
    fair = rng.standard_normal(5000)
    assert L6.martingale_check(fair).is_martingale
    assert not L6.martingale_check(fair + 0.2).is_martingale
    momentum = np.zeros(5000)
    for t in range(1, 5000):
        momentum[t] = 0.4 * momentum[t - 1] + rng.standard_normal()
    assert not L6.martingale_check(momentum).is_martingale
    signal = rng.standard_normal(5000)
    assert not L6.martingale_check(0.3 * signal + rng.standard_normal(5000), conditioning=signal).is_martingale
    with pytest.raises(ValueError):
        L6.martingale_check([0.1, 0.2])
