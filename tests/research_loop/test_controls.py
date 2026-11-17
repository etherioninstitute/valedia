"""Controls for the gauntlet: the reference edge must pass, the no-edge book must fail, and the ladder must behave.

Pass marks come from the builder's measurements. Since addendum 2 they are taken on the DRIFTLESS universe the
module fixture runs (`universe(3, drift=False)`: expected simple return zero, market basket development Sharpe
0.04), unless a comment names another one. 30 prior attempts are drawn from N(0.005, 0.03) with seed 0. Each mark
has both numbers in its comment: what was measured, and the mark, set at about three times the margin seen.
"""
import json
import os
import sqlite3
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from valedia_trader.backtest.engine import CostModel, run_backtest
from valedia_trader.backtest.walkforward import build_cost_model
from valedia_trader.data_and_fees.venue_registry import VenueRegistry
from valedia_trader.research_loop import REPO
from valedia_trader.research_loop import controls
from valedia_trader.research_loop import ledger as lg
from valedia_trader.research_loop.strategy import run_rules

NAMES = ("BTC-USD", "DBC", "EEM", "EFA", "ETH-USD", "GLD", "IEF", "IWM", "LQD", "QQQ", "SPY", "TLT", "XLE", "XLF",
         "XLK", "XLU", "XLV")
# annual drift, annual volatility, equity beta, bond beta
SHAPE = {"SPY": (.09, .16, 1.0, 0), "QQQ": (.12, .21, 1.15, 0), "IWM": (.07, .22, 1.15, 0), "EFA": (.05, .17, .9, 0),
         "EEM": (.03, .21, 1.0, 0), "XLE": (.04, .28, .9, 0), "XLF": (.08, .21, 1.1, 0), "XLK": (.14, .23, 1.15, 0),
         "XLU": (.07, .17, .5, .3), "XLV": (.08, .16, .75, 0), "IEF": (.02, .07, -.1, 1.0), "TLT": (.02, .15, -.2, 2.0),
         "LQD": (.03, .08, .1, .9), "GLD": (.05, .15, .05, .3), "DBC": (.01, .18, .4, 0),
         "BTC-USD": (.35, .70, .4, 0), "ETH-USD": (.35, .85, .45, 0)}
PRIORS = [float(x) for x in np.random.default_rng(0).normal(0.005, 0.03, 30)]     # per-period Sharpes of 30 earlier attempts
UNIVERSE = 3
RUNGS = [0.50, 0.52, 0.55, 0.60, 0.70, 1.00]
LADDERS = [("given", "fresh_ledger"), ("given", "at_prior_attempts"), ("detrended", "fresh_ledger"),
           ("detrended", "at_prior_attempts")]


def universe(seed=7, years=14, drift=True):
    """17 symbols, 14 years of daily closes ending 2026-11-13: one equity factor with calm and stress regimes,
    one bond factor, fat-tailed (t, 5 dof) idiosyncratic noise, and uniform dollar volume of 0.5 to 5 billion.
    drift=False: no upward drift, each symbol's expected simple daily return is zero (log drift -vol^2/2) and the
    stress regime carries no drift of its own."""
    rng = np.random.default_rng(seed)
    n = years * 252
    idx = pd.bdate_range(end="2026-11-13", periods=n)
    stress, on = np.zeros(n, bool), False
    for t in range(n):
        on = (rng.random() > 1 / 40) if on else (rng.random() < 1 / 300)
        stress[t] = on
    tilt = 1.0 if drift else 0.0
    eq = np.where(stress, 0.40, 0.13) / np.sqrt(252) * rng.standard_t(5, n) / np.sqrt(5 / 3) - tilt * np.where(stress, 0.0015, 0.0)
    bond = 0.06 / np.sqrt(252) * rng.standard_normal(n) + tilt * np.where(stress, 0.0003, 0.0)
    cols = {}
    for c in NAMES:
        mu, vol, b_eq, b_b = SHAPE[c]
        idio = np.sqrt(max(vol ** 2 - (b_eq * 0.16) ** 2 - (b_b * 0.06) ** 2, (0.3 * vol) ** 2)) / np.sqrt(252)
        mu = mu if drift else -0.5 * vol ** 2
        r = mu / 252 + b_eq * eq + b_b * bond + idio * rng.standard_t(5, n) / np.sqrt(5 / 3)
        cols[c] = 100 * np.exp(np.cumsum(r))
    prices = pd.DataFrame(cols, index=idx)[list(NAMES)]
    dollar = pd.DataFrame(rng.uniform(5e8, 5e9, (n, len(NAMES))), index=idx, columns=list(NAMES))
    return prices, dollar


