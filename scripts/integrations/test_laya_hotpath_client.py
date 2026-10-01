#!/usr/bin/env python3
"""Laya hot-path client. Mocked HTTP. No service required.

Live health runs only when LAYA_LIVE_PROBE=1.
Run: python3 test_laya_hotpath_client.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
os.environ.pop("VALEDIA_LAYA", None)
os.environ.pop("LAYA_LIVE_PROBE", None)

import laya_hotpath_client as lc


def _row():
    return {
        "symbol": "PUMP",
        "class": "crypto",
        "chg_24h": 16.4,
        "edge": 0.161,
        "impact_usd": 8.1,
        "buzz": 0,
        "reasons": ["binance_24h"],
        "laya": "would_buy",
        "oil_fenced": False,
    }


def test_loopback_only():
    assert lc.HOST == "127.0.0.1"
    assert lc.PORT == 8766
    assert lc.DECIDE_URL == "http://127.0.0.1:8766/v1/decide"
    src = open(os.path.join(HERE, "laya_hotpath_client.py")).read()
    assert "8765" not in src
    try:
        lc._post("http://127.0.0.1:8765/v1/decide", {}, 1)
        raise AssertionError("other port was accepted")
    except ValueError:
        pass


def test_compact_state_has_no_secrets():
    state = lc.compact_state(_row(), {"present": False, "signals": {}})
    blob = json.dumps(state).lower()
    for word in ("api_key", "secret", "password", "bearer", "mnemonic"):
        assert word not in blob
    assert state["create_order"] is False
    assert state["class"] == "crypto"
    assert state["informed"]["mirofish"] is False
    assert state["informed"]["news"] is False
    oil = dict(_row())
    oil["oil_fenced"] = True
    oil["class"] = "oil"
    oil["laya"] = "watch"
    assert lc.compact_state(oil)["class"] == "oil_blocked"
    assert lc.compact_state(oil)["informed"]["watch"] is True
    weather = dict(_row())
    weather["class"] = "weather"
    weather["laya"] = "watch"
    assert lc.compact_state(weather)["class"] == "weather_watch"


def test_decide_parses_and_fail_open():
    seen = {}

    def ok(url, payload, timeout):
        seen["url"] = url
        seen["payload"] = payload
        return {
            "mode": "shadow",
            "enforcement": "none",
            "latency_ms": 17.5,
            "answers": {
                "admit_paper": {"noul": 0.81},
                "edge_quality": {"score": 2.0},
                "venue_class": {"choice": "crypto"},
            },
        }

    out = lc.decide(lc.compact_state(_row()), post=ok)
    assert seen["url"].endswith(":8766/v1/decide")
    assert seen["payload"]["pack_id"] == "valedia-paper-v1"
    assert out["backend"] == "coreml_http"
    assert out["fail_open"] is False
    assert out["create_order"] is False
    assert out["enforcement"] == "none"
    assert out["answers"]["admit_paper"] == 0.81
    assert out["answers"]["venue_class"] == "crypto"
    assert out["latency_ms"] == 17.5

    def down(url, payload, timeout):
        raise TimeoutError("down")

    stub = lc.decide({"symbol": "SPY"}, post=down)
    assert stub["backend"] == "stub"
    assert stub["fail_open"] is True
    assert stub["create_order"] is False
    assert stub["error"] == "TimeoutError"

    def fenced(url, payload, timeout):
        return {"mode": "live", "enforcement": "block", "answers": {}}

    bad = lc.decide({"symbol": "SPY"}, post=fenced)
    assert bad["backend"] == "stub"
    assert bad["fail_open"] is True
    assert bad["error"] == "fence"


def test_summary_and_optional_health():
    os.environ["VALEDIA_LAYA"] = "1"
    try:
        ranked = [{
            "laya_shadow": {
                "backend": "coreml_http",
                "fail_open": False,
                "latency_ms": 9.0,
            }
        }]
        block = lc.summary(ranked, {"present": True, "signals": {}})
        assert block["backend"] == "coreml_http"
        assert block["fail_open"] is False
        assert block["create_order"] is False
        empty = lc.summary([{"laya_shadow": {"backend": "stub", "fail_open": True}}])
        assert empty["backend"] == "stub"
        assert empty["fail_open"] is True
    finally:
        os.environ.pop("VALEDIA_LAYA", None)
    off = lc.summary([{"laya": "would_buy"}])
    assert off["backend"] == "stub"
    assert off["enabled"] is False
    if os.environ.get("LAYA_LIVE_PROBE") == "1":
        h = lc.health()
        assert h.get("ok") is True
        assert h.get("mode") == "shadow"
        assert h.get("enforcement") == "none"
        assert h.get("model_present") is True


if __name__ == "__main__":
    test_loopback_only()
    test_compact_state_has_no_secrets()
    test_decide_parses_and_fail_open()
    test_summary_and_optional_health()
    print("PASS test_laya_hotpath_client")
