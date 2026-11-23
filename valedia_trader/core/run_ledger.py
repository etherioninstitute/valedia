"""The run ledger: what each day's runs of the order step did, who is told about it, and the clean-week streak.

    python -m valedia_trader.core.run_ledger record     --store PATH --book BOOK --kind K --trigger T --outcome O [...]
    python -m valedia_trader.core.run_ledger record-run --store PATH --book BOOK --trigger T --exit-code N   (summary on stdin)
    python -m valedia_trader.core.run_ledger preflight  --store PATH --book BOOK --repo R --accepted-commit-file F \\
                                                        --files-file L --venv-python P [--trigger T]
    python -m valedia_trader.core.run_ledger deadman    --store PATH --book BOOK
    python -m valedia_trader.core.run_ledger notify     --store PATH --book BOOK [--mac]
    python -m valedia_trader.core.run_ledger streak     --store PATH --book BOOK --since YYYY-MM-DD

The store is a sqlite file whose path is always given; this module has no default path. Three tables:

  run_events      one row for each thing that happened (a pre-flight, a run start, a run end, a skip, the dead-man).
                  Append-only: the database itself refuses UPDATE, DELETE and a REPLACE or UPSERT on an existing id.
  alert_incidents one row for each (day, book, cause) that was told to the owner. Only its sent count, its last
                  sent time and its follow-up flag can change, and only upwards.
  send_attempts   one row for each try to tell the owner, whether it worked or not. Append-only. A failed alert is
                  tried at most NOTIFY_MAX_TRIES (5) times in all, the rule core.alerts has.

What reaches the owner (`decide`, a pure function of the day's rows):
  P0  a run that ended in a fault or a crash, or any row with a fault count; a run that started and never ended (an
      orphaned start, written by the dead-man check). Sent every time, never held back.
  P1  the dead-man: no run ended and none was skipped on purpose by 10:25 New York. Once a day.
  P2  a skip, or a pre-flight warning. Once for each cause in a day. A 6:55 skip with the cause of a warning already
      sent adds one quiet follow-up; a 7:10 skip of that cause is not sent.
  P3  an ordinary stop (a holiday, a closed market) or a no-op (another step held the lock; a run outside the session
      window). Written down, never sent.
A NOOP run end is not a terminal row: it does not satisfy the dead-man and the streak ignores it. A run that did nothing
because another run held the lock must not hide the run that hung holding it.
Any cause that is new, and any P0, is sent. Sending is a sender object with `send(title, text)`; `MacSender` wraps
core.alerts.MacNotifier for it. A send is made before it is written down, so a crash between the two sends it again
rather than never.

Nothing here makes a network call, reads a secrets file or runs a shell: the pre-flight runs `git` with an argument list.
Times are UTC in the store (`YYYY-MM-DDTHH:MM:SSZ`); a day is the New York date; a trading day is an XNYS session."""
from __future__ import annotations

import argparse
import functools
import json
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

from valedia_trader.core.alerts import NOTIFY_MAX_TRIES, Alert, MacNotifier, clean
from valedia_trader.core.secrets import child_env

NEW_YORK = ZoneInfo("America/New_York")
KINDS = ("PREFLIGHT", "RUN_START", "RUN_END", "SKIP", "DEADMAN")
TRIGGERS = ("0630", "0655", "0710", "0725", "MANUAL")                 # the Pacific time the job is set for
OUTCOMES = ("OK", "WARN", "COMPLETED", "ORDINARY_STOP", "FAULT", "CRASH", "SKIPPED", "MISSING", "NOOP")
SEVERITIES = ("P0", "P1", "P2", "P3")
TERMINAL_SKIP_TRIGGERS = ("0655", "0710")                             # a skip at these times ends the day's run
DEADMAN_CUTOFF = time(10, 25)                                         # New York: 7:25 AM Pacific
CLEAN_SESSIONS_NEEDED = 5
HOLIDAY = "calendar:holiday"
DEADMAN_CAUSE = "deadman:no-terminal-row"
ORPHAN_MINUTES = 20                                                   # a RUN_START with no RUN_END after this long is an orphan
ORPHAN_CAUSE = "run:orphan-start:"                                    # + the trigger, lower case
NOOP_LOCK_HELD = "run:noop:lock-held"
NOOP_OUTSIDE_WINDOW = "run:noop:outside-window"
# How the order step words a stop at its `session` stage. tests/core/test_run_ledger.py reads the order step's source for
# these exact words, so a change to its wording fails a test here rather than quietly changing what a stop maps to.
LOCK_HELD_REASON = "another order step is running"
OUTSIDE_WINDOW_REASON = "outside the regular session window"
CALENDAR_FAULT_REASON = "the exchange calendar could not be loaded"
CALENDAR_ANSWER_REASON = "the exchange calendar could not answer for"   # market_window: a calendar error, not a closed market
MIN_FILES = 10                                                        # a files list shorter than this is not the order path
MAX_SUMMARY_BYTES = 16 * 1024 * 1024
GIT_SECONDS = 60
SCHEMA_VERSION = 2                                                    # 2: the NOOP outcome (a layout-1 store would refuse it)

_STAMP = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z"
_DAY = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"
_CAUSE_RE = re.compile(r"[a-z0-9_:.-]{1,120}")
_STAGE_RE = re.compile(r"[a-z0-9_-]{1,40}")
_SHA_RE = re.compile(r"[0-9a-fA-F]{7,64}")


def _sql_in(values: Iterable[str]) -> str:
    return ", ".join("'" + v + "'" for v in values)


def _cause_check(col: str, nullable: bool) -> str:
    body = f"typeof({col}) = 'text' AND length({col}) BETWEEN 1 AND 120 AND {col} NOT GLOB '*[^a-z0-9_:.-]*'"
    return f"{col} IS NULL OR ({body})" if nullable else body


