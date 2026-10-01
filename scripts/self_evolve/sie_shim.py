"""sie_shim.py — run vendors/self-evolve's CLI with the policy lab's IMMUTABLE gate.

Why a shim: self-evolve's IMMUTABLE manifest (tools/sie/immutable.py) is a hard-coded
list of self-evolve's OWN 14 decision files, matched by basename, and only enforced
with --self/--enforce-immutable. A target cannot declare its own immutable files, so
a candidate could rewrite the lab's grader/ledger/baseline and grade itself. We do not
patch vendors/self-evolve (a git subtree); instead this process wraps
tools.sie.statemachine.apply_patch so that, before self-evolve's own gates run, ANY
write other than policy/candidate.json is REJECTed with reason `valedia_lab_immutable`
(the round is then a STATIC_REJECT at PATCH, recorded in events.jsonl).

PROPOSE seam: with the shim-only flag `--valedia-proposer grid` (stripped before
self-evolve parses argv) tools.sie.statemachine.propose is wrapped so the builtin
backend calls grid_proposer.propose (offline, deterministic, no LLM). Before each
proposal the sandbox's candidate.json is reset to the lineage tail (last accepted
snapshot, else the base commit): upstream never commits accepted edits, so a later
REJECT (`git checkout -- .`) or PAUSE would otherwise leave the sandbox off-lineage.
Each search is appended to <run_dir>/valedia_grid_proposals.jsonl for `export`.

Also refuses `--live` (self-evolve's real-LLM switch) and `--self`, including argparse
abbreviations (`--liv`, `--sel=1`), re-checks the parsed namespace (live/self_mode must
be False) and requires run ids / vids to be plain path segments. Usage (cwd must be
vendors/self-evolve, as `python -m tools.sie.cli` would need):
  python3 scripts/self_evolve/sie_shim.py run --target LAB --run-id ID \
      [--valedia-proposer grid|none] ...
(c) GOATWORTHY Post LLC.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SIE_ROOT = os.path.join(REPO, "vendors", "self-evolve")
EVOLVABLE = ("policy/candidate.json",)
REFUSED_FLAGS = ("--live", "--self")
GRID_LOG = "valedia_grid_proposals.jsonl"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def refused_flags(argv):
    """Tokens naming --live/--self, INCLUDING argparse abbreviations (--l, --liv, --sel=1):
    self-evolve's argparse expands any unambiguous prefix, so exact matching is not enough."""
    bad = []
    for a in argv:
        name = str(a).split("=", 1)[0]
        if len(name) >= 3 and name.startswith("--") and any(f.startswith(name)
                                                             for f in REFUSED_FLAGS):
            bad.append(a)
    return bad


def valid_id(s):
    """Run ids / vids become path segments: ^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$, no '..'."""
    return isinstance(s, str) and bool(ID_RE.match(s)) and ".." not in s


class _ParsedGuard:
    """Belt and braces: after self-evolve's own argparse runs, refuse live/self_mode=True
    and any run id / vid that is not a plain path segment (covers abbreviations too)."""

    def __enter__(self):
        self.orig = orig = argparse.ArgumentParser.parse_args

        def parse_args(parser, *a, **k):
            ns = orig(parser, *a, **k)
            if getattr(ns, "live", False) or getattr(ns, "self_mode", False):
                parser.exit(2, "sie_shim: parsed live/self_mode=True; refusing\n")
            for key in ("run_id", "vid"):
                v = getattr(ns, key, None)
                if v is not None and not valid_id(v):
                    parser.exit(2, "sie_shim: invalid --%s %r\n" % (key.replace("_", "-"), v))
            return ns
        argparse.ArgumentParser.parse_args = parse_args
        return self

    def __exit__(self, *exc):
        argparse.ArgumentParser.parse_args = self.orig
        return False


def normalize_rel(file_rel):
    """Canonical lab-relative path, or None if it escapes / is absolute."""
    p = str(file_rel or "").replace("\\", "/")
    if not p or p.startswith("/") or (len(p) > 1 and p[1] == ":"):
        return None
    p = os.path.normpath(p).replace("\\", "/")
    if p == ".." or p.startswith("../"):
        return None
    return p


