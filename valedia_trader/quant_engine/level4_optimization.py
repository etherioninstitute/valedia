"""Level 4 - calculus and optimisation (formulas 4.1 to 4.5).

Self-contained, pure numpy/scipy/cvxpy implementations: Taylor/Greeks P&L approximations,
gradient descent and scipy loss calibration, Lagrange-multiplier solvers, constrained and
transaction-cost-aware mean-variance portfolio construction, and convexity/Jensen tools.

Conventions
-----------
* Array-likes are accepted everywhere; scalar inputs give Python floats back.
* Invalid input raises ``ValueError``. Solver failures raise :class:`OptimizationError`
  (``InfeasibleProblemError`` / ``UnboundedProblemError`` when the problem itself is at
  fault) - a portfolio is never returned unless a solver certified it and it passed an
  independent feasibility check.
* Iterative routines report ``converged`` flags instead of raising on non-convergence.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

import cvxpy as cp
import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import optimize

__all__ = [
    "DEFAULT_SOLVERS",
    "FORMULAS",
    "ConvexityScore",
    "EqualityConstrainedResult",
    "GradientDescentResult",
    "InfeasibleProblemError",
    "JensenGap",
    "MinimizeResult",
    "OptimizationError",
    "OptionPnLAttribution",
    "PortfolioConstraints",
    "PortfolioSolution",
    "UnboundedProblemError",
    "compound_returns",
    "convexity_score",
    "geometric_mean_return",
    "geometric_return_approx",
    "gradient_descent",
    "jensen_gap",
    "markowitz_closed_form",
    "mean_variance_optimize",
    "minimize_loss",
    "optimize_constrained_portfolio",
    "option_pnl_approximation",
    "option_pnl_attribution",
    "solve_equality_constrained",
    "solve_equality_constrained_quadratic",
    "taylor_expansion",
    "taylor_expansion_numeric",
    "volatility_drag",
]

FloatArray = NDArray[np.float64]

FORMULAS: dict[str, tuple[str, ...]] = {
    "4.1": (
        "taylor_expansion",
        "taylor_expansion_numeric",
        "option_pnl_approximation",
        "option_pnl_attribution",
    ),
    "4.2": ("gradient_descent", "minimize_loss"),
    "4.3": (
        "solve_equality_constrained",
        "solve_equality_constrained_quadratic",
        "optimize_constrained_portfolio",
    ),
    "4.4": ("markowitz_closed_form", "mean_variance_optimize"),
    "4.5": (
        "jensen_gap",
        "volatility_drag",
        "geometric_return_approx",
        "compound_returns",
        "geometric_mean_return",
        "convexity_score",
    ),
}

DEFAULT_SOLVERS: tuple[str, ...] = ("CLARABEL", "OSQP", "SCS")

_SOLVER_OPTIONS: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {
        "CLARABEL": MappingProxyType({}),
        "OSQP": MappingProxyType({"eps_abs": 1e-9, "eps_rel": 1e-9, "max_iter": 200_000, "polish": True}),
        "SCS": MappingProxyType({"eps_abs": 1e-8, "eps_rel": 1e-8, "max_iters": 100_000}),
    }
)

_EPS = float(np.finfo(np.float64).eps)
_TINY = float(np.finfo(np.float64).tiny)
_FEASIBILITY_TOL = 1e-6
_INACCURATE_FEASIBILITY_TOL = 1e-4


class OptimizationError(RuntimeError):
    """No solver produced a usable solution."""


class InfeasibleProblemError(OptimizationError):
    """The constraint set is empty (a solver returned an infeasibility certificate)."""


class UnboundedProblemError(OptimizationError):
    """The objective is unbounded over the feasible set."""


# --------------------------------------------------------------------------- helpers
def _to_array(x: ArrayLike, name: str, *, allow_empty: bool = False) -> FloatArray:
    """Convert to a finite float array or raise ``ValueError``."""
    try:
        arr = np.asarray(x, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if arr.size == 0 and not allow_empty:
        raise ValueError(f"{name} must not be empty")
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} must contain only finite values (no NaN/inf)")
    return arr


def _to_vector(x: ArrayLike, name: str, *, size: int | None = None) -> FloatArray:
    arr = _to_array(x, name)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be 1-D, got shape {arr.shape}")
    if size is not None and arr.size != size:
        raise ValueError(f"shape mismatch: {name} has {arr.size} entries, expected {size}")
    return arr


def _to_scalar(x: Any, name: str) -> float:
    try:
        value = float(x)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a real scalar") from exc
    if not np.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _to_positive_int(x: Any, name: str) -> int:
    if isinstance(x, bool) or not isinstance(x, (int, np.integer)) or x < 1:
        raise ValueError(f"{name} must be a positive integer")
    return int(x)


def _unwrap(arr: ArrayLike) -> float | FloatArray:
    out = np.asarray(arr, dtype=np.float64)
    return float(out) if out.ndim == 0 else out


def _broadcast(names: tuple[str, ...], *arrays: FloatArray) -> list[FloatArray]:
    try:
        return list(np.broadcast_arrays(*arrays))
    except ValueError as exc:
        shapes = ", ".join(f"{n}{a.shape}" for n, a in zip(names, arrays, strict=True))
        raise ValueError(f"shape mismatch: cannot broadcast {shapes}") from exc


def _to_symmetric(matrix: ArrayLike, name: str, n: int) -> FloatArray:
    """Finite, symmetric ``n x n`` matrix, returned exactly symmetrised."""
    mat = _to_array(matrix, name)
    if mat.ndim != 2 or mat.shape[0] != mat.shape[1]:
        raise ValueError(f"{name} must be a square matrix, got shape {mat.shape}")
    if mat.shape[0] != n:
        raise ValueError(f"shape mismatch: {name} is {mat.shape[0]}x{mat.shape[0]}, expected {n}x{n}")
    if np.max(np.abs(mat - mat.T)) > 1e-8 * max(float(np.max(np.abs(mat))), _TINY):
        raise ValueError(f"{name} must be symmetric")
    return 0.5 * (mat + mat.T)


def _to_cov(cov: ArrayLike, n: int) -> FloatArray:
    return _to_symmetric(cov, "cov", n)


def _per_name(value: ArrayLike, name: str, n: int) -> FloatArray:
    """Scalar or length-n array of non-negative finite numbers, broadcast to length n."""
    arr = _to_array(value, name)
    if arr.ndim > 1 or (arr.ndim == 1 and arr.size != n):
        raise ValueError(f"shape mismatch: {name} must be a scalar or have {n} entries")
    if np.any(arr < 0.0):
        raise ValueError(f"{name} must be >= 0")
    return np.broadcast_to(arr, (n,)).astype(np.float64)


# --------------------------------------------------------------------------- 4.1 Taylor
def taylor_expansion(
    f_x: ArrayLike,
    delta: ArrayLike,
    first_derivative: ArrayLike,
    second_derivative: ArrayLike | None = None,
) -> float | FloatArray:
    """4.1 Taylor expansion: f(x + d) ~ f(x) + f'(x) d + 0.5 f''(x) d^2.

    First order when ``second_derivative`` is omitted, second order otherwise. Vectorised
    with broadcasting.
    """
    f0 = _to_array(f_x, "f_x")
    d = _to_array(delta, "delta")
    d1 = _to_array(first_derivative, "first_derivative")
    if second_derivative is None:
        f0, d, d1 = _broadcast(("f_x", "delta", "first_derivative"), f0, d, d1)
        return _unwrap(f0 + d1 * d)
    d2 = _to_array(second_derivative, "second_derivative")
    f0, d, d1, d2 = _broadcast(("f_x", "delta", "first_derivative", "second_derivative"), f0, d, d1, d2)
    return _unwrap(f0 + d1 * d + 0.5 * d2 * d * d)


def taylor_expansion_numeric(
    func: Callable[[FloatArray], ArrayLike],
    x: ArrayLike,
    delta: ArrayLike,
    *,
    order: Literal[1, 2] = 2,
    step: float | None = None,
) -> float | FloatArray:
    """4.1 Taylor expansion f(x + d) ~ f(x) + f'(x) d + 0.5 f''(x) d^2 with numerical derivatives.

    ``func`` must be vectorised. Derivatives are central finite differences with step
    ``step`` (default ``1e-4 * max(1, |x|)``).
    """
    if order not in (1, 2):
        raise ValueError("order must be 1 or 2")
    x0 = _to_array(x, "x")
    d = _to_array(delta, "delta")
    if step is None:
        h = 1e-4 * np.maximum(1.0, np.abs(x0))
    else:
        h = np.full(x0.shape, _to_scalar(step, "step"))
        if np.any(h <= 0.0):
            raise ValueError("step must be > 0")

    def evaluate(point: FloatArray) -> FloatArray:
        with np.errstate(all="ignore"):  # non-finite values are reported below, not warned about
            value = np.asarray(func(point), dtype=np.float64)
        if value.shape != point.shape:
            raise ValueError("func must be vectorised: it has to return an array shaped like its input")
        if not np.all(np.isfinite(value)):
            raise ValueError("func returned non-finite values near x")
        return value

    centre = evaluate(x0)
    up = evaluate(x0 + h)
    down = evaluate(x0 - h)
    first = (up - down) / (2.0 * h)
    second = (up - 2.0 * centre + down) / (h * h) if order == 2 else None
    return taylor_expansion(centre, d, first, second)


@dataclass(frozen=True)
class OptionPnLAttribution:
    """Greeks P&L split; ``total = delta_pnl + gamma_pnl + vega_pnl + theta_pnl``."""

    delta_pnl: float | FloatArray
    gamma_pnl: float | FloatArray
    vega_pnl: float | FloatArray
    theta_pnl: float | FloatArray
    total: float | FloatArray


def option_pnl_attribution(
    delta: ArrayLike,
    gamma: ArrayLike,
    vega: ArrayLike,
    theta: ArrayLike,
    d_spot: ArrayLike,
    d_vol: ArrayLike = 0.0,
    d_time: ArrayLike = 0.0,
) -> OptionPnLAttribution:
    """4.1 Option P&L by Greek: Delta*dS, 0.5*Gamma*dS^2, Vega*dSigma, Theta*dt.

    Units must be consistent: ``vega`` per unit of ``d_vol`` and ``theta`` per unit of
    ``d_time``. All inputs broadcast (positions x scenarios).
    """
    names = ("delta", "gamma", "vega", "theta", "d_spot", "d_vol", "d_time")
    values = (delta, gamma, vega, theta, d_spot, d_vol, d_time)
    arrays = [_to_array(v, n) for v, n in zip(values, names, strict=True)]
    dl, gm, vg, th, ds, dv, dt = _broadcast(names, *arrays)
    delta_pnl = dl * ds
    gamma_pnl = 0.5 * gm * ds * ds
    vega_pnl = vg * dv
    theta_pnl = th * dt
    return OptionPnLAttribution(
        delta_pnl=_unwrap(delta_pnl),
        gamma_pnl=_unwrap(gamma_pnl),
        vega_pnl=_unwrap(vega_pnl),
        theta_pnl=_unwrap(theta_pnl),
        total=_unwrap(delta_pnl + gamma_pnl + vega_pnl + theta_pnl),
    )


def option_pnl_approximation(
    delta: ArrayLike,
    gamma: ArrayLike,
    vega: ArrayLike,
    theta: ArrayLike,
    d_spot: ArrayLike,
    d_vol: ArrayLike = 0.0,
    d_time: ArrayLike = 0.0,
) -> float | FloatArray:
    """4.1 Option P&L approximation: dPnL ~ Delta*dS + 0.5*Gamma*dS^2 + Vega*dSigma + Theta*dt."""
    return option_pnl_attribution(delta, gamma, vega, theta, d_spot, d_vol, d_time).total


# --------------------------------------------------------------------------- 4.2 gradient descent
@dataclass(frozen=True)
class GradientDescentResult:
    """Outcome of :func:`gradient_descent`.

    ``status`` is ``"converged"`` (gradient norm <= tol), ``"diverged"`` (non-finite or
    exploding iterate; ``theta`` is then the last finite iterate) or ``"max_iter"``.
    Histories hold the starting point plus every accepted iterate; ``theta_history`` is
    empty when ``record_history=False`` and ``loss_history`` is empty without ``loss``.
    """

    theta: FloatArray
    converged: bool
    diverged: bool
    status: str
    n_iter: int
    grad_norm: float
    loss: float | None
    theta_history: FloatArray
    grad_norm_history: FloatArray
    loss_history: FloatArray


def gradient_descent(
    grad: Callable[[FloatArray], ArrayLike],
    theta0: ArrayLike,
    *,
    learning_rate: float,
    max_iter: int = 10_000,
    tol: float = 1e-8,
    loss: Callable[[FloatArray], float] | None = None,
    divergence_factor: float = 1e8,
    record_history: bool = True,
) -> GradientDescentResult:
    """4.2 Gradient descent: theta <- theta - eta * grad L(theta).

    Stops when ``||grad|| <= tol`` (converged), when an iterate, gradient or loss becomes
    non-finite or the gradient norm exceeds ``divergence_factor * max(1, ||grad_0||)``
    (diverged), or after ``max_iter`` steps.
    """
    eta = _to_scalar(learning_rate, "learning_rate")
    if eta <= 0.0:
        raise ValueError("learning_rate must be > 0")
    steps = _to_positive_int(max_iter, "max_iter")
    tolerance = _to_scalar(tol, "tol")
    if tolerance < 0.0:
        raise ValueError("tol must be >= 0")
    blow_up = _to_scalar(divergence_factor, "divergence_factor")
    if blow_up <= 1.0:
        raise ValueError("divergence_factor must be > 1")
    theta = np.array(np.atleast_1d(_to_array(theta0, "theta0")), dtype=np.float64)
    if theta.ndim != 1:
        raise ValueError(f"theta0 must be a scalar or 1-D vector, got shape {theta.shape}")

    def eval_grad(point: FloatArray) -> FloatArray:
        try:
            g = np.atleast_1d(np.asarray(grad(point.copy()), dtype=np.float64))
        except (TypeError, ValueError) as exc:
            raise ValueError("grad must return a numeric array") from exc
        if g.shape != point.shape:
            raise ValueError(f"grad returned shape {g.shape}, expected {point.shape}")
        return g

    def eval_loss(point: FloatArray) -> float:
        return float(loss(point.copy())) if loss is not None else float("nan")

    g = eval_grad(theta)
    if not np.all(np.isfinite(g)):
        raise ValueError("the gradient is not finite at theta0")
    g_norm = float(np.linalg.norm(g))
    loss_value = eval_loss(theta)
    if loss is not None and not np.isfinite(loss_value):
        raise ValueError("the loss is not finite at theta0")
    limit = blow_up * max(1.0, g_norm)

    thetas = [theta.copy()] if record_history else []
    grad_norms = [g_norm]
    losses = [loss_value] if loss is not None else []
    status = "max_iter"
    n_iter = 0
    with np.errstate(over="ignore", invalid="ignore"):
        for _ in range(steps):
            if g_norm <= tolerance:
                status = "converged"
                break
            n_iter += 1
            candidate = theta - eta * g
            if not np.all(np.isfinite(candidate)):
                status = "diverged"
                break
            g_new = eval_grad(candidate)
            g_new_norm = float(np.linalg.norm(g_new)) if np.all(np.isfinite(g_new)) else float("inf")
            loss_new = eval_loss(candidate)
            if g_new_norm > limit or (loss is not None and not np.isfinite(loss_new)):
                status = "diverged"
                break
            theta, g, g_norm, loss_value = candidate, g_new, g_new_norm, loss_new
            if record_history:
                thetas.append(theta.copy())
            grad_norms.append(g_norm)
            if loss is not None:
                losses.append(loss_value)
    if status == "max_iter" and g_norm <= tolerance:
        status = "converged"

    return GradientDescentResult(
        theta=theta,
        converged=status == "converged",
        diverged=status == "diverged",
        status=status,
        n_iter=n_iter,
        grad_norm=g_norm,
        loss=loss_value if loss is not None else None,
        theta_history=np.array(thetas, dtype=np.float64).reshape(len(thetas), theta.size),
        grad_norm_history=np.array(grad_norms, dtype=np.float64),
        loss_history=np.array(losses, dtype=np.float64),
    )


@dataclass(frozen=True)
class MinimizeResult:
    """Outcome of :func:`minimize_loss` (``converged`` mirrors scipy's ``success`` plus finiteness)."""

    theta: FloatArray
    loss: float
    converged: bool
    n_iter: int
    n_fev: int
    message: str
    method: str


def minimize_loss(
    loss: Callable[[FloatArray], float],
    theta0: ArrayLike,
    *,
    method: Literal["L-BFGS-B", "SLSQP"] = "L-BFGS-B",
    grad: Callable[[FloatArray], ArrayLike] | None = None,
    bounds: Sequence[tuple[float | None, float | None]] | None = None,
    constraints: Sequence[Mapping[str, Any]] = (),
    tol: float | None = None,
    max_iter: int = 1_000,
) -> MinimizeResult:
    """4.2 Loss calibration, theta* = argmin L(theta), via scipy.optimize.minimize (L-BFGS-B or SLSQP).

    L-BFGS-B handles box ``bounds``; SLSQP additionally takes scipy-style ``constraints``
    dicts. Non-convergence is reported through ``converged`` rather than raised.
    """
    if method not in ("L-BFGS-B", "SLSQP"):
        raise ValueError("method must be 'L-BFGS-B' or 'SLSQP'")
    start = np.array(np.atleast_1d(_to_array(theta0, "theta0")), dtype=np.float64)
    if start.ndim != 1:
        raise ValueError(f"theta0 must be a scalar or 1-D vector, got shape {start.shape}")
    iterations = _to_positive_int(max_iter, "max_iter")
    constraint_list = list(constraints)
    if constraint_list and method != "SLSQP":
        raise ValueError("constraints require method='SLSQP' (L-BFGS-B supports bounds only)")
    if bounds is not None:
        bounds = list(bounds)
        if len(bounds) != start.size:
            raise ValueError(f"shape mismatch: {len(bounds)} bounds for {start.size} parameters")
        for lo, hi in bounds:
            if lo is not None and hi is not None and lo > hi:
                raise ValueError("each bound must satisfy lower <= upper")
    if tol is not None and (not np.isfinite(tol) or tol <= 0.0):
        raise ValueError("tol must be a positive finite number")
    initial = float(loss(start.copy()))
    if not np.isfinite(initial):
        raise ValueError("the loss is not finite at theta0")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = optimize.minimize(
            loss,
            start,
            jac=grad,
            method=method,
            bounds=bounds,
            constraints=constraint_list,
            tol=tol,
            options={"maxiter": iterations},
        )
    theta = np.asarray(res.x, dtype=np.float64)
    value = float(res.fun)
    return MinimizeResult(
        theta=theta,
        loss=value,
        converged=bool(res.success) and bool(np.isfinite(value)) and bool(np.all(np.isfinite(theta))),
        n_iter=int(getattr(res, "nit", 0)),
        n_fev=int(getattr(res, "nfev", 0)),
        message=str(res.message),
        method=method,
    )


# --------------------------------------------------------------------------- 4.3 Lagrange multipliers
@dataclass(frozen=True)
class EqualityConstrainedResult:
    """Constrained optimum with its Lagrange multipliers.

    ``multipliers[j]`` satisfies ``grad f(x*) = sum_j multipliers[j] * grad h_j(x*)``, so it
    is the shadow price ``d f* / d c_j`` of relaxing constraint ``h_j(x) = g_j(x) - c_j = 0``
    (same sign convention whether minimising or maximising). ``stationarity_residual`` is
    ``||grad f - J' lambda||`` and ``constraint_violation`` is ``max |h_j(x*)|``.
    """

    x: FloatArray
    objective: float
    multipliers: FloatArray
    constraint_violation: float
    stationarity_residual: float
    converged: bool
    n_iter: int
    message: str


def _central_jacobian(func: Callable[[FloatArray], FloatArray], x: FloatArray) -> FloatArray:
    """Central-difference Jacobian of a vector function, shape ``(m, n)``."""
    base = np.atleast_1d(np.asarray(func(x), dtype=np.float64))
    jac = np.empty((base.size, x.size))
    steps = np.cbrt(_EPS) * np.maximum(1.0, np.abs(x))
    for i in range(x.size):
        shift = np.zeros_like(x)
        shift[i] = steps[i]
        up = np.atleast_1d(np.asarray(func(x + shift), dtype=np.float64))
        down = np.atleast_1d(np.asarray(func(x - shift), dtype=np.float64))
        jac[:, i] = (up - down) / (2.0 * steps[i])
    return jac


def _stack_constraints(
    constraints: Callable[[FloatArray], ArrayLike] | Sequence[Callable[[FloatArray], ArrayLike]],
) -> Callable[[FloatArray], FloatArray]:
    funcs = [constraints] if callable(constraints) else list(constraints)
    if not funcs or not all(callable(f) for f in funcs):
        raise ValueError("constraints must be a callable or a non-empty sequence of callables")

    def stacked(x: FloatArray) -> FloatArray:
        return np.concatenate([np.atleast_1d(np.asarray(f(x), dtype=np.float64)).ravel() for f in funcs])

    return stacked


def solve_equality_constrained(
    objective: Callable[[FloatArray], float],
    constraints: Callable[[FloatArray], ArrayLike] | Sequence[Callable[[FloatArray], ArrayLike]],
    x0: ArrayLike,
    *,
    maximize: bool = False,
    grad: Callable[[FloatArray], ArrayLike] | None = None,
    constraint_jac: Callable[[FloatArray], ArrayLike] | None = None,
    tol: float = 1e-12,
    max_iter: int = 500,
    feasibility_tol: float = 1e-8,
) -> EqualityConstrainedResult:
    """4.3 Lagrange multipliers: optimise f(x) s.t. h(x) = 0, with grad f(x*) = sum_j lambda_j grad h_j(x*).

    ``constraints`` is a callable returning the residual vector ``h(x)`` or a sequence of
    callables. The optimum is found with SLSQP (central-difference derivatives unless
    ``grad`` / ``constraint_jac`` are supplied) and the multipliers by least squares on
    the stationarity condition at the optimum.
    """
    start = np.array(np.atleast_1d(_to_array(x0, "x0")), dtype=np.float64)
    if start.ndim != 1:
        raise ValueError(f"x0 must be a scalar or 1-D vector, got shape {start.shape}")
    iterations = _to_positive_int(max_iter, "max_iter")
    if not (np.isfinite(tol) and tol > 0.0):
        raise ValueError("tol must be a positive finite number")
    if not (np.isfinite(feasibility_tol) and feasibility_tol > 0.0):
        raise ValueError("feasibility_tol must be a positive finite number")
    residuals = _stack_constraints(constraints)
    sign = -1.0 if maximize else 1.0

    def value(x: FloatArray) -> float:
        return float(objective(x))

    def gradient(x: FloatArray) -> FloatArray:
        if grad is not None:
            g = np.asarray(grad(x), dtype=np.float64).ravel()
        else:
            g = _central_jacobian(lambda z: np.array([value(z)]), x)[0]
        if g.size != x.size:
            raise ValueError(f"grad returned {g.size} entries, expected {x.size}")
        return g

    def jacobian(x: FloatArray) -> FloatArray:
        if constraint_jac is not None:
            jac = np.atleast_2d(np.asarray(constraint_jac(x), dtype=np.float64))
        else:
            jac = _central_jacobian(residuals, x)
        if jac.shape[1] != x.size:
            raise ValueError(f"constraint Jacobian has {jac.shape[1]} columns, expected {x.size}")
        return jac

    if not np.isfinite(value(start)):
        raise ValueError("the objective is not finite at x0")
    h0 = residuals(start)
    if h0.size == 0 or not np.all(np.isfinite(h0)):
        raise ValueError("constraints must return at least one finite residual at x0")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = optimize.minimize(
            lambda x: sign * value(x),
            start,
            jac=lambda x: sign * gradient(x),
            method="SLSQP",
            constraints=[{"type": "eq", "fun": residuals, "jac": jacobian}],
            options={"ftol": tol, "maxiter": iterations},
        )
    x_opt = np.asarray(res.x, dtype=np.float64)
    finite = bool(np.all(np.isfinite(x_opt)))
    if finite:
        grad_f = gradient(x_opt)
        jac_h = jacobian(x_opt)
        multipliers, *_ = np.linalg.lstsq(jac_h.T, grad_f, rcond=None)
        stationarity = float(np.linalg.norm(grad_f - jac_h.T @ multipliers))
        violation = float(np.max(np.abs(residuals(x_opt))))
        objective_value = value(x_opt)
    else:
        multipliers = np.full(h0.size, np.nan)
        stationarity = violation = objective_value = float("nan")
    return EqualityConstrainedResult(
        x=x_opt,
        objective=objective_value,
        multipliers=np.asarray(multipliers, dtype=np.float64),
        constraint_violation=violation,
        stationarity_residual=stationarity,
        converged=bool(res.success) and finite and violation <= feasibility_tol,
        n_iter=int(getattr(res, "nit", 0)),
        message=str(res.message),
    )


def solve_equality_constrained_quadratic(
    q: ArrayLike,
    c: ArrayLike,
    a: ArrayLike,
    b: ArrayLike,
) -> EqualityConstrainedResult:
    """4.3 Lagrange multipliers, quadratic case: min 0.5 x'Qx + c'x s.t. Ax = b (KKT: Qx + c = A' lambda).

    Solved in closed form by the null-space method, which stays stable when the KKT matrix
    is ill-conditioned. Raises if ``Ax = b`` is inconsistent or the problem has no unique
    minimiser (``Q`` not positive definite on the null-space of ``A``).
    """
    lin = _to_vector(c, "c")
    n = lin.size
    quad = _to_symmetric(q, "q", n)
    a_mat = np.atleast_2d(_to_array(a, "a"))
    if a_mat.ndim != 2 or a_mat.shape[1] != n:
        raise ValueError(f"shape mismatch: a must have {n} columns, got shape {a_mat.shape}")
    rhs = np.atleast_1d(_to_array(b, "b"))
    if rhs.ndim != 1 or rhs.size != a_mat.shape[0]:
        raise ValueError(f"shape mismatch: b must have {a_mat.shape[0]} entries")

    _, sing, vt = np.linalg.svd(a_mat, full_matrices=True)
    cutoff = max(a_mat.shape) * _EPS * (float(sing[0]) if sing.size else 0.0)
    rank = int(np.count_nonzero(sing > cutoff))
    particular = np.linalg.pinv(a_mat) @ rhs
    scale = max(1.0, float(np.max(np.abs(rhs))))
    if float(np.max(np.abs(a_mat @ particular - rhs))) > 1e-8 * scale:
        raise ValueError("the equality constraints Ax = b are inconsistent")
    null_basis = vt[rank:].T
    x_opt = particular
    if null_basis.shape[1] > 0:
        reduced = null_basis.T @ quad @ null_basis
        reduced = 0.5 * (reduced + reduced.T)
        eigenvalues = np.linalg.eigvalsh(reduced)
        hess_scale = max(float(np.max(np.abs(quad))), _TINY)
        if eigenvalues[0] <= 1e-12 * hess_scale:
            raise ValueError(
                "no unique minimiser: q is not positive definite on the null-space of a "
                "(the problem is unbounded or degenerate)"
            )
        x_opt = particular - null_basis @ np.linalg.solve(reduced, null_basis.T @ (quad @ particular + lin))
    grad_f = quad @ x_opt + lin
    multipliers, *_ = np.linalg.lstsq(a_mat.T, grad_f, rcond=None)
    return EqualityConstrainedResult(
        x=x_opt,
        objective=float(0.5 * x_opt @ quad @ x_opt + lin @ x_opt),
        multipliers=multipliers,
        constraint_violation=float(np.max(np.abs(a_mat @ x_opt - rhs))),
        stationarity_residual=float(np.linalg.norm(grad_f - a_mat.T @ multipliers)),
        converged=True,
        n_iter=0,
        message="closed-form null-space solution",
    )


@dataclass(frozen=True, eq=False)
class PortfolioConstraints:
    """Constraint set shared by the cvxpy portfolio optimisers (weights are fractions of capital).

    * ``dollar_neutral`` - ``sum(w) == 0``; ``net_exposure`` - ``sum(w) == value`` (1.0 is
      fully invested). Giving both with a non-zero ``net_exposure`` is an error.
    * ``betas`` - per-name betas; enforces ``|betas' w - beta_target| <= beta_tolerance``
      (beta-neutral with the defaults ``0`` and ``0``).
    * ``sectors`` - one label per name. ``sector_net_cap`` bounds ``|sum_{i in s} w_i|`` and
      ``sector_gross_cap`` bounds ``sum_{i in s} |w_i|``; each is a single cap for every
      sector or a mapping ``label -> cap`` (unlisted sectors are unconstrained).
    * ``gross_leverage`` - margin limit ``sum |w_i| <= L``.
    * ``lower_bound`` / ``upper_bound`` - per-name bounds, scalar or one per name
      (``+-inf`` entries mean unbounded).
    """

    dollar_neutral: bool = False
    net_exposure: float | None = None
    betas: ArrayLike | None = None
    beta_target: float = 0.0
    beta_tolerance: float = 0.0
    sectors: Sequence[Hashable] | None = None
    sector_net_cap: float | Mapping[Hashable, float] | None = None
    sector_gross_cap: float | Mapping[Hashable, float] | None = None
    gross_leverage: float | None = None
    lower_bound: ArrayLike | None = None
    upper_bound: ArrayLike | None = None


@dataclass(frozen=True, eq=False)
class PortfolioSolution:
    """Solved portfolio.

    ``objective = expected_return - (gamma/2) variance - transaction_cost``;
    ``turnover = sum |w - w_prev|``. ``duals`` maps each constraint name to its Lagrange
    multiplier in cvxpy's sign convention (non-negative for inequalities; zero when the
    constraint is slack): a float for ``net_exposure``, ``beta``, ``gross_leverage`` and
    ``sector_net[label]`` / ``sector_gross[label]``, and a length-n vector in asset order
    for ``lower_bound`` / ``upper_bound`` (zero for names without a finite bound).
    """

    weights: FloatArray
    objective: float
    expected_return: float
    variance: float
    turnover: float
    transaction_cost: float
    gross_leverage: float
    net_exposure: float
    status: str
    solver: str
    duals: Mapping[str, float | FloatArray]


def _bound_vector(value: ArrayLike | None, name: str, n: int, default: float) -> FloatArray:
    if value is None:
        return np.full(n, default)
    try:
        arr = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if np.any(np.isnan(arr)):
        raise ValueError(f"{name} must not contain NaN")
    if arr.ndim > 1 or (arr.ndim == 1 and arr.size != n):
        raise ValueError(f"shape mismatch: {name} must be a scalar or have {n} entries")
    return np.broadcast_to(arr, (n,)).astype(np.float64)


def _sector_caps(
    cap: float | Mapping[Hashable, float] | None,
    name: str,
    groups: Mapping[Hashable, NDArray[np.intp]] | None,
) -> dict[Hashable, float]:
    if cap is None:
        return {}
    if groups is None:
        raise ValueError(f"{name} requires sectors")
    if isinstance(cap, Mapping):
        unknown = [label for label in cap if label not in groups]
        if unknown:
            raise ValueError(f"{name} refers to unknown sectors: {unknown!r}")
        caps = {label: _to_scalar(value, name) for label, value in cap.items()}
    else:
        scalar = _to_scalar(cap, name)
        caps = {label: scalar for label in groups}
    if any(value < 0.0 for value in caps.values()):
        raise ValueError(f"{name} must be >= 0")
    return caps


_NamedConstraint = tuple[str, Any, NDArray[np.intp] | None]


def _build_constraints(w: cp.Variable, n: int, spec: PortfolioConstraints) -> list[_NamedConstraint]:
    """Translate a :class:`PortfolioConstraints` into named cvxpy constraints (validating it).

    Each entry is ``(name, constraint, asset_index)``; ``asset_index`` is set for per-name
    constraints (bounds) and ``None`` for scalar ones.
    """
    named: list[_NamedConstraint] = []

    net = None if spec.net_exposure is None else _to_scalar(spec.net_exposure, "net_exposure")
    if spec.dollar_neutral:
        if net is not None and net != 0.0:
            raise ValueError("dollar_neutral conflicts with a non-zero net_exposure")
        net = 0.0
    if net is not None:
        named.append(("net_exposure", cp.sum(w) == net, None))

    beta_target = _to_scalar(spec.beta_target, "beta_target")
    beta_tol = _to_scalar(spec.beta_tolerance, "beta_tolerance")
    if beta_tol < 0.0:
        raise ValueError("beta_tolerance must be >= 0")
    if spec.betas is not None:
        betas = _to_vector(spec.betas, "betas", size=n)
        if beta_tol == 0.0:
            named.append(("beta", betas @ w == beta_target, None))
        else:
            named.append(("beta", cp.abs(betas @ w - beta_target) <= beta_tol, None))
    elif beta_target != 0.0 or beta_tol != 0.0:
        raise ValueError("beta_target and beta_tolerance require betas")

    if spec.gross_leverage is not None:
        gross = _to_scalar(spec.gross_leverage, "gross_leverage")
        if gross <= 0.0:
            raise ValueError("gross_leverage must be > 0")
        named.append(("gross_leverage", cp.norm1(w) <= gross, None))

    lower = _bound_vector(spec.lower_bound, "lower_bound", n, -np.inf)
    upper = _bound_vector(spec.upper_bound, "upper_bound", n, np.inf)
    if np.any(lower > upper):
        raise ValueError("lower_bound must not exceed upper_bound")
    if np.any(np.isposinf(lower)) or np.any(np.isneginf(upper)):
        raise ValueError("lower_bound cannot be +inf and upper_bound cannot be -inf")
    has_lower = np.isfinite(lower)
    has_upper = np.isfinite(upper)
    # Only names with a finite bound are constrained; the index travels with the constraint so
    # its duals can be reported per asset (length n, zero for unbounded names).
    if has_lower.any():
        idx = np.flatnonzero(has_lower)
        named.append(("lower_bound", w[idx] >= lower[idx], idx))
    if has_upper.any():
        idx = np.flatnonzero(has_upper)
        named.append(("upper_bound", w[idx] <= upper[idx], idx))

    groups: dict[Hashable, NDArray[np.intp]] | None = None
    if spec.sectors is not None:
        labels = list(spec.sectors)
        if len(labels) != n:
            raise ValueError(f"shape mismatch: sectors has {len(labels)} labels for {n} assets")
        members: dict[Hashable, list[int]] = {}
        for i, label in enumerate(labels):
            members.setdefault(label, []).append(i)
        groups = {label: np.array(idx, dtype=np.intp) for label, idx in members.items()}
    for label, cap in _sector_caps(spec.sector_net_cap, "sector_net_cap", groups).items():
        named.append((f"sector_net[{label}]", cp.abs(cp.sum(w[groups[label]])) <= cap, None))
    for label, cap in _sector_caps(spec.sector_gross_cap, "sector_gross_cap", groups).items():
        named.append((f"sector_gross[{label}]", cp.norm1(w[groups[label]]) <= cap, None))
    return named


def _risk_root(sigma: FloatArray) -> FloatArray:
    """Rows ``sqrt(lambda_k) v_k'`` so that ``||root w||^2 = w' Sigma w``; validates PSD-ness."""
    values, vectors = np.linalg.eigh(sigma)
    top = max(float(np.max(np.abs(values))), _TINY)
    if values[0] < -1e-8 * top:
        raise ValueError(
            f"cov is not positive semi-definite (smallest eigenvalue {values[0]:.3e}); "
            "project it to PSD or shrink it first"
        )
    keep = values > values.size * _EPS * top
    return (vectors[:, keep] * np.sqrt(values[keep])).T


def _check_solvers(solvers: Sequence[str]) -> tuple[str, ...]:
    if isinstance(solvers, str):
        solvers = (solvers,)
    names = tuple(solvers)
    if not names or not all(isinstance(s, str) and s for s in names):
        raise ValueError("solvers must be a non-empty sequence of solver names")
    return tuple(s.upper() for s in names)


def _solve_with_fallback(
    problem: cp.Problem,
    variable: cp.Variable,
    solvers: tuple[str, ...],
) -> tuple[FloatArray, str, str, list[Any]]:
    """Try each solver in turn; return ``(weights, status, solver, duals)`` or raise a clear error.

    A solution is accepted only if the solver reports ``optimal`` and it passes an
    independent constraint-violation check. An ``optimal_inaccurate`` solution is kept as
    a last resort and returned only if no later solver does better. ``duals`` is aligned
    with ``problem.constraints``.
    """
    failures: list[str] = []
    inaccurate: tuple[FloatArray, str, str, list[Any]] | None = None
    for name in solvers:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                problem.solve(solver=name, **dict(_SOLVER_OPTIONS.get(name, {})))
        except Exception as exc:  # any backend failure means "try the next solver"
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        status = str(problem.status)
        if status == cp.INFEASIBLE:
            raise InfeasibleProblemError(
                f"the portfolio constraints are infeasible ({name} returned an infeasibility certificate)"
            )
        if status == cp.UNBOUNDED:
            raise UnboundedProblemError(
                f"the portfolio problem is unbounded ({name}); add risk aversion, leverage or position limits"
            )
        value = variable.value
        if status not in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE) or value is None:
            failures.append(f"{name}: status {status}")
            continue
        weights = np.asarray(value, dtype=np.float64).ravel()
        if not np.all(np.isfinite(weights)):
            failures.append(f"{name}: non-finite solution")
            continue
        violation = max((float(np.max(con.violation())) for con in problem.constraints), default=0.0)
        duals = [con.dual_value for con in problem.constraints]
        if status == cp.OPTIMAL and violation <= _FEASIBILITY_TOL:
            return weights, status, name, duals
        if violation <= _INACCURATE_FEASIBILITY_TOL and inaccurate is None:
            inaccurate = (weights, cp.OPTIMAL_INACCURATE, name, duals)
        failures.append(f"{name}: status {status}, max constraint violation {violation:.2e}")
    if inaccurate is not None:
        return inaccurate
    raise OptimizationError("all solvers failed: " + "; ".join(failures))


