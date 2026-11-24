"""Tests for the run ledger: the day's runs of the order step, the alert policy, the clean-week streak, the pre-flight,
the dead-man and the command line. Every guard is tested both ways: the allowed case goes through and the refused or
suppressed case is refused (in code and, where the rule lives in the database, through raw SQL).

Every outside thing is a stand-in: the store is a temporary file, the sender is a fake one (no notification is shown),
the clock is given, the git repository of the pre-flight is built here, and nothing real is read."""
import io
import json
import re
import socket
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from valedia_trader.core import run_ledger as rl

BOOK = "webull-paper-shared"
MON = "2026-11-23"                                    # a Monday, an XNYS session


def utc(y, m, d, h, mi=0, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc)


# Pacific Standard Time is UTC-8 and New York is UTC-5 on these dates: 6:55 PST is 09:55 New York.
T0630 = utc(2026, 11, 23, 14, 30)
T0655 = utc(2026, 11, 23, 14, 55)
T0710 = utc(2026, 11, 23, 15, 10)
T1024 = utc(2026, 11, 23, 15, 24)                     # 10:24 New York
T1025 = utc(2026, 11, 23, 15, 25)                     # 10:25 New York


class FakeSender:
    """Stands in for the notifier: remembers what it was given; fails the first `fail` times (or always)."""

    def __init__(self, fail=0, always=False):
        self.sent, self.calls, self.fail, self.always = [], 0, fail, always

    def send(self, title, text):
        self.calls += 1
        if self.always or self.calls <= self.fail:
            raise RuntimeError("the notifier is down")
        self.sent.append((title, text))


@pytest.fixture
def conn(tmp_path):
    c = rl.connect(tmp_path / "ledger.sqlite")
    yield c
    c.close()


def add(conn, kind, trigger, outcome, now, **kw):
    return rl.record_event(conn, book=kw.pop("book", BOOK), kind=kind, trigger=trigger, outcome=outcome, now=now, **kw)


def count(conn, table="run_events"):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def notify(conn, sender, now, book=BOOK):
    return rl.notify(conn, book, sender, now)


# ------------------------------------------------------------------------------------------------ the store

GOOD = dict(trade_date=MON, book=BOOK, kind="RUN_END", trigger="0655", outcome="COMPLETED", root_cause=None,
            fault_count=0, revision_ok=1, detail=None, at_utc="2026-11-23T14:55:00Z")


def raw_event(conn, **over):
    row = {**GOOD, **over}
    cols = ", ".join(row)
    return conn.execute(f"INSERT INTO run_events ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values())).lastrowid


def raw_incident(conn, event_id, **over):
    row = dict(trade_date=MON, book=BOOK, root_cause="gate:orderpath-mismatch", severity="P2", first_event_id=event_id,
               first_seen_utc="2026-11-23T14:30:00Z", sent_count=1, last_sent_utc="2026-11-23T14:30:05Z", followup_sent=0)
    row.update(over)
    cols = ", ".join(row)
    return conn.execute(f"INSERT INTO alert_incidents ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values())).lastrowid


def test_connect_sets_full_sync_and_foreign_keys(conn):
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2                 # FULL
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_connect_needs_a_path_and_never_defaults_one():
    with pytest.raises(TypeError):
        rl.connect()                                                               # there is no default path


def test_connect_twice_keeps_the_rows(tmp_path):
    p = tmp_path / "l.sqlite"
    c = rl.connect(p)
    raw_event(c)
    c.close()
    c2 = rl.connect(p)                                                             # the schema is made once, not remade
    assert count(c2) == 1
    c2.close()


def test_a_good_row_goes_in(conn):
    assert raw_event(conn) >= 1


def test_update_is_refused(conn):
    i = raw_event(conn)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE run_events SET outcome = 'FAULT' WHERE event_id = ?", (i,))
    assert conn.execute("SELECT outcome FROM run_events").fetchone()[0] == "COMPLETED"


def test_delete_is_refused(conn):
    i = raw_event(conn)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM run_events WHERE event_id = ?", (i,))
    assert count(conn) == 1


@pytest.mark.parametrize("sql", [
    "INSERT OR REPLACE INTO run_events (event_id, trade_date, book, kind, trigger, outcome, fault_count, at_utc) "
    "VALUES (?, '2026-11-23', 'b', 'RUN_END', '0655', 'FAULT', 1, '2026-11-23T14:55:00Z')",
    "REPLACE INTO run_events (event_id, trade_date, book, kind, trigger, outcome, fault_count, at_utc) "
    "VALUES (?, '2026-11-23', 'b', 'RUN_END', '0655', 'FAULT', 1, '2026-11-23T14:55:00Z')",
    "INSERT INTO run_events (event_id, trade_date, book, kind, trigger, outcome, fault_count, at_utc) "
    "VALUES (?, '2026-11-23', 'b', 'RUN_END', '0655', 'FAULT', 1, '2026-11-23T14:55:00Z') "
    "ON CONFLICT (event_id) DO UPDATE SET outcome = 'FAULT'",
    "INSERT INTO run_events (event_id, trade_date, book, kind, trigger, outcome, fault_count, at_utc) "
    "VALUES (?, '2026-11-23', 'b', 'RUN_END', '0655', 'FAULT', 1, '2026-11-23T14:55:00Z')",
])
def test_replace_and_upsert_on_an_existing_id_are_refused(conn, sql):
    i = raw_event(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, (i,))
    assert conn.execute("SELECT outcome FROM run_events WHERE event_id = ?", (i,)).fetchone()[0] == "COMPLETED"
    assert count(conn) == 1


def test_an_insert_with_a_new_explicit_id_goes_in(conn):
    i = raw_event(conn)
    j = raw_event(conn, event_id=i + 100)
    assert j == i + 100 and count(conn) == 2


@pytest.mark.parametrize("field,bad", [
    ("kind", "BOGUS"), ("kind", "run_end"), ("kind", None),
    ("trigger", "0600"), ("trigger", "manual"), ("trigger", None),
    ("outcome", "DONE"), ("outcome", "ok"), ("outcome", "noop"), ("outcome", None),
    ("book", None), ("book", ""),
    ("fault_count", -1), ("fault_count", "many"), ("fault_count", 1.5), ("fault_count", None),
    ("revision_ok", 2), ("revision_ok", -1), ("revision_ok", "yes"),
    ("trade_date", None), ("trade_date", "2026-11-31"), ("trade_date", "23-11-2026"), ("trade_date", "2026-11-23T00:00:00Z"),
    ("at_utc", None), ("at_utc", "2026-11-23 14:55:00"), ("at_utc", "2026-11-23T14:55:00"), ("at_utc", "2026-11-23T14:55:00+00:00"),
    ("at_utc", "2026-11-23T14:55:00.5Z"), ("at_utc", "2026-13-23T14:55:00Z"), ("at_utc", "2026-11-23T25:55:00Z"),
    ("at_utc", "2026-11-23T14:55:00Zjunk"),
    ("root_cause", "Gate:Mismatch"), ("root_cause", "has space"), ("root_cause", ""), ("root_cause", "x" * 121),
])
def test_every_enum_and_format_check_refuses_a_bad_value(conn, field, bad):
    with pytest.raises(sqlite3.IntegrityError):
        raw_event(conn, **{field: bad})
    assert count(conn) == 0


@pytest.mark.parametrize("field,good", [
    ("kind", "PREFLIGHT"), ("kind", "RUN_START"), ("kind", "SKIP"), ("kind", "DEADMAN"),
    ("trigger", "0630"), ("trigger", "0710"), ("trigger", "0725"), ("trigger", "MANUAL"),
    ("outcome", "OK"), ("outcome", "WARN"), ("outcome", "ORDINARY_STOP"), ("outcome", "FAULT"), ("outcome", "CRASH"),
    ("outcome", "SKIPPED"), ("outcome", "MISSING"), ("outcome", "NOOP"),
    ("revision_ok", None), ("revision_ok", 0), ("fault_count", 7),
    ("root_cause", "run:status-stopped:send"), ("root_cause", "gate:orderpath-mismatch"), ("root_cause", "x" * 120),
    ("at_utc", "2026-02-28T23:59:59Z"),
])
def test_every_enum_and_format_check_allows_a_good_value(conn, field, good):
    raw_event(conn, **{field: good})
    assert count(conn) == 1


def test_fault_count_defaults_to_zero(conn):
    conn.execute("INSERT INTO run_events (trade_date, book, kind, trigger, outcome, at_utc) "
                 "VALUES ('2026-11-23', 'b', 'RUN_START', '0655', 'OK', '2026-11-23T14:55:00Z')")
    assert conn.execute("SELECT fault_count FROM run_events").fetchone()[0] == 0


def test_the_trade_date_book_index_exists(conn):
    names = {r[1] for r in conn.execute("PRAGMA index_list(run_events)")}
    cols = [[c[2] for c in conn.execute(f"PRAGMA index_info({n})")] for n in names]
    assert ["trade_date", "book"] in cols


# ---- the incidents table

def test_an_incident_row_goes_in_and_a_second_for_the_same_key_is_refused(conn):
    e = raw_event(conn)
    raw_incident(conn, e)
    with pytest.raises(sqlite3.IntegrityError):
        raw_incident(conn, e)                                                      # one row per (date, book, cause)
    raw_incident(conn, e, root_cause="gate:other")                                 # another cause is another row
    raw_incident(conn, e, book="other-book")                                       # another book too
    assert count(conn, "alert_incidents") == 3


def test_an_incident_needs_a_real_first_event(conn):
    with pytest.raises(sqlite3.IntegrityError):
        raw_incident(conn, 999)


@pytest.mark.parametrize("field,bad", [("severity", "P4"), ("severity", "p2"), ("severity", None), ("followup_sent", 2),
                                       ("sent_count", -1), ("first_seen_utc", "2026-11-23 14:30:00"),
                                       ("last_sent_utc", "yesterday"), ("trade_date", "2026-02-30")])
def test_incident_checks_refuse_a_bad_value(conn, field, bad):
    e = raw_event(conn)
    with pytest.raises(sqlite3.IntegrityError):
        raw_incident(conn, e, **{field: bad})


@pytest.mark.parametrize("sql", [
    "UPDATE alert_incidents SET sent_count = 2",
    "UPDATE alert_incidents SET last_sent_utc = '2026-11-23T14:56:00Z'",
    "UPDATE alert_incidents SET followup_sent = 1",
    "UPDATE alert_incidents SET sent_count = 2, last_sent_utc = '2026-11-23T14:56:00Z', followup_sent = 1",
])
def test_an_incident_may_be_raised_in_the_three_sent_fields(conn, sql):
    raw_incident(conn, raw_event(conn))
    conn.execute(sql)


@pytest.mark.parametrize("sql", [
    "UPDATE alert_incidents SET severity = 'P0'",
    "UPDATE alert_incidents SET root_cause = 'gate:renamed'",
    "UPDATE alert_incidents SET trade_date = '2026-11-24'",
    "UPDATE alert_incidents SET book = 'other'",
    "UPDATE alert_incidents SET first_seen_utc = '2026-11-23T14:31:00Z'",
    "UPDATE alert_incidents SET first_event_id = (SELECT max(event_id) FROM run_events)",
    "UPDATE alert_incidents SET sent_count = 0",                                   # lowered
    "UPDATE alert_incidents SET last_sent_utc = '2026-11-23T14:00:00Z'",           # moved back
    "UPDATE alert_incidents SET last_sent_utc = NULL",
    "DELETE FROM alert_incidents",
])
def test_an_incident_is_otherwise_immutable(conn, sql):
    e = raw_event(conn)
    raw_event(conn)                                                                # a second event, for first_event_id
    raw_incident(conn, e)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)


def test_a_followup_flag_cannot_be_lowered(conn):
    raw_incident(conn, raw_event(conn), followup_sent=1)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE alert_incidents SET followup_sent = 0")


def test_send_attempts_are_append_only(conn):
    e = raw_event(conn)
    conn.execute("INSERT INTO send_attempts (trade_date, book, root_cause, event_id, ok, error, at_utc) "
                 "VALUES (?, ?, 'x', ?, 0, 'down', '2026-11-23T14:55:00Z')", (MON, BOOK, e))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE send_attempts SET ok = 1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM send_attempts")


# ---- round 2: replace guards on the incidents and the attempts, the hour 24, a blob cause, duplicates, raw incident rows

INCIDENT_COLS = ("trade_date, book, root_cause, severity, first_event_id, first_seen_utc, sent_count, last_sent_utc, followup_sent")


def incident_row(conn):
    return tuple(conn.execute("SELECT * FROM alert_incidents").fetchone())


def raised_incident(conn):
    """An incident that has been sent three times with its follow-up: what a replace would reset."""
    e = raw_event(conn)
    raw_incident(conn, e, severity="P2", sent_count=3, last_sent_utc="2026-11-23T14:56:00Z", followup_sent=1)
    return e


@pytest.mark.parametrize("verb", ["INSERT OR REPLACE INTO", "REPLACE INTO", "INSERT OR IGNORE INTO", "INSERT INTO"])
def test_an_incident_key_that_exists_cannot_be_inserted_again(conn, verb):
    e = raised_incident(conn)
    before = incident_row(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"{verb} alert_incidents ({INCIDENT_COLS}) VALUES (?, ?, 'gate:orderpath-mismatch', 'P0', ?, "
                     "'2026-11-23T15:00:00Z', 0, NULL, 0)", (MON, BOOK, e))
    assert incident_row(conn) == before and count(conn, "alert_incidents") == 1


@pytest.mark.parametrize("verb", ["INSERT OR REPLACE INTO", "REPLACE INTO", "INSERT OR IGNORE INTO"])
def test_an_incident_id_that_exists_cannot_be_inserted_again(conn, verb):
    e = raised_incident(conn)
    before = incident_row(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"{verb} alert_incidents (incident_id, {INCIDENT_COLS}) VALUES (?, ?, ?, 'gate:other', 'P0', ?, "
                     "'2026-11-23T15:00:00Z', 0, NULL, 0)", (before[0], MON, BOOK, e))
    assert incident_row(conn) == before and count(conn, "alert_incidents") == 1


def test_an_incident_upsert_on_its_key_is_refused_and_changes_nothing(conn):
    e = raised_incident(conn)
    before = incident_row(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"INSERT INTO alert_incidents ({INCIDENT_COLS}) VALUES (?, ?, 'gate:orderpath-mismatch', 'P0', ?, "
                     "'2026-11-23T15:00:00Z', 0, NULL, 0) ON CONFLICT (trade_date, book, root_cause) DO UPDATE SET sent_count = 0", (MON, BOOK, e))
    assert incident_row(conn) == before


def test_a_new_incident_key_still_goes_in_by_every_insert_form(conn):
    e = raw_event(conn)
    for n, verb in enumerate(["INSERT INTO", "INSERT OR REPLACE INTO", "REPLACE INTO", "INSERT OR IGNORE INTO"]):
        conn.execute(f"{verb} alert_incidents ({INCIDENT_COLS}) VALUES (?, ?, ?, 'P2', ?, '2026-11-23T15:00:00Z', 1, NULL, 0)",
                     (MON, BOOK, f"gate:new-{n}", e))
    assert count(conn, "alert_incidents") == 4


def test_apply_raises_an_existing_incident_without_replacing_it(conn):
    add(conn, "SKIP", "0655", "SKIPPED", T0655, root_cause="gate:x")
    o = rl.decide(rl.events_for(conn, MON, BOOK), [])[0]
    rl.apply(conn, o, FakeSender(), T0655)
    before = incident_row(conn)
    rl.apply(conn, o, FakeSender(), T0710)                                          # the same alert again: an update, no insert
    after = incident_row(conn)
    assert after[:7] == before[:7] and after[7] == before[7] + 1 and count(conn, "alert_incidents") == 1


def test_the_python_code_creates_incidents_with_a_plain_insert_only():
    src = Path(rl.__file__).read_text()
    assert "ON CONFLICT" not in src and "INSERT OR" not in src and "REPLACE INTO" not in src.replace("run_events_no_replace", "")


ATTEMPT_COLS = "trade_date, book, root_cause, event_id, ok, error, at_utc"


def raw_attempt(conn, event_id, ok=0, **over):
    row = dict(trade_date=MON, book=BOOK, root_cause="gate:x", event_id=event_id, ok=ok, error="down",
               at_utc="2026-11-23T14:55:00Z")
    row.update(over)
    cols = ", ".join(row)
    return conn.execute(f"INSERT INTO send_attempts ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values())).lastrowid


@pytest.mark.parametrize("verb", ["INSERT OR REPLACE INTO", "REPLACE INTO", "INSERT OR IGNORE INTO", "INSERT INTO"])
def test_a_failed_attempt_cannot_be_replaced_by_a_success(conn, verb):
    e = raw_event(conn)
    a = raw_attempt(conn, e)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"{verb} send_attempts (attempt_id, {ATTEMPT_COLS}) VALUES (?, ?, ?, 'gate:x', ?, 1, NULL, '2026-11-23T14:56:00Z')",
                     (a, MON, BOOK, e))
    assert [tuple(r) for r in conn.execute("SELECT ok, error FROM send_attempts")] == [(0, "down")]


def test_an_attempt_upsert_on_its_id_is_refused(conn):
    e = raw_event(conn)
    a = raw_attempt(conn, e)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"INSERT INTO send_attempts (attempt_id, {ATTEMPT_COLS}) VALUES (?, ?, ?, 'gate:x', ?, 1, NULL, '2026-11-23T14:56:00Z') "
                     "ON CONFLICT (attempt_id) DO UPDATE SET ok = 1", (a, MON, BOOK, e))
    assert conn.execute("SELECT ok FROM send_attempts").fetchone()[0] == 0


def test_a_replace_cannot_reset_the_five_try_cap(conn):
    s = FakeSender(always=True)
    add(conn, "SKIP", "0655", "SKIPPED", T0655, root_cause="gate:x")
    for _ in range(5):
        notify(conn, s, T0655)
    first = conn.execute("SELECT attempt_id, event_id FROM send_attempts ORDER BY attempt_id").fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"INSERT OR REPLACE INTO send_attempts (attempt_id, {ATTEMPT_COLS}) VALUES (?, ?, ?, 'gate:x', ?, 1, NULL, "
                     "'2026-11-23T15:00:00Z')", (first[0], MON, BOOK, first[1]))
    notify(conn, s, T0655)
    assert s.calls == 5 and rl.incidents_for(conn, MON, BOOK) == []                   # still given up, and nothing was faked as sent


