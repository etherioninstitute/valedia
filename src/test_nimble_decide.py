#!/usr/bin/env python3
"""nimble_decide: questions, modes off/shadow/veto, exits, the instance check, logging, cap.

On the shared Elephant-1 instance: the typed call goes through the client's own transport (replaced here
with a typed fake), the list check through a fake list. Calibration is held until a map fitted on rows under
the decision's own key exists. No server, no network. Run: python3 test_nimble_decide.py
"""
import copy
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
for _k in list(os.environ):
    if _k.startswith("VALEDIA_NIMBLE") or _k.startswith("VALEDIA_SYSTEMONE"):
        os.environ.pop(_k)
LOG = os.path.join(tempfile.mkdtemp(prefix="nimdec_"), "decisions.jsonl")
os.environ["VALEDIA_SYSTEMONE_LOG"] = LOG

import calibration_ledger as cl  # noqa: E402
import nimble_decide as nd  # noqa: E402
import systemone_hotpath_client as s1  # noqa: E402
import systemone_test_fakes as fk  # noqa: E402
import valedia_config as vc  # noqa: E402

vc._MODELS = tempfile.mkdtemp(prefix="nimdec_models_")
import ere_test_fixture  # ERE v3: healthy BOOK -> phi 0.5 (was neutral 1.0 on missing file)
ere_test_fixture.write_state(vc._MODELS)
cl._reset_cache(items=[])  # no record: calibration is held

INFORMED = {"facts": {"mirofish_fresh": True, "mirofish_agrees": True, "news_present": False},
            "sources": {"mirofish": {"used": True, "age_s": 60}}}
CAND = {"symbol": "NVDA", "venue": "alpaca", "price": 123.45, "fused": 71.2}
FACTS = {"fused_strong": True, "filter_passed": True, "fee_ok": True, "already_held": False,
         "raw_fused": 71.2}


def _get(body=None):
    return fk.list_get(body)


def _post(choice="buy", conf=0.9, risk="3", calls=None, exc=None, p=0.9):
    """Typed fake: the chosen action gets probability p; conf None drops confidence; an unknown choice
    is sent as given (the typed parser refuses it)."""
    def fake(url, payload, timeout):
        if calls is not None:
            calls.append({"url": url, "payload": payload, "timeout": timeout})
        if exc:
            raise exc
        body = fk.typed_reply(payload["questions"],
                              {"action": choice if choice in nd.ACTIONS else "buy", "risk": risk},
                              confidence=conf, p=p)
        body["answers"]["action"]["choice"] = choice
        if conf is None:
            body["answers"]["action"].pop("confidence")
        return body
    return fake


_REAL_POST = s1._post

try:
    import pytest

    @pytest.fixture(autouse=True)
    def _restore_fakes():
        yield
        s1._post = _REAL_POST
        s1._reset_check()
        cl._reset_cache(items=[])
except ImportError:  # script mode
    pass


def _train(source="test"):
    """A fitted map under the decision's own key: p .9 -> .875, p .5 -> .5."""
    key = nd._cal_source(source)
    items = [{"source": key, "p": 0.9, "y": 1 if i < 27 else 0} for i in range(30)]
    items += [{"source": key, "p": 0.5, "y": i % 2} for i in range(30)]
    cl._reset_cache(items=items)
    return items


def _reset(mode=None, enabled=True, **env):
    for k in list(os.environ):
        if k.startswith("VALEDIA_NIMBLE"):
            os.environ.pop(k)
    if enabled:
        os.environ["VALEDIA_SYSTEMONE"] = "1"
    else:
        os.environ.pop("VALEDIA_SYSTEMONE", None)
    if mode:
        os.environ["VALEDIA_NIMBLE_MODE"] = mode
    os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)  # unset: the shared instance
    os.environ["VALEDIA_SYSTEMONE_LOG"] = LOG
    os.environ.update(env)
    s1._reset_check()
    s1._post = fk.refuse_post
    cl._reset_cache(items=[])
    nd._WARMED = True
    if os.path.exists(LOG):
        os.remove(LOG)


