"""Tests for the Kalman hedge-ratio filter (T38-2 and its four addenda). Every test is seeded; each
tolerance carries its reason. Numbers quoted as "measured" come from the builder's run of this file."""
import inspect

import numpy as np
import pytest

from valedia_trader.quant_engine import kalman as K

FIELDS = ("beta", "alpha", "forecast_error", "forecast_scale", "obs_var", "z")
PV = 1e8


def logpx(rng, n, step=0.01, level=4.6):
    """A log-price-like series: level near 4.6 (a price near 100), random-walk steps."""
    return level + np.cumsum(rng.normal(0.0, step, n))


def pair(rng, n, beta=1.3, alpha=0.5, noise=0.02, step=0.01):
    x = logpx(rng, n, step)
    return beta * x + alpha + rng.normal(0.0, noise, n), x


def run(y, x, **kw):
    return K._filter(y, x, **kw)


# ------------------------------------------------------------------ 1. delta = 0 is least squares
def _prefix_errors(y, x, res, intercept):
    """Per prefix of t + 1 bars (index t from 0), the error relative to the largest coefficient of the
    reference: (a) lstsq on the ridge problem, (b) plain lstsq. NaN before a prefix of 30 bars."""
    lam = 1.0 / PV
    ridge, plain = np.full(y.size, np.nan), np.full(y.size, np.nan)
    for t in range(29, y.size):
        X = np.c_[x[: t + 1], np.ones(t + 1)] if intercept else x[: t + 1, None]
        p = X.shape[1]
        got = np.array([res.beta[t], res.alpha[t]][:p])
        aug = np.linalg.lstsq(np.r_[X, np.sqrt(lam) * np.eye(p)], np.r_[y[: t + 1], np.zeros(p)], rcond=None)[0]
        ls = np.linalg.lstsq(X, y[: t + 1], rcond=None)[0]
        ridge[t] = np.abs(got - aug).max() / np.abs(aug).max()
        plain[t] = np.abs(got - ls).max() / np.abs(ls).max()
    return ridge, plain


@pytest.mark.parametrize("intercept", [True, False])
def test_1_delta_zero_is_least_squares_on_every_prefix(intercept):
    # (a) the filter at delta 0 solves least squares with a ridge of 1/prior_var exactly: against lstsq on
    #     the system augmented with the prior rows, every prefix from bar 30, 5e-5 of the largest
    #     coefficient (measured 1.5e-5 with the covariance carried as a matrix, 3.0e-12 as a square-root
    #     factor: what was left was rounding through the 1e8 prior).
    # (b) against plain lstsq, every prefix from bar 60, 1e-4 (measured 2.6e-5): the ridge itself moves the
    #     estimates where X'X is nearly singular, as on short prefixes of log prices (1.2e-4 at bar 30).
    rng = np.random.default_rng(38)
    worst_a = worst_b = 0.0
    for _ in range(20):
        y, x = pair(rng, 300, beta=rng.uniform(0.5, 2.0), alpha=rng.uniform(1.0, 3.0) if intercept else 0.0)
        res = K.kalman_regression(y, x, delta=0.0, intercept=intercept)
        ridge, plain = _prefix_errors(y, x, res, intercept)
        worst_a, worst_b = max(worst_a, ridge[29:].max()), max(worst_b, plain[59:].max())
    print(f"\n[T1 intercept={intercept}] worst vs ridge lstsq from bar 30: {worst_a:.2e}; "
          f"vs plain lstsq from bar 60: {worst_b:.2e}")
    assert worst_a < 5e-5
    assert worst_b < 1e-4


