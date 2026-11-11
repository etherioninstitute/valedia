"""Level 9: market microstructure and execution."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

__all__ = [
    "mid_price", "quoted_spread", "spread_bps", "effective_spread", "realized_spread", "maker_taker_decision",
    "RoutingDecision", "order_book_imbalance", "micro_price", "depth_weighted_imbalance", "sqrt_impact",
    "impact_cost", "max_order_for_impact", "vwap", "twap_schedule", "vwap_schedule",
    "implementation_shortfall", "almgren_chriss", "ACSchedule", "as_reservation_price", "as_optimal_spread",
    "as_quotes", "Quotes", "simulate_market_maker", "MMSimResult",
]

FORMULAS: dict[str, tuple[str, ...]] = {
    "9.1": ("mid_price", "quoted_spread", "spread_bps", "effective_spread", "realized_spread",
            "maker_taker_decision"),
    "9.2": ("order_book_imbalance", "micro_price", "depth_weighted_imbalance"),
    "9.3": ("sqrt_impact", "impact_cost", "max_order_for_impact"),
    "9.4": ("vwap", "twap_schedule", "vwap_schedule", "implementation_shortfall", "almgren_chriss"),
    "9.5": ("as_reservation_price", "as_optimal_spread", "as_quotes", "simulate_market_maker"),
}


def _quotes(bid, ask) -> tuple[np.ndarray, np.ndarray]:
    b, a = np.asarray(bid, dtype=float), np.asarray(ask, dtype=float)
    if not (np.all(np.isfinite(b)) and np.all(np.isfinite(a))):
        raise ValueError("quotes must be finite")
    if np.any(b <= 0) or np.any(a < b):
        raise ValueError("require 0 < bid <= ask (crossed or non-positive book)")
    return b, a


def _scalar(x):
    x = np.asarray(x)
    return float(x) if x.ndim == 0 else x


# ---------------------------------------------------------------- 9.1 spread and routing
def mid_price(bid, ask):
    """9.1 Mid price: (bid + ask) / 2."""
    b, a = _quotes(bid, ask)
    return _scalar(0.5 * (b + a))


def quoted_spread(bid, ask):
    """9.1 Quoted spread: ask - bid."""
    b, a = _quotes(bid, ask)
    return _scalar(a - b)


def spread_bps(bid, ask):
    """9.1 Quoted spread in basis points of the mid: (ask - bid) / mid * 1e4."""
    b, a = _quotes(bid, ask)
    return _scalar((a - b) / (0.5 * (a + b)) * 1e4)


def effective_spread(trade_price, mid):
    """9.1 Effective spread: 2 * |trade price - mid at the time of the trade|."""
    return _scalar(2.0 * np.abs(np.asarray(trade_price, dtype=float) - np.asarray(mid, dtype=float)))


def realized_spread(trade_price, later_mid, side):
    """9.1 Realized spread: 2 * side * (trade price - mid some time later); side +1 buy, -1 sell.

    What the liquidity provider keeps after the price has moved against them."""
    s = np.asarray(side, dtype=float)
    if np.any(np.abs(s) != 1):
        raise ValueError("side must be +1 (buy) or -1 (sell)")
    return _scalar(2.0 * s * (np.asarray(trade_price, dtype=float) - np.asarray(later_mid, dtype=float)))


@dataclass(frozen=True)
class RoutingDecision:
    route: str              # "maker" or "taker"
    taker_cost_bps: float
    maker_cost_bps: float


def maker_taker_decision(spread_bps_value: float, taker_fee_bps: float, maker_fee_bps: float,
                         fill_probability: float, adverse_selection_bps: float = 0.0,
                         delay_cost_bps: float = 0.0, urgency: float = 0.0) -> RoutingDecision:
    """9.1 Maker-versus-taker routing by expected cost in bps.

    taker: half spread + taker fee.
    maker: p * (maker fee - half spread + adverse selection) + (1 - p) * (taker cost + delay cost)
           + urgency * delay cost, with urgency in [0, 1]. Maker fee may be negative (a rebate)."""
    vals = (spread_bps_value, taker_fee_bps, maker_fee_bps, fill_probability, adverse_selection_bps,
            delay_cost_bps, urgency)
    if not all(np.isfinite(v) for v in vals):
        raise ValueError("routing inputs must be finite")
    if spread_bps_value < 0 or not 0.0 <= fill_probability <= 1.0 or not 0.0 <= urgency <= 1.0:
        raise ValueError("spread must be >= 0, fill_probability and urgency in [0, 1]")
    if adverse_selection_bps < 0 or delay_cost_bps < 0:
        raise ValueError("adverse selection and delay costs must be non-negative")
    half = 0.5 * spread_bps_value
    taker = half + taker_fee_bps
    maker = (fill_probability * (maker_fee_bps - half + adverse_selection_bps)
             + (1.0 - fill_probability) * (taker + delay_cost_bps) + urgency * delay_cost_bps)
    return RoutingDecision("maker" if maker < taker else "taker", float(taker), float(maker))


# ---------------------------------------------------------------- 9.2 imbalance and micro-price
def _sizes(bid_qty, ask_qty) -> tuple[np.ndarray, np.ndarray]:
    qb, qa = np.asarray(bid_qty, dtype=float), np.asarray(ask_qty, dtype=float)
    if not (np.all(np.isfinite(qb)) and np.all(np.isfinite(qa))) or np.any(qb < 0) or np.any(qa < 0):
        raise ValueError("sizes must be finite and non-negative")
    return qb, qa


def order_book_imbalance(bid_qty, ask_qty):
    """9.2 Order-book imbalance: OBI = (Q_b - Q_a) / (Q_b + Q_a), in [-1, 1]; 0 for an empty book."""
    qb, qa = _sizes(bid_qty, ask_qty)
    tot = qb + qa
    return _scalar(np.divide(qb - qa, tot, out=np.zeros(np.broadcast(qb, qa).shape), where=tot > 0))


def micro_price(bid, ask, bid_qty, ask_qty):
    """9.2 Stoikov micro-price: (Q_b * ask + Q_a * bid) / (Q_b + Q_a); the mid for an empty book."""
    b, a = _quotes(bid, ask)
    qb, qa = _sizes(bid_qty, ask_qty)
    tot = qb + qa
    mid = np.array(np.broadcast_to(0.5 * (b + a), np.broadcast(b, a, qb, qa).shape), dtype=float)
    return _scalar(np.divide(qb * a + qa * b, tot, out=mid, where=tot > 0))


def depth_weighted_imbalance(bid_qty, ask_qty, decay: float = 0.5):
    """9.2 Multi-level imbalance over L1..Ln (last axis = level) with weights decay^level."""
    if not 0.0 < decay <= 1.0:
        raise ValueError("decay must be in (0, 1]")
    qb, qa = _sizes(bid_qty, ask_qty)
    if qb.shape != qa.shape or qb.ndim == 0:
        raise ValueError("bid and ask depth arrays must share a shape with levels on the last axis")
    w = decay ** np.arange(qb.shape[-1])
    wb, wa = (qb * w).sum(axis=-1), (qa * w).sum(axis=-1)
    tot = wb + wa
    return _scalar(np.divide(wb - wa, tot, out=np.zeros(np.shape(tot)), where=tot > 0))


# ---------------------------------------------------------------- 9.3 square-root impact
def sqrt_impact(order_qty, daily_volume, sigma, Y: float = 1.0):
    """9.3 Square-root law: dP/P = Y * sigma * sqrt(Q / V) (sigma = daily volatility, V = daily volume)."""
    q, v, s = (np.asarray(x, dtype=float) for x in (order_qty, daily_volume, sigma))
    if np.any(q < 0) or np.any(v <= 0) or np.any(s < 0) or Y <= 0:
        raise ValueError("require order_qty >= 0, daily_volume > 0, sigma >= 0, Y > 0")
    return _scalar(Y * s * np.sqrt(q / v))


def impact_cost(order_qty, price, daily_volume, sigma, Y: float = 1.0):
    """9.3 Pre-trade slippage deduction in currency: impact fraction * price * quantity."""
    return _scalar(np.asarray(sqrt_impact(order_qty, daily_volume, sigma, Y))
                   * np.asarray(price, dtype=float) * np.asarray(order_qty, dtype=float))


def max_order_for_impact(impact_budget_bps: float, daily_volume: float, sigma: float, Y: float = 1.0) -> float:
    """9.3 Largest order whose expected impact stays within a bps budget: Q = V * (budget / (Y sigma))^2."""
    if impact_budget_bps < 0 or daily_volume <= 0 or Y <= 0:
        raise ValueError("require budget >= 0, daily_volume > 0, Y > 0")
    if sigma <= 0:
        raise ValueError("sigma must be positive to size against impact")
    return float(daily_volume * (impact_budget_bps * 1e-4 / (Y * sigma)) ** 2)


# ---------------------------------------------------------------- 9.4 schedules and shortfall
def vwap(prices, quantities) -> float:
    """9.4 Volume-weighted average price: sum(p q) / sum(q)."""
    p, q = np.asarray(prices, dtype=float).ravel(), np.asarray(quantities, dtype=float).ravel()
    if p.size != q.size or p.size == 0:
        raise ValueError("prices and quantities must be non-empty and the same length")
    if np.any(q < 0) or q.sum() <= 0:
        raise ValueError("quantities must be non-negative with a positive total")
    return float(p @ q / q.sum())


def twap_schedule(total_qty: float, n_slices: int) -> np.ndarray:
    """9.4 TWAP: equal child orders across n time slices."""
    if n_slices < 1:
        raise ValueError("n_slices must be >= 1")
    return np.full(n_slices, total_qty / n_slices)


def vwap_schedule(total_qty: float, volume_profile) -> np.ndarray:
    """9.4 VWAP schedule: child orders proportional to the expected volume profile."""
    v = np.asarray(volume_profile, dtype=float).ravel()
    if v.size == 0 or np.any(v < 0) or v.sum() <= 0 or not np.all(np.isfinite(v)):
        raise ValueError("volume profile must be non-negative with a positive total")
    return total_qty * v / v.sum()


def implementation_shortfall(side: int, arrival_price: float, fill_prices, fill_qtys,
                             fees: float = 0.0) -> tuple[float, float]:
    """9.4 Implementation shortfall versus arrival price: (currency, bps of arrival notional)."""
    if side not in (1, -1) or arrival_price <= 0:
        raise ValueError("side must be +1 or -1 and arrival_price positive")
    p, q = np.asarray(fill_prices, dtype=float).ravel(), np.asarray(fill_qtys, dtype=float).ravel()
    if p.size != q.size or q.sum() <= 0 or np.any(q < 0):
        raise ValueError("fills must be non-negative quantities with a positive total")
    cost = float(side * (p - arrival_price) @ q + fees)
    return cost, cost / (arrival_price * q.sum()) * 1e4


@dataclass(frozen=True)
class ACSchedule:
    holdings: np.ndarray      # x_0 .. x_n, x_0 = X, x_n = 0
    trades: np.ndarray        # n child orders summing to X
    expected_cost: float
    variance: float
    kappa: float


def almgren_chriss(total_qty: float, n_slices: int, T: float, sigma: float, eta: float, risk_aversion: float,
                   gamma_perm: float = 0.0, epsilon: float = 0.0) -> ACSchedule:
    """9.4 Almgren-Chriss optimal execution minimizing E[IS] + lambda Var(IS) (discrete-time solution).

    x_j = X sinh(kappa (T - t_j)) / sinh(kappa T) with cosh(kappa tau) = 1 + lambda sigma^2 tau^2 / (2 eta~),
    eta~ = eta - gamma tau / 2. sigma is price volatility per sqrt(time), eta temporary impact,
    gamma_perm permanent impact, epsilon a fixed half-spread cost. lambda -> 0 gives TWAP."""
    if n_slices < 1 or T <= 0 or sigma < 0 or eta <= 0 or risk_aversion < 0 or gamma_perm < 0 or epsilon < 0:
        raise ValueError("invalid Almgren-Chriss parameters")
    tau = T / n_slices
    eta_t = eta - 0.5 * gamma_perm * tau
    if eta_t <= 0:
        raise ValueError("eta - gamma_perm * tau / 2 must be positive")
    kappa = math.acosh(1.0 + risk_aversion * sigma**2 * tau**2 / (2.0 * eta_t)) / tau
    t = tau * np.arange(n_slices + 1)
    if kappa * T < 1e-7:
        holdings = total_qty * (1.0 - t / T)
    else:
        holdings = total_qty * np.sinh(kappa * (T - t)) / math.sinh(kappa * T)
    holdings[-1] = 0.0
    trades = -np.diff(holdings)
    cost = (0.5 * gamma_perm * total_qty**2 + epsilon * float(np.abs(trades).sum())
            + eta_t / tau * float(trades @ trades))
    return ACSchedule(holdings, trades, float(cost), float(sigma**2 * tau * (holdings[1:] ** 2).sum()), float(kappa))


# ---------------------------------------------------------------- 9.5 Avellaneda-Stoikov
def _as_check(gamma: float, sigma: float, time_left: float) -> None:
    if gamma <= 0 or sigma < 0 or time_left < 0 or not all(np.isfinite(v) for v in (gamma, sigma, time_left)):
        raise ValueError("require gamma > 0, sigma >= 0, time_left >= 0 (all finite)")


def as_reservation_price(mid: float, inventory: float, gamma: float, sigma: float, time_left: float) -> float:
    """9.5 Avellaneda-Stoikov reservation price: r = s - q * gamma * sigma^2 * (T - t)."""
    _as_check(gamma, sigma, time_left)
    return mid - inventory * gamma * sigma**2 * time_left


def as_optimal_spread(gamma: float, sigma: float, time_left: float, kappa: float) -> float:
    """9.5 Optimal total spread: gamma * sigma^2 * (T - t) + (2 / gamma) * ln(1 + gamma / kappa)."""
    _as_check(gamma, sigma, time_left)
    if kappa <= 0:
        raise ValueError("kappa must be positive")
    return gamma * sigma**2 * time_left + (2.0 / gamma) * math.log1p(gamma / kappa)


@dataclass(frozen=True)
class Quotes:
    bid: float | None       # None when the side is pulled by the inventory limit
    ask: float | None
    reservation: float
    spread: float


def as_quotes(mid: float, inventory: float, gamma: float, sigma: float, time_left: float, kappa: float,
              tick: float = 0.0, max_inventory: float | None = None, min_spread: float = 0.0) -> Quotes:
    """9.5 Inventory-skewed two-sided quotes around the reservation price.

    Bids round down and asks round up to `tick`; the spread never falls below `min_spread`; the side
    that would push inventory through +-max_inventory is pulled."""
    if not np.isfinite(mid) or mid <= 0 or not np.isfinite(inventory):
        raise ValueError("mid must be positive and inventory finite")
    if tick < 0 or min_spread < 0:
        raise ValueError("tick and min_spread must be non-negative")
    r = as_reservation_price(mid, inventory, gamma, sigma, time_left)
    spread = max(as_optimal_spread(gamma, sigma, time_left, kappa), min_spread)
    bid, ask = r - 0.5 * spread, r + 0.5 * spread
    if tick > 0:
        bid, ask = math.floor(bid / tick + 1e-9) * tick, math.ceil(ask / tick - 1e-9) * tick
    out_bid: float | None = bid if bid > 0 else None
    out_ask: float | None = ask
    if max_inventory is not None:
        if max_inventory <= 0:
            raise ValueError("max_inventory must be positive")
        if inventory >= max_inventory:
            out_bid = None
        if inventory <= -max_inventory:
            out_ask = None
    return Quotes(out_bid, out_ask, float(r), float(ask - bid))


@dataclass(frozen=True)
class MMSimResult:
    pnl: np.ndarray                 # terminal mark-to-market P&L per path
    final_inventory: np.ndarray
    max_abs_inventory: np.ndarray


def simulate_market_maker(n_paths: int, n_steps: int, T: float, sigma: float, gamma: float, kappa: float,
                          arrival_rate: float, rng: np.random.Generator, s0: float = 100.0,
                          inventory_aware: bool = True, max_inventory: float | None = None) -> MMSimResult:
    """9.5 Simulate Avellaneda-Stoikov quoting on an arithmetic Brownian mid with Poisson fills of
    intensity A * exp(-kappa * delta). inventory_aware=False quotes symmetrically around the mid."""
    if n_paths < 1 or n_steps < 1 or T <= 0 or arrival_rate <= 0 or kappa <= 0:
        raise ValueError("invalid simulation parameters")
    _as_check(gamma, sigma, T)
    dt = T / n_steps
    s = np.full(n_paths, float(s0))
    q, cash, max_abs = np.zeros(n_paths), np.zeros(n_paths), np.zeros(n_paths)
    base = (2.0 / gamma) * math.log1p(gamma / kappa)
    for i in range(n_steps):
        left = T - i * dt
        r = s - q * gamma * sigma**2 * left if inventory_aware else s
        half = 0.5 * (gamma * sigma**2 * left + base)
        bid, ask = r - half, r + half
        p_bid = np.clip(arrival_rate * np.exp(-kappa * (s - bid)) * dt, 0.0, 1.0)
        p_ask = np.clip(arrival_rate * np.exp(-kappa * (ask - s)) * dt, 0.0, 1.0)
        hit_bid, hit_ask = rng.random(n_paths) < p_bid, rng.random(n_paths) < p_ask
        if max_inventory is not None:
            hit_bid &= q < max_inventory
            hit_ask &= q > -max_inventory
        q = q + hit_bid - hit_ask
        cash = cash - bid * hit_bid + ask * hit_ask
        max_abs = np.maximum(max_abs, np.abs(q))
        s = s + sigma * math.sqrt(dt) * rng.standard_normal(n_paths)
    return MMSimResult(cash + q * s, q, max_abs)
