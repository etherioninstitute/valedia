"""Nimble 2 questions and JSON helpers (split out of decision_engine.py; re-exported there).

The model answers narrow qualitative questions about precomputed facts. No threshold or number
appears in any question text: gates.py enforces every number afterwards.
"""
from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Any, Optional

# --- Questions -------------------------------------------------------------------------
ACTIONS = ("buy", "hold", "skip")
RISK_LABELS = [str(i) for i in range(1, 11)]

# At pool creation there is NO momentum or buy-pressure data, so the criteria only talk about
# facts the state really contains; asking about momentum would invite guessing. No threshold
# or number appears in any question text: the code gates enforce every number afterwards.
# Risk is a 10-way "choice" instead of Ollama's "score" type: the score index scale is
# ambiguous (0- vs 1-based) and an off-by-one would silently move the risk gate. With choice
# labels "1".."10", int(choice) is unambiguous and the probabilities give a distribution.
QUESTIONS = {
    "action": {
        "type": "choice",
        "instructions": ("A new token pool was just created. Using only the facts in the state, "
                         "should a small paper position be opened right now?"),
        "criteria": {
            "buy": ("Facts look like a legitimate launch: real contract code, ownership "
                    "renounced, meaningful two-sided liquidity, plausible supply and price"),
            "hold": "Facts are mixed, incomplete, or too early to judge",
            "skip": ("Red flags: no contract code, active owner, one-sided or tiny liquidity, "
                     "absurd supply or price, a custom hook contract, or a dynamic fee"),
        },
    },
    "risk_score": {
        "type": "choice",
        "instructions": ("Rate the rug-pull and scam risk of this launch from 1 (safe) to 10 "
                         "(extreme), using only the facts in the state."),
        "criteria": {
            "1": "no red flags at all", "2": "negligible concerns", "3": "minor concerns",
            "4": "some concerns but plausible", "5": "mixed picture", "6": "several red flags",
            "7": "serious red flags", "8": "many serious red flags",
            "9": "almost certainly a scam", "10": "near-certain rug pull",
        },
    },
}


RAW_LIMIT = 4096


def raw_snippet(obj: Any, limit: int = RAW_LIMIT) -> str:
    """Text form of an unparseable body / odd output, truncated for the decision log."""
    if isinstance(obj, bytes):
        obj = obj.decode("utf-8", errors="replace")
    if not isinstance(obj, str):
        try:
            obj = json.dumps(obj, default=str)
        except (TypeError, ValueError):
            obj = repr(obj)
    return obj if len(obj) <= limit else obj[:limit] + f"...[truncated {len(obj) - limit} chars]"


def sanitize(obj: Any) -> Any:
    """JSON-safe copy: NaN/inf -> None, Decimal -> float, tuples -> lists, keys -> str."""
    if isinstance(obj, bool) or obj is None or isinstance(obj, (int, str)):
        return obj
    if isinstance(obj, Decimal):
        obj = float(obj)
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [sanitize(v) for v in obj]
    return str(obj)


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _risk_expected(probs: Any) -> Optional[float]:
    """sum(k * p_k) over the risk labels (renormalised); None if unusable."""
    if not isinstance(probs, dict) or not probs:
        return None
    total = acc = 0.0
    for label, p in probs.items():
        pv = _num(p)
        if label not in RISK_LABELS or pv is None or pv < 0:
            return None
        total += pv
        acc += int(label) * pv
    return round(acc / total, 4) if total > 0 else None
