"""T51c S4e (addendum 7, R24; ruling R2): the alerts of a shared run.

What is held here:
- every stop of a shared run (practice or live, unattended, propose or approve) at stage `shortfall`, `marks` or
  `state` ALREADY raises an alert through the watchdog's own checks, and S4e leaves those checks alone: the unattended
  run's `step-stopped:<venue>` (critical) and the approval flow's `practice-stopped:<mode>:<day>` (warning). Each names
  the venue, the stage, the day and the plain words of the stop, never an id; a repeated stop of one cause on one day is
  one alert, not one a run;
- the one gap: a run that ENDS done whose last reconcile found a shortfall (summary `shared.shortfalls` not empty)
  raised nothing but the ledger's own `orders:<ledger>` block, which carries no words. S4e adds ONE check for it,
  `shared-shortfall:<venue>:<day>` (a warning), built from the summary's figures only: the venue, the day, the funds and
  their units, the two ordinary causes, and what clears it. One alert for a repeat of the same venue and day; it clears
  when a later shared run of that venue the same day is done with no shortfall.
Everything runs on stand-in venues, clocks and journals in a temporary folder; nothing opens a socket."""
import json
import socket

import pytest

import test_order_step as T
from test_shared_live import LW, LiveLag
from test_shared_run import Lag, SW
from valedia_trader.core import alerts

DAY = "2026-11-13"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise OSError("the T51c S4e tests open no socket")
    for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "create_connection"),
                        (socket, "getaddrinfo")):
        monkeypatch.setattr(owner, name, refuse)


@pytest.fixture
def mk(tmp_path, monkeypatch, capsys):
    count = []

    def make(venue=None, **limits):
        count.append(1)
        return SW(tmp_path / f"w{len(count)}", monkeypatch, capsys, venue, **limits)
    return make


@pytest.fixture
def mkl(tmp_path, monkeypatch, capsys):
    count = []

    def make(venue=None, conf=None, **limits):
        count.append(1)
        return LW(tmp_path / f"l{len(count)}", monkeypatch, capsys, venue, conf, **limits)
    return make


def env_of(w):
    return alerts.Env(root=w.tmp_root, now=w.clock.t, halt_file=None, logs_dir=w.tmp / "logs",
                      launch_agents_dir=w.tmp / "la", launchctl=lambda: "", free_bytes=lambda p: 10 ** 12,
                      sessions=lambda: None)


def step_alerts(w):
    """What the watchdog's checks that read order-step summaries raise now, as {key: alert}."""
    env = env_of(w)
    out = {}
    new = getattr(alerts, "check_shared_shortfall", None)       # S4e's one new check (absent before it is built)
    for check in (alerts.check_order_step, alerts.check_modes, alerts.check_approval_refused, *([new] if new else [])):
        for a in check(env).alerts:
            out[a.key] = a
    return out


def stop_line(w, stage, reason, *, mode=None, venue=None, day=DAY, live=False, status="stopped", shared=True, **extra):
    """An order-step summary line as a shared run journals it, written by the journal's own writer."""
    line = {"type": "order_step", "event": "summary", "day": day, "venue": venue or w.venue.name, "status": status,
            "stage": stage, "reason": reason, "orders_sent": 0, "batch": "b-1"}
    if shared:
        line["shared"] = {"capital": 100000.0, "owner_surplus": []}
    if mode is not None:
        line["mode"] = mode
    if live:
        line["live"] = True
    line.update(extra)
    w.journal.write(day, line)


WORDS = {"shortfall": "the account holds less than the book counts as its own: SPY: 70.0 held, 100.0 counted",
         "marks": "SPY quotes 700, and the book's last mark of it was 500, further than max_drawdown_pct away",
         "state": "the state file and the journal differ on the owner's entries; a person must look"}


# ---- what already raises an alert, and is left alone

@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("mode", [None, "propose", "approve"])
@pytest.mark.parametrize("stage", ["shortfall", "marks", "state"])
def test_every_shared_stop_at_these_stages_already_raises_one_alert_with_venue_stage_day_and_words(mk, stage, mode, live):
    w = mk()
    venue = "webull-production" if live else w.venue.name
    stop_line(w, stage, WORDS[stage], mode=mode, venue=venue, live=live)
    got = step_alerts(w)
    key = f"step-stopped:{venue}" if mode is None else f"practice-stopped:{mode}:{DAY}"
    assert set(got) == {key}, sorted(got)
    a = got[key]
    assert f"stage {stage}" in a.title
    for words in (DAY, venue, WORDS[stage][:40]):
        assert words in a.detail, (words, a.detail)
    assert a.level == ("critical" if mode is None else "warning")


