"""Recorded-answer rehearsals of the order step (big run, BR-B).

Each case runs the REAL adapter (valedia_trader.venues.webull.WebullVenue) over a fake Webull client that answers with
shapes already recorded in docs/WEBULL.md and tests/venues/test_webull.py, through the REAL order step (`run_order_step`),
the real order ledger and the real journal, every store in the test's temporary folder. Nothing touches the network.

Provenance of the answers:
  - sale and cancel "not yet": recorded live on the practice account (docs/WEBULL.md, test_webull.py `observed_order`).
  - partial fill (PARTIAL_FILLED): documented by Webull, never observed live.
  - EXPIRED: never observed and never documented; it is the adapter's own reading (webull.py ORDER_STATES).
"""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _extra in (HERE, HERE.parent / "venues"):          # the sibling test modules are plain modules (no package)
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

import test_order_step as tos                           # noqa: E402  (World, paper, CAL, ny: the order step's own harness)
import test_webull as twb                               # noqa: E402  (FakeClient, observed_order, observed_detail, ...)
from valedia_trader.execution_and_risk import order_step as osx   # noqa: E402
from valedia_trader.venues import webull as wb          # noqa: E402

PAID = "SPY"
DONE_STATES = ("FILLED", "CANCELLED", "FAILED", "EXPIRED")


@pytest.fixture(autouse=True)
def _rehearsal_isolation(tmp_path, monkeypatch):
    """The same isolation tests/venues/test_webull.py gives every test: HOME and the live-repo folder are temporary, the
    pacer never waits; and no test here loads the real second price source (T51 ruling C)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(wb, "LIVE_REPO", tmp_path, raising=False)
    monkeypatch.setattr(wb, "RATE_LIMITS", {env: dict(twb.HIGH_LIMITS) for env in twb.REAL_RATE_LIMITS}, raising=False)
    monkeypatch.setattr(wb, "_default_sleep", lambda seconds: None, raising=False)
    monkeypatch.setattr(osx, "_load_second_source", lambda: pytest.fail("the real second price source would be loaded"),
                        raising=False)


class Desk(twb.FakeClient):
    """The fake Webull client of test_webull.py, made to answer an order step: it remembers each order placed, answers
    the order-detail call from a per-order script (default: the order as SUBMITTED, the observed 17-field row), answers
    cancel as Webull does (200 and the order reads CANCELLED, keeping what it had filled; or a scripted refusal), and
    serves the holdings and the cash the order step reads."""

    def __init__(self, cash="1000000"):
        super().__init__(balance={"total_asset_currency": "USD", "total_cash_balance": cash,
                                  "account_currency_assets": [{"currency": "USD", "cash_balance": cash, "settled_cash": cash,
                                                               "buying_power": cash}]})
        self.sent = {}                    # client order id -> the payload placed
        self.rows = {}                    # client order id -> overrides for the order row (status, filled_quantity, ...)
        self.omit = {}                    # client order id -> row fields to leave out of the answer
        self.refuse_cancel = {}           # client order id -> the exception its cancel raises
        self.after_refusal = {}           # client order id -> row overrides that apply once its cancel was refused
        self.held = {}                    # symbol -> quantity, the positions answer
        self.order_v3.place_order = self._place

    def _place(self, account, orders):
        self.calls.append(("place", account, orders))
        for o in orders:
            self.sent[o["client_order_id"]] = dict(o)
        return twb.Response(self._placed_answer(orders), self._order_status, "rejected: " + twb.KEY)

    def _row(self, cid):
        sent = self.sent[cid]
        row = twb.observed_order(cid, symbol=sent["symbol"], side=sent["side"], total_quantity=sent["quantity"],
                                 limit_price=sent["limit_price"], **self.rows.get(cid, {}))
        for field in self.omit.get(cid, ()):
            row.pop(field)
        return row

    def _detail(self, account, cid):
        self.calls.append(("detail", account, cid))
        if cid not in self.sent:
            raise AssertionError("the rehearsal asked about an order it never placed")
        return twb.Response(twb.observed_detail(cid, self._row(cid)))

    def _cancel(self, account, cid):
        self.calls.append(("cancel", account, cid))
        if cid in self.refuse_cancel:
            self.rows.setdefault(cid, {}).update(self.after_refusal.get(cid, {}))
            raise self.refuse_cancel[cid]
        row = self.rows.setdefault(cid, {})
        if row.get("status", "SUBMITTED") not in DONE_STATES:
            row["status"] = "CANCELLED"
        return twb.Response({"client_order_id": cid, "order_id": "ORD-777"})

    def _positions_now(self):
        return [{"symbol": s, "quantity": repr(float(q)), "instrument_type": "EQUITY"} for s, q in self.held.items() if q]

    @property
    def _positions(self):
        return self._positions_now()

    @_positions.setter
    def _positions(self, value):          # FakeClient.__init__ sets it; the desk serves `held` instead
        pass

    def cancels(self):
        return [c[2] for c in self.calls if c[0] == "cancel"]

    def say(self, tag, **row):
        """Script what Webull's detail answer says about the order sent under `tag` from now on."""
        self.rows.setdefault(wb.client_order_id(tag), {}).update(row)


