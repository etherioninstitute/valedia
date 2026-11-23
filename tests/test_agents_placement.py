"""The placement guard: a Hermes call whose LM Studio model is not already loaded is refused before it starts.
No real lms, no real Hermes, no network; profiles live in a temporary Hermes home."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from valedia_trader.agents import desk as D
from valedia_trader.agents import panel as pn
from valedia_trader.agents import placement as P
from valedia_trader.research_loop import research as rs


def _home(tmp_path, **profiles):
    for name, (provider, model) in profiles.items():
        d = tmp_path / "profiles" / name
        d.mkdir(parents=True)
        (d / "config.yaml").write_text(f"model:\n  provider: {provider}\n  default: {model}\n" if model else f"model:\n  provider: {provider}\n")
    return tmp_path


def _runner(rows=None, code=0, stdout=None, exc=None):
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw))
        if exc:
            raise exc
        return SimpleNamespace(returncode=code, stdout=json.dumps(rows) if stdout is None else stdout)
    run.calls = calls
    return run


def _argv(profile="lm", *extra, prompt="PROMPT"):
    return ["hermes", "-p", profile, "-z", prompt, "-t", "clarify", *extra]


REMOTE = [{"identifier": "m1", "deviceIdentifier": "dev-mini"}]
LOCAL = [{"identifier": "m1", "deviceIdentifier": None}]


def test_remote_model_runs(tmp_path):
    run = _runner(REMOTE)
    assert P.check_hermes_argv(_argv(), _home(tmp_path, lm=("lmstudio", "m1")), run, "lms") == "remote"
    assert run.calls[0][0] == ["lms", "ps", "--json"]
    assert run.calls[0][1]["timeout"] == 30 and run.calls[0][1]["stdin"] == subprocess.DEVNULL


def test_local_model_runs(tmp_path):
    assert P.check_hermes_argv(_argv(), _home(tmp_path, lm=("lmstudio", "m1")), _runner(LOCAL), "lms") == "local"


def test_unloaded_model_is_refused(tmp_path):
    home = _home(tmp_path, lm=("lmstudio", "m1"))
    with pytest.raises(P.ModelNotLoaded, match=r"'lm'.*'m1'.*unloaded.*not .*load it on this Mac"):
        P.check_hermes_argv(_argv(), home, _runner([{"identifier": "other", "deviceIdentifier": None}]), "lms")
    with pytest.raises(P.ModelNotLoaded):
        P.check_hermes_argv(_argv(), home, _runner([]), "lms")


@pytest.mark.parametrize("run", [
    _runner(code=1, stdout=""),
    _runner(stdout="not json"),
    _runner(stdout='{"identifier": "m1"}'),
    _runner(exc=subprocess.TimeoutExpired("lms", 30)),
    _runner(exc=OSError("no lms")),
], ids=["nonzero", "bad-json", "not-a-list", "timeout", "oserror"])
def test_a_failed_check_is_refused_never_waved_through(tmp_path, run):
    assert P.lmstudio_placement("m1", run, "lms") == "unknown"
    with pytest.raises(P.ModelNotLoaded, match="unknown"):
        P.check_hermes_argv(_argv(), _home(tmp_path, lm=("lmstudio", "m1")), run, "lms")


def test_mixed_rows_count_as_local():
    rows = [{"identifier": "m1", "deviceIdentifier": "dev-mini"}, {"identifier": "m1", "deviceIdentifier": None}]
    assert P.lmstudio_placement("m1", _runner(rows), "lms") == "local"
    assert P.lmstudio_placement("m1", _runner(REMOTE * 2), "lms") == "remote"


def test_lms_path_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("VALEDIA_LMS", "/made/up/lms")
    run = _runner(REMOTE)
    P.lmstudio_placement("m1", run)
    assert run.calls[0][0][0] == "/made/up/lms"


def test_other_providers_are_not_checked(tmp_path):
    run = _runner(exc=AssertionError("lms must not be asked"))
    assert P.check_hermes_argv(_argv(), _home(tmp_path, lm=("ollama-cloud", "m1")), run, "lms") is None
    assert P.check_hermes_argv(_argv("missing"), tmp_path, run, "lms") is None       # no such profile: nothing to check
    assert run.calls == []


def test_provider_flag_makes_a_cloud_profile_checked_against_the_flag_model(tmp_path):
    home = _home(tmp_path, cloud=("ollama-cloud", "cloud-model"))
    run = _runner([{"identifier": "X", "deviceIdentifier": "d"}])
    assert P.check_hermes_argv(_argv("cloud", "--provider", "lmstudio", "-m", "X"), home, run, "lms") == "remote"
    with pytest.raises(P.ModelNotLoaded, match="'Y'"):
        P.check_hermes_argv(_argv("cloud", "--provider", "lmstudio", "-m", "Y"), home, run, "lms")


def test_model_flag_overrides_the_profile_default(tmp_path):
    home = _home(tmp_path, lm=("lmstudio", "m1"))
    run = _runner([{"identifier": "X", "deviceIdentifier": "d"}])                     # X loaded, the default m1 is not
    assert P.check_hermes_argv(_argv("lm", "-m", "X"), home, run, "lms") == "remote"
    assert P.check_hermes_argv(_argv("lm", "--model", "X"), home, run, "lms") == "remote"
    with pytest.raises(P.ModelNotLoaded, match="'m1'"):
        P.check_hermes_argv(_argv("lm"), home, run, "lms")


def test_a_prompt_that_mentions_flags_changes_nothing(tmp_path):
    home = _home(tmp_path, lm=("lmstudio", "m1"))
    run = _runner([{"identifier": "m1", "deviceIdentifier": "d"}])
    prompt = "please use -m foo and --provider ollama and -p other now"
    assert P.check_hermes_argv(_argv("lm", prompt=prompt), home, run, "lms") == "remote"
    with pytest.raises(P.ModelNotLoaded, match="'m1'"):
        P.check_hermes_argv(_argv("lm", prompt=prompt), home, _runner([]), "lms")


def test_invalid_profile_name_is_rejected(tmp_path):
    for bad in ("../x", "a b", "x; rm -rf", ""):
        with pytest.raises(ValueError):
            P.profile_model(bad, tmp_path)
    assert P.profile_model("absent", tmp_path) == (None, None)
    assert P.profile_model("lm", _home(tmp_path, lm=("lmstudio", "m1"))) == ("lmstudio", "m1")


def test_lmstudio_with_no_model_is_refused(tmp_path):
    run = _runner(REMOTE)
    with pytest.raises(P.ModelNotLoaded, match="names no model"):
        P.check_hermes_argv(_argv(), _home(tmp_path, lm=("lmstudio", None)), run, "lms")
    assert run.calls == []


def test_an_unreadable_profile_config_is_refused(tmp_path):
    d = tmp_path / "profiles" / "lm"
    d.mkdir(parents=True)
    (d / "config.yaml").write_text("model: [unclosed")
    with pytest.raises(P.ModelNotLoaded, match="could not be read"):
        P.check_hermes_argv(_argv(), tmp_path, _runner(REMOTE), "lms")


# ---- each launch site runs the guard before it starts Hermes -------------------------------------------------------

@pytest.fixture
def unloaded(monkeypatch, tmp_path):
    """Every profile is an LM Studio profile and nothing is loaded; no real home or lms is touched."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(P, "profile_model", lambda profile, hermes_home=None: ("lmstudio", "m1"))
    monkeypatch.setattr(P, "lmstudio_placement", lambda identifier, runner=None, lms=None: "unloaded")


