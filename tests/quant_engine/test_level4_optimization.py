"""Tests for valedia_trader.quant_engine.level4_optimization (formulas 4.1 to 4.5)."""

from __future__ import annotations

import dataclasses
import warnings

import cvxpy as cp
import numpy as np
import pytest
from scipy import optimize, stats

from valedia_trader.quant_engine import level4_optimization as l4


def _market(seed: int = 0, n: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """A well-conditioned covariance and an expected-return vector."""
    rng = np.random.default_rng(seed)
    a = rng.normal(size=(n, n))
    cov = 0.04 * (a @ a.T) / n + 0.01 * np.eye(n)
    mu = rng.normal(0.05, 0.03, size=n)
    return mu, cov


def _utility(w, mu, cov, gamma, w_prev=None, lin_bps=0.0, quad=0.0) -> float:
    w_prev = np.zeros_like(w) if w_prev is None else w_prev
    d = w - w_prev
    return float(
        mu @ w - 0.5 * gamma * w @ cov @ w - np.sum(lin_bps * 1e-4 * np.abs(d)) - np.sum(quad * d * d)
    )


# --------------------------------------------------------------------------- registry
def test_formulas_registry_is_complete_and_resolvable():
    assert set(l4.FORMULAS) == {"4.1", "4.2", "4.3", "4.4", "4.5"}
    for formula_id, names in l4.FORMULAS.items():
        assert names, formula_id
        for name in names:
            func = getattr(l4, name)
            assert callable(func)
            assert name in l4.__all__
            assert func.__doc__.strip().startswith(formula_id), (name, func.__doc__)


def test_all_names_exist():
    for name in l4.__all__:
        assert hasattr(l4, name), name


# --------------------------------------------------------------------------- 4.1
def test_sqrt_101_by_first_order_taylor_is_10_05():
    # f = sqrt, x = 100: f(x) = 10, f'(x) = 1 / (2 * 10) = 0.05
    approx = l4.taylor_expansion(10.0, 1.0, 0.05)
    assert approx == pytest.approx(10.05, abs=1e-12)
    assert approx == pytest.approx(np.sqrt(101.0), abs=2e-4)
    second = l4.taylor_expansion(10.0, 1.0, 0.05, -1.0 / (4.0 * 1000.0))
    assert abs(second - np.sqrt(101.0)) < abs(approx - np.sqrt(101.0)) / 100.0
    assert l4.taylor_expansion_numeric(np.sqrt, 100.0, 1.0, order=1) == pytest.approx(10.05, abs=1e-8)
    assert l4.taylor_expansion_numeric(np.sqrt, 100.0, 1.0) == pytest.approx(second, abs=1e-7)


def test_taylor_expansion_error_orders_and_vectorisation():
    x = 0.3
    steps = np.array([0.1, 0.05, 0.025])
    exact = np.exp(x + steps)
    first = l4.taylor_expansion(np.exp(x), steps, np.exp(x))
    second = l4.taylor_expansion(np.exp(x), steps, np.exp(x), np.exp(x))
    err1, err2 = np.abs(first - exact), np.abs(second - exact)
    assert np.all(err2 < err1)
    # halving the step divides the error by ~4 (first order) and ~8 (second order)
    np.testing.assert_allclose(err1[:-1] / err1[1:], 4.0, rtol=0.1)
    np.testing.assert_allclose(err2[:-1] / err2[1:], 8.0, rtol=0.1)
    grid = np.linspace(0.5, 2.0, 7)
    numeric = l4.taylor_expansion_numeric(np.log, grid, 0.01)
    np.testing.assert_allclose(numeric, np.log(grid + 0.01), atol=5e-6)  # remainder d^3 / (3 x^3)
    assert numeric.shape == (7,)
    # a quadratic is reproduced exactly by its second-order expansion
    assert l4.taylor_expansion(4.0, 3.0, 4.0, 2.0) == pytest.approx(25.0)  # f = x^2 at 2, d = 3


def test_taylor_expansion_validation():
    with pytest.raises(ValueError, match="shape mismatch"):
        l4.taylor_expansion([1.0, 2.0], [1.0, 2.0, 3.0], 1.0)
    with pytest.raises(ValueError, match="finite"):
        l4.taylor_expansion(np.nan, 1.0, 1.0)
    with pytest.raises(ValueError, match="order"):
        l4.taylor_expansion_numeric(np.sqrt, 100.0, 1.0, order=3)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="step"):
        l4.taylor_expansion_numeric(np.sqrt, 100.0, 1.0, step=0.0)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # reported as an error, never as a stray RuntimeWarning
        with pytest.raises(ValueError, match="non-finite"):
            l4.taylor_expansion_numeric(np.sqrt, 0.0, 1.0)  # sqrt is undefined just left of 0
    with pytest.raises(ValueError, match="vectorised"):
        l4.taylor_expansion_numeric(lambda v: np.array([1.0, 2.0]), 1.0, 0.1)


def _bs_call(spot, strike, vol, tau, rate=0.0):
    d1 = (np.log(spot / strike) + (rate + 0.5 * vol**2) * tau) / (vol * np.sqrt(tau))
    d2 = d1 - vol * np.sqrt(tau)
    price = spot * stats.norm.cdf(d1) - strike * np.exp(-rate * tau) * stats.norm.cdf(d2)
    delta = stats.norm.cdf(d1)
    gamma = stats.norm.pdf(d1) / (spot * vol * np.sqrt(tau))
    vega = spot * stats.norm.pdf(d1) * np.sqrt(tau)
    theta = -spot * stats.norm.pdf(d1) * vol / (2.0 * np.sqrt(tau))  # per year, rate = 0
    return price, delta, gamma, vega, theta


def test_option_pnl_approximation_tracks_black_scholes_repricing():
    spot, strike, vol, tau = 100.0, 100.0, 0.25, 0.5
    price, delta, gamma, vega, theta = _bs_call(spot, strike, vol, tau)
    d_spot = np.array([-2.0, -0.5, 0.0, 0.5, 2.0])
    d_vol, d_time = 0.01, 1.0 / 252.0
    exact = _bs_call(spot + d_spot, strike, vol + d_vol, tau - d_time)[0] - price
    approx = l4.option_pnl_approximation(delta, gamma, vega, theta, d_spot, d_vol, d_time)
    assert approx.shape == (5,)
    np.testing.assert_allclose(approx, exact, atol=0.02)
    # dropping gamma is materially worse for the larger moves
    delta_only = l4.option_pnl_approximation(delta, 0.0, vega, theta, d_spot, d_vol, d_time)
    assert np.abs(delta_only - exact)[[0, 4]].min() > 5 * np.abs(approx - exact)[[0, 4]].max()


def test_option_pnl_formula_attribution_and_broadcasting():
    assert l4.option_pnl_approximation(0.5, 0.02, 30.0, -10.0, 2.0, 0.01, 0.1) == pytest.approx(
        0.5 * 2.0 + 0.5 * 0.02 * 4.0 + 30.0 * 0.01 - 10.0 * 0.1
    )
    attr = l4.option_pnl_attribution(0.5, 0.02, 30.0, -10.0, 2.0, 0.01, 0.1)
    assert (attr.delta_pnl, attr.gamma_pnl, attr.vega_pnl, attr.theta_pnl) == pytest.approx(
        (1.0, 0.04, 0.3, -1.0)
    )
    assert attr.total == pytest.approx(attr.delta_pnl + attr.gamma_pnl + attr.vega_pnl + attr.theta_pnl)
    # positions (rows) x spot scenarios (columns)
    deltas = np.array([[0.5], [-0.3], [0.0]])
    gammas = np.array([[0.02], [0.01], [0.05]])
    scenarios = np.linspace(-5.0, 5.0, 11)
    grid = l4.option_pnl_approximation(deltas, gammas, 0.0, 0.0, scenarios)
    assert grid.shape == (3, 11)
    np.testing.assert_allclose(grid[2], 0.5 * 0.05 * scenarios**2)  # delta-hedged long gamma: always >= 0
    assert np.all(grid[2] >= 0.0)
    # vol and time default to "no change"
    assert l4.option_pnl_approximation(1.0, 0.0, 99.0, 99.0, 3.0) == pytest.approx(3.0)
    with pytest.raises(ValueError, match="shape mismatch"):
        l4.option_pnl_approximation([0.5, 0.4], 0.0, 0.0, 0.0, [1.0, 2.0, 3.0])
    with pytest.raises(ValueError, match="finite"):
        l4.option_pnl_approximation(0.5, np.inf, 0.0, 0.0, 1.0)