class WebullWorld(tos.World):
    """The order-step test world of test_order_step.py, with the REAL WebullVenue over a Desk in place of the stand-in."""

    def __init__(self, tmp):
        super().__init__(tmp)
        self.desk = Desk()
        self.venue = wb.WebullVenue(twb.KEY, twb.SECRET, environment="sandbox", client=self.desk,
                                    confirmation_file=tmp / "config" / "webull_live.json")
        self.state_path = osx.state_path_for(self.ledger_path, self.venue.name)

    def quotes(self, symbols):
        self.quote_calls.append(list(symbols))
        return {s: (self.prices[s], self.clock.t - self.quote_age.get(s, 60.0)) for s in symbols if s in self.prices}

    def tags(self):
        return [o["tag"] for o in self.orders()]

    def summaries(self):
        return self.entries("summary")


@pytest.fixture
def world(tmp_path):
    return WebullWorld(tmp_path)


NOT_YET = ("OPENAPI_ORDER_CAN_NOT_BE_CANCEL_FOR_PENDING_SUBMIT", "pending", 417)     # recorded, practice account
FINISHED = ("OPENAPI_ORDER_CAN_NOT_BE_CANCEL", "cannot cancel", 417)                # recorded, practice account
THREE_FEES = [{"actual_value": "0.01"}, {"actual_value": "0.01"}, {"actual_value": "0.01"}]   # a sale's three entries (0.03)


def send_first(world, units=10.0):
    """Run 1: the paper book wants `units` of SPY; the order goes out through the real adapter and reads as open."""
    world.paper = tos.paper({"a": {PAID: units}})
    out = world.run()
    assert out["status"] == "done" and out["orders_sent"] == 1 and "faults" not in out, out
    (tag,) = world.tags()
    return tag


def plain_runs(world):
    """Every run's own summary journal line must carry no `faults` key (it is present only when not empty)."""
    return [s for s in world.summaries() if "faults" in s]


def fills_of(world):
    return [(f["tag"], f["units"], f["price"]) for f in world.fills()]


# ---- 1. partial fill (PARTIAL_FILLED: documented by Webull, never observed live)

