"""Kalshi Opportunity Alert — Cron Wrapper
Scans all platforms, generates profit report, sends to Signal if hot.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kalshi_executor import send_opportunity_alert
print(send_opportunity_alert())
