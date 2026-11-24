"""The run ledger: what each day's runs of the order step did, who is told about it, and the clean-week streak.

    python -m valedia_trader.core.run_ledger record     --store PATH --book BOOK --kind K --trigger T --outcome O [...]
    python -m valedia_trader.core.run_ledger record-run --store PATH --book BOOK --trigger T --exit-code N   (summary on stdin)
    python -m valedia_trader.core.run_ledger preflight  --store PATH --book BOOK --repo R --accepted-commit-file F \\
                                                        --files-file L --venv-python P [--trigger T]
    python -m valedia_trader.core.run_ledger deadman    --store PATH --book BOOK [--wait-open] [--orders-ledger PATH]
    python -m valedia_trader.core.run_ledger notify     --store PATH --book BOOK [--mac]
    python -m valedia_trader.core.run_ledger streak     --store PATH --book BOOK --since YYYY-MM-DD

The store is a sqlite file whose path is always given: this module has no default path for it. The one other file it reads is the
practice order ledger, read only and only for the P2 `p2:collar-miss` (see `_collar_miss`). That file has a default,
`default_orders_ledger()`: `memory/orders-webull-sandbox.sqlite` of the repository the module is run from (the order step's
own place for it), so the 7:25 job needs no new argument; `deadman --orders-ledger PATH` overrides it, and a missing or unreadable
file only makes the check fall back to the printed counts. Three tables:

  run_events      one row for each thing that happened (a pre-flight, a run start, a run end, a skip, the dead-man).
                  Append-only: the database itself refuses UPDATE, DELETE and a REPLACE or UPSERT on an existing id.
  alert_incidents one row for each (day, book, cause) that was told to the owner. Only its sent count, its last
                  sent time and its follow-up flag can change, and only upwards.
  send_attempts   one row for each try to tell the owner, whether it worked or not. Append-only. A failed alert is
                  tried at most NOTIFY_MAX_TRIES (5) times in all, the rule core.alerts has.

What reaches the owner (`decide`, a pure function of the day's rows):
  P0  a run that ended in a fault or a crash, or any row with a fault count; a run that started and never ended (an
      orphaned start, written by the dead-man check), the first time it is reported, whether on its own day or later;
      a start that never ended and was never reported, found after a later run took the lock. Sent every time.
  P1  the dead-man: no run ended and none was skipped on purpose by 10:25 New York. Once a day. And a run start of today
      that is still open at the check but not yet old enough to be an orphan (a late launchd fire): once for each trigger.
      And a start from an earlier day that is still unproven dead (no later run has taken the lock) and was already
      reported once as a P0: once a day, naming the start; it replaces the generic dead-man P1 of that day.
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
import time as _time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import quote
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
ORPHAN_MINUTES = 10                                                   # a RUN_START with no RUN_END after this long is an orphan
ORPHAN_CAUSE = "run:orphan-start:"                                    # + the trigger, lower case
PRIOR_ORPHAN_CAUSE = "run:orphan-start-prior:"                        # + <YYYYMMDD of the start>:<trigger lower case>: no lock proof since the start
UNREPORTED_CAUSE = "run:orphan-start-unreported:"                     # + <YYYYMMDD of the start>:<trigger lower case>: a lock proof, and no P0 was ever written
ORDINARY_STOP_SESSION = "run:ordinary-stop:session"                   # a closed market: the order step stops at `session`, after the lock
WAIT_POLL_SECONDS = 15
STILL_OPEN_CAUSE = "deadman:run-still-open:"                          # + the trigger, lower case: an open start too young to be an orphan
HELD_LOCK_TOKEN = "held_lock=1"                                       # first word of the detail of a FAULT run the order step says held its lock
COLLAR_CAUSE = "p2:collar-miss"                                       # two counted sessions where fewer than half the orders filled
COLLAR_MIN_ORDERS = 3                                                 # a session of fewer orders pauses the check
COLLAR_FILL_RATIO = 0.5                                               # a miss is fil/ord below this
ORDERS_LEDGER_NAME = "orders-webull-sandbox.sqlite"                   # the practice venue's order ledger, in the repository's memory folder
ORDERS_LEDGER_SECONDS = 5.0                                           # how long a read of the order ledger waits for a lock
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
_COLLAR_TOKENS_RE = re.compile(r"ord=(\d{1,8}) fil=(\d{1,8})(?: |$)")
_COLLAR_BATCH_RE = re.compile(r"ord=\d{1,8} fil=\d{1,8} batch=(os-\d{8}-\d{6})(?: |$)")   # the token after the two counts
_BATCH_ID_RE = re.compile(r"(os-\d{8}-\d{6})-[0-9a-f]{4,16}")                   # the order step's batch id: os-<date>-<time>-<hex>


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
        if outcome == "WARN" and cause == COLLAR_CAUSE:
            return "P2"
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
    prior_p1 = [e for e in events if e["kind"] == "DEADMAN" and e["outcome"] == "MISSING"
                and (e.get("root_cause") or "").startswith(PRIOR_ORPHAN_CAUSE)]
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
            if e["kind"] == "DEADMAN" and cause.startswith(UNREPORTED_CAUSE):
                out.append(_outbound("P0", e, cause, "P0 order step: a run started and never ended, and was never reported",
                                     f"{cause} on {e['trade_date']}: a run on {_cause_day(cause, UNREPORTED_CAUSE)} started, never ended "
                                     f"and was never reported; a later run took the lock, so it is no longer running"))
                continue
            if e["kind"] == "DEADMAN" and cause.startswith(PRIOR_ORPHAN_CAUSE):
                started = _cause_day(cause, PRIOR_ORPHAN_CAUSE)
                out.append(_outbound("P0", e, cause, "P0 order step: a run from an earlier day never ended",
                                     f"{cause} on {e['trade_date']}: a run started on {started} and no run end ever followed it; "
                                     f"its process may still be alive and hold the order step's lock"))
                continue
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
            if cause == DEADMAN_CAUSE and prior_p1:
                continue                                                        # one P1 a day: the one that names the start wins
            if cause.startswith(PRIOR_ORPHAN_CAUSE) and any(
                    t["severity"] == "P1" and (c == DEADMAN_CAUSE or c.startswith(PRIOR_ORPHAN_CAUSE)) for c, t in told.items()):
                continue                                                        # the day's one P1 was told already
            told[cause], first_kind[cause] = {"root_cause": cause, "severity": "P1", "followup_sent": 0}, e["kind"]
            if cause.startswith(PRIOR_ORPHAN_CAUSE):
                names = "; ".join(f"the {_RUN_NAMES.get(_cause_trigger(p['root_cause']), _cause_trigger(p['root_cause']))} run of "
                                  f"{_cause_day(p['root_cause'], PRIOR_ORPHAN_CAUSE)} ({p['root_cause']})" for p in prior_p1)
                none_ended = "; no terminal row for the day either" if any(
                    x["kind"] == "DEADMAN" and x["outcome"] == "MISSING" and (x.get("root_cause") or DEADMAN_CAUSE) == DEADMAN_CAUSE
                    for x in events) else ""
                out.append(_outbound("P1", e, cause, "P1 order step: a run from an earlier day may still hold the lock",
                                     f"{names} started and never ended, and no run since has taken the order step's lock, so its "
                                     f"process may still be hung on it{none_ended}"))
                continue
            if cause.startswith(STILL_OPEN_CAUSE):
                out.append(_outbound("P1", e, cause, "P1 order step: run still open",
                                     f"{cause} on {e['trade_date']}: a run started and had not ended at the dead-man check "
                                     f"({ORPHAN_MINUTES} minutes old or less); a later check would make it a P0, but none is "
                                     f"scheduled today: look at the Live page"))
                continue
            out.append(_outbound("P1", e, cause, "P1 order step: no run recorded",
                                 f"no terminal row for {e['trade_date']}: nothing ended or was skipped by 10:25 New York"))
        else:
            cause = cause or ("preflight:warn" if e["kind"] == "PREFLIGHT" else "run:skipped")
            have = told.get(cause)
            if have is None:                                                     # a new cause is always sent
                told[cause], first_kind[cause] = {"root_cause": cause, "severity": "P2", "followup_sent": 0}, e["kind"]
                if e["kind"] == "DEADMAN" and cause == COLLAR_CAUSE:
                    out.append(_outbound("P2", e, cause, "P2 order step: orders missing their collar",
                                         f"{cause} on {e['trade_date']}: {e.get('detail') or 'two sessions where fewer than half the orders filled'}"))
                elif e["kind"] == "PREFLIGHT":
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


def _cause_day(cause: str, prefix: str) -> str:
    """The `YYYY-MM-DD` of the start a prior-orphan or unreported cause names."""
    d = cause[len(prefix):].split(":")[0]
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def _cause_trigger(cause: str) -> str:
    return cause.rsplit(":", 1)[1].upper()


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
    if any((e.get("root_cause") or "").startswith((PRIOR_ORPHAN_CAUSE, UNREPORTED_CAUSE)) for e in day_events):
        return "a run from an earlier day never ended"                           # whatever the row's severity
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


def _pair_runs(day_events: Sequence[dict], conservative: bool = False) -> tuple[list[dict], dict[int, dict]]:
    """One day's run rows paired: (the RUN_START rows that never got their own RUN_END, in event order; each paired RUN_END's
    event id mapped to its own RUN_START row). Starts and ends pair per trigger in event order: each RUN_END closes the latest
    still-open start of its trigger before it (a NOOP end counts). A RUN_END with no open start of its trigger is in neither.
    A RUN_END carries no run id (the wrapper writes the trigger only), so when two starts of one trigger are open an end cannot
    say which of them it ends. `conservative` is the pairing for judging lock proofs (`_prior_starts`), where the returned
    mapping holds only the ends whose start is KNOWN:
    - an end that arrives while MORE THAN ONE start of its trigger is open is ambiguous, whatever its kind (a lock-held NOOP
      too): it proves nothing, it closes the EARLIEST open start, so the later starts, the ones that may have hung, stay open
      and are the ones judged, and every start still open at that moment is TAINTED;
    - a later end that closes a tainted start proves nothing either (it may be another run's end);
    - an end that closes the only open start of its trigger, a start that is not tainted, proves it: that is the mapping.
    A start is thus proven only by a run whose own start is later and unambiguously paired. The next such run after a start
    proves it as usual (a run of another trigger, or of this trigger once nothing of it is open), so one ambiguous end never
    blocks a start for good."""
    open_starts: dict[str, list[dict]] = {}
    ended: dict[int, dict] = {}
    tainted: set[int] = set()
    for e in sorted(day_events, key=lambda x: x["event_id"]):
        if e["kind"] == "RUN_START":
            open_starts.setdefault(e["trigger"], []).append(e)
        elif e["kind"] == "RUN_END" and open_starts.get(e["trigger"]):
            stack = open_starts[e["trigger"]]
            if not conservative:
                ended[e["event_id"]] = stack.pop()
            elif len(stack) > 1:
                stack.pop(0)                                                     # ambiguous: no proof, the later starts stay open
                tainted.update(x["event_id"] for x in stack)
            else:
                start = stack.pop()
                if start["event_id"] not in tainted:
                    ended[e["event_id"]] = start
    return sorted((e for starts in open_starts.values() for e in starts), key=lambda x: x["event_id"]), ended


def _unpaired_starts(day_events: Sequence[dict]) -> list[dict]:
    """The RUN_START rows of one day that never got their own RUN_END, in event order (see `_pair_runs`)."""
    return _pair_runs(day_events)[0]


def _open_triggers(day_events: Sequence[dict], now: datetime) -> tuple[list[str], list[str]]:
    """The triggers with a RUN_START that never got its own RUN_END, as (orphans, still_open), each in the order they started.
    A start older than ORPHAN_MINUTES is an orphan; a younger one (or exactly that old) is still open: too young to say it
    hung, as a start launchd fired late after a wake. Starts and ends pair per trigger in event order: each RUN_END closes
    the latest still-open start of its trigger before it (a NOOP end counts). So a later run's end, even a lock-held NOOP,
    never closes an earlier hung start. A trigger with an orphan is not also listed as still open. Only today's events are given."""
    limit = stamp(now - timedelta(minutes=ORPHAN_MINUTES))                      # stamps sort as text: older is smaller
    left = _unpaired_starts(day_events)
    orphans: list[str] = []
    for e in left:
        if e["at_utc"] < limit and e["trigger"] not in orphans:
            orphans.append(e["trigger"])
    still_open: list[str] = []
    for e in left:
        if e["at_utc"] >= limit and e["trigger"] not in orphans and e["trigger"] not in still_open:
            still_open.append(e["trigger"])
    return orphans, still_open


def _is_lock_proof(e: dict) -> bool:
    """A RUN_END that proves its run held the order step's lock (see `deadman_check`). Anything else proves nothing: a missed
    proof only keeps a start blocking, which is the safe side. A FAULT proves it only when its detail's first word is the token
    `held_lock=1` (`map_run` writes it when the order step's summary says `held_lock` is true); a CRASH and a lock-held NOOP never do."""
    if e["kind"] != "RUN_END":
        return False
    cause = e.get("root_cause")
    detail = e.get("detail")
    return (e["outcome"] == "COMPLETED" or (e["outcome"] == "ORDINARY_STOP" and cause == ORDINARY_STOP_SESSION)
            or (e["outcome"] == "NOOP" and cause == NOOP_OUTSIDE_WINDOW)
            or (e["outcome"] == "FAULT" and isinstance(detail, str) and detail.split(" ", 1)[0] == HELD_LOCK_TOKEN))   # the order step took the lock


def _prior_starts(conn, book: str, day: str, now: datetime) -> list[int]:
    """The prior-day rows of `deadman_check`, from one query of the book's run rows up to today."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM run_events WHERE book = ? AND trade_date <= ? AND kind IN ('RUN_START', 'RUN_END', 'DEADMAN') ORDER BY event_id",
        (book, day))]
    by_day: dict[str, list[dict]] = {}
    for e in rows:
        if e["kind"] in ("RUN_START", "RUN_END"):
            by_day.setdefault(e["trade_date"], []).append(e)
    # A lock proof for a start is the RUN_END of a run that STARTED after it: the proof's own RUN_START (paired as everywhere
    # here) has a later `at_utc` than the start (stamps sort as text; the same second is not later). A run that began earlier and
    # ended later may have taken the lock before the start and says nothing about it; a RUN_END with no start of its own proves
    # nothing (the safe side: the start keeps blocking). Pairing is the conservative one (`_pair_runs`): an end that arrives while
    # two starts of its trigger are open is ambiguous and proves nothing, nor does a later end of a start it left tainted.
    proof_start = ""
    for day_rows in by_day.values():
        ended = _pair_runs(day_rows, True)[1]
        proof_start = max([proof_start, *(ended[e["event_id"]]["at_utc"] for e in day_rows
                                          if e["event_id"] in ended and _is_lock_proof(e))])
    faults = [e for e in rows if e["kind"] == "DEADMAN" and e["outcome"] == "FAULT" and e.get("root_cause")]
    p0_own_day: dict[tuple[str, str], str] = {}                                  # (its day, cause) -> the time of the latest such P0
    p0_anywhere: dict[str, str] = {}                                             # a prior or unreported P0 on any day: cause -> its latest time
    for e in faults:
        key = (e["trade_date"], e["root_cause"])
        p0_own_day[key] = max(e["at_utc"], p0_own_day.get(key, ""))
        p0_anywhere[e["root_cause"]] = max(e["at_utc"], p0_anywhere.get(e["root_cause"], ""))
    today_causes = {e["root_cause"] for e in rows if e["kind"] == "DEADMAN" and e["trade_date"] == day and e.get("root_cause")}
    written: list[int] = []
    for prior_day in sorted(d for d in by_day if d < day):
        latest: dict[str, dict] = {}
        for e in _pair_runs(by_day[prior_day], True)[0]:
            latest[e["trigger"]] = e                                             # the latest unpaired start of the trigger: the safe side
        for trigger, e in latest.items():
            tag = f"{prior_day.replace('-', '')}:{trigger.lower()}"
            # A P0 counts for THIS start only when it was written after the start was: a P0 of the same trigger and day that
            # was written before it (an earlier hung run of that trigger) never told the owner about it.
            had_p0 = (p0_own_day.get((prior_day, ORPHAN_CAUSE + trigger.lower()), "") > e["at_utc"]
                      or p0_anywhere.get(PRIOR_ORPHAN_CAUSE + tag, "") > e["at_utc"]
                      or p0_anywhere.get(UNREPORTED_CAUSE + tag, "") > e["at_utc"])
            name = _RUN_NAMES.get(trigger, trigger)
            if proof_start > e["at_utc"]:                                        # a run that started later took the lock: this one is not running
                if not had_p0:
                    written.append(_insert_event(conn, book=book, kind="DEADMAN", trigger="0725", outcome="FAULT", now=now, trade_date=day,
                                                 root_cause=UNREPORTED_CAUSE + tag,
                                                 detail=f"the {name} run of {prior_day} started and never ended, and was never reported; "
                                                        f"a later run took the lock"))
                continue
            cause = PRIOR_ORPHAN_CAUSE + tag
            if cause in today_causes:
                continue                                                         # once for each session day
            written.append(_insert_event(conn, book=book, kind="DEADMAN", trigger="0725", outcome="MISSING" if had_p0 else "FAULT",
                                         now=now, trade_date=day, root_cause=cause,
                                         detail=f"the {name} run of {prior_day} started and never ended: its process may still hold "
                                                f"the order step's lock"))
    return written


@contextmanager
def _orders_reader(path):
    """Read-only access to the order ledger's filled-order counts: yields `filled(stem) -> int | None`, then closes.
    `filled('os-YYYYMMDD-HHMMSS')` is the number of DISTINCT order tags that start with `stem + '-'` (a batch's tags are its id,
    a dash, the symbol) and hold at least one fill row with a nonzero number of units, as the ledger holds them now; a partly
    filled order counts. It is None when nothing can be said: no path, no file, a file sqlite will not read as the order ledger
    (the connection is `mode=ro` with `query_only`: nothing is created, written or upgraded), a failed query, or a batch with no
    order row in the file (a different ledger would otherwise read as nothing filled). The caller then keeps its estimate."""
    db = None
    if path is not None:
        try:
            file = Path(path)
            if file.is_file():
                db = sqlite3.connect("file:" + quote(str(file.resolve())) + "?mode=ro", uri=True, timeout=ORDERS_LEDGER_SECONDS,
                                     isolation_level=None)
                db.execute("PRAGMA query_only = ON")
                db.execute("SELECT 1 FROM orders LIMIT 1")
                db.execute("SELECT 1 FROM fills LIMIT 1")
        except (OSError, ValueError, sqlite3.Error):
            if db is not None:
                db.close()
            db = None

    def filled(stem: str) -> int | None:
        if db is None:
            return None
        prefix = stem + "-"
        try:
            if db.execute("SELECT 1 FROM orders WHERE substr(tag, 1, ?) = ? LIMIT 1", (len(prefix), prefix)).fetchone() is None:
                return None
            return int(db.execute("SELECT COUNT(DISTINCT tag) FROM fills WHERE substr(tag, 1, ?) = ? AND abs(units) > 1e-9",
                                  (len(prefix), prefix)).fetchone()[0])
        except (sqlite3.Error, TypeError, ValueError):
            return None
    try:
        yield filled
    finally:
        if db is not None:
            db.close()


def default_orders_ledger() -> Path:
    """The practice order ledger the order step keeps for the paper book: `memory/orders-webull-sandbox.sqlite` in the repository
    this module is part of (the order step puts one ledger for each venue name in the loop's memory folder; the paper book's venue is
    `webull-sandbox`). Only a path: nothing is opened, created or moved."""
    return Path(__file__).resolve().parents[2] / "memory" / ORDERS_LEDGER_NAME


def _collar_detail(conn, book: str, day: str, orders_ledger=None) -> str | None:
    """The P2 `p2:collar-miss` (read the master list, rounds 37 to 40): written by the 7:25 job on today's trade date D when the
    two latest COUNTED, SETTLED sessions both ended with fewer than half their orders filled.

    A session is one trade date. At the check on D a session S is SETTLED when S < D and at least one COMPLETED RUN_END of the
    book has a trade date after S and up to D: that later run's reconcile has journaled S's late fills (the order step journals
    the fills of earlier orders at the start of its next run). Today's session is never judged at its own check, and a session
    after which no run has completed stays unsettled (the dead-man and the run faults are the alert then).
    A settled session is counted in FULL: its totals are the sums of `ord` and `fil` over ALL its COMPLETED RUN_END rows that carry
    the tokens `ord=N fil=M` first in `detail` (written by `map_run`: the order count and an estimate of the filled count made
    when the run printed its summary; see `_collar_tokens`). A row with a third token `batch=os-<date>-<time>` is counted again
    at the check: its `fil` is the number of that run's orders (the order ledger's tags that start with the batch id and a dash)
    that hold a fill in the order LEDGER now (`orders_ledger`, default `default_orders_ledger()`, opened read only), at most the
    row's `ord`. A row without the batch token (an older revision), or whose ledger cannot be read or holds no order of that
    batch, keeps its printed `fil`. A session is COUNTED when its ord is at least COLLAR_MIN_ORDERS (0 to 2 orders pause it); a
    counted session is a MISS when fil/ord is below COLLAR_FILL_RATIO. Days with no COMPLETED run and rows without tokens (an
    older revision, a FAULT, a SKIP) neither count nor break the pair. The pair is the two latest counted settled sessions; they
    need not be neighbours. One query reads the rows and one read-only connection the ledger, whatever their number.
    The detail says `estimate` only when one of the two sessions of the pair used a printed `fil` (an old row, an unreadable
    ledger or an unknown batch); a row of no orders never does.
    The row is written at most once for each check day (trade date), at every check where the latest settled pair is a miss: a
    check where no new session became settled judges the same pair again, so a miss that goes on gives one row at EVERY check,
    fault days and days without runs included, and it stops at the first check whose latest settled pair is not a miss. Misses on
    sessions D1 and D2 therefore give the first row at the check of the next session day that has a completed run after D2
    (D3 in a week of runs): a lag of one session day compared with judging D2 at its own check, which is what makes the count
    reliable. Sessions whose orders all fill never trip it while the ledger is readable, because the order step sends equity orders as
    DAY orders (venues/webull.py, time in force `DAY`) and the fills of one day's orders are journaled by the next run's reconcile,
    which has completed before the session counts as settled.
    Known limits:
    - when no run completes after a session, it stays unsettled and is not judged;
    - an order still unjournaled after the next completed run (a fill the venue reports late, or a reconcile that missed it) reads
      as unfilled until a later run journals it, so it can count against its session at that check;
    - a fill that lands after the settling run has reconciled reads as unfilled until a later run journals it, so a session
      counted right after its first settling run can still show a late fill as unfilled (it is counted right at the next check);
    - a partly filled order counts as filled;
    - FALLBACK: when the order ledger cannot be read, holds no order of the batch, or the row has no `batch=` token, the printed
      `fil` is used (see `_collar_tokens` for its own limits), and a fill that lands after the run printed its summary reads as
      unfilled, so LATE FILLS CAN TRIP A P2 in that mode; the detail says `estimate`. An order ledger that is not where
      `default_orders_ledger` says is such a case unless `orders_ledger` is given.
    The row is DEADMAN/WARN, cause p2:collar-miss, fault_count 0: `_judge` and `streak` never read it, so it neither resets nor
    pauses the clean streak and cannot make a clean day unclean, and nothing is ever removed or rewritten. A store with no
    tokens, or with one counted settled session, writes nothing."""
    rows = [dict(r) for r in conn.execute(
        "SELECT trade_date, detail FROM run_events WHERE book = ? AND trade_date <= ? AND kind = 'RUN_END' "
        "AND outcome = 'COMPLETED' ORDER BY event_id", (book, day))]
    last_run_day = max((r["trade_date"] for r in rows), default="")             # a session before it has a later completed run
    totals: dict[str, list[int]] = {}
    estimate_days: set[str] = set()                                              # the days with a row that used its printed `fil`
    with _orders_reader(default_orders_ledger() if orders_ledger is None else orders_ledger) as filled:
        for r in rows:
            if r["trade_date"] >= last_run_day or r["trade_date"] >= day:
                continue                                                         # not settled
            detail = r["detail"] or ""
            m = _COLLAR_TOKENS_RE.match(detail)
            if not m:
                continue
            ord_, fil = int(m.group(1)), int(m.group(2))
            b = _COLLAR_BATCH_RE.match(detail)
            seen = filled(b.group(1)) if b else None
            if seen is not None:
                fil = min(seen, ord_)
            elif ord_ > 0:
                estimate_days.add(r["trade_date"])                               # a row of no orders adds nothing to be unsure of
            t = totals.setdefault(r["trade_date"], [0, 0])
            t[0] += ord_
            t[1] += fil
    counted = [(d, o, f) for d, (o, f) in sorted(totals.items()) if o >= COLLAR_MIN_ORDERS]
    if len(counted) < 2:
        return None
    pair = counted[-2:]
    if any(f / o >= COLLAR_FILL_RATIO for _, o, f in pair):
        return None
    (d1, o1, f1), (d2, o2, f2) = pair
    how = ("the count is an estimate from the run summary" if {d1, d2} & estimate_days
           else "counted from the order ledger at the check")
    return f"fewer than half the orders filled on {d1} (fil {f1} of ord {o1}) and {d2} (fil {f2} of ord {o2}); {how}"


def _collar_write(conn, book: str, day: str, now: datetime, detail: str | None) -> list[int]:
    """The write half of `_collar_miss`: the row for `detail` (from `_collar_detail`), unless today already has one. This is what
    runs inside the store's write lock, and the only check made there: at most one collar row for each trade date."""
    if detail is None:
        return []
    if conn.execute("SELECT 1 FROM run_events WHERE trade_date = ? AND book = ? AND kind = 'DEADMAN' AND root_cause = ? LIMIT 1",
                    (day, book, COLLAR_CAUSE)).fetchone():
        return []
    return [_insert_event(conn, book=book, kind="DEADMAN", trigger="0725", outcome="WARN", now=now, trade_date=day,
                          root_cause=COLLAR_CAUSE, detail=detail)]


