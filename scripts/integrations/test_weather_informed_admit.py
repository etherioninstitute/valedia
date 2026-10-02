#!/usr/bin/env python3
"""WEATHER-INFORMED-ADMIT-BRIDGE-1. Stubbed post. No Kalshi POST. No WEATHER_LIVE flip.

Run: python3 test_weather_informed_admit.py
"""
import os
import sys
import json
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import informed_before_create as ibc
import kalshi_auth as ka
import valedia_config as vc
# ERE v3: hermetic — valid gate state in a temp models dir (never the live one)
import ere_test_fixture
vc._MODELS = tempfile.mkdtemp(prefix="wxadmit_ere_")
ere_test_fixture.write_state(vc._MODELS)
import weather_live
import weather_morning_go as wmg
import weather_shadow as ws


DEN = {
    "ticker": "KXHIGHDEN-26SEP25-T72",
    "tag": "official",
    "fv_source": "nws_cli",
    "fv": 0.99,
    "side": "yes",
    "price": 0.075,
    "fee_true_ev": 96.47,
    "would_have_size": 106,
    "create_order": False,
    "dispatchable": 0,
    "settlement_fv": {
        "value": 60,
        "source": "nws_cli",
        "confidence": "final",
        "metric": "maximum",
        "observe_date": "2026-09-25",
    },
    "market": {"yes_bid_dollars": "0.07", "yes_ask_dollars": "0.08"},
}


def _order(row, **over):
    body = {
        "ticker": row.get("ticker"),
        "strategy": "WEATHER_ARB",
        "side": row.get("side") or "yes",
        "price": row.get("price"),
        "informed": row,
    }
    body.update(over)
    return body


def _quiet():
    os.environ["VALEDIA_TRADING_HALT_FILE"] = os.path.join(
        "/tmp", "valedia-halt-missing-weather-bridge.json")
    os.environ.pop("VALEDIA_TRADING_HALT", None)


def test_fences_unchanged():
    assert ibc.ALLOWED_FV == frozenset({"model", "rules_clock", "researched"})
    assert "nws" not in ibc.ALLOWED_FV and "nws_cli" not in ibc.ALLOWED_FV
    assert ibc.LOTTERY_PX == 0.05 and ibc.LATE_PX == 0.90
    assert vc.WEATHER_LIVE is False
    assert "OIL_BRACKET" in vc.LIVE_EXEC_FENCED
    assert "KXWTI" in vc.LIVE_EXEC_FENCED_SERIES and "KXOIL" in vc.LIVE_EXEC_FENCED_SERIES
    src = open(os.path.join(HERE, "weather_live.py")).read()
    assert "WEATHER_LIVE=" not in src
    assert "ALLOWED_FV" not in src or "not widened" in src


def test_pick_and_dry_stay_closed():
    assert wmg.admit_open(True, True, "PASS") is False
    assert wmg.admit_open(False, False, "PASS") is False
    assert wmg.admit_open(False, True, "FAIL") is False
    assert wmg.admit_open(False, True, "PASS") is True
    small = dict(DEN, ticker="KXLOWTDAL-26SEP24-B76.5", fee_true_ev=0.21, fv_source="nws")
    twc = dict(DEN, ticker="KXHIGHNY-26SEP25-T70", fv_source="weather_com", fee_true_ev=50)
    raw_conf = dict(DEN)
    raw_conf["settlement_fv"] = dict(DEN["settlement_fv"], confidence="forecast")
    chosen, skipped = wmg.pick_admit([small, DEN, twc, raw_conf])
    assert chosen["ticker"] == "KXHIGHDEN-26SEP25-T72"
    assert skipped == 1
    assert wmg.pick_admit([twc, raw_conf])[0] is None


