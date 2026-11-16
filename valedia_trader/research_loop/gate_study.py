"""A measured study of candidate pass rules for the gauntlet: how often each rule lets through books with no skill,
and how much real skill it needs before it lets a book through. It changes nothing in the gate and recommends no rule.

ONE BACKTEST PER BOOK, EVERY RULE JUDGED FROM IT. Each book is run as a real idea would be: in its own temporary
ledger a neutral prediction is written, the REAL `Gauntlet.develop` runs (so the criteria, walk-forward, breaker and
re-timing test are the gate's own) and, where development passed, the REAL `Gauntlet.seal`. From that one run the
study keeps the raw material every rule needs: the report's checks, the re-timing p-value, the idea's own counted
attempt Sharpe, and (rebuilt with the same calls `develop` makes for one candidate) the out-of-sample net returns and
the book's weights, from which the Sharpe, length, skew and kurtosis the Deflated Sharpe uses are taken. The sealed
checks are rebuilt with the same calls `seal` makes, for every book some rule passes in development, whether or not
the gate's own development passed. Then every rule is judged by pure functions (`develops`, `passes`).

THE ANCHOR. Rule R0 judged this way must equal the real gate's own verdict. The study checks it on every book at one
attempt (the gate's own fresh-ledger run, development and sealed), checks that the rebuilt material reproduces the
report's Deflated Sharpe, re-timing p-value and headline Sharpe exactly, and, when prior attempts are given, re-runs
the first book of every cell through the real gate in a ledger seeded with them. `report["anchor"]` counts every
disagreement; the study is void unless they are all zero.

BOOK FAMILIES (no skill: N1 to N4; real, known skill: E1 to E3)
* N1: the long-only oracle (`controls.OracleCheat`) at hit rate 0.50, 5-bar blocks: random timing.
* N2: the same, 20-bar blocks.
* N3: the long-short no-edge book (`controls.NoEdgeBook`).
* N4: rule strategies on SURROGATE prices: `n_surrogates` joint stationary-bootstrap resamples of the frame
  (`surrogate_prices`), and on each `specs_per_surrogate` long-only rule strategies drawn from the rule library
  (`draw_rule_spec`, ranges in `DRAW_RANGES`). Identical books on one surrogate count once (`_surrogate_books`:
  a repeated spec, or a different spec with the same whole-history book, is dropped and counted). Books on one
  surrogate share its path and are not independent, so N4 also reports each surrogate's own pass share and a 95%
  interval from a bootstrap over whole surrogates (`surrogate_bootstrap`, seeded, 2,000 resamples); the reading judges
  N4 by that interval, not the Wilson one.
* E1: the long-only oracle at hit rates 0.52, 0.55, 0.60, 5-bar blocks. E2: the same, 20-bar blocks.
* E3: a market-neutral oracle (`NeutralOracle`) at the same hit rates, 5-bar blocks.

DATA VERSIONS. "given": the prices as handed in. "detrended": `controls.detrend` applied to the frame each book runs
on (for N4: to each surrogate drawn from the detrended prices, so its drift is exactly zero as well).

CANDIDATE RULES. Each is "passes development under these conditions AND passes the sealed checks as today"; the
development checks it does not name stay as the gate has them. N is the number of attempts counted on the market,
this one included (1 on a fresh ledger, len(prior_attempt_sharpes) + 1 at the prior attempts).
* R0: the gate as it is today.
* R1: R0 and the re-timing p-value (the report's `timing_null`, 300 shifts) at most 0.05.
* R2: R1, with the Deflated Sharpe's variance of attempt Sharpes taken from the OTHER attempts only (the idea's own
  left out; fewer than 3 others: 1 / n_obs, what the gate does today for fewer than 3).
* R3: the Sharpe test for one trial (the gate's Probabilistic Sharpe against zero) instead of the Deflated Sharpe, and
  the re-timing p-value at most 0.05 / N, from `gauntlet.timing_null` re-run with 400 N shifts (20 times the
  reciprocal of the threshold), same seed, on the same out-of-sample book and returns.
* R4: the Deflated Sharpe computed on ACTIVE returns: the out-of-sample net return minus the return of the same
  average book, held throughout (exactly `timing_null`'s definition: |w| averaged over the out-of-sample bars, times
  the sign of the summed weight, per symbol with any position), over the same bars; N counted as today, the idea's own
  attempt left out of the variance (as R2). No re-timing condition (the rule does not name one).

Everything runs in a `tempfile.TemporaryDirectory()`; each book's folder is deleted when it is done. The study takes
no path, so it cannot write the real ledger; the command line reads prior Sharpes from a ledger opened read-only.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import tempfile
import time
import warnings
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd

from valedia_trader.backtest.engine import CostModel, run_backtest
from valedia_trader.quant_engine import level10_ml_research as research
from valedia_trader.research_loop import LoopPaths
from valedia_trader.research_loop import controls
from valedia_trader.research_loop import gauntlet as gt
from valedia_trader.research_loop.ledger import HypothesisLedger
from valedia_trader.research_loop.strategy import CRYPTO, SCORES, RuleStrategy, Strategy, StrategyRisk, run_rules

__all__ = ["study", "develops", "passes", "deflated_value", "wilson", "surrogate_bootstrap", "surrogate_prices",
           "stationary_bootstrap_rows",
           "draw_rule_spec", "NeutralOracle", "RULES", "FAMILIES", "DRAW_RANGES", "main"]

RULES = ("R0", "R1", "R2", "R3", "R4")
ALPHA = 0.05                          # the re-timing threshold of R1 and R2; R3 divides it by N
R3_SHIFTS_PER_RECIPROCAL = 20         # R3 re-runs timing_null with this many times 1 / threshold shifts
GATE_SHIFTS = 300                     # timing_null's own default, used by develop
POWER_SHARE = 0.8                     # "passes at least 80% of books"
MEAN_BLOCK = 20                       # stationary bootstrap: mean block length in bars
PROGRESS_EVERY = 100
EDGE_HIT_RATES = (0.52, 0.55, 0.60)
_Z95 = 1.959963984540054
_SEALED_CHECKS = ("made_money", "prediction_failure_terms")
_VERSIONS = ("given", "detrended")
_CODES = {"N1": 1, "N2": 2, "N3": 3, "N4": 4, "E1": 5, "E2": 6, "E3": 7, "surrogate": 8, "spec": 9, "cluster": 10}
N_BOOT = 2000                         # resamples of whole surrogates for N4's interval

# family: (kind, hit rates, horizon, plain description)
FAMILIES: dict[str, tuple[str, tuple[float, ...], int | None, str]] = {
    "N1": ("oracle", (0.50,), 5, "long-only random timing, 5-bar blocks (the oracle at hit rate 0.50)"),
    "N2": ("oracle", (0.50,), 20, "long-only random timing, 20-bar blocks (the oracle at hit rate 0.50)"),
    "N3": ("no_edge", (), None, "the long-short no-edge book"),
    "N4": ("surrogate_rules", (), None, "long-only rule strategies on stationary-bootstrap surrogate prices"),
    "E1": ("oracle", EDGE_HIT_RATES, 5, "the long-only oracle, 5-bar blocks"),
    "E2": ("oracle", EDGE_HIT_RATES, 20, "the long-only oracle, 20-bar blocks"),
    "E3": ("neutral", EDGE_HIT_RATES, 5, "the market-neutral oracle, 5-bar blocks"),
}
NO_SKILL = ("N1", "N2", "N3", "N4")
EDGE = ("E1", "E2", "E3")

# The ranges N4 draws from. Score parameters: integers drawn uniformly from the library's documented ranges
# (`strategy._SCORE_PARAMS`), with the extra constraints named. Thresholds: `enter` uniform in the range given, in the
# score's own units, and `exit` = enter - width, width uniform in the range given (so exit <= enter always).
DRAW_RANGES: dict[str, dict] = {
    "momentum": {"lookback": (5, 756), "skip": (0, 63), "enter": (0.0, 0.10), "width": (0.0, 0.10),
                 "constraint": "skip < lookback"},
    "breakout": {"lookback": (5, 504), "enter": (0.0, 1.0), "width": (0.0, 2.0)},
    "reversion": {"lookback": (1, 63), "vol_window": (10, 504), "enter": (0.0, 2.0), "width": (0.0, 2.0)},
    "ma_cross": {"fast": (2, 252), "slow": (5, 756), "enter": (0.0, 0.05), "width": (0.0, 0.05),
                 "constraint": "slow > fast"},
    "drift": {"window": (20, 756), "horizon": (1, 63), "enter": (0.0, 0.5), "width": (0.0, 0.5)},
    "trend_blend": {"fast": (2, 64), "speeds": (1, 4), "vol_window": (5, 126), "enter": (0.0, 1.0), "width": (0.0, 1.0)},
    "turn_of_month": {"before": (0, 10), "after": (0, 10), "enter": 1, "exit": 0,
                      "constraint": "before + after > 0"},
    "hold": {"enter": 1, "exit": 0},
}
SIZING = {"rule": "equal"}            # the library's default sizing; risk is the default StrategyRisk()


# ---- small pure pieces

def wilson(passes: int, runs: int, z: float = _Z95) -> list[float] | None:
    """The Wilson score interval for a share passes / runs (95% by default); None for no runs."""
    if runs <= 0:
        return None
    if not 0 <= passes <= runs:
        raise ValueError("passes must be between 0 and runs")
    p, n = passes / runs, float(runs)
    mid = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [max(0.0, mid - half), min(1.0, mid + half)]


def surrogate_bootstrap(passes_per: Sequence[int], books_per: Sequence[int], rng: np.random.Generator,
                        n_boot: int = N_BOOT) -> list[float] | None:
    """A 95% interval for a family's pass share when its books cluster by surrogate: `n_boot` resamples of whole
    surrogates (drawn with replacement, as many as there are), each resample's share being its passes over its books;
    the 2.5th and 97.5th percentiles. None with fewer than 2 surrogates (no spread between surrogates to resample)."""
    p, b = np.asarray(passes_per, dtype=float), np.asarray(books_per, dtype=float)
    if p.shape != b.shape or p.ndim != 1 or (b < 1).any() or (p < 0).any() or (p > b).any():
        raise ValueError("one pass count and one book count (at least 1) per surrogate, passes between 0 and books")
    if len(p) < 2:
        return None
    idx = rng.integers(0, len(p), size=(int(n_boot), len(p)))
    shares = p[idx].sum(axis=1) / b[idx].sum(axis=1)
    return [float(np.quantile(shares, 0.025)), float(np.quantile(shares, 0.975))]


def deflated_value(moments: dict | None, own: Sequence[float], others: Sequence[float], *, leave_own_out: bool = False,
                   one_trial: bool = False) -> float:
    """`Gauntlet._deflated`'s value from its inputs. `moments`: sharpe (per period), n_obs, skew, kurt (non-excess) of
    the out-of-sample net returns, or None when `sharpe_moments` refused them (the gate's value is then 0). `own`: the
    idea's counted attempt Sharpes; `others`: every other attempt on the market, in ledger order. The market's
    attempts are others then own. The variance of trial Sharpes is that of the market's attempts (of `others` only when
    `leave_own_out`) once there are 3 or more, else 1 / n_obs. `one_trial`: the Sharpe test for one trial."""
    if moments is None:
        return 0.0
    sr, n_obs, skew, kurt = moments["sharpe"], int(moments["n_obs"]), moments["skew"], moments["kurt"]
    mkt = np.array([*others, *own], dtype=float)
    pool = np.array(list(others), dtype=float) if leave_own_out else mkt
    var = float(pool.var(ddof=1)) if len(pool) >= 3 else 1.0 / n_obs

    def dsr(n: int) -> float:
        if n < 2:
            return float(research.probabilistic_sharpe_ratio(sr, 0.0, n_obs, skew, kurt))
        return float(research.deflated_sharpe_ratio(sr, n_obs, n, var, skew, kurt))
    if one_trial:
        return dsr(1)
    return min(dsr(max(len(own), 1)), dsr(max(len(mkt), 1)))


def attempts_counted(material: dict, others: Sequence[float]) -> int:
    return len(others) + max(len(material["own"]), 1)


def r3_shifts(n_attempts: int) -> int:
    """Shifts R3's re-timing test needs to resolve 0.05 / N: 20 times the reciprocal of the threshold."""
    return int(math.ceil(R3_SHIFTS_PER_RECIPROCAL * n_attempts / ALPHA - 1e-9))