def _lines():
    if not os.path.exists(LOG):
        return []
    with open(LOG) as f:
        return [json.loads(x) for x in f if x.strip()]


def _decide(post, cls="stock", side="buy", cand=None, get=None):
    s1._post = post  # the client's own transport; an injected post is not honoured
    return nd.decide_candidate(cls, cand or CAND, side=side, code_facts=FACTS, informed=INFORMED,
                               source="test", label="would_buy", post=post, get=get or _get())


def test_questions_have_no_digits():
    for cls in nd.CLASSES:
        qs = nd.QUESTIONS[cls]
        assert set(qs) == {"action", "risk"}
        assert list(qs["action"]["criteria"]) == ["buy", "hold", "skip"]
        assert list(qs["risk"]["criteria"]) == [str(i) for i in range(1, 11)]
        for q in qs.values():
            texts = [q["instructions"]] + list(q["criteria"].values())
            for t in texts:
                assert not re.search(r"[0-9<>=%$]", t), (cls, t)
            if q is qs["action"]:
                assert not any(re.search(r"\d", k) for k in q["criteria"])


def test_modes_resolution():
    _reset()
    assert nd.mode_for("stock") == "shadow" and nd.mode_for("prediction") == "shadow"
    _reset(mode="veto", VALEDIA_NIMBLE_MODE_CRYPTO="off")
    assert nd.mode_for("stock") == "veto" and nd.mode_for("crypto") == "off"
    _reset(VALEDIA_NIMBLE_MODE_PREDICTION="VETO", VALEDIA_NIMBLE_MODE_STOCK="junk")
    assert nd.mode_for("prediction") == "veto" and nd.mode_for("stock") == "shadow"
    _reset(VALEDIA_NIMBLE_MIN_SHARPNESS="junk", VALEDIA_NIMBLE_TIMEOUT_S="-3")
    assert nd.min_sharpness() == 0.05 and nd.timeout_s() == 10.0
    assert nd.p_floor() == 0.60 and nd.base_margin() == 0.02


def test_off_no_call():
    _reset(mode="off")
    calls = []
    v = _decide(_post(choice="skip", calls=calls))
    assert v["allow"] is True and v["mode"] == "off" and not calls and not _lines()
    assert set(v) == {"action", "confidence", "risk", "mode", "allow", "reason",
                      "latency_ms", "within_budget", "error", "p_action", "p_buy",
                      "p_cal", "calibration", "required_p", "break_even_p", "margin",
                      "phi_multiplier", "phi_source", "rule_reason"}


def test_shadow_never_blocks():
    for post in (_post("skip"), _post("hold"), _post("buy", 0.1), _post(exc=TimeoutError()),
                 _post("banana")):
        _reset()
        v = _decide(post)
        assert v["allow"] is True and v["mode"] == "shadow", v
        assert len(_lines()) == 1
    _reset()
    v = _decide(_post(exc=TimeoutError()))
    assert v["error"] == "TimeoutError" and v["reason"] == "shadow_error"
    _reset(enabled=False)
    calls = []
    v = _decide(_post(calls=calls))
    assert v["allow"] is True and v["reason"] == "engine_disabled" and not calls


