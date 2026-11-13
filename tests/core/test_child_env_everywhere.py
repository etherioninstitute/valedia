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

LAUNCHERS = {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}
ASYNC_LAUNCHERS = {"create_subprocess_exec", "create_subprocess_shell"}
REAL_LAUNCHES = 13                                  # launches in the package today, every one with env=child_env(...)
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


def _is_os_launcher(attr):
    return attr in {"system", "popen"} or attr.startswith(("exec", "spawn", "posix_spawn"))


_WATCHED = {"subprocess": lambda a: a in LAUNCHERS, "os": _is_os_launcher, "asyncio": lambda a: a in ASYNC_LAUNCHERS}


def _env_is_child_env(call):
    """True when the call has `env=child_env(...)`, written plain or as any `<x>.child_env(...)`."""
    for k in call.keywords:
        if k.arg == "env" and isinstance(k.value, ast.Call):
            f = k.value.func
            return (isinstance(f, ast.Name) and f.id == "child_env") or (isinstance(f, ast.Attribute) and f.attr == "child_env")
    return False


def _launch_audit(tree):
    """(accepted, bad): `accepted` counts references to a program launcher that are the function of a
    call carrying `env=child_env(...)` (subprocess launchers only); `bad` lists the line of every other
    reference to a launcher of subprocess, os (system, popen, exec*, spawn*, posix_spawn*) or asyncio
    (create_subprocess_*), however it is named (module alias, `X = subprocess`, a name imported from
    the module, `f = subprocess.run`, `getattr(subprocess, ...)`, `partial(subprocess.run, ...)`).
    Type annotations are not launches and are skipped."""
    aliases = {m: set() for m in _WATCHED}          # names bound to each watched module
    direct = {m: set() for m in _WATCHED}           # names bound to one of its launchers
    star = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name in aliases:
                    aliases[a.name].add(a.asname or a.name)
        elif isinstance(n, ast.ImportFrom) and n.module in direct:
            for a in n.names:
                if a.name == "*":
                    star.append(n.lineno)
                elif _WATCHED[n.module](a.name):
                    direct[n.module].add(a.asname or a.name)
    for _ in range(3):                              # `X = subprocess`, then `Y = X`
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Name):
                for m, names in aliases.items():
                    if n.value.id in names:
                        names.update(t.id for t in n.targets if isinstance(t, ast.Name))
    skip = set()
    for n in ast.walk(tree):
        notes = []
        if isinstance(n, ast.AnnAssign):
            notes.append(n.annotation)
        elif isinstance(n, ast.arg) and n.annotation is not None:
            notes.append(n.annotation)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.returns is not None:
            notes.append(n.returns)
        for note in notes:
            skip.update(id(x) for x in ast.walk(note))
    accepted_funcs = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call) and _env_is_child_env(n)}
    accepted, bad = 0, list(star)
    for n in ast.walk(tree):
        if id(n) in skip:
            continue
        module = None
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            module = next((m for m, names in aliases.items() if n.value.id in names and _WATCHED[m](n.attr)), None)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            module = next((m for m, names in direct.items() if n.id in names), None)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "getattr" and len(n.args) >= 2 \
                and isinstance(n.args[0], ast.Name) and any(n.args[0].id in names for names in aliases.values()):
            name = n.args[1]
            literal = isinstance(name, ast.Constant) and isinstance(name.value, str)
            module = "getattr" if not literal or name.value in LAUNCHERS or _is_os_launcher(name.value) \
                or name.value in ASYNC_LAUNCHERS else None
        if module is None:
            continue
        if module == "subprocess" and id(n) in accepted_funcs:
            accepted += 1
        else:
            bad.append(n.lineno)
    return accepted, sorted(bad)


def _audit_package():
    accepted, bad = 0, []
    for path in sorted(PACKAGE.rglob("*.py")):
        a, b = _launch_audit(ast.parse(path.read_text(), filename=str(path)))
        accepted += a
        bad += [f"{path.relative_to(PACKAGE.parent)}:{line}" for line in b]
    return accepted, bad


def test_every_program_launch_in_the_package_passes_child_env():
    accepted, bad = _audit_package()
    assert not bad, f"program launches that do not pass env=child_env(...): {bad}"
    assert accepted == REAL_LAUNCHES, f"the walk found {accepted} launches, expected {REAL_LAUNCHES}"