def test_a_partial_fill_books_the_fill_once_cancels_the_rest_and_sends_only_the_missing_part(world):
    """Recorded-answer shape: the order-detail row of docs/WEBULL.md with status PARTIAL_FILLED and a filled_quantity below
    total_quantity. Webull documents this status but it was never observed live; the answer is the documented shape."""
    first = send_first(world, 10.0)
    world.desk.say(first, status="PARTIAL_FILLED", filled_quantity="4", filled_price="500")
    world.desk.held[PAID] = 4.0
    read = world.venue.order_status(first)                         # the real adapter's reading of the recorded answer
    assert read["state"] == "partly_filled" and read["filled_units"] == 4.0 and read["raw_status"] == "PARTIAL_FILLED"
    world.next_day()
    out = world.run()
    assert out["status"] == "done" and "faults" not in out and "faults" not in world.entries("summary")[-1]
    assert out["cancels"] == [{"tag": first, "symbol": PAID, "answer": "cancel requested"}]
    assert world.desk.cancels() == [wb.client_order_id(first)]
    assert [(o["units"], o["state"], o["filled_units"]) for o in world.orders()] == [(10.0, "cancelled", 4.0), (6.0, "accepted", 0.0)]
    assert [(f["fill_id"], f["units"], f["tag"]) for f in world.entries("fill")] == [(1, 4.0, first)]
    assert fills_of(world) == [(first, 4.0, 500.0)] and out["fills_journaled"] == 1
    second = world.tags()[1]
    assert world.desk.sent[wb.client_order_id(second)]["quantity"] == "6"      # the missing part only
    world.desk.say(second, status="FILLED", filled_quantity="6", filled_price="500")
    world.desk.held[PAID] = 10.0
    world.later()
    out = world.run()
    assert out["status"] == "done" and out["orders_sent"] == 0 and "faults" not in out
    assert world.book() == {PAID: 10.0} and all(f["applied"] for f in world.fills())
    assert sorted(f["fill_id"] for f in world.entries("fill")) == [f["id"] for f in world.fills()]
    assert plain_runs(world) == []


def test_a_partial_row_without_filled_quantity_is_not_read_as_zero_and_nothing_is_booked(world):
    """The opposite of the case above: the same PARTIAL_FILLED row with `filled_quantity` left out. The adapter says the
    fill is unknown (None, never 0.0); the ledger books nothing; the held shares make the reconcile unclean, so the run
    stops and sends nothing, and the order stays open. (The row without the field is the adapter's own reading, not an
    answer Webull was seen to give.)"""
    first = send_first(world, 10.0)
    cid = wb.client_order_id(first)
    world.desk.say(first, status="PARTIAL_FILLED")
    world.desk.omit[cid] = ("filled_quantity",)
    world.desk.held[PAID] = 4.0
    read = world.venue.order_status(first)
    assert read["state"] == "partly_filled" and read["filled_units"] is None
    world.desk.refuse_cancel[cid] = twb.ServerException(*NOT_YET)             # the remainder is not cancelled yet
    world.later()
    out = world.run()
    assert out["status"] == "stopped" and out["stage"] == "reconcile" and out["orders_sent"] == 0
    assert "positions differ" in out["reason"] and first in out["reason"]
    assert world.fills() == [] and world.entries("fill") == []
    assert [(o["state"], o["filled_units"]) for o in world.orders()] == [("partly_filled", 0.0)]
    assert len(world.desk.placed()) == 1


# ---- 2. cancel "not yet" (OPENAPI_ORDER_CAN_NOT_BE_CANCEL_FOR_PENDING_SUBMIT, recorded on the practice account)

