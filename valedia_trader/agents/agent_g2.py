"""Agent-G2 Gaussian guidance: how much of an expert's worked solution an agent is shown.

A port of the schedule in Agent-G2 (Wang et al., "Gaussian Guidance for Agentic Reinforcement
Learning", arXiv 2608.23318; upstream `gmsv/alfworld.py`):

1. Tasks are clustered by the length of their expert trajectory (a cheap difficulty signal).
2. Each cluster k keeps moving averages of success A_k and of its variance V_k. A global level
   mu_g moves one step up when a batch succeeds too rarely and one step down when too often.
3. For each task a guidance ratio is drawn from N(mu_k, sigma_k) with
   mu_k = clip(mu_g + lambda (0.5 - A_k)), sigma_k = max(sigma_min, gamma V_k), and that share of the
   expert trajectory is handed to the agent before it works on its own.
4. The same terminal rewards update A_k, V_k and mu_g for the next batch. No probe rollouts.

What this is used for here. Upstream this is a training curriculum: it keeps tasks near 50% success,
where a policy learns fastest. Valedia does not fine-tune its models, so the schedule is used to
measure them: `run_profile` finds, for each difficulty cluster, how much of the quant engine's
analysis a model must be given to reach a target success rate. A member that only succeeds when
handed nearly all of it adds no independent judgement to the panel. The schedule is not in the
live decision path, where the aim is accuracy and not a 50% success rate.

Deviations from upstream: step sizes default to one unit per step (upstream measures tokens); a
batch with no counted rollout leaves mu_g unchanged (upstream reads it as 0% success).
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field, replace
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np

__all__ = ["GuidanceConfig", "GroupStats", "PrefixPlan", "Outcome", "group_thresholds", "GaussianGuidance",
           "GuidedTask", "guided_case", "run_profile", "sizing_tasks", "chat_attempt", "main"]

_MAX_PARTITIONS = 2_000_000


@dataclass(frozen=True)
class GuidanceConfig:
    """Defaults are upstream's (verl/trainer/config/ppo_trainer.yaml), with the two settings its
    paper scripts change: three clusters and variance-driven sigma."""
    num_groups: int = 3
    sigma_mode: str = "dynamic_sigma"        # or "fixed_sigma"
    fixed_sigma: float = 0.25
    sigma_min: float = 0.1
    gamma: float = 1.0
    mu_global_init: float = 0.8
    mu_global_min: float = 0.0
    mu_global_max: float = 0.8
    global_step_delta: float = 0.1
    batch_acc_low: float = 0.4
    batch_acc_high: float = 0.6
    alpha: float = 0.2
    lambda_value: float = 0.3
    max_length: float = 0.8
    initial_group_accuracy: float = 0.0
    initial_group_variance: float = 0.0
    no_prefix_ratio: float = 0.0             # share of training tasks run with no guidance, kept off the scoreboard
    allow_full_prefix: bool = False
    seed: int = 1

    def __post_init__(self):
        if self.num_groups < 1 or self.sigma_mode not in ("dynamic_sigma", "fixed_sigma"):
            raise ValueError("num_groups must be >= 1 and sigma_mode dynamic_sigma or fixed_sigma")
        if not 0.0 <= self.mu_global_min <= self.mu_global_max <= 1.0 or not 0.0 < self.max_length <= 1.0:
            raise ValueError("guidance levels must lie in [0, 1]")
        if not 0.0 < self.alpha <= 1.0 or not 0.0 <= self.batch_acc_low <= self.batch_acc_high <= 1.0:
            raise ValueError("alpha must be in (0, 1] and the accuracy band ordered inside [0, 1]")
        if not 0.0 <= self.no_prefix_ratio < 1.0:
            raise ValueError("no_prefix_ratio must be in [0, 1)")


@dataclass
class GroupStats:
    accuracy_ema: float
    variance_ema: float


@dataclass(frozen=True)
class PrefixPlan:
    task_id: str
    group: int
    mu: float
    sigma: float
    sampled_ratio: float
    clipped_ratio: float
    prefix_steps: int
    total_steps: int
    no_prefix: bool            # forced to zero guidance and excluded from the scoreboard

    def outcome(self, reward: float) -> "Outcome":
        return Outcome(self.task_id, self.group, float(reward), not self.no_prefix)


@dataclass(frozen=True)
class Outcome:
    task_id: str
    group: int
    reward: float
    counted: bool = True


def group_thresholds(lengths: Sequence[int], num_groups: int) -> list[int]:
    """Upper length bound of each cluster. Unique lengths are split into `num_groups` contiguous
    runs holding as equal a number of tasks as possible (smallest max-min spread, then smallest total
    deviation from the equal share), exactly as upstream does."""
    if num_groups < 1:
        raise ValueError("num_groups must be >= 1")
    counts_in = [int(x) for x in lengths]
    if not counts_in:
        return [1] * num_groups
    unique, counts = np.unique(np.asarray(counts_in, dtype=np.int32), return_counts=True)
    unique, counts = unique.tolist(), counts.tolist()
    if len(unique) < num_groups:
        out: list[int] = []
        last = None
        for q in np.linspace(0.0, 1.0, num_groups)[1:]:
            value = int(np.quantile(counts_in, q))
            if last is None or value > last:
                out.append(max(1, value))
                last = value
        while len(out) < num_groups:
            out.append(out[-1] if out else 1)
        return out[:num_groups]
    if math.comb(len(unique) - 1, num_groups - 1) > _MAX_PARTITIONS:
        raise ValueError("too many distinct trajectory lengths for this many groups; bucket the lengths first")
    target = sum(counts) / float(num_groups)
    cum = np.cumsum(counts).tolist()
    best, best_score = None, None
    for bounds in combinations(range(1, len(unique)), num_groups - 1):
        sizes, start = [], 0
        for b in (*bounds, len(unique)):
            sizes.append(int(cum[b - 1] - (cum[start - 1] if start > 0 else 0)))
            start = b
        if any(s <= 0 for s in sizes):
            continue
        score = (int(max(sizes) - min(sizes)), float(sum(abs(s - target) for s in sizes)))
        if best_score is None or score < best_score:
            best, best_score = bounds, score
    if best is None:
        return [max(1, int(unique[-1]))] * num_groups
    return [max(1, int(unique[b - 1])) for b in best] + [max(1, int(unique[-1]))]


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class GaussianGuidance:
    """The schedule: `plan` a guidance depth for a task, then `update` from the batch's rewards."""

    def __init__(self, lengths: Iterable[int] = (), config: GuidanceConfig | None = None,
                 thresholds: Sequence[int] | None = None) -> None:
        self.config = config or GuidanceConfig()
        cfg = self.config
        self.thresholds = [int(t) for t in thresholds] if thresholds is not None else group_thresholds(list(lengths), cfg.num_groups)
        if not self.thresholds or any(b < a for a, b in zip(self.thresholds, self.thresholds[1:])):
            raise ValueError("thresholds must be a non-empty non-decreasing list")
        self.stats = {g: GroupStats(cfg.initial_group_accuracy, cfg.initial_group_variance)
                      for g in range(1, len(self.thresholds) + 1)}
        self.mu_global = _clip(cfg.mu_global_init, cfg.mu_global_min, cfg.mu_global_max)
        self.rng = np.random.default_rng(cfg.seed)
        self.batches = 0

    # ---- schedule
    def group_for_length(self, total_steps: int) -> int:
        for g, bound in enumerate(self.thresholds, start=1):
            if total_steps <= bound:
                return g
        return len(self.thresholds)

    def mu(self, group: int) -> float:
        cfg = self.config
        return _clip(self.mu_global + cfg.lambda_value * (0.5 - self.stats[group].accuracy_ema), 0.0, cfg.max_length)

    def sigma(self, group: int) -> float:
        cfg = self.config
        if cfg.sigma_mode == "fixed_sigma":
            return cfg.fixed_sigma
        return max(cfg.sigma_min, cfg.gamma * self.stats[group].variance_ema)

    def plan(self, task_id: str, total_steps: int, step_sizes: Sequence[float] | None = None,
             train: bool = True) -> PrefixPlan:
        """Guidance depth for one task. `step_sizes` weights the steps (upstream: tokens per step);
        the prefix is extended to the end of the step the target falls in. With `train=False` no
        guidance is given, as upstream does on validation. The agent always keeps at least the last
        step unless `allow_full_prefix`."""
        cfg = self.config
        if total_steps < 0 or (step_sizes is not None and len(step_sizes) != total_steps):
            raise ValueError("total_steps must be >= 0 and match step_sizes")
        group = self.group_for_length(total_steps)
        mu, sigma = self.mu(group), self.sigma(group)
        no_prefix = bool(train and cfg.no_prefix_ratio > 0.0 and self.rng.random() < cfg.no_prefix_ratio)
        if not train or no_prefix or total_steps == 0:
            return PrefixPlan(str(task_id), group, mu, sigma, 0.0, 0.0, 0, total_steps, no_prefix)
        sampled = float(self.rng.normal(loc=mu, scale=max(sigma, 1e-8)))
        clipped = _clip(sampled, 0.0, cfg.max_length)
        ends = np.cumsum(np.ones(total_steps) if step_sizes is None else np.asarray(step_sizes, dtype=float))
        total = float(ends[-1])
        target = max(0, min(int(math.floor(total * clipped)), int(total)))
        if target <= 0:
            keep = 0
        elif target >= total:
            keep = total_steps
        else:
            keep = int(np.argmax(ends >= target)) + 1
        keep = min(keep, total_steps if cfg.allow_full_prefix else max(total_steps - 1, 0))
        return PrefixPlan(str(task_id), group, mu, sigma, sampled, clipped, keep, total_steps, False)

    def update(self, outcomes: Iterable[Outcome]) -> dict[str, float]:
        """Refresh the scoreboard from one batch. Several outcomes with the same task_id are rollouts
        of one prompt: the prompt counts as solved if any of them earned a positive reward."""
        cfg = self.config
        flags: dict[str, list[float]] = defaultdict(list)
        group_of: dict[str, int] = {}
        counted: dict[str, bool] = {}
        for o in outcomes:
            if o.group not in self.stats:
                raise KeyError(f"unknown group {o.group}")
            flags[o.task_id].append(float(o.reward > 0.0))
            group_of[o.task_id] = o.group
            counted[o.task_id] = counted.get(o.task_id, True) and o.counted
        if not flags:
            return {}
        all_acc = [max(v) for v in flags.values()]
        by_group: dict[int, list[float]] = defaultdict(list)
        for task, v in flags.items():
            if counted[task]:
                by_group[group_of[task]].append(max(v))
        scored = [a for accs in by_group.values() for a in accs]
        metrics = {"batch_accuracy": float(np.mean(all_acc)), "prompts": float(len(flags)),
                   "prompts_on_scoreboard": float(len(scored)), "mu_global_before": float(self.mu_global)}
        for g, accs in by_group.items():
            values = np.asarray(accs, dtype=float)
            s = self.stats[g]
            s.accuracy_ema = (1.0 - cfg.alpha) * s.accuracy_ema + cfg.alpha * float(values.mean())
            s.variance_ema = (1.0 - cfg.alpha) * s.variance_ema + cfg.alpha * float(values.var())
            metrics[f"group_{g}/batch_acc"] = float(values.mean())
        if scored:
            acc = float(np.mean(scored))
            metrics["scoreboard_accuracy"] = acc
            if acc < cfg.batch_acc_low:
                self.mu_global += cfg.global_step_delta
            elif acc > cfg.batch_acc_high:
                self.mu_global -= cfg.global_step_delta
            self.mu_global = _clip(self.mu_global, cfg.mu_global_min, cfg.mu_global_max)
        metrics["mu_global_after"] = float(self.mu_global)
        for g, s in self.stats.items():
            metrics[f"group_{g}/A"], metrics[f"group_{g}/V"] = float(s.accuracy_ema), float(s.variance_ema)
            metrics[f"group_{g}/mu"], metrics[f"group_{g}/sigma"] = self.mu(g), self.sigma(g)
        self.batches += 1
        return metrics

    # ---- persistence
    def state(self) -> dict:
        return {"config": asdict(self.config), "thresholds": list(self.thresholds), "mu_global": self.mu_global,
                "batches": self.batches, "stats": {str(g): asdict(s) for g, s in self.stats.items()},
                "rng": self.rng.bit_generator.state}

    @classmethod
    def from_state(cls, state: dict) -> "GaussianGuidance":
        obj = cls(config=GuidanceConfig(**state["config"]), thresholds=state["thresholds"])
        obj.mu_global, obj.batches = float(state["mu_global"]), int(state["batches"])
        obj.stats = {int(g): GroupStats(**s) for g, s in state["stats"].items()}
        obj.rng.bit_generator.state = state["rng"]
        return obj

    def save(self, path: str | Path) -> None:
        p = Path(path)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.state(), indent=2))
        tmp.replace(p)

    @classmethod
    def load(cls, path: str | Path) -> "GaussianGuidance":
        return cls.from_state(json.loads(Path(path).read_text()))


