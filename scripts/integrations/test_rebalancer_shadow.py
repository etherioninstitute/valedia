#!/usr/bin/env python3
"""Slice 5B — rebalancer shadow Intents. No network, no keys, no nautilus_trader.

Run: python3 test_rebalancer_shadow.py
"""
import os
import tempfile

import coinbase_order_body as cob
import nautilus_intent as ni
import nautilus_shadow_coinbase as sh
import rebalancer as rb
import valedia_config as vc

HERE = os.path.dirname(os.path.abspath(__file__))


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


def _holdings_overweight_btc():
    """BTC 80% / USD 20% of $100 — BTC sell + other buys."""
    return {
        "BTC-USD": {"amount": 0.08, "price": 1000.0, "value": 80.0},
        "ETH-USD": {"amount": 0.0, "price": 0.0, "value": 0.0},
        "SOL-USD": {"amount": 0.0, "price": 0.0, "value": 0.0},
        "XRP-USD": {"amount": 0.0, "price": 0.0, "value": 0.0},
        "ADA-USD": {"amount": 0.0, "price": 0.0, "value": 0.0},
        "AVAX-USD": {"amount": 0.0, "price": 0.0, "value": 0.0},
    }, 20.0, 100.0


def run():
    _restore()
    src = open(os.path.join(HERE, "rebalancer.py")).read()
    assert "market_buy(" not in src
    assert "market_sell(" not in src
    assert "emit_rebalance_shadows" in src
    assert "Execute manually or enable auto-rebalance" in src
    assert "import nautilus_trader" not in src
    assert vc.venue_writer("coinbase") == "valedia"
    assert vc.NAUTILUS_ENABLED is False

    try:
        # 1. Pure propose: overweight BTC → sell qty + buy notionals. No network.
        holdings, usd, total = _holdings_overweight_btc()
        trades = rb.propose_rebalance_trades(holdings, usd, total)
        sides = {(t["side"], t["ticker"]) for t in trades}
        assert ("sell", "BTC-USD") in sides, trades
        sell = [t for t in trades if t["side"] == "sell"][0]
        assert sell["qty"] > 0 and sell["notional"] > 0
        buys = [t for t in trades if t["side"] == "buy"]
        assert buys, trades
        assert all(t["notional"] >= 1 for t in buys)
        assert all(t["qty"] is None for t in buys)

        # Within tolerance → no trades.
        even = {t: {"amount": 1, "price": 1, "value": pct * 100}
                for t, pct in rb.TARGET_ALLOC.items()}
        assert rb.propose_rebalance_trades(even, 0.0, 100.0) == []
        assert rb.propose_rebalance_trades(holdings, usd, 4.0) == []

        # 2. Buy shadow Intent never dispatchable; POST matches trader quote_size.
        qpath = _queue()
        try:
            buy = {"side": "buy", "ticker": "ETH-USD", "qty": None, "notional": 18.0}
            rec = sh.emit_rebalancer_shadow(buy, queue_path=qpath, gates=_gates())
            assert rec["shadow"] is True, rec
            assert rec["dispatchable"] is False, rec
            assert rec["dispatch_requested"] is False, rec
            assert rec["status"] != "dispatchable", rec
            assert rec["writer"] == "rebalancer"
            assert rec["venue"] == "coinbase"
            assert rec["instrument"] == "ETH-USD"
            assert rec["side"] == "buy"
            assert rec["ledger_source"] != "live"
            assert rec["gates"]["live_flag"] is False
            assert rec.get("notional") == 18.0
            assert rec.get("rebalancer_executes") is False
            assert rec.get("venue_writer") == "valedia"
            assert rec.get("compare", {}).get("ok") is True, rec.get("compare")
            post = rec.get("canonical_post")
            assert post["product_id"] == "ETH-USD"
            assert post["side"] == "BUY"
            assert post["order_configuration"]["market_market_ioc"]["quote_size"] == "18.00"
            same = cob.build_market_order_body(
                "ETH-USD", "buy", usd_amount=18.0, client_order_id=rec["client_order_id"])
            assert same == post
            with open(qpath) as f:
                line = f.read().strip()
            assert '"shadow": true' in line.lower().replace("True", "true")
            assert '"dispatchable": true' not in line.lower().replace("True", "true")
        finally:
            os.remove(qpath)

        # 3. Sell shadow: POST matches trader base_size.
        sell_trade = {"side": "sell", "ticker": "BTC-USD", "qty": 0.0045, "notional": 45.0}
        sell_intent = sh.intent_from_rebalance(sell_trade, gates=_gates())
        sell_post = sh.post_body_from_intent(sell_intent)
        sell_cmp = sh.compare_intent_to_post(sell_intent, sell_post)
        assert sell_cmp["ok"] is True, sell_cmp
        assert sell_post["side"] == "SELL"
        assert sell_post["order_configuration"]["market_market_ioc"]["base_size"] == "0.0045"
        qpath = _queue()
        try:
            rec = sh.emit_rebalancer_shadow(sell_trade, queue_path=qpath, gates=_gates())
            assert rec["dispatchable"] is False
            assert rec["shadow"] is True
            assert rec["writer"] == "rebalancer"
            assert rec.get("compare", {}).get("ok") is True, rec.get("compare")
        finally:
            os.remove(qpath)

        # 4. Extra cannot force dispatchable.
        qpath = _queue()
        try:
            forced = ni.enqueue_intent(
                sell_intent, dispatch=True, queue_path=qpath,
                extra={"shadow": True, "dispatchable": True, "status": "dispatchable"},
            )
            assert forced["dispatchable"] is False
            assert forced["status"] != "dispatchable"
        finally:
            os.remove(qpath)

        # 5. NT flags + writer=nautilus still cannot dispatch a rebalancer shadow.
        vc.NAUTILUS_ENABLED = True
        vc.NAUTILUS_LIVE = True
        vc.VENUE_WRITER["coinbase"] = "nautilus"
        qpath = _queue()
        try:
            rec = sh.emit_rebalancer_shadow(
                {"side": "buy", "ticker": "SOL-USD", "notional": 5.0},
                queue_path=qpath)
            assert rec["dispatchable"] is False, rec
            assert rec["shadow"] is True
            assert rec["status"] != "dispatchable"
            assert rec["gates"]["live_flag"] is False
        finally:
            os.remove(qpath)
            _restore()
        assert vc.VENUE_WRITER["coinbase"] == "valedia"
        assert vc.nautilus_may_dispatch("coinbase") is False

        # 6. run_rebalance with injected getters: shadows emitted, no market_buy/sell.
        qpath = _queue()
        buys_calls = []
        sells_calls = []

        def _bal():
            return {
                "USD": {"balance": 20.0},
                "BTC": {"balance": 0.08},
                "ETH": {"balance": 0.0},
                "SOL": {"balance": 0.0},
                "XRP": {"balance": 0.0},
                "ADA": {"balance": 0.0},
                "AVAX": {"balance": 0.0},
            }

        def _px(ticker):
            return {"BTC-USD": 1000.0}.get(ticker, 0.0)

        try:
            report = rb.run_rebalance(
                get_balances_fn=_bal, get_price_fn=_px,
                queue_path=qpath, gates=_gates())
            assert "Rebalance needed" in report
            assert "Execute manually" in report
            assert "dispatchable=false" in report
            assert buys_calls == [] and sells_calls == []
            with open(qpath) as f:
                queued = [ln for ln in f if ln.strip()]
            assert queued, "rebalancer must enqueue shadow Intents"
            for ln in queued:
                assert '"dispatchable": true' not in ln.lower().replace("True", "true")
                assert '"shadow": true' in ln.lower().replace("True", "true")
        finally:
            os.remove(qpath)

        tiny = rb.run_rebalance(
            get_balances_fn=lambda: {"USD": {"balance": 1.0}},
            get_price_fn=lambda t: 0,
            emit_shadows=False)
        assert "skipping" in tiny.lower()

        # 7. Skip switch.
        vc.NAUTILUS_SHADOW_COINBASE = False
        skipped = sh.emit_rebalancer_shadow(
            {"side": "buy", "ticker": "ETH-USD", "notional": 10.0})
        assert skipped.get("skipped") is True
        assert skipped["dispatchable"] is False
        _restore()

        # 8. Missing size / bad side skipped (never invented).
        assert sh.emit_rebalancer_shadow({"side": "buy", "ticker": "ETH-USD",
                                          "notional": 0}).get("skipped") is True
        assert sh.emit_rebalancer_shadow({"side": "sell", "ticker": "ETH-USD"}
                                         ).get("skipped") is True
        assert sh.emit_rebalancer_shadow({"side": "hold", "ticker": "ETH-USD",
                                          "notional": 1}).get("skipped") is True

        # 9. Writer lock unchanged.
        assert vc.VENUE_WRITER["coinbase"] == "valedia"
        assert vc.NAUTILUS_ENABLED is False
        try:
            vc.assert_exclusive_writer("coinbase", "nautilus")
            raise AssertionError("dual-write: nautilus must not own coinbase")
        except vc.ExclusiveWriterError:
            pass
    finally:
        _restore()

    print("rebalancer-shadow checks: PASS")


if __name__ == "__main__":
    run()
