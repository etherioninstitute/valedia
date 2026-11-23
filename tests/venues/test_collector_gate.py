"""CollectorGate and GatedPacer. Fixture databases live under tmp_path only; time is simulated; no network."""
import os
import sqlite3

import pytest

from valedia_trader.venues import collector_gate as cg

NOW = 1_700_000_000.0 - 1_700_000_000.0 % 60 + 30.0       # second 30 of some minute
M0 = NOW - NOW % 60                                        # that minute's start


def make_db(path, decisions=(), beat=NOW - 10, forced_col=False):
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE decisions(slot_ts REAL, decision_ts REAL, status TEXT" + (", forced INTEGER DEFAULT 0" if forced_col else "") + ")")
    c.execute("CREATE TABLE heartbeats(ts REAL, model_calls INTEGER, paused INTEGER)")
    for d in decisions:
        c.execute("INSERT INTO decisions(slot_ts, decision_ts, status" + (", forced" if forced_col else "") + ") VALUES (?,?,?"
                  + (",?" if forced_col else "") + ")", d)
    if beat is not None:
        c.execute("INSERT INTO heartbeats VALUES (?,?,?)", (beat, 0, 0))
    c.commit()
    c.close()
    return str(path)


def idle_rows(n=5):
    """One decision per minute for the last n minutes, each done 3 s after its minute's start."""
    return [(M0 - 60 * i, M0 - 60 * i + 3.0, "ok") for i in range(1, n + 1)]


def test_idle_collector_opens(tmp_path):
    db = make_db(tmp_path / "c.sqlite", idle_rows())
    assert cg.CollectorGate(db).is_open(NOW) == (True, "collector idle")


def test_no_decisions_but_fresh_heartbeat_opens(tmp_path):
    assert cg.CollectorGate(make_db(tmp_path / "c.sqlite")).is_open(NOW)[0] is True


def test_two_decisions_in_one_minute_close(tmp_path):
    rows = idle_rows() + [(M0 - 60 + 1, M0 - 60 + 4.0, "ok")]
    ok, why = cg.CollectorGate(make_db(tmp_path / "c.sqlite", rows)).is_open(NOW)
    assert not ok and "busy" in why


def test_decision_ending_at_second_25_closes(tmp_path):
    rows = idle_rows() + [(M0 - 600 + 60, M0 - 600 + 60 + 25.0, "ok")]
    ok, why = cg.CollectorGate(make_db(tmp_path / "c.sqlite", rows)).is_open(NOW)
    assert not ok and "second 25" in why


def test_late_status_closes(tmp_path):
    rows = idle_rows() + [(M0 - 360, M0 - 357.0, "late")]
    ok, why = cg.CollectorGate(make_db(tmp_path / "c.sqlite", rows)).is_open(NOW)
    assert not ok and "late" in why


def test_old_busy_rows_outside_lookback_are_ignored(tmp_path):
    rows = idle_rows() + [(M0 - 1200, M0 - 1200 + 40.0, "late"), (M0 - 1200, M0 - 1200 + 5.0, "ok")]
    assert cg.CollectorGate(make_db(tmp_path / "c.sqlite", rows)).is_open(NOW)[0] is True


def test_open_decision_without_decision_ts_closes(tmp_path):
    rows = idle_rows() + [(M0 - 120, None, "ok")]
    assert cg.CollectorGate(make_db(tmp_path / "c.sqlite", rows)).is_open(NOW)[0] is False


def test_stale_heartbeat_closes(tmp_path):
    db = make_db(tmp_path / "c.sqlite", idle_rows(), beat=NOW - 181)
    assert cg.CollectorGate(db).is_open(NOW) == (False, "collector silent")


def test_no_heartbeat_closes(tmp_path):
    db = make_db(tmp_path / "c.sqlite", idle_rows(), beat=None)
    assert cg.CollectorGate(db).is_open(NOW) == (False, "collector silent")


def test_missing_db_closes_and_is_not_created(tmp_path):
    p = tmp_path / "nope.sqlite"
    assert cg.CollectorGate(p).is_open(NOW) == (False, "no collector db")
    assert not p.exists() and list(tmp_path.iterdir()) == []


def test_sqlite_error_closes_with_class_name(tmp_path):
    p = tmp_path / "junk.sqlite"
    p.write_bytes(b"this is not a database" * 50)
    ok, why = cg.CollectorGate(p).is_open(NOW)
    assert not ok and "DatabaseError" in why
    q = tmp_path / "empty.sqlite"
    sqlite3.connect(q).close()                                 # a database with no tables
    ok, why = cg.CollectorGate(q).is_open(NOW)
    assert not ok and "OperationalError" in why


def test_connect_failure_closes(tmp_path):
    db = make_db(tmp_path / "c.sqlite", idle_rows())

    def boom(path):
        raise sqlite3.OperationalError("locked")
    ok, why = cg.CollectorGate(db, connect=boom).is_open(NOW)
    assert not ok and "OperationalError" in why


def test_forced_rows_are_skipped_when_the_column_exists(tmp_path):
    rows = [(*r, 0) for r in idle_rows()] + [(M0 - 60 + 1, M0 - 60 + 40.0, "late", 1)]
    db = make_db(tmp_path / "c.sqlite", rows, forced_col=True)
    assert cg.CollectorGate(db).is_open(NOW)[0] is True


