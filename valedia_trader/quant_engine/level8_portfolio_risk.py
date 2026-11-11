"""Level 8: portfolio and risk management. Sizing functions never exceed their caps and risk
checks fail closed: invalid or missing inputs mean "no trade" or "breach", never a silent default."""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import cvxpy as cp
import numpy as np
from scipy import stats
from sklearn.covariance import LedoitWolf

__all__ = [
    "capm_beta", "CAPMResult", "beta_hedged_returns", "factor_regression", "FactorModel",
    "factor_neutralizing_hedge", "factor_risk_decomposition", "sharpe_ratio", "annualize_sharpe",
    "sortino_ratio", "max_drawdown", "MaxDrawdown", "calmar_ratio", "information_ratio", "kelly_fraction",
    "kelly_multi_event", "kelly_continuous", "kelly_continuous_from_returns", "clamp_kelly_multiplier",
    "fractional_kelly", "regime_kelly_multiplier", "apply_leverage_cap", "KELLY_MULTIPLIER_MIN",
    "KELLY_MULTIPLIER_MAX", "var_parametric", "es_parametric", "var_historical", "es_historical",
    "var_es_monte_carlo", "running_drawdown", "DrawdownCircuitBreaker", "BreachDecision",
]

FORMULAS: dict[str, tuple[str, ...]] = {
    "8.1": ("capm_beta", "beta_hedged_returns"),
    "8.2": ("factor_regression", "factor_neutralizing_hedge", "factor_risk_decomposition"),
    "8.3": ("sharpe_ratio", "annualize_sharpe", "sortino_ratio", "calmar_ratio", "information_ratio",
            "max_drawdown"),
    "8.4": ("kelly_fraction", "kelly_multi_event", "kelly_continuous", "kelly_continuous_from_returns",
            "fractional_kelly", "clamp_kelly_multiplier", "regime_kelly_multiplier", "apply_leverage_cap"),
    "8.5": ("var_parametric", "es_parametric", "var_historical", "es_historical", "var_es_monte_carlo",
            "running_drawdown", "DrawdownCircuitBreaker"),
}

KELLY_MULTIPLIER_MIN = 0.15
KELLY_MULTIPLIER_MAX = 0.50


def _no_dispersion(sd: float, values: np.ndarray) -> bool:
    """True when a standard deviation is zero up to floating-point noise."""
    return sd <= 1e-12 * max(1.0, float(np.max(np.abs(values))))


def _vec(x, name: str, min_len: int = 2) -> np.ndarray:
    a = np.asarray(x, dtype=float).ravel()
    if a.size < min_len:
        raise ValueError(f"{name} needs at least {min_len} observations")
    if not np.all(np.isfinite(a)):
        raise ValueError(f"{name} contains NaN or infinite values")
    return a


# ---------------------------------------------------------------- 8.1 CAPM
@dataclass(frozen=True)
class CAPMResult:
    beta: float
    alpha: float        # per period
    alpha_se: float
    alpha_t: float
    r_squared: float
    n_obs: int


def capm_beta(asset_returns, market_returns, rf=0.0) -> CAPMResult:
    """8.1 CAPM: E[R_i] - R_f = beta_i (E[R_m] - R_f) + alpha_i, with beta = Cov(R_i, R_m) / Var(R_m)."""
    y = _vec(asset_returns, "asset_returns", 3) - rf
    x = _vec(market_returns, "market_returns", 3) - rf
    if y.size != x.size:
        raise ValueError("asset and market returns must have the same length")
    xc = x - x.mean()
    sxx = float(xc @ xc)
    if sxx == 0.0:
        raise ValueError("market returns have zero variance; beta is undefined")
    beta = float(xc @ (y - y.mean()) / sxx)
    alpha = float(y.mean() - beta * x.mean())
    resid = y - alpha - beta * x
    n = y.size
    s2 = float(resid @ resid) / (n - 2)
    alpha_se = math.sqrt(s2 * (1.0 / n + x.mean() ** 2 / sxx))
    sst = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float(resid @ resid) / sst if sst > 0 else 0.0
    alpha_t = alpha / alpha_se if alpha_se > 0 else (math.inf if alpha else 0.0)
    return CAPMResult(beta, alpha, alpha_se, alpha_t, r2, n)


