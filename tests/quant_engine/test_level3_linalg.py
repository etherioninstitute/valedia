"""Tests for valedia_trader.quant_engine.level3_linalg (formulas 3.1 to 3.5)."""

from __future__ import annotations

import dataclasses
import warnings

import numpy as np
import pytest
from sklearn.covariance import OAS, LedoitWolf
from sklearn.decomposition import PCA

from valedia_trader.quant_engine import level3_linalg as l3


def _random_cov(rng: np.random.Generator, n: int, *, scale: float = 1.0) -> np.ndarray:
    a = rng.normal(size=(n, n))
    return scale * (a @ a.T) / n


def _lambda_max(cov: np.ndarray) -> float:
    """Largest eigenvalue: 1e6 / lambda_max bounds the gain of a kappa = 1e6 ridge solve."""
    return float(np.linalg.eigvalsh(cov)[-1])


def _factor_returns(rng: np.random.Generator, t: int = 400, n: int = 8) -> np.ndarray:
    """Two strong common factors plus idiosyncratic noise."""
    factors = rng.normal(size=(t, 2)) * np.array([0.02, 0.01])
    loadings = rng.normal(1.0, 0.4, size=(n, 2))
    return 0.0005 + factors @ loadings.T + rng.normal(0.0, 0.004, size=(t, n))


# --------------------------------------------------------------------------- registry
def test_formulas_registry_is_complete_and_resolvable():
    assert set(l3.FORMULAS) == {"3.1", "3.2", "3.3", "3.4", "3.5"}
    for formula_id, names in l3.FORMULAS.items():
        assert names, formula_id
        for name in names:
            func = getattr(l3, name)
            assert callable(func)
            assert name in l3.__all__
            assert func.__doc__.strip().startswith(formula_id), (name, func.__doc__)


def test_all_names_exist():
    for name in l3.__all__:
        assert hasattr(l3, name), name


# --------------------------------------------------------------------------- 3.1
def test_covariance_matrix_matches_numpy():
    rng = np.random.default_rng(1)
    r = rng.normal(size=(250, 5)) * np.array([0.01, 0.02, 0.005, 0.03, 0.015])
    cov = l3.covariance_matrix(r)
    np.testing.assert_allclose(cov, np.cov(r, rowvar=False), rtol=1e-12, atol=1e-18)
    np.testing.assert_allclose(l3.covariance_matrix(r, ddof=0), np.cov(r, rowvar=False, ddof=0), rtol=1e-12)
    np.testing.assert_array_equal(cov, cov.T)
    assert l3.is_psd(cov)
    # entry (i, j) is Cov(r_i, r_j)
    assert cov[1, 3] == pytest.approx(
        np.mean((r[:, 1] - r[:, 1].mean()) * (r[:, 3] - r[:, 3].mean())) * 250 / 249
    )
    uncentred = l3.covariance_matrix(r, assume_centered=True, ddof=0)
    np.testing.assert_allclose(uncentred, r.T @ r / 250, rtol=1e-12)


def test_covariance_matrix_degenerate_inputs():
    rng = np.random.default_rng(2)
    r = rng.normal(size=(50, 3))
    r[:, 1] = 0.01  # an asset that never moves
    cov = l3.covariance_matrix(r)
    np.testing.assert_allclose(cov[1], 0.0, atol=1e-18)
    np.testing.assert_allclose(cov[:, 1], 0.0, atol=1e-18)
    assert l3.is_psd(cov) and l3.condition_number(cov) == np.inf
    single = l3.covariance_matrix(rng.normal(size=30))  # 1-D input is one asset
    assert single.shape == (1, 1) and single[0, 0] > 0
    # fewer observations than assets: still PSD, but rank deficient
    wide = l3.covariance_matrix(rng.normal(size=(4, 10)))
    assert l3.is_psd(wide) and np.linalg.matrix_rank(wide) == 3


@pytest.mark.parametrize(
    "returns, kwargs",
    [
        (np.ones((1, 3)), {}),
        (np.ones((2, 3)), {"ddof": 2}),
        (np.ones((2, 3, 4)), {}),
        ([[0.1, np.nan], [0.2, 0.3]], {}),
        ([], {}),
        ("abc", {}),
    ],
)
def test_covariance_matrix_validation(returns, kwargs):
    with pytest.raises(ValueError):
        l3.covariance_matrix(returns, **kwargs)


