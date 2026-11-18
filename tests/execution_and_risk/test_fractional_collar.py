"""T51 A3: the collar before a MARKET (remainder) leg is sent, the verdict after its fill, and the fence, on the
practice path (addendum 1 R5, R6, R8; addendum 2 ruling C; addendum 4 points a, b, d).

R5, each a separate hold-back with a named reason (`collar` on the leg's `left_over` line), never a fault:
  B1 the leg's judged quote at most MARKET_QUOTE_MAX_AGE_SECONDS old at the moment of sending;
  B2 the second price source agrees on that quote (ruling C: with `fractional` on and none handed in, the run loads
     the one live mode loads; one that cannot be loaded, or any other verdict, holds the leg back);
  B3 the quote within TICKET_QUOTE_MOVE of the quote the leg was decided at (an approval: the ticket's quote);
  B4 the clock: from MARKET_AFTER_OPEN after the open to MARKET_BEFORE_CLOSE before the close;
  B5 its collar price c (the price a LIMIT leg would carry) charged to the purchase budget as r x c;
  B6 the minimum at the quote it is sent on.
R6: a MARKET leg's average is judged against c by today's `_limit_verdict` rules; beyond c is `breach` and `cap`.
R8: no order of either leg sells more than the ledger's own book of a fund.

Every second source here is a stand-in, and nothing opens a socket: the autouse guard fails any test that would
load or ask the real second source, or connect anywhere."""
import json
import random
import socket
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import pytest

from valedia_trader.data_and_fees import second_quote
from valedia_trader.execution_and_risk import go_live
from valedia_trader.execution_and_risk import order_step as osx
from test_order_step import Crash, GENEROUS, Second, approve, live_world, lpropose, paper, propose, ticket_rows, \
    ticket_world
from test_fractional_legs import FUNDS, LegVenue, batch_orders, book_without, check_fence, check_gate, check_shape, \
    leg_world

REAL_LOADER = getattr(osx, "_load_second_source", None)   # taken at import, before any guard replaces it


@pytest.fixture(autouse=True)
def guard(monkeypatch):
    """No test here opens a socket or reaches the real second price source: either fails the test."""
    seen = []

    def no_socket(*a, **k):
        seen.append("socket")
        raise OSError("the T51 A3 tests open no socket")
    for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "create_connection"),
                        (socket, "getaddrinfo")):
        monkeypatch.setattr(owner, name, no_socket)

    def tripwire(*a, **k):
        seen.append("second source")
        pytest.fail("the real second price source would be loaded or asked: hand the run a stand-in")
    monkeypatch.setattr(osx, "_load_second_source", tripwire, raising=False)
    monkeypatch.setattr(second_quote, "second_opinion", tripwire)
    yield seen
    assert seen == [], seen


def collar_world(tmp, **kw):
    w = leg_world(tmp, **kw)
    w.paper = paper({"a": {"SPY": 2.5}})                              # LIMIT 2 at 505.00, MARKET 0.5 with c = 505.00
    return w


def held_back(out, sym="SPY"):
    return [e for e in out["left_over"] if e["symbol"] == sym and e.get("leg") == "remainder"]


def advance_on_market_intend(w, seconds):
    """A ledger patch: time passes between a MARKET leg's checks and its sending (right after its intend)."""
    def patch(led):
        real = led.intend

        def intend(tag, *a, **k):
            rec = real(tag, *a, **k)
            if k.get("order_type") == "MARKET":
                w.clock.t += seconds
            return rec
        led.intend = intend
    return patch


def ask_again_at(w, price, age=60.0):
    """The quote function: every ask after the first finds `price` (the MARKET leg's own ask again)."""
    real, n = w.quotes, [0]

    def quotes(symbols):
        n[0] += 1
        if n[0] > 1:
            w.prices["SPY"], w.quote_age["SPY"] = price, age
        return real(symbols)
    w.quotes = quotes


# ---- the guard itself, and ruling C: the second source on practice

def test_a_test_run_that_would_load_the_real_second_source_or_open_a_socket_fails(tmp_path, guard):
    w = collar_world(tmp_path / "w")
    with pytest.raises(pytest.fail.Exception, match="real second price source"):
        w.run(fractional=True, second_opinion=None)                   # no stand-in handed in: the loader is reached
    assert guard == ["second source"]
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", 9))
    assert guard == ["second source", "socket"]
    guard.clear()


def test_ruling_c_a_fractional_practice_run_with_no_second_source_loads_the_one_live_mode_loads(tmp_path, monkeypatch):
    asked = Second()
    monkeypatch.setattr(osx, "_load_second_source", REAL_LOADER, raising=False)
    monkeypatch.setattr(second_quote, "second_opinion", asked)       # the real module's name, a stand-in behind it
    w = collar_world(tmp_path / "w")
    out = w.run(fractional=True, second_opinion=None)
    b = out["batch"]
    assert out["status"] == "done" and [a["tag"] for a in w.venue.args] == [f"{b}-SPY", f"{b}-SPY.r"], out
    assert [(c["symbol"], c["price"], c["sources"]) for c in asked.calls] == [("SPY", 500.0, go_live.SECOND_SOURCES)]