def beta_hedged_returns(asset_returns, market_returns, beta: float | None = None, rf=0.0) -> np.ndarray:
    """8.1 Pure-alpha stream: (R_i - R_f) - beta (R_m - R_f). Capital then funds alpha, not market beta."""
    y = _vec(asset_returns, "asset_returns", 1) - rf
    x = _vec(market_returns, "market_returns", 1) - rf
    if y.size != x.size:
        raise ValueError("asset and market returns must have the same length")
    if beta is None:
        beta = capm_beta(asset_returns, market_returns, rf).beta
    return y - beta * x


# ---------------------------------------------------------------- 8.2 multi-factor model
@dataclass(frozen=True)
class FactorModel:
    alpha: np.ndarray           # (N,)
    betas: np.ndarray           # (N, K)
    residuals: np.ndarray       # (T, N)
    r_squared: np.ndarray       # (N,)
    factor_cov: np.ndarray      # (K, K)
    specific_var: np.ndarray    # (N,)


def factor_regression(returns, factors, rf=0.0) -> FactorModel:
    """8.2 Multi-factor model R_i - R_f = alpha_i + sum_k beta_ik F_k + eps_i by time-series OLS.

    `factors` may hold Fama-French, momentum, crypto or macro factors: one column per factor."""
    y = np.asarray(returns, dtype=float)
    y = (y.reshape(-1, 1) if y.ndim == 1 else y) - rf
    f = np.asarray(factors, dtype=float)
    f = f.reshape(-1, 1) if f.ndim == 1 else f
    if y.shape[0] != f.shape[0]:
        raise ValueError("returns and factors must have the same number of rows")
    t, k = f.shape
    if t <= k + 1:
        raise ValueError("need more observations than factors + 1")
    if not (np.all(np.isfinite(y)) and np.all(np.isfinite(f))):
        raise ValueError("inputs contain NaN or infinite values")
    design = np.column_stack([np.ones(t), f])
    if np.linalg.matrix_rank(design) < k + 1:
        raise ValueError("factors are collinear (or constant); loadings are not identified")
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    resid = y - design @ coef
    sst = ((y - y.mean(axis=0)) ** 2).sum(axis=0)
    r2 = np.where(sst > 0, 1.0 - (resid**2).sum(axis=0) / np.where(sst > 0, sst, 1.0), 0.0)
    return FactorModel(coef[0], coef[1:].T, resid, r2, np.atleast_2d(np.cov(f, rowvar=False)),
                       (resid**2).sum(axis=0) / (t - k - 1))


def factor_neutralizing_hedge(weights, betas, hedge_betas, factors_to_neutralize=None) -> tuple[np.ndarray, np.ndarray]:
    """8.2 Hedge weights h that remove chosen factor exposures: solve hedge_betas^T h = -(betas^T w).

    Returns (hedge weights, residual exposure after hedging) for the selected factors."""
    w = np.asarray(weights, dtype=float).ravel()
    b = np.atleast_2d(np.asarray(betas, dtype=float))
    hb = np.atleast_2d(np.asarray(hedge_betas, dtype=float))
    if b.shape[0] != w.size or hb.shape[1] != b.shape[1]:
        raise ValueError("shape mismatch between weights, betas and hedge_betas")
    cols = np.arange(b.shape[1]) if factors_to_neutralize is None else np.asarray(factors_to_neutralize, dtype=int)
    exposure = b[:, cols].T @ w
    h, *_ = np.linalg.lstsq(hb[:, cols].T, -exposure, rcond=None)
    return h, exposure + hb[:, cols].T @ h


def factor_risk_decomposition(weights, betas, factor_cov, specific_var) -> dict[str, float]:
    """8.2 Portfolio variance split into factor risk (b^T F b) and specific risk (sum w_i^2 s_i^2)."""
    w = np.asarray(weights, dtype=float).ravel()
    b = np.atleast_2d(np.asarray(betas, dtype=float)).T @ w
    factor = float(b @ np.atleast_2d(np.asarray(factor_cov, dtype=float)) @ b)
    specific = float(w**2 @ np.asarray(specific_var, dtype=float).ravel())
    return {"factor_variance": factor, "specific_variance": specific, "total_variance": factor + specific}