def test_is_psd():
    assert l3.is_psd(np.eye(3))
    assert l3.is_psd(np.zeros((2, 2)))
    assert l3.is_psd([[1.0, 1.0], [1.0, 1.0]])  # singular but PSD
    assert not l3.is_psd([[1.0, 2.0], [2.0, 1.0]])  # eigenvalues 3 and -1
    assert not l3.is_psd([[1.0, 0.5], [0.0, 1.0]])  # not symmetric
    assert not l3.is_psd([[1.0, 0.0], [0.0, -1e-6]])
    assert l3.is_psd([[1.0, 0.0], [0.0, -1e-6]], tol=1e-5)
    # rounding-level negativity does not flag a genuine covariance
    rng = np.random.default_rng(3)
    low_rank = rng.normal(size=(30, 3))
    assert l3.is_psd(low_rank @ low_rank.T)
    with pytest.raises(ValueError, match="square"):
        l3.is_psd(np.ones((2, 3)))
    with pytest.raises(ValueError, match="tol"):
        l3.is_psd(np.eye(2), tol=-1.0)


def test_nearest_psd_projects_matrix_with_negative_eigenvalue():
    bad = np.array([[1.0, 2.0], [2.0, 1.0]])  # eigenvalues 3 and -1
    assert np.linalg.eigvalsh(bad).min() == pytest.approx(-1.0)
    fixed = l3.nearest_psd(bad)
    assert np.linalg.eigvalsh(fixed).min() >= 0.0
    assert l3.is_psd(fixed)
    np.testing.assert_allclose(fixed, [[1.5, 1.5], [1.5, 1.5]], atol=1e-14)
    np.testing.assert_array_equal(fixed, fixed.T)


def test_nearest_psd_on_inconsistent_correlation_matrix():
    # pairwise-estimated correlations that no joint distribution can produce
    corr = np.array([[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]])
    assert np.linalg.eigvalsh(corr).min() < -0.5
    fixed = l3.nearest_psd(corr)
    assert np.linalg.eigvalsh(fixed).min() >= 0.0
    # eigenvalue clipping is the Frobenius-nearest PSD matrix: any other PSD matrix is farther
    rng = np.random.default_rng(4)
    best = np.linalg.norm(fixed - corr)
    for _ in range(50):
        other = fixed + 0.05 * _random_cov(rng, 3)
        assert np.linalg.norm(other - corr) >= best
    keep = l3.nearest_psd(corr, preserve_diagonal=True)
    np.testing.assert_array_equal(np.diag(keep), 1.0)  # still a correlation matrix
    assert np.linalg.eigvalsh(keep).min() >= 0.0
    assert np.all(np.abs(keep) <= 1.0 + 1e-12)
    # usable as a covariance again
    assert l3.portfolio_variance([1.0, -1.0, 1.0], keep) >= 0.0


def test_nearest_psd_random_matrices_always_confirm_psd():
    rng = np.random.default_rng(5)
    for _ in range(150):
        n = int(rng.integers(2, 25))
        a = rng.normal(size=(n, n)) * 10.0 ** int(rng.integers(-6, 6))
        sym = a + a.T
        fixed = l3.nearest_psd(sym)
        assert np.linalg.eigvalsh(fixed).min() >= 0.0
        values, vectors = np.linalg.eigh(sym)
        reference = (vectors * np.maximum(values, 0.0)) @ vectors.T
        np.testing.assert_allclose(fixed, reference, atol=1e-11 * np.abs(sym).max())
        floor = 1e-4 * np.abs(sym).max()
        assert np.linalg.eigvalsh(l3.nearest_psd(sym, min_eigenvalue=floor)).min() >= floor
        variances = sym.copy()
        np.fill_diagonal(variances, np.abs(np.diag(sym)))
        kept = l3.nearest_psd(variances, preserve_diagonal=True)
        np.testing.assert_array_equal(np.diag(kept), np.diag(variances))
        assert np.linalg.eigvalsh(kept).min() >= 0.0


def test_nearest_psd_symmetrises_first_and_leaves_psd_input_alone():
    rng = np.random.default_rng(6)
    cov = _random_cov(rng, 5)
    np.testing.assert_array_equal(l3.nearest_psd(cov), cov)  # already PSD: untouched
    skewed = cov.copy()
    skewed[0, 1] += 0.2  # asymmetric input
    np.testing.assert_allclose(l3.nearest_psd(skewed), l3.nearest_psd(0.5 * (skewed + skewed.T)))
    np.testing.assert_array_equal(l3.nearest_psd(skewed), l3.nearest_psd(skewed).T)
    floored = l3.nearest_psd(np.diag([2.0, 1e-12, 0.0]), min_eigenvalue=1e-6)
    np.testing.assert_allclose(np.linalg.eigvalsh(floored), [1e-6, 1e-6, 2.0], rtol=1e-9)
    # zero variance with preserve_diagonal: the asset's covariances are zeroed, not NaN
    weird = np.array([[0.0, 0.5], [0.5, 1.0]])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        kept = l3.nearest_psd(weird, preserve_diagonal=True)
    np.testing.assert_allclose(kept, [[0.0, 0.0], [0.0, 1.0]], atol=1e-15)


