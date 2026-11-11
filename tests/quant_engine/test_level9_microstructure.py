import math

import numpy as np
import pytest

from valedia_trader.quant_engine import level9_microstructure as L9


def test_formula_registry_complete():
    assert set(L9.FORMULAS) == {"9.1", "9.2", "9.3", "9.4", "9.5"}
    for names in L9.FORMULAS.values():
        for n in names:
            assert callable(getattr(L9, n))


# 9.1
def test_mid_and_four_bps_spread():
    assert L9.mid_price(99.98, 100.02) == pytest.approx(100.00)
    assert L9.quoted_spread(99.98, 100.02) == pytest.approx(0.04)
    assert L9.spread_bps(99.98, 100.02) == pytest.approx(4.0)
    assert L9.spread_bps([99.98, 49.99], [100.02, 50.01]) == pytest.approx([4.0, 4.0])
    assert L9.effective_spread(100.015, 100.00) == pytest.approx(0.03)
    assert L9.realized_spread(100.02, 100.01, 1) == pytest.approx(0.02)       # buyer paid 2c over the later mid
    assert L9.realized_spread(99.98, 100.01, -1) == pytest.approx(0.06)
    with pytest.raises(ValueError):
        L9.mid_price(100.02, 99.98)                                          # crossed book
    with pytest.raises(ValueError):
        L9.realized_spread(100.0, 100.0, 0)


def test_maker_taker_routing():
    patient = L9.maker_taker_decision(4.0, taker_fee_bps=5.0, maker_fee_bps=-1.0, fill_probability=0.8,
                                      adverse_selection_bps=1.0)
    assert patient.route == "maker" and patient.taker_cost_bps == pytest.approx(7.0)
    assert patient.maker_cost_bps == pytest.approx(0.8 * (-1 - 2 + 1) + 0.2 * 7.0)
    urgent = L9.maker_taker_decision(4.0, 5.0, -1.0, fill_probability=0.2, adverse_selection_bps=3.0,
                                     delay_cost_bps=12.0, urgency=1.0)
    assert urgent.route == "taker"
    assert L9.maker_taker_decision(4.0, 5.0, -1.0, 0.0).route == "taker"      # never filled -> just cross
    with pytest.raises(ValueError):
        L9.maker_taker_decision(4.0, 5.0, -1.0, 1.2)
    with pytest.raises(ValueError):
        L9.maker_taker_decision(float("nan"), 5.0, -1.0, 0.5)


# 9.2
def test_imbalance_and_micro_price_reference():
    assert L9.order_book_imbalance(70, 30) == pytest.approx(0.40)
    assert L9.micro_price(99.98, 100.02, 70, 30) == pytest.approx(100.008)
    assert L9.micro_price(99.98, 100.02, 50, 50) == pytest.approx(100.00)
    assert L9.order_book_imbalance(0, 0) == 0.0 and L9.micro_price(99.98, 100.02, 0, 0) == pytest.approx(100.0)
    assert L9.order_book_imbalance([70, 10], [30, 90]) == pytest.approx([0.4, -0.8])
    with pytest.raises(ValueError):
        L9.order_book_imbalance(-1, 5)


def test_depth_weighted_imbalance():
    bids, asks = np.array([[70.0, 10.0, 10.0], [30.0, 30.0, 30.0]]), np.array([[30.0, 90.0, 90.0], [30.0, 30.0, 30.0]])
    out = L9.depth_weighted_imbalance(bids, asks, decay=0.5)
    wb, wa = 70 + 5 + 2.5, 30 + 45 + 22.5
    assert out == pytest.approx([(wb - wa) / (wb + wa), 0.0])
    assert L9.depth_weighted_imbalance(bids[0], asks[0], decay=1.0) == pytest.approx((90 - 210) / 300)
    top_only = L9.depth_weighted_imbalance(bids[0], asks[0], decay=1e-9)
    assert top_only == pytest.approx(0.4, abs=1e-6)                           # collapses to L1 imbalance
    with pytest.raises(ValueError):
        L9.depth_weighted_imbalance(bids, asks, decay=0.0)


# 9.3
def test_square_root_impact_reference_and_inverse():
    assert L9.sqrt_impact(1_000, 100_000, 0.02) == pytest.approx(0.002)       # 1% of ADV, 2% vol -> 0.2%
    assert L9.impact_cost(1_000, 50.0, 100_000, 0.02) == pytest.approx(0.002 * 50 * 1_000)
    q = L9.max_order_for_impact(20.0, 100_000, 0.02)
    assert q == pytest.approx(1_000)
    assert L9.sqrt_impact(q, 100_000, 0.02) * 1e4 == pytest.approx(20.0)
    assert L9.sqrt_impact(4_000, 100_000, 0.02) == pytest.approx(2 * 0.002)   # 4x size -> 2x impact
    with pytest.raises(ValueError):
        L9.sqrt_impact(100, 0, 0.02)
    with pytest.raises(ValueError):
        L9.max_order_for_impact(10.0, 100_000, 0.0)


