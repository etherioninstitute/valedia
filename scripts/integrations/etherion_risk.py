"""ERE v3 (ere/2): deterministic, offline risk measurement, not an alpha model.

Returns are SIMPLE fractional returns, not log returns. For N>1 explicit weights
and stable asset IDs are required. Weights are signed fractions of NAV, not
renormalized. Baseline prices must precede the entire monitored short window.
Pass actual UTC bar END times. A 1-D date vector asserts alignment; a T-by-N
vector additionally checks it. No array API can detect falsely labelled rows.
No economic deployment approval is implied by a valid result.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Mapping
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


def _f(default, provenance):
    return field(default=default, metadata={"provenance": provenance})


@dataclass(frozen=True)
class EREConfig:
    version: str = "ERE v3 (ere/2)"
    config_id: str = "ere3-research-unapproved"
    assets: tuple[str, ...] = ()
    short: int = _f(10, "PROTOCOL.md: fixed before holdout; ROC sensitivity required")
    reference: int = _f(120, "PROTOCOL.md: disjoint six-month reference, not PDE constant")
    half_life: float = _f(5.0, "PROTOCOL.md: fixed recent exponential weights")
    cut_q: float = _f(0.95, "2020-2023 ROC + independent null margin; see frozen_thresholds.json")
    halt_q: float = _f(1.0, "2020-2023 ROC: historical maximum; no distribution-free tail guarantee")
    min_scores: int = _f(252, "Minimum reference sample policy; not a tail guarantee")
    max_history: int = _f(4096, "Bounded baseline storage; deterministic oldest-first eviction")
    max_gross: float = _f(1.0, "Unlevered operating scope; independent capital-risk policy")
    max_bar_age_s: float = _f(345600.0, "Four calendar days; sleeve/session checks belong in adapter")
    heartbeat_ttl_s: float = _f(5400.0, "Hourly producer, 30-minute lateness allowance; operational policy")
    position_ttl_s: float = _f(5400.0, "Holdings snapshot maximum age; invalidate on every fill")
    feed_grace_s: float = _f(900.0, "Completed-bar publication grace; operational policy, not forecast")
    baseline_embargo: int = _f(60, "Annual reference embargo covers longest research comparator")
    fetch_timeout_s: float = _f(20.0, "External producer network timeout; core performs no network I/O")
    caution_floor: float = _f(0.75, "Display only: not a trading threshold")
    elevated_floor: float = _f(0.50, "Display only: not a trading threshold")
    numeric_tol: float = _f(1e-12, "Dimensionless floating-point comparison tolerance")

    def __post_init__(self):
        integers = (self.short, self.reference, self.min_scores, self.max_history, self.baseline_embargo)
        if any(type(x) is not int for x in integers):
            raise ValueError("Window lengths must be integers")
        if not isinstance(self.assets, tuple) or any(not isinstance(x, str) or not x for x in self.assets):
            raise ValueError("Asset IDs must be a tuple of nonempty strings")
        nums = (self.half_life, self.cut_q, self.halt_q, self.max_gross,
                self.max_bar_age_s, self.caution_floor, self.elevated_floor,
                self.numeric_tol, self.heartbeat_ttl_s, self.position_ttl_s,
                self.feed_grace_s, self.fetch_timeout_s)
        if not all(np.isfinite(x) for x in nums):
            raise ValueError("Non-finite configuration")
        if not (self.short >= 2 and self.reference >= 2 and self.half_life > 0
                and 0 < self.cut_q < self.halt_q <= 1 and self.min_scores >= 2
                and self.max_history >= self.short + self.reference + self.min_scores
                and 0 < self.max_gross <= 1 and self.max_bar_age_s > 0
                and 0 < self.elevated_floor < self.caution_floor < 1
                and 0 < self.numeric_tol < 1 and self.heartbeat_ttl_s > 0
                and self.position_ttl_s > 0 and self.feed_grace_s >= 0
                and self.fetch_timeout_s > 0 and self.baseline_embargo >= self.short):
            raise ValueError("Invalid configuration")
        if len(set(self.assets)) != len(self.assets):
            raise ValueError("Duplicate asset IDs")

    @property
    def feature_key(self):
        return ("downside_mean/1", self.short, self.reference, self.half_life)


@dataclass(frozen=True)
class Baseline:
    """Immutable raw past sample; reprojected on the CURRENT weights at assessment.

    This is historical scenario calibration for today's exposures, NOT a claim
    that today's weights were held historically. Persist with dataclasses.asdict.
    """
    schema: str
    assets: tuple[str, ...]
    feature_key: tuple
    dates: tuple[str, ...]
    returns: tuple[tuple[float, ...], ...]


@dataclass(frozen=True)
class EREResult:
    schema: str
    asof: str
    multiplier: float
    level: str
    halt: bool
    components: dict[str, dict[str, float]]
    reasons: tuple[str, ...]
    data_quality: str
    version: str = "ERE v3 (ere/2)"
    config_id: str = "ere3-research-unapproved"
    assets: tuple[str, ...] = ()
    n_obs: int = 0


class DataError(ValueError):
    def __init__(self, quality, message):
        super().__init__(message)
        self.quality = quality


def utc(value):
    """Strict timezone-aware timestamp; no implicit local timezone or wall clock."""
    if isinstance(value, np.datetime64):
        if np.isnat(value):
            raise ValueError("NaT timestamp")
        value = str(value) + "Z"
    d = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if d.tzinfo is None or d.utcoffset() is None:
        raise ValueError("UTC-aware bar-end timestamp required")
    return d.astimezone(timezone.utc)


def _iso(d):
    return d.isoformat().replace("+00:00", "Z")


def _data(returns, dates, cfg):
    r = np.asarray(returns, dtype=float)
    if r.ndim == 1:
        r = r[:, None]
    if r.ndim != 2 or not r.shape[0] or not r.shape[1]:
        raise DataError("INSUFFICIENT", "Empty or non-matrix returns")
    if not np.isfinite(r).all() or np.any(r < -1):
        raise DataError("INSUFFICIENT", "Non-finite or impossible simple return")
    a = cfg.assets or (("ASSET",) if r.shape[1] == 1 else ())
    if len(a) != r.shape[1]:
        raise DataError("MISALIGNED", "Asset IDs required and must match columns")
    ds = np.asarray(dates, dtype=object)
    if ds.ndim == 2:
        if ds.shape != r.shape:
            raise DataError("MISALIGNED", "Date matrix shape differs from returns")
        parsed = [[utc(v) for v in row] for row in ds]
        if any(any(d != row[0] for d in row) for row in parsed):
            raise DataError("MISALIGNED", "Different bar endpoints across assets")
        ds = [row[0] for row in parsed]
    elif ds.ndim != 1:
        raise DataError("MISALIGNED", "Dates must be T or T-by-N")
    ds = tuple(utc(v) for v in ds)
    if len(ds) != len(r) or any(b <= a for a, b in zip(ds, ds[1:])):
        raise DataError("MISALIGNED", "Dates missing, duplicated or out of order")
    return r, ds, tuple(a)


def _weights(weights, n, cfg):
    if weights is None:
        if n != 1:
            raise DataError("INSUFFICIENT", "Held-book weights are mandatory")
        weights = (1.0,)
    w = np.asarray(weights, dtype=float)
    if w.shape != (n,) or not np.isfinite(w).all():
        raise DataError("MISALIGNED", "Invalid weights")
    if not (0 < np.abs(w).sum() <= cfg.max_gross + cfg.numeric_tol):
        raise DataError("INSUFFICIENT", "Zero/unsupported gross exposure")
    return w


def score_series(returns, weights, cfg=EREConfig()):
    """Causal standard lower partial moment of order one / disjoint past RMS.

    Result j ends at input bar j + reference + short - 1. Exposed for the
    offline harness; callers must validate data before interpreting the output.
    Zero reference RMS yields NaN, never an invented safe score.
    """
    r = np.asarray(returns, dtype=float)
    p = r @ np.asarray(weights) if r.ndim == 2 else r * float(np.asarray(weights)[0])
    length = cfg.reference + cfg.short
    if len(p) < length:
        return np.empty(0)
    windows = sliding_window_view(p, length)
    ref = windows[:, :cfg.reference]
    tail = windows[:, cfg.reference:]
    a = np.exp2(-np.arange(cfg.short - 1, -1, -1) / cfg.half_life)
    a = a / a.sum()
    scale = np.sqrt(np.mean(ref * ref, axis=1))
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        return np.maximum(-tail, 0) @ a / scale


def update_baseline(prev, returns_TxN, dates, cfg=EREConfig()):
    """Append ONLY genuinely observed past data. No in-place mutation or I/O.

    Overlap must be bit-identical (revisions require an explicit fresh rebuild).
    Caller freezes this before the monitored window. assess enforces separation.
    Do not filter to 'calm' days; that would bias the reference distribution.
    """
    r, ds, assets = _data(returns_TxN, dates, cfg)
    if prev is not None:
        if (prev.schema != "ere-baseline/1" or prev.assets != assets
                or prev.feature_key != cfg.feature_key):
            raise DataError("MISALIGNED", "Baseline identity/specification mismatch")
        old_r, old_ds, _ = _data(prev.returns, prev.dates, cfg)
        old = dict(zip(old_ds, old_r))
        for d, row in zip(ds, r):
            if d <= old_ds[-1] and (d not in old or not np.array_equal(row, old[d])):
                raise DataError("MISALIGNED", "Revised/backfilled baseline history")
        new = np.array([d > old_ds[-1] for d in ds])
        if not new.any():
            return prev
        r = np.concatenate((old_r, r[new]))
        ds = old_ds + tuple(d for d, keep in zip(ds, new) if keep)
    r, ds = r[-cfg.max_history:], ds[-cfg.max_history:]
    return Baseline("ere-baseline/1", assets, cfg.feature_key,
                    tuple(_iso(d) for d in ds), tuple(map(tuple, r.tolist())))


def level_for(multiplier, cfg=EREConfig()):
    if multiplier <= 0:
        return "HALT"
    if multiplier >= 1:
        return "NORMAL"
    if multiplier >= cfg.caution_floor:
        return "CAUTION"
    if multiplier >= cfg.elevated_floor:
        return "ELEVATED"
    return "WARNING"


def failure(quality="INSUFFICIENT", reason="Missing risk state", asof="", cfg=EREConfig()):
    return EREResult("ere/2", asof, 0.0, "HALT", True, {}, (reason,), quality,
                     cfg.version, cfg.config_id, cfg.assets)


def assess(returns_TxN, dates, weights=None, baseline=None, cfg=EREConfig(), *, now=None):
    """Measure at the final completed bar. now is optional for offline replay.

    Online callers must supply now and validate the session calendar separately.
    All input/data errors fail CLOSED for new risk. This does not submit sells.
    """
    asof = ""
    try:
        r, ds, assets = _data(returns_TxN, dates, cfg)
        asof = _iso(ds[-1])
        w = _weights(weights, r.shape[1], cfg)
        if len(r) < cfg.reference + cfg.short or baseline is None:
            raise DataError("INSUFFICIENT", "No independent baseline or insufficient bars")
        if now is not None:
            age = (utc(now) - ds[-1]).total_seconds()
            if age < 0 or age > cfg.max_bar_age_s:
                raise DataError("STALE", "Future or stale last completed bar")
        b, bd, ba = _data(baseline.returns, baseline.dates, cfg)
        if (baseline.schema != "ere-baseline/1" or ba != assets
                or baseline.assets != assets or baseline.feature_key != cfg.feature_key):
            raise DataError("MISALIGNED", "Baseline asset order/specification mismatch")
        if bd[-1] >= ds[-cfg.short]:
            raise DataError("MISALIGNED", "Baseline overlaps monitored window")
        scores = score_series(b, w, cfg)
        if len(scores) < cfg.min_scores or not np.isfinite(scores).all():
            raise DataError("INSUFFICIENT", "Degenerate/short reference distribution")
        value = float(score_series(r[-cfg.reference-cfg.short:], w, cfg)[0])
        if not np.isfinite(value):
            raise DataError("INSUFFICIENT", "Degenerate current volatility reference")
        low, high = np.quantile(scores, (cfg.cut_q, cfg.halt_q))
        if high - low <= cfg.numeric_tol:
            raise DataError("INSUFFICIENT", "Unresolved stress quantiles")
        m = float(np.clip((high - value) / (high - low), 0, 1))
        unique, counts = np.unique(scores, return_counts=True)
        ranks = (np.cumsum(counts) - counts / 2) / len(scores)
        pct = float(np.interp(value, unique, ranks, left=0, right=1))
        parts = {"downside_mean": {"value": value, "percentile": pct,
                    "cut_threshold": float(low), "halt_threshold": float(high),
                    "cut_quantile": cfg.cut_q, "halt_quantile": cfg.halt_q,
                    "baseline_n": float(len(scores))}}
        return EREResult("ere/2", asof, m, level_for(m, cfg), m == 0, parts,
                         ("downside_mean",) if m < 1 else (), "OK", cfg.version,
                         cfg.config_id, assets, cfg.reference + cfg.short)
    except DataError as exc:
        return failure(exc.quality, str(exc), asof, cfg)
    except (ValueError, TypeError, IndexError, AttributeError, OverflowError) as exc:
        return failure("MISALIGNED", f"Invalid input: {type(exc).__name__}", asof, cfg)


def mult_for(result, max_age_s, *, now, cfg=EREConfig()):
    """The only result-to-sizing interpretation; no hidden wall-clock read.

    State-envelope heartbeat, approved-config and holdings checks belong in the
    I/O adapter. Missing/errored/inconsistent results return ZERO, never 1.0.
    """
    try:
        d = asdict(result) if isinstance(result, EREResult) else result
        if not isinstance(d, Mapping) or not np.isfinite(max_age_s) or max_age_s <= 0:
            return 0.0
        m = d["multiplier"]
        if (isinstance(m, bool) or not isinstance(m, (float, int)) or not np.isfinite(m)
                or not 0 <= m <= 1 or d["schema"] != "ere/2"
                or d["version"] != cfg.version or d.get("config_id") != cfg.config_id
                or d["data_quality"] != "OK"
                or type(d["halt"]) is not bool or d["halt"] != (m == 0)
                or d["level"] != level_for(m, cfg)
                or d.get("risk_level", d["level"]) != d["level"]):
            return 0.0
        age = (utc(now) - utc(d["asof"])).total_seconds()
        if age < 0 or age > min(max_age_s, cfg.max_bar_age_s):
            return 0.0
        return float(m)
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
        return 0.0
