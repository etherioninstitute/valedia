#!/bin/bash
# Abundance Agent — Master Cron Runner (VPS LEGACY)
# Sources env, runs the specified script, sends result via Signal.
#
# LEGACY / UNVERIFIED PATH: this runner relays RAW script output, which cannot be
# checked against broker/ledger. It therefore REFUSES to send money figures:
# if the output contains any ($N, N.NN) it sends only a withheld notice (no
# figures) and logs to data/signal/verify_log.jsonl. Money reports go through
# signal_verify (pnl_run.py, settle_run.py, kalshi_alert.py, sports_engine.py).
# Figure-free ops text is still relayed as before.

set -a
[ -f "${OPENCLAW_ENV:-/root/.openclaw/.env}" ] && source "${OPENCLAW_ENV:-/root/.openclaw/.env}"
set +a

SCRIPT="$1"
LABEL="$2"
TARGET="uuid:47bd6dd5-2c96-4514-9541-acd8ed013612"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Run script and capture output
OUTPUT=$(python3 "$SCRIPT" 2>&1)

# Money guard: $ amounts, "USD 12", or any 2-decimal figure (version stamps and
# dotted ids like v0.74.1 / 100.125.9.30 are not matched).
MONEY_RE='(\$ *[0-9])|(USD *[0-9])|((^|[^0-9.])[+-]?[0-9][0-9,]*\.[0-9]{2}([^0-9.]|$))'
if printf '%s\n' "$OUTPUT" | grep -Eq "$MONEY_RE"; then
    echo "$OUTPUT"   # raw output stays in the cron log only
    SIGDIR="${VALEDIA_SIGNAL_DIR:-$HERE/../data/signal}"
    mkdir -p "$SIGDIR" 2>/dev/null
    LABEL="$LABEL" SCRIPT="$SCRIPT" python3 -c '
import json, os, sys, datetime
rec = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
       "kind": "cron_runner legacy", "status": "withheld", "sent": False,
       "problems": [{"code": "legacy cron_runner.sh output carries money figures (unverifiable)",
                     "detail": os.environ.get("SCRIPT", "")}],
       "label": os.environ.get("LABEL", "")}
sys.stdout.write(json.dumps(rec) + "\n")' >> "$SIGDIR/verify_log.jsonl" 2>/dev/null || true
    OUTPUT="Report withheld (legacy cron_runner): script output carries money figures this path cannot verify. Unverified figures not sent; see data/signal/verify_log.jsonl."
fi

# Send to Signal
openclaw message send --channel signal --target "$TARGET" --message "[$LABEL] $OUTPUT" 2>&1 | tail -1
