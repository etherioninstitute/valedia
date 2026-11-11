#!/usr/bin/env python3
"""ASI Full Scan — Run by cron, sends results to Signal via cron_runner.sh"""
import os, sys
sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")

# Force IPv4
import socket
_orig = socket.getaddrinfo
def _v4(*a, **k):
    r = _orig(*a, **k)
    return [x for x in r if x[0] == socket.AF_INET] or r
socket.getaddrinfo = _v4

from kelly_sizing import full_asi_report

if __name__ == "__main__":
    try:
        from mercury_client import get_balances
        bals = get_balances()
        capital = 80.78
        for b in bals:
            if b.get("kind") == "checking":
                capital = b.get("availableBalance", 80.78)
                break
    except:
        capital = 80.78
    
    print(full_asi_report(capital))