def test_default_connection_is_read_only_and_leaves_the_file_alone(tmp_path):
    db = make_db(tmp_path / "c.sqlite", idle_rows())
    before = os.stat(db).st_mtime_ns
    conn = cg.connect_ro(db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO heartbeats VALUES (1, 1, 1)")
    conn.close()
    seen = []

    def spy(path):
        seen.append(cg.connect_ro(path))
        return seen[-1]
    assert cg.CollectorGate(db, connect=spy).is_open(NOW)[0] is True
    with pytest.raises(sqlite3.ProgrammingError):               # the gate closed its connection
        seen[0].execute("SELECT 1")
    assert os.stat(db).st_mtime_ns == before
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0] == 1
    c.close()


class Sim:
    def __init__(self, t):
        self.t, self.sleeps = t, []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class Inner:
    def __init__(self):
        self.calls = 0

    def wait(self):
        self.calls += 1


class FakeGate:
    def __init__(self, answers):
        self.answers, self.asked = list(answers), []

    def is_open(self, now):
        self.asked.append(now)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def test_gate_asked_once_per_minute_when_open():
    sim, gate, inner = Sim(M0 + 45.0), FakeGate([(True, "collector idle")]), Inner()
    p = cg.GatedPacer(inner, gate, sleep=sim.sleep, clock=sim.clock)
    for _ in range(5):
        p.wait()
        sim.t += 1.0
    assert len(gate.asked) == 1 and inner.calls == 5 and sim.sleeps == []
    sim.t = M0 + 60 + 45.0
    p.wait()
    assert len(gate.asked) == 2


def test_closed_gate_sleeps_to_next_minute_then_returns_once_open():
    sim, inner = Sim(M0 + 45.0), Inner()
    gate = FakeGate([(False, "collector busy"), (False, "collector busy"), (True, "collector idle")])
    logged = []
    p = cg.GatedPacer(inner, gate, sleep=sim.sleep, clock=sim.clock, log=logged.append)
    p.wait()
    assert sim.sleeps == [pytest.approx(15.5), pytest.approx(60.0)]       # to M0+60.5, then to M0+120.5
    assert logged == ["collector busy", "collector busy"]                 # once per closed minute
    assert len(gate.asked) == 3 and inner.calls == 3
    assert [int(a // 60) for a in gate.asked] == [int(M0 // 60) + i for i in range(3)]


def test_closed_gate_never_returns_while_closed_and_log_is_optional():
    sim, inner = Sim(M0 + 5.0), Inner()
    gate = FakeGate([(False, "x")] * 3 + [(True, "ok")])
    cg.GatedPacer(inner, gate, sleep=sim.sleep, clock=sim.clock).wait()
    assert len(gate.asked) == 4 and len(sim.sleeps) == 3


def test_a_short_sleep_inside_a_closed_minute_does_not_requery_or_relog():
    sim, inner = Sim(M0 + 5.0), Inner()
    gate = FakeGate([(False, "busy"), (True, "ok")])
    logged = []
    orig = sim.sleep
    state = {"n": 0}

    def short_first(s):                                        # the first sleep returns early, still in the minute
        state["n"] += 1
        orig(s / 2 if state["n"] == 1 else s)
    p = cg.GatedPacer(inner, gate, sleep=short_first, clock=sim.clock, log=logged.append)
    p.wait()
    assert logged == ["busy"] and len(gate.asked) == 2


class CrossingInner:
    """An inner pacer whose wait carries the clock over a minute boundary."""
    def __init__(self, sim, step):
        self.sim, self.step = sim, step

    def wait(self):
        self.sim.t += self.step


def test_answer_belongs_to_the_minute_wait_returns_in():
    # minute M0 is open; inner.wait() crosses into M0+60, which is closed: wait must not return on M0's answer
    sim = Sim(M0 + 54.0)
    gate = FakeGate([(True, "collector idle"), (False, "collector busy"), (True, "collector idle")])
    p = cg.GatedPacer(CrossingInner(sim, 0.5), gate, sleep=sim.sleep, clock=sim.clock)
    p.wait()                                   # returns at M0+54.5, open
    sim.t = M0 + 59.8
    p.wait()                                   # inner moves it to M0+60.3: asks again, closed, sleeps, then open
    assert len(gate.asked) == 3 and gate.asked[1] >= M0 + 60
    assert sim.t >= M0 + 120


def test_null_forced_rows_count_and_future_rows_are_ignored(tmp_path):
    import time as _t
    now = _t.time()
    db = tmp_path / "c.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE heartbeats (ts REAL, model_calls INTEGER, paused INTEGER)")
    c.execute("CREATE TABLE decisions (slot_ts REAL, decision_ts REAL, status TEXT, forced INTEGER)")
    c.execute("INSERT INTO heartbeats VALUES (?, 1, 0)", (now - 10,))
    m = int((now - 120) // 60) * 60
    c.execute("INSERT INTO decisions VALUES (?, ?, 'ok', NULL)", (m, m + 30))      # busy, forced unknown
    c.commit()
    assert cg.CollectorGate(db).is_open(now)[0] is False
    c.execute("DELETE FROM decisions")
    f = int((now + 300) // 60) * 60
    c.execute("INSERT INTO decisions VALUES (?, ?, 'ok', 0)", (f, f + 30))         # after `now`: not counted
    c.commit()
    c.close()
    assert cg.CollectorGate(db).is_open(now)[0] is True
