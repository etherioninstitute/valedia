import ast
import math
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from valedia_trader.agents import harness2 as h2
from valedia_trader.agents import meta_n as mn

UPSTREAM = Path("/Volumes/NIMROD_SSD/Qwen-Weight-Space/sources/meta-n/meta_n/core/archive.py")


def cand(cid, scores, parent=None, patch=None):
    return mn.Candidate.evaluated(cid, [mn.Trace(t, s) for t, s in scores.items()], parent, patch)


# ---- Meta-N

def test_layers_stack_and_a_later_layer_can_revert_an_earlier_one():
    base = {"entry": {"lookback": 50, "enter": 0.02}, "market": ["A", "B", "C"], "risk": {"stop": 0.1}}
    l2 = mn.Patch(({"op": "set", "path": "entry.lookback", "value": 100},), "slower signal")
    l3 = mn.Patch(({"op": "remove", "path": "market", "value": "C"}, {"op": "set", "path": "risk.stop", "value": None}))
    l4 = mn.Patch(({"op": "revert", "depth": 2}, {"op": "append", "path": "market", "value": "D"}), "undo the slower signal")
    assert mn.apply_chain(base, [l2])["entry"]["lookback"] == 100 and base["entry"]["lookback"] == 50
    out = mn.apply_chain(base, [l2, l3, l4])
    assert out == {"entry": {"lookback": 50, "enter": 0.02}, "market": ["A", "B", "D"], "risk": {"stop": None}}
    assert mn.apply_chain(base, [mn.Patch(({"op": "set", "path": "sizing.rule", "value": "equal"},))])["sizing"] == {"rule": "equal"}
    with pytest.raises(mn.PatchError):
        mn.apply_chain(base, [mn.Patch(({"op": "revert", "depth": 3},))])          # cannot revert itself or above
    with pytest.raises(mn.PatchError):
        mn.apply_chain(base, [mn.Patch(({"op": "remove", "path": "entry.lookback", "value": 1},))])
    with pytest.raises(mn.PatchError):
        mn.apply_chain(base, [mn.Patch(({"op": "append", "path": "nope.list", "value": 1},))])
    for bad in ({"op": "exec", "code": "x"}, {"op": "set", "path": ""}, {"op": "revert", "depth": 1}, "set"):
        with pytest.raises(mn.PatchError):
            mn.Patch((bad,))
    with pytest.raises(ValueError, match="refused"):
        mn.apply_chain(base, [l2], validate=lambda spec: (_ for _ in ()).throw(ValueError("refused")))


def test_archive_only_grows_and_tracks_best_and_per_task_best():
    a = mn.Archive()
    seed = cand("seed", {"w1": 0.2, "w2": 0.4})
    better = cand("c1", {"w1": 0.5, "w2": 0.3}, seed, mn.Patch(({"op": "set", "path": "x", "value": 1},)))
    worse = cand("c2", {"w1": 0.1, "w2": 0.9}, seed, mn.Patch(({"op": "set", "path": "x", "value": 2},)))
    broken = cand("c3", {"w1": float("nan")}, seed, mn.Patch(({"op": "set", "path": "x", "value": 3},)))
    for c in (seed, better, worse, broken):
        assert a.add(c)
    assert not a.add(cand("c1", {"w1": 9.0})) and len(a) == 4                       # replay is harmless
    assert a.best().candidate_id == "c2" and a.per_task_best() == {"w1": (0.5, "c1"), "w2": (0.9, "c2")}
    assert a.elite_ids() == ["c2", "c1"] and seed.num_children == 3 and better.depth == 2
    assert a.score_range() == pytest.approx(0.5 - 0.3) and [c.candidate_id for c in a.lineage("c1")] == ["seed", "c1"]
    assert a.to_dict()["candidates"][1]["layers"][0]["ops"][0]["value"] == 1 and a.to_dict()["candidates"][3]["mean_score"] is None