def _stamp_check(col: str, nullable: bool = False) -> str:
    """A time written as YYYY-MM-DDTHH:MM:SSZ that is a real time: SQLite reads an hour of 24 as the next day, so it is refused."""
    body = (f"typeof({col}) = 'text' AND {col} GLOB '{_STAMP}' AND substr({col}, 12, 2) < '24' "
            f"AND strftime('%Y-%m-%dT%H:%M:%SZ', {col}) IS {col}")
    return f"{col} IS NULL OR ({body})" if nullable else body


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS run_events (
    event_id    INTEGER PRIMARY KEY,
    trade_date  TEXT NOT NULL CHECK (trade_date GLOB '{_DAY}' AND date(trade_date) IS trade_date),
    book        TEXT NOT NULL CHECK (length(book) > 0),
    kind        TEXT NOT NULL CHECK (kind IN ({_sql_in(KINDS)})),
    "trigger"   TEXT NOT NULL CHECK ("trigger" IN ({_sql_in(TRIGGERS)})),
    outcome     TEXT NOT NULL CHECK (outcome IN ({_sql_in(OUTCOMES)})),
    root_cause  TEXT CHECK ({_cause_check("root_cause", True)}),
    fault_count INTEGER NOT NULL DEFAULT 0 CHECK (typeof(fault_count) = 'integer' AND fault_count >= 0),
    revision_ok INTEGER CHECK (revision_ok IS NULL OR (typeof(revision_ok) = 'integer' AND revision_ok IN (0, 1))),
    detail      TEXT,
    at_utc      TEXT NOT NULL CHECK ({_stamp_check("at_utc")})
);
CREATE INDEX IF NOT EXISTS run_events_day ON run_events (trade_date, book);
CREATE TRIGGER IF NOT EXISTS run_events_no_update BEFORE UPDATE ON run_events
BEGIN SELECT RAISE(ABORT, 'run_events is append-only: no update'); END;
CREATE TRIGGER IF NOT EXISTS run_events_no_delete BEFORE DELETE ON run_events
BEGIN SELECT RAISE(ABORT, 'run_events is append-only: no delete'); END;
CREATE TRIGGER IF NOT EXISTS run_events_no_replace BEFORE INSERT ON run_events
WHEN NEW.event_id IS NOT NULL AND EXISTS (SELECT 1 FROM run_events WHERE event_id = NEW.event_id)
BEGIN SELECT RAISE(ABORT, 'run_events is append-only: that id is taken'); END;

CREATE TABLE IF NOT EXISTS alert_incidents (
    incident_id    INTEGER PRIMARY KEY,
    trade_date     TEXT NOT NULL CHECK (trade_date GLOB '{_DAY}' AND date(trade_date) IS trade_date),
    book           TEXT NOT NULL CHECK (length(book) > 0),
    root_cause     TEXT NOT NULL CHECK ({_cause_check("root_cause", False)}),
    severity       TEXT NOT NULL CHECK (severity IN ({_sql_in(SEVERITIES)})),
    first_event_id INTEGER NOT NULL REFERENCES run_events (event_id),
    first_seen_utc TEXT NOT NULL CHECK ({_stamp_check("first_seen_utc")}),
    sent_count     INTEGER NOT NULL DEFAULT 0 CHECK (typeof(sent_count) = 'integer' AND sent_count >= 0),
    last_sent_utc  TEXT CHECK ({_stamp_check("last_sent_utc", True)}),
    followup_sent  INTEGER NOT NULL DEFAULT 0 CHECK (followup_sent IN (0, 1)),
    UNIQUE (trade_date, book, root_cause)
);
CREATE TRIGGER IF NOT EXISTS alert_incidents_fixed BEFORE UPDATE ON alert_incidents
WHEN NEW.incident_id IS NOT OLD.incident_id OR NEW.trade_date IS NOT OLD.trade_date OR NEW.book IS NOT OLD.book
  OR NEW.root_cause IS NOT OLD.root_cause OR NEW.severity IS NOT OLD.severity OR NEW.first_event_id IS NOT OLD.first_event_id
  OR NEW.first_seen_utc IS NOT OLD.first_seen_utc
  OR NEW.sent_count < OLD.sent_count OR NEW.followup_sent < OLD.followup_sent
  OR (OLD.last_sent_utc IS NOT NULL AND (NEW.last_sent_utc IS NULL OR NEW.last_sent_utc < OLD.last_sent_utc))
BEGIN SELECT RAISE(ABORT, 'alert_incidents: only the sent count, the last sent time and the follow-up flag may rise'); END;
CREATE TRIGGER IF NOT EXISTS alert_incidents_no_replace BEFORE INSERT ON alert_incidents
WHEN (NEW.incident_id IS NOT NULL AND EXISTS (SELECT 1 FROM alert_incidents WHERE incident_id = NEW.incident_id))
  OR EXISTS (SELECT 1 FROM alert_incidents WHERE trade_date IS NEW.trade_date AND book IS NEW.book AND root_cause IS NEW.root_cause)
BEGIN SELECT RAISE(ABORT, 'alert_incidents: that incident exists; it can only be raised, never inserted again'); END;
CREATE TRIGGER IF NOT EXISTS alert_incidents_no_delete BEFORE DELETE ON alert_incidents
BEGIN SELECT RAISE(ABORT, 'alert_incidents: no delete'); END;

