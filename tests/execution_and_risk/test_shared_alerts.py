"""T51c S4e (addendum 7, R24; ruling R2): the alerts of a shared run.

What is held here (repair round 2):
- a stop of a shared run (practice or live; unattended, propose or approve) is an alert that reflects the book's PRESENT state, not the
  day: `shared-stopped:<venue>:<class>:<stage>`, class `unattended` (critical) or `attended` (propose and approve, a warning). It is
  open while the latest shared summary of that venue and class is a stop at that stage (any day), and clears only when a later summary
  of the same venue and class is `done` with no shortfall (or stops at another stage); a run of the other class never clears it;
  ordinary skips (a closed market, `ticket`, `session` for propose and approve) neither open nor clear;
- a shared summary is judged whatever other keys it carries (`Mode`, `mode` "both" or empty); the stages that alert, old and new per
  mode, are shown equal where the old rules judged the mode;
- the gap a stop does not cover, a run that ENDS done whose last reconcile found a shortfall: `shared-shortfall:<venue>:<class>`
  (a warning), open while the latest summary of that class is done with a shortfall, with the count of short funds and every name
  first in its 300 characters (or "and K more"), the figures after;
- an old merged key is cleared only in a watch where the shared check ran; a run that is not shared keeps its two old keys.
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
    new = [getattr(alerts, n) for n in ("check_shared_shortfall", "check_shared_stops") if hasattr(alerts, n)]
    for check in (alerts.check_order_step, alerts.check_modes, alerts.check_approval_refused, *new):
        for a in check(env).alerts:
            out[a.key] = a
    return out


def stop_line(w, stage, reason, *, mode=None, venue=None, day=DAY, live=False, status="stopped", shared=True, **extra):
    """An order-step summary line as a shared run journals it, written by the journal's own writer."""
    line = {"type": "order_step", "event": "summary", "day": day, "venue": venue or w.venue.name, "status": status,
            "stage": stage, "reason": reason, "orders_sent": 0, "batch": "b-1"}
    if isinstance(shared, dict):
        line["shared"] = shared
    elif shared:
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


# ---- the stops of a shared run: the book's present state, per venue and class of run (repair round 2)

def sk(venue, stage, cls="unattended"):
    return f"shared-stopped:{venue}:{cls}:{stage}"


def cls_of(mode):
    return "unattended" if mode is None else "attended"


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("mode", [None, "propose", "approve"])
@pytest.mark.parametrize("stage", ["shortfall", "marks", "state"])
def test_every_shared_stop_at_these_stages_raises_one_alert_keyed_by_venue_class_and_stage(mk, stage, mode, live):
    w = mk()
    venue = "webull-production" if live else w.venue.name
    stop_line(w, stage, WORDS[stage], mode=mode, venue=venue, live=live)
    got = step_alerts(w)
    key = sk(venue, stage, cls_of(mode))
    assert set(got) == {key}, sorted(got)
    a = got[key]
    assert f"stage {stage}" in a.title
    for words in (DAY, venue, WORDS[stage][:40]):
        assert words in a.detail, (words, a.detail)
    assert a.level == ("critical" if mode is None else "warning")


@pytest.mark.parametrize("mode", [None, "propose", "approve"])
@pytest.mark.parametrize("stage", ["shortfall", "marks", "state"])
def test_a_stop_of_a_run_that_is_not_shared_keeps_the_old_keys(mk, stage, mode):
    w = mk()
    stop_line(w, stage, WORDS[stage], mode=mode, shared=False)
    key = f"step-stopped:{w.venue.name}" if mode is None else f"practice-stopped:{mode}:{DAY}"
    assert set(step_alerts(w)) == {key}


