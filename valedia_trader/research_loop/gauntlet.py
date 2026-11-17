"""Backtest like you are trying to kill it.

Five rules, each enforced here in code:

1. The prediction is written first. `write_prediction` stores it in the ledger (which refuses edits)
   and as a read-only file; `develop` will not run without it and stops if the file was changed.
2. A test period is locked away. The last `sealed_fraction` of history never reaches `develop`.
   `seal` runs on it once per idea; the ledger's primary key refuses a second run.
3. Walk-forward. Results are read on consecutive out-of-sample windows. With several variants of a
   strategy, each window trades the variant that was best on the data before it.
4. The bar rises with every attempt. Each variation is counted in the ledger, and the count sets the bar.
5. The breaker: double trading costs, fills delayed by a day, and the market's worst periods.

WHAT A PASS CERTIFIES: THE CLAIM. Every prediction says, before its backtest, what kind of idea it is.
* An EDGE claim ("the rule's timing earns more than holding the same book would"; the default, and the only claim the
  research round's automatic path writes) is judged by the rule the gate study measured (`gate_study.py`, R3_listed):
  `sharpe_one_trial`, the Probabilistic Sharpe against zero for one trial, at least `min_dsr`; and `timing`, the
  re-timing test on the LISTED WINDOW (`timing_listed`) at 0.05 / N, N being every counted attempt in the ledger on
  any market, this run's included. Where the window's D distinct re-timed copies can resolve 0.05 / N (0.05 / N at
  least 1 / (D + 1)) the p-value comes from 400 N drawn shifts, exactly as studied; beyond that the book must beat
  every copy, found by enumerating all D (`edge_timing`). The Deflated Sharpe is reported and decides nothing.
* An EXPOSURE claim ("holding this book is all it shows"; set only by a person, through the argument) is judged
  exactly as the gate judged every idea before the split: the Deflated Sharpe at the idea's and the market label's
  counts. A random long-only timing book on a fresh label passes most of the time, so the pass says what it is.
The claim is stored in the development and sealed results; a stored result without one was judged before the split,
and `seal` refuses such a pass.

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

from valedia_trader.backtest.engine import CostModel, booked_returns, buy_and_hold, performance_report, run_backtest
from valedia_trader.quant_engine import level10_ml_research as research
from valedia_trader.research_loop import LoopPaths
from valedia_trader.research_loop.diagnostics import diagnose
from valedia_trader.research_loop.ledger import HypothesisLedger, LedgerError, SealedPeriodUsed, market_regime
from valedia_trader.research_loop.strategy import Strategy, run_rules

__all__ = ["Prediction", "Criteria", "Gauntlet", "GauntletError", "PredictionMissing", "PredictionTampered",
           "RevisionCapReached", "write_prediction", "read_prediction", "stressed_costs", "worst_periods", "CLAIMS",
           "booked_asset_returns", "timing_null", "timing_exact", "listed_start", "timing_listed", "edge_timing",
           "one_trial_sharpe", "r3_shifts", "ALPHA", "GATE_SHIFTS"]

CLAIMS = ("edge", "exposure")
EXPOSURE_PASS = "pass (development, exposure claim: holding is all it shows); sealed test pending"
EDGE_PASS = "pass (development); sealed test pending"
BEFORE_THE_SPLIT = "judged before the edge/exposure split: run the development test again"
ALPHA = 0.05                          # the re-timing level of an edge claim at one attempt; N attempts divide it
R3_SHIFTS_PER_RECIPROCAL = 20         # the drawn re-timing test uses this many times 1 / threshold shifts
GATE_SHIFTS = 300                     # timing_null's own default: the figures reported for an exposure claim


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
    """What the researcher expects, and what would count as failure, stated before any backtest.
    Every figure is a finite real number, never a bool (numpy reals are taken as floats), judged when it is made:
    `annual_return`, `sharpe`: any finite number. `win_rate`: from 0 to 1. `fail_if_sharpe_below`: any finite number;
    a negative floor is allowed, since a Sharpe can lie below any finite floor and the check can still fail.
    `fail_if_drawdown_above`: above 0 and at most 1 (a limit of 0 would fail every book)."""
    annual_return: float
    win_rate: float                 # share of closed trades that make money
    sharpe: float
    fail_if_sharpe_below: float
    fail_if_drawdown_above: float
    notes: str = ""
    claim: str = "edge"             # "edge": the rule's timing is the claim; "exposure": holding is all it claims

    def __post_init__(self):
        if not isinstance(self.claim, str) or self.claim not in CLAIMS:
            raise ValueError(f"the claim is one of {CLAIMS}, not {self.claim!r}")
        for name in ("annual_return", "sharpe", "fail_if_sharpe_below"):
            _judge_mark(self, name, -math.inf, math.inf, lo_open=True, hi_open=True)
        _judge_mark(self, "win_rate", 0.0, 1.0)
        _judge_mark(self, "fail_if_drawdown_above", 0.0, 1.0, lo_open=True)


@dataclass(frozen=True)
class Criteria:
    """The gate's pass marks, each a finite real number (never a bool; numpy reals are taken as floats), judged when
    made, in a range that leaves its check able to fail and able to pass: `min_dsr` strictly between 0 and 1;
    `min_positive_windows` above 0 and at most 1 (0 would let a book with no winning window pass); `min_stress_sharpe`
    any finite number (negative allowed: a Sharpe can lie below any finite floor); `sealed_fraction` from 0.2 to 0.3."""
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
        _judge_mark(self, "min_dsr", 0.0, 1.0, lo_open=True, hi_open=True,
                    why="a probability: a pass mark that nothing can miss, or nothing can meet, is refused")
        _judge_mark(self, "min_positive_windows", 0.0, 1.0, lo_open=True)
        _judge_mark(self, "min_stress_sharpe", -math.inf, math.inf, lo_open=True, hi_open=True)
        _judge_mark(self, "sealed_fraction", 0.2, 0.3, why="the sealed period is the most recent 20 to 30 percent")
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
            f"- Claim: {prediction.claim} ({_CLAIM_WORDS[prediction.claim]})\n"
            + (f"\nNotes: {prediction.notes}\n" if prediction.notes else ""))
    ledger.register_prediction(hypothesis_id, body, asdict(prediction))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    os.chmod(path, 0o444)
    return path


_CLAIM_WORDS = {"edge": "the rule's timing earns more than holding the same book would",
                "exposure": "holding the book is all it claims; its timing is not what is tested"}


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
        return _gross_sharpe(book, r)

    actual = sharpe(w)
    rng = np.random.default_rng(seed)
    nulls = np.array([sharpe(np.roll(w, int(k), axis=0)) for k in rng.integers(min_shift, days - min_shift, size=n)])
    always = sharpe(np.tile(np.abs(w).mean(axis=0) * np.sign(w.sum(axis=0)), (days, 1)))          # the same average book, held throughout
    return {"actual_gross_sharpe": actual, "null_median": float(np.median(nulls)), "null_95th": float(np.quantile(nulls, 0.95)),
            "p_value": float((1 + (nulls >= actual).sum()) / (n + 1)), "shifts": int(n),
            "average_book_held_throughout_sharpe": always,
            "reading": "p is the share of randomly re-timed copies of this book that did at least as well; "
                       "a high p means the rule's timing added nothing beyond its average exposure"}


def _gross_sharpe(book: np.ndarray, r: np.ndarray) -> float:
    """The re-timing tests' statistic: the gross Sharpe of `book` held into the next bar's returns, annualised."""
    pnl = (book[:-1] * r[1:]).sum(axis=1)
    sd = pnl.std(ddof=1)
    return float(pnl.mean() / sd * math.sqrt(252.0)) if sd > 0 else 0.0


def timing_exact(weights: pd.DataFrame, asset_returns: pd.DataFrame, min_shift: int = 21) -> dict:
    """`timing_null` with every distinct common shift taken once instead of drawn: the D copies of the book slid by
    `min_shift` to `days - min_shift - 1` bars (wrapping round), the same statistic. `p_exact` is the share
    (1 + copies at or above the book) / (D + 1); `beats_every_copy` holds when no copy reached the book's figure (a copy
    whose figure is not a finite number counts as reaching it). Unavailable exactly where `timing_null` is."""
    cols = [c for c in weights.columns if c in asset_returns.columns and float(weights[c].abs().sum()) > 0]
    w = weights[cols].to_numpy(float); r = asset_returns[cols].fillna(0.0).to_numpy(float)
    days = len(w)
    if not cols or days < 4 * min_shift:
        return {"unavailable": "too little history or no positions"}
    actual = _gross_sharpe(w, r)
    nulls = np.array([_gross_sharpe(np.roll(w, k, axis=0), r) for k in range(min_shift, days - min_shift)])
    above = int((~(nulls < actual)).sum()) if math.isfinite(actual) else len(nulls)
    return {"actual_gross_sharpe": actual, "copies": int(len(nulls)), "copies_at_or_above": above,
            "p_exact": (1 + above) / (len(nulls) + 1), "floor": 1.0 / (len(nulls) + 1),
            "beats_every_copy": bool(above == 0)}


def r3_shifts(n_attempts: int) -> int:
    """Shifts the drawn re-timing test needs to resolve 0.05 / N: 20 times the reciprocal of the threshold."""
    return int(math.ceil(R3_SHIFTS_PER_RECIPROCAL * n_attempts / ALPHA - 1e-9))


def _held_cols(weights: pd.DataFrame, other: pd.DataFrame) -> list[str]:
    """The symbols a book holds at some time in the window: `timing_null`'s own choice of columns."""
    return [c for c in weights.columns if c in other.columns and float(weights[c].abs().sum()) > 0]


