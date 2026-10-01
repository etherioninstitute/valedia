"""Cron Watchdog — Valedia ASI
Checks if cron_runner_mac.sh is executable and cron log is fresh.
Alerts Burton via Signal if something is wrong.
Runs via a SEPARATE crontab entry (not through cron_runner_mac.sh).
Ops alerts carry no money figures: the text goes through signal_verify.ops_alert,
which strips/labels any money-like number and logs to data/signal/verify_log.jsonl.
(c) GOATWORTHY Post LLC
"""
import os, re, subprocess, sys, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RUNNER = os.path.expanduser("~/.openclaw/workspace/scripts/cron_runner_mac.sh")
LOG = os.path.expanduser("~/.openclaw/logs/valedia-cron.log")
TARGET = "uuid:47bd6dd5-2c96-4514-9541-acd8ed013612"
_MONEY = r"[+-]?\$\s*\d[\d,]*(?:\.\d+)?|(?<![\w.])[+-]?\d[\d,]*\.\d{2}(?![.\d])"


def _ssh_send(msg):
    """Relay transport (ssh -> openclaw). True only when the relay exits 0."""
    safe = msg.replace("$", "USD ").replace("'", "'\\''")
    r = subprocess.run([
        'ssh', '-o', 'ConnectTimeout=5', 'openclaw',
        f"openclaw message send --channel signal --target '{TARGET}' --message '{safe}'"
    ], capture_output=True, timeout=20)
    return r.returncode == 0


def alert(msg, send_fn=None):
    """Ops alert, text only, money figures stripped. Never raises."""
    send_fn = send_fn or _ssh_send
    try:
        import signal_verify as sv
        return sv.ops_alert("watchdog", msg, send_fn=send_fn)
    except Exception as e:  # verifier unavailable: still figure-free
        try:
            send_fn(re.sub(_MONEY, "[figure withheld]", msg))
        except Exception:
            pass
        print(f"[watchdog] verifier unavailable ({type(e).__name__}); sent figure-free text")


def check(send_fn=None):
    problems = []

    # 1. Is cron_runner_mac.sh executable?
    if os.path.exists(RUNNER) and not os.access(RUNNER, os.X_OK):
        problems.append("cron_runner_mac.sh lost execute permission!")
        # Auto-fix it
        os.chmod(RUNNER, 0o755)
        problems.append("AUTO-FIXED: chmod +x applied")
    elif not os.path.exists(RUNNER):
        problems.append("cron_runner_mac.sh is missing")

    # 2. Is the cron log fresh? (should update at least every 30 min from latency arb)
    if os.path.exists(LOG):
        age_min = (time.time() - os.path.getmtime(LOG)) / 60
        if age_min > 120:  # 2 hours stale = something is wrong
            problems.append(f"Cron log is {age_min:.0f} min stale (>2h)")
    else:
        problems.append("Cron log does not exist")

    # 3. Fix all .sh scripts preemptively
    scripts_dir = os.path.expanduser("~/.openclaw/workspace/scripts")
    try:
        names = os.listdir(scripts_dir)
    except OSError as e:
        names = []
        problems.append(f"Cannot list scripts dir ({type(e).__name__})")
    for f in names:
        if f.endswith('.sh'):
            fp = os.path.join(scripts_dir, f)
            if not os.access(fp, os.X_OK):
                os.chmod(fp, 0o755)
                problems.append(f"Fixed: {f} was not executable")

    if problems:
        msg = "WATCHDOG ALERT\n" + "\n".join(problems)
        alert(msg, send_fn)
        print(msg)
    else:
        pass  # Silent when healthy
    return problems


if __name__ == "__main__":
    check()