def test_veto_semantics():
    # No map under the decision's own key: every buy is held (no calibrated probability).
    cases = [(_post("buy", 0.9), False, "veto_no_p_buy"),
             (_post("buy", 0.9, p=0.5), False, "veto_no_p_buy"),
             (_post("buy", 0.01), False, "veto_not_sharp"),
             (_post("buy", None), False, "veto_engine_error"),  # typed: confidence is required
             (_post("hold", 0.9), False, "veto_model_hold"),
             (_post("skip", 0.9), False, "veto_model_skip"),
             (_post("banana", 0.9), False, "veto_engine_error"),
             (_post(exc=TimeoutError()), False, "veto_engine_error"),
             (_post(exc=ConnectionRefusedError()), False, "veto_engine_error")]
    for post, allow, reason in cases:
        _reset(mode="veto")
        v = _decide(post)
        assert v["allow"] is allow and v["reason"] == reason, (reason, v)
        if reason == "veto_no_p_buy":
            assert v["p_cal"] is None and v["calibration"] == "untrained_hold", v
    # A map fitted on rows under the decision's own key is applied.
    for p, allow, reason in ((0.9, True, "veto_model_buy"), (0.5, False, "veto_p_below_required")):
        _reset(mode="veto")
        _train()
        v = _decide(_post("buy", 0.9, p=p))
        assert v["allow"] is allow and v["reason"] == reason and v["calibration"] == "isotonic", v
    # The same rows under the old keys are never applied: held, as with an empty store.
    _reset(mode="veto")
    items = [dict(it, source=src) for it in _train()
             for src in ("nimble_decide:test", "nimble_decide:v3:%s:test" % ("0" * 64))]
    cl._reset_cache(items=items)
    v = _decide(_post("buy", 0.9))
    assert v["reason"] == "veto_no_p_buy" and v["calibration"] == "untrained_hold", v
    assert nd._calibrate(nd._cal_source("test"), 0.9) == {"p_cal": None, "calibration": "untrained_hold",
                                                          "n": 0}
    _reset(mode="veto")
    v = _decide(_post("buy", None))
    assert v["confidence"] is None and v["error"] == "ContractError"  # never filled from a probability
    # VALEDIA_NIMBLE_P_FLOOR may only RAISE the 0.60 floor: 0.4 is ignored, 0.7 binds
    _reset(mode="veto", VALEDIA_NIMBLE_P_FLOOR="0.4")
    _train()
    assert nd.p_floor() == 0.60 and _decide(_post("buy", 0.9, p=0.5))["allow"] is False
    _reset(mode="veto", VALEDIA_NIMBLE_P_FLOOR="0.7")
    _train()
    assert nd.p_floor() == 0.7 and _decide(_post("buy", 0.9, p=0.65))["allow"] is False
    assert _decide(_post("buy", 0.9, p=0.9))["allow"] is True
    _reset(mode="veto", enabled=False)
    v = _decide(_post())
    assert v["allow"] is False and v["error"] == "engine_disabled"


def test_exits_never_blocked():
    for mode in ("veto", "shadow", "off"):
        _reset(mode=mode)
        calls = []
        for side, cand, cls in (("sell", CAND, "stock"), ("exit", CAND, "crypto"),
                                ("yes", dict(CAND, action="sell"), "prediction"),
                                ("buy", dict(CAND, exit=True), "stock")):
            v = _decide(_post("skip", calls=calls), cls=cls, side=side, cand=cand)
            assert v["allow"] is True and v["reason"] == "exit_never_blocked", (mode, side)
        assert not calls
    _reset(mode="veto", enabled=False)
    assert _decide(_post(), side="sell")["allow"] is True


def test_instance_missing_or_wrong():
    for body, err in ((fk.listing(fk.entry(publisher="someone")), "instance_wrong"),
                      (fk.listing(fk.entry(instances=[])), "instance_missing")):
        for mode, allow in (("veto", False), ("shadow", True)):
            _reset(mode=mode)
            calls = []
            v = _decide(_post(calls=calls), get=_get(body))
            assert v["allow"] is allow and v["error"] == err and not calls, v
            rec = _lines()
            assert len(rec) == 1 and rec[0]["error"] == err