def test_ruling_c_the_live_path_loads_its_second_source_through_the_same_loader(tmp_path, monkeypatch):
    w = live_world(tmp_path / "w")
    loads = []
    monkeypatch.setattr(osx, "_load_second_source", lambda: loads.append(1) or w.second, raising=False)
    w.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    out = lpropose(w, second_opinion=None)
    assert out["status"] == "done" and out["ticket"]["orders"], out
    assert loads == [1] and [c["symbol"] for c in w.second.calls] == ["IWM"]


def test_ruling_c_a_fractional_run_with_no_market_leg_never_loads_or_asks_a_second_source(tmp_path):
    w = leg_world(tmp_path / "w")
    w.paper = paper({"a": {"SPY": 2.0, "QQQ": 3.0}})                  # whole shares only: no MARKET leg
    out = w.run(fractional=True, second_opinion=None)                 # the guard fails the test if it is loaded
    assert out["status"] == "done" and len(w.venue.args) == 2 and "faults" not in out


def test_ruling_c_with_fractional_off_the_practice_path_never_asks_a_second_source_over_seeded_worlds(tmp_path):
    runs = 0
    for k in range(12):
        rng = random.Random(57_000 + k)
        w = leg_world(tmp_path / f"w{k}", fill=rng.choice([0.0, 1.0]))
        asked = Second()
        for run in range(3):
            w.paper = paper({"a": {sym: rng.choice([0.5, 2.5, 3.0, 1.37]) for sym in rng.sample(FUNDS, 3)}})
            if rng.random() < 0.5:
                outs = [w.run(second_opinion=asked)]
            else:
                p = propose(w, second_opinion=asked)
                outs = [p] + ([approve(w, p["ticket_number"], second_opinion=asked)] if p.get("ticket_number") else [])
            assert all(o["status"] == "done" for o in outs), outs
            runs += len(outs)
            w.later(30)
        assert asked.calls == []
    assert runs > 40


@pytest.mark.parametrize("answer", ["disagree", "no second quote", "raise", "not a verdict", "cannot be loaded"])
def test_b2_any_answer_but_agree_holds_the_market_leg_back_and_its_limit_leg_goes(tmp_path, monkeypatch, answer):
    w = collar_world(tmp_path / "w")
    kw = {}
    if answer == "cannot be loaded":
        def broken():
            raise ImportError("no module named the second source")
        monkeypatch.setattr(osx, "_load_second_source", broken, raising=False)
        kw["second_opinion"] = None
    else:
        w.second.verdicts["SPY"] = {"raise": ConnectionError("down"), "not a verdict": "maybe"}.get(answer, answer)
    out = w.run(fractional=True, **kw)
    b = out["batch"]
    assert out["status"] == "done" and "faults" not in out, out
    assert [a["tag"] for a in w.venue.args] == [f"{b}-SPY"]
    (left,) = held_back(out)
    assert left["collar"] == "second_source" and "second price source" in left["reason"], left
    if answer == "cannot be loaded":
        assert "could not be loaded" in left["reason"] and "ImportError" in left["reason"]
    assert [e["collar"] for e in w.entries("left_over")] == ["second_source"]
    assert [o["tag"] for o in w.orders()] == [f"{b}-SPY"]                  # the remainder is never intended


def test_b2_the_second_source_is_asked_once_on_the_quote_the_market_leg_is_sent_on(tmp_path):
    w = collar_world(tmp_path / "w")
    out = w.run(fractional=True)
    assert [(c["symbol"], c["price"]) for c in w.second.calls] == [("SPY", 500.0)]
    assert [e["leg"] for e in out["sent"]] == ["whole", "remainder"]


# ---- B1: the quote's age at the moment of sending

@pytest.mark.parametrize("age, sent", [(60.0, True), (120.0, True), (121.0, False), (1199.0, False)])
def test_b1_a_market_legs_quote_older_than_120_seconds_holds_it_back_and_its_limit_leg_goes(tmp_path, age, sent):
    w = collar_world(tmp_path / "w")
    w.quote_age["SPY"] = age                                          # asked again: the venue's quote is that old
    out = w.run(fractional=True)
    b = out["batch"]
    assert out["status"] == "done" and "faults" not in out, out
    if sent:
        assert [a["tag"] for a in w.venue.args] == [f"{b}-SPY", f"{b}-SPY.r"]
        return
    assert [a["tag"] for a in w.venue.args] == [f"{b}-SPY"]
    (left,) = held_back(out)
    assert left["collar"] == "quote_age" and f"{osx.MARKET_QUOTE_MAX_AGE_SECONDS} seconds" in left["reason"], left
    assert w.second.calls == []                                       # B2 is asked only when the others pass


def test_b1_a_market_leg_whose_quote_ages_past_120_seconds_after_its_checks_is_not_sent(tmp_path):
    w = collar_world(tmp_path / "w")
    out = w.run(patch=advance_on_market_intend(w, 61), fractional=True)   # 60 s at the checks, 121 s at the send
    b = out["batch"]
    assert out["status"] == "done" and [a["tag"] for a in w.venue.args] == [f"{b}-SPY"], out
    (left,) = held_back(out)
    assert left["collar"] == "quote_age"
    assert {o["tag"]: o["state"] for o in w.orders()} == {f"{b}-SPY": "accepted", f"{b}-SPY.r": "intended"}


