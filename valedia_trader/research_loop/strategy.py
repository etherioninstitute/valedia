"""One shape for every strategy: entry rules, exit rules, position sizing, risk limits.

A strategy is a file in `strategies/` with a version number and its hypothesis in one line. The
backtester and the paper trader run any of them through `target_weights`, which walks the rules
forward one bar at a time: row t is decided from prices up to and including t.

Two ways to write one:

* a JSON spec built from the rule library below (`RuleStrategy`). Research agents can only produce
  this form, so nothing a model writes is ever executed as code;
* a Python file with a `STRATEGY` object (a `Strategy` subclass), for ideas the library cannot say.
"""
from __future__ import annotations

import importlib.util
import json
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

__all__ = ["StrategyRisk", "Strategy", "RuleStrategy", "SCORES", "SIZING", "SpecError", "run_rules", "load_strategy",
           "save_spec", "bump_version", "CRYPTO"]

CRYPTO = ("BTC-USD", "ETH-USD")       # spot crypto is not shorted in this system
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class SpecError(ValueError):
    """A strategy file or spec does not follow the common shape."""


@dataclass(frozen=True)
class StrategyRisk:
    """Risk limits of one strategy, as fractions of the capital given to it."""
    max_position_pct: float = 0.10
    max_open_positions: int = 5
    max_gross: float = 1.0
    stop_loss_pct: float | None = 0.10
    take_profit_pct: float | None = None
    max_holding_days: int | None = None

    def __post_init__(self):
        if not 0.0 < self.max_position_pct <= 1.0 or not 0.0 < self.max_gross <= 2.0 or self.max_open_positions < 1:
            raise SpecError("risk: max_position_pct in (0, 1], max_gross in (0, 2], max_open_positions >= 1")
        for name in ("stop_loss_pct", "take_profit_pct"):
            v = getattr(self, name)
            if v is not None and not 0.0 < v < 1.0:
                raise SpecError(f"risk: {name} must be in (0, 1) or null")
        if self.max_holding_days is not None and self.max_holding_days < 1:
            raise SpecError("risk: max_holding_days must be >= 1 or null")


class Strategy(ABC):
    """The common shape. Subclasses supply the four parts; `target_weights` combines them."""
    name: str
    version: str
    hypothesis: str
    market: tuple[str, ...]
    risk: StrategyRisk

    @abstractmethod
    def entry(self, prices: pd.DataFrame) -> pd.DataFrame:
        """Entry rules: +1 open a long, -1 open a short, 0 nothing. Row t uses prices up to t."""

    @abstractmethod
    def exit(self, prices: pd.DataFrame, side: int) -> pd.DataFrame:
        """Exit rules: True where an open position on `side` (+1 or -1) should be closed."""

    @abstractmethod
    def size(self, prices: pd.DataFrame) -> pd.DataFrame:
        """Position sizing: the weight to open a position with, as a positive fraction of capital."""

    def strength(self, prices: pd.DataFrame) -> pd.DataFrame:
        """Which entry goes first when more signals fire than there are free slots (higher wins)."""
        return self.entry(prices).abs().astype(float)

    def target_weights(self, prices: pd.DataFrame) -> pd.DataFrame:
        return run_rules(self, prices)[0]

    def describe(self) -> dict:
        return {"name": self.name, "version": self.version, "hypothesis": self.hypothesis, "market": list(self.market),
                "risk": asdict(self.risk)}


def _check_identity(s: Strategy) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,40}", str(getattr(s, "name", ""))):
        raise SpecError("name must be lower_snake_case, 3 to 41 characters")
    if not _SEMVER.match(str(getattr(s, "version", ""))):
        raise SpecError("version must look like 1.0.0")
    if len(str(getattr(s, "hypothesis", "")).split()) < 4:
        raise SpecError("hypothesis must be a one-line sentence")
    if not getattr(s, "market", None) or not all(isinstance(m, str) and m for m in s.market):
        raise SpecError("market must list at least one symbol")
    if not isinstance(getattr(s, "risk", None), StrategyRisk):
        raise SpecError("risk must be a StrategyRisk")


