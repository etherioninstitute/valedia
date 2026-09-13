#!/bin/bash
# Null browser — catches Tailscale auth redirects, logs them, does nothing
echo "$(date '+%Y-%m-%d %H:%M:%S') BLOCKED: $@" >> /Users/burtonchatelain/.openclaw/logs/blocked_urls.log
exit 0
