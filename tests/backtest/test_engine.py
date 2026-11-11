import numpy as np
import pandas as pd
import pytest

from valedia_trader.backtest.engine import CostModel, buy_and_hold, performance_report, run_backtest
from valedia_trader.quant_engine.level10_ml_research import CostModelError

FREE = CostModel(fees_bps=0.0, slippage_bps=0.0, borrow_annual=0.0, funding_annual=0.0)


def make_prices(n=300, seed=0, cols=("A", "B")):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame(100 * np.exp(np.cumsum(0.01 * rng.standard_normal((n, len(cols))), axis=0)), index=idx,
                        columns=list(cols))


def test_constant_weights_earn_asset_returns_after_the_lag():
    px = make_prices()
    w = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    w["A"] = 1.0
    res = run_backtest(px, w, FREE)
    r = px["A"].pct_change()
    assert res.net.iloc[:2].abs().sum() == 0.0                      # nothing held until the lag has passed
    assert np.allclose(res.net.iloc[2:], r.iloc[2:])
    assert res.weights["A"].iloc[0] == 0.0 and res.weights["A"].iloc[1] == 1.0
    assert res.turnover.iloc[1] == pytest.approx(1.0)
    assert res.equity.iloc[-1] == pytest.approx(px["A"].iloc[-1] / px["A"].iloc[1])


def test_future_decisions_cannot_change_past_results():
    px = make_prices(seed=1)
    rng = np.random.default_rng(2)
    w = pd.DataFrame(rng.uniform(-0.5, 0.5, px.shape), index=px.index, columns=px.columns)
    cost = CostModel(5.0, 2.0, 0.02, 0.0)
    base = run_backtest(px, w, cost)
    k = 150
    w2 = w.copy()
    w2.iloc[k:] = rng.uniform(-0.5, 0.5, (len(w) - k, 2))
    alt = run_backtest(px, w2, cost)
    assert np.allclose(base.net.iloc[: k + 1], alt.net.iloc[: k + 1])  # decisions at k first act at k + 1
    assert not np.allclose(base.net.iloc[k + 1:], alt.net.iloc[k + 1:])
    px2 = px.copy()
    px2.iloc[k + 1:] *= 1.5                                             # tamper with future prices only
    assert np.allclose(run_backtest(px2, w, cost).net.iloc[: k + 1], base.net.iloc[: k + 1])


def test_same_bar_execution_and_incomplete_costs_are_refused():
    px = make_prices()
    w = pd.DataFrame(0.5, index=px.index, columns=px.columns)
    with pytest.raises(ValueError, match="look-ahead"):
        run_backtest(px, w, FREE, execution_lag=0)
    with pytest.raises(CostModelError, match="funding"):
        run_backtest(px, w, CostModel(1.0, 1.0, 0.0, None))
    with pytest.raises(CostModelError, match="fees missing"):
        run_backtest(px, w, CostModel({"A": 1.0}, 1.0, 0.0, 0.0))
    with pytest.raises(ValueError):
        run_backtest(px, w.iloc[:-1], FREE)


def test_commission_slippage_borrow_and_funding_are_charged():
    px = make_prices()
    w = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    w.loc[w.index[10]:, "A"] = 1.0
    w.loc[w.index[10]:, "B"] = -0.5
    res = run_backtest(px, w, CostModel({"A": 10.0, "B": 20.0}, 5.0, borrow_annual=0.0252, funding_annual={"A": 0.0, "B": 0.0504}))
    day = res.costs.iloc[11]                                            # the trade happens one bar later
    assert day["commission"] == pytest.approx(1.0 * 10e-4 + 0.5 * 20e-4)
    assert day["slippage"] == pytest.approx(1.5 * 5e-4)
    assert res.costs["borrow"].iloc[12] == pytest.approx(0.5 * 0.0252 / 252)
    assert res.costs["funding"].iloc[12] == pytest.approx(0.5 * 0.0504 / 252)
    assert res.costs["commission"].iloc[:11].sum() == 0.0
    assert np.allclose(res.net, res.gross - res.costs.sum(axis=1))


def test_square_root_impact_grows_faster_than_linearly_with_size():
    px = make_prices(seed=3, cols=("A",))
    dv = pd.DataFrame(5e6, index=px.index, columns=px.columns)
    w = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    w.iloc[100:] = 1.0
    small = run_backtest(px, w, FREE, dollar_volume=dv, capital=1e5).costs["impact"].iloc[101]
    big = run_backtest(px, w, FREE, dollar_volume=dv, capital=4e5).costs["impact"].iloc[101]
    assert small > 0 and big == pytest.approx(2.0 * small)              # 4x notional -> 2x impact per unit
    assert run_backtest(px, w, FREE).costs["impact"].sum() == 0.0       # no volume data, no impact estimate


def test_performance_report_and_benchmarks():
    px = make_prices(n=600, seed=4)
    bh = buy_and_hold(px[["A"]])
    assert np.allclose(bh.iloc[2:], px["A"].pct_change().iloc[2:])
    rep = performance_report(bh, n_trials=10, sharpe_variance=0.001)
    assert rep["total_return"] == pytest.approx(px["A"].iloc[-1] / px["A"].iloc[0] - 1.0)   # held from day 0
    assert rep["sharpe_t_stat"] == pytest.approx(rep["sharpe"] * np.sqrt(rep["years"]))
    assert 0.0 <= rep["deflated_sharpe"] <= 1.0 and 0 < rep["max_drawdown"] < 1
    assert buy_and_hold(px, {"A": 0.6, "B": 0.4}).iloc[5] == pytest.approx(
        0.6 * px["A"].pct_change().iloc[5] + 0.4 * px["B"].pct_change().iloc[5])
    with pytest.raises(ValueError):
        performance_report(bh.iloc[:10])
