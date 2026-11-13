"""The watchdog: a fixed list of conditions, each raised once, cleared once, in a file the owner can read.

Every outside thing is a stand-in: the folders are a temporary tree, `launchctl` and `osascript` are never started
(the program launcher is replaced for the whole module), the clock, the free disk space and the exchange calendar
are given to the checks. No real notification is shown, nothing real is read, nothing is created in the repository.
All secrets and account numbers here are made up."""
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from valedia_trader.core import alerts as al
from valedia_trader.core import secrets as s
from valedia_trader.execution_and_risk.orders import OrderLedger
from valedia_trader.research_loop import orderbook_loop as ol
from valedia_trader.research_loop.paper import Journal

NOW = datetime(2026, 11, 13, 20, 0, tzinfo=timezone.utc).timestamp()      # a Friday; its last completed session is the 12th
LAST_SESSION = "2026-11-12"
LABELS = ("com.valedia.paper.daily", "com.valedia.orderbook-collector", "com.valedia.picks.hourly",
          "com.valedia.research-loop.weekly")
PLANTED = "MadeUpSecretValueQ7x9Zk2LmN4"
ACCOUNT = "123456789012345"


@pytest.fixture(autouse=True)
def no_real_launch(monkeypatch):
    """No test in this file may start a program: a launch not replaced by a spy fails the test."""
    def refuse(argv, **kw):
        raise AssertionError(f"a test tried to start {argv[:1]}")
    monkeypatch.setattr(al.subprocess, "run", refuse)


def launchctl_text(rows):
    return "PID\tStatus\tLabel\n" + "\n".join(f"{pid}\t{status}\t{label}" for pid, status, label in rows) + "\n"