# --------------------------------------------------------------------------- 4.2
def test_gradient_descent_converges_with_small_rate_and_diverges_with_large_rate():
    hessian = np.diag([1.0, 4.0])  # L = 0.5 theta' H theta; stable iff eta < 2 / 4

    def grad(theta):
        return hessian @ theta

    def loss(theta):
        return 0.5 * theta @ hessian @ theta

    good = l4.gradient_descent(grad, [3.0, -2.0], learning_rate=0.1, loss=loss)
    assert good.converged and not good.diverged and good.status == "converged"
    np.testing.assert_allclose(good.theta, 0.0, atol=1e-7)
    assert good.grad_norm <= 1e-8
    assert np.all(np.diff(good.loss_history) < 0)  # monotone descent
    assert good.loss == pytest.approx(0.0, abs=1e-14)

    bad = l4.gradient_descent(grad, [3.0, -2.0], learning_rate=0.6, loss=loss)
    assert bad.diverged and not bad.converged and bad.status == "diverged"
    assert np.all(np.isfinite(bad.theta))  # last finite iterate is returned
    assert np.all(np.diff(bad.loss_history[5:]) > 0)  # loss climbing
    assert bad.grad_norm_history[-1] > 1e6 * bad.grad_norm_history[0]
    assert bad.n_iter < 200


def test_gradient_descent_history_and_iteration_accounting():
    res = l4.gradient_descent(lambda t: 2.0 * t, [1.0], learning_rate=0.25, max_iter=5, tol=0.0)
    # theta_{k+1} = theta_k - 0.25 * 2 theta_k = theta_k / 2
    np.testing.assert_allclose(res.theta_history[:, 0], [1.0, 0.5, 0.25, 0.125, 0.0625, 0.03125])
    np.testing.assert_allclose(res.grad_norm_history, 2.0 * res.theta_history[:, 0])
    assert res.n_iter == 5 and res.status == "max_iter" and not res.converged and not res.diverged
    assert res.loss is None and res.loss_history.size == 0
    lean = l4.gradient_descent(lambda t: 2.0 * t, [1.0], learning_rate=0.25, record_history=False)
    assert lean.converged and lean.theta_history.shape == (0, 1)
    assert lean.grad_norm_history.size == lean.n_iter + 1
    # already at the optimum: zero iterations
    at_opt = l4.gradient_descent(lambda t: 2.0 * t, [0.0, 0.0], learning_rate=0.1)
    assert at_opt.converged and at_opt.n_iter == 0
    # scalar start is accepted
    assert l4.gradient_descent(lambda t: 2.0 * (t - 3.0), 0.0, learning_rate=0.2).theta == pytest.approx(
        [3.0]
    )


def test_gradient_descent_marginal_rate_neither_converges_nor_diverges():
    res = l4.gradient_descent(lambda t: t, [1.0, -2.0], learning_rate=2.0, max_iter=100)
    assert res.status == "max_iter" and not res.converged and not res.diverged
    np.testing.assert_allclose(np.abs(res.theta), [1.0, 2.0])  # bounces between +theta0 and -theta0


def test_gradient_descent_detects_overflow_without_warnings():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        res = l4.gradient_descent(
            lambda t: 4.0 * t**3, [10.0], learning_rate=1.0, loss=lambda t: float(t[0] ** 4)
        )
    assert res.diverged and np.all(np.isfinite(res.theta))
    nan_grad = l4.gradient_descent(lambda t: np.where(np.abs(t) > 1.5, np.nan, -t), [1.0], learning_rate=1.0)
    assert nan_grad.diverged and nan_grad.theta == pytest.approx([1.0])


def test_gradient_descent_solves_least_squares():
    rng = np.random.default_rng(1)
    x = rng.normal(size=(200, 3))
    y = x @ np.array([1.0, -2.0, 0.5]) + rng.normal(0.0, 0.1, size=200)

    def grad(beta):
        return x.T @ (x @ beta - y) / 200.0

    res = l4.gradient_descent(grad, np.zeros(3), learning_rate=0.3, tol=1e-10)
    assert res.converged
    np.testing.assert_allclose(res.theta, np.linalg.lstsq(x, y, rcond=None)[0], atol=1e-8)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"learning_rate": 0.0},
        {"learning_rate": -0.1},
        {"learning_rate": np.nan},
        {"learning_rate": 0.1, "max_iter": 0},
        {"learning_rate": 0.1, "max_iter": 2.5},
        {"learning_rate": 0.1, "tol": -1.0},
        {"learning_rate": 0.1, "divergence_factor": 1.0},
    ],
)
def test_gradient_descent_parameter_validation(kwargs):
    with pytest.raises(ValueError):
        l4.gradient_descent(lambda t: t, [1.0], **kwargs)


def test_gradient_descent_input_validation():
    with pytest.raises(ValueError, match="shape"):
        l4.gradient_descent(lambda t: np.ones(3), [1.0, 2.0], learning_rate=0.1)
    with pytest.raises(ValueError, match="not finite at theta0"):
        l4.gradient_descent(lambda t: np.full_like(t, np.inf), [1.0], learning_rate=0.1)
    with pytest.raises(ValueError, match="loss is not finite"):
        l4.gradient_descent(lambda t: t, [1.0], learning_rate=0.1, loss=lambda t: np.nan)
    with pytest.raises(ValueError, match="finite"):
        l4.gradient_descent(lambda t: t, [np.nan], learning_rate=0.1)
    with pytest.raises(ValueError, match="1-D"):
        l4.gradient_descent(lambda t: t, np.ones((2, 2)), learning_rate=0.1)
    start = np.array([1.0, 2.0])
    l4.gradient_descent(lambda t: t.__imul__(0.0) + 1.0, start, learning_rate=0.1, max_iter=3)
    np.testing.assert_array_equal(start, [1.0, 2.0])  # caller's array is never mutated


@pytest.mark.parametrize("method", ["L-BFGS-B", "SLSQP"])
def test_minimize_loss_rosenbrock(method):
    res = l4.minimize_loss(
        optimize.rosen, [-1.2, 1.0, 0.8], method=method, grad=optimize.rosen_der, tol=1e-12
    )
    assert res.converged and res.method == method
    np.testing.assert_allclose(res.theta, 1.0, atol=1e-4)
    assert res.loss == pytest.approx(0.0, abs=1e-8)
    assert res.n_iter > 0 and res.n_fev > 0 and isinstance(res.message, str)
    no_grad = l4.minimize_loss(optimize.rosen, [-1.2, 1.0], method=method)
    np.testing.assert_allclose(no_grad.theta, 1.0, atol=1e-3)


def test_minimize_loss_calibrates_a_decay_model():
    rng = np.random.default_rng(2)
    t = np.linspace(0.0, 5.0, 60)
    observed = 2.0 * np.exp(-0.7 * t) + 0.3 + rng.normal(0.0, 0.01, size=t.size)

    def loss(theta):
        amp, rate, level = theta
        return float(np.mean((amp * np.exp(-rate * t) + level - observed) ** 2))

    fit = l4.minimize_loss(loss, [1.0, 1.0, 0.0], bounds=[(0.0, 10.0), (0.0, 5.0), (-1.0, 1.0)], tol=1e-14)
    assert fit.converged
    np.testing.assert_allclose(fit.theta, [2.0, 0.7, 0.3], atol=0.03)
    assert fit.loss < 2e-4


def test_minimize_loss_respects_bounds_and_constraints():
    def loss(theta):
        return float((theta[0] - 2.0) ** 2 + (theta[1] + 1.0) ** 2)

    boxed = l4.minimize_loss(loss, [0.5, 0.5], bounds=[(0.0, 1.0), (0.0, 1.0)])
    np.testing.assert_allclose(boxed.theta, [1.0, 0.0], atol=1e-7)
    simplex = l4.minimize_loss(
        loss,
        [0.5, 0.5],
        method="SLSQP",
        bounds=[(None, None), (None, None)],
        constraints=[{"type": "eq", "fun": lambda th: th[0] + th[1] - 1.0}],
    )
    np.testing.assert_allclose(simplex.theta, [2.0, -1.0], atol=1e-6)  # the optimum already satisfies it
    tight = l4.minimize_loss(
        loss, [0.0, 0.0], method="SLSQP", constraints=[{"type": "eq", "fun": lambda th: th[0] + th[1]}]
    )
    np.testing.assert_allclose(tight.theta, [1.5, -1.5], atol=1e-6)