def cost_for(prices) -> CostModel:
    reg = VenueRegistry()                                                 # in memory, seeded; no file
    try:
        return build_cost_model(prices.columns, reg, as_of=prices.index[-1])
    finally:
        reg.close()


@pytest.fixture(scope="module")
def market():
    prices, dollar = universe(UNIVERSE, drift=False)
    return prices, dollar, cost_for(prices)


@pytest.fixture(scope="module")
def full(market, tmp_path_factory):
    prices, dollar, cost = market
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(tempfile, "tempdir", str(tmp_path_factory.mktemp("controls_tmp")))
        return controls.run_controls(prices, dollar, cost, prior_attempt_sharpes=PRIORS)


def small(market, seed=20261116, hit_rates=(0.55,), **kw):
    prices, dollar, cost = market
    return controls.run_controls(prices, dollar, cost, seed=seed, hit_rates=hit_rates, seeds_per_rate=1, **kw)


def suite(report, name):
    return report if name == "given" else report["detrended"]


def rung(report, ladder, h, name="given"):
    return next(r for r in suite(report, name)[ladder]["ladder"] if r["hit_rate"] == h)


# ---- the controls

@pytest.mark.parametrize("name", ["given", "detrended"])
def test_the_reference_edge_passes_on_a_fresh_ledger_and_is_measured_at_prior_attempts(full, name):
    s = suite(full, name)
    ref = s["reference_edge"]
    assert ref["hit_rate"] == 0.60 and ref["horizon"] == 5 and ref["runs"] == 5
    # measured 5 of 5 on the driftless universe, its detrended copy, and universe 1 as given and detrended: every seed
    assert ref["passed_development"] == ref["passed_sealed"] == 5 and ref["failed_checks"] == {}
    # reference net Sharpe per seed measured 1.35 to 1.63 (driftless, as given) and 1.33 to 1.59 (detrended); the
    # gate needs about 0.53 for a Deflated Sharpe of 0.95 at one attempt; mark 0.8 = 0.53 plus a third of the
    # smallest margin seen (0.80)
    assert min(x["net_sharpe"] for x in rung(full, "fresh_ledger", 0.60, name)["seeds"]) >= 0.8
    # the gate's re-timing test: measured p 0.003 (its floor, 1/301) on every reference seed; all 5 also timed
    assert ref["passed_both_and_timing"] == 5 and ref["median_timing_p_value"] <= 0.05
    at_prior = s["reference_edge_at_prior_attempts"]
    # with 30 priors it is MEASURED, not required: 2 of 5 on the driftless universe and on its detrended copy
    assert at_prior["runs"] == 5 and 0 <= at_prior["passed_sealed"] <= 5
    assert at_prior["passed_sealed"] == rung(full, "at_prior_attempts", 0.60, name)["passed_sealed"]
    assert full["prediction"]["fail_if_sharpe_below"] == 0.0 and full["prediction"]["fail_if_drawdown_above"] == 1.0


@pytest.mark.parametrize("name", ["given", "detrended"])
def test_the_no_edge_book_fails_development_on_every_seed(full, name):
    ne = suite(full, name)["no_edge"]
    assert ne["runs"] == 5 and ne["passed_development"] == 0 and ne["passed_sealed"] == 0
    # T39-2b: the controls state the edge claim, whose checks are the one-trial Sharpe test and the re-timing test
    # (was: "deflated_sharpe" in every seed's failures)
    assert all(s["claim"] == "edge" and {"sharpe_one_trial", "timing"} & set(s["failed"]) for s in ne["seeds"])
    assert all("deflated_sharpe" not in s["failed"] for s in ne["seeds"])
    # median Deflated Sharpe measured 0.106 (as given) and 0.114 (detrended); mark 0.35, about three times the
    # larger. Highest single seed measured 0.422 here and 0.876 on drifting universe 1, detrended.
    assert ne["median_dsr"] <= 0.35
    assert all(s["attempts_on_market_before"] == 0 and s["attempts_on_market"] == 1 for s in ne["seeds"])


