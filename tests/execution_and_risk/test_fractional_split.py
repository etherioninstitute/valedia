"""T51 A1 (R1): the split of a difference into a whole-share LIMIT leg and a MARKET remainder leg, as a pure function.

Every property is checked over seeded inputs against an oracle written here in whole steps of 1e-5 units (integer
arithmetic), which shares no code with `split_difference`. Nothing in the order step calls the split yet (slice A1)."""
import math
import random
from decimal import Decimal, localcontext

import pytest

from valedia_trader.execution_and_risk import order_step
from valedia_trader.execution_and_risk.order_step import (FRACTIONAL_MIN_VALUE, MAX_QUOTE_PRICE, MAX_UNITS,
                                                          OrderStepError, Split, SplitLeg, round_units,
                                                          split_difference)

STEPS = 10 ** 5                                   # 1e-5 units: FRACTIONAL_DECIMALS = 5
SEEDS = range(40)
PER_SEED = 150


def _d(x) -> Decimal:
    return Decimal(repr(float(x)))


def _steps(x: Decimal) -> int:
    """Whole 1e-5 steps of x, toward zero (int() of a Decimal truncates)."""
    return int(x * STEPS)


def oracle(difference: float, quote: float, held: float, move: float) -> dict:
    """What R1 says, worked in integer steps: the rounded difference (a sale capped at the holding), the target of
    zero when the target is worth less than the minimum, the whole and remainder legs, the residual."""
    with localcontext() as ctx:
        ctx.prec = 100
        return _oracle(_d(difference), _d(quote), _d(held), _d(move))


def _oracle(d: Decimal, q: Decimal, h: Decimal, move: Decimal) -> dict:
    rounded = _steps(max(d, -h))
    zeroed = (d + h) * q < Decimal(5)
    effective = -_steps(h) if zeroed else rounded
    sign = 1 if effective > 0 else -1
    whole, rem = divmod(abs(effective), STEPS)
    legs = []
    if whole >= 1:
        legs.append(("whole", "LIMIT", sign * whole * STEPS))
    if rem > 0 and Decimal(rem) / STEPS * q * (1 - move) >= Decimal(5):
        legs.append(("remainder", "MARKET", sign * rem))
    sent = sum(u for _, _, u in legs)
    return {"difference": rounded, "zeroed": zeroed, "legs": legs, "residual": rounded - sent}


def _as_steps(units: Decimal) -> int:
    n = units * STEPS
    assert n == n.to_integral_value(), f"{units} has more than 5 decimals"
    return int(n)


def _quote(rng: random.Random) -> float:
    kind = rng.random()
    if kind < 0.15:
        return round(rng.uniform(0.5, 5.0), 2)                  # cheap: a whole share is worth less than the minimum
    if kind < 0.85:
        return round(rng.uniform(5.0, 700.0), 2)
    if kind < 0.95:
        return round(rng.uniform(1.0, 3000.0), rng.choice([3, 4]))
    return round(rng.uniform(1000.0, 50_000.0), 2)


def _units(rng: random.Random, quote: float) -> float:
    kind = rng.random()
    if kind < 0.2:
        return 0.0
    if kind < 0.35:
        return float(rng.randint(1, 400))
    if kind < 0.55:
        return round(rng.uniform(0, 4.99) / quote, rng.choice([5, 6, 8]))   # worth less than the minimum
    if kind < 0.8:
        return round(rng.uniform(0, 300), 5)
    return round(rng.uniform(0, 30), rng.choice([6, 8, 10]))               # more decimals than the step


def world(rng: random.Random) -> tuple[float, float, str, float]:
    """(difference, quote, side, held) with a difference that is not zero."""
    while True:
        quote = _quote(rng)
        held, target = _units(rng, quote), _units(rng, quote)
        if rng.random() < 0.15:                                   # a remainder right at the minimum's edge
            edge = math.ceil(5 * STEPS / (quote * (1 - order_step.TICKET_QUOTE_MOVE))) + rng.choice([-1, 0, 1])
            if 0 < edge < STEPS:
                target = held + rng.randint(0, 3) + edge / STEPS
        if rng.random() < 0.05:
            target = -rng.uniform(0, 10)                          # a target below zero: the sale is capped
        difference = target - held
        if difference != 0:
            return difference, quote, ("buy" if difference > 0 else "sell"), held


