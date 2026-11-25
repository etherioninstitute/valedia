"""The watchdog: a fixed list of conditions, each raised once, cleared once, in a file the owner can read.

Every outside thing is a stand-in: the folders are a temporary tree, `launchctl` and `osascript` are never started
(the program launcher is replaced for the whole module), the clock, the free disk space and the exchange calendar
are given to the checks. No real notification is shown, nothing real is read, nothing is created in the repository.
All secrets and account numbers here are made up."""
import fcntl
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
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
        self.write_weekly_log(ran_ago=86400)
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

    def write_weekly_log(self, ran_ago=None, lines=()):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW - ran_ago)) if ran_ago is not None else None
        body = ([f"=== {stamp} weekly research loop", "cycle output"] if stamp else []) + list(lines)
        (self.logs / "valedia-research-loop.log").write_text("\n".join(body) + "\n")

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
def step_on(w): w.write_step("stopped", "reconcile", "the ledger, the state file or the journal failed")    # today, in New York
def step_off(w): w.write_step("done")                                     # a later run of the same day that ended done
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

# one hourly run each, oldest first: F the listing failed, L the listing was rate limited, S the listing came, T the
# listing came and a later thread was rate limited, N said nothing (cut off)
LISTING_404 = "error: listing: the skill script exited 2: HTTP 404 for https://www.reddit.com/user/examplebot.rss?limit=50"
RUN_BODY = {"F": ["requests: 1", "threads followed: 0", "new threads: 0", "new comment versions: 0", LISTING_404, "stopped: error"],
            "L": ["requests: 1", "threads followed: 0", "new threads: 0", "new comment versions: 0", "stopped: rate_limited"],
            "S": ["requests: 2", "threads followed: 1", "new threads: 0", "new comment versions: 8"],
            "T": ["requests: 3", "threads followed: 1", "new threads: 0", "new comment versions: 2", "stopped: rate_limited"],
            "N": []}


def write_runs(w, outcomes):
    lines, n = [], len(outcomes)
    for i, o in enumerate(outcomes):
        start = NOW - 600 - 300 - (n - 1 - i) * 3600                 # an hour apart; the newest ended ten minutes ago
        lines += [f"{iso(start)} start", *RUN_BODY[o], "parsed: 0", "failed: 0", "schedule stored", f"{iso(start + 300)} end"]
    (w.logs / "valedia-picks-hourly.log").write_text("\n".join(lines) + "\n")
def listing_on(w): write_runs(w, "SFFF")
def listing_off(w): write_runs(w, "SFFFS")
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

ORDERSTEP = "com.valedia.orderstep.paper"
def orderstep_on(w): w.jobs = [j for j in w.jobs if j[2] != ORDERSTEP] + [("-", "0", ORDERSTEP)]
def orderstep_off(w): w.jobs = [j for j in w.jobs if j[2] != ORDERSTEP]
def weekly_on(w): w.write_weekly_log(ran_ago=8 * 86400 + 1)
def weekly_off(w): w.write_weekly_log(ran_ago=3600)

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
    "pick listing failing": (listing_on, listing_off, "picks:listing-failing"),
    "damaged journal line": (damage_on, damage_off, "journal:damaged"),
    "paper step missing": (paper_on, paper_off, "paper:no-entry"),
    "disk low": (disk_on, disk_off, "disk:low"),
    "order step not run today": (orderstep_on, orderstep_off, "orderstep:no-run"),
    "weekly loop not run": (weekly_on, weekly_off, "weekly:no-run"),
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
                                    "2026-11-26 (Thursday) is not a trading day", "2026-11-28 (Saturday) is not a trading day"])
def test_a_stop_for_the_market_being_closed_is_ordinary_and_raises_nothing(world, reason):
    world.write_step("stopped", "session", reason)
    assert watch(world).raised == []


@pytest.mark.parametrize("stage, reason", [("session", "another order step is running on this venue's state file"),
                                           ("session", "the exchange calendar could not be loaded: OSError"),
                                           ("environment", "outside the regular session window"),    # only stage session is ordinary
                                           ("send", "2026-11-26 is a market holiday"),
                                           ("session", "2026-11-26 is a market holiday"),           # a word inside a text is not a reason
                                           ("session", "the exchange calendar could not be loaded: the holiday table is missing"),
                                           ("session", "the exchange calendar could not answer for 2026-11-26: holiday"),
                                           ("session", "outside the regular session window 09:35 to 15:45"),     # not the exact text
                                           ("session", "outside the regular session window 09:35 to 15:45 New York time (it is 06:10) "
                                                       "and the calendar could not be loaded")])
def test_every_other_stop_still_raises(world, stage, reason):
    world.write_step("stopped", stage, reason)
    assert keys(watch(world)) == ["step-stopped:webull-sandbox"]


def test_an_ordinary_stop_after_a_real_one_does_not_clear_the_alert(world):
    world.write_step("stopped", "send", "the venue refused the session")
    assert keys(watch(world)) == ["step-stopped:webull-sandbox"]
    world.write_step("stopped", "session", "outside the regular session window 09:35 to 15:45 New York time (it is 06:10)")
    assert watch(world).cleared == []


def test_only_the_last_summary_of_the_day_decides(world):
    world.write_step("stopped", "environment", "the clock is wrong")
    world.write_step("done")                                       # a later run the same day went through
    assert watch(world).raised == []
    world.write_step("stopped", "send", "the venue refused the session")
    assert keys(watch(world)) == ["step-stopped:webull-sandbox"]


def test_two_venues_are_watched_apart(world):
    world.write_step("stopped", "send", "busy", venue="webull-sandbox")
    world.write_step("done", venue="other-venue")
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


# ---------------------------------------------------------------- the pick listing failing hour after hour (1.19)

@pytest.mark.parametrize("outcomes, expect", [
    ("SFF", False),            # two in a row: below the threshold
    ("SFFF", True),            # the third in a row
    ("FFF", True),             # a log whose tail starts inside the streak
    ("SFFFS", False),          # a run that got its listing ends it
    ("SFFSF", False),          # three, but not in a row
    ("SFFNF", True),           # a run cut off before it said anything neither counts nor ends the count
    ("SLLF", True),            # a run whose listing was told to slow down did not get through either
    ("SFFTF", False),          # a run whose listing came and a later thread was rate limited ends the count
    ("TFFF", True),            # ... and only that: the three after it still count
    ("SNNN", False),           # runs that said nothing are not failures
    ("SSSS", False),
])
def test_the_listing_alert_needs_three_runs_in_a_row_without_the_listing(world, outcomes, expect):
    assert al.PICKS_LISTING_FAILS == 3
    write_runs(world, outcomes)
    r = watch(world)
    assert ("picks:listing-failing" in keys(r)) is expect, outcomes
    assert "picks:failed" not in keys(r)                              # "failed: 0" in every run is a count


def test_the_listing_alert_says_how_many_runs_since_when_and_what_the_newest_said(world):
    write_runs(world, "SFFFF")
    watch(world)
    (line,) = [x for x in world.lines() if x["key"] == "picks:listing-failing"]
    oldest = iso(NOW - 600 - 300 - 3 * 3600)
    assert line["level"] == "warning" and line["title"].startswith("P2")
    assert line["detail"].startswith(f"4 runs in a row since {oldest};") and "HTTP 404" in line["detail"]


def test_the_streak_ignores_lines_before_the_first_start_line_of_the_tail():
    lines = ["requests: 2", LISTING_404, f"{iso(NOW - 3600)} start", LISTING_404, f"{iso(NOW - 3500)} end",
             f"{iso(NOW - 60)} start", "requests: 1", "stopped: rate_limited"]
    assert al.listing_failure_streak(lines) == (2, iso(NOW - 3600), "stopped: rate_limited")
    assert al.listing_failure_streak([]) == (0, None, None)


def test_a_watch_of_one_check_neither_clears_nor_shows_nor_reports_another_checks_alert(world):
    halt_on(world)
    assert keys(watch(world)) == ["switch:halt"]                      # open, from a full watch
    halt_off(world)
    listing_on(world)
    got = []

    class Phone:
        def send(self, alert):
            got.append(alert.key)

    r = watch(world, senders=[Phone()], only=["pick-list log"])
    assert keys(r) == ["picks:listing-failing"] and r.cleared == []
    assert [e["key"] for e in r.open] == ["picks:listing-failing"] and got == ["picks:listing-failing"]
    assert [x["key"] for x in world.lines()] == ["switch:halt", "picks:listing-failing"]
    full = watch(world)                                                # a full watch still clears it
    assert [c.key for c in full.cleared] == ["switch:halt"]


def test_a_watch_of_one_check_does_not_raise_another_checks_alert_and_clears_its_own(world):
    halt_on(world)
    listing_on(world)
    r = watch(world, only=["pick-list log"])
    assert keys(r) == ["picks:listing-failing"] and [x["key"] for x in world.lines()] == ["picks:listing-failing"]
    listing_off(world)
    r = watch(world, only=["pick-list log"])
    assert [c.key for c in r.cleared] == ["picks:listing-failing"] and r.open == [] and r.raised == []


def test_only_names_a_check_that_exists(world):
    with pytest.raises(ValueError):
        watch(world, only=["no such check"])
    with pytest.raises(SystemExit) as e:
        al.main(["watch", "--only", "no such check"], env=world.env())
    assert e.value.code == 2 and world.lines() == []


def test_main_watch_only_the_pick_list_check(world, monkeypatch):
    spy = Spy()
    monkeypatch.setattr(al.subprocess, "run", spy)
    halt_on(world)
    listing_on(world)
    assert al.main(["watch", "--only", "pick-list log", "--notify"], env=world.env()) == 1
    assert [x["key"] for x in world.lines()] == ["picks:listing-failing"]
    assert [c[0] for c in spy.calls] == ["osascript"]                 # one notification, and no launchctl: no job check ran
    listing_off(world)
    assert al.main(["watch", "--only", "pick-list log"], env=world.env()) == 0      # the halt file is not this watch's


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



# =====================================================================================================================
# rev 2: what the blind check found
# =====================================================================================================================

PHRASE = "alpha bravo candle delta eagle frost gamma hotel india juliet kilo lemon"        # 12 words of 3 to 8 letters
TRADING = {"BROKER_API_KEY": "Zq8Lm2Xv9Rt4Wc7Yb1Nh5Jd3Kf6Gs0Pa", "BROKER_API_SECRET": "Hn4Tb7Qw2Ze9Ux5Cv8Ml3Kj6Ar1Sd0Fg",
           "WALLET_MNEMONIC": PHRASE, "WEBULL_ACCOUNT_ID": "987654321098", "WEBULL_ENV": "production",
           "WEBULL_BASE_URL": "https://example.invalid/api"}


@pytest.fixture
def trading_file(tmp_path, monkeypatch):
    """A made-up trading file, its path given to the real commands the way the package finds it (VALEDIA_ENV_FILE); the
    tests never call remember(): only the commands' own registration can make its values known."""
    f = tmp_path / "trading.env"
    f.write_text("".join(f"{k}={v}\n" for k, v in TRADING.items()))
    os.chmod(f, 0o600)
    monkeypatch.setenv("VALEDIA_ENV_FILE", str(f))
    for k in TRADING:
        monkeypatch.delenv(k, raising=False)
    return f


def four_words(phrase=PHRASE):
    w = phrase.split()
    return [" ".join(w[i:i + 4]) for i in range(len(w) - 3)]


def leaked(text):
    low = text.lower()
    return [name for name in ("BROKER_API_KEY", "BROKER_API_SECRET", "WEBULL_ACCOUNT_ID")
            if TRADING[name].lower() in low or TRADING[name][:12].lower() in low] + \
        [g for g in four_words() for g in (g, g.replace(" ", "_"), g.replace(" ", "-")) if g in low]


def plant_everywhere(w):
    """Every value of the trading file in a source the watch reads: an order tag, a journal reason, a job label, a log line."""
    w.unknown_order(tag=f"paper-{TRADING['BROKER_API_KEY']}")
    w.write_step("stopped", "send", f"the venue said {TRADING['BROKER_API_SECRET']} for account {TRADING['WEBULL_ACCOUNT_ID']}; "
                                    f"the words were {PHRASE}")
    w.jobs = w.jobs + [("-", "9", f"com.valedia.{TRADING['BROKER_API_KEY']}")]
    w.write_picks_log(end_ago=600, extra=[f"match failed for {TRADING['BROKER_API_SECRET']} {PHRASE}"])


def test_a_real_watch_never_writes_or_prints_a_value_of_the_trading_file(world, trading_file, monkeypatch, capsys):
    spy = Spy()
    monkeypatch.setattr(al.subprocess, "run", spy)
    plant_everywhere(world)
    before = dict(os.environ)
    assert al.main(["watch", "--notify"], env=world.env()) == 1
    assert al.main(["list"], env=world.env()) == 1
    seen = capsys.readouterr()
    assert os.environ == before                                                      # nothing was exported
    assert len(world.lines()) >= 4 and len(spy.calls) >= 4
    texts = [seen.out, seen.err, world.alerts_file.read_text(), " ".join(" ".join(c) for c in spy.calls),
             (world.alerts_file.parent / "notifications.jsonl").read_text()]
    for text in texts:
        assert leaked(text) == [], leaked(text)
    assert "<secret>" in world.alerts_file.read_text()


def test_the_registration_exports_nothing_and_a_child_never_gets_the_values(world, trading_file, monkeypatch):
    before = dict(os.environ)
    assert al.main(["list"], env=world.env()) == 0
    assert os.environ == before and not set(TRADING) & set(os.environ)
    assert not set(TRADING) & set(s.child_env())


