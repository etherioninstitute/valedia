"""Backtest like you are trying to kill it.

Five rules, each enforced here in code:

1. The prediction is written first. `write_prediction` stores it in the ledger (which refuses edits)
   and as a read-only file; `develop` will not run without it and stops if the file was changed.
2. A test period is locked away. The last `sealed_fraction` of history never reaches `develop`.
   `seal` runs on it once per idea; the ledger's primary key refuses a second run.
3. Walk-forward. Results are read on consecutive out-of-sample windows. With several variants of a
   strategy, each window trades the variant that was best on the data before it.
4. The bar rises with every attempt. Each variation is counted in the ledger and the Deflated
   Sharpe uses that count (for the idea and for the whole market; the stricter one decides).
5. The breaker: double trading costs, fills delayed by a day, and the market's worst periods.

A failed idea may be revised, but every revision is another counted attempt and `max_revisions`
caps them. Nothing is revised after the sealed period has been looked at.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from valedia_trader.backtest.engine import CostModel, buy_and_hold, performance_report, run_backtest
from valedia_trader.quant_engine import level10_ml_research as research
from valedia_trader.research_loop import LoopPaths
from valedia_trader.research_loop.diagnostics import diagnose
from valedia_trader.research_loop.ledger import HypothesisLedger, LedgerError, SealedPeriodUsed, market_regime
from valedia_trader.research_loop.strategy import Strategy, run_rules

__all__ = ["Prediction", "Criteria", "Gauntlet", "GauntletError", "PredictionMissing", "PredictionTampered",
           "RevisionCapReached", "write_prediction", "read_prediction", "stressed_costs", "worst_periods"]


class GauntletError(RuntimeError):
    pass


class PredictionMissing(GauntletError):
    """No prediction was written before the backtest."""


class PredictionTampered(GauntletError):
    """The prediction file no longer matches what was recorded."""


class RevisionCapReached(GauntletError):
    """The idea has used all the rewrites it is allowed."""


@dataclass(frozen=True)
class Prediction:
    """What the researcher expects, and what would count as failure, stated before any backtest."""
    annual_return: float
    win_rate: float                 # share of closed trades that make money
    sharpe: float
    fail_if_sharpe_below: float
    fail_if_drawdown_above: float
    notes: str = ""

    def __post_init__(self):
        if not 0.0 <= self.win_rate <= 1.0 or not 0.0 < self.fail_if_drawdown_above <= 1.0:
            raise ValueError("win_rate must be in [0, 1] and fail_if_drawdown_above in (0, 1]")
        if not all(math.isfinite(x) for x in (self.annual_return, self.sharpe, self.fail_if_sharpe_below)):
            raise ValueError("the prediction must be numbers")


@dataclass(frozen=True)
class Criteria:
    min_dsr: float = 0.95
    min_positive_windows: float = 0.6
    min_stress_sharpe: float = 0.0
    walk_forward_windows: int = 5
    worst_periods: int = 5
    worst_period_days: int = 63
    sealed_fraction: float = 0.25
    max_revisions: int = 5
    warmup_days: int = 252
    min_train_days: int = 504

    def __post_init__(self):
        if not 0.2 <= self.sealed_fraction <= 0.3:
            raise ValueError("the sealed period is the most recent 20 to 30 percent of the data")
        if not 3 <= self.max_revisions <= 5:
            raise ValueError("revisions per idea are capped at 3 to 5")
        if self.walk_forward_windows < 3:
            raise ValueError("walk-forward needs at least 3 windows")


def _prediction_path(paths: LoopPaths, strategy_name: str) -> Path:
    return paths.backtests / f"{strategy_name}-prediction.md"


def write_prediction(ledger: HypothesisLedger, paths: LoopPaths, hypothesis_id: int, strategy_name: str,
                     prediction: Prediction) -> Path:
    """Save the prediction to the ledger and to `backtests/<strategy>-prediction.md` (read-only).
    It can be written once, and only before the idea's first backtest."""
    h = ledger.get(hypothesis_id)
    path = _prediction_path(paths, strategy_name)
    if path.exists():
        raise GauntletError(f"{path.name} already exists; a prediction is never rewritten")
    body = (f"# Prediction for {strategy_name} (hypothesis {hypothesis_id})\n\n"
            f"Idea: {h['idea']}\n\nWritten before any backtest of this idea.\n\n"
            f"- Expected net annual return: {prediction.annual_return:+.2%}\n"
            f"- Expected share of winning trades: {prediction.win_rate:.0%}\n"
            f"- Expected Sharpe ratio: {prediction.sharpe:.2f}\n"
            f"- It has failed if the Sharpe ratio is below {prediction.fail_if_sharpe_below:.2f}\n"
            f"- It has failed if the drawdown exceeds {prediction.fail_if_drawdown_above:.0%}\n"
            + (f"\nNotes: {prediction.notes}\n" if prediction.notes else ""))
    ledger.register_prediction(hypothesis_id, body, asdict(prediction))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    os.chmod(path, 0o444)
    return path


