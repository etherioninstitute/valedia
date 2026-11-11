#!/usr/bin/env python3
"""Cron-optional PAPER fight entry. Not installed to crontab this session.

Always exit 0 so a later cron self-heal does not retry a report-only job.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adversarial_paper_fight as apf


def run():
    doc = apf.run_round()
    print(json.dumps({
        "path": doc.get("path"),
        "n": doc.get("n"),
        "mode": "PAPER/SHADOW",
        "no_orders": True,
        "no_ln": True,
        "note": "report-only; not crontab-installed",
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
