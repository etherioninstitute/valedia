"""T51c S4c-2 (R32, R33): the go-live gate reads a shared practice run's shortfall as R24 says: a skipped session, never
a fault, and a run that ended `done` with a shortfall at its last reconcile is not a clean run."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from valedia_trader.execution_and_risk import go_live as gl
from valedia_trader.research_loop.paper import Journal

TODAY = date(2026, 11, 13)                                    # a Friday


class WeekdayCalendar:
    def is_session(self, day):
        return date.fromisoformat(str(day)[:10]).weekday() < 5


CAL = WeekdayCalendar()
SHORT = {"VTI": {"book": 5.0, "venue": 3.0, "unapplied": 0.0, "short": 2.0}}


def _run(j, day, batch, status, stage=None, fills=0, shared=None, **extra):
    for k in range(fills):
        j.write(day, {"type": "order_step", "venue": gl.PAPER_VENUE, "batch": batch, "event": "fill", "fill_id": f"{batch}-{k}"})
    entry = {"type": "order_step", "venue": gl.PAPER_VENUE, "batch": batch, "event": "summary", "status": status,
             "stage": stage, **extra}
    if shared is not None:
        entry["shared"] = shared
    j.write(day, entry)


def _raw(folder, **entry):
    """An entry whose `day` the journal reader keeps but `paper_record` cannot read (as the base test does)."""
    j = Journal(folder)
    j.write("2026-11-12", {"type": "order_step", "venue": gl.PAPER_VENUE, "batch": "z", "event": "fill"})   # makes the folder
    with (Path(folder) / "2026-11-12.jsonl").open("a") as fh:
        fh.write(json.dumps({"type": "order_step", "venue": gl.PAPER_VENUE, "batch": "u", "event": "summary", "day": "someday",
                             **entry}) + "\n")


def _record(folder):
    record, why = gl.paper_record(folder, gl.PAPER_VENUE, TODAY, CAL)
    assert why is None
    return record


def test_r32_a_shortfall_stop_is_a_skipped_session_not_a_stopped_run(tmp_path):
    j = Journal(tmp_path / "j")
    _run(j, "2026-11-12", "s1", "stopped", "shortfall", fills=1, shared={"shortfalls": SHORT})
    record = _record(tmp_path / "j")
    assert record["days_since_a_stopped_run"] is None and record["last_stopped_run"] is None
    assert record["reconciled_fills"] == 0 and record["paper_broker_days"] == 0
    assert record["skipped_shortfall_runs"] == ["2026-11-12"]                   # listed as skipped


def test_r32_a_shortfall_stop_leaves_an_earlier_stop_clock_as_it_was(tmp_path):
    j = Journal(tmp_path / "j")
    _run(j, "2026-11-10", "s0", "stopped", "send")
    _run(j, "2026-11-12", "s1", "stopped", "shortfall", shared={"shortfalls": SHORT})
    record = _record(tmp_path / "j")
    assert record["days_since_a_stopped_run"] == 3 and record["last_stopped_run"] == "2026-11-10"
    assert record["skipped_shortfall_runs"] == ["2026-11-12"]


@pytest.mark.parametrize("shared", [None, "garbled", [], 0])
def test_r32_a_shortfall_stop_without_a_well_formed_shared_block_is_a_stopped_run(tmp_path, shared):
    j = Journal(tmp_path / "j")                      # the runner raises `shortfall` in shared mode only: this entry is corrupt
    _run(j, "2026-11-12", "s1", "stopped", "shortfall", shared=shared)
    record = _record(tmp_path / "j")
    assert record["days_since_a_stopped_run"] == 1 and record["last_stopped_run"] == "2026-11-12"
    assert "skipped_shortfall_runs" not in record


@pytest.mark.parametrize("shared", [None, {}, {"shortfalls": SHORT}])
def test_a_shortfall_stop_with_no_day_is_refused_as_base_refused_it(tmp_path, shared):
    _raw(tmp_path / "j", status="stopped", stage="shortfall", **({} if shared is None else {"shared": shared}))
    assert gl.paper_record(tmp_path / "j", gl.PAPER_VENUE, TODAY, CAL) == (
        None, "a stopped paper run in the journal carries no date; a person must look")


@pytest.mark.parametrize("stage", ["session", "ticket"])
def test_a_session_or_ticket_stop_with_no_day_is_still_no_refusal(tmp_path, stage):
    _raw(tmp_path / "j", status="stopped", stage=stage)
    record, why = gl.paper_record(tmp_path / "j", gl.PAPER_VENUE, TODAY, CAL)
    assert why is None and record["last_stopped_run"] is None


def test_no_none_is_listed_among_the_skipped_or_unclean_days(tmp_path):
    _raw(tmp_path / "j", status="done", shared={"shortfalls": SHORT})
    record = _record(tmp_path / "j")
    assert "unclean_shared_runs" not in record and "skipped_shortfall_runs" not in record   # no "None" day is listed


def test_r33_a_done_shared_run_with_a_shortfall_is_not_clean(tmp_path):
    j = Journal(tmp_path / "j")
    _run(j, "2026-11-12", "d1", "done", fills=3, shared={"shortfalls": SHORT, "owner_surplus": []})
    record = _record(tmp_path / "j")
    assert record["reconciled_fills"] == 0                       # its fills are not reconciled fills
    assert record["paper_broker_days"] == 0                      # not a clean session, so not a clean day
    assert record["days_since_a_stopped_run"] is None and record["last_stopped_run"] is None   # the clock is untouched
    assert record["unclean_shared_runs"] == ["2026-11-12"]
    assert "skipped_shortfall_runs" not in record


def test_r33_the_clock_is_unchanged_next_to_an_earlier_stop(tmp_path):
    j = Journal(tmp_path / "j")
    _run(j, "2026-11-10", "s0", "stopped", "reconcile")
    _run(j, "2026-11-12", "d1", "done", fills=2, shared={"shortfalls": SHORT})
    record = _record(tmp_path / "j")
    assert record["days_since_a_stopped_run"] == 3 and record["last_stopped_run"] == "2026-11-10"
    assert record["reconciled_fills"] == 0


def test_r33_a_clean_run_on_the_same_day_still_counts_that_day_and_its_own_fills(tmp_path):
    j = Journal(tmp_path / "j")
    _run(j, "2026-11-12", "d1", "done", fills=3, shared={"shortfalls": SHORT})
    _run(j, "2026-11-12", "d2", "done", fills=2, shared={"shortfalls": {}, "owner_surplus": ["QQQ"]})
    record = _record(tmp_path / "j")
    assert record["reconciled_fills"] == 2 and record["paper_broker_days"] == 1


def test_a_done_shared_run_without_shortfalls_counts_as_a_run_does_today(tmp_path):
    j = Journal(tmp_path / "j")
    _run(j, "2026-11-12", "d1", "done", fills=3, shared={"owner_surplus": ["QQQ"], "fees_bounded": {"count": 1}})
    _run(j, "2026-11-11", "d2", "done", fills=1, shared={"shortfalls": {}})
    record = _record(tmp_path / "j")
    assert record == {"paper_broker_days": 2, "reconciled_fills": 4, "days_since_a_stopped_run": None,
                      "last_stopped_run": None}


@pytest.mark.parametrize("shared", [None, {}, {"owner_surplus": []}])
def test_a_non_shared_or_shortfall_free_done_record_is_judged_as_today(tmp_path, shared):
    j = Journal(tmp_path / "j")
    _run(j, "2026-11-12", "d1", "done", fills=2, shared=shared)
    assert _record(tmp_path / "j") == {"paper_broker_days": 1, "reconciled_fills": 2, "days_since_a_stopped_run": None,
                                       "last_stopped_run": None}


def test_a_non_shared_run_stopped_at_another_stage_still_counts_as_today(tmp_path):
    j = Journal(tmp_path / "j")
    _run(j, "2026-11-12", "s1", "stopped", "marks", shared={"shortfalls": {}})
    assert _record(tmp_path / "j")["days_since_a_stopped_run"] == 1


def test_a_shared_block_that_is_not_a_record_makes_a_done_run_unclean_not_clean(tmp_path):
    j = Journal(tmp_path / "j")
    _run(j, "2026-11-12", "d1", "done", fills=2, shared="garbled")
    record = _record(tmp_path / "j")
    assert record["reconciled_fills"] == 0 and record["unclean_shared_runs"] == ["2026-11-12"]
    assert record["last_stopped_run"] is None


def test_the_gate_names_the_skipped_and_unclean_runs_in_its_figures(tmp_path):
    j = Journal(tmp_path / "j")
    _run(j, "2026-11-12", "d1", "done", fills=2, shared={"shortfalls": SHORT})
    _run(j, "2026-11-11", "s1", "stopped", "shortfall")
    lock = gl.judge_checklist({"min_paper_broker_days": 1, "min_reconciled_fills": 1, "min_days_since_a_stopped_run": 1,
                               "fills_checked_by_hand": 1, "min_fills_checked_by_hand": 1, "confirmed_by": "x"},
                              _record(tmp_path / "j"), None)
    assert not lock["met"] and "0 fills journaled in paper runs with a clean reconcile" in lock["reason"]
    assert lock["figures"]["record"]["unclean_shared_runs"] == ["2026-11-12"]