def test_minimize_loss_reports_non_convergence_and_validates():
    stalled = l4.minimize_loss(optimize.rosen, [-1.2, 1.0, 0.8, 1.5], max_iter=2)
    assert stalled.converged is False and np.all(np.isfinite(stalled.theta))
    with pytest.raises(ValueError, match="method"):
        l4.minimize_loss(optimize.rosen, [0.0, 0.0], method="Nelder-Mead")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="SLSQP"):
        l4.minimize_loss(optimize.rosen, [0.0, 0.0], constraints=[{"type": "eq", "fun": lambda th: th[0]}])
    with pytest.raises(ValueError, match="bounds"):
        l4.minimize_loss(optimize.rosen, [0.0, 0.0], bounds=[(0.0, 1.0)])
    with pytest.raises(ValueError, match="lower <= upper"):
        l4.minimize_loss(optimize.rosen, [0.0, 0.0], bounds=[(1.0, 0.0), (0.0, 1.0)])
    with pytest.raises(ValueError, match="not finite at theta0"):
        l4.minimize_loss(lambda th: np.inf, [0.0, 0.0])
    with pytest.raises(ValueError, match="tol"):
        l4.minimize_loss(optimize.rosen, [0.0, 0.0], tol=0.0)
    with pytest.raises(ValueError, match="max_iter"):
        l4.minimize_loss(optimize.rosen, [0.0, 0.0], max_iter=0)
    with pytest.raises(ValueError, match="finite"):
        l4.minimize_loss(optimize.rosen, [0.0, np.nan])


# --------------------------------------------------------------------------- 4.3
def test_maximize_xy_subject_to_x_plus_y_equals_10():
    res = l4.solve_equality_constrained(
        lambda x: x[0] * x[1], lambda x: x[0] + x[1] - 10.0, [1.0, 2.0], maximize=True
    )
    assert res.converged
    np.testing.assert_allclose(res.x, [5.0, 5.0], atol=1e-6)
    assert res.objective == pytest.approx(25.0, abs=1e-8)
    # grad f = (y, x) = (5, 5) = lambda * grad h = lambda * (1, 1)
    np.testing.assert_allclose(res.multipliers, [5.0], atol=1e-5)
    assert res.constraint_violation < 1e-9 and res.stationarity_residual < 1e-6


def test_multiplier_is_the_shadow_price_of_the_constraint():
    def solve(c: float):
        return l4.solve_equality_constrained(
            lambda x: x[0] * x[1], lambda x: x[0] + x[1] - c, [1.0, 2.0], maximize=True
        )

    base, bumped = solve(10.0), solve(10.01)
    shadow = (bumped.objective - base.objective) / 0.01  # f* = c^2 / 4 -> df*/dc = c / 2
    assert shadow == pytest.approx(base.multipliers[0], rel=1e-3)
    # the same convention holds when minimising: min x^2 + y^2 s.t. x + y = c has f* = c^2 / 2
    mins = [
        l4.solve_equality_constrained(lambda x: x @ x, lambda x, c=c: x[0] + x[1] - c, [0.3, 0.1])
        for c in (1.0, 1.01)
    ]
    np.testing.assert_allclose(mins[0].x, [0.5, 0.5], atol=1e-6)
    assert mins[0].multipliers[0] == pytest.approx(1.0, abs=1e-5)
    assert (mins[1].objective - mins[0].objective) / 0.01 == pytest.approx(mins[0].multipliers[0], rel=1e-2)


def test_equality_constrained_with_several_constraints_and_analytic_derivatives():
    # closest point to the origin on the line {x + y + z = 1, x - y = 0.5}
    constraints = [lambda x: x[0] + x[1] + x[2] - 1.0, lambda x: x[0] - x[1] - 0.5]
    numeric = l4.solve_equality_constrained(lambda x: x @ x, constraints, [0.0, 0.0, 0.0])
    analytic = l4.solve_equality_constrained(
        lambda x: x @ x,
        lambda x: np.array([x[0] + x[1] + x[2] - 1.0, x[0] - x[1] - 0.5]),
        [0.0, 0.0, 0.0],
        grad=lambda x: 2.0 * x,
        constraint_jac=lambda x: np.array([[1.0, 1.0, 1.0], [1.0, -1.0, 0.0]]),
    )
    closed = l4.solve_equality_constrained_quadratic(
        2.0 * np.eye(3), np.zeros(3), [[1.0, 1.0, 1.0], [1.0, -1.0, 0.0]], [1.0, 0.5]
    )
    for res in (numeric, analytic):
        assert res.converged and res.multipliers.shape == (2,)
        np.testing.assert_allclose(res.x, closed.x, atol=1e-6)
        np.testing.assert_allclose(res.multipliers, closed.multipliers, atol=1e-5)
        assert res.objective == pytest.approx(closed.objective, abs=1e-8)
    np.testing.assert_allclose(closed.x, [7.0 / 12.0, 1.0 / 12.0, 1.0 / 3.0], atol=1e-12)


def test_equality_constrained_nonlinear_constraint():
    # maximise x + y on the unit circle: optimum (1/sqrt2, 1/sqrt2), value sqrt(2)
    res = l4.solve_equality_constrained(
        lambda x: x[0] + x[1], lambda x: x[0] ** 2 + x[1] ** 2 - 1.0, [0.9, 0.1], maximize=True
    )
    assert res.converged
    np.testing.assert_allclose(res.x, [np.sqrt(0.5), np.sqrt(0.5)], atol=1e-6)
    assert res.objective == pytest.approx(np.sqrt(2.0), abs=1e-8)
    # grad f = (1, 1) = lambda * (2x, 2y)  ->  lambda = 1 / sqrt(2)
    assert res.multipliers[0] == pytest.approx(np.sqrt(0.5), abs=1e-5)


def test_equality_constrained_validation():
    def f(x):
        return float(x @ x)

    def h(x):
        return x[0] - 1.0

    with pytest.raises(ValueError, match="constraints"):
        l4.solve_equality_constrained(f, [], [0.0, 0.0])
    with pytest.raises(ValueError, match="constraints"):
        l4.solve_equality_constrained(f, [h, 3.0], [0.0, 0.0])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="objective is not finite"):
        l4.solve_equality_constrained(lambda x: np.nan, h, [0.0, 0.0])
    with pytest.raises(ValueError, match="finite residual"):
        l4.solve_equality_constrained(f, lambda x: np.inf, [0.0, 0.0])
    with pytest.raises(ValueError, match="finite"):
        l4.solve_equality_constrained(f, h, [0.0, np.nan])
    with pytest.raises(ValueError, match="max_iter"):
        l4.solve_equality_constrained(f, h, [0.0, 0.0], max_iter=0)
    with pytest.raises(ValueError, match="tol"):
        l4.solve_equality_constrained(f, h, [0.0, 0.0], tol=0.0)
    with pytest.raises(ValueError, match="grad returned"):
        l4.solve_equality_constrained(f, h, [0.0, 0.0], grad=lambda x: np.ones(3))
    # an inconsistent constraint pair cannot be satisfied: reported, not hidden
    stuck = l4.solve_equality_constrained(f, [lambda x: x[0] - 1.0, lambda x: x[0] + 1.0], [0.0, 0.0])
    assert stuck.converged is False


def test_equality_constrained_quadratic_matches_kkt_system():
    rng = np.random.default_rng(3)
    n, m = 6, 2
    a_rand = rng.normal(size=(n, n))
    q = a_rand @ a_rand.T + np.eye(n)
    c = rng.normal(size=n)
    a = rng.normal(size=(m, n))
    b = rng.normal(size=m)
    res = l4.solve_equality_constrained_quadratic(q, c, a, b)
    kkt = np.block([[q, a.T], [a, np.zeros((m, m))]])
    sol = np.linalg.solve(kkt, np.concatenate([-c, b]))
    np.testing.assert_allclose(res.x, sol[:n], atol=1e-10)
    np.testing.assert_allclose(res.multipliers, -sol[n:], atol=1e-10)  # shadow-price sign convention
    np.testing.assert_allclose(a @ res.x, b, atol=1e-12)
    np.testing.assert_allclose(q @ res.x + c, a.T @ res.multipliers, atol=1e-10)
    assert res.converged and res.constraint_violation < 1e-12 and res.stationarity_residual < 1e-10
    # any other feasible point is worse
    null = np.linalg.svd(a)[2][m:].T
    for _ in range(20):
        other = res.x + null @ rng.normal(size=n - m)
        assert 0.5 * other @ q @ other + c @ other > res.objective
    # shadow price: d f* / d b_j = multipliers[j]
    bumped = l4.solve_equality_constrained_quadratic(q, c, a, b + np.array([1e-5, 0.0]))
    assert (bumped.objective - res.objective) / 1e-5 == pytest.approx(res.multipliers[0], rel=1e-3)


