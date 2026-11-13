"""Autoresearch loop: find the weakest agent, try one targeted hyperparameter mutation, keep it only
if it improves a walk-forward replay without hurting the short shadow window, and commit or revert.
Every mutation tried is recorded as a trial so the Deflated Sharpe gate sees the true trial count."""
from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

import numpy as np
import pandas as pd

from valedia_trader.core.secrets import child_env

__all__ = ["ParamStore", "InMemoryParamStore", "GitParamStore", "AutoresearchLoop", "MutationOutcome",
           "period_sharpe"]


def period_sharpe(returns) -> float:
    """Per-period Sharpe of a return series (0 when it cannot be computed)."""
    r = np.asarray(pd.Series(returns).dropna(), dtype=float)
    sd = r.std(ddof=1) if r.size > 2 else 0.0
    return float(r.mean() / sd) if sd > 1e-15 else 0.0


class ParamStore(Protocol):
    def get(self, agent: str) -> dict[str, Any]: ...
    def stage(self, agent: str, params: dict[str, Any]) -> None: ...
    def commit(self, message: str) -> None: ...
    def revert(self) -> None: ...


class InMemoryParamStore:
    def __init__(self, params: Mapping[str, dict[str, Any]]) -> None:
        self._committed = {k: dict(v) for k, v in params.items()}
        self._staged = {k: dict(v) for k, v in params.items()}
        self.log: list[str] = []

    def get(self, agent: str) -> dict[str, Any]:
        return dict(self._staged[agent])

    def stage(self, agent: str, params: dict[str, Any]) -> None:
        self._staged[agent] = dict(params)

    def commit(self, message: str) -> None:
        self._committed = {k: dict(v) for k, v in self._staged.items()}
        self.log.append(message)

    def revert(self) -> None:
        self._staged = {k: dict(v) for k, v in self._committed.items()}


class GitParamStore:
    """Agent hyperparameters in one JSON file under git. commit() commits only that file; revert()
    restores it from HEAD. Nothing else in the repository is ever staged or reset."""

    def __init__(self, repo: str | Path, relative_path: str = "valedia_trader/agents/params.json",
                 defaults: Mapping[str, dict[str, Any]] | None = None) -> None:
        self.repo, self.rel = Path(repo), relative_path
        self.path = self.repo / relative_path
        if not (self.repo / ".git").exists():
            raise ValueError(f"{self.repo} is not a git repository")
        if not self.path.exists():
            if defaults is None:
                raise FileNotFoundError(self.path)
            self._write({k: dict(v) for k, v in defaults.items()})
            self.commit("autoresearch: initial agent parameters")

    def _git(self, *args: str) -> str:
        out = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True,
                             env=child_env())
        return out.stdout.strip()

    def _read(self) -> dict[str, dict[str, Any]]:
        return json.loads(self.path.read_text())

    def _write(self, data: Mapping[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

    def get(self, agent: str) -> dict[str, Any]:
        return dict(self._read()[agent])

    def stage(self, agent: str, params: dict[str, Any]) -> None:
        data = self._read()
        data[agent] = dict(params)
        self._write(data)

    def commit(self, message: str) -> None:
        self._git("add", "--", self.rel)
        if self._git("status", "--porcelain", "--", self.rel):
            self._git("commit", "-q", "-m", message, "--", self.rel)

    def revert(self) -> None:
        self._git("checkout", "HEAD", "--", self.rel)


@dataclass(frozen=True)
class MutationOutcome:
    agent: str
    param: str
    old_value: Any
    new_value: Any
    replay_baseline: float     # per-period Sharpe on the walk-forward replay
    replay_candidate: float
    shadow_baseline: float     # mean return over the shadow window
    shadow_candidate: float
    accepted: bool
    reason: str


Evaluator = Callable[[str, dict[str, Any]], pd.Series]


class AutoresearchLoop:
    """One step = one mutation of the lowest-scoring agent.

    `evaluate(agent, params)` returns that agent's net return series over the evaluation history
    (data strictly before the decision time). The last `shadow_days` rows are the paper/shadow
    window; everything before is the walk-forward replay. `search_space[agent][param]` is an ordered
    list of allowed values; a mutation moves one step along it."""

    def __init__(self, evaluate: Evaluator, search_space: Mapping[str, Mapping[str, Sequence[Any]]], store: ParamStore,
                 rng: np.random.Generator, registry=None, shadow_days: int = 5, min_replay: int = 250) -> None:
        if shadow_days < 1 or min_replay < 30:
            raise ValueError("shadow_days must be >= 1 and min_replay >= 30")
        self.evaluate, self.space, self.store, self.rng = evaluate, search_space, store, rng
        self.registry, self.shadow_days, self.min_replay = registry, shadow_days, min_replay

    def weakest(self, scores: Mapping[str, float]) -> str:
        """Lowest rolling-Sharpe agent among those with a search space (missing scores rank last)."""
        candidates = [a for a in self.space if self.space[a]]
        if not candidates:
            raise ValueError("no agent has a search space")
        return min(candidates, key=lambda a: scores[a] if a in scores and np.isfinite(scores[a]) else -math.inf)

    def propose(self, agent: str) -> tuple[str, Any, Any]:
        current = self.store.get(agent)
        options = [(p, vals) for p, vals in self.space[agent].items() if len(vals) > 1 and current.get(p) in vals]
        if not options:
            raise ValueError(f"agent '{agent}' has no mutable parameter at a listed value")
        param, vals = options[int(self.rng.integers(len(options)))]
        i = list(vals).index(current[param])
        moves = [j for j in (i - 1, i + 1) if 0 <= j < len(vals)]
        return param, current[param], vals[moves[int(self.rng.integers(len(moves)))]]

    def step(self, scores: Mapping[str, float]) -> MutationOutcome:
        agent = self.weakest(scores)
        param, old, new = self.propose(agent)
        base_params = self.store.get(agent)
        cand_params = {**base_params, param: new}
        base, cand = self.evaluate(agent, base_params), self.evaluate(agent, cand_params)
        if len(base) != len(cand) or len(base) < self.min_replay + self.shadow_days:
            raise ValueError("evaluation history is too short for a replay plus shadow window")
        split = len(base) - self.shadow_days
        rb, rc = period_sharpe(base.iloc[:split]), period_sharpe(cand.iloc[:split])
        sb, sc = float(base.iloc[split:].mean()), float(cand.iloc[split:].mean())
        if self.registry is not None:
            self.registry.record(f"autoresearch:{agent}:{param}={new}", rc, split, cand_params)
        accepted = bool(rc > rb and sc >= sb)
        if accepted:
            reason = "replay Sharpe improved and the shadow window did not get worse"
        elif rc <= rb:
            reason = "replay Sharpe did not improve"
        else:
            reason = "shadow window got worse"
        if accepted:
            self.store.stage(agent, cand_params)
            self.store.commit(f"autoresearch: {agent}.{param} {old} -> {new} (replay Sharpe {rb:.4f} -> {rc:.4f})")
        else:
            self.store.revert()
        return MutationOutcome(agent, param, old, new, rb, rc, sb, sc, accepted, reason)
