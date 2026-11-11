"""Cost-aware no-trade buffering (the idea is Robert Carver's, as used in pysystemtrade; this is an
independent implementation, see THIRD_PARTY.md). Trading only when the target leaves a band around
the current position, and then only to the edge of the band, removes most small rebalancing trades."""
from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = ["apply_buffer", "buffer_width"]


def buffer_width(targets: pd.DataFrame, fraction: float) -> pd.Series:
    """Half-width of the band on each day: `fraction` of the average absolute target weight among
    assets with a non-zero target."""
    if not 0.0 <= fraction < 1.0:
        raise ValueError("fraction must be in [0, 1)")
    active = (targets.abs() > 0).sum(axis=1).replace(0, np.nan)
    return (fraction * targets.abs().sum(axis=1) / active).fillna(0.0)


def apply_buffer(targets: pd.DataFrame, prices: pd.DataFrame, fraction: float = 0.10) -> pd.DataFrame:
    """Buffered weights. Row t depends only on targets and prices through t: yesterday's buffered
    weights drift with today's returns, then each is clipped into [target - b, target + b].
    A target of exactly zero is always honoured in full, so exits and risk vetoes are never delayed."""
    if not targets.index.equals(prices.index) or list(targets.columns) != list(prices.columns):
        raise ValueError("targets and prices must share index and columns")
    width = buffer_width(targets, fraction).to_numpy()
    tgt = np.nan_to_num(targets.to_numpy(dtype=float), nan=0.0)
    ret = prices.pct_change().fillna(0.0).to_numpy()
    out = np.zeros_like(tgt)
    prev = np.zeros(tgt.shape[1])
    for t in range(len(tgt)):
        growth = 1.0 + float(prev @ ret[t])
        drifted = prev * (1.0 + ret[t]) / growth if growth > 0 else np.zeros_like(prev)
        new = np.clip(drifted, tgt[t] - width[t], tgt[t] + width[t])
        new[tgt[t] == 0.0] = 0.0
        out[t] = prev = new
    return pd.DataFrame(out, index=targets.index, columns=targets.columns)