def test_a_trading_file_that_cannot_be_read_is_exit_two_and_nothing_is_written(world, trading_file, capsys):
    for mode in (0o644, 0o000):
        os.chmod(trading_file, mode)
        for argv in (["watch"], ["watch", "--notify"], ["list"]):
            assert al.main(argv, env=world.env()) == 2, (mode, argv)
            err = capsys.readouterr()
            assert "trading file" in err.err and "nothing was done" in err.err and leaked(err.out + err.err) == []
            assert not world.alerts_file.exists() and not (world.alerts_file.parent / "notifications.jsonl").exists()
    os.chmod(trading_file, 0o600)


def test_a_missing_trading_file_is_no_reason_to_stop(world, tmp_path, monkeypatch):
    monkeypatch.setenv("VALEDIA_ENV_FILE", str(tmp_path / "there-is-no-trading-file.env"))
    assert al.main(["watch"], env=world.env()) == 0


def test_the_trading_file_of_the_environment_can_be_named_by_the_env(world, tmp_path):
    f = tmp_path / "other.env"
    f.write_text(f"BROKER_API_KEY={TRADING['BROKER_API_KEY']}\n")
    os.chmod(f, 0o600)
    env = world.env()
    env.trading_file = f
    assert al.register_trading_file(env) == {"BROKER_API_KEY": TRADING["BROKER_API_KEY"]}
    assert al.clean(f"a {TRADING['BROKER_API_KEY']} b") == "a <secret> b"


# ----------------------------------------------------------------------------------------------- order ids: last 4 only

TAG = "os-20261113-143501-a1b2c3-XLE"


def test_a_ledger_tag_is_shown_as_the_last_four_of_its_random_part_and_its_fund():
    assert al.clean(f"order {TAG} is unknown") == "order …b2c3-XLE is unknown"
    assert al.clean("os-20261113-143501-a1b2c3-BRK.B filled.") == "…b2c3-BRK.B filled."
    assert al.clean("os-20261113-143501-a1b2c3-BTC-USD, then") == "…b2c3-BTC-USD, then"
    assert al.clean(al.clean(f"order {TAG}")) == al.clean(f"order {TAG}")                  # shown once, stable when cleaned again


def test_other_order_ids_are_shown_as_their_last_four_characters():
    cases = {"order V-1234567890 is open": "order …7890 is open", "Order ID: 7f3a9c2e51b84d60 failed": "Order ID: …4d60 failed",
             "venue_order_id=ABCD1234efgh5678": "venue_order_id=…5678", "client order id paper-XLE-batch-0007": "client order id …0007",
             "tag abcdefghijklmnopqrs7 was sent": "tag …qrs7 was sent", "clientOrderId: Z9y8X7w6V5u4": "clientOrderId: …V5u4"}
    for text, want in cases.items():
        assert al.clean(text) == want, text


def test_ordinary_words_and_names_are_not_taken_for_ids():
    for text in ("order cancelled by the venue", "the order ledger orders-webull-sandbox is blocked", "orders:orders-webull-sandbox",
                 "id 20261113 is a date", "the client authentication failed", "orders sent 2, refused 1"):
        assert al.clean(text) == text, text


def test_generated_texts_with_tags_and_ids_never_keep_one_whole():
    rng = random.Random(2026)
    alnum = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    lead = ["order", "Order", "order id", "Order ID:", "id=", "client order id", "client_order_id=", "venue_order_id:", "orderId", "tag",
            "clientOrderId:", "order number"]
    tail = ["is unknown", "was refused by the venue", "", "filled 5 at 60.0", "(XLE +5)"]
    for i in range(2000):
        if i % 2:
            rand, sym = "".join(rng.choice("0123456789abcdef") for _ in range(6)), rng.choice(["XLE", "SPY", "BRK.B", "BTC-USD", "QQQ"])
            tag = f"os-2026{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}-{rng.randint(0, 23):02d}{rng.randint(0, 59):02d}{rng.randint(0, 59):02d}-{rand}-{sym}"
            text = f"{rng.choice(['order', 'the ledger refused to send', 'the outcome of order', ''])} {tag} {rng.choice(tail)}".strip()
            out = al.clean(text)
            assert tag not in out and f"…{rand[-4:]}-{sym}" in out, (text, out)
        else:
            ident = "".join(rng.choice(alnum + "-_") for _ in range(rng.randint(12, 36))).strip("-_") + str(rng.randint(0, 9))
            text = f"{rng.choice(lead)} {ident} {rng.choice(tail)}".replace("= ", "=").replace(": ", ": ").strip()
            out = al.clean(text)
            assert ident not in out and f"…{ident[-4:]}" in out, (text, out)


def test_a_whole_order_tag_reaches_no_output_in_any_source(world, capsys, monkeypatch):
    spy = Spy()
    monkeypatch.setattr(al.subprocess, "run", spy)
    world.unknown_order(tag=TAG)                                                                    # the ledger's own blocked() names it
    world.write_step("stopped", "send", f"order {TAG} could not be recorded or sent; the venue id was V-1234567890")
    world.jobs = world.jobs + [("-", "9", "com.valedia.order-1234567890abcdef")]
    world.write_picks_log(end_ago=600, extra=[f"match failed for order {TAG} id 1a2b3c4d5e6f7a8b"])
    assert al.main(["watch", "--notify"], env=world.env()) == 1
    al.main(["list"], env=world.env())
    out = capsys.readouterr()
    for text in (out.out, out.err, world.alerts_file.read_text(), " ".join(" ".join(c) for c in spy.calls)):
        assert TAG not in text and "a1b2c3" not in text and "1234567890" not in text and "1a2b3c4d5e6f7a8b" not in text
    assert any("…b2c3-XLE" in x["detail"] for x in world.lines())


# ----------------------------------------------------------------------------------------------- the read-only opening

def test_a_store_is_opened_read_only_and_cannot_be_made_writable(tmp_path):
    db = tmp_path / "x.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE t (a)")
    c.commit()
    c.close()
    before = (db.read_bytes(), db.stat().st_mtime_ns)
    conn = al.open_readonly(db)
    conn.execute("PRAGMA query_only = OFF")                       # even a connection told it may write cannot: the file is mode=ro
    for sql in ("INSERT INTO t VALUES (1)", "CREATE TABLE z (a)", "DROP TABLE t"):
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(sql)
    conn.close()
    assert (db.read_bytes(), db.stat().st_mtime_ns) == before and sorted(p.name for p in tmp_path.iterdir()) == ["x.sqlite"]


def test_a_busy_store_is_waited_for_only_as_long_as_the_budget_and_then_said_busy(tmp_path):
    db = tmp_path / "x.sqlite"
    c = sqlite3.connect(db, isolation_level=None)
    c.execute("CREATE TABLE t (a)")
    c.execute("BEGIN EXCLUSIVE")
    t0 = time.monotonic()
    conn = al.open_readonly(db, timeout=0.4)
    with pytest.raises(sqlite3.OperationalError) as e:
        conn.execute("SELECT COUNT(*) FROM t")
        conn.execute("SELECT COUNT(*) FROM t")
    took = time.monotonic() - t0
    assert 0.3 <= took < 2.0 and "busy" in al.describe(e.value)
    t1 = time.monotonic()
    with pytest.raises(sqlite3.OperationalError):                 # the budget is for the store in all, not for each statement
        conn.execute("SELECT 1 FROM t")
    assert time.monotonic() - t1 < 0.2
    c.execute("ROLLBACK")
    assert al.open_readonly(db).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0


def make_hot_journal(db):
    """A store a writer was killed in the middle of: a journal of the pages it was changing is left beside the file."""
    code = (f"import sqlite3, os\nc = sqlite3.connect({str(db)!r}, isolation_level=None)\nc.execute('PRAGMA cache_size = 10')\n"
            "c.execute('BEGIN')\nc.execute('CREATE TABLE IF NOT EXISTS junk (a)')\n"
            "c.executemany('INSERT INTO junk VALUES (?)', [('x' * 200,)] * 3000)\nos._exit(0)\n")
    p = subprocess.Popen([sys.executable, "-I", "-c", code])
    assert p.wait() == 0
    assert (db.parent / (db.name + "-journal")).stat().st_size > 0


def test_a_store_that_needs_recovery_is_said_so_and_left_exactly_as_it_was(tmp_path):
    db = tmp_path / "x.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE t (a)")
    c.executemany("INSERT INTO t VALUES (?)", [("y" * 200,)] * 500)
    c.commit()
    c.close()
    make_hot_journal(db)

    def state():
        return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(tmp_path.iterdir())}
    before = state()
    with pytest.raises(sqlite3.OperationalError) as e:
        al.open_readonly(db).execute("SELECT COUNT(*) FROM t")
    assert "recover" in al.describe(e.value)
    assert state() == before


# ----------------------------------------------------------------------------------------------- shown only once acknowledged

class Flaky:
    """A sender that fails the first `fail` times and then answers."""
    name = "phone"

    def __init__(self, fail=0):
        self.fail, self.calls = fail, []

    def send(self, alert):
        self.calls.append(alert.key)
        if len(self.calls) <= self.fail:
            raise OSError("no signal")


def test_an_alert_whose_notification_failed_is_tried_again_on_each_later_watch_until_it_is_shown(world):
    halt_on(world)
    phone = Flaky(fail=2)
    r1 = watch(world, senders=[phone])
    assert keys(r1) == ["switch:halt"] and r1.shown == [] and r1.failures
    r2 = watch(world, senders=[phone])                                    # still open and not shown: tried again
    assert r2.raised == [] and r2.shown == [] and r2.failures and len(phone.calls) == 2
    r3 = watch(world, senders=[phone])
    assert [a.key for a in r3.shown] == ["switch:halt"] and r3.failures == [] and len(phone.calls) == 3
    r4 = watch(world, senders=[phone])                                    # shown: never again
    assert r4.shown == [] and len(phone.calls) == 3 and len(world.lines()) == 1


def test_a_failing_sender_is_tried_five_times_in_all_and_then_the_alert_is_left_to_list_and_the_page(world, capsys):
    halt_on(world)
    phone = Flaky(fail=99)
    for n in range(1, 8):
        r = watch(world, senders=[phone])
        assert len(phone.calls) == min(n, 5)
        assert bool(r.failures) == (n <= 5)                               # after the fifth, nothing more is tried and nothing fails
    assert al.main(["list"], env=world.env()) == 1
    out = capsys.readouterr().out
    assert "switch:halt" in out and "not shown" in out and "5 times" in out
    states = al.read_notifications(al.notifications_file(world.root))
    assert "5 times" in al.notification_note(states, world.lines()[0])


def test_a_later_watch_with_notify_shows_an_alert_raised_by_a_watch_without_it(world, monkeypatch):
    spy = Spy()
    monkeypatch.setattr(al.subprocess, "run", spy)
    halt_on(world)
    assert al.main(["watch"], env=world.env()) == 1 and spy.calls == []        # recorded, never shown (also what a kill leaves)
    assert al.main(["watch", "--notify"], env=world.env()) == 1 and len(spy.calls) == 1
    assert al.main(["watch", "--notify"], env=world.env()) == 1 and len(spy.calls) == 1


def test_an_osascript_that_fails_is_retried_and_then_shown_once(world, monkeypatch):
    halt_on(world)
    bad = Spy(returncode=1)
    monkeypatch.setattr(al.subprocess, "run", bad)
    assert al.main(["watch", "--notify"], env=world.env()) == 2 and len(bad.calls) == 1
    good = Spy()
    monkeypatch.setattr(al.subprocess, "run", good)
    assert al.main(["watch", "--notify"], env=world.env()) == 1 and len(good.calls) == 1
    assert al.main(["watch", "--notify"], env=world.env()) == 1 and len(good.calls) == 1


def test_an_alert_raised_again_after_it_cleared_is_a_new_alert_for_the_notifier(world):
    phone = Flaky()
    halt_on(world)
    watch(world, senders=[phone])
    halt_off(world)
    watch(world, senders=[phone])
    halt_on(world)
    time_later = world.env()
    time_later.now = NOW + 60
    al.watch(time_later, senders=[phone])
    assert phone.calls == ["switch:halt", "switch:halt"]


# ----------------------------------------------------------------------------------------------- small ones

HIDDEN = "\u202e\u200b\u2066\u0007\u001b\u0085\ufeff"


def test_format_and_control_characters_never_reach_the_file_the_page_or_a_notification(world, monkeypatch, capsys):
    spy = Spy()
    monkeypatch.setattr(al.subprocess, "run", spy)
    world.write_step("stopped", "send", f"the {HIDDEN}venue{HIDDEN} said{HIDDEN} no \x1b[31mred\x1b[0m")
    world.jobs = world.jobs + [("-", "9", f"com.valedia.job{HIDDEN}x")]
    assert al.main(["watch", "--notify"], env=world.env()) == 1
    al.main(["list"], env=world.env())
    out = capsys.readouterr().out
    strings = [out, " ".join(" ".join(c) for c in spy.calls)]
    for line in world.alerts_file.read_text().splitlines():
        strings += [str(v) for v in json.loads(line).values()]
    for text in strings:
        assert not [c for c in text if unicodedata.category(c) in ("Cf", "Cc") and c != "\n"], repr(text)
    assert any("the venue said no" in x for x in strings)


def test_a_secret_split_by_invisible_characters_is_still_caught(trading_file, world):
    al.register_trading_file(world.env())
    key = TRADING["BROKER_API_KEY"]
    assert key[:10] not in al.clean(key[:10] + "\u200b" + key[10:])


def test_list_on_a_folder_or_an_unreadable_alert_file_is_exit_two(world, capsys):
    world.alerts_file.parent.mkdir(parents=True, exist_ok=True)
    world.alerts_file.mkdir()
    assert al.main(["list"], env=world.env()) == 2
    assert capsys.readouterr().err
    world.alerts_file.rmdir()
    world.alerts_file.write_text("{}\n")
    os.chmod(world.alerts_file, 0o000)
    try:
        assert al.main(["list"], env=world.env()) == 2
    finally:
        os.chmod(world.alerts_file, 0o600)