def test_b1_a_market_leg_asks_for_a_fresher_quote_and_is_sent_on_it(tmp_path):
    w = collar_world(tmp_path / "w")
    w.quote_age["SPY"] = 600.0                                        # fine for the LIMIT leg, too old for MARKET
    ask_again_at(w, 501.0)
    out = w.run(fractional=True)
    b = out["batch"]
    o = {x["tag"]: x for x in w.orders()}
    assert [a["tag"] for a in w.venue.args] == [f"{b}-SPY", f"{b}-SPY.r"], out
    assert o[f"{b}-SPY"]["reference_price"] == 500.0 and o[f"{b}-SPY.r"]["reference_price"] == 501.0
    assert o[f"{b}-SPY.r"]["collar_price"] == osx.limit_price(0.5, 501.0) == 506.01


# ---- B4: the clock

@pytest.mark.parametrize("when, sent", [("2026-11-13 09:59:59", False), ("2026-11-13 10:00", True),
                                        ("2026-11-13 15:30", True), ("2026-11-13 15:30:01", False),
                                        ("2026-11-27 12:30", True), ("2026-11-27 12:30:01", False)])
def test_b4_market_legs_go_only_from_30_minutes_after_the_open_to_30_minutes_before_the_close(tmp_path, when, sent):
    w = collar_world(tmp_path / "w")
    w.clock.t = pd.Timestamp(when, tz="America/New_York").timestamp()
    out = w.run(fractional=True)
    b = out["batch"]
    assert out["status"] == "done" and "faults" not in out, out
    assert w.venue.args[0]["tag"] == f"{b}-SPY"                       # the LIMIT leg's own window is today's
    if sent:
        assert [a["tag"] for a in w.venue.args] == [f"{b}-SPY", f"{b}-SPY.r"]
        return
    assert len(w.venue.args) == 1
    (left,) = held_back(out)
    assert left["collar"] == "clock" and "30 minutes" in left["reason"], left


def test_b4_a_market_leg_whose_window_closes_after_its_checks_is_not_sent(tmp_path):
    w = collar_world(tmp_path / "w")
    w.clock.t = pd.Timestamp("2026-11-13 15:29:40", tz="America/New_York").timestamp()
    out = w.run(patch=advance_on_market_intend(w, 30), fractional=True)   # 15:30:10 at the send
    b = out["batch"]
    assert out["status"] == "done" and [a["tag"] for a in w.venue.args] == [f"{b}-SPY"], out
    (left,) = held_back(out)
    assert left["collar"] == "clock"


# ---- B3: the move from the quote the leg was decided at (an approval: the ticket's)

@pytest.mark.parametrize("price, sent", [(502.5, True), (502.51, False), (497.5, True), (497.49, False)])
def test_b3_a_market_leg_whose_quote_moved_beyond_ticket_quote_move_is_held_back(tmp_path, price, sent):
    w = collar_world(tmp_path / "w")
    w.quote_age["SPY"] = 200.0                                        # the MARKET leg asks again and finds `price`
    ask_again_at(w, price)
    out = w.run(fractional=True)
    b = out["batch"]
    assert out["status"] == "done" and w.venue.args[0]["tag"] == f"{b}-SPY", out
    if sent:
        assert [a["tag"] for a in w.venue.args] == [f"{b}-SPY", f"{b}-SPY.r"]
        return
    assert len(w.venue.args) == 1
    (left,) = held_back(out)
    assert left["collar"] == "quote_move" and "0.5%" in left["reason"], left


@pytest.mark.parametrize("price, sent", [(502.5, True), (503.0, False)])
def test_b3_an_approval_bands_a_market_leg_on_the_tickets_quote_as_today(tmp_path, price, sent):
    w = leg_world(tmp_path / "w")
    w.paper = paper({"a": {"SPY": 0.5}})                              # a remainder leg alone: its own band
    p = propose(w, fractional=True)
    w.later(1)
    w.prices["SPY"] = price
    out = approve(w, p["ticket_number"], fractional=True)
    assert out["status"] == "done", out
    if sent:
        assert [e["leg"] for e in out["sent"]] == ["remainder"]
    else:
        assert out["sent"] == [] and [(e["leg"], e["reason"][:13]) for e in out["dropped"]] == [("remainder", "the fresh quo")]


# ---- B5: the collar price, stored with the order and charged to the purchase budget

