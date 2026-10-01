"""lab_template.py — materialise a Valedia policy lab (a self-evolve target repo).

Called by valedia_policy_lab.py `init`. Reads the Valedia ledger / baseline policy /
backtest_gate source READ-ONLY and writes only inside the lab directory (which the
caller has already checked lies OUTSIDE the Valedia tree). No network, no keys.

Lab layout (only policy/candidate.json is evolvable; everything else is IMMUTABLE):
  policy/candidate.json        evolvable policy (starts as a copy of the baseline)
  policy/baseline.json         frozen baseline policy
  data/visible.jsonl           frozen visible slice of the ledger snapshot
  judge/backtest_gate.py       verbatim copy of scripts/integrations/backtest_gate.py
  judge/forward_test_ledger.py verbatim extract of the 4 helpers backtest_gate imports
  judge/lab_adjudicate.py      verbatim copy of scripts/self_evolve/lab_adjudicate.py
  judge/lab_config.json        frozen thresholds / split / hashes
  judge/IMMUTABLE.json         manifest {path: sha256} of the files above
  tests/conftest.py, tests/test_policy_gate.py, pytest.ini, .gitignore, LAB.md
Outside every worktree, in <lab>/.git/valedia-policy-lab/:
  holdout.jsonl (holdout slice), ledger_snapshot.jsonl (full copy), pins.json
(c) GOATWORTHY Post LLC.
"""
import ast
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
INTEGRATIONS = os.path.join(REPO, "scripts", "integrations")
for _p in (HERE, INTEGRATIONS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import lab_adjudicate as adj  # noqa: E402
DEFAULT_LEDGER = os.path.join(REPO, "models", "forward_test_ledger.jsonl")
DEFAULT_BASELINE = os.path.join(INTEGRATIONS, "backtest_policies", "baseline_take_all.json")
FTL_HELPERS = ("kalshi_fee", "CATEGORY_ALIASES", "canonical_category", "_is_non_real_source")
GATE_DEFAULTS = {"min_n": 30, "max_p_worse": 0.35, "max_dd_worse": 0.20, "n_boot": 2000, "seed": 7}
GIT_ID = ["-c", "user.name=valedia-policy-lab", "-c", "user.email=policy-lab@localhost",
          "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]

PYTEST_INI = """[pytest]
testpaths = tests
addopts = -p no:cacheprovider
"""
GITIGNORE = ".sie/\n__pycache__/\n.pytest_cache/\n"
LAB_MD = """# Valedia policy lab (self-evolve target)

Evolve ONLY `policy/candidate.json` (Valedia backtest-gate policy schema:
`{"name","description","min_edge","categories_allow","categories_block","size_mult"}`).
Every other file is IMMUTABLE (judge/IMMUTABLE.json, pinned outside the tree):
edits are refused at the PATCH gate and fail the integrity test.

Grader = `pytest` (self-evolve A-tier). Guards must stay green; a candidate is
accepted only when backtest_gate says PASS on the visible ledger slice with a strict
fee-true improvement and the held-out last slice PASSes too (a holdout too thin
to judge blocks acceptance unless lab_config.allow_thin_holdout). Fold tests turn from XFAIL
to PASSED only for such a candidate. Output is a PROPOSAL; nothing is auto-promoted.
"""

CONFTEST = '''"""Frozen grader wiring for the Valedia policy lab. IMMUTABLE."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JUDGE = os.path.join(ROOT, "judge")
sys.path.insert(0, JUDGE)
import backtest_gate as gate  # noqa: E402  frozen copy
import lab_adjudicate as adj  # noqa: E402  frozen copy

with open(os.path.join(JUDGE, "lab_config.json")) as _f:
    CFG = json.load(_f)


@pytest.fixture(scope="session")
def lab():
    pins = adj.load_pins(ROOT)                 # fail-closed: no pins -> every test errors
    priv = adj.private_dir(ROOT)
    return {"root": ROOT, "pins": pins, "cfg": CFG, "gate": gate, "adj": adj,
            "baseline": os.path.join(ROOT, "policy", "baseline.json"),
            "candidate": os.path.join(ROOT, "policy", "candidate.json"),
            "visible": os.path.join(ROOT, "data", "visible.jsonl"),
            "holdout": os.path.join(priv, "holdout.jsonl")}


@pytest.fixture(scope="session")
def verdict(lab):
    return adj.adjudicate(gate, CFG, lab["baseline"], lab["candidate"],
                          lab["visible"], lab["holdout"])
'''

TESTS = '''"""Valedia policy lab grader (self-evolve A-tier). IMMUTABLE.

Guards (green at baseline; pass->fail is self-evolve's no-regression hard REJECT):
  schema, judge integrity, pinned ledger slices, visible not FAIL, holdout not FAIL.
Adjudication (XFAIL at baseline = headroom; PASSED only for an accepted candidate):
  test_gate_adjudication + one test per disjoint chronological fold of visible.
"""
import os

import pytest
from conftest import CFG, JUDGE


def test_candidate_policy_schema(lab):
    lab["gate"].load_policy(lab["candidate"])


def test_judge_integrity(lab):
    assert os.path.dirname(os.path.abspath(lab["gate"].__file__)) == JUDGE
    assert os.path.dirname(os.path.abspath(lab["adj"].__file__)) == JUDGE
    pins, adj = lab["pins"], lab["adj"]
    assert adj.sha256_file(os.path.join(JUDGE, "IMMUTABLE.json")) == pins["manifest_sha256"]
    assert adj.tree_violations(lab["root"], pins) == []


def test_ledger_slices_pinned(lab):
    adj, pins = lab["adj"], lab["pins"]
    assert adj.sha256_file(lab["visible"]) == pins["visible_sha256"] == CFG["visible_sha256"]
    assert adj.sha256_file(lab["holdout"]) == pins["holdout_sha256"] == CFG["holdout_sha256"]


def test_visible_gate_not_fail(verdict):
    assert verdict["visible"]["verdict"] != "FAIL", verdict["visible"]["summary"]


def test_holdout_gate_not_fail(verdict):
    assert verdict["holdout"]["verdict"] != "FAIL", verdict["holdout"]["summary"]


def test_gate_adjudication(verdict):
    if not verdict["accepted"]:
        pytest.xfail("; ".join(verdict["reasons"]))


@pytest.mark.parametrize("fold", range(CFG["n_folds"]))
def test_fold_improves(verdict, fold):
    f = verdict["folds"][fold]
    if not (verdict["accepted"] and f["improved"]):
        pytest.xfail("fold %d: accepted=%s improved=%s" % (fold, verdict["accepted"], f["improved"]))
'''


def _ftl_extract(path):
    """Verbatim source of the helpers backtest_gate imports, nothing else."""
    with open(path) as f:
        src = f.read()
    parts = {}
    for node in ast.parse(src).body:
        name = getattr(node, "name", None)
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
        if name in FTL_HELPERS:
            parts[name] = ast.get_source_segment(src, node)
    missing = [n for n in FTL_HELPERS if n not in parts]
    if missing:
        raise ValueError("forward_test_ledger.py lacks %s" % missing)
    body = "\n\n\n".join(parts[n] for n in FTL_HELPERS)
    return ('"""Frozen extract of scripts/integrations/forward_test_ledger.py (helpers only). '
            'IMMUTABLE."""\nimport math\n\n\n' + body + "\n")


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _git(lab, *args):
    return subprocess.run(["git", *GIT_ID, *args], cwd=lab, check=True,
                          capture_output=True, text=True).stdout.strip()


def materialize(lab, ledger=DEFAULT_LEDGER, baseline=DEFAULT_BASELINE, holdout_frac=0.3,
                n_folds=12, holdout_min_n=30, n_boot=GATE_DEFAULTS["n_boot"],
                allow_thin_holdout=False):
    """Create + git-commit the lab. Returns the pins dict. Caller validates `lab`."""
    lab = os.path.abspath(lab)
    with open(baseline) as f:
        base_obj = json.load(f)
    import backtest_gate as valedia_gate                 # Valedia's gate validates the baseline
    valedia_gate.load_policy(base_obj)
    lines = adj._ledger_lines(ledger)
    vis, hold = adj.split_ledger(lines, holdout_frac)
    os.makedirs(lab, exist_ok=True)
    cand = dict(base_obj, name="candidate_v0",
                description="Evolvable copy of %s. Only this file may change." % base_obj["name"])
    _write(os.path.join(lab, "policy", "candidate.json"), json.dumps(cand, indent=2) + "\n")
    _write(os.path.join(lab, "policy", "baseline.json"), json.dumps(base_obj, indent=2) + "\n")
    _write(os.path.join(lab, "data", "visible.jsonl"), "".join(vis))
    os.makedirs(os.path.join(lab, "judge"), exist_ok=True)
    shutil.copyfile(os.path.join(INTEGRATIONS, "backtest_gate.py"),
                    os.path.join(lab, "judge", "backtest_gate.py"))
    _write(os.path.join(lab, "judge", "forward_test_ledger.py"),
           _ftl_extract(os.path.join(INTEGRATIONS, "forward_test_ledger.py")))
    shutil.copyfile(os.path.join(HERE, "lab_adjudicate.py"),
                    os.path.join(lab, "judge", "lab_adjudicate.py"))
    _write(os.path.join(lab, "tests", "conftest.py"), CONFTEST)
    _write(os.path.join(lab, "tests", "test_policy_gate.py"), TESTS)
    _write(os.path.join(lab, "pytest.ini"), PYTEST_INI)
    _write(os.path.join(lab, ".gitignore"), GITIGNORE)
    _write(os.path.join(lab, "LAB.md"), LAB_MD)
    vis_sha = adj.sha256_file(os.path.join(lab, "data", "visible.jsonl"))
    hold_sha = adj.hashlib.sha256("".join(hold).encode()).hexdigest()
    cfg = {"gate": dict(GATE_DEFAULTS, n_boot=int(n_boot), live_only=False),
           "holdout_min_n": int(holdout_min_n), "n_folds": int(n_folds),
           "holdout_frac": float(holdout_frac),
           "allow_thin_holdout": bool(allow_thin_holdout),
           "split": {"visible_rows": len(vis), "holdout_rows": len(hold)},
           "source_ledger_sha256": adj.sha256_file(ledger),
           "visible_sha256": vis_sha, "holdout_sha256": hold_sha}
    _write(os.path.join(lab, "judge", "lab_config.json"), json.dumps(cfg, indent=2) + "\n")
    immutable = sorted(
        os.path.relpath(os.path.join(d, fn), lab).replace(os.sep, "/")
        for d, dn, fns in os.walk(lab) for fn in fns
        if not os.path.relpath(os.path.join(d, fn), lab).startswith(".git" + os.sep))
    immutable = [p for p in immutable if p != adj.EVOLVABLE]
    manifest = {p: adj.sha256_file(os.path.join(lab, p)) for p in immutable}
    _write(os.path.join(lab, "judge", "IMMUTABLE.json"), json.dumps(
        {"evolvable": [adj.EVOLVABLE], "immutable": manifest}, indent=2, sort_keys=True) + "\n")
    files = dict(manifest)
    files["judge/IMMUTABLE.json"] = adj.sha256_file(os.path.join(lab, "judge", "IMMUTABLE.json"))
    _git(lab, "-c", "init.defaultBranch=main", "init", "-q")
    _git(lab, "add", "-A")
    _git(lab, "commit", "-q", "-m", "valedia policy lab: frozen judge + baseline candidate")
    priv = adj.private_dir(lab)
    os.makedirs(priv, exist_ok=True)
    _write(os.path.join(priv, "holdout.jsonl"), "".join(hold))
    shutil.copyfile(ledger, os.path.join(priv, "ledger_snapshot.jsonl"))
    pins = {"files": files, "manifest_sha256": files["judge/IMMUTABLE.json"],
            "visible_sha256": vis_sha, "holdout_sha256": hold_sha,
            "source_ledger_sha256": cfg["source_ledger_sha256"],
            "source_ledger_path": os.path.abspath(ledger),
            "base_commit": _git(lab, "rev-parse", "HEAD"),
            "created_at": datetime.now(timezone.utc).isoformat()}
    _write(os.path.join(priv, "pins.json"), json.dumps(pins, indent=2, sort_keys=True) + "\n")
    return pins