def test_the_watch_on_a_folder_as_the_alert_file_is_exit_two(world):
    world.alerts_file.parent.mkdir(parents=True, exist_ok=True)
    world.alerts_file.mkdir()
    assert al.main(["watch"], env=world.env()) == 2


# ----------------------------------------------------------------------------------------------- the two new conditions

def test_the_order_step_condition_only_applies_to_a_loaded_job_on_a_trading_day_after_ten_thirty_new_york(world):
    orderstep_on(world)
    after = datetime(2026, 11, 13, 15, 30, 0, tzinfo=timezone.utc).timestamp()           # 10:30:00 New York
    for now, expect in ((after, False), (after + 1, True), (after - 3600, False)):
        env = world.env()
        env.now = now
        assert ("orderstep:no-run" in [a.key for a in al.run_checks(env)[0]]) is expect, now
    saturday = world.env()
    saturday.now = datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc).timestamp()
    assert "orderstep:no-run" not in [a.key for a in al.run_checks(saturday)[0]]
    orderstep_off(world)                                                                  # not loaded: nothing to say
    assert "orderstep:no-run" not in [a.key for a in al.run_checks(world.env())[0]]


def test_a_journaled_order_step_run_of_today_clears_it_and_one_of_another_day_does_not(world):
    orderstep_on(world)
    world.write_step("ok", day="2026-11-12")
    assert "orderstep:no-run" in keys(watch(world))
    world.write_step("stopped", "session", "outside the regular session window 09:35 to 15:45 New York time (it is 06:10)",
                     day="2026-11-13")                                                     # any run counts as a run
    assert [c.key for c in watch(world).cleared] == ["orderstep:no-run"]


def test_the_weekly_condition_boundaries_and_a_loop_that_never_ran(world):
    for ago, expect in ((8 * 86400 - 5, False), (8 * 86400, False), (8 * 86400 + 1, True)):
        world.write_weekly_log(ran_ago=ago)
        assert ("weekly:no-run" in [a.key for a in al.run_checks(world.env())[0]]) is expect, ago
    log = world.logs / "valedia-research-loop.log"
    log.unlink()
    plist = world.agents / f"{LABELS[3]}.plist"
    for age_days, expect in ((2, False), (9, True)):
        os.utime(plist, (NOW - age_days * 86400, NOW - age_days * 86400))
        assert ("weekly:no-run" in [a.key for a in al.run_checks(world.env())[0]]) is expect, age_days
    plist.unlink()
    assert "weekly:no-run" in [a.key for a in al.run_checks(world.env())[0]]
    world.jobs = [j for j in world.jobs if j[2] != LABELS[3]]                              # not loaded: the job alert says it
    assert "weekly:no-run" not in [a.key for a in al.run_checks(world.env())[0]]


def test_a_long_weekly_log_is_searched_for_its_last_run_line(world):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW - 3600))
    (world.logs / "valedia-research-loop.log").write_text(f"=== {stamp} weekly research loop\n" + "agent output line\n" * 300000)
    assert "weekly:no-run" not in [a.key for a in al.run_checks(world.env())[0]]


# ----------------------------------------------------------------------------------------------- the mutants the mutation run asked for

def test_the_damage_window_is_thirty_days_to_the_day(world):
    inside, outside = world.root / "journal" / "2026-10-14.jsonl", world.root / "journal" / "2026-10-13.jsonl"   # 30 and 31 days before
    outside.write_text('{"half a line\n')
    assert "journal:damaged" not in keys(watch(world))
    inside.write_text('{"half a line\n')
    (a,) = [x for x in watch(world).raised if x.key == "journal:damaged"]
    assert "2026-10-14.jsonl line 1" in a.detail and "2026-10-13" not in a.detail


def test_the_watch_waits_for_the_lock_on_the_alert_file(world):
    path = world.alerts_file
    path.parent.mkdir(parents=True, exist_ok=True)
    held = path.open("a+b")
    fcntl.flock(held, fcntl.LOCK_EX)
    done, out = threading.Event(), []
    halt_on(world)

    def run():
        out.append(watch(world))
        done.set()

    th = threading.Thread(target=run)
    th.start()
    try:
        assert not done.wait(1.0), "the watch wrote while another process held the alert file"
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        held.close()
    assert done.wait(15)
    th.join()
    assert keys(out[0]) == ["switch:halt"] and len(world.lines()) == 1


def test_two_watches_started_together_raise_an_alert_once(world):
    halt_on(world)
    results, gate = [], threading.Barrier(2)

    def run():
        gate.wait()
        results.append(watch(world))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(30)
    assert sorted(len(r.raised) for r in results) == [0, 1] and len(world.lines()) == 1


def test_the_watch_function_itself_registers_the_trading_file_when_it_is_not_started_by_main(world, trading_file):
    plant_everywhere(world)
    watch(world)
    assert leaked(world.alerts_file.read_text()) == []


class Killed(BaseException):
    """What a kill looks like to a sender: nothing comes back."""


def test_a_run_killed_before_the_sender_answered_leaves_the_alert_not_shown_and_it_is_tried_again(world):
    halt_on(world)

    class Dies:
        name = "phone"

        def send(self, alert):
            raise Killed()

    with pytest.raises(Killed):
        watch(world, senders=[Dies()])
    assert len(world.lines()) == 1                                         # the alert was written first
    states = al.read_notifications(al.notifications_file(world.root))
    assert not any(st["shown"] for st in states.values())
    phone = Flaky()
    assert [a.key for a in watch(world, senders=[phone]).shown] == ["switch:halt"]


# =====================================================================================================================
# rev 3: what is read back from the tool's own files is cleaned too
# =====================================================================================================================

def raw_pair(value):
    return {"v": value}


def plant_old_files(w):
    """An alerts.jsonl and a notifications.jsonl that already hold values of the trading file in every field (as an earlier
    version, a hand or another program could have left them). The values in a key are all in the characters a key may hold."""
    key, sec, acct = TRADING["BROKER_API_KEY"], TRADING["BROKER_API_SECRET"], TRADING["WEBULL_ACCOUNT_ID"]
    phrase_key = "_".join(PHRASE.split()[:6])
    rows = [
        {"time": "2026-11-13T19:00:00Z", "level": "warning", "key": f"job:com.valedia.{key}", "title": f"Job {sec} failed", "detail": f"words {PHRASE}"},
        {"time": "2026-11-13T19:01:00Z", "level": "critical", "key": f"job:com.valedia.{phrase_key}", "title": "t", "detail": f"account {acct}"},
        {"time": f"2026-11-13T{sec}", "level": "warning", "key": f"picks:{sec}", "title": "title", "detail": "d"},
        {"time": "2026-11-13T19:03:00Z", "level": f"warning{key}", "key": f"job:com.valedia.{acct}", "title": "t", "detail": "d"},
        {"time": "2026-11-13T19:04:00Z", "level": "warning", "key": f"orders:{key}", "title": f"order {sec}", "detail": "d"},
        {"time": "2026-11-13T19:05:00Z", "level": "warning", "key": "job:com.valedia.55512345678901", "title": "an account-like key", "detail": "d"},
    ]
    folder = w.alerts_file.parent
    folder.mkdir(parents=True, exist_ok=True)
    w.alerts_file.write_text("".join(json.dumps(r) + "\n" for r in rows))
    notes = [{"time": "2026-11-13T19:05:00Z", "sender": f"phone-{key}", "key": f"job:com.valedia.{key}", "since": "2026-11-13T19:00:00Z",
              "result": "failed"},
             {"time": "2026-11-13T19:05:00Z", "sender": "mac", "key": f"picks:{sec}", "since": f"2026-11-13T{sec}", "result": "failed"},
             {"time": "2026-11-13T19:05:00Z", "sender": "mac", "key": f"orders:{key}", "since": "2026-11-13T19:04:00Z", "result": "shown"}]
    (folder / "notifications.jsonl").write_text("".join(json.dumps(r) + "\n" for r in notes))


def test_values_already_in_the_alert_and_notification_files_never_get_out_through_the_real_commands(world, trading_file, monkeypatch, capsys):
    spy = Spy()
    monkeypatch.setattr(al.subprocess, "run", spy)
    plant_old_files(world)
    halt_on(world)                                                # one real alert beside the old ones
    folder = world.alerts_file.parent
    old = (world.alerts_file.read_text(), (folder / "notifications.jsonl").read_text())     # what was planted: it is there to stay
    outputs = []
    for argv in (["list"], ["watch", "--notify"], ["list"], ["watch", "--notify"], ["list"]):
        al.main(argv, env=world.env())
        seen = capsys.readouterr()
        outputs += [seen.out, seen.err]
    new_alerts, new_notes = world.alerts_file.read_text(), (folder / "notifications.jsonl").read_text()
    assert new_alerts.startswith(old[0]) and new_notes.startswith(old[1])                    # append-only: the old lines are untouched
    texts = outputs + [new_alerts[len(old[0]):], new_notes[len(old[1]):], " ".join(" ".join(c) for c in spy.calls)]
    for i, text in enumerate(texts):
        assert leaked(text) == [], (i, text[:600])
    lines = world.lines()
    assert [x["key"] for x in lines[6:] if x["level"] == "cleared"] == ["job:com.valedia._number_"]    # cleared under its cleaned key
    assert spy.calls                                                    # and the open ones were sent, cleaned


def test_the_commands_register_the_trading_file_before_they_bind_the_stream_they_print_to(world, trading_file, monkeypatch, capsys):
    def raw_print(rows, out, states=None):
        print("raw " + TRADING["BROKER_API_KEY"], file=out)         # a line that nothing cleaned: only the stream guard can stop it
    monkeypatch.setattr(al, "_show", raw_print)
    al.main(["list"], env=world.env())
    assert TRADING["BROKER_API_KEY"] not in capsys.readouterr().out


def test_a_stored_line_is_read_back_as_its_cleaned_form_everywhere(world, trading_file):
    plant_old_files(world)
    al.register_trading_file(world.env())
    rows = al.open_alerts(world.alerts_file)
    assert len(rows) >= 1                                              # keys that clean to the same form are one key
    for r in rows:
        assert leaked(json.dumps(r)) == [] and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-" for c in r["key"])
    states = al.read_notifications(al.notifications_file(world.root))
    assert leaked(json.dumps([list(k) for k in states])) == []


# ----------------------------------------------------------------------------------------------- account-style digit groups

def test_three_or_more_groups_of_three_or_more_digits_are_masked_and_dates_stay_readable():
    for text, want in (("card 1234 5678 9012 3456 ok", "card <number> ok"), ("1234-5678-9012", "<number>"),
                       ("acct 1234 5678-9012 3456 7890", "acct <number>"), ("x 1234 5678 y", "x 1234 5678 y"),
                       ("987-654-321-098", "<number>"), ("987 654 321 098", "<number>"), ("acct 987 654 321 098.", "acct <number>."),
                       ("123 456 789", "<number>"), ("x 123 456 y", "x 123 456 y"),
                       ("1234  5678  9012", "<number>"),                         # spaces are collapsed first, then the groups are seen
                       ("1234 5678 90123", "<number>"), ("12 3456 7890 1234", "12 <number>"),
                       ("2024 2025 2026", "<number>")):                          # a list of years in fours is masked too: a known cost
        assert al.clean(text) == want, text
    for ok in ("2026-11-13", "2026-11-13 17:30:00", "20261113", "2026-11-13T17:30:00Z", "on 2026-11-13 at 2026-11-14 09:30", "1 2 4 8 16",
               "2026-11-13 17:30", "17:30:00 17:31:00 17:32:00"):
        assert al.clean(ok) == ok, ok


def test_an_account_style_number_in_a_source_does_not_reach_the_alert_file(world):
    world.write_step("stopped", "send", "the venue said account 4111 1111 1111 1111 and 5500-0000-0000-0004 on 2026-11-13 17:30:00")
    (a,) = [x for x in watch(world).raised if x.key.startswith("step-stopped")]
    assert "4111" not in a.detail and "5500" not in a.detail and "2026-11-13 17:30:00" in a.detail


# ----------------------------------------------------------------------------------------------- tries are counted before the send

def test_a_try_is_written_before_the_send_and_counts_toward_the_five(world):
    halt_on(world)
    seen = []

    class Watches:
        name = "phone"

        def send(self, alert):
            record = (world.alerts_file.parent / "notifications.jsonl").read_text()
            seen.append([json.loads(x)["result"] for x in record.splitlines()])

    watch(world, senders=[Watches()])
    assert seen == [["trying"]]                                          # on disk before the send began
    assert [json.loads(x)["result"] for x in (world.alerts_file.parent / "notifications.jsonl").read_text().splitlines()] == ["trying", "shown"]
    states = al.read_notifications(al.notifications_file(world.root))
    assert list(states.values()) == [{"shown": True, "tries": 1}]


CHILD = """
import sys, time, json
from pathlib import Path
import pandas as pd
from valedia_trader.core import alerts as al
a = json.loads(sys.argv[1])
env = al.Env(root=Path(a["root"]), now=a["now"], halt_file=Path(a["halt"]), logs_dir=Path(a["logs"]), launch_agents_dir=Path(a["agents"]),
             launchctl=lambda: a["launchctl"], free_bytes=lambda p: 50 * 10 ** 9,
             sessions=lambda: pd.bdate_range("2026-01-01", "2027-12-31"), environ={})
class Hang:
    name = "phone"
    def send(self, alert):
        print("sending", flush=True)
        time.sleep(300)
al.watch(env, senders=[Hang()])
"""


def kill_during_send(world, tmp_path):
    root = Path(al.__file__).resolve().parents[2]
    arg = json.dumps({"root": str(world.root), "now": NOW, "halt": str(world.halt), "logs": str(world.logs), "agents": str(world.agents),
                      "launchctl": launchctl_text(world.jobs)})
    child_env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PYTHONPATH": str(root), "VALEDIA_ENV_FILE": str(tmp_path / "none.env"),
                 "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.Popen([sys.executable, "-c", CHILD, arg], env=child_env, stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "sending"
    finally:
        p.kill()                                                         # our own child, by its handle
        p.wait()


