"""Level 2 - statistics primitives (formulas 2.1 to 2.5).

Self-contained, pure numpy/scipy/statsmodels implementations used to decide whether an
edge is real: standard errors, Sharpe t-stats and promotion gates, OLS/robust hedge
ratios, correlation and lead-lag scans, and multiple-testing corrections.

Conventions
-----------
* Array-likes are accepted everywhere; scalar inputs give Python floats back.
* Invalid input raises ``ValueError``. Undefined statistics (correlation of a constant
  series, Sharpe of a zero-volatility series, ...) raise instead of returning NaN.
* Rolling/scanning outputs that cannot be defined for every element carry an explicit
  boolean ``valid`` mask next to the NaN placeholders.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from numpy.typing import ArrayLike, NDArray
from scipy import stats
from statsmodels.robust import norms as _sm_norms
from statsmodels.robust.robust_linear_model import RLM as _SmRLM

__all__ = [
    "FORMULAS",
    "LeadLagResult",
    "MeanEstimate",
    "MultipleTestResult",
    "OLSResult",
    "PromotionGate",
    "RobustRegressionResult",
    "RollingHedgeRatio",
    "adjust_pvalues",
    "benjamini_hochberg",
    "benjamini_yekutieli",
    "bonferroni",
    "correlation",
    "family_wise_error_rate",
    "holm_bonferroni",
    "lead_lag_cross_correlation",
    "mean_confidence_interval",
    "ols",
    "ols_slope",
    "r_squared",
    "robust_regression",
    "rolling_hedge_ratio",
    "sharpe_promotion_gate",
    "sharpe_ratio",
    "sharpe_t_stat",
    "sharpe_t_stat_from_returns",
    "sharpe_t_stat_per_period",
    "standard_error",
    "standard_error_of_mean",
]

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
BoolArray = NDArray[np.bool_]

FORMULAS: dict[str, tuple[str, ...]] = {
    "2.1": ("standard_error", "standard_error_of_mean", "mean_confidence_interval"),
    "2.2": (
        "sharpe_ratio",
        "sharpe_t_stat",
        "sharpe_t_stat_per_period",
        "sharpe_t_stat_from_returns",
        "sharpe_promotion_gate",
    ),
    "2.3": ("ols", "ols_slope", "robust_regression", "rolling_hedge_ratio"),
    "2.4": ("correlation", "r_squared", "lead_lag_cross_correlation"),
    "2.5": (
        "family_wise_error_rate",
        "bonferroni",
        "holm_bonferroni",
        "benjamini_hochberg",
        "benjamini_yekutieli",
        "adjust_pvalues",
    ),
}

_EPS = float(np.finfo(np.float64).eps)
_ROLLING_BLOCK_ELEMENTS = 1 << 21  # ~16 MB of float64 scratch per block of rolling windows


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


def _to_series(x: ArrayLike, name: str, *, min_n: int = 1) -> FloatArray:
    arr = _to_array(x, name)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be 1-D, got shape {arr.shape}")
    if arr.size < min_n:
        raise ValueError(f"{name} needs at least {min_n} observations, got {arr.size}")
    return arr


def _unwrap(arr: ArrayLike) -> float | FloatArray:
    out = np.asarray(arr, dtype=np.float64)
    return float(out) if out.ndim == 0 else out


def _broadcast(names: tuple[str, ...], *arrays: FloatArray) -> list[FloatArray]:
    try:
        return list(np.broadcast_arrays(*arrays))
    except ValueError as exc:
        shapes = ", ".join(f"{n}{a.shape}" for n, a in zip(names, arrays, strict=True))
        raise ValueError(f"shape mismatch: cannot broadcast {shapes}") from exc


def _is_constant(x: FloatArray) -> bool:
    """True when ``x`` has no variation beyond floating-point noise."""
    spread = float(np.max(x) - np.min(x))
    return spread <= 16.0 * _EPS * max(float(np.max(np.abs(x))), np.finfo(float).tiny)


def _magnitude(x: FloatArray) -> float:
    """Largest absolute value (0.0 for an all-zero array)."""
    return float(np.max(np.abs(x)))


def _scaled_std(x: FloatArray, ddof: int) -> float:
    """Standard deviation evaluated on unit-scaled data, so the squares cannot over- or underflow."""
    scale = _magnitude(x)
    if scale == 0.0 or _is_constant(x):
        return 0.0
    return scale * float(np.std(x / scale, ddof=ddof))


def _centred_unit(x: FloatArray) -> tuple[FloatArray, float]:
    """Return ``(u, s)`` with ``x - mean(x) = s * u`` and ``max|u| = 1`` (``x`` must not be constant).

    Sums of squares and cross products of ``u`` are O(n) whatever the units of ``x``, which
    keeps correlations and slopes exact for very small or very large inputs.
    """
    top = _magnitude(x)
    z = x / top
    deviation = z - z.mean()
    spread = float(np.max(np.abs(deviation)))
    return deviation / spread, spread * top


def _safe_ratio(numerator: FloatArray, denominator: FloatArray) -> FloatArray:
    """Elementwise ``numerator / denominator`` with 0/0 := 0 and x/0 := +-inf (t-stat convention)."""
    num = np.asarray(numerator, dtype=np.float64)
    den = np.asarray(denominator, dtype=np.float64)
    out = np.zeros(np.broadcast(num, den).shape, dtype=np.float64)
    np.divide(num, den, out=out, where=den != 0.0)
    zero_den = np.broadcast_to(den == 0.0, out.shape)
    signed_inf = np.where(num > 0.0, np.inf, np.where(num < 0.0, -np.inf, 0.0))
    return np.where(zero_den, signed_inf, out)


def _two_sided_p(t_stats: FloatArray, dof: float) -> FloatArray:
    return np.asarray(2.0 * stats.t.sf(np.abs(t_stats), dof), dtype=np.float64)


# --------------------------------------------------------------------------- 2.1 standard error
def standard_error(sample_std: ArrayLike, n: ArrayLike) -> float | FloatArray:
    """2.1 Standard error of the mean: SE = s / sqrt(n)."""
    s = _to_array(sample_std, "sample_std")
    count = _to_array(n, "n")
    if np.any(s < 0.0):
        raise ValueError("sample_std must be >= 0")
    if np.any(count < 1.0):
        raise ValueError("n must be >= 1")
    s, count = _broadcast(("sample_std", "n"), s, count)
    return _unwrap(s / np.sqrt(count))


def standard_error_of_mean(samples: ArrayLike, *, ddof: int = 1) -> float:
    """2.1 Standard error of the mean from data: SE = s / sqrt(n), s with ``ddof`` (default 1)."""
    x = _to_series(samples, "samples", min_n=2)
    if x.size - ddof <= 0:
        raise ValueError(f"need more than {ddof} observations for ddof={ddof}, got {x.size}")
    return _scaled_std(x, ddof) / float(np.sqrt(x.size))


@dataclass(frozen=True)
class MeanEstimate:
    """Sample mean with its standard error and a Student-t confidence interval.

    With zero sample variance the interval collapses to the mean, ``std_err`` is 0 and
    ``t_stat`` is ``+-inf`` (0 when the mean is also 0) with ``p_value`` 0 (1).
    """

    mean: float
    std: float
    std_err: float
    n: int
    dof: int
    t_stat: float
    p_value: float
    ci_low: float
    ci_high: float
    confidence: float


def mean_confidence_interval(samples: ArrayLike, confidence: float = 0.95) -> MeanEstimate:
    """2.1 t-based confidence interval for a mean: x_bar +- t_{1-a/2, n-1} * s / sqrt(n).

    Use on per-trade P&L to bound the per-trade edge; ``t_stat``/``p_value`` test edge = 0.
    """
    x = _to_series(samples, "samples", min_n=2)
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must lie strictly between 0 and 1")
    n = x.size
    dof = n - 1
    mean = float(np.mean(x))
    std = _scaled_std(x, 1)
    se = std / float(np.sqrt(n))
    t_stat = float(_safe_ratio(np.float64(mean), np.float64(se)))
    half_width = float(stats.t.ppf(0.5 + 0.5 * confidence, dof)) * se
    return MeanEstimate(
        mean=mean,
        std=std,
        std_err=se,
        n=n,
        dof=dof,
        t_stat=t_stat,
        p_value=float(_two_sided_p(np.float64(t_stat), dof)),
        ci_low=mean - half_width,
        ci_high=mean + half_width,
        confidence=float(confidence),
    )


# --------------------------------------------------------------------------- 2.2 Sharpe t-stat
def sharpe_ratio(
    returns: ArrayLike,
    *,
    periods_per_year: float = 1.0,
    risk_free: float = 0.0,
    ddof: int = 1,
) -> float:
    """2.2 Sharpe ratio: SR = mean(r - rf) / std(r - rf) * sqrt(periods_per_year).

    ``risk_free`` is per period. ``periods_per_year=1`` gives the per-period Sharpe. Raises
    when the excess returns have zero volatility (the ratio is undefined).
    """
    r = _to_series(returns, "returns", min_n=2)
    if not (np.isfinite(periods_per_year) and periods_per_year > 0.0):
        raise ValueError("periods_per_year must be > 0")
    if not np.isfinite(risk_free):
        raise ValueError("risk_free must be finite")
    if r.size - ddof <= 0:
        raise ValueError(f"need more than {ddof} observations for ddof={ddof}, got {r.size}")
    excess = r - risk_free
    if _is_constant(excess):
        raise ValueError("returns have zero volatility: the Sharpe ratio is undefined")
    unit = excess / _magnitude(excess)  # the ratio is scale-free; unit scaling keeps the squares finite
    return float(np.mean(unit) / np.std(unit, ddof=ddof) * np.sqrt(periods_per_year))


def sharpe_t_stat(sharpe_annual: ArrayLike, years: ArrayLike) -> float | FloatArray:
    """2.2 t-stat of an annualised Sharpe ratio: t = SR * sqrt(T_years)."""
    sr = _to_array(sharpe_annual, "sharpe_annual")
    t_years = _to_array(years, "years")
    if np.any(t_years < 0.0):
        raise ValueError("years must be >= 0")
    sr, t_years = _broadcast(("sharpe_annual", "years"), sr, t_years)
    return _unwrap(sr * np.sqrt(t_years))


def sharpe_t_stat_per_period(sharpe_period: ArrayLike, n_periods: ArrayLike) -> float | FloatArray:
    """2.2 t-stat of a per-period Sharpe ratio: t = SR_period * sqrt(n_periods)."""
    sr = _to_array(sharpe_period, "sharpe_period")
    n = _to_array(n_periods, "n_periods")
    if np.any(n < 0.0):
        raise ValueError("n_periods must be >= 0")
    sr, n = _broadcast(("sharpe_period", "n_periods"), sr, n)
    return _unwrap(sr * np.sqrt(n))


def sharpe_t_stat_from_returns(returns: ArrayLike, *, risk_free: float = 0.0) -> float:
    """2.2 t-stat from a return series: t = SR_period * sqrt(n) = mean / (s / sqrt(n))."""
    r = _to_series(returns, "returns", min_n=2)
    return float(sharpe_ratio(r, risk_free=risk_free) * np.sqrt(r.size))


@dataclass(frozen=True)
class PromotionGate:
    """Outcome of the Sharpe t-stat promotion gate.

    ``years_required`` is the track-record length at which the given Sharpe would reach
    the threshold, ``(threshold / SR)^2`` (``inf`` for a non-positive Sharpe).
    """

    t_stat: float | FloatArray
    threshold: float
    passed: bool | BoolArray
    years_required: float | FloatArray


def sharpe_promotion_gate(
    sharpe_annual: ArrayLike,
    years: ArrayLike,
    *,
    threshold: float = 2.5,
) -> PromotionGate:
    """2.2 Promotion gate: promote only if t = SR * sqrt(T_years) >= threshold (default 2.5)."""
    if not (np.isfinite(threshold) and threshold > 0.0):
        raise ValueError("threshold must be a positive finite number")
    t_stat = np.asarray(sharpe_t_stat(sharpe_annual, years), dtype=np.float64)
    sr = np.broadcast_to(_to_array(sharpe_annual, "sharpe_annual"), t_stat.shape)
    safe_sr = np.where(sr > 0.0, sr, 1.0)
    years_required = np.where(sr > 0.0, (threshold / safe_sr) ** 2, np.inf)
    passed = t_stat >= threshold
    return PromotionGate(
        t_stat=_unwrap(t_stat),
        threshold=float(threshold),
        passed=bool(passed) if passed.ndim == 0 else passed,
        years_required=_unwrap(years_required),
    )


# --------------------------------------------------------------------------- 2.3 regression
def _design_matrix(x: ArrayLike, y: ArrayLike, add_intercept: bool) -> tuple[FloatArray, FloatArray]:
    features = _to_array(x, "x")
    target = _to_array(y, "y")
    if target.ndim != 1:
        raise ValueError(f"y must be 1-D, got shape {target.shape}")
    if features.ndim == 1:
        features = features[:, np.newaxis]
    if features.ndim != 2:
        raise ValueError(f"x must be 1-D or 2-D, got shape {features.shape}")
    if features.shape[0] != target.size:
        raise ValueError(f"shape mismatch: x has {features.shape[0]} rows but y has {target.size}")
    if add_intercept:
        features = np.column_stack([np.ones(target.size), features])
    n, p = features.shape
    if n <= p:
        raise ValueError(f"need more observations than parameters: n={n}, parameters={p}")
    return features, target


@dataclass(frozen=True)
class OLSResult:
    """Ordinary least squares fit.

    ``coef`` lists the intercept first when ``has_intercept`` is true. R^2 is centred with
    an intercept and uncentred without (statsmodels convention); for a constant ``y`` it
    is 1.0 for an exact fit and 0.0 otherwise. ``rank < len(coef)`` flags collinear
    regressors, in which case ``coef`` is the minimum-norm solution.
    """

    coef: FloatArray
    std_err: FloatArray
    t_stats: FloatArray
    p_values: FloatArray
    r_squared: float
    adj_r_squared: float
    residuals: FloatArray
    sigma2: float
    n_obs: int
    dof_resid: int
    rank: int
    has_intercept: bool
    condition_number: float

    @property
    def intercept(self) -> float:
        """Intercept (0.0 when the model was fitted without one)."""
        return float(self.coef[0]) if self.has_intercept else 0.0

    @property
    def slopes(self) -> FloatArray:
        """Coefficients on the regressors, excluding the intercept."""
        return self.coef[1:] if self.has_intercept else self.coef


def _r_squared_from_ss(ss_res: float, ss_tot: float, *, no_variation: bool, exact: bool) -> float:
    """R^2 = 1 - SS_res / SS_tot with the SS_tot = 0 case made explicit (1 if exact, else 0)."""
    if no_variation or ss_tot <= 0.0:
        return 1.0 if exact else 0.0
    return 1.0 - ss_res / ss_tot


def ols(x: ArrayLike, y: ArrayLike, *, add_intercept: bool = True) -> OLSResult:
    """2.3 OLS: beta = (X'X)^-1 X'y, solved by SVD least squares (never forms the inverse).

    Returns coefficients, standard errors ``sqrt(sigma^2 diag((X'X)^+))``, t-stats, p-values
    and R^2. Rank-deficient designs fall back to the pseudo-inverse and report ``rank``.
    """
    design, target = _design_matrix(x, y, add_intercept)
    n, p = design.shape
    # Solve in unit-scaled coordinates. A uniform rescaling of X and of y leaves the (minimum-norm)
    # solution, t-stats and R^2 unchanged, but stops squares over/underflowing for extreme units.
    x_scale = _magnitude(design) or 1.0
    y_scale = _magnitude(target) or 1.0
    xs = design / x_scale
    ys = target / y_scale
    u, sing, vt = np.linalg.svd(xs, full_matrices=False)
    cutoff = max(n, p) * _EPS * float(sing[0]) if sing.size else 0.0
    keep = sing > cutoff
    rank = int(np.count_nonzero(keep))
    inv_sing = np.zeros_like(sing)
    inv_sing[keep] = 1.0 / sing[keep]
    coef_s = vt.T @ (inv_sing * (u.T @ ys))
    resid_s = ys - xs @ coef_s
    ss_res = float(resid_s @ resid_s)
    dof = n - rank
    centred = ys - ys.mean() if add_intercept else ys
    ss_tot = float(centred @ centred)
    no_variation = _is_constant(target) if add_intercept else not np.any(target)
    magnitude = max(_magnitude(ys), float(np.max(np.abs(xs) @ np.abs(coef_s))))
    exact = _magnitude(resid_s) <= 1e3 * _EPS * max(magnitude, np.finfo(float).tiny)
    if exact:
        # Exact fit: residual noise is not information. Zero it, and zero any coefficient whose
        # largest possible contribution is itself below rounding noise (so its t-stat is 0, not inf).
        ss_res = 0.0
        negligible = np.abs(coef_s) * np.max(np.abs(xs), axis=0) <= 1e3 * _EPS * magnitude
        coef_s = np.where(negligible, 0.0, coef_s)
    sigma2_s = ss_res / dof
    xtx_inv_diag = np.sum((vt.T * inv_sing) ** 2, axis=1)
    std_err_s = np.sqrt(sigma2_s * xtx_inv_diag)
    t_stats = _safe_ratio(coef_s, std_err_s)
    r2 = _r_squared_from_ss(ss_res, ss_tot, no_variation=no_variation, exact=exact)
    df_total = n - 1 if add_intercept else n
    adj_r2 = 1.0 - (1.0 - r2) * df_total / dof
    smallest = float(sing[-1]) if sing.size else 0.0
    cond = float(sing[0] / smallest) if smallest > 0.0 else float("inf")
    with np.errstate(over="ignore", invalid="ignore"):
        back = y_scale / x_scale
        coef = coef_s * back
        std_err = std_err_s * back
        residuals = resid_s * y_scale
        sigma2 = sigma2_s * y_scale * y_scale
    if not (np.all(np.isfinite(coef)) and np.all(np.isfinite(std_err)) and np.isfinite(sigma2)):
        raise ValueError("the OLS estimates overflow float64: rescale x and/or y to sensible units")
    return OLSResult(
        coef=coef,
        std_err=std_err,
        t_stats=t_stats,
        p_values=_two_sided_p(t_stats, dof),
        r_squared=r2,
        adj_r_squared=adj_r2,
        residuals=residuals,
        sigma2=sigma2,
        n_obs=n,
        dof_resid=dof,
        rank=rank,
        has_intercept=add_intercept,
        condition_number=cond,
    )


def ols_slope(x: ArrayLike, y: ArrayLike) -> float:
    """2.3 Single-regressor OLS slope: beta_1 = Cov(X, Y) / Var(X)."""
    xs = _to_series(x, "x", min_n=2)
    ys = _to_series(y, "y", min_n=2)
    if xs.size != ys.size:
        raise ValueError(f"shape mismatch: x has {xs.size} observations but y has {ys.size}")
    if _is_constant(xs):
        raise ValueError("x has zero variance: the slope Cov(X, Y) / Var(X) is undefined")
    if _is_constant(ys):
        return 0.0
    ux, x_scale = _centred_unit(xs)
    uy, y_scale = _centred_unit(ys)
    with np.errstate(over="ignore"):
        slope = float(ux @ uy / (ux @ ux)) * (y_scale / x_scale)
    if not np.isfinite(slope):
        raise ValueError("the slope overflows float64: rescale x and/or y to sensible units")
    return slope


@dataclass(frozen=True)
class RobustRegressionResult:
    """Huber M-estimator (IRLS) fit; ``weights`` near 0 mark observations treated as outliers.

    ``rank < len(coef)`` flags collinear regressors (the estimate is then a minimum-norm one).
    """

    coef: FloatArray
    std_err: FloatArray
    t_stats: FloatArray
    weights: FloatArray
    scale: float
    n_iter: int
    converged: bool
    has_intercept: bool
    rank: int

    @property
    def intercept(self) -> float:
        """Intercept (0.0 when the model was fitted without one)."""
        return float(self.coef[0]) if self.has_intercept else 0.0

    @property
    def slopes(self) -> FloatArray:
        """Coefficients on the regressors, excluding the intercept."""
        return self.coef[1:] if self.has_intercept else self.coef


def robust_regression(
    x: ArrayLike,
    y: ArrayLike,
    *,
    add_intercept: bool = True,
    huber_t: float = 1.345,
    max_iter: int = 100,
    tol: float = 1e-8,
) -> RobustRegressionResult:
    """2.3 Robust regression: beta = argmin sum_i rho_Huber((y_i - x_i'beta) / s), via statsmodels RLM.

    Down-weights outliers (bad prints, fat-finger fills) instead of letting them drive the
    hedge ratio. An exact OLS fit short-circuits the IRLS loop (the MAD scale would be 0).
    """
    if not (np.isfinite(huber_t) and huber_t > 0.0):
        raise ValueError("huber_t must be > 0")
    if max_iter < 1:
        raise ValueError("max_iter must be >= 1")
    if not (np.isfinite(tol) and tol > 0.0):
        raise ValueError("tol must be > 0")
    design, target = _design_matrix(x, y, add_intercept)
    exact = ols(design, target, add_intercept=False)
    if exact.sigma2 == 0.0:
        return RobustRegressionResult(
            coef=exact.coef,
            std_err=np.zeros_like(exact.coef),
            t_stats=_safe_ratio(exact.coef, np.zeros_like(exact.coef)),
            weights=np.ones(target.size),
            scale=0.0,
            n_iter=0,
            converged=True,
            has_intercept=add_intercept,
            rank=exact.rank,
        )
    # Fit in unit-scaled coordinates (the M-estimate is equivariant) so extreme units cannot
    # overflow inside statsmodels; rank deficiency is reported through ``rank`` instead of a warning.
    x_scale = _magnitude(design) or 1.0
    y_scale = _magnitude(target) or 1.0
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore")
        model = _SmRLM(target / y_scale, design / x_scale, M=_sm_norms.HuberT(t=huber_t))
        fit = model.fit(maxiter=max_iter, tol=tol)
        back = y_scale / x_scale
        coef = np.asarray(fit.params, dtype=np.float64) * back
        std_err = np.asarray(fit.bse, dtype=np.float64) * back
        scale = float(fit.scale) * y_scale
    weights = np.asarray(fit.weights, dtype=np.float64)
    if not (np.all(np.isfinite(coef)) and np.all(np.isfinite(std_err)) and np.isfinite(scale)):
        raise ValueError(
            "robust regression failed to produce finite estimates (the residual MAD scale is zero "
            "or the design is degenerate)"
        )
    n_iter = int(fit.fit_history["iteration"])
    return RobustRegressionResult(
        coef=coef,
        std_err=std_err,
        t_stats=_safe_ratio(coef, std_err),
        weights=weights,
        scale=scale,
        n_iter=n_iter,
        converged=n_iter < max_iter,
        has_intercept=add_intercept,
        rank=exact.rank,
    )


@dataclass(frozen=True)
class RollingHedgeRatio:
    """Rolling OLS hedge ratio aligned to the window end.

    ``beta[t]`` and ``alpha[t]`` use observations ``t - window + 1 .. t``. Entries where
    ``valid`` is false (warm-up, or a window in which ``x`` has no variation) are NaN.
    """

    beta: FloatArray
    alpha: FloatArray
    valid: BoolArray
    window: int


def rolling_hedge_ratio(
    y: ArrayLike,
    x: ArrayLike,
    window: int,
    *,
    add_intercept: bool = True,
) -> RollingHedgeRatio:
    """2.3 Rolling hedge ratio: beta_t = Cov_w(x, y) / Var_w(x) over a trailing window.

    Without an intercept the regression is through the origin, ``sum(x y) / sum(x^2)``.
    Vectorised over windows with exact per-window centring (no cumulative-sum cancellation);
    windows are processed in blocks so peak memory stays bounded for long series.
    """
    ys = _to_series(y, "y")
    xs = _to_series(x, "x")
    if xs.size != ys.size:
        raise ValueError(f"shape mismatch: x has {xs.size} observations but y has {ys.size}")
    if isinstance(window, bool) or not isinstance(window, (int, np.integer)):
        raise ValueError("window must be an integer")
    min_window = 2 if add_intercept else 1
    if window < min_window:
        raise ValueError(f"window must be >= {min_window}")
    if window > xs.size:
        raise ValueError(f"window ({window}) exceeds the number of observations ({xs.size})")
    n = xs.size
    window = int(window)
    # Work in unit-scaled coordinates (beta scales by y_unit / x_unit, alpha by y_unit).
    x_unit = _magnitude(xs) or 1.0
    y_unit = _magnitude(ys) or 1.0
    x_windows = sliding_window_view(xs / x_unit, window)
    y_windows = sliding_window_view(ys / y_unit, window)
    n_windows = x_windows.shape[0]
    beta = np.full(n, np.nan)
    alpha = np.full(n, np.nan)
    valid = np.zeros(n, dtype=bool)
    block = max(1, _ROLLING_BLOCK_ELEMENTS // window)
    for start in range(0, n_windows, block):
        stop = min(start + block, n_windows)
        xw = x_windows[start:stop]
        yw = y_windows[start:stop]
        if add_intercept:
            x_mean = xw.mean(axis=1)
            y_mean = yw.mean(axis=1)
            dx = xw - x_mean[:, np.newaxis]
            sxx = np.sum(dx * dx, axis=1)
            sxy = np.sum(dx * (yw - y_mean[:, np.newaxis]), axis=1)
        else:
            x_mean = np.zeros(stop - start)
            y_mean = np.zeros(stop - start)
            sxx = np.sum(xw * xw, axis=1)
            sxy = np.sum(xw * yw, axis=1)
        x_scale = np.max(np.abs(xw), axis=1)
        ok = sxx > window * (16.0 * _EPS * x_scale) ** 2
        beta_block = np.full(stop - start, np.nan)
        np.divide(sxy, sxx, out=beta_block, where=ok)
        out = slice(start + window - 1, stop + window - 1)
        beta[out] = beta_block
        alpha[out] = np.where(ok, y_mean - np.where(ok, beta_block, 0.0) * x_mean, np.nan)
        valid[out] = ok
    with np.errstate(over="ignore", invalid="ignore"):
        beta *= y_unit / x_unit
        alpha *= y_unit
    if not np.all(np.isfinite(beta[valid])):
        raise ValueError("the hedge ratio overflows float64: rescale x and/or y to sensible units")
    return RollingHedgeRatio(beta=beta, alpha=alpha, valid=valid, window=window)


# --------------------------------------------------------------------------- 2.4 correlation
def correlation(x: ArrayLike, y: ArrayLike) -> float:
    """2.4 Pearson correlation: rho = Cov(X, Y) / (sigma_X sigma_Y).

    Measures linear association only: dependent series can have rho = 0. Raises when either
    series is constant (rho undefined).
    """
    xs = _to_series(x, "x", min_n=2)
    ys = _to_series(y, "y", min_n=2)
    if xs.size != ys.size:
        raise ValueError(f"shape mismatch: x has {xs.size} observations but y has {ys.size}")
    if _is_constant(xs) or _is_constant(ys):
        raise ValueError("correlation is undefined when a series has zero variance")
    return _unit_correlation(xs, ys)


def _unit_correlation(a: FloatArray, b: FloatArray) -> float:
    """Pearson correlation of two non-constant series, evaluated on unit-scaled deviations."""
    ua, _ = _centred_unit(a)
    ub, _ = _centred_unit(b)
    rho = float(ua @ ub / np.sqrt((ua @ ua) * (ub @ ub)))
    return float(np.clip(rho, -1.0, 1.0))


def r_squared(y_true: ArrayLike, y_pred: ArrayLike) -> float:
    """2.4 Coefficient of determination: R^2 = 1 - SS_res / SS_tot.

    Can be negative for predictions worse than the mean. For a constant ``y_true``
    (SS_tot = 0) returns 1.0 if the predictions are exact and 0.0 otherwise.
    """
    actual = _to_series(y_true, "y_true")
    predicted = _to_series(y_pred, "y_pred")
    if actual.size != predicted.size:
        raise ValueError(
            f"shape mismatch: y_true has {actual.size} observations but y_pred has {predicted.size}"
        )
    # R^2 is scale-free: divide through by the larger magnitude so the squares stay finite.
    unit = max(_magnitude(actual), _magnitude(predicted)) or 1.0
    actual_u = actual / unit
    resid = actual_u - predicted / unit
    centred = actual_u - actual_u.mean()
    return _r_squared_from_ss(
        float(resid @ resid),
        float(centred @ centred),
        no_variation=_is_constant(actual),
        exact=_magnitude(resid) <= 16.0 * _EPS * max(_magnitude(actual_u), np.finfo(float).tiny),
    )


@dataclass(frozen=True)
class LeadLagResult:
    """Cross-correlation scan over lags ``-max_lag..max_lag``.

    ``correlations[i] = Corr(x[t], y[t + lags[i]])``. A positive ``best_lag`` means ``x``
    leads ``y`` by that many periods; a negative one means ``y`` leads ``x``. Lags whose
    overlapping segment is constant have ``valid = False`` and a NaN correlation.
    """

    lags: IntArray
    correlations: FloatArray
    valid: BoolArray
    n_overlap: IntArray
    best_lag: int
    best_correlation: float


def lead_lag_cross_correlation(
    x: ArrayLike,
    y: ArrayLike,
    max_lag: int,
    *,
    min_overlap: int = 3,
) -> LeadLagResult:
    """2.4 Lead-lag scan: argmax_k |rho(k)|, rho(k) = Corr(x_t, y_{t+k}), k in [-max_lag, max_lag].

    Feed stationary series (returns, not price levels). Ties in ``|rho|`` are resolved in
    favour of the smallest ``|lag|``, then the non-negative lag.
    """
    xs = _to_series(x, "x")
    ys = _to_series(y, "y")
    if xs.size != ys.size:
        raise ValueError(f"shape mismatch: x has {xs.size} observations but y has {ys.size}")
    if isinstance(max_lag, bool) or not isinstance(max_lag, (int, np.integer)) or max_lag < 0:
        raise ValueError("max_lag must be a non-negative integer")
    if isinstance(min_overlap, bool) or not isinstance(min_overlap, (int, np.integer)) or min_overlap < 3:
        raise ValueError("min_overlap must be an integer >= 3")
    n = xs.size
    if n - max_lag < min_overlap:
        raise ValueError(
            f"max_lag={max_lag} leaves only {n - max_lag} overlapping observations; need >= {min_overlap}"
        )
    lags = np.arange(-max_lag, max_lag + 1, dtype=np.int64)
    correlations = np.full(lags.size, np.nan)
    valid = np.zeros(lags.size, dtype=bool)
    n_overlap = n - np.abs(lags)
    for i, lag in enumerate(lags):
        k = int(lag)
        a = xs[: n - k] if k >= 0 else xs[-k:]
        b = ys[k:] if k >= 0 else ys[: n + k]
        if _is_constant(a) or _is_constant(b):
            continue
        correlations[i] = _unit_correlation(a, b)
        valid[i] = True
    if not valid.any():
        raise ValueError("cross-correlation is undefined at every lag (a series is constant)")
    strength = np.where(valid, np.abs(correlations), -np.inf)
    candidates = np.flatnonzero(strength >= strength.max() - 1e-12)
    order = np.lexsort((-lags[candidates], np.abs(lags[candidates])))
    best = int(candidates[order[0]])
    return LeadLagResult(
        lags=lags,
        correlations=correlations,
        valid=valid,
        n_overlap=n_overlap.astype(np.int64),
        best_lag=int(lags[best]),
        best_correlation=float(correlations[best]),
    )


# --------------------------------------------------------------------------- 2.5 multiple testing
def family_wise_error_rate(alpha: ArrayLike, n_tests: ArrayLike) -> float | FloatArray:
    """2.5 Family-wise error rate: P(>= 1 false positive) = 1 - (1 - alpha)^k for k independent tests."""
    a = _to_array(alpha, "alpha")
    k = _to_array(n_tests, "n_tests")
    if np.any((a < 0.0) | (a > 1.0)):
        raise ValueError("alpha must lie in [0, 1]")
    if np.any(k < 0.0) or np.any(k != np.floor(k)):
        raise ValueError("n_tests must be a non-negative integer")
    a, k = _broadcast(("alpha", "n_tests"), a, k)
    with np.errstate(divide="ignore", invalid="ignore"):
        fwer = -np.expm1(k * np.log1p(-a))
    fwer = np.where(a >= 1.0, np.where(k > 0.0, 1.0, 0.0), fwer)
    return _unwrap(fwer)


@dataclass(frozen=True)
class MultipleTestResult:
    """Multiple-testing correction: ``reject = pvalues_adjusted <= alpha`` in the input order."""

    reject: BoolArray
    pvalues_adjusted: FloatArray
    alpha: float
    method: str

    @property
    def n_rejected(self) -> int:
        """Number of hypotheses rejected (discoveries)."""
        return int(np.count_nonzero(self.reject))


def _check_pvalues(pvalues: ArrayLike, alpha: float) -> FloatArray:
    p = _to_array(pvalues, "pvalues", allow_empty=True)
    if p.ndim != 1:
        raise ValueError(f"pvalues must be 1-D, got shape {p.shape}")
    if np.any((p < 0.0) | (p > 1.0)):
        raise ValueError("pvalues must lie in [0, 1]")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must lie strictly between 0 and 1")
    return p


def _result(adjusted: FloatArray, alpha: float, method: str) -> MultipleTestResult:
    adjusted = np.clip(adjusted, 0.0, 1.0)
    return MultipleTestResult(adjusted <= alpha, adjusted, float(alpha), method)


def bonferroni(pvalues: ArrayLike, alpha: float = 0.05) -> MultipleTestResult:
    """2.5 Bonferroni (FWER): p_adj_i = min(1, m * p_i); reject if p_adj_i <= alpha."""
    p = _check_pvalues(pvalues, alpha)
    return _result(p * p.size, alpha, "bonferroni")


def holm_bonferroni(pvalues: ArrayLike, alpha: float = 0.05) -> MultipleTestResult:
    """2.5 Holm-Bonferroni step-down (FWER): p_adj_(i) = max_{j <= i} min(1, (m - j + 1) p_(j))."""
    p = _check_pvalues(pvalues, alpha)
    m = p.size
    order = np.argsort(p, kind="stable")
    stepped = np.maximum.accumulate((m - np.arange(m)) * p[order]) if m else p.copy()
    adjusted = np.empty(m)
    adjusted[order] = stepped
    return _result(adjusted, alpha, "holm")


def _step_up_fdr(p: FloatArray, factor: float) -> FloatArray:
    m = p.size
    if m == 0:
        return p.copy()
    order = np.argsort(p, kind="stable")
    scaled = factor * m / np.arange(1, m + 1) * p[order]
    stepped = np.minimum.accumulate(scaled[::-1])[::-1]
    adjusted = np.empty(m)
    adjusted[order] = stepped
    return adjusted


def benjamini_hochberg(pvalues: ArrayLike, alpha: float = 0.05) -> MultipleTestResult:
    """2.5 Benjamini-Hochberg step-up (FDR): p_adj_(i) = min_{j >= i} min(1, m p_(j) / j)."""
    p = _check_pvalues(pvalues, alpha)
    return _result(_step_up_fdr(p, 1.0), alpha, "fdr_bh")


def benjamini_yekutieli(pvalues: ArrayLike, alpha: float = 0.05) -> MultipleTestResult:
    """2.5 Benjamini-Yekutieli (FDR under dependence): p_adj_(i) = min_{j >= i} min(1, c_m m p_(j) / j).

    ``c_m = sum_{i=1..m} 1/i`` is the harmonic penalty that makes the bound valid under
    arbitrary dependence between the tests.
    """
    p = _check_pvalues(pvalues, alpha)
    harmonic = float(np.sum(1.0 / np.arange(1, p.size + 1))) if p.size else 1.0
    return _result(_step_up_fdr(p, harmonic), alpha, "fdr_by")


def adjust_pvalues(
    pvalues: ArrayLike,
    method: Literal["bonferroni", "holm", "fdr_bh", "fdr_by"] = "fdr_bh",
    alpha: float = 0.05,
) -> MultipleTestResult:
    """2.5 Multiple-testing dispatcher: FWER (bonferroni, holm) or FDR (fdr_bh, fdr_by) by name."""
    methods = {
        "bonferroni": bonferroni,
        "holm": holm_bonferroni,
        "fdr_bh": benjamini_hochberg,
        "fdr_by": benjamini_yekutieli,
    }
    if method not in methods:
        raise ValueError(f"unknown method {method!r}; choose one of {sorted(methods)}")
    return methods[method](pvalues, alpha)
