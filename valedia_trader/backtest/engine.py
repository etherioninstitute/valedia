"""Zero-look-ahead portfolio backtest engine.

Timeline (daily bars): target weights decided with data through the close of day t take effect at
the close of day t + lag (lag >= 1) and first earn the return of day t + lag + 1. Costs are charged
on the day a trade happens: commission and spread per unit traded, square-root market impact
(formula 9.3) from participation in trailing dollar volume, borrow on shorts and carry on perps.
The cost model must be complete (formula 10.1) or the engine refuses to run.

A bar is priced for a symbol when its price is finite and above zero. Before a symbol's first priced bar any cell
is ignored; after it, NaN is the only legal "no price" and a zero, negative or infinite price is refused. On an
unpriced bar the engine neither buys nor sells the symbol: the weight carried from the bar before stays in effect
(drifted by the book's return), adds no trading cost and still pays borrow and funding; the return booked when the
price comes back is the move since the last priced bar. A position still open on an unpriced last bar is reported
in `stranded`, not valued. While a symbol is carried, the book's total weight can exceed the total of the targets:
the reported weights show it and nothing is scaled back. When the book's own return on a bar is -100% or worse,
every weight, carried ones included, becomes zero as before."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
import pandas as pd

from valedia_trader.quant_engine import level8_portfolio_risk as risk
from valedia_trader.quant_engine import level10_ml_research as research

__all__ = ["CostModel", "BacktestResult", "run_backtest", "performance_report", "buy_and_hold"]


@dataclass(frozen=True)
class CostModel:
    fees_bps: Mapping[str, float] | float | None          # commission per side, bps of traded notional
    slippage_bps: Mapping[str, float] | float | None      # half-spread per side, bps
    borrow_annual: float | None                           # annual rate on short notional
    funding_annual: Mapping[str, float] | float | None    # annual carry on gross notional (perps); 0 for spot
    impact_y: float = 1.0                                 # square-root law constant
    extra: dict = field(default_factory=dict)

    def as_config(self) -> dict:
        return {"fees": self.fees_bps, "slippage": self.slippage_bps, "borrow": self.borrow_annual,
                "funding": self.funding_annual}

    def _per_asset(self, value, columns: pd.Index, name: str) -> np.ndarray:
        if isinstance(value, Mapping):
            missing = [c for c in columns if c not in value]
            if missing:
                raise research.CostModelError(f"{name} missing for: {', '.join(map(str, missing))}")
            out = np.array([float(value[c]) for c in columns])
        else:
            out = np.full(len(columns), float(value))
        if not np.all(np.isfinite(out)):
            raise research.CostModelError(f"{name} contains non-finite values")
        return out


@dataclass(frozen=True)
class BacktestResult:
    net: pd.Series          # daily net return on equity
    gross: pd.Series
    costs: pd.DataFrame     # columns: commission, slippage, impact, borrow, funding (fractions of equity)
    weights: pd.DataFrame   # weights in effect after each close
    turnover: pd.Series     # sum |traded weight| per day
    stranded: dict = field(default_factory=dict)   # symbol -> weight still held on an unpriced last bar

    @property
    def equity(self) -> pd.Series:
        return (1.0 + self.net).cumprod()


def run_backtest(prices: pd.DataFrame, decided_weights: pd.DataFrame, cost: CostModel,
                 dollar_volume: pd.DataFrame | None = None, capital: float = 1_000_000.0, execution_lag: int = 1,
                 vol_window: int = 20, periods_per_year: float = 252.0) -> BacktestResult:
    """Run the backtest. `decided_weights.loc[t]` must depend only on prices up to and including t
    (verify with quant_engine.level10_ml_research.detect_lookahead); the engine adds the execution lag."""
    research.require_complete_costs(cost.as_config())
    if execution_lag < 1:
        raise ValueError("execution_lag must be >= 1: same-bar execution is look-ahead")
    if not prices.index.equals(decided_weights.index) or list(prices.columns) != list(decided_weights.columns):
        raise ValueError("prices and decided_weights must share index and columns")
    if not prices.index.is_monotonic_increasing or prices.index.has_duplicates:
        raise ValueError("price index must be strictly increasing")
    if capital <= 0:
        raise ValueError("capital must be positive")
    cols = prices.columns
    px = prices.to_numpy(dtype=float)
    tradable = np.isfinite(px) & (px > 0)
    bad = np.maximum.accumulate(tradable, axis=0) & ~tradable & ~np.isnan(px)     # after listing only NaN is legal
    if bad.any():
        t, j = np.argwhere(bad)[0]
        raise ValueError(f"price of {cols[j]!r} on {prices.index[t]} is {px[t, j]} after its first price: "
                         "mark a missing price as NaN, or model the delisting (an explicit exit and its loss)")
    step = np.zeros_like(px)                                                 # bar-to-bar move: the impact
    step[1:] = px[1:] / px[:-1] - 1.0                                        # model's volatility input, as before
    step[~np.isfinite(step)] = 0.0
    last = pd.DataFrame(np.where(tradable, px, np.nan)).ffill().shift(1).to_numpy(dtype=float)
    booked = tradable & np.isfinite(last)                                    # priced, with a priced bar before
    with np.errstate(divide="ignore", invalid="ignore"):
        ret = np.where(booked, px / last - 1.0, 0.0)                         # move since the last priced bar
    decided = decided_weights.to_numpy(dtype=float)
    decided = np.where(np.isfinite(decided), decided, 0.0)
    held = np.zeros_like(decided)                                            # target; becomes the weight in effect
    held[execution_lag:] = decided[:-execution_lag]

    fee = cost._per_asset(cost.fees_bps, cols, "fees") * 1e-4
    slip = cost._per_asset(cost.slippage_bps, cols, "slippage") * 1e-4
    fund = cost._per_asset(cost.funding_annual, cols, "funding") / periods_per_year
    borrow = float(cost.borrow_annual) / periods_per_year

    ret_df = pd.DataFrame(step, index=prices.index, columns=cols)
    sigma = ret_df.rolling(vol_window).std().shift(1).to_numpy()            # known before the trade
    adv = None
    if dollar_volume is not None:
        adv = dollar_volume.reindex(index=prices.index, columns=cols).rolling(vol_window).mean().shift(1).to_numpy()

    n = len(prices)
    gross, turnover = np.zeros(n), np.zeros(n)
    c_comm, c_slip, c_imp, c_bor, c_fund = (np.zeros(n) for _ in range(5))
    equity = capital
    prev = np.zeros(len(cols))
    for t in range(n):
        g = float(prev @ ret[t])
        gross[t] = g
        drifted = prev * (1.0 + ret[t]) / (1.0 + g) if g > -1.0 else np.zeros_like(prev)
        held[t] = np.where(tradable[t], held[t], drifted)                   # no price, no trade: carry the weight
        trade = np.abs(held[t] - drifted)
        turnover[t] = trade.sum()
        c_comm[t], c_slip[t] = float(trade @ fee), float(trade @ slip)
        if adv is not None and turnover[t] > 0:
            part = np.divide(trade * equity, adv[t], out=np.zeros_like(trade), where=np.isfinite(adv[t]) & (adv[t] > 0))
            sig = np.where(np.isfinite(sigma[t]), sigma[t], 0.0)
            c_imp[t] = float(np.sum(trade * cost.impact_y * sig * np.sqrt(part)))
        c_bor[t] = float(np.maximum(-prev, 0.0).sum() * borrow)
        c_fund[t] = float(np.abs(prev) @ fund)
        net_t = g - (c_comm[t] + c_slip[t] + c_imp[t] + c_bor[t] + c_fund[t])
        equity *= max(1.0 + net_t, 1e-12)
        prev = held[t]
    costs = pd.DataFrame({"commission": c_comm, "slippage": c_slip, "impact": c_imp, "borrow": c_bor,
                          "funding": c_fund}, index=prices.index)
    gross_s = pd.Series(gross, index=prices.index, name="gross")
    stranded = {cols[j]: float(held[-1, j]) for j in range(len(cols)) if not tradable[-1, j] and held[-1, j] != 0.0} \
        if n else {}
    return BacktestResult((gross_s - costs.sum(axis=1)).rename("net"), gross_s, costs,
                          pd.DataFrame(held, index=prices.index, columns=cols),
                          pd.Series(turnover, index=prices.index, name="turnover"), stranded)


def buy_and_hold(prices: pd.DataFrame, weights: Mapping[str, float] | None = None) -> pd.Series:
    """Daily returns of a buy-and-hold basket (default: one asset or equal weight, rebalanced daily)."""
    r = prices.pct_change().fillna(0.0)
    if weights is None:
        live = prices.notna()
        w = live.div(live.sum(axis=1).replace(0, np.nan), axis=0).shift(1).fillna(0.0)
        return (w * r).sum(axis=1)
    w = pd.Series(weights, dtype=float)
    return (r[w.index] * w).sum(axis=1)


def performance_report(net: pd.Series, n_trials: int | None = None, sharpe_variance: float | None = None,
                       turnover: pd.Series | None = None, costs: pd.DataFrame | None = None,
                       periods_per_year: float = 252.0) -> dict:
    """Net performance statistics: return, Sharpe, t-stat of the Sharpe (2.2), Deflated Sharpe (10.3,
    when the trial count and variance of trial Sharpes are supplied), drawdown, win rate, profit factor
    and realized cost in bps of traded notional."""
    r = net.dropna().to_numpy(dtype=float)
    if r.size < 30:
        raise ValueError("need at least 30 daily returns")
    years = r.size / periods_per_year
    total = float(np.prod(1.0 + r) - 1.0)
    sd = r.std(ddof=1)
    sr_period = float(r.mean() / sd) if sd > 1e-15 else 0.0
    sr_annual = sr_period * math.sqrt(periods_per_year)
    wins, losses = r[r > 0], r[r < 0]
    out = {
        "start": str(net.dropna().index[0].date()), "end": str(net.dropna().index[-1].date()), "years": years,
        "total_return": total, "cagr": (1.0 + total) ** (1.0 / years) - 1.0 if total > -1 else -1.0,
        "annual_vol": float(sd * math.sqrt(periods_per_year)), "sharpe": sr_annual,
        "sharpe_t_stat": sr_annual * math.sqrt(years),
        "max_drawdown": risk.max_drawdown(np.clip(r, -0.999999, None)).depth,
        "win_rate": float((r > 0).mean()),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.size and losses.sum() < 0 else math.inf,
        "skew": float(pd.Series(r).skew()), "kurtosis": float(pd.Series(r).kurt() + 3.0),
    }
    if n_trials is not None and sharpe_variance is not None and n_trials >= 2 and sd > 1e-15:
        out["n_trials"] = int(n_trials)
        out["deflated_sharpe"] = research.deflated_sharpe_ratio(
            sr_period, r.size, n_trials, sharpe_variance, out["skew"], out["kurtosis"])
    if turnover is not None and costs is not None:
        traded = float(turnover.sum())
        trade_costs = float(costs[["commission", "slippage", "impact"]].to_numpy().sum())
        out["annual_turnover"] = traded / years
        out["cost_bps_of_traded"] = trade_costs / traded * 1e4 if traded > 0 else 0.0
        out["impact_bps_of_traded"] = float(costs["impact"].sum()) / traded * 1e4 if traded > 0 else 0.0
        out["annual_cost_drag"] = float(costs.to_numpy().sum()) / years
    return out
