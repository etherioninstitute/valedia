"""Level 5: time series. Every rolling or online estimator here is causal: the value
labelled t uses data at or before t only."""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass

import numpy as np
from arch import arch_model
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.stattools import acf, adfuller, coint, kpss, pacf
from statsmodels.tsa.vector_ar.vecm import coint_johansen

__all__ = [
    "log_returns", "simple_returns", "log_to_simple", "simple_to_log", "aggregate_log_returns",
    "annualize_volatility", "autocorrelation", "partial_autocorrelation", "ljung_box",
    "classify_serial_dependence", "SerialDependence", "adf_test", "kpss_test", "stationarity_verdict",
    "StationarityVerdict", "hedge_ratio_ols", "engle_granger", "CointegrationResult", "johansen_rank",
    "JohansenResult", "fit_ou", "OUParams", "half_life", "rolling_zscore", "zscore_signals",
    "fit_garch", "GarchFit", "garch11_filter", "garch11_update", "garch_long_run_variance",
    "vol_scaled_stop", "vol_target_scale",
]

FORMULAS: dict[str, tuple[str, ...]] = {
    "5.1": ("log_returns", "simple_returns", "log_to_simple", "simple_to_log", "aggregate_log_returns",
            "annualize_volatility"),
    "5.2": ("autocorrelation", "partial_autocorrelation", "ljung_box", "classify_serial_dependence"),
    "5.3": ("adf_test", "kpss_test", "stationarity_verdict"),
    "5.4": ("hedge_ratio_ols", "engle_granger", "johansen_rank", "fit_ou", "half_life", "rolling_zscore",
            "zscore_signals"),
    "5.5": ("fit_garch", "garch11_filter", "garch11_update", "garch_long_run_variance", "vol_scaled_stop",
            "vol_target_scale"),
}


def _series(x, name: str, min_len: int = 2) -> np.ndarray:
    a = np.asarray(x, dtype=float).ravel()
    if a.size < min_len:
        raise ValueError(f"{name} needs at least {min_len} observations, got {a.size}")
    if not np.all(np.isfinite(a)):
        raise ValueError(f"{name} contains NaN or infinite values")
    return a


def _non_constant(a: np.ndarray, name: str) -> None:
    if np.ptp(a) == 0.0:
        raise ValueError(f"{name} is constant; the statistic is undefined")


# ---------------------------------------------------------------- 5.1 returns
def log_returns(prices) -> np.ndarray:
    """5.1 Log returns: r_t = ln(P_t / P_{t-1})."""
    p = _series(prices, "prices")
    if np.any(p <= 0):
        raise ValueError("prices must be strictly positive")
    return np.diff(np.log(p))


def simple_returns(prices) -> np.ndarray:
    """5.1 Simple returns: R_t = P_t / P_{t-1} - 1 (use these to aggregate across assets)."""
    p = _series(prices, "prices")
    if np.any(p <= 0):
        raise ValueError("prices must be strictly positive")
    return p[1:] / p[:-1] - 1.0


def log_to_simple(r) -> np.ndarray:
    """5.1 Convert log returns to simple returns: R = e^r - 1."""
    return np.expm1(np.asarray(r, dtype=float))


def simple_to_log(R) -> np.ndarray:
    """5.1 Convert simple returns to log returns: r = ln(1 + R)."""
    a = np.asarray(R, dtype=float)
    if np.any(a <= -1.0):
        raise ValueError("simple returns must be greater than -1")
    return np.log1p(a)