def test_logging_and_state():
    _reset()
    calls = []
    cand = copy.deepcopy(CAND)
    v = _decide(_post(calls=calls), cand=cand)
    assert cand == CAND, "candidate must not be mutated"
    assert v["latency_ms"] is not None and 9.0 < calls[0]["timeout"] <= 10.0
    rec = _lines()
    assert len(rec) == 1
    r = rec[0]
    assert r["source"] == "decide:elephant-1@q8_0:test" and r["label"]["code"] == "would_buy"
    assert r["model"] == "elephant-1" and calls[0]["payload"]["model"] == "elephant-1"
    assert r["label"]["mode"] == "shadow" and r["label"]["rule"]["min_sharpness"] == 0.05
    assert r["label"]["rule"]["required_p"] == 0.60 and r["label"]["rule"]["phi_multiplier"] == 0.5
    assert r["label"]["informed_sources"]["mirofish"]["used"] is True
    state = calls[0]["payload"]["state"]
    assert state["symbol"] == "NVDA" and state["venue"] == "alpaca" and state["side"] == "buy"
    assert "raw_fused" not in state["facts"] and "sources" not in state
    assert state["informed"] == INFORMED["facts"] and state["create_order"] is False

    def walk(x):
        if isinstance(x, dict):
            for val in x.values():
                walk(val)
        else:
            assert x is None or isinstance(x, (bool, str)), x
    walk(state)  # no raw numbers reach the model
    _reset(VALEDIA_NIMBLE_TIMEOUT_S="2.5")
    calls = []
    _decide(_post(calls=calls))
    assert 0 < calls[0]["timeout"] <= 2.5


def test_no_warmup_request():
    _reset()
    nd._WARMED = False
    s1._WARM = False
    calls = []
    _decide(_post(calls=calls))
    _decide(_post(calls=calls))
    srcs = [r["source"] for r in _lines()]
    assert srcs == ["decide:elephant-1@q8_0:test"] * 2, srcs
    assert len(calls) == 2


def test_informed_none_is_computed_offline():
    _reset()
    seen = []
    saved = nd.inf.informed_facts
    nd.inf.informed_facts = lambda sym, side, **k: seen.append((sym, side, k)) or {
        "facts": {"news_present": True}, "sources": {"news": {}}}
    try:
        calls = []
        s1._post = _post(calls=calls)
        nd.decide_candidate("crypto", {"ticker": "BTC-USD"}, side="buy", code_facts={},
                            source="t", post=_post(calls=calls), get=_get())
    finally:
        nd.inf.informed_facts = saved
    assert seen == [("BTC-USD", "buy", {"asset_class": "crypto"})]
    assert calls[0]["payload"]["state"]["informed"] == {"news_present": True}


def test_decide_many_cap():
    t = [0.0]

    def clock():
        return t[0]

    def post(url, payload, timeout):
        t[0] += 1.0
        return _post("skip")(url, payload, timeout)
    items = [{"candidate": {"symbol": s}, "side": "buy", "code_facts": {}, "informed": {}}
             for s in ("A", "B", "C", "D")]
    items.append({"candidate": {"symbol": "E"}, "side": "sell", "code_facts": {}})
    for mode, rest_allow in (("shadow", True), ("veto", False)):
        _reset(mode=mode)
        s1._post = post
        out = nd.decide_many("stock", items, source="many", cap_s=2.5, post=post, get=_get(),
                             clock=clock)
        assert len(out) == 5 and len(_lines()) == 3
        assert [v["reason"] for v in out[3:]] == [
            ("veto_cap_reached" if mode == "veto" else "cap_reached"), "exit_never_blocked"]
        assert out[3]["allow"] is rest_allow and out[4]["allow"] is True
        t[0] = 0.0


