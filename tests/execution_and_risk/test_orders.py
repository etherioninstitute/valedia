import math
import multiprocessing
import os
import random
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from valedia_trader.execution_and_risk import orders
from valedia_trader.execution_and_risk.orders import OrderError, OrderLedger, OrdersBlocked, allocate, net_orders


class LocalRefusal(Exception):
    """Stands for a refusal made on this machine before any network call."""


class Crash(BaseException):
    """Stands for the process dying in the middle of a venue call."""


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeVenue:
    """A scripted venue. `mode` says what the next submit does; `book` holds what it knows about each tag."""
    name = "fake"

    def __init__(self, mode="accept", on_submit=None):
        self.mode = mode
        self.on_submit = on_submit
        self.calls = []
        self.book = {}
        self.held = {}
        self.status_calls = []
        self.status_raises = set()
        self.positions_raise = False

    def _take(self, symbol, units, tag):
        self.book[tag] = {"state": "open", "filled_units": 0.0, "avg_price": None, "fee": None,
                          "venue_order_id": f"V-{tag}", "raw_status": "NEW"}

    def submit(self, symbol, units, tag, price=None, limit_price=None):
        self.calls.append({"symbol": symbol, "units": units, "tag": tag, "price": price, "limit_price": limit_price})
        if self.on_submit:
            self.on_submit(tag)
        mode = self.mode
        if mode == "accept":
            self._take(symbol, units, tag)
            return {"status": "accepted", "venue_order_id": f"V-{tag}"}
        if mode == "raise":
            raise TimeoutError("read timed out")
        if mode == "raise_after_taking":
            self._take(symbol, units, tag)
            raise ConnectionError("connection reset after the request was sent")
        if mode == "not_sent":
            raise LocalRefusal("the order is larger than the account allows")
        if mode == "odd_result":
            return {"status": "pending_review"}
        if mode == "crash_before":
            raise Crash()
        if mode == "crash_after":
            self._take(symbol, units, tag)
            raise Crash()
        raise AssertionError(mode)

    def order_status(self, tag):
        self.status_calls.append(tag)
        if tag in self.status_raises:
            raise ConnectionError("status endpoint down")
        got = self.book.get(tag)
        return dict(got) if got is not None else None

    def positions(self):
        if self.positions_raise:
            raise ConnectionError("positions endpoint down")
        return dict(self.held)

    def set(self, tag, **fields):
        self.book[tag].update(fields)


def ledger(path=":memory:", clock=None):
    return OrderLedger(path, not_sent=(LocalRefusal,), clock=clock or Clock())


def ready(led, venue, book=None):
    """A ledger with one clean reconcile behind it, so it may submit."""
    result = led.reconcile(venue, book if book is not None else venue.positions())
    assert result["clean"], result
    return led


def intend(led, tag="s1-XLE-2026-11-12", symbol="XLE", units=10.0, price=90.0, **kw):
    return led.intend(tag, symbol, units, price, **kw)


# ---- intend


def test_intend_records_an_order_in_intended_with_its_parts():
    led = ledger()
    rec = intend(led, limit_price=91.0, batch="b1", parts=[{"strategy": "s1", "units": 10.0}])
    assert rec["state"] == "intended" and rec["symbol"] == "XLE" and rec["units"] == 10.0
    assert rec["reference_price"] == 90.0 and rec["limit_price"] == 91.0 and rec["batch"] == "b1"
    assert rec["parts"] == [{"strategy": "s1", "units": 10.0}] and rec["filled_units"] == 0.0
    assert led.order("s1-XLE-2026-11-12") == rec
    assert [(e["from"], e["to"]) for e in led.events()] == [(None, "intended")]


def test_intend_twice_with_the_same_fields_returns_the_record_and_with_different_fields_raises():
    led = ledger()
    first = intend(led, parts=[{"strategy": "s1", "units": 10.0}])
    assert intend(led, parts=[{"strategy": "s1", "units": 10.0}]) == first
    assert len(led.orders()) == 1 and len(led.events()) == 1
    with pytest.raises(OrderError, match="different"):
        intend(led, units=11.0)
    with pytest.raises(OrderError, match="different"):
        intend(led, parts=[{"strategy": "s2", "units": 10.0}])


@pytest.mark.parametrize("units, price", [(0.0, 90.0), (10.0, 0.0), (10.0, -1.0), (float("nan"), 90.0)])
def test_intend_refuses_zero_units_and_a_price_that_is_not_above_zero(units, price):
    with pytest.raises(OrderError):
        intend(ledger(), units=units, price=price)


# ---- submit_once


def test_happy_path_intend_submit_reconcile_fill():
    clock = Clock()
    led, venue = ledger(clock=clock), FakeVenue()
    ready(led, venue)
    intend(led, limit_price=91.0)
    rec = led.submit_once(venue, "s1-XLE-2026-11-12")
    assert rec["state"] == "accepted" and rec["venue_order_id"] == "V-s1-XLE-2026-11-12"
    assert venue.calls == [{"symbol": "XLE", "units": 10.0, "tag": "s1-XLE-2026-11-12", "price": 90.0, "limit_price": 91.0}]
    venue.set("s1-XLE-2026-11-12", state="filled", filled_units=10.0, avg_price=90.5, fee=1.0, raw_status="FILLED")
    venue.held = {"XLE": 10.0}
    clock.t += 30
    result = led.reconcile(venue, {})                               # the book does not hold the new fill yet
    assert result["clean"] and result["orders_checked"] == 1 and result["time"] == clock.t
    assert [core(f) for f in result["fills"]] == [{"tag": "s1-XLE-2026-11-12", "symbol": "XLE", "units": 10.0, "price": 90.5, "fee": 1.0}]
    assert result["updated"] == [{"tag": "s1-XLE-2026-11-12", "from": "accepted", "to": "filled"}]
    assert led.order("s1-XLE-2026-11-12")["state"] == "filled"
    assert [(e["from"], e["to"]) for e in led.events("s1-XLE-2026-11-12")] == [
        (None, "intended"), ("intended", "sent"), ("sent", "accepted"), ("accepted", "filled")]
    assert "not yet applied" in led.blocked()[0]
    assert led.mark_applied([f["id"] for f in result["fills"]]) == 1
    assert led.blocked() == []
    assert led.last_reconcile()["clean"] is True and led.last_reconcile()["time"] == clock.t


def test_the_same_tag_submitted_twice_makes_one_venue_call():
    led, venue = ledger(), FakeVenue()
    ready(led, venue)
    intend(led)
    first = led.submit_once(venue, "s1-XLE-2026-11-12")
    second = led.submit_once(venue, "s1-XLE-2026-11-12")
    assert len(venue.calls) == 1 and second == first and second["state"] == "accepted"


def test_a_venue_exception_gives_unknown_and_a_second_submit_makes_no_call():
    led, venue = ledger(), FakeVenue(mode="raise")
    ready(led, venue)
    intend(led)
    rec = led.submit_once(venue, "s1-XLE-2026-11-12")
    assert rec["state"] == "unknown" and rec["note"].startswith("TimeoutError: read timed out")
    with pytest.raises(OrdersBlocked):
        led.submit_once(venue, "s1-XLE-2026-11-12")
    assert len(venue.calls) == 1


def test_an_unknown_order_is_not_resubmitted_even_when_nothing_blocks():
    led, venue = ledger(), FakeVenue(mode="raise")
    ready(led, venue)
    intend(led)
    led.submit_once(venue, "s1-XLE-2026-11-12")
    led.blocked = lambda max_age_seconds=21600: []         # pretend nothing blocks: the state alone must stop a second call
    assert led.submit_once(venue, "s1-XLE-2026-11-12")["state"] == "unknown"
    assert len(venue.calls) == 1


def test_a_result_that_is_not_accepted_gives_unknown():
    led, venue = ledger(), FakeVenue(mode="odd_result")
    ready(led, venue)
    intend(led)
    rec = led.submit_once(venue, "s1-XLE-2026-11-12")
    assert rec["state"] == "unknown" and "pending_review" in rec["note"]


def test_a_not_sent_exception_gives_refused_and_it_is_terminal():
    led, venue = ledger(), FakeVenue(mode="not_sent")
    ready(led, venue)
    intend(led)
    rec = led.submit_once(venue, "s1-XLE-2026-11-12")
    assert rec["state"] == "refused" and rec["note"].startswith("LocalRefusal: the order is larger")
    assert led.blocked() == []                              # a refusal settles the order: nothing is unknown
    assert led.submit_once(venue, "s1-XLE-2026-11-12")["state"] == "refused" and len(venue.calls) == 1
    led.reconcile(venue, {})
    assert venue.status_calls == []                          # terminal orders are not asked about


def test_stored_error_text_is_the_class_name_and_at_most_200_characters():
    led = ledger()

    class Loud:
        name = "loud"

        def submit(self, *a, **k):
            raise ValueError("x" * 1000)

        def positions(self):
            return {}

        def order_status(self, tag):
            return None

    ready(led, Loud())
    intend(led)
    note = led.submit_once(Loud(), "s1-XLE-2026-11-12")["note"]
    assert note == "ValueError: " + "x" * 200


def test_sent_is_on_disk_before_the_venue_is_called(tmp_path):
    path = tmp_path / "orders.sqlite"
    seen = []
    venue = FakeVenue(on_submit=lambda tag: seen.append(ledger(path).order(tag)["state"]))
    led = ledger(path)
    ready(led, venue)
    intend(led)
    led.submit_once(venue, "s1-XLE-2026-11-12")
    assert seen == ["sent"]


def test_a_venue_that_raises_after_taking_the_order_is_settled_by_reconcile():
    clock = Clock()
    led, venue = ledger(clock=clock), FakeVenue(mode="raise_after_taking")
    ready(led, venue)
    intend(led)
    assert led.submit_once(venue, "s1-XLE-2026-11-12")["state"] == "unknown"
    first = led.reconcile(venue, {})
    assert led.order("s1-XLE-2026-11-12")["state"] == "accepted"
    assert first["updated"] == [{"tag": "s1-XLE-2026-11-12", "from": "unknown", "to": "accepted"}] and first["clean"]
    assert led.order("s1-XLE-2026-11-12")["venue_order_id"] == "V-s1-XLE-2026-11-12"
    venue.set("s1-XLE-2026-11-12", state="filled", filled_units=10.0, avg_price=90.0, fee=0.0)
    venue.held = {"XLE": 10.0}
    second = led.reconcile(venue, {})
    assert led.order("s1-XLE-2026-11-12")["state"] == "filled" and second["clean"]
    assert [core(f) for f in second["fills"]] == [{"tag": "s1-XLE-2026-11-12", "symbol": "XLE", "units": 10.0, "price": 90.0, "fee": 0.0}]


def test_a_keyboard_interrupt_leaves_the_order_in_sent():
    led = ledger()

    class Interrupted(FakeVenue):
        def submit(self, *a, **k):
            raise KeyboardInterrupt

    venue = Interrupted()
    ready(led, venue)
    intend(led)
    with pytest.raises(KeyboardInterrupt):
        led.submit_once(venue, "s1-XLE-2026-11-12")
    assert led.order("s1-XLE-2026-11-12")["state"] == "sent"
    assert any("s1-XLE-2026-11-12" in r for r in led.blocked())


def _crash_and_reopen(tmp_path, mode, clock):
    path = tmp_path / "orders.sqlite"
    venue = FakeVenue(mode=mode)
    led = ledger(path, clock)
    ready(led, venue)
    intend(led)
    with pytest.raises(Crash):
        led.submit_once(venue, "s1-XLE-2026-11-12")
    del led                                                  # abandoned, never closed
    return ledger(path, clock), venue


def test_a_crash_between_sent_and_the_result_blocks_and_is_settled_when_found_at_the_venue(tmp_path):
    clock = Clock()
    led, venue = _crash_and_reopen(tmp_path, "crash_after", clock)
    assert led.order("s1-XLE-2026-11-12")["state"] == "sent"
    reasons = led.blocked()
    assert any("s1-XLE-2026-11-12" in r for r in reasons)
    with pytest.raises(OrdersBlocked) as err:
        led.submit_once(venue, "s1-XLE-2026-11-12")
    assert err.value.reasons == reasons
    clock.t += 5
    result = led.reconcile(venue, {})
    assert result["clean"] and led.order("s1-XLE-2026-11-12")["state"] == "accepted"
    assert led.blocked() == [] and len(venue.calls) == 1


def test_a_crash_between_sent_and_the_result_is_settled_as_never_arrived_once_old_enough(tmp_path):
    clock = Clock()
    led, venue = _crash_and_reopen(tmp_path, "crash_before", clock)
    clock.t += 60
    early = led.reconcile(venue, {})
    assert led.order("s1-XLE-2026-11-12")["state"] == "sent" and not early["clean"]
    assert [u["tag"] for u in early["unresolved"]] == ["s1-XLE-2026-11-12"]
    clock.t += 61                                            # now 121 s after `sent`
    late = led.reconcile(venue, {})
    assert led.order("s1-XLE-2026-11-12")["state"] == "never_arrived" and late["clean"]
    assert late["updated"] == [{"tag": "s1-XLE-2026-11-12", "from": "sent", "to": "never_arrived"}]
    assert led.blocked() == [] and len(venue.calls) == 1


def test_never_arrived_is_not_declared_early_for_an_unknown_order():
    clock = Clock()
    led, venue = ledger(clock=clock), FakeVenue(mode="raise")
    ready(led, venue)
    intend(led)
    led.submit_once(venue, "s1-XLE-2026-11-12")
    for step in (1, 59, 60):                                 # 1 s, 60 s and exactly 120 s after `sent`
        clock.t += step
        assert not led.reconcile(venue, {}, settle_seconds=120)["clean"]
        assert led.order("s1-XLE-2026-11-12")["state"] == "unknown"
    clock.t += 0.5
    led.reconcile(venue, {}, settle_seconds=120)
    assert led.order("s1-XLE-2026-11-12")["state"] == "never_arrived"


def test_an_accepted_order_the_venue_no_longer_knows_is_an_anomaly():
    clock = Clock()
    led, venue = ledger(clock=clock), FakeVenue()
    ready(led, venue)
    intend(led)
    led.submit_once(venue, "s1-XLE-2026-11-12")
    del venue.book["s1-XLE-2026-11-12"]
    clock.t += 10_000
    result = led.reconcile(venue, {})
    assert led.order("s1-XLE-2026-11-12")["state"] == "accepted" and not result["clean"]
    assert [a["tag"] for a in result["anomalies"]] == ["s1-XLE-2026-11-12"]


def test_a_status_exception_or_unknown_status_leaves_the_state_and_counts_as_unresolved():
    led, venue = ledger(), FakeVenue()
    ready(led, venue)
    intend(led, tag="a")
    intend(led, tag="b")
    led.submit_once(venue, "a")
    led.submit_once(venue, "b")
    venue.status_raises.add("a")
    venue.set("b", state="unknown_status", raw_status="WEIRD")
    result = led.reconcile(venue, {})
    assert not result["clean"] and sorted(u["tag"] for u in result["unresolved"]) == ["a", "b"]
    assert led.order("a")["state"] == "accepted" and led.order("b")["state"] == "accepted"
    assert "ConnectionError" in [u for u in result["unresolved"] if u["tag"] == "a"][0]["reason"]


@pytest.mark.parametrize("venue_state, ledger_state", [("cancelled", "cancelled"), ("rejected", "rejected"),
                                                       ("expired", "expired"), ("open", "accepted")])
def test_venue_states_map_to_ledger_states(venue_state, ledger_state):
    led, venue = ledger(), FakeVenue()
    ready(led, venue)
    intend(led)
    led.submit_once(venue, "s1-XLE-2026-11-12")
    venue.set("s1-XLE-2026-11-12", state=venue_state)
    led.reconcile(venue, {})
    assert led.order("s1-XLE-2026-11-12")["state"] == ledger_state


# ---- blocked


def test_blocked_with_no_reconcile_yet():
    reasons = ledger().blocked()
    assert len(reasons) == 1 and "no reconcile" in reasons[0]


def test_blocked_by_an_order_in_unknown_names_the_tag():
    led, venue = ledger(), FakeVenue(mode="raise")
    ready(led, venue)
    assert led.blocked() == []
    intend(led)
    led.submit_once(venue, "s1-XLE-2026-11-12")
    reasons = led.blocked()
    assert len(reasons) == 1 and "s1-XLE-2026-11-12" in reasons[0] and "unknown" in reasons[0]


def test_blocked_when_the_latest_reconcile_is_not_clean():
    led, venue = ledger(), FakeVenue()
    venue.held = {"XLE": 5.0}
    assert not led.reconcile(venue, {})["clean"]
    reasons = led.blocked()
    assert len(reasons) == 1 and "not clean" in reasons[0]
    ready(led, venue, {"XLE": 5.0})
    assert led.blocked() == []


def test_blocked_when_the_latest_reconcile_is_too_old():
    clock = Clock()
    led, venue = ledger(clock=clock), FakeVenue()
    ready(led, venue)
    clock.t += 21600
    assert led.blocked() == []
    clock.t += 1
    reasons = led.blocked()
    assert len(reasons) == 1 and "old" in reasons[0]
    assert led.blocked(max_age_seconds=30000) == []