@pytest.mark.parametrize("intercept,p", [(True, 2), (False, 1)])
def test_1b_delta_zero_variance_is_the_least_squares_residual_variance(intercept, p):
    # Not counting the first p bars buys the identity: at delta 0, R at the last bar (from the bars before
    # it) equals RSS / (n - p) of least squares on those bars. The identity is exact only for a truly
    # diffuse prior: with prior_var 1e8, when the first two log prices nearly coincide, bar 1 does not
    # pin the second direction, carries real noise and is not counted, while a later near-zero e^2/S* is.
    # Measured with the intercept (agreeing with a 50-digit copy to 1e-11): worst -4.73e-3 over 200 seeds,
    # -3.43e-4 over seeds 0-19; without the intercept 9.4e-10. Limit 1.5e-2, about three times 4.73e-3.
    worst = 0.0
    for seed in range(20):
        rng = np.random.default_rng(seed)
        y, x = pair(rng, 500, alpha=0.5 if intercept else 0.0)
        res = K.kalman_regression(y, x, delta=0.0, intercept=intercept)
        n = y.size - 1
        X = np.c_[x[:n], np.ones(n)] if intercept else x[:n, None]
        resid = y[:n] - X @ np.linalg.lstsq(X, y[:n], rcond=None)[0]
        rel = res.obs_var[-1] / (resid @ resid / (n - p)) - 1.0
        worst = max(worst, abs(rel))
        assert abs(rel) < 1.5e-2, (seed, rel)
    print(f"\n[T1b intercept={intercept}] worst |R_last / (RSS/(n-p)) - 1| over 20 seeds = {worst:.2e}")


@pytest.mark.parametrize("intercept,p", [(True, 2), (False, 1)])
def test_1c_the_first_p_bars_are_not_counted(intercept, p):
    # At delta 0 the count n rises by exactly 1 per counted bar: after bar t it is (bars with data up to
    # t) - p, never below 0, and R is NaN on every bar before which nothing has been counted.
    rng = np.random.default_rng(11)
    y, x = pair(rng, 120)
    y[[0, 5, 6]] = np.nan
    res, tr = run(y, x, delta=0.0, intercept=intercept)
    seen = np.cumsum(~np.isnan(y))
    np.testing.assert_array_equal(tr.count, np.maximum(seen - p, 0).astype(float))
    before = np.r_[0.0, tr.count[:-1]]
    has = ~np.isnan(y)
    assert np.isnan(res.obs_var[has & (before == 0)]).all()
    assert np.isfinite(res.obs_var[has & (before > 0)]).all()


# ------------------------------------------------------------------ 2. following a step
def test_2_slope_step_is_followed_with_memory_and_not_without():
    # Independent N(0, 1) x (addendum 2): the slope is identified bar by bar. The slope steps 1 -> 2 at
    # bar 1000; with delta 0.01 every bar from 1000 + 3/delta on has |beta - 2| < 0.1; with delta 0
    # (recursive least squares, no memory) every one of those bars is off by more than 0.1.
    worst_mem, best_rls = 0.0, np.inf
    for seed in range(5):
        rng = np.random.default_rng(200 + seed)
        n, step_at, delta = 2000, 1000, 0.01
        x = rng.normal(0.0, 1.0, n)
        b_true = np.where(np.arange(n) < step_at, 1.0, 2.0)
        y = b_true * x + 0.5 + rng.normal(0.0, 0.1, n)
        after = slice(step_at + int(3 / delta), n)
        mem = np.abs(K.kalman_regression(y, x, delta=delta).beta - b_true)[after].max()
        rls = np.abs(K.kalman_regression(y, x, delta=0.0).beta - b_true)[after].min()
        worst_mem, best_rls = max(worst_mem, mem), min(best_rls, rls)
        assert mem < 0.1, (seed, mem)
        assert rls > 0.1, (seed, rls)
    print(f"\n[T2 N(0,1) x] delta 0.01 worst |beta-2| from bar 1300: {worst_mem:.4f}; "
          f"delta 0 smallest |beta-2| there: {best_rls:.4f}")


def test_2b_log_price_step_forecast_recovers_slope_reported():
    # Log-price x (level 4.6, 1% steps): slope and intercept trade off, so only the forecast is asserted.
    # Slope 1.0 -> 1.2 at bar 1000 (a jump of about 0.92 in y, 46 noise units). Asserted: the RMS of the
    # one-step forecast error over the 50 bars ending at bar 1000 + 6/delta is under 2 times the noise
    # (measured 0.87 to 1.64 over the five seeds). REPORTED only, at 1000 + 3/delta: that RMS was still
    # 1.80 to 8.93 times the noise and |beta - 1.2| was 0.015 to 1.515 (0.007 to 0.618 at 6/delta).
    noise, delta, step_at, n = 0.02, 0.01, 1000, 2000
    k3, k6 = step_at + int(3 / delta), step_at + int(6 / delta)
    for seed in range(5):
        rng = np.random.default_rng(300 + seed)
        x = logpx(rng, n)
        b_true = np.where(np.arange(n) < step_at, 1.0, 1.2)
        y = b_true * x + 0.5 + rng.normal(0.0, noise, n)
        res = K.kalman_regression(y, x, delta=delta)
        rms = {k: np.sqrt(np.mean(res.forecast_error[k - 49 : k + 1] ** 2)) / noise for k in (k3, k6)}
        print(f"\n[T2b seed {seed}] forecast RMS / noise: {rms[k3]:.2f} at bar {k3}, {rms[k6]:.2f} at bar {k6}; "
              f"reported |beta - 1.2|: {abs(res.beta[k3] - 1.2):.3f} at {k3}, {abs(res.beta[k6] - 1.2):.3f} at {k6}")
        assert rms[k6] < 2.0, (seed, rms[k6])