def _collar_miss(conn, book: str, day: str, now: datetime, orders_ledger=None) -> list[int]:
    """The two halves together (`_collar_detail`, then `_collar_write`); `deadman_check` calls them apart so that the order ledger
    is read BEFORE the store's write lock is taken. The rule is in `_collar_detail`."""
    return _collar_write(conn, book, day, now, _collar_detail(conn, book, day, orders_ledger))


def deadman_check(conn, book: str, now: datetime, orders_ledger=None) -> list[int]:
    """The dead-man check of the 7:25 job: on a session day from 10:25 New York on, write what is due and return the new ids.

    - an orphaned run start (see `_open_triggers`) writes DEADMAN/FAULT with cause run:orphan-start:<trigger> (lower case),
      once for each (day, trigger): a P0 that always reaches the owner;
    - an open run start that is not yet an orphan (ORPHAN_MINUTES old or less) writes DEADMAN/MISSING with cause
      deadman:run-still-open:<trigger> (lower case), once for each (day, trigger): a P1. It covers a run launchd fired late;
      a later check writes the orphan P0 beside it if the run is still open by then;
    - an unpaired run start from an earlier day (same book, paired per trigger within its own trade_date, however old) is judged
      by whether a LOCK PROOF has come since it: the RUN_END, of a run that STARTED after the start (its own RUN_START, paired per
      trigger as above, was recorded after the start; a run that began earlier and ended later may have taken the lock before the
      start and proves nothing, nor does an end with no start of its own), that is COMPLETED,
      or ORDINARY_STOP with cause run:ordinary-stop:session, or NOOP with cause run:noop:outside-window (the order step takes
      its lock before the `session` stage, so those runs held it; a FAULT whose detail starts with the word `held_lock=1`, written when
      the order step's summary says it held the lock, proves it too; a lock-held NOOP, any other FAULT and a CRASH prove nothing).
        no proof: on TODAY's trade_date, once for each (today, start day, trigger), cause run:orphan-start-prior:<YYYYMMDD of
          the start>:<trigger lower case>. DEADMAN/FAULT (a P0) when no P0 was ever written for the start (its own day's
          run:orphan-start:<trigger>, or an earlier prior or unreported row; a P0 counts for a start only when it was written
          AFTER that start, so a second hung start of one trigger and day is not covered by the first one's P0), else DEADMAN/MISSING (a P1). Either resets today's streak;
        a proof, and the start already had a P0: nothing;
        a proof, and the start never had a P0: DEADMAN/FAULT with cause run:orphan-start-unreported:<YYYYMMDD>:<trigger>,
          once ever for the book, written on today (a P0, and it resets today's streak);
      all of it is found with ONE query of the book's run rows, whatever the number of starts;
    - the day's dead-man row: DEADMAN/MISSING when no run ended (a NOOP end is not a run end) and none was skipped at 6:55 or
      7:10, else DEADMAN/OK; once a day. A still-open row never stands in for it.
    - the P2 `p2:collar-miss` (DEADMAN/WARN, see `_collar_miss`): the two latest counted SETTLED sessions (a session before today
      that a later completed run has followed; today is never judged at its own check) both with fewer than half their orders
      filled, counted in full from the order ledger (`orders_ledger`, read only, default `default_orders_ledger()`) for rows that
      carry a `batch=` token; once for each check day, at every check where the latest settled pair is a miss; it is last in the
      result and no row of the streak reads it.
    The order ledger for the collar row is read once, before the store's write lock is taken (a slow or locked order ledger
    never holds the run ledger's lock); inside the lock only the one-row-for-each-date check and the writes are made.
    The rows are separate, so all of them can be there on one day. Nothing is written before 10:25 or on a day that is not a
    session; a check that finds all already written writes nothing. Today's orphan rows come first in the result, then prior-day rows (oldest start first), then still-open rows, then the day row, then the collar row."""
    ny = _utc(now).astimezone(NEW_YORK)
    day = ny.date().isoformat()
    if ny.time() < DEADMAN_CUTOFF or not is_session(day):
        return []
    written: list[int] = []
    collar = _collar_detail(conn, book, day, orders_ledger)                      # reads the order ledger BEFORE the write lock is taken
    with _tx(conn):
        have = [r[0] for r in conn.execute("SELECT root_cause FROM run_events WHERE trade_date = ? AND book = ? AND kind = 'DEADMAN' "
                                           "AND outcome = 'FAULT'", (day, book))]
        orphans, still_open = _open_triggers(events_for(conn, day, book), now)
        for trigger in orphans:
            cause = ORPHAN_CAUSE + trigger.lower()
            if cause not in have:
                written.append(_insert_event(conn, book=book, kind="DEADMAN", trigger="0725", outcome="FAULT", now=now, trade_date=day,
                                             root_cause=cause, detail=f"the {_RUN_NAMES.get(trigger, trigger)} run started and did not end "
                                                                      f"within {ORPHAN_MINUTES} minutes"))
        written += _prior_starts(conn, book, day, now)
        have_open = [r[0] for r in conn.execute("SELECT root_cause FROM run_events WHERE trade_date = ? AND book = ? AND kind = 'DEADMAN' "
                                                "AND outcome = 'MISSING' AND root_cause GLOB ?", (day, book, STILL_OPEN_CAUSE + "*"))]
        for trigger in still_open:
            cause = STILL_OPEN_CAUSE + trigger.lower()
            if cause not in have_open:
                written.append(_insert_event(conn, book=book, kind="DEADMAN", trigger="0725", outcome="MISSING", now=now, trade_date=day,
                                             root_cause=cause, detail=f"the {_RUN_NAMES.get(trigger, trigger)} run started and had not ended "
                                                                      f"at the 10:25 New York check"))
        if not conn.execute("SELECT 1 FROM run_events WHERE trade_date = ? AND book = ? AND kind = 'DEADMAN' "
                            "AND outcome IN ('OK', 'MISSING') AND COALESCE(root_cause, '') NOT GLOB ? AND COALESCE(root_cause, '') NOT GLOB ? LIMIT 1",
                            (day, book, STILL_OPEN_CAUSE + "*", PRIOR_ORPHAN_CAUSE + "*")).fetchone():
            if _has_terminal_row(conn, day, book):
                written.append(_insert_event(conn, book=book, kind="DEADMAN", trigger="0725", outcome="OK", now=now, trade_date=day,
                                             detail="a terminal row is there"))
            else:
                written.append(_insert_event(conn, book=book, kind="DEADMAN", trigger="0725", outcome="MISSING", now=now, trade_date=day,
                                             root_cause=DEADMAN_CAUSE, detail="no run ended and none was skipped by 10:25 New York"))
        written += _collar_write(conn, book, day, now, collar)                   # only the once-for-each-date check is made in the lock
    return written