def read_prediction(ledger: HypothesisLedger, paths: LoopPaths, hypothesis_id: int, strategy_name: str) -> Prediction:
    row = ledger.prediction(hypothesis_id)
    if row is None:
        raise PredictionMissing(f"hypothesis {hypothesis_id}: write the prediction before the backtest")
    path = _prediction_path(paths, strategy_name)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
        raise PredictionTampered(f"{path.name} is missing or differs from the prediction recorded on {row['recorded_at']}")
    return Prediction(**row["expected"])


def stressed_costs(cost: CostModel, factor: float = 2.0) -> CostModel:
    """The same cost model with commissions, spread, borrow and market impact multiplied."""
    def scale(v):
        if v is None:
            return None
        return {k: x * factor for k, x in v.items()} if isinstance(v, Mapping) else v * factor
    return replace(cost, fees_bps=scale(cost.fees_bps), slippage_bps=scale(cost.slippage_bps),
                   borrow_annual=scale(cost.borrow_annual), impact_y=cost.impact_y * factor)


def worst_periods(market_returns: pd.Series, count: int = 5, days: int = 63) -> list[tuple[pd.Timestamp, pd.Timestamp, float]]:
    """The `count` worst non-overlapping `days`-long stretches of a return series: (start, end, return)."""
    r = market_returns.dropna()
    if len(r) < days:
        return []
    roll = np.exp(np.log1p(r.clip(lower=-0.999999)).rolling(days).sum()) - 1.0
    taken = np.zeros(len(r), dtype=bool)
    out = []
    for end in np.argsort(roll.to_numpy(), kind="stable"):
        if len(out) == count:
            break
        if not np.isfinite(roll.iloc[end]) or taken[end - days + 1: end + 1].any():
            continue
        taken[end - days + 1: end + 1] = True
        out.append((r.index[end - days + 1], r.index[end], float(roll.iloc[end])))
    return out


def _stats(net: pd.Series, turnover: pd.Series | None = None, costs: pd.DataFrame | None = None) -> dict:
    keep = ("start", "end", "years", "total_return", "cagr", "annual_vol", "sharpe", "sharpe_t_stat", "max_drawdown",
            "win_rate", "profit_factor", "annual_turnover", "cost_bps_of_traded", "annual_cost_drag")
    try:
        rep = performance_report(net, None, None, turnover, costs)
    except ValueError as exc:
        return {"error": str(exc), "sharpe": float("nan"), "total_return": float("nan"), "max_drawdown": float("nan")}
    out = {k: rep[k] for k in keep if k in rep}
    out["daily_win_rate"] = out.pop("win_rate")
    if not math.isfinite(out["profit_factor"]):
        out["profit_factor"] = None
    return out


