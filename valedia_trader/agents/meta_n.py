"""Meta-N: improvement as a stack of layers, with an archive that never forgets and never breeds
from a regression.

A port of the search skeleton of Meta^n (Kim et al., "Recursive Self-Improvement through Emergent
Depth", arXiv 2608.24735; upstream `meta_n/core/archive.py` and `meta_layer.py`):

* A candidate is a chain of layers on a fixed base. Depth is one plus the number of layers. Each
  layer is produced by the same meta-operation, which reads the traces of the stack below it.
* The archive only grows. It tracks the best candidate and the best per task. Parents are drawn by
  rank-normalised score plus a UCB exploration bonus, with the elites reserved first.
* A candidate that regressed against its parent, on the mean or on any single task, is kept in the
  archive but is not bred from. That is the rollback: the search continues from what still works.

One deliberate difference. Upstream, a layer is code written by a model and executed. Here a layer
is a `Patch`: a list of validated operations on a configuration (set a value, enable or disable an
item, revert an earlier layer). Nothing a model writes is executed in the trading process.
"""
from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = ["Trace", "Patch", "PatchError", "apply_chain", "Candidate", "Archive", "ucb_bonus"]


class PatchError(ValueError):
    pass


@dataclass(frozen=True)
class Trace:
    """What happened when a candidate ran on one task."""
    task_id: str
    score: float
    success: bool = True
    info: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Patch:
    """One layer. Operations, applied in order to a nested dict:
        {"op": "set", "path": "a.b", "value": v}
        {"op": "remove", "path": "list.path", "value": item}     drop an item from a list
        {"op": "append", "path": "list.path", "value": item}
        {"op": "revert", "depth": d}                              undo the layer at that depth
    """
    ops: tuple[dict, ...]
    rationale: str = ""
    source_depth: int = 0

    def __post_init__(self):
        for op in self.ops:
            kind = op.get("op") if isinstance(op, dict) else None
            if kind == "revert":
                if not isinstance(op.get("depth"), int) or op["depth"] < 2:
                    raise PatchError("revert needs the depth (>= 2) of the layer to undo")
            elif kind in ("set", "remove", "append"):
                if not isinstance(op.get("path"), str) or not op["path"] or "value" not in op:
                    raise PatchError(f"{kind} needs a path and a value")
            else:
                raise PatchError(f"unknown operation {kind!r}")


def _walk(target: dict, path: str, create: bool) -> tuple[dict, str]:
    keys = path.split(".")
    for key in keys[:-1]:
        if key not in target or not isinstance(target[key], dict):
            if not create:
                raise PatchError(f"path '{path}' does not exist")
            target[key] = {}
        target = target[key]
    return target, keys[-1]


def apply_chain(base: dict, patches: list[Patch], validate: Callable[[dict], Any] | None = None) -> dict:
    """The configuration a chain of layers produces. Layer i sits at depth i + 2 (the base is depth
    1). A `revert` in a later layer removes an earlier layer entirely. `validate` is called on the
    result and may raise."""
    reverted = {op["depth"] for i, p in enumerate(patches) for op in p.ops if op["op"] == "revert" and op["depth"] < i + 2}
    out = copy.deepcopy(base)
    for i, patch in enumerate(patches):
        if i + 2 in reverted:
            continue
        for op in patch.ops:
            if op["op"] == "revert":
                if op["depth"] >= i + 2:
                    raise PatchError("a layer can only revert a layer below it")
                continue
            holder, key = _walk(out, op["path"], create=op["op"] == "set")
            if op["op"] == "set":
                holder[key] = copy.deepcopy(op["value"])
            else:
                if not isinstance(holder.get(key), list):
                    raise PatchError(f"'{op['path']}' is not a list")
                if op["op"] == "append" and op["value"] not in holder[key]:
                    holder[key] = [*holder[key], op["value"]]
                elif op["op"] == "remove":
                    holder[key] = [x for x in holder[key] if x != op["value"]]
    if validate is not None:
        validate(out)
    return out


@dataclass
class Candidate:
    candidate_id: str
    parent_id: str | None = None
    iteration: int = 0
    patches: list[Patch] = field(default_factory=list)
    traces: list[Trace] = field(default_factory=list)
    mean_score: float = float("nan")
    per_task_scores: dict[str, float] = field(default_factory=dict)
    num_children: int = 0

    @property
    def depth(self) -> int:
        return 1 + len(self.patches)

    @classmethod
    def evaluated(cls, candidate_id: str, traces: list[Trace], parent: "Candidate | None" = None,
                  patch: Patch | None = None, iteration: int = 0, mean_score: float | None = None) -> "Candidate":
        """Build a candidate from its traces. The mean is over finite task scores unless given."""
        scores = {t.task_id: t.score for t in traces}
        finite = [s for s in scores.values() if math.isfinite(s)]
        mean = mean_score if mean_score is not None else (sum(finite) / len(finite) if finite else float("nan"))
        patches = ([*parent.patches, patch] if parent is not None and patch is not None else list(parent.patches) if parent else [])
        return cls(candidate_id, parent.candidate_id if parent else None, iteration, patches, list(traces), mean, scores)


def ucb_bonus(alpha: float, pool_size: int, num_children: int) -> float:
    """alpha * sqrt(ln(pool) / (1 + children)); zero for a pool of one."""
    return 0.0 if pool_size <= 1 else alpha * math.sqrt(math.log(pool_size) / (1 + num_children))


