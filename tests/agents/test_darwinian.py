import numpy as np
import pandas as pd
import pytest

from valedia_trader.agents import darwinian as dw


def test_darwinian_update_rule_and_bounds():
    d = dw.DarwinianWeights(["a", "b", "c", "d"])
    out = d.update({"a": 3.0, "b": 2.0, "c": 1.0, "d": 0.0})
    assert out == pytest.approx({"a": 1.05, "b": 1.0, "c": 1.0, "d": 0.95})
    assert sum(d.shares().values()) == pytest.approx(1.0) and d.shares()["a"] > d.shares()["d"]
    for _ in range(400):
        d.update({"a": 3.0, "b": 2.0, "c": 1.0, "d": 0.0})
    assert d.weights["a"] == 2.5 and d.weights["d"] == 0.3              # clipped to [0.3, 2.5]
    same = dw.DarwinianWeights(["a", "b", "c", "d"])
    assert same.update({k: 1.0 for k in "abcd"}) == {k: 1.0 for k in "abcd"}   # no dispersion, no change
    nan = dw.DarwinianWeights(["a", "b", "c"])
    assert nan.update({"a": 2.0, "b": float("nan"), "c": 0.0}) == pytest.approx({"a": 1.05, "b": 1.0, "c": 0.95})
    with pytest.raises(ValueError):
        dw.DarwinianWeights(["a", "a"])


def test_scores():
    rng = np.random.default_rng(0)
    r = pd.DataFrame({"good": 0.002 + 0.01 * rng.standard_normal(300), "bad": -0.002 + 0.01 * rng.standard_normal(300),
                      "flat": np.zeros(300)})
    s = dw.rolling_sharpe_scores(r, 250)
    assert s["good"] > 1 > -1 > s["bad"] and np.isnan(s["flat"])
    assert dw.brier_score([0.9, 0.2, 0.6], [1, 0, 0]) == pytest.approx((0.01 + 0.04 + 0.36) / 3)
    with pytest.raises(ValueError):
        dw.brier_score([1.2], [1])


def test_reflexivity_monitor_flags_a_loop_after_five_rounds():
    m = dw.ReflexivityMonitor()
    for i in range(4):
        assert not m.observe("narrative_flows", +1).reversal_risk
    assert m.observe("narrative_flows", +0.3).reversal_risk and m.reversal_risk()
    assert m.state()["narrative_flows"].streak == 5
    assert m.observe("narrative_flows", -1).streak == 1 and not m.reversal_risk()   # the loop reversed
    assert m.observe("pnl_behavior", 0).streak == 0
    assert set(m.state()) == set(dw.REFLEXIVE_LOOPS)
    with pytest.raises(KeyError):
        m.observe("moon_phase", 1)


def history(seed=0, n=300, vol=0.01):
    return np.random.default_rng(seed).standard_normal((n, 3)) * vol


def test_risk_officer_resizes_vetoes_and_latches():
    cro = dw.RiskOfficer(dw.RiskLimits(max_var=0.02, max_es=0.03, max_drawdown=0.10))
    ok = cro.review([0.3, 0.3, 0.3], history(), 100.0, 0.0)
    assert ok.approved and ok.scale == 1.0 and ok.var < 0.02
    big = cro.review([3.0, 3.0, 3.0], history(), 100.0, 0.0)
    assert big.approved and 0 < big.scale < 1 and big.var <= 0.02 + 1e-9 and "resized" in big.reasons[0]
    assert np.allclose(big.weights, np.array([3.0, 3.0, 3.0]) * big.scale)
    hit = cro.review([0.3, 0.3, 0.3], history(), 89.0, -0.01)          # 11% below the 100 peak
    assert not hit.approved and hit.scale == 0.0 and not hit.weights.any()
    assert not cro.review([0.1, 0.1, 0.1], history(), 99.0, 0.0).approved        # still latched
    cro.reset("burton", 99.0)
    assert cro.review([0.1, 0.1, 0.1], history(), 99.0, 0.0).approved
    fresh = dw.RiskOfficer()
    assert not fresh.review([0.1, 0.1, 0.1], history(n=20), 100.0, 0.0).approved  # too little history
    assert not dw.RiskOfficer().review([float("nan"), 0, 0], history(), 100.0, 0.0).approved
    assert not dw.RiskOfficer().review([0.1, 0.1, 0.1], history(), 100.0, -0.05).approved   # daily loss limit


def test_committee_blends_sizes_and_respects_caps():
    rng = np.random.default_rng(1)
    names = ["trend", "carry"]
    darwin = dw.DarwinianWeights(names, weights={"trend": 2.0, "carry": 1.0})
    committee = dw.DecisionCommittee(darwin, dw.RiskOfficer(dw.RiskLimits(max_var=1.0, max_es=1.0)), max_gross=1.5,
                                     base_kelly_multiplier=0.25, regime_multipliers={"calm": 0.5, "storm": 0.15})
    targets = {"trend": np.array([1.0, 0.0, 0.0]), "carry": np.array([0.0, 0.5, -0.5])}
    sleeve_r = pd.DataFrame({"trend": 0.001 + 0.01 * rng.standard_normal(250), "carry": 0.0005 + 0.005 * rng.standard_normal(250)})
    dec = committee.decide(targets, sleeve_r, history(), 100.0, 0.0)
    assert dec.shares == pytest.approx({"trend": 2 / 3, "carry": 1 / 3})
    assert np.abs(dec.weights).sum() <= 1.5 + 1e-9 and dec.kelly_multiplier == 0.25
    direction = dec.weights / np.abs(dec.weights).sum()
    blend = np.array([2 / 3, 1 / 6, -1 / 6])
    assert np.allclose(direction, blend / np.abs(blend).sum())
    assert committee.kelly_multiplier({"calm": 1.0}, False) == 0.5
    assert committee.kelly_multiplier({"calm": 0.5, "storm": 0.5}, False) == pytest.approx(0.325)
    assert committee.kelly_multiplier({"calm": 1.0}, True) == 0.15       # reflexive reversal -> minimum size
    losing = sleeve_r - 0.01
    assert not committee.decide(targets, losing, history(), 100.0, 0.0).weights.any()   # no edge, no position
