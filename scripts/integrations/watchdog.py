"""Cron Watchdog — Valedia ASI
Checks if cron_runner_mac.sh is executable and cron log is fresh.
Alerts Burton via Signal if something is wrong.
Runs via a SEPARATE crontab entry (not through cron_runner_mac.sh).
(c) GOATWORTHY Post LLC
"""
import os, subprocess, time
from datetime import datetime, timezone

RUNNER = os.path.expanduser("~/.openclaw/workspace/scripts/cron_runner_mac.sh")
LOG = os.path.expanduser("~/.openclaw/logs/valedia-cron.log")
TARGET = "uuid:47bd6dd5-2c96-4514-9541-acd8ed013612"

def check():
    problems = []
    
    # 1. Is cron_runner_mac.sh executable?
    if not os.access(RUNNER, os.X_OK):
        problems.append("cron_runner_mac.sh lost execute permission!")
        # Auto-fix it
        os.chmod(RUNNER, 0o755)
        problems.append("AUTO-FIXED: chmod +x applied")
    
    # 2. Is the cron log fresh? (should update at least every 30 min from latency arb)
    if os.path.exists(LOG):
        age_min = (time.time() - os.path.getmtime(LOG)) / 60
        if age_min > 120:  # 2 hours stale = something is wrong
            problems.append(f"Cron log is {age_min:.0f} min stale (>2h)")
    else:
        problems.append("Cron log does not exist")
    
    # 3. Fix all .sh scripts preemptively
    scripts_dir = os.path.expanduser("~/.openclaw/workspace/scripts")
    for f in os.listdir(scripts_dir):
        if f.endswith('.sh'):
            fp = os.path.join(scripts_dir, f)
            if not os.access(fp, os.X_OK):
                os.chmod(fp, 0o755)
                problems.append(f"Fixed: {f} was not executable")
    
    if problems:
        msg = "WATCHDOG ALERT\n" + "\n".join(problems)
        try:
            safe = msg.replace("$", "USD ").replace("'", "'\\''")
            subprocess.run([
                'ssh', '-o', 'ConnectTimeout=5', 'openclaw',
                f"openclaw message send --channel signal --target '{TARGET}' --message '{safe}'"
            ], capture_output=True, timeout=20)
        except:
            pass
        print(msg)
    else:
        pass  # Silent when healthy

if __name__ == "__main__":
    check()