# ------------------------------------------------------------------ 3. causality
@pytest.mark.parametrize("with_missing", [False, True])
@pytest.mark.parametrize("delta,intercept", [(0.01, True), (0.05, True), (0.0, True), (0.05, False)])
def test_3_no_output_row_depends_on_a_later_bar(with_missing, delta, intercept):
    # Bit-for-bit: the first k rows of every output, computed on all 400 bars, equal the outputs computed
    # on the first k bars only, at 50 cut points. A constant stretch makes the cap act inside the series.
    rng = np.random.default_rng(3)
    y, x = pair(rng, 400)
    x[150:230] = x[150]
    if with_missing:
        y[rng.choice(400, 40, replace=False)] = np.nan
        x[rng.choice(400, 20, replace=False)] = np.nan
        y[0] = np.nan
    full, _ = run(y, x, delta=delta, intercept=intercept)
    cuts = np.linspace(20, 400, 50).astype(int)
    for k in cuts:
        part, _ = run(y[:k], x[:k], delta=delta, intercept=intercept)
        for f in FIELDS:
            assert getattr(full, f)[:k].tobytes() == getattr(part, f).tobytes(), (k, f)


# ------------------------------------------------------------------ 4. rescaling
def test_4_rescaling_y_scales_the_state_and_leaves_z():
    # y times c is exact from the start (theta and the prior are linear in y; R scales by c^2): from
    # warm-up on, beta, alpha, forecast_error scale by c and z, forecast_scale do not change, 1e-6 relative
    # (z and forecast_error with a floor of 1 and of the noise level, as they cross zero).
    rng = np.random.default_rng(4)
    y, x = pair(rng, 2000)
    base = K.kalman_regression(y, x, delta=0.01)
    w = slice(60, None)
    noise = np.sqrt(base.obs_var[w])
    for c in (1e-3, 7.0, 250.0):
        r = K.kalman_regression(c * y, x, delta=0.01)
        np.testing.assert_allclose(r.beta[w] / c, base.beta[w], rtol=1e-6)
        np.testing.assert_allclose(r.alpha[w] / c, base.alpha[w], rtol=1e-6)
        assert np.all(np.abs(r.forecast_error[w] / c - base.forecast_error[w])
                      <= 1e-6 * np.maximum(np.abs(base.forecast_error[w]), noise))
        np.testing.assert_allclose(r.forecast_scale[w], base.forecast_scale[w], rtol=1e-6)
        np.testing.assert_allclose(r.obs_var[w] / c**2, base.obs_var[w], rtol=1e-6)
        assert np.all(np.abs(r.z[w] - base.z[w]) <= 1e-6 * np.maximum(np.abs(base.z[w]), 1.0))


def test_4_rescaling_x_divides_beta_and_leaves_alpha_and_z_once_the_prior_has_faded():
    # The prior is a fixed variance on the slope, so x times c puts a prior of prior_var c^2 on the
    # rescaled slope: the smaller c, the tighter that prior and the longer it takes to fade (it decays
    # like (1 - delta)^t). Measured at delta 0.01 on this data: the last bar above 1e-6 was 757 for c = 0.1
    # and 393 for c = 10 and 100 (on other data 461 and 67: how long the prior takes to fade depends on
    # how much x moves). The worst error from bar 800 was 6.3e-7 at c = 0.1, 1.2e-8 at c = 10 and 100.
    # Compared from bar 1000, relative 1e-6 on beta * c,
    # alpha and z (z with a floor of 1, as it crosses zero).
    rng = np.random.default_rng(38)
    y, x = pair(rng, 2000)
    base = K.kalman_regression(y, x, delta=0.01)
    w = slice(1000, None)
    for c in (0.1, 10.0, 100.0):
        r = K.kalman_regression(y, c * x, delta=0.01)
        np.testing.assert_allclose(r.beta[w] * c, base.beta[w], rtol=1e-6)
        np.testing.assert_allclose(r.alpha[w], base.alpha[w], rtol=1e-6)
        assert np.all(np.abs(r.z[w] - base.z[w]) <= 1e-6 * np.maximum(np.abs(base.z[w]), 1.0)), c


