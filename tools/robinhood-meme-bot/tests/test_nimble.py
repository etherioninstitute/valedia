import json
import re

import pytest

from fakes import FakeClock, FakeNimble, nimble_body
from rh_meme_bot.decision_engine import (QUESTIONS, RISK_LABELS, NimbleEngine, ask_nimble,
                                         is_nimble2, sanitize)

STATE = {"symbol": "MEME", "liquidity_usd": 50000.0, "owner_renounced": True,
         "contract_has_code": True}


def engine(fake=None, clock=None, **kw):
    fake = fake or FakeNimble(clock=clock)
    return NimbleEngine(transport=fake, clock=clock or FakeClock(), **kw), fake


@pytest.mark.parametrize("tag,ok", [
    ("nimble2", True), ("Nimble2", True), ("nimble2:q4_k_m", True), ("nimble:v2", True),
    ("bespokelabs/Bespoke-Nimble-9B-v2", True), ("nimble", False), ("nimble:9b", False),
    ("nimble:latest", False), ("jev-latest", False), ("nimble2-v1", False), ("tev", False),
    ("", False), (None, False)])
def test_is_nimble2(tag, ok):
    assert is_nimble2(tag) is ok


def test_request_shape_and_pin():
    eng, fake = engine()
    out = eng.decide(STATE)
    (url, payload, timeout), = fake.requests
    assert url == "http://127.0.0.1:11434/v1/systemone" and timeout == 2.0
    assert payload["model"] == "nimble2" and payload["keep_alive"] == "30m"
    assert payload["state"] == STATE and payload["questions"] == QUESTIONS
    assert set(payload) == {"model", "state", "questions", "keep_alive"}  # no temperature etc.
    assert out["error"] is None and out["model"] == "nimble2"


def test_questions_contain_no_thresholds_and_risk_labels_are_choice():
    texts = []

    def walk(q):
        texts.append(q["instructions"])
        texts.extend(q["criteria"].values())  # label keys are excluded on purpose

    for q in QUESTIONS.values():
        assert q["type"] == "choice"
        walk(q)
    blob = " ".join(texts)
    assert not re.search(r"20000|20,000|0\.7|\b4\b|\b5\b", blob)
    assert list(QUESTIONS["risk_score"]["criteria"]) == RISK_LABELS == [str(i) for i in range(1, 11)]
    assert set(QUESTIONS["action"]["criteria"]) == {"buy", "hold", "skip"}
    assert "momentum" not in blob.lower() and "pressure" not in blob.lower()
    full = json.dumps(QUESTIONS)
    assert "20000" not in full and "0.7" not in full


def test_response_parsing_and_risk_expected():
    body = nimble_body("buy", 0.93, "2")
    body["answers"]["risk_score"]["probabilities"] = {"2": 0.6, "3": 0.4}
    eng, _ = engine(FakeNimble(respond=lambda p: body))
    out = eng.decide(STATE)
    assert out["action"] == "buy" and out["confidence"] == 0.93
    assert out["risk_score"] == 2 and isinstance(out["risk_score"], int)
    assert out["risk_expected"] == pytest.approx(2.4)
    assert out["raw"] == body and out["error"] is None


@pytest.mark.parametrize("mutate,reason", [
    (lambda b: b.pop("answers"), "no answers"),
    (lambda b: b["answers"].pop("action"), "invalid action"),
    (lambda b: b["answers"]["action"].update(choice="moon"), "invalid action"),
    (lambda b: b["answers"]["action"].update(confidence=1.5), "confidence"),
    (lambda b: b["answers"]["action"].update(confidence=float("nan")), "confidence"),
    (lambda b: b["answers"]["action"].update(confidence="high"), "confidence"),
    (lambda b: b["answers"]["risk_score"].update(choice="11"), "risk label"),
    (lambda b: b["answers"]["risk_score"].update(choice="0"), "risk label"),
    (lambda b: b["answers"].pop("risk_score"), "risk label"),
    (lambda b: b.update(model="nimble"), "not Nimble 2"),
])
def test_malformed_responses_fail_closed(mutate, reason):
    body = nimble_body("buy", 0.9, "2")
    mutate(body)
    out = engine(FakeNimble(respond=lambda p: body))[0].decide(STATE)
    assert out["action"] == "skip" and out["confidence"] == 0.0 and out["risk_score"] is None
    assert reason in out["error"]


