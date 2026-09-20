#!/usr/bin/env python3
"""SCAN-ACCURACY — honest Signal scan, oil ticker fence, Kalshi deployable.

No network, no keys, no --live, no writer restore, no weather/oil unfence.
Run: python3 test_scan_accuracy.py
"""
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import valedia_config as vc
import master_allocator as ma
import kalshi_executor as ke
import kalshi_auth


def _restore_writers():
    vc.VENUE_WRITER["kalshi"] = "valedia"
    vc.VENUE_WRITER["coinbase"] = "valedia"
    vc.VENUE_WRITER["alpaca"] = "valedia"


def _state():
    return {
        "balance": 103.0, "portfolio_value": 48.0, "n_positions": 3,
        "deployed": 48.0, "max_per_trade": 10.0, "max_deployable": 60.0,
        "positions": [],
    }


def _opp(ticker, strategy, price, edge, title="t"):
    return {
        "ticker": ticker, "strategy": strategy, "side": "no",
        "price": price, "edge": edge, "score": 50, "title": title,
        "hours_to_exp": 24, "rationale": "fee-true sim",
    }


def test_hygiene_and_version():
    _restore_writers()
    assert vc.VALEDIA_VERSION == "0.45.0", vc.VALEDIA_VERSION
    assert vc.WEATHER_LIVE is False
    import polymarket_us_client as pmu
    assert pmu.live_enabled() is False
    assert vc.VENUE_WRITER["kalshi"] == "valedia"
    src = open(os.path.join(HERE, "kalshi_executor.py")).read()
    assert "WHAT EXTRA CAPITAL COULD DO" not in src
    assert "avg ROI on top picks" not in src
    lane = open(os.path.join(ROOT, "recommendations", "GROK-BUILD-LANE.md")).read()
    assert "Accuracy bar" in lane
    inv = open(os.path.join(ROOT, "recommendations", "PROFIT-PATHS-INVENTORY.md")).read()
    assert "OIL_BRACKET" in inv


def test_opportunity_report_no_hype():
    _restore_writers()
    # Cheap YES-style edge/price would invent 100%+ if averaged. Do not.
    cheap = _opp("KXGDP-1", "NO_HARVEST", 0.20, 0.75, "cheap tail")
    mid = _opp("KXCPI-1", "EXPIRY_SCALP", 0.90, 0.08, "near expiry")
    oil = _opp("KXWTI-26MAR23-T99.99", "NO_HARVEST", 0.85, 0.10, "WTI oil")
    import coinbase_trader
    import alpaca_executor
    orig_cb, orig_alp = coinbase_trader.get_balances, alpaca_executor.get_account
    coinbase_trader.get_balances = lambda: {"USD": {"balance": 0}, "USDC": {"balance": 0}}
    alpaca_executor.get_account = lambda: {"cash": 1.0, "equity": 30.0}
    try:
        report = ke.opportunity_report(state=_state(), opps=[cheap, mid, oil])
    finally:
        coinbase_trader.get_balances = orig_cb
        alpaca_executor.get_account = orig_alp
    assert "[not fills / not realized P&L]" in report
    assert "WHAT EXTRA CAPITAL" not in report
    assert "avg ROI" not in report
    assert "ROI on deployed capital" not in report
    assert "fee-true Kalshi sim" in report
    assert "not additive" in report
    assert "FENCED/SKIP_LIVE_FENCE" in report
    assert "broker cash/equity" in report
    rois = [float(x) for x in re.findall(r"\(([+-]?\d+)% ROI\)", report)]
    assert rois, report
    for extra in re.findall(r"Deposit \$\d+ .*?\((\d+)%", report):
        assert float(extra) <= max(rois) + 1.0, (extra, rois, report)
    fit = ke.constrained_scan_profit(
        [{"total_cost": 80, "potential_profit": 40},
         {"total_cost": 80, "potential_profit": 40}],
        cash=100, deployable=100)
    assert abs(fit - 40) < 1e-9, fit


