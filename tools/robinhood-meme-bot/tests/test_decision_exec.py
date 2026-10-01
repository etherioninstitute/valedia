import json
from decimal import Decimal

import pytest

from rh_meme_bot.config import ConfigError, load_config
from rh_meme_bot.decision import (apply_gates, decide, load_engine, run_engine,
                                  validate_decision)
from rh_meme_bot.executor import LiveSwapExecutor, PaperExecutor
from rh_meme_bot.risk import compute_risk

GOOD = {"risk_score": 0.1, "liquidity": 10_000_000}
GATES = dict(min_confidence=0.7, max_risk_score=0.5, min_liquidity=1_000_000)


def test_stub_is_safe_skip():
    assert decide({}) == {"action": "skip", "confidence": 0.0}
    assert load_engine("") is decide


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
    assert dec == {"action": "skip", "confidence": 0.0} and problem


def test_valid_output_passes_and_engine_exception_is_contained():
    assert validate_decision({"action": "hold", "confidence": 1})[0] == \
        {"action": "hold", "confidence": 1.0}
    dec, problem = run_engine(load_engine("plugin_engine:explode"), {})
    assert dec["action"] == "skip" and "model offline" in problem


def test_gating_downgrades_with_reasons():
    buy = {"action": "buy", "confidence": 0.9}
    assert apply_gates(buy, GOOD, **GATES) == ("buy", None)
    act, why = apply_gates({"action": "buy", "confidence": 0.5}, GOOD, **GATES)
    assert act == "skip" and "confidence" in why
    act, why = apply_gates(buy, {**GOOD, "risk_score": 0.9}, **GATES)
    assert act == "skip" and "risk" in why
    act, why = apply_gates(buy, {**GOOD, "liquidity": 5}, **GATES)
    assert act == "skip" and "liquidity" in why
    assert apply_gates({"action": "hold", "confidence": 0.1}, GOOD, **GATES) == ("hold", None)


def test_risk_heuristic():
    clean = {"has_bytecode": True, "owner_status": "renounced", "liquidity": 10,
             "total_supply": 1, "price_token1_per_token0": 1.0, "symbol": "X", "fee": 3000}
    assert compute_risk(clean, 5).score == 0.0
    assert compute_risk({**clean, "has_bytecode": False}, 5).score == 1.0
    r = compute_risk({**clean, "owner_status": "owned:0x1", "liquidity": 1}, 5)
    assert r.score == 0.55 and len(r.reasons) == 2


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
    cfg = load_config(env={"MIN_CONFIDENCE": "0.9", "MAX_BLOCK_RANGE": "500",
                           "QUOTE_TOKENS": "0x" + "ab" * 20})
    assert cfg.min_confidence == 0.9 and cfg.max_block_range == 500 and len(cfg.quote_tokens) == 1
    with pytest.raises(ConfigError) as ei:
        load_config(env={"MIN_CONFIDENCE": "1.5", "POLL_INTERVAL_SECONDS": "abc",
                         "FACTORY_ADDRESS": "0x12", "RPC_URL": "ftp://x",
                         "MAX_BLOCK_RANGE": "0", "DECISION_ENGINE": "nocolon"})
    msg = str(ei.value)
    for key in ("MIN_CONFIDENCE", "POLL_INTERVAL_SECONDS", "FACTORY_ADDRESS", "RPC_URL",
                "MAX_BLOCK_RANGE", "DECISION_ENGINE"):
        assert key in msg
