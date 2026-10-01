"""Observable decision latency: p50/p95 and hit-rate against a budget. Nothing is faked."""
from __future__ import annotations

import math
from typing import List, Optional


class LatencyStats:
    def __init__(self, budget_ms: float = 500.0):
        self.budget_ms = budget_ms
        self.samples: List[float] = []
        self.hits = 0
        self.warm = False  # True once a warmup/decision succeeded
        self.warmup_ms: Optional[float] = None
        self.last_error: Optional[str] = None

    def record(self, ms: float) -> bool:
        """Record one decision latency; returns whether it was within budget."""
        ok = ms <= self.budget_ms
        self.samples.append(ms)
        self.hits += ok
        return ok

    def percentile(self, p: float) -> Optional[float]:
        if not self.samples:
            return None
        s = sorted(self.samples)
        return s[max(0, math.ceil(p / 100 * len(s)) - 1)]  # nearest-rank

    def snapshot(self, model: str) -> dict:
        n = len(self.samples)
        return {"model": model, "warm": self.warm, "warmup_ms": self.warmup_ms, "n": n,
                "last_ms": self.samples[-1] if n else None, "p50": self.percentile(50),
                "p95": self.percentile(95), "budget_ms": self.budget_ms,
                "hit_rate": (self.hits / n) if n else None, "last_error": self.last_error}


def format_status(s: dict) -> str:
    def ms(v):
        return "-" if v is None else f"{v:.0f}ms"
    hit = "-" if s["hit_rate"] is None else f"{s['hit_rate'] * 100:.0f}%"
    return (f"engine {s['model']} | {'warm' if s['warm'] else 'cold'} | last {ms(s['last_ms'])} | "
            f"p50 {ms(s['p50'])} p95 {ms(s['p95'])} | budget {s['budget_ms']:.0f}ms "
            f"hit {hit} (n={s['n']})")