def test_a_run_killed_during_the_send_can_never_show_one_alert_more_than_five_times_in_all(world, tmp_path):
    halt_on(world)
    for _ in range(5):
        kill_during_send(world, tmp_path)
    states = al.read_notifications(al.notifications_file(world.root))
    assert [st["tries"] for st in states.values()] == [5] and not any(st["shown"] for st in states.values())
    phone = Flaky()
    r = watch(world, senders=[phone])
    assert phone.calls == [] and r.shown == []                           # five tries were used up by the killed ones


def test_two_killed_sends_then_a_good_one_shows_it_once(world, tmp_path):
    halt_on(world)
    kill_during_send(world, tmp_path)
    kill_during_send(world, tmp_path)
    phone = Flaky()
    assert [a.key for a in watch(world, senders=[phone]).shown] == ["switch:halt"]
    assert watch(world, senders=[phone]).shown == [] and len(phone.calls) == 1


# ----------------------------------------------------------------------------------------------- a file that is not a trading file

def test_a_trading_file_too_large_or_with_too_many_settings_is_refused_quickly_with_nothing_written(world, trading_file, capsys):
    cases = {"larger than 64 KB": "# " + "x" * 70000 + "\n",
             "a few MB": "".join(f"SETTING_{i}=v\n" for i in range(400000)),
             "more than 200 settings": "".join(f"SETTING_{i}=value{i}\n" for i in range(201))}
    for name, body in cases.items():
        trading_file.write_text(body)
        os.chmod(trading_file, 0o600)
        for argv in (["watch"], ["list"]):
            t0 = time.monotonic()
            code = al.main(argv, env=world.env())
            took = time.monotonic() - t0
            err = capsys.readouterr().err
            assert code == 2 and took < 1.0, (name, took)
            assert "trading file" in err and "nothing was done" in err and ("64 KB" in err or "200" in err)
            assert not world.alerts_file.exists()
    trading_file.write_text("".join(f"SETTING_{i}=value{i}\n" for i in range(200)))
    os.chmod(trading_file, 0o600)
    assert al.main(["list"], env=world.env()) == 0                       # 200 settings is still a trading file


def test_a_trading_file_that_is_not_utf8_says_so_and_not_chmod(world, trading_file, capsys):
    trading_file.write_bytes(b"BROKER_KEY=\xff\xfe\xfa not text\n")
    os.chmod(trading_file, 0o600)
    assert al.main(["watch"], env=world.env()) == 2
    err = capsys.readouterr().err
    assert "UTF-8" in err and "chmod" not in err and "nothing was done" in err
    os.chmod(trading_file, 0o644)
    assert al.main(["watch"], env=world.env()) == 2
    assert "chmod 600" in capsys.readouterr().err                          # a file others can read still says why


# ----------------------------------------------------------------------------------------------- order ids, both ways

def test_our_own_tags_and_batch_ids_are_shortened_wherever_they_stand():
    batch, tag = "os-20261113-143501-a1b2c3", "os-20261113-143501-a1b2c3-XLE"
    for text, want in ((tag, "…b2c3-XLE"), (batch, "…b2c3"), (f"the {batch} run", "the …b2c3 run"),
                       (f"sent: {tag}, {tag.replace('XLE', 'SPY')}", "sent: …b2c3-XLE, …b2c3-SPY"),
                       (f"refused {tag}!", "refused …b2c3-XLE!"), (f"batch={batch}.", "batch=…b2c3."),
                       (f"{batch}-BRK.B and {batch}-BTC-USD", "…b2c3-BRK.B and …b2c3-BTC-USD"), (f"paper-{tag}", "paper-…b2c3-XLE")):
        assert al.clean(text) == want, text


def test_other_ids_after_the_words_in_lists_and_after_is_and_ref():
    cases = {"orders A1B2C3D4E5F6 and G7H8I9J0K1L2 were refused": "orders …E5F6 and …K1L2 were refused",
             "orders X9Y8Z7W6V5U4T3, Q1W2E3R4T5Y6, M0N9B8V7C6X5": "orders …U4T3, …T5Y6, …C6X5",
             "the id is 4f9a7c1e0b2d3a5f6e": "the id is …5f6e",
             "ref 20261113ABCDEF and tag 9z8y7x6w5v4u3t": "ref …CDEF and tag …4u3t",
             "the client sent a request for order number q1w2e3r4t5y6u7": "the client sent a request for order number …y6u7",
             "tag: AAAABBBBCCCC1234": "tag: …1234", "IDs 5f3c8a1e9b7d2046, 0b9e8d7c6a5f4e31": "IDs …2046, …4e31"}
    for text, want in cases.items():
        assert al.clean(text) == want, (text, al.clean(text))


def test_dates_decimals_file_names_versions_tickers_funds_prices_and_counts_are_never_shortened():
    for text in ("order placed 2026-11-13T17:30:00Z", "order filled at 2026-11-13T17:30:00+00:00 for 60.25", "order 20261113T173000Z done",
                 "order 20261113-173000 done", "order price 123456789.55 and 60.2575", "orders file orders-20261113-143501.json written",
                 "client version 1.2.3-rc1 and python3.12.11 build", "order KXNBAGAME-26NOV13LALBOS-LAL filled", "id KXHIGHNY-26NOV13-T60 open",
                 "order BTC-USD and BRK.B and XLE", "order 5 units at 450.00", "ref 2026-11-13 17:30:00", "tag 20261113", "orders sent 2, refused 1",
                 "the order ledger orders-webull-sandbox-events is blocked", "order sandbox.example.com:8443 down",
                 "client 127.0.0.1 id 192.168.100.200", "order 1.0.0-beta.2+build.5 shipped"):
        assert al.clean(text) == text, (text, al.clean(text))


def test_generated_ids_in_clauses_and_lists_never_survive_and_generated_non_ids_always_do():
    rng = random.Random(2027)
    alnum = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    words = ["order", "orders", "id", "ids", "ref", "tag", "client", "Order", "ID"]
    for i in range(2000):
        ids = []
        for _ in range(rng.randint(1, 3)):
            n = rng.randint(12, 30)
            core = "".join(rng.choice(alnum) for _ in range(n))
            ids.append(core[:n - 1] + str(rng.randint(0, 9)))
        word = rng.choice(words)
        joiner = rng.choice([", ", " and ", " ", " / "])
        glue = rng.choice(["", "is ", "= ", ": ", "number ", "sent "])
        text = f"The venue said {word} {glue}{joiner.join(ids)} {rng.choice(['was refused', 'is open', '(XLE +5)', ''])}".strip()
        out = al.clean(text)
        assert all(x not in out for x in ids), (text, out)
        assert all(f"…{x[-4:]}" in out for x in ids), (text, out)
    for i in range(2000):
        date = f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        pick = rng.choice([f"{date}T{rng.randint(0, 23):02d}:00:00Z", f"{date}", f"{rng.randint(1, 99999)}.{rng.randint(10, 99999)}",
                           f"orders-{date.replace('-', '')}-1435{rng.randint(10, 99)}.json", f"{rng.randint(1, 9)}.{rng.randint(0, 20)}.{rng.randint(0, 20)}-rc{rng.randint(1, 9)}",
                           f"KX{''.join(rng.choice('ABCDEFGHIJ') for _ in range(10))}-{date[2:4]}NOV13-T{rng.randint(10, 99)}"])
        text = f"{rng.choice(['order', 'id', 'ref', 'tag', 'client'])} {pick} ok"
        assert al.clean(text) == text, (text, al.clean(text))


# ----------------------------------------------------------------------------------------------- practice runs are not the unattended run

def write_practice(w, mode, status, stage=None, reason=None, day="2026-11-13", venue="webull-sandbox"):
    w.journal.write(day, {"type": "order_step", "venue": venue, "event": "summary", "mode": mode, "status": status, "stage": stage,
                          "reason": reason, "orders_sent": 0, "sent": [], "refused": [], "left_over": []})


def test_a_propose_that_ended_done_never_clears_a_stop_of_the_unattended_run(world):
    world.write_step("stopped", "send", "the venue refused", day="2026-11-13")
    assert keys(watch(world)) == ["step-stopped:webull-sandbox"]
    write_practice(world, "propose", "done", day="2026-11-13")
    write_practice(world, "approve", "done", day="2026-11-13")
    r = watch(world)
    assert r.cleared == [] and [a["key"] for a in r.open] == ["step-stopped:webull-sandbox"]
    world.write_step("done", day="2026-11-13")                         # only an unattended run can clear it
    assert [c.key for c in watch(world).cleared] == ["step-stopped:webull-sandbox"]


def test_a_stopped_propose_or_approve_is_not_a_stop_of_the_unattended_run(world):
    write_practice(world, "propose", "stopped", "send", "the venue refused")
    write_practice(world, "approve", "stopped", "ticket", "ticket 7 is not the latest")
    assert not [a for a in watch(world).raised if a.key.startswith("step-stopped")]


def test_the_order_step_not_run_today_looks_only_at_the_unattended_run(world):
    orderstep_on(world)
    write_practice(world, "propose", "done", day="2026-11-13")
    assert "orderstep:no-run" in keys(watch(world))                      # a propose is not the unattended run
    world.write_step("ok", day="2026-11-13")
    assert [c.key for c in watch(world).cleared] == ["orderstep:no-run"]


def test_a_refused_approval_is_its_own_warning_once_per_new_york_day(world):
    write_practice(world, "approve", "stopped", "ticket", "the ticket is not the latest one")
    r1 = watch(world)
    (a,) = [x for x in r1.raised if x.key.startswith("approval")]
    assert a.level == "warning" and "approval was refused" in a.title and "ticket" in a.detail
    assert not [x for x in r1.raised if x.key.startswith("step-stopped")]
    r2 = watch(world)
    assert not [x for x in r2.raised if x.key.startswith("approval")]
    write_practice(world, "approve", "stopped", "ticket", "again", day="2026-11-13")
    assert not [x for x in watch(world).raised if x.key.startswith("approval")]          # the same day: still the one alert
    tomorrow = world.env()
    tomorrow.now = NOW + 86400
    r3 = al.watch(tomorrow)
    assert [c.key for c in r3.cleared if c.key.startswith("approval")]                  # the day is over: cleared
    write_practice(world, "approve", "stopped", "ticket", "next day", day="2026-11-14")
    nextday = world.env()
    nextday.now = NOW + 86400 + 60
    assert [a.key for a in al.watch(nextday).raised if a.key.startswith("approval")]    # a new day, a new alert


def test_an_approve_stopped_at_another_stage_is_a_stop_of_a_practice_run_only(world):
    write_practice(world, "approve", "stopped", "send", "the venue refused")
    assert not [a for a in watch(world).raised if a.key.startswith(("approval", "step-stopped"))]


def test_an_alert_key_is_cleaned_when_it_is_made(world, trading_file):
    al.register_trading_file(world.env())
    a = al.Alert(f"job:com.valedia.{TRADING['BROKER_API_KEY']}", "warning", "t", "d")
    assert leaked(a.key) == [] and all(c.isalnum() or c in "_.:-" for c in a.key)
    assert al.Alert("job:com.valedia.55512345678901", "warning", "t", "d").key == "job:com.valedia._number_"
    assert al.Alert("orders:orders-webull-sandbox", "warning", "t", "d").key == "orders:orders-webull-sandbox"


# =====================================================================================================================
# rev 4
# =====================================================================================================================

def test_an_account_number_written_in_groups_of_three_never_reaches_the_alert_file_or_the_screen(world, capsys):
    world.write_step("stopped", "send", "the venue said account 987-654-321-098 and 987 654 321 098 and 123 456 789")
    assert al.main(["watch"], env=world.env()) == 1
    al.main(["list"], env=world.env())
    seen = capsys.readouterr()
    for text in (seen.out, seen.err, world.alerts_file.read_text()):
        assert "987" not in text and "321" not in text and "654" not in text and "098" not in text and "123 456" not in text


# ----------------------------------------------------------------------------------------------- the guard runs last; a fixed point

WHITESPACE = ["  ", "   ", "         ", "　", " ", "  ", "\n", "\n\n", "\t", " \n ", "\r\n", " ", "  ", " 　 "]


def spaced(rng, words):
    out = words[0]
    for w in words[1:]:
        out += rng.choice(WHITESPACE) + w
    return out


def test_phrase_words_apart_by_wide_or_many_spaces_or_lines_are_masked_and_cleaning_is_a_fixed_point(world, trading_file):
    al.register_trading_file(world.env())
    rng = random.Random(404)
    words = PHRASE.split()
    for i in range(2000):
        start = rng.randint(0, len(words) - 4)
        chunk = words[start:start + rng.randint(4, 8)]
        text = f"the venue said {spaced(rng, chunk)} for {rng.choice(['XLE', 'account 5', 'order'])}"
        out = al.clean(text)
        assert leaked(out) == [], (text, out)
        assert al.clean(out) == out, (text, out)
        key = al.Alert("job:" + spaced(rng, chunk), "warning", "t", "d").key
        assert leaked(key) == [] and al.Alert(key, "warning", "t", "d").key == key and al._key_part(key) == key, key
    for i in range(2000):                                                                   # fixed point on texts of every sort
        pieces = [rng.choice(["order", "id", "tag", "client", "1a2b3c4d5e6f7a8b", "os-20261113-143501-a1b2c3-XLE", "987 654 321 098",
                              "2026-11-13", "17:30:00", "x" * rng.randint(1, 120), "​", "\x07", "alpha", "bravo", "candle", "delta",
                              "<secret>", "…", ";", ".", "KXNBA-26NOV13-T60", "12345678901234"]) for _ in range(rng.randint(1, 14))]
        text = rng.choice([" ", "  ", "\n", ""]).join(pieces)
        for limit in (40, 120, 300):
            once = al.clean(text, limit)
            assert al.clean(once, limit) == once, (text, limit, once)