def test_fact_builders():
    row = {"ticker": "NVDA", "fused": 70.0, "has_pos": False, "ere": {"safe": True},
           "q": {"passes_filter": True, "p5": 0.02, "dom": "MOM"}, "fee_check": {"pass": True}}
    f = nd.stock_facts(row)
    assert f == {"fused_strong": True, "filter_passed": True, "fee_ok": True, "already_held": False,
                 "trend_up_5d": True, "momentum_dominant": True, "regime_risk_off": False}
    f = nd.crypto_facts({"fused": 56.0, "ere": {"safe": False}, "q": {"p5": -0.01}})
    assert f["fused_strong"] is False and f["regime_risk_off"] is True and f["trend_up_5d"] is False
    assert f["filter_passed"] is None and f["fee_ok"] is None and f["already_held"] is None
    assert all(v is None for v in nd.stock_facts({}).values())
    assert nd.crypto_facts({"regime": "EXTREME_FEAR"})["regime_risk_off"] is True
    opp = {"ticker": "KXHIGHNY-T70", "side": "yes", "ev_per_dollar": 0.12, "hours_to_resolve": 5,
           "informed": {"fv": 0.6, "fv_source": "model", "depth": 4, "bid": 0.40, "ask": 0.43}}
    p = nd.prediction_facts(opp)
    assert p["edge_positive"] and p["fv_source_allowed"] and p["clock_ok"] and p["depth_ok"]
    assert p["spread_tight"] and p["side_yes"] and p["fv_present"]
    assert p["fee_true_edge_positive"] is None and p["official_resolution"] is None
    p = nd.prediction_facts({"side": "no", "fv_source": "tag_prior", "spread": 12, "depth": 0.5,
                             "fee_true_edge": -0.01, "late_observe": True})
    assert p["fv_source_allowed"] is False and p["spread_tight"] is False and p["depth_ok"] is False
    assert p["side_yes"] is False and p["fee_true_edge_positive"] is False and p["clock_ok"] is False
    assert nd.prediction_facts({"fv_source": "market"}, poly=True)["fv_source_allowed"] is True
    assert nd.prediction_facts({"fv_source": "market"})["fv_source_allowed"] is False
    assert all(v is None for v in nd.prediction_facts({}).values())


def test_confidence_is_not_p_chosen():
    """The typed `confidence` is sharpness, NOT P(chosen): read as given, never replaced."""
    def typed(probs):
        def post(url, payload, timeout):
            return fk.typed_reply(payload["questions"], {"action": probs, "risk": "5"},
                                  confidence=0.00159)
        return post
    post = typed({"buy": 0.4765, "hold": 0.0, "skip": 0.5235})
    _reset(mode="shadow")
    v = _decide(post)
    assert v["action"] == "skip" and v["confidence"] == 0.00159
    assert v["p_action"] == 0.5235 and v["p_buy"] == 0.4765
    for k in ("action", "confidence", "risk", "mode", "allow", "reason", "latency_ms",
              "within_budget", "error"):  # existing keys kept
        assert k in v
    # veto: skip is vetoed; a "buy" at the same near-coin-flip confidence is not sharp
    _reset(mode="veto")
    v = _decide(post)
    assert v["allow"] is False and v["reason"] == "veto_model_skip" and v["p_buy"] == 0.4765

    v = _decide(typed({"buy": 0.5235, "hold": 0.0, "skip": 0.4765}))
    assert v["allow"] is False and v["reason"] == "veto_not_sharp"
    assert v["p_action"] == 0.5235 == v["p_buy"]

    # no probabilities: the typed parser refuses the reply; no invented odds
    def bare(url, payload, timeout):
        body = fk.typed_reply(payload["questions"], {"action": "buy"}, confidence=0.9)
        body["answers"]["action"].pop("probabilities")
        return body
    v = _decide(bare)
    assert v["p_action"] is None and v["p_buy"] is None and v["allow"] is False
    assert v["reason"] == "veto_engine_error" and v["error"] == "ContractError"
    # engine off / exit: keys present, None
    _reset(mode="veto", enabled=False)
    v = _decide(post)
    assert v["p_action"] is None and v["p_buy"] is None


if __name__ == "__main__":
    test_questions_have_no_digits()
    test_modes_resolution()
    test_off_no_call()
    test_shadow_never_blocks()
    test_veto_semantics()
    test_exits_never_blocked()
    test_instance_missing_or_wrong()
    test_logging_and_state()
    test_no_warmup_request()
    test_informed_none_is_computed_offline()
    test_decide_many_cap()
    test_fact_builders()
    test_confidence_is_not_p_chosen()
    print("test_nimble_decide: OK")
