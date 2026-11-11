"""Walk-forward evaluation of the sleeve + Darwinian committee stack on cached daily data.

Protocol
* Sleeve hyperparameters start from defaults fixed before any evaluation.
* Selection period A (to 2019-12-31) is the only data the autoresearch loop may use; holdout
  period B (2020-01-01 onward) is never used for any choice.
* Every configuration evaluated is recorded in the trial registry, so the Deflated Sharpe ratio
  uses the true number of trials. The registry file persists across runs on purpose.
* Fees come from the venue registry (point-in-time). Spread and borrow are stated assumptions.

Run: python -m valedia_trader.backtest.walkforward [--data DIR] [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from valedia_trader.agents import autoresearch as ar
from valedia_trader.agents import darwinian as dw
from valedia_trader.agents import specialists as sp
from valedia_trader.backtest.engine import BacktestResult, CostModel, buy_and_hold, performance_report, run_backtest
from valedia_trader.data_and_fees.venue_registry import VenueRegistry
from valedia_trader.execution_and_risk.buffering import apply_buffer
from valedia_trader.quant_engine import level10_ml_research as research

CRYPTO = ("BTC-USD", "ETH-USD")
SPLIT = "2020-01-01"
START = "2013-01-02"
# Stated assumptions (not registry facts): half-spread per side and annual borrow on shorts.
HALF_SPREAD_BPS = {"etf": 1.0, "crypto": 5.0}
BORROW_ANNUAL = 0.02
SEARCH_SPACE = {
    "trend": {"lookback": [63, 126, 189, 252], "skip": [0, 5, 10], "vol_window": [20, 40, 60]},
    "xs_momentum": {"lookback": [63, 126, 189, 252], "fraction": [0.2, 0.3, 0.4]},
    "reversal": {"lookback": [3, 5, 10], "entry_z": [0.5, 1.0, 1.5]},
    "risk_parity": {"vol_window": [20, 60, 120]},
}


@dataclass(frozen=True)
class CommitteeConfig:
    name: str
    darwinian: bool = True
    kelly: bool = True
    cro: bool = True
    regimes: bool = False
    reflexivity: bool = False
    score_window: int = 120
    max_gross: float = 1.5
    kelly_multiplier: float = 0.25
    cooloff_days: int = 20
    limits: dw.RiskLimits = field(default_factory=dw.RiskLimits)
    regime_multipliers: tuple = (("low_vol", 0.50), ("high_vol", 0.15))


def load_universe(data_dir: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Prices and dollar volume on the equity trading calendar (crypto sampled on those days)."""
    d = Path(data_dir)
    close, volume = pd.read_parquet(d / "close.parquet"), pd.read_parquet(d / "volume.parquet")
    trading_days = close["SPY"].dropna().index
    close, volume = close.loc[trading_days].sort_index(axis=1), volume.loc[trading_days].sort_index(axis=1)
    dollar = volume * close
    for c in CRYPTO:                     # the data vendor reports crypto volume in dollars already
        if c in dollar:
            dollar[c] = volume[c]
    return close, dollar


def build_cost_model(columns, registry: VenueRegistry, as_of) -> CostModel:
    """Commission per side from the venue registry as of `as_of`; spread and borrow are assumptions."""
    fees, slip = {}, {}
    for c in columns:
        venue, product, kind = ("kraken", "spot", "crypto") if c in CRYPTO else ("alpaca", "equity", "etf")
        fees[c] = registry.fee_for(venue, product, "taker", notional=10_000.0, as_of=as_of).bps
        slip[c] = HALF_SPREAD_BPS[kind]
    return CostModel(fees, slip, BORROW_ANNUAL, 0.0)


def sleeve_targets(prices: pd.DataFrame, params: dict[str, dict]) -> dict[str, pd.DataFrame]:
    out = {}
    for name in sp.SLEEVES:
        w = sp.sleeve_weights(name, prices, params[name])
        for c in CRYPTO:                 # spot crypto cannot be shorted in this cost model
            if c in w:
                w[c] = w[c].clip(lower=0.0)
        out[name] = w
    return out