@pytest.mark.parametrize("table,field", [("run_events", "at_utc"), ("incident", "first_seen_utc"), ("incident", "last_sent_utc"),
                                         ("attempt", "at_utc")])
@pytest.mark.parametrize("stamp,good", [("2026-11-24T24:00:00Z", False), ("2026-11-24T24:59:59Z", False), ("2026-11-24T25:00:00Z", False),
                                        ("2026-11-24T23:59:59Z", True), ("2026-11-24T00:00:00Z", True)])
def test_an_hour_of_24_is_refused_in_every_time_column(conn, table, field, stamp, good):
    e = raw_event(conn)

    def put():
        if table == "run_events":
            raw_event(conn, at_utc=stamp)
        elif table == "incident":
            raw_incident(conn, e, **{field: stamp})
        else:
            raw_attempt(conn, e, at_utc=stamp)
    if good:
        put()
    else:
        with pytest.raises(sqlite3.IntegrityError):
            put()


@pytest.mark.parametrize("table", ["run_events", "incident", "attempt"])
def test_a_blob_root_cause_is_refused(conn, table):
    e = raw_event(conn)
    blob = b"gate:x"
    with pytest.raises(sqlite3.IntegrityError):
        if table == "run_events":
            raw_event(conn, root_cause=blob)
        elif table == "incident":
            raw_incident(conn, e, root_cause=blob)
        else:
            raw_attempt(conn, e, root_cause=blob)
    assert conn.execute("SELECT count(*) FROM run_events WHERE root_cause IS NOT NULL").fetchone()[0] == 0


def test_a_text_or_null_root_cause_is_still_allowed(conn):
    raw_event(conn, root_cause=None)
    raw_event(conn, root_cause="gate:x")
    assert count(conn) == 2


def test_decide_refuses_an_incident_row_without_sent_event_ids():
    ev = [dict(event_id=1, trade_date=MON, book=BOOK, kind="RUN_END", trigger="0655", outcome="FAULT", root_cause="r:x",
               fault_count=1, revision_ok=1, detail=None, at_utc="2026-11-23T14:55:00Z")]
    raw_row = dict(trade_date=MON, book=BOOK, root_cause="r:x", severity="P0", first_event_id=1, sent_count=1, followup_sent=0)
    with pytest.raises(ValueError, match="sent_event_ids"):
        rl.decide(ev, [raw_row])
    assert len(rl.decide(ev, [])) == 1                                                # no incidents at all is fine
    assert rl.decide(ev, [{**raw_row, "sent_event_ids": [1]}]) == []                  # with the field the P0 is known as sent


def test_decide_given_the_rows_incidents_for_reads_does_not_resend_a_p0(conn):
    s = FakeSender()
    run_end(conn, T0655, "FAULT", root_cause="run:status-stopped:send", fault_count=1)
    notify(conn, s, T0655)
    assert rl.decide(rl.events_for(conn, MON, BOOK), rl.incidents_for(conn, MON, BOOK)) == []


# ---- record_event

def test_record_event_stores_the_row_and_the_new_york_date(conn):
    i = add(conn, "RUN_END", "0655", "COMPLETED", T0655, revision_ok=1)
    row = rl.events_for(conn, MON, BOOK)[0]
    assert row["event_id"] == i and row["trade_date"] == MON and row["at_utc"] == "2026-11-23T14:55:00Z"
    assert row["fault_count"] == 0 and row["revision_ok"] == 1 and row["root_cause"] is None


def test_the_trade_date_is_new_york_date_not_utc(conn):
    add(conn, "RUN_START", "0655", "OK", utc(2026, 11, 24, 3, 0))                  # 22:00 on the 23rd in New York
    assert rl.events_for(conn, MON, BOOK) != [] and rl.events_for(conn, "2026-11-24", BOOK) == []


def test_record_event_cleans_the_detail(conn):
    add(conn, "SKIP", "0655", "SKIPPED", T0655, root_cause="gate:x", detail="account 123456789012 refused\nsecond line")
    d = rl.events_for(conn, MON, BOOK)[0]["detail"]
    assert "123456789012" not in d and "<number>" in d and "\n" not in d


def test_record_event_refuses_a_bad_value_and_writes_nothing(conn):
    with pytest.raises(sqlite3.IntegrityError):
        add(conn, "NOPE", "0655", "OK", T0655)
    with pytest.raises(ValueError):
        add(conn, "SKIP", "0655", "SKIPPED", T0655, root_cause="Not A Key")
    with pytest.raises(ValueError):
        add(conn, "SKIP", "0655", "SKIPPED", datetime(2026, 11, 23, 14, 55))        # a clock with no zone
    assert count(conn) == 0


def test_events_are_read_in_order_and_per_book(conn):
    a = add(conn, "RUN_START", "0655", "OK", T0655)
    b = add(conn, "RUN_END", "0655", "COMPLETED", T0655, revision_ok=1)
    add(conn, "RUN_START", "0655", "OK", T0655, book="another-book")
    assert [e["event_id"] for e in rl.events_for(conn, MON, BOOK)] == [a, b]


# ------------------------------------------------------------------------------------------------ the policy

def run_end(conn, now, outcome="COMPLETED", trigger="0655", **kw):
    kw.setdefault("revision_ok", 1)
    return add(conn, "RUN_END", trigger, outcome, now, **kw)


def titles(sender):
    return [t for t, _ in sender.sent]


def texts(sender):
    return [x for _, x in sender.sent]


def test_warn_then_two_skips_of_the_same_cause_send_a_warning_and_one_quiet_followup(conn):
    s = FakeSender()
    add(conn, "PREFLIGHT", "0630", "WARN", T0630, root_cause="gate:orderpath-mismatch")
    notify(conn, s, T0630)
    assert len(s.sent) == 1 and "the 6:55 run will skip: gate:orderpath-mismatch" in s.sent[0][1]
    add(conn, "SKIP", "0655", "SKIPPED", T0655, root_cause="gate:orderpath-mismatch")
    notify(conn, s, T0655)
    assert len(s.sent) == 2 and "skipped as warned; clean-week streak reset to 0" in s.sent[1][1]
    inc = rl.incidents_for(conn, MON, BOOK)
    assert len(inc) == 1 and inc[0]["followup_sent"] == 1 and inc[0]["severity"] == "P2"
    add(conn, "SKIP", "0710", "SKIPPED", T0710, root_cause="gate:orderpath-mismatch")
    notify(conn, s, T0710)
    assert len(s.sent) == 2                                                         # 1 warning, 1 follow-up, 0 more
    notify(conn, s, T0710)
    assert len(s.sent) == 2                                                         # asking again sends nothing again


def test_a_0710_skip_of_a_warned_cause_sends_nothing_even_with_no_0655_row(conn):
    s = FakeSender()
    add(conn, "PREFLIGHT", "0630", "WARN", T0630, root_cause="gate:orderpath-mismatch")
    notify(conn, s, T0630)
    add(conn, "SKIP", "0710", "SKIPPED", T0710, root_cause="gate:orderpath-mismatch")
    notify(conn, s, T0710)
    assert len(s.sent) == 1 and rl.incidents_for(conn, MON, BOOK)[0]["followup_sent"] == 0


def test_a_warning_is_not_sent_twice_when_notify_runs_twice(conn):
    s = FakeSender()
    add(conn, "PREFLIGHT", "0630", "WARN", T0630, root_cause="env:venv-missing")
    notify(conn, s, T0630)
    notify(conn, s, T0630)
    assert len(s.sent) == 1


def test_a_skip_with_a_new_cause_sends_one_p2(conn):
    s = FakeSender()
    add(conn, "SKIP", "0655", "SKIPPED", T0655, root_cause="gate:no-accepted-revision")
    out = notify(conn, s, T0655)
    assert len(s.sent) == 1 and "gate:no-accepted-revision" in s.sent[0][1]
    assert "skipped as warned" not in s.sent[0][1]                                  # no warning came first: not a follow-up
    assert rl.incidents_for(conn, MON, BOOK)[0]["severity"] == "P2" and out["sent"] == 1
    add(conn, "SKIP", "0710", "SKIPPED", T0710, root_cause="gate:no-accepted-revision")
    notify(conn, s, T0710)
    assert len(s.sent) == 1                                                         # the same cause a second time: deduplicated


def test_a_warning_then_a_skip_of_another_cause_sends_the_warning_and_a_new_p2(conn):
    s = FakeSender()
    add(conn, "PREFLIGHT", "0630", "WARN", T0630, root_cause="gate:orderpath-mismatch")
    notify(conn, s, T0630)
    add(conn, "SKIP", "0655", "SKIPPED", T0655, root_cause="env:venv-missing")
    notify(conn, s, T0655)
    assert len(s.sent) == 2 and "env:venv-missing" in s.sent[1][1] and "skipped as warned" not in s.sent[1][1]
    assert {i["root_cause"] for i in rl.incidents_for(conn, MON, BOOK)} == {"gate:orderpath-mismatch", "env:venv-missing"}


def test_two_faults_in_a_day_send_two_p0(conn):
    s = FakeSender()
    run_end(conn, T0655, "FAULT", root_cause="run:status-stopped:send", fault_count=1)
    notify(conn, s, T0655)
    run_end(conn, T0710, "FAULT", trigger="0710", root_cause="run:status-stopped:send", fault_count=1)   # the same cause again
    notify(conn, s, T0710)
    notify(conn, s, T0710)
    assert len(s.sent) == 2                                                         # never deduplicated, never repeated
    inc = rl.incidents_for(conn, MON, BOOK)
    assert len(inc) == 1 and inc[0]["severity"] == "P0" and inc[0]["sent_count"] == 2


def test_two_faults_before_one_notify_send_two_p0(conn):
    s = FakeSender()
    run_end(conn, T0655, "FAULT", root_cause="run:status-stopped:send", fault_count=1)
    run_end(conn, T0710, "CRASH", trigger="0710", root_cause="run:exit-nonzero")
    notify(conn, s, T0710)
    assert len(s.sent) == 2


@pytest.mark.parametrize("outcome,kw", [("FAULT", {}), ("CRASH", {}), ("COMPLETED", {"fault_count": 2}),
                                        ("ORDINARY_STOP", {"fault_count": 1})])
def test_every_kind_of_fault_is_a_p0(conn, outcome, kw):
    s = FakeSender()
    run_end(conn, T0655, outcome, **kw)
    notify(conn, s, T0655)
    assert len(s.sent) == 1 and rl.incidents_for(conn, MON, BOOK)[0]["severity"] == "P0"


@pytest.mark.parametrize("outcome,kw", [("COMPLETED", {}), ("ORDINARY_STOP", {"root_cause": "run:ordinary-stop:session"})])
def test_a_clean_end_sends_nothing(conn, outcome, kw):
    s = FakeSender()
    add(conn, "RUN_START", "0655", "OK", T0655)
    run_end(conn, T0655, outcome, **kw)
    add(conn, "PREFLIGHT", "0630", "OK", T0630)
    notify(conn, s, T0710)
    assert s.sent == [] and rl.incidents_for(conn, MON, BOOK) == []


def test_a_p2_then_a_p0_sends_the_p0(conn):
    s = FakeSender()
    add(conn, "SKIP", "0655", "SKIPPED", T0655, root_cause="gate:orderpath-mismatch")
    notify(conn, s, T0655)
    run_end(conn, T0710, "FAULT", trigger="0710", root_cause="run:status-stopped:send", fault_count=1)
    notify(conn, s, T0710)
    assert len(s.sent) == 2 and "run:status-stopped:send" in s.sent[1][1]
    assert s.sent[1][0] != s.sent[0][0]


def test_a_p0_with_the_cause_of_an_already_sent_p2_is_still_sent(conn):
    s = FakeSender()
    add(conn, "SKIP", "0655", "SKIPPED", T0655, root_cause="run:shared")
    notify(conn, s, T0655)
    run_end(conn, T0710, "FAULT", trigger="0710", root_cause="run:shared", fault_count=1)
    notify(conn, s, T0710)
    assert len(s.sent) == 2


def test_a_holiday_sends_nothing(conn):
    s = FakeSender()
    thanksgiving = utc(2026, 11, 26, 14, 30)
    add(conn, "PREFLIGHT", "0630", "OK", thanksgiving, root_cause="calendar:holiday")
    add(conn, "SKIP", "0655", "SKIPPED", thanksgiving, root_cause="calendar:holiday")
    run_end(conn, thanksgiving, "ORDINARY_STOP", root_cause="calendar:holiday")
    out = notify(conn, s, thanksgiving)
    assert s.sent == [] and out["sent"] == 0 and rl.incidents_for(conn, "2026-11-26", BOOK) == []
    assert count(conn) == 3                                                         # logged, not sent


def test_the_dead_man_firing_twice_sends_once(conn):
    s = FakeSender()
    assert rl.deadman(conn, BOOK, T1025) is not None
    notify(conn, s, T1025)
    assert len(s.sent) == 1 and "no terminal row" in s.sent[0][1]
    assert rl.deadman(conn, BOOK, utc(2026, 11, 23, 16, 0)) is None                 # idempotent: nothing new recorded
    notify(conn, s, utc(2026, 11, 23, 16, 0))
    assert len(s.sent) == 1
    assert rl.incidents_for(conn, MON, BOOK)[0]["severity"] == "P1"


def test_two_dead_man_missing_events_still_send_once(conn):
    # even if a second one were written (here by hand), the day's single P1 is not repeated
    s = FakeSender()
    for at in ("2026-11-23T15:25:00Z", "2026-11-23T15:40:00Z"):
        raw_event(conn, kind="DEADMAN", trigger="0725", outcome="MISSING", root_cause="deadman:no-terminal-row",
                  revision_ok=None, at_utc=at)
    notify(conn, s, T1025)
    assert len(s.sent) == 1


def test_a_dead_man_ok_sends_nothing(conn):
    s = FakeSender()
    run_end(conn, T0655)
    rl.deadman(conn, BOOK, T1025)
    notify(conn, s, T1025)
    assert s.sent == []


def test_a_p2_then_the_dead_man_sends_the_p1(conn):
    s = FakeSender()
    add(conn, "PREFLIGHT", "0630", "WARN", T0630, root_cause="gate:x")               # a warning is no terminal row
    notify(conn, s, T0630)
    rl.deadman(conn, BOOK, T1025)
    notify(conn, s, T1025)
    assert len(s.sent) == 2 and "no terminal row" in s.sent[1][1] and s.sent[0][0] != s.sent[1][0]
    assert {i["severity"] for i in rl.incidents_for(conn, MON, BOOK)} == {"P1", "P2"}


def test_another_book_and_another_day_are_judged_apart(conn):
    s = FakeSender()
    add(conn, "SKIP", "0655", "SKIPPED", T0655, root_cause="gate:x")
    add(conn, "SKIP", "0655", "SKIPPED", T0655, root_cause="gate:x", book="another-book")
    notify(conn, s, T0655)
    notify(conn, s, T0655, book="another-book")
    assert len(s.sent) == 2
    add(conn, "SKIP", "0655", "SKIPPED", utc(2026, 11, 24, 14, 55), root_cause="gate:x")
    notify(conn, s, utc(2026, 11, 24, 14, 55))
    assert len(s.sent) == 3                                                         # the next day is a new incident


def test_the_notify_of_a_day_does_not_resend_yesterdays_fault(conn):
    s = FakeSender()
    run_end(conn, T0655, "FAULT", root_cause="run:status-stopped:send", fault_count=1)
    notify(conn, s, T0655)
    notify(conn, s, utc(2026, 11, 24, 14, 55))
    assert len(s.sent) == 1


def test_decide_is_pure_and_works_on_plain_rows():
    ev = [dict(event_id=1, trade_date=MON, book=BOOK, kind="SKIP", trigger="0655", outcome="SKIPPED", root_cause="gate:x",
               fault_count=0, revision_ok=None, detail=None, at_utc="2026-11-23T14:55:00Z")]
    before = json.dumps(ev)
    out = rl.decide(ev, [])
    assert len(out) == 1 and out[0].severity == "P2" and out[0].root_cause == "gate:x" and out[0].event_id == 1
    assert json.dumps(ev) == before                                                 # nothing changed
    assert rl.decide(ev, []) == out                                                 # the same answer twice
    sent = [dict(trade_date=MON, book=BOOK, root_cause="gate:x", severity="P2", first_event_id=1, sent_count=1,
                 followup_sent=0, sent_event_ids=[1])]
    assert rl.decide(ev, sent) == []                                                # sent already: nothing owed


def test_decide_of_nothing_is_nothing():
    assert rl.decide([], []) == []


