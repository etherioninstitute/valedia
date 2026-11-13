"""The kill switch. It lives in code: every order goes through `check_order` before it can reach a
broker, and a False answer is final whatever an agent argues.

Limits come from `config/risk.json`. A missing or malformed file blocks every order (fail closed),
and so does any error while checking. Orders that only reduce an existing position are let through
the loss, drawdown and position-count checks, so a stop can always get out; an operator halt file
blocks those too.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from valedia_trader.research_loop import REPO

__all__ = ["DEFAULT_LIMITS", "OPTIONAL_LIMITS", "RiskConfigError", "load_limits", "write_default_limits", "check_order", "HALT_FILE"]

# The guide's example numbers. They are placeholders, not advice: set your own in config/risk.json.
DEFAULT_LIMITS = {"max_daily_loss_pct": 2.0, "max_drawdown_pct": 8.0, "max_position_pct": 0.10, "max_open_positions": 5}
# Keys the file may also carry. check_order does not read them: max_orders_per_day caps the order step's sending.
OPTIONAL_LIMITS = ("max_orders_per_day",)
HALT_FILE = REPO / "state" / "halt.json"          # the legacy operator kill switch; its presence stops everything


class RiskConfigError(RuntimeError):
    pass


def load_limits(path: str | Path) -> dict:
    """Read and validate the limits file. Raises RiskConfigError on anything unexpected."""
    try:
        limits = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RiskConfigError(f"cannot read risk limits from {path}: {exc}") from exc
    if not isinstance(limits, dict) or not set(DEFAULT_LIMITS) <= set(limits) <= set(DEFAULT_LIMITS) | set(OPTIONAL_LIMITS):
        raise RiskConfigError(f"risk limits must have exactly the keys {sorted(DEFAULT_LIMITS)}, "
                              f"and may also have {sorted(OPTIONAL_LIMITS)}")
    if "max_orders_per_day" in limits:
        cap = limits["max_orders_per_day"]
        if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not math.isfinite(cap) or cap < 1 or int(cap) != cap:
            raise RiskConfigError("max_orders_per_day must be a whole number of at least 1")
        limits["max_orders_per_day"] = int(cap)
    for key, value in ((k, limits[k]) for k in DEFAULT_LIMITS):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise RiskConfigError(f"{key} must be a positive number")
    if limits["max_position_pct"] > 1.0 or limits["max_daily_loss_pct"] > 100 or limits["max_drawdown_pct"] > 100:
        raise RiskConfigError("max_position_pct is a fraction of equity (at most 1); loss limits are percentages (at most 100)")
    if int(limits["max_open_positions"]) != limits["max_open_positions"]:
        raise RiskConfigError("max_open_positions must be a whole number")
    return limits


def write_default_limits(path: str | Path) -> bool:
    """Create the limits file with the example numbers if it does not exist. Never overwrites."""
    p = Path(path)
    if p.exists():
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(DEFAULT_LIMITS, indent=2) + "\n")
    return True


def check_order(order: dict, account: dict, limits: dict | None = None, limits_file: str | Path | None = None,
                latch_file: str | Path | None = None, halt_file: str | Path | None = HALT_FILE) -> tuple[bool, str]:
    """Decide whether an order may go to the broker. Returns (allowed, reason).

    order    {"value": dollars, "reduces_risk": bool, "adds_to_existing": bool, "position_value_after": dollars}
    account  {"equity", "daily_loss_pct", "drawdown_pct", "open_positions"}
    """
    try:
        if halt_file is not None and Path(halt_file).exists():
            return False, "Operator halt file is present. Trading stopped."
        if limits is None:
            limits = load_limits(limits_file) if limits_file is not None else None
        if limits is None:
            return False, "No risk limits were supplied. Trading stopped."
        value, equity = float(order["value"]), float(account["equity"])
        daily, drawdown, positions = float(account["daily_loss_pct"]), float(account["drawdown_pct"]), int(account["open_positions"])
        if not all(math.isfinite(x) for x in (value, equity, daily, drawdown)) or value < 0 or equity <= 0:
            return False, "Order or account numbers are not usable. Order blocked."
        if order.get("reduces_risk") is True:
            return True, "OK (reduces an open position)"
        if latch_file is not None and Path(latch_file).exists():
            return False, "Max drawdown was hit earlier and the latch has not been reset. Trading stopped."
        if daily >= limits["max_daily_loss_pct"]:
            return False, "Daily loss limit hit. Trading stopped."
        if drawdown >= limits["max_drawdown_pct"]:
            return False, "Max drawdown hit. Trading stopped."
        if max(value, float(order.get("position_value_after", 0.0))) > equity * limits["max_position_pct"]:
            return False, "Order is larger than the position limit."
        if positions >= limits["max_open_positions"] and not order.get("adds_to_existing"):
            return False, "Too many open positions."
        return True, "OK"
    except Exception as exc:  # anything unexpected blocks the order
        return False, f"Risk check could not be completed ({type(exc).__name__}: {exc}). Order blocked."