def test_spaced_out_phrase_words_in_every_source_do_not_reach_the_alert_file_the_screen_or_a_notification(world, trading_file, monkeypatch, capsys):
    spy = Spy()
    monkeypatch.setattr(al.subprocess, "run", spy)
    gap = "          \u3000    "                                   # 15 characters that are not letters: past what the guard bridges
    words = PHRASE.split()
    world.write_step("stopped", "send", f"the venue said {gap.join(words)} and {chr(10).join(words)}")
    world.jobs = world.jobs + [("-", "9", "com.valedia." + gap.join(words[:5]))]
    world.write_picks_log(end_ago=600, extra=["match failed " + "  ".join(words)])
    old = {"time": "2026-11-13T19:00:00Z", "level": "warning", "key": "job:" + gap.join(words[:5]), "title": gap.join(words), "detail": "\n".join(words)}
    world.alerts_file.parent.mkdir(parents=True, exist_ok=True)
    world.alerts_file.write_text(json.dumps(old) + "\n")
    outputs = []
    for argv in (["watch", "--notify"], ["list"], ["watch", "--notify"], ["list"]):
        al.main(argv, env=world.env())
        seen = capsys.readouterr()
        outputs += [seen.out, seen.err]
    new = world.alerts_file.read_text()[len(json.dumps(old)) + 1:]
    for text in outputs + [new, " ".join(" ".join(c) for c in spy.calls)]:
        assert leaked(text) == [], text[:300]


def test_a_key_that_cleans_twice_differently_does_not_write_a_cleared_line_on_every_watch(world, trading_file):
    gap = "         "
    old = {"time": "2026-11-13T19:00:00Z", "level": "warning", "key": "job:com.valedia." + gap.join(PHRASE.split()[:5]), "title": "t", "detail": "d"}
    world.alerts_file.parent.mkdir(parents=True, exist_ok=True)
    world.alerts_file.write_text(json.dumps(old) + "\n")
    sizes = []
    for _ in range(4):
        al.main(["watch"], env=world.env())
        sizes.append(world.alerts_file.stat().st_size)
    assert sizes[1] == sizes[2] == sizes[3]                                                   # settled after the first watch


# ----------------------------------------------------------------------------------------------- only propose and approve are practice runs

def write_mode(w, mode, status="done", stage=None, reason=None, day="2026-11-13", event="summary", venue="webull-sandbox"):
    e = {"type": "order_step", "venue": venue, "event": event, "status": status, "stage": stage, "reason": reason,
         "orders_sent": 0, "sent": [], "refused": [], "left_over": []}
    if mode is not NO_MODE:
        e["mode"] = mode
    w.journal.write(day, e)


NO_MODE = object()
ODD_MODES = ["", None, 1, 0, True, False, ["propose"], {"a": 1}, "Propose", "APPROVE", " propose", "approve ", "propose\n", "practice", "approvex"]


def test_only_the_two_exact_words_are_practice_runs_and_no_key_is_the_unattended_run():
    assert al.is_practice({"mode": "propose"}) and al.is_practice({"mode": "approve"})
    assert not al.is_practice({}) and not al.is_practice({"status": "ok"})
    for odd in ODD_MODES:
        assert not al.is_practice({"mode": odd}), odd
        assert al.has_unknown_mode({"mode": odd}), odd
    assert not al.has_unknown_mode({}) and not al.has_unknown_mode({"mode": "propose"}) and not al.has_unknown_mode({"mode": "approve"})


@pytest.mark.parametrize("odd", ODD_MODES, ids=repr)
def test_a_summary_with_an_unknown_mode_is_neither_never_clears_never_counts_and_raises_one_warning_a_day(world, odd):
    world.write_step("stopped", "send", "the venue refused", day="2026-11-13")
    assert keys(watch(world)) == ["step-stopped:webull-sandbox"]
    write_mode(world, odd)                                                                      # a summary that is neither
    r = watch(world)
    assert r.cleared == [] and ["step-stopped:webull-sandbox"] == [a["key"] for a in r.open if a["key"].startswith("step")]
    unknown = [a for a in r.raised if a.key.startswith("mode:")]
    assert len(unknown) == 1 and unknown[0].level == "warning" and "unknown mode" in unknown[0].title
    assert not [a for a in watch(world).raised if a.key.startswith("mode:")]                    # once
    write_mode(world, odd, day="2026-11-13")                                                    # the same day again: still the one alert
    assert not [a for a in watch(world).raised if a.key.startswith("mode:")]
    later = world.env()
    later.now = NOW + 86400
    assert [c.key for c in al.watch(later).cleared if c.key.startswith("mode:")]                # the day is over: cleared


@pytest.mark.parametrize("odd", ODD_MODES, ids=repr)
def test_a_summary_with_an_unknown_mode_does_not_count_as_ran_today(world, odd):
    orderstep_on(world)
    write_mode(world, odd)
    assert "orderstep:no-run" in keys(watch(world))
    write_mode(world, NO_MODE)                                                                  # no mode key at all: the unattended run
    assert [c.key for c in watch(world).cleared] == ["orderstep:no-run"]


def test_an_unattended_stop_is_a_stop_with_no_mode_key_and_nothing_else(world):
    write_mode(world, NO_MODE, "stopped", "send", "the venue refused")
    assert keys(watch(world)) == ["step-stopped:webull-sandbox"]


@pytest.mark.parametrize("mode", ["propose", "approve"])
@pytest.mark.parametrize("stage", ["send", "reconcile", "environment", "baseline", "after", "unexpected", "journal"])
def test_a_practice_run_stopped_at_any_other_stage_raises_its_own_warning_once_a_day(world, mode, stage):
    write_mode(world, mode, "stopped", stage, "the venue refused")
    r = watch(world)
    (a,) = [x for x in r.raised if x.key.startswith("practice-stopped")]
    assert a.level == "warning" and mode in a.title and f"stage {stage}" in a.title
    assert not [x for x in r.raised if x.key.startswith("step-stopped")]
    assert not [x for x in watch(world).raised if x.key.startswith("practice-stopped")]
    later = world.env()
    later.now = NOW + 86400
    assert [c.key for c in al.watch(later).cleared if c.key.startswith("practice-stopped")]


@pytest.mark.parametrize("mode, stage", [("approve", "ticket"), ("propose", "ticket"), ("approve", "session"), ("propose", "session")])
def test_a_practice_run_stopped_at_ticket_or_session_raises_no_stopped_warning(world, mode, stage):
    write_mode(world, mode, "stopped", stage, "outside the regular session window 09:35 to 15:45 New York time (it is 06:10)")
    assert not [x for x in watch(world).raised if x.key.startswith(("practice-stopped", "step-stopped"))]


# ----------------------------------------------------------------------------------------------- a withheld old alert is not open

def plant_withheld(w, n=2):
    key = TRADING["BROKER_API_KEY"]
    rows = [{"time": f"2026-11-13T19:0{i}:00Z", "level": "warning", "key": f"job:com.valedia.{key}{i}", "title": "t", "detail": "d"} for i in range(n)]
    rows.append({"time": "2026-11-13T19:09:00Z", "level": "cleared", "key": f"job:com.valedia.{key}9", "title": "t", "detail": "d"})
    w.alerts_file.parent.mkdir(parents=True, exist_ok=True)
    w.alerts_file.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return len(rows)


def test_old_lines_whose_key_holds_a_secret_are_withheld_not_open_and_never_cleared(world, trading_file, capsys):
    n = plant_withheld(world)
    size = world.alerts_file.stat().st_size
    assert al.main(["list"], env=world.env()) == 0                                               # nothing open: exit 0
    out = capsys.readouterr().out
    assert out.count("withheld") == 1 and f"{n} old lines" in out and "no open alerts" in out
    for _ in range(3):
        assert al.main(["watch"], env=world.env()) == 0
    assert world.alerts_file.stat().st_size == size                                              # no cleared line, ever
    seen = capsys.readouterr().out
    assert seen.count("withheld") == 3 and leaked(seen) == []                                     # once per command output


def test_withheld_lines_do_not_hide_a_real_open_alert_and_are_not_counted_with_it(world, trading_file, capsys):
    plant_withheld(world)
    halt_on(world)
    assert al.main(["watch"], env=world.env()) == 1
    out = capsys.readouterr().out
    assert "switch:halt" in out and out.count("withheld") == 1
    assert [a["key"] for a in al.open_alerts(world.alerts_file)] == ["switch:halt"]
    rows, withheld = al.read_alert_file(world.alerts_file)
    assert [r["key"] for r in rows] == ["switch:halt"] and withheld == 3


def test_the_page_counts_no_withheld_line_as_open_and_says_how_many_were_withheld_once(world, trading_file):
    import valedia_trader.research_loop.owner_page as op
    plant_withheld(world)
    out = world.root.parent / "page.html"
    env = world.env()
    env.environ = None
    op.build(env, out=out)
    html = out.read_text()
    assert html.count("withheld") == 1 and "3 old lines" in html and "No open alerts" in html and leaked(html) == []


def test_a_line_whose_key_merely_changes_under_cleaning_is_still_an_open_alert(world):
    row = {"time": "2026-11-13T19:00:00Z", "level": "warning", "key": "job:com.valedia.55512345678901", "title": "t", "detail": "d"}
    world.alerts_file.parent.mkdir(parents=True, exist_ok=True)
    world.alerts_file.write_text(json.dumps(row) + "\n")
    rows, withheld = al.read_alert_file(world.alerts_file)
    assert [r["key"] for r in rows] == ["job:com.valedia._number_"] and withheld == 0


# ----------------------------------------------------------------------------------------------- raised once; the clause end

def test_two_labels_that_clean_to_the_same_key_raise_one_alert_in_one_run(world):
    world.jobs = world.jobs + [("-", "9", f"com.valedia.5551234567{i:04d}") for i in range(5)]
    r = watch(world)
    mine = [a for a in r.raised if a.key == "job:com.valedia._number_"]
    assert len(mine) == 1
    assert sum(x["key"] == "job:com.valedia._number_" for x in world.lines()) == 1
    r2 = watch(world)
    assert r2.raised == [] and sum(x["key"] == "job:com.valedia._number_" for x in world.lines()) == 1


def test_an_id_after_the_end_of_a_clause_does_not_borrow_the_keyword_of_the_one_before():
    id_ = "1a2b3c4d5e6f7a8b"
    for text in (f"the order was refused; then {id_} came", f"the order was refused. Build {id_} came", f"tag ok; build {id_} done",
                 f"order refused. version {id_}"):
        assert al.clean(text) == text, text
    for text, want in ((f"the order was refused, then {id_} came", "the order was refused, then …7a8b came"),
                       (f"order x; order {id_}", "order x; order …7a8b"), (f"order. order {id_}", "order. order …7a8b")):
        assert al.clean(text) == want, text


# =====================================================================================================================
# rev 5: the cleaning, done once and in the right order; a stop of the unattended run is never hidden
# =====================================================================================================================