def deadman(conn, book: str, now: datetime, orders_ledger=None) -> int | None:
    """`deadman_check`, returning the id of the day's DEADMAN/OK or DEADMAN/MISSING row if this call wrote it. None: nothing
    written (before 10:25, on a day that is not a session, or the day already has its dead-man row; an orphan row may still
    have been written, and `deadman_check` says so)."""
    ids = deadman_check(conn, book, now, orders_ledger)
    for i in ids:
        outcome, cause = conn.execute("SELECT outcome, root_cause FROM run_events WHERE event_id = ?", (i,)).fetchone()
        if outcome in ("OK", "MISSING") and not (cause or "").startswith((STILL_OPEN_CAUSE, PRIOR_ORPHAN_CAUSE)):
            return i
    return None


def _any_open_today(conn, book: str, now: datetime) -> bool:
    """True when it is 10:25 New York or later on a session day and one of today's run starts is unpaired and not yet an orphan."""
    ny = _utc(now).astimezone(NEW_YORK)
    day = ny.date().isoformat()
    if ny.time() < DEADMAN_CUTOFF or not is_session(day):
        return False
    return bool(_open_triggers(events_for(conn, day, book), now)[1])


def deadman_wait(conn, book: str, now_fn, sleep_fn, poll_s: float = WAIT_POLL_SECONDS, orders_ledger=None) -> list[int]:
    """Call this after `deadman_check` has written, and notify has sent, the still-open P1. While one of today's run starts is
    still open and at most ORPHAN_MINUTES old, `sleep_fn(poll_s)` and run `deadman_check` again; return the ids written (the
    orphan P0, normally) as soon as every start is paired or is an orphan. A run that hangs thus gets its P0 from this very
    process. Nothing open (or before 10:25, or on a day that is no session): it returns at once, never sleeping. A prior-day
    orphan is written by `deadman_check` itself, so it is never waited for.

    The wait is capped at ORPHAN_MINUTES + 1 minutes in all, by the sum of the sleeps and by `now_fn`, whichever comes first;
    the last sleep is shortened to fit. `now_fn() -> datetime` and `sleep_fn(seconds)` are injected, so a test never really
    sleeps. After the cap it returns whatever was written, even if a start is still open."""
    cap = (ORPHAN_MINUTES + 1) * 60
    began = now_fn()
    slept = 0.0
    written: list[int] = []
    while _any_open_today(conn, book, now_fn()):
        waited = max(slept, (now_fn() - began).total_seconds())
        if waited >= cap:
            break
        step = min(poll_s, cap - waited)
        sleep_fn(step)
        slept += step
        written += deadman_check(conn, book, now_fn(), orders_ledger)
    return written


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


