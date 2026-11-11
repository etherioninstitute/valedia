"""Level 10: research hygiene and machine learning. Everything that feeds a decision is
point-in-time and causal, and every acceptance gate fails closed."""
from __future__ import annotations

import json
import math
import sqlite3
import warnings
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Sequence

import numpy as np
import pandas as pd
import xgboost as xgb
from hmmlearn.hmm import GaussianHMM
from scipy import stats
from scipy.special import logsumexp

__all__ = [
    "LookAheadError", "CostModelError", "PointInTimeStore", "universe_as_of", "detect_lookahead",
    "check_cost_completeness", "require_complete_costs", "REQUIRED_COST_TERMS", "walk_forward_splits",
    "PurgedKFold", "expected_max_sharpe", "probabilistic_sharpe_ratio", "deflated_sharpe_ratio",
    "sharpe_moments", "SharpeMoments", "TrialRegistry", "dsr_gate", "DSRDecision", "precision_recall_f1",
    "triple_barrier_labels", "bet_size_from_probability", "meta_label_fit", "MetaLabelResult", "fit_hmm",
    "RegimeModel", "filtered_probabilities", "causal_regime_probabilities", "regime_capital_multipliers",
]

FORMULAS: dict[str, tuple[str, ...]] = {
    "10.1": ("PointInTimeStore", "universe_as_of", "detect_lookahead", "check_cost_completeness",
             "require_complete_costs"),
    "10.2": ("walk_forward_splits", "PurgedKFold"),
    "10.3": ("deflated_sharpe_ratio", "expected_max_sharpe", "probabilistic_sharpe_ratio", "sharpe_moments",
             "TrialRegistry", "dsr_gate"),
    "10.4": ("precision_recall_f1", "triple_barrier_labels", "meta_label_fit", "bet_size_from_probability"),
    "10.5": ("fit_hmm", "filtered_probabilities", "causal_regime_probabilities", "regime_capital_multipliers"),
}

EULER_GAMMA = 0.5772156649015329
REQUIRED_COST_TERMS = ("fees", "slippage", "borrow", "funding")


# ---------------------------------------------------------------- 10.1 bias firewall
class LookAheadError(RuntimeError):
    """Raised when code asks for data that was not yet known at the backtest clock."""


class CostModelError(ValueError):
    """Raised when a backtest configuration omits a cost term."""


class PointInTimeStore:
    """10.1 Point-in-time data access. Every record carries when the event happened and when it
    became known; queries only ever return rows known at or before the (forward-only) clock."""

    def __init__(self, event_col: str = "event_time", knowledge_col: str = "knowledge_time") -> None:
        self.event_col, self.knowledge_col = event_col, knowledge_col
        self._data = pd.DataFrame()
        self._clock: pd.Timestamp | None = None

    def add(self, records: pd.DataFrame) -> None:
        for col in (self.event_col, self.knowledge_col):
            if col not in records.columns:
                raise ValueError(f"records need a '{col}' column")
        df = records.copy()
        df[self.event_col] = pd.to_datetime(df[self.event_col])
        df[self.knowledge_col] = pd.to_datetime(df[self.knowledge_col])
        if df[[self.event_col, self.knowledge_col]].isna().any().any():
            raise ValueError("event and knowledge timestamps must not be missing")
        if (df[self.knowledge_col] < df[self.event_col]).any():
            raise ValueError("a record cannot be known before its event happens")
        self._data = pd.concat([self._data, df], ignore_index=True).sort_values(
            self.knowledge_col, kind="stable").reset_index(drop=True)

    @property
    def clock(self) -> pd.Timestamp | None:
        return self._clock

    def set_clock(self, ts) -> None:
        ts = pd.Timestamp(ts)
        if self._clock is not None and ts < self._clock:
            raise LookAheadError("the backtest clock only moves forward")
        self._clock = ts

    def as_of(self, ts=None, latest_by: str | Sequence[str] | None = None) -> pd.DataFrame:
        """Rows known at or before `ts` (default: the clock). Reading past the clock raises."""
        if self._clock is None:
            raise LookAheadError("set the clock before reading")
        ts = self._clock if ts is None else pd.Timestamp(ts)
        if ts > self._clock:
            raise LookAheadError(f"requested {ts} but the clock is at {self._clock}")
        out = self._data[self._data[self.knowledge_col] <= ts]
        if latest_by is not None:
            out = out.groupby(latest_by, sort=False).tail(1)
        return out.reset_index(drop=True)


