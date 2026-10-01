#!/usr/bin/env python3
"""Slice 21 — trade-status label accuracy.

dry_run cannot emit bare [LIVE]; shadow cannot say filled; UNRECONCILED/sports-log
cannot claim FTL realized P&L; ERE no-op cannot report status OK.
No network, no keys, no --live, no nautilus_trader.
Run: python3 test_slice21.py
"""
import inspect
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import unified_brain as ub
sys.path.insert(0, HERE)
import weather_arb as wx
sys.path.insert(0, HERE)
import master_allocator as ma
sys.path.insert(0, HERE)
import sports_engine as se
sys.path.insert(0, HERE)
import settle_run as sr
sys.path.insert(0, HERE)
import pnl_tracker as pt
sys.path.insert(0, HERE)
import quant_engine as qe
sys.path.insert(0, HERE)
import kalshi_executor as ke


def test_ere_fail_closed_status_not_ok():
    long = np.linspace(100.0, 110.0, 80)
    ere = ub.stage_ere_safety(long)
    assert ere["status"] == "FAIL_CLOSED", ere
    assert ere["status"] != "OK"
    assert ere["safe"] is True
    assert ere["multiplier"] == 1.0
    short = np.linspace(100.0, 101.0, 10)
    ere2 = ub.stage_ere_safety(short)
    assert ere2["status"] == "INSUFFICIENT_DATA", ere2


def test_dry_run_cannot_emit_bare_live():
    for fn in (ma.run, se.run, wx.run):
        src = inspect.getsource(fn)
        assert "{'[DRY]' if dry_run else '[LIVE]'}" in src, fn.__name__
    wx_src = inspect.getsource(wx.run)
    assert "dry_run = True" in wx_src
    out = wx.execute_weather_trades(
        [{"ticker": "KXHIGHNY", "city": "NY", "side": "yes", "price": 0.40,
          "profit_per": 0.5, "edge_pct": 20, "forecast_high": 80, "bracket": "x"}],
        dry_run=False, max_trades=1)
    assert out and all(r["status"] == "DRY_RUN" for r in out), out
    banner_dry = f"🧠 MASTER ALLOCATOR (ts) {'[DRY]' if True else '[LIVE]'}"
    banner_live = f"🧠 MASTER ALLOCATOR (ts) {'[DRY]' if False else '[LIVE]'}"
    assert "[LIVE]" not in banner_dry and "[DRY]" in banner_dry
    assert "[LIVE]" in banner_live


def test_weather_forced_dry_banner():
    src = open(os.path.join(HERE, "weather_arb.py")).read()
    run_block = src.split("def run(", 1)[1].split("if __name__", 1)[0]
    assert "dry_run = True" in run_block
    assert "run(dry_run=True" in src
    wt = open(os.path.join(HERE, "weather_trade.py")).read()
    assert "dry_run=False" not in wt


def test_shadow_cannot_say_filled():
    for name in ("nautilus_shadow_coinbase.py", "nautilus_shadow_venues.py",
                 "intel_opportunity_gatherer.py"):
        src = open(os.path.join(HERE, name)).read().lower()
        assert "filled" not in src, name
        assert 'status"] = "executed"' not in src


def test_unreconciled_cannot_say_realized_pnl():
    # Only fee_true_settlements (UNRECONCILED excluded) reach this formatter, and
    # signal_verify re-reads the ledger before send -> "Realized (ledger, fee-incl.)".
    msg = sr.format_settled_signal(-1.25, 0, 1, -12.5, "0W/4L")
    assert msg.startswith("Profit (settled since last report):"), msg
    assert "Realized P&L (all-time, ledger, all venues, fee-incl.)" in msg, msg
    arb = sr.format_arb_signal({"net_pnl": 2.0, "wins": 1, "losses": 0})
    assert not arb.startswith("Profit:"), arb  # arb-log is never a Profit ping
    assert "realized" not in arb.lower()
    assert "not FTL" in arb and "not sent" in arb
    pnl = pt.simple_pnl_message({"net_pnl": 1.0, "current_deployed": 2.0,
                                 "current_balance": 3.0, "wins": 1, "losses": 0,
                                 "win_rate": 100})
    assert "Realized P&L" not in pnl, pnl
    assert "not FTL" in pnl and "log only" in pnl, pnl


def test_brain_acked_not_executed_fill():
    src = open(os.path.join(HERE, "unified_brain.py")).read()
    assert 'EXECUTED: {len(actions)} trades' not in src
    assert "ACKED:" in src
    assert "POST-ack, not fill-confirmed" in src
    assert 'status": "FAIL_CLOSED"' in src


def test_allocator_prints_unreconciled_status():
    src = inspect.getsource(ma.run)
    assert "UNRECONCILED" in src
    assert '{st:16}' in src or "{st:16}" in src


def test_quant_scan_only_not_quarter_kelly():
    src = inspect.getsource(qe.run)
    assert "[SCAN-ONLY]" in src
    assert "Kelly: quarter" not in src
    assert "Kalshi: ACTIVE" not in src
    assert "Kalshi: SCAN" in src


def test_opportunity_scan_not_realized():
    src = inspect.getsource(ke.opportunity_report)
    assert "VALEDIA PROFIT SCAN" not in src
    assert "not fills / not realized P&L" in src
    audit = inspect.getsource(ke.audit)
    assert "expected (not realized)" in audit.lower() or "Expected (not realized)" in audit


if __name__ == "__main__":
    test_ere_fail_closed_status_not_ok()
    test_dry_run_cannot_emit_bare_live()
    test_weather_forced_dry_banner()
    test_shadow_cannot_say_filled()
    test_unreconciled_cannot_say_realized_pnl()
    test_brain_acked_not_executed_fill()
    test_allocator_prints_unreconciled_status()
    test_quant_scan_only_not_quarter_kelly()
    test_opportunity_scan_not_realized()
    print("slice21 label accuracy: PASS")
