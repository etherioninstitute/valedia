"""Master Capital Allocator — Cron Wrapper
Scans ALL platforms, ranks ALL opportunities, deploys capital to highest EV.
Replaces individual kalshi_trade.py and weather_trade.py.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from master_allocator import run
print(run(dry_run=False, max_allocs=15))
