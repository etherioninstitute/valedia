"""Executors. Paper trading only: nothing here signs or broadcasts a transaction.

There is intentionally no key handling, wallet, or signer anywhere in this project.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Context, Decimal
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


class DuplicatePoolError(ValueError):
    """A position for this pool is already open (e.g. re-seen after a restart)."""


class Executor(Protocol):
    def buy(self, pool: str, token: str, amount: float, price: Decimal) -> Position: ...


_ROUND = Context(prec=12)  # 12 significant digits: readable, far finer than slot0 accuracy


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PaperExecutor:
    """Records simulated positions in memory and appends them to a JSONL file."""

    def __init__(self, path: Optional[str] = None, clock: Callable[[], str] = _utc_iso):
        self.path = path
        self._clock = clock
        self.positions: List[Position] = self._load(path)  # survive restarts
        self._next_id = max((p.id for p in self.positions), default=0) + 1
        self._lock = threading.Lock()

    @staticmethod
    def _load(path: Optional[str]) -> List[Position]:
        """Read an existing JSONL file; unreadable lines are skipped, a missing file is empty."""
        out: List[Position] = []
        if not path or not os.path.exists(path):
            return out
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(Position(**json.loads(line)))
                except (ValueError, TypeError):
                    continue
        return out

    def has_position(self, pool: str) -> bool:
        return any(p.pool == pool for p in self.positions)

    def buy(self, pool: str, token: str, amount: float, price: Decimal) -> Position:
        if amount <= 0:
            raise ValueError("amount must be positive")
        if price <= 0:
            raise ValueError("price must be positive")
        with self._lock:
            if self.has_position(pool):
                raise DuplicatePoolError(f"position already open for pool {pool}")
            entry = format(_ROUND.create_decimal(price).normalize(), "f")
            pos = Position(id=self._next_id, timestamp=self._clock(), pool=pool, token=token,
                           size=amount, entry_price=entry, simulated=True)
            if self.path:
                parent = os.path.dirname(os.path.abspath(self.path))
                os.makedirs(parent, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(pos.to_json() + "\n")
            self.positions.append(pos)
            self._next_id += 1
        return pos


class LiveSwapExecutor:
    """Interface placeholder for a future real swap. Deliberately NOT implemented."""

    def buy(self, pool: str, token: str, amount: float, price: Decimal) -> Position:
        return self.swap(pool, token, amount, price)

    def swap(self, pool: str, token: str, amount: float, price: Decimal) -> Position:
        raise NotImplementedError(
            "Live swapping is not implemented and this project holds no keys or signer. "
            "Use PaperExecutor. Adding live execution requires a separate, reviewed change.")
