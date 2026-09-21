#!/usr/bin/env python3
"""H6 — Nautilus shadow event model + parity deepen. No live broker.

No network, no keys, no --live, no nautilus_trader, no second Kelly.
Run: python3 test_h6.py
"""
import os
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import nautilus_fill_sink as sink
import nautilus_intent as ni
import nautilus_shadow_events as ev
import nautilus_shadow_parity as sp
import valedia_config as vc

NOW = datetime(2026, 9, 18, 18, 0, tzinfo=timezone.utc)


def _restore():
    vc.NAUTILUS_ENABLED = False
    vc.NAUTILUS_LIVE = False
    vc.NAUTILUS_SHADOW_COINBASE = True
    vc.NAUTILUS_SHADOW_VENUES = True
    vc.VENUE_WRITER["coinbase"] = "valedia"
    vc.VENUE_WRITER["hyperliquid"] = "none"
    vc.VENUE_WRITER["polymarket"] = "none"
    os.environ.pop("SPOT_RECONCILE_HTTP", None)
    os.environ.pop("VALEDIA_SELF_ADJUST", None)
    vc._STAMP_CACHE = None


def _flags():
    return {
        "NAUTILUS_ENABLED": vc.NAUTILUS_ENABLED,
        "NAUTILUS_LIVE": vc.NAUTILUS_LIVE,
        "SPOT_RECONCILE_HTTP": vc.spot_reconcile_http_enabled(),
        "VENUE_WRITER": dict(vc.VENUE_WRITER),
        "MIROFISH_AUTO_RAISE": vc.MIROFISH_AUTO_RAISE,
        "VALEDIA_SELF_ADJUST": vc.VALEDIA_SELF_ADJUST,
    }


def _assert_fence(before=None):
    now = _flags()
    if before is not None:
        assert now == before, (before, now)
    assert vc.NAUTILUS_LIVE is False
    assert vc.NAUTILUS_ENABLED is False
    assert vc.SPOT_RECONCILE_HTTP is False
    assert vc.MIROFISH_AUTO_RAISE is False
    assert vc.VALEDIA_SELF_ADJUST is False
    assert vc.VENUE_WRITER["coinbase"] == "valedia"
    assert vc.nautilus_live_fills_allowed("coinbase") is False


def _rec(**over):
    d = {
        "intent_id": "h6-1",
        "client_order_id": "h6-1",
        "strategy": "H6_SHADOW",
        "venue": "coinbase",
        "instrument": "BTC-USD",
        "side": "buy",
        "order_type": "market",
        "tif": "IOC",
        "qty": 0.001,
        "max_qty": 0.001,
        "max_notional": 50.0,
        "dispatchable": False,
        "leverage": 0,
        "ledger_source": "paper",
        "ts": NOW.isoformat(),
        "enqueued_at": NOW.isoformat(),
        "gates": {
            "ere_level": "NORMAL", "fee_ok": True, "category_ok": True,
            "blacklist_ok": True, "exec_dry_run": True, "live_flag": False,
        },
    }
    d.update(over)
    return d


def _intent(**over):
    kw = dict(
        strategy="UNIFIED_BRAIN", venue="coinbase", instrument="BTC-USD",
        side="buy", order_type="market", tif="IOC", qty=0.001,
        max_notional=50.0, max_qty=0.01,
        gates={
            "ere_level": "NORMAL", "fee_ok": True, "category_ok": True,
            "blacklist_ok": True, "exec_dry_run": False, "live_flag": False,
        },
        ledger_source="paper",
    )
    kw.update(over)
    return ni.make_intent(**kw)


def test_hygiene_and_version():
    _restore()
    for name in ("nautilus_shadow_events.py", "nautilus_fill_sink.py",
                 "nautilus_shadow_parity.py"):
        src = open(os.path.join(HERE, name)).read()
        assert "import nautilus_trader" not in src
        assert "from nautilus_trader" not in src
        assert "import requests" not in src
        assert "kelly_sizing" not in src
        assert "KellySizer" not in src
        assert "calculate_fixed_risk_position_size" not in src
        assert "NAUTILUS_LIVE =" not in src
        assert "SPOT_RECONCILE_HTTP =" not in src
        assert "MIROFISH_AUTO_RAISE =" not in src
        assert "VALEDIA_SELF_ADJUST =" not in src
        assert "VENUE_WRITER[" not in src or name == "nautilus_fill_sink.py"
        assert "--live" not in src or name == "nautilus_fill_sink.py"
    src_ev = open(os.path.join(HERE, "nautilus_shadow_events.py")).read()
    assert "LiveNode" in src_ev
    assert vc.VALEDIA_VERSION == "0.50.0", vc.VALEDIA_VERSION
    assert vc.version_stamp().startswith("v0.50.0 ")
    assert "last_qty" in ev.ORDER_FILLED_MAP
    _assert_fence()


