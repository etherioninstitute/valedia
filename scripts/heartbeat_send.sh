#!/bin/bash
# Valedia Heartbeat v2 — Mac Studio → VPS ping every 5 min
# Uses direct VPS IP + SSH key (same as SOCKS tunnel — no Tailscale auth needed)
# VPS activates ONLY after 3 CONSECUTIVE failed pings (not a single glitch)
# (c) GOATWORTHY Post LLC

VPS_IP="85.239.234.32"          # Direct IP — no Tailscale auth needed
SSH_KEY="$HOME/.ssh/openclaw_vps"
HEARTBEAT_LOG="/tmp/valedia_heartbeat"
FAIL_COUNT_FILE="/tmp/valedia_heartbeat_fails"

TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
PAYLOAD="{\"source\":\"mac-studio\",\"ts\":\"$TIMESTAMP\",\"status\":\"alive\"}"

ssh -i "$SSH_KEY" \
    -o ConnectTimeout=5 \
    -o StrictHostKeyChecking=no \
    -o BatchMode=yes \
    -o PasswordAuthentication=no \
    root@$VPS_IP \
    "echo '$PAYLOAD' > /tmp/valedia_mac_heartbeat && echo '{\"consecutive_fails\":0}' > /tmp/valedia_fail_count" \
    2>/dev/null

SSH_EXIT=$?

if [ $SSH_EXIT -eq 0 ]; then
    # ── SUCCESS: reset consecutive fail counter ──────────────────────
    echo "0" > "$FAIL_COUNT_FILE"
    echo "$TIMESTAMP OK" >> "$HEARTBEAT_LOG"
else
    # ── FAILURE: increment counter ───────────────────────────────────
    PREV=$(cat "$FAIL_COUNT_FILE" 2>/dev/null || echo "0")
    NEW=$((PREV + 1))
    echo "$NEW" > "$FAIL_COUNT_FILE"
    echo "$TIMESTAMP FAIL #$NEW" >> "$HEARTBEAT_LOG"

    if   [ "$NEW" -eq 1 ]; then
        echo "$TIMESTAMP  → Glitch? Will retry next cycle before alerting" >> "$HEARTBEAT_LOG"
    elif [ "$NEW" -eq 2 ]; then
        echo "$TIMESTAMP  → 2nd failure — one more before VPS takes over" >> "$HEARTBEAT_LOG"
    elif [ "$NEW" -ge 3 ]; then
        echo "$TIMESTAMP  → 3 consecutive failures — VPS is activating" >> "$HEARTBEAT_LOG"
    fi
fi

# Keep log to last 200 lines
tail -200 "$HEARTBEAT_LOG" > "$HEARTBEAT_LOG.tmp" && mv "$HEARTBEAT_LOG.tmp" "$HEARTBEAT_LOG"
