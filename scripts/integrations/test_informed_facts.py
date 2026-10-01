#!/usr/bin/env python3
"""informed_facts: freshness, agree/conflict rules, no network. Temp files only.

Run: python3 test_informed_facts.py
"""
import json
import os
import socket
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import informed_facts as inf  # noqa: E402
import systemone_hotpath_client as s1  # noqa: E402

NOW = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
TMP = tempfile.mkdtemp(prefix="inffacts_")
NOPE = os.path.join(TMP, "missing.json")


def _write(name, doc):
    path = os.path.join(TMP, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(doc, f)
    return path


def _mf(age_h=1.0, source="mirofish_swarm", bias=30.0, macro=37.5, events=None, signals=None):
    return {
        "as_of": (NOW - timedelta(hours=age_h)).isoformat(), "source": source,
        "overall_bias": bias, "macro_bias": macro,
        "events": [{"type": "geopolitics", "direction": "RISK_OFF"}] if events is None else events,
        "signals": signals if signals is not None else {
            "BTC-USD": {"score": 70.0, "direction": "BUY", "confidence": 0.6},
            "SOL-USD": {"score": 51.9, "direction": "HOLD", "confidence": 0.1},
            "ETH-USD": {"score": 30.0, "confidence": 0.4},
        },
        "report_id": "report_test",
    }


def _facts(sym, side, mf_path, **kw):
    kw.setdefault("ere_paths", [NOPE])
    kw.setdefault("prediction_log_paths", [NOPE])
    return inf.informed_facts(sym, side, mirofish_paths=[mf_path], now=NOW, **kw)


def test_mirofish_fresh_agree_conflict():
    p = _write("fresh/mirofish_signal.json", _mf())
    f = _facts("BTC", "buy", p)["facts"]
    assert f["mirofish_fresh"] and f["mirofish_has_symbol"] and f["mirofish_agrees"]
    assert not f["mirofish_conflicts"] and f["mirofish_macro_risk_off"]
    f = _facts("BTC/USD", "sell", p)["facts"]
    assert f["mirofish_conflicts"] and not f["mirofish_agrees"] and f["any_conflict"]
    # explicit HOLD stays neutral even with a score; missing direction uses the score band
    f = _facts("SOL-USD", "buy", p)["facts"]
    assert f["mirofish_has_symbol"] and not f["mirofish_agrees"] and not f["mirofish_conflicts"]
    f = _facts("ETHUSDT", "buy", p)["facts"]
    assert f["mirofish_conflicts"]
    f = _facts("DOGE", "buy", p)["facts"]
    assert f["mirofish_fresh"] and not f["mirofish_has_symbol"] and not f["mirofish_agrees"]
    # a prediction side has no underlying direction unless the caller passes one
    f = _facts("BTC", "yes", p)["facts"]
    assert not f["mirofish_agrees"] and not f["mirofish_conflicts"]
    assert _facts("BTC", "yes", p, direction="up")["facts"]["mirofish_agrees"]


def test_mirofish_stale_lightweight_neutral_archive_ignored():
    cases = {
        "stale/mirofish_signal.json": _mf(age_h=25),
        "light/mirofish_signal.json": _mf(source="intel_refresh (lightweight)"),
        "neutral/mirofish_signal.json": _mf(bias=52.0),
        "mirofish-archive/2026-10-01.json": _mf(),
    }
    for name, doc in cases.items():
        r = _facts("BTC", "buy", _write(name, doc))
        assert not any(v for k, v in r["facts"].items() if k.startswith("mirofish")), name
        assert r["sources"]["mirofish"]["used"] is False, name
    assert not _facts("BTC", "buy", NOPE)["facts"]["mirofish_fresh"]


def test_macro_rule():
    assert inf.macro_risk_off({"macro_bias": 30.0})
    assert not inf.macro_risk_off({"macro_bias": 45.0})
    assert inf.macro_risk_off({"macro_bias": 45.0, "events": [{"direction": "RISK_OFF"}]})
    assert not inf.macro_risk_off({"macro_bias": 60.0, "events": [{"direction": "RISK_OFF"}]})
    assert not inf.macro_risk_off({"macro_bias": 45.0, "events": [
        {"direction": "RISK_OFF"}, {"direction": "RISK_ON"}]})
    assert inf.macro_risk_off({"events": [{"direction": "RISK_OFF"}]})
    assert not inf.macro_risk_off({})


def test_hotpath_load_informed_uses_freshness():
    fresh = _write("h1/mirofish_signal.json", _mf(age_h=0))
    stale = _write("h2/mirofish_signal.json", _mf(age_h=30))
    light = _write("h3/mirofish_signal.json", _mf(age_h=0, source="intel_refresh (lightweight)"))
    assert s1.load_informed([stale, light, NOPE], now=NOW) == {"present": False, "signals": {}}
    got = s1.load_informed([stale, fresh], now=NOW)
    assert got["present"] is True and "BTC-USD" in got["signals"]
    assert set(got) == {"present", "signals"}


def test_news_rules():
    p = _write("n/mirofish_signal.json", _mf(age_h=30))  # stale: MiroFish silent
    buzz = {"NVDA": {"score": 2, "reasons": ["Finnhub:general"], "price": 0}}
    f = _facts("NVDA", "buy", p, news=buzz)["facts"]
    assert f["news_present"] and f["news_bullish"] and not f["news_bearish"] and f["news_agrees"]
    f = _facts("NVDA", "sell", p, news=buzz)["facts"]
    assert f["news_conflicts"] and not f["news_agrees"]
    f = _facts("NVDA", "buy", p)["facts"]
    assert not any(v for k, v in f.items() if k.startswith("news"))
    plog = _write("plog.json", {"predictions": [
        {"ts": (NOW - timedelta(hours=2)).isoformat(), "ticker": "NVDA", "direction": "SELL"},
        {"ts": (NOW - timedelta(hours=30)).isoformat(), "ticker": "AMD", "direction": "BUY"},
    ]})
    f = _facts("NVDA", "buy", p, news=buzz, prediction_log_paths=[plog])["facts"]
    assert f["news_bullish"] and f["news_bearish"]  # mixed: neither agrees nor conflicts
    assert not f["news_agrees"] and not f["news_conflicts"]
    f = _facts("NVDA", "buy", p, prediction_log_paths=[plog])["facts"]
    assert f["news_present"] and f["news_bearish"] and f["news_conflicts"]
    f = _facts("AMD", "buy", p, prediction_log_paths=[plog])["facts"]
    assert not f["news_present"], "a prediction older than a day is ignored"


def test_ere_freshness_and_fade():
    p = _write("e/mirofish_signal.json", _mf(age_h=30))
    ere = _write("ere_fresh.json", {
        "timestamp": (NOW - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M UTC"),
        "risk_level": "HIGH", "signals": {"BTC": {"signal": "FADE_SHORT", "confidence": 70}}})
    f = _facts("BTC-USD", "buy", p, ere_paths=[ere])["facts"]
    assert f["ere_fresh"] and f["ere_risk_off"] and f["ere_fade_side_conflict"] and f["any_risk_off"]
    f = _facts("BTC-USD", "sell", p, ere_paths=[ere])["facts"]
    assert not f["ere_fade_side_conflict"]
    calm = _write("ere_calm.json", {"ts": (NOW - timedelta(hours=1)).isoformat(),
                                    "risk_level": "NORMAL", "source": "intel_refresh (lightweight)"})
    f = _facts("SPY", "buy", p, ere_paths=[calm])["facts"]
    assert f["ere_fresh"] and not f["ere_risk_off"]
    old = _write("ere_old.json", {"ts": (NOW - timedelta(hours=25)).isoformat(), "risk_level": "HALT"})
    r = _facts("SPY", "buy", p, ere_paths=[old])
    assert not r["facts"]["ere_fresh"] and not r["facts"]["ere_risk_off"]
    assert r["sources"]["ere"]["used"] is False
    nots = _write("ere_nots.json", {"risk_level": "HALT"})
    assert not _facts("SPY", "buy", p, ere_paths=[nots])["facts"]["ere_fresh"]


def test_all_booleans_and_no_network():
    def boom(*a, **k):
        raise AssertionError("network call from informed_facts")
    saved = (socket.socket.connect, urllib.request.urlopen)
    socket.socket.connect = boom
    urllib.request.urlopen = boom
    try:
        p = _write("nn/mirofish_signal.json", _mf())
        r = inf.informed_facts("BTC", "buy", news={"BTC": {"score": 1}}, mirofish_paths=[p],
                               ere_paths=[NOPE], prediction_log_paths=[NOPE], now=NOW)
    finally:
        socket.socket.connect, urllib.request.urlopen = saved
    assert r["facts"] and all(isinstance(v, bool) for v in r["facts"].values())
    assert r["sources"]["intel_layer"] == "skipped_network"
    assert r["sources"]["mirofish"]["age_s"] == 3600


if __name__ == "__main__":
    test_mirofish_fresh_agree_conflict()
    test_mirofish_stale_lightweight_neutral_archive_ignored()
    test_macro_rule()
    test_hotpath_load_informed_uses_freshness()
    test_news_rules()
    test_ere_freshness_and_fade()
    test_all_booleans_and_no_network()
    print("test_informed_facts: OK")
