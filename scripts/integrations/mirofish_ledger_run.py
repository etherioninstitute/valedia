#!/usr/bin/env python3
"""Cron entry: keep the MiroFish accuracy ledger current so MiroFish EARNS its fusion weight.

cron_runner passes no argv, so this wraps the ledger CLI: score any matured predictions
against realized price moves, then record today's REAL-sim predictions (record() itself
skips lightweight/fallback signals — only real swarm output counts). Runs after the morning
swarm+report have produced a real mirofish_signal.json.
(c) GOATWORTHY Post LLC.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mirofish_accuracy_ledger as m

if __name__ == "__main__":
    try:
        m.score()      # score matured predictions vs fee-true FTL settlements
    except Exception as e:
        print(f"[mirofish_ledger] score failed: {e}")
    try:
        m.record()     # log today's real-sim predictions (no-op on lightweight signals)
    except Exception as e:
        print(f"[mirofish_ledger] record failed: {e}")
    m.summary()        # writes mirofish_accuracy_summary.json for intel/fusion
