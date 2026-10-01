"""lab_adjudicate.py — the policy lab's adjudication rule (stdlib only).

ONE source of truth, used twice:
  * copied verbatim into every policy lab as judge/lab_adjudicate.py (IMMUTABLE,
    sha256-pinned) where the lab's pytest suite calls it with the lab's frozen
    copy of backtest_gate — that suite is self-evolve's A-tier grader;
  * imported by scripts/self_evolve/valedia_policy_lab.py `export`, which calls it
    with Valedia's own scripts/integrations/backtest_gate.py to re-adjudicate an
    accepted candidate out-of-process before writing any proposal.

The `gate` argument is a backtest_gate module (dependency injection), so this file
never imports Valedia code and never touches the network, keys or live config.

Rule: ACCEPT iff visible verdict == PASS and candidate fee-true net strictly beats
baseline on visible and holdout verdict != FAIL. INSUFFICIENT_DATA on visible is
never a pass. Fold results (disjoint chronological slices of visible) are
evidence for self-evolve's e-process; they only count when the rule above holds.
(c) GOATWORTHY Post LLC.
"""
import hashlib
import json
import os

EVOLVABLE = "policy/candidate.json"
PRIVATE_SUBDIR = "valedia-policy-lab"        # lives in <lab>/.git/, outside every worktree
IGNORED_DIRS = {".git", ".sie", "__pycache__", ".pytest_cache"}
EPS = 1e-9


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def git_common_dir(root):
    """<lab>/.git for the lab checkout AND for any `git worktree` of it. Pure file reads."""
    dot = os.path.join(root, ".git")
    if os.path.isdir(dot):
        return os.path.abspath(dot)
    with open(dot) as f:
        line = f.read().strip()
    if not line.startswith("gitdir:"):
        raise ValueError("unrecognised .git file in %s" % root)
    gd = line.split(":", 1)[1].strip()
    if not os.path.isabs(gd):
        gd = os.path.join(root, gd)
    cf = os.path.join(gd, "commondir")
    if os.path.isfile(cf):
        with open(cf) as f:
            cd = f.read().strip()
        gd = cd if os.path.isabs(cd) else os.path.join(gd, cd)
    return os.path.normpath(os.path.abspath(gd))


def private_dir(root):
    return os.path.join(git_common_dir(root), PRIVATE_SUBDIR)


def load_pins(root):
    with open(os.path.join(private_dir(root), "pins.json")) as f:
        return json.load(f)


def tree_violations(root, pins):
    """Every way the tree at `root` differs from the pinned judge. [] == intact.
    Only policy/candidate.json may differ; nothing may be added or removed."""
    bad = []
    files = pins["files"]
    seen = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")
            if rel == ".git":                      # worktree pointer file
                continue
            seen.add(rel)
            if rel == EVOLVABLE:
                continue
            if rel not in files:
                bad.append("unexpected file: %s" % rel)
            elif sha256_file(os.path.join(root, rel)) != files[rel]:
                bad.append("IMMUTABLE file modified: %s" % rel)
    for rel in sorted(set(files) - seen):
        bad.append("IMMUTABLE file missing: %s" % rel)
    if EVOLVABLE not in seen:
        bad.append("evolvable file missing: %s" % EVOLVABLE)
    return bad


def _ledger_lines(path):
    with open(path) as f:
        return [ln if ln.endswith("\n") else ln + "\n" for ln in f if ln.strip()]


def split_ledger(lines, holdout_frac):
    """Deterministic time split of raw ledger lines -> (visible_lines, holdout_lines).
    Rows are grouped per trade (entry + its settlements) so a trade never straddles
    the split; groups are ordered like backtest_gate.build_trades (entry ts, else
    settlement logged_at, then ledger order) and the LAST holdout_frac go to holdout."""
    if not 0.0 <= holdout_frac < 1.0:
        raise ValueError("holdout_frac must be in [0, 1)")
    groups, order = {}, []
    for i, ln in enumerate(lines):
        try:
            r = json.loads(ln)
        except ValueError:
            r = None
        key, ts = ("_row", i), ""
        if isinstance(r, dict):
            if r.get("type") == "entry" and isinstance(r.get("id"), str):
                key, ts = ("id", r["id"]), str(r.get("ts") or "")
            elif r.get("type") == "settlement" and isinstance(r.get("entry_id"), str):
                key, ts = ("id", r["entry_id"]), str(r.get("logged_at") or "")
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"ts": ts, "first": i, "lines": [], "has_entry": False}
            order.append(key)
        if isinstance(r, dict) and r.get("type") == "entry" and not g["has_entry"]:
            g["ts"], g["has_entry"] = str(r.get("ts") or ""), True
        g["lines"].append((i, ln))
    ranked = sorted(order, key=lambda k: (groups[k]["ts"], groups[k]["first"]))
    n_hold = int(round(len(ranked) * holdout_frac))
    hold_keys = set(ranked[len(ranked) - n_hold:]) if n_hold else set()
    vis, hold = [], []
    for k in order:                                # keep original ledger order inside each slice
        (hold if k in hold_keys else vis).extend(g for g in groups[k]["lines"])
    return [ln for _, ln in sorted(vis)], [ln for _, ln in sorted(hold)]


def _gate_kw(cfg, min_n):
    g = cfg["gate"]
    return {"min_n": min_n, "max_p_worse": g["max_p_worse"], "max_dd_worse": g["max_dd_worse"],
            "n_boot": g["n_boot"], "seed": g["seed"], "live_only": bool(g.get("live_only", False))}


def fold_results(gate, ledger_path, baseline, candidate, n_folds, live_only=False):
    """Fee-true replay per disjoint chronological fold of the (visible) ledger."""
    recs, _ = gate.load_ledger(ledger_path)
    trades, _ = gate.build_trades(recs, live_only=live_only)
    _, bc = gate.replay(trades, gate.load_policy(baseline))
    _, cc = gate.replay(trades, gate.load_policy(candidate))
    n, out = len(trades), []
    for k in range(n_folds):
        lo, hi = k * n // n_folds, (k + 1) * n // n_folds
        b, c = sum(bc[lo:hi]), sum(cc[lo:hi])
        out.append({"fold": k, "n": hi - lo, "base_net": round(b, 4), "cand_net": round(c, 4),
                    "improved": c > b + EPS})
    return out


def adjudicate(gate, cfg, baseline, candidate, visible_path, holdout_path):
    """The lab rule. Returns {accepted, reasons, visible, holdout, folds}."""
    g = cfg["gate"]
    vis = gate.run_gate(visible_path, baseline, candidate, **_gate_kw(cfg, g["min_n"]))
    hold = gate.run_gate(holdout_path, baseline, candidate, **_gate_kw(cfg, cfg["holdout_min_n"]))
    reasons = []
    if vis["verdict"] != "PASS":
        reasons.append("visible verdict %s (only PASS counts): %s" % (vis["verdict"], vis["summary"]))
    if not vis["candidate"]["net"] > vis["baseline"]["net"] + EPS:
        reasons.append("no strict fee-true improvement on visible (%+.2f vs %+.2f)"
                       % (vis["candidate"]["net"], vis["baseline"]["net"]))
    if hold["verdict"] == "FAIL":
        reasons.append("holdout verdict FAIL: %s" % hold["summary"])
    folds = fold_results(gate, visible_path, baseline, candidate, cfg["n_folds"],
                         live_only=bool(g.get("live_only", False)))
    return {"accepted": not reasons, "reasons": reasons, "visible": vis, "holdout": hold,
            "folds": folds}