# ---- measuring a model with the schedule

@dataclass(frozen=True)
class GuidedTask:
    """A task with an expert's worked solution: `steps` in order, the last one being the answer."""
    task_id: str
    steps: tuple[str, ...]
    payload: Any = None
    step_sizes: tuple[float, ...] | None = field(default=None)


def guided_case(case: dict, steps: Sequence[str], plan: PrefixPlan) -> dict:
    """A panel case at the planned guidance depth: the first `plan.prefix_steps` expert steps are
    disclosed and the member is told how many are left to work out."""
    if plan.total_steps != len(steps):
        raise ValueError("the plan was made for a different number of steps")
    return {**case, "expert_steps_done": list(steps[: plan.prefix_steps]), "expert_steps_left": len(steps) - plan.prefix_steps}


def run_profile(guidance: GaussianGuidance, tasks: Sequence[GuidedTask],
                attempt: Callable[[GuidedTask, PrefixPlan], float], batches: int, batch_size: int,
                rollouts: int = 1) -> list[dict[str, float]]:
    """Run the closed loop: each batch draws tasks, plans their guidance depth, lets `attempt` solve
    them from that depth (reward > 0 means solved) and updates the schedule. Returns the metrics of
    every batch; `guidance.mu(k)` afterwards is the depth cluster k needs to sit in the target band."""
    if not tasks or batches < 1 or batch_size < 1 or rollouts < 1:
        raise ValueError("need tasks and positive batches, batch_size and rollouts")
    history = []
    for b in range(batches):
        picks = guidance.rng.choice(len(tasks), size=batch_size, replace=len(tasks) < batch_size)
        outcomes, depth = [], []
        for i, k in enumerate(picks):
            task = tasks[int(k)]
            plan = replace(guidance.plan(task.task_id, len(task.steps), task.step_sizes), task_id=f"{b}:{i}:{task.task_id}")
            depth.append(plan.prefix_steps / max(plan.total_steps, 1))
            outcomes += [plan.outcome(attempt(task, plan)) for _ in range(rollouts)]
        metrics = guidance.update(outcomes)
        metrics["mean_guidance_given"] = float(np.mean(depth))
        history.append(metrics)
    return history