def run_committee(prices: pd.DataFrame, targets: dict[str, pd.DataFrame], sleeve_net: pd.DataFrame, cost: CostModel,
                  cfg: CommitteeConfig, regime_probs: pd.DataFrame | None = None, warmup: int = 252) -> tuple[pd.DataFrame, dict]:
    """Day-by-day committee simulation. The weights for day t use only information through the close
    of day t; the engine applies the one-bar execution lag afterwards."""
    names = list(targets)
    cols = prices.columns
    ret = prices.pct_change().fillna(0.0).to_numpy()
    tgt = {n: targets[n].to_numpy() for n in names}
    trade_cost = (cost._per_asset(cost.fees_bps, cols, "fees") + cost._per_asset(cost.slippage_bps, cols, "slippage")) * 1e-4
    darwin = dw.DarwinianWeights(names)
    limits = cfg.limits if cfg.cro else dw.RiskLimits(1.0, 10.0, 10.0, 0.999999)
    cro = dw.RiskOfficer(limits)
    committee = dw.DecisionCommittee(darwin, cro, cfg.max_gross, cfg.kelly_multiplier, dict(cfg.regime_multipliers))
    monitor = dw.ReflexivityMonitor()
    n = len(prices)
    decided = np.zeros((n, len(cols)))
    darwin_hist = np.ones((n, len(names)))
    equity, latched_days, vetoes, resizes, own = 1.0, 0, 0, 0, np.zeros(n)
    no_short = np.array([c in CRYPTO for c in cols])
    for t in range(n):
        if t >= 2:
            own[t] = decided[t - 2] @ ret[t] - np.abs(decided[t - 1] - decided[t - 2]) @ trade_cost
        equity *= 1.0 + own[t]
        if t < warmup:
            continue
        if cfg.darwinian:
            darwin.update(dw.rolling_sharpe_scores(sleeve_net.iloc[: t + 1], cfg.score_window))
        darwin_hist[t] = [darwin.weights[k] for k in names]
        if cro.breaker.latched:
            latched_days += 1
            if latched_days >= cfg.cooloff_days:
                cro.reset("backtest-policy", equity)
                latched_days = 0
        reversal = False
        if cfg.reflexivity and t % 5 == 0:
            monitor.observe("pnl_behavior", float(own[t - 4: t + 1].sum()))
        if cfg.reflexivity:
            reversal = monitor.reversal_risk()
        probs = None
        if cfg.regimes and regime_probs is not None and np.isfinite(regime_probs.iloc[t]).all():
            probs = regime_probs.iloc[t].to_dict()
        now = {k: tgt[k][t] for k in names}
        hist = ret[max(0, t - 249): t + 1]
        if cfg.kelly:
            dec = committee.decide(now, sleeve_net.iloc[max(0, t - 251): t + 1], hist, equity, own[t], probs, reversal)
            w, verdict = dec.weights, dec.verdict
        else:
            shares = darwin.shares()
            blend = sum(shares[k] * now[k] for k in names)
            verdict = cro.review(np.where(np.isfinite(blend), blend, 0.0), hist, equity, own[t])
            w = verdict.weights
        vetoes += not verdict.approved
        resizes += verdict.approved and verdict.scale < 1.0
        decided[t] = np.where(no_short, np.maximum(w, 0.0), w)
    diag = {"darwin_weights_final": dict(zip(names, darwin_hist[-1].round(4).tolist())),
            "darwin_weights_min": dict(zip(names, darwin_hist[warmup:].min(axis=0).round(4).tolist())),
            "darwin_weights_max": dict(zip(names, darwin_hist[warmup:].max(axis=0).round(4).tolist())),
            "cro_veto_days": int(vetoes), "cro_resize_days": int(resizes)}
    return pd.DataFrame(decided, index=prices.index, columns=cols), diag


def _slice(s: pd.Series, lo: str, hi: str | None = None) -> pd.Series:
    return s.loc[lo:hi] if hi else s.loc[lo:]


