#!/usr/bin/env python3
"""Checks for the adopted Kelly upgrades: Thorp concurrent reduction + Bayesian prob shrink.
Run: python3 test_concurrent_kelly.py"""
import math
from kelly_sizing import KellySizer, bayesian_shrink_prob


def run():
    # Bayesian shrink: no data -> pulled toward the α=β=2 posterior (50%), not left at raw 80.
    assert bayesian_shrink_prob(80, wins=0, total=0) == 50.0
    # Lots of consistent data -> mostly trusts the raw estimate.
    s = bayesian_shrink_prob(80, wins=60, total=100)
    assert 70 < s < 80, s
    # A losing category on a SMALL sample drags an optimistic raw prob DOWN (heavier shrink).
    assert bayesian_shrink_prob(80, wins=1, total=4) < 60
    # The same 1-win record on a LARGER sample shrinks less (raw dominates as data grows).
    assert bayesian_shrink_prob(80, wins=1, total=4) < bayesian_shrink_prob(80, wins=20, total=80)
    # Clamped to [1,99].
    assert 1.0 <= bayesian_shrink_prob(99, 0, 0) <= 99.0

    # Thorp reduction: 4 concurrent bets -> each ~half of the single-bet size (f/sqrt(4)).
    # Pass a high track-record/risk so the dynamic cap can't bind and mask the ratio.
    k1 = KellySizer(10000)
    r1 = k1.size_position(70, 2.0, regime="SIDEWAYS", concurrent_bets=1, category=None,
                          track_record_mult=1.5, risk_factor=1.0)
    k4 = KellySizer(10000)
    r4 = k4.size_position(70, 2.0, regime="SIDEWAYS", concurrent_bets=4, category=None,
                          track_record_mult=1.5, risk_factor=1.0)
    if r1.get("position_pct", 0) > 0 and r4.get("position_pct", 0) > 0:
        ratio = r4["position_pct"] / r1["position_pct"]
        assert abs(ratio - 0.5) < 0.05, f"expected ~0.5, got {ratio:.3f}"
    else:
        # If the single-bet size was cap-bound, at least the 4-bet size must be <= it.
        assert r4.get("position_pct", 0) <= r1.get("position_pct", 0)

    print("concurrent-Kelly + Bayesian-shrink checks: PASS")


if __name__ == "__main__":
    run()
