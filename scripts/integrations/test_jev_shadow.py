#!/usr/bin/env python3
"""AUTONOMY-2 — Jev shadow. Off = no network. On = mocked HTTP. Fail-open.

Run: python3 test_jev_shadow.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import jev_shadow as jev
from mirofish_signal_extractor import process_mirofish_report
import valedia_config as vc


SIG = {
    "ts": "2026-09-20T00:00:00+00:00",
    "status": "active",
    "overall_bias": 58.5,
    "macro_bias": 40.0,
    "source": "mirofish_swarm",
    "events": [{"type": "regulation", "direction": "RISK_ON"}],
    "consensus": {"bullish": 5, "bearish": 2, "bull_ratio": 0.71},
    "signals": {
        "BTC-USD": {"score": 62.5, "direction": "BUY", "confidence": 0.7},
        "ETH-USD": {"score": 42.0, "direction": "SELL", "confidence": 0.4},
    },
}

REPORT = {
    "outline": {"summary": "Bitcoin likely to recover from extreme fear"},
    "markdown_content": (
        "Bitcoin rally surge accumulate buying pressure near support. "
        "Institutional traders executed a BUY trade for BTC-USD."
    ),
}

OK_BODY = {
    "model": "jev-1.13.0",
    "answers": {
        "action": {
            "type": "choice",
            "choice": "annotate",
            "probabilities": {
                "annotate": 0.8, "veto": 0.1, "resize": 0.05, "stand_down": 0.05,
            },
            "confidence": 0.7,
        },
        "stand_down": {"type": "noul", "noul": 0.12},
        "report_usable": {"type": "noul", "noul": 0.91},
        "we_are_the_exit": {"type": "noul", "noul": 0.08},
    },
    "usage": {"input_tokens": 100, "output_tokens": 20},
}


def _env(name, val=None):
    prev = os.environ.get(name)
    if val is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = val
    return prev


def _restore(name, prev):
    if prev is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = prev


def _boom(*_a, **_k):
    raise AssertionError("network")


def test_hygiene_and_defaults():
    assert vc.VALEDIA_JEV is False
    assert vc.jev_enabled() is False
    assert vc.VALEDIA_VERSION == "0.74.1"
    src = open(os.path.join(HERE, "jev_shadow.py")).read()
    assert "VALEDIA_JEV = True" not in src
    assert "create_order" not in src
    assert "place_order" not in src
    assert "place_market_order" not in src
    assert "POLYMARKET_US_LIVE" not in src
    assert "KALSHI_FEE_MAP_GATE" not in src
    assert "jev-1.13.0" in src
    assert "api.typesafe.ai/v1/systemone" in src
    cfg = open(os.path.join(HERE, "valedia_config.py")).read()
    assert "VALEDIA_JEV           = False" in cfg
    assert "VALEDIA_JEV           = True" not in cfg
    alloc = open(os.path.join(HERE, "master_allocator.py")).read()
    assert "jev_shadow" not in alloc
    exe = open(os.path.join(HERE, "kalshi_executor.py")).read()
    assert "jev_shadow" not in exe
    ext = open(os.path.join(HERE, "mirofish_signal_extractor.py")).read()
    assert "jev_shadow" in ext
    snap = {r["name"]: r for r in vc.controls_snapshot()["items"]}
    assert snap["VALEDIA_JEV"]["value"] is False
    assert snap["VALEDIA_JEV"]["source"] == "const"
    assert "fail-open" in snap["VALEDIA_JEV"]["notes"]


def test_off_path_no_network():
    prev = _env("VALEDIA_JEV", None)
    try:
        assert jev.jev_on() is False
        out = jev.annotate(SIG, post=_boom)
        assert out is SIG
        assert "jev" not in out
        attached = jev.attach_to_signal(SIG, post=_boom)
        assert attached is SIG
        assert attached["overall_bias"] == 58.5
        assert attached["signals"]["BTC-USD"]["score"] == 62.5
    finally:
        _restore("VALEDIA_JEV", prev)


def test_on_path_mocked_http():
    prev_j = _env("VALEDIA_JEV", "1")
    prev_k = _env("TYPESAFE_API_KEY", "test-key")
    calls = []

    def fake(url, payload, headers, timeout):
        calls.append((url, payload, headers, timeout))
        assert url == jev.SYSTEMONE_URL
        assert payload["model"] == "jev-1.13.0"
        assert set(payload["questions"]) == {
            "action", "stand_down", "report_usable", "we_are_the_exit",
        }
        assert "markdown" not in json.dumps(payload["state"])
        assert payload["state"]["overall_bias"] == 58.5
        assert payload["state"]["n_signals"] == 2
        assert headers["Authorization"] == "Bearer test-key"
        return OK_BODY

    try:
        assert jev.jev_on() is True
        out = jev.annotate(SIG, post=fake)
        assert len(calls) == 1
        assert out is not SIG
        assert out["overall_bias"] == 58.5
        assert out["signals"]["BTC-USD"]["score"] == 62.5
        j = out["jev"]
        assert j["enabled"] is True
        assert j["fail_open"] is False
        assert j["action"] == "annotate"
        assert j["report_usable"] == 0.91
        assert j["stand_down"] == 0.12
        assert j["we_are_the_exit"] == 0.08
        assert j["model"] == "jev-1.13.0"
    finally:
        _restore("VALEDIA_JEV", prev_j)
        _restore("TYPESAFE_API_KEY", prev_k)
    assert jev.jev_on() is False


def test_fail_open_on_error():
    prev_j = _env("VALEDIA_JEV", "1")
    prev_k = _env("TYPESAFE_API_KEY", "test-key")
    try:
        out = jev.annotate(SIG, post=_boom)
        assert out["jev"]["fail_open"] is True
        assert out["jev"]["enabled"] is True
        assert out["overall_bias"] == 58.5
        assert out["signals"]["BTC-USD"]["direction"] == "BUY"

        def boom_timeout(*_a, **_k):
            raise TimeoutError("slow")

        out = jev.annotate(SIG, post=boom_timeout)
        assert out["jev"]["fail_open"] is True
        assert out["jev"]["error"] == "TimeoutError"

        def bad(*_a, **_k):
            return {"not": "answers"}

        out = jev.annotate(SIG, post=bad)
        assert out["jev"]["fail_open"] is True
    finally:
        _restore("VALEDIA_JEV", prev_j)
        _restore("TYPESAFE_API_KEY", prev_k)


def test_on_missing_key_fail_open_no_post():
    prev_j = _env("VALEDIA_JEV", "1")
    prev_a = _env("TYPESAFE_API_KEY", None)
    prev_b = _env("TYPESAFE_AI_API_KEY", None)
    # Don't load ~/.openclaw/.env: stub api_key via empty env after load.
    real_load = jev._load_openclaw_env
    jev._load_openclaw_env = lambda: None
    try:
        out = jev.annotate(SIG, post=_boom)
        assert out["jev"]["fail_open"] is True
        assert out["jev"]["error"] == "no_key"
        assert out["overall_bias"] == 58.5
    finally:
        jev._load_openclaw_env = real_load
        _restore("VALEDIA_JEV", prev_j)
        _restore("TYPESAFE_API_KEY", prev_a)
        _restore("TYPESAFE_AI_API_KEY", prev_b)


def test_extractor_off_passthrough():
    prev = _env("VALEDIA_JEV", None)
    real = jev.http_post
    jev.http_post = _boom
    try:
        md = tempfile.mkdtemp(prefix="jev_")
        outp = os.path.join(md, "mirofish_signal.json")
        doc = process_mirofish_report(REPORT, enrichment={}, out_path=outp)
        assert "jev" not in doc
        assert doc["source"] == "mirofish_swarm"
        assert "BTC-USD" in doc["signals"]
        bias = doc["overall_bias"]
        score = doc["signals"]["BTC-USD"]["score"]
        again = process_mirofish_report(REPORT, enrichment={}, out_path=outp)
        assert again["overall_bias"] == bias
        assert again["signals"]["BTC-USD"]["score"] == score
    finally:
        jev.http_post = real
        _restore("VALEDIA_JEV", prev)


def test_extractor_on_mocked():
    prev_j = _env("VALEDIA_JEV", "1")
    prev_k = _env("TYPESAFE_API_KEY", "test-key")
    real = jev.http_post

    def fake(*_a, **_k):
        return OK_BODY

    jev.http_post = fake
    try:
        md = tempfile.mkdtemp(prefix="jev_on_")
        outp = os.path.join(md, "mirofish_signal.json")
        doc = process_mirofish_report(REPORT, enrichment={}, out_path=outp)
        assert doc["jev"]["action"] == "annotate"
        assert doc["jev"]["fail_open"] is False
        saved = json.load(open(outp))
        assert saved["jev"]["report_usable"] == 0.91
        assert saved["signals"]["BTC-USD"]["score"] == doc["signals"]["BTC-USD"]["score"]
    finally:
        jev.http_post = real
        _restore("VALEDIA_JEV", prev_j)
        _restore("TYPESAFE_API_KEY", prev_k)


if __name__ == "__main__":
    test_hygiene_and_defaults()
    test_off_path_no_network()
    test_on_path_mocked_http()
    test_fail_open_on_error()
    test_on_missing_key_fail_open_no_post()
    test_extractor_off_passthrough()
    test_extractor_on_mocked()
    print("AUTONOMY-2 JEV-SHADOW: PASS")
