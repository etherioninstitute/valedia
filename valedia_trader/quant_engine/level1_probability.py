"""Level 1 - probability primitives (formulas 1.1 to 1.5).

Self-contained, pure, vectorised numpy/scipy implementations used for regime
probabilities, prediction-market fair values, fill probabilities, book-wide
expected P&L and order-arrival modelling.

Conventions
-----------
* Array-likes are accepted everywhere; scalar inputs give Python floats back.
* Invalid input raises ``ValueError`` with an explicit message. Undefined
  quantities (zero evidence, zero variance, ...) raise instead of returning NaN.
* Randomness only flows through an explicit ``rng: np.random.Generator``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import optimize, special, stats

__all__ = [
    "FORMULAS",
    "BetaPosterior",
    "PoissonFit",
    "SequentialBayesResult",
    "StudentTFit",
    "TotalVariance",
    "VarianceDecomposition",
    "bayes_binary_posterior",
    "bayes_posterior",
    "beta_binomial_update",
    "binomial_cdf",
    "binomial_pmf",
    "coupon_collector_expected_draws",
    "expected_count",
    "expected_pnl",
    "expected_pnl_contributions",
    "expected_value",
    "fit_student_t",
    "geometric_expected_trials",
    "levy_stable_cdf",
    "levy_stable_pdf",
    "levy_stable_rvs",
    "normal_cdf",
    "normal_mass_within",
    "normal_pdf",
    "poisson_cdf",
    "poisson_mle",
    "poisson_pmf",
    "prob_at_least_one_arrival",
    "sequential_bayes_update",
    "total_expectation",
    "total_variance",
    "variance",
    "variance_decomposition",
    "variance_of_sum",
]

FloatArray = NDArray[np.float64]

FORMULAS: dict[str, tuple[str, ...]] = {
    "1.1": (
        "bayes_posterior",
        "bayes_binary_posterior",
        "sequential_bayes_update",
        "beta_binomial_update",
    ),
    "1.2": ("expected_value", "expected_pnl", "expected_pnl_contributions", "expected_count"),
    "1.3": ("variance", "variance_of_sum", "variance_decomposition"),
    "1.4": ("total_expectation", "total_variance"),
    "1.5": (
        "binomial_pmf",
        "binomial_cdf",
        "poisson_pmf",
        "poisson_cdf",
        "poisson_mle",
        "prob_at_least_one_arrival",
        "normal_pdf",
        "normal_cdf",
        "normal_mass_within",
        "fit_student_t",
        "levy_stable_pdf",
        "levy_stable_cdf",
        "levy_stable_rvs",
        "geometric_expected_trials",
        "coupon_collector_expected_draws",
    ),
}

_PROB_TOL = 1e-6  # tolerates probabilities that were rounded through float32
_MAD_TO_SIGMA = 1.482602218505602


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


def _to_probability(x: ArrayLike, name: str, *, allow_empty: bool = False) -> FloatArray:
    arr = _to_array(x, name, allow_empty=allow_empty)
    if np.any((arr < 0.0) | (arr > 1.0)):
        raise ValueError(f"{name} must lie in [0, 1]")
    return arr


def _to_distribution(p: ArrayLike, name: str) -> FloatArray:
    """Probability vector(s) summing to one (within ``_PROB_TOL``) along the last axis.

    Vectors inside the tolerance are renormalised so downstream sums are exact.
    """
    arr = _to_probability(p, name)
    if arr.ndim == 0:
        raise ValueError(f"{name} must be a probability vector, not a scalar")
    total = arr.sum(axis=-1, keepdims=True)
    if np.any(np.abs(total - 1.0) > _PROB_TOL):
        raise ValueError(f"{name} must sum to 1 along the last axis")
    return arr / total


def _broadcast(names: tuple[str, ...], *arrays: FloatArray) -> list[FloatArray]:
    try:
        return list(np.broadcast_arrays(*arrays))
    except ValueError as exc:
        shapes = ", ".join(f"{n}{a.shape}" for n, a in zip(names, arrays, strict=True))
        raise ValueError(f"shape mismatch: cannot broadcast {shapes}") from exc


def _unwrap(arr: ArrayLike) -> float | FloatArray:
    out = np.asarray(arr, dtype=np.float64)
    return float(out) if out.ndim == 0 else out


def _to_int_valued(x: ArrayLike, name: str, *, minimum: int = 0) -> FloatArray:
    arr = _to_array(x, name)
    if np.any(arr != np.floor(arr)):
        raise ValueError(f"{name} must be integer-valued")
    if np.any(arr < minimum):
        raise ValueError(f"{name} must be >= {minimum}")
    return arr


def _to_cov(cov: ArrayLike, n: int) -> FloatArray:
    mat = _to_array(cov, "cov")
    if mat.ndim != 2 or mat.shape[0] != mat.shape[1]:
        raise ValueError(f"cov must be a square matrix, got shape {mat.shape}")
    if mat.shape[0] != n:
        raise ValueError(f"cov is {mat.shape[0]}x{mat.shape[0]} but there are {n} weights")
    scale = max(1.0, float(np.max(np.abs(mat))))
    if np.max(np.abs(mat - mat.T)) > 1e-8 * scale:
        raise ValueError("cov must be symmetric")
    return 0.5 * (mat + mat.T)


# --------------------------------------------------------------------------- 1.1 Bayes
def bayes_posterior(likelihood: ArrayLike, prior: ArrayLike, evidence: ArrayLike) -> float | FloatArray:
    """1.1 Bayes' rule: P(A|B) = P(B|A) P(A) / P(B).

    Vectorised with broadcasting. Raises if ``P(B) == 0`` (posterior undefined) or if the
    inputs are incoherent, i.e. the joint ``P(B|A) P(A)`` exceeds ``P(B)``.
    """
    lik = _to_probability(likelihood, "likelihood")
    pri = _to_probability(prior, "prior")
    ev = _to_probability(evidence, "evidence")
    lik, pri, ev = _broadcast(("likelihood", "prior", "evidence"), lik, pri, ev)
    if np.any(ev <= 0.0):
        raise ValueError("evidence P(B) must be > 0: the posterior is undefined when P(B) = 0")
    joint = lik * pri
    if np.any(joint > ev * (1.0 + _PROB_TOL) + 1e-15):
        raise ValueError("incoherent inputs: P(B|A) P(A) exceeds P(B)")
    return _unwrap(np.minimum(joint / ev, 1.0))


def bayes_binary_posterior(
    prior: ArrayLike,
    p_evidence_given_true: ArrayLike,
    p_evidence_given_false: ArrayLike,
) -> float | FloatArray:
    """1.1 Bayes' rule, two hypotheses: P(A|B) = P(B|A)P(A) / [P(B|A)P(A) + P(B|~A)P(~A)].

    ``p_evidence_given_true`` is the sensitivity (hit rate) and ``p_evidence_given_false``
    the false-positive rate ``1 - specificity``.
    """
    pri = _to_probability(prior, "prior")
    p1 = _to_probability(p_evidence_given_true, "p_evidence_given_true")
    p0 = _to_probability(p_evidence_given_false, "p_evidence_given_false")
    pri, p1, p0 = _broadcast(("prior", "p_evidence_given_true", "p_evidence_given_false"), pri, p1, p0)
    numerator = p1 * pri
    evidence = numerator + p0 * (1.0 - pri)
    if np.any(evidence <= 0.0):
        raise ValueError("the evidence has zero probability under both hypotheses: posterior undefined")
    return _unwrap(numerator / evidence)


@dataclass(frozen=True)
class SequentialBayesResult:
    """Posterior path of a sequential Bayes update over K hypotheses.

    ``posteriors[t]`` is the normalised posterior after observations ``0..t``;
    ``final`` is the last posterior (the normalised prior when the stream is empty);
    ``log_evidence[t]`` is ``log P(e_t | e_0..e_{t-1})`` (``-inf`` for skipped steps);
    ``skipped[t]`` flags observations ignored under ``on_impossible="skip"``.
    """

    posteriors: FloatArray
    final: FloatArray
    log_evidence: FloatArray
    skipped: NDArray[np.bool_]


def sequential_bayes_update(
    prior: ArrayLike,
    likelihoods: ArrayLike,
    *,
    log_likelihoods: bool = False,
    on_impossible: Literal["raise", "skip"] = "raise",
) -> SequentialBayesResult:
    """1.1 Sequential Bayes: P(R_k | e_1..e_t) ∝ P(R_k) prod_s P(e_s | R_k), in log space.

    Parameters
    ----------
    prior : (K,) non-negative weights; normalised internally.
    likelihoods : (T, K) stream of likelihood vectors ``P(e_t | R_k)`` (a single (K,)
        vector is treated as T = 1). Likelihoods may be densities (> 1). With
        ``log_likelihoods=True`` the entries are log-likelihoods and ``-inf`` is allowed.
    on_impossible : what to do when an observation has zero likelihood under every
        hypothesis that still carries posterior mass. ``"raise"`` (default) raises
        ``ValueError``; ``"skip"`` keeps the previous posterior and flags the step.
    """
    if on_impossible not in ("raise", "skip"):
        raise ValueError("on_impossible must be 'raise' or 'skip'")
    pri = _to_array(prior, "prior")
    if pri.ndim != 1:
        raise ValueError(f"prior must be a 1-D vector over hypotheses, got shape {pri.shape}")
    if np.any(pri < 0.0):
        raise ValueError("prior must be non-negative")
    mass = float(pri.sum())
    if mass <= 0.0:
        raise ValueError("prior must have positive total mass")
    k = pri.size
    with np.errstate(divide="ignore"):
        log_prior = np.log(pri / mass)

    try:
        lik = np.asarray(likelihoods, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("likelihoods must be numeric") from exc
    if lik.size == 0:
        lik = lik.reshape(0, k)
    elif lik.ndim == 1:
        lik = lik[np.newaxis, :]
    if lik.ndim != 2 or lik.shape[1] != k:
        raise ValueError(f"likelihoods must have shape (T, {k}), got {lik.shape}")
    if log_likelihoods:
        if np.any(np.isnan(lik)) or np.any(np.isposinf(lik)):
            raise ValueError("log-likelihoods must not contain NaN or +inf")
        log_lik = lik
    else:
        if not np.all(np.isfinite(lik)) or np.any(lik < 0.0):
            raise ValueError("likelihoods must be finite and non-negative")
        with np.errstate(divide="ignore"):
            log_lik = np.log(lik)

    n_obs = log_lik.shape[0]
    if n_obs == 0:
        return SequentialBayesResult(
            posteriors=np.empty((0, k)),
            final=np.exp(log_prior),
            log_evidence=np.empty(0),
            skipped=np.zeros(0, dtype=bool),
        )

    cumulative = log_prior + np.cumsum(log_lik, axis=0)
    log_norm = special.logsumexp(cumulative, axis=1)
    impossible = np.isneginf(log_norm)
    if impossible.any():
        if on_impossible == "raise":
            step = int(np.argmax(impossible))
            raise ValueError(
                f"observation {step} has zero likelihood under every hypothesis with posterior mass; "
                "pass on_impossible='skip' to ignore such observations"
            )
        return _sequential_bayes_skip(log_prior, log_lik)

    posteriors = np.exp(cumulative - log_norm[:, np.newaxis])
    posteriors /= posteriors.sum(axis=1, keepdims=True)
    return SequentialBayesResult(
        posteriors=posteriors,
        final=posteriors[-1].copy(),
        log_evidence=np.diff(log_norm, prepend=0.0),
        skipped=np.zeros(n_obs, dtype=bool),
    )


def _sequential_bayes_skip(log_prior: FloatArray, log_lik: FloatArray) -> SequentialBayesResult:
    """Step-by-step update that ignores observations impossible under the current posterior."""
    n_obs, k = log_lik.shape
    posteriors = np.empty((n_obs, k))
    log_evidence = np.empty(n_obs)
    skipped = np.zeros(n_obs, dtype=bool)
    log_post = log_prior.copy()
    for t in range(n_obs):
        candidate = log_post + log_lik[t]
        log_norm = float(special.logsumexp(candidate))
        if np.isneginf(log_norm):
            skipped[t] = True
        else:
            log_post = candidate - log_norm
        log_evidence[t] = log_norm
        row = np.exp(log_post)
        posteriors[t] = row / row.sum()
    return SequentialBayesResult(posteriors, posteriors[-1].copy(), log_evidence, skipped)


@dataclass(frozen=True)
class BetaPosterior:
    """Beta(alpha, beta) posterior for a binary-contract probability."""

    alpha: float | FloatArray
    beta: float | FloatArray
    mean: float | FloatArray
    variance: float | FloatArray
    ci_low: float | FloatArray
    ci_high: float | FloatArray
    credible_level: float


def beta_binomial_update(
    prior_alpha: ArrayLike,
    prior_beta: ArrayLike,
    successes: ArrayLike,
    failures: ArrayLike,
    *,
    credible_level: float = 0.95,
) -> BetaPosterior:
    """1.1 Beta-Binomial conjugate update: Beta(a, b) + (s, f) -> Beta(a + s, b + f).

    The posterior mean ``(a + s) / (a + b + s + f)`` is the Bayesian fair value of a binary
    contract. Counts may be fractional (weighted evidence). An equal-tailed credible
    interval at ``credible_level`` is reported.
    """
    a0 = _to_array(prior_alpha, "prior_alpha")
    b0 = _to_array(prior_beta, "prior_beta")
    s = _to_array(successes, "successes")
    f = _to_array(failures, "failures")
    if np.any(a0 <= 0.0) or np.any(b0 <= 0.0):
        raise ValueError("prior_alpha and prior_beta must be > 0")
    if np.any(s < 0.0) or np.any(f < 0.0):
        raise ValueError("successes and failures must be >= 0")
    if not 0.0 < credible_level < 1.0:
        raise ValueError("credible_level must lie strictly between 0 and 1")
    a0, b0, s, f = _broadcast(("prior_alpha", "prior_beta", "successes", "failures"), a0, b0, s, f)
    a = a0 + s
    b = b0 + f
    total = a + b
    tail = 0.5 * (1.0 - credible_level)
    return BetaPosterior(
        alpha=_unwrap(a),
        beta=_unwrap(b),
        mean=_unwrap(a / total),
        variance=_unwrap(a * b / (total * total * (total + 1.0))),
        ci_low=_unwrap(stats.beta.ppf(tail, a, b)),
        ci_high=_unwrap(stats.beta.ppf(1.0 - tail, a, b)),
        credible_level=float(credible_level),
    )


# --------------------------------------------------------------------------- 1.2 expectation
def expected_value(values: ArrayLike, probabilities: ArrayLike) -> float | FloatArray:
    """1.2 Expectation of a discrete distribution: E[X] = sum_i x_i p_i (last axis)."""
    x = _to_array(values, "values")
    p = _to_distribution(probabilities, "probabilities")
    x, p = _broadcast(("values", "probabilities"), x, p)
    return _unwrap(np.sum(x * p, axis=-1))


def expected_pnl_contributions(weights: ArrayLike, expected_returns: ArrayLike) -> FloatArray:
    """1.2 Per-position expected P&L: w_i E[X_i] (the terms of E[sum w_i X_i])."""
    w = np.atleast_1d(_to_array(weights, "weights", allow_empty=True))
    mu = np.atleast_1d(_to_array(expected_returns, "expected_returns", allow_empty=True))
    w, mu = _broadcast(("weights", "expected_returns"), w, mu)
    return w * mu


def expected_pnl(weights: ArrayLike, expected_returns: ArrayLike) -> float | FloatArray:
    """1.2 Linearity of expectation: E[sum_i w_i X_i] = sum_i w_i E[X_i].

    Book-wide expected P&L from weights/notionals and per-position expected returns. Holds
    for any dependence between positions, so no covariance input is needed. Sums over the
    last axis (leading axes are books/scenarios). An empty book has expected P&L 0.
    """
    return _unwrap(np.sum(expected_pnl_contributions(weights, expected_returns), axis=-1))


def expected_count(probabilities: ArrayLike) -> float | FloatArray:
    """1.2 Linearity with indicators: E[sum_i 1{A_i}] = sum_i P(A_i).

    Expected number of events (fills, contracts resolving YES, ...) regardless of how the
    events are correlated. Sums over the last axis; an empty set of events gives 0.
    """
    p = np.atleast_1d(_to_probability(probabilities, "probabilities", allow_empty=True))
    return _unwrap(np.sum(p, axis=-1))


# --------------------------------------------------------------------------- 1.3 variance
def variance(
    values: ArrayLike, probabilities: ArrayLike | None = None, *, ddof: int = 0
) -> float | FloatArray:
    """1.3 Variance: Var(X) = E[X^2] - (E[X])^2, along the last axis.

    With ``probabilities`` the values describe a discrete distribution; otherwise they are
    a sample and ``ddof`` selects the population (0) or unbiased (1) estimator. The result
    is evaluated in the algebraically identical centred form E[(X - E[X])^2], which avoids
    the catastrophic cancellation of the raw-moment form for large means.
    """
    x = np.atleast_1d(_to_array(values, "values"))
    if probabilities is not None:
        if ddof != 0:
            raise ValueError("ddof applies to samples only; omit it when probabilities are given")
        p = _to_distribution(probabilities, "probabilities")
        x, p = _broadcast(("values", "probabilities"), x, p)
        mean = np.sum(x * p, axis=-1, keepdims=True)
        return _unwrap(np.sum(p * (x - mean) ** 2, axis=-1))
    n = x.shape[-1]
    if n - ddof <= 0:
        raise ValueError(f"need more than {ddof} observations for ddof={ddof}, got {n}")
    mean = np.mean(x, axis=-1, keepdims=True)
    return _unwrap(np.sum((x - mean) ** 2, axis=-1) / (n - ddof))


def variance_of_sum(
    var_x: ArrayLike,
    var_y: ArrayLike,
    cov_xy: ArrayLike,
    *,
    a: ArrayLike = 1.0,
    b: ArrayLike = 1.0,
) -> float | FloatArray:
    """1.3 Var(aX + bY) = a^2 Var(X) + b^2 Var(Y) + 2ab Cov(X, Y) (a = b = 1 by default)."""
    vx = _to_array(var_x, "var_x")
    vy = _to_array(var_y, "var_y")
    cxy = _to_array(cov_xy, "cov_xy")
    wa = _to_array(a, "a")
    wb = _to_array(b, "b")
    if np.any(vx < 0.0) or np.any(vy < 0.0):
        raise ValueError("variances must be >= 0")
    vx, vy, cxy, wa, wb = _broadcast(("var_x", "var_y", "cov_xy", "a", "b"), vx, vy, cxy, wa, wb)
    bound = np.sqrt(vx * vy)
    if np.any(np.abs(cxy) > bound * (1.0 + _PROB_TOL) + 1e-15):
        raise ValueError("|cov_xy| exceeds sqrt(var_x * var_y): implied correlation is outside [-1, 1]")
    total = wa * wa * vx + wb * wb * vy + 2.0 * wa * wb * cxy
    return _unwrap(np.maximum(total, 0.0))


@dataclass(frozen=True)
class VarianceDecomposition:
    """Per-position split of portfolio variance (Euler allocation).

    ``contributions[i] = w_i (Sigma w)_i`` and ``contributions.sum() == total``;
    ``fractions = contributions / total`` (all zeros when ``total == 0``);
    ``marginal = 2 Sigma w`` is the gradient of the variance in the weights.
    """

    total: float
    contributions: FloatArray
    fractions: FloatArray
    marginal: FloatArray


def variance_decomposition(weights: ArrayLike, cov: ArrayLike) -> VarianceDecomposition:
    """1.3 Diversification: Var(sum w_i X_i) = sum_i w_i (Sigma w)_i, per-position terms.

    Each contribution collects the position's own variance plus half of every covariance
    cross term it participates in, so contributions sum exactly to the total variance.
    """
    w = _to_array(weights, "weights")
    if w.ndim != 1:
        raise ValueError(f"weights must be 1-D, got shape {w.shape}")
    sigma = _to_cov(cov, w.size)
    sigma_w = sigma @ w
    contributions = w * sigma_w
    total = float(contributions.sum())
    scale = float(np.sum(np.abs(contributions)))
    if total < -1e-9 * max(scale, np.finfo(float).tiny):
        raise ValueError("cov is not positive semi-definite: w' Sigma w is negative")
    total = max(total, 0.0)
    fractions = contributions / total if total > 0.0 else np.zeros_like(contributions)
    return VarianceDecomposition(total, contributions, fractions, 2.0 * sigma_w)


# --------------------------------------------------------------------------- 1.4 total expectation
def total_expectation(conditional_means: ArrayLike, regime_probs: ArrayLike) -> float | FloatArray:
    """1.4 Law of total expectation: E[X] = sum_k E[X | R_k] P(R_k) (regimes on the last axis)."""
    means = _to_array(conditional_means, "conditional_means")
    probs = _to_distribution(regime_probs, "regime_probs")
    means, probs = _broadcast(("conditional_means", "regime_probs"), means, probs)
    return _unwrap(np.sum(means * probs, axis=-1))


@dataclass(frozen=True)
class TotalVariance:
    """Law of total variance split: ``total = within + between``.

    ``within = E[Var(X | R)]`` (average in-regime risk) and ``between = Var(E[X | R])``
    (risk from not knowing the regime).
    """

    total: float | FloatArray
    within: float | FloatArray
    between: float | FloatArray
    mean: float | FloatArray


def total_variance(
    conditional_means: ArrayLike,
    conditional_variances: ArrayLike,
    regime_probs: ArrayLike,
) -> TotalVariance:
    """1.4 Law of total variance: Var(X) = E[Var(X | R)] + Var(E[X | R])."""
    means = _to_array(conditional_means, "conditional_means")
    variances = _to_array(conditional_variances, "conditional_variances")
    if np.any(variances < 0.0):
        raise ValueError("conditional_variances must be >= 0")
    probs = _to_distribution(regime_probs, "regime_probs")
    means, variances, probs = _broadcast(
        ("conditional_means", "conditional_variances", "regime_probs"), means, variances, probs
    )
    mean = np.sum(means * probs, axis=-1, keepdims=True)
    within = np.sum(variances * probs, axis=-1)
    between = np.sum(probs * (means - mean) ** 2, axis=-1)
    return TotalVariance(
        total=_unwrap(within + between),
        within=_unwrap(within),
        between=_unwrap(between),
        mean=_unwrap(mean[..., 0]),
    )


def geometric_expected_trials(p_success: ArrayLike) -> float | FloatArray:
    """1.5 Geometric expectation: E[N] = 1/p, from E[N] = p*1 + (1-p)(1 + E[N]).

    Expected number of independent trials up to and including the first success; the
    recursion is the law of total expectation (1.4) conditioned on the first trial.
    """
    p = _to_probability(p_success, "p_success")
    if np.any(p <= 0.0):
        raise ValueError("p_success must be > 0: the expected waiting time is infinite at p = 0")
    return _unwrap(1.0 / p)


def coupon_collector_expected_draws(n_types: int) -> float:
    """1.5 Coupon collector: E[T] = n * H_n = n * sum_{i=1..n} 1/i.

    Expected number of uniform draws needed to see all ``n_types`` distinct outcomes.
    """
    if isinstance(n_types, bool) or not isinstance(n_types, (int, np.integer)):
        raise ValueError("n_types must be an integer")
    n = int(n_types)
    if n < 0:
        raise ValueError("n_types must be >= 0")
    if n == 0:
        return 0.0
    return float(n * np.sum(1.0 / np.arange(1, n + 1, dtype=np.float64)))


# --------------------------------------------------------------------------- 1.5 distributions
def binomial_pmf(k: ArrayLike, n: ArrayLike, p: ArrayLike) -> float | FloatArray:
    """1.5 Binomial pmf: P(K = k) = C(n, k) p^k (1 - p)^(n - k)."""
    kk = _to_int_valued(k, "k", minimum=-np.iinfo(np.int64).max)
    nn = _to_int_valued(n, "n")
    pp = _to_probability(p, "p")
    kk, nn, pp = _broadcast(("k", "n", "p"), kk, nn, pp)
    return _unwrap(stats.binom.pmf(kk, nn, pp))


def binomial_cdf(k: ArrayLike, n: ArrayLike, p: ArrayLike) -> float | FloatArray:
    """1.5 Binomial cdf: P(K <= k) = sum_{j <= k} C(n, j) p^j (1 - p)^(n - j)."""
    kk = _to_array(k, "k")
    nn = _to_int_valued(n, "n")
    pp = _to_probability(p, "p")
    kk, nn, pp = _broadcast(("k", "n", "p"), kk, nn, pp)
    return _unwrap(stats.binom.cdf(kk, nn, pp))


def _to_rate(lam: ArrayLike, name: str = "lam") -> FloatArray:
    rate = _to_array(lam, name)
    if np.any(rate < 0.0):
        raise ValueError(f"{name} must be >= 0")
    return rate


def poisson_pmf(k: ArrayLike, lam: ArrayLike) -> float | FloatArray:
    """1.5 Poisson pmf: P(k) = lambda^k e^(-lambda) / k!."""
    kk = _to_int_valued(k, "k", minimum=-np.iinfo(np.int64).max)
    rate = _to_rate(lam)
    kk, rate = _broadcast(("k", "lam"), kk, rate)
    return _unwrap(stats.poisson.pmf(kk, rate))


def poisson_cdf(k: ArrayLike, lam: ArrayLike) -> float | FloatArray:
    """1.5 Poisson cdf: P(K <= k) = sum_{j <= k} lambda^j e^(-lambda) / j!."""
    kk = _to_array(k, "k")
    rate = _to_rate(lam)
    kk, rate = _broadcast(("k", "lam"), kk, rate)
    return _unwrap(stats.poisson.cdf(kk, rate))


@dataclass(frozen=True)
class PoissonFit:
    """Poisson MLE of an arrival intensity (events per unit exposure)."""

    rate: float
    std_err: float
    n_obs: int
    total_count: float
    total_exposure: float


def poisson_mle(counts: ArrayLike, exposure: ArrayLike | None = None) -> PoissonFit:
    """1.5 Poisson MLE: lambda_hat = sum(counts) / sum(exposure), SE = sqrt(lambda_hat / exposure).

    ``counts`` are event counts per observation window; ``exposure`` is the length of each
    window (default 1), so the rate is per unit of exposure. All-zero counts give the valid
    estimate ``rate = 0`` with ``std_err = 0``.
    """
    c = np.atleast_1d(_to_int_valued(counts, "counts"))
    if c.ndim != 1:
        raise ValueError(f"counts must be 1-D, got shape {c.shape}")
    if exposure is None:
        e = np.ones_like(c)
    else:
        e = np.atleast_1d(_to_array(exposure, "exposure"))
        if np.any(e <= 0.0):
            raise ValueError("exposure must be > 0")
        (c, e) = _broadcast(("counts", "exposure"), c, e)
    total_count = float(c.sum())
    total_exposure = float(e.sum())
    rate = total_count / total_exposure
    return PoissonFit(
        rate=rate,
        std_err=float(np.sqrt(rate / total_exposure)),
        n_obs=int(c.size),
        total_count=total_count,
        total_exposure=total_exposure,
    )


def prob_at_least_one_arrival(rate: ArrayLike, window: ArrayLike = 1.0) -> float | FloatArray:
    """1.5 Poisson arrivals: P(N >= 1 in window) = 1 - e^(-lambda * window)."""
    lam = _to_rate(rate, "rate")
    tau = _to_array(window, "window")
    if np.any(tau < 0.0):
        raise ValueError("window must be >= 0")
    lam, tau = _broadcast(("rate", "window"), lam, tau)
    return _unwrap(-np.expm1(-lam * tau))


def _to_scale(std: ArrayLike, name: str = "std") -> FloatArray:
    scale = _to_array(std, name)
    if np.any(scale <= 0.0):
        raise ValueError(f"{name} must be > 0")
    return scale


def normal_pdf(x: ArrayLike, mean: ArrayLike = 0.0, std: ArrayLike = 1.0) -> float | FloatArray:
    """1.5 Normal pdf: f(x) = exp(-(x - mu)^2 / (2 sigma^2)) / (sigma sqrt(2 pi))."""
    xx = _to_array(x, "x")
    mu = _to_array(mean, "mean")
    sigma = _to_scale(std)
    xx, mu, sigma = _broadcast(("x", "mean", "std"), xx, mu, sigma)
    return _unwrap(stats.norm.pdf(xx, loc=mu, scale=sigma))


def normal_cdf(x: ArrayLike, mean: ArrayLike = 0.0, std: ArrayLike = 1.0) -> float | FloatArray:
    """1.5 Normal cdf: Phi((x - mu) / sigma)."""
    xx = _to_array(x, "x")
    mu = _to_array(mean, "mean")
    sigma = _to_scale(std)
    xx, mu, sigma = _broadcast(("x", "mean", "std"), xx, mu, sigma)
    return _unwrap(stats.norm.cdf(xx, loc=mu, scale=sigma))


def normal_mass_within(n_sigma: ArrayLike) -> float | FloatArray:
    """1.5 Normal mass within k sigma: P(|X - mu| <= k sigma) = erf(k / sqrt(2))."""
    k = _to_array(n_sigma, "n_sigma")
    if np.any(k < 0.0):
        raise ValueError("n_sigma must be >= 0")
    return _unwrap(special.erf(k / np.sqrt(2.0)))


@dataclass(frozen=True)
class StudentTFit:
    """Maximum-likelihood location-scale Student-t fit with a tail comparison.

    ``tail_prob_t`` is ``P(|X - loc| > tail_sigmas * sample_std)`` under the fitted t and
    ``tail_prob_normal`` the same event under a normal with the sample standard deviation;
    ``tail_ratio > 1`` means fatter tails than normal. ``df_at_bound`` flags a degrees of
    freedom estimate pinned at ``df_bounds`` (the upper bound means "indistinguishable
    from normal").
    """

    df: float
    loc: float
    scale: float
    log_likelihood: float
    n_obs: int
    sample_std: float
    tail_sigmas: float
    tail_prob_t: float
    tail_prob_normal: float
    tail_ratio: float
    converged: bool
    df_at_bound: bool


def _student_t_nll(theta: FloatArray, z: FloatArray) -> float:
    """Negative log-likelihood of standardised data under t(nu, loc, scale), log-parametrised."""
    nu = np.exp(theta[0])
    scale = np.exp(theta[2])
    resid = (z - theta[1]) / scale
    log_pdf = (
        special.gammaln(0.5 * (nu + 1.0))
        - special.gammaln(0.5 * nu)
        - 0.5 * np.log(nu * np.pi)
        - theta[2]
        - 0.5 * (nu + 1.0) * np.log1p(resid * resid / nu)
    )
    return float(-np.sum(log_pdf))


def fit_student_t(
    data: ArrayLike,
    *,
    tail_sigmas: float = 3.0,
    df_bounds: tuple[float, float] = (0.5, 1000.0),
) -> StudentTFit:
    """1.5 Student-t fit: MLE of (df, loc, scale) for f(x) ∝ (1 + ((x-loc)/scale)^2/df)^(-(df+1)/2).

    Fat-tail diagnostic for returns. The likelihood is maximised with bounded L-BFGS-B on
    robustly standardised data from several starting degrees of freedom. Requires at least
    4 observations and non-zero variance; raises if the likelihood is degenerate (scale
    collapsing onto repeated observations).
    """
    x = _to_array(data, "data")
    if x.ndim != 1:
        raise ValueError(f"data must be 1-D, got shape {x.shape}")
    n = x.size
    if n < 4:
        raise ValueError(f"need at least 4 observations to fit a Student-t, got {n}")
    if not 0.0 < tail_sigmas <= 30.0:
        raise ValueError("tail_sigmas must lie in (0, 30]")
    df_lo, df_hi = (float(df_bounds[0]), float(df_bounds[1]))
    if not 0.0 < df_lo < df_hi or not np.isfinite(df_hi):
        raise ValueError("df_bounds must satisfy 0 < lower < upper < inf")
    sample_std = float(np.std(x, ddof=1))
    if sample_std <= 1e-14 * max(1.0, float(np.max(np.abs(x)))):
        raise ValueError("data has zero variance: a Student-t fit is undefined")

    median = float(np.median(x))
    mad = float(np.median(np.abs(x - median))) * _MAD_TO_SIGMA
    unit = mad if mad > 1e-8 * sample_std else sample_std
    z = (x - median) / unit

    bounds = [(np.log(df_lo), np.log(df_hi)), (float(z.min()), float(z.max())), (-14.0, 14.0)]
    starts = sorted({float(np.clip(nu0, df_lo, df_hi)) for nu0 in (2.5, 6.0, 30.0)})
    best: optimize.OptimizeResult | None = None
    for nu0 in starts:
        res = optimize.minimize(
            _student_t_nll,
            x0=np.array([np.log(nu0), 0.0, 0.0]),
            args=(z,),
            method="L-BFGS-B",
            bounds=bounds,
        )
        if np.isfinite(res.fun) and (best is None or res.fun < best.fun):
            best = res
    if best is None:
        raise ValueError("Student-t likelihood could not be evaluated for this data")
    if abs(float(best.x[2])) >= 14.0 - 1e-6:
        raise ValueError(
            "degenerate Student-t fit: the scale collapsed or exploded (typically because a large "
            "share of the observations are identical), so the likelihood is unbounded"
        )

    df = float(np.clip(np.exp(best.x[0]), df_lo, df_hi))
    loc = median + unit * float(best.x[1])
    scale = unit * float(np.exp(best.x[2]))
    threshold = tail_sigmas * sample_std
    tail_t = float(2.0 * stats.t.sf(threshold / scale, df))
    tail_normal = float(2.0 * stats.norm.sf(tail_sigmas))
    return StudentTFit(
        df=df,
        loc=loc,
        scale=scale,
        log_likelihood=float(-best.fun - n * np.log(unit)),
        n_obs=n,
        sample_std=sample_std,
        tail_sigmas=float(tail_sigmas),
        tail_prob_t=tail_t,
        tail_prob_normal=tail_normal,
        tail_ratio=tail_t / tail_normal,
        converged=bool(best.success),
        df_at_bound=bool(df <= df_lo * (1.0 + 1e-6) or df >= df_hi * (1.0 - 1e-6)),
    )


def _check_stable_params(
    alpha: float, beta: float, loc: float, scale: float
) -> tuple[float, float, float, float]:
    try:
        alpha, beta, loc, scale = (float(alpha), float(beta), float(loc), float(scale))
    except (TypeError, ValueError) as exc:
        raise ValueError("alpha, beta, loc and scale must be real scalars") from exc
    if not 0.0 < alpha <= 2.0:
        raise ValueError("alpha (tail index) must lie in (0, 2]")
    if not -1.0 <= beta <= 1.0:
        raise ValueError("beta (skewness) must lie in [-1, 1]")
    if not np.isfinite(loc):
        raise ValueError("loc must be finite")
    if not (scale > 0.0 and np.isfinite(scale)):
        raise ValueError("scale must be > 0 and finite")
    return alpha, beta, loc, scale


def levy_stable_pdf(
    x: ArrayLike, alpha: float, beta: float = 0.0, *, loc: float = 0.0, scale: float = 1.0
) -> float | FloatArray:
    """1.5 Levy alpha-stable pdf; char. function exp(i t loc - |scale t|^alpha (1 - i beta sgn(t) Phi)).

    Wrapper over ``scipy.stats.levy_stable`` (S1 parameterisation). ``alpha = 2`` is normal
    with standard deviation ``sqrt(2) * scale``; ``alpha = 1, beta = 0`` is Cauchy.
    """
    alpha, beta, loc, scale = _check_stable_params(alpha, beta, loc, scale)
    xx = _to_array(x, "x")
    return _unwrap(stats.levy_stable.pdf(xx, alpha, beta, loc=loc, scale=scale))


def levy_stable_cdf(
    x: ArrayLike, alpha: float, beta: float = 0.0, *, loc: float = 0.0, scale: float = 1.0
) -> float | FloatArray:
    """1.5 Levy alpha-stable cdf F(x) = P(X <= x) for X ~ S(alpha, beta, loc, scale)."""
    alpha, beta, loc, scale = _check_stable_params(alpha, beta, loc, scale)
    xx = _to_array(x, "x")
    return _unwrap(np.clip(stats.levy_stable.cdf(xx, alpha, beta, loc=loc, scale=scale), 0.0, 1.0))


def levy_stable_rvs(
    alpha: float,
    beta: float = 0.0,
    *,
    loc: float = 0.0,
    scale: float = 1.0,
    size: int | tuple[int, ...],
    rng: np.random.Generator,
) -> FloatArray:
    """1.5 Levy alpha-stable random variates X ~ S(alpha, beta, loc, scale) from an explicit rng."""
    alpha, beta, loc, scale = _check_stable_params(alpha, beta, loc, scale)
    if not isinstance(rng, np.random.Generator):
        raise ValueError("rng must be a numpy.random.Generator")
    shape = (size,) if isinstance(size, (int, np.integer)) else tuple(size)
    if any((not isinstance(s, (int, np.integer))) or s < 0 for s in shape):
        raise ValueError("size must be a non-negative int or a tuple of non-negative ints")
    draws = stats.levy_stable.rvs(alpha, beta, loc=loc, scale=scale, size=shape, random_state=rng)
    return np.asarray(draws, dtype=np.float64).reshape(shape)
