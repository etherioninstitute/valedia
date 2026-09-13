"""Kalshi Trade Executor — Cron Wrapper
Runs kalshi_executor in execute mode. Called by cron_runner_mac.sh.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kalshi_executor import run
print(run(dry_run=False, max_trades=5, min_score=40))