def _check(split: Split, want: dict) -> None:
    assert isinstance(split, Split)
    assert _as_steps(split.difference) == want["difference"]
    assert split.target_zeroed is want["zeroed"]
    got = [(leg.leg, leg.order_type, _as_steps(leg.units)) for leg in split.legs]
    assert got == want["legs"]
    assert _as_steps(split.residual) == want["residual"]


def _all_worlds():
    for seed in SEEDS:
        rng = random.Random(seed)
        for _ in range(PER_SEED):
            yield world(rng)


def test_the_split_is_the_oracle_over_seeded_worlds():
    kinds = {"limit only": 0, "market only": 0, "both": 0, "residual": 0, "zeroed": 0, "capped": 0}
    for difference, quote, side, held in _all_worlds():
        split = split_difference(difference, quote, side, held)
        _check(split, oracle(difference, quote, held, order_step.TICKET_QUOTE_MOVE))
        types = [leg.order_type for leg in split.legs]
        kinds["limit only"] += types == ["LIMIT"]
        kinds["market only"] += types == ["MARKET"]
        kinds["both"] += types == ["LIMIT", "MARKET"]
        kinds["residual"] += split.residual != 0
        kinds["zeroed"] += split.target_zeroed
        kinds["capped"] += difference < -held
    assert all(n >= 50 for n in kinds.values()), kinds         # every case of R1 is met many times, none vacuous


def test_the_legs_and_the_residual_sum_to_the_rounded_difference_exactly():
    for difference, quote, side, held in _all_worlds():
        split = split_difference(difference, quote, side, held)
        assert sum((leg.units for leg in split.legs), Decimal(0)) + split.residual == split.difference
        today = round_units(difference, True)                     # the rounded difference as today's code makes it
        if today < 0:
            today = round_units(max(today, -max(held, 0.0)), True)
        assert split.difference == _d(today)
        assert split.residual_value == abs(split.residual) * _d(quote)


def test_no_limit_leg_has_decimals_and_no_market_leg_is_one_or_more_or_zero():
    for difference, quote, side, held in _all_worlds():
        split = split_difference(difference, quote, side, held)
        assert [leg.leg for leg in split.legs] in ([], ["whole"], ["remainder"], ["whole", "remainder"])
        for leg in split.legs:
            assert isinstance(leg, SplitLeg) and isinstance(leg.units, Decimal)
            assert _d(float(leg.units)) == leg.units                # handed on as a float, it is the same number
            if leg.order_type == "LIMIT":
                assert leg.leg == "whole" and leg.units == leg.units.to_integral_value() and abs(leg.units) >= 1
            else:
                assert leg.order_type == "MARKET" and leg.leg == "remainder"
                assert 0 < abs(leg.units) < 1
                assert abs(leg.units) * _d(quote) * (1 - _d(order_step.TICKET_QUOTE_MOVE)) >= FRACTIONAL_MIN_VALUE
        assert len({leg.units > 0 for leg in split.legs}) <= 1     # both legs on one side


def test_a_sale_never_exceeds_the_ledgers_own_holding():
    for difference, quote, side, held in _all_worlds():
        split = split_difference(difference, quote, side, held)
        sold = -sum((leg.units for leg in split.legs if leg.units < 0), Decimal(0))
        assert sold <= _d(held)
        assert split.difference >= -_d(held)


def test_a_target_worth_less_than_the_minimum_is_a_target_of_zero():
    met = 0
    for difference, quote, side, held in _all_worlds():
        split = split_difference(difference, quote, side, held)
        worth = (_d(difference) + _d(held)) * _d(quote)
        assert split.target_zeroed is (worth < FRACTIONAL_MIN_VALUE)
        if not split.target_zeroed:
            continue
        met += 1
        assert all(leg.units < 0 for leg in split.legs)            # nothing is ever bought toward such a target
        whole_held = _steps(_d(held)) // STEPS
        assert [-_as_steps(leg.units) for leg in split.legs if leg.leg == "whole"] == \
            ([whole_held * STEPS] if whole_held >= 1 else [])       # every whole share of the book is sold
    assert met >= 200


