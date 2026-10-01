import re
from decimal import Decimal

import pytest

from fakes import FakeRpc, uint_word
from fakes_v4 import (FULL_HI, FULL_LO, HOOKS, MEME3, PM, SV, WETH, initialize_log,
                      install_v4, modify_log, pool_id, set_v4_pool, tick_at, v4_meme_pool)
from keccak import keccak256 as ref_keccak
from rh_meme_bot import abi
from rh_meme_bot.chain import ChainReader
from rh_meme_bot.config import load_config
from rh_meme_bot.keccak import keccak256
from rh_meme_bot.profiles import NATIVE
from rh_meme_bot.quotes import QuoteUsd
from rh_meme_bot.rpc import RpcClient
from rh_meme_bot.state import build_state
from rh_meme_bot.tickmath import get_amounts_for_liquidity, get_sqrt_ratio_at_tick
from rh_meme_bot.v4 import (V4Reader, compute_pool_id, decode_initialize,
                            decode_modify_liquidity, reserves_from_positions)


def v4reader(rpc):
    return V4Reader(RpcClient("http://fake", transport=rpc, sleep=lambda s: None), PM, SV, 2000)


def sig(s: str) -> str:
    return "0x" + ref_keccak(s.encode()).hex()


def test_v4_topics_and_selectors_match_keccak():
    assert sig("Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)") \
        == abi.V4_INITIALIZE_TOPIC \
        == "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"
    assert sig("ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)") \
        == abi.V4_MODIFY_LIQUIDITY_TOPIC
    assert sig("getSlot0(bytes32)")[:10] == abi.SEL_V4_GET_SLOT0
    assert sig("getLiquidity(bytes32)")[:10] == abi.SEL_V4_GET_LIQUIDITY
    assert sig("poolManager()")[:10] == abi.SEL_V4_POOL_MANAGER
    assert sig("getPool(address,address,uint24)")[:10] == abi.SEL_GET_POOL == "0x1698ee82"


def test_source_keccak_matches_known_vector_and_test_copy():
    assert keccak256(b"").hex() == \
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    for data in (b"abc", b"x" * 200, bytes(range(160))):
        assert keccak256(data) == ref_keccak(data)


def test_pool_id_computation():
    pid = compute_pool_id(NATIVE, MEME3, 3000, 60, NATIVE)
    assert pid == pool_id(NATIVE, MEME3, 3000, 60, NATIVE) and len(pid) == 66
    assert compute_pool_id(NATIVE, MEME3, 3000, 60, HOOKS) != pid  # hooks are part of the key
    assert compute_pool_id(NATIVE, MEME3, 0x800000, -1 & 0xFFFFFF, NATIVE) \
        != compute_pool_id(NATIVE, MEME3, 0x800000, -1, NATIVE)  # int24 sign-extends


def test_decode_initialize_native_eth_and_hooks():
    log = initialize_log(NATIVE, MEME3, 3000, 60, NATIVE, 2 ** 96, 0, 900, 3)
    ev = decode_initialize(log)
    assert ev.version == "v4" and ev.pool == ev.pool_id == pool_id(NATIVE, MEME3, 3000, 60)
    assert ev.pool_address is None and (ev.token0, ev.token1) == (NATIVE, MEME3)
    assert (ev.fee, ev.tick_spacing, ev.block_number, ev.log_index) == (3000, 60, 900, 3)
    assert ev.hooks == NATIVE and not ev.has_hooks and not ev.dynamic_fee
    hooked = decode_initialize(initialize_log(NATIVE, MEME3, 0x800000, 10, HOOKS, 2 ** 96, -5, 1))
    assert hooked.has_hooks and hooked.dynamic_fee and hooked.hooks == HOOKS


def test_decode_initialize_rejects_wrong_id_and_bad_logs():
    bad_id = initialize_log(NATIVE, MEME3, 3000, 60, NATIVE, 2 ** 96, 0, 1, pid="0x" + "11" * 32)
    with pytest.raises(ValueError, match="pool id"):
        decode_initialize(bad_id)
    with pytest.raises(ValueError):
        decode_initialize({"topics": [abi.V4_INITIALIZE_TOPIC], "data": "0x", "blockNumber": "0x1"})
    with pytest.raises(ValueError, match="unsorted"):
        decode_initialize(initialize_log(MEME3, NATIVE, 3000, 60, NATIVE, 2 ** 96, 0, 1))


def test_decode_modify_liquidity_negative_values():
    pid = pool_id(NATIVE, MEME3, 3000, 60)
    got = decode_modify_liquidity(modify_log(pid, -120, 60, -5 * 10 ** 18, 7))
    assert got == (pid, -120, 60, -5 * 10 ** 18)


