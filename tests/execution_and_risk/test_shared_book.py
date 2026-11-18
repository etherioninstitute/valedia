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
    return entry(1, T0 - 3600, book_figures([], [], [], {}, DAY), "cash_in", amount=D(amount))


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
    fig = book_figures(led.fills(), led.orders(), [opening()], {"SPY": 100.0}, DAY)
    assert fig.units == {"SPY": D("3.0")} and "AAPL" not in fig.units
    assert {s: float(u) for s, u in fig.units.items()} == osx.broker_book(led.fills())
    # cost: 1 at 100.5, then 2 more making an average of 100.25 over 3: 300.75 - 100.5 = 200.25; fees 0.75
    assert fig.cash == D("10000") - D("300.75") - D("0.75") and fig.cash_bounded is False


def test_book_cash_stake_less_costs_plus_proceeds_less_fees():
    o = [order("b-SPY", "SPY", 10.0, limit=101.0, fee=1.0), order("s-QQQ", "QQQ", -4.0, limit=99.0, fee=0.35)]
    f = [fill(1, "b-SPY", "SPY", 10.0, 100.01, fee=1.0), fill(2, "s-QQQ", "QQQ", -4.0, 99.5, fee=0.35)]
    fig = book_figures(f, o, [opening("5000")], {"SPY": 100.0, "QQQ": 99.0}, DAY)
    assert fig.cash == fig.cash_low == fig.cash_high == D("5000") - D("1000.1") - D("1.0") + D("398.0") - D("0.35")
    assert fig.units == {"SPY": D("10.0"), "QQQ": D("-4.0")}
    assert fig.value == fig.cash + D("10.0") * D("100.0") + D("-4.0") * D("99.0")
    assert fig.units_outstanding == D("5000") and fig.value_per_unit == fig.value / D("5000")


def test_unpriced_piece_bounded_by_limit():
    o = [order("b-SPY", "SPY", 10.0, limit=101.0, fee=0.0), order("s-TLT", "TLT", -2.0, limit=88.0, fee=0.0)]
    f = [fill(1, "b-SPY", "SPY", 6.0, 100.0, fee=0.0), fill(2, "b-SPY", "SPY", 4.0, None, fee=0.0),
         fill(3, "s-TLT", "TLT", -2.0, None, fee=0.0)]
    fig = book_figures(f, o, [opening()], {"SPY": 100.0, "TLT": 90.0}, DAY)
    assert fig.cash_bounded is True
    # the buy's 4 unpriced units cost at most their limit; the sale's 2 brought at least theirs
    assert fig.cash_low == D("10000") - D("600.0") - D("4") * D("101.0") + D("2") * D("88.0")
    assert fig.cash_high is None                                 # a sale's price has no upper bound
    assert fig.cash == fig.cash_low and any("b-SPY" in w for w in fig.bounded_by)
    buy_only = book_figures(f[:2], o[:1], [opening()], {"SPY": 100.0}, DAY)
    assert buy_only.cash_high == D("10000") - D("600.0")         # a buy cost at least nothing more


def test_market_piece_bounded_by_collar():
    o = [order("b-GLD.r", "GLD", 0.4, collar=201.0, fee=0.0)]
    f = [fill(1, "b-GLD.r", "GLD", 0.4, None, fee=0.0)]
    fig = book_figures(f, o, [opening()], {"GLD": 200.0}, DAY)
    assert fig.cash_bounded is True and fig.cash_low == D("10000") - D("0.4") * D("201.0")
    assert fig.cash_high == D("10000")


def test_unknown_fee_marks_cash_bounded():
    o = [order("b-SPY", "SPY", 10.0, limit=101.0, fee=None)]
    f = [fill(1, "b-SPY", "SPY", 10.0, 100.0, fee=None)]
    fig = book_figures(f, o, [opening()], {"SPY": 100.0}, DAY)
    assert fig.cash_bounded is True and fig.cash_low is None and fig.cash is None
    assert fig.cash_high == D("10000") - D("1000.0")              # a fee is never below nothing
    assert fig.value is None and fig.value_per_unit is None and fig.usable_cash is None
    assert any("fee" in w and "b-SPY" in w for w in fig.bounded_by)


