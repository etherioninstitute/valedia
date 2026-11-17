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
    for bad in (0, -1, 1.5, None, "3"):
        with pytest.raises(ValueError):
            gt.edge_timing(w, r, prices, bad)


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
