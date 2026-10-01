"""sie_shim.py — run vendors/self-evolve's CLI with the policy lab's IMMUTABLE gate.

Why a shim: self-evolve's IMMUTABLE manifest (tools/sie/immutable.py) is a hard-coded
list of self-evolve's OWN 14 decision files, matched by basename, and only enforced
with --self/--enforce-immutable. A target cannot declare its own immutable files, so
a candidate could rewrite the lab's grader/ledger/baseline and grade itself. We do not
patch vendors/self-evolve (a git subtree); instead this process wraps
tools.sie.statemachine.apply_patch so that, before self-evolve's own gates run, ANY
write other than policy/candidate.json is REJECTed with reason `valedia_lab_immutable`
(the round is then a STATIC_REJECT at PATCH, recorded in events.jsonl).

Also refuses `--live` (self-evolve's real-LLM switch) and `--self`. Usage (cwd must be
vendors/self-evolve, as `python -m tools.sie.cli` would need):
  python3 scripts/self_evolve/sie_shim.py run --target LAB --run-id ID ...
(c) GOATWORTHY Post LLC.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SIE_ROOT = os.path.join(REPO, "vendors", "self-evolve")
EVOLVABLE = ("policy/candidate.json",)
REFUSED_FLAGS = ("--live", "--self")


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


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    bad = [a for a in argv if a.split("=", 1)[0] in REFUSED_FLAGS]
    if bad:
        sys.stderr.write("sie_shim: refusing %s (self-evolve --live calls external LLM CLIs; "
                         "--self evolves self-evolve itself). Use valedia_policy_lab.py "
                         "--llm-agents if you really want LLM agents.\n" % bad)
        return 2
    install()
    from tools.sie.cli import main as sie_main
    return sie_main(argv)


if __name__ == "__main__":
    sys.exit(main())
