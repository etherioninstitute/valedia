#!/bin/bash
# Valedia Claude Router — runs on VPS, calls NIMROD
# Called by OpenClaw when Valedia needs Claude-grade intelligence
# Uses Burton's Claude Max subscription — ZERO API cost
#
# Usage: claude_router.sh "question here" [sonnet|opus] [--signal]
#
# INJECTION-SAFE: the question is never interpolated into a shell string. It is
# base64-encoded (alphabet A-Z a-z 0-9 + / =) and passed as ONE argv word
# (--question-b64), so quotes, apostrophes, $( ), backticks and ; are inert.
# MODEL is whitelisted (sonnet|opus).
#
# Signal relay is STRUCTURALLY GATED (text markers alone are never trusted).
# claude_bridge.py --router prints line 1 "VALEDIA-GATE <status> <sha256>" then
# the gated text, and exits 0 (verified) / 3 (withheld) / 4 (other).
# The router relays to Signal ONLY when ALL hold:
#   - exit 0 with status "verified" and last line "✓ figures from broker/ledger
#     only", OR exit 3 with status "withheld" and a single "Report withheld (" line;
#   - the sha256 in the header equals the sha256 of the text that follows.
# Anything else (old bridge, raw AI text, errors, spoofed markers) is replaced by
# a figure-free withheld notice. Bridge stderr is dropped.

QUESTION="${1:-What is my portfolio status?}"
MODEL="${2:-sonnet}"
SIGNAL="${3:-}"
NIMROD="burtonchatelain@100.125.9.30"
BRIDGE="/Users/burtonchatelain/.openclaw/workspace/scripts/integrations/claude_bridge.py"

case "$MODEL" in sonnet|opus) ;; *) MODEL="sonnet" ;; esac
QB64=$(printf '%s' "$QUESTION" | base64 | tr -d '\n\r ')
case "$QB64" in *[!A-Za-z0-9+/=]*) QB64="" ;; esac

# SSH to NIMROD, run claude_bridge.py with live data (exit code propagates via ssh)
RAW=$(ssh -o ConnectTimeout=15 -o ServerAliveInterval=10 "$NIMROD" \
    "source ~/.bashrc 2>/dev/null; export PATH=\$HOME/.npm-global/bin:/usr/local/bin:\$PATH; cd ~/.openclaw/workspace/scripts/integrations && python3 claude_bridge.py --question-b64 '$QB64' --model $MODEL --router 2>/dev/null" \
    2>/dev/null)
RC=$?

HEADER=$(printf '%s\n' "$RAW" | sed -n '1p')
read -r TAG STATUS DIGEST EXTRA <<< "$HEADER"
if [ "$TAG" = "VALEDIA-GATE" ]; then
    BODY=$(printf '%s\n' "$RAW" | sed '1d')
else
    BODY="$RAW"
fi
[ -z "$BODY" ] && BODY="Claude bridge failed — NIMROD may be unreachable or Claude Code timed out."

echo "$BODY"

_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum | cut -d' ' -f1
    else shasum -a 256 | cut -d' ' -f1; fi
}

# Optionally send via Signal (only structurally-verified bridge output)
if [ "$SIGNAL" = "--signal" ]; then
    GATED=0
    if [ "$TAG" = "VALEDIA-GATE" ] && [ -z "$EXTRA" ] && [ -n "$DIGEST" ] \
       && [ "$(printf '%s' "$BODY" | _sha256)" = "$DIGEST" ]; then
        NLINES=$(printf '%s\n' "$BODY" | wc -l | tr -d ' ')
        LAST=$(printf '%s\n' "$BODY" | tail -n 1)
        if [ "$RC" -eq 0 ] && [ "$STATUS" = "verified" ]; then
            case "$LAST" in "✓ figures from broker/ledger only") GATED=1 ;; esac
        elif [ "$RC" -eq 3 ] && [ "$STATUS" = "withheld" ] && [ "$NLINES" -eq 1 ]; then
            case "$BODY" in "Report withheld ("*) GATED=1 ;; esac
        fi
    fi
    if [ "$GATED" = "1" ]; then
        OUT="$BODY"
    else
        OUT="Report withheld (claude router): bridge output was not signal_verify-gated. Unverified figures not sent; see data/signal/verify_log.jsonl."
    fi
    # --message is a single argv word (no shell re-parse): apostrophes need no escaping.
    SAFE=$(printf '%s\n' "$OUT" | head -c 1800 | sed 's/\$/USD /g')
    openclaw message send --channel signal \
        --target "uuid:47bd6dd5-2c96-4514-9541-acd8ed013612" \
        --message "$SAFE" 2>/dev/null
fi