def _never(*a, **k):
    raise AssertionError("hermes was launched")


def test_desk_refuses_before_launching_hermes(unloaded, monkeypatch):
    monkeypatch.setattr(D.subprocess, "run", _never)
    with pytest.raises(D.DeskError, match="refused"):
        D.hermes_runner("researcher", {"profile": "res", "toolsets": "web"}, "PROMPT")


def test_panel_member_refuses_before_launching_hermes(unloaded, monkeypatch):
    monkeypatch.setattr(pn.subprocess, "run", _never)
    with pytest.raises(P.ModelNotLoaded, match="refused"):
        pn.HermesProfileMember._subprocess(["hermes", "-p", "p", "-z", "PROMPT"], 5.0)


def test_research_asker_refuses_before_launching_hermes(unloaded, monkeypatch):
    monkeypatch.setattr(rs.subprocess, "run", _never)
    with pytest.raises(P.ModelNotLoaded, match="refused"):
        rs.hermes_asker("valedia-trading-decider")("PROMPT")


def test_a_loaded_model_lets_each_site_launch(unloaded, monkeypatch):
    monkeypatch.setattr(P, "lmstudio_placement", lambda identifier, runner=None, lms=None: "remote")
    ok = lambda argv, **k: SimpleNamespace(returncode=0, stdout="ok")
    for mod in (D, pn, rs):
        monkeypatch.setattr(mod.subprocess, "run", ok)
    assert D.hermes_runner("researcher", {"profile": "res"}, "PROMPT") == "ok"
    assert pn.HermesProfileMember._subprocess(["hermes", "-p", "p", "-z", "PROMPT"], 5.0) == "ok"
    assert rs.hermes_asker("valedia-trading-decider")("PROMPT") == "ok"


def test_equals_forms_are_read(tmp_path):
    home = _home(tmp_path, lm=("lmstudio", "m1"), cloud=("ollama-cloud", "c1"))
    with pytest.raises(P.ModelNotLoaded):
        P.check_hermes_argv(["hermes", "-p", "lm", "-z", "x", "--model=other"], home, _runner(REMOTE), "lms")
    with pytest.raises(P.ModelNotLoaded):
        P.check_hermes_argv(["hermes", "--profile=cloud", "-z", "x", "--provider=lmstudio", "-m", "other"], home, _runner(REMOTE), "lms")


def test_a_trailing_value_flag_is_refused(tmp_path):
    with pytest.raises(P.ModelNotLoaded):
        P.check_hermes_argv(["hermes", "-p", "lm", "-z", "x", "-m"], _home(tmp_path, lm=("lmstudio", "m1")), _runner(REMOTE), "lms")


@pytest.mark.parametrize("spelling", ["LMStudio", "lm-studio", "lm_studio"])
def test_provider_spellings_are_checked(tmp_path, spelling):
    with pytest.raises(P.ModelNotLoaded):
        P.check_hermes_argv(_argv(), _home(tmp_path, lm=(spelling, "other")), _runner(REMOTE), "lms")
