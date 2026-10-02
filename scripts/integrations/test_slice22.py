#!/usr/bin/env python3
"""Slice 22 — FTL Profit body, fill-vs-ack labels, Alpaca one flag.

UNRECONCILED cannot appear as realized P&L. Spot EXECUTED only on
extract_spot_fill.complete. Missing place_market_order stays FAILED.
latency_arb POST-ack → UNRECONCILED; cron stays dry-run.
No network, no keys, no --live, no nautilus_trader.
Run: python3 test_slice22.py
"""
import inspect
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import valedia_config as vc
sys.path.insert(0, HERE)
import settle_run as sr
sys.path.insert(0, HERE)
import master_allocator as ma
sys.path.insert(0, HERE)
import alpaca_executor as ae
sys.path.insert(0, HERE)
import latency_arb as la
sys.path.insert(0, HERE)
import unified_brain as ub
sys.path.insert(0, HERE)
import numpy as np


def test_unreconciled_ftl_cannot_be_realized_pnl():
    entries = {
        "ft_unrec": {"id": "ft_unrec", "source": "unreconciled",
                     "fill_status": "UNRECONCILED"},
        "ft_live": {"id": "ft_live", "source": "live", "fill_status": "FILLED"},
    }
    unrec = {"type": "settlement", "entry_id": "ft_unrec",
             "realized_pnl_net": 9.99, "won": True, "source": "unreconciled"}
    live = {"type": "settlement", "entry_id": "ft_live",
            "realized_pnl_net": -1.25, "won": False, "source": "live",
            "logged_at": datetime.now(timezone.utc).isoformat()}
    got = sr.fee_true_settlements([unrec, live], entries)
    assert got == [live], got
    none = sr.ftl_profit_signal(
        recs=[{"type": "entry", **entries["ft_unrec"]}, unrec], minutes=None)
    assert none is None, none
    body = sr.ftl_profit_signal(
        recs=[{"type": "entry", **entries["ft_live"]}, live], minutes=None)
    assert body is not None and body.startswith("Profit (settled since last report):"), body
    assert "Realized P&L (all-time, ledger, all venues, fee-incl.)" in body  # fee-true FTL only
    assert "9.99" not in body
    assert "−$1.25 (net loss)" in body and "-1.25" not in body  # real minus, money()
    assert "UNRECONCILED" not in body


def test_spot_fill_status_and_missing_writer():
    class _F:
        @staticmethod
        def extract_spot_fill(venue, raw):
            import forward_test_ledger as ftl
            return ftl.extract_spot_fill(venue, raw)
    ack = {"id": "ord1", "status": "accepted"}
    assert ma.spot_fill_status("alpaca", ack, _F) == "UNRECONCILED"
    fill = {"id": "ord2", "filled_qty": 1, "filled_avg_price": 10.0, "status": "filled"}
    assert ma.spot_fill_status("alpaca", fill, _F) == "EXECUTED"
    cb_ack = {"order_id": "cb1", "success": True}
    assert ma.spot_fill_status("coinbase", cb_ack, _F) == "UNRECONCILED"
    src = inspect.getsource(ma.execute_allocations)
    assert "spot_fill_status" in src
    assert 'r["status"] = "FAILED"' in src
    trader = open(os.path.join(HERE, "coinbase_trader.py")).read()
    assert "def place_market_order" not in trader


def test_alpaca_one_flag():
    prev = os.environ.get("ALPACA_PAPER")
    try:
        os.environ.pop("ALPACA_PAPER", None)
        assert ae.alpaca_is_live() is True
        os.environ["ALPACA_PAPER"] = "true"
        assert ae.alpaca_is_live() is False
        os.environ["ALPACA_PAPER"] = "false"
        assert ae.alpaca_is_live() is True
        os.environ["ALPACA_PAPER"] = "1"
        assert ae.alpaca_is_live() is False
    finally:
        if prev is None:
            os.environ.pop("ALPACA_PAPER", None)
        else:
            os.environ["ALPACA_PAPER"] = prev
    pm = open(os.path.join(HERE, "portfolio_monitor.py")).read()
    assert "alpaca_is_live" in pm
    assert 'ALPACA_PAPER", "true"' not in pm
    ub_src = inspect.getsource(ub.get_alpaca)
    assert "alpaca_is_live" in ub_src


def test_latency_arb_post_ack_unreconciled_cron_dry():
    assert la.ack_status({"order": {"order_id": "x", "status": "resting"}}) == "UNRECONCILED"
    filled = {"order": {"order_id": "y", "status": "executed",
                        "fill_count": 2, "count": 2, "yes_price": 40}}
    assert la.ack_status(filled) == "EXECUTED"
    src = inspect.getsource(la.execute_arb)
    assert 'result["status"] = ack_status(order)' in src
    assert "DRY_RUN" in src
    arb_run = open(os.path.join(HERE, "arb_run.py")).read()
    assert "dry-run" in arb_run
    assert "mode='execute'" not in arb_run and 'mode="execute"' not in arb_run


def test_ere_still_fail_closed():
    # ERE v3: the reader itself is fail-closed. Without valid state the gate
    # returns HALT/0 (safe False); the brain never treats that as tradeable.
    ere = ub.stage_ere_safety("BTC-USD")
    assert ere["status"] == "HALT", ere
    assert ere["multiplier"] == 0.0
    assert ere["safe"] is False


def test_version_stamp_on_headers():
    st = vc.version_stamp()
    assert vc.VALEDIA_VERSION in st or st.startswith("v" + vc.VALEDIA_VERSION), st
    msg = sr.format_settled_signal(1.0, 1, 0, 1.0, "1W/0L")
    assert vc.VALEDIA_VERSION in msg or st in msg
    ma_src = inspect.getsource(ma.run)
    assert "version_stamp" in ma_src
    se = open(os.path.join(HERE, "sports_engine.py")).read()
    assert "version_stamp" in se
    ub_src = open(os.path.join(HERE, "unified_brain.py")).read()
    assert "version_stamp" in ub_src


if __name__ == "__main__":
    test_unreconciled_ftl_cannot_be_realized_pnl()
    test_spot_fill_status_and_missing_writer()
    test_alpaca_one_flag()
    test_latency_arb_post_ack_unreconciled_cron_dry()
    test_ere_still_fail_closed()
    test_version_stamp_on_headers()
    print("slice22 label accuracy: PASS")
