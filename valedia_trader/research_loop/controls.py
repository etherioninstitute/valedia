"""Controls for the gauntlet: an edge it must pass, noise it must fail, and the smallest edge it lets through.

Every control is run as a real idea would be: a prediction is written first, the REAL `Gauntlet.develop` runs, and
only where development passed the REAL `Gauntlet.seal` runs once. Nothing here is a shortcut around either.

Everything happens inside one `tempfile.TemporaryDirectory()`. Every run (each rung of the ladder and each seed)
gets its own fresh ledger and its own folders there, because the gauntlet counts every run as an attempt and the
Deflated Sharpe bar rises with that count: a control that wrote to the real ledger would make every real idea
harder to pass. `run_controls` takes no path, so it cannot be pointed at the real ledger. When the real bar is
wanted, the attempt Sharpes are passed in as numbers (`prior_attempt_sharpes`); the command line reads them from a
ledger opened read-only.

The controls:

* the ORACLE at hit rate h and horizon H. It carries its own copy of the whole price history. Every H bars, for each
  symbol, it knows the sign of the return a decision on that bar earns over the next H bars under the gauntlet's own
  execution lag; with probability h (seeded per symbol and block) it uses the true sign, otherwise the opposite. It
  holds the positive ones long, equal weights (1/number of symbols, so gross at most 1), for the whole block. h = 0.5
  is noise; the REFERENCE EDGE (h = 0.60, H = 5) is the control that must pass.
* the SINGLE-BAR EDGE: the oracle at H = 1, h = 1.00. Reported, not required: the delayed-fills check of the breaker
  shifts every fill by one more bar, which removes an edge that lives in one bar entirely.
* the NO-EDGE book: each block of a random 1 to 10 bars, a random half of the symbols long and the rest short, equal
  weights, gross 1. Expected return before costs is zero.
* the FRAME-PEEKING cheat: the oracle's rule computed from later rows of the frame it is handed. The look-ahead
  detector must flag it and `develop` must refuse it. The oracle carries its future instead and is NOT flagged: that
  is the detector's blind spot, measured and reported, not assumed.

Every control with a pass count also runs on DETRENDED prices (`detrend`: each symbol's average drift removed), so
a pass that came from the market's own rise can be told from one that came from timing. Each rung also reports
the gate's own re-timing test (`timing_null` in the development breaker): it is reported, not a gate, and is the
measurement a later gate change would rest on. `reading` says the main results in plain sentences.

All controls are ordinary `Strategy` subclasses walked by `run_rules` (no dependency on a strategy's own `walk`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from valedia_trader.backtest.engine import CostModel, run_backtest
from valedia_trader.quant_engine import level10_ml_research as research
from valedia_trader.research_loop import LoopPaths
from valedia_trader.research_loop import gauntlet as gt
from valedia_trader.research_loop.ledger import HypothesisLedger
from valedia_trader.research_loop.strategy import CRYPTO, Strategy, StrategyRisk, run_rules

__all__ = ["run_controls", "detrend", "smallest_passing_hit_rate", "OracleCheat", "FramePeekingCheat", "NoEdgeBook",
           "NEUTRAL_PREDICTION", "read_prior_attempt_sharpes", "main"]

MARKET = "controls"                  # market label inside the temporary ledgers
REFERENCE_HIT_RATE = 0.60
PASS_SHARE = 0.8                     # "at least 4 of 5 seeds"
SMALLEST_UP_TO = 0.70                # smallest_passing_hit_rate considers only rungs up to this one
NEUTRAL_PREDICTION = gt.Prediction(annual_return=0.0, win_rate=0.5, sharpe=0.0, fail_if_sharpe_below=0.0,
                                   fail_if_drawdown_above=1.0,
                                   notes="control run: neutral terms, so only the gate's own checks decide")
_LAG = 1                             # the execution lag of the gauntlet's main and sealed runs (engine default)
_KIND = {"oracle": 1, "no_edge": 2, "peek": 3}


def detrend(prices: pd.DataFrame) -> pd.DataFrame:
    """The same prices with each symbol's average drift removed: its log returns (between consecutive prices it
    has) have their own mean over the WHOLE frame subtracted, and the prices are rebuilt from the same first price.
    Volatility, correlation and missing values are those of the data; only the average drift is gone.

    This uses the whole period on purpose: the result is a control universe for measuring the gate, never an input
    a strategy decides on."""
    out = pd.DataFrame(np.nan, index=prices.index, columns=prices.columns)
    for c in prices.columns:
        col = prices[c].to_numpy(dtype=float)
        ok = np.flatnonzero(np.isfinite(col) & (col > 0))
        if len(ok) == 0:
            continue
        lr = np.diff(np.log(col[ok]))
        if len(lr):
            lr = lr - lr.mean()
        out.iloc[ok, out.columns.get_loc(c)] = col[ok[0]] * np.exp(np.concatenate([[0.0], np.cumsum(lr)]))
    return out


def _risk(m: int) -> StrategyRisk:
    return StrategyRisk(max_position_pct=1.0 / m, max_open_positions=m, max_gross=1.0, stop_loss_pct=None)


def _block_signs(px: pd.DataFrame, horizon: int, flip: np.ndarray) -> np.ndarray:
    """Per bar, the (possibly flipped) sign of the return its block's decision earns over `horizon` bars.
    Blocks start at row 0 of `px`. Unknown signs (past the end of `px`) are 0."""
    p = px.to_numpy(dtype=float)
    n = len(p)
    sign = np.zeros(p.shape)
    for b in range(0, n, horizon):
        end, start = b + horizon + _LAG, b + _LAG
        if end < n:
            s = np.sign(p[end] / p[start] - 1.0)
            sign[b:b + horizon] = np.where(flip[b // horizon], -s, s)
    return np.nan_to_num(sign)


class _BlockBook(Strategy):
    """Long the symbols whose block sign is positive, 1/m each; entries and exits through the ordinary rules."""
    rebalance_days = None

    def _long(self, px: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    def entry(self, prices: pd.DataFrame) -> pd.DataFrame:
        return self._long(prices).astype(int)

    def exit(self, prices: pd.DataFrame, side: int) -> pd.DataFrame:
        return ~self._long(prices)

    def size(self, prices: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(1.0 / len(self.market), index=prices.index, columns=prices.columns)


class OracleCheat(_BlockBook):
    """Carries its own copy of the full history: its book at a date does not depend on the frame it is handed."""

    def __init__(self, full_prices: pd.DataFrame, hit_rate: float, horizon: int, rng: np.random.Generator,
                 name: str = "control_oracle"):
        if not 0.0 <= hit_rate <= 1.0 or horizon < 1:
            raise ValueError("hit_rate in [0, 1] and horizon >= 1")
        self.name, self.version = name, "1.0.0"
        self.hypothesis = f"An oracle knows the sign of the next {horizon}-bar return with hit rate {hit_rate:.2f}"
        self.market = tuple(full_prices.columns)
        self.risk = _risk(len(self.market))
        self.hit_rate, self.horizon = float(hit_rate), int(horizon)
        n, m = full_prices.shape
        flip = rng.random((-(-n // horizon), m)) >= hit_rate
        self._book = pd.DataFrame(_block_signs(full_prices, horizon, flip) > 0, index=full_prices.index,
                                  columns=full_prices.columns)

    def _long(self, px):
        return self._book.reindex(index=px.index, columns=px.columns).fillna(False).astype(bool)


class FramePeekingCheat(_BlockBook):
    """The same rule computed from later rows of the frame it is handed: what a careless strategy file would do."""

    def __init__(self, columns: Sequence[str], hit_rate: float, horizon: int, rng: np.random.Generator,
                 max_rows: int, name: str = "control_frame_peeking"):
        self.name, self.version = name, "1.0.0"
        self.hypothesis = f"A rule reads the next {horizon}-bar return from the frame it is given"
        self.market = tuple(columns)
        self.risk = _risk(len(self.market))
        self.horizon = int(horizon)
        self._flip = rng.random((-(-max_rows // horizon), len(self.market))) >= hit_rate

    def _long(self, px):
        return pd.DataFrame(_block_signs(px, self.horizon, self._flip) > 0, index=px.index, columns=px.columns)


class NoEdgeBook(Strategy):
    """A seeded random book with no information: each block of a random 1 to 10 bars, a random half of the symbols
    long and the rest short (for an odd count the extra one is long or short at random), 1/m each, gross 1.

    It trades only the symbols the rules let it hold on both sides: `run_rules` never shorts spot crypto, so a
    crypto symbol in the book would be long half the time and flat the rest, a long bias that earns crypto's drift
    and is not zero before costs (measured: development Sharpe 0.08 to 0.59 on all five seeds of one universe).

    It goes through the ordinary rules, so a symbol whose side flips at a block change is closed on that bar and
    reopened on the next one (`run_rules` never reopens a position on the bar it closed): one flat bar per flip."""
    rebalance_days = None

    def __init__(self, full_prices: pd.DataFrame, rng: np.random.Generator, name: str = "control_no_edge"):
        self.name, self.version = name, "1.0.0"
        self.hypothesis = "A random long and short book with no information at all"
        self.market = tuple(c for c in full_prices.columns if c not in CRYPTO)
        if not self.market:
            raise ValueError("the no-edge book needs at least one symbol that can be shorted")
        full_prices = full_prices[list(self.market)]
        n, m = full_prices.shape
        self.risk = _risk(m)
        book, t = np.zeros((n, m)), 0
        while t < n:
            hold = int(rng.integers(1, 11))
            side = np.where(rng.permutation(m) < m // 2, 1.0, -1.0)
            if m % 2 and rng.random() < 0.5:
                side = -side
            book[t:t + hold] = side
            t += hold
        self._side = pd.DataFrame(book, index=full_prices.index, columns=full_prices.columns)

    def _sides(self, px):
        return self._side.reindex(index=px.index, columns=px.columns).fillna(0.0)

    def entry(self, prices):
        return self._sides(prices).astype(int)

    def exit(self, prices, side):
        return self._sides(prices) != side

    def size(self, prices):
        return pd.DataFrame(1.0 / len(self.market), index=prices.index, columns=prices.columns)


# ---- running one control as a real idea

def _fresh_ledger(paths: LoopPaths) -> HypothesisLedger:
    return HypothesisLedger(paths.ledger)


class _Context:
    def __init__(self, prices, dollar_volume, cost, criteria, tmp: Path, seed: int):
        self.prices, self.dollar, self.cost, self.criteria, self.tmp, self.seed = prices, dollar_volume, cost, criteria, tmp, seed
        self.count = 0
        self.cut = int(len(prices) * (1.0 - (criteria or gt.Criteria()).sealed_fraction))

    def rng(self, kind: str, *parts: int) -> np.random.Generator:
        return np.random.default_rng([int(self.seed), _KIND[kind], *[int(p) for p in parts]])

    def new_paths(self) -> LoopPaths:
        self.count += 1
        return LoopPaths(self.tmp / f"run{self.count:04d}").ensure()


def _seed_priors(ledger: HypothesisLedger, priors: Sequence[float]) -> None:
    """Put the given attempt Sharpes into the ledger as attempts on the controls' market label, under one
    placeholder idea, through the ledger's own `record_attempt`. `Gauntlet._deflated` then counts them in
    `attempt_sharpes(market=...)`: the market count N and, once there are 3 or more, the variance of trial Sharpes."""
    if not priors:
        return
    hid = ledger.propose("Placeholder holding the attempts already made on the real market", "controls", MARKET)
    for s in priors:
        ledger.record_attempt(hid, "backtest", float(s), None, "0.0.0", {"variant": "prior"})


def _fingerprint_weights(w: pd.DataFrame) -> str:
    return hashlib.sha256(np.ascontiguousarray(np.round(w.to_numpy(dtype=float), 12)).tobytes()).hexdigest()[:16]


def _full_run(ctx: _Context, strategy: Strategy) -> dict:
    """The same full-history run the sealed test makes (gauntlet.py: run_rules then run_backtest on all prices),
    read for the equity multiple at the first sealed bar, impact's share of sealed gross return, and a fingerprint
    of the book (so seeds that claim to differ can be shown to differ)."""
    w, _ = run_rules(strategy, ctx.prices)
    res = run_backtest(ctx.prices, w, ctx.cost, ctx.dollar)
    dev_net = res.net.iloc[: ctx.cut].to_numpy(dtype=float)
    log_mult = float(np.log1p(np.clip(dev_net, -0.999999, None)).sum())
    gross = float(res.gross.iloc[ctx.cut:].sum())
    impact = float(res.costs["impact"].iloc[ctx.cut:].sum())
    return {"equity_multiple_end_of_development": math.exp(log_mult) if log_mult < 700 else None,
            "log10_equity_multiple_end_of_development": log_mult / math.log(10.0),
            "sealed_impact_share_of_gross": impact / gross if gross > 0 else None,
            "book_fingerprint": _fingerprint_weights(w)}


def _run_one(ctx: _Context, strategy: Strategy, priors: Sequence[float] = ()) -> dict:
    paths = ctx.new_paths()
    ledger = _fresh_ledger(paths)
    try:
        _seed_priors(ledger, priors)
        before = len(ledger.attempt_sharpes(market=MARKET))
        g = gt.Gauntlet(ctx.prices, ctx.dollar, ctx.cost, ledger, paths, ctx.criteria)
        hid = ledger.propose(strategy.hypothesis, "controls", MARKET)
        gt.write_prediction(ledger, paths, hid, strategy.name, NEUTRAL_PREDICTION)
        out: dict = {"attempts_on_market_before": before}
        try:
            rep = g.develop(hid, strategy)
        except gt.GauntletError as exc:
            out.update(refused=True, error=str(exc)[:200], attempts_recorded=ledger.variations(hid))
            return out
        dsr = rep["deflated_sharpe"]
        timing = rep["breaker"].get("timing_null") or {}               # develop reports it for passed and failed runs
        out.update(refused=False, passed_development=rep["verdict"].startswith("pass"), dsr=dsr["value"],
                   net_sharpe=rep["result"].get("sharpe"),
                   failed=[k for k, c in rep["checks"].items() if not c["passed"]],
                   delayed_fills_sharpe=rep["breaker"]["delayed_fills"]["sharpe"],
                   market_basket_sharpe=rep["breaker"]["market_basket"].get("sharpe"),
                   timing_p_value=timing.get("p_value"),
                   variations_of_idea=dsr["variations_of_idea"], attempts_on_market=dsr["attempts_on_market"],
                   variance_of_attempt_sharpes=dsr.get("variance_of_attempt_sharpes"),
                   sealed_ran=False, passed_sealed=False, sealed_sharpe=None, sealed_failed=None)
        if out["passed_development"]:
            s = g.seal(hid, strategy)
            out.update(sealed_ran=True, passed_sealed=s["verdict"] == "pass", sealed_sharpe=s["result"].get("sharpe"),
                       sealed_failed=[k for k, c in s["checks"].items() if not c["passed"]])
        full = _full_run(ctx, strategy)
        if not out["sealed_ran"]:
            full["sealed_impact_share_of_gross"] = None                 # describes a sealed run that did not happen
        out.update(full)
        return out
    finally:
        ledger.close()


def _median(xs):
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    return float(np.median(xs)) if xs else None


def _timed(r: dict) -> bool:
    p = r.get("timing_p_value")
    return p is not None and math.isfinite(p) and p <= 0.05


def _summary(runs: list[dict]) -> dict:
    failed: dict[str, int] = {}
    for r in runs:
        for f in r.get("failed") or (["refused"] if r.get("refused") else []):
            failed[f] = failed.get(f, 0) + 1
    return {"runs": len(runs),
            "passed_development": sum(bool(r.get("passed_development")) for r in runs),
            "passed_sealed": sum(bool(r.get("passed_sealed")) for r in runs),
            "median_timing_p_value": _median([r.get("timing_p_value") for r in runs]),
            "timing_p_at_most_0_05": sum(_timed(r) for r in runs),
            "passed_both_and_timing": sum(bool(r.get("passed_sealed")) and _timed(r) for r in runs),
            "median_dsr": _median([r.get("dsr") for r in runs]),
            "median_net_sharpe": _median([r.get("net_sharpe") for r in runs]),
            "median_market_basket_sharpe": _median([r.get("market_basket_sharpe") for r in runs]),
            "failed_checks": dict(sorted(failed.items())),
            "median_log10_equity_multiple_end_of_development": _median([r.get("log10_equity_multiple_end_of_development") for r in runs]),
            "median_sealed_impact_share_of_gross": _median([r.get("sealed_impact_share_of_gross") for r in runs]),
            "seeds": runs}


def _rung(ctx: _Context, hit_rate: float, horizon: int, seeds: int, priors: Sequence[float]) -> dict:
    runs = []
    for k in range(seeds):
        oracle = OracleCheat(ctx.prices, hit_rate, horizon, ctx.rng("oracle", round(hit_rate * 10000), horizon, k))
        runs.append({"seed": k, **_run_one(ctx, oracle, priors)})
    return {"hit_rate": float(hit_rate), "horizon": int(horizon), **_summary(runs)}


def _check_distinct(hit_rates: Sequence[float]) -> None:
    rates = [float(h) for h in hit_rates]
    if len(set(rates)) != len(rates):
        raise ValueError(f"hit rates must not repeat: {rates}")


def smallest_passing_hit_rate(ladder: list[dict]) -> float | None:
    """The smallest rung h <= 0.70 such that at h and at every higher rung up to 0.70, at least 4 of 5 seeds (80%)
    passed BOTH development and the sealed test. Rungs above 0.70 are reported but never considered, so they can
    neither be returned nor stand in for a failed 0.70. None if there is none."""
    _check_distinct([r["hit_rate"] for r in ladder])
    ok = {r["hit_rate"]: r["passed_sealed"] >= PASS_SHARE * r["runs"] for r in ladder if r["hit_rate"] <= SMALLEST_UP_TO}
    for h in sorted(ok):
        if all(ok[x] for x in ok if x >= h):
            return h
    return None


def _ladder(ctx, hit_rates, horizon, seeds, priors) -> dict:
    rows = [_rung(ctx, h, horizon, seeds, priors) for h in hit_rates]
    return {"ladder": rows, "smallest_passing_hit_rate": smallest_passing_hit_rate(rows)}


def _single_statement(s0: dict) -> str:
    if s0.get("refused"):
        return "the one-bar edge was refused: " + str(s0.get("error"))
    if s0["passed_development"] and s0["passed_sealed"]:
        return "this gate let through an edge that lives in one bar on this data"
    if not s0["passed_development"] and s0["failed"] == ["delayed_fills"]:
        return "this gate turns away an edge that lives in one bar, because of the delayed-fills check"
    if not s0["passed_development"]:
        return "the one-bar edge failed development on: " + ", ".join(s0["failed"])
    return "the one-bar edge failed the sealed test on: " + ", ".join(s0["sealed_failed"] or [])


def _suite(ctx: _Context, hit_rates, horizon: int, seeds: int, priors: list[float]) -> dict:
    """Every control that has a pass count, on one price frame: the ladders, the reference edge, the no-edge book
    and the single-bar edge."""
    fresh = _ladder(ctx, hit_rates, horizon, seeds, ())

    def reference(ladder: dict, with_priors: list[float]) -> dict:
        row = next((r for r in ladder["ladder"] if r["hit_rate"] == REFERENCE_HIT_RATE), None)
        row = row or _rung(ctx, REFERENCE_HIT_RATE, horizon, seeds, with_priors)
        return {k: v for k, v in row.items() if k != "seeds"}

    single = _rung(ctx, 1.0, 1, 1, ())
    single["statement"] = _single_statement(single["seeds"][0])
    no_edge = _summary([{"seed": k, **_run_one(ctx, NoEdgeBook(ctx.prices, ctx.rng("no_edge", k)))} for k in range(seeds)])
    ref = reference(fresh, [])
    out = {"fresh_ledger": fresh, "at_prior_attempts": None, "reference_edge": ref,
           "reference_edge_at_prior_attempts": None, "no_edge": no_edge, "single_bar_edge": single,
           "market_basket_sharpe": ref["median_market_basket_sharpe"]}      # the oracle's market: every symbol
    if priors:
        at_prior = {"prior_attempts": len(priors),
                    "prior_variance_of_sharpes": float(np.var(priors, ddof=1)) if len(priors) >= 2 else None,
                    "variance_rule": "Gauntlet._deflated uses the variance of all attempt Sharpes on the market "
                                     "(priors plus the run's own) once there are 3 or more, else 1/n_obs",
                    **_ladder(ctx, hit_rates, horizon, seeds, priors)}
        at_prior["median_variance_used"] = _median([r.get("variance_of_attempt_sharpes") for row in at_prior["ladder"]
                                                    for r in row["seeds"]])
        out["at_prior_attempts"] = at_prior
        out["reference_edge_at_prior_attempts"] = reference(at_prior, priors)
    return out


def _reading(given: dict, flat: dict) -> list[str]:
    """Plain sentences generated from the numbers."""
    def row(suite, ladder, h):
        lad = suite.get(ladder)
        return None if lad is None else next((r for r in lad["ladder"] if r["hit_rate"] == h), None)

    def both(r):
        return f"{r['passed_sealed']} of {r['runs']}"

    out = []
    a, b = row(given, "fresh_ledger", 0.5), row(flat, "fresh_ledger", 0.5)
    if a and b:
        out.append(f"Long-only random timing (h = 0.50) passed both tests in {both(a)} seeds on a fresh ledger on the data as "
                   f"given, and in {both(b)} seeds on the detrended data; {a['passed_both_and_timing']} and "
                   f"{b['passed_both_and_timing']} of them also had a re-timing p-value of 0.05 or less.")
    for suite, data in ((given, "the data as given"), (flat, "the detrended data")):
        for ladder, ledger in (("fresh_ledger", "a fresh ledger"), ("at_prior_attempts", "the prior attempts")):
            if suite.get(ladder) is None:
                continue
            h = suite[ladder]["smallest_passing_hit_rate"]
            out.append(f"Smallest passing hit rate on {data} with {ledger}: "
                       + (f"{h:.2f}." if h is not None else f"none up to {SMALLEST_UP_TO:.2f}."))
            ref = suite["reference_edge" if ladder == "fresh_ledger" else "reference_edge_at_prior_attempts"]
            out.append(f"The reference edge (h = {REFERENCE_HIT_RATE:.2f}) passed both tests in {both(ref)} seeds on {data} "
                       f"with {ledger}, {ref['passed_both_and_timing']} of them also with a re-timing p-value of 0.05 or less.")
    mb, flat_mb = given.get("market_basket_sharpe"), flat.get("market_basket_sharpe")
    if mb is not None and mb > 0.3:
        out.append("On this data a fresh-ledger pass can come from the market's own rise: read the detrended ladder for "
                   "the smallest timing skill the gate lets through.")
    if flat_mb is not None and flat_mb > 0.3:
        out.append(f"The detrended market basket still has a development Sharpe of {flat_mb:.2f}: removing each symbol's "
                   "mean log return leaves about half its variance as drift in simple returns, which a long book earns.")
    return out


def run_controls(prices: pd.DataFrame, dollar_volume: pd.DataFrame | None, cost: CostModel, *,
                 criteria: gt.Criteria | None = None, seed: int = 20261116,
                 hit_rates: Sequence[float] = (0.50, 0.52, 0.55, 0.60, 0.70, 1.00), seeds_per_rate: int = 5,
                 horizon: int = 5, prior_attempt_sharpes: Sequence[float] = ()) -> dict:
    """Run every control through the real gauntlet in a temporary folder, on the prices as given and on the same
    prices detrended (`detrend`); return the report (strict JSON)."""
    if seeds_per_rate < 1 or horizon < 1:
        raise ValueError("seeds_per_rate and horizon must be at least 1")
    _check_distinct(hit_rates)
    priors = [float(s) for s in prior_attempt_sharpes]
    if not all(math.isfinite(s) for s in priors):
        raise ValueError("prior attempt Sharpes must be finite numbers")
    with tempfile.TemporaryDirectory(prefix="valedia-controls-") as tmp:
        ctx = _Context(prices, dollar_volume, cost, criteria, Path(tmp) / "given", seed)
        given = _suite(ctx, hit_rates, horizon, seeds_per_rate, priors)
        flat_ctx = _Context(detrend(prices), dollar_volume, cost, criteria, Path(tmp) / "detrended", seed)
        flat = _suite(flat_ctx, hit_rates, horizon, seeds_per_rate, priors)
        peek = FramePeekingCheat(prices.columns, REFERENCE_HIT_RATE, horizon, ctx.rng("peek"), len(prices))
        peek_run = _run_one(ctx, peek)
        dev = prices.iloc[: ctx.cut]
        probe = dev.iloc[-min(len(dev), 900):]
        oracle = OracleCheat(prices, REFERENCE_HIT_RATE, horizon, ctx.rng("oracle", 0, horizon, 0))
        sees_peek = research.detect_lookahead(peek.target_weights, probe)
        sees_oracle = research.detect_lookahead(oracle.target_weights, probe)
        sees_noise = research.detect_lookahead(NoEdgeBook(prices, ctx.rng("no_edge", 0)).target_weights, probe)
    report = {
        "prediction": asdict(NEUTRAL_PREDICTION),
        **given,
        "detrended": flat,
        "reading": _reading(given, flat),
        "frame_peeking": {**peek_run, "flagged_columns": len(sees_peek)},
        "detector_sees_frame_peeking": bool(sees_peek),
        "detector_sees_carried_future": bool(sees_oracle),
        "detector_flags_no_edge": bool(sees_noise),
        "detector_blind_spot": ("the look-ahead detector compares a strategy's output on a frame with its output on the "
                                "same frame cut short; a strategy that carries its own copy of the future gives the same "
                                "answer both ways and is not flagged" if sees_peek and not sees_oracle else
                                "no blind spot shown by these two cheats"),
        "data": {"symbols": int(prices.shape[1]), "first": str(prices.index[0].date()), "last": str(prices.index[-1].date()),
                 "sealed_from": str(prices.index[ctx.cut].date())},
    }
    return gt._clean(report)


# ---- reading the real bar, read-only

def _open_read_only(path: str | Path) -> sqlite3.Connection:
    """Open an existing sqlite file read-only (URI mode=ro): it cannot be created, written or changed."""
    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"no ledger at {p}")
    return sqlite3.connect(f"{p.as_uri()}?mode=ro", uri=True)


def read_prior_attempt_sharpes(path: str | Path, market: str) -> list[float]:
    """The per-period Sharpe of every counted attempt on `market` in the ledger at `path`, opened read-only."""
    con = _open_read_only(path)
    try:
        rows = con.execute("SELECT a.sharpe FROM attempts a JOIN hypotheses h ON h.id = a.hypothesis_id "
                           "WHERE a.sharpe IS NOT NULL AND h.market = ? ORDER BY a.id", (market,)).fetchall()
    finally:
        con.close()
    return [float(r[0]) for r in rows]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="valedia_trader.research_loop.controls", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="folder with close.parquet and volume.parquet")
    ap.add_argument("--prior-from-ledger", default=None, help="ledger to read attempt Sharpes from (opened read-only)")
    ap.add_argument("--market", default=None, help="market label of those attempts (default: the research command's)")
    ap.add_argument("--seed", type=int, default=20261116)
    args = ap.parse_args(argv)
    from valedia_trader.research_loop import cli
    prices, dollar, cost = cli._market(argparse.Namespace(data=args.data))        # the research command's cost model
    priors = read_prior_attempt_sharpes(args.prior_from_ledger, args.market or cli.MARKET) if args.prior_from_ledger else ()
    print(json.dumps(run_controls(prices, dollar, cost, seed=args.seed, prior_attempt_sharpes=priors), indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
