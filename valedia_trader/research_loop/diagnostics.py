"""What the quant sheet says about one return stream.

Every backtest report carries these numbers, and the panel and the weekly critic read the same ones, so
each part of the trader judges a strategy from one set of facts. Nothing here is a gate: the pass/fail
checks stay in the gauntlet, fixed before the test. These answer the questions a gate cannot: is the
return just market beta, how uncertain is the mean, how fat are the tails, what does the volatility model
expect next, and how likely is the drawdown limit to be hit before a profit target.

Each section names the sheet formula it applies ("2.1" .. "10.4"). A section that cannot be computed
reports why instead of failing the report.
"""
from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np
import pandas as pd

from valedia_trader.quant_engine import level1_probability as l1
from valedia_trader.quant_engine import level2_statistics as l2
from valedia_trader.quant_engine import level3_linalg as l3
from valedia_trader.quant_engine import level4_optimization as l4
from valedia_trader.quant_engine import level5_timeseries as l5
from valedia_trader.quant_engine import level6_stochastic as l6
from valedia_trader.quant_engine import level8_portfolio_risk as l8
from valedia_trader.quant_engine import level10_ml_research as l10

__all__ = ["diagnose", "order_facts", "SECTIONS"]

TRADING_DAYS = 252.0
MIN_OBS = 60


