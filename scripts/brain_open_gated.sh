#!/bin/bash
# Gated BRAIN OPEN launcher — dynamic dependency on the overnight MiroFish phase.
# Fires unified_brain.py "BRAIN OPEN" only once MiroFish's phase is complete for today (PT),
# but never later than BRAIN_OPEN_WAIT_SECS past start, so the market open is never missed.
# This is a COORDINATED wait, not an arbitrary killer (see memory: coordinate-timeouts-no-arbitrary-caps).
# (c) GOATWORTHY Post LLC.
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"
STATE="$HOME/.openclaw/state"
RUNNER="$HOME/.openclaw/workspace/scripts/cron_runner_mac.sh"
LOG="$HOME/.openclaw/logs/brain_open_gated.log"
TODAY=$(TZ=America/Los_Angeles date +%F)
DEADLINE_SECS="${BRAIN_OPEN_WAIT_SECS:-1200}"   # wait up to 20 min for MiroFish freshness, then proceed

mkdir -p "$STATE"
log(){ echo "[$(TZ=America/Los_Angeles date '+%F %T %Z')] $*" >> "$LOG"; }

# Idempotent: don't run twice for the same day.
if [ "$(cat "$STATE/brain_open_done" 2>/dev/null)" = "$TODAY" ]; then
    log "BRAIN OPEN already ran today ($TODAY) — skip"; exit 0
fi

# Bounded wait for MiroFish's overnight phase to be fresh for today.
waited=0
while [ "$(cat "$STATE/mirofish_ready" 2>/dev/null)" != "$TODAY" ] && [ "$waited" -lt "$DEADLINE_SECS" ]; do
    sleep 30; waited=$((waited + 30))
done
if [ "$(cat "$STATE/mirofish_ready" 2>/dev/null)" = "$TODAY" ]; then
    log "MiroFish complete for $TODAY (waited ${waited}s) — running BRAIN OPEN with full analysis"
else
    log "MiroFish NOT complete after ${waited}s — running BRAIN OPEN anyway (deadline fallback; open not missed)"
fi

"$RUNNER" unified_brain.py "BRAIN OPEN"; RC=$?
echo "$TODAY" > "$STATE/brain_open_done"
log "BRAIN OPEN done (rc=$RC)"
