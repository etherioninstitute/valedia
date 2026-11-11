"""Level 7: derivatives. European pricing, Greeks and implied volatility are native vectorized
numpy (validated against py_vollib and QuantLib in the tests); American, barrier and the digital
cross-check use QuantLib. QuantLib maturities are whole days on an Actual/365 calendar."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import QuantLib as ql
from scipy import optimize
from scipy.special import ndtr

__all__ = [
    "bsm_price", "digital_price", "american_option_price", "barrier_option_price", "digital_price_quantlib",
    "put_call_parity_residual", "synthetic_forward", "parity_arbitrage_scan", "box_spread_implied_rate",
    "bsm_greeks", "numerical_greeks", "delta_neutral_hedge", "delta_gamma_neutral_hedge",
    "implied_volatility", "svi_total_variance", "calibrate_svi", "SVIParams", "svi_butterfly_density",
    "smile_summary", "SmileSummary", "term_structure_summary", "iv_rv_signal", "gamma_scalping_pnl_rate",
    "simulate_delta_hedged_pnl", "gamma_scalp_decision",
]

FORMULAS: dict[str, tuple[str, ...]] = {
    "7.1": ("bsm_price", "digital_price", "american_option_price", "barrier_option_price",
            "digital_price_quantlib"),
    "7.2": ("put_call_parity_residual", "synthetic_forward", "parity_arbitrage_scan", "box_spread_implied_rate"),
    "7.3": ("bsm_greeks", "numerical_greeks", "delta_neutral_hedge", "delta_gamma_neutral_hedge"),
    "7.4": ("implied_volatility", "svi_total_variance", "calibrate_svi", "svi_butterfly_density",
            "smile_summary", "term_structure_summary", "iv_rv_signal"),
    "7.5": ("gamma_scalping_pnl_rate", "simulate_delta_hedged_pnl", "gamma_scalp_decision"),
}

_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


def _phi(x):
    return _INV_SQRT_2PI * np.exp(-0.5 * x * x)


def _sign(option) -> np.ndarray:
    o = np.asarray(option)
    if o.dtype.kind in "fiu":
        s = np.sign(o.astype(float))
        if np.any(s == 0):
            raise ValueError("numeric option flags must be +1 (call) or -1 (put)")
        return s
    flat = np.char.lower(o.astype(str))
    is_call, is_put = np.isin(flat, ("call", "c")), np.isin(flat, ("put", "p"))
    if not np.all(is_call | is_put):
        raise ValueError("option must be 'call' or 'put'")
    return np.where(is_call, 1.0, -1.0)


def _inputs(S, K, T, r, sigma, q, option):
    S, K, T, r, sigma, q, w = np.broadcast_arrays(*(np.asarray(v, dtype=float) for v in (S, K, T, r, sigma, q)),
                                                  _sign(option))
    if not all(np.all(np.isfinite(v)) for v in (S, K, T, r, sigma, q)):
        raise ValueError("inputs must be finite")
    if np.any(S <= 0) or np.any(K <= 0):
        raise ValueError("S and K must be positive")
    if np.any(T < 0) or np.any(sigma < 0):
        raise ValueError("T and sigma must be non-negative")
    return tuple(np.atleast_1d(v) for v in (S, K, T, r, sigma, q, w)) + (np.shape(S),)


def _d1d2(S, K, T, r, sigma, q):
    vol = sigma * np.sqrt(T)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / vol
    return d1, d1 - vol


def _out(x, *args):
    return float(x) if all(np.ndim(a) == 0 for a in args) else np.asarray(x)


# ---------------------------------------------------------------- 7.1 pricing
def bsm_price(S, K, T, r, sigma, q=0.0, option="call"):
    """7.1 Black-Scholes-Merton: C = S e^{-qT} N(d1) - K e^{-rT} N(d2) (puts by symmetry). Vectorized.

    At T = 0 or sigma = 0 the price is the discounted forward intrinsic value."""
    Sa, Ka, Ta, ra, va, qa, w, shape = _inputs(S, K, T, r, sigma, q, option)
    fwd, strike = Sa * np.exp(-qa * Ta), Ka * np.exp(-ra * Ta)
    degenerate = (Ta == 0) | (va == 0)
    price = np.maximum(w * (fwd - strike), 0.0)
    if not np.all(degenerate):
        ok = ~degenerate
        d1, d2 = _d1d2(Sa[ok], Ka[ok], Ta[ok], ra[ok], va[ok], qa[ok])
        price = price.copy()
        price[ok] = w[ok] * (fwd[ok] * ndtr(w[ok] * d1) - strike[ok] * ndtr(w[ok] * d2))
    return _out(price.reshape(shape), S, K, T, r, sigma, q, option)


def digital_price(S, K, T, r, sigma, q=0.0, option="call", cash=1.0):
    """7.1 Cash-or-nothing digital: cash * e^{-rT} N(+-d2). Prices prediction-market style binaries."""
    Sa, Ka, Ta, ra, va, qa, w, shape = _inputs(S, K, T, r, sigma, q, option)
    if np.any(va <= 0) and np.any(Ta > 0):
        raise ValueError("sigma must be positive for a digital with time remaining")
    price = np.where(w * (Sa - Ka) > 0, 1.0, 0.0) * np.ones_like(Sa)
    live = Ta > 0
    if np.any(live):
        _, d2 = _d1d2(Sa[live], Ka[live], Ta[live], ra[live], va[live], qa[live])
        price = price.copy()
        price[live] = np.exp(-ra[live] * Ta[live]) * ndtr(w[live] * d2)
    return _out((cash * price).reshape(shape), S, K, T, r, sigma, q, option)


def _ql_setup(S: float, T: float, r: float, sigma: float, q: float):
    if S <= 0 or T <= 0 or sigma <= 0:
        raise ValueError("require S > 0, T > 0 and sigma > 0")
    days = int(round(T * 365))
    if days < 1:
        raise ValueError("T must be at least one day")
    today = ql.Date(2, 1, 2025)
    dc, cal = ql.Actual365Fixed(), ql.NullCalendar()
    process = ql.BlackScholesMertonProcess(
        ql.QuoteHandle(ql.SimpleQuote(float(S))),
        ql.YieldTermStructureHandle(ql.FlatForward(today, float(q), dc)),
        ql.YieldTermStructureHandle(ql.FlatForward(today, float(r), dc)),
        ql.BlackVolTermStructureHandle(ql.BlackConstantVol(today, cal, float(sigma), dc)))
    return today, today + days, process


def _ql_npv(instrument, today) -> float:
    settings = ql.Settings.instance()
    previous = settings.evaluationDate
    settings.evaluationDate = today
    try:
        return float(instrument.NPV())
    finally:
        settings.evaluationDate = previous


def _ql_type(option: str):
    return ql.Option.Call if float(_sign(option)) > 0 else ql.Option.Put


def american_option_price(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0,
                          option: str = "put", steps: int = 800) -> float:
    """7.1 American option by a Cox-Ross-Rubinstein binomial tree (QuantLib)."""
    today, maturity, process = _ql_setup(S, T, r, sigma, q)
    opt = ql.VanillaOption(ql.PlainVanillaPayoff(_ql_type(option), float(K)), ql.AmericanExercise(today, maturity))
    opt.setPricingEngine(ql.BinomialVanillaEngine(process, "crr", int(steps)))
    return _ql_npv(opt, today)


_BARRIERS = {"down_out": ql.Barrier.DownOut, "down_in": ql.Barrier.DownIn,
             "up_out": ql.Barrier.UpOut, "up_in": ql.Barrier.UpIn}


def barrier_option_price(S: float, K: float, barrier: float, T: float, r: float, sigma: float, q: float = 0.0,
                         option: str = "call", barrier_type: str = "down_out", rebate: float = 0.0) -> float:
    """7.1 Continuously monitored single-barrier European option (QuantLib analytic engine)."""
    if barrier_type not in _BARRIERS:
        raise ValueError(f"barrier_type must be one of {sorted(_BARRIERS)}")
    if barrier <= 0:
        raise ValueError("barrier must be positive")
    knocked = (barrier_type.startswith("down") and S <= barrier) or (barrier_type.startswith("up") and S >= barrier)
    if knocked:
        if barrier_type.endswith("out"):
            return float(rebate)
        return float(bsm_price(S, K, T, r, sigma, q, option))
    today, maturity, process = _ql_setup(S, T, r, sigma, q)
    opt = ql.BarrierOption(_BARRIERS[barrier_type], float(barrier), float(rebate),
                           ql.PlainVanillaPayoff(_ql_type(option), float(K)), ql.EuropeanExercise(maturity))
    opt.setPricingEngine(ql.AnalyticBarrierEngine(process))
    return _ql_npv(opt, today)


def digital_price_quantlib(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0,
                           option: str = "call", cash: float = 1.0) -> float:
    """7.1 Cash-or-nothing digital priced by QuantLib (independent cross-check of digital_price)."""
    today, maturity, process = _ql_setup(S, T, r, sigma, q)
    opt = ql.VanillaOption(ql.CashOrNothingPayoff(_ql_type(option), float(K), float(cash)),
                           ql.EuropeanExercise(maturity))
    opt.setPricingEngine(ql.AnalyticEuropeanEngine(process))
    return _ql_npv(opt, today)


# ---------------------------------------------------------------- 7.2 parity
def put_call_parity_residual(C, P, S, K, T, r, q=0.0):
    """7.2 Put-call parity residual: (C - P) - (S e^{-qT} - K e^{-rT}); zero when parity holds."""
    C, P, S, K, T, r, q = (np.asarray(v, dtype=float) for v in (C, P, S, K, T, r, q))
    return _out(C - P - (S * np.exp(-q * T) - K * np.exp(-r * T)), C, P, S, K, T, r, q)


def synthetic_forward(C, P, K, T, r):
    """7.2 Forward implied by a call/put pair at one strike: F = K + (C - P) e^{rT}."""
    C, P, K, T, r = (np.asarray(v, dtype=float) for v in (C, P, K, T, r))
    return _out(K + (C - P) * np.exp(r * T), C, P, K, T, r)


def parity_arbitrage_scan(call_bid, call_ask, put_bid, put_ask, spot_bid, spot_ask, K, T, r, q=0.0,
                          fee_per_leg=0.0) -> dict[str, np.ndarray]:
    """7.2 Conversion / reversal scanner net of bid-ask and fees (three legs each).

    conversion: buy stock, buy put, sell call  -> locks K at expiry
    reversal:   short stock, sell put, buy call -> owes K at expiry
    Profits are present values per unit; `best` is 'conversion', 'reversal' or 'none'."""
    cb, ca, pb, pa, sb, sa, K, T, r, q, fee = np.broadcast_arrays(
        *(np.asarray(v, dtype=float) for v in (call_bid, call_ask, put_bid, put_ask, spot_bid, spot_ask, K, T, r,
                                               q, fee_per_leg)))
    if np.any(ca < cb) or np.any(pa < pb) or np.any(sa < sb):
        raise ValueError("ask must be >= bid for every quote")
    pv_k, carry = K * np.exp(-r * T), np.exp(-q * T)
    conversion = cb - pa - sa * carry + pv_k - 3.0 * fee
    reversal = pb - ca + sb * carry - pv_k - 3.0 * fee
    profit = np.maximum(np.maximum(conversion, reversal), 0.0)
    best = np.where(profit <= 0, "none", np.where(conversion >= reversal, "conversion", "reversal"))
    return {"conversion_profit": conversion, "reversal_profit": reversal, "profit": profit, "best": best}


def box_spread_implied_rate(call_low: float, put_low: float, call_high: float, put_high: float, K_low: float,
                            K_high: float, T: float) -> float:
    """7.2 Rate implied by a box spread: (C1 - P1) - (C2 - P2) = (K2 - K1) e^{-rT}."""
    if not 0 < K_low < K_high or T <= 0:
        raise ValueError("require 0 < K_low < K_high and T > 0")
    cost = (call_low - put_low) - (call_high - put_high)
    if cost <= 0:
        raise ValueError("box cost must be positive; quotes are inconsistent")
    return math.log((K_high - K_low) / cost) / T


# ---------------------------------------------------------------- 7.3 Greeks
def bsm_greeks(S, K, T, r, sigma, q=0.0, option="call") -> dict[str, np.ndarray]:
    """7.3 Analytic Greeks: delta, gamma, vega, theta, rho, vanna (dDelta/dSigma), charm (dDelta/dt).

    Units: per 1.00 of spot, per 1.00 (100 vol points) of sigma, per 1.00 of rate, per year of
    calendar time (theta and charm are the change as time passes)."""
    Sa, Ka, Ta, ra, va, qa, w, shape = _inputs(S, K, T, r, sigma, q, option)
    if np.any(Ta <= 0) or np.any(va <= 0):
        raise ValueError("Greeks need T > 0 and sigma > 0")
    d1, d2 = _d1d2(Sa, Ka, Ta, ra, va, qa)
    sqrt_t, eq, er, pdf = np.sqrt(Ta), np.exp(-qa * Ta), np.exp(-ra * Ta), _phi(d1)
    delta = w * eq * ndtr(w * d1)
    gamma = eq * pdf / (Sa * va * sqrt_t)
    vega = Sa * eq * pdf * sqrt_t
    theta = (-Sa * eq * pdf * va / (2.0 * sqrt_t) - w * ra * Ka * er * ndtr(w * d2)
             + w * qa * Sa * eq * ndtr(w * d1))
    rho = w * Ka * Ta * er * ndtr(w * d2)
    vanna = -eq * pdf * d2 / va
    charm = w * qa * eq * ndtr(w * d1) - eq * pdf * (2.0 * (ra - qa) * Ta - d2 * va * sqrt_t) / (
        2.0 * Ta * va * sqrt_t)
    greeks = {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "rho": rho, "vanna": vanna,
              "charm": charm}
    return {name: value.reshape(shape) for name, value in greeks.items()}


def numerical_greeks(S, K, T, r, sigma, q=0.0, option="call", rel_bump: float = 1e-4) -> dict[str, np.ndarray]:
    """7.3 Bump-and-reprice Greeks (central differences) in the same units as bsm_greeks."""
    Sa, Ka, Ta, ra, va, qa, w, shape = _inputs(S, K, T, r, sigma, q, option)
    hs, hv, ht, hr = rel_bump * Sa, rel_bump, np.minimum(rel_bump, 0.5 * Ta), rel_bump

    def px(s=Sa, t=Ta, rate=ra, vol=va):
        return np.asarray(bsm_price(s, Ka, t, rate, vol, qa, w))

    def delta_at(t=Ta, vol=va):
        return (px(s=Sa + hs, t=t, vol=vol) - px(s=Sa - hs, t=t, vol=vol)) / (2 * hs)

    greeks = {
        "delta": delta_at(),
        "gamma": (px(s=Sa + hs) - 2 * px() + px(s=Sa - hs)) / hs**2,
        "vega": (px(vol=va + hv) - px(vol=va - hv)) / (2 * hv),
        "theta": -(px(t=Ta + ht) - px(t=Ta - ht)) / (2 * ht),
        "rho": (px(rate=ra + hr) - px(rate=ra - hr)) / (2 * hr),
        "vanna": (delta_at(vol=va + hv) - delta_at(vol=va - hv)) / (2 * hv),
        "charm": -(delta_at(t=Ta + ht) - delta_at(t=Ta - ht)) / (2 * ht),
    }
    return {name: np.asarray(value).reshape(shape) for name, value in greeks.items()}


def delta_neutral_hedge(book_delta: float) -> float:
    """7.3 Units of the underlying to trade so the book is delta neutral."""
    return -float(book_delta)


def delta_gamma_neutral_hedge(book_delta: float, book_gamma: float, option_delta: float,
                              option_gamma: float) -> tuple[float, float]:
    """7.3 (hedge-option units, underlying units) making the book delta and gamma neutral."""
    if abs(option_gamma) < 1e-14:
        raise ValueError("hedge option has no gamma; cannot neutralize gamma with it")
    n_opt = -book_gamma / option_gamma
    return float(n_opt), float(-(book_delta + n_opt * option_delta))


# ---------------------------------------------------------------- 7.4 implied volatility
def implied_volatility(price, S, K, T, r, q=0.0, option="call", tol: float = 1e-12,
                       max_iter: int = 200) -> tuple[np.ndarray, np.ndarray]:
    """7.4 Vectorized implied volatility by safeguarded Newton (bisection fallback).

    Returns (iv, valid). Prices at or outside the no-arbitrage bounds give NaN and valid = False."""
    p_in = np.asarray(price, dtype=float)
    Sa, Ka, Ta, ra, _, qa, w, _ = _inputs(S + 0.0 * p_in, K, T, r, 0.2, q, option)
    shape = Sa.shape if p_in.ndim or Sa.size > 1 else ()
    p = np.broadcast_to(p_in, Sa.shape).astype(float)
    fwd, strike = Sa * np.exp(-qa * Ta), Ka * np.exp(-ra * Ta)
    lower = np.maximum(w * (fwd - strike), 0.0)
    upper = np.where(w > 0, fwd, strike)
    slack = 1e-12 * np.maximum(upper, 1.0)
    valid = np.isfinite(p) & (Ta > 0) & (p > lower + slack) & (p < upper - slack)
    iv = np.full(Sa.shape, np.nan)
    if not np.any(valid):
        return iv.reshape(shape), valid.reshape(shape)
    s, k, t, rate, div, sgn, target = (v[valid] for v in (Sa, Ka, Ta, ra, qa, w, p))
    lo, hi = np.full(s.shape, 1e-8), np.full(s.shape, 20.0)
    x = np.clip(np.sqrt(2.0 * math.pi / t) * target / s, 0.05, 3.0)
    for _ in range(max_iter):
        d1, d2 = _d1d2(s, k, t, rate, x, div)
        f = sgn * (s * np.exp(-div * t) * ndtr(sgn * d1) - k * np.exp(-rate * t) * ndtr(sgn * d2)) - target
        hi = np.where(f > 0, x, hi)
        lo = np.where(f <= 0, x, lo)
        if np.all((np.abs(f) < tol) | (hi - lo < 1e-14)):
            break
        vega = s * np.exp(-div * t) * _phi(d1) * np.sqrt(t)
        step = np.divide(f, vega, out=np.full_like(f, np.inf), where=vega > 1e-300)
        newton = x - step
        x = np.where((newton > lo) & (newton < hi), newton, 0.5 * (lo + hi))
    iv[valid] = x
    return iv.reshape(shape), valid.reshape(shape)


def svi_total_variance(k, a: float, b: float, rho: float, m: float, sigma: float):
    """7.4 SVI raw parameterization: w(k) = a + b (rho (k - m) + sqrt((k - m)^2 + sigma^2))."""
    k = np.asarray(k, dtype=float)
    return a + b * (rho * (k - m) + np.sqrt((k - m) ** 2 + sigma**2))


@dataclass(frozen=True)
class SVIParams:
    a: float
    b: float
    rho: float
    m: float
    sigma: float
    rmse: float
    min_variance_ok: bool   # a + b sigma sqrt(1 - rho^2) >= 0


def calibrate_svi(k, total_variance) -> SVIParams:
    """7.4 Least-squares SVI fit to one smile slice (k = log-moneyness, w = sigma_imp^2 T)."""
    k = np.asarray(k, dtype=float).ravel()
    wv = np.asarray(total_variance, dtype=float).ravel()
    if k.size != wv.size or k.size < 5:
        raise ValueError("need at least 5 (k, w) points of equal length")
    if np.any(wv <= 0) or not (np.all(np.isfinite(k)) and np.all(np.isfinite(wv))):
        raise ValueError("total variances must be positive and finite")
    span = max(float(np.ptp(k)), 1e-3)
    lb = [-float(wv.max()), 0.0, -0.999, float(k.min()) - span, 1e-4]
    ub = [float(wv.max()), 10.0, 0.999, float(k.max()) + span, 5.0]

    def resid(p):
        return svi_total_variance(k, *p) - wv

    best = None
    for rho0 in (-0.7, -0.3, 0.0, 0.3):
        for sig0 in (0.05, 0.2, 0.6):
            x0 = [float(wv.min()) * 0.5, float(np.ptp(wv)) / span + 0.05, rho0, float(k[np.argmin(wv)]), sig0]
            x0 = [min(max(v, lo + 1e-9), hi - 1e-9) for v, lo, hi in zip(x0, lb, ub)]
            sol = optimize.least_squares(resid, x0, bounds=(lb, ub), xtol=1e-14, ftol=1e-14, gtol=1e-14)
            if best is None or sol.cost < best.cost:
                best = sol
    a, b, rho, m, sig = (float(v) for v in best.x)
    rmse = float(np.sqrt(np.mean(best.fun**2)))
    return SVIParams(a, b, rho, m, sig, rmse, bool(a + b * sig * math.sqrt(1.0 - rho**2) >= -1e-12))


def svi_butterfly_density(k, params: SVIParams) -> np.ndarray:
    """7.4 Gatheral's g(k); the slice is free of butterfly arbitrage where g(k) >= 0."""
    k = np.asarray(k, dtype=float)
    x = k - params.m
    root = np.sqrt(x**2 + params.sigma**2)
    w = svi_total_variance(k, params.a, params.b, params.rho, params.m, params.sigma)
    w1 = params.b * (params.rho + x / root)
    w2 = params.b * params.sigma**2 / root**3
    if np.any(w <= 0):
        raise ValueError("SVI total variance is non-positive on the grid")
    return (1.0 - k * w1 / (2.0 * w)) ** 2 - 0.25 * w1**2 * (1.0 / w + 0.25) + 0.5 * w2