# ---- a concrete benchmark: the system's own position-sizing arithmetic

def sizing_tasks(n: int, rng: np.random.Generator) -> list[GuidedTask]:
    """Position-sizing problems with an expert chain of 3, 5 or 7 steps, cycled. The expert is the
    Level 8 Kelly code, so a model is graded against what the engine itself would compute."""
    from valedia_trader.quant_engine import level8_portfolio_risk as risk
    tasks = []
    for i in range(n):
        length = (3, 5, 7)[i % 3]
        p, b = round(float(rng.uniform(0.52, 0.68)), 2), round(float(rng.uniform(0.8, 2.5)), 1)
        equity = float(rng.choice([25_000, 50_000, 100_000, 250_000]))
        mult = float(rng.choice([0.25, 0.5])) if length >= 5 else 1.0
        regime = float(rng.choice([0.5, 0.8])) if length == 7 else 1.0
        cap = float(rng.choice([0.05, 0.10])) if length == 7 else 1.0
        full = float(risk.kelly_fraction(p, b))
        frac, sized = full * mult, min(full * mult * regime, cap)
        answer = sized * equity
        ask = [f"A bet wins with probability {p} and pays {b} to 1. Account equity is ${equity:,.0f}."]
        steps = [f"edge per unit staked = p*b - (1-p) = {p * b - (1 - p):.4f}", f"full Kelly fraction = edge / b = {full:.4f}"]
        if length >= 5:
            ask.append(f"Use {mult} times the Kelly fraction.")
            steps += [f"Kelly multiplier to apply = {mult}", f"fractional Kelly = {full:.4f} * {mult} = {frac:.4f}"]
        if length == 7:
            ask.append(f"The current regime scales positions by {regime}, and no position may exceed {cap:.0%} of equity.")
            steps += [f"after the regime scale = {frac:.4f} * {regime} = {frac * regime:.4f}",
                      f"after the {cap:.0%} cap = {sized:.4f}"]
        steps.append(f"answer = fraction * equity = {answer:.2f}")
        ask.append("How many dollars should be staked? A negative edge means stake nothing.")
        tasks.append(GuidedTask(f"sizing-{i}", tuple(steps), {"question": " ".join(ask), "answer": answer, "p": p, "b": b,
                                                               "equity": equity, "multiplier": mult, "regime": regime, "cap": cap}))
    return tasks