@pytest.mark.parametrize("cash, sent", [(1273.74, False), (1275.26, True)])
def test_b5_the_purchase_budget_is_charged_r_times_the_collar_not_r_times_the_quote(tmp_path, cash, sent):
    w = collar_world(tmp_path / "w")
    w.venue.cash_value = cash                                         # budget 1261.00 / 1262.51: LIMIT 1010 + r x c 252.50
    out = w.run(fractional=True)
    b = out["batch"]
    assert out["status"] == "done", out
    if sent:
        assert [a["tag"] for a in w.venue.args] == [f"{b}-SPY", f"{b}-SPY.r"]
        assert {o["tag"]: o["collar_price"] for o in w.orders()}[f"{b}-SPY.r"] == 505.0
        return
    assert [a["tag"] for a in w.venue.args] == [f"{b}-SPY"]           # r x q = 250 fits; r x c = 252.50 does not
    (left,) = held_back(out)
    assert left["collar"] == "value" and "cash" in left["reason"], left


# ---- B6: the minimum, again at the quote the leg is sent on

@pytest.mark.parametrize("price, sent", [(10.06, True), (10.04, False)])
def test_b6_the_minimum_holds_at_the_moment_of_sending_after_the_quote_moved(tmp_path, price, sent):
    w = leg_world(tmp_path / "w")
    w.prices["SPY"] = 10.06                                           # 0.5 x 10.06 x 0.995 = 5.0049: a MARKET leg
    w.paper = paper({"a": {"SPY": 0.5}})
    w.quote_age["SPY"] = 200.0
    ask_again_at(w, price)                                            # 0.5 x 10.04 x 0.995 = 4.9949: under 5
    out = w.run(fractional=True)
    assert out["status"] == "done" and "faults" not in out, out
    if sent:
        assert [a["units"] for a in w.venue.args] == [0.5]
        return
    assert w.venue.args == []
    (left,) = held_back(out)
    assert left["collar"] == "minimum" and "minimum" in left["reason"], left


def test_b6_the_minimum_at_the_moment_of_sending_is_the_splits_own_rule():
    for units, price in ((0.5, 10.06), (0.5, 10.04), (0.37, 13.6), (0.99999, 5.03), (0.1, 50.25), (0.1, 50.26)):
        split = osx.split_difference(units, price, "buy", 0.0)
        assert osx.market_leg_clears_minimum(units, price) == any(leg.leg == "remainder" for leg in split.legs)
        assert osx.market_leg_clears_minimum(-units, price) == osx.market_leg_clears_minimum(units, price)


# ---- every collar check is a hold-back, never a fault, an attention item or a stop

def test_no_collar_hold_back_is_a_fault_an_attention_item_or_a_stop_over_seeded_worlds(tmp_path):
    named = {}
    for k in range(30):
        rng = random.Random(57_500 + k)
        w = leg_world(tmp_path / f"w{k}", fill=rng.choice([0.0, 1.0]))
        for run in range(2):
            w.paper = paper({"a": {sym: rng.choice([0.5, 2.5, 3.37, 1.25]) for sym in rng.sample(FUNDS, 3)}})
            w.quote_age = {sym: rng.choice([60.0, 60.0, 130.0]) for sym in FUNDS}
            w.second.verdicts = {sym: rng.choice(["agree", "agree", "disagree"]) for sym in FUNDS}
            w.clock.t += rng.choice([0, 0, 6 * 3600])                  # 10:00 or 16:00 (the session closed)
            out = w.run(fractional=True)
            if w.clock.t > osx.market_window(osx._default_calendar(), w.clock.t)[1]:
                assert out["status"] == "stopped" and out["stage"] == "session"
            else:
                assert out["status"] == "done" and "faults" not in out, out
                check_shape(w, out)
                check_gate(w, out)
                for e in out["left_over"]:
                    if "collar" in e:
                        named[e["collar"]] = named.get(e["collar"], 0) + 1
            led = w.ledger()
            try:
                assert led.attention() == []
            finally:
                led.close()
            w.next_day()
    assert named.get("quote_age", 0) > 5 and named.get("second_source", 0) > 5, named


# ---- R6: a MARKET leg's average judged against its collar

def test_r6_a_market_legs_average_is_judged_against_its_collar_by_todays_limit_rules():
    v = osx._Run._limit_verdict
    buy = {"units": 0.5, "order_type": "MARKET", "limit_price": None, "collar_price": 505.0}
    sell = {"units": -0.5, "order_type": "MARKET", "limit_price": None, "collar_price": 490.0}
    assert [v(buy, a) for a in (500.0, 505.0, 505.0 * (1 + osx.LIMIT_SAME), 505.01, 600.0)] == \
        ["ok", "ok", "ok", "breach", "breach"]
    assert [v(sell, a) for a in (495.0, 490.0, 490.0 * (1 - osx.LIMIT_SAME), 489.99, 400.0)] == \
        ["ok", "ok", "ok", "breach", "breach"]
    assert v(buy, None) == "unjudged" and v({**buy, "collar_price": None}, 500.0) == "unjudged"
    limit = {"units": 2.0, "order_type": "LIMIT", "limit_price": 505.0, "collar_price": None}
    assert [v(limit, a) for a in (505.0, 505.01)] == ["ok", "breach"]          # a LIMIT leg exactly as today


