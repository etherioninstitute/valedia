#!/usr/bin/env python3
"""WEATHER-KALSHI-DOLLARS-1. Quote helper. No network. No orders.

Run: python3 test_kalshi_quote.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import kalshi_quote as kq
import valedia_config as vc


def test_dollars_only():
    bid, ask, last, mid = kq.quote({
        "yes_bid_dollars": "0.40",
        "yes_ask_dollars": "0.46",
        "last_price_dollars": "0.43",
    })
    assert (bid, ask, last, mid) == (0.40, 0.46, 0.43, 0.43)
    assert kq.yes_price({
        "yes_bid_dollars": "0.40",
        "yes_ask_dollars": "0.46",
        "last_price_dollars": "0.99",
    }) == 0.43


def test_cents_only():
    bid, ask, last, mid = kq.quote({
        "yes_bid": 40, "yes_ask": 46, "last_price": 42,
    })
    assert (bid, ask, last, mid) == (0.40, 0.46, 0.42, 0.43)
    bid, ask, last, mid = kq.quote({
        "yes_bid": "0.40", "yes_ask": "0.46", "last_price": "0.42",
    })
    assert (bid, ask, last, mid) == (0.40, 0.46, 0.42, 0.43)


def test_mixed_and_dollars_win():
    bid, ask, last, mid = kq.quote({
        "yes_bid_dollars": "0.40",
        "yes_ask": 90,
        "last_price": 50,
    })
    assert bid == 0.40 and ask == 0.90 and last == 0.50 and mid == 0.65
    bid, ask, last, mid = kq.quote({
        "yes_bid_dollars": "0.22",
        "yes_bid": 99,
        "yes_ask_dollars": "0.28",
        "yes_ask": 1,
        "last_price_dollars": "0.25",
        "last_price": 77,
    })
    assert (bid, ask, last, mid) == (0.22, 0.28, 0.25, 0.25)


def test_missing_and_zero():
    assert kq.quote({}) == (None, None, None, None)
    assert kq.yes_price({}) is None
    assert kq.quote({"yes_bid": None, "last_price": ""}) == (None, None, None, None)
    assert kq.quote({
        "yes_bid_dollars": "0",
        "yes_ask_dollars": "0.0000",
        "last_price_dollars": 0,
    }) == (None, None, None, None)
    assert kq.yes_price({"last_price_dollars": "0.55"}) == 0.55
    assert kq.yes_price({"yes_bid_dollars": "0.30"}) == 0.30
    assert kq.yes_price({"yes_ask_dollars": "0.70"}) == 0.70


def test_legacy_morning_go_null_on_dollars_payload():
    m = {
        "yes_bid_dollars": "0.40",
        "yes_ask_dollars": "0.46",
        "last_price_dollars": "0.43",
    }
    yes = m.get("yes_bid")
    if yes is None:
        yes = m.get("last_price")
    assert yes is None
    assert kq.yes_price(m) == 0.43


def test_wired_into_morning_go():
    src = open(os.path.join(HERE, "weather_morning_go.py")).read()
    assert "from kalshi_quote import yes_price" in src
    assert "price = yes_price(m)" in src
    assert 'yes = m.get("yes_bid")' not in src


def test_version_and_fence():
    assert vc.VALEDIA_VERSION == "0.74.1", vc.VALEDIA_VERSION
    assert "OIL_BRACKET" in vc.LIVE_EXEC_FENCED
    assert "KXWTI" in vc.LIVE_EXEC_FENCED_SERIES and "KXOIL" in vc.LIVE_EXEC_FENCED_SERIES
    informed = open(os.path.join(HERE, "informed_before_create.py")).read()
    assert "SKIP_LOTTERY" in informed and "SKIP_LATE_COLLAPSE" in informed
    shadow = open(os.path.join(HERE, "weather_shadow.py")).read()
    assert "LOTTERY_PX = 0.05" in shadow and "LATE_PX = 0.90" in shadow


def main():
    test_dollars_only()
    test_cents_only()
    test_mixed_and_dollars_win()
    test_missing_and_zero()
    test_legacy_morning_go_null_on_dollars_payload()
    test_wired_into_morning_go()
    test_version_and_fence()
    print("KALSHI-QUOTE: PASS")


if __name__ == "__main__":
    main()