def _gate_checks_ok(material: dict) -> bool:
    """Every development check of the gate other than the Deflated Sharpe."""
    return all(ok for k, ok in material["checks"].items() if k != "deflated_sharpe")


def _p_ok(p, threshold: float) -> bool:
    return p is not None and math.isfinite(p) and p <= threshold


def _r3_rest(material: dict, criteria: gt.Criteria) -> bool:
    return _gate_checks_ok(material) and deflated_value(material["moments"], material["own"], (), one_trial=True) >= criteria.min_dsr


def develops(rule: str, material: dict, others: Sequence[float], criteria: gt.Criteria | None = None) -> bool:
    """Does the book pass development under `rule`, with `others` the other attempts counted on the market?
    Pure: everything comes from `material` (see `_book`). R3 reads `material["strict_p"][N]`, which must be there
    whenever the rest of R3 passes (KeyError otherwise)."""
    c = criteria or gt.Criteria()
    if material.get("refused"):
        return False
    own, m = material["own"], material["moments"]
    if rule == "R0":
        return _gate_checks_ok(material) and deflated_value(m, own, others) >= c.min_dsr
    if rule == "R1":
        return develops("R0", material, others, c) and _p_ok(material["timing_p"], ALPHA)
    if rule == "R2":
        return (_gate_checks_ok(material) and deflated_value(m, own, others, leave_own_out=True) >= c.min_dsr
                and _p_ok(material["timing_p"], ALPHA))
    if rule == "R3":
        if not _r3_rest(material, c):
            return False
        n = attempts_counted(material, others)
        return _p_ok(material["strict_p"][n], ALPHA / n)
    if rule == "R4":
        return _gate_checks_ok(material) and deflated_value(material["active_moments"], own, others, leave_own_out=True) >= c.min_dsr
    raise ValueError(f"unknown rule {rule!r}")