# ---------------------------------------------------------------- 8.3 performance ratios
def annualize_sharpe(sharpe_per_period: float, periods_per_year: float = 252.0) -> float:
    """8.3 Annualize a per-period Sharpe ratio: SR_annual = SR_period * sqrt(periods per year)."""
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    return sharpe_per_period * math.sqrt(periods_per_year)


def sharpe_ratio(returns, rf=0.0, periods_per_year: float = 252.0) -> float:
    """8.3 Sharpe ratio: E[R_p - R_f] / sigma_p * sqrt(N periods). Zero-volatility streams return 0."""
    ex = _vec(returns, "returns") - rf
    sd = ex.std(ddof=1)
    return 0.0 if _no_dispersion(sd, ex) else annualize_sharpe(float(ex.mean() / sd), periods_per_year)


def sortino_ratio(returns, target=0.0, periods_per_year: float = 252.0) -> float:
    """8.3 Sortino ratio: mean excess over target divided by downside deviation, annualized."""
    ex = _vec(returns, "returns") - target
    downside = math.sqrt(float(np.mean(np.minimum(ex, 0.0) ** 2)))
    if _no_dispersion(downside, ex):
        return math.inf if ex.mean() > 0 else 0.0
    return float(ex.mean() / downside) * math.sqrt(periods_per_year)


@dataclass(frozen=True)
class MaxDrawdown:
    depth: float        # positive fraction, e.g. 0.25 for a 25% drawdown
    peak_index: int     # index into the equity curve (0 = starting capital)
    trough_index: int


def running_drawdown(equity) -> np.ndarray:
    """8.5 Running drawdown of an equity curve: 1 - equity / running peak (0 at new highs)."""
    e = _vec(equity, "equity", 1)
    if np.any(e <= 0):
        raise ValueError("equity must stay positive")
    return 1.0 - e / np.maximum.accumulate(e)


def max_drawdown(returns) -> MaxDrawdown:
    """8.3 Maximum drawdown of the compounded equity curve built from simple returns."""
    r = _vec(returns, "returns", 1)
    if np.any(r <= -1.0):
        raise ValueError("a return of -100% or worse wipes out the equity curve")
    equity = np.concatenate([[1.0], np.cumprod(1.0 + r)])
    dd = running_drawdown(equity)
    trough = int(np.argmax(dd))
    return MaxDrawdown(float(dd[trough]), int(np.argmax(equity[: trough + 1])), trough)


def calmar_ratio(returns, periods_per_year: float = 252.0) -> float:
    """8.3 Calmar ratio: annualized compound return divided by maximum drawdown."""
    r = _vec(returns, "returns")
    dd = max_drawdown(r).depth
    growth = float(np.prod(1.0 + r))
    cagr = growth ** (periods_per_year / r.size) - 1.0
    if dd == 0:
        return math.inf if cagr > 0 else 0.0
    return cagr / dd


def information_ratio(returns, benchmark_returns, periods_per_year: float = 252.0) -> float:
    """8.3 Information ratio: annualized mean active return divided by tracking error."""
    a = _vec(returns, "returns")
    b = _vec(benchmark_returns, "benchmark_returns")
    if a.size != b.size:
        raise ValueError("returns and benchmark must have the same length")
    active = a - b
    te = active.std(ddof=1)
    return 0.0 if _no_dispersion(te, active) else float(active.mean() / te) * math.sqrt(periods_per_year)


# ---------------------------------------------------------------- 8.4 Kelly
def kelly_fraction(p, b):
    """8.4 Kelly criterion (discrete): f* = (p b - q) / b, floored at 0 for negative-edge bets."""
    pa, ba = np.asarray(p, dtype=float), np.asarray(b, dtype=float)
    if np.any((pa < 0) | (pa > 1)) or not np.all(np.isfinite(pa)):
        raise ValueError("p must be a probability in [0, 1]")
    if np.any(ba <= 0) or not np.all(np.isfinite(ba)):
        raise ValueError("net odds b must be positive")
    f = np.maximum((pa * ba - (1.0 - pa)) / ba, 0.0)
    return float(f) if f.ndim == 0 else f