def test_a_practice_stop_then_a_live_stop_of_another_stage_the_same_day_are_two_alerts_the_checkers_first_repro(mk):
    w = mk()
    stop_line(w, "marks", WORDS["marks"], mode="propose", venue="webull-sandbox")
    stop_line(w, "shortfall", WORDS["shortfall"], mode="propose", venue="webull-production", live=True)
    got = step_alerts(w)
    assert set(got) == {sk("webull-sandbox", "marks", "attended"), sk("webull-production", "shortfall", "attended")}, sorted(got)
    assert "webull-production" in got[sk("webull-production", "shortfall", "attended")].detail


def test_a_practice_and_a_live_stop_of_the_same_stage_the_same_day_are_two_alerts(mk):
    w = mk()
    stop_line(w, "marks", WORDS["marks"], mode="propose", venue="webull-sandbox")
    stop_line(w, "marks", WORDS["marks"], mode="propose", venue="webull-production", live=True)
    assert set(step_alerts(w)) == {sk("webull-sandbox", "marks", "attended"), sk("webull-production", "marks", "attended")}


def test_a_stop_at_another_stage_replaces_the_one_before_in_its_class_the_checkers_second_repro(mk, tmp_path):
    w = mk()
    path = tmp_path / "alerts.jsonl"
    stop_line(w, "shortfall", WORDS["shortfall"])
    assert set(step_alerts(w)) == {sk(w.venue.name, "shortfall")}
    alerts.watch(env_of(w), path=path)
    stop_line(w, "marks", WORDS["marks"])
    got = step_alerts(w)
    assert set(got) == {sk(w.venue.name, "marks")} and "stage marks" in got[sk(w.venue.name, "marks")].title
    res = alerts.watch(env_of(w), path=path)
    assert sk(w.venue.name, "marks") in {a.key for a in res.raised} and sk(w.venue.name, "shortfall") in {a.key for a in res.cleared}


def test_an_attended_stop_first_and_an_unattended_one_after_are_two_keys_the_later_one_critical(mk, tmp_path):
    w = mk()
    path = tmp_path / "alerts.jsonl"
    stop_line(w, "orders", "the ledger is blocked", mode="propose")
    alerts.watch(env_of(w), path=path)
    stop_line(w, "orders", "the ledger is blocked")
    res = alerts.watch(env_of(w), path=path)
    got = {a.key: a for a in res.raised}
    assert got[sk(w.venue.name, "orders")].level == "critical", sorted(got)           # raised, and as critical
    assert {a["key"]: a["level"] for a in res.open}[sk(w.venue.name, "orders", "attended")] == "warning"


def test_a_repeat_of_one_cause_stays_one_alert_per_class(mk):
    w = mk()
    for mode in (None, "propose", "approve", None, "propose"):
        stop_line(w, "shortfall", WORDS["shortfall"], mode=mode)
    got = step_alerts(w)
    assert set(got) == {sk(w.venue.name, "shortfall"), sk(w.venue.name, "shortfall", "attended")}
    assert got[sk(w.venue.name, "shortfall")].level == "critical" and got[sk(w.venue.name, "shortfall", "attended")].level == "warning"


def test_a_stop_stays_open_over_a_weekend_with_no_runs_and_is_never_cleared_by_the_day_passing(mk, tmp_path):
    """The checker's third repro: a Friday stop that nothing fixed was cleared at New York midnight."""
    w = mk()
    path = tmp_path / "alerts.jsonl"
    stop_line(w, "marks", WORDS["marks"], day="2026-11-13")
    key = sk(w.venue.name, "marks")
    assert alerts.watch(env_of(w), path=path).raised[0].key == key
    for days in (1, 2, 3, 10):                                        # Saturday, Sunday, Monday, the week after: no run, no summary
        w.clock.t += 86400 if days <= 3 else 7 * 86400
        res = alerts.watch(env_of(w), path=path)
        assert res.cleared == [] and key in {a["key"] for a in res.open}, days
    assert key in {a["key"] for a in alerts.open_alerts(path)}


