"""Re-check window: liquidity is often added blocks AFTER a pool is created (v3 and v4).

A pool whose only failing FACT gate at detection is liquidity_usd is re-evaluated every poll
until ``creation block + RECHECK_BLOCKS``. Facts are rebuilt each poll; only when they CHANGE
is the model asked again and a decision-log line written. A buy ends the watch, and the
existing duplicate_pool rule still prevents any re-buy. An engine error keeps the pool
watched until the window ends, but is re-asked only after ENGINE_RETRY_BLOCKS more blocks (the
global breaker in backoff.py can pause every call). RECHECK_BLOCKS=0 turns this off.
The watch list is in memory only: a restart forgets it (pools already decided are not
re-decided after a restart unless --redecide).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Optional

from .chain import PoolCreatedEvent
from .gates import FACT_GATES, only_liquidity_fails
from .nimble_questions import sanitize


def fingerprint(state: dict) -> str:
    return json.dumps(sanitize(state), sort_keys=True, default=str)


@dataclass
class Pending:
    ev: PoolCreatedEvent
    until_block: int
    facts: str  # fingerprint of the last evaluated state
    checks: int = 0  # re-evaluations so far (with changed facts)
    retry_at: Optional[int] = None  # engine-error backoff: not re-asked before this head block


class RecheckBook:
    def __init__(self, blocks: int):
        self.blocks = max(0, int(blocks))
        self.pending: Dict[str, Pending] = {}

    def consider(self, ev: PoolCreatedEvent, state: dict, gates: List[dict],
                 final: str, checks: int = 0, engine_error: bool = False,
                 retry_at: Optional[int] = None) -> bool:
        """Start / keep / stop watching ``ev`` after an evaluation. Returns True if watched.

        An engine error (Nimble down, timeout, digest refusal) is not a verdict: if the facts
        pass, or only liquidity fails, the pool stays watched and is re-asked next poll even
        when the facts do not change (empty fingerprint). ``retry_at`` (a head block) spaces
        those re-asks out (ENGINE_RETRY_BLOCKS) so a failing engine is not hit every poll."""
        key = ev.pool.lower()
        facts_ok = not any(not g["passed"] for g in gates if g["gate"] in FACT_GATES)
        keep = only_liquidity_fails(gates) or (engine_error and facts_ok)
        if self.blocks <= 0 or final == "buy" or not keep:
            self.pending.pop(key, None)
            return False
        self.pending[key] = Pending(ev, ev.block_number + self.blocks,
                                    "" if engine_error else fingerprint(state), checks,
                                    retry_at if engine_error else None)
        return True

    def expire(self, head: int) -> List[Pending]:
        """Drop and return the pools whose window has passed."""
        gone = [p for p in self.pending.values() if head > p.until_block]
        for p in gone:
            self.pending.pop(p.ev.pool.lower(), None)
        return gone

    def due(self, head: Optional[int] = None) -> List[Pending]:
        """Watched pools to look at now; with ``head``, those still backing off are left out."""
        return [p for p in self.pending.values()
                if head is None or p.retry_at is None or head >= p.retry_at]