CREATE TABLE IF NOT EXISTS send_attempts (
    attempt_id INTEGER PRIMARY KEY,
    trade_date TEXT NOT NULL CHECK (trade_date GLOB '{_DAY}' AND date(trade_date) IS trade_date),
    book       TEXT NOT NULL CHECK (length(book) > 0),
    root_cause TEXT NOT NULL CHECK ({_cause_check("root_cause", False)}),
    event_id   INTEGER NOT NULL REFERENCES run_events (event_id),
    ok         INTEGER NOT NULL CHECK (ok IN (0, 1)),
    error      TEXT,
    at_utc     TEXT NOT NULL CHECK ({_stamp_check("at_utc")})
);
CREATE INDEX IF NOT EXISTS send_attempts_event ON send_attempts (event_id, root_cause);
CREATE TRIGGER IF NOT EXISTS send_attempts_no_replace BEFORE INSERT ON send_attempts
WHEN NEW.attempt_id IS NOT NULL AND EXISTS (SELECT 1 FROM send_attempts WHERE attempt_id = NEW.attempt_id)
BEGIN SELECT RAISE(ABORT, 'send_attempts is append-only: that id is taken'); END;
CREATE TRIGGER IF NOT EXISTS send_attempts_no_update BEFORE UPDATE ON send_attempts
BEGIN SELECT RAISE(ABORT, 'send_attempts is append-only: no update'); END;
CREATE TRIGGER IF NOT EXISTS send_attempts_no_delete BEFORE DELETE ON send_attempts
BEGIN SELECT RAISE(ABORT, 'send_attempts is append-only: no delete'); END;
"""


# ------------------------------------------------------------------------------------------------ the store

def connect(path) -> sqlite3.Connection:
    """The store at `path` (always given), made if it is not there; writes are synchronous=FULL and foreign keys are on.
    A folder that does not exist is an error: it is never made."""
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=15.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA foreign_keys = ON")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            raise sqlite3.DatabaseError(f"the store has layout {version}; this code reads layout {SCHEMA_VERSION}")
        conn.executescript(SCHEMA)
        if version == 0:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def _tx(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _utc(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("the time must be a datetime with a time zone")
    return now.astimezone(timezone.utc)


def stamp(now: datetime) -> str:
    return _utc(now).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_york_date(now: datetime) -> str:
    return _utc(now).astimezone(NEW_YORK).date().isoformat()


def _insert_event(conn, *, book, kind, trigger, outcome, now, root_cause=None, fault_count=0, revision_ok=None, detail=None,
                  trade_date=None) -> int:
    if root_cause is not None and not _CAUSE_RE.fullmatch(root_cause):
        raise ValueError("a root cause is 1 to 120 characters of a-z, 0-9, _ : . -")
    row = conn.execute(
        'INSERT INTO run_events (trade_date, book, kind, "trigger", outcome, root_cause, fault_count, revision_ok, detail, at_utc) '
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (trade_date or new_york_date(now), book, kind, trigger, outcome, root_cause, fault_count, revision_ok,
         None if detail is None else clean(detail, 300), stamp(now)))
    return row.lastrowid


def record_event(conn, *, book: str, kind: str, trigger: str, outcome: str, now: datetime, root_cause: str | None = None,
                 fault_count: int = 0, revision_ok: int | None = None, detail: str | None = None,
                 trade_date: str | None = None) -> int:
    """Write one event and return its id. The detail goes through core.alerts.clean first. `trade_date` is the New York
    date of `now` unless given. A value the table refuses raises sqlite3.IntegrityError; a malformed cause, ValueError."""
    return _insert_event(conn, book=book, kind=kind, trigger=trigger, outcome=outcome, now=now, root_cause=root_cause,
                         fault_count=fault_count, revision_ok=revision_ok, detail=detail, trade_date=trade_date)


def events_for(conn, trade_date: str, book: str) -> list[dict]:
    """The day's events of a book, oldest first."""
    rows = conn.execute("SELECT * FROM run_events WHERE trade_date = ? AND book = ? ORDER BY event_id", (trade_date, book))
    return [dict(r) for r in rows]


def events_between(conn, book: str, first: str, last: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM run_events WHERE book = ? AND trade_date BETWEEN ? AND ? ORDER BY event_id",
                        (book, first, last))
    return [dict(r) for r in rows]


def incidents_for(conn, trade_date: str, book: str) -> list[dict]:
    """The day's incidents, each with `sent_event_ids`: the events whose alert was shown (from send_attempts)."""
    out = []
    for r in conn.execute("SELECT * FROM alert_incidents WHERE trade_date = ? AND book = ? ORDER BY incident_id", (trade_date, book)):
        row = dict(r)
        row["sent_event_ids"] = [x[0] for x in conn.execute(
            "SELECT event_id FROM send_attempts WHERE trade_date = ? AND book = ? AND root_cause = ? AND ok = 1 ORDER BY attempt_id",
            (trade_date, book, row["root_cause"]))]
        out.append(row)
    return out


# ------------------------------------------------------------------------------------------------ the calendar

@functools.lru_cache(maxsize=1)
def _calendar():
    import exchange_calendars
    return exchange_calendars.get_calendar("XNYS")


def _as_date(value) -> date:
    return value if isinstance(value, date) and not isinstance(value, datetime) else date.fromisoformat(str(value))


def is_session(day) -> bool:
    """Whether the day (a date or `YYYY-MM-DD`) is an XNYS trading session."""
    return bool(_calendar().is_session(_as_date(day).isoformat()))


def sessions_between(first, last) -> list[date]:
    """The XNYS sessions from `first` to `last`, both included, as dates in order."""
    a, b = _as_date(first), _as_date(last)
    if a > b:
        return []
    return [s.date() for s in _calendar().sessions_in_range(a.isoformat(), b.isoformat())]


# ------------------------------------------------------------------------------------------------ the policy

@dataclass(frozen=True)
class Outbound:
    """One thing to tell the owner. `event_id` is the event that raised it (its id, with the cause, is the alert's identity)."""
    severity: str
    root_cause: str
    title: str
    text: str
    event_id: int
    trade_date: str
    book: str
    event_at_utc: str
    followup: bool = False