def test_a_stop_first_seen_by_a_watch_after_midnight_is_raised(mk, tmp_path):
    w = mk()
    stop_line(w, "state", WORDS["state"], day="2026-11-13")
    w.clock.t += 86400                                                # the first watch to see it is the next day's
    res = alerts.watch(env_of(w), path=tmp_path / "alerts.jsonl")
    assert sk(w.venue.name, "state") in {a.key for a in res.raised}


def test_an_attended_clean_run_never_clears_an_unattended_stop_nor_the_reverse_the_checkers_first_repro(mk):
    w = mk()
    stop_line(w, "orders", "the ledger is blocked")
    stop_line(w, "orders", "the ledger is blocked", mode="propose")
    stop_line(w, None, None, status="done", mode="propose")           # a clean propose run
    assert set(step_alerts(w)) == {sk(w.venue.name, "orders")}, "the unattended stop is still open"
    stop_line(w, "orders", "the ledger is blocked", mode="approve")
    stop_line(w, None, None, status="done")                           # a clean unattended run
    assert set(step_alerts(w)) == {sk(w.venue.name, "orders", "attended")}, "the attended stop is still open"
    stop_line(w, None, None, status="done", mode="approve")
    assert step_alerts(w) == {}


def test_only_a_clean_run_of_the_same_venue_and_class_clears_and_a_done_run_with_a_shortfall_changes_the_state(mk):
    w = mk()
    stop_line(w, "marks", WORDS["marks"], venue="webull-sandbox")
    stop_line(w, "marks", WORDS["marks"], venue="webull-production", live=True)
    stop_line(w, None, None, status="done", venue="webull-production")                     # the other venue's clean run
    assert set(step_alerts(w)) == {sk("webull-sandbox", "marks")}
    stop_line(w, None, None, status="done", venue="webull-sandbox",
              shared={"shortfalls": {"SPY": {"book": 1.0, "venue": 0.0, "short": 1.0}}})
    got = step_alerts(w)
    assert set(got) == {"shared-shortfall:webull-sandbox:unattended"}, sorted(got)         # the stop is over; the shortfall is open
    stop_line(w, None, None, status="done", venue="webull-sandbox")
    assert step_alerts(w) == {}


def test_ordinary_skips_neither_open_nor_clear(mk):
    w = mk()
    stop_line(w, "marks", WORDS["marks"])
    stop_line(w, "marks", WORDS["marks"], mode="propose")
    stop_line(w, "session", "2026-11-13 (Friday) is not a trading day")                    # an ordinary closed market
    stop_line(w, "session", "outside the regular session window 09:35 to 15:45 New York time (it is 08:00)", mode="propose")
    stop_line(w, "ticket", "no ticket", mode="approve")
    assert {k for k in step_alerts(w) if k.startswith("shared-")} == {sk(w.venue.name, "marks"), sk(w.venue.name, "marks", "attended")}
    w2 = mk()
    stop_line(w2, "session", "2026-11-13 (Friday) is not a trading day")
    stop_line(w2, "session", "x", mode="propose")
    stop_line(w2, "ticket", "no ticket", mode="approve")
    assert not any(k.startswith("shared-stopped:") for k in step_alerts(w2))
    stop_line(w2, "session", "the calendar did not answer")                                # not ordinary: an alert, as before
    assert {k for k in step_alerts(w2) if k.startswith("shared-")} == {sk(w2.venue.name, "session")}


MODES = {"none": {}, "propose": {"mode": "propose"}, "approve": {"mode": "approve"}, "both": {"mode": "both"},
         "Mode key": {"Mode": "propose"}, "empty mode": {"mode": ""}}
STAGES = ["session", "ticket", "targets", "state", "reconcile", "shortfall", "marks", "prices", "lock", "send", "after", "journal",
          "unexpected"]


