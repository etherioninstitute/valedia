import math

import numpy as np
import pandas as pd
import pytest

from valedia_trader.quant_engine import level10_ml_research as L10


def test_formula_registry_complete():
    assert set(L10.FORMULAS) == {"10.1", "10.2", "10.3", "10.4", "10.5"}
    for names in L10.FORMULAS.values():
        for n in names:
            assert callable(getattr(L10, n))


# 10.1
def test_point_in_time_store_never_returns_the_future():
    store = L10.PointInTimeStore()
    store.add(pd.DataFrame({
        "symbol": ["AAA", "AAA", "BBB"],
        "event_time": ["2024-03-31", "2024-03-31", "2024-03-31"],
        "knowledge_time": ["2024-04-25", "2024-06-10", "2024-05-02"],   # second AAA row is a restatement
        "eps": [1.00, 1.10, 2.00]}))
    with pytest.raises(L10.LookAheadError):
        store.as_of()                                                   # clock not set
    store.set_clock("2024-05-01")
    seen = store.as_of()
    assert seen["eps"].tolist() == [1.00]
    assert (seen["knowledge_time"] <= pd.Timestamp("2024-05-01")).all()
    with pytest.raises(L10.LookAheadError):
        store.as_of("2024-06-30")                                       # beyond the clock
    store.set_clock("2024-07-01")
    assert store.as_of(latest_by="symbol").set_index("symbol")["eps"].to_dict() == {"AAA": 1.10, "BBB": 2.00}
    assert store.as_of("2024-05-15")["eps"].tolist() == [1.00, 2.00]    # the past may be re-read
    with pytest.raises(L10.LookAheadError):
        store.set_clock("2024-01-01")                                   # clock cannot run backwards
    with pytest.raises(ValueError):
        store.add(pd.DataFrame({"event_time": ["2024-05-01"], "knowledge_time": ["2024-04-01"]}))


def test_universe_includes_names_that_were_later_delisted():
    members = pd.DataFrame({"symbol": ["LIVE", "DEAD", "NEW"],
                            "start": ["2010-01-01", "2010-01-01", "2022-01-01"],
                            "end": [None, "2020-06-30", None]})
    assert L10.universe_as_of(members, "2015-01-01") == ["DEAD", "LIVE"]
    assert L10.universe_as_of(members, "2023-01-01") == ["LIVE", "NEW"]


def test_lookahead_detector_flags_centered_window_only():
    rng = np.random.default_rng(0)
    data = pd.DataFrame({"px": 100 + np.cumsum(rng.standard_normal(300))})

    def feats(df):
        return pd.DataFrame({"trailing": df["px"].rolling(10).mean(),
                             "centered": df["px"].rolling(11, center=True).mean(),
                             "full_sample_z": (df["px"] - df["px"].mean()) / df["px"].std()})

    assert L10.detect_lookahead(feats, data) == ["centered", "full_sample_z"]
    assert L10.detect_lookahead(lambda df: df[["px"]].shift(1), data) == []


def test_cost_model_must_be_complete():
    assert L10.check_cost_completeness({"fees": 1.0, "slippage": 2.0}) == ["borrow", "funding"]
    L10.require_complete_costs({"fees": 1.0, "slippage": 2.0, "borrow": 0.0, "funding": 0.0})
    with pytest.raises(L10.CostModelError, match="funding"):
        L10.require_complete_costs({"fees": 1.0, "slippage": 2.0, "borrow": 0.5, "funding": float("nan")})


# 10.2
def test_walk_forward_splits_never_train_on_the_future():
    rolling = list(L10.walk_forward_splits(100, 40, 10, embargo=2))
    assert len(rolling) == 5
    for train, test in rolling:
        assert train.max() + 2 < test.min() and len(train) == 40 and len(test) == 10
    expanding = list(L10.walk_forward_splits(100, 40, 10, expanding=True))
    assert [len(tr) for tr, _ in expanding] == [40, 50, 60, 70, 80, 90]
    assert all(tr[0] == 0 for tr, _ in expanding)


def test_purged_kfold_purges_overlap_and_applies_embargo():
    n, horizon, embargo = 200, 5, 3
    cv = L10.PurgedKFold(n_splits=5, label_horizon=horizon, embargo=embargo)
    assert cv.get_n_splits() == 5
    ends = np.minimum(np.arange(n) + horizon, n - 1)
    covered = []
    for train, test in cv.split(np.zeros((n, 2))):
        covered.extend(test.tolist())
        lo, hi = test.min(), int(ends[test].max())
        for i in train:                                       # no training label interval overlaps the test span
            assert ends[i] < lo or i > hi
        assert not np.any((train > hi) & (train <= hi + embargo))   # embargo respected
        assert not set(train) & set(test)
    assert sorted(covered) == list(range(n))
    custom = np.minimum(np.arange(60) + 10, 59)
    for train, test in L10.PurgedKFold(3).split(np.zeros(60), t1=custom):
        assert all(custom[i] < test.min() or i > custom[test].max() for i in train)
    with pytest.raises(ValueError):
        L10.PurgedKFold(n_splits=1)