def kelly_multi_event(probs, odds, budget: float = 1.0, max_events: int = 12) -> np.ndarray:
    """8.4 Simultaneous Kelly for independent binary bets: maximize E[log(1 + sum_i f_i x_i)] over the
    joint outcome distribution subject to f >= 0 and sum f <= budget. Negative-edge bets get exactly 0."""
    p, b = np.asarray(probs, dtype=float).ravel(), np.asarray(odds, dtype=float).ravel()
    if p.size != b.size or p.size == 0:
        raise ValueError("probs and odds must be non-empty and the same length")
    if p.size > max_events:
        raise ValueError(f"at most {max_events} simultaneous events are supported (2^n outcomes)")
    if not 0 < budget <= 1.0:
        raise ValueError("budget must be in (0, 1]")
    single = np.atleast_1d(kelly_fraction(p, b))
    out = np.zeros(p.size)
    live = np.flatnonzero(single > 0)
    if live.size == 0:
        return out
    pl, bl = p[live], b[live]
    outcomes = np.array(list(itertools.product([0, 1], repeat=live.size)), dtype=float)
    prob = np.prod(np.where(outcomes == 1, pl, 1.0 - pl), axis=1)
    payoff = np.where(outcomes == 1, bl, -1.0)
    f = cp.Variable(live.size, nonneg=True)
    problem = cp.Problem(cp.Maximize(prob @ cp.log1p(payoff @ f)), [cp.sum(f) <= budget * (1.0 - 1e-9)])
    for solver in (cp.CLARABEL, cp.SCS):
        try:
            problem.solve(solver=solver)
        except cp.SolverError:
            continue
        if problem.status in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE) and f.value is not None:
            sol = np.clip(np.asarray(f.value, dtype=float), 0.0, None)
            sol[sol < 1e-7] = 0.0
            if sol.sum() > budget:
                sol *= budget / sol.sum()
            out[live] = sol
            return out
    raise RuntimeError("multi-event Kelly optimization failed with every solver")


def kelly_continuous(mu, cov, rf=0.0) -> np.ndarray:
    """8.4 Continuous multi-asset Kelly: f* = Sigma^{-1} (mu - r_f). Scale it with fractional_kelly."""
    m = np.asarray(mu, dtype=float).ravel() - rf
    s = np.atleast_2d(np.asarray(cov, dtype=float))
    if s.shape != (m.size, m.size):
        raise ValueError("cov must be (n, n) matching mu")
    if not (np.all(np.isfinite(m)) and np.all(np.isfinite(s))):
        raise ValueError("inputs contain NaN or infinite values")
    s = 0.5 * (s + s.T)
    eig = np.linalg.eigvalsh(s)
    if eig.min() <= 0 or eig.max() / eig.min() > 1e12:
        raise ValueError("covariance is singular or ill-conditioned; shrink it before sizing")
    return np.linalg.solve(s, m)


def kelly_continuous_from_returns(returns, rf=0.0, shrinkage: bool = True) -> np.ndarray:
    """8.4 Continuous Kelly from a (T, N) return history using Ledoit-Wolf shrunk covariance."""
    r = np.asarray(returns, dtype=float)
    if r.ndim != 2 or r.shape[0] <= r.shape[1] + 1:
        raise ValueError("returns must be (T, N) with T > N + 1")
    if not np.all(np.isfinite(r)):
        raise ValueError("returns contain NaN or infinite values")
    cov = LedoitWolf().fit(r).covariance_ if shrinkage else np.cov(r, rowvar=False)
    return kelly_continuous(r.mean(axis=0), cov, rf)


def clamp_kelly_multiplier(multiplier: float) -> float:
    """8.4 Clamp a fractional-Kelly multiplier to [0.15, 0.50]; unusable input gets the minimum."""
    if multiplier is None or not np.isfinite(multiplier):
        return KELLY_MULTIPLIER_MIN
    return float(min(max(multiplier, KELLY_MULTIPLIER_MIN), KELLY_MULTIPLIER_MAX))


def fractional_kelly(full_kelly, multiplier: float):
    """8.4 Fractional Kelly: clamp(multiplier) * f*. Non-finite sizes become 0 (no trade)."""
    f = np.asarray(full_kelly, dtype=float)
    out = np.where(np.isfinite(f), f, 0.0) * clamp_kelly_multiplier(multiplier)
    return float(out) if out.ndim == 0 else out


