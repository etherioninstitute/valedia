import pytest

from fakes import (MEME, MEME2, POOL, POOL2, QUOTE, QUOTE_ENV, RUG, RUG_POOL, WETH, WETH_ENV,
                   WETH_POOL, FakeRpc, add_pool, add_token, pool_created_log, set_balance,
                   sqrt_for, standard_scenario, weth_scenario)
from rh_meme_bot import abi
from rh_meme_bot.chain import ChainReader, decode_pool_created
from rh_meme_bot.config import Config, load_config
from rh_meme_bot.gates import evaluate_gates
from rh_meme_bot.rpc import RpcClient
from rh_meme_bot.state import QuoteUsd, build_state


def setup(rpc, quotes):
    cfg = load_config(env={"QUOTE_TOKENS": quotes})
    reader = ChainReader(RpcClient("http://fake", transport=rpc, sleep=lambda s: None),
                         cfg.factory_address, cfg.chain_id)
    return cfg, reader, QuoteUsd(reader, cfg.quote_tokens)


def state_for(rpc, quotes, index=0):
    cfg, reader, usd = setup(rpc, quotes)
    ev = decode_pool_created(rpc.logs[index])
    return build_state(reader, cfg, ev, usd)


def test_stable_quote_liquidity_math():
    st, entry = state_for(standard_scenario(), QUOTE_ENV)
    assert st["token_address"] == MEME and st["pool_address"] == POOL and st["fee_tier"] == 3000
    assert st["symbol"] == "MEME" and st["decimals"] == 18 and st["quote_symbol"] == "USDC"
    assert st["total_supply"] == pytest.approx(1e9)  # 1e27 raw / 1e18
    assert st["owner_renounced"] is True and st["owner_status"] == "renounced"
    assert st["contract_has_code"] is True
    assert st["price_in_quote"] == pytest.approx(0.0005, rel=1e-9)
    assert st["price_usd"] == pytest.approx(0.0005, rel=1e-9) and float(entry) == st["price_in_quote"]
    assert st["quote_reserve_usd"] == pytest.approx(30000.0)
    assert st["liquidity_usd"] == pytest.approx(60000.0)  # 2 x quote side
    assert st["meme_reserve_usd"] == pytest.approx(20000.0, rel=1e-9)
    assert st["tvl_usd"] == pytest.approx(50000.0, rel=1e-9)  # informational only
    assert not any(st[k] for k in st if k.startswith("flag_"))


def test_pool_priced_quote_liquidity_math():
    rpc = weth_scenario()
    st, entry = state_for(rpc, f"{QUOTE_ENV},{WETH_ENV}")
    assert st["token_address"] == MEME2 and st["quote_symbol"] == "WETH"
    assert st["price_in_quote"] == pytest.approx(0.0001, rel=1e-9)
    assert st["price_usd"] == pytest.approx(0.2, rel=1e-6)  # 0.0001 WETH x 2000
    assert st["quote_reserve_usd"] == pytest.approx(20000.0, rel=1e-6)  # 10 WETH x 2000
    assert st["liquidity_usd"] == pytest.approx(40000.0, rel=1e-6)
    assert st["meme_reserve_usd"] == pytest.approx(100_000_000 * 0.2, rel=1e-6)


def test_meme_is_token1_quote_is_token0():
    rpc = FakeRpc()
    add_token(rpc, MEME, symbol="MEME")
    add_token(rpc, QUOTE, decimals=6, symbol="USDC", owner_fn=False)
    # token0 = USDC (6d), token1 = MEME (18d): 2000 MEME per USDC => 0.0005 USDC per MEME
    add_pool(rpc, POOL, QUOTE, MEME, sqrt_price=sqrt_for(2000, 6, 18))
    set_balance(rpc, QUOTE, POOL, 5_000 * 10 ** 6)
    set_balance(rpc, MEME, POOL, 10 ** 24)
    rpc.logs = [pool_created_log(QUOTE, MEME, 500, 10, POOL, 5)]
    st, entry = state_for(rpc, QUOTE_ENV)
    assert st["token_address"] == MEME and st["price_in_quote"] == pytest.approx(0.0005, rel=1e-9)
    assert st["liquidity_usd"] == pytest.approx(10000.0)


def test_unknown_quote_gives_none_and_gate_skips():
    rpc = standard_scenario()
    st, _ = state_for(rpc, "")  # no QUOTE_TOKENS configured
    assert st["liquidity_usd"] is None and st["quote_symbol"] is None
    assert st["flag_liquidity_unknown"] is True and st["price_in_quote"] is not None
    final, gates = evaluate_gates({"action": "buy", "confidence": 0.99, "risk_score": 1}, st,
                                  Config())
    assert final == "skip" and [g["gate"] for g in gates if not g["passed"]] == ["liquidity_usd"]


def test_pool_priced_quote_unreadable_is_fail_closed():
    rpc = weth_scenario()
    rpc.calls[(WETH_POOL, abi.SEL_SLOT0)] = "revert"
    st, _ = state_for(rpc, f"{QUOTE_ENV},{WETH_ENV}")
    assert st["liquidity_usd"] is None and st["price_usd"] is None
    assert st["price_in_quote"] is not None  # pool price itself still readable
    rpc2 = weth_scenario()
    rpc2.fail_next = []
    cfg, reader, usd = setup(rpc2, f"{QUOTE_ENV},{WETH_ENV}")
    rpc2.calls[(WETH_POOL, abi.SEL_TOKEN0)] = "revert"
    assert usd.usd(WETH) is None


def test_quote_usd_values_and_cache():
    rpc = weth_scenario()
    clock = [0.0]
    cfg, reader, _ = setup(rpc, f"{QUOTE_ENV},{WETH_ENV}")
    usd = QuoteUsd(reader, cfg.quote_tokens, ttl=30.0, clock=lambda: clock[0])
    assert usd.usd(QUOTE) == 1
    assert float(usd.usd(WETH)) == pytest.approx(2000.0, rel=1e-6)
    n = len(rpc.requests)
    usd.usd(WETH)
    assert len(rpc.requests) == n  # cached
    clock[0] = 31.0
    usd.usd(WETH)
    assert len(rpc.requests) > n  # expired
    assert usd.usd("0x" + "12" * 20) is None  # unconfigured quote


def test_no_owner_fn_is_not_renounced_and_no_code():
    rpc = standard_scenario()
    rpc.calls.pop((MEME, abi.SEL_OWNER))  # owner() now reverts
    st, _ = state_for(rpc, QUOTE_ENV)
    assert st["owner_renounced"] is False and st["owner_status"] == "no_owner_fn"
    st, entry = state_for(standard_scenario(), QUOTE_ENV, index=2)  # RUG: no bytecode
    assert st["token_address"] == RUG and st["pool_address"] == RUG_POOL
    assert st["contract_has_code"] is False and st["owner_renounced"] is False
    assert st["flag_no_bytecode"] is True
    assert st["liquidity_usd"] == pytest.approx(200.0)
    assert entry is None  # meme decimals unreadable -> no entry price