# 9.4
def test_vwap_twap_and_shortfall():
    assert L9.vwap([10.0, 11.0, 12.0], [100, 200, 100]) == pytest.approx(11.0)
    assert L9.twap_schedule(900, 3).tolist() == [300.0, 300.0, 300.0]
    assert L9.vwap_schedule(1000, [1, 3, 1]).tolist() == pytest.approx([200.0, 600.0, 200.0])
    cost, bps = L9.implementation_shortfall(1, 100.0, [100.05, 100.10], [500, 500], fees=10.0)
    assert cost == pytest.approx(0.05 * 500 + 0.10 * 500 + 10) and bps == pytest.approx(cost / 100_000 * 1e4)
    sell_cost, _ = L9.implementation_shortfall(-1, 100.0, [99.9], [1000])
    assert sell_cost == pytest.approx(100.0)
    with pytest.raises(ValueError):
        L9.vwap([10.0], [0.0])
    with pytest.raises(ValueError):
        L9.vwap_schedule(100, [0, 0])


def test_almgren_chriss_trajectory_properties():
    X, n, T, sigma, eta = 100_000.0, 10, 1.0, 0.95, 2.5e-6
    twap = L9.almgren_chriss(X, n, T, sigma, eta, risk_aversion=0.0)
    assert twap.trades == pytest.approx(np.full(n, X / n))
    near = L9.almgren_chriss(X, n, T, sigma, eta, risk_aversion=1e-12)
    assert near.trades == pytest.approx(twap.trades, rel=1e-4)
    mild, urgent = (L9.almgren_chriss(X, n, T, sigma, eta, lam) for lam in (2e-6, 2e-5))
    for sched in (mild, urgent):
        assert sched.trades.sum() == pytest.approx(X)
        assert sched.holdings[0] == X and sched.holdings[-1] == 0.0
        assert np.all(np.diff(sched.trades) < 0)                               # front-loaded, decreasing
    assert urgent.trades[0] > mild.trades[0] > twap.trades[0]
    assert urgent.variance < mild.variance < twap.variance                     # risk falls ...
    assert urgent.expected_cost > mild.expected_cost > twap.expected_cost      # ... as cost rises
    tau = T / n
    assert math.cosh(mild.kappa * tau) == pytest.approx(1 + 2e-6 * sigma**2 * tau**2 / (2 * eta))
    with pytest.raises(ValueError):
        L9.almgren_chriss(X, n, T, sigma, eta=0.0, risk_aversion=1e-6)


# 9.5
def test_reservation_price_and_spread_formulas():
    assert L9.as_reservation_price(100.0, 0.0, 0.1, 2.0, 1.0) == 100.0
    assert L9.as_reservation_price(100.0, 5.0, 0.1, 2.0, 0.5) == pytest.approx(100 - 5 * 0.1 * 4 * 0.5)
    assert L9.as_reservation_price(100.0, -5.0, 0.1, 2.0, 0.5) == pytest.approx(101.0)
    assert L9.as_optimal_spread(0.1, 2.0, 0.5, 1.5) == pytest.approx(0.1 * 4 * 0.5 + 20 * math.log(1 + 0.1 / 1.5))
    with pytest.raises(ValueError):
        L9.as_optimal_spread(0.0, 2.0, 0.5, 1.5)


def test_quotes_skew_ticks_floor_and_inventory_limits():
    flat = L9.as_quotes(100.0, 0.0, 0.1, 2.0, 0.5, 1.5)
    long_ = L9.as_quotes(100.0, 5.0, 0.1, 2.0, 0.5, 1.5)
    assert flat.bid < 100.0 < flat.ask and flat.reservation == 100.0
    assert long_.bid < flat.bid and long_.ask < flat.ask                       # long inventory shifts both down
    assert long_.spread == pytest.approx(flat.spread)
    ticked = L9.as_quotes(100.0, 0.0, 0.1, 2.0, 0.5, 1.5, tick=0.05)
    assert ticked.bid <= flat.bid and ticked.ask >= flat.ask
    assert round(ticked.bid / 0.05) * 0.05 == pytest.approx(ticked.bid)
    wide = L9.as_quotes(100.0, 0.0, 0.1, 2.0, 0.5, 1.5, min_spread=5.0)
    assert wide.spread == pytest.approx(5.0)
    assert L9.as_quotes(100.0, 10.0, 0.1, 2.0, 0.5, 1.5, max_inventory=10).bid is None
    assert L9.as_quotes(100.0, -10.0, 0.1, 2.0, 0.5, 1.5, max_inventory=10).ask is None
    assert L9.as_quotes(100.0, 9.0, 0.1, 2.0, 0.5, 1.5, max_inventory=10).bid is not None
    with pytest.raises(ValueError):
        L9.as_quotes(float("nan"), 0.0, 0.1, 2.0, 0.5, 1.5)


def test_inventory_aware_quoting_controls_inventory_and_pnl_variance():
    kw = dict(n_paths=4000, n_steps=200, T=1.0, sigma=2.0, gamma=0.1, kappa=1.5, arrival_rate=140.0)
    aware = L9.simulate_market_maker(rng=np.random.default_rng(0), inventory_aware=True, **kw)
    naive = L9.simulate_market_maker(rng=np.random.default_rng(0), inventory_aware=False, **kw)
    assert aware.pnl.mean() > 0 and naive.pnl.mean() > 0                       # both earn the spread
    assert aware.pnl.std() < 0.75 * naive.pnl.std()
    assert np.abs(aware.final_inventory).mean() < 0.6 * np.abs(naive.final_inventory).mean()
    capped = L9.simulate_market_maker(rng=np.random.default_rng(1), max_inventory=3, **kw)
    assert capped.max_abs_inventory.max() <= 3
