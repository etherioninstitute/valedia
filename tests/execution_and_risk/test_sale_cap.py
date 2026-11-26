"""CF-C: a sale is capped at min(the book's settled units, the account's sellable quantity).

The book's settled units: the units of the ledger's OWN lots that have settled, T+1 on the exchange calendar the
order step runs on, keyed to each lot's own New York trade date (the day its DAY order was sent), never to the
reconcile that saw the fill; a lot whose trade date cannot be established is unsettled; the owner's own units at the
account are never in it. The account's sellable quantity: the venue's `sellable` (Webull: the holding less open
sells' remainders and in-flight sales). A sale over the cap is cut to it (toward zero, to the step); a cap of zero
sends nothing and says so; the summary gives each sale's cap and its two inputs."""
from decimal import Decimal

import exchange_calendars
import pandas as pd
import pytest

from valedia_trader.execution_and_risk import order_step as osx
from test_order_step import CAL, World, ny, paper
from test_shared_run import SW, no_network  # noqa: F401  (no_network: the shared world's autouse guard)

SYM = "SPY"
WIDE = exchange_calendars.get_calendar("XNYS", start="2015-01-01", end="2030-12-31")  # CAL ends a year on


def bought(tmp, when="2026-11-24 10:00", units=10.0, held=None):
    """A world whose book bought `units` of SYM at `when`, filled at once."""
    w = World(tmp)
    if held:
        w.venue.held.update(held)
    w.venue.fill_at_once = 1.0
    w.clock.t = ny(when)
    w.paper = paper({"a": {SYM: units}})
    out = w.run()
    assert out["status"] == "done" and out["orders_sent"] == 1, out
    return w


def sell_all(w, when=None):
    if when is not None:
        w.clock.t = ny(when)
    w.paper = paper({"a": {SYM: 0.0}})
    return w.run()


def sold(w, out):
    return [s["units"] for s in out["sent"] if s["symbol"] == SYM]


# ---- the pure parts: settlement on the calendar, the book's settled units

@pytest.mark.parametrize("trade, settles", [("2026-11-24", "2026-11-25"), ("2026-11-25", "2026-11-27"),
                                            ("2026-11-27", "2026-11-30"), ("2026-11-26", None), ("2026-11-28", None)])
def test_a_lot_settles_on_the_next_session_after_its_trade_date(trade, settles):
    assert osx.settlement_day(CAL, trade) == settles                 # Thanksgiving (Thu 11-26) is not a session


def test_a_calendar_that_cannot_answer_leaves_the_lot_unsettled():
    class Broken:
        def is_session(self, day):
            raise RuntimeError("no calendar")
    assert osx.settlement_day(Broken(), "2026-11-24") is None


@pytest.mark.parametrize("trade, settles", [
    ("2026-10-09", "2026-10-13"),    # Columbus Day Mon 10-12 is an XNYS session but not a settlement day
    ("2026-10-12", "2026-10-13"),    # a lot traded on Columbus Day itself (a session) settles the Tuesday
    ("2026-11-10", "2026-11-12"),    # Veterans Day Wed 11-11: NYSE trades, nothing settles
    ("2026-11-11", "2026-11-12"),    # a lot traded on Veterans Day itself settles the next day
    ("2029-11-09", "2029-11-13"),    # Veterans Day Sun 11-11 is observed Mon 11-12
    ("2028-11-09", "2028-11-10"),    # Veterans Day Sat 11-11: not moved, Friday 11-10 still settles
    ("2027-06-17", "2027-06-21"),    # Juneteenth Sat 06-19: XNYS is closed Fri 06-18 (the Fed is not), so Monday
    ("2021-12-30", "2021-12-31"),    # New Year's Day Sat 2022-01-01: XNYS open Fri 12-31 and it settles (not moved)
    ("2018-11-09", "2018-11-13")])   # Veterans Day Sun 2018-11-11 observed Mon 11-12, an XNYS session
def test_a_lot_settles_on_the_first_session_after_its_trade_date_that_is_also_a_settlement_day(trade, settles):
    assert WIDE.is_session(settles)
    assert osx.settlement_day(WIDE, trade) == settles