# ------------------------------------------------------------------ 5. calibration
@pytest.mark.parametrize("delta,tol_mean,tol_var,tol_ac1", [
    (0.0, 0.03, 0.05, 0.03),
    (0.01, 0.03, 0.05, 0.03),
    # A fixed state is the filter's own model only at delta 0. With memory the filter fits recent noise:
    # variance about 5% high and lag-one autocorrelation about -0.03 at delta 0.05, so the wider band.
    (0.05, 0.03, 0.10, 0.06),
])
def test_5_z_is_calibrated_on_data_from_the_model(delta, tol_mean, tol_var, tol_ac1):
    # 20,000 bars, fixed state, Gaussian noise. Standard errors: mean 0.007, variance 0.01, lag-one 0.007.
    rng = np.random.default_rng(5)
    y, x = pair(rng, 20000)
    z = K.kalman_regression(y, x, delta=delta).z[60:]
    assert np.isfinite(z).all()
    mean, var, ac1 = z.mean(), z.var(), np.corrcoef(z[:-1], z[1:])[0, 1]
    print(f"\n[T5 delta={delta}] mean {mean:+.4f} var {var:.4f} ac1 {ac1:+.4f}")
    assert abs(mean) < tol_mean
    assert abs(var - 1.0) < tol_var
    assert abs(ac1) < tol_ac1


# ------------------------------------------------------------------ 6. constant x, gaps, missing values
@pytest.mark.parametrize("delta", [0.01, 0.05])
def test_6a_constant_x_stays_finite_follows_the_level_and_needs_the_cap(delta):
    # x = 4.6 for 5,000 bars, y = 2 + N(0, 0.01). What it returns: beta x + alpha follows the level of y
    # (within 0.01 = one noise unit; the discounted mean's standard error is 0.0007 to 0.0016), split as
    # the smallest [beta, alpha] that fits, so beta / alpha = x; z stays calibrated (variance within 0.15).
    rng = np.random.default_rng(6)
    n = 5000
    x = np.full(n, 4.6)
    y = 2.0 + rng.normal(0.0, 0.01, n)
    res, tr = run(y, x, delta=delta)
    assert np.all(res.forecast_scale >= 1.0)
    for f in FIELDS:
        assert np.isfinite(getattr(res, f)[60:]).all(), f
    fit = res.beta * 4.6 + res.alpha
    assert np.abs(fit[60:] - 2.0).max() < 0.01
    np.testing.assert_allclose(res.beta[60:] / res.alpha[60:], 4.6, rtol=1e-3)
    zvar = res.z[60:].var()
    print(f"\n[T6a delta={delta}] min S* {res.forecast_scale.min():.4f}; max |fit - 2| "
          f"{np.abs(fit[60:] - 2.0).max():.4f}; z var {zvar:.4f}; cap acted on {tr.capped.sum()} bars")
    assert abs(zvar - 1.0) < 0.15
    # Two-way: with the cap S* stays near 1 + delta on the constant stretch (measured 1.0101 and 1.0526);
    # without it the uninformed direction grows without limit and S* runs past 1e6 (measured 2.2e9 and
    # 2.3e9 in square-root form; the matrix form instead went below 1 or failed).
    assert res.forecast_scale[60:].max() < 1.1
    nocap, _ = run(y, x, delta=delta, cap=False)
    assert np.nanmax(nocap.forecast_scale) > 1e6


