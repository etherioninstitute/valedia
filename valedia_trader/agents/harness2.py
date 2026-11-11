"""Harness2: refine the system around a fixed idea by Propose, Probe, Compose.

A port of the task-level step of Harness2 ("Recursive Agent Harnessing for an Open World", arXiv
2610.14224; upstream `harness2/core/recursion.py`, `selection.py`, `prompts.py`), with its second
level of recursion:

* Propose. From the evidence of the base run, propose up to k edits on each of two dimensions.
  INV edits change how the thing works (procedure: exits, sizing, limits). DOM edits change what
  it knows (the domain content: the signal and where it is applied). The two never touch the same
  part, so an INV edit and a DOM edit can be adopted together.
* Probe. Run every candidate on the same task and record its behaviour.
* Compose. Build one refined version from the contrasts between the probes and the base. KEEP_BASE
  is a real answer: an edit the evidence does not argue for is a regression.
* Harnessing-level recursion. Every edit tried, adopted or not, goes into a persistent library with
  the contrast that decided it. Later proposals are ordered by what that library has seen work.

Differences from upstream: edits are validated patches (see meta_n.Patch) and not free-form files;
the composer is a fixed rule, so there is one composition per step where upstream draws k; and the
probes here do have a score, the development-period result, which upstream's tasks lack.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from valedia_trader.agents.meta_n import Patch

__all__ = ["Edit", "Probe", "Composition", "EditLibrary", "Proposer", "compose", "ppc_step", "DIMENSIONS"]

DIMENSIONS = ("INV", "DOM")


@dataclass(frozen=True)
class Edit:
    label: str
    dimension: str            # "INV": how it works; "DOM": what it knows
    kind: str                 # the reusable name of this sort of edit, e.g. "widen_stop"
    description: str
    patch: Patch

    def __post_init__(self):
        if self.dimension not in DIMENSIONS:
            raise ValueError(f"dimension must be one of {DIMENSIONS}")


@dataclass(frozen=True)
class Probe:
    edit: Edit
    behavior: dict            # must hold `score`; may hold `tasks` (task -> score) and guard metrics


@dataclass(frozen=True)
class Composition:
    adopted: tuple[Edit, ...]
    keep_base: bool
    reasons: tuple[str, ...]

    def patch(self, source_depth: int = 0) -> Patch | None:
        """The adopted edits as one layer (INV first, then DOM), or None for KEEP_BASE."""
        if self.keep_base:
            return None
        ops = tuple(op for e in self.adopted for op in e.patch.ops)
        return Patch(ops, "; ".join(e.description for e in self.adopted), source_depth)


class Proposer(Protocol):
    def propose(self, evidence: dict, k: int, library: "EditLibrary") -> list[Edit]: ...


class EditLibrary:
    """The improvement harness: what was tried on earlier tasks and what the evidence said."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.entries: list[dict] = json.loads(self.path.read_text()) if self.path and self.path.exists() else []

    def record(self, task_id: str, probe: Probe, adopted: bool, contrast: dict, lesson: str) -> None:
        self.entries.append({"task": task_id, "kind": probe.edit.kind, "dimension": probe.edit.dimension,
                             "description": probe.edit.description, "adopted": bool(adopted), "contrast": contrast, "lesson": lesson})
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.entries, indent=1, default=str))
            tmp.replace(self.path)

    def stats(self, kind: str) -> tuple[int, int]:
        """(times adopted, times tried) for a kind of edit, over distinct tasks."""
        tried = {e["task"] for e in self.entries if e["kind"] == kind}
        adopted = {e["task"] for e in self.entries if e["kind"] == kind and e["adopted"]}
        return len(adopted), len(tried)

    def prior(self, kind: str) -> float:
        """Smoothed adoption rate, (adopted + 1) / (tried + 2): 0.5 for an edit never tried."""
        a, n = self.stats(kind)
        return (a + 1.0) / (n + 2.0)

    def rank(self, edits: list[Edit]) -> list[Edit]:
        """Order candidate edits by what the library has seen work; ties keep their given order."""
        return sorted(edits, key=lambda e: -self.prior(e.kind))

    def lessons(self, limit: int = 20) -> list[str]:
        return [e["lesson"] for e in self.entries[-limit:] if e.get("lesson")]


