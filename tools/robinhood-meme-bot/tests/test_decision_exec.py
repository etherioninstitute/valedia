import json
from decimal import Decimal

import pytest

from rh_meme_bot.config import ConfigError, load_config
from rh_meme_bot.decision import decide, load_engine, run_engine, validate_decision
from rh_meme_bot.decision_engine import NimbleEngine
from rh_meme_bot.executor import LiveSwapExecutor, PaperExecutor
from rh_meme_bot.risk import risk_flags


def test_stub_is_safe_skip_and_selectable():
    assert decide({}) == {"action": "skip", "confidence": 0.0}
    assert load_engine("stub") is decide
    assert load_engine("rh_meme_bot.decision:decide") is decide


def test_default_engine_is_nimble2():
    eng = load_engine("")
    assert isinstance(eng, NimbleEngine) and eng.model == "nimble2"


def test_plugin_loading_and_errors():
    fn = load_engine("plugin_engine:always_buy")
    assert fn({})["action"] == "buy"
    with pytest.raises(ValueError, match="cannot load"):
        load_engine("no_such_module_xyz:f")
    with pytest.raises(ValueError, match="cannot load"):
        load_engine("plugin_engine:missing")


@pytest.mark.parametrize("bad", [None, "buy", {"action": "moon", "confidence": 0.5},
                                 {"action": "buy", "confidence": 1.5},
                                 {"action": "buy", "confidence": -0.1},
                                 {"action": "buy", "confidence": float("nan")},
                                 {"action": "buy", "confidence": True},
                                 {"action": "buy"}])
def test_invalid_output_coerced_to_skip(bad):
    dec, problem = validate_decision(bad)
    assert dec["action"] == "skip" and dec["confidence"] == 0.0 and problem


def test_valid_output_extras_and_error_forces_skip():
    dec, problem = validate_decision({"action": "hold", "confidence": 1, "risk_score": 3,
                                      "latency_ms": 12.5, "model": "nimble2", "raw": {"a": 1}})
    assert problem is None and dec["risk_score"] == 3 and dec["latency_ms"] == 12.5
    assert dec["raw"] == {"a": 1}
    assert validate_decision({"action": "buy", "confidence": 0.9, "risk_score": 11})[0][
        "risk_score"] is None
    dec, _ = validate_decision({"action": "buy", "confidence": 0.9, "error": "down"})
    assert dec["action"] == "skip" and dec["confidence"] == 0.0 and dec["error"] == "down"


def test_engine_exception_is_contained():
    dec, problem = run_engine(load_engine("plugin_engine:explode"), {})
    assert dec["action"] == "skip" and "model offline" in problem


def test_risk_flags_informational_booleans():
    clean = {"contract_has_code": True, "owner_status": "renounced", "liquidity_usd": 5.0,
             "total_supply": 1.0, "price_in_quote": 1.0, "symbol": "X", "fee_tier": 3000}
    assert not any(risk_flags(clean).values())
    bad = {"contract_has_code": False, "owner_status": "owned:0x1", "fee_tier": 10000}
    f = risk_flags(bad)
    assert f["flag_no_bytecode"] and f["flag_owner_active"] and f["flag_high_fee_tier"]
    assert f["flag_liquidity_unknown"] and f["flag_supply_unusable"]


def test_paper_executor_writes_jsonl(tmp_path):
    path = tmp_path / "sub" / "pos.jsonl"
    ex = PaperExecutor(str(path), clock=lambda: "2026-01-01T00:00:00+00:00")
    p = ex.buy("0xpool", "0xtoken", 100.0, Decimal("0.000123"))
    ex.buy("0xpool2", "0xtoken2", 50.0, Decimal("2"))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 2 and ex.positions[0] == p
    assert rows[0] == {"id": 1, "timestamp": "2026-01-01T00:00:00+00:00", "pool": "0xpool",
                       "token": "0xtoken", "size": 100.0, "entry_price": "0.000123",
                       "simulated": True}
    with pytest.raises(ValueError):
        ex.buy("p", "t", 0, Decimal(1))
    with pytest.raises(ValueError):
        ex.buy("p", "t", 1, Decimal(0))


def test_live_swap_stub_raises():
    with pytest.raises(NotImplementedError, match="not implemented"):
        LiveSwapExecutor().swap("p", "t", 1.0, Decimal(1))
    with pytest.raises(NotImplementedError):
        LiveSwapExecutor().buy("p", "t", 1.0, Decimal(1))


def test_config_defaults_and_validation():
    cfg = load_config(env={})
    assert cfg.chain_id == 4663 and cfg.rpc_url.endswith("chain.robinhood.com")
    assert cfg.factory_address == "0x1f7d7550b1b028f7571e69a784071f0205fd2efa"
    assert (cfg.min_liquidity_usd, cfg.min_confidence, cfg.max_risk_score) == (20000.0, 0.70, 4)
    assert (cfg.decision_timeout_s, cfg.decision_latency_budget_ms) == (2.0, 500.0)
    assert cfg.nimble_url == "http://127.0.0.1:11434/v1/systemone" and cfg.nimble_model == "nimble2"
    assert cfg.nimble_allow_remote is False and cfg.decisions_log == "decisions.jsonl"
    a, b = "0x" + "ab" * 20, "0x" + "cd" * 20
    cfg = load_config(env={"MIN_CONFIDENCE": "0.9", "MAX_BLOCK_RANGE": "500",
                           "MAX_RISK_SCORE": "6", "NIMBLE_ALLOW_REMOTE": "1",
                           "QUOTE_TOKENS": f"{a}:USDC:stable, {b}:WETH:pool:{a}"})
    assert cfg.min_confidence == 0.9 and cfg.max_block_range == 500 and cfg.max_risk_score == 6
    assert cfg.nimble_allow_remote is True
    assert cfg.quote_tokens[a] == {"symbol": "USDC", "usd": "stable"}
    assert cfg.quote_tokens[b] == {"symbol": "WETH", "usd": f"pool:{a}"}


def test_config_rejects_bad_values():
    with pytest.raises(ConfigError) as ei:
        load_config(env={"MIN_CONFIDENCE": "1.5", "POLL_INTERVAL_SECONDS": "abc",
                         "FACTORY_ADDRESS": "0x12", "RPC_URL": "ftp://x",
                         "MAX_BLOCK_RANGE": "0", "DECISION_ENGINE": "nocolon",
                         "MAX_RISK_SCORE": "11", "MIN_LIQUIDITY_USD": "-1",
                         "DECISION_TIMEOUT_S": "0", "NIMBLE_ALLOW_REMOTE": "maybe",
                         "QUOTE_TOKENS": "0x12:USDC:stable", "NIMBLE_URL": "x"})
    msg = str(ei.value)
    for key in ("MIN_CONFIDENCE", "POLL_INTERVAL_SECONDS", "FACTORY_ADDRESS", "RPC_URL",
                "MAX_BLOCK_RANGE", "DECISION_ENGINE", "MAX_RISK_SCORE", "MIN_LIQUIDITY_USD",
                "DECISION_TIMEOUT_S", "NIMBLE_ALLOW_REMOTE", "QUOTE_TOKENS", "NIMBLE_URL"):
        assert key in msg
    with pytest.raises(ConfigError):
        load_config(env={"QUOTE_TOKENS": f"{'0x' + 'ab' * 20}:USDC:pool"})  # pool needs address