def classify(event: dict) -> str | None:
    """The severity one event carries on its own: P0, P1, P2, P3, or None when it needs no word at all."""
    kind, outcome, cause = event["kind"], event["outcome"], event.get("root_cause")
    if ((event.get("fault_count") or 0) > 0 or (kind == "RUN_END" and outcome in ("FAULT", "CRASH"))
            or (kind == "DEADMAN" and outcome == "FAULT")):                    # the last: an orphaned run start
        return "P0"
    if kind == "DEADMAN":
        return "P1" if outcome == "MISSING" else None
    if cause == HOLIDAY or outcome in ("ORDINARY_STOP", "NOOP"):
        return "P3"
    if kind == "SKIP" or (kind == "PREFLIGHT" and outcome == "WARN"):
        return "P2"
    return None


_RUN_NAMES = {"0655": "6:55", "0710": "7:10", "0630": "6:30", "0725": "7:25", "MANUAL": "manual"}


def _outbound(severity, event, cause, title, text, followup=False) -> Outbound:
    return Outbound(severity, cause, title, text, int(event["event_id"]), event["trade_date"], event["book"], event["at_utc"], followup)


def decide(events_today: Sequence[dict], incidents_today: Sequence[dict]) -> list[Outbound]:
    """What is owed to the owner now, from one day's events of one book and the day's incidents. No input or output.

    An incident row means "this cause was told today"; its `sent_event_ids` (required: a row without it is a
    ValueError, as every P0 would look unsent) are the events whose alert was shown. Events are read in order, as if each had just arrived, so a warning and the skip it warned of, both
    still unsent, give the warning and then the follow-up. The result is ordered P0, P1, P2 and then by event."""
    events = sorted(events_today, key=lambda e: e["event_id"])
    told = {i["root_cause"]: dict(i) for i in incidents_today}                  # cause -> incident, grown as we go
    if any("sent_event_ids" not in i for i in incidents_today):
        raise ValueError("every incident row needs `sent_event_ids` (read the rows with incidents_for); without it every P0 "
                         "would look unsent and be sent again")
    shown_ids = {n for i in incidents_today for n in i["sent_event_ids"]}
    first_kind = {i["root_cause"]: _first_kind(i, events) for i in incidents_today}
    out: list[Outbound] = []
    for e in events:
        sev = classify(e)
        if sev in (None, "P3"):
            continue                                                            # P3 is logged only
        cause = e.get("root_cause")
        if sev == "P0":
            if e["event_id"] in shown_ids:
                continue                                                        # this very fault was told already
            cause = cause or f"run:{str(e['outcome']).lower()}"
            if e["kind"] == "DEADMAN":
                out.append(_outbound("P0", e, cause, "P0 order step: a run started and never ended",
                                     f"{cause} on {e['trade_date']}: a run started and no run end followed it within "
                                     f"{ORPHAN_MINUTES} minutes; it may still hold the order step's lock"))
                continue
            out.append(_outbound("P0", e, cause, f"P0 order step {e['outcome']}",
                                 f"{e['outcome']} at {_RUN_NAMES.get(e['trigger'], e['trigger'])} on {e['trade_date']}: {cause}"
                                 + (f" ({e['fault_count']} fault(s))" if e.get("fault_count") else "")))
        elif sev == "P1":
            cause = cause or DEADMAN_CAUSE
            if cause in told:
                continue                                                        # once a day
            told[cause], first_kind[cause] = {"root_cause": cause, "severity": "P1", "followup_sent": 0}, e["kind"]
            out.append(_outbound("P1", e, cause, "P1 order step: no run recorded",
                                 f"no terminal row for {e['trade_date']}: nothing ended or was skipped by 10:25 New York"))
        else:
            cause = cause or ("preflight:warn" if e["kind"] == "PREFLIGHT" else "run:skipped")
            have = told.get(cause)
            if have is None:                                                     # a new cause is always sent
                told[cause], first_kind[cause] = {"root_cause": cause, "severity": "P2", "followup_sent": 0}, e["kind"]
                if e["kind"] == "PREFLIGHT":
                    out.append(_outbound("P2", e, cause, "P2 order step will skip", f"the 6:55 run will skip: {cause}"))
                else:
                    out.append(_outbound("P2", e, cause, "P2 order step skipped",
                                         f"the {_RUN_NAMES.get(e['trigger'], e['trigger'])} run skipped: {cause}"))
            elif (e["kind"] == "SKIP" and e["trigger"] == "0655" and not have.get("followup_sent")
                  and have.get("severity") == "P2" and first_kind.get(cause) == "PREFLIGHT"):
                have["followup_sent"] = 1                                        # the one quiet follow-up
                out.append(_outbound("P2", e, cause, "P2 order step skipped as warned",
                                     f"skipped as warned; clean-week streak reset to 0 ({cause})", followup=True))
    out.sort(key=lambda o: (SEVERITIES.index(o.severity), o.event_id))
    return out


def _first_kind(incident: dict, events: Sequence[dict]) -> str | None:
    for e in events:
        if e["event_id"] == incident.get("first_event_id"):
            return e["kind"]
    return None


def _attempts(conn, o: Outbound, ok: int) -> int:
    return conn.execute("SELECT count(*) FROM send_attempts WHERE trade_date = ? AND book = ? AND root_cause = ? AND event_id = ? AND ok = ?",
                        (o.trade_date, o.book, o.root_cause, o.event_id, ok)).fetchone()[0]