def sealed_ok(material: dict) -> bool:
    s = material["sealed"]
    if s is None:
        raise KeyError("the sealed checks of this book were not computed")
    return all(bool(s[k]) for k in _SEALED_CHECKS)


def passes(rule: str, material: dict, others: Sequence[float], criteria: gt.Criteria | None = None) -> bool:
    """Development under `rule` and the sealed checks as today."""
    return develops(rule, material, others, criteria) and sealed_ok(material)


# ---- surrogate prices

def stationary_bootstrap_rows(n: int, pool: int, mean_block: float, rng: np.random.Generator) -> np.ndarray:
    """`n` row numbers in [0, pool): Politis and Romano's stationary bootstrap. Each row continues the previous one
    (wrapping round) with probability 1 - 1 / mean_block, else starts a new block at a uniformly drawn row."""
    if n < 1 or pool < 1 or mean_block < 1:
        raise ValueError("n, pool and mean_block must be at least 1")
    starts = rng.integers(0, pool, size=n)
    jump = rng.random(n) < 1.0 / mean_block
    out = np.empty(n, dtype=np.int64)
    out[0] = starts[0]
    for t in range(1, n):
        out[t] = starts[t] if jump[t] else (out[t - 1] + 1) % pool
    return out


def surrogate_prices(prices: pd.DataFrame, rng: np.random.Generator, mean_block: float = MEAN_BLOCK) -> pd.DataFrame:
    """A joint stationary-bootstrap resample of the frame's simple returns, rebuilt into prices.

    Rows are resampled together, so the cross-section of each bar is kept. Only COMPLETE rows are resampled: bars on
    which every symbol has a simple return (both prices present and positive). Each bar after the first gets one
    resampled complete row; each symbol's prices are rebuilt by compounding from its own first price, at its own
    first row, and the frame's missing prices stay missing (a symbol listed later is still missing before its
    listing; a gap stays a gap, and the compounding runs through it). Fewer than 252 complete rows: ValueError."""
    px = prices.to_numpy(dtype=float)
    ok = np.isfinite(px) & (px > 0)
    r = np.full(px.shape, np.nan)
    both = ok[1:] & ok[:-1]
    r[1:][both] = px[1:][both] / px[:-1][both] - 1.0
    complete = np.flatnonzero(np.isfinite(r).all(axis=1))
    if len(complete) < 252:
        raise ValueError(f"only {len(complete)} bars have a return for every symbol; the surrogate needs at least 252")
    n, m = px.shape
    rows = complete[stationary_bootstrap_rows(n - 1, len(complete), mean_block, rng)]
    sim = np.vstack([np.zeros((1, m)), r[rows]])
    out = np.full(px.shape, np.nan)
    for j in range(m):
        first = np.flatnonzero(ok[:, j])
        if not len(first):
            continue
        f = first[0]
        path = px[f, j] * np.cumprod(1.0 + np.concatenate([[0.0], sim[f + 1:, j]]))
        out[f:, j] = path
    out[~ok] = np.nan
    return pd.DataFrame(out, index=prices.index, columns=prices.columns)


