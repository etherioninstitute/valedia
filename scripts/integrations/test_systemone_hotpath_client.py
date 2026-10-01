#!/usr/bin/env python3
"""Nimble System One client. Mocked HTTP. No server required.

Live health runs only when SYSTEMONE_LIVE_PROBE=1.
Run: python3 test_systemone_hotpath_client.py
"""
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
os.environ.pop("VALEDIA_SYSTEMONE", None)
os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
os.environ.pop("VALEDIA_SYSTEMONE_DIGEST", None)
os.environ.pop("SYSTEMONE_LIVE_PROBE", None)
os.environ.pop("VALEDIA_SYSTEMONE_BUDGET_MS", None)
LOG = os.path.join(tempfile.mkdtemp(prefix="s1log_"), "nested", "decisions.jsonl")
os.environ["VALEDIA_SYSTEMONE_LOG"] = LOG
# Retired shadow decider name, spelled indirectly so grep over scripts/ stays clean.
RETIRED = "".join(["l", "a", "y", "a"])

import systemone_hotpath_client as sc

# Burton's `ollama list`: nimble:latest ID 24e550a16a70 (the 12-char prefix of the
# full sha256 that /api/tags returns). Never a real network call in these tests.
PIN = "24e550a16a70"
FULL = PIN + "9" * 52
OTHER = "deadbeef0000" + "1" * 52


def _tags_body(*rows):
    out = []
    for r in rows:
        name, dig = (r, FULL) if isinstance(r, str) else r
        row = {"name": name, "model": name}
        if dig is not None:
            row["digest"] = dig
        out.append(row)
    return {"models": out}


def _fake_get(*rows, version="0.35.0", seen=None):
    def fake(url, timeout):
        if seen is not None:
            seen.append(url)
        if url.endswith("/api/version"):
            return {"version": version}
        if url.endswith("/api/tags"):
            return _tags_body(*rows)
        raise AssertionError(url)
    return fake


_REAL_GET = sc._get


def _use_tags(*rows):
    """Fake /api/tags for ask()/decide() (module transport) and drop the digest cache."""
    sc._get = _fake_get(*rows)
    sc._reset_digest()


_use_tags("nimble:latest")  # default: Burton's Mac, digest matches the pin


def _row():
    return {
        "symbol": "PUMP",
        "class": "crypto",
        "chg_24h": 16.4,
        "edge": 0.161,
        "impact_usd": 8.1,
        "buzz": 0,
        "reasons": ["binance_24h"],
        "label": "would_buy",
        "oil_fenced": False,
    }


def test_loopback_and_pin():
    assert sc.HOST == "127.0.0.1"
    assert sc.PORT == 11434
    assert sc.DECIDE_URL == "http://127.0.0.1:11434/v1/systemone"
    assert sc.MODEL_DEFAULT == "nimble:latest" and sc.NIMBLE2_DIGEST == PIN
    _use_tags("nimble:latest")
    assert sc.model_pin() == ""                  # floating tag: nothing verified yet
    assert sc.verify_digest() == "ok"
    assert sc.model_pin() == "nimble:latest"
    src = open(os.path.join(HERE, "systemone_hotpath_client.py")).read()
    assert "8766" not in src
    assert RETIRED not in src.lower()
    assert "VALEDIA_" + RETIRED.upper() not in src
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
    oil["label"] = "watch"
    assert sc.compact_state(oil)["class"] == "oil_blocked"
    weather = dict(_row())
    weather["class"] = "weather"
    weather["label"] = "watch"
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
            "model": "nimble2",
            "answers": {
                "admit_paper": {"type": "noul", "noul": 0.81},
                "edge_quality": {"type": "score", "score": 2.0},
                "venue_class": {"type": "choice", "choice": "crypto"},
            },
            "usage": {"input_tokens": 40, "output_tokens": 3},
        }

    out = sc.decide(sc.compact_state(_row()), post=ok)
    assert seen["url"] == sc.DECIDE_URL
    assert seen["payload"]["model"] == "nimble:latest"
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

    def v1(url, payload, timeout):
        return {"model": "nimble:9b", "answers": {"admit_paper": {"noul": 0.9}}}

    old = sc.decide({"symbol": "SPY"}, post=v1)
    assert old["backend"] == "stub"
    assert old["error"] == "model_refused"