def _solve_portfolio(
    expected_returns: ArrayLike,
    cov: ArrayLike,
    risk_aversion: float,
    w_prev: ArrayLike | None,
    linear_cost_bps: ArrayLike,
    quadratic_cost: ArrayLike,
    constraints: PortfolioConstraints | None,
    solvers: Sequence[str],
) -> PortfolioSolution:
    mu = _to_vector(expected_returns, "expected_returns")
    n = mu.size
    sigma = _to_cov(cov, n)
    gamma = _to_scalar(risk_aversion, "risk_aversion")
    if gamma < 0.0:
        raise ValueError("risk_aversion must be >= 0")
    previous = np.zeros(n) if w_prev is None else _to_vector(w_prev, "w_prev", size=n)
    linear = _per_name(linear_cost_bps, "linear_cost_bps", n) * 1e-4
    quadratic = _per_name(quadratic_cost, "quadratic_cost", n)
    if constraints is not None and not isinstance(constraints, PortfolioConstraints):
        raise ValueError("constraints must be a PortfolioConstraints instance or None")
    solver_names = _check_solvers(solvers)
    root = _risk_root(sigma)

    # The argmax is invariant to a positive rescaling of the objective, but solver stopping
    # tolerances are absolute: intraday-sized inputs (mu ~ 1e-6, Sigma ~ 1e-8) would be declared
    # "optimal" far from the optimum. Normalise the objective to O(1) and rescale the duals back.
    scale = max(
        float(np.max(np.abs(mu))),
        float(np.max(linear)),
        float(np.max(quadratic)),
        gamma * float(np.max(np.abs(sigma))),
    )
    if scale <= 0.0:
        scale = 1.0

    w = cp.Variable(n)
    named = _build_constraints(w, n, constraints if constraints is not None else PortfolioConstraints())
    utility = (mu / scale) @ w
    if gamma > 0.0 and root.shape[0] > 0:
        utility = utility - 0.5 * gamma * cp.sum_squares((root / np.sqrt(scale)) @ w)
    trade = w - previous
    if np.any(linear > 0.0):
        utility = utility - (linear / scale) @ cp.abs(trade)
    if np.any(quadratic > 0.0):
        utility = utility - (quadratic / scale) @ cp.square(trade)
    problem = cp.Problem(cp.Maximize(utility), [con for _, con, _ in named])
    weights, status, solver, dual_values = _solve_with_fallback(problem, w, solver_names)

    delta = weights - previous
    variance = max(float(weights @ sigma @ weights), 0.0)
    expected = float(mu @ weights)
    cost = float(linear @ np.abs(delta) + quadratic @ (delta * delta))
    duals: dict[str, float | FloatArray] = {}
    for (label, _, asset_index), dual in zip(named, dual_values, strict=True):
        if dual is None:
            continue
        dual_arr = np.asarray(dual, dtype=np.float64).ravel() * scale
        if asset_index is None:
            duals[label] = float(dual_arr[0])
        else:
            per_asset = np.zeros(n)
            per_asset[asset_index] = dual_arr
            duals[label] = per_asset
    return PortfolioSolution(
        weights=weights,
        objective=expected - 0.5 * gamma * variance - cost,
        expected_return=expected,
        variance=variance,
        turnover=float(np.sum(np.abs(delta))),
        transaction_cost=cost,
        gross_leverage=float(np.sum(np.abs(weights))),
        net_exposure=float(np.sum(weights)),
        status=status,
        solver=solver,
        duals=MappingProxyType(duals),
    )