def test_nearest_psd_validation():
    with pytest.raises(ValueError, match="square"):
        l3.nearest_psd(np.ones((2, 3)))
    with pytest.raises(ValueError, match="min_eigenvalue"):
        l3.nearest_psd(np.eye(2), min_eigenvalue=-1.0)
    with pytest.raises(ValueError, match="non-negative diagonal"):
        l3.nearest_psd(np.array([[-1.0, 0.0], [0.0, 1.0]]), preserve_diagonal=True)
    with pytest.raises(ValueError, match="finite"):
        l3.nearest_psd(np.array([[1.0, np.inf], [np.inf, 1.0]]))


# --------------------------------------------------------------------------- 3.2
def test_two_assets_twenty_vol_half_correlation_gives_17_32_vol():
    vol, rho = 0.20, 0.5
    cov = np.array([[vol**2, rho * vol * vol], [rho * vol * vol, vol**2]])
    w = np.array([0.5, 0.5])
    assert l3.portfolio_variance(w, cov) == pytest.approx(0.03, rel=1e-12)
    assert l3.portfolio_volatility(w, cov) == pytest.approx(0.1732, abs=5e-5)
    assert l3.portfolio_volatility(w, cov) == pytest.approx(np.sqrt(0.03), rel=1e-12)
    assert isinstance(l3.portfolio_volatility(w, cov), float)
    # diversification: below the 20% of either asset; equal to it only at rho = 1
    perfect = np.full((2, 2), vol**2)
    assert l3.portfolio_volatility(w, perfect) == pytest.approx(0.20)


def test_portfolio_variance_is_vectorised_over_weight_vectors():
    rng = np.random.default_rng(7)
    cov = _random_cov(rng, 6, scale=0.04)
    weights = rng.normal(size=(1_000, 6))
    batch = l3.portfolio_variance(weights, cov)
    assert batch.shape == (1_000,)
    loop = np.array([w @ cov @ w for w in weights])
    np.testing.assert_allclose(batch, loop, rtol=1e-12)
    np.testing.assert_allclose(l3.portfolio_volatility(weights, cov), np.sqrt(loop), rtol=1e-12)
    grid = l3.portfolio_variance(weights.reshape(10, 100, 6), cov)
    assert grid.shape == (10, 100)
    np.testing.assert_allclose(grid.ravel(), loop, rtol=1e-12)
    # matches the variance of simulated portfolio returns
    draws = rng.multivariate_normal(np.zeros(6), cov, size=200_000)
    assert (draws @ weights[0]).var() == pytest.approx(batch[0], rel=0.02)


def test_portfolio_variance_degenerate_and_validation():
    cov = np.array([[0.04, 0.04], [0.04, 0.04]])
    assert l3.portfolio_variance([1.0, -1.0], cov) == 0.0  # perfect hedge, clipped at exactly 0
    assert l3.portfolio_volatility([0.0, 0.0], cov) == 0.0
    assert l3.portfolio_variance([2.0], [[0.25]]) == pytest.approx(1.0)
    with pytest.raises(ValueError, match="positive semi-definite"):
        l3.portfolio_variance([1.0, -1.0], [[1.0, 2.0], [2.0, 1.0]])
    with pytest.raises(ValueError, match="shape mismatch"):
        l3.portfolio_variance([1.0, 2.0, 3.0], cov)
    with pytest.raises(ValueError, match="symmetric"):
        l3.portfolio_variance([1.0, 1.0], [[1.0, 0.3], [0.1, 1.0]])
    with pytest.raises(ValueError, match="square"):
        l3.portfolio_variance([1.0, 1.0], np.ones((2, 3)))
    with pytest.raises(ValueError, match="at least 1-D"):
        l3.portfolio_variance(1.0, [[1.0]])
    with pytest.raises(ValueError, match="finite"):
        l3.portfolio_variance([1.0, np.nan], cov)


# --------------------------------------------------------------------------- 3.3
def test_eigen_decomposition_satisfies_definition():
    rng = np.random.default_rng(8)
    cov = _random_cov(rng, 7)
    eig = l3.eigen_decomposition(cov)
    assert np.all(np.diff(eig.eigenvalues) <= 0)  # descending
    for k in range(7):
        np.testing.assert_allclose(
            cov @ eig.eigenvectors[:, k], eig.eigenvalues[k] * eig.eigenvectors[:, k], atol=1e-12
        )
    np.testing.assert_allclose(eig.eigenvectors.T @ eig.eigenvectors, np.eye(7), atol=1e-12)
    np.testing.assert_allclose((eig.eigenvectors * eig.eigenvalues) @ eig.eigenvectors.T, cov, atol=1e-12)
    assert eig.explained_variance_ratio.sum() == pytest.approx(1.0)
    np.testing.assert_allclose(eig.explained_variance_ratio, eig.eigenvalues / np.trace(cov), rtol=1e-12)
    np.testing.assert_allclose(eig.eigenvalues, np.linalg.eigvalsh(cov)[::-1], rtol=1e-10, atol=1e-13)
    # deterministic orientation: largest-magnitude entry of each eigenvector is positive
    idx = np.argmax(np.abs(eig.eigenvectors), axis=0)
    assert np.all(eig.eigenvectors[idx, np.arange(7)] > 0)
    # the top eigenvalue is the variance of the top eigen-portfolio
    assert l3.portfolio_variance(eig.eigenvectors[:, 0], cov) == pytest.approx(eig.eigenvalues[0])


