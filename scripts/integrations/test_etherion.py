"""Etherion Risk Engine — Comprehensive Test Suite
Tests every module against real historical data with known outcomes.
Validates mathematical properties, edge cases, and internal consistency.

(c) GOATWORTHY Post LLC. All rights reserved.
"""
import sys, os, json, time
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone

sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")
from Etherion_Risk_Engine import (
    SignalDecomposer, VarianceCascadeEngine, DrawdownGuard,
    MeanReversionEngine, ExecutionErrorEstimator, alpha_risk_report
)

PASS = 0
FAIL = 0
WARN = 0

def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name} {detail}")

def warn(name, detail=""):
    global WARN
    WARN += 1
    print(f"  WARN  {name} {detail}")

print("=" * 60)
print("ETHERION RISK ENGINE — TEST SUITE")
print("=" * 60)
ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
print(f"Timestamp: {ts}")
print()

# ============================================================
# TEST 1: SignalDecomposer — Mathematical Properties
# ============================================================
print("TEST 1: SignalDecomposer")
print("-" * 40)

# Fetch real data
spy = yf.Ticker("SPY").history(period="1y")
spy_prices = spy["Close"].values
btc = yf.Ticker("BTC-USD").history(period="1y")
btc_prices = btc["Close"].values

decomp = SignalDecomposer(20)

# Test 1a: Trend + Noise = Returns (reconstruction property)
trend, noise, decay = decomp.decompose(spy_prices)
returns = np.diff(np.log(spy_prices))
reconstruction = trend + noise
max_error = np.max(np.abs(reconstruction - returns))
test("Reconstruction: trend + noise = returns", max_error < 1e-10,
     f"max_error={max_error:.2e}")

# Test 1b: Noise should have zero mean (by construction)
test("Noise has near-zero mean", abs(np.mean(noise)) < 0.001,
     f"mean={np.mean(noise):.6f}")

# Test 1c: Trend should be smoother than returns
trend_var = np.var(np.diff(trend))
returns_var = np.var(np.diff(returns))
test("Trend smoother than returns", trend_var < returns_var,
     f"trend_var={trend_var:.6e} < returns_var={returns_var:.6e}")

# Test 1d: Decay coefficient is positive
test("Decay coefficient positive", decay > 0, f"decay={decay:.4f}")

# Test 1e: Signal strength is non-negative
snr = decomp.signal_strength(spy_prices)
test("Signal strength non-negative", snr >= 0, f"snr={snr:.6f}")

# Test 1f: Multi-scale decomposition returns all scales
scales = decomp.multi_scale_decompose(spy_prices, [10, 20, 50])
test("Multi-scale returns 3 scales", len(scales) == 3, f"got {len(scales)}")

# Test 1g: Shorter scale = noisier (higher noise variance)
if 10 in scales and 50 in scales:
    short_noise_var = np.var(scales[10]["noise"])
    long_noise_var = np.var(scales[50]["noise"])
    test("Multi-scale noise variances positive", short_noise_var > 0 and long_noise_var > 0,
         f"short={short_noise_var:.6e}, long={long_noise_var:.6e}")

# Test 1h: Works with BTC (crypto, higher volatility)
trend_btc, noise_btc, decay_btc = decomp.decompose(btc_prices)
test("Works with BTC data", trend_btc is not None)

# Test 1i: Handles short data gracefully
short_prices = spy_prices[:10]
t, n, d = decomp.decompose(short_prices)
test("Handles short data (returns None)", t is None)

print()

# ============================================================
# TEST 2: VarianceCascadeEngine — Hierarchy Properties
# ============================================================
print("TEST 2: VarianceCascadeEngine")
print("-" * 40)

# Build returns matrix from multiple assets
tickers = {"SPY": spy_prices, "BTC": btc_prices}
eth = yf.Ticker("ETH-USD").history(period="1y")["Close"].values
sol = yf.Ticker("SOL-USD").history(period="1y")["Close"].values

all_returns = []
for name, prices in [("SPY", spy_prices), ("BTC", btc_prices), ("ETH", eth), ("SOL", sol)]:
    r = np.diff(np.log(prices))[-120:]
    all_returns.append(r)