def aggregate_log_returns(r, horizon: int) -> np.ndarray:
    """5.1 Multi-horizon log returns: non-overlapping sums of `horizon` consecutive log returns."""
    a = _series(r, "returns", 1)
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    n = (a.size // horizon) * horizon
    if n == 0:
        raise ValueError("not enough observations for one full horizon")
    return a[:n].reshape(-1, horizon).sum(axis=1)


def annualize_volatility(vol, periods_per_year: float = 252.0):
    """5.1 Square-root-of-time scaling: sigma_annual = sigma_period * sqrt(periods per year)."""
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    return np.asarray(vol, dtype=float) * math.sqrt(periods_per_year)


# ---------------------------------------------------------------- 5.2 autocorrelation
def autocorrelation(x, nlags: int = 10) -> np.ndarray:
    """5.2 Autocorrelation rho_k = Corr(r_t, r_{t-k}) for k = 0..nlags."""
    a = _series(x, "series", 3)
    _non_constant(a, "series")
    return acf(a, nlags=min(nlags, a.size - 1), fft=False)


def partial_autocorrelation(x, nlags: int = 10) -> np.ndarray:
    """5.2 Partial autocorrelation for k = 0..nlags."""
    a = _series(x, "series", 5)
    _non_constant(a, "series")
    return pacf(a, nlags=min(nlags, a.size // 2 - 1))


def ljung_box(x, lags: int = 10) -> tuple[float, float]:
    """5.2 Ljung-Box Q statistic and p-value for joint zero autocorrelation up to `lags`."""
    a = _series(x, "series", lags + 2)
    _non_constant(a, "series")
    out = acorr_ljungbox(a, lags=[lags], return_df=True)
    return float(out["lb_stat"].iloc[0]), float(out["lb_pvalue"].iloc[0])


@dataclass(frozen=True)
class SerialDependence:
    label: str        # "momentum", "mean_reversion" or "noise"
    lag: int          # lag carrying the strongest autocorrelation (0 for noise)
    rho: float
    p_value: float    # Ljung-Box p-value up to max_lag


def classify_serial_dependence(x, max_lag: int = 5, alpha: float = 0.05) -> SerialDependence:
    """5.2 Classify a return series: rho_k > 0 momentum, rho_k < 0 mean reversion, else noise."""
    a = _series(x, "series", max_lag + 10)
    _non_constant(a, "series")
    _, p = ljung_box(a, max_lag)
    rho = autocorrelation(a, max_lag)[1:]
    band = 1.96 / math.sqrt(a.size)
    k = int(np.argmax(np.abs(rho)))
    if p > alpha or abs(rho[k]) <= band:
        return SerialDependence("noise", 0, float(rho[k]), p)
    return SerialDependence("momentum" if rho[k] > 0 else "mean_reversion", k + 1, float(rho[k]), p)


# ---------------------------------------------------------------- 5.3 stationarity
def adf_test(x, regression: str = "c") -> tuple[float, float]:
    """5.3 Augmented Dickey-Fuller: Delta y_t = gamma y_{t-1} + sum a_i Delta y_{t-i} + e_t.

    Returns (statistic, p-value); a small p-value rejects the unit root."""
    a = _series(x, "series", 20)
    _non_constant(a, "series")
    stat, p, *_ = adfuller(a, regression=regression, autolag="AIC")
    return float(stat), float(p)


def kpss_test(x, regression: str = "c") -> tuple[float, float]:
    """5.3 KPSS test; a small p-value rejects stationarity (p is clipped to [0.01, 0.10])."""
    a = _series(x, "series", 20)
    _non_constant(a, "series")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stat, p, *_ = kpss(a, regression=regression, nlags="auto")
    return float(stat), float(p)


@dataclass(frozen=True)
class StationarityVerdict:
    label: str            # "I(0)", "unit_root" or "inconclusive"
    is_stationary: bool   # True only when both tests agree on stationarity
    adf_p: float
    kpss_p: float


def stationarity_verdict(x, alpha: float = 0.05) -> StationarityVerdict:
    """5.3 I(0) gate: ADF must reject a unit root AND KPSS must not reject stationarity."""
    _, adf_p = adf_test(x)
    _, kpss_p = kpss_test(x)
    adf_stationary, kpss_stationary = adf_p < alpha, kpss_p > alpha
    if adf_stationary and kpss_stationary:
        return StationarityVerdict("I(0)", True, adf_p, kpss_p)
    if not adf_stationary and not kpss_stationary:
        return StationarityVerdict("unit_root", False, adf_p, kpss_p)
    return StationarityVerdict("inconclusive", False, adf_p, kpss_p)


# ---------------------------------------------------------------- 5.4 cointegration and OU
def hedge_ratio_ols(y, x) -> tuple[float, float]:
    """5.4 Hedge ratio beta and intercept from OLS of y on x (spread s = y - beta x)."""
    ya, xa = _series(y, "y", 3), _series(x, "x", 3)
    if ya.size != xa.size:
        raise ValueError("y and x must have the same length")
    _non_constant(xa, "x")
    xc = xa - xa.mean()
    beta = float(xc @ (ya - ya.mean()) / (xc @ xc))
    return beta, float(ya.mean() - beta * xa.mean())


@dataclass(frozen=True)
class CointegrationResult:
    beta: float
    intercept: float
    statistic: float
    p_value: float
    cointegrated: bool
    spread: np.ndarray


def engle_granger(y, x, alpha: float = 0.05) -> CointegrationResult:
    """5.4 Engle-Granger cointegration test with spread s_t = y_t - beta x_t."""
    ya, xa = _series(y, "y", 30), _series(x, "x", 30)
    beta, a0 = hedge_ratio_ols(ya, xa)
    stat, p, _ = coint(ya, xa)
    return CointegrationResult(beta, a0, float(stat), float(p), bool(p < alpha), ya - beta * xa)


@dataclass(frozen=True)
class JohansenResult:
    rank: int
    trace_statistics: np.ndarray
    critical_values_95: np.ndarray
    eigenvectors: np.ndarray


def johansen_rank(data, det_order: int = 0, k_ar_diff: int = 1) -> JohansenResult:
    """5.4 Johansen trace test: cointegration rank at the 95% level."""
    a = np.asarray(data, dtype=float)
    if a.ndim != 2 or a.shape[1] < 2 or a.shape[0] < 30:
        raise ValueError("data must be (n_obs >= 30, n_series >= 2)")
    if not np.all(np.isfinite(a)):
        raise ValueError("data contains NaN or infinite values")
    res = coint_johansen(a, det_order, k_ar_diff)
    crit = res.cvt[:, 1]
    rank = 0
    for stat, c in zip(res.lr1, crit):
        if stat <= c:
            break
        rank += 1
    return JohansenResult(rank, np.asarray(res.lr1), np.asarray(crit), np.asarray(res.evec))


@dataclass(frozen=True)
class OUParams:
    theta: float      # mean-reversion speed per unit time
    mu: float         # long-run mean
    sigma: float      # diffusion per sqrt(unit time)
    half_life: float  # ln 2 / theta; inf when not mean reverting


def half_life(theta: float) -> float:
    """5.4 Ornstein-Uhlenbeck half-life: ln 2 / theta (infinite when theta <= 0)."""
    return math.log(2.0) / theta if theta > 0 else math.inf


def fit_ou(spread, dt: float = 1.0) -> OUParams:
    """5.4 Fit Delta s_t = -theta (s_{t-1} - mu) dt + sigma dW by regressing Delta s on s_{t-1}."""
    s = _series(spread, "spread", 10)
    if dt <= 0:
        raise ValueError("dt must be positive")
    lag, ds = s[:-1], np.diff(s)
    _non_constant(lag, "spread")
    b, a = hedge_ratio_ols(ds, lag)
    resid = ds - (a + b * lag)
    theta = -b / dt
    mu = -a / b if b != 0 else float(np.mean(s))
    sigma = float(np.std(resid, ddof=2) / math.sqrt(dt))
    return OUParams(float(theta), float(mu), sigma, half_life(theta))


def rolling_zscore(x, window: int) -> np.ndarray:
    """5.4 Causal rolling z-score: z_t uses the window ending at t (NaN until the window fills)."""
    a = _series(x, "series", 1)
    if window < 2:
        raise ValueError("window must be >= 2")
    z = np.full(a.size, np.nan)
    if a.size < window:
        return z
    c1 = np.cumsum(np.insert(a, 0, 0.0))
    c2 = np.cumsum(np.insert(a * a, 0, 0.0))
    s1, s2 = c1[window:] - c1[:-window], c2[window:] - c2[:-window]
    mean = s1 / window
    var = np.maximum((s2 - window * mean**2) / (window - 1), 0.0)
    sd = np.sqrt(var)
    tail = a[window - 1:]
    z[window - 1:] = np.divide(tail - mean, sd, out=np.zeros_like(sd), where=sd > 1e-12)
    return z


def zscore_signals(z, entry_z: float = 2.0, exit_z: float = 0.5, stop_z: float = 4.0) -> np.ndarray:
    """5.4 Spread positions from z-scores: short above +entry, long below -entry, flat inside
    +-exit or beyond +-stop. The position at t depends only on z up to t; trade it on the next bar."""
    if not (0 <= exit_z < entry_z < stop_z):
        raise ValueError("thresholds must satisfy 0 <= exit_z < entry_z < stop_z")
    za = np.asarray(z, dtype=float).ravel()
    pos = np.zeros(za.size)
    state = 0.0
    for i, v in enumerate(za):
        if not np.isfinite(v):
            state = 0.0
        elif abs(v) >= stop_z:
            state = 0.0
        elif state == 0.0:
            if v >= entry_z:
                state = -1.0
            elif v <= -entry_z:
                state = 1.0
        elif abs(v) <= exit_z or (state > 0 and v > 0) or (state < 0 and v < 0):
            state = 0.0
        pos[i] = state
    return pos


# ---------------------------------------------------------------- 5.5 GARCH
@dataclass(frozen=True)
class GarchFit:
    model: str
    omega: float
    alpha: float
    beta: float
    gamma: float                       # asymmetry term (0 for plain GARCH)
    persistence: float
    long_run_variance: float           # in squared return units; inf if not covariance stationary
    conditional_volatility: np.ndarray  # sigma_t in return units, aligned with the input
    forecast_variance: np.ndarray       # h-step-ahead variances, h = 1..horizon
    loglikelihood: float


def garch_long_run_variance(omega: float, alpha: float, beta: float) -> float:
    """5.5 GARCH(1,1) long-run variance: omega / (1 - alpha - beta)."""
    if omega <= 0 or alpha < 0 or beta < 0:
        raise ValueError("require omega > 0, alpha >= 0, beta >= 0")
    return omega / (1.0 - alpha - beta) if alpha + beta < 1.0 else math.inf


def fit_garch(returns, model: str = "garch", horizon: int = 5, mean: str = "Constant",
              dist: str = "normal", seed: int = 0) -> GarchFit:
    """5.5 Fit sigma_t^2 = omega + alpha r_{t-1}^2 + beta sigma_{t-1}^2 (GARCH), GJR-GARCH or EGARCH."""
    r = _series(returns, "returns", 100)
    _non_constant(r, "returns")
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    kind = model.lower()
    if kind not in ("garch", "gjr", "egarch"):
        raise ValueError("model must be 'garch', 'gjr' or 'egarch'")
    scale = 100.0
    spec = arch_model(r * scale, mean=mean, vol="EGARCH" if kind == "egarch" else "GARCH", p=1,
                      o=0 if kind == "garch" else 1, q=1, dist=dist, rescale=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = spec.fit(disp="off", show_warning=False)
    p = res.params
    alpha, beta = float(p["alpha[1]"]), float(p["beta[1]"])
    gamma = float(p["gamma[1]"]) if "gamma[1]" in p.index else 0.0
    if kind == "egarch":
        omega = float(p["omega"]) - (1.0 - beta) * 2.0 * math.log(scale)
        persistence = beta
        long_run = math.exp(omega / (1.0 - beta)) if abs(beta) < 1.0 else math.inf
        rng = np.random.default_rng(seed)
        fc = res.forecast(horizon=horizon, method="analytic" if horizon == 1 else "simulation",
                          simulations=2000, rng=lambda size: rng.standard_normal(size), reindex=False)
    else:
        omega = float(p["omega"]) / scale**2
        persistence = alpha + beta + 0.5 * gamma
        long_run = omega / (1.0 - persistence) if persistence < 1.0 else math.inf
        fc = res.forecast(horizon=horizon, reindex=False)
    return GarchFit(kind, omega, alpha, beta, gamma, float(persistence), float(long_run),
                    np.asarray(res.conditional_volatility, dtype=float) / scale,
                    np.asarray(fc.variance.values[-1], dtype=float) / scale**2, float(res.loglikelihood))


def garch11_update(prev_variance: float, prev_return: float, omega: float, alpha: float, beta: float) -> float:
    """5.5 One online GARCH(1,1) step: sigma_t^2 = omega + alpha r_{t-1}^2 + beta sigma_{t-1}^2."""
    if prev_variance < 0 or not np.isfinite(prev_variance) or not np.isfinite(prev_return):
        raise ValueError("previous variance must be finite and non-negative, return finite")
    return omega + alpha * prev_return**2 + beta * prev_variance


def garch11_filter(returns, omega: float, alpha: float, beta: float,
                   initial_variance: float | None = None) -> np.ndarray:
    """5.5 Recursive GARCH(1,1) variance path; element t uses returns up to t-1 only."""
    r = _series(returns, "returns", 1)
    if omega <= 0 or alpha < 0 or beta < 0:
        raise ValueError("require omega > 0, alpha >= 0, beta >= 0")
    var = np.empty(r.size)
    if initial_variance is None:
        lr = garch_long_run_variance(omega, alpha, beta)
        initial_variance = lr if math.isfinite(lr) else float(np.var(r))
    if initial_variance <= 0:
        raise ValueError("initial_variance must be positive")
    var[0] = initial_variance
    for t in range(1, r.size):
        var[t] = omega + alpha * r[t - 1] ** 2 + beta * var[t - 1]
    return var


def vol_scaled_stop(price: float, vol_forecast: float, multiple: float = 2.0, horizon: float = 1.0) -> float:
    """5.5 Volatility-scaled stop distance in price units: multiple * sigma * sqrt(horizon) * price."""
    if price <= 0 or vol_forecast < 0 or multiple <= 0 or horizon <= 0:
        raise ValueError("price, multiple and horizon must be positive and vol_forecast non-negative")
    return multiple * vol_forecast * math.sqrt(horizon) * price


def vol_target_scale(vol_forecast: float, target_vol: float, max_leverage: float = 1.0) -> float:
    """5.5 Position scale that targets `target_vol`, capped at max_leverage; 0 when the forecast is unusable."""
    if target_vol < 0 or max_leverage < 0:
        raise ValueError("target_vol and max_leverage must be non-negative")
    if not np.isfinite(vol_forecast) or vol_forecast <= 0:
        return 0.0
    return float(min(target_vol / vol_forecast, max_leverage))
