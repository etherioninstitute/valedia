"""modeled_prob.py — Calibrated probability estimator for the EV gate.

Derived from the Phase-A manuscript integration (see Etherion_Risk_Engine.py):
  - OU mean-reversion of mispricing  (Fusion v20.7 Thm 11.3 / Cor 11.7):
        e(t) = price - fair ~ OU(kappa, sigma_eq);  E[e(t+h)] = e0·exp(-kappa·h)
        gives P(price moves toward fair over horizon h).
  - OS spectral-measure inversion    (YM v38.5 thm:os-gap):
        autocorr(t) = ∫ e^{-tλ} dρ(λ);  invert → dominant reversion rate λ*.

CORE DISCIPLINE (UGFT v32.9): these are calibratable priors, NOT trusted truths.
`calibrate_isotonic` remaps raw model scores onto realized frequencies, and the
backtest (backtest_modeled_prob) reports Brier/reliability/edge so we only trust
what the data supports. Nothing here gates money until the backtest says so.

Pure numpy/scipy. No network. (c) GOATWORTHY Post LLC.
"""
import numpy as np
from scipy.stats import norm
from scipy.optimize import nnls


# ────────────────────────────────────────────────────────────────────────────
# OU mean-reversion (Fusion v20.7 Thm 11.3)
# ────────────────────────────────────────────────────────────────────────────
def fit_ou(mispricing):
    """Fit an OU process to a mispricing series e(t) via AR(1).

    e_{t+1} = phi·e_t + noise  ⇒  kappa = -ln(phi) (reversion rate),
    sigma_eq = std(e) (stationary spread), half_life = ln2/kappa.
    Returns dict; kappa<=0 means no mean reversion detected.
    """
    e = np.asarray(mispricing, dtype=float)
    e = e[np.isfinite(e)]
    if len(e) < 20:
        return {"kappa": 0.0, "sigma_eq": float(np.std(e) if len(e) else 0.0),
                "half_life": np.inf, "phi": 1.0}
    x, y = e[:-1], e[1:]
    denom = np.dot(x, x)
    phi = float(np.dot(x, y) / denom) if denom > 1e-18 else 1.0
    phi = min(max(phi, -0.999), 0.999)
    kappa = -np.log(abs(phi)) if 0 < abs(phi) < 1 else 0.0
    sigma_eq = float(np.std(e))
    half_life = (np.log(2) / kappa) if kappa > 1e-9 else np.inf
    return {"kappa": float(kappa), "sigma_eq": sigma_eq,
            "half_life": float(half_life), "phi": phi}


def ou_prob_toward_fair(mispricing_now, ou, horizon=1):
    """P(price moves toward fair value over `horizon` steps), OU-implied.

    Under OU, E[e(t+h)] = e0·exp(-kappa·h); the expected move is
    mu = e0·(exp(-kappa·h) - 1) (toward 0 when kappa>0). Conditional spread
    sigma_h = sigma_eq·sqrt(1 - exp(-2·kappa·h)). P(reversion move up) = Phi(mu/sigma_h)
    signed so that a large positive mispricing (over-priced) → P(down) high.
    Returns P(next move is UP) in [0,1]; 0.5 = no directional information.
    """
    e0 = float(mispricing_now)
    kappa = ou.get("kappa", 0.0)
    sig = ou.get("sigma_eq", 0.0)
    if kappa <= 1e-9 or sig <= 1e-12:
        return 0.5
    decay = np.exp(-kappa * horizon)
    mu = e0 * (decay - 1.0)                       # expected move (toward fair)
    sigma_h = sig * np.sqrt(max(1e-12, 1.0 - decay ** 2))
    z = mu / (sigma_h + 1e-12)
    return float(norm.cdf(z))                     # P(move up)


