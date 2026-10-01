import pytest

from rh_meme_bot.config import Config
from rh_meme_bot.gates import evaluate_gates, first_failure

CFG = Config()  # 20000 / 0.70 / 4
GOOD_STATE = {"liquidity_usd": 20000.0, "owner_renounced": True, "contract_has_code": True,
              "price_in_quote": 0.5}
GOOD_DEC = {"action": "buy", "confidence": 0.70, "risk_score": 4}


def run(dec=None, **st):
    return evaluate_gates({**GOOD_DEC, **(dec or {})}, {**GOOD_STATE, **st}, CFG)


def failed(gates):
    return [g["gate"] for g in gates if not g["passed"]]


def test_all_pass_at_exact_boundaries():
    final, gates = run()
    assert final == "buy" and failed(gates) == [] and len(gates) == 7
    assert first_failure(gates) is None
    assert {"gate", "value", "threshold", "passed"} <= set(gates[0])


@pytest.mark.parametrize("liq,ok", [(19999.99, False), (20000.0, True), (None, False)])
def test_liquidity_boundary(liq, ok):
    final, gates = run(liquidity_usd=liq)
    assert (final == "buy") is ok and (failed(gates) == []) is ok
    if not ok:
        assert failed(gates) == ["liquidity_usd"]


@pytest.mark.parametrize("conf,ok", [(0.6999, False), (0.70, True), (0.0, False)])
def test_confidence_boundary(conf, ok):
    final, gates = run({"confidence": conf})
    assert (final == "buy") is ok
    assert ok or failed(gates) == ["confidence"]


@pytest.mark.parametrize("risk,ok", [(4, True), (5, False), (None, False), (1, True)])
def test_risk_boundary(risk, ok):
    final, gates = run({"risk_score": risk})
    assert (final == "buy") is ok
    assert ok or failed(gates) == ["risk_score"]


def test_owner_code_and_action_gates():
    assert failed(run(owner_renounced=False)[1]) == ["owner_renounced"]
    assert failed(run(contract_has_code=False)[1]) == ["contract_has_code"]
    assert failed(run({"action": "hold"})[1]) == ["model_action"]
    assert run({"action": "skip"})[0] == "skip"
    assert failed(run(owner_renounced=None)[1]) == ["owner_renounced"]  # missing = fail
    assert failed(run(price_in_quote=None)[1]) == ["entry_price"]
    assert failed(run(price_in_quote=0.0)[1]) == ["entry_price"]


def test_empty_state_fails_closed_and_first_failure_text():
    final, gates = evaluate_gates({"action": "buy", "confidence": 0.9, "risk_score": 2}, {}, CFG)
    assert final == "skip"
    assert first_failure(gates) == "liquidity_usd=None"


def test_thresholds_come_from_config():
    cfg = Config(min_liquidity_usd=100.0, min_confidence=0.5, max_risk_score=6)
    final, _ = evaluate_gates({"action": "buy", "confidence": 0.5, "risk_score": 6},
                              {**GOOD_STATE, "liquidity_usd": 100.0}, cfg)
    assert final == "buy"
