import math

import numpy as np
import pytest

from valedia_trader.quant_engine import level8_portfolio_risk as L8


def test_formula_registry_complete():
    assert set(L8.FORMULAS) == {"8.1", "8.2", "8.3", "8.4", "8.5"}
    for names in L8.FORMULAS.values():
        for n in names:
            assert callable(getattr(L8, n))


# 8.1
def test_capm_recovers_beta_and_alpha_and_hedge_removes_market():
    rng = np.random.default_rng(0)
    mkt = 0.0004 + 0.01 * rng.standard_normal(5000)
    asset = 0.0003 + 1.4 * mkt + 0.005 * rng.standard_normal(5000)
    res = L8.capm_beta(asset, mkt)
    assert res.beta == pytest.approx(1.4, abs=0.02)
    assert res.alpha == pytest.approx(0.0003, abs=2e-4)
    assert res.alpha_t > 2 and 0.85 < res.r_squared < 0.95
    hedged = L8.beta_hedged_returns(asset, mkt)
    assert abs(np.corrcoef(hedged, mkt)[0, 1]) < 1e-10
    assert hedged.mean() == pytest.approx(res.alpha)
    with pytest.raises(ValueError):
        L8.capm_beta(asset, np.zeros(5000))


# 8.2
def test_factor_model_loadings_neutralization_and_risk_split():
    rng = np.random.default_rng(1)
    f = 0.01 * rng.standard_normal((3000, 3))
    true_b = np.array([[1.0, 0.5, -0.2], [0.8, -0.3, 0.6], [1.2, 0.0, 0.1], [0.3, 0.9, -0.5]])
    r = 0.0002 + f @ true_b.T + 0.004 * rng.standard_normal((3000, 4))
    model = L8.factor_regression(r, f)
    assert model.betas == pytest.approx(true_b, abs=0.03)
    assert model.specific_var == pytest.approx(np.full(4, 0.004**2), rel=0.1)
    assert np.all(model.r_squared > 0.7) and model.residuals.shape == (3000, 4)
    w = np.array([0.4, 0.3, 0.2, 0.1])
    hedge_betas = np.eye(3)                                    # three pure factor portfolios
    h, left = L8.factor_neutralizing_hedge(w, model.betas, hedge_betas)
    assert left == pytest.approx(np.zeros(3), abs=1e-12)
    assert h == pytest.approx(-(model.betas.T @ w))
    h1, left1 = L8.factor_neutralizing_hedge(w, model.betas, hedge_betas, factors_to_neutralize=[0])
    assert left1 == pytest.approx([0.0], abs=1e-12)
    risk = L8.factor_risk_decomposition(w, model.betas, model.factor_cov, model.specific_var)
    assert risk["total_variance"] == pytest.approx((r @ w).var(ddof=1), rel=0.05)
    with pytest.raises(ValueError):
        L8.factor_regression(r, np.column_stack([f[:, 0], f[:, 0]]))


# 8.3
def test_daily_sharpe_of_point_one_annualizes_to_1_587():
    assert L8.annualize_sharpe(0.1) == pytest.approx(1.587, abs=1e-3)
    r = np.array([0.02, -0.01, 0.015, 0.005, -0.005] * 40)
    assert L8.sharpe_ratio(r) == pytest.approx(r.mean() / r.std(ddof=1) * math.sqrt(252))
    assert L8.sharpe_ratio(np.full(10, 0.001)) == 0.0


def test_drawdown_sortino_calmar_information_ratio():
    r = np.array([0.10, -0.20, -0.10, 0.05, 0.30])
    dd = L8.max_drawdown(r)
    assert dd.depth == pytest.approx(1 - 0.8 * 0.9)            # 1.10 -> 0.792
    assert (dd.peak_index, dd.trough_index) == (1, 3)
    assert L8.running_drawdown([100, 110, 99, 120])[2] == pytest.approx(0.1)
    growth = np.prod(1 + r)
    assert L8.calmar_ratio(r, periods_per_year=5) == pytest.approx((growth - 1) / dd.depth)
    downside = math.sqrt(np.mean(np.minimum(r, 0) ** 2))
    assert L8.sortino_ratio(r, periods_per_year=1) == pytest.approx(r.mean() / downside)
    assert L8.sortino_ratio([0.01, 0.02, 0.03]) == math.inf
    bench = np.array([0.08, -0.15, -0.12, 0.04, 0.25])
    active = r - bench
    assert L8.information_ratio(r, bench, 1) == pytest.approx(active.mean() / active.std(ddof=1))
    with pytest.raises(ValueError):
        L8.max_drawdown([0.1, -1.0])