def _trade_stats(events: list[dict], lo, hi) -> dict:
    closed = [e for e in events if e["action"] == "exit" and lo <= e["date"] <= hi]
    wins = sum(e["pnl_pct"] > 0 for e in closed)
    return {"trades": len(closed), "trade_win_rate": wins / len(closed) if closed else float("nan"),
            "mean_trade_pct": float(np.mean([e["pnl_pct"] for e in closed])) if closed else float("nan")}


def timing_null(weights: pd.DataFrame, asset_returns: pd.DataFrame, n: int = 300, seed: int = 20261111, min_shift: int = 21) -> dict:
    """Was the timing worth anything, or only the average exposure?

    The strategy's own book is slid along the calendar by a random number of days (the same shift for every
    asset, wrapping round), which keeps what it held, how much and for how long, and breaks only when. If the
    real book does not beat most of these, its return came from being exposed, not from the rule. Gross of
    costs on both sides: a shifted book trades exactly as much as the real one."""
    cols = [c for c in weights.columns if c in asset_returns.columns and float(weights[c].abs().sum()) > 0]
    w = weights[cols].to_numpy(float); r = asset_returns[cols].fillna(0.0).to_numpy(float)
    days = len(w)
    if not cols or days < 4 * min_shift:
        return {"unavailable": "too little history or no positions"}

    def sharpe(book: np.ndarray) -> float:
        pnl = (book[:-1] * r[1:]).sum(axis=1)
        sd = pnl.std(ddof=1)
        return float(pnl.mean() / sd * math.sqrt(252.0)) if sd > 0 else 0.0

    actual = sharpe(w)
    rng = np.random.default_rng(seed)
    nulls = np.array([sharpe(np.roll(w, int(k), axis=0)) for k in rng.integers(min_shift, days - min_shift, size=n)])
    always = sharpe(np.tile(np.abs(w).mean(axis=0) * np.sign(w.sum(axis=0)), (days, 1)))          # the same average book, held throughout
    return {"actual_gross_sharpe": actual, "null_median": float(np.median(nulls)), "null_95th": float(np.quantile(nulls, 0.95)),
            "p_value": float((1 + (nulls >= actual).sum()) / (n + 1)), "shifts": int(n),
            "average_book_held_throughout_sharpe": always,
            "reading": "p is the share of randomly re-timed copies of this book that did at least as well; "
                       "a high p means the rule's timing added nothing beyond its average exposure"}


def _attempt_pvalues(ledger: HypothesisLedger, hypothesis_id: int) -> list[float]:
    """One-sided p-value of each counted variation's Sharpe (normal approximation), for the multiple-testing view."""
    out = []
    for a in ledger.attempts(hypothesis_id):
        if a.get("sharpe") is None or not a.get("n_obs"):
            continue
        z = float(a["sharpe"]) * math.sqrt(float(a["n_obs"]))
        out.append(0.5 * math.erfc(z / math.sqrt(2.0)))
    return out


def _diagnostics(ledger: HypothesisLedger, hypothesis_id: int, net: pd.Series, market: pd.Series | None,
                 asset_returns: pd.DataFrame, weights: pd.DataFrame, prediction: "Prediction") -> dict:
    """The sheet's view of a tested return stream. Information for the reader, the panel and the critic; never a gate."""
    try:
        return diagnose(net, benchmark=market, asset_returns=asset_returns, weights=weights,
                        variation_pvalues=_attempt_pvalues(ledger, hypothesis_id),
                        drawdown_limit=float(prediction.fail_if_drawdown_above), profit_target=max(0.02, float(prediction.annual_return)))
    except Exception as exc:                                             # reports must survive a diagnostics fault
        return {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}