def test_a_cancel_answered_not_yet_stops_at_reconcile_sends_nothing_and_the_next_run_goes_on(world):
    """What the run does today: the cancel word is journaled as "not yet"; the run ends `stopped` at stage `reconcile`
    with "still open" in its reason, sends nothing, and the summary has no `faults` entry (a stop, not a fault); the next
    run, once the order reads cancelled, asks again, books nothing and sends."""
    first = send_first(world, 10.0)
    cid = wb.client_order_id(first)
    world.desk.refuse_cancel[cid] = twb.ServerException(*NOT_YET)
    world.later()
    out = world.run()
    assert out["status"] == "stopped" and out["stage"] == "reconcile" and out["orders_sent"] == 0
    assert "still open" in out["reason"] and first in out["reason"] and "faults" not in out
    assert out["cancels"] == [{"tag": first, "symbol": PAID, "answer": "not yet"}]
    assert [e["answer"] for e in world.entries("cancel")] == ["not yet"]
    assert "faults" not in world.entries("summary")[-1] and world.entries("summary")[-1]["status"] == "stopped"
    assert len(world.desk.placed()) == 1 and [o["state"] for o in world.orders()] == ["accepted"]
    world.desk.refuse_cancel.clear()                                           # "it works a little later"
    world.later()
    out = world.run()
    assert out["status"] == "done" and out["orders_sent"] == 1 and "faults" not in out
    assert out["cancels"] == [{"tag": first, "symbol": PAID, "answer": "cancel requested"}]
    assert [(o["units"], o["state"]) for o in world.orders()] == [(10.0, "cancelled"), (10.0, "accepted")]
    assert len(world.desk.placed()) == 2


def test_a_cancel_refused_as_already_finished_lets_the_run_go_on_when_the_order_reads_cancelled(world):
    """The opposite: OPENAPI_ORDER_CAN_NOT_BE_CANCEL (recorded: a second cancel) is the word "already finished"; the order
    reads CANCELLED on the next read, so the run is not stopped and sends the order again."""
    first = send_first(world, 10.0)
    cid = wb.client_order_id(first)
    world.desk.refuse_cancel[cid] = twb.ServerException(*FINISHED)
    world.desk.after_refusal[cid] = {"status": "CANCELLED"}                    # it finished between the two reads
    world.later()
    out = world.run()
    assert out["status"] == "done" and out["orders_sent"] == 1 and "faults" not in out
    assert out["cancels"] == [{"tag": first, "symbol": PAID, "answer": "already finished"}]
    assert [(o["units"], o["state"]) for o in world.orders()] == [(10.0, "cancelled"), (10.0, "accepted")]


# ---- 3. sale (recorded live on the practice account: a SELL row FILLED with three fee entries)

def _hold_ten_bought(world, owner=5.0):
    """The owner holds `owner` SPY; run 1 buys 10 and Webull reports it FILLED; a second run books the fill."""
    world.desk.held[PAID] = owner
    first = send_first(world, 10.0)
    world.desk.say(first, status="FILLED", filled_quantity="10", filled_price="500")
    world.desk.held[PAID] = owner + 10.0
    world.later()
    out = world.run()
    assert out["status"] == "done" and out["orders_sent"] == 0 and world.book() == {PAID: 10.0}
    return first


def test_a_sale_read_as_filled_books_one_fill_with_its_fee_and_the_book_falls_by_the_sold_units(world):
    _hold_ten_bought(world)
    world.paper = tos.paper({"a": {PAID: 4.0}})
    world.later()
    out = world.run()
    assert out["status"] == "done" and out["orders_sent"] == 1 and out["sent"][0]["side"] == "sell" and out["sent"][0]["units"] == -6.0
    sale = world.tags()[1]
    payload = world.desk.sent[wb.client_order_id(sale)]
    assert payload["side"] == "SELL" and payload["quantity"] == "6"
    world.desk.say(sale, status="FILLED", filled_quantity="6", filled_price="500", fees=THREE_FEES)
    world.desk.held[PAID] = 9.0
    read = world.venue.order_status(sale)
    assert read["state"] == "filled" and read["filled_units"] == 6.0 and read["fee"] == pytest.approx(0.03)
    world.later()
    out = world.run()
    assert out["status"] == "done" and out["orders_sent"] == 0 and "faults" not in out
    assert [(o["units"], o["state"], o["filled_units"]) for o in world.orders()] == [(10.0, "filled", 10.0), (-6.0, "filled", 6.0)]
    assert [(f["units"], f["price"]) for f in world.fills()] == [(10.0, 500.0), (-6.0, 500.0)]
    sold = [f for f in world.entries("fill") if f["units"] < 0]
    assert len(sold) == 1 and sold[0]["tag"] == sale and sold[0]["fee"] == pytest.approx(0.03) and out["fills_journaled"] == 1
    assert world.book() == {PAID: 4.0} and world.desk.held[PAID] == 5.0 + 4.0      # the owner's 5 are untouched
    assert plain_runs(world) == []


