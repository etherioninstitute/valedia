from decimal import Decimal

import pytest

from fakes import (FACTORY, MEME, OWNER, POOL, QUOTE, FakeRpc, add_pool, add_token,
                   bytes32_string, pool_created_log, sqrt_for, standard_scenario)
from keccak import keccak256
from rh_meme_bot import abi
from rh_meme_bot.chain import (ChainMismatchError, ChainReader, PoolWatcher,
                               decode_pool_created, price_from_sqrt)
from rh_meme_bot.rpc import RpcClient, RpcError


def reader(rpc, **kw):
    return ChainReader(RpcClient("http://fake", transport=rpc, sleep=lambda s: None, **kw), FACTORY)


def test_topic_and_selectors_match_keccak():
    assert "0x" + keccak256(b"PoolCreated(address,address,uint24,int24,address)").hex() \
        == abi.POOL_CREATED_TOPIC
    for sig, sel in [("totalSupply()", abi.SEL_TOTAL_SUPPLY), ("decimals()", abi.SEL_DECIMALS),
                     ("symbol()", abi.SEL_SYMBOL), ("owner()", abi.SEL_OWNER),
                     ("slot0()", abi.SEL_SLOT0), ("liquidity()", abi.SEL_LIQUIDITY),
                     ("token0()", abi.SEL_TOKEN0), ("token1()", abi.SEL_TOKEN1)]:
        assert "0x" + keccak256(sig.encode()).hex()[:8] == sel


def test_chain_id_mismatch_raises_clear_error():
    r = reader(FakeRpc(chain_id=1))
    with pytest.raises(ChainMismatchError, match="chain id 1, expected 4663"):
        r.verify_chain()
    assert reader(FakeRpc(chain_id=4663)).verify_chain() == 4663


def test_decode_pool_created_real_shape():
    ev = decode_pool_created(pool_created_log(MEME, QUOTE, 3000, 60, POOL, 995, 7))
    assert (ev.token0, ev.token1, ev.pool) == (MEME, QUOTE, POOL)
    assert (ev.fee, ev.tick_spacing, ev.block_number, ev.log_index) == (3000, 60, 995, 7)


def test_decode_negative_tick_spacing_and_bad_logs():
    log = pool_created_log(MEME, QUOTE, 500, -10, POOL, 1)
    assert decode_pool_created(log).tick_spacing == -10
    with pytest.raises(ValueError):
        decode_pool_created({"topics": [abi.POOL_CREATED_TOPIC], "data": "0x", "blockNumber": "0x1"})


def test_fetch_skips_bad_log_without_crashing():
    rpc = standard_scenario()
    skipped: list = []
    evs = reader(rpc).fetch_pool_created(900, 1000, skipped)
    assert len(evs) == 2 and len(skipped) == 1


def test_erc20_renounced_symbol_bytes32():
    rpc = standard_scenario()
    info = reader(rpc).token_info(MEME)
    assert info.has_bytecode and info.owner_status == "renounced"
    assert info.symbol == "MEME" and info.decimals == 18 and info.total_supply == 10 ** 27


def test_erc20_owned_and_string_symbol():
    rpc = FakeRpc()
    add_token(rpc, MEME, owner=OWNER, symbol="LONGERNAME")
    info = reader(rpc).token_info(MEME)
    assert info.owner_status == f"owned:{OWNER}" and info.symbol == "LONGERNAME"


def test_erc20_no_owner_fn_and_no_bytecode():
    rpc = FakeRpc()
    add_token(rpc, MEME, owner_fn=False)
    assert reader(rpc).token_info(MEME).owner_status == "no_owner_fn"
    ghost = reader(rpc).token_info("0x" + "99" * 20)
    assert ghost.has_bytecode is False and ghost.total_supply is None


def test_symbol_decoders():
    assert abi.decode_string_or_bytes32(bytes32_string("PEPE")) == "PEPE"
    assert abi.decode_string_or_bytes32("0x") is None
    assert abi.decode_string_or_bytes32("0x1234") is None


def test_slot0_price_math_and_decimals():
    t1_per_t0, t0_per_t1 = price_from_sqrt(2 * 2 ** 96, 18, 18)
    assert t1_per_t0 == Decimal(4) and t0_per_t1 == Decimal("0.25")
    # token0 has 18 decimals, token1 has 6: raw 4 -> 4e12 token1 per token0
    p, inv = price_from_sqrt(2 * 2 ** 96, 18, 6)
    assert p == Decimal(4) * Decimal(10) ** 12 and inv == Decimal("2.5E-13")
    assert price_from_sqrt(2 ** 160 - 1, 0, 18)[0] > 0  # max sqrtPrice: no overflow


