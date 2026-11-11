"""Sports Engine — Cron Wrapper
70-85¢ sports favorites. Size from SPORTS category_stats (unproven throttle).
Cron: every 2h at minute SPORTS_CRON_MINUTE (:00). Allocator is :20 every 4h
so the two cannot double-bet the same game on the 4h boundary (Slice 7).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sports_engine import run
print(run(dry_run=False))
