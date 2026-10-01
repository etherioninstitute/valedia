"""Cron drift guard: scripts/install_cron.sh (source of truth) vs skills/valedia/cron-schedule.md.

Plain asserts, no network, never runs the installer or touches a crontab.
Run: python3 scripts/test_cron_schedule.py
"""
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
INSTALLER = os.path.join(HERE, "install_cron.sh")
DOC = os.path.join(REPO, "skills", "valedia", "cron-schedule.md")
INTEGRATIONS = os.path.join(HERE, "integrations")

PDT_OFFSET_H = 7  # PT -> UTC during PDT; the doc states UTC for PDT.

CRON_FIELD = r"[0-9*/,\-]+"
CRON_RE = re.compile(r"^(%s(?:\s+%s){4})\s+(/\S+)\s*(.*)$" % (CRON_FIELD, CRON_FIELD))
RUNNER_ARGS_RE = re.compile(r'^(\S+\.py)\s+"([^"]+)"')
SH_LABEL_RE = re.compile(r"#\s*(.+?)\s*$")


def parse_installer(text=None):
    """Return {(label): dict(cron, script, kind)} for active jobs in the CRON heredoc."""
    text = open(INSTALLER).read() if text is None else text
    m = re.search(r"<< 'CRON'\n(.*?)\nCRON\n", text, re.S)
    assert m, "CRON heredoc not found in install_cron.sh"
    jobs = {}
    deprecated = []
    for line in m.group(1).splitlines():
        s = line.strip()
        if s.startswith("#"):
            dm = re.match(r"#\s*(\S+\.py)\s+\"([^\"]+)\".*DEPRECATED", s)
            if dm:
                deprecated.append(dm.group(1))
            continue
        cm = CRON_RE.match(s)
        if not cm:
            continue
        cron, cmd, rest = cm.group(1), cm.group(2), cm.group(3)
        if os.path.basename(cmd) == "cron_runner_mac.sh":
            am = RUNNER_ARGS_RE.match(rest)
            assert am, "runner line without script+label: %r" % s
            script, label, kind = am.group(1), am.group(2), "runner"
        else:
            assert cmd.endswith(".sh"), "unexpected direct command: %r" % s
            lm = SH_LABEL_RE.search(rest)
            assert lm, "direct .sh job needs a trailing '# LABEL': %r" % s
            script, label, kind = os.path.basename(cmd), lm.group(1), "direct"
        assert label not in jobs, "duplicate job label in installer: %s" % label
        jobs[label] = {"cron": " ".join(cron.split()), "script": script, "kind": kind}
    return jobs, deprecated


def parse_doc(text=None):
    text = open(DOC).read() if text is None else text
    jobs = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        cron = cells[0].strip("`")
        if not re.fullmatch(r"%s(?:\s+%s){4}" % (CRON_FIELD, CRON_FIELD), cron):
            continue
        label, script = cells[2], cells[3]
        assert label not in jobs, "duplicate job label in doc: %s" % label
        jobs[label] = {"cron": " ".join(cron.split()), "script": script, "when": cells[1]}
    return jobs