@pytest.mark.parametrize("day, holiday", [
    ("2026-01-01", True), ("2026-01-19", True), ("2026-01-12", False),     # New Year's Day; MLK Day: 3rd Mon Jan
    ("2026-02-16", True), ("2026-02-09", False),                           # Washington's Birthday: 3rd Mon Feb
    ("2026-05-25", True), ("2026-05-18", False),                           # Memorial Day: last Mon May
    ("2027-05-31", True), ("2027-05-24", False),                           # last Mon May on the 31st
    ("2026-06-19", True), ("2026-07-03", False),                           # Juneteenth; Sat Jul 4 not moved to Fri
    ("2027-07-05", True), ("2027-07-02", False),                           # Sun Jul 4 observed Mon; Friday is not
    ("2026-09-07", True), ("2026-10-12", True), ("2026-10-05", False),     # Labor Day: 1st Mon Sep; Columbus: 2nd Mon
    ("2026-11-11", True), ("2026-11-26", True), ("2026-11-19", False),     # Veterans Day; Thanksgiving: 4th Thu Nov
    ("2028-11-10", False), ("2029-11-12", True),                           # Veterans Day Sat: Fri open; Sun: Mon off
    ("2027-06-18", False), ("2021-12-31", False),                          # Sat Juneteenth, Sat New Year: Fri open
    ("2022-06-20", True), ("2022-12-26", True),                            # Sun Juneteenth, Sun Christmas: Mon off
    ("2026-12-25", True), ("2026-12-24", False), ("2026-11-27", False)])   # Christmas; its eve and Black Friday settle
def test_the_settlement_holidays_are_the_federal_reserves_with_sunday_moved_to_monday_and_saturday_not_moved(day,
                                                                                                            holiday):
    assert osx.us_settlement_holiday(day) is holiday


def order(tag, sent):
    return {"tag": tag, "sent_at": sent}


def fill(i, tag, units, t, sym=SYM):
    return {"id": i, "tag": tag, "symbol": sym, "units": units, "time": t}


def test_the_trade_date_is_the_day_the_order_was_sent_never_the_reconcile_that_saw_the_fill():
    sent = ny("2026-11-24 15:44")
    seen = ny("2026-11-25 09:50")                                    # first seen the next morning
    figures = osx.book_settled_units([fill(1, "b-SPY", 10.0, seen)], [order("b-SPY", sent)], [], CAL, "2026-11-25")
    assert osx.lot_trade_day(fill(1, "b-SPY", 10.0, seen), order("b-SPY", sent)) == "2026-11-24"
    assert figures == {SYM: 10.0}                                     # traded 11-24, settled 11-25


@pytest.mark.parametrize("o", [None, order("b-SPY", None), order("b-SPY", float("nan")),
                               order("b-SPY", ny("2026-11-24 10:00"))])
def test_a_lot_with_no_trade_date_is_never_settled(o):
    f = fill(1, "b-SPY", 10.0, ny("2026-11-24 09:59"))               # the last: a fill row before its order was sent
    orders = [] if o is None else [o]
    for day in ("2026-11-25", "2026-12-31"):
        assert osx.book_settled_units([f], orders, [], CAL, day) == {SYM: 0.0}


def test_a_sale_and_an_owners_units_out_are_taken_from_the_settled_lots_first_and_a_split_multiplies():
    orders = [order("a", ny("2026-11-23 10:00")), order("b", ny("2026-11-24 10:00")), order("c", ny("2026-11-24 11:00"))]
    fills = [fill(1, "a", 6.0, ny("2026-11-23 10:01")), fill(2, "b", 4.0, ny("2026-11-24 10:01")),
             fill(3, "c", -2.0, ny("2026-11-24 11:01"))]
    entries = [{"id": 1, "time": ny("2026-11-24 12:00"), "kind": "units_out", "symbol": SYM, "units": 1.0},
               {"id": 2, "time": ny("2026-11-24 13:00"), "kind": "split", "symbol": SYM, "ratio": 2.0},
               {"id": 3, "time": ny("2026-11-24 13:00"), "kind": "cash_in", "amount": 5.0}]
    assert osx.book_settled_units(fills, orders, entries, CAL, "2026-11-24") == {SYM: (6.0 - 2.0 - 1.0) * 2}
    assert osx.book_settled_units(fills, orders, entries, CAL, "2026-11-25") == {SYM: (10.0 - 2.0 - 1.0) * 2}


def test_a_lot_bought_the_day_before_veterans_day_is_unsettled_on_it_and_settled_the_day_after():
    lot = ([fill(1, "b-SPY", 10.0, ny("2026-11-10 10:01"))], [order("b-SPY", ny("2026-11-10 10:00"))], [])
    assert osx.book_settled_units(*lot, CAL, "2026-11-11") == {SYM: 0.0}     # NYSE trades 11-11, nothing settles
    assert osx.book_settled_units(*lot, CAL, "2026-11-12") == {SYM: 10.0}


