"""Decision-engine interface, the inert stub, plugin loading, and output validation.

Contract: ``decide(state: dict) -> {"action": "buy"|"hold"|"skip", "confidence": float, ...}``
Optional extra keys (risk_score 1-10, risk_expected, latency_ms, model, raw, error) are kept.
Default engine is Nimble 2 (decision_engine.py). ``DECISION_ENGINE=stub`` selects the stub
below; ``DECISION_ENGINE=package.module:function`` loads an external callable.
Numeric limits are NOT applied here; gates.py enforces them after the engine answers.
"""
from __future__ import annotations

import importlib
import math
from typing import Callable, Optional, Tuple

from .decision_engine import raw_snippet

ACTIONS = ("buy", "hold", "skip")
_KNOWN = {"action", "confidence", "risk_score", "risk_expected", "latency_ms", "model", "raw",
          "error"}
Engine = Callable[[dict], dict]


def decide(state: dict) -> dict:
    """Stub decision engine: always skip with zero confidence (safe default)."""
    return {"action": "skip", "confidence": 0.0}


def _finite(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def validate_decision(out: object) -> Tuple[dict, Optional[str]]:
    """Coerce arbitrary engine output into a valid decision. Returns (decision, problem)."""
    safe = {"action": "skip", "confidence": 0.0, "risk_score": None, "risk_expected": None,
            "latency_ms": None, "model": None, "raw": None, "error": None}
    if out is not None:
        safe["raw"] = {"invalid_output": raw_snippet(out)}  # keep what the engine said
    if not isinstance(out, dict):
        return safe, f"engine returned {type(out).__name__}, expected dict"
    action, conf = out.get("action"), out.get("confidence")
    if action not in ACTIONS:
        return safe, f"invalid action {action!r}"
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        return safe, f"invalid confidence {conf!r}"
    if not math.isfinite(conf) or not 0.0 <= conf <= 1.0:
        return safe, f"confidence {conf!r} outside [0,1]"
    risk = out.get("risk_score")
    risk = risk if isinstance(risk, int) and not isinstance(risk, bool) and 1 <= risk <= 10 \
        else None
    err = out.get("error")
    extras = {k: v for k, v in out.items() if k not in _KNOWN}
    raw = out.get("raw")
    if extras:  # plugin-specific keys are preserved under raw, never dropped
        raw = {"extra": extras} if raw is None else {"raw": raw, "extra": extras}
    dec = {"action": action, "confidence": float(conf), "risk_score": risk,
           "risk_expected": _finite(out.get("risk_expected")),
           "latency_ms": _finite(out.get("latency_ms")),
           "model": out.get("model") if isinstance(out.get("model"), str) else None,
           "raw": raw, "error": str(err) if err else None}
    if dec["error"]:  # an engine that reports an error can never buy
        dec["action"], dec["confidence"] = "skip", 0.0
    return dec, None


def load_engine(spec: str = "", cfg=None, transport=None) -> Engine:
    """'' -> Nimble 2 engine; 'stub' -> the inert stub; 'module:function' -> external."""
    if not spec:
        from .config import Config
        from .decision_engine import engine_from_config
        return engine_from_config(cfg or Config(), transport)
    if spec == "stub":
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
        dec, _ = validate_decision(None)
        return dec, f"engine error: {type(e).__name__}: {e}"