def test_stake_entry_moves_units_not_nav():
    o = [order("b-SPY", "SPY", 10.0, limit=101.0, fee=1.0)]
    f = [fill(1, "b-SPY", "SPY", 10.0, 100.0, fee=1.0)]
    marks = {"SPY": 110.0}
    e1 = opening("10000")
    before = book_figures(f, o, [e1], marks, DAY)
    assert before.value == D("10000") - D("1001.0") + D("1100.0")
    e2 = entry(2, T0 + 100, before, "cash_in", amount=D("2500"))
    after = book_figures(f, o, [e1, e2], marks, DAY)
    assert abs(after.value_per_unit - before.value_per_unit) <= TINY
    assert after.units_outstanding > before.units_outstanding and after.cash == before.cash + D("2500")
    e3 = entry(3, T0 + 200, after, "cash_out", amount=D("700.25"))
    out = book_figures(f, o, [e1, e2, e3], marks, DAY)
    assert abs(out.value_per_unit - before.value_per_unit) <= TINY and out.cash == after.cash - D("700.25")


# ---- R8a: the cash usable for purchases today


def test_usable_cash_leaves_out_the_proceeds_of_todays_sales():
    o = [order("s1-SPY", "SPY", -2.0, limit=99.0, fee=0.0), order("s0-QQQ", "QQQ", -1.0, limit=50.0, fee=0.0)]
    f = [fill(1, "s1-SPY", "SPY", -2.0, 100.0, fee=0.0, t=T0), fill(2, "s0-QQQ", "QQQ", -1.0, 50.0, fee=0.0, t=YESTERDAY_T)]
    fig = book_figures(f, o, [opening("1000")], {"SPY": 100.0, "QQQ": 50.0}, DAY)
    assert fig.cash == D("1000") + D("200.0") + D("50.0")
    assert fig.usable_cash == D("1000") + D("50.0")              # yesterday's proceeds count, today's do not
    tomorrow = book_figures(f, o, [opening("1000")], {"SPY": 100.0, "QQQ": 50.0}, "2025-11-19")
    assert tomorrow.usable_cash == tomorrow.cash


# ---- owner entries in the figures


def test_units_out_and_split_move_the_units_the_book_counts():
    o = [order("b-SPY", "SPY", 10.0, limit=101.0, fee=0.0)]
    f = [fill(1, "b-SPY", "SPY", 10.0, 100.0, fee=0.0)]
    e1 = opening()
    fig = book_figures(f, o, [e1], {"SPY": 100.5}, DAY)
    e2 = entry(2, T0 + 10, fig, "units_out", symbol="SPY", units=D("3"), mark=D("100.5"))
    out = book_figures(f, o, [e1, e2], {"SPY": 100.5}, DAY)
    assert out.units == {"SPY": D("7.0")} and out.cash == fig.cash
    assert abs(out.value_per_unit - fig.value_per_unit) <= TINY
    e3 = entry(3, T0 + 20, out, "split", symbol="SPY", ratio=D("2"))
    split = book_figures(f, o, [e1, e2, e3], {"SPY": 50.25}, DAY)   # the venue's mark after a 2-for-1
    assert split.units == {"SPY": D("14.0")} and split.value == out.value
    assert split.units_outstanding == out.units_outstanding
    later = [fill(2, "b2-SPY", "SPY", 1.0, 50.0, fee=0.0, t=T0 + 30)]   # a fill after the split is not doubled
    more = book_figures(f + later, o + [order("b2-SPY", "SPY", 1.0, limit=51.0, fee=0.0)], [e1, e2, e3],
                        {"SPY": 50.25}, DAY)
    assert more.units == {"SPY": D("15.0")}


def test_next_entry_refuses_what_the_book_cannot_give():
    o = [order("b-SPY", "SPY", 10.0, limit=101.0, fee=0.0)]
    f = [fill(1, "b-SPY", "SPY", 10.0, 100.0, fee=0.0)]
    fig = book_figures(f, o, [opening("2000")], {"SPY": 100.0}, DAY)
    with pytest.raises(BookError, match="cash"):
        next_entry(fig, "cash_out", amount=D("1000.01"))          # the book's cash is 1000
    with pytest.raises(BookError, match="SPY"):
        next_entry(fig, "units_out", symbol="SPY", units=D("10.5"), mark=D("100"))
    for bad in (D("0"), D("-1"), D("NaN"), D("Infinity"), 5, "5", None):
        with pytest.raises(BookError):
            next_entry(fig, "cash_in", amount=bad)
    with pytest.raises(BookError, match="kind"):
        next_entry(fig, "dividend", amount=D("1"))
    unknown = book_figures(f, [order("b-SPY", "SPY", 10.0, limit=101.0, fee=None)], [opening("2000")],
                           {"SPY": 100.0}, DAY)
    with pytest.raises(BookError, match="value"):
        next_entry(unknown, "cash_in", amount=D("1"))             # an entry is never valued on an unknown figure


def test_entries_that_do_not_chain_are_refused_in_words():
    e1 = opening()
    e2 = entry(2, T0, book_figures([], [], [e1], {}, DAY), "cash_in", amount=D("5"))
    e2["units_before"] = "9999"
    with pytest.raises(BookError, match="entry 2"):
        book_figures([], [], [e1, e2], {}, DAY)


