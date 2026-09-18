#!/usr/bin/env python3
"""Slice 18 — MiroFish weight recommendation (local JSON, cap ≤10–15%).

No network, no HTTP Intent, no auto-raise, no permanent blacklist.
Run: python3 test_slice18.py
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mirofish_accuracy_ledger as mal
import pnl_tracker as pt
import test_slice13 as s13
import valedia_config as vc

NOW = s13.NOW


def test_hygiene():
    s13._restore()
    src = open(os.path.join(HERE, "mirofish_accuracy_ledger.py")).read()
    assert "def recommend_weight" in src
    assert "HARD_CEILING = 0.15" in src
    assert "import requests" not in src
    assert "VENUE_WRITER" not in src
    run_src = open(os.path.join(HERE, "mirofish_ledger_run.py")).read()
    assert "recommend_weight" in run_src
    assert vc.MIROFISH_AUTO_RAISE is False
    assert vc.SPOT_RECONCILE_HTTP is False


def test_unproven_recommends_10pct_does_not_apply():
    s13._restore()
    md = s13._tmpdir()
    s13._bind(md)
    s13._jsonl(os.path.join(md, "mirofish_accuracy_ledger.jsonl"),
               s13._fee_true_rows(5, 5))
    before_w = vc.MIROFISH_WEIGHT
    before_auto = vc.MIROFISH_AUTO_RAISE
    rec = mal.recommend_weight()
    assert rec["proven"] is False
    assert rec["recommended_weight"] == 0.10
    assert rec["runtime_weight"] == 0.10
    assert rec["applied"] is False
    assert rec["hard_ceiling"] == 0.15
    assert rec["learning"]["permanent_blacklist"] is False
    saved = json.load(open(os.path.join(md, "mirofish_weight_recommendation.json")))
    assert saved["recommended_weight"] == 0.10
    assert vc.MIROFISH_WEIGHT == before_w
    assert vc.MIROFISH_AUTO_RAISE is before_auto
    s13._restore()


def test_proven_recommends_15pct_runtime_stays_capped():
    s13._restore()
    md = s13._tmpdir()
    s13._bind(md)
    s13._jsonl(os.path.join(md, "mirofish_accuracy_ledger.jsonl"),
               s13._fee_true_rows(20, 12))  # 60% > 55%
    vc.MIROFISH_WEIGHT = 0.50  # would be reckless if applied
    rec = mal.recommend_weight()
    assert rec["proven"] is True
    assert rec["recommended_weight"] == 0.15
    assert rec["runtime_weight"] == 0.10  # AUTO_RAISE false
    assert rec["applied"] is False
    assert rec["action"] == "burton_may_set_MIROFISH_WEIGHT_or_AUTO_RAISE"
    assert vc.MIROFISH_WEIGHT == 0.50  # unchanged
    assert vc.MIROFISH_AUTO_RAISE is False
    assert vc.effective_mirofish_weight() == 0.10
    s13._restore()


def test_ceiling_holds_if_proven_cap_is_raised():
    s13._restore()
    md = s13._tmpdir()
    s13._bind(md)
    s13._jsonl(os.path.join(md, "mirofish_accuracy_ledger.jsonl"),
               s13._fee_true_rows(20, 20))
    vc.MIROFISH_PROVEN_CAP = 0.40
    rec = mal.recommend_weight()
    assert rec["recommended_weight"] == 0.15
    assert rec["hard_ceiling"] == 0.15
    s13._restore()


def test_learning_legacy_grave_is_not_paused():
    s13._restore()
    md = s13._tmpdir()
    s13._bind(md)
    bl = os.path.join(md, "strategy_blacklist.json")
    with open(bl, "w") as f:
        json.dump({"blacklisted": ["WEATHER_ARB"], "paused": [
            {"strategy": "LEGACY_GRAVE"}  # no until → not paused
        ], "probation": [
            {"strategy": "OIL_BRACKET",
             "until": (NOW + timedelta(days=2)).isoformat()}
        ]}, f)
    rec = mal.recommend_weight(pause_path=bl, now=NOW)
    learn = rec["learning"]
    assert learn["permanent_blacklist"] is False
    assert "LEGACY_GRAVE" not in (learn.get("paused") or [])
    assert "OIL_BRACKET" in (learn.get("probation") or [])
    assert "WEATHER_ARB" in learn["live_exec_fenced"]
    assert pt.is_strategy_paused("LEGACY_GRAVE", now=NOW, path=bl) is False
    s13._restore()


def test_flags_untouched():
    s13._restore()
    before = s13._flags()
    md = s13._tmpdir()
    s13._bind(md)
    mal.recommend_weight()
    assert s13._flags() == before
    s13._restore()


def run():
    s13._restore()
    try:
        test_hygiene()
        test_unproven_recommends_10pct_does_not_apply()
        test_proven_recommends_15pct_runtime_stays_capped()
        test_ceiling_holds_if_proven_cap_is_raised()
        test_learning_legacy_grave_is_not_paused()
        test_flags_untouched()
    finally:
        s13._restore()
    print("slice18 checks: PASS")


if __name__ == "__main__":
    run()
