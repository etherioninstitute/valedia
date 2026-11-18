"""T51c S2 (addendum 1, R4, R5, R8a): `book_figures`, the fund book's own figures from the ledger's own fills and
orders, the owner's entries and the marks; and `next_entry`, the figures an owner's entry is recorded with. Both pure:
no file, clock, venue or global read. Called by nothing yet (S4 wires them).

PROPERTIES over seeded histories: a `cash_in` or `cash_out` entry changes the value per unit by nothing (at its moment)
and changes no other figure than the cash, the value and the units outstanding, each by exactly the entry; with every
price and fee known the two bounds are equal; a missing price or fee only widens them (the true cash stays inside);
the order in which same-time rows are read changes no figure. Nothing here opens a socket."""
import builtins
import random
import socket
import sqlite3
import time
from decimal import Decimal as D

import pytest

from valedia_trader.execution_and_risk import order_step as osx
from valedia_trader.execution_and_risk.order_step import BookError, book_figures, next_entry
from valedia_trader.execution_and_risk.orders import OrderLedger

SYMS = ("SPY", "QQQ", "IWM", "TLT", "GLD")
T0 = 1_763_478_000.0                    # 2025-11-18 15:00 UTC: 10:00 in New York
DAY = "2025-11-18"
NOW = T0 + 600                          # the present time handed to book_figures (addendum 7, R22): on DAY
YESTERDAY_T = T0 - 86_400.0
SEEDS = range(60)
TINY = D("1e-40")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise OSError("the T51c S2 tests open no socket")
    for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "create_connection"),
                        (socket, "getaddrinfo")):
        monkeypatch.setattr(owner, name, refuse)


def entry(eid, t, figures, kind, **kw):
    """An owner's entry as the state file holds it: the figures next_entry gives, with an id and a time."""
    got = next_entry(figures, kind, **kw)
    return {"id": eid, "time": t, "day": DAY, "who": "test", "note": "seeded", **got}


def opening(amount="10000"):
    return entry(1, T0 - 3600, book_figures([], [], [], {}, DAY, now=NOW), "cash_in", amount=D(amount))


def order(tag, sym, units, limit=None, collar=None, fee=None):
    return {"tag": tag, "symbol": sym, "units": units, "limit_price": limit, "collar_price": collar,
            "order_type": "MARKET" if collar is not None else "LIMIT", "fee": fee}


def fill(fid, tag, sym, units, price, covers=None, fee=None, t=T0):
    return {"id": fid, "tag": tag, "symbol": sym, "units": units, "price": price,
            "price_covers_units": (abs(units) if covers is None else covers) if price is not None else None,
            "fee": fee, "time": t, "applied": 1}


def history(rng, n=None, t=T0):
    """Orders and fill rows with every price and fee known; buys at or under their limit, sales at or above it.
    Returns (orders, fills, the true cash those rows imply, as exact decimals)."""
    orders, fills, cash, fid = [], [], D(0), 0
    for i in range(n if n is not None else rng.randint(1, 8)):
        sym, side = rng.choice(SYMS), rng.choice((1, 1, -1))
        units = rng.choice((1.0, 2.0, 5.0, 10.0, 3.0, 0.5, 0.125, 0.37))
        market = units < 1 and rng.random() < 0.5
        ref = round(rng.uniform(10, 500), 2)
        bound = round(ref * (1.01 if side > 0 else 0.99), 2)
        tag = f"b{i}-{sym}" + (".r" if market else "")
        pieces = [units] if rng.random() < 0.6 else [units / 2, units / 2]
        fee_total = D(0)
        for p in pieces:
            price = round(bound - rng.uniform(0, 0.02) * bound, 2) if side > 0 else round(bound + rng.uniform(0, 0.02) * bound, 2)
            fee = rng.choice((0.0, 0.01, 0.35, 1.0))
            fid += 1
            fills.append(fill(fid, tag, sym, side * p, price, fee=fee, t=t + fid))
            cash += -D(repr(side * p)) * D(repr(price)) - D(repr(fee))
            fee_total += D(repr(fee))
        orders.append(order(tag, sym, side * units, limit=None if market else bound, collar=bound if market else None,
                            fee=float(fee_total)))
    return orders, fills, cash


def marks_for(fills, rng):
    return {f["symbol"]: round(rng.uniform(10, 500), 2) for f in fills}


# ---- the study's six


def test_book_units_are_own_fills():
    """Read from a real ledger: units are the signed sum of its own fill rows, applied or not, as broker_book is."""
    clock = [T0]
    led = OrderLedger(":memory:", clock=lambda: clock[0])

    class Venue:
        name, book, held = "stand-in", {}, {}

        def submit(self, symbol, units, tag, price=None, limit_price=None):
            self.book[tag] = {"state": "open", "filled_units": 0.0, "avg_price": None, "fee": None}
            return {"status": "accepted"}

        def order_status(self, tag):
            return dict(self.book[tag])

        def positions(self):
            return dict(self.held)
    v = Venue()
    led.reconcile(v, {})
    led.intend("a-SPY", "SPY", 3.0, 100.0, limit_price=101.0)
    led.submit_once(v, "a-SPY")
    v.book["a-SPY"].update(state="partly_filled", filled_units=1.0, avg_price=100.5, fee=0.5)
    clock[0] += 30
    v.held = {"SPY": 1.0, "AAPL": 50.0}                          # AAPL is the owner's: never the book's
    led.reconcile(v, {})
    led.mark_applied([f["id"] for f in led.unapplied_fills()])
    v.book["a-SPY"].update(state="filled", filled_units=3.0, avg_price=100.25, fee=0.75)
    clock[0] += 30
    led.reconcile(v, {"SPY": 1.0})                               # a second row, unapplied
    fig = book_figures(led.fills(), led.orders(), [opening()], {"SPY": 100.0}, DAY, now=NOW)
    assert fig.units == {"SPY": D("3.0")} and "AAPL" not in fig.units
    assert {s: float(u) for s, u in fig.units.items()} == osx.broker_book(led.fills())
    # cost: 1 at 100.5, then 2 more making an average of 100.25 over 3: 300.75 - 100.5 = 200.25; fees 0.75
    assert fig.cash == D("10000") - D("300.75") - D("0.75") and fig.cash_bounded is False


def test_book_cash_stake_less_costs_plus_proceeds_less_fees():
    o = [order("b-SPY", "SPY", 10.0, limit=101.0, fee=1.0), order("s-QQQ", "QQQ", -4.0, limit=99.0, fee=0.35)]
    f = [fill(1, "b-SPY", "SPY", 10.0, 100.01, fee=1.0), fill(2, "s-QQQ", "QQQ", -4.0, 99.5, fee=0.35)]
    fig = book_figures(f, o, [opening("5000")], {"SPY": 100.0, "QQQ": 99.0}, DAY, now=NOW)
    assert fig.cash == fig.cash_low == fig.cash_high == D("5000") - D("1000.1") - D("1.0") + D("398.0") - D("0.35")
    assert fig.units == {"SPY": D("10.0"), "QQQ": D("-4.0")}
    assert fig.value == fig.cash + D("10.0") * D("100.0") + D("-4.0") * D("99.0")
    assert fig.units_outstanding == D("5000") and fig.value_per_unit == fig.value / D("5000")