def test_eigen_decomposition_known_two_asset_case_and_degenerate():
    eig = l3.eigen_decomposition([[0.04, 0.02], [0.02, 0.04]])
    np.testing.assert_allclose(eig.eigenvalues, [0.06, 0.02], rtol=1e-12)
    np.testing.assert_allclose(eig.eigenvectors[:, 0], [np.sqrt(0.5), np.sqrt(0.5)], rtol=1e-12)
    np.testing.assert_allclose(eig.explained_variance_ratio, [0.75, 0.25], rtol=1e-12)
    zero = l3.eigen_decomposition(np.zeros((3, 3)))
    np.testing.assert_array_equal(zero.explained_variance_ratio, 0.0)  # explicit, not 0/0 = NaN
    with pytest.raises(ValueError, match="positive semi-definite"):
        l3.eigen_decomposition([[1.0, 2.0], [2.0, 1.0]])
    raw = l3.eigen_decomposition([[1.0, 2.0], [2.0, 1.0]], require_psd=False)
    np.testing.assert_allclose(raw.eigenvalues, [3.0, -1.0], rtol=1e-12)
    np.testing.assert_allclose(raw.explained_variance_ratio, [1.0, 0.0], atol=1e-12)
    with pytest.raises(ValueError, match="symmetric"):
        l3.eigen_decomposition([[1.0, 0.5], [0.0, 1.0]])


@pytest.mark.parametrize("k", [1, 2, 5])
def test_pca_matches_sklearn(k):
    rng = np.random.default_rng(9)
    r = _factor_returns(rng)
    mine = l3.pca(r, k)
    ref = PCA(n_components=k, svd_solver="full").fit(r)
    np.testing.assert_allclose(mine.eigenvalues, ref.explained_variance_, rtol=1e-10)
    np.testing.assert_allclose(mine.explained_variance_ratio, ref.explained_variance_ratio_, rtol=1e-10)
    np.testing.assert_allclose(mine.mean, ref.mean_, rtol=1e-12)
    scores_ref = ref.transform(r)
    for j in range(k):  # components are defined up to sign
        sign = np.sign(mine.components[j] @ ref.components_[j])
        np.testing.assert_allclose(mine.components[j], sign * ref.components_[j], atol=1e-9)
        np.testing.assert_allclose(mine.factor_returns[:, j], sign * scores_ref[:, j], atol=1e-9)
    assert mine.components.shape == (k, r.shape[1])
    assert mine.factor_returns.shape == (r.shape[0], k)
    assert mine.total_variance == pytest.approx(np.trace(np.cov(r, rowvar=False)), rel=1e-12)


def test_pca_factors_are_orthonormal_uncorrelated_and_capture_structure():
    rng = np.random.default_rng(10)
    r = _factor_returns(rng, t=1_000)
    fit = l3.pca(r)
    assert fit.components.shape == (8, 8)
    np.testing.assert_allclose(fit.components @ fit.components.T, np.eye(8), atol=1e-12)
    factor_cov = np.cov(fit.factor_returns, rowvar=False)
    np.testing.assert_allclose(factor_cov, np.diag(fit.eigenvalues), atol=1e-12)
    assert fit.explained_variance_ratio.sum() == pytest.approx(1.0)
    assert np.all(np.diff(fit.eigenvalues) <= 0)
    assert fit.explained_variance_ratio[:2].sum() > 0.85  # two planted factors dominate
    eig = l3.eigen_decomposition(l3.covariance_matrix(r))
    np.testing.assert_allclose(fit.eigenvalues, eig.eigenvalues, rtol=1e-10)
    np.testing.assert_allclose(fit.components, eig.eigenvectors.T, atol=1e-10)


def test_residualize_strips_top_components():
    rng = np.random.default_rng(11)
    r = _factor_returns(rng, t=600)
    k = 2
    res = l3.residualize_returns(r, k)
    ref = PCA(n_components=k, svd_solver="full").fit(r)
    np.testing.assert_allclose(res.residuals, r - ref.inverse_transform(ref.transform(r)), atol=1e-10)
    # residuals are orthogonal to every removed factor and add back to the demeaned returns
    np.testing.assert_allclose(res.residuals @ res.components.T, 0.0, atol=1e-12)
    np.testing.assert_allclose(res.factor_returns.T @ res.residuals, 0.0, atol=1e-12)
    np.testing.assert_allclose(res.residuals + res.systematic, r - r.mean(axis=0), atol=1e-14)
    np.testing.assert_allclose(res.residuals.mean(axis=0), 0.0, atol=1e-14)
    # the residual covariance has lost exactly the top-k eigenvalues
    full = l3.pca(r)
    resid_eigs = np.linalg.eigvalsh(l3.covariance_matrix(res.residuals))[::-1]
    np.testing.assert_allclose(resid_eigs[: 8 - k], full.eigenvalues[k:], rtol=1e-8)
    np.testing.assert_allclose(resid_eigs[8 - k :], 0.0, atol=1e-15)
    # cross-sectional correlation collapses once the common factors are gone
    raw_corr = np.corrcoef(r, rowvar=False)
    res_corr = np.corrcoef(res.residuals, rowvar=False)
    off = ~np.eye(8, dtype=bool)
    assert np.abs(raw_corr[off]).mean() > 0.7
    assert np.abs(res_corr[off]).mean() < 0.25


