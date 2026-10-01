"""Nimble failure containment: a global circuit breaker (per-pool retry spacing is in recheck.py).

When the engine keeps failing (Nimble down, or hanging until DECISION_TIMEOUT_S on every call),
re-asking every watched pool each poll stalls the loop. After ``threshold`` CONSECUTIVE engine
errors the breaker opens for ``cooldown_s`` seconds: no engine call is made in that window and
each affected pool is logged once as a skip with error ``engine_cooldown``. When the window ends
the next call is a probe: one more error re-opens it at once, a success resets the count.
threshold=0 or cooldown_s=0 disables the breaker. Pure bookkeeping: no I/O, injected clock.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

ENGINE_COOLDOWN = "engine_cooldown"  # decision error recorded for a call that was not made


class EngineBreaker:
    def __init__(self, threshold: int = 3, cooldown_s: float = 30.0,
                 mono: Callable[[], float] = time.monotonic):
        self.threshold, self.cooldown_s, self._mono = max(0, int(threshold)), cooldown_s, mono
        self.consecutive = 0  # engine errors since the last success
        self.open_until: Optional[float] = None
        self.trips = 0

    @property
    def enabled(self) -> bool:
        return self.threshold > 0 and self.cooldown_s > 0

    def is_open(self) -> bool:
        return self.enabled and self.open_until is not None and self._mono() < self.open_until

    def remaining_s(self) -> float:
        return max(0.0, self.open_until - self._mono()) if self.is_open() else 0.0

    def record(self, error: bool) -> bool:
        """Record one real engine call. Returns True when this error (re)opens the breaker."""
        if not error:
            self.consecutive, self.open_until = 0, None
            return False
        self.consecutive += 1
        if self.enabled and self.consecutive >= self.threshold:
            self.open_until, self.trips = self._mono() + self.cooldown_s, self.trips + 1
            return True
        return False
