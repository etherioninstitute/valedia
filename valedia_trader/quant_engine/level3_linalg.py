"""Level 3 - linear algebra for covariance and risk (formulas 3.1 to 3.5).

Self-contained, pure numpy/scipy/scikit-learn implementations: covariance estimation with
PSD repair, portfolio variance, eigen-decomposition/PCA with residualisation, conditioning
guards with a stable solve, and covariance shrinkage.

Conventions
-----------
* Return matrices are ``(T, N)``: rows are observations, columns are assets.
* Covariance inputs must be square, finite and symmetric (to a relative 1e-8); they are
  symmetrised before use. ``nearest_psd`` is the one place that accepts asymmetric input.
* Invalid input raises ``ValueError``; singular or ill-conditioned matrices are reported
  explicitly (``inf`` condition number, ``safe_to_invert = False``) rather than inverted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import linalg as sla
from sklearn.covariance import OAS, LedoitWolf

__all__ = [
    "FORMULAS",
    "ConditionReport",
    "EigenDecomposition",
    "PCAResult",
    "ResidualizationResult",
    "ShrinkageResult",
    "StableSolveResult",
    "condition_number",
    "covariance_matrix",
    "eigen_decomposition",
    "inversion_guard",
    "is_psd",
    "ledoit_wolf_shrinkage",
    "nearest_psd",
    "oas_shrinkage",
    "pca",
    "portfolio_variance",
    "portfolio_volatility",
    "residualize_returns",
    "shrink_covariance",
    "shrinkage_target",
    "stable_solve",
]

FloatArray = NDArray[np.float64]

FORMULAS: dict[str, tuple[str, ...]] = {
    "3.1": ("covariance_matrix", "is_psd", "nearest_psd"),
    "3.2": ("portfolio_variance", "portfolio_volatility"),
    "3.3": ("eigen_decomposition", "pca", "residualize_returns"),
    "3.4": ("condition_number", "inversion_guard", "stable_solve"),
    "3.5": ("ledoit_wolf_shrinkage", "oas_shrinkage", "shrinkage_target", "shrink_covariance"),
}

_EPS = float(np.finfo(np.float64).eps)
_TINY = float(np.finfo(np.float64).tiny)
_SYM_RTOL = 1e-8


# --------------------------------------------------------------------------- helpers
def _to_array(x: ArrayLike, name: str) -> FloatArray:
    """Convert to a non-empty finite float array or raise ``ValueError``."""
    try:
        arr = np.asarray(x, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if arr.size == 0:
        raise ValueError(f"{name} must not be empty")
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} must contain only finite values (no NaN/inf)")
    return arr


def _to_square(matrix: ArrayLike, name: str) -> FloatArray:
    mat = _to_array(matrix, name)
    if mat.ndim != 2 or mat.shape[0] != mat.shape[1]:
        raise ValueError(f"{name} must be a square matrix, got shape {mat.shape}")
    return mat


def _is_symmetric(mat: FloatArray) -> bool:
    scale = max(float(np.max(np.abs(mat))), _TINY)
    return bool(np.max(np.abs(mat - mat.T)) <= _SYM_RTOL * scale)


def _to_symmetric(matrix: ArrayLike, name: str = "cov") -> FloatArray:
    """Square, finite, symmetric (within tolerance) matrix, returned exactly symmetrised."""
    mat = _to_square(matrix, name)
    if not _is_symmetric(mat):
        raise ValueError(f"{name} must be symmetric")
    return 0.5 * (mat + mat.T)


def _to_returns(returns: ArrayLike, name: str = "returns", *, min_obs: int = 2) -> FloatArray:
    data = _to_array(returns, name)
    if data.ndim == 1:
        data = data[:, np.newaxis]
    if data.ndim != 2:
        raise ValueError(f"{name} must be a (T, N) matrix, got shape {data.shape}")
    if data.shape[0] < min_obs:
        raise ValueError(f"{name} needs at least {min_obs} observations (rows), got {data.shape[0]}")
    return data


def _centre(data: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Demean columns; exactly-constant columns become exactly zero (not rounding noise).

    Without this, an asset that never moves would carry a ~1e-36 "variance" that PCA and
    explained-variance ratios would then treat as signal.
    """
    mean = data.mean(axis=0)
    centred = data - mean
    constant = np.ptp(data, axis=0) == 0.0
    if constant.any():
        centred[:, constant] = 0.0
        mean = np.where(constant, data[0], mean)
    return centred, mean


