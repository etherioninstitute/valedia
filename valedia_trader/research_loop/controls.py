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

All controls are ordinary `Strategy` subclasses walked by `run_rules` (no dependency on a strategy's own `walk`).
"""
from __future__ import annotations

import argparse
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

__all__ = ["run_controls", "OracleCheat", "FramePeekingCheat", "NoEdgeBook", "NEUTRAL_PREDICTION",
           "read_prior_attempt_sharpes", "main"]

MARKET = "controls"                  # market label inside the temporary ledgers
REFERENCE_HIT_RATE = 0.60
PASS_SHARE = 0.8                     # "at least 4 of 5 seeds"
SMALLEST_UP_TO = 0.70                # smallest_passing_hit_rate looks at every rung from h up to this one
NEUTRAL_PREDICTION = gt.Prediction(annual_return=0.0, win_rate=0.5, sharpe=0.0, fail_if_sharpe_below=0.0,
                                   fail_if_drawdown_above=1.0,
                                   notes="control run: neutral terms, so only the gate's own checks decide")
_LAG = 1                             # the execution lag of the gauntlet's main and sealed runs (engine default)
_KIND = {"oracle": 1, "no_edge": 2, "peek": 3}


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


def _full_run(ctx: _Context, strategy: Strategy) -> dict:
    """The same full-history run the sealed test makes (gauntlet.py: run_rules then run_backtest on all prices),
    read for two numbers: the equity multiple at the first sealed bar and impact's share of sealed gross return."""
    w, _ = run_rules(strategy, ctx.prices)
    res = run_backtest(ctx.prices, w, ctx.cost, ctx.dollar)
    dev_net = res.net.iloc[: ctx.cut].to_numpy(dtype=float)
    log_mult = float(np.log1p(np.clip(dev_net, -0.999999, None)).sum())
    gross = float(res.gross.iloc[ctx.cut:].sum())
    impact = float(res.costs["impact"].iloc[ctx.cut:].sum())
    return {"equity_multiple_end_of_development": math.exp(log_mult) if log_mult < 700 else None,
            "log10_equity_multiple_end_of_development": log_mult / math.log(10.0),
            "sealed_impact_share_of_gross": impact / gross if gross > 0 else None}


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
        out.update(refused=False, passed_development=rep["verdict"].startswith("pass"), dsr=dsr["value"],
                   net_sharpe=rep["result"].get("sharpe"),
                   failed=[k for k, c in rep["checks"].items() if not c["passed"]],
                   delayed_fills_sharpe=rep["breaker"]["delayed_fills"]["sharpe"],
                   market_basket_sharpe=rep["breaker"]["market_basket"].get("sharpe"),
                   variations_of_idea=dsr["variations_of_idea"], attempts_on_market=dsr["attempts_on_market"],
                   variance_of_attempt_sharpes=dsr.get("variance_of_attempt_sharpes"),
                   passed_sealed=False, sealed_sharpe=None, sealed_failed=None)
        if out["passed_development"]:
            s = g.seal(hid, strategy)
            out.update(passed_sealed=s["verdict"] == "pass", sealed_sharpe=s["result"].get("sharpe"),
                       sealed_failed=[k for k, c in s["checks"].items() if not c["passed"]])
        out.update(_full_run(ctx, strategy))
        return out
    finally:
        ledger.close()


def _median(xs):
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    return float(np.median(xs)) if xs else None


def _rung(ctx: _Context, hit_rate: float, horizon: int, seeds: int, priors: Sequence[float]) -> dict:
    runs = []
    for k in range(seeds):
        oracle = OracleCheat(ctx.prices, hit_rate, horizon, ctx.rng("oracle", round(hit_rate * 10000), horizon, k))
        runs.append({"seed": k, **_run_one(ctx, oracle, priors)})
    failed: dict[str, int] = {}
    for r in runs:
        for f in r.get("failed") or (["refused"] if r.get("refused") else []):
            failed[f] = failed.get(f, 0) + 1
    return {"hit_rate": float(hit_rate), "horizon": int(horizon), "runs": len(runs),
            "passed_development": sum(bool(r.get("passed_development")) for r in runs),
            "passed_sealed": sum(bool(r.get("passed_sealed")) for r in runs),
            "median_dsr": _median([r.get("dsr") for r in runs]),
            "median_net_sharpe": _median([r.get("net_sharpe") for r in runs]),
            "median_market_basket_sharpe": _median([r.get("market_basket_sharpe") for r in runs]),
            "failed_checks": dict(sorted(failed.items())),
            "median_log10_equity_multiple_end_of_development": _median([r.get("log10_equity_multiple_end_of_development") for r in runs]),
            "median_sealed_impact_share_of_gross": _median([r.get("sealed_impact_share_of_gross") for r in runs]),
            "seeds": runs}


def smallest_passing_hit_rate(ladder: list[dict]) -> float | None:
    """The smallest rung h such that at h and at every higher rung up to 0.70, at least 4 of 5 seeds (80%) passed
    BOTH development and the sealed test. None if there is none."""
    rows = sorted(ladder, key=lambda r: r["hit_rate"])
    ok = {r["hit_rate"]: r["passed_sealed"] >= PASS_SHARE * r["runs"] for r in rows}
    for r in rows:
        h = r["hit_rate"]
        if all(ok[x] for x in ok if h <= x <= max(h, SMALLEST_UP_TO)):
            return h
    return None


def _ladder(ctx, hit_rates, horizon, seeds, priors) -> dict:
    rows = [_rung(ctx, h, horizon, seeds, priors) for h in hit_rates]
    return {"ladder": rows, "smallest_passing_hit_rate": smallest_passing_hit_rate(rows)}


def run_controls(prices: pd.DataFrame, dollar_volume: pd.DataFrame | None, cost: CostModel, *,
                 criteria: gt.Criteria | None = None, seed: int = 20261116,
                 hit_rates: Sequence[float] = (0.50, 0.52, 0.55, 0.60, 0.70, 1.00), seeds_per_rate: int = 5,
                 horizon: int = 5, prior_attempt_sharpes: Sequence[float] = ()) -> dict:
    """Run every control through the real gauntlet in a temporary folder; return the report (strict JSON)."""
    if seeds_per_rate < 1 or horizon < 1:
        raise ValueError("seeds_per_rate and horizon must be at least 1")
    priors = [float(s) for s in prior_attempt_sharpes]
    if not all(math.isfinite(s) for s in priors):
        raise ValueError("prior attempt Sharpes must be finite numbers")
    with tempfile.TemporaryDirectory(prefix="valedia-controls-") as tmp:
        ctx = _Context(prices, dollar_volume, cost, criteria, Path(tmp), seed)
        fresh = _ladder(ctx, hit_rates, horizon, seeds_per_rate, ())
        ref = next((r for r in fresh["ladder"] if r["hit_rate"] == REFERENCE_HIT_RATE), None)
        if ref is None:
            ref = _rung(ctx, REFERENCE_HIT_RATE, horizon, seeds_per_rate, ())
        single = _rung(ctx, 1.0, 1, 1, ())
        no_edge = [{"seed": k, **_run_one(ctx, NoEdgeBook(prices, ctx.rng("no_edge", k)))} for k in range(seeds_per_rate)]
        peek = FramePeekingCheat(prices.columns, REFERENCE_HIT_RATE, horizon, ctx.rng("peek"), len(prices))
        peek_run = _run_one(ctx, peek)
        dev = prices.iloc[: ctx.cut]
        probe = dev.iloc[-min(len(dev), 900):]
        oracle = OracleCheat(prices, REFERENCE_HIT_RATE, horizon, ctx.rng("oracle", 0, horizon, 0))
        sees_peek = research.detect_lookahead(peek.target_weights, probe)
        sees_oracle = research.detect_lookahead(oracle.target_weights, probe)
        sees_noise = research.detect_lookahead(NoEdgeBook(prices, ctx.rng("no_edge", 0)).target_weights, probe)
        at_prior = None
        if priors:
            at_prior = {"prior_attempts": len(priors),
                        "prior_variance_of_sharpes": float(np.var(priors, ddof=1)) if len(priors) >= 2 else None,
                        "variance_rule": "Gauntlet._deflated uses the variance of all attempt Sharpes on the market "
                                         "(priors plus the run's own) once there are 3 or more, else 1/n_obs",
                        **_ladder(ctx, hit_rates, horizon, seeds_per_rate, priors)}
            at_prior["median_variance_used"] = _median([r.get("variance_of_attempt_sharpes") for row in at_prior["ladder"]
                                                        for r in row["seeds"]])
    single_row = dict(single)
    s0 = single["seeds"][0]
    if s0["passed_development"] and s0["passed_sealed"]:
        statement = "this gate let through an edge that lives in one bar on this data"
    elif not s0["passed_development"] and s0["failed"] == ["delayed_fills"]:
        statement = "this gate turns away an edge that lives in one bar, because of the delayed-fills check"
    else:
        statement = ("the one-bar edge failed " + ("development on: " + ", ".join(s0["failed"]) if not s0["passed_development"]
                                                  else "the sealed test on: " + ", ".join(s0["sealed_failed"] or [])))
    single_row["statement"] = statement
    report = {
        "prediction": asdict(NEUTRAL_PREDICTION),
        "reference_edge": {"hit_rate": REFERENCE_HIT_RATE, "horizon": horizon, "runs": ref["runs"],
                           "passed_development": ref["passed_development"], "passed_sealed": ref["passed_sealed"],
                           "median_dsr": ref["median_dsr"], "failed_checks": ref["failed_checks"],
                           "median_log10_equity_multiple_end_of_development": ref["median_log10_equity_multiple_end_of_development"],
                           "median_sealed_impact_share_of_gross": ref["median_sealed_impact_share_of_gross"]},
        "no_edge": {"runs": len(no_edge), "passed_development": sum(r["passed_development"] for r in no_edge),
                    "passed_sealed": sum(r["passed_sealed"] for r in no_edge),
                    "median_dsr": _median([r["dsr"] for r in no_edge]), "seeds": no_edge},
        "frame_peeking": {**peek_run, "flagged_columns": len(sees_peek)},
        "single_bar_edge": single_row,
        "fresh_ledger": fresh,
        "at_prior_attempts": at_prior,
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