def apply(conn, outbound: Outbound, sender, now: datetime) -> str:
    """Send one alert through `sender.send(title, text)` and write down what happened: "sent", "failed" or "gave_up"
    (it has already failed NOTIFY_MAX_TRIES times, so it is not tried again). A try is written after the sender answered."""
    if _attempts(conn, outbound, 0) >= NOTIFY_MAX_TRIES:
        return "gave_up"
    at = stamp(now)
    try:
        sender.send(outbound.title, outbound.text)
    except Exception as exc:
        conn.execute("INSERT INTO send_attempts (trade_date, book, root_cause, event_id, ok, error, at_utc) VALUES (?, ?, ?, ?, 0, ?, ?)",
                     (outbound.trade_date, outbound.book, outbound.root_cause, outbound.event_id,
                      clean(f"{type(exc).__name__}: {exc}", 200), at))
        return "failed"
    with _tx(conn):
        conn.execute("INSERT INTO send_attempts (trade_date, book, root_cause, event_id, ok, error, at_utc) VALUES (?, ?, ?, ?, 1, NULL, ?)",
                     (outbound.trade_date, outbound.book, outbound.root_cause, outbound.event_id, at))
        known = conn.execute("SELECT incident_id FROM alert_incidents WHERE trade_date = ? AND book = ? AND root_cause = ?",
                             (outbound.trade_date, outbound.book, outbound.root_cause)).fetchone()
        if known is None:                                                        # a plain INSERT, never a replace or an upsert
            conn.execute(
                "INSERT INTO alert_incidents (trade_date, book, root_cause, severity, first_event_id, first_seen_utc, sent_count, "
                "last_sent_utc, followup_sent) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (outbound.trade_date, outbound.book, outbound.root_cause, outbound.severity, outbound.event_id,
                 outbound.event_at_utc, at, int(outbound.followup)))
        else:                                                                    # only the three fields that may rise
            conn.execute("UPDATE alert_incidents SET sent_count = sent_count + 1, "
                         "last_sent_utc = max(coalesce(last_sent_utc, ''), ?), followup_sent = max(followup_sent, ?) "
                         "WHERE incident_id = ?", (at, int(outbound.followup), known[0]))
    return "sent"


def notify(conn, book: str, sender, now: datetime) -> dict:
    """Decide what today's rows owe the owner and send it through `sender` (None: decide only, send and write nothing).
    Returns {"dry_run", "due": [...], "sent", "failed", "gave_up"}."""
    day = new_york_date(now)
    due = decide(events_for(conn, day, book), incidents_for(conn, day, book))
    result = {"dry_run": sender is None, "due": [asdict(o) for o in due], "sent": 0, "failed": 0, "gave_up": 0}
    if sender is not None:
        for o in due:
            result[{"sent": "sent", "failed": "failed", "gave_up": "gave_up"}[apply(conn, o, sender, now)]] += 1
    return result


class MacSender:
    """The thin adapter from `send(title, text)` to core.alerts.MacNotifier, which shows one macOS notification."""

    def __init__(self, notifier=None) -> None:
        self._notifier = notifier if notifier is not None else MacNotifier()

    def send(self, title: str, text: str) -> None:
        self._notifier.send(Alert("order-step-ledger", "warning", title, text))


# ------------------------------------------------------------------------------------------------ the streak

def target_met(count: int) -> bool:
    return count >= CLEAN_SESSIONS_NEEDED


def _judge(day_events: Sequence[dict]) -> str | None:
    """None when the day is clean, else the reason it resets the streak. A NOOP row with no faults is ignored: the day is
    judged by the rest. A NOOP that carries faults is a P0 when sent, so it resets the streak like any other fault."""
    day_events = [e for e in day_events if e["outcome"] != "NOOP" or (e.get("fault_count") or 0) > 0]
    if not day_events:
        return "no terminal row"
    if any(e["kind"] == "SKIP" for e in day_events):
        return "skip"
    if any(e["outcome"] == "CRASH" for e in day_events):
        return "crash"
    if any(e["outcome"] == "FAULT" or (e.get("fault_count") or 0) > 0 for e in day_events):
        return "fault"
    if any(e["kind"] in ("RUN_START", "RUN_END") and e.get("revision_ok") == 0 for e in day_events):
        return "revision mismatch"
    if any(e["kind"] == "DEADMAN" and e["outcome"] == "MISSING" for e in day_events):
        return "dead-man fired"
    ends = sorted((e for e in day_events if e["kind"] == "RUN_END"), key=lambda e: e["event_id"])
    if not ends:
        return "no terminal row"
    last = ends[-1]
    if last["outcome"] not in ("COMPLETED", "ORDINARY_STOP"):
        return f"terminal outcome {last['outcome']}"
    if last.get("revision_ok") != 1:
        return "revision unknown"
    return None


def streak(events: Iterable[dict], sessions: Iterable) -> tuple[int, list[dict]]:
    """The clean-week streak over the given XNYS session days (dates or `YYYY-MM-DD`), in date order.

    A session day adds 1 when its runs used the accepted revision, had no fault, and its last RUN_END is COMPLETED or
    ORDINARY_STOP (a NOOP run end is ignored, as if it were not there); it resets the count to 0 on a skip, a crash, a fault, a revision that did not match or is not known,
    or no terminal row. A weekday between the first and last session that is not a session (an exchange holiday) pauses:
    the count is kept. Weekends are not shown. Returns (count, per_day) with per_day = [{date, status, count, reason}]
    and status "clean", "reset" or "paused". Rows of days that are not sessions are ignored."""
    days = sorted({_as_date(s) for s in sessions})
    if not days:
        return 0, []
    by_day: dict[str, list[dict]] = {}
    for e in events:
        by_day.setdefault(e["trade_date"], []).append(e)
    session_set = set(days)
    count, per_day = 0, []
    d = days[0]
    while d <= days[-1]:
        if d in session_set:
            why = _judge(by_day.get(d.isoformat(), []))
            count = 0 if why else count + 1
            per_day.append({"date": d.isoformat(), "status": "reset" if why else "clean", "count": count, "reason": why})
        elif d.weekday() < 5:
            per_day.append({"date": d.isoformat(), "status": "paused", "count": count, "reason": "not a session"})
        d += timedelta(days=1)
    return count, per_day