def test_equality_constrained_quadratic_degenerate_and_validation():
    # a fully determined system needs no curvature at all
    pinned = l4.solve_equality_constrained_quadratic(np.zeros((2, 2)), [1.0, 1.0], np.eye(2), [3.0, 4.0])
    np.testing.assert_allclose(pinned.x, [3.0, 4.0])
    # redundant (but consistent) constraints are fine
    dup = l4.solve_equality_constrained_quadratic(np.eye(2), [0.0, 0.0], [[1.0, 1.0], [2.0, 2.0]], [1.0, 2.0])
    np.testing.assert_allclose(dup.x, [0.5, 0.5], atol=1e-12)
    with pytest.raises(ValueError, match="inconsistent"):
        l4.solve_equality_constrained_quadratic(np.eye(2), [0.0, 0.0], [[1.0, 1.0], [1.0, 1.0]], [1.0, 2.0])
    with pytest.raises(ValueError, match="no unique minimiser"):
        l4.solve_equality_constrained_quadratic(np.zeros((2, 2)), [1.0, 0.0], [[1.0, 1.0]], [1.0])
    with pytest.raises(ValueError, match="no unique minimiser"):  # negative curvature along the constraint
        l4.solve_equality_constrained_quadratic(np.diag([1.0, -1.0]), [0.0, 0.0], [[1.0, 0.0]], [1.0])
    with pytest.raises(ValueError, match="symmetric"):
        l4.solve_equality_constrained_quadratic([[1.0, 0.5], [0.0, 1.0]], [0.0, 0.0], [[1.0, 1.0]], [1.0])
    with pytest.raises(ValueError, match="shape mismatch"):
        l4.solve_equality_constrained_quadratic(np.eye(3), [0.0, 0.0], [[1.0, 1.0]], [1.0])
    with pytest.raises(ValueError, match="columns"):
        l4.solve_equality_constrained_quadratic(np.eye(2), [0.0, 0.0], [[1.0, 1.0, 1.0]], [1.0])
    with pytest.raises(ValueError, match="entries"):
        l4.solve_equality_constrained_quadratic(np.eye(2), [0.0, 0.0], [[1.0, 1.0]], [1.0, 2.0])


def test_constrained_portfolio_satisfies_every_constraint():
    mu, cov = _market(0)
    rng = np.random.default_rng(4)
    betas = rng.normal(1.0, 0.3, size=8)
    sectors = ["tech", "tech", "tech", "energy", "energy", "fin", "fin", "fin"]
    spec = l4.PortfolioConstraints(
        dollar_neutral=True,
        betas=betas,
        sectors=sectors,
        sector_net_cap=0.10,
        sector_gross_cap={"tech": 0.60},
        gross_leverage=1.5,
        lower_bound=-0.30,
        upper_bound=0.30,
    )
    sol = l4.optimize_constrained_portfolio(mu, cov, spec, risk_aversion=3.0)
    w = sol.weights
    tol = 1e-6
    assert sol.status == "optimal" and sol.solver == "CLARABEL"
    assert abs(w.sum()) < tol and abs(sol.net_exposure) < tol  # dollar neutral
    assert abs(betas @ w) < tol  # beta neutral
    assert np.abs(w).sum() <= 1.5 + tol and sol.gross_leverage == pytest.approx(np.abs(w).sum())
    assert w.min() >= -0.30 - tol and w.max() <= 0.30 + tol
    labels = np.array(sectors)
    for sector in ("tech", "energy", "fin"):
        assert abs(w[labels == sector].sum()) <= 0.10 + tol
    assert np.abs(w[labels == "tech"]).sum() <= 0.60 + tol
    assert sol.turnover == pytest.approx(np.abs(w).sum()) and sol.transaction_cost == 0.0
    assert sol.objective == pytest.approx(_utility(w, mu, cov, 3.0))
    assert sol.variance == pytest.approx(w @ cov @ w) and sol.expected_return == pytest.approx(mu @ w)
    # the unconstrained optimum violates the constraints, so they are doing real work
    free = l4.markowitz_closed_form(mu, cov, 3.0)
    assert np.abs(free).sum() > 1.5 and abs(free.sum()) > 0.1
    assert sol.objective < _utility(free, mu, cov, 3.0)
    assert {
        "net_exposure",
        "beta",
        "gross_leverage",
        "lower_bound",
        "upper_bound",
        "sector_net[tech]",
    } <= set(sol.duals)
    assert "sector_gross[tech]" in sol.duals and "sector_gross[fin]" not in sol.duals


@pytest.mark.parametrize("solver", ["CLARABEL", "OSQP", "SCS"])
def test_constrained_portfolio_matches_lagrange_closed_form(solver):
    mu, cov = _market(1, n=6)
    rng = np.random.default_rng(5)
    betas = rng.normal(1.0, 0.4, size=6)
    gamma = 5.0
    spec = l4.PortfolioConstraints(dollar_neutral=True, betas=betas)
    sol = l4.optimize_constrained_portfolio(mu, cov, spec, risk_aversion=gamma, solvers=[solver])
    # max mu'w - gamma/2 w'Sigma w  ==  min 0.5 w'(gamma Sigma)w - mu'w  s.t.  [1; beta] w = 0
    closed = l4.solve_equality_constrained_quadratic(
        gamma * cov, -mu, np.vstack([np.ones(6), betas]), [0.0, 0.0]
    )
    np.testing.assert_allclose(sol.weights, closed.x, atol=2e-6)
    assert sol.solver == solver
    # KKT stationarity: mu - gamma Sigma w lies in the span of the constraint normals
    residual = mu - gamma * cov @ sol.weights
    coeffs, *_ = np.linalg.lstsq(np.vstack([np.ones(6), betas]).T, residual, rcond=None)
    np.testing.assert_allclose(np.vstack([np.ones(6), betas]).T @ coeffs, residual, atol=1e-5)
    np.testing.assert_allclose(
        np.abs([sol.duals["net_exposure"], sol.duals["beta"]]), np.abs(closed.multipliers), atol=1e-5
    )


def test_constrained_portfolio_long_only_fully_invested_and_binding_duals():
    mu, cov = _market(2, n=5)
    spec = l4.PortfolioConstraints(net_exposure=1.0, lower_bound=0.0, upper_bound=0.4)
    sol = l4.optimize_constrained_portfolio(mu, cov, spec, risk_aversion=10.0)
    w = sol.weights
    assert w.sum() == pytest.approx(1.0, abs=1e-7)
    assert w.min() >= -1e-7 and w.max() <= 0.4 + 1e-7
    assert sol.gross_leverage == pytest.approx(1.0, abs=1e-6)
    # complementary slackness: a bound has a non-zero multiplier only where it binds
    upper_dual = np.asarray(sol.duals["upper_bound"])
    assert np.all(upper_dual[w < 0.4 - 1e-4] < 1e-6)
    # optimality against random feasible competitors
    rng = np.random.default_rng(6)
    for _ in range(200):
        cand = rng.dirichlet(np.ones(5))
        if cand.max() <= 0.4:
            assert _utility(cand, mu, cov, 10.0) <= sol.objective + 1e-9
    # per-name bounds given as arrays with infinities (unbounded names)
    mixed = l4.PortfolioConstraints(
        net_exposure=1.0,
        lower_bound=[0.0, 0.0, -np.inf, 0.0, 0.0],
        upper_bound=[0.2, np.inf, np.inf, 0.2, 0.2],
    )
    mixed_sol = l4.optimize_constrained_portfolio(mu, cov, mixed, risk_aversion=10.0)
    w2 = mixed_sol.weights
    # bound duals are reported per asset (length n), zero where no finite bound exists
    assert mixed_sol.duals["lower_bound"].shape == (5,) and mixed_sol.duals["upper_bound"].shape == (5,)
    assert mixed_sol.duals["lower_bound"][2] == 0.0
    np.testing.assert_array_equal(mixed_sol.duals["upper_bound"][[1, 2]], 0.0)
    assert isinstance(mixed_sol.duals["net_exposure"], float)
    # KKT stationarity with every multiplier: mu - gamma Sigma w + lower - upper = nu * 1
    stationarity = mu - 10.0 * cov @ w2 + mixed_sol.duals["lower_bound"] - mixed_sol.duals["upper_bound"]
    np.testing.assert_allclose(stationarity, np.full(5, stationarity[0]), atol=1e-6)
    assert abs(stationarity[0]) == pytest.approx(abs(mixed_sol.duals["net_exposure"]), abs=1e-6)
    assert (
        w2[0] <= 0.2 + 1e-7 and w2[[0, 1, 3, 4]].min() >= -1e-7 and w2.sum() == pytest.approx(1.0, abs=1e-7)
    )