@pytest.mark.parametrize("delta,slope_tol", [(0.05, 0.15), (0.01, 0.03)])
def test_6b_a_long_gap_is_survived_and_the_slope_relearned(delta, slope_tol):
    # y missing on bars 1000-1599 (x keeps moving). x step 0.02 and noise 0.005 so that a 20-bar memory
    # (delta 0.05) identifies the slope at all. Slope tolerance per delta from the builder's measurement:
    # worst |beta - 1.3| from bar 1600 + 3/delta on was 0.076 at delta 0.05 and 0.0105 at 0.01, and the
    # same tolerance must hold before the gap (it is the filter's ordinary error, not a gap allowance).
    rng = np.random.default_rng(7)
    n = 4000
    y, x = pair(rng, n, noise=0.005, step=0.02)
    y[1000:1600] = np.nan
    res, tr = run(y, x, delta=delta)
    has = ~np.isnan(y)
    assert np.isfinite(res.beta).all() and np.isfinite(res.alpha).all()
    for f in ("forecast_error", "forecast_scale", "obs_var", "z"):
        assert np.isfinite(getattr(res, f)[has][60:]).all(), f
    assert np.all(res.forecast_scale[has] >= 1.0)
    back = 1600 + int(3 / delta)
    err = np.abs(res.beta - 1.3)
    print(f"\n[T6b delta={delta}] |beta-1.3| at bar {back}: {err[back]:.4f}; worst after: {err[back:].max():.4f}; "
          f"worst bars {int(3 / delta)}-999: {err[int(3 / delta):1000].max():.4f}; z at 1600: {res.z[1600]:+.3f}")
    assert err[back:].max() < slope_tol
    assert err[int(3 / delta):1000].max() < slope_tol
    if delta == 0.05:
        # 600 bars of discount at 5% inflate P* by 1e13: the cap returns it to the prior and z is near zero
        assert tr.capped[1600] and abs(res.z[1600]) < 0.05


@pytest.mark.parametrize("intercept", [True, False])
def test_6c_missing_values_as_specified(intercept):
    rng = np.random.default_rng(8)
    y, x = pair(rng, 600)
    y[[0, 1]] = np.nan                  # leading bars with nothing to learn from
    y[[100, 101, 102, 300]] = np.nan    # y missing
    x[[200, 450]] = np.nan              # x missing
    miss = np.isnan(y) | np.isnan(x)
    res, tr = run(y, x, delta=0.02, intercept=intercept, keep_cov=True)
    idx = np.nonzero(miss)[0]
    for f in ("forecast_error", "forecast_scale", "obs_var", "z"):
        assert np.isnan(getattr(res, f)[idx]).all(), f
    assert res.beta[0] == 0.0 and res.alpha[0] == 0.0 and res.beta[1] == 0.0
    for t in idx[idx > 0]:
        assert res.beta[t] == res.beta[t - 1] and res.alpha[t] == res.alpha[t - 1]
        # n and d unchanged on a missing bar (seen through the private trace)
        assert tr.count[t] == tr.count[t - 1] and tr.sum_sq[t] == tr.sum_sq[t - 1]
        np.testing.assert_array_equal(tr.cov[t], tr.cov_pred[t])     # no update
    for t in idx[idx > 2]:
        # the discount still applies on a missing bar (no cap acts on this data after the first bars)
        assert not tr.capped[t]
        np.testing.assert_allclose(tr.cov[t], tr.cov[t - 1] / (1 - 0.02), rtol=1e-14)
    # the leading missing bars change nothing: up to the first interior gap, and on its first bar back,
    # the outputs equal those of the series with the missing rows removed
    keep = ~miss
    comp = K.kalman_regression(y[keep], x[keep], delta=0.02, intercept=intercept)
    pos = np.cumsum(keep) - 1
    for f in FIELDS:
        assert getattr(res, f)[2:100].tobytes() == getattr(comp, f)[:98].tobytes(), f
    assert res.obs_var[103] == comp.obs_var[pos[103]]
    # warm-up counts absorbed bars: with 2 leading bars missing, the first z is at bar 62, none before
    assert np.isnan(res.z[:62]).all() and np.isfinite(res.z[62])
    # at delta 0 nothing passes on a missing bar: the bars with data equal the compressed series exactly
    full0 = K.kalman_regression(y, x, delta=0.0, intercept=intercept)
    comp0 = K.kalman_regression(y[keep], x[keep], delta=0.0, intercept=intercept)
    for f in FIELDS:
        assert getattr(full0, f)[keep].tobytes() == getattr(comp0, f).tobytes(), f


