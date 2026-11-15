"""The stake ladder and the working stop (T34a; docs/specs/2026-11-14-caps-ladder-design.md, D1 to D6 and D11).

Every rule is tested as a rule: the owner's file refusal by refusal, the session facts and the position on hand-made
edges, and the properties over hundreds of generated journals with a fixed seed."""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import random
import statistics
import sys
from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import valedia_trader
from valedia_trader.execution_and_risk import go_live as gl
from valedia_trader.execution_and_risk import stake_ladder as sl
from valedia_trader.venues import webull as wb

NY = ZoneInfo("America/New_York")
TODAY = date(2026, 11, 16)                       # a Monday, a session
LIVE, PAPER = gl.LIVE_VENUE, gl.PAPER_VENUE
EM_DASH = "—"


def test_the_package_under_test_is_this_tree():
    tree = Path(__file__).resolve().parents[2]
    assert Path(valedia_trader.__file__).resolve().is_relative_to(tree)
    assert Path(sl.__file__).resolve().is_relative_to(tree)


def test_the_named_constants():
    assert (sl.TICKET_SESSIONS, sl.UNATTENDED_SESSIONS, sl.DOUBLING_SESSIONS) == (20, 40, 60)
    assert (sl.STOP_MULTIPLE, sl.STOP_WINDOW, sl.STOP_FLOOR_PCT) == (2.5, 25, 1.0)
    assert sl.FAULT_STAGES == frozenset({"state", "reconcile", "send", "after", "journal", "baseline", "unexpected"})
    assert sl.DRIFT_ACTS is False
    assert sl.LIVE_VENUE is gl.LIVE_VENUE and sl.PAPER_VENUE is gl.PAPER_VENUE


# ------------------------------------------------------------------ 1. the owner's ladder file (D1)
GOOD = {"confirmed_by": "Owner Name", "first_stake": 500, "unattended": False, "fractional": True,
        "valid_until": (TODAY + timedelta(days=10)).isoformat()}
CAP = 2000.0


def owner_root(tmp_path: Path, data=None, raw: bytes | None = None, mode: int = 0o644) -> Path:
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True, exist_ok=True)
    root.chmod(0o755)
    (root / "config").chmod(0o755)
    path = root / "config" / "ladder.json"
    path.write_bytes(raw if raw is not None else json.dumps(GOOD if data is None else data).encode())
    path.chmod(mode)
    return root


def refused(root, today=TODAY, cap=CAP) -> str:
    with pytest.raises(sl.LadderRefused) as info:
        sl.read_ladder_file(today, cap, root=root)
    assert not isinstance(info.value, sl.LadderAbsent)
    text = str(info.value)
    assert "config/ladder.json" in text and "\n" not in text and EM_DASH not in text
    assert info.value.reason == text
    return text


def test_a_good_ladder_file_is_read(tmp_path):
    root = owner_root(tmp_path)
    got = sl.read_ladder_file(TODAY, CAP, root=root)
    assert (got.confirmed_by, got.first_stake, got.unattended, got.fractional, got.valid_until) == \
           ("Owner Name", 500.0, False, True, GOOD["valid_until"])
    assert type(got.first_stake) is float
    assert got.digest == hashlib.sha256(json.dumps(GOOD, sort_keys=True, ensure_ascii=True,
                                                   allow_nan=False).encode()).hexdigest()
    with pytest.raises(FrozenInstanceError):
        got.first_stake = 1.0                                         # type: ignore[misc]


def test_the_digest_follows_the_content(tmp_path):
    a = sl.read_ladder_file(TODAY, CAP, root=owner_root(tmp_path / "a"))
    b = sl.read_ladder_file(TODAY, CAP, root=owner_root(tmp_path / "b", {**GOOD, "first_stake": 501}))
    assert a.digest != b.digest


def test_today_and_31_days_ahead_are_accepted_and_a_stake_at_the_ceiling(tmp_path):
    for n in (0, 31):
        until = (TODAY + timedelta(days=n)).isoformat()
        got = sl.read_ladder_file(TODAY, 500, root=owner_root(tmp_path / str(n), {**GOOD, "valid_until": until}))
        assert got.valid_until == until and got.first_stake == 500.0


def test_the_default_root_is_the_live_repository(tmp_path, monkeypatch):
    root = owner_root(tmp_path)
    monkeypatch.setattr(wb, "LIVE_REPO", root)
    assert sl.read_ladder_file(TODAY, CAP).first_stake == 500.0


def test_an_absent_file_is_told_apart_from_a_wrong_one(tmp_path):
    root = owner_root(tmp_path)
    (root / "config" / "ladder.json").unlink()
    with pytest.raises(sl.LadderAbsent) as info:
        sl.read_ladder_file(TODAY, CAP, root=root)
    assert isinstance(info.value, sl.LadderRefused)                 # a caller that catches only refusals stops
    assert "config/ladder.json" in str(info.value) and "\n" not in str(info.value)
    with pytest.raises(sl.LadderAbsent):
        sl.read_ladder_file(TODAY, CAP, root=tmp_path / "nothing-here")
    refused(owner_root(tmp_path / "wrong", {**GOOD, "first_stake": "500"}))


WRONG_TYPES = [
    ("confirmed_by", 5), ("confirmed_by", ""), ("confirmed_by", "   "), ("confirmed_by", True), ("confirmed_by", None),
    ("confirmed_by", "x" * 201), ("confirmed_by", "two\nlines"), ("confirmed_by", ["Owner"]),
    ("first_stake", "500"), ("first_stake", True), ("first_stake", False), ("first_stake", 0), ("first_stake", -1),
    ("first_stake", None), ("first_stake", [500]), ("first_stake", {"v": 500}), ("first_stake", 2e9),
    ("unattended", 0), ("unattended", 1), ("unattended", "true"), ("unattended", None), ("unattended", "false"),
    ("unattended", [True]),
    ("fractional", 0), ("fractional", 1), ("fractional", "true"), ("fractional", None), ("fractional", 1.0),
    ("valid_until", 20261120), ("valid_until", "2026-11-20T00:00"), ("valid_until", "2026-13-01"),
    ("valid_until", "20261120"), ("valid_until", None), ("valid_until", True), ("valid_until", "2026-11-2"),
    ("valid_until", ["2026-11-20"]),
]


@pytest.mark.parametrize("key,value", WRONG_TYPES)
def test_each_wrong_type_of_each_key_is_refused(tmp_path, key, value):
    text = refused(owner_root(tmp_path, {**GOOD, key: value}))
    assert key in text


def test_an_extra_key_is_refused(tmp_path):
    assert "max_capital" in refused(owner_root(tmp_path, {**GOOD, "max_capital": 100}))


@pytest.mark.parametrize("key", sorted(GOOD))
def test_a_missing_key_is_refused(tmp_path, key):
    assert key in refused(owner_root(tmp_path, {k: v for k, v in GOOD.items() if k != key}))


def test_a_lapsed_date_is_refused(tmp_path):
    text = refused(owner_root(tmp_path, {**GOOD, "valid_until": (TODAY - timedelta(days=1)).isoformat()}))
    assert "lapsed" in text


def test_a_date_32_days_ahead_is_refused(tmp_path):
    text = refused(owner_root(tmp_path, {**GOOD, "valid_until": (TODAY + timedelta(days=32)).isoformat()}))
    assert "31 days" in text


def test_a_stake_above_the_ceiling_is_refused(tmp_path):
    text = refused(owner_root(tmp_path, {**GOOD, "first_stake": 500.01}), cap=500)
    assert "first_stake" in text and "max_capital" in text


def test_nan_and_infinity_are_refused(tmp_path):
    for i, token in enumerate((b"NaN", b"Infinity", b"-Infinity")):
        raw = json.dumps({**GOOD, "first_stake": 1}).encode().replace(b'"first_stake": 1', b'"first_stake": ' + token)
        refused(owner_root(tmp_path / str(i), raw=raw))


def test_a_boolean_stake_is_refused(tmp_path):
    assert "first_stake" in refused(owner_root(tmp_path, {**GOOD, "first_stake": True}))


def test_a_symbolic_link_is_refused(tmp_path):
    root = owner_root(tmp_path)
    real = root / "config" / "real.json"
    (root / "config" / "ladder.json").rename(real)
    (root / "config" / "ladder.json").symlink_to(real)
    assert "link" in refused(root)


def test_a_dangling_link_is_a_refusal_not_an_absence(tmp_path):
    root = owner_root(tmp_path)
    (root / "config" / "ladder.json").unlink()
    (root / "config" / "ladder.json").symlink_to(root / "config" / "gone.json")
    assert "link" in refused(root)


def test_a_hard_link_is_refused(tmp_path):
    root = owner_root(tmp_path)
    os.link(root / "config" / "ladder.json", root / "config" / "second-name.json")
    assert "hard link" in refused(root)


@pytest.mark.parametrize("mode", [0o664, 0o646, 0o666])
def test_a_group_or_world_writable_file_is_refused_where_the_volume_enforces_it(tmp_path, mode):
    root = owner_root(tmp_path, mode=mode)
    if not os.stat(root / "config" / "ladder.json").st_mode & 0o022:
        pytest.skip("this volume does not keep the write bits")
    assert "writable" in refused(root)


def test_a_writable_folder_above_is_refused(tmp_path):
    root = owner_root(tmp_path)
    (root / "config").chmod(0o775)
    if not os.stat(root / "config").st_mode & 0o022:
        pytest.skip("this volume does not keep the write bits")
    try:
        assert "folder" in refused(root)
    finally:
        (root / "config").chmod(0o755)


@pytest.mark.parametrize("raw", [b"", b"not json", b"[1, 2]", b'"text"', b'{"confirmed_by": "a", "confirmed_by": "b"}',
                                 b"\xff\xfe"])
def test_a_file_that_is_not_one_json_object_is_refused(tmp_path, raw):
    refused(owner_root(tmp_path, raw=raw))


def test_the_judging_inputs_must_be_real(tmp_path):
    from datetime import datetime
    root = owner_root(tmp_path)
    for today, cap in ((datetime(2026, 11, 16), CAP), ("2026-11-16", CAP), (TODAY, True), (TODAY, 0), (TODAY, math.nan),
                       (TODAY, "2000"), (TODAY, None)):
        with pytest.raises(sl.LadderRefused) as info:
            sl.read_ladder_file(today, cap, root=root)
        assert not isinstance(info.value, sl.LadderAbsent)


def test_reading_writes_nothing(tmp_path):
    good, bad = owner_root(tmp_path / "good"), owner_root(tmp_path / "bad", {**GOOD, "unattended": 1})

    def snapshot():
        return sorted((str(p), p.stat().st_mtime_ns, p.stat().st_mode) for p in tmp_path.rglob("*"))
    before = snapshot()
    sl.read_ladder_file(TODAY, CAP, root=good)
    refused(bad)
    assert snapshot() == before


# ------------------------------------------------------------------ 2. session facts (D3), 6. day stopped, 7. fractions
class Weekdays:
    """A stand-in exchange calendar (the same method paper_record calls): weekdays are sessions, but for `closed`."""

    def __init__(self, closed=()):
        self.closed = set(closed)

    def is_session(self, text):
        d = date.fromisoformat(text)
        return d.weekday() < 5 and text not in self.closed


CAL = Weekdays()


