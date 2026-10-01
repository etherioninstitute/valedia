"""Executors. Paper trading only: nothing here signs or broadcasts a transaction.

There is intentionally no key handling, wallet, or signer anywhere in this project.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, List, Optional, Protocol


@dataclass(frozen=True)
class Position:
    id: int
    timestamp: str  # UTC ISO-8601
    pool: str
    token: str
    size: float  # quote-token units committed
    entry_price: str  # quote per token, Decimal as string (no float rounding)
    simulated: bool = True

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


class Executor(Protocol):
    def buy(self, pool: str, token: str, amount: float, price: Decimal) -> Position: ...


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PaperExecutor:
    """Records simulated positions in memory and appends them to a JSONL file."""

    def __init__(self, path: Optional[str] = None, clock: Callable[[], str] = _utc_iso):
        self.path = path
        self._clock = clock
        self.positions: List[Position] = []
        self._lock = threading.Lock()

    def buy(self, pool: str, token: str, amount: float, price: Decimal) -> Position:
        if amount <= 0:
            raise ValueError("amount must be positive")
        if price <= 0:
            raise ValueError("price must be positive")
        with self._lock:
            pos = Position(id=len(self.positions) + 1, timestamp=self._clock(), pool=pool,
                           token=token, size=amount, entry_price=format(price, "f"),
                           simulated=True)
            if self.path:
                parent = os.path.dirname(os.path.abspath(self.path))
                os.makedirs(parent, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(pos.to_json() + "\n")
            self.positions.append(pos)
        return pos


class LiveSwapExecutor:
    """Interface placeholder for a future real swap. Deliberately NOT implemented."""

    def buy(self, pool: str, token: str, amount: float, price: Decimal) -> Position:
        return self.swap(pool, token, amount, price)

    def swap(self, pool: str, token: str, amount: float, price: Decimal) -> Position:
        raise NotImplementedError(
            "Live swapping is not implemented and this project holds no keys or signer. "
            "Use PaperExecutor. Adding live execution requires a separate, reviewed change.")
