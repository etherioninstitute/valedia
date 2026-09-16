#!/usr/bin/env python3
"""Slice 5A — reconcile_run UNRECONCILED scan with injected getter.

Default/mock getter never HTTP. Tests inject payloads only. No network, no keys.

Run: python3 test_reconcile_run.py
"""
import os
import tempfile

import forward_test_ledger as ftl
import reconcile_run as rr
import valedia_config as vc

HERE = os.path.dirname(os.path.abspath(__file__))


def _ledger():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    orig = ftl.LEDGER
    ftl.LEDGER = path
    return orig, path


def _restore(orig, path):
    ftl.LEDGER = orig
    rr.set_spot_order_getter(None)
    try:
        os.remove(path)
    except OSError:
        pass


def _cb_get(oid="oid-run", size="0.01", px="2000", fee="0.22"):
    return {"order": {
        "order_id": oid, "product_id": "ETH-USD", "side": "BUY", "status": "FILLED",
        "filled_size": size, "average_filled_price": px, "total_fees": fee,
    }}


def run():
    src = open(os.path.join(HERE, "reconcile_run.py")).read()
    assert "import requests" not in src
    assert "import coinbase_trader" not in src
    assert "from coinbase_trader" not in src
    assert "import nautilus_trader" not in src
    assert "_request(" not in src
    assert "def mock_spot_order_getter" in src
    assert "def set_spot_order_getter" in src
    assert "sr.reconcile_unreconciled" in src
    assert "ftl.reconcile()" in src
    assert vc.VENUE_WRITER["coinbase"] == "valedia"
    assert vc.NAUTILUS_ENABLED is False

    rr.set_spot_order_getter(None)
    assert rr.get_spot_order_getter() is rr.mock_spot_order_getter
    assert rr.mock_spot_order_getter("any", "coinbase") is None

    orig, path = _ledger()
    try:
        # 1. Default mock getter: UNRECONCILED stays UNRECONCILED. No Kelly.
        eid_u = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "ETH-USD", "buy", "coinbase",
            {"success": True, "order_id": "oid-run", "usd": 20.0},
            intended_source="live")
        stub = [r for r in ftl._read() if r.get("id") == eid_u][0]
        assert stub["source"] == "unreconciled"
        rec = rr.reconcile_unreconciled_run()
        assert rec["n"] >= 1
        assert rec["upgraded"] == 0, rec
        still = [r for r in ftl._read() if r.get("id") == eid_u][0]
        assert still["source"] == "unreconciled"
        assert still["fill_status"] == "UNRECONCILED"
        assert ftl.real_track_record()["n"] == 0
        assert ftl.open_position_count() == 0

        # 2. Injected getter upgrades fee-true live; stub closed; Kelly still 0 until settle.
        called = []

        def _getter(order_id, venue):
            called.append((order_id, venue))
            return _cb_get(order_id)

        rec = rr.reconcile_unreconciled_run(getter=_getter)
        assert rec["upgraded"] == 1, rec
        assert called == [("oid-run", "coinbase")]
        filled = [r for r in ftl._read()
                  if r.get("type") == "entry" and r.get("order_id") == "oid-run"
                  and r.get("fill_status") == "FILLED"]
        assert filled and filled[0]["source"] == "live"
        assert filled[0]["entry_fee"] > 0
        assert ftl.real_track_record()["n"] == 0
        assert ftl.open_position_count() == 1

        # 3. Module hook is used when getter arg omitted; restore mock after.
        eid_h = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "BTC-USD", "buy", "coinbase",
            {"success": True, "order_id": "oid-hook", "usd": 10.0},
            intended_source="live")
        hook_called = []

        def _hook(order_id, venue):
            hook_called.append((order_id, venue))
            return {"order": {
                "order_id": order_id, "filled_size": "0.002",
                "average_filled_price": "50000", "total_fees": "0.6",
            }}

        rr.set_spot_order_getter(_hook)
        assert rr.get_spot_order_getter() is _hook
        rec = rr.reconcile_unreconciled_run()
        assert rec["upgraded"] >= 1, rec
        assert ("oid-hook", "coinbase") in hook_called
        rr.set_spot_order_getter(None)
        assert rr.get_spot_order_getter() is rr.mock_spot_order_getter

        # 4. run(..., kalshi=False) never calls Kalshi; mock path is a no-op upgrade.
        eid_k = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "SOL-USD", "buy", "coinbase",
            {"success": True, "order_id": "oid-kalshi-skip", "usd": 8.0},
            intended_source="live")
        rc = rr.run(kalshi=False)
        assert rc == 0
        still_k = [r for r in ftl._read() if r.get("id") == eid_k][0]
        assert still_k["source"] == "unreconciled"
        assert ftl.real_track_record()["n"] == 0

        # 5. Explicit getter wins over a live-looking hook (tests never HTTP).
        boom = []

        def _must_not_run(order_id, venue):
            boom.append(1)
            raise RuntimeError("hook must not run when getter is passed")

        rr.set_spot_order_getter(_must_not_run)
        rec = rr.reconcile_unreconciled_run(getter=_getter)
        assert boom == []
        rr.set_spot_order_getter(None)

        # Paper intended_source + injected fill stays paper (no Kelly).
        n_before = ftl.real_track_record()["n"]
        eid_p = ftl.book_spot_fill(
            "EQUITY_MOMENTUM", "AAPL", "buy", "alpaca",
            {"id": "p-run", "status": "accepted"},
            intended_source="paper")

        def _paper_get(order_id, venue):
            return {"id": order_id, "status": "filled",
                    "filled_qty": "2", "filled_avg_price": "10"}

        rec = rr.reconcile_unreconciled_run(getter=_paper_get)
        pap = [r for r in ftl._read()
               if r.get("order_id") == "p-run" and r.get("fill_status") == "FILLED"]
        assert pap and pap[0]["source"] == "paper"
        ftl.settle_open("AAPL", "alpaca", 12.0, source="live")
        assert ftl.real_track_record()["n"] == n_before
    finally:
        _restore(orig, path)

    print("reconcile-run checks: PASS")


if __name__ == "__main__":
    run()
