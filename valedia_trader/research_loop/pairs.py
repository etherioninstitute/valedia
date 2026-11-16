"""Pairs: a second kind of strategy, one position with two legs that open and close together.

A pairs strategy is a JSON spec with `"kind": "pairs"`, a closed set of parameters with bounds (nothing a
model writes is executed):

    {"kind": "pairs", "name": ..., "version": ..., "hypothesis": ...,
     "pairs": [["KO", "PEP"], ["XOM", "CVX"]],
     "signal": {"hedge": "ratio" | "log_ratio" | "ols", "window": 20, "enter": 1.5, "exit": 0.5, "stop_z": null,
                "hedge_window": 120 (ols only)},
     "legs": "both" | "long",
     "sizing": {"pair_weight": 0.10, "max_open_pairs": 3},
     "risk": StrategyRisk fields except max_open_positions, which is derived}

The spread of a pair (A, B) is A/B (`ratio`), ln A - ln B (`log_ratio`), or ln A - beta_t ln B - alpha_t (`ols`,
beta_t and alpha_t the least-squares fit of ln A on ln B over the last `hedge_window` bars ending at t, and the
whole z window computed with that bar's fit). z_t is the spread's distance from its mean over the last `window`
bars including t, in sample standard deviations. Long the spread (long A, short B) opens at z_t <= -enter and
closes at z_t >= -exit; short the spread mirrors it. `PairsStrategy.walk` does the walk; `run_rules` hands a
pairs strategy over to it, so the backtester, the gauntlet and the paper trader run it with no change.
"""
from __future__ import annotations

import math
from dataclasses import asdict
from typing import Any

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from valedia_trader.research_loop.strategy import SpecError, Strategy, StrategyRisk, _check_identity

__all__ = ["PairsStrategy", "HEDGES", "LEGS"]

HEDGES = ("ratio", "log_ratio", "ols")
LEGS = ("both", "long")
_TOP = {"kind", "name", "version", "hypothesis", "pairs", "signal", "legs", "sizing", "risk"}
_REQUIRED = {"kind", "name", "version", "hypothesis", "pairs", "signal"}
_SIGNAL = {"hedge", "window", "enter", "exit", "stop_z", "hedge_window"}
_SIGNAL_REQUIRED = {"hedge", "window", "enter", "exit"}
_SIZING = {"pair_weight": 0.10, "max_open_pairs": 3}
_RISK = {"max_position_pct", "max_gross", "stop_loss_pct", "take_profit_pct", "max_holding_days"}
_BETA_RANGE = (0.1, 10.0)        # ols: no entry unless the bar's hedge ratio is in this range
_ROWS = 512                      # rows per block in the windowed arithmetic (memory, not results)
_NOT_WALKED = "a pairs strategy is walked as pairs (PairsStrategy.walk); it has no per-symbol entry, exit or size"