def _prefixed(detail: str, tokens: Sequence[str]) -> str:
    """`detail` with the given `key=value` tokens as its first words, cleaned and cut to 300 as every detail is. The tokens come
    first so the cut never takes them. With no tokens the detail is returned as it is (byte for byte). The one helper for both
    kinds: `held_lock=1` goes on a FAULT row only and `ord=N fil=M` on a COMPLETED row only, so they never share a row."""
    return clean(" ".join([*tokens, detail]), 300) if tokens else detail


def _is_count(v) -> bool:
    return type(v) is int and v >= 0


def _collar_tokens(summary: dict) -> list[str]:
    """`ord=N fil=M` (and, when the summary names its batch, `batch=os-<date>-<time>` after them) for a COMPLETED run, or []
    when the summary does not give the three facts as usable values.
    ord is `orders_sent`; fil is the number of entries of `sent` whose symbol has a nonzero value in `filled_so_far`.
    `fil` is an ESTIMATE at the moment the run printed its summary, because that summary has no per-order filled fact and
    `filled_so_far` holds only the fills this run's own batch had journaled by then. Known limits of `fil` itself: a fill that a
    later run journals is not seen by it; two legs of one symbol (a fractional order and its residual) count together; a
    purchase and a sale of one symbol that net to zero read as unfilled. `_collar_miss` therefore does not trust `fil` when the
    row has a `batch=` token: it counts the batch's filled orders again from the order ledger when it judges.
    The batch token is the order step's batch id without its random tail (`os-YYYYMMDD-HHMMSS`): `clean` cuts a whole batch id
    to its last 4 characters, which would leave nothing to match, and the stem alone names the run (a batch id is one run's
    start time and the order step's lock allows one run at a time). A `batch` that is not in the order step's format adds
    no token, and the row reads as before."""
    ord_, sent, filled = summary.get("orders_sent"), summary.get("sent"), summary.get("filled_so_far")
    if not _is_count(ord_) or not isinstance(sent, list) or not isinstance(filled, dict):
        return []
    def moved(symbol) -> bool:
        v = filled.get(symbol) if isinstance(symbol, str) else None
        return type(v) in (int, float) and v == v and abs(v) > 1e-9
    fil = sum(1 for entry in sent if isinstance(entry, dict) and moved(entry.get("symbol")))
    batch = summary.get("batch")
    m = _BATCH_ID_RE.fullmatch(batch) if isinstance(batch, str) else None
    return [f"ord={ord_}", f"fil={fil}", *([f"batch={m.group(1)}"] if m else [])]


