"""Child processes never inherit the trading file's variables. All values are made up; checks
compare names, booleans and counts, never a value."""
import json
import os
import random
import string
import sys

import pytest

from valedia_trader.agents import desk as D
from valedia_trader.agents import panel as P
from valedia_trader.core import secrets as s
from valedia_trader.data_and_fees import timesfm_forecaster as tf
from valedia_trader.research_loop import price_models as pm

PRINT_NAMES = "import json, os, sys; print(json.dumps(sorted(os.environ)))"


def _made_up(rng, n=32):
    return "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(n))


@pytest.fixture
def trading(tmp_path, monkeypatch):
    """A trading file loaded into this process's environment, a credential served by `secret`, a
    variable that only repeats a secret's value, and an unrelated variable set by the test."""
    rng = random.Random(1)
    key = _made_up(rng)
    names = {"MADE_UP_BROKER_KEY": key, "MADE_UP_BROKER_SECRET": _made_up(rng),
             "MADE_UP_ACCOUNT_ID": "".join(rng.choice(string.digits) for _ in range(10)), "MADE_UP_BROKER_ENV": "sandbox"}
    f = tmp_path / "trading.env"
    f.write_text("".join(f"{k}={v}\n" for k, v in names.items()))
    os.chmod(f, 0o600)
    for name in names:
        monkeypatch.setenv(name, "x")                                 # recorded, so teardown removes what the load sets
    s.load_env(f, override=True)
    already = tmp_path / "already.env"
    already.write_text(f"MADE_UP_ALREADY_KEY={_made_up(rng)}\n")
    os.chmod(already, 0o600)
    monkeypatch.setenv("MADE_UP_ALREADY_KEY", _made_up(rng))         # already set: left alone, still not passed on
    s.load_env(already)
    monkeypatch.setenv("MADE_UP_SERVED_TOKEN", _made_up(rng))
    s.secret("MADE_UP_SERVED_TOKEN")
    monkeypatch.setenv("MADE_UP_COPY", key)                           # an unrelated name holding a secret's value
    monkeypatch.setenv("UNRELATED_TEST_VAR", "kept")
    return set(names) | {"MADE_UP_ALREADY_KEY", "MADE_UP_SERVED_TOKEN", "MADE_UP_COPY"}


def _clean(names_seen, hidden):
    return not (set(names_seen) & hidden) and "UNRELATED_TEST_VAR" in names_seen and "PATH" in names_seen


def test_child_env_removes_the_trading_variables_and_nothing_else(trading):
    env = s.child_env({"EXTRA_FOR_CHILD": "1", "MADE_UP_BROKER_KEY": "sneaked-back-in-value"})
    others = {k for k in os.environ if k not in trading}
    ok = _clean(env, trading) and env.get("EXTRA_FOR_CHILD") == "1" and others <= set(env)
    same = all(env[k] == os.environ[k] for k in others)
    assert ok and same


def test_a_hermes_panel_member_sees_none_of_them(trading):
    out = P.HermesProfileMember._subprocess([sys.executable, "-c", PRINT_NAMES], 60)
    ok = _clean(json.loads(out), trading)
    assert ok


def test_the_desk_sees_none_of_them(trading, tmp_path, monkeypatch):
    fake = tmp_path / "bin" / "hermes"
    fake.parent.mkdir()
    fake.write_text(f"#!{sys.executable}\n{PRINT_NAMES}\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    out = D.hermes_runner("researcher", {"profile": "p", "timeout": 60}, "prompt")
    ok = _clean(json.loads(out), trading)
    assert ok


def test_the_kronos_worker_sees_none_of_them(trading, tmp_path):
    seen = tmp_path / "names.json"
    script = tmp_path / "worker.py"
    script.write_text(f"import json, os, sys\nopen({str(seen)!r}, 'w').write(json.dumps(sorted(os.environ)))\n"
                      "print(json.dumps({'ready': True, 'device': 'cpu'}), flush=True)\nsys.stdin.read()\n")
    fc = pm.KronosForecaster(name="stand-in", worker_argv=[sys.executable, str(script)])
    try:
        fc._start()
    finally:
        fc.close()
    ok = _clean(json.loads(seen.read_text()), trading)
    assert ok


def test_the_timesfm_worker_sees_none_of_them(trading, tmp_path, monkeypatch):
    (tmp_path / "model.safetensors").write_bytes(b"x" * 16)
    fake = tf.Checkpoint("x", "x/y", "rev", tmp_path, "model.safetensors", 16, "0" * 64, "apache-2.0", "2025-09-02")
    script = tmp_path / "worker.py"
    script.write_text("import json, os, sys\nfrom pathlib import Path\n"
                      "Path(sys.argv[1], 'names.json').write_text(json.dumps(sorted(os.environ)))\n"
                      f"print({tf._MARK!r} + ' ready', flush=True)\nsys.stdin.read()\n")
    monkeypatch.setattr(tf, "WORKER", script)
    backend = tf.TimesFMBackend(fake)
    try:
        backend._start()
    finally:
        backend.close()
    ok = _clean(json.loads((tmp_path / "names.json").read_text()), trading)
    assert ok