class FillVenue(LegVenue):
    """Fills each order at prices of its own: honest between its quote and its bound (the limit of a LIMIT leg, the
    collar of a MARKET leg), or, for a MARKET leg drawn to breach, at least a cent beyond the collar; at once, in
    pieces between runs, racing a cancel and late after a cancel; it reports averages exact, to four decimals or to
    the cent. `pieces` keeps every piece of a breaching order with how it came."""

    def __init__(self, rng, rounding, beyond=0.0, **kw):
        super().__init__(**kw)
        self.prng, self.rounding, self.beyond = rng, rounding, beyond
        self.meta, self.pieces, self.how = {}, [], "at once"

    def submit(self, symbol, units, tag, price=None, limit_price=None, session="CORE"):
        market = limit_price is None
        bound = osx.limit_price(units, price, units < 0) if market else limit_price
        self.meta[tag] = {"quote": price, "bound": bound, "market": market,
                          "breach": market and self.prng.random() < self.beyond}
        self.fill_at_once = self.prng.choice([0.0, 0.0, 0.3, 1.0])
        self.how = "at once"
        return super().submit(symbol, units, tag, price, limit_price, session)

    def _price(self, tag):
        m = self.meta[tag]
        q, bound = m["quote"], m["bound"]
        if m["breach"]:
            side = 1.0 if bound >= q else -1.0
            return bound + side * max(0.01, bound * self.prng.choice([1e-5, 1e-3, 0.02]))
        return q + self.prng.random() * (bound - q)

    def fill(self, tag, amount=None):
        done = super().fill(tag, amount)
        if done:
            o = self.book[tag]
            o["value"] = o.get("value", 0.0) + done * self._price(tag)
            if self.meta[tag]["breach"]:
                self.pieces.append((tag, self.how))
        return done

    def order_status(self, tag):
        s = super().order_status(tag)
        o = self.book.get(tag)
        if s is not None and o["filled"] > 0:
            avg = o["value"] / o["filled"]
            if self.rounding:
                avg = float(Decimal(repr(avg)).quantize(Decimal(self.rounding), rounding=ROUND_HALF_UP))
            s["avg_price"] = avg
        return s

    def cancel(self, tag):
        o = self.book.get(tag)
        if o is not None and o["state"] in ("open", "partly_filled") and self.prng.random() < 0.3:
            self.how = "racing a cancel"
            self.fill(tag, abs(o["units"]) * 0.5)
        return super().cancel(tag)

    def market(self, how):
        for tag, o in self.book.items():
            self.how = how
            if o["state"] in ("open", "partly_filled") and self.prng.random() < 0.5:
                self.fill(tag, abs(o["units"]) * self.prng.choice([0.25, 0.5, 1.0]))
            elif o["state"] == "cancelled" and 0 < o["filled"] < abs(o["units"]) - 1e-9 and self.prng.random() < 0.5:
                self.how = "after a cancel"
                o["state"] = "partly_filled"                          # a larger amount reported after the cancel
                self.fill(tag, abs(o["units"]) * 0.25)
                o["state"] = "cancelled"


def _fill_world(tmp, rng, beyond):
    v = FillVenue(rng, rng.choice([None, "0.0001", "0.01"]), beyond, held={"SPY": float(rng.choice([0, 0, 3]))})
    w = ticket_world(tmp, v)
    w.limits.write_text(json.dumps({**GENEROUS, "max_orders_per_day": 40}))
    outs = []
    for day in range(3):
        for run in range(rng.randint(1, 3)):
            if rng.random() < 0.8 or not outs:
                w.paper = paper({s: {sym: rng.choice([0.0, 0.5, 1.37, 2.5, 3.99999, 7.3, 0.2])
                                     for sym in rng.sample(FUNDS, rng.randint(1, 4))} for s in ("a", "b")})
            for sym in FUNDS:
                w.prices[sym] = rng.choice([20.0, 55.5, 101.25, 500.0])
            outs.append(w.run(fractional=True))
            w.venue.market("late the same day")
            w.later(rng.choice([5, 30, 90]))
        w.venue.market("the next day")
        w.next_day()
    outs.append(w.run(fractional=True))                              # the last reads: nothing filled after it
    return w, outs


def _fill_lines(w):
    return [e for e in w.entries("fill")]


def test_r6_question_one_no_ordinary_day_writes_a_fault_over_seeded_worlds(tmp_path):
    """Fills anywhere between the quote and the collar (or the limit), in pieces, late, racing a cancel, after a
    cancel; averages exact, to four decimals or to the cent: no run writes a fault, every MARKET fill is judged."""
    market_ok = runs = pieces = 0
    roundings = set()
    for k in range(40):
        rng = random.Random(58_000 + k)
        w, outs = _fill_world(tmp_path / f"w{k}", rng, beyond=0.0)
        roundings.add(w.venue.rounding)
        for out in outs:
            assert out["status"] == "done", (k, out)
            assert "faults" not in out and "cap_unjudged" not in out, (k, out)
            runs += 1
        lines = [e for e in _fill_lines(w) if e.get("order_type") == "MARKET"]
        assert all(e["verdict"] == "ok" for e in lines), (k, lines)
        market_ok += len(lines)
        pieces += sum(1 for o in w.venue.book.values() if o["filled"] > 0)
    assert runs > 150 and market_ok > 150 and pieces > 300 and roundings == {None, "0.0001", "0.01"}, \
        (runs, market_ok, pieces, roundings)


