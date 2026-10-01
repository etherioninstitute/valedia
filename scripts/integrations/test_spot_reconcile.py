#!/usr/bin/env python3
"""Slice 4A — UNRECONCILED GET-reconcile. Mocked payloads only. No network, no keys.

Run: python3 test_spot_reconcile.py
"""
import os
import tempfile

import forward_test_ledger as ftl
import spot_reconcile as sr

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


def _cb_get(oid="oid-get", size="0.01", px="2000", fee="0.22"):
    return {"order": {
        "order_id": oid, "product_id": "ETH-USD", "side": "BUY", "status": "FILLED",
        "filled_size": size, "average_filled_price": px, "total_fees": fee,
    }}


def run():
    src = open(os.path.join(HERE, "spot_reconcile.py")).read()
    assert "import requests" not in src
    assert "import coinbase_trader" not in src
    assert "from coinbase_trader" not in src
    assert "_request(" not in src
    assert "import nautilus_trader" not in src
    assert sr.coinbase_order_get_path("abc") == "/api/v3/brokerage/orders/historical/abc"
    assert sr.alpaca_order_get_path("xyz") == "/v2/orders/xyz"

    orig, path = _ledger()
    try:
        # 1. POST-only UNRECONCILED + mocked GET fill → fee-true live entry; stub closed.
        eid_u = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "ETH-USD", "buy", "coinbase",
            {"success": True, "order_id": "oid-get", "usd": 20.0},
            intended_source="live")
        stub = [r for r in ftl._read() if r.get("id") == eid_u][0]
        assert stub["source"] == "unreconciled"
        assert stub.get("intended_source") == "live"
        assert ftl.real_track_record()["n"] == 0
        rec = sr.reconcile_unreconciled_entry(stub, get_response=_cb_get())
        assert rec["upgraded"] is True, rec
        assert rec["action"] == "record_entry"
        assert rec["source"] == "live"
        rows = ftl._read()
        filled = [r for r in rows if r.get("id") == rec["id"]][0]
        assert filled["fill_status"] == "FILLED"
        assert filled["source"] == "live"
        assert filled["contracts"] == 0.01
        assert filled["entry_price"] == 2000.0
        assert filled["entry_fee"] > 0, filled
        assert filled["order_id"] == "oid-get"
        stub_setts = [r for r in rows if r.get("type") == "settlement" and r.get("entry_id") == eid_u]
        assert stub_setts and stub_setts[0]["source"] == "unreconciled"
        assert ftl.real_track_record()["n"] == 0  # not settled yet
        assert ftl.open_position_count() == 1

        # Live settlement of the upgraded fill lifts Kelly.
        ftl.settle_open("ETH-USD", "coinbase", 2100.0, fee=0.2, source="live")
        tr = ftl.real_track_record()
        assert tr["n"] == 1 and tr["net"] != 0.0, tr
        # Stub settlement must not have been the Kelly row.
        assert stub_setts[0].get("realized_pnl_net") == 0.0

        # 2. Incomplete GET stays UNRECONCILED. Never invents qty/price.
        eid_m = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "BTC-USD", "buy", "coinbase",
            {"success": True, "order_id": "oid-miss", "usd": 10.0},
            intended_source="live")
        stub_m = [r for r in ftl._read() if r.get("id") == eid_m][0]
        n_before = ftl.real_track_record()["n"]
        miss = sr.reconcile_unreconciled_entry(stub_m, get_response={
            "order": {"order_id": "oid-miss", "status": "OPEN", "filled_size": "0"},
        })
        assert miss["upgraded"] is False
        assert miss["reason"] == "incomplete_fill"
        still = [r for r in ftl._read() if r.get("id") == eid_m][0]
        assert still["source"] == "unreconciled"
        assert still["fill_status"] == "UNRECONCILED"
        assert ftl.real_track_record()["n"] == n_before

        # Empty payload / no GET → still UNRECONCILED (never invent).
        empty = sr.reconcile_unreconciled_entry(stub_m)
        assert empty["upgraded"] is False
        assert empty["reason"] == "incomplete_fill"

        # 3. No order_id cannot reconcile.
        eid_n = ftl.record_unreconciled("CRYPTO_MOMENTUM", "SOL-USD", "buy", 0, 0, "coinbase")
        stub_n = [r for r in ftl._read() if r.get("id") == eid_n][0]
        noid = sr.reconcile_unreconciled_entry(stub_n, get_response=_cb_get("nope"))
        assert noid["upgraded"] is False and noid["reason"] == "no_order_id"

        # 4. order_id mismatch stays UNRECONCILED.
        bad = sr.reconcile_unreconciled_entry(stub_m, get_response=_cb_get("other-id"))
        assert bad["upgraded"] is False and bad["reason"] == "order_id_mismatch"
        assert [r for r in ftl._read() if r.get("id") == eid_m][0]["source"] == "unreconciled"

        # 5. Paper intended_source + complete GET stays paper (does not lift Kelly).
        eid_p = ftl.book_spot_fill(
            "EQUITY_MOMENTUM", "AAPL", "buy", "alpaca",
            {"id": "p-oid", "status": "accepted"},
            intended_source="paper")
        stub_p = [r for r in ftl._read() if r.get("id") == eid_p][0]
        n_before = ftl.real_track_record()["n"]
        pap = sr.reconcile_unreconciled_entry(stub_p, fill_payload={
            "id": "p-oid", "status": "filled", "filled_qty": "2", "filled_avg_price": "10",
        })
        assert pap["upgraded"] is True, pap
        assert pap["source"] == "paper"
        rec_p = [r for r in ftl._read() if r.get("id") == pap["id"]][0]
        assert rec_p["source"] == "paper"
        ftl.settle_open("AAPL", "alpaca", 12.0, source="live")  # launder attempt
        assert ftl.real_track_record()["n"] == n_before

        # 6. Injected getter is used; default path never HTTP.
        called = []

        def _getter(order_id, venue):
            called.append((order_id, venue))
            return _cb_get(order_id, size="0.02", px="100", fee="0.05")

        eid_g = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "XRP-USD", "buy", "coinbase",
            {"success": True, "order_id": "oid-getter", "usd": 5.0},
            intended_source="live")
        stub_g = [r for r in ftl._read() if r.get("id") == eid_g][0]
        got = sr.reconcile_unreconciled_entry(stub_g, getter=_getter)
        assert got["upgraded"] is True, got
        assert called == [("oid-getter", "coinbase")]
        rec_g = [r for r in ftl._read() if r.get("id") == got["id"]][0]
        assert rec_g["contracts"] == 0.02 and rec_g["entry_price"] == 100.0

        # getter exception → stay UNRECONCILED
        eid_e = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "ADA-USD", "buy", "coinbase",
            {"success": True, "order_id": "oid-err", "usd": 3.0},
            intended_source="live")
        stub_e = [r for r in ftl._read() if r.get("id") == eid_e][0]

        def _boom(order_id, venue):
            raise RuntimeError("no network in tests")

        boom = sr.reconcile_unreconciled_entry(stub_e, getter=_boom)
        assert boom["upgraded"] is False
        assert "getter_error" in boom["reason"]
        assert [r for r in ftl._read() if r.get("id") == eid_e][0]["source"] == "unreconciled"

        # 7. Idempotent: second pass does not double-book.
        again = sr.reconcile_unreconciled_entry(stub_g, getter=_getter)
        assert again["upgraded"] is False and again["reason"] == "already_filled"

        # 8. Sell UNRECONCILED + GET fill settles matching live buy (fee-true).
        eid_live = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "DOGE-USD", "buy", "coinbase",
            {"filled_size": 10, "average_filled_price": 0.1, "order_id": "doge-buy"},
            intended_source="live")
        assert ftl.open_position_count() >= 1
        eid_sell_u = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "DOGE-USD", "sell", "coinbase",
            {"success": True, "order_id": "doge-sell", "qty": 10},
            intended_source="live")
        stub_s = [r for r in ftl._read() if r.get("id") == eid_sell_u][0]
        n_before = ftl.real_track_record()["n"]
        sold = sr.reconcile_unreconciled_entry(stub_s, fill_payload={
            "order_id": "doge-sell", "filled_size": 10,
            "average_filled_price": 0.12, "total_fees": 0.01,
        })
        assert sold["upgraded"] is True, sold
        assert sold["action"] == "settle"
        tr = ftl.real_track_record()
        assert tr["n"] == n_before + 1, tr
        live_setts = [r for r in ftl._read()
                      if r.get("type") == "settlement" and r.get("entry_id") == eid_live]
        assert live_setts and live_setts[0]["source"] == "live"
        stub_s_setts = [r for r in ftl._read()
                        if r.get("type") == "settlement" and r.get("entry_id") == eid_sell_u]
        assert stub_s_setts and stub_s_setts[0]["source"] == "unreconciled"

        # 9. Batch: maps by order_id; rows without a payload stay UNRECONCILED.
        eid_b1 = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "LINK-USD", "buy", "coinbase",
            {"success": True, "order_id": "batch-1", "usd": 8.0},
            intended_source="live")
        eid_b2 = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "AVAX-USD", "buy", "coinbase",
            {"success": True, "order_id": "batch-2", "usd": 8.0},
            intended_source="live")
        batch = sr.reconcile_unreconciled(get_responses={
            "batch-1": _cb_get("batch-1", size="0.5", px="10", fee="0.03"),
        })
        assert batch["upgraded"] >= 1
        b1 = [r for r in ftl._read() if r.get("id") == eid_b1][0]
        b2 = [r for r in ftl._read() if r.get("id") == eid_b2][0]
        # stub b1 closed; b2 still unreconciled
        assert b2["source"] == "unreconciled" and b2["fill_status"] == "UNRECONCILED"
        assert any(r.get("entry_id") == eid_b1 and r.get("type") == "settlement"
                   for r in ftl._read())

        # 10. Not-unreconciled row is refused.
        live_row = [r for r in ftl._read() if r.get("id") == rec["id"]][0]
        skip = sr.reconcile_unreconciled_entry(live_row, get_response=_cb_get())
        assert skip["upgraded"] is False and skip["reason"] == "not_unreconciled"
    finally:
        _restore(orig, path)

    print("spot-reconcile checks: PASS")


