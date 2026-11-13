"""Every program the package starts gets `child_env()`, never the whole environment. All values are
made up; checks compare names and booleans, never a value."""
import ast
import os
import random
import string
import subprocess
from pathlib import Path

import pytest

import valedia_trader
from valedia_trader.agents import autoresearch as AR
from valedia_trader.agents import mirofish_models as MM
from valedia_trader.core import secrets as s

LAUNCHERS = {"run", "Popen", "check_output", "check_call", "call"}
PACKAGE = Path(valedia_trader.__file__).resolve().parent


def _made_up(rng, n=32):
    return "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(n))


@pytest.fixture
def loaded_secret(tmp_path, monkeypatch):
    """A made-up trading file loaded into this process's environment (a temporary file, never the
    real one). Returns the name that was loaded."""
    name = "MADE_UP_BROKER_KEY"
    f = tmp_path / "trading.env"
    f.write_text(f"{name}={_made_up(random.Random(7))}\n")
    os.chmod(f, 0o600)
    monkeypatch.setenv(name, "x")                       # recorded, so teardown removes what the load sets
    monkeypatch.setenv("VALEDIA_ENV_FILE", str(tmp_path / "not-the-real-file.env"))
    s.load_env(f, override=True)
    assert name in os.environ
    return name


class _Spy:
    """Stands in for subprocess.run: records the env it was given, never starts anything."""

    def __init__(self, stdout=""):
        self.stdout, self.envs = stdout, []

    def __call__(self, argv, **kw):
        self.envs.append(kw.get("env", "MISSING"))
        return subprocess.CompletedProcess(argv, 0, stdout=self.stdout, stderr="")


def _assert_reduced(env, secret_name):
    assert isinstance(env, dict), "the call passed no env, so the child would inherit everything"
    assert secret_name not in env
    assert "PATH" in env and "HOME" in env


def test_git_param_store_launches_git_with_child_env(loaded_secret, monkeypatch):
    spy = _Spy("abc\n")
    monkeypatch.setattr(AR.subprocess, "run", spy)
    store = object.__new__(AR.GitParamStore)
    store.repo = Path("/nonexistent-repo-for-test")
    assert store._git("status") == "abc"
    assert len(spy.envs) == 1
    _assert_reduced(spy.envs[0], loaded_secret)


def test_lmstudio_listing_launches_lms_with_child_env(loaded_secret, monkeypatch):
    spy = _Spy('[{"modelKey": "made-up/model"}]')
    monkeypatch.setattr(MM.subprocess, "run", spy)
    assert MM.lmstudio_listing("/nonexistent/lms") == {"made-up/model": {"modelKey": "made-up/model"}}
    assert len(spy.envs) == 1
    _assert_reduced(spy.envs[0], loaded_secret)


def _launches_without_env(tree):
    """Line numbers of every subprocess launch (run/Popen/check_output/check_call/call, written as
    `subprocess.X(...)` or imported by name from subprocess) whose whole call has no `env=` keyword."""
    from_subprocess = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "subprocess"
                       for a in n.names if a.name in LAUNCHERS}
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        launch = (isinstance(f, ast.Attribute) and f.attr in LAUNCHERS and isinstance(f.value, ast.Name) and f.value.id == "subprocess") \
            or (isinstance(f, ast.Name) and f.id in from_subprocess)
        if launch and not any(k.arg == "env" for k in node.keywords):
            bad.append(node.lineno)
    return bad


def test_every_subprocess_launch_in_the_package_passes_env():
    missing = []
    seen = 0
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        seen += sum(1 for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                    and n.value.id == "subprocess" and n.attr in LAUNCHERS)
        missing += [f"{path.relative_to(PACKAGE.parent)}:{line}" for line in _launches_without_env(tree)]
    assert seen > 5, "the walk found almost no launches; it is not looking at the package"
    assert not missing, f"subprocess launches without env=: {missing}"


def test_the_source_walk_catches_a_launch_without_env():
    code = ("import subprocess\nfrom subprocess import run as r\n"
            "subprocess.run(['a'],\n  capture_output=True)\n"
            "subprocess.Popen(['a'], env=None)\n"
            "subprocess.check_output(['a'])\nsubprocess.check_call(['a'])\nsubprocess.call(['a'])\nr(['a'])\n"
            "subprocess.run(['a'],\n  env=None)\n")
    assert _launches_without_env(ast.parse(code)) == [3, 6, 7, 8, 9]