@pytest.mark.parametrize("name", list(MODES))
def test_the_stages_that_alert_old_and_new_per_mode_and_nothing_shared_falls_between_the_checks(mk, name):
    """Old = the judgement of a run that is not shared (`step-stopped`, `practice-stopped`); new = the same summary of a shared run
    (`shared-stopped`). Where the old rules judged a mode (a `Mode` key reads as the unattended run), the two lists are equal. A
    summary the old rules left out (`mode` "both" or empty) is judged by the new check, as an attended run."""
    old, new = set(), set()
    for stage in STAGES:
        for shared, found in ((False, old), (True, new)):
            w = mk()
            stop_line(w, stage, "the calendar did not answer", shared=shared, **MODES[name])
            got = step_alerts(w)
            if any(k.startswith(("step-stopped:", "practice-stopped:", "shared-stopped:")) for k in got):
                found.add(stage)
    judged = {"none": set(STAGES), "Mode key": set(STAGES), "propose": set(STAGES) - {"ticket", "session"}, "approve": set(STAGES) - {"ticket", "session"}}
    if name in judged:
        assert old == new == judged[name], (name, sorted(old), sorted(new))
    else:
        assert old == set(), (name, sorted(old))                       # the old rules left these summaries out
        assert new == set(STAGES) - {"ticket", "session"}, (name, sorted(new))


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
    key = sk(w.venue.name, stage)
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
    key = sk("webull-production", stage, "attended")
    assert key in got and f"stage {stage}" in got[key].title, sorted(got)
    assert DAY in got[key].detail
    assert out["reason"][:60] in got[key].detail, (out["reason"], got[key].detail)


def test_a_repeated_real_stop_is_one_alert_and_the_watch_writes_its_line_once(mk, tmp_path):
    w = mk()
    w.open_book()
    w.shared()
    w.venue.held["SPY"] -= 30.0
    for _ in range(3):
        w.later(5)
        assert w.shared()["stage"] == "shortfall"
    key = sk(w.venue.name, "shortfall")
    assert [k for k in step_alerts(w) if k.startswith("shared-stopped:")] == [key]
    path = tmp_path / "alerts.jsonl"
    for _ in range(2):
        alerts.watch(env_of(w), path=path)
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert sum(1 for x in lines if x["key"] == key) == 1, lines


def test_old_keys_open_for_shared_stops_are_cleared_by_the_next_watch_and_the_new_key_is_raised(mk, tmp_path):
    """The old merged keys (and round 1's day keys) are no longer produced for a shared stop, so the watch writes a `cleared` line for
    each (neither left open for ever nor dropped without a line) and raises the per-venue, class and stage alert in the same watch."""
    w = mk()
    path = tmp_path / "alerts.jsonl"
    old = [("step-stopped:" + w.venue.name, "critical"), (f"practice-stopped:propose:{DAY}", "warning"),
           (f"shared-stopped:{w.venue.name}:marks:{DAY}", "warning"), ("step-stopped:webull-elsewhere", "critical")]
    path.write_text("".join(json.dumps({"time": "2026-11-13T14:00:00Z", "level": lv, "key": k, "title": "old", "detail": "old"}) + "\n"
                            for k, lv in old))
    stop_line(w, "marks", WORDS["marks"], mode="propose")
    stop_line(w, "shortfall", WORDS["shortfall"])
    stop_line(w, "marks", WORDS["marks"], venue="webull-elsewhere", shared=False)       # a run that is not shared keeps its key
    res = alerts.watch(env_of(w), path=path)
    assert {a.key for a in res.cleared} == {k for k, _ in old[:3]}
    assert {a.key for a in res.raised if a.key.startswith("shared-stopped:")} == {sk(w.venue.name, "marks", "attended"),
                                                                                 sk(w.venue.name, "shortfall")}
    assert {a["key"] for a in res.open} >= {"step-stopped:webull-elsewhere", sk(w.venue.name, "marks", "attended")}
    again = alerts.watch(env_of(w), path=path)
    assert again.raised == [] and again.cleared == []