def test_a_fill_of_an_order_the_ledger_does_not_hold_is_refused():
    with pytest.raises(BookError, match="x-SPY"):
        book_figures([fill(1, "x-SPY", "SPY", 1.0, 10.0, fee=0.0)], [], [opening()], {"SPY": 10.0}, DAY)


def test_a_symbol_held_without_a_mark_leaves_the_value_unknown():
    o = [order("b-SPY", "SPY", 1.0, limit=101.0, fee=0.0)]
    fig = book_figures([fill(1, "b-SPY", "SPY", 1.0, 100.0, fee=0.0)], o, [opening()], {}, DAY)
    assert fig.unmarked == ("SPY",) and fig.value is None and fig.cash == D("9900.0")


def test_exact_decimals():
    o = [order("b-SPY", "SPY", 0.3, collar=34.0, fee=0.03)]
    f = [fill(i, "b-SPY", "SPY", 0.1, 33.33, fee=0.01) for i in (1, 2, 3)]
    fig = book_figures(f, o, [opening("100")], {"SPY": 33.33}, DAY)
    assert fig.units == {"SPY": D("0.3")} and fig.cash == D("100") - D("9.999") - D("0.03")


def test_book_figures_reads_no_file_clock_or_venue(monkeypatch):
    rng = random.Random(7)
    o, f, _ = history(rng, 5)
    e = [opening()]
    marks = marks_for(f, rng)
    want = book_figures(f, o, e, marks, DAY)

    def trip(*a, **k):
        raise AssertionError("book_figures read something outside its arguments")
    for owner, name in ((time, "time"), (time, "monotonic"), (builtins, "open"), (sqlite3, "connect")):
        monkeypatch.setattr(owner, name, trip)
    assert book_figures(f, o, e, marks, DAY) == want
    assert next_entry(want, "cash_in", amount=D("1")) == next_entry(want, "cash_in", amount=D("1"))


# ---- properties over seeded histories


@pytest.mark.parametrize("seed", SEEDS)
def test_prop_cash_entries_change_neither_value_per_unit_nor_any_other_figure(seed):
    rng = random.Random(9000 + seed)
    o, f, _ = history(rng)
    marks = marks_for(f, rng)
    e = [opening(str(rng.choice((5000, 10000, 25000))))]
    base = book_figures(f, o, e, marks, DAY)
    if base.value is None or base.value <= 0:
        return
    kind = rng.choice(("cash_in", "cash_out"))
    amount = D(str(round(rng.uniform(1, float(min(base.cash, D(2000))) if kind == "cash_out" else 5000), 2)))
    if kind == "cash_out" and (base.cash <= 1 or amount > base.cash):
        kind = "cash_in"
    moved = book_figures(f, o, e + [entry(2, T0 + 10_000, base, kind, amount=amount)], marks, DAY)
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
def test_prop_every_price_and_fee_known_gives_equal_bounds(seed):
    rng = random.Random(10_000 + seed)
    o, f, true_cash = history(rng)
    fig = book_figures(f, o, [opening("10000")], marks_for(f, rng), DAY)
    assert fig.cash_bounded is False and fig.bounded_by == ()
    assert fig.cash == fig.cash_low == fig.cash_high == D("10000") + true_cash


@pytest.mark.parametrize("seed", SEEDS)
def test_prop_a_missing_price_or_fee_only_widens_the_bounds(seed):
    rng = random.Random(11_000 + seed)
    o, f, true_cash = history(rng)
    marks = marks_for(f, rng)
    known = book_figures(f, o, [opening("10000")], marks, DAY)
    for row in rng.sample(f, rng.randint(1, len(f))):
        if rng.random() < 0.6:
            row["price"], row["price_covers_units"] = None, None
        else:
            row["fee"] = None
            next(x for x in o if x["tag"] == row["tag"])["fee"] = None
    gone = book_figures(f, o, [opening("10000")], marks, DAY)
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
    fig = book_figures(f, o, e, marks, DAY)
    sym = rng.choice(sorted(fig.units))
    if fig.value is not None and fig.units[sym] > 0:
        split = entry(2, stamp, fig, "split", symbol=sym, ratio=D(rng.choice(("2", "3", "0.5"))))
        e = e + [split]                                            # an entry at that same time too
    want = book_figures(f, o, e, marks, DAY)
    for _ in range(5):
        ff, oo, ee = f[:], o[:], e[:]
        rng.shuffle(ff), rng.shuffle(oo), rng.shuffle(ee)
        assert book_figures(ff, oo, ee, marks, DAY) == want