def optimize_constrained_portfolio(
    expected_returns: ArrayLike,
    cov: ArrayLike,
    constraints: PortfolioConstraints,
    *,
    risk_aversion: float = 1.0,
    solvers: Sequence[str] = DEFAULT_SOLVERS,
) -> PortfolioSolution:
    """4.3 Constrained optimisation (KKT): maximize mu'w - (gamma/2) w'Sigma w s.t. the constraint set.

    Supports dollar-neutral, beta-neutral, per-sector caps, gross-leverage (margin) and
    per-name bounds through :class:`PortfolioConstraints`. The Lagrange multipliers of the
    constraints are returned in ``duals``. Solvers are tried in order (CLARABEL, OSQP, SCS).
    """
    if not isinstance(constraints, PortfolioConstraints):
        raise ValueError("constraints must be a PortfolioConstraints instance")
    return _solve_portfolio(expected_returns, cov, risk_aversion, None, 0.0, 0.0, constraints, solvers)


# --------------------------------------------------------------------------- 4.4 Markowitz
def markowitz_closed_form(
    expected_returns: ArrayLike,
    cov: ArrayLike,
    risk_aversion: float = 1.0,
    *,
    ridge: float = 0.0,
    max_condition: float = 1e12,
) -> FloatArray:
    """4.4 Markowitz closed form: w* = (1/gamma) Sigma^-1 mu.

    The unconstrained maximiser of ``mu'w - (gamma/2) w'Sigma w``. ``Sigma + ridge * I``
    must be positive definite with condition number at most ``max_condition``; otherwise
    a ``ValueError`` explains that the covariance needs shrinkage or a ridge, instead of
    returning weights dominated by estimation noise.
    """
    mu = _to_vector(expected_returns, "expected_returns")
    sigma = _to_cov(cov, mu.size)
    gamma = _to_scalar(risk_aversion, "risk_aversion")
    if gamma <= 0.0:
        raise ValueError("risk_aversion must be > 0")
    penalty = _to_scalar(ridge, "ridge")
    if penalty < 0.0:
        raise ValueError("ridge must be >= 0")
    limit = _to_scalar(max_condition, "max_condition")
    if limit <= 1.0:
        raise ValueError("max_condition must be > 1")
    values, vectors = np.linalg.eigh(sigma)
    values = values + penalty
    if values[0] <= 0.0 or values[-1] / values[0] > limit:
        kappa = float("inf") if values[0] <= 0.0 else float(values[-1] / values[0])
        raise ValueError(
            f"cov is singular or ill-conditioned (condition number {kappa:.3e} > {limit:.1e}); "
            "shrink the covariance or pass ridge > 0"
        )
    return vectors @ ((vectors.T @ mu) / values) / gamma