def test_an_old_key_is_cleared_only_in_a_watch_where_the_shared_check_ran(mk, tmp_path, monkeypatch):
    """The checker's fifth finding: when check_shared_stops fails in the first watch after the upgrade, the old key stays open (no
    `cleared` line); the next watch, where it runs, clears it and raises the replacement."""
    w = mk()
    path = tmp_path / "alerts.jsonl"
    old = f"step-stopped:{w.venue.name}"
    path.write_text(json.dumps({"time": "2026-11-13T14:00:00Z", "level": "critical", "key": old, "title": "old", "detail": "old"}) + "\n")
    stop_line(w, "marks", WORDS["marks"])
    real = alerts.check_shared_stops

    def broken(env):
        raise OSError("the journal is busy")
    monkeypatch.setattr(alerts, "CHECKS", [(n, p, broken if fn is real else fn) for n, p, fn in alerts.CHECKS])
    res = alerts.watch(env_of(w), path=path)
    assert res.cleared == [] and old in {a["key"] for a in res.open} and any("busy" in f for f in res.failures)
    assert not any(a.key.startswith("shared-stopped:") for a in res.raised)
    monkeypatch.undo()
    res = alerts.watch(env_of(w), path=path)
    assert old in {a.key for a in res.cleared} and sk(w.venue.name, "marks") in {a.key for a in res.raised}


def test_the_stop_alerts_of_a_run_that_is_not_shared_are_unchanged_the_unattended_one_clears_on_a_later_done_run(mk):
    w = mk()
    stop_line(w, "marks", WORDS["marks"], shared=False)
    assert set(step_alerts(w)) == {f"step-stopped:{w.venue.name}"}
    stop_line(w, None, None, status="done", shared=False)
    assert step_alerts(w) == {}


# ---- a key is cleared only on positive evidence (repair round 3): an aged-out or missing summary clears nothing

def open_keys(path):
    return {a["key"]: a["level"] for a in alerts.open_alerts(path)}


def test_a_stop_older_than_the_journal_window_stays_open_critical_with_no_cleared_line(mk, tmp_path):
    w = mk()
    path = tmp_path / "alerts.jsonl"
    stop_line(w, "marks", WORDS["marks"], day=DAY)
    key = sk(w.venue.name, "marks")
    assert key in {a.key for a in alerts.watch(env_of(w), path=path).raised}
    for days in (31, 60):                                          # the stop's day is outside the 30 days the journal read covers
        w.clock.t = T.ny("2026-11-13 10:00") + days * 86400
        res = alerts.watch(env_of(w), path=path)
        assert res.cleared == [] and open_keys(path).get(key) == "critical", (days, [x.key for x in res.cleared])
    assert not [x for x in map(json.loads, path.read_text().splitlines()) if x["level"] == "cleared"]


def test_a_stop_whose_day_file_is_deleted_stays_open(mk, tmp_path):
    w = mk()
    path = tmp_path / "alerts.jsonl"
    stop_line(w, "marks", WORDS["marks"])
    key = sk(w.venue.name, "marks")
    alerts.watch(env_of(w), path=path)
    for f in (w.tmp / "journal").glob("*.jsonl"):
        f.unlink()
    res = alerts.watch(env_of(w), path=path)
    assert res.cleared == [] and key in open_keys(path)


