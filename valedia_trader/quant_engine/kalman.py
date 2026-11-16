"""Kalman hedge-ratio filter: a causal regression y_t = beta_t x_t + alpha_t + e_t whose state
theta_t = [beta_t, alpha_t] is a random walk, run in discount form. Every value labelled t uses
data at or before t only; nothing looks at a later bar and nothing is normalised on the full sample.

Discount form, with the state covariance carried in units of the observation variance (P*), so the
estimates do not depend on the noise level and there is no price-scale constant to tune:

    predict   P*_pred = P*_prev / (1 - delta), then its eigenvalues are clipped at prior_var
    forecast  F_t = [x_t, 1],  e_t = y_t - F_t theta_prev,  S*_t = F_t P*_pred F_t' + 1
    update    K_t = P*_pred F_t' / S*_t,  theta_t = theta_prev + K_t e_t
              P*_t = (I - K F) P*_pred (I - K F)' + K K'   (Joseph form, kept symmetric)
    noise     n_t = (1 - delta) n_{t-1} + 1,  d_t = (1 - delta) d_{t-1} + e_t^2 / S*_t
              R_t = d_{t-1} / n_{t-1}  (bars before t only),  z_t = e_t / sqrt(R_t S*_t)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

__all__ = ["KalmanRegression", "kalman_regression"]


@dataclass(frozen=True)
class KalmanRegression:
    beta: np.ndarray            # filtered slope AFTER seeing bar t
    alpha: np.ndarray           # filtered intercept AFTER seeing bar t (zeros when intercept=False)
    forecast_error: np.ndarray  # e_t = y_t - (beta x_t + alpha) with the state held BEFORE bar t's update
    forecast_scale: np.ndarray  # S*_t = F P*_pred F' + 1, in units of the observation variance
    obs_var: np.ndarray         # R_t: the observation variance estimate used at bar t (from bars before t)
    z: np.ndarray               # e_t / sqrt(R_t S*_t); NaN until `warmup` bars have been seen


@dataclass(frozen=True)
class _Trace:
    """What the private core exposes to tests; not part of the public result."""
    capped: np.ndarray                 # bool per bar: the cap clipped P*_pred on this bar
    cov_pred: np.ndarray | None        # P*_pred after the discount and the cap, shape (n, p, p)
    cov: np.ndarray | None             # P* after bar t (P*_pred carried on a missing bar), shape (n, p, p)
    count: np.ndarray                  # n_t after bar t (the discounted number of counted bars)
    sum_sq: np.ndarray                 # d_t after bar t (the discounted sum of e^2 / S*)


def _is_real(v) -> bool:
    return isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, (bool, np.bool_))


def _inputs(y, x) -> tuple[np.ndarray, np.ndarray]:
    out = []
    for v, name in ((y, "y"), (x, "x")):
        try:
            a = np.array(v, dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a numeric series") from exc
        if a.ndim != 1:
            raise ValueError(f"{name} must be one-dimensional, got {a.ndim} dimensions")
        if np.isinf(a).any():
            raise ValueError(f"{name} contains infinite values (use NaN for a missing bar)")
        out.append(a)
    ya, xa = out
    if ya.size != xa.size:
        raise ValueError(f"y and x must have the same length, got {ya.size} and {xa.size}")
    pairs = int(np.count_nonzero(~(np.isnan(ya) | np.isnan(xa))))
    if pairs < 3:
        raise ValueError(f"need at least 3 bars with both y and x present, got {pairs}")
    return ya, xa


def _params(delta, warmup, intercept, prior_var) -> None:
    if not _is_real(delta) or not 0.0 <= float(delta) < 0.5:
        raise ValueError(f"delta must be a number in [0, 0.5), got {delta!r}")
    if not isinstance(warmup, (int, np.integer)) or isinstance(warmup, (bool, np.bool_)) or warmup < 2:
        raise ValueError(f"warmup must be an integer of at least 2, got {warmup!r}")
    if not isinstance(intercept, (bool, np.bool_)):
        raise ValueError(f"intercept must be True or False, got {intercept!r}")
    if not _is_real(prior_var) or not (math.isfinite(float(prior_var)) and float(prior_var) > 0.0):
        raise ValueError(f"prior_var must be a finite positive number, got {prior_var!r}")


def _run_two(yl, xl, delta, warmup, prior_var, keep_cov, cap):
    """Slope and intercept: state [beta, alpha], covariance [[p11, p12], [p12, p22]]."""
    nan = math.nan
    disc, keep = 1.0 / (1.0 - delta), 1.0 - delta
    b = a = 0.0
    p11 = p22 = prior_var
    p12 = 0.0
    nn = dd = 0.0
    absorbed = 0
    beta, alpha, fe, fs, ov, zz, capped, cpred, cpost = [], [], [], [], [], [], [], [], []
    cnt, ssq = [], []
    for yt, xt in zip(yl, xl):
        hit = False
        if delta > 0.0:
            p11 *= disc
            p12 *= disc
            p22 *= disc
            if cap:
                m = 0.5 * (p11 + p22)
                r = math.hypot(0.5 * (p11 - p22), p12)
                lam = m + r
                if lam > prior_var:
                    hit = True
                    if m - r >= prior_var:          # no direction informed: back to the prior itself
                        p11 = p22 = prior_var
                        p12 = 0.0
                    else:                           # clip the one uninformed direction (rank one)
                        u1, u2 = p12, lam - p11
                        w1, w2 = lam - p22, p12
                        if w1 * w1 + w2 * w2 > u1 * u1 + u2 * u2:
                            u1, u2 = w1, w2
                        s = (lam - prior_var) / (u1 * u1 + u2 * u2)
                        p11 -= s * u1 * u1
                        p12 -= s * u1 * u2
                        p22 -= s * u2 * u2
        capped.append(hit)
        if keep_cov:
            cpred.append((p11, p12, p22))
        if yt != yt or xt != xt:                    # missing bar: time passes, nothing is learned
            beta.append(b)
            alpha.append(a)
            fe.append(nan)
            fs.append(nan)
            ov.append(nan)
            zz.append(nan)
            if keep_cov:
                cpost.append((p11, p12, p22))
            cnt.append(nn)
            ssq.append(dd)
            continue
        g1 = p11 * xt + p12
        g2 = p12 * xt + p22
        s_star = xt * g1 + g2 + 1.0
        e = yt - (b * xt + a)
        r_t = dd / nn if nn > 0.0 else nan
        z = e / math.sqrt(r_t * s_star) if absorbed >= warmup and r_t > 0.0 else nan
        k1, k2 = g1 / s_star, g2 / s_star
        b += k1 * e
        a += k2 * e
        a11, a12, a21, a22 = 1.0 - k1 * xt, -k1, -k2 * xt, 1.0 - k2
        m11 = a11 * p11 + a12 * p12
        m12 = a11 * p12 + a12 * p22
        m21 = a21 * p11 + a22 * p12
        m22 = a21 * p12 + a22 * p22
        n11 = m11 * a11 + m12 * a12 + k1 * k1
        n12 = m11 * a21 + m12 * a22 + k1 * k2
        n21 = m21 * a11 + m22 * a12 + k2 * k1
        n22 = m21 * a21 + m22 * a22 + k2 * k2
        p11, p12, p22 = n11, 0.5 * (n12 + n21), n22
        if absorbed >= 2:                           # the first p bars are fitted exactly: not counted
            nn = keep * nn + 1.0
            dd = keep * dd + e * e / s_star
        absorbed += 1
        beta.append(b)
        alpha.append(a)
        fe.append(e)
        fs.append(s_star)
        ov.append(r_t)
        zz.append(z)
        if keep_cov:
            cpost.append((p11, p12, p22))
        cnt.append(nn)
        ssq.append(dd)

    def mats(rows):
        c = np.array(rows, dtype=float).reshape(-1, 3)
        return np.stack([np.stack([c[:, 0], c[:, 1]], -1), np.stack([c[:, 1], c[:, 2]], -1)], -2)

    return (beta, alpha, fe, fs, ov, zz, capped,
            mats(cpred) if keep_cov else None, mats(cpost) if keep_cov else None, cnt, ssq)


def _run_one(yl, xl, delta, warmup, prior_var, keep_cov, cap):
    """Slope only: state [beta], scalar covariance p."""
    nan = math.nan
    disc, keep = 1.0 / (1.0 - delta), 1.0 - delta
    b = 0.0
    p = prior_var
    nn = dd = 0.0
    absorbed = 0
    beta, fe, fs, ov, zz, capped, cpred, cpost = [], [], [], [], [], [], [], []
    cnt, ssq = [], []
    for yt, xt in zip(yl, xl):
        hit = False
        if delta > 0.0:
            p *= disc
            if cap and p > prior_var:
                p = prior_var
                hit = True
        capped.append(hit)
        if keep_cov:
            cpred.append(p)
        if yt != yt or xt != xt:
            beta.append(b)
            fe.append(nan)
            fs.append(nan)
            ov.append(nan)
            zz.append(nan)
            if keep_cov:
                cpost.append(p)
            cnt.append(nn)
            ssq.append(dd)
            continue
        g = p * xt
        s_star = xt * g + 1.0
        e = yt - b * xt
        r_t = dd / nn if nn > 0.0 else nan
        z = e / math.sqrt(r_t * s_star) if absorbed >= warmup and r_t > 0.0 else nan
        k = g / s_star
        b += k * e
        one = 1.0 - k * xt
        p = one * one * p + k * k
        if absorbed >= 1:
            nn = keep * nn + 1.0
            dd = keep * dd + e * e / s_star
        absorbed += 1
        beta.append(b)
        fe.append(e)
        fs.append(s_star)
        ov.append(r_t)
        zz.append(z)
        if keep_cov:
            cpost.append(p)
        cnt.append(nn)
        ssq.append(dd)
    n = len(beta)
    return (beta, [0.0] * n, fe, fs, ov, zz, capped,
            np.array(cpred, dtype=float).reshape(-1, 1, 1) if keep_cov else None,
            np.array(cpost, dtype=float).reshape(-1, 1, 1) if keep_cov else None, cnt, ssq)


def _filter(y, x, *, delta: float = 0.01, warmup: int = 60, intercept: bool = True,
            prior_var: float = 1e8, keep_cov: bool = False, cap: bool = True
            ) -> tuple[KalmanRegression, _Trace]:
    """Private core: the public result plus a trace for tests. `cap=False` exists only so a test can
    show what the cap prevents; nothing outside the tests may pass it."""
    ya, xa = _inputs(y, x)
    _params(delta, warmup, intercept, prior_var)
    run = _run_two if intercept else _run_one
    beta, alpha, fe, fs, ov, zz, capped, cpred, cpost, cnt, ssq = run(
        ya.tolist(), xa.tolist(), float(delta), int(warmup), float(prior_var), keep_cov, cap)
    res = KalmanRegression(beta=np.array(beta, dtype=float), alpha=np.array(alpha, dtype=float),
                           forecast_error=np.array(fe, dtype=float), forecast_scale=np.array(fs, dtype=float),
                           obs_var=np.array(ov, dtype=float), z=np.array(zz, dtype=float))
    return res, _Trace(capped=np.array(capped, dtype=bool), cov_pred=cpred, cov=cpost,
                       count=np.array(cnt, dtype=float), sum_sq=np.array(ssq, dtype=float))


def kalman_regression(y, x, *, delta: float = 0.01, warmup: int = 60, intercept: bool = True,
                      prior_var: float = 1e8) -> KalmanRegression:
    """Causal Kalman regression of y on x (callers pass log prices): a slowly moving hedge ratio and
    intercept, and for every bar a one-step forecast error and its standardised value z.

    delta, in [0, 0.5), is the share of information forgotten per bar (memory about 1/delta bars).
    delta = 0 is recursive least squares: with the prior below it is least squares with a ridge of
    1/prior_var on [beta, alpha]. On 20 log-price series in the tests the filter differed from plain
    least squares by at most 2.6e-5 of the largest coefficient from bar 60 on (the ridge matters where
    X'X is nearly singular: up to 1.2e-4 on 30-bar prefixes) and from the ridge problem itself by at
    most 1.5e-5 from bar 30 on.

    Start and prior: theta = 0 and P* = prior_var times the identity, on the inputs as given. With the
    default 1e8 the prior is nearly diffuse: the first p bars (p = 2 with the intercept, 1 without) are
    fitted almost exactly, their forecast_scale is of the order of prior_var (x^2 + 1), and they say
    nothing about the noise, so they are not counted in the variance estimate. Because the prior is a
    fixed variance on the slope, rescaling x by c changes the prior on the rescaled slope by c^2: the
    estimates are invariant to rescaling x only once the prior has faded (in the tests, to 1e-6 from bar
    1000 at delta 0.01 for c in [0.1, 100]; c = 0.1 needed 757 bars; smaller c needs longer, and the
    fade depends on how much x moves). Rescaling y is exact from the first bar.

    The cap: after the discount step the eigenvalues of P*_pred are clipped at prior_var (with no
    intercept, min(P*, prior_var)). The filter is never less certain than knowing nothing. Without the
    cap a direction the data do not inform grows without limit and the forecast scale cancels below 1.
    What a caller sees: after a long stretch with no information about the slope (a constant x, a long
    gap) the slope is treated as unknown and relearned; the forecast scale is then of the order of
    prior_var and z stays near zero until the slope is learned again. A constant x returns finite
    values: the level y is followed (beta x + alpha tracks y), split between slope and intercept as
    the smallest [beta, alpha] that fits (beta / alpha = x); z stays calibrated.

    Observation variance: learned causally with the same discount from e_t^2 / S*_t of the counted
    bars; R_t uses bars before t only and is NaN while nothing is counted. z is NaN while fewer than
    `warmup` bars have been absorbed before bar t, or when R_t is not positive. After a relearning the
    first bars back have e^2 / S* near zero and count, so R runs a little low for a while (accepted).

    Slope and intercept trade off: when x barely moves relative to its level (log prices near 4.6
    moving 1% a bar), slope and intercept are nearly indistinguishable and slow to follow a change in
    the relation: in the tests, 3/delta bars after a slope step from 1.0 to 1.2 the forecast error was
    still 1.8 to 8.9 times the noise and the slope off by up to 1.5; by 6/delta it was under 2 times.
    A caller who needs the slope itself (to size legs) should pass x with its level removed, for
    example the log price relative to a starting value.

    Missing bars (NaN in y or x): no update; beta and alpha carried; forecast_error, forecast_scale,
    obs_var and z NaN on that bar; the discount still applies to P* (time passed); the variance
    estimate is unchanged. An infinite value is refused.

    Raises ValueError for inputs that are not one-dimensional numeric series of equal length with at
    least 3 bars where both are present, for delta outside [0, 0.5), warmup below 2, a non-boolean
    intercept or a prior_var that is not finite and positive.
    """
    return _filter(y, x, delta=delta, warmup=warmup, intercept=intercept, prior_var=prior_var)[0]