def _fill(oid, size, px, fee):
    return {"order_id": oid, "filled_size": str(size), "average_filled_price": str(px),
            "total_fees": str(fee)}


def _setts(order_id=None):
    return [r for r in ftl._read() if r.get("type") == "settlement" and r.get("result") == "exit"
            and (order_id is None or r.get("order_id") == order_id)]


def _close(a, b):
    return abs(a - b) < 1e-6


def run_fifo():
    """Sell reconcile closes lots FIFO for the filled qty (spot_ledger.close_sell)."""
    orig, path = _ledger()
    try:
        # A. Three lots, sell 2.5: oldest first, third lot partial, remainder stays open.
        lots = [ftl.book_spot_fill("DCA_CRYPTO", "BTC-USD", "buy", "coinbase",
                                   _fill(f"b{i}", 1, px, 0.1), ts=f"2026-09-0{i + 1}T00:00:00+00:00")
                for i, px in enumerate((10, 12, 14))]
        sid = ftl.record_unreconciled("BRAIN_CRYPTO", "BTC-USD", "sell", 0, 0, "coinbase",
                                      ts="2026-09-04T00:00:00+00:00", order_id="s-fifo",
                                      intended_source="live")
        stub = [r for r in ftl._read() if r.get("id") == sid][0]
        res = sr.reconcile_unreconciled_entry(stub, fill_payload=_fill("s-fifo", 2.5, 20, 0.25))
        assert res["upgraded"] is True and res["action"] == "settle", res
        assert res["closed"] == lots, res  # oldest first
        by = {s["entry_id"]: s for s in _setts("s-fifo")}
        assert _close(by[lots[0]]["realized_pnl_net"], 10 - 0.1 - 0.1)
        assert _close(by[lots[1]]["realized_pnl_net"], 8 - 0.1 - 0.1)
        third = by[lots[2]]
        assert third["filled_contracts"] == 0.5
        assert _close(third["realized_pnl_net"], 3 - 0.05 - 0.05)
        assert _close(res["realized"], 9.8 + 7.8 + 2.9) and res["excess"] == 0.0
        rem = [r for r in ftl._read() if r.get("id") == third["remainder_id"]][0]
        assert rem["contracts"] == 0.5 and rem["entry_price"] == 14 and _close(rem["entry_fee"], 0.05)
        assert rem["ts"] == "2026-09-03T00:00:00+00:00" and rem["source"] == "live"
        assert ftl.open_position_count() == 1
        tr = ftl.real_track_record()
        assert tr["n"] == 3 and _close(tr["net"], 20.5), tr

        # Idempotent: rerunning the same stub, the batch, or a duplicate stub for the same
        # sell order (crash between settle and stub-close) never closes another lot.
        n = len(ftl._read())
        again = sr.reconcile_unreconciled_entry(stub, fill_payload=_fill("s-fifo", 2.5, 20, 0.25))
        assert again["upgraded"] is False and again["reason"] == "already_settled", again
        assert sr.reconcile_unreconciled(fill_payloads={"s-fifo": _fill("s-fifo", 2.5, 20, 0.25)}
                                         )["upgraded"] == 0
        assert len(ftl._read()) == n
        dup = ftl.record_unreconciled("BRAIN_CRYPTO", "BTC-USD", "sell", 0, 0, "coinbase",
                                      order_id="s-fifo", intended_source="live")
        dup_row = [r for r in ftl._read() if r.get("id") == dup][0]
        d = sr.reconcile_unreconciled_entry(dup_row, fill_payload=_fill("s-fifo", 2.5, 20, 0.25))
        assert d["reason"] == "already_settled" and len(_setts("s-fifo")) == 3
        assert ftl.open_position_count() == 1 and ftl.real_track_record()["n"] == 3

        # B. Mixed: EXECUTED lot + UNRECONCILED buy stub reconciled in the same run; a lot
        # bought AFTER the sell is never closed by it.
        a = ftl.book_spot_fill("BRAIN_CRYPTO", "ETH-USD", "buy", "coinbase",
                               _fill("e-a", 1, 100, 1.0), ts="2026-09-01T00:00:00+00:00")
        ftl.book_spot_fill("BRAIN_CRYPTO", "ETH-USD", "buy", "coinbase",
                           {"success": True, "order_id": "e-b"}, ts="2026-09-02T00:00:00+00:00")
        ftl.record_unreconciled("BRAIN_CRYPTO", "ETH-USD", "sell", 0, 0, "coinbase",
                                ts="2026-09-03T00:00:00+00:00", order_id="e-s",
                                intended_source="live")
        c = ftl.book_spot_fill("BRAIN_CRYPTO", "ETH-USD", "buy", "coinbase",
                               _fill("e-c", 1, 130, 1.3), ts="2026-09-05T00:00:00+00:00")
        out = sr.reconcile_unreconciled(fill_payloads={
            "e-b": _fill("e-b", 1, 110, 1.1), "e-s": _fill("e-s", 2, 120, 2.4)})
        assert out["upgraded"] == 2, out
        sell = [r for r in out["results"] if r.get("action") == "settle"][0]
        b_up = [r for r in out["results"] if r.get("action") == "record_entry"][0]["id"]
        assert sell["closed"] == [a, b_up] and c not in sell["closed"], sell
        by = {s["entry_id"]: s for s in _setts("e-s")}
        assert _close(by[a]["realized_pnl_net"], 20 - 1.0 - 1.2)
        assert _close(by[b_up]["realized_pnl_net"], 10 - 1.1 - 1.2)
        assert by[a]["fee_status"] == "venue" and by[b_up]["source"] == "live"
        assert [r for r in ftl._read() if r.get("id") == b_up][0]["ts"] == "2026-09-02T00:00:00+00:00"
        open_ids = {r["id"] for r in ftl._read() if r.get("type") == "entry"} - {
            r.get("entry_id") for r in ftl._read() if r.get("type") == "settlement"}
        assert c in open_ids
    finally:
        _restore(orig, path)
    print("spot-reconcile FIFO checks: PASS")


if __name__ == "__main__":
    run()
    run_fifo()
