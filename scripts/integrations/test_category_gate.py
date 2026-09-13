#!/usr/bin/env python3
"""Safety checks for the category calibration gate. Run: python3 test_category_gate.py
Asserts the three properties Burton required: blocks only on VERIFIED net losses (not
win-rate, not errors), fail-open on any error, never blocks thin/paper/old samples."""
import forward_test_ledger as ftl


def _with(stats, overrides=None):
    ftl.category_stats = lambda lookback_days=None: dict(stats)
    ftl._overrides = lambda: dict(overrides or {})


def run():
    # 1. Real net loss over a real sample -> BLOCK.
    _with({"OIL_BRACKET": {"n": 4, "wins": 0, "net": -19.90}})
    ok, why = ftl.should_trade_category("OIL_BRACKET")
    assert ok is False, why

    # 2. Fat-tailed WINNER with LOW win-rate -> ALLOW (net>0 protects it; not a win-rate gate).
    _with({"CRYPTO_BRACKET": {"n": 16, "wins": 4, "net": 29.00}})
    ok, why = ftl.should_trade_category("CRYPTO_BRACKET")
    assert ok is True, why

    # 3. Thin sample (below CATEGORY_MIN_TRADES) even if losing -> ALLOW (not enough evidence).
    _with({"WEATHER_ARB": {"n": 2, "wins": 0, "net": -5.0}})
    ok, why = ftl.should_trade_category("WEATHER_ARB")
    assert ok is True, why

    # 4. Unknown category (no data) -> ALLOW.
    _with({})
    assert ftl.should_trade_category("NEW_THING")[0] is True

    # 5. Gate error (stats raises) -> FAIL-OPEN allow. This is Burton's core fear: an error
    #    (not a verified loss) must NEVER block.
    def _boom(lookback_days=None):
        raise RuntimeError("ledger read failed")
    ftl.category_stats = _boom
    ftl._overrides = lambda: {}
    assert ftl.should_trade_category("OIL_BRACKET")[0] is True

    # 6. Manual park -> block; retest override -> allow (nothing permanent).
    _with({}, {"parked": ["OIL_BRACKET"]})
    assert ftl.should_trade_category("OIL_BRACKET")[0] is False
    _with({"OIL_BRACKET": {"n": 4, "wins": 0, "net": -19.90}},
          {"parked": ["OIL_BRACKET"], "retest": ["OIL_BRACKET"]})
    assert ftl.should_trade_category("OIL_BRACKET")[0] is True  # retest wins

    print("category-gate safety checks: PASS")


if __name__ == "__main__":
    run()