def test_beta_tolerance_band_and_target():
    mu, cov = _market(3, n=6)
    betas = np.linspace(0.5, 1.5, 6)
    band = l4.PortfolioConstraints(betas=betas, beta_target=0.2, beta_tolerance=0.05, gross_leverage=1.0)
    w = l4.optimize_constrained_portfolio(mu, cov, band, risk_aversion=1.0).weights
    assert 0.15 - 1e-7 <= betas @ w <= 0.25 + 1e-7
    exact = l4.PortfolioConstraints(betas=betas, beta_target=0.2, gross_leverage=1.0)
    assert betas @ l4.optimize_constrained_portfolio(mu, cov, exact).weights == pytest.approx(0.2, abs=1e-7)


def test_infeasible_and_unbounded_problems_raise_clear_errors():
    mu, cov = _market(4, n=4)
    # every name must be >= 10% long, yet the book must net to zero
    with pytest.raises(l4.InfeasibleProblemError, match="infeasible"):
        l4.optimize_constrained_portfolio(
            mu, cov, l4.PortfolioConstraints(dollar_neutral=True, lower_bound=0.1)
        )
    # gross leverage below the required net exposure
    with pytest.raises(l4.InfeasibleProblemError):
        l4.mean_variance_optimize(
            mu, cov, constraints=l4.PortfolioConstraints(net_exposure=1.0, gross_leverage=0.5)
        )
    # no risk penalty and no limits: the linear objective runs off to infinity
    with pytest.raises(l4.UnboundedProblemError, match="unbounded"):
        l4.mean_variance_optimize(mu, cov, risk_aversion=0.0)
    assert issubclass(l4.InfeasibleProblemError, l4.OptimizationError)
    assert issubclass(l4.UnboundedProblemError, l4.OptimizationError)
    assert issubclass(l4.OptimizationError, RuntimeError)
    # zero risk aversion is fine once the feasible set is bounded: pure linear programme
    lp = l4.mean_variance_optimize(
        mu, cov, risk_aversion=0.0, constraints=l4.PortfolioConstraints(net_exposure=1.0, lower_bound=0.0)
    )
    np.testing.assert_allclose(lp.weights, np.eye(4)[np.argmax(mu)], atol=1e-6)


def test_solver_fallback_order_and_total_failure(monkeypatch):
    mu, cov = _market(5, n=5)
    spec = l4.PortfolioConstraints(dollar_neutral=True, gross_leverage=1.0)
    reference = l4.mean_variance_optimize(mu, cov, constraints=spec)
    assert reference.solver == "CLARABEL" and l4.DEFAULT_SOLVERS == ("CLARABEL", "OSQP", "SCS")

    original = cp.Problem.solve
    attempted: list[str] = []

    def make_flaky(broken: set[str]):
        def flaky(self, *args, **kwargs):
            attempted.append(kwargs["solver"])
            if kwargs["solver"] in broken:
                raise cp.error.SolverError(f"{kwargs['solver']} is down")
            return original(self, *args, **kwargs)

        return flaky

    monkeypatch.setattr(cp.Problem, "solve", make_flaky({"CLARABEL"}))
    second = l4.mean_variance_optimize(mu, cov, constraints=spec)
    assert second.solver == "OSQP" and attempted == ["CLARABEL", "OSQP"]
    np.testing.assert_allclose(second.weights, reference.weights, atol=1e-5)

    attempted.clear()
    monkeypatch.setattr(cp.Problem, "solve", make_flaky({"CLARABEL", "OSQP"}))
    third = l4.mean_variance_optimize(mu, cov, constraints=spec)
    assert third.solver == "SCS" and attempted == ["CLARABEL", "OSQP", "SCS"]
    np.testing.assert_allclose(third.weights, reference.weights, atol=1e-4)

    attempted.clear()
    monkeypatch.setattr(cp.Problem, "solve", make_flaky({"CLARABEL", "OSQP", "SCS"}))
    with pytest.raises(l4.OptimizationError, match="all solvers failed") as excinfo:
        l4.mean_variance_optimize(mu, cov, constraints=spec)
    assert attempted == ["CLARABEL", "OSQP", "SCS"]
    assert all(name in str(excinfo.value) for name in ("CLARABEL", "OSQP", "SCS"))
    assert not isinstance(excinfo.value, l4.InfeasibleProblemError)


def test_solver_fallback_skips_non_optimal_status(monkeypatch):
    mu, cov = _market(6, n=4)
    original = cp.Problem.solve

    def gives_up(self, *args, **kwargs):
        if kwargs["solver"] == "CLARABEL":
            self._status = cp.USER_LIMIT  # solver ran out of iterations
            return None
        return original(self, *args, **kwargs)

    monkeypatch.setattr(cp.Problem, "solve", gives_up)
    sol = l4.mean_variance_optimize(mu, cov, risk_aversion=2.0)
    assert sol.solver == "OSQP"
    np.testing.assert_allclose(sol.weights, l4.markowitz_closed_form(mu, cov, 2.0), atol=1e-6)


def test_solver_argument_handling():
    mu, cov = _market(7, n=4)
    unknown_first = l4.mean_variance_optimize(mu, cov, solvers=("NOT_A_SOLVER", "clarabel"))
    assert unknown_first.solver == "CLARABEL"
    assert l4.mean_variance_optimize(mu, cov, solvers="OSQP").solver == "OSQP"
    with pytest.raises(l4.OptimizationError, match="NOT_A_SOLVER"):
        l4.mean_variance_optimize(mu, cov, solvers=("NOT_A_SOLVER",))
    with pytest.raises(ValueError, match="solvers"):
        l4.mean_variance_optimize(mu, cov, solvers=())
    with pytest.raises(ValueError, match="solvers"):
        l4.mean_variance_optimize(mu, cov, solvers=(1, 2))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "spec_kwargs, match",
    [
        ({"dollar_neutral": True, "net_exposure": 1.0}, "conflicts"),
        ({"beta_target": 0.5}, "require betas"),
        ({"betas": [1.0, 1.0]}, "shape mismatch"),
        ({"betas": [1.0, 1.0, 1.0, 1.0], "beta_tolerance": -0.1}, "beta_tolerance"),
        ({"gross_leverage": 0.0}, "gross_leverage"),
        ({"lower_bound": 0.5, "upper_bound": 0.1}, "must not exceed"),
        ({"lower_bound": [0.0, 0.0]}, "shape mismatch"),
        ({"upper_bound": [0.1, np.nan, 0.1, 0.1]}, "NaN"),
        ({"lower_bound": np.inf}, "cannot be"),
        ({"sector_net_cap": 0.1}, "requires sectors"),
        ({"sectors": ["a", "b"], "sector_net_cap": 0.1}, "shape mismatch"),
        ({"sectors": ["a", "a", "b", "b"], "sector_gross_cap": {"zzz": 0.1}}, "unknown sectors"),
        ({"sectors": ["a", "a", "b", "b"], "sector_net_cap": -0.1}, ">= 0"),
        ({"net_exposure": np.nan}, "finite"),
    ],
)
def test_portfolio_constraint_validation(spec_kwargs, match):
    mu, cov = _market(8, n=4)
    with pytest.raises(ValueError, match=match):
        l4.optimize_constrained_portfolio(mu, cov, l4.PortfolioConstraints(**spec_kwargs))


