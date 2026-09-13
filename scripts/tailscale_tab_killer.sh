#!/bin/bash
# Tailscale Tab Killer v4 — ONLY kills auth/login popups, NOT intentional visits
# Only targets: login.tailscale.com and controlplane.tailscale.com
# Leaves tailscale.com (the main site) alone

LOG="/Users/burtonchatelain/.openclaw/logs/tailscale_tabs.log"

while true; do
    RESULT=$(osascript -e '
    set killed to ""
    tell application "Safari"
        if it is running then
            repeat with w in (every window)
                set tabCount to count of tabs of w
                repeat with i from tabCount to 1 by -1
                    try
                        set t to tab i of w
                        set tabURL to URL of t
                        if tabURL contains "login.tailscale" or tabURL contains "controlplane.tailscale" or tabURL contains "/auth/" then
                            set killed to killed & tabURL & "\n"
                            close t
                        end if
                    end try
                end repeat
                try
                    if (count of tabs of w) is 0 then close w
                end try
            end repeat
        end if
    end tell
    return killed
    ' 2>/dev/null)

    if [ -n "$RESULT" ] && [ "$RESULT" != "" ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') KILLED: $RESULT" >> "$LOG"
    fi
    sleep 3
done
