#!/usr/bin/env python3
"""systemone_pin: one model, the shared Elephant-1 instance; the pin is the shared module's list test.

Ruling A (the model env), ruling B (the list test with its cache windows), the shared module reached by file,
and ruling H (a search of the changed source files). Fake lists only: no server, no network.
Run: python3 test_systemone_pin.py   (or pytest)
"""
import contextlib
import io
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
for _k in ("VALEDIA_SYSTEMONE", "VALEDIA_SYSTEMONE_MODEL", "VALEDIA_SYSTEMONE_DIGEST",
           "VALEDIA_SYSTEMONE_DIGEST_TTL_S", "VALEDIA_SYSTEMONE_BUDGET_MS"):
    os.environ.pop(_k, None)
LOG = os.path.join(tempfile.mkdtemp(prefix="s1pin_"), "decisions.jsonl")

import systemone_hotpath_client as sc  # noqa: E402
import systemone_pin as pin  # noqa: E402
import systemone_test_fakes as fk  # noqa: E402

DM = pin.dm
ROOT = os.path.dirname(HERE)
_S = fk.Scoped(sc, LOG)
_scoped_fakes = _S.fixture()


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _with_clock(fn):
    clock, saved = Clock(), pin.time.monotonic
    pin.time.monotonic = clock
    try:
        fn(clock)
    finally:
        pin.time.monotonic = saved


def _counting(body):
    seen = []
    return fk.list_get(body, seen), seen


def _env(**kv):
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ---------------------------------------------------------------- the shared module, by file
def test_shared_module_loaded_by_file_from_the_same_tree():
    assert DM is not None and pin.DM_ERROR is None
    assert os.path.realpath(DM.__file__) == os.path.realpath(
        os.path.join(ROOT, "valedia_trader", "core", "decision_model.py"))
    assert os.path.realpath(DM.__file__).startswith(os.path.realpath(ROOT) + os.sep)
    assert "valedia_decision_model_by_file" not in sys.modules       # nothing registered
    # one implementation: the pin's constants are the shared module's own values
    assert pin.LIST_URL == DM.URL + "/api/v1/models"
    assert pin.SOURCE_PREFIX == "decide:%s@%s" % (DM.INSTANCE, DM.QUANT.lower()) == "decide:elephant-1@q8_0"
    src = open(os.path.join(HERE, "systemone_pin.py")).read()
    for literal in ("127.0.0.1", "1235", '"elephant-1"', "debackerl", "Q8_0", "8192", "2048",
                    "def _instances", "def _wrong_found", "loaded_instances"):
        assert literal not in src, literal                            # no second copy
    assert "sys.path.insert" not in src and "sys.path.append" not in src  # nothing put on sys.path


def test_unloadable_shared_module_refuses_and_never_raises():
    tmp = tempfile.mkdtemp(prefix="s1pin_dm_")
    missing = os.path.join(tmp, "nope", "decision_model.py")
    mod, why = pin._load_dm(missing)
    assert mod is None and "\n" not in why and "FileNotFoundError" in why
    broken = os.path.join(tmp, "decision_model.py")
    with open(broken, "w") as f:
        f.write("URL = (\n")
    mod, why = pin._load_dm(broken)
    assert mod is None and "SyntaxError" in why
    with open(broken, "w") as f:
        f.write("URL = 'x'\n")                                        # loads, but is not the module
    assert pin._load_dm(broken)[0] is None
    unreadable = os.path.join(tmp, "copy.py")
    shutil.copy(os.path.join(ROOT, "valedia_trader", "core", "decision_model.py"), unreadable)
    assert pin._load_dm(unreadable)[0] is not None                    # a good copy loads
    os.chmod(unreadable, 0)
    try:
        if os.geteuid() != 0:
            assert pin._load_dm(unreadable)[0] is None
    finally:
        os.chmod(unreadable, 0o600)
    # the chain with no shared module: every pin refuses, no request is made, nothing raises
    saved = (pin.dm, pin.DM_ERROR)
    pin.dm, pin.DM_ERROR = None, why
    os.environ["VALEDIA_SYSTEMONE"] = "1"
    try:
        get, seen = _counting(None)
        assert pin.configured_model() == "" and pin.model_pin() == ""
        assert pin.verify(get) == "decision_model_unavailable" and seen == []
        assert pin.pin_refusal() == "decision_model_unavailable"
        assert pin.refusal_reason() == why
        res = sc.ask({"symbol": "X"}, sc.WARM_QUESTIONS, source="t", get=get)
        assert res["error"] == "decision_model_unavailable" and res["refusal"] == why
        assert seen == [] and _S.posts == []
    finally:
        pin.dm, pin.DM_ERROR = saved
        os.environ.pop("VALEDIA_SYSTEMONE", None)


