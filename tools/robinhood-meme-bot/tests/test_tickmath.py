from decimal import Decimal, getcontext

import pytest

from rh_meme_bot.tickmath import (MAX_SQRT_RATIO, MAX_TICK, MIN_SQRT_RATIO, MIN_TICK, Q96,
                                  get_amount0_for_liquidity, get_amount1_for_liquidity,
                                  get_amounts_for_liquidity, get_sqrt_ratio_at_tick)

getcontext().prec = 80


def ref_sqrt(tick: int) -> Decimal:
    """Independent reference: sqrt(1.0001^tick) * 2^96 in 80-digit Decimal."""
    return (Decimal("1.0001") ** tick).sqrt() * Decimal(2 ** 96)


def test_known_vectors():
    assert get_sqrt_ratio_at_tick(0) == 2 ** 96 == 79228162514264337593543950336
    assert get_sqrt_ratio_at_tick(MIN_TICK) == MIN_SQRT_RATIO == 4295128739
    assert get_sqrt_ratio_at_tick(MAX_TICK) == MAX_SQRT_RATIO \
        == 1461446703485210103287273052203988822378723970342
    with pytest.raises(ValueError):
        get_sqrt_ratio_at_tick(MAX_TICK + 1)
    with pytest.raises(ValueError):
        get_sqrt_ratio_at_tick(MIN_TICK - 1)


@pytest.mark.parametrize("tick", [1, -1, 2, -2, 60, -60, 100, 887, -76000, 115129]
                         + [2 ** k for k in range(20)] + [-(2 ** k) for k in range(20)])
def test_every_magic_constant_against_decimal_reference(tick):
    # each power of two exercises exactly one of the 20 Q128.128 constants
    got, ref = get_sqrt_ratio_at_tick(tick), ref_sqrt(tick)
    assert abs(Decimal(got) - ref) <= max(Decimal(2), ref * Decimal("1e-25"))


def test_monotonic_and_round_up():
    prev = 0
    for t in range(-500, 500, 7):
        cur = get_sqrt_ratio_at_tick(t)
        assert cur > prev
        prev = cur


L = 10 ** 18
LO, HI = -600, 600
SA, SB = get_sqrt_ratio_at_tick(LO), get_sqrt_ratio_at_tick(HI)


def test_amounts_in_range_both_sides():
    sp = get_sqrt_ratio_at_tick(0)
    a0, a1 = get_amounts_for_liquidity(sp, SA, SB, L)
    # float reference: x = L (1/sqrtP - 1/sqrtB), y = L (sqrtP - sqrtA) with Q96 scaling
    x = Decimal(L) * (Decimal(Q96) / sp - Decimal(Q96) / SB)
    y = Decimal(L) * (Decimal(sp) - Decimal(SA)) / Decimal(Q96)
    assert a0 > 0 and a1 > 0
    assert abs(a0 - x) <= 2 and abs(a1 - y) <= 2
    assert get_amounts_for_liquidity(sp, SB, SA, L) == (a0, a1)  # order-insensitive


def test_amounts_below_range_all_token0():
    sp = get_sqrt_ratio_at_tick(-1000)
    a0, a1 = get_amounts_for_liquidity(sp, SA, SB, L)
    assert a1 == 0 and a0 == get_amount0_for_liquidity(SA, SB, L) > 0
    assert get_amounts_for_liquidity(SA, SA, SB, L)[1] == 0  # exactly at lower bound


def test_amounts_above_range_all_token1():
    sp = get_sqrt_ratio_at_tick(1000)
    a0, a1 = get_amounts_for_liquidity(sp, SA, SB, L)
    assert a0 == 0 and a1 == get_amount1_for_liquidity(SA, SB, L) > 0
    assert get_amounts_for_liquidity(SB, SA, SB, L)[0] == 0  # exactly at upper bound