@dataclass(frozen=True)
class SmileSummary:
    atm_iv: float
    skew: float        # d iv / d log-moneyness at the money
    curvature: float   # d^2 iv / d k^2 at the money


def smile_summary(log_moneyness, iv) -> SmileSummary:
    """7.4 At-the-money level, skew and curvature from a quadratic fit of iv against log-moneyness."""
    k, v = np.asarray(log_moneyness, dtype=float).ravel(), np.asarray(iv, dtype=float).ravel()
    ok = np.isfinite(k) & np.isfinite(v)
    if ok.sum() < 3:
        raise ValueError("need at least 3 finite smile points")
    c2, c1, c0 = np.polyfit(k[ok], v[ok], 2)
    return SmileSummary(float(c0), float(c1), float(2.0 * c2))


def term_structure_summary(maturities, atm_iv, flat_tolerance: float = 1e-3) -> tuple[float, str]:
    """7.4 Slope of ATM vol against maturity and its shape: contango, backwardation or flat."""
    t, v = np.asarray(maturities, dtype=float).ravel(), np.asarray(atm_iv, dtype=float).ravel()
    if t.size != v.size or t.size < 2 or np.ptp(t) == 0:
        raise ValueError("need at least 2 distinct maturities")
    slope = float(np.polyfit(t, v, 1)[0])
    shape = "flat" if abs(slope) <= flat_tolerance else ("contango" if slope > 0 else "backwardation")
    return slope, shape


