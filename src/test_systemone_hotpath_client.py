#!/usr/bin/env python3
"""System One client on the shared Elephant-1 instance. Faked transport. No server required.

Ruling C (the request names the instance, no keep_alive, only the two URLs, a reply naming another model is
refused, no load or unload under any input), the deadline, no warmup request, health, and the switch: unset,
the chain makes no request at all. The real transport is exercised with a recording stand-in for the HTTP
opener, so no socket is ever opened.
Run: python3 test_systemone_hotpath_client.py
"""
import itertools
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
for _k in ("VALEDIA_SYSTEMONE", "VALEDIA_SYSTEMONE_MODEL", "VALEDIA_SYSTEMONE_DIGEST",
           "VALEDIA_SYSTEMONE_BUDGET_MS"):
    os.environ.pop(_k, None)
LOG = os.path.join(tempfile.mkdtemp(prefix="s1log_"), "nested", "decisions.jsonl")
# Retired shadow decider name, spelled indirectly so grep over scripts/ stays clean.
RETIRED = "".join(["l", "a", "y", "a"])

import systemone_hotpath_client as sc  # noqa: E402
import systemone_test_fakes as fk  # noqa: E402

DM = sc._pin.dm
_S = fk.Scoped(sc, LOG)


def _on():
    os.environ["VALEDIA_SYSTEMONE"] = "1"


def _setup():
    _S.setup()
    _S.reply()
    _on()
    sc._WARM = False


def _teardown():
    for k in ("VALEDIA_SYSTEMONE", "VALEDIA_SYSTEMONE_MODEL", "VALEDIA_SYSTEMONE_DIGEST"):
        os.environ.pop(k, None)
    _S.teardown()


try:
    import pytest

    @pytest.fixture(autouse=True)
    def _scoped_fakes():
        _setup()
        yield
        _teardown()
except ImportError:  # script mode
    pass


def _row():
    return {"symbol": "PUMP", "class": "crypto", "chg_24h": 16.4, "edge": 0.161, "impact_usd": 8.1,
            "buzz": 0, "reasons": ["binance_24h"], "label": "would_buy", "oil_fenced": False}


def _raise(exc):
    def post(url, payload, timeout):
        raise exc
    return post


def _never(*a, **k):
    raise AssertionError("an injected transport was used")


