from decimal import Decimal

import pytest

from fakes import FACTORY, FakeRpc, add_token, sqrt_for
from fakes_v4 import (USDG, WETH, WETH_POOL_3000, install_v4, install_weth_usdg, pool_id,
                      set_v4_pool)
from rh_meme_bot import abi
from rh_meme_bot.chain import ChainReader
from rh_meme_bot.config import ConfigError, load_config
from rh_meme_bot.profiles import NATIVE, PROFILES, profile_for
from rh_meme_bot.quotes import QuoteUsd, discover_weth_pool, quote_usd
from rh_meme_bot.rpc import RpcClient
from rh_meme_bot.v4 import V4Reader


def reader(rpc, chain_id=4663):
    return ChainReader(RpcClient("http://fake", transport=rpc, sleep=lambda s: None), FACTORY,
                       chain_id)


def test_mainnet_profile_addresses_and_defaults():
    p = PROFILES[4663]
    assert p.weth == "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
    assert p.usdg == "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
    assert p.v4_pool_manager == "0x8366a39cc670b4001a1121b8f6a443a643e40951"
    assert p.v4_state_view == "0xf3334192d15450cdd385c8b70e03f9a6bd9e673b"
    cfg = load_config(env={})
    assert cfg.quote_tokens[USDG] == {"symbol": "USDG", "usd": "stable"}
    assert cfg.quote_tokens[WETH] == {"symbol": "WETH", "usd": "weth"}
    assert cfg.quote_tokens[NATIVE] == {"symbol": "ETH", "usd": "weth"}
    assert (cfg.v4_pool_manager, cfg.v4_state_view) == (p.v4_pool_manager, p.v4_state_view)


def test_testnet_46630_profile_has_weth_but_no_usdg_so_weth_usd_unknown():
    p = profile_for(46630)
    assert p.weth == "0x7943e237c7f95da44e0301572d358911207852fa" and p.usdg is None
    cfg = load_config(env={"CHAIN_ID": "46630"})
    assert set(cfg.quote_tokens) == {p.weth, NATIVE} and cfg.v4_pool_manager == ""
    assert quote_usd(p.weth, p, reader(FakeRpc(chain_id=46630), 46630)) is None
    assert profile_for(1).quote_tokens() == {}  # unknown chain: no built-ins


def test_quote_tokens_env_overrides_and_extends_profile():
    extra = "0x" + "ab" * 20
    cfg = load_config(env={"QUOTE_TOKENS": f"{extra}:USDX:stable,{WETH}:WETH:pool:{extra}"})
    assert cfg.quote_tokens[extra]["usd"] == "stable"
    assert cfg.quote_tokens[WETH]["usd"] == f"pool:{extra}"  # override
    assert cfg.quote_tokens[USDG]["usd"] == "stable"  # built-in kept


def test_getpool_discovery_picks_deepest_usdg_pool():
    rpc = FakeRpc()
    install_weth_usdg(rpc, FACTORY, price=2000.0, deep_fee=3000)
    pool, bal = discover_weth_pool(reader(rpc), WETH, USDG)
    assert pool == WETH_POOL_3000 and bal == 5_000_000 * 10 ** 6
    p = PROFILES[4663]
    assert float(quote_usd(WETH, p, reader(rpc))) == pytest.approx(2000.0, rel=1e-6)
    assert float(quote_usd(NATIVE, p, reader(rpc))) == pytest.approx(2000.0, rel=1e-6)
    assert quote_usd(USDG, p, reader(rpc)) == Decimal(1)
    sent = [q["params"][0]["data"] for q in rpc.requests if q["method"] == "eth_call"]
    for fee in (100, 500, 3000, 10000):  # every tier asked, nothing hardcoded
        assert abi.encode_get_pool(WETH, USDG, fee) in sent


def test_no_weth_pool_means_unknown_and_named_usdc_is_not_stable():
    rpc = FakeRpc()
    p = PROFILES[4663]
    assert quote_usd(WETH, p, reader(rpc)) is None  # getPool reverts / zero everywhere
    fake_usdc = "0x" + "bc" * 20
    add_token(rpc, fake_usdc, decimals=6, symbol="USDC", owner_fn=False)
    assert quote_usd(fake_usdc, p, reader(rpc)) is None  # a name is not a price
    usd = QuoteUsd(reader(rpc), load_config(env={}).quote_tokens, profile=p)
    assert usd.usd(fake_usdc) is None and usd.usd(WETH) is None and usd.usd(USDG) == 1


def test_weth_and_native_share_one_cached_price():
    rpc = FakeRpc()
    install_weth_usdg(rpc, FACTORY)
    usd = QuoteUsd(reader(rpc), load_config(env={}).quote_tokens, profile=PROFILES[4663],
                   clock=lambda: 0.0)
    assert float(usd.usd(WETH)) == pytest.approx(2000.0, rel=1e-6)
    n = len(rpc.requests)
    assert usd.usd(NATIVE) == usd.usd(WETH) and len(rpc.requests) == n


def test_v4_fallback_when_no_v3_weth_pool():
    rpc = FakeRpc()
    install_v4(rpc)
    add_token(rpc, USDG, decimals=6, symbol="USDG", owner_fn=False)
    p = PROFILES[4663]
    pid_small = pool_id(WETH, USDG, 500, 10)
    pid_big = pool_id(WETH, USDG, 3000, 60)
    set_v4_pool(rpc, pid_small, sqrt_for(1500, 18, 6), 0, 10 ** 10)
    set_v4_pool(rpc, pid_big, sqrt_for(2100, 18, 6), 0, 10 ** 15)  # deepest in-range L
    v4 = V4Reader(RpcClient("http://fake", transport=rpc, sleep=lambda s: None),
                  p.v4_pool_manager, p.v4_state_view)
    assert quote_usd(WETH, p, reader(rpc)) is None  # v4 not allowed without a reader
    assert float(quote_usd(WETH, p, reader(rpc), v4=v4)) == pytest.approx(2100.0, rel=1e-6)


def test_v4_and_recheck_config_validation():
    cfg = load_config(env={"ALLOWED_V4_HOOKS": "0x" + "AB" * 20, "ALLOW_DYNAMIC_FEE": "1",
                           "RECHECK_BLOCKS": "0", "V4_POOL_MANAGER": "0x" + "11" * 20})
    assert cfg.allowed_v4_hooks == frozenset({"0x" + "ab" * 20}) and cfg.allow_dynamic_fee
    assert cfg.recheck_blocks == 0 and cfg.v4_pool_manager == "0x" + "11" * 20
    assert load_config(env={}).recheck_blocks == 300
    with pytest.raises(ConfigError) as ei:
        load_config(env={"ALLOWED_V4_HOOKS": "0x12", "RECHECK_BLOCKS": "-1",
                         "V4_STATE_VIEW": "nope", "NIMBLE_DIGEST": "xyz"})
    for key in ("ALLOWED_V4_HOOKS", "RECHECK_BLOCKS", "V4_STATE_VIEW", "NIMBLE_DIGEST"):
        assert key in str(ei.value)