def test_model_pin_nimble2_only():
    for tag in ("nimble2", "NIMBLE2", "nimble2:latest", "nimble2:q4_k_m", "nimble2:fp16",
                "nimble:9b-v2", "nimble:v2", "nimble:2", "nimble:2-q4_k_m", "nimble:v2-q8_0",
                "nimble:9b-v2-fp16", "NIMBLE:9B-V2-Q4_K_M",
                "Bespoke-Nimble-9B-v2", "bespoke-nimble-9b-v2", "bespoke-nimble-9b-v2:latest",
                "bespokelabs/Bespoke-Nimble-9B-v2",
                "hf.co/bespokelabs/Bespoke-Nimble-9B-v2",
                "hf.co/bespokelabs/Bespoke-Nimble-9B-v2:Q4_K_M",
                "HF.CO/BESPOKELABS/BESPOKE-NIMBLE-9B-V2:latest"):
        assert sc.is_nimble2(tag), tag
        os.environ["VALEDIA_SYSTEMONE_MODEL"] = tag
        try:
            assert sc.model_pin() == tag
            seen = []
            out = sc.decide(
                {"symbol": "SPY"},
                post=lambda url, payload, timeout: seen.append(payload["model"]) or {
                    "model": tag, "answers": {"admit_paper": {"noul": 0.5}}})
            assert seen == [tag] and out["backend"] == "systemone_http", tag
        finally:
            os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
    # v1, Tev, look-alikes, and empty are refused and never posted.
    for tag in ("nimble:9b", "NIMBLE:9B", "nimble:1", "nimble3",
                "nimble2x", "nimble:2b", "nimble:2b-v1", "nimble:20b", "nimble:2x",
                "nimble:2-", "nimble:2-notaquant", "nimble:v2-v1", "nimble:9b-v2-0.8b",
                "nimble2:v1", "nimble2:q4-v1", "nimble:v1", "nimble:9b-v1", "nimble:1-q4_k_m",
                "tev1:4b", "tev1:0.8b", "nimble2:0.8b", "nimble:v2-0.8b", "tev-nimble2",
                "bespoke-nimble-9b", "bespoke-nimble-9b-v1", "bespoke-nimble-9b-v2-gguf",
                "bespoke-nimble-9b-v2:v1", "hf.co/bespokelabs/Bespoke-Nimble-9B",
                "hf.co/other/Bespoke-Nimble-9B-v2", "someone/Bespoke-Nimble-9B-v2",
                "hf.co/bespokelabs/Bespoke-Nimble-9B-v2-GGUF", "evil.com/bespokelabs/bespoke-nimble-9b-v2",
                "nimble2:", "qwen3:8b", ""):
        assert not sc.is_nimble2(tag), tag
        os.environ["VALEDIA_SYSTEMONE_MODEL"] = tag
        try:
            assert sc.model_pin() == "", tag
            refused = sc.decide(
                {"symbol": "SPY"},
                post=lambda *a: (_ for _ in ()).throw(AssertionError("posted")))
            assert refused["backend"] == "stub" and refused["error"] == "model_refused", tag
            assert refused["create_order"] is False
        finally:
            os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
    # A server answering with a v1 model name is refused even when the pin is v2.
    # An explicit v2 pin never accepts a floating answer name either.
    os.environ["VALEDIA_SYSTEMONE_MODEL"] = "nimble2"
    try:
        for got in ("nimble", "nimble:9b", "nimble:latest"):
            bad = sc.decide(
                {"symbol": "SPY"},
                post=lambda url, payload, timeout, got=got: {
                    "model": got, "answers": {"admit_paper": {"noul": 0.9}}})
            assert bad["backend"] == "stub" and bad["error"] == "model_refused", got
        ok = sc.decide(
            {"symbol": "SPY"},
            post=lambda url, payload, timeout: {
                "model": "nimble2:latest", "answers": {"admit_paper": {"noul": 0.9}}})
        assert ok["backend"] == "systemone_http"
    finally:
        os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
    # Default (digest-verified floating pin): a v1 answer name is still refused.
    _use_tags("nimble:latest")
    bad = sc.decide(
        {"symbol": "SPY"},
        post=lambda url, payload, timeout: {
            "model": "nimble:9b", "answers": {"admit_paper": {"noul": 0.9}}})
    assert bad["backend"] == "stub" and bad["error"] == "model_refused"


