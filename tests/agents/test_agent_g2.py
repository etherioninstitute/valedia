import ast
import math
from itertools import combinations
from pathlib import Path
from typing import Optional

import numpy as np
import pytest

from valedia_trader.agents import agent_g2 as g2

UPSTREAM = Path("/Volumes/NIMROD_SSD/Qwen-Weight-Space/sources/upstream/agent-g2/gmsv/alfworld.py")


def test_thresholds_split_tasks_into_balanced_contiguous_clusters():
    lengths = [3] * 4 + [5] * 4 + [8] * 4
    assert g2.group_thresholds(lengths, 3) == [3, 5, 8]
    assert g2.group_thresholds([2, 2, 2, 3, 9, 9, 9, 9, 10], 2) == [3, 10]           # 4 tasks against 5
    assert g2.group_thresholds([], 3) == [1, 1, 1]
    assert g2.group_thresholds([4, 4, 4], 3) == [4, 4, 4]                           # fewer distinct lengths than groups
    assert g2.group_thresholds([7, 2, 5], 1) == [7]
    with pytest.raises(ValueError):
        g2.group_thresholds([1, 2], 0)
    with pytest.raises(ValueError, match="bucket"):
        g2.group_thresholds(list(range(1, 400)), 6)


@pytest.mark.skipif(not UPSTREAM.is_file(), reason="Agent-G2 checkout is not on this machine")
def test_thresholds_match_the_upstream_function_on_random_inputs():
    tree = ast.parse(UPSTREAM.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_compute_group_thresholds")
    scope = {"np": np, "combinations": combinations, "Optional": Optional}
    exec(compile(ast.Module([fn], []), str(UPSTREAM), "exec"), scope)
    rng = np.random.default_rng(0)
    for _ in range(300):
        lengths = rng.integers(1, int(rng.integers(2, 16)), size=int(rng.integers(0, 40))).tolist()
        k = int(rng.integers(1, 6))
        assert g2.group_thresholds(lengths, k) == scope["_compute_group_thresholds"](lengths, k), (lengths, k)


def test_config_validation():
    for bad in ({"num_groups": 0}, {"sigma_mode": "wide"}, {"alpha": 0.0}, {"batch_acc_low": 0.7, "batch_acc_high": 0.6},
                {"mu_global_min": 0.9, "mu_global_max": 0.8}, {"max_length": 0.0}, {"no_prefix_ratio": 1.0}):
        with pytest.raises(ValueError):
            g2.GuidanceConfig(**bad)


def test_mu_and_sigma_follow_the_published_rules():
    g = g2.GaussianGuidance([3, 5, 8], g2.GuidanceConfig(mu_global_init=0.4))
    assert g.thresholds == [3, 5, 8] and [g.group_for_length(n) for n in (1, 3, 4, 8, 99)] == [1, 1, 2, 3, 3]
    assert g.mu(1) == pytest.approx(0.4 + 0.3 * 0.5)                    # A_k starts at 0: maximum extra help
    assert g.sigma(1) == pytest.approx(0.1)                             # variance 0 falls back to sigma_min
    g.stats[2] = g2.GroupStats(accuracy_ema=0.9, variance_ema=0.2)
    assert g.mu(2) == pytest.approx(0.4 + 0.3 * (0.5 - 0.9)) and g.sigma(2) == pytest.approx(0.2)
    g.mu_global = 0.8
    assert g.mu(1) == pytest.approx(0.8)                                # clipped at max_length
    fixed = g2.GaussianGuidance([3, 5, 8], g2.GuidanceConfig(sigma_mode="fixed_sigma"))
    assert fixed.sigma(3) == 0.25


def test_update_is_an_ema_scoreboard_with_a_stepped_global_level():
    g = g2.GaussianGuidance([3, 5, 8], g2.GuidanceConfig(mu_global_init=0.5))
    batch = [g2.Outcome("a", 1, 1.0), g2.Outcome("b", 1, 0.0), g2.Outcome("c", 1, 0.0), g2.Outcome("d", 1, 0.0),
             g2.Outcome("e", 2, 0.0)]
    m = g.update(batch)
    assert g.stats[1].accuracy_ema == pytest.approx(0.2 * 0.25) and g.stats[1].variance_ema == pytest.approx(0.2 * 0.1875)
    assert g.stats[3].accuracy_ema == 0.0                               # a cluster with no rollouts is untouched
    assert m["scoreboard_accuracy"] == pytest.approx(0.2) and g.mu_global == pytest.approx(0.6)     # below 0.4: one step up
    g.update([g2.Outcome(str(i), 1, 1.0) for i in range(4)])
    assert g.mu_global == pytest.approx(0.5)                            # above 0.6: one step down
    g.update([g2.Outcome("x", 1, 1.0), g2.Outcome("y", 1, 0.0)])
    assert g.mu_global == pytest.approx(0.5) and g.batches == 3         # inside the band: unchanged
    for _ in range(20):
        g.update([g2.Outcome("z", 2, 0.0)])
    assert g.mu_global == pytest.approx(0.8)                            # never above mu_global_max
    assert g.update([]) == {}
    with pytest.raises(KeyError):
        g.update([g2.Outcome("q", 9, 1.0)])


def test_rollouts_of_one_prompt_count_once_and_unguided_ones_stay_off_the_scoreboard():
    g = g2.GaussianGuidance([3, 5, 8], g2.GuidanceConfig(mu_global_init=0.5))
    m = g.update([g2.Outcome("a", 1, 0.0), g2.Outcome("a", 1, 2.0), g2.Outcome("b", 1, 0.0),
                  g2.Outcome("c", 1, 1.0, counted=False)])
    assert m["prompts"] == 3 and m["prompts_on_scoreboard"] == 2
    assert m["batch_accuracy"] == pytest.approx(2 / 3) and m["scoreboard_accuracy"] == pytest.approx(0.5)
    before = g.mu_global
    g.update([g2.Outcome("n", 1, 0.0, counted=False)])                   # nothing counted: the level does not move
    assert g.mu_global == before


def test_plan_samples_a_gaussian_ratio_and_maps_it_to_whole_steps():
    cfg = g2.GuidanceConfig(mu_global_init=0.35, sigma_mode="fixed_sigma", fixed_sigma=0.1, lambda_value=0.0)
    g = g2.GaussianGuidance([4, 8, 12], cfg)
    plans = [g.plan(f"t{i}", 10) for i in range(4000)]
    raw = np.array([p.sampled_ratio for p in plans])
    assert raw.mean() == pytest.approx(0.35, abs=0.01) and raw.std() == pytest.approx(0.1, abs=0.01)
    assert all(0.0 <= p.clipped_ratio <= 0.8 and p.prefix_steps == math.floor(10 * p.clipped_ratio) for p in plans)
    assert all(p.group == 3 and not p.no_prefix for p in plans)          # 10 steps: above 8, at most 12
    assert g.plan("eval", 10, train=False).prefix_steps == 0             # no guidance outside training
    assert g.plan("empty", 0).prefix_steps == 0
    with pytest.raises(ValueError):
        g.plan("bad", 3, step_sizes=[1, 2])


def test_prefix_extends_to_a_step_end_and_leaves_the_last_step_to_the_agent():
    always = g2.GuidanceConfig(mu_global_init=0.8, sigma_mode="fixed_sigma", fixed_sigma=1e-9, lambda_value=0.0)
    g = g2.GaussianGuidance([5], always, thresholds=[5])
    # sizes 10,10,10,70: 80% of 100 is 80, inside the last step, so the prefix would be all 4 steps: capped at 3
    assert g.plan("t", 4, step_sizes=[10, 10, 10, 70]).prefix_steps == 3
    # sizes 50,30,10,10: target 80 ends exactly with step 2
    assert g.plan("t", 4, step_sizes=[50, 30, 10, 10]).prefix_steps == 2
    # target 80 falls inside step 2 (40..90): extended to its end
    assert g.plan("t", 4, step_sizes=[40, 50, 5, 5]).prefix_steps == 2
    full = g2.GaussianGuidance([5], g2.GuidanceConfig(mu_global_init=1.0, mu_global_max=1.0, max_length=1.0, sigma_mode="fixed_sigma",
                                                     fixed_sigma=1e-9, lambda_value=0.0, allow_full_prefix=True), thresholds=[5])
    assert full.plan("t", 4).prefix_steps == 4
    some_unguided = g2.GaussianGuidance([5], g2.GuidanceConfig(no_prefix_ratio=0.5), thresholds=[5])
    plans = [some_unguided.plan(str(i), 5) for i in range(400)]
    share = np.mean([p.no_prefix for p in plans])
    assert 0.4 < share < 0.6 and all(p.prefix_steps == 0 and not p.outcome(1.0).counted for p in plans if p.no_prefix)


def test_state_round_trip_continues_the_same_random_stream(tmp_path):
    g = g2.GaussianGuidance([3, 5, 8, 8, 9])
    g.update([g.plan("a", 5).outcome(1.0), g.plan("b", 8).outcome(0.0)])
    g.save(tmp_path / "g2.json")
    h = g2.GaussianGuidance.load(tmp_path / "g2.json")
    assert h.thresholds == g.thresholds and h.mu_global == g.mu_global and h.batches == 1
    assert h.stats == g.stats and h.plan("n", 8) == g.plan("n", 8)


def test_guided_case_discloses_only_the_planned_prefix():
    steps = ["regime: low volatility", "signal: +0.8 sigma", "size: 4% of equity", "risk check: inside limits"]
    plan = g2.PrefixPlan("c", 1, 0.5, 0.1, 0.5, 0.5, 2, 4, False)
    case = g2.guided_case({"thesis": "hold the basket"}, steps, plan)
    assert case["expert_steps_done"] == steps[:2] and case["expert_steps_left"] == 2 and case["thesis"] == "hold the basket"
    with pytest.raises(ValueError):
        g2.guided_case({}, steps[:3], plan)


def test_closed_loop_finds_the_guidance_each_difficulty_needs():
    # A simulated agent: it solves a task when the guidance ratio exceeds what the task's difficulty
    # demands, with noise. Longer expert trajectories demand more.
    need = {3: 0.15, 6: 0.40, 9: 0.65}
    tasks = [g2.GuidedTask(f"len{n}-{i}", tuple(f"s{j}" for j in range(n))) for n in need for i in range(20)]
    sim = np.random.default_rng(5)

    def attempt(task, plan):
        p = 1.0 / (1.0 + math.exp(-12.0 * (plan.clipped_ratio - need[len(task.steps)])))
        return float(sim.random() < p)

    g = g2.GaussianGuidance([len(t.steps) for t in tasks], g2.GuidanceConfig(mu_global_init=0.8, seed=3))
    history = g2.run_profile(g, tasks, attempt, batches=120, batch_size=48)
    assert g.thresholds == [3, 6, 9] and len(history) == 120
    late = history[60:]
    assert 0.4 <= np.mean([h["scoreboard_accuracy"] for h in late]) <= 0.6          # held in the target band
    mu = [np.mean([h[f"group_{k}/mu"] for h in late]) for k in (1, 2, 3)]
    assert mu[0] < mu[1] < mu[2]                                                     # harder clusters get more help
    acc = [np.mean([h[f"group_{k}/A"] for h in late]) for k in (1, 2, 3)]
    assert acc[0] > acc[2]                                                           # and still succeed less often
    assert history[0]["mu_global_before"] == 0.8 and np.mean([h["mu_global_after"] for h in late]) < 0.7
    with pytest.raises(ValueError):
        g2.run_profile(g, [], attempt, 1, 1)


def test_sizing_tasks_have_a_checkable_expert_chain():
    tasks = g2.sizing_tasks(30, np.random.default_rng(2))
    assert {len(t.steps) for t in tasks} == {3, 5, 7}
    for t in tasks:
        assert t.steps[-1].startswith("answer") and math.isfinite(t.payload["answer"]) and t.payload["answer"] >= 0.0
        assert "question" in t.payload
    t = next(x for x in tasks if len(x.steps) == 7)
    p = t.payload
    kelly = max(0.0, (p["p"] * p["b"] - (1 - p["p"])) / p["b"])
    assert p["answer"] == pytest.approx(min(kelly * p["multiplier"] * p["regime"], p["cap"]) * p["equity"])


def test_chat_attempt_grades_the_models_number_and_never_raises():
    task = next(x for x in g2.sizing_tasks(9, np.random.default_rng(4)) if len(x.steps) == 5)
    plan = g2.PrefixPlan("t", 2, 0.5, 0.1, 0.5, 0.5, 2, 5, False)
    seen = {}

    def transport(url, payload, timeout):
        seen["prompt"] = payload["messages"][0]["content"]
        return {"choices": [{"message": {"content": seen["reply"]}}]}

    attempt = g2.chat_attempt("http://127.0.0.1:11434/v1", "m", transport=transport)
    seen["reply"] = '{"answer": %r}' % (task.payload["answer"] * 1.004)
    assert attempt(task, plan) == 1.0                                   # inside the 1% tolerance
    assert task.steps[0] in seen["prompt"] and task.steps[1] in seen["prompt"] and task.steps[2] not in seen["prompt"]
    seen["reply"] = '{"answer": %r}' % (task.payload["answer"] * 1.2)
    assert attempt(task, plan) == 0.0
    seen["reply"] = "I think it is roughly a lot"
    assert attempt(task, plan) == 0.0
    assert g2.chat_attempt("http://127.0.0.1:1/v1", "m", transport=lambda *a: 1 / 0)(task, plan) == 0.0
    with pytest.raises(ValueError):
        g2.chat_attempt("http://example.com/v1", "m")