def test_residualize_edge_cases():
    rng = np.random.default_rng(12)
    r = _factor_returns(rng, t=100, n=4)
    none = l3.residualize_returns(r, 0)
    np.testing.assert_allclose(none.residuals, r - r.mean(axis=0))
    assert none.components.shape == (0, 4) and none.factor_returns.shape == (100, 0)
    everything = l3.residualize_returns(r, 4)
    np.testing.assert_allclose(everything.residuals, 0.0, atol=1e-14)
    kept_mean = l3.residualize_returns(r, 1, demean=False)
    np.testing.assert_array_equal(kept_mean.mean, 0.0)
    np.testing.assert_allclose(kept_mean.residuals + kept_mean.systematic, r, atol=1e-14)
    for bad in (-1, 5, 1.5, None, True):
        with pytest.raises(ValueError):
            l3.residualize_returns(r, bad)
    for bad in (0, 5, 2.0):
        with pytest.raises(ValueError):
            l3.pca(r, bad)
    with pytest.raises(ValueError, match="at least 2"):
        l3.pca(r[:1])


def test_pca_zero_variance_panel_is_explicit():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        flat = l3.pca(np.full((20, 3), 0.01))
    np.testing.assert_array_equal(flat.eigenvalues, 0.0)
    np.testing.assert_array_equal(flat.explained_variance_ratio, 0.0)
    assert flat.total_variance == 0.0
    assert np.all(np.isfinite(flat.components)) and np.all(np.isfinite(flat.factor_returns))


# --------------------------------------------------------------------------- 3.4
def test_near_collinear_assets_give_huge_kappa_and_trip_the_guard():
    rng = np.random.default_rng(13)
    base = rng.normal(0.0, 0.01, size=500)
    returns = np.column_stack([base, base + rng.normal(0.0, 1e-8, size=500), rng.normal(0.0, 0.01, size=500)])
    cov = l3.covariance_matrix(returns)
    kappa = l3.condition_number(cov)
    assert kappa > 1e10
    report = l3.inversion_guard(cov)
    assert report.safe_to_invert is False
    assert report.condition_number == kappa
    assert report.effective_rank == 2
    assert report.lambda_max / report.lambda_min == pytest.approx(kappa)
    assert kappa == pytest.approx(np.linalg.cond(cov), rel=1e-2)
    # well-separated assets are fine
    healthy = l3.inversion_guard(l3.covariance_matrix(rng.normal(0.0, 0.01, size=(500, 3))))
    assert healthy.safe_to_invert is True and healthy.condition_number < 2.0
    assert healthy.effective_rank == 3


def test_condition_number_definition_and_singular_cases():
    assert l3.condition_number(np.diag([4.0, 2.0, 0.5])) == pytest.approx(8.0)
    assert l3.condition_number(np.eye(4)) == pytest.approx(1.0)
    assert l3.condition_number(np.zeros((2, 2))) == np.inf
    assert l3.condition_number([[1.0, 1.0], [1.0, 1.0]]) > 1e15  # singular
    assert l3.condition_number([[1.0, 2.0], [2.0, 1.0]]) == np.inf  # not positive definite
    assert l3.condition_number(1e-12 * np.diag([4.0, 1.0])) == pytest.approx(4.0)  # scale invariant
    rng = np.random.default_rng(14)
    cov = _random_cov(rng, 6)
    assert l3.condition_number(cov) == pytest.approx(np.linalg.cond(cov), rel=1e-8)


def test_inversion_guard_threshold_parameter():
    cov = np.diag([1.0, 1e-4])
    assert l3.inversion_guard(cov, max_condition=1e5).safe_to_invert
    assert not l3.inversion_guard(cov, max_condition=1e3).safe_to_invert
    assert l3.inversion_guard(cov, max_condition=1e3).effective_rank == 1
    assert not l3.inversion_guard(np.zeros((2, 2))).safe_to_invert
    assert l3.inversion_guard(np.zeros((2, 2))).effective_rank == 0
    report = l3.inversion_guard(cov)
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.safe_to_invert = True  # type: ignore[misc]
    for bad in (1.0, 0.0, -5.0, np.inf, np.nan):
        with pytest.raises(ValueError, match="max_condition"):
            l3.inversion_guard(cov, max_condition=bad)
    with pytest.raises(ValueError, match="symmetric"):
        l3.inversion_guard([[1.0, 0.5], [0.0, 1.0]])