def test_the_frame_peeking_cheat_is_flagged_and_refused_and_the_oracle_is_not_flagged(full):
    fp = full["frame_peeking"]
    assert fp["refused"] is True and "future data" in fp["error"] and fp["attempts_recorded"] == 0
    assert fp["flagged_columns"] >= 1                                     # measured 17 of 17 on every universe
    assert full["detector_sees_frame_peeking"] is True
    assert full["detector_sees_carried_future"] is False                   # the blind spot, measured
    assert full["detector_flags_no_edge"] is False
    assert "carries its own copy of the future" in full["detector_blind_spot"]


def test_the_detector_itself_on_both_cheats(market):
    prices, _, _ = market
    probe = prices.iloc[1500:2400]
    peek = controls.FramePeekingCheat(prices.columns, 1.0, 5, np.random.default_rng(1), len(prices))
    oracle = controls.OracleCheat(prices, 1.0, 5, np.random.default_rng(1))
    from valedia_trader.quant_engine import level10_ml_research as research
    assert research.detect_lookahead(peek.target_weights, probe)
    assert research.detect_lookahead(oracle.target_weights, probe) == []
    w_peek, w_oracle = peek.target_weights(probe), oracle.target_weights(probe)
    assert (w_peek.iloc[:-10] == w_oracle.iloc[:-10]).all().all()        # the same rule, until the frame runs out


