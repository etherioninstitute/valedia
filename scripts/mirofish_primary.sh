#!/bin/bash
# MiroFish primary runner — real swarm first, intel_refresh fallback.
# mirofish_runner acquires a gpu-steward lease (batch_mirofish); the steward denies
# it outside the 01:00-07:00 PT quiet window or under memory pressure → we fall back.
# (c) GOATWORTHY Post LLC.
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"
INT="$HOME/.openclaw/workspace/scripts/integrations"
LOG="$HOME/.openclaw/logs/mirofish_primary.log"
PY=/usr/local/bin/python3
TS=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

echo "[$TS] mirofish_primary start" >> "$LOG"

# Ensure the MiroFish backend is up on :5001 (start it if a reboot killed the nohup proc)
if ! curl -s -m4 -o /dev/null "http://127.0.0.1:5001/api" 2>/dev/null; then
    echo "[$TS] backend down — starting it" >> "$LOG"
    ( cd "$HOME/mirofish-offline/backend" && \
      FLASK_DEBUG=0 nohup .venv/bin/python run.py >> "$HOME/.openclaw/logs/mirofish_offline_backend.log" 2>&1 & )
    for i in $(seq 1 15); do sleep 2; curl -s -m4 -o /dev/null "http://127.0.0.1:5001/api" 2>/dev/null && break; done
fi

"$PY" "$INT/mirofish_runner.py" >> "$LOG" 2>&1
RC=$?
if [ $RC -eq 0 ]; then
    echo "[$TS] real swarm OK — recording accuracy predictions" >> "$LOG"
    "$PY" "$INT/mirofish_accuracy_ledger.py" record >> "$LOG" 2>&1
    "$PY" "$INT/mirofish_accuracy_ledger.py" score  >> "$LOG" 2>&1   # score any due predictions
else
    echo "[$TS] real swarm exit=$RC (denied/failed) — FALLBACK intel_refresh (lightweight)" >> "$LOG"
    "$PY" "$INT/intel_refresh.py" >> "$LOG" 2>&1
fi
# Mark the overnight MiroFish phase complete for today (PT) so the gated BRAIN OPEN
# launcher fires with a complete analysis instead of on a blind clock. Written on BOTH
# the real-swarm and fallback paths — the signal means "phase done", not "succeeded".
mkdir -p "$HOME/.openclaw/state"
TZ=America/Los_Angeles date +%F > "$HOME/.openclaw/state/mirofish_ready"
echo "[$TS] mirofish_primary done (rc=$RC)" >> "$LOG"