def test_decide_does_no_io(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("decide touched the outside")
    ev = [dict(event_id=1, trade_date=MON, book=BOOK, kind="RUN_END", trigger="0655", outcome="FAULT", root_cause="r:x",
               fault_count=1, revision_ok=1, detail=None, at_utc="2026-11-23T14:55:00Z")]
    with monkeypatch.context() as m:
        m.setattr(sqlite3, "connect", boom)
        m.setattr(subprocess, "run", boom)
        m.setattr(socket, "socket", boom)
        m.setattr("builtins.open", boom)
        out = rl.decide(ev, [])
    assert len(out) == 1


# ---- sending and its failures

def test_a_failed_send_is_recorded_and_not_counted_as_sent(conn):
    s = FakeSender(always=True)
    add(conn, "SKIP", "0655", "SKIPPED", T0655, root_cause="gate:x")
    out = notify(conn, s, T0655)
    assert out["sent"] == 0 and out["failed"] == 1
    assert rl.incidents_for(conn, MON, BOOK) == []
    rows = conn.execute("SELECT ok, error FROM send_attempts").fetchall()
    assert len(rows) == 1 and rows[0][0] == 0 and "down" in rows[0][1]


def test_a_failed_send_is_retried_at_most_five_times_in_all(conn):
    s = FakeSender(always=True)
    add(conn, "SKIP", "0655", "SKIPPED", T0655, root_cause="gate:x")
    outs = [notify(conn, s, T0655) for _ in range(8)]
    assert s.calls == 5                                                             # 5 tries in all, never a 6th
    assert [o["failed"] for o in outs] == [1] * 5 + [0] * 3
    assert [o["gave_up"] for o in outs] == [0] * 5 + [1] * 3
    assert count(conn, "send_attempts") == 5 and rl.incidents_for(conn, MON, BOOK) == []


def test_a_send_that_works_on_a_retry_is_recorded_once(conn):
    s = FakeSender(fail=2)
    add(conn, "SKIP", "0655", "SKIPPED", T0655, root_cause="gate:x")
    for _ in range(5):
        notify(conn, s, T0655)
    assert s.calls == 3 and len(s.sent) == 1                                        # two failures, one success, then quiet
    inc = rl.incidents_for(conn, MON, BOOK)
    assert len(inc) == 1 and inc[0]["sent_count"] == 1
    assert [r[0] for r in conn.execute("SELECT ok FROM send_attempts ORDER BY attempt_id")] == [0, 0, 1]


def test_the_retry_count_is_per_alert_not_per_day(conn):
    s = FakeSender(always=True)
    add(conn, "SKIP", "0655", "SKIPPED", T0655, root_cause="gate:x")
    for _ in range(5):
        notify(conn, s, T0655)
    add(conn, "SKIP", "0710", "SKIPPED", T0710, root_cause="gate:y")                # a new alert has its own five
    for _ in range(6):
        notify(conn, s, T0710)
    assert s.calls == 5 + 5


def test_a_p0_that_failed_is_retried_and_a_later_p0_is_separate(conn):
    s = FakeSender(fail=1)
    run_end(conn, T0655, "FAULT", root_cause="run:status-stopped:send", fault_count=1)
    notify(conn, s, T0655)
    run_end(conn, T0710, "FAULT", trigger="0710", root_cause="run:status-stopped:send", fault_count=1)
    notify(conn, s, T0710)
    notify(conn, s, T0710)
    assert len(s.sent) == 2                                                         # the first retried, the second sent


def test_apply_returns_what_happened(conn):
    add(conn, "SKIP", "0655", "SKIPPED", T0655, root_cause="gate:x")
    o = rl.decide(rl.events_for(conn, MON, BOOK), [])[0]
    assert rl.apply(conn, o, FakeSender(always=True), T0655) == "failed"
    assert rl.apply(conn, o, FakeSender(), T0655) == "sent"


def test_the_mac_adapter_gives_the_notifier_an_alert():
    seen = []

    class Notifier:
        def send(self, alert):
            seen.append(alert)

    rl.MacSender(Notifier()).send("a title", "some text")
    assert len(seen) == 1 and seen[0].title == "a title" and seen[0].detail == "some text"


def test_the_mac_adapter_passes_a_failure_up():
    class Notifier:
        def send(self, alert):
            raise RuntimeError("osascript exited with code 1")

    with pytest.raises(RuntimeError):
        rl.MacSender(Notifier()).send("t", "x")


def test_the_mac_adapter_uses_the_alerts_module_notifier_by_default():
    from valedia_trader.core.alerts import MacNotifier
    assert isinstance(rl.MacSender()._notifier, MacNotifier)


# ------------------------------------------------------------------------------------------------ the streak

WEEK = ["2026-11-24", "2026-11-25", "2026-11-27", "2026-11-30", "2026-12-01"]      # Thanksgiving, the 26th, is closed


def ev(day, kind="RUN_END", outcome="COMPLETED", trigger="0655", fault_count=0, revision_ok=1, root_cause=None, eid=[0]):
    eid[0] += 1
    return dict(event_id=eid[0], trade_date=day, book=BOOK, kind=kind, trigger=trigger, outcome=outcome,
                root_cause=root_cause, fault_count=fault_count, revision_ok=revision_ok, detail=None, at_utc=day + "T14:55:00Z")


def clean_day(day):
    return [ev(day, "RUN_START", "OK"), ev(day)]


def week(**override):
    """The five clean days; `override` maps a day to its own list of events."""
    out = []
    for d in WEEK:
        out += override.get(d, clean_day(d))
    return out


def test_the_calendar_sessions_of_the_example_week():
    got = rl.sessions_between("2026-11-24", "2026-12-01")
    assert got == [date.fromisoformat(d) for d in WEEK]
    assert rl.is_session("2026-11-26") is False and rl.is_session("2026-11-25") is True
    assert rl.is_session("2026-11-28") is False                                      # a Saturday


def test_five_clean_sessions_with_thanksgiving_paused_reach_the_target():
    sessions = rl.sessions_between("2026-11-24", "2026-12-01")
    count_, per_day = rl.streak(week(), sessions)
    assert count_ == 5 and rl.CLEAN_SESSIONS_NEEDED == 5 and rl.target_met(count_) is True
    by = {d["date"]: d for d in per_day}
    assert by["2026-11-26"]["status"] == "paused" and by["2026-11-26"]["count"] == 2     # the count is kept, not reset
    assert [by[d]["count"] for d in WEEK] == [1, 2, 3, 4, 5]
    assert all(by[d]["status"] == "clean" for d in WEEK)
    assert "2026-11-28" not in by and "2026-11-29" not in by                         # a weekend is not shown at all


def test_four_clean_sessions_do_not_reach_the_target():
    count_, _ = rl.streak(week(**{"2026-12-01": []}), rl.sessions_between("2026-11-24", "2026-11-30"))
    assert count_ == 4 and rl.target_met(count_) is False
    assert rl.target_met(5) is True and rl.target_met(4) is False and rl.target_met(6) is True


def test_a_holiday_never_resets_even_with_a_stray_row():
    events = week() + [ev("2026-11-26", "SKIP", "SKIPPED", root_cause="calendar:holiday")]
    count_, per_day = rl.streak(events, rl.sessions_between("2026-11-24", "2026-12-01"))
    assert count_ == 5 and {d["date"]: d["status"] for d in per_day}["2026-11-26"] == "paused"


def test_a_skip_resets_to_zero_and_the_count_starts_again():
    events = week(**{"2026-11-25": [ev("2026-11-25", "SKIP", "SKIPPED", root_cause="gate:orderpath-mismatch")]})
    count_, per_day = rl.streak(events, rl.sessions_between("2026-11-24", "2026-12-01"))
    by = {d["date"]: d for d in per_day}
    assert by["2026-11-25"]["status"] == "reset" and by["2026-11-25"]["count"] == 0 and "skip" in by["2026-11-25"]["reason"]
    assert count_ == 3 and not rl.target_met(count_)                                 # the 27th, 30th and 1st


def test_a_skip_on_the_last_day_leaves_zero():
    events = week(**{"2026-12-01": [ev("2026-12-01", "SKIP", "SKIPPED", trigger="0710", root_cause="env:venv-missing")]})
    count_, _ = rl.streak(events, rl.sessions_between("2026-11-24", "2026-12-01"))
    assert count_ == 0 and not rl.target_met(count_)


def test_a_revision_mismatch_resets():
    bad = [ev("2026-11-25", "RUN_START", "OK", revision_ok=0), ev("2026-11-25", revision_ok=0)]
    count_, per_day = rl.streak(week(**{"2026-11-25": bad}), rl.sessions_between("2026-11-24", "2026-12-01"))
    by = {d["date"]: d for d in per_day}
    assert by["2026-11-25"]["status"] == "reset" and "revision" in by["2026-11-25"]["reason"] and count_ == 3


def test_a_mismatch_on_a_start_row_alone_resets():
    bad = [ev("2026-11-25", "RUN_START", "OK", revision_ok=0), ev("2026-11-25", revision_ok=1)]
    count_, _ = rl.streak(week(**{"2026-11-25": bad}), rl.sessions_between("2026-11-24", "2026-12-01"))
    assert count_ == 3


def test_an_unknown_revision_is_not_clean():
    unknown = [ev("2026-11-25", revision_ok=None)]
    count_, per_day = rl.streak(week(**{"2026-11-25": unknown}), rl.sessions_between("2026-11-24", "2026-12-01"))
    assert count_ == 3 and {d["date"]: d for d in per_day}["2026-11-25"]["status"] == "reset"


def test_a_missing_terminal_row_resets():
    only_start = [ev("2026-11-25", "RUN_START", "OK")]
    count_, per_day = rl.streak(week(**{"2026-11-25": only_start}), rl.sessions_between("2026-11-24", "2026-12-01"))
    by = {d["date"]: d for d in per_day}
    assert by["2026-11-25"]["status"] == "reset" and "terminal" in by["2026-11-25"]["reason"] and count_ == 3


def test_a_session_day_with_no_rows_at_all_resets():
    count_, _ = rl.streak(week(**{"2026-11-27": []}), rl.sessions_between("2026-11-24", "2026-12-01"))
    assert count_ == 2


@pytest.mark.parametrize("bad", [
    [ev("2026-11-25", outcome="CRASH")],
    [ev("2026-11-25", outcome="FAULT", root_cause="run:status-stopped:send", fault_count=1)],
    [ev("2026-11-25", fault_count=2)],
    [ev("2026-11-25", outcome="FAULT"), ev("2026-11-25", trigger="0710")],              # a fault, then a clean later run
    [ev("2026-11-25"), ev("2026-11-25", "DEADMAN", "MISSING", trigger="0725", revision_ok=None)],
])
def test_a_fault_or_a_crash_resets(bad):
    count_, per_day = rl.streak(week(**{"2026-11-25": bad}), rl.sessions_between("2026-11-24", "2026-12-01"))
    assert {d["date"]: d for d in per_day}["2026-11-25"]["status"] == "reset" and count_ == 3


def test_an_ordinary_stop_on_a_session_day_is_clean():
    stop = [ev("2026-11-25", outcome="ORDINARY_STOP", root_cause="run:ordinary-stop:session")]
    count_, _ = rl.streak(week(**{"2026-11-25": stop}), rl.sessions_between("2026-11-24", "2026-12-01"))
    assert count_ == 5


def test_a_clean_day_with_a_warning_that_did_not_skip_is_clean():
    day = [ev("2026-11-25", "PREFLIGHT", "WARN", trigger="0630", revision_ok=None), *clean_day("2026-11-25")]
    count_, _ = rl.streak(week(**{"2026-11-25": day}), rl.sessions_between("2026-11-24", "2026-12-01"))
    assert count_ == 5


def test_the_streak_of_nothing_is_zero_and_an_empty_week_is_empty():
    assert rl.streak([], []) == (0, [])
    count_, per_day = rl.streak([], rl.sessions_between("2026-11-24", "2026-11-25"))
    assert count_ == 0 and [d["status"] for d in per_day] == ["reset", "reset"]


def test_the_streak_ignores_rows_outside_its_days():
    count_, _ = rl.streak(week() + [ev("2026-11-20", "SKIP", "SKIPPED", root_cause="gate:x")],
                          rl.sessions_between("2026-11-24", "2026-12-01"))
    assert count_ == 5


def test_the_streak_does_not_change_its_input():
    events = week()
    before = json.dumps(events)
    rl.streak(events, rl.sessions_between("2026-11-24", "2026-12-01"))
    assert json.dumps(events) == before


# ------------------------------------------------------------------------------------------------ the pre-flight

def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@localhost",
                           "-c", "commit.gpgsign=false", *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    names = [f"f{i:02d}.py" for i in range(12)]
    for n in names:
        (r / n).write_text(f"x = '{n}'\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "base")
    accepted = tmp_path / "accepted.txt"
    accepted.write_text(git(r, "rev-parse", "HEAD") + "\n")
    files = tmp_path / "files.txt"
    files.write_text("\n".join(names) + "\n")
    return dict(repo=r, accepted_commit_file=accepted, files_file=files, venv_python=Path(sys.executable), now=T0630, names=names)


def pre(r, **over):
    kw = {k: v for k, v in r.items() if k != "names"}
    kw.update(over)
    return rl.preflight(**kw)


def test_a_clean_tree_passes_the_preflight(repo):
    res = pre(repo)
    assert (res.outcome, res.root_cause) == ("OK", None)


def test_an_order_path_edit_warns(repo):
    (repo["repo"] / "f03.py").write_text("x = 'edited'\n")
    res = pre(repo)
    assert (res.outcome, res.root_cause) == ("WARN", "gate:orderpath-mismatch")


def test_a_committed_change_after_the_accepted_commit_warns(repo):
    (repo["repo"] / "f11.py").write_text("x = 'later'\n")
    git(repo["repo"], "commit", "-q", "-am", "later")
    assert pre(repo).root_cause == "gate:orderpath-mismatch"


def test_a_deleted_listed_file_warns(repo):
    (repo["repo"] / "f00.py").unlink()
    assert pre(repo).root_cause == "gate:orderpath-mismatch"


def test_an_edit_to_a_file_not_in_the_list_does_not_warn(repo):
    (repo["repo"] / "unlisted.py").write_text("x = 1\n")
    git(repo["repo"], "add", "unlisted.py")
    assert pre(repo).outcome == "OK"


def test_a_missing_venv_warns(repo, tmp_path):
    res = pre(repo, venv_python=tmp_path / "no-such-python")
    assert (res.outcome, res.root_cause) == ("WARN", "env:venv-missing")


def test_a_venv_that_is_a_directory_warns(repo, tmp_path):
    assert pre(repo, venv_python=tmp_path).root_cause == "env:venv-missing"


def test_an_empty_or_missing_accepted_commit_file_warns(repo, tmp_path):
    repo["accepted_commit_file"].write_text("  \n")
    assert (pre(repo).outcome, pre(repo).root_cause) == ("WARN", "gate:no-accepted-revision")
    assert pre(repo, accepted_commit_file=tmp_path / "gone.txt").root_cause == "gate:no-accepted-revision"


@pytest.mark.parametrize("junk", ["--output=/x", "-p", "not a sha", "HEAD", "deadbeef\nsecond", "g" * 40])
def test_a_commit_that_is_not_a_hash_warns_and_is_never_passed_to_git(repo, tmp_path, monkeypatch, junk):
    repo["accepted_commit_file"].write_text(junk + "\n")
    calls = []
    real = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a) or real(*a, **k))
    res = pre(repo)
    assert res.root_cause == "gate:no-accepted-revision"
    assert not any("diff" in c[0] for c in calls)


def test_a_commit_that_git_does_not_know_warns(repo):
    repo["accepted_commit_file"].write_text("0123456789abcdef0123456789abcdef01234567\n")
    res = pre(repo)
    assert res.outcome == "WARN" and res.root_cause == "gate:git-error"


def test_a_files_list_with_fewer_than_ten_entries_warns(repo):
    repo["files_file"].write_text("\n".join(repo["names"][:9]) + "\n")
    res = pre(repo)
    assert (res.outcome, res.root_cause) == ("WARN", "gate:files-list-short")


def test_ten_entries_are_enough_and_blank_lines_do_not_count(repo):
    repo["files_file"].write_text("\n\n".join(repo["names"][:10]) + "\n\n   \n")
    assert pre(repo).outcome == "OK"
    repo["files_file"].write_text("\n\n".join(repo["names"][:9]) + "\n\n   \n")
    assert pre(repo).root_cause == "gate:files-list-short"


def test_duplicate_entries_count_once_toward_the_minimum(repo):
    repo["files_file"].write_text("\n".join(repo["names"][:5] * 3) + "\n")                  # 15 lines, 5 different files
    res = pre(repo)
    assert (res.outcome, res.root_cause) == ("WARN", "gate:files-list-short") and "5 " in res.detail
    repo["files_file"].write_text("\n".join(repo["names"][:10] + repo["names"][:3]) + "\n")   # 13 lines, 10 different files
    assert pre(repo).outcome == "OK"


def test_a_missing_files_list_warns(repo, tmp_path):
    assert pre(repo, files_file=tmp_path / "gone.txt").root_cause == "gate:files-list-short"


def test_the_first_failure_wins(repo, tmp_path):
    (repo["repo"] / "f03.py").write_text("x = 'edited'\n")
    repo["accepted_commit_file"].write_text("\n")
    repo["files_file"].write_text("a\n")
    assert pre(repo).root_cause == "gate:no-accepted-revision"                         # before files and the diff
    assert pre(repo, venv_python=tmp_path / "no").root_cause == "env:venv-missing"     # before all of them
    repo["accepted_commit_file"].write_text(git(repo["repo"], "rev-parse", "HEAD") + "\n")
    assert pre(repo).root_cause == "gate:files-list-short"                             # before the diff


def test_a_holiday_is_ok_with_the_holiday_cause(repo):
    res = pre(repo, now=utc(2026, 11, 26, 14, 30))
    assert (res.outcome, res.root_cause) == ("OK", "calendar:holiday")


def test_a_weekend_is_a_holiday_too(repo):
    assert pre(repo, now=utc(2026, 11, 28, 14, 30)).root_cause == "calendar:holiday"


def test_a_gate_failure_on_a_holiday_still_warns(repo):
    (repo["repo"] / "f03.py").write_text("x = 'edited'\n")
    assert pre(repo, now=utc(2026, 11, 26, 14, 30)).root_cause == "gate:orderpath-mismatch"


def test_the_preflight_runs_git_as_an_argument_list_and_makes_no_network_call(repo, monkeypatch):
    calls = []
    real = subprocess.run

    def spy(*a, **k):
        calls.append((a, k))
        return real(*a, **k)

    def no_network(*a, **k):
        raise AssertionError("a network call")
    monkeypatch.setattr(subprocess, "run", spy)
    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    assert pre(repo).outcome == "OK"
    argv, kw = calls[-1][0][0], calls[-1][1]
    assert isinstance(argv, list) and argv[:3] == ["git", "-C", str(repo["repo"])] and "diff" in argv and "--quiet" in argv
    assert argv[argv.index("--") + 1:] == repo["names"] and not kw.get("shell")
    assert kw["env"]["GIT_LITERAL_PATHSPECS"] == "1" and kw["env"]["GIT_TERMINAL_PROMPT"] == "0" and "PATH" in kw["env"]


def test_filenames_with_shell_characters_are_data_not_commands(repo, tmp_path):
    marker = tmp_path / "pwned"
    repo["files_file"].write_text("\n".join(repo["names"]) + f"\n$(touch {marker}).py\n; touch {marker}\n")
    pre(repo)
    assert not marker.exists()


def test_the_preflight_result_can_be_recorded(conn, repo):
    res = pre(repo)
    add(conn, "PREFLIGHT", "0630", res.outcome, T0630, root_cause=res.root_cause, detail=res.detail)
    assert rl.events_for(conn, MON, BOOK)[0]["outcome"] == "OK"