def test_the_minimums_margin_uses_ticket_quote_move(monkeypatch):
    """The remainder goes when r x q x (1 - TICKET_QUOTE_MOVE) >= 5, read from the module when the split runs."""
    monkeypatch.setattr(order_step, "TICKET_QUOTE_MOVE", 0.2)                      # 6.25 x 0.8 = 5 exactly: sent
    (leg,) = split_difference(0.5, 12.5, "buy", 10.0).legs
    assert (leg.order_type, leg.units) == ("MARKET", Decimal("0.5"))
    assert split_difference(0.5, 12.49, "buy", 10.0).legs == ()                    # 4.996 < 5: a residual
    monkeypatch.setattr(order_step, "TICKET_QUOTE_MOVE", 0.0)
    (leg,) = split_difference(0.5, 10.0, "buy", 10.0).legs                         # 5 x 1 = 5: sent
    assert leg.units == Decimal("0.5")
    monkeypatch.setattr(order_step, "TICKET_QUOTE_MOVE", 0.005)
    assert split_difference(0.5, 10.0, "buy", 10.0).legs == ()                     # 5 x 0.995 < 5


@pytest.mark.parametrize("move", [0.0, 0.005, 0.03, 0.25])
def test_the_margin_follows_ticket_quote_move_over_seeded_worlds(monkeypatch, move):
    monkeypatch.setattr(order_step, "TICKET_QUOTE_MOVE", move)
    decided = []
    for difference, quote, side, held in _all_worlds():
        split = split_difference(difference, quote, side, held)
        _check(split, oracle(difference, quote, held, move))
        decided.append(any(leg.order_type == "MARKET" for leg in split.legs))
    if move != 0.005:                                               # the margin changes some decisions
        monkeypatch.setattr(order_step, "TICKET_QUOTE_MOVE", 0.005)
        other = [any(leg.order_type == "MARKET" for leg in split_difference(*w).legs) for w in _all_worlds()]
        assert decided != other


def test_the_remainder_at_the_minimums_edge_over_seeded_quotes():
    """For each quote, the smallest remainder in 1e-5 steps that clears the margin is sent and one step less is not."""
    rng = random.Random(7)
    move = _d(order_step.TICKET_QUOTE_MOVE)
    checked = 0
    for _ in range(3000):
        quote = _quote(rng)
        q = _d(quote)
        edge = math.ceil(Decimal(5) * STEPS / (q * (1 - move)))
        while Decimal(edge) / STEPS * q * (1 - move) < 5:
            edge += 1
        while Decimal(edge - 1) / STEPS * q * (1 - move) >= 5:
            edge -= 1
        if not 1 < edge < STEPS:
            continue
        whole = rng.randint(0, 5)
        held = float(rng.randint(1, 50))                            # a target worth well above the minimum
        for r, sent in ((edge, True), (edge - 1, False)):
            difference = float(f"{whole}.{r:05d}")
            split = split_difference(difference, quote, "buy", held)
            markets = [leg for leg in split.legs if leg.order_type == "MARKET"]
            assert bool(markets) is sent, (quote, r)
            assert split.residual == (0 if sent else Decimal(r) / STEPS)
        checked += 1
    assert checked > 1000