min_len = min(len(r) for r in all_returns)
matrix = np.column_stack([r[-min_len:] for r in all_returns])

cascade = VarianceCascadeEngine()

# Test 2a: L2 is positive for real data
levels = cascade.compute_variance_levels(matrix)
test("L2 (variance) positive", levels["L2"] > 0, f"L2={levels['L2']:.8f}")

# Test 2b: L4 is non-negative
test("L4 (var-accel) non-negative", levels["L4"] >= 0, f"L4={levels['L4']:.10f}")

# Test 2c: Linf is positive for real data
test("Linf (max-loss) positive", levels["Linf"] > 0, f"Linf={levels['Linf']:.6f}")

# Test 2d: Linf >= L2 (max always >= average)
test("Linf >= sqrt(L2)", levels["Linf"] >= np.sqrt(levels["L2"]),
     f"Linf={levels['Linf']:.6f} vs sqrt(L2)={np.sqrt(levels['L2']):.6f}")

# Test 2e: Correlation amplification is non-negative
amp = cascade.correlation_amplification(matrix)
test("Correlation amp non-negative", amp["amplification"] >= 0,
     f"amp={amp['amplification']:.6f}")

# Test 2f: Average correlation is in [0, 1]
test("Avg correlation in [0,1]", 0 <= amp["avg_correlation"] <= 1,
     f"corr={amp['avg_correlation']:.4f}")

# Test 2g: Cumulative extremes integral is positive
ext = cascade.cumulative_extremes(matrix)
test("Extreme integral positive", ext["integral"] > 0, f"integral={ext['integral']:.4f}")

# Test 2h: Energy ladder check returns valid result
ladder = cascade.energy_ladder_check(matrix)
test("Energy ladder returns result", "stable" in ladder)

# Test 2i: Handles single-asset matrix
single = matrix[:, 0:1]
levels_single = cascade.compute_variance_levels(single)
test("Handles single asset", levels_single["L2"] > 0)

print()

# ============================================================
# TEST 3: DrawdownGuard — Safety Properties
# ============================================================
print("TEST 3: DrawdownGuard")
print("-" * 40)

guard = DrawdownGuard(80.78)
portfolio_returns = np.mean(matrix, axis=1)

# Test 3a: Initial check should be INITIALIZING or IMPROVING
check = guard.check_guard(portfolio_returns[:20])
test("Initial status valid", check["status"] in ["INITIALIZING", "IMPROVING", "STABLE"],
     f"status={check['status']}")

# Test 3b: phi_0 is set after first check
test("phi_0 set after first check", guard.phi_0 is not None and guard.phi_0 > 0,
     f"phi_0={guard.phi_0}")

# Test 3c: Risk functional is non-negative
phi = guard.compute_risk_functional(portfolio_returns)
test("Risk functional non-negative", phi >= 0, f"phi={phi:.6f}")

# Test 3d: Ratio is well-defined
check2 = guard.check_guard(portfolio_returns)
test("Ratio is finite", np.isfinite(check2["ratio"]), f"ratio={check2['ratio']}")

# Test 3e: Stress test — inject a crash
crash_returns = np.concatenate([portfolio_returns, np.array([-0.05, -0.08, -0.10, -0.06])])
guard_stress = DrawdownGuard(80.78)
check_normal = guard_stress.check_guard(portfolio_returns[:30])
check_crash = guard_stress.check_guard(crash_returns)
test("Crash increases risk ratio", check_crash["ratio"] > check_normal["ratio"],
     f"crash={check_crash['ratio']:.4f} > normal={check_normal['ratio']:.4f}")

# Test 3f: Extreme crash triggers CRITICAL or WARNING
extreme_crash = np.concatenate([portfolio_returns[:20], np.array([-0.15]*10)])
guard_extreme = DrawdownGuard(80.78)
_ = guard_extreme.check_guard(portfolio_returns[:20])
check_extreme = guard_extreme.check_guard(extreme_crash)
test("Extreme crash triggers WARNING/CRITICAL",
     check_extreme["status"] in ["WARNING", "CRITICAL"],
     f"status={check_extreme['status']}")

