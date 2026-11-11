import math
import warnings

import numpy as np
import pytest
import QuantLib as ql

from valedia_trader.quant_engine import level7_derivatives as L7

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from py_vollib.black_scholes_merton import black_scholes_merton as pv_bsm
    from py_vollib.black_scholes_merton.greeks import analytical as pv_greeks

S0, K0, R0, V0, T0 = 100.0, 100.0, 0.05, 0.20, 1.0


def ql_european(S, K, T, r, sigma, q, option):
    today, maturity, process = L7._ql_setup(S, T, r, sigma, q)
    opt = ql.VanillaOption(ql.PlainVanillaPayoff(L7._ql_type(option), K), ql.EuropeanExercise(maturity))
    opt.setPricingEngine(ql.AnalyticEuropeanEngine(process))
    return L7._ql_npv(opt, today)


def test_formula_registry_complete():
    assert set(L7.FORMULAS) == {"7.1", "7.2", "7.3", "7.4", "7.5"}
    for names in L7.FORMULAS.values():
        for n in names:
            assert callable(getattr(L7, n))


# 7.1
def test_reference_call_and_put():
    call = L7.bsm_price(S0, K0, T0, R0, V0)
    put = L7.bsm_price(S0, K0, T0, R0, V0, option="put")
    assert call == pytest.approx(10.4506, abs=1e-4)
    assert put == pytest.approx(5.5735, abs=1e-4)
    assert isinstance(call, float)


def test_native_prices_match_py_vollib_and_quantlib_on_a_grid():
    for flag in ("call", "put"):
        for K in (80.0, 100.0, 125.0):
            for days in (30, 180, 365, 730):
                for sigma in (0.1, 0.35):
                    for q in (0.0, 0.03):
                        T = days / 365
                        ours = L7.bsm_price(S0, K, T, R0, sigma, q, flag)
                        assert ours == pytest.approx(pv_bsm(flag[0], S0, K, T, R0, sigma, q), abs=1e-6)
                        assert ours == pytest.approx(ql_european(S0, K, T, R0, sigma, q, flag), abs=1e-6)


def test_vectorized_pricing_and_degenerate_cases():
    strikes = np.array([90.0, 100.0, 110.0])
    out = L7.bsm_price(S0, strikes, T0, R0, V0, 0.0, np.array(["call", "put", "call"]))
    assert out.shape == (3,)
    assert out[1] == pytest.approx(5.5735, abs=1e-4)
    assert L7.bsm_price(110, 100, 0.0, R0, V0) == pytest.approx(10.0)
    assert L7.bsm_price(90, 100, 0.0, R0, V0) == 0.0
    assert L7.bsm_price(100, 90, 1.0, 0.05, 0.0) == pytest.approx(100 - 90 * math.exp(-0.05))
    with pytest.raises(ValueError):
        L7.bsm_price(-1, 100, 1, 0.05, 0.2)
    with pytest.raises(ValueError):
        L7.bsm_price(100, 100, 1, 0.05, 0.2, option="straddle")


def test_american_put_is_worth_at_least_european_and_american_call_equals_european_without_dividends():
    eu_put = L7.bsm_price(S0, 110.0, T0, R0, V0, option="put")
    am_put = L7.american_option_price(S0, 110.0, T0, R0, V0, option="put")
    assert am_put > eu_put + 0.1
    am_call = L7.american_option_price(S0, K0, T0, R0, V0, option="call")
    assert am_call == pytest.approx(L7.bsm_price(S0, K0, T0, R0, V0), abs=0.01)


def test_barrier_in_out_parity_and_knocked_states():
    args = (S0, K0, 85.0, T0, R0, V0)
    out_, in_ = L7.barrier_option_price(*args, barrier_type="down_out"), L7.barrier_option_price(
        *args, barrier_type="down_in")
    assert out_ + in_ == pytest.approx(L7.bsm_price(S0, K0, T0, R0, V0), abs=1e-6)
    assert 0 < out_ < L7.bsm_price(S0, K0, T0, R0, V0)
    assert L7.barrier_option_price(80.0, K0, 85.0, T0, R0, V0, barrier_type="down_out", rebate=1.5) == 1.5
    assert L7.barrier_option_price(80.0, K0, 85.0, T0, R0, V0, barrier_type="down_in") == pytest.approx(
        L7.bsm_price(80.0, K0, T0, R0, V0))
    with pytest.raises(ValueError):
        L7.barrier_option_price(*args, barrier_type="sideways")


