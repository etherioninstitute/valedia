"""P&L Tracker — Cron Wrapper (11pm)
Tracks Kalshi settlements, writes learning pauses (TTL/evidence, not graves),
runs local AI audit (log only), then sends Burton the verified money ping:
  Invested = Kalshi open-position cost (broker, re-read to verify)
  Profit   = Realized P&L from forward_test_ledger (fee-incl., re-read to verify)
Any mismatch / stale source -> "Report withheld" instead (signal_verify).
WR flags are report-only and do not feed Kelly.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from pnl_tracker import run
    print(run())  # full tracking + local log (verbose, not sent)
except Exception as e:
    print(f"[pnl_run] tracker failed (report still verified from broker+ledger): {e}")

try:
    import signal_verify
    res = signal_verify.pnl_report()
    print(f"[pnl_run] Signal {res['status']} sent={res['sent']}: {res['text']}")
except Exception as e:
    print(f"[pnl_run] Signal report failed: {e}")