def test_unpriced_piece_bounded_by_limit():
    o = [order("b-SPY", "SPY", 10.0, limit=101.0, fee=0.0), order("s-TLT", "TLT", -2.0, limit=88.0, fee=0.0)]
    f = [fill(1, "b-SPY", "SPY", 6.0, 100.0, fee=0.0), fill(2, "b-SPY", "SPY", 4.0, None, fee=0.0),
         fill(3, "s-TLT", "TLT", -2.0, None, fee=0.0)]
    fig = book_figures(f, o, [opening()], {"SPY": 100.0, "TLT": 90.0}, DAY, now=NOW)
    assert fig.cash_bounded is True
    # the buy's 4 unpriced units cost at most their limit; the sale's 2 brought at least theirs
    assert fig.cash_low == D("10000") - D("600.0") - D("4") * D("101.0") + D("2") * D("88.0")
    assert fig.cash_high is None                                 # a sale's price has no upper bound
    assert fig.cash == fig.cash_low and any("b-SPY" in w for w in fig.bounded_by)
    buy_only = book_figures(f[:2], o[:1], [opening()], {"SPY": 100.0}, DAY, now=NOW)
    assert buy_only.cash_high == D("10000") - D("600.0")         # a buy cost at least nothing more


def test_market_piece_bounded_by_collar():
    o = [order("b-GLD.r", "GLD", 0.4, collar=201.0, fee=0.0)]
    f = [fill(1, "b-GLD.r", "GLD", 0.4, None, fee=0.0)]
    fig = book_figures(f, o, [opening()], {"GLD": 200.0}, DAY, now=NOW)
    assert fig.cash_bounded is True and fig.cash_low == D("10000") - D("0.4") * D("201.0")
    assert fig.cash_high == D("10000")


def test_unknown_fee_marks_cash_bounded():
    """A SALE with no fee reported stays as built (addendum 7, R22 (d)): the lower bound open, said in words."""
    o = [order("s-SPY", "SPY", -10.0, limit=99.0, fee=None)]
    f = [fill(1, "s-SPY", "SPY", -10.0, 100.0, fee=None)]
    fig = book_figures(f, o, [opening()], {"SPY": 100.0}, DAY, now=NOW)
    assert fig.cash_bounded is True and fig.cash_low is None and fig.cash is None
    assert fig.cash_high == D("10000") + D("1000.0")              # a fee is never below nothing
    assert fig.value is None and fig.value_per_unit is None and fig.usable_cash is None
    assert fig.bounded_by == ("order s-SPY: its fee is not known",)    # the words of the code before this slice


def test_stake_entry_moves_units_not_nav():
    o = [order("b-SPY", "SPY", 10.0, limit=101.0, fee=1.0)]
    f = [fill(1, "b-SPY", "SPY", 10.0, 100.0, fee=1.0)]
    marks = {"SPY": 110.0}
    e1 = opening("10000")
    before = book_figures(f, o, [e1], marks, DAY, now=NOW)
    assert before.value == D("10000") - D("1001.0") + D("1100.0")
    e2 = entry(2, T0 + 100, before, "cash_in", amount=D("2500"))
    after = book_figures(f, o, [e1, e2], marks, DAY, now=NOW)
    assert abs(after.value_per_unit - before.value_per_unit) <= TINY
    assert after.units_outstanding > before.units_outstanding and after.cash == before.cash + D("2500")
    e3 = entry(3, T0 + 200, after, "cash_out", amount=D("700.25"))
    out = book_figures(f, o, [e1, e2, e3], marks, DAY, now=NOW)
    assert abs(out.value_per_unit - before.value_per_unit) <= TINY and out.cash == after.cash - D("700.25")


# ---- R8a: the cash usable for purchases today


def test_usable_cash_leaves_out_the_proceeds_of_todays_sales():
    o = [order("s1-SPY", "SPY", -2.0, limit=99.0, fee=0.0), order("s0-QQQ", "QQQ", -1.0, limit=50.0, fee=0.0)]
    f = [fill(1, "s1-SPY", "SPY", -2.0, 100.0, fee=0.0, t=T0), fill(2, "s0-QQQ", "QQQ", -1.0, 50.0, fee=0.0, t=YESTERDAY_T)]
    fig = book_figures(f, o, [opening("1000")], {"SPY": 100.0, "QQQ": 50.0}, DAY, now=NOW)
    assert fig.cash == D("1000") + D("200.0") + D("50.0")
    assert fig.usable_cash == D("1000") + D("50.0")              # yesterday's proceeds count, today's do not
    tomorrow = book_figures(f, o, [opening("1000")], {"SPY": 100.0, "QQQ": 50.0}, "2025-11-19", now=NOW + 86_400)
    assert tomorrow.usable_cash == tomorrow.cash


# ---- owner entries in the figures


def test_units_out_and_split_move_the_units_the_book_counts():
    o = [order("b-SPY", "SPY", 10.0, limit=101.0, fee=0.0)]
    f = [fill(1, "b-SPY", "SPY", 10.0, 100.0, fee=0.0)]
    e1 = opening()
    fig = book_figures(f, o, [e1], {"SPY": 100.5}, DAY, now=NOW)
    e2 = entry(2, T0 + 10, fig, "units_out", symbol="SPY", units=D("3"), mark=D("100.5"))
    out = book_figures(f, o, [e1, e2], {"SPY": 100.5}, DAY, now=NOW)
    assert out.units == {"SPY": D("7.0")} and out.cash == fig.cash
    assert abs(out.value_per_unit - fig.value_per_unit) <= TINY
    e3 = entry(3, T0 + 20, out, "split", symbol="SPY", ratio=D("2"))
    split = book_figures(f, o, [e1, e2, e3], {"SPY": 50.25}, DAY, now=NOW)   # the venue's mark after a 2-for-1
    assert split.units == {"SPY": D("14.0")} and split.value == out.value
    assert split.units_outstanding == out.units_outstanding
    later = [fill(2, "b2-SPY", "SPY", 1.0, 50.0, fee=0.0, t=T0 + 30)]   # a fill after the split is not doubled
    more = book_figures(f + later, o + [order("b2-SPY", "SPY", 1.0, limit=51.0, fee=0.0)], [e1, e2, e3],
                        {"SPY": 50.25}, DAY, now=NOW)
    assert more.units == {"SPY": D("15.0")}
    same = [fill(2, "b2-SPY", "SPY", 1.0, 100.0, fee=0.0, t=T0 + 20)]   # a fill at the split's own time: before it
    tie = book_figures(f + same, o + [order("b2-SPY", "SPY", 1.0, limit=101.0, fee=0.0)], [e1, e2, e3],
                       {"SPY": 50.25}, DAY, now=NOW)
    assert tie.units == {"SPY": D("16.0")}