# ---------------------------------------------------------------- ruling A: one model
def test_model_env_unset_or_instance_accepted():
    for value in (None, "", "elephant-1", "  elephant-1  "):
        _env(VALEDIA_SYSTEMONE_MODEL=value)
        pin._reset_check()
        assert pin.configured_model() == DM.INSTANCE, value
        assert pin.model_pin() == ""                                  # nothing checked yet
        get, seen = _counting(None)
        assert pin.verify(get) == "ok" and seen == [pin.LIST_URL]
        assert pin.model_pin() == DM.INSTANCE and pin.pin_refusal() is None
        assert pin.refusal_reason() is None
    _env(VALEDIA_SYSTEMONE_MODEL=None)


def test_model_env_anything_else_refused_with_one_line():
    for value in ("nimble-v3:9b-mxfp8", "nimble:latest", "nimble", "nimble2", "nimble:9b",
                  "bespoke-nimble-9b-v2", "tev1:0.8b", "Elephant-1", "elephant-1:latest",
                  "elephant-1@q8_0", "lmstudio:elephant-1@q8_0", "elephant", "x" * 300, "elephant-1 2"):
        _env(VALEDIA_SYSTEMONE_MODEL=value)
        pin._reset_check()
        get, seen = _counting(None)
        assert pin.configured_model() == "", value
        assert pin.verify(get) == "model_refused" and seen == [], value   # no request
        assert pin.model_pin() == "" and pin.pin_refusal() == "model_refused"
        why = pin.refusal_reason()
        assert "\n" not in why and "elephant-1" in why and len(why) < 200, why
    _env(VALEDIA_SYSTEMONE_MODEL=None)


def test_digest_env_ignored_with_one_note():
    pin._NOTED["deprecated"] = False
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        for value in ("24e550a16a70", "sha256:" + "0" * 64, "junk"):
            _env(VALEDIA_SYSTEMONE_DIGEST=value)
            pin._reset_check()
            get, seen = _counting(None)
            assert pin.configured_model() == DM.INSTANCE
            assert pin.verify(get) == "ok" and pin.model_pin() == DM.INSTANCE
            bad, _ = _counting(fk.listing(fk.entry(publisher="someone")))
            pin._reset_check()
            assert pin.verify(bad) == "instance_wrong" and pin.model_pin() == ""
    lines = [x for x in err.getvalue().splitlines() if x.strip()]
    assert len(lines) == 1 and "VALEDIA_SYSTEMONE_DIGEST" in lines[0] and "ignored" in lines[0], lines
    _env(VALEDIA_SYSTEMONE_DIGEST=None)


# ---------------------------------------------------------------- ruling B: the list test
def _status(body):
    pin._reset_check()
    get, seen = _counting(body)
    st = pin.verify(get)
    assert seen == [pin.LIST_URL]
    return st


def test_list_right_missing_wrong_two_malformed():
    assert _status(fk.listing()) == "ok"
    assert _status(fk.listing(fk.entry(), fk.entry(key="other", loaded_instances=[]))) == "ok"
    # missing
    assert _status({"models": []}) == "instance_missing"
    assert _status(fk.listing(fk.entry(instances=[]))) == "instance_missing"
    assert _status(fk.listing(fk.entry(instances=[dict(fk.instance(), id="elephant-1:2")]))) == \
        "instance_missing"
    # each wrong setting of the entry and of the instance
    for over in ({"key": "elephant-2"}, {"type": "llm"}, {"decision_type": "other"},
                 {"publisher": "someone"}, {"quantization": {"name": "Q4_K_M"}},
                 {"quantization": "Q8_0"}, {"size_bytes": 123}, {"size_bytes": "10151733984"}):
        assert _status(fk.listing(fk.entry(**over))) == "instance_wrong", over
        assert pin.model_pin() == ""
    for cfg in ({"context_length": 4096}, {"parallel": 2}, {"physical_batch_size": 512},
                {"eval_batch_size": 512}, {"context_length": 8192.0}, {"parallel": True}):
        assert _status(fk.listing(fk.entry(instances=[fk.instance(**cfg)]))) == "instance_wrong", cfg
    assert _status(fk.listing(fk.entry(instances=[{"id": DM.INSTANCE}]))) == "instance_wrong"
    # two instances named INSTANCE, in one entry or across two
    assert _status(fk.listing(fk.entry(instances=[fk.instance(), fk.instance()]))) == "instance_wrong"
    assert _status(fk.listing(fk.entry(), fk.entry(key="copy"))) == "instance_wrong"
    # malformed
    for body in (None, [], "x", {}, {"models": {}}, {"models": ["x"]},
                 {"models": [dict(fk.entry(), loaded_instances="x")]},
                 {"models": [dict(fk.entry(), loaded_instances=["x"])]}):
        assert _status(body if body is not None else {"models": None}) == "instance_unverified", body
        assert pin.model_pin() == ""
    # unreachable
    pin._reset_check()

    def down(url, timeout):
        raise ConnectionRefusedError("down")
    assert pin.verify(down) == "instance_unverified" and pin.model_pin() == ""
    assert "unreachable" in pin.refusal_reason() and "\n" not in pin.refusal_reason()