def lab_patch_gate(file_rel):
    """None if the write is allowed, else a REJECT dict (self-evolve apply_patch shape)."""
    rel = normalize_rel(file_rel)
    if rel in EVOLVABLE:
        return None
    return {"status": "REJECT",
            "reason": "valedia_lab_immutable: %r is not evolvable (only %s)"
                      % (file_rel, ", ".join(EVOLVABLE))}


def install():
    """Wrap self-evolve's PATCH gate in-process. Idempotent."""
    if SIE_ROOT not in sys.path:
        sys.path.insert(0, SIE_ROOT)
    from tools.sie import statemachine as sm
    if getattr(sm.apply_patch, "_valedia_lab_gate", False):
        return sm
    orig = sm.apply_patch

    def gated(sandbox_root, file_rel, new_content, *a, **k):
        rej = lab_patch_gate(file_rel)
        if rej is not None:
            return rej
        return orig(sandbox_root, normalize_rel(file_rel), new_content, *a, **k)

    gated._valedia_lab_gate = True
    sm.apply_patch = gated
    return sm


def _run_dir_of(sandbox_root):
    """<lab>/.sie/worktrees/<rid> -> <lab>/.sie/runs/<rid> (self-evolve's layout)."""
    sb = os.path.abspath(sandbox_root)
    return os.path.join(os.path.dirname(os.path.dirname(sb)), "runs", os.path.basename(sb))


def restore_parent_candidate(sandbox_root):
    """Put the lineage tail's candidate.json back into the sandbox. Returns the vid."""
    rd = _run_dir_of(sandbox_root)
    lin_p = os.path.join(rd, "archive", "lineage.json")
    lin = []
    if os.path.isfile(lin_p):
        with open(lin_p) as f:
            lin = json.load(f)
    if lin:
        vid = lin[-1]["vid"]
        shutil.copyfile(os.path.join(rd, "archive", "versions", vid, "snapshot", EVOLVABLE[0]),
                        os.path.join(sandbox_root, EVOLVABLE[0]))
        return vid
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "checkout", "--", EVOLVABLE[0]],
                   cwd=sandbox_root, check=True, capture_output=True)
    return "base"


def install_grid():
    """Route the builtin PROPOSE backend to grid_proposer. Idempotent."""
    sm = install()
    if getattr(sm.propose, "_valedia_grid", False):
        return sm
    orig = sm.propose
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import grid_proposer

    def grid(sandbox_root, reflections, backend="builtin"):
        if backend != "builtin":
            return orig(sandbox_root, reflections, backend=backend)
        parent = restore_parent_candidate(sandbox_root)
        res = grid_proposer.propose(sandbox_root)
        rec = dict(res["search"], parent=parent)
        try:
            with open(os.path.join(_run_dir_of(sandbox_root), GRID_LOG), "a") as f:
                f.write(json.dumps(rec, sort_keys=True) + "\n")
        except OSError as e:
            sys.stderr.write("sie_shim: could not log grid search: %s\n" % e)
        return res["proposals"]

    grid._valedia_grid = True
    sm.propose = grid
    return sm


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    bad = refused_flags(argv)
    if bad:
        sys.stderr.write("sie_shim: refusing %s (self-evolve --live calls external LLM CLIs; "
                         "--self evolves self-evolve itself). Use valedia_policy_lab.py "
                         "--llm-agents if you really want LLM agents.\n" % bad)
        return 2
    proposer = "none"
    if "--valedia-proposer" in argv:
        i = argv.index("--valedia-proposer")
        proposer = argv[i + 1] if i + 1 < len(argv) else ""
        del argv[i:i + 2]
    if proposer not in ("grid", "none"):
        sys.stderr.write("sie_shim: --valedia-proposer must be grid or none\n")
        return 2
    if proposer == "grid":
        install_grid()
    else:
        install()
    from tools.sie.cli import main as sie_main
    with _ParsedGuard():
        return sie_main(argv)


if __name__ == "__main__":
    sys.exit(main())
