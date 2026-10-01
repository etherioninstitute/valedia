"""Exact integer ports of Uniswap TickMath.getSqrtRatioAtTick and LiquidityAmounts.

Python ints are unbounded, so uint256 wrap-around never happens here; every step matches the
Solidity integer math (floor division, the same Q128.128 magic constants, round-up on >> 32).
"""
from __future__ import annotations

from typing import Tuple

MIN_TICK = -887272
MAX_TICK = 887272
MIN_SQRT_RATIO = 4295128739
MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342
Q96 = 1 << 96
_UINT256_MAX = (1 << 256) - 1

# ratio multipliers for bits 1..19 of |tick| (bit 0 selects the initial value)
_MULS = [
    0xfff97272373d413259a46990580e213a, 0xfff2e50f5f656932ef12357cf3c7fdcc,
    0xffe5caca7e10e4e61c3624eaa0941cd0, 0xffcb9843d60f6159c9db58835c926644,
    0xff973b41fa98c081472e6896dfb254c0, 0xff2ea16466c96a3843ec78b326b52861,
    0xfe5dee046a99a2a811c461f1969c3053, 0xfcbe86c7900a88aedcffc83b479aa3a4,
    0xf987a7253ac413176f2b074cf7815e54, 0xf3392b0822b70005940c7a398e4b70f3,
    0xe7159475a2c29b7443b29c7fa6e889d9, 0xd097f3bdfd2022b8845ad8f792aa5825,
    0xa9f746462d870fdf8a65dc1f90e061e5, 0x70d869a156d2a1b890bb3df62baf32f7,
    0x31be135f97d08fd981231505542fcfa6, 0x9aa508b5b7a84e1c677de54f3e99bc9,
    0x5d6af8dedb81196699c329225ee604, 0x2216e584f5fa1ea926041bedfe98,
    0x48a170391f7dc42444e8fa2,
]


def get_sqrt_ratio_at_tick(tick: int) -> int:
    """sqrt(1.0001^tick) * 2^96 as a Q64.96, exactly as TickMath.sol computes it."""
    if not isinstance(tick, int) or isinstance(tick, bool):
        raise TypeError("tick must be int")
    abs_tick = -tick if tick < 0 else tick
    if abs_tick > MAX_TICK:
        raise ValueError("T")  # same revert reason as TickMath
    ratio = 0xfffcb933bd6fad37aa2d162d1a594001 if abs_tick & 0x1 else 1 << 128
    for i, mul in enumerate(_MULS, start=1):
        if abs_tick & (1 << i):
            ratio = (ratio * mul) >> 128
    if tick > 0:
        ratio = _UINT256_MAX // ratio
    return (ratio >> 32) + (0 if ratio % (1 << 32) == 0 else 1)


def _mul_div(a: int, b: int, d: int) -> int:
    return (a * b) // d  # FullMath.mulDiv (floor); exact with unbounded ints


def get_amount0_for_liquidity(sqrt_a: int, sqrt_b: int, liquidity: int) -> int:
    if sqrt_a > sqrt_b:
        sqrt_a, sqrt_b = sqrt_b, sqrt_a
    return _mul_div(liquidity << 96, sqrt_b - sqrt_a, sqrt_b) // sqrt_a


def get_amount1_for_liquidity(sqrt_a: int, sqrt_b: int, liquidity: int) -> int:
    if sqrt_a > sqrt_b:
        sqrt_a, sqrt_b = sqrt_b, sqrt_a
    return _mul_div(liquidity, sqrt_b - sqrt_a, Q96)


def get_amounts_for_liquidity(sqrt_price: int, sqrt_a: int, sqrt_b: int,
                              liquidity: int) -> Tuple[int, int]:
    """(amount0, amount1) held by ``liquidity`` between sqrt_a and sqrt_b at sqrt_price."""
    if sqrt_a > sqrt_b:
        sqrt_a, sqrt_b = sqrt_b, sqrt_a
    if sqrt_price <= sqrt_a:
        return get_amount0_for_liquidity(sqrt_a, sqrt_b, liquidity), 0
    if sqrt_price < sqrt_b:
        return (get_amount0_for_liquidity(sqrt_price, sqrt_b, liquidity),
                get_amount1_for_liquidity(sqrt_a, sqrt_price, liquidity))
    return 0, get_amount1_for_liquidity(sqrt_a, sqrt_b, liquidity)