def chat_attempt(base_url: str, model: str, tolerance: float = 0.01, timeout: float = 120.0,
                 transport: Callable[..., Any] | None = None) -> Callable[[GuidedTask, PrefixPlan], float]:
    """`attempt` function for run_profile: ask a local OpenAI-compatible model to finish a task from
    the planned depth. Reward 1 when its number is within `tolerance` of the expert answer. Any
    failure of the model or the transport is a failed attempt, never an exception."""
    from valedia_trader.agents import panel
    if not panel._loopback(base_url):
        raise ValueError("profiling runs against local models only")
    http = transport or panel._http_json

    def attempt(task: GuidedTask, plan: PrefixPlan) -> float:
        done = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(task.steps[: plan.prefix_steps])) or "(none)"
        prompt = (f"{task.payload['question']}\n\nWork already done by the desk's quant ({plan.prefix_steps} of "
                  f"{plan.total_steps} steps):\n{done}\n\nFinish the calculation. Reply with JSON only: "
                  '{"answer": <dollars as a number>}')
        try:
            body = http(base_url.rstrip("/") + "/chat/completions",
                        {"model": model, "temperature": 0, "max_tokens": 300, "reasoning_effort": "none",
                         "messages": [{"role": "user", "content": prompt}]}, timeout)
            text = body["choices"][0]["message"]["content"]
            value = float(json.loads(text[text.index("{"): text.rindex("}") + 1])["answer"])
        except Exception:
            return 0.0
        truth = float(task.payload["answer"])
        return float(abs(value - truth) <= tolerance * max(abs(truth), 1.0))

    return attempt


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="agent_g2", description="Measure how much expert guidance a local model needs.")
    ap.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--batches", type=int, default=8)
    ap.add_argument("--batch-size", type=int, default=12)
    ap.add_argument("--tasks", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="artifacts/agent_g2_profile.json")
    args = ap.parse_args(argv)
    tasks = sizing_tasks(args.tasks, np.random.default_rng(args.seed))
    guidance = GaussianGuidance([len(t.steps) for t in tasks], GuidanceConfig(seed=args.seed))
    history = run_profile(guidance, tasks, chat_attempt(args.base_url, args.model), args.batches, args.batch_size)
    report = {"model": args.model, "base_url": args.base_url, "thresholds": guidance.thresholds, "batches": history,
              "guidance_needed_by_group": {str(g): guidance.mu(g) for g in guidance.stats},
              "success_ema_by_group": {str(g): s.accuracy_ema for g, s in guidance.stats.items()},
              "state": guidance.state()}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2))
    for i, h in enumerate(history):
        print(f"batch {i + 1:2d}: success {h.get('scoreboard_accuracy', float('nan')):.2f}  guidance given "
              f"{h['mean_guidance_given']:.2f}  mu_global {h['mu_global_after']:.2f}")
    print("guidance needed by cluster (steps <= %s):" % guidance.thresholds,
          {g: round(guidance.mu(g), 2) for g in guidance.stats})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
