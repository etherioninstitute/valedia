#!/usr/bin/env python3
"""LEGACY TESTS LEAVE state/ ALONE: running the legacy kill-switch tests must not create or
modify <repo>/state/halt.json or <repo>/state/halt-ping.json in the checkout they run in.

Why: valedia_config.halt_state_path() and halt_ping_path() deliberately return the
repository's own state/ files (the env override below the return is unreachable, and the
current risk gate, valedia_trader/research_loop/risk_gate.py HALT_FILE, watches the same
path). So any test that calls write_halt_state() without replacing those two functions
writes the operator's real halt file, and the risk gate treats that file's PRESENCE as a
halt, whatever it contains. On 2026-11-16 this halted the trader for 77 minutes.

Run: python3 test_legacy_tests_leave_state_alone.py   (or under pytest)
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
STATE = os.path.join(REPO, "state")
GUARDED = ("halt.json", "halt-ping.json")
# Every legacy test file that can reach write_halt_state() / record_halt_event(arm=True).
TOUCHED = ("test_controls_readout.py", "test_kill_switch.py")


def _snapshot():
    snap = {}
    for name in GUARDED:
        p = os.path.join(STATE, name)
        if os.path.lexists(p):
            st = os.stat(p)
            with open(p, "rb") as f:
                snap[name] = (st.st_mtime_ns, st.st_size, f.read())
        else:
            snap[name] = None
    return snap


def _put_back(before):
    """Undo whatever the run did, so a failing run does not leave a halt behind."""
    for name in GUARDED:
        p = os.path.join(STATE, name)
        if before[name] is None:
            if os.path.lexists(p):
                os.remove(p)
        else:
            mtime_ns, _size, data = before[name]
            with open(p, "wb") as f:
                f.write(data)
            os.utime(p, ns=(mtime_ns, mtime_ns))


def test_touched_legacy_tests_leave_real_state_alone():
    before = _snapshot()
    problems = []
    try:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        for fn in TOUCHED:
            # Only the exit status of these runs is irrelevant here (many legacy tests fail for
            # missing fixtures); what matters is what they leave behind in state/.
            subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q",
                            "--no-header", os.path.join(HERE, fn)],
                           cwd=REPO, env=env, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False)
            after = _snapshot()
            for name in GUARDED:
                if after[name] != before[name]:
                    what = "created" if before[name] is None else "modified"
                    problems.append(f"{fn} {what} state/{name}")
            _put_back(before)
    finally:
        _put_back(before)
    assert not problems, "; ".join(problems)


if __name__ == "__main__":
    test_touched_legacy_tests_leave_real_state_alone()
    print("OK")
