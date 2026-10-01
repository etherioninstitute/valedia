"""Example external decision engines used by tests (and a template for real ones)."""


def always_buy(state: dict) -> dict:
    return {"action": "buy", "confidence": 0.95}


def bad_output(state: dict) -> dict:
    return {"action": "moon", "confidence": 7}


def explode(state: dict) -> dict:
    raise RuntimeError("model offline")