# 10.3
def test_expected_max_sharpe_reference_and_dsr_properties():
    sr0 = L10.expected_max_sharpe(50, 0.25)                   # 50 trials, sigma(SR) = 0.5
    assert sr0 == pytest.approx(1.138, abs=2e-3)
    assert L10.deflated_sharpe_ratio(sr0, 500, 50, 0.25) == pytest.approx(0.5)
    base = L10.deflated_sharpe_ratio(0.15, 1000, 20, 0.002)
    assert L10.deflated_sharpe_ratio(0.15, 2000, 20, 0.002) > base        # more data helps
    assert L10.deflated_sharpe_ratio(0.15, 1000, 200, 0.002) < base       # more trials hurt
    assert L10.deflated_sharpe_ratio(0.15, 1000, 20, 0.002, skew=-1.0, kurt=8.0) < base   # ugly tails hurt
    assert L10.probabilistic_sharpe_ratio(0.1, 0.0, 400) == pytest.approx(
        float(__import__("scipy").stats.norm.cdf(0.1 * math.sqrt(399) / math.sqrt(1 + 0.5 * 0.01))))
    with pytest.raises(ValueError):
        L10.expected_max_sharpe(1, 0.25)


def test_trial_registry_drives_the_dsr_gate(tmp_path):
    rng = np.random.default_rng(1)
    good = 0.004 + 0.01 * rng.standard_normal(1500)                      # per-period SR ~ 0.4
    reg = L10.TrialRegistry(str(tmp_path / "trials.sqlite"))
    assert not L10.dsr_gate(good, reg).accepted                          # no trials recorded: fail closed
    reg.record("v1", 0.02, 1500)
    assert L10.dsr_gate(good, reg).reason.startswith("rejected: fewer than 2")
    for i, s in enumerate(rng.normal(0.0, 0.03, 30)):
        reg.record(f"variant{i}", float(s), 1500, {"lookback": i})
    assert reg.n_trials() == 31 and reg.sharpe_variance() > 0
    ok = L10.dsr_gate(good, reg)
    assert ok.accepted and ok.dsr > 0.95 and ok.sharpe > ok.benchmark_sharpe > 0
    lucky = 0.0005 + 0.01 * rng.standard_normal(1500)                    # SR ~ 0.05: inside the selection noise
    assert not L10.dsr_gate(lucky, reg).accepted
    assert not L10.dsr_gate(good[:20], reg).accepted                     # too few observations
    assert not L10.dsr_gate(np.zeros(100), reg).accepted                 # zero variance
    reg.close()
    reopened = L10.TrialRegistry(str(tmp_path / "trials.sqlite"))
    assert reopened.n_trials() == 31                                     # persisted
    with pytest.raises(ValueError):
        reopened.record("", 0.1, 100)


# 10.4
def test_precision_recall_f1_by_hand():
    y_true = [1, 1, 1, 0, 0, 0, 0, 1]
    y_pred = [1, 1, 0, 1, 0, 0, 0, 0]                                    # TP=2 FP=1 FN=2
    p, r, f1 = L10.precision_recall_f1(y_true, y_pred)
    assert (p, r) == pytest.approx((2 / 3, 0.5)) and f1 == pytest.approx(2 * (2 / 3) * 0.5 / (2 / 3 + 0.5))
    assert L10.precision_recall_f1([0, 0], [0, 0]) == (0.0, 0.0, 0.0)


def test_triple_barrier_labels():
    prices = [100, 101, 103, 102, 99, 96, 97, 97.5, 98, 98.2]
    labels, t1 = L10.triple_barrier_labels(prices, profit_take=0.025, stop_loss=0.03, max_holding=4,
                                           event_idx=[0, 2, 5])
    assert labels.tolist() == [1, -1, 0] and t1.tolist() == [2, 4, 9]    # +3% at bar 2; -3.9% at bar 4; timeout
    short, _ = L10.triple_barrier_labels(prices, 0.025, 0.03, 4, side=[-1], event_idx=[2])
    assert short.tolist() == [1]                                         # the same fall is a win for a short
    with pytest.raises(ValueError):
        L10.triple_barrier_labels(prices, 0.0, 0.03, 4)