def test_stable_solve_well_conditioned_matches_numpy():
    rng = np.random.default_rng(15)
    cov = _random_cov(rng, 6) + 0.1 * np.eye(6)
    b = rng.normal(size=6)
    out = l3.stable_solve(cov, b)
    assert out.method == "cholesky" and out.regularization == 0.0 and out.effective_rank == 6
    np.testing.assert_allclose(out.solution, np.linalg.solve(cov, b), rtol=1e-10)
    many = rng.normal(size=(6, 3))
    np.testing.assert_allclose(l3.stable_solve(cov, many).solution, np.linalg.solve(cov, many), rtol=1e-10)


def test_stable_solve_falls_back_instead_of_exploding():
    rng = np.random.default_rng(16)
    base = rng.normal(0.0, 0.01, size=400)
    returns = np.column_stack([base, base + rng.normal(0.0, 1e-9, size=400), rng.normal(0.0, 0.01, size=400)])
    cov = l3.covariance_matrix(returns)
    mu = np.array([0.001, 0.0011, 0.0005])
    naive = np.linalg.solve(cov, mu)
    assert np.abs(naive).max() > 1e6  # the naive inverse bets the farm on a spurious spread

    pinv = l3.stable_solve(cov, mu)
    assert pinv.method == "pinv" and pinv.effective_rank == 2
    assert np.abs(pinv.solution).max() < 100.0
    np.testing.assert_allclose(pinv.solution, np.linalg.pinv(cov, rcond=1e-6, hermitian=True) @ mu, rtol=1e-6)
    # the two collinear assets are treated symmetrically
    assert pinv.solution[0] == pytest.approx(pinv.solution[1], rel=1e-3)

    ridge = l3.stable_solve(cov, mu, fallback="ridge")
    assert ridge.method == "ridge" and ridge.regularization > 0.0
    regularised = cov + ridge.regularization * np.eye(3)
    assert l3.condition_number(regularised) == pytest.approx(1e6, rel=1e-6)
    np.testing.assert_allclose(regularised @ ridge.solution, mu, rtol=1e-8)
    # noise amplification is capped at max_condition / lambda_max instead of 1 / lambda_min
    assert np.abs(ridge.solution).max() < 1e-6 * np.abs(naive).max()
    assert np.abs(ridge.solution).max() <= np.linalg.norm(mu) * 1e6 / _lambda_max(cov)

    fixed = l3.stable_solve(cov, mu, fallback="ridge", ridge=1e-5)
    assert fixed.regularization == 1e-5
    np.testing.assert_allclose(fixed.solution, np.linalg.solve(cov + 1e-5 * np.eye(3), mu), rtol=1e-8)
    assert np.abs(fixed.solution).max() < 100.0


def test_stable_solve_singular_and_zero_matrices():
    singular = np.array([[1.0, 1.0], [1.0, 1.0]])
    out = l3.stable_solve(singular, [2.0, 2.0])
    assert out.method == "pinv" and out.effective_rank == 1
    np.testing.assert_allclose(out.solution, [1.0, 1.0], rtol=1e-12)  # minimum-norm solution
    zero = l3.stable_solve(np.zeros((2, 2)), [1.0, 1.0])
    np.testing.assert_array_equal(zero.solution, [0.0, 0.0])
    assert zero.effective_rank == 0 and zero.condition_number == np.inf
    with pytest.raises(ValueError, match="no positive eigenvalue"):
        l3.stable_solve(np.zeros((2, 2)), [1.0, 1.0], fallback="ridge")
    ridge = l3.stable_solve(singular, [2.0, 2.0], fallback="ridge")
    assert np.all(np.isfinite(ridge.solution))
    np.testing.assert_allclose(ridge.solution, [1.0, 1.0], rtol=1e-5)


@pytest.mark.parametrize(
    "cov, b, kwargs",
    [
        (np.eye(2), [1.0, 2.0, 3.0], {}),
        (np.eye(2), np.ones((2, 2, 2)), {}),
        (np.eye(2), [1.0, 2.0], {"fallback": "lu"}),
        (np.eye(2), [1.0, 2.0], {"ridge": 0.0}),
        (np.eye(2), [1.0, 2.0], {"ridge": -1.0}),
        (np.eye(2), [1.0, 2.0], {"max_condition": 0.5}),
        (np.eye(2), [1.0, np.nan], {}),
        ([[1.0, 0.5], [0.0, 1.0]], [1.0, 2.0], {}),
    ],
)
def test_stable_solve_validation(cov, b, kwargs):
    with pytest.raises(ValueError):
        l3.stable_solve(cov, b, **kwargs)