def test_reserves_mixed_positions_and_liquidity_crosscheck():
    rpc = FakeRpc(head=1000)
    install_v4(rpc)
    sqrt = get_sqrt_ratio_at_tick(0) + 12345
    tick = tick_at(sqrt)
    pid = pool_id(NATIVE, MEME3, 3000, 60)
    positions = [(-600, 600, 4 * 10 ** 18),     # in range
                 (FULL_LO, FULL_HI, 10 ** 18),   # in range (full)
                 (600, 1200, 3 * 10 ** 18),      # above price: token0 only
                 (-1200, -600, 2 * 10 ** 18)]    # below price: token1 only
    rpc.logs = [initialize_log(NATIVE, MEME3, 3000, 60, NATIVE, sqrt, tick, 990)]
    for i, (lo, hi, liq) in enumerate(positions):
        rpc.logs.append(modify_log(pid, lo, hi, liq, 991 + i, i))
    rpc.logs.append(modify_log(pid, -60, 60, 9 * 10 ** 18, 996))   # added then removed
    rpc.logs.append(modify_log(pid, -60, 60, -9 * 10 ** 18, 997))
    rpc.logs.append(modify_log(pool_id(NATIVE, WETH, 500, 10), -60, 60, 10 ** 30, 998))  # other
    in_range = 4 * 10 ** 18 + 10 ** 18
    set_v4_pool(rpc, pid, sqrt, tick, in_range)
    r = v4reader(rpc)
    ev = decode_initialize(rpc.logs[0])
    res = r.reserves(ev, r.slot0(pid))
    exp0 = exp1 = 0
    for lo, hi, liq in positions:
        x, y = get_amounts_for_liquidity(sqrt, get_sqrt_ratio_at_tick(lo),
                                         get_sqrt_ratio_at_tick(hi), liq)
        exp0, exp1 = exp0 + x, exp1 + y
    assert (res.amount0, res.amount1) == (exp0, exp1)
    assert res.in_range_liquidity == in_range == r.liquidity(pid) and res.consistent
    set_v4_pool(rpc, pid, sqrt, tick, in_range + 1)  # logs incomplete vs StateView
    assert not r.reserves(ev, r.slot0(pid)).consistent


def test_negative_net_position_is_fail_closed():
    assert reserves_from_positions({(-60, 60): -1}, 2 ** 96, 0) is None
    assert reserves_from_positions({(-60, 60): 0}, 2 ** 96, 0) == (0, 0, 0)


def test_v4_pool_state_liquidity_usd_with_native_eth():
    rpc = FakeRpc(head=1000)
    install_v4(rpc)
    cfg = load_config(env={})  # built-in 4663 profile: native ETH is a 'weth'-priced quote
    pid, liq, sqrt, tick = v4_meme_pool(rpc, 990, eth_reserve=10)
    reader = ChainReader(RpcClient("http://fake", transport=rpc, sleep=lambda s: None),
                         cfg.factory_address)

    class Fixed(QuoteUsd):  # WETH price discovery is covered in test_quotes.py
        def usd(self, quote):
            return Decimal(2000) if quote in (WETH, NATIVE) else None

    ev = decode_initialize(rpc.logs[0])
    st, entry = build_state(reader, cfg, ev, Fixed(reader, cfg.quote_tokens), v4reader(rpc))
    assert st["version"] == "v4" and st["pool_id"] == pid and st["pool_address"] is None
    assert st["token_address"] == MEME3 and st["quote_symbol"] == "ETH"
    assert st["has_hooks"] is False and st["dynamic_fee"] is False and st["fee_tier"] == 3000
    assert st["price_in_quote"] == pytest.approx(1e-5, rel=1e-9)
    assert st["quote_reserve_usd"] == pytest.approx(20000.0, rel=1e-6)  # ~10 ETH x 2000
    assert st["liquidity_usd"] == pytest.approx(40000.0, rel=1e-6)
    assert st["v4_liquidity_consistent"] is True
    # no ERC-20 call (or getCode) was ever made on the native-ETH address
    for q in rpc.requests:
        if q["method"] in ("eth_call", "eth_getCode"):
            target = q["params"][0]["to"] if q["method"] == "eth_call" else q["params"][0]
            assert target != NATIVE


@pytest.mark.parametrize("breakage,reason", [
    (lambda rpc: rpc.code.pop(SV), "V4_STATE_VIEW .* has no bytecode"),
    (lambda rpc: rpc.code.pop(PM), "V4_POOL_MANAGER .* has no bytecode"),
    (lambda rpc: rpc.set_call(SV, abi.SEL_V4_POOL_MANAGER, "0x" + uint_word(1)),
     "poolManager"),
    (lambda rpc: rpc.calls.pop((SV, abi.SEL_V4_GET_SLOT0 + "00" * 32)), "getSlot0"),
])
def test_stateview_verification(breakage, reason):
    rpc = FakeRpc()
    install_v4(rpc)
    assert v4reader(rpc).verify() is None
    breakage(rpc)
    assert re.search(reason, v4reader(rpc).verify())