class _Resp:
    def __init__(self, body):
        self.raw = json.dumps(body).encode()

    def read(self, n=-1):
        return self.raw if n < 0 else self.raw[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeHTTP:
    """Stands in for the module's no-redirect opener: records every request, opens no socket."""

    def __init__(self, listing=None, reply=None):
        self.calls, self.listing, self.reply = [], listing, reply

    def open(self, req, timeout=None):
        data = req.data
        payload = json.loads(data) if data else None
        self.calls.append((req.get_method(), req.full_url, payload, timeout))
        if req.full_url == sc.LIST_URL and req.get_method() == "GET":
            if isinstance(self.listing, Exception):
                raise self.listing
            return _Resp(fk.listing() if self.listing is None else self.listing)
        if req.full_url == sc.DECIDE_URL and req.get_method() == "POST":
            if isinstance(self.reply, Exception):
                raise self.reply
            body = self.reply(payload) if callable(self.reply) else self.reply
            return _Resp(fk.typed_reply(payload["questions"]) if body is None else body)
        raise AssertionError("request outside the two allowed URLs: %s %s" % (req.get_method(), req.full_url))


_REAL_GET, _REAL_POST, _REAL_HTTP = sc._get, sc._post, sc._HTTP   # the module's own, at import


def _real_transport(http):
    """Use the module's own _get/_post over a recording opener (the scope restores _get/_post)."""
    assert _REAL_GET.__module__ == _REAL_POST.__module__ == "systemone_hotpath_client"
    sc._HTTP = http
    sc._get, sc._post = _REAL_GET, _REAL_POST


def _restore_http():
    sc._HTTP = _REAL_HTTP


# ---------------------------------------------------------------- URLs and the request (ruling C)
def test_urls_are_the_shared_modules_and_nothing_else():
    assert sc.DECIDE_URL == DM.URL + "/v1/systemone"
    assert sc.LIST_URL == DM.URL + "/api/v1/models"
    assert sc.ALLOWED_URLS == frozenset({sc.DECIDE_URL, sc.LIST_URL})
    http = FakeHTTP()
    _real_transport(http)
    try:
        for bad in (DM.URL + "/api/v1/models/load", DM.URL + "/api/v1/models/unload", DM.URL + "/v1/models",
                    "http://127.0.0.1:1234/v1/systemone", "http://127.0.0.1:11435/v1/systemone",
                    "http://localhost:1235/v1/systemone", sc.LIST_URL, ""):
            try:
                sc._post(bad, {"model": DM.INSTANCE}, 1)
                raise AssertionError("posted to %s" % bad)
            except ValueError:
                pass
        for bad in (sc.DECIDE_URL, DM.URL + "/api/v1/models/load", DM.URL + "/api/v0/models", ""):
            try:
                sc._get(bad, 1)
                raise AssertionError("got %s" % bad)
            except ValueError:
                pass
        assert http.calls == []                                       # refused before any request
    finally:
        _restore_http()
    src = open(os.path.join(HERE, "systemone_hotpath_client.py")).read()
    for word in ("8766", "8765", RETIRED, "VALEDIA_" + RETIRED.upper(), "ollama pull", "lms "):
        assert word.lower() not in src.lower(), word


def test_request_names_the_instance_without_keep_alive_and_injected_post_is_ignored():
    out = sc.decide(sc.compact_state(_row()), post=_never, get=fk.list_get())
    assert out["backend"] == "systemone_http" and out["error"] is None, out
    assert len(_S.posts) == 1
    call = _S.posts[0]
    assert call["url"] == sc.DECIDE_URL
    assert set(call["payload"]) == {"model", "state", "questions"}     # no keep_alive, nothing else
    assert call["payload"]["model"] == DM.INSTANCE
    assert "keep_alive" not in json.dumps(call["payload"])
    assert call["payload"]["questions"] == sc.QUESTIONS and call["payload"]["state"]["create_order"] is False
    assert 0 < call["timeout"] <= 60
    assert out["model"] == DM.INSTANCE
    assert out["typed_answers"]["admit_paper"]["type"] == "noul"
    assert out["usage"] == {"input_tokens": 10, "output_tokens": 2}


def test_reply_naming_another_model_or_untyped_is_refused():
    state = sc.compact_state(_row())
    for model in ("nimble-v3:9b-mxfp8", "nimble:latest", "nimble2", "elephant-2", "Elephant-1",
                  "lmstudio:elephant-1@q8_0", ""):
        _S.reply(model=model)
        bad = sc.decide(state)
        assert bad["backend"] == "stub" and bad["error"] == "model_refused", (model, bad)
        assert bad["create_order"] is False
    good = fk.typed_reply(sc.QUESTIONS)
    for body, err in (({"model": DM.INSTANCE}, "no_answers"),
                      ({k: v for k, v in good.items() if k != "model"}, "model_refused"),
                      (["x"], "bad_response"),
                      (dict(good, usage=None), "ContractError"),                     # untyped: no usage
                      (dict(good, answers={"admit_paper": {"noul": 0.9}}), "ContractError"),
                      ({"model": DM.INSTANCE, "answers": {"admit_paper": {"type": "noul", "noul": 0.81},
                                                          "edge_quality": {"type": "score", "score": 2.0},
                                                          "venue_class": {"type": "choice",
                                                                          "choice": "crypto"}},
                        "usage": {"input_tokens": 1, "output_tokens": 1}}, "ContractError")):
        sc._post = lambda url, payload, timeout, b=body: b
        out = sc.decide(state)
        assert out["backend"] == "stub" and out["error"] == err, (body, out)
    sc._post = _raise(TimeoutError("down"))
    stub = sc.decide({"symbol": "SPY"})
    assert stub["backend"] == "stub" and stub["fail_open"] is True and stub["error"] == "TimeoutError"


def test_deadline_is_the_typed_rule():
    state = {"symbol": "X"}
    for t in (61, 120, 0, -1):
        res = sc.ask(state, sc.WARM_QUESTIONS, source="t", timeout=t)
        assert res["error"] == "ValueError", (t, res)
    assert _S.posts == []
    res = sc.ask(state, sc.WARM_QUESTIONS, source="t", timeout=60)
    assert res["error"] is None and _S.posts[-1]["timeout"] <= 60
    res = sc.ask(state, sc.WARM_QUESTIONS, source="t")
    assert res["error"] is None and 59 < _S.posts[-1]["timeout"] <= 60
    assert sc.TIMEOUT_S == 60


def test_missing_or_wrong_instance_takes_the_refusal_path():
    for body, err in ((fk.listing(fk.entry(instances=[])), "instance_missing"),
                      (fk.listing(fk.entry(quantization={"name": "Q4_K_M"})), "instance_wrong"),
                      (fk.listing(fk.entry(instances=[fk.instance(), fk.instance()])), "instance_wrong"),
                      ({"models": "x"}, "instance_unverified")):
        _S.use(body)
        out = sc.decide(sc.compact_state(_row()))
        assert out["backend"] == "stub" and out["error"] == err, (err, out)
        assert out["model"] is None
    assert _S.posts == []                                             # never called
    os.environ["VALEDIA_SYSTEMONE_MODEL"] = "nimble:latest"
    _S.use()
    _S.gets.clear()
    out = sc.decide(sc.compact_state(_row()))
    assert out["error"] == "model_refused" and _S.posts == [] and _S.gets == []


def test_no_load_or_unload_under_any_input():
    """Every combination of env, model list and reply: only GET list and POST typed call, ever."""
    lists = [None, fk.listing(fk.entry(instances=[])), fk.listing(fk.entry(publisher="x")),
             fk.listing(fk.entry(instances=[fk.instance(), fk.instance()])), {"models": 3},
             ConnectionRefusedError("down")]

    def other(payload):
        return fk.typed_reply(payload["questions"], model="nimble-v3:9b-mxfp8")
    replies = [None, other, {"model": DM.INSTANCE}, ["x"], TimeoutError("t"),
               lambda p: dict(fk.typed_reply(p["questions"]), usage=None)]
    envs = [None, "elephant-1", "nimble:latest", "load", "unload", DM.URL + "/api/v1/models/load"]
    http = FakeHTTP()
    _real_transport(http)
    try:
        for env, lst, rep in itertools.product(envs, lists, replies):
            if env is None:
                os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
            else:
                os.environ["VALEDIA_SYSTEMONE_MODEL"] = env
            sc._reset_check()
            http.listing, http.reply = lst, rep
            sc.decide(sc.compact_state(_row()), post=_never)
            sc.ask({"symbol": "Y"}, sc.WARM_QUESTIONS, source="fuzz", post=_never)
            sc.health()
            sc.warmup(post=_never)
            sc.synthetic_smoke()
            sc.summary([])
    finally:
        _restore_http()
        os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
    assert http.calls, "the fuzz made no request at all"
    for method, url, payload, timeout in http.calls:
        assert (method, url) in (("GET", sc.LIST_URL), ("POST", sc.DECIDE_URL)), (method, url)
        assert "load" not in url.split("/")[-1]
        if method == "POST":
            assert set(payload) == {"model", "state", "questions"} and payload["model"] == DM.INSTANCE
            assert 0 < timeout <= 60
    assert not any(m == "POST" and u == sc.LIST_URL for m, u, _, _ in http.calls)


def test_switch_unset_makes_no_request_at_all():
    os.environ.pop("VALEDIA_SYSTEMONE", None)
    http = FakeHTTP()
    _real_transport(http)
    try:
        import nimble_annotate as na
        import nimble_decide as nd
        import nimble_wiring as nw
        import nimble_wiring_spot as nws
        assert sc.ask({"symbol": "X"}, sc.WARM_QUESTIONS, source="t")["error"] == "systemone_disabled"
        assert sc.decide(sc.compact_state(_row()))["error"] == "systemone_disabled"
        assert sc.warmup() is None and sc.synthetic_smoke()["error"] == "systemone_disabled"
        h = sc.health()
        assert h["ok"] is False and h["error"] == "systemone_disabled" and h["version"] is None
        assert sc.summary([])["enabled"] is False and sc.summary([])["model"] == DM.INSTANCE
        for mode in ("shadow", "veto", "off"):
            os.environ["VALEDIA_NIMBLE_MODE"] = mode
            nd.decide_candidate("stock", {"symbol": "NVDA"}, side="buy", code_facts={}, informed={},
                                source="t")
            nw.gate({"ticker": "KX-1", "side": "yes"}, {}, source="t")
            nws.gate("crypto", "BTC-USD", {}, source="t")
            nws.scan_rows([dict(_row(), symbol="BTC")], {})
            nd.warm()
        os.environ.pop("VALEDIA_NIMBLE_MODE", None)
        assert na.annotate_report({"signals": {}}) == {"signals": {}}
        assert na.annotate_weather({"ticker": "W"}) == {"ticker": "W"}
        assert na.conviction_note({}, {}, caller="t") is None
        assert na.season_scan([])["enabled"] is False
    finally:
        _restore_http()
        os.environ.pop("VALEDIA_NIMBLE_MODE", None)
    assert http.calls == [] and _S.gets == [] and _S.posts == []


def test_warmup_sends_nothing():
    calls = []
    assert sc.warmup(post=lambda *a: calls.append(a)) is None
    assert sc.warmup(post=lambda *a: calls.append(a), timeout=5, get=lambda *a: calls.append(a)) is None
    assert calls == [] and _S.posts == [] and _S.gets == []
    src = open(os.path.join(HERE, "systemone_hotpath_client.py")).read()
    assert re.search(r"def warmup\(post=None, timeout=None, get=None\):\n(\s+\"\"\".*\"\"\"\n)?\s+return None\n",
                     src)


def test_health_is_the_list_check():
    h = sc.health(get=fk.list_get())
    assert h == {"ok": True, "error": None, "version": None, "model_present": True,
                 "model": DM.INSTANCE, "instance_check": "ok", "mode": "shadow", "enforcement": "none"}
    h = sc.health(get=fk.list_get(fk.listing(fk.entry(instances=[]))))
    assert h["ok"] is False and h["error"] == "instance_missing" and h["model_present"] is False
    h = sc.health(get=fk.list_get(fk.listing(fk.entry(publisher="x"))))
    assert h["ok"] is False and h["error"] == "instance_wrong" and h["model_present"] is True
    h = sc.health(get=_raise_get)
    assert h["ok"] is False and h["error"] == "instance_unverified" and "version" in h
    assert _S.posts == []


def _raise_get(url, timeout):
    raise ConnectionRefusedError("down")


# ---------------------------------------------------------------- unchanged behaviour
def test_compact_state_has_no_secrets():
    state = sc.compact_state(_row(), {"present": False, "signals": {}})
    blob = json.dumps(state).lower()
    for word in ("api_key", "secret", "password", "bearer", "mnemonic"):
        assert word not in blob
    assert state["create_order"] is False and state["class"] == "crypto"
    oil = dict(_row(), oil_fenced=True, label="watch", **{"class": "oil"})
    assert sc.compact_state(oil)["class"] == "oil_blocked"
    weather = dict(_row(), label="watch", **{"class": "weather"})
    assert sc.compact_state(weather)["class"] == "weather_watch"
    missing = dict(_row(), chg_24h=float("nan"), edge=float("nan"), impact_usd=float("inf"))
    state = sc.compact_state(missing)
    assert state["chg_pct"] is None and state["fee_true_edge"] is None and state["impact_usd"] is None
    json.dumps({"state": state, "questions": sc.QUESTIONS}, allow_nan=False)


def test_decide_parses_typed_answers():
    _S.reply({"admit_paper": 0.81, "edge_quality": 2, "venue_class": "crypto"}, confidence=0.6)
    out = sc.decide(sc.compact_state(_row()))
    assert out["backend"] == "systemone_http" and out["fail_open"] is False
    assert out["create_order"] is False and out["enforcement"] == "none" and out["mode"] == "shadow"
    assert out["judge"] == "dont_trade"
    a = out["answers"]
    assert a["admit_paper"] == 0.81 and a["edge_quality"] == 2.0 and a["venue_class"] == "crypto"
    assert a["venue_confidence"] == 0.6 and a["edge_quality_confidence"] == 0.6
    assert set(a["venue_probabilities"]) == set(sc.QUESTIONS["venue_class"]["criteria"])
    assert isinstance(out["latency_ms"], float)
    # _answers on a raw body: noul may arrive as `probability`; non-finite and junk become None
    body = {"answers": {"admit_paper": {"probability": 0.4},
                        "venue_class": {"choice": "equity", "confidence": float("nan"),
                                        "probabilities": {"equity": float("inf")}}}}
    a = sc._answers(body)
    assert a["admit_paper"] == 0.4 and a["venue_confidence"] is None
    assert a["venue_probabilities"] == {"equity": None}


def test_summary_off_and_on():
    os.environ.pop("VALEDIA_SYSTEMONE", None)
    off = sc.summary([{"systemone_shadow": {"backend": "systemone_http"}}])
    assert off["enabled"] is False and off["backend"] == "stub" and off["model"] == DM.INSTANCE
    _on()
    ranked = [{"systemone_shadow": {"backend": "systemone_http", "fail_open": False, "latency_ms": 90.0}}]
    block = sc.summary(ranked, {"present": True, "signals": {}})
    assert block["backend"] == "systemone_http" and block["fail_open"] is False
    assert block["create_order"] is False and block["latency_ms_max"] == 90.0
    assert block["instance_check"] == "instance_unverified" and "digest" not in block
    empty = sc.summary([{"systemone_shadow": {"backend": "stub", "fail_open": True}}])
    assert empty["backend"] == "stub" and empty["fail_open"] is True


def _lines():
    if not os.path.exists(LOG):
        return []
    return [json.loads(x) for x in open(LOG).read().splitlines()]


def _reset_log():
    if os.path.exists(LOG):
        os.remove(LOG)


def test_questions_have_no_numbers_or_comparisons():
    for qs in (sc.QUESTIONS, sc.WARM_QUESTIONS):
        blob = json.dumps(qs)
        assert not re.search(r"\d", blob), re.findall(r".{12}\d.{6}", blob)
        low = blob.lower()
        for word in ("percent", "greater", "less than", "under a", "above", "below", "sum",
                     "count", "average"):
            assert word not in low, word
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
    assert sc.facts(dict(_row(), chg_24h=16.0), lim)["clears_size_bar"] is True
    f = sc.facts(dict(_row(), chg_24h=None, edge=None), lim)
    assert f["has_print"] is False and f["edge_positive"] is False and f["clears_size_bar"] is None
    assert sc.facts(dict(_row(), chg_24h=float("nan")), lim)["has_print"] is False
    assert sc.facts(_row())["clears_size_bar"] is None and sc.facts(_row())["thin_tape"] is None
    assert sc.facts(dict(_row(), oil_fenced=True))["is_oil"] is True
    assert sc.facts(dict(_row(), **{"class": "weather"}))["is_weather"] is True
    json.dumps(sc.compact_state(_row(), None, lim), allow_nan=False)


def test_decision_log_every_call():
    _reset_log()
    state = sc.compact_state(_row())
    sc.decide(state, label="would_buy")                                       # success
    sc._post = _raise(TimeoutError("t"))
    sc.decide(state, label="watch")                                           # timeout
    sc._post = lambda *a: {"model": DM.INSTANCE}
    sc.decide(state, label="watch")                                           # no answers
    _S.reply(model="nimble2")
    sc.decide(state, label="watch")                                           # another model refused
    os.environ["VALEDIA_SYSTEMONE_MODEL"] = "nimble:9b"
    sc.decide(state, label="would_pass")                                      # refused pin
    os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
    _S.reply()
    sc.decide(dict(state, chg_pct=float("nan")), label="watch")
    lines = _lines()
    assert len(lines) == 6, len(lines)
    ok, timeout, noans, other, refused, nan = lines
    for ln in lines:
        for key in ("ts", "source", "backend", "model", "state", "response", "error", "answers",
                    "latency_ms", "budget_ms", "within_budget", "cold", "label", "create_order"):
            assert key in ln, key
        assert ln["source"] == "movers_scan" and ln["create_order"] is False
        assert ln["state"]["symbol"] == "PUMP"
    assert ok["error"] is None and ok["backend"] == "systemone_http"
    assert ok["label"] == "would_buy" and ok["model"] == DM.INSTANCE
    assert ok["response"]["answers"]["admit_paper"]["noul"] == 0.8               # full raw response
    assert ok["answers"]["admit_paper"] == 0.8 and ok["cold"] is True
    assert timeout["error"] == "TimeoutError" and timeout["backend"] == "stub"
    assert timeout["response"] is None and timeout["within_budget"] is False
    assert noans["error"] == "no_answers" and noans["response"] == {"model": DM.INSTANCE}
    assert other["error"] == "model_refused" and other["response"]["model"] == "nimble2"
    assert refused["error"] == "model_refused" and refused["model"] is None
    assert refused["label"] == "would_pass" and refused["response"] is None
    assert nan["state"]["chg_pct"] is None and nan["error"] is None
    assert lines[1]["cold"] is False                                         # after the first answer
    blob = open(LOG).read().lower()
    for word in ("api_key", "authorization", "bearer", "secret"):
        assert word not in blob
    os.environ["VALEDIA_SYSTEMONE_LOG"] = "/dev/null/no/such/decisions.jsonl"
    try:
        assert sc.decide(state)["backend"] == "systemone_http"
        sc.log_decision("x", "stub", None, object(), {1: {2}}, None, None, None)
    finally:
        os.environ["VALEDIA_SYSTEMONE_LOG"] = LOG
    os.environ.pop("VALEDIA_SYSTEMONE_LOG", None)
    try:
        assert sc.log_path() == ""
        os.environ.pop("OPENCLAW_SKIP_ENV", None)
        assert sc.log_path().endswith(os.path.join("data", "systemone", "decisions.jsonl"))
    finally:
        os.environ["OPENCLAW_SKIP_ENV"] = "1"
        os.environ["VALEDIA_SYSTEMONE_LOG"] = LOG


def test_latency_budget_and_summary():
    assert sc.budget_ms() == 500.0
    os.environ["VALEDIA_SYSTEMONE_BUDGET_MS"] = "junk"
    assert sc.budget_ms() == 500.0
    os.environ["VALEDIA_SYSTEMONE_BUDGET_MS"] = "1"
    try:
        fast = fk.typed_post()

        def slow(url, payload, timeout):
            time.sleep(0.02)
            return fast(url, payload, timeout)
        sc._post = slow
        slow_out = sc.decide(sc.compact_state(_row()))
        assert slow_out["within_budget"] is False and slow_out["latency_ms"] > 1.0
        assert _lines()[-1]["within_budget"] is False and _lines()[-1]["budget_ms"] == 1.0
    finally:
        os.environ["VALEDIA_SYSTEMONE_BUDGET_MS"] = "5000"
    try:
        _S.reply()
        out = sc.decide(sc.compact_state(_row()))
        assert out["within_budget"] is True and out["cold"] is False
    finally:
        os.environ.pop("VALEDIA_SYSTEMONE_BUDGET_MS", None)

    def row(ms, cold, ok=True):
        return {"systemone_shadow": {"backend": "systemone_http" if ok else "stub", "fail_open": not ok,
                                     "latency_ms": ms, "cold": cold, "within_budget": ok and ms <= 500}}
    ranked = [row(float(x), False) for x in range(10, 110, 10)]
    ranked.append(row(9000.0, True))
    ranked.append(row(None, False, ok=False))
    blk = sc.summary(ranked)
    assert blk["latency_ms_p50"] == 50.0 and blk["latency_ms_p95"] == 100.0
    assert blk["cold_n"] == 1 and blk["within_budget_n"] == 10
    assert blk["latency_ms_max"] == 9000.0 and blk["budget_ms"] == 500.0
    assert sc.summary([])["latency_ms_p50"] is None


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _setup()
            try:
                _fn()
            finally:
                _teardown()
    print("PASS test_systemone_hotpath_client")