def mean_variance_optimize(
    expected_returns: ArrayLike,
    cov: ArrayLike,
    *,
    risk_aversion: float = 1.0,
    w_prev: ArrayLike | None = None,
    linear_cost_bps: ArrayLike = 0.0,
    quadratic_cost: ArrayLike = 0.0,
    constraints: PortfolioConstraints | None = None,
    solvers: Sequence[str] = DEFAULT_SOLVERS,
) -> PortfolioSolution:
    """4.4 Cost-aware mean-variance: maximize mu'w - (gamma/2) w'Sigma w - TC(w - w_prev).

    ``TC(d) = sum_i c_i |d_i| + sum_i q_i d_i^2`` with ``c = linear_cost_bps * 1e-4``
    (spread/fees, scalar or per name) and ``q = quadratic_cost`` (impact, optional).
    ``w_prev`` defaults to an all-cash book. Constraints are those of
    :class:`PortfolioConstraints`. Solvers are tried in order (CLARABEL, then OSQP, SCS);
    :class:`InfeasibleProblemError` / :class:`OptimizationError` is raised if the problem
    is infeasible or every solver fails.
    """
    return _solve_portfolio(
        expected_returns, cov, risk_aversion, w_prev, linear_cost_bps, quadratic_cost, constraints, solvers
    )