# The cleaning of revisions 2 (8d166e5) and 3 (6f02ec1), their own code as it was committed (`git show <rev>:valedia_trader/
# core/alerts.py`, the digit rules and the text section, copied unchanged), run over this process's guard: what they masked is
# what the candidate may never show.
REV2_RULES = r'''
_LONG_DIGITS = re.compile(r"(?<!\d)\d{9,}(?!\d)")
_GROUPED_DIGITS = re.compile(r"(?<!\d)\d{3,}(?:[ -]\d{3,}){2,}(?!\d)")

_LEDGER_TAG = re.compile(r"(?<![A-Za-z0-9])os-\d{8}-\d{6}-([A-Za-z0-9]{4,})-([A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)*)")
_ID_TOKEN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.\-]*[A-Za-z0-9])?")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")                # clientOrderId reads as client Order Id
_ID_WORDS = r"(?:orders?|ids?|tags?|client)"
_ID_CONTEXT = re.compile(rf"(?i)(?<![a-z]){_ID_WORDS}(?![a-z])[^A-Za-z0-9]*(?:(?:orders?|ids?|tags?|number|no)(?![a-z])[^A-Za-z0-9]*)*$")


def hide_order_ids(text: str) -> str:
    """An order id is shown by its last 4 characters only. A ledger tag (`os-20261113-143501-a1b2c3-XLE`) becomes `…b2c3-XLE`
    (the last 4 characters of its random part, then its fund). Any other id, a run of 8 or more letters, digits, `_`, `-`
    and `.` (with a digit in it, or 16 or more plain letters) that follows the word order, id, tag or client, becomes `…`
    and its last 4 characters."""
    t = _LEDGER_TAG.sub(lambda m: f"…{m.group(1)[-4:]}-{m.group(2)}", text)
    out, last = [], 0
    for m in _ID_TOKEN.finditer(t):
        tok = m.group(0)
        if (len(tok) >= 8 and not tok.isdigit() and (any(c.isdigit() for c in tok) or (len(tok) >= 16 and tok.isalpha()))
                and not t[max(0, m.start() - 1):m.start()] == "…" and _ID_CONTEXT.search(_CAMEL.sub(" ", t[max(0, m.start() - 40):m.start()]))):
            out += [t[last:m.start()], "…" + tok[-4:]]
            last = m.end()
    return "".join(out) + t[last:]


def _unseen_out(text: str) -> str:
    """Format characters (Cf: direction overrides and marks, zero-width characters) and control characters (Cc) are dropped,
    and a line break, tab or separator is a space: nothing in a text can reorder or hide what is around it."""
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if ch in "\n\r\t" or cat in ("Zl", "Zp"):
            out.append(" ")
        elif cat not in ("Cf", "Cc", "Cs"):
            out.append(ch)
    return "".join(out)


def clean(text, limit: int = 300) -> str:
    """One line of text that is safe to show or write: format and control characters dropped, a remembered secret replaced by
    the package's own guard, an order id cut to its last 4 characters, any run of 9 or more digits (an account number) or
    digit groups written together replaced, and no more than `limit` characters."""
    t, _ = redact(_unseen_out(str(text)))
    t = hide_order_ids(t)
    t = _GROUPED_DIGITS.sub("<number>", t)
    t = _LONG_DIGITS.sub("<number>", t)
    t = " ".join(t.split())
    return t if len(t) <= limit else t[:limit - 1] + "…"
'''
REV3_RULES = r'''
_LONG_DIGITS = re.compile(r"(?<!\d)(?<!\d\.)\d{9,}(?!\d)(?!\.\d)")          # a number with a decimal point is a price, not an account
_GROUPED_DIGITS = re.compile(r"(?<!\d)\d{4}(?:[ -]\d{4}){2,}(?!\d)")          # card-style: 3 or more groups of exactly four

_RANDOM = r"[0-9A-Fa-f]{6}"
_FUND = r"[A-Z0-9]+(?:[.\-][A-Z0-9]+)*"
_OUR_TAG = re.compile(rf"(?<![A-Za-z0-9])os-\d{{8}}-\d{{6}}-({_RANDOM})(?:-({_FUND}))?(?![A-Za-z0-9])")
_ID_TOKEN = re.compile(r"(?<![A-Za-z0-9_.\-])[A-Za-z0-9_\-]+(?![A-Za-z0-9_\-])")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")                # clientOrderId reads as client Order Id
_ID_KEYWORD = re.compile(r"(?i)(?<![a-z])(?:orders?|ids?|refs?|tags?|client)(?![a-z])")
_CLAUSE_END = re.compile(r"(?:;|\.\s)")
_DATEISH = re.compile(r"\d{4}-\d\d-\d\d(?:T\d\d)?|\d{8}(?:T\d{0,6}Z?)?|\d{8}-\d{6}")
ID_MIN = 12


def _is_id(tok: str) -> bool:
    """A run of 12 or more letters and digits (`_` and `-` may join them) holding a digit: not a date or time, not a Kalshi ticker."""
    return (len(tok) >= ID_MIN and any(c.isdigit() for c in tok) and not tok.isdigit() and not _DATEISH.fullmatch(tok)
            and not (tok.startswith("KX") and tok == tok.upper()))


def hide_order_ids(text: str) -> str:
    """An order id is shown by its last 4 characters only. Our own ledger tag (`os-20261113-143501-a1b2c3-XLE`) and batch id
    (`os-20261113-143501-a1b2c3`) are shortened wherever they stand (`…b2c3-XLE`, `…b2c3`: the last 4 of the random part, then
    the fund). Any other id, a run of 12 or more letters and digits with a digit in it and no dot (`_` and `-` may join its
    parts), that follows one of the words order, orders, id, ids, ref, tag or client anywhere in the same clause (lists and
    `id is X` included) becomes `…` and its last 4 characters. Dates and times, numbers with a decimal point, file names, version
    strings, Kalshi tickers (upper case, beginning KX), funds, prices and counts are never shortened."""
    t = _OUR_TAG.sub(lambda m: "…" + m.group(1)[-4:] + (f"-{m.group(2)}" if m.group(2) else ""), text)
    out, last = [], 0
    for m in _ID_TOKEN.finditer(t):
        tok = m.group(0)
        if not _is_id(tok) or t[max(0, m.start() - 1):m.start()] == "…":
            continue
        if m.end() < len(t) and t[m.end()] == "." and m.end() + 1 < len(t) and t[m.end() + 1].isalnum():
            continue                                                     # inside a dotted name: a file, a version, a host
        starts = [c.end() for c in _CLAUSE_END.finditer(t, 0, m.start())]
        clause = _CAMEL.sub(" ", t[(starts[-1] if starts else 0):m.start()])
        if _ID_KEYWORD.search(clause):
            out += [t[last:m.start()], "…" + tok[-4:]]
            last = m.end()
    return "".join(out) + t[last:]


def _unseen_out(text: str) -> str:
    """Format characters (Cf: direction overrides and marks, zero-width characters) and control characters (Cc) are dropped,
    and a line break, tab or separator is a space: nothing in a text can reorder or hide what is around it."""
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if ch in "\n\r\t" or cat in ("Zl", "Zp"):
            out.append(" ")
        elif cat not in ("Cf", "Cc", "Cs"):
            out.append(ch)
    return "".join(out)


def clean(text, limit: int = 300) -> str:
    """One line of text that is safe to show or write: format and control characters dropped, a remembered secret replaced by
    the package's own guard, an order id cut to its last 4 characters, any run of 9 or more digits (an account number) or
    digit groups written together replaced, and no more than `limit` characters."""
    t, _ = redact(_unseen_out(str(text)))
    t = hide_order_ids(t)
    t = _GROUPED_DIGITS.sub("<number>", t)
    t = _LONG_DIGITS.sub("<number>", t)
    t = " ".join(t.split())
    return t if len(t) <= limit else t[:limit - 1] + "…"
'''


def earlier_clean(source, name):
    ns = {"re": re, "unicodedata": unicodedata, "redact": s.redact, "__name__": name}
    exec(compile(source, name, "exec"), ns)
    return ns["clean"]


REV2_CLEAN, REV3_CLEAN = earlier_clean(REV2_RULES, "rev2"), earlier_clean(REV3_RULES, "rev3")
UNLIMITED = 10 ** 7
LIMITS = (40, 80, 120, 300)
FILLER = "ee eee e eeee ee e eee eeeee "                       # only the letter e: any other character shown came from a value
PLANT = {   # what is put in a text, and what may show of it: the placeholder of its mask, or what the earlier revisions showed
    "account": ("987654321098", []),
    "account in fours": ("9876 5432 1098", []),
    "account in threes": ("987-654-321-098", []),
    "long digit run": ("98765432109876543", []),
    "ledger tag": ("os-20261113-143501-a1b2c3-XLE", ["…b2c3-XLE"]),
    "batch id": ("os-20261113-143501-a1b2c3", ["…b2c3"]),
    "id after a word": ("order QZ7XK9VW3JQ8RT", ["order", "…Q8RT"]),
    "short id after a word": ("order QZ7XK9VW", ["order", "…K9VW"]),
    "key": (TRADING["BROKER_API_KEY"], []),
    "secret in capitals": (TRADING["BROKER_API_SECRET"].upper(), []),
    "recovery phrase": (PHRASE, []),
    "phrase on lines": ("\n  ".join(PHRASE.split()), []),
}
MASKS = ["<secret>", "<number>"]


def beyond_masks(out, allowed):
    """What `out` shows besides the filler, the masks and the allowed placeholders (and, at a cut, the start of one of them)."""
    body = out[:-1] if out.endswith("…") else out
    places = sorted(allowed + MASKS, key=len, reverse=True)
    for p in places:
        body = body.replace(p, " ")
    if out.endswith("…"):
        for p in places:
            cut = next((k for k in range(len(p) - 1, 0, -1) if body.endswith(p[:k])), 0)
            if cut:
                body = body[:-cut]
                break
    return body.replace("e", "").replace(" ", "")


@pytest.fixture
def registered(world, trading_file):
    al.register_trading_file(world.env())
    return world