def test_native_digital_matches_quantlib_and_sums_to_discount_factor():
    for flag in ("call", "put"):
        for K in (90.0, 100.0, 115.0):
            assert L7.digital_price(S0, K, T0, R0, V0, 0.01, flag) == pytest.approx(
                L7.digital_price_quantlib(S0, K, T0, R0, V0, 0.01, flag), abs=1e-8)
    both = L7.digital_price(S0, 105.0, T0, R0, V0) + L7.digital_price(S0, 105.0, T0, R0, V0, option="put")
    assert both == pytest.approx(math.exp(-R0 * T0))
    assert L7.digital_price(101, 100, 0.0, R0, V0) == 1.0 and L7.digital_price(99, 100, 0.0, R0, V0) == 0.0


# 7.2
def test_put_call_parity_and_synthetic_forward():
    c, p = L7.bsm_price(S0, K0, T0, R0, V0, 0.02), L7.bsm_price(S0, K0, T0, R0, V0, 0.02, "put")
    assert L7.put_call_parity_residual(c, p, S0, K0, T0, R0, 0.02) == pytest.approx(0.0, abs=1e-12)
    assert 10.4506 - 100 + 100 * math.exp(-0.05) == pytest.approx(5.5735, abs=1e-4)
    assert L7.synthetic_forward(c, p, K0, T0, R0) == pytest.approx(S0 * math.exp((R0 - 0.02) * T0))


def test_parity_scanner_flags_only_real_arbitrage():
    c, p = L7.bsm_price(S0, K0, T0, R0, V0), L7.bsm_price(S0, K0, T0, R0, V0, option="put")
    fair = L7.parity_arbitrage_scan(c - 0.05, c + 0.05, p - 0.05, p + 0.05, S0 - 0.01, S0 + 0.01, K0, T0, R0)
    assert fair["best"] == "none" and fair["profit"] == 0.0
    rich_call = L7.parity_arbitrage_scan(c + 0.95, c + 1.05, p - 0.05, p + 0.05, S0 - 0.01, S0 + 0.01, K0, T0, R0,
                                         fee_per_leg=0.01)
    assert rich_call["best"] == "conversion"
    assert rich_call["profit"] == pytest.approx(0.95 - 0.05 - 0.01 - 0.03, abs=1e-9)
    rich_put = L7.parity_arbitrage_scan(c - 0.05, c + 0.05, p + 0.95, p + 1.05, S0 - 0.01, S0 + 0.01, K0, T0, R0)
    assert rich_put["best"] == "reversal" and rich_put["profit"] > 0.8
    with pytest.raises(ValueError):
        L7.parity_arbitrage_scan(c + 1, c, p, p, S0, S0, K0, T0, R0)


def test_box_spread_recovers_the_rate():
    c1, p1 = L7.bsm_price(S0, 95, T0, R0, V0), L7.bsm_price(S0, 95, T0, R0, V0, option="put")
    c2, p2 = L7.bsm_price(S0, 105, T0, R0, V0), L7.bsm_price(S0, 105, T0, R0, V0, option="put")
    assert L7.box_spread_implied_rate(c1, p1, c2, p2, 95, 105, T0) == pytest.approx(R0, abs=1e-10)


# 7.3
def test_reference_greeks():
    g = L7.bsm_greeks(S0, K0, T0, R0, V0)
    assert float(g["delta"]) == pytest.approx(0.6368, abs=1e-4)
    assert float(g["gamma"]) == pytest.approx(0.018762, abs=1e-6)