def listed_start(weights: pd.DataFrame, prices: pd.DataFrame) -> int | None:
    """The first row of the LISTED WINDOW: the first bar on which every symbol the book ever holds in `weights` has a
    price in `prices` (the same rows). 0 for a book that holds nothing; None if there is no such bar."""
    cols = _held_cols(weights, prices)
    if not cols:
        return 0
    px = prices[cols].to_numpy(dtype=float)
    hit = np.flatnonzero((np.isfinite(px) & (px > 0)).all(axis=1))
    return int(hit[0]) if len(hit) else None


def timing_listed(weights: pd.DataFrame, asset_returns: pd.DataFrame, prices: pd.DataFrame, n: int = GATE_SHIFTS,
                  exact: bool = False) -> dict:
    """`timing_null` run on the listed window only (same statistic, common shift, minimum shift and seed),
    plus `listed_start`, `listed_bars` and `listed_share` (the count and the share of the window's bars inside it;
    both None for a book that holds nothing in the window, 0 and 0.0 when no bar has every held symbol priced).
    Unavailable as timing_null is (too short a stretch, no positions), or when no bar has every held symbol priced.
    `exact`: `timing_exact` on that stretch instead (every distinct shift once; `n` unused)."""
    def test(w, r):
        return timing_exact(w, r) if exact else timing_null(w, r, n=n)
    if not _held_cols(weights, prices):
        return {**test(weights, asset_returns), "listed_start": 0, "listed_bars": None, "listed_share": None}
    s = listed_start(weights, prices)
    if s is None:
        return {"unavailable": "no bar has a price for every symbol the book holds", "listed_start": None,
                "listed_bars": 0, "listed_share": 0.0}
    out = test(weights.iloc[s:], asset_returns.iloc[s:])
    return {**out, "listed_start": s, "listed_bars": len(weights) - s, "listed_share": (len(weights) - s) / len(weights)}


