"""Re-check window: liquidity is often added blocks AFTER a pool is created (v3 and v4).

A pool whose only failing FACT gate at detection is liquidity_usd is re-evaluated every poll
until ``creation block + RECHECK_BLOCKS``. Facts are rebuilt each poll; only when they CHANGE
is the model asked again and a decision-log line written. A buy ends the watch, and the
existing duplicate_pool rule still prevents any re-buy. RECHECK_BLOCKS=0 turns this off.
The watch list is in memory only: a restart forgets it (pools already decided are not
re-decided after a restart unless --redecide).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List

from .chain import PoolCreatedEvent
from .gates import only_liquidity_fails
from .nimble_questions import sanitize


def fingerprint(state: dict) -> str:
    return json.dumps(sanitize(state), sort_keys=True, default=str)


@dataclass
class Pending:
    ev: PoolCreatedEvent
    until_block: int
    facts: str  # fingerprint of the last evaluated state
    checks: int = 0  # re-evaluations so far (with changed facts)


class RecheckBook:
    def __init__(self, blocks: int):
        self.blocks = max(0, int(blocks))
        self.pending: Dict[str, Pending] = {}

    def consider(self, ev: PoolCreatedEvent, state: dict, gates: List[dict],
                 final: str, checks: int = 0) -> bool:
        """Start / keep / stop watching ``ev`` after an evaluation. Returns True if watched."""
        key = ev.pool.lower()
        if self.blocks <= 0 or final == "buy" or not only_liquidity_fails(gates):
            self.pending.pop(key, None)
            return False
        self.pending[key] = Pending(ev, ev.block_number + self.blocks, fingerprint(state),
                                    checks)
        return True

    def expire(self, head: int) -> List[Pending]:
        """Drop and return the pools whose window has passed."""
        gone = [p for p in self.pending.values() if head > p.until_block]
        for p in gone:
            self.pending.pop(p.ev.pool.lower(), None)
        return gone

    def due(self) -> List[Pending]:
        return list(self.pending.values())