def test_a_clean_run_of_the_same_venue_and_class_after_the_stop_aged_out_clears_it_and_no_other_run_does(mk, tmp_path):
    w = mk()
    path = tmp_path / "alerts.jsonl"
    stop_line(w, "marks", WORDS["marks"], day=DAY)
    stop_line(w, "marks", WORDS["marks"], day=DAY, mode="propose")
    unattended, attended = sk(w.venue.name, "marks"), sk(w.venue.name, "marks", "attended")
    alerts.watch(env_of(w), path=path)
    w.clock.t = T.ny("2026-12-23 10:00")                            # day 40
    stop_line(w, None, None, status="done", day="2026-12-23", venue="webull-elsewhere")          # another venue
    stop_line(w, None, None, status="done", day="2026-12-23", mode="propose")                     # the other class
    res = alerts.watch(env_of(w), path=path)
    assert {a.key for a in res.cleared} == {attended} and unattended in open_keys(path), open_keys(path)
    stop_line(w, None, None, status="done", day="2026-12-23")                                     # the same venue and class
    res = alerts.watch(env_of(w), path=path)
    assert {a.key for a in res.cleared} == {unattended} and open_keys(path).get(unattended) is None


def test_a_stop_at_another_stage_after_the_old_one_aged_out_replaces_it(mk, tmp_path):
    w = mk()
    path = tmp_path / "alerts.jsonl"
    stop_line(w, "marks", WORDS["marks"], day=DAY)
    alerts.watch(env_of(w), path=path)
    w.clock.t = T.ny("2026-12-23 10:00")
    stop_line(w, "state", WORDS["state"], day="2026-12-23")
    res = alerts.watch(env_of(w), path=path)
    assert {a.key for a in res.cleared} == {sk(w.venue.name, "marks")} and {a.key for a in res.raised} == {sk(w.venue.name, "state")}


def test_a_shortfall_alert_follows_the_same_rule_aged_out_stays_clean_run_clears(mk, tmp_path):
    w = mk()
    path = tmp_path / "alerts.jsonl"
    bad = {"shortfalls": {"SPY": {"book": 1.0, "venue": 0.0, "short": 1.0}}}
    stop_line(w, None, None, status="done", shared=bad, day=DAY)
    key = "shared-shortfall:" + w.venue.name + ":unattended"
    alerts.watch(env_of(w), path=path)
    for days in (31, 60):
        w.clock.t = T.ny("2026-11-13 10:00") + days * 86400
        res = alerts.watch(env_of(w), path=path)
        assert res.cleared == [] and key in open_keys(path), days
    w.clock.t = T.ny("2026-12-23 10:00")
    stop_line(w, None, None, status="done", day="2026-12-23")
    assert key in {a.key for a in alerts.watch(env_of(w), path=path).cleared}


def test_the_older_forms_of_the_keys_are_still_cleared_not_held(mk, tmp_path):
    """A key of an earlier form (the day form of the stop, the day form of the shortfall) has no venue and class to hold it by."""
    w = mk()
    path = tmp_path / "alerts.jsonl"
    old = [f"shared-stopped:{w.venue.name}:marks:{DAY}", f"shared-shortfall:{w.venue.name}:{DAY}"]
    path.write_text("".join(json.dumps({"time": "2026-11-13T14:00:00Z", "level": "warning", "key": k, "title": "old", "detail": "old"}) + "\n"
                            for k in old))
    res = alerts.watch(env_of(w), path=path)
    assert {a.key for a in res.cleared} == set(old)


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
    key = f"shared-shortfall:{w.venue.name}:unattended"
    assert key in got, sorted(got)
    a = got[key]
    assert a.level == "warning" and "less than the book counts as its own" in a.title
    for words in (w.venue.name, DAY, "QQQ", f"{short['book']:g}", f"{short['venue']:g}", f"{short['short']:g}",
                  "The owner sold shares the book counts as its own", "a split", "units_out"):
        assert words in a.detail, (words, a.detail)
    assert "os-" not in a.detail and "os-" not in a.title
    assert not any(k.startswith(("step-stopped:", "practice-stopped:")) for k in got), "the run was done: no stop alert"


def test_the_shortfall_alert_is_one_for_a_repeat_and_clears_when_a_later_run_of_its_class_is_done_clean(mk, tmp_path):
    w = mk(Lag())
    a_done_run_with_a_shortfall(w)
    w.journal.write(DAY, {"type": "order_step", "event": "summary", "day": DAY, "venue": w.venue.name, "status": "done",
                          "stage": None, "reason": None, "shared": {"shortfalls": {"QQQ": {"book": 100.0, "venue": 60.0,
                                                                                            "unapplied": 0.0, "short": 40.0}}}})
    key = f"shared-shortfall:{w.venue.name}:unattended"
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