def test_next_entry_refuses_what_the_book_cannot_give():
    o = [order("b-SPY", "SPY", 10.0, limit=101.0, fee=0.0)]
    f = [fill(1, "b-SPY", "SPY", 10.0, 100.0, fee=0.0)]
    fig = book_figures(f, o, [opening("2000")], {"SPY": 100.0}, DAY, now=NOW)
    with pytest.raises(BookError, match="cash"):
        next_entry(fig, "cash_out", amount=D("1000.01"))          # the book's cash is 1000
    with pytest.raises(BookError, match="SPY"):
        next_entry(fig, "units_out", symbol="SPY", units=D("10.5"), mark=D("100"))
    for bad in (D("0"), D("-1"), D("NaN"), D("Infinity"), 5, "5", None):
        with pytest.raises(BookError):
            next_entry(fig, "cash_in", amount=bad)
    with pytest.raises(BookError, match="kind"):
        next_entry(fig, "dividend", amount=D("1"))
    unknown = book_figures([fill(1, "s-SPY", "SPY", -10.0, 100.0, fee=None)],
                           [order("s-SPY", "SPY", -10.0, limit=99.0, fee=None)], [opening("2000")],
                           {"SPY": 100.0}, DAY, now=NOW)      # a sale with no fee (R22 (d)): the value not known
    with pytest.raises(BookError, match="value"):
        next_entry(unknown, "cash_in", amount=D("1"))             # an entry is never valued on an unknown figure


def test_entries_that_do_not_chain_are_refused_in_words():
    e1 = opening()
    e2 = entry(2, T0, book_figures([], [], [e1], {}, DAY, now=NOW), "cash_in", amount=D("5"))
    e2["units_before"] = "9999"
    with pytest.raises(BookError, match="entry 2"):
        book_figures([], [], [e1, e2], {}, DAY, now=NOW)


def test_a_fill_of_an_order_the_ledger_does_not_hold_is_refused():
    with pytest.raises(BookError, match="x-SPY"):
        book_figures([fill(1, "x-SPY", "SPY", 1.0, 10.0, fee=0.0)], [], [opening()], {"SPY": 10.0}, DAY, now=NOW)


def test_a_symbol_held_without_a_mark_leaves_the_value_unknown():
    o = [order("b-SPY", "SPY", 1.0, limit=101.0, fee=0.0)]
    fig = book_figures([fill(1, "b-SPY", "SPY", 1.0, 100.0, fee=0.0)], o, [opening()], {}, DAY, now=NOW)
    assert fig.unmarked == ("SPY",) and fig.value is None and fig.cash == D("9900.0")


def test_exact_decimals():
    o = [order("b-SPY", "SPY", 0.3, collar=34.0, fee=0.03)]
    f = [fill(i, "b-SPY", "SPY", 0.1, 33.33, fee=0.01) for i in (1, 2, 3)]
    fig = book_figures(f, o, [opening("100")], {"SPY": 33.33}, DAY, now=NOW)
    assert fig.units == {"SPY": D("0.3")} and fig.cash == D("100") - D("9.999") - D("0.03")


def test_book_figures_reads_no_file_clock_or_venue(monkeypatch):
    rng = random.Random(7)
    o, f, _ = history(rng, 5)
    e = [opening()]
    marks = marks_for(f, rng)
    want = book_figures(f, o, e, marks, DAY, now=NOW)

    def trip(*a, **k):
        raise AssertionError("book_figures read something outside its arguments")
    for owner, name in ((time, "time"), (time, "monotonic"), (builtins, "open"), (sqlite3, "connect")):
        monkeypatch.setattr(owner, name, trip)
    assert book_figures(f, o, e, marks, DAY, now=NOW) == want
    assert next_entry(want, "cash_in", amount=D("1")) == next_entry(want, "cash_in", amount=D("1"))


# ---- properties over seeded histories


@pytest.mark.parametrize("seed", SEEDS)
def test_prop_cash_entries_change_neither_value_per_unit_nor_any_other_figure(seed):
    rng = random.Random(9000 + seed)
    o, f, _ = history(rng)
    marks = marks_for(f, rng)
    e = [opening(str(rng.choice((50000, 100000, 250000))))]       # enough that the value is above nothing
    base = book_figures(f, o, e, marks, DAY, now=NOW)
    assert base.value is not None and base.value > 0
    kind = rng.choice(("cash_in", "cash_out"))
    amount = D(str(round(rng.uniform(1, float(min(base.cash, D(2000))) if kind == "cash_out" else 5000), 2)))
    if kind == "cash_out" and (base.cash <= 1 or amount > base.cash):
        kind, amount = "cash_in", D(str(round(rng.uniform(1, 5000), 2)))
    moved = book_figures(f, o, e + [entry(2, T0 + 10_000, base, kind, amount=amount)], marks, DAY, now=NOW)
    sign = 1 if kind == "cash_in" else -1
    assert abs(moved.value_per_unit - base.value_per_unit) <= TINY * base.value_per_unit
    assert moved.units == base.units and moved.cash_bounded == base.cash_bounded and moved.unmarked == base.unmarked
    assert moved.bounded_by == base.bounded_by
    for name in ("cash", "cash_low", "cash_high", "usable_cash", "value", "value_high"):
        a, b = getattr(base, name), getattr(moved, name)
        assert (a is None and b is None) or b - a == sign * amount, (name, a, b)
    issued = moved.units_outstanding - base.units_outstanding
    assert abs(issued - sign * amount / base.value_per_unit) <= TINY * abs(issued)


@pytest.mark.parametrize("seed", SEEDS)
def test_prop_a_cash_entry_keeps_the_value_per_unit_as_the_same_text(seed):
    """Addendum 4, R14: by construction, the same decimal text before and after; not equal to sixty digits."""
    rng = random.Random(9500 + seed)
    o, f, _ = history(rng)
    marks = marks_for(f, rng)
    e = [opening(str(rng.choice((50000, 100000, 250000)))),]
    base = book_figures(f, o, e, marks, DAY, now=NOW)
    e.append(entry(2, T0 + 9_000, base, "cash_in", amount=D("333.33")))      # a value per unit with no short form
    fig = book_figures(f, o, e, marks, DAY, now=NOW)
    for kind in ("cash_in", "cash_out"):
        amount = D(str(round(rng.uniform(1, 1000), 2)))
        got = next_entry(fig, kind, amount=amount)
        assert got["value_per_unit_before"] == str(fig.value_per_unit)
        assert got["value_per_unit_after"] == got["value_per_unit_before"], (kind, got)