def regime_kelly_multiplier(regime_probs: dict[str, float], regime_multipliers: dict[str, float]) -> float:
    """8.4 Probability-weighted Kelly multiplier across regimes, clamped to [0.15, 0.50].

    A regime with no configured multiplier, or unusable probabilities, counts at the minimum."""
    total = sum(v for v in regime_probs.values() if np.isfinite(v) and v >= 0)
    if not regime_probs or total <= 0 or any(not np.isfinite(v) or v < 0 for v in regime_probs.values()):
        return KELLY_MULTIPLIER_MIN
    blended = sum(p / total * clamp_kelly_multiplier(regime_multipliers.get(name, KELLY_MULTIPLIER_MIN))
                  for name, p in regime_probs.items())
    return clamp_kelly_multiplier(blended)


def apply_leverage_cap(weights, max_gross: float) -> np.ndarray:
    """8.4 Scale weights down so gross exposure sum |w| never exceeds max_gross."""
    if max_gross < 0:
        raise ValueError("max_gross must be non-negative")
    w = np.asarray(weights, dtype=float).copy()
    w[~np.isfinite(w)] = 0.0
    gross = float(np.abs(w).sum())
    return w if gross <= max_gross or gross == 0 else w * (max_gross / gross)


# ---------------------------------------------------------------- 8.5 VaR, ES, circuit breaker
def _check_alpha(alpha: float) -> None:
    if not 0.5 < alpha < 1.0:
        raise ValueError("alpha must be in (0.5, 1), e.g. 0.95")


def _t_scale(df: float) -> float:
    if df is None or df <= 2:
        raise ValueError("Student-t needs df > 2")
    return math.sqrt((df - 2.0) / df)


def var_parametric(sigma: float, mu: float = 0.0, alpha: float = 0.95, value: float = 1.0, horizon: float = 1.0,
                   dist: str = "normal", df: float | None = None) -> float:
    """8.5 Parametric VaR_alpha as a positive loss: value * (z_alpha sigma sqrt(h) - mu h)."""
    _check_alpha(alpha)
    if sigma < 0 or value < 0 or horizon <= 0 or not np.isfinite(sigma):
        raise ValueError("sigma, value must be non-negative and finite, horizon positive")
    if dist == "normal":
        z = stats.norm.ppf(alpha)
    elif dist == "t":
        z = stats.t.ppf(alpha, df) * _t_scale(df)
    else:
        raise ValueError("dist must be 'normal' or 't'")
    return float(value * (z * sigma * math.sqrt(horizon) - mu * horizon))


def es_parametric(sigma: float, mu: float = 0.0, alpha: float = 0.95, value: float = 1.0, horizon: float = 1.0,
                  dist: str = "normal", df: float | None = None) -> float:
    """8.5 Parametric expected shortfall ES_alpha = E[L | L > VaR_alpha] as a positive loss."""
    _check_alpha(alpha)
    if sigma < 0 or value < 0 or horizon <= 0 or not np.isfinite(sigma):
        raise ValueError("sigma, value must be non-negative and finite, horizon positive")
    if dist == "normal":
        k = stats.norm.pdf(stats.norm.ppf(alpha)) / (1.0 - alpha)
    elif dist == "t":
        q = stats.t.ppf(alpha, df)
        k = stats.t.pdf(q, df) / (1.0 - alpha) * (df + q**2) / (df - 1.0) * _t_scale(df)
    else:
        raise ValueError("dist must be 'normal' or 't'")
    return float(value * (k * sigma * math.sqrt(horizon) - mu * horizon))


def var_historical(returns, alpha: float = 0.95, value: float = 1.0) -> float:
    """8.5 Historical VaR: the alpha-quantile of realized losses, as a positive loss."""
    _check_alpha(alpha)
    losses = -_vec(returns, "returns", 20)
    return float(value * np.quantile(losses, alpha))


def es_historical(returns, alpha: float = 0.95, value: float = 1.0) -> float:
    """8.5 Historical ES: mean of the losses at or beyond the historical VaR."""
    _check_alpha(alpha)
    losses = -_vec(returns, "returns", 20)
    var = np.quantile(losses, alpha)
    return float(value * losses[losses >= var].mean())