def iv_rv_signal(implied_vol, realized_vol_forecast, threshold: float = 0.02) -> np.ndarray:
    """7.4 Volatility-spread signal: +1 buy vol when forecast realized exceeds implied by more than
    `threshold`, -1 sell vol when implied exceeds realized by more than it, else 0 (0 on bad input)."""
    if threshold < 0:
        raise ValueError("threshold must be non-negative")
    iv, rv = np.broadcast_arrays(np.asarray(implied_vol, dtype=float), np.asarray(realized_vol_forecast, dtype=float))
    spread = rv - iv
    out = np.where(spread > threshold, 1.0, np.where(spread < -threshold, -1.0, 0.0))
    return np.where(np.isfinite(spread) & (iv > 0) & (rv > 0), out, 0.0)


# ---------------------------------------------------------------- 7.5 gamma scalping
def gamma_scalping_pnl_rate(gamma, S, sigma_realized, sigma_implied):
    """7.5 Delta-hedged P&L per unit time: 0.5 * Gamma * S^2 * (sigma_realized^2 - sigma_implied^2)."""
    g, s, sr, si = (np.asarray(v, dtype=float) for v in (gamma, S, sigma_realized, sigma_implied))
    return _out(0.5 * g * s**2 * (sr**2 - si**2), gamma, S, sigma_realized, sigma_implied)


