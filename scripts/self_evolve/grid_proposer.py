"""grid_proposer.py — deterministic, offline, no-LLM policy proposer for the policy lab.

Plugged into self-evolve's PROPOSE step by sie_shim.py (`evolve --proposer grid`).
It is a PROPOSER only: self-evolve's A-tier grader (the lab's pytest suite) and its
deterministic acceptor still decide; the PATCH allowlist still admits only
policy/candidate.json.

Inputs (LAB.md-visible only, read from the sandbox worktree):
  policy/baseline.json, policy/candidate.json, data/visible.jsonl and the lab's own
  frozen judge/backtest_gate.py (imported). It NEVER reads the holdout, the ledger
  snapshot, pins.json or anything under .git/ — the holdout stays out-of-sample.

Search space (bounded, hill-climb moves applied to the CURRENT candidate, in this
fixed order; categories = sorted canonical categories present in visible trades):
  1. block one more category            (k moves)
  2. block one more PAIR of categories  (k*(k-1)/2 moves)
  3. size_mult[c] = 0.5 / 0.75 for one c (2k moves)
  4. min_edge in MIN_EDGE_GRID          (only if some visible trade carries an edge)
Capped at MAX_CANDIDATES (200); the rest is dropped and reported as truncated.

Scoring (lab judge, deterministic seed):
  * fee-true replay net on visible; keep moves with candidate n_taken >= gate min_n
    and net strictly above the current candidate's net;
  * best-first (net desc, then canonical JSON), run the full gate (paired bootstrap
    Monte-Carlo, N_BOOT draws, gate seed) vs the BASELINE on at most MAX_GATE_EVALS;
  * the first that PASSes is proposed; otherwise no-op (empty list).
Same inputs -> same proposal. Everything is in-sample on visible: the number of
candidates searched is reported so the export can flag multiple comparisons.
(c) GOATWORTHY Post LLC.
"""
import hashlib
import importlib.util
import json
import os
import sys
from itertools import combinations

EVOLVABLE = "policy/candidate.json"
MAX_CANDIDATES = 200
MAX_GATE_EVALS = 10
N_BOOT = 500
SIZE_GRID = (0.5, 0.75)
MIN_EDGE_GRID = (0.02, 0.05, 0.08, 0.10, 0.15)
EPS = 1e-9


def load_lab_gate(root):
    """Import the sandbox's frozen judge/backtest_gate.py under a private module name."""
    judge = os.path.join(root, "judge")
    path = os.path.join(judge, "backtest_gate.py")
    name = "_lab_gate_" + hashlib.sha256(path.encode()).hexdigest()[:12]
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _canon(pol):
    return json.dumps(pol, sort_keys=True, separators=(",", ":"))


def _raw(pol):
    """Plain policy dict with only gate keys, stable list ordering."""
    return {"name": pol.get("name") or "candidate", "description": pol.get("description", ""),
            "min_edge": pol.get("min_edge"),
            "categories_allow": (None if pol.get("categories_allow") is None
                                 else sorted(pol["categories_allow"])),
            "categories_block": sorted(set(pol.get("categories_block") or [])),
            "size_mult": {k: pol["size_mult"][k] for k in sorted(pol.get("size_mult") or {})}}


def moves(current, cats, has_edge):
    """Deterministic, bounded list of (label, policy) hill-climb moves."""
    out, blocked = [], set(current["categories_block"])
    free = [c for c in cats if c not in blocked]
    for c in free:
        out.append(("block %s" % c, dict(current, categories_block=sorted(blocked | {c}))))
    for a, b in combinations(free, 2):
        out.append(("block %s+%s" % (a, b), dict(current, categories_block=sorted(blocked | {a, b}))))
    for c in free:
        for m in SIZE_GRID:
            if current["size_mult"].get(c, 1.0) != m:
                sm = dict(current["size_mult"], **{c: m})
                out.append(("size_mult %s=%s" % (c, m),
                            dict(current, size_mult={k: sm[k] for k in sorted(sm)})))
    if has_edge:
        for e in MIN_EDGE_GRID:
            if current["min_edge"] != e:
                out.append(("min_edge=%s" % e, dict(current, min_edge=e)))
    return out


def propose(root):
    """-> {"proposals": [] | [{file_rel, new_content, fixes}], "search": {...}}."""
    gate = load_lab_gate(root)
    with open(os.path.join(root, "policy", "baseline.json")) as f:
        baseline = json.load(f)
    with open(os.path.join(root, EVOLVABLE)) as f:
        current = _raw(json.load(f))
    recs, _ = gate.load_ledger(os.path.join(root, "data", "visible.jsonl"))
    trades, _ = gate.build_trades(recs)
    min_n = gate.DEFAULTS["min_n"]
    base_n = gate.load_policy(baseline)
    cur_r, _ = gate.replay(trades, gate.load_policy(current))
    cats = sorted({t["category"] for t in trades})
    has_edge = any(t["edge"] is not None for t in trades)
    space = moves(current, cats, has_edge)
    search = {"proposer": "grid", "categories": cats, "has_edge": has_edge,
              "space_size": len(space), "searched": min(len(space), MAX_CANDIDATES),
              "truncated": len(space) > MAX_CANDIDATES, "current_net": cur_r["net"],
              "current_n": cur_r["n_taken"], "min_n": min_n, "n_boot": N_BOOT,
              "in_sample": "visible slice only; holdout never read", "gate_evals": []}
    scored = []
    for label, pol in space[:MAX_CANDIDATES]:
        r, _ = gate.replay(trades, gate.load_policy(pol))
        if r["n_taken"] >= min_n and r["net"] > cur_r["net"] + EPS:
            scored.append((-r["net"], _canon(pol), label, pol, r))
    scored.sort(key=lambda s: (s[0], s[1]))
    search["improving"] = len(scored)
    for _, _, label, pol, r in scored[:MAX_GATE_EVALS]:
        rep = gate.evaluate(trades, base_n, gate.load_policy(pol), n_boot=N_BOOT,
                            seed=gate.DEFAULTS["seed"])
        search["gate_evals"].append({"move": label, "net": r["net"], "n": r["n_taken"],
                                     "verdict": rep["verdict"]})
        if rep["verdict"] != "PASS":
            continue
        tag = hashlib.sha256(_canon(pol).encode()).hexdigest()[:8]
        new = dict(pol, name="grid_%s" % tag,
                   description=("grid_proposer: %s on top of %s; searched %d candidates on the "
                                "visible slice (in-sample); visible net %+.2f vs current %+.2f"
                                % (label, current["name"], search["searched"], r["net"],
                                   cur_r["net"])))
        search["chosen"] = {"move": label, "name": new["name"], "visible_net": r["net"]}
        return {"proposals": [{"file_rel": EVOLVABLE,
                               "new_content": json.dumps(new, indent=2, sort_keys=True) + "\n",
                               "fixes": "valedia-grid-proposer: " + label}],
                "search": search}
    search["chosen"] = None
    search["reason"] = ("no move beats the current candidate on visible with n_taken >= %d "
                        "and a gate PASS vs baseline" % min_n)
    return {"proposals": [], "search": search}