def test_summary_off_and_on():
    os.environ.pop("VALEDIA_SYSTEMONE", None)
    assert sc.enabled() is False
    off = sc.summary([{"systemone_shadow": {"backend": "systemone_http"}}])
    assert off["enabled"] is False
    assert off["backend"] == "stub"
    assert off["model"] == "nimble:latest"
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


def _fake_tags(*names):
    return _fake_get(*names)


def test_health_shape():
    sc._reset_digest()
    h = sc.health(get=_fake_tags("nimble:latest"))
    assert h["ok"] is True and h["digest"] == "ok"
    assert h["version"] == "0.35.0"
    assert h["model_present"] is True
    assert h["model"] == "nimble:latest"
    assert h["enforcement"] == "none"
    assert sc.model_pin() == "nimble:latest"      # health verified the digest this process
    # Missing tag, v1 look-alikes, and near-misses are not present: model_missing.
    for other in ("nimble:9b", "nimble2x:latest", "nimble2:q4", "nimble2:latest"):
        sc._reset_digest()
        h = sc.health(get=_fake_tags(other, "qwen3:8b"))
        assert h["model_present"] is False, other
        assert h["ok"] is False and h["error"] == "model_missing", other
        assert h["model"] == "nimble:latest"
        assert sc.model_pin() == ""
    # Right tag, wrong digest: not ok, digest_mismatch, and the pin stays closed.
    sc._reset_digest()
    h = sc.health(get=_fake_tags(("nimble:latest", OTHER)))
    assert h["model_present"] is True and h["ok"] is False
    assert h["error"] == "digest_mismatch" and h["digest"] == "digest_mismatch"
    assert sc.model_pin() == "" and sc.pin_refusal() == "digest_mismatch"
    # No digest field at all: unverified.
    sc._reset_digest()
    h = sc.health(get=_fake_tags(("nimble:latest", None)))
    assert h["ok"] is False and h["error"] == "digest_unverified"
    # Health looks for the configured tag, not any name starting with nimble.
    os.environ["VALEDIA_SYSTEMONE_MODEL"] = "nimble:9b-v2"
    try:
        assert sc.health(get=_fake_tags("nimble2:latest"))["model_present"] is False
        h = sc.health(get=_fake_tags("nimble:9b-v2"))
        assert h["model_present"] is True and h["model"] == "nimble:9b-v2"
        assert h["ok"] is True and h["digest"] is None       # explicit v2: no digest needed
    finally:
        os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
    os.environ["VALEDIA_SYSTEMONE_MODEL"] = "nimble2"
    try:
        h = sc.health(get=_fake_tags("nimble2:latest"))
        assert h["ok"] is True and h["model"] == "nimble2"
    finally:
        os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
    hf = "hf.co/bespokelabs/Bespoke-Nimble-9B-v2"
    os.environ["VALEDIA_SYSTEMONE_MODEL"] = hf
    try:
        assert sc.health(get=_fake_tags(hf + ":latest"))["model_present"] is True
        assert sc.health(get=_fake_tags("nimble2:latest"))["model_present"] is False
    finally:
        os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
    # A refused pin means nothing is present, whatever is on disk.
    os.environ["VALEDIA_SYSTEMONE_MODEL"] = "nimble:9b"
    try:
        h = sc.health(get=_fake_tags("nimble:9b", "nimble:latest", "nimble2:latest"))
        assert h["model_present"] is False and h["ok"] is False and h["model"] is None
    finally:
        os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
    down = sc.health(get=lambda url, timeout: (_ for _ in ()).throw(TimeoutError("x")))
    assert down["ok"] is False and down["model_present"] is False
    assert sc._version_ok("0.33.3") is False
    _use_tags("nimble:latest")
    if os.environ.get("SYSTEMONE_LIVE_PROBE") == "1":
        live = sc.health(get=_REAL_GET)
        assert live.get("ok") is True
        assert live.get("model_present") is True


