#!/usr/bin/env python3
"""Checks for scripts/self_evolve/weekly_run.py. Run: python3 scripts/self_evolve/test_weekly_run.py
Plain asserts. Synthetic ledgers in a tempdir; reports/labs go to tempdirs, never the repo.
No network (sockets blocked in-process; the lab is offline by design), no keys, never --live,
never writes models/ or config. Needs pytest + git (the policy lab's grader)."""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "scripts", "integrations"))
import weekly_run as W  # noqa: E402
from forward_test_ledger import kalshi_fee  # noqa: E402

TMP = tempfile.mkdtemp(prefix="weekly_run_test_")
DATE = "2026-10-01"
NOCAL = ["--decisions", os.path.join(TMP, "none.jsonl"), "--mirofish", os.path.join(TMP, "none2.jsonl")]


def _rows(strategy, n, tag, day0, win_fn, source="live"):
    out, fee = [], kalshi_fee(0.5, 10)
    for i in range(n):
        eid, won = "%s%03d" % (tag, i), win_fn(i)
        ts = "2026-%02d-%02dT%02d:00:00Z" % (1 + day0 // 28, 1 + (day0 + i // 24) % 28, i % 24)
        if source == "live":
            out.append({"type": "entry", "id": eid, "ts": ts, "strategy": strategy, "venue": "kalshi",
                        "side": "yes", "entry_price": 0.5, "contracts": 10, "entry_fee": fee,
                        "source": "live"})
            out.append({"type": "settlement", "entry_id": eid, "strategy": strategy, "won": won,
                        "result": "yes" if won else "no", "filled_contracts": 10, "fee": fee,
                        "realized_pnl_net": round((5.0 if won else -5.0) - fee, 6), "source": "live"})
        else:                                                    # backfill: unverified fee, no price
            out.append({"type": "entry", "id": eid, "ts": ts, "strategy": strategy, "venue": "kalshi",
                        "side": "yes", "entry_price": 0.0, "contracts": 0.0, "entry_fee": 0.0,
                        "source": "backfill:pnl_tracker"})
            out.append({"type": "settlement", "entry_id": eid, "strategy": strategy,
                        "result": "yes" if won else "no", "realized_pnl_net": 0.4 if won else -0.6,
                        "fee": None, "fee_status": "unverified_source", "cost": 0.6, "won": won,
                        "source": "backfill:pnl_tracker"})
    return out


def _write(path, rows):
    with open(path, "w") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows))
    return path


def make_ledger(path):
    """WEATHER family: WEATHER_GOOD wins 7/10, WEATHER_BAD always loses (improvable: block it).
    OIL: 12 live + 40 backfill (thin live -> waiting). SPORTS: backfill only."""
    rows = []
    for i in range(150):
        strat = "WEATHER_RARE" if i % 37 == 5 else ("WEATHER_BAD" if i % 3 == 0 else "WEATHER_GOOD")
        rows += _rows(strat, 1, "w%03d_" % i, i // 24, lambda _, i=i, s=strat:
                      s != "WEATHER_BAD" and i % 10 not in (1, 4, 7))
    # fix timestamps to be strictly increasing per trade index (one row group per i above)
    for r in rows:
        if r["type"] == "entry":
            i = int(r["id"][1:4])
            r["ts"] = "2026-01-%02dT%02d:00:00Z" % (1 + i // 24, i % 24)
    rows += _rows("OIL_BRACKET", 12, "o", 10, lambda i: i % 2 == 0)
    rows += _rows("OIL_BRACKET", 40, "ob", 11, lambda i: i % 2 == 0, source="backfill")
    rows += _rows("SPORTS", 20, "sb", 12, lambda i: i % 3 != 0, source="backfill")
    return _write(path, rows)


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else None


def _git_status():
    return subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=REPO,
                          capture_output=True, text=True).stdout


def _run(name, ledger, extra=()):
    root, rep = os.path.join(TMP, name, "labs"), os.path.join(TMP, name, "report")
    rc = W.main(["--date", DATE, "--ledger", ledger, "--root", root, "--report-dir", rep,
                 "--max-rounds", "3", "--n-boot", "400", *NOCAL, *extra])
    return rc, root, rep


def _read(rep):
    md = open(os.path.join(rep, "SELF-EVOLVE-%s.md" % DATE)).read()
    js = json.load(open(os.path.join(rep, "SELF-EVOLVE-%s.json" % DATE)))
    return md, js


class _Spy:
    """Record every subprocess.run command weekly_run makes; pass through."""
    def __init__(self):
        self.cmds, self.real = [], subprocess.run

    def __call__(self, cmd, *a, **k):
        self.cmds.append(list(cmd))
        return self.real(cmd, *a, **k)