def _f(x: Any) -> float | None:
    """A JSON-safe float: None for missing, infinite or non-numeric values."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _uncertainty(r: np.ndarray, **_: Any) -> dict:
    est = l2.mean_confidence_interval(r, 0.95)
    years = len(r) / TRADING_DAYS
    sd = float(np.std(r, ddof=1))
    sharpe_annual = float(np.mean(r) / sd * math.sqrt(TRADING_DAYS)) if sd > 0 else float("nan")
    return {"formula": "2.1 and 2.2", "mean_daily": _f(est.mean), "std_error": _f(est.std_err), "ci95_daily": [_f(est.ci_low), _f(est.ci_high)],
            "sharpe_annual": _f(sharpe_annual), "sharpe_t_stat": _f(l2.sharpe_t_stat_from_returns(r)), "years": _f(years),
            "years_needed_for_t_of_2_at_this_sharpe": _f((2.0 / sharpe_annual) ** 2) if sharpe_annual and math.isfinite(sharpe_annual) and sharpe_annual != 0 else None,
            "ci95_annual": [_f(est.ci_low * TRADING_DAYS), _f(est.ci_high * TRADING_DAYS)], "p_value_mean_zero": _f(est.p_value),
            "reading": "the 95% range for the average daily return; a range that includes zero means the edge is not established"}


def _win_rate(r: np.ndarray, **_: Any) -> dict:
    active = r[r != 0.0]
    wins, losses = int((active > 0).sum()), int((active < 0).sum())
    post = l1.beta_binomial_update(1.0, 1.0, wins, losses)
    return {"formula": "1.1", "active_days": int(active.size), "wins": wins, "losses": losses, "posterior_mean": _f(post.mean),
            "credible95": [_f(post.ci_low), _f(post.ci_high)],
            "reading": "win rate on days with a position, updated from an even prior; the range is what the data supports"}


def _tails(r: np.ndarray, **_: Any) -> dict:
    fit = l1.fit_student_t(r[r != 0.0] if (r != 0.0).sum() >= MIN_OBS else r)
    return {"formula": "1.5", "student_t_df": _f(fit.df), "tail_ratio_vs_normal_3sd": _f(fit.tail_ratio), "normal_mass_within_3sd": _f(l1.normal_mass_within(3.0)),
            "reading": "degrees of freedom under about 5 mean fat tails: 3-sigma days come far more often than a normal curve says"}


def _regimes(r: np.ndarray, benchmark: np.ndarray | None = None, **_: Any) -> dict:
    if benchmark is None:
        raise ValueError("needs a benchmark")
    level = np.cumprod(1.0 + benchmark)
    trend = pd.Series(level).rolling(200, min_periods=100).mean().to_numpy()
    up = np.r_[False, (level > trend)[:-1]]                      # yesterday's state, so today's return is out of sample
    known = np.r_[False, np.isfinite(trend)[:-1]]
    means, variances, probs, rows = [], [], [], {}
    for label, mask in (("market_above_200d", up & known), ("market_below_200d", ~up & known)):
        x = r[mask]
        if x.size < 20:
            continue
        means.append(float(x.mean())); variances.append(float(x.var(ddof=1))); probs.append(x.size)
        rows[label] = {"days": int(x.size), "mean_daily": _f(x.mean()), "annual_vol": _f(x.std(ddof=1) * math.sqrt(TRADING_DAYS))}
    if len(means) < 2:
        raise ValueError("one market state has too few days")
    p = np.array(probs, float) / sum(probs)
    tv = l1.total_variance(means, variances, p)
    return {"formula": "1.4", **rows, "mean_from_regimes": _f(l1.total_expectation(means, p)),
            "variance_within_regimes_share": _f(tv.within / tv.total) if tv.total else None,
            "reading": "where the return comes from by market state; a strategy that earns only in one state is a bet on that state"}


def _market_exposure(r: np.ndarray, benchmark: np.ndarray | None = None, **_: Any) -> dict:
    if benchmark is None:
        raise ValueError("needs a benchmark")
    capm = l8.capm_beta(r, benchmark)
    fit = l2.ols(benchmark, r)
    hedged = l8.beta_hedged_returns(r, benchmark, capm.beta)
    sd = float(np.std(hedged, ddof=1))
    return {"formula": "8.1 and 2.3", "beta": _f(capm.beta), "alpha_annual": _f(capm.alpha * TRADING_DAYS), "alpha_t_stat": _f(capm.alpha_t),
            "r_squared": _f(fit.r_squared), "sharpe_after_removing_beta": _f(np.mean(hedged) / sd * math.sqrt(TRADING_DAYS)) if sd > 0 else None,
            "reading": "how much of the return is the market itself; alpha with a t-statistic under 2 is not distinguishable from zero"}


def _factors(r: np.ndarray, factor_returns: pd.DataFrame | None = None, **_: Any) -> dict:
    if factor_returns is None or factor_returns.shape[1] < 2:
        raise ValueError("needs at least two factor series")
    model = l8.factor_regression(r, factor_returns.to_numpy())
    betas = np.ravel(model.betas)
    return {"formula": "8.2", "factors": list(factor_returns.columns), "betas": {c: _f(b) for c, b in zip(factor_returns.columns, betas)},
            "alpha_annual": _f(np.ravel(model.alpha)[0] * TRADING_DAYS), "r_squared": _f(np.ravel(model.r_squared)[0]),
            "reading": "exposure to each tradable factor proxy; a high R-squared means the strategy can be replicated by holding them"}


def _multiple_testing(r: np.ndarray, variation_pvalues: list[float] | None = None, **_: Any) -> dict:
    if not variation_pvalues:
        raise ValueError("needs the p-value of each variation tried")
    p = np.asarray(variation_pvalues, float); p = p[np.isfinite(p)]
    holm = l2.holm_bonferroni(p, 0.05)
    return {"formula": "2.5", "variations": int(p.size), "best_p_raw": _f(p.min()), "best_p_holm": _f(np.min(holm.pvalues_adjusted)),
            "chance_of_a_false_win_at_5pct": _f(l2.family_wise_error_rate(0.05, p.size)), "survive_holm": int(np.sum(holm.reject)),
            "reading": "with this many tries, the chance that at least one looks significant by luck alone"}


def _serial(r: np.ndarray, **_: Any) -> dict:
    q, p = l5.ljung_box(r, 10)
    dep = l5.classify_serial_dependence(r)
    stat = l5.stationarity_verdict(r)
    return {"formula": "5.2 and 5.3", "ljung_box_p": _f(p), "serial_dependence": dep.label, "lag": int(dep.lag) if dep.lag is not None else None,
            "rho": _f(dep.rho), "stationary": bool(stat.is_stationary), "adf_p": _f(stat.adf_p), "kpss_p": _f(stat.kpss_p),
            "reading": "whether yesterday's result predicts today's, and whether the return series is stable enough to average at all"}


def _volatility(r: np.ndarray, target_vol: float = 0.10, **_: Any) -> dict:
    fit = l5.fit_garch(r * 100.0, horizon=5)
    forecast = float(np.sqrt(np.mean(np.ravel(fit.forecast_variance)))) / 100.0
    annual = float(l5.annualize_volatility(forecast))
    realized = float(np.std(r, ddof=1) * math.sqrt(TRADING_DAYS))
    sq = r * r

    def qlike(theta: np.ndarray) -> float:                                   # one-step variance forecast loss for an EWMA with decay theta[0]
        lam, var, total = float(theta[0]), float(sq[:20].mean()) or 1e-12, 0.0
        for x in sq[20:]:
            total += math.log(var) + x / var
            var = lam * var + (1.0 - lam) * x
        return total / max(1, len(sq) - 20)

    ewma = l4.minimize_loss(qlike, [0.94], bounds=[(0.80, 0.995)])
    return {"formula": "5.5, 5.1 and 4.2", "garch_persistence": _f(fit.persistence), "forecast_annual_vol_next_5d": _f(annual), "realized_annual_vol": _f(realized),
            "scale_to_target_vol": _f(l5.vol_target_scale(annual, target_vol, 1.0)), "target_vol": target_vol,
            "ewma_decay_fit": _f(np.ravel(ewma.theta)[0]),
            "reading": "the volatility the model expects over the next week, and how much to scale the book to run at the target"}


def _growth(r: np.ndarray, **_: Any) -> dict:
    vol = float(np.std(r, ddof=1) * math.sqrt(TRADING_DAYS))
    geo = float(l4.geometric_mean_return(r))
    return {"formula": "4.5", "arithmetic_annual": _f(np.mean(r) * TRADING_DAYS), "geometric_annual": _f((1.0 + geo) ** TRADING_DAYS - 1.0),
            "volatility_drag_annual": _f(l4.volatility_drag(vol)),
            "reading": "what compounds is the geometric return: the average return minus about half the variance"}


def _scaling(r: np.ndarray, **_: Any) -> dict:
    daily = float(np.std(r, ddof=1))
    out = {"formula": "6.1"}
    for n in (5, 21):
        k = (len(r) // n) * n
        block = np.log1p(r[-k:]).reshape(-1, n).sum(axis=1)
        out[f"vol_{n}d_vs_sqrt_time"] = _f(np.std(block, ddof=1) / l6.random_walk_sd(n, daily)) if daily > 0 and block.size > 3 else None
    out["reading"] = "1.0 means risk grows with the square root of time; above 1 returns trend, below 1 they revert"
    return out


def _ruin(r: np.ndarray, drawdown_limit: float = 0.08, profit_target: float = 0.08, **_: Any) -> dict:
    mu, sigma = float(np.mean(r) * TRADING_DAYS), float(np.std(r, ddof=1) * math.sqrt(TRADING_DAYS))
    p = l6.gbm_take_profit_before_stop_probability(1.0, 1.0 + profit_target, 1.0 - drawdown_limit, mu, sigma)
    paths = l6.gbm_paths(1.0, mu, sigma, 0.25, 63, 4000, np.random.default_rng(7))
    worst = 1.0 - (paths / np.maximum.accumulate(paths, axis=1)).min(axis=1)
    return {"formula": "6.5 and 6.3", "drawdown_limit": drawdown_limit, "profit_target": profit_target, "p_target_before_limit": _f(p),
            "p_breach_limit_next_63d": _f(np.mean(worst >= drawdown_limit)), "median_worst_drawdown_63d": _f(np.median(worst)),
            "reading": "if returns keep this mean and volatility: the chance of reaching the profit target before the drawdown limit stops the book"}


def _portfolio(r: np.ndarray, asset_returns: pd.DataFrame | None = None, weights: pd.DataFrame | None = None,
               drawdown_limit: float = 0.08, **_: Any) -> dict:
    if asset_returns is None or weights is None:
        raise ValueError("needs asset returns and weights")
    held = [c for c in weights.columns if c in asset_returns.columns and float(weights[c].abs().sum()) > 0]
    if len(held) < 2:
        raise ValueError("fewer than two assets were ever held")
    x = asset_returns[held].dropna()
    if len(x) < MIN_OBS:
        raise ValueError("too little overlapping history")
    sample = l3.covariance_matrix(x.to_numpy())
    shrunk = l3.ledoit_wolf_shrinkage(x.to_numpy())
    w = weights[held].iloc[-1].to_numpy(float)
    mean_w = weights[held].abs().mean().to_numpy(float)
    pcs = l3.pca(x.to_numpy(), n_components=min(3, len(held)))
    ones = np.ones(len(held))
    try:                                                                   # 4.3: minimum variance with weights summing to one
        min_var = np.ravel(l4.solve_equality_constrained_quadratic(shrunk.covariance, np.zeros(len(held)), ones[None, :], np.array([1.0])).x)
    except Exception:
        min_var = np.full(len(held), np.nan)
    try:                                                                   # 4.4: mean-variance weights from the sample means, scaled to gross 1
        mvw = np.ravel(l4.markowitz_closed_form(x.mean().to_numpy() * TRADING_DAYS, shrunk.covariance * TRADING_DAYS, 3.0))
        mvw = mvw / np.abs(mvw).sum() if np.abs(mvw).sum() > 0 else mvw
    except Exception:
        mvw = np.full(len(held), np.nan)
    try:                                                                   # 6.2: the current book on correlated random paths, no drift assumed
        vol = np.sqrt(np.diag(shrunk.covariance)); corr = shrunk.covariance / np.outer(vol, vol)
        dw = np.asarray(l6.correlated_brownian_increments(corr, 2000, 63, 1.0, np.random.default_rng(11)))
        if dw.shape[-1] != len(held):
            dw = np.moveaxis(dw, 1, -1)
        equity = np.cumprod(1.0 + (dw * (w * vol)).sum(axis=-1), axis=1)
        worst = 1.0 - (equity / np.maximum.accumulate(equity, axis=1)).min(axis=1)
        book_breach = _f(np.mean(worst >= drawdown_limit))
    except Exception:
        book_breach = None
    contrib = l1.expected_pnl_contributions(mean_w, x.mean().to_numpy() * TRADING_DAYS)        # 1.2: the book's expected return, asset by asset
    return {"formula": "1.2, 3.1, 3.2, 3.3, 3.4, 3.5, 4.3, 4.4 and 6.2", "assets_held": held,
            "expected_annual_return_by_asset": {a: _f(v) for a, v in zip(held, np.ravel(contrib))},
            "expected_annual_return_of_average_book": _f(l1.expected_pnl(mean_w, x.mean().to_numpy() * TRADING_DAYS)),
            "mean_variance_weights": {a: _f(v) for a, v in zip(held, mvw)}, "p_current_book_breaches_limit_63d_no_drift": book_breach,
            "condition_number_sample": _f(l3.condition_number(sample)), "condition_number_shrunk": _f(l3.condition_number(shrunk.covariance)),
            "shrinkage": _f(shrunk.delta), "first_component_share": _f(pcs.explained_variance_ratio[0]),
            "current_book_annual_vol": _f(l3.portfolio_volatility(w, shrunk.covariance) * math.sqrt(TRADING_DAYS)),
            "average_book_annual_vol": _f(l3.portfolio_volatility(mean_w, shrunk.covariance) * math.sqrt(TRADING_DAYS)),
            "minimum_variance_weights": {a: _f(v) for a, v in zip(held, min_var)},
            "reading": "how concentrated the book's risk is; a first component near 1 means the assets move as one and diversification is nominal"}


def _pairs(r: np.ndarray, asset_returns: pd.DataFrame | None = None, weights: pd.DataFrame | None = None, **_: Any) -> dict:
    if asset_returns is None or weights is None:
        raise ValueError("needs asset returns and weights")
    held = [c for c in weights.columns if c in asset_returns.columns and float(weights[c].abs().sum()) > 0]
    x = asset_returns[held].dropna()
    if len(held) < 2 or len(x) < 250:
        raise ValueError("needs two held assets with a year of overlapping history")
    corr = np.array(x.corr().to_numpy(), dtype=float, copy=True); np.fill_diagonal(corr, -np.inf)
    i, j = np.unravel_index(np.argmax(corr), corr.shape)
    a, b = np.log1p(x.iloc[:, i]).cumsum().to_numpy(), np.log1p(x.iloc[:, j]).cumsum().to_numpy()
    res = l5.engle_granger(a, b)
    out = {"formula": "5.4", "pair": [held[i], held[j]], "correlation": _f(corr[i, j]), "cointegrated": bool(res.cointegrated),
           "p_value": _f(res.p_value), "hedge_ratio": _f(res.beta)}
    try:
        out["half_life_days"] = _f(l5.fit_ou(np.asarray(res.spread, float)).half_life)
    except Exception:
        out["half_life_days"] = None
    out["reading"] = "whether the two most correlated holdings are tied together; if so, holding both is one bet and their gap tends to close"
    return out


def _signal_quality(r: np.ndarray, asset_returns: pd.DataFrame | None = None, weights: pd.DataFrame | None = None, **_: Any) -> dict:
    if asset_returns is None or weights is None:
        raise ValueError("needs asset returns and weights")
    cols = [c for c in weights.columns if c in asset_returns.columns]
    held = weights[cols].shift(1).reindex(asset_returns.index).fillna(0.0).to_numpy() > 0       # yesterday's long positions
    raw = asset_returns[cols].to_numpy(float)
    traded = np.isfinite(raw) & (raw != 0.0)                                                    # days the asset actually traded
    up = raw > 0
    precision, recall, f1 = l10.precision_recall_f1(up[traded], held[traded])
    return {"formula": "10.4", "precision": _f(precision), "recall": _f(recall), "f1": _f(f1), "base_rate_up": _f(up[traded].mean()),
            "reading": "of the asset-days it was long, the share that went up, against the share of all asset-days that went up"}


def _stability(r: np.ndarray, **_: Any) -> dict:
    n = len(r); test = max(63, n // 6)
    sharpes = []
    for _, idx in l10.walk_forward_splits(n, train_size=test, test_size=test, step=test):
        x = r[idx]; sd = float(np.std(x, ddof=1))
        if sd > 0:
            sharpes.append(float(np.mean(x) / sd * math.sqrt(TRADING_DAYS)))
    if len(sharpes) < 2:
        raise ValueError("too few blocks")
    return {"formula": "10.2", "blocks": len(sharpes), "block_sharpes": [round(s, 2) for s in sharpes], "share_positive": _f(np.mean(np.array(sharpes) > 0)),
            "worst_block_sharpe": _f(min(sharpes)), "reading": "the same strategy on consecutive stretches of time it was not chosen on"}


def _martingale(r: np.ndarray, **_: Any) -> dict:
    chk = l6.martingale_check(r)
    return {"formula": "6.5", "mean_increment": _f(chk.mean_increment), "p_value": _f(chk.p_value), "looks_like_a_fair_game": bool(chk.is_martingale),
            "reading": "a fair game has no edge; failing to reject it means the returns are consistent with luck"}


SECTIONS: dict[str, Callable[..., dict]] = {
    "uncertainty_of_the_mean": _uncertainty, "win_rate": _win_rate, "tails": _tails, "by_market_state": _regimes,
    "market_exposure": _market_exposure, "factor_exposure": _factors, "multiple_testing": _multiple_testing,
    "serial_dependence": _serial, "volatility_forecast": _volatility, "compounding": _growth, "time_scaling": _scaling,
    "ruin_and_target": _ruin, "book_risk": _portfolio, "pairs_check": _pairs, "signal_quality": _signal_quality, "stability": _stability,
    "fair_game_test": _martingale,
}


def diagnose(returns: pd.Series, benchmark: pd.Series | None = None, asset_returns: pd.DataFrame | None = None,
             weights: pd.DataFrame | None = None, variation_pvalues: list[float] | None = None,
             drawdown_limit: float = 0.08, profit_target: float = 0.08, target_vol: float = 0.10,
             factor_columns: tuple[str, ...] = ("SPY", "TLT", "GLD", "BTC-USD")) -> dict:
    """Every section of the sheet that applies to one daily return series. Never raises."""
    r = pd.Series(returns).dropna()
    if len(r) < MIN_OBS:
        return {"error": f"only {len(r)} daily returns; {MIN_OBS} are needed"}
    bench = None if benchmark is None else pd.Series(benchmark).reindex(r.index).fillna(0.0).to_numpy(float)
    assets = None if asset_returns is None else asset_returns.reindex(r.index)
    w = None if weights is None else weights.reindex(r.index).fillna(0.0)
    factors = None
    if assets is not None:
        cols = [c for c in factor_columns if c in assets.columns]
        factors = assets[cols].fillna(0.0) if len(cols) >= 2 else None
    ctx = dict(benchmark=bench, asset_returns=assets, weights=w, factor_returns=factors, variation_pvalues=variation_pvalues,
               drawdown_limit=drawdown_limit, profit_target=profit_target, target_vol=target_vol)
    out: dict[str, Any] = {"observations": int(len(r)), "start": str(r.index[0])[:10], "end": str(r.index[-1])[:10]}
    for name, fn in SECTIONS.items():
        try:
            out[name] = fn(r.to_numpy(float), **ctx)
        except Exception as exc:                                             # a diagnostic must never break a report
            out[name] = {"unavailable": f"{type(exc).__name__}: {str(exc)[:120]}"}
    return out


def order_facts(prices: pd.DataFrame, symbol: str, benchmark: str = "SPY", lookback: int = 252) -> dict:
    """The facts every panel member is shown about one asset, computed here so they all judge the same numbers."""
    if symbol not in prices.columns:
        raise KeyError(f"no price history for {symbol}")
    px = prices[symbol].dropna()
    if len(px) < MIN_OBS + 1:
        raise ValueError(f"only {len(px)} prices for {symbol}")
    r = pd.Series(l5.log_returns(px.to_numpy()), index=px.index[1:]).iloc[-lookback:]
    facts: dict[str, Any] = {"symbol": symbol, "as_of": str(px.index[-1])[:10], "data_age_days": int((pd.Timestamp.now().normalize() - px.index[-1].normalize()).days)}
    facts["momentum_20d_pct"] = _f((px.iloc[-1] / px.iloc[-21] - 1.0) * 100.0) if len(px) > 21 else None
    facts["momentum_120d_pct"] = _f((px.iloc[-1] / px.iloc[-121] - 1.0) * 100.0) if len(px) > 121 else None
    facts["above_200d_average"] = bool(px.iloc[-1] > px.iloc[-200:].mean()) if len(px) >= 200 else None
    facts["vol_60d_annual_pct"] = _f(r.iloc[-60:].std(ddof=1) * math.sqrt(TRADING_DAYS) * 100.0)
    level = px.iloc[-lookback:]
    facts["drawdown_from_1y_high_pct"] = _f((1.0 - level.iloc[-1] / level.max()) * 100.0)
    try:
        facts["var_95_1d_pct"] = _f(l8.var_historical(r.to_numpy(), 0.95) * 100.0)
        facts["expected_shortfall_95_1d_pct"] = _f(l8.es_historical(r.to_numpy(), 0.95) * 100.0)
    except Exception:
        facts["var_95_1d_pct"] = facts["expected_shortfall_95_1d_pct"] = None
    try:
        fit = l5.fit_garch(r.to_numpy() * 100.0, horizon=5)
        facts["vol_forecast_5d_annual_pct"] = _f(l5.annualize_volatility(float(np.sqrt(np.mean(np.ravel(fit.forecast_variance)))) / 100.0) * 100.0)
    except Exception:
        facts["vol_forecast_5d_annual_pct"] = None
    if benchmark in prices.columns and benchmark != symbol:
        b = pd.Series(l5.log_returns(prices[benchmark].dropna().to_numpy()), index=prices[benchmark].dropna().index[1:]).reindex(r.index).dropna()
        if len(b) >= MIN_OBS:
            capm = l8.capm_beta(r.reindex(b.index).to_numpy(), b.to_numpy())
            facts["beta_to_" + benchmark] = _f(capm.beta)
    est = l2.mean_confidence_interval(r.to_numpy(), 0.95)
    facts["mean_daily_return_distinguishable_from_zero"] = bool(est.ci_low > 0 or est.ci_high < 0)
    return facts
