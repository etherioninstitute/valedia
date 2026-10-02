#!/usr/bin/env python3
"""PHASE-2 session vs resolve vs fence. No network. No orders.

Run: python3 test_session_clocks.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import informed_before_create as ibc
import session_clocks as sc
import valedia_config as vc
from test_informed_before_create import good_kalshi, good_poly, pin_kelly, unpin_kelly

NOW = "2026-09-22T18:00:00+00:00"
OPEN = "2026-09-22T17:00:00+00:00"
CLOSE = "2026-09-22T23:00:00+00:00"
EARLY = "2026-09-22T20:30:00+00:00"


def _refused(fn):
    try:
        fn()
    except ibc.InformedCreateRefused as e:
        return e
    raise AssertionError("expected InformedCreateRefused")


def test_sports_early_close_is_not_close_time():
    market = {
        "status": "active", "open_time": OPEN, "close_time": CLOSE,
        "expected_expiration_time": EARLY, "can_close_early": True,
        "early_close_condition": "game end",
    }
    sess = sc.session_clock("kalshi_sports", ticker="KXNBAGAME-1", market=market, now=NOW)
    res = sc.resolve_clock("kalshi_sports", ticker="KXNBAGAME-1", market=market, now=NOW)
    assert sess["active"] is True and sess["reason"] == "schedule_and_active"
    assert res["known"] is True and res["kind"] == "early_close"
    assert res["ignored_field"] == "close_time"
    assert res["resolve_at"] != CLOSE
    assert "20:30:00" in res["resolve_at"]
    only = {"status": "active", "open_time": OPEN, "close_time": CLOSE}
    blind = sc.resolve_clock("KXNBAGAME-1", market=only, now=NOW)
    assert blind["known"] is False and blind["reason"] == "close_time_alone"
    outside = sc.session_clock("kalshi_sports", market=market, now="2026-09-22T23:30:00+00:00")
    assert outside["active"] is False
    qe, old = pin_kelly()
    try:
        import kalshi_fee_map as kfm
        fmap = {"KXNBAGAME": kfm._row("KXNBAGAME", 0.0, 0.0, "sports")}
        err = _refused(lambda: ibc.require_kalshi(
            "KXNBAGAME-1", "yes", 1, 50, informed=good_kalshi(
                series="KXNBAGAME", fmap=fmap, sports_attestation=True,
                now=NOW, market=only)))
        assert "resolve_unknown" in err.reasons
        err = _refused(lambda: ibc.require_kalshi(
            "KXNBAGAME-1", "yes", 1, 50, informed=good_kalshi(
                series="KXNBAGAME", fmap=fmap, sports_attestation=True,
                now="2026-09-22T23:30:00+00:00", market=market)))
        assert "session_inactive" in err.reasons
    finally:
        unpin_kelly(qe, old)


def test_15m_window_and_next_ticker():
    cur = {
        "ticker": "KXBTC15M-A", "status": "active",
        "open_time": "2026-09-22T17:45:00+00:00",
        "close_time": "2026-09-22T18:00:00+00:00",
    }
    nxt = {
        "ticker": "KXBTC15M-B", "status": "open",
        "open_time": "2026-09-22T18:00:00+00:00",
        "close_time": "2026-09-22T18:15:00+00:00",
    }
    inside = "2026-09-22T17:50:00+00:00"
    sess = sc.session_clock("kalshi_15m", ticker="KXBTC15M-A", market=cur, now=inside)
    res = sc.resolve_clock("KXBTC15M-A", market=cur, now=inside)
    assert sess["active"] is True and sess["enter_rule"] == "window_open_close"
    assert res["known"] is True and res["kind"] == "window_close"
    assert "18:00:00" in res["resolve_at"]
    closed = sc.session_clock("KXETH15M-A", market=cur, now=NOW)
    assert closed["active"] is False and closed["venue_class"] == "kalshi_15m"
    poll = sc.next_window("KXBTC15M", [cur, nxt], now=inside)
    assert poll["next_ticker"] == "KXBTC15M-B"
    assert sc.next_window("KXGOLD15M", [cur], now=inside)["next_ticker"] is None
    qe, old = pin_kelly()
    try:
        import kalshi_fee_map as kfm
        fmap = {"KXBTC15M": kfm._row("KXBTC15M", 0.0, 0.0, "15m")}
        err = _refused(lambda: ibc.require_kalshi(
            "KXBTC15M-A", "yes", 1, 50, informed=good_kalshi(
                series="KXBTC15M", fmap=fmap, now=NOW, market=cur)))
        assert "session_inactive" in err.reasons
    finally:
        unpin_kelly(qe, old)


def test_poly_uma_is_not_end_date():
    end = "2026-12-31T00:00:00+00:00"
    bare = {"acceptingOrders": True, "endDate": end}
    res = sc.resolve_clock("poly_us", market=bare, now=NOW)
    assert res["known"] is False and res["ignored_field"] == "endDate"
    assert res["reason"] == "endDate_not_resolve"
    uma = dict(bare)
    uma["umaProposalTime"] = "2026-09-22T18:00:00+00:00"
    uma["challengePeriodSeconds"] = 7200
    got = sc.resolve_clock("poly_us", market=uma, now=NOW)
    assert got["known"] is True and got["kind"] == "uma_challenge"
    assert "20:00:00" in got["resolve_at"]
    assert got["resolve_at"] != end
    early = {"gameStartTime": "2026-09-22T17:00:00+00:00", "orderDelaySeconds": 120,
             "acceptingOrders": False}
    assert sc.session_clock("poly_us", market=early, now="2026-09-22T17:01:00+00:00")["active"] is False
    assert sc.session_clock("poly_us", market=early, now="2026-09-22T17:03:00+00:00")["active"] is True
    qe, old = pin_kelly()
    try:
        err = _refused(lambda: ibc.require_poly(good_poly(
            acceptingOrders=True, endDate=end, umaProposalTime=None,
            challengePeriodSeconds=None)))
        assert "resolve_unknown" in err.reasons
        assert ibc.poly_reasons(good_poly()) == []
    finally:
        unpin_kelly(qe, old)


def test_fence_stays_and_allocator_consults():
    assert vc.weather_live_enabled() is False
    assert "WEATHER_ARB" in vc.LIVE_EXEC_FENCED and "OIL_BRACKET" in vc.LIVE_EXEC_FENCED
    oil = sc.fence_gate("oil", ticker="KXWTI-1")
    wx = sc.fence_gate("WEATHER_ARB", strategy="WEATHER_ARB")
    assert oil["fenced"] is True and oil["live_exec"] is False
    assert oil["mode"] == "RESEARCH/SHADOW" and oil["dispatchable"] == 0
    assert wx["reason"] == "LIVE_EXEC_FENCED"
    assert sc.before_create({"ticker": "KXWTI-1", "type": "NO_HARVEST"})[0] == "SKIP_LIVE_FENCE"
    assert sc.before_create({"ticker": "KXBTCY-1", "type": "MODEL"}) is None
    sports = {"ticker": "KXNBAGAME-1", "type": "SPORTS", "market": {
        "status": "active", "open_time": OPEN, "close_time": CLOSE}}
    assert sc.before_create(sports, now=NOW)[0] == "SKIP_RESOLVE"
    legs = sc.leg_clocks("latency_arb", [
        {"ticker": "KXBTC15M-A", "status": "active", "open_time": OPEN, "close_time": CLOSE},
        {"ticker": "KXNBAGAME-1"},
    ], now=NOW)
    assert len(legs) == 2 and legs[0]["session"]["venue_class"] == "kalshi_15m"
    assert sc.fence_gate("mve")["reason"] == "no_live_rfq"
    assert sc.before_create({"type": "LATENCY_ARB", "ticker": "KXBTCY-1"})[0] == "SKIP_CLOCK_STUB"
    src = open(os.path.join(HERE, "master_allocator.py")).read()
    assert "before_create(a)" in src
    snap = {r["name"]: r for r in vc.controls_snapshot()["items"]}
    for name in ("CLOCK_KALSHI_SPORTS", "CLOCK_KALSHI_15M", "CLOCK_POLY_US",
                 "CLOCK_OIL_WEATHER", "CLOCK_ARB_MVE"):
        assert snap[name]["source"] == "const", name
    assert "LIVE_EXEC_FENCED" in snap["CLOCK_OIL_WEATHER"]["value"]
    assert "endDate" in snap["CLOCK_POLY_US"]["notes"]
    assert "close_time" in snap["CLOCK_KALSHI_SPORTS"]["notes"]
    assert vc.POLYMARKET_US_LIVE is False
    assert vc.VENUE_WRITER["polymarket"] == "valedia"


if __name__ == "__main__":
    test_sports_early_close_is_not_close_time()
    test_15m_window_and_next_ticker()
    test_poly_uma_is_not_end_date()
    test_fence_stays_and_allocator_consults()
    print("SESSION-RESOLVE-CLOCKS: PASS")