class Gauntlet:
    """Holds the full history, hands out only the development part, and runs the two tests."""

    def __init__(self, prices: pd.DataFrame, dollar_volume: pd.DataFrame | None, cost: CostModel,
                 ledger: HypothesisLedger, paths: LoopPaths, criteria: Criteria | None = None,
                 capital: float = 1_000_000.0) -> None:
        self.criteria = criteria or Criteria()
        self.ledger, self.paths, self.cost, self.capital = ledger, paths, cost, capital
        cut = int(len(prices) * (1.0 - self.criteria.sealed_fraction))
        if cut < self.criteria.warmup_days + self.criteria.min_train_days or len(prices) - cut < 60:
            raise GauntletError("not enough history for a development period and a sealed period")
        self.__prices, self.__dollar, self._cut = prices, dollar_volume, cut

    # ---- data access
    @property
    def dev_prices(self) -> pd.DataFrame:
        """The development period: everything research and revisions are allowed to see."""
        return self.__prices.iloc[: self._cut]

    @property
    def dev_dollar_volume(self) -> pd.DataFrame | None:
        return None if self.__dollar is None else self.__dollar.iloc[: self._cut]

    @property
    def sealed_start(self) -> pd.Timestamp:
        return self.__prices.index[self._cut]

    def _report_path(self, strategy: Strategy, label: str) -> Path:
        return self.paths.backtests / f"{strategy.name}-v{strategy.version}-{label}.json"

    # ---- development test
    def develop(self, hypothesis_id: int, strategy: Strategy, strategy_file: str = "",
                variants: dict[str, Strategy] | None = None) -> dict:
        """Run the development test. Returns the report (also saved under backtests/). The idea's
        status becomes 'failed' unless every check passes; a pass leaves it 'untested' until `seal`."""
        c, ledger = self.criteria, self.ledger
        h = ledger.get(hypothesis_id)
        prediction = read_prediction(ledger, self.paths, hypothesis_id, strategy.name)
        if ledger.sealed_used(hypothesis_id):
            raise SealedPeriodUsed(f"hypothesis {hypothesis_id} has seen its sealed result; it is not revised any more")
        candidates = dict(variants) if variants else {"base": strategy}
        fingerprint = {label: _fingerprint(s) for label, s in candidates.items()}
        earlier = ledger.attempts(hypothesis_id)
        known = {a["strategy_version"] for a in earlier}
        first = not known
        for a in earlier:                 # a changed strategy must carry a new version, or the cap could be dodged
            label = a["params"].get("variant")
            if a["strategy_version"] == strategy.version and label in fingerprint and a["params"].get("sha") != fingerprint[label]:
                raise GauntletError(f"{strategy.name} changed since v{strategy.version} was tested; a revision needs a new version")
        if strategy.version not in known and not first and ledger.revisions(hypothesis_id) >= c.max_revisions:
            ledger.set_status(hypothesis_id, "failed", f"needed more than {c.max_revisions} rewrites to pass", "gauntlet")
            raise RevisionCapReached(f"hypothesis {hypothesis_id} has used its {c.max_revisions} revisions")

        dev, dollar = self.dev_prices, self.dev_dollar_volume
        runs: dict[str, tuple[pd.DataFrame, list[dict], pd.Series]] = {}
        probe = dev.iloc[-min(len(dev), 900):]
        for label, s in candidates.items():
            leaks = research.detect_lookahead(s.target_weights, probe, n_checks=3)
            if leaks:
                raise GauntletError(f"{s.name} ({label}) uses future data in columns {leaks}")
            w, events = run_rules(s, dev)
            net = run_backtest(dev, w, self.cost, dollar, capital=self.capital).net
            runs[label] = (w, events, net)

        # walk-forward: consecutive test windows; each trades the variant that was best before it
        start = c.warmup_days + (c.min_train_days if len(candidates) > 1 else 0)
        windows = [idx for idx in np.array_split(np.arange(start, len(dev)), c.walk_forward_windows) if len(idx)]
        stitched = pd.DataFrame(0.0, index=dev.index, columns=dev.columns)
        chosen, events_used = [], []
        for idx in windows:
            if len(candidates) == 1:
                pick = next(iter(candidates))
            else:
                train = slice(c.warmup_days, idx[0])
                pick = max(candidates, key=lambda k: _sharpe(runs[k][2].iloc[train]))
            chosen.append(pick)
            stitched.iloc[idx] = runs[pick][0].iloc[idx].to_numpy()
            lo, hi = dev.index[idx[0]], dev.index[idx[-1]]
            events_used += [e for e in runs[pick][1] if lo <= e["date"] <= hi]
        oos = slice(windows[0][0], None)
        base = run_backtest(dev, stitched, self.cost, dollar, capital=self.capital)
        net = base.net.iloc[oos]
        headline = _stats(net, base.turnover.iloc[oos], base.costs.iloc[oos])
        headline.update(_trade_stats(events_used, dev.index[windows[0][0]], dev.index[-1]))
        per_window = []
        for idx, pick in zip(windows, chosen):
            r = base.net.iloc[idx]
            per_window.append({"start": str(dev.index[idx[0]].date()), "end": str(dev.index[idx[-1]].date()), "variant": pick,
                               "return": float(np.prod(1.0 + r) - 1.0), "sharpe": _sharpe(r) * math.sqrt(252.0)})
        positive = float(np.mean([w["return"] > 0 for w in per_window]))

        # every variant is a counted attempt, and the count sets the Deflated Sharpe bar
        kind = "backtest" if first else "revision"
        for label, s in candidates.items():
            v = runs[label][2].iloc[oos]
            ledger.record_attempt(hypothesis_id, kind, _sharpe(v), int(v.notna().sum()), strategy.version,
                                  {"variant": label, "sha": fingerprint[label], "spec": _spec_of(s)})
        dsr = self._deflated(net, hypothesis_id, h["market"])

        # the breaker
        stress = run_backtest(dev, stitched, stressed_costs(self.cost, 2.0), dollar, capital=self.capital).net.iloc[oos]
        delayed = run_backtest(dev, stitched, self.cost, dollar, capital=self.capital, execution_lag=2).net.iloc[oos]
        market = buy_and_hold(dev[[m for m in strategy.market if m in dev.columns]]).iloc[oos]
        worst = [{"start": str(a.date()), "end": str(b.date()), "market_return": mr,
                  "strategy_return": float(np.prod(1.0 + net.loc[a:b]) - 1.0)}
                 for a, b, mr in worst_periods(market, c.worst_periods, c.worst_period_days)]
        breaker = {"double_costs": _stats(stress), "delayed_fills": _stats(delayed), "worst_periods": worst,
                   "timing_null": timing_null(base.weights.iloc[oos], dev.pct_change().iloc[oos]),      # reported, not a gate
                   "market_basket": _stats(market)}

        sharpe, dd = headline.get("sharpe", float("nan")), headline.get("max_drawdown", float("nan"))
        worst_loss = min((w["strategy_return"] for w in worst), default=0.0)
        checks = {
            "prediction_failure_terms": (bool(sharpe >= prediction.fail_if_sharpe_below and dd <= prediction.fail_if_drawdown_above),
                                         f"Sharpe {sharpe:.2f} (fails below {prediction.fail_if_sharpe_below:.2f}), drawdown "
                                         f"{dd:.1%} (fails above {prediction.fail_if_drawdown_above:.0%})"),
            "deflated_sharpe": (bool(dsr["value"] >= c.min_dsr), f"{dsr['value']:.3f} against {c.min_dsr} "
                                f"({dsr['variations_of_idea']} variations of the idea, {dsr['attempts_on_market']} on the market)"),
            "walk_forward": (bool(positive >= c.min_positive_windows), f"{positive:.0%} of {len(per_window)} windows made money"),
            "double_costs": (bool(breaker["double_costs"]["sharpe"] > c.min_stress_sharpe),
                             f"Sharpe {breaker['double_costs']['sharpe']:.2f} at twice the trading costs"),
            "delayed_fills": (bool(breaker["delayed_fills"]["sharpe"] > c.min_stress_sharpe),
                              f"Sharpe {breaker['delayed_fills']['sharpe']:.2f} with fills one day late"),
            "worst_periods": (bool(worst_loss >= -prediction.fail_if_drawdown_above),
                              f"worst result in the market's {len(worst)} worst stretches: {worst_loss:+.1%}"),
        }
        passed = all(ok for ok, _ in checks.values())
        contrib = (base.weights.shift(1).iloc[oos] * dev.pct_change().iloc[oos]).sum().sort_values()
        report = {
            "hypothesis_id": hypothesis_id, "idea": h["idea"], "strategy": strategy.describe(), "strategy_file": strategy_file,
            "attempt": ledger.variations(hypothesis_id), "revisions_used": ledger.revisions(hypothesis_id),
            "revisions_allowed": c.max_revisions,
            "development_period": [str(dev.index[0].date()), str(dev.index[-1].date())],
            "sealed_period": "sealed; not used by this test", "out_of_sample_from": str(dev.index[windows[0][0]].date()),
            "result": headline, "walk_forward": per_window, "positive_windows": positive, "deflated_sharpe": dsr,
            "breaker": breaker,
            "prediction": asdict(prediction),
            "versus_prediction": {"annual_return": headline.get("cagr", float("nan")) - prediction.annual_return,
                                  "win_rate": headline.get("trade_win_rate", float("nan")) - prediction.win_rate,
                                  "sharpe": sharpe - prediction.sharpe},
            "where_it_loses": {"worst_symbols": {k: float(v) for k, v in contrib.head(3).items()},
                               "worst_window": min(per_window, key=lambda w: w["return"])},
            "exits": _exit_reasons(events_used),
            "checks": {k: {"passed": ok, "detail": d} for k, (ok, d) in checks.items()},
            "verdict": "pass (development); sealed test pending" if passed else "fail",
        }
        report["diagnostics"] = _diagnostics(ledger, hypothesis_id, net, market, dev.pct_change().iloc[oos], base.weights.iloc[oos], prediction)
        regime = market_regime(dev[[m for m in strategy.market if m in dev.columns]].iloc[oos], dev)
        ledger.record_backtest(hypothesis_id, _clean({k: report[k] for k in ("result", "positive_windows", "deflated_sharpe", "checks", "verdict",
                                                                             "walk_forward", "where_it_loses", "exits")}),
                               regime, strategy_file, strategy.version, "untested" if passed else "failed")
        if not passed:
            failed = "; ".join(f"{k}: {d}" for k, (ok, d) in checks.items() if not ok)
            ledger.add_lesson(hypothesis_id, f"v{strategy.version} failed the development test in a {regime} market. {failed}. "
                              f"Largest losses came from {', '.join(contrib.head(3).index)}.", "gauntlet")
        self._save(self._report_path(strategy, f"attempt{report['attempt']}"), report)
        return report

    def probe(self, hypothesis_id: int, version: str, candidates: dict[str, Strategy]) -> dict[str, dict]:
        """Run candidate rewrites of an idea on the development period and return how each behaved:
        score (out-of-sample Sharpe), per-window Sharpe, drawdown, trades and turnover. Every probe is
        a counted variation under `version`, so probing raises the Deflated Sharpe bar and spends a
        revision exactly as a full rewrite would."""
        c, ledger = self.criteria, self.ledger
        read_prediction(ledger, self.paths, hypothesis_id, next(iter(candidates.values())).name)
        if ledger.sealed_used(hypothesis_id):
            raise SealedPeriodUsed(f"hypothesis {hypothesis_id} has seen its sealed result; it is not revised any more")
        known = {a["strategy_version"] for a in ledger.attempts(hypothesis_id)}
        if version not in known and known and ledger.revisions(hypothesis_id) >= c.max_revisions:
            ledger.set_status(hypothesis_id, "failed", f"needed more than {c.max_revisions} rewrites to pass", "gauntlet")
            raise RevisionCapReached(f"hypothesis {hypothesis_id} has used its {c.max_revisions} revisions")
        dev, dollar = self.dev_prices, self.dev_dollar_volume
        windows = [idx for idx in np.array_split(np.arange(c.warmup_days, len(dev)), c.walk_forward_windows) if len(idx)]
        oos = slice(windows[0][0], None)
        out = {}
        for label, s in candidates.items():
            leaks = research.detect_lookahead(s.target_weights, dev.iloc[-min(len(dev), 900):], n_checks=3)
            if leaks:
                raise GauntletError(f"{s.name} ({label}) uses future data in columns {leaks}")
            w, events = run_rules(s, dev)
            res = run_backtest(dev, w, self.cost, dollar, capital=self.capital)
            net = res.net.iloc[oos]
            stats = _stats(net, res.turnover.iloc[oos], res.costs.iloc[oos])
            ledger.record_attempt(hypothesis_id, "probe", _sharpe(net), int(net.notna().sum()), version,
                                  {"variant": label, "sha": _fingerprint(s), "spec": _spec_of(s)})
            in_span = [e for e in events if e["date"] >= dev.index[windows[0][0]]]
            out[label] = {"score": stats.get("sharpe", float("nan")), "drawdown": stats.get("max_drawdown", float("nan")),
                          "total_return": stats.get("total_return", float("nan")), "turnover": stats.get("annual_turnover", float("nan")),
                          "tasks": {f"w{i + 1}": _sharpe(res.net.iloc[idx]) * math.sqrt(252.0) for i, idx in enumerate(windows)},
                          **_trade_stats(in_span, dev.index[windows[0][0]], dev.index[-1]), "exits": _exit_reasons(in_span)}
        return out

    def _deflated(self, net: pd.Series, hypothesis_id: int, market: str) -> dict:
        r = net.dropna().to_numpy(dtype=float)
        idea, mkt = self.ledger.attempt_sharpes(hypothesis_id=hypothesis_id), self.ledger.attempt_sharpes(market=market)
        out = {"variations_of_idea": int(self.ledger.variations(hypothesis_id)), "attempts_on_market": int(len(mkt))}
        try:
            m = research.sharpe_moments(r)
        except ValueError:
            return {**out, "value": 0.0, "for_idea": 0.0, "for_market": 0.0}
        # variance of trial Sharpes: from the market's own attempts once there are several, else the
        # sampling variance of a zero-skill Sharpe estimate
        var = float(mkt.var(ddof=1)) if len(mkt) >= 3 else 1.0 / m.n_obs
        def dsr(n):
            if n < 2:
                return research.probabilistic_sharpe_ratio(m.sharpe, 0.0, m.n_obs, m.skew, m.kurt)
            return research.deflated_sharpe_ratio(m.sharpe, m.n_obs, n, var, m.skew, m.kurt)
        for_idea, for_market = float(dsr(max(len(idea), 1))), float(dsr(max(len(mkt), 1)))
        return {**out, "for_idea": for_idea, "for_market": for_market, "value": min(for_idea, for_market),
                "variance_of_attempt_sharpes": var}

    # ---- the sealed test: once
    def seal(self, hypothesis_id: int, strategy: Strategy, strategy_file: str = "") -> dict:
        """The idea's single run on the sealed period. Requires a passed development test of this exact
        version. The use is claimed before the data is touched, so a crash also spends it."""
        ledger = self.ledger
        h = ledger.get(hypothesis_id)
        prediction = read_prediction(ledger, self.paths, hypothesis_id, strategy.name)
        last = h["backtest_result"] or {}
        if not str(last.get("verdict", "")).startswith("pass") or h["strategy_version"] != strategy.version:
            raise GauntletError("the sealed test needs a passed development test of this exact strategy version")
        ledger.use_sealed(hypothesis_id, strategy.version)
        prices = self.__prices
        w, events = run_rules(strategy, prices)
        res = run_backtest(prices, w, self.cost, self.__dollar, capital=self.capital)
        sl = slice(self._cut, None)
        result = _stats(res.net.iloc[sl], res.turnover.iloc[sl], res.costs.iloc[sl])
        result.update(_trade_stats(events, prices.index[self._cut], prices.index[-1]))
        sharpe, dd, total = result.get("sharpe", float("nan")), result.get("max_drawdown", float("nan")), result.get("total_return", float("nan"))
        checks = {
            "made_money": (bool(total > 0), f"net return {total:+.1%}"),
            "prediction_failure_terms": (bool(sharpe >= prediction.fail_if_sharpe_below and dd <= prediction.fail_if_drawdown_above),
                                         f"Sharpe {sharpe:.2f} (fails below {prediction.fail_if_sharpe_below:.2f}), drawdown "
                                         f"{dd:.1%} (fails above {prediction.fail_if_drawdown_above:.0%})"),
        }
        passed = all(ok for ok, _ in checks.values())
        regime = market_regime(prices[[m for m in strategy.market if m in prices.columns]].iloc[sl], self.dev_prices)
        report = {"hypothesis_id": hypothesis_id, "strategy": strategy.describe(), "strategy_file": strategy_file,
                  "sealed_period": [str(prices.index[self._cut].date()), str(prices.index[-1].date())], "market_regime": regime,
                  "result": result, "prediction": asdict(prediction),
                  "versus_development": {"sharpe": sharpe - float((last.get("result") or {}).get("sharpe", float("nan")))},
                  "checks": {k: {"passed": ok, "detail": d} for k, (ok, d) in checks.items()},
                  "verdict": "pass" if passed else "fail"}
        sealed_market = buy_and_hold(prices[[m for m in strategy.market if m in prices.columns]]).iloc[sl]
        report["diagnostics"] = _diagnostics(ledger, hypothesis_id, res.net.iloc[sl], sealed_market, prices.pct_change().iloc[sl], res.weights.iloc[sl], prediction)
        ledger.record_sealed(hypothesis_id, _clean({k: report[k] for k in ("sealed_period", "market_regime", "result", "checks", "verdict")}),
                             "passed backtest only" if passed else "failed")
        if not passed:
            ledger.add_lesson(hypothesis_id, f"v{strategy.version} passed development but failed its one sealed test in a {regime} "
                              "market: " + "; ".join(d for ok, d in checks.values() if not ok) + ". Treat the development result as overfit.",
                              "gauntlet")
        self._save(self._report_path(strategy, "sealed"), report)
        return report

    @staticmethod
    def _save(path: Path, report: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_clean(report), indent=2, default=str))