@pytest.mark.parametrize("name", sorted(PLANT))
def test_a_value_slid_across_the_length_limit_never_shows_a_part_at_any_limit(registered, name):
    value, allowed = PLANT[name]
    for limit in LIMITS:
        for start in range(limit - 60, limit + 61):
            lead = (FILLER * (start // len(FILLER) + 2))[:max(0, start - 1)] + " "
            text = lead + value + " " + FILLER * 4
            out = al.clean(text, limit)
            assert len(out) <= limit and beyond_masks(out, allowed) == "", (name, limit, start, out[-60:])


@pytest.mark.parametrize("name", sorted(PLANT))
def test_a_value_slid_across_the_raw_bound_never_shows_a_part(registered, name):
    """Padding that cleaning removes (spaces, line breaks, zero-width characters) puts the value at the front of what is shown;
    every offset from TAIL + 50 before the raw bound to 50 after it."""
    value, allowed = PLANT[name]
    for limit in (120, 300):
        raw_max = max(20 * limit, 4096)
        for d in range(-(al.TAIL + 50), 51):
            pad = ("\u200b" if d % 25 == 0 else "\n" if d % 2 else " ") * (raw_max + d)
            text = pad + value + " " + FILLER * 300
            out = al.clean(text, limit)
            assert len(out) <= limit and beyond_masks(out, allowed) == "", (name, limit, d, out)
            assert al.clean(out, limit) == out


READABLE = ["2026-11-13", "2026-11-13T17:30:00Z", "17:30:00", "60.25", "1234.5", "450.00", "5 shares", "XLE", "BRK.B", "BTC-USD",
            "report_2026.csv", "orders-20261113-143501.json", "1.2.3-rc1", "python3.12.11", "KXNBAGAME-26NOV13LALBOS-LAL",
            "KXHIGHNY-26NOV13-T60"]


def test_readable_things_stay_readable_wherever_the_cut_does_not_reach_them():
    rng = random.Random(55)
    for i in range(3000):
        items = [rng.choice(READABLE) for _ in range(rng.randint(3, 30))]
        words = [rng.choice(["order", "id", "tag", "client", "ref", "the", "fund", "at", "on"]) + " " + x for x in items]
        text = rng.choice([" ", ", ", "; "]).join(words)
        for limit in LIMITS + (UNLIMITED,):
            out = al.clean(text, limit)
            reach = limit - 1 - max(map(len, words)) - 2
            pos = 0
            for w in words:
                end = text.index(w, pos) + len(w)
                if end <= reach:
                    assert w in out, (text, limit, w, out)
                pos = end
        assert al.clean(text, UNLIMITED) == text


ALNUM_UP = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"


def generated(rng):
    """A text made of words, readable things, id words, joints and planted values (each new), and the values with what of each
    may be shown: (kind, value, what may show)."""
    parts, planted = [], []
    for _ in range(rng.randint(2, 16)):
        k = rng.random()
        if k < 0.25:
            parts.append(rng.choice(["the venue said", "for", "and", "was refused", "is open", "filled 5 at", "fund", "shares"]))
        elif k < 0.37:
            parts.append(rng.choice(READABLE))
        elif k < 0.52:
            parts.append(rng.choice(["order", "orders", "id", "ids", "ref", "tag", "client", "Order ID:", "clientOrderId:",
                                     "order number", "id=", "the id is", "venue_order_id=", "tag no", ";", ",", "number", "is"]))
        elif k < 0.64:
            while True:
                v = "".join(rng.choice(ALNUM_UP) for _ in range(rng.randint(8, 30)))
                if any(c.isdigit() for c in v) and not v.isdigit() and not v.startswith("KX"):
                    break
            parts.append(v)
            planted.append(("id", v, v[-4:]))
        elif k < 0.70:
            rand = "".join(rng.choice("0123456789abcdef") for _ in range(6)) if rng.random() < 0.6 else \
                "".join(rng.choice(ALNUM_UP) for _ in range(rng.randint(4, 9)))
            fund = rng.choice(["", "-XLE", "-BRK.B", "-BTC-USD"])
            v = f"os-2026{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}-{rng.randint(10, 23):02d}{rng.randint(10, 59):02d}" \
                f"{rng.randint(10, 59):02d}-{rand}{fund}"
            parts.append(v)
            planted.append(("tag", v, rand[-4:] + fund))
        elif k < 0.82:
            if rng.random() < 0.5:
                v = "".join(rng.choice("0123456789") for _ in range(rng.randint(9, 16)))
            else:
                v = rng.choice([" ", "-"]).join("".join(rng.choice("0123456789") for _ in range(rng.randint(3, 4)))
                                                for _ in range(rng.randint(3, 5)))
            parts.append(v)
            planted.append(("digits", v, ""))
        else:
            name = rng.choice(["BROKER_API_KEY", "BROKER_API_SECRET", "WEBULL_ACCOUNT_ID", "WALLET_MNEMONIC"])
            if name == "WALLET_MNEMONIC":
                words = PHRASE.split()
                a = rng.randint(0, len(words) - 4)
                v = spaced(rng, words[a:a + rng.randint(4, len(words) - a)])
            else:
                v = rng.choice([str.upper, str.lower, str])(TRADING[name])
            parts.append(v)
            planted.append(("secret", v, ""))
    text, before = "", None
    for p in parts:
        near_readable = any(q is not None and (q in READABLE or "." in q) for q in (p, before))   # a dotted name (a fund
        joints = [" ", " ", " ", "  ", "\n", "\t", "\u3000", ", ", "; ", ". ", "=", ": "] + ([] if near_readable else ["", "-"])
        text += (rng.choice(joints) if text else "") + p             # like BRK.B too), a date or a ticker glued to an id is a
                                                                      # readable name (see hide_order_ids): not compared here
        before = p
    return text, planted


def hidden_core(kind, value, shown):
    """What of a planted value an earlier revision did not show when it masked it: all of it but its last 4 for an id, all of a
    tag but the last 4 of its random part and its fund, all of anything else."""
    norm = " ".join(value.split())
    if kind == "id":
        return norm[:-4]
    if kind == "tag":
        return norm[:len(norm) - len(shown)]
    return norm


def windows(text, n=4):
    return {text[i:i + n].lower() for i in range(len(text) - n + 1)}


def shown_of(x, out, old, kind, value, shown):
    """The pieces of a planted value's hidden part found in `out` that the earlier revision's output `old` does not show and the
    text does not hold anywhere else, and at a cut, the start of it ending the text (two characters or more)."""
    core = hidden_core(kind, value, shown).lower()
    elsewhere = " ".join(x.replace(value, " ").split()).lower() + " " + old
    low = out.lower()
    found = [w for w in windows(core) if w in low and w not in elsewhere]
    if out.endswith("…"):
        body = low[:-1]
        found += [core[:k] for k in range(2, len(core) + 1) if body.endswith(core[:k]) and core[:k] not in elsewhere]
    return found


def test_generated_texts_never_show_what_revision_2_or_3_masked_at_any_limit_and_cleaning_is_a_fixed_point(registered):
    rng = random.Random(5005)
    compared = hidden = 0
    for i in range(20000):
        x, planted = generated(rng)
        old = {"rev 2": REV2_CLEAN(x, UNLIMITED).lower(), "rev 3": REV3_CLEAN(x, UNLIMITED).lower()}
        masked = [(p, o) for p in planted for o in old.values() if " ".join(p[1].split()).lower() not in o]
        compared += len(planted)
        hidden += len(masked)
        for limit in LIMITS + (UNLIMITED,):
            out = al.clean(x, limit)
            assert len(out) <= limit
            for p, o in masked:
                assert shown_of(x, out, o, *p) == [], (x, limit, p, out)
            if limit != UNLIMITED:
                assert al.clean(out, limit) == out, (x, limit, out)
    assert compared > 50000 and hidden > 20000                     # the comparison had something to compare


def test_a_key_is_its_own_key_form_for_generated_keys(registered):
    rng = random.Random(77)
    for i in range(5000):
        x, _ = generated(rng)
        key = al._key_part(rng.choice(["job:com.valedia.", "orders:", "step-stopped:", ""]) + x)
        assert al._key_part(key) == key and al.Alert(key, "warning", "t", "d").key == key, (x, key)
        assert all(c.isalnum() or c in "_.:-" for c in key) and len(key) <= 80


@pytest.mark.parametrize("shape", ["words and ids", "padding then a value", "digits", "letters with accents", "zero-width"])
def test_a_five_megabyte_text_is_cleaned_in_under_fifty_milliseconds(registered, shape):
    body = {"words and ids": "the venue said order QZ7XK9VW3JQ8RT for 2026-11-13 " * 100000,
            "padding then a value": " " * 5_000_000 + TRADING["BROKER_API_KEY"],
            "digits": "1234567890" * 500000, "letters with accents": "é" * 5_000_000,
            "zero-width": "\u200b" * 5_000_000 + PHRASE}[shape][:5_000_000]
    best = None
    for _ in range(3):                                                  # the best of three: the machine may be busy with others
        t0 = time.perf_counter()
        out = al.clean(body)
        took = time.perf_counter() - t0
        best = took if best is None else min(best, took)
    assert best < 0.05, (shape, best)
    assert al.clean(out) == out


# ----------------------------------------------------------------------------------------------- through the real commands

FORMS = {"key": [TRADING["BROKER_API_KEY"], TRADING["BROKER_API_KEY"].lower(), TRADING["BROKER_API_KEY"].upper()],
         "secret": [TRADING["BROKER_API_SECRET"], TRADING["BROKER_API_SECRET"].upper()],
         "account": ["987654321098", "9876 5432 1098", "987-654-321-098", "987 654 321 098"],
         "phrase": [PHRASE, "  ".join(PHRASE.split()), "\n".join(PHRASE.split()), "_".join(PHRASE.split())]}
ALL_FORMS = [f for forms in FORMS.values() for f in forms]
PHRASE_WORD = re.compile(r"(?i)(?<![a-z])(?:" + "|".join(PHRASE.split()) + r")(?![a-z])")


def any_part(text):
    """Any piece (4 characters, in any letter case) of the key, the secret or the account number, or any word of the phrase."""
    low = text.lower()
    found = [w for name in ("BROKER_API_KEY", "BROKER_API_SECRET", "WEBULL_ACCOUNT_ID") for w in windows(TRADING[name]) if w in low]
    return found + PHRASE_WORD.findall(text)


def across(value, at, size=None):
    """A long text with the value starting at `at`: words before it and after it, or (`size`) padding that cleaning removes,
    so the value stands across the raw bound of a text cut to `size`."""
    if size is not None:
        return " " * at + value + " " + FILLER * ((size - at) // len(FILLER) + 30)
    return (FILLER * (at // len(FILLER) + 1))[:max(0, at - 1)] + " " + value + " " + FILLER * 20


def placements(prefix_len, limit):
    """Where to put a value in a text that is shown cut to `limit` behind `prefix_len` characters: at the head, in the middle,
    at offsets across the length cut and across the raw bound."""
    raw = max(20 * limit, 4096)
    out = [(0, None), (limit // 2, None)]
    out += [(limit - 1 - prefix_len - j, None) for j in (1, 3, 6, 10)]
    out += [(raw - prefix_len - j, raw) for j in (1, 4, 8, 12)]
    return out


def test_no_part_of_any_value_reaches_any_output_of_the_real_commands_at_the_head_middle_or_across_either_cut(world, trading_file, monkeypatch, capsys):
    spy = Spy()
    monkeypatch.setattr(al.subprocess, "run", spy)
    n = 0
    for form in ALL_FORMS:
        for at, size in placements(len("2026-11-13 v000: "), 300):            # the detail: day, venue, reason; cut to 300
            world.write_step("stopped", "send", across(form, max(0, at), size), venue=f"v{n:03d}")
            n += 1
        for at, size in placements(len("The order step stopped at stage "), 120):   # the title: the stage; cut to 120
            world.write_step("stopped", across(form, max(0, at), size), "r", venue=f"v{n:03d}")
            n += 1
    world.jobs = world.jobs + [("-", "9", "com.valedia." + across(f, 60).replace(" ", "")) for f in ALL_FORMS[:6]]
    world.write_picks_log(end_ago=600, extra=["schedule failed " + across(ALL_FORMS[0], 250)])
    assert al.main(["watch", "--notify"], env=world.env()) == 1
    assert al.main(["list"], env=world.env()) == 1
    seen = capsys.readouterr()
    assert len([x for x in world.lines() if x["key"].startswith("step-stopped")]) == n
    texts = [seen.out, seen.err, world.alerts_file.read_text(), " ".join(" ".join(c) for c in spy.calls),
             (world.alerts_file.parent / "notifications.jsonl").read_text()]
    for i, text in enumerate(texts):
        assert any_part(text) == [], (i, any_part(text)[:10])


def test_no_part_of_a_value_in_any_stored_field_gets_out_through_the_real_commands(world, trading_file, monkeypatch, capsys):
    spy = Spy()
    monkeypatch.setattr(al.subprocess, "run", spy)
    rows, notes = [], []
    for i, form in enumerate(ALL_FORMS):
        for field, limit in (("title", 120), ("detail", 300), ("time", 40), ("level", 20), ("key", 80)):
            for k, (at, size) in enumerate(placements(len(f"job:com.valedia.x{i}-{field}-0 ") if field == "key" else 0, limit)):
                row = {"time": "2026-11-13T19:00:00Z", "level": "warning", "key": f"job:com.valedia.x{i}-{field}-{k}", "title": "t",
                       "detail": "d"}
                row[field] = (row["key"] + " " if field == "key" else "") + across(form, max(0, at), size)
                rows.append(row)
                notes.append({"time": "x", "sender": across(form, max(0, at), size), "key": row["key"], "since": row["time"],
                              "result": "failed"})
    folder = world.alerts_file.parent
    folder.mkdir(parents=True, exist_ok=True)
    world.alerts_file.write_text("".join(json.dumps(r) + "\n" for r in rows))
    (folder / "notifications.jsonl").write_text("".join(json.dumps(r) + "\n" for r in notes))
    old = (world.alerts_file.read_text(), (folder / "notifications.jsonl").read_text())
    outputs = []
    for argv in (["list"], ["watch", "--notify"], ["list"]):
        al.main(argv, env=world.env())
        seen = capsys.readouterr()
        outputs += [seen.out, seen.err]
    texts = outputs + [world.alerts_file.read_text()[len(old[0]):], (folder / "notifications.jsonl").read_text()[len(old[1]):],
                       " ".join(" ".join(c) for c in spy.calls)]
    assert any("<secret>" in t or "<number>" in t for t in texts[:2])
    for i, text in enumerate(texts):
        assert any_part(text) == [], (i, any_part(text)[:10])


# ----------------------------------------------------------------------------------------------- withheld: the guard on the whole key

def test_a_stored_line_is_withheld_when_the_guard_masks_any_part_of_its_whole_key(world, trading_file):
    al.register_trading_file(world.env())
    key = TRADING["BROKER_API_KEY"]
    rows = [{"time": "2026-11-13T19:00:00Z", "level": "warning", "key": "job:com.valedia." + "x" * 70 + " " + key, "title": "t", "detail": "d"},
            {"time": "2026-11-13T19:00:00Z", "level": "warning", "key": "job:com.valedia." + "y" * 300 + key, "title": "t", "detail": "d"},
            {"time": "2026-11-13T19:00:00Z", "level": "warning", "key": "job:" + "z" * 75 + "_" + "_".join(PHRASE.split()[:5]), "title": "t", "detail": "d"},
            {"time": "2026-11-13T19:00:00Z", "level": "warning", "key": "job:com.valedia.55512345678901", "title": "t", "detail": "d"}]
    world.alerts_file.parent.mkdir(parents=True, exist_ok=True)
    world.alerts_file.write_text("".join(json.dumps(r) + "\n" for r in rows))
    open_rows, withheld = al.read_alert_file(world.alerts_file)
    assert withheld == 3 and [r["key"] for r in open_rows] == ["job:com.valedia._number_"]      # digits alone: not the guard
    for _ in range(2):
        al.watch(world.env())
    assert [x["key"] for x in world.lines()[len(rows):]] == ["job:com.valedia._number_"]        # cleared once; the withheld never


# ----------------------------------------------------------------------------------------------- a stop of the unattended run

def write_raw_summary(w, day_file, entry):
    """A summary written straight into a journal file, so a test can give it any day and any keys."""
    with (w.root / "journal" / f"{day_file}.jsonl").open("a") as fh:
        fh.write(json.dumps({"type": "order_step", "venue": "webull-sandbox", "event": "summary", "orders_sent": 0, "sent": [],
                             "refused": [], "left_over": [], **entry}) + "\n")


def test_a_later_stop_at_stage_session_never_clears_a_stop(world):
    world.write_step("stopped", "send", "the venue refused the session")
    assert keys(watch(world)) == ["step-stopped:webull-sandbox"]
    world.write_step("stopped", "session", "outside the regular session window 09:35 to 15:45 New York time (it is 16:10)")
    world.write_step("stopped", "session", "2026-11-13 (Friday) is not a trading day")
    r = watch(world)
    assert r.cleared == [] and [a["key"] for a in r.open] == ["step-stopped:webull-sandbox"]


@pytest.mark.parametrize("status", [None, 1, True, ["done"], {"done": 1}, "Done", "DONE", " done", "done ", "done\n", "ok", "", "finished"],
                         ids=repr)
def test_a_later_summary_whose_status_is_not_exactly_done_never_clears_and_is_a_warning_once_a_day(world, status):
    world.write_step("stopped", "send", "the venue refused the session")
    assert keys(watch(world)) == ["step-stopped:webull-sandbox"]
    world.write_step(status)
    r = watch(world)
    assert r.cleared == [] and "step-stopped:webull-sandbox" in [a["key"] for a in r.open]
    (a,) = [x for x in r.raised if x.key.startswith("step-unreadable")]
    assert a.key == "step-unreadable:2026-11-13" and a.level == "warning" and "could not be read" in a.title
    world.write_step(status)
    assert not [x for x in watch(world).raised if x.key.startswith("step-unreadable")]          # once a day
    later = world.env()
    later.now = NOW + 86400
    assert [c.key for c in al.watch(later).cleared if c.key.startswith("step-unreadable")]


def test_a_summary_with_no_status_at_all_never_clears_and_is_a_warning(world):
    world.write_step("stopped", "send", "the venue refused the session")
    watch(world)
    write_raw_summary(world, "2026-11-13", {"day": "2026-11-13", "stage": None, "reason": None})
    r = watch(world)
    assert r.cleared == [] and "step-unreadable:2026-11-13" in keys(r)


@pytest.mark.parametrize("mode_key", ["Mode", "MODE", " mode", "mode ", "mOdE"])
def test_a_done_summary_with_a_key_like_mode_in_another_case_never_clears_a_stop(world, mode_key):
    world.write_step("stopped", "send", "the venue refused the session")
    watch(world)
    write_raw_summary(world, "2026-11-13", {"day": "2026-11-13", "status": "done", "stage": None, "reason": None, mode_key: "propose"})
    r = watch(world)
    assert r.cleared == [] and [a["key"] for a in r.open] == ["step-stopped:webull-sandbox"]


def test_a_done_summary_in_a_journal_file_dated_after_today_never_clears_a_stop(world):
    world.write_step("stopped", "send", "the venue refused the session")
    watch(world)
    write_raw_summary(world, "2026-11-14", {"day": "2026-11-13", "status": "done", "stage": None, "reason": None})
    world.write_step("done", day="2026-11-14")
    r = watch(world)
    assert r.cleared == [] and [a["key"] for a in r.open] == ["step-stopped:webull-sandbox"]


def test_a_later_done_of_the_same_day_and_venue_clears_it_and_a_later_stop_opens_it_again(world):
    world.write_step("stopped", "reconcile", "the ledger failed")
    world.write_step("done", venue="other-venue")                                  # another venue's run clears nothing here
    assert keys(watch(world)) == ["step-stopped:webull-sandbox"]
    world.write_step("done")
    assert [c.key for c in watch(world).cleared] == ["step-stopped:webull-sandbox"]
    world.write_step("stopped", "after", "the last reconcile could not be completed")
    assert keys(watch(world)) == ["step-stopped:webull-sandbox"]


def test_any_stop_of_the_day_counts_not_only_the_last_summary(world):
    world.write_step("stopped", "send", "the venue refused the session")
    world.write_step("stopped", "session", "outside the regular session window 09:35 to 15:45 New York time (it is 16:10)")
    (a,) = watch(world).raised
    assert a.key == "step-stopped:webull-sandbox" and "send" in a.title and "refused the session" in a.detail


def test_a_stop_of_an_earlier_day_is_over_when_the_day_passes(world):
    world.write_step("stopped", "send", "the venue refused", day="2026-11-12")
    assert not [a for a in watch(world).raised if a.key.startswith("step-stopped")]
    world.write_step("stopped", "send", "the venue refused")
    assert keys(watch(world)) == ["step-stopped:webull-sandbox"]
    tomorrow = world.env()
    tomorrow.now = NOW + 86400
    assert [c.key for c in al.watch(tomorrow).cleared if c.key.startswith("step-stopped")] == ["step-stopped:webull-sandbox"]


# =====================================================================================================================
# rev 6: the tail dropped at the raw bound, pinned by what it must hide (not by the module's own constant)
# =====================================================================================================================

LONG_PHRASE = ("counterrevolutionaries incomprehensibilities electroencephalographs uncharacteristically internationalizations "
               "disproportionateness interdenominational institutionalization overintellectualizing compartmentalization "
               "misinterpretations antiestablishmentarian")                    # 12 words of 18 to 22 letters
KNOWN_PHRASE = ("photosynthesizingly thermoluminescences crystallographically spectrophotometrical ultramicroscopically "
                "hypercholesterolemia immunohistochemistry neuropharmacologists otorhinolaryngology psychophysiologically "
                "radiotelegraphically superconductivities")                    # 12 more, known to the guard by remember()
RAW_OFFSETS = range(-300, 51)                                                  # fixed: never read from the module


def slide_across_the_raw_bound(value):
    """Every offset from 300 characters before the raw bound to 50 after it, behind padding that cleaning removes: what is
    shown beyond the masks and the filler (nothing, when the tail dropped at the raw bound is long enough)."""
    shown = []
    for limit in (120, 300):
        raw_max = max(20 * limit, 4096)
        for d in RAW_OFFSETS:
            out = al.clean(" " * (raw_max + d) + value + " " + FILLER * 300, limit)
            if beyond_masks(out, []):
                shown.append((limit, d, out[:60]))
    return shown


def test_a_long_registered_phrase_and_the_longest_registered_value_slid_across_the_raw_bound_show_nothing(world, tmp_path, monkeypatch):
    """The guard matches a phrase only from 4 words on: up to 3 words and a part of the 4th (here up to 88 characters) of a
    phrase cut by the raw bound pass it, and only the tail dropped at the raw bound hides them."""
    f = tmp_path / "long-trading.env"
    f.write_text("".join(f"{k}={v}\n" for k, v in {**TRADING, "WALLET_MNEMONIC": LONG_PHRASE}.items()))
    os.chmod(f, 0o600)
    monkeypatch.setenv("VALEDIA_ENV_FILE", str(f))
    al.register_trading_file(world.env())
    longest = max((v for k, v in TRADING.items() if k != "WALLET_MNEMONIC"), key=len)
    assert len(LONG_PHRASE) > len(longest)                                     # the long phrase is the longest registered value
    assert s.redact(" ".join(LONG_PHRASE.split()[:3]))[1] == 0                 # three words alone pass the guard
    assert slide_across_the_raw_bound(LONG_PHRASE) == []
    assert slide_across_the_raw_bound("  ".join(LONG_PHRASE.split())) == []


def test_a_long_phrase_the_guard_knows_from_elsewhere_slid_across_the_raw_bound_shows_nothing(monkeypatch):
    """A phrase the guard was given by another way (`remember`, as `load_env` does for any command), in a run that registered
    no trading file: the tail must hide its part that lay across the raw bound by its own size."""
    monkeypatch.setitem(al._registered, "longest", 0)                          # no trading file registered in this run
    s.remember(KNOWN_PHRASE)
    assert s.redact(" ".join(KNOWN_PHRASE.split()[:4]))[1] == 1 and s.redact(" ".join(KNOWN_PHRASE.split()[:3]))[1] == 0
    assert slide_across_the_raw_bound(KNOWN_PHRASE) == []


# ---- T50 addendum 4 (1): the alerts commands never open the live file and register nothing from a file naming a live
#      name; they refuse in words

T50_KEY, T50_SECRET = "ZqLiveKey7Rt4Kw9PmXv2Bn", "ZqLiveSecret3LbNHs4GdYp8"          # made up
_T50_WATCHED: dict = {}
_T50_OPENS: list = []


def _t50_audit(event, args):
    if event == "open" and args and _T50_WATCHED.get("inode"):
        try:
            st = os.stat(args[0]) if isinstance(args[0], (str, bytes, os.PathLike)) else None
        except (OSError, ValueError, TypeError):
            st = None
        if st is not None and (st.st_dev, st.st_ino) == _T50_WATCHED["inode"]:
            _T50_OPENS.append("opened")


sys.addaudithook(_t50_audit)


@pytest.fixture
def t50_live(tmp_path, monkeypatch):
    folder = tmp_path / "home" / ".hermes" / "secrets"
    folder.mkdir(parents=True)
    folder.chmod(0o700)
    live = folder / "webull-live.env"
    live.write_text(f"WEBULL_LIVE_APP_KEY={T50_KEY}\nWEBULL_LIVE_APP_SECRET={T50_SECRET}\n")
    live.chmod(0o600)
    monkeypatch.setattr(s, "LIVE_ENV_FILE", live)
    monkeypatch.setattr(s, "read_live_pair", lambda *a, **k: pytest.fail("the live file was read by its reader"))
    links = tmp_path / "links"
    links.mkdir(mode=0o700)
    (links / "trading.env").symlink_to(live)
    copy = tmp_path / "copies" / "trading.env"
    copy.parent.mkdir(mode=0o700)
    copy.write_bytes(live.read_bytes())
    copy.chmod(0o600)
    st = os.stat(live)
    _T50_OPENS.clear()
    _T50_WATCHED["inode"] = (st.st_dev, st.st_ino)
    yield {"live": live, "link": links / "trading.env", "spelling": folder / ".." / "secrets" / live.name, "copy": copy}
    _T50_WATCHED.clear()
    _T50_OPENS.clear()


@pytest.mark.parametrize("argv", [["watch"], ["watch", "--notify"], ["list"]])
@pytest.mark.parametrize("way", ["live", "link", "spelling", "copy"])
def test_t50_a4_the_alerts_commands_refuse_the_live_file_however_named_and_register_nothing(world, t50_live, monkeypatch,
                                                                                           capsys, way, argv):
    monkeypatch.setenv("VALEDIA_ENV_FILE", str(t50_live[way]))
    for name in ("WEBULL_LIVE_APP_KEY", "WEBULL_LIVE_APP_SECRET"):
        monkeypatch.delenv(name, raising=False)
    longest = al._registered["longest"]
    env = world.env()
    _T50_OPENS.clear()
    assert al.main(argv, env=env) == 2
    printed = capsys.readouterr()
    clean = T50_KEY not in printed.out + printed.err and T50_SECRET not in printed.out + printed.err
    assert clean and "trading file" in printed.err and "nothing was done" in printed.err
    assert ("webull-live.env" if way != "copy" else "WEBULL_LIVE_APP_KEY") in printed.err, printed.err
    assert _T50_OPENS == [] or way == "copy", (way, len(_T50_OPENS))   # the live file itself is never opened
    assert "trading_values" not in env._cache and al._registered["longest"] == longest      # nothing registered
    assert not world.alerts_file.exists()
    assert "WEBULL_LIVE_APP_KEY" not in os.environ and "WEBULL_LIVE_APP_SECRET" not in os.environ


def test_t50_a4_an_ordinary_file_naming_a_live_name_registers_none_of_its_values(world, tmp_path, monkeypatch, capsys):
    f = tmp_path / "trading.env"
    f.write_text("".join(f"{k}={v}\n" for k, v in TRADING.items()) + f"WEBULL_LIVE_APP_KEY={T50_KEY}\n")
    f.chmod(0o600)
    env = world.env()
    env.trading_file = f
    longest = al._registered["longest"]
    with pytest.raises(al.TradingFileError) as exc:
        al.register_trading_file(env)
    why = str(exc.value)
    clean = T50_KEY not in why and all(v not in why for v in TRADING.values())
    assert clean and "WEBULL_LIVE_APP_KEY" in why and "nothing was done" in why
    assert "trading_values" not in env._cache and al._registered["longest"] == longest
    assert al.clean(f"a {TRADING['BROKER_API_KEY']} b") == f"a {TRADING['BROKER_API_KEY']} b"   # none of its values known


# ---- T51 A1 (addendum 3, ruling 2): a layout-3 ledger and a layout-4 one, each made by the code that writes it, read alike

@pytest.fixture(scope="module")
def ledger_module_at_33ace6f(tmp_path_factory):
    """The ledger module as 33ace6f had it (it writes layout 3), from git, under a name of its own. Module-scoped, so
    git runs before `no_real_launch` refuses every program for the test itself."""
    import importlib.util
    where = tmp_path_factory.mktemp("at-33ace6f")
    import io
    import tarfile
    rel = "valedia_trader/execution_and_risk/orders.py"
    root = Path(__file__).resolve().parents[2]
    got = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", "33ace6f", rel], capture_output=True, timeout=60)
    assert got.returncode == 0, got.stderr.decode(errors="replace")[:300]
    with tarfile.open(fileobj=io.BytesIO(got.stdout)) as tar:
        tar.extractall(where, filter="data")
    spec = importlib.util.spec_from_file_location("orders_at_33ace6f_alerts", where / rel)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    assert old.LAYOUT_VERSION == 3
    return old


def _filled_ledger(module, path: Path, unknown: bool) -> None:
    """With the given ledger module: a clean reconcile, an order filled and applied, another open, and (with
    `unknown`) one whose submit raised, which blocks the ledger."""
    clock = [NOW - 600]
    led = module.OrderLedger(path, not_sent=(), clock=lambda: clock[0])
    v = _Booked()
    led.reconcile(v, {})
    led.intend("paper-XLE-1", "XLE", 5.0, 60.0, limit_price=60.6)
    led.submit_once(v, "paper-XLE-1")
    v.book["paper-XLE-1"].update(state="filled", filled_units=5.0, avg_price=60.1, fee=0.05)
    clock[0] += 60
    led.reconcile(v, {})
    led.mark_applied([f["id"] for f in led.unapplied_fills()])
    v.held = {"XLE": 5.0}
    clock[0] += 60
    led.reconcile(v, {"XLE": 5.0})
    led.intend("paper-SPY-2", "SPY", 2.0, 600.0, limit_price=606.0)
    led.submit_once(v, "paper-SPY-2")
    if unknown:
        led.intend("paper-QQQ-3", "QQQ", 1.0, 500.0, limit_price=505.0)
        led.submit_once(_Venue(raises=True), "paper-QQQ-3")
    led.close()


class _Booked:
    name = "fake"

    def __init__(self):
        self.book, self.held = {}, {}

    def submit(self, symbol, units, tag, price=None, limit_price=None):
        self.book[tag] = {"state": "open", "filled_units": 0.0, "avg_price": None, "fee": None,
                          "venue_order_id": f"V-{tag}", "raw_status": "NEW"}
        return {"status": "accepted", "venue_order_id": f"V-{tag}"}

    def order_status(self, tag):
        got = self.book.get(tag)
        return dict(got) if got is not None else None

    def positions(self):
        return dict(self.held)


@pytest.mark.parametrize("unknown", [False, True])
def test_every_ledger_reader_reads_layout_3_and_layout_4_alike_and_writes_neither(world, ledger_module_at_33ace6f,
                                                                                  unknown):
    from valedia_trader.research_loop import owner_page as op
    from valedia_trader.execution_and_risk import orders as new_orders
    old = ledger_module_at_33ace6f
    seen = {}
    for layout, module in ((3, old), (4, new_orders)):
        world.ledger_path.unlink()
        _filled_ledger(module, world.ledger_path, unknown)
        raw = sqlite3.connect(world.ledger_path)
        assert raw.execute("PRAGMA user_version").fetchone()[0] == layout
        raw.close()
        before = {p.name: p.read_bytes() for p in (world.root / "memory").glob("orders-*")}
        r = watch(world)
        led = al.open_order_ledger(world.ledger_path, lambda: NOW)
        try:
            records = led.orders()
            reasons = led.blocked(al.LEDGER_MAX_AGE_SECONDS)
        finally:
            led.close()
        times, body = op.broker_part(world.ledger_path, world.env(), op.Context(world.env()))
        assert {p.name: p.read_bytes() for p in (world.root / "memory").glob("orders-*")} == before   # nothing written
        assert not any("orders" in f or "layout" in f for f in r.failures), (layout, r.failures)
        assert any(a["key"] == "orders:orders-webull-sandbox" for a in r.open) is unknown, (layout, r.open)
        assert all((o["order_type"], o["collar_price"]) == ("LIMIT", None) for o in records)
        seen[layout] = ([{k: v for k, v in o.items() if k not in ("order_type", "collar_price")} for o in records],
                        reasons, body, times)
    assert seen[3] == seen[4]                                       # the same orders, gate, page and times either way


def test_a_ledger_of_layout_3_with_a_schema_not_layout_3s_is_a_failure_in_words(world):
    world.ledger_path.unlink()
    db = sqlite3.connect(world.ledger_path)
    db.execute("CREATE TABLE orders (tag TEXT)")
    db.execute("PRAGMA user_version = 3")
    db.commit()
    db.close()
    before = world.ledger_path.read_bytes()
    r = watch(world)
    assert r.raised == [] and any("layout 3" in f for f in r.failures), r.failures
    assert world.ledger_path.read_bytes() == before
