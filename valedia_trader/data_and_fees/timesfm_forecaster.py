"""TimesFM forecaster: zero-shot quantile forecasts of log prices as point-in-time trading signals.

A forecast stamped day t is computed from prices up to and including t only. The backtest engine
then applies its one-bar execution lag, so a signal stamped t first trades at t + 1.

    from valedia_trader.data_and_fees import timesfm_forecaster as tf
    signals = tf.walk_forward_signals(prices, tf.TimesFMBackend(), horizon=5, step=5)
    weights = tf.forecast_sleeve(prices, signals)

Checkpoint: google/timesfm-2.5-200m-pytorch (Apache-2.0 weights), read from the SSD and checked
against the recorded size (and SHA-256 on request). The TimesFM 3.0 weights are published under a
licence that bars production and commercial use, so they are deliberately not wired here.

Reading a backtest of this model: its stated pretraining corpus (GiftEvalPretrain, Wikimedia
pageviews to Nov 2023, Google Trends to end 2022, synthetic data) is not market prices, but the only
window that is certainly unseen by the model is the one after CHECKPOINT.published.
`evaluate_signals` reports that window separately.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import math
import os
import select
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd
from scipy.special import ndtri

from valedia_trader.quant_engine import level8_portfolio_risk as risk

__all__ = ["QUANTILE_LEVELS", "Checkpoint", "CHECKPOINT", "CheckpointError", "QuantileForecast", "ForecastBackend",
           "NaiveDriftBackend", "TimesFMBackend", "SIGNAL_FIELDS", "price_signals", "walk_forward_signals",
           "forecast_sleeve", "kelly_weights", "evaluate_signals"]

QUANTILE_LEVELS = np.round(np.arange(0.1, 1.0, 0.1), 1)
_Z90 = 1.2815515655446004          # standard normal 90% quantile
SIGNAL_FIELDS = ("expected_return", "sigma", "prob_up", "score")


@dataclass(frozen=True)
class Checkpoint:
    name: str
    repo: str
    revision: str
    path: Path
    weights: str
    weight_bytes: int
    weight_sha256: str
    license: str
    published: str          # first publication of the checkpoint (UTC date)


CHECKPOINT = Checkpoint(
    "timesfm-2.5-200m", "google/timesfm-2.5-200m-pytorch", "1d952420fba87f3c6dee4f240de0f1a0fbc790e3",
    Path("/Volumes/NIMROD_SSD/Qwen-Weight-Space/models/timesfm-2.5-200m-pytorch"), "model.safetensors", 925181104,
    "2f776efe6245e42b24bc4153ffdf61810140210e4bd3b01fb21f7aa779ab6ce8", "apache-2.0", "2025-09-02")


class CheckpointError(RuntimeError):
    """The checkpoint on disk is missing or is not the pinned one."""


@dataclass(frozen=True)
class QuantileForecast:
    """Forecast paths for n series: `median` (n, h) and `quantiles` (n, h, 9) at QUANTILE_LEVELS."""
    median: np.ndarray
    quantiles: np.ndarray

    def __post_init__(self):
        if self.median.ndim != 2 or self.quantiles.shape != (*self.median.shape, len(QUANTILE_LEVELS)):
            raise ValueError("median must be (n, h) and quantiles (n, h, 9)")

    def sigma(self, step: int = -1) -> np.ndarray:
        """Forecast standard deviation at one step, from the 10%-90% spread under a normal reading."""
        q = self.quantiles[:, step, :]
        return np.maximum(q[:, -1] - q[:, 0], 0.0) / (2.0 * _Z90)

    def prob_above(self, threshold, step: int = -1) -> np.ndarray:
        """P(value > threshold) at one step: linear interpolation of the forecast CDF between the
        deciles and normal tails (continuous at the 10% and 90% deciles) outside them."""
        thr = np.broadcast_to(np.asarray(threshold, dtype=float), (len(self.median),))
        q = np.maximum.accumulate(self.quantiles[:, step, :], axis=1)        # enforce a monotone CDF
        sig = self.sigma(step)
        out = np.empty(len(thr))
        for i, (x, qi, s) in enumerate(zip(thr, q, sig)):
            if not np.isfinite(x) or not np.all(np.isfinite(qi)):
                out[i] = np.nan
            elif s <= 0:
                out[i] = 0.5 if x == qi[4] else float(x < qi[4])
            elif x < qi[0]:
                out[i] = 1.0 - 0.5 * math.erfc(-((x - qi[0]) / s - _Z90) / math.sqrt(2.0))
            elif x > qi[-1]:
                out[i] = 0.5 * math.erfc(((x - qi[-1]) / s + _Z90) / math.sqrt(2.0))
            else:
                out[i] = 1.0 - float(np.interp(x, qi, QUANTILE_LEVELS))
        return out


class ForecastBackend(Protocol):
    name: str

    def forecast(self, contexts: list[np.ndarray], horizon: int) -> QuantileForecast: ...


@dataclass
class NaiveDriftBackend:
    """Random walk with drift fitted on the context. This is the control: a model sleeve that cannot
    beat it adds nothing beyond trailing mean and volatility."""
    window: int = 250
    name: str = "naive-drift"

    def forecast(self, contexts: list[np.ndarray], horizon: int) -> QuantileForecast:
        z = ndtri(QUANTILE_LEVELS)
        steps = np.arange(1, horizon + 1, dtype=float)
        med = np.empty((len(contexts), horizon))
        qs = np.empty((len(contexts), horizon, len(QUANTILE_LEVELS)))
        for i, ctx in enumerate(contexts):
            x = np.asarray(ctx, dtype=float)
            d = np.diff(x[-(self.window + 1):])
            mu, sd = (float(d.mean()), float(d.std(ddof=1))) if len(d) > 1 else (0.0, 0.0)
            med[i] = x[-1] + mu * steps
            qs[i] = med[i][:, None] + sd * np.sqrt(steps)[:, None] * z[None, :]
        return QuantileForecast(med, qs)


WORKER = Path(__file__).with_name("timesfm_worker.py")
_MARK = "@@TFM"


@dataclass
class TimesFMBackend:
    """TimesFM 2.5 (200M) on CPU through the upstream `timesfm` package.

    The model runs in a worker process (timesfm_worker.py) that stays up between calls. torch never
    shares a process with scikit-learn or xgboost: each ships its own OpenMP runtime and the pair
    crashes on macOS. `request_timeout` is None by default: a forecast waits as long as the worker is
    alive, and a dead worker raises with the tail of its log."""
    checkpoint: Checkpoint = CHECKPOINT
    max_context: int = 512
    max_horizon: int = 64
    batch_size: int = 32
    verify_hash: bool = False
    request_timeout: float | None = None
    name: str = field(init=False)
    _proc: subprocess.Popen | None = field(default=None, init=False, repr=False)
    _dir: str | None = field(default=None, init=False, repr=False)
    _calls: int = field(default=0, init=False, repr=False)

    def __post_init__(self):
        self.name = self.checkpoint.name

    def verify(self) -> None:
        w = self.checkpoint.path / self.checkpoint.weights
        if not w.is_file():
            raise CheckpointError(f"weights not found: {w}")
        if w.stat().st_size != self.checkpoint.weight_bytes:
            raise CheckpointError(f"{w} has {w.stat().st_size} bytes, pinned {self.checkpoint.weight_bytes}")
        if self.verify_hash:
            h = hashlib.sha256()
            with w.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 22), b""):
                    h.update(chunk)
            if h.hexdigest() != self.checkpoint.weight_sha256:
                raise CheckpointError(f"{w} does not match the pinned SHA-256")

    def _log_tail(self) -> str:
        try:
            return Path(self._dir, "worker.log").read_text(errors="replace")[-600:].strip()
        except (OSError, TypeError):
            return ""

    def _reply(self) -> list[str]:
        """Next protocol line from the worker. Lines without the marker are library chatter."""
        proc, waited = self._proc, 0.0
        while True:
            ready, _, _ = select.select([proc.stdout], [], [], 1.0)
            if ready:
                line = proc.stdout.readline()
                if line.startswith(_MARK):
                    return line[len(_MARK):].strip().split(" ", 1)
                if line:
                    continue
            if proc.poll() is not None:
                tail = self._log_tail()
                self.close()
                raise CheckpointError(f"TimesFM worker exited with code {proc.returncode}: {tail}")
            waited += 0.0 if ready else 1.0
            if self.request_timeout is not None and waited >= self.request_timeout:
                self.close()
                raise TimeoutError(f"TimesFM worker gave no answer in {self.request_timeout:.0f}s")

    def _start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        self.verify()
        self._dir = tempfile.mkdtemp(prefix="timesfm-")
        log = open(Path(self._dir, "worker.log"), "w")
        env = {**os.environ, "HF_HUB_OFFLINE": "1", "PYTHONUNBUFFERED": "1"}
        from valedia_trader.core.secrets import child_env
        self._proc = subprocess.Popen(
            [sys.executable, str(WORKER), str(self.checkpoint.path), str(self.max_context), str(self.max_horizon),
             str(self.batch_size)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, bufsize=1,
            cwd=self._dir, env=child_env(env))
        log.close()
        atexit.register(self.close)
        if self._reply()[0] != "ready":
            tail = self._log_tail()
            self.close()
            raise CheckpointError(f"TimesFM worker did not start: {tail}")

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.stdin.close()
                proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
                proc.wait()
            proc.stdout.close()
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None

    def forecast(self, contexts: list[np.ndarray], horizon: int) -> QuantileForecast:
        if not 1 <= horizon <= self.max_horizon:
            raise ValueError(f"horizon must be in [1, {self.max_horizon}]")
        if not contexts:
            return QuantileForecast(np.empty((0, horizon)), np.empty((0, horizon, len(QUANTILE_LEVELS))))
        self._start()
        self._calls += 1
        src, dst = Path(self._dir, f"in-{self._calls}.npz"), Path(self._dir, f"out-{self._calls}.npz")
        inputs = [np.asarray(c, dtype=np.float32)[-self.max_context:] for c in contexts]
        np.savez(src, flat=np.concatenate(inputs), lengths=np.array([len(c) for c in inputs], dtype=np.int64))
        self._proc.stdin.write(f"{horizon}\t{src}\t{dst}\n")
        self._proc.stdin.flush()
        status = self._reply()
        if status[0] != "ok":
            raise RuntimeError(f"TimesFM worker failed: {status[-1]}")
        with np.load(dst) as res:
            point, quant = res["point"].astype(float), res["quant"].astype(float)
        src.unlink(missing_ok=True)
        dst.unlink(missing_ok=True)
        return QuantileForecast(point, quant[:, :, 1:])            # column 0 of the upstream output is the mean head


def _signals_from(forecast: QuantileForecast, last: np.ndarray) -> np.ndarray:
    """(n, 4) array of SIGNAL_FIELDS at the forecast horizon, in log-return units."""
    er = forecast.median[:, -1] - last
    sig = forecast.sigma(-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        score = np.where(sig > 0, er / sig, 0.0)
    return np.column_stack([er, sig, forecast.prob_above(last, -1), score])


def price_signals(prices: pd.DataFrame, backend: ForecastBackend, horizon: int = 5, context: int = 512,
                  min_context: int = 128) -> pd.DataFrame:
    """Signals for the last row of `prices` (index: asset, columns: SIGNAL_FIELDS). Assets with fewer
    than `min_context` usable prices get NaN."""
    panel = walk_forward_signals(prices.iloc[-(context + 1):] if len(prices) > context else prices, backend, horizon,
                                 step=1, context=context, min_context=min_context, start=prices.index[-1])
    return pd.DataFrame({f: panel[f].iloc[-1] for f in SIGNAL_FIELDS})


def walk_forward_signals(prices: pd.DataFrame, backend: ForecastBackend, horizon: int = 5, step: int = 5,
                         context: int = 512, min_context: int = 128, start=None, chunk: int = 2048,
                         cache_dir: str | Path | None = None) -> dict[str, pd.DataFrame]:
    """Forecast every `step` rows and hold the signal in between. Returns one date x asset frame per
    SIGNAL_FIELDS entry. Row t is built from `prices.iloc[: t + 1]` only; rebalance rows are the
    positions divisible by `step`, so truncating the frame never moves them.

    With `cache_dir`, results are stored under a key that covers the backend, every parameter and the
    exact price content, so a changed input can never read a stale forecast."""
    if step < 1 or horizon < 1 or min_context < 8:
        raise ValueError("step and horizon must be >= 1 and min_context >= 8")
    key = None
    if cache_dir is not None:
        h = hashlib.sha256(json.dumps([backend.name, getattr(backend, "checkpoint", None) and backend.checkpoint.weight_sha256,
                                       horizon, step, context, min_context, str(start), list(map(str, prices.columns)),
                                       [str(prices.index[0]), str(prices.index[-1]), len(prices)]]).encode())
        h.update(np.ascontiguousarray(prices.to_numpy(dtype=float)).tobytes())
        key = Path(cache_dir) / f"signals-{h.hexdigest()[:24]}.parquet"
        if key.is_file():
            flat = pd.read_parquet(key)
            return {f: flat[f].unstack("asset").reindex(index=prices.index, columns=prices.columns) for f in SIGNAL_FIELDS}

    logp = np.log(prices.where(prices > 0)).to_numpy(dtype=float)
    n, m = logp.shape
    first = 0 if start is None else int(prices.index.searchsorted(pd.Timestamp(start)))
    jobs: list[tuple[int, int]] = []
    contexts: list[np.ndarray] = []
    for t in range(n):
        if t % step or t < first:
            continue
        for j in range(m):
            col = logp[max(0, t - context + 1): t + 1, j]
            col = col[np.isfinite(col)]
            if len(col) >= min_context and np.isfinite(logp[t, j]):
                jobs.append((t, j))
                contexts.append(col)
    out = np.full((len(SIGNAL_FIELDS), n, m), np.nan)
    for lo in range(0, len(jobs), chunk):
        part = contexts[lo: lo + chunk]
        sig = _signals_from(backend.forecast(part, horizon), np.array([c[-1] for c in part]))
        for (t, j), row in zip(jobs[lo: lo + chunk], sig):
            out[:, t, j] = row
    # A forecast is held until the next rebalance row. Filling runs inside each block of `step` rows,
    # so an asset with no forecast on a rebalance row stays NaN instead of inheriting an older one.
    block = np.arange(n) // step
    frames = {f: pd.DataFrame(out[k], index=prices.index, columns=prices.columns).groupby(block).ffill()
              for k, f in enumerate(SIGNAL_FIELDS)}
    if key is not None:
        key.parent.mkdir(parents=True, exist_ok=True)
        flat = pd.concat({f: frames[f].rename_axis(index="date", columns="asset").stack(future_stack=True) for f in SIGNAL_FIELDS}, axis=1)
        flat.to_parquet(key)
    return frames


def forecast_sleeve(prices: pd.DataFrame, signals: dict[str, pd.DataFrame], score_cap: float = 2.0,
                    vol_window: int = 20) -> pd.DataFrame:
    """Sleeve weights from forecast scores: position proportional to the capped forecast t-score
    times inverse trailing volatility, normalised to gross exposure 1 (the trend sleeve's sizing with
    a graded signal in place of a sign)."""
    score = signals["score"].reindex(index=prices.index, columns=prices.columns).clip(-score_cap, score_cap)
    vol = prices.pct_change().rolling(vol_window).std()
    raw = (score * (1.0 / vol.where(vol > 0))).where(prices.notna())
    w = raw.div(raw.abs().sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    return w


def kelly_weights(signals_row: pd.DataFrame, multiplier: float = 0.25, max_gross: float = 1.0) -> pd.Series:
    """Level 8.4 sizing from one row of forecasts: per-asset continuous Kelly f* = mu / sigma^2 on the
    forecast horizon (correlations ignored, so this is an upper bound per asset), then fractional
    Kelly and the leverage cap. Input: index asset, columns SIGNAL_FIELDS."""
    mu = np.expm1(signals_row["expected_return"].to_numpy(dtype=float))
    var = np.square(signals_row["sigma"].to_numpy(dtype=float))
    with np.errstate(divide="ignore", invalid="ignore"):
        full = np.where(var > 0, mu / var, 0.0)
    sized = risk.apply_leverage_cap(risk.fractional_kelly(full, multiplier), max_gross)
    return pd.Series(sized, index=signals_row.index)


def evaluate_signals(prices: pd.DataFrame, signals: dict[str, pd.DataFrame], horizon: int, step: int,
                     windows: dict[str, tuple[str | None, str | None]]) -> dict[str, dict]:
    """Forecast quality on non-overlapping rebalance rows, per window: rank information coefficient of
    the score against the realised forward log return (mean and t-statistic over dates), directional
    hit rate, Brier score of prob_up (0.25 is a coin flip), and coverage of the symmetric 80% band
    around the median (0.80 if calibrated). Forward returns are used here for scoring only."""
    logp = np.log(prices.where(prices > 0))
    fwd = logp.shift(-horizon) - logp
    rows = prices.index[:: max(step, horizon)]
    out = {}
    for label, (lo, hi) in windows.items():
        idx = rows[(rows >= pd.Timestamp(lo or rows[0])) & (rows <= pd.Timestamp(hi or rows[-1]))]
        s, er, sg, pu, f = (x.reindex(idx) for x in (signals["score"], signals["expected_return"], signals["sigma"],
                                                      signals["prob_up"], fwd))
        ok = s.notna() & f.notna()
        ics = [s.loc[d][ok.loc[d]].rank().corr(f.loc[d][ok.loc[d]].rank()) for d in idx if ok.loc[d].sum() >= 5]
        ics = np.array([x for x in ics if np.isfinite(x)])
        sv, fv, pv = s.to_numpy()[ok.to_numpy()], f.to_numpy()[ok.to_numpy()], pu.to_numpy()[ok.to_numpy()]
        erv, sgv = er.to_numpy()[ok.to_numpy()], sg.to_numpy()[ok.to_numpy()]
        nz = sv != 0
        out[label] = {
            "dates": int(len(ics)), "observations": int(ok.to_numpy().sum()),
            "ic_mean": float(ics.mean()) if len(ics) else float("nan"),
            "ic_tstat": float(ics.mean() / (ics.std(ddof=1) / math.sqrt(len(ics)))) if len(ics) > 2 and ics.std(ddof=1) > 0 else float("nan"),
            "hit_rate": float((np.sign(sv[nz]) == np.sign(fv[nz])).mean()) if nz.any() else float("nan"),
            "brier_prob_up": float(np.mean((pv - (fv > 0)) ** 2)) if len(pv) else float("nan"),
            "base_rate_up": float((fv > 0).mean()) if len(fv) else float("nan"),
            "coverage_10_90": float((np.abs(fv - erv) <= _Z90 * sgv).mean()) if len(fv) else float("nan"),
        }
    return out
