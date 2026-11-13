import math
import multiprocessing
import os
import random
import sqlite3
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
    venue.set("b", state="partly_filled", filled_units=3.0, avg_price="ninety", fee=0.1)
    venue.set("c", state="partly_filled", filled_units=5.0, avg_price=91.0, fee=0.2)
    clock.t += 5
    result = led.reconcile(venue, {})
    assert [f["tag"] for f in result["fills"]] == ["a", "c"] and not result["clean"]
    assert [x["tag"] for x in result["anomalies"]] == ["b"]
    assert led.order("b")["filled_units"] == 0.0 and led.order("b")["state"] == "accepted"
    assert led.last_reconcile()["time"] == clock.t and len(led.last_reconcile()["summary"]["fills"]) == 2
    assert [f["tag"] for f in led.unapplied_fills()] == ["a", "c"]


@pytest.mark.parametrize("field, value", [("avg_price", float("nan")), ("fee", float("inf")), ("filled_units", "4"),
                                          ("fee", "x"), ("filled_units", float("nan"))])
def test_a_present_but_non_finite_number_is_an_anomaly_and_changes_nothing(field, value):
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
    led, venue, _ = _accepted()
    venue.set(TAG, state="partly_filled", filled_units=4.0, avg_price=90.0, fee=1.0)
    led.reconcile(venue, {})
    venue.set(TAG, state="partly_filled", filled_units=6.0, avg_price=90.0, fee=0.5)
    result = led.reconcile(venue, {})
    assert [a["tag"] for a in result["anomalies"]] == [TAG] and "fee" in result["anomalies"][0]["reason"]
    (row,) = result["fills"]
    assert (row["units"], row["fee"], row["price"], row["price_covers_units"]) == (2.0, None, 90.0, 2.0)
    rec = led.order(TAG)
    assert rec["filled_units"] == 6.0 and rec["fee"] == 1.0 and not result["clean"]
    venue.set(TAG, state="partly_filled", filled_units=6.0, avg_price=90.0, fee=0.5)
    again = led.reconcile(venue, {})                                   # still lower: still an anomaly, no new row
    assert again["fills"] == [] and [a["tag"] for a in again["anomalies"]] == [TAG]
    venue.set(TAG, state="partly_filled", filled_units=7.0, avg_price=90.0, fee=1.25)
    (row,) = led.reconcile(venue, {})["fills"]
    assert (row["units"], row["fee"]) == (1.0, 0.25) and led.order(TAG)["fee"] == 1.25


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


@pytest.mark.parametrize("change", [{"filled_units": 3.0}, {"state": "filled", "filled_units": 10.0}, {"state": "expired"}])
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
    assert [a["tag"] for a in result["anomalies"]] == [TAG] and not result["clean"]
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
    assert [a["tag"] for a in result["anomalies"]] == [TAG] and led.order(TAG)["state"] == "expired"


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