def test_r6_question_two_no_fill_beyond_the_collar_is_lost_over_seeded_worlds(tmp_path):
    """MARKET legs drawn to fill at least a cent beyond their collar, at once, late the same day, the next day,
    racing a cancel and after a cancel: every such fill row of the ledger is journaled as a breach and named in the
    cap_refs of a summary whose faults say `cap`."""
    breaches, how = 0, {}
    for k in range(40):
        rng = random.Random(59_000 + k)
        w, outs = _fill_world(tmp_path / f"w{k}", rng, beyond=0.5)
        bad = {t for t, m in w.venue.meta.items() if m["breach"]}
        rows = [f for f in w.fills() if f["tag"] in bad and f["units"] != 0]
        refs = set()
        for out in outs:
            if out.get("cap_refs"):
                assert "cap" in out["faults"], out
                refs |= set(out["cap_refs"])
        lines = {e["fill_id"]: e for e in _fill_lines(w)}
        for f in rows:
            assert lines[f["id"]]["verdict"] == "breach", (k, f, lines[f["id"]])
            assert f"fill-{f['id']}" in refs, (k, f)
        breaches += len(rows)
        for _, h in w.venue.pieces:
            how[h] = how.get(h, 0) + 1
        for out in outs:                                               # nothing else on those days is a fault
            assert set(out.get("faults", [])) <= {"cap"}, out
    assert breaches > 100 and set(how) == {"at once", "late the same day", "the next day", "racing a cancel",
                                           "after a cancel"} and min(how.values()) > 3, (breaches, how)


# ---- R8: the fence, with a baseline, the owner's own trades by hand and cash moved in and out

def test_r8_no_order_of_either_leg_sells_more_than_the_ledgers_own_book_over_seeded_worlds(tmp_path):
    """The owner's baseline is never the ledger's; his trades by hand after it make the positions differ, which stops
    the runs until he reverses them (the order ledger's own rule: a reconcile that is not clean blocks); cash moved in
    and out changes no position. In every run, done or not, no order of either leg sells more of a fund than the
    ledger's own book of it at the sends."""
    sales = above = stopped = resumed = 0
    for k in range(40):
        rng = random.Random(60_000 + k)
        baseline = {"SPY": float(rng.choice([0, 3, 7.5])), "QQQ": float(rng.choice([0, 2.25]))}
        w = leg_world(tmp_path / f"w{k}", held={s: u for s, u in baseline.items() if u}, fill=rng.choice([0.5, 1.0]))
        fractional = rng.random() < 0.8
        by_hand = {}                                                  # the owner's trades not yet reversed
        for day in range(4):
            for run in range(rng.randint(1, 3)):
                if by_hand and rng.random() < 0.6:                    # he reverses them
                    for sym, u in by_hand.items():
                        w.venue.held[sym] = w.venue.held.get(sym, 0.0) - u
                    by_hand, resumed = {}, resumed + 1
                elif rng.random() < 0.25:                             # he buys or sells by hand
                    sym = rng.choice(FUNDS)
                    u = rng.choice([-2.0, -0.5, 1.0, 2.25])
                    u = max(u, -w.venue.held.get(sym, 0.0))
                    w.venue.held[sym] = w.venue.held.get(sym, 0.0) + u
                    by_hand[sym] = by_hand.get(sym, 0.0) + u
                w.venue.cash_value = max(0.0, w.venue.cash_value + rng.choice([0.0, -5_000.0, 10_000.0]))
                w.paper = paper({s: {sym: rng.choice([0.0, 0.0, 0.0, 0.5, 1.37, 2.5, 4.0])
                                     for sym in rng.sample(FUNDS, rng.randint(1, 4))} for s in ("a", "b")})
                for sym in FUNDS:
                    w.prices[sym] = rng.choice([20.0, 55.5, 101.25, 500.0])
                before = dict(w.venue.held)                          # the venue's holdings while the run sends
                out = w.run(fractional=fractional)
                stopped += out["status"] == "stopped"
                book = book_without(w, out["batch"])                  # the ledger's own book at the sends
                check_fence(w, out, book)
                for o in batch_orders(w, out["batch"]):
                    if o["units"] < 0:
                        sales += 1
                        above += before.get(o["symbol"], 0.0) > book.get(o["symbol"], 0.0) + 1e-9
                w.later(30)
            w.next_day()
    assert sales > 80 and above > 20 and stopped > 10 and resumed > 10, (sales, above, stopped, resumed)


# ---- point a: the remainder goes only after an ACCEPTED LIMIT leg, by that leg's state in the ledger

class ModeVenue(LegVenue):
    """Answers each submit the way its `modes` say: accept; accept and fill part or all at once (then read by a
    concurrent reconcile, so the ledger holds partly_filled or filled right after the submit); refuse; reject; raise;
    lose the answer."""

    def __init__(self, modes, **kw):
        super().__init__(**kw)
        self.modes, self.last = list(modes), None

    def submit(self, symbol, units, tag, price=None, limit_price=None, session="CORE"):
        mode = self.modes.pop(0) if self.modes else "accept"
        self.last = mode
        self.fill_at_once = {"fill_part": 0.5, "fill_all": 1.0}.get(mode, 0.0)
        self.submit_mode = mode if mode in ("refuse", "reject", "raise", "lose") else "accept"
        return super().submit(symbol, units, tag, price, limit_price, session)