# ------------------------------------------------------------------------------------------------ the pre-flight

@dataclass(frozen=True)
class PreflightResult:
    outcome: str                      # OK or WARN
    root_cause: str | None
    detail: str


def preflight(repo, accepted_commit_file, files_file, venv_python, now: datetime) -> PreflightResult:
    """The checks before the 6:55 run, in this order, the first failure winning. No network is used and no secrets file is read.

    1. the venv python is a file you can run                       env:venv-missing
    2. the accepted commit file holds a commit hash                gate:no-accepted-revision
    3. the files list has at least MIN_FILES entries               gate:files-list-short
    4. `git -C repo diff --quiet COMMIT -- FILES` finds no change  gate:orderpath-mismatch (git failing: gate:git-error)
    5. today, New York, is a session; if not, OK with calendar:holiday (an ordinary stop)"""
    py = Path(venv_python)
    if not (py.is_file() and os.access(py, os.X_OK)):
        return PreflightResult("WARN", "env:venv-missing", "the venv python is not there or cannot be run")
    try:
        commit = Path(accepted_commit_file).read_text().strip()
    except (OSError, UnicodeDecodeError):
        commit = ""
    if not _SHA_RE.fullmatch(commit):                                           # also keeps an option from reaching git
        return PreflightResult("WARN", "gate:no-accepted-revision", "the accepted commit file is missing, empty or not a commit hash")
    try:
        names = [ln.strip() for ln in Path(files_file).read_text().splitlines()]
    except (OSError, UnicodeDecodeError):
        names = []
    names = list(dict.fromkeys(n for n in names if n and not n.startswith("#")))      # each file once
    if len(names) < MIN_FILES:
        return PreflightResult("WARN", "gate:files-list-short", f"the files list has {len(names)} entries; at least {MIN_FILES} are needed")
    try:
        done = subprocess.run(["git", "-C", str(repo), "diff", "--quiet", commit, "--", *names], capture_output=True, text=True,
                              timeout=GIT_SECONDS, env=child_env({"LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0",
                                                                  "GIT_LITERAL_PATHSPECS": "1", "GIT_OPTIONAL_LOCKS": "0"}))
        code = done.returncode
    except (OSError, subprocess.SubprocessError):
        code = -1
    if code == 1:
        return PreflightResult("WARN", "gate:orderpath-mismatch", "the order-path files differ from the accepted revision")
    if code != 0:
        return PreflightResult("WARN", "gate:git-error", "git could not compare the order-path files with the accepted revision")
    if not is_session(new_york_date(now)):
        return PreflightResult("OK", HOLIDAY, "the exchange is closed today")
    return PreflightResult("OK", None, "the order-path files match the accepted revision")


# ------------------------------------------------------------------------------------------------ the dead-man

def _has_terminal_row(conn, trade_date: str, book: str) -> bool:
    return conn.execute(
        'SELECT 1 FROM run_events WHERE trade_date = ? AND book = ? AND ((kind = \'RUN_END\' AND outcome != \'NOOP\') '
        'OR (kind = \'SKIP\' AND "trigger" IN (\'0655\', \'0710\'))) LIMIT 1',
        (trade_date, book)).fetchone() is not None


def _orphan_triggers(day_events: Sequence[dict], now: datetime) -> list[str]:
    """The triggers with a RUN_START that is older than ORPHAN_MINUTES and never got its own RUN_END, in the order they
    started. Starts and ends pair per trigger in event order: each RUN_END closes the latest still-open start of its trigger
    before it (a NOOP end counts). So a later run's end, even a lock-held NOOP, never closes an earlier hung start. Only
    today's events are given."""
    limit = stamp(now - timedelta(minutes=ORPHAN_MINUTES))                      # stamps sort as text: older is smaller
    open_starts: dict[str, list[dict]] = {}
    for e in sorted(day_events, key=lambda x: x["event_id"]):
        if e["kind"] == "RUN_START":
            open_starts.setdefault(e["trigger"], []).append(e)
        elif e["kind"] == "RUN_END" and open_starts.get(e["trigger"]):
            open_starts[e["trigger"]].pop()
    left = sorted((e for starts in open_starts.values() for e in starts), key=lambda x: x["event_id"])
    found: list[str] = []
    for e in left:
        if e["at_utc"] < limit and e["trigger"] not in found:
            found.append(e["trigger"])
    return found


def deadman_check(conn, book: str, now: datetime) -> list[int]:
    """The dead-man check of the 7:25 job: on a session day from 10:25 New York on, write what is due and return the new ids.

    - an orphaned run start (see `_orphan_triggers`) writes DEADMAN/FAULT with cause run:orphan-start:<trigger> (lower case),
      once for each (day, trigger): a P0 that always reaches the owner;
    - the day's dead-man row: DEADMAN/MISSING when no run ended (a NOOP end is not a run end) and none was skipped at 6:55 or
      7:10, else DEADMAN/OK; once a day.
    The two are separate rows, so both can be there on one day. Nothing is written before 10:25 or on a day that is not a
    session; a check that finds both already written writes nothing. Orphan rows come first in the result."""
    ny = _utc(now).astimezone(NEW_YORK)
    day = ny.date().isoformat()
    if ny.time() < DEADMAN_CUTOFF or not is_session(day):
        return []
    written: list[int] = []
    with _tx(conn):
        have = [r[0] for r in conn.execute("SELECT root_cause FROM run_events WHERE trade_date = ? AND book = ? AND kind = 'DEADMAN' "
                                           "AND outcome = 'FAULT'", (day, book))]
        for trigger in _orphan_triggers(events_for(conn, day, book), now):
            cause = ORPHAN_CAUSE + trigger.lower()
            if cause not in have:
                written.append(_insert_event(conn, book=book, kind="DEADMAN", trigger="0725", outcome="FAULT", now=now, trade_date=day,
                                             root_cause=cause, detail=f"the {_RUN_NAMES.get(trigger, trigger)} run started and did not end "
                                                                      f"within {ORPHAN_MINUTES} minutes"))
        if not conn.execute("SELECT 1 FROM run_events WHERE trade_date = ? AND book = ? AND kind = 'DEADMAN' "
                            "AND outcome IN ('OK', 'MISSING') LIMIT 1", (day, book)).fetchone():
            if _has_terminal_row(conn, day, book):
                written.append(_insert_event(conn, book=book, kind="DEADMAN", trigger="0725", outcome="OK", now=now, trade_date=day,
                                             detail="a terminal row is there"))
            else:
                written.append(_insert_event(conn, book=book, kind="DEADMAN", trigger="0725", outcome="MISSING", now=now, trade_date=day,
                                             root_cause=DEADMAN_CAUSE, detail="no run ended and none was skipped by 10:25 New York"))
    return written