@pytest.mark.parametrize("intercept", [True, False])
def test_6c_a_constant_x_does_not_divide_by_zero_with_or_without_intercept(intercept):
    # Short constant runs at delta 0 (no cap involved): finite, and without intercept beta = y / x.
    rng = np.random.default_rng(9)
    y = 2.0 + rng.normal(0.0, 0.01, 300)
    res = K.kalman_regression(y, np.full(300, 4.6), delta=0.0, intercept=intercept)
    for f in FIELDS:
        assert np.isfinite(getattr(res, f)[60:]).all(), f
    lvl = res.beta[-1] * 4.6 + res.alpha[-1]
    assert abs(lvl - y.mean()) < 1e-6


# ------------------------------------------------------------------ 7. covariance health
def test_7_covariance_symmetric_positive_definite_over_100k_bars():
    # 100,000 bars at delta 0.05 with a constant stretch of x (bars 30,000-34,999) and a gap in y
    # (60,000-60,999). The private core carries P*_pred and P* per bar (keep_cov=True).
    rng = np.random.default_rng(10)
    n = 100_000
    y, x = pair(rng, n)
    x[30_000:35_000] = x[30_000]
    y[60_000:61_000] = np.nan
    res, tr = run(y, x, delta=0.05, keep_cov=True)
    for cov in (tr.cov_pred, tr.cov):
        assert cov.shape == (n, 2, 2) and np.isfinite(cov).all()
        assert np.array_equal(cov[:, 0, 1], cov[:, 1, 0])
        eig = np.linalg.eigvalsh(cov)
        assert eig.min() > 0.0
        assert eig.max() <= PV * (1 + 1e-9)
    assert tr.capped[31_000:35_000].all() and tr.capped[60_500]   # the cap did act in both stretches
    has = ~np.isnan(y)
    assert np.all(res.forecast_scale[has] >= 1.0) and np.isfinite(res.z[has][60:]).all()
    # the no-intercept branch, shorter
    _, tr1 = run(y[:20_000], x[:20_000], delta=0.05, intercept=False, keep_cov=True)
    assert np.isfinite(tr1.cov).all() and (tr1.cov > 0).all() and (tr1.cov_pred <= PV).all()


# ------------------------------------------------------------------ 8. bad inputs
GOOD = np.arange(10.0)


@pytest.mark.parametrize("y,x", [
    (np.ones((5, 2)), np.ones((5, 2))),           # not one-dimensional
    (3.0, 4.0),                                    # scalars
    (GOOD, GOOD[:-1]),                             # different lengths
    ([1.0, 2.0], [1.0, 2.0]),                      # fewer than 3 pairs
    ([1.0, 2.0, np.nan, 4.0], [1.0, np.nan, 3.0, 4.0]),  # 2 finite pairs
    ([], []),
    (np.r_[GOOD[:-1], np.inf], GOOD),              # infinite
    (GOOD, np.r_[-np.inf, GOOD[1:]]),
    (["a", "b", "c"], [1.0, 2.0, 3.0]),            # not numeric
    (GOOD, [{"a": 1}] * 10),
    (["1.5", "2", "3"], [1.0, 2.0, 3.0]),          # numeric text is still text
    (np.array(["1", "2", "3"]), [1.0, 2.0, 3.0]),
    (GOOD, np.array(GOOD, dtype=object)),          # objects, even holding numbers
    ([1.0, None, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0]),  # None is not a missing value: use NaN
    ([True, False, True], [1.0, 2.0, 3.0]),        # booleans are not prices
    ([b"1", b"2", b"3"], [1.0, 2.0, 3.0]),
])
def test_8_bad_series_refused(y, x):
    with pytest.raises(ValueError):
        K.kalman_regression(y, x)


@pytest.mark.parametrize("kw", [
    {"delta": -0.01}, {"delta": 0.5}, {"delta": 0.9}, {"delta": float("nan")}, {"delta": True}, {"delta": "0.1"},
    {"warmup": 1}, {"warmup": 0}, {"warmup": 2.5}, {"warmup": True}, {"warmup": None},
    {"intercept": 1}, {"intercept": "yes"},
    {"prior_var": 0.0}, {"prior_var": -1.0}, {"prior_var": float("inf")}, {"prior_var": float("nan")},
    {"prior_var": True}, {"prior_var": 0.999}, {"prior_var": 1.0001e12}, {"prior_var": 1e13}, {"prior_var": "1e8"},
])
def test_8_bad_parameters_refused(kw):
    with pytest.raises(ValueError, match=next(iter(kw))):
        K.kalman_regression(GOOD * 2 + 1, GOOD, **kw)