@pytest.mark.parametrize("stage", ["shortfall", "marks", "state"])
def test_a_real_practice_run_stopped_at_the_stage_raises_its_alert_with_the_runs_own_words(mk, stage):
    w = mk(max_drawdown_pct=8.0)
    w.open_book()
    assert w.shared()["status"] == "done"
    if stage == "shortfall":
        w.venue.held["SPY"] -= 30.0
    elif stage == "marks":
        w.prices["SPY"] = 700.0
    else:
        for path in (w.tmp / "journal").glob("*.jsonl"):
            path.write_text("")                           # the witness is gone: the state file and journal differ
    w.later()
    out = w.shared()
    assert out["status"] == "stopped" and out["stage"] == stage, out
    got = step_alerts(w)
    key = f"step-stopped:{w.venue.name}"
    assert key in got and f"stage {stage}" in got[key].title, sorted(got)
    assert DAY in got[key].detail and w.venue.name in got[key].detail
    assert out["reason"][:60] in got[key].detail, (out["reason"], got[key].detail)
    assert "os-" not in got[key].detail and "os-" not in got[key].title


@pytest.mark.parametrize("stage", ["shortfall", "marks"])
def test_a_real_live_run_stopped_at_the_stage_raises_its_alert(mkl, stage):
    w = mkl(LiveLag(), max_drawdown_pct=8.0)
    w.open_book("10000")
    w.buy()
    if stage == "shortfall":
        w.venue.held["SPY"] -= 3.0
    else:
        w.prices["SPY"] = 700.0
    w.later()
    out = w.propose()
    assert out["status"] == "stopped" and out["stage"] == stage, out
    got = step_alerts(w)
    key = f"practice-stopped:propose:{DAY}"
    assert key in got and f"stage {stage}" in got[key].title, sorted(got)
    assert "webull-production" in got[key].detail and DAY in got[key].detail
    assert out["reason"][:60] in got[key].detail, (out["reason"], got[key].detail)


def test_a_repeated_stop_of_one_cause_on_one_day_is_one_alert_not_one_a_run(mk, tmp_path):
    w = mk()
    w.open_book()
    w.shared()
    w.venue.held["SPY"] -= 30.0
    for _ in range(3):
        w.later(5)
        assert w.shared()["stage"] == "shortfall"
    got = step_alerts(w)
    assert [k for k in got if k.startswith("step-stopped:")] == [f"step-stopped:{w.venue.name}"], sorted(got)
    path = tmp_path / "alerts.jsonl"
    for _ in range(2):                                    # and the watch writes the line once, however often it looks
        alerts.watch(env_of(w), path=path)
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert sum(1 for x in lines if x["key"] == f"step-stopped:{w.venue.name}") == 1, lines


def test_the_stop_alerts_are_unchanged_by_this_slice_the_unattended_one_clears_on_a_later_done_run(mk):
    w = mk()
    stop_line(w, "marks", WORDS["marks"])
    assert set(step_alerts(w)) == {f"step-stopped:{w.venue.name}"}
    stop_line(w, None, None, status="done")
    assert step_alerts(w) == {}


# ---- the one gap: a run that ends done with a shortfall in its last reconcile

def a_done_run_with_a_shortfall(w):
    """The book holds SPY and QQQ; the owner sells the book's QQQ while the run is going: the run is done and its last
    reconcile found the shortfall (test_shared_run's own scenario)."""
    w.open_book()
    w.paper = T.paper({"a": {"SPY": 100.0, "QQQ": 100.0}})
    w.shared()
    w.paper = T.paper({"a": {"SPY": 110.0, "QQQ": 100.0}})
    w.venue.lag_after_submit = {"QQQ": 0.0}
    w.later()
    out = w.shared()
    assert out["status"] == "done" and set(out["shared"]["shortfalls"]) == {"QQQ"}, out
    return out


def test_a_done_run_whose_last_reconcile_found_a_shortfall_raises_one_alert_with_the_plain_words(mk):
    w = mk(Lag())
    out = a_done_run_with_a_shortfall(w)
    short = out["shared"]["shortfalls"]["QQQ"]
    got = step_alerts(w)
    key = f"shared-shortfall:{w.venue.name}:{DAY}"
    assert key in got, sorted(got)
    a = got[key]
    assert a.level == "warning" and "less than the book counts as its own" in a.title
    for words in (w.venue.name, DAY, "QQQ", f"{short['book']:g}", f"{short['venue']:g}", f"{short['short']:g}",
                  "The owner sold shares the book counts as its own", "a split", "units_out"):
        assert words in a.detail, (words, a.detail)
    assert "os-" not in a.detail and "os-" not in a.title
    assert not any(k.startswith(("step-stopped:", "practice-stopped:")) for k in got), "the run was done: no stop alert"


