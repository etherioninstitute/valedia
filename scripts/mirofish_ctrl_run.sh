#!/bin/bash
# Controlled MiroFish validation run (manual, daytime). Temporarily widens the GPU-steward
# quiet window so the real swarm can acquire a lease now, runs the REAL production path
# (mirofish_primary.sh -> real swarm -> record() -> mirofish_ready marker), and ALWAYS reverts
# the window on exit (trap) so the steward policy is restored even on crash.
# Usage:  nohup ~/.openclaw/workspace/scripts/mirofish_ctrl_run.sh >/dev/null 2>&1 &
#         tail -f ~/.openclaw/logs/mirofish_primary.log      # watch progress
#         python3 ~/.openclaw/workspace/scripts/mirofish_healthcheck.py   # verify loop closed
set -u
POL="$HOME/.openclaw/tools/gpu-steward/policy.yaml"
BAK="$POL.bak.ctrlrun.$(date +%s)"
LOG="$HOME/.openclaw/logs/mirofish_primary.log"
cp "$POL" "$BAK"
revert(){ cp "$BAK" "$POL"; echo "[ctrl-run $(date -u +%H:%M:%S)] steward window REVERTED from $BAK" >> "$LOG"; }
trap revert EXIT
sed -i '' 's/end: "07:00"/end: "23:59"/' "$POL"
echo "[ctrl-run $(date -u +%H:%M:%S)] window widened; launching REAL production run" >> "$LOG"
"$HOME/.openclaw/workspace/scripts/mirofish_primary.sh"
echo "[ctrl-run $(date -u +%H:%M:%S)] mirofish_primary returned rc=$?" >> "$LOG"
