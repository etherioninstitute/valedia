#!/usr/bin/env python3
"""Slice 8 — one fusion + one fee table; scalper/weather dead; REBALANCER_AUTOEXEC=False.

No network, no keys, no --live, no nautilus_trader.
Run: python3 test_slice8.py
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import valedia_config as vc
import fee_engine as fe
import unified_brain as ub  # before rebalancer/kelly, which insert the live tree
sys.path.insert(0, HERE)
import master_allocator as ma
sys.path.insert(0, HERE)
import rebalancer as rb
sys.path.insert(0, HERE)
import weather_arb as wx
sys.path.insert(0, HERE)
import kelly_sizing as ks  # may insert live-tree path
sys.path.insert(0, HERE)

assert os.path.dirname(os.path.abspath(ub.__file__)) == HERE, ub.__file__


def _restore():
    vc.NAUTILUS_ENABLED = False
    vc.NAUTILUS_LIVE = False
    vc.NAUTILUS_SHADOW_COINBASE = True
    vc.VENUE_WRITER["coinbase"] = "valedia"
    vc.REBALANCER_AUTOEXEC = False
    os.environ.pop("REBALANCER_AUTOEXEC", None)
    os.environ.pop("SPOT_RECONCILE_HTTP", None)


def test_fusion_single_source():
    assert vc.FUSION_WEIGHTS["mirofish"] == vc.MIROFISH_WEIGHT
    we = vc.fusion_weights_for("equity")
    wc = vc.fusion_weights_for("crypto")
    for w in (we, wc):
        s = w["kernel"] + w["mirofish"] + w["ml"] + w["intel"] + w["remainder"]
        assert abs(s - 1.0) < 1e-9, (w, s)
    assert we["kernel"] == 0.35 and wc["kernel"] == 0.30
    assert we["intel"] == 0.10 and wc["intel"] == 0.15

    ere = {"multiplier": 1.0}
    intel = {"fg_signal": 0}
    eq = ub.stage_fusion({"score": 50}, ere, intel, 50, 50, "equity")
    cr = ub.stage_fusion({"score": 50}, ere, intel, 50, 50, "crypto")
    assert eq == 50.0 and cr == 50.0

    hi = ub.stage_fusion({"score": 100}, ere, intel, 50, 50, "equity")
    assert hi == 67.5, hi  # 50 + 50*0.35 remainder-neutral

    src_ub = open(os.path.join(HERE, "unified_brain.py")).read()
    src_sf = open(os.path.join(HERE, "signal_fusion.py")).read()
    src_vc = open(os.path.join(HERE, "valedia_config.py")).read()
    assert "FUSION_WEIGHTS" in src_vc
    assert "fusion_weights_for" in src_ub
    assert re.search(r"LIVE_PATH\s*=\s*False", src_sf)
    assert not re.search(r"kernel_weight\s*=\s*0\.3[05]", src_ub)
    assert "from valedia_config import" in src_ub


def test_one_fee_table():
    assert not hasattr(ub, "FEES")
    src_ub = open(os.path.join(HERE, "unified_brain.py")).read()
    assert not re.search(r"^FEES\s*=", src_ub, re.M)
    assert "use_static=True" in src_ub
    assert "roundtrip_cost" in src_ub

    cb = fe.STATIC_FEES["coinbase"]
    assert cb["taker"] == 0.006 and cb["spread"] == 0.005
    alp = fe.STATIC_FEES["alpaca"]
    assert alp.get("taker", 0) == 0 and alp.get("maker", 0) == 0

    # Coinbase spread is in the canonical round-trip — 1% edge is -EV (was a lie).
    rt = fe.roundtrip_cost("coinbase", 100.0, use_static=True)
    assert abs(rt["total_roundtrip"] - 2.2) < 1e-6, rt  # 2*(0.6%+0.5%)
    gate_fail = ub.stage_fee_gate(100.0, 1.0, "coinbase")
    assert gate_fail["pass"] is False, gate_fail
    gate_ok = ub.stage_fee_gate(100.0, 5.0, "coinbase")
    assert gate_ok["pass"] is True, gate_ok

    # KellySizer is the brain sizer (not edge*0.5*equity).
    assert "KellySizer" in src_ub
    sized = ub.stage_kelly(
        80, 10000, max_pct=0.04, expected_return_pct=5.0,
        track_record_mult=0.5, risk_factor=1.0, concurrent_bets=1)
    assert sized > 0
    assert sized <= 10000 * 0.04 + 1e-9
    assert ub.stage_kelly(50, 10000) == 0.0


def test_scalper_weather_dead():
    assert vc.SCALPER_LIVE is False
    assert vc.WEATHER_LIVE is False
    assert vc.scalper_live_enabled() is False
    assert vc.weather_live_enabled() is False
    assert vc.is_killed_strategy("WEATHER_ARB")
    assert vc.is_killed_strategy("OIL_BRACKET")
    assert not vc.is_killed_strategy("SPORTS")

    called = []
    rec = ub.scalper_place_buy("SPY", 2.0, alp_buy_fn=lambda *a, **k: called.append(1))
    assert rec["bought"] is False
    assert rec["status"] == "DISABLED"
    assert called == []

    src_ub = open(os.path.join(HERE, "unified_brain.py")).read()
    scalp_block = src_ub.split("# ── Scalper", 1)[-1].split("# ── Summary", 1)[0]
    assert "alp_buy_fn" not in scalp_block
    assert "book_spot_fill" not in scalp_block
    assert "scalper_place_buy" in scalp_block

    src_wx = open(os.path.join(HERE, "weather_arb.py")).read()
    exec_block = src_wx.split("def execute_weather_trades", 1)[-1].split("def run", 1)[0]
    assert "create_order(" not in exec_block
    out = wx.execute_weather_trades(
        [{"ticker": "KXHIGHNY", "city": "NY", "side": "yes", "price": 0.40,
          "profit_per": 0.5, "edge_pct": 20, "forecast_high": 80, "bracket": "x"}],
        dry_run=False, max_trades=3)
    assert out, out
    assert all(r["status"] == "DRY_RUN" for r in out)
    assert all(r.get("reason") == "WEATHER_ARB_KILLED" for r in out)

    src_wt = open(os.path.join(HERE, "weather_trade.py")).read()
    assert "dry_run=True" in src_wt
    assert "dry_run=False" not in src_wt

    assert ma.gather_weather_opportunities() == []
    src_ma = open(os.path.join(HERE, "master_allocator.py")).read()
    assert "from weather_arb import find_edges" not in src_ma

    dummy = {
        "platform": "kalshi", "type": "WEATHER_ARB", "ticker": "WX1",
        "title": "wx", "side": "yes", "price": 0.4, "ev_per_dollar": 0.5,
        "ev_per_day": 0.5, "hours_to_resolve": 24, "confidence": 0.9,
        "max_loss_pct": 100, "composite_score": 90,
    }
    oil = dict(dummy, type="OIL_BRACKET", ticker="OIL1")
    sports = dict(dummy, type="SPORTS", ticker="SP1")
    # Slice 9: unpaused weather/oil may be sized (evaluation). Live fence is execute.
    orig_pause = ma._is_strategy_paused
    ma._is_strategy_paused = lambda n: False
    try:
        allocs, _why = ma.allocate([dummy, oil, sports], {"deployable": 100.0})
        tickers = {a["ticker"] for a in allocs}
        assert "WX1" in tickers and "OIL1" in tickers and "SP1" in tickers, tickers
        called = []
        import types
        import kalshi_auth
        orig_co = kalshi_auth.create_order
        orig_pos = kalshi_auth.get_positions
        orig_plan, orig_mom = ma.build_execution_plan, ma.MakerOrderManager
        orig_bl = ma._is_kalshi_blacklisted
        orig_intel = sys.modules.get("intel_layer")
        fake = types.ModuleType("intel_layer")

        class _I:
            def get_kalshi_conviction(self, *a, **k):
                return {"recommended_side": "yes", "confidence": 0.2, "conviction_yes": 50}

        fake.get_intel = lambda: _I()
        sys.modules["intel_layer"] = fake
        kalshi_auth.create_order = lambda *a, **k: called.append(a) or {"order": {"status": "resting"}}
        kalshi_auth.get_positions = lambda: {"market_positions": []}
        ma.build_execution_plan = None
        ma.MakerOrderManager = None
        ma._is_kalshi_blacklisted = lambda t: False
        try:
            fenced = [a for a in allocs if a["ticker"] in ("WX1", "OIL1")]
            out = ma.execute_allocations(fenced, dry_run=False)
            by = {r["ticker"]: r["status"] for r in out}
            assert by["WX1"] == "SKIP_LIVE_FENCE", by
            assert by["OIL1"] == "SKIP_LIVE_FENCE", by
            assert called == [], called  # fenced names never reach create_order
        finally:
            kalshi_auth.create_order = orig_co
            kalshi_auth.get_positions = orig_pos
            ma.build_execution_plan = orig_plan
            ma.MakerOrderManager = orig_mom
            ma._is_kalshi_blacklisted = orig_bl
            if orig_intel is not None:
                sys.modules["intel_layer"] = orig_intel
            else:
                sys.modules.pop("intel_layer", None)
    finally:
        ma._is_strategy_paused = orig_pause


def test_rebalancer_autoexec_false():
    _restore()
    assert vc.REBALANCER_AUTOEXEC is False
    assert vc.rebalancer_autoexec_enabled() is False
    assert vc.SPOT_RECONCILE_HTTP is False
    assert vc.venue_writer("coinbase") == "valedia"
    assert vc.NAUTILUS_ENABLED is False
    assert vc.NAUTILUS_LIVE is False

    src_rb = open(os.path.join(HERE, "rebalancer.py")).read()
    assert "market_buy(" not in src_rb
    assert "market_sell(" not in src_rb
    assert "import nautilus_trader" not in src_rb

    buys, sells = [], []
    holdings, usd, total = (
        {"BTC-USD": {"amount": 0.08, "price": 1000.0, "value": 80.0},
         "ETH-USD": {"amount": 0.0, "price": 0.0, "value": 0.0},
         "SOL-USD": {"amount": 0.0, "price": 0.0, "value": 0.0},
         "XRP-USD": {"amount": 0.0, "price": 0.0, "value": 0.0},
         "ADA-USD": {"amount": 0.0, "price": 0.0, "value": 0.0},
         "AVAX-USD": {"amount": 0.0, "price": 0.0, "value": 0.0}},
        20.0, 100.0,
    )
    trades = rb.propose_rebalance_trades(holdings, usd, total)
    assert trades

    # Default False: no POST even with injected writers.
    posted = rb.exec_rebalance_trades(
        trades, market_buy_fn=lambda *a: buys.append(a),
        market_sell_fn=lambda *a: sells.append(a),
        gates={"live_flag": True, "ere_level": "NORMAL", "fee_ok": True,
               "category_ok": True})
    assert posted == []
    assert buys == [] and sells == []

    # AUTOEXEC True + NT flags still cannot POST.
    vc.REBALANCER_AUTOEXEC = True
    vc.NAUTILUS_ENABLED = True
    vc.NAUTILUS_LIVE = True
    vc.VENUE_WRITER["coinbase"] = "nautilus"
    assert rb.rebalancer_may_exec({"live_flag": True, "ere_level": "NORMAL",
                                   "fee_ok": True, "category_ok": True}) is False
    posted = rb.exec_rebalance_trades(
        trades, market_buy_fn=lambda *a: buys.append(a),
        market_sell_fn=lambda *a: sells.append(a),
        gates={"live_flag": True, "ere_level": "NORMAL", "fee_ok": True,
               "category_ok": True})
    assert posted == []
    assert buys == [] and sells == []
    _restore()

    # AUTOEXEC True + valedia + live_flag False → still no POST (gates).
    vc.REBALANCER_AUTOEXEC = True
    assert rb.rebalancer_may_exec({"live_flag": False}) is False
    posted = rb.exec_rebalance_trades(
        trades, market_buy_fn=lambda *a: buys.append(a),
        market_sell_fn=lambda *a: sells.append(a), gates={"live_flag": False})
    assert posted == []
    _restore()

    # AUTOEXEC True + valedia + live_flag + injected mocks → POST to mock only.
    vc.REBALANCER_AUTOEXEC = True
    gates = {"live_flag": True, "ere_level": "NORMAL", "fee_ok": True,
             "category_ok": True}
    assert rb.rebalancer_may_exec(gates) is True
    posted = rb.exec_rebalance_trades(
        trades, market_buy_fn=lambda *a: buys.append(a),
        market_sell_fn=lambda *a: sells.append(a), gates=gates)
    assert posted
    assert buys or sells
    _restore()
    assert vc.REBALANCER_AUTOEXEC is False
    assert vc.rebalancer_autoexec_enabled() is False

    # run_rebalance default: shadows, no POST.
    buys.clear(); sells.clear()
    qpath = tempfile.mkstemp(suffix=".jsonl")[1]
    try:
        report = rb.run_rebalance(
            get_balances_fn=lambda: {
                "USD": {"balance": 20.0}, "BTC": {"balance": 0.08},
                "ETH": {"balance": 0}, "SOL": {"balance": 0},
                "XRP": {"balance": 0}, "ADA": {"balance": 0},
                "AVAX": {"balance": 0},
            },
            get_price_fn=lambda t: {"BTC-USD": 1000.0}.get(t, 0.0),
            queue_path=qpath, gates={"live_flag": False},
            market_buy_fn=lambda *a: buys.append(a),
            market_sell_fn=lambda *a: sells.append(a),
        )
        assert "shadow only" in report or "REBALANCER_AUTOEXEC=False" in report
        assert buys == [] and sells == []
    finally:
        os.remove(qpath)
        _restore()


def run():
    _restore()
    try:
        test_fusion_single_source()
        test_one_fee_table()
        test_scalper_weather_dead()
        test_rebalancer_autoexec_false()
        assert vc.VENUE_WRITER["coinbase"] == "valedia"
        assert vc.VENUE_WRITER["kalshi"] == "valedia"
        assert vc.NAUTILUS_ENABLED is False
        assert vc.SPOT_RECONCILE_HTTP is False
        assert vc.REBALANCER_AUTOEXEC is False
    finally:
        _restore()
    print("slice8 checks: PASS")


if __name__ == "__main__":
    run()