def test_cache_windows_and_ttl_override():
    def run(clock):
        pin._reset_check()
        get, seen = _counting(None)
        assert pin.verify(get) == "ok" and len(seen) == 1
        clock.t += pin.CHECK_OK_TTL_S - 1
        assert pin.verify(get) == "ok" and len(seen) == 1             # trusted inside its TTL
        assert pin.model_pin() == DM.INSTANCE
        clock.t += 2
        assert pin.check_status() == "instance_unverified" and pin.model_pin() == ""   # expired
        assert pin.verify(get) == "ok" and len(seen) == 2             # checked again
        assert pin.verify(get, force=True) == "ok" and len(seen) == 3
        # a failed check is not retried inside its window
        bad, bseen = _counting({"models": []})
        assert pin.verify(bad, force=True) == "instance_missing" and len(bseen) == 1
        clock.t += pin.CHECK_RETRY_S - 1
        assert pin.verify(get) == "instance_missing" and len(seen) == 3
        assert pin.model_pin() == "" and pin.pin_refusal() == "instance_missing"
        clock.t += 2
        assert pin.verify(get) == "ok" and len(seen) == 4
        # the TTL override (kept env name); junk or non-positive -> default
        os.environ["VALEDIA_SYSTEMONE_DIGEST_TTL_S"] = "5"
        try:
            assert pin.check_ttl() == 5.0
            clock.t += 6
            assert pin.model_pin() == ""
            for junk in ("0", "-1", "nan", "inf", "x"):
                os.environ["VALEDIA_SYSTEMONE_DIGEST_TTL_S"] = junk
                assert pin.check_ttl() == pin.CHECK_OK_TTL_S, junk
        finally:
            os.environ.pop("VALEDIA_SYSTEMONE_DIGEST_TTL_S", None)
    _with_clock(run)


# ---------------------------------------------------------------- ruling H: a search
CHANGED = ("systemone_pin.py", "systemone_hotpath_client.py", "systemone_test_fakes.py",
           "nimble_decide.py", "nimble_annotate.py", "nimble_wiring.py", "nimble_wiring_spot.py",
           "calibration_ledger.py", "valedia_config.py")
FORBIDDEN = ("11434", "/api/tags", "/api/version", "nimble-v3", "nimble:latest", "keep_alive")


def test_no_old_endpoint_tag_or_digest_constant_in_changed_sources():
    for name in CHANGED:
        src = open(os.path.join(HERE, name)).read()
        for needle in FORBIDDEN:
            assert needle not in src, (name, needle)
        assert not re.search(r"^\s*[A-Z_]*(DIGEST|MANIFEST)[A-Z_0-9]*\s*=", src, re.M), name
        assert not re.search(r"['\"][0-9a-f]{12,}['\"]", src), name  # no digest or hash literal
        if name not in ("calibration_ledger.py", "valedia_config.py"):  # config: MANUSCRIPT_MANIFEST.md
            assert "MANIFEST" not in src, name
    # the one exception (ruling 8): calibration_ledger classifies rows written before today
    led = open(os.path.join(HERE, "calibration_ledger.py")).read().splitlines()
    uses = [i for i, ln in enumerate(led) if "MANIFEST" in ln]
    assert uses and all('src.startswith("nimble_decide:v3:")' in led[i - 2] for i in uses), uses


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _S.setup()
            try:
                _fn()
            finally:
                _S.teardown()
    print("PASS test_systemone_pin")
