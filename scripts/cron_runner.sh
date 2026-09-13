#!/bin/bash
# Abundance Agent — Master Cron Runner
# Sources env, runs the specified script, sends result via Signal

set -a
source /root/.openclaw/.env
set +a

SCRIPT="$1"
LABEL="$2"
TARGET="uuid:47bd6dd5-2c96-4514-9541-acd8ed013612"

# Run script and capture output
OUTPUT=$(python3 "$SCRIPT" 2>&1)

# Send to Signal
openclaw message send --channel signal --target "$TARGET" --message "[$LABEL] $OUTPUT" 2>&1 | tail -1