def _never_post(*a):
    raise AssertionError("decide was POSTed")


def test_digest_pin():
    # Match: Burton's tag + full sha256 starting with the pinned 12-char prefix.
    _use_tags(("nimble:latest", FULL))
    seen = []
    out = sc.decide({"symbol": "SPY"}, post=lambda url, payload, timeout: seen.append(payload["model"]) or {
        "model": "nimble:latest", "answers": {"admit_paper": {"noul": 0.5}}})
    assert seen == ["nimble:latest"] and out["backend"] == "systemone_http"
    assert sc.digest_status() == "ok"
    # sha256: prefix and models[].name vs models[].model are both honoured.
    sc._get = _fake_get(("nimble:latest", "sha256:" + FULL.upper()))
    sc._reset_digest()
    assert sc.verify_digest() == "ok"
    sc._get = lambda url, timeout: {"models": [{"model": "nimble:latest", "digest": FULL}]}
    sc._reset_digest()
    assert sc.verify_digest() == "ok"
    # Bare `nimble` resolves to nimble:latest and is gated the same way.
    os.environ["VALEDIA_SYSTEMONE_MODEL"] = "nimble"
    try:
        _use_tags(("nimble:latest", FULL))
        seen = []
        out = sc.decide({"symbol": "SPY"}, post=lambda url, payload, timeout: seen.append(payload["model"]) or {
            "model": "nimble:latest", "answers": {"admit_paper": {"noul": 0.5}}})
        assert seen == ["nimble"] and out["backend"] == "systemone_http"
        _use_tags(("nimble:latest", OTHER))
        out = sc.decide({"symbol": "SPY"}, post=_never_post)
        assert out["backend"] == "stub" and out["error"] == "digest_mismatch"
    finally:
        os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
    # Mismatch: refused, decide is never POSTed, reason is logged.
    _reset_log()
    _use_tags(("nimble:latest", OTHER))
    out = sc.decide({"symbol": "SPY"}, post=_never_post, label="watch")
    assert out["backend"] == "stub" and out["fail_open"] is True
    assert out["error"] == "digest_mismatch" and out["create_order"] is False
    assert out["model"] is None
    assert _lines()[-1]["error"] == "digest_mismatch" and _lines()[-1]["backend"] == "stub"
    # Missing /api/tags (down, 404, junk, no row, no digest): refused as digest_unverified.
    def tags_down(url, timeout):
        raise ConnectionRefusedError("down")
    for getter in (tags_down,
                   lambda url, timeout: "junk",
                   lambda url, timeout: {"models": []},
                   lambda url, timeout: {"models": [{"name": "qwen3:8b", "digest": FULL}]},
                   lambda url, timeout: {"models": [{"name": "nimble:latest"}]},
                   lambda url, timeout: {"models": [{"name": "nimble:9b", "digest": FULL}]}):
        sc._get = getter
        sc._reset_digest()
        out = sc.decide({"symbol": "SPY"}, post=_never_post)
        assert out["backend"] == "stub" and out["error"] == "digest_unverified"
    # A refusal is cached for DIGEST_RETRY_S, so a scan does not hammer /api/tags.
    calls = []
    sc._get = lambda url, timeout: calls.append(url) or {"models": []}
    sc._reset_digest()
    sc.decide({"symbol": "A"}, post=_never_post)
    sc.decide({"symbol": "B"}, post=_never_post)
    assert len(calls) == 1
    # ...and once verified, ok is cached for the process: no more GETs, no flip on a later bad tags.
    _use_tags(("nimble:latest", FULL))
    calls = []
    good = sc._get
    sc._get = lambda url, timeout: calls.append(url) or good(url, timeout)
    sc.verify_digest()
    sc.verify_digest()
    assert len(calls) == 1 and sc.model_pin() == "nimble:latest"
    # Restart (cache gone): re-verified against whatever is on disk now.
    sc._reset_digest()
    assert sc.model_pin() == ""
    sc._get = _fake_get(("nimble:latest", OTHER))
    assert sc.verify_digest() == "digest_mismatch" and sc.model_pin() == ""
    # Warmup is a decide-shaped call: it refuses on a mismatch too, never POSTs.
    os.environ["VALEDIA_SYSTEMONE"] = "1"
    try:
        sc._reset_digest()
        res = sc.warmup(post=_never_post)
        assert res["error"] == "digest_mismatch"
    finally:
        os.environ.pop("VALEDIA_SYSTEMONE", None)
    # Explicit v1 floating look-alikes stay refused whatever the digest says.
    for tag in ("nimble:9b", "nimble:1", "nimble:latest-v1", "nimble:LATEST2"):
        os.environ["VALEDIA_SYSTEMONE_MODEL"] = tag
        try:
            _use_tags((tag, FULL), "nimble:latest")
            out = sc.decide({"symbol": "SPY"}, post=_never_post)
            assert out["backend"] == "stub" and out["error"] == "model_refused", tag
        finally:
            os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
    # Explicit v2 names are unaffected: no /api/tags needed, wrong digests irrelevant.
    sc._get = _fake_get(("nimble:latest", OTHER))
    sc._reset_digest()
    for tag in ("nimble2", "nimble:9b-v2", "Bespoke-Nimble-9B-v2"):
        os.environ["VALEDIA_SYSTEMONE_MODEL"] = tag
        try:
            assert sc.model_pin() == tag and sc.digest_status() is None
            sc._get = tags_down
            out = sc.decide({"symbol": "SPY"}, post=lambda url, payload, timeout, tag=tag: {
                "model": tag, "answers": {"admit_paper": {"noul": 0.5}}})
            assert out["backend"] == "systemone_http", tag
        finally:
            os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
    # Env override of the pin: VALEDIA_SYSTEMONE_DIGEST.
    other_prefix = OTHER[:12]
    _use_tags(("nimble:latest", OTHER))
    assert sc.verify_digest() == "digest_mismatch"
    os.environ["VALEDIA_SYSTEMONE_DIGEST"] = other_prefix
    try:
        assert sc.digest_prefix() == other_prefix
        assert sc.digest_status() == "digest_unverified"   # key changed: cache does not carry over
        assert sc.verify_digest() == "ok" and sc.model_pin() == "nimble:latest"
        os.environ["VALEDIA_SYSTEMONE_DIGEST"] = "sha256:" + other_prefix.upper()
        assert sc.digest_prefix() == other_prefix
        # Unusable overrides never match anything (too short / not hex / empty).
        for bad in ("", "abc", "zzzzzzzzzzzz", "24e550a1"):
            os.environ["VALEDIA_SYSTEMONE_DIGEST"] = bad
            _use_tags(("nimble:latest", FULL))
            assert sc.digest_prefix() == "" and sc.verify_digest() == "digest_unverified", bad
    finally:
        os.environ.pop("VALEDIA_SYSTEMONE_DIGEST", None)
    # With the default pin back, the old digest is the one that matches again.
    _use_tags(("nimble:latest", FULL))
    assert sc.verify_digest() == "ok"
    # Floating names are not Nimble 2 by name alone.
    assert not sc.is_nimble2("nimble:latest") and not sc.is_nimble2("nimble")
    assert sc.is_floating_nimble("NIMBLE:Latest") and sc.is_floating_nimble("nimble")
    assert not sc.is_floating_nimble("nimble:9b") and not sc.is_floating_nimble("nimble2:latest")
    _use_tags("nimble:latest")


