"""Bounded offline research derived from ATLAS Janus; never an execution authority.

Upstream cf4349f15c9d68a67042f792973372f2e0231088, General Intelligence
Capital MIT framework scope. See docs/ATLAS-LICENSE.txt. No upstream runtime
imports, placeholder outcome scoring, provider calls, state or self-modification.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import re

class ResearchRefused(ValueError):
    pass

def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", value):
        raise ResearchRefused("bounded explicit identity required")
    return value

def _number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResearchRefused("numeric input required")
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ResearchRefused("finite bounded number required")
    return value

def _utc(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ResearchRefused("explicit UTC timestamp required")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ResearchRefused("invalid timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset().total_seconds() != 0:
        raise ResearchRefused("UTC timestamp required")
    return dt

def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ResearchRefused("canonical research value required") from exc

def _digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()

@dataclass(frozen=True)
class ResearchConfig:
    enable_cohorts: bool = False
    enable_scenarios: bool = False
    min_weight: float = 0.2
    max_weight: float = 0.8
    max_age_seconds: int = 86400

    def validate(self):
        if type(self.enable_cohorts) is not bool or type(self.enable_scenarios) is not bool:
            raise ResearchRefused("feature flags require strict booleans")
        _number(self.min_weight, 0, 1)
        _number(self.max_weight, 0, 1)
        if self.min_weight > self.max_weight:
            raise ResearchRefused("invalid weight bounds")
        if type(self.max_age_seconds) is not int or not 1 <= self.max_age_seconds <= 604800:
            raise ResearchRefused("bounded research freshness required")

@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    source_id: str
    sha256: str
    observed_at: str

@dataclass(frozen=True)
class Recommendation:
    cohort: str
    instrument: str
    direction: str
    conviction: float
    as_of: str
    evidence: tuple[Evidence, ...]

@dataclass(frozen=True)
class DevelopmentOutcome:
    outcome_id: str
    cohort: str
    signed_return: float
    predicted_at: str
    matured_at: str
    evidence: tuple[Evidence, ...]
    split: str = "development"

@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    label: str
    assumptions: str
    as_of: str
    horizon_end: str
    evidence: tuple[Evidence, ...]

@dataclass(frozen=True)
class ResearchArtifact:
    as_of: str
    weights: tuple[tuple[str, float], ...]
    signals: tuple[tuple[str, float], ...]
    disagreement: tuple[str, ...]
    evidence: tuple[Evidence, ...]
    outcomes: tuple[DevelopmentOutcome, ...]
    recommendations: tuple[Recommendation, ...]
    scenarios: tuple[Scenario, ...]
    config: ResearchConfig
    schema: str = "valedia.atlas.research.v1"

    def payload(self):
        return asdict(self)

    @property
    def artifact_id(self):
        return _digest(self.payload())

def _evidence(items, cutoff):
    if not isinstance(items, tuple) or not 1 <= len(items) <= 32:
        raise ResearchRefused("bounded nonempty evidence tuple required")
    seen = set()
    for e in items:
        if type(e) is not Evidence:
            raise ResearchRefused("typed evidence required")
        _id(e.evidence_id); _id(e.source_id)
        if e.evidence_id in seen or not isinstance(e.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", e.sha256):
            raise ResearchRefused("unique evidence and content hash required")
        if _utc(e.observed_at) > cutoff:
            raise ResearchRefused("future evidence refused")
        seen.add(e.evidence_id)

def bounded_weights(scores, minimum=0.2, maximum=0.8):
    """Euclidean projection of softmax onto a feasible bounded simplex."""
    if not isinstance(scores, dict) or not 2 <= len(scores) <= 5:
        raise ResearchRefused("two to five cohorts required")
    lo = _number(minimum, 0, 1); hi = _number(maximum, 0, 1)
    n = len(scores)
    if lo > hi or n * lo > 1 + 1e-12 or n * hi < 1 - 1e-12:
        raise ResearchRefused("infeasible weight bounds")
    keys = sorted(scores)
    for k in keys: _id(k); _number(scores[k], -1, 1)
    top = max(scores.values())
    raw = [math.exp(scores[k] - top) for k in keys]
    total = sum(raw); raw = [x / total for x in raw]
    left = min(x - hi for x in raw) - 1
    right = max(x - lo for x in raw) + 1
    for _ in range(100):
        shift = (left + right) / 2
        if sum(min(hi, max(lo, x - shift)) for x in raw) > 1: left = shift
        else: right = shift
    weights = [min(hi, max(lo, x - (left + right) / 2)) for x in raw]
    residual = 1 - sum(weights)
    for i in range(n):
        change = min(hi - weights[i], max(lo - weights[i], residual))
        weights[i] += change; residual -= change
    if abs(sum(weights) - 1) > 1e-12 or any(x < lo - 1e-12 or x > hi + 1e-12 for x in weights):
        raise ResearchRefused("weight projection failed")
    return tuple(zip(keys, weights))

def build_research(config, *, as_of, recommendations=(), outcomes=(), scenarios=()):
    if type(config) is not ResearchConfig: raise ResearchRefused("trusted typed config required")
    config.validate(); cutoff = _utc(as_of)
    for rows in (recommendations, outcomes, scenarios):
        if not isinstance(rows, tuple) or len(rows) > 100: raise ResearchRefused("bounded typed tuples required")
    if (recommendations or outcomes) and not config.enable_cohorts: raise ResearchRefused("cohort feature disabled")
    if scenarios and not config.enable_scenarios: raise ResearchRefused("scenario feature disabled")
    evidence = {}; cohorts = set(); rec_keys = set()
    def collect(items, at):
        _evidence(items, at)
        for e in items:
            if e.evidence_id in evidence and evidence[e.evidence_id] != e: raise ResearchRefused("evidence identity drift")
            evidence[e.evidence_id] = e
    for r in recommendations:
        if type(r) is not Recommendation: raise ResearchRefused("typed recommendation required")
        _id(r.cohort); _id(r.instrument); _number(r.conviction, 0, 1)
        if r.direction not in ("BUY", "SELL", "HOLD"): raise ResearchRefused("unsupported direction")
        at = _utc(r.as_of)
        if not 0 <= (cutoff - at).total_seconds() <= config.max_age_seconds: raise ResearchRefused("stale/future recommendation")
        key = (r.cohort, r.instrument)
        if key in rec_keys: raise ResearchRefused("duplicate recommendation")
        rec_keys.add(key); cohorts.add(r.cohort); collect(r.evidence, at)
    scores = {c: [] for c in cohorts}; outcome_ids = set()
    for o in outcomes:
        if type(o) is not DevelopmentOutcome: raise ResearchRefused("typed development outcome required")
        _id(o.outcome_id)
        if o.outcome_id in outcome_ids or o.cohort not in cohorts or o.split != "development": raise ResearchRefused("duplicate/foreign/non-development outcome")
        outcome_ids.add(o.outcome_id)
        predicted = _utc(o.predicted_at); matured = _utc(o.matured_at)
        if not predicted < matured <= cutoff: raise ResearchRefused("outcome not matured before decision")
        collect(o.evidence, matured); scores[o.cohort].append(_number(o.signed_return, -1, 1))
    if recommendations and any(not x for x in scores.values()): raise ResearchRefused("measured matured development outcomes required for every cohort")
    weights = bounded_weights({c: sum(v) / len(v) for c,v in scores.items()}, config.min_weight, config.max_weight) if recommendations else ()
    w = dict(weights); signals = {}; directions = {}
    for r in recommendations:
        sign = {"BUY": 1, "SELL": -1, "HOLD": 0}[r.direction]
        signals[r.instrument] = signals.get(r.instrument, 0) + w[r.cohort] * sign * r.conviction
        directions.setdefault(r.instrument, set()).add(r.direction)
    scenario_ids = set()
    for s in scenarios:
        if type(s) is not Scenario: raise ResearchRefused("typed hypothetical scenario required")
        _id(s.scenario_id)
        if s.scenario_id in scenario_ids or s.label not in ("hypothetical-bull", "hypothetical-base", "hypothetical-bear"): raise ResearchRefused("unique explicit hypothetical label required")
        scenario_ids.add(s.scenario_id)
        if not isinstance(s.assumptions, str) or not 1 <= len(s.assumptions) <= 2000: raise ResearchRefused("bounded assumptions required")
        at = _utc(s.as_of); end = _utc(s.horizon_end)
        if not 0 <= (cutoff-at).total_seconds() <= config.max_age_seconds or not cutoff < end or (end-at).days > 366: raise ResearchRefused("bounded future hypothesis horizon required")
        collect(s.evidence, at)
    return ResearchArtifact(as_of, weights, tuple(sorted(signals.items())), tuple(sorted(k for k,v in directions.items() if len(v)>1)), tuple(evidence[k] for k in sorted(evidence)), tuple(sorted(outcomes,key=lambda x:x.outcome_id)), tuple(sorted(recommendations,key=lambda x:(x.cohort,x.instrument))), tuple(sorted(scenarios,key=lambda x:x.scenario_id)), config)

@dataclass(frozen=True)
class ReviewedBrainInputs:
    instrument: str
    platform: str
    fused_score: float
    expected_return_pct: float
    track_record_mult: float
    risk_factor: float
    max_pct: float
    pos_multiplier: float
    concurrent_bets: int
    regime: str = "normal"

def prepare_brain_leg(artifact, inputs):
    """Research-to-proposal only. Trusted policy, Journal and transport stay external.

    ReviewedBrainInputs must originate in the trusted caller, never model JSON.
    This function grants no permission and cannot execute, approve or size orders.
    """
    if type(artifact) is not ResearchArtifact or type(inputs) is not ReviewedBrainInputs:
        raise ResearchRefused("validated research and reviewed caller inputs required")
    # Rebuild to refuse hand-constructed/mutated artifacts before preparation.
    checked = build_research(artifact.config, as_of=artifact.as_of, recommendations=artifact.recommendations, outcomes=artifact.outcomes, scenarios=artifact.scenarios)
    if checked != artifact or not artifact.config.enable_cohorts: raise ResearchRefused("validated enabled research required")
    if dict(artifact.signals).get(inputs.instrument, 0) <= 0: raise ResearchRefused("only positive research-backed prepared buys supported")
    _id(inputs.instrument)
    if inputs.platform not in ("coinbase", "alpaca"): raise ResearchRefused("unsupported prepared venue")
    for name, low, high in [("fused_score",0,100),("expected_return_pct",0,100),("track_record_mult",0,2),("risk_factor",0,1),("max_pct",0,0.04),("pos_multiplier",0,1)]:
        _number(getattr(inputs,name),low,high)
    if type(inputs.concurrent_bets) is not int or not 1 <= inputs.concurrent_bets <= 20 or inputs.regime not in ("normal", "bull", "bear", "volatile"):
        raise ResearchRefused("bounded reviewed sizing inputs required")
    leg = asdict(inputs); leg["ticker"] = leg.pop("instrument")
    leg["leg_id"] = "atlas-" + _digest({"artifact":artifact.artifact_id,"inputs":leg})[:48]
    return leg