def test_states_submitted_is_not_fill():
    _restore()
    o = ev.make_order(_rec())
    assert o["state"] == "initialized"
    assert ev.is_open(o["state"]) is False
    assert ev.is_closed(o["state"]) is False
    assert ev.is_inflight(o["state"]) is False
    o = ev.apply(o, {"type": "submitted"})
    assert o["state"] == "submitted"
    assert ev.is_inflight("submitted") is True
    assert ev.is_open("submitted") is False
    assert ev.is_closed("submitted") is False
    try:
        ev.apply(o, ev.fill_event(qty=0.001, price=100.0))
        raise AssertionError("submitted must not fill")
    except ev.EventRefused as e:
        assert e.code == "submitted_not_fill"
    o = ev.apply(o, {"type": "accepted"})
    assert ev.is_open(o["state"]) is True
    o = ev.apply(o, ev.fill_event(qty=0.0004, price=100.0, fee=0.01, trade_id="t1",
                                  liquidity="taker"))
    assert o["state"] == "partially_filled"
    assert o["filled_qty"] == 0.0004
    assert o["fee"] == 0.01
    assert o["trade_ids"] == ["t1"]
    try:
        ev.apply(o, ev.fill_event(qty=0.0004, price=100.0, trade_id="t1"))
        raise AssertionError("dup trade_id")
    except ev.EventRefused as e:
        assert e.code == "dup_trade"
    o = ev.apply(o, ev.fill_event(qty=0.0006, price=101.0, trade_id="t2"))
    assert o["state"] == "filled"
    assert o["trade_ids"] == ["t1", "t2"]


def test_command_failure_ambiguous_not_terminal():
    _restore()
    o = ev.apply(ev.make_order(_rec()), {"type": "submitted"})
    o = ev.apply(o, {"type": "command_failure", "kind": "Ambiguous", "reason": "timeout"})
    assert o["state"] == "inflight_unknown"
    assert o["failure"] == "ambiguous"
    assert ev.is_inflight(o["state"]) is True
    assert ev.is_closed(o["state"]) is False
    try:
        ev.apply(o, {"type": "denied"})
        raise AssertionError("ambiguous must not invent denied")
    except ev.EventRefused as e:
        assert e.code == "ambiguous_terminal"
    o = ev.apply(o, {"type": "command_failure", "kind": "venue_rejected", "reason": "ack"})
    assert o["state"] == "rejected"
    ns = ev.apply(ev.make_order(_rec()), {"type": "command_failure", "kind": "NotSent"})
    assert ns["state"] == "denied"
    assert ns["failure"] == "not_sent"
    assert ev.is_closed("denied") is True


def test_fail_closed_side_size_venue():
    _restore()
    o = ev.apply(ev.apply(ev.make_order(_rec()), {"type": "submitted"}), {"type": "accepted"})
    try:
        ev.apply(o, ev.fill_event(qty=0.001, price=100.0, side="sell"))
        raise AssertionError("side mismatch")
    except ev.EventRefused as e:
        assert e.code == "side"
    try:
        ev.apply(o, ev.fill_event(qty=0.001, price=100.0, venue="hyperliquid"))
        raise AssertionError("venue mismatch")
    except ev.EventRefused as e:
        assert e.code == "venue"
    try:
        ev.apply(o, ev.fill_event(qty=9.0, price=100.0))
        raise AssertionError("overfill")
    except ev.EventRefused as e:
        assert e.code == "overfill"


def test_inferred_fill_not_live():
    _restore()
    booked = []

    def rec(*a, **k):
        booked.append((a, k))
        return "ft_h6"

    fe = ev.fill_event(qty=0.001, price=100.0, inferred=True, source="live")
    assert fe["source"] == "paper"
    try:
        sink.apply_fill(_intent(), {"price": 100.0, "qty": 0.001, "source": "live",
                                    "inferred": True}, recorder=rec)
        raise AssertionError("inferred live must refuse")
    except sink.FillRefused as e:
        assert e.code == "inferred_live"
    assert booked == []
    try:
        sink.apply_fill_event(_intent(), ev.fill_event(qty=0.001, price=100.0), recorder=rec)
        raise AssertionError("fill from initialized must refuse")
    except sink.FillRefused as e:
        assert e.code == "bad_transition"
    out = sink.apply_fill(
        _intent(ledger_source="paper"),
        {"price": 100.0, "qty": 0.001, "fee": 0.02, "trade_id": "tr-1", "inferred": True},
        recorder=rec,
    )
    assert out["recorded"] is True
    assert out["source"] != "live"
    assert out["inferred"] is True
    assert booked[0][1]["order_id"] == "tr-1"
    assert booked[0][1]["entry_fee"] == 0.02
    assert booked[0][1]["source"] != "live"