def test_analytic_greeks_match_bump_and_reprice_and_py_vollib():
    for flag in ("call", "put"):
        for K in (85.0, 100.0, 120.0):
            for T in (0.1, 1.0, 2.0):
                a = L7.bsm_greeks(S0, K, T, R0, 0.25, 0.02, flag)
                n = L7.numerical_greeks(S0, K, T, R0, 0.25, 0.02, flag)
                for name in a:
                    assert float(a[name]) == pytest.approx(float(n[name]), rel=2e-4, abs=2e-5), (name, flag, K, T)
                f = flag[0]
                assert float(a["delta"]) == pytest.approx(pv_greeks.delta(f, S0, K, T, R0, 0.25, 0.02), abs=1e-9)
                assert float(a["gamma"]) == pytest.approx(pv_greeks.gamma(f, S0, K, T, R0, 0.25, 0.02), abs=1e-9)
                assert float(a["vega"]) * 0.01 == pytest.approx(pv_greeks.vega(f, S0, K, T, R0, 0.25, 0.02), abs=1e-9)
                assert float(a["theta"]) / 365 == pytest.approx(pv_greeks.theta(f, S0, K, T, R0, 0.25, 0.02), abs=1e-9)
                assert float(a["rho"]) * 0.01 == pytest.approx(pv_greeks.rho(f, S0, K, T, R0, 0.25, 0.02), abs=1e-9)


def test_gamma_peaks_at_the_money_near_expiry():
    spots = np.array([90.0, 100.0, 110.0])
    near, far = L7.bsm_greeks(spots, 100, 5 / 365, 0.0, 0.2)["gamma"], L7.bsm_greeks(spots, 100, 1.0, 0.0, 0.2)["gamma"]
    assert near[1] > near[0] and near[1] > near[2]
    assert near[1] > 5 * far[1]


def test_hedge_solvers_neutralize_the_book():
    book = L7.bsm_greeks(S0, 100, 0.5, R0, 0.2)
    hedge = L7.bsm_greeks(S0, 105, 0.25, R0, 0.2)
    bd, bg = 10 * float(book["delta"]), 10 * float(book["gamma"])
    n_opt, n_und = L7.delta_gamma_neutral_hedge(bd, bg, float(hedge["delta"]), float(hedge["gamma"]))
    assert bg + n_opt * float(hedge["gamma"]) == pytest.approx(0.0, abs=1e-12)
    assert bd + n_opt * float(hedge["delta"]) + n_und == pytest.approx(0.0, abs=1e-12)
    assert L7.delta_neutral_hedge(bd) == -bd
    with pytest.raises(ValueError):
        L7.delta_gamma_neutral_hedge(1.0, 1.0, 0.5, 0.0)
    with pytest.raises(ValueError):
        L7.bsm_greeks(S0, K0, 0.0, R0, V0)


# 7.4
def test_implied_vol_round_trips_over_a_grid():
    K, T, sig = np.meshgrid(np.array([70.0, 90.0, 100.0, 110.0, 140.0]), np.array([0.05, 0.5, 2.0]),
                            np.array([0.08, 0.2, 0.6, 1.2]), indexing="ij")
    for flag in ("call", "put"):
        prices = L7.bsm_price(S0, K, T, R0, sig, 0.01, flag)
        w = 1.0 if flag == "call" else -1.0
        intrinsic = np.maximum(w * (S0 * np.exp(-0.01 * T) - K * np.exp(-R0 * T)), 0.0)
        liquid = prices - intrinsic > 1e-4           # without time value the price carries no vol information
        iv, valid = L7.implied_volatility(prices, S0, K, T, R0, 0.01, flag)
        assert valid[liquid].all()
        assert iv[liquid] == pytest.approx(sig[liquid], abs=1e-6)


def test_implied_vol_rejects_prices_outside_arbitrage_bounds():
    iv, valid = L7.implied_volatility(np.array([150.0, 0.0, 4.0, 10.4506]), S0, K0, T0, R0)
    assert valid.tolist() == [False, False, False, True]      # 4.0 is below intrinsic 100 - 100e^-0.05
    assert np.isnan(iv[:3]).all() and iv[3] == pytest.approx(0.20, abs=1e-5)