def test_portfolio_input_validation():
    mu, cov = _market(9, n=4)
    spec = l4.PortfolioConstraints(gross_leverage=1.0)
    with pytest.raises(ValueError, match="PortfolioConstraints"):
        l4.optimize_constrained_portfolio(mu, cov, {"gross_leverage": 1.0})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="PortfolioConstraints"):
        l4.mean_variance_optimize(mu, cov, constraints={"gross_leverage": 1.0})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="shape mismatch"):
        l4.optimize_constrained_portfolio(mu[:3], cov, spec)
    with pytest.raises(ValueError, match="symmetric"):
        l4.optimize_constrained_portfolio(mu, cov + np.triu(np.ones((4, 4)), 1), spec)
    with pytest.raises(ValueError, match="positive semi-definite"):
        l4.optimize_constrained_portfolio(mu, cov - 0.5 * np.eye(4), spec)
    with pytest.raises(ValueError, match="risk_aversion"):
        l4.optimize_constrained_portfolio(mu, cov, spec, risk_aversion=-1.0)
    with pytest.raises(ValueError, match="finite"):
        l4.optimize_constrained_portfolio(np.array([0.1, np.nan, 0.1, 0.1]), cov, spec)
    with pytest.raises(ValueError, match="w_prev"):
        l4.mean_variance_optimize(mu, cov, w_prev=[0.1, 0.2])
    with pytest.raises(ValueError, match="linear_cost_bps"):
        l4.mean_variance_optimize(mu, cov, linear_cost_bps=-1.0)
    with pytest.raises(ValueError, match="quadratic_cost"):
        l4.mean_variance_optimize(mu, cov, quadratic_cost=[0.1, 0.1])
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.gross_leverage = 2.0  # type: ignore[misc]


# --------------------------------------------------------------------------- 4.4
def test_markowitz_closed_form():
    mu, cov = _market(10)
    gamma = 4.0
    w = l4.markowitz_closed_form(mu, cov, gamma)
    np.testing.assert_allclose(w, np.linalg.solve(cov, mu) / gamma, rtol=1e-10)
    np.testing.assert_allclose(gamma * cov @ w, mu, rtol=1e-10)  # first-order condition
    np.testing.assert_allclose(l4.markowitz_closed_form(mu, cov, 2.0 * gamma), w / 2.0, rtol=1e-12)
    # one asset: w = mu / (gamma sigma^2); uncorrelated assets decouple
    assert l4.markowitz_closed_form([0.08], [[0.04]], 2.0) == pytest.approx([1.0])
    np.testing.assert_allclose(
        l4.markowitz_closed_form([0.08, 0.02], np.diag([0.04, 0.01]), 2.0), [1.0, 1.0], rtol=1e-12
    )
    # it is the maximiser: every perturbation lowers the utility
    rng = np.random.default_rng(7)
    best = _utility(w, mu, cov, gamma)
    for _ in range(50):
        assert _utility(w + 0.01 * rng.normal(size=8), mu, cov, gamma) < best


def test_markowitz_closed_form_refuses_ill_conditioned_covariance():
    singular = np.array([[0.04, 0.04], [0.04, 0.04]])
    with pytest.raises(ValueError, match="ill-conditioned"):
        l4.markowitz_closed_form([0.05, 0.06], singular)
    ridged = l4.markowitz_closed_form([0.05, 0.06], singular, ridge=1e-4)
    np.testing.assert_allclose(ridged, np.linalg.solve(singular + 1e-4 * np.eye(2), [0.05, 0.06]), rtol=1e-8)
    with pytest.raises(ValueError, match="ill-conditioned"):
        l4.markowitz_closed_form([0.05, 0.06], np.diag([1.0, 1e-9]), max_condition=1e6)
    with pytest.raises(ValueError, match="ill-conditioned"):
        l4.markowitz_closed_form([0.05, 0.06], [[1.0, 2.0], [2.0, 1.0]])  # not PSD
    for kwargs in ({"risk_aversion": 0.0}, {"ridge": -1.0}, {"max_condition": 1.0}):
        with pytest.raises(ValueError):
            l4.markowitz_closed_form([0.05, 0.06], np.eye(2), **kwargs)
    with pytest.raises(ValueError, match="shape mismatch"):
        l4.markowitz_closed_form([0.05, 0.06, 0.07], np.eye(2))


@pytest.mark.parametrize("gamma", [1.0, 4.0, 25.0])
def test_cvxpy_matches_closed_form_with_zero_costs_and_no_constraints(gamma):
    mu, cov = _market(11)
    closed = l4.markowitz_closed_form(mu, cov, gamma)
    sol = l4.mean_variance_optimize(mu, cov, risk_aversion=gamma)
    np.testing.assert_allclose(sol.weights, closed, atol=1e-6)
    assert sol.status == "optimal" and sol.solver == "CLARABEL"
    assert sol.objective == pytest.approx(_utility(closed, mu, cov, gamma), rel=1e-8)
    assert sol.objective == pytest.approx(0.5 * mu @ closed, rel=1e-6)  # u* = mu' Sigma^-1 mu / (2 gamma)
    assert sol.transaction_cost == 0.0 and dict(sol.duals) == {}
    # zero costs: the starting book is irrelevant
    rng = np.random.default_rng(8)
    moved = l4.mean_variance_optimize(mu, cov, risk_aversion=gamma, w_prev=rng.normal(size=8))
    np.testing.assert_allclose(moved.weights, closed, atol=1e-6)


def test_transaction_costs_strictly_reduce_turnover():
    mu, cov = _market(12)
    rng = np.random.default_rng(9)
    w_prev = rng.normal(0.0, 0.1, size=8)
    gamma = 4.0
    free = l4.mean_variance_optimize(mu, cov, risk_aversion=gamma, w_prev=w_prev)
    costly = l4.mean_variance_optimize(mu, cov, risk_aversion=gamma, w_prev=w_prev, linear_cost_bps=25.0)
    assert costly.turnover < free.turnover - 1e-3
    assert free.turnover == pytest.approx(np.abs(free.weights - w_prev).sum())
    assert costly.transaction_cost == pytest.approx(25e-4 * costly.turnover)
    assert costly.objective == pytest.approx(_utility(costly.weights, mu, cov, gamma, w_prev, 25.0))
    # turnover falls monotonically as costs rise, and trading eventually stops altogether
    turnovers = [
        l4.mean_variance_optimize(mu, cov, risk_aversion=gamma, w_prev=w_prev, linear_cost_bps=bps).turnover
        for bps in (0.0, 10.0, 50.0, 200.0, 1e5)
    ]
    assert np.all(np.diff(turnovers) < 0)
    assert turnovers[-1] == pytest.approx(0.0, abs=1e-7)
    # the cost-aware book beats the naive "ignore costs" book once costs are charged
    naive_after_costs = _utility(free.weights, mu, cov, gamma, w_prev, 25.0)
    assert costly.objective > naive_after_costs
    for _ in range(100):
        cand = costly.weights + 0.02 * rng.normal(size=8)
        assert _utility(cand, mu, cov, gamma, w_prev, 25.0) <= costly.objective + 1e-10


def test_no_trade_region_and_quadratic_costs():
    mu, cov = _market(13, n=5)
    gamma = 3.0
    optimum = l4.markowitz_closed_form(mu, cov, gamma)
    # starting at the frictionless optimum, any cost keeps the book exactly where it is
    stay = l4.mean_variance_optimize(mu, cov, risk_aversion=gamma, w_prev=optimum, linear_cost_bps=5.0)
    np.testing.assert_allclose(stay.weights, optimum, atol=1e-7)
    assert stay.turnover == pytest.approx(0.0, abs=1e-6)
    # linear costs create a dead band: a small mispricing is not worth trading
    nudged = optimum + 1e-4
    dead = l4.mean_variance_optimize(mu, cov, risk_aversion=gamma, w_prev=nudged, linear_cost_bps=50.0)
    np.testing.assert_allclose(dead.weights, nudged, atol=1e-7)
    # quadratic (impact) costs alone shrink trades smoothly: closed form (gamma S + 2 q I) w = mu + 2 q w_prev
    w_prev = np.zeros(5)
    q = 0.05
    impact = l4.mean_variance_optimize(mu, cov, risk_aversion=gamma, w_prev=w_prev, quadratic_cost=q)
    np.testing.assert_allclose(
        impact.weights, np.linalg.solve(gamma * cov + 2.0 * q * np.eye(5), mu), atol=1e-6
    )
    assert impact.turnover < np.abs(optimum).sum()
    assert impact.transaction_cost == pytest.approx(q * np.sum(impact.weights**2))
    both = l4.mean_variance_optimize(
        mu, cov, risk_aversion=gamma, w_prev=w_prev, linear_cost_bps=20.0, quadratic_cost=q
    )
    assert both.turnover < impact.turnover
    assert both.objective == pytest.approx(_utility(both.weights, mu, cov, gamma, w_prev, 20.0, q))


