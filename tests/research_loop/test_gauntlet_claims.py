"""T39-2b: the gate judges an EDGE claim by the studied rule (the Sharpe test for one trial and the re-timing test on
the listed window at 0.05 / N, N over the whole ledger) and labels an EXPOSURE claim (today's rule, said so)."""
import json
import math
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from valedia_trader.backtest import engine
from valedia_trader.backtest.engine import CostModel
from valedia_trader.research_loop import LoopPaths
from valedia_trader.research_loop import gauntlet as gt
from valedia_trader.research_loop import ledger as lg
from valedia_trader.research_loop import strategy as st

COST = CostModel(fees_bps=1.0, slippage_bps=1.0, borrow_annual=0.02, funding_annual=0.0)
EDGE = gt.Prediction(annual_return=0.05, win_rate=0.5, sharpe=0.8, fail_if_sharpe_below=0.3, fail_if_drawdown_above=0.25)
EXPOSURE = replace(EDGE, claim="exposure")
EDGE_CHECKS = {"prediction_failure_terms", "sharpe_one_trial", "timing", "walk_forward", "double_costs", "delayed_fills",
               "worst_periods"}
EXPOSURE_CHECKS = {"prediction_failure_terms", "deflated_sharpe", "walk_forward", "double_costs", "delayed_fills",
                   "worst_periods"}
EXPOSURE_PASS = "pass (development, exposure claim: holding is all it shows)"


def drift_market(seed=0, n=2400):
    """Constant drifts: being long the risers and short the fallers pays whatever the timing."""
    rng = np.random.default_rng(seed)
    r = np.array((0.0009, 0.0008, 0.0007, -0.0007, -0.0008, -0.0009)) + 0.01 * rng.standard_normal((n, 6))
    return pd.DataFrame(100 * np.exp(np.cumsum(r, axis=0)), index=pd.bdate_range("2012-01-02", periods=n), columns=list("ABCDEF"))


def regime_market(seed=0, n=2400, switch=1 / 60, mu=0.0015, sd=0.01):
    """Each drift changes sign at random (a regime lasts 60 bars on average) and has no average: only timing pays."""
    rng = np.random.default_rng(seed)
    sign = np.where(rng.random(6) < 0.5, 1.0, -1.0)
    r = np.empty((n, 6))
    for t in range(n):
        sign = np.where(rng.random(6) < switch, -sign, sign)
        r[t] = mu * sign + sd * rng.standard_normal(6)
    return pd.DataFrame(100 * np.exp(np.cumsum(r, axis=0)), index=pd.bdate_range("2012-01-02", periods=n), columns=list("ABCDEF"))


def momentum(version="1.0.0", lookback=120, name="claims_test"):
    return st.RuleStrategy.from_spec({
        "name": name, "version": version, "hypothesis": "Assets with a persistent drift keep drifting the same way",
        "market": list("ABCDEF"), "entry": {"rule": "momentum", "lookback": lookback, "enter": 0.02, "exit": 0.0, "direction": "both"},
        "sizing": {"rule": "equal"}, "risk": {"max_position_pct": 0.15, "max_open_positions": 6, "stop_loss_pct": None}})


def desk(tmp_path, prices, prediction, name="claims_test", market="synthetic"):
    paths = LoopPaths(tmp_path).ensure()
    ledger = lg.HypothesisLedger(paths.ledger)
    g = gt.Gauntlet(prices, None, COST, ledger, paths)
    hid = ledger.propose("Assets with a persistent drift keep drifting the same way", "price-analyst", market)
    gt.write_prediction(ledger, paths, hid, name, prediction)
    return paths, ledger, g, hid


# ---- R1: the claim

def test_the_claim_is_edge_or_exposure_and_is_written_with_the_prediction(tmp_path):
    assert EDGE.claim == "edge" and EXPOSURE.claim == "exposure"
    for bad in ("Edge", "", "timing", None, 1, 1.0, True, ["edge"]):
        with pytest.raises(ValueError):
            replace(EDGE, claim=bad)
    paths, ledger, g, hid = desk(tmp_path, drift_market(), EXPOSURE)
    text = (paths.backtests / "claims_test-prediction.md").read_text()
    assert "Claim: exposure" in text
    assert gt.read_prediction(ledger, paths, hid, "claims_test").claim == "exposure"
    assert ledger.prediction(hid)["expected"]["claim"] == "exposure"


