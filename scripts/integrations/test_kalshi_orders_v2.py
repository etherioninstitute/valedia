#!/usr/bin/env python3
"""KALSHI-ORDERS-V2 — Create/Cancel Order V2 mapping. Mocked transport only.

No network, no keys, no live orders.
Run: python3 test_kalshi_orders_v2.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kalshi_auth as ka
import forward_test_ledger as ftl
import valedia_config as vc


def _v2_ack(oid="oid-1", fill="0.00", remain="5.00"):
    return {"order_id": oid, "fill_count": fill, "remaining_count": remain, "ts_ms": 1}


def test_yes_leg_mapping():
    assert ka.yes_leg_v2("buy", "yes", 56) == ("bid", 56)
    assert ka.yes_leg_v2("buy", "no", 40) == ("ask", 60)
    assert ka.yes_leg_v2("sell", "yes", 40) == ("ask", 40)
    assert ka.yes_leg_v2("sell", "no", 40) == ("bid", 60)
    assert ka.yes_leg_v2("buy", "no", 1) == ("ask", 99)
    assert ka.yes_leg_v2("buy", "no", 99) == ("ask", 1)
    try:
        ka.yes_leg_v2("buy", "yes", 0)
        raise AssertionError("cents 0 must fail")
    except ValueError:
        pass
    try:
        ka.yes_leg_v2("buy", "maybe", 50)
        raise AssertionError("bad side must fail")
    except ValueError:
        pass


def test_create_buy_yes_and_no():
    posted = []
    orig = ka.post
    ka.post = lambda ep, body=None: posted.append((ep, dict(body or {}))) or _v2_ack()
    try:
        out = ka.create_order("TICK-Y", "yes", 5, 56)
        assert posted[-1][0] == "/portfolio/events/orders"
        body = posted[-1][1]
        assert body["ticker"] == "TICK-Y"
        assert body["side"] == "bid"
        assert body["count"] == "5.00"
        assert body["price"] == "0.5600"
        assert body["time_in_force"] == "good_till_canceled"
        assert body["self_trade_prevention_type"] == "taker_at_cross"
        assert "action" not in body and "yes_price" not in body
        assert out["order_id"] == "oid-1"
        assert out["order"]["order_id"] == "oid-1"

        ka.create_order("TICK-N", "no", 2, 40)
        body = posted[-1][1]
        assert body["side"] == "ask"
        assert body["price"] == "0.6000"
        assert body["count"] == "2.00"
    finally:
        ka.post = orig


def test_sell_maps_and_reduce_only():
    posted = []
    orig = ka.post
    ka.post = lambda ep, body=None: posted.append((ep, dict(body or {}))) or _v2_ack("s1")
    try:
        ka.sell_position("TICK", "yes", 3, 40)
        body = posted[-1][1]
        assert posted[-1][0] == "/portfolio/events/orders"
        assert body["side"] == "ask" and body["price"] == "0.4000"
        assert body["reduce_only"] is True
        ka.sell_position("TICK", "no", 3, 40)
        body = posted[-1][1]
        assert body["side"] == "bid" and body["price"] == "0.6000"
        assert body["reduce_only"] is True
    finally:
        ka.post = orig


def test_cancel_v2_and_get_order():
    deleted, got = [], []
    orig_d, orig_g = ka.delete, ka.get
    ka.delete = lambda ep: deleted.append(ep) or {"order_id": "x", "reduced_by": "1.00"}
    ka.get = lambda ep: got.append(ep) or {"order": {"order_id": "x", "fill_count_fp": "0.00"}}
    try:
        ka.cancel_order("oid-1")
        assert deleted[-1] == "/portfolio/events/orders/oid-1"
        ka.cancel_order("oid-1", ticker="TICK-Y")
        assert deleted[-1].startswith("/portfolio/events/orders/oid-1?")
        assert "market_ticker=TICK-Y" in deleted[-1]
        ka.get_order("oid-1")
        assert got[-1] == "/portfolio/orders/oid-1"
    finally:
        ka.delete, ka.get = orig_d, orig_g


def test_v2_ack_is_not_a_fill():
    wrapped = ka._wrap_v2_ack(_v2_ack("v2-rest", "0.00", "5.00"))
    if not hasattr(ftl, "extract_kalshi_fill"):
        return
    ext = ftl.extract_kalshi_fill(wrapped)
    assert ext["complete"] is False and ext["order_id"] == "v2-rest", ext
    if not hasattr(ftl, "resolve_kalshi_fill"):
        return
    polled = []

    def fills():
        polled.append(1)
        return {"fills": [{"order_id": "v2-rest", "count": 5, "yes_price": 56}]}

    fill, payload = ftl.resolve_kalshi_fill(wrapped, fills)
    assert fill["complete"] is True and fill["qty"] == 5, fill
    assert polled == [1] and payload["fills"][0]["order_id"] == "v2-rest"

    # Immediate fill_count > 0 on V2 ack is a matching-engine fill; do not poll.
    polled.clear()
    immediate = ka._wrap_v2_ack(_v2_ack("v2-fill", "5.00", "0.00"))
    fill, payload = ftl.resolve_kalshi_fill(immediate, lambda: polled.append(1) or fills())
    assert fill["complete"] is True and fill["qty"] == 5 and polled == []


def test_hygiene_no_legacy_write_path():
    src = open(os.path.join(HERE, "kalshi_auth.py")).read()
    assert 'post("/portfolio/orders"' not in src
    assert 'delete(f"/portfolio/orders/' not in src
    assert "CREATE_ORDER_V2" in src
    assert "/portfolio/events/orders" in src
    lat = open(os.path.join(HERE, "latency_arb.py")).read()
    assert 'kalshi_post("/portfolio/orders"' not in lat
    assert "create_order(" in lat
    assert vc.WEATHER_LIVE is False
    assert vc.MIROFISH_AUTO_RAISE is False
    assert os.environ.get("POLYMARKET_US_LIVE") in (None, "0", "false", "")
    assert vc.VALEDIA_VERSION == "0.49.0", vc.VALEDIA_VERSION


if __name__ == "__main__":
    test_yes_leg_mapping()
    test_create_buy_yes_and_no()
    test_sell_maps_and_reduce_only()
    test_cancel_v2_and_get_order()
    test_v2_ack_is_not_a_fill()
    test_hygiene_no_legacy_write_path()
    print("KALSHI-ORDERS-V2: PASS")
