"""Deterministic hard gates. Pure function: the model proposes, this code disposes.

Every numeric threshold lives here (via config), never in a model prompt. A paper buy happens
only if EVERY gate passes. Missing data (None) always fails: fail-closed.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .config import Config


def _num(v) -> Optional[float]:
    return None if isinstance(v, bool) or not isinstance(v, (int, float)) else float(v)


def evaluate_gates(decision: dict, state: dict, cfg: Config) -> Tuple[str, List[dict]]:
    """Return (final_action, gates). final_action is "buy" only if all gates pass, else "skip"."""
    conf = _num(decision.get("confidence"))
    risk = decision.get("risk_score")
    liq = _num(state.get("liquidity_usd"))
    price = _num(state.get("price_in_quote"))
    gates = [
        {"gate": "model_action", "value": decision.get("action"), "threshold": "buy",
         "passed": decision.get("action") == "buy"},
        {"gate": "confidence", "value": conf, "threshold": f">= {cfg.min_confidence}",
         "passed": conf is not None and conf >= cfg.min_confidence},
        {"gate": "risk_score", "value": risk, "threshold": f"<= {cfg.max_risk_score}",
         "passed": isinstance(risk, int) and not isinstance(risk, bool)
         and risk <= cfg.max_risk_score},
        {"gate": "liquidity_usd", "value": liq, "threshold": f">= {cfg.min_liquidity_usd}",
         "passed": liq is not None and liq >= cfg.min_liquidity_usd},
        {"gate": "owner_renounced", "value": state.get("owner_renounced"), "threshold": True,
         "passed": state.get("owner_renounced") is True},
        {"gate": "contract_has_code", "value": state.get("contract_has_code"), "threshold": True,
         "passed": state.get("contract_has_code") is True},
        # Needed to record a paper entry price; not one of the model-facing numbers.
        {"gate": "entry_price", "value": price, "threshold": "> 0",
         "passed": price is not None and price > 0},
    ]
    return ("buy" if all(g["passed"] for g in gates) else "skip"), gates


def first_failure(gates: List[dict]) -> Optional[str]:
    """Name of the first failing gate with its value, e.g. 'liquidity_usd=1200.0'."""
    for g in gates:
        if not g["passed"]:
            return f"{g['gate']}={g['value']}"
    return None