def pt_to_utc(hour, minute):
    total = hour * 60 + minute + PDT_OFFSET_H * 60
    return (total // 60) % 24, total % 60, total >= 24 * 60


def check_times(label, cron, when):
    """Fixed-time jobs: the 'PT (UTC)' cell must agree with the cron fields."""
    fields = cron.split()
    minute, hour = fields[0], fields[1]
    tm = re.search(r"(\d{1,2}):(\d{2}) (AM|PM) PT \((\d{2}):(\d{2}) UTC( \+1d)?\)", when)
    if not (minute.isdigit() and hour.isdigit()):
        assert tm is None, "%s: interval job must not carry a fixed time cell: %r" % (label, when)
        return False
    assert tm, "%s: fixed-time job needs 'H:MM AM/PM PT (HH:MM UTC)' cell, got %r" % (label, when)
    h12, mm, ap = int(tm.group(1)), int(tm.group(2)), tm.group(3)
    h24 = (h12 % 12) + (12 if ap == "PM" else 0)
    assert (h24, mm) == (int(hour), int(minute)), \
        "%s: doc PT time %d:%02d != cron %s:%s" % (label, h24, mm, hour, minute)
    uh, um, nextday = pt_to_utc(h24, mm)
    assert (int(tm.group(4)), int(tm.group(5)), bool(tm.group(6))) == (uh, um, nextday), \
        "%s: doc UTC %s:%s%s != computed %02d:%02d%s" % (
            label, tm.group(4), tm.group(5), tm.group(6) or "", uh, um, " +1d" if nextday else "")
    return True


def script_path(job):
    if job["kind"] == "runner":
        return os.path.join(INTEGRATIONS, job["script"])
    return os.path.join(HERE, job["script"])


def test_same_jobs_and_times():
    inst, _ = parse_installer()
    doc = parse_doc()
    assert set(inst) == set(doc), "job set differs: only-installer=%s only-doc=%s" % (
        sorted(set(inst) - set(doc)), sorted(set(doc) - set(inst)))
    fixed = 0
    for label, j in inst.items():
        d = doc[label]
        assert d["cron"] == j["cron"], "%s: cron differs installer=%r doc=%r" % (label, j["cron"], d["cron"])
        assert d["script"] == j["script"], "%s: script differs installer=%r doc=%r" % (
            label, j["script"], d["script"])
        fixed += check_times(label, j["cron"], d["when"])
    assert fixed >= 15, "expected many fixed-time jobs, parsed %d" % fixed


def test_required_jobs_present():
    inst, _ = parse_installer()
    scripts = {j["script"] for j in inst.values()}
    for need in ("arb_run.py", "quant_run.py", "scanner_run.py", "mirofish_primary.sh",
                 "intel_sentiment.py", "dynamic_universe.py", "weather_morning_go.sh",
                 "brain_open_gated.sh", "sports_run.py", "allocator_run.py",
                 "settle_run.py", "reconcile_run.py"):
        assert need in scripts, "installer missing %s" % need
    # BRAIN OPEN must go through the gate, not straight to unified_brain
    assert inst["BRAIN OPEN"]["script"] == "brain_open_gated.sh"


def test_stagger_preserved():
    inst, _ = parse_installer()
    assert inst["SPORTS ENGINE"]["cron"] == "0 */2 * * *"
    assert inst["MASTER ALLOCATOR"]["cron"] == "20 */4 * * *"
    assert inst["SETTLEMENT"]["cron"] == "30 * * * *"
    assert inst["LEDGER RECONCILE"]["cron"] == "0 */6 * * *"


def test_referenced_scripts_exist():
    inst, _ = parse_installer()
    for label, j in inst.items():
        assert os.path.isfile(script_path(j)), "%s: %s missing at %s" % (label, j["script"], script_path(j))
    assert os.path.isfile(os.path.join(HERE, "cron_runner_mac.sh"))


def test_deprecated_not_installed():
    inst, deprecated = parse_installer()
    assert set(deprecated) >= {"emerging_crypto_scanner.py", "sentiment_scanner.py", "alpha_intelligence.py"}, deprecated
    active = {j["script"] for j in inst.values()}
    for name in deprecated:
        assert name not in active, "deprecated script still installed: %s" % name
        assert not os.path.isfile(os.path.join(INTEGRATIONS, name)), "%s should be renamed .DEPRECATED" % name
        assert os.path.isfile(os.path.join(INTEGRATIONS, name + ".DEPRECATED")), name
    # no installed script may be a .DEPRECATED stub
    for j in inst.values():
        assert not script_path(j).endswith(".DEPRECATED")
    doc_text = open(DOC).read()
    for name in deprecated:
        assert name in doc_text, "doc should mention removed %s" % name


def test_timezone_labelling():
    inst_text = open(INSTALLER).read()
    head = inst_text.split("RUNNER=", 1)[0]
    assert "All times in UTC" not in inst_text
    assert "America/Los_Angeles (PT)" in head and "mac-local" in head.lower()
    doc_text = open(DOC).read()
    assert "All times UTC" not in doc_text
    assert "Mac's LOCAL clock" in doc_text and "PT" in doc_text


def test_installer_syntax_only():
    r = subprocess.run(["bash", "-n", INSTALLER], capture_output=True, text=True)  # parse only, never executes
    assert r.returncode == 0, r.stderr


def test_parser_detects_drift():
    inst_text = open(INSTALLER).read()
    mutated = inst_text.replace('20 */4 * * * ', '0 */4 * * * ', 1)
    assert mutated != inst_text
    m_inst, _ = parse_installer(mutated)
    doc = parse_doc()
    assert m_inst["MASTER ALLOCATOR"]["cron"] != doc["MASTER ALLOCATOR"]["cron"]


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
    print("PASS test_cron_schedule (%d tests)" % n)