def test_8_prior_var_range_ends_are_accepted():
    for pv in (1.0, 1e12, 1, np.float64(1e10)):
        K.kalman_regression(GOOD * 2 + 1, GOOD, prior_var=pv)


def test_8_good_edges_accepted_and_shapes_follow_the_input():
    r = K.kalman_regression([1.0, 2.0, 3.5, np.nan], [1.0, 2.0, 3.0, 4.0], delta=0.0, warmup=2)
    assert all(getattr(r, f).shape == (4,) for f in FIELDS)
    assert np.all(K.kalman_regression(GOOD * 2, GOOD, intercept=False).alpha == 0.0)
    K.kalman_regression(GOOD * 2, GOOD, delta=0.499, warmup=np.int64(3), prior_var=np.float64(1e6))


# ------------------------------------------------------------------ 9. when the cap acts
def test_9_the_cap_acts_only_on_the_untouched_prior_on_ordinary_data():
    # At delta 0 the cap never acts. At delta > 0 it acts only on bars up to and including the one on
    # which the p-th bar is absorbed (the discounted untouched prior is above prior_var by construction),
    # never after; after bar 0's predict step P*_pred is prior_var times the identity exactly.
    rng = np.random.default_rng(38)
    for _ in range(3):                                          # data of test 1
        y, x = pair(rng, 300)
        assert not run(y, x, delta=0.0)[1].capped.any()
    datasets = []
    rng2 = np.random.default_rng(200)                           # data of test 2 (both forms)
    x2 = rng2.normal(0.0, 1.0, 2000)
    datasets.append((np.where(np.arange(2000) < 1000, 1.0, 2.0) * x2 + 0.5 + rng2.normal(0, 0.1, 2000), x2, 0.01))
    rng3 = np.random.default_rng(300)
    x3 = logpx(rng3, 2000)
    datasets.append((np.where(np.arange(2000) < 1000, 1.0, 1.2) * x3 + 0.5 + rng3.normal(0, 0.02, 2000), x3, 0.01))
    y5, x5 = pair(np.random.default_rng(5), 20000)              # data of test 5
    datasets += [(y5, x5, 0.0), (y5, x5, 0.01), (y5, x5, 0.05)]
    for y, x, delta in datasets:
        for intercept, p in ((True, 2), (False, 1)):
            _, tr = run(y, x, delta=delta, intercept=intercept, keep_cov=True)
            acted = np.nonzero(tr.capped)[0]
            if delta == 0.0:
                assert acted.size == 0
            else:
                assert acted.max() <= p - 1, (delta, intercept, acted[:5])
                assert np.array_equal(tr.cov_pred[0], PV * np.eye(p))
    # Two-way: the same counter does see the cap act after the first bars when x stops moving.
    yc, xc = 2.0 + np.random.default_rng(6).normal(0, 0.01, 500), np.full(500, 4.6)
    assert run(yc, xc, delta=0.01)[1].capped[2:].all()


def test_9_the_public_function_cannot_turn_the_cap_off():
    # The private switch `_filter(cap=False)` exists only for test 6a's two-way check. The public function
    # has exactly the specified parameters, no catch-all keyword, and refuses a `cap` (or any extra) keyword.
    params = inspect.signature(K.kalman_regression).parameters
    assert list(params) == ["y", "x", "delta", "warmup", "intercept", "prior_var"]
    assert all(p.kind not in (p.VAR_KEYWORD, p.VAR_POSITIONAL) for p in params.values())
    for kw in ({"cap": False}, {"cap": True}, {"keep_cov": True}):
        with pytest.raises(TypeError):
            K.kalman_regression(GOOD * 2 + 1, GOOD, **kw)
    # and the public result equals the capped core's result on data where the cap acts
    yc, xc = 2.0 + np.random.default_rng(6).normal(0, 0.01, 500), np.full(500, 4.6)
    pub, core = K.kalman_regression(yc, xc, delta=0.05), run(yc, xc, delta=0.05)[0]
    for f in FIELDS:
        assert getattr(pub, f).tobytes() == getattr(core, f).tobytes(), f