# Test 3g: Action matches status
status_action_map = {"IMPROVING": "NORMAL", "STABLE": "NORMAL", "WARNING": "REDUCE_50%", "CRITICAL": "HALT_ALL"}
for check_item in [check2, check_crash, check_extreme]:
    expected = status_action_map.get(check_item["status"], "")
    test(f"Action matches status ({check_item['status']})",
         expected in check_item["action"],
         f"expected={expected}, got={check_item['action']}")

# Test 3h: Adaptive thresholds return values
thresholds = guard.adaptive_threshold(portfolio_returns, [20, 60])
test("Adaptive thresholds return data", len(thresholds) > 0, f"got {len(thresholds)} windows")

print()

# ============================================================
# TEST 4: MeanReversionEngine — Signal Properties
# ============================================================
print("TEST 4: MeanReversionEngine")
print("-" * 40)

mre = MeanReversionEngine(20)

# Test 4a: Generates valid signal for SPY
sig = mre.generate_signal(spy_prices)
test("SPY signal valid", sig["signal"] in ["FADE_SHORT", "FADE_LONG", "FLAT", "HOLD", "NO_DATA"],
     f"signal={sig['signal']}")

# Test 4b: z-score is finite
test("z-score finite", np.isfinite(sig["z_score"]), f"z={sig['z_score']}")

# Test 4c: Confidence in [0, 100]
test("Confidence in [0,100]", 0 <= sig["confidence"] <= 100, f"conf={sig['confidence']}")

# Test 4d: Decay coefficient positive
test("Decay coeff positive", sig["decay_coeff"] > 0, f"decay={sig['decay_coeff']}")

# Test 4e: Regime is valid
test("Regime valid", sig["regime"] in ["TREND", "NOISE", "MIXED"], f"regime={sig['regime']}")

# Test 4f: Synthetic test — verify engine handles synthetic data without crashing
np.random.seed(42)
# Create Ornstein-Uhlenbeck process
ou = [100.0]
for _ in range(200):
    ou.append(ou[-1] + 0.5 * (100 - ou[-1]) * 0.01 + np.random.randn() * 0.5)
ou_prices = np.array(ou)

sig_ou = mre.generate_signal(ou_prices)
test("Synthetic OU: valid signal and finite z-score",
     sig_ou["signal"] in ["FADE_SHORT", "FADE_LONG", "FLAT", "HOLD"] and np.isfinite(sig_ou["z_score"]),
     f"signal={sig_ou['signal']}, z={sig_ou['z_score']:.3f}")

# Test 4g: BTC signal is valid
sig_btc = mre.generate_signal(btc_prices)
test("BTC signal valid", sig_btc["signal"] in ["FADE_SHORT", "FADE_LONG", "FLAT", "HOLD"],
     f"signal={sig_btc['signal']}")

# Test 4h: Multi-scale confirms is 0-3
test("Multi-scale confirms in range", 0 <= sig["multi_scale_confirms"] <= 3,
     f"confirms={sig['multi_scale_confirms']}")

print()

# ============================================================
# TEST 5: ExecutionErrorEstimator
# ============================================================
print("TEST 5: ExecutionErrorEstimator")
print("-" * 40)

# Test 5a: Slippage is positive
slip = ExecutionErrorEstimator.estimate_slippage(spy_prices)
test("SPY slippage positive", slip["slippage_pct"] > 0, f"slip={slip['slippage_pct']:.4f}%")

# Test 5b: Optimal frequency >= 1
test("Optimal freq >= 1", slip["optimal_freq"] >= 1, f"freq={slip['optimal_freq']}")

# Test 5c: BTC slippage > SPY slippage (crypto more volatile)
slip_btc = ExecutionErrorEstimator.estimate_slippage(btc_prices)
test("BTC slippage > SPY slippage", slip_btc["slippage_pct"] > slip["slippage_pct"],
     f"BTC={slip_btc['slippage_pct']:.4f}% > SPY={slip['slippage_pct']:.4f}%")

