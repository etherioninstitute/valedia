"""Nimble 2 digest pin: nimble / nimble:latest only when /api/tags shows the 24e550a16a70 digest."""
import pytest

from fakes import NIMBLE_DIGEST_FULL, FakeNimble, nimble_tags
from rh_meme_bot import nimble_pin
from rh_meme_bot.config import load_config
from rh_meme_bot.decision_engine import NIMBLE2_DIGEST, NimbleEngine, engine_from_config

STATE = {"symbol": "MEME", "owner_renounced": True, "contract_has_code": True}


def engine(fake=None, **kw):
    fake = fake or FakeNimble()
    return NimbleEngine(transport=fake, **kw), fake


def test_constant_and_default_model():
    assert NIMBLE2_DIGEST == "24e550a16a70"
    cfg = load_config(env={})
    assert cfg.nimble_model == "nimble:latest" and cfg.nimble_digest == "24e550a16a70"
    assert load_config(env={"NIMBLE_DIGEST": "ABCDEF012345"}).nimble_digest == "abcdef012345"


@pytest.mark.parametrize("model", ["nimble:latest", "nimble", "Nimble:Latest"])
def test_digest_match_accepts_latest_and_bare(model):
    eng, fake = engine(model=model)
    assert eng.config_problem() is None
    res = eng.warmup()
    assert res["error"] is None and len(fake.requests) == 1
    assert fake.tag_requests[0][0] == "http://127.0.0.1:11434/api/tags"
    assert eng.decide(STATE)["error"] is None
    assert len(fake.tag_requests) == 1  # verified once, cached for the process


def test_digest_mismatch_refuses_and_never_posts():
    fake = FakeNimble()
    fake.tags = nimble_tags(digest="deadbeef0000" + "00" * 26)
    eng, _ = engine(fake)
    res = eng.warmup()
    assert res["action"] == "skip" and res["error"].startswith("digest_mismatch")
    out = eng.decide(STATE)
    assert out["action"] == "skip" and out["error"].startswith("digest_mismatch")
    assert fake.requests == [] and len(fake.tag_requests) == 1  # mismatch is cached


@pytest.mark.parametrize("tags", [
    {"models": []},                                  # model not pulled
    nimble_tags(name="nimble:9b"),                   # only a different tag present
    ConnectionError("ollama down"),                  # tags endpoint unreachable
    ["not", "a", "dict"],                            # garbage body
    nimble_tags(digest=""),                          # no digest reported
])
def test_digest_unverified_refuses(tags):
    fake = FakeNimble()
    fake.tags = tags
    eng, _ = engine(fake)
    out = eng.decide(STATE)
    assert out["action"] == "skip" and fake.requests == []
    assert out["error"].startswith("digest_unverified")


