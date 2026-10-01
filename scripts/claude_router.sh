#!/bin/bash
# Valedia Claude Router — runs on VPS, calls NIMROD
# Called by OpenClaw when Valedia needs Claude-grade intelligence
# Uses Burton's Claude Max subscription — ZERO API cost
#
# Usage: claude_router.sh "question here" [sonnet|opus] [--signal]
#
# Signal relay is GATED: claude_bridge.py prints only signal_verify-gated text
# (last line "✓ verified: ..." or a single "Report withheld (...)" line). This
# router relays to Signal ONLY text carrying that gate marker; anything else
# (old bridge copy, errors, raw AI text) is replaced by a withheld notice with
# no figures. stderr from the bridge is dropped so noise never enters the relay.

QUESTION="${1:-What is my portfolio status?}"
MODEL="${2:-sonnet}"
SIGNAL="${3:-}"
NIMROD="burtonchatelain@100.125.9.30"
BRIDGE="/Users/burtonchatelain/.openclaw/workspace/scripts/integrations/claude_bridge.py"

# SSH to NIMROD, run claude_bridge.py with live data
RESPONSE=$(ssh -o ConnectTimeout=15 -o ServerAliveInterval=10 "$NIMROD" \
    "source ~/.bashrc 2>/dev/null; export PATH=\$HOME/.npm-global/bin:/usr/local/bin:\$PATH; cd ~/.openclaw/workspace/scripts/integrations && python3 claude_bridge.py '${QUESTION//\'/\\\'}' --model $MODEL 2>/dev/null" \
    2>/dev/null)

if [ -z "$RESPONSE" ]; then
    RESPONSE="Claude bridge failed — NIMROD may be unreachable or Claude Code timed out."
fi

echo "$RESPONSE"

# Optionally send via Signal (only if the bridge output carries the verify gate)
if [ "$SIGNAL" = "--signal" ]; then
    FIRST=$(printf '%s\n' "$RESPONSE" | sed -n '1p')
    LAST=$(printf '%s\n' "$RESPONSE" | sed '/^[[:space:]]*$/d' | tail -n 1)
    case "$FIRST" in
        "Report withheld ("*) [ "$(printf '%s\n' "$RESPONSE" | wc -l)" -eq 1 ] && GATED=1 || GATED=0 ;;
        *) case "$LAST" in "✓ verified"*) GATED=1 ;; *) GATED=0 ;; esac ;;
    esac
    if [ "$GATED" = "1" ]; then
        OUT="$RESPONSE"
    else
        OUT="Report withheld (claude router): bridge output was not signal_verify-gated. Unverified figures not sent; see data/signal/verify_log.jsonl."
    fi
    SAFE=$(echo "$OUT" | head -c 1800 | sed "s/'/\\\\'/g" | sed 's/\$/USD /g')
    openclaw message send --channel signal \
        --target "uuid:47bd6dd5-2c96-4514-9541-acd8ed013612" \
        --message "$SAFE" 2>/dev/null
fi