def read_after_submit(w, states, in_flight=()):
    """A ledger patch: right after each submit, an order filled at once is read by a concurrent reconcile; the
    ledger's state of each order right after its submit is kept in `states`. A tag in `in_flight` is taken by
    another process first, which dies with the order sent and no answer."""
    def patch(led):
        real = led.submit_once

        def submit_once(venue, tag, *a, **k):
            if tag.split("-", 4)[-1] in in_flight:
                other = osx.open_ledger(w.ledger_path, clock=w.clock)

                class Dies:
                    name = w.venue.name

                    def submit(self, *a, **k):
                        raise Crash()
                try:
                    with pytest.raises(Crash):
                        other.submit_once(Dies(), tag)
                finally:
                    other.close()
            rec = real(venue, tag, *a, **k)
            if w.venue.last in ("fill_part", "fill_all") and rec["state"] == "accepted":
                led.reconcile(w.venue, osx.broker_book(led.fills()))
                rec = led.order(tag)
            states[tag] = rec["state"]
            return rec
        led.submit_once = submit_once
    return patch


@pytest.mark.parametrize("mode, goes, words", [
    ("accept", True, None), ("fill_part", True, None), ("fill_all", True, None),
    ("refuse", False, "refused by the venue"), ("reject", False, "refused by the venue"),
    ("raise", False, "unknown"), ("lose", False, "unknown"), ("in flight", False, "sent"),
])
def test_point_a_the_limit_legs_state_in_the_ledger_right_after_its_submit_decides_the_remainder(
        tmp_path, mode, goes, words):
    v = ModeVenue([mode if mode != "in flight" else "accept"])
    w = ticket_world(tmp_path / "w", v)
    w.limits.write_text(json.dumps(GENEROUS))
    w.paper = paper({"a": {"SPY": 2.5}})
    states = {}
    out = w.run(patch=read_after_submit(w, states, ("SPY",) if mode == "in flight" else ()), fractional=True)
    b = out["batch"]
    tags = [a["tag"] for a in v.args]
    if goes:
        assert out["status"] == "done" and tags == [f"{b}-SPY", f"{b}-SPY.r"], out
        assert states[f"{b}-SPY"] == {"accept": "accepted", "fill_part": "partly_filled", "fill_all": "filled"}[mode]
        return
    assert f"{b}-SPY.r" not in tags
    (left,) = [e for e in w.entries("left_over") if e["symbol"] == "SPY" and e["leg"] == "remainder"]
    assert "LIMIT leg" in left["reason"] and words in left["reason"], left
    if mode in ("raise", "lose", "in flight"):
        assert out["status"] == "stopped" and out["stage"] == "send", out
    else:
        assert out["status"] == "done", out


def test_point_a_a_limit_leg_left_over_dropped_refused_by_the_gate_or_cut_by_the_count_holds_its_remainder(tmp_path):
    # left over: its quote went stale before it was sent
    w = collar_world(tmp_path / "left")

    def stale_limit(led):
        real = led.intend

        def intend(tag, *a, **k):
            rec = real(tag, *a, **k)
            if k.get("order_type", "LIMIT") == "LIMIT":
                w.clock.t += osx.QUOTE_MAX_AGE_SECONDS
            return rec
        led.intend = intend
    out = w.run(patch=stale_limit, fractional=True)
    (left,) = held_back(out)
    assert "LIMIT leg of this run was left over (the quote went stale" in left["reason"], left
    # refused by the risk gate
    w = collar_world(tmp_path / "gate")
    w.latch.write_text("{}")                                          # the drawdown latch: no new risk
    out = w.run(fractional=True)
    (left,) = held_back(out)
    assert "LIMIT leg of this run was refused by the risk gate" in left["reason"], left
    # cut by the count
    w = collar_world(tmp_path / "cap", cap=1)
    w.paper = paper({"a": {"SPY": 2.5, "QQQ": 3.5}})
    out = w.run(fractional=True)
    (left,) = held_back(out)
    assert "LIMIT leg of this run was left over (the day's cap of 1 orders is reached)" in left["reason"], left
    # dropped: the approval's whole line no longer the size the fresh targets want
    w = collar_world(tmp_path / "drop")
    p = propose(w, fractional=True)
    w.paper = paper({"a": {"SPY": 3.5}})
    w.later(1)
    out = approve(w, p["ticket_number"], fractional=True)
    assert out["status"] == "done" and w.venue.args == [], out
    (left,) = held_back(out)
    assert "LIMIT leg of this run was dropped (the wanted size changed" in left["reason"], left