def draw_rule_spec(columns: Sequence[str], rng: np.random.Generator, k: int) -> dict:
    """One long-only rule strategy spec drawn from the library (uniform over `SCORES`), within `DRAW_RANGES`."""
    name = sorted(SCORES)[int(rng.integers(len(SCORES)))]
    rg = DRAW_RANGES[name]

    def integer(key, lo=None, hi=None):
        a, b = rg[key]
        return int(rng.integers(a if lo is None else max(a, lo), (b if hi is None else min(b, hi)) + 1))
    entry: dict = {"rule": name}
    if name == "momentum":
        entry["lookback"] = integer("lookback")
        entry["skip"] = integer("skip", hi=entry["lookback"] - 1)
    elif name == "breakout":
        entry["lookback"] = integer("lookback")
    elif name == "reversion":
        entry.update(lookback=integer("lookback"), vol_window=integer("vol_window"))
    elif name == "ma_cross":
        entry["fast"] = integer("fast")
        entry["slow"] = integer("slow", lo=entry["fast"] + 1)
    elif name == "drift":
        entry.update(window=integer("window"), horizon=integer("horizon"))
    elif name == "trend_blend":
        entry.update(fast=integer("fast"), speeds=integer("speeds"), vol_window=integer("vol_window"))
    elif name == "turn_of_month":
        while True:
            before, after = integer("before"), integer("after")
            if before + after > 0:
                break
        entry.update(before=before, after=after)
    if "width" in rg:
        enter = float(rng.uniform(*rg["enter"]))
        entry.update(enter=enter, exit=enter - float(rng.uniform(*rg["width"])))
    else:
        entry.update(enter=rg["enter"], exit=rg["exit"])
    entry["direction"] = "long"
    return {"name": f"study_rule_{k}", "version": "1.0.0", "hypothesis": f"A drawn {name} rule has no real edge here",
            "market": list(columns), "entry": entry, "sizing": dict(SIZING)}