def test_the_settled_units_never_go_below_zero():
    orders = [order("a", ny("2026-11-24 10:00")), order("b", ny("2026-11-24 11:00"))]
    fills = [fill(1, "a", 5.0, ny("2026-11-24 10:01")), fill(2, "b", -5.0, ny("2026-11-24 11:01"))]
    assert osx.book_settled_units(fills, orders, [], CAL, "2026-11-24") == {SYM: 0.0}


@pytest.mark.parametrize("cap, difference, legs", [(None, "-3.5", ["-3", "-0.5"]), (2.25, "-2.25", ["-2", "-0.25"]),
                                                   (3.0, "-3", ["-3"]), (0.0, "0", [])])
def test_the_split_never_sells_beyond_the_cap(cap, difference, legs):
    split = osx.split_difference(-3.5, 100.0, "sell", 3.5, cap=cap)
    assert split.difference == Decimal(difference) and [leg.units for leg in split.legs] == [Decimal(x) for x in legs]


def test_the_zero_rule_sells_the_holding_only_as_far_as_the_cap():
    free = osx.split_difference(-0.04, 200.0, "sell", 0.05)          # the target 0.01 is worth 2.00: taken as zero
    capped = osx.split_difference(-0.04, 200.0, "sell", 0.05, cap=0.03)
    assert free.target_zeroed and [leg.units for leg in free.legs] == [Decimal("-0.05")]
    assert capped.target_zeroed and [leg.units for leg in capped.legs] == [Decimal("-0.03")]
    assert capped.difference == Decimal("-0.03")


@pytest.mark.parametrize("bad", [-1.0, float("nan"), "1", True, osx.MAX_UNITS * 2])
def test_a_cap_that_is_not_a_plain_number_is_refused(bad):
    with pytest.raises(osx.OrderStepError):
        osx.split_difference(-3.5, 100.0, "sell", 3.5, cap=bad)


# ---- the run

def test_the_11_24_shape_a_fill_reconciled_the_same_morning_is_not_sold_until_the_next_session(tmp_path):
    w = World(tmp_path / "w")
    w.clock.t = ny("2026-11-24 09:55")                               # submitted in the run at 9:55 (6:55 Pacific)
    w.paper = paper({"a": {SYM: 10.0}})
    out = w.run()
    (tag,) = w.venue.submits()
    w.clock.t += 1                                                   # filled at the venue a second later
    assert w.venue.fill(tag) == 10.0
    out = sell_all(w, "2026-11-24 10:10")                            # reconciled at 10:10 (7:10 Pacific): T+0
    assert out["status"] == "done" and sold(w, out) == [], out
    assert out["sale_caps"] == {SYM: {"cap": 0.0, "book_settled": 0.0, "account_sellable": 10.0, "wanted": -10.0,
                                      "units": 0.0}}, out
    assert out["sales_left_unsettled"] == {SYM: 10.0}
    (skip,) = [s for s in out["skipped"] if s["symbol"] == SYM]
    assert skip["reason"].startswith("the sale cap is 0 (the book's settled units 0, the account's sellable quantity 10)")
    out = sell_all(w, "2026-11-25 10:00")                            # the next session: settled
    assert out["status"] == "done" and sold(w, out) == [-10.0], out
    assert out["sale_caps"][SYM]["cap"] == 10.0 and out["sales_left_unsettled"] == {}


def test_a_lot_bought_the_day_before_thanksgiving_is_sold_on_the_friday(tmp_path):
    w = bought(tmp_path / "w", "2026-11-25 10:00")
    out = sell_all(w, "2026-11-25 14:00")
    assert sold(w, out) == [] and out["sale_caps"][SYM]["book_settled"] == 0.0, out
    w.next_day()
    assert pd.Timestamp(w.clock.t, unit="s", tz="UTC").tz_convert("America/New_York").day == 27
    out = sell_all(w)
    assert out["status"] == "done" and sold(w, out) == [-10.0], out


def test_the_owners_own_units_are_never_counted(tmp_path):
    w = bought(tmp_path / "w", held={SYM: 5.0})                      # the owner holds 5 of the same symbol
    out = sell_all(w, "2026-11-24 14:00")
    assert sold(w, out) == [] and out["sale_caps"][SYM] == {
        "cap": 0.0, "book_settled": 0.0, "account_sellable": 15.0, "wanted": -10.0, "units": 0.0}, out
    out = sell_all(w, "2026-11-25 10:00")
    assert sold(w, out) == [-10.0] and out["sale_caps"][SYM]["book_settled"] == 10.0, out
    assert w.venue.held[SYM] == 5.0                                  # the owner's 5 still there


