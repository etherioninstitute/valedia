"""Weather Arbitrage — Cron Wrapper
NOAA forecast vs Kalshi weather brackets. Runs every 4 hours.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from weather_arb import run
print(run(dry_run=False, max_trades=5))