def test_regressions_stay_in_the_archive_but_are_not_bred_from():
    a = mn.Archive()
    seed = cand("seed", {"w1": 0.30, "w2": 0.30})
    up = cand("up", {"w1": 0.50, "w2": 0.50}, seed, mn.Patch(({"op": "set", "path": "x", "value": 1},)))
    down = cand("down", {"w1": 0.10, "w2": 0.10}, up, mn.Patch(({"op": "set", "path": "x", "value": 2},)))
    lopsided = cand("lopsided", {"w1": 0.70, "w2": 0.32}, up, mn.Patch(({"op": "set", "path": "x", "value": 3},)))
    for c in (seed, up, down, lopsided):
        a.add(c)
    pool = {c.candidate_id for c in a.breedable_pool(max_depth=6)}
    assert "down" not in pool and a.regressed("down") and {"seed", "up"} <= pool
    assert "lopsided" in pool                                    # it is an elite (best on w1 and on the mean)
    assert a.regressed("lopsided")                               # though it fell behind its parent on w2
    assert not a.regressed("up") and not a.regressed("seed")
    assert a.breedable_pool(max_depth=1) == [] and {c.candidate_id for c in a.breedable_pool(max_depth=2)} == {"seed"}
    parents = a.select_parents(3, random.Random(0), a.breedable_pool(6))
    assert [p.candidate_id for p in parents[:2]] == ["lopsided", "up"] and all(p.candidate_id != "down" for p in parents)


def test_selection_weights_are_rank_plus_ucb_and_scale_free():
    a = mn.Archive(novelty_alpha=0.3)
    cs = [cand(f"c{i}", {"t": s}) for i, s in enumerate([0.1, 0.5, 0.5, 0.9])]
    cs[3].num_children = 3
    w = a.selection_weights(cs)
    assert w[0] == pytest.approx(0.0 + 0.3 * math.sqrt(math.log(4) / 1)) and w[1] == w[2] == pytest.approx(1 / 3 + 0.3 * math.sqrt(math.log(4)))
    assert w[3] == pytest.approx(1.0 + 0.3 * math.sqrt(math.log(4) / 4))
    scaled = [cand(f"s{i}", {"t": 1000 * s - 7}) for i, s in enumerate([0.1, 0.5, 0.5, 0.9])]
    scaled[3].num_children = 3
    assert a.selection_weights(scaled) == pytest.approx(w)
    assert mn.ucb_bonus(0.3, 1, 0) == 0.0 and a.selection_weights([cs[0]]) == [1e-6]
    assert a.select_parents(2) == []