def _clean(obj):
    """NaN and infinity become null so the saved report is strict JSON."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (float, np.floating)):
        return float(obj) if math.isfinite(obj) else None
    if isinstance(obj, (np.integer, np.bool_)):
        return obj.item()
    return obj


def _exit_reasons(events: list[dict]) -> dict:
    """How positions were closed: count and mean result per kind of exit."""
    groups: dict[str, list[float]] = {}
    for e in events:
        if e["action"] == "exit":
            groups.setdefault(str(e["reason"]).split(":")[0], []).append(float(e["pnl_pct"]))
    return {k: {"count": len(v), "mean_pnl_pct": float(np.mean(v))} for k, v in groups.items()}


def _spec_of(strategy: Strategy) -> dict:
    return strategy.to_spec() if hasattr(strategy, "to_spec") else strategy.describe()


def _fingerprint(strategy: Strategy) -> str:
    """Identity of a strategy's rules: its spec for rule strategies, its source file for hand-written ones."""
    import inspect
    if hasattr(strategy, "to_spec"):
        text = json.dumps(strategy.to_spec(), sort_keys=True)
    else:
        try:
            text = inspect.getsource(type(strategy))
        except (OSError, TypeError):
            text = json.dumps(strategy.describe(), sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _sharpe(net: pd.Series) -> float:
    r = net.dropna().to_numpy(dtype=float)
    sd = r.std(ddof=1) if r.size > 2 else 0.0
    return float(r.mean() / sd) if sd > 1e-15 else 0.0


__all__ += ["LedgerError"]
