#!/usr/bin/env python3
"""Append-only decision log + latency budget for the System One shadow paths.

Every shadow decision (movers scan and Jev shadow, any backend) is one JSON
line, yes and no alike, so a review can read what the model said no to as
well as yes to. Default data/systemone/decisions.jsonl; VALEDIA_SYSTEMONE_LOG
overrides. No keys, no headers. Never raises into the caller.
"""
import json
import math
import os
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DEFAULT_LOG = os.path.join(ROOT, "data", "systemone", "decisions.jsonl")
BUDGET_MS_DEFAULT = 500.0  # a warm decision should land well inside half a second


def budget_ms():
    try:
        return max(1.0, float(os.environ.get("VALEDIA_SYSTEMONE_BUDGET_MS", BUDGET_MS_DEFAULT)))
    except (TypeError, ValueError):
        return BUDGET_MS_DEFAULT


def finite_tree(obj):
    """Copy with NaN/Infinity as None. Ollama and strict JSON reject them."""
    if isinstance(obj, dict):
        return {str(k): finite_tree(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [finite_tree(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def log_path():
    explicit = os.environ.get("VALEDIA_SYSTEMONE_LOG")
    if explicit:
        return explicit
    # Unit tests set OPENCLAW_SKIP_ENV=1: they must not write the repo's real log.
    return "" if os.environ.get("OPENCLAW_SKIP_ENV") == "1" else DEFAULT_LOG


def log_decision(source, backend, model, state, response, error, answers,
                 latency_ms, label=None, cold=None):
    """Append one JSON line per decision, yes and no alike. Never raises.

    Full state sent and full raw response (or the error) are kept so a review
    can set what the model said beside the code's own label. No keys or headers.
    """
    try:
        path = log_path()
        if not path:
            return
        budget = budget_ms()
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
            "source": source, "backend": backend, "model": model,
            "state": state, "response": response, "error": error,
            "answers": answers, "latency_ms": latency_ms, "budget_ms": budget,
            "within_budget": bool(error is None and latency_ms is not None and latency_ms <= budget),
            "cold": cold, "label": label, "create_order": False,
        }
        data = (json.dumps(finite_tree(rec), default=str, allow_nan=False) + "\n").encode()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            while data:
                data = data[os.write(fd, data):]
        finally:
            os.close(fd)
    except Exception:
        pass
