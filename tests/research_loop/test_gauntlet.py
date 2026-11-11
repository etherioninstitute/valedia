import json
import os

import numpy as np
import pandas as pd
import pytest

from valedia_trader.backtest.engine import CostModel
from valedia_trader.research_loop import LoopPaths
from valedia_trader.research_loop import gauntlet as gt
from valedia_trader.research_loop import ledger as lg
from valedia_trader.research_loop import strategy as st

COST = CostModel(fees_bps=1.0, slippage_bps=1.0, borrow_annual=0.02, funding_annual=0.0)
PRED = gt.Prediction(annual_return=0.05, win_rate=0.5, sharpe=0.8, fail_if_sharpe_below=0.3, fail_if_drawdown_above=0.25)


def market(seed=0, drift=(0.0009, 0.0008, 0.0007, -0.0007, -0.0008, -0.0009), n=2400):
    rng = np.random.default_rng(seed)
    r = np.array(drift) + 0.01 * rng.standard_normal((n, len(drift)))
    return pd.DataFrame(100 * np.exp(np.cumsum(r, axis=0)), index=pd.bdate_range("2012-01-02", periods=n), columns=list("ABCDEF"))


def momentum(version="1.0.0", lookback=120, name="trend_test"):
    return st.RuleStrategy.from_spec({
        "name": name, "version": version, "hypothesis": "Assets with a persistent drift keep drifting the same way",
        "market": list("ABCDEF"), "entry": {"rule": "momentum", "lookback": lookback, "enter": 0.02, "exit": 0.0, "direction": "both"},
        "sizing": {"rule": "equal"}, "risk": {"max_position_pct": 0.15, "max_open_positions": 6, "stop_loss_pct": None}})


@pytest.fixture
def env(tmp_path):
    paths = LoopPaths(tmp_path).ensure()
    ledger = lg.HypothesisLedger(paths.ledger)
    prices = market()
    g = gt.Gauntlet(prices, None, COST, ledger, paths)
    hid = ledger.propose("Assets with a persistent drift keep drifting the same way", "price-analyst", "synthetic")
    return paths, ledger, prices, g, hid


def test_no_backtest_without_a_prediction_and_none_after_it_is_edited(env):
    paths, ledger, _, g, hid = env
    with pytest.raises(gt.PredictionMissing):
        g.develop(hid, momentum())
    path = gt.write_prediction(ledger, paths, hid, "trend_test", PRED)
    assert path.name == "trend_test-prediction.md" and not os.access(path, os.W_OK)
    assert "Expected Sharpe ratio: 0.80" in path.read_text() and "Written before any backtest" in path.read_text()
    with pytest.raises((gt.GauntletError, lg.PredictionExists)):
        gt.write_prediction(ledger, paths, hid, "trend_test", PRED)
    os.chmod(path, 0o644)
    path.write_text(path.read_text().replace("0.80", "0.10"))            # moving the goalposts
    with pytest.raises(gt.PredictionTampered):
        g.develop(hid, momentum())
    assert ledger.variations(hid) == 0                                    # nothing ran
    with pytest.raises(ValueError):
        gt.Prediction(0.1, 1.5, 1.0, 0.0, 0.2)


def test_development_never_sees_the_sealed_period(env):
    _, _, prices, g, _ = env
    assert len(g.dev_prices) == 1800 and g.sealed_start == prices.index[1800]
    assert g.dev_prices.index[-1] < g.sealed_start
    assert not hasattr(g, "prices") and not hasattr(g, "_prices")
    for bad in ({"sealed_fraction": 0.5}, {"sealed_fraction": 0.1}, {"max_revisions": 40}, {"max_revisions": 2}, {"walk_forward_windows": 1}):
        with pytest.raises(ValueError):
            gt.Criteria(**bad)
    with pytest.raises(gt.GauntletError):
        gt.Gauntlet(prices.iloc[:500], None, COST, lg.HypothesisLedger(), LoopPaths(prices.attrs.get("x", ".")))