def test_main_flow():
    ledger = make_ledger(os.path.join(TMP, "ledger.jsonl"))
    models = os.path.join(REPO, "models", "forward_test_ledger.jsonl")
    before = (_git_status(), _sha(models), _sha(os.path.join(REPO, "scripts", "integrations",
                                                          "valedia_config.py")))
    spy, real_connect = _Spy(), socket.socket.connect

    def no_net(*a, **k):
        raise AssertionError("network attempted")
    W.subprocess.run, socket.socket.connect = spy, no_net
    try:
        rc, root, rep = _run("main", ledger)
    finally:
        W.subprocess.run, socket.socket.connect = spy.real, real_connect
    assert rc == 0, rc
    md, js = _read(rep)
    # banner is the very first line
    assert md.splitlines()[0] == "# NOTHING IS LIVE — proposals only; Burton approves.", md[:80]
    assert js["banner"] == "NOTHING IS LIVE — proposals only; Burton approves."
    cats = {c["category"]: c for c in js["categories"]}
    assert set(cats) == {"WEATHER", "OIL", "SPORTS"}, set(cats)
    # improvable category: accepted proposal with the exact diff
    w = cats["WEATHER"]
    assert w["status"] == "evaluated" and w["accepted"] is True, w
    assert w["n_live"] >= 30 and w["n_backfill"] == 0
    assert w["gate_verdict"] == "PASS" and w["holdout_verdict"] in ("PASS", "INSUFFICIENT_DATA")
    assert w["candidates_searched"] > 0 and "IN-SAMPLE" in w["in_sample_warning"]
    assert "WEATHER_BAD" in w["candidate_policy"]["categories_block"]
    fields = {c["field"] for c in w["policy_diff"]["changes"]}
    assert "categories_block" in fields, fields
    g = w["gate_reports"]["full_snapshot_in_sample"]
    assert g["net_candidate"] > g["net_baseline"], g
    assert "```diff" in md and "+++ candidate" in md and "WEATHER_BAD" in md
    assert "ACCEPTED PROPOSAL" in md and "IN-SAMPLE WARNING" in md and "holdout" in md.lower()
    assert "Calibration" in md
    assert os.path.isfile(w["proposal_json"]) and w["proposal_json"].startswith(root)
    # thin categories: waiting for data, with live-vs-backfill counts, and no lab created
    assert cats["OIL"]["status"] == "waiting_for_data"
    assert (cats["OIL"]["n_live"], cats["OIL"]["n_backfill"]) == (12, 40)
    assert "waiting for data: 12/30" in md and "waiting for data: 0/30" in md
    assert cats["SPORTS"]["n_live"] == 0 and cats["SPORTS"]["n_backfill"] == 20
    dated = os.path.join(root, DATE)
    assert sorted(d for d in os.listdir(dated) if not d.startswith("_") and "." not in d) == ["WEATHER"]
    # fence: nothing in the repo changed; no --live ever; only the lab CLI was invoked
    after = (_git_status(), _sha(models), _sha(os.path.join(REPO, "scripts", "integrations",
                                                         "valedia_config.py")))
    assert before == after, "repo changed during weekly run"
    assert spy.cmds and all(c[1].endswith("valedia_policy_lab.py") for c in spy.cmds), spy.cmds
    assert not any(W.refused_flags(c) for c in spy.cmds), "a --live/--self flag was passed"
    assert [c[2] for c in spy.cmds] == ["init", "evolve", "export"], [c[2] for c in spy.cmds]


def test_deterministic():
    ledger = os.path.join(TMP, "ledger.jsonl")
    outs = []
    for name in ("det1", "det2"):
        rc, root, rep = _run(name, ledger)
        assert rc == 0
        md, js = _read(rep)
        base = os.path.join(TMP, name)
        outs.append((md.replace(base, "<T>"), json.dumps(js, sort_keys=True).replace(base, "<T>")))
    assert outs[0] == outs[1], "report is not deterministic for a fixed date/ledger"
    assert "Burton approves" in outs[0][0]


def test_same_date_rerun_never_reuses_lab():
    ledger = os.path.join(TMP, "ledger.jsonl")
    root, rep = os.path.join(TMP, "rerun", "labs"), os.path.join(TMP, "rerun", "report")
    args = ["--date", DATE, "--ledger", ledger, "--root", root, "--report-dir", rep,
            "--n-boot", "400", *NOCAL]
    assert W.main(args) == 0 and W.main(args) == 0
    dated = os.path.join(root, DATE)
    assert os.path.isdir(os.path.join(dated, "WEATHER")) and os.path.isdir(os.path.join(dated, "WEATHER-r2"))


