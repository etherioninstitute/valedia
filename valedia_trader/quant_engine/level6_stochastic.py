"""Level 6: stochastic calculus. Vectorized Monte Carlo; all randomness comes from the
`rng: np.random.Generator` the caller passes in."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy import stats

__all__ = [
    "random_walk_paths", "random_walk_sd", "random_walk_expected_abs_distance", "brownian_paths",
    "nearest_psd_correlation", "correlated_brownian_increments", "quadratic_variation", "gbm_terminal",
    "gbm_paths", "merton_jump_paths", "mc_european_option", "MCResult", "ito_coefficients", "ItoResult",
    "ito_log_price", "binary_call_value", "binary_contract_dynamics", "gambler_ruin_win_probability",
    "gambler_ruin_expected_duration", "brownian_hit_upper_first_probability",
    "gbm_take_profit_before_stop_probability", "mc_first_passage_probability", "martingale_check",
    "MartingaleCheck",
]

FORMULAS: dict[str, tuple[str, ...]] = {
    "6.1": ("random_walk_paths", "random_walk_sd", "random_walk_expected_abs_distance"),
    "6.2": ("brownian_paths", "correlated_brownian_increments", "nearest_psd_correlation", "quadratic_variation"),
    "6.3": ("gbm_terminal", "gbm_paths", "merton_jump_paths", "mc_european_option"),
    "6.4": ("ito_coefficients", "ito_log_price", "binary_call_value", "binary_contract_dynamics"),
    "6.5": ("gambler_ruin_win_probability", "gambler_ruin_expected_duration",
            "brownian_hit_upper_first_probability", "gbm_take_profit_before_stop_probability",
            "mc_first_passage_probability", "martingale_check"),
}


def _positive_int(value: int, name: str) -> None:
    if int(value) != value or value < 1:
        raise ValueError(f"{name} must be a positive integer")


# ---------------------------------------------------------------- 6.1 random walk
def random_walk_paths(n_steps: int, n_paths: int, rng: np.random.Generator, sigma: float = 1.0,
                      p_up: float = 0.5) -> np.ndarray:
    """6.1 Random walk X_N = sum eps_i with eps = +-sigma; shape (n_paths, n_steps + 1), X_0 = 0."""
    _positive_int(n_steps, "n_steps")
    _positive_int(n_paths, "n_paths")
    if not 0.0 <= p_up <= 1.0 or sigma < 0:
        raise ValueError("p_up must be in [0, 1] and sigma non-negative")
    steps = np.where(rng.random((n_paths, n_steps)) < p_up, sigma, -sigma)
    out = np.zeros((n_paths, n_steps + 1))
    out[:, 1:] = np.cumsum(steps, axis=1)
    return out


def random_walk_sd(n_steps: int, sigma: float = 1.0) -> float:
    """6.1 Square-root-of-time scaling: SD(X_N) = sigma * sqrt(N)."""
    _positive_int(n_steps, "n_steps")
    return sigma * math.sqrt(n_steps)


def random_walk_expected_abs_distance(n_steps: int, sigma: float = 1.0) -> float:
    """6.1 Exact E|X_N| for the symmetric +-sigma walk (approaches sigma * sqrt(2N/pi))."""
    _positive_int(n_steps, "n_steps")
    m = (n_steps + 1) // 2
    log_term = math.lgamma(2 * m + 1) - 2.0 * math.lgamma(m + 1) - 2 * m * math.log(2.0)
    return sigma * 2 * m * math.exp(log_term)


# ---------------------------------------------------------------- 6.2 Brownian motion
def brownian_paths(n_paths: int, n_steps: int, T: float, rng: np.random.Generator) -> np.ndarray:
    """6.2 Standard Brownian motion: W_t - W_s ~ N(0, t - s); shape (n_paths, n_steps + 1), W_0 = 0."""
    _positive_int(n_paths, "n_paths")
    _positive_int(n_steps, "n_steps")
    if T <= 0:
        raise ValueError("T must be positive")
    dw = rng.standard_normal((n_paths, n_steps)) * math.sqrt(T / n_steps)
    out = np.zeros((n_paths, n_steps + 1))
    out[:, 1:] = np.cumsum(dw, axis=1)
    return out


def nearest_psd_correlation(corr, eps: float = 1e-10) -> np.ndarray:
    """6.2 Repair a slightly non-PSD correlation matrix by eigenvalue clipping (unit diagonal kept)."""
    c = np.asarray(corr, dtype=float)
    if c.ndim != 2 or c.shape[0] != c.shape[1]:
        raise ValueError("correlation matrix must be square")
    if not np.all(np.isfinite(c)):
        raise ValueError("correlation matrix contains NaN or infinite values")
    c = 0.5 * (c + c.T)
    w, v = np.linalg.eigh(c)
    if w.min() >= eps:
        return c
    fixed = (v * np.maximum(w, eps)) @ v.T
    d = np.sqrt(np.diag(fixed))
    fixed = fixed / np.outer(d, d)
    return 0.5 * (fixed + fixed.T)


def correlated_brownian_increments(corr, n_paths: int, n_steps: int, dt: float,
                                   rng: np.random.Generator) -> np.ndarray:
    """6.2 Correlated increments with dW_i dW_j = rho_ij dt via Cholesky; shape (n_paths, n_steps, d)."""
    _positive_int(n_paths, "n_paths")
    _positive_int(n_steps, "n_steps")
    if dt <= 0:
        raise ValueError("dt must be positive")
    c = nearest_psd_correlation(corr)
    chol = np.linalg.cholesky(c)
    z = rng.standard_normal((n_paths, n_steps, c.shape[0]))
    return (z @ chol.T) * math.sqrt(dt)


def quadratic_variation(path) -> np.ndarray:
    """6.2 Realized quadratic variation sum (dW)^2 along the last axis (converges to T for W)."""
    p = np.asarray(path, dtype=float)
    if p.shape[-1] < 2:
        raise ValueError("path needs at least two points")
    return np.sum(np.diff(p, axis=-1) ** 2, axis=-1)


# ---------------------------------------------------------------- 6.3 GBM and jumps
def _check_gbm(S0: float, sigma: float, T: float) -> None:
    if S0 <= 0 or sigma < 0 or T <= 0:
        raise ValueError("require S0 > 0, sigma >= 0, T > 0")


def _normals(shape: tuple[int, ...], rng: np.random.Generator, antithetic: bool) -> np.ndarray:
    if not antithetic:
        return rng.standard_normal(shape)
    if shape[0] % 2:
        raise ValueError("antithetic sampling needs an even number of paths")
    half = rng.standard_normal((shape[0] // 2, *shape[1:]))
    return np.concatenate([half, -half], axis=0)


def gbm_terminal(S0: float, mu: float, sigma: float, T: float, n_paths: int, rng: np.random.Generator,
                 antithetic: bool = False) -> np.ndarray:
    """6.3 Exact GBM terminal value: S_T = S_0 exp((mu - sigma^2/2) T + sigma W_T)."""
    _check_gbm(S0, sigma, T)
    _positive_int(n_paths, "n_paths")
    z = _normals((n_paths,), rng, antithetic)
    return S0 * np.exp((mu - 0.5 * sigma**2) * T + sigma * math.sqrt(T) * z)


def gbm_paths(S0: float, mu: float, sigma: float, T: float, n_steps: int, n_paths: int,
              rng: np.random.Generator, antithetic: bool = False) -> np.ndarray:
    """6.3 Exact log-Euler GBM paths dS = mu S dt + sigma S dW; shape (n_paths, n_steps + 1)."""
    _check_gbm(S0, sigma, T)
    _positive_int(n_steps, "n_steps")
    _positive_int(n_paths, "n_paths")
    dt = T / n_steps
    z = _normals((n_paths, n_steps), rng, antithetic)
    log_inc = (mu - 0.5 * sigma**2) * dt + sigma * math.sqrt(dt) * z
    out = np.empty((n_paths, n_steps + 1))
    out[:, 0] = S0
    out[:, 1:] = S0 * np.exp(np.cumsum(log_inc, axis=1))
    return out


def merton_jump_paths(S0: float, mu: float, sigma: float, T: float, n_steps: int, n_paths: int,
                      rng: np.random.Generator, jump_intensity: float, jump_mean: float,
                      jump_std: float) -> np.ndarray:
    """6.3 Merton jump-diffusion dS = mu S dt + sigma S dW + S dJ with the drift compensated so
    E[S_T] = S_0 exp(mu T). Jumps are Poisson(lambda) with lognormal sizes N(jump_mean, jump_std^2)."""
    _check_gbm(S0, sigma, T)
    _positive_int(n_steps, "n_steps")
    _positive_int(n_paths, "n_paths")
    if jump_intensity < 0 or jump_std < 0:
        raise ValueError("jump_intensity and jump_std must be non-negative")
    dt = T / n_steps
    k = math.exp(jump_mean + 0.5 * jump_std**2) - 1.0
    counts = rng.poisson(jump_intensity * dt, size=(n_paths, n_steps))
    jumps = counts * jump_mean + np.sqrt(counts) * jump_std * rng.standard_normal((n_paths, n_steps))
    diff = (mu - jump_intensity * k - 0.5 * sigma**2) * dt + sigma * math.sqrt(dt) * rng.standard_normal(
        (n_paths, n_steps))
    out = np.empty((n_paths, n_steps + 1))
    out[:, 0] = S0
    out[:, 1:] = S0 * np.exp(np.cumsum(diff + jumps, axis=1))
    return out


@dataclass(frozen=True)
class MCResult:
    price: float
    std_error: float
    n_paths: int


def mc_european_option(S0: float, K: float, r: float, sigma: float, T: float, n_paths: int,
                       rng: np.random.Generator, option: str = "call", antithetic: bool = False,
                       control_variate: bool = False) -> MCResult:
    """6.3 Monte Carlo European option price with optional antithetic and control variates.

    The control variate is the discounted terminal price, whose risk-neutral mean is S_0."""
    if K <= 0:
        raise ValueError("K must be positive")
    if option not in ("call", "put"):
        raise ValueError("option must be 'call' or 'put'")
    st = gbm_terminal(S0, r, sigma, T, n_paths, rng, antithetic)
    disc = math.exp(-r * T)
    payoff = disc * (np.maximum(st - K, 0.0) if option == "call" else np.maximum(K - st, 0.0))
    control = disc * st
    if antithetic:
        h = n_paths // 2
        payoff, control = 0.5 * (payoff[:h] + payoff[h:]), 0.5 * (control[:h] + control[h:])
    if control_variate:
        var_c = float(np.var(control, ddof=1))
        if var_c > 0:
            beta = float(np.cov(payoff, control, ddof=1)[0, 1]) / var_c
            payoff = payoff - beta * (control - S0)
    n = payoff.size
    return MCResult(float(payoff.mean()), float(payoff.std(ddof=1) / math.sqrt(n)), n_paths)


# ---------------------------------------------------------------- 6.4 Ito's lemma
@dataclass(frozen=True)
class ItoResult:
    drift: float       # coefficient of dt
    diffusion: float   # coefficient of dW


def ito_coefficients(f: Callable[[float, float], float], S: float, t: float, mu: float, sigma: float,
                     f_t: Callable[[float, float], float] | None = None,
                     f_S: Callable[[float, float], float] | None = None,
                     f_SS: Callable[[float, float], float] | None = None,
                     rel_step: float = 1e-4) -> ItoResult:
    """6.4 Ito's lemma for f(S, t) with dS = mu S dt + sigma S dW:

    df = (f_t + mu S f_S + 0.5 sigma^2 S^2 f_SS) dt + sigma S f_S dW.
    Partials default to central finite differences; analytic partials may be supplied."""
    if S <= 0:
        raise ValueError("S must be positive")
    hs, ht = rel_step * S, rel_step
    d_t = f_t(S, t) if f_t else (f(S, t + ht) - f(S, t - ht)) / (2 * ht)
    d_s = f_S(S, t) if f_S else (f(S + hs, t) - f(S - hs, t)) / (2 * hs)
    d_ss = f_SS(S, t) if f_SS else (f(S + hs, t) - 2.0 * f(S, t) + f(S - hs, t)) / hs**2
    drift = d_t + mu * S * d_s + 0.5 * sigma**2 * S**2 * d_ss
    return ItoResult(float(drift), float(sigma * S * d_s))


def ito_log_price(mu: float, sigma: float) -> ItoResult:
    """6.4 Ito applied to f = ln S: d ln S = (mu - sigma^2/2) dt + sigma dW."""
    return ItoResult(mu - 0.5 * sigma**2, sigma)


def binary_call_value(S: float, t: float, K: float, r: float, sigma: float, T: float) -> float:
    """6.4 Value of a cash-or-nothing binary paying 1 if S_T > K: e^{-r tau} N(d2), tau = T - t."""
    tau = T - t
    if S <= 0 or K <= 0 or sigma <= 0 or tau <= 0:
        raise ValueError("require S, K, sigma > 0 and t < T")
    d2 = (math.log(S / K) + (r - 0.5 * sigma**2) * tau) / (sigma * math.sqrt(tau))
    return math.exp(-r * tau) * float(stats.norm.cdf(d2))


def binary_contract_dynamics(S: float, t: float, K: float, r: float, sigma: float, T: float,
                             mu: float) -> ItoResult:
    """6.4 Drift and diffusion of a binary (prediction-market style) contract value under drift mu."""
    return ito_coefficients(lambda s, u: binary_call_value(s, u, K, r, sigma, T), S, t, mu, sigma)


# ---------------------------------------------------------------- 6.5 martingales and ruin
def gambler_ruin_win_probability(start: float, target: float, p: float = 0.5) -> float:
    """6.5 Probability of reaching `target` before 0 from `start` with unit bets won with probability p."""
    if not 0 < start < target:
        raise ValueError("require 0 < start < target")
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    if abs(p - 0.5) < 1e-12:
        return start / target
    ratio = (1.0 - p) / p
    return (1.0 - ratio**start) / (1.0 - ratio**target)


def gambler_ruin_expected_duration(start: float, target: float, p: float = 0.5) -> float:
    """6.5 Expected number of unit bets until absorption at 0 or `target`."""
    win = gambler_ruin_win_probability(start, target, p)
    if abs(p - 0.5) < 1e-12:
        return start * (target - start)
    return (target * win - start) / (2.0 * p - 1.0)


def brownian_hit_upper_first_probability(x0: float, lower: float, upper: float, mu: float = 0.0,
                                         sigma: float = 1.0) -> float:
    """6.5 P(drifted Brownian motion started at x0 hits `upper` before `lower`).

    Uses the scale function s(x) = exp(-2 mu x / sigma^2); with mu = 0 it is (x0 - L) / (U - L)."""
    if not lower < x0 < upper:
        raise ValueError("require lower < x0 < upper")
    if sigma <= 0:
        raise ValueError("sigma must be positive")
    if abs(mu) < 1e-14:
        return (x0 - lower) / (upper - lower)
    a = -2.0 * mu / sigma**2
    return float(np.expm1(a * (x0 - lower)) / np.expm1(a * (upper - lower)))


def gbm_take_profit_before_stop_probability(S0: float, take_profit: float, stop_loss: float, mu: float,
                                            sigma: float) -> float:
    """6.5 P(a GBM price hits take_profit before stop_loss): ln S is Brownian with drift mu - sigma^2/2."""
    if not 0 < stop_loss < S0 < take_profit:
        raise ValueError("require 0 < stop_loss < S0 < take_profit")
    return brownian_hit_upper_first_probability(math.log(S0), math.log(stop_loss), math.log(take_profit),
                                                mu - 0.5 * sigma**2, sigma)


def mc_first_passage_probability(x0: float, lower: float, upper: float, mu: float, sigma: float, dt: float,
                                 n_paths: int, rng: np.random.Generator,
                                 max_steps: int = 200_000) -> tuple[float, float]:
    """6.5 Monte Carlo estimate (probability, standard error) of hitting `upper` before `lower`.

    Paths still alive after max_steps count as not having hit the upper barrier."""
    if not lower < x0 < upper or sigma <= 0 or dt <= 0:
        raise ValueError("require lower < x0 < upper, sigma > 0 and dt > 0")
    _positive_int(n_paths, "n_paths")
    x = np.full(n_paths, float(x0))
    alive = np.ones(n_paths, dtype=bool)
    hit_upper = np.zeros(n_paths, dtype=bool)
    sd = sigma * math.sqrt(dt)
    for _ in range(max_steps):
        idx = np.flatnonzero(alive)
        if idx.size == 0:
            break
        x[idx] += mu * dt + sd * rng.standard_normal(idx.size)
        up, down = x[idx] >= upper, x[idx] <= lower
        hit_upper[idx[up]] = True
        alive[idx[up | down]] = False
    p = float(hit_upper.mean())
    return p, math.sqrt(max(p * (1.0 - p), 0.0) / n_paths)


@dataclass(frozen=True)
class MartingaleCheck:
    mean_increment: float
    p_value: float        # smallest Bonferroni-adjusted p-value across the tests run
    is_martingale: bool   # True when no test rejects at `alpha`


def martingale_check(increments, conditioning=None, alpha: float = 0.01) -> MartingaleCheck:
    """6.5 Test E[M_t - M_s | F_s] = 0: zero mean increments, and no dependence of the increment on
    information known beforehand (`conditioning`, defaulting to the previous increment)."""
    d = np.asarray(increments, dtype=float).ravel()
    if d.size < 30 or not np.all(np.isfinite(d)):
        raise ValueError("need at least 30 finite increments")
    if np.ptp(d) == 0.0:
        return MartingaleCheck(float(d[0]), 1.0 if d[0] == 0 else 0.0, bool(d[0] == 0))
    pvals = [float(stats.ttest_1samp(d, 0.0).pvalue)]
    if conditioning is None:
        y, c = d[1:], d[:-1]
    else:
        c = np.asarray(conditioning, dtype=float).ravel()
        if c.size != d.size:
            raise ValueError("conditioning must have the same length as increments")
        y = d
    if np.ptp(c) > 0:
        pvals.append(float(stats.pearsonr(y, c).pvalue))
    p = min(1.0, min(pvals) * len(pvals))
    return MartingaleCheck(float(d.mean()), p, bool(p >= alpha))