def compose(base: dict, probes: list[Probe], margin: float = 0.0, task_tolerance: float | None = None,
            guards: dict[str, float] | None = None) -> Composition:
    """Decide what to adopt. On each dimension the best-scoring probe is adopted if it beats the base
    score by more than `margin`, no task falls more than `task_tolerance` below the base's score on
    that task, and no guard metric (`guards`: name -> largest allowed increase) gets worse by more
    than allowed. If nothing qualifies the answer is KEEP_BASE."""
    adopted, reasons = [], []
    for dim in DIMENSIONS:
        pool = [p for p in probes if p.edit.dimension == dim and math.isfinite(p.behavior.get("score", float("nan")))]
        if not pool:
            continue
        best = max(pool, key=lambda p: p.behavior["score"])
        gain = best.behavior["score"] - base["score"]
        if gain <= margin:
            reasons.append(f"{dim}: best edit '{best.edit.description}' changed the score by {gain:+.3f}, not more than the margin {margin:.3f}")
            continue
        fell = [t for t, s in (best.behavior.get("tasks") or {}).items()
                if task_tolerance is not None and t in (base.get("tasks") or {}) and s < base["tasks"][t] - task_tolerance]
        if fell:
            reasons.append(f"{dim}: '{best.edit.description}' raised the score by {gain:+.3f} but fell behind the base on {', '.join(map(str, fell))}")
            continue
        broke = [g for g, allowed in (guards or {}).items()
                 if g in best.behavior and g in base and best.behavior[g] - base[g] > allowed]
        if broke:
            reasons.append(f"{dim}: '{best.edit.description}' raised the score by {gain:+.3f} but worsened {', '.join(broke)}")
            continue
        adopted.append(best.edit)
        reasons.append(f"{dim}: adopted '{best.edit.description}' ({gain:+.3f})")
    if not adopted:
        reasons.append("KEEP_BASE: no edit was supported by the evidence")
    return Composition(tuple(adopted), not adopted, tuple(reasons))


@dataclass
class StepResult:
    edits: list[Edit]
    probes: list[Probe]
    composition: Composition
    contrasts: dict[str, dict] = field(default_factory=dict)


def ppc_step(task_id: str, evidence: dict, proposer: Proposer, probe: Callable[[list[Edit]], dict[str, dict]], k: int,
             library: EditLibrary, margin: float = 0.0, task_tolerance: float | None = None,
             guards: dict[str, float] | None = None) -> StepResult:
    """One Propose-Probe-Compose step. `evidence` is the base run's behaviour (it must hold `score`).
    `probe` runs the candidate edits on the same task and returns label -> behaviour. At most k edits
    per dimension are probed, in the order the library ranks them. Every probed edit is recorded in
    the library with its contrast, adopted or not."""
    if k < 1:
        raise ValueError("k must be >= 1")
    proposed = library.rank(proposer.propose(evidence, k, library))
    edits = [e for dim in DIMENSIONS for e in [x for x in proposed if x.dimension == dim][:k]]
    if len({e.label for e in edits}) != len(edits):
        raise ValueError("edit labels must be unique")
    behaviors = probe(edits) if edits else {}
    probes = [Probe(e, behaviors[e.label]) for e in edits if e.label in behaviors]
    composition = compose(evidence, probes, margin, task_tolerance, guards)
    contrasts = {}
    for p in probes:
        diff = {key: p.behavior[key] - evidence[key] for key in p.behavior
                if key in evidence and isinstance(p.behavior[key], (int, float)) and isinstance(evidence[key], (int, float))}
        contrasts[p.edit.label] = diff
        took = p.edit in composition.adopted
        library.record(task_id, p, took, diff, f"{p.edit.kind} ({p.edit.dimension}) on {task_id}: score {diff.get('score', float('nan')):+.3f}; "
                       + ("adopted" if took else "not adopted"))
    return StepResult(edits, probes, composition, contrasts)


def edit_summary(edit: Edit) -> dict:
    return {**{k: v for k, v in asdict(edit).items() if k != "patch"}, "ops": list(edit.patch.ops)}
