#!/usr/bin/env python3
"""valedia_policy_lab.py — let self-evolve (vendors/self-evolve, MIT) evolve Valedia's
trading POLICY behind the backtest gate, as a proposal generator only.

NOTHING IS AUTO-PROMOTED. No code path here (or in lab_template.py, lab_adjudicate.py,
sie_shim.py) writes Valedia live config, valedia_config.py, crontab, models/, allocator
or category settings. The ledger is read, never written. `export` writes ONE proposal
JSON to the --out path you give (inside the repo only under recommendations/); a human
applies it — after re-running scripts/integrations/backtest_gate.py — or not at all.
Per CLAUDE.md, self-improvement systems may propose/evaluate but never self-promote.

How it maps onto self-evolve (reflect -> propose -> evaluate -> judge -> accept):
  * target  = a "policy lab": a small git repo materialised OUTSIDE the Valedia tree.
    Only policy/candidate.json is evolvable; grader, gate copy, baseline, ledger slices
    and config are IMMUTABLE (sha256 manifest pinned in <lab>/.git/valedia-policy-lab/,
    which no worktree can patch).
  * evaluate = self-evolve's A-tier pytest grader running the lab suite, i.e. Valedia's
    backtest gate on a frozen ledger snapshot. Guards stay green (pass->fail = hard
    REJECT). Adjudication tests are XFAIL at baseline and PASS only when the gate says
    PASS on the visible slice with a strict fee-true improvement AND the time-held-out
    last 20-50% (default 30%) PASSes or is not FAIL with >= 30 candidate trades.
    INSUFFICIENT_DATA never passes: a thin holdout blocks acceptance unless the lab was
    initialised with --allow-thin-holdout (in-sample risk; pinned and reported). One test per
    disjoint chronological fold supplies the >=9 red->green flips self-evolve's
    e-process needs at alpha=0.05; folds only flip for an accepted candidate.
  * judge/accept = self-evolve's deterministic acceptor; `export` then re-adjudicates
    the accepted snapshot out-of-process with Valedia's own backtest_gate.py and
    refuses if any IMMUTABLE file differs or the verdict disagrees.
  * IMMUTABLE at PATCH: self-evolve's manifest only covers its own files, so
    sie_shim.py wraps its apply_patch to refuse every write except candidate.json.

Usage (offline; default self-evolve mode is builtin/serial: no LLM, no network):
  python3 scripts/self_evolve/valedia_policy_lab.py init   --lab ~/.valedia-policy-lab
        [--ledger models/forward_test_ledger.jsonl] [--baseline POLICY.json]
        [--holdout-frac 0.3 (0.2..0.5)] [--n-folds 12] [--holdout-min-n 30] [--n-boot 2000]
        [--allow-thin-holdout]
  python3 scripts/self_evolve/valedia_policy_lab.py evolve --lab DIR [--max-rounds 3] \
        [--proposer grid|none]
  python3 scripts/self_evolve/valedia_policy_lab.py status --lab DIR [--run-id ID]
  python3 scripts/self_evolve/valedia_policy_lab.py export --lab DIR --run-id ID \
        --out recommendations/policy_proposal.json [--vid v1] [--force]

Proposer: self-evolve's builtin proposer only replays a `fix_content` it is handed, so
`--proposer grid` (default) routes builtin PROPOSE to grid_proposer.py: a bounded
(<=200 moves), deterministic, offline hill-climb over categories_block / size_mult /
min_edge scored on the VISIBLE slice with the lab's own gate. It searches in-sample,
so the time-held-out slice is the only out-of-sample check; export records how many
candidates were searched and warns. `--proposer none` keeps the old no-op rounds.
The gate's hard floor clamps any min_n below 30 (holdout included) up to 30.

`--live` NAMING CLASH: self-evolve's `--live` means "real LLM agents", unrelated to
Valedia trading `--live`. This wrapper refuses --live outright and never passes it.
`--llm-agents` instead runs self-evolve with `--proposer llm --reflect-mode parallel`
(what its --live expands to): COSTS TOKENS, needs node plus the `cc`/`claude` and
`codex` CLIs. Caveat: upstream's llm proposer only rewrites non-test .py sources, which
here are the frozen judge, so its proposals are refused by the IMMUTABLE gate until a
JSON-policy proposer is plugged into tools.sie.propose (see export/evolve output).
(c) GOATWORTHY Post LLC.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
INTEGRATIONS = os.path.join(REPO, "scripts", "integrations")
SIE_ROOT = os.path.join(REPO, "vendors", "self-evolve")
SHIM = os.path.join(HERE, "sie_shim.py")
for _p in (HERE, INTEGRATIONS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import lab_adjudicate as adj  # noqa: E402
from sie_shim import refused_flags, valid_id  # noqa: E402

LIVE_REFUSAL = ("refusing --live: in self-evolve, --live means 'call real LLM agents' (external "
                "cc/claude + codex CLIs, costs tokens) — it has nothing to do with Valedia trading "
                "and this wrapper never passes it. Use --llm-agents to opt in to LLM agents.")


def _real(p):
    return os.path.realpath(os.path.abspath(os.path.expanduser(p)))


def _inside(path, root):
    path, root = _real(path), _real(root)
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _err(msg, code=3):
    sys.stderr.write("valedia_policy_lab: %s\n" % msg)
    return code


def _lab_arg(lab):
    lab = _real(lab)
    if _inside(lab, REPO):
        raise ValueError("--lab %s is inside the Valedia tree %s; the lab must live outside it"
                         % (lab, REPO))
    return lab


def _id_arg(lab, value, what="--run-id"):
    """A user-supplied run id / vid: plain segment, and its run dir stays inside the lab."""
    if value is None:
        return None
    if not valid_id(value):
        raise ValueError("%s %r must match ^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$ without '..'"
                         % (what, value))
    if not _inside(os.path.join(lab, ".sie", "runs", value), os.path.join(lab, ".sie", "runs")):
        raise ValueError("%s %r resolves outside the lab" % (what, value))
    return value


def check_lab(lab):
    """(pins, violations) for the lab checkout. Raises if it is not a policy lab."""
    pins = adj.load_pins(lab)
    return pins, adj.tree_violations(lab, pins)


def sie(args, timeout=1800):
    """Run self-evolve's CLI through the IMMUTABLE shim. Never passes --live."""
    if refused_flags(args):
        raise RuntimeError(LIVE_REFUSAL)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, SHIM, *args], cwd=SIE_ROOT, env=env,
                          capture_output=True, text=True, timeout=timeout)