def test_nothing_qualifies():
    rows = _rows("OIL_BRACKET", 12, "o", 10, lambda i: i % 2 == 0)
    rows += _rows("SPORTS", 25, "s", 11, lambda i: i % 2 == 0, source="backfill")
    ledger = _write(os.path.join(TMP, "thin.jsonl"), rows)
    rc, root, rep = _run("thin", ledger)
    assert rc == 0, "exit must be 0 when nothing qualifies"
    md, js = _read(rep)
    assert md.splitlines()[0].startswith("# NOTHING IS LIVE")
    assert "No category produced an accepted proposal" in md
    assert "OIL live=12 backfill=0 waiting" in md and "SPORTS live=0 backfill=25 waiting" in md
    assert "waiting for data: 12/30" in md
    assert js["summary"] == {"seen": 2, "accepted": 0, "waiting_for_data": 2, "errors": 0}
    assert not os.path.exists(os.path.join(root, DATE)), "no lab should be created below the floor"


def test_evaluated_but_not_accepted():
    """>= 30 live trades but a holdout too thin to judge: lab runs, nothing is accepted, and the
    report says so honestly (no proposal, no diff)."""
    rows = []
    for i in range(45):
        strat = "WEATHER_BAD" if i % 3 == 0 else "WEATHER_GOOD"
        rows += _rows(strat, 1, "t%03d_" % i, 0, lambda _, s=strat, i=i: s != "WEATHER_BAD" and i % 10 != 4)
        rows[-2]["ts"] = "2026-01-%02dT%02d:00:00Z" % (1 + i // 24, i % 24)
    ledger = _write(os.path.join(TMP, "mid.jsonl"), rows)
    rc, root, rep = _run("mid", ledger)
    assert rc == 0
    md, js = _read(rep)
    w = js["categories"][0]
    assert w["status"] == "evaluated" and w["accepted"] is False and w["n_live"] == 45, w
    assert "reason_not_accepted" in w and w["candidates_searched"] > 0
    assert "no candidate accepted" in md and "No category produced an accepted proposal" in md
    assert "```diff" not in md


def test_empty_and_errors():
    empty = _write(os.path.join(TMP, "empty.jsonl"), [])
    rc, _, rep = _run("empty", empty)
    assert rc == 0
    md, _ = _read(rep)
    assert "no settled trades in the ledger" in md
    assert _run("missing", os.path.join(TMP, "nope.jsonl"))[0] != 0
    assert W.main(["--live"]) == 2 and W.main(["--liv"]) == 2
    assert W.main(["--date", "nope"]) == 2
    # outputs inside the repo are only allowed under recommendations/; labs never inside the repo
    ledger = os.path.join(TMP, "thin.jsonl")
    assert W.main(["--ledger", ledger, "--root", os.path.join(REPO, "scripts", "x")]) == 2
    assert W.main(["--ledger", ledger, "--root", os.path.join(TMP, "r"),
                   "--report-dir", os.path.join(REPO, "models")]) == 2
    assert not os.path.exists(os.path.join(REPO, "scripts", "x"))


def test_default_ledger_fallback():
    home = os.path.join(TMP, "home")
    assert W.default_ledger(home) == (W.gate.DEFAULT_LEDGER, "repo")
    live = os.path.join(home, ".openclaw", "workspace", "models")
    os.makedirs(live)
    _write(os.path.join(live, "forward_test_ledger.jsonl"), [])
    assert W.default_ledger(home) == (os.path.join(live, "forward_test_ledger.jsonl"), "workspace")


def test_group_by_strategy_splits_sub_categories():
    cats, _ = W.count_categories(os.path.join(TMP, "ledger.jsonl"), "strategy")
    assert {"WEATHER_GOOD", "WEATHER_BAD", "OIL_BRACKET", "SPORTS"} <= set(cats), set(cats)
    fam, _ = W.count_categories(os.path.join(TMP, "ledger.jsonl"), "family")
    assert fam["WEATHER"]["live"] == sum(cats[k]["live"] for k in cats if k.startswith("WEATHER"))


if __name__ == "__main__":
    try:
        n = 0
        for name, fn in sorted(globals().items(), key=lambda kv: kv[1].__code__.co_firstlineno
                               if callable(kv[1]) and hasattr(kv[1], "__code__") else 0):
            if name.startswith("test_") and callable(fn):
                fn()
                n += 1
        print("PASS test_weekly_run (%d tests)" % n)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