def map_run(text: str, exit_code: int) -> RunMapping:
    """Map the order step's summary text and exit code to an outcome.

    - not a readable summary, or an exit code that its status does not explain, is CRASH. The order step itself exits 0 when
      its status is `done` and 2 when it is `stopped`, so 2 with a `stopped` summary is the step speaking, not a crash;
    - `done` with exit 0 is COMPLETED (its `faults`, if any, are the fault count and the first one the cause);
    - `stopped` at stage `session` is, by the start of its reason: "another order step is running" NOOP (run:noop:lock-held);
      "outside the regular session window" NOOP (run:noop:outside-window); "the exchange calendar could not be loaded"
      or "... could not answer for" FAULT; any other (the market is closed) ORDINARY_STOP. A NOOP did nothing and says nothing about the day: it is not
      a terminal row; with no faults it is ignored by the streak and never sent (with faults it is a P0 and resets);
    - any other stop is FAULT with cause run:status-stopped:<stage>, its fault count at least 1; when the summary's `held_lock`
      is a real True (the order step took its lock), the detail starts with `held_lock=1` (a lock proof, see `_is_lock_proof`);
    - a COMPLETED run whose summary gives `orders_sent`, `sent` and `filled_so_far` as usable values has `ord=N fil=M` first in
      its detail (see `_collar_tokens`)."""
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
        detail = clean("done" + (f"; faults: {', '.join(faults)}" if faults else ""), 300)
        return RunMapping("COMPLETED", cause, len(faults), _prefixed(detail, _collar_tokens(summary)))
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
    tokens = [HELD_LOCK_TOKEN] if summary.get("held_lock") is True else []      # only a real bool True; anything else reads as before
    return RunMapping("FAULT", f"run:status-stopped:{stage}", max(1, len(faults)), _prefixed(detail, tokens))


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
    c = command("deadman", "write the day's dead-man row when it is due")
    c.add_argument("--orders-ledger", help="the order ledger's file, read only, for the p2:collar-miss recount "
                                           "(default: memory/orders-webull-sandbox.sqlite of this repository)")
    c.add_argument("--wait-open", action="store_true",
                   help="then wait (at most ORPHAN_MINUTES + 1 minutes) for a still-open run to end or become an orphan")
    c = command("notify", "decide what the owner is owed, send it, write it down")
    c.add_argument("--mac", action="store_true", help="send through macOS notifications (without it nothing is sent or written)")
    c = command("streak", "print the clean-week streak")
    c.add_argument("--since", required=True, help="YYYY-MM-DD")
    return p