def run_rules(strategy: Strategy, prices: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Walk the strategy forward. Returns (weights, events): weights has the columns of `prices`
    (zero outside the strategy's market) and row t is the position decided at the close of t; events
    record every entry and exit with the rule that caused it.

    Order of business on each bar: exits first (stop loss, take profit, time limit, exit rule), then
    entries into free slots by strength. A position keeps the size it was opened with. Spot crypto is
    never shorted. Gross exposure is scaled down to `risk.max_gross` if the positions exceed it."""
    _check_identity(strategy)
    cols = [c for c in strategy.market if c in prices.columns]
    if not cols:
        raise SpecError(f"none of the strategy's market {strategy.market} is in the price data")
    px = prices[cols]
    risk = strategy.risk
    entry = np.array(strategy.entry(px).reindex(index=px.index, columns=cols).fillna(0).to_numpy(), copy=True)
    exit_long = strategy.exit(px, 1).reindex(index=px.index, columns=cols).fillna(False).to_numpy(dtype=bool)
    exit_short = strategy.exit(px, -1).reindex(index=px.index, columns=cols).fillna(False).to_numpy(dtype=bool)
    size = strategy.size(px).reindex(index=px.index, columns=cols).to_numpy(dtype=float)
    strength = strategy.strength(px).reindex(index=px.index, columns=cols).fillna(0.0).to_numpy(dtype=float)
    p = px.to_numpy(dtype=float)
    n, m = p.shape
    no_short = np.array([c in CRYPTO for c in cols])
    side, weight, entry_px, held = np.zeros(m), np.zeros(m), np.full(m, np.nan), np.zeros(m, dtype=int)
    out = np.zeros((n, m))
    events: list[dict] = []
    for t in range(n):
        for j in np.flatnonzero(side != 0):
            if not np.isfinite(p[t, j]):
                continue                                     # no price today: the position is carried
            held[j] += 1
            move = (p[t, j] / entry_px[j] - 1.0) * side[j]
            reason = None
            if risk.stop_loss_pct is not None and move <= -risk.stop_loss_pct:
                reason = f"stop loss: {move:+.1%} against the position"
            elif risk.take_profit_pct is not None and move >= risk.take_profit_pct:
                reason = f"take profit: {move:+.1%}"
            elif risk.max_holding_days is not None and held[j] >= risk.max_holding_days:
                reason = f"time limit: held {held[j]} days"
            elif (exit_long if side[j] > 0 else exit_short)[t, j]:
                reason = "exit rule"
            if reason:
                events.append({"date": px.index[t], "symbol": cols[j], "action": "exit", "side": int(side[j]),
                               "price": float(p[t, j]), "reason": reason, "pnl_pct": float(move)})
                side[j], weight[j], entry_px[j], held[j] = 0, 0.0, np.nan, 0
                entry[t, j] = 0                              # closed on this bar: not reopened on the same bar
        free = risk.max_open_positions - int((side != 0).sum())
        if free > 0:
            want = [j for j in np.flatnonzero((entry[t] != 0) & (side == 0))
                    if np.isfinite(p[t, j]) and np.isfinite(size[t, j]) and size[t, j] > 0 and not (entry[t, j] < 0 and no_short[j])]
            for j in sorted(want, key=lambda k: (-strength[t, k], k))[:free]:
                side[j], entry_px[j], held[j] = np.sign(entry[t, j]), p[t, j], 0
                weight[j] = min(float(size[t, j]), risk.max_position_pct)
                events.append({"date": px.index[t], "symbol": cols[j], "action": "enter", "side": int(side[j]),
                               "price": float(p[t, j]), "reason": "entry rule", "weight": float(weight[j])})
        w = side * weight
        gross = np.abs(w).sum()
        out[t] = w * (risk.max_gross / gross) if gross > risk.max_gross else w
    weights = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    weights[cols] = out
    return weights, events


# ---- the rule library: each rule is a score; entries and exits are thresholds on it

def _momentum(px: pd.DataFrame, lookback: int = 126, skip: int = 0) -> pd.DataFrame:
    """Return over the past `lookback` days, ignoring the most recent `skip`."""
    return px.shift(skip) / px.shift(lookback) - 1.0


def _breakout(px: pd.DataFrame, lookback: int = 55) -> pd.DataFrame:
    """Position inside the prior `lookback`-day range: +1 at a new high, -1 at a new low, 0 at the middle."""
    hi, lo = px.shift(1).rolling(lookback).max(), px.shift(1).rolling(lookback).min()
    return (2.0 * (px - lo) / (hi - lo).where(hi > lo) - 1.0)


def _reversion(px: pd.DataFrame, lookback: int = 5, vol_window: int = 60) -> pd.DataFrame:
    """Minus the z-score of the `lookback`-day move: high after a sharp fall."""
    sd = px.pct_change().rolling(vol_window).std() * np.sqrt(lookback)
    return -(px / px.shift(lookback) - 1.0) / sd.where(sd > 0)


def _ma_cross(px: pd.DataFrame, fast: int = 50, slow: int = 200) -> pd.DataFrame:
    """Fast moving average relative to the slow one, minus 1."""
    return px.rolling(fast).mean() / px.rolling(slow).mean() - 1.0


def _drift(px: pd.DataFrame, window: int = 250, horizon: int = 5) -> pd.DataFrame:
    """Trailing mean log return over its standard deviation, scaled to `horizon` days."""
    d = np.log(px.where(px > 0)).diff()
    sd = d.rolling(window).std()
    return d.rolling(window).mean() / sd.where(sd > 0) * np.sqrt(horizon)


def _trend_blend(px: pd.DataFrame, fast: int = 16, speeds: int = 3, vol_window: int = 25) -> pd.DataFrame:
    """Trend at several speeds at once, each on the same scale, averaged.

    For speed k the fast average spans `fast * 2**k` days and the slow one four times that. Their gap is
    divided by the recent daily price volatility, then by its own average size so far (so every speed
    counts equally and no future data is used), capped at +-2, and the speeds are averaged. About +1 is
    an ordinary up-trend; 0 is none."""
    vol = px.diff().ewm(span=vol_window, min_periods=vol_window).std()
    parts = []
    for k in range(int(speeds)):
        f, sl = int(fast) * 2 ** k, int(fast) * 2 ** k * 4
        raw = (px.ewm(span=f, min_periods=f).mean() - px.ewm(span=sl, min_periods=sl).mean()) / vol.where(vol > 0)
        typical = raw.abs().expanding(min_periods=sl).mean()
        parts.append((raw / typical.where(typical > 0)).clip(-2.0, 2.0))
    return sum(parts) / len(parts)


@lru_cache(maxsize=1)
def _exchange_sessions() -> pd.DatetimeIndex:
    """Every New York Stock Exchange session from 1990 to about a year ahead: early closes count, holidays
    and the exchange's unscheduled closures do not."""
    import exchange_calendars
    return exchange_calendars.get_calendar("XNYS", start="1990-01-01").sessions


def _turn_of_month(px: pd.DataFrame, before: int = 1, after: int = 3) -> pd.DataFrame:
    """1 when the next exchange session is one of the last `before` of a month or the first `after` of one, else 0: the calendar, not prices.

    Row t is read at the close of t and the position it opens earns the next session's return, so the
    rule looks one session ahead on the exchange calendar, which is published in advance. With enter 1
    and exit 0 it holds from the close before the window to the window's last close. Every fund gets the
    same score. Dates before 1990 or beyond the published calendar are refused."""
    sessions = _exchange_sessions()
    month = pd.Series(sessions.year * 12 + sessions.month, index=sessions)
    in_window = ((month.groupby(month).cumcount(ascending=False) < int(before))
                 | (month.groupby(month).cumcount() < int(after))).to_numpy()
    if len(px.index) and (px.index[0] < sessions[0] or px.index[-1] > sessions[-1] - pd.Timedelta(days=45)):
        raise SpecError(f"turn_of_month: the exchange calendar covers {sessions[0].date()} to about a year ahead, "
                        f"and the prices run from {px.index[0].date()} to {px.index[-1].date()}")
    mark = in_window[sessions.searchsorted(px.index, side="right")].astype(float)
    return pd.DataFrame(np.repeat(mark[:, None], px.shape[1], axis=1), index=px.index, columns=px.columns)


SCORES: dict[str, Callable[..., pd.DataFrame]] = {"momentum": _momentum, "breakout": _breakout, "reversion": _reversion,
                                                  "ma_cross": _ma_cross, "drift": _drift, "trend_blend": _trend_blend,
                                                  "turn_of_month": _turn_of_month}
_SCORE_PARAMS = {"momentum": {"lookback": (5, 756), "skip": (0, 63)}, "breakout": {"lookback": (5, 504)},
                 "reversion": {"lookback": (1, 63), "vol_window": (10, 504)}, "ma_cross": {"fast": (2, 252), "slow": (5, 756)},
                 "drift": {"window": (20, 756), "horizon": (1, 63)},
                 "trend_blend": {"fast": (2, 64), "speeds": (1, 4), "vol_window": (5, 126)},
                 "turn_of_month": {"before": (0, 10), "after": (0, 10)}}


def _size_equal(px: pd.DataFrame, risk: StrategyRisk) -> pd.DataFrame:
    return pd.DataFrame(risk.max_position_pct, index=px.index, columns=px.columns).where(px.notna())


def _size_inverse_vol(px: pd.DataFrame, risk: StrategyRisk, vol_window: int = 20, position_vol: float = 0.02) -> pd.DataFrame:
    """Each position sized to contribute `position_vol` of annual volatility, capped by the risk limit."""
    ann = px.pct_change().rolling(vol_window).std() * np.sqrt(252.0)
    return (position_vol / ann.where(ann > 0)).clip(upper=risk.max_position_pct)


SIZING: dict[str, Callable[..., pd.DataFrame]] = {"equal": _size_equal, "inverse_vol": _size_inverse_vol}
_SIZING_PARAMS = {"equal": {}, "inverse_vol": {"vol_window": (5, 252), "position_vol": (0.001, 0.20)}}


def _params(given: dict, allowed: dict[str, tuple], what: str) -> dict:
    out = {}
    for k, v in given.items():
        if k not in allowed:
            raise SpecError(f"{what}: unknown parameter '{k}' (allowed: {sorted(allowed)})")
        lo, hi = allowed[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise SpecError(f"{what}: {k} must be a number in [{lo}, {hi}]")
        out[k] = int(v) if isinstance(lo, int) and isinstance(hi, int) else float(v)
    return out


@dataclass
class RuleStrategy(Strategy):
    """A strategy assembled from the rule library.

    entry   {"rule": <score>, <score parameters>, "enter": x, "exit": y, "direction": "long" | "short" | "both"}
            long when score >= enter and closed when score <= exit; shorts mirror with the signs flipped
    sizing  {"rule": "equal" | "inverse_vol", <parameters>}
    risk    StrategyRisk fields
    """
    name: str
    version: str
    hypothesis: str
    market: tuple[str, ...]
    entry_rule: dict[str, Any]
    sizing_rule: dict[str, Any] = field(default_factory=lambda: {"rule": "equal"})
    risk: StrategyRisk = field(default_factory=StrategyRisk)

    def __post_init__(self):
        self.market = tuple(self.market)
        _check_identity(self)
        e = dict(self.entry_rule)
        rule = e.pop("rule", None)
        if rule not in SCORES:
            raise SpecError(f"entry: rule must be one of {sorted(SCORES)}")
        self._enter, self._exit = e.pop("enter", None), e.pop("exit", None)
        self._direction = e.pop("direction", "long")
        for label, v in (("enter", self._enter), ("exit", self._exit)):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v):
                raise SpecError(f"entry: '{label}' must be a finite number")
        if self._exit > self._enter:
            raise SpecError("entry: 'exit' must not be above 'enter'")
        if self._direction not in ("long", "short", "both"):
            raise SpecError("entry: direction must be long, short or both")
        if self._direction != "long" and self._exit < -self._enter:
            raise SpecError("entry: with shorts, 'exit' must be at or above -'enter' so the two sides cannot overlap")
        self._rule, self._score_params = rule, _params(e, _SCORE_PARAMS[rule], f"entry.{rule}")
        s = dict(self.sizing_rule)
        srule = s.pop("rule", None)
        if srule not in SIZING:
            raise SpecError(f"sizing: rule must be one of {sorted(SIZING)}")
        self._srule, self._size_params = srule, _params(s, _SIZING_PARAMS[srule], f"sizing.{srule}")

    def score(self, prices: pd.DataFrame) -> pd.DataFrame:
        return SCORES[self._rule](prices, **self._score_params)

    def entry(self, prices: pd.DataFrame) -> pd.DataFrame:
        s = self.score(prices)
        out = pd.DataFrame(0, index=s.index, columns=s.columns)
        if self._direction in ("long", "both"):
            out = out.mask(s >= self._enter, 1)
        if self._direction in ("short", "both"):
            out = out.mask(s <= -self._enter, -1)
        return out

    def exit(self, prices: pd.DataFrame, side: int) -> pd.DataFrame:
        s = self.score(prices)
        return (s <= self._exit) if side > 0 else (s >= -self._exit)

    def size(self, prices: pd.DataFrame) -> pd.DataFrame:
        return SIZING[self._srule](prices, self.risk, **self._size_params)

    def strength(self, prices: pd.DataFrame) -> pd.DataFrame:
        return self.score(prices).abs()

    def to_spec(self) -> dict:
        return {"name": self.name, "version": self.version, "hypothesis": self.hypothesis, "market": list(self.market),
                "entry": dict(self.entry_rule), "sizing": dict(self.sizing_rule), "risk": asdict(self.risk)}

    @classmethod
    def from_spec(cls, spec: dict) -> "RuleStrategy":
        if not isinstance(spec, dict):
            raise SpecError("a strategy spec is a JSON object")
        unknown = set(spec) - {"name", "version", "hypothesis", "market", "entry", "sizing", "risk"}
        missing = {"name", "version", "hypothesis", "market", "entry"} - set(spec)
        if unknown or missing:
            raise SpecError(f"spec: unknown keys {sorted(unknown)}, missing keys {sorted(missing)}")
        if not isinstance(spec["entry"], dict) or not isinstance(spec.get("sizing", {}), dict) or not isinstance(spec.get("risk", {}), dict):
            raise SpecError("spec: entry, sizing and risk must be objects")
        if not isinstance(spec["market"], (list, tuple)):
            raise SpecError("spec: market must be a list of symbols")
        try:
            risk = StrategyRisk(**spec.get("risk", {}))
        except TypeError as exc:
            raise SpecError(f"risk: {exc}") from exc
        return cls(spec["name"], spec["version"], spec["hypothesis"], tuple(spec["market"]), dict(spec["entry"]),
                   dict(spec.get("sizing", {"rule": "equal"})), risk)


def bump_version(version: str, part: str = "minor") -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    return {"major": f"{major + 1}.0.0", "minor": f"{major}.{minor + 1}.0", "patch": f"{major}.{minor}.{patch + 1}"}[part]


def save_spec(strategy: RuleStrategy, directory: str | Path) -> Path:
    """Write the strategy as `<name>.json`: one file per strategy, the version inside it."""
    path = Path(directory) / f"{strategy.name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(strategy.to_spec(), indent=2) + "\n")
    return path


def load_strategy(path: str | Path) -> Strategy:
    """Load a strategy file. JSON specs are built from the rule library. A Python file must define
    `STRATEGY` and state the same version and hypothesis in its module docstring."""
    path = Path(path)
    if path.suffix == ".json":
        try:
            return RuleStrategy.from_spec(json.loads(path.read_text()))
        except json.JSONDecodeError as exc:
            raise SpecError(f"{path.name} is not valid JSON: {exc}") from exc
    if path.suffix != ".py":
        raise SpecError("a strategy file is .json or .py")
    module_spec = importlib.util.spec_from_file_location(f"valedia_strategy_{path.stem}", path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    strategy = getattr(module, "STRATEGY", None)
    if not isinstance(strategy, Strategy):
        raise SpecError(f"{path.name} must define STRATEGY, an instance of Strategy")
    _check_identity(strategy)
    doc = module.__doc__ or ""
    if f"version: {strategy.version}" not in doc or "hypothesis:" not in doc:
        raise SpecError(f"{path.name}: the module docstring must carry 'version: {strategy.version}' and a 'hypothesis:' line")
    return strategy