def test_a_cash_entry_keeps_the_value_per_unit_where_division_would_move_the_sixtieth_digit():
    fig = book_figures([], [], [opening("3")], {}, DAY, now=NOW)
    fig = book_figures([], [], [opening("3"), entry(2, T0, fig, "cash_in", amount=D("1"))], {}, DAY, now=NOW)
    import dataclasses
    marks_moved = dataclasses.replace(fig, value=D(10) / D(3) * 4, value_high=D(10) / D(3) * 4,
                                      value_per_unit=D(10) / D(3) * 4 / fig.units_outstanding)
    for amount in ("1", "7", "0.01"):
        got = next_entry(marks_moved, "cash_in", amount=D(amount))
        assert got["value_per_unit_after"] == got["value_per_unit_before"] == str(marks_moved.value_per_unit)


def _fee_fell_ledger():
    """A real ledger whose venue reports a fee total of 3.0 on the first piece and 1.0 at the end: a fall."""
    clock = [T0]
    led = OrderLedger(":memory:", clock=lambda: clock[0])

    class Venue:
        name, book, held = "stand-in", {}, {}

        def submit(self, symbol, units, tag, price=None, limit_price=None):
            self.book[tag] = {"state": "open", "filled_units": 0.0, "avg_price": None, "fee": None}
            return {"status": "accepted"}

        def order_status(self, tag):
            return dict(self.book[tag])

        def positions(self):
            return dict(self.held)
    v = Venue()
    led.reconcile(v, {})
    led.intend("a-SPY", "SPY", 2.0, 100.0, limit_price=101.0)
    led.submit_once(v, "a-SPY")
    v.book["a-SPY"].update(state="partly_filled", filled_units=1.0, avg_price=100.0, fee=3.0)
    clock[0] += 30
    v.held = {"SPY": 1.0}
    led.reconcile(v, {})
    v.book["a-SPY"].update(state="filled", filled_units=2.0, avg_price=100.0, fee=1.0)
    clock[0] += 30
    v.held = {"SPY": 2.0}
    led.reconcile(v, {})
    return led


def test_a_fee_total_that_falls_is_possible_with_the_ledger_as_built_and_the_bounds_hold_either_fee():
    """Addendum 4, R14 (finding 6): the ledger keeps the rows' higher fees and the order's total follows the fall, so
    the true fee is one of the two; the cash bounds hold the cash at either, and the cash is marked bounded."""
    led = _fee_fell_ledger()
    (o,) = led.orders()
    rows = [r["fee"] for r in led.fills() if r["fee"] is not None]
    assert o["fee"] == 1.0 and sum(rows) == 3.0                        # it happens: the rows keep 3, the total is 1
    fig = book_figures(led.fills(), led.orders(), [opening()], {"SPY": 100.0}, DAY, now=NOW)
    for fee in (D("1.0"), D("3.0")):
        truth = D("10000") - D("200.0") - fee
        assert fig.cash_low <= truth <= fig.cash_high, (fee, fig.cash_low, fig.cash_high)
    assert fig.cash_bounded is True and any("fee" in b and "a-SPY" in b for b in fig.bounded_by)
    led.close()


@pytest.mark.parametrize("order_fee, row_fees", [(1.0, (3.0,)), (0.0, (0.5, 0.5)), (2.0, (2.0,)), (0.25, (0.25, 0.0))])
def test_the_cash_bounds_hold_the_rows_fees_and_the_orders_total(order_fee, row_fees):
    o = [order("a-SPY", "SPY", 2.0, limit=101.0, fee=order_fee)]
    f = [fill(i + 1, "a-SPY", "SPY", 2.0 / len(row_fees), 100.0, fee=x, t=T0 + i) for i, x in enumerate(row_fees)]
    fig = book_figures(f, o, [opening()], {"SPY": 100.0}, DAY, now=NOW)
    for fee in {D(repr(order_fee)), sum((D(repr(x)) for x in row_fees), D(0))}:
        truth = D("10000") - D("200.0") - fee
        assert fig.cash_low <= truth <= fig.cash_high, (fee, fig.cash_low, fig.cash_high)
    if D(repr(order_fee)) == sum((D(repr(x)) for x in row_fees), D(0)):
        assert fig.cash_bounded is False and fig.cash_low == fig.cash_high   # no fall: exact, as before


@pytest.mark.parametrize("rows", [(None,), (None, None), (None, None, None)])
@pytest.mark.parametrize("total", [0.35, 1.0, 0.0, 1.0 / 3.0])
def test_a_fee_total_with_no_fee_row_is_the_fee_for_both_bounds(rows, total):
    """Addendum 6, R20: the rows are silent, not zero: the total is the fee, exact, nothing flagged."""
    o = [order("a-SPY", "SPY", float(len(rows)), limit=101.0, fee=total)]
    f = [fill(i + 1, "a-SPY", "SPY", 1.0, 100.0, fee=x, t=T0 + i) for i, x in enumerate(rows)]
    fig = book_figures(f, o, [opening()], {"SPY": 100.0}, DAY, now=NOW)
    truth = D("10000") - D("100.0") * len(rows) - D(repr(total))
    assert fig.cash_low == fig.cash_high == truth and fig.cash_bounded is False and fig.bounded_by == ()


def test_with_one_fee_row_the_pair_rule_stands_and_silent_rows_count_nothing():
    o = [order("a-SPY", "SPY", 2.0, limit=101.0, fee=1.0)]
    f = [fill(1, "a-SPY", "SPY", 1.0, 100.0, fee=0.25, t=T0), fill(2, "a-SPY", "SPY", 1.0, 100.0, fee=None, t=T0 + 1)]
    fig = book_figures(f, o, [opening()], {"SPY": 100.0}, DAY, now=NOW)
    assert fig.cash_low == D("10000") - D("200.0") - D("1.0") and fig.cash_high == D("10000") - D("200.0") - D("0.25")
    assert fig.cash_bounded is True


@pytest.mark.parametrize("seed", range(80))
def test_prop_the_bounds_hold_the_cash_at_either_fee_figure_whatever_the_floats_do(seed):
    """Addendum 5, R17: fee rows as the ledger keeps them (floats, each what the total rose by) against the order's
    total (a float the venue gave): the true cash from either figure lies inside the bounds, the upper bound is never
    under the lower, and a fee is named as bounding the cash only when the two differ by more than the tolerance."""
    rng = random.Random(77_000 + seed)
    orders, fills, cash_rows, cash_total, fid = [], [], D(10000), D(10000), 0
    for i in range(rng.randint(1, 6)):
        sym = rng.choice(SYMS)
        tag = f"f{i}-{sym}"
        pieces = rng.randint(1, 4)
        totals, total = [], 0.0
        for _ in range(pieces):
            total = total + rng.choice((0.1, 0.2, 0.35, 0.07, 1.0 / 3.0, 0.01))
            totals.append(total)
        if rng.random() < 0.3:                                       # the venue's total fell at the end
            totals[-1] = round(totals[-1] * rng.uniform(0.2, 0.9), 2)
        rows_fee, seen = [], 0.0
        for t in totals:
            rows_fee.append(max(t - seen, 0.0))                      # a row's fee is what the total rose by
            seen = max(seen, t)
        price = round(rng.uniform(10, 300), 2)
        for rf in rows_fee:
            fid += 1
            fills.append(fill(fid, tag, sym, 1.0, price, fee=rf, t=T0 + fid))
        orders.append(order(tag, sym, float(pieces), limit=price, fee=totals[-1]))
        cost = D(repr(price)) * pieces
        cash_rows -= cost + sum((D(repr(x)) for x in rows_fee), D(0))
        cash_total -= cost + D(repr(totals[-1]))
    fig = book_figures(fills, orders, [opening()], {}, DAY, now=NOW)
    assert fig.cash_low <= fig.cash_high
    for truth in (cash_rows, cash_total):
        assert fig.cash_low <= truth <= fig.cash_high, (seed, truth, fig.cash_low, fig.cash_high)
    if abs(cash_rows - cash_total) <= D("1e-12"):
        assert not any("fee total" in b for b in fig.bounded_by), fig.bounded_by