def test_a_real_edge_passes_development_then_its_one_sealed_test(env):
    paths, ledger, prices, g, hid = env
    gt.write_prediction(ledger, paths, hid, "trend_test", PRED)
    seen = []
    s = momentum()
    original = s.target_weights
    s.target_weights = lambda px: (seen.append(px.index[-1]), original(px))[1]
    rep = g.develop(hid, s, "strategies/trend_test.json")
    assert max(seen) < g.sealed_start                                     # the strategy was never shown sealed data
    assert rep["verdict"].startswith("pass") and all(c["passed"] for c in rep["checks"].values())
    assert set(rep["checks"]) == {"prediction_failure_terms", "deflated_sharpe", "walk_forward", "double_costs", "delayed_fills", "worst_periods"}
    assert len(rep["walk_forward"]) == 5 and rep["positive_windows"] >= 0.6 and rep["result"]["trades"] > 0
    assert len(rep["breaker"]["worst_periods"]) == 5
    assert 0.0 < rep["breaker"]["timing_null"]["p_value"] <= 1.0 and "sharpe" in rep["breaker"]["market_basket"]      # reported, no new gate
    assert rep["breaker"]["double_costs"]["sharpe"] < rep["result"]["sharpe"]             # costs hurt
    assert rep["deflated_sharpe"]["variations_of_idea"] == 1 and rep["attempt"] == 1
    assert set(rep["versus_prediction"]) == {"annual_return", "win_rate", "sharpe"}
    h = ledger.get(hid)
    assert h["status"] == "untested" and h["variations_tried"] == 1 and h["market_regime"] in lg.REGIMES
    assert h["strategy_file"] == "strategies/trend_test.json" and h["strategy_version"] == "1.0.0"
    saved = json.loads((paths.backtests / "trend_test-v1.0.0-attempt1.json").read_text())
    assert saved["verdict"] == rep["verdict"] and saved["sealed_period"].startswith("sealed")
    diag = saved["diagnostics"]                                           # the sheet's view rides along; it adds no gate
    assert "error" not in diag and diag["multiple_testing"]["variations"] == 1
    assert diag["market_exposure"]["beta"] is not None and len(diag["book_risk"]["assets_held"]) >= 2

    sealed = g.seal(hid, momentum(), "strategies/trend_test.json")
    assert sealed["verdict"] == "pass" and sealed["sealed_period"][0] == str(g.sealed_start.date())
    assert "formula" in sealed["diagnostics"]["uncertainty_of_the_mean"]
    assert ledger.get(hid)["status"] == "passed backtest only"
    with pytest.raises(lg.SealedPeriodUsed):
        g.seal(hid, momentum())                                           # once means once
    with pytest.raises(lg.SealedPeriodUsed):
        g.develop(hid, momentum("1.1.0", 60))                             # and no revisions after the look
    assert (paths.backtests / "trend_test-v1.0.0-sealed.json").is_file()


def test_no_edge_fails_and_the_failure_is_remembered(tmp_path):
    paths = LoopPaths(tmp_path).ensure()
    ledger = lg.HypothesisLedger(paths.ledger)
    g = gt.Gauntlet(market(seed=1, drift=(0.0,) * 6), None, COST, ledger, paths)
    hid = ledger.propose("Momentum works on pure noise", "price-analyst", "synthetic")
    gt.write_prediction(ledger, paths, hid, "trend_test", PRED)
    rep = g.develop(hid, momentum())
    assert rep["verdict"] == "fail" and not rep["checks"]["deflated_sharpe"]["passed"]
    h = ledger.get(hid)
    assert h["status"] == "failed" and "failed the development test" in h["lessons_learned"]
    assert "worst_symbols" in rep["where_it_loses"] and rep["where_it_loses"]["worst_window"]["return"] <= 0
    with pytest.raises(gt.GauntletError, match="passed development test"):
        g.seal(hid, momentum())                                           # a failed idea never reaches the sealed data
    assert not ledger.sealed_used(hid)
    with pytest.raises(lg.DuplicateIdea):
        ledger.propose("Momentum works on pure noise", "macro-watcher", "synthetic")


def test_revisions_are_counted_capped_and_need_a_new_version(tmp_path):
    paths = LoopPaths(tmp_path).ensure()
    ledger = lg.HypothesisLedger(paths.ledger)
    g = gt.Gauntlet(market(seed=1, drift=(0.0,) * 6), None, COST, ledger, paths, gt.Criteria(max_revisions=3))
    hid = ledger.propose("Momentum works on pure noise", "price-analyst", "synthetic")
    gt.write_prediction(ledger, paths, hid, "trend_test", PRED)
    g.develop(hid, momentum("1.0.0", 120))
    with pytest.raises(gt.GauntletError, match="new version"):
        g.develop(hid, momentum("1.0.0", 60))                             # changed rules under an old version number
    first_dsr = None
    for i, lookback in enumerate((60, 90, 150), start=1):
        rep = g.develop(hid, momentum(f"1.{i}.0", lookback))
        assert rep["revisions_used"] == i and rep["deflated_sharpe"]["variations_of_idea"] == i + 1
        first_dsr = first_dsr or rep
    assert ledger.revisions(hid) == 3 and ledger.variations(hid) == 4
    with pytest.raises(gt.RevisionCapReached):
        g.develop(hid, momentum("1.4.0", 200))
    h = ledger.get(hid)
    assert h["status"] == "failed" and "more than 3 rewrites" in h["lessons_learned"] and ledger.variations(hid) == 4