def deadman(conn, book: str, now: datetime) -> int | None:
    """`deadman_check`, returning the id of the day's DEADMAN/OK or DEADMAN/MISSING row if this call wrote it. None: nothing
    written (before 10:25, on a day that is not a session, or the day already has its dead-man row; an orphan row may still
    have been written, and `deadman_check` says so)."""
    ids = deadman_check(conn, book, now)
    for i in ids:
        if conn.execute("SELECT outcome FROM run_events WHERE event_id = ?", (i,)).fetchone()[0] in ("OK", "MISSING"):
            return i
    return None


# ------------------------------------------------------------------------------------------------ the order step's summary

@dataclass(frozen=True)
class RunMapping:
    outcome: str
    root_cause: str | None
    fault_count: int
    detail: str


def _read_summary(text: str) -> dict | None:
    """The order step prints its summary as JSON (indented, and a ticket line may follow it); read the first object with a status."""
    text = text[:MAX_SUMMARY_BYTES]
    try:
        obj = json.loads(text.strip())
        return obj if isinstance(obj, dict) and "status" in obj else None
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for n, m in enumerate(re.finditer(r"^\{", text, re.M)):
        if n >= 20:
            break
        try:
            obj, _ = decoder.raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict) and "status" in obj:
            return obj
    return None


def _fault_names(summary: dict) -> list[str]:
    faults = summary.get("faults")
    if not isinstance(faults, list):
        return []
    return [(re.sub(r"[^a-z0-9_:.-]", "-", str(f).lower())[:60] or "unknown") for f in faults]


def map_run(text: str, exit_code: int) -> RunMapping:
    """Map the order step's summary text and exit code to an outcome.

    - not a readable summary, or an exit code that its status does not explain, is CRASH. The order step itself exits 0 when
      its status is `done` and 2 when it is `stopped`, so 2 with a `stopped` summary is the step speaking, not a crash;
    - `done` with exit 0 is COMPLETED (its `faults`, if any, are the fault count and the first one the cause);
    - `stopped` at stage `session` is, by the start of its reason: "another order step is running" NOOP (run:noop:lock-held);
      "outside the regular session window" NOOP (run:noop:outside-window); "the exchange calendar could not be loaded"
      or "... could not answer for" FAULT; any other (the market is closed) ORDINARY_STOP. A NOOP did nothing and says nothing about the day: it is not
      a terminal row; with no faults it is ignored by the streak and never sent (with faults it is a P0 and resets);
    - any other stop is FAULT with cause run:status-stopped:<stage>, its fault count at least 1."""
    summary = _read_summary(text) if isinstance(text, str) else None
    status = summary.get("status") if summary else None
    if status not in ("done", "stopped"):
        if exit_code != 0:
            return RunMapping("CRASH", "run:exit-nonzero", 0, f"exit code {exit_code} and no readable summary")
        return RunMapping("CRASH", "run:summary-unreadable", 0, "exit code 0 but the summary could not be read")
    faults = _fault_names(summary)
    if status == "done":
        if exit_code != 0:
            return RunMapping("CRASH", "run:exit-nonzero", 0, f"summary says done but the exit code is {exit_code}")
        cause = f"run:faults:{faults[0]}" if faults else None
        return RunMapping("COMPLETED", cause, len(faults), clean("done" + (f"; faults: {', '.join(faults)}" if faults else ""), 300))
    if exit_code not in (0, 2):
        return RunMapping("CRASH", "run:exit-nonzero", 0, f"summary says stopped but the exit code is {exit_code}")
    if exit_code == 0:
        return RunMapping("CRASH", "run:exit-mismatch", 0, "summary says stopped but the exit code is 0")
    stage = summary.get("stage")
    stage = stage if isinstance(stage, str) and _STAGE_RE.fullmatch(stage) else "unknown"
    reason = clean(summary.get("reason") or "", 200)
    detail = clean(f"stopped at {stage}" + (f": {reason}" if reason else ""), 300)
    if stage == "session" and not reason.startswith((CALENDAR_FAULT_REASON, CALENDAR_ANSWER_REASON)):
        if reason.startswith(LOCK_HELD_REASON):
            return RunMapping("NOOP", NOOP_LOCK_HELD, len(faults), detail)
        if reason.startswith(OUTSIDE_WINDOW_REASON):
            return RunMapping("NOOP", NOOP_OUTSIDE_WINDOW, len(faults), detail)
        return RunMapping("ORDINARY_STOP", "run:ordinary-stop:session", len(faults), detail)
    return RunMapping("FAULT", f"run:status-stopped:{stage}", max(1, len(faults)), detail)


