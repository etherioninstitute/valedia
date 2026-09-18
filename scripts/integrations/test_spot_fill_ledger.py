#!/usr/bin/env python3
"""Slice 3 — Coinbase/Alpaca ledger truth.

No network, no keys, no orders. Isolated temp ledger.
Run: python3 test_spot_fill_ledger.py
"""
import os
import tempfile

import forward_test_ledger as ftl

HERE = os.path.dirname(os.path.abspath(__file__))


def _ledger():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    orig = ftl.LEDGER
    ftl.LEDGER = path
    return orig, path


def _restore(orig, path):
    ftl.LEDGER = orig
    try:
        os.remove(path)
    except OSError:
        pass


def run():
    # 0. Quote/notional/POST-ack is NOT a fill.
    ext = ftl.extract_spot_fill("coinbase", {
        "success": True, "order_id": "abc", "usd": 10.0, "pair": "BTC-USD", "qty": 0.01,
        "price": 65000,
    })
    assert ext["complete"] is False, ext
    assert ftl.spot_ack_ok("coinbase", {"success": True, "order_id": "abc"}) is True
    assert ftl.spot_ack_ok("coinbase", {"success": False, "error": "nope"}) is False
    assert ftl.spot_ack_ok("alpaca", {"id": "oid", "status": "accepted"}) is True
    assert ftl.spot_ack_ok("alpaca", {"id": "oid", "status": "rejected"}) is False
    assert ftl.spot_ack_ok("alpaca", {"message": "forbidden", "code": 403}) is False

    # Nested Coinbase fill + Alpaca fill parse.
    cb = ftl.extract_spot_fill("coinbase", {
        "success": True,
        "success_response": {
            "order_id": "oid1", "filled_size": "0.001",
            "average_filled_price": "65000", "total_fees": "0.715",
        },
    })
    assert cb["complete"] is True and cb["qty"] == 0.001 and cb["price"] == 65000
    assert cb["fee"] == 0.715 and cb["order_id"] == "oid1"
    alp = ftl.extract_spot_fill("alpaca", {
        "id": "a1", "status": "filled", "filled_qty": "0.5", "filled_avg_price": "100",
        "commission": "0",
    })
    assert alp["complete"] is True and alp["qty"] == 0.5 and alp["fee"] == 0.0

    orig, path = _ledger()
    try:
        # 1. Incomplete Coinbase POST → UNRECONCILED, does not lift Kelly / open count.
        eid_u = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "BTC-USD", "buy", "coinbase",
            {"success": True, "order_id": "post-only", "usd": 25.0},
            intended_source="live")
        rows = ftl._read()
        assert rows[-1]["id"] == eid_u
        assert rows[-1]["source"] == "unreconciled"
        assert rows[-1]["fill_status"] == "UNRECONCILED"
        assert ftl.open_position_count() == 0
        assert ftl.real_track_record()["n"] == 0

        # Live settlement cannot launder an unreconciled entry into Kelly.
        ftl.settle_open("BTC-USD", "coinbase", 70000.0, fee=0.0, source="live")
        tr = ftl.real_track_record()
        assert tr["n"] == 0 and tr["net"] == 0.0, tr
        assert ftl.open_position_count() == 0

        # 2. Real Coinbase fill books live + fee > 0, then settlement lifts Kelly.
        eid = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "ETH-USD", "buy", "coinbase",
            {"filled_size": 0.01, "average_filled_price": 2000.0, "order_id": "fill1"},
            intended_source="live")
        rec = [r for r in ftl._read() if r.get("id") == eid][0]
        assert rec["source"] == "live"
        assert rec["fill_status"] == "FILLED"
        assert rec["contracts"] == 0.01
        assert rec["entry_price"] == 2000.0
        assert rec["entry_fee"] > 0, rec  # fee_engine coinbase taker+spread
        assert rec["venue"] == "coinbase"
        assert ftl.open_position_count() == 1
        assert ftl.real_track_record()["n"] == 0  # not settled yet

        ftl.settle_spot_fill(
            "ETH-USD", "coinbase",
            {"filled_size": 0.01, "average_filled_price": 2100.0, "total_fees": 0.2},
            intended_source="live")
        tr = ftl.real_track_record()
        assert tr["n"] == 1, tr
        assert tr["wins"] == 1
        assert tr["net"] > 0, tr
        assert ftl.open_position_count() == 0

        # 3. Alpaca live fill books live, not paper.
        eid_a = ftl.book_spot_fill(
            "EQUITY_MOMENTUM", "AAPL", "buy", "alpaca",
            {"id": "ap1", "filled_qty": 1.0, "filled_avg_price": 50.0, "status": "filled"},
            intended_source="live")
        rec_a = [r for r in ftl._read() if r.get("id") == eid_a][0]
        assert rec_a["source"] == "live", rec_a
        assert rec_a["venue"] == "alpaca"
        assert rec_a["source"] != "paper"
        assert ftl.open_position_count() == 1
        ftl.settle_spot_fill(
            "AAPL", "alpaca",
            {"filled_qty": 1.0, "filled_avg_price": 55.0, "id": "ap1s"},
            intended_source="live")
        tr = ftl.real_track_record()
        assert tr["n"] == 2, tr

        # 4. Alpaca paper fill is excluded even when settled as live.
        n_before = ftl.real_track_record()["n"]
        eid_p = ftl.book_spot_fill(
            "EQUITY_MOMENTUM", "MSFT", "buy", "alpaca",
            {"id": "p1", "filled_qty": 2.0, "filled_avg_price": 10.0},
            intended_source="paper")
        rec_p = [r for r in ftl._read() if r.get("id") == eid_p][0]
        assert rec_p["source"] == "paper"
        ftl.settle_open("MSFT", "alpaca", 12.0, source="live")  # launder attempt
        tr = ftl.real_track_record()
        assert tr["n"] == n_before, tr
        assert ftl.open_position_count() == 0  # paper excluded; live alpaca settled

        # 5. Sandbox / nautilus_sandbox complete fills never lift Kelly.
        n_before = ftl.real_track_record()["n"]
        ftl.book_spot_fill(
            "NT", "BTC-USD", "buy", "coinbase",
            {"filled_size": 1, "average_filled_price": 100, "filled": True},
            intended_source="nautilus_sandbox")
        ftl.settle_spot_fill(
            "BTC-USD", "coinbase",
            {"filled_size": 1, "average_filled_price": 110},
            intended_source="nautilus_sandbox")
        ftl.book_spot_fill(
            "NT", "SOL-USD", "buy", "coinbase",
            {"filled_size": 1, "average_filled_price": 100, "filled": True},
            intended_source="sandbox")
        ftl.settle_open("SOL-USD", "coinbase", 120.0, source="sandbox")
        tr = ftl.real_track_record()
        assert tr["n"] == n_before, tr

        # 6. Coinbase POST-only (the old silent-live hole) → UNRECONCILED.
        eid_b = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "SOL-USD", "buy", "coinbase",
            {"success": True, "order_id": "brain-post", "usd": 15.0},
            intended_source="live")
        rec_b = [r for r in ftl._read() if r.get("id") == eid_b][0]
        assert rec_b["source"] == "unreconciled"
        assert rec_b["fill_status"] == "UNRECONCILED"

        # 7. Incomplete exit of a live fill cannot book live P&L.
        eid_l = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "XRP-USD", "buy", "coinbase",
            {"filled_size": 10, "average_filled_price": 0.5, "order_id": "xrp1"},
            intended_source="live")
        assert ftl.open_position_count() >= 1
        n_before = ftl.real_track_record()["n"]
        ftl.settle_spot_fill(
            "XRP-USD", "coinbase",
            {"success": True, "order_id": "xrp-sell", "qty": 10},  # qty is requested, not filled
            intended_source="live", fallback_price=0.6)
        recs = ftl._read()
        sett = [r for r in recs if r.get("type") == "settlement" and r.get("entry_id") == eid_l]
        assert sett and sett[0]["source"] == "unreconciled"
        assert ftl.real_track_record()["n"] == n_before

        # 8. Dead allocator writers stay dead; brain no longer tags Alpaca live as paper.
        trader_src = open(os.path.join(HERE, "coinbase_trader.py")).read()
        alp_src = open(os.path.join(HERE, "alpaca_executor.py")).read()
        brain_src = open(os.path.join(HERE, "unified_brain.py")).read()
        dca_src = open(os.path.join(HERE, "dca_module.py")).read()
        alloc_src = open(os.path.join(HERE, "master_allocator.py")).read()
        assert "def place_market_order" not in trader_src
        assert "def place_order" not in alp_src
        assert "source=\"paper\"" not in brain_src
        assert "ledger_spot_buy" in brain_src and "book_spot_fill" in brain_src
        assert "book_spot_fill" in dca_src
        assert "book_spot_fill" in alloc_src
    finally:
        _restore(orig, path)

    print("spot-fill-ledger checks: PASS")


if __name__ == "__main__":
    run()
