#!/usr/bin/env python3
"""Nimble System One client. Mocked HTTP. No server required.

Live health runs only when SYSTEMONE_LIVE_PROBE=1.
Run: python3 test_systemone_hotpath_client.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
os.environ.pop("VALEDIA_SYSTEMONE", None)
os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
os.environ.pop("VALEDIA_LAYA", None)
os.environ.pop("SYSTEMONE_LIVE_PROBE", None)

import systemone_hotpath_client as sc


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


def test_loopback_and_pin():
    assert sc.HOST == "127.0.0.1"
    assert sc.PORT == 11434
    assert sc.DECIDE_URL == "http://127.0.0.1:11434/v1/systemone"
    assert sc.model_pin() == "nimble"
    src = open(os.path.join(HERE, "systemone_hotpath_client.py")).read()
    assert "8766" not in src
    assert "8765" not in src
    assert "ollama pull" not in src.lower()
    assert '"0.8"' in src or "0.8" in src
    try:
        sc._post("http://127.0.0.1:11435/v1/systemone", {}, 1)
        raise AssertionError("mirofish port was accepted")
    except ValueError:
        pass
    try:
        sc._post("http://127.0.0.1:1234/v1/systemone", {}, 1)
        raise AssertionError("lms port was accepted")
    except ValueError:
        pass
    os.environ["VALEDIA_SYSTEMONE_MODEL"] = "tev1:0.8b"
    try:
        assert sc.model_pin() == ""
        refused = sc.decide(sc.compact_state(_row()), post=lambda *a: (_ for _ in ()).throw(AssertionError("posted")))
        assert refused["backend"] == "stub"
        assert refused["error"] == "model_refused"
        assert refused["create_order"] is False
    finally:
        os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
    os.environ["VALEDIA_SYSTEMONE_MODEL"] = "tev1:4b"
    try:
        assert sc.model_pin() == ""
    finally:
        os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)


def test_compact_state_has_no_secrets():
    state = sc.compact_state(_row(), {"present": False, "signals": {}})
    blob = json.dumps(state).lower()
    for word in ("api_key", "secret", "password", "bearer", "mnemonic"):
        assert word not in blob
    assert state["create_order"] is False
    assert state["class"] == "crypto"
    oil = dict(_row())
    oil["oil_fenced"] = True
    oil["class"] = "oil"
    oil["laya"] = "watch"
    assert sc.compact_state(oil)["class"] == "oil_blocked"
    weather = dict(_row())
    weather["class"] = "weather"
    weather["laya"] = "watch"
    assert sc.compact_state(weather)["class"] == "weather_watch"
    missing = dict(_row())
    missing["chg_24h"] = float("nan")
    missing["edge"] = float("nan")
    missing["impact_usd"] = float("inf")
    state = sc.compact_state(missing)
    assert state["chg_pct"] is None
    assert state["fee_true_edge"] is None
    assert state["impact_usd"] is None
    json.dumps({"state": state, "questions": sc.QUESTIONS}, allow_nan=False)


def test_decide_parses_and_fail_open():
    seen = {}

    def ok(url, payload, timeout):
        seen["url"] = url
        seen["payload"] = payload
        return {
            "model": "nimble",
            "answers": {
                "admit_paper": {"type": "noul", "noul": 0.81},
                "edge_quality": {"type": "score", "score": 2.0},
                "venue_class": {"type": "choice", "choice": "crypto"},
            },
            "usage": {"input_tokens": 40, "output_tokens": 3},
        }

    out = sc.decide(sc.compact_state(_row()), post=ok)
    assert seen["url"] == sc.DECIDE_URL
    assert seen["payload"]["model"] == "nimble"
    assert seen["payload"]["questions"]["admit_paper"]["type"] == "noul"
    assert seen["payload"]["state"]["create_order"] is False
    assert out["backend"] == "systemone_http"
    assert out["fail_open"] is False
    assert out["create_order"] is False
    assert out["enforcement"] == "none"
    assert out["mode"] == "shadow"
    assert out["judge"] == "dont_trade"
    assert out["answers"]["admit_paper"] == 0.81
    assert out["answers"]["venue_class"] == "crypto"
    assert out["answers"]["edge_quality"] == 2.0
    assert isinstance(out["latency_ms"], float)

    def down(url, payload, timeout):
        raise TimeoutError("down")

    stub = sc.decide({"symbol": "SPY"}, post=down)
    assert stub["backend"] == "stub"
    assert stub["fail_open"] is True
    assert stub["create_order"] is False
    assert stub["error"] == "TimeoutError"

    def swapped(url, payload, timeout):
        return {"model": "tev1:0.8b", "answers": {"admit_paper": {"noul": 0.9}}}

    bad = sc.decide({"symbol": "SPY"}, post=swapped)
    assert bad["backend"] == "stub"
    assert bad["error"] == "model_refused"
    assert bad["create_order"] is False


def test_summary_ignores_laya_flag():
    os.environ["VALEDIA_LAYA"] = "1"
    os.environ.pop("VALEDIA_SYSTEMONE", None)
    try:
        assert sc.enabled() is False
        off = sc.summary([{"laya_shadow": {"backend": "coreml_http"}}])
        assert off["enabled"] is False
        assert off["backend"] == "stub"
    finally:
        os.environ.pop("VALEDIA_LAYA", None)
    os.environ["VALEDIA_SYSTEMONE"] = "1"
    try:
        ranked = [{
            "systemone_shadow": {
                "backend": "systemone_http",
                "fail_open": False,
                "latency_ms": 90.0,
            }
        }]
        block = sc.summary(ranked, {"present": True, "signals": {}})
        assert block["backend"] == "systemone_http"
        assert block["fail_open"] is False
        assert block["create_order"] is False
        assert block["latency_ms_max"] == 90.0
        empty = sc.summary([{"systemone_shadow": {"backend": "stub", "fail_open": True}}])
        assert empty["backend"] == "stub"
        assert empty["fail_open"] is True
    finally:
        os.environ.pop("VALEDIA_SYSTEMONE", None)


def test_health_shape():
    def fake(url, timeout):
        if url.endswith("/api/version"):
            return {"version": "0.35.0"}
        if url.endswith("/api/tags"):
            return {"models": [{"name": "nimble:latest"}]}
        raise AssertionError(url)

    h = sc.health(get=fake)
    assert h["ok"] is True
    assert h["version"] == "0.35.0"
    assert h["model_present"] is True
    assert h["enforcement"] == "none"
    assert sc._version_ok("0.33.3") is False
    if os.environ.get("SYSTEMONE_LIVE_PROBE") == "1":
        live = sc.health()
        assert live.get("ok") is True
        assert live.get("model_present") is True


if __name__ == "__main__":
    test_loopback_and_pin()
    test_compact_state_has_no_secrets()
    test_decide_parses_and_fail_open()
    test_summary_ignores_laya_flag()
    test_health_shape()
    print("PASS test_systemone_hotpath_client")