def _last_json(text):
    for line in reversed((text or "").strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return None


def run_dir(lab, run_id):
    return os.path.join(lab, ".sie", "runs", run_id)


def summarize_run(lab, run_id, pins):
    rd = run_dir(lab, run_id)
    counts, static = {}, []
    try:
        with open(os.path.join(rd, "events.jsonl")) as f:
            for line in f:
                ev = json.loads(line)
                counts[ev.get("type")] = counts.get(ev.get("type"), 0) + 1
                if ev.get("type") == "STATIC_REJECT":
                    static.append({"phase": ev.get("phase"),
                                   "rejections": ev.get("rejections", [])[:3]})
    except OSError:
        pass
    lin = _lineage(lab, run_id)
    snaps = {e["vid"]: adj.tree_violations(_snapshot(lab, run_id, e["vid"]), pins) for e in lin}
    return {"run_id": run_id, "run_dir": rd, "event_counts": counts, "static_rejects": static,
            "accepted_versions": [e["vid"] for e in lin],
            "snapshot_integrity": {v: (bad or "intact") for v, bad in snaps.items()}}


def _lineage(lab, run_id):
    p = os.path.join(run_dir(lab, run_id), "archive", "lineage.json")
    if not os.path.isfile(p):
        return []
    with open(p) as f:
        return json.load(f)


def grid_log(lab, run_id):
    """Grid-proposer searches recorded by sie_shim for this run ([] if none)."""
    p = os.path.join(run_dir(lab, run_id), "valedia_grid_proposals.jsonl")
    if not os.path.isfile(p):
        return []
    with open(p) as f:
        return [json.loads(line) for line in f if line.strip()]


def _snapshot(lab, run_id, vid):
    return os.path.join(run_dir(lab, run_id), "archive", "versions", vid, "snapshot")


# ── subcommands ─────────────────────────────────────────────────────────────
def cmd_init(a):
    from lab_template import materialize, DEFAULT_LEDGER, DEFAULT_BASELINE
    lab = _lab_arg(a.lab)
    if os.path.exists(lab) and os.listdir(lab):
        return _err("--lab %s exists and is not empty; refusing to overwrite" % lab)
    pins = materialize(lab, ledger=a.ledger or DEFAULT_LEDGER,
                       baseline=a.baseline or DEFAULT_BASELINE, holdout_frac=a.holdout_frac,
                       n_folds=a.n_folds, holdout_min_n=a.holdout_min_n, n_boot=a.n_boot,
                       allow_thin_holdout=a.allow_thin_holdout)
    with open(os.path.join(lab, "judge", "lab_config.json")) as f:
        cfg = json.load(f)
    print(json.dumps({"lab": lab, "base_commit": pins["base_commit"], "split": cfg["split"],
                      "source_ledger_sha256": pins["source_ledger_sha256"],
                      "evolvable": adj.EVOLVABLE, "immutable_files": len(pins["files"])},
                     indent=2))
    return 0


def cmd_evolve(a):
    if a.live:
        return _err(LIVE_REFUSAL, 2)
    lab = _lab_arg(a.lab)
    pins, bad = check_lab(lab)
    if bad:
        return _err("lab integrity violated, refusing to evolve: %s" % bad, 1)
    r = sie(["init", "--target", lab] + (["--run-id", a.run_id] if a.run_id else []))
    meta = _last_json(r.stdout)
    if r.returncode != 0 or not meta:
        return _err("self-evolve init failed: %s" % (r.stderr or r.stdout)[-800:], 1)
    rid = meta["run_id"]
    mode = (["--proposer", "llm", "--reflect-mode", "parallel"] if a.llm_agents
            else ["--proposer", "builtin", "--reflect-mode", "serial", "--single",
                  "--valedia-proposer", a.proposer])
    if a.llm_agents:
        sys.stderr.write("valedia_policy_lab: --llm-agents: self-evolve will call external LLM "
                         "CLIs (costs tokens). Its llm proposer edits .py sources only; here those "
                         "are IMMUTABLE, so expect PATCH rejections.\n")
    r = sie(["run", "--target", lab, "--run-id", rid, "--base-ref", "HEAD",
             "--max-rounds", str(a.max_rounds), "--mode", "auto"] + mode)
    out = {"harness_exit": r.returncode, "harness_summary": _last_json(r.stdout),
           "mode": "llm-agents" if a.llm_agents else
                   "builtin/serial, proposer=%s (no LLM, no network)" % a.proposer}
    out.update(summarize_run(lab, rid, pins))
    out["grid_searches"] = [{k: s.get(k) for k in ("parent", "searched", "truncated",
                                                     "improving", "chosen", "reason")}
                            for s in grid_log(lab, rid)]
    if r.returncode != 0:
        out["harness_stderr_tail"] = (r.stderr or "")[-1500:]
    if not out["accepted_versions"]:
        out["note"] = ("no candidate accepted" + (
            "; --proposer none never proposes" if a.proposer == "none" and not a.llm_agents
            else "; see grid_searches / static_rejects for why"))
    print(json.dumps(out, indent=2))
    return 0 if r.returncode == 0 else 1


def cmd_status(a):
    lab = _lab_arg(a.lab)
    pins, bad = check_lab(lab)
    runs_root = os.path.join(lab, ".sie", "runs")
    rids = [a.run_id] if a.run_id else sorted(d for d in os.listdir(runs_root) if valid_id(d)) if os.path.isdir(runs_root) else []
    runs = []
    for rid in rids:
        r = sie(["status", "--target", lab, "--run-id", rid], timeout=120)
        s = summarize_run(lab, rid, pins)
        s["sie_status"] = _last_json(r.stdout) if r.returncode == 0 else (r.stderr or "")[-400:]
        runs.append(s)
    print(json.dumps({"lab": lab, "base_commit": pins["base_commit"],
                      "integrity": bad or "intact", "runs": runs}, indent=2))
    return 0 if not bad else 1


def _out_path(out, force):
    p = _real(out)
    if not p.endswith(".json"):
        raise ValueError("--out must be a .json proposal file")
    if _inside(p, REPO) and not _inside(p, os.path.join(REPO, "recommendations")):
        raise ValueError("--out inside the Valedia tree must be under recommendations/ "
                         "(never models/, config or scripts)")
    if os.path.exists(p) and not force:
        raise ValueError("--out %s exists (use --force to overwrite)" % p)
    return p


def cmd_export(a):
    import backtest_gate as valedia_gate               # Valedia's own gate, not the lab copy
    lab = _lab_arg(a.lab)
    out = _out_path(a.out, a.force)
    pins = adj.load_pins(lab)
    lin = _lineage(lab, a.run_id)
    if not lin:
        return _err("run %s accepted no version; nothing to export" % a.run_id, 1)
    entry = next((e for e in lin if e["vid"] == a.vid), None) if a.vid else lin[-1]
    if entry is None:
        return _err("vid %s not in lineage %s" % (a.vid, [e["vid"] for e in lin]), 1)
    snap = _snapshot(lab, a.run_id, entry["vid"])
    bad = adj.tree_violations(snap, pins)
    if bad:
        return _err("snapshot %s failed IMMUTABLE check, refusing to export: %s"
                    % (entry["vid"], bad), 1)
    hold = os.path.join(adj.private_dir(lab), "holdout.jsonl")
    if adj.sha256_file(hold) != pins["holdout_sha256"]:
        return _err("holdout slice hash mismatch; refusing", 1)
    with open(os.path.join(snap, "judge", "lab_config.json")) as f:
        cfg = json.load(f)
    base_p, cand_p = (os.path.join(snap, "policy", n) for n in ("baseline.json", "candidate.json"))
    verdict = adj.adjudicate(valedia_gate, cfg, base_p, cand_p,
                             os.path.join(snap, "data", "visible.jsonl"), hold)
    if not verdict["accepted"]:
        return _err("Valedia's backtest_gate does not confirm %s: %s"
                    % (entry["vid"], verdict["reasons"]), 1)
    full = valedia_gate.run_gate(os.path.join(adj.private_dir(lab), "ledger_snapshot.jsonl"),
                                 base_p, cand_p, **adj._gate_kw(cfg, cfg["gate"]["min_n"]))
    with open(cand_p) as f:
        cand = json.load(f)
    with open(base_p) as f:
        base = json.load(f)
    searches = grid_log(lab, a.run_id)
    n_searched = sum(int(s.get("searched") or 0) for s in searches)
    warning = ("IN-SAMPLE WARNING: visible-slice results (gate, folds, full snapshot) are "
               "in-sample%s; only the holdout verdict (%s) is out-of-sample."
               % (" — the grid proposer searched %d candidates on visible data" % n_searched
                  if searches else "", verdict["holdout"]["verdict"]))
    if cfg.get("allow_thin_holdout"):
        warning += (" THIN HOLDOUT ALLOWED: this lab accepts a holdout that is merely not FAIL "
                    "(holdout candidate n_taken=%d) — treat the result as in-sample."
                    % verdict["holdout"]["candidate"]["n_taken"])
    sys.stderr.write("valedia_policy_lab: %s\n" % warning)
    proposal = {
        "kind": "valedia_policy_proposal",
        "status": "PROPOSAL ONLY - not applied. Nothing in this tool writes live config.",
        "how_to_apply": "human review; re-run scripts/integrations/backtest_gate.py on the "
                        "current ledger (prefer --live-only once live settlements exist).",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lab": lab, "run_id": a.run_id, "vid": entry["vid"], "lineage_entry": entry,
        "candidate_policy": cand, "baseline_policy": base,
        "adjudication": {"accepted": True, "rule": "visible PASS + strict fee-true improvement "
                         "+ holdout PASS (or not FAIL with >= 30 candidate trades; "
                         "re-run with Valedia backtest_gate.py)",
                         "allow_thin_holdout": bool(cfg.get("allow_thin_holdout", False)),
                         "visible_verdict": verdict["visible"]["verdict"],
                         "holdout_verdict": verdict["holdout"]["verdict"],
                         "folds_improved": sum(f["improved"] for f in verdict["folds"]),
                         "n_folds": len(verdict["folds"])},
        "gate_reports": {"visible": verdict["visible"], "holdout": verdict["holdout"],
                         "full_snapshot_in_sample": full},
        "folds": verdict["folds"], "lab_config": cfg,
        "multiple_comparisons_warning": warning,
        "search": {"proposer": "grid" if searches else "external/none",
                   "candidates_searched_total": n_searched, "rounds": searches},
        "integrity": {"snapshot": "intact", "base_commit": pins["base_commit"],
                      "source_ledger_sha256": pins["source_ledger_sha256"]},
    }
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(proposal, f, indent=2, sort_keys=True)
    print(json.dumps({"proposal": out, "vid": entry["vid"],
                      "visible": verdict["visible"]["summary"],
                      "holdout": verdict["holdout"]["summary"]}, indent=2))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="valedia_policy_lab",
                                 description="self-evolve over Valedia trading policy (proposal-only)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="materialise + git-commit a policy lab outside the repo")
    p.add_argument("--lab", required=True)
    p.add_argument("--ledger", default=None, help="ledger to snapshot (read-only)")
    p.add_argument("--baseline", default=None, help="baseline policy JSON")
    p.add_argument("--holdout-frac", type=float, default=0.3)
    p.add_argument("--n-folds", type=int, default=12)
    p.add_argument("--holdout-min-n", type=int, default=30)
    p.add_argument("--n-boot", type=int, default=2000)
    p.add_argument("--allow-thin-holdout", action="store_true",
                   help="accept with a holdout too thin to judge (only 'not FAIL' required): "
                        "IN-SAMPLE RISK, pinned in lab_config and recorded in exports")
    p = sub.add_parser("evolve", help="run self-evolve against the lab (builtin by default)")
    p.add_argument("--lab", required=True)
    p.add_argument("--max-rounds", type=int, default=3)
    p.add_argument("--proposer", choices=("grid", "none"), default="grid",
                   help="builtin-mode proposer: grid (offline search, default) or none (no-op)")
    p.add_argument("--run-id", default=None)
    p.add_argument("--llm-agents", action="store_true",
                   help="opt in to LLM agents (costs tokens; needs node + cc/claude + codex)")
    p.add_argument("--live", action="store_true", help=argparse.SUPPRESS)
    p = sub.add_parser("status", help="lab integrity + run states")
    p.add_argument("--lab", required=True)
    p.add_argument("--run-id", default=None)
    p = sub.add_parser("export", help="write an accepted candidate as a PROPOSAL json")
    p.add_argument("--lab", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--vid", default=None)
    p.add_argument("--force", action="store_true")
    if refused_flags(argv):                          # --live/--self and abbreviations
        return _err(LIVE_REFUSAL, 2)
    a = ap.parse_args(argv)
    lo, hi = adj.HOLDOUT_FRAC_RANGE
    if a.cmd == "init" and (a.n_folds < 8 or not lo <= a.holdout_frac <= hi or a.n_boot < 1):
        # 1 adjudication test + n_folds fold tests must reach the 9 red->green flips the
        # A-tier e-process needs at alpha=0.05, else nothing could ever be accepted.
        return _err("--n-folds must be >= 8, --n-boot >= 1, --holdout-frac in [%s, %s]"
                    % (lo, hi))
    try:
        if getattr(a, "run_id", None) is not None:
            _id_arg(_lab_arg(a.lab), a.run_id)
        if getattr(a, "vid", None) is not None:
            _id_arg(_lab_arg(a.lab), a.vid, "--vid")
        return {"init": cmd_init, "evolve": cmd_evolve, "status": cmd_status,
                "export": cmd_export}[a.cmd](a)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
        return _err("%s: %s" % (type(e).__name__, e))


if __name__ == "__main__":
    sys.exit(main())