@pytest.mark.parametrize("seed", SEEDS)
def test_prop_every_price_and_fee_known_gives_equal_bounds(seed):
    rng = random.Random(10_000 + seed)
    o, f, true_cash = history(rng)
    fig = book_figures(f, o, [opening("10000")], marks_for(f, rng), DAY, now=NOW)
    assert fig.cash_bounded is False and fig.bounded_by == ()
    assert fig.cash == fig.cash_low == fig.cash_high == D("10000") + true_cash


@pytest.mark.parametrize("seed", SEEDS)
def test_prop_a_missing_price_or_fee_only_widens_the_bounds(seed):
    rng = random.Random(11_000 + seed)
    o, f, true_cash = history(rng)
    marks = marks_for(f, rng)
    known = book_figures(f, o, [opening("10000")], marks, DAY, now=NOW)
    for row in rng.sample(f, rng.randint(1, len(f))):
        if rng.random() < 0.6:
            row["price"], row["price_covers_units"] = None, None
        elif next(x for x in o if x["tag"] == row["tag"])["units"] < 0:   # a sale's fee: R22's purchases are below
            row["fee"] = None
            next(x for x in o if x["tag"] == row["tag"])["fee"] = None
        else:
            row["price"], row["price_covers_units"] = None, None
    gone = book_figures(f, o, [opening("10000")], marks, DAY, now=NOW)
    truth = D("10000") + true_cash
    assert gone.cash_bounded is True and gone.bounded_by
    assert gone.cash_low is None or gone.cash_low <= truth
    assert gone.cash_high is None or gone.cash_high >= truth
    assert gone.cash_low is None or gone.cash_low <= known.cash_low
    assert gone.cash_high is None or gone.cash_high >= known.cash_high
    assert gone.units == known.units


@pytest.mark.parametrize("seed", SEEDS)
def test_prop_the_order_same_time_rows_are_read_in_changes_no_figure(seed):
    rng = random.Random(12_000 + seed)
    o, f, _ = history(rng)
    stamp = rng.choice([r["time"] for r in f])
    for row in f:                                                  # many rows at one time
        if rng.random() < 0.5:
            row["time"] = stamp
    marks = marks_for(f, rng)
    e = [opening()]
    fig = book_figures(f, o, e, marks, DAY, now=NOW)
    sym = rng.choice(sorted(fig.units))
    if fig.value is not None and fig.units[sym] > 0:
        split = entry(2, stamp, fig, "split", symbol=sym, ratio=D(rng.choice(("2", "3", "0.5"))))
        e = e + [split]                                            # an entry at that same time too
    want = book_figures(f, o, e, marks, DAY, now=NOW)
    for _ in range(5):
        ff, oo, ee = f[:], o[:], e[:]
        rng.shuffle(ff), rng.shuffle(oo), rng.shuffle(ee)
        assert book_figures(ff, oo, ee, marks, DAY, now=NOW) == want


# ---- addendum 7, R22: a fee the venue leaves empty on a filled purchase

S4A_BASE = "3fe6268"                    # the code before this slice (T51c S4a's base), read from this checkout's history
RATE = D("0.000003")                    # the test's own copy of the published rate, for its own oracle
R = osx.LEDGER_RECHECK_SECONDS          # the ledger's own window, not a second figure


def published_bound(units, bound):
    """The test's oracle for B: the rate times the larger of the filled units and their value at the limit, up to
    the next cent (a whole cent stays)."""
    u = abs(D(repr(units)))
    v = max(u, u * D(repr(bound)))
    b = RATE * v
    cents = (b * 100).to_integral_value(rounding="ROUND_CEILING")
    return cents / 100


def done(o, state="filled", at=T0 + 5.0):
    """The order as the ledger holds it once finished: its state and when it reached it."""
    return {**o, "state": state, "finished_at": at if state != "never_arrived" else None,
            "never_arrived_at": at if state == "never_arrived" else None}


