#!/usr/bin/env python3
"""Nimble 2 pin and digest pin (systemone_pin via the client). Mocked HTTP.

Pure move of the pin/digest tests out of test_systemone_hotpath_client.py
(500-line rule), plus the digest-OK TTL test.
Run: python3 test_systemone_pin.py   (or pytest)
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
for _k in ("VALEDIA_SYSTEMONE", "VALEDIA_SYSTEMONE_MODEL", "VALEDIA_SYSTEMONE_DIGEST",
           "VALEDIA_SYSTEMONE_DIGEST_TTL_S", "SYSTEMONE_LIVE_PROBE", "VALEDIA_SYSTEMONE_BUDGET_MS"):
    os.environ.pop(_k, None)
LOG = os.path.join(tempfile.mkdtemp(prefix="s1pin_"), "nested", "decisions.jsonl")

import systemone_hotpath_client as sc
import systemone_pin as pin

_REAL_GET = sc._get

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




def _use_tags(*rows):
    """Fake /api/tags for ask()/decide() (module transport) and drop the digest cache."""
    sc._get = _fake_get(*rows)
    sc._reset_digest()


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


def _setup():
    """Own log + faked local Ollama (digest matches). Restored by _teardown."""
    global _SAVED
    _SAVED = (sc._get, os.environ.get("VALEDIA_SYSTEMONE_LOG"))
    os.environ["VALEDIA_SYSTEMONE_LOG"] = LOG
    _use_tags("nimble:latest")


def _teardown():
    sc._get = _SAVED[0]
    if _SAVED[1] is None:
        os.environ.pop("VALEDIA_SYSTEMONE_LOG", None)
    else:
        os.environ["VALEDIA_SYSTEMONE_LOG"] = _SAVED[1]
    for k in ("VALEDIA_SYSTEMONE", "VALEDIA_SYSTEMONE_MODEL", "VALEDIA_SYSTEMONE_DIGEST",
              "VALEDIA_SYSTEMONE_DIGEST_TTL_S"):
        os.environ.pop(k, None)
    sc._reset_digest()


try:
    import pytest

    @pytest.fixture(autouse=True)
    def _scoped_fakes():
        _setup()
        yield
        _teardown()
except ImportError:  # script mode
    pass


def test_digest_ok_ttl():
    # A passed check is trusted for DIGEST_OK_TTL_S, then must be re-verified.
    assert pin.DIGEST_OK_TTL_S == 300.0 and pin.digest_ok_ttl() == 300.0
    _use_tags(("nimble:latest", FULL))
    calls = []
    good = sc._get
    sc._get = lambda url, timeout: calls.append(url) or good(url, timeout)
    assert sc.verify_digest() == "ok" and sc.verify_digest() == "ok" and len(calls) == 1
    assert sc.model_pin() == "nimble:latest"
    # Expiry (age the cached ok): model_pin is network-free and now refuses.
    sc._DIGEST["ts"] -= pin.DIGEST_OK_TTL_S + 1
    assert sc.digest_status() == "digest_unverified" and sc.model_pin() == ""
    # Same digest after expiry: re-verified over /api/tags, POSTs again.
    seen = []
    out = sc.decide({"symbol": "SPY"}, post=lambda url, payload, timeout: seen.append(1) or {
        "model": "nimble:latest", "answers": {"admit_paper": {"noul": 0.5}}})
    assert len(calls) == 2 and seen == [1] and out["backend"] == "systemone_http"
    # Pass, then a re-pull with a different digest, then TTL expiry: refused, no POST.
    _use_tags(("nimble:latest", FULL))
    assert sc.verify_digest() == "ok"
    sc._get = _fake_get(("nimble:latest", OTHER))             # model re-pulled
    sc._DIGEST["ts"] -= pin.DIGEST_OK_TTL_S + 1
    _reset_log()
    out = sc.decide({"symbol": "SPY"}, post=_never_post, label="watch")
    assert out["backend"] == "stub" and out["error"] == "digest_mismatch"
    assert _lines()[-1]["error"] == "digest_mismatch"
    assert sc.model_pin() == ""
    # Env override of the TTL; junk or non-positive values fall back to the default.
    for val, want in (("5", 5.0), ("0", 300.0), ("-1", 300.0), ("junk", 300.0), ("inf", 300.0), ("nan", 300.0)):
        os.environ["VALEDIA_SYSTEMONE_DIGEST_TTL_S"] = val
        assert pin.digest_ok_ttl() == want, val
    os.environ["VALEDIA_SYSTEMONE_DIGEST_TTL_S"] = "5"
    _use_tags(("nimble:latest", FULL))
    assert sc.verify_digest() == "ok"
    sc._DIGEST["ts"] -= 6
    assert sc.model_pin() == ""
    os.environ.pop("VALEDIA_SYSTEMONE_DIGEST_TTL_S", None)
    # Explicit v2 names never expire (no digest involved).
    os.environ["VALEDIA_SYSTEMONE_MODEL"] = "nimble2"
    assert sc.model_pin() == "nimble2" and sc.digest_status() is None


if __name__ == "__main__":
    _setup()
    try:
        test_model_pin_nimble2_only()
        test_health_shape()
        test_digest_pin()
        test_digest_ok_ttl()
    finally:
        _teardown()
    print("PASS test_systemone_pin")
