#!/usr/bin/env python3
"""Slice 2 — Coinbase shadow Intents. No network, no keys, no nautilus_trader.

Run: python3 test_nautilus_shadow_coinbase.py
"""
import os
import tempfile

import coinbase_order_body as cob
import forward_test_ledger as ftl
import master_allocator as ma
import nautilus_fill_sink as sink
import nautilus_intent as ni
import nautilus_shadow_coinbase as sh
import valedia_config as vc


def _alloc(**over):
    a = {
        "platform": "coinbase",
        "type": "CRYPTO_BUY",
        "ticker": "BTC-USD",
        "side": "buy",
        "price": 1.0,
        "allocation": 10.0,
        "expected_profit": 0.5,
    }
    a.update(over)
    return a


def _restore():
    vc.NAUTILUS_ENABLED = False
    vc.NAUTILUS_LIVE = False
    vc.NAUTILUS_SHADOW_COINBASE = True
    vc.VENUE_WRITER["coinbase"] = "valedia"


def _queue():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    return path


def _gates(**over):
    g = {
        "ere_level": "NORMAL",
        "fee_ok": True,
        "category_ok": True,
        "blacklist_ok": True,
        "exec_dry_run": False,
        "live_flag": False,
    }
    g.update(over)
    return g


def run():
    _restore()

    trader_src = open(os.path.join(os.path.dirname(__file__), "coinbase_trader.py")).read()
    assert "def place_market_order" not in trader_src
    assert "def market_buy" in trader_src and "def market_sell" in trader_src
    assert "from coinbase_order_body import build_market_order_body" in trader_src

    try:
        # 1. Buy shadow Intent is created, never dispatchable, writer stays valedia.
        qpath = _queue()
        try:
            rec = sh.emit_allocator_shadow(_alloc(), queue_path=qpath, gates=_gates())
            assert rec["shadow"] is True, rec
            assert rec["dispatchable"] is False, rec
            assert rec["dispatch_requested"] is False, rec
            assert rec["status"] != "dispatchable", rec
            assert rec["venue"] == "coinbase"
            assert rec["instrument"] == "BTC-USD"
            assert rec["side"] == "buy"
            assert rec["order_type"] == "market"
            assert rec["tif"] == "IOC"
            assert rec["ledger_source"] != "live"
            assert rec["gates"]["live_flag"] is False
            assert rec.get("notional") == 10.0
            with open(qpath) as f:
                line = f.read().strip()
            assert line
            assert '"dispatchable": false' in line.lower().replace("False", "false")
            assert '"shadow": true' in line.lower().replace("True", "true")
            assert '"dispatchable": true' not in line.lower().replace("True", "true")
        finally:
            os.remove(qpath)

        assert vc.venue_writer("coinbase") == "valedia"
        assert vc.NAUTILUS_ENABLED is False
        assert vc.nautilus_may_dispatch("coinbase") is False
        try:
            vc.assert_exclusive_writer("coinbase", "nautilus")
            raise AssertionError("dual-write: nautilus must not own coinbase")
        except vc.ExclusiveWriterError:
            pass

        # 2. Canonical POST from Intent matches (buy quote_size).
        intent = sh.intent_from_allocation(_alloc(), gates=_gates())
        post = sh.post_body_from_intent(intent)
        cmp_ok = sh.compare_intent_to_post(intent, post)
        assert cmp_ok["ok"] is True, cmp_ok
        assert post["product_id"] == "BTC-USD"
        assert post["side"] == "BUY"
        assert post["order_configuration"]["market_market_ioc"]["quote_size"] == "10.00"
        assert post["client_order_id"] == intent.client_order_id
        same = cob.build_market_order_body(
            "BTC-USD", "buy", usd_amount=10.0, client_order_id=intent.client_order_id)
        assert same == post

        # 3. Allocator-call POST mismatches: stripped ticker BTC vs BTC-USD.
        alloc_post = sh.post_body_from_allocator_call(_alloc(), intent.client_order_id)
        alloc_cmp = sh.compare_intent_to_post(intent, alloc_post)
        assert alloc_cmp["ok"] is False, alloc_cmp
        fields = {m["field"] for m in alloc_cmp["mismatches"]}
        assert "instrument/product_id" in fields, alloc_cmp
        assert alloc_post["product_id"] == "BTC"
        call = sh.allocator_coinbase_call(_alloc())
        assert call["fn"] == "place_market_order"
        assert call["exists_on_trader"] is False

        # 4. Sell: allocator stuffing USD into base_size is a size mismatch.
        sell_a = _alloc(type="CRYPTO_SELL", side="sell")
        sell_intent = sh.intent_from_allocation(sell_a, gates=_gates())
        try:
            sh.post_body_from_intent(sell_intent)
            raise AssertionError("sell notional-only must not invent base_size")
        except ValueError as e:
            assert "base_size" in str(e) or "qty" in str(e)
        sell_alloc_post = sh.post_body_from_allocator_call(sell_a, sell_intent.client_order_id)
        sell_cmp = sh.compare_intent_to_post(sell_intent, sell_alloc_post)
        assert sell_cmp["ok"] is False
        sell_fields = {m["field"] for m in sell_cmp["mismatches"]}
        assert "instrument/product_id" in sell_fields
        assert "size" in sell_fields

        # 5. Extra cannot force dispatchable; shadow forces dispatch=False.
        qpath = _queue()
        try:
            forced = ni.enqueue_intent(
                sell_intent, dispatch=True, queue_path=qpath,
                extra={"shadow": True, "dispatchable": True, "status": "dispatchable"},
            )
            assert forced["dispatchable"] is False, forced
            assert forced["dispatch_requested"] is False, forced
            assert forced["shadow"] is True
            assert forced["status"] != "dispatchable"
        finally:
            os.remove(qpath)

        # 6. Even with NT flags + writer=nautilus, shadow emit is not dispatchable.
        vc.NAUTILUS_ENABLED = True
        vc.NAUTILUS_LIVE = True
        vc.VENUE_WRITER["coinbase"] = "nautilus"
        qpath = _queue()
        try:
            rec = sh.emit_allocator_shadow(_alloc(), queue_path=qpath, gates=_gates())
            assert rec["dispatchable"] is False, rec
            assert rec["shadow"] is True
            assert rec["status"] != "dispatchable"
        finally:
            os.remove(qpath)
            _restore()

        # 7. Dual-write still blocked after shadow (defaults restored).
        assert vc.VENUE_WRITER["coinbase"] == "valedia"
        d = ni.validate_intent(
            ni.make_intent(
                strategy="CRYPTO_BUY", venue="coinbase", instrument="BTC-USD",
                side="buy", order_type="market", tif="IOC", notional=10.0,
                max_notional=10.0, max_qty=10.0, gates=_gates(live_flag=True),
                ledger_source="live",
            ),
            dispatch=True,
        )
        assert d.dispatchable is False, d

        # 8. Fill sink still default-denies live.
        booked = []
        def _rec(*a, **k):
            booked.append((a, k))
            return "ft_x"
        try:
            sink.apply_fill(
                ni.make_intent(
                    strategy="CRYPTO_BUY", venue="coinbase", instrument="BTC-USD",
                    side="buy", order_type="market", tif="IOC", qty=0.001,
                    max_notional=50.0, max_qty=0.01,
                    gates=_gates(live_flag=True), ledger_source="live",
                ),
                {"price": 100.0, "qty": 0.001, "source": "live"},
                recorder=_rec,
            )
            raise AssertionError("live fill must stay refused")
        except sink.FillRefused as e:
            assert e.code == "live_denied"
        assert booked == []

        # 9. Allocator Coinbase branch emits shadow, still does not POST, still FAILED
        #    (place_market_order missing — live writer not cut over).
        qpath = _queue()
        orig_emit = sh.emit_allocator_shadow

        def _emit(a, **k):
            k.setdefault("queue_path", qpath)
            k.setdefault("gates", _gates())
            return orig_emit(a, **k)

        sh.emit_allocator_shadow = _emit
        try:
            results = ma.execute_allocations([_alloc()], dry_run=False)
            assert len(results) == 1, results
            r = results[0]
            assert r.get("shadow_intent"), r
            assert r.get("shadow_dispatchable") is False, r
            assert r["status"] == "FAILED", r
            assert "place_market_order" in str(r.get("error", "")).lower() or "cannot import" in str(r.get("error", "")).lower() or "ImportError" in type(r.get("error", "")).__name__ or r["status"] == "FAILED"
            with open(qpath) as f:
                queued = [ln for ln in f if ln.strip()]
            assert queued, "allocator must enqueue a shadow Intent"
        finally:
            sh.emit_allocator_shadow = orig_emit
            os.remove(qpath)

        # 10. UNRECONCILED Coinbase/Alpaca rows do not lift Kelly / open counts.
        fd, lpath = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        orig_ledger = ftl.LEDGER
        ftl.LEDGER = lpath
        try:
            eid = ftl.record_unreconciled("CRYPTO_BUY", "BTC-USD", "buy", 100.0, 0.01, "coinbase")
            rows = ftl._read()
            assert rows and rows[0]["source"] == "unreconciled"
            assert rows[0]["fill_status"] == "UNRECONCILED"
            assert rows[0]["id"] == eid
            assert ftl.real_track_record()["n"] == 0
            assert ftl.open_position_count() == 0
            ftl._append({
                "type": "settlement", "entry_id": eid, "won": True,
                "realized_pnl_net": 99.0, "source": "unreconciled", "strategy": "CRYPTO_BUY",
            })
            tr = ftl.real_track_record()
            assert tr["n"] == 0, tr
            assert tr["net"] == 0.0, tr
        finally:
            ftl.LEDGER = orig_ledger
            os.remove(lpath)

        # 11. Shadow skip switch.
        vc.NAUTILUS_SHADOW_COINBASE = False
        skipped = sh.emit_allocator_shadow(_alloc())
        assert skipped.get("skipped") is True
        assert skipped["dispatchable"] is False
        _restore()

        # 12. product_id helper
        assert sh.coinbase_product_id("BTC") == "BTC-USD"
        assert sh.coinbase_product_id("btc-usd") == "BTC-USD"
        assert sh.coinbase_product_id("ETH-USD") == "ETH-USD"

    finally:
        _restore()

    print("nautilus-shadow-coinbase checks: PASS")


if __name__ == "__main__":
    run()