def test_more_attempts_raise_the_bar(env):
    paths, ledger, _, g, hid = env
    gt.write_prediction(ledger, paths, hid, "trend_test", PRED)
    net = pd.Series(0.0004 + 0.01 * np.random.default_rng(5).standard_normal(1500))
    ledger.record_attempt(hid, "backtest", 0.04, 1500, "1.0.0")
    one = g._deflated(net, hid, "synthetic")
    for k in range(30):
        ledger.record_attempt(hid, "revision", 0.02 + 0.002 * k, 1500, f"1.{k + 1}.0")
    many = g._deflated(net, hid, "synthetic")
    assert many["variations_of_idea"] == 31 and many["value"] < one["value"]
    other = ledger.propose("An unrelated idea on the same market", "macro-watcher", "synthetic")
    assert g._deflated(net, other, "synthetic")["for_market"] == pytest.approx(many["for_market"])     # the market count is shared


def test_walk_forward_picks_each_windows_variant_from_earlier_data_only(env):
    paths, ledger, _, g, hid = env
    gt.write_prediction(ledger, paths, hid, "trend_test", PRED)
    variants = {"fast": momentum(lookback=40), "slow": momentum(lookback=200)}
    rep = g.develop(hid, momentum(), variants=variants)
    assert ledger.variations(hid) == 2                                    # both variants are counted
    assert {w["variant"] for w in rep["walk_forward"]} <= {"fast", "slow"}
    assert pd.Timestamp(rep["out_of_sample_from"]) >= g.dev_prices.index[252 + 504]


def test_breaker_helpers():
    doubled = gt.stressed_costs(CostModel({"A": 1.0}, 2.0, 0.02, 0.0, impact_y=1.0))
    assert doubled.fees_bps == {"A": 2.0} and doubled.slippage_bps == 4.0 and doubled.borrow_annual == 0.04 and doubled.impact_y == 2.0
    r = pd.Series(0.001, index=pd.bdate_range("2020-01-01", periods=400))
    r.iloc[100:120] = -0.03
    r.iloc[300:310] = -0.02
    worst = gt.worst_periods(r, count=3, days=20)
    assert len(worst) == 3 and worst[0][0] == r.index[100] and worst[0][2] == pytest.approx(0.97 ** 20 - 1)
    spans = [(a, b) for a, b, _ in worst]
    assert all(a2 > b1 or b2 < a1 for i, (a1, b1) in enumerate(spans) for (a2, b2) in spans[i + 1:])    # no overlap
    assert gt.worst_periods(r.iloc[:10], 5, 63) == []


def test_timing_null_tells_timing_skill_from_plain_exposure():
    rng = np.random.default_rng(4); n = 1500
    idx = pd.bdate_range("2018-01-01", periods=n)
    r = pd.DataFrame({"A": rng.normal(0.0004, 0.01, n), "B": rng.normal(0.0004, 0.012, n)}, index=idx)
    skilled = (r.shift(-1) > 0).astype(float) * 0.5                    # holds each asset only on the eve of an up day
    exposed = pd.DataFrame(0.5, index=idx, columns=r.columns)          # always long: no timing at all
    exposed.iloc[::7] = 0.0
    a, b = gt.timing_null(skilled, r, n=120), gt.timing_null(exposed, r, n=120)
    assert a["p_value"] < 0.02 and a["actual_gross_sharpe"] > a["null_95th"]
    assert b["p_value"] > 0.10                                         # re-timed copies do just as well
    assert abs(b["actual_gross_sharpe"] - b["average_book_held_throughout_sharpe"]) < 0.5
    assert gt.timing_null(skilled, r, n=50)["p_value"] == gt.timing_null(skilled, r, n=50)["p_value"]      # seeded
    assert "unavailable" in gt.timing_null(skilled.iloc[:30], r.iloc[:30])
    assert "unavailable" in gt.timing_null(skilled * 0.0, r)