# ------------------------------------------------------------------------------------------------ record-run

def summary(status="done", stage=None, **extra):
    return json.dumps({"status": status, "stage": stage, "reason": extra.pop("reason", None), "venue": "v", **extra}, indent=2)


@pytest.mark.parametrize("text,code,outcome,cause,faults", [
    (summary("done"), 0, "COMPLETED", None, 0),
    (summary("done", faults=["reconcile", "cap"]), 0, "COMPLETED", "run:faults:reconcile", 2),
    (summary("stopped", "session", reason="outside the regular session window 09:30 to 16:00 New York time"), 2,
     "NOOP", "run:noop:outside-window", 0),
    (summary("stopped", "session", reason="another order step is running on this venue's state file"), 2,
     "NOOP", "run:noop:lock-held", 0),
    (summary("stopped", "session", reason="2026-11-22 (Sunday) is not a trading day"), 2,
     "ORDINARY_STOP", "run:ordinary-stop:session", 0),
    (summary("stopped", "session"), 2, "ORDINARY_STOP", "run:ordinary-stop:session", 0),
    (summary("stopped", "session", reason="the exchange calendar could not be loaded: boom"), 2,
     "FAULT", "run:status-stopped:session", 1),
    (summary("stopped", "send", reason="the venue refused"), 2, "FAULT", "run:status-stopped:send", 1),
    (summary("stopped", "reconcile"), 2, "FAULT", "run:status-stopped:reconcile", 1),
    (summary("stopped", "unexpected"), 2, "FAULT", "run:status-stopped:unexpected", 1),
    (summary("stopped", "lock"), 2, "FAULT", "run:status-stopped:lock", 1),
    (summary("stopped", None), 2, "FAULT", "run:status-stopped:unknown", 1),
    (summary("stopped", "Odd Stage!"), 2, "FAULT", "run:status-stopped:unknown", 1),
    (summary("stopped", "send", faults=["reconcile"]), 2, "FAULT", "run:status-stopped:send", 1),
    (summary("done"), 1, "CRASH", "run:exit-nonzero", 0),
    (summary("done"), 2, "CRASH", "run:exit-nonzero", 0),
    (summary("stopped", "send"), 1, "CRASH", "run:exit-nonzero", 0),
    (summary("stopped", "send"), 137, "CRASH", "run:exit-nonzero", 0),
    (summary("stopped", "session"), 0, "CRASH", "run:exit-mismatch", 0),
    ("", 1, "CRASH", "run:exit-nonzero", 0),
    ("", 2, "CRASH", "run:exit-nonzero", 0),
    ("Traceback (most recent call last):\n  boom\n", 1, "CRASH", "run:exit-nonzero", 0),
    ("", 0, "CRASH", "run:summary-unreadable", 0),
    ("not json at all", 0, "CRASH", "run:summary-unreadable", 0),
    ("[1, 2, 3]", 0, "CRASH", "run:summary-unreadable", 0),
    ('{"status": "weird"}', 0, "CRASH", "run:summary-unreadable", 0),
    ('{"stage": "send"}', 0, "CRASH", "run:summary-unreadable", 0),
    ('{"status": "done"', 0, "CRASH", "run:summary-unreadable", 0),
])
def test_record_run_maps_each_status_stage_and_exit_case(text, code, outcome, cause, faults):
    m = rl.map_run(text, code)
    assert (m.outcome, m.root_cause, m.fault_count) == (outcome, cause, faults)


def test_only_the_session_stage_is_ordinary():
    for stage in ("after", "baseline", "live", "lock", "reconcile", "send", "state", "targets", "ticket", "unexpected",
                  "prices", "marks", "shortfall"):
        assert rl.map_run(summary("stopped", stage), 2).outcome == "FAULT", stage
    assert rl.map_run(summary("stopped", "session", reason="outside the regular session window"), 2).outcome == "NOOP"


def test_the_real_printed_form_with_a_ticket_line_after_it_is_read():
    text = summary("done") + "\nticket number: 12345 (approve within 10 minutes with: order-step --approve NUMBER)\n"
    assert rl.map_run(text, 0).outcome == "COMPLETED"
    one_line = json.dumps({"status": "stopped", "stage": "session", "reason": "closed"})
    assert rl.map_run(one_line, 2).outcome == "ORDINARY_STOP"
    assert rl.map_run("warning: something\n" + summary("done"), 0).outcome == "COMPLETED"


def test_record_run_detail_is_clean_short_and_has_no_ids():
    m = rl.map_run(summary("stopped", "send", reason="refused for account 123456789012\nwith more words " + "w " * 400), 2)
    assert "123456789012" not in m.detail and "\n" not in m.detail and len(m.detail) <= 300
    assert "send" in m.detail


def test_a_huge_summary_is_still_read():
    big = summary("done", sent=[{"symbol": "S%d" % i} for i in range(20000)])
    assert rl.map_run(big, 0).outcome == "COMPLETED"


# ------------------------------------------------------------------------------------------------ the dead-man

def test_before_1025_new_york_nothing_is_recorded(conn):
    assert rl.deadman(conn, BOOK, T1024) is None
    assert count(conn) == 0


def test_at_1025_without_a_terminal_row_it_is_missing(conn):
    i = rl.deadman(conn, BOOK, T1025)
    row = rl.events_for(conn, MON, BOOK)[0]
    assert row["event_id"] == i and (row["kind"], row["outcome"], row["trigger"]) == ("DEADMAN", "MISSING", "0725")
    assert row["root_cause"] == "deadman:no-terminal-row"


@pytest.mark.parametrize("kind,trigger,outcome", [
    ("RUN_END", "0655", "COMPLETED"), ("RUN_END", "0710", "FAULT"), ("RUN_END", "MANUAL", "COMPLETED"),
    ("RUN_END", "0655", "ORDINARY_STOP"), ("SKIP", "0655", "SKIPPED"), ("SKIP", "0710", "SKIPPED"),
])
def test_with_a_terminal_row_it_is_ok(conn, kind, trigger, outcome):
    add(conn, kind, trigger, outcome, T0655)
    rl.deadman(conn, BOOK, T1025)
    row = [e for e in rl.events_for(conn, MON, BOOK) if e["kind"] == "DEADMAN"]
    assert len(row) == 1 and row[0]["outcome"] == "OK" and row[0]["root_cause"] is None


@pytest.mark.parametrize("kind,trigger,outcome", [
    ("RUN_START", "0655", "OK"), ("PREFLIGHT", "0630", "WARN"), ("SKIP", "0630", "SKIPPED"), ("SKIP", "MANUAL", "SKIPPED"),
    ("SKIP", "0725", "SKIPPED"), ("RUN_END", "0710", "NOOP"), ("RUN_END", "0655", "NOOP"),
])
def test_a_row_that_is_not_terminal_does_not_count(conn, kind, trigger, outcome):
    add(conn, kind, trigger, outcome, T0655)
    rl.deadman(conn, BOOK, T1025)
    # (a RUN_START that old is also an orphan; that row is not the no-terminal-row check, which is the one asked here)
    assert [e["outcome"] for e in rl.events_for(conn, MON, BOOK)
            if e["kind"] == "DEADMAN" and e["outcome"] in ("OK", "MISSING")] == ["MISSING"]


def test_a_terminal_row_of_another_day_or_book_does_not_count(conn):
    add(conn, "RUN_END", "0655", "COMPLETED", utc(2026, 11, 20, 14, 55))
    add(conn, "RUN_END", "0655", "COMPLETED", T0655, book="another-book")
    rl.deadman(conn, BOOK, T1025)
    assert rl.events_for(conn, MON, BOOK)[0]["outcome"] == "MISSING"


def test_on_a_holiday_it_records_nothing(conn):
    assert rl.deadman(conn, BOOK, utc(2026, 11, 26, 16, 0)) is None
    assert rl.deadman(conn, BOOK, utc(2026, 11, 28, 16, 0)) is None                    # a Saturday
    assert count(conn) == 0


def test_the_dead_man_is_idempotent_per_day_and_book(conn):
    first = rl.deadman(conn, BOOK, T1025)
    assert rl.deadman(conn, BOOK, utc(2026, 11, 23, 16, 0)) is None and count(conn) == 1
    assert rl.deadman(conn, "another-book", T1025) is not None and count(conn) == 2   # another book is its own day
    assert rl.deadman(conn, BOOK, utc(2026, 11, 24, 15, 30)) is not None and count(conn) == 3   # the next day too
    assert first is not None


def test_a_terminal_row_that_arrives_after_the_dead_man_does_not_rewrite_it(conn):
    rl.deadman(conn, BOOK, T1025)
    run_end(conn, utc(2026, 11, 23, 16, 0))
    assert rl.deadman(conn, BOOK, utc(2026, 11, 23, 16, 5)) is None
    assert [e["outcome"] for e in rl.events_for(conn, MON, BOOK) if e["kind"] == "DEADMAN"] == ["MISSING"]


# ------------------------------------------------------------------------------------------------ no-op stops and orphaned starts (T60)

ORDER_STEP = Path(rl.__file__).resolve().parents[1] / "execution_and_risk" / "order_step.py"
T0725 = T1025                                          # the dead-man job: 7:25 Pacific is 10:25 New York


def order_step_session_reasons():
    """The literal reasons the order step stops at the `session` stage with, read from its source (never edited here)."""
    src = ORDER_STEP.read_text()
    lock = re.search(r'_finish\("stopped", "session", f?"([^"{]*)', src)
    calendar = re.search(r'_Stop\("session", f"(the exchange calendar could not be loaded[^"{]*)', src)
    window = re.search(r'_Stop\("session", f"(outside the regular session window[^"{]*)', src)
    assert lock and calendar and window, "the order step no longer words a session stop the way the ledger reads it"
    return lock.group(1), calendar.group(1), window.group(1)


def test_the_reason_prefixes_the_ledger_reads_are_the_order_steps_exact_words():
    src = ORDER_STEP.read_text()
    for prefix in (rl.LOCK_HELD_REASON, rl.OUTSIDE_WINDOW_REASON, rl.CALENDAR_FAULT_REASON):
        assert f'"{prefix}' in src, f"{prefix!r} is no longer how the order step words a session stop"
    assert (rl.LOCK_HELD_REASON, rl.OUTSIDE_WINDOW_REASON, rl.CALENDAR_FAULT_REASON) == (
        "another order step is running", "outside the regular session window", "the exchange calendar could not be loaded")
    assert f'f"{rl.CALENDAR_ANSWER_REASON} {{day}}' in src, "market_window's calendar-error wording changed"
    assert rl.CALENDAR_ANSWER_REASON == "the exchange calendar could not answer for"


def test_the_order_steps_own_session_reasons_map_as_the_ticket_says():
    lock, calendar, window = order_step_session_reasons()
    got = {name: rl.map_run(summary("stopped", "session", reason=text), 2)
           for name, text in (("lock", lock), ("calendar", calendar + ": boom"), ("window", window + " 09:30 to 15:55 (it is 06:55)"))}
    assert (got["lock"].outcome, got["lock"].root_cause) == ("NOOP", "run:noop:lock-held")
    assert (got["window"].outcome, got["window"].root_cause) == ("NOOP", "run:noop:outside-window")
    assert (got["calendar"].outcome, got["calendar"].root_cause, got["calendar"].fault_count) == ("FAULT", "run:status-stopped:session", 1)


@pytest.mark.parametrize("reason,outcome,cause", [
    ("the market is closed today", "ORDINARY_STOP", "run:ordinary-stop:session"),
    ("2026-11-28 (Saturday) is not a trading day", "ORDINARY_STOP", "run:ordinary-stop:session"),
    ("the exchange calendar could not answer for 2026-11-28: boom", "FAULT", "run:status-stopped:session"),
    ("", "ORDINARY_STOP", "run:ordinary-stop:session"),
    ("Another order step is running", "ORDINARY_STOP", "run:ordinary-stop:session"),               # the words are matched exactly
    ("then another order step is running", "ORDINARY_STOP", "run:ordinary-stop:session"),          # at the start of the reason only
    ("a note: outside the regular session window", "ORDINARY_STOP", "run:ordinary-stop:session"),
    ("another order step is running on this venue's state file", "NOOP", "run:noop:lock-held"),
    ("another order step is running", "NOOP", "run:noop:lock-held"),
    ("outside the regular session window 09:30 to 15:55 New York time (it is 06:55)", "NOOP", "run:noop:outside-window"),
    ("the exchange calendar could not be loaded: boom", "FAULT", "run:status-stopped:session"),
])
def test_a_session_stop_maps_by_the_start_of_its_reason(reason, outcome, cause):
    m = rl.map_run(summary("stopped", "session", reason=reason), 2)
    assert (m.outcome, m.root_cause) == (outcome, cause)


@pytest.mark.parametrize("stage", ["send", "lock", "environment", "unexpected"])
def test_a_noop_reason_at_another_stage_is_still_a_fault(stage):
    for reason in ("another order step is running on this venue's state file", "outside the regular session window"):
        m = rl.map_run(summary("stopped", stage, reason=reason), 2)
        assert (m.outcome, m.fault_count) == ("FAULT", 1)


def test_a_noop_stop_with_the_wrong_exit_code_is_still_a_crash():
    text = summary("stopped", "session", reason="another order step is running on this venue's state file")
    assert rl.map_run(text, 1).outcome == "CRASH" and rl.map_run(text, 0).outcome == "CRASH"


def test_a_noop_is_p3_and_a_noop_row_sends_nothing(conn):
    s = FakeSender()
    run_end(conn, T0710, "NOOP", trigger="0710", root_cause="run:noop:lock-held")
    run_end(conn, T0710, "NOOP", trigger="0710", root_cause="run:noop:outside-window")
    assert rl.classify(rl.events_for(conn, MON, BOOK)[0]) == "P3"
    out = notify(conn, s, T0710)
    assert s.sent == [] and out["due"] == [] and rl.incidents_for(conn, MON, BOOK) == []
    assert rl.classify({"kind": "RUN_END", "outcome": "ORDINARY_STOP", "root_cause": None, "fault_count": 0}) == "P3"


def test_a_noop_with_a_fault_count_is_still_a_p0(conn):
    run_end(conn, T0710, "NOOP", trigger="0710", root_cause="run:noop:lock-held", fault_count=1)
    assert rl.classify(rl.events_for(conn, MON, BOOK)[0]) == "P0"


def test_the_noop_outcome_is_in_the_table_check(conn):
    raw_event(conn, outcome="NOOP", trigger="0710", root_cause="run:noop:lock-held")
    assert count(conn) == 1


def test_a_store_of_the_old_layout_is_refused_not_silently_kept(tmp_path):
    old = sqlite3.connect(str(tmp_path / "old.sqlite"))
    old.execute("PRAGMA user_version = 1")
    old.close()
    with pytest.raises(sqlite3.DatabaseError, match="layout"):
        rl.connect(tmp_path / "old.sqlite").close()


def start(conn, now, trigger="0655", **kw):
    return add(conn, "RUN_START", trigger, "OK", now, revision_ok=1, **kw)


def noop(conn, now, trigger="0710", cause="run:noop:lock-held"):
    return add(conn, "RUN_END", trigger, "NOOP", now, root_cause=cause, revision_ok=1)


def deadman_rows(conn, book=BOOK, day=MON):
    return [(e["outcome"], e["root_cause"]) for e in rl.events_for(conn, day, book) if e["kind"] == "DEADMAN"]


def day_streak(conn, first, last):
    count_, per_day = rl.streak(rl.events_between(conn, BOOK, first, last), rl.sessions_between(first, last))
    return count_, {d["date"]: d for d in per_day}


def test_the_orphan_age_is_ten_minutes():
    assert rl.ORPHAN_MINUTES == 10


def test_a_hung_0655_then_a_0710_lock_held_noop_sends_the_orphan_p0_and_the_no_terminal_row_p1_and_resets(conn):
    s = FakeSender()
    clean_start = utc(2026, 11, 20, 14, 55)
    start(conn, clean_start)
    run_end(conn, utc(2026, 11, 20, 15, 0))                                         # Friday: clean, count 1
    start(conn, T0655)                                                              # 6:55: written, never ended
    start(conn, T0710, "0710")
    noop(conn, T0710)                                                               # 7:10: the lock is held
    rl.deadman(conn, BOOK, T0725)
    assert sorted(deadman_rows(conn)) == [("FAULT", "run:orphan-start:0655"), ("MISSING", "deadman:no-terminal-row")]
    out = notify(conn, s, T0725)
    assert out["sent"] == 2 and out["due"] and sorted(o["severity"] for o in out["due"]) == ["P0", "P1"]
    assert sorted(i["severity"] for i in rl.incidents_for(conn, MON, BOOK)) == ["P0", "P1"]
    p0 = [t for t in s.sent if t[0].startswith("P0")]
    assert len(p0) == 1 and "run:orphan-start:0655" in p0[0][1]
    count_, by = day_streak(conn, "2026-11-20", MON)
    assert count_ == 0 and by[MON]["status"] == "reset" and by["2026-11-20"]["status"] == "clean"


def test_a_completed_0655_then_a_0710_lock_held_noop_sends_nothing_and_adds_one(conn):
    s = FakeSender()
    start(conn, utc(2026, 11, 20, 14, 55))
    run_end(conn, utc(2026, 11, 20, 15, 0))
    start(conn, T0655)
    run_end(conn, utc(2026, 11, 23, 15, 0))                                         # 6:55 COMPLETED
    start(conn, T0710, "0710")
    noop(conn, T0710)
    rl.deadman(conn, BOOK, T0725)
    assert deadman_rows(conn) == [("OK", None)]
    assert notify(conn, s, T0725)["due"] == [] and s.sent == []
    count_, by = day_streak(conn, "2026-11-20", MON)
    assert count_ == 2 and by[MON]["status"] == "clean" and by[MON]["count"] == 2


def test_only_a_noop_outside_the_window_is_a_p1_and_a_reset(conn):
    s = FakeSender()
    start(conn, T0655)
    run_end(conn, utc(2026, 11, 23, 14, 56), "NOOP", trigger="0655", root_cause="run:noop:outside-window", revision_ok=1)
    rl.deadman(conn, BOOK, T0725)
    assert deadman_rows(conn) == [("MISSING", "deadman:no-terminal-row")]              # its own RUN_END closes the start: no orphan
    out = notify(conn, s, T0725)
    assert [o["severity"] for o in out["due"]] == ["P1"] and len(s.sent) == 1
    count_, by = day_streak(conn, MON, MON)
    assert count_ == 0 and by[MON]["status"] == "reset"


