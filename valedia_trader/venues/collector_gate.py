"""A gate that lets a study pull send requests only while the live order-book collector is idle.

The collector writes a sqlite file (WAL mode) with `decisions(slot_ts, decision_ts, status, ...)` and
`heartbeats(ts, ...)`. A pull that shares its Kalshi host is harmless while the collector makes at most a decision a
minute and finishes within seconds of the minute start; once it is busy (several decisions a minute, ending late in
the minute) the pull makes its cycle slip. `CollectorGate` reads the file READ-ONLY and answers whether now is such an
idle time; `GatedPacer` holds a pacer's `wait()` until the gate is open. Everything fails closed: a missing file, a
silent collector, an unreadable table or any sqlite error means closed. Nothing here writes or creates a file."""
from __future__ import annotations

import math
import os
import sqlite3
import time
from collections.abc import Callable
from typing import Any

__all__ = ["CollectorGate", "GatedPacer", "connect_ro"]


def connect_ro(path: str | os.PathLike) -> sqlite3.Connection:
    """A read-only connection (never creates the file; a missing one raises)."""
    return sqlite3.connect(f"file:{os.fspath(path)}?mode=ro", uri=True, timeout=1)


class CollectorGate:
    """`is_open(now)` -> (open, reason). Open only while the collector is idle:

    - a heartbeat no older than `stale_s`;
    - over (non-forced, when a `forced` column exists) decisions with slot_ts within `lookback_s`: at most
      `max_per_minute` per slot minute, each finished within `max_decision_second` of its slot minute's start,
      and every one with status 'ok'."""

    def __init__(self, db_path: str | os.PathLike, *, lookback_s: float = 600, max_per_minute: int = 1,
                 max_decision_second: float = 20.0, stale_s: float = 180,
                 connect: Callable[[str], sqlite3.Connection] | None = None,
                 clock: Callable[[], float] = time.time):
        self.db_path = os.fspath(db_path)
        self.lookback_s, self.max_per_minute = lookback_s, max_per_minute
        self.max_decision_second, self.stale_s = max_decision_second, stale_s
        self.connect = connect or connect_ro
        self.clock = clock

    def is_open(self, now: float | None = None) -> tuple[bool, str]:
        now = self.clock() if now is None else now
        if not os.path.exists(self.db_path):
            return False, "no collector db"
        conn = None
        try:
            conn = self.connect(self.db_path)
            return self._check(conn, now)
        except Exception as e:                    # fail closed on anything (sqlite errors, a bad connect seam)
            return False, f"collector db error: {type(e).__name__}"
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _check(self, conn: sqlite3.Connection, now: float) -> tuple[bool, str]:
        row = conn.execute("SELECT MAX(ts) FROM heartbeats").fetchone()
        last = row[0] if row else None
        if last is None or last < now - self.stale_s:
            return False, "collector silent"
        cols = {r[1] for r in conn.execute("PRAGMA table_info(decisions)")}
        where = "slot_ts >= ? AND slot_ts <= ?" + (" AND COALESCE(forced, 0) = 0" if "forced" in cols else "")
        rows = conn.execute(f"SELECT slot_ts, decision_ts, status FROM decisions WHERE {where}",
                            (now - self.lookback_s, now)).fetchall()
        per_minute: dict[int, int] = {}
        for slot_ts, decision_ts, status in rows:
            if status != "ok":
                return False, f"collector has a {status!r} decision"
            minute = math.floor(slot_ts / 60)
            per_minute[minute] = per_minute.get(minute, 0) + 1
            if per_minute[minute] > self.max_per_minute:
                return False, "collector busy: %d decisions in one minute" % per_minute[minute]
            if decision_ts is None:
                return False, "collector has a decision still open"
            late = decision_ts - minute * 60
            if late > self.max_decision_second:
                return False, "collector busy: a decision ended at second %.0f" % late
        return True, "collector idle"


class GatedPacer:
    """A pacer whose `wait()` returns only while `gate` is open. The gate is asked at most once per wall-clock
    minute; while closed it sleeps to the start of the next minute (+0.5 s) and tries again."""

    def __init__(self, inner: Any, gate: Any, *, sleep: Callable[[float], Any] = time.sleep,
                 clock: Callable[[], float] = time.time, log: Callable[[str], Any] | None = None):
        self.inner, self.gate, self.sleep, self.clock, self.log = inner, gate, sleep, clock, log
        self._cache: tuple[int, bool, str] | None = None

    def wait(self) -> None:
        while True:
            self.inner.wait()
            now = self.clock()
            minute = math.floor(now / 60)
            if self._cache is not None and self._cache[0] == minute:
                _, ok, _reason = self._cache
            else:
                ok, reason = self.gate.is_open(now)
                self._cache = (minute, ok, reason)
                if not ok and self.log is not None:
                    self.log(reason)
            if ok:
                return
            self.sleep(max((minute + 1) * 60 + 0.5 - now, 0.5))