def _real(x) -> bool:
    """A finite real number: never a flag (bool), a string or None."""
    return (not isinstance(x, (bool, np.bool_)) and isinstance(x, (int, float, np.integer, np.floating))
            and math.isfinite(float(x)))


def _count(x) -> bool:
    """A whole number (never a flag) of at least 1."""
    return not isinstance(x, (bool, np.bool_)) and isinstance(x, (int, np.integer)) and int(x) >= 1


def _judge_mark(obj, field: str, lo: float, hi: float, lo_open: bool = False, hi_open: bool = False,
                why: str = "") -> None:
    """Judge one figure of a `Prediction` or `Criteria` when it is made: a finite real number (never a bool) inside
    [lo, hi] (an end open where asked), or ValueError naming the field. A numpy real is stored as a Python float."""
    x = getattr(obj, field)
    inside = _real(x) and (lo < x if lo_open else lo <= x) and (x < hi if hi_open else x <= hi)
    if not inside:
        span = f"{'(' if lo_open else '['}{lo}, {hi}{')' if hi_open else ']'}"
        raise ValueError(f"{type(obj).__name__}.{field} must be a finite real number (never a bool) in {span}, "
                         f"not {x!r}" + (f": {why}" if why else ""))
    if isinstance(x, (np.integer, np.floating)):
        object.__setattr__(obj, field, float(x))


