"""Transparent risk heuristic. NOT financial advice; it only flags obvious red flags.

Score is the sum of fixed penalties, capped at 1.0 (0 = no flags found, 1 = worst):

  no contract bytecode at token address      -> 1.00 (immediate)
  token owner not renounced (owned:<addr>)   -> +0.25
  pool liquidity unreadable                  -> +0.30
  pool liquidity below MIN_LIQUIDITY         -> +0.30  (zero liquidity: +0.40)
  total supply unreadable or zero            -> +0.20
  price unreadable (slot0 failed)            -> +0.15
  symbol unreadable                          -> +0.10
  fee tier >= 1.00% (10000)                  -> +0.05

"no_owner_fn" is neutral: many safe tokens have no owner(), and so do many scams.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class RiskResult:
    score: float
    reasons: List[str] = field(default_factory=list)


def compute_risk(state: dict, min_liquidity: int) -> RiskResult:
    if not state.get("has_bytecode"):
        return RiskResult(1.0, ["no bytecode at token address"])
    score, reasons = 0.0, []

    def flag(pts: float, why: str) -> None:
        nonlocal score
        score += pts
        reasons.append(why)

    if str(state.get("owner_status", "")).startswith("owned:"):
        flag(0.25, "owner not renounced")
    liq = state.get("liquidity")
    if liq is None:
        flag(0.30, "liquidity unreadable")
    elif liq == 0:
        flag(0.40, "zero liquidity")
    elif liq < min_liquidity:
        flag(0.30, f"liquidity {liq} below minimum {min_liquidity}")
    if not state.get("total_supply"):
        flag(0.20, "total supply unreadable or zero")
    if state.get("price_token1_per_token0") is None:
        flag(0.15, "price unreadable")
    if not state.get("symbol"):
        flag(0.10, "symbol unreadable")
    if (state.get("fee") or 0) >= 10000:
        flag(0.05, "high fee tier")
    return RiskResult(round(min(1.0, score), 4), reasons)