def test_per_name_costs_freeze_only_the_expensive_names():
    mu, cov = _market(14, n=6)
    w_prev = np.full(6, 0.05)
    bps = np.array([0.0, 0.0, 0.0, 1e5, 1e5, 1e5])  # last three names are untradeable
    sol = l4.mean_variance_optimize(mu, cov, risk_aversion=4.0, w_prev=w_prev, linear_cost_bps=bps)
    np.testing.assert_allclose(sol.weights[3:], 0.05, atol=1e-7)
    assert np.abs(sol.weights[:3] - 0.05).min() > 1e-3
    # matches re-optimising the tradeable names with the frozen ones held fixed
    s_tt, s_tf = cov[:3, :3], cov[:3, 3:]
    expected = np.linalg.solve(4.0 * s_tt, mu[:3] - 4.0 * s_tf @ w_prev[3:])
    np.testing.assert_allclose(sol.weights[:3], expected, atol=1e-6)


def test_cost_aware_optimisation_with_the_full_constraint_set():
    mu, cov = _market(15)
    rng = np.random.default_rng(10)
    betas = rng.normal(1.0, 0.3, size=8)
    w_prev = rng.normal(0.0, 0.05, size=8)
    w_prev -= w_prev.mean()
    spec = l4.PortfolioConstraints(
        dollar_neutral=True,
        betas=betas,
        beta_tolerance=0.02,
        sectors=[0, 0, 1, 1, 2, 2, 3, 3],
        sector_net_cap={0: 0.05, 1: 0.05},
        gross_leverage=1.0,
        lower_bound=-0.25,
        upper_bound=0.25,
    )
    kwargs = dict(risk_aversion=5.0, w_prev=w_prev, constraints=spec)
    free = l4.mean_variance_optimize(mu, cov, **kwargs)
    costly = l4.mean_variance_optimize(mu, cov, linear_cost_bps=30.0, quadratic_cost=0.02, **kwargs)
    for sol in (free, costly):
        w = sol.weights
        assert abs(w.sum()) < 1e-6 and abs(betas @ w) <= 0.02 + 1e-6
        assert np.abs(w).sum() <= 1.0 + 1e-6 and np.abs(w).max() <= 0.25 + 1e-6
        assert abs(w[:2].sum()) <= 0.05 + 1e-6 and abs(w[2:4].sum()) <= 0.05 + 1e-6
    assert costly.turnover < free.turnover
    assert costly.objective == pytest.approx(_utility(costly.weights, mu, cov, 5.0, w_prev, 30.0, 0.02))
    assert "sector_net[0]" in costly.duals and "sector_net[2]" not in costly.duals


@pytest.mark.parametrize("solver", ["CLARABEL", "OSQP", "SCS"])
def test_solution_is_invariant_to_the_scale_of_the_inputs(solver):
    # Rescaling mu, Sigma and the costs by s leaves the optimal book unchanged. Solver tolerances
    # are absolute, so without internal normalisation intraday-sized inputs would be declared
    # "optimal" far from the optimum.
    mu, cov = _market(17, n=12)
    rng = np.random.default_rng(18)
    w_prev = rng.normal(0.0, 0.02, size=12)
    spec = l4.PortfolioConstraints(
        dollar_neutral=True,
        betas=rng.normal(1.0, 0.3, size=12),
        gross_leverage=1.5,
        lower_bound=-0.2,
        upper_bound=0.2,
        sectors=list(np.arange(12) % 3),
        sector_net_cap=0.1,
    )

    def solve(s: float) -> l4.PortfolioSolution:
        return l4.mean_variance_optimize(
            s * mu,
            s * cov,
            risk_aversion=5.0,
            w_prev=w_prev,
            linear_cost_bps=20.0 * s,
            quadratic_cost=0.01 * s,
            constraints=spec,
            solvers=[solver],
        )

    base = solve(1.0)
    for s in (1e-4, 1e-8, 1e-12, 1e6):
        scaled = solve(s)
        assert scaled.status == "optimal"
        np.testing.assert_allclose(scaled.weights, base.weights, atol=1e-6)
        assert scaled.objective == pytest.approx(s * base.objective, rel=1e-5)
        for name, dual in base.duals.items():  # shadow prices scale with the objective
            np.testing.assert_allclose(np.asarray(scaled.duals[name]) / s, np.asarray(dual), atol=1e-5)


def test_mean_variance_handles_singular_psd_covariance():
    # two perfectly correlated assets: Sigma is PSD but singular; bounds keep the problem finite
    cov = np.array([[0.04, 0.04, 0.0], [0.04, 0.04, 0.0], [0.0, 0.0, 0.09]])
    mu = np.array([0.06, 0.05, 0.04])
    with pytest.raises(l4.UnboundedProblemError):
        l4.mean_variance_optimize(mu, cov, risk_aversion=2.0)  # free arbitrage between the twins
    spec = l4.PortfolioConstraints(lower_bound=-1.0, upper_bound=1.0)
    sol = l4.mean_variance_optimize(mu, cov, risk_aversion=2.0, constraints=spec)
    assert sol.weights[0] == pytest.approx(1.0, abs=1e-6) and sol.weights[0] > sol.weights[1]
    assert sol.weights[2] == pytest.approx(0.04 / (2.0 * 0.09), abs=1e-6)
    zero_cov = l4.mean_variance_optimize(
        mu, np.zeros((3, 3)), constraints=l4.PortfolioConstraints(gross_leverage=1.0)
    )
    np.testing.assert_allclose(zero_cov.weights, [1.0, 0.0, 0.0], atol=1e-6)
    assert zero_cov.variance == 0.0


# --------------------------------------------------------------------------- 4.5
def test_ten_percent_arithmetic_twenty_vol_is_about_eight_percent_geometric():
    assert l4.geometric_return_approx(0.10, 0.20) == pytest.approx(0.08, abs=1e-12)
    assert l4.volatility_drag(0.20) == pytest.approx(0.02, abs=1e-12)
    np.testing.assert_allclose(
        l4.geometric_return_approx(0.10, [0.0, 0.1, 0.2, 0.4]), [0.10, 0.095, 0.08, 0.02]
    )
    # simulated log growth of a lognormal asset with 10% arithmetic drift and 20% vol
    rng = np.random.default_rng(11)
    dt = 1.0 / 252.0
    daily = 0.10 * dt + 0.20 * np.sqrt(dt) * rng.normal(size=252 * 400)
    growth = np.mean(np.log1p(daily)) / dt
    assert growth == pytest.approx(0.08, abs=0.012)
    with pytest.raises(ValueError, match=">= 0"):
        l4.volatility_drag(-0.1)
    with pytest.raises(ValueError, match="shape mismatch"):
        l4.geometric_return_approx([0.1, 0.2], [0.1, 0.2, 0.3])


def test_plus_fifty_then_minus_fifty_leaves_you_down_25_percent():
    assert l4.compound_returns([0.5, -0.5]) == pytest.approx(-0.25, abs=1e-15)
    assert l4.compound_returns([-0.5, 0.5]) == pytest.approx(-0.25, abs=1e-15)  # order does not matter
    assert np.mean([0.5, -0.5]) == 0.0  # the arithmetic mean says "flat"
    assert l4.geometric_mean_return([0.5, -0.5]) == pytest.approx(np.sqrt(0.75) - 1.0)
    assert l4.geometric_mean_return([0.5, -0.5]) < 0.0


def test_compounding_exactness_and_edge_cases():
    rng = np.random.default_rng(12)
    r = rng.normal(0.0005, 0.01, size=(3, 500))
    total = l4.compound_returns(r)
    np.testing.assert_allclose(total, np.cumprod(1.0 + r, axis=1)[:, -1] - 1.0, rtol=1e-12)
    assert total.shape == (3,)
    gm = l4.geometric_mean_return(r)
    np.testing.assert_allclose((1.0 + gm) ** 500 - 1.0, total, rtol=1e-9)
    assert np.all(gm <= r.mean(axis=1))  # AM-GM: geometric mean never exceeds the arithmetic mean
    # the drag approximation: r_geom ~ r_arith - sigma^2 / 2
    np.testing.assert_allclose(gm, l4.geometric_return_approx(r.mean(axis=1), r.std(axis=1)), atol=2e-6)
    assert l4.compound_returns([]) == 0.0
    assert l4.compound_returns([0.1]) == pytest.approx(0.1)
    assert l4.compound_returns(0.1) == pytest.approx(0.1)
    assert l4.compound_returns([0.2, -1.0, 0.3]) == -1.0  # wiped out: no recovery
    assert l4.geometric_mean_return([0.2, -1.0, 0.3]) == -1.0
    assert l4.geometric_mean_return([0.1, 0.1, 0.1]) == pytest.approx(0.1)
    with pytest.raises(ValueError, match="-100%"):
        l4.compound_returns([0.1, -1.5])
    with pytest.raises(ValueError, match="empty"):
        l4.geometric_mean_return([])
    with pytest.raises(ValueError, match="finite"):
        l4.compound_returns([0.1, np.nan])


