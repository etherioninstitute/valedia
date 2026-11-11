"""Quant Engine — Cron Wrapper
Runs the LunarResearcher quant framework. Currently Kalshi only.
Polymarket adapter built but DISABLED.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quant_engine import run
run(bankroll=300, top_n=5)
