"""P&L Tracker — Cron Wrapper (11pm)
Tracks Kalshi settlements, auto-kills losing strategies, runs local AI audit,
then sends Burton a simple, accurate P&L + invested message via Signal.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pnl_tracker import run, load_pnl, simple_pnl_message

print(run())  # full tracking + local log (verbose)

# Send Burton the simple, real (broker-sourced) daily P&L message.
try:
    import signal_send
    signal_send.send(simple_pnl_message(load_pnl().get("summary", {})))
except Exception as e:
    print(f"[pnl_run] Signal send failed: {e}")
