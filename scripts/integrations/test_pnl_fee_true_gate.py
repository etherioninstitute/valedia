#!/usr/bin/env python3
"""Only fee-true category_stats may pause/unpause a category; fee-blind
(pnl_tracker.json profit-cost) is report-only. No network, no keys, no --live.
Run: python3 test_pnl_fee_true_gate.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import forward_test_ledger as ftl
import pnl_tracker as pt

NOW = datetime(2026, 9, 17, 15, 0, tzinfo=timezone.utc)
# Fee-blind net -12 over 5 trades (>= min trades): paused under the old gap-fill.
BLIND = {"X": {"wins": 0, "losses": 5, "profit": 0, "cost": 12}}


class _Env:
    """Temp registry + pnl_tracker.json; restores module state on exit."""

    def __init__(self, strategies):
        self.strategies = strategies

    def __enter__(self):
        fd, self.bl = tempfile.mkstemp(suffix=".json"); os.close(fd)
        fd, self.pnl = tempfile.mkstemp(suffix=".json"); os.close(fd)
        json.dump({"trades": [], "summary": {"strategies": self.strategies}},
                  open(self.pnl, "w"))
        self.orig = (pt.STRATEGY_BLACKLIST_PATH, pt.PNL_PATH, ftl.category_stats)
        pt.STRATEGY_BLACKLIST_PATH, pt.PNL_PATH = self.bl, self.pnl
        return self

    def __exit__(self, *a):
        pt.STRATEGY_BLACKLIST_PATH, pt.PNL_PATH, ftl.category_stats = self.orig
        os.remove(self.bl); os.remove(self.pnl)


def _report(e):
    return {r["strategy"]: r for r in json.load(open(e.bl))["fee_blind_report"]}


def test_no_fee_true_blind_negative_not_paused():
    with _Env(BLIND) as e:
        ftl.category_stats = lambda lookback_days=None: {}
        paused = pt.update_strategy_blacklist(now=NOW, path=e.bl)  # live loader path
        assert paused == [], paused
        assert not pt.is_strategy_paused("X", now=NOW, path=e.bl)
        raw = json.load(open(e.bl))
        assert raw["paused"] == [] and raw["blacklisted"] == []
        r = _report(e)["X"]
        assert r["net"] == -12.0 and r["n"] == 5
        assert r["source"] == "fee_blind" and r["report_only"] is True
        assert pt.load_pause_registry(path=e.bl, now=NOW)["fee_blind_report"]
        # explicit empty stats: same
        assert pt.update_strategy_blacklist(now=NOW, stats={}, path=e.bl) == []
        # the gate's stats view never contains fee-blind
        assert pt._merged_stats({}) == {}


def test_fee_true_negative_pauses_as_before():
    with _Env(BLIND) as e:
        stats = {"X": {"n": 4, "wins": 0, "net": -3.5}}
        paused = pt.update_strategy_blacklist(now=NOW, stats=stats, path=e.bl)
        assert paused == ["X"], paused
        p = json.load(open(e.bl))["paused"][0]
        assert p["source"] == "category_stats" and p["net"] == -3.5 and p["n"] == 4
        assert pt.is_strategy_paused("X", now=NOW, path=e.bl)
    # thin fee-true (n<min) still does not pause
    with _Env(BLIND) as e2:
        assert pt.update_strategy_blacklist(
            now=NOW, stats={"X": {"n": 3, "wins": 0, "net": -9.0}}, path=e2.bl) == []


def test_fee_true_nonneg_beats_fee_blind_negative():
    with _Env(BLIND) as e:
        stats = {"X": {"n": 6, "wins": 3, "net": 0.0}}
        assert pt.update_strategy_blacklist(now=NOW, stats=stats, path=e.bl) == []
        assert not pt.is_strategy_paused("X", now=NOW, path=e.bl)
        assert _report(e)["X"]["net"] == -12.0  # still reported, labeled
        # existing pause + fee-true net>=0 -> unpaused despite fee-blind negative
        json.dump({"paused": [{"strategy": "X", "reason": "old",
                               "until": (NOW + timedelta(days=5)).isoformat()}]},
                  open(e.bl, "w"))
        assert pt.is_strategy_paused("X", now=NOW, path=e.bl)
        assert pt.update_strategy_blacklist(now=NOW, stats=stats, path=e.bl) == []
        assert not pt.is_strategy_paused("X", now=NOW, path=e.bl)


def test_fee_blind_cannot_unpause():
    """Existing pause, no fee-true evidence: fee-blind positive must NOT unpause."""
    blind_pos = {"X": {"wins": 9, "losses": 0, "profit": 50, "cost": 0}}
    with _Env(blind_pos) as e:
        json.dump({"paused": [{"strategy": "X", "reason": "fee-true",
                               "until": (NOW + timedelta(days=5)).isoformat()}]},
                  open(e.bl, "w"))
        paused = pt.update_strategy_blacklist(now=NOW, stats={}, path=e.bl)
        assert paused == ["X"], paused  # fee-blind +50 did not unpause
        assert pt.is_strategy_paused("X", now=NOW, path=e.bl)


def test_loader_raises_fail_open():
    with _Env(BLIND) as e:
        def boom(lookback_days=None):
            raise RuntimeError("ledger unreadable")
        ftl.category_stats = boom
        assert pt._fee_true_stats() == {}
        paused = pt.update_strategy_blacklist(now=NOW, path=e.bl)
        assert paused == [], paused
        assert not pt.is_strategy_paused("X", now=NOW, path=e.bl)
        assert _report(e)["X"]["report_only"] is True


def test_should_trade_category_ignores_fee_blind():
    with _Env(BLIND) as e:
        orig_ov = ftl._overrides
        ftl.category_stats = lambda lookback_days=None: {}
        ftl._overrides = lambda: {}
        try:
            pt.update_strategy_blacklist(now=NOW, path=e.bl)
            ok, why = ftl.should_trade_category("X")
            assert ok is True, why
        finally:
            ftl._overrides = orig_ov


def run():
    test_no_fee_true_blind_negative_not_paused()
    test_fee_true_negative_pauses_as_before()
    test_fee_true_nonneg_beats_fee_blind_negative()
    test_fee_blind_cannot_unpause()
    test_loader_raises_fail_open()
    test_should_trade_category_ignores_fee_blind()
    print("pnl fee-true gate checks: PASS")


if __name__ == "__main__":
    run()