def _holding_share(prices: pd.DataFrame, oracle, horizon: int = 5) -> tuple[float, float, int]:
    """Share of (symbol, block) holdings that made money, read through the engine at zero cost and the gate's lag,
    with its standard error clustered by block (holdings in one block share the market's move)."""
    w, _ = run_rules(oracle, prices)
    res = run_backtest(prices, w, CostModel(0.0, 0.0, 0.0, 0.0))
    held = res.weights.to_numpy()                                         # in effect after close t, earns bar t + 1
    r = np.log1p(prices.pct_change().fillna(0.0).to_numpy())
    n = len(prices)
    block_of_earning_bar = (np.arange(n) - 2) // horizon                   # decided at t - 2 (lag 1), earns bar t
    earned = held[np.clip(np.arange(n) - 1, 0, None)] > 0                  # position held into bar t
    earned[0] = False
    rights, counts = [], []
    for b in range(0, n // horizon):
        bars = np.flatnonzero(block_of_earning_bar == b)
        if len(bars) < horizon or bars[-1] >= n:
            continue
        holding = earned[bars].all(axis=0)                                # held through the whole block
        partly = earned[bars].any(axis=0) & ~holding
        assert not partly.any()                                           # one decision per block, held for all of it
        if holding.any():
            ret = r[bars][:, holding].sum(axis=0)
            rights.append(int((ret > 0).sum()))
            counts.append(int(holding.sum()))
    rights, counts = np.array(rights, float), np.array(counts, float)
    share = rights.sum() / counts.sum()
    nb = len(counts)
    se = float(np.sqrt(nb / (nb - 1) * np.sum((rights - share * counts) ** 2)) / counts.sum())
    return float(share), se, int(counts.sum())


def test_the_oracle_is_right_h_of_the_time_through_the_engine_and_a_shifted_sign_is_not():
    prices = controls.detrend(universe(UNIVERSE, drift=False)[0])        # driftless: up and down blocks equally likely
    for h in (0.5, 0.6, 0.7, 1.0):
        share, se, held = _holding_share(prices, controls.OracleCheat(prices, h, 5, np.random.default_rng(11)))
        assert held > 3000 and abs(share - h) <= 3 * se + 1e-12, (h, share, se)
    for shift in (1, -1):                                                 # the same oracle, its sign one bar early or late
        wrong = controls.OracleCheat(prices.shift(shift), 1.0, 5, np.random.default_rng(11))
        share, se, _ = _holding_share(prices, wrong)
        assert abs(share - 1.0) > 3 * se + 1e-12, (shift, share, se)


def test_the_seeds_of_a_rung_are_different_books(full):
    for name, ladder in LADDERS:
        prints = {}
        for r in suite(full, name)[ladder]["ladder"]:
            fp = [s["book_fingerprint"] for s in r["seeds"]]
            if r["hit_rate"] < 1.0:
                assert len(set(fp)) == len(fp), (name, ladder, r["hit_rate"])   # five draws, not one counted five times
            else:
                assert len(set(fp)) == 1                                  # h = 1.00 has no draw: the same book by construction
            prints[r["hit_rate"]] = set(fp)
        rates = sorted(prints)
        assert all(not (prints[a] & prints[b]) for i, a in enumerate(rates) for b in rates[i + 1:])
    given = {s["book_fingerprint"] for r in full["fresh_ledger"]["ladder"] for s in r["seeds"]}
    flat = {s["book_fingerprint"] for r in full["detrended"]["fresh_ledger"]["ladder"] for s in r["seeds"]}
    assert not given & flat


def test_the_ladders_are_monotone_enough(full):
    for name, ladder in LADDERS:
        rows = sorted(suite(full, name)[ladder]["ladder"], key=lambda r: r["hit_rate"])
        assert [r["hit_rate"] for r in rows] == RUNGS
        for i, lo in enumerate(rows):
            for hi in rows[i + 1:]:
                for key in ("passed_development", "passed_sealed"):
                    assert hi[key] >= lo[key] - lo["runs"] / 5, (name, ladder, lo["hit_rate"], hi["hit_rate"], key)


def test_every_rung_reports_the_re_timing_test(full):
    for name, ladder in LADDERS:
        for r in suite(full, name)[ladder]["ladder"]:
            timed = [s["timing_p_value"] for s in r["seeds"]]
            assert all(p is not None and 0.0 < p <= 1.0 for p in timed)   # develop reports it for passed and failed runs
            assert r["timing_p_at_most_0_05"] == sum(p <= 0.05 for p in timed)
            assert r["passed_both_and_timing"] == sum(s["passed_sealed"] and s["timing_p_value"] <= 0.05 for s in r["seeds"])
            assert r["passed_both_and_timing"] <= min(r["passed_sealed"], r["timing_p_at_most_0_05"])
        noise = rung(full, ladder, 0.50, name)
        # long-only random timing: median p measured 0.754 to 0.781 on all eight measured ladders, and 0 seeds that
        # passed both tests with p <= 0.05 (4 seeds passed both tests on drifting universe 1); mark p >= 0.25
        assert noise["median_timing_p_value"] >= 0.25 and noise["passed_both_and_timing"] == 0


def test_passed_both_and_timing_counts_sealed_passes_not_development_passes():
    def run(dev, sealed, p):
        return {"passed_development": dev, "sealed_ran": dev, "passed_sealed": sealed, "timing_p_value": p,
                "failed": [], "dsr": 0.99, "net_sharpe": 1.0}
    rows = [run(True, False, 0.01),                                       # passed development, FAILED the sealed test, timed
            run(True, True, 0.01),                                        # passed all three
            run(True, True, 0.50),                                        # passed both tests, not timed
            run(False, False, 0.01)]                                      # failed development, timed
    out = controls._summary(rows)
    assert out["passed_development"] == 3 and out["passed_sealed"] == 2 and out["timing_p_at_most_0_05"] == 3
    assert out["passed_both_and_timing"] == 1


def test_smallest_passing_hit_rate_reads_back_from_the_table_and_ignores_rungs_above_0_70(full):
    for name, ladder in LADDERS:
        lad = suite(full, name)[ladder]
        assert lad["smallest_passing_hit_rate"] == controls.smallest_passing_hit_rate(lad["ladder"])
    # measured on the driftless universe: 0.55 fresh and 0.70 at priors, as given and detrended alike
    assert full["fresh_ledger"]["smallest_passing_hit_rate"] <= 0.60
    assert full["at_prior_attempts"]["smallest_passing_hit_rate"] >= full["fresh_ledger"]["smallest_passing_hit_rate"]
    table = [{"hit_rate": h, "runs": 5, "passed_sealed": k} for h, k in ((0.5, 5), (0.52, 3), (0.55, 4), (0.6, 4), (0.7, 5))]
    assert controls.smallest_passing_hit_rate(table) == 0.55              # 0.50 passes but 0.52 above it does not
    assert controls.smallest_passing_hit_rate([dict(r, passed_sealed=3) for r in table]) is None
    above = {"hit_rate": 1.0, "runs": 5, "passed_sealed": 5}
    failed_070 = [dict(r, passed_sealed=5 if r["hit_rate"] < 0.7 else 2) for r in table] + [above]
    assert controls.smallest_passing_hit_rate(failed_070) is None          # 1.00 does not stand in for a failed 0.70
    assert controls.smallest_passing_hit_rate([above]) is None             # and is never returned
    assert controls.smallest_passing_hit_rate(table + [above]) == 0.55
    with pytest.raises(ValueError, match="repeat"):
        controls.smallest_passing_hit_rate(table + [table[0]])


def test_a_repeated_hit_rate_is_refused(market):
    prices, dollar, cost = market
    with pytest.raises(ValueError, match="repeat"):
        controls.run_controls(prices, dollar, cost, hit_rates=(0.5, 0.6, 0.5))


def test_sealed_fields_are_null_when_the_sealed_test_did_not_run(full):
    runs = [s for name, ladder in LADDERS for r in suite(full, name)[ladder]["ladder"] for s in r["seeds"]]
    runs += full["no_edge"]["seeds"] + full["detrended"]["no_edge"]["seeds"]
    unsealed = [s for s in runs if not s["sealed_ran"]]
    assert unsealed and any(s["sealed_ran"] for s in runs)
    for s in runs:
        assert s["sealed_ran"] == s["passed_development"]
        assert s["equity_multiple_end_of_development"] is not None
    for s in unsealed:
        assert s["sealed_impact_share_of_gross"] is None and s["sealed_sharpe"] is None and s["sealed_failed"] is None
        assert s["passed_sealed"] is False
    nobody = rung(full, "at_prior_attempts", 0.50)                        # measured 0 of 5 developed: no sealed runs
    assert nobody["passed_development"] == 0 and nobody["median_sealed_impact_share_of_gross"] is None
    # impact share measured 0.135 to 0.185 at h = 1.00 against 0.019 to 0.023 at h = 0.60: the compounder pays
    assert (rung(full, "fresh_ledger", 1.00)["median_sealed_impact_share_of_gross"]
            > 3 * rung(full, "fresh_ledger", 0.60)["median_sealed_impact_share_of_gross"])


def test_the_single_bar_edge_is_reported_with_its_failing_check(full):
    for name in ("given", "detrended"):
        sb = suite(full, name)["single_bar_edge"]
        assert sb["horizon"] == 1 and sb["hit_rate"] == 1.0 and sb["runs"] == 1
        # driftless universe measured: development failed on delayed_fills only, as given and detrended; drifting
        # universe 1 as given passed both
        assert sb["passed_development"] == 0 and sb["seeds"][0]["failed"] == ["delayed_fills"]
        assert sb["statement"] == "this gate turns away an edge that lives in one bar, because of the delayed-fills check"


def test_the_reading_says_it_in_words(full):
    reading = full["reading"]
    assert reading[0].startswith("Long-only random timing (h = 0.50) passed both tests in 0 of 5 seeds")
    assert sum(line.startswith("Smallest passing hit rate on") for line in reading) == 4
    assert sum(line.startswith("The reference edge (h = 0.60) passed both tests in") for line in reading) == 4
    assert "Smallest passing hit rate on the data as given with a fresh ledger: 0.55." in reading
    warning = ("On this data a fresh-ledger pass can come from the market's own rise: read the detrended ladder for the "
               "smallest timing skill the gate lets through.")
    assert full["market_basket_sharpe"] <= 0.3 and warning not in reading   # measured 0.04 on the driftless universe
    # two-way: the same report with a rising market basket carries the sentence
    risen = dict(full, market_basket_sharpe=1.0)
    assert warning in controls._reading(risen, full["detrended"])
    assert warning not in controls._reading(dict(full, market_basket_sharpe=0.3), full["detrended"])
    # detrended in simple returns the basket has no drift: Sharpe measured -0.003 here and -0.051 on drifting
    # universe 1 (0.37 and 0.34 when the drift was removed in log terms); mark 0.15, about three times the larger.
    # This mark is for THIS fixture universe only: the gate's basket Sharpe is taken over the development test window,
    # not the whole frame the detrending zeroes, so on other universes it sits within about one standard error of
    # zero (a 9.5-year window: about 0.3), not within 0.15.
    assert abs(full["detrended"]["market_basket_sharpe"]) <= 0.15
    assert any(line.startswith("The detrended market basket has a development Sharpe of") for line in reading)


def _simple(series: pd.Series) -> np.ndarray:
    v = series.dropna().to_numpy(dtype=float)                             # between consecutive prices the symbol has
    return v[1:] / v[:-1] - 1.0


def test_the_detrended_frame_keeps_volatility_and_correlation_and_loses_the_drift_in_simple_returns():
    prices, _ = universe(1)                                               # the drifting one
    gaps = prices.copy()
    gaps.iloc[100:130, 3] = np.nan                                        # missing values stay missing
    for px in (prices, gaps):
        flat = controls.detrend(px)
        assert flat.isna().equals(px.isna()) and (flat.iloc[0] == px.iloc[0]).all() and np.nanmin(flat.to_numpy()) > 0
        for c in px.columns:
            a, b = _simple(px[c]), _simple(flat[c])
            assert len(a) == len(b) and abs(b.mean()) <= 1e-12             # every symbol's mean simple return is zero
            assert abs(np.std(b, ddof=1) - np.std(a, ddof=1)) <= 1e-9
        assert max(abs(_simple(px[c]).mean()) for c in px.columns) > 1e-4   # two-way: the data as given did drift
    a, b = prices.pct_change().iloc[1:], controls.detrend(prices).pct_change().iloc[1:]
    assert np.allclose(b.corr().to_numpy(), a.corr().to_numpy(), rtol=0, atol=1e-9)
    assert np.abs(b.mean(axis=1).mean()) <= 1e-12                         # the equal-weight basket's mean return per bar
    assert np.allclose(controls.detrend(controls.detrend(prices)), controls.detrend(prices), rtol=1e-12)


def test_detrend_refuses_a_return_it_cannot_rebuild_into_a_price():
    idx = pd.bdate_range("2020-01-01", periods=6)
    px = pd.DataFrame({"OK": [10.0, 10.1, 10.0, 10.2, 10.1, 10.3], "JUMP": [1.0, 100.0, 0.5, 0.6, 0.55, 0.6]}, index=idx)
    with pytest.raises(ValueError, match=r"JUMP: the detrended return on 2020-01-03"):
        controls.detrend(px)                                              # -0.995 minus a mean of about 19.8 is below -1
    assert (controls.detrend(px[["OK"]]) > 0).all().all()


# ---- the ledgers

def test_each_run_starts_from_the_seeded_count_only(full):
    for name in ("given", "detrended"):
        for r in suite(full, name)["fresh_ledger"]["ladder"]:
            assert all(s["attempts_on_market_before"] == 0 and s["attempts_on_market"] == 1 and s["variations_of_idea"] == 1
                       for s in r["seeds"])
        for r in suite(full, name)["at_prior_attempts"]["ladder"]:
            assert all(s["attempts_on_market_before"] == 30 and s["attempts_on_market"] == 31 for s in r["seeds"])
        assert suite(full, name)["at_prior_attempts"]["prior_attempts"] == 30
        assert suite(full, name)["at_prior_attempts"]["median_variance_used"] > 0


def test_prior_attempts_raise_the_bar_and_an_empty_list_changes_nothing(full, market, tmp_path):
    for name in ("given", "detrended"):
        for fresh_row, prior_row in zip(suite(full, name)["fresh_ledger"]["ladder"], suite(full, name)["at_prior_attempts"]["ladder"]):
            for a, b in zip(fresh_row["seeds"], prior_row["seeds"]):
                assert a["net_sharpe"] == b["net_sharpe"] and a["book_fingerprint"] == b["book_fingerprint"]
                assert b["dsr"] <= a["dsr"] + 1e-12
        lowest = rung(full, "fresh_ledger", 0.50, name)["seeds"]
        assert all(b["dsr"] < a["dsr"] for a, b in zip(lowest, rung(full, "at_prior_attempts", 0.50, name)["seeds"]))
    # two-way: the same rung and seed rebuilt with an empty prior list is the fresh one, with the priors the seeded one
    prices, dollar, cost = market
    ctx = controls._Context(prices, dollar, cost, None, tmp_path, 20261116)
    empty, seeded = controls._rung(ctx, 0.50, 5, 1, []), controls._rung(ctx, 0.50, 5, 1, PRIORS)
    lowest = rung(full, "fresh_ledger", 0.50)["seeds"]
    assert empty["seeds"][0]["dsr"] == lowest[0]["dsr"] and empty["seeds"][0]["attempts_on_market"] == 1
    assert seeded["seeds"][0]["dsr"] == rung(full, "at_prior_attempts", 0.50)["seeds"][0]["dsr"] < empty["seeds"][0]["dsr"]


class _SharedLedger(lg.HypothesisLedger):
    """The broken variant: one ledger for every run (and the duplicate guard waived so the runs can share it)."""

    def propose(self, idea, source_agent, market, **kw):
        kw["conditions_changed"] = True
        return super().propose(idea, source_agent, market, **kw)

    def close(self):
        pass


def test_a_shared_ledger_would_raise_the_bar_on_later_rungs(market, tmp_path, monkeypatch):
    prices, dollar, cost = market
    ctx = controls._Context(prices, dollar, cost, None, tmp_path / "own", 20261116)
    rates = (0.50, 0.52, 0.55)
    own = [controls._rung(ctx, h, 5, 1, [])["seeds"][0] for h in rates]                  # each rung on its own ledger
    assert all(s["attempts_on_market_before"] == 0 and s["attempts_on_market"] == 1 for s in own)
    shared = _SharedLedger(tmp_path / "shared.db")
    monkeypatch.setattr(controls, "_fresh_ledger", lambda paths: shared)
    ctx = controls._Context(prices, dollar, cost, None, tmp_path / "shared", 20261116)
    broken = [controls._rung(ctx, h, 5, 1, [])["seeds"][0] for h in rates]
    assert [s["net_sharpe"] for s in broken] == [s["net_sharpe"] for s in own]     # the same books
    assert [s["attempts_on_market_before"] for s in broken] == [0, 1, 2]
    assert [s["attempts_on_market"] for s in broken] == [1, 2, 3]
    assert broken[0]["dsr"] == own[0]["dsr"]
    assert broken[1]["dsr"] < own[1]["dsr"] and broken[2]["dsr"] < own[2]["dsr"]   # a higher bar on the later rungs
    shared._db.close()


# ---- the report and its footprint

def test_the_report_is_strict_json_and_the_same_seed_gives_the_same_report(market, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    one, two, other = small(market), small(market), small(market, seed=7)
    text = json.dumps(one, allow_nan=False, sort_keys=True)
    assert json.loads(text) == one
    assert text == json.dumps(two, allow_nan=False, sort_keys=True)
    assert json.dumps(other, allow_nan=False, sort_keys=True) != text      # two-way: the seed does change it
    assert one["data"] == {"symbols": 17, "first": str(market[0].index[0].date()), "last": "2026-11-13",
                           "sealed_from": str(market[0].index[int(len(market[0]) * 0.75)].date())}
    assert one["at_prior_attempts"] is None and one["detrended"]["at_prior_attempts"] is None
    assert set(one["detrended"]) == {"fresh_ledger", "random_timing_by_claim", "at_prior_attempts", "reference_edge", "reference_edge_at_prior_attempts",
                                     "no_edge", "single_bar_edge", "market_basket_sharpe"}


def test_it_leaves_no_file_and_never_uses_a_real_path(market, tmp_path, monkeypatch):
    parent, cwd = tmp_path / "parent", tmp_path / "cwd"
    parent.mkdir(), cwd.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(parent))
    monkeypatch.chdir(cwd)
    real_paths, roots, opened = controls.LoopPaths, [], []

    def guarded(root=None):                                               # LoopPaths raises on its default root
        if root is None or os.path.realpath(root).startswith(os.path.realpath(REPO)) \
                or not os.path.realpath(root).startswith(os.path.realpath(parent)):
            raise AssertionError(f"LoopPaths used outside the temporary directory: {root}")
        roots.append(root)
        return real_paths(root)

    connect = sqlite3.connect

    def record(db, *a, **k):
        opened.append(os.path.realpath(str(db)))
        return connect(db, *a, **k)

    monkeypatch.setattr(controls, "LoopPaths", guarded)
    monkeypatch.setattr(sqlite3, "connect", record)
    before = sorted(p.name for p in parent.rglob("*"))
    rep = small(market, hit_rates=(0.60,))
    assert rep["reference_edge"]["runs"] == 1 and roots and opened
    assert all(p.startswith(os.path.realpath(parent)) for p in opened)
    assert not any(p.startswith(os.path.realpath(REPO)) for p in opened)
    assert sorted(p.name for p in parent.rglob("*")) == before == [] and list(cwd.iterdir()) == []


# ---- the command line's read-only ledger

def _ledger_with_attempts(path: Path) -> list[float]:
    led = lg.HypothesisLedger(path)
    a = led.propose("Momentum on exchange traded funds lasts for months", "price-analyst", "us-etf+crypto")
    b = led.propose("Something about a different market entirely", "price-analyst", "elsewhere")
    for s in (0.03, -0.01, 0.02):
        led.record_attempt(a, "backtest", s, 2000, "1.0.0")
    led.record_attempt(a, "revision", None, None, "1.1.0")              # no Sharpe: not an attempt Sharpe
    led.record_attempt(b, "backtest", 0.5, 2000, "1.0.0")
    led.close()
    return [0.03, -0.01, 0.02]


def test_prior_attempts_are_read_from_a_ledger_opened_read_only(tmp_path):
    path = tmp_path / "ledger.db"
    want = _ledger_with_attempts(path)
    raw, mtime = path.read_bytes(), path.stat().st_mtime_ns
    assert controls.read_prior_attempt_sharpes(path, "us-etf+crypto") == want
    assert controls.read_prior_attempt_sharpes(path, "nowhere") == []
    assert controls.read_prior_attempt_sharpes(path) == want + [0.5]        # T39-2b: every market, the gate's N
    con = controls._open_read_only(path)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("INSERT INTO findings (hypothesis_id, source, finding, recorded_at) VALUES (NULL, 'x', 'y', 'z')")
    con.close()
    assert path.read_bytes() == raw and path.stat().st_mtime_ns == mtime
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ledger.db"]  # no journal or lock file left beside it
    with pytest.raises(FileNotFoundError):
        controls.read_prior_attempt_sharpes(tmp_path / "missing.db", "us-etf+crypto")
    assert not (tmp_path / "missing.db").exists()


def test_the_command_line_reads_the_data_the_research_way_and_passes_the_priors(market, tmp_path, monkeypatch, capsys):
    prices, dollar, _ = market
    data = tmp_path / "data"
    data.mkdir()
    prices.to_parquet(data / "close.parquet")
    volume = dollar / prices
    for c in ("BTC-USD", "ETH-USD"):                                      # the vendor reports crypto volume in dollars
        volume[c] = dollar[c]
    volume.to_parquet(data / "volume.parquet")
    ledger = tmp_path / "ledger.db"
    want = _ledger_with_attempts(ledger)
    seen = {}

    def fake(p, d, c, **kw):
        seen.update(prices=p, dollar=d, cost=c, **kw)
        return {"ok": float("nan")}

    monkeypatch.setattr(controls, "run_controls", lambda p, d, c, **kw: controls.gt._clean(fake(p, d, c, **kw)))
    assert controls.main(["--data", str(data), "--prior-from-ledger", str(ledger), "--market", "us-etf+crypto"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": None}
    assert list(seen["prior_attempt_sharpes"]) == want
    assert controls.main(["--data", str(data), "--prior-from-ledger", str(ledger)]) == 0      # T39-2b: every market
    capsys.readouterr()
    assert list(seen["prior_attempt_sharpes"]) == want + [0.5]
    from valedia_trader.research_loop import cli
    _, _, research_cost = cli._market(type("A", (), {"data": str(data)})())
    assert seen["cost"] == research_cost and seen["cost"].slippage_bps["BTC-USD"] == 5.0
    assert seen["prices"].shape == prices.shape
    assert np.allclose(seen["dollar"].to_numpy(), dollar.to_numpy())      # dollar volume as the research command reads it
    monkeypatch.setattr(controls, "run_controls", lambda p, d, c, **kw: seen.update(none=kw["prior_attempt_sharpes"]) or {})
    controls.main(["--data", str(data)])
    assert list(seen["none"]) == []



@pytest.mark.parametrize("name", ["given", "detrended"])
def test_the_long_only_random_timing_book_runs_under_each_claim(full, name):
    """T39-2b, R7: one row runs the h = 0.50 book under each claim, so the report shows what the label changes."""
    row = suite(full, name)["random_timing_by_claim"]
    assert set(row) == {"edge", "exposure"} and row["edge"]["runs"] == row["exposure"]["runs"] == 5
    same = {k: v for k, v in rung(full, "fresh_ledger", 0.50, name).items() if k != "seeds"}
    assert row["edge"] == same                                          # the ladder's own rung: the same seeds
    assert "sharpe_one_trial" not in row["exposure"]["failed_checks"] and "timing" not in row["exposure"]["failed_checks"]
    assert row["exposure"]["passed_sealed"] >= row["edge"]["passed_sealed"]
    assert all(s["claim"] == "edge" for r in suite(full, name)["fresh_ledger"]["ladder"] for s in r["seeds"])