# ────────────────────────────────────────────────────────────────────────────
# OS spectral-measure inversion (YM v38.5 thm:os-gap)
# ────────────────────────────────────────────────────────────────────────────
def spectral_reversion(returns, max_lag=40, n_grid=24):
    """Invert the autocorrelation into a positive spectral measure ρ(λ).

    autocorr(t) = ∫ e^{-tλ} dρ(λ). Fit ρ as non-negative weights over a
    log-spaced λ grid via NNLS. Returns the mass-weighted dominant reversion
    rate λ* and the spectral mass fraction at fast (mean-reverting) rates.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < max_lag + 5:
        return {"lambda_star": 0.0, "reverting_mass": 0.0, "half_life": np.inf}
    r = r - r.mean()
    var = np.dot(r, r) / len(r)
    if var <= 1e-18:
        return {"lambda_star": 0.0, "reverting_mass": 0.0, "half_life": np.inf}
    lags = np.arange(0, max_lag + 1)
    ac = np.array([np.dot(r[:len(r) - k], r[k:]) / (len(r) - k) for k in lags]) / var
    # design matrix M[t,j] = exp(-lag_t * lambda_j); solve M w ≈ ac, w>=0
    lam = np.logspace(-2, 0.7, n_grid)            # rates ~0.01 .. 5 per step
    M = np.exp(-np.outer(lags, lam))
    try:
        w, _ = nnls(M, ac)
    except Exception:
        return {"lambda_star": 0.0, "reverting_mass": 0.0, "half_life": np.inf}
    total = w.sum()
    if total <= 1e-12:
        return {"lambda_star": 0.0, "reverting_mass": 0.0, "half_life": np.inf}
    lambda_star = float(np.dot(w, lam) / total)   # mass-weighted mean rate
    reverting_mass = float(w[lam >= 0.1].sum() / total)  # fraction at reverting rates
    half_life = (np.log(2) / lambda_star) if lambda_star > 1e-9 else np.inf
    return {"lambda_star": lambda_star, "reverting_mass": reverting_mass,
            "half_life": float(half_life)}


# ────────────────────────────────────────────────────────────────────────────
# Combined modeled probability
# ────────────────────────────────────────────────────────────────────────────
def modeled_prob_up(prices, fair_window=20, horizon=1):
    """Calibrated-prior P(next move UP) from OU + spectral reversion.

    fair = EMA(prices, fair_window); mispricing e = price - fair.
    The spectral reverting-mass gates how much OU signal to trust (shrinks
    toward 0.5 when the series shows little mean-reverting structure).
    Returns P(up) in [0,1] (0.5 = no signal). This is a RAW score — pass it
    through calibrate_isotonic before using as a probability.
    """
    p = np.asarray(prices, dtype=float)
    p = p[np.isfinite(p)]
    if len(p) < fair_window + 25:
        return 0.5
    # EMA fair value
    alpha = 2.0 / (fair_window + 1.0)
    fair = np.zeros_like(p)
    fair[0] = p[0]
    for i in range(1, len(p)):
        fair[i] = alpha * p[i] + (1 - alpha) * fair[i - 1]
    e = p - fair
    scale = np.std(e[-fair_window * 3:]) + 1e-12
    ou = fit_ou(e / scale)                        # scale-normalized mispricing
    spec = spectral_reversion(np.diff(p) / (p[:-1] + 1e-12))
    p_up = ou_prob_toward_fair(e[-1] / scale, ou, horizon)
    # Trust-shrink toward 0.5 by the spectral reverting mass (0..1).
    trust = max(0.0, min(1.0, spec["reverting_mass"]))
    return 0.5 + (p_up - 0.5) * trust


def ema_fair(prices, window=20):
    """Last EMA of a YES/price series (same EMA as modeled_prob_up).

    A modeled contract fair, not P(next move up). None if the series is
    shorter than `window`.
    """
    p = np.asarray(prices, dtype=float)
    p = p[np.isfinite(p)]
    if len(p) < window:
        return None
    alpha = 2.0 / (window + 1.0)
    fair = float(p[0])
    for x in p[1:]:
        fair = alpha * float(x) + (1.0 - alpha) * fair
    return float(max(0.0, min(1.0, fair)))


# ────────────────────────────────────────────────────────────────────────────
# Calibration + honest scoring
# ────────────────────────────────────────────────────────────────────────────
def calibrate_isotonic(raw_scores, outcomes):
    """Fit an isotonic (monotone) calibration map raw→empirical frequency.

    Returns a function f(score)->calibrated_prob. Uses the pool-adjacent-
    violators algorithm. This is what makes a raw model score an honest
    probability: it is remapped to the frequency actually realized.
    """
    s = np.asarray(raw_scores, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    order = np.argsort(s)
    s_sorted, y_sorted = s[order], y[order]
    # PAVA
    g = y_sorted.astype(float).copy()
    w = np.ones_like(g)
    i = 0
    while i < len(g) - 1:
        if g[i] > g[i + 1] + 1e-12:
            new = (g[i] * w[i] + g[i + 1] * w[i + 1]) / (w[i] + w[i + 1])
            g[i] = new
            g = np.delete(g, i + 1)
            w[i] += w[i + 1]
            w = np.delete(w, i + 1)
            # merged knots collapse s too
            s_sorted = np.delete(s_sorted, i + 1)
            if i > 0:
                i -= 1
        else:
            i += 1
    knots_x, knots_y = s_sorted, g

    def f(score):
        return float(np.interp(score, knots_x, knots_y,
                               left=knots_y[0], right=knots_y[-1]))
    return f


def brier_score(probs, outcomes):
    p = np.asarray(probs, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    return float(np.mean((p - y) ** 2))


def reliability_table(probs, outcomes, bins=5):
    """Reliability: mean predicted vs realized frequency per probability bin."""
    p = np.asarray(probs, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    rows = []
    for i in range(bins):
        m = (p >= edges[i]) & (p < edges[i + 1] if i < bins - 1 else p <= edges[i + 1])
        if m.sum() > 0:
            rows.append((round((edges[i] + edges[i + 1]) / 2, 2),
                         round(float(p[m].mean()), 3),
                         round(float(y[m].mean()), 3),
                         int(m.sum())))
    return rows
