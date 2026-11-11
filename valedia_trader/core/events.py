"""Core plumbing: a small synchronous event bus with per-topic telemetry, and clocks."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

__all__ = ["Event", "EventBus", "Clock", "SystemClock", "SimulatedClock"]


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class SimulatedClock:
    """Backtest clock: timezone-aware and only ever moves forward."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("clock times must be timezone-aware")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance_to(self, ts: datetime) -> None:
        if ts.tzinfo is None:
            raise ValueError("clock times must be timezone-aware")
        if ts < self._now:
            raise ValueError("a simulated clock cannot move backwards")
        self._now = ts


@dataclass(frozen=True)
class Event:
    topic: str
    payload: Any
    ts: datetime


@dataclass
class EventBus:
    """Publish/subscribe. A failing handler never stops the others; failures are counted and kept."""
    clock: Clock = field(default_factory=SystemClock)
    _handlers: dict[str, list[Callable[[Event], None]]] = field(default_factory=lambda: defaultdict(list))
    published: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    failures: list[tuple[str, str, str]] = field(default_factory=list)

    def subscribe(self, topic: str, handler: Callable[[Event], None]) -> None:
        if not topic:
            raise ValueError("topic must be non-empty")
        self._handlers[topic].append(handler)

    def publish(self, topic: str, payload: Any = None) -> Event:
        event = Event(topic, payload, self.clock.now())
        self.published[topic] += 1
        for handler in list(self._handlers.get(topic, ())) + list(self._handlers.get("*", ())):
            try:
                handler(event)
            except Exception as exc:  # isolate subscribers from each other
                self.failures.append((topic, getattr(handler, "__name__", repr(handler)), f"{type(exc).__name__}: {exc}"))
        return event

    def telemetry(self) -> dict[str, Any]:
        return {"published": dict(self.published), "failures": len(self.failures)}