def test_the_shortfall_alert_is_one_for_a_repeat_of_the_same_venue_and_day_and_clears_when_a_later_run_is_done_clean(mk, tmp_path):
    w = mk(Lag())
    a_done_run_with_a_shortfall(w)
    w.journal.write(DAY, {"type": "order_step", "event": "summary", "day": DAY, "venue": w.venue.name, "status": "done",
                          "stage": None, "reason": None, "shared": {"shortfalls": {"QQQ": {"book": 100.0, "venue": 60.0,
                                                                                            "unapplied": 0.0, "short": 40.0}}}})
    key = f"shared-shortfall:{w.venue.name}:{DAY}"
    assert [k for k in step_alerts(w) if k.startswith("shared-shortfall:")] == [key]
    path = tmp_path / "alerts.jsonl"
    alerts.watch(env_of(w), path=path)
    assert any(x["key"] == key and x["level"] != "cleared" for x in map(json.loads, path.read_text().splitlines()))
    w.venue.lag, w.venue.lag_after_submit = {}, {}        # the shares are back: the next run is done with none
    w.later()
    out = w.shared()
    assert out["status"] == "done" and "shortfalls" not in out["shared"], out
    assert [k for k in step_alerts(w) if k.startswith("shared-shortfall:")] == []
    alerts.watch(env_of(w), path=path)
    last = [x for x in map(json.loads, path.read_text().splitlines()) if x["key"] == key][-1]
    assert last["level"] == "cleared", last


def test_a_done_run_with_no_shortfall_or_no_shared_block_or_a_malformed_one_raises_nothing(mk):
    w = mk()
    stop_line(w, None, None, status="done")
    stop_line(w, None, None, status="done", shortfalls=None)
    w.journal.write(DAY, {"type": "order_step", "event": "summary", "day": DAY, "venue": w.venue.name, "status": "done",
                          "shared": {"shortfalls": {}}})
    w.journal.write(DAY, {"type": "order_step", "event": "summary", "day": DAY, "venue": w.venue.name, "status": "done",
                          "shared": {"shortfalls": "nonsense"}})
    w.journal.write(DAY, {"type": "order_step", "event": "summary", "day": DAY, "venue": w.venue.name, "status": "done",
                          "shared": "nonsense"})
    w.journal.write(DAY, {"type": "order_step", "event": "summary", "day": DAY, "venue": w.venue.name, "status": "done"})
    assert alerts.check_shared_shortfall(env_of(w)).alerts == []


def test_a_fund_name_that_is_not_plain_and_hostile_figures_never_reach_the_alert(mk):
    w = mk()
    tag = "os-20261113-143501-a1b2c3-XLE"
    w.journal.write(DAY, {"type": "order_step", "event": "summary", "day": DAY, "venue": w.venue.name, "status": "done",
                          "shared": {"shortfalls": {tag: {"book": 1.0, "venue": 0.0, "unapplied": 0.0, "short": 1.0},
                                                    "SPY": {"book": "x" * 50, "venue": 123456789012345.0, "unapplied": None,
                                                            "short": float("inf")}}}})
    (a,) = alerts.check_shared_shortfall(env_of(w)).alerts
    text = a.title + " " + a.detail
    assert "a1b2c3" not in text and tag not in text and "123456789012345" not in text and "x" * 20 not in text, text
    assert "SPY" in a.detail


def test_the_new_check_is_in_the_watch_list_with_its_prefix_and_is_documented():
    assert any("shared-shortfall:" in prefixes and fn is alerts.check_shared_shortfall for _, prefixes, fn in alerts.CHECKS)
    assert "shared-shortfall:<venue>:<day>" in alerts.__doc__


def test_a_failing_new_check_holds_only_its_own_keys(mk, monkeypatch):
    w = mk()
    stop_line(w, "marks", WORDS["marks"])

    def broken(env):
        raise OSError("the journal is busy")
    monkeypatch.setattr(alerts, "check_shared_shortfall", broken)
    chk = [(n, p, broken if p == ("shared-shortfall:",) else f) for n, p, f in alerts.CHECKS]
    monkeypatch.setattr(alerts, "CHECKS", chk)
    found, held, held_prefixes, failures = alerts.run_checks(env_of(w))
    assert "shared-shortfall:" in held_prefixes and any("busy" in f for f in failures)
    assert any(a.key.startswith("step-stopped:") for a in found)
