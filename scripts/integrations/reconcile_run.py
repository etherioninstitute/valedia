#!/usr/bin/env python3
"""Cron entry: settle the forward-test ledger against REAL fills.

cron_runner_mac.sh invokes `python3 <script>` with no argv, but
forward_test_ledger's CLI requires a subcommand — so this wraps reconcile().
Idempotent (already-settled entries are skipped); safe to run often.
(c) GOATWORTHY Post LLC.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import forward_test_ledger as ftl

if __name__ == "__main__":
    sys.exit(ftl.reconcile())