# 8.4
def test_kelly_single_bet():
    assert L8.kelly_fraction(0.55, 1.0) == pytest.approx(0.10)
    assert L8.kelly_fraction(0.45, 1.0) == 0.0                                   # negative edge
    assert L8.kelly_fraction(0.25, 4.0) == pytest.approx((0.25 * 4 - 0.75) / 4)
    assert L8.kelly_fraction([0.6, 0.4], [1.0, 1.0]).tolist() == pytest.approx([0.2, 0.0])
    with pytest.raises(ValueError):
        L8.kelly_fraction(1.2, 1.0)
    with pytest.raises(ValueError):
        L8.kelly_fraction(0.5, 0.0)


def test_multi_event_kelly():
    assert L8.kelly_multi_event([0.55], [1.0]) == pytest.approx([0.10], abs=1e-4)
    two = L8.kelly_multi_event([0.55, 0.55], [1.0, 1.0])
    assert two[0] == pytest.approx(two[1], abs=1e-4) and 0.09 < two[0] < 0.10    # each below single-bet Kelly
    mixed = L8.kelly_multi_event([0.60, 0.45, 0.30], [1.0, 1.0, 3.0])
    assert mixed[1] == 0.0 and mixed[0] > 0 and mixed[2] > 0
    assert L8.kelly_multi_event([0.4, 0.3], [1.0, 1.0]).tolist() == [0.0, 0.0]
    big = L8.kelly_multi_event([0.9, 0.9, 0.9], [1.0, 1.0, 1.0], budget=0.5)
    assert big.sum() <= 0.5 + 1e-9
    with pytest.raises(ValueError):
        L8.kelly_multi_event([0.6] * 13, [1.0] * 13)


def test_continuous_kelly_and_fractional_clamp():
    mu, cov = np.array([0.08, 0.05]), np.array([[0.04, 0.01], [0.01, 0.09]])
    f = L8.kelly_continuous(mu, cov, rf=0.02)
    assert cov @ f == pytest.approx(mu - 0.02)
    assert L8.kelly_continuous([0.10], [[0.04]]) == pytest.approx([2.5])        # mu / sigma^2
    assert L8.clamp_kelly_multiplier(0.05) == 0.15 and L8.clamp_kelly_multiplier(0.9) == 0.50
    assert L8.clamp_kelly_multiplier(float("nan")) == 0.15
    assert L8.fractional_kelly(2.5, 0.9) == pytest.approx(1.25)
    assert L8.fractional_kelly([2.5, float("nan")], 0.05).tolist() == pytest.approx([0.375, 0.0])
    with pytest.raises(ValueError):
        L8.kelly_continuous(mu, np.array([[0.04, 0.06], [0.06, 0.09]]))       # singular
    rng = np.random.default_rng(2)
    rets = rng.multivariate_normal([0.001, 0.0005], [[1e-4, 2e-5], [2e-5, 2e-4]], size=4000)
    est = L8.kelly_continuous_from_returns(rets)
    assert est == pytest.approx(L8.kelly_continuous([0.001, 0.0005], [[1e-4, 2e-5], [2e-5, 2e-4]]), rel=0.6)


def test_regime_multiplier_and_leverage_cap():
    table = {"low_vol_bull": 0.50, "liquidity_crash": 0.15, "chop": 0.25}
    assert L8.regime_kelly_multiplier({"low_vol_bull": 1.0}, table) == 0.50
    assert L8.regime_kelly_multiplier({"low_vol_bull": 0.5, "liquidity_crash": 0.5}, table) == pytest.approx(0.325)
    assert L8.regime_kelly_multiplier({"unknown": 1.0}, table) == 0.15           # unknown regime -> minimum
    assert L8.regime_kelly_multiplier({"low_vol_bull": float("nan")}, table) == 0.15
    assert L8.regime_kelly_multiplier({}, table) == 0.15
    capped = L8.apply_leverage_cap([1.5, -1.0, 0.5], 2.0)
    assert np.abs(capped).sum() == pytest.approx(2.0) and capped[1] < 0
    assert L8.apply_leverage_cap([0.3, -0.2], 2.0).tolist() == [0.3, -0.2]
    assert L8.apply_leverage_cap([float("nan"), 0.5], 1.0).tolist() == [0.0, 0.5]


