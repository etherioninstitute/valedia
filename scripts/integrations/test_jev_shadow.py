#!/usr/bin/env python3
"""AUTONOMY-2 — Jev shadow. Off = no network. On = mocked HTTP. Fail-open.

Run: python3 test_jev_shadow.py
"""
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"
os.environ.pop("VALEDIA_JEV_BACKEND", None)
os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
LOG = os.path.join(tempfile.mkdtemp(prefix="jevlog_"), "decisions.jsonl")
os.environ["VALEDIA_SYSTEMONE_LOG"] = LOG

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


def _lines():
    if not os.path.exists(LOG):
        return []
    return [json.loads(x) for x in open(LOG).read().splitlines()]


def _reset_log():
    if os.path.exists(LOG):
        os.remove(LOG)


LOCAL_OK = {
    "model": "nimble2",
    "answers": {
        "action": {"type": "choice", "choice": "veto", "confidence": 0.55,
                   "probabilities": {"annotate": 0.2, "veto": 0.6, "resize": 0.1, "stand_down": 0.1}},
        "stand_down": {"type": "noul", "noul": 0.3},
        "report_usable": {"type": "noul", "probability": 0.8},
        "we_are_the_exit": {"type": "noul", "noul": 0.2},
        "agree": {"type": "choice", "choice": "agree", "confidence": 0.7,
                  "probabilities": {"agree": 0.7, "disagree": 0.2, "abstain": 0.1}},
        "confidence": {"type": "noul", "noul": 0.6},
        "status_honesty": {"type": "choice", "choice": "honest"},
    },
}


def test_questions_have_no_numbers_and_facts_are_code_computed():
    for qs in (jev.QUESTIONS, jev.WEATHER_QUESTIONS):
        blob = json.dumps(qs)
        assert not re.search(r"\d", blob), re.findall(r".{12}\d.{6}", blob)
        for word in ("percent", "greater", "less than", "threshold", "sum of", "count the"):
            assert word not in blob.lower(), word
    for key in ("action", "stand_down", "report_usable", "we_are_the_exit"):
        assert "facts" in jev.QUESTIONS[key]["instructions"], key
    assert "facts" in jev.WEATHER_QUESTIONS["agree"]["instructions"]
    st = jev.tiny_state(SIG)
    assert st["facts"] == {"has_signals": True, "has_events": True,
                           "directions_conflict": True, "consensus_side": "bull"}
    one = jev.tiny_state({"signals": {"A": {"direction": "BUY"}}, "consensus": {"bullish": 2, "bearish": 2}})
    assert one["facts"]["directions_conflict"] is False and one["facts"]["consensus_side"] == "split"
    empty = jev.tiny_state({})
    assert empty["facts"]["has_signals"] is False and empty["facts"]["consensus_side"] is None
    w = jev.weather_state({"fv": 0.62, "price": 0.55, "fee_true_ev": 0.03,
                           "settlement_fv": {"value": 71.0, "source": "nws"}})
    assert w["facts"] == {"settle_present": True, "fv_vs_book": "above", "fee_true_ev_positive": True}
    w = jev.weather_state({"fv": 0.4, "price": 0.55, "fee_true_ev": -0.01})
    assert w["facts"] == {"settle_present": False, "fv_vs_book": "below", "fee_true_ev_positive": False}
    assert jev.weather_state({})["facts"]["fv_vs_book"] is None