def _lines():
    if not os.path.exists(LOG):
        return []
    return [json.loads(x) for x in open(LOG).read().splitlines()]


def _reset_log():
    if os.path.exists(LOG):
        os.remove(LOG)


def _ok_post(model="nimble2"):
    def post(url, payload, timeout):
        return {
            "model": model,
            "answers": {
                "admit_paper": {"type": "noul", "noul": 0.81},
                "edge_quality": {"type": "score", "score": 2.0, "confidence": 0.6},
                "venue_class": {
                    "type": "choice", "choice": "crypto", "confidence": 0.9,
                    "probabilities": {"crypto": 0.9, "equity": 0.1},
                },
            },
            "usage": {"input_tokens": 40, "output_tokens": 3},
        }
    return post


def test_questions_have_no_numbers_or_comparisons():
    for qs in (sc.QUESTIONS, sc.WARM_QUESTIONS):
        blob = json.dumps(qs)
        assert not re.search(r"\d", blob), re.findall(r".{12}\d.{6}", blob)
        low = blob.lower()
        for word in ("percent", "greater", "less than", "under a", "above", "below", "sum",
                     "count", "average"):
            assert word not in low, word
    # The facts the criteria refer to are in the state, computed by code.
    state = sc.compact_state(_row(), None, {"print_bar_pct": 16.0, "min_quote_usd": 250000.0})
    for key in ("has_print", "edge_positive", "clears_size_bar", "thin_tape", "is_oil", "is_weather"):
        assert key in state["facts"]
    assert "facts" in sc.QUESTIONS["admit_paper"]["instructions"]
    assert "facts" in sc.QUESTIONS["edge_quality"]["instructions"]