def test_a_day_whose_only_run_end_is_a_noop_resets_with_no_terminal_row():
    events = [dict(event_id=1, trade_date="2026-11-25", book=BOOK, kind="RUN_END", trigger="0710", outcome="NOOP",
                   root_cause="run:noop:lock-held", fault_count=0, revision_ok=1, detail=None, at_utc="2026-11-25T15:10:00Z")]
    count_, per_day = rl.streak(week(**{"2026-11-25": events}), rl.sessions_between("2026-11-24", "2026-12-01"))
    day = {d["date"]: d for d in per_day}["2026-11-25"]
    assert (day["status"], day["reason"]) == ("reset", "no terminal row") and count_ == 3


def test_a_noop_is_ignored_when_the_day_has_a_real_end():
    day = "2026-11-25"
    for rows in ([ev(day), ev(day, outcome="NOOP", trigger="0710", root_cause="run:noop:lock-held")],
                 [ev(day, outcome="NOOP", trigger="0655", root_cause="run:noop:outside-window", revision_ok=0), ev(day, trigger="0710")],
                 [ev(day), ev(day, outcome="NOOP", trigger="0710", revision_ok=None)]):          # a NOOP is not "the last end"
        count_, per_day = rl.streak(week(**{day: rows}), rl.sessions_between("2026-11-24", "2026-12-01"))
        assert count_ == 5 and {d["date"]: d for d in per_day}[day]["status"] == "clean", rows


def test_a_noop_does_not_hide_a_real_failure_on_the_same_day():
    day = "2026-11-25"
    rows = [ev(day, outcome="FAULT", fault_count=1, root_cause="run:status-stopped:send"),
            ev(day, outcome="NOOP", trigger="0710", root_cause="run:noop:lock-held")]
    count_, per_day = rl.streak(week(**{day: rows}), rl.sessions_between("2026-11-24", "2026-12-01"))
    assert {d["date"]: d for d in per_day}[day]["reason"] == "fault" and count_ == 3


def test_only_an_ordinary_stop_for_a_closed_market_on_a_non_session_day_is_paused_and_silent(conn):
    s = FakeSender()
    thanksgiving = utc(2026, 11, 26, 14, 55)
    start(conn, thanksgiving)
    run_end(conn, thanksgiving, "ORDINARY_STOP", root_cause="run:ordinary-stop:session")
    after = utc(2026, 11, 26, 15, 25)
    assert rl.deadman(conn, BOOK, after) is None and deadman_rows(conn, day="2026-11-26") == []
    assert notify(conn, s, after)["due"] == [] and s.sent == []
    count_, by = day_streak(conn, "2026-11-25", "2026-11-27")
    assert by["2026-11-26"]["status"] == "paused" and by["2026-11-26"]["reason"] == "not a session"


def test_a_run_start_ten_minutes_old_is_no_orphan_but_is_still_open(conn):
    start(conn, utc(2026, 11, 23, 15, 15))
    rl.deadman(conn, BOOK, T0725)
    assert sorted(deadman_rows(conn)) == [("MISSING", "deadman:no-terminal-row"), ("MISSING", "deadman:run-still-open:0655")]


@pytest.mark.parametrize("age_seconds,orphan", [(5 * 60, False), (10 * 60, False), (10 * 60 + 1, True), (11 * 60, True), (20 * 60, True),
                                                (95 * 60, True)])
def test_a_start_is_an_orphan_only_when_it_is_older_than_ten_minutes(conn, age_seconds, orphan):
    started = utc(2026, 11, 23, 15, 25) - timedelta(seconds=age_seconds)
    start(conn, started)
    rl.deadman(conn, BOOK, T0725)
    assert (("FAULT", "run:orphan-start:0655") in deadman_rows(conn)) is orphan
    assert (("MISSING", "deadman:run-still-open:0655") in deadman_rows(conn)) is (not orphan)        # one or the other, never both


@pytest.mark.parametrize("outcome", ["COMPLETED", "FAULT", "CRASH", "ORDINARY_STOP", "NOOP"])
def test_a_start_that_gets_its_run_end_is_no_orphan_whatever_the_outcome(conn, outcome):
    start(conn, T0655)
    add(conn, "RUN_END", "0655", outcome, utc(2026, 11, 23, 15, 0), revision_ok=1)
    rl.deadman(conn, BOOK, T0725)
    assert not [r for r in deadman_rows(conn) if r[1] and r[1].startswith("run:orphan-start")]


def test_a_run_end_of_another_trigger_or_from_before_the_start_does_not_close_it(conn):
    run_end(conn, utc(2026, 11, 23, 14, 50))                                         # 0655, but before the start
    start(conn, T0655)
    run_end(conn, utc(2026, 11, 23, 15, 0), trigger="0710")                          # after it, but another trigger
    rl.deadman(conn, BOOK, T0725)
    assert ("FAULT", "run:orphan-start:0655") in deadman_rows(conn)


def test_each_trigger_that_hangs_has_its_own_orphan_row_and_each_is_written_once(conn):
    start(conn, T0655, "0655")
    start(conn, T0655, "MANUAL")
    start(conn, T0630, "0630")
    first = rl.deadman_check(conn, BOOK, T0725)
    again = rl.deadman_check(conn, BOOK, utc(2026, 11, 23, 16, 0))
    causes = sorted(c for o, c in deadman_rows(conn) if o == "FAULT")
    assert causes == ["run:orphan-start:0630", "run:orphan-start:0655", "run:orphan-start:manual"]
    assert len(first) == 4 and again == [] and len(deadman_rows(conn)) == 4          # three orphans and the no-terminal-row


def test_an_orphan_and_the_no_terminal_row_check_each_fire_once_over_repeated_checks(conn):
    start(conn, T0655)
    for _ in range(3):
        rl.deadman(conn, BOOK, T0725)
    assert sorted(deadman_rows(conn)) == [("FAULT", "run:orphan-start:0655"), ("MISSING", "deadman:no-terminal-row")]


def test_an_orphan_found_by_a_later_check_is_written_beside_the_day_row_already_there(conn):
    start(conn, utc(2026, 11, 23, 15, 15))                                           # 10 minutes old at 10:25
    rl.deadman(conn, BOOK, T0725)
    assert sorted(deadman_rows(conn)) == [("MISSING", "deadman:no-terminal-row"), ("MISSING", "deadman:run-still-open:0655")]
    assert rl.deadman(conn, BOOK, utc(2026, 11, 23, 15, 45)) is None                 # the day row is not rewritten
    assert sorted(deadman_rows(conn)) == [("FAULT", "run:orphan-start:0655"), ("MISSING", "deadman:no-terminal-row"),
                                          ("MISSING", "deadman:run-still-open:0655")]


def test_an_orphan_with_a_real_terminal_row_still_gives_the_p0_but_no_p1(conn):
    s = FakeSender()
    start(conn, T0630, "0630")                                                       # hung, but 6:55 ran and ended
    start(conn, T0655)
    run_end(conn, utc(2026, 11, 23, 15, 0))
    rl.deadman(conn, BOOK, T0725)
    assert sorted(deadman_rows(conn)) == [("FAULT", "run:orphan-start:0630"), ("OK", None)]
    out = notify(conn, s, T0725)
    assert [o["severity"] for o in out["due"]] == ["P0"] and len(s.sent) == 1
    assert day_streak(conn, MON, MON)[1][MON]["reason"] == "fault"


def test_the_orphan_p0_is_sent_once_and_never_held_back(conn):
    s = FakeSender()
    start(conn, T0655)
    rl.deadman(conn, BOOK, T0725)
    assert notify(conn, FakeSender(always=True), T0725)["failed"] >= 1               # a failed send is tried again
    notify(conn, s, T0725)
    notify(conn, s, T0725)
    assert len([t for t in s.sent if t[0].startswith("P0")]) == 1


def test_the_orphan_row_is_a_dead_man_fault_of_the_0725_job(conn):
    start(conn, T0655)
    rl.deadman(conn, BOOK, T0725)
    row = [e for e in rl.events_for(conn, MON, BOOK) if e["outcome"] == "FAULT"][0]
    assert (row["kind"], row["trigger"], row["root_cause"]) == ("DEADMAN", "0725", "run:orphan-start:0655")
    assert rl.classify(row) == "P0"
    assert rl.classify({"kind": "DEADMAN", "outcome": "OK", "root_cause": None, "fault_count": 0}) is None


def test_an_orphan_is_looked_for_only_in_this_book_and_from_1025_on_a_session_day(conn):
    start(conn, utc(2026, 11, 13, 14, 55))                                           # a hung start from 10 days ago (T65: still named)
    start(conn, T0655, book="another-book")
    rl.deadman(conn, BOOK, T1024)
    assert count(conn) == 2                                                          # before 10:25: nothing written
    rl.deadman(conn, BOOK, T0725)
    assert [r for r in deadman_rows(conn) if r[0] == "FAULT"] == [("FAULT", "run:orphan-start-prior:20261113:0655")]
    assert deadman_rows(conn, book="another-book") == []                             # the other book's own start is not this book's row
    start(conn, utc(2026, 11, 28, 14, 55))                                           # a Saturday
    assert rl.deadman(conn, BOOK, utc(2026, 11, 28, 16, 0)) is None
    assert deadman_rows(conn, day="2026-11-28") == []


def test_cli_deadman_reports_an_orphan_and_exits_one(tmp_path, capsys):
    cli(["record", "--kind", "RUN_START", "--trigger", "0655", "--outcome", "OK"], tmp_path, now=T0655, capsys=capsys)
    code, out = cli(["deadman"], tmp_path, now=T0725, capsys=capsys)
    j = json.loads(out.out)
    assert code == 1 and j["recorded"] is True and j["outcome"] == "MISSING" and len(j["event_ids"]) == 2
    assert j["orphans"] == ["run:orphan-start:0655"]
    code, out = cli(["deadman"], tmp_path, now=T0725, capsys=capsys)
    assert code == 0 and json.loads(out.out)["recorded"] is False


def test_cli_deadman_with_a_terminal_row_and_an_orphan_still_exits_one(tmp_path, capsys):
    cli(["record", "--kind", "RUN_START", "--trigger", "0630", "--outcome", "OK"], tmp_path, now=T0630, capsys=capsys)
    cli(["record-run", "--trigger", "0655", "--exit-code", "0", "--revision-ok", "1"], tmp_path, now=T0655, stdin=summary("done"), capsys=capsys)
    code, out = cli(["deadman"], tmp_path, now=T0725, capsys=capsys)
    j = json.loads(out.out)
    assert code == 1 and j["outcome"] == "OK" and j["orphans"] == ["run:orphan-start:0630"]


def test_cli_record_run_writes_a_noop_and_exits_zero(tmp_path, capsys):
    code, out = cli(["record-run", "--trigger", "0710", "--exit-code", "2"], tmp_path, now=T0710, capsys=capsys,
                    stdin=summary("stopped", "session", reason="another order step is running on this venue's state file"))
    j = json.loads(out.out)
    assert code == 0 and (j["outcome"], j["root_cause"]) == ("NOOP", "run:noop:lock-held")
    c = rl.connect(tmp_path / "cli.sqlite")
    row = rl.events_for(c, MON, BOOK)[0]
    c.close()
    assert (row["kind"], row["outcome"]) == ("RUN_END", "NOOP")


def test_cli_a_noop_record_needs_nobody_and_the_notify_after_it_is_quiet(tmp_path, capsys):
    code, _ = cli(["record", "--kind", "RUN_END", "--trigger", "0710", "--outcome", "NOOP", "--root-cause", "run:noop:lock-held"],
                  tmp_path, now=T0710, capsys=capsys)
    assert code == 0
    code, out = cli(["notify", "--mac"], tmp_path, now=T0710, sender=FakeSender(), capsys=capsys)
    assert code == 0 and json.loads(out.out)["due"] == []


# ------------------------------------------------------------------------------------------------ the command line

def cli(argv, tmp_path, now=T0655, stdin="", sender=None, capsys=None):
    code = rl.main([*argv[:1], "--store", str(tmp_path / "cli.sqlite"), "--book", BOOK, *argv[1:]], now=now,
                   stdin=io.StringIO(stdin), sender=sender)
    out = capsys.readouterr() if capsys else None
    return code, out


def test_cli_record_writes_one_event_and_says_so(tmp_path, capsys):
    code, out = cli(["record", "--kind", "RUN_START", "--trigger", "0655", "--outcome", "OK", "--revision-ok", "1"], tmp_path, capsys=capsys)
    assert code == 0 and json.loads(out.out)["event_id"] >= 1
    c = rl.connect(tmp_path / "cli.sqlite")
    row = rl.events_for(c, MON, BOOK)[0]
    assert row["kind"] == "RUN_START" and row["revision_ok"] == 1
    c.close()


@pytest.mark.parametrize("kind,outcome,code", [
    ("RUN_START", "OK", 0), ("PREFLIGHT", "OK", 0), ("RUN_END", "COMPLETED", 0), ("RUN_END", "ORDINARY_STOP", 0),
    ("DEADMAN", "OK", 0), ("PREFLIGHT", "WARN", 1), ("RUN_END", "FAULT", 1), ("RUN_END", "CRASH", 1),
    ("SKIP", "SKIPPED", 1), ("DEADMAN", "MISSING", 1)])
def test_cli_record_exit_code_says_whether_the_owner_is_needed(tmp_path, capsys, kind, outcome, code):
    got, _ = cli(["record", "--kind", kind, "--trigger", "0655", "--outcome", outcome, "--root-cause", "gate:x"], tmp_path, capsys=capsys)
    assert got == code


def test_cli_a_holiday_event_needs_nobody(tmp_path, capsys):
    got, _ = cli(["record", "--kind", "SKIP", "--trigger", "0655", "--outcome", "SKIPPED", "--root-cause", "calendar:holiday"],
                 tmp_path, now=utc(2026, 11, 26, 14, 55), capsys=capsys)
    assert got == 0


def test_cli_record_with_faults_needs_the_owner(tmp_path, capsys):
    got, _ = cli(["record", "--kind", "RUN_END", "--trigger", "0655", "--outcome", "COMPLETED", "--fault-count", "2"], tmp_path, capsys=capsys)
    assert got == 1


@pytest.mark.parametrize("argv", [
    ["record", "--kind", "NOPE", "--trigger", "0655", "--outcome", "OK"],
    ["record", "--kind", "RUN_END", "--trigger", "0655", "--outcome", "OK", "--root-cause", "Bad Cause"],
    ["record", "--kind", "RUN_END", "--trigger", "0655", "--outcome", "OK", "--fault-count", "-3"],
    ["record", "--kind", "RUN_END", "--trigger", "0655", "--outcome", "OK", "--revision-ok", "7"],
    ["record-run", "--trigger", "0655"],
    ["record-run", "--trigger", "0655", "--exit-code", "notanumber"],
    ["frobnicate"], [],
])
def test_cli_a_command_that_fails_exits_two_without_a_traceback(tmp_path, capsys, argv):
    got, out = cli(argv, tmp_path, capsys=capsys)
    assert got == 2 and "Traceback" not in out.err
    assert out.err.strip() != ""


def test_cli_without_a_store_or_book_exits_two(tmp_path, capsys):
    assert rl.main(["record", "--book", BOOK, "--kind", "RUN_END", "--trigger", "0655", "--outcome", "OK"], now=T0655) == 2
    assert rl.main(["record", "--store", str(tmp_path / "x.sqlite"), "--kind", "RUN_END", "--trigger", "0655", "--outcome", "OK"],
                   now=T0655) == 2
    assert not (tmp_path / "x.sqlite").exists()
    capsys.readouterr()


def test_cli_a_store_in_a_folder_that_does_not_exist_exits_two(tmp_path, capsys):
    got = rl.main(["record", "--store", str(tmp_path / "no" / "such" / "l.sqlite"), "--book", BOOK, "--kind", "RUN_END",
                   "--trigger", "0655", "--outcome", "OK"], now=T0655, stdin=io.StringIO(""))
    assert got == 2 and not (tmp_path / "no").exists()
    capsys.readouterr()


def test_cli_record_run_reads_stdin_and_maps(tmp_path, capsys):
    code, out = cli(["record-run", "--trigger", "0655", "--exit-code", "0", "--revision-ok", "1"], tmp_path, stdin=summary("done"), capsys=capsys)
    assert code == 0 and json.loads(out.out)["outcome"] == "COMPLETED"
    code, _ = cli(["record-run", "--trigger", "0710", "--exit-code", "2"], tmp_path, now=T0710,
                  stdin=summary("stopped", "send", reason="refused"), capsys=capsys)
    assert code == 1
    code, _ = cli(["record-run", "--trigger", "0710", "--exit-code", "2"], tmp_path, now=T0710,
                  stdin=summary("stopped", "session", reason="closed"), capsys=capsys)
    assert code == 0
    c = rl.connect(tmp_path / "cli.sqlite")
    rows = rl.events_for(c, MON, BOOK)
    c.close()
    assert [(r["kind"], r["outcome"], r["trigger"]) for r in rows] == [
        ("RUN_END", "COMPLETED", "0655"), ("RUN_END", "FAULT", "0710"), ("RUN_END", "ORDINARY_STOP", "0710")]
    assert rows[1]["root_cause"] == "run:status-stopped:send" and rows[1]["fault_count"] == 1 and rows[0]["revision_ok"] == 1


def test_cli_record_run_with_a_crash_and_no_summary(tmp_path, capsys):
    code, out = cli(["record-run", "--trigger", "0655", "--exit-code", "1"], tmp_path, stdin="", capsys=capsys)
    assert code == 1 and json.loads(out.out)["outcome"] == "CRASH"


