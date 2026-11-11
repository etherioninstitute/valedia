"""Tests for valedia_trader.quant_engine.level1_probability (formulas 1.1 to 1.5)."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest
from scipy import stats

from valedia_trader.quant_engine import level1_probability as l1


# --------------------------------------------------------------------------- registry
def test_formulas_registry_is_complete_and_resolvable():
    assert set(l1.FORMULAS) == {"1.1", "1.2", "1.3", "1.4", "1.5"}
    for formula_id, names in l1.FORMULAS.items():
        assert names, formula_id
        for name in names:
            func = getattr(l1, name)
            assert callable(func)
            assert name in l1.__all__
            assert func.__doc__.strip().startswith(formula_id), (name, func.__doc__)


def test_all_names_exist():
    for name in l1.__all__:
        assert hasattr(l1, name), name


# --------------------------------------------------------------------------- 1.1
def test_bayes_medical_test_gives_one_half():
    # sensitivity 0.99, specificity 0.99, prevalence 0.01 -> P(sick | positive) = 0.5
    posterior = l1.bayes_binary_posterior(0.01, 0.99, 1.0 - 0.99)
    assert posterior == pytest.approx(0.5, abs=1e-12)
    evidence = 0.99 * 0.01 + 0.01 * 0.99
    assert l1.bayes_posterior(0.99, 0.01, evidence) == pytest.approx(0.5, abs=1e-12)


def test_bayes_posterior_is_vectorised_and_returns_scalars_for_scalars():
    out = l1.bayes_posterior([0.9, 0.5], [0.2, 0.4], [0.3, 0.5])
    np.testing.assert_allclose(out, [0.6, 0.4])
    assert isinstance(l1.bayes_posterior(0.9, 0.2, 0.3), float)
    grid = l1.bayes_binary_posterior(np.array([0.1, 0.5, 0.9])[:, None], 0.8, np.array([0.1, 0.2]))
    assert grid.shape == (3, 2)
    assert np.all(np.diff(grid, axis=0) > 0)  # more prior -> more posterior


@pytest.mark.parametrize(
    "args",
    [
        (1.2, 0.5, 0.5),
        (0.5, -0.1, 0.5),
        (0.5, 0.5, 0.0),  # zero evidence
        (0.9, 0.9, 0.5),  # joint exceeds evidence
        (np.nan, 0.5, 0.5),
        ([0.1, 0.2], [0.1, 0.2, 0.3], 0.5),  # shape mismatch
    ],
)
def test_bayes_posterior_validation(args):
    with pytest.raises(ValueError):
        l1.bayes_posterior(*args)


def test_bayes_binary_validation():
    with pytest.raises(ValueError, match="zero probability"):
        l1.bayes_binary_posterior(0.5, 0.0, 0.0)
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        l1.bayes_binary_posterior(1.5, 0.5, 0.5)
    assert l1.bayes_binary_posterior(0.0, 0.9, 0.1) == 0.0
    assert l1.bayes_binary_posterior(1.0, 0.9, 0.1) == 1.0


def test_sequential_update_matches_direct_product():
    rng = np.random.default_rng(11)
    prior = np.array([0.2, 0.5, 0.3])
    lik = rng.uniform(0.05, 1.0, size=(25, 3))
    res = l1.sequential_bayes_update(prior, lik)
    assert res.posteriors.shape == (25, 3)
    np.testing.assert_allclose(res.posteriors.sum(axis=1), 1.0, atol=1e-12)
    direct = prior * np.cumprod(lik, axis=0)
    direct /= direct.sum(axis=1, keepdims=True)
    np.testing.assert_allclose(res.posteriors, direct, rtol=1e-10)
    np.testing.assert_allclose(res.final, direct[-1], rtol=1e-10)
    # incremental evidences multiply to the marginal likelihood of the whole stream
    marginal = float(np.sum(prior * np.prod(lik, axis=0)))
    assert np.sum(res.log_evidence) == pytest.approx(np.log(marginal), rel=1e-10)


def test_sequential_update_equals_repeated_single_step_bayes():
    prior = np.array([0.5, 0.5])
    lik = np.array([[0.8, 0.3], [0.6, 0.9], [0.1, 0.4]])
    res = l1.sequential_bayes_update(prior, lik)
    p = prior[0]
    for t in range(3):
        p = l1.bayes_binary_posterior(p, lik[t, 0], lik[t, 1])
        assert res.posteriors[t, 0] == pytest.approx(p, rel=1e-12)


def test_sequential_update_is_stable_where_naive_products_underflow():
    prior = np.array([0.5, 0.5])
    lik = np.tile(np.array([[1e-200, 2e-200]]), (10, 1))  # naive product underflows to 0/0
    res = l1.sequential_bayes_update(prior, lik)
    assert np.all(np.isfinite(res.posteriors))
    odds = res.posteriors[:, 1] / res.posteriors[:, 0]
    np.testing.assert_allclose(odds, 2.0 ** np.arange(1, 11), rtol=1e-9)
    log_res = l1.sequential_bayes_update(prior, np.log(lik), log_likelihoods=True)
    np.testing.assert_allclose(log_res.posteriors, res.posteriors, rtol=1e-10)


def test_sequential_update_normalises_prior_and_accepts_single_vector():
    res = l1.sequential_bayes_update([2.0, 2.0, 4.0], [0.5, 0.5, 0.5])
    assert res.posteriors.shape == (1, 3)
    np.testing.assert_allclose(res.final, [0.25, 0.25, 0.5])


def test_sequential_update_empty_stream_returns_prior():
    res = l1.sequential_bayes_update([1.0, 3.0], np.empty((0, 2)))
    assert res.posteriors.shape == (0, 2)
    np.testing.assert_allclose(res.final, [0.25, 0.75])
    assert res.log_evidence.size == 0
    res2 = l1.sequential_bayes_update([1.0, 3.0], [])
    np.testing.assert_allclose(res2.final, [0.25, 0.75])


def test_sequential_update_zero_likelihood_eliminates_hypothesis():
    res = l1.sequential_bayes_update([0.5, 0.5], [[0.0, 0.3], [0.9, 0.2]])
    np.testing.assert_allclose(res.posteriors, [[0.0, 1.0], [0.0, 1.0]])


def test_sequential_update_impossible_evidence_raises_or_skips():
    lik = [[0.9, 0.1], [0.0, 0.0], [0.2, 0.8]]
    with pytest.raises(ValueError, match="observation 1"):
        l1.sequential_bayes_update([0.5, 0.5], lik)
    res = l1.sequential_bayes_update([0.5, 0.5], lik, on_impossible="skip")
    np.testing.assert_array_equal(res.skipped, [False, True, False])
    np.testing.assert_allclose(res.posteriors[1], res.posteriors[0])
    expected_last = np.array([0.9 * 0.2, 0.1 * 0.8])
    np.testing.assert_allclose(res.final, expected_last / expected_last.sum())
    assert np.isneginf(res.log_evidence[1])
    # evidence that contradicts the only surviving hypothesis is also impossible
    with pytest.raises(ValueError, match="observation 1"):
        l1.sequential_bayes_update([0.5, 0.5], [[0.0, 1.0], [1.0, 0.0]])


@pytest.mark.parametrize(
    "prior, lik, kwargs",
    [
        ([0.5, -0.5], [[0.1, 0.2]], {}),
        ([0.0, 0.0], [[0.1, 0.2]], {}),
        ([[0.5, 0.5]], [[0.1, 0.2]], {}),
        ([0.5, 0.5], [[0.1, 0.2, 0.3]], {}),
        ([0.5, 0.5], [[0.1, -0.2]], {}),
        ([0.5, 0.5], [[0.1, np.nan]], {}),
        ([0.5, 0.5], [[0.1, np.inf]], {"log_likelihoods": True}),
        ([0.5, 0.5], [[0.1, 0.2]], {"on_impossible": "ignore"}),
    ],
)
def test_sequential_update_validation(prior, lik, kwargs):
    with pytest.raises(ValueError):
        l1.sequential_bayes_update(prior, lik, **kwargs)


def test_beta_binomial_update():
    post = l1.beta_binomial_update(1.0, 1.0, 7, 3)
    assert post.alpha == 8.0 and post.beta == 4.0
    assert post.mean == pytest.approx(8.0 / 12.0)
    assert post.variance == pytest.approx(stats.beta.var(8, 4))
    assert post.ci_low == pytest.approx(stats.beta.ppf(0.025, 8, 4))
    assert post.ci_high == pytest.approx(stats.beta.ppf(0.975, 8, 4))
    assert post.ci_low < post.mean < post.ci_high
    with pytest.raises(dataclasses.FrozenInstanceError):
        post.alpha = 1.0  # type: ignore[misc]


def test_beta_binomial_is_order_independent_and_vectorised():
    step = l1.beta_binomial_update(2.0, 5.0, 3, 1)
    twice = l1.beta_binomial_update(step.alpha, step.beta, 4, 6)
    once = l1.beta_binomial_update(2.0, 5.0, 7, 7)
    assert twice.mean == pytest.approx(once.mean)
    vec = l1.beta_binomial_update(1.0, 1.0, np.array([0, 5, 50]), np.array([0, 5, 50]))
    np.testing.assert_allclose(vec.mean, 0.5)
    assert np.all(np.diff(vec.variance) < 0)  # more evidence -> tighter posterior
    # no evidence leaves the prior untouched
    assert vec.alpha[0] == 1.0 and vec.beta[0] == 1.0


@pytest.mark.parametrize(
    "args, kwargs",
    [
        ((0.0, 1.0, 1, 1), {}),
        ((1.0, -1.0, 1, 1), {}),
        ((1.0, 1.0, -1, 1), {}),
        ((1.0, 1.0, 1, 1), {"credible_level": 1.0}),
        ((1.0, 1.0, np.nan, 1), {}),
    ],
)
def test_beta_binomial_validation(args, kwargs):
    with pytest.raises(ValueError):
        l1.beta_binomial_update(*args, **kwargs)


# --------------------------------------------------------------------------- 1.2
def test_expected_fixed_points_of_a_shuffle_is_one_analytically():
    for n in (1, 2, 5, 52, 1000):
        # card i is a fixed point with probability 1/n; linearity gives n * (1/n) = 1
        assert l1.expected_count(np.full(n, 1.0 / n)) == pytest.approx(1.0, abs=1e-12)
        assert l1.expected_pnl(np.ones(n), np.full(n, 1.0 / n)) == pytest.approx(1.0, abs=1e-12)


def test_expected_fixed_points_of_a_shuffle_is_one_by_simulation():
    rng = np.random.default_rng(20261110)
    n, trials = 52, 40_000
    perms = rng.permuted(np.tile(np.arange(n), (trials, 1)), axis=1)
    fixed = (perms == np.arange(n)).sum(axis=1)
    # Var(#fixed points) = 1, so the standard error is 1/sqrt(trials) = 0.005
    assert fixed.mean() == pytest.approx(1.0, abs=0.025)
    assert fixed.var() == pytest.approx(1.0, abs=0.05)


def test_expected_pnl_holds_regardless_of_correlation():
    rng = np.random.default_rng(5)
    mu = np.array([0.01, -0.02, 0.03])
    weights = np.array([1_000_000.0, 500_000.0, -250_000.0])
    for rho in (-0.4, 0.0, 0.95):
        cov = 0.04 * (np.full((3, 3), rho) + (1.0 - rho) * np.eye(3))
        draws = rng.multivariate_normal(mu, cov, size=200_000)
        simulated = (draws @ weights).mean()
        assert simulated == pytest.approx(l1.expected_pnl(weights, mu), abs=2_500.0)
    assert l1.expected_pnl(weights, mu) == pytest.approx(10_000.0 - 10_000.0 - 7_500.0)


def test_expected_pnl_vectorised_contributions_and_empty_book():
    weights = np.array([[1.0, 2.0, 3.0], [0.5, 0.5, 0.0]])
    mu = np.array([0.1, 0.2, 0.3])
    np.testing.assert_allclose(l1.expected_pnl(weights, mu), [1.4, 0.15])
    contrib = l1.expected_pnl_contributions(weights[0], mu)
    np.testing.assert_allclose(contrib, [0.1, 0.4, 0.9])
    assert contrib.sum() == pytest.approx(l1.expected_pnl(weights[0], mu))
    assert l1.expected_pnl([], []) == 0.0
    assert l1.expected_count([]) == 0.0
    with pytest.raises(ValueError, match="shape mismatch"):
        l1.expected_pnl([1.0, 2.0], [0.1, 0.2, 0.3])
    with pytest.raises(ValueError):
        l1.expected_pnl([1.0, np.inf], [0.1, 0.2])
    with pytest.raises(ValueError):
        l1.expected_count([0.5, 1.5])


def test_expected_value_of_a_die_and_validation():
    faces = np.arange(1, 7)
    assert l1.expected_value(faces, np.full(6, 1 / 6)) == pytest.approx(3.5)
    with pytest.raises(ValueError, match="sum to 1"):
        l1.expected_value(faces, np.full(6, 0.2))
    with pytest.raises(ValueError):
        l1.expected_value(faces, np.full(5, 0.2))


# --------------------------------------------------------------------------- 1.3
def test_variance_of_fair_die_is_35_over_12():
    faces = np.arange(1, 7)
    probs = np.full(6, 1.0 / 6.0)
    assert l1.variance(faces, probs) == pytest.approx(35.0 / 12.0, abs=1e-12)
    raw_moment_form = np.sum(probs * faces**2) - np.sum(probs * faces) ** 2
    assert l1.variance(faces, probs) == pytest.approx(raw_moment_form, abs=1e-12)
    assert l1.variance(faces) == pytest.approx(35.0 / 12.0, abs=1e-12)  # equally weighted sample


def test_variance_sample_matches_numpy_and_is_stable_for_large_means():
    rng = np.random.default_rng(3)
    x = rng.normal(size=(4, 500))
    np.testing.assert_allclose(l1.variance(x), x.var(axis=-1))
    np.testing.assert_allclose(l1.variance(x, ddof=1), x.var(axis=-1, ddof=1))
    shifted = 1e9 + np.array([0.0, 1.0, 2.0])
    assert l1.variance(shifted) == pytest.approx(2.0 / 3.0, rel=1e-9)  # raw moments would lose this
    assert l1.variance([4.2, 4.2, 4.2]) == 0.0


def test_variance_validation():
    with pytest.raises(ValueError):
        l1.variance([])
    with pytest.raises(ValueError, match="ddof"):
        l1.variance([1.0], ddof=1)
    with pytest.raises(ValueError, match="ddof"):
        l1.variance([1.0, 2.0], [0.5, 0.5], ddof=1)
    with pytest.raises(ValueError, match="sum to 1"):
        l1.variance([1.0, 2.0], [0.5, 0.6])


def test_variance_of_sum_two_asset_identity():
    rng = np.random.default_rng(8)
    xy = rng.multivariate_normal([0.0, 0.0], [[0.04, 0.012], [0.012, 0.09]], size=50_000)
    x, y = xy[:, 0], xy[:, 1]
    cov = np.cov(x, y, ddof=0)
    identity = l1.variance_of_sum(cov[0, 0], cov[1, 1], cov[0, 1])
    assert identity == pytest.approx(np.var(x + y), rel=1e-10)
    # exact population check and the weighted form
    assert l1.variance_of_sum(0.04, 0.09, 0.012) == pytest.approx(0.04 + 0.09 + 0.024)
    assert l1.variance_of_sum(0.04, 0.09, 0.012, a=2.0, b=-1.0) == pytest.approx(0.16 + 0.09 - 0.048)
    # perfect hedge and perfect correlation limits
    assert l1.variance_of_sum(0.04, 0.04, -0.04) == pytest.approx(0.0, abs=1e-15)
    assert l1.variance_of_sum(0.04, 0.04, 0.04) == pytest.approx(0.16)
    np.testing.assert_allclose(l1.variance_of_sum([1.0, 4.0], [1.0, 1.0], [0.0, 2.0]), [2.0, 9.0])


def test_variance_of_sum_validation():
    with pytest.raises(ValueError, match="correlation"):
        l1.variance_of_sum(0.04, 0.09, 0.07)
    with pytest.raises(ValueError, match=">= 0"):
        l1.variance_of_sum(-0.01, 0.09, 0.0)


def test_variance_decomposition_contributions_sum_to_total():
    rng = np.random.default_rng(21)
    a = rng.normal(size=(6, 6))
    cov = a @ a.T / 6.0
    w = rng.normal(size=6)
    dec = l1.variance_decomposition(w, cov)
    assert dec.total == pytest.approx(float(w @ cov @ w), rel=1e-12)
    assert dec.contributions.sum() == pytest.approx(dec.total, rel=1e-12)
    assert dec.fractions.sum() == pytest.approx(1.0, rel=1e-12)
    np.testing.assert_allclose(dec.marginal, 2.0 * cov @ w)
    # two-asset case reproduces Var(X) + Var(Y) + 2 Cov(X, Y)
    two = l1.variance_decomposition([1.0, 1.0], [[0.04, 0.012], [0.012, 0.09]])
    assert two.total == pytest.approx(l1.variance_of_sum(0.04, 0.09, 0.012))
    np.testing.assert_allclose(two.contributions, [0.052, 0.102])


def test_variance_decomposition_shows_diversification_and_degenerate_cases():
    n = 50
    uncorrelated = l1.variance_decomposition(np.full(n, 1.0 / n), 0.04 * np.eye(n))
    assert uncorrelated.total == pytest.approx(0.04 / n)
    zero = l1.variance_decomposition([0.0, 0.0], [[0.04, 0.0], [0.0, 0.09]])
    assert zero.total == 0.0
    np.testing.assert_array_equal(zero.fractions, [0.0, 0.0])
    with pytest.raises(ValueError, match="positive semi-definite"):
        l1.variance_decomposition([1.0, -1.0], [[1.0, 2.0], [2.0, 1.0]])
    with pytest.raises(ValueError, match="symmetric"):
        l1.variance_decomposition([1.0, 1.0], [[1.0, 0.5], [0.1, 1.0]])
    with pytest.raises(ValueError, match="weights"):
        l1.variance_decomposition([1.0, 1.0, 1.0], np.eye(2))
    with pytest.raises(ValueError, match="square"):
        l1.variance_decomposition([1.0, 1.0], np.ones((2, 3)))


# --------------------------------------------------------------------------- 1.4
def test_expected_rolls_until_first_six_is_six():
    p = 1.0 / 6.0
    expected = l1.geometric_expected_trials(p)
    assert expected == pytest.approx(6.0, abs=1e-12)
    # law of total expectation: E[N] = E[N | six] P(six) + E[N | no six] P(no six)
    assert l1.total_expectation([1.0, 1.0 + expected], [p, 1.0 - p]) == pytest.approx(expected, abs=1e-12)
    rng = np.random.default_rng(6)
    assert rng.geometric(p, size=200_000).mean() == pytest.approx(6.0, abs=0.06)


def test_geometric_expected_trials_vectorised_and_validation():
    np.testing.assert_allclose(l1.geometric_expected_trials([1.0, 0.5, 0.01]), [1.0, 2.0, 100.0])
    with pytest.raises(ValueError, match="infinite"):
        l1.geometric_expected_trials(0.0)
    with pytest.raises(ValueError):
        l1.geometric_expected_trials(1.1)


def test_total_expectation_over_regimes():
    cond = np.array([0.10, -0.25, 0.02])
    probs = np.array([0.6, 0.1, 0.3])
    assert l1.total_expectation(cond, probs) == pytest.approx(0.06 - 0.025 + 0.006)
    # several assets (rows) against one regime distribution
    many = np.array([[0.1, -0.2, 0.0], [0.0, 0.0, 0.5]])
    np.testing.assert_allclose(l1.total_expectation(many, probs), [0.04, 0.15])
    with pytest.raises(ValueError, match="sum to 1"):
        l1.total_expectation(cond, [0.5, 0.2, 0.2])
    with pytest.raises(ValueError, match="shape mismatch"):
        l1.total_expectation(cond, [0.5, 0.5])
    with pytest.raises(ValueError):
        l1.total_expectation(cond, [1.5, -0.25, -0.25])


def test_total_variance_matches_mixture_simulation():
    means = np.array([0.02, -0.05])
    variances = np.array([0.01, 0.09])
    probs = np.array([0.8, 0.2])
    tv = l1.total_variance(means, variances, probs)
    assert tv.mean == pytest.approx(0.006)
    assert tv.within == pytest.approx(0.8 * 0.01 + 0.2 * 0.09)
    assert tv.between == pytest.approx(0.8 * (0.02 - 0.006) ** 2 + 0.2 * (-0.05 - 0.006) ** 2)
    assert tv.total == pytest.approx(tv.within + tv.between)
    rng = np.random.default_rng(77)
    regime = rng.choice(2, size=400_000, p=probs)
    draws = rng.normal(means[regime], np.sqrt(variances[regime]))
    assert draws.var() == pytest.approx(tv.total, rel=0.02)
    assert draws.mean() == pytest.approx(tv.mean, abs=1e-3)


def test_total_variance_degenerate_and_validation():
    single = l1.total_variance([0.03], [0.04], [1.0])
    assert single.between == 0.0 and single.total == pytest.approx(0.04)
    with pytest.raises(ValueError, match=">= 0"):
        l1.total_variance([0.0, 0.0], [0.1, -0.1], [0.5, 0.5])
    with pytest.raises(ValueError, match="scalar"):
        l1.total_variance(0.0, 0.1, 1.0)


# --------------------------------------------------------------------------- 1.5
def test_coupon_collector_six_faces_is_14_7():
    assert l1.coupon_collector_expected_draws(6) == pytest.approx(14.7, abs=1e-12)
    assert l1.coupon_collector_expected_draws(1) == 1.0
    assert l1.coupon_collector_expected_draws(0) == 0.0
    # linearity: sum of geometric waiting times with success probabilities (n - i) / n
    stages = l1.geometric_expected_trials((6 - np.arange(6)) / 6.0)
    assert stages.sum() == pytest.approx(14.7, abs=1e-12)
    for bad in (-1, 2.5, True):
        with pytest.raises(ValueError):
            l1.coupon_collector_expected_draws(bad)


def test_coupon_collector_simulation():
    rng = np.random.default_rng(99)
    trials = 20_000
    rolls = rng.integers(0, 6, size=(trials, 120))
    first_seen = np.stack([(rolls == face).argmax(axis=1) for face in range(6)], axis=1)
    assert (rolls == 0).any(axis=1).all()
    draws_needed = first_seen.max(axis=1) + 1
    assert draws_needed.mean() == pytest.approx(14.7, abs=0.15)


def test_normal_mass_within_one_two_three_sigma():
    mass = l1.normal_mass_within([1.0, 2.0, 3.0])
    np.testing.assert_allclose(mass, [0.6827, 0.9545, 0.9973], atol=5e-5)
    np.testing.assert_allclose(mass, l1.normal_cdf([1, 2, 3]) - l1.normal_cdf([-1, -2, -3]), atol=1e-14)
    assert l1.normal_mass_within(0.0) == 0.0
    with pytest.raises(ValueError):
        l1.normal_mass_within(-1.0)


def test_normal_pdf_cdf_match_scipy_and_validate():
    x = np.linspace(-4, 4, 17)
    np.testing.assert_allclose(l1.normal_pdf(x, 0.5, 2.0), stats.norm.pdf(x, 0.5, 2.0))
    np.testing.assert_allclose(l1.normal_cdf(x, 0.5, 2.0), stats.norm.cdf(x, 0.5, 2.0))
    assert l1.normal_cdf(0.0) == pytest.approx(0.5)
    assert l1.normal_pdf(0.0) == pytest.approx(1.0 / np.sqrt(2.0 * np.pi))
    for bad_std in (0.0, -1.0):
        with pytest.raises(ValueError, match="> 0"):
            l1.normal_pdf(0.0, 0.0, bad_std)
        with pytest.raises(ValueError, match="> 0"):
            l1.normal_cdf(0.0, 0.0, bad_std)


def test_binomial_pmf_cdf():
    k = np.arange(0, 11)
    pmf = l1.binomial_pmf(k, 10, 0.3)
    np.testing.assert_allclose(pmf, stats.binom.pmf(k, 10, 0.3))
    assert pmf.sum() == pytest.approx(1.0)
    np.testing.assert_allclose(l1.binomial_cdf(k, 10, 0.3), np.cumsum(pmf), atol=1e-12)
    assert l1.binomial_pmf(2, 4, 0.5) == pytest.approx(6.0 / 16.0)
    assert l1.binomial_pmf(-1, 4, 0.5) == 0.0 and l1.binomial_pmf(5, 4, 0.5) == 0.0
    assert l1.binomial_pmf(0, 0, 0.5) == 1.0
    assert l1.expected_value(k, pmf) == pytest.approx(3.0)  # n p
    assert l1.variance(k, pmf) == pytest.approx(2.1)  # n p (1 - p)
    for args in ((1, -1, 0.5), (1, 2.5, 0.5), (1, 2, 1.5), (0.5, 2, 0.5)):
        with pytest.raises(ValueError):
            l1.binomial_pmf(*args)


def test_poisson_pmf_matches_formula_and_cdf():
    lam = 3.2
    k = np.arange(0, 12)
    from math import exp, factorial

    explicit = np.array([lam**i * exp(-lam) / factorial(i) for i in k])
    np.testing.assert_allclose(l1.poisson_pmf(k, lam), explicit, rtol=1e-12)
    np.testing.assert_allclose(l1.poisson_cdf(k, lam), np.cumsum(explicit), rtol=1e-12)
    assert l1.poisson_pmf(0, 0.0) == 1.0 and l1.poisson_pmf(3, 0.0) == 0.0
    assert l1.poisson_cdf(-1, lam) == 0.0
    with pytest.raises(ValueError, match=">= 0"):
        l1.poisson_pmf(1, -0.5)
    with pytest.raises(ValueError, match="integer"):
        l1.poisson_pmf(1.5, 1.0)


def test_poisson_mle_recovers_intensity():
    rng = np.random.default_rng(42)
    counts = rng.poisson(4.5, size=20_000)
    fit = l1.poisson_mle(counts)
    assert fit.rate == pytest.approx(counts.mean())
    assert fit.rate == pytest.approx(4.5, abs=4 * fit.std_err)
    assert fit.std_err == pytest.approx(np.sqrt(counts.mean() / counts.size))
    assert fit.n_obs == 20_000
    # exposure-weighted: windows of different length
    exposure = rng.uniform(0.5, 3.0, size=5_000)
    counts2 = rng.poisson(2.0 * exposure)
    fit2 = l1.poisson_mle(counts2, exposure)
    assert fit2.rate == pytest.approx(counts2.sum() / exposure.sum())
    assert fit2.rate == pytest.approx(2.0, abs=4 * fit2.std_err)


def test_poisson_mle_degenerate_and_validation():
    zero = l1.poisson_mle([0, 0, 0])
    assert zero.rate == 0.0 and zero.std_err == 0.0
    for bad in ([], [1, -1], [1.5, 2.0], [1, np.nan]):
        with pytest.raises(ValueError):
            l1.poisson_mle(bad)
    with pytest.raises(ValueError, match="exposure"):
        l1.poisson_mle([1, 2], [1.0, 0.0])
    with pytest.raises(ValueError, match="shape mismatch"):
        l1.poisson_mle([1, 2], [1.0, 1.0, 1.0])


def test_prob_at_least_one_arrival():
    assert l1.prob_at_least_one_arrival(2.0, 0.5) == pytest.approx(1.0 - np.exp(-1.0))
    assert l1.prob_at_least_one_arrival(2.0, 0.5) == pytest.approx(1.0 - l1.poisson_pmf(0, 1.0))
    assert l1.prob_at_least_one_arrival(0.0, 10.0) == 0.0
    assert l1.prob_at_least_one_arrival(5.0, 0.0) == 0.0
    # accurate for tiny intensities where 1 - exp(-x) would round to 0
    assert l1.prob_at_least_one_arrival(1e-18, 1.0) == pytest.approx(1e-18, rel=1e-12)
    np.testing.assert_allclose(
        l1.prob_at_least_one_arrival([0.1, 1.0], [[1.0], [2.0]]),
        1.0 - np.exp(-np.array([[0.1, 1.0], [0.2, 2.0]])),
    )
    with pytest.raises(ValueError):
        l1.prob_at_least_one_arrival(-1.0, 1.0)
    with pytest.raises(ValueError):
        l1.prob_at_least_one_arrival(1.0, -1.0)


def test_fit_student_t_recovers_parameters_and_flags_fat_tails():
    rng = np.random.default_rng(1)
    x = stats.t.rvs(4, loc=0.001, scale=0.01, size=6_000, random_state=rng)
    fit = l1.fit_student_t(x)
    assert fit.converged and not fit.df_at_bound
    assert fit.df == pytest.approx(4.0, abs=0.6)
    assert fit.loc == pytest.approx(0.001, abs=6e-4)
    assert fit.scale == pytest.approx(0.01, rel=0.06)
    # cross-check against scipy's generic MLE: same optimum, likelihood at least as good
    df_ref, loc_ref, scale_ref = stats.t.fit(x)
    ll_ref = float(stats.t.logpdf(x, df_ref, loc_ref, scale_ref).sum())
    assert fit.log_likelihood >= ll_ref - 1e-4
    assert fit.log_likelihood == pytest.approx(float(stats.t.logpdf(x, fit.df, fit.loc, fit.scale).sum()))
    assert fit.df == pytest.approx(df_ref, rel=5e-3)
    assert fit.scale == pytest.approx(scale_ref, rel=5e-3)
    # tails are heavier than the normal benchmark
    assert fit.tail_prob_normal == pytest.approx(2.0 * stats.norm.sf(3.0))
    assert fit.tail_ratio > 2.0
    empirical = float(np.mean(np.abs(x - fit.loc) > 3.0 * fit.sample_std))
    assert fit.tail_prob_t == pytest.approx(empirical, abs=0.004)


def test_fit_student_t_on_normal_data_reports_near_normal_tails():
    rng = np.random.default_rng(2)
    fit = l1.fit_student_t(rng.normal(0.0, 0.02, size=5_000))
    assert fit.df > 25.0
    assert fit.tail_ratio == pytest.approx(1.0, abs=0.35)
    assert fit.scale == pytest.approx(0.02, rel=0.08)


def test_fit_student_t_is_scale_and_shift_equivariant():
    rng = np.random.default_rng(3)
    x = stats.t.rvs(5, size=1_500, random_state=rng)
    base = l1.fit_student_t(x)
    moved = l1.fit_student_t(1e-4 * x + 7.0)
    assert moved.df == pytest.approx(base.df, rel=1e-3)
    assert moved.scale == pytest.approx(1e-4 * base.scale, rel=1e-3)
    assert moved.loc == pytest.approx(7.0 + 1e-4 * base.loc, abs=1e-7)
    assert moved.tail_ratio == pytest.approx(base.tail_ratio, rel=1e-3)


def test_fit_student_t_validation_and_degenerate_inputs():
    with pytest.raises(ValueError, match="zero variance"):
        l1.fit_student_t(np.full(20, 0.01))
    with pytest.raises(ValueError, match="at least 4"):
        l1.fit_student_t([0.1, 0.2, 0.3])
    with pytest.raises(ValueError, match="1-D"):
        l1.fit_student_t(np.zeros((4, 4)))
    with pytest.raises(ValueError, match="finite"):
        l1.fit_student_t([0.1, np.nan, 0.2, 0.3, 0.4])
    with pytest.raises(ValueError, match="degenerate"):
        l1.fit_student_t([1.0] * 7 + [2.0])  # likelihood spike on repeated values
    rng = np.random.default_rng(4)
    with pytest.raises(ValueError, match="tail_sigmas"):
        l1.fit_student_t(rng.normal(size=50), tail_sigmas=0.0)
    with pytest.raises(ValueError, match="df_bounds"):
        l1.fit_student_t(rng.normal(size=50), df_bounds=(5.0, 2.0))


def test_levy_stable_matches_closed_form_special_cases():
    x = np.linspace(-3.0, 3.0, 9)
    # alpha = 2 is Gaussian with standard deviation sqrt(2) * scale
    np.testing.assert_allclose(
        l1.levy_stable_pdf(x, 2.0, 0.0, loc=0.5, scale=0.7),
        stats.norm.pdf(x, loc=0.5, scale=0.7 * np.sqrt(2.0)),
        rtol=1e-8,
    )
    np.testing.assert_allclose(
        l1.levy_stable_cdf(x, 2.0, 0.0, loc=0.5, scale=0.7),
        stats.norm.cdf(x, loc=0.5, scale=0.7 * np.sqrt(2.0)),
        rtol=1e-8,
    )
    # alpha = 1, beta = 0 is Cauchy
    np.testing.assert_allclose(l1.levy_stable_pdf(x, 1.0, 0.0), stats.cauchy.pdf(x), rtol=1e-8)
    np.testing.assert_allclose(l1.levy_stable_cdf(x, 1.0, 0.0), stats.cauchy.cdf(x), rtol=1e-8)
    # alpha = 1/2, beta = 1 is the Levy distribution
    pos = np.array([0.25, 0.5, 1.0, 4.0])
    np.testing.assert_allclose(l1.levy_stable_pdf(pos, 0.5, 1.0), stats.levy.pdf(pos), rtol=1e-6)


def test_levy_stable_general_alpha_has_fat_tails_and_valid_cdf():
    x = np.array([-6.0, -2.0, 0.0, 2.0, 6.0])
    cdf = l1.levy_stable_cdf(x, 1.7, 0.0)
    assert np.all(np.diff(cdf) > 0) and np.all((cdf >= 0) & (cdf <= 1))
    assert cdf[2] == pytest.approx(0.5, abs=1e-8)
    np.testing.assert_allclose(cdf + cdf[::-1], 1.0, atol=1e-8)  # symmetric for beta = 0
    pdf = l1.levy_stable_pdf(x, 1.7, 0.0)
    np.testing.assert_allclose(pdf, pdf[::-1], rtol=1e-8)
    # power-law tail dominates the Gaussian (alpha = 2) tail with the same scale
    assert l1.levy_stable_cdf(-6.0, 1.7, 0.0) > 10.0 * l1.levy_stable_cdf(-6.0, 2.0, 0.0)
    assert isinstance(l1.levy_stable_pdf(0.0, 1.7), float)


def test_levy_stable_rvs_reproducible_and_distributed_correctly():
    a = l1.levy_stable_rvs(1.7, 0.2, loc=0.1, scale=0.5, size=2_000, rng=np.random.default_rng(7))
    b = l1.levy_stable_rvs(1.7, 0.2, loc=0.1, scale=0.5, size=2_000, rng=np.random.default_rng(7))
    np.testing.assert_array_equal(a, b)
    assert a.shape == (2_000,) and np.all(np.isfinite(a))
    assert l1.levy_stable_rvs(1.5, size=(3, 4), rng=np.random.default_rng(0)).shape == (3, 4)
    assert l1.levy_stable_rvs(1.5, size=0, rng=np.random.default_rng(0)).shape == (0,)
    # alpha = 2 draws are Gaussian with variance 2 * scale^2
    g = l1.levy_stable_rvs(2.0, 0.0, scale=1.0, size=40_000, rng=np.random.default_rng(8))
    assert g.var() == pytest.approx(2.0, rel=0.05)
    # empirical cdf agrees with the analytic cdf (Kolmogorov-Smirnov distance)
    grid = np.array([-2.0, -0.5, 0.1, 0.7, 2.5])
    analytic = l1.levy_stable_cdf(grid, 1.7, 0.2, loc=0.1, scale=0.5)
    empirical = (a[:, None] <= grid).mean(axis=0)
    np.testing.assert_allclose(empirical, analytic, atol=0.04)


@pytest.mark.parametrize(
    "alpha, beta, scale",
    [
        (0.0, 0.0, 1.0),
        (2.1, 0.0, 1.0),
        (1.5, 1.5, 1.0),
        (1.5, 0.0, 0.0),
        (1.5, 0.0, -1.0),
        (np.nan, 0.0, 1.0),
    ],
)
def test_levy_stable_validation(alpha, beta, scale):
    with pytest.raises(ValueError):
        l1.levy_stable_pdf(0.0, alpha, beta, scale=scale)
    with pytest.raises(ValueError):
        l1.levy_stable_cdf(0.0, alpha, beta, scale=scale)
    with pytest.raises(ValueError):
        l1.levy_stable_rvs(alpha, beta, scale=scale, size=3, rng=np.random.default_rng(0))


def test_levy_stable_rvs_requires_explicit_generator():
    with pytest.raises(ValueError, match="Generator"):
        l1.levy_stable_rvs(1.5, size=3, rng=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="size"):
        l1.levy_stable_rvs(1.5, size=-1, rng=np.random.default_rng(0))
    with pytest.raises(ValueError):
        l1.levy_stable_pdf([0.0, np.inf], 1.5)


def test_float32_probabilities_are_accepted_and_renormalised():
    # regime probabilities coming out of a float32 model sum to 1 only to ~1e-7
    probs32 = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
    probs32 = probs32 / probs32.sum(dtype=np.float32)
    assert abs(float(probs32.astype(np.float64).sum()) - 1.0) < 1e-6
    means = np.array([0.01, -0.02, 0.03, 0.0])
    exact = np.asarray(probs32, dtype=np.float64)
    exact = exact / exact.sum()
    assert l1.total_expectation(means, probs32) == pytest.approx(float(means @ exact), rel=1e-14)
    assert l1.total_expectation(np.ones(4), probs32) == pytest.approx(1.0, abs=1e-15)  # exactly normalised
    assert l1.total_variance(means, np.zeros(4), probs32).within == 0.0
    # a float32 round trip of P(B|A) P(A) = P(B) must not be called incoherent
    lik, prior = np.float32(0.9), np.float32(0.5)
    assert l1.bayes_posterior(lik, prior, np.float32(0.45)) == pytest.approx(1.0, abs=1e-6)
    assert l1.variance_of_sum(np.float32(0.04), np.float32(0.09), np.float32(0.06)) == pytest.approx(
        0.25, rel=1e-6
    )
    # genuinely wrong inputs are still rejected
    with pytest.raises(ValueError, match="sum to 1"):
        l1.total_expectation(means, [0.25, 0.25, 0.25, 0.2501])
    with pytest.raises(ValueError, match="incoherent"):
        l1.bayes_posterior(0.9, 0.5, 0.4499)


def test_inputs_are_not_mutated():
    prior = np.array([2.0, 2.0])
    lik = np.array([[0.5, 0.25]])
    weights = np.array([1.0, 2.0])
    cov = np.array([[1.0, 0.2], [0.2, 2.0]])
    snapshots = [a.copy() for a in (prior, lik, weights, cov)]
    l1.sequential_bayes_update(prior, lik)
    l1.variance_decomposition(weights, cov)
    for original, snap in zip((prior, lik, weights, cov), snapshots, strict=True):
        np.testing.assert_array_equal(original, snap)