def _number(v: Any, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise SpecError(f"{what} must be a finite number")
    return v


def _whole(v: Any, lo: int, hi: int, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or int(v) != v or not lo <= v <= hi:
        raise SpecError(f"{what} must be a whole number in [{lo}, {hi}]")
    return int(v)


def _object(v: Any, what: str) -> dict:
    if not isinstance(v, dict):
        raise SpecError(f"{what} must be an object")
    return v


class PairsStrategy(Strategy):
    """A pairs strategy built from its spec (`from_spec`), walked by `walk`."""
    rebalance_days = None

    def __init__(self, spec: dict) -> None:
        if not isinstance(spec, dict):
            raise SpecError("a strategy spec is a JSON object")
        unknown, missing = set(spec) - _TOP, _REQUIRED - set(spec)
        if unknown or missing:
            raise SpecError(f"spec: unknown keys {sorted(unknown)}, missing keys {sorted(missing)}")
        if spec["kind"] != "pairs":
            raise SpecError("kind must be 'pairs' for a pairs strategy")
        self.name, self.version, self.hypothesis = spec["name"], spec["version"], spec["hypothesis"]
        self.pairs = self._read_pairs(spec["pairs"])
        self.market = tuple(dict.fromkeys(sym for pair in self.pairs for sym in pair))
        self.legs = spec.get("legs", "both")
        if self.legs not in LEGS:
            raise SpecError(f"legs must be one of {list(LEGS)}")
        if self.legs != "long" and any(sym.endswith("-USD") for sym in self.market):
            raise SpecError("a pair with a spot crypto symbol (-USD) must be legs 'long': spot crypto is not shorted here")
        self._read_signal(_object(spec["signal"], "signal"))
        sizing = _object(spec.get("sizing", {}), "sizing")
        if set(sizing) - set(_SIZING):
            raise SpecError(f"sizing: unknown keys {sorted(set(sizing) - set(_SIZING))} (allowed: {sorted(_SIZING)})")
        pw = _number(sizing.get("pair_weight", _SIZING["pair_weight"]), "sizing.pair_weight")
        if not 0.0 < pw <= 1.0:
            raise SpecError("sizing.pair_weight must be in (0, 1]")
        self.pair_weight = float(pw)
        self.max_open_pairs = _whole(sizing.get("max_open_pairs", _SIZING["max_open_pairs"]), 1, 40, "sizing.max_open_pairs")
        risk = dict(_object(spec.get("risk", {}), "risk"))
        if "max_open_positions" in risk:
            raise SpecError("risk: a pairs strategy may not set max_open_positions; it is derived from max_open_pairs and legs")
        if set(risk) - _RISK:
            raise SpecError(f"risk: unknown keys {sorted(set(risk) - _RISK)} (allowed: {sorted(_RISK)})")
        for key, v in risk.items():
            if v is None and key in ("stop_loss_pct", "take_profit_pct", "max_holding_days"):
                continue
            _number(v, f"risk.{key}")
            if key == "max_holding_days":
                risk[key] = _whole(v, 1, 100_000, "risk.max_holding_days")
            else:
                risk[key] = float(v)
        per_pair = 2 if self.legs == "both" else 1
        self.risk = StrategyRisk(**risk, max_open_positions=per_pair * self.max_open_pairs)
        _check_identity(self)

    @staticmethod
    def _read_pairs(raw: Any) -> tuple[tuple[str, str], ...]:
        if not isinstance(raw, (list, tuple)) or not 1 <= len(raw) <= 40:
            raise SpecError("pairs must be a list of 1 to 40 pairs")
        seen, out = set(), []
        for p in raw:
            if not isinstance(p, (list, tuple)) or len(p) != 2 or not all(isinstance(s, str) and s for s in p):
                raise SpecError("each pair is two symbols, e.g. [\"KO\", \"PEP\"]")
            if p[0] == p[1]:
                raise SpecError(f"pair {list(p)}: the two symbols must differ")
            key = frozenset(p)
            if key in seen:
                raise SpecError(f"pair {list(p)} appears twice (in either order)")
            seen.add(key)
            out.append((p[0], p[1]))
        return tuple(out)

    def _read_signal(self, sig: dict) -> None:
        unknown, missing = set(sig) - _SIGNAL, _SIGNAL_REQUIRED - set(sig)
        if unknown or missing:
            raise SpecError(f"signal: unknown keys {sorted(unknown)}, missing keys {sorted(missing)}")
        if sig["hedge"] not in HEDGES:
            raise SpecError(f"signal.hedge must be one of {list(HEDGES)}")
        self.hedge = sig["hedge"]
        self.window = _whole(sig["window"], 5, 504, "signal.window")
        enter, exit_ = _number(sig["enter"], "signal.enter"), _number(sig["exit"], "signal.exit")
        if not 0.0 < enter <= 10.0:
            raise SpecError("signal.enter must be in (0, 10]")
        if not 0.0 <= exit_ < enter:
            raise SpecError("signal.exit must be in [0, enter), strictly below enter")
        self.enter, self.exit_z = float(enter), float(exit_)
        stop = sig.get("stop_z")
        if stop is not None:
            stop = _number(stop, "signal.stop_z")
            if not enter < stop <= 20.0:
                raise SpecError("signal.stop_z must be null or in (enter, 20]")
            stop = float(stop)
        self.stop_z = stop
        if self.hedge == "ols":
            if "hedge_window" not in sig:
                raise SpecError("signal.hedge_window is required with hedge 'ols'")
            self.hedge_window = _whole(sig["hedge_window"], 20, 1260, "signal.hedge_window")
            if self.hedge_window < self.window:
                raise SpecError("signal.hedge_window must be at least signal.window")
        else:
            if "hedge_window" in sig:
                raise SpecError("signal.hedge_window is only for hedge 'ols'")
            self.hedge_window = None

    # ---- the spec

    @classmethod
    def from_spec(cls, spec: dict) -> "PairsStrategy":
        return cls(spec)

    def to_spec(self) -> dict:
        signal = {"hedge": self.hedge, "window": self.window, "enter": self.enter, "exit": self.exit_z, "stop_z": self.stop_z}
        if self.hedge == "ols":
            signal["hedge_window"] = self.hedge_window
        risk = {k: v for k, v in asdict(self.risk).items() if k != "max_open_positions"}
        return {"kind": "pairs", "name": self.name, "version": self.version, "hypothesis": self.hypothesis,
                "pairs": [list(p) for p in self.pairs], "signal": signal, "legs": self.legs,
                "sizing": {"pair_weight": self.pair_weight, "max_open_pairs": self.max_open_pairs}, "risk": risk}

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PairsStrategy) and self.to_spec() == other.to_spec()

    __hash__ = None

    def __repr__(self) -> str:
        return f"PairsStrategy({self.name} v{self.version}, {len(self.pairs)} pairs, {self.hedge})"

    # ---- no per-symbol rules

    def entry(self, prices: pd.DataFrame) -> pd.DataFrame:
        raise SpecError(_NOT_WALKED)

    def exit(self, prices: pd.DataFrame, side: int) -> pd.DataFrame:
        raise SpecError(_NOT_WALKED)

    def size(self, prices: pd.DataFrame) -> pd.DataFrame:
        raise SpecError(_NOT_WALKED)

    # ---- the signal

    def _signal(self, a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(z, beta) for one pair, one value per bar; NaN where there is none. Prices that are missing, not finite
        or not positive count as missing. Each bar is computed from its own window only, so a bar's value does not
        depend on how much history follows it."""
        n, w = len(a), self.window
        z, beta = np.full(n, np.nan), np.full(n, np.nan)
        if self.hedge == "ols":
            hw = self.hedge_window
            if n < hw:
                return z, beta
            with np.errstate(divide="ignore", invalid="ignore"):
                la, lb = np.log(a), np.log(b)
            LA, LB = sliding_window_view(la, hw), sliding_window_view(lb, hw)          # row i ends at bar i + hw - 1
            SA, SB = sliding_window_view(la, w)[hw - w:], sliding_window_view(lb, w)[hw - w:]
            for i0 in range(0, len(LA), _ROWS):
                rows = slice(i0, i0 + _ROWS)
                y, x = LA[rows], LB[rows]
                with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                    xm, ym = x.mean(axis=1), y.mean(axis=1)
                    xc = x - xm[:, None]
                    bt = (xc * (y - ym[:, None])).sum(axis=1) / (xc * xc).sum(axis=1)
                    al = ym - bt * xm
                    bt[~(np.ptp(x, axis=1) > 0) | ~np.isfinite(bt)] = np.nan       # a constant ln B: no fit
                    s = SA[rows] - bt[:, None] * SB[rows] - al[:, None]
                    beta[i0 + hw - 1: i0 + hw - 1 + len(bt)] = bt
                    z[i0 + hw - 1: i0 + hw - 1 + len(bt)] = self._z_rows(s)
            return z, beta
        if n < w:
            return z, beta
        with np.errstate(divide="ignore", invalid="ignore"):
            spread = a / b if self.hedge == "ratio" else np.log(a) - np.log(b)
        S = sliding_window_view(spread, w)                                               # row i ends at bar i + w - 1
        for i0 in range(0, len(S), _ROWS):
            rows = S[i0: i0 + _ROWS]
            z[i0 + w - 1: i0 + w - 1 + len(rows)] = self._z_rows(rows)
        return z, beta

    @staticmethod
    def _z_rows(s: np.ndarray) -> np.ndarray:
        """z of each row's last value against the row: NaN for a row with a missing value or a deviation that is
        zero (to within rounding: at most 1e-12 of the row's largest magnitude, and of 1) or not finite."""
        with np.errstate(invalid="ignore", over="ignore"):
            m = s.mean(axis=1)
            sd = s.std(axis=1, ddof=1)
            scale = np.maximum(1.0, np.abs(s).max(axis=1))
            ok = np.isfinite(sd) & np.isfinite(m) & (sd > 1e-12 * scale)
            return np.where(ok, (s[:, -1] - m) / np.where(ok, sd, 1.0), np.nan)

    # ---- the walk

    def walk(self, prices: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
        """Walk the pairs forward. Returns (weights, events) in `run_rules`' shape: weights has every column of
        `prices` (zero outside the market) and row t is decided from prices up to and including t.

        Each bar: exits first (stop loss, take profit, time limit, z stop, exit rule; on the pair's result r_t),
        then entries into free slots by |z_t| (ties by the pair's order in the spec). A pair closed on a bar opens
        nothing on that bar; after an exit other than the exit rule it opens nothing until a later bar with
        |z_t| <= exit. A bar on which either leg has no price opens nothing and carries an open pair unchanged
        (its day count does not advance). The book is the sum of the open pairs' leg weights per symbol, scaled
        down to `risk.max_gross`."""
        _check_identity(self)
        cols = [c for c in self.market if c in prices.columns]
        if not cols:
            raise SpecError(f"none of the strategy's market {self.market} is in the price data")
        n, risk = len(prices), self.risk
        price: dict[str, np.ndarray] = {}
        for sym in self.market:
            if sym in prices.columns:
                p = pd.to_numeric(prices[sym], errors="coerce").to_numpy(dtype=float, copy=True)
                p[~(np.isfinite(p) & (p > 0))] = np.nan
            else:
                p = np.full(n, np.nan)
            price[sym] = p
        k = len(self.pairs)
        sig = [self._signal(price[a], price[b]) for a, b in self.pairs]
        z = np.array([s[0] for s in sig]).reshape(k, n)
        beta = np.array([s[1] for s in sig]).reshape(k, n)
        names = [f"{a}/{b}" for a, b in self.pairs]
        col = {sym: j for j, sym in enumerate(cols)}
        side = [0] * k
        wgt = [(0.0, 0.0)] * k                  # signed weights of the legs held (0.0 for a leg not taken)
        px0 = [(math.nan, math.nan)] * k
        held, armed = [0] * k, [True] * k
        out = np.zeros((n, len(cols)))
        events: list[dict] = []
        dates = prices.index
        lo, hi = _BETA_RANGE

        def zval(i: int, t: int) -> float | None:
            return float(z[i, t]) if np.isfinite(z[i, t]) else None

        def hedge_of(i: int, t: int) -> float | None:
            return float(beta[i, t]) if self.hedge == "ols" and np.isfinite(beta[i, t]) else None

        def emit(i: int, t: int, entering: bool, reason: str, pnl: float = 0.0) -> None:
            """One event per held leg: the first held leg in the pair's order carries `enter` / `exit` (the one the
            trade statistics count), the other `enter_leg` / `exit_leg`. `side` is the leg's own side."""
            held_legs = [(sym, wl) for sym, wl in zip(self.pairs[i], wgt[i]) if wl != 0.0]
            for n_leg, (sym, wl) in enumerate(held_legs):
                action = ("enter" if entering else "exit") + ("" if n_leg == 0 else "_leg")
                ev = {"date": dates[t], "symbol": sym, "action": action, "side": 1 if wl > 0 else -1,
                      "price": float(price[sym][t]), "reason": reason, "pair": names[i], "z": zval(i, t), "hedge": hedge_of(i, t)}
                ev.update({"weight": abs(wl)} if entering else {"pnl_pct": float(pnl)})
                events.append(ev)

        for t in range(n):
            closed = set()
            for i in range(k):
                if side[i] == 0:
                    continue
                a, b = self.pairs[i]
                pa, pb = price[a][t], price[b][t]
                if not (np.isfinite(pa) and np.isfinite(pb)):
                    continue                                         # a leg has no price today: carried unchanged
                held[i] += 1
                wa, wb = wgt[i]
                r = (wa * (pa / px0[i][0] - 1.0) if wa else 0.0) + (wb * (pb / px0[i][1] - 1.0) if wb else 0.0)
                r /= abs(wa) + abs(wb)
                zt, s = z[i, t], side[i]
                reason = None
                if risk.stop_loss_pct is not None and r <= -risk.stop_loss_pct:
                    reason = f"stop loss: {r:+.1%} against the pair"
                elif risk.take_profit_pct is not None and r >= risk.take_profit_pct:
                    reason = f"take profit: {r:+.1%}"
                elif risk.max_holding_days is not None and held[i] >= risk.max_holding_days:
                    reason = f"time limit: held {held[i]} days"
                elif self.stop_z is not None and np.isfinite(zt) and ((s > 0 and zt <= -self.stop_z) or (s < 0 and zt >= self.stop_z)):
                    reason = f"z stop: z {zt:+.2f} beyond {self.stop_z:g}"
                elif np.isfinite(zt) and ((s > 0 and zt >= -self.exit_z) or (s < 0 and zt <= self.exit_z)):
                    reason = f"exit rule: z {zt:+.2f}"
                if reason:
                    emit(i, t, False, reason, r)
                    side[i], wgt[i], px0[i], held[i] = 0, (0.0, 0.0), (math.nan, math.nan), 0
                    armed[i] = reason.startswith("exit rule")
                    closed.add(i)
            want = []
            for i in range(k):
                if side[i] != 0 or i in closed:
                    continue
                zt = z[i, t]
                if not np.isfinite(zt):
                    continue
                if not armed[i]:
                    armed[i] = abs(zt) <= self.exit_z                # re-armed: the spread came back inside exit
                    continue
                a, b = self.pairs[i]
                if not (np.isfinite(price[a][t]) and np.isfinite(price[b][t])) or abs(zt) < self.enter:
                    continue
                if self.hedge == "ols" and not (np.isfinite(beta[i, t]) and lo <= beta[i, t] <= hi):
                    continue
                want.append(i)
            free = self.max_open_pairs - sum(1 for s in side if s != 0)
            for i in sorted(want, key=lambda j: (-abs(z[j, t]), j))[:max(free, 0)]:
                a, b = self.pairs[i]
                s = 1 if z[i, t] <= -self.enter else -1
                if self.hedge == "ols":
                    bt = float(beta[i, t])
                    ga, gb = self.pair_weight / (1.0 + bt), self.pair_weight * bt / (1.0 + bt)
                else:
                    ga = gb = self.pair_weight / 2.0
                big = max(ga, gb)
                if big > risk.max_position_pct:
                    ga, gb = ga * risk.max_position_pct / big, gb * risk.max_position_pct / big
                wa, wb = (ga, -gb) if s > 0 else (-ga, gb)
                if self.legs == "long":
                    wa, wb = max(wa, 0.0), max(wb, 0.0)
                side[i], wgt[i], px0[i], held[i] = s, (wa, wb), (float(price[a][t]), float(price[b][t])), 0
                emit(i, t, True, f"entry rule: z {z[i, t]:+.2f} on {names[i]}")
            row = np.zeros(len(cols))
            for i in range(k):
                if side[i] != 0:
                    for sym, wl in zip(self.pairs[i], wgt[i]):
                        if wl != 0.0:
                            row[col[sym]] += wl
            gross = np.abs(row).sum()
            out[t] = row * (risk.max_gross / gross) if gross > risk.max_gross else row
        weights = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        weights[cols] = out
        return weights, events