# ------------------------------------------------------------------ 10. the forecast is made before the update
@pytest.mark.parametrize("intercept", [True, False])
@pytest.mark.parametrize("delta", [0.0, 0.01, 0.05])
def test_10_forecast_error_and_z_from_the_reported_arrays(intercept, delta):
    # For every bar t >= 1 with data the forecast error is the one made with the state reported for bar
    # t - 1 (carried across missing bars), and z = e / sqrt(R S*) from the reported arrays. Measured worst
    # difference 0.0 over 10 seeds (the code evaluates the same expressions), so asserted exactly.
    for seed in range(10):
        rng = np.random.default_rng(400 + seed)
        y, x = pair(rng, 800)
        y[rng.choice(800, 60, replace=False)] = np.nan
        x[rng.choice(800, 30, replace=False)] = np.nan
        r = K.kalman_regression(y, x, delta=delta, intercept=intercept)
        has = ~(np.isnan(y) | np.isnan(x))
        has[0] = False
        made_before = y[1:] - (r.beta[:-1] * x[1:] + r.alpha[:-1])
        np.testing.assert_array_equal(r.forecast_error[has], made_before[has[1:]])
        expected_z = r.forecast_error / np.sqrt(r.obs_var * r.forecast_scale)
        ok = np.isfinite(r.z)
        assert ok.sum() > 500
        np.testing.assert_array_equal(r.z[ok], expected_z[ok])


# ------------------------------------------------------------------ 11. slowly moving x, large prior_var
def _near_constant(seed):
    """Near-constant x at a log-price level in four shapes (the checker's repro shape and three
    slower ones), noise 1e-3."""
    rng = np.random.default_rng(seed)
    n = 2000
    kind = seed % 4
    if kind == 0:
        x = 4.6 + np.cumsum(rng.normal(0.0, 1e-4, n))      # the checker's repro shape
    elif kind == 1:
        x = 4.6 + np.cumsum(rng.normal(0.0, 1e-5, n))
    elif kind == 2:
        x = 4.6 + rng.normal(0.0, 1e-4, n)
    else:
        x = 4.6 + rng.normal(0.0, 1e-6, n)
    return 1.2 * x + 0.3 + rng.normal(0.0, 1e-3, n), x


def _healthy(res, y, x):
    has = ~(np.isnan(y) | np.isnan(x))
    assert np.all(res.forecast_scale[has] >= 1.0)
    for f in FIELDS:
        assert np.isfinite(getattr(res, f)[60:]).all(), f


def test_11_the_checkers_repro_keeps_the_forecast_scale_at_least_one():
    # At commit 8bc8750 (covariance carried as a matrix) this gave a smallest forecast_scale of -3.78.
    rng = np.random.default_rng(0)
    x = 4.6 + np.cumsum(rng.normal(0, 1e-4, 2000))
    y = 1.2 * x + .3 + rng.normal(0, 1e-3, 2000)
    res = K.kalman_regression(y, x, delta=0.01, prior_var=1e12)
    _healthy(res, y, x)


@pytest.mark.parametrize("prior_var", [1e8, 1e9, 1e10, 1e12])
def test_11_near_constant_x_family_never_fails(prior_var):
    # 200 seeded runs per prior_var. At 8bc8750 (matrix form) this family failed (raised, S* below 1 or
    # not finite) in 0, 0, 60 and 180 of 200 runs at 1e8, 1e9, 1e10, 1e12; in square-root form in none.
    for seed in range(200):
        y, x = _near_constant(seed)
        _healthy(K.kalman_regression(y, x, delta=0.01, prior_var=prior_var), y, x)


def test_11_log_price_family_at_the_largest_prior_var():
    # Log-price x (1% steps) at prior_var 1e12: 3 of these 200 runs failed at 8bc8750, none now.
    for seed in range(200):
        rng = np.random.default_rng(1000 + seed)
        y, x = pair(rng, 2000, beta=1.2, alpha=0.3)
        _healthy(K.kalman_regression(y, x, delta=0.01, prior_var=1e12), y, x)