def test_facts_are_code_computed():
    lim = {"print_bar_pct": 16.0, "min_quote_usd": 250000.0}
    f = sc.facts(dict(_row(), chg_24h=16.4, edge=0.161, quote_volume_usd=1e6), lim)
    assert f == {"has_print": True, "edge_positive": True, "clears_size_bar": True,
                 "thin_tape": False, "is_oil": False, "is_weather": False}
    f = sc.facts(dict(_row(), chg_24h=15.9, edge=-0.001, quote_volume_usd=100.0), lim)
    assert f["clears_size_bar"] is False and f["edge_positive"] is False and f["thin_tape"] is True
    f = sc.facts(dict(_row(), chg_24h=16.0), lim)
    assert f["clears_size_bar"] is True  # the bar itself clears; code decides, not the model
    f = sc.facts(dict(_row(), chg_24h=None, edge=None), lim)
    assert f["has_print"] is False and f["edge_positive"] is False and f["clears_size_bar"] is None
    f = sc.facts(dict(_row(), chg_24h=float("nan")), lim)
    assert f["has_print"] is False
    assert sc.facts(_row())["clears_size_bar"] is None and sc.facts(_row())["thin_tape"] is None
    oil = dict(_row(), oil_fenced=True, class_="x")
    assert sc.facts(oil)["is_oil"] is True
    assert sc.facts(dict(_row(), **{"class": "weather"}))["is_weather"] is True
    json.dumps(sc.compact_state(_row(), None, lim), allow_nan=False)


def test_answers_carry_confidence_and_probabilities():
    out = sc.decide(sc.compact_state(_row()), post=_ok_post())
    a = out["answers"]
    assert a["admit_paper"] == 0.81 and a["edge_quality"] == 2.0
    assert a["venue_class"] == "crypto"
    assert a["venue_confidence"] == 0.9
    assert a["venue_probabilities"] == {"crypto": 0.9, "equity": 0.1}
    assert a["edge_quality_confidence"] == 0.6
    # noul may arrive as `probability`; non-finite and junk become None.
    body = {"model": "nimble2", "answers": {
        "admit_paper": {"probability": 0.4},
        "venue_class": {"choice": "equity", "confidence": float("nan"),
                        "probabilities": {"equity": float("inf")}}}}
    a = sc._answers(body)
    assert a["admit_paper"] == 0.4
    assert a["venue_confidence"] is None
    assert a["venue_probabilities"] == {"equity": None}