def test_cli_preflight_records_an_event(tmp_path, capsys, repo):
    args = ["preflight", "--repo", str(repo["repo"]), "--accepted-commit-file", str(repo["accepted_commit_file"]),
            "--files-file", str(repo["files_file"]), "--venv-python", str(repo["venv_python"])]
    code, out = cli(args, tmp_path, now=T0630, capsys=capsys)
    assert code == 0 and json.loads(out.out)["outcome"] == "OK"
    (repo["repo"] / "f02.py").write_text("x = 'edited'\n")
    code, out = cli(args, tmp_path, now=T0630, capsys=capsys)
    assert code == 1 and json.loads(out.out)["root_cause"] == "gate:orderpath-mismatch"
    c = rl.connect(tmp_path / "cli.sqlite")
    rows = rl.events_for(c, MON, BOOK)
    c.close()
    assert [(r["kind"], r["trigger"], r["outcome"]) for r in rows] == [("PREFLIGHT", "0630", "OK"), ("PREFLIGHT", "0630", "WARN")]


def test_cli_preflight_on_a_holiday_exits_zero(tmp_path, capsys, repo):
    args = ["preflight", "--repo", str(repo["repo"]), "--accepted-commit-file", str(repo["accepted_commit_file"]),
            "--files-file", str(repo["files_file"]), "--venv-python", str(repo["venv_python"])]
    code, out = cli(args, tmp_path, now=utc(2026, 11, 26, 14, 30), capsys=capsys)
    assert code == 0 and json.loads(out.out)["root_cause"] == "calendar:holiday"


def test_cli_deadman_and_exit_codes(tmp_path, capsys):
    code, out = cli(["deadman"], tmp_path, now=T1024, capsys=capsys)
    assert code == 0 and json.loads(out.out)["recorded"] is False
    code, out = cli(["deadman"], tmp_path, now=T1025, capsys=capsys)
    assert code == 1 and json.loads(out.out)["outcome"] == "MISSING"
    code, out = cli(["deadman"], tmp_path, now=T1025, capsys=capsys)                    # a second call: nothing new
    assert code == 0 and json.loads(out.out)["recorded"] is False
    code, out = cli(["deadman"], tmp_path, now=utc(2026, 11, 24, 15, 30), capsys=capsys)
    assert code == 1


def test_cli_deadman_ok_exits_zero(tmp_path, capsys):
    cli(["record-run", "--trigger", "0655", "--exit-code", "0", "--revision-ok", "1"], tmp_path, stdin=summary("done"), capsys=capsys)
    code, out = cli(["deadman"], tmp_path, now=T1025, capsys=capsys)
    assert code == 0 and json.loads(out.out)["outcome"] == "OK"


def test_cli_notify_without_mac_decides_and_prints_but_sends_and_records_nothing(tmp_path, capsys):
    cli(["record", "--kind", "SKIP", "--trigger", "0655", "--outcome", "SKIPPED", "--root-cause", "gate:x"], tmp_path, capsys=capsys)
    code, out = cli(["notify"], tmp_path, capsys=capsys)
    j = json.loads(out.out)
    assert code == 1 and j["dry_run"] is True and len(j["due"]) == 1 and j["sent"] == 0
    c = rl.connect(tmp_path / "cli.sqlite")
    assert rl.incidents_for(c, MON, BOOK) == []
    c.close()


def test_cli_notify_with_a_sender_sends_records_and_then_is_quiet(tmp_path, capsys):
    s = FakeSender()
    cli(["record", "--kind", "SKIP", "--trigger", "0655", "--outcome", "SKIPPED", "--root-cause", "gate:x"], tmp_path, capsys=capsys)
    code, out = cli(["notify", "--mac"], tmp_path, sender=s, capsys=capsys)
    assert code == 1 and json.loads(out.out)["sent"] == 1 and len(s.sent) == 1
    code, out = cli(["notify", "--mac"], tmp_path, sender=s, capsys=capsys)
    assert code == 0 and json.loads(out.out)["due"] == [] and len(s.sent) == 1


def test_cli_notify_with_nothing_to_say_exits_zero(tmp_path, capsys):
    cli(["record-run", "--trigger", "0655", "--exit-code", "0"], tmp_path, stdin=summary("done"), capsys=capsys)
    code, _ = cli(["notify", "--mac"], tmp_path, sender=FakeSender(), capsys=capsys)
    assert code == 0


def test_cli_notify_whose_send_fails_says_so(tmp_path, capsys):
    cli(["record", "--kind", "SKIP", "--trigger", "0655", "--outcome", "SKIPPED", "--root-cause", "gate:x"], tmp_path, capsys=capsys)
    code, out = cli(["notify", "--mac"], tmp_path, sender=FakeSender(always=True), capsys=capsys)
    assert code == 1 and json.loads(out.out)["failed"] == 1


def test_cli_notify_mac_builds_the_mac_sender(tmp_path, capsys, monkeypatch):
    made = []
    monkeypatch.setattr(rl, "MacSender", lambda *a, **k: made.append(1) or FakeSender())
    cli(["record", "--kind", "SKIP", "--trigger", "0655", "--outcome", "SKIPPED", "--root-cause", "gate:x"], tmp_path, capsys=capsys)
    cli(["notify", "--mac"], tmp_path, capsys=capsys)
    assert made == [1]


def test_cli_streak_prints_the_count_the_per_day_and_the_target(tmp_path, capsys):
    for d in (24, 25, 27, 30):
        now = utc(2026, 11, d, 14, 55)
        cli(["record-run", "--trigger", "0655", "--exit-code", "0", "--revision-ok", "1"], tmp_path, now=now,
            stdin=summary("done"), capsys=capsys)
    now = utc(2026, 12, 1, 16, 0)                                                        # after 10:25 on the 1st
    code, out = cli(["streak", "--since", "2026-11-24"], tmp_path, now=now, capsys=capsys)
    j = json.loads(out.out)
    assert code == 0 and j["clean_sessions_needed"] == 5 and j["target_met"] is False
    assert j["per_day"][-1] == {"date": "2026-12-01", "status": "reset", "count": 0, "reason": "no terminal row"}
    assert j["count"] == 0                                                               # the 1st has no row: it is judged, and resets
    cli(["record-run", "--trigger", "0655", "--exit-code", "0", "--revision-ok", "1"], tmp_path, now=utc(2026, 12, 1, 14, 55),
        stdin=summary("done"), capsys=capsys)
    code, out = cli(["streak", "--since", "2026-11-24"], tmp_path, now=now, capsys=capsys)
    j = json.loads(out.out)
    assert j["count"] == 5 and j["target_met"] is True


def test_cli_streak_leaves_out_a_day_that_is_not_over_yet(tmp_path, capsys):
    code, out = cli(["streak", "--since", "2026-11-23"], tmp_path, now=T1024, capsys=capsys)     # before 10:25 on the 23rd
    j = json.loads(out.out)
    assert code == 0 and j["per_day"] == [] and j["count"] == 0
    code, out = cli(["streak", "--since", "2026-11-23"], tmp_path, now=T1025, capsys=capsys)
    assert [d["date"] for d in json.loads(out.out)["per_day"]] == [MON]


def test_cli_streak_since_must_be_a_date(tmp_path, capsys):
    code, out = cli(["streak", "--since", "last tuesday"], tmp_path, capsys=capsys)
    assert code == 2