def sessions_from(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


OMIT = object()                                  # a summary with no `ceiling` key at all


def summ(day, status="done", stage=None, venue=LIVE, batch="b1", ceiling=1e6, **extra):
    """An order_step summary; `ceiling` (A4) is the owner's max_capital the run read (OMIT: no such key)."""
    day = day.isoformat() if isinstance(day, date) else day
    e = {"type": "order_step", "event": "summary", "venue": venue, "day": day, "status": status, "stage": stage,
         "batch": batch, **extra}
    if ceiling is not OMIT:
        e["ceiling"] = ceiling
    return e


def stop(day, stage, **extra):
    return summ(day, status="stopped", stage=stage, **extra)


def pause(day, ts="15:00:00", venue=LIVE):
    day = day.isoformat() if isinstance(day, date) else day
    return {"type": "drawdown_pause", "venue": venue, "day": day, "timestamp": f"{day}T{ts}+00:00"}


def reset(day, who="Owner Name", ts="20:00:00", venue=LIVE):
    day = day.isoformat() if isinstance(day, date) else day
    return {"type": "latch_reset", "venue": venue, "day": day, "who": who, "timestamp": f"{day}T{ts}+00:00"}


def kinds(facts):
    return [(f.day, f.kind) for f in facts]


def test_one_fact_per_session_day_in_day_order():
    d1, d2, d3 = sessions_from(TODAY, 3)
    entries = [summ(d2), stop(d1, "prices"), summ(d1, batch="b2"), stop(d3, "send"), stop(d3, "prices")]
    facts = sl.session_facts(entries, LIVE, CAL)
    assert isinstance(facts, list) and facts.unreadable == 0
    assert kinds(facts) == [(d1, "clean"), (d2, "clean"), (d3, "fault")]
    assert all(isinstance(f.reasons, tuple) and f.reasons and all(type(r) is str for r in f.reasons) for f in facts)
    with pytest.raises(FrozenInstanceError):
        facts[0].kind = "fault"                                         # type: ignore[misc]


@pytest.mark.parametrize("stage", sorted(sl.FAULT_STAGES))
def test_a_stop_at_a_fault_stage_is_a_fault_even_beside_a_done_run(stage):
    facts = sl.session_facts([summ(TODAY), stop(TODAY, stage)], LIVE, CAL)
    assert kinds(facts) == [(TODAY, "fault")]
    assert any(stage in r for r in facts[0].reasons)


@pytest.mark.parametrize("stage", ["session", "ticket", "lock", "live", "clock", "environment", "targets", "follow",
                                   "prices", "marks"])
def test_an_ordinary_or_data_stop_is_skipped_alone_and_clean_beside_a_done_run(stage):
    assert kinds(sl.session_facts([stop(TODAY, stage)], LIVE, CAL)) == [(TODAY, "skipped")]
    assert kinds(sl.session_facts([stop(TODAY, stage), summ(TODAY)], LIVE, CAL)) == [(TODAY, "clean")]


@pytest.mark.parametrize("faults", [["reconcile"], ["attention:stale"], ["cap"], ["drift", "cap"], [{"kind": "cap"}],
                                    "reconcile", {"cap": 1}, [""], 0, False, "", {"drift": 1}, ("drift",)])
def test_a_done_run_carrying_a_fault_is_a_fault(faults):
    assert kinds(sl.session_facts([summ(TODAY, faults=faults)], LIVE, CAL)) == [(TODAY, "fault")]


def test_an_empty_faults_list_or_no_faults_key_is_clean_and_a_null_one_is_a_fault():          # F4
    assert kinds(sl.session_facts([summ(TODAY, faults=[])], LIVE, CAL)) == [(TODAY, "clean")]
    assert kinds(sl.session_facts([summ(TODAY)], LIVE, CAL)) == [(TODAY, "clean")]
    assert kinds(sl.session_facts([summ(TODAY, faults=None)], LIVE, CAL)) == [(TODAY, "fault")]


def test_drift_does_not_act_while_the_constant_is_false(monkeypatch):
    entries = [summ(TODAY, faults=["drift"])]
    assert kinds(sl.session_facts(entries, LIVE, CAL)) == [(TODAY, "clean")]
    monkeypatch.setattr(sl, "DRIFT_ACTS", True)
    assert kinds(sl.session_facts(entries, LIVE, CAL)) == [(TODAY, "fault")]


def test_an_unknown_stage_or_status_is_a_fault():                       # the careful reading
    assert kinds(sl.session_facts([stop(TODAY, "something new")], LIVE, CAL)) == [(TODAY, "fault")]
    assert kinds(sl.session_facts([stop(TODAY, None)], LIVE, CAL)) == [(TODAY, "fault")]
    assert kinds(sl.session_facts([summ(TODAY, status="half done")], LIVE, CAL)) == [(TODAY, "fault")]


def test_only_this_venues_summaries_make_a_session():
    entries = [summ(TODAY, venue=PAPER), {**summ(TODAY), "event": "fill"}, {**summ(TODAY), "type": "summary"},
               stop(TODAY + timedelta(days=1), "send", venue=PAPER), pause(TODAY, venue=PAPER), reset(TODAY, venue=PAPER)]
    facts = sl.session_facts(entries, LIVE, CAL)
    assert list(facts) == [] and facts.unreadable == 0


def test_unusable_entries_are_skipped_and_counted_never_raised():
    sat = date(2026, 11, 21)
    entries = [None, 5, "text", [summ(TODAY)], summ("2026-11-31"), summ(20261116), summ(None), summ("16/11/2026"),
               summ(sat), stop(sat, "send"), pause("2026-11-31"), reset(TODAY, who=""), reset(TODAY, who=None),
               {"type": "order_step", "event": "summary", "venue": LIVE}, summ(TODAY), reset(None),
               {"type": "drawdown_pause", "venue": LIVE}]
    facts = sl.session_facts(entries, LIVE, CAL)
    assert kinds(facts) == [(TODAY, "clean")]
    assert facts.unreadable == 16


# A2 (addendum 1): a pause or a reset is an event, not a session: it counts on any calendar day
FRI, SAT, MON = date(2026, 11, 20), date(2026, 11, 21), date(2026, 11, 23)


def unattended_at_1000():
    """60 clean sessions ending on the Thursday before FRI: unattended at 1000 on a first stake of 500."""
    days = sessions_from(FRI - timedelta(days=200), 400)
    days = [d for d in days if d < FRI][-60:]
    return [summ(d) for d in days]


def test_a_saturday_reset_after_a_friday_pause_takes_effect_before_monday():
    entries = unattended_at_1000() + [summ(FRI), pause(FRI), reset(SAT), summ(MON)]
    facts = sl.session_facts(entries, LIVE, CAL)
    assert facts.unreadable == 0
    assert kinds(facts)[-4:] == [(FRI, "clean"), (FRI, "pause"), (SAT, "reset"), (MON, "clean")]
    assert shape(sl.position(facts, 500, 1e6)) == ("unattended", 500.0, 1, 40, False)   # stepped down; Monday counts


def test_a_pause_journaled_on_a_holiday_counts():
    thanksgiving = date(2026, 11, 26)
    cal = Weekdays(closed={thanksgiving.isoformat()})
    after = date(2026, 11, 27)
    entries = unattended_at_1000() + [pause(thanksgiving), summ(after)]
    facts = sl.session_facts(entries, LIVE, cal)
    assert facts.unreadable == 0 and kinds(facts)[-2:] == [(thanksgiving, "pause"), (after, "clean")]
    assert shape(sl.position(facts, 500, 1e6)) == ("unattended", 1000.0, 0, 60, True)


def test_a_pause_or_reset_outside_the_calendars_window_still_counts():
    class Window(Weekdays):
        def is_session(self, text):
            if text > "2026-12-31":
                raise ValueError("outside the calendar")
            return super().is_session(text)
    far = date(2027, 6, 5)
    facts = sl.session_facts([pause(far), reset(far + timedelta(days=1)), summ(far)], LIVE, Window())
    assert kinds(facts) == [(far, "pause"), (far + timedelta(days=1), "reset")] and facts.unreadable == 1


def test_a_reset_on_a_session_day_behaves_as_before():
    entries = unattended_at_1000() + [pause(FRI), summ(MON), reset(MON), summ(MON + timedelta(days=1))]
    facts = sl.session_facts(entries, LIVE, CAL)
    p = sl.position(facts, 500, 1e6)
    assert shape(p) == ("unattended", 500.0, 1, 40, False)                # Monday's session did not count


def test_a_nameless_saturday_reset_is_unreadable_and_the_position_stays_paused():
    base = unattended_at_1000() + [summ(FRI), pause(FRI)]
    without = sl.session_facts(base + [summ(MON)], LIVE, CAL)
    facts = sl.session_facts(base + [reset(SAT, who=""), summ(MON)], LIVE, CAL)
    assert facts.unreadable == without.unreadable + 1
    assert list(facts) == list(without)
    assert sl.position(facts, 500, 1e6).paused is True


def test_a_calendar_that_cannot_answer_counts_the_entry():
    class Broken:
        def is_session(self, text):
            raise ValueError("outside the calendar")
    facts = sl.session_facts([summ(TODAY)], LIVE, Broken())
    assert list(facts) == [] and facts.unreadable == 1


def test_the_exchange_calendar_is_the_default_and_a_holiday_is_no_session():
    thanksgiving = date(2026, 11, 26)
    facts = sl.session_facts([summ(thanksgiving), summ(TODAY)], LIVE, None)
    assert kinds(facts) == [(TODAY, "clean")] and facts.unreadable == 1


def test_pauses_and_resets_are_their_own_events_in_day_order():
    d1, d2, d3 = sessions_from(TODAY, 3)
    entries = [reset(d3), summ(d3), summ(d1), pause(d1), summ(d2), pause(d2, ts="14:00:00")]
    facts = sl.session_facts(entries, LIVE, CAL)
    assert kinds(facts) == [(d1, "clean"), (d1, "pause"), (d2, "clean"), (d2, "pause"), (d3, "clean"), (d3, "reset")]
    assert any("Owner Name" in r for r in facts[-1].reasons)


def test_within_a_day_pauses_and_resets_go_by_their_time_and_an_unknown_time_ends_paused():
    entries = [pause(TODAY, ts="21:00:00"), reset(TODAY, ts="20:00:00")]
    assert [f.kind for f in sl.session_facts(entries, LIVE, CAL)] == ["reset", "pause"]
    entries = [reset(TODAY, ts="20:00:00"), pause(TODAY, ts="09:00:00")]
    assert [f.kind for f in sl.session_facts(entries, LIVE, CAL)] == ["pause", "reset"]
    timeless = {k: v for k, v in pause(TODAY).items() if k != "timestamp"}
    assert [f.kind for f in sl.session_facts([timeless, reset(TODAY)], LIVE, CAL)] == ["reset", "pause"]
    same = [pause(TODAY, ts="20:00:00"), reset(TODAY, ts="20:00:00")]
    assert [f.kind for f in sl.session_facts(same, LIVE, CAL)] == ["reset", "pause"]


def test_the_day_stop_shows_on_the_fact():
    facts = sl.session_facts([summ(TODAY, day_stop={"pct": 1.4, "limit": 1.2, "wall": False}), summ(TODAY)], LIVE, CAL)
    assert facts[0].day_stop is True and facts[0].kind == "clean"
    assert sl.session_facts([summ(TODAY)], LIVE, CAL)[0].day_stop is False
    assert sl.session_facts([summ(TODAY, day_stop="yes")], LIVE, CAL)[0].day_stop is True


def test_day_stopped_reads_the_stop_of_that_day_and_venue_only():
    d2 = TODAY + timedelta(days=1)
    entries = [None, summ(TODAY), summ(TODAY, batch="b2", day_stop={"pct": 1.4, "limit": 1.2, "wall": False}),
               summ(d2, venue=PAPER, day_stop={"pct": 3, "limit": 2, "wall": True})]
    why = sl.day_stopped(entries, LIVE, TODAY)
    assert why and "1.40%" in why and "1.20%" in why and EM_DASH not in why and "\n" not in why
    assert sl.day_stopped(entries, LIVE, d2) is None
    assert sl.day_stopped(entries, PAPER, d2) and "daily halt" in sl.day_stopped(entries, PAPER, d2)
    assert sl.day_stopped([summ(TODAY)], LIVE, TODAY) is None
    assert sl.day_stopped([], LIVE, TODAY) is None


@pytest.mark.parametrize("bad", ["yes", None, [1.4, 1.2], 1.4, True, {"pct": "a lot"}, {}])
def test_a_malformed_day_stop_counts_as_a_stop(bad):
    why = sl.day_stopped([summ(TODAY, day_stop=bad)], LIVE, TODAY)
    assert isinstance(why, str) and why


def fill(units, batch="b1", venue=PAPER):
    return {"type": "order_step", "event": "fill", "venue": venue, "batch": batch, "day": TODAY.isoformat(),
            "units": units, "symbol": "SPY"}


def test_fractional_proven_counts_non_whole_fills_of_done_paper_batches():
    done, stopped = summ(TODAY, venue=PAPER, batch="b1"), stop(TODAY, "send", venue=PAPER, batch="b2")
    entries = [None, done, stopped, fill(0.5), fill(-0.25), fill(2.0), fill(3), fill(0.5, batch="b2"),
               fill(0.5, batch="b3"), fill(0.5, venue=LIVE), fill(math.nan), fill(math.inf), fill("0.5"), fill(True),
               fill(None)]
    assert sl.fractional_proven(entries, PAPER) == 2
    # T34b-1 R2: a "summary" of b1 with no status is not a clean summary of b1, so b1 proves nothing
    assert sl.fractional_proven(entries + [{**fill(0.5), "event": "summary"}], PAPER) == 0
    assert sl.fractional_proven([done, fill(1.0), fill(7)], PAPER) == 0
    assert sl.fractional_proven([fill(0.5), done], PAPER) == 1              # the done summary comes after its fills
    assert sl.fractional_proven([], PAPER) == 0


# ------------------------------------------------------------------ 3. the position (D4): edges
FIRST = date(2025, 1, 6)                       # a Monday


class Days:
    """Consecutive weekdays, handed out one at a time."""

    def __init__(self, start=FIRST):
        self.d = start - timedelta(days=1)

    def __call__(self) -> date:
        self.d += timedelta(days=1)
        while self.d.weekday() >= 5:
            self.d += timedelta(days=1)
        return self.d


BIG = 1e12                                       # a recorded day ceiling that never bites


def F(kind, day, reasons=("r",), ceiling=BIG):
    """A fact; a session carries the day's recorded ceiling (A4), an event none."""
    return sl.SessionFact(day, kind, tuple(reasons), False, ceiling if kind in sl.SESSION_KINDS else None)


def run(days, kinds_, ceiling=BIG):
    return [F(k, days(), ceiling=ceiling) for k in kinds_]


def pos(facts, first=500.0, cap=1e6):
    return sl.position(facts, first, cap)


def shape(p):
    return (p.mode, p.stake, p.clean, p.needed, p.paused)


def test_the_start_is_ticket_mode_at_the_first_stake():
    assert shape(pos([])) == ("ticket", 500.0, 0, 20, False)
    p = pos([])
    assert p.changes == () and isinstance(p.changes, tuple)
    with pytest.raises(FrozenInstanceError):
        p.stake = 1.0                                                   # type: ignore[misc]


def test_the_exact_sessions_at_which_the_mode_and_the_stake_change():
    days = Days()
    facts = run(days, ["clean"] * 19)
    assert shape(pos(facts)) == ("ticket", 500.0, 19, 20, False)
    facts += run(days, ["clean"])
    assert shape(pos(facts)) == ("unattended", 500.0, 0, 40, False)
    assert pos(facts).changes[-1][0] == facts[-1].day
    facts += run(days, ["clean"] * 39)
    assert shape(pos(facts)) == ("unattended", 500.0, 39, 40, False)
    facts += run(days, ["clean"])
    assert shape(pos(facts)) == ("unattended", 1000.0, 0, 60, False)
    facts += run(days, ["clean"] * 59)
    assert shape(pos(facts)) == ("unattended", 1000.0, 59, 60, False)
    facts += run(days, ["clean"])
    assert shape(pos(facts)) == ("unattended", 2000.0, 0, 60, False)
    assert [c[0] for c in pos(facts).changes] == [facts[19].day, facts[59].day, facts[119].day]
    assert all(type(c[1]) is str and EM_DASH not in c[1] for c in pos(facts).changes)


def test_a_stake_at_the_ceiling_stays_there_and_a_doubling_stops_at_the_ceiling():
    days = Days()
    facts = run(days, ["clean"] * 60)
    assert shape(pos(facts, cap=750)) == ("unattended", 750.0, 0, 60, False)       # min(1000, 750)
    facts += run(days, ["clean"] * 60)
    assert shape(pos(facts, cap=750)) == ("unattended", 750.0, 0, 60, False)
    assert shape(pos(facts, cap=1000)) == ("unattended", 1000.0, 0, 60, False)


def test_a_ceiling_below_the_first_stake_at_read_time_lowers_the_stake_at_once():
    p = pos([], first=500, cap=300)
    assert shape(p) == ("ticket", 300.0, 0, 20, False)
    assert len(p.changes) == 1 and p.changes[0][0] is None and "300.00" in p.changes[0][1]


def test_a_fault_during_ticket_mode_halves_and_zeroes_the_count():
    days = Days()
    facts = run(days, ["clean"] * 7 + ["fault"])
    assert shape(pos(facts)) == ("ticket", 250.0, 0, 20, False)
    facts += run(days, ["clean"] * 20)
    assert shape(pos(facts)) == ("unattended", 250.0, 0, 40, False)


def test_a_fault_unattended_keeps_the_mode_and_the_needed():
    days = Days()
    facts = run(days, ["clean"] * 60 + ["clean"] * 5 + ["fault"])
    assert shape(pos(facts)) == ("unattended", 500.0, 0, 60, False)


def test_halving_has_no_floor():
    days = Days()
    assert pos(run(days, ["fault"] * 30)).stake == 500.0 / 2 ** 30


def test_a_skipped_session_neither_counts_nor_resets():
    days = Days()
    facts = run(days, ["clean"] * 10 + ["skipped"] * 5 + ["clean"] * 9)
    assert shape(pos(facts)) == ("ticket", 500.0, 19, 20, False)


def test_a_pause_with_no_reset_is_paused_no_clean_session_counts_and_faults_still_halve():
    days = Days()
    facts = run(days, ["clean"] * 5) + [F("pause", days())] + run(days, ["clean"] * 100)
    assert shape(pos(facts)) == ("ticket", 500.0, 5, 20, True)
    facts += run(days, ["fault"] * 3)                                     # F1: a fault halves on any day
    assert shape(pos(facts)) == ("ticket", 62.5, 0, 20, True)


def test_the_session_of_the_pause_day_and_of_the_reset_day_do_not_count():
    days = Days()
    facts = run(days, ["clean"] * 19)
    d = days()
    paused = facts + [F("clean", d), F("pause", d)]
    assert shape(pos(paused)) == ("ticket", 500.0, 19, 20, True)
    d2 = days()
    back = paused + [F("clean", d2), F("reset", d2)]
    assert shape(pos(back)) == ("ticket", 500.0, 0, 20, False)
    back += [F("clean", days())]
    assert shape(pos(back)) == ("ticket", 500.0, 1, 20, False)


def _shape_then_reset(prefix, first=500.0, cap=1e6):
    days = Days()
    facts = run(days, prefix)
    before = pos(facts, first, cap)
    facts += [F("pause", days()), F("reset", days())]
    return before, pos(facts, first, cap)


def test_a_reset_from_ticket_mode_stays_ticket():
    before, after = _shape_then_reset(["clean"] * 9 + ["fault"] + ["clean"] * 3)
    assert shape(before) == ("ticket", 250.0, 3, 20, False)
    assert shape(after) == ("ticket", 250.0, 0, 20, False)


def test_a_reset_unattended_at_or_below_the_first_stake_goes_back_to_ticket_mode():
    before, after = _shape_then_reset(["clean"] * 20 + ["clean"] * 7)
    assert shape(before) == ("unattended", 500.0, 7, 40, False)
    assert shape(after) == ("ticket", 500.0, 0, 20, False)
    before, after = _shape_then_reset(["clean"] * 20 + ["fault"])
    assert shape(before) == ("unattended", 250.0, 0, 40, False)
    assert shape(after) == ("ticket", 250.0, 0, 20, False)


def test_a_reset_unattended_halving_to_the_first_stake_needs_40():
    before, after = _shape_then_reset(["clean"] * 60 + ["clean"] * 3)
    assert shape(before) == ("unattended", 1000.0, 3, 60, False)
    assert shape(after) == ("unattended", 500.0, 0, 40, False)


def test_a_reset_unattended_halving_above_the_first_stake_keeps_60():
    before, after = _shape_then_reset(["clean"] * 120 + ["clean"] * 3)
    assert shape(before) == ("unattended", 2000.0, 3, 60, False)
    assert shape(after) == ("unattended", 1000.0, 0, 60, False)


def test_a_second_pause_and_a_doubled_pause():
    days = Days()
    facts = run(days, ["clean"] * 120)                                   # unattended at 2000
    facts += [F("pause", days()), F("reset", days())]                    # 1000, needs 60
    facts += run(days, ["clean"] * 4)
    assert shape(pos(facts)) == ("unattended", 1000.0, 4, 60, False)
    facts += [F("pause", days())]
    assert shape(pos(facts)) == ("unattended", 1000.0, 4, 60, True)
    facts += [F("pause", days())] + run(days, ["clean"] * 3) + [F("reset", days())]
    assert shape(pos(facts)) == ("unattended", 500.0, 0, 40, False)     # two pauses, one reset: one step down


def test_a_reset_with_no_pause_changes_nothing():                        # the reading: the trigger is a pause, then a reset
    days = Days()
    facts = run(days, ["clean"] * 60) + [F("reset", days())]
    assert shape(pos(facts)) == ("unattended", 1000.0, 0, 60, False)


def test_a_pause_and_a_reset_on_one_day_step_down_once_in_the_order_their_times_tell():
    days = Days()
    facts = run(days, ["clean"] * 60)
    d = days()
    t1, t2 = datetime.fromisoformat(_utc(d, 15)), datetime.fromisoformat(_utc(d, 16))
    p = pos(facts + [replace(F("pause", d), at=t1), replace(F("reset", d), at=t2)])
    assert (shape(p), p.possible) == (("unattended", 500.0, 0, 40, False), 1)
    p = pos(facts + [replace(F("reset", d), at=t1), replace(F("pause", d), at=t2)])
    assert (shape(p), p.possible) == (("unattended", 1000.0, 0, 60, True), 1)
    # addendum 2: with no times both orders are possible; the lowest stake, the pause where any has it, the slowest
    p = pos(facts + [F("pause", d), F("reset", d)])
    assert (shape(p), p.possible, p.earned) == (("unattended", 500.0, 0, 60, True), 2, 500.0)


def test_twenty_halvings_and_doublings_return_exactly_the_start():
    for first in (500.0, 0.1, 333.33, 1e-3, 123456.789, 7, 1 / 3):
        days = Days()
        facts = run(days, ["clean"] * 60)                                    # unattended at 2 * first
        top = pos(facts, first, 1e9).stake
        assert top == 2 * first
        facts += run(days, ["fault"] * 20)
        assert pos(facts, first, 1e9).stake == top / 2 ** 20
        facts += run(days, ["clean"] * 60 * 20)
        assert pos(facts, first, 1e9).stake == top                           # exactly, not approximately


def test_the_position_refuses_inputs_it_cannot_judge():
    days = Days()
    good = run(days, ["clean"])
    for first, cap in ((True, 1e6), (0, 1e6), (-1, 1e6), (math.nan, 1e6), (math.inf, 1e6), ("500", 1e6),
                       (500, False), (500, 0), (500, math.nan), (500, None)):
        with pytest.raises(ValueError):
            sl.position(good, first, cap)
    with pytest.raises(ValueError):
        sl.position([{"day": TODAY, "kind": "clean"}], 500, 1e6)
    with pytest.raises(ValueError):
        sl.position([F("clean", TODAY), F("clean", TODAY - timedelta(days=1))], 500, 1e6)
    with pytest.raises(ValueError):
        sl.position([F("great", TODAY)], 500, 1e6)


def _reset_after(cleans, first, cap):
    days = Days()
    facts = run(days, ["clean"] * cleans, ceiling=cap)                           # the same ceiling every day
    before = pos(facts, first, cap)
    return before, pos(facts + [F("pause", days()), F("reset", days())], first, cap)


def test_a1_the_reset_step_down_never_goes_below_the_first_stake():
    before, after = _reset_after(60, 100, 150)                                   # the capped example
    assert shape(before) == ("unattended", 150.0, 0, 60, False)
    assert shape(after) == ("unattended", 100.0, 0, 40, False)                   # the step it came from, not 75
    before, after = _reset_after(25, 100, 1e6)                                   # exactly the first stake
    assert shape(before) == ("unattended", 100.0, 5, 40, False)
    assert shape(after) == ("ticket", 100.0, 0, 20, False)
    before, after = _reset_after(60, 100, 100.5)                                 # just above it
    assert shape(before) == ("unattended", 100.5, 0, 60, False)
    assert shape(after) == ("unattended", 100.0, 0, 40, False)
    before, after = _reset_after(180, 100, 1e6)                                  # far above it
    assert shape(before) == ("unattended", 800.0, 0, 60, False)
    assert shape(after) == ("unattended", 400.0, 0, 60, False)
    before, after = _reset_after(120, 100, 300)                                  # half above the first stake: 60
    assert shape(before) == ("unattended", 300.0, 0, 60, False)
    assert shape(after) == ("unattended", 150.0, 0, 60, False)


def test_a1_a_fault_still_halves_below_the_first_stake():
    days = Days()
    assert pos(run(days, ["clean"] * 60 + ["fault"] * 3, ceiling=150), 100, 150).stake == 150 / 8


def test_a4_the_former_201_against_200_counterexample_holds_at_every_prefix():
    """Addendum-1's counterexample (first stake 100, 120 clean sessions, pause, reset, 40 clean) under A4: the
    history is the same whatever the read-time ceiling, so the lower one (200) is never above the higher (201)."""
    for recorded in (BIG, 201, 200):
        days = Days()
        facts = (run(days, ["clean"] * 120, ceiling=recorded) + [F("pause", days()), F("reset", days())]
                 + run(days, ["clean"] * 40, ceiling=recorded))
        low, high = sl._walk(facts, 100, 200), sl._walk(facts, 100, 201)
        assert len(low) == len(high) == len(facts)
        for (_, a), (_, b) in zip(low, high):
            assert a.stake <= b.stake
            assert (a.mode, a.clean, a.needed, a.paused) == (b.mode, b.clean, b.needed, b.paused)
        assert low[-1][1].stake <= 200 and high[-1][1].stake <= 201


# A4 (addendum 2): the ceiling is a fact of each day; the read-time ceiling only caps the result
def test_a4_the_days_ceiling_is_the_smallest_usable_one_of_its_summaries():
    entries = [summ(TODAY, ceiling=900), summ(TODAY, ceiling=700, batch="b2"), stop(TODAY, "prices", ceiling=800),
               summ(TODAY, ceiling=True), summ(TODAY, ceiling=math.nan), summ(TODAY, ceiling=-math.inf),
               summ(TODAY, ceiling=0), summ(TODAY, ceiling=-5), summ(TODAY, ceiling="1"), summ(TODAY, ceiling=None),
               summ(TODAY, ceiling=[1]), summ(TODAY, ceiling=OMIT), summ(TODAY, ceiling=1, venue=PAPER)]
    facts = sl.session_facts(entries, LIVE, CAL)
    assert len(facts) == 1 and facts[0].ceiling == 700.0
    assert facts.unreadable == 0                                         # an unusable ceiling is no unusable entry
    for bad in (True, False, math.nan, math.inf, -math.inf, 0, -5, "500", None, [500], {"v": 1}, OMIT):
        assert sl.session_facts([summ(TODAY, ceiling=bad)], LIVE, CAL)[0].ceiling is None
    assert sl.session_facts([summ(TODAY, ceiling=3)], LIVE, CAL)[0].ceiling == 3.0
    assert sl.session_facts([pause(TODAY)], LIVE, CAL)[0].ceiling is None


def test_a4_a_lowered_day_ceiling_lowers_the_earned_stake_at_once_on_any_session():
    days = Days()
    facts = run(days, ["clean"] * 120)                                   # earned 2000
    for kind in ("clean", "skipped", "fault"):
        p = pos(facts + [F(kind, days(), ceiling=700)], 500, 1e6)
        assert p.earned == (350.0 if kind == "fault" else 700.0) and p.stake == p.earned
    paused = facts + [F("pause", days()), F("clean", days(), ceiling=600)]
    assert pos(paused, 500, 1e6).earned == 600.0                         # paused: nothing counts, the clamp still acts
    d = days()
    assert any(day == d and "700.00" in w and "2,000.00" in w
               for day, w in pos(facts + [F("skipped", d, ceiling=700)], 500, 1e6).changes)


def test_a4_the_doubling_stops_at_the_days_ceiling_and_a_day_without_one_cannot_raise():
    days = Days()
    facts = run(days, ["clean"] * 60)                                    # earned 1000, 0 of 60
    p = pos(facts + run(days, ["clean"] * 60, ceiling=1500), 500, 1e6)
    assert (p.earned, p.clean, p.needed) == (1500.0, 0, 60)
    q = pos(facts + run(days, ["clean"] * 59) + [F("clean", days(), ceiling=None)], 500, 1e6)
    assert (q.earned, q.stake, q.clean, q.needed, q.mode) == (1000.0, 1000.0, 0, 60, "unattended")
    assert "no ceiling" in q.changes[-1][1]
    r = pos(run(Days(), ["clean"] * 20, ceiling=None), 500, 1e6)          # ticket to unattended needs no ceiling
    assert (r.mode, r.earned) == ("unattended", 500.0)


def test_a4_the_read_time_ceiling_only_caps_the_returned_stake():
    days = Days()
    facts = run(days, ["clean"] * 120) + [F("pause", days()), F("reset", days())] + run(days, ["clean"] * 7)
    a, b = pos(facts, 500, 300), pos(facts, 500, 1e6)
    assert (a.mode, a.clean, a.needed, a.paused, a.earned) == (b.mode, b.clean, b.needed, b.paused, b.earned)
    assert (b.earned, b.stake, a.stake) == (1000.0, 1000.0, 300.0)
    assert a.changes[-1][0] is None and "300.00" in a.changes[-1][1] and "1,000.00" in a.changes[-1][1]
    assert a.changes[:-1] == b.changes
    assert pos([], 500, 300).earned == 500.0 and pos([], 500, 300).stake == 300.0


def test_a4_describe_says_when_the_owners_ceiling_holds_the_stake_below_the_earned():
    p = pos(run(Days(), ["clean"] * 60), 500, 800)
    text = lines_ok(sl.describe(p, None, LADDER, 0))
    assert "800.00" in text and "1,000.00" in text and "the owner's ceiling" in text
    assert "the owner's ceiling" not in lines_ok(sl.describe(pos(run(Days(), ["clean"] * 60), 500, 1e6), None,
                                                             LADDER, 0))


# ------------------------------------------------------------------ 3. the position (D4): properties on generated journals
SEED = 20261114
FAULT_KINDS = ["reconcile", "attention:stale_quote", "cap"]
SKIPS = sorted(sl.SKIP_STAGES)
FAULTS = sorted(sl.FAULT_STAGES)


def _ts(rng, d):
    """A time on `d`'s own New York date (addendum 2, D-F: a time on another New York date is unknown)."""
    return datetime(d.year, d.month, d.day, rng.randint(0, 22), rng.randint(0, 59), rng.randint(0, 59),
                    tzinfo=NY).isoformat(timespec="seconds")


UNUSABLE_CEILINGS = [True, False, math.nan, math.inf, 0, -100.0, "500", None, OMIT]


def generate(rng, n_days, fault_rate=None, pause_rate=None, drift_rate=0.05, skip_rate=0.08, gaps=0.12, ceilings=None,
             noisy=False, apart=False):
    """A generated live journal: (blocks, meta). Each block is one weekday's entries (shuffled); meta per day says what
    was put there: runs (done, fault-stage stop, fault kind, drift, skip stop) and events. `ceilings` (A4) is the
    owner's ceiling per day, written into each summary; `noisy` also gives some summaries a higher one, none, or an
    unusable one, so a day's recorded ceiling is the smallest usable one or None (kept in meta["ceiling"]). `apart`
    (T34b-1): no day holds both a fault and a pause or reset (the events of a fault day are left out)."""
    fault_rate = rng.choice([0.0, 0.002, 0.01, 0.04]) if fault_rate is None else fault_rate
    pause_rate = rng.choice([0.0, 0.01, 0.03]) if pause_rate is None else pause_rate
    blocks, meta, batch = [], [], 0
    for d in sessions_from(FIRST, n_days):
        block, m = [], {"day": d, "done": False, "fault": False, "drift": False, "skip": False, "runs": 0,
                        "ceiling": None}
        day_cap = None if ceilings is None else ceilings[len(blocks)]
        if rng.random() >= gaps:
            for _ in range(rng.randint(1, 3)):
                batch += 1
                x = rng.random()
                if x < fault_rate:
                    m["fault"] = True
                    e = stop(d, rng.choice(FAULTS), batch=f"b{batch}") if rng.random() < 0.5 else \
                        summ(d, batch=f"b{batch}", faults=[rng.choice(FAULT_KINDS)])
                elif x < fault_rate + skip_rate:
                    m["skip"] = True
                    e = stop(d, rng.choice(SKIPS), batch=f"b{batch}")
                else:
                    m["done"] = True
                    e = summ(d, batch=f"b{batch}")
                    if rng.random() < drift_rate:
                        m["drift"] = True
                        e["faults"] = ["drift"]
                if rng.random() < 0.05:
                    e["day_stop"] = {"pct": 1.5, "limit": 1.2, "wall": False}
                e.pop("ceiling", None)
                c = day_cap
                if noisy and day_cap is not None:
                    y = rng.random()
                    c = (rng.choice(UNUSABLE_CEILINGS) if y < 0.08 else day_cap * rng.choice([1.001, 1.5, 3])
                         if y < 0.2 else day_cap)
                if c is not OMIT:
                    e["ceiling"] = c
                if type(c) in (int, float) and math.isfinite(c) and c > 0:
                    m["ceiling"] = float(c) if m["ceiling"] is None else min(m["ceiling"], float(c))
                e["timestamp"] = _ts(rng, d)
                block.append(e)
                m["runs"] += 1
            if rng.random() < 0.3:
                block.append(fill(rng.choice([1.0, 0.5]), batch=f"b{batch}", venue=LIVE))
        if rng.random() < 0.15:
            block.append(summ(d, venue=PAPER))                           # another venue: ignored
        if rng.random() < pause_rate and not (apart and m["fault"]):
            block.append({**pause(d), "timestamp": _ts(rng, d)})
        if rng.random() < pause_rate * 1.5 and not (apart and m["fault"]):
            block.append({**reset(d), "timestamp": _ts(rng, d)})
        if rng.random() < 0.02:
            block.append(rng.choice([None, 7, "torn line", summ("2025-02-30")]))
        rng.shuffle(block)
        blocks.append(block)
        meta.append(m)
    return blocks, meta


def flat(blocks):
    return [e for b in blocks for e in b]


def moving_ceilings(rng, first, n):
    """A ceiling per day that moves up and down (A4): mostly still, sometimes halved, doubled or nudged."""
    c, out = first * rng.choice([0.5, 1, 1.5, 2, 4, 8, 64]), []
    for _ in range(n):
        if rng.random() < 0.04:
            c = max(first / 8, min(first * 256, c * rng.choice([0.5, 0.9, 0.999, 1.001, 1.1, 2, 4])))
        out.append(c)
    return out


def histories(n, lo=30, hi=220, moving=False, seed=SEED, **kw):
    """Generated histories with a fixed seed. The owner's ceiling is written into every summary: the read-time `cap`
    on every day, or (moving) a ceiling that moves day by day, with noise (see generate)."""
    rng = random.Random(seed)
    for _ in range(n):
        first = rng.choice([500.0, 250.0, 1000.0, 0.1, 333.33, 77.7])
        cap = first * rng.choice([0.5, 1, 1.5, 2, 3, 4, 8, 1000])
        n_days = rng.randint(lo, hi)
        ceilings = moving_ceilings(rng, first, n_days) if moving else [cap] * n_days
        blocks, meta = generate(rng, n_days, ceilings=ceilings, noisy=moving, **kw)
        yield rng, first, cap, blocks, meta


def daily_positions(facts, first, cap):
    """The position at the end of each day that has a fact, from the facts up to that day."""
    out, days = [], sorted({f.day for f in facts})
    for d in days:
        out.append((d, sl.position([f for f in facts if f.day <= d], first, cap)))
    return out


def test_property_session_kinds_match_what_was_written():
    n = 0
    for _, _, _, blocks, meta in histories(300):
        facts = sl.session_facts(flat(blocks), LIVE, CAL)
        got = {f.day: f.kind for f in facts if f.kind in sl.SESSION_KINDS}
        for m in meta:
            want = None if not m["runs"] else "fault" if m["fault"] else "clean" if m["done"] else "skipped"
            assert got.get(m["day"]) == want
            n += 1
    assert n > 30_000


def test_property_a_and_c_stake_capped_rises_only_on_the_needed_clean_session_and_a_fault_halves():
    checked_rises = checked_faults = 0
    for _, first, cap, blocks, _ in histories(400):
        facts = sl.session_facts(flat(blocks), LIVE, CAL)
        by_day = {}
        for f in facts:
            by_day.setdefault(f.day, []).append(f)
        prev = sl.position([], first, cap)
        for d, p in daily_positions(facts, first, cap):
            today = by_day[d]
            events = [f for f in today if f.kind in sl.EVENT_KINDS]
            session = [f for f in today if f.kind in sl.SESSION_KINDS]
            assert p.stake <= cap
            one = prev.possible == 1 and p.possible == 1                                   # addendum 2: one position
            if p.stake > prev.stake:                                                       # (a)
                assert session and session[0].kind == "clean" and not any(f.kind == "pause" for f in events)
            if p.stake > prev.stake and one:
                checked_rises += 1
                assert not prev.paused
                assert prev.mode == "unattended" and prev.clean == prev.needed - 1 and p.clean == 0
            if session and session[0].kind == "fault" and not events and not prev.paused and one:  # (c)
                checked_faults += 1
                assert p.stake == prev.stake / 2 and p.clean == 0
                assert (p.mode, p.needed) == (prev.mode, prev.needed)
            prev = p
    assert checked_rises > 100 and checked_faults > 100


def test_property_b_a_skipped_session_changes_nothing():
    checked = 0
    for rng, first, cap, blocks, meta in histories(300):
        empty = [i for i, m in enumerate(meta) if not m["runs"]]
        if not empty:
            continue
        i = rng.choice(empty)
        d = meta[i]["day"]
        added = [stop(d, rng.choice(SKIPS), batch="extra", ceiling=cap)] * rng.randint(1, 2)
        before = sl.position(sl.session_facts(flat(blocks), LIVE, CAL), first, cap)
        blocks2 = blocks[:i] + [blocks[i] + added] + blocks[i + 1:]

        def _not_clamp(changes):
            return [c for c in changes if not c[1].startswith("the owner's ceiling that day")]
        facts2 = sl.session_facts(flat(blocks2), LIVE, CAL)
        assert any(f.day == d and f.kind == "skipped" for f in facts2)
        after = sl.position(facts2, first, cap)
        # A4: every session, a skipped one too, first clamps to its day's ceiling: with a ceiling below the first
        # stake, an inserted skipped day can only move that clamp's line earlier. Everything else is unchanged.
        assert (after.mode, after.stake, after.clean, after.needed, after.paused, after.earned) == \
               (before.mode, before.stake, before.clean, before.needed, before.paused, before.earned)
        assert _not_clamp(after.changes) == _not_clamp(before.changes)
        checked += 1
    assert checked > 250


def test_property_d_n_halvings_then_clean_sessions_return_through_the_same_stakes():
    rng = random.Random(SEED + 4)
    for _ in range(300):
        first = rng.choice([500.0, 0.1, 333.33, 1 / 3, 9.99, 12345.678]) * rng.choice([1, 3, 7])
        n = rng.randint(1, 20)
        days = Days()
        facts = run(days, ["clean"] * 60)                                   # unattended at 2 * first
        top = sl.position(facts, first, 1e9).stake
        facts += run(days, ["fault"] * n)
        down = [top / 2 ** k for k in range(n + 1)]
        assert sl.position(facts, first, 1e9).stake == down[n]
        for k in range(n):
            facts += run(days, ["clean"] * 60)
            assert sl.position(facts, first, 1e9).stake == down[n - k - 1]
        assert sl.position(facts, first, 1e9).stake == top


E_HISTORIES = 2000                  # (e1) to (e3): generated histories with day-by-day ceilings that move


def test_property_the_days_ceiling_is_the_smallest_usable_one_written():
    days = 0
    for _, _, _, blocks, meta in histories(300, moving=True):
        got = {f.day: f.ceiling for f in sl.session_facts(flat(blocks), LIVE, CAL) if f.kind in sl.SESSION_KINDS}
        for m in meta:
            if m["runs"]:
                assert got[m["day"]] == m["ceiling"]
                days += 1
    assert days > 30_000


def test_property_e1_a_lower_read_time_ceiling_never_gives_more_and_changes_nothing_else():
    broken, prefixes = [], 0
    for rng, first, cap, blocks, _ in histories(E_HISTORIES, moving=True):
        facts = sl.session_facts(flat(blocks), LIVE, CAL)
        c2 = first * rng.choice([0.5, 1, 2, 3, 8, 100, 1e6]) * rng.uniform(0.5, 2)
        c2 = min(c2, wb.LIVE_MONEY_MAX)                           # T34b-1 R3: a ceiling above it is refused
        c1 = c2 * rng.choice([1.0, 0.999, rng.uniform(0.9, 1.0), rng.uniform(0.01, 1.0)])
        w1, w2 = sl._walk(facts, first, c1), sl._walk(facts, first, c2)
        assert len(w1) == len(w2) and (not w2 or w2[-1][1] == sl.position(facts, first, c2))
        for (d1, a), (d2, b) in zip(w1, w2):
            prefixes += 1
            assert d1 == d2
            assert (a.mode, a.clean, a.needed, a.paused, a.earned) == (b.mode, b.clean, b.needed, b.paused, b.earned)
            if a.stake > b.stake:
                broken.append((len(facts), first, c1, c2, d1))
                break
    print(f"property (e1): {E_HISTORIES} histories, {prefixes} prefixes, {len(broken)} counterexamples", broken[:1])
    assert prefixes > 200_000
    assert broken == []


def _latest_ceiling(facts):
    out, latest = {}, None
    for f in facts:
        if f.kind in sl.SESSION_KINDS and f.ceiling is not None:
            latest = f.ceiling
        out[f.day] = latest
    return out


def test_property_e2_a_raised_read_time_ceiling_raises_nothing_at_once():
    checked = 0
    for rng, first, _, blocks, _ in histories(E_HISTORIES, moving=True, seed=SEED + 2):
        facts = sl.session_facts(flat(blocks), LIVE, CAL)
        latest = _latest_ceiling(facts)
        for d, p in sl._walk(facts, first, wb.LIVE_MONEY_MAX):             # the earned stake at every prefix
            if latest[d] is not None:
                assert p.earned <= latest[d]                               # so any cap at or above it leaves it there
                checked += 1
        last = latest[facts[-1].day] if facts else None
        if last is not None:
            at = sl.position(facts, first, last)
            for k in (1.0001, 1.5, 10, 1e6):
                if last * k > wb.LIVE_MONEY_MAX:                           # T34b-1 R3: refused, never walked
                    with pytest.raises(ValueError):
                        sl.position(facts, first, last * k)
                    continue
                above = sl.position(facts, first, last * k)
                assert above.stake == at.stake == at.earned
                assert (above.mode, above.clean, above.needed, above.paused) == (at.mode, at.clean, at.needed, at.paused)
    assert checked > 200_000


def test_property_e3_the_stake_never_exceeds_the_days_ceiling_and_no_ceiling_never_rises():
    with_ceiling = without = 0
    for _, first, cap, blocks, _ in histories(E_HISTORIES, moving=True, seed=SEED + 3):
        facts = sl.session_facts(flat(blocks), LIVE, CAL)
        ceiling = {}
        for f in facts:
            if f.kind in sl.SESSION_KINDS:
                ceiling[f.day] = f.ceiling
        prev = first
        for d, p in sl._walk(facts, first, cap):
            assert p.stake <= cap and p.stake == min(p.earned, cap)
            if ceiling.get(d) is not None:
                assert p.earned <= ceiling[d]
                with_ceiling += 1
            else:
                assert p.earned <= prev
                without += 1
            prev = p.earned
    assert with_ceiling > 200_000 and without > 1_000


def reference_a1(facts, first, cap):
    """Addendum-1's walk, kept as the reference for (e4): one ceiling for the whole history, clamped at every step;
    since addendum 3 (F1) with its fault rule: a fault halves on any day, and a day's sessions come before its events."""
    mode, stake, clean, needed, paused = "ticket", min(first, cap), 0, sl.TICKET_SESSIONS, False
    groups = {}
    for f in facts:
        groups.setdefault(f.day, []).append(f)
    for day in sorted(groups):
        group = groups[day]
        counts = not paused and not any(f.kind == "pause" for f in group)
        group = [f for f in group if f.kind in sl.SESSION_KINDS] + [f for f in group if f.kind in sl.EVENT_KINDS]
        for f in group:
            if f.kind == "pause":
                paused = True
            elif f.kind == "reset":
                if not paused:
                    continue
                paused, clean = False, 0
                if mode == "unattended" and stake > first:
                    stake = max(first, stake / 2)
                    needed = sl.DOUBLING_SESSIONS if stake > first else sl.UNATTENDED_SESSIONS
                elif mode == "unattended":
                    mode, needed = "ticket", sl.TICKET_SESSIONS
            elif f.kind == "fault":
                stake, clean = stake / 2, 0
            elif not counts or f.kind == "skipped":
                continue
            else:
                clean += 1
                if clean < needed:
                    continue
                clean = 0
                if mode == "ticket":
                    mode, needed = "unattended", sl.UNATTENDED_SESSIONS
                else:
                    stake, needed = min(2 * stake, cap), sl.DOUBLING_SESSIONS
            stake = min(stake, cap)
    return mode, stake, clean, needed, paused


# T34b-1 R6 (b): a LITERAL copy of the walk as it stood before T34b-1 (the accepted module at 69f4fc4, `_amount` and
# `_walk_all`), names qualified, a plain record in place of Position. Where no day holds both a fault and a pause or
# reset, the new walk must give exactly what this one gives: every field and every change line.
@dataclass(frozen=True)
class BasePosition:
    mode: str
    stake: float
    clean: int
    needed: int
    paused: bool
    changes: tuple
    earned: float


def base_amount(x: float) -> str:
    d = Decimal(repr(float(x)))
    if d == 0:
        return "0.00"
    if abs(d) >= 1:
        return f"{d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"
    text = format(d.quantize(Decimal(1).scaleb(d.adjusted() - 5), rounding=ROUND_HALF_UP), "f").rstrip("0")
    whole, _, cents = text.partition(".")
    return f"{whole}.{cents.ljust(2, '0')}"


def base_walk_all(facts, first_stake, max_capital):
    _amount, Position = base_amount, BasePosition
    SESSION_KINDS, EVENT_KINDS, SessionFact, _number = sl.SESSION_KINDS, sl.EVENT_KINDS, sl.SessionFact, sl._number
    TICKET_SESSIONS, UNATTENDED_SESSIONS, DOUBLING_SESSIONS = (sl.TICKET_SESSIONS, sl.UNATTENDED_SESSIONS,
                                                               sl.DOUBLING_SESSIONS)
    _PAUSE_WORDS = sl._PAUSE_WORDS
    first = sl._positive(first_stake, "first_stake")
    cap = sl._positive(max_capital, "max_capital")
    facts = list(facts)
    for i, f in enumerate(facts):
        if not isinstance(f, SessionFact) or type(f.day) is not date or f.kind not in SESSION_KINDS + EVENT_KINDS:
            raise ValueError("the position is worked out from session facts only (session_facts gives them)")
        if f.ceiling is not None and (_number(f.ceiling) is None or f.ceiling <= 0):
            raise ValueError("a session fact's ceiling must be a finite number above 0, or None")
        if i and f.day < facts[i - 1].day:
            raise ValueError("the session facts are not in day order")
    mode, stake, clean, needed, paused = "ticket", first, 0, TICKET_SESSIONS, False     # stake: the earned E
    changes = []

    def at(changes_now: list):
        held = min(stake, cap)
        if held < stake:
            changes_now = changes_now + [(None, f"the owner's ceiling at this read (max_capital {_amount(cap)}) holds the "
                                                f"stake at {_amount(held)}, below the {_amount(stake)} the record earned")]
        return Position(mode, held, clean, needed, paused, tuple(changes_now), stake)

    walk = []
    i = 0
    while i < len(facts):
        day = facts[i].day
        j = i
        while j < len(facts) and facts[j].day == day:
            j += 1
        group = facts[i:j]
        counts = not paused and not any(f.kind == "pause" for f in group)
        group = [f for f in group if f.kind in SESSION_KINDS] + [f for f in group if f.kind in EVENT_KINDS]
        for f in group:
            if f.kind in SESSION_KINDS and f.ceiling is not None and stake > f.ceiling:
                changes.append((day, f"the owner's ceiling that day ({_amount(f.ceiling)}) lowers the stake from "
                                     f"{_amount(stake)} to {_amount(f.ceiling)}"))
                stake = f.ceiling
            if f.kind == "pause":
                if not paused:
                    paused = True
                    changes.append((day, _PAUSE_WORDS))
            elif f.kind == "reset":
                if not paused:
                    continue
                paused, clean = False, 0
                if mode == "unattended" and stake > first:
                    before, stake = stake, max(first, stake / 2)
                    needed = DOUBLING_SESSIONS if stake > first else UNATTENDED_SESSIONS
                    changes.append((day, f"the owner's reset after the pause: one step down, the stake goes from "
                                         f"{_amount(before)} to {_amount(stake)} (half, never below the first stake), "
                                         f"unattended, {needed} clean sessions to the next doubling"))
                elif mode == "unattended":
                    mode, needed = "ticket", TICKET_SESSIONS
                    changes.append((day, f"the owner's reset after the pause: one step down, back to ticket mode at "
                                         f"{_amount(stake)}"))
                else:
                    changes.append((day, "the owner's reset after the pause: ticket mode stays, the count restarts"))
            elif f.kind == "fault":                      # addendum 3, F1: on any day, paused or not
                before, stake, clean = stake, stake / 2, 0
                changes.append((day, f"a fault session ({f.reasons[0] if f.reasons else 'a fault'}): the stake halves "
                                     f"from {_amount(before)} to {_amount(stake)} and the count restarts"))
            elif not counts or f.kind == "skipped":
                continue
            else:
                clean += 1
                if clean < needed:
                    continue
                clean = 0
                if mode == "ticket":
                    mode, needed = "unattended", UNATTENDED_SESSIONS
                    changes.append((day, f"{TICKET_SESSIONS} clean sessions: unattended at the same stake "
                                         f"{_amount(stake)}"))
                elif f.ceiling is None:
                    done, needed = needed, DOUBLING_SESSIONS
                    changes.append((day, f"{done} clean sessions, but no ceiling was recorded that day, so the stake "
                                         f"stays at {_amount(stake)} (no rise without the owner's ceiling); the count "
                                         "restarts"))
                else:
                    done, before = needed, stake
                    stake, needed = min(2 * stake, f.ceiling), DOUBLING_SESSIONS
                    words = (f"the stake doubles from {_amount(before)} to {_amount(stake)}" if stake == 2 * before else
                             f"the stake rises from {_amount(before)} to that day's ceiling {_amount(stake)}"
                             if stake > before else f"the stake stays at that day's ceiling {_amount(stake)}")
                    changes.append((day, f"{done} clean sessions: {words}"))
        walk.append((day, at(changes)))
        i = j
    return at(changes), walk


def as_base(p):
    return (p.mode, p.stake, p.clean, p.needed, p.paused, p.changes, p.earned)


def test_property_e4_one_constant_ceiling_gives_what_addendum_1_gave():
    differing = days = 0
    for _, first, cap, blocks, _ in histories(500, seed=SEED + 5, apart=True):   # every summary's ceiling is `cap`
        facts = sl.session_facts(flat(blocks), LIVE, CAL)
        assert all(f.ceiling == cap for f in facts if f.kind in sl.SESSION_KINDS)
        p = sl.position(facts, first, cap)
        if (p.mode, p.stake, p.clean, p.needed, p.paused) != reference_a1(facts, first, cap):
            differing += 1
        mine, theirs = sl._walk(facts, first, cap), base_walk_all(facts, first, cap)[1]
        assert [(d, as_base(q)) for d, q in mine] == [(d, as_base(q)) for d, q in theirs]
        assert all(q.possible == 1 for _, q in mine)                    # addendum 2: one position throughout
        days += len(mine)
    assert differing == 0 and days > 50_000


def test_property_e4_the_literal_earlier_walk_equals_the_new_one_where_no_day_holds_a_fault_and_an_event():
    days = 0
    for _, first, cap, blocks, _ in histories(500, moving=True, seed=SEED + 15, apart=True, pause_rate=0.03,
                                              fault_rate=0.03):
        facts = sl.session_facts(flat(blocks), LIVE, CAL)
        assert not any(f.kind == "fault" and any(g.day == f.day and g.kind in sl.EVENT_KINDS for g in facts)
                       for f in facts)
        mine, theirs = sl._walk(facts, first, cap), base_walk_all(facts, first, cap)[1]
        assert [(d, as_base(q)) for d, q in mine] == [(d, as_base(q)) for d, q in theirs]
        assert all(q.possible == 1 for _, q in mine)                    # addendum 2: one position throughout
        days += len(mine)
    assert days > 50_000


def test_property_f_reordering_entries_within_one_day_never_changes_the_position():
    for rng, first, cap, blocks, _ in histories(300):
        facts = sl.session_facts(flat(blocks), LIVE, CAL)
        again = [rng.sample(b, len(b)) for b in blocks]
        facts2 = sl.session_facts(flat(again), LIVE, CAL)
        assert list(facts2) == list(facts) and facts2.unreadable == facts.unreadable
        assert sl.position(facts2, first, cap) == sl.position(facts, first, cap)


def test_property_g_no_look_ahead():
    checked = 0
    for rng, first, cap, blocks, meta in histories(300):
        everything = sl.session_facts(flat(blocks), LIVE, CAL)
        for _ in range(4):
            k = rng.randint(0, len(blocks))
            cut = meta[k]["day"] if k < len(meta) else date.max
            head = sl.session_facts(flat(blocks[:k]), LIVE, CAL)
            assert list(head) == [f for f in everything if f.day < cut]
            assert sl.position(head, first, cap) == sl.position([f for f in everything if f.day < cut], first, cap)
            checked += 1
    assert checked == 1200


def test_property_i_drift_acts_only_when_switched_on(monkeypatch):
    seen = 0
    for _, _, _, blocks, meta in histories(300, fault_rate=0.0, drift_rate=0.3):
        entries = flat(blocks)
        off = {f.day: f.kind for f in sl.session_facts(entries, LIVE, CAL) if f.kind in sl.SESSION_KINDS}
        monkeypatch.setattr(sl, "DRIFT_ACTS", True)
        on = {f.day: f.kind for f in sl.session_facts(entries, LIVE, CAL) if f.kind in sl.SESSION_KINDS}
        monkeypatch.setattr(sl, "DRIFT_ACTS", False)
        for m in meta:
            if m["drift"]:
                seen += 1
                assert off[m["day"]] == "clean" and on[m["day"]] == "fault"
            elif m["runs"]:
                assert off[m["day"]] == on[m["day"]]
    assert seen > 5000


# ------------------------------------------------------------------ 4. the working stop (D5) and 5. the paper returns
def rets(values, start=FIRST, exact=True):
    days = Days(start)
    return [(days(), v, exact) for v in values]


def test_fewer_than_25_exact_returns_is_the_floor():
    got = sl.working_stop_pct(rets([0.03, -0.04] * 12), 2.0)
    assert (got.pct, got.basis, got.n) == (1.0, "fewer than 25 returns: the floor", 24)
    assert sl.working_stop_pct([], 2.0).pct == 1.0
    with pytest.raises(FrozenInstanceError):
        got.pct = 3.0                                                   # type: ignore[misc]


def test_a_wall_below_the_floor_is_the_stop():
    got = sl.working_stop_pct(rets([0.0] * 3), 0.5)
    assert got.pct == 0.5 and "floor" in got.basis


def test_25_exact_returns_give_2_5_times_their_spread_in_percent():
    values = [0.01, -0.01] * 12 + [0.01]
    s = statistics.stdev([v * 100 for v in values])
    got = sl.working_stop_pct(rets(values), 5.0)
    assert got.pct == pytest.approx(2.5 * s) and got.n == 25
    assert got.basis == f"25 returns ending {rets(values)[-1][0].isoformat()}"
    assert sl.working_stop_pct(rets(values), 2.0).pct == 2.0                      # the wall


def test_a_quiet_book_is_held_at_the_floor():
    got = sl.working_stop_pct(rets([0.001, -0.001] * 13), 2.0)
    assert got.pct == 1.0 and got.n == 25 and got.basis.startswith("25 returns ending")


def test_only_the_last_25_exact_returns_count():
    wild = rets([0.2, -0.2] * 20)
    calm = rets([0.005, -0.005] * 13, start=wild[-1][0] + timedelta(days=3))
    bridged = [(d, 0.5 if i % 2 else -0.5, False) for i, (d, _, _) in
               enumerate(rets([0] * 10, start=calm[-1][0] + timedelta(days=3)))]
    got = sl.working_stop_pct(wild + calm + bridged, 10.0)
    s = statistics.stdev([v * 100 for _, v, _ in calm[-25:]])
    assert got.pct == pytest.approx(max(1.0, 2.5 * s)) and got.n == 25
    assert got.basis == f"25 returns ending {calm[-1][0].isoformat()}"
    only_bridged = [(d, v, False) for d, v, _ in wild]
    assert sl.working_stop_pct(only_bridged, 10.0).pct == 1.0


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_a_non_finite_return_is_refused_even_a_bridged_one(bad):
    for exact in (True, False):
        with pytest.raises(ValueError):
            sl.working_stop_pct(rets([0.01] * 30) + [(date(2030, 1, 2), bad, exact)], 2.0)


@pytest.mark.parametrize("item", [(FIRST, "0.01", True), (FIRST, True, True), (FIRST, None, True), (FIRST, 0.01, 1),
                                  (FIRST, 0.01, "yes"), ("2025-01-06", 0.01, True), (FIRST, 0.01), 0.01,
                                  (FIRST, 1e307, True)])
def test_a_malformed_return_is_refused(item):
    with pytest.raises(ValueError):
        sl.working_stop_pct([item], 2.0)


def test_returns_out_of_day_order_are_refused():
    a, b = rets([0.01, 0.02])
    with pytest.raises(ValueError):
        sl.working_stop_pct([b, a], 2.0)
    with pytest.raises(ValueError):
        sl.working_stop_pct([a, a], 2.0)


@pytest.mark.parametrize("wall", [0, -1, 100.01, math.nan, math.inf, True, "2", None])
def test_the_wall_must_be_a_number_above_0_and_at_most_100(wall):
    with pytest.raises(ValueError):
        sl.working_stop_pct(rets([0.01] * 3), wall)
    assert sl.working_stop_pct(rets([0.01] * 3), 100).pct == 1.0


def test_property_h_the_working_stop_lies_between_the_floor_and_the_wall():
    rng = random.Random(SEED + 8)
    for _ in range(500):
        wall = rng.choice([0.1, 0.5, 1.0, 2.0, 3.3, 25.0, 100.0, rng.uniform(0.01, 100)])
        scale = rng.choice([0.0, 1e-4, 0.003, 0.01, 0.05, 0.5])
        n = rng.randint(0, 60)
        returns = [(d, rng.gauss(0, scale), rng.random() < 0.8) for d, _, _ in rets([0] * n)]
        got = sl.working_stop_pct(returns, wall)
        assert min(wall, 1.0) <= got.pct <= wall
        exact = [v for _, v, x in returns if x]
        assert got.n == min(25, len(exact))


def summary(day, values, capital=1000.0):
    return {"type": "summary", "day": day, "equity": 3000.0 + sum(values.values()), "capital_per_strategy": capital,
            "strategy_values": dict(values)}


def decay_record(summaries, sessions):
    from valedia_trader.research_loop import decay
    entries = [(s["day"], 1, s) for s in summaries]
    return decay._Record(entries, [], max(sessions), sessions)


def test_paper_returns_combine_the_followed_strategies_over_their_capital():
    d = [x.isoformat() for x in sessions_from(FIRST, 6)]
    record = decay_record([summary(d[0], {"A": 1000, "B": 1000, "C": 1000}),
                           summary(d[1], {"A": 1010, "B": 990, "C": 1500}),
                           summary(d[2], {"A": 1030, "B": 1000, "C": 1500}),
                           summary(d[4], {"A": 1040, "B": 1020, "C": 1500}),         # d[3] lost: a bridge
                           summary(d[5], {"A": 1050, "C": 1500})], d)                # B sold
    got = sl.paper_returns(record, ["A", "B"])
    want = [(d[1], 0.0, True), (d[2], 0.015, True), (d[4], 0.015, False), (d[5], 0.01, False)]
    assert [(x.isoformat(), r, e) for x, r, e in got] == [(a, pytest.approx(b, abs=1e-12), c) for a, b, c in want]
    assert all(type(x) is date for x, _, _ in got)
    assert [(x.isoformat(), e) for x, _, e in sl.paper_returns(record, ["C"])] == [(d[1], True), (d[2], True),
                                                                                    (d[4], False), (d[5], True)]
    assert sl.paper_returns(record, []) == []
    with pytest.raises(ValueError):
        sl.paper_returns(record, [3])
    stop_ = sl.working_stop_pct(got, 2.0)
    assert stop_.n == 2 and stop_.pct == 1.0


def test_paper_returns_weight_by_each_observations_own_capital():
    d = [x.isoformat() for x in sessions_from(FIRST, 3)]
    record = decay_record([summary(d[0], {"A": 1000}, capital=1000.0),
                           summary(d[1], {"A": 1100, "B": 50}, capital=2000.0),      # B from nothing, on 2000
                           summary(d[2], {"A": 1150, "B": 70}, capital=2000.0)], d)
    got = sl.paper_returns(record, ["A", "B"])
    assert [(x.isoformat(), e) for x, _, e in got] == [(d[1], True), (d[2], True)]
    assert got[0][1] == pytest.approx((100 + 50) / (1000 + 2000))
    assert got[1][1] == pytest.approx((50 + 20) / (2000 + 2000))


# ------------------------------------------------------------------ 8. what the owner reads
LADDER = sl.LadderFile("Owner Name", 500.0, True, True, (TODAY + timedelta(days=9)).isoformat(), "0" * 64)


def lines_ok(lines):
    assert lines and all(type(x) is str and x and "\n" not in x and EM_DASH not in x for x in lines)
    text = " ".join(lines)
    for bad in ("/Users", "/Volumes", "/private", "/var", "account_ends", "0" * 64):
        assert bad not in text
    return text


def test_describe_tells_the_mode_the_stake_the_count_the_next_change_the_stop_and_fractions():
    days = Days()
    p = pos(run(days, ["clean"] * 25))
    ws = sl.working_stop_pct(rets([0.01] * 3), 2.0)
    text = lines_ok(sl.describe(p, ws, LADDER, 2))
    assert "unattended" in text and "500.00" in text and "5 of 40" in text
    assert "35 more clean sessions" in text and "doubles" in text
    assert "1.00%" in text and "fewer than 25 returns: the floor" in text
    assert "fractional units: allowed" in text and "2 " in text


def test_describe_in_ticket_mode_and_with_fractions_not_yet_proven():
    p = pos(run(Days(), ["clean"] * 3))
    text = lines_ok(sl.describe(p, sl.working_stop_pct([], 2.0), LADDER, 0))
    assert "ticket" in text and "3 of 20" in text and "17 more clean sessions" in text
    assert "fractional units: not allowed" in text and "paper" in text


def test_describe_when_the_file_says_no_fractions_or_no_unattended():
    p = pos(run(Days(), ["clean"] * 25))
    no = sl.LadderFile("Owner Name", 500.0, False, False, LADDER.valid_until, LADDER.digest)
    text = lines_ok(sl.describe(p, sl.working_stop_pct([], 2.0), no, 5))
    assert "fractional units: not allowed" in text and "fractional: false" in text
    assert "unattended: false" in text and "ticket" in text


def test_describe_when_paused_and_after_a_change():
    days = Days()
    facts = run(days, ["clean"] * 20 + ["fault"])
    d = days()
    p = pos(facts + [F("pause", d)])
    lines = sl.describe(p, sl.working_stop_pct([], 2.0), LADDER, 1)
    text = lines_ok(lines)
    assert "paused since " + d.isoformat() in text and sl.RESET_COMMAND in text                 # paused
    assert f"last change: {d.isoformat()}: the 25% drawdown pause" in text                     # after a change
    q = pos(facts)
    assert any(line.startswith(f"last change: {facts[-1].day.isoformat()}: a fault session")
               for line in sl.describe(q, None, LADDER, 1))


def test_a3_describe_says_paused_since_when_and_that_only_the_owners_reset_clears_it():
    days = Days()
    facts = run(days, ["clean"] * 60)
    d = days()
    p = pos(facts + [F("pause", d)] + run(days, ["clean"] * 5))
    lines = sl.describe(p, sl.working_stop_pct([], 2.0), LADDER, 1)
    text = lines_ok(lines)
    assert any(line.startswith("paused: ") for line in lines)
    assert f"paused since {d.isoformat()}" in text
    assert "only the owner's reset clears it" in text and sl.RESET_COMMAND in text
    assert "paused" not in lines_ok(sl.describe(pos(facts), None, LADDER, 1))


def test_describe_with_no_ladder_file():
    text = lines_ok(sl.describe(None, sl.working_stop_pct([], 2.0), None, 0))
    assert "config/ladder.json" in text and "as it does today" in text and "1.00%" in text


def test_describe_refuses_a_count_that_is_not_a_whole_number():
    for bad in (True, -1, 1.0, None):
        with pytest.raises(ValueError):
            sl.describe(None, None, None, bad)


# ------------------------------------------------------------------ addendum 3: the blind check's findings
def test_f1_a_fault_then_a_pause_the_same_day_halves_then_the_reset_steps_down():
    days = Days()
    facts = run(days, ["clean"] * 120, ceiling=1e6)                            # unattended at 400
    assert pos(facts, 100, 1e6).earned == 400.0
    d = days()
    same_day = facts + [F("fault", d), F("pause", d), F("reset", days())]
    assert shape(pos(same_day, 100, 1e6)) == ("unattended", 100.0, 0, 40, False)     # 400, 200, then 100
    earlier = facts + [F("fault", d), F("pause", days()), F("reset", days())]
    assert shape(pos(earlier, 100, 1e6)) == shape(pos(same_day, 100, 1e6))
    journal = [summ(x) for x in sessions_from(FIRST, 120)]                     # the same through session_facts
    fd = sessions_from(FIRST, 122)[120]
    journal += [stop(fd, "reconcile", timestamp=f"{fd}T14:00:00+00:00"), pause(fd, ts="15:00:00"),
                reset(sessions_from(FIRST, 122)[121])]
    assert shape(sl.position(sl.session_facts(journal, LIVE, CAL), 100, 1e6)) == ("unattended", 100.0, 0, 40, False)


def test_f1_a_reset_in_the_morning_and_a_fault_in_the_afternoon_of_one_day():
    # T34b-1 R1: the fault takes its place in real time. Paused at 400 on a first stake of 100.
    days = Days()
    facts = run(days, ["clean"] * 120) + [F("pause", days())]
    d = days()
    nine, three = (datetime(d.year, d.month, d.day, h, tzinfo=timezone.utc) for h in (9, 15))
    fault = sl.SessionFact(d, "fault", ("r",), False, BIG, fault_times=(three,))
    p = pos(facts + [sl.SessionFact(d, "reset", ("r",), at=nine), fault], 100, 1e6)     # the reset, then the fault
    assert shape(p) == ("unattended", 100.0, 0, 60, False)                              # 400, 200 (needs 60), 100
    fault = replace(fault, fault_times=(nine,))
    p = pos(facts + [sl.SessionFact(d, "reset", ("r",), at=three), fault], 100, 1e6)    # the fault, then the reset
    assert shape(p) == ("unattended", 100.0, 0, 40, False)                              # 400, 200, 100 (needs 40)
    p = pos(facts + [F("reset", d), F("fault", d)], 100, 1e6)                  # no times: the same stake either way,
    assert shape(p) == ("unattended", 100.0, 0, 60, False)                     # so the larger needed is kept


def test_f1_a_fault_wholly_inside_a_pause_and_two_fault_days_inside_one_pause():
    days = Days()
    facts = run(days, ["clean"] * 120) + [F("pause", days())] + run(days, ["clean"] * 5)
    one = facts + run(days, ["fault"])
    assert shape(pos(one, 100, 1e6)) == ("unattended", 200.0, 0, 60, True)
    two = one + run(days, ["clean", "fault"])
    assert shape(pos(two, 100, 1e6)) == ("unattended", 100.0, 0, 60, True)
    assert shape(pos(two + [F("reset", days())], 100, 1e6)) == ("ticket", 100.0, 0, 20, False)


# ------------------------------------------------------------------ T34b-1 R1 and R6 (a): the fault in real time
# The single-order reference below was written from the ticket (T34b-1 R1, R6) and the design note (D4) BEFORE
# `_walk_all` was read again, and does not use the module. A day is (ceilings, session, fault_times, events):
# `ceilings` the usable ceilings of the day's summaries, `session` None, "clean", "skipped" or "fault", `fault_times`
# one entry per faulty run (seconds after New York midnight; None when unknown), `events` [(instant or None, "pause"
# or "reset")]. Within a day, for one given order: the ceiling clamp; a clean or skipped session before the events; a
# fault halving once, after the first `place` events. (Addendum 1's part-by-part kept end, `ref_compose`, and the
# helpers built on it were removed with addendum 2.)
def ref_day(state, first, ceilings, session, kinds_, place):
    """One day from `state` = (mode, earned, clean, needed, paused); `kinds_` the day's events in the order applied,
    and for a fault day `place` how many of them come before the fault."""
    mode, earned, clean, needed, paused = state
    cap = min(ceilings) if ceilings else None
    if session is not None and cap is not None:
        earned = min(earned, cap)

    def apply(kind):
        nonlocal mode, earned, clean, needed, paused
        if kind == "pause":
            paused = True
            return
        if not paused:                                   # a reset with no pause before it changes nothing
            return
        paused, clean = False, 0
        if mode == "unattended":
            if earned > first:
                earned = max(first, earned / 2)
                needed = 60 if earned > first else 40
            else:
                mode, needed = "ticket", 20

    if session == "fault":
        for kind in kinds_[:place]:
            apply(kind)
        earned, clean = earned / 2, 0
        for kind in kinds_[place:]:
            apply(kind)
    else:
        if session == "clean" and not paused and "pause" not in kinds_:
            clean += 1
            if clean == needed:
                clean = 0
                if mode == "ticket":
                    mode, needed = "unattended", 40
                else:
                    if cap is not None:
                        earned = min(2 * earned, cap)
                    needed = 60
        for kind in kinds_:
            apply(kind)
    return mode, earned, clean, needed, paused


def possible_places(fault_times, ordered):
    """The places of the fault among `ordered` that the times allow: for one instant (R1 (4)) no event before it known
    later, none after it known earlier, an equal or unknown time on either side; A2: the union over the day's faulty
    runs, an unknown time (or no time at all) allowing every place."""
    def one(t0):
        return {p for p in range(len(ordered) + 1)
                if t0 is None or (all(t is None or t <= t0 for t, _ in ordered[:p])
                                  and all(t is None or t >= t0 for t, _ in ordered[p:]))}
    if not fault_times:
        return list(range(len(ordered) + 1))
    return sorted(set().union(*(one(t) for t in fault_times)))


# ------------------------------------------------------------------ T34b-1 addendum 2: the oracle
# Written from addendum 2 (D-A to D-D) BEFORE `_walk_all` was changed for it, and independent of the module. The
# single-order walk is `ref_day` (today's rules for one given order of a day); the brute force below lists every
# possible order of every day and walks every combination of them, giving the set of states at the end of each day.
def oracle_day_orders(session, fault_times, events):
    """D-A: the distinct possible orders of one day, as (kinds of the events in order, place of the fault): every
    arrangement of `events` [(instant or None, kind)] in which each pair with known DIFFERENT instants keeps its real
    order, on a fault day combined with every place A2 allows in that arrangement (the union over the faulty runs)."""
    out = set()
    for perm in itertools.permutations(range(len(events))):
        seq = [events[i] for i in perm]
        if any(a[0] is not None and b[0] is not None and a[0] > b[0] for i, a in enumerate(seq) for b in seq[i + 1:]):
            continue
        kinds_ = tuple(k for _, k in seq)
        if session == "fault" and seq:
            out.update((kinds_, p) for p in possible_places(fault_times, seq))
        else:
            out.add((kinds_, 0))
    return sorted(out)


def oracle_combinations(truth):
    days = sorted(truth)
    n = 1
    for d in days:
        n *= len(oracle_day_orders(truth[d][1], truth[d][2], truth[d][3]))
    return n


def oracle_sets(truth, first):
    """Brute force: every combination of every day's possible orders, each walked from the start with `ref_day`; the
    set of states at the end of each day over all combinations. Days in day order; [(day, set of states)]."""
    days = sorted(truth)
    orders = [oracle_day_orders(truth[d][1], truth[d][2], truth[d][3]) for d in days]
    sets = [set() for _ in days]

    def go(i, state):                                    # one combination per path; a prefix is shared, not merged
        if i == len(days):
            return
        ceilings, session, _, _ = truth[days[i]]
        for kinds_, place in orders[i]:
            end = ref_day(state, first, ceilings, session, list(kinds_), place)
            sets[i].add(end)
            go(i + 1, end)
    go(0, ("ticket", first, 0, 20, False))
    return list(zip(days, sets))


def oracle_report(states):
    """D-C, part by part over a set of states: (mode, smallest earned, clean, needed, paused, how many)."""
    mode = "ticket" if any(s[0] == "ticket" for s in states) else "unattended"
    slow = max((s for s in states if s[0] == mode), key=lambda s: (s[3] - s[2], s[3], -s[2]))
    return mode, min(s[1] for s in states), slow[2], slow[3], any(s[4] for s in states), len(states)


def oracle_set_walk(truth, first):
    """The same sets by propagation: each day, every state of the set walked through every possible order of the day
    with `ref_day`, the distinct ends kept (sound because a day's end depends only on its start and its order; checked
    against the brute force `oracle_sets` in the oracle property). For histories too long to brute-force."""
    states, out = {("ticket", first, 0, 20, False)}, []
    for d in sorted(truth):
        ceilings, session, fault_times, events = truth[d]
        orders = oracle_day_orders(session, fault_times, events)
        states = {ref_day(s, first, ceilings, session, list(k), q) for s in states for k, q in orders}
        out.append((d, states))
    return out


T_FIRSTS = [100.0, 500.0, 0.1, 333.33, 77.7]
OFFSETS = [0, 0, -300, -240, 60, 330]                # minutes: one instant written in other offsets
OTHER_DATE = object()                               # a usable instant, but on the New York date before the entry's day
UNKNOWN_STAMPS = [OMIT, None, 12345, "", "2025-01-06T09:00:00", "09:00:00", "not a time", "2025-01-06", OTHER_DATE]


def _stamp(d, sec, offset):
    """`sec` seconds after New York midnight of `d` (kept under 23 hours, so on `d` in New York), written in UTC
    offset `offset` (minutes)."""
    t = datetime(d.year, d.month, d.day, tzinfo=NY) + timedelta(seconds=sec)
    return t.astimezone(timezone(timedelta(minutes=offset))).isoformat(timespec="seconds")


def _with_time(rng, e, d, sec, mode):
    e.pop("timestamp", None)
    if sec is None:
        bad = rng.choice(UNKNOWN_STAMPS)
        if bad is OTHER_DATE:
            e["timestamp"] = _stamp(d - timedelta(days=1), rng.randrange(60, 82800), 0)
        elif bad is not OMIT:
            e["timestamp"] = bad
    else:
        e["timestamp"] = _stamp(d, sec, rng.choice(OFFSETS) if mode == "distinct" else 0)
    return e


def timed_journal(rng, n_days, mode):
    """(blocks, truth, first, cap): a live journal over `n_days` calendar days from FIRST (a block of entries per day,
    shuffled), with several faults, pauses and resets, many on shared days. mode "distinct": every time known and no
    two equal within a day, written in mixed UTC offsets (so a text comparison misorders them); "ties": times from
    two instants, some unknown (absent, not text, no offset); "apart": no day holds both a fault and an event, every
    time UTC and well formed (the earlier walk's domain). truth[day] = (ceilings, session, fault_times, events). A
    history may open with a run of clean sessions (none, 25, 65 or 130; "ties": 65, 130 or 250, with fewer faults between
    the event days), so that many of its fault days fall while unattended above the first stake, where the order of a
    fault and a reset matters. Every usable time falls on its entry's own New York date (addendum 2, D-F); an unknown
    time may also be a usable instant on another New York date. mode "mixed" (addendum 2): "distinct", but for one to
    four days after the opening run, which hold one to three events and often a fault of one to three runs, with times
    as in "ties" (the days whose order the times cannot tell, few enough to walk every combination)."""
    first = rng.choice(T_FIRSTS)
    ties = mode == "ties"
    mixed = mode == "mixed"
    warm = rng.choice([65, 130, 250] if ties else [0, 25, 65] if mixed else [0, 25, 65, 130])
    cap = first * rng.choice([1, 2, 8, 1000])
    fault_rate = rng.choice([0.0, 0.005, 0.04] if ties else [0.0, 0.005, 0.04, 0.1, 0.2])
    ev_rate = rng.choice([0.04, 0.1, 0.25, 0.4])
    if ties or mixed:
        n_days += warm * 7 // 5                          # the opening run comes on top of the `n_days` asked for
    lo = warm * 7 // 5 + 2
    undecided = set(rng.sample(range(lo, n_days), rng.randint(1, 4))) if mixed else set()
    moving = rng.random() < 0.5
    c = first * rng.choice([1, 2, 4, 64])
    blocks, truth, batch, open_ = [], {}, 0, False
    d = FIRST
    for index in range(n_days):
        day_mode = "ties" if index in undecided else "distinct" if mixed else mode
        if moving and rng.random() < 0.04:
            c = max(first / 8, min(first * 256, c * rng.choice([0.5, 0.9, 2, 4])))
        day_cap = c if moving else BIG
        secs = rng.sample(range(60, 82800), 12)

        def when():
            if day_mode == "distinct":
                return secs.pop()
            if day_mode == "ties":
                return None if rng.random() < 0.25 else rng.choice([9, 15]) * 3600
            return rng.randrange(60, 82800)

        block, runs, events, warming = [], [], [], bool(warm)
        with_events = not warming and (index in undecided or (not mixed and rng.random() < ev_rate)
                                       or (mixed and rng.random() < ev_rate / 4))
        rate = 0.5 if index in undecided else 0.35 if with_events and mode != "apart" else fault_rate
        if warming and d.weekday() < 5:
            warm -= 1
            batch += 1
            sec = when()
            runs.append(("clean", sec))
            block.append(_with_time(rng, summ(d, batch=f"b{batch}", ceiling=day_cap), d, sec, day_mode))
        elif d.weekday() < 5 and rng.random() < 0.88:
            for _ in range(rng.randint(1, 3)):
                batch += 1
                x = rng.random()
                kind = "fault" if x < rate else "skipped" if x < rate + 0.08 else "clean"
                if kind == "fault":
                    e = (stop(d, rng.choice(FAULTS), batch=f"b{batch}", ceiling=day_cap) if rng.random() < 0.5 else
                         summ(d, batch=f"b{batch}", ceiling=day_cap, faults=[rng.choice(FAULT_KINDS)]))
                elif kind == "skipped":
                    e = stop(d, rng.choice(SKIPS), batch=f"b{batch}", ceiling=day_cap)
                else:
                    e = summ(d, batch=f"b{batch}", ceiling=day_cap)
                sec = when()
                runs.append((kind, sec))
                block.append(_with_time(rng, e, d, sec, day_mode))
        has_fault = any(k == "fault" for k, _ in runs)
        if with_events and not (mode == "apart" and has_fault):
            for _ in range(rng.choice([1, 2, 2, 3] if index in undecided else [1, 1, 2, 3])):
                if open_:
                    kind = "reset" if rng.random() < 0.7 else "pause"
                else:
                    kind = "pause" if rng.random() < 0.7 else "reset"
                open_ = kind == "pause"
                e = ({"type": "drawdown_pause", "venue": LIVE, "day": d.isoformat()} if kind == "pause" else
                     {"type": "latch_reset", "venue": LIVE, "day": d.isoformat(), "who": "Owner Name"})
                sec = when()
                events.append((sec, kind))
                block.append(_with_time(rng, e, d, sec, day_mode))
        if runs or events:
            session = None if not runs else "fault" if has_fault else \
                "clean" if any(k == "clean" for k, _ in runs) else "skipped"
            faulty = [s for k, s in runs if k == "fault"]
            fault_times = tuple(sorted(t for t in faulty if t is not None)) + tuple(t for t in faulty if t is None)
            truth[d] = ([day_cap] if runs else [], session, fault_times, events)
        rng.shuffle(block)
        blocks.append(block)
        d += timedelta(days=1)
    return blocks, truth, first, cap


F1_HISTORIES = 2000


def test_property_f1_every_fault_halves_and_a_fault_inside_a_pause_never_gives_more_than_one_moved_before_it():
    """R6 (a): histories with several faults placed before, on and inside pauses and on reset days on both sides of
    the reset by time; every time known and distinct: the walk's set equals the independent reference's at every day
    (one faulty run: the real order; runs on both sides of an event: A2's union of places, so several positions), and
    the report is its part-by-part reading (addendum 2)."""
    rng = random.Random(SEED + 7)
    days_checked = moved = several = single = spread = 0
    seen = dict.fromkeys(("day before a pause", "pause's day", "inside a pause", "reset's day, before it",
                          "reset's day, after it", "day after a reset"), 0)
    for _ in range(F1_HISTORIES):
        blocks, truth, first, cap = timed_journal(rng, rng.randint(40, 160), "distinct")
        facts = sl.session_facts(flat(blocks), LIVE, CAL)
        assert facts.unreadable == 0
        facts = list(facts)
        _, walk, sets = sl._walk_sets(facts, first, cap)
        days = sorted(truth)
        want = oracle_set_walk(truth, first)
        assert [(d, set(x)) for d, x in sets] == want
        assert [(d, report(p)) for d, p in walk] == [(d, oracle_report(x)) for d, x in want]
        assert all(p.stake == min(p.earned, cap) for _, p in walk)
        days_checked += len(walk)
        several += sum(1 for d in days if truth[d][1] == "fault") >= 2
        prev = {("ticket", first, 0, 20, False)}
        for i, (d, p) in enumerate(walk):
            ceilings, session, fault_times, events = truth[d]
            ordered = sorted(events, key=lambda ev: ev[0])
            places = possible_places(fault_times, ordered) if session == "fault" and events else [0]
            if session == "fault" and events:
                single += len(fault_times) == 1
                spread += len(places) > 1
                if len(fault_times) == 1:
                    assert len(places) == 1                                    # one known instant: the real order
                    assert p.possible == len(prev)
            if session == "fault":                       # every fault halves and zeroes the count, on any day
                assert p.clean == 0
                if not events:
                    without = [ref_day(x, first, ceilings, "skipped", [], 0) for x in prev]
                    assert p.earned == min(w[1] for w in without) / 2
                kinds_here = {k for _, k in events}
                if "pause" in kinds_here:
                    seen["pause's day"] += 1
                if any(x[4] for x in prev) and not events:
                    seen["inside a pause"] += 1
                for t, k in events:
                    if k == "reset":
                        seen["reset's day, before it"] += any(x < t for x in fault_times)
                        seen["reset's day, after it"] += any(x > t for x in fault_times)
                nxt = walk[i + 1][0] if i + 1 < len(walk) else None
                if nxt is not None and any(k == "pause" for _, k in truth[nxt][3]) and not events:
                    seen["day before a pause"] += 1
                if i and any(k == "reset" for _, k in truth[walk[i - 1][0]][3]) and not events:
                    seen["day after a reset"] += 1
            if all(x[4] for x in prev) or any(k == "pause" for _, k in events):  # paused: no clean session counts
                before = oracle_report(prev)
                assert p.earned <= before[1]
                if len(prev) == 1 and p.possible == 1:
                    assert p.clean <= before[2]
            prev = set(sets[i][1])
        # a fault on a day inside a pause (or holding one), moved to a free day just before that pause began
        paused, start = False, None
        for i, f in enumerate(facts):
            if f.kind == "pause" and not paused:
                paused, start = True, f.day
            if f.kind == "reset" and paused:
                paused = False
            pause_today = any(g.kind == "pause" and g.day == f.day for g in facts)
            if f.kind == "fault" and (paused or pause_today):
                begun = f.day if pause_today and not paused else start
                before_start = [g.day for g in facts if g.day < begun]
                free = begun - timedelta(days=1)
                if before_start and free <= max(before_start):
                    break
                other = facts[:i] + facts[i + 1:]
                shifted = sorted(other + [sl.SessionFact(free, "fault", f.reasons, False, f.ceiling)],
                                 key=lambda g: g.day)
                b = sl._walk(shifted, first, cap)
                for d, pa in walk:
                    if d >= f.day:
                        pb = [q for e_, q in b if e_ <= d][-1]
                        assert pa.stake <= pb.stake
                moved += 1
                break
    print(f"property (F1, T34b-1): {F1_HISTORIES} histories, {days_checked} days against the reference, "
          f"{several} with several faults, {single} fault-and-event days with one faulty run, {spread} with runs on "
          f"both sides of an event, {moved} faults moved, cases {seen}")
    assert days_checked > 100_000 and several > F1_HISTORIES // 2 and moved > 100 and single > 2000 and spread > 500
    assert all(n >= 50 for n in seen.values()), seen


def test_f2_day_stopped_reads_a_stop_on_a_stopped_run_too():
    entries = [summ(TODAY), stop(TODAY, "prices", batch="b2", day_stop={"pct": 2.1, "limit": 2.0, "wall": True})]
    why = sl.day_stopped(entries, LIVE, TODAY)
    assert why and "2.10%" in why and "daily halt" in why


def test_f3_a_fractional_fill_counts_only_from_a_clean_done_batch(monkeypatch):
    def proven(**extra):
        return sl.fractional_proven([summ(TODAY, venue=PAPER, batch="b1", **extra), fill(0.5)], PAPER)
    assert proven() == 1
    assert proven(faults=["reconcile"]) == 0
    assert proven(faults=["drift"]) == 1
    assert proven(faults="reconcile") == 0 and proven(faults=None) == 0 and proven(faults={"cap": 1}) == 0
    monkeypatch.setattr(sl, "DRIFT_ACTS", True)
    assert proven(faults=["drift"]) == 0


def test_f5_a_pause_that_names_no_venue_pauses_and_is_counted():
    for venue in (OMIT, None, "", 7, ["webull-production"]):
        e = pause(TODAY)
        if venue is OMIT:
            del e["venue"]
        else:
            e["venue"] = venue
        facts = sl.session_facts([summ(TODAY), e], LIVE, CAL)
        assert kinds(facts) == [(TODAY, "clean"), (TODAY, "pause")] and facts.unreadable == 1
        r = reset(TODAY)
        if venue is OMIT:
            del r["venue"]
        else:
            r["venue"] = venue
        facts = sl.session_facts([pause(TODAY), r], LIVE, CAL)
        assert kinds(facts) == [(TODAY, "pause")] and facts.unreadable == 1
    assert sl.session_facts([pause(TODAY, venue=PAPER)], LIVE, CAL).unreadable == 0       # another venue's: not ours


WEEK_DATE = "2026-W47-1"                         # Monday 2026-11-16 as a week date; also 10 characters


@pytest.mark.parametrize("day", [WEEK_DATE, "2026-11-16T09", "20261116", "2026-11-16 ", " 2026-11-16", "2026-1-16",
                                 "\uff12026-11-16"])
def test_f6_a_day_is_usable_only_as_strict_yyyy_mm_dd(day):
    assert date.fromisoformat("2026-W47-1") == TODAY                      # what Python's own parser would accept
    for e in (summ(day), pause(day), reset(day)):
        e["day"] = day
        facts = sl.session_facts([e], LIVE, CAL)
        assert list(facts) == [] and facts.unreadable == 1
    stopped = summ(TODAY, day_stop={"pct": 3, "limit": 2, "wall": True})
    stopped["day"] = day
    assert sl.day_stopped([stopped], LIVE, TODAY) is None
    good = summ(TODAY, venue=PAPER)
    bad_fill = {**fill(0.5), "day": day}
    bad_summary = {**summ(TODAY, venue=PAPER), "day": day}
    assert sl.fractional_proven([good, bad_fill], PAPER) == 0
    assert sl.fractional_proven([bad_summary, fill(0.5)], PAPER) == 0
    assert sl.fractional_proven([good, fill(0.5)], PAPER) == 1


def test_f7_describe_says_no_rise_under_todays_ceiling_and_prints_small_amounts_fully():
    p = pos(run(Days(), ["clean"] * 61), 500, 800)                          # earned 1000, held at 800
    text = lines_ok(sl.describe(p, None, LADDER, 0))
    assert "doubles from" not in text
    assert "no rise is possible under today's ceiling" in text and "1,000.00" in text
    q = pos(run(Days(), ["clean"] * 61), 500, 1e6)
    assert "doubles from 1,000.00" in lines_ok(sl.describe(q, None, LADDER, 0))
    assert sl._amount(0.075) == "0.075" and sl._amount(2.675) == "2.68" and sl._amount(0.125) == "0.125"
    assert sl._amount(1234.565) == "1,234.57" and sl._amount(1 / 3) == "0.333333" and sl._amount(0.0) == "0.00"
    tiny = pos(run(Days(), ["fault"] * 3), 0.6, 1e6)                       # 0.075
    assert "stake at work: 0.075" in lines_ok(sl.describe(tiny, None, LADDER, 0))


# ------------------------------------------------------------------ T34b-1: the six notes of the last blind check
def _utc(d, hh, mm=0):
    return f"{d.isoformat()}T{hh:02d}:{mm:02d}:00+00:00"


CASE_DAYS = sessions_from(FIRST, 62)


def the_case(reset_ts=OMIT, fault_ts=OMIT, more=()):
    """First stake 100: 60 clean sessions (unattended at an earned 200), a pause, then one day with the owner's reset
    and a faulty run, stamped `reset_ts` and `fault_ts` (OMIT: no timestamp at all). The ceiling never bites."""
    journal = [summ(x, ceiling=BIG) for x in CASE_DAYS[:60]] + [pause(CASE_DAYS[60])]
    r, f = reset(CASE_DAYS[61]), stop(CASE_DAYS[61], "reconcile", ceiling=BIG)
    for e, ts in ((r, reset_ts), (f, fault_ts)):
        e.pop("timestamp", None)
        if ts is not OMIT:
            e["timestamp"] = ts
    facts = sl.session_facts(journal + [f, r, *more], LIVE, CAL)
    return facts, sl.position(facts, 100, 1e6)


def untold(p):
    """Addendum 2, D-D: the one undated line naming the days whose order the times do not tell, or None."""
    lines = [words for d, words in p.changes if d is None and words.startswith(sl._UNTOLD_START)]
    assert len(lines) <= 1
    return lines[0] if lines else None


def untold_words(days, n):
    """D-D's fixed words, written here from the addendum (not from the module's template)."""
    named = ", ".join(d.isoformat() for d in days[:5]) + (f" and {len(days) - 5} more" if len(days) > 5 else "")
    return (f"the times do not tell the order of the events on {named}: {n} position{' is' if n == 1 else 's are'} "
            "possible; what is shown is the lowest stake, ticket mode where any of them has it and the pause where any "
            "of them has it")


def report(p):
    return (p.mode, p.earned, p.clean, p.needed, p.paused, p.possible)


CASE_TWO = {("unattended", 50.0, 0, 40, False), ("ticket", 100.0, 0, 20, False)}


def test_d_the_case_that_must_hold():
    """Addendum 2, THE CASE, lines 1 to 3: first stake 100, unattended at an earned 200, paused; one day with the
    owner's reset and a faulty run."""
    d = CASE_DAYS[61]
    head = sl.position(sl.session_facts([summ(x, ceiling=BIG) for x in CASE_DAYS[:60]] + [pause(CASE_DAYS[60])],
                                        LIVE, CAL), 100, 1e6)
    assert shape(head) == ("unattended", 200.0, 0, 60, True) and head.earned == 200.0
    facts, p = the_case(_utc(d, 9), _utc(d, 15))                     # 1. the reset at 09:00, the fault at 15:00
    assert report(p) == ("unattended", 50.0, 0, 40, False, 1) and untold(p) is None
    assert sl._walk_sets(facts, 100, 1e6)[2][-1] == (d, (("unattended", 50.0, 0, 40, False),))
    _, p = the_case(_utc(d, 15), _utc(d, 9))                         # 2. the fault at 09:00, the reset at 15:00
    assert report(p) == ("ticket", 100.0, 0, 20, False, 1) and untold(p) is None
    later = [summ(x, ceiling=BIG) for x in sessions_from(d + timedelta(days=1), 40)]
    for reset_ts, fault_ts in ((_utc(d, 12), _utc(d, 12)),           # 3. equal instants
                               (_utc(d, 9), OMIT), (_utc(d, 15), OMIT),       # the fault's time unknown
                               (OMIT, _utc(d, 9)), (OMIT, _utc(d, 15)),       # the reset's time unknown
                               (OMIT, OMIT)):
        facts, p = the_case(reset_ts, fault_ts)
        assert report(p) == ("ticket", 50.0, 0, 20, False, 2) and p.stake == 50.0, (reset_ts, fault_ts)
        assert set(sl._walk_sets(facts, 100, 1e6)[2][-1][1]) == CASE_TWO
        assert untold(p) == untold_words([d], 2) and not any(x == d for x, _ in p.changes)    # no dated line on d
        facts, _ = the_case(reset_ts, fault_ts, more=later)
        walk = dict(sl._walk(facts, 100, 1e6))
        xs = sessions_from(d + timedelta(days=1), 40)
        assert report(walk[xs[19]]) == ("unattended", 50.0, 0, 40, False, 2)     # twenty later: no member in ticket
        assert report(walk[xs[39]]) == ("unattended", 100.0, 0, 60, False, 2)    # twenty more
        assert set(sl._walk_sets(facts, 100, 1e6)[2][-1][1]) == {("unattended", 100.0, 20, 40, False),
                                                                 ("unattended", 100.0, 0, 60, False)}
    # A2: faulty runs at 09:00 and 15:00 around a reset at 12:00: both places possible
    _, p = the_case(_utc(d, 12), _utc(d, 9), more=[stop(d, "send", ceiling=BIG, batch="b9", timestamp=_utc(d, 15))])
    assert report(p) == ("ticket", 50.0, 0, 20, False, 2) and untold(p) is not None
    _, p = the_case(_utc(d, 12), _utc(d, 13), more=[stop(d, "send", ceiling=BIG, batch="b9", timestamp=_utc(d, 15))])
    assert report(p) == ("unattended", 50.0, 0, 40, False, 1) and untold(p) is None  # both after the reset: one place


def test_d_v1_a_pause_and_a_reset_at_the_same_second_report_the_stepped_down_stake_and_the_pause():
    """Addendum 2, THE CASE, line 4 (the check's V1): 120 clean sessions from 100 (unattended at 400), then a pause
    and a reset at the same second. Pause then reset: 200, not paused; reset then pause: 400, paused. The report: the
    stepped-down 200, paused."""
    xs = sessions_from(FIRST, 121)
    journal = [summ(x, ceiling=BIG) for x in xs[:120]]
    same = [pause(xs[120], ts="16:00:00"), reset(xs[120], ts="16:00:00")]
    facts = sl.session_facts(journal + same, LIVE, CAL)
    p = sl.position(facts, 100, 1e6)
    assert report(p) == ("unattended", 200.0, 0, 60, True, 2) and p.stake == 200.0
    assert set(sl._walk_sets(facts, 100, 1e6)[2][-1][1]) == {("unattended", 200.0, 0, 60, False),
                                                             ("unattended", 400.0, 0, 60, True)}
    for ts_p, ts_r, want in (("15:59:59", "16:00:00", ("unattended", 200.0, 0, 60, False, 1)),
                             ("16:00:00", "15:59:59", ("unattended", 400.0, 0, 60, True, 1))):
        q = sl.position(sl.session_facts(journal + [pause(xs[120], ts=ts_p), reset(xs[120], ts=ts_r)], LIVE, CAL),
                        100, 1e6)
        assert report(q) == want
        assert q.earned >= p.earned and (not q.paused or p.paused) and (q.mode != "ticket" or p.mode == "ticket")
    lines = sl.describe(p, None, LADDER, 0)
    assert "paused: the 25% drawdown pause (in at least one of the positions the record allows)" in lines_ok(lines)
    [nxt] = [line for line in lines if line.startswith("next: ")]
    assert "400" not in nxt and "200" not in nxt                         # no stake the reported position does not hold
    assert "stake at work: 200.00" in lines


def test_d_v2_the_check_p3_min2_the_report_stays_at_or_below_both_concrete_orders_on_every_later_day():
    """Addendum 2, THE CASE, line 5: the check's `p3_min2.py`, rebuilt. 120 clean sessions from 100, a pause, then a
    day with a faulty run and the owner's reset (equal, or one second apart either way), then 69 clean sessions, the
    40th of them under a ceiling of 60 (or no ceiling). On every day from the fault day on, the reported earned stake
    is at or below both concrete orders', ticket whenever one is, and paused whenever one is."""
    def nxt(x):
        x += timedelta(days=1)
        while x.weekday() >= 5:
            x += timedelta(days=1)
        return x

    def clean(x, ceiling=1e4):
        e = {"type": "order_step", "event": "summary", "venue": LIVE, "status": "done", "day": x.isoformat(),
             "timestamp": x.isoformat() + "T15:00:00+00:00"}
        if ceiling is not None:
            e["ceiling"] = ceiling
        return e

    checked = 0
    for variant in (None, 60.0):
        x, pre = date(2026, 1, 4), []
        for _ in range(120):
            x = nxt(x)
            pre.append(clean(x))
        x = nxt(x)
        pre.append({"type": "drawdown_pause", "venue": LIVE, "day": x.isoformat(),
                    "timestamp": x.isoformat() + "T16:00:00+00:00"})
        fd = nxt(x)

        def fault_day(ft, rt):
            return [{"type": "order_step", "event": "summary", "venue": LIVE, "status": "stopped", "stage": "send",
                     "day": fd.isoformat(), "ceiling": 1e4, "timestamp": fd.isoformat() + ft},
                    {"type": "latch_reset", "venue": LIVE, "who": "Owner", "day": fd.isoformat(),
                     "timestamp": fd.isoformat() + rt}]
        post, y = [], fd
        for k in range(1, 70):
            y = nxt(y)
            post.append(clean(y, variant if k == 40 else 1e4))
        both = sl._walk(sl.session_facts(pre + fault_day("T10:00:00+00:00", "T10:00:00+00:00") + post, LIVE, CAL),
                        100.0, 1e9)
        a = sl._walk(sl.session_facts(pre + fault_day("T10:00:01+00:00", "T10:00:00+00:00") + post, LIVE, CAL),
                     100.0, 1e9)
        b = sl._walk(sl.session_facts(pre + fault_day("T09:59:59+00:00", "T10:00:00+00:00") + post, LIVE, CAL),
                     100.0, 1e9)
        assert [d for d, _ in both] == [d for d, _ in a] == [d for d, _ in b]
        later = [(d, p, q, r) for (d, p), (_, q), (_, r) in zip(both, a, b) if d >= fd]
        assert len(later) == 70
        for d, p, q, r in later:
            for c in (q, r):
                assert c.possible == 1
                assert p.earned <= c.earned and p.stake <= c.stake
                assert (c.mode != "ticket" or p.mode == "ticket") and (not c.paused or p.paused)
            checked += 1
        assert any(q.earned != r.earned for _, _, q, r in later)          # the two orders do part
    assert checked == 140


def test_r1_times_are_instants_not_text_and_a_time_needs_its_offset():
    d = CASE_DAYS[61]
    # 10:00 at -05:00 is 15:00 UTC, after a fault at 14:00 UTC (as text it would come first)
    _, p = the_case(f"{d}T10:00:00-05:00", _utc(d, 14))
    assert report(p) == ("ticket", 100.0, 0, 20, False, 1) and untold(p) is None
    _, p = the_case(_utc(d, 14), f"{d}T10:00:00-05:00")
    assert report(p) == ("unattended", 50.0, 0, 40, False, 1) and untold(p) is None
    # a fault time with no offset (or not a datetime) is unknown: both orders are possible (addendum 2: two positions)
    for bad in (f"{d}T08:00:00", f"{d}", "08:00:00", "", 20250101, None, f"{d}T08:00:00+25:00", f"{d}T08:00:00 UTC"):
        facts, p = the_case(_utc(d, 9), bad)
        assert facts[-2].kind == "fault" and facts[-2].at is None and facts[-2].fault_times == (None,)
        assert report(p) == ("ticket", 50.0, 0, 20, False, 2) and untold(p) is not None, bad
    # "Z" is an offset: read
    facts, p = the_case(f"{d}T09:00:00Z", f"{d}T08:00:00Z")
    assert facts[-1].at == datetime(d.year, d.month, d.day, 9, tzinfo=timezone.utc)
    assert report(p) == ("ticket", 100.0, 0, 20, False, 1)


def test_d_f_a_time_on_another_new_york_date_is_an_unknown_time():
    d = CASE_DAYS[61]                                                 # in March 2025: New York is UTC-4 or UTC-5
    prev_ny = f"{d}T03:00:00+00:00"                                   # the evening before, in New York
    next_ny = f"{d + timedelta(days=1)}T08:00:00+00:00"               # the next New York day
    for reset_ts, fault_ts in ((prev_ny, _utc(d, 15)), (_utc(d, 9), next_ny), (f"{d}T23:30:00-05:00", _utc(d, 15)),
                               (_utc(d, 9), f"{d - timedelta(days=1)}T22:00:00-05:00")):
        facts, p = the_case(reset_ts, fault_ts)
        reset_fact = [f for f in facts if f.kind == "reset"][0]
        fault_fact = [f for f in facts if f.kind == "fault"][0]
        assert reset_fact.at is None or fault_fact.fault_times == (None,)
        assert report(p) == ("ticket", 50.0, 0, 20, False, 2), (reset_ts, fault_ts)
    # the same instants, on the right New York date, order the day
    _, p = the_case(f"{d}T09:00:00-04:00", f"{d}T15:00:00-04:00")
    assert report(p) == ("unattended", 50.0, 0, 40, False, 1)
    # and the walk reads a hand-built fact the same way: an `at` on another New York date orders nothing
    days = Days()
    facts = run(days, ["clean"] * 120, ceiling=1e6) + [F("pause", days())]
    e = days()
    nine = datetime(e.year, e.month, e.day, 9, tzinfo=NY)
    fault = sl.SessionFact(e, "fault", ("r",), False, BIG, fault_times=(nine.replace(hour=15),))
    on_day = pos(facts + [sl.SessionFact(e, "reset", ("r",), at=nine), fault], 100, 1e6)
    off_day = pos(facts + [sl.SessionFact(e, "reset", ("r",), at=nine - timedelta(days=1)), fault], 100, 1e6)
    assert on_day.possible == 1 and off_day.possible == 2 and off_day.earned <= on_day.earned
    assert sl._instant(f"{e}T09:00:00-05:00", e) is not None and sl._instant(f"{e}T09:00:00-05:00",
                                                                                e + timedelta(days=1)) is None


def test_r1_events_are_listed_by_their_instants_and_a_reset_first_at_the_same_instant():
    entries = [pause(TODAY, ts="14:00:00"), {**reset(TODAY), "timestamp": f"{TODAY}T10:00:00-05:00"}]
    assert [f.kind for f in sl.session_facts(entries, LIVE, CAL)] == ["pause", "reset"]      # 14:00 then 15:00 UTC
    entries = [pause(TODAY, ts="15:00:00"), {**reset(TODAY), "timestamp": f"{TODAY}T10:00:00-05:00"}]
    assert [f.kind for f in sl.session_facts(entries, LIVE, CAL)] == ["reset", "pause"]      # the same instant
    for bad in (f"{TODAY}T01:00:00", "zz", 5):                       # unknown: a reset first, a pause last
        entries = [{**reset(TODAY), "timestamp": bad}, pause(TODAY, ts="05:00:01"), reset(TODAY, ts="05:00:02")]
        assert [f.kind for f in sl.session_facts(entries, LIVE, CAL)] == ["reset", "pause", "reset"]
        entries = [{**pause(TODAY), "timestamp": bad}, reset(TODAY, ts="23:59:59")]
        assert [f.kind for f in sl.session_facts(entries, LIVE, CAL)] == ["reset", "pause"]
    # addendum 2: the listing claims no order; the walk takes both (not "a reset first, a pause last")
    facts = sl.session_facts([pause(TODAY, ts="15:00:00"), {**reset(TODAY), "timestamp": f"{TODAY}T10:00:00-05:00"}],
                             LIVE, CAL)
    p = sl.position(facts, 500, 1e6)                                  # pause-reset: not paused; reset-pause: paused
    assert report(p) == ("ticket", 500.0, 0, 20, True, 2)


def test_r1_the_fact_carries_its_instant():
    nine = datetime(TODAY.year, TODAY.month, TODAY.day, 9, tzinfo=timezone.utc)
    entries = [stop(TODAY, "send", timestamp=_utc(TODAY, 15)), summ(TODAY, faults=["cap"], timestamp=_utc(TODAY, 11)),
               summ(TODAY, timestamp=_utc(TODAY, 8)), pause(TODAY, ts="09:00:00")]
    facts = sl.session_facts(entries, LIVE, CAL)
    assert [(f.kind, f.at) for f in facts] == [("fault", None), ("pause", nine)]
    assert facts[0].fault_times == (nine.replace(hour=11), nine.replace(hour=15)) and facts[1].fault_times == ()
    untimed = sl.session_facts(entries + [stop(TODAY, "after")], LIVE, CAL)          # one faulty run without a time
    assert untimed[0].kind == "fault" and untimed[0].at is None
    assert untimed[0].fault_times == (nine.replace(hour=11), nine.replace(hour=15), None)        # one per run
    again = sl.session_facts([stop(TODAY, "after")] + entries[::-1], LIVE, CAL)
    assert again[0].fault_times == untimed[0].fault_times                           # ascending, None last
    for e in ([summ(TODAY, timestamp=_utc(TODAY, 8))], [stop(TODAY, "prices", timestamp=_utc(TODAY, 8))]):
        f = sl.session_facts(e, LIVE, CAL)[0]                                       # clean, skipped
        assert f.at is None and f.fault_times == ()
    assert sl.SessionFact(TODAY, "clean", ("r",)).at is None and sl.SessionFact(TODAY, "clean", ("r",)).fault_times == ()
    # one faulty run without a time, one at 08:00 and a reset at 09:00: the untimed run widens the places (A2)
    d = CASE_DAYS[61]
    _, p = the_case(_utc(d, 9), _utc(d, 8), more=[stop(d, "send", ceiling=BIG, batch="b9")])
    assert report(p) == ("ticket", 50.0, 0, 20, False, 2) and untold(p) is not None
    _, p = the_case(_utc(d, 9), _utc(d, 8))                                         # 08:00 alone: the real order
    assert report(p) == ("ticket", 100.0, 0, 20, False, 1) and untold(p) is None


def test_r1_the_walk_refuses_a_time_it_cannot_judge_and_events_out_of_their_order():
    nine = datetime(TODAY.year, TODAY.month, TODAY.day, 9, tzinfo=timezone.utc)
    for bad in (_utc(TODAY, 9), datetime(2026, 11, 16, 9), 9.0, TODAY, True):
        for kind in ("pause", "fault", "clean"):
            with pytest.raises(ValueError) as why:
                sl.position([sl.SessionFact(TODAY, kind, ("r",), ceiling=BIG if kind != "pause" else None, at=bad)],
                            500, 1e6)
            assert str(why.value) and "\n" not in str(why.value)
    late = [sl.SessionFact(TODAY, "pause", ("r",), at=nine.replace(hour=15)), sl.SessionFact(TODAY, "reset", ("r",),
                                                                                           at=nine)]
    with pytest.raises(ValueError) as why:
        sl.position(late, 500, 1e6)
    assert "\n" not in str(why.value)
    unknown_between = [late[0], sl.SessionFact(TODAY, "pause", ("r",)), late[1]]
    with pytest.raises(ValueError):
        sl.position(unknown_between, 500, 1e6)
    assert sl.position(late[::-1], 500, 1e6).paused is True                          # in order: walked
    with pytest.raises(ValueError):                                                  # one session a day
        sl.position([F("clean", TODAY), F("fault", TODAY)], 500, 1e6)
    # A2: fault_times a tuple of None or aware datetimes, and only on a fault
    for bad in ([nine], (nine.replace(tzinfo=None),), (_utc(TODAY, 9),), (nine, 9), None, nine):
        with pytest.raises(ValueError) as why:
            sl.position([sl.SessionFact(TODAY, "fault", ("r",), ceiling=BIG, fault_times=bad)], 500, 1e6)
        assert str(why.value) and "\n" not in str(why.value)
    for kind in ("clean", "skipped", "pause", "reset"):
        for times in ((nine,), (None,)):
            with pytest.raises(ValueError):
                sl.position([sl.SessionFact(TODAY, kind, ("r",), ceiling=BIG if kind in sl.SESSION_KINDS else None,
                                            fault_times=times)], 500, 1e6)
    ok = sl.SessionFact(TODAY, "fault", ("r",), ceiling=BIG, fault_times=(nine, None))
    assert sl.position([ok, late[1]], 500, 1e6).stake == 250.0


def test_d_f_a_hand_set_time_on_a_session_fact_is_refused():
    nine = datetime(TODAY.year, TODAY.month, TODAY.day, 9, tzinfo=NY)
    for kind in sl.SESSION_KINDS:
        for fn in (sl.position, sl._walk):
            with pytest.raises(ValueError) as why:
                fn([sl.SessionFact(TODAY, kind, ("r",), ceiling=BIG, at=nine)], 500, 1e6)
            assert "carries no time" in str(why.value) and "\n" not in str(why.value)
    for kind in sl.EVENT_KINDS:
        assert sl.position([sl.SessionFact(TODAY, kind, ("r",), at=nine)], 500, 1e6).possible == 1


def test_d_a_more_than_720_possible_orders_on_one_day_is_refused():
    assert sl.MAX_DAY_ORDERS == 720 and sl.MAX_POSITIONS == 64
    d = TODAY
    six = [sl.SessionFact(d, k, ("r",)) for k in ["pause"] * 6 + ["reset"] * 6]      # 924 orders of unknown time
    with pytest.raises(ValueError) as why:
        sl.position(six, 500, 1e6)
    assert "720" in str(why.value) and d.isoformat() in str(why.value) and "\n" not in str(why.value)
    assert len(sl._orders(d, None, six[:5] + six[6:11])) == 252                     # C(10, 5): walked
    sl.position(six[:5] + six[6:11], 500, 1e6)
    # 6 unknown pauses and 5 unknown resets (462 arrangements) with a fault of unknown time: 462 x 12 places
    fault = sl.SessionFact(d, "fault", ("r",), ceiling=BIG, fault_times=(None,))
    with pytest.raises(ValueError) as why:
        sl.position([fault] + six[:11], 500, 1e6)
    assert "720" in str(why.value) and "\n" not in str(why.value)
    # interchangeable events (one kind, one time) are one order, however many there are
    many = [sl.SessionFact(d, "pause", ("r",)) for _ in range(2000)]
    assert len(sl._orders(d, None, many)) == 1 and sl.position(many, 500, 1e6).paused is True
    timed = [sl.SessionFact(d, k, ("r",), at=datetime(d.year, d.month, d.day, 9, 0, s, tzinfo=NY))
             for s in range(50) for k in ("pause", "reset")]                        # 2^50 orders: refused at once
    with pytest.raises(ValueError):
        sl.position(timed, 500, 1e6)


def _many_positions(rounds, seed=19):
    """A record whose possible positions grow: from a first stake of 100, a climb, then `rounds` days drawn with a
    fixed seed, each with a fault or a clean session or none, under ceilings that move near the first stake, and
    pauses and resets of unknown time, then a run of clean sessions. With seed 19 the set first passes 64 on the
    38th such day (found by search; 60 at most before it)."""
    rng = random.Random(seed)
    days = Days(date(2025, 1, 6))
    facts = [sl.SessionFact(days(), "clean", ("r",), ceiling=1e6) for _ in range(rng.choice([60, 120, 180]))]
    for _ in range(rounds):
        x = days()
        kinds_ = rng.choice([["pause", "reset"], ["reset", "pause", "reset"], ["pause"], ["reset"], []])
        s = rng.choice(["fault", "fault", "clean", None])
        if s:
            facts.append(sl.SessionFact(x, s, ("r",), ceiling=rng.choice([1e6, 1e6, 77.0, 130.0, 260.0, 333.0])))
        facts += [sl.SessionFact(x, k, ("r",)) for k in kinds_]
        for _ in range(rng.choice([0, 1, 5, 19, 20, 39, 40, 59, 60])):
            facts.append(sl.SessionFact(days(), "clean", ("r",), ceiling=rng.choice([1e6, 1e6, 1e6, 150.0, 400.0])))
    return facts


def test_d_b_more_than_64_positions_is_refused():
    sizes = [len(s) for _, s in sl._walk_sets(_many_positions(37), 100, 1e6)[2]]
    assert 32 < max(sizes) <= 64
    with pytest.raises(ValueError) as why:
        sl.position(_many_positions(38), 100, 1e6)
    assert "64" in str(why.value) and "\n" not in str(why.value)


def test_r1_a_fault_between_two_events_and_a_fault_before_a_reset_with_a_pause_of_unknown_time():
    d = CASE_DAYS[61]
    entries = [summ(x, ceiling=BIG) for x in CASE_DAYS[:60]]         # unattended at 200, not paused
    day = [pause(d, ts="09:00:00"), stop(d, "send", ceiling=BIG, timestamp=_utc(d, 12)), reset(d, ts="15:00:00")]
    p = sl.position(sl.session_facts(entries + day, LIVE, CAL), 100, 1e6)
    assert shape(p) == ("ticket", 100.0, 0, 20, False)               # pause, 100, then the reset: ticket at 100
    day = [pause(d, ts="09:00:00"), stop(d, "send", ceiling=BIG, timestamp=_utc(d, 16)), reset(d, ts="15:00:00")]
    p = sl.position(sl.session_facts(entries + day, LIVE, CAL), 100, 1e6)
    assert shape(p) == ("unattended", 50.0, 0, 40, False)            # pause, reset to 100, then the fault: 50


def test_d_d_the_lines_stop_at_the_first_day_whose_order_is_not_told():
    """D-D: one member: its lines as today; from the first day whose order is not told: the shared dated lines up to
    the day before, then the one undated line (days named up to five, then "and N more"), and no dated line after."""
    d = CASE_DAYS[61]
    facts, p = the_case(_utc(d, 12), _utc(d, 12))
    one, _ = the_case(_utc(d, 9), _utc(d, 15))
    q = sl.position(one, 100, 1e6)
    shared = [c for c in q.changes if c[0] is not None and c[0] < d]
    assert list(p.changes) == shared + [(None, untold_words([d], 2))]
    later = sessions_from(d + timedelta(days=1), 70)
    facts, _ = the_case(_utc(d, 12), _utc(d, 12), more=[summ(x, ceiling=BIG) for x in later])
    p = sl.position(facts, 100, 1e6)
    assert all(x is None or x < d for x, _ in p.changes) and untold(p) == untold_words([d], 2)
    # seven days whose order is not told, then a ceiling at the read: five named, "and 2 more", the read line last
    days = Days()
    facts = run(days, ["clean"] * 600, ceiling=1e12)                         # 500 doubled nine times
    named = []
    for _ in range(7):
        x = days()
        named.append(x)
        facts += [F("fault", x), sl.SessionFact(x, "pause", ("r",)), sl.SessionFact(x, "reset", ("r",))]
        facts += run(days, ["clean"] * 3, ceiling=1e12)
    p = sl.position(facts, 500, 1.0)
    n = p.possible
    assert n > 1 and p.changes[-2] == (None, untold_words(named, n)) and " and 2 more: " in p.changes[-2][1]
    assert p.changes[-1][0] is None and "lowest stake of the positions the record allows" in p.changes[-1][1]
    assert all(x is None or x < named[0] for x, _ in p.changes)
    # the words are one line each, and the template is the addendum's
    assert untold_words(named[:1], 1).endswith("1 position is possible; what is shown is the lowest stake, ticket "
                                               "mode where any of them has it and the pause where any of them has it")
    assert sl._untold_line(named, 3) == untold_words(named, 3)
    # describe: every sentence of the reported position, no stake it does not hold
    text = lines_ok(sl.describe(sl.position(the_case(_utc(d, 12), _utc(d, 12))[0], 100, 1e6), None, LADDER, 0))
    assert "at the earliest, for the slowest of the positions the record allows" in text
    assert "order not told: " + untold_words([d], 2) in text and "100.00" not in text   # 50 is held; 100 is not
    assert "last change: at the read" not in text


ORACLE_JOURNALS = 3000
ORACLE_MAX_COMBINATIONS = 5000


def test_property_d_the_set_equals_the_brute_force_on_every_day():
    """Addendum 2, by the oracle written FIRST: journals with one to four days whose order the times do not tell
    (faults of one to three faulty runs, pauses and resets of equal and unknown instants, moving ceilings), small
    enough to walk every combination of every day's possible orders. The module's set equals the brute force's on
    every day, its report is the part-by-part reading of it, and the propagated set (`oracle_set_walk`) is the brute
    force's too. Reordering a day's entries changes nothing; nothing after a day changes that day's position."""
    rng = random.Random(SEED + 41)
    journals = skipped = days_checked = several = 0
    largest, by_runs = 0, {1: 0, 2: 0, 3: 0}
    while journals < ORACLE_JOURNALS:
        blocks, truth, first, cap = timed_journal(rng, rng.randint(15, 60), "mixed")
        orders = {d: oracle_day_orders(truth[d][1], truth[d][2], truth[d][3]) for d in truth}
        undecidable = [d for d in truth if len(orders[d]) > 1]
        if not 1 <= len(undecidable) <= 4 or oracle_combinations(truth) > ORACLE_MAX_COMBINATIONS:
            skipped += 1
            continue
        journals += 1
        facts = sl.session_facts(flat(blocks), LIVE, CAL)
        final, walk, sets = sl._walk_sets(facts, first, cap)
        want = oracle_sets(truth, first)
        assert [(d, set(s)) for d, s in sets] == want
        assert [(d, set(s)) for d, s in oracle_set_walk(truth, first)] == want
        for (d, p), (_, states) in zip(walk, want):
            assert report(p) == oracle_report(states) and p.stake == min(p.earned, cap)
            several += len(states) > 1
            largest = max(largest, len(states))
            days_checked += 1
        for d in undecidable:
            if truth[d][1] == "fault":
                by_runs[min(3, len(truth[d][2]))] += 1
        assert final == walk[-1][1]
        again = sl.session_facts(flat([rng.sample(b, len(b)) for b in blocks]), LIVE, CAL)
        assert list(again) == list(facts) and sl.position(again, first, cap) == final
        k = rng.randint(0, len(blocks))
        head = sl._walk(sl.session_facts(flat(blocks[:k]), LIVE, CAL), first, cap)
        assert head == walk[:len(head)]
    print(f"property (D oracle): {journals} journals ({skipped} generated ones skipped), {days_checked} days, "
          f"{several} with more than one position, largest set {largest}, undecidable fault days by runs {by_runs}")
    assert several > 10_000 and largest >= 4 and min(by_runs.values()) > 200


def _faulty(e):
    return (e.get("type") == "order_step" and e.get("event") == "summary" and e.get("venue") == LIVE
            and ((e.get("status") == "stopped" and e.get("stage") in sl.FAULT_STAGES) or bool(e.get("faults"))))


def _usable(e, d):
    """The entry's time as an instant on its own New York date, read here (not by the module), or None."""
    ts = e.get("timestamp")
    if type(ts) is not str:
        return None
    try:
        t = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if t.utcoffset() is None or t.astimezone(NY).date() != d:
        return None
    return t


def _ny_or_none(t, d):
    """D-F, read here: an instant orders nothing unless it falls on `d` in New York."""
    return t if t is not None and t.astimezone(NY).date() == d else None


def concrete_versions(rng, blocks, limit=50):
    """D-E: concrete versions of a journal: on every day, the faulty runs and the pauses and resets put in one order
    the known different instants allow, and given times that are all known and different (a known time moves by at
    most milliseconds, so it keeps its place among the other known instants). All of them when there are at most
    `limit`, else `limit` drawn at random. Returns (how many exist, [blocks])."""
    days = []
    for i, block in enumerate(blocks):
        d = FIRST + timedelta(days=i)
        items = [j for j, e in enumerate(block) if isinstance(e, dict) and (_faulty(e) or (
            e.get("type") in ("drawdown_pause", "latch_reset") and e.get("venue") == LIVE))]
        times = {j: _usable(block[j], d) for j in items}
        perms = [p for p in itertools.permutations(items)
                 if all(times[a] is None or times[b] is None or times[a] <= times[b]
                        for x, a in enumerate(p) for b in p[x + 1:])]
        # equal known instants are one order whichever runs first, unless they are of different kinds or runs
        days.append((i, d, perms, times))
    total = 1
    for _, _, perms, _ in days:
        total *= len(perms)
    if total <= limit:
        choices = list(itertools.product(*[range(len(p)) for _, _, p, _ in days]))
    else:
        seen, choices = set(), []
        while len(choices) < limit:
            c = tuple(rng.randrange(len(p)) for _, _, p, _ in days)
            if c not in seen:
                seen.add(c)
                choices.append(c)
    out = []
    for choice in choices:
        version = [list(b) for b in blocks]
        for (i, d, perms, times), k in zip(days, choice):
            anchor = datetime(d.year, d.month, d.day, 0, 1, tzinfo=NY)
            for n, j in enumerate(perms[k]):
                if times[j] is not None:
                    anchor = times[j]
                t = anchor + timedelta(milliseconds=n + 1)
                version[i][j] = {**version[i][j], "timestamp": t.isoformat(timespec="microseconds")}
        out.append(version)
    return total, out


DE_JOURNALS = 2000
DE_VERSIONS = 50


def test_property_d_e_every_concrete_version_is_in_the_set_and_never_reported_more_careless():
    """D-E: for journals with days whose order the times do not tell, and at least 50 concrete versions of each (all
    of them when fewer exist): on every day the concrete walk's members are in the original's set, the reported
    earned stake is never above the concrete one's, the report is ticket whenever the concrete one is, and paused
    whenever the concrete one is."""
    rng = random.Random(SEED + 43)
    journals = versions = days_checked = all_of_them = 0
    lower = ticket_only = paused_only = 0
    while journals < DE_JOURNALS:
        blocks, truth, first, cap = timed_journal(rng, rng.randint(10, 40), "mixed")
        if not any(len(oracle_day_orders(truth[d][1], truth[d][2], truth[d][3])) > 1 for d in truth):
            continue
        journals += 1
        _, walk, sets = sl._walk_sets(sl.session_facts(flat(blocks), LIVE, CAL), first, cap)
        total, made = concrete_versions(rng, blocks, DE_VERSIONS)
        assert len(made) == min(total, DE_VERSIONS)
        all_of_them += total <= DE_VERSIONS
        for version in made:
            facts = sl.session_facts(flat(version), LIVE, CAL)
            assert all(f.at is not None for f in facts if f.kind in sl.EVENT_KINDS)
            assert all(None not in f.fault_times for f in facts if f.kind == "fault")
            _, cwalk, csets = sl._walk_sets(facts, first, cap)
            assert [d for d, _ in csets] == [d for d, _ in sets]
            for (d, s), (_, cs), (_, p), (_, c) in zip(sets, csets, walk, cwalk):
                assert set(cs) <= set(s)
                assert p.earned <= c.earned and p.stake <= c.stake
                assert c.mode != "ticket" or p.mode == "ticket"
                assert not c.paused or p.paused
                lower += p.earned < c.earned
                ticket_only += p.mode == "ticket" and c.mode != "ticket"
                paused_only += p.paused and not c.paused
                days_checked += 1
            versions += 1
    print(f"property (D-E): {journals} journals, {versions} concrete versions ({all_of_them} journals with every "
          f"version), {days_checked} days; the report strictly lower {lower}, ticket where the concrete is not "
          f"{ticket_only}, paused where it is not {paused_only}")
    assert versions >= DE_JOURNALS * 10 and lower > 1000 and ticket_only > 100 and paused_only > 100


def test_r2_a_batch_proves_a_fraction_only_when_every_summary_of_it_is_clean_and_dated():
    clean = summ(TODAY, venue=PAPER, batch="b1")
    assert sl.fractional_proven([clean, fill(0.5)], PAPER) == 1
    faulty = stop(TODAY, "reconcile", venue=PAPER, batch="b1")
    assert sl.fractional_proven([clean, faulty, fill(0.5)], PAPER) == 0
    assert sl.fractional_proven([faulty, clean, fill(0.5)], PAPER) == 0
    assert sl.fractional_proven([clean, {**clean, "faults": ["cap"]}, fill(0.5)], PAPER) == 0
    assert sl.fractional_proven([clean, stop(TODAY, "prices", venue=PAPER, batch="b1"), fill(0.5)], PAPER) == 0
    for day in ("2026-11-31", WEEK_DATE, None, 20261116, OMIT):
        undated = dict(clean)
        if day is OMIT:
            del undated["day"]
        else:
            undated["day"] = day
        assert sl.fractional_proven([clean, undated, fill(0.5)], PAPER) == 0
        assert sl.fractional_proven([undated, fill(0.5)], PAPER) == 0
    assert sl.fractional_proven([fill(0.5)], PAPER) == 0                                    # no summary at all
    assert sl.fractional_proven([clean, stop(TODAY, "send", venue=PAPER, batch="b2"), fill(0.5)], PAPER) == 1
    assert sl.fractional_proven([clean, stop(TODAY, "send", venue=LIVE, batch="b1"), fill(0.5)], PAPER) == 1
    assert sl.fractional_proven([clean, {**faulty, "event": "fill", "units": 1}, fill(0.5)], PAPER) == 1


@pytest.mark.parametrize("x, want", [
    (1e26, "100,000,000,000,000,000,000,000,000.00"),
    (1e300, "1" + "0" * 300 + ".00"),
    (5e-324, "0." + "0" * 323 + "5"),
    (sys.float_info.max, "17976931348623157" + "0" * 292 + ".00"),
    (-1e300, "-1" + "0" * 300 + ".00"),
    (2.2250738585072014e-308, "0." + "0" * 307 + "222507"),
])
def test_r3_amount_renders_any_finite_float(x, want):
    assert sl._amount(x).replace(",", "") == want.replace(",", "")
    if x == 1e26:
        assert sl._amount(x) == want


def test_r3_amount_is_total_on_random_finite_floats():
    import struct
    rng = random.Random(SEED + 31)
    n = 0
    while n < 20_000:
        x = struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
        if math.isfinite(x):
            text = sl._amount(x)
            assert type(text) is str and text
            if abs(x) >= 1:
                with localcontext() as ctx:
                    ctx.prec = 400
                    want = Decimal(repr(x)).quantize(Decimal("0.01"), ROUND_HALF_UP)
                assert Decimal(text.replace(",", "")) == want
            n += 1


def test_r3_position_and_describe_refuse_amounts_above_the_owners_first_stake_cap(monkeypatch):
    top = wb.LIVE_MONEY_MAX
    good = run(Days(), ["clean"])
    assert pos(good, top, top).stake == top                                     # at the cap: walked
    for first, ceiling in ((top * 2, 1e6), (500, top * 2), (1e300, 1e6), (500, 1e300),
                           (sys.float_info.max, sys.float_info.max), (math.nextafter(top, math.inf), top)):
        for fn in (sl.position, sl._walk):
            with pytest.raises(ValueError) as why:
                fn(good, first, ceiling)
            assert str(why.value) and "\n" not in str(why.value)
    p = pos(good)
    assert p.ceiling == 1e6
    lines_ok(sl.describe(p, None, LADDER, 0))
    for bad in (replace(LADDER, first_stake=top * 2), replace(LADDER, first_stake=1e300),
                replace(LADDER, first_stake=math.inf)):
        with pytest.raises(ValueError) as why:
            sl.describe(p, None, bad, 0)
        assert "\n" not in str(why.value)
    for bad in (top * 2, 1e300, math.nan):
        with pytest.raises(ValueError):
            sl.describe(replace(p, ceiling=bad), None, LADDER, 0)
    monkeypatch.setattr(wb, "LIVE_MONEY_MAX", 1000.0)                           # the one constant, read at the call
    with pytest.raises(ValueError):
        sl.position(good, 2000, 1e6)
    with pytest.raises(ValueError):
        sl.describe(p, None, LADDER, 0)


@pytest.mark.parametrize("venue", [" ", "   ", "\t", "\n ", "　", " "])
def test_r4_a_venue_of_only_whitespace_names_no_venue(venue):
    facts = sl.session_facts([summ(TODAY), pause(TODAY, venue=venue)], LIVE, CAL)
    assert kinds(facts) == [(TODAY, "clean"), (TODAY, "pause")] and facts.unreadable == 1
    facts = sl.session_facts([pause(TODAY), reset(TODAY, venue=venue)], LIVE, CAL)
    assert kinds(facts) == [(TODAY, "pause")] and facts.unreadable == 1
    assert sl.position(facts, 500, 1e6).paused is True


def test_r5_describe_at_the_ceiling_says_no_rise_is_possible():
    p = pos(run(Days(), ["clean"] * 61), 500, 1000)                            # earned 1000, today's ceiling 1000
    lines = sl.describe(p, None, LADDER, 0)
    lines_ok(lines)
    [nxt] = [line for line in lines if line.startswith("next: ")]
    assert "no rise is possible" in nxt and "doubles" not in nxt and "up to the ceiling" not in nxt
    assert (p.mode, p.stake, p.earned, p.ceiling) == ("unattended", 1000.0, 1000.0, 1000.0)
    q = pos(run(Days(), ["clean"] * 61), 500, 1500)                            # below the ceiling: it may double
    assert "the stake doubles from 1,000.00, up to the ceiling" in lines_ok(sl.describe(q, None, LADDER, 0))
