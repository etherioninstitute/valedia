#!/usr/bin/env python3
"""Regression: kalshi_fee must not bump exact-cent fees up a cent via float error."""
import os, sys
from decimal import Decimal, ROUND_CEILING

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forward_test_ledger import kalshi_fee
import fee_engine

CASES = [
    (0.5, 100, 1.75),   # raw 1.7500000000000002 used to give 1.76
    (0.1, 100, 0.63),   # used to give 0.64
    (0.5, 0.6, 0.02),   # genuine fractional cent rounds up
    (0.5, 1, 0.02),     # 0.0175 -> 0.02
    (0, 10, 0.0),
    (1, 10, 0.0),
]
for p, c, want in CASES:
    got = kalshi_fee(p, c)
    assert got == want, f"kalshi_fee({p},{c})={got!r} want {want!r}"

# genuine fractional cent: raw 0.0105 -> 0.02 (P=0.5, C=0.6 -> 0.0105)
assert kalshi_fee(0.5, 0.6) == 0.02


def exact(pc, c):
    p = Decimal(pc) / 100
    return Decimal("0.07") * c * p * (1 - p)


n = 0
for pc in range(1, 100):
    for c in range(1, 501):
        got = kalshi_fee(pc / 100, c)
        ex = exact(pc, c)
        cents = Decimal(repr(got)) * 100
        assert cents == cents.to_integral_value(), (pc, c, got)
        gd = Decimal(repr(got))
        assert gd >= ex, (pc, c, got, ex)
        assert gd - ex < Decimal("0.01"), (pc, c, got, ex)
        # must equal exact ceil-to-cent
        want = (ex * 100).to_integral_value(rounding=ROUND_CEILING) / 100
        assert gd == want, (pc, c, got, want)
        n += 1

# fee_engine.calculate_trade_cost kalshi path uses the same rounding
r = fee_engine.calculate_trade_cost("kalshi", "buy", 50.0, price=0.5, use_static=True)
assert r["trading_fee"] == 1.75, r  # 100 contracts at 0.5

print(f"PASS test_kalshi_fee_rounding ({n} grid points)")