ACCEPTED_FORMS = [
    "import subprocess\nsubprocess.run(['a'], env=child_env())\n",
    "import subprocess\nsubprocess.Popen(['a'], stdin=1,\n  env=secrets.child_env({'A': '1'}))\n",
    "import subprocess as sp\nsp.check_output(['a'], env=m.secrets.child_env())\n",
    "from subprocess import run as r\nr(['a'], env=child_env())\n",
    "import subprocess\nsubprocess.call(['a'], env=child_env())\nsubprocess.check_call(['a'], env=child_env())\n",
]
ANNOTATION_ONLY = [
    "import subprocess\nclass C:\n    p: subprocess.Popen | None = None\n    def f(self, q: subprocess.Popen) -> subprocess.Popen:\n        return q\n",
    "from __future__ import annotations\nimport subprocess\nx: subprocess.CompletedProcess\ny = subprocess.DEVNULL\n",
]
CAUGHT_FORMS = {
    "no env": "import subprocess\nsubprocess.run(['a'])\n",
    "no env across lines": "import subprocess\nsubprocess.run(['a'],\n  capture_output=True)\n",
    "env None": "import subprocess\nsubprocess.run(['a'], env=None)\n",
    "env os.environ": "import os, subprocess\nsubprocess.run(['a'], env=os.environ)\n",
    "env dict(os.environ)": "import os, subprocess\nsubprocess.run(['a'], env=dict(os.environ))\n",
    "env a variable": "import subprocess\ne = child_env()\nsubprocess.run(['a'], env=e)\n",
    "env some other call": "import subprocess\nsubprocess.run(['a'], env=make_env())\n",
    "kwargs": "import subprocess\nsubprocess.run(['a'], **kw)\n",
    "Popen": "import subprocess\nsubprocess.Popen(['a'])\n",
    "check_output": "import subprocess\nsubprocess.check_output(['a'])\n",
    "check_call": "import subprocess\nsubprocess.check_call(['a'])\n",
    "call": "import subprocess\nsubprocess.call(['a'])\n",
    "getoutput": "import subprocess\nsubprocess.getoutput('a')\n",
    "getstatusoutput": "import subprocess\nsubprocess.getstatusoutput('a')\n",
    "from-import": "from subprocess import run as r\nr(['a'])\n",
    "module alias": "import subprocess as X\nX.run(['a'])\n",
    "module alias even with env None": "import subprocess as X\nX.run(['a'], env=None)\n",
    "module assigned to a name": "import subprocess\nX = subprocess\nX.run(['a'])\n",
    "module assigned twice": "import subprocess\nX = subprocess\nY = X\nY.run(['a'])\n",
    "launcher stored in a name": "import subprocess\nf = subprocess.run\nf(['a'])\n",
    "launcher stored even with env at the call": "import subprocess\nf = subprocess.run\nf(['a'], env=child_env())\n",
    "getattr": "import subprocess\ngetattr(subprocess, 'run')(['a'])\n",
    "getattr with env": "import subprocess\ngetattr(subprocess, 'run')(['a'], env=child_env())\n",
    "getattr computed name": "import subprocess\ngetattr(subprocess, name)(['a'])\n",
    "functools.partial": "import functools, subprocess\nfunctools.partial(subprocess.run, capture_output=True)(['a'])\n",
    "partial with env": "import functools, subprocess\nfunctools.partial(subprocess.run, env=child_env())(['a'])\n",
    "star import": "from subprocess import *\n",
    "os.system": "import os\nos.system('a')\n",
    "os.popen": "import os\nos.popen('a')\n",
    "os.execv": "import os\nos.execv('a', ['a'])\n",
    "os.execvp": "import os\nos.execvp('a', ['a'])\n",
    "os.spawnl": "import os\nos.spawnl(os.P_WAIT, 'a', 'a')\n",
    "os.spawnvpe": "import os\nos.spawnvpe(os.P_WAIT, 'a', ['a'], {})\n",
    "os.posix_spawn": "import os\nos.posix_spawn('a', ['a'], {})\n",
    "os.posix_spawnp": "import os\nos.posix_spawnp('a', ['a'], {})\n",
    "os alias": "import os as o\no.system('a')\n",
    "os imported by name": "from os import system\nsystem('a')\n",
    "os launcher with env=child_env": "import os\nos.execve('a', ['a'], env=child_env())\n",
    "asyncio exec": "import asyncio\nasyncio.create_subprocess_exec('a')\n",
    "asyncio shell": "import asyncio\nasyncio.create_subprocess_shell('a')\n",
    "asyncio imported by name": "from asyncio import create_subprocess_exec\ncreate_subprocess_exec('a')\n",
}


@pytest.mark.parametrize("code", ACCEPTED_FORMS)
def test_the_source_walk_accepts_a_launch_with_child_env(code):
    accepted, bad = _launch_audit(ast.parse(code))
    assert bad == [] and accepted == code.count("env=")


@pytest.mark.parametrize("code", ANNOTATION_ONLY)
def test_the_source_walk_ignores_type_annotations(code):
    assert _launch_audit(ast.parse(code)) == (0, [])


@pytest.mark.parametrize("name", sorted(CAUGHT_FORMS))
def test_the_source_walk_catches(name):
    accepted, bad = _launch_audit(ast.parse(CAUGHT_FORMS[name]))
    assert bad, f"the walk let this through: {name}"


def test_the_source_walk_reports_the_line_of_a_multi_line_launch():
    code = "import subprocess\n\nsubprocess.run(['a'],\n  capture_output=True)\nsubprocess.run(['a'], env=child_env())\n"
    assert _launch_audit(ast.parse(code)) == (1, [3])