def test_submit_when_blocked_raises_and_changes_nothing():
    led, venue = ledger(), FakeVenue()
    intend(led)
    events = led.events()
    with pytest.raises(OrdersBlocked) as err:
        led.submit_once(venue, "s1-XLE-2026-11-12")
    assert err.value.reasons and "no reconcile" in err.value.reasons[0]
    assert venue.calls == [] and led.order("s1-XLE-2026-11-12")["state"] == "intended" and led.events() == events


def test_submit_of_a_tag_never_intended_raises():
    led, venue = ledger(), FakeVenue()
    ready(led, venue)
    with pytest.raises(OrderError, match="no order"):
        led.submit_once(venue, "nope")


def test_a_batch_stops_at_the_first_unknown():
    led, venue = ledger(), FakeVenue()
    ready(led, venue)
    tags = ["b1-a", "b1-b", "b1-c"]
    for t in tags:
        intend(led, tag=t, batch="b1")
    modes = iter(["accept", "raise", "accept"])
    sent = []
    for t in tags:
        venue.mode = next(modes)
        try:
            sent.append(led.submit_once(venue, t)["state"])
        except OrdersBlocked as err:
            assert any("b1-b" in r for r in err.reasons)
            break
    assert sent == ["accepted", "unknown"] and [c["tag"] for c in venue.calls] == ["b1-a", "b1-b"]
    assert led.order("b1-c")["state"] == "intended"
    assert [o["tag"] for o in led.orders(states=("intended",))] == ["b1-c"]


# ---- fills


def test_partial_fills_are_reported_exactly_once_with_the_price_of_each_increment():
    led, venue = ledger(), FakeVenue()
    ready(led, venue)
    intend(led, tag="sell", units=-10.0)
    led.submit_once(venue, "sell")
    venue.set("sell", state="partly_filled", filled_units=4.0, avg_price=100.0, fee=1.0)
    r1 = led.reconcile(venue, {})
    assert [core(f) for f in r1["fills"]] == [{"tag": "sell", "symbol": "XLE", "units": -4.0, "price": 100.0, "fee": 1.0}]
    assert led.order("sell")["state"] == "partly_filled" and led.order("sell")["filled_units"] == 4.0
    r2 = led.reconcile(venue, {})
    assert r2["fills"] == [] and r2["updated"] == []
    venue.set("sell", state="filled", filled_units=10.0, avg_price=102.0, fee=2.5)
    r3 = led.reconcile(venue, {})
    (fill,) = r3["fills"]
    assert fill["units"] == pytest.approx(-6.0) and fill["price"] == pytest.approx((102.0 * 10 - 100.0 * 4) / 6)
    assert fill["fee"] == pytest.approx(1.5)
    rec = led.order("sell")
    assert rec["state"] == "filled" and rec["filled_units"] == 10.0 and rec["avg_price"] == 102.0 and rec["fee"] == 2.5
    assert led.reconcile(venue, {})["fills"] == [] and led.reconcile(venue, {})["orders_checked"] == 1   # asked again for late fees


def test_a_fill_without_an_average_price_or_fee_is_reported_with_none():
    led, venue = ledger(), FakeVenue()
    ready(led, venue)
    intend(led)
    led.submit_once(venue, "s1-XLE-2026-11-12")
    venue.set("s1-XLE-2026-11-12", state="partly_filled", filled_units=3.0, avg_price=None, fee=None)
    (fill,) = led.reconcile(venue, {})["fills"]
    assert fill["units"] == 3.0 and fill["price"] is None and fill["fee"] is None


def test_filled_units_never_decrease():
    led, venue = ledger(), FakeVenue()
    ready(led, venue)
    intend(led)
    led.submit_once(venue, "s1-XLE-2026-11-12")
    venue.set("s1-XLE-2026-11-12", state="partly_filled", filled_units=6.0, avg_price=90.0, fee=0.5)
    led.reconcile(venue, {})
    before = led.order("s1-XLE-2026-11-12")
    venue.set("s1-XLE-2026-11-12", state="cancelled", filled_units=2.0, avg_price=89.0, fee=0.1)
    result = led.reconcile(venue, {})
    assert result["fills"] == [] and not result["clean"]
    assert [a["tag"] for a in result["anomalies"]] == ["s1-XLE-2026-11-12"]
    assert led.order("s1-XLE-2026-11-12") == before


def test_a_fill_larger_than_the_order_is_an_anomaly():
    led, venue = ledger(), FakeVenue()
    ready(led, venue)
    intend(led)
    led.submit_once(venue, "s1-XLE-2026-11-12")
    venue.set("s1-XLE-2026-11-12", state="filled", filled_units=12.0, avg_price=90.0)
    result = led.reconcile(venue, {})
    assert result["fills"] == [] and [a["tag"] for a in result["anomalies"]] == ["s1-XLE-2026-11-12"]
    assert led.order("s1-XLE-2026-11-12")["state"] == "accepted"


# ---- positions


def test_position_differences_over_the_union_of_symbols():
    led, venue = ledger(), FakeVenue()
    venue.held = {"XLE": 10.0, "SPY": 3.0, "GLD": 1.0}
    result = led.reconcile(venue, {"XLE": 10.0 + 1e-9, "SPY": 2.0, "TLT": 4.0})
    assert not result["clean"]
    assert result["position_differences"] == {"GLD": {"book": 0.0, "venue": 1.0, "unapplied": 0.0},
                                              "SPY": {"book": 2.0, "venue": 3.0, "unapplied": 0.0},
                                              "TLT": {"book": 4.0, "venue": 0.0, "unapplied": 0.0}}


def test_venue_positions_raising_makes_the_reconcile_not_clean_and_says_why():
    led, venue = ledger(), FakeVenue()
    venue.positions_raise = True
    result = led.reconcile(venue, {})
    assert not result["clean"] and "ConnectionError" in result["positions_error"]
    assert result["position_differences"] == {}
    assert led.last_reconcile()["clean"] is False and "ConnectionError" in led.last_reconcile()["summary"]["positions_error"]
    assert "not clean" in led.blocked()[0]


# ---- the record


def test_events_can_never_be_updated_or_deleted(tmp_path):
    path = tmp_path / "orders.sqlite"
    led = ledger(path)
    intend(led)
    raw = sqlite3.connect(str(path))
    with pytest.raises(sqlite3.DatabaseError):
        raw.execute("UPDATE events SET note = 'edited'")
    with pytest.raises(sqlite3.DatabaseError):
        raw.execute("DELETE FROM events")
    raw.close()
    assert len(led.events()) == 1


def test_every_reconcile_is_recorded_and_a_reopened_ledger_sees_it(tmp_path):
    path = tmp_path / "orders.sqlite"
    clock = Clock()
    led, venue = ledger(path, clock), FakeVenue()
    ready(led, venue)
    led.close()
    again = ledger(path, clock)
    last = again.last_reconcile()
    assert last["clean"] is True and last["time"] == clock.t and last["summary"]["orders_checked"] == 0
    assert again.blocked() == []


# ---- netting and allocation


def test_net_orders_sums_per_symbol_and_keeps_parts():
    deltas = [{"strategy": "trend", "symbol": "XLE", "units": 10.0}, {"strategy": "hold", "symbol": "XLE", "units": -4.0},
              {"strategy": "trend", "symbol": "XLE", "units": 2.0}, {"strategy": "hold", "symbol": "SPY", "units": 3.0},
              {"strategy": "trend", "symbol": "SPY", "units": -3.0}, {"strategy": "carry", "symbol": "GLD", "units": 0.0},
              {"strategy": "carry", "symbol": "XLE", "units": 1.0}, {"strategy": "carry", "symbol": "XLE", "units": -1.0}]
    assert net_orders(deltas) == [
        {"symbol": "SPY", "units": 0.0, "parts": [{"strategy": "hold", "units": 3.0}, {"strategy": "trend", "units": -3.0}]},
        {"symbol": "XLE", "units": 8.0, "parts": [{"strategy": "hold", "units": -4.0}, {"strategy": "trend", "units": 12.0}]},
    ]
    assert net_orders([]) == []


def test_allocate_fills_the_opposite_side_in_full_and_shares_the_rest():
    parts = [{"strategy": "a", "units": 6.0}, {"strategy": "b", "units": 4.0}, {"strategy": "c", "units": -2.0}]  # net +8
    got = allocate(parts, 4.0)                              # pool = 2 + 4 = 6 shared 6:4 between a and b
    assert got == pytest.approx({"a": 3.6, "b": 2.4, "c": -2.0})
    assert allocate(parts, 8.0) == {"a": 6.0, "b": 4.0, "c": -2.0}
    assert allocate(parts, 0.0) == pytest.approx({"a": 1.2, "b": 0.8, "c": -2.0})


def test_allocate_with_a_zero_net_fills_every_part():
    parts = [{"strategy": "a", "units": 3.0}, {"strategy": "b", "units": -3.0}]
    assert allocate(parts, 0.0) == {"a": 3.0, "b": -3.0}
    with pytest.raises(OrderError):
        allocate(parts, 1.0)


@pytest.mark.parametrize("filled", [-1.0, 8.5, 100.0])
def test_allocate_refuses_an_impossible_venue_fill(filled):
    parts = [{"strategy": "a", "units": 6.0}, {"strategy": "b", "units": 4.0}, {"strategy": "c", "units": -2.0}]
    with pytest.raises(OrderError):
        allocate(parts, filled)


def test_allocate_properties_hold_on_random_cases():
    rng = random.Random(20261112)
    for _ in range(2000):
        n = rng.randint(1, 6)
        deltas = [{"strategy": f"s{i}", "symbol": "X", "units": round(rng.uniform(-50, 50), rng.choice([0, 2, 6]))}
                  for i in range(n)]
        if rng.random() < 0.15 and n >= 2:                   # some cases whose parts cancel exactly
            deltas[-1]["units"] = -sum(d["units"] for d in deltas[:-1])
        netted = net_orders(deltas)
        if not netted:
            continue
        (entry,) = netted
        net, parts = entry["units"], entry["parts"]
        filled = 0.0 if net == 0.0 else net * rng.choice([0.0, 1.0, rng.random()])
        got = allocate(parts, filled)
        assert set(got) == {p["strategy"] for p in parts}
        assert sum(got.values()) == pytest.approx(filled, abs=1e-9)
        for p in parts:
            g = got[p["strategy"]]
            assert g * p["units"] >= 0 and abs(g) <= abs(p["units"]) + 1e-12
        if filled == net:
            assert got == {p["strategy"]: p["units"] for p in parts}


def test_the_module_exposes_its_names():
    assert {"LiveVenue", "OrderLedger", "OrderError", "OrdersBlocked", "net_orders", "allocate"} <= set(orders.__all__)
    assert issubclass(OrdersBlocked, OrderError) and issubclass(OrderError, RuntimeError)


# ---- rev 2: filled_units None means the venue did not say


def _accepted(units=10.0):
    clock = Clock()
    led, venue = ledger(clock=clock), FakeVenue()
    ready(led, venue)
    intend(led, units=units)
    led.submit_once(venue, "s1-XLE-2026-11-12")
    return led, venue, clock


def test_filled_with_no_amount_and_nothing_stored_is_unresolved_and_not_moved():
    led, venue, _ = _accepted()
    before = led.order("s1-XLE-2026-11-12")
    venue.set("s1-XLE-2026-11-12", state="filled", filled_units=None, avg_price=None, fee=None)
    result = led.reconcile(venue, {})
    assert not result["clean"] and result["fills"] == [] and result["updated"] == []
    (u,) = result["unresolved"]
    assert u["tag"] == "s1-XLE-2026-11-12" and "filled" in u["reason"] and "whole size" in u["reason"]
    assert result["anomalies"] == []
    after, keys = led.order("s1-XLE-2026-11-12"), ("state", "filled_units", "avg_price", "fee")
    assert {k: after[k] for k in keys} == {k: before[k] for k in keys} == {
        "state": "accepted", "filled_units": 0.0, "avg_price": None, "fee": None}
    assert led.events("s1-XLE-2026-11-12")[-1]["to"] == "accepted"            # no state change was written


def test_filled_with_an_amount_short_of_the_size_is_unresolved_and_the_increase_is_reported_once():
    led, venue, _ = _accepted()
    venue.set("s1-XLE-2026-11-12", state="filled", filled_units=7.0, avg_price=90.0, fee=0.7)
    first = led.reconcile(venue, {})
    assert [core(f) for f in first["fills"]] == [{"tag": "s1-XLE-2026-11-12", "symbol": "XLE", "units": 7.0, "price": 90.0, "fee": 0.7}]
    assert [u["tag"] for u in first["unresolved"]] == ["s1-XLE-2026-11-12"] and not first["clean"]
    rec = led.order("s1-XLE-2026-11-12")
    assert rec["state"] == "accepted" and rec["filled_units"] == 7.0 and rec["avg_price"] == 90.0 and rec["fee"] == 0.7
    second = led.reconcile(venue, {})
    assert second["fills"] == [] and [u["tag"] for u in second["unresolved"]] == ["s1-XLE-2026-11-12"]
    assert led.order("s1-XLE-2026-11-12")["state"] == "accepted"


def test_filled_with_no_amount_when_the_stored_total_is_the_whole_size_moves_to_filled():
    led, venue, _ = _accepted()
    venue.set("s1-XLE-2026-11-12", state="partly_filled", filled_units=10.0, avg_price=90.0, fee=1.0)
    led.reconcile(venue, {})
    venue.set("s1-XLE-2026-11-12", state="filled", filled_units=None, avg_price=None, fee=None)
    venue.held = {"XLE": 10.0}                                          # the book plus its unapplied fill
    result = led.reconcile(venue, {})
    assert result["fills"] == [] and result["unresolved"] == [] and result["clean"]
    assert result["updated"] == [{"tag": "s1-XLE-2026-11-12", "from": "partly_filled", "to": "filled"}]
    rec = led.order("s1-XLE-2026-11-12")
    assert rec["state"] == "filled" and rec["filled_units"] == 10.0 and rec["avg_price"] == 90.0 and rec["fee"] == 1.0


def test_partly_filled_then_a_status_with_no_amount_keeps_the_totals():
    led, venue, _ = _accepted()
    venue.set("s1-XLE-2026-11-12", state="partly_filled", filled_units=4.0, avg_price=90.0, fee=0.4)
    led.reconcile(venue, {})
    venue.set("s1-XLE-2026-11-12", state="partly_filled", filled_units=None, avg_price=None, fee=None)
    venue.held = {"XLE": 4.0}                                           # the book plus its unapplied fill
    result = led.reconcile(venue, {})
    assert result["anomalies"] == [] and result["fills"] == [] and result["clean"]
    rec = led.order("s1-XLE-2026-11-12")
    assert rec["state"] == "partly_filled" and rec["filled_units"] == 4.0 and rec["avg_price"] == 90.0 and rec["fee"] == 0.4


def test_accepted_then_partly_filled_with_no_amount_moves_state_only():
    led, venue, _ = _accepted()
    venue.set("s1-XLE-2026-11-12", state="partly_filled", filled_units=None)
    result = led.reconcile(venue, {})
    assert result["fills"] == [] and result["anomalies"] == []
    rec = led.order("s1-XLE-2026-11-12")
    assert rec["state"] == "partly_filled" and rec["filled_units"] == 0.0 and rec["avg_price"] is None


def test_cancelled_with_no_amount_after_a_partial_fill_keeps_the_totals():
    led, venue, _ = _accepted()
    venue.set("s1-XLE-2026-11-12", state="partly_filled", filled_units=4.0, avg_price=90.0, fee=0.4)
    led.reconcile(venue, {})
    venue.set("s1-XLE-2026-11-12", state="cancelled", filled_units=None, avg_price=None, fee=None)
    result = led.reconcile(venue, {})
    assert result["anomalies"] == [] and result["fills"] == [] and result["unresolved"] == []
    rec = led.order("s1-XLE-2026-11-12")
    assert rec["state"] == "cancelled" and rec["filled_units"] == 4.0 and rec["avg_price"] == 90.0 and rec["fee"] == 0.4


def test_submit_once_passes_max_age_seconds_to_blocked():
    clock = Clock()
    led, venue = ledger(clock=clock), FakeVenue()
    ready(led, venue)
    intend(led, tag="a")
    intend(led, tag="b")
    clock.t += 60
    with pytest.raises(OrdersBlocked) as err:
        led.submit_once(venue, "a", max_age_seconds=30)
    assert "old" in err.value.reasons[0] and venue.calls == [] and led.order("a")["state"] == "intended"
    assert led.submit_once(venue, "a", max_age_seconds=60)["state"] == "accepted"
    assert led.submit_once(venue, "b")["state"] == "accepted"          # default limit is 6 h


# ---- rev 3: several ledgers on one file, durable fills, prices and fees, rechecks


CORE = ("tag", "symbol", "units", "price", "fee")


def core(fill):
    return {k: fill[k] for k in CORE}