def test_cases_the_ticket_names():
    s = split_difference(0.4, 100.0, "buy", 0.0)                                   # under one share: MARKET only
    assert [(x.leg, x.order_type, x.units) for x in s.legs] == [("remainder", "MARKET", Decimal("0.4"))]
    s = split_difference(3.0, 100.0, "buy", 2.0)                                   # exactly whole: LIMIT only
    assert [(x.order_type, x.units) for x in s.legs] == [("LIMIT", Decimal(3))] and s.residual == 0
    s = split_difference(3.04, 100.0, "buy", 2.0)                                  # remainder worth 4: residual
    assert [(x.order_type, x.units) for x in s.legs] == [("LIMIT", Decimal(3))]
    assert s.residual == Decimal("0.04") and s.residual_value == Decimal("4.0000") and s.why
    s = split_difference(-12.34567, 50.0, "sell", 12.34567)                        # a whole holding with decimals
    assert [(x.order_type, x.units) for x in s.legs] == [("LIMIT", Decimal(-12)), ("MARKET", Decimal("-0.34567"))]
    s = split_difference(-12.04, 50.0, "sell", 12.04)                              # its remainder is dust (2.00)
    assert [(x.order_type, x.units) for x in s.legs] == [("LIMIT", Decimal(-12))] and s.residual == Decimal("-0.04")
    s = split_difference(-9.97, 100.0, "sell", 10.0)                               # a target worth 3: zero
    assert s.target_zeroed and [(x.order_type, x.units) for x in s.legs] == [("LIMIT", Decimal(-10))]
    assert s.residual == Decimal("0.03")                            # the target's units not held, not a sale
    s = split_difference(-20.0, 100.0, "sell", 10.0)                               # a sale capped at the book
    assert s.difference == Decimal(-10) and [x.units for x in s.legs] == [Decimal(-10)]
    s = split_difference(0.00000499, 100.0, "buy", 1.0)                            # rounds to nothing
    assert s.legs == () and s.difference == 0 and s.residual == 0 and s.why is None


BAD = {
    "difference": [math.nan, math.inf, -math.inf, 0, 0.0, -0.0, MAX_UNITS * 2, -MAX_UNITS * 2, True, "1", None,
                   Decimal("1")],
    "quote": [math.nan, math.inf, -math.inf, 0, 0.0, -1.0, -0.01, MAX_QUOTE_PRICE * 1.5, False, "100", None],
    "held": [math.nan, math.inf, -math.inf, -1.0, -0.00001, MAX_UNITS * 2, True, "0", None],
    "side": ["BUY", "short", "", None, 1, "sell "],
}


def test_unusable_inputs_are_refused_in_words_over_seeded_worlds():
    names = ("difference", "quote", "side", "held")
    refused = 0
    for seed in SEEDS:
        rng = random.Random(1000 + seed)
        for _ in range(60):
            args = dict(zip(names, world(rng)))
            field = rng.choice(list(BAD))
            args[field] = rng.choice(BAD[field])
            with pytest.raises(OrderStepError) as caught:
                split_difference(**args)
            assert field in str(caught.value), (field, args[field], str(caught.value))
            refused += 1
    assert refused == 2400


def test_a_side_that_disagrees_with_the_difference_is_refused():
    for difference, quote, side, held in _all_worlds():
        other = "sell" if side == "buy" else "buy"
        with pytest.raises(OrderStepError, match="side"):
            split_difference(difference, quote, other, held)


def test_the_split_is_pure_and_deterministic():
    worlds = list(_all_worlds())[:2000]
    first = [split_difference(*w) for w in worlds]
    assert [split_difference(*w) for w in reversed(worlds)][::-1] == first
    with pytest.raises(Exception):
        first[0].legs = ()                                          # frozen


def test_nothing_in_the_order_step_calls_the_split_yet():
    import inspect
    source = inspect.getsource(order_step)
    assert source.count("split_difference(") == 1                   # its own definition only (slice A1)


EXACT_FIVE = [(0.5, 10.0), (0.05, 100.0), (0.25, 20.0), (2.5, 2.0), (0.04, 125.0), (0.0125, 400.0), (1.0, 5.0),
              (0.00625, 800.0), (0.2, 25.0), (4.0, 1.25)]                  # target x quote = 5 exactly


def test_a_target_worth_exactly_the_minimum_is_kept_and_a_step_less_is_zero():
    rng = random.Random(55)
    for target, quote in EXACT_FIVE:
        n = _steps(_d(target))
        for _ in range(20):
            held_steps = rng.randint(0, 2 * n)
            held = held_steps / STEPS
            for steps, zeroed in ((n, False), (n - 1, True)):
                difference = (steps - held_steps) / STEPS
                if difference == 0:
                    continue
                side = "buy" if difference > 0 else "sell"
                split = split_difference(difference, quote, side, held)
                assert split.target_zeroed is zeroed, (target, quote, held, steps)
                if zeroed:
                    assert all(leg.units < 0 for leg in split.legs)
