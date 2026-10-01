#!/usr/bin/env python3
"""Slice 9 / P1.4 — pnl_tracker report-only; net-based pause; learning re-enable.

No network, no keys, no --live, no nautilus_trader.
Run: python3 test_slice9.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import forward_test_ledger as ftl
import master_allocator as ma
import pnl_tracker as pt
import valedia_config as vc
import weather_arb as wx
import unified_brain as ub


NOW = datetime(2026, 9, 17, 15, 0, tzinfo=timezone.utc)


def _tmp():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    return path


def _write_pnl(path, strategies):
    json.dump({"trades": [], "summary": {"strategies": strategies,
                                         "wins": 0, "losses": 0}}, open(path, "w"))


def test_config_pause_vs_fence():
    assert vc.PAUSE_TTL_DAYS == 7
    assert vc.live_exec_fenced("WEATHER_ARB")
    assert vc.live_exec_fenced("OIL_BRACKET")
    assert vc.is_killed_strategy("WEATHER_ARB")  # alias = live fence, not a grave
    assert not vc.live_exec_fenced("SPORTS")
    assert vc.scalper_live_enabled() is False
    assert vc.weather_live_enabled() is False
    assert vc.SPOT_RECONCILE_HTTP is False
    assert vc.REBALANCER_AUTOEXEC is False
    assert vc.NAUTILUS_ENABLED is False
    assert vc.VENUE_WRITER["coinbase"] == "valedia"


def test_fat_tail_not_paused_on_wr():
    """Low WR + fee-true net>0 must NOT pause (the WR-kill bug)."""
    bl, pnl = _tmp(), _tmp()
    orig = (pt.STRATEGY_BLACKLIST_PATH, pt.PNL_PATH)
    pt.STRATEGY_BLACKLIST_PATH, pt.PNL_PATH = bl, pnl
    _write_pnl(pnl, {"CRYPTO_BRACKET": {"wins": 4, "losses": 16, "profit": 40, "cost": 11}})
    try:
        stats = {"CRYPTO_BRACKET": {"n": 20, "wins": 4, "net": 29.0}}
        paused = pt.update_strategy_blacklist(now=NOW, stats=stats, path=bl)
        assert "CRYPTO_BRACKET" not in paused, paused
        assert not pt.is_strategy_paused("CRYPTO_BRACKET", now=NOW, path=bl)
        wr = pt.get_strategy_kill_list()
        assert wr and wr[0].get("fuse") == "report_only"
        raw = json.load(open(bl))
        assert raw.get("wr_report")  # stored as report, not a fuse
        assert "CRYPTO_BRACKET" not in raw.get("blacklisted", [])
    finally:
        pt.STRATEGY_BLACKLIST_PATH, pt.PNL_PATH = orig
        os.remove(bl); os.remove(pnl)


def test_net_pause_not_wr_after_three():
    """n=3 WR=0 is NOT enough. n>=4 and net<0 is."""
    bl, pnl = _tmp(), _tmp()
    orig = (pt.STRATEGY_BLACKLIST_PATH, pt.PNL_PATH)
    pt.STRATEGY_BLACKLIST_PATH, pt.PNL_PATH = bl, pnl
    _write_pnl(pnl, {"OTHER": {"wins": 0, "losses": 3, "profit": 0, "cost": 9}})
    try:
        thin = pt.update_strategy_blacklist(
            now=NOW, stats={"OTHER": {"n": 3, "wins": 0, "net": -9.0}}, path=bl)
        assert thin == [], thin
        paused = pt.update_strategy_blacklist(
            now=NOW, stats={"OIL_BRACKET": {"n": 4, "wins": 0, "net": -19.9}}, path=bl)
        assert paused == ["OIL_BRACKET"], paused
        until = pt.paused_until("OIL_BRACKET", now=NOW, path=bl)
        assert until == NOW + timedelta(days=vc.PAUSE_TTL_DAYS)
        raw = json.load(open(bl))
        assert raw["paused"][0]["source"] == "category_stats"
        assert "WR" not in raw["paused"][0]["reason"]
    finally:
        pt.STRATEGY_BLACKLIST_PATH, pt.PNL_PATH = orig
        os.remove(bl); os.remove(pnl)


def test_ttl_expiry_and_legacy_not_life_sentence():
    bl, pnl = _tmp(), _tmp()
    orig = (pt.STRATEGY_BLACKLIST_PATH, pt.PNL_PATH)
    pt.STRATEGY_BLACKLIST_PATH, pt.PNL_PATH = bl, pnl
    _write_pnl(pnl, {})
    try:
        # Legacy grave with no until → not paused.
        json.dump({"blacklisted": ["WEATHER_ARB", "OIL_BRACKET"],
                   "updated": "2026-09-13T06:00:09+00:00"}, open(bl, "w"))
        assert not pt.is_strategy_paused("WEATHER_ARB", now=NOW, path=bl)
        assert not pt.is_strategy_paused("OIL_BRACKET", now=NOW, path=bl)

        # Writer releases legacy to probation; does not immediately re-pause.
        stats = {"WEATHER_ARB": {"n": 11, "wins": 0, "net": -22.91},
                 "OIL_BRACKET": {"n": 4, "wins": 0, "net": -19.9}}
        paused = pt.update_strategy_blacklist(now=NOW, stats=stats, path=bl)
        assert paused == [], paused
        raw = json.load(open(bl))
        prob = {p["strategy"] for p in raw.get("probation") or []}
        assert "WEATHER_ARB" in prob and "OIL_BRACKET" in prob

        # Explicit pause expires at until.
        json.dump({"paused": [{
            "strategy": "MACRO", "until": (NOW + timedelta(days=1)).isoformat(),
            "reason": "net",
        }]}, open(bl, "w"))
        assert pt.is_strategy_paused("MACRO", now=NOW, path=bl)
        later = NOW + timedelta(days=2)
        assert not pt.is_strategy_paused("MACRO", now=later, path=bl)
        released = pt.update_strategy_blacklist(now=later, stats={
            "MACRO": {"n": 4, "wins": 0, "net": -5.0}}, path=bl)
        assert "MACRO" not in released  # probation, not re-paused same cycle
        raw = json.load(open(bl))
        assert any(p.get("end_reason") == "ttl" for p in raw.get("history") or [])
    finally:
        pt.STRATEGY_BLACKLIST_PATH, pt.PNL_PATH = orig
        os.remove(bl); os.remove(pnl)


def test_evidence_unpause():
    bl, pnl = _tmp(), _tmp()
    orig = (pt.STRATEGY_BLACKLIST_PATH, pt.PNL_PATH)
    pt.STRATEGY_BLACKLIST_PATH, pt.PNL_PATH = bl, pnl
    _write_pnl(pnl, {})
    try:
        json.dump({"paused": [{
            "strategy": "WEATHER_ARB",
            "until": (NOW + timedelta(days=5)).isoformat(),
            "reason": "old net", "net": -22.91, "n": 11,
        }]}, open(bl, "w"))
        assert pt.is_strategy_paused("WEATHER_ARB", now=NOW, path=bl)
        paused = pt.update_strategy_blacklist(now=NOW, stats={
            "WEATHER_ARB": {"n": 20, "wins": 12, "net": 8.0}}, path=bl)
        assert paused == [], paused
        assert not pt.is_strategy_paused("WEATHER_ARB", now=NOW, path=bl)
        raw = json.load(open(bl))
        assert any(h.get("end_reason") == "evidence_net_recovered"
                   for h in raw.get("history") or [])
        # recovered names are not stuffed into probation
        assert not any(_canon_eq(p.get("strategy"), "WEATHER_ARB")
                       for p in raw.get("probation") or [])
    finally:
        pt.STRATEGY_BLACKLIST_PATH, pt.PNL_PATH = orig
        os.remove(bl); os.remove(pnl)


def _canon_eq(a, b):
    return pt._canon(a) == pt._canon(b)


def test_allocator_pause_vs_fence():
    dummy = {
        "platform": "kalshi", "type": "WEATHER_ARB", "ticker": "WX1",
        "title": "wx", "side": "yes", "price": 0.4, "ev_per_dollar": 0.5,
        "ev_per_day": 0.5, "hours_to_resolve": 24, "confidence": 0.9,
        "max_loss_pct": 100, "composite_score": 90,
    }
    oil = dict(dummy, type="OIL_BRACKET", ticker="OIL1")
    sports = dict(dummy, type="SPORTS", ticker="SP1")
    orig = ma._is_strategy_paused

    ma._is_strategy_paused = lambda n: pt._canon(n) in ("WEATHER_ARB", "OIL_BRACKET")
    allocs, _ = ma.allocate([dummy, oil, sports], {"deployable": 100.0})
    tickers = {a["ticker"] for a in allocs}
    assert "WX1" not in tickers and "OIL1" not in tickers
    assert "SP1" in tickers

    ma._is_strategy_paused = lambda n: False
    allocs, _ = ma.allocate([dummy, oil, sports], {"deployable": 100.0})
    tickers = {a["ticker"] for a in allocs}
    assert "WX1" in tickers and "OIL1" in tickers and "SP1" in tickers

    # Execute: paused → SKIP_PAUSED; unpaused weather → SKIP_LIVE_FENCE; no create_order.
    called = []
    import types
    import kalshi_auth
    orig_co, orig_pos = kalshi_auth.create_order, kalshi_auth.get_positions
    orig_plan, orig_mom = ma.build_execution_plan, ma.MakerOrderManager
    orig_bl = ma._is_kalshi_blacklisted
    orig_intel = sys.modules.get("intel_layer")
    fake = types.ModuleType("intel_layer")

    class _I:
        def get_kalshi_conviction(self, *a, **k):
            return {"recommended_side": "yes", "confidence": 0.2, "conviction_yes": 50}

    fake.get_intel = lambda: _I()
    sys.modules["intel_layer"] = fake
    kalshi_auth.create_order = lambda *a, **k: called.append(a) or {"ok": True}
    kalshi_auth.get_positions = lambda: {"market_positions": []}
    ma.build_execution_plan = None
    ma.MakerOrderManager = None
    ma._is_kalshi_blacklisted = lambda t: False
    try:
        ma._is_strategy_paused = lambda n: pt._canon(n) == "WEATHER_ARB"
        subset = [a for a in allocs if a["ticker"] in ("WX1", "OIL1")]
        out = ma.execute_allocations(subset, dry_run=False)
        by = {r["ticker"]: r["status"] for r in out}
        assert by["WX1"] == "SKIP_PAUSED", by
        assert by["OIL1"] == "SKIP_LIVE_FENCE", by
        assert called == [], called

        ma._is_strategy_paused = lambda n: False
        out = ma.execute_allocations(
            [a for a in allocs if a["ticker"] == "OIL1"], dry_run=False)
        assert out[0]["status"] == "SKIP_LIVE_FENCE"
        assert called == []
    finally:
        ma._is_strategy_paused = orig
        kalshi_auth.create_order = orig_co
        kalshi_auth.get_positions = orig_pos
        ma.build_execution_plan = orig_plan
        ma.MakerOrderManager = orig_mom
        ma._is_kalshi_blacklisted = orig_bl
        if orig_intel is not None:
            sys.modules["intel_layer"] = orig_intel
        else:
            sys.modules.pop("intel_layer", None)


def test_category_gate_pause_until():
    orig = ftl._learning_pause_until
    orig_stats = ftl.category_stats
    orig_ov = ftl._overrides
    try:
        ftl.category_stats = lambda lookback_days=None: {
            "OIL_BRACKET": {"n": 4, "wins": 4, "net": 10.0}}
        ftl._overrides = lambda: {}
        until = NOW + timedelta(days=3)
        ftl._learning_pause_until = lambda cat, now=None: until if cat == "OIL_BRACKET" else None
        ok, why = ftl.should_trade_category("OIL_BRACKET")
        assert ok is False
        assert "paused until" in why and "not permanent" in why, why
        ftl._learning_pause_until = lambda *a, **k: None
        ok, why = ftl.should_trade_category("OIL_BRACKET")
        assert ok is True, why
    finally:
        ftl._learning_pause_until = orig
        ftl.category_stats = orig_stats
        ftl._overrides = orig_ov


def test_slice8_live_exec_still_dead():
    assert "create_order(" not in open(os.path.join(HERE, "weather_arb.py")).read().split(
        "def execute_weather_trades", 1)[-1].split("def run", 1)[0]
    out = wx.execute_weather_trades(
        [{"ticker": "KXHIGHNY", "city": "NY", "side": "yes", "price": 0.40,
          "profit_per": 0.5, "edge_pct": 20, "forecast_high": 80, "bracket": "x"}],
        dry_run=False, max_trades=3)
    assert out and all(r["status"] == "DRY_RUN" for r in out)
    called = []
    rec = ub.scalper_place_buy("SPY", 2.0, alp_buy_fn=lambda *a, **k: called.append(1))
    assert rec["bought"] is False and called == []
    assert ma.gather_weather_opportunities() == []
    src_ma = open(os.path.join(HERE, "master_allocator.py")).read()
    assert "from weather_arb import find_edges" not in src_ma


def test_pnl_fallback_is_report_only_not_gate():
    """Fee-blind pnl_tracker net is report-only: with ledger (fee-true) stats empty it
    must NOT pause. Replaces test_pnl_fallback_uses_net_not_wr, which asserted the
    fee-blind LOSER net paused -- the gap-fill behavior this change removes."""
    bl, pnl = _tmp(), _tmp()
    orig = (pt.STRATEGY_BLACKLIST_PATH, pt.PNL_PATH)
    pt.STRATEGY_BLACKLIST_PATH, pt.PNL_PATH = bl, pnl
    _write_pnl(pnl, {
        "FAT_TAIL": {"wins": 1, "losses": 5, "profit": 50, "cost": 10},
        "LOSER": {"wins": 0, "losses": 5, "profit": 0, "cost": 12},
    })
    try:
        paused = pt.update_strategy_blacklist(now=NOW, stats={}, path=bl)
        assert paused == [], paused
        rep = {r["strategy"]: r for r in json.load(open(bl))["fee_blind_report"]}
        assert rep["LOSER"]["net"] == -12.0 and rep["LOSER"]["report_only"] is True
        assert rep["FAT_TAIL"]["net"] == 40.0  # net +40 despite WR ~17%
    finally:
        pt.STRATEGY_BLACKLIST_PATH, pt.PNL_PATH = orig
        os.remove(bl); os.remove(pnl)


def run():
    test_config_pause_vs_fence()
    test_fat_tail_not_paused_on_wr()
    test_net_pause_not_wr_after_three()
    test_ttl_expiry_and_legacy_not_life_sentence()
    test_evidence_unpause()
    test_allocator_pause_vs_fence()
    test_category_gate_pause_until()
    test_slice8_live_exec_still_dead()
    test_pnl_fallback_is_report_only_not_gate()
    print("slice9 checks: PASS")


if __name__ == "__main__":
    run()
