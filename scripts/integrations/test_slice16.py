#!/usr/bin/env python3
"""Slice 16 — shadow parity ops (report-only CLI/cron, mocked queue).

No network, no keys, no nautilus_trader. Does not flip flags.
Run: python3 test_slice16.py
"""
import json
import os
import sys
import tempfile
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import nautilus_shadow_parity as sp
import parity_run
import test_slice12 as s12
import valedia_config as vc

NOW = s12.NOW


def test_hygiene():
    s12._restore()
    src = open(os.path.join(HERE, "parity_run.py")).read()
    assert "import requests" not in src
    assert "import nautilus_trader" not in src
    assert "VENUE_WRITER[" not in src
    assert "SPOT_RECONCILE_HTTP =" not in src
    assert "REBALANCER_AUTOEXEC =" not in src
    assert "NAUTILUS_LIVE =" not in src
    assert "raise SystemExit(0)" in src
    assert sp.DEFAULT_REPORT.endswith("shadow_parity_report.json")
    s12._assert_fence()


def test_ops_writes_report_four_streams():
    s12._restore()
    qpath = s12._queue()
    fd, out = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        rows = [
            s12._stamp(s12._emit_brain(qpath), NOW - timedelta(days=1)),
            s12._stamp(s12._emit_hl(qpath), NOW - timedelta(days=1)),
            s12._stamp(s12._emit_pm(qpath), NOW - timedelta(days=1)),
            s12._stamp(s12._emit_intel_cb(qpath), NOW - timedelta(days=1)),
        ]
        # rewrite queue with stamped rows
        with open(qpath, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        before = s12._flags()
        os.environ["SHADOW_PARITY_WINDOW"] = "7d"
        try:
            rep = parity_run.run(queue_path=qpath, out_path=out, now=NOW)
        finally:
            os.environ.pop("SHADOW_PARITY_WINDOW", None)
        saved = json.load(open(out))
        assert saved["ok"] is True
        assert saved["n_in_window"] == 4
        for b in ("brain_coinbase", "hl_short", "polymarket_arb", "intel_coinbase"):
            assert saved["buckets"][b]["n"] == 1
            assert saved["buckets"][b]["compare_ok"] == 1
        assert saved["cutover_ready"]["coinbase"] is False  # need 7 days
        assert saved["flags"]["NAUTILUS_LIVE"] is False
        assert saved["flags"]["SPOT_RECONCILE_HTTP"] is False
        assert rep["ok"] is True
        s12._assert_fence(before)
    finally:
        os.remove(qpath)
        os.remove(out)


def test_mismatch_ledger_cron_still_exits_zero():
    s12._restore()
    qpath = s12._queue()
    fd, out = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        rec = s12._stamp(s12._emit_brain(qpath), NOW)
        rec["instrument"] = "ETH-USD"  # vs BTC-USD post
        rec["canonical_post"]["product_id"] = "BTC-USD"
        with open(qpath, "w") as f:
            f.write(json.dumps(rec) + "\n")
        before = s12._flags()
        rep = parity_run.run(queue_path=qpath, out_path=out, now=NOW)
        saved = json.load(open(out))
        assert saved["ok"] is False
        assert saved["dangerous"]
        assert any(d.get("category") in ("instrument", "side", "size") or
                   "instrument" in str(d.get("field"))
                   for d in saved["dangerous"])
        # wrapper always exits 0; this function must not flip flags
        s12._assert_fence(before)
        assert vc.VENUE_WRITER["coinbase"] == "valedia"
        assert rep["ok"] is False
    finally:
        os.remove(qpath)
        os.remove(out)


def test_cli_main_default_writes():
    s12._restore()
    qpath = s12._queue()
    fd, out = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        rec = s12._stamp(s12._emit_brain(qpath), NOW)
        with open(qpath, "w") as f:
            f.write(json.dumps(rec) + "\n")
        before = s12._flags()
        rc = sp.main(["--queue", qpath, "--window", "7d", "--now", NOW.isoformat(),
                      "--out", out])
        assert rc == 0
        saved = json.load(open(out))
        assert saved["ok"] is True
        assert saved["buckets"]["brain_coinbase"]["n"] == 1
        s12._assert_fence(before)
    finally:
        os.remove(qpath)
        os.remove(out)


def test_allocator_still_ignored():
    s12._restore()
    qpath = s12._queue()
    fd, out = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        rec = s12._stamp(s12._emit_brain(qpath), NOW)
        rec["allocator_call"] = {"product_id": rec.get("instrument")}
        rec["kind"] = "allocator"
        with open(qpath, "w") as f:
            f.write(json.dumps(rec) + "\n")
        rep = parity_run.run(queue_path=qpath, out_path=out, now=NOW)
        assert rep["ignored"].get("allocator_coinbase", 0) == 1
        assert "allocator_coinbase" not in sp.EVIDENCE
        assert rep["cutover_ready"]["coinbase"] is False
        s12._assert_fence()
    finally:
        os.remove(qpath)
        os.remove(out)


def run():
    s12._restore()
    try:
        test_hygiene()
        test_ops_writes_report_four_streams()
        test_mismatch_ledger_cron_still_exits_zero()
        test_cli_main_default_writes()
        test_allocator_still_ignored()
    finally:
        s12._restore()
    print("slice16 checks: PASS")


if __name__ == "__main__":
    run()
