#!/usr/bin/env python3
"""Checks for the self-evolve policy lab. Run: python3 test_valedia_policy_lab.py
Synthetic ledgers in a tempdir only — no network, no LLM, never passes --live to
self-evolve, never writes models/ or any Valedia config. Needs pytest + git.
Covers: init layout/git/pins, holdout outside the tree, lab suite PASS for a better
candidate / FAIL for worse / not-PASS for INSUFFICIENT, IMMUTABLE edits detected and
refused at PATCH, --live refused, builtin evolve round end-to-end (STATIC_REJECT),
full self-evolve ACCEPT of an injected better candidate + REJECT of a worse one,
export proposal + re-adjudication + tamper refusal + out-path guard."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "scripts", "integrations"))
import lab_adjudicate as adj  # noqa: E402
import sie_shim  # noqa: E402
import valedia_policy_lab as V  # noqa: E402
from forward_test_ledger import kalshi_fee  # noqa: E402

TMP = tempfile.mkdtemp(prefix="policy_lab_test_")
CLI = [sys.executable, os.path.join(HERE, "valedia_policy_lab.py")]
BASE = {"name": "take_all", "min_edge": None, "categories_allow": None,
        "categories_block": [], "size_mult": {}}
BETTER = dict(BASE, name="block_bad", categories_block=["BAD"])
WORSE = dict(BASE, name="block_good", categories_block=["GOOD"])
THIN = dict(BASE, name="rare_only", categories_allow=["RARE"])


def _ledger(path, n=150):
    """GOOD wins 7/10, BAD always loses, a few RARE; recorded (net) fees, live source."""
    rows = []
    for i in range(n):
        cat = "RARE" if i % 37 == 5 else ("BAD" if i % 3 == 0 else "GOOD")
        won = cat != "BAD" and i % 10 not in (1, 4, 7)
        eid, ts = "e%03d" % i, "2026-01-%02dT%02d:00:00Z" % (1 + i // 24, i % 24)
        fee = kalshi_fee(0.5, 10)
        rows.append({"type": "entry", "id": eid, "ts": ts, "strategy": cat, "venue": "kalshi",
                     "side": "yes", "entry_price": 0.5, "contracts": 10, "entry_fee": fee,
                     "source": "live"})
        rows.append({"type": "settlement", "entry_id": eid, "strategy": cat, "won": won,
                     "result": "yes" if won else "no", "filled_contracts": 10, "fee": fee,
                     "realized_pnl_net": round((5.0 if won else -5.0) - fee, 6),
                     "source": "live"})
    with open(path, "w") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows))
    return path


def _baseline_file():
    p = os.path.join(TMP, "baseline.json")
    with open(p, "w") as f:
        json.dump(BASE, f)
    return p


def _init(name, n=150):
    lab = os.path.join(TMP, name)
    r = subprocess.run(CLI + ["init", "--lab", lab, "--ledger", _ledger(lab + ".jsonl", n),
                              "--baseline", _baseline_file(), "--n-boot", "400"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return lab


def _set_candidate(lab, pol):
    with open(os.path.join(lab, "policy", "candidate.json"), "w") as f:
        json.dump(pol, f)


def _pytest(root):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-m", "pytest", "-v", "--tb=no", "--no-header"],
                       cwd=root, capture_output=True, text=True, env=env)
    out = dict(re.findall(r"^tests/test_policy_gate\.py::(\S+)\s+(PASSED|FAILED|ERROR|XFAIL|XPASS)",
                          r.stdout, re.M))
    return r.returncode, out


def _git(lab, *a):
    return subprocess.run(["git", *a], cwd=lab, capture_output=True, text=True).stdout.strip()


def _repo_state():
    """Valedia worktree status + hashes of the protected paths, to prove nothing was written."""
    st = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=REPO,
                        capture_output=True, text=True).stdout
    led = os.path.join(REPO, "models", "forward_test_ledger.jsonl")
    return st, adj.sha256_file(led) if os.path.exists(led) else None


def check_init(lab):
    for rel in ("policy/candidate.json", "policy/baseline.json", "data/visible.jsonl",
                "judge/backtest_gate.py", "judge/forward_test_ledger.py", "judge/lab_adjudicate.py",
                "judge/lab_config.json", "judge/IMMUTABLE.json", "tests/conftest.py",
                "tests/test_policy_gate.py", "pytest.ini", ".gitignore", "LAB.md"):
        assert os.path.isfile(os.path.join(lab, rel)), rel
    assert os.path.isdir(os.path.join(lab, ".git"))
    assert _git(lab, "status", "--porcelain") == "", "lab not committed clean"
    assert len(_git(lab, "log", "--oneline").splitlines()) == 1
    pins, bad = V.check_lab(lab)
    assert bad == [] and pins["base_commit"] == _git(lab, "rev-parse", "HEAD")
    priv = adj.private_dir(lab)
    assert priv.startswith(os.path.join(lab, ".git")), priv          # outside the tree
    assert not os.path.exists(os.path.join(lab, "data", "holdout.jsonl"))
    with open(os.path.join(lab, "judge", "lab_config.json")) as f:
        cfg = json.load(f)
    assert cfg["split"] == {"visible_rows": 210, "holdout_rows": 90}, cfg["split"]  # last 30%
    with open(os.path.join(priv, "holdout.jsonl")) as f:
        hold_ids = {json.loads(l).get("id") for l in f} - {None}
    assert hold_ids == {"e%03d" % i for i in range(105, 150)}, "holdout is not the last 30% by time"
    with open(os.path.join(lab, "judge", "IMMUTABLE.json")) as f:
        man = json.load(f)
    assert man["evolvable"] == ["policy/candidate.json"]
    assert "policy/candidate.json" not in man["immutable"] and "tests/test_policy_gate.py" in man["immutable"]
    r = subprocess.run(CLI + ["init", "--lab", os.path.join(REPO, "scripts", "self_evolve", "_lab")],
                       capture_output=True, text=True)
    assert r.returncode == 3 and "outside" in r.stderr
    assert not os.path.exists(os.path.join(REPO, "scripts", "self_evolve", "_lab"))


def check_suite_verdicts(lab):
    code, out = _pytest(lab)                                         # baseline: green + headroom
    assert code == 0 and out["test_gate_adjudication"] == "XFAIL", out
    assert all(out[k] == "PASSED" for k in out if k.startswith("test_") and "fold" not in k
               and k != "test_gate_adjudication"), out
    _set_candidate(lab, BETTER)
    code, out = _pytest(lab)
    assert code == 0 and out["test_gate_adjudication"] == "PASSED", out
    assert sum(v == "PASSED" for k, v in out.items() if "fold" in k) >= 9, out
    _set_candidate(lab, WORSE)
    code, out = _pytest(lab)
    assert code == 1 and out["test_visible_gate_not_fail"] == "FAILED", out
    assert out["test_gate_adjudication"] == "XFAIL", out
    _set_candidate(lab, THIN)
    code, out = _pytest(lab)
    assert out["test_gate_adjudication"] == "XFAIL", out           # INSUFFICIENT never PASS
    assert not any(v == "PASSED" for k, v in out.items() if "fold" in k), out
    import backtest_gate as G
    v = adj.adjudicate(G, json.load(open(os.path.join(lab, "judge", "lab_config.json"))),
                       os.path.join(lab, "policy", "baseline.json"),
                       os.path.join(lab, "policy", "candidate.json"),
                       os.path.join(lab, "data", "visible.jsonl"),
                       os.path.join(adj.private_dir(lab), "holdout.jsonl"))
    assert v["visible"]["verdict"] == "INSUFFICIENT_DATA" and not v["accepted"], v["reasons"]
    _git(lab, "checkout", "--", ".")


def check_immutable(lab):
    cfgp = os.path.join(lab, "judge", "lab_config.json")
    orig = open(cfgp).read()
    with open(cfgp, "w") as f:                                       # weaken own judge
        f.write(orig.replace('"min_n": 30', '"min_n": 1'))
    code, out = _pytest(lab)
    assert code == 1 and out["test_judge_integrity"] == "FAILED", out
    man = os.path.join(lab, "judge", "IMMUTABLE.json")               # ...and forge the manifest
    m = json.load(open(man))
    m["immutable"]["judge/lab_config.json"] = adj.sha256_file(cfgp)
    with open(man, "w") as f:
        json.dump(m, f)
    code, out = _pytest(lab)
    assert code == 1 and out["test_judge_integrity"] == "FAILED", "forged manifest not caught"
    with open(os.path.join(lab, "conftest.py"), "w") as f:           # extra rootdir file
        f.write("# injected\n")
    assert any("unexpected file: conftest.py" in b for b in V.check_lab(lab)[1])
    os.remove(os.path.join(lab, "conftest.py"))
    _git(lab, "checkout", "--", ".")
    assert V.check_lab(lab)[1] == []
    for rel in ("tests/test_policy_gate.py", "judge/lab_config.json", "policy/baseline.json",
                "data/visible.jsonl", "./policy/../tests/conftest.py", "../x/policy/candidate.json",
                "/abs/policy/candidate.json", "policy\\..\\pytest.ini"):
        rej = sie_shim.lab_patch_gate(rel)
        assert rej and rej["status"] == "REJECT" and "valedia_lab_immutable" in rej["reason"], rel
    assert sie_shim.lab_patch_gate("policy/candidate.json") is None
    assert sie_shim.lab_patch_gate("./policy/candidate.json") is None


def check_live_refused(lab):
    for argv in (["evolve", "--lab", lab, "--live"], ["evolve", "--lab", lab, "--live=1"],
                 ["status", "--lab", lab, "--live"]):
        r = subprocess.run(CLI + argv, capture_output=True, text=True)
        assert r.returncode == 2 and "LLM" in r.stderr, (argv, r.stderr)
    assert not os.path.exists(os.path.join(lab, ".sie")), "--live run started something"
    assert sie_shim.main(["run", "--target", lab, "--run-id", "x", "--live"]) == 2
    try:
        V.sie(["run", "--live"])
        assert False, "sie() passed --live"
    except RuntimeError as e:
        assert "LLM" in str(e)


def check_evolve_builtin(lab):
    r = subprocess.run(CLI + ["evolve", "--lab", lab, "--max-rounds", "1"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    s = json.loads(r.stdout)
    assert s["harness_exit"] == 0 and s["accepted_versions"] == [], s
    assert s["event_counts"].get("PROFILE") == 1 and s["static_rejects"][0]["phase"] == "PROPOSE", s
    with open(os.path.join(s["run_dir"], "target.json")) as f:
        assert json.load(f)["tier"] == "A"                           # lab suite = A-tier grader
    r = subprocess.run(CLI + ["status", "--lab", lab], capture_output=True, text=True)
    assert r.returncode == 0 and json.loads(r.stdout)["integrity"] == "intact", r.stdout


def _loop(lab, rid, file_rel, content):
    sm = sie_shim.install()
    return sm.run_loop(lab, "HEAD", rid, max_rounds=1, proposer="builtin", reflect_mode="serial",
                       dual=False, _injected_fix={"file_rel": file_rel, "fix_content": content,
                                                  "target_failure": "test proposal"})


def check_accept_and_export(lab):
    res = _loop(lab, "better", "policy/candidate.json", json.dumps(BETTER))
    assert res["accepted_versions"] == ["v1"], res
    res = _loop(lab, "worse", "policy/candidate.json", json.dumps(WORSE))
    assert res["accepted_versions"] == [], res
    ev = [json.loads(l) for l in open(os.path.join(res["run_dir"], "events.jsonl"))]
    assert any(e["type"] == "REJECT" for e in ev), ev
    res = _loop(lab, "tamper", "tests/test_policy_gate.py", "def test_x():\n    pass\n")
    ev = [json.loads(l) for l in open(os.path.join(res["run_dir"], "events.jsonl"))]
    pr = [e for e in ev if e["type"] == "STATIC_REJECT" and e.get("phase") == "PATCH"]
    assert res["accepted_versions"] == [] and pr, ev
    assert "valedia_lab_immutable" in pr[0]["rejections"][0]["reason"]
    assert V.check_lab(lab)[1] == []

    out = os.path.join(TMP, "proposals", "proposal.json")
    r = subprocess.run(CLI + ["export", "--lab", lab, "--run-id", "better", "--out", out],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    p = json.load(open(out))
    assert p["kind"] == "valedia_policy_proposal" and "PROPOSAL ONLY" in p["status"]
    assert p["candidate_policy"]["categories_block"] == ["BAD"] and p["vid"] == "v1"
    assert p["adjudication"]["visible_verdict"] == "PASS"
    assert p["adjudication"]["holdout_verdict"] != "FAIL" and p["adjudication"]["folds_improved"] >= 9
    assert p["gate_reports"]["visible"]["tool"] == "backtest_gate"
    r = subprocess.run(CLI + ["export", "--lab", lab, "--run-id", "worse", "--out", out + "2.json"],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "no version" in r.stderr and not os.path.exists(out + "2.json")
    for bad_out in (os.path.join(REPO, "models", "p.json"), os.path.join(REPO, "valedia_config.json"),
                    os.path.join(TMP, "p.txt"), out):
        r = subprocess.run(CLI + ["export", "--lab", lab, "--run-id", "better", "--out", bad_out],
                           capture_output=True, text=True)
        assert r.returncode == 3, (bad_out, r.stderr)
    assert not os.path.exists(os.path.join(REPO, "models", "p.json"))
    snap = V._snapshot(lab, "better", "v1")
    with open(os.path.join(snap, "judge", "lab_config.json"), "a") as f:
        f.write(" ")
    r = subprocess.run(CLI + ["export", "--lab", lab, "--run-id", "better", "--out", out, "--force"],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "IMMUTABLE" in r.stderr, r.stderr


def run():
    before = _repo_state()
    lab = _init("lab_a")
    check_init(lab)
    check_suite_verdicts(lab)
    check_immutable(lab)
    check_live_refused(_init("lab_live"))
    check_evolve_builtin(_init("lab_evolve"))
    check_accept_and_export(_init("lab_accept"))
    assert _repo_state() == before, "Valedia tree or ledger changed during the test"
    shutil.rmtree(TMP, ignore_errors=True)
    print("test_valedia_policy_lab: all checks passed")


if __name__ == "__main__":
    run()