def test_point_a_over_seeded_worlds_a_remainder_is_sent_only_after_its_limit_leg_was_accepted_partly_or_wholly_filled(
        tmp_path):
    went, held = {}, {}
    modes = ["accept", "fill_part", "fill_all", "refuse", "reject", "raise", "lose"]
    for k in range(80):
        rng = random.Random(61_000 + k)
        v = ModeVenue([rng.choice(modes) for _ in range(12)])
        w = ticket_world(tmp_path / f"w{k}", v)
        w.limits.write_text(json.dumps(GENEROUS))
        w.paper = paper({"a": {sym: rng.choice([2.5, 1.37, 3.25, 1.5]) for sym in rng.sample(FUNDS, 3)}})
        states = {}
        out = w.run(patch=read_after_submit(w, states), fractional=True)
        b = out["batch"]
        sent = [a["tag"] for a in v.args]
        lines = {e["symbol"]: e for e in w.entries("left_over") if e.get("leg") == "remainder"}
        for tag in sent:
            if tag.endswith(".r"):
                whole = tag[:-2]
                assert whole in sent[:sent.index(tag)] and states[whole] in ("accepted", "partly_filled", "filled")
                went[states[whole]] = went.get(states[whole], 0) + 1
        for tag, state in states.items():
            sym = tag[len(b) + 1:]
            if tag.endswith(".r") or state in ("accepted", "partly_filled", "filled"):
                continue
            assert f"{tag}.r" not in sent
            assert sym in lines and "LIMIT leg" in lines[sym]["reason"], (k, tag, state, lines)
            held[state] = held.get(state, 0) + 1
        assert out["status"] == "done" or out["stage"] == "send", out
    assert set(went) == {"accepted", "partly_filled", "filled"} and set(held) == {"refused", "unknown"}, (went, held)
    assert min(went.values()) > 5 and min(held.values()) > 5, (went, held)


# ---- point b: no two orders can share a tag

def test_point_b_a_fund_whose_tag_equals_another_funds_remainder_tag_refuses_both_before_anything_is_sent(tmp_path):
    w = leg_world(tmp_path / "w")
    w.prices.update({"AB": 50.0, "AB.r": 60.0})
    w.paper = paper({"a": {"AB": 2.5, "AB.r": 3.0, "SPY": 2.5}})
    out = w.run(fractional=True)
    b = out["batch"]
    assert out["status"] == "done", out
    assert sorted(a["tag"] for a in w.venue.args) == [f"{b}-SPY", f"{b}-SPY.r"]
    refused = {e["symbol"]: e["reason"] for e in out["refused"]}
    assert set(refused) == {"AB", "AB.r"} and all("remainder tag" in r for r in refused.values()), refused
    assert {o["symbol"] for o in w.orders()} == {"SPY"}
    out = w.run()                                                      # whole shares only: no tag ends in .r
    assert {o["symbol"] for o in batch_orders(w, out["batch"])} == {"AB", "AB.r", "SPY"}


def test_point_b_over_seeded_symbol_sets_no_two_orders_share_a_tag_and_only_clashing_funds_are_refused(tmp_path):
    pool = ["AB", "AB.r", "AB.r.r", "CD", "CD.R", "EF.r", "GH", "GH.rr", "IJ.r", "IJ"]
    clashes = runs = 0
    for k in range(40):
        rng = random.Random(62_000 + k)
        w = leg_world(tmp_path / f"w{k}", fill=rng.choice([0.0, 1.0]))
        syms = rng.sample(pool, rng.randint(2, 6))
        w.prices.update({s: rng.choice([20.0, 55.5, 101.25]) for s in pool})
        w.paper = paper({"a": {s: rng.choice([2.5, 1.37, 3.0, 0.5]) for s in syms}})
        out = w.run(fractional=True)
        assert out["status"] == "done", out
        names = set(syms)
        clash = {s for s in names if s.endswith(".r") and s[:-2] in names}
        clash |= {s[:-2] for s in clash}
        refused = {e["symbol"] for e in out["refused"] if "remainder tag" in e["reason"]}
        assert refused == clash, (syms, refused, clash)
        tags = [a["tag"] for a in w.venue.args]
        assert len(tags) == len(set(tags)) and not {o["symbol"] for o in w.orders()} & clash
        assert {o["symbol"] for o in w.orders()} == {s for s in names - clash}, (syms, w.orders())
        clashes += bool(clash)
        runs += 1
    assert clashes > 10 and runs - clashes > 5, (clashes, runs)


# ---- point d: an approval runs in the ticket's own mode

@pytest.mark.parametrize("made, asked", [(True, False), (False, True)])
def test_point_d_an_approval_whose_fractional_differs_from_its_tickets_is_refused_in_words_and_sends_nothing(
        tmp_path, made, asked):
    w = collar_world(tmp_path / "w")
    p = propose(w, fractional=made)
    assert p["ticket"]["orders"], p
    w.later(1)
    out = approve(w, p["ticket_number"], fractional=asked, second_opinion=None)
    assert out["status"] == "stopped" and out["stage"] == "ticket", out
    assert out["reason"].startswith("that ticket was made " + ("with" if made else "without") + " fractional units")
    assert w.venue.args == [] and w.orders() == []
    assert ticket_rows(w, "verdicts")[-1]["verdict"] == "refused"
