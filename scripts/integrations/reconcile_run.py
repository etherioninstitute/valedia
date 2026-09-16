#!/usr/bin/env python3
"""Cron entry: settle the forward-test ledger against REAL fills.

cron_runner_mac.sh invokes `python3 <script>` with no argv, but
forward_test_ledger's CLI requires a subcommand — so this wraps reconcile().

Slice 5: also walks UNRECONCILED Coinbase/Alpaca rows via spot_reconcile
with an **injected** getter. Default getter is a mock (returns None, no HTTP).
Valedia runtime supplies a real GET later via set_spot_order_getter().

Never live venue HTTP on the default/test path. No nautilus_trader. No --live.
Idempotent (already-settled entries are skipped); safe to run often.
(c) GOATWORTHY Post LLC.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import forward_test_ledger as ftl
import spot_reconcile as sr

# ── injected getter hook ──────────────────────────────────────────────────
# Default/tests: mock (no HTTP). Valedia runtime:
#   import reconcile_run as rr
#   rr.set_spot_order_getter(real_get)   # real_get(order_id, venue) -> dict|None
# This lane never installs a live GET.

_spot_order_getter = None  # None → mock_spot_order_getter


def mock_spot_order_getter(order_id, venue):
    """Shipped default. Never network. Incomplete → stay UNRECONCILED."""
    return None


def set_spot_order_getter(fn):
    """Valedia runtime hook. Pass a callable(order_id, venue) or None (restore mock)."""
    global _spot_order_getter
    _spot_order_getter = fn


def get_spot_order_getter():
    """Active getter. Default is mock_spot_order_getter (no HTTP)."""
    return _spot_order_getter if _spot_order_getter is not None else mock_spot_order_getter


def reconcile_unreconciled_run(getter=None, rows=None):
    """Scan UNRECONCILED ledger rows; upgrade only when getter/payload is complete.

    getter: explicit override. If omitted, uses get_spot_order_getter() (mock
    unless Valedia runtime injected one). Never HTTP unless that callable does.
    """
    g = getter if getter is not None else get_spot_order_getter()
    return sr.reconcile_unreconciled(getter=g, rows=rows)


def run(getter=None, kalshi=True):
    """Cron body. Spot UNRECONCILED first (injected getter), then existing Kalshi settle.

    Tests pass kalshi=False so this file never opens a venue socket.
    """
    rec = reconcile_unreconciled_run(getter=getter)
    print(f"spot unreconciled: n={rec.get('n', 0)} upgraded={rec.get('upgraded', 0)}")
    if not kalshi:
        return 0 if rec is not None else 1
    return ftl.reconcile()


if __name__ == "__main__":
    sys.exit(run())