# --------------------------------------------------------------------------- 4.5 convexity / Jensen
@dataclass(frozen=True)
class JensenGap:
    """Sample Jensen gap ``E[f(X)] - f(E[X])`` with a delta-method standard error."""

    gap: float
    mean_of_f: float
    f_of_mean: float
    std_err: float
    n: int


def jensen_gap(samples: ArrayLike, func: Callable[[FloatArray], ArrayLike]) -> JensenGap:
    """4.5 Jensen's inequality: gap = E[f(X)] - f(E[X]) >= 0 for convex f (<= 0 for concave f).

    ``func`` must be vectorised. The standard error uses the delta method,
    ``sd(f(X) - f'(x_bar) X) / sqrt(n)``, so the gap can be compared with its sampling noise.
    """
    x = _to_vector(samples, "samples")
    n = x.size
    if n < 2:
        raise ValueError(f"samples needs at least 2 observations, got {n}")

    def evaluate(points: FloatArray) -> FloatArray:
        try:
            with np.errstate(all="ignore"):  # non-finite values are reported below, not warned about
                out = np.asarray(func(points), dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise ValueError("func must map a float array to a float array") from exc
        if out.shape != points.shape:
            raise ValueError("func must be vectorised: it has to return an array shaped like its input")
        if not np.all(np.isfinite(out)):
            raise ValueError("func returned non-finite values on the samples or at their mean")
        return out

    fx = evaluate(x)
    mean_x = float(np.mean(x))
    f_of_mean = float(evaluate(np.array([mean_x]))[0])
    mean_of_f = float(np.mean(fx))
    lo, hi = float(np.min(x)), float(np.max(x))
    h = min(1e-6 * (hi - lo), 0.5 * (mean_x - lo), 0.5 * (hi - mean_x))
    if h <= 0.0 or mean_x + h == mean_x:
        # all samples (numerically) identical: the gap is exactly zero and has no sampling noise
        return JensenGap(gap=0.0, mean_of_f=mean_of_f, f_of_mean=f_of_mean, std_err=0.0, n=n)
    around = evaluate(np.array([mean_x - h, mean_x + h]))
    with np.errstate(over="ignore", invalid="ignore"):
        slope = float((around[1] - around[0]) / (2.0 * h))
        linearised = fx - slope * x
        unit = float(np.max(np.abs(linearised)))
    if not np.isfinite(unit):
        raise ValueError("the Jensen gap overflows float64: rescale the samples to sensible units")
    # unit-scale before squaring so the standard deviation cannot overflow
    spread = unit * float(np.std(linearised / unit, ddof=1)) if unit > 0.0 else 0.0
    std_err = spread / float(np.sqrt(n))
    return JensenGap(
        gap=mean_of_f - f_of_mean, mean_of_f=mean_of_f, f_of_mean=f_of_mean, std_err=std_err, n=n
    )


def volatility_drag(volatility: ArrayLike) -> float | FloatArray:
    """4.5 Volatility drag: r_arith - r_geom ~ 0.5 sigma^2."""
    sigma = _to_array(volatility, "volatility")
    if np.any(sigma < 0.0):
        raise ValueError("volatility must be >= 0")
    return _unwrap(0.5 * sigma * sigma)


def geometric_return_approx(arithmetic_return: ArrayLike, volatility: ArrayLike) -> float | FloatArray:
    """4.5 Geometric growth under volatility drag: r_geom ~ r_arith - 0.5 sigma^2."""
    r = _to_array(arithmetic_return, "arithmetic_return")
    drag = np.asarray(volatility_drag(volatility), dtype=np.float64)
    r, drag = _broadcast(("arithmetic_return", "volatility"), r, drag)
    return _unwrap(r - drag)


def _to_simple_returns(returns: ArrayLike, *, allow_empty: bool) -> FloatArray:
    r = np.atleast_1d(_to_array(returns, "returns", allow_empty=allow_empty))
    if np.any(r < -1.0):
        raise ValueError("simple returns cannot be below -100%")
    return r


def compound_returns(returns: ArrayLike) -> float | FloatArray:
    """4.5 Exact compounding: R_total = prod_t (1 + r_t) - 1, along the last axis.

    An empty sequence compounds to 0 (nothing happened); a -100% return gives -1.
    """
    r = _to_simple_returns(returns, allow_empty=True)
    return _unwrap(np.prod(1.0 + r, axis=-1) - 1.0)


def geometric_mean_return(returns: ArrayLike) -> float | FloatArray:
    """4.5 Geometric mean return: r_geom = (prod_t (1 + r_t))^(1/n) - 1, along the last axis."""
    r = _to_simple_returns(returns, allow_empty=False)
    with np.errstate(divide="ignore"):
        mean_log = np.mean(np.log1p(r), axis=-1)
    return _unwrap(np.expm1(mean_log))


@dataclass(frozen=True)
class ConvexityScore:
    """Convexity of a payoff sampled on a grid.

    ``score`` is the net change in slope divided by the total absolute change in slope:
    +1 for a convex payoff (long gamma), -1 for a concave one (short gamma), 0 for a
    linear payoff, in between for mixed shapes. ``second_derivatives`` are the divided
    second differences at the interior grid points; ``chord_gap`` is the mean of
    ``chord - payoff`` over the grid (positive when the payoff sags below its chord).
    """

    score: float
    is_convex: bool
    is_concave: bool
    is_linear: bool
    second_derivatives: FloatArray
    mean_second_derivative: float
    chord_gap: float


def convexity_score(grid: ArrayLike, payoff: ArrayLike, *, rel_tol: float = 1e-9) -> ConvexityScore:
    """4.5 Convexity score from second differences: sum(d slope) / sum |d slope| in [-1, 1].

    ``grid`` must be strictly increasing with at least 3 points (uniform spacing is not
    required). Slope changes smaller than ``rel_tol`` times the largest slope magnitude
    are treated as zero so floating-point noise on a linear payoff does not register.
    """
    x = _to_vector(grid, "grid")
    f = _to_vector(payoff, "payoff", size=x.size)
    if x.size < 3:
        raise ValueError("need at least 3 grid points to measure convexity")
    if not (np.isfinite(rel_tol) and rel_tol >= 0.0):
        raise ValueError("rel_tol must be a non-negative finite number")
    dx = np.diff(x)
    if np.any(dx <= 0.0):
        raise ValueError("grid must be strictly increasing")
    slopes = np.diff(f) / dx
    d_slope = np.diff(slopes)
    noise = rel_tol * max(float(np.max(np.abs(slopes))), _TINY)
    d_slope = np.where(np.abs(d_slope) <= noise, 0.0, d_slope)
    total = float(np.sum(np.abs(d_slope)))
    net = float(np.sum(d_slope))
    span = 0.5 * (x[2:] - x[:-2])
    second = d_slope / span
    chord = f[0] + (f[-1] - f[0]) * (x - x[0]) / (x[-1] - x[0])
    gap = chord - f
    chord_gap = float(np.sum(0.5 * (gap[1:] + gap[:-1]) * dx) / (x[-1] - x[0]))
    is_linear = total == 0.0
    return ConvexityScore(
        score=0.0 if is_linear else net / total,
        is_convex=bool(not is_linear and np.all(d_slope >= 0.0)),
        is_concave=bool(not is_linear and np.all(d_slope <= 0.0)),
        is_linear=is_linear,
        second_derivatives=second,
        mean_second_derivative=net / float(np.sum(span)),
        chord_gap=0.0 if is_linear else chord_gap,
    )