HEALTHY_JOBS = [("-", "0", LABELS[0]), ("512", "0", LABELS[1]), ("-", "0", LABELS[2]), ("-", "0", LABELS[3]),
                ("-", "78", "com.apple.something")]


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class World:
    """A healthy small system in a temporary tree, and a way to break and mend each thing the watchdog watches."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.root = tmp_path / "repo"
        self.logs = tmp_path / "logs"
        self.agents = tmp_path / "agents"
        self.halt = tmp_path / "state" / "halt.json"
        for d in (self.root / "journal", self.root / "memory", self.root / "artifacts", self.logs, self.agents, self.halt.parent):
            d.mkdir(parents=True, exist_ok=True)
        self.jobs = list(HEALTHY_JOBS)
        self.free = 50 * 10 ** 9
        self.launchctl_error = None
        for label in LABELS:
            (self.agents / f"{label}.plist").write_text("<plist/>")
        self.journal = Journal(self.root / "journal")
        self.journal.write(LAST_SESSION, {"type": "summary", "equity": 100000.0})
        self.write_picks_log(end_ago=1200)
        self.write_heartbeat(ago=60)
        self.ledger_path = self.root / "memory" / "orders-webull-sandbox.sqlite"
        self.make_ledger()

    # ---- the stand-ins the checks are given
    def env(self):
        def launchctl():
            if self.launchctl_error:
                raise OSError(self.launchctl_error)
            return launchctl_text(self.jobs)
        return al.Env(root=self.root, now=NOW, halt_file=self.halt, logs_dir=self.logs, launch_agents_dir=self.agents,
                      launchctl=launchctl, free_bytes=lambda p: self.free,
                      sessions=lambda: pd.bdate_range("2026-01-01", "2027-12-31"), environ={})

    @property
    def alerts_file(self):
        return self.root / "artifacts" / "owner" / "alerts.jsonl"

    def lines(self):
        return [json.loads(x) for x in self.alerts_file.read_text().splitlines()] if self.alerts_file.exists() else []

    # ---- the order ledger
    def make_ledger(self):
        led = OrderLedger(self.ledger_path, clock=lambda: NOW)
        led.reconcile(_Venue(), {})
        led.close()

    def unknown_order(self, tag=None):
        self.orders_made = getattr(self, "orders_made", 0) + 1
        self.last_tag = tag = tag or f"paper-XLE-{self.orders_made}"
        led = OrderLedger(self.ledger_path, not_sent=(), clock=lambda: NOW)
        led.intend(tag, "XLE", 5.0, 60.0)
        led.submit_once(_Venue(raises=True), tag)
        led.close()

    def settle_order(self):
        led = OrderLedger(self.ledger_path, clock=lambda: NOW)
        led.settle_by_hand(self.last_tag, "owner", "looked at the broker")
        led.reconcile(_Venue(), {})
        led.close()

    # ---- the other sources
    def write_picks_log(self, end_ago=None, extra=(), start_ago=None):
        lines = []
        if end_ago is not None:
            lines += [f"{iso(NOW - end_ago - 300)} start", "requests: 3", *extra, f"{iso(NOW - end_ago)} end"]
        elif start_ago is not None:
            lines += [f"{iso(NOW - start_ago)} start", *extra]
        (self.logs / "valedia-picks-hourly.log").write_text("\n".join(lines) + "\n")

    def write_heartbeat(self, ago):
        store = ol.Store(self.root / "memory" / "orderbook_loop.sqlite")
        store.insert("heartbeats", {**ol.stamp("", NOW - ago), "watched": 20, "kalshi_requests": 3, "kalshi_failures": 0,
                                    "model_calls": 1, "paused": 0})
        store.close()

    def write_step(self, status, stage=None, reason=None, day="2026-11-13", venue="webull-sandbox"):
        self.journal.write(day, {"type": "order_step", "venue": venue, "event": "summary", "status": status, "stage": stage,
                                 "reason": reason, "orders_sent": 0, "sent": [], "refused": [], "left_over": []})


class _Venue:
    name = "fake"

    def __init__(self, raises=False):
        self.raises = raises

    def submit(self, symbol, units, tag, price=None, limit_price=None):
        raise TimeoutError("read timed out")

    def order_status(self, tag):
        return None

    def positions(self):
        return {}


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def watch(w, **kw):
    return al.watch(w.env(), **kw)


def keys(result):
    return sorted(a.key for a in result.raised)


# ----------------------------------------------------------------------------------------- a healthy system

def test_a_healthy_system_raises_nothing(world):
    result = watch(world)
    assert result.raised == [] and result.cleared == [] and result.open == [] and result.failures == []
    assert world.lines() == []


# --------------------------------------------------------------------- each condition: raised once, kept, cleared once

def halt_on(w): w.halt.write_text("{}")
def halt_off(w): w.halt.unlink()
def latch_on(w): (w.root / "journal" / "kill_switch.json").write_text("{}")
def latch_off(w): (w.root / "journal" / "kill_switch.json").unlink()
def order_on(w): w.unknown_order()
def order_off(w): w.settle_order()
def step_on(w):
    w.step_n = getattr(w, "step_n", 0) + 1
    w.write_step("stopped", "reconcile", "the ledger, the state file or the journal failed", day=f"2026-11-{10 + 2 * w.step_n}")
def step_off(w): w.write_step("ok", day=f"2026-11-{11 + 2 * w.step_n}")
def job_gone_on(w): w.jobs = [j for j in w.jobs if j[2] != LABELS[2]]
def job_gone_off(w): w.jobs = list(HEALTHY_JOBS)
def job_exit_on(w): w.jobs = [("-", "78", LABELS[3]) if j[2] == LABELS[3] else j for j in w.jobs]
def job_exit_off(w): w.jobs = list(HEALTHY_JOBS)
def beat_on(w):
    (w.root / "memory" / "orderbook_loop.sqlite").unlink()
    w.write_heartbeat(ago=601)
def beat_off(w): w.write_heartbeat(ago=30)
def no_end_on(w): w.write_picks_log(end_ago=3 * 3600 + 5)
def no_end_off(w): w.write_picks_log(end_ago=600)
def failed_on(w): w.write_picks_log(end_ago=600, extra=["schedule failed (see picks-hourly.err)"])
def failed_off(w): w.write_picks_log(end_ago=600, extra=["schedule stored"])
def damage_on(w):
    with (w.root / "journal" / "2026-11-12.jsonl").open("a") as fh:
        fh.write('{"type": "decision", "br\n')
def damage_off(w):
    f = w.root / "journal" / "2026-11-12.jsonl"
    f.write_text("".join(x for x in f.read_text().splitlines(True) if x.startswith('{"timestamp"')))
def paper_on(w): (w.root / "journal" / f"{LAST_SESSION}.jsonl").unlink()
def paper_off(w): w.journal.write(LAST_SESSION, {"type": "summary", "equity": 100000.0})
def disk_on(w): w.free = 5 * 10 ** 9 - 1
def disk_off(w): w.free = 5 * 10 ** 9

CONDITIONS = {
    "halt file": (halt_on, halt_off, "switch:halt"),
    "latch": (latch_on, latch_off, "switch:latch"),
    "order ledger blocked": (order_on, order_off, "orders:orders-webull-sandbox"),
    "order step stopped": (step_on, step_off, "step-stopped:webull-sandbox"),
    "job not loaded": (job_gone_on, job_gone_off, f"job:{LABELS[2]}"),
    "job exit code": (job_exit_on, job_exit_off, f"job:{LABELS[3]}"),
    "collector heartbeat": (beat_on, beat_off, "collector:heartbeat"),
    "no end line": (no_end_on, no_end_off, "picks:no-end"),
    "pick run failed": (failed_on, failed_off, "picks:failed"),
    "damaged journal line": (damage_on, damage_off, "journal:damaged"),
    "paper step missing": (paper_on, paper_off, "paper:no-entry"),
    "disk low": (disk_on, disk_off, "disk:low"),
}


@pytest.mark.parametrize("name", sorted(CONDITIONS))
def test_a_condition_is_raised_once_kept_quiet_while_open_and_cleared_once(world, name):
    on, off, key = CONDITIONS[name]
    on(world)
    first = watch(world)
    assert keys(first) == [key], f"{name}: {[a.key for a in first.raised]}"
    assert [x["key"] for x in world.lines()] == [key]
    assert first.open and first.open[0]["key"] == key and first.failures == []
    again = watch(world)                                           # still true: not raised again, no new line
    assert again.raised == [] and again.cleared == [] and [x["key"] for x in again.open] == [key]
    assert len(world.lines()) == 1
    off(world)
    cleared = watch(world)                                         # no longer true: one cleared line
    assert [c.key for c in cleared.cleared] == [key] and cleared.open == []
    lines = world.lines()
    assert [(x["key"], x["level"]) for x in lines][1:] == [(key, "cleared")]
    quiet = watch(world)
    assert quiet.raised == [] and quiet.cleared == [] and len(world.lines()) == 2
    on(world)                                                      # true again after clearing: a new alert
    assert keys(watch(world)) == [key]


def test_a_line_has_exactly_time_level_key_title_detail(world):
    halt_on(world)
    watch(world)
    (line,) = world.lines()
    assert sorted(line) == ["detail", "key", "level", "time", "title"]
    assert line["level"] in ("warning", "critical") and line["time"].endswith("Z") and line["title"] and line["detail"]


def test_the_order_ledger_alert_says_why_and_a_clean_ledger_does_not_alert(world):
    assert watch(world).raised == []
    order_on(world)
    (a,) = watch(world).raised
    assert "unknown" in a.detail and a.level == "critical"


def test_an_old_reconcile_alone_does_not_block_the_watch_overnight(world):
    """The order step reconciles first thing each run, so a reconcile that is hours old is not a problem."""
    env = world.env()
    env.now = NOW + 20 * 3600
    assert [a for a in al.watch(env).raised if a.key.startswith("orders:")] == []


def test_the_stopped_order_step_names_its_stage_and_reason(world):
    step_on(world)
    (a,) = watch(world).raised
    assert "reconcile" in a.title and "the ledger, the state file or the journal failed" in a.detail


@pytest.mark.parametrize("reason", ["outside the regular session window 09:35 to 15:45 New York time (it is 06:10)",
                                    "2026-11-26 (Thursday) is not a trading day", "2026-11-26 is a market holiday"])
def test_a_stop_for_the_market_being_closed_is_ordinary_and_raises_nothing(world, reason):
    world.write_step("stopped", "session", reason)
    assert watch(world).raised == []


@pytest.mark.parametrize("stage, reason", [("session", "another order step is running on this venue's state file"),
                                           ("session", "the exchange calendar could not be loaded: OSError"),
                                           ("environment", "outside the regular session window"),    # only stage session is ordinary
                                           ("send", "2026-11-26 is a market holiday")])
def test_every_other_stop_still_raises(world, stage, reason):
    world.write_step("stopped", stage, reason)
    assert keys(watch(world)) == ["step-stopped:webull-sandbox"]


def test_an_ordinary_stop_after_a_real_one_clears_the_alert(world):
    world.write_step("stopped", "send", "the venue refused the session", day="2026-11-12")
    assert keys(watch(world)) == ["step-stopped:webull-sandbox"]
    world.write_step("stopped", "session", "outside the regular session window 09:35 to 15:45", day="2026-11-13")
    assert [c.key for c in watch(world).cleared] == ["step-stopped:webull-sandbox"]


def test_only_the_last_summary_of_the_day_decides(world):
    world.write_step("stopped", "environment", "the clock is wrong")
    world.write_step("ok")                                         # a later run the same day went through
    assert watch(world).raised == []
    world.write_step("stopped", "send", "the venue refused the session")
    assert keys(watch(world)) == ["step-stopped:webull-sandbox"]


def test_two_venues_are_watched_apart(world):
    world.write_step("stopped", "send", "busy", venue="webull-sandbox")
    world.write_step("ok", venue="other-venue")
    assert keys(watch(world)) == ["step-stopped:webull-sandbox"]


def test_a_job_that_is_loaded_but_not_installed_is_still_checked_and_a_running_job_with_no_code_yet_is_fine(world):
    world.jobs = world.jobs + [("-", "-", "com.valedia.extra.job")]          # no exit code yet: not a failure
    assert watch(world).raised == []
    world.jobs = world.jobs + [("-", "-15", "com.valedia.extra2.job")]
    assert keys(watch(world)) == ["job:com.valedia.extra2.job"]


def test_a_collector_with_no_record_at_all_is_an_alert(world):
    (world.root / "memory" / "orderbook_loop.sqlite").unlink()
    (a,) = watch(world).raised
    assert a.key == "collector:heartbeat" and "no heartbeat" in a.detail.lower()


def test_a_heartbeat_exactly_ten_minutes_old_is_not_yet_late(world):
    (world.root / "memory" / "orderbook_loop.sqlite").unlink()
    world.write_heartbeat(ago=600)
    assert watch(world).raised == []


def test_a_missing_pick_log_is_an_alert_and_a_run_still_going_is_not(world):
    (world.logs / "valedia-picks-hourly.log").unlink()
    (a,) = watch(world).raised
    assert a.key == "picks:no-end" and "missing" in a.detail


def test_a_run_still_going_does_not_hide_the_end_line_of_the_one_before(world):
    (world.logs / "valedia-picks-hourly.log").write_text(
        f"{iso(NOW - 7500)} start\nschedule stored\n{iso(NOW - 7200)} end\n{iso(NOW - 120)} start\nrequests: 3\n")
    assert watch(world).raised == []                                # the last end line is two hours old: within three
    (world.logs / "valedia-picks-hourly.log").write_text(
        f"{iso(NOW - 12000)} start\nschedule stored\n{iso(NOW - 11700)} end\n{iso(NOW - 120)} start\nrequests: 3\n")
    assert keys(watch(world)) == ["picks:no-end"]                   # a run that has not ended does not count as an end


def test_a_pick_count_that_says_failed_zero_is_not_a_failed_run(world):
    world.write_picks_log(end_ago=600, extra=["failed: 0", "thread set aside (failed 3 times in a row): abc"])
    assert watch(world).raised == []
    world.write_picks_log(end_ago=600, extra=["match failed (see picks-hourly.err)"])
    assert keys(watch(world)) == ["picks:failed"]


def test_only_the_last_pick_run_counts_for_a_failure(world):
    log = world.logs / "valedia-picks-hourly.log"
    log.write_text(f"{iso(NOW - 7200)} start\nschedule failed (see picks-hourly.err)\n{iso(NOW - 7000)} end\n"
                   f"{iso(NOW - 600)} start\nschedule stored\n{iso(NOW - 500)} end\n")
    assert watch(world).raised == []


def test_a_huge_log_is_read_from_its_end(world):
    log = world.logs / "valedia-picks-hourly.log"
    old = f"{iso(NOW - 99999)} start\nschedule failed\n{iso(NOW - 99990)} end\n" * 40000
    log.write_text(old + f"{iso(NOW - 600)} start\nschedule stored\n{iso(NOW - 500)} end\n")
    assert watch(world).raised == []


def test_the_last_trading_day_comes_from_the_exchange_calendar(world):
    sessions = al.exchange_sessions
    thursday_before_thanksgiving = pd.Timestamp("2026-11-25")
    for now, expected in (("2026-11-14 12:00", "2026-11-13"), ("2026-11-16 12:00", "2026-11-13"),
                          ("2026-11-27 09:00", "2026-11-25"), ("2026-11-13 15:00", "2026-11-12")):
        ts = pd.Timestamp(now, tz="UTC").timestamp()
        assert al.last_trading_day(ts, sessions()) == expected, now
    assert thursday_before_thanksgiving in sessions()


def test_a_paper_entry_of_another_kind_does_not_stand_in_for_the_paper_step(world):
    (world.root / "journal" / f"{LAST_SESSION}.jsonl").unlink()
    world.journal.write(LAST_SESSION, {"type": "order_step", "venue": "x", "event": "summary", "status": "ok"})
    assert keys(watch(world)) == ["paper:no-entry"]


def test_damage_older_than_thirty_days_is_not_raised(world):
    old = world.root / "journal" / "2026-09-01.jsonl"
    old.write_text('{"half a line\n')
    assert watch(world).raised == []
    damage_on(world)
    (a,) = watch(world).raised
    assert "2026-11-12.jsonl line" in a.detail


def test_two_conditions_at_once_are_two_alerts_and_one_clearing_leaves_the_other(world):
    halt_on(world)
    disk_on(world)
    assert keys(watch(world)) == ["disk:low", "switch:halt"]
    disk_off(world)
    r = watch(world)
    assert [c.key for c in r.cleared] == ["disk:low"] and [a["key"] for a in r.open] == ["switch:halt"]


# --------------------------------------------------------------------------------------------- failures of the watch

def test_a_source_that_cannot_be_asked_fails_the_watch_without_raising_or_clearing_its_alerts(world):
    job_gone_on(world)
    assert keys(watch(world)) == [f"job:{LABELS[2]}"]
    world.launchctl_error = "launchctl is gone"
    halt_on(world)
    r = watch(world)
    assert keys(r) == ["switch:halt"]                              # the other checks still ran
    assert r.failures and "launchctl" in r.failures[0]
    assert r.cleared == []                                         # a job alert is not cleared by a check that could not run
    assert any(a["key"] == f"job:{LABELS[2]}" for a in r.open)


def test_an_unreadable_ledger_fails_only_the_ledger_check(world):
    world.ledger_path.write_text("this is not a database")
    halt_on(world)
    r = watch(world)
    assert keys(r) == ["switch:halt"] and any("orders" in f for f in r.failures)


def test_a_ledger_of_another_layout_is_a_failure_not_an_alert(world):
    world.ledger_path.unlink()
    db = sqlite3.connect(world.ledger_path)
    db.execute("CREATE TABLE orders (tag TEXT)")
    db.execute("PRAGMA user_version = 99")
    db.commit()
    db.close()
    r = watch(world)
    assert r.raised == [] and any("layout" in f for f in r.failures)


def test_a_busy_collector_record_is_a_failure_not_an_alert(world, monkeypatch):
    def busy(path, timeout=2.0):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(al, "open_readonly", busy)
    r = watch(world)
    assert any("busy" in f for f in r.failures) and "collector:heartbeat" not in keys(r)


# --------------------------------------------------------------------------------------------- the alert file

def test_the_alert_file_survives_damaged_and_torn_lines(world):
    world.alerts_file.parent.mkdir(parents=True, exist_ok=True)
    good = {"time": "2026-11-13T00:00:00Z", "level": "warning", "key": "disk:low", "title": "t", "detail": "d"}
    world.alerts_file.write_text('not json\n' + json.dumps(good) + '\n[1, 2]\n{"half')
    r = watch(world)                                                # disk:low was open from before and is not true now
    assert r.raised == [] and [c.key for c in r.cleared] == ["disk:low"] and r.open == []
    assert world.alerts_file.read_text().endswith("\n")
    for line in world.alerts_file.read_text().splitlines()[-1:]:
        assert json.loads(line)["level"] == "cleared"


def test_open_alerts_are_read_back_by_the_list_command(world, capsys):
    halt_on(world)
    watch(world)
    code = al.main(["list"], env=world.env())
    out = capsys.readouterr().out
    assert code == 1 and "switch:halt" in out and "critical" in out
    halt_off(world)
    watch(world)
    code = al.main(["list"], env=world.env())
    assert code == 0 and "no open alert" in capsys.readouterr().out.lower()


def test_list_with_no_file_prints_none_and_exits_zero(world, capsys):
    assert al.main(["list"], env=world.env()) == 0
    assert "no open alert" in capsys.readouterr().out.lower()
    assert not world.alerts_file.exists()                          # listing creates nothing


def test_the_watch_changes_nothing_but_the_alert_file(world):
    def snapshot():
        return {str(p.relative_to(world.tmp)): (p.stat().st_size, p.read_bytes() if p.suffix != ".sqlite" else b"")
                for p in sorted(world.tmp.rglob("*"))
                if p.is_file() and p != world.alerts_file and not p.name.endswith(("-wal", "-shm"))}
    halt_on(world)
    before = snapshot()
    watch(world)
    assert snapshot() == before


def test_exit_codes_zero_one_two(world, capsys):
    assert al.main(["watch"], env=world.env()) == 0
    halt_on(world)
    assert al.main(["watch"], env=world.env()) == 1
    assert al.main(["watch"], env=world.env()) == 1                # still open: still 1, nothing new
    halt_off(world)
    assert al.main(["watch"], env=world.env()) == 0
    world.launchctl_error = "boom"
    capsys.readouterr()
    assert al.main(["watch"], env=world.env()) == 2
    assert "launchctl" in capsys.readouterr().err


# --------------------------------------------------------------------------------------------- secrets and numbers

def test_nothing_secret_and_no_account_number_reaches_the_alert_file(world):
    s.remember(PLANTED)
    world.unknown_order(tag=f"paper-{PLANTED}")                                        # a secret inside an order tag
    world.write_step("stopped", "send", f"the venue said {PLANTED} for account {ACCOUNT}")    # inside a journal reason
    world.jobs = world.jobs + [("-", "9", f"com.valedia.{PLANTED}.job")]                # inside a job label
    world.write_picks_log(end_ago=600, extra=[f"match failed for {PLANTED} account {ACCOUNT}"])   # inside a log line
    r = watch(world)
    assert len(r.raised) >= 4
    text = world.alerts_file.read_text()
    assert PLANTED not in text and PLANTED.lower() not in text.lower()
    assert ACCOUNT not in text and ACCOUNT[:10] not in text
    assert "<secret>" in text or "<number>" in text


def test_a_secret_never_reaches_the_notification(world, monkeypatch):
    s.remember(PLANTED)
    world.write_step("stopped", "send", f"the venue said {PLANTED}")
    spy = Spy()
    monkeypatch.setattr(al.subprocess, "run", spy)
    watch(world, senders=[al.MacNotifier()])
    assert spy.calls and all(PLANTED not in " ".join(c) for c in spy.calls)


# --------------------------------------------------------------------------------------------- notifications

class Spy:
    """Stands in for subprocess.run: records the argument list and the env, starts nothing."""

    def __init__(self, stdout="", returncode=0):
        self.calls, self.envs, self.stdout, self.returncode = [], [], stdout, returncode

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        self.envs.append(kw.get("env", "MISSING"))
        return subprocess.CompletedProcess(argv, self.returncode, stdout=self.stdout, stderr="")


def test_a_notification_per_new_alert_with_the_text_as_an_argument_never_in_the_script(world, monkeypatch):
    spy = Spy()
    monkeypatch.setattr(al.subprocess, "run", spy)
    halt_on(world)
    disk_on(world)
    r = watch(world, senders=[al.MacNotifier()])
    assert len(r.raised) == 2 and len(spy.calls) == 2
    for argv, alert in zip(spy.calls, r.raised, strict=True):
        assert argv[0] == "osascript"
        script_parts = [argv[i + 1] for i, x in enumerate(argv) if x == "-e"]
        assert script_parts and "on run" in script_parts[0]
        for part in script_parts:                                    # the script source holds none of the alert's words
            assert alert.title not in part and alert.detail not in part and alert.key not in part
        assert argv[-2] == "Valedia" and argv[-1] == f"{alert.title}: {alert.detail}"[:200]
        assert not argv[-1].startswith("-") and not argv[-2].startswith("-")
    again = watch(world, senders=[al.MacNotifier()])                 # not raised again: not shown again
    assert again.raised == [] and len(spy.calls) == 2
    halt_off(world)
    watch(world, senders=[al.MacNotifier()])                         # a cleared line is not a notification
    assert len(spy.calls) == 2


def test_a_hostile_alert_text_stays_an_argument(monkeypatch):
    spy = Spy()
    monkeypatch.setattr(al.subprocess, "run", spy)
    hostile = al.Alert("x:y", "warning", 'a" & do shell script "touch /tmp/x" & "', "-e 'display dialog \"hi\"' \\ $(id) `id`")
    al.MacNotifier().send(hostile)
    (argv,) = spy.calls
    script_parts = [argv[i + 1] for i, x in enumerate(argv) if x == "-e"]
    assert all("shell script" not in p and "display dialog" not in p for p in script_parts)
    assert not argv[-1].startswith("-") and not argv[-2].startswith("-")


def test_the_notifier_and_launchctl_start_their_programs_with_the_reduced_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("VALEDIA_ENV_FILE", str(tmp_path / "no.env"))
    secret_name = "MADE_UP_BROKER_KEY"
    monkeypatch.setenv(secret_name, PLANTED)
    s.remember(PLANTED)
    spy = Spy(stdout="PID\tStatus\tLabel\n")
    monkeypatch.setattr(al.subprocess, "run", spy)
    al.launchctl_text()
    al.MacNotifier().send(al.Alert("k", "warning", "title", "detail"))
    assert [c[0] for c in spy.calls] == ["launchctl", "osascript"]
    for env in spy.envs:
        assert isinstance(env, dict) and secret_name not in env and "PATH" in env and "HOME" in env


def test_a_notification_that_cannot_be_shown_fails_the_watch_but_keeps_the_alert(world, monkeypatch, capsys):
    spy = Spy(returncode=1)
    monkeypatch.setattr(al.subprocess, "run", spy)
    halt_on(world)
    r = watch(world, senders=[al.MacNotifier()])
    assert keys(r) == ["switch:halt"] and r.failures and "notification" in r.failures[0] and len(world.lines()) == 1
    world.halt.unlink()
    watch(world)
    halt_on(world)
    assert al.main(["watch", "--notify"], env=world.env()) == 2        # a new alert whose notification failed: exit 2
    assert "notification" in capsys.readouterr().err and len(world.lines()) == 3


def test_a_sender_is_any_object_with_send(world):
    got = []

    class Phone:
        def send(self, alert):
            got.append(alert.key)

    halt_on(world)
    watch(world, senders=[Phone()])
    assert got == ["switch:halt"]


def test_main_notify_uses_the_mac_notifier_once_per_new_alert(world, monkeypatch):
    spy = Spy()
    monkeypatch.setattr(al.subprocess, "run", spy)
    halt_on(world)
    assert al.main(["watch", "--notify"], env=world.env()) == 1
    assert len(spy.calls) == 1
    assert al.main(["watch", "--notify"], env=world.env()) == 1
    assert len(spy.calls) == 1
    assert al.main(["watch"], env=world.env()) == 1                  # without the flag: no notification at all
    assert len(spy.calls) == 1


def test_the_default_environment_points_at_the_repository(tmp_path):
    env = al.Env.default(tmp_path)
    assert env.root == tmp_path and env.halt_file.name == "halt.json"
    assert env.logs_dir == Path.home() / "Library" / "Logs"
    assert env.launch_agents_dir == Path.home() / "Library" / "LaunchAgents"
    assert env.free_bytes(tmp_path) > 0 and callable(env.launchctl) and len(env.sessions()) > 1000