def test_a_sale_is_never_more_than_the_book_holds_and_the_adapter_refuses_more_than_held_before_any_call(world):
    """The opposite: the paper book wants out of everything; the order step sells the 10 it bought, not the owner's 5 on
    top. And the real adapter, asked directly to sell more than the account holds, refuses before any call is made."""
    _hold_ten_bought(world)
    world.paper = tos.paper({"a": {}})
    world.later()
    out = world.run()
    assert out["status"] == "done" and out["orders_sent"] == 1 and out["sent"][0]["units"] == -10.0
    placed = len(world.desk.placed())
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        world.venue.submit(PAID, -(world.desk.held[PAID] + 1.0), "too-much")
    assert len(world.desk.placed()) == placed


# ---- 4. EXPIRED (never observed and never documented: the adapter's own reading, exact match of the raw word)

def test_an_expired_order_that_part_filled_books_the_fill_first_ends_expired_and_sends_the_rest(world):
    """EXPIRED is not in Webull's documented statuses and was never seen live: the row below is the adapter's own reading
    (webull.py ORDER_STATES, exact match) with the documented filled_quantity / filled_price fields."""
    first = send_first(world, 10.0)
    world.desk.say(first, status="EXPIRED", filled_quantity="3", filled_price="500")
    world.desk.held[PAID] = 3.0
    read = world.venue.order_status(first)
    assert read["state"] == "expired" and read["filled_units"] == 3.0 and read["raw_status"] == "EXPIRED"
    world.next_day()
    out = world.run()
    assert out["status"] == "done" and "faults" not in out and "faults" not in world.entries("summary")[-1]
    assert out["cancels"] == [] and world.desk.cancels() == []                  # a dead order is not cancelled
    assert [(o["units"], o["state"], o["filled_units"]) for o in world.orders()] == [(10.0, "expired", 3.0), (7.0, "accepted", 0.0)]
    assert [(f["fill_id"], f["units"], f["tag"]) for f in world.entries("fill")] == [(1, 3.0, first)]
    assert world.book() == {PAID: 3.0} and world.desk.sent[wb.client_order_id(world.tags()[1])]["quantity"] == "7"


def test_an_expired_order_with_nothing_filled_books_nothing_and_the_whole_order_is_sent_again(world):
    first = send_first(world, 10.0)
    world.desk.say(first, status="EXPIRED", filled_quantity="0")
    world.next_day()
    out = world.run()
    assert out["status"] == "done" and "faults" not in out and world.fills() == [] and world.entries("fill") == []
    assert [(o["units"], o["state"], o["filled_units"]) for o in world.orders()] == [(10.0, "expired", 0.0), (10.0, "accepted", 0.0)]


def test_a_status_word_that_is_not_listed_stays_unresolved_and_stops_the_run(world):
    """The opposite: a word the adapter does not know (here a near spelling of EXPIRED) is unknown_status, never expired:
    the order stays open on the ledger (its cancel is answered "already finished" yet the row never reads cancelled), the
    run stops at reconcile and nothing new is sent."""
    first = send_first(world, 10.0)
    world.desk.say(first, status="EXPIRED ", filled_quantity="0")
    assert world.venue.order_status(first)["state"] == "unknown_status"
    world.desk.refuse_cancel[wb.client_order_id(first)] = twb.ServerException(*FINISHED)   # the row never turns cancelled
    world.later()
    out = world.run()
    assert out["status"] == "stopped" and out["stage"] == "reconcile" and out["orders_sent"] == 0
    assert [o["state"] for o in world.orders()] == ["accepted"] and len(world.desk.placed()) == 1
