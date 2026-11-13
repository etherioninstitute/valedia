import random
import sqlite3

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
    result = led.reconcile(venue, {"XLE": 10.0})
    assert result["clean"] and result["orders_checked"] == 1 and result["time"] == clock.t
    assert result["fills"] == [{"tag": "s1-XLE-2026-11-12", "symbol": "XLE", "units": 10.0, "price": 90.5, "fee": 1.0}]
    assert result["updated"] == [{"tag": "s1-XLE-2026-11-12", "from": "accepted", "to": "filled"}]
    assert led.order("s1-XLE-2026-11-12")["state"] == "filled"
    assert [(e["from"], e["to"]) for e in led.events("s1-XLE-2026-11-12")] == [
        (None, "intended"), ("intended", "sent"), ("sent", "accepted"), ("accepted", "filled")]
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
    second = led.reconcile(venue, {"XLE": 10.0})
    assert led.order("s1-XLE-2026-11-12")["state"] == "filled" and second["clean"]
    assert second["fills"] == [{"tag": "s1-XLE-2026-11-12", "symbol": "XLE", "units": 10.0, "price": 90.0, "fee": 0.0}]


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
    assert r1["fills"] == [{"tag": "sell", "symbol": "XLE", "units": -4.0, "price": 100.0, "fee": 1.0}]
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
    assert led.reconcile(venue, {})["fills"] == [] and led.reconcile(venue, {})["orders_checked"] == 0


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
    assert result["position_differences"] == {"GLD": {"book": 0.0, "venue": 1.0}, "SPY": {"book": 2.0, "venue": 3.0},
                                              "TLT": {"book": 4.0, "venue": 0.0}}


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
    assert first["fills"] == [{"tag": "s1-XLE-2026-11-12", "symbol": "XLE", "units": 7.0, "price": 90.0, "fee": 0.7}]
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
