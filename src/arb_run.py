"""Latency Arb — Cron Wrapper
Runs the latency arb engine in dry-run mode for monitoring.
Switch to --mode execute when ready to go live.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from latency_arb import run, https_session
# Pin CA store on this (main) thread before run() starts the poller.
https_session()
# Dry run for 15 min each cycle (cron fires every 30 min)
print(run(mode='dry-run', duration_min=15))