def _run(args, now: datetime, stdin, sender, now_fn=None, sleep_fn=None) -> int:
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
            ids = deadman_check(conn, args.book, now, args.orders_ledger)
            if args.wait_open:
                ids += deadman_wait(conn, args.book, now_fn or (lambda: now), sleep_fn or _time.sleep, orders_ledger=args.orders_ledger)
            if not ids:
                _print({"recorded": False})
                return 0
            rows = [dict(conn.execute("SELECT event_id, outcome, root_cause FROM run_events WHERE event_id = ?", (i,)).fetchone())
                    for i in ids]
            is_open = [(r["root_cause"] or "").startswith(STILL_OPEN_CAUSE) for r in rows]
            is_prior = [(r["root_cause"] or "").startswith(PRIOR_ORPHAN_CAUSE) for r in rows]      # a P1 prior row is no day row
            main = next((r for r, o, q in zip(rows, is_open, is_prior) if r["outcome"] in ("OK", "MISSING") and not o and not q), rows[0])
            _print({"recorded": True, "event_id": main["event_id"], "outcome": main["outcome"], "event_ids": ids,
                    "orphans": [r["root_cause"] for r, q in zip(rows, is_prior) if r["outcome"] == "FAULT" or q],
                    "still_open": [r["root_cause"] for r, o in zip(rows, is_open) if o]})
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


def main(argv: Sequence[str] | None = None, *, now: datetime | None = None, stdin=None, sender=None, sleep=None) -> int:
    """The command line. 0: nothing needs the owner. 1: an alert was raised or is due. 2: the command itself failed.
    `now` fixes the time for a test; the clock of `deadman --wait-open` is then a virtual one that moves forward by each
    sleep (and `sleep` is called too, to watch it), so a test never really sleeps. Without `now` the clock and sleep are real."""
    try:
        args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as stop:
        return int(stop.code or 0) if isinstance(stop.code, int) else 2
    try:
        if now is None:
            now_fn, sleep_fn = (lambda: datetime.now(timezone.utc)), (sleep or _time.sleep)
        else:
            virtual = [now]

            def now_fn():
                return virtual[0]

            def sleep_fn(seconds):
                virtual[0] += timedelta(seconds=seconds)
                if sleep is not None:
                    sleep(seconds)
        return _run(args, now_fn(), stdin if stdin is not None else sys.stdin, sender, now_fn, sleep_fn)
    except Exception as exc:                                                     # the command failed: say so, no traceback
        print(f"error: {clean(f'{type(exc).__name__}: {exc}', 300)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