# --------------------------------------------------------------------------- 3.5
def test_shrinkage_reduces_condition_number_when_obs_close_to_assets():
    rng = np.random.default_rng(17)
    n_assets, n_obs = 30, 36
    true_cov = _random_cov(rng, n_assets, scale=1e-4) + 1e-4 * np.eye(n_assets)
    returns = rng.multivariate_normal(np.zeros(n_assets), true_cov, size=n_obs)
    sample = l3.covariance_matrix(returns)
    kappa_sample = l3.condition_number(sample)
    lw = l3.ledoit_wolf_shrinkage(returns)
    oas = l3.oas_shrinkage(returns)
    for shrunk in (lw, oas):
        assert 0.0 < shrunk.delta < 1.0
        assert l3.condition_number(shrunk.covariance) < kappa_sample
        assert l3.is_psd(shrunk.covariance)
    assert l3.condition_number(lw.covariance) < 0.1 * kappa_sample
    manual = l3.shrink_covariance(sample, 0.3)
    assert l3.condition_number(manual.covariance) < kappa_sample
    # and the shrunk estimate is closer to the truth than the raw sample covariance
    assert np.linalg.norm(lw.covariance - true_cov) < np.linalg.norm(sample - true_cov)
    # fewer observations than assets: the sample is singular, the shrunk matrix is not
    short = returns[:20]
    assert l3.condition_number(l3.covariance_matrix(short)) > 1e12
    assert l3.inversion_guard(l3.ledoit_wolf_shrinkage(short).covariance).safe_to_invert


def test_ledoit_wolf_and_oas_match_sklearn_and_the_shrinkage_formula():
    rng = np.random.default_rng(18)
    returns = _factor_returns(rng, t=60, n=10)
    mle = l3.covariance_matrix(returns, ddof=0)
    for wrapper, estimator, name in (
        (l3.ledoit_wolf_shrinkage, LedoitWolf(), "ledoit_wolf"),
        (l3.oas_shrinkage, OAS(), "oas"),
    ):
        out = wrapper(returns)
        ref = estimator.fit(returns)
        np.testing.assert_allclose(out.covariance, ref.covariance_, rtol=1e-12, atol=1e-18)
        assert out.delta == pytest.approx(ref.shrinkage_)
        assert out.method == name
        # Sigma_shrunk = (1 - delta) S + delta F with F = (tr S / N) I
        np.testing.assert_allclose(out.target, np.eye(10) * np.trace(mle) / 10, rtol=1e-10)
        rebuilt = l3.shrink_covariance(mle, out.delta, "scaled_identity")
        np.testing.assert_allclose(rebuilt.covariance, out.covariance, rtol=1e-9, atol=1e-18)
        np.testing.assert_allclose((1 - out.delta) * mle + out.delta * out.target, out.covariance, rtol=1e-9)
        assert np.trace(out.covariance) == pytest.approx(np.trace(mle), rel=1e-10)  # trace preserved
    centred = l3.ledoit_wolf_shrinkage(returns, assume_centered=True)
    np.testing.assert_allclose(centred.covariance, LedoitWolf(assume_centered=True).fit(returns).covariance_)


def test_sklearn_shrinkage_validation_and_degenerate_panel():
    for wrapper in (l3.ledoit_wolf_shrinkage, l3.oas_shrinkage):
        with pytest.raises(ValueError, match="at least 2"):
            wrapper(np.ones((1, 3)))
        with pytest.raises(ValueError, match="finite"):
            wrapper([[0.1, np.nan], [0.2, 0.1], [0.3, 0.2]])
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            flat = wrapper(np.full((10, 3), 0.01))  # nothing moves: zero covariance, no NaN
        np.testing.assert_array_equal(flat.covariance, 0.0)
        assert 0.0 <= flat.delta <= 1.0


def test_shrinkage_intensity_is_scale_free_and_overflow_is_reported():
    rng = np.random.default_rng(180)
    returns = _factor_returns(rng, t=40, n=6)
    for wrapper in (l3.ledoit_wolf_shrinkage, l3.oas_shrinkage):
        base = wrapper(returns)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            for unit in (1e-100, 1e-6, 1e4, 1e120):
                scaled = wrapper(unit * returns)
                assert scaled.delta == pytest.approx(base.delta, rel=1e-9)
                np.testing.assert_allclose(scaled.covariance, unit**2 * base.covariance, rtol=1e-9)
            with pytest.raises(ValueError, match="overflow"):
                wrapper(1e160 * returns)  # variances of ~1e316 do not fit in a float
    with pytest.raises(ValueError, match="overflow"):
        l3.covariance_matrix(1e160 * returns)
    with pytest.raises(ValueError, match="overflow"):
        l3.pca(1e160 * returns, 2)


