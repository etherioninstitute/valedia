#!/bin/bash
# Valedia Claude Router — runs on VPS, calls NIMROD
# Called by OpenClaw when Valedia needs Claude-grade intelligence
# Uses Burton's Claude Max subscription — ZERO API cost
#
# Usage: claude_router.sh "question here" [sonnet|opus] [--signal]

QUESTION="${1:-What is my portfolio status?}"
MODEL="${2:-sonnet}"
SIGNAL="${3:-}"
NIMROD="burtonchatelain@100.125.9.30"
BRIDGE="/Users/burtonchatelain/.openclaw/workspace/scripts/integrations/claude_bridge.py"

# SSH to NIMROD, run claude_bridge.py with live data
RESPONSE=$(ssh -o ConnectTimeout=15 -o ServerAliveInterval=10 "$NIMROD" \
    "source ~/.bashrc 2>/dev/null; export PATH=\$HOME/.npm-global/bin:/usr/local/bin:\$PATH; cd ~/.openclaw/workspace/scripts/integrations && python3 claude_bridge.py '${QUESTION//\'/\\\'}' --model $MODEL 2>&1" \
    2>/dev/null)

if [ -z "$RESPONSE" ]; then
    RESPONSE="Claude bridge failed — NIMROD may be unreachable or Claude Code timed out."
fi

echo "$RESPONSE"

# Optionally send via Signal
if [ "$SIGNAL" = "--signal" ]; then
    SAFE=$(echo "$RESPONSE" | head -c 1400 | sed "s/'/\\\\'/g" | sed 's/\$/USD /g')
    openclaw message send --channel signal \
        --target "uuid:47bd6dd5-2c96-4514-9541-acd8ed013612" \
        --message "$SAFE" 2>/dev/null
fi