def test_the_count_and_every_fund_name_come_first_in_the_alert_detail_the_figures_after(mk):
    w = mk(venue=None)
    shorts = {sym: {"book": 100.0, "venue": 60.0, "unapplied": 0.0, "short": 40.0} for sym in ("QQQ", "SPY", "IWM", "TLT", "GLD")}
    w.journal.write(DAY, {"type": "order_step", "event": "summary", "day": DAY, "venue": "webull-production", "status": "done",
                          "shared": {"shortfalls": shorts}})
    (a,) = alerts.check_shared_shortfall(env_of(w)).alerts
    assert len(a.detail) <= 300
    assert "5 funds short: GLD, IWM, QQQ, SPY, TLT" in a.detail, a.detail
    assert a.detail.index("TLT") < a.detail.index("counted"), a.detail
    for words in ("webull-production", DAY, "units_out"):
        assert words in a.detail, (words, a.detail)


def test_a_long_list_of_short_funds_says_how_many_more_inside_the_300_characters(mk):
    w = mk(venue=None)
    names = [f"FUND{i:02d}" for i in range(40)]
    shorts = {sym: {"book": 100.0, "venue": 60.0, "unapplied": 0.0, "short": 40.0} for sym in names}
    w.journal.write(DAY, {"type": "order_step", "event": "summary", "day": DAY, "venue": "webull-production", "status": "done",
                          "shared": {"shortfalls": shorts}})
    (a,) = alerts.check_shared_shortfall(env_of(w)).alerts
    assert len(a.detail) <= 300 and "…" not in a.detail, a.detail
    assert "40 funds short: FUND00, FUND01" in a.detail, a.detail
    shown = [n for n in names if n in a.detail.split(" counted")[0]]
    assert f", and {40 - len(shown)} more" in a.detail and 0 < len(shown) < 40, (shown, a.detail)
    for words in ("units_out", "webull-production", DAY):
        assert words in a.detail, (words, a.detail)


def test_a_shortfall_alert_follows_the_latest_summary_of_its_class_over_days_and_runs_of_the_other_class_leave_it(mk, tmp_path):
    w = mk()
    path = tmp_path / "alerts.jsonl"
    bad = {"shortfalls": {"SPY": {"book": 1.0, "venue": 0.0, "short": 1.0}}}
    stop_line(w, None, None, status="done", shared=bad, day="2026-11-13")
    key = "shared-shortfall:" + w.venue.name + ":unattended"
    alerts.watch(env_of(w), path=path)
    w.clock.t += 3 * 86400                                              # no runs for the weekend
    res = alerts.watch(env_of(w), path=path)
    assert res.cleared == [] and key in {a["key"] for a in res.open}
    stop_line(w, None, None, status="done", mode="propose", day="2026-11-13")             # a clean attended run: another class
    assert key in step_alerts(w)
    stop_line(w, None, None, status="done", day="2026-11-13")                              # a clean unattended run
    assert key not in step_alerts(w)


def test_the_new_check_is_in_the_watch_list_with_its_prefix_and_is_documented():
    assert any("shared-shortfall:" in prefixes and fn is alerts.check_shared_shortfall for _, prefixes, fn in alerts.CHECKS)
    assert "shared-shortfall:<venue>:<class>" in alerts.__doc__
    assert any("shared-stopped:" in prefixes and fn is alerts.check_shared_stops for _, prefixes, fn in alerts.CHECKS)
    assert "shared-stopped:<venue>:<class>:<stage>" in alerts.__doc__


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
    assert any(a.key.startswith("shared-stopped:") for a in found)