@pytest.mark.skipif(not UPSTREAM.is_file(), reason="Meta-N checkout is not on this machine")
def test_weights_and_breeding_rule_match_the_upstream_methods():
    tree = ast.parse(UPSTREAM.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Archive")
    wanted = {"ucb_exploration_bonus", "_selection_weights", "_is_breedable"}
    fns = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    for fn in fns:
        fn.decorator_list, fn.returns = [], None
        for arg in fn.args.args + fn.args.kwonlyargs:
            arg.annotation = None
    scope = {"math": math}
    exec(compile(ast.fix_missing_locations(ast.Module(fns, [])), str(UPSTREAM), "exec"), scope)
    rng = random.Random(1)
    for _ in range(100):
        ours = mn.Archive(novelty_alpha=rng.uniform(0.0, 1.0))
        cands = [cand("seed", {"a": rng.random(), "b": rng.random()})]
        ours.add(cands[0])
        for i in range(rng.randint(1, 8)):
            c = cand(f"c{i}", {"a": rng.random(), "b": rng.choice([rng.random(), float("nan")])}, rng.choice(cands),
                     mn.Patch(({"op": "set", "path": "x", "value": i},)))
            ours.add(c)
            cands.append(c)
        shim = SimpleNamespace(novelty_alpha=ours.novelty_alpha, within_task_depth_bonus=0.0, _by_id=ours._by_id,
                               ucb_exploration_bonus=scope["ucb_exploration_bonus"])
        assert ours.selection_weights(cands) == pytest.approx(scope["_selection_weights"](shim, cands))
        tol, elites = 0.05 * ours.score_range(), set(ours.elite_ids())
        for c in cands:
            assert ours._breedable(c, elites, tol) == scope["_is_breedable"](shim, c, elites, tol), c.candidate_id


# ---- Harness2

def edit(label, dim, kind, path="x", value=1):
    return h2.Edit(label, dim, kind, f"{kind} via {label}", mn.Patch(({"op": "set", "path": path, "value": value},)))


class FixedProposer:
    def __init__(self, edits):
        self.edits, self.calls = edits, []

    def propose(self, evidence, k, library):
        self.calls.append((evidence["score"], k))
        return list(self.edits)


def test_compose_adopts_one_edit_per_dimension_or_keeps_the_base():
    base = {"score": 0.50, "tasks": {"w1": 0.4, "w2": 0.6}, "drawdown": 0.10}
    probes = [h2.Probe(edit("inv1", "INV", "widen_stop", "risk.stop", 0.2), {"score": 0.70, "tasks": {"w1": 0.5, "w2": 0.7}, "drawdown": 0.11}),
              h2.Probe(edit("inv2", "INV", "tighten_stop", "risk.stop", 0.05), {"score": 0.60, "tasks": {"w1": 0.5, "w2": 0.6}, "drawdown": 0.09}),
              h2.Probe(edit("dom1", "DOM", "longer_lookback", "entry.lookback", 200), {"score": 0.65, "tasks": {"w1": 0.9, "w2": 0.2}, "drawdown": 0.10})]
    both = h2.compose(base, probes, margin=0.05)
    assert [e.label for e in both.adopted] == ["inv1", "dom1"] and not both.keep_base
    assert both.patch(3).ops == ({"op": "set", "path": "risk.stop", "value": 0.2}, {"op": "set", "path": "entry.lookback", "value": 200})
    guarded = h2.compose(base, probes, margin=0.05, task_tolerance=0.1)
    assert [e.label for e in guarded.adopted] == ["inv1"] and "fell behind the base on w2" in " ".join(guarded.reasons)
    strict = h2.compose(base, probes, margin=0.05, guards={"drawdown": 0.005})
    assert [e.label for e in strict.adopted] == ["dom1"] and "worsened drawdown" in " ".join(strict.reasons)
    none = h2.compose(base, probes, margin=0.5)
    assert none.keep_base and none.adopted == () and none.patch() is None and none.reasons[-1].startswith("KEEP_BASE")
    assert h2.compose(base, []).keep_base
    with pytest.raises(ValueError):
        h2.Edit("x", "STYLE", "k", "d", mn.Patch(()))


def test_a_step_probes_at_most_k_edits_per_dimension_and_records_every_one(tmp_path):
    library = h2.EditLibrary(tmp_path / "memory" / "edits.json")
    proposer = FixedProposer([edit("inv1", "INV", "widen_stop"), edit("inv2", "INV", "tighten_stop"), edit("dom1", "DOM", "longer_lookback"),
                              edit("dom2", "DOM", "drop_worst_symbol")])
    probed = []

    def probe(edits):
        probed.extend(e.label for e in edits)
        return {e.label: {"score": {"inv1": 0.4, "dom1": 0.9}[e.label], "trades": 10} for e in edits}

    step = h2.ppc_step("idea-7", {"score": 0.5, "trades": 12}, proposer, probe, k=1, library=library, margin=0.1)
    assert probed == ["inv1", "dom1"] and proposer.calls == [(0.5, 1)]            # 2k probes for k = 1
    assert [e.label for e in step.composition.adopted] == ["dom1"]
    assert step.contrasts["dom1"] == {"score": pytest.approx(0.4), "trades": -2}
    assert library.stats("longer_lookback") == (1, 1) and library.stats("widen_stop") == (0, 1) and library.stats("tighten_stop") == (0, 0)
    assert "longer_lookback (DOM) on idea-7: score +0.400; adopted" in library.lessons()
    reopened = h2.EditLibrary(tmp_path / "memory" / "edits.json")
    assert len(reopened.entries) == 2 and reopened.prior("longer_lookback") == pytest.approx(2 / 3)
    with pytest.raises(ValueError):
        h2.ppc_step("idea-7", {"score": 0.5}, proposer, probe, k=0, library=library)


def test_the_library_changes_what_is_tried_next():
    library = h2.EditLibrary()
    for task in ("idea-1", "idea-2", "idea-3"):
        library.record(task, h2.Probe(edit("a", "INV", "widen_stop"), {"score": 0.1}), False, {"score": -0.2}, "widen_stop did not help")
        library.record(task, h2.Probe(edit("b", "INV", "add_time_limit"), {"score": 0.9}), True, {"score": 0.3}, "time limit helped")
    assert library.prior("widen_stop") == pytest.approx(1 / 5) and library.prior("add_time_limit") == pytest.approx(4 / 5)
    assert library.prior("never_tried") == 0.5
    ranked = library.rank([edit("x", "INV", "widen_stop"), edit("y", "INV", "never_tried"), edit("z", "INV", "add_time_limit")])
    assert [e.kind for e in ranked] == ["add_time_limit", "never_tried", "widen_stop"]
    proposer = FixedProposer([edit("x", "INV", "widen_stop"), edit("z", "INV", "add_time_limit")])
    probed = []
    h2.ppc_step("idea-4", {"score": 0.5}, proposer, lambda es: probed.extend(e.kind for e in es) or {e.label: {"score": 0.5} for e in es},
                k=1, library=library)
    assert probed == ["add_time_limit"]                          # the edit that has worked before goes first
    assert h2.edit_summary(edit("x", "INV", "widen_stop"))["ops"] == [{"op": "set", "path": "x", "value": 1}]