@pytest.fixture(scope="module")
def old():
    """The order step of the code before this slice, from git (it imports this tree's other modules, unchanged)."""
    import importlib.util
    import pathlib
    import subprocess
    import sys
    import tempfile
    root = pathlib.Path(__file__).resolve().parents[2]
    try:
        src = subprocess.run(["git", "-C", str(root), "show",
                              f"{S4A_BASE}:valedia_trader/execution_and_risk/order_step.py"],
                             capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        pytest.fail(f"the code before this slice, commit {S4A_BASE}, cannot be read from this checkout's history "
                    f"({type(e).__name__}); the comparison cannot be made, so it fails")
    folder = pathlib.Path(tempfile.mkdtemp(prefix="s4a_base_"))
    path = folder / "order_step_s4a_base.py"
    path.write_bytes(src)
    spec = importlib.util.spec_from_file_location("order_step_s4a_base", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["order_step_s4a_base"] = mod
    spec.loader.exec_module(mod)
    assert "now" not in __import__("inspect").signature(mod.book_figures).parameters
    return mod


def test_r22_the_rate_is_one_constant_with_its_citation():
    c = osx.PUBLISHED_PURCHASE_FEE
    assert c.rate == D("0.000003") and isinstance(c.rate, D)
    assert "Webull pricing page" in c.source and "Consolidated Audit Trail" in c.source and "2026-11-18" in c.source
    assert "shares" in c.note and "dollars" in c.note and "larger" in c.note
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.rate = D("0")


@pytest.mark.parametrize("units, bound, market, want", [
    (10.0, 101.0, False, D("0.01")),          # 1010 x rate = 0.00303: up to the cent, never down to nothing
    (0.4, 201.0, True, D("0.01")),            # a MARKET leg: its collar
    (1000.0, 500.0, False, D("1.50")),        # the value is the larger: 500000 x rate
    (10000.0, 0.5, False, D("0.03")),         # the units are the larger: 10000 x rate (the value would give 0.02)
    (2000.0, 500.0, False, D("3.00")),        # a whole cent stays
    (3.0, 33.34, False, D("0.01")),
])
def test_r22_a_purchase_with_no_fee_in_its_window_is_bounded_by_the_published_rate(units, bound, market, want):
    tag = "b-SPY.r" if market else "b-SPY"
    o = [done(order(tag, "SPY", units, limit=None if market else bound, collar=bound if market else None, fee=None))]
    f = [fill(1, tag, "SPY", units, bound - 0.01, fee=None)]
    assert published_bound(units, bound) == want
    fig = book_figures(f, o, [opening("10000000")], {"SPY": 100.0}, DAY, now=NOW)
    cost = D(repr(units)) * D(repr(bound - 0.01))
    assert fig.cash_high == D("10000000") - cost                  # the upper bound subtracts nothing
    assert fig.cash_low == D("10000000") - cost - want             # the lower subtracts B
    assert fig.cash == fig.cash_low and fig.cash_bounded is True and fig.fees_taken_as_none == 0
    assert fig.value is not None and fig.usable_cash is not None and fig.value_per_unit is not None   # KNOWN, bounded
    (words,) = fig.bounded_by
    assert tag in words and str(want) in words and "Webull pricing page" in words and "2026-11-18" in words
    assert "not known" not in words


@pytest.mark.parametrize("state, at, now", [
    ("filled", NOW - R - 1.0, NOW), ("cancelled", NOW - R - 0.5, NOW), ("expired", T0 - 400_000, NOW),
    ("never_arrived", NOW - R - 60, NOW)])
def test_r22_once_the_window_has_closed_the_fee_is_none_and_counted(state, at, now):
    o = [done(order("b-SPY", "SPY", 10.0, limit=101.0, fee=None), state, at)]
    f = [fill(1, "b-SPY", "SPY", 10.0, 100.0, fee=None, t=T0 - 500_000)]
    fig = book_figures(f, o, [opening()], {"SPY": 100.0}, DAY, now=now)
    assert fig.cash_low == fig.cash_high == D("10000") - D("1000.0")
    assert fig.cash_bounded is False and fig.bounded_by == () and fig.fees_taken_as_none == 1


@pytest.mark.parametrize("o_extra", [
    {"state": "filled", "finished_at": NOW - R},               # exactly at the edge: the ledger still asks (>= since)
    {"state": "filled", "finished_at": NOW - R + 1.0},
    {"state": "partly_filled", "finished_at": None},           # still asked about
    {"state": "unknown", "finished_at": None}, {"state": "sent", "finished_at": None},
    {"state": "filled", "finished_at": None},                  # no time it was finished: not known closed
    {"state": "filled", "finished_at": float("nan")},
    {"state": "rejected", "finished_at": NOW - 10 * R},        # not a state the ledger's window is kept for
    {},                                                        # no state at all
])
def test_r22_a_window_not_known_closed_is_open(o_extra):
    o = [{**order("b-SPY", "SPY", 10.0, limit=101.0, fee=None), **o_extra}]
    f = [fill(1, "b-SPY", "SPY", 10.0, 100.0, fee=None)]
    fig = book_figures(f, o, [opening()], {"SPY": 100.0}, DAY, now=NOW)
    assert fig.cash_low == D("10000") - D("1000.0") - D("0.01") and fig.cash_high == D("10000") - D("1000.0")
    assert fig.fees_taken_as_none == 0 and len(fig.bounded_by) == 1


@pytest.mark.parametrize("what", ["sale", "sale closed", "row fee without a total", "no limit"])
def test_r22_what_stays_as_built(what):
    if what.startswith("sale"):
        o = [done(order("s-SPY", "SPY", -10.0, limit=99.0, fee=None), at=NOW - R - 10 if "closed" in what else T0)]
        f = [fill(1, "s-SPY", "SPY", -10.0, 100.0, fee=None)]
    elif what == "row fee without a total":
        o = [done(order("s-SPY", "SPY", 10.0, limit=101.0, fee=None))]
        f = [fill(1, "s-SPY", "SPY", 5.0, 100.0, fee=0.01), fill(2, "s-SPY", "SPY", 5.0, 100.0, fee=None, t=T0 + 1)]
    else:                                                      # every unit priced, but no limit to compute B at
        o = [done({**order("s-SPY", "SPY", 10.0, fee=None), "limit_price": None})]
        f = [fill(1, "s-SPY", "SPY", 10.0, 100.0, fee=None)]
    fig = book_figures(f, o, [opening()], {"SPY": 100.0}, DAY, now=NOW)
    assert fig.cash_low is None and fig.value is None and fig.usable_cash is None and fig.fees_taken_as_none == 0
    assert any("s-SPY" in w and "fee is not known" in w for w in fig.bounded_by), fig.bounded_by


@pytest.mark.parametrize("fee", [0.0, 0.01, 0.02, 0.5])
def test_r22_a_fee_that_arrives_is_the_fee(fee):
    """R20 decides as today: a total with silent rows is the fee for both bounds, above B or not."""
    o = [done(order("b-SPY", "SPY", 10.0, limit=101.0, fee=fee))]
    f = [fill(1, "b-SPY", "SPY", 10.0, 100.0, fee=None)]
    fig = book_figures(f, o, [opening()], {"SPY": 100.0}, DAY, now=NOW)
    assert fig.cash_low == fig.cash_high == D("10000") - D("1000.0") - D(repr(fee))
    assert fig.bounded_by == () and fig.fees_taken_as_none == 0


@pytest.mark.parametrize("now", [True, None, "1763478600", float("nan"), float("inf"), NOW * 1000, NOW - 86_400,
                                 NOW + 86_400])
def test_r22_a_present_time_that_is_not_a_time_on_the_day_given_is_refused(now):
    """The window is judged at `now`; a time that is not a finite reading on the New York day handed in (a reading in
    milliseconds, another day's clock) is a caller's mistake, refused in words rather than closing windows early."""
    o = [done(order("b-SPY", "SPY", 10.0, limit=101.0, fee=None))]
    with pytest.raises(BookError, match="present time"):
        book_figures([fill(1, "b-SPY", "SPY", 10.0, 100.0)], o, [opening()], {"SPY": 100.0}, DAY, now=now)
    with pytest.raises(TypeError):
        book_figures([], [], [], {}, DAY)                          # never defaulted


def r22_history(rng, closed_share=0.0, now=NOW):
    """Seeded orders: some purchases with no fee (R22's), the rest with every fee known. Returns (orders, fills, the
    true cash before the R22 fees, {tag: B} of the R22 orders with an open window, tags of those closed)."""
    o, f, cash = history(rng)
    o = [done(x, at=T0 + 1 + i) for i, x in enumerate(o)]
    bounds, closed = {}, []
    for x in o:
        if x["units"] > 0 and rng.random() < 0.6:
            rows = [r for r in f if r["tag"] == x["tag"]]
            cash += sum((D(repr(r["fee"])) for r in rows), D(0))      # the fee the rows held is put back
            for r in rows:
                r["fee"] = None
            x["fee"] = None
            if rng.random() < closed_share:
                x["finished_at"] = now - R - rng.choice((0.001, 1.0, 3600.0))
                closed.append(x["tag"])
            else:
                lim = x["collar_price"] if x["order_type"] == "MARKET" else x["limit_price"]
                bounds[x["tag"]] = published_bound(sum(r["units"] for r in rows), lim)
    return o, f, cash, bounds, closed


@pytest.mark.parametrize("seed", range(80))
def test_prop_r22_the_bounds_hold_every_fee_between_nothing_and_b(seed):
    rng = random.Random(51_000 + seed)
    o, f, cash, bounds, closed = r22_history(rng, closed_share=0.3)
    marks = marks_for(f, rng)
    fig = book_figures(f, o, [opening("100000")], marks, DAY, now=NOW)
    start = D("100000") + cash
    assert fig.cash_low <= fig.cash_high and fig.value <= fig.value_high
    assert fig.cash_high == start and fig.cash_low == start - sum(bounds.values(), D(0))
    assert fig.fees_taken_as_none == len(closed)
    for _ in range(12):                                            # any fee each could have had, 0 and B among them
        fees = {t: rng.choice((D(0), b, (b * D(repr(rng.random()))).quantize(D("0.000001")))) for t, b in bounds.items()}
        truth = start - sum(fees.values(), D(0))
        assert fig.cash_low <= truth <= fig.cash_high
    assert set(t for t in bounds if any(t in w for w in fig.bounded_by)) == set(bounds)
    assert not any(t in w for t in closed for w in fig.bounded_by)


def _no_r22_history(rng):
    """Histories with no purchase whose fee is empty and whose rows are silent: missing prices, sales without fees,
    purchases with a row fee but no total, owner's entries."""
    o, f, _ = history(rng)
    o = [done(x, at=T0 + 1 + i) if rng.random() < 0.7 else x for i, x in enumerate(o)]
    for x in o:
        rows = [r for r in f if r["tag"] == x["tag"]]
        roll = rng.random()
        if roll < 0.2 and x["units"] < 0:
            x["fee"] = None
            for r in rows:
                r["fee"] = None
        elif roll < 0.35 and x["units"] > 0 and any(r["fee"] for r in rows):
            x["fee"] = None                                        # the rows still carry a fee
        elif roll < 0.55:
            rows[0]["price"], rows[0]["price_covers_units"] = None, None
    return o, f


@pytest.mark.parametrize("seed", range(80))
def test_prop_r22_a_history_with_no_such_order_gives_what_the_code_before_this_slice_gives(seed, old):
    rng = random.Random(52_000 + seed)
    o, f = _no_r22_history(rng)
    marks = marks_for(f, rng)
    e = [opening(str(rng.choice((50000, 250000))))]
    now = rng.choice((NOW, T0 + 0.0, T0 + 30_000.0))
    want = old.book_figures(f, o, e, marks, DAY)
    if want.value is not None and want.units:
        sym = rng.choice(sorted(want.units))
        if want.units[sym] > 0 and rng.random() < 0.5:
            e = e + [entry(2, T0 + 50, want, "split", symbol=sym, ratio=D("2"))]
            want = old.book_figures(f, o, e, marks, DAY)
    got = book_figures(f, o, e, marks, DAY, now=now)
    import dataclasses
    for field in dataclasses.fields(want):
        assert getattr(got, field.name) == getattr(want, field.name), field.name
    assert got.fees_taken_as_none == 0


@pytest.mark.parametrize("seed", range(60))
def test_prop_r22_moving_the_present_across_a_windows_end_changes_only_that_order(seed):
    rng = random.Random(53_000 + seed)
    o, f, cash, bounds, _ = r22_history(rng)
    if not bounds:
        return
    marks = marks_for(f, rng)
    e = [opening("100000")]
    day = "2025-11-21"                                         # T0 + 3 days: the windows' ends all fall on it
    crossing = rng.choice(sorted(bounds))
    edge = next(x["finished_at"] for x in o if x["tag"] == crossing) + R
    before = book_figures(f, o, e, marks, day, now=edge)          # the ledger still asks at the edge itself
    after = book_figures(f, o, e, marks, day, now=edge + 0.0005)
    crossed = {x["tag"] for x in o if x["tag"] in bounds and edge <= x["finished_at"] + R < edge + 0.0005}
    assert crossed == {crossing}                               # the orders finished one second apart
    assert after.cash_low - before.cash_low == bounds[crossing]
    assert after.cash_high == before.cash_high and after.units == before.units
    assert after.fees_taken_as_none - before.fees_taken_as_none == 1
    assert set(before.bounded_by) - set(after.bounded_by) == {w for w in before.bounded_by if crossing in w}
    assert set(after.bounded_by) <= set(before.bounded_by)
    assert after.value - before.value == bounds[crossing] and after.value_high == before.value_high


def _ledger_one_purchase(units, price, limit, fee, late):
    """A real ledger: one purchase filled; its fee `fee` reported with the fill, or (late) only at a later recheck."""
    clock = [T0]
    led = OrderLedger(":memory:", clock=lambda: clock[0])

    class Venue:
        name, book, held = "stand-in", {}, {}

        def submit(self, symbol, units, tag, price=None, limit_price=None):
            self.book[tag] = {"state": "open", "filled_units": 0.0, "avg_price": None, "fee": None}
            return {"status": "accepted"}

        def order_status(self, tag):
            return dict(self.book[tag])

        def positions(self):
            return dict(self.held)
    v = Venue()
    led.reconcile(v, {})
    led.intend("a-SPY", "SPY", units, price, limit_price=limit)
    led.submit_once(v, "a-SPY")
    v.book["a-SPY"].update(state="filled", filled_units=units, avg_price=price, fee=None if late else fee)
    clock[0] += 30
    v.held = {"SPY": units}
    led.reconcile(v, {})
    if late:
        clock[0] += 3600
        v.book["a-SPY"]["fee"] = fee
        led.reconcile(v, {"SPY": units})                     # the recheck finds the fee
    return led


@pytest.mark.parametrize("seed", range(40))
def test_prop_r22_a_fee_that_arrives_later_gives_the_figures_it_would_from_the_start(seed):
    rng = random.Random(54_000 + seed)
    units = rng.choice((1.0, 3.0, 10.0, 0.5, 250.0))
    price = round(rng.uniform(10, 400), 2)
    limit = round(price * 1.01, 2)
    b = published_bound(units, limit)
    fee = rng.choice((0.0, float(b), round(float(b) * rng.random(), 6), 0.02, 1.25))
    late, first = _ledger_one_purchase(units, price, limit, fee, True), _ledger_one_purchase(units, price, limit, fee, False)
    now = T0 + 7200
    if fee == 0.0:                       # the ledger as built records no total for a later report of 0 (no rise):
        late.close()                     # the order stays empty, so its window decides; see the test below
        first.close()
        return
    a = book_figures(late.fills(), late.orders(), [opening()], {"SPY": 100.0}, DAY, now=now)
    z = book_figures(first.fills(), first.orders(), [opening()], {"SPY": 100.0}, DAY, now=now)
    assert len(late.fills()) == 2 and late.fills()[1]["units"] == 0.0     # the ledger's own adjustment row
    for name in ("units", "cash", "cash_low", "cash_high", "cash_bounded", "bounded_by", "usable_cash", "value",
                 "value_high", "value_per_unit", "fees_taken_as_none"):
        assert getattr(a, name) == getattr(z, name), name
    cost = sum((D(repr(r["price"])) * D(repr(r["price_covers_units"])) for r in first.fills() if r["price"] is not None),
               D(0))                                       # the price the ledger derived from the venue's average
    truth = D("10000") - cost - D(repr(fee))
    assert a.cash_low == a.cash_high == truth
    if D(repr(fee)) <= b:                                       # before it arrived, the bounds held it
        empty = _ledger_one_purchase(units, price, limit, None, False)
        mid = book_figures(empty.fills(), empty.orders(), [opening()], {"SPY": 100.0}, DAY, now=now)
        assert mid.cash_low <= truth <= mid.cash_high
        empty.close()
    late.close()
    first.close()


def test_r22_a_fee_of_nothing_reported_late_is_not_stored_and_the_window_then_takes_it_as_none():
    """Found while building R22: a venue that reports a fee total of 0 only at a recheck leaves the order's total
    empty in the ledger as built (no rise). Inside the window the bounds still hold the truth (0 is in [0, B]); once
    the window has closed the cash is the cash a fee of 0 from the start gives."""
    late = _ledger_one_purchase(10.0, 100.0, 101.0, 0.0, True)
    first = _ledger_one_purchase(10.0, 100.0, 101.0, 0.0, False)
    assert late.orders()[0]["fee"] is None and first.orders()[0]["fee"] == 0.0
    truth = D("10000") - D("1000.0")
    inside = book_figures(late.fills(), late.orders(), [opening()], {"SPY": 100.0}, DAY, now=T0 + 7200)
    assert inside.cash_low == truth - D("0.01") and inside.cash_high == truth
    after = T0 + 4 * 86_400
    shut = book_figures(late.fills(), late.orders(), [opening()], {"SPY": 100.0}, "2025-11-22", now=after)
    z = book_figures(first.fills(), first.orders(), [opening()], {"SPY": 100.0}, "2025-11-22", now=after)
    assert shut.cash_low == shut.cash_high == z.cash_low == z.cash_high == truth and shut.value == z.value
    assert shut.fees_taken_as_none == 1 and z.fees_taken_as_none == 0
    late.close()
    first.close()


# ---- T51c S4a addendum 1, R32: the refusal names the order that makes the value unknown


def _bounded_and(other):
    """A purchase bounded only by the published rate (its tag sorts first, its line is long) and `other`."""
    o = [done(order("a0-SPY", "SPY", 10.0, limit=101.0, fee=None))]
    f = [fill(1, "a0-SPY", "SPY", 10.0, 100.0, fee=None)]
    if other == "sale":                                        # a sale with an empty fee: the lower bound open
        o.append(done(order("z1-QQQ", "QQQ", -4.0, limit=49.0, fee=None)))
        f.append(fill(2, "z1-QQQ", "QQQ", -4.0, 50.0, fee=None, t=T0 + 1))
    elif other == "rows without a total":                      # a purchase with fee rows but no total
        o.append(done(order("z1-QQQ", "QQQ", 4.0, limit=51.0, fee=None)))
        f += [fill(2, "z1-QQQ", "QQQ", 2.0, 50.0, fee=0.01, t=T0 + 1),
              fill(3, "z1-QQQ", "QQQ", 2.0, 50.0, fee=None, t=T0 + 2)]
    elif other == "no limit":                                  # units without a price and no limit to bound them
        o.append(done({**order("z1-QQQ", "QQQ", 4.0, fee=0.0), "limit_price": None}))
        f.append(fill(2, "z1-QQQ", "QQQ", 4.0, None, fee=0.0, t=T0 + 1))
    return f, o


@pytest.mark.parametrize("other, named", [
    ("sale", "order z1-QQQ: its fee is not known"),
    ("rows without a total", "order z1-QQQ: its fee is not known"),
    ("no limit", "order z1-QQQ: 4.0 units without a price, with no limit to bound them"),
])
def test_r32_the_refusal_names_the_order_that_leaves_a_bound_open(other, named):
    f, o = _bounded_and(other)
    fig = book_figures(f, o, [opening()], {"SPY": 100.0, "QQQ": 50.0}, DAY, now=NOW)
    assert fig.value is None and fig.open_by == (named,)
    assert len(fig.bounded_by) == 2 and fig.bounded_by[0].startswith("order a0-SPY: the venue reported no fee")
    with pytest.raises(BookError) as got:
        next_entry(fig, "cash_in", amount=D("1"))
    words = str(got.value)
    assert f"the book's value is not known ({named}" in words, words      # main's words stay findable, first
    assert "a0-SPY" not in words                                  # a bounded order is never given as the reason
    assert "1 other order is only bounded" in words, words


def test_r32_a_missing_mark_is_named_and_the_bounded_orders_counted():
    f, o = _bounded_and(None)
    o.append(done(order("a1-SPY", "SPY", 1.0, limit=101.0, fee=None)))
    f.append(fill(2, "a1-SPY", "SPY", 1.0, 100.0, fee=None, t=T0 + 1))
    f.append(fill(3, "b2-QQQ", "QQQ", 1.0, 50.0, fee=0.0, t=T0 + 2))
    o.append(done(order("b2-QQQ", "QQQ", 1.0, limit=51.0, fee=0.0)))
    fig = book_figures(f, o, [opening()], {"SPY": 100.0}, DAY, now=NOW)
    assert fig.value is None and fig.open_by == () and fig.unmarked == ("QQQ",)
    with pytest.raises(BookError) as got:
        next_entry(fig, "cash_in", amount=D("1"))
    words = str(got.value)
    assert "the book's value is not known (no mark for QQQ" in words and "2 other orders are only bounded" in words
    assert "a0-SPY" not in words and "a1-SPY" not in words


def test_r32_many_open_orders_are_named_first_and_the_count_survives_the_cut():
    f, o = _bounded_and(None)
    for i in range(12):
        tag = f"s{i:02d}-QQQ-{'x' * 30}"
        o.append(done(order(tag, "QQQ", -1.0, limit=49.0, fee=None)))
        f.append(fill(10 + i, tag, "QQQ", -1.0, 50.0, fee=None, t=T0 + 10 + i))
    fig = book_figures(f, o, [opening()], {"SPY": 100.0, "QQQ": 50.0}, DAY, now=NOW)
    with pytest.raises(BookError) as got:
        next_entry(fig, "cash_in", amount=D("1"))
    words = str(got.value)
    assert f"the book's value is not known (order s00-QQQ-{'x' * 30}: its fee is not known" in words
    assert "1 other order is only bounded" in words and "a0-SPY" not in words