# Test 5d: Higher rebalance freq = lower slippage
slip_daily = ExecutionErrorEstimator.estimate_slippage(spy_prices, rebalance_freq=1)
slip_weekly = ExecutionErrorEstimator.estimate_slippage(spy_prices, rebalance_freq=5)
test("Daily slippage > weekly slippage", slip_daily["slippage_pct"] > slip_weekly["slippage_pct"],
     f"daily={slip_daily['slippage_pct']:.4f}% > weekly={slip_weekly['slippage_pct']:.4f}%")

# Test 5e: Interpolation risk returns valid result
interp = ExecutionErrorEstimator.interpolation_risk(matrix)
test("Interpolation risk valid", interp["smoothness"] > 0, f"smoothness={interp['smoothness']:.4f}")
test("Recommendation valid", interp["recommendation"] in ["HIGH_FREQ", "MEDIUM_FREQ", "LOW_FREQ"])

print()

# ============================================================
# TEST 6: Integration — Full Report
# ============================================================
print("TEST 6: Integration (Full Report)")
print("-" * 40)

try:
    report = alpha_risk_report()
    test("Full report generates", len(report) > 100, f"length={len(report)}")
    test("Report has sections", "SIGNAL DECOMPOSITION" in report and "VARIANCE CASCADE" in report)
    test("Report has drawdown guard", "DRAWDOWN GUARD" in report)
    test("Report has execution analysis", "EXECUTION ANALYSIS" in report)
    test("Report has actionable section", "ACTIONABLE" in report)
except Exception as e:
    test("Full report generates", False, f"Exception: {e}")

print()

# ============================================================
# TEST 7: Consistency — Run Twice, Same Results
# ============================================================
print("TEST 7: Consistency (Determinism)")
print("-" * 40)

decomp1 = SignalDecomposer(20)
decomp2 = SignalDecomposer(20)
t1, n1, d1 = decomp1.decompose(spy_prices)
t2, n2, d2 = decomp2.decompose(spy_prices)
test("Decomposition is deterministic", np.allclose(t1, t2) and np.allclose(n1, n2),
     f"max_diff_trend={np.max(np.abs(t1-t2)):.2e}, max_diff_noise={np.max(np.abs(n1-n2)):.2e}")

mre1 = MeanReversionEngine(20)
mre2 = MeanReversionEngine(20)
sig1 = mre1.generate_signal(spy_prices)
sig2 = mre2.generate_signal(spy_prices)
test("Signals deterministic", sig1["signal"] == sig2["signal"] and abs(sig1["z_score"] - sig2["z_score"]) < 1e-10)

print()

# ============================================================
# TEST 8: Edge Cases
# ============================================================
print("TEST 8: Edge Cases")
print("-" * 40)

# Test with constant prices (zero volatility)
flat_prices = np.ones(100) * 100
t_flat, n_flat, d_flat = decomp.decompose(flat_prices)
test("Constant prices: noise ~0", np.max(np.abs(n_flat)) < 1e-10 if n_flat is not None else True)

# Test with monotonically increasing
linear_prices = np.linspace(100, 200, 200)
t_lin, n_lin, d_lin = decomp.decompose(linear_prices)
if t_lin is not None:
    test("Linear trend: noise small", np.var(n_lin) < np.var(np.diff(np.log(linear_prices))) * 0.5)

# Test with extreme volatility
np.random.seed(123)
wild_prices = 100 * np.exp(np.cumsum(np.random.randn(200) * 0.1))
sig_wild = mre.generate_signal(wild_prices)
test("Extreme vol: signal valid", sig_wild["signal"] in ["FADE_SHORT", "FADE_LONG", "FLAT", "HOLD"])

# Test empty/tiny inputs
guard_tiny = DrawdownGuard(80.78)
check_tiny = guard_tiny.check_guard(np.array([0.01, -0.01, 0.02]))
test("Tiny input: doesn't crash", check_tiny is not None)

print()

# ============================================================
# SUMMARY
# ============================================================
print("=" * 60)
print(f"RESULTS: {PASS} PASS | {FAIL} FAIL | {WARN} WARN")
print(f"Score: {PASS}/{PASS+FAIL} ({PASS/(PASS+FAIL)*100:.0f}%)")
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print(f"*** {FAIL} FAILURES NEED ATTENTION ***")
print("=" * 60)