def _report(result: BacktestResult, lo: str, hi: str | None, n_trials: int | None, v: float | None) -> dict:
    return performance_report(_slice(result.net, lo, hi), n_trials, v, _slice(result.turnover, lo, hi),
                              result.costs.loc[lo:hi] if hi else result.costs.loc[lo:])


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="artifacts/data")
    ap.add_argument("--out", default="artifacts/walkforward_report.json")
    ap.add_argument("--trials", default="artifacts/trials.sqlite")
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--mutations", type=int, default=3)
    args = ap.parse_args(argv)

    prices, dollar = load_universe(args.data)
    registry = VenueRegistry()
    cost = build_cost_model(prices.columns, registry, as_of=prices.index[-1])
    stale = registry.stale_fees(90, today=prices.index[-1])
    trials = research.TrialRegistry(args.trials)
    a_end = str(prices.loc[:SPLIT].index[-2].date()) if SPLIT in prices.index else str(prices.loc[:SPLIT].index[-1].date())

    def evaluate(name: str, decided: pd.DataFrame) -> BacktestResult:
        res = run_backtest(prices, decided, cost, dollar, capital=args.capital)
        m = research.sharpe_moments(_slice(res.net, START).to_numpy())
        trials.record(name, m.sharpe, m.n_obs)
        return res

    def sleeves_for(params: dict) -> tuple[dict, pd.DataFrame, dict]:
        tg = sleeve_targets(prices, params)
        res = {k: run_backtest(prices, tg[k], cost, dollar, capital=args.capital) for k in tg}
        return tg, pd.DataFrame({k: r.net for k, r in res.items()}), res

    # causality check of every sleeve on the real data before anything is scored
    leaks = {k: research.detect_lookahead(lambda df, k=k: sp.sleeve_weights(k, df), prices.iloc[:1500], n_checks=4)
             for k in sp.SLEEVES}
    if any(leaks.values()):
        raise RuntimeError(f"look-ahead detected in sleeves: {leaks}")

    base_params = {k: dict(v) for k, v in sp.DEFAULT_PARAMS.items()}
    targets, sleeve_net, sleeve_res = sleeves_for(base_params)
    results: dict[str, BacktestResult] = {}
    diags: dict[str, dict] = {}
    for k, r in sleeve_res.items():
        results[f"sleeve:{k}"] = evaluate(f"sleeve:{k}", targets[k])

    spy_r = prices["SPY"].pct_change().dropna().to_numpy()
    regime = pd.DataFrame(research.causal_regime_probabilities(spy_r, 2, 500, 63), index=prices.index[1:],
                          columns=["low_vol", "high_vol"]).reindex(prices.index)

    configs = [
        CommitteeConfig("equal_weight_sleeves", darwinian=False, kelly=False, cro=False),
        CommitteeConfig("darwinian", kelly=False, cro=False),
        CommitteeConfig("darwinian+kelly+cro"),
        CommitteeConfig("darwinian+kelly+cro+regimes", regimes=True),
        CommitteeConfig("darwinian+kelly+cro+reflexivity", reflexivity=True),
    ]
    decided_by_name: dict[str, pd.DataFrame] = {}
    for cfg in configs:
        decided, diags[cfg.name] = run_committee(prices, targets, sleeve_net, cost, cfg, regime)
        decided_by_name[cfg.name] = decided
        results[cfg.name] = evaluate(cfg.name, decided)
    # cost control: a no-trade band around the Darwinian blend (width fixed a priori at 10%)
    results["darwinian+buffer"] = evaluate("darwinian+buffer", apply_buffer(decided_by_name["darwinian"], prices, 0.10))

    # ---- one autoresearch cycle using period A only
    a_prices = prices.loc[:a_end]

    def eval_sleeve(agent: str, params: dict) -> pd.Series:
        w = sp.sleeve_weights(agent, a_prices, params)
        for c in CRYPTO:
            if c in w:
                w[c] = w[c].clip(lower=0.0)
        return run_backtest(a_prices, w, cost, dollar.loc[:a_end], capital=args.capital).net.loc[START:]

    store = ar.InMemoryParamStore(base_params)
    loop = ar.AutoresearchLoop(eval_sleeve, SEARCH_SPACE, store, np.random.default_rng(20261110), trials)
    outcomes = []
    for _ in range(args.mutations):
        scores = {k: ar.period_sharpe(eval_sleeve(k, store.get(k)).tail(250)) for k in sp.SLEEVES}
        outcomes.append(loop.step(scores).__dict__)
    tuned_params = {k: store.get(k) for k in sp.SLEEVES}
    t_targets, t_net, _ = sleeves_for(tuned_params)
    best_cfg = CommitteeConfig("darwinian+kelly+cro+autoresearch")
    decided, diags[best_cfg.name] = run_committee(prices, t_targets, t_net, cost, best_cfg, regime)
    results[best_cfg.name] = evaluate(best_cfg.name, decided)

    n_trials, v = trials.n_trials(), trials.sharpe_variance()
    bench = {"SPY buy-and-hold": buy_and_hold(prices[["SPY"]]), "BTC buy-and-hold": buy_and_hold(prices[["BTC-USD"]]),
             "equal-weight universe": buy_and_hold(prices)}
    report = {
        "generated": str(pd.Timestamp.now(tz="UTC").floor("s")),
        "data": {"start": str(prices.index[0].date()), "end": str(prices.index[-1].date()), "assets": list(prices.columns)},
        "protocol": {"selection_period": [START, a_end], "holdout_period": [SPLIT, str(prices.index[-1].date())],
                     "execution_lag_bars": 1, "capital": args.capital},
        "costs": {"commission_bps_per_side": cost.fees_bps, "half_spread_bps_assumed": HALF_SPREAD_BPS,
                  "borrow_annual_assumed": BORROW_ANNUAL, "impact": "square-root law, Y = 1, 20-day dollar ADV",
                  "stale_fee_sources": stale[["venue", "product", "age_days"]].to_dict("records")},
        "trials": {"n_trials_recorded": n_trials, "variance_of_trial_sharpes": v,
                   "expected_max_sharpe_per_period": research.expected_max_sharpe(n_trials, v)},
        "lookahead_check": leaks,
        "autoresearch": {"mutations": outcomes, "tuned_params": tuned_params, "base_params": base_params},
        "strategies": {}, "benchmarks": {}, "diagnostics": diags,
    }
    for name, res in results.items():
        report["strategies"][name] = {"full": _report(res, START, None, n_trials, v),
                                      "selection": _report(res, START, a_end, None, None),
                                      "holdout": _report(res, SPLIT, None, n_trials, v)}
    for name, r in bench.items():
        report["benchmarks"][name] = {"full": performance_report(_slice(r, START)),
                                      "selection": performance_report(_slice(r, START, a_end)),
                                      "holdout": performance_report(_slice(r, SPLIT))}
    final = results["darwinian+buffer"]
    last = final.weights.iloc[-1]
    px = prices.ffill().iloc[-1]
    report["paper_order_plan"] = {
        "as_of": str(prices.index[-1].date()), "mode": "DRY RUN - nothing is sent to any venue",
        "notional": args.capital,
        "positions": {c: {"weight": round(float(w), 4), "dollars": round(float(w * args.capital), 0),
                          "units": round(float(w * args.capital / px[c]), 4)} for c, w in last.items() if abs(w) > 1e-4}}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, default=lambda o: None if isinstance(o, float) and math.isnan(o) else str(o)))
    trials.close()
    registry.close()
    return report


if __name__ == "__main__":
    rep = main()
    print(f"trials recorded: {rep['trials']['n_trials_recorded']}")
    for section in ("strategies", "benchmarks"):
        for name, r in rep[section].items():
            h = r["holdout"]
            print(f"{name:36s} holdout: ret {h['total_return']:+8.1%}  sharpe {h['sharpe']:5.2f}  maxDD {h['max_drawdown']:6.1%}")