def var_es_monte_carlo(mu, cov, weights, rng: np.random.Generator, alpha: float = 0.95, value: float = 1.0,
                       n_sims: int = 100_000, df: float | None = None) -> tuple[float, float]:
    """8.5 Monte Carlo (VaR, ES) for a portfolio with normal (or Student-t when df is given) returns."""
    _check_alpha(alpha)
    m, w = np.asarray(mu, dtype=float).ravel(), np.asarray(weights, dtype=float).ravel()
    s = np.atleast_2d(np.asarray(cov, dtype=float))
    if s.shape != (m.size, m.size) or w.size != m.size:
        raise ValueError("mu, cov and weights have inconsistent shapes")
    if n_sims < 1000:
        raise ValueError("n_sims must be at least 1000")
    draws = rng.multivariate_normal(np.zeros(m.size), s, size=n_sims, method="eigh")
    if df is not None:
        scale = _t_scale(df)
        draws = draws * scale / np.sqrt(rng.chisquare(df, size=(n_sims, 1)) / df)
    losses = -((draws + m) @ w)
    var = float(np.quantile(losses, alpha))
    return value * var, float(value * losses[losses >= var].mean())


@dataclass(frozen=True)
class BreachDecision:
    breached: bool
    reasons: tuple[str, ...]
    latched: bool


@dataclass
class DrawdownCircuitBreaker:
    """8.5 Kill switch on daily loss, VaR, ES and drawdown limits (all positive fractions of equity).

    Once tripped it stays latched until reset(); NaN, missing or non-positive inputs trip it."""
    max_daily_loss: float
    max_var: float
    max_es: float
    max_drawdown: float
    peak_equity: float | None = None
    latched: bool = False
    trip_reasons: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in ("max_daily_loss", "max_var", "max_es", "max_drawdown"):
            v = getattr(self, name)
            if not np.isfinite(v) or v <= 0:
                raise ValueError(f"{name} must be a positive finite fraction")

    def check(self, equity: float, daily_return: float, var: float, es: float) -> BreachDecision:
        """Evaluate limits. `daily_return` is today's P&L as a fraction of equity; var/es are positive
        loss fractions. Any breach latches the breaker."""
        reasons: list[str] = []
        inputs = {"equity": equity, "daily_return": daily_return, "var": var, "es": es}
        for name, v in inputs.items():
            if v is None or not np.isfinite(v):
                reasons.append(f"{name} missing or not finite")
        if not reasons:
            if equity <= 0:
                reasons.append("equity is not positive")
            else:
                self.peak_equity = equity if self.peak_equity is None else max(self.peak_equity, equity)
                dd = 1.0 - equity / self.peak_equity
                if dd > self.max_drawdown:
                    reasons.append(f"drawdown {dd:.4f} exceeds {self.max_drawdown:.4f}")
            if -daily_return > self.max_daily_loss:
                reasons.append(f"daily loss {-daily_return:.4f} exceeds {self.max_daily_loss:.4f}")
            if var > self.max_var:
                reasons.append(f"VaR {var:.4f} exceeds {self.max_var:.4f}")
            if es > self.max_es:
                reasons.append(f"ES {es:.4f} exceeds {self.max_es:.4f}")
        if reasons:
            self.latched = True
            self.trip_reasons = tuple(reasons)
            return BreachDecision(True, tuple(reasons), True)
        if self.latched:
            return BreachDecision(True, ("latched: " + "; ".join(self.trip_reasons),), True)
        return BreachDecision(False, (), False)

    def reset(self, authorized_by: str, new_peak_equity: float | None = None) -> None:
        """Explicitly clear the latch. Requires a named authorizer; optionally rebase the equity peak."""
        if not authorized_by or not authorized_by.strip():
            raise ValueError("reset requires the name of whoever authorized it")
        if new_peak_equity is not None and (not np.isfinite(new_peak_equity) or new_peak_equity <= 0):
            raise ValueError("new_peak_equity must be positive and finite")
        self.latched = False
        self.trip_reasons = ()
        if new_peak_equity is not None:
            self.peak_equity = new_peak_equity