def test_a_stored_prediction_without_a_claim_reads_as_an_edge_claim(tmp_path):
    paths = LoopPaths(tmp_path).ensure()
    ledger = lg.HypothesisLedger(paths.ledger)
    hid = ledger.propose("An idea from before the split", "price-analyst", "synthetic")
    body = "# Prediction written before the claim existed\n"
    old = {k: v for k, v in EDGE.__dict__.items() if k != "claim"}
    ledger.register_prediction(hid, body, old)
    (paths.backtests / "old_idea-prediction.md").write_text(body)
    assert gt.read_prediction(ledger, paths, hid, "old_idea").claim == "edge"


# ---- R5: a pass mark nothing can miss

def test_criteria_refuse_a_pass_mark_that_nothing_can_miss_or_meet():
    for bad in (0.0, 1.0, -1.0, 1.5, float("nan"), float("inf"), "0.95", True, None):
        with pytest.raises(ValueError):
            gt.Criteria(min_dsr=bad)
    assert gt.Criteria(min_dsr=0.95).min_dsr == 0.95 and gt.Criteria(min_dsr=float(np.nextafter(0.0, 1.0))).min_dsr > 0


# ---- R2: the two checks of an edge claim, as pure functions

def test_the_one_trial_sharpe_check_fails_closed():
    good = pd.Series(0.001 + 0.01 * np.random.default_rng(1).standard_normal(2000))
    value, ok, detail = gt.one_trial_sharpe(good, 0.95)
    assert ok and value > 0.95 and "one trial" in detail
    flat = pd.Series(-0.001 + 0.01 * np.random.default_rng(1).standard_normal(2000))
    assert not gt.one_trial_sharpe(flat, 0.95)[1]                                      # a real figure below the mark
    for unsafe in (pd.Series(dtype=float), pd.Series([0.01] * 5), pd.Series([np.nan] * 3000),
                   pd.Series(np.r_[good.to_numpy()[:-1], np.inf])):
        value, ok, detail = gt.one_trial_sharpe(unsafe, 0.95)
        assert not ok and "fails" in detail, unsafe.head()


def _skilled_book(r: pd.DataFrame) -> pd.DataFrame:
    return (r.shift(-1) > 0).astype(float) * 0.5                     # holds each asset only on the eve of an up day