def test_stub_admit_clears_five():
    _quiet()
    prev = vc.weather_live_enabled
    posted = []
    vc.weather_live_enabled = lambda: True
    weather_live._creates = 0
    tmp = tempfile.mkdtemp(prefix="wx-admit-")
    old_live, old_alert = ws.LIVE_LEDGER_PATH, ws.ALERT_MD
    ws.LIVE_LEDGER_PATH = os.path.join(tmp, "weather_live_ledger.jsonl")
    ws.ALERT_MD = os.path.join(tmp, "WEATHER-MORNING-ALERT.md")

    def boom(*_a, **_k):
        raise AssertionError("Kalshi POST")

    old_post = ka.create_order
    ka.create_order = boom
    try:
        raw = ibc.informed_from_opp(DEN)
        before = ibc.kalshi_reasons("KXHIGHDEN-26SEP25-T72", "yes", 1, 8, informed=raw)
        for code in ("fv_source", "fv_confidence", "edge_missing", "depth", "size"):
            assert code in before, before
        assert "live_fence" not in before
        bridged = weather_live.nws_informed(_order(DEN))
        assert bridged["fv_source"] == "rules_clock"
        assert bridged["fv_source_raw"] == "nws_cli"
        assert bridged["fv_confidence"] == "final"
        assert bridged["count"] == 1
        assert bridged["depth"] >= ibc.MIN_DEPTH
        naked = dict(bridged, fv_source="nws_cli")
        still = ibc.kalshi_reasons(
            "KXHIGHDEN-26SEP25-T72", "yes", 1, 8, informed=ibc.informed_from_opp(naked))
        assert "fv_source" in still
        ack = weather_live.admit(_order(DEN), post=lambda *a: posted.append(a))
        assert ack["create_order"] is True and ack["reasons"] == [] and len(posted) == 1
        assert posted[0][2] == 1
        line = json.loads(open(ws.LIVE_LEDGER_PATH).read().strip().splitlines()[-1])
        assert line["reasons"] == [] and line["create_order"] is True
        assert line["fv_source"] == "nws_cli" and line["fv_confidence"] == "final"
        again = weather_live.admit(_order(DEN), post=lambda *a: posted.append(a))
        assert again["create_order"] is False and again["reasons"] == ["max_creates"]
        assert len(posted) == 1
        print("BEFORE", before)
        print("AFTER", ack["reasons"], "create_order", ack["create_order"])
    finally:
        vc.weather_live_enabled = prev
        ka.create_order = old_post
        ws.LIVE_LEDGER_PATH = old_live
        ws.ALERT_MD = old_alert
        weather_live._creates = 0


def test_oil_lottery_late_and_shadow():
    _quiet()
    prev = vc.weather_live_enabled
    vc.weather_live_enabled = lambda: True
    weather_live._creates = 0
    called = []
    try:
        oil = weather_live.admit(
            {"ticker": "KXWTI-1", "strategy": "OIL_BRACKET", "side": "yes", "price": 0.40},
            post=lambda *a: called.append(1))
        assert oil["create_order"] is False and oil["reason"] == "oil_fenced" and called == []
        oil2 = weather_live.admit(
            {"ticker": "KXOIL-1", "strategy": "WEATHER_ARB", "side": "yes", "price": 0.40, "informed": DEN},
            post=lambda *a: called.append(1))
        assert oil2["reason"] == "oil_fenced" and called == []
        lot = dict(DEN, price=0.04, fee_true_ev=1.0)
        assert weather_live.nws_informed(_order(lot)) is None
        late = dict(DEN, price=0.95, fee_true_ev=1.0)
        assert weather_live.nws_informed(_order(late)) is None
        one_sided = dict(DEN)
        one_sided["market"] = {"yes_ask_dollars": "0.08"}
        assert weather_live.nws_informed(_order(one_sided)) is None
        lot_reasons = ibc.kalshi_reasons(
            DEN["ticker"], "yes", 1, 4,
            informed=ibc.informed_from_opp(dict(DEN, strategy="WEATHER_ARB", price=0.04)))
        assert "SKIP_LOTTERY" in lot_reasons
        late_reasons = ibc.kalshi_reasons(
            DEN["ticker"], "yes", 1, 95,
            informed=ibc.informed_from_opp(dict(DEN, strategy="WEATHER_ARB", price=0.95)))
        assert "SKIP_LATE_COLLAPSE" in late_reasons
    finally:
        vc.weather_live_enabled = prev
        weather_live._creates = 0
    vc.weather_live_enabled = lambda: False
    try:
        blocked = weather_live.admit(_order(DEN), post=lambda *a: called.append(1))
        assert blocked["create_order"] is False and blocked["reason"] == "WEATHER_LIVE_OFF"
        assert called == []
    finally:
        vc.weather_live_enabled = prev
    fmap = {"KXHIGHNY": {
        "series_ticker": "KXHIGHNY", "status": "ok",
        "maker_multiplier": 0.0, "taker_multiplier": 1.0, "maker_viable": True,
    }}
    scan = ws.morning_scan([{
        "ticker": "KXHIGHNY-26SEP22-T72",
        "title": "high temp >72°",
        "fv_source": "nws",
        "forecast_temp": 80,
        "price": 0.40,
        "side": "yes",
        "fixture": True,
    }], fmap=fmap, ledger_path=False, alert_path=os.path.join("/tmp", "wx-bridge-alert-skip.md"))
    assert scan["create_order"] is False and scan["rows"][0]["create_order"] is False
    assert vc.WEATHER_LIVE is False


def main():
    test_fences_unchanged()
    test_pick_and_dry_stay_closed()
    test_stub_admit_clears_five()
    test_oil_lottery_late_and_shadow()
    print("WEATHER-INFORMED-ADMIT-BRIDGE-1: PASS")


if __name__ == "__main__":
    main()
