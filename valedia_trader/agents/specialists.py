"""Specialist strategy sleeves. Each function maps a price history to target weights where row t
depends only on prices up to and including t (checked by the look-ahead detector in the tests).
Weights are per unit of sleeve capital with gross exposure at most 1."""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

__all__ = ["trend_following", "cross_sectional_momentum", "short_term_reversal", "risk_parity",
           "SLEEVES", "DEFAULT_PARAMS", "sleeve_weights"]


def _inverse_vol(prices: pd.DataFrame, vol_window: int) -> pd.DataFrame:
    vol = prices.pct_change().rolling(vol_window).std()
    return (1.0 / vol.where(vol > 0)).where(prices.notna())


def _cap_gross(w: pd.DataFrame, gross: float = 1.0) -> pd.DataFrame:
    w = w.fillna(0.0)
    total = w.abs().sum(axis=1)
    scale = (gross / total.where(total > gross)).fillna(1.0)
    return w.mul(scale, axis=0)


def trend_following(prices: pd.DataFrame, lookback: int = 126, skip: int = 5, vol_window: int = 20) -> pd.DataFrame:
    """Time-series momentum: long assets whose return over (t - lookback, t - skip] is positive, short
    the rest, sized by inverse volatility."""
    signal = np.sign(prices.shift(skip) / prices.shift(lookback) - 1.0)
    raw = signal * _inverse_vol(prices, vol_window)
    return _cap_gross(raw.div(raw.abs().sum(axis=1).replace(0, np.nan), axis=0))


def cross_sectional_momentum(prices: pd.DataFrame, lookback: int = 126, skip: int = 5,
                             fraction: float = 0.3) -> pd.DataFrame:
    """Cross-sectional momentum: long the top `fraction` of assets by past return and short the
    bottom `fraction`, equal weighted and dollar neutral."""
    mom = prices.shift(skip) / prices.shift(lookback) - 1.0
    rank = mom.rank(axis=1, pct=True)
    n = mom.notna().sum(axis=1)
    long_, short_ = (rank > 1.0 - fraction).astype(float), (rank <= fraction).astype(float)
    w = long_.div(long_.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0) * 0.5 - short_.div(
        short_.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0) * 0.5
    return _cap_gross(w.where(n >= 4, 0.0))


def short_term_reversal(prices: pd.DataFrame, lookback: int = 5, vol_window: int = 60,
                        entry_z: float = 1.0) -> pd.DataFrame:
    """Short-term reversal: fade moves over `lookback` days that exceed `entry_z` trailing standard
    deviations (buy the oversold, sell the overbought), inverse-volatility sized."""
    r = prices.pct_change()
    move = prices / prices.shift(lookback) - 1.0
    z = move / (r.rolling(vol_window).std() * np.sqrt(lookback))
    signal = -np.sign(z).where(z.abs() >= entry_z, 0.0)
    raw = signal * _inverse_vol(prices, vol_window)
    return _cap_gross(raw.div(raw.abs().sum(axis=1).replace(0, np.nan), axis=0))


def risk_parity(prices: pd.DataFrame, vol_window: int = 60) -> pd.DataFrame:
    """Long-only inverse-volatility allocation across the universe (the diversified beta sleeve)."""
    iv = _inverse_vol(prices, vol_window)
    return _cap_gross(iv.div(iv.sum(axis=1).replace(0, np.nan), axis=0))


SLEEVES: dict[str, Callable[..., pd.DataFrame]] = {
    "trend": trend_following,
    "xs_momentum": cross_sectional_momentum,
    "reversal": short_term_reversal,
    "risk_parity": risk_parity,
}

DEFAULT_PARAMS: dict[str, dict] = {
    "trend": {"lookback": 126, "skip": 5, "vol_window": 20},
    "xs_momentum": {"lookback": 126, "skip": 5, "fraction": 0.3},
    "reversal": {"lookback": 5, "vol_window": 60, "entry_z": 1.0},
    "risk_parity": {"vol_window": 60},
}


def sleeve_weights(name: str, prices: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Target weights of one sleeve with the given (or default) hyperparameters."""
    if name not in SLEEVES:
        raise KeyError(f"unknown sleeve '{name}'")
    return SLEEVES[name](prices, **(DEFAULT_PARAMS[name] if params is None else params))