def simulate_delta_hedged_pnl(path, K: float, T: float, r: float, sigma_implied: float, q: float = 0.0,
                              option: str = "call", quantity: float = 1.0) -> float:
    """7.5 P&L at expiry of buying `quantity` options at the implied-vol price and delta hedging at
    each point of `path` (equally spaced over [0, T]) with the implied-vol delta. Cash earns r."""
    s = np.asarray(path, dtype=float).ravel()
    if s.size < 3 or np.any(s <= 0) or not np.all(np.isfinite(s)):
        raise ValueError("path needs at least 3 positive finite prices")
    if T <= 0 or sigma_implied <= 0:
        raise ValueError("T and sigma_implied must be positive")
    n = s.size - 1
    dt = T / n
    w = float(_sign(option))
    taus = T - dt * np.arange(n)
    deltas = bsm_greeks(s[:-1], K, taus, r, sigma_implied, q, option)["delta"]
    growth, carry = math.exp(r * dt), math.expm1(q * dt)
    cash = -float(bsm_price(s[0], K, T, r, sigma_implied, q, option)) + deltas[0] * s[0]
    for i in range(1, n):
        cash = cash * growth - deltas[i - 1] * s[i] * carry
        cash += (deltas[i] - deltas[i - 1]) * s[i]
    cash = cash * growth - deltas[-1] * s[-1] * carry
    payoff = max(w * (s[-1] - K), 0.0)
    return float(quantity * (payoff - deltas[-1] * s[-1] + cash))


def gamma_scalp_decision(forecast_realized_vol: float, implied_vol: float, cost_vol_points: float = 0.0) -> str:
    """7.5 'long_gamma' when forecast realized vol beats implied by more than costs, 'short_gamma'
    for the reverse, else 'flat' (also 'flat' on unusable inputs)."""
    if cost_vol_points < 0:
        raise ValueError("cost_vol_points must be non-negative")
    if not (np.isfinite(forecast_realized_vol) and np.isfinite(implied_vol)) or min(
            forecast_realized_vol, implied_vol) <= 0:
        return "flat"
    edge = forecast_realized_vol - implied_vol
    if edge > cost_vol_points:
        return "long_gamma"
    return "short_gamma" if -edge > cost_vol_points else "flat"
