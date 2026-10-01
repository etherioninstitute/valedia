"""Offline Uniswap v4 + WETH/USDG fixtures on top of fakes.FakeRpc. No network.

Pool ids here are computed with the INDEPENDENT tests/keccak.py, so a bug in the source's
keccak or PoolKey encoding cannot hide itself.
"""
from __future__ import annotations

import math
from decimal import Decimal

from fakes import (FakeRpc, add_pool, add_token, addr_word, pool_created_log, set_balance,
                   sqrt_for, uint_word)
from keccak import keccak256
from rh_meme_bot import abi
from rh_meme_bot.profiles import NATIVE, PROFILES
from rh_meme_bot.tickmath import get_sqrt_ratio_at_tick

P = PROFILES[4663]
PM, SV, WETH, USDG = P.v4_pool_manager, P.v4_state_view, P.weth, P.usdg
MEME3 = "0x" + "a3" * 20  # v4 meme token (paired with native ETH)
MEME4 = "0x" + "a4" * 20  # v3 meme token (paired with USDG)
POOL4 = "0x" + "c4" * 20  # v3 MEME4/USDG pool
WETH_POOL_500 = "0x" + "e5" * 20
WETH_POOL_3000 = "0x" + "e3" * 20
FULL_LO, FULL_HI = -887220, 887220  # full range for tick spacing 60
HOOKS = "0x" + "40" * 19 + "c0"


def pool_id(c0, c1, fee, spacing, hooks=NATIVE) -> str:
    enc = addr_word(c0) + addr_word(c1) + uint_word(fee) + uint_word(spacing) + addr_word(hooks)
    return "0x" + keccak256(bytes.fromhex(enc)).hex()


def initialize_log(c0, c1, fee, spacing, hooks, sqrt, tick, block, idx=0, pid=None) -> dict:
    pid = pid or pool_id(c0, c1, fee, spacing, hooks)
    return {"address": PM,
            "topics": [abi.V4_INITIALIZE_TOPIC, pid, "0x" + addr_word(c0), "0x" + addr_word(c1)],
            "data": "0x" + uint_word(fee) + uint_word(spacing) + addr_word(hooks)
                    + uint_word(sqrt) + uint_word(tick),
            "blockNumber": hex(block), "transactionHash": "0x" + "cd" * 32, "logIndex": hex(idx)}


def modify_log(pid, lo, hi, delta, block, idx=0, sender="0x" + "77" * 20) -> dict:
    return {"address": PM,
            "topics": [abi.V4_MODIFY_LIQUIDITY_TOPIC, pid, "0x" + addr_word(sender)],
            "data": "0x" + uint_word(lo) + uint_word(hi) + uint_word(delta) + "00" * 32,
            "blockNumber": hex(block), "transactionHash": "0x" + "ef" * 32, "logIndex": hex(idx)}


def tick_at(sqrt: int) -> int:
    """Largest tick whose sqrt ratio is <= sqrt (what slot0.tick holds)."""
    t = math.floor(math.log((sqrt / 2 ** 96) ** 2) / math.log(1.0001))
    while get_sqrt_ratio_at_tick(t) > sqrt:
        t -= 1
    while get_sqrt_ratio_at_tick(t + 1) <= sqrt:
        t += 1
    return t


def set_v4_pool(rpc: FakeRpc, pid, sqrt, tick, liquidity, lp_fee=3000) -> None:
    rpc.set_call(SV, abi.SEL_V4_GET_SLOT0 + pid[2:],
                 "0x" + uint_word(sqrt) + uint_word(tick) + uint_word(0) + uint_word(lp_fee))
    rpc.set_call(SV, abi.SEL_V4_GET_LIQUIDITY + pid[2:], "0x" + uint_word(liquidity))


def install_v4(rpc: FakeRpc) -> None:
    """PoolManager + StateView that pass startup verification."""
    rpc.code[PM] = "0x6080"
    rpc.code[SV] = "0x6080"
    rpc.set_call(SV, abi.SEL_V4_POOL_MANAGER, "0x" + addr_word(PM))
    set_v4_pool(rpc, "0x" + "00" * 32, 0, 0, 0, 0)


def install_weth_usdg(rpc: FakeRpc, factory, price=2000.0, deep_fee=3000) -> None:
    """Two v3 WETH/USDG pools: the deeper one (more USDG) quotes ``price``."""
    add_token(rpc, WETH, symbol="WETH", owner_fn=False)
    add_token(rpc, USDG, decimals=6, symbol="USDG", owner_fn=False)
    shallow_fee = 500 if deep_fee != 500 else 3000
    for fee, pool, px, usdg in ((deep_fee, WETH_POOL_3000, price, 5_000_000),
                                (shallow_fee, WETH_POOL_500, price * 0.75, 1_000_000)):
        rpc.set_call(factory, abi.encode_get_pool(WETH, USDG, fee), "0x" + addr_word(pool))
        add_pool(rpc, pool, WETH, USDG, sqrt_price=sqrt_for(px, 18, 6))
        set_balance(rpc, USDG, pool, usdg * 10 ** 6)
    rpc.set_call(factory, abi.encode_get_pool(WETH, USDG, 100), "0x" + addr_word(NATIVE))


def v4_meme_pool(rpc: FakeRpc, block, *, eth_reserve=10, with_liquidity=True,
                 hooks=NATIVE, fee=3000, spacing=60, meme_per_eth=100_000):
    """MEME3/native-ETH v4 pool at ``meme_per_eth``; full-range liquidity holding about
    ``eth_reserve`` ETH. Returns (pool id, liquidity L, sqrt, tick)."""
    add_token(rpc, MEME3, symbol="MEME3", owner=None)
    sqrt = sqrt_for(meme_per_eth, 18, 18)
    tick = tick_at(sqrt)
    pid = pool_id(NATIVE, MEME3, fee, spacing, hooks)
    liq = int(Decimal(eth_reserve) * 10 ** 18 * Decimal(sqrt) / Decimal(2 ** 96))
    rpc.logs.append(initialize_log(NATIVE, MEME3, fee, spacing, hooks, sqrt, tick, block, 1))
    set_v4_pool(rpc, pid, sqrt, tick, liq if with_liquidity else 0)
    if with_liquidity:
        rpc.logs.append(modify_log(pid, FULL_LO, FULL_HI, liq, block, 2))
    return pid, liq, sqrt, tick


def v3_meme_pool(rpc: FakeRpc, block, usdg_reserve=30_000) -> None:
    """MEME4 (token0) / USDG (token1) v3 pool at 0.0005 USDG per MEME4."""
    add_token(rpc, MEME4, symbol="MEME4", owner=None)
    add_pool(rpc, POOL4, MEME4, USDG, sqrt_price=sqrt_for(0.0005, 18, 6))
    set_balance(rpc, USDG, POOL4, usdg_reserve * 10 ** 6)
    set_balance(rpc, MEME4, POOL4, 40_000_000 * 10 ** 18)
    rpc.logs.append(pool_created_log(MEME4, USDG, 3000, 60, POOL4, block, 0))


def dual_scenario(factory, head=1000) -> FakeRpc:
    """One v3 launch (MEME4/USDG) and one v4 launch (MEME3/ETH), WETH/USDG discoverable."""
    rpc = FakeRpc(head=head)
    install_v4(rpc)
    install_weth_usdg(rpc, factory)
    v3_meme_pool(rpc, head - 6)
    v4_meme_pool(rpc, head - 4)
    return rpc