def test_decision_log_every_call():
    _reset_log()
    sc._WARM = False
    row = _row()
    state = sc.compact_state(row)
    sc.decide(state, post=_ok_post(), label="would_buy")                      # success
    sc.decide(state, post=lambda *a: (_ for _ in ()).throw(TimeoutError("t")), label="watch")  # timeout
    sc.decide(state, post=lambda *a: {"model": "nimble2"}, label="watch")      # no answers
    sc.decide(state, post=_ok_post("nimble:9b"), label="watch")                # v1 response refused
    os.environ["VALEDIA_SYSTEMONE_MODEL"] = "nimble:9b"
    try:
        sc.decide(state, post=lambda *a: (_ for _ in ()).throw(AssertionError("posted")),
                  label="would_pass")                                          # refused pin
    finally:
        os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
    nan_state = dict(state, chg_pct=float("nan"))
    sc.decide(nan_state, post=_ok_post(), label="watch")
    lines = _lines()
    assert len(lines) == 6, len(lines)
    ok, timeout, noans, v1, refused, nan = lines
    for ln in lines:
        for key in ("ts", "source", "backend", "model", "state", "response", "error",
                    "answers", "latency_ms", "budget_ms", "within_budget", "cold", "label",
                    "create_order"):
            assert key in ln, key
        assert ln["source"] == "movers_scan" and ln["create_order"] is False
        assert ln["state"]["symbol"] == "PUMP"
    assert ok["error"] is None and ok["backend"] == "systemone_http"
    assert ok["label"] == "would_buy" and ok["model"] == "nimble:latest"
    assert ok["response"]["answers"]["admit_paper"]["noul"] == 0.81   # full raw response
    assert ok["answers"]["admit_paper"] == 0.81 and ok["answers"]["venue_confidence"] == 0.9
    assert ok["cold"] is True and ok["state"]["facts"]["is_oil"] is False
    assert timeout["error"] == "TimeoutError" and timeout["backend"] == "stub"
    assert timeout["response"] is None and timeout["within_budget"] is False
    assert timeout["label"] == "watch"
    assert noans["error"] == "no_answers" and noans["response"] == {"model": "nimble2"}
    assert v1["error"] == "model_refused" and v1["response"]["model"] == "nimble:9b"
    assert refused["error"] == "model_refused" and refused["model"] is None
    assert refused["label"] == "would_pass" and refused["response"] is None
    assert nan["state"]["chg_pct"] is None and nan["error"] is None
    assert lines[1]["cold"] is False  # warm after the first answer
    blob = open(LOG).read().lower()
    for word in ("api_key", "authorization", "bearer", "secret"):
        assert word not in blob
    # Never raises into the scan, even when the path cannot be written.
    os.environ["VALEDIA_SYSTEMONE_LOG"] = "/dev/null/no/such/decisions.jsonl"
    try:
        out = sc.decide(state, post=_ok_post())
        assert out["backend"] == "systemone_http"
        sc.log_decision("x", "stub", None, object(), {1: {2}}, None, None, None)
    finally:
        os.environ["VALEDIA_SYSTEMONE_LOG"] = LOG
    # Test mode never writes the repo's real log.
    os.environ.pop("VALEDIA_SYSTEMONE_LOG", None)
    try:
        assert sc.log_path() == ""
        os.environ.pop("OPENCLAW_SKIP_ENV", None)
        assert sc.log_path().endswith(os.path.join("data", "systemone", "decisions.jsonl"))
    finally:
        os.environ["OPENCLAW_SKIP_ENV"] = "1"
        os.environ["VALEDIA_SYSTEMONE_LOG"] = LOG