def test_pool_reads_via_rpc():
    rpc = standard_scenario()
    r = reader(rpc)
    s0 = r.pool_slot0(POOL)
    assert s0.sqrt_price_x96 == sqrt_for(0.0005, 18, 6) and s0.tick == -76000
    assert r.pool_liquidity(POOL) == 5_000_000
    assert r.pool_tokens(POOL) == (MEME, QUOTE)
    assert float(r.pool_price(POOL, 18, 6)[0]) == pytest.approx(0.0005, rel=1e-9)
    assert r.pool_slot0("0x" + "00" * 20) is None  # reverts


def test_negative_tick():
    rpc = FakeRpc()
    add_pool(rpc, POOL, MEME, QUOTE, sqrt_price=2 ** 96, tick=-887272)
    assert reader(rpc).pool_slot0(POOL).tick == -887272


def test_watcher_chunking_and_last_block_tracking():
    rpc = FakeRpc(head=10_000)
    w = PoolWatcher(reader(rpc), lookback=4_500, max_range=2000)
    w.poll()
    assert rpc.log_ranges == [(5500, 7499), (7500, 9499), (9500, 10000)]
    assert w.last_block == 10_000
    rpc.head = 10_010
    w.poll()
    assert rpc.log_ranges[-1] == (10_001, 10_010) and w.last_block == 10_010
    n = len(rpc.log_ranges)
    w.poll()  # nothing new
    assert len(rpc.log_ranges) == n


def test_watcher_keeps_progress_on_failure_then_resumes():
    rpc = FakeRpc(head=5000)
    state = {"getlogs": 0, "break_second": True}

    def flaky(payload):
        if payload["method"] == "eth_getLogs":
            state["getlogs"] += 1
            if state["break_second"] and state["getlogs"] == 2:
                raise RuntimeError("boom")
        return rpc(payload)

    errors: list = []
    r = ChainReader(RpcClient("http://fake", transport=flaky, retries=0), FACTORY)
    w = PoolWatcher(r, lookback=3999, max_range=2000, on_error=errors.append)
    w.poll()
    assert len(errors) == 1 and w.last_block == 3000  # chunk 1 kept, chunk 2 pending
    state["break_second"] = False
    w.poll()
    assert rpc.log_ranges == [(1001, 3000), (3001, 5000)] and w.last_block == 5000


def test_rpc_retry_with_backoff_then_success_and_exhaustion():
    rpc = FakeRpc()
    sleeps: list = []
    rpc.fail_next = [ConnectionError("down"), {"code": -32005, "message": "rate limit"}]
    c = RpcClient("http://fake", transport=rpc, retries=3, backoff=0.5, sleep=sleeps.append)
    assert c.call("eth_blockNumber") == hex(1000)
    assert sleeps == [0.5, 1.0]
    rpc.fail_next = [ConnectionError("x")] * 5
    with pytest.raises(RpcError):
        RpcClient("http://fake", transport=rpc, retries=2, sleep=lambda s: None).call("eth_chainId")
    # a revert is not retried
    rpc2 = FakeRpc()
    with pytest.raises(RpcError) as ei:
        RpcClient("http://fake", transport=rpc2, retries=3, sleep=lambda s: None).call(
            "eth_call", [{"to": MEME, "data": abi.SEL_OWNER}, "latest"])
    assert ei.value.retryable is False and len(rpc2.requests) == 1


def test_balance_of_and_decimals():
    rpc = standard_scenario()
    r = reader(rpc)
    assert r.balance_of(QUOTE, POOL) == 30_000 * 10 ** 6
    assert r.balance_of(MEME, POOL) == 40_000_000 * 10 ** 18
    assert r.balance_of(QUOTE, "0x" + "00" * 20) is None  # reverts -> None
    assert r.decimals(QUOTE) == 6
    # calldata is selector 0x70a08231 + left-padded holder
    sent = [q["params"][0]["data"] for q in rpc.requests if q["method"] == "eth_call"]
    assert abi.encode_balance_of(POOL) in sent
    assert abi.encode_balance_of(POOL).startswith("0x70a08231" + "0" * 24)
    assert "0x" + keccak256(b"balanceOf(address)").hex()[:8] == abi.SEL_BALANCE_OF