class FileVenue:
    """A venue for child processes: every submit appends one line to `calls` in its directory."""
    name = "file"

    def __init__(self, directory):
        self.calls = Path(directory) / "calls"

    def submit(self, symbol, units, tag, price=None, limit_price=None):
        fd = os.open(self.calls, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        try:
            os.write(fd, f"{os.getpid()} {tag}\n".encode())
        finally:
            os.close(fd)
        time.sleep(0.002)
        return {"status": "accepted", "venue_order_id": "V"}

    def order_status(self, tag):
        return None

    def positions(self):
        return {}


def _race_worker(paths, barrier, errors):
    for path in paths:
        try:
            barrier.wait(timeout=30)
            led = OrderLedger(path)
            try:
                led.submit_once(FileVenue(Path(path).parent), "race")
            except OrdersBlocked:
                pass                                         # the other process's order is in flight: no call
            led.close()
        except BaseException as exc:                         # every failure is reported to the parent
            Path(errors, f"{os.getpid()}-{Path(path).parent.name}").write_text(f"{type(exc).__name__}: {exc}")


def test_two_processes_racing_submit_once_make_one_venue_call_in_every_trial(tmp_path):
    trials = 50
    paths = []
    for i in range(trials):
        d = tmp_path / f"t{i}"
        led = OrderLedger(d / "orders.sqlite")
        led.reconcile(FileVenue(d), {})
        led.intend("race", "XLE", 10.0, 90.0)
        led.close()
        paths.append(str(d / "orders.sqlite"))
    errors = tmp_path / "errors"
    errors.mkdir()
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    procs = [ctx.Process(target=_race_worker, args=(paths, barrier, str(errors))) for _ in range(2)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
    assert [p.exitcode for p in procs] == [0, 0]
    assert sorted(e.read_text() for e in errors.iterdir()) == []
    counts = [len((Path(p).parent / "calls").read_text().splitlines()) if (Path(p).parent / "calls").exists() else 0
              for p in paths]
    assert counts == [1] * trials
    for p in paths:
        led = OrderLedger(p)
        assert led.order("race")["state"] == "accepted"
        assert [(e["from"], e["to"]) for e in led.events("race")] == [(None, "intended"), ("intended", "sent"), ("sent", "accepted")]
        assert led.attention(open_only=False) == []                      # the loser left nothing for a person to settle
        led.close()


def test_a_stale_object_cannot_move_an_order_backwards(tmp_path):
    path = tmp_path / "orders.sqlite"
    clock = Clock()
    a, b = ledger(path, clock), ledger(path, clock)

    def b_settles_it(tag):                                   # while a's submit is in flight, b reconciles it to filled
        venue.set(tag, state="filled", filled_units=10.0, avg_price=90.0, fee=0.0)
        b.reconcile(venue, {})

    venue = FakeVenue(mode="accept")
    ready(a, venue)
    intend(a)
    venue.on_submit = lambda tag: (venue._take("XLE", 10.0, tag), b_settles_it(tag))
    venue.mode = "raise"                                    # a then sees an exception: must not write unknown over filled
    rec = a.submit_once(venue, "s1-XLE-2026-11-12")
    assert rec["state"] == "filled" and a.order("s1-XLE-2026-11-12")["state"] == "filled"
    assert [(e["from"], e["to"]) for e in a.events("s1-XLE-2026-11-12")] == [
        (None, "intended"), ("intended", "sent"), ("sent", "filled")]
    assert len(venue.calls) == 1


def test_a_stale_accepted_result_does_not_overwrite_a_fill(tmp_path):
    path = tmp_path / "orders.sqlite"
    clock = Clock()
    a, b = ledger(path, clock), ledger(path, clock)
    venue = FakeVenue(mode="accept")
    ready(a, venue)
    intend(a)

    def settle(tag):
        venue._take("XLE", 10.0, tag)
        venue.set(tag, state="filled", filled_units=10.0, avg_price=90.0, fee=0.0)
        b.reconcile(venue, {})

    venue.on_submit = settle
    assert a.submit_once(venue, "s1-XLE-2026-11-12")["state"] == "filled"
    assert a.events("s1-XLE-2026-11-12")[-1]["to"] == "filled"


class RacingVenue(FakeVenue):
    """On the first status call, another ledger reconciles first (it finishes before this one goes on)."""

    def __init__(self):
        super().__init__()
        self.other = None
        self.other_result = None

    def order_status(self, tag):
        got = super().order_status(tag)
        other, self.other = self.other, None
        if other is not None:
            self.other_result = other.reconcile(self, {})
        return got


def test_two_reconciles_racing_create_one_fill_row(tmp_path):
    path = tmp_path / "orders.sqlite"
    clock = Clock()
    a, b = ledger(path, clock), ledger(path, clock)
    venue = RacingVenue()
    ready(a, venue)
    intend(a)
    a.submit_once(venue, "s1-XLE-2026-11-12")
    venue.set("s1-XLE-2026-11-12", state="partly_filled", filled_units=4.0, avg_price=90.0, fee=0.4)
    venue.other = b
    first = a.reconcile(venue, {})
    assert first["fills"] == []
    rows = a.fills()
    assert len(rows) == 1 and core(rows[0]) == {"tag": "s1-XLE-2026-11-12", "symbol": "XLE", "units": 4.0, "price": 90.0, "fee": 0.4}
    assert [f["id"] for f in venue.other_result["fills"]] == [rows[0]["id"]]


def test_a_non_numeric_average_on_one_order_does_not_lose_the_others_fills():
    clock = Clock()
    led, venue = ledger(clock=clock), FakeVenue()
    ready(led, venue)
    for t in ("a", "b", "c"):
        intend(led, tag=t)
        led.submit_once(venue, t)
    venue.set("a", state="partly_filled", filled_units=2.0, avg_price=90.0, fee=0.1)
    venue.set("b", state="partly_filled", filled_units="three", avg_price=90.5, fee=0.1)   # rev 6: an amount, not an average
    venue.set("c", state="partly_filled", filled_units=5.0, avg_price=91.0, fee=0.2)
    clock.t += 5
    result = led.reconcile(venue, {})
    assert [f["tag"] for f in result["fills"]] == ["a", "c"] and not result["clean"]
    assert [x["tag"] for x in result["anomalies"]] == ["b"]
    assert led.order("b")["filled_units"] == 0.0 and led.order("b")["state"] == "accepted"
    assert led.last_reconcile()["time"] == clock.t and len(led.last_reconcile()["summary"]["fills"]) == 2
    assert [f["tag"] for f in led.unapplied_fills()] == ["a", "c"]


@pytest.mark.parametrize("field, value", [("filled_units", "4"), ("filled_units", float("nan")),
                                          ("filled_units", float("inf"))])
def test_a_present_but_non_finite_amount_is_an_anomaly_and_changes_nothing(field, value):
    led, venue, _ = _accepted()
    venue.set("s1-XLE-2026-11-12", state="partly_filled", filled_units=4.0, avg_price=90.0, fee=0.4)
    venue.set("s1-XLE-2026-11-12", **{field: value})
    before = led.order("s1-XLE-2026-11-12")
    result = led.reconcile(venue, {})
    assert [x["tag"] for x in result["anomalies"]] == ["s1-XLE-2026-11-12"] and result["fills"] == []
    assert led.order("s1-XLE-2026-11-12") == before and led.fills() == []


def test_fee_and_price_sequences_with_reports_that_lack_them():
    led, venue, _ = _accepted()
    tag = "s1-XLE-2026-11-12"
    venue.set(tag, state="partly_filled", filled_units=4.0, avg_price=100.0, fee=1.0)
    (f1,) = led.reconcile(venue, {})["fills"]
    venue.set(tag, state="partly_filled", filled_units=6.0, avg_price=None, fee=None)
    (f2,) = led.reconcile(venue, {})["fills"]
    venue.set(tag, state="filled", filled_units=10.0, avg_price=110.0, fee=3.0)
    (f3,) = led.reconcile(venue, {})["fills"]
    assert [f["units"] for f in (f1, f2, f3)] == [4.0, 2.0, 4.0]
    assert [f["fee"] for f in (f1, f2, f3)] == [1.0, None, 2.0]
    assert f1["price"] == 100.0 and f1["price_covers_units"] == 4.0
    assert f2["price"] is None and f2["price_covers_units"] is None
    assert f3["price"] == pytest.approx(116.66666666666667) and f3["price_covers_units"] == 6.0
    priced = [f for f in (f1, f2, f3) if f["price"] is not None]
    assert sum(f["price"] * f["price_covers_units"] for f in priced) == pytest.approx(110.0 * 10)
    assert sum(f["price_covers_units"] for f in priced) == 10.0
    rec = led.order(tag)
    assert rec["state"] == "filled" and rec["fee"] == 3.0 and rec["avg_price"] == 110.0
    assert [f["id"] for f in led.fills(tag)] == sorted(f["id"] for f in (f1, f2, f3))


def test_fee_increases_sum_to_the_last_fee_total_over_random_sequences():
    rng = random.Random(7)
    for _ in range(200):
        led, venue, _ = _accepted()
        tag, total, cost, last_fee, last_avg_point = "s1-XLE-2026-11-12", 0.0, 0.0, None, None
        while total < 10.0:
            inc = min(10.0 - total, rng.choice([0.5, 1.0, 2.5]))
            total, cost = total + inc, cost + inc * round(rng.uniform(90, 110), 2)   # averages from real prices
            fee = None if rng.random() < 0.4 else round(rng.uniform(0, 1), 3) + (last_fee or 0.0)
            avg = None if (rng.random() < 0.4 and total < 10.0) else cost / total
            last_fee = fee if fee is not None else last_fee
            last_avg_point = (total, avg) if avg is not None else last_avg_point
            venue.set(tag, state="filled" if total >= 10.0 else "partly_filled", filled_units=total, avg_price=avg, fee=fee)
            led.reconcile(venue, {})
        rows = led.fills(tag)
        assert math.fsum(r["units"] for r in rows) == pytest.approx(10.0)
        assert math.fsum(r["fee"] for r in rows if r["fee"] is not None) == pytest.approx(last_fee or 0.0)
        priced = [r for r in rows if r["price"] is not None]
        assert math.fsum(r["price"] * r["price_covers_units"] for r in priced) == pytest.approx(last_avg_point[1] * 10.0)
        assert math.fsum(r["price_covers_units"] for r in priced) == pytest.approx(10.0)


def test_unapplied_fills_mark_applied_and_the_blocked_reason():
    clock = Clock()
    led, venue = ledger(clock=clock), FakeVenue()
    ready(led, venue)
    intend(led, tag="a")
    intend(led, tag="b")
    led.submit_once(venue, "a")
    led.submit_once(venue, "b")
    venue.set("a", state="filled", filled_units=10.0, avg_price=90.0, fee=0.0)
    venue.set("b", state="partly_filled", filled_units=3.0, avg_price=91.0, fee=0.0)
    venue.held = {"XLE": 13.0}
    result = led.reconcile(venue, {})
    assert result["clean"]
    reasons = led.blocked()
    assert len(reasons) == 1 and "2 fills" in reasons[0] and "applied" in reasons[0]
    ids = [f["id"] for f in led.unapplied_fills()]
    assert ids == sorted(ids) and [f["tag"] for f in led.unapplied_fills()] == ["a", "b"]
    assert led.mark_applied(ids[:1]) == 1
    assert "1 fill " in led.blocked()[0]
    assert led.mark_applied(ids) == 1 and led.mark_applied(ids) == 0 and led.mark_applied([999_999]) == 0
    assert led.unapplied_fills() == [] and led.blocked() == []
    assert all(f["applied"] == 1 and f["applied_time"] == clock.t for f in led.fills())


def test_the_position_comparison_counts_unapplied_fills():
    led, venue, _ = _accepted()
    venue.set("s1-XLE-2026-11-12", state="partly_filled", filled_units=4.0, avg_price=90.0, fee=0.0)
    venue.held = {"XLE": 4.0}
    assert led.reconcile(venue, {})["clean"]                            # the book does not hold the fill yet
    wrong = led.reconcile(venue, {"XLE": 4.0})                           # holding it and an unapplied row counts it twice
    assert not wrong["clean"] and wrong["position_differences"]["XLE"]["venue"] == 4.0
    led.mark_applied([f["id"] for f in led.unapplied_fills()])
    assert led.reconcile(venue, {"XLE": 4.0})["clean"]


def test_a_clock_behind_the_latest_reconcile_blocks():
    clock = Clock()
    led, venue = ledger(clock=clock), FakeVenue()
    ready(led, venue)
    clock.t -= 1
    reasons = led.blocked()
    assert len(reasons) == 1 and "clock is behind" in reasons[0]


def test_a_never_arrived_order_that_later_appears_is_an_anomaly_moves_and_creates_its_fills(tmp_path):
    clock = Clock()
    led, venue = _crash_and_reopen(tmp_path, "crash_before", clock)
    clock.t += 121
    led.reconcile(venue, {})
    assert led.order("s1-XLE-2026-11-12")["state"] == "never_arrived"
    venue._take("XLE", 10.0, "s1-XLE-2026-11-12")                     # the venue was slow: it shows up after all
    venue.set("s1-XLE-2026-11-12", state="filled", filled_units=10.0, avg_price=90.0, fee=0.5)
    clock.t += 3600
    result = led.reconcile(venue, {})
    assert not result["clean"] and [a["tag"] for a in result["anomalies"]] == ["s1-XLE-2026-11-12"]
    assert led.order("s1-XLE-2026-11-12")["state"] == "filled"
    assert [core(f) for f in result["fills"]] == [{"tag": "s1-XLE-2026-11-12", "symbol": "XLE", "units": 10.0, "price": 90.0, "fee": 0.5}]
    last = led.events("s1-XLE-2026-11-12")[-1]
    assert (last["from"], last["to"]) == ("never_arrived", "filled") and "never arrived" in last["note"]


def test_a_never_arrived_order_that_does_not_appear_is_left_alone_and_stops_being_asked(tmp_path):
    clock = Clock()
    led, venue = _crash_and_reopen(tmp_path, "crash_before", clock)
    clock.t += 121
    led.reconcile(venue, {})
    venue.status_calls.clear()
    clock.t += 86400
    result = led.reconcile(venue, {})
    assert result["clean"] and venue.status_calls == ["s1-XLE-2026-11-12"] and result["unresolved"] == []
    clock.t += 259200 - 86400 + 1                                        # just over 3 days after it was marked
    venue.status_calls.clear()
    assert led.reconcile(venue, {})["clean"] and venue.status_calls == []
    assert led.order("s1-XLE-2026-11-12")["state"] == "never_arrived"


def test_settle_by_hand_moves_an_unknown_order_to_never_arrived_and_the_recheck_still_applies():
    clock = Clock()
    led, venue = ledger(clock=clock), FakeVenue(mode="raise")
    ready(led, venue)
    intend(led)
    led.submit_once(venue, "s1-XLE-2026-11-12")
    with pytest.raises(OrderError):
        led.settle_by_hand("s1-XLE-2026-11-12", "", "looked in the app")
    with pytest.raises(OrderError):
        led.settle_by_hand("s1-XLE-2026-11-12", "Burton", " ")
    rec = led.settle_by_hand("s1-XLE-2026-11-12", "Burton", "not in the Webull app's order list")
    assert rec["state"] == "never_arrived"
    last = led.events("s1-XLE-2026-11-12")[-1]
    assert (last["from"], last["to"]) == ("unknown", "never_arrived")
    assert "Burton" in last["note"] and "not in the Webull app's order list" in last["note"]
    assert led.reconcile(venue, {})["clean"] and led.blocked() == []
    with pytest.raises(OrderError):
        led.settle_by_hand("s1-XLE-2026-11-12", "Burton", "again")
    venue._take("XLE", 10.0, "s1-XLE-2026-11-12")
    assert not led.reconcile(venue, {})["clean"] and led.order("s1-XLE-2026-11-12")["state"] == "accepted"


def test_settle_by_hand_refuses_an_order_whose_fate_is_known():
    led, venue, _ = _accepted()
    with pytest.raises(OrderError):
        led.settle_by_hand("s1-XLE-2026-11-12", "Burton", "x")
    with pytest.raises(OrderError):
        led.settle_by_hand("nope", "Burton", "x")


@pytest.mark.parametrize("held, book", [({"XLE": float("nan")}, {"XLE": 1.0}), ({"XLE": 1.0}, {"XLE": float("nan")}),
                                        ({"XLE": "ten"}, {}), ({}, {"XLE": None})])
def test_a_position_that_is_not_a_finite_number_is_a_difference(held, book):
    led, venue = ledger(), FakeVenue()
    venue.held = held
    result = led.reconcile(venue, book)
    assert not result["clean"] and list(result["position_differences"]) == ["XLE"]


def test_intend_refuses_an_order_below_the_size_floor():
    led = ledger()
    with pytest.raises(OrderError):
        intend(led, tag="tiny", units=5e-10)
    with pytest.raises(OrderError):
        intend(led, tag="tiny2", units=-9.9e-9)
    assert intend(led, tag="small", units=1e-8)["units"] == 1e-8


def test_a_tiny_order_called_filled_with_nothing_filled_is_unresolved():
    led, venue = ledger(), FakeVenue()
    ready(led, venue)
    intend(led, tag="small", units=1e-8)
    led.submit_once(venue, "small")
    venue.set("small", state="filled", filled_units=0.0)
    result = led.reconcile(venue, {})
    assert [u["tag"] for u in result["unresolved"]] == ["small"] and led.order("small")["state"] == "accepted"


def test_the_fill_tolerance_is_relative_to_the_order_size_at_1e8_units():
    led, venue = ledger(), FakeVenue()
    ready(led, venue)
    intend(led, tag="big", units=1e8)
    intend(led, tag="big2", units=1e8)
    led.submit_once(venue, "big")
    led.submit_once(venue, "big2")
    venue.set("big", state="filled", filled_units=math.nextafter(1e8, math.inf), avg_price=1.0)
    venue.set("big2", state="filled", filled_units=1e8 * (1 - 1e-10), avg_price=1.0)
    venue.held = {"XLE": 2e8}
    result = led.reconcile(venue, {})
    assert result["anomalies"] == [] and result["unresolved"] == []
    assert led.order("big")["state"] == "filled" and led.order("big2")["state"] == "filled"


def test_an_exception_whose_str_raises_still_gives_unknown():
    class BadStr(Exception):
        def __str__(self):
            raise RuntimeError("no text")

    class Venue(FakeVenue):
        def submit(self, *a, **k):
            self.calls.append(a)
            raise BadStr()

    led, venue = ledger(), Venue()
    ready(led, venue)
    intend(led)
    rec = led.submit_once(venue, "s1-XLE-2026-11-12")
    assert rec["state"] == "unknown" and rec["note"].startswith("BadStr")


def test_fill_rows_can_only_be_marked_applied(tmp_path):
    path = tmp_path / "orders.sqlite"
    led, venue, _ = _accepted()
    led = ledger(path)
    venue = FakeVenue()
    ready(led, venue)
    intend(led)
    led.submit_once(venue, "s1-XLE-2026-11-12")
    venue.set("s1-XLE-2026-11-12", state="partly_filled", filled_units=4.0, avg_price=90.0, fee=0.0)
    led.reconcile(venue, {})
    (row,) = led.fills()
    raw = sqlite3.connect(str(path))
    for sql in ("UPDATE fills SET units = 5", "DELETE FROM fills", "UPDATE fills SET applied = 1, price = 1"):
        with pytest.raises(sqlite3.DatabaseError):
            raw.execute(sql)
    raw.rollback()
    raw.close()
    assert led.mark_applied([row["id"]]) == 1
    raw = sqlite3.connect(str(path))
    with pytest.raises(sqlite3.DatabaseError):
        raw.execute("UPDATE fills SET applied = 0")
    raw.close()


REV3_LAYOUT = (
    "CREATE TABLE orders (tag TEXT PRIMARY KEY, symbol TEXT NOT NULL, units REAL NOT NULL, reference_price REAL NOT NULL, "
    "limit_price REAL, batch TEXT, parts TEXT, state TEXT NOT NULL, created REAL NOT NULL, sent_at REAL, updated REAL NOT NULL, "
    "venue TEXT, venue_order_id TEXT, filled_units REAL NOT NULL DEFAULT 0, avg_price REAL, fee REAL, raw_status TEXT, note TEXT);"
    "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT NOT NULL, time REAL NOT NULL, from_state TEXT, "
    "to_state TEXT NOT NULL, note TEXT);"
    "INSERT INTO orders (tag, symbol, units, reference_price, state, created, updated) VALUES ('old', 'XLE', 10, 90, 'accepted', 1, 1);")


@pytest.mark.parametrize("version", [0, 99])
def test_a_ledger_file_with_another_layout_version_is_refused(tmp_path, version):
    path = tmp_path / "old.sqlite"
    raw = sqlite3.connect(str(path))
    raw.executescript(REV3_LAYOUT + f"PRAGMA user_version = {version};")
    raw.close()
    with pytest.raises(OrderError, match="different version of this module"):
        ledger(path)
    raw = sqlite3.connect(str(path))
    assert raw.execute("PRAGMA user_version").fetchone()[0] == version          # nothing about the file was changed
    assert [r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE name = 'fills'")] == []
    raw.close()


def test_a_new_ledger_file_carries_the_current_layout_version_and_reopens(tmp_path):
    path = tmp_path / "orders.sqlite"
    ledger(path).close()
    raw = sqlite3.connect(str(path))
    assert raw.execute("PRAGMA user_version").fetchone()[0] == orders.LAYOUT_VERSION >= 1
    raw.close()
    intend(ledger(path))


# ---- rev 4: fee and price from every report, late reports, late acceptance, failed writes


TAG = "s1-XLE-2026-11-12"


def test_a_fee_that_rises_without_units_makes_an_adjustment_row():
    led, venue, _ = _accepted()
    venue.set(TAG, state="partly_filled", filled_units=4.0, avg_price=None, fee=1.0)
    led.reconcile(venue, {})
    venue.set(TAG, state="partly_filled", filled_units=4.0, fee=1.5)
    second = led.reconcile(venue, {})
    venue.set(TAG, state="cancelled", filled_units=4.0, fee=1.5)
    third = led.reconcile(venue, {})
    rows = led.fills(TAG)
    assert [(r["units"], r["fee"]) for r in rows] == [(4.0, 1.0), (0.0, 0.5)]
    assert [f["id"] for f in second["fills"]] == [rows[1]["id"]] and third["fills"] == []
    assert led.order(TAG)["fee"] == 1.5 and led.order(TAG)["state"] == "cancelled"
    assert [f["id"] for f in led.unapplied_fills()] == [r["id"] for r in rows] and "2 fills" in led.blocked()[0]


def test_a_late_average_for_filled_units_makes_a_priced_adjustment_row():
    led, venue, _ = _accepted()
    venue.set(TAG, state="partly_filled", filled_units=4.0, avg_price=None, fee=None)
    led.reconcile(venue, {})
    venue.set(TAG, state="partly_filled", filled_units=4.0, avg_price=100.0)
    (row,) = led.reconcile(venue, {})["fills"]
    assert (row["units"], row["price"], row["price_covers_units"], row["fee"]) == (0.0, 100.0, 4.0, None)
    assert led.reconcile(venue, {})["fills"] == []                              # the same average again: nothing new
    rec = led.order(TAG)
    assert rec["avg_price"] == 100.0 and rec["priced_units"] == 4.0 and rec["filled_units"] == 4.0


def test_an_average_without_an_amount_is_not_used():
    led, venue, _ = _accepted()
    venue.set(TAG, state="partly_filled", filled_units=4.0, avg_price=None)
    led.reconcile(venue, {})
    venue.set(TAG, state="partly_filled", filled_units=None, avg_price=100.0)  # which units does it cover? unknown
    assert led.reconcile(venue, {})["fills"] == [] and led.order(TAG)["priced_units"] == 0.0


def test_a_fee_total_that_falls_beside_a_rise_in_units_drops_only_the_fee():
    # T34b-3 (A3): a fall that stays at or above zero is a notice and the order's total follows; the row has no fee
    led, venue, _ = _accepted()
    venue.set(TAG, state="partly_filled", filled_units=4.0, avg_price=90.0, fee=1.0)
    led.reconcile(venue, {})
    venue.set(TAG, state="partly_filled", filled_units=6.0, avg_price=90.0, fee=0.5)
    result = led.reconcile(venue, {})
    assert result["anomalies"] == [] and [n["kind"] for n in result["notices"]] == ["fee_fell"]
    (row,) = result["fills"]
    assert (row["units"], row["fee"], row["price"], row["price_covers_units"]) == (2.0, None, 90.0, 2.0)
    rec = led.order(TAG)
    assert rec["filled_units"] == 6.0 and rec["fee"] == 0.5
    venue.set(TAG, state="partly_filled", filled_units=6.0, avg_price=90.0, fee=0.5)
    again = led.reconcile(venue, {})                                   # the same lower total: nothing new
    assert again["fills"] == [] and again["anomalies"] == [] and again["notices"] == []
    venue.set(TAG, state="partly_filled", filled_units=7.0, avg_price=90.0, fee=1.25)
    (row,) = led.reconcile(venue, {})["fills"]
    assert (row["units"], row["fee"]) == (1.0, 0.75) and led.order(TAG)["fee"] == 1.25


def test_an_average_that_implies_a_negative_price_beside_a_rise_in_units_drops_only_the_price():
    led, venue, _ = _accepted()
    venue.set(TAG, state="partly_filled", filled_units=4.0, avg_price=100.0, fee=0.0)
    led.reconcile(venue, {})
    venue.set(TAG, state="partly_filled", filled_units=6.0, avg_price=50.0, fee=0.0)  # 2 new units at -50 each
    result = led.reconcile(venue, {})
    assert [a["tag"] for a in result["anomalies"]] == [TAG] and not result["clean"]
    (row,) = result["fills"]
    assert (row["units"], row["price"], row["price_covers_units"], row["fee"]) == (2.0, None, None, 0.0)
    rec = led.order(TAG)
    assert (rec["filled_units"], rec["priced_units"], rec["priced_avg"], rec["avg_price"]) == (6.0, 4.0, 100.0, 100.0)
    venue.set(TAG, state="partly_filled", filled_units=6.0, avg_price=100.0)       # a sane average prices them later
    (row,) = led.reconcile(venue, {})["fills"]
    assert (row["units"], row["price"], row["price_covers_units"]) == (0.0, 100.0, 2.0)


def test_fee_and_average_after_filled_are_taken_from_a_late_report():
    clock = Clock()
    led, venue = ledger(clock=clock), FakeVenue()
    ready(led, venue)
    intend(led)
    led.submit_once(venue, TAG)
    venue.set(TAG, state="filled", filled_units=10.0, avg_price=None, fee=None)
    led.reconcile(venue, {})
    assert led.order(TAG)["state"] == "filled"
    clock.t += 3600
    venue.set(TAG, state="filled", filled_units=10.0, avg_price=90.0, fee=2.0)
    venue.status_calls.clear()
    result = led.reconcile(venue, {})
    assert venue.status_calls == [TAG] and result["anomalies"] == []
    (row,) = result["fills"]
    assert (row["units"], row["price"], row["price_covers_units"], row["fee"]) == (0.0, 90.0, 10.0, 2.0)
    assert led.order(TAG)["fee"] == 2.0 and led.order(TAG)["state"] == "filled"


def test_fee_and_average_after_a_cancel_with_a_partial_fill_are_taken():
    led, venue, _ = _accepted()
    venue.set(TAG, state="partly_filled", filled_units=4.0, avg_price=None, fee=0.4)
    led.reconcile(venue, {})
    venue.set(TAG, state="cancelled", filled_units=4.0)
    led.reconcile(venue, {})
    venue.set(TAG, state="cancelled", filled_units=4.0, avg_price=100.0, fee=0.6)
    (row,) = led.reconcile(venue, {})["fills"]
    assert (row["units"], row["price"], row["price_covers_units"], row["fee"]) == (0.0, 100.0, 4.0, pytest.approx(0.2))
    assert led.order(TAG)["state"] == "cancelled"


def test_a_finished_order_is_not_asked_again_after_recheck_seconds():
    clock = Clock()
    led, venue = ledger(clock=clock), FakeVenue()
    ready(led, venue)
    intend(led)
    led.submit_once(venue, TAG)
    venue.set(TAG, state="filled", filled_units=10.0, avg_price=90.0, fee=None)
    led.reconcile(venue, {})
    clock.t += 259200 + 1
    venue.set(TAG, fee=2.0)
    venue.status_calls.clear()
    result = led.reconcile(venue, {})
    assert venue.status_calls == [] and result["fills"] == [] and led.order(TAG)["fee"] is None


def test_a_finished_order_with_nothing_filled_is_not_asked_again():
    led, venue, _ = _accepted()
    venue.set(TAG, state="cancelled", filled_units=0.0)
    led.reconcile(venue, {})
    venue.status_calls.clear()
    led.reconcile(venue, {})
    assert venue.status_calls == []


@pytest.mark.parametrize("change", [{"filled_units": 3.0}, {"state": "filled", "filled_units": 4.0}, {"state": "expired"}])
def test_a_smaller_amount_or_a_changed_state_on_a_finished_order_is_an_anomaly(change):
    led, venue, _ = _accepted()
    venue.set(TAG, state="partly_filled", filled_units=4.0, avg_price=90.0, fee=0.4)
    led.reconcile(venue, {})
    venue.set(TAG, state="cancelled")
    led.reconcile(venue, {})
    before, rows = led.order(TAG), led.fills()
    venue.set(TAG, **change)
    result = led.reconcile(venue, {})
    assert [a["tag"] for a in result["anomalies"]] == [TAG] and not result["clean"]
    assert led.order(TAG) == before and led.fills() == rows


def test_a_status_error_on_a_finished_order_goes_to_recheck_errors():
    led, venue, _ = _accepted()
    venue.set(TAG, state="filled", filled_units=10.0, avg_price=90.0, fee=0.0)
    venue.held = {"XLE": 10.0}
    led.reconcile(venue, {})
    venue.status_raises.add(TAG)
    result = led.reconcile(venue, {})
    assert result["clean"] and [e["tag"] for e in result["recheck_errors"]] == [TAG]


def test_fee_and_price_totals_hold_when_reports_repeat_the_same_amount():
    rng = random.Random(4)
    for _ in range(400):
        led, venue, clock = _accepted()
        total = cost = fee_total = 0.0
        last_fee = last_avg_point = None
        for _step in range(rng.randint(2, 16)):
            if total < 10.0 and rng.random() < 0.45:
                inc = min(10.0 - total, rng.choice([0.5, 1.0, 2.5, 4.0]))
                total, cost = total + inc, cost + inc * round(rng.uniform(90, 110), 2)
            if rng.random() < 0.4:
                fee_total += round(rng.uniform(0, 0.5), 3)
            units = total if rng.random() < 0.85 else None
            avg = cost / total if (total > 0 and rng.random() < 0.5) else None
            fee = fee_total if rng.random() < 0.6 else None
            venue.set(TAG, state="filled" if total >= 10.0 else ("partly_filled" if total > 0 else "open"),
                      filled_units=units, avg_price=avg, fee=fee)
            clock.t += 60
            result = led.reconcile(venue, {})
            assert result["anomalies"] == [], result["anomalies"]
            if fee is not None:
                last_fee = fee
            if avg is not None and units is not None:
                last_avg_point = (avg, units)
        rows, rec = led.fills(TAG), led.order(TAG)
        assert math.fsum(r["units"] for r in rows) == pytest.approx(rec["filled_units"])
        assert math.fsum(r["fee"] for r in rows if r["fee"] is not None) == pytest.approx(last_fee or 0.0)
        priced = [r for r in rows if r["price"] is not None]
        if last_avg_point is not None:
            avg, at = last_avg_point
            assert math.fsum(r["price"] * r["price_covers_units"] for r in priced) == pytest.approx(avg * at)
            assert math.fsum(r["price_covers_units"] for r in priced) == pytest.approx(at)
            assert all(r["price"] >= 0 for r in priced)


def test_a_late_acceptance_wins_over_never_arrived_set_while_the_submit_was_in_flight(tmp_path):
    path, clock = tmp_path / "orders.sqlite", Clock()
    a, b = ledger(path, clock), ledger(path, clock)
    venue = FakeVenue(on_submit=lambda tag: b.settle_by_hand(tag, "Burton", "not in the app yet"))
    ready(a, venue)
    intend(a)
    rec = a.submit_once(venue, TAG)
    assert rec["state"] == "accepted" and rec["venue_order_id"] == f"V-{TAG}"
    last = a.events(TAG)[-1]
    assert (last["from"], last["to"]) == ("never_arrived", "accepted") and "late" in last["note"]


def test_a_late_acceptance_wins_over_unknown(tmp_path):
    path, clock = tmp_path / "orders.sqlite", Clock()
    a = ledger(path, clock)

    def mark_unknown(tag):                                  # no public path does this mid-flight; a raw write stands in
        raw = sqlite3.connect(str(path))
        raw.execute("UPDATE orders SET state = 'unknown' WHERE tag = ?", (tag,))
        raw.commit()
        raw.close()

    venue = FakeVenue(on_submit=mark_unknown)
    ready(a, venue)
    intend(a)
    rec = a.submit_once(venue, TAG)
    assert rec["state"] == "accepted" and rec["venue_order_id"] == f"V-{TAG}"
    assert (a.events(TAG)[-1]["from"], a.events(TAG)[-1]["to"]) == ("unknown", "accepted")


class HookedStatusVenue(FakeVenue):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.before_status = None

    def order_status(self, tag):
        hook, self.before_status = self.before_status, None
        if hook is not None:
            hook(tag)
        return super().order_status(tag)


def test_never_arrived_set_by_hand_during_a_reconcile_that_reads_open_is_turned_up(tmp_path):
    path, clock = tmp_path / "orders.sqlite", Clock()
    a, b = ledger(path, clock), ledger(path, clock)
    venue = HookedStatusVenue(mode="raise_after_taking")
    ready(a, venue)
    intend(a)
    assert a.submit_once(venue, TAG)["state"] == "unknown"
    venue.before_status = lambda tag: b.settle_by_hand(tag, "Burton", "looked in the app")
    result = a.reconcile(venue, {})
    assert not result["clean"] and [x["tag"] for x in result["anomalies"]] == [TAG]
    assert a.order(TAG)["state"] == "accepted"
    assert (a.events(TAG)[-1]["from"], a.events(TAG)[-1]["to"]) == ("never_arrived", "accepted")


def test_a_failed_commit_leaves_the_ledger_usable_and_other_connections_unblocked(tmp_path, monkeypatch):
    monkeypatch.setattr(orders, "BUSY_SECONDS", 0.2)
    path = tmp_path / "orders.sqlite"
    led = ledger(path)
    reader = sqlite3.connect(str(path), isolation_level=None)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM orders").fetchall()           # a read transaction held past the busy timeout
    with pytest.raises(OrderError, match="could not be written"):
        intend(led, tag="x")
    assert not led._db.in_transaction
    other = sqlite3.connect(str(path), timeout=0.2, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")                                   # the failed writer holds no lock
    other.execute("ROLLBACK")
    reader.execute("ROLLBACK")
    reader.close()
    assert led.order("x") is None and intend(led, tag="x")["state"] == "intended"
    other.execute("BEGIN IMMEDIATE")
    other.execute("ROLLBACK")
    other.close()


def test_a_failed_rollback_does_not_hide_the_original_error():
    led = ledger()
    with pytest.raises(OrderError, match="database or disk is full"):
        with led._write():
            led._db.execute("ROLLBACK")                                # sqlite has already rolled back by itself
            raise sqlite3.OperationalError("database or disk is full")
    assert not led._db.in_transaction
    assert intend(led)["state"] == "intended"


def test_mark_applied_ignores_anything_that_is_not_a_plain_int_id():
    led, venue, _ = _accepted()
    venue.set(TAG, state="partly_filled", filled_units=4.0, avg_price=90.0, fee=0.0)
    led.reconcile(venue, {})
    (row,) = led.fills()
    assert row["id"] == 1
    assert led.mark_applied([2 ** 70, -(2 ** 70), 1.9, 1.0, True, "1", None]) == 0
    assert led.unapplied_fills() == [led.fills()[0]]
    assert led.mark_applied([1]) == 1


@pytest.mark.parametrize("bad", [("notatype",), (int,), (ValueError, 3), 5])
def test_not_sent_must_hold_exception_classes(bad):
    with pytest.raises(OrderError, match="not_sent"):
        OrderLedger(":memory:", not_sent=bad)
    assert OrderLedger(":memory:", not_sent=(ValueError, LocalRefusal)).not_sent == (ValueError, LocalRefusal)


# ---- rev 5: only committed writes are reported, finished orders' late amounts, odd venue data, read errors


class LockingVenue(FakeVenue):
    """Before the status call for `lock_at`, another connection starts holding a read transaction (so that order's
    COMMIT cannot finish within the busy timeout); before the status call for `free_at`, it lets go."""

    def __init__(self, path, lock_at, free_at):
        super().__init__()
        self.path, self.lock_at, self.free_at, self.reader = path, lock_at, free_at, None

    def order_status(self, tag):
        if tag == self.lock_at and self.reader is None:
            self.reader = sqlite3.connect(str(self.path), isolation_level=None)
            self.reader.execute("BEGIN")
            self.reader.execute("SELECT COUNT(*) FROM orders").fetchall()
        if tag == self.free_at and self.reader is not None:
            self.reader.execute("ROLLBACK")
            self.reader.close()
            self.reader = None
        return super().order_status(tag)


def test_a_failed_commit_on_one_order_among_several_reports_only_what_was_written(tmp_path, monkeypatch):
    monkeypatch.setattr(orders, "BUSY_SECONDS", 0.2)
    path, clock = tmp_path / "orders.sqlite", Clock()
    led = ledger(path, clock)
    venue = LockingVenue(path, lock_at=None, free_at=None)
    ready(led, venue)
    for t in ("a", "b", "c"):
        intend(led, tag=t)
        led.submit_once(venue, t)
    for t, n in (("a", 2.0), ("b", 4.0), ("c", 6.0)):
        venue.set(t, state="partly_filled", filled_units=n, avg_price=90.0, fee=0.1)
    venue.lock_at, venue.free_at = "b", "c"
    result = led.reconcile(venue, {})
    assert [f["tag"] for f in result["fills"]] == ["a", "c"] and [u["tag"] for u in result["updated"]] == ["a", "c"]
    assert [u["tag"] for u in result["unresolved"]] == ["b"] and "could not be written" in result["unresolved"][0]["reason"]
    assert not result["clean"] and led.order("b")["filled_units"] == 0.0 and led.order("b")["state"] == "accepted"
    on_disk = led.fills()
    assert on_disk == result["fills"] == led.unapplied_fills()
    assert led.last_reconcile()["summary"]["fills"] == result["fills"]
    venue.lock_at = venue.free_at = None
    later = led.reconcile(venue, {})                                    # the lock is gone: b's fill is written now
    (row,) = later["fills"]
    assert row["tag"] == "b" and row["units"] == 4.0 and row["id"] not in [f["id"] for f in result["fills"]]
    assert [f["tag"] for f in led.fills()] == ["a", "c", "b"] and len({f["id"] for f in led.fills()}) == 3


def test_a_cancelled_order_reported_without_an_amount_gets_its_fill_when_the_amount_comes():
    led, venue, _ = _accepted()
    venue.set(TAG, state="cancelled", filled_units=None)
    led.reconcile(venue, {})
    assert led.order(TAG)["state"] == "cancelled" and led.order(TAG)["filled_units"] == 0.0
    venue.set(TAG, state="cancelled", filled_units=3.0, avg_price=90.0, fee=0.3)
    venue.held = {"XLE": 3.0}
    result = led.reconcile(venue, {})
    assert result["anomalies"] == [] and result["clean"]                 # T34b-3 (A2): a late amount is a notice
    assert [(n["tag"], n["kind"]) for n in result["notices"]] == [(TAG, "late_amount")]
    (row,) = result["fills"]
    assert (row["units"], row["price"], row["price_covers_units"], row["fee"]) == (3.0, 90.0, 3.0, 0.3)
    rec = led.order(TAG)
    assert rec["state"] == "cancelled" and rec["filled_units"] == 3.0
    assert led.reconcile(venue, {})["fills"] == []


def test_an_expired_order_whose_amount_rises_later_gets_its_fill():
    led, venue, _ = _accepted()
    venue.set(TAG, state="partly_filled", filled_units=4.0, avg_price=90.0, fee=0.0)
    led.reconcile(venue, {})
    venue.set(TAG, state="expired", filled_units=None)
    led.reconcile(venue, {})
    venue.set(TAG, state="expired", filled_units=6.0, avg_price=91.0, fee=0.0)
    result = led.reconcile(venue, {})
    (row,) = result["fills"]
    assert row["units"] == 2.0 and row["price"] == pytest.approx((91.0 * 6 - 90.0 * 4) / 2)
    assert result["anomalies"] == [] and led.order(TAG)["state"] == "expired"   # T34b-3 (A2): a notice
    assert [n["kind"] for n in result["notices"]] == ["late_amount"]


def test_a_position_key_that_is_not_a_string_is_a_positions_error_and_the_reconcile_is_recorded():
    clock = Clock()
    led, venue = ledger(clock=clock), FakeVenue()
    venue.held = {("XLE",): 1.0}
    result = led.reconcile(venue, {})
    assert not result["clean"] and "('XLE',)" in result["positions_error"]
    assert led.last_reconcile()["time"] == clock.t and led.last_reconcile()["clean"] is False


def test_a_book_position_key_that_is_not_a_string_is_a_positions_error():
    led, venue = ledger(), FakeVenue()
    result = led.reconcile(venue, {1: 1.0})
    assert not result["clean"] and result["positions_error"] and led.last_reconcile() is not None


READERS = [("order", ("a",)), ("orders", ()), ("events", ()), ("fills", ()), ("unapplied_fills", ()),
           ("last_reconcile", ()), ("blocked", ())]


@pytest.mark.parametrize("method, args", READERS)
def test_every_reading_method_raises_order_error_under_a_held_lock(tmp_path, monkeypatch, method, args):
    monkeypatch.setattr(orders, "BUSY_SECONDS", 0.2)
    path = tmp_path / "orders.sqlite"
    led = ledger(path)
    intend(led, tag="a")
    locker = sqlite3.connect(str(path), isolation_level=None)
    locker.execute("BEGIN EXCLUSIVE")                                  # no one may read until it lets go
    try:
        with pytest.raises(OrderError, match="could not be read"):
            getattr(led, method)(*args)
    finally:
        locker.execute("ROLLBACK")
        locker.close()
    getattr(led, method)(*args)


def test_the_constructor_raises_order_error_under_a_held_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(orders, "BUSY_SECONDS", 0.2)
    path = tmp_path / "orders.sqlite"
    ledger(path).close()
    locker = sqlite3.connect(str(path), isolation_level=None)
    locker.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(OrderError, match="ledger"):
            ledger(path)
    finally:
        locker.execute("ROLLBACK")
        locker.close()


def test_a_file_that_is_not_a_database_is_refused_with_order_error(tmp_path):
    path = tmp_path / "orders.sqlite"
    path.write_bytes(b"this is not a sqlite database at all, just some text " * 40)
    led = None
    with pytest.raises(OrderError, match="not a database"):
        led = ledger(path)
    assert led is None


@pytest.mark.parametrize("field, value", [("tag", "bad\ud800tag"), ("symbol", "X\udfffLE"), ("units", 10 ** 400),
                                          ("price", 10 ** 400), ("limit_price", -(10 ** 400))])
def test_intend_refuses_text_that_is_not_utf8_and_numbers_that_are_not_finite_floats(field, value):
    led = ledger()
    kw = {"tag": "t", "symbol": "XLE", "units": 10.0, "price": 90.0}
    if field == "limit_price":
        with pytest.raises(OrderError):
            intend(led, **kw, limit_price=value)
    else:
        kw[field] = value
        with pytest.raises(OrderError):
            intend(led, **kw)
    assert led.orders() == []


class WedgedRollback:
    """A connection whose ROLLBACK fails and leaves it inside its transaction."""

    def __init__(self, real):
        self.real = real

    def execute(self, sql, *args):
        if sql.strip().upper() == "ROLLBACK":
            raise sqlite3.OperationalError("cannot roll back")
        return self.real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self.real, name)


def test_a_wedged_rollback_on_a_file_ledger_reopens_its_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(orders, "BUSY_SECONDS", 0.2)
    path = tmp_path / "orders.sqlite"
    led = ledger(path)
    led._db = WedgedRollback(led._db)
    reader = sqlite3.connect(str(path), isolation_level=None)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM orders").fetchall()           # makes the COMMIT fail
    with pytest.raises(OrderError):
        intend(led, tag="x")
    assert not isinstance(led._db, WedgedRollback) and not led._db.in_transaction
    other = sqlite3.connect(str(path), timeout=0.2, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")                                   # nothing of the failed write holds a lock
    other.execute("ROLLBACK")
    other.close()
    reader.execute("ROLLBACK")
    reader.close()
    assert led.order("x") is None and intend(led, tag="x")["state"] == "intended"


def test_a_wedged_rollback_on_a_memory_ledger_says_it_must_be_discarded():
    led = ledger()
    led._db = WedgedRollback(led._db)
    with pytest.raises(OrderError, match="discarded"):
        with led._write():
            raise sqlite3.OperationalError("disk I/O error")


# ---- rev 6: attention items, bad pieces beside a rise, text from the venue, odd status objects, input checks


def _turned_up(tmp_path, clock):
    """An order declared never arrived that then turns up at the venue, filled; its fill applied to the book."""
    led, venue = _crash_and_reopen(tmp_path, "crash_before", clock)
    clock.t += 121
    led.reconcile(venue, {})
    venue._take("XLE", 10.0, TAG)
    venue.set(TAG, state="filled", filled_units=10.0, avg_price=90.0, fee=0.5)
    venue.held = {"XLE": 10.0}
    clock.t += 60
    found = led.reconcile(venue, {})
    led.mark_applied([f["id"] for f in found["fills"]])
    return led, venue, found


def test_a_turned_up_order_keeps_orders_blocked_until_a_person_closes_the_attention_item(tmp_path):
    clock = Clock()
    led, venue, found = _turned_up(tmp_path, clock)
    assert not found["clean"] and led.order(TAG)["state"] == "filled"
    (item,) = led.attention()
    assert item["tag"] == TAG and item["closed_time"] is None and "never arrived" in item["reason"]
    for _ in range(3):
        clock.t += 60
        assert led.reconcile(venue, {"XLE": 10.0})["clean"]
        reasons = led.blocked()
        assert len(reasons) == 1 and TAG in reasons[0] and "never arrived" in reasons[0]
    with pytest.raises(OrderError):
        led.close_attention(item["id"], "", "looked")
    with pytest.raises(OrderError):
        led.close_attention(item["id"], "Burton", " ")
    with pytest.raises(OrderError):
        led.close_attention(item["id"] + 100, "Burton", "no such item")
    closed = led.close_attention(item["id"], "Burton", "no replacement was sent; the 10 units are wanted")
    assert closed["closed_by"] == "Burton" and closed["closed_time"] == clock.t and "replacement" in closed["closed_note"]
    assert led.blocked() == [] and led.attention() == [] and led.attention(open_only=False) == [closed]
    with pytest.raises(OrderError):
        led.close_attention(item["id"], "Burton", "again")


def test_a_late_acceptance_over_never_arrived_opens_one_attention_item(tmp_path):
    path, clock = tmp_path / "orders.sqlite", Clock()
    a, b = ledger(path, clock), ledger(path, clock)
    venue = FakeVenue(on_submit=lambda tag: b.settle_by_hand(tag, "Burton", "not in the app yet"))
    ready(a, venue)
    intend(a)
    assert a.submit_once(venue, TAG)["state"] == "accepted"
    (item,) = a.attention()
    assert item["tag"] == TAG and "late" in item["reason"]
    assert any(TAG in r and "late" in r for r in a.blocked())


def test_a_late_acceptance_over_unknown_opens_no_attention_item(tmp_path):
    path, clock = tmp_path / "orders.sqlite", Clock()
    a = ledger(path, clock)

    def mark_unknown(tag):
        raw = sqlite3.connect(str(path))
        raw.execute("UPDATE orders SET state = 'unknown' WHERE tag = ?", (tag,))
        raw.commit()
        raw.close()

    venue = FakeVenue(on_submit=mark_unknown)
    ready(a, venue)
    intend(a)
    assert a.submit_once(venue, TAG)["state"] == "accepted" and a.attention() == []


def test_two_reconciles_racing_over_a_turned_up_order_open_one_attention_item(tmp_path):
    clock = Clock()
    path = tmp_path / "orders.sqlite"
    a, venue = _crash_and_reopen(tmp_path, "crash_before", clock)
    clock.t += 121
    a.reconcile(venue, {})
    b = ledger(path, clock)
    racing = RacingVenue()
    racing.book = venue.book
    racing._take("XLE", 10.0, TAG)
    racing.set(TAG, state="partly_filled", filled_units=4.0, avg_price=90.0, fee=0.0)
    racing.other = b
    a.reconcile(racing, {})
    assert len(a.attention()) == 1 and len(a.fills()) == 1
    a.reconcile(racing, {})                                            # found again while the item is open: still one
    assert len(a.attention(open_only=False)) == 1


def test_attention_rows_are_never_deleted_and_only_closed(tmp_path):
    clock = Clock()
    led, venue, _ = _turned_up(tmp_path, clock)
    raw = sqlite3.connect(str(tmp_path / "orders.sqlite"))
    for sql in ("DELETE FROM attention", "UPDATE attention SET reason = 'edited'",
                "UPDATE attention SET closed_time = 1"):
        with pytest.raises(sqlite3.DatabaseError):
            raw.execute(sql)
    raw.rollback()
    raw.close()
    (item,) = led.attention()
    led.close_attention(item["id"], "Burton", "checked")
    raw = sqlite3.connect(str(tmp_path / "orders.sqlite"))
    with pytest.raises(sqlite3.DatabaseError):
        raw.execute("UPDATE attention SET closed_time = NULL, closed_by = NULL, closed_note = NULL")
    raw.close()


@pytest.mark.parametrize("field, value", [("avg_price", float("nan")), ("avg_price", float("inf")), ("avg_price", "x"),
                                          ("fee", float("nan")), ("fee", float("-inf")), ("fee", "x")])
def test_a_bad_average_or_fee_beside_a_rise_in_units_drops_only_that_piece(field, value):
    led, venue, _ = _accepted()
    venue.set(TAG, state="partly_filled", filled_units=4.0, avg_price=90.0, fee=0.4)
    led.reconcile(venue, {})
    before = led.order(TAG)
    venue.set(TAG, state="partly_filled", filled_units=6.0, avg_price=90.0, fee=0.6)
    venue.set(TAG, **{field: value})
    result = led.reconcile(venue, {})
    assert [x["tag"] for x in result["anomalies"]] == [TAG] and not result["clean"]
    (row,) = result["fills"]
    rec = led.order(TAG)
    assert row["units"] == 2.0 and rec["filled_units"] == 6.0
    if field == "avg_price":
        assert row["price"] is None and row["price_covers_units"] is None and row["fee"] == pytest.approx(0.2)
        assert (rec["priced_units"], rec["priced_avg"]) == (before["priced_units"], before["priced_avg"])
    else:
        assert row["fee"] is None and row["price"] == 90.0 and rec["fee"] == before["fee"]


def test_text_from_the_venue_that_is_not_utf8_is_made_storable():
    class Venue(FakeVenue):
        def submit(self, symbol, units, tag, price=None, limit_price=None):
            self.calls.append(tag)
            if tag == "a":
                raise ValueError("bad \ud800 text")
            self._take(symbol, units, tag)
            return {"status": "accepted", "venue_order_id": "V\udfff1"}

    led, venue = ledger(), Venue()
    venue.name = "fake\ud800"
    ready(led, venue)
    intend(led, tag="a")
    intend(led, tag="b")
    rec = led.submit_once(venue, "a")
    assert rec["state"] == "unknown" and rec["note"].startswith("ValueError: bad ")
    led.settle_by_hand("a", "Burton", "not in the app")
    rec = led.submit_once(venue, "b")
    assert rec["state"] == "accepted" and rec["venue_order_id"].startswith("V")
    venue.set("b", state="partly_filled", filled_units=1.0, avg_price=90.0, fee=0.0, raw_status="PART\udc00")
    led.reconcile(venue, {})
    assert led.order("b")["raw_status"].startswith("PART") and led.order("b")["filled_units"] == 1.0


def test_a_reading_method_given_a_tag_that_is_not_utf8_behaves_as_for_an_unknown_tag():
    led = ledger()
    intend(led)
    bad = "s1\ud800"
    assert led.order(bad) is None and led.events(bad) == [] and led.fills(bad) == []
    with pytest.raises(OrderError, match="no order"):
        led.settle_by_hand(bad, "Burton", "x")


class RaisingGet(dict):
    def get(self, *a, **k):
        raise RuntimeError("this status cannot be read")


def test_a_status_whose_get_raises_makes_only_that_order_unresolved():
    class Venue(FakeVenue):
        def order_status(self, tag):
            got = super().order_status(tag)
            return RaisingGet(got) if tag == "b" else got

    clock = Clock()
    led, venue = ledger(clock=clock), Venue()
    ready(led, venue)
    for t in ("a", "b", "c"):
        intend(led, tag=t)
        led.submit_once(venue, t)
        venue.set(t, state="partly_filled", filled_units=2.0, avg_price=90.0, fee=0.0)
    clock.t += 5
    result = led.reconcile(venue, {})
    assert [f["tag"] for f in result["fills"]] == ["a", "c"] and [u["tag"] for u in result["unresolved"]] == ["b"]
    assert not any(a["tag"] is None for a in result["anomalies"]) and led.last_reconcile()["time"] == clock.t


def test_a_finished_order_reported_with_another_state_and_a_larger_amount_takes_the_units():
    led, venue, _ = _accepted()
    venue.set(TAG, state="partly_filled", filled_units=3.0, avg_price=90.0, fee=0.0)
    led.reconcile(venue, {})
    venue.set(TAG, state="cancelled")
    led.reconcile(venue, {})
    venue.set(TAG, state="filled", filled_units=10.0, avg_price=91.0, fee=0.0)
    result = led.reconcile(venue, {})
    (row,) = result["fills"]
    assert row["units"] == 7.0 and [a["tag"] for a in result["anomalies"]] == [TAG]
    assert led.order(TAG)["state"] == "cancelled" and led.order(TAG)["filled_units"] == 10.0


def test_a_sqlite_file_that_is_not_empty_and_not_a_ledger_is_refused(tmp_path):
    other = tmp_path / "other.sqlite"
    raw = sqlite3.connect(str(other))
    raw.execute("CREATE TABLE notes (x TEXT)")
    raw.commit()
    raw.close()
    with pytest.raises(OrderError, match="not an order ledger"):
        ledger(other)
    versioned = tmp_path / "versioned.sqlite"
    raw = sqlite3.connect(str(versioned))
    raw.execute("PRAGMA user_version = 5")
    raw.close()
    with pytest.raises(OrderError, match="not an order ledger"):
        ledger(versioned)
    empty = tmp_path / "empty.sqlite"
    sqlite3.connect(str(empty)).close()
    intend(ledger(empty))


def test_intend_refuses_a_batch_that_is_not_a_string():
    led = ledger()
    with pytest.raises(OrderError, match="batch"):
        intend(led, batch=5)
    assert led.orders() == [] and intend(led, batch="b1")["batch"] == "b1"


@pytest.mark.parametrize("ids", [5, None, 1.5])
def test_mark_applied_refuses_an_argument_that_is_not_an_iterable_of_ids(ids):
    with pytest.raises(OrderError):
        ledger().mark_applied(ids)


# ---- rev 7: an outcome that finds its order declared never arrived, where a ledger is opened, a negative fee total


LATE_OUTCOME, NEGATIVE_FEE = "late_outcome", "negative_fee"
OUTCOMES = ["raise", "raise_after_taking", "not_sent", "odd_result"]   # a timeout, an error, a refusal, an unknown answer


def _raced(tmp_path, mode, by_hand=False):
    """Ledgers a and b on one file. While a's submit is in flight, b declares the order never arrived (a reconcile with
    the clock 200 s ahead, or a person's ruling); then the venue gives a its outcome, `mode`."""
    path, clock = tmp_path / "orders.sqlite", Clock()
    a, b = ledger(path, clock), ledger(path, clock)
    venue = FakeVenue(mode=mode)
    ready(a, venue)
    intend(a)

    def b_declares_it_never_arrived(tag):
        if by_hand:
            b.settle_by_hand(tag, "Burton", "not in the app")
        else:
            clock.t += 200
            assert b.reconcile(venue, {})["clean"] and b.order(tag)["state"] == "never_arrived"

    venue.on_submit = b_declares_it_never_arrived
    return a, b, venue, clock


@pytest.mark.parametrize("by_hand", [False, True])
@pytest.mark.parametrize("mode", OUTCOMES)
def test_an_outcome_that_finds_its_order_declared_never_arrived_blocks_every_further_order(tmp_path, mode, by_hand):
    a, b, venue, clock = _raced(tmp_path, mode, by_hand)
    rec = a.submit_once(venue, TAG)
    assert rec["state"] == "never_arrived" and len(venue.calls) == 1
    (item,) = a.attention()
    assert item["tag"] == TAG and item["kind"] == LATE_OUTCOME and "never arrived" in item["reason"]
    reasons = a.blocked()
    assert len(reasons) == 1 and TAG in reasons[0] and "never arrived" in reasons[0]
    assert b.blocked() == reasons                                         # every connection on the file sees it
    intend(a, tag="next")
    with pytest.raises(OrdersBlocked) as err:
        a.submit_once(venue, "next")
    assert err.value.reasons == reasons and a.order("next")["state"] == "intended" and len(venue.calls) == 1


def test_a_late_outcome_is_lifted_only_when_a_person_closes_it(tmp_path):
    a, b, venue, clock = _raced(tmp_path, "raise")
    a.submit_once(venue, TAG)
    (item,) = a.attention()
    for _ in range(2):                                                    # a reconcile does not lift it
        clock.t += 60
        assert a.reconcile(venue, {})["clean"] and len(a.blocked()) == 1
    a.close_attention(item["id"], "Burton", "not in the app; no replacement was sent")
    assert a.blocked() == [] and a.order(TAG)["state"] == "never_arrived"
    venue.mode, venue.on_submit = "accept", None
    intend(a, tag="next")
    assert a.submit_once(venue, "next")["state"] == "accepted" and len(venue.calls) == 2


def test_an_order_that_arrived_after_a_late_outcome_is_also_turned_up(tmp_path):
    a, b, venue, clock = _raced(tmp_path, "raise_after_taking")         # it did reach the venue
    a.submit_once(venue, TAG)
    clock.t += 60
    found = a.reconcile(venue, {})
    assert a.order(TAG)["state"] == "accepted" and [x["tag"] for x in found["anomalies"]] == [TAG]
    assert sorted(i["kind"] for i in a.attention()) == [LATE_OUTCOME, "turned_up"]
    assert sum(r.startswith("attention item") for r in a.blocked()) == 2


def test_an_acceptance_that_finds_its_order_declared_never_arrived_by_a_reconcile_blocks(tmp_path):
    a, b, venue, clock = _raced(tmp_path, "accept")
    assert a.submit_once(venue, TAG)["state"] == "accepted"
    (item,) = a.attention()
    assert item["kind"] == "late_acceptance" and len(a.blocked()) == 1 and TAG in a.blocked()[0]


@pytest.mark.parametrize("mode", ["raise", "odd_result"])
def test_an_order_that_never_arrived_without_a_race_blocks_nothing(tmp_path, mode):
    path, clock = tmp_path / "orders.sqlite", Clock()
    a, venue = ledger(path, clock), FakeVenue(mode=mode)
    ready(a, venue)
    intend(a)
    assert a.submit_once(venue, TAG)["state"] == "unknown"
    clock.t += 200
    assert a.reconcile(venue, {})["clean"] and a.order(TAG)["state"] == "never_arrived"
    assert a.attention(open_only=False) == [] and a.blocked() == []
    venue.mode = "accept"
    intend(a, tag="next")
    assert a.submit_once(venue, "next")["state"] == "accepted"


def test_an_unknown_order_settled_by_hand_after_its_submit_blocks_nothing():
    led, venue = ledger(), FakeVenue(mode="raise")
    ready(led, venue)
    intend(led)
    led.submit_once(venue, TAG)
    led.settle_by_hand(TAG, "Burton", "not in the app")
    assert led.attention(open_only=False) == [] and led.blocked() == []


def test_a_reconcile_during_the_submit_that_does_not_declare_never_arrived_opens_nothing(tmp_path):
    path, clock = tmp_path / "orders.sqlite", Clock()
    a, b = ledger(path, clock), ledger(path, clock)
    venue = FakeVenue(mode="raise")
    ready(a, venue)
    intend(a)

    def too_early(tag):
        clock.t += 5
        b.reconcile(venue, {})

    venue.on_submit = too_early
    assert a.submit_once(venue, TAG)["state"] == "unknown" and a.attention(open_only=False) == []


@pytest.mark.parametrize("mode", OUTCOMES)
def test_an_outcome_that_finds_its_order_settled_by_the_venue_opens_nothing(tmp_path, mode):
    path, clock = tmp_path / "orders.sqlite", Clock()
    a, b = ledger(path, clock), ledger(path, clock)
    venue = FakeVenue(mode=mode)
    ready(a, venue)
    intend(a)

    def b_sees_it_filled(tag):
        venue._take("XLE", 10.0, tag)
        venue.set(tag, state="filled", filled_units=10.0, avg_price=90.0, fee=0.0)
        b.reconcile(venue, {})

    venue.on_submit = b_sees_it_filled
    assert a.submit_once(venue, TAG)["state"] == "filled" and a.attention(open_only=False) == []


def _listing(directory):
    """Every name in the folder with its kind, mode, size, change time and (for a file) its bytes."""
    out = []
    for p in sorted(Path(directory).iterdir()):
        st = os.lstat(p)
        body = p.read_bytes() if stat.S_ISREG(st.st_mode) else None
        out.append((p.name, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ino, body))
    return out


def _filled_ledger(path):
    led, venue = ledger(path), FakeVenue()
    ready(led, venue)
    intend(led)
    led.submit_once(venue, TAG)
    led.close()


def test_must_exist_refuses_a_missing_ledger_and_creates_nothing(tmp_path):
    (tmp_path / "keep").write_text("x")
    before = _listing(tmp_path)
    for path in (tmp_path / "orders.sqlite", tmp_path / "new" / "orders.sqlite"):
        with pytest.raises(OrderError) as err:
            OrderLedger(path, must_exist=True)
        assert str(path) in str(err.value) and "\n" not in str(err.value) and "no order ledger" in str(err.value)
        assert _listing(tmp_path) == before                               # no file, no folder, no journal
    with pytest.raises(OrderError, match="file"):
        OrderLedger(":memory:", must_exist=True)


def test_must_exist_opens_an_existing_ledger_and_the_default_still_creates_one(tmp_path):
    path = tmp_path / "orders.sqlite"
    _filled_ledger(path)
    led = OrderLedger(path, must_exist=True)
    assert led.order(TAG)["state"] == "accepted"
    led.close()
    fresh = OrderLedger(tmp_path / "sub" / "fresh.sqlite")              # the default: today's behaviour
    assert fresh.orders() == [] and (tmp_path / "sub" / "fresh.sqlite").exists()
    assert stat.S_IMODE(os.stat(tmp_path / "sub" / "fresh.sqlite").st_mode) & 0o022 == 0
    fresh.close()
    assert OrderLedger(tmp_path / "sub" / "fresh.sqlite", must_exist=True).orders() == []


@pytest.mark.parametrize("value", [1, "yes", None])
def test_must_exist_must_be_true_or_false_and_is_a_keyword(tmp_path, value):
    with pytest.raises(OrderError, match="must_exist"):
        OrderLedger(tmp_path / "orders.sqlite", must_exist=value)
    with pytest.raises(TypeError):
        OrderLedger(tmp_path / "orders.sqlite", (), time.time, True)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("must_exist", [False, True])
def test_a_ledger_path_that_is_a_symbolic_link_is_refused_in_every_mode(tmp_path, must_exist):
    real, link, dangling = tmp_path / "real.sqlite", tmp_path / "orders.sqlite", tmp_path / "gone.sqlite"
    _filled_ledger(real)
    link.symlink_to(real)
    dangling.symlink_to(tmp_path / "missing.sqlite")
    before = _listing(tmp_path)
    for path in (link, dangling):
        with pytest.raises(OrderError) as err:
            OrderLedger(path, must_exist=must_exist)
        assert str(path) in str(err.value) and "symbolic link" in str(err.value) and "\n" not in str(err.value)
        assert _listing(tmp_path) == before                               # the target and the folder are untouched


@pytest.mark.parametrize("must_exist", [False, True])
@pytest.mark.parametrize("mode", [0o664, 0o646, 0o666, 0o622])
def test_a_ledger_file_writable_by_group_or_others_is_refused_in_every_mode(tmp_path, must_exist, mode):
    path = tmp_path / "orders.sqlite"
    _filled_ledger(path)
    os.chmod(path, mode)
    before = _listing(tmp_path)
    with pytest.raises(OrderError) as err:
        OrderLedger(path, must_exist=must_exist)
    assert str(path) in str(err.value) and "writable by group or others" in str(err.value) and "\n" not in str(err.value)
    assert _listing(tmp_path) == before
    os.chmod(path, 0o644)
    assert OrderLedger(path, must_exist=must_exist).order(TAG)["state"] == "accepted"


def test_a_ledger_replaced_by_a_link_while_it_is_opened_is_refused_and_nothing_is_written(tmp_path, monkeypatch):
    real, path = tmp_path / "real.sqlite", tmp_path / "orders.sqlite"
    sqlite3.connect(str(real)).close()                                   # an empty database: it would become a ledger
    _filled_ledger(path)
    connect = OrderLedger._connect

    def swap_then_connect(self):
        os.replace(path, tmp_path / "moved.sqlite")
        os.symlink(real, path)
        return connect(self)

    monkeypatch.setattr(OrderLedger, "_connect", swap_then_connect)
    before = real.read_bytes()
    with pytest.raises(OrderError, match="symbolic link"):
        OrderLedger(path)
    assert real.read_bytes() == before and not (tmp_path / "real.sqlite-journal").exists()


def test_a_ledger_file_swapped_for_another_file_while_it_is_opened_is_refused(tmp_path, monkeypatch):
    path, other = tmp_path / "orders.sqlite", tmp_path / "other.sqlite"
    _filled_ledger(path)
    _filled_ledger(other)
    connect = OrderLedger._connect

    def swap_then_connect(self):
        os.replace(other, path)
        return connect(self)

    monkeypatch.setattr(OrderLedger, "_connect", swap_then_connect)
    with pytest.raises(OrderError, match="replaced"):
        OrderLedger(path, must_exist=True)


def test_a_link_in_a_folder_above_the_ledger_is_followed(tmp_path):
    (tmp_path / "real").mkdir()
    _filled_ledger(tmp_path / "real" / "orders.sqlite")
    (tmp_path / "linked").symlink_to(tmp_path / "real", target_is_directory=True)
    assert OrderLedger(tmp_path / "linked" / "orders.sqlite", must_exist=True).order(TAG)["state"] == "accepted"


def _negative_items(led):
    return [i for i in led.attention(open_only=False) if i["kind"] == NEGATIVE_FEE]


def test_a_negative_fee_total_is_kept_and_raises_one_notice_that_does_not_block():
    led, venue, clock = _accepted()
    venue.set(TAG, state="filled", filled_units=10.0, avg_price=90.0, fee=-0.35)
    venue.held = {"XLE": 10.0}
    clock.t += 5
    found = led.reconcile(venue, {})
    assert found["clean"] and [f["fee"] for f in found["fills"]] == [-0.35] and led.order(TAG)["fee"] == -0.35
    led.mark_applied([f["id"] for f in found["fills"]])
    (item,) = led.attention()
    assert item["tag"] == TAG and item["kind"] == NEGATIVE_FEE and TAG in item["reason"] and "-0.35" in item["reason"]
    assert led.blocked() == []
    for fee in (-0.35, -0.1, -0.1):                                     # asked again (late fees): still one notice
        venue.set(TAG, fee=fee)
        clock.t += 60
        assert led.reconcile(venue, {"XLE": 10.0})["clean"]
        led.mark_applied([f["id"] for f in led.unapplied_fills()])
    assert led.order(TAG)["fee"] == -0.1 and len(_negative_items(led)) == 1 and led.blocked() == []
    intend(led, tag="next")
    assert led.submit_once(venue, "next")["state"] == "accepted"


def test_a_negative_fee_notice_is_raised_once_per_order_even_after_a_person_closes_it():
    led, venue, clock = _accepted()
    venue.set(TAG, state="partly_filled", filled_units=4.0, avg_price=90.0, fee=-0.2)
    venue.held = {"XLE": 4.0}
    clock.t += 5
    led.reconcile(venue, {})
    led.mark_applied([f["id"] for f in led.unapplied_fills()])
    (item,) = led.attention()
    led.close_attention(item["id"], "Burton", "a rebate, as the statement says")
    venue.set(TAG, state="filled", filled_units=10.0, fee=-0.1)
    venue.held = {"XLE": 10.0}
    clock.t += 5
    assert led.reconcile(venue, {"XLE": 4.0})["clean"] and led.order(TAG)["fee"] == -0.1
    assert len(_negative_items(led)) == 1 and led.attention() == []
    venue.mode = "accept"
    led.mark_applied([f["id"] for f in led.unapplied_fills()])
    intend(led, tag="second", units=5.0)
    led.submit_once(venue, "second")
    venue.set("second", state="filled", filled_units=5.0, avg_price=90.0, fee=-0.05)
    venue.held = {"XLE": 15.0}
    clock.t += 5
    assert led.reconcile(venue, {"XLE": 10.0})["clean"]
    assert sorted(i["tag"] for i in _negative_items(led)) == sorted(["second", TAG]) and led.blocked() != [] \
        and all("attention" not in r for r in led.blocked())               # only the unapplied fill stands in the way


@pytest.mark.parametrize("fee", [0.0, 0.35, None])
def test_a_fee_total_of_zero_or_more_raises_no_notice(fee):
    led, venue, clock = _accepted()
    venue.set(TAG, state="filled", filled_units=10.0, avg_price=90.0, fee=fee)
    clock.t += 5
    led.reconcile(venue, {"XLE": 0.0})
    assert led.attention(open_only=False) == []


def test_a_fee_total_that_falls_below_zero_is_an_anomaly_and_the_stored_total_is_kept():
    led, venue, clock = _accepted()
    venue.set(TAG, state="filled", filled_units=10.0, avg_price=90.0, fee=0.5)
    venue.held = {"XLE": 10.0}
    clock.t += 5
    led.reconcile(venue, {})
    led.mark_applied([f["id"] for f in led.unapplied_fills()])
    venue.set(TAG, fee=-0.5)
    clock.t += 5
    found = led.reconcile(venue, {"XLE": 10.0})
    assert not found["clean"] and "fell" in found["anomalies"][0]["reason"] and led.order(TAG)["fee"] == 0.5
    assert led.attention(open_only=False) == []                           # nothing negative was recorded


# ---- generated sequences: one ledger object, and the two families where the change is meant to show


SYMBOLS = ("XLE", "XLK")
SUBMIT_MODES = ("accept",) * 4 + ("raise", "raise_after_taking", "not_sent", "odd_result")
ACTIONS = (("intend",) * 3 + ("submit",) * 4 + ("reconcile",) * 5 + ("venue",) * 5
           + ("late_arrival", "status_raises", "settle_by_hand", "close_attention", "restart", "clock"))


def _snapshot(led):
    return {"orders": led.orders(), "events": led.events(), "fills": led.fills(),
            "attention": led.attention(open_only=False), "blocked": led.blocked(), "last": led.last_reconcile()}


def _sequence_trace(mod, seed, family="single", path=None):
    """One generated sequence. `family` is single (one ledger object, fee totals never below zero), race (a second
    object on the same file declares a submitted order never arrived while the venue call is in flight, at random) or
    negfee (one object; fee totals may go below zero). Restarts (close and reopen) happen only on a file. Returns the
    steps, each (action, what it returned, a snapshot of the ledger after it), and the venue's calls per tag."""
    rng = random.Random(seed)
    clock = Clock()

    def open_ledger():
        return mod.OrderLedger(path if path is not None else ":memory:", not_sent=(LocalRefusal,), clock=clock)

    led = open_ledger()
    other = open_ledger() if family == "race" else None
    venue, sizes, book, steps, actual = FakeVenue(), {}, {}, [], {}   # actual: what the venue has filled, said or not
    fee_steps = (-0.3, -0.05, 0.0, 0.02, 0.1) if family == "negfee" else (0.0, 0.01, 0.05, -0.02)

    def held():
        out = {}
        for tag in venue.book:
            symbol, units = sizes[tag]
            out[symbol] = out.get(symbol, 0.0) + math.copysign(actual.get(tag, 0.0), units)
        return out

    def next_fee(st):
        if rng.random() < 0.15:
            return None
        base = st["fee"] if st["fee"] is not None else 0.0
        fee = round(base + rng.choice(fee_steps), 4)
        return fee if family == "negfee" else max(fee, 0.0)

    def apply_fills():
        rows = led.unapplied_fills()
        for f in rows:
            book[f["symbol"]] = book.get(f["symbol"], 0.0) + f["units"]
        led.mark_applied([f["id"] for f in rows])

    def act(action):
        nonlocal led
        if action == "intend":
            tag = f"t{len(sizes)}"
            sizes[tag] = (rng.choice(SYMBOLS), rng.choice((-5.0, 3.0, 10.0)))
            return led.intend(tag, sizes[tag][0], sizes[tag][1], 90.0, limit_price=rng.choice((None, 91.0)))["state"]
        if action == "submit":
            if not sizes:
                return None
            waiting = [o["tag"] for o in led.orders(("intended",))]
            tag = rng.choice(waiting if waiting and rng.random() < 0.8 else sorted(sizes))
            venue.mode = rng.choice(SUBMIT_MODES)
            raced = other is not None and rng.random() < 0.5

            def declare(_tag):
                clock.t += 200
                venue.held = held()
                other.reconcile(venue, book)

            venue.on_submit = declare if raced else None
            before = led.order(tag)["state"]
            try:
                return (tag, before, raced, venue.mode, led.submit_once(venue, tag)["state"])
            finally:
                venue.on_submit = None
        if action == "reconcile":
            clock.t += rng.choice((0, 1, 30, 130, 400))
            venue.held = held()
            r = led.reconcile(venue, book)
            if rng.random() < 0.8:
                apply_fills()
            return (r["clean"], [(u["tag"], u["to"]) for u in r["updated"]], len(r["fills"]), len(r["anomalies"]),
                    len(r["unresolved"]))
        if action == "venue":
            if not venue.book:
                return None
            tag = rng.choice(sorted(venue.book))
            st, size = venue.book[tag], abs(sizes[tag][1])
            now = actual.get(tag, 0.0)
            change = rng.choice(("partial", "fill", "cancel", "expire", "reject", "fee", "avg", "no_amount", "odd"))
            if change == "partial":
                got = actual[tag] = min(size, now + rng.choice((1.0, 2.0)))
                venue.set(tag, filled_units=got, state="filled" if got >= size else "partly_filled",
                          avg_price=rng.choice((90.0, 90.5, None)), fee=next_fee(st))
            elif change == "fill":
                actual[tag] = size
                venue.set(tag, filled_units=size, state="filled", avg_price=90.0, fee=next_fee(st))
            elif change in ("cancel", "expire"):
                venue.set(tag, state="cancelled" if change == "cancel" else "expired", filled_units=now)
            elif change == "reject":
                venue.set(tag, state="rejected" if now == 0 else "cancelled", filled_units=now)
            elif change == "fee":
                venue.set(tag, fee=next_fee(st), filled_units=now)
            elif change == "avg":
                venue.set(tag, avg_price=rng.choice((89.0, 90.0, 91.0)), filled_units=now)
            elif change == "no_amount":
                venue.set(tag, filled_units=None)
            else:
                venue.set(tag, state="unknown_status", filled_units=now)
            return (tag, change)
        if action == "late_arrival":
            gone = [o["tag"] for o in led.orders(("sent", "unknown", "never_arrived")) if o["tag"] not in venue.book]
            if not gone:
                return None
            tag = rng.choice(gone)
            venue._take(*sizes[tag], tag)
            return tag
        if action == "status_raises":
            if not sizes:
                return None
            tag = rng.choice(sorted(sizes))
            venue.status_raises ^= {tag}
            return tag
        if action == "settle_by_hand":
            open_ = led.orders(("sent", "unknown"))
            if not open_:
                return None
            return led.settle_by_hand(open_[0]["tag"], "tester", "checked in the app")["state"]
        if action == "close_attention":
            items = [i for i in led.attention() if i["kind"] in ("late_acceptance", "turned_up")]
            if not items:
                return None
            return led.close_attention(items[0]["id"], "tester", "looked")["tag"]
        if action == "restart":
            if path is None:
                return None
            led.close()
            led = open_ledger()
            return "reopened"
        clock.t += rng.choice((5, 200, 30000))
        return clock.t

    def possible(action):
        states = {o["state"] for o in led.orders()}
        return {"intend": len(sizes) < 6, "submit": "intended" in states, "venue": bool(venue.book),
                "late_arrival": bool(states & {"sent", "unknown", "never_arrived"}), "status_raises": bool(sizes),
                "settle_by_hand": bool(states & {"sent", "unknown"}), "restart": path is not None,
                "close_attention": any(i["kind"] in ("late_acceptance", "turned_up") for i in led.attention()),
                }.get(action, True)

    for i in range(rng.randint(10, 30)):
        action = "reconcile" if i == 0 else rng.choice([a for a in ACTIONS if possible(a)])   # the first lets submits go
        try:
            got = act(action)
        except mod.OrdersBlocked as exc:
            got = ("blocked", len(exc.reasons))
        except mod.OrderError as exc:
            got = ("error", str(exc))
        steps.append((action, got, _snapshot(led)))
        if family == "race":                                              # a person looks at once, so later steps compare
            for item in led.attention():
                if item["kind"] == LATE_OUTCOME:
                    led.close_attention(item["id"], "tester", "looked")
    calls = {}
    for c in venue.calls:
        calls[c["tag"]] = calls.get(c["tag"], 0) + 1
    led.close()
    if other is not None:
        other.close()
    return steps, calls


def test_generated_sequences_on_one_ledger_open_no_new_kind_of_item_and_send_at_most_once(tmp_path):
    for seed in range(300):
        path = tmp_path / f"s{seed}.sqlite" if seed % 6 == 0 else None
        steps, calls = _sequence_trace(orders, seed, "single", path)
        assert all(n == 1 for n in calls.values()), (seed, calls)
        for action, got, snap in steps:
            assert not [i for i in snap["attention"] if i["kind"] in (LATE_OUTCOME, NEGATIVE_FEE)], (seed, action)
            assert all(o["fee"] is None or o["fee"] >= 0 for o in snap["orders"]), (seed, action)


def test_generated_races_open_a_late_outcome_item_exactly_when_an_outcome_finds_never_arrived(tmp_path):
    opened = 0
    for seed in range(100):
        steps, calls = _sequence_trace(orders, seed, "race", tmp_path / f"r{seed}.sqlite")
        assert all(n == 1 for n in calls.values()), (seed, calls)
        seen = set()
        for action, got, snap in steps:
            items = {i["id"]: i for i in snap["attention"] if i["kind"] == LATE_OUTCOME}
            new = [items[i] for i in sorted(set(items) - seen)]
            seen |= set(items)
            lost = (action == "submit" and isinstance(got, tuple) and len(got) == 5 and got[2]
                    and got[1] == "intended" and got[3] != "accept" and got[4] == "never_arrived")
            assert [i["tag"] for i in new] == ([got[0]] if lost else []), (seed, action, got)
            if lost:
                assert any(got[0] in r and "never arrived" in r for r in snap["blocked"]), (seed, snap["blocked"])
            opened += len(new)
    assert opened >= 10


def test_generated_negative_fees_raise_one_notice_per_order_and_block_nothing():
    noticed = 0
    for seed in range(100):
        steps, _ = _sequence_trace(orders, seed, "negfee")
        went_negative = set()
        for action, got, snap in steps:
            went_negative |= {o["tag"] for o in snap["orders"] if o["fee"] is not None and o["fee"] < 0}
            items = [i for i in snap["attention"] if i["kind"] == NEGATIVE_FEE]
            assert sorted(i["tag"] for i in items) == sorted(went_negative), (seed, action)
            assert all(f"attention item {i['id']} " not in r for i in items for r in snap["blocked"]), (seed, action)
        noticed += len(went_negative)
    assert noticed >= 5


# ---- sent at most once, with real processes killed at generated moments


KILL_CHILD = r'''
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("orders_under_test", sys.argv[1])
orders = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orders)
path, hold = sys.argv[2], sys.argv[3]
calls = os.path.join(os.path.dirname(path), "calls")


def say(word):
    sys.stdout.write(word + "\n")
    sys.stdout.flush()


def wait():
    sys.stdin.readline()                     # the parent never writes: the process waits here until it is killed


class Venue:
    name = "file"

    def submit(self, symbol, units, tag, price=None, limit_price=None):
        say("venue")
        if hold == "venue":
            wait()
        fd = os.open(calls, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        os.write(fd, (tag + "\n").encode())
        os.close(fd)
        say("called")
        if hold == "called":
            wait()
        return {"status": "accepted", "venue_order_id": "V"}

    def order_status(self, tag):
        return None

    def positions(self):
        return {}


led = orders.OrderLedger(path)
say("ready")
if hold == "ready":
    wait()
led.submit_once(Venue(), "kill")
say("done")
wait()
'''


class CallsVenue:
    """The venue after a kill: it knows an order when the child's call reached it, and records any new call."""
    name = "file"

    def __init__(self, directory):
        self.calls = Path(directory) / "calls"

    def lines(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def submit(self, symbol, units, tag, price=None, limit_price=None):
        with open(self.calls, "a") as f:
            f.write(tag + "\n")
        return {"status": "accepted", "venue_order_id": "V"}

    def order_status(self, tag):
        if tag not in self.lines():
            return None
        return {"state": "open", "filled_units": 0.0, "avg_price": None, "fee": None, "venue_order_id": "V",
                "raw_status": "NEW"}

    def positions(self):
        return {}


def test_a_submitting_process_killed_at_generated_moments_sends_at_most_once_and_recovers(tmp_path):
    rng = random.Random(7)
    holds = ["ready", "venue", "called"] * 6 + ["free"] * 24               # 42 kills
    rng.shuffle(holds)
    seen = {}
    for i, hold in enumerate(holds):
        d = tmp_path / f"k{i}"
        path = d / "orders.sqlite"
        led = OrderLedger(path)
        led.reconcile(CallsVenue(d), {})
        led.intend("kill", "XLE", 10.0, 90.0)
        led.close()
        child = subprocess.Popen([sys.executable, "-I", "-B", "-c", KILL_CHILD, orders.__file__, str(path), hold],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=d, text=True)
        try:
            assert child.stdout.readline() == "ready\n"
            if hold in ("venue", "called"):
                for word in ("venue", "called")[:("venue", "called").index(hold) + 1]:
                    assert child.stdout.readline() == word + "\n"
            elif hold == "free":
                try:
                    child.wait(timeout=rng.uniform(0.0, 0.03))             # a generated moment after it starts
                except subprocess.TimeoutExpired:
                    pass
            child.kill()
            child.wait(timeout=30)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=30)
            child.stdin.close()
            child.stdout.close()
        venue = CallsVenue(d)
        arrived = len(venue.lines())
        assert arrived <= 1, (i, hold)
        led = OrderLedger(path, clock=Clock(time.time() + 200))           # recovery: a new process, 200 s later
        assert led.reconcile(venue, {})["clean"], (i, hold)
        state = led.order("kill")["state"]
        assert state == ("accepted" if arrived else state) and state in ("accepted", "intended", "never_arrived"), (i, hold)
        chain = [(e["from"], e["to"]) for e in led.events("kill")]
        assert all(a[1] == b[0] for a, b in zip(chain, chain[1:])) and chain[0] == (None, "intended")
        assert led.submit_once(venue, "kill")["state"] in ("accepted", "never_arrived")
        assert len(venue.lines()) == (1 if state in ("intended", "accepted") else 0), (i, hold, state)
        assert led.blocked() == [] and led.attention(open_only=False) == [], (i, hold)
        led.close()
        seen[(hold, state)] = seen.get((hold, state), 0) + 1
    assert seen[("ready", "intended")] == 6 and seen[("venue", "never_arrived")] == 6
    assert seen[("called", "accepted")] == 6 and sum(n for (h, _), n in seen.items() if h == "free") == 24


# ---- T34b-3: every entry of a reconcile carries a kind; three after-the-fact restatements are notices


def _kinds(entries):
    return [e.get("kind") for e in entries]


def _settled(led, venue, book):
    """Reconcile, apply what was found, return the result."""
    result = led.reconcile(venue, book)
    led.mark_applied([f["id"] for f in led.unapplied_fills()])
    return result


def test_t34b3_the_kind_sets_are_closed_exported_and_the_four_notices_never_block():
    assert orders.RECONCILE_NOTICE_KINDS == {"avg_restated", "late_amount", "fee_fell", "piece_unpriced"}
    assert orders.NOTICE_KINDS == {"negative_fee"} | orders.RECONCILE_NOTICE_KINDS
    assert orders.ANOMALY_KINDS == {"reconcile_failed", "not_storable", "status_not_dict", "units_not_finite", "turned_up",
                                    "missing_at_venue", "value_not_finite", "units_negative", "units_fell", "overfill",
                                    "late_amount_other_state", "state_changed", "fee_fell_below_zero",
                                    "piece_price_unexplained", "avg_restated_beyond", "notice_not_recorded"}
    assert orders.UNRESOLVED_KINDS == {"status_unread", "answer_unusable", "status_not_dict", "status_unreadable",
                                       "not_written", "not_found_yet", "state_unknown", "filled_short"}
    assert orders.RECHECK_KINDS == {"status_unread", "gone", "state_unknown"}
    assert orders.POSITIONS_ERROR_KINDS == {"positions_unread", "positions_malformed"}
    assert not orders.ANOMALY_KINDS & orders.RECONCILE_NOTICE_KINDS
    for name in ("ANOMALY_KINDS", "RECONCILE_NOTICE_KINDS", "NOTICE_KINDS", "UNRESOLVED_KINDS", "RECHECK_KINDS",
                 "POSITIONS_ERROR_KINDS"):
        assert name in orders.__all__


def test_t34b3_a_status_read_that_raises_is_status_unread_on_an_open_order_and_on_a_recheck():
    led, venue, _ = _accepted()
    venue.status_raises.add(TAG)
    result = led.reconcile(venue, {})
    assert _kinds(result["unresolved"]) == ["status_unread"] and not result["clean"]
    venue.status_raises.clear()
    venue.set(TAG, state="filled", filled_units=10.0, avg_price=90.0, fee=0.0)
    venue.held = {"XLE": 10.0}
    _settled(led, venue, {})
    venue.status_raises.add(TAG)
    again = led.reconcile(venue, {"XLE": 10.0})
    assert _kinds(again["recheck_errors"]) == ["status_unread"] and again["clean"]


def test_t34b3_positions_that_raise_and_positions_with_unusable_keys_are_told_apart():
    led, venue = ledger(), FakeVenue()
    venue.positions_raise = True
    down = led.reconcile(venue, {})
    assert down["positions_error_kind"] == "positions_unread" and down["positions_error"] and not down["clean"]
    venue.positions_raise = False
    venue.held = {("XLE",): 1.0}
    odd = led.reconcile(venue, {})
    assert odd["positions_error_kind"] == "positions_malformed" and not odd["clean"]
    venue.held = {}
    assert led.reconcile(venue, {})["positions_error_kind"] is None


@pytest.mark.parametrize("change, kind", [({"filled_units": 11.0}, "overfill"), ({"state": "mystery"}, "state_unknown")])
def test_t34b3_an_overfill_is_an_overfill_anomaly_and_an_unknown_state_is_unresolved(change, kind):
    led, venue, _ = _accepted()
    venue.set(TAG, state="partly_filled", filled_units=4.0, avg_price=90.0, fee=0.0)
    led.reconcile(venue, {})
    venue.set(TAG, **change)
    result = led.reconcile(venue, {})
    where = "anomalies" if kind in orders.ANOMALY_KINDS else "unresolved"
    assert _kinds(result[where]) == [kind] and not result["clean"]


@pytest.mark.parametrize("change, kind", [({"filled_units": 3.0}, "units_fell"),
                                          ({"state": "filled", "filled_units": 4.0}, "state_changed")])
def test_t34b3_a_finished_order_reported_with_fewer_units_or_another_state_carries_its_kind(change, kind):
    led, venue, _ = _accepted()
    venue.set(TAG, state="partly_filled", filled_units=4.0, avg_price=90.0, fee=0.4)
    led.reconcile(venue, {})
    venue.set(TAG, state="cancelled")
    led.reconcile(venue, {})
    venue.set(TAG, **change)
    result = led.reconcile(venue, {})
    assert _kinds(result["anomalies"]) == [kind] and result["notices"] == []


def test_t34b3_an_accepted_order_the_venue_does_not_know_is_missing_at_venue():
    led, venue, _ = _accepted()
    del venue.book[TAG]
    result = led.reconcile(venue, {})
    assert _kinds(result["anomalies"]) == ["missing_at_venue"]


def _restate(first, second, units=10.0, fee=0.0):
    """An order filled at the venue's `first` average, then reported again with `second`, no new units."""
    led, venue, clock = _accepted(units)
    venue.set(TAG, state="filled", filled_units=units, avg_price=first, fee=fee)
    venue.held = {"XLE": units}
    _settled(led, venue, {})
    venue.set(TAG, avg_price=second)
    clock.t += 60
    return led, venue, clock, led.reconcile(venue, {"XLE": units})


def test_t34b3_an_average_restated_within_a_cent_is_a_notice_the_stored_average_follows_and_it_is_raised_once():
    led, venue, clock, result = _restate(90.004, 90.0)
    assert result["clean"] and result["anomalies"] == [] and result["fills"] == []
    (n,) = result["notices"]
    assert (n["kind"], n["tag"], n["old"], n["new"]) == ("avg_restated", TAG, 90.004, 90.0) and type(n["id"]) is int
    rec = led.order(TAG)
    assert rec["avg_price"] == 90.0 and rec["priced_avg"] == 90.0 and rec["priced_units"] == 10.0
    (item,) = led.attention()
    assert item["id"] == n["id"] and item["kind"] == "avg_restated" and led.blocked() == []
    clock.t += 60
    again = led.reconcile(venue, {"XLE": 10.0})
    assert again["clean"] and again["notices"] == [] and again["anomalies"] == []
    assert [r["price"] for r in led.fills(TAG)] == [90.004]          # the rows keep the price first worked out


@pytest.mark.parametrize("second", [90.02, 89.98, 0.9])
def test_t34b3_an_average_restated_by_more_than_a_cent_stays_a_blocking_anomaly_and_the_stored_one_is_kept(second):
    led, venue, clock, result = _restate(90.0, second)
    assert _kinds(result["anomalies"]) == ["avg_restated_beyond"] and not result["clean"] and result["notices"] == []
    assert led.order(TAG)["avg_price"] == 90.0 and led.order(TAG)["priced_avg"] == 90.0
    assert led.attention(open_only=False) == []


def test_t34b3_an_average_restated_by_exactly_a_cent_is_still_a_notice():
    led, venue, clock, result = _restate(90.0, 90.01)
    assert result["clean"] and _kinds(result["notices"]) == ["avg_restated"] and led.order(TAG)["avg_price"] == 90.01


def test_t34b3_three_restatements_in_a_row_give_three_notice_ids_and_supersede_the_earlier_open_ones():
    led, venue, clock, first = _restate(90.0, 90.005)
    ids = [first["notices"][0]["id"]]
    for avg in (90.01, 90.015):
        venue.set(TAG, avg_price=avg)
        clock.t += 60
        r = led.reconcile(venue, {"XLE": 10.0})
        assert r["clean"] and _kinds(r["notices"]) == ["avg_restated"]
        ids.append(r["notices"][0]["id"])
    assert len(set(ids)) == 3 and led.order(TAG)["avg_price"] == 90.015
    items = {i["id"]: i for i in led.attention(open_only=False)}
    assert [items[i]["closed_time"] is not None for i in ids] == [True, True, False]
    assert all(items[i]["closed_by"] == "order ledger" and "superseded" in items[i]["closed_note"] for i in ids[:2])
    assert led.blocked() == []


def test_t34b3_a_notice_that_cannot_be_recorded_leaves_the_figure_as_it_was_and_the_reconcile_unclean(tmp_path):
    path = tmp_path / "orders.sqlite"
    clock = Clock()
    led, venue = ledger(path, clock=clock), FakeVenue()
    ready(led, venue)
    intend(led)
    led.submit_once(venue, TAG)
    venue.set(TAG, state="filled", filled_units=10.0, avg_price=90.0, fee=0.0)
    venue.held = {"XLE": 10.0}
    _settled(led, venue, {})
    raw = sqlite3.connect(str(path))
    raw.execute("CREATE TRIGGER drop_notices BEFORE INSERT ON attention WHEN NEW.kind = 'avg_restated' "
                "BEGIN SELECT RAISE(IGNORE); END")
    raw.commit()
    raw.close()
    before = led.order(TAG)
    venue.set(TAG, avg_price=90.005)
    clock.t += 60
    result = led.reconcile(venue, {"XLE": 10.0})
    assert not result["clean"] and _kinds(result["anomalies"]) == ["notice_not_recorded"] and result["notices"] == []
    assert led.order(TAG) == before and led.attention(open_only=False) == []
    assert led.blocked() == ["the latest reconcile was not clean"]


def test_t34b3_a_late_amount_on_a_finished_order_in_the_same_state_is_a_notice_and_the_units_are_taken():
    led, venue, clock = _accepted()
    venue.set(TAG, state="partly_filled", filled_units=4.0, avg_price=90.0, fee=0.0)
    venue.held = {"XLE": 4.0}
    _settled(led, venue, {})
    venue.set(TAG, state="cancelled")
    _settled(led, venue, {"XLE": 4.0})
    venue.set(TAG, state="cancelled", filled_units=6.0, avg_price=90.5)
    venue.held = {"XLE": 6.0}
    clock.t += 60
    result = led.reconcile(venue, {"XLE": 4.0})
    assert result["clean"] and result["anomalies"] == []
    (n,) = result["notices"]
    assert (n["kind"], n["old"], n["new"]) == ("late_amount", 4.0, 6.0)
    (row,) = result["fills"]
    assert row["units"] == 2.0 and row["price"] == pytest.approx((90.5 * 6 - 90.0 * 4) / 2)
    assert led.order(TAG)["state"] == "cancelled" and led.order(TAG)["filled_units"] == 6.0


def test_t34b3_a_late_amount_with_another_state_stays_a_blocking_anomaly_and_the_units_are_taken():
    led, venue, _ = _accepted()
    venue.set(TAG, state="partly_filled", filled_units=3.0, avg_price=90.0, fee=0.0)
    led.reconcile(venue, {})
    venue.set(TAG, state="cancelled")
    led.reconcile(venue, {})
    venue.set(TAG, state="filled", filled_units=10.0, avg_price=91.0, fee=0.0)
    result = led.reconcile(venue, {})
    assert _kinds(result["anomalies"]) == ["late_amount_other_state"] and result["notices"] == []
    assert led.order(TAG)["filled_units"] == 10.0 and not result["clean"]


def test_t34b3_a_fee_total_that_falls_but_stays_at_or_above_zero_is_a_notice_followed_with_no_fill_row():
    led, venue, clock = _accepted()
    venue.set(TAG, state="filled", filled_units=10.0, avg_price=90.0, fee=1.0)
    venue.held = {"XLE": 10.0}
    _settled(led, venue, {})
    venue.set(TAG, fee=0.4)
    clock.t += 60
    rows = led.fills()
    result = led.reconcile(venue, {"XLE": 10.0})
    assert result["clean"] and result["fills"] == [] and led.fills() == rows
    (n,) = result["notices"]
    assert (n["kind"], n["old"], n["new"]) == ("fee_fell", 1.0, 0.4)
    assert led.order(TAG)["fee"] == 0.4 and sum(r["fee"] for r in led.fills(TAG)) == 1.0   # rows: the earlier total
    clock.t += 60
    assert led.reconcile(venue, {"XLE": 10.0})["notices"] == []
    venue.set(TAG, fee=0.0)
    clock.t += 60
    assert _kinds(led.reconcile(venue, {"XLE": 10.0})["notices"]) == ["fee_fell"] and led.order(TAG)["fee"] == 0.0


def test_t34b3_a_fee_total_that_falls_below_zero_keeps_its_anomaly_kind():
    led, venue, clock = _accepted()
    venue.set(TAG, state="filled", filled_units=10.0, avg_price=90.0, fee=0.5)
    venue.held = {"XLE": 10.0}
    _settled(led, venue, {})
    venue.set(TAG, fee=-0.5)
    clock.t += 5
    found = led.reconcile(venue, {"XLE": 10.0})
    assert _kinds(found["anomalies"]) == ["fee_fell_below_zero"] and led.order(TAG)["fee"] == 0.5
    assert found["notices"] == [] and led.attention(open_only=False) == []


def test_t34b3_a_rounded_average_that_implies_a_negative_piece_is_a_notice_and_the_priced_point_moves():
    led, venue, clock = _accepted(units=1001.0)
    venue.set(TAG, state="partly_filled", filled_units=1000.0, avg_price=5.01, fee=0.0)   # 5.00501 rounded up
    venue.held = {"XLE": 1000.0}
    _settled(led, venue, {})
    venue.set(TAG, state="filled", filled_units=1001.0, avg_price=5.00)                  # one more at 4.99: 5.004995
    venue.held = {"XLE": 1001.0}
    clock.t += 60
    result = led.reconcile(venue, {"XLE": 1000.0})
    assert result["clean"] and result["anomalies"] == []
    (n,) = result["notices"]
    assert (n["kind"], n["old"], n["new"]) == ("piece_unpriced", 5.01, 5.0)
    (row,) = result["fills"]
    assert (row["units"], row["price"], row["price_covers_units"]) == (1.0, None, None)
    rec = led.order(TAG)
    assert (rec["avg_price"], rec["priced_avg"], rec["priced_units"]) == (5.0, 5.0, 1001.0)
    led.mark_applied([row["id"]])
    clock.t += 60
    again = led.reconcile(venue, {"XLE": 1001.0})
    assert again["clean"] and again["notices"] == [] and again["anomalies"] == []


def test_t34b3_a_negative_piece_that_a_cent_of_rounding_cannot_explain_stays_a_blocking_anomaly():
    led, venue, _ = _accepted()
    venue.set(TAG, state="partly_filled", filled_units=4.0, avg_price=100.0, fee=0.0)
    led.reconcile(venue, {})
    venue.set(TAG, state="partly_filled", filled_units=6.0, avg_price=50.0)
    result = led.reconcile(venue, {})
    assert _kinds(result["anomalies"]) == ["piece_price_unexplained"] and result["notices"] == []
    assert led.order(TAG)["priced_avg"] == 100.0


def test_t34b3_every_anomaly_unresolved_and_recheck_entry_of_a_generated_run_carries_a_kind_of_its_set():
    rng = random.Random(34)
    seen = set()
    for _ in range(300):
        led, venue, clock = _accepted()
        for _step in range(rng.randint(1, 6)):
            change = rng.choice([{"filled_units": rng.choice([2.0, 4.0, 10.0, 11.0, None])},
                                 {"avg_price": rng.choice([90.0, 90.004, 91.0, 50.0, None, float("nan")])},
                                 {"fee": rng.choice([0.0, 0.5, 1.0, -0.2, None])},
                                 {"state": rng.choice(["open", "partly_filled", "filled", "cancelled", "odd"])}])
            venue.set(TAG, **change)
            if rng.random() < 0.15:
                venue.status_raises.add(TAG)
            else:
                venue.status_raises.discard(TAG)
            venue.positions_raise = rng.random() < 0.1
            clock.t += 60
            r = led.reconcile(venue, {})
            for key, allowed in (("anomalies", orders.ANOMALY_KINDS), ("unresolved", orders.UNRESOLVED_KINDS),
                                 ("recheck_errors", orders.RECHECK_KINDS), ("notices", orders.RECONCILE_NOTICE_KINDS)):
                for e in r[key]:
                    assert e["kind"] in allowed, (key, e)
                    seen.add(e["kind"])
            assert (r["positions_error"] is None) == (r["positions_error_kind"] is None)
            assert r["clean"] == (not (r["unresolved"] or r["anomalies"] or r["position_differences"]
                                       or r["positions_error"] or led.orders(("sent", "unknown"))))
            led.mark_applied([f["id"] for f in led.unapplied_fills()])
    assert {"status_unread", "overfill", "avg_restated_beyond"} <= seen, seen


@pytest.mark.parametrize("state", ["filled", "cancelled"])
def test_t34b3_a_finished_order_reported_beyond_its_size_is_an_overfill_never_a_late_amount(state):
    led, venue, clock = _accepted(10.0)
    venue.set(TAG, state=state, filled_units=6.0 if state == "cancelled" else 10.0, avg_price=90.0, fee=0.0)
    _settled(led, venue, {})
    before = led.order(TAG)["filled_units"]
    venue.set(TAG, filled_units=11.0)                     # the order's own finished state, one unit beyond its size
    clock.t += 60
    result = led.reconcile(venue, {})
    assert _kinds(result["anomalies"]) == ["overfill"] and result["notices"] == [] and not result["clean"]
    assert led.order(TAG)["filled_units"] == before and result["fills"] == []