def _p_ok(p, threshold: float) -> bool:
    return _real(p) and 0.0 <= p <= 1.0 and p <= threshold


def edge_timing(weights: pd.DataFrame, asset_returns: pd.DataFrame, prices: pd.DataFrame, n_attempts: int) -> dict:
    """The `timing` check of an edge claim, on the listed window, at N = `n_attempts` counted attempts.
    The window offers D distinct re-timed copies, so no test on it has a level below 1 / (D + 1) (`floor`).
    * 0.05 / N at least `floor` (region "drawn"): the studied rule exactly, the p-value of 400 N drawn shifts (same
      seed) at most 0.05 / N.
    * 0.05 / N below `floor` (region "enumerated"): the book must beat every one of the D copies, enumerated once; the
      level per test is then 1 / (D + 1), and the detail says the nominal level cannot be reached.
    `p_exact` from the enumeration is reported in both regions. A figure that is missing, unavailable or not finite
    fails the check, and so does a listed window too short for the test (with that reason). Every figure the check
    reads (N, the copies D, the floor, the drawn p-value, "beats every copy") must be of its kind, finite and in its
    range; otherwise the check fails with a reason naming the figure."""
    if not _count(n_attempts):
        return {"attempts_counted": n_attempts, "threshold": None, "listed_start": None, "listed_bars": None,
                "listed_share": None, "copies": None, "floor": None, "p_exact": None, "region": None,
                "drawn_p_value": None, "passed": False, "listed": {"unavailable": "no valid number of attempts"},
                "detail": f"fails: the number of counted attempts N is not a whole number of at least 1 "
                          f"({n_attempts!r})"}
    n = int(n_attempts)
    threshold = ALPHA / n
    exact = timing_listed(weights, asset_returns, prices, exact=True)
    out = {"attempts_counted": n, "threshold": threshold, "listed_start": exact.get("listed_start"),
           "listed_bars": exact.get("listed_bars"), "listed_share": exact.get("listed_share"),
           "copies": exact.get("copies"), "floor": exact.get("floor"), "p_exact": exact.get("p_exact"),
           "region": None, "drawn_p_value": None}
    if "unavailable" in exact:
        return {**out, "passed": False, "listed": exact,
                "detail": f"fails: the listed window is too short for the re-timing test or holds nothing "
                          f"({exact['unavailable']})"}
    d, floor = exact.get("copies"), exact.get("floor")
    if not _count(d):
        return {**out, "passed": False, "listed": exact,
                "detail": f"fails: the number of re-timed copies D is not a whole number of at least 1 ({d!r})"}
    if not (_real(floor) and 0.0 < floor <= 1.0):
        return {**out, "passed": False, "listed": exact,
                "detail": f"fails: the reachable floor is not a number above 0 and at most 1 ({floor!r})"}
    d = int(d)
    if threshold >= floor:
        drawn = timing_listed(weights, asset_returns, prices, n=r3_shifts(n))
        p = drawn.get("p_value")
        if not (_real(p) and 0.0 <= p <= 1.0):
            return {**out, "region": "drawn", "drawn_p_value": p, "passed": False, "listed": drawn,
                    "detail": f"fails: the drawn re-timing p-value is not a number from 0 to 1 ({p!r}) from "
                              f"{r3_shifts(n)} drawn shifts of the listed window ({d} distinct copies)"}
        return {**out, "region": "drawn", "drawn_p_value": p, "passed": _p_ok(p, threshold), "listed": drawn,
                "detail": f"re-timing p {p:.5f} from {r3_shifts(n)} drawn shifts of the listed window "
                          f"({d} distinct copies), against 0.05 / {n} = {threshold:.6f}"}
    above, beats = exact.get("copies_at_or_above"), exact.get("beats_every_copy")
    if beats is not True and beats is not False:
        return {**out, "region": "enumerated", "passed": False, "listed": exact,
                "detail": f"fails: the figure 'beats every copy' is not the flag True or False ({beats!r})"}
    passed = beats is True
    return {**out, "region": "enumerated", "passed": passed, "listed": exact,
            "detail": f"the listed window offers {d} distinct re-timed copies, so the nominal level 0.05 / {n} = "
                      f"{threshold:.6f} cannot be reached; the book must beat every copy (level per test 1 / {d + 1} = "
                      f"{floor:.6f}): " + ("it beat all of them" if passed else f"{above} of them did at least as well")}