def test_oil_ticker_fence_no_harvest():
    _restore_writers()
    assert vc.live_exec_fenced("OIL_BRACKET")
    assert vc.live_exec_fenced("NO_HARVEST", ticker="KXWTI-26MAR23-T99.99")
    assert vc.live_exec_fenced("NO_HARVEST", ticker="KXOIL-26APR01")
    assert vc.live_exec_fenced("CONVICTION_YES", ticker="WTI-99")
    assert not vc.live_exec_fenced("NO_HARVEST", ticker="KXBTC-26MAR23")
    assert not vc.live_exec_fenced("SPORTS")
    assert vc.weather_live_enabled() is False

    dummy = {
        "platform": "kalshi", "type": "NO_HARVEST",
        "ticker": "KXWTI-26MAR23-T99.99", "title": "oil",
        "side": "no", "price": 0.85, "allocation": 5.0,
        "expected_profit": 0.4, "hours_to_resolve": 24, "raw": {},
    }
    called = []
    orig_co = kalshi_auth.create_order
    orig_pos = kalshi_auth.get_positions
    orig_plan, orig_mom = ma.build_execution_plan, ma.MakerOrderManager
    orig_bl = ma._is_kalshi_blacklisted
    orig_pause = ma._is_strategy_paused
    orig_intel = sys.modules.get("intel_layer")
    fake = types.ModuleType("intel_layer")

    class _I:
        def get_kalshi_conviction(self, *a, **k):
            return {"recommended_side": "no", "confidence": 0.2, "conviction_yes": 10}

    fake.get_intel = lambda: _I()
    sys.modules["intel_layer"] = fake
    ma._is_strategy_paused = lambda n: False
    ma.build_execution_plan = None
    ma.MakerOrderManager = None
    ma._is_kalshi_blacklisted = lambda t: False
    kalshi_auth.create_order = lambda *a, **k: called.append(a) or {"order": {"status": "resting"}}
    kalshi_auth.get_positions = lambda: {"market_positions": []}
    orig_ke_co = ke.create_order
    ke.create_order = lambda **k: called.append(k) or {"order": {"status": "resting"}}
    try:
        out = ma.execute_allocations([dummy], dry_run=False)
        assert out[0]["status"] == "SKIP_LIVE_FENCE", out
        assert called == [], called
        result = ke.execute_trade(
            _opp("KXWTI-26MAR23-T99.99", "NO_HARVEST", 0.85, 0.10),
            _state(), dry_run=False)
        assert result["status"] == "SKIP_LIVE_FENCE", result
        assert called == [], called
        sport = ke.execute_trade(
            _opp("KXNBAGAME-1", "SPORTS", 0.60, 0.35),
            _state(), dry_run=True)
        assert sport["status"] == "DRY_RUN", sport
    finally:
        kalshi_auth.create_order = orig_co
        kalshi_auth.get_positions = orig_pos
        ma.build_execution_plan = orig_plan
        ma.MakerOrderManager = orig_mom
        ma._is_kalshi_blacklisted = orig_bl
        ma._is_strategy_paused = orig_pause
        ke.create_order = orig_ke_co
        if orig_intel is not None:
            sys.modules["intel_layer"] = orig_intel
        else:
            sys.modules.pop("intel_layer", None)


def test_kalshi_deployable_not_zeroed_by_alpaca():
    _restore_writers()
    import alpaca_executor
    import coinbase_trader
    orig_bal = kalshi_auth.get_balance
    orig_acct = alpaca_executor.get_account
    orig_cb = coinbase_trader.get_balances
    orig_writers = dict(vc.VENUE_WRITER)
    kalshi_auth.get_balance = lambda: {"balance": 10300, "portfolio_value": 4800}
    alpaca_executor.get_account = lambda: {"cash": 1.0, "equity": 30.0}
    coinbase_trader.get_balances = lambda: {"USD": {"balance": 0}, "USDC": {"balance": 0}}
    vc.VENUE_WRITER["coinbase"] = "none"
    vc.VENUE_WRITER["alpaca"] = "none"
    try:
        cap = ma.get_total_capital()
        assert cap["kalshi"] == 103.0, cap
        assert abs(cap["kalshi_deployed"] - 48.0) < 1e-9, cap
        assert cap["alpaca_equity"] == 30.0, cap
        assert cap["kalshi_deployable"] > 0, cap
        assert cap["deployable"] > 0, cap
        mixed = (103.0 + 0 + 0 + 1.0) * ma.MAX_TOTAL_DEPLOY_PCT - (48.0 + 29.0)
        assert mixed <= 0, mixed  # old formula would zero
        assert cap["deployable"] == cap["kalshi_deployable"], cap
    finally:
        kalshi_auth.get_balance = orig_bal
        alpaca_executor.get_account = orig_acct
        coinbase_trader.get_balances = orig_cb
        vc.VENUE_WRITER.clear()
        vc.VENUE_WRITER.update(orig_writers)
        _restore_writers()


if __name__ == "__main__":
    test_hygiene_and_version()
    test_opportunity_report_no_hype()
    test_oil_ticker_fence_no_harvest()
    test_kalshi_deployable_not_zeroed_by_alpaca()
    print("SCAN-ACCURACY: PASS")
