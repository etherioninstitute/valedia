import pytest

from rh_meme_bot.config import Config
from rh_meme_bot.gates import evaluate_gates, first_failure

CFG = Config()  # 20000 / 0.70 / 4
GOOD_STATE = {"liquidity_usd": 20000.0, "owner_renounced": True, "contract_has_code": True,
              "price_in_quote": 0.5, "has_hooks": False, "dynamic_fee": False}
GOOD_DEC = {"action": "buy", "confidence": 0.70, "risk_score": 4}


def run(dec=None, **st):
    return evaluate_gates({**GOOD_DEC, **(dec or {})}, {**GOOD_STATE, **st}, CFG)


def failed(gates):
    return [g["gate"] for g in gates if not g["passed"]]


def test_all_pass_at_exact_boundaries():
    final, gates = run()
    assert final == "buy" and failed(gates) == [] and len(gates) == 9
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


HOOK = "0x" + "40" * 19 + "c0"


def test_hooks_gate_blocks_unless_allowlisted():
    final, gates = evaluate_gates(GOOD_DEC, {**GOOD_STATE, "has_hooks": True}, CFG, HOOK)
    assert final == "skip" and failed(gates) == ["v4_hooks"]
    assert next(g for g in gates if g["gate"] == "v4_hooks")["value"] == HOOK
    allow = Config(allowed_v4_hooks=frozenset({HOOK}))
    assert evaluate_gates(GOOD_DEC, {**GOOD_STATE, "has_hooks": True}, allow,
                          HOOK.upper().replace("0X", "0x"))[0] == "buy"
    other = "0x" + "41" * 20
    assert failed(evaluate_gates(GOOD_DEC, {**GOOD_STATE, "has_hooks": True}, allow,
                                 other)[1]) == ["v4_hooks"]
    assert failed(run(has_hooks=None)[1]) == ["v4_hooks"]  # unknown = fail-closed


def test_dynamic_fee_gate_needs_opt_in():
    assert failed(run(dynamic_fee=True)[1]) == ["v4_dynamic_fee"]
    assert evaluate_gates(GOOD_DEC, {**GOOD_STATE, "dynamic_fee": True},
                          Config(allow_dynamic_fee=True))[0] == "buy"
    assert failed(run(dynamic_fee=None)[1]) == ["v4_dynamic_fee"]


def test_only_liquidity_fails_ignores_model_gates():
    from rh_meme_bot.gates import only_liquidity_fails
    assert only_liquidity_fails(run({"action": "skip"}, liquidity_usd=0.0)[1])
    assert not only_liquidity_fails(run(liquidity_usd=0.0, owner_renounced=False)[1])
    assert not only_liquidity_fails(run()[1])
    assert not only_liquidity_fails(run(liquidity_usd=0.0, has_hooks=True)[1])