class Archive:
    """Monotonically growing set of evaluated candidates."""

    def __init__(self, novelty_alpha: float = 0.3) -> None:
        self.candidates: list[Candidate] = []
        self._by_id: dict[str, Candidate] = {}
        self._best_per_task: dict[str, tuple[float, str]] = {}
        self._best_id: str | None = None
        self.novelty_alpha = novelty_alpha

    def __len__(self) -> int:
        return len(self.candidates)

    def add(self, candidate: Candidate) -> bool:
        """Add a candidate. A duplicate id is ignored, so replaying a step is harmless. Nothing is
        ever removed."""
        if candidate.candidate_id in self._by_id:
            return False
        self.candidates.append(candidate)
        self._by_id[candidate.candidate_id] = candidate
        if math.isfinite(candidate.mean_score) and (self._best_id is None or candidate.mean_score > self._by_id[self._best_id].mean_score):
            self._best_id = candidate.candidate_id
        for t in candidate.traces:
            if not math.isfinite(t.score):
                continue
            current = self._best_per_task.get(t.task_id)
            if current is None or t.score > current[0]:
                self._best_per_task[t.task_id] = (t.score, candidate.candidate_id)
        parent = self._by_id.get(candidate.parent_id or "")
        if parent is not None:
            parent.num_children += 1
        return True

    def get(self, candidate_id: str) -> Candidate:
        return self._by_id[candidate_id]

    def best(self) -> Candidate | None:
        return self._by_id.get(self._best_id) if self._best_id else None

    def per_task_best(self) -> dict[str, tuple[float, str]]:
        return dict(self._best_per_task)

    def elite_ids(self) -> list[str]:
        """The archive best, then every per-task winner in task order."""
        ids = [self._best_id] if self._best_id else []
        for task in sorted(self._best_per_task):
            cid = self._best_per_task[task][1]
            if cid not in ids:
                ids.append(cid)
        return ids

    def score_range(self) -> float:
        finite = sorted(c.mean_score for c in self.candidates if math.isfinite(c.mean_score))
        return max(1e-9, finite[-1] - finite[0]) if len(finite) >= 2 else 1e-9

    def selection_weights(self, candidates: list[Candidate]) -> list[float]:
        """rank_norm(mean_score) + UCB bonus. rank_norm is the share of candidates with a strictly
        smaller finite mean, so the weights do not depend on the scale of the score."""
        n = len(candidates)
        means = [c.mean_score if math.isfinite(c.mean_score) else float("-inf") for c in candidates]
        out = []
        for i, c in enumerate(candidates):
            rank = sum(1 for m in means if m < means[i]) / (n - 1) if n > 1 else 0.0
            w = rank + ucb_bonus(self.novelty_alpha, n, c.num_children)
            out.append(w if math.isfinite(w) and w > 1e-6 else 1e-6)
        return out

    def select_parents(self, n: int, rng: random.Random | None = None, pool: list[Candidate] | None = None) -> list[Candidate]:
        """n parents: with n >= 2 the first n - 1 are the elites, the rest are weighted draws. With
        n == 1 the single parent is a weighted draw."""
        candidates = pool if pool is not None else self.candidates
        if not candidates:
            return []
        rng = rng or random.Random()
        selected: list[Candidate] = []
        if n >= 2:
            by_id = {c.candidate_id: c for c in candidates}
            selected += [by_id[i] for i in self.elite_ids() if i in by_id][: n - 1]
        remaining = n - len(selected)
        if remaining > 0:
            selected += rng.choices(candidates, weights=self.selection_weights(candidates), k=remaining)
        return selected

    def breedable_pool(self, max_depth: int, tol_frac: float = 0.05) -> list[Candidate]:
        """Candidates that may be parents: below `max_depth` and an elite, a seed, or not a regression
        against their parent (mean and every task within `tol_frac` of the score range). Falls back
        to seeds and elites, then to anything extendable, so it is empty only when nothing can grow."""
        extendable = [c for c in self.candidates if c.depth < max_depth]
        if not extendable:
            return []
        tol, elites = tol_frac * self.score_range(), set(self.elite_ids())
        pool = [c for c in extendable if self._breedable(c, elites, tol)]
        if pool:
            return pool
        fallback = [c for c in extendable if c.parent_id is None or c.candidate_id in elites]
        return fallback or extendable

    def _breedable(self, c: Candidate, elites: set[str], tol: float) -> bool:
        if c.candidate_id in elites or c.parent_id is None:
            return True
        parent = self._by_id.get(c.parent_id)
        if parent is None:
            return True
        if not math.isfinite(c.mean_score) or c.mean_score < parent.mean_score - tol:
            return False
        for task, score in c.per_task_scores.items():
            p = parent.per_task_scores.get(task)
            if p is not None and math.isfinite(score) and score < p - tol:
                return False
        return True

    def regressed(self, candidate_id: str, tol_frac: float = 0.05) -> bool:
        c = self._by_id[candidate_id]
        return c.parent_id is not None and not self._breedable(c, set(), tol_frac * self.score_range())

    def lineage(self, candidate_id: str) -> list[Candidate]:
        """The candidate's ancestors, oldest first, ending with the candidate."""
        out, cur = [], self._by_id.get(candidate_id)
        while cur is not None:
            out.append(cur)
            cur = self._by_id.get(cur.parent_id or "")
        return out[::-1]

    def to_dict(self) -> dict:
        return {"best": self._best_id, "per_task_best": {k: list(v) for k, v in self._best_per_task.items()},
                "candidates": [{"id": c.candidate_id, "parent": c.parent_id, "iteration": c.iteration, "depth": c.depth,
                                "mean_score": c.mean_score if math.isfinite(c.mean_score) else None,
                                "per_task_scores": c.per_task_scores, "children": c.num_children,
                                "layers": [{"ops": list(p.ops), "rationale": p.rationale} for p in c.patches]} for c in self.candidates]}
