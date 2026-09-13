"""Kalshi Audit — Cron Wrapper
Nightly review of trade performance. Adjusts thresholds.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kalshi_executor import audit
print(audit())
