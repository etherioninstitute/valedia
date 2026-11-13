import json

import pytest

from valedia_trader.research_loop import risk_gate as rg

LIMITS = dict(rg.DEFAULT_LIMITS)
OK_ACCOUNT = {"equity": 100_000.0, "daily_loss_pct": 0.5, "drawdown_pct": 3.0, "open_positions": 2}


def check(order=None, account=None, **kw):
    kw.setdefault("halt_file", None)
    return rg.check_order({"value": 5_000.0, **(order or {})}, {**OK_ACCOUNT, **(account or {})}, LIMITS, **kw)


def test_the_four_limits_block_exactly_at_their_thresholds():
    assert check() == (True, "OK")
    assert check(account={"daily_loss_pct": 2.0}) == (False, "Daily loss limit hit. Trading stopped.")
    assert check(account={"daily_loss_pct": 1.99})[0]
    assert check(account={"drawdown_pct": 8.0}) == (False, "Max drawdown hit. Trading stopped.")
    assert check(order={"value": 10_000.0})[0]                                   # exactly 10% of equity is allowed
    assert check(order={"value": 10_000.01}) == (False, "Order is larger than the position limit.")
    assert check(order={"value": 2_000.0, "position_value_after": 12_000.0})[1] == "Order is larger than the position limit."
    assert check(account={"open_positions": 5}) == (False, "Too many open positions.")
    assert check(order={"adds_to_existing": True}, account={"open_positions": 5})[0]


def test_exits_always_get_out_unless_an_operator_halts(tmp_path):
    stressed = {"daily_loss_pct": 9.0, "drawdown_pct": 20.0, "open_positions": 9}
    assert not check(account=stressed)[0]
    assert check(order={"reduces_risk": True, "value": 50_000.0}, account=stressed) == (True, "OK (reduces an open position)")
    latch = tmp_path / "kill_switch.json"
    latch.write_text("{}")
    assert check(latch_file=latch)[1].startswith("Max drawdown was hit earlier")       # even after equity recovers
    assert check(order={"reduces_risk": True}, latch_file=latch)[0]
    halt = tmp_path / "halt.json"
    halt.write_text("{}")
    assert check(order={"reduces_risk": True}, halt_file=halt) == (False, "Operator halt file is present. Trading stopped.")


def test_anything_unexpected_blocks_the_order(tmp_path):
    assert not rg.check_order({}, OK_ACCOUNT, LIMITS, halt_file=None)[0]
    assert not rg.check_order({"value": "a lot"}, OK_ACCOUNT, LIMITS, halt_file=None)[0]
    assert not check(order={"value": float("nan")})[0] and not check(order={"value": -1})[0]
    assert not check(account={"equity": 0})[0] and not check(account={"drawdown_pct": float("inf")})[0]
    assert rg.check_order({"value": 1}, OK_ACCOUNT, None, halt_file=None)[1].startswith("No risk limits")
    assert rg.check_order({"value": 1}, OK_ACCOUNT, limits_file=tmp_path / "missing.json", halt_file=None)[1].startswith("Risk check could not")
    assert not rg.check_order(None, None, LIMITS, halt_file=None)[0]


def test_limits_file_is_validated_and_never_overwritten(tmp_path):
    path = tmp_path / "config" / "risk.json"
    assert rg.write_default_limits(path) and json.loads(path.read_text()) == rg.DEFAULT_LIMITS
    path.write_text(json.dumps({**rg.DEFAULT_LIMITS, "max_drawdown_pct": 5.0}))
    assert not rg.write_default_limits(path) and rg.load_limits(path)["max_drawdown_pct"] == 5.0
    assert rg.check_order({"value": 1_000.0}, {**OK_ACCOUNT, "drawdown_pct": 5.0}, limits_file=path, halt_file=None)[1] == "Max drawdown hit. Trading stopped."
    for bad in ({"max_daily_loss_pct": 2.0}, {**rg.DEFAULT_LIMITS, "extra": 1}, {**rg.DEFAULT_LIMITS, "max_position_pct": 2.0},
                {**rg.DEFAULT_LIMITS, "max_open_positions": 2.5}, {**rg.DEFAULT_LIMITS, "max_daily_loss_pct": -1},
                {**rg.DEFAULT_LIMITS, "max_drawdown_pct": True}, [1, 2]):
        path.write_text(json.dumps(bad))
        with pytest.raises(rg.RiskConfigError):
            rg.load_limits(path)
    path.write_text("{oops")
    with pytest.raises(rg.RiskConfigError):
        rg.load_limits(path)


def test_the_limits_file_may_carry_a_daily_order_cap_and_nothing_else_extra(tmp_path):
    path = tmp_path / "risk.json"
    path.write_text(json.dumps({**rg.DEFAULT_LIMITS, "max_orders_per_day": 12}))
    limits = rg.load_limits(path)
    assert limits["max_orders_per_day"] == 12 and {k: limits[k] for k in rg.DEFAULT_LIMITS} == rg.DEFAULT_LIMITS
    assert rg.check_order({"value": 5_000.0}, OK_ACCOUNT, limits, halt_file=None) == (True, "OK")   # the gate ignores it
    path.write_text(json.dumps({**rg.DEFAULT_LIMITS, "max_orders_per_day": 1.0}))
    assert rg.load_limits(path)["max_orders_per_day"] == 1
    for bad in (0, -3, 2.5, True, "40", None, 1e308 * 10):
        path.write_text(json.dumps({**rg.DEFAULT_LIMITS, "max_orders_per_day": bad}))
        with pytest.raises(rg.RiskConfigError):
            rg.load_limits(path)
    path.write_text(json.dumps({k: v for k, v in rg.DEFAULT_LIMITS.items() if k != "max_open_positions"} | {"max_orders_per_day": 5}))
    with pytest.raises(rg.RiskConfigError):                      # the optional key does not stand in for a required one
        rg.load_limits(path)
    path.write_text(json.dumps({**rg.DEFAULT_LIMITS, "max_orders_per_week": 5}))
    with pytest.raises(rg.RiskConfigError):
        rg.load_limits(path)
