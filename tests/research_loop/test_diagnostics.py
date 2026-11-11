"""The sheet's diagnostics: computed on synthetic series only, so no tradable rule ever meets real prices here."""
import json
import math

import numpy as np
import pandas as pd
import pytest

from valedia_trader.research_loop import diagnostics as D


def _world(n=900, seed=3, edge=0.0006):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=n)
    market = pd.Series(rng.normal(0.0004, 0.01, n), index=idx)
    assets = pd.DataFrame({"SPY": market, "TLT": rng.normal(0.0001, 0.006, n), "GLD": rng.normal(0.0002, 0.008, n),
                           "BTC-USD": 1.5 * market + rng.normal(0.0, 0.03, n)}, index=idx)
    weights = pd.DataFrame({"SPY": 0.5, "TLT": 0.3, "GLD": 0.2, "BTC-USD": 0.0}, index=idx)
    strategy = (weights.shift(1).fillna(0.0) * assets).sum(axis=1) + edge
    return strategy, market, assets, weights


def test_every_section_computes_and_is_json_safe():
    r, market, assets, weights = _world()
    out = D.diagnose(r, benchmark=market, asset_returns=assets, weights=weights, variation_pvalues=[0.01, 0.2, 0.5])
    missing = [k for k in D.SECTIONS if "unavailable" in out[k]]
    assert missing == []
    assert set(D.SECTIONS) <= set(out) and out["observations"] == len(r)
    text = json.dumps(out)                                  # no NaN, no numpy scalars
    assert "NaN" not in text and "Infinity" not in text
    assert all("formula" in out[k] and "reading" in out[k] for k in D.SECTIONS)


def test_market_exposure_recovers_a_known_beta_and_alpha():
    rng = np.random.default_rng(5); n = 2000
    idx = pd.bdate_range("2015-01-01", periods=n)
    market = pd.Series(rng.normal(0.0003, 0.01, n), index=idx)
    r = 0.7 * market + 0.0004 + pd.Series(rng.normal(0.0, 0.002, n), index=idx)
    sec = D.diagnose(r, benchmark=market)["market_exposure"]
    assert sec["beta"] == pytest.approx(0.7, abs=0.02)
    assert sec["alpha_annual"] == pytest.approx(0.0004 * 252, rel=0.25) and sec["alpha_t_stat"] > 4
    pure_beta = D.diagnose(0.7 * market + pd.Series(rng.normal(0.0, 0.002, n), index=idx), benchmark=market)["market_exposure"]
    assert abs(pure_beta["alpha_t_stat"]) < 2.5             # no alpha was put in


def test_uncertainty_and_fair_game_separate_an_edge_from_noise():
    rng = np.random.default_rng(11); idx = pd.bdate_range("2018-01-01", periods=1500)
    noise = pd.Series(rng.normal(0.0, 0.01, 1500), index=idx)
    edge = noise + 0.0012
    a, b = D.diagnose(noise), D.diagnose(edge)
    assert a["uncertainty_of_the_mean"]["ci95_daily"][0] < 0 < a["uncertainty_of_the_mean"]["ci95_daily"][1]
    assert b["uncertainty_of_the_mean"]["ci95_daily"][0] > 0
    assert a["fair_game_test"]["looks_like_a_fair_game"] is True and b["fair_game_test"]["looks_like_a_fair_game"] is False
    assert b["win_rate"]["posterior_mean"] > a["win_rate"]["posterior_mean"]


def test_multiple_testing_and_ruin_move_the_right_way():
    r, market, assets, weights = _world()
    few = D.diagnose(r, variation_pvalues=[0.03])["multiple_testing"]
    many = D.diagnose(r, variation_pvalues=[0.03] + [0.5] * 19)["multiple_testing"]
    assert few["best_p_holm"] == pytest.approx(0.03) and many["best_p_holm"] == pytest.approx(0.6)
    assert many["chance_of_a_false_win_at_5pct"] > 0.6 > few["chance_of_a_false_win_at_5pct"]
    tight = D.diagnose(r, drawdown_limit=0.03, profit_target=0.20)["ruin_and_target"]
    loose = D.diagnose(r, drawdown_limit=0.30, profit_target=0.05)["ruin_and_target"]
    assert loose["p_target_before_limit"] > tight["p_target_before_limit"]
    assert tight["p_breach_limit_next_63d"] > loose["p_breach_limit_next_63d"]


def test_missing_inputs_degrade_to_reasons_not_errors():
    r, *_ = _world(n=400)
    out = D.diagnose(r)                                     # no benchmark, assets, weights or p-values
    for name in ("by_market_state", "market_exposure", "factor_exposure", "multiple_testing", "book_risk", "pairs_check", "signal_quality"):
        assert "unavailable" in out[name]
    assert "formula" in out["uncertainty_of_the_mean"]
    assert "error" in D.diagnose(r.iloc[:20])


def test_order_facts_are_complete_and_refuse_unknown_symbols():
    rng = np.random.default_rng(2); n = 600
    idx = pd.bdate_range("2023-01-01", periods=n)
    px = pd.DataFrame({"SPY": 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, n))), "AAA": 50 * np.exp(np.cumsum(rng.normal(0.0002, 0.02, n)))}, index=idx)
    facts = D.order_facts(px, "AAA")
    for key in ("momentum_20d_pct", "momentum_120d_pct", "vol_60d_annual_pct", "drawdown_from_1y_high_pct", "var_95_1d_pct",
                "expected_shortfall_95_1d_pct", "beta_to_SPY", "above_200d_average", "mean_daily_return_distinguishable_from_zero"):
        assert key in facts and facts[key] is not None
    assert facts["expected_shortfall_95_1d_pct"] >= facts["var_95_1d_pct"] > 0
    assert math.isfinite(facts["vol_60d_annual_pct"]) and "NaN" not in json.dumps(facts)
    with pytest.raises(KeyError):
        D.order_facts(px, "ZZZ")


def test_book_risk_and_volatility_use_the_optimisation_and_simulation_formulas():
    r, market, assets, weights = _world()
    out = D.diagnose(r, benchmark=market, asset_returns=assets, weights=weights, drawdown_limit=0.05)
    book = out["book_risk"]
    assert book["assets_held"] == ["SPY", "TLT", "GLD"]                      # BTC-USD was never held
    assert sum(book["minimum_variance_weights"].values()) == pytest.approx(1.0, abs=1e-6)
    assert sum(abs(v) for v in book["mean_variance_weights"].values()) == pytest.approx(1.0, abs=1e-6)
    assert 0.0 <= book["p_current_book_breaches_limit_63d_no_drift"] <= 1.0
    assert book["condition_number_shrunk"] <= book["condition_number_sample"] + 1e-9
    assert 0.80 <= out["volatility_forecast"]["ewma_decay_fit"] <= 0.995
    assert out["pairs_check"]["pair"] and "cointegrated" in out["pairs_check"]
    assert sum(book["expected_annual_return_by_asset"].values()) == pytest.approx(book["expected_annual_return_of_average_book"], abs=1e-9)
    u = out["uncertainty_of_the_mean"]
    assert u["sharpe_t_stat"] == pytest.approx(u["sharpe_annual"] * math.sqrt(u["years"]), rel=0.02)