def test_manual_shrink_scaled_identity_and_bounds():
    rng = np.random.default_rng(19)
    sample = _random_cov(rng, 5, scale=0.04)
    mu = np.trace(sample) / 5
    half = l3.shrink_covariance(sample, 0.5)
    np.testing.assert_allclose(half.covariance, 0.5 * sample + 0.5 * mu * np.eye(5), rtol=1e-12)
    np.testing.assert_allclose(half.target, mu * np.eye(5), rtol=1e-12)
    assert half.delta == 0.5 and half.method == "scaled_identity"
    np.testing.assert_allclose(l3.shrink_covariance(sample, 0.0).covariance, sample, rtol=1e-15)
    np.testing.assert_allclose(l3.shrink_covariance(sample, 1.0).covariance, mu * np.eye(5), rtol=1e-12)
    # condition number falls monotonically as delta rises
    kappas = [
        l3.condition_number(l3.shrink_covariance(sample, d).covariance) for d in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]
    assert np.all(np.diff(kappas) < 0) and kappas[-1] == pytest.approx(1.0)
    # eigenvalues are pulled linearly toward the grand mean, eigenvectors unchanged
    np.testing.assert_allclose(
        np.linalg.eigvalsh(half.covariance), 0.5 * np.linalg.eigvalsh(sample) + 0.5 * mu, rtol=1e-10
    )


def test_manual_shrink_constant_correlation_target():
    vols = np.array([0.1, 0.2, 0.3])
    corr = np.array([[1.0, 0.8, 0.2], [0.8, 1.0, 0.5], [0.2, 0.5, 1.0]])
    sample = corr * np.outer(vols, vols)
    target = l3.shrinkage_target(sample, "constant_correlation")
    np.testing.assert_allclose(np.diag(target), vols**2, rtol=1e-12)  # variances kept
    np.testing.assert_allclose(
        target / np.outer(vols, vols), np.full((3, 3), 0.5) + 0.5 * np.eye(3), rtol=1e-12
    )
    full = l3.shrink_covariance(sample, 1.0, "constant_correlation")
    np.testing.assert_allclose(full.covariance, target, rtol=1e-12)
    mid = l3.shrink_covariance(sample, 0.4, "constant_correlation")
    np.testing.assert_allclose(mid.covariance, 0.6 * sample + 0.4 * target, rtol=1e-12)
    np.testing.assert_allclose(np.diag(mid.covariance), vols**2, rtol=1e-12)
    assert l3.is_psd(mid.covariance) and mid.method == "constant_correlation"
    # an asset with zero variance is excluded from the average correlation, not a NaN source
    padded = np.zeros((4, 4))
    padded[:3, :3] = sample
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        padded_target = l3.shrinkage_target(padded, "constant_correlation")
    np.testing.assert_allclose(padded_target[:3, :3], target, rtol=1e-12)
    np.testing.assert_array_equal(padded_target[3], 0.0)
    np.testing.assert_array_equal(l3.shrinkage_target([[0.04]], "constant_correlation"), [[0.04]])


def test_manual_shrink_custom_target_and_validation():
    sample = np.array([[0.04, 0.01], [0.01, 0.09]])
    custom = np.diag([0.04, 0.09])
    out = l3.shrink_covariance(sample, 0.25, custom)
    np.testing.assert_allclose(out.covariance, [[0.04, 0.0075], [0.0075, 0.09]], rtol=1e-12)
    assert out.method == "custom"
    for delta in (-0.1, 1.1, np.nan, "x"):
        with pytest.raises(ValueError, match="delta"):
            l3.shrink_covariance(sample, delta)
    with pytest.raises(ValueError, match="kind"):
        l3.shrink_covariance(sample, 0.5, "single_factor")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="shape mismatch"):
        l3.shrink_covariance(sample, 0.5, np.eye(3))
    with pytest.raises(ValueError, match="symmetric"):
        l3.shrink_covariance([[1.0, 0.5], [0.0, 1.0]], 0.5)
    with pytest.raises(ValueError, match="negative variance"):
        l3.shrinkage_target([[-1.0, 0.0], [0.0, 1.0]])


def test_inputs_are_not_mutated():
    rng = np.random.default_rng(20)
    returns = _factor_returns(rng, t=60, n=5)
    bad = np.array([[1.0, 2.0], [2.0, 1.0]])
    cov = l3.covariance_matrix(returns)
    snapshots = [a.copy() for a in (returns, bad, cov)]
    l3.nearest_psd(bad, preserve_diagonal=True)
    l3.nearest_psd(bad, min_eigenvalue=0.1)
    l3.pca(returns, 2)
    l3.residualize_returns(returns, 2)
    l3.ledoit_wolf_shrinkage(returns)
    l3.oas_shrinkage(returns)
    l3.shrink_covariance(cov, 0.5, "constant_correlation")
    l3.stable_solve(cov, np.ones(5), fallback="ridge", max_condition=2.0)
    l3.eigen_decomposition(cov)
    for original, snap in zip((returns, bad, cov), snapshots, strict=True):
        np.testing.assert_array_equal(original, snap)
