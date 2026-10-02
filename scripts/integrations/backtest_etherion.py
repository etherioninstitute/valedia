"""Retired: the network-fetching ERE v2 backtest was removed.
The ERE v3 offline research harness is validate_ere.py; the runtime is
ere_runtime.py. This file is kept as a tombstone so old imports fail loudly
rather than silently fetching yfinance data.
"""
if __name__ == "__main__":
    raise SystemExit("Retired: run validate_ere.py for offline ERE v3 experiments")