# ------------------------------------------------------------------------------------------------ the command line

def _print(obj) -> None:
    print(json.dumps(obj, sort_keys=True))


def _needs_owner(conn, event_id: int) -> bool:
    row = dict(conn.execute("SELECT * FROM run_events WHERE event_id = ?", (event_id,)).fetchone())
    return classify(row) in ("P0", "P1", "P2")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m valedia_trader.core.run_ledger", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def command(name, help_):
        c = sub.add_parser(name, help=help_)
        c.add_argument("--store", required=True, help="the sqlite file (never defaulted)")
        c.add_argument("--book", required=True)
        return c

    c = command("record", "write one event")
    c.add_argument("--kind", required=True, choices=KINDS)
    c.add_argument("--trigger", required=True, choices=TRIGGERS)
    c.add_argument("--outcome", required=True, choices=OUTCOMES)
    c.add_argument("--root-cause")
    c.add_argument("--fault-count", type=int, default=0)
    c.add_argument("--revision-ok", type=int, choices=(0, 1))
    c.add_argument("--detail")
    c = command("record-run", "write a RUN_END from the order step's summary on stdin")
    c.add_argument("--trigger", required=True, choices=TRIGGERS)
    c.add_argument("--exit-code", required=True, type=int)
    c.add_argument("--revision-ok", type=int, choices=(0, 1))
    c = command("preflight", "run the pre-flight and write a PREFLIGHT event")
    c.add_argument("--repo", required=True)
    c.add_argument("--accepted-commit-file", required=True)
    c.add_argument("--files-file", required=True)
    c.add_argument("--venv-python", required=True)
    c.add_argument("--trigger", default="0630", choices=TRIGGERS)
    command("deadman", "write the day's dead-man row when it is due")
    c = command("notify", "decide what the owner is owed, send it, write it down")
    c.add_argument("--mac", action="store_true", help="send through macOS notifications (without it nothing is sent or written)")
    c = command("streak", "print the clean-week streak")
    c.add_argument("--since", required=True, help="YYYY-MM-DD")
    return p


def _run(args, now: datetime, stdin, sender) -> int:
    conn = connect(args.store)
    try:
        if args.cmd == "record":
            i = record_event(conn, book=args.book, kind=args.kind, trigger=args.trigger, outcome=args.outcome, now=now,
                             root_cause=args.root_cause, fault_count=args.fault_count, revision_ok=args.revision_ok, detail=args.detail)
            _print({"event_id": i})
            return 1 if _needs_owner(conn, i) else 0
        if args.cmd == "record-run":
            m = map_run(stdin.read(MAX_SUMMARY_BYTES), args.exit_code)
            i = record_event(conn, book=args.book, kind="RUN_END", trigger=args.trigger, outcome=m.outcome, now=now,
                             root_cause=m.root_cause, fault_count=m.fault_count, revision_ok=args.revision_ok, detail=m.detail)
            _print({"event_id": i, "outcome": m.outcome, "root_cause": m.root_cause, "fault_count": m.fault_count})
            return 1 if _needs_owner(conn, i) else 0
        if args.cmd == "preflight":
            r = preflight(args.repo, args.accepted_commit_file, args.files_file, args.venv_python, now)
            i = record_event(conn, book=args.book, kind="PREFLIGHT", trigger=args.trigger, outcome=r.outcome, now=now,
                             root_cause=r.root_cause, detail=r.detail)
            _print({"event_id": i, "outcome": r.outcome, "root_cause": r.root_cause})
            return 1 if _needs_owner(conn, i) else 0
        if args.cmd == "deadman":
            ids = deadman_check(conn, args.book, now)
            if not ids:
                _print({"recorded": False})
                return 0
            rows = [dict(conn.execute("SELECT event_id, outcome, root_cause FROM run_events WHERE event_id = ?", (i,)).fetchone())
                    for i in ids]
            main = next((r for r in rows if r["outcome"] in ("OK", "MISSING")), rows[0])
            _print({"recorded": True, "event_id": main["event_id"], "outcome": main["outcome"], "event_ids": ids,
                    "orphans": [r["root_cause"] for r in rows if r["outcome"] == "FAULT"]})
            return 1 if any(r["outcome"] in ("MISSING", "FAULT") for r in rows) else 0
        if args.cmd == "notify":
            use = (sender if sender is not None else MacSender()) if args.mac else None
            result = notify(conn, args.book, use, now)
            _print(result)
            return 1 if result["due"] else 0
        if args.cmd == "streak":
            since = date.fromisoformat(args.since)
            ny = _utc(now).astimezone(NEW_YORK)
            last = ny.date() if ny.time() >= DEADMAN_CUTOFF else ny.date() - timedelta(days=1)   # a day is judged once it is over
            sessions = sessions_between(since, last)
            count, per_day = streak(events_between(conn, args.book, since.isoformat(), last.isoformat()), sessions)
            _print({"since": since.isoformat(), "through": last.isoformat(), "count": count, "per_day": per_day,
                    "clean_sessions_needed": CLEAN_SESSIONS_NEEDED, "target_met": target_met(count)})
            return 0
        raise ValueError(f"unknown command {args.cmd}")
    finally:
        conn.close()


def main(argv: Sequence[str] | None = None, *, now: datetime | None = None, stdin=None, sender=None) -> int:
    """The command line. 0: nothing needs the owner. 1: an alert was raised or is due. 2: the command itself failed."""
    try:
        args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as stop:
        return int(stop.code or 0) if isinstance(stop.code, int) else 2
    try:
        return _run(args, now if now is not None else datetime.now(timezone.utc), stdin if stdin is not None else sys.stdin, sender)
    except Exception as exc:                                                     # the command failed: say so, no traceback
        print(f"error: {clean(f'{type(exc).__name__}: {exc}', 300)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