def _psd_tolerance(eigenvalues: FloatArray) -> float:
    """Eigenvalues above ``-tol`` are indistinguishable from zero at this matrix scale."""
    return eigenvalues.size * _EPS * max(float(np.max(np.abs(eigenvalues))), _TINY) * 8.0


def _fix_signs(vectors: FloatArray) -> FloatArray:
    """Deterministic orientation: the largest-magnitude entry of each column is positive."""
    idx = np.argmax(np.abs(vectors), axis=0)
    signs = np.sign(vectors[idx, np.arange(vectors.shape[1])])
    signs[signs == 0.0] = 1.0
    return vectors * signs


def _sorted_eigh(sym: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Eigen-decomposition of a symmetric matrix, eigenvalues in descending order."""
    values, vectors = np.linalg.eigh(sym)
    order = np.argsort(values)[::-1]
    return values[order], _fix_signs(vectors[:, order])


# --------------------------------------------------------------------------- 3.1 covariance and PSD
def covariance_matrix(returns: ArrayLike, *, ddof: int = 1, assume_centered: bool = False) -> FloatArray:
    """3.1 Covariance matrix: Sigma_ij = Cov(r_i, r_j) = sum_t (r_ti - m_i)(r_tj - m_j) / (T - ddof).

    ``returns`` is ``(T, N)``. A sample covariance is PSD by construction; assets that never
    move simply get a zero row/column.
    """
    data = _to_returns(returns)
    t = data.shape[0]
    if t - ddof <= 0:
        raise ValueError(f"need more than {ddof} observations for ddof={ddof}, got {t}")
    centred = data if assume_centered else _centre(data)[0]
    with np.errstate(over="ignore", invalid="ignore"):
        cov = centred.T @ centred / (t - ddof)
    if not np.all(np.isfinite(cov)):
        raise ValueError("the covariance overflows float64: rescale the returns to sensible units")
    return 0.5 * (cov + cov.T)


def is_psd(matrix: ArrayLike, *, tol: float | None = None) -> bool:
    """3.1 PSD check: Sigma is PSD iff it is symmetric and lambda_min(Sigma) >= 0.

    ``tol`` is the absolute slack on the smallest eigenvalue; the default scales with the
    matrix (``8 N eps |lambda|_max``) so rounding noise does not flag a valid covariance.
    """
    mat = _to_square(matrix, "matrix")
    if tol is not None and (not np.isfinite(tol) or tol < 0.0):
        raise ValueError("tol must be a non-negative finite number")
    if not _is_symmetric(mat):
        return False
    eigenvalues = np.linalg.eigvalsh(0.5 * (mat + mat.T))
    slack = _psd_tolerance(eigenvalues) if tol is None else tol
    return bool(eigenvalues[0] >= -slack)


def nearest_psd(
    matrix: ArrayLike,
    *,
    min_eigenvalue: float = 0.0,
    preserve_diagonal: bool = False,
) -> FloatArray:
    """3.1 PSD projection by eigenvalue clipping: Sigma_+ = V max(Lambda, floor) V'.

    The input is symmetrised first (``(A + A') / 2``), then eigenvalues below
    ``min_eigenvalue`` are raised to it. With a floor of 0 this is the Frobenius-nearest
    PSD matrix. ``preserve_diagonal`` rescales the result by a congruence ``D Sigma_+ D``
    so the original variances are kept (the correlation structure absorbs the repair);
    that requires a non-negative input diagonal, and the eigenvalue floor then applies
    before the rescaling (the result is PSD, but its smallest eigenvalue may be below
    ``min_eigenvalue``). A final rounding guard nudges the result (by a few ulps) until
    ``numpy.linalg.eigvalsh`` confirms the floor, so downstream PSD checks pass.
    """
    mat = _to_square(matrix, "matrix")
    if not np.isfinite(min_eigenvalue) or min_eigenvalue < 0.0:
        raise ValueError("min_eigenvalue must be a non-negative finite number")
    sym = 0.5 * (mat + mat.T)
    diagonal = np.diag(sym).copy()
    if preserve_diagonal and np.any(diagonal < 0.0):
        raise ValueError("preserve_diagonal requires a non-negative diagonal (variances)")
    values, vectors = np.linalg.eigh(sym)
    clipped = np.maximum(values, min_eigenvalue)
    if np.array_equal(clipped, values):
        return sym
    projected = (vectors * clipped) @ vectors.T
    projected = 0.5 * (projected + projected.T)
    if preserve_diagonal:
        new_diag = np.diag(projected)
        scale = np.zeros_like(diagonal)
        positive = new_diag > 0.0
        scale[positive] = np.sqrt(diagonal[positive] / new_diag[positive])
        projected = projected * np.outer(scale, scale)
        projected = 0.5 * (projected + projected.T)
        np.fill_diagonal(projected, diagonal)
        return _confirm_eigenvalue_floor(projected, 0.0, keep_diagonal=True)
    return _confirm_eigenvalue_floor(projected, min_eigenvalue, keep_diagonal=False)


def _confirm_eigenvalue_floor(matrix: FloatArray, floor: float, *, keep_diagonal: bool) -> FloatArray:
    """Rounding guard: nudge ``matrix`` by a few ulps until eigvalsh confirms lambda_min >= floor.

    Reconstructing ``V Lambda V'`` in floating point can leave the smallest eigenvalue a
    rounding error below the floor. Either the diagonal is lifted (default) or, when the
    diagonal must be kept, the off-diagonals are shrunk; both moves preserve PSD-ness and
    are bounded at ~1e3 N eps relative, far below any estimation noise.
    """
    n = matrix.shape[0]
    scale = max(float(np.max(np.abs(np.diag(matrix)))), _TINY)
    step = 4.0 * n * _EPS
    for _ in range(8):
        low = float(np.linalg.eigvalsh(matrix)[0])
        if low >= floor:
            break
        if keep_diagonal:
            diagonal = np.diag(matrix).copy()
            matrix = (1.0 - step) * matrix
            np.fill_diagonal(matrix, diagonal)
        else:
            matrix = matrix + np.eye(n) * ((floor - low) + step * scale)
        step *= 2.0
    return matrix


# --------------------------------------------------------------------------- 3.2 portfolio variance
def portfolio_variance(weights: ArrayLike, cov: ArrayLike) -> float | FloatArray:
    """3.2 Portfolio variance: sigma_p^2 = w' Sigma w.

    ``weights`` is ``(N,)`` or ``(..., N)``; the quadratic form is evaluated for every weight
    vector at once. Raises if a result is materially negative (``cov`` is not PSD).
    """
    sigma = _to_symmetric(cov)
    w = _to_array(weights, "weights")
    if w.ndim == 0:
        raise ValueError("weights must be at least 1-D")
    n = sigma.shape[0]
    if w.shape[-1] != n:
        raise ValueError(f"shape mismatch: weights have {w.shape[-1]} assets but cov is {n}x{n}")
    variance = np.sum((w @ sigma) * w, axis=-1)
    magnitude = np.sum((np.abs(w) @ np.abs(sigma)) * np.abs(w), axis=-1)
    if np.any(variance < -1e-9 * np.maximum(magnitude, _TINY)):
        raise ValueError("cov is not positive semi-definite: w' Sigma w is negative")
    variance = np.maximum(variance, 0.0)
    return float(variance) if variance.ndim == 0 else variance


def portfolio_volatility(weights: ArrayLike, cov: ArrayLike) -> float | FloatArray:
    """3.2 Portfolio volatility: sigma_p = sqrt(w' Sigma w)."""
    variance = portfolio_variance(weights, cov)
    vol = np.sqrt(variance)
    return float(vol) if np.ndim(vol) == 0 else vol


# --------------------------------------------------------------------------- 3.3 eigen / PCA
@dataclass(frozen=True)
class EigenDecomposition:
    """Spectral decomposition ``Sigma = V diag(lambda) V'`` with eigenvalues descending.

    ``eigenvectors[:, k]`` is the unit eigenvector of ``eigenvalues[k]``, oriented so its
    largest-magnitude entry is positive. ``explained_variance_ratio`` is
    ``lambda_k / sum(lambda)`` (all zeros for the zero matrix).
    """

    eigenvalues: FloatArray
    eigenvectors: FloatArray
    explained_variance_ratio: FloatArray


def _explained_ratio(eigenvalues: FloatArray, total: float) -> FloatArray:
    return eigenvalues / total if total > 0.0 else np.zeros_like(eigenvalues)


def eigen_decomposition(cov: ArrayLike, *, require_psd: bool = True) -> EigenDecomposition:
    """3.3 Eigen-decomposition: Sigma v_k = lambda_k v_k, sorted by descending lambda_k.

    With ``require_psd`` (default) a materially negative eigenvalue raises and rounding-level
    negatives are clipped to zero, so the explained-variance ratios are well defined.
    """
    sigma = _to_symmetric(cov)
    values, vectors = _sorted_eigh(sigma)
    if require_psd:
        if values[-1] < -_psd_tolerance(values):
            raise ValueError(
                f"cov is not positive semi-definite (smallest eigenvalue {values[-1]:.3e}); "
                "project it with nearest_psd first"
            )
        values = np.maximum(values, 0.0)
    positive = np.maximum(values, 0.0)
    return EigenDecomposition(values, vectors, _explained_ratio(positive, float(positive.sum())))


@dataclass(frozen=True)
class PCAResult:
    """Top-k principal components of a return panel.

    ``components`` is ``(k, N)`` with unit-norm rows (factor portfolios / loadings);
    ``factor_returns = (returns - mean) @ components.T`` is ``(T, k)``; ``eigenvalues`` are
    the factor variances and ``explained_variance_ratio`` their share of ``total_variance``.
    """

    eigenvalues: FloatArray
    components: FloatArray
    explained_variance_ratio: FloatArray
    factor_returns: FloatArray
    mean: FloatArray
    total_variance: float


def _check_n_components(n_components: int | None, n_assets: int, *, minimum: int) -> int:
    if n_components is None:
        return n_assets
    if isinstance(n_components, bool) or not isinstance(n_components, (int, np.integer)):
        raise ValueError("n_components must be an integer")
    if not minimum <= n_components <= n_assets:
        raise ValueError(f"n_components must lie in [{minimum}, {n_assets}], got {n_components}")
    return int(n_components)


def pca(
    returns: ArrayLike,
    n_components: int | None = None,
    *,
    demean: bool = True,
    ddof: int = 1,
) -> PCAResult:
    """3.3 PCA / top-k factor extraction: Sigma v_k = lambda_k v_k, factors f_k = (r - m) v_k.

    Uses ``numpy.linalg.eigh`` on the sample covariance. ``n_components=None`` keeps all N.
    """
    data = _to_returns(returns)
    k = _check_n_components(n_components, data.shape[1], minimum=1)
    centred, mean = _centre(data) if demean else (data, np.zeros(data.shape[1]))
    cov = covariance_matrix(centred, ddof=ddof, assume_centered=True)
    values, vectors = _sorted_eigh(cov)
    values = np.maximum(values, 0.0)
    total = float(values.sum())
    components = vectors[:, :k].T
    return PCAResult(
        eigenvalues=values[:k],
        components=components,
        explained_variance_ratio=_explained_ratio(values[:k], total),
        factor_returns=centred @ components.T,
        mean=mean,
        total_variance=total,
    )


@dataclass(frozen=True)
class ResidualizationResult:
    """Returns with the top-k principal components stripped out.

    ``residuals = (returns - mean) (I - V_k V_k')`` are the idiosyncratic returns, orthogonal
    to every removed factor; ``systematic = factor_returns @ components`` is what was
    removed, so ``residuals + systematic == returns - mean``.
    """

    residuals: FloatArray
    systematic: FloatArray
    factor_returns: FloatArray
    components: FloatArray
    explained_variance_ratio: FloatArray
    mean: FloatArray


def residualize_returns(
    returns: ArrayLike,
    n_components: int,
    *,
    demean: bool = True,
    ddof: int = 1,
) -> ResidualizationResult:
    """3.3 PCA residualisation: e = (r - m)(I - V_k V_k'), removing the top-k eigen-factors.

    ``n_components=0`` returns the demeaned returns unchanged.
    """
    data = _to_returns(returns)
    n_assets = data.shape[1]
    if n_components is None:
        raise ValueError("n_components is required")
    k = _check_n_components(n_components, n_assets, minimum=0)
    if k == 0:
        centred, mean = _centre(data) if demean else (data.copy(), np.zeros(n_assets))
        return ResidualizationResult(
            residuals=centred,
            systematic=np.zeros_like(centred),
            factor_returns=np.empty((data.shape[0], 0)),
            components=np.empty((0, n_assets)),
            explained_variance_ratio=np.empty(0),
            mean=mean,
        )
    fit = pca(data, k, demean=demean, ddof=ddof)
    centred = _centre(data)[0] if demean else data
    systematic = fit.factor_returns @ fit.components
    return ResidualizationResult(
        residuals=centred - systematic,
        systematic=systematic,
        factor_returns=fit.factor_returns,
        components=fit.components,
        explained_variance_ratio=fit.explained_variance_ratio,
        mean=fit.mean,
    )


# --------------------------------------------------------------------------- 3.4 conditioning
def _spectrum(cov: ArrayLike) -> tuple[FloatArray, FloatArray, FloatArray]:
    sigma = _to_symmetric(cov)
    values, vectors = np.linalg.eigh(sigma)
    return sigma, values, vectors


def _condition_from_eigenvalues(values: FloatArray) -> float:
    lam_max = float(values[-1])
    lam_min = float(values[0])
    if lam_max <= 0.0 or lam_min <= 0.0:
        return float("inf")
    return lam_max / lam_min


def condition_number(cov: ArrayLike) -> float:
    """3.4 Condition number: kappa = lambda_max / lambda_min.

    ``inf`` when the matrix is singular or not positive definite (``lambda_min <= 0``).
    """
    _, values, _ = _spectrum(cov)
    return _condition_from_eigenvalues(values)


def _check_max_condition(max_condition: float) -> float:
    if not np.isfinite(max_condition) or max_condition <= 1.0:
        raise ValueError("max_condition must be a finite number > 1")
    return float(max_condition)


@dataclass(frozen=True)
class ConditionReport:
    """Verdict of the inversion guard.

    ``effective_rank`` counts eigenvalues above ``lambda_max / threshold``, i.e. the
    directions that can be inverted without amplifying noise beyond the threshold.
    """

    condition_number: float
    lambda_max: float
    lambda_min: float
    threshold: float
    safe_to_invert: bool
    effective_rank: int


def inversion_guard(cov: ArrayLike, *, max_condition: float = 1e6) -> ConditionReport:
    """3.4 Inversion guard: safe to invert iff kappa = lambda_max / lambda_min <= max_condition.

    Errors in ``Sigma^-1 mu`` are amplified by up to ``kappa``, so an ill-conditioned
    covariance turns estimation noise into extreme weights.
    """
    threshold = _check_max_condition(max_condition)
    _, values, _ = _spectrum(cov)
    kappa = _condition_from_eigenvalues(values)
    lam_max = float(values[-1])
    rank = int(np.count_nonzero(values > lam_max / threshold)) if lam_max > 0.0 else 0
    return ConditionReport(
        condition_number=kappa,
        lambda_max=lam_max,
        lambda_min=float(values[0]),
        threshold=threshold,
        safe_to_invert=bool(kappa <= threshold),
        effective_rank=rank,
    )


@dataclass(frozen=True)
class StableSolveResult:
    """Solution of ``Sigma x = b`` plus how it was obtained.

    ``method`` is ``"cholesky"`` (well-conditioned, exact), ``"pinv"`` (eigenvalues below
    ``lambda_max / max_condition`` dropped) or ``"ridge"`` (``regularization`` added to the
    diagonal). ``condition_number`` is that of the original matrix.
    """

    solution: FloatArray
    method: str
    condition_number: float
    regularization: float
    effective_rank: int


def stable_solve(
    cov: ArrayLike,
    b: ArrayLike,
    *,
    max_condition: float = 1e6,
    fallback: Literal["pinv", "ridge"] = "pinv",
    ridge: float | None = None,
) -> StableSolveResult:
    """3.4 Stable solve of Sigma x = b, guarded by kappa = lambda_max / lambda_min.

    Well-conditioned systems use a Cholesky solve. Otherwise ``fallback="pinv"`` applies the
    truncated pseudo-inverse and ``fallback="ridge"`` solves ``(Sigma + r I) x = b``; when
    ``ridge`` is not given, ``r`` is the smallest value that brings the condition number
    down to ``max_condition``. ``b`` may be ``(N,)`` or ``(N, M)``.
    """
    threshold = _check_max_condition(max_condition)
    if fallback not in ("pinv", "ridge"):
        raise ValueError("fallback must be 'pinv' or 'ridge'")
    if ridge is not None and (not np.isfinite(ridge) or ridge <= 0.0):
        raise ValueError("ridge must be a positive finite number")
    sigma, values, vectors = _spectrum(cov)
    rhs = _to_array(b, "b")
    n = sigma.shape[0]
    if rhs.ndim not in (1, 2) or rhs.shape[0] != n:
        raise ValueError(f"shape mismatch: b has shape {rhs.shape} but cov is {n}x{n}")
    kappa = _condition_from_eigenvalues(values)
    lam_max = float(values[-1])
    lam_min = float(values[0])

    if kappa <= threshold:
        try:
            solution = sla.cho_solve(sla.cho_factor(sigma, lower=True, check_finite=False), rhs)
            return StableSolveResult(solution, "cholesky", kappa, 0.0, n)
        except sla.LinAlgError:
            pass  # numerically not positive definite after all: use the requested fallback

    if fallback == "ridge":
        if ridge is None:
            if lam_max <= 0.0:
                raise ValueError("cannot choose a ridge for a matrix with no positive eigenvalue; pass ridge")
            ridge_value = max((lam_max - threshold * lam_min) / (threshold - 1.0), lam_max * _EPS)
        else:
            ridge_value = float(ridge)
        if lam_min + ridge_value <= 0.0:
            raise ValueError("ridge is too small to make the matrix positive definite")
        coords = vectors.T @ rhs
        scaled = coords / (values + ridge_value)[(...,) + (np.newaxis,) * (rhs.ndim - 1)]
        return StableSolveResult(vectors @ scaled, "ridge", kappa, ridge_value, n)

    keep = values > lam_max / threshold if lam_max > 0.0 else np.zeros(n, dtype=bool)
    inverse = np.zeros(n)
    inverse[keep] = 1.0 / values[keep]
    coords = vectors.T @ rhs
    scaled = coords * inverse[(...,) + (np.newaxis,) * (rhs.ndim - 1)]
    return StableSolveResult(vectors @ scaled, "pinv", kappa, 0.0, int(np.count_nonzero(keep)))


# --------------------------------------------------------------------------- 3.5 shrinkage
@dataclass(frozen=True)
class ShrinkageResult:
    """Shrunk covariance ``(1 - delta) Sigma + delta F`` with its intensity and target."""

    covariance: FloatArray
    delta: float
    target: FloatArray
    method: str


def shrinkage_target(
    sample_cov: ArrayLike,
    kind: Literal["scaled_identity", "constant_correlation"] = "scaled_identity",
) -> FloatArray:
    """3.5 Shrinkage target F: scaled identity (tr(Sigma)/N) I, or constant correlation.

    The constant-correlation target keeps every variance and sets
    ``F_ij = rho_bar sqrt(Sigma_ii Sigma_jj)`` with ``rho_bar`` the mean pairwise sample
    correlation (zero-variance assets are excluded from the mean and get zero covariances).
    """
    sigma = _to_symmetric(sample_cov, "sample_cov")
    n = sigma.shape[0]
    variances = np.diag(sigma)
    if np.any(variances < 0.0):
        raise ValueError("sample_cov has a negative variance on its diagonal")
    if kind == "scaled_identity":
        return np.eye(n) * (float(np.trace(sigma)) / n)
    if kind != "constant_correlation":
        raise ValueError("kind must be 'scaled_identity' or 'constant_correlation'")
    vol = np.sqrt(variances)
    live = vol > 0.0
    n_live = int(np.count_nonzero(live))
    rho_bar = 0.0
    if n_live >= 2:
        sub = sigma[np.ix_(live, live)] / np.outer(vol[live], vol[live])
        rho_bar = float((sub.sum() - n_live) / (n_live * (n_live - 1)))
        rho_bar = float(np.clip(rho_bar, -1.0 / (n_live - 1), 1.0))
    target = rho_bar * np.outer(vol, vol)
    np.fill_diagonal(target, variances)
    return target


def shrink_covariance(
    sample_cov: ArrayLike,
    delta: float,
    target: Literal["scaled_identity", "constant_correlation"] | ArrayLike = "scaled_identity",
) -> ShrinkageResult:
    """3.5 Shrinkage: Sigma_shrunk = (1 - delta) Sigma + delta F for a given delta in [0, 1].

    ``target`` is a named target (see :func:`shrinkage_target`) or an explicit matrix F.
    """
    sigma = _to_symmetric(sample_cov, "sample_cov")
    try:
        intensity = float(delta)
    except (TypeError, ValueError) as exc:
        raise ValueError("delta must be a real number") from exc
    if not 0.0 <= intensity <= 1.0:
        raise ValueError("delta must lie in [0, 1]")
    if isinstance(target, str):
        f = shrinkage_target(sigma, target)  # type: ignore[arg-type]
        method = target
    else:
        f = _to_symmetric(target, "target")
        if f.shape != sigma.shape:
            raise ValueError(f"shape mismatch: target is {f.shape} but sample_cov is {sigma.shape}")
        method = "custom"
    shrunk = (1.0 - intensity) * sigma + intensity * f
    return ShrinkageResult(0.5 * (shrunk + shrunk.T), intensity, f, method)


def _sklearn_shrinkage(
    estimator_cls: type[LedoitWolf] | type[OAS], returns: ArrayLike, assume_centered: bool, method: str
) -> ShrinkageResult:
    data = _to_returns(returns)
    # Centre here (same arithmetic as sklearn, plus exact zeros for assets that never move)
    # and hand sklearn the centred panel.
    centred = data if assume_centered else _centre(data)[0]
    # The shrinkage intensity is scale-free but sklearn forms fourth moments, which overflow or
    # underflow for extreme units: fit on the unit-scaled panel and scale the covariance back.
    unit = float(np.max(np.abs(centred)))
    if unit == 0.0:
        unit = 1.0
    fitted = estimator_cls(assume_centered=True).fit(centred / unit)
    with np.errstate(over="ignore", invalid="ignore"):
        cov = np.asarray(fitted.covariance_, dtype=np.float64) * unit * unit
    if not np.all(np.isfinite(cov)):
        raise ValueError("the covariance overflows float64: rescale the returns to sensible units")
    cov = 0.5 * (cov + cov.T)
    delta = float(np.clip(fitted.shrinkage_, 0.0, 1.0))
    mu = float(np.trace(cov)) / cov.shape[0]
    return ShrinkageResult(cov, delta, np.eye(cov.shape[0]) * mu, method)


def ledoit_wolf_shrinkage(returns: ArrayLike, *, assume_centered: bool = False) -> ShrinkageResult:
    """3.5 Ledoit-Wolf shrinkage: Sigma_shrunk = (1 - delta) S + delta (tr(S)/N) I, delta optimal.

    Wraps ``sklearn.covariance.LedoitWolf``; ``S`` is the maximum-likelihood (``ddof=0``)
    sample covariance and ``delta`` minimises the expected Frobenius loss.
    """
    return _sklearn_shrinkage(LedoitWolf, returns, assume_centered, "ledoit_wolf")


def oas_shrinkage(returns: ArrayLike, *, assume_centered: bool = False) -> ShrinkageResult:
    """3.5 OAS shrinkage: Sigma_shrunk = (1 - delta) S + delta (tr(S)/N) I, delta from OAS.

    Wraps ``sklearn.covariance.OAS`` (Oracle Approximating Shrinkage), which typically
    shrinks harder than Ledoit-Wolf for small, near-Gaussian samples.
    """
    return _sklearn_shrinkage(OAS, returns, assume_centered, "oas")