def universe_as_of(membership: pd.DataFrame, ts) -> list[str]:
    """10.1 Survivorship-safe universe: symbols that were members at `ts`, including names delisted
    later. `membership` has columns symbol, start, end (end missing while still listed)."""
    for col in ("symbol", "start", "end"):
        if col not in membership.columns:
            raise ValueError("membership needs columns symbol, start, end")
    ts = pd.Timestamp(ts)
    start, end = pd.to_datetime(membership["start"]), pd.to_datetime(membership["end"])
    alive = (start <= ts) & (end.isna() | (ts < end))
    return sorted(membership.loc[alive, "symbol"].unique().tolist())


def detect_lookahead(feature_fn: Callable[[pd.DataFrame], pd.DataFrame], data: pd.DataFrame, n_checks: int = 8,
                     rtol: float = 1e-9, atol: float = 1e-12) -> list[str]:
    """10.1 Look-ahead detector: a feature leaks if its value at t changes when the data after t is
    removed. Returns the names of leaking feature columns (empty list = clean)."""
    n = len(data)
    if n < 30:
        raise ValueError("need at least 30 rows to test for look-ahead")
    full = feature_fn(data)
    if len(full) != n:
        raise ValueError("feature_fn must return one row per input row")
    leaks: set[str] = set()
    for t in np.unique(np.linspace(n // 3, n - 2, n_checks).astype(int)):
        part = feature_fn(data.iloc[: t + 1])
        a, b = full.iloc[t], part.iloc[-1]
        for col in full.columns:
            x, y = a[col], b[col]
            if pd.isna(x) and pd.isna(y):
                continue
            if pd.isna(x) != pd.isna(y) or not np.isclose(float(x), float(y), rtol=rtol, atol=atol):
                leaks.add(str(col))
    return sorted(leaks)


def check_cost_completeness(config: dict[str, Any]) -> list[str]:
    """10.1 Cost terms missing from a backtest config (fees, slippage, borrow, funding).

    An explicit 0 is accepted (for example funding on cash equities); absent, None or NaN is not."""
    missing = []
    for term in REQUIRED_COST_TERMS:
        v = config.get(term)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            missing.append(term)
    return missing


def require_complete_costs(config: dict[str, Any]) -> None:
    """10.1 Refuse to run a backtest whose cost model is incomplete."""
    missing = check_cost_completeness(config)
    if missing:
        raise CostModelError("backtest cost model is missing: " + ", ".join(missing))


# ---------------------------------------------------------------- 10.2 walk-forward and purged CV
def walk_forward_splits(n_samples: int, train_size: int, test_size: int, step: int | None = None,
                        expanding: bool = False, embargo: int = 0) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """10.2 Walk-forward splits: train strictly before test, with an optional embargo gap between them."""
    if min(n_samples, train_size, test_size) < 1 or embargo < 0:
        raise ValueError("sizes must be positive and embargo non-negative")
    step = test_size if step is None else step
    if step < 1:
        raise ValueError("step must be >= 1")
    end_train = train_size
    while end_train + embargo + test_size <= n_samples:
        start = 0 if expanding else end_train - train_size
        yield (np.arange(start, end_train),
               np.arange(end_train + embargo, end_train + embargo + test_size))
        end_train += step


class PurgedKFold:
    """10.2 K-fold for overlapping labels (Lopez de Prado). Sample i's label spans [i, t1[i]].
    Training samples whose label interval overlaps the test fold are purged, and `embargo`
    samples after each test fold are dropped. scikit-learn splitter interface."""

    def __init__(self, n_splits: int = 5, label_horizon: int = 0, embargo: int = 0) -> None:
        if n_splits < 2 or label_horizon < 0 or embargo < 0:
            raise ValueError("n_splits must be >= 2; label_horizon and embargo non-negative")
        self.n_splits, self.label_horizon, self.embargo = n_splits, label_horizon, embargo

    def get_n_splits(self, X=None, y=None, groups=None) -> int:
        return self.n_splits

    def split(self, X, y=None, groups=None, t1=None) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        n = len(X)
        if n < self.n_splits:
            raise ValueError("more splits than samples")
        idx = np.arange(n)
        ends = np.minimum(idx + self.label_horizon, n - 1) if t1 is None else np.asarray(t1, dtype=int)
        if ends.shape != (n,) or np.any(ends < idx):
            raise ValueError("t1 must give, for every sample, an end index >= its own index")
        for test in np.array_split(idx, self.n_splits):
            first, last_label = test[0], int(ends[test].max())
            train = idx[(ends < first) | (idx > last_label + self.embargo)]
            yield train, test


# ---------------------------------------------------------------- 10.3 deflated Sharpe
def expected_max_sharpe(n_trials: int, sharpe_variance: float) -> float:
    """10.3 Expected maximum Sharpe under the null across N trials:
    SR_0 = sqrt(V) * ((1 - g) Phi^-1(1 - 1/N) + g Phi^-1(1 - 1/(N e))), g = Euler-Mascheroni."""
    if n_trials < 2:
        raise ValueError("need at least 2 trials")
    if sharpe_variance < 0 or not np.isfinite(sharpe_variance):
        raise ValueError("sharpe_variance must be finite and non-negative")
    z1 = stats.norm.ppf(1.0 - 1.0 / n_trials)
    z2 = stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(sharpe_variance) * ((1.0 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def probabilistic_sharpe_ratio(sharpe: float, benchmark_sharpe: float, n_obs: int, skew: float = 0.0,
                               kurt: float = 3.0) -> float:
    """10.3 Probabilistic Sharpe ratio: P(true SR > benchmark) allowing for skew and fat tails.

    `sharpe` and `benchmark_sharpe` are per-period (not annualized); kurt is non-excess (3 = normal)."""
    if n_obs < 2:
        raise ValueError("need at least 2 observations")
    den = 1.0 - skew * sharpe + (kurt - 1.0) / 4.0 * sharpe**2
    if den <= 0 or not np.isfinite(den):
        raise ValueError("Sharpe standard-error term is not positive; moments are inconsistent")
    return float(stats.norm.cdf((sharpe - benchmark_sharpe) * math.sqrt(n_obs - 1) / math.sqrt(den)))


def deflated_sharpe_ratio(sharpe: float, n_obs: int, n_trials: int, sharpe_variance: float, skew: float = 0.0,
                          kurt: float = 3.0) -> float:
    """10.3 Deflated Sharpe ratio (Bailey and Lopez de Prado):
    DSR = Phi((SR - SR_0) sqrt(T - 1) / sqrt(1 - g3 SR + (g4 - 1)/4 SR^2))."""
    return probabilistic_sharpe_ratio(sharpe, expected_max_sharpe(n_trials, sharpe_variance), n_obs, skew, kurt)


@dataclass(frozen=True)
class SharpeMoments:
    sharpe: float     # per period
    skew: float
    kurt: float       # non-excess
    n_obs: int


def sharpe_moments(returns) -> SharpeMoments:
    """10.3 Per-period Sharpe, skewness and (non-excess) kurtosis of a return stream."""
    r = np.asarray(returns, dtype=float).ravel()
    if r.size < 3 or not np.all(np.isfinite(r)):
        raise ValueError("need at least 3 finite returns")
    sd = r.std(ddof=1)
    if sd <= 1e-12 * max(1.0, float(np.max(np.abs(r)))):
        raise ValueError("returns have zero variance; Sharpe is undefined")
    return SharpeMoments(float(r.mean() / sd), float(stats.skew(r)), float(stats.kurtosis(r, fisher=False)), r.size)


class TrialRegistry:
    """10.3 Experiment database: every strategy variant tried is recorded so the number of trials N
    and the variance of their Sharpe ratios come from data, not from memory."""

    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path)
        self._db.execute("CREATE TABLE IF NOT EXISTS trials (id INTEGER PRIMARY KEY, strategy TEXT NOT NULL, "
                         "sharpe REAL NOT NULL, n_obs INTEGER NOT NULL, params TEXT, "
                         "recorded_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        self._db.commit()

    def record(self, strategy: str, sharpe: float, n_obs: int, params: dict[str, Any] | None = None) -> int:
        if not strategy or not np.isfinite(sharpe) or n_obs < 2:
            raise ValueError("a trial needs a name, a finite per-period Sharpe and n_obs >= 2")
        cur = self._db.execute("INSERT INTO trials (strategy, sharpe, n_obs, params) VALUES (?, ?, ?, ?)",
                               (strategy, float(sharpe), int(n_obs), json.dumps(params or {}, sort_keys=True)))
        self._db.commit()
        return int(cur.lastrowid)

    def trial_sharpes(self) -> np.ndarray:
        return np.array([row[0] for row in self._db.execute("SELECT sharpe FROM trials ORDER BY id")], dtype=float)

    def n_trials(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM trials").fetchone()[0])

    def sharpe_variance(self) -> float:
        s = self.trial_sharpes()
        if s.size < 2:
            raise ValueError("need at least 2 recorded trials")
        return float(s.var(ddof=1))

    def close(self) -> None:
        self._db.close()


@dataclass(frozen=True)
class DSRDecision:
    accepted: bool
    dsr: float
    sharpe: float
    benchmark_sharpe: float
    n_trials: int
    reason: str


def dsr_gate(returns, registry: TrialRegistry, threshold: float = 0.95, min_obs: int = 30) -> DSRDecision:
    """10.3 Accept a strategy only if its Deflated Sharpe ratio reaches `threshold`. Fails closed when
    the registry has fewer than 2 trials, there are too few observations, or moments are unusable."""
    n_trials = registry.n_trials()
    try:
        m = sharpe_moments(returns)
    except ValueError as exc:
        return DSRDecision(False, 0.0, 0.0, math.nan, n_trials, f"rejected: {exc}")
    if n_trials < 2:
        return DSRDecision(False, 0.0, m.sharpe, math.nan, n_trials, "rejected: fewer than 2 recorded trials")
    if m.n_obs < min_obs:
        return DSRDecision(False, 0.0, m.sharpe, math.nan, n_trials, f"rejected: only {m.n_obs} observations")
    sr0 = expected_max_sharpe(n_trials, registry.sharpe_variance())
    try:
        dsr = probabilistic_sharpe_ratio(m.sharpe, sr0, m.n_obs, m.skew, m.kurt)
    except ValueError as exc:
        return DSRDecision(False, 0.0, m.sharpe, sr0, n_trials, f"rejected: {exc}")
    ok = bool(dsr >= threshold)
    return DSRDecision(ok, dsr, m.sharpe, sr0, n_trials,
                       "accepted" if ok else f"rejected: DSR {dsr:.3f} below {threshold:.2f}")


# ---------------------------------------------------------------- 10.4 meta-labelling
def precision_recall_f1(y_true, y_pred) -> tuple[float, float, float]:
    """10.4 Precision = TP/(TP+FP), recall = TP/(TP+FN), F1 = 2PR/(P+R) (0 when undefined)."""
    t, p = np.asarray(y_true).astype(bool).ravel(), np.asarray(y_pred).astype(bool).ravel()
    if t.size != p.size or t.size == 0:
        raise ValueError("y_true and y_pred must be non-empty and the same length")
    tp, fp, fn = int((t & p).sum()), int((~t & p).sum()), int((t & ~p).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def triple_barrier_labels(prices, profit_take: float, stop_loss: float, max_holding: int, side=None,
                          event_idx=None) -> tuple[np.ndarray, np.ndarray]:
    """10.4 Triple-barrier labels. For each event at i the first of: return >= profit_take (+1),
    return <= -stop_loss (-1), or max_holding bars elapsed (0). Returns (labels, t1) where t1 is the
    index at which the label became known. `side` (+1/-1) flips returns for short events."""
    p = np.asarray(prices, dtype=float).ravel()
    if p.size < 2 or np.any(p <= 0) or not np.all(np.isfinite(p)):
        raise ValueError("prices must be positive and finite")
    if profit_take <= 0 or stop_loss <= 0 or max_holding < 1:
        raise ValueError("barriers must be positive and max_holding >= 1")
    events = np.arange(p.size) if event_idx is None else np.asarray(event_idx, dtype=int)
    sides = np.ones(events.size) if side is None else np.asarray(side, dtype=float).ravel()
    if sides.size != events.size:
        raise ValueError("side must have one entry per event")
    labels, t1 = np.zeros(events.size, dtype=int), np.empty(events.size, dtype=int)
    for k, i in enumerate(events):
        end = min(i + max_holding, p.size - 1)
        path = sides[k] * (p[i + 1: end + 1] / p[i] - 1.0)
        hit = np.flatnonzero((path >= profit_take) | (path <= -stop_loss))
        if hit.size:
            labels[k] = 1 if path[hit[0]] >= profit_take else -1
            t1[k] = i + 1 + hit[0]
        else:
            t1[k] = end
    return labels, t1


def bet_size_from_probability(probability, threshold: float = 0.5):
    """10.4 Map a predicted probability to a position size in [0, 1]: 2 Phi((p - 0.5)/sqrt(p(1-p))) - 1,
    and exactly 0 at or below `threshold` or for unusable probabilities."""
    if not 0.5 <= threshold < 1.0:
        raise ValueError("threshold must be in [0.5, 1)")
    p = np.asarray(probability, dtype=float)
    ok = np.isfinite(p) & (p > threshold) & (p <= 1.0)
    q = np.clip(np.where(ok, p, 0.75), 1e-9, 1.0 - 1e-9)
    size = np.clip(2.0 * stats.norm.cdf((q - 0.5) / np.sqrt(q * (1.0 - q))) - 1.0, 0.0, 1.0)
    out = np.where(ok, size, 0.0)
    return float(out) if out.ndim == 0 else out


@dataclass(frozen=True)
class MetaLabelResult:
    oof_probability: np.ndarray   # out-of-fold P(primary signal is right), NaN where never tested
    bet_size: np.ndarray
    precision_primary: float
    precision_meta: float
    recall_meta: float
    f1_meta: float
    model: Any                    # classifier refitted on all events


def meta_label_fit(features, primary_side, outcomes, t1=None, n_splits: int = 5, label_horizon: int = 0,
                   embargo: int = 0, probability_threshold: float = 0.5, random_state: int = 0,
                   xgb_params: dict[str, Any] | None = None) -> MetaLabelResult:
    """10.4 Meta-labelling (Lopez de Prado): a secondary XGBoost model learns when the primary
    signal's side is right, scored out-of-fold with purged, embargoed cross-validation.

    features (n, d); primary_side +1/-1 per event; outcomes 1 if the primary call made money."""
    x = np.asarray(features, dtype=float)
    x = x.reshape(-1, 1) if x.ndim == 1 else x
    side, y = np.asarray(primary_side, dtype=float).ravel(), np.asarray(outcomes).astype(int).ravel()
    if not (len(x) == side.size == y.size) or len(x) < 10 * n_splits:
        raise ValueError("need aligned inputs with at least 10 events per fold")
    if np.any(~np.isin(side, (-1.0, 1.0))) or np.any(~np.isin(y, (0, 1))):
        raise ValueError("primary_side must be +1/-1 and outcomes 0/1")
    if len(np.unique(y)) < 2:
        raise ValueError("outcomes contain a single class; nothing to learn")
    design = np.column_stack([x, side])
    params = {"n_estimators": 120, "max_depth": 3, "learning_rate": 0.1, "subsample": 0.9, "n_jobs": 1,
              "tree_method": "hist", "eval_metric": "logloss", "random_state": random_state}
    params.update(xgb_params or {})
    oof = np.full(len(x), np.nan)
    for train, test in PurgedKFold(n_splits, label_horizon, embargo).split(design, t1=t1):
        if train.size == 0 or len(np.unique(y[train])) < 2:
            continue
        oof[test] = xgb.XGBClassifier(**params).fit(design[train], y[train]).predict_proba(design[test])[:, 1]
    scored = np.isfinite(oof)
    if not scored.any():
        raise ValueError("no fold could be trained; check purging against the sample size")
    precision, recall, f1 = precision_recall_f1(y[scored], oof[scored] > probability_threshold)
    model = xgb.XGBClassifier(**params).fit(design, y)
    return MetaLabelResult(oof, np.asarray(bet_size_from_probability(oof, probability_threshold)),
                           float(y[scored].mean()), precision, recall, f1, model)


# ---------------------------------------------------------------- 10.5 hidden Markov regimes
_NAMES_4 = ("low_vol_bull", "mean_reverting_chop", "high_vol_meltup", "liquidity_crash")


@dataclass(frozen=True)
class RegimeModel:
    model: GaussianHMM
    order: np.ndarray          # order[k] = hidden-state index reported in column k
    names: tuple[str, ...]


def _features(features, min_rows: int) -> np.ndarray:
    x = np.asarray(features, dtype=float)
    x = x.reshape(-1, 1) if x.ndim == 1 else x
    if x.ndim != 2 or x.shape[0] < min_rows:
        raise ValueError(f"features must be (T >= {min_rows}, d)")
    if not np.all(np.isfinite(x)):
        raise ValueError("features contain NaN or infinite values")
    return x


def _order_states(model: GaussianHMM) -> tuple[np.ndarray, tuple[str, ...]]:
    k = model.n_components
    vol = np.array([np.trace(np.atleast_2d(c)) for c in model.covars_])
    mean0 = model.means_[:, 0]
    by_vol = np.argsort(vol, kind="stable")
    if k == 4:
        low, high = by_vol[:2], by_vol[2:]
        low = low[np.argsort(-mean0[low], kind="stable")]       # bull first, then chop
        high = high[np.argsort(-mean0[high], kind="stable")]    # melt-up first, then crash
        return np.concatenate([low, high]), _NAMES_4
    names = {2: ("low_vol", "high_vol"), 3: ("low_vol", "mid_vol", "high_vol")}.get(
        k, tuple(f"regime_{i}" for i in range(k)))
    return by_vol, names


def fit_hmm(features, n_states: int = 2, covariance_type: str = "full", n_iter: int = 200,
            random_state: int = 0) -> RegimeModel:
    """10.5 Fit a Gaussian hidden Markov regime model. States are reported in a deterministic order:
    by volatility, and for 4 states as low_vol_bull, mean_reverting_chop, high_vol_meltup,
    liquidity_crash (the first feature column is treated as the return)."""
    if n_states < 2:
        raise ValueError("n_states must be >= 2")
    x = _features(features, 20 * n_states)
    model = GaussianHMM(n_components=n_states, covariance_type=covariance_type, n_iter=n_iter,
                        random_state=random_state, tol=1e-4)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model.fit(x)
    if not (np.all(np.isfinite(model.means_)) and np.all(np.isfinite(model.transmat_))):
        raise ValueError("HMM fit produced non-finite parameters")
    order, names = _order_states(model)
    return RegimeModel(model, order, names)


def filtered_probabilities(regime: RegimeModel, features) -> np.ndarray:
    """10.5 Causal state probabilities P(state_t | data up to t) by the forward algorithm only.

    Row t never depends on rows after t (no smoothing, no Viterbi)."""
    x = _features(features, 1)
    m = regime.model
    k = m.n_components
    log_b = np.column_stack([stats.multivariate_normal.logpdf(x, m.means_[j], np.atleast_2d(m.covars_[j]),
                                                              allow_singular=True).reshape(-1) for j in range(k)])
    log_a = np.log(np.maximum(m.transmat_, 1e-300))
    out = np.empty((x.shape[0], k))
    log_alpha = np.log(np.maximum(m.startprob_, 1e-300)) + log_b[0]
    log_alpha -= logsumexp(log_alpha)
    out[0] = np.exp(log_alpha)
    for t in range(1, x.shape[0]):
        log_alpha = logsumexp(log_alpha[:, None] + log_a, axis=0) + log_b[t]
        log_alpha -= logsumexp(log_alpha)
        out[t] = np.exp(log_alpha)
    return out[:, regime.order]


def causal_regime_probabilities(features, n_states: int = 2, min_train: int = 250, refit_every: int = 50,
                                covariance_type: str = "full", random_state: int = 0) -> np.ndarray:
    """10.5 Walk-forward regime probabilities: the row for time t comes from a model fitted only on
    data up to its last refit (<= t) and filtered only on data up to t. Rows before min_train are NaN."""
    x = _features(features, 1)
    if min_train < 20 * n_states or refit_every < 1:
        raise ValueError("min_train must be at least 20 observations per state and refit_every >= 1")
    out = np.full((x.shape[0], n_states), np.nan)
    start = min_train - 1
    while start < x.shape[0]:
        end = min(start + refit_every, x.shape[0])
        regime = fit_hmm(x[: start + 1], n_states, covariance_type, random_state=random_state)
        out[start:end] = filtered_probabilities(regime, x[:end])[start:end]
        start = end
    return out


def regime_capital_multipliers(regime_probs, table: dict[str, Sequence[float]]) -> dict[str, float]:
    """10.5 Capital gating: each strategy's allocation multiplier is the probability-weighted average
    of its per-regime multipliers (each in [0, 1]). Unusable probabilities gate everything to 0."""
    p = np.asarray(regime_probs, dtype=float).ravel()
    for name, mult in table.items():
        m = np.asarray(mult, dtype=float).ravel()
        if m.size != p.size or np.any((m < 0) | (m > 1)) or not np.all(np.isfinite(m)):
            raise ValueError(f"multipliers for '{name}' must be {p.size} values in [0, 1]")
    if not np.all(np.isfinite(p)) or np.any(p < 0) or p.sum() <= 0:
        return {name: 0.0 for name in table}
    p = p / p.sum()
    return {name: float(np.clip(p @ np.asarray(mult, dtype=float), 0.0, 1.0)) for name, mult in table.items()}
