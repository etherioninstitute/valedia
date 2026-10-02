#!/bin/bash
# Abundance Agent — Master Cron Runner (VPS LEGACY)
# Sources env, runs the specified script, logs its output, pings Signal.
#
# LEGACY PATH — NEVER FORWARDS SCRIPT OUTPUT TO SIGNAL. Raw output cannot be
# verified against broker/ledger, so it stays in the cron log only
# (stdout + ${CRON_LOG:-/root/.openclaw/logs/valedia-cron.log}). Signal gets ONE
# fixed, code-built line with no figures:
#   "Valedia job <LABEL> finished OK"   or   "Valedia job <LABEL> FAILED (see cron log)"
# <LABEL> is shown only if it is one of the known install_cron.sh labels (allowlist).
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

# Label ALLOWLIST = the labels of the active jobs in scripts/install_cron.sh
# (test_signal_scrub.py asserts this list equals that file's set). Any other label
# is dropped: the line then reads "Valedia job finished OK" with no label.
KNOWN_LABELS="ASI SCAN|BRAIN CLOSE|BRAIN CRYPTO|BRAIN MIDDAY|DCA|DISCOVER|EOD PORTFOLIO|FUND CHECK|HEDGE INTEL|INSIDER|INTEL+SENTIMENT|INTEL REFRESH|KALSHI AUDIT|LATENCY ARB|LEDGER RECONCILE|MARKET SCANNER|MASTER ALLOCATOR|ML RETRAIN|MORNING BRIEF|P&L TRACKER|QUANT ENGINE|REBALANCE|SELF-EVOLVE WEEKLY|SETTLEMENT|SHADOW PARITY|SPORTS ENGINE|WHALE MONITOR|WATCHDOG"
JOB="Valedia job"
IFS='|' read -r -a _KNOWN <<< "$KNOWN_LABELS"
for _k in "${_KNOWN[@]}"; do
    [ "$LABEL" = "$_k" ] && JOB="Valedia job $_k"
done
if [ "$RC" -eq 0 ]; then
    MSG="$JOB finished OK"
else
    MSG="$JOB FAILED (see cron log)"
fi

# Send to Signal (fixed line only)
openclaw message send --channel signal --target "$TARGET" --message "$MSG" 2>&1 | tail -1