def _window(days, seed=4, period=None):
    rng = np.random.default_rng(seed)
    base = rng.normal(0.0002, 0.01, (period or days, 2))
    raw = np.tile(base, (days // len(base) + 1, 1))[:days]
    idx = pd.bdate_range("2018-01-01", periods=days)
    r = pd.DataFrame(raw, index=idx, columns=["A", "B"])
    prices = 100 * (1 + r).cumprod()
    return r, prices


def test_edge_timing_uses_draws_where_the_level_can_be_reached_and_enumeration_beyond():
    days = 251                                                         # 209 distinct shifts: floor 1 / 210
    r, prices = _window(days)
    w = _skilled_book(r)
    below, beyond = gt.edge_timing(w, r, prices, 10), gt.edge_timing(w, r, prices, 11)
    assert below["copies"] == beyond["copies"] == 209 and below["floor"] == pytest.approx(1 / 210)
    assert below["region"] == "drawn" and beyond["region"] == "enumerated"             # 0.05/10 >= 1/210 > 0.05/11
    assert below["drawn_p_value"] is not None and below["listed"]["shifts"] == gt.r3_shifts(10) == 4000
    assert beyond["drawn_p_value"] is None and "cannot be reached" in beyond["detail"] and "209" in beyond["detail"]
    assert below["passed"] and beyond["passed"] and beyond["p_exact"] == pytest.approx(1 / 210)
    assert below["p_exact"] == beyond["p_exact"]                                        # computed in both regions


def test_beyond_the_floor_a_book_that_loses_to_one_copy_fails_and_one_that_beats_all_passes():
    days = 250                                                          # returns repeat after 125 bars: the copy
    r, prices = _window(days, period=125)                               # shifted by 125 is the book itself
    w = _skilled_book(pd.DataFrame(np.tile(r.to_numpy()[:125], (2, 1)), index=r.index, columns=r.columns))
    w.iloc[-1] = w.iloc[124]                                            # the eve of the wrap: keep the period exact
    one = gt.edge_timing(w, r, prices, 50)
    assert one["region"] == "enumerated" and not one["passed"]
    assert one["listed"]["copies_at_or_above"] == 1 and one["p_exact"] == pytest.approx(2 / (one["copies"] + 1))
    r2, prices2 = _window(days, seed=9)
    assert gt.edge_timing(_skilled_book(r2), r2, prices2, 50)["passed"]                 # the twin beats every copy


def test_edge_timing_fails_closed_on_missing_figures(monkeypatch):
    r, prices = _window(400)
    w = _skilled_book(r)
    assert gt.edge_timing(w, r, prices, 1)["passed"]                                     # the safe twin
    short = gt.edge_timing(w.iloc[:60], r.iloc[:60], prices.iloc[:60], 1)
    assert not short["passed"] and "too short" in short["detail"]
    assert not gt.edge_timing(w * 0.0, r, prices, 1)["passed"]                           # no positions
    unpriced = prices.copy(); unpriced["B"] = np.nan                                      # a held symbol never priced
    assert not gt.edge_timing(w, r, unpriced, 1)["passed"]
    for bad in (None, float("nan")):
        monkeypatch.setattr(gt, "timing_null", lambda *a, _b=bad, **k: {"p_value": _b, "shifts": 400})
        assert not gt.edge_timing(w, r, prices, 1)["passed"]
    for bad in (0, -1, 1.5, None, "3"):                                                  # a verdict, not an exception
        out = gt.edge_timing(w, r, prices, bad)
        assert not out["passed"] and "fails" in out["detail"] and "attempts" in out["detail"], bad


# ---- addendum 2: every figure the timing check reads is of the right kind, finite and in range, or the check fails

def _exact(**over):
    """A `timing_exact` result for the 400-bar window (358 copies, floor 1 / 359), with figures replaced."""
    base = {"actual_gross_sharpe": 1.0, "copies": 358, "copies_at_or_above": 0, "p_exact": 1 / 359, "floor": 1 / 359,
            "beats_every_copy": True}
    base.update(over)
    return lambda *a, **k: dict(base)


def _drawn(p):
    return lambda *a, **k: {"p_value": p, "shifts": 400}


def _fails_naming(out, word):
    return out["passed"] is False and "fails" in out["detail"] and word in out["detail"]


def test_the_number_of_attempts_must_be_a_whole_number_of_at_least_one():
    r, prices = _window(400)
    w = _skilled_book(r)
    for good in (1, np.int64(2)):
        assert gt.edge_timing(w, r, prices, good)["passed"], good
    for bad in (0, -1, True, False, 1.5, np.float64(2.0), None, "3", float("nan")):
        out = gt.edge_timing(w, r, prices, bad)
        assert _fails_naming(out, "attempts"), (bad, out["detail"])


def test_the_drawn_p_value_must_be_a_number_from_zero_to_one(monkeypatch):
    r, prices = _window(400)
    w = _skilled_book(r)
    for good in (0.0, 0, 0.01, np.float64(0.05)):
        monkeypatch.setattr(gt, "timing_null", _drawn(good))
        out = gt.edge_timing(w, r, prices, 1)
        assert out["region"] == "drawn" and out["passed"], good
    for bad in (-0.1, False, True, np.bool_(False), 1.5, float("nan"), float("inf"), -float("inf"), None, "0.001"):
        monkeypatch.setattr(gt, "timing_null", _drawn(bad))
        out = gt.edge_timing(w, r, prices, 1)
        assert _fails_naming(out, "p-value"), (bad, out["detail"])


def test_the_number_of_copies_must_be_a_whole_number_of_at_least_one(monkeypatch):
    r, prices = _window(400)
    w = _skilled_book(r)
    monkeypatch.setattr(gt, "timing_null", _drawn(0.001))
    for good in (358, np.int64(358)):
        monkeypatch.setattr(gt, "timing_exact", _exact(copies=good))
        assert gt.edge_timing(w, r, prices, 1)["passed"], good
    for bad in (True, np.bool_(True), 0, -3, 1.5, 358.0, None, "358"):
        monkeypatch.setattr(gt, "timing_exact", _exact(copies=bad))
        out = gt.edge_timing(w, r, prices, 1)
        assert _fails_naming(out, "copies"), (bad, out["detail"])


def test_the_reachable_floor_must_be_a_number_above_zero_and_at_most_one(monkeypatch):
    r, prices = _window(400)
    w = _skilled_book(r)
    monkeypatch.setattr(gt, "timing_null", _drawn(0.001))                  # both regions would pass on their figure
    for good in (1 / 359, 1.0):
        monkeypatch.setattr(gt, "timing_exact", _exact(floor=good))
        assert gt.edge_timing(w, r, prices, 1)["passed"], good
    for bad in (float("nan"), float("inf"), -1.0, 0.0, 1.5, True, None, "0.01"):
        monkeypatch.setattr(gt, "timing_exact", _exact(floor=bad))
        out = gt.edge_timing(w, r, prices, 1)
        assert _fails_naming(out, "floor"), (bad, out["detail"])


def test_beats_every_copy_passes_only_on_the_flag_true(monkeypatch):
    r, prices = _window(400)
    w = _skilled_book(r)
    monkeypatch.setattr(gt, "timing_exact", _exact(beats_every_copy=True))
    out = gt.edge_timing(w, r, prices, 50)
    assert out["region"] == "enumerated" and out["passed"]                 # 0.05 / 50 < 1 / 359: the twin passes
    for bad in ("yes", "True", 1, 1.0, None):
        monkeypatch.setattr(gt, "timing_exact", _exact(beats_every_copy=bad))
        out = gt.edge_timing(w, r, prices, 50)
        assert _fails_naming(out, "beats every copy"), (bad, out["detail"])


def test_the_one_trial_sharpe_check_refuses_a_mistyped_mark_or_figure(monkeypatch):
    good = pd.Series(0.001 + 0.01 * np.random.default_rng(1).standard_normal(2000))
    assert gt.one_trial_sharpe(good, 0.95)[1] and gt.one_trial_sharpe(good, np.float64(0.5))[1]      # the twins
    for bad in (False, True, 0, 0.0, -1.0, 1.0, float("nan"), None, "0.95"):
        value, ok, detail = gt.one_trial_sharpe(good, bad)
        assert not ok and "fails" in detail and "mark" in detail, (bad, detail)
    real = gt.research.probabilistic_sharpe_ratio
    for bad in (True, 1.5, -0.2, float("nan")):                                              # a figure of the wrong kind
        monkeypatch.setattr(gt.research, "probabilistic_sharpe_ratio", lambda *a, _b=bad, **k: _b)
        value, ok, detail = gt.one_trial_sharpe(good, 0.95)
        assert not ok and "fails" in detail, (bad, detail)

    def refuses(*a, **k):
        raise ValueError("Sharpe standard-error term is not positive; moments are inconsistent")
    monkeypatch.setattr(gt.research, "probabilistic_sharpe_ratio", refuses)
    value, ok, detail = gt.one_trial_sharpe(good, 0.95)
    assert not ok and "fails" in detail and value is None
    monkeypatch.setattr(gt.research, "probabilistic_sharpe_ratio", real)
    m = gt.research.sharpe_moments(good.to_numpy())
    for bad in ({"n_obs": True}, {"n_obs": 1.5}, {"sharpe": True}, {"skew": None}):
        monkeypatch.setattr(gt.research, "sharpe_moments", lambda *a, _m=replace(m, **bad), **k: _m)
        value, ok, detail = gt.one_trial_sharpe(good, 0.95)
        assert not ok and "fails" in detail and "moments" in detail, (bad, detail)


def test_a_development_figure_is_never_a_flag():
    assert gt._fin(1.0, 0, np.float64(-2.0), np.int64(3))
    for bad in (True, False, np.bool_(True), float("nan"), None, "1"):
        assert not gt._fin(1.0, bad), bad


def test_a_worst_stretch_that_is_not_a_number_fails():
    def stretches(*returns):
        return [{"start": f"2020-0{k + 1}-01", "end": f"2020-0{k + 1}-20", "market_return": -0.1, "strategy_return": x}
                for k, x in enumerate(returns)]
    ok, detail = gt._worst_stretch_check(stretches(-0.05, -0.02), 0.25)
    assert ok and "-5.0%" in detail                                                   # the twin
    assert not gt._worst_stretch_check(stretches(-0.05, -0.30), 0.25)[0]                # a real figure past the limit
    for bad in (float("nan"), float("inf"), -float("inf"), None, True, "-0.01"):
        ok, detail = gt._worst_stretch_check(stretches(-0.05, bad), 0.25)       # second, where min() would skip it
        assert not ok and "fails" in detail and "2020-02-01" in detail, (bad, detail)
    ok, detail = gt._worst_stretch_check([], 0.25)
    assert not ok and "no worst stretch could be formed" in detail


# ---- addendum 2: three tests the surviving mutants showed were missing

def test_the_region_boundary_belongs_to_the_drawn_region():
    days = 101                                                          # 59 distinct shifts: floor 1 / 60 == 0.05 / 3
    r, prices = _window(days)
    w = _skilled_book(r)
    at, past = gt.edge_timing(w, r, prices, 3), gt.edge_timing(w, r, prices, 4)
    assert at["copies"] == 59 and at["floor"] == at["threshold"] == 0.05 / 3          # exactly on the boundary
    assert at["region"] == "drawn" and at["drawn_p_value"] is not None             # `>` instead of `>=` fails here
    assert past["region"] == "enumerated"


def test_a_copy_whose_figure_is_not_a_number_counts_against_the_book(monkeypatch):
    days = 251
    r, prices = _window(days)
    w = _skilled_book(r)
    clean = gt.timing_exact(w, r)
    assert clean["copies_at_or_above"] == 0 and clean["beats_every_copy"] is True
    real, calls = gt._gross_sharpe, []

    def one_nan_copy(*a, **k):
        calls.append(1)
        return float("nan") if len(calls) == 2 else real(*a, **k)          # call 1 is the book, call 2 the first copy
    monkeypatch.setattr(gt, "_gross_sharpe", one_nan_copy)
    out = gt.timing_exact(w, r)
    assert out["copies"] == clean["copies"] and out["copies_at_or_above"] == 1
    assert out["beats_every_copy"] is False and out["p_exact"] == pytest.approx(2 / (out["copies"] + 1))


def test_develop_reads_the_engines_return_across_an_interior_gap(tmp_path, monkeypatch):
    prices = regime_market()
    prices.iloc[1200:1206, 2] = np.nan                                  # an interior gap inside the out-of-sample run
    prices.iloc[1400, 0] = np.nan
    _, _, g, hid = desk(tmp_path, prices, EDGE)
    real, seen = gt.timing_null, []

    def capture(w, r, *a, **k):
        seen.append((w, r))
        return real(w, r, *a, **k)
    monkeypatch.setattr(gt, "timing_null", capture)
    rep = g.develop(hid, momentum(lookback=20))
    w, r = seen[0]                                                      # the breaker's call
    dev = g.dev_prices
    booked, pct = gt.booked_asset_returns(dev).loc[r.index], dev.pct_change().loc[r.index]
    assert np.array_equal(r.to_numpy(), booked.to_numpy(), equal_nan=True)
    assert not np.array_equal(r.to_numpy(), pct.to_numpy(), equal_nan=True)
    figure = rep["breaker"]["timing_null"]["actual_gross_sharpe"]
    assert figure == real(w, booked)["actual_gross_sharpe"] != real(w, pct)["actual_gross_sharpe"]


# ---- R2 and R3 through the real gate

def test_an_edge_claim_passes_on_timing_skill_reports_its_figures_and_seals(tmp_path):
    paths, ledger, g, hid = desk(tmp_path, regime_market(), EDGE)
    other = ledger.propose("An idea on a label of its own", "macro-watcher", "another-label")
    for k in range(4):
        ledger.record_attempt(other, "backtest", 0.01 * k, 1000, "1.0.0")
    rep = g.develop(hid, momentum(lookback=20))
    assert rep["claim"] == "edge" and set(rep["checks"]) == EDGE_CHECKS
    assert rep["verdict"] == "pass (development); sealed test pending", rep["checks"]
    t = rep["timing"]
    assert t["attempts_counted"] == 5 and rep["attempts_counted"] == {"ledger": 5, "label": 1}       # every market
    assert t["threshold"] == pytest.approx(0.05 / 5) and t["region"] == "drawn"
    assert rep["breaker"]["timing_listed"]["shifts"] == 2000 and t["drawn_p_value"] == rep["breaker"]["timing_listed"]["p_value"]
    assert 0 < t["p_exact"] <= 1 and t["copies"] > 1000 and "stitched" not in t["note"]
    assert "timing_null" in rep["breaker"] and "variance_leaving_idea_out" in rep["deflated_sharpe"]
    stored = ledger.get(hid)["backtest_result"]
    assert stored["claim"] == "edge" and stored["timing"]["region"] == "drawn"
    sealed = g.seal(hid, momentum(lookback=20))
    assert sealed["claim"] == "edge" and ledger.get(hid)["sealed_test_result"]["claim"] == "edge"


def test_drift_alone_fails_an_edge_claim_and_passes_only_as_an_exposure_claim(tmp_path):
    _, ledger, g, hid = desk(tmp_path / "edge", drift_market(), EDGE)
    rep = g.develop(hid, momentum())
    assert rep["verdict"] == "fail" and not rep["checks"]["timing"]["passed"]
    assert rep["checks"]["sharpe_one_trial"]["passed"]                              # profitable, but not by its timing
    _, ledger, g, hid = desk(tmp_path / "exposure", drift_market(), EXPOSURE)
    rep = g.develop(hid, momentum())
    assert rep["claim"] == "exposure" and set(rep["checks"]) == EXPOSURE_CHECKS and "timing" not in rep
    assert rep["verdict"].startswith(EXPOSURE_PASS) and rep["verdict"].startswith("pass")
    assert rep["breaker"]["timing_listed"]["shifts"] == 300                        # reported, never a condition
    assert ledger.get(hid)["backtest_result"]["claim"] == "exposure"
    sealed = g.seal(hid, momentum())
    assert sealed["verdict"] == "pass" and sealed["claim"] == "exposure"
    assert ledger.get(hid)["sealed_test_result"]["claim"] == "exposure" and ledger.get(hid)["status"] == "passed backtest only"


def test_seal_refuses_a_pass_judged_before_the_split(tmp_path):
    _, ledger, g, hid = desk(tmp_path, drift_market(), EXPOSURE)
    rep = g.develop(hid, momentum())
    assert rep["verdict"].startswith("pass")
    h = ledger.get(hid)
    old = {k: v for k, v in h["backtest_result"].items() if k != "claim"}
    old["verdict"] = "pass (development); sealed test pending"
    ledger.record_backtest(hid, old, h["market_regime"], h["strategy_file"] or "", h["strategy_version"], "untested")
    with pytest.raises(gt.GauntletError, match="judged before the edge/exposure split: run the development test again"):
        g.seal(hid, momentum())
    assert not ledger.sealed_used(hid)                                              # refused before the data is touched


def test_an_empty_list_of_worst_stretches_fails_under_either_claim(tmp_path, monkeypatch):
    for name, prediction in (("edge", EDGE), ("exposure", EXPOSURE)):
        _, _, g, hid = desk(tmp_path / f"{name}-ok", drift_market(), prediction)
        assert g.develop(hid, momentum())["checks"]["worst_periods"]["passed"]          # the safe twin
    monkeypatch.setattr(gt, "worst_periods", lambda *a, **k: [])
    for name, prediction in (("edge", EDGE), ("exposure", EXPOSURE)):
        _, _, g, hid = desk(tmp_path / f"{name}-empty", drift_market(), prediction)
        check = g.develop(hid, momentum())["checks"]["worst_periods"]
        assert not check["passed"] and "no worst stretch could be formed" in check["detail"]


# ---- R6: one definition of an asset's return

def test_the_gate_takes_the_engines_booked_return():
    prices = regime_market(n=400)
    assert np.array_equal(gt.booked_asset_returns(prices).to_numpy(), prices.pct_change().to_numpy(), equal_nan=True)
    gappy = prices.copy()
    gappy.iloc[:50, 1] = np.nan                                         # a late listing
    gappy.iloc[200:210, 2] = np.nan                                     # an interior gap
    gappy.iloc[300, 0] = np.nan
    ret = gt.booked_asset_returns(gappy)
    assert np.array_equal(np.nan_to_num(ret.to_numpy(), nan=0.0), engine.booked_returns(gappy.to_numpy(float)))
    assert np.isfinite(ret.iloc[210, 2]) and not np.isfinite(gappy.pct_change().iloc[210, 2])    # the bar after the gap
    held = pd.DataFrame(0.15, index=gappy.index, columns=gappy.columns)
    res = engine.run_backtest(gappy, held, COST)
    attribution = (res.weights.shift(1) * ret).sum().sum()
    assert np.isclose(attribution, res.gross.sum(), rtol=1e-12, atol=0.0)


def test_the_report_is_strict_json_under_both_claims(tmp_path):
    for name, prediction in (("edge", EDGE), ("exposure", EXPOSURE)):
        paths, _, g, hid = desk(tmp_path / name, drift_market(), prediction)
        g.develop(hid, momentum())
        saved = json.loads((paths.backtests / "claims_test-v1.0.0-attempt1.json").read_text())
        assert saved["claim"] == name and math.isfinite(saved["attempts_counted"]["ledger"])


# ---- addendum 3: `develop` never raises on a bad Sharpe figure, under either claim

NAN, INF = float("nan"), float("inf")
BAD_MOMENTS = [("sharpe", None), ("sharpe", NAN), ("sharpe", "0.05"), ("sharpe", INF),
               ("skew", None), ("skew", NAN), ("skew", "0"),
               ("kurt", None), ("kurt", NAN), ("kurt", "3"),
               ("n_obs", None), ("n_obs", True), ("n_obs", "1500"), ("n_obs", 1500.0)]
BAD_PSR = [None, NAN, "0.99"]                                           # with BAD_MOMENTS: 17 cases
NOT_PROBABILITIES = [True, np.True_, 1.5, 3.0, NAN, INF, -0.1]


def _bad_moments(field, value):
    real = gt.research.sharpe_moments
    return lambda *a, **k: replace(real(*a, **k), **{field: value})


def _refused_through_develop(prediction, word, g, ledger, hid):
    rep = g.develop(hid, momentum())                                    # a verdict, never an exception
    check = rep["checks"]["sharpe_one_trial" if prediction.claim == "edge" else "deflated_sharpe"]
    assert not check["passed"] and "fails" in check["detail"] and word in check["detail"], check
    assert rep["verdict"] == "fail" and ledger.get(hid)["status"] == "failed"
    with pytest.raises(gt.GauntletError):
        g.seal(hid, momentum())
    assert not ledger.sealed_used(hid)                                   # nothing sealed


def test_the_twins_pass_the_check_that_reads_the_sharpe_figure(tmp_path):
    for name, prediction, check in (("edge", EDGE, "sharpe_one_trial"), ("exposure", EXPOSURE, "deflated_sharpe")):
        _, _, g, hid = desk(tmp_path / name, drift_market(), prediction)
        assert g.develop(hid, momentum())["checks"][check]["passed"]


@pytest.mark.parametrize("prediction", [EDGE, EXPOSURE], ids=["edge", "exposure"])
@pytest.mark.parametrize("field,value", BAD_MOMENTS, ids=[f"{f}={v!r}" for f, v in BAD_MOMENTS])
def test_develop_fails_a_bad_moment_with_a_reason(tmp_path, monkeypatch, prediction, field, value):
    _, ledger, g, hid = desk(tmp_path, drift_market(), prediction)
    monkeypatch.setattr(gt.research, "sharpe_moments", _bad_moments(field, value))
    _refused_through_develop(prediction, "moments", g, ledger, hid)


@pytest.mark.parametrize("prediction", [EDGE, EXPOSURE], ids=["edge", "exposure"])
@pytest.mark.parametrize("value", BAD_PSR, ids=[repr(v) for v in BAD_PSR])
def test_develop_fails_a_bad_probabilistic_sharpe_with_a_reason(tmp_path, monkeypatch, prediction, value):
    _, ledger, g, hid = desk(tmp_path, drift_market(), prediction)
    monkeypatch.setattr(gt.research, "probabilistic_sharpe_ratio", lambda *a, _v=value, **k: _v)
    word = "Probabilistic Sharpe" if prediction.claim == "edge" else "for_idea"
    _refused_through_develop(prediction, word, g, ledger, hid)


# ---- addendum 3: the exposure claim's figures are probabilities, each of them, before `min` is taken

@pytest.mark.parametrize("value", NOT_PROBABILITIES, ids=[repr(v) for v in NOT_PROBABILITIES])
def test_the_idea_figure_of_the_deflated_sharpe_must_be_a_probability(tmp_path, monkeypatch, value):
    _, ledger, g, hid = desk(tmp_path, drift_market(), EXPOSURE)
    monkeypatch.setattr(gt.research, "probabilistic_sharpe_ratio", lambda *a, _v=value, **k: _v)
    _refused_through_develop(EXPOSURE, "for_idea", g, ledger, hid)


def _desk_with_market_attempts(tmp_path):
    """One attempt of the idea's own (its figure is the Probabilistic Sharpe), four on its market label (that
    figure is the Deflated Sharpe)."""
    paths, ledger, g, hid = desk(tmp_path, drift_market(), EXPOSURE)
    for k in range(3):
        other = ledger.propose(f"another idea on the label {k}", "price-analyst", "synthetic")
        ledger.record_attempt(other, "backtest", 0.01 * (k + 1), 1000, "1.0.0")
    return ledger, g, hid


def test_the_market_figure_twin_passes(tmp_path):
    ledger, g, hid = _desk_with_market_attempts(tmp_path)
    rep = g.develop(hid, momentum())
    d = rep["deflated_sharpe"]
    assert rep["checks"]["deflated_sharpe"]["passed"] and d["attempts_on_market"] == 4
    assert d["value"] == min(d["for_idea"], d["for_market"]) and d["for_idea"] != d["for_market"]


@pytest.mark.parametrize("value", NOT_PROBABILITIES, ids=[repr(v) for v in NOT_PROBABILITIES])
def test_the_market_figure_of_the_deflated_sharpe_must_be_a_probability(tmp_path, monkeypatch, value):
    ledger, g, hid = _desk_with_market_attempts(tmp_path)
    monkeypatch.setattr(gt.research, "deflated_sharpe_ratio", lambda *a, _v=value, **k: _v)   # `min` must not drop it
    _refused_through_develop(EXPOSURE, "for_market", g, ledger, hid)


# ---- addendum 3: the pass marks are judged when they are made

def test_every_figure_of_a_prediction_is_judged_at_construction():
    good = dict(annual_return=0.05, win_rate=0.5, sharpe=0.8, fail_if_sharpe_below=0.3, fail_if_drawdown_above=0.25)
    p = gt.Prediction(**{k: np.float32(v) for k, v in good.items()})                   # numpy reals are numbers
    assert p.fail_if_drawdown_above == pytest.approx(0.25) and type(p.fail_if_drawdown_above) is float
    gt.Prediction(**{**good, "fail_if_sharpe_below": -0.5, "fail_if_drawdown_above": 1.0, "win_rate": 0,
                     "annual_return": -0.1})                                            # the edges of each range
    bad = {"annual_return": (True, NAN, INF, None, "0.05"), "sharpe": (True, NAN, -INF, None, "0.8"),
           "win_rate": (True, -0.1, 1.1, NAN, None, "0.5"),
           "fail_if_sharpe_below": (True, np.bool_(False), NAN, INF, -INF, None, "0.3"),
           "fail_if_drawdown_above": (True, np.bool_(True), 0, 0.0, -0.1, 1.5, NAN, INF, None, "0.25")}
    for field, values in bad.items():
        for v in values:
            with pytest.raises(ValueError, match=field):
                gt.Prediction(**{**good, field: v})


def test_every_pass_mark_of_the_criteria_is_judged_at_construction():
    c = gt.Criteria(min_dsr=np.float32(0.95), min_positive_windows=np.float64(0.6), min_stress_sharpe=np.float32(0.0),
                    sealed_fraction=np.float64(0.25))
    assert c.min_dsr == pytest.approx(0.95) and type(c.min_dsr) is float
    gt.Criteria(min_positive_windows=1.0, min_stress_sharpe=-0.5)                     # the edges of each range
    bad = {"min_dsr": (True, 0.0, 1.0, NAN, None, "0.95"),
           "min_positive_windows": (0, 0.0, -1, 1.5, NAN, INF, True, None, "0.6"),
           "min_stress_sharpe": (NAN, INF, -INF, True, None, "0"),
           "sealed_fraction": (True, NAN, None, "0.25")}
    for field, values in bad.items():
        for v in values:
            with pytest.raises(ValueError, match=field):
                gt.Criteria(**{field: v})


# ---- addendum 3: a count of observations is a whole number

def test_a_float_count_of_observations_fails_the_one_trial_check(monkeypatch):
    good = pd.Series(0.001 + 0.01 * np.random.default_rng(1).standard_normal(2000))
    assert gt.one_trial_sharpe(good, 0.95)[1]                                          # the twin: n_obs 2000
    monkeypatch.setattr(gt.research, "sharpe_moments", _bad_moments("n_obs", 2000.0))
    value, ok, detail = gt.one_trial_sharpe(good, 0.95)
    assert not ok and value is None and "moments" in detail
