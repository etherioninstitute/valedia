import subprocess

import numpy as np
import pandas as pd
import pytest

from valedia_trader.agents import autoresearch as ar
from valedia_trader.quant_engine.level10_ml_research import TrialRegistry


def evaluator(quality):
    """Returns for an agent whose mean return rises with `quality[lookback]`; same noise for every call."""
    def run(agent, params):
        rng = np.random.default_rng(42)
        return pd.Series(quality[params["lookback"]] + 0.01 * rng.standard_normal(400))
    return run


SPACE = {"trend": {"lookback": [10, 20, 40]}, "carry": {"lookback": [10, 20, 40]}}
START = {"trend": {"lookback": 20}, "carry": {"lookback": 20}}


def test_improving_mutation_is_committed_and_recorded():
    store, reg = ar.InMemoryParamStore(START), TrialRegistry()
    loop = ar.AutoresearchLoop(evaluator({10: 0.002, 20: 0.0, 40: 0.002}), SPACE, store, np.random.default_rng(0), reg)
    out = loop.step({"trend": -0.5, "carry": 1.2})
    assert out.agent == "trend" and out.accepted and out.new_value in (10, 40)
    assert out.replay_candidate > out.replay_baseline
    assert store.get("trend")["lookback"] == out.new_value and store.get("carry") == {"lookback": 20}
    assert len(store.log) == 1 and "trend.lookback 20 ->" in store.log[0]
    assert reg.n_trials() == 1                                         # every mutation counts as a trial


def test_non_improving_mutation_is_reverted():
    store, reg = ar.InMemoryParamStore(START), TrialRegistry()
    loop = ar.AutoresearchLoop(evaluator({10: -0.002, 20: 0.0, 40: -0.002}), SPACE, store, np.random.default_rng(0), reg)
    out = loop.step({"trend": 0.9, "carry": float("nan")})            # a missing score ranks as the weakest
    assert out.agent == "carry" and not out.accepted and "did not improve" in out.reason
    assert store.get("carry") == {"lookback": 20} and store.log == [] and reg.n_trials() == 1


def test_shadow_window_can_block_a_replay_winner():
    def run(agent, params):
        rng = np.random.default_rng(7)
        r = pd.Series(0.01 * rng.standard_normal(400))
        if params["lookback"] != 20:
            r.iloc[:-5] += 0.002                                       # better in replay ...
            r.iloc[-5:] -= 0.05                                        # ... but bad in the last 5 days
        return r
    store = ar.InMemoryParamStore(START)
    out = ar.AutoresearchLoop(run, SPACE, store, np.random.default_rng(1)).step({"trend": 0.0, "carry": 1.0})
    assert not out.accepted and out.reason == "shadow window got worse" and store.get("trend") == {"lookback": 20}


def test_loop_validation():
    store = ar.InMemoryParamStore(START)
    short = ar.AutoresearchLoop(lambda a, p: pd.Series(np.zeros(50)), SPACE, store, np.random.default_rng(0))
    with pytest.raises(ValueError, match="too short"):
        short.step({"trend": 0.0, "carry": 1.0})
    assert ar.period_sharpe(np.zeros(10)) == 0.0


def test_git_param_store_touches_only_its_own_file(tmp_path):
    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True, text=True, check=True).stdout
    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "test")
    (tmp_path / "other.txt").write_text("v1\n")
    git("add", "other.txt")
    git("commit", "-q", "-m", "init")
    store = ar.GitParamStore(tmp_path, "agents/params.json", START)
    assert store.get("trend") == {"lookback": 20} and "initial agent parameters" in git("log", "--oneline")
    (tmp_path / "other.txt").write_text("uncommitted work\n")          # unrelated dirty file
    store.stage("trend", {"lookback": 40})
    store.commit("autoresearch: trend.lookback 20 -> 40")
    assert git("show", "--name-only", "--format=", "HEAD").split() == ["agents/params.json"]
    assert (tmp_path / "other.txt").read_text() == "uncommitted work\n"
    store.stage("trend", {"lookback": 10})
    store.revert()
    assert store.get("trend") == {"lookback": 40} and (tmp_path / "other.txt").read_text() == "uncommitted work\n"
    loop = ar.AutoresearchLoop(evaluator({10: -0.002, 20: 0.0, 40: 0.0005}), SPACE, store, np.random.default_rng(3))
    before = git("rev-parse", "HEAD")
    out = loop.step({"trend": -1.0, "carry": 1.0})
    assert (git("rev-parse", "HEAD") != before) == out.accepted
    with pytest.raises(ValueError):
        ar.GitParamStore(tmp_path / "nowhere")
