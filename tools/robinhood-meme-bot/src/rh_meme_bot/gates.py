"""Deterministic hard gates. Pure function: the model proposes, this code disposes.

Every numeric threshold lives here (via config), never in a model prompt. A paper buy happens
only if EVERY gate passes. Missing data (None) always fails: fail-closed.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .config import Config

# Gates about on-chain facts (not the model's answer). The re-check window watches pools whose
# ONLY failing fact gate is liquidity_usd (see recheck.py).
FACT_GATES = ("liquidity_usd", "owner_renounced", "contract_has_code", "entry_price",
              "v4_hooks", "v4_dynamic_fee")


def _num(v) -> Optional[float]:
    return None if isinstance(v, bool) or not isinstance(v, (int, float)) else float(v)


def evaluate_gates(decision: dict, state: dict, cfg: Config,
                   hooks: Optional[str] = None) -> Tuple[str, List[dict]]:
    """Return (final_action, gates). final_action is "buy" only if all gates pass, else "skip".

    v4 gates: hooks can tax or block sells (honeypot), so a pool with hooks passes only if its
    hooks address is in ALLOWED_V4_HOOKS; dynamic-fee pools pass only with ALLOW_DYNAMIC_FEE=1.
    ``has_hooks`` / ``dynamic_fee`` missing from the state = fail (fail-closed).
    """
    conf = _num(decision.get("confidence"))
    risk = decision.get("risk_score")
    liq = _num(state.get("liquidity_usd"))
    price = _num(state.get("price_in_quote"))
    has_hooks, dyn = state.get("has_hooks"), state.get("dynamic_fee")
    hooks_ok = has_hooks is False or (
        has_hooks is True and hooks is not None and hooks.lower() in cfg.allowed_v4_hooks)
    dyn_ok = dyn is False or (dyn is True and cfg.allow_dynamic_fee)
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
        {"gate": "v4_hooks", "value": hooks if has_hooks else has_hooks,
         "threshold": "no hooks, or hooks in ALLOWED_V4_HOOKS", "passed": hooks_ok},
        {"gate": "v4_dynamic_fee", "value": dyn,
         "threshold": "static fee, or ALLOW_DYNAMIC_FEE=1", "passed": dyn_ok},
    ]
    return ("buy" if all(g["passed"] for g in gates) else "skip"), gates


def only_liquidity_fails(gates: List[dict]) -> bool:
    """True when liquidity_usd is the only failing FACT gate. Model gates are ignored here:
    the model is asked again when the facts change."""
    failed = {g["gate"] for g in gates if not g["passed"] and g["gate"] in FACT_GATES}
    return failed == {"liquidity_usd"}


def first_failure(gates: List[dict]) -> Optional[str]:
    """Name of the first failing gate with its value, e.g. 'liquidity_usd=1200.0'."""
    for g in gates:
        if not g["passed"]:
            return f"{g['gate']}={g['value']}"
    return None