def test_bet_size_transform():
    assert L10.bet_size_from_probability(0.5) == 0.0
    assert L10.bet_size_from_probability(0.4) == 0.0
    assert L10.bet_size_from_probability(float("nan")) == 0.0
    sizes = L10.bet_size_from_probability(np.array([0.55, 0.7, 0.9, 0.999]))
    assert np.all(np.diff(sizes) > 0) and 0 < sizes[0] < 0.1 and sizes[-1] > 0.99
    assert L10.bet_size_from_probability(0.6, threshold=0.65) == 0.0


def test_meta_labelling_improves_precision_when_the_primary_is_conditionally_right():
    rng = np.random.default_rng(2)
    n = 1500
    regime = rng.standard_normal(n)                 # the primary signal only works when regime > 0
    noise = rng.standard_normal(n)
    side = rng.choice([-1.0, 1.0], n)
    p_right = np.where(regime > 0, 0.80, 0.35)
    outcome = (rng.random(n) < p_right).astype(int)
    res = L10.meta_label_fit(np.column_stack([regime, noise]), side, outcome, n_splits=5, label_horizon=3, embargo=2)
    assert res.precision_primary == pytest.approx(outcome.mean())
    assert res.precision_meta > res.precision_primary + 0.15
    assert res.f1_meta > 0.6
    assert res.bet_size[regime < -0.5].mean() < 0.1 < res.bet_size[regime > 0.5].mean()
    assert res.model.predict_proba(np.array([[1.5, 0.0, 1.0]]))[0, 1] > 0.6
    with pytest.raises(ValueError):
        L10.meta_label_fit(regime[:20], side[:20], outcome[:20])


# 10.5
def two_regime_series(rng, n=1600):
    state = np.zeros(n, dtype=int)
    for t in range(1, n):
        state[t] = state[t - 1] if rng.random() < 0.98 else 1 - state[t - 1]
    ret = np.where(state == 0, 0.0005 + 0.005 * rng.standard_normal(n), -0.0005 + 0.025 * rng.standard_normal(n))
    return state, ret


def test_hmm_recovers_two_volatility_regimes():
    state, ret = two_regime_series(np.random.default_rng(3))
    regime = L10.fit_hmm(ret, n_states=2)
    assert regime.names == ("low_vol", "high_vol")
    probs = L10.filtered_probabilities(regime, ret)
    assert probs.shape == (len(ret), 2) and np.allclose(probs.sum(axis=1), 1.0)
    assert (probs.argmax(axis=1) == state).mean() > 0.9
    four = L10.fit_hmm(np.column_stack([ret, np.abs(ret)]), n_states=4)
    assert four.names == ("low_vol_bull", "mean_reverting_chop", "high_vol_meltup", "liquidity_crash")
    assert sorted(four.order.tolist()) == [0, 1, 2, 3]
    with pytest.raises(ValueError):
        L10.fit_hmm(ret[:10], n_states=2)


def test_regime_inference_is_strictly_causal():
    state, ret = two_regime_series(np.random.default_rng(4), n=1200)
    full = L10.causal_regime_probabilities(ret, n_states=2, min_train=400, refit_every=200)
    assert np.isnan(full[:399]).all() and np.isfinite(full[399:]).all()
    for cut in (450, 700, 999):                                          # truncating the future changes nothing
        part = L10.causal_regime_probabilities(ret[: cut + 1], n_states=2, min_train=400, refit_every=200)
        assert np.allclose(part[399:], full[399: cut + 1], atol=1e-10)
    tampered = ret.copy()
    tampered[800:] *= 10.0
    assert np.allclose(L10.causal_regime_probabilities(tampered, 2, 400, 200)[399:800], full[399:800], atol=1e-10)
    # Walk-forward filtering lags each regime switch by a few bars, so it scores below a full-sample fit.
    assert (full[399:].argmax(axis=1) == state[399:]).mean() > 0.75
    regime = L10.fit_hmm(ret, 2)
    filt = L10.filtered_probabilities(regime, ret)
    assert np.allclose(L10.filtered_probabilities(regime, ret[:600]), filt[:600], atol=1e-12)


def test_regime_capital_gating():
    table = {"trend": [1.0, 0.2], "market_making": [0.6, 0.0]}
    out = L10.regime_capital_multipliers([0.75, 0.25], table)
    assert out == pytest.approx({"trend": 0.8, "market_making": 0.45})
    assert L10.regime_capital_multipliers([float("nan"), 0.5], table) == {"trend": 0.0, "market_making": 0.0}
    assert L10.regime_capital_multipliers([0.0, 0.0], table) == {"trend": 0.0, "market_making": 0.0}
    with pytest.raises(ValueError):
        L10.regime_capital_multipliers([0.5, 0.5], {"bad": [1.5, 0.0]})
