"""Pluggable decision engine and the post-decision config gates.

Contract: ``decide(state: dict) -> {"action": "buy"|"hold"|"skip", "confidence": float}``.
The built-in stub is deliberately inert (always skip). Wire a real model with
``DECISION_ENGINE=package.module:function``.
"""
from __future__ import annotations

import importlib
import math
from typing import Callable, Optional, Tuple

ACTIONS = ("buy", "hold", "skip")
Engine = Callable[[dict], dict]


def decide(state: dict) -> dict:
    """Stub decision engine: always skip with zero confidence (safe default)."""
    return {"action": "skip", "confidence": 0.0}


def validate_decision(out: object) -> Tuple[dict, Optional[str]]:
    """Coerce arbitrary engine output into a valid decision. Returns (decision, problem)."""
    safe = {"action": "skip", "confidence": 0.0}
    if not isinstance(out, dict):
        return safe, f"engine returned {type(out).__name__}, expected dict"
    action, conf = out.get("action"), out.get("confidence")
    if action not in ACTIONS:
        return safe, f"invalid action {action!r}"
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        return safe, f"invalid confidence {conf!r}"
    if not math.isfinite(conf) or not 0.0 <= conf <= 1.0:
        return safe, f"confidence {conf!r} outside [0,1]"
    return {"action": action, "confidence": float(conf)}, None


def load_engine(spec: str = "") -> Engine:
    """Resolve 'module:function' to a callable; empty spec returns the stub."""
    if not spec:
        return decide
    module_name, _, func_name = spec.partition(":")
    try:
        fn = getattr(importlib.import_module(module_name), func_name)
    except (ImportError, AttributeError) as e:
        raise ValueError(f"cannot load DECISION_ENGINE {spec!r}: {e}") from e
    if not callable(fn):
        raise ValueError(f"DECISION_ENGINE {spec!r} is not callable")
    return fn


def run_engine(engine: Engine, state: dict) -> Tuple[dict, Optional[str]]:
    """Call the engine; never raises. Bad output or exceptions become a skip + problem."""
    try:
        return validate_decision(engine(dict(state)))
    except Exception as e:  # noqa: BLE001 - external model code must not crash the loop
        return {"action": "skip", "confidence": 0.0}, f"engine error: {type(e).__name__}: {e}"


def apply_gates(decision: dict, state: dict, *, min_confidence: float,
                max_risk_score: float, min_liquidity: int) -> Tuple[str, Optional[str]]:
    """Return (final_action, reason). Only a 'buy' can be downgraded, to 'skip'."""
    if decision["action"] != "buy":
        return decision["action"], None
    reasons = []
    if decision["confidence"] < min_confidence:
        reasons.append(f"confidence {decision['confidence']:.2f} < {min_confidence:.2f}")
    risk = state.get("risk_score")
    if risk is None or risk > max_risk_score:
        reasons.append(f"risk {risk} > {max_risk_score:.2f}")
    liq = state.get("liquidity")
    if liq is None or liq < min_liquidity:
        reasons.append(f"liquidity {liq} < {min_liquidity}")
    if reasons:
        return "skip", "gated: " + "; ".join(reasons)
    return "buy", None