# 8.5
def test_million_dollar_two_percent_var_and_es():
    assert L8.var_parametric(0.02, value=1_000_000) == pytest.approx(32_900, abs=50)
    assert L8.es_parametric(0.02, value=1_000_000) == pytest.approx(41_250, abs=50)
    assert L8.var_parametric(0.02, horizon=4) == pytest.approx(2 * L8.var_parametric(0.02))
    t_var = L8.var_parametric(0.02, alpha=0.99, dist="t", df=4)
    assert t_var > L8.var_parametric(0.02, alpha=0.99)                           # fat tails widen the 99% VaR
    assert L8.es_parametric(0.02, alpha=0.99, dist="t", df=4) > t_var
    with pytest.raises(ValueError):
        L8.var_parametric(0.02, alpha=0.4)
    with pytest.raises(ValueError):
        L8.var_parametric(0.02, dist="t", df=2)


def test_historical_and_monte_carlo_var_agree_with_parametric():
    rng = np.random.default_rng(3)
    r = 0.02 * rng.standard_normal(200_000)
    hv, he = L8.var_historical(r), L8.es_historical(r)
    assert he >= hv
    assert hv == pytest.approx(L8.var_parametric(0.02), rel=0.02)
    assert he == pytest.approx(L8.es_parametric(0.02), rel=0.02)
    cov = np.array([[0.0004, 0.0001], [0.0001, 0.0009]])
    w = np.array([0.6, 0.4])
    sigma_p = math.sqrt(w @ cov @ w)
    mv, me = L8.var_es_monte_carlo([0.0, 0.0], cov, w, np.random.default_rng(4), n_sims=300_000)
    assert mv == pytest.approx(L8.var_parametric(sigma_p), rel=0.02)
    assert me == pytest.approx(L8.es_parametric(sigma_p), rel=0.02)
    tv, te = L8.var_es_monte_carlo([0.0, 0.0], cov, w, np.random.default_rng(5), alpha=0.99, n_sims=300_000, df=4)
    assert te > tv > L8.var_parametric(sigma_p, alpha=0.99)


def test_circuit_breaker_trips_latches_and_fails_closed():
    cb = L8.DrawdownCircuitBreaker(max_daily_loss=0.03, max_var=0.05, max_es=0.07, max_drawdown=0.10)
    assert not cb.check(100.0, 0.01, 0.02, 0.03).breached
    assert not cb.check(105.0, 0.05, 0.02, 0.03).breached
    hit = cb.check(94.0, -0.01, 0.02, 0.03)                                      # 10.5% off the 105 peak
    assert hit.breached and "drawdown" in hit.reasons[0]
    again = cb.check(104.0, 0.01, 0.01, 0.01)                                    # healthy inputs, still latched
    assert again.breached and again.latched and again.reasons[0].startswith("latched")
    with pytest.raises(ValueError):
        cb.reset("")
    cb.reset("burton", new_peak_equity=104.0)
    assert not cb.check(104.0, 0.0, 0.01, 0.01).breached
    assert cb.check(104.0, -0.04, 0.01, 0.01).reasons[0].startswith("daily loss")
    for bad in (float("nan"), None, float("inf")):
        fresh = L8.DrawdownCircuitBreaker(0.03, 0.05, 0.07, 0.10)
        assert fresh.check(100.0, 0.0, bad, 0.01).breached and fresh.latched
    fresh = L8.DrawdownCircuitBreaker(0.03, 0.05, 0.07, 0.10)
    assert fresh.check(0.0, 0.0, 0.01, 0.01).breached
    both = L8.DrawdownCircuitBreaker(0.03, 0.05, 0.07, 0.10).check(100.0, 0.0, 0.06, 0.08)
    assert len(both.reasons) == 2
    with pytest.raises(ValueError):
        L8.DrawdownCircuitBreaker(0.03, 0.05, 0.07, float("nan"))