class NeutralOracle(Strategy):
    """The oracle's block signs (`controls._block_signs`, the gate's execution lag) on the symbols that can be
    shorted: long the positive ones, short the negative ones, half the gross on each side, equal weights within a
    side (gross 1, net 0). A block with no symbol on one side holds nothing. It writes its book directly (`walk`), so
    every block has exactly equal gross each side; it carries its own copy of the whole history, as the oracle does."""
    rebalance_days = None

    def __init__(self, full_prices: pd.DataFrame, hit_rate: float, horizon: int, rng: np.random.Generator,
                 name: str = "study_neutral_oracle"):
        if not 0.0 <= hit_rate <= 1.0 or horizon < 1:
            raise ValueError("hit_rate in [0, 1] and horizon >= 1")
        self.name, self.version = name, "1.0.0"
        self.hypothesis = f"A market-neutral oracle knows {horizon}-bar signs with hit rate {hit_rate:.2f}"
        self.market = tuple(c for c in full_prices.columns if c not in CRYPTO)
        if len(self.market) < 2:
            raise ValueError("the neutral oracle needs at least two symbols that can be shorted")
        px = full_prices[list(self.market)]
        n, m = px.shape
        self.risk = StrategyRisk(max_position_pct=0.5, max_open_positions=m, max_gross=1.0, stop_loss_pct=None)
        flip = rng.random((-(-n // horizon), m)) >= hit_rate
        sign = controls._block_signs(px, horizon, flip)
        up, down = sign > 0, sign < 0
        nu, nd = up.sum(axis=1, keepdims=True), down.sum(axis=1, keepdims=True)
        w = np.where((nu > 0) & (nd > 0), up * (0.5 / np.maximum(nu, 1)) - down * (0.5 / np.maximum(nd, 1)), 0.0)
        self._book = pd.DataFrame(w, index=px.index, columns=px.columns)

    def walk(self, prices: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
        out = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        cols = [c for c in self.market if c in prices.columns]
        out[cols] = self._book.reindex(index=prices.index, columns=cols).fillna(0.0).to_numpy()
        return out, []

    def entry(self, prices):
        return np.sign(self.walk(prices)[0]).astype(int)

    def exit(self, prices, side):
        return np.sign(self.walk(prices)[0]) != side

    def size(self, prices):
        return self.walk(prices)[0].abs()


# ---- one book through the real gate

def _moments(x: np.ndarray) -> dict | None:
    try:
        m = research.sharpe_moments(x)
    except ValueError:
        return None
    return {"sharpe": m.sharpe, "n_obs": m.n_obs, "skew": m.skew, "kurt": m.kurt}


def _gate_run(frame, dollar, cost, criteria, folder: Path, strategy: Strategy, priors: Sequence[float]) -> dict:
    """The real gate on one book, in a ledger of its own (seeded with `priors`): develop, and seal where it passed."""
    paths = LoopPaths(folder).ensure()
    ledger = HypothesisLedger(paths.ledger)
    try:
        controls._seed_priors(ledger, priors)
        g = gt.Gauntlet(frame, dollar, cost, ledger, paths, criteria)
        hid = ledger.propose(strategy.hypothesis, "gate_study", controls.MARKET)
        gt.write_prediction(ledger, paths, hid, strategy.name, controls.NEUTRAL_PREDICTION)
        try:
            rep = g.develop(hid, strategy)
        except gt.GauntletError as exc:
            return {"refused": True, "error": str(exc)[:200]}
        own = [float(x) for x in ledger.attempt_sharpes(hypothesis_id=hid)]
        out = {"refused": False, "report": rep, "own": own, "dev_pass": rep["verdict"].startswith("pass"),
               "sealed": None, "sealed_pass": False}
        if out["dev_pass"]:
            s = g.seal(hid, strategy)
            out["sealed"] = {k: bool(v["passed"]) for k, v in s["checks"].items()}
            out["sealed_pass"] = s["verdict"] == "pass"
        return out
    finally:
        ledger.close()


class _Book:
    """One book: the gate's run and the material every rule is judged from."""

    def __init__(self, frame: pd.DataFrame, dollar, cost: CostModel, criteria: gt.Criteria, folder: Path,
                 strategy: Strategy):
        self.frame, self.dollar, self.cost, self.criteria, self.strategy = frame, dollar, cost, criteria, strategy
        self.cut = int(len(frame) * (1.0 - criteria.sealed_fraction))
        self.gate = _gate_run(frame, dollar, cost, criteria, folder, strategy, ())
        self.material: dict = {"refused": self.gate["refused"], "strict_p": {}, "sealed": None}
        self.mismatches: list[str] = []
        self.r3_seconds: dict[int, float] = {}
        if self.gate["refused"]:
            self.material.update(error=self.gate["error"], checks={}, own=[], moments=None, active_moments=None,
                                 timing_p=None, development_sharpe=None)
            return
        rep = self.gate["report"]
        timing = rep["breaker"].get("timing_null") or {}
        self.material.update(checks={k: bool(v["passed"]) for k, v in rep["checks"].items()}, own=self.gate["own"],
                             timing_p=timing.get("p_value"), development_sharpe=rep["result"].get("sharpe"))
        self._rebuild(rep)

    def _rebuild(self, rep: dict) -> None:
        """The out-of-sample net returns and book, with the calls `develop` makes for one candidate."""
        c = self.criteria
        dev = self.frame.iloc[: self.cut]
        dollar = None if self.dollar is None else self.dollar.iloc[: self.cut]
        w, _ = run_rules(self.strategy, dev)
        windows = [idx for idx in np.array_split(np.arange(c.warmup_days, len(dev)), c.walk_forward_windows) if len(idx)]
        stitched = pd.DataFrame(0.0, index=dev.index, columns=dev.columns)
        for idx in windows:
            stitched.iloc[idx] = w.iloc[idx].to_numpy()
        oos = slice(windows[0][0], None)
        base = run_backtest(dev, stitched, self.cost, dollar)
        net = base.net.iloc[oos]
        self._w, self._r = base.weights.iloc[oos], dev.pct_change().iloc[oos]
        self.material["moments"] = _moments(net.dropna().to_numpy(dtype=float))
        self.material["active_moments"] = _moments(self._active(net).to_numpy(dtype=float))
        # the rebuilt material must give back the report's own numbers, exactly
        if deflated_value(self.material["moments"], self.material["own"], ()) != rep["deflated_sharpe"]["value"]:
            self.mismatches.append("deflated_sharpe")
        if gt._stats(net).get("sharpe") != rep["result"].get("sharpe") and not (
                _nan(gt._stats(net).get("sharpe")) and _nan(rep["result"].get("sharpe"))):
            self.mismatches.append("headline_sharpe")
        if gt.timing_null(self._w, self._r).get("p_value") != self.material["timing_p"]:
            self.mismatches.append("timing_p_value")

    def _active(self, net: pd.Series) -> pd.Series:
        """Net return minus the return of the same average book held throughout (timing_null's definition)."""
        w, r = self._w, self._r
        cols = [x for x in w.columns if x in r.columns and float(w[x].abs().sum()) > 0]
        net = net.dropna()
        if not cols:
            return net
        book = np.abs(w[cols].to_numpy(float)).mean(axis=0) * np.sign(w[cols].to_numpy(float).sum(axis=0))
        held = r[cols].fillna(0.0).to_numpy(float) @ book
        return net - pd.Series(held, index=r.index).reindex(net.index)

    def strict_p(self, n_attempts: int) -> float | None:
        if n_attempts not in self.material["strict_p"]:
            t = time.time()
            out = gt.timing_null(self._w, self._r, n=r3_shifts(n_attempts))
            self.material["strict_p"][n_attempts] = out.get("p_value")
            self.r3_seconds[n_attempts] = time.time() - t
        return self.material["strict_p"][n_attempts]

    def sealed(self) -> dict:
        """The sealed checks, with the calls `seal` makes (all prices, then the sealed slice)."""
        if self.material["sealed"] is None:
            w, events = run_rules(self.strategy, self.frame)
            res = run_backtest(self.frame, w, self.cost, self.dollar)
            sl = slice(self.cut, None)
            result = gt._stats(res.net.iloc[sl], res.turnover.iloc[sl], res.costs.iloc[sl])
            p = controls.NEUTRAL_PREDICTION
            sharpe, dd, total = (result.get(k, float("nan")) for k in ("sharpe", "max_drawdown", "total_return"))
            self.material["sealed"] = {"made_money": bool(total > 0),
                                       "prediction_failure_terms": bool(sharpe >= p.fail_if_sharpe_below
                                                                        and dd <= p.fail_if_drawdown_above)}
        return self.material["sealed"]

    def judge(self, others: Sequence[float]) -> dict[str, bool]:
        """Every rule at these other attempts, computing the lazy material (strict p, sealed checks) as needed."""
        m, c = self.material, self.criteria
        if m["refused"]:
            return dict.fromkeys(RULES, False)
        if _r3_rest(m, c):
            self.strict_p(attempts_counted(m, others))
        dev = {rule: develops(rule, m, others, c) for rule in RULES}
        if any(dev.values()):
            self.sealed()
        return {rule: dev[rule] and sealed_ok(m) for rule in RULES}


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and not math.isfinite(x))


# ---- the study

def _rng(seed: int, *parts) -> np.random.Generator:
    return np.random.default_rng([int(seed), *[_CODES[p] if isinstance(p, str) else int(p) for p in parts]])


def _surrogate_books(base: pd.DataFrame, flat: bool, seed: int, j: int, specs_per_surrogate: int) -> dict:
    """Surrogate `j` of `base` (detrended again when `flat`) and its drawn rule strategies, identical books counted
    once: a spec drawn again (the same rule and numbers; the hold rule has none) is dropped, and so is a different
    spec whose whole-history book is the same as one kept (`controls._fingerprint_weights`). The first draw is kept."""
    frame = surrogate_prices(base, _rng(seed, "surrogate", j))
    if flat:
        frame = controls.detrend(frame)
    spec_rng = _rng(seed, "spec", j)
    specs, prints, books = set(), set(), []
    out = {"frame": frame, "books": books, "duplicate_specs_dropped": 0, "duplicate_books_dropped": 0}
    for i in range(specs_per_surrogate):
        spec = draw_rule_spec(frame.columns, spec_rng, i)
        key = json.dumps({k: v for k, v in spec.items() if k not in ("name", "hypothesis")}, sort_keys=True)
        if key in specs:
            out["duplicate_specs_dropped"] += 1
            continue
        specs.add(key)
        strategy = RuleStrategy.from_spec(spec)
        fp = controls._fingerprint_weights(run_rules(strategy, frame)[0])
        if fp in prints:
            out["duplicate_books_dropped"] += 1
            continue
        prints.add(fp)
        books.append((j * specs_per_surrogate + i, strategy))
    return out


def _books(fam: str, base: pd.DataFrame, version: str, seed: int, n_null: int, n_edge: int, n_surrogates: int,
           specs_per_surrogate: int, dropped: dict | None = None):
    """(cell key, book number, frame, strategy, surrogate number) for every book of a family on one data version.
    For N4 the duplicates dropped are added up in `dropped`."""
    kind, rates, horizon, _ = FAMILIES[fam]
    flat = version == "detrended"
    if kind == "oracle" or kind == "neutral":
        per = n_null if fam in NO_SKILL else n_edge
        for h in rates:
            for k in range(per):
                rng = _rng(seed, fam, round(h * 10000), k)
                s = (controls.OracleCheat(base, h, horizon, rng, name="study_oracle") if kind == "oracle"
                     else NeutralOracle(base, h, horizon, rng))
                yield h, k, base, s, None
    elif kind == "no_edge":
        for k in range(n_null):
            yield None, k, base, controls.NoEdgeBook(base, _rng(seed, fam, k), name="study_no_edge"), None
    else:
        for j in range(n_surrogates):
            sur = _surrogate_books(base, flat, seed, j, specs_per_surrogate)
            if dropped is not None:
                for key in ("duplicate_specs_dropped", "duplicate_books_dropped"):
                    dropped[key] = dropped.get(key, 0) + sur[key]
            for k, strategy in sur["books"]:
                yield None, k, sur["frame"], strategy, j


def _cell_report(fam: str, h, verdicts: list[dict], counts: list[int], sharpes: list, refused: int,
                 surrogate_of: list, *, dropped: dict | None = None, seed: int = 0, version: str = "given") -> dict:
    """One cell's counts. For N4 also, per rule and count: the surrogates with a pass, each surrogate's own pass share
    (minimum, median, maximum), and `surrogate_bootstrap`'s interval (the same seeded resamples for every rule and
    count of the cell); and the cell's surrogates and dropped duplicates."""
    kind, _, horizon, _ = FAMILIES[fam]
    out: dict = {"hit_rate": h, "horizon": horizon, "runs": len(verdicts), "refused": refused,
                 "median_development_sharpe": _median(sharpes), "by_attempts": {}}
    clusters = sorted({s for s in surrogate_of if s is not None})
    if kind == "surrogate_rules":
        out.update(surrogates=len(clusters), **{k: int((dropped or {}).get(k, 0))
                                                for k in ("duplicate_specs_dropped", "duplicate_books_dropped")})
    for n in counts:
        row = {}
        for rule in RULES:
            k = sum(v[n][rule] for v in verdicts)
            row[rule] = {"passes": k, "runs": len(verdicts), "share": k / len(verdicts) if verdicts else None,
                         "wilson_95": wilson(k, len(verdicts))}
            if kind == "surrogate_rules":
                per_p = [sum(v[n][rule] for v, s in zip(verdicts, surrogate_of) if s == j) for j in clusters]
                per_b = [sum(1 for s in surrogate_of if s == j) for j in clusters]
                shares = [a / b for a, b in zip(per_p, per_b)]
                row[rule].update(
                    surrogates_with_a_pass=sum(a > 0 for a in per_p),
                    per_surrogate_share={"min": min(shares), "median": float(np.median(shares)), "max": max(shares)},
                    surrogate_bootstrap_95=surrogate_bootstrap(per_p, per_b, _rng(seed, "cluster", _VERSIONS.index(version))))
        out["by_attempts"][str(n)] = row
    return out


def _median(xs):
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    return float(np.median(xs)) if xs else None


def study(prices: pd.DataFrame, dollar_volume: pd.DataFrame | None, cost: CostModel, *,
          prior_attempt_sharpes: Sequence[float] = (), n_null: int = 400, n_edge: int = 100, n_surrogates: int = 50,
          specs_per_surrogate: int = 20, seed: int = 20261116, criteria: gt.Criteria | None = None) -> dict:
    """Run every book family through the real gate, on the data as given and detrended, judge every candidate rule at
    one attempt and at len(prior_attempt_sharpes) + 1, and return the report (strict JSON)."""
    if min(n_null, n_edge, n_surrogates, specs_per_surrogate) < 1:
        raise ValueError("n_null, n_edge, n_surrogates and specs_per_surrogate must be at least 1")
    priors = [float(s) for s in prior_attempt_sharpes]
    if not all(math.isfinite(s) for s in priors):
        raise ValueError("prior attempt Sharpes must be finite numbers")
    c = criteria or gt.Criteria()
    counts = [1] + ([len(priors) + 1] if priors else [])
    others_at = {1: [], len(priors) + 1: priors}
    results: dict = {}
    anchor = {"books": 0, "development_disagreements": 0, "sealed_compared": 0, "sealed_disagreements": 0,
              "material_mismatches": 0, "mismatch_kinds": {}, "at_prior_attempts_books": 0,
              "at_prior_attempts_disagreements": 0}
    r3_seconds: dict[int, list[float]] = {}
    done, t0 = 0, time.time()
    with warnings.catch_warnings(), tempfile.TemporaryDirectory(prefix="valedia-gate-study-") as tmp:
        warnings.simplefilter("ignore", FutureWarning)               # statsmodels' adfuller notice, in diagnostics
        root = Path(tmp)
        for version in _VERSIONS:
            base = prices if version == "given" else controls.detrend(prices)
            results[version] = {}
            for fam in FAMILIES:
                cells: dict = {}
                dropped: dict = {}
                for h, k, frame, strategy, sur in _books(fam, base, version, seed, n_null, n_edge, n_surrogates,
                                                         specs_per_surrogate, dropped):
                    folder = root / f"book{done:06d}"
                    book = _Book(frame, dollar_volume, cost, c, folder, strategy)
                    verdicts = {n: book.judge(others_at[n]) for n in counts}
                    for n, secs in book.r3_seconds.items():
                        r3_seconds.setdefault(n, []).append(secs)
                    _anchor_book(anchor, book, verdicts)
                    if priors and k == 0:
                        _anchor_priors(anchor, book, frame, dollar_volume, cost, c, root / f"prior{done:06d}", priors,
                                       verdicts[counts[-1]])
                    cell = cells.setdefault(h, {"verdicts": [], "sharpes": [], "refused": 0, "surrogate": []})
                    cell["verdicts"].append(verdicts)
                    cell["sharpes"].append(book.material.get("development_sharpe"))
                    cell["refused"] += int(book.material["refused"])
                    cell["surrogate"].append(sur)
                    shutil.rmtree(folder, ignore_errors=True)
                    done += 1
                    if done % PROGRESS_EVERY == 0:
                        print(f"gate_study: {done} books in {time.time() - t0:.0f} s", file=sys.stderr, flush=True)
                results[version][fam] = [_cell_report(fam, h, v["verdicts"], counts, v["sharpes"], v["refused"],
                                                      v["surrogate"], dropped=dropped, seed=seed, version=version)
                                         for h, v in cells.items()]
    elapsed = time.time() - t0
    timed = ", ".join(f"{_median(v):.2f} s at N = {n} ({r3_shifts(n)} shifts, {len(v)} books)"
                      for n, v in sorted(r3_seconds.items()))
    print(f"gate_study: finished: {done} books, {elapsed:.0f} s, {elapsed / max(done, 1):.2f} s per book; "
          f"R3 re-timing median {timed or 'not needed'}", file=sys.stderr, flush=True)
    report = {
        "rules": _RULE_TEXT, "families": {f: d for f, (_, _, _, d) in FAMILIES.items()},
        "attempt_counts": counts, "prior_attempts": len(priors),
        "sizes": {"n_null": n_null, "n_edge": n_edge, "n_surrogates": n_surrogates,
                  "specs_per_surrogate": specs_per_surrogate, "seed": int(seed)},
        "results": results, "anchor": anchor,
        "r3_re_timing": {"shifts": {str(n): r3_shifts(n) for n in counts},
                         "books_re_timed": {str(n): len(v) for n, v in sorted(r3_seconds.items())}},
        "surrogates": {"mean_block": MEAN_BLOCK, "complete_rows_in_pool": int(_complete_rows(prices)),
                       "draw_ranges": DRAW_RANGES, "sizing": SIZING, "risk": "the default StrategyRisk()"},
        "books": done,
        "data": {"symbols": int(prices.shape[1]), "first": str(prices.index[0].date()),
                 "last": str(prices.index[-1].date())},
    }
    report["reading"] = _reading(report)
    return gt._clean(report)                                    # timings go to stderr only: the report is deterministic


def _complete_rows(prices: pd.DataFrame) -> int:
    px = prices.to_numpy(dtype=float)
    ok = np.isfinite(px) & (px > 0)
    return int((ok[1:] & ok[:-1]).all(axis=1).sum())


def _anchor_book(anchor: dict, book: _Book, verdicts: dict) -> None:
    """R0 at one attempt against the gate's own fresh-ledger run, and the rebuilt material against its report."""
    anchor["books"] += 1
    m, g = book.material, book.gate
    if g["refused"]:
        return
    if develops("R0", m, (), book.criteria) != g["dev_pass"]:
        anchor["development_disagreements"] += 1
    if g["dev_pass"]:
        anchor["sealed_compared"] += 1
        if book.sealed() != g["sealed"] or verdicts[1]["R0"] != g["sealed_pass"]:
            anchor["sealed_disagreements"] += 1
    if book.mismatches:
        anchor["material_mismatches"] += 1
        for k in book.mismatches:
            anchor["mismatch_kinds"][k] = anchor["mismatch_kinds"].get(k, 0) + 1


def _anchor_priors(anchor, book, frame, dollar, cost, criteria, folder: Path, priors, verdict: dict) -> None:
    """The real gate again, in a ledger seeded with the priors, against R0 judged at len(priors) + 1."""
    g = _gate_run(frame, dollar, cost, criteria, folder, book.strategy, priors)
    shutil.rmtree(folder, ignore_errors=True)
    anchor["at_prior_attempts_books"] += 1
    gate_pass = (not g["refused"]) and g["dev_pass"] and g["sealed_pass"]
    if gate_pass != verdict["R0"]:
        anchor["at_prior_attempts_disagreements"] += 1


_RULE_TEXT = {
    "R0": "the gate as it is today",
    "R1": "R0 and the re-timing p-value (300 shifts) at most 0.05",
    "R2": "R1, with the variance of attempt Sharpes taken from the other attempts only (fewer than 3: 1 / n_obs)",
    "R3": "the Sharpe test for one trial, and the re-timing p-value at most 0.05 / N from 400 N shifts",
    "R4": "the Deflated Sharpe on active returns (net minus the same average book held throughout), N as today, "
          "the idea's own attempt left out of the variance",
}


def _fmt_share(r: dict) -> str:
    lo, hi = r["wilson_95"]
    return f"{r['passes']} of {r['runs']} ({r['share']:.3f}, 95% interval {lo:.3f} to {hi:.3f})"


def _fmt_clustered(r: dict, surrogates: int) -> str:
    """N4: the interval from the bootstrap over whole surrogates, named as such."""
    head = f"{r['passes']} of {r['runs']} ({r['share']:.3f}, "
    if r["surrogate_bootstrap_95"] is None:
        return head + f"no interval: {surrogates} surrogate, and books on one surrogate are not independent)"
    lo, hi = r["surrogate_bootstrap_95"]
    return head + (f"95% interval {lo:.3f} to {hi:.3f} from a bootstrap over {surrogates} whole surrogates, "
                   "not the Wilson interval)")


def _lower(r: dict, fam: str) -> float | None:
    """The lower end of the interval the reading judges a family by: the surrogate bootstrap for N4, else Wilson."""
    iv = r.get("surrogate_bootstrap_95") if FAMILIES[fam][0] == "surrogate_rules" else r["wilson_95"]
    return None if iv is None else iv[0]


def _reading(report: dict) -> list[str]:
    """Plain sentences generated from the numbers."""
    out, above = [], {}
    words = {"given": "the data as given", "detrended": "the detrended data"}
    for rule in RULES:
        for version in _VERSIONS:
            for n in report["attempt_counts"]:
                att = "1 attempt" if n == 1 else f"{n} attempts"
                for fam in NO_SKILL:
                    cell = report["results"][version][fam][0]
                    r = cell["by_attempts"][str(n)][rule]
                    text = _fmt_clustered(r, cell["surrogates"]) if fam == "N4" else _fmt_share(r)
                    out.append(f"{rule} false-pass share on {fam} ({words[version]}, {att}): {text}.")
                    lower = _lower(r, fam)
                    if lower is not None and lower > ALPHA:
                        above.setdefault(rule, []).append(f"{fam} {version} {att}")
                for fam in EDGE:
                    cells = report["results"][version][fam]
                    ok = [c["hit_rate"] for c in cells
                          if c["by_attempts"][str(n)][rule]["share"] is not None
                          and c["by_attempts"][str(n)][rule]["share"] >= POWER_SHARE]
                    out.append(f"{rule} on {fam} ({words[version]}, {att}): smallest hit rate passing at least 80% of "
                               "books: " + (f"{min(ok):.2f}." if ok else "none of " + ", ".join(
                                   "{:.2f}".format(c["hit_rate"]) for c in cells) + "."))
    if above:
        out.append("Rules whose false-pass interval (for N4 the bootstrap over whole surrogates) lies wholly above 0.05 "
                   "on at least one no-skill family: "
                   + "; ".join(f"{rule} ({', '.join(v)})" for rule, v in above.items()) + ".")
    else:
        out.append("No rule has a false-pass interval lying wholly above 0.05 on any no-skill family.")
    a = report["anchor"]
    bad = (a["development_disagreements"] + a["sealed_disagreements"] + a["material_mismatches"]
           + a["at_prior_attempts_disagreements"])
    out.append(f"Anchor: R0 judged from the kept material agreed with the real gate on {a['books']} books at one attempt "
               f"and {a['at_prior_attempts_books']} at the prior attempts, with {bad} disagreements"
               + (" (the study is void)." if bad else "."))
    return out


# ---- command line

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="valedia_trader.research_loop.gate_study", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="folder with close.parquet and volume.parquet")
    ap.add_argument("--prior-from-ledger", default=None, help="ledger to read attempt Sharpes from (opened read-only)")
    ap.add_argument("--market", default=None, help="market label of those attempts (default: the research command's)")
    ap.add_argument("--n-null", type=int, default=400)
    ap.add_argument("--n-edge", type=int, default=100)
    ap.add_argument("--n-surrogates", type=int, default=50)
    ap.add_argument("--specs-per-surrogate", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20261116)
    args = ap.parse_args(argv)
    from valedia_trader.research_loop import cli
    prices, dollar, cost = cli._market(argparse.Namespace(data=args.data))        # the research command's cost model
    priors = (controls.read_prior_attempt_sharpes(args.prior_from_ledger, args.market or cli.MARKET)
              if args.prior_from_ledger else ())
    rep = study(prices, dollar, cost, prior_attempt_sharpes=priors, n_null=args.n_null, n_edge=args.n_edge,
                n_surrogates=args.n_surrogates, specs_per_surrogate=args.specs_per_surrogate, seed=args.seed)
    print(json.dumps(rep, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
