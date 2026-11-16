"""Kalman hedge-ratio filter: a causal regression y_t = beta_t x_t + alpha_t + e_t whose state
theta_t = [beta_t, alpha_t] is a random walk, run in discount form. Every value labelled t uses
data at or before t only; nothing looks at a later bar and nothing is normalised on the full sample.

Discount form, with the state covariance carried in units of the observation variance (P*), so the
estimates do not depend on the noise level and there is no price-scale constant to tune. P* is never
stored: the filter carries a square-root factor S with P* = S S', so the forecast scale is a sum of
squares plus one and cannot fall below 1 however ill-conditioned P* becomes:

    predict   S_pred = S_prev / sqrt(1 - delta), then its singular values are clipped at sqrt(prior_var)
    forecast  F_t = [x_t, 1],  e_t = y_t - F_t theta_prev,  phi = S_pred' F_t',  S*_t = 1 + |phi|^2
    update    K_t = S_pred phi / S*_t,  theta_t = theta_prev + K_t e_t
              S_t = S_pred - c (S_pred phi) phi',  c = 1 / (S*_t + sqrt(S*_t))   (Potter's update)
    noise     n_t = (1 - delta) n_{t-1} + 1,  d_t = (1 - delta) d_{t-1} + e_t^2 / S*_t
              R_t = d_{t-1} / n_{t-1}  (bars before t only),  z_t = e_t / sqrt(R_t S*_t)

Mathematically this is the discount filter with P*_t = (I - K F) P*_pred (I - K F)' + K K'. Potter's
form is used because the observation is a scalar: the update is closed form (no decomposition per
bar), it needs no square root of a matrix, and it is plain arithmetic, so a prefix of the input
gives bit-for-bit the same outputs.
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
    forecast_scale: np.ndarray  # S*_t = F P*_pred F' + 1 (always >= 1), in units of the observation variance
    obs_var: np.ndarray         # R_t: the observation variance estimate used at bar t (from bars before t)
    z: np.ndarray               # e_t / sqrt(R_t S*_t); NaN until `warmup` bars have been seen


@dataclass(frozen=True)
class _Trace:
    """What the private core exposes to tests; not part of the public result."""
    capped: np.ndarray                 # bool per bar: the cap clipped P*_pred on this bar
    cov_pred: np.ndarray | None        # P*_pred = S S' after the discount and the cap, shape (n, p, p)
    cov: np.ndarray | None             # P* = S S' after bar t (P*_pred on a missing bar), shape (n, p, p)
    count: np.ndarray                  # n_t after bar t (the discounted number of counted bars)
    sum_sq: np.ndarray                 # d_t after bar t (the discounted sum of e^2 / S*)


def _is_real(v) -> bool:
    return isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, (bool, np.bool_))


def _inputs(y, x) -> tuple[np.ndarray, np.ndarray]:
    out = []
    for v, name in ((y, "y"), (x, "x")):
        raw = np.asarray(v)
        if raw.dtype.kind not in "iuf":             # never let numpy parse text or objects into numbers
            raise ValueError(f"{name} must be a series of numbers, got values of type {raw.dtype}")
        a = np.array(raw, dtype=float)
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
    if not _is_real(prior_var) or not 1.0 <= float(prior_var) <= 1e12:
        raise ValueError(f"prior_var must be a finite number in [1, 1e12], got {prior_var!r}")


def _run_two(yl, xl, delta, warmup, prior_var, keep_cov, cap):
    """Slope and intercept: state [beta, alpha], covariance factor S = [[s11, s12], [s21, s22]] with
    P* = S S'. Measurement update by Potter's square-root form for a scalar observation."""
    nan = math.nan
    grow, keep = 1.0 / math.sqrt(1.0 - delta), 1.0 - delta
    root = math.sqrt(prior_var)
    b = a = 0.0
    s11 = s22 = root
    s12 = s21 = 0.0
    nn = dd = 0.0
    absorbed = 0
    beta, alpha, fe, fs, ov, zz, capped, cpred, cpost = [], [], [], [], [], [], [], [], []
    cnt, ssq = [], []
    for yt, xt in zip(yl, xl):
        hit = False
        if delta > 0.0:
            s11 *= grow
            s12 *= grow
            s21 *= grow
            s22 *= grow
            if cap:
                q11 = s11 * s11 + s12 * s12             # P* = S S', used only to find the clip
                q22 = s21 * s21 + s22 * s22
                q12 = s11 * s21 + s12 * s22
                m = 0.5 * (q11 + q22)
                lam = m + math.hypot(0.5 * (q11 - q22), q12)    # largest eigenvalue = sigma_1^2
                if lam > prior_var:
                    hit = True
                    det = s11 * s22 - s12 * s21         # sigma_1 sigma_2, accurate from S itself
                    if det * det >= lam * prior_var:    # sigma_2 above too: back to the prior itself
                        s11 = s22 = root
                        s12 = s21 = 0.0
                    else:                               # scale the top singular direction to root
                        u1, u2 = q12, lam - q11
                        w1, w2 = lam - q22, q12
                        if w1 * w1 + w2 * w2 > u1 * u1 + u2 * u2:
                            u1, u2 = w1, w2
                        norm = math.hypot(u1, u2)
                        u1, u2 = u1 / norm, u2 / norm
                        f = 1.0 - root / math.sqrt(lam)
                        r1 = u1 * s11 + u2 * s21        # u' S
                        r2 = u1 * s12 + u2 * s22
                        s11 -= f * u1 * r1
                        s12 -= f * u1 * r2
                        s21 -= f * u2 * r1
                        s22 -= f * u2 * r2
        capped.append(hit)
        if keep_cov:
            cpred.append((s11, s12, s21, s22))
        if yt != yt or xt != xt:                    # missing bar: time passes, nothing is learned
            beta.append(b)
            alpha.append(a)
            fe.append(nan)
            fs.append(nan)
            ov.append(nan)
            zz.append(nan)
            if keep_cov:
                cpost.append((s11, s12, s21, s22))
            cnt.append(nn)
            ssq.append(dd)
            continue
        phi1 = s11 * xt + s21                       # phi = S' F'
        phi2 = s12 * xt + s22
        s_star = 1.0 + phi1 * phi1 + phi2 * phi2    # a sum of squares plus one: never below 1
        g1 = s11 * phi1 + s12 * phi2                # g = S phi = P* F'
        g2 = s21 * phi1 + s22 * phi2
        e = yt - (b * xt + a)
        r_t = dd / nn if nn > 0.0 else nan
        z = e / math.sqrt(r_t * s_star) if absorbed >= warmup and r_t > 0.0 else nan
        b += g1 / s_star * e
        a += g2 / s_star * e
        c = 1.0 / (s_star + math.sqrt(s_star))      # Potter: S <- S (I - c phi phi')
        s11 -= c * g1 * phi1
        s12 -= c * g1 * phi2
        s21 -= c * g2 * phi1
        s22 -= c * g2 * phi2
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
            cpost.append((s11, s12, s21, s22))
        cnt.append(nn)
        ssq.append(dd)

    def mats(rows):                                 # rebuild P* = S S' for inspection
        f = np.array(rows, dtype=float).reshape(-1, 2, 2)
        return f @ np.swapaxes(f, 1, 2)

    return (beta, alpha, fe, fs, ov, zz, capped,
            mats(cpred) if keep_cov else None, mats(cpost) if keep_cov else None, cnt, ssq)


def _run_one(yl, xl, delta, warmup, prior_var, keep_cov, cap):
    """Slope only: state [beta], scalar factor s with P* = s^2."""
    nan = math.nan
    grow, keep = 1.0 / math.sqrt(1.0 - delta), 1.0 - delta
    root = math.sqrt(prior_var)
    b = 0.0
    s = root
    nn = dd = 0.0
    absorbed = 0
    beta, fe, fs, ov, zz, capped, cpred, cpost = [], [], [], [], [], [], [], []
    cnt, ssq = [], []
    for yt, xt in zip(yl, xl):
        hit = False
        if delta > 0.0:
            s *= grow
            if cap and s > root:
                s = root
                hit = True
        capped.append(hit)
        if keep_cov:
            cpred.append(s)
        if yt != yt or xt != xt:
            beta.append(b)
            fe.append(nan)
            fs.append(nan)
            ov.append(nan)
            zz.append(nan)
            if keep_cov:
                cpost.append(s)
            cnt.append(nn)
            ssq.append(dd)
            continue
        phi = s * xt
        s_star = 1.0 + phi * phi
        e = yt - b * xt
        r_t = dd / nn if nn > 0.0 else nan
        z = e / math.sqrt(r_t * s_star) if absorbed >= warmup and r_t > 0.0 else nan
        b += s * phi / s_star * e
        s /= math.sqrt(s_star)                      # Potter for one state: P* <- P* / S*
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
            cpost.append(s)
        cnt.append(nn)
        ssq.append(dd)
    n = len(beta)

    def mats(rows):
        f = np.array(rows, dtype=float).reshape(-1, 1, 1)
        return f * f

    return (beta, [0.0] * n, fe, fs, ov, zz, capped,
            mats(cpred) if keep_cov else None, mats(cpost) if keep_cov else None, cnt, ssq)


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
    most 3e-12 from bar 30 on.

    prior_var, in [1, 1e12]. Precision against a 50-digit copy of the same filter (10 series of 600
    bars, half log prices moving 1% a bar, half near-constant x moving 0.01% a bar; delta 0.01): at
    prior_var 1e8 at most 1.4e-11 of the largest coefficient in beta and alpha, 1.0e-11 of the typical
    forecast error, 1.8e-12 relative in forecast_scale, 6.8e-12 in z; at 1e12 at most 3.4e-10, 9.6e-11,
    9.2e-12 and 7.8e-11. (A covariance carried as a matrix was off by 9e-4 at 1e8 and by order 1 at
    1e12 on the same data, and its forecast scale could go negative.)

    Start and prior: theta = 0 and P* = prior_var times the identity, on the inputs as given. With the
    default 1e8 the prior is nearly diffuse: the first p bars (p = 2 with the intercept, 1 without) are
    fitted almost exactly, their forecast_scale is of the order of prior_var (x^2 + 1), and they say
    nothing about the noise, so they are not counted in the variance estimate. Because the prior is a
    fixed variance on the slope, rescaling x by c changes the prior on the rescaled slope by c^2: the
    estimates are invariant to rescaling x only once the prior has faded (in the tests, to 1e-6 from bar
    1000 at delta 0.01 for c in [0.1, 100]; c = 0.1 needed 757 bars; smaller c needs longer, and the
    fade depends on how much x moves). Rescaling y is exact from the first bar.

    The cap: after the discount step the singular values of the factor are clipped at sqrt(prior_var),
    that is the eigenvalues of P*_pred at prior_var (with no intercept, min(P*, prior_var)). The filter
    is never less certain than knowing nothing. Without the cap a direction the data do not inform
    grows without limit, and the forecast scale and the estimates degrade with it.
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
    obs_var and z NaN on that bar; the discount still applies to the factor (time passed); the variance
    estimate is unchanged. An infinite value, None, text (even numeric text such as "1.5"), booleans
    and objects are refused: the series must hold numbers.

    Raises ValueError for inputs that are not one-dimensional numeric series of equal length with at
    least 3 bars where both are present, for delta outside [0, 0.5), warmup below 2, a non-boolean
    intercept or a prior_var outside [1, 1e12]; each message names the parameter.
    """
    return _filter(y, x, delta=delta, warmup=warmup, intercept=intercept, prior_var=prior_var)[0]