def test_default_backend_unchanged_and_logged():
    _reset_log()
    for val in (None, "", "typesafe", "cloud", "NIMBLE3"):
        prev = _env("VALEDIA_JEV_BACKEND", val)
        try:
            assert jev.backend() == "typesafe", val
        finally:
            _restore("VALEDIA_JEV_BACKEND", prev)
    prev_j = _env("VALEDIA_JEV", "1")
    prev_k = _env("TYPESAFE_API_KEY", "sk-test-secret-key")
    try:
        local_called = []
        out = jev.annotate(
            SIG, post=lambda url, payload, headers, timeout: OK_BODY,
            local_post=lambda *a: local_called.append(a))
        assert not local_called                      # default never touches loopback
        assert out["jev"]["action"] == "annotate" and out["jev"]["backend"] == "typesafe"
        jev.annotate(SIG, post=_boom)                # error path logged too
        jev.annotate(SIG, post=lambda *a: {"no": "answers"})
        lines = _lines()
        assert [ln["error"] for ln in lines] == [None, "AssertionError", "bad_response"]
        ok = lines[0]
        assert ok["source"] == "jev_shadow:annotate" and ok["backend"] == "typesafe_cloud"
        assert ok["model"] == "jev-1.13.0" and ok["response"] == OK_BODY
        assert ok["state"]["facts"]["has_signals"] is True
        assert ok["answers"]["action"] == "annotate" and ok["latency_ms"] is not None
        blob = open(LOG).read()
        assert "sk-test-secret-key" not in blob and "authorization" not in blob.lower()
        assert "bearer" not in blob.lower()
        # No key: still logged, no post.
        _env("TYPESAFE_API_KEY", None)
        real_load = jev._load_openclaw_env
        jev._load_openclaw_env = lambda: None
        try:
            jev.annotate(SIG, post=_boom)
        finally:
            jev._load_openclaw_env = real_load
        assert _lines()[-1]["error"] == "no_key" and _lines()[-1]["latency_ms"] is None
    finally:
        _restore("VALEDIA_JEV", prev_j)
        _restore("TYPESAFE_API_KEY", prev_k)
    # Off: nothing is logged.
    _reset_log()
    jev.annotate(SIG, post=_boom)
    assert _lines() == []


