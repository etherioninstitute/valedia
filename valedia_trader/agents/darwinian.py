"""Darwinian agent weighting, reflexivity monitoring and the decision committee (risk officer and
allocator). Adapted from the ATLAS idea of weighting research cohorts by measured outcomes; see
THIRD_PARTY.md. Everything here is deterministic: language-model agents plug in as sleeves or as
opinions scored by the same rules."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from valedia_trader.quant_engine import level8_portfolio_risk as risk

__all__ = ["DarwinianWeights", "rolling_sharpe_scores", "brier_score", "ReflexivityMonitor", "LoopState",
           "REFLEXIVE_LOOPS", "RiskLimits", "RiskOfficer", "RiskVerdict", "DecisionCommittee", "Decision"]


# ---------------------------------------------------------------- Darwinian weights
@dataclass
class DarwinianWeights:
    """Each agent carries a weight in [lo, hi]. After every scoring round the top quartile is
    multiplied by `up` and the bottom quartile by `down`. Agents without a usable score are untouched."""
    names: Sequence[str]
    lo: float = 0.3
    hi: float = 2.5
    up: float = 1.05
    down: float = 0.95
    weights: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.names or len(set(self.names)) != len(self.names):
            raise ValueError("agent names must be unique and non-empty")
        if not 0 < self.lo <= 1.0 <= self.hi or not 0 < self.down <= 1.0 <= self.up:
            raise ValueError("require 0 < lo <= 1 <= hi and 0 < down <= 1 <= up")
        self.weights = {n: float(min(max(self.weights.get(n, 1.0), self.lo), self.hi)) for n in self.names}

    def update(self, scores: Mapping[str, float]) -> dict[str, float]:
        """Apply one round. Higher score is better. Returns the new weights."""
        usable = {n: float(scores[n]) for n in self.names if n in scores and np.isfinite(scores[n])}
        if len(usable) >= 2:
            vals = np.array(list(usable.values()))
            q25, q75, med = np.quantile(vals, 0.25), np.quantile(vals, 0.75), np.median(vals)
            for n, s in usable.items():
                if s >= q75 and s > med:
                    self.weights[n] = min(self.weights[n] * self.up, self.hi)
                elif s <= q25 and s < med:
                    self.weights[n] = max(self.weights[n] * self.down, self.lo)
        return dict(self.weights)

    def shares(self) -> dict[str, float]:
        """Capital shares proportional to the weights (they sum to 1)."""
        total = sum(self.weights.values())
        return {n: w / total for n, w in self.weights.items()}


def rolling_sharpe_scores(returns: pd.DataFrame, window: int, periods_per_year: float = 252.0) -> dict[str, float]:
    """Annualized Sharpe of each column over the last `window` rows (NaN when it cannot be computed)."""
    tail = returns.tail(window)
    out = {}
    for col in tail.columns:
        r = tail[col].dropna().to_numpy(dtype=float)
        sd = r.std(ddof=1) if r.size >= max(5, window // 2) else 0.0
        out[str(col)] = float(r.mean() / sd * math.sqrt(periods_per_year)) if sd > 1e-15 else math.nan
    return out


def brier_score(probabilities, outcomes) -> float:
    """Brier score of probability forecasts (lower is better); score prediction agents with its negative."""
    p, y = np.asarray(probabilities, dtype=float).ravel(), np.asarray(outcomes, dtype=float).ravel()
    if p.size != y.size or p.size == 0 or np.any((p < 0) | (p > 1)) or np.any((y != 0) & (y != 1)):
        raise ValueError("need probabilities in [0, 1] and binary outcomes of equal length")
    return float(np.mean((p - y) ** 2))


# ---------------------------------------------------------------- reflexivity
REFLEXIVE_LOOPS = ("price_fundamentals", "pnl_behavior", "narrative_flows", "market_policy")


@dataclass(frozen=True)
class LoopState:
    loop: str
    direction: int       # +1 reinforcing upward, -1 downward, 0 inactive
    streak: int          # consecutive rounds in that direction
    reversal_risk: bool


class ReflexivityMonitor:
    """Tracks Soros-style feedback loops (price -> fundamentals, P&L -> behavior, narrative -> flows,
    market -> policy). The fifth element is reversal detection: a loop that has run `reversal_rounds`
    or more consecutive rounds in one direction is flagged as at risk of reversing."""

    def __init__(self, reversal_rounds: int = 5, loops: Sequence[str] = REFLEXIVE_LOOPS) -> None:
        if reversal_rounds < 2:
            raise ValueError("reversal_rounds must be >= 2")
        self.reversal_rounds = reversal_rounds
        self._state = {name: (0, 0) for name in loops}

    def observe(self, loop: str, direction: float) -> LoopState:
        if loop not in self._state:
            raise KeyError(f"unknown loop '{loop}'")
        d = 0 if not np.isfinite(direction) or direction == 0 else (1 if direction > 0 else -1)
        prev_d, streak = self._state[loop]
        streak = 0 if d == 0 else (streak + 1 if d == prev_d else 1)
        self._state[loop] = (d, streak)
        return LoopState(loop, d, streak, streak >= self.reversal_rounds)

    def state(self) -> dict[str, LoopState]:
        return {k: LoopState(k, d, s, s >= self.reversal_rounds) for k, (d, s) in self._state.items()}

    def reversal_risk(self) -> bool:
        return any(s >= self.reversal_rounds for _, s in self._state.values())


# ---------------------------------------------------------------- risk officer
@dataclass(frozen=True)
class RiskLimits:
    max_daily_loss: float = 0.03
    max_var: float = 0.025         # one-day historical VaR at var_alpha, fraction of equity
    max_es: float = 0.035
    max_drawdown: float = 0.15
    var_alpha: float = 0.95
    min_history: int = 60


@dataclass(frozen=True)
class RiskVerdict:
    approved: bool
    weights: np.ndarray           # approved (possibly scaled) weights, or zeros on a veto
    scale: float                  # 1.0 untouched, < 1 resized, 0 vetoed
    reasons: tuple[str, ...]
    var: float
    es: float


class RiskOfficer:
    """Adversarial pre-trade review with veto power. A proposal whose historical VaR or ES exceeds
    the limits is resized to fit; a breach of the realized daily-loss or drawdown limits (or unusable
    inputs) vetoes all exposure and latches until an explicit reset."""

    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()
        self.breaker = risk.DrawdownCircuitBreaker(self.limits.max_daily_loss, self.limits.max_var * 1.0001,
                                                   self.limits.max_es * 1.0001, self.limits.max_drawdown)

    def review(self, proposed_weights, asset_return_history, equity: float, daily_return: float) -> RiskVerdict:
        w = np.asarray(proposed_weights, dtype=float).ravel()
        hist = np.asarray(asset_return_history, dtype=float)
        flat = np.zeros_like(w)
        if hist.ndim != 2 or hist.shape[1] != w.size or hist.shape[0] < self.limits.min_history or not np.all(
                np.isfinite(w)):
            self.breaker.check(equity, daily_return, math.nan, math.nan)
            return RiskVerdict(False, flat, 0.0, ("insufficient or invalid risk inputs",), math.nan, math.nan)
        port = np.nan_to_num(hist, nan=0.0) @ w
        if np.ptp(port) == 0.0:
            var = es = 0.0
        else:
            var = max(risk.var_historical(port, self.limits.var_alpha), 0.0)
            es = max(risk.es_historical(port, self.limits.var_alpha), 0.0)
        scale = min(1.0, self.limits.max_var / var if var > 0 else 1.0, self.limits.max_es / es if es > 0 else 1.0)
        decision = self.breaker.check(equity, daily_return, var * scale, es * scale)
        if decision.breached:
            return RiskVerdict(False, flat, 0.0, decision.reasons, var, es)
        reasons = () if scale == 1.0 else (f"resized to {scale:.2f} to fit VaR/ES limits",)
        return RiskVerdict(True, w * scale, float(scale), reasons, var * scale, es * scale)

    def reset(self, authorized_by: str, equity: float) -> None:
        self.breaker.reset(authorized_by, new_peak_equity=equity)


# ---------------------------------------------------------------- allocator and committee
@dataclass(frozen=True)
class Decision:
    weights: np.ndarray
    shares: dict[str, float]
    leverage: float
    kelly_multiplier: float
    verdict: RiskVerdict


class DecisionCommittee:
    """Allocator (CIO) plus risk officer (CRO). The allocator blends sleeve targets by their Darwinian
    capital shares and sizes the blend with fractional Kelly from the blend's own trailing record;
    the risk officer then resizes or vetoes."""

    def __init__(self, darwin: DarwinianWeights, risk_officer: RiskOfficer, max_gross: float = 1.5,
                 base_kelly_multiplier: float = 0.25,
                 regime_multipliers: Mapping[str, float] | None = None) -> None:
        if max_gross <= 0:
            raise ValueError("max_gross must be positive")
        self.darwin, self.cro, self.max_gross = darwin, risk_officer, max_gross
        self.base_multiplier, self.regime_multipliers = base_kelly_multiplier, dict(regime_multipliers or {})

    def kelly_multiplier(self, regime_probs: Mapping[str, float] | None, reflexive_reversal: bool) -> float:
        if reflexive_reversal:
            return risk.KELLY_MULTIPLIER_MIN
        if regime_probs and self.regime_multipliers:
            return risk.regime_kelly_multiplier(dict(regime_probs), self.regime_multipliers)
        return risk.clamp_kelly_multiplier(self.base_multiplier)

    def decide(self, sleeve_targets: Mapping[str, np.ndarray], sleeve_returns: pd.DataFrame,
               asset_return_history, equity: float, daily_return: float,
               regime_probs: Mapping[str, float] | None = None, reflexive_reversal: bool = False) -> Decision:
        shares = self.darwin.shares()
        blend = sum(shares[n] * np.asarray(sleeve_targets[n], dtype=float) for n in self.darwin.names)
        blend = np.where(np.isfinite(blend), blend, 0.0)
        track = (sleeve_returns[list(self.darwin.names)].fillna(0.0).to_numpy() @ np.array(
            [shares[n] for n in self.darwin.names]))
        mult = self.kelly_multiplier(regime_probs, reflexive_reversal)
        var = float(np.var(track, ddof=1)) if track.size > 20 else 0.0
        full_kelly = max(float(track.mean()), 0.0) / var if var > 1e-12 else 0.0
        leverage = float(risk.fractional_kelly(full_kelly, mult))
        proposal = risk.apply_leverage_cap(blend * leverage, self.max_gross)
        verdict = self.cro.review(proposal, asset_return_history, equity, daily_return)
        gross = float(np.abs(blend).sum())
        return Decision(verdict.weights, shares, float(np.abs(verdict.weights).sum() / gross) if gross > 0 else 0.0,
                        mult, verdict)