def test_a_sale_over_the_book_settled_units_is_cut_to_them(tmp_path):
    w = bought(tmp_path / "w", "2026-11-24 10:00", 10.0)
    w.clock.t = ny("2026-11-25 10:00")
    w.paper = paper({"a": {SYM: 15.0}})                              # 5 more, unsettled today
    assert sold(w, w.run()) == [5.0]
    out = sell_all(w, "2026-11-25 11:00")
    assert out["status"] == "done" and sold(w, out) == [-10.0], out
    assert out["sale_caps"][SYM] == {"cap": 10.0, "book_settled": 10.0, "account_sellable": 15.0, "wanted": -15.0,
                                     "units": -10.0}
    assert out["sales_left_unsettled"] == {SYM: 5.0}


def test_a_sale_over_the_accounts_sellable_quantity_is_cut_to_it_rounded_down(tmp_path):
    w = bought(tmp_path / "w")
    w.venue.sellable_answer[SYM] = 3.7                               # an open sell of the owner's holds the rest
    out = sell_all(w, "2026-11-25 10:00")
    assert sold(w, out) == [-3.0] and out["sale_caps"][SYM]["cap"] == 3.7, out
    assert out["sales_left_unsettled"] == {SYM: 7.0}


@pytest.mark.parametrize("answer, words", [(ConnectionError("down"), "could not be read: ConnectionError"),
                                           (None, "not a finite number"), (float("nan"), "not a finite number"),
                                           (-1.0, "not a finite number"), (True, "not a finite number"),
                                           ("missing", "could not be read: TypeError")])
def test_an_account_sellable_quantity_that_cannot_be_had_sends_nothing(tmp_path, answer, words):
    w = bought(tmp_path / "w")
    if answer == "missing":
        w.venue.sellable = None                                      # a venue without a usable method: calling fails
    else:
        w.venue.sellable_answer[SYM] = answer
    out = sell_all(w, "2026-11-25 10:00")
    assert out["status"] == "done" and sold(w, out) == [] and w.venue.submits()[1:] == [], out
    assert out["sale_caps"][SYM]["cap"] == 0.0 and words in out["sale_caps"][SYM]["why"], out


def test_a_book_whose_settled_units_cannot_be_worked_out_sends_no_sale(tmp_path, monkeypatch):
    w = bought(tmp_path / "w")

    def broken(*a, **k):
        raise osx.BookError("a figure is not a number")
    monkeypatch.setattr(osx, "book_settled_units", broken)
    out = sell_all(w, "2026-11-25 10:00")
    assert sold(w, out) == [] and "the book's settled units could not be worked out" in out["sale_caps"][SYM]["why"]


def test_a_purchase_is_never_capped_and_a_run_without_a_sale_reports_no_caps(tmp_path):
    w = bought(tmp_path / "w")
    out = w.run()
    assert "sale_caps" not in out and "sales_left_unsettled" not in out and w.venue.sellable_calls == []


def test_the_cap_is_read_once_per_sale_symbol(tmp_path):
    w = bought(tmp_path / "w")
    sell_all(w, "2026-11-25 10:00")
    assert w.venue.sellable_calls == [SYM]


# ---- the stop in front of the cap: the account holding less than the book counts as its own

def test_an_unrecorded_hand_sale_stops_the_run_at_the_reconcile_before_any_sale_is_capped_or_sent(tmp_path):
    w = bought(tmp_path / "w")
    w.venue.held[SYM] -= 4.0                                         # sold by hand, never recorded: the account is short
    w.venue.calls.clear()
    out = sell_all(w, "2026-11-25 10:00")
    assert out["status"] == "stopped" and out["stage"] == "reconcile", out
    assert "submit" not in w.venue.names() and w.venue.sellable_calls == [], out
    assert "sale_caps" not in out and out["orders_sent"] == 0


def test_in_the_shared_run_an_unrecorded_hand_sale_stops_it_at_shortfall_before_any_sale_is_capped(tmp_path,
                                                                                                 monkeypatch, capsys):
    w = SW(tmp_path / "w", monkeypatch, capsys)
    w.open_book()
    assert w.shared()["status"] == "done" and w.book() == {SYM: 100.0}
    w.next_day()                                                     # the book's 100 have settled
    w.venue.held[SYM] -= 30.0                                        # the owner sells 30 of them by hand
    w.paper = paper({"a": {SYM: 0.0}})
    w.venue.calls.clear()
    out = w.shared()
    assert out["status"] == "stopped" and out["stage"] == "shortfall", out
    assert "submit" not in w.venue.names() and w.venue.sellable_calls == [] and "sale_caps" not in out, out
