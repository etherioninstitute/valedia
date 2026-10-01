#!/bin/bash
# Abundance Agent — Master Cron Runner (VPS LEGACY)
# Sources env, runs the specified script, logs its output, pings Signal.
#
# LEGACY PATH — NEVER FORWARDS SCRIPT OUTPUT TO SIGNAL. Raw output cannot be
# verified against broker/ledger, so it stays in the cron log only
# (stdout + ${CRON_LOG:-/root/.openclaw/logs/valedia-cron.log}). Signal gets ONE
# fixed, code-built line with no figures:
#   "Valedia job <LABEL> finished OK"   or   "Valedia job <LABEL> FAILED (see cron log)"
# <LABEL> is reduced to letters, spaces, '_' and '-' (no digits, no symbols).
# Money reports go through signal_verify (pnl_run.py, settle_run.py,
# kalshi_alert.py, sports_engine.py) — never through this runner.

set -a
[ -f "${OPENCLAW_ENV:-/root/.openclaw/.env}" ] && source "${OPENCLAW_ENV:-/root/.openclaw/.env}"
set +a

SCRIPT="$1"
LABEL="$2"
TARGET="uuid:47bd6dd5-2c96-4514-9541-acd8ed013612"
LOG="${CRON_LOG:-/root/.openclaw/logs/valedia-cron.log}"

# Run script and capture output (log only)
OUTPUT=$(python3 "$SCRIPT" 2>&1)
RC=$?
echo "$OUTPUT"
{ mkdir -p "$(dirname "$LOG")" && printf '[%s] %s rc=%s\n%s\n---\n' \
    "$(date -u '+%Y-%m-%d %H:%M UTC')" "$LABEL" "$RC" "$OUTPUT" >> "$LOG"; } 2>/dev/null || true

SAFE_LABEL=$(printf '%s' "$LABEL" | LC_ALL=C tr -cd 'A-Za-z _-' | head -c 40)
[ -z "$SAFE_LABEL" ] && SAFE_LABEL="job"
if [ "$RC" -eq 0 ]; then
    MSG="Valedia job $SAFE_LABEL finished OK"
else
    MSG="Valedia job $SAFE_LABEL FAILED (see cron log)"
fi

# Send to Signal (fixed line only)
openclaw message send --channel signal --target "$TARGET" --message "$MSG" 2>&1 | tail -1