def test_nimble2_backend_routing_and_fail_open():
    _reset_log()
    prev_j = _env("VALEDIA_JEV", "1")
    prev_b = _env("VALEDIA_JEV_BACKEND", "nimble2")
    prev_k = _env("TYPESAFE_API_KEY", None)
    real_load = jev._load_openclaw_env
    jev._load_openclaw_env = lambda: None            # no key anywhere: local needs none
    seen = []

    def local(url, payload, timeout):
        seen.append((url, payload, timeout))
        return LOCAL_OK

    try:
        assert jev.backend() == "nimble2"
        out = jev.annotate(SIG, post=_boom, local_post=local)
        url, payload, timeout = seen[0]
        import systemone_hotpath_client as s1
        assert url == s1.DECIDE_URL and url.startswith("http://127.0.0.1:11434/")
        assert payload["model"] == "nimble2" and payload["keep_alive"] == s1.KEEP_ALIVE
        assert set(payload["questions"]) == set(jev.QUESTIONS)
        assert payload["state"]["facts"]["directions_conflict"] is True
        assert timeout == jev._timeout()             # live-adjacent path keeps the short timeout
        j = out["jev"]
        assert j["fail_open"] is False and j["backend"] == "nimble2" and j["model"] == "nimble2"
        assert j["action"] == "veto" and j["action_confidence"] == 0.55
        assert j["action_probabilities"]["veto"] == 0.6
        assert j["report_usable"] == 0.8 and j["stand_down"] == 0.3
        # Annotations never change the report.
        assert out["overall_bias"] == 58.5 and out["signals"]["BTC-USD"]["score"] == 62.5
        assert out["signals"]["BTC-USD"]["direction"] == "BUY"
        # NaN in the report is coerced, not fatal.
        nan = dict(SIG, overall_bias=float("nan"))
        out = jev.annotate(nan, local_post=local)
        assert out["jev"]["fail_open"] is False
        assert seen[-1][1]["state"]["overall_bias"] is None
        json.dumps(seen[-1][1], allow_nan=False)
        # Weather on the same backend.
        w = jev.annotate_weather({"ticker": "KXHIGHNY", "tag": "WATCH", "fv": 0.6, "price": 0.5},
                                 post=_boom, local_post=local)
        assert w["jev"]["agree"] == "agree" and w["jev"]["confidence"] == 0.7
        assert w["jev"]["oms"] is False and w["jev"]["backend"] == "nimble2"
        assert w["fv"] == 0.6 and w["price"] == 0.5
        # Fail-open: timeout, junk body, no answers.
        for bad, err in ((lambda *a: (_ for _ in ()).throw(TimeoutError("t")), "TimeoutError"),
                         (lambda *a: ["junk"], "bad_response"),
                         (lambda *a: {"model": "nimble2"}, "no_answers"),
                         (lambda *a: dict(LOCAL_OK, model="nimble"), "model_refused")):
            o = jev.annotate(SIG, post=_boom, local_post=bad)
            assert o["jev"]["fail_open"] is True and o["jev"]["error"] == err, err
            assert o["overall_bias"] == 58.5 and o["signals"]["ETH-USD"]["direction"] == "SELL"
        # Pin refusal: v1 and Tev pins are never posted.
        for pin in ("nimble", "nimble:9b", "nimble:latest", "tev1:4b", "tev1:0.8b"):
            os.environ["VALEDIA_SYSTEMONE_MODEL"] = pin
            try:
                n = len(seen)
                o = jev.annotate(SIG, local_post=local)
                assert len(seen) == n, pin
                assert o["jev"]["fail_open"] is True and o["jev"]["error"] == "model_refused", pin
            finally:
                os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
        # v2 pin override is honored.
        os.environ["VALEDIA_SYSTEMONE_MODEL"] = "nimble:9b-v2"
        try:
            jev.annotate(SIG, local_post=local)
            assert seen[-1][1]["model"] == "nimble:9b-v2"
        finally:
            os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
        # Off wins over the backend switch.
        _env("VALEDIA_JEV", None)
        assert jev.annotate(SIG, local_post=_boom) is SIG
        _env("VALEDIA_JEV", "1")
        lines = _lines()
        srcs = {ln["source"] for ln in lines}
        assert srcs == {"jev_shadow:annotate", "jev_shadow:annotate_weather"}
        assert all(ln["backend"] in ("systemone_http", "stub") for ln in lines)
        assert lines[0]["response"] == LOCAL_OK and lines[0]["error"] is None
        assert any(ln["error"] == "model_refused" and ln["model"] is None for ln in lines)
        assert any(ln["error"] == "TimeoutError" for ln in lines)
        wl = [ln for ln in lines if ln["source"] == "jev_shadow:annotate_weather"][0]
        assert wl["label"] == "WATCH" and wl["state"]["facts"]["fv_vs_book"] == "above"
    finally:
        jev._load_openclaw_env = real_load
        _restore("VALEDIA_JEV", prev_j)
        _restore("VALEDIA_JEV_BACKEND", prev_b)
        _restore("TYPESAFE_API_KEY", prev_k)


def test_nimble2_backend_via_attach_to_signal():
    prev_j = _env("VALEDIA_JEV", "1")
    prev_b = _env("VALEDIA_JEV_BACKEND", "nimble2")
    import systemone_hotpath_client as s1
    real = s1._post
    s1._post = lambda url, payload, timeout: LOCAL_OK   # fake transport, no network
    try:
        out = jev.attach_to_signal(SIG)
        assert out["jev"]["backend"] == "nimble2" and out["jev"]["action"] == "veto"
        assert out["overall_bias"] == SIG["overall_bias"]
    finally:
        s1._post = real
        _restore("VALEDIA_JEV", prev_j)
        _restore("VALEDIA_JEV_BACKEND", prev_b)


if __name__ == "__main__":
    test_questions_have_no_numbers_and_facts_are_code_computed()
    test_default_backend_unchanged_and_logged()
    test_nimble2_backend_routing_and_fail_open()
    test_nimble2_backend_via_attach_to_signal()
    test_hygiene_and_defaults()
    test_off_path_no_network()
    test_on_path_mocked_http()
    test_fail_open_on_error()
    test_on_missing_key_fail_open_no_post()
    test_extractor_off_passthrough()
    test_extractor_on_mocked()
    print("AUTONOMY-2 JEV-SHADOW: PASS")