def one_trial_sharpe(net: pd.Series, min_dsr: float) -> tuple[float | None, bool, str]:
    """The `sharpe_one_trial` check of an edge claim: the Probabilistic Sharpe of the out-of-sample net returns
    against zero for one trial (the value `_deflated` gives for one trial), at least `min_dsr`. Moments that cannot be
    formed, or are not finite, fail; so does a pass mark that is not a number strictly between 0 and 1, and a
    Probabilistic Sharpe that is not a number from 0 to 1."""
    if not (_real(min_dsr) and 0.0 < min_dsr < 1.0):
        return None, False, f"fails: the pass mark is not a number strictly between 0 and 1 ({min_dsr!r})"
    r = net.dropna().to_numpy(dtype=float)
    try:
        m = research.sharpe_moments(r)
    except ValueError as exc:
        return None, False, f"fails: no Sharpe moments ({str(exc)[:80]})"
    if not (all(_real(getattr(m, k, None)) for k in ("sharpe", "skew", "kurt")) and _count(getattr(m, "n_obs", None))):
        return None, False, "fails: the Sharpe moments are not finite numbers (or the count is not a whole number)"
    try:
        v = research.probabilistic_sharpe_ratio(m.sharpe, 0.0, m.n_obs, m.skew, m.kurt)
    except (ValueError, TypeError, ArithmeticError) as exc:
        return None, False, f"fails: no Probabilistic Sharpe from these moments ({str(exc)[:80]})"
    if not (_real(v) and 0.0 <= v <= 1.0):
        return None, False, f"fails: the Probabilistic Sharpe is not a number from 0 to 1 ({v!r})"
    v = float(v)
    return v, bool(v >= min_dsr), f"{v:.3f} against {min_dsr} (the Probabilistic Sharpe for one trial)"