def test_svi_calibration_recovers_known_parameters():
    true = (0.02, 0.4, -0.5, 0.05, 0.15)
    k = np.linspace(-0.6, 0.6, 41)
    fit = L7.calibrate_svi(k, L7.svi_total_variance(k, *true))
    assert (fit.a, fit.b, fit.rho, fit.m, fit.sigma) == pytest.approx(true, abs=1e-4)
    assert fit.rmse < 1e-7 and fit.min_variance_ok
    assert np.all(L7.svi_butterfly_density(k, fit) > 0)
    # Axel Vogt's well-known SVI slice: positive variance everywhere yet butterfly arbitrage in the wing
    arb = L7.SVIParams(-0.0410, 0.1331, 0.3060, 0.3586, 0.4153, 0.0, True)
    assert np.any(L7.svi_butterfly_density(np.linspace(-1.5, 1.5, 301), arb) < 0)
    with pytest.raises(ValueError):
        L7.calibrate_svi([0.0, 0.1], [0.04, 0.05])


def test_smile_term_structure_and_vol_spread_signal():
    k = np.linspace(-0.3, 0.3, 13)
    summary = L7.smile_summary(k, 0.22 - 0.15 * k + 0.4 * k**2)
    assert (summary.atm_iv, summary.skew, summary.curvature) == pytest.approx((0.22, -0.15, 0.8))
    assert L7.term_structure_summary([0.1, 0.5, 1.0], [0.30, 0.26, 0.22])[1] == "backwardation"
    assert L7.term_structure_summary([0.1, 0.5, 1.0], [0.18, 0.20, 0.23])[1] == "contango"
    sig = L7.iv_rv_signal([0.20, 0.20, 0.20, float("nan")], [0.26, 0.15, 0.21, 0.3])
    assert sig.tolist() == [1.0, -1.0, 0.0, 0.0]


# 7.5
def test_gamma_scalping_formula_matches_discrete_hedging_simulation():
    rng = np.random.default_rng(0)
    T, n_steps, n_paths, s_real, s_imp = 0.25, 63, 4000, 0.30, 0.20
    dt = T / n_steps
    z = rng.standard_normal((n_paths, n_steps))
    paths = np.empty((n_paths, n_steps + 1))
    paths[:, 0] = 100.0
    paths[:, 1:] = 100.0 * np.exp(np.cumsum(-0.5 * s_real**2 * dt + s_real * math.sqrt(dt) * z, axis=1))
    sim = np.array([L7.simulate_delta_hedged_pnl(p, 100.0, T, 0.0, s_imp) for p in paths])
    taus = T - dt * np.arange(n_steps)
    gam = L7.bsm_greeks(paths[:, :-1], 100.0, taus, 0.0, s_imp)["gamma"]
    formula = (L7.gamma_scalping_pnl_rate(gam, paths[:, :-1], s_real, s_imp) * dt).sum(axis=1)
    assert sim.mean() > 0                                           # long gamma earns when realized > implied
    assert sim.mean() == pytest.approx(formula.mean(), rel=0.05)
    value_gap = L7.bsm_price(100, 100, T, 0.0, s_real) - L7.bsm_price(100, 100, T, 0.0, s_imp)
    assert sim.mean() == pytest.approx(value_gap, rel=0.10)
    short = np.array([L7.simulate_delta_hedged_pnl(p, 100.0, T, 0.0, 0.40) for p in paths[:1500]])
    assert short.mean() < 0                                         # and loses when implied was too high
    assert L7.gamma_scalping_pnl_rate(0.02, 100.0, 0.3, 0.2) == pytest.approx(0.5 * 0.02 * 1e4 * 0.05)


def test_gamma_scalp_decision_accounts_for_costs():
    assert L7.gamma_scalp_decision(0.30, 0.20) == "long_gamma"
    assert L7.gamma_scalp_decision(0.15, 0.20) == "short_gamma"
    assert L7.gamma_scalp_decision(0.21, 0.20, cost_vol_points=0.02) == "flat"
    assert L7.gamma_scalp_decision(float("nan"), 0.20) == "flat"
    with pytest.raises(ValueError):
        L7.simulate_delta_hedged_pnl([100.0, 101.0], 100.0, 0.25, 0.0, 0.2)