def test_jensen_gap_sign_follows_convexity():
    rng = np.random.default_rng(13)
    x = rng.normal(1.0, 0.3, size=50_000)
    convex = l4.jensen_gap(x, np.exp)
    concave = l4.jensen_gap(x, np.log1p)
    linear = l4.jensen_gap(x, lambda v: 3.0 * v - 2.0)
    assert convex.gap > 5 * convex.std_err > 0  # E[f(X)] > f(E[X]), significantly
    assert concave.gap < -5 * concave.std_err < 0
    assert linear.gap == pytest.approx(0.0, abs=1e-12) and linear.std_err == pytest.approx(0.0, abs=1e-12)
    assert convex.gap == pytest.approx(convex.mean_of_f - convex.f_of_mean)
    assert convex.n == 50_000
    # lognormal: E[e^X] - e^{E[X]} = e^mu (e^{sigma^2/2} - 1)
    assert convex.gap == pytest.approx(np.e * (np.exp(0.045) - 1.0), abs=4 * convex.std_err)
    # f(x) = x^2: the gap is exactly the (population) variance
    square = l4.jensen_gap(x, np.square)
    assert square.gap == pytest.approx(x.var(), rel=1e-10)
    assert square.std_err == pytest.approx(np.sqrt(2.0) * 0.09 / np.sqrt(50_000), rel=0.05)


def test_jensen_gap_explains_volatility_drag_and_option_convexity():
    rng = np.random.default_rng(14)
    r = rng.normal(0.0, 0.02, size=200_000)
    log_gap = l4.jensen_gap(r, np.log1p)
    assert log_gap.gap == pytest.approx(-l4.volatility_drag(0.02), rel=0.05)  # -sigma^2 / 2
    # a call payoff is convex: holding the option beats the payoff of the average outcome
    spot = 100.0 * np.exp(rng.normal(-0.02, 0.2, size=200_000))
    call = l4.jensen_gap(spot, lambda s: np.maximum(s - 100.0, 0.0))
    assert call.gap > 0 and call.f_of_mean == pytest.approx(max(spot.mean() - 100.0, 0.0))


def test_jensen_gap_degenerate_and_validation():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        const = l4.jensen_gap(np.full(10, 2.0), np.exp)
    assert const.gap == 0.0 and const.std_err == 0.0
    # extreme units: no warnings, a finite standard error, or an explicit error when unrepresentable
    rng = np.random.default_rng(140)
    x = rng.normal(size=500)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        huge = l4.jensen_gap(1e150 * x, np.square)
        assert huge.gap == pytest.approx(1e300 * x.var(), rel=1e-9) and np.isfinite(huge.std_err)
        assert huge.std_err == pytest.approx(1e300 * l4.jensen_gap(x, np.square).std_err, rel=1e-6)
        with pytest.raises(ValueError, match="non-finite"):
            l4.jensen_gap(1e200 * x, np.square)
    with pytest.raises(ValueError, match="at least 2"):
        l4.jensen_gap([1.0], np.exp)
    with pytest.raises(ValueError, match="1-D"):
        l4.jensen_gap(np.ones((2, 2)), np.exp)
    with pytest.raises(ValueError, match="vectorised"):
        l4.jensen_gap([1.0, 2.0, 3.0], lambda v: 1.0)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(ValueError, match="non-finite"):
            l4.jensen_gap([-1.0, 0.0, 1.0], np.log)
    with pytest.raises(ValueError, match="finite"):
        l4.jensen_gap([1.0, np.inf], np.exp)


def test_convexity_score_of_standard_payoffs():
    grid = np.linspace(50.0, 150.0, 41)
    long_call = l4.convexity_score(grid, np.maximum(grid - 100.0, 0.0))
    short_call = l4.convexity_score(grid, -np.maximum(grid - 100.0, 0.0))
    stock = l4.convexity_score(grid, 2.0 * grid + 1.0)
    straddle = l4.convexity_score(grid, np.abs(grid - 100.0))
    call_spread = l4.convexity_score(grid, np.clip(grid - 90.0, 0.0, 20.0))
    assert long_call.score == 1.0 and long_call.is_convex and not long_call.is_concave
    assert short_call.score == -1.0 and short_call.is_concave and not short_call.is_convex
    assert stock.score == 0.0 and stock.is_linear and not stock.is_convex and not stock.is_concave
    assert stock.chord_gap == 0.0
    assert straddle.score == 1.0 and straddle.chord_gap > long_call.chord_gap > 0.0
    # long the 90 call, short the 110 call: convex kink then concave kink cancel out
    assert call_spread.score == pytest.approx(0.0, abs=1e-12)
    assert not call_spread.is_convex and not call_spread.is_concave and not call_spread.is_linear
    assert short_call.chord_gap == pytest.approx(-long_call.chord_gap)
    assert long_call.second_derivatives.shape == (39,)
    assert long_call.mean_second_derivative == pytest.approx(1.0 / (grid[-1] - grid[0] - 2.5))


def test_convexity_score_smooth_functions_and_non_uniform_grids():
    rng = np.random.default_rng(15)
    grid = np.sort(rng.uniform(0.5, 3.0, size=60))
    grid = grid[np.concatenate([[True], np.diff(grid) > 1e-3])]
    exp = l4.convexity_score(grid, np.exp(grid))
    log = l4.convexity_score(grid, np.log(grid))
    assert exp.score == 1.0 and exp.is_convex and log.score == -1.0 and log.is_concave
    # second differences approximate f'' even on an irregular grid
    quad = l4.convexity_score(grid, 3.0 * grid**2 - grid)
    np.testing.assert_allclose(quad.second_derivatives, 6.0, rtol=1e-8)
    assert quad.mean_second_derivative == pytest.approx(6.0, rel=1e-8)
    assert l4.convexity_score(grid, -7.5 * grid + 2.0).is_linear  # no noise on a straight line
    wave = l4.convexity_score(np.linspace(0.0, 2.0 * np.pi, 201), np.sin(np.linspace(0.0, 2.0 * np.pi, 201)))
    assert wave.score == pytest.approx(0.0, abs=1e-6)  # concave half cancels the convex half
    # the score is invariant to positive rescaling and to adding a linear term
    base = l4.convexity_score(grid, np.exp(grid) - 2.0 * np.sin(3.0 * grid))
    shifted = l4.convexity_score(grid, 1e6 * (np.exp(grid) - 2.0 * np.sin(3.0 * grid)) + 40.0 * grid - 9.0)
    assert shifted.score == pytest.approx(base.score, rel=1e-6)
    assert -1.0 < base.score < 1.0


@pytest.mark.parametrize(
    "grid, payoff, kwargs",
    [
        ([1.0, 2.0], [1.0, 2.0], {}),
        ([1.0, 2.0, 2.0], [1.0, 2.0, 3.0], {}),
        ([3.0, 2.0, 1.0], [1.0, 2.0, 3.0], {}),
        ([1.0, 2.0, 3.0], [1.0, 2.0], {}),
        ([1.0, 2.0, np.nan], [1.0, 2.0, 3.0], {}),
        ([[1.0, 2.0, 3.0]], [[1.0, 2.0, 3.0]], {}),
        ([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], {"rel_tol": -1.0}),
    ],
)
def test_convexity_score_validation(grid, payoff, kwargs):
    with pytest.raises(ValueError):
        l4.convexity_score(grid, payoff, **kwargs)


def test_inputs_are_not_mutated():
    mu, cov = _market(16, n=5)
    w_prev = np.linspace(-0.1, 0.1, 5)
    betas = np.linspace(0.8, 1.2, 5)
    lower = np.full(5, -0.5)
    snapshots = [a.copy() for a in (mu, cov, w_prev, betas, lower)]
    spec = l4.PortfolioConstraints(dollar_neutral=True, betas=betas, lower_bound=lower, upper_bound=0.5)
    l4.mean_variance_optimize(mu, cov, w_prev=w_prev, linear_cost_bps=10.0, constraints=spec)
    l4.markowitz_closed_form(mu, cov)
    l4.compound_returns(w_prev)
    l4.convexity_score(np.arange(5.0), w_prev)
    for original, snap in zip((mu, cov, w_prev, betas, lower), snapshots, strict=True):
        np.testing.assert_array_equal(original, snap)
