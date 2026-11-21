"""T51c S4c-2 (R33 for the ladder): a `done` shared run whose summary lists a shortfall is not a clean session: it
counts as skipped (no fault), and `fractional_proven` counts none of its fills. A done run without shortfalls is as
it was."""
from __future__ import annotations

from datetime import date

import pytest

from valedia_trader.execution_and_risk import go_live as gl
from valedia_trader.execution_and_risk import stake_ladder as sl

DAY = date(2026, 11, 16)                                  # a Monday
LIVE, PAPER = gl.LIVE_VENUE, gl.PAPER_VENUE
SHORT = {"VTI": {"book": 5.0, "venue": 3.0, "unapplied": 0.0, "short": 2.0}}


class Weekdays:
    def is_session(self, text):
        return date.fromisoformat(text).weekday() < 5


def summ(batch="b1", venue=LIVE, status="done", stage=None, **extra):
    return {"type": "order_step", "event": "summary", "venue": venue, "day": DAY.isoformat(), "status": status,
            "stage": stage, "batch": batch, "ceiling": 1e6, **extra}


def fill(batch="b1", units=0.5):
    return {"type": "order_step", "event": "fill", "venue": PAPER, "batch": batch, "day": DAY.isoformat(),
            "units": units, "symbol": "SPY"}


def kinds(entries, venue=LIVE):
    return [(f.day, f.kind) for f in sl.session_facts(entries, venue, Weekdays())]


def test_a_done_run_with_a_shortfall_is_a_skipped_session_not_a_clean_one_nor_a_fault():
    facts = sl.session_facts([summ(shared={"shortfalls": SHORT})], LIVE, Weekdays())
    assert [(f.day, f.kind) for f in facts] == [(DAY, "skipped")]
    assert any("shortfall" in r for r in facts[0].reasons)


@pytest.mark.parametrize("shared", [None, {}, {"shortfalls": {}}, {"owner_surplus": ["QQQ"]}])
def test_a_done_run_without_shortfalls_is_clean_as_today(shared):
    entries = [summ(**({} if shared is None else {"shared": shared}))]
    assert kinds(entries) == [(DAY, "clean")]


def test_a_shared_block_that_is_not_a_record_is_not_clean():
    assert kinds([summ(shared="garbled")]) == [(DAY, "skipped")]


def test_a_clean_run_on_the_same_day_still_makes_the_day_clean():
    assert kinds([summ(shared={"shortfalls": SHORT}), summ(batch="b2", shared={"shortfalls": {}})]) == [(DAY, "clean")]


def test_a_fault_beside_a_done_run_with_a_shortfall_is_still_a_fault():
    assert kinds([summ(shared={"shortfalls": SHORT}), summ(batch="b2", status="stopped", stage="send")]) == [(DAY, "fault")]


def test_fractional_proven_counts_none_of_a_done_run_with_a_shortfall():
    assert sl.fractional_proven([summ(venue=PAPER, shared={"shortfalls": SHORT}), fill()], PAPER) == 0
    assert sl.fractional_proven([summ(venue=PAPER, shared="garbled"), fill()], PAPER) == 0
    assert sl.fractional_proven([summ(venue=PAPER, shared={"shortfalls": {}}), fill()], PAPER) == 1
    assert sl.fractional_proven([summ(venue=PAPER), fill()], PAPER) == 1


def test_fractional_proven_batch_with_one_shortfall_summary_proves_nothing():
    entries = [summ(venue=PAPER), summ(venue=PAPER, shared={"shortfalls": SHORT}), fill()]
    assert sl.fractional_proven(entries, PAPER) == 0
