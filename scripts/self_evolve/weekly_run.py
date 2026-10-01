#!/usr/bin/env python3
"""weekly_run.py — weekly self-evolve over Valedia's real ledger -> ONE proposal report.

NOTHING IS LIVE. This is a PROPOSAL generator: it never writes live config, valedia_config.py,
crontab, models/ or the ledger, never places orders, never uses keys or the network, and never
passes --live (valedia_policy_lab.py refuses it too). Burton reads the report and decides; if he
approves, a human re-runs scripts/integrations/backtest_gate.py and applies the change by hand.

What it does (offline, builtin/serial self-evolve, grid proposer, no LLM):
  1. Reads the ledger (default ~/.openclaw/workspace/models/forward_test_ledger.jsonl, else the
     repo's models/forward_test_ledger.jsonl) read-only and joins settlements to entries with
     backtest_gate.build_trades (fee-true, real rows only).
  2. Groups trades into CATEGORIES (default: the family = canonical strategy up to the first
     "_", e.g. WEATHER_ARB -> WEATHER; the strategies inside a family are its sub-categories,
     which is what the grid proposer blocks / resizes). --group-by strategy = one category per
     canonical strategy.
  3. A category with >= HARD_MIN_N (30) settled LIVE trades gets a fresh policy lab in
     <root>/<YYYY-MM-DD>/<category> (outside the repo): init + evolve (grid, --max-rounds) and,
     if a candidate is accepted, export as a proposal. Fewer -> "waiting for data: n/30".
  4. Writes recommendations/proposals/SELF-EVOLVE-<date>.md and a .json twin: per category n
     (live vs backfill), baseline vs candidate fee-true net, gate + holdout verdicts, candidates
     searched, in-sample warning, calibration for the sources seen in that category's rows, and
     the exact policy diff. If nothing qualifies the report says so, with counts.

Exit 0 even when nothing qualifies; 1 on a real error (logged to stderr and in the report);
2 on bad usage.  Usage: python3 scripts/self_evolve/weekly_run.py [--date YYYY-MM-DD]
  [--ledger PATH] [--root DIR] [--report-dir DIR] [--max-rounds 3] [--min-n 30] [--n-boot 2000]
  [--group-by family|strategy] [--allow-thin-holdout] [--baseline POLICY.json]
(c) GOATWORTHY Post LLC.
"""
import argparse
import datetime as dt
import difflib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
INTEGRATIONS = os.path.join(REPO, "scripts", "integrations")
for _p in (HERE, INTEGRATIONS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import backtest_gate as gate  # noqa: E402
import valedia_policy_lab as V  # noqa: E402
from forward_test_ledger import canonical_category  # noqa: E402
from sie_shim import refused_flags  # noqa: E402

LAB_CLI = os.path.join(HERE, "valedia_policy_lab.py")
DEFAULT_BASELINE = os.path.join(INTEGRATIONS, "backtest_policies", "baseline_take_all.json")
DEFAULT_ROOT = "~/.openclaw/state/policy_lab"
DEFAULT_REPORT_DIR = os.path.join(REPO, "recommendations", "proposals")
BANNER = "NOTHING IS LIVE — proposals only; Burton approves."
IN_SAMPLE = ("IN-SAMPLE WARNING: the grid proposer searches the visible slice of the ledger, so "
             "visible-slice nets, gate and folds are in-sample (multiple comparisons). Only the "
             "time-held-out holdout verdict is out-of-sample.")
POLICY_FIELDS = ("min_edge", "categories_allow", "categories_block", "size_mult")
TIMEOUT_S = 3600


def default_ledger(home=None):
    """(path, origin): live workspace ledger when present, else the repo's models/ copy."""
    home = os.path.expanduser(home or "~")
    live = os.path.join(home, ".openclaw", "workspace", "models", "forward_test_ledger.jsonl")
    if os.path.isfile(live):
        return live, "workspace"
    return gate.DEFAULT_LEDGER, "repo"


def category_of(canon, group_by):
    c = str(canon or "").strip()
    if group_by == "family":
        c = c.split("_", 1)[0]
    return re.sub(r"[^A-Za-z0-9_.-]", "_", c) if c and c != "?" else "UNKNOWN"


# ── ledger -> per-category slices ───────────────────────────────────────────
def split_by_category(path, group_by):
    """{category: [raw ledger lines]}: entry+settlement rows of settled trades, by the
    settlement's strategy (as build_trades does). Read-only; order preserved."""
    with open(path) as f:
        lines = [ln if ln.endswith("\n") else ln + "\n" for ln in f if ln.strip()]
    rows = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            r = None
        rows.append(r if isinstance(r, dict) else None)
    entry_strat = {r["id"]: r.get("strategy") for r in rows
                   if r and r.get("type") == "entry" and isinstance(r.get("id"), str)}
    owner = {}
    for r in rows:
        if r and r.get("type") == "settlement" and isinstance(r.get("entry_id"), str):
            strat = r.get("strategy") or entry_strat.get(r["entry_id"]) or "?"
            owner.setdefault(r["entry_id"], category_of(canonical_category(strat), group_by))
    out = {}
    for ln, r in zip(lines, rows):
        if r is None:
            continue
        if r.get("type") == "entry":
            cat = owner.get(r.get("id"))
        elif r.get("type") == "settlement":
            cat = owner.get(r.get("entry_id")) or (
                None if isinstance(r.get("entry_id"), str)
                else category_of(canonical_category(r.get("strategy") or "?"), group_by))
        else:
            cat = None
        if cat:
            out.setdefault(cat, []).append(ln)
    return out


def count_categories(path, group_by):
    """({cat: {live, backfill, trades}}, ledger_stats) from the fee-true trade join."""
    recs, bad = gate.load_ledger(path)
    trades, stats = gate.build_trades(recs)
    cats = {}
    for t in trades:
        c = cats.setdefault(category_of(t["category"], group_by),
                            {"live": 0, "backfill": 0, "trades": []})
        c[t["source"]] += 1
        c["trades"].append(t)
    stats["records"] = len(recs) + bad
    return cats, stats


# ── lab plumbing (subprocess to the documented CLI; no --live ever) ──────────
def _lab(args, timeout=TIMEOUT_S):
    r = subprocess.run([sys.executable, LAB_CLI, *args], capture_output=True, text=True,
                       timeout=timeout)
    return r


def _json_out(r, what):
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise RuntimeError("%s: no JSON on stdout (exit %s): %s"
                           % (what, r.returncode, (r.stderr or r.stdout)[-600:]))


def pick_lab_dir(dated, cat):
    p, i = os.path.join(dated, cat), 2
    while os.path.exists(p):                    # same-date rerun: never reuse or delete a lab
        p, i = os.path.join(dated, "%s-r%d" % (cat, i)), i + 1
    return p


def policy_diff(base, cand):
    nb, nc = gate.load_policy(base), gate.load_policy(cand)
    changes = [{"field": k, "baseline": nb[k], "candidate": nc[k]}
               for k in POLICY_FIELDS if nb[k] != nc[k]]
    dump = lambda p: json.dumps({k: p[k] for k in POLICY_FIELDS}, indent=2, sort_keys=True).splitlines()
    unified = "\n".join(difflib.unified_diff(dump(nb), dump(nc), "baseline", "candidate",
                                              lineterm="", n=3))
    return {"changes": changes, "unified": unified}


def _rep(r):
    return {"verdict": r["verdict"], "n_baseline": r["baseline"]["n_taken"],
            "n_candidate": r["candidate"]["n_taken"], "net_baseline": r["baseline"]["net"],
            "net_candidate": r["candidate"]["net"], "summary": r["summary"]}


def calibration_for(slice_path, decisions, mirofish, now):
    try:
        import calibration_ledger as cal
        kw = {"decisions_path": decisions, "mirofish_path": mirofish}
        res = cal.score(ftl_path=slice_path, now=now,
                        **{k: v for k, v in kw.items() if v})
    except Exception as e:                      # analysis is advisory; never block the run
        return {"error": "%s: %s" % (type(e).__name__, e), "sources": {}}
    keep = ("n", "n_scorable", "brier", "bss", "hit_rate", "base_rate")
    return {"sources": {s: {k: st.get(k) for k in keep} for s, st in res.items() if st["n"]}}


def run_category(cat, info, lines, a, dated, baseline):
    """Lab init + evolve (+ export). Returns the category result dict (status evaluated)."""
    lab = pick_lab_dir(dated, cat)
    os.makedirs(os.path.join(dated, "_slices"), exist_ok=True)
    slice_p = os.path.join(dated, "_slices", os.path.basename(lab) + ".jsonl")
    with open(slice_p, "w") as f:
        f.write("".join(lines))
    res = {"category": cat, "status": "evaluated", "n_live": info["live"],
           "n_backfill": info["backfill"], "lab": lab, "accepted": False,
           "in_sample_warning": IN_SAMPLE}
    init_args = ["init", "--lab", lab, "--ledger", slice_p, "--baseline", a.baseline,
                 "--n-boot", str(a.n_boot)] + (["--allow-thin-holdout"] if a.allow_thin_holdout else [])
    r = _lab(init_args)
    if r.returncode != 0:
        raise RuntimeError("lab init failed: %s" % (r.stderr or r.stdout)[-600:])
    res["lab_split"] = _json_out(r, "lab init")["split"]
    rid = "weekly-%s" % a.date
    r = _lab(["evolve", "--lab", lab, "--run-id", rid, "--max-rounds", str(a.max_rounds),
              "--proposer", "grid"])
    ev = _json_out(r, "lab evolve")
    if r.returncode != 0:
        raise RuntimeError("lab evolve failed (exit %d): %s" % (r.returncode, (r.stderr or "")[-600:]))
    searches = ev.get("grid_searches") or []
    res["candidates_searched"] = sum(int(s.get("searched") or 0) for s in searches)
    res["grid_rounds"] = searches
    base_rep, _ = gate.replay(info["trades"], gate.load_policy(baseline))
    res["baseline_full_slice"] = {"n": base_rep["n_taken"], "net": base_rep["net"]}
    res["calibration"] = calibration_for(slice_p, a.decisions, a.mirofish,
                                         dt.datetime.fromisoformat(a.date + "T00:00:00+00:00"))
    if not ev.get("accepted_versions"):
        last = searches[-1] if searches else {}
        chosen = (last.get("chosen") or {}).get("move")
        if last.get("reason"):
            why = last["reason"]
        elif chosen:
            why = ("grid chose %r on the visible slice but the lab did not accept it (holdout "
                   "too thin to judge, holdout FAIL, or gate not PASS)" % chosen)
        else:
            why = "no candidate accepted: %s" % ev.get("note", "see lab status")
        res["reason_not_accepted"] = why
        res["gate_verdict"] = res["holdout_verdict"] = None
        return res
    prop_p = os.path.join(dated, os.path.basename(lab) + ".proposal.json")
    r = _lab(["export", "--lab", lab, "--run-id", rid, "--out", prop_p])
    if r.returncode != 0:
        raise RuntimeError("lab export failed: %s" % (r.stderr or r.stdout)[-600:])
    with open(prop_p) as f:
        prop = json.load(f)
    adj = prop["adjudication"]
    gr = prop["gate_reports"]
    res.update(accepted=True, proposal_json=prop_p, vid=prop["vid"],
               gate_verdict=adj["visible_verdict"], holdout_verdict=adj["holdout_verdict"],
               folds_improved="%d/%d" % (adj["folds_improved"], adj["n_folds"]),
               allow_thin_holdout=adj["allow_thin_holdout"],
               gate_reports={k: _rep(gr[k]) for k in ("visible", "holdout", "full_snapshot_in_sample")},
               in_sample_warning=prop["multiple_comparisons_warning"],
               candidate_policy=prop["candidate_policy"], baseline_policy=prop["baseline_policy"],
               policy_diff=policy_diff(prop["baseline_policy"], prop["candidate_policy"]))
    return res


# ── report ──────────────────────────────────────────────────────────────────
def _money(x):
    return "n/a" if x is None else "%+.2f" % x


def render_md(doc):
    cats = doc["categories"]
    ev = [c for c in cats if c["status"] == "evaluated"]
    acc = [c for c in ev if c["accepted"]]
    wait = [c for c in cats if c["status"] == "waiting_for_data"]
    err = [c for c in cats if c["status"] == "error"]
    L = ["# " + BANNER, "",
         "Self-evolve weekly run for %s. Nothing here is applied: no live config, crontab, models/ "
         "or orders were touched, and `--live` is never passed. Approve or ignore each proposal; "
         "if approved, a human re-runs `scripts/integrations/backtest_gate.py` on the current ledger "
         "(prefer `--live-only` once live settlements exist) and applies it by hand." % doc["date"], "",
         "- Ledger: `%s` (%s)" % (doc["ledger"], doc["ledger_origin"]),
         "- Ledger rows: %d; settled fee-true trades: %d (live %d, backfill %d)"
         % (doc["ledger_stats"]["records"], doc["ledger_stats"]["eligible"],
            doc["ledger_stats"].get("source:live", 0), doc["ledger_stats"].get("source:backfill", 0)),
         "- Categories by `%s`; floor %d settled live trades; grid proposer, max %d rounds, "
         "offline (no LLM, no network); thin holdout %s"
         % (doc["group_by"], doc["min_live_n"], doc["max_rounds"],
            "ALLOWED (in-sample risk)" if doc["allow_thin_holdout"] else "blocks acceptance"),
         "- Result: %d categories seen, %d evaluated, %d accepted proposal(s), %d waiting for data, "
         "%d error(s)" % (len(cats), len(ev), len(acc), len(wait), len(err)), ""]
    if not acc:
        L += ["**No category produced an accepted proposal this week.** Counts: %s."
              % ("; ".join("%s live=%d backfill=%d %s" % (
                  c["category"], c["n_live"], c["n_backfill"],
                  "waiting" if c["status"] == "waiting_for_data" else c["status"]) for c in cats)
                 or "no settled trades in the ledger"), ""]
    if cats:
        L += ["| Category | Live n | Backfill n | Status | Baseline net | Candidate net | Gate | Holdout |",
              "|---|---|---|---|---|---|---|---|"]
        for c in cats:
            if c["status"] == "evaluated" and c["accepted"]:
                f = c["gate_reports"]["full_snapshot_in_sample"]
                row = ("ACCEPTED proposal", _money(f["net_baseline"]), _money(f["net_candidate"]),
                       c["gate_verdict"], c["holdout_verdict"])
            elif c["status"] == "evaluated":
                row = ("no candidate accepted", _money(c["baseline_full_slice"]["net"]), "-", "-", "-")
            elif c["status"] == "waiting_for_data":
                row = ("waiting for data: %d/%d" % (c["n_live"], doc["min_live_n"]), "-", "-", "-", "-")
            else:
                row = ("ERROR", "-", "-", "-", "-")
            L.append("| %s | %d | %d | %s |" % (c["category"], c["n_live"], c["n_backfill"],
                                                " | ".join(row)))
        L += ["", "Nets are fee-true (Kalshi fee modelled where unrecorded). Baseline/candidate net "
              "in the table is the full category slice, IN-SAMPLE.", ""]
    for c in ev:
        L += ["## %s — %s" % (c["category"], "ACCEPTED PROPOSAL" if c["accepted"]
                                   else "no candidate accepted"), "",
              "- Settled trades: live %d, backfill %d (lab ledger: %d visible / %d holdout rows)"
              % (c["n_live"], c["n_backfill"], c["lab_split"]["visible_rows"], c["lab_split"]["holdout_rows"]),
              "- Candidates searched (grid, in-sample): %d" % c["candidates_searched"],
              "- Lab: `%s`" % c["lab"]]
        if c["accepted"]:
            L += ["- Gate verdict (visible): %s; holdout verdict: %s; folds improved: %s"
                  % (c["gate_verdict"], c["holdout_verdict"], c["folds_improved"]), ""]
            L += ["| Slice | n base | n cand | Baseline net | Candidate net | Verdict |", "|---|---|---|---|---|---|"]
            for k, lbl in (("visible", "visible (in-sample)"), ("holdout", "holdout (out-of-sample)"),
                           ("full_snapshot_in_sample", "full slice (in-sample)")):
                g = c["gate_reports"][k]
                L.append("| %s | %d | %d | %s | %s | %s |" % (lbl, g["n_baseline"], g["n_candidate"],
                         _money(g["net_baseline"]), _money(g["net_candidate"]), g["verdict"]))
            L += ["", c["in_sample_warning"], "", "Policy diff (baseline -> candidate):", "",
                  "```diff", c["policy_diff"]["unified"] or "(no field differs)", "```", "",
                  "Full candidate policy:", "", "```json",
                  json.dumps(c["candidate_policy"], indent=2, sort_keys=True), "```", "",
                  "Proposal JSON: `%s`" % c["proposal_json"], ""]
        else:
            L += ["- Baseline fee-true net on the full slice: %s (n=%d)"
                  % (_money(c["baseline_full_slice"]["net"]), c["baseline_full_slice"]["n"]),
                  "- Why nothing was proposed: %s" % c["reason_not_accepted"], "", c["in_sample_warning"], ""]
        cal = c.get("calibration", {})
        if cal.get("error"):
            L += ["Calibration: unavailable (%s)" % cal["error"], ""]
        elif cal.get("sources"):
            L += ["Calibration of sources seen in this category's rows:", "",
                  "| Source | n | scored | Brier | BSS | Hit rate |", "|---|---|---|---|---|---|"]
            for s, m in sorted(cal["sources"].items()):
                L.append("| %s | %d | %d | %s | %s | %s |" % (s, m["n"], m["n_scorable"], m["brier"],
                                                              m["bss"], m["hit_rate"]))
            L.append("")
        else:
            L += ["Calibration: no scored source data for this category's rows.", ""]
    if wait:
        L += ["## Waiting for data", ""]
        L += ["- %s: waiting for data: %d/%d (live %d, backfill %d)"
              % (c["category"], c["n_live"], doc["min_live_n"], c["n_live"], c["n_backfill"]) for c in wait]
        L.append("")
    if err:
        L += ["## Errors (logged)", ""] + ["- %s: %s" % (c["category"], c["error"]) for c in err] + [""]
    return "\n".join(L).rstrip() + "\n"


# ── main ────────────────────────────────────────────────────────────────────
def _err(msg, code=1):
    sys.stderr.write("weekly_run: %s\n" % msg)
    return code


def _path_ok(p, label):
    """Outputs inside the repo must be under recommendations/ (never models/, config, scripts)."""
    if V._inside(p, REPO) and not V._inside(p, os.path.join(REPO, "recommendations")):
        raise ValueError("%s %s is inside the Valedia tree but not under recommendations/" % (label, p))


def build_parser():
    ap = argparse.ArgumentParser(prog="weekly_run", description="weekly self-evolve proposals "
                                 "(proposal-only; nothing is applied)")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default today, local)")
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--root", default=DEFAULT_ROOT, help="lab root (outside the repo)")
    ap.add_argument("--report-dir", default=DEFAULT_REPORT_DIR)
    ap.add_argument("--max-rounds", type=int, default=3)
    ap.add_argument("--min-n", type=int, default=gate.HARD_MIN_N,
                    help="live-trade floor per category (clamped up to %d)" % gate.HARD_MIN_N)
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--group-by", choices=("family", "strategy"), default="family")
    ap.add_argument("--allow-thin-holdout", action="store_true")
    ap.add_argument("--baseline", default=DEFAULT_BASELINE)
    ap.add_argument("--decisions", default=None, help="Nimble decisions.jsonl for calibration")
    ap.add_argument("--mirofish", default=None, help="mirofish accuracy ledger for calibration")
    return ap


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if refused_flags(argv):
        return _err("refusing --live/--self: this tool is proposal-only", 2)
    try:
        a = build_parser().parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    a.date = a.date or dt.date.today().isoformat()
    try:
        dt.date.fromisoformat(a.date)
    except ValueError:
        return _err("--date must be YYYY-MM-DD", 2)
    if not 1 <= a.max_rounds <= 5 or a.n_boot < 1 or a.min_n < 1:
        return _err("--max-rounds must be 1..5; --n-boot and --min-n >= 1", 2)
    a.min_n = max(a.min_n, gate.HARD_MIN_N)
    origin = "--ledger"
    if a.ledger is None:
        a.ledger, origin = default_ledger()
    a.ledger = os.path.abspath(os.path.expanduser(a.ledger))
    root = V._real(a.root)
    report_dir = V._real(a.report_dir)
    try:
        if V._inside(root, REPO):
            raise ValueError("--root %s is inside the Valedia tree; labs must live outside it" % root)
        _path_ok(report_dir, "--report-dir")
        if not os.path.isfile(a.ledger):
            raise ValueError("ledger not found: %s" % a.ledger)
        counts, stats = count_categories(a.ledger, a.group_by)
        slices = split_by_category(a.ledger, a.group_by)
    except (OSError, ValueError) as e:
        return _err("%s: %s" % (type(e).__name__, e), 2 if isinstance(e, ValueError) else 1)
    dated = os.path.join(root, a.date)
    baseline = gate.load_policy(a.baseline)
    results, failed = [], 0
    for cat in sorted(counts):
        info = counts[cat]
        if info["live"] < a.min_n:
            results.append({"category": cat, "status": "waiting_for_data", "n_live": info["live"],
                            "n_backfill": info["backfill"], "needed": a.min_n})
            continue
        try:
            results.append(run_category(cat, info, slices.get(cat, []), a, dated, baseline))
        except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as e:
            failed += 1
            sys.stderr.write("weekly_run: category %s failed: %s: %s\n" % (cat, type(e).__name__, e))
            results.append({"category": cat, "status": "error", "n_live": info["live"],
                            "n_backfill": info["backfill"], "error": "%s: %s" % (type(e).__name__, e)})
    doc = {"kind": "valedia_self_evolve_weekly", "banner": BANNER, "date": a.date,
           "ledger": a.ledger, "ledger_origin": origin, "group_by": a.group_by,
           "min_live_n": a.min_n, "max_rounds": a.max_rounds, "n_boot": a.n_boot,
           "allow_thin_holdout": bool(a.allow_thin_holdout), "root": dated,
           "ledger_stats": {k: stats.get(k, 0) for k in ("records", "eligible", "source:live",
                                                         "source:backfill")},
           "categories": results,
           "summary": {"seen": len(results),
                       "accepted": sum(1 for c in results if c.get("accepted")),
                       "waiting_for_data": sum(1 for c in results if c["status"] == "waiting_for_data"),
                       "errors": failed}}
    try:
        os.makedirs(report_dir, exist_ok=True)
        stem = os.path.join(report_dir, "SELF-EVOLVE-%s" % a.date)
        with open(stem + ".md", "w") as f:
            f.write(render_md(doc))
        with open(stem + ".json", "w") as f:
            json.dump(doc, f, indent=2, sort_keys=True)
            f.write("\n")
    except OSError as e:
        return _err("could not write report: %s" % e)
    print("self-evolve weekly %s: %d categories, %d proposal(s), %d waiting for data, %d error(s); "
          "report %s.md (NOTHING IS LIVE)" % (a.date, len(results), doc["summary"]["accepted"],
                                              doc["summary"]["waiting_for_data"], failed, stem))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