@pytest.mark.parametrize("exc", [TimeoutError("timed out"), ConnectionError("refused"),
                                 ValueError("bad json")])
def test_transport_failures_fail_closed(exc):
    fake = FakeNimble()
    fake.fail = exc
    out = engine(fake)[0].decide(STATE)
    assert out["action"] == "skip" and out["error"].startswith(type(exc).__name__)
    assert out["latency_ms"] is not None


def test_non_dict_response_fails_closed():
    out = engine(FakeNimble(respond=lambda p: ["x"]))[0].decide(STATE)
    assert out["action"] == "skip" and "not a JSON object" in out["error"]


def test_refused_pin_never_posts():
    for bad in ("nimble", "jev-latest", "nimble:9b"):
        eng, fake = engine(model=bad)
        out = eng.decide(STATE)
        assert out["action"] == "skip" and "Nimble 2" in out["error"] and fake.requests == []
        assert eng.config_problem()


def test_non_loopback_url_refused_unless_allowed():
    url = "http://10.0.0.5:11434/v1/systemone"
    eng, fake = engine(url=url)
    out = eng.decide(STATE)
    assert "not loopback" in out["error"] and fake.requests == []
    eng, fake = engine(url=url, allow_remote=True)
    assert eng.decide(STATE)["error"] is None and len(fake.requests) == 1
    for ok in ("http://localhost:11434/x", "http://127.0.0.1:1/x", "http://[::1]:11434/x"):
        assert engine(url=ok)[0].config_problem() is None


def test_nan_inf_coerced_to_null():
    eng, fake = engine()
    from decimal import Decimal
    state = {"a": float("nan"), "b": float("inf"), "c": [float("-inf"), 1.5], "d": Decimal("2.5"),
             "e": {"f": float("nan")}, 3: "int key"}
    out = eng.decide(state)
    sent = fake.requests[0][1]["state"]
    assert sent == {"a": None, "b": None, "c": [None, 1.5], "d": 2.5, "e": {"f": None},
                    "3": "int key"}
    json.dumps(fake.requests[0][1], allow_nan=False)
    assert out["error"] is None
    assert sanitize(float("nan")) is None


def test_warmup_is_one_tiny_call_and_reports_latency():
    clock = FakeClock()
    eng, fake = engine(FakeNimble(clock=clock, step_ms=230), clock=clock)
    res = eng.warmup()
    assert len(fake.requests) == 1 and fake.requests[0][1]["state"] == {"warmup": True}
    assert res["latency_ms"] == pytest.approx(230.0)
    assert res["error"] is None
    fake.fail = ConnectionError("down")
    assert eng.warmup()["error"].startswith("ConnectionError")


def test_latency_measured_with_injected_clock():
    clock = FakeClock()
    eng, _ = engine(FakeNimble(clock=clock, step_ms=640), clock=clock)
    assert eng.decide(STATE)["latency_ms"] == pytest.approx(640.0)


def test_ask_nimble_module_function_refuses_bad_model(monkeypatch):
    monkeypatch.delenv("NIMBLE_MODEL", raising=False)
    from rh_meme_bot import decision_engine as de
    monkeypatch.setattr(de, "_DEFAULT", NimbleEngine(transport=FakeNimble()))
    assert ask_nimble(STATE, QUESTIONS)["model"] == "nimble2"
    with pytest.raises(de.NimbleError, match="refused"):
        ask_nimble(STATE, QUESTIONS, model="nimble")
