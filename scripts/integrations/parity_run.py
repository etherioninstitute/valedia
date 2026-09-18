#!/usr/bin/env python3
"""Cron entry: report-only shadow parity (Slice 16).

cron_runner passes no argv. Compares brain Coinbase + HL short + Polymarket
arb + intel Coinbase shadow Intents vs Valedia POST bodies for a window
(default 7d, override SHADOW_PARITY_WINDOW). Writes
models/shadow_parity_report.json. Never POSTs. Never mutates flags.

How to read the JSON:
  ok            no dangerous mismatches in the window
  diffs         per-row compare mismatches (advisory + stored)
  dangerous     fail-closed (side/instrument/size/venue/signed/hook/
                leverage/dispatchable/live_flag/writer)
  buckets       n / compare_ok / dangerous / days_ok per stream
  ignored       allocator Coinbase is not cutover evidence
  cutover_ready 7 calendar days of matching evidence; report-only,
                does not enable NT / writer / GET HTTP / AUTOEXEC
  flags         snapshot; must stay NAUTILUS_LIVE false, writers valedia|none

CLI: python3 nautilus_shadow_parity.py --window 7d --queue PATH --out PATH
Always exit 0 so cron self-heal does not retry a report-only job.
(c) GOATWORTHY Post LLC.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nautilus_shadow_parity as sp


def run(queue_path=None, window=None, out_path=None, now=None):
    win = window or os.environ.get("SHADOW_PARITY_WINDOW") or sp.WINDOW_DEFAULT
    out = out_path or sp.DEFAULT_REPORT
    rep = sp.report(queue_path=queue_path, window=win, now=now, out_path=out)
    print(json.dumps({
        "ok": rep["ok"],
        "n_in_window": rep["n_in_window"],
        "n_dangerous": len(rep["dangerous"]),
        "cutover_ready": rep["cutover_ready"],
        "buckets": {k: v for k, v in (rep.get("buckets") or {}).items()
                    if k in sp.EVIDENCE or (v or {}).get("n")},
        "out": out,
        "flags": rep["flags"],
        "note": "report-only; cutover_ready does not enable anything",
    }, indent=2, default=str))
    return rep


if __name__ == "__main__":
    run()
    raise SystemExit(0)
