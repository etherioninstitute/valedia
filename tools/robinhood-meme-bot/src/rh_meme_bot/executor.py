"""Executors. Paper trading only: nothing here signs or broadcasts a transaction.

There is intentionally no key handling, wallet, or signer anywhere in this project.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timezone
from decimal import Context, Decimal
from typing import Callable, List, Optional, Protocol, Set, Tuple


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


_FIELDS = tuple(f.name for f in fields(Position))
_ADDR = re.compile(r"0x[0-9a-fA-F]{40}")
_ID = re.compile(r'"id"\s*:\s*(\d+)')
_ROUND = Context(prec=12)  # 12 significant digits: readable, far finer than slot0 accuracy


class DuplicatePoolError(ValueError):
    """A position for this pool is already open (e.g. re-seen after a restart)."""


class Executor(Protocol):
    def buy(self, pool: str, token: str, amount: float, price: Decimal) -> Position: ...


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load(path: Optional[str]) -> Tuple[List[Position], Set[str], int]:
    """Read an existing JSONL file -> (positions, known pools, max id seen).

    Fail-closed for dedupe: a line that cannot be parsed into a Position (torn write, schema
    drift, garbage) still contributes every 0x address it contains to the known pools, and any
    id it shows, so a pool we may already hold is never bought again. Unknown extra fields on
    an otherwise valid line are ignored.
    """
    positions: List[Position] = []
    pools: Set[str] = set()
    max_id = 0
    if not path or not os.path.exists(path):
        return positions, pools, max_id
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            pos = None
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    pos = Position(**{"simulated": True, **{k: row[k] for k in _FIELDS if k in row}})
                    pos = replace(pos, pool=str(pos.pool).lower(), token=str(pos.token).lower())
            except (ValueError, TypeError):
                pos = None
            if pos is not None:
                positions.append(pos)
                pools.add(pos.pool)
                if isinstance(pos.id, int) and not isinstance(pos.id, bool):
                    max_id = max(max_id, pos.id)
            else:
                pools.update(a.lower() for a in _ADDR.findall(line))
                max_id = max([max_id] + [int(i) for i in _ID.findall(line)])
    return positions, pools, max_id


def _ends_without_newline(path: str) -> bool:
    try:
        if os.path.getsize(path) == 0:
            return False
        with open(path, "rb") as fh:
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) != b"\n"
    except OSError:
        return False


class PaperExecutor:
    """Records simulated positions in memory and appends them to a JSONL file."""

    def __init__(self, path: Optional[str] = None, clock: Callable[[], str] = _utc_iso):
        self.path = path
        self._clock = clock
        self.positions, self._known_pools, max_id = _load(path)  # survive restarts
        self._next_id = max_id + 1
        self._lock = threading.Lock()

    def has_position(self, pool: str) -> bool:
        return str(pool).lower() in self._known_pools

    def buy(self, pool: str, token: str, amount: float, price: Decimal) -> Position:
        if amount <= 0:
            raise ValueError("amount must be positive")
        if price <= 0:
            raise ValueError("price must be positive")
        pool, token = pool.lower(), token.lower()
        with self._lock:
            if self.has_position(pool):
                raise DuplicatePoolError(f"position already open for pool {pool}")
            entry = format(_ROUND.create_decimal(price).normalize(), "f")
            pos = Position(id=self._next_id, timestamp=self._clock(), pool=pool, token=token,
                           size=amount, entry_price=entry, simulated=True)
            if self.path:
                os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
                # Always start on a fresh line (a torn last line must not swallow this one).
                prefix = "\n" if _ends_without_newline(self.path) else ""
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(prefix + pos.to_json() + "\n")
            self.positions.append(pos)
            self._known_pools.add(pool)
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