def booked_asset_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Each symbol's return as the engine books it (`engine.booked_returns`: the move since the last priced bar), and
    NaN on a bar where the engine books none for the symbol (an unpriced bar, or the symbol's first priced bar). On a
    frame without interior gaps this is exactly `prices.pct_change()`; across an unpriced bar it is the engine's move
    since the last priced bar (pct_change has no figure for the bar after a gap)."""
    px = prices.to_numpy(dtype=float)
    priced = np.isfinite(px) & (px > 0)
    before = np.zeros_like(priced)
    before[1:] = np.maximum.accumulate(priced, axis=0)[:-1]
    return pd.DataFrame(np.where(priced & before, booked_returns(px), np.nan), index=prices.index, columns=prices.columns)


def _fin(*xs) -> bool:
    """Every figure a development check reads is a finite number, never a flag (a check never passes on anything else)."""
    return all(_real(x) for x in xs)


def _worst_stretch_check(worst: list[dict], fail_if_drawdown_above: float) -> tuple[bool, str]:
    """The `worst_periods` check: every worst stretch's strategy return is a finite number, and the worst of them
    loses no more than the prediction's drawdown limit. No stretch at all fails, and so does any stretch whose return
    is not a finite number (named), wherever it sits in the list."""
    if not worst:
        return False, "fails: no worst stretch could be formed from the market's returns"
    for w in worst:
        x = w.get("strategy_return")
        if not _real(x):
            return False, (f"fails: the strategy's return over the worst stretch {w.get('start')} to {w.get('end')} "
                           f"is not a finite number ({x!r})")
    worst_loss = min(float(w["strategy_return"]) for w in worst)
    return (bool(worst_loss >= -fail_if_drawdown_above),
            f"worst result in the market's {len(worst)} worst stretches: {worst_loss:+.1%}")


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

        claim = prediction.claim
        dev, dollar = self.dev_prices, self.dev_dollar_volume
        returns = booked_asset_returns(dev)
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

        # every variant is a counted attempt, and the count sets the bar
        kind = "backtest" if first else "revision"
        for label, s in candidates.items():
            v = runs[label][2].iloc[oos]
            ledger.record_attempt(hypothesis_id, kind, _sharpe(v), int(v.notna().sum()), strategy.version,
                                  {"variant": label, "sha": fingerprint[label], "spec": _spec_of(s)})
        dsr = self._deflated(net, hypothesis_id, h["market"])
        counted = {"ledger": int(len(ledger.attempt_sharpes())), "label": int(dsr["attempts_on_market"])}
        w_oos, r_oos, px_oos = base.weights.iloc[oos], returns.iloc[oos], dev.iloc[oos]

        # the breaker
        stress = run_backtest(dev, stitched, stressed_costs(self.cost, 2.0), dollar, capital=self.capital).net.iloc[oos]
        delayed = run_backtest(dev, stitched, self.cost, dollar, capital=self.capital, execution_lag=2).net.iloc[oos]
        market = buy_and_hold(dev[[m for m in strategy.market if m in dev.columns]]).iloc[oos]
        worst = [{"start": str(a.date()), "end": str(b.date()), "market_return": mr,
                  "strategy_return": float(np.prod(1.0 + net.loc[a:b]) - 1.0)}
                 for a, b, mr in worst_periods(market, c.worst_periods, c.worst_period_days)]
        breaker = {"double_costs": _stats(stress), "delayed_fills": _stats(delayed), "worst_periods": worst,
                   "timing_null": timing_null(w_oos, r_oos),                     # reported, not a gate
                   "market_basket": _stats(market)}
        if claim == "edge":
            timing = edge_timing(w_oos, r_oos, px_oos, counted["ledger"])
            breaker["timing_listed"] = timing.pop("listed")                 # the figure the timing check reads
            timing.update(attempts_on_label=counted["label"], variants=len(candidates),
                          note=("" if len(candidates) == 1 else
                                f"{len(candidates)} variants: the test ran on the stitched walk-forward book, which the "
                                "gate study did not measure (it measured books of one variant)"))
        else:
            timing = None
            breaker["timing_listed"] = timing_listed(w_oos, r_oos, px_oos, n=GATE_SHIFTS)      # reported, never a condition

        sharpe, dd = headline.get("sharpe", float("nan")), headline.get("max_drawdown", float("nan"))
        stress_sharpe, delayed_sharpe = breaker["double_costs"]["sharpe"], breaker["delayed_fills"]["sharpe"]
        checks = {
            "prediction_failure_terms": (bool(_fin(sharpe, dd) and sharpe >= prediction.fail_if_sharpe_below
                                              and dd <= prediction.fail_if_drawdown_above),
                                         f"Sharpe {sharpe:.2f} (fails below {prediction.fail_if_sharpe_below:.2f}), drawdown "
                                         f"{dd:.1%} (fails above {prediction.fail_if_drawdown_above:.0%})"),
        }
        if claim == "edge":
            one_value, one_ok, one_detail = one_trial_sharpe(net, c.min_dsr)
            dsr["one_trial"] = one_value
            checks["sharpe_one_trial"] = (one_ok, one_detail)
            checks["timing"] = (bool(timing["passed"]), timing["detail"] + (
                f" ({counted['ledger']} attempts in the ledger, {counted['label']} on this market label)"))
        else:
            counts = f"({dsr['variations_of_idea']} variations of the idea, {dsr['attempts_on_market']} on the market)"
            checks["deflated_sharpe"] = ((False, f"fails: {dsr['fault']} {counts}") if "fault" in dsr else
                                         (bool(_fin(dsr["value"]) and dsr["value"] >= c.min_dsr),
                                          f"{dsr['value']:.3f} against {c.min_dsr} {counts}"))
        checks.update({
            "walk_forward": (bool(_fin(positive) and positive >= c.min_positive_windows), f"{positive:.0%} of {len(per_window)} windows made money"),
            "double_costs": (bool(_fin(stress_sharpe) and stress_sharpe > c.min_stress_sharpe),
                             f"Sharpe {stress_sharpe:.2f} at twice the trading costs"),
            "delayed_fills": (bool(_fin(delayed_sharpe) and delayed_sharpe > c.min_stress_sharpe),
                              f"Sharpe {delayed_sharpe:.2f} with fills one day late"),
            "worst_periods": _worst_stretch_check(worst, prediction.fail_if_drawdown_above),
        })
        passed = all(ok for ok, _ in checks.values())
        contrib = (base.weights.shift(1).iloc[oos] * r_oos).sum().sort_values()
        report = {
            "hypothesis_id": hypothesis_id, "idea": h["idea"], "claim": claim, "attempts_counted": counted,
            "strategy": strategy.describe(), "strategy_file": strategy_file,
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
            "verdict": (EDGE_PASS if claim == "edge" else EXPOSURE_PASS) if passed else "fail",
        }
        if timing is not None:
            report["timing"] = timing
        report["diagnostics"] = _diagnostics(ledger, hypothesis_id, net, market, r_oos, w_oos, prediction)
        regime = market_regime(dev[[m for m in strategy.market if m in dev.columns]].iloc[oos], dev)
        ledger.record_backtest(hypothesis_id, _clean({k: report[k] for k in ("claim", "attempts_counted", "result", "positive_windows",
                                                                             "deflated_sharpe", "timing", "checks", "verdict",
                                                                             "walk_forward", "where_it_loses", "exits") if k in report}),
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
            return {**out, "value": 0.0, "for_idea": 0.0, "for_market": 0.0, "variance_leaving_idea_out": None}

        def fault(why: str) -> dict:     # a figure not of its kind: no figures, and the value 0.0 (it fails), as above
            return {**out, "value": 0.0, "for_idea": None, "for_market": None, "variance_leaving_idea_out": None,
                    "fault": why}
        moments = {k: getattr(m, k, None) for k in ("sharpe", "skew", "kurt", "n_obs")}
        if not (all(_real(moments[k]) for k in ("sharpe", "skew", "kurt")) and _count(moments["n_obs"])):
            return fault("the Sharpe moments are not finite numbers or the count is not a whole number ("
                         + ", ".join(f"{k} {v!r}" for k, v in moments.items()) + ")")
        # variance of trial Sharpes: from the market's own attempts once there are several, else the
        # sampling variance of a zero-skill Sharpe estimate
        var = float(mkt.var(ddof=1)) if len(mkt) >= 3 else 1.0 / m.n_obs
        def dsr(n):
            if n < 2:
                return research.probabilistic_sharpe_ratio(m.sharpe, 0.0, m.n_obs, m.skew, m.kurt)
            return research.deflated_sharpe_ratio(m.sharpe, m.n_obs, n, var, m.skew, m.kurt)
        figures = {}
        for name, n in (("for_idea", max(len(idea), 1)), ("for_market", max(len(mkt), 1))):
            try:
                x = dsr(n)
            except (ValueError, TypeError, ArithmeticError) as exc:
                return fault(f"no Deflated Sharpe {name} from these figures ({str(exc)[:80]})")
            if not (_real(x) and 0.0 <= x <= 1.0):
                return fault(f"the Deflated Sharpe {name} is not a number from 0 to 1 ({x!r})")
            figures[name] = float(x)
        for_idea, for_market = figures["for_idea"], figures["for_market"]
        others = [float(x) for x in mkt]
        for s in idea:                                                   # the label's attempts, the idea's own taken out
            if float(s) in others:
                others.remove(float(s))
        var_out = float(np.var(others, ddof=1)) if len(others) >= 3 else 1.0 / m.n_obs
        return {**out, "for_idea": for_idea, "for_market": for_market, "value": min(for_idea, for_market),
                "variance_of_attempt_sharpes": var, "variance_leaving_idea_out": var_out}

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
        claim = last.get("claim")
        if not isinstance(claim, str) or claim not in CLAIMS:            # the claim comes from the stored result only
            raise GauntletError(BEFORE_THE_SPLIT)
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
        report = {"hypothesis_id": hypothesis_id, "claim": claim, "strategy": strategy.describe(), "strategy_file": strategy_file,
                  "sealed_period": [str(prices.index[self._cut].date()), str(prices.index[-1].date())], "market_regime": regime,
                  "result": result, "prediction": asdict(prediction),
                  "versus_development": {"sharpe": sharpe - float((last.get("result") or {}).get("sharpe", float("nan")))},
                  "checks": {k: {"passed": ok, "detail": d} for k, (ok, d) in checks.items()},
                  "verdict": "pass" if passed else "fail"}
        sealed_market = buy_and_hold(prices[[m for m in strategy.market if m in prices.columns]]).iloc[sl]
        report["diagnostics"] = _diagnostics(ledger, hypothesis_id, res.net.iloc[sl], sealed_market,
                                             booked_asset_returns(prices).iloc[sl], res.weights.iloc[sl], prediction)
        ledger.record_sealed(hypothesis_id, _clean({k: report[k] for k in ("claim", "sealed_period", "market_regime", "result", "checks", "verdict")}),
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