@pytest.fixture
def clock(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(nimble_pin, "_now", lambda: t[0])
    return t


def test_failures_held_for_retry_window_then_retried(clock):
    fake = FakeNimble()
    fake.tags = ConnectionError("starting up")
    eng, _ = engine(fake)
    assert eng.warmup()["error"].startswith("digest_unverified")
    fake.tags = nimble_tags()
    clock[0] += nimble_pin.DIGEST_RETRY_S - 1  # inside the 30 s window: cached failure
    assert eng.decide(STATE)["error"].startswith("digest_unverified")
    assert len(fake.tag_requests) == 1 and fake.requests == []
    clock[0] += 2  # window over: re-verified, recovers
    assert eng.decide(STATE)["error"] is None and len(fake.tag_requests) == 2
    # a mismatch is retried after the window too (e.g. after `ollama pull` of the right one)
    nimble_pin.clear_cache()
    fake.tags = nimble_tags(digest="deadbeef0000" + "00" * 26)
    assert eng.decide(STATE)["error"].startswith("digest_mismatch")
    fake.tags = nimble_tags()
    assert eng.decide(STATE)["error"].startswith("digest_mismatch")  # held
    clock[0] += nimble_pin.DIGEST_RETRY_S
    assert eng.decide(STATE)["error"] is None


def test_ok_expires_after_ttl_and_repull_is_caught(clock, caplog):
    fake = FakeNimble()
    eng, _ = engine(fake)
    assert eng.decide(STATE)["error"] is None and len(fake.requests) == 1
    fake.tags = nimble_tags(digest="badc0ffee000" + "11" * 26)  # someone re-pulled nimble
    clock[0] += nimble_pin.DIGEST_OK_TTL_S - 1
    assert eng.decide(STATE)["error"] is None and len(fake.tag_requests) == 1  # still trusted
    clock[0] += 2  # past the 300 s TTL: re-verified
    out = eng.decide(STATE)
    assert out["action"] == "skip" and out["error"].startswith("digest_mismatch")
    assert len(fake.requests) == 2 and len(fake.tag_requests) == 2  # no POST after mismatch
    assert any("digest_mismatch" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("raw,want", [("", 300.0), ("junk", 300.0), ("0", 300.0),
                                      ("-5", 300.0), ("inf", 300.0), ("nan", 300.0),
                                      ("60", 60.0), ("1.5", 1.5)])
def test_digest_ttl_env_parsing(raw, want):
    assert nimble_pin.digest_ok_ttl(raw) == want
    assert load_config(env={"NIMBLE_DIGEST_TTL_S": raw}).nimble_digest_ttl_s == want


def test_config_ttl_reaches_engine(clock):
    fake = FakeNimble()
    eng = engine_from_config(load_config(env={"NIMBLE_DIGEST_TTL_S": "10"}), fake)
    assert eng.digest_ttl_s == 10.0 and eng.decide(STATE)["error"] is None
    clock[0] += 11
    eng.decide(STATE)
    assert len(fake.tag_requests) == 2


def test_nimble_digest_accepts_sha256_prefix():
    cfg = load_config(env={"NIMBLE_DIGEST": "sha256:24E550A16A70"})
    assert cfg.nimble_digest == "24e550a16a70"
    assert engine_from_config(cfg, FakeNimble()).decide(STATE)["error"] is None


def test_explicit_v2_names_skip_digest_and_v1_tags_refused():
    eng, fake = engine(model="nimble2:q4_k_m")
    assert eng.decide(STATE)["error"] is None and fake.tag_requests == []
    for bad in ("nimble:9b", "nimble:v1", "nimble:1", "nimble:latest-q4"):
        eng, fake = engine(model=bad)
        assert "not a Nimble 2 tag" in eng.config_problem()
        assert eng.decide(STATE)["action"] == "skip" and fake.requests == []


def test_digest_override_from_config_and_sha256_prefix():
    fake = FakeNimble()
    fake.tags = nimble_tags(digest="sha256:" + "0123456789ab" + "cd" * 26)
    cfg = load_config(env={"NIMBLE_DIGEST": "0123456789ab"})
    eng = engine_from_config(cfg, fake)
    assert eng.decide(STATE)["error"] is None
    nimble_pin.clear_cache()
    eng2 = engine_from_config(load_config(env={}), fake)  # default 24e550a16a70 != 0123...
    assert eng2.decide(STATE)["error"].startswith("digest_mismatch")


def test_response_model_must_match_the_pinned_tag():
    from fakes import nimble_body
    eng, _ = engine(FakeNimble(respond=lambda p: nimble_body(model="nimble:latest")),
                    model="nimble")
    assert eng.decide(STATE)["error"] is None  # nimble == nimble:latest
    eng, _ = engine(FakeNimble(respond=lambda p: nimble_body(model="nimble:9b")))
    assert "not Nimble 2" in eng.decide(STATE)["error"]


def test_custom_transport_without_get_is_unverified_not_networked():
    calls = []
    eng = NimbleEngine(transport=lambda url, payload, timeout: calls.append(1))
    out = eng.decide(STATE)
    assert out["error"].startswith("digest_unverified") and calls == []
    assert NIMBLE_DIGEST_FULL.startswith(NIMBLE2_DIGEST)