def test_risk_floor_deny_only():
    _restore()
    assert ev.risk_floor_deny({"ere_level": "NORMAL", "ere_ok": True}) is None
    why = ev.risk_floor_deny({"ere_level": "HALT"})
    assert why == "ere_risk_floor"
    assert not isinstance(why, (int, float))
    assert "kelly" not in why.lower()


def test_parity_timeline_and_fail_closed():
    _restore()
    before = _flags()
    clean = _rec()
    rep = sp.report(rows=[clean], window="7d", now=NOW)
    assert rep["ok"] is True, rep["dangerous"]
    assert rep["event_timelines"]
    assert rep["event_timelines"][0]["state"] == "initialized"
    assert rep["event_timelines"][0]["steps"][0]["t"] == "initialized"
    assert "submitted≠fill" in rep["note"] or "submitted" in rep["note"]

    bad_side = _rec(events=[
        {"type": "submitted"}, {"type": "accepted"},
        ev.fill_event(qty=0.001, price=100.0, side="sell", trade_id="x"),
    ])
    bad_sz = _rec(intent_id="h6-sz", events=[
        {"type": "submitted"}, {"type": "accepted"},
        ev.fill_event(qty=5.0, price=100.0, trade_id="y"),
    ])
    bad_venue = _rec(intent_id="h6-v", events=[
        {"type": "submitted"}, {"type": "accepted"},
        ev.fill_event(qty=0.001, price=100.0, venue="kalshi", trade_id="z"),
    ])
    bad_lev = _rec(intent_id="h6-lev", leverage=5)
    bad_disp = _rec(intent_id="h6-d", dispatchable=True)
    amb = _rec(intent_id="h6-amb", events=[
        {"type": "submitted"},
        {"type": "command_failure", "kind": "ambiguous"},
        {"type": "denied"},
    ])
    sub_fill = _rec(intent_id="h6-sf", events=[
        {"type": "submitted"},
        ev.fill_event(qty=0.001, price=100.0, trade_id="sf"),
    ])
    rep = sp.report(
        rows=[bad_side, bad_sz, bad_venue, bad_lev, bad_disp, amb, sub_fill],
        window="7d", now=NOW)
    assert rep["ok"] is False
    cats = {d["category"] for d in rep["dangerous"]}
    assert "side" in cats, rep["dangerous"]
    assert "size" in cats, rep["dangerous"]
    assert "venue" in cats, rep["dangerous"]
    assert "leverage" in cats, rep["dangerous"]
    assert "dispatchable" in cats, rep["dangerous"]
    assert "ambiguous_terminal" in cats, rep["dangerous"]
    _assert_fence(before)
    assert vc.NAUTILUS_LIVE is False


def test_happy_event_path_does_not_flip_flags():
    _restore()
    before = _flags()
    row = _rec(events=[
        {"type": "submitted"}, {"type": "accepted"},
        ev.fill_event(qty=0.001, price=100.0, fee=0.01, trade_id="ok1", inferred=True),
    ])
    diffs, tl = ev.inspect_record(row)
    assert diffs == [], diffs
    assert tl["state"] == "filled"
    assert [s["t"] for s in tl["steps"]] == [
        "initialized", "submitted", "accepted", "fill"]
    rep = sp.report(rows=[row], window="7d", now=NOW)
    # POST body quote_size 25 vs qty 0.001 buy-notional gap may advisory;
    # fence must not flip flags. cutover_ready stays report-only.
    _assert_fence(before)
    assert rep["flags"]["NAUTILUS_LIVE"] is False
    assert rep["cutover_ready"]["coinbase"] in (True, False)
    assert vc.VENUE_WRITER["coinbase"] != "nautilus"


if __name__ == "__main__":
    _restore()
    try:
        test_hygiene_and_version()
        test_states_submitted_is_not_fill()
        test_command_failure_ambiguous_not_terminal()
        test_fail_closed_side_size_venue()
        test_inferred_fill_not_live()
        test_risk_floor_deny_only()
        test_parity_timeline_and_fail_closed()
        test_happy_event_path_does_not_flip_flags()
    finally:
        _restore()
    print("H6 shadow event model: PASS")