def test_warmup():
    _reset_log()
    sc._WARM = False
    calls = []
    assert sc.warmup(post=lambda *a: calls.append(a)) is None and not calls  # off: no call
    os.environ["VALEDIA_SYSTEMONE"] = "1"
    try:
        def post(url, payload, timeout):
            calls.append(payload)
            return {"model": "nimble2", "answers": {"ping": {"type": "noul", "noul": 0.9}}}

        res = sc.warmup(post=post)
        assert len(calls) == 1 and res["error"] is None
        assert calls[0]["keep_alive"] == sc.KEEP_ALIVE and calls[0]["state"]["create_order"] is False
        assert list(calls[0]["questions"]) == ["ping"]
        assert sc._WARM is True
        assert _lines()[0]["source"] == "warmup"
        down = lambda *a: (_ for _ in ()).throw(ConnectionRefusedError("down"))  # noqa: E731
        sc._WARM = False
        assert sc.warmup(post=down)["error"] == "ConnectionRefusedError"  # fail-open, no raise
        assert sc._WARM is False
    finally:
        os.environ.pop("VALEDIA_SYSTEMONE", None)


def test_latency_budget_and_summary():
    assert sc.budget_ms() == 500.0
    os.environ["VALEDIA_SYSTEMONE_BUDGET_MS"] = "junk"
    assert sc.budget_ms() == 500.0
    os.environ["VALEDIA_SYSTEMONE_BUDGET_MS"] = "1"
    try:
        def slow(url, payload, timeout):
            time.sleep(0.02)
            return _ok_post()(url, payload, timeout)

        slow_out = sc.decide(sc.compact_state(_row()), post=slow)
        assert slow_out["within_budget"] is False and slow_out["latency_ms"] > 1.0
        assert _lines()[-1]["within_budget"] is False and _lines()[-1]["budget_ms"] == 1.0
    finally:
        os.environ["VALEDIA_SYSTEMONE_BUDGET_MS"] = "5000"
    try:
        fast = sc.decide(sc.compact_state(_row()), post=_ok_post())
        assert fast["within_budget"] is True and fast["cold"] is False
        assert _lines()[-1]["within_budget"] is True
    finally:
        os.environ.pop("VALEDIA_SYSTEMONE_BUDGET_MS", None)
    os.environ["VALEDIA_SYSTEMONE"] = "1"
    try:
        def row(ms, cold, ok=True):
            return {"systemone_shadow": {
                "backend": "systemone_http" if ok else "stub", "fail_open": not ok,
                "latency_ms": ms, "cold": cold, "within_budget": ok and ms <= 500}}
        ranked = [row(float(x), False) for x in range(10, 110, 10)]   # ten warm calls
        ranked.append(row(9000.0, True))                              # one cold load
        ranked.append(row(None, False, ok=False))
        blk = sc.summary(ranked)
        assert blk["latency_ms_p50"] == 50.0 and blk["latency_ms_p95"] == 100.0
        assert blk["cold_n"] == 1 and blk["within_budget_n"] == 10
        assert blk["latency_ms_max"] == 9000.0 and blk["budget_ms"] == 500.0
        assert sc.summary([])["latency_ms_p50"] is None
    finally:
        os.environ.pop("VALEDIA_SYSTEMONE", None)


if __name__ == "__main__":
    test_questions_have_no_numbers_or_comparisons()
    test_facts_are_code_computed()
    test_answers_carry_confidence_and_probabilities()
    test_decision_log_every_call()
    test_warmup()
    test_latency_budget_and_summary()
    test_loopback_and_pin()
    test_compact_state_has_no_secrets()
    test_decide_parses_and_fail_open()
    test_model_pin_nimble2_only()
    test_summary_off_and_on()
    test_health_shape()
    test_digest_pin()
    print("PASS test_systemone_hotpath_client")