def test_the_module_runs_as_a_command(tmp_path):
    store = tmp_path / "sub.sqlite"
    base = [sys.executable, "-m", "valedia_trader.core.run_ledger", "record", "--store", str(store), "--book", BOOK]
    done = subprocess.run([*base, "--kind", "RUN_START", "--trigger", "0655", "--outcome", "OK"],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and json.loads(done.stdout)["event_id"] >= 1
    done = subprocess.run([*base, "--kind", "WRONG", "--trigger", "0655", "--outcome", "OK"],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 2 and "Traceback" not in done.stderr


# ------------------------------------------------------------------------------------------------ what the module may do

def test_the_module_has_no_network_import_no_shell_and_no_env_file():
    src = Path(rl.__file__).read_text()
    for word in ("import socket", "import urllib", "import requests", "import http", "shell=True", "os.system", "load_env", ".env\""):
        assert word not in src, word


def test_the_module_does_not_touch_the_order_step_or_the_alerts_file():
    src = Path(rl.__file__).read_text()
    assert "order_step" not in src and "research_loop" not in src


def test_the_module_holds_no_default_store_path():
    src = Path(rl.__file__).read_text()
    for word in ("artifacts/", "memory/", "journal/", "valedia-data", "Path.home", "expanduser"):
        assert word not in src, word


# ------------------------------------------------------------------------------------------------ lead fixes after the blind check

def test_a_later_runs_noop_end_does_not_close_an_earlier_hung_start_of_the_same_trigger(conn):
    start(conn, utc(2026, 11, 23, 14, 0), trigger="MANUAL")                   # 9:00 New York: hangs
    start(conn, utc(2026, 11, 23, 14, 30), trigger="MANUAL")                  # 9:30: a second run finds the lock held
    run_end(conn, utc(2026, 11, 23, 14, 30, 5), "NOOP", trigger="MANUAL", root_cause="run:noop:lock-held")
    rl.deadman(conn, BOOK, T0725)
    assert ("FAULT", "run:orphan-start:manual") in deadman_rows(conn)


def test_each_run_end_closes_its_own_start_so_two_finished_runs_leave_no_orphan(conn):
    start(conn, utc(2026, 11, 23, 14, 0), trigger="MANUAL")
    run_end(conn, utc(2026, 11, 23, 14, 5), "COMPLETED", trigger="MANUAL")
    start(conn, utc(2026, 11, 23, 14, 30), trigger="MANUAL")
    run_end(conn, utc(2026, 11, 23, 14, 30, 5), "NOOP", trigger="MANUAL", root_cause="run:noop:lock-held")
    rl.deadman(conn, BOOK, T0725)
    assert not any((r[1] or "").startswith("run:orphan-start") for r in deadman_rows(conn))


def test_a_noop_carrying_faults_resets_the_streak_and_one_without_faults_does_not():
    days = rl.sessions_between("2026-11-24", "2026-11-24")
    noisy = [ev("2026-11-24", "RUN_START", "OK"), ev("2026-11-24"), ev("2026-11-24", outcome="NOOP", trigger="0710", fault_count=2)]
    quiet = [ev("2026-11-24", "RUN_START", "OK"), ev("2026-11-24"), ev("2026-11-24", outcome="NOOP", trigger="0710")]
    assert rl.streak(noisy, days)[0] == 0
    assert rl.streak(quiet, days)[0] == 1


# ------------------------------------------------------------------------------------------------ the 7:10 trap (T61)

T0720 = utc(2026, 11, 23, 15, 20)                      # 7:20 Pacific: a start only 5 minutes old at the 7:25 check
STILL_OPEN_0710 = ("MISSING", "deadman:run-still-open:0710")


def gate_skip(conn, now=T0655):
    return add(conn, "SKIP", "0655", "SKIPPED", now, root_cause="gate:closed", revision_ok=1)


def test_a_0655_gate_skip_then_a_hung_0710_start_is_a_p0_orphan_that_is_sent_and_resets_the_streak(conn):
    s = FakeSender()
    start(conn, utc(2026, 11, 20, 14, 55))
    run_end(conn, utc(2026, 11, 20, 15, 0))                                         # Friday: clean, count 1
    gate_skip(conn)                                                                 # 6:55: a terminal row, so no P1
    start(conn, T0710, "0710")                                                      # 7:10: the order step starts and hangs
    rl.deadman(conn, BOOK, T0725)                                                   # 15 minutes old at 7:25
    assert sorted(deadman_rows(conn)) == [("FAULT", "run:orphan-start:0710"), ("OK", None)]
    out = notify(conn, s, T0725)
    assert [o["severity"] for o in out["due"]] == ["P0", "P2"] and out["sent"] == 2         # the orphan, and the gate skip's own P2
    p0 = [t for t in s.sent if t[0].startswith("P0")]
    assert len(p0) == 1 and "run:orphan-start:0710" in p0[0][1]
    assert not [t for t in s.sent if t[0].startswith("P1")]                          # the skip is a terminal row: no dead-man P1
    count_, by = day_streak(conn, "2026-11-20", MON)
    assert count_ == 0 and by[MON]["status"] == "reset" and by["2026-11-20"]["status"] == "clean"


def test_the_same_day_without_the_hung_0710_start_sends_nothing_but_the_skip(conn):
    s = FakeSender()
    gate_skip(conn)
    rl.deadman(conn, BOOK, T0725)
    assert deadman_rows(conn) == [("OK", None)]                                     # the control: the start is what the P0 needs
    assert [o["severity"] for o in notify(conn, s, T0725)["due"]] == ["P2"]


def test_a_0720_start_still_open_at_0725_is_a_p1_sent_once_and_not_written_again(conn):
    s = FakeSender()
    gate_skip(conn)
    start(conn, T0720, "0710")
    first = rl.deadman_check(conn, BOOK, T0725)
    assert len(first) == 2 and sorted(deadman_rows(conn)) == [STILL_OPEN_0710, ("OK", None)]
    out = notify(conn, s, T0725)
    assert [o["severity"] for o in out["due"] if o["severity"] != "P2"] == ["P1"] and out["sent"] == 2     # the P2 skip and the P1
    p1 = [t for t in s.sent if t[0].startswith("P1")]
    assert len(p1) == 1 and "deadman:run-still-open:0710" in p1[0][1] and "still open" in p1[0][0]
    assert rl.deadman_check(conn, BOOK, utc(2026, 11, 23, 15, 26)) == []            # a second check writes nothing
    assert deadman_rows(conn).count(STILL_OPEN_0710) == 1
    again = notify(conn, s, utc(2026, 11, 23, 15, 26))
    assert again["due"] == [] and len(s.sent) == 2                                  # and the P1 is not sent twice
    assert day_streak(conn, MON, MON)[1][MON]["status"] == "reset"


def test_the_still_open_row_is_separate_from_the_days_ok_or_missing_row(conn):
    start(conn, T0720, "0710")                                                      # no terminal row at all
    ids = rl.deadman_check(conn, BOOK, T0725)
    assert len(ids) == 2 and sorted(deadman_rows(conn)) == [("MISSING", "deadman:no-terminal-row"), STILL_OPEN_0710]
    assert rl.deadman(conn, BOOK, utc(2026, 11, 23, 15, 30)) is None                 # the day row is not written twice either
    s = FakeSender()
    notify(conn, s, T0725)
    assert sorted(t[0] for t in s.sent) == ["P1 order step: no run recorded", "P1 order step: run still open"]


def test_deadman_returns_the_day_row_not_the_still_open_row_and_the_cli_names_both(conn, tmp_path, capsys):
    start(conn, T0720, "0710")
    i = rl.deadman(conn, BOOK, T0725)
    assert conn.execute("SELECT root_cause FROM run_events WHERE event_id = ?", (i,)).fetchone()[0] == "deadman:no-terminal-row"
    cli(["record", "--kind", "RUN_START", "--trigger", "0710", "--outcome", "OK"], tmp_path, now=T0720, capsys=capsys)
    code, out = cli(["deadman"], tmp_path, now=T0725, capsys=capsys)
    j = json.loads(out.out)
    assert code == 1 and j["outcome"] == "MISSING" and len(j["event_ids"]) == 2 and j["orphans"] == []
    assert j["still_open"] == ["deadman:run-still-open:0710"]


def test_a_completed_0710_start_fires_neither_alert(conn):
    s = FakeSender()
    start(conn, T0710, "0710")
    run_end(conn, utc(2026, 11, 23, 15, 11), trigger="0710")                         # 7:10 to 7:11, as every day 11-16 to 11-20
    rl.deadman(conn, BOOK, T0725)
    assert deadman_rows(conn) == [("OK", None)]
    assert notify(conn, s, T0725)["due"] == [] and s.sent == []
    assert day_streak(conn, MON, MON)[1][MON]["status"] == "clean"


def test_a_still_open_start_is_found_for_its_own_trigger_and_a_later_end_of_another_trigger_does_not_close_it(conn):
    start(conn, T0720, "MANUAL")
    run_end(conn, utc(2026, 11, 23, 15, 21), trigger="0710")                         # another trigger's end
    rl.deadman(conn, BOOK, T0725)
    assert ("MISSING", "deadman:run-still-open:manual") in deadman_rows(conn)


def test_a_still_open_start_that_gets_its_end_before_the_check_is_not_flagged_and_one_ended_after_stays_written(conn):
    start(conn, T0720, "0710")
    run_end(conn, utc(2026, 11, 23, 15, 22), trigger="0710", outcome="NOOP", root_cause="run:noop:lock-held")
    rl.deadman(conn, BOOK, T0725)
    assert not [r for r in deadman_rows(conn) if (r[1] or "").startswith("deadman:run-still-open")]
    start(conn, utc(2026, 11, 23, 15, 23), "MANUAL")
    rl.deadman(conn, BOOK, utc(2026, 11, 23, 15, 26))
    assert ("MISSING", "deadman:run-still-open:manual") in deadman_rows(conn)
    run_end(conn, utc(2026, 11, 23, 15, 27), trigger="MANUAL")
    rl.deadman(conn, BOOK, utc(2026, 11, 23, 15, 28))
    assert deadman_rows(conn).count(("MISSING", "deadman:run-still-open:manual")) == 1       # append-only: the row stays


def test_a_still_open_start_is_looked_for_only_today_in_this_book_from_1025_on(conn):
    start(conn, utc(2026, 11, 20, 15, 20), "0710")                                   # Friday's
    start(conn, T0720, "0710", book="another-book")
    rl.deadman(conn, BOOK, T1024)
    assert count(conn) == 2
    rl.deadman(conn, BOOK, T0725)
    assert [r for r in deadman_rows(conn) if "run-still-open" in (r[1] or "")] == []
    assert [r for r in deadman_rows(conn, book="another-book") if "run-still-open" in (r[1] or "")] == []   # not checked for it


# ------------------------------------------------------------------------------------------------ waiting for an open run, prior-day orphans (T64)

TUE = "2026-11-24"
T_TUE_0725 = utc(2026, 11, 24, 15, 25)                # 10:25 New York on the Tuesday


class Clock:
    """A fake clock: `sleep` moves it (or not, with advance=False), logs the sleep, and may run a hook at the new time."""

    def __init__(self, now, on_sleep=None, advance=True):
        self.now, self.sleeps, self.on_sleep, self.advance = now, [], on_sleep, advance

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        if self.advance:
            self.now += timedelta(seconds=seconds)
        if self.on_sleep:
            self.on_sleep(self.now)


def fault_rows(conn, day=MON):
    return [r for r in rl.events_for(conn, day, BOOK) if r["kind"] == "DEADMAN" and r["outcome"] == "FAULT"]


def late_wake(conn):
    """A run launchd fired at 7:23 (15:23 UTC); the dead-man check at 7:25 finds it open, writes the P1, and notify sends it."""
    start(conn, utc(2026, 11, 23, 15, 23))
    ids = rl.deadman_check(conn, BOOK, T1025)
    assert ("MISSING", "deadman:run-still-open:0655") in deadman_rows(conn) and len(ids) == 2
    s = FakeSender()
    notify(conn, s, T1025)
    assert any(t.startswith("P1 order step: run still open") for t in titles(s))


def test_a_late_run_that_ends_while_the_dead_man_waits_gets_no_p0(conn):
    late_wake(conn)
    ended = []

    def end_at_0727(now):
        if now >= utc(2026, 11, 23, 15, 27) and not ended:
            ended.append(run_end(conn, now))

    clock = Clock(T1025, end_at_0727)
    assert rl.deadman_wait(conn, BOOK, clock, clock.sleep) == []
    assert ended and fault_rows(conn) == []
    assert clock.now == utc(2026, 11, 23, 15, 27) and clock.sleeps == [15] * 8              # it came back as soon as the end was there


def test_a_late_run_that_never_ends_gets_the_orphan_p0_once_when_it_is_over_ten_minutes_old_and_the_wait_returns(conn):
    late_wake(conn)
    clock = Clock(T1025)
    written = rl.deadman_wait(conn, BOOK, clock, clock.sleep)
    rows = fault_rows(conn)
    assert [r["root_cause"] for r in rows] == ["run:orphan-start:0655"] and written == [rows[0]["event_id"]]
    assert rows[0]["at_utc"] == "2026-11-23T15:33:15Z"                                      # first poll after the start passed 10:00
    assert sum(clock.sleeps) == 8 * 60 + 15 and clock.now == utc(2026, 11, 23, 15, 33, 15)
    s = FakeSender()
    out = notify(conn, s, clock.now)
    assert [t for t in titles(s) if t.startswith("P0")] == ["P0 order step: a run started and never ended"] and out["sent"] == 1
    assert rl.deadman_wait(conn, BOOK, clock, clock.sleep) == [] and len(fault_rows(conn)) == 1       # nothing open now: no wait


def test_the_wait_never_runs_longer_than_the_cap_even_when_the_clock_does_not_move(conn):
    late_wake(conn)
    clock = Clock(T1025, advance=False)
    assert rl.deadman_wait(conn, BOOK, clock, clock.sleep) == []
    assert sum(clock.sleeps) == (rl.ORPHAN_MINUTES + 1) * 60 and max(clock.sleeps) == 15 and fault_rows(conn) == []


def test_the_wait_returns_at_once_when_nothing_is_open_and_never_sleeps(conn):
    clock = Clock(T1025)
    assert rl.deadman_wait(conn, BOOK, clock, clock.sleep) == [] and clock.sleeps == []
    start(conn, T0655)
    run_end(conn, utc(2026, 11, 23, 14, 58))
    assert rl.deadman_wait(conn, BOOK, clock, clock.sleep) == [] and clock.sleeps == []     # a paired start is not open


def test_the_wait_does_not_wait_before_1025_or_on_a_day_that_is_no_session(conn):
    start(conn, T0655)
    start(conn, utc(2026, 11, 23, 15, 20), "MANUAL")      # lead: a start still under 10 minutes old at 10:24
    early = Clock(T1024)
    assert rl.deadman_wait(conn, BOOK, early, early.sleep) == [] and early.sleeps == []
    sat = Clock(utc(2026, 11, 28, 16, 0))
    start(conn, utc(2026, 11, 28, 15, 55))
    assert rl.deadman_wait(conn, BOOK, sat, sat.sleep) == [] and sat.sleeps == []


def test_the_wait_polls_every_poll_s_and_clamps_the_last_sleep(conn):
    late_wake(conn)
    clock = Clock(T1025, advance=False)
    rl.deadman_wait(conn, BOOK, clock, clock.sleep, poll_s=50)
    assert clock.sleeps == [50] * 13 + [10]


def test_cli_deadman_wait_open_runs_the_check_then_the_wait_and_prints_what_it_wrote(tmp_path, capsys):
    cli(["record", "--kind", "RUN_START", "--trigger", "0655", "--outcome", "OK"], tmp_path, now=utc(2026, 11, 23, 15, 23), capsys=capsys)
    slept = []
    code = rl.main(["deadman", "--store", str(tmp_path / "cli.sqlite"), "--book", BOOK, "--wait-open"], now=T1025,
                   stdin=io.StringIO(""), sleep=slept.append)
    j = json.loads(capsys.readouterr().out)
    assert code == 1 and j["recorded"] is True and len(j["event_ids"]) == 3 and j["orphans"] == ["run:orphan-start:0655"]
    assert len(j["still_open"]) == 1 and sum(slept) == 8 * 60 + 15                           # the P1 from the check, then the P0 from the wait
    before = len(slept)
    code = rl.main(["deadman", "--store", str(tmp_path / "cli.sqlite"), "--book", BOOK, "--wait-open"], now=utc(2026, 11, 23, 15, 40),
                   stdin=io.StringIO(""), sleep=slept.append)
    assert code == 0 and json.loads(capsys.readouterr().out)["recorded"] is False and len(slept) == before     # nothing open: no wait


def test_cli_deadman_without_wait_open_never_sleeps(tmp_path, capsys):
    cli(["record", "--kind", "RUN_START", "--trigger", "0655", "--outcome", "OK"], tmp_path, now=utc(2026, 11, 23, 15, 23), capsys=capsys)
    slept = []
    rl.main(["deadman", "--store", str(tmp_path / "cli.sqlite"), "--book", BOOK], now=T1025, stdin=io.StringIO(""), sleep=slept.append)
    capsys.readouterr()
    assert slept == []


def yesterdays_hung_start(conn, trigger="0655"):
    start(conn, utc(2026, 11, 23, 14, 55), trigger)                                           # Monday: started, never ended, holds the lock


def lock_held_tuesday(conn):
    for trig, at in (("0655", utc(2026, 11, 24, 14, 55)), ("0710", utc(2026, 11, 24, 15, 10))):
        start(conn, at, trig)
        noop(conn, at + timedelta(seconds=2), trig)


def test_yesterdays_hung_start_with_two_lock_held_noops_today_gives_the_prior_p0_and_the_days_p1(conn):
    s = FakeSender()
    yesterdays_hung_start(conn)
    lock_held_tuesday(conn)
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert sorted(deadman_rows(conn, day=TUE)) == [("FAULT", "run:orphan-start-prior:20261123:0655"), ("MISSING", "deadman:no-terminal-row")]
    out = notify(conn, s, T_TUE_0725)
    assert sorted(o["severity"] for o in out["due"]) == ["P0", "P1"] and out["sent"] == 2
    p0 = [x for t, x in s.sent if t.startswith("P0")]
    assert len(p0) == 1 and "run:orphan-start-prior:20261123:0655" in p0[0]
    assert day_streak(conn, "2026-11-23", TUE)[1][TUE]["status"] == "reset"
    assert day_streak(conn, "2026-11-23", TUE)[1][TUE]["reason"] == "fault"


def test_the_prior_orphan_is_written_on_today_and_never_on_the_start_day(conn):
    yesterdays_hung_start(conn)
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert deadman_rows(conn, day=MON) == []
    row = fault_rows(conn, TUE)[0]
    assert (row["kind"], row["trigger"], row["outcome"]) == ("DEADMAN", "0725", "FAULT") and rl.classify(row) == "P0"


def test_a_second_check_writes_nothing_and_a_later_day_writes_its_own_p1_row(conn):
    # T65: the row is once per session day, not once ever: Wednesday names the same start again, as a P1.
    yesterdays_hung_start(conn)
    lock_held_tuesday(conn)
    first = rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert len(first) == 2 and rl.deadman_check(conn, BOOK, T_TUE_0725) == []
    wed = utc(2026, 11, 25, 15, 25)                                                           # the start is 2 days old now
    rl.deadman_check(conn, BOOK, wed)
    assert [r for r in deadman_rows(conn, day="2026-11-25") if (r[1] or "").startswith("run:orphan-start-prior")] == [
        ("MISSING", "run:orphan-start-prior:20261123:0655")]
    assert rl.deadman_check(conn, BOOK, wed) == []
    assert count(conn) == 4 + 1 + 2 + 2                                                       # 1 start, 4 today, 2 prior+P1, wed's prior P1 and P1


@pytest.mark.parametrize("days_ago", [1, 6, 7, 8, 30])
def test_a_start_with_no_lock_proof_is_named_however_old_it_is(conn, days_ago):
    at = utc(2026, 11, 24, 14, 55) - timedelta(days=days_ago)                                 # T65: no 7-day window
    start(conn, at)
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    day = at.date().strftime("%Y%m%d")
    assert ("FAULT", f"run:orphan-start-prior:{day}:0655") in deadman_rows(conn, day=TUE)


def test_yesterdays_start_with_its_own_end_gives_nothing(conn):
    yesterdays_hung_start(conn)
    run_end(conn, utc(2026, 11, 23, 14, 58))
    run_end(conn, utc(2026, 11, 23, 15, 0), trigger="0710", outcome="NOOP", root_cause="run:noop:lock-held")      # another trigger's end
    lock_held_tuesday(conn)
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert fault_rows(conn, TUE) == []


def test_a_prior_day_start_is_closed_only_by_an_end_on_its_own_day_and_trigger(conn):
    yesterdays_hung_start(conn)
    run_end(conn, utc(2026, 11, 24, 14, 58), outcome="NOOP", root_cause="run:noop:lock-held")   # today's 0655 end does not close it
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert [r["root_cause"] for r in fault_rows(conn, TUE)] == ["run:orphan-start-prior:20261123:0655"]


def test_each_prior_day_and_trigger_has_its_own_row_in_this_book_only(conn):
    start(conn, utc(2026, 11, 20, 14, 30), "0630")
    start(conn, utc(2026, 11, 20, 14, 55))
    start(conn, utc(2026, 11, 23, 15, 0), "MANUAL")
    start(conn, utc(2026, 11, 23, 14, 55), book="another-book")
    first = rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert sorted(r["root_cause"] for r in fault_rows(conn, TUE)) == [
        "run:orphan-start-prior:20261120:0630", "run:orphan-start-prior:20261120:0655", "run:orphan-start-prior:20261123:manual"]
    assert len(first) == 4 and rl.deadman_check(conn, BOOK, T_TUE_0725) == []
    assert fault_rows(conn, TUE) and deadman_rows(conn, book="another-book", day=TUE) == []


def test_a_prior_orphan_row_in_another_book_does_not_stop_this_books_row(conn):
    start(conn, utc(2026, 11, 20, 14, 55), book="another-book")
    start(conn, utc(2026, 11, 20, 14, 55))
    assert len(rl.deadman_check(conn, "another-book", T_TUE_0725)) >= 1
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert [r["root_cause"] for r in fault_rows(conn, TUE)] == ["run:orphan-start-prior:20261120:0655"]


def test_a_prior_orphan_is_not_checked_before_1025_or_on_a_day_that_is_no_session(conn):
    yesterdays_hung_start(conn)
    assert rl.deadman_check(conn, BOOK, utc(2026, 11, 24, 15, 24)) == []
    assert rl.deadman_check(conn, BOOK, utc(2026, 11, 28, 16, 0)) == []                       # a Saturday
    assert count(conn) == 1


def test_the_prior_orphan_p0_is_sent_once_and_retried_when_the_send_fails(conn):
    yesterdays_hung_start(conn)
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert notify(conn, FakeSender(always=True), T_TUE_0725)["failed"] >= 1
    s = FakeSender()
    notify(conn, s, T_TUE_0725)
    notify(conn, s, T_TUE_0725)
    assert len([t for t in titles(s) if t.startswith("P0")]) == 1


def test_a_prior_orphan_row_is_a_valid_cause_in_the_table_and_the_regex(conn):
    for trig in rl.TRIGGERS:
        cause = f"run:orphan-start-prior:20261123:{trig.lower()}"
        assert add(conn, "DEADMAN", "0725", "FAULT", T_TUE_0725, root_cause=cause) > 0


def test_the_prior_orphan_gets_its_own_wording_in_the_alert(conn):
    yesterdays_hung_start(conn)
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    s = FakeSender()
    notify(conn, s, T_TUE_0725)
    title, text = s.sent[0]
    assert title.startswith("P0") and "earlier day" in title
    assert "run:orphan-start-prior:20261123:0655" in text and "2026-11-23" in text


def test_the_wait_never_waits_for_a_prior_orphan(conn):
    yesterdays_hung_start(conn)
    clock = Clock(T_TUE_0725)
    written = rl.deadman_wait(conn, BOOK, clock, clock.sleep)
    assert written == [] and clock.sleeps == []                                               # only today's open start is waited for


# ------------------------------------------------------------------------------------------------ prior-day orphans: no window, lock proofs (T65)

WED = "2026-11-25"
T_WED_0725 = utc(2026, 11, 25, 15, 25)
T_FRI_0725 = utc(2026, 11, 27, 15, 25)                # the Friday after Thanksgiving: a session
T_MON30_0725 = utc(2026, 11, 30, 15, 25)
T_MON14_0725 = utc(2026, 12, 7, 15, 25)               # 14 days after the hung Monday start
PRIOR_0655 = "run:orphan-start-prior:20261123:0655"
UNREPORTED_0655 = "run:orphan-start-unreported:20261123:0655"


def prior_rows(conn, day):
    return [(r[0], r[1]) for r in deadman_rows(conn, day=day) if (r[1] or "").startswith(("run:orphan-start-prior", "run:orphan-start-unreported"))]


def check_and_notify(conn, now, sender=None):
    s = sender or FakeSender()
    rl.deadman_check(conn, BOOK, now)
    return notify(conn, s, now), s


def late_start_after_the_deadman(conn):
    """Monday: the dead-man ran at 10:25 and saw nothing open; a run started at 11:30 New York and hung."""
    rl.deadman_check(conn, BOOK, T1025)
    start(conn, utc(2026, 11, 23, 16, 30))


def test_the_constants_are_the_t65_ones():
    assert not hasattr(rl, "PRIOR_ORPHAN_DAYS")
    assert rl.UNREPORTED_CAUSE == "run:orphan-start-unreported:"


# rule 1: no lock proof since the start

def test_the_first_report_of_a_start_is_a_p0_and_every_later_day_a_p1_on_day_2_and_day_14(conn):
    yesterdays_hung_start(conn)
    tue, s = check_and_notify(conn, T_TUE_0725)
    assert prior_rows(conn, TUE) == [("FAULT", PRIOR_0655)]
    assert [(o["severity"], o["root_cause"]) for o in tue["due"] if o["root_cause"] == PRIOR_0655] == [("P0", PRIOR_0655)]
    wed, s2 = check_and_notify(conn, T_WED_0725)
    assert prior_rows(conn, WED) == [("MISSING", PRIOR_0655)]
    assert [o["severity"] for o in wed["due"] if o["root_cause"] == PRIOR_0655] == ["P1"]
    assert not [t for t in titles(s2) if t.startswith("P0")]
    mon14, s3 = check_and_notify(conn, T_MON14_0725)                                           # the 14th day: still named, still P1
    assert prior_rows(conn, "2026-12-07") == [("MISSING", PRIOR_0655)]
    assert [o["severity"] for o in mon14["due"] if o["root_cause"] == PRIOR_0655] == ["P1"]


def test_a_second_check_in_the_same_day_writes_no_second_prior_row(conn):
    yesterdays_hung_start(conn)
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    rl.deadman_check(conn, BOOK, T_WED_0725)
    assert rl.deadman_check(conn, BOOK, T_WED_0725) == []
    assert rl.deadman_check(conn, BOOK, utc(2026, 11, 25, 17, 0)) == []
    assert prior_rows(conn, WED) == [("MISSING", PRIOR_0655)]


def test_a_start_that_began_after_its_own_days_dead_man_gets_its_first_p0_the_next_day(conn):
    late_start_after_the_deadman(conn)
    assert [r for r in deadman_rows(conn) if r[0] == "FAULT"] == []                            # nothing named on Monday itself
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert prior_rows(conn, TUE) == [("FAULT", PRIOR_0655)]                                    # Tuesday: P0, not P1
    rl.deadman_check(conn, BOOK, T_WED_0725)
    assert prior_rows(conn, WED) == [("MISSING", PRIOR_0655)]                                  # Wednesday: P1


def test_a_start_that_the_dead_man_already_named_gets_a_p1_from_the_next_day_on(conn):
    yesterdays_hung_start(conn)
    rl.deadman_check(conn, BOOK, T1025)                                                        # Monday's own P0
    assert ("FAULT", "run:orphan-start:0655") in deadman_rows(conn)
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert prior_rows(conn, TUE) == [("MISSING", PRIOR_0655)]


def test_the_own_day_p0_of_another_trigger_does_not_make_this_start_a_p1(conn):
    start(conn, utc(2026, 11, 23, 14, 30), "0630")
    rl.deadman_check(conn, BOOK, T1025)                                                        # names 0630 only
    start(conn, utc(2026, 11, 23, 16, 30), "0710")                                             # a later start, after the check
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert sorted(prior_rows(conn, TUE)) == [("FAULT", "run:orphan-start-prior:20261123:0710"),
                                             ("MISSING", "run:orphan-start-prior:20261123:0630")]


def test_a_prior_row_in_another_book_is_not_this_books_p0(conn):
    yesterdays_hung_start(conn)
    start(conn, utc(2026, 11, 23, 14, 55), book="another-book")
    rl.deadman_check(conn, "another-book", T_TUE_0725)                                         # that book's first report
    rl.deadman_check(conn, "another-book", T_WED_0725)
    rl.deadman_check(conn, BOOK, T_WED_0725)
    assert prior_rows(conn, WED) == [("FAULT", PRIOR_0655)]                                    # this book's first report is a P0


def test_the_prior_p1_resets_the_days_streak_explicitly_and_a_p0_row_does_too():
    row = ev("2026-11-24", kind="DEADMAN", outcome="OK", trigger="0725", root_cause=PRIOR_0655)
    end = ev("2026-11-24", outcome="COMPLETED")
    count_, per_day = rl.streak([row, end], ["2026-11-24"])
    assert count_ == 0 and per_day[0]["status"] == "reset" and "earlier day" in per_day[0]["reason"]
    count_, per_day = rl.streak([end], ["2026-11-24"])                                         # the opposite: without the row it is clean
    assert count_ == 1 and per_day[0]["status"] == "clean"
    for outcome in ("FAULT", "MISSING"):
        count_, _ = rl.streak([ev("2026-11-24", kind="DEADMAN", outcome=outcome, trigger="0725", root_cause=PRIOR_0655), end], ["2026-11-24"])
        assert count_ == 0


def test_a_day_with_a_prior_p1_resets_even_when_the_day_has_an_ordinary_stop_and_a_clean_end(conn):
    yesterdays_hung_start(conn)
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    run_end(conn, utc(2026, 11, 25, 14, 58), outcome="ORDINARY_STOP")                           # no cause: not a lock proof, looks clean
    rl.deadman_check(conn, BOOK, T_WED_0725)
    assert ("MISSING", PRIOR_0655) in deadman_rows(conn, day=WED)
    assert day_streak(conn, MON, WED)[1][WED]["status"] == "reset"


def test_the_one_p1_of_a_day_names_the_blocking_start_and_the_generic_one_is_not_sent_too(conn):
    yesterdays_hung_start(conn)
    lock_held_tuesday(conn)
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    notify(conn, FakeSender(), T_TUE_0725)
    for trig, at in (("0655", utc(2026, 11, 25, 14, 55)), ("0710", utc(2026, 11, 25, 15, 10))):
        start(conn, at, trig)
        noop(conn, at + timedelta(seconds=2), trig)
    rl.deadman_check(conn, BOOK, T_WED_0725)
    assert ("MISSING", "deadman:no-terminal-row") in deadman_rows(conn, day=WED) and ("MISSING", PRIOR_0655) in deadman_rows(conn, day=WED)
    s = FakeSender()
    out = notify(conn, s, T_WED_0725)
    p1 = [x for t, x in s.sent if t.startswith("P1")]
    assert len(p1) == 1 and len([o for o in out["due"] if o["severity"] == "P1"]) == 1
    assert "2026-11-23" in p1[0] and "0655" in p1[0] and PRIOR_0655 in p1[0] and "no terminal row" in p1[0]
    assert notify(conn, s, T_WED_0725)["due"] == [] and len([t for t in titles(s) if t.startswith("P1")]) == 1


def test_a_day_with_only_the_generic_p1_keeps_the_generic_text(conn):
    rl.deadman_check(conn, BOOK, T1025)
    s = FakeSender()
    notify(conn, s, T1025)
    assert [x for t, x in s.sent if t.startswith("P1")] == ["no terminal row for 2026-11-23: nothing ended or was skipped by 10:25 New York"]


def test_two_blocking_starts_in_one_day_send_one_p1_that_names_both(conn):
    start(conn, utc(2026, 11, 20, 14, 55))
    start(conn, utc(2026, 11, 23, 14, 30), "0630")
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    notify(conn, FakeSender(), T_TUE_0725)                                                     # both P0 the first day
    rl.deadman_check(conn, BOOK, T_WED_0725)
    s = FakeSender()
    notify(conn, s, T_WED_0725)
    notify(conn, s, T_WED_0725)
    p1 = [x for t, x in s.sent if t.startswith("P1")]
    assert len(p1) == 1 and "2026-11-20" in p1[0] and "2026-11-23" in p1[0] and not [t for t in titles(s) if t.startswith("P0")]


def test_a_p1_that_failed_to_send_is_retried_and_then_sent_once(conn):
    yesterdays_hung_start(conn)
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    notify(conn, FakeSender(), T_TUE_0725)
    rl.deadman_check(conn, BOOK, T_WED_0725)
    assert notify(conn, FakeSender(always=True), T_WED_0725)["failed"] == 1
    s = FakeSender()
    notify(conn, s, T_WED_0725)
    notify(conn, s, T_WED_0725)
    assert len([t for t in titles(s) if t.startswith("P1")]) == 1


def test_the_prior_p1_row_does_not_stand_in_for_the_days_dead_man_row(conn):
    yesterdays_hung_start(conn)
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    ids = rl.deadman_check(conn, BOOK, T_WED_0725)
    assert len(ids) == 2 and sorted(deadman_rows(conn, day=WED)) == [("MISSING", "deadman:no-terminal-row"), ("MISSING", PRIOR_0655)]


def test_the_days_dead_man_row_is_ok_when_a_terminal_row_is_there_even_beside_a_prior_p1(conn):
    yesterdays_hung_start(conn)
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    run_end(conn, utc(2026, 11, 25, 14, 58), outcome="ORDINARY_STOP")                           # a terminal row, but no lock proof
    i = rl.deadman(conn, BOOK, T_WED_0725)
    assert conn.execute("SELECT outcome FROM run_events WHERE event_id = ?", (i,)).fetchone()[0] == "OK"
    assert ("MISSING", PRIOR_0655) in deadman_rows(conn, day=WED)


def test_the_dead_man_function_returns_the_days_row_and_not_the_prior_row(conn):
    yesterdays_hung_start(conn)
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    i = rl.deadman(conn, BOOK, T_WED_0725)
    assert conn.execute("SELECT root_cause FROM run_events WHERE event_id = ?", (i,)).fetchone()[0] == "deadman:no-terminal-row"


def test_the_cli_deadman_names_a_prior_p1_as_an_orphan_beside_the_days_row_and_exits_one(tmp_path, capsys):
    cli(["record", "--kind", "RUN_START", "--trigger", "0655", "--outcome", "OK", "--revision-ok", "1"], tmp_path, now=utc(2026, 11, 23, 14, 55), capsys=capsys)
    cli(["deadman"], tmp_path, now=T_TUE_0725, capsys=capsys)
    code, out = cli(["deadman"], tmp_path, now=T_WED_0725, capsys=capsys)
    got = json.loads(out.out)
    assert code == 1 and got["outcome"] == "MISSING" and PRIOR_0655 in got["orphans"] and len(got["event_ids"]) == 2
    main = got["event_id"]
    c = rl.connect(tmp_path / "cli.sqlite")
    try:
        assert c.execute("SELECT root_cause FROM run_events WHERE event_id = ?", (main,)).fetchone()[0] == "deadman:no-terminal-row"
    finally:
        c.close()


def test_plain_deadman_without_a_prior_orphan_is_unchanged(conn):
    run_end(conn, T0655)
    assert rl.deadman_check(conn, BOOK, T1025) and deadman_rows(conn) == [("OK", None)]
    assert rl.deadman_check(conn, BOOK, T1025) == [] and prior_rows(conn, MON) == []


# rule 2 and the lock proofs

def proof_outcome(conn, now_check=T_TUE_0725):
    return sorted(prior_rows(conn, TUE))


@pytest.mark.parametrize("outcome,cause", [
    ("COMPLETED", None),
    ("ORDINARY_STOP", "run:ordinary-stop:session"),
    ("NOOP", "run:noop:outside-window"),
])
def test_each_lock_proof_outcome_closes_a_start_that_never_had_a_p0_with_the_unreported_p0(conn, outcome, cause):
    late_start_after_the_deadman(conn)
    run_end(conn, utc(2026, 11, 24, 14, 55), outcome=outcome, root_cause=cause)
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert proof_outcome(conn) == [("FAULT", UNREPORTED_0655)]


@pytest.mark.parametrize("outcome,cause,fault_count", [
    ("NOOP", "run:noop:lock-held", 0),
    ("FAULT", "run:status-stopped:ticket", 1),
    ("FAULT", "run:status-stopped:lock", 1),
    ("CRASH", "run:exit-nonzero", 0),
    ("ORDINARY_STOP", None, 0),
    ("ORDINARY_STOP", "calendar:holiday", 0),
    ("NOOP", None, 0),
])
def test_the_other_run_ends_are_no_lock_proof_and_the_start_stays_blocking(conn, outcome, cause, fault_count):
    late_start_after_the_deadman(conn)
    run_end(conn, utc(2026, 11, 24, 14, 55), outcome=outcome, root_cause=cause, fault_count=fault_count)
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert proof_outcome(conn) == [("FAULT", PRIOR_0655)]


def test_a_proof_in_another_book_does_not_count(conn):
    late_start_after_the_deadman(conn)
    run_end(conn, utc(2026, 11, 24, 14, 55), book="another-book")
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert proof_outcome(conn) == [("FAULT", PRIOR_0655)]


def test_a_proof_earlier_than_the_start_does_not_count_and_one_later_does(conn):
    run_end(conn, utc(2026, 11, 23, 14, 40), trigger="0630")                                   # Monday 6:40: before the 6:55 start
    late_start_after_the_deadman(conn)
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert proof_outcome(conn) == [("FAULT", PRIOR_0655)]
    run_end(conn, utc(2026, 11, 24, 14, 55), trigger="0630")                                   # the opposite: a later proof closes it
    rl.deadman_check(conn, BOOK, T_WED_0725)
    assert prior_rows(conn, WED) == []                                                         # Tuesday's P0 was the report: a proof now writes nothing


def test_a_proof_on_the_start_day_but_before_the_start_does_not_close_it(conn):
    run_end(conn, utc(2026, 11, 23, 14, 55, 0), trigger="0630")                                # the same minute, earlier second
    start(conn, utc(2026, 11, 23, 14, 55, 1))
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert proof_outcome(conn) == [("FAULT", PRIOR_0655)]


def test_the_proof_is_a_run_end_of_any_trigger_and_any_later_day(conn):
    late_start_after_the_deadman(conn)
    run_end(conn, utc(2026, 11, 25, 15, 10), trigger="0710")                                   # Wednesday, another trigger
    rl.deadman_check(conn, BOOK, T_WED_0725)
    assert prior_rows(conn, WED) == [("FAULT", UNREPORTED_0655)]


def test_a_lock_proof_after_a_p0_writes_nothing_at_all(conn):
    yesterdays_hung_start(conn)
    rl.deadman_check(conn, BOOK, T1025)                                                        # Monday's own P0 (run:orphan-start:0655)
    run_end(conn, utc(2026, 11, 24, 14, 55))
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert prior_rows(conn, TUE) == []


def test_a_blocking_start_becomes_quiet_once_a_lock_proof_lands(conn):
    yesterdays_hung_start(conn)
    lock_held_tuesday(conn)
    rl.deadman_check(conn, BOOK, T_TUE_0725)                                                   # P0 on Tuesday
    rl.deadman_check(conn, BOOK, T_WED_0725)                                                   # P1 on Wednesday
    assert prior_rows(conn, WED) == [("MISSING", PRIOR_0655)]
    run_end(conn, utc(2026, 11, 27, 14, 55))                                                   # Friday a run completes: the lock was taken
    rl.deadman_check(conn, BOOK, T_FRI_0725)
    assert prior_rows(conn, "2026-11-27") == []                                                # rule 2: nothing
    rl.deadman_check(conn, BOOK, T_MON30_0725)
    assert prior_rows(conn, "2026-11-30") == []


def test_the_proof_must_follow_the_start_in_time_not_in_event_order(conn):
    run_end(conn, utc(2026, 11, 23, 14, 55, 0), trigger="0630")                                # earlier time written first
    start(conn, utc(2026, 11, 23, 14, 55, 30))
    run_end(conn, utc(2026, 11, 23, 14, 55, 10), trigger="0710")                               # written LAST but stamped before the start
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert proof_outcome(conn) == [("FAULT", PRIOR_0655)]


# rule 3: unreported

def test_an_unreported_start_is_one_p0_ever_written_on_today_with_its_own_wording_and_resets_the_streak(conn):
    late_start_after_the_deadman(conn)
    run_end(conn, utc(2026, 11, 24, 14, 55))
    s = FakeSender()
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    out = notify(conn, s, T_TUE_0725)
    assert prior_rows(conn, TUE) == [("FAULT", UNREPORTED_0655)] and deadman_rows(conn, day=MON) == [("MISSING", "deadman:no-terminal-row")]
    p0 = [(t, x) for t, x in s.sent if t.startswith("P0")]
    assert len(p0) == 1 and UNREPORTED_0655 in p0[0][1] and "2026-11-23" in p0[0][1]
    assert "never ended and was never reported" in p0[0][1] and "a later run took the lock, so it is no longer running" in p0[0][1]
    assert "may still be alive" not in p0[0][1] and "may still hold" not in p0[0][1]
    assert day_streak(conn, MON, TUE)[1][TUE]["status"] == "reset"
    for now in (T_WED_0725, T_FRI_0725, T_MON14_0725):                                         # once ever: never again
        rl.deadman_check(conn, BOOK, now)
        assert [r for r in prior_rows(conn, now.date().isoformat()) if "unreported" in r[1]] == []
    assert out["sent"] >= 1


def test_the_unreported_row_does_not_come_back_as_a_prior_blocking_row_later(conn):
    late_start_after_the_deadman(conn)
    run_end(conn, utc(2026, 11, 24, 14, 55))
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    rl.deadman_check(conn, BOOK, T_WED_0725)
    assert prior_rows(conn, WED) == []


def test_a_start_with_a_proof_that_had_its_p0_is_not_reported_as_unreported(conn):
    yesterdays_hung_start(conn)
    rl.deadman_check(conn, BOOK, T1025)
    run_end(conn, utc(2026, 11, 24, 14, 55))
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert [r for r in deadman_rows(conn, day=TUE) if "unreported" in (r[1] or "")] == []


def test_each_unreported_start_has_its_own_row_by_day_and_trigger(conn):
    start(conn, utc(2026, 11, 20, 16, 30), "0710")
    start(conn, utc(2026, 11, 23, 16, 30), "MANUAL")
    run_end(conn, utc(2026, 11, 24, 14, 55))
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert sorted(prior_rows(conn, TUE)) == [("FAULT", "run:orphan-start-unreported:20261120:0710"),
                                             ("FAULT", "run:orphan-start-unreported:20261123:manual")]
    assert rl.deadman_check(conn, BOOK, T_TUE_0725) == []


def test_the_unreported_row_is_not_written_for_a_start_that_is_still_blocking(conn):
    late_start_after_the_deadman(conn)
    lock_held_tuesday(conn)
    rl.deadman_check(conn, BOOK, T_TUE_0725)
    assert prior_rows(conn, TUE) == [("FAULT", PRIOR_0655)]


def test_the_unreported_cause_is_a_valid_cause_for_all_five_triggers_in_the_table_and_the_regex(conn):
    assert len(rl.TRIGGERS) == 5
    for trig in rl.TRIGGERS:
        cause = f"{rl.UNREPORTED_CAUSE}20261123:{trig.lower()}"
        assert rl._CAUSE_RE.fullmatch(cause) and add(conn, "DEADMAN", "0725", "FAULT", T_TUE_0725, root_cause=cause) > 0
        cause = f"{rl.PRIOR_ORPHAN_CAUSE}20261123:{trig.lower()}"
        assert rl._CAUSE_RE.fullmatch(cause) and add(conn, "DEADMAN", "0725", "MISSING", T_TUE_0725, root_cause=cause) > 0


def test_classify_the_prior_rows_p0_for_a_fault_and_p1_for_a_missing(conn):
    f = {"kind": "DEADMAN", "outcome": "FAULT", "root_cause": UNREPORTED_0655, "fault_count": 0}
    assert rl.classify(f) == "P0"
    assert rl.classify({**f, "outcome": "MISSING", "root_cause": PRIOR_0655}) == "P1"


# speed, empty store, other commands

def test_the_prior_scan_costs_the_same_number_of_run_event_queries_for_one_start_or_for_thirty(tmp_path):
    def selects(n):
        c = rl.connect(tmp_path / f"s{n}.sqlite")
        try:
            for k in range(n):
                start(c, utc(2026, 10, 1, 14, 55) + timedelta(days=k))                          # n different unpaired starts
            seen = []
            c.set_trace_callback(lambda q: seen.append(q) if q.lstrip().upper().startswith("SELECT") and "run_events" in q else None)
            rl.deadman_check(c, BOOK, T_TUE_0725)
            return len(seen)
        finally:
            c.close()
    assert selects(1) == selects(30)


def test_an_empty_store_works_for_every_command(tmp_path, capsys, repo):
    store = tmp_path / "fresh" / "ledger.sqlite"
    store.parent.mkdir()
    base = ["--store", str(store), "--book", BOOK]
    out = io_main(["deadman", *base], T_TUE_0725, capsys)
    assert out[0] == 1 and json.loads(out[1])["outcome"] == "MISSING"
    store.unlink()
    assert io_main(["deadman", "--wait-open", *base], T_TUE_0725, capsys)[0] == 1
    store.unlink()
    assert io_main(["notify", *base], T_TUE_0725, capsys)[0] == 0
    store.unlink()
    assert io_main(["record", *base, "--kind", "RUN_START", "--trigger", "0655", "--outcome", "OK", "--revision-ok", "1"], T0655, capsys)[0] == 0
    store.unlink()
    assert io_main(["record-run", *base, "--trigger", "0655", "--exit-code", "0", "--revision-ok", "1"], T0655, capsys,
                   stdin=summary("done"))[0] == 0
    store.unlink()
    r = repo
    assert io_main(["preflight", *base, "--repo", str(r["repo"]), "--accepted-commit-file", str(r["accepted_commit_file"]),
                    "--files-file", str(r["files_file"]), "--venv-python", str(r["venv_python"])], T0655, capsys)[0] == 0


def io_main(argv, now, capsys, stdin=""):
    code = rl.main(argv, now=now, stdin=io.StringIO(stdin), sender=None)
    return code, capsys.readouterr().out


def test_a_store_that_starts_empty_names_a_prior_start_written_by_the_first_commands(tmp_path, capsys):
    store = tmp_path / "ledger.sqlite"
    base = ["--store", str(store), "--book", BOOK]
    assert io_main(["record", *base, "--kind", "RUN_START", "--trigger", "0655", "--outcome", "OK", "--revision-ok", "1"],
                   utc(2026, 11, 23, 14, 55), capsys)[0] == 0
    code, out = io_main(["deadman", "--wait-open", *base], T_TUE_0725, capsys)
    assert code == 1 and PRIOR_0655 in json.loads(out)["orphans"]
    code, out = io_main(["notify", *base], T_TUE_0725, capsys)
    assert code == 1 and any(o["root_cause"] == PRIOR_0655 for o in json.loads(out)["due"])
