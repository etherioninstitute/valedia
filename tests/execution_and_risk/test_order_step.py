"""The order step (T13): from the paper book's decisions to orders at the broker, test environment only.

Every test uses a stand-in venue, a ledger and a state file in a temporary folder, a fake clock and fake quotes.
Nothing here builds a Webull client or touches the network."""
import dataclasses
import json
import math
import random
from datetime import datetime

import exchange_calendars
import pandas as pd
import pytest

from valedia_trader.execution_and_risk import order_step as osx
from valedia_trader.execution_and_risk.orders import OrderLedger
from valedia_trader.research_loop.paper import Journal
from valedia_trader.venues.webull import NotTransmitted, OrderRejected

CAL = exchange_calendars.get_calendar("XNYS")
GENEROUS = {"max_daily_loss_pct": 99.0, "max_drawdown_pct": 99.0, "max_position_pct": 1.0, "max_open_positions": 50}


def ny(text):
    return pd.Timestamp(text, tz="America/New_York").timestamp()


class Crash(BaseException):
    """Stands for the process dying at that point."""


class Clock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t


class StandIn:
    """A stand-in for the Webull adapter in its test environment: the public names the order step uses, no network.
    `held` is the account's truth; `book` holds every order the venue has, by tag; `calls` lists every call made."""
    environment = "sandbox"

    def __init__(self, held=None, cash=1_000_000.0, name="standin-sandbox"):
        self.name = name
        self.held = {k: float(v) for k, v in (held or {}).items()}
        self.cash_value = cash
        self.book = {}
        self.calls = []
        self.submit_mode = "accept"                  # or a list, one mode per submit
        self.cancel_answer = "cancel requested"
        self.fill_at_once = 0.0                      # share of each accepted order filled at once
        self.raise_on = set()

    def _maybe_raise(self, method):
        if method in self.raise_on:
            raise ConnectionError(f"{method} is down")

    def _take(self, symbol, units, tag, limit_price):
        self.book[tag] = {"symbol": symbol, "units": float(units), "limit": limit_price, "filled": 0.0, "state": "open"}
        if self.fill_at_once:
            self.fill(tag, self.fill_at_once * abs(units))

    def submit(self, symbol, units, tag, price=None, limit_price=None, session="CORE"):
        self.calls.append(("submit", tag))
        mode = self.submit_mode.pop(0) if isinstance(self.submit_mode, list) and self.submit_mode else (
            "accept" if isinstance(self.submit_mode, list) else self.submit_mode)
        if mode == "accept":
            self._take(symbol, units, tag, limit_price)
            return {"status": "accepted", "venue_order_id": "V" + tag[-6:]}
        if mode == "refuse":
            raise NotTransmitted("the order is larger than the holding allows")
        if mode == "reject":
            raise OrderRejected("Webull refused the order and no order exists under its client order id", "SOME_CODE")
        if mode == "raise":
            raise TimeoutError("read timed out")
        if mode == "lose":
            self._take(symbol, units, tag, limit_price)
            raise ConnectionError("connection reset after the request was sent")
        raise AssertionError(mode)

    def fill(self, tag, amount=None):
        o = self.book[tag]
        left = abs(o["units"]) - o["filled"]
        amount = left if amount is None else min(float(amount), left)
        if amount <= 0 or o["state"] not in ("open", "partly_filled"):
            return 0.0
        sign = 1.0 if o["units"] > 0 else -1.0
        o["filled"] += amount
        self.held[o["symbol"]] = self.held.get(o["symbol"], 0.0) + sign * amount
        self.cash_value -= sign * amount * o["limit"]
        o["state"] = "filled" if abs(o["units"]) - o["filled"] <= 1e-12 else "partly_filled"
        return amount

    def order_status(self, tag):
        self.calls.append(("order_status", tag))
        self._maybe_raise("order_status")
        o = self.book.get(tag)
        if o is None:
            return None
        return {"state": o["state"], "filled_units": o["filled"], "avg_price": o["limit"] if o["filled"] else None,
                "fee": 0.0, "venue_order_id": "V" + tag[-6:], "raw_status": None}

    def positions(self):
        self.calls.append(("positions", None))
        self._maybe_raise("positions")
        return {s: u for s, u in self.held.items() if abs(u) > 1e-12}

    def cash(self):
        self.calls.append(("cash", None))
        self._maybe_raise("cash")
        return {"cash": self.cash_value, "settled_cash": None, "buying_power": None, "currency": "USD"}

    def cancel(self, tag):
        self.calls.append(("cancel", tag))
        self._maybe_raise("cancel")
        o = self.book.get(tag)
        if o is None:
            return {"status": "no such order"}
        if self.cancel_answer == "cancel requested" and o["state"] in ("open", "partly_filled"):
            o["state"] = "cancelled"
        elif self.cancel_answer == "cancel requested":
            return {"status": "already finished"}
        return {"status": self.cancel_answer}

    def names(self):
        return [c[0] for c in self.calls]

    def submits(self):
        return [t for m, t in self.calls if m == "submit"]


AUTO = "auto"                                     # the paper book's day: the session before the run's day


def paper(wanted, equity=100_000.0, share=None, min_trade_pct=0.0, day=AUTO):
    return osx.PaperTargets(wanted=wanted, equity=equity, share=equity if share is None else share, min_trade_pct=min_trade_pct,
                            day=day)


def ny_day(t):
    return pd.Timestamp(t, unit="s", tz="UTC").tz_convert("America/New_York").normalize().tz_localize(None)


class World:
    """The files, clock, venue and quotes one test drives the order step with."""

    def __init__(self, tmp):
        self.tmp = tmp
        tmp.mkdir(parents=True, exist_ok=True)
        self.clock = Clock(ny("2026-11-13 10:00"))
        self.venue = StandIn()
        self.prices = {"SPY": 500.0, "QQQ": 400.0, "IWM": 200.0, "TLT": 100.0, "GLD": 250.0, "OWN": 50.0}
        self.quote_age = {}
        self.quote_override = {}
        self.quote_calls = []
        self.limits = tmp / "risk.json"
        self.limits.write_text(json.dumps(GENEROUS))
        self.journal = Journal(tmp / "journal")
        self.ledger_path = tmp / "memory" / "orders.sqlite"
        self.state_path = osx.state_path_for(self.ledger_path, self.venue.name)
        self.latch = tmp / "kill_switch.json"
        self.halt = None
        self.paper = paper({})

    def quotes(self, symbols):
        self.quote_calls.append(list(symbols))
        self.venue.calls.append(("quotes", tuple(symbols)))
        out = {s: (self.prices[s], self.clock.t - self.quote_age.get(s, 60.0)) for s in symbols if s in self.prices}
        out.update({s: q for s, q in self.quote_override.items() if s in symbols})
        return out

    def ledger(self):
        return osx.open_ledger(self.ledger_path, clock=self.clock)

    def run(self, patch=None, ledger=None, **kw):
        led = ledger or self.ledger()
        if patch:
            patch(led)
        book = self.paper
        if isinstance(book, osx.PaperTargets) and getattr(book, "day", None) == AUTO:
            prev = CAL.sessions[CAL.sessions.searchsorted(ny_day(self.clock.t)) - 1] if math.isfinite(self.clock.t) else None
            book = dataclasses.replace(book, day=None if prev is None else str(prev.date()))
        try:
            return osx.run_order_step(ledger=led, venue=self.venue, paper=book, quotes=self.quotes, journal=self.journal,
                                      limits_file=self.limits, state=self.state_path, clock=self.clock, calendar=CAL,
                                      halt_file=self.halt, latch_file=self.latch, **kw)
        finally:
            led.close()

    def later(self, minutes=30):
        self.clock.t += minutes * 60

    def next_day(self, at="10:00"):
        day = pd.Timestamp(self.clock.t, unit="s", tz="UTC").tz_convert("America/New_York").normalize().tz_localize(None)
        nxt = CAL.sessions[CAL.sessions.searchsorted(day, side="right")]
        self.clock.t = ny(f"{nxt.date()} {at}")

    def entries(self, event=None):
        return [e for e in self.journal.read() if e.get("type") == "order_step" and (event is None or e["event"] == event)]

    def orders(self):
        led = self.ledger()
        try:
            return led.orders()
        finally:
            led.close()

    def fills(self):
        led = self.ledger()
        try:
            return led.fills()
        finally:
            led.close()

    def book(self):
        return osx.broker_book(self.fills())

    def baseline(self):
        st = osx.OrderStepState(self.state_path)
        try:
            return st.baseline()
        finally:
            st.close()


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def units_sent(world):
    return {o["symbol"]: o["units"] for o in world.orders() if o["state"] not in ("refused", "intended")}


# ---- outcome 1: the steps in order; a failure in steps 1 to 6 sends nothing and says why

def test_a_first_run_goes_through_the_steps_in_order(world):
    world.venue.held = {"OWN": 3.0}
    world.paper = paper({"a": {"SPY": 10.0}})
    out = world.run()
    assert out["status"] == "done" and out["orders_sent"] == 1
    assert world.venue.names() == ["positions",                  # 3 the baseline
                                   "positions",                  # 4 reconcile (nothing open yet, so nothing to cancel)
                                   "positions",                  # 4 reconcile again
                                   "quotes",                     # 6 prices
                                   "cash",                       # 8 the broker account
                                   "submit",                     # 10
                                   "order_status", "positions"]  # 11 reconcile once more
    assert world.quote_calls == [["OWN", "SPY"]]
    assert [e["event"] for e in world.entries()] == ["baseline", "order", "summary"]


def test_a_later_run_reconciles_cancels_reconciles_then_prices_and_sends(world):
    world.paper = paper({"a": {"SPY": 10.0}})
    world.run()
    first = world.venue.submits()[0]
    world.venue.calls.clear()
    world.later()
    out = world.run()
    names = world.venue.names()
    assert names[:names.index("quotes")] == ["order_status", "positions", "cancel", "order_status", "positions"]
    assert names[names.index("quotes"):] == ["quotes", "cash", "submit", "order_status", "positions"]
    assert world.venue.calls[2] == ("cancel", first) and out["orders_sent"] == 1


def _stop_at(world, stage):
    world.venue.calls.clear()
    out = world.run()
    assert out["status"] == "stopped" and out["stage"] == stage and out["orders_sent"] == 0
    assert "submit" not in world.venue.names()
    stops = world.entries("stopped")
    assert stops and stops[-1]["stage"] == stage and len(stops[-1]["reason"]) > 10
    assert world.entries("summary")[-1]["status"] == "stopped"
    return out


@pytest.mark.parametrize("how, stage", [
    ("production", "environment"), ("saturday", "session"), ("positions down", "baseline"), ("paper raises", "targets"),
    ("paper equity zero", "targets"), ("capital not finite", "targets"), ("quotes raise", "prices"), ("cash down", "prices"),
    ("bad clock", "clock")])
def test_any_failure_in_the_first_steps_sends_nothing_and_journals_why(world, how, stage):
    world.paper = paper({"a": {"SPY": 10.0}})
    kw = {}
    if how == "production":
        world.venue.environment = "production"
    elif how == "saturday":
        world.clock.t = ny("2026-11-14 11:00")
    elif how == "positions down":
        world.venue.raise_on.add("positions")
    elif how == "paper raises":
        world.paper = lambda: (_ for _ in ()).throw(RuntimeError("paper_state.json is damaged"))
    elif how == "paper equity zero":
        world.paper = paper({"a": {"SPY": 10.0}}, equity=0.0)
    elif how == "capital not finite":
        kw["capital"] = float("nan")
    elif how == "quotes raise":
        world.quotes = lambda symbols: (_ for _ in ()).throw(TimeoutError("price source down"))
    elif how == "cash down":
        world.venue.raise_on.add("cash")
    elif how == "bad clock":
        world.clock.t = float("nan")
    out = world.run(**kw)
    assert out["status"] == "stopped" and out["stage"] == stage and out["orders_sent"] == 0
    assert "submit" not in world.venue.names() and not [o for o in world.orders() if o["state"] != "intended"]
    stops = world.entries("stopped")
    if stage in ("environment", "clock"):                          # before the lock: returned only, nothing written (rev 4)
        assert stops == [] and len(out["reason"]) > 10
    else:
        assert stops and stops[-1]["stage"] == stage and len(stops[-1]["reason"]) > 10


def test_an_unclean_reconcile_sends_nothing(world):
    world.paper = paper({"a": {"SPY": 10.0}})
    world.venue.fill_at_once = 1.0
    world.run()
    world.venue.held["SPY"] += 5                                  # someone bought five by hand in the account
    world.paper = paper({"a": {"SPY": 20.0}})
    world.venue.calls.clear()
    out = _stop_at(world, "reconcile")
    assert "differ" in out["reason"] and "SPY" in out["reason"]
    world.later()
    _stop_at(world, "reconcile")                                  # and it stays that way until a person has looked


# ---- outcome 2: production refused before any venue call

def test_production_is_refused_with_no_venue_call_and_the_only_environment_is_a_named_constant(world):
    assert osx.TEST_ENVIRONMENT == "sandbox"
    world.paper = paper({"a": {"SPY": 10.0}})
    for env in ("production", None, "SANDBOX", "paper"):
        world.venue.environment = env
        out = world.run()
        assert out["stage"] == "environment" and world.venue.calls == [] and world.quote_calls == []

    class NoEnvironment(StandIn):
        environment = property(lambda self: (_ for _ in ()).throw(AttributeError("no environment")))
    world.venue = NoEnvironment()
    assert world.run()["stage"] == "environment" and world.venue.calls == []


def test_a_ledger_that_does_not_treat_the_adapters_refusals_as_not_sent_is_refused_before_any_venue_call(world):
    world.paper = paper({"a": {"SPY": 10.0}})
    for not_sent in ((), (NotTransmitted,), (OrderRejected,), (Exception,), (NotTransmitted, OrderRejected, ConnectionError),
                     (NotTransmitted, OrderRejected, BaseException)):   # broader would turn an unknown outcome into "not sent"
        led = OrderLedger(world.ledger_path, not_sent=not_sent, clock=world.clock)
        out = world.run(ledger=led)
        assert out["stage"] == "environment" and world.venue.calls == []
    assert world.run()["status"] == "done"                        # open_ledger gives the right one


# ---- outcome 3: outside the regular session nothing is read and nothing is sent

@pytest.mark.parametrize("when", ["2026-11-14 11:00", "2026-11-15 11:00", "2026-11-26 11:00", "2026-11-13 09:34:59",
                                  "2026-11-13 08:00", "2026-11-13 15:45:01", "2026-11-13 20:00", "2026-11-27 12:45:01",
                                  "2026-12-24 14:00", "2026-12-25 11:00"])
def test_outside_the_regular_session_nothing_is_read_or_sent(world, when):
    world.paper = paper({"a": {"SPY": 10.0}})
    world.clock.t = ny(when)
    out = world.run()
    assert out["stage"] == "session" and world.venue.calls == [] and world.quote_calls == []
    assert world.entries("stopped")[-1]["day"] == when[:10]


@pytest.mark.parametrize("when", ["2026-11-13 09:35", "2026-11-13 15:45", "2026-11-27 12:45", "2026-11-27 09:35"])
def test_the_window_edges_and_a_half_day_inside_its_shorter_window_are_open(world, when):
    world.paper = paper({"a": {"SPY": 10.0}})
    world.clock.t = ny(when)
    assert world.run()["orders_sent"] == 1


def test_the_window_comes_from_the_calendar():
    assert osx.market_window(CAL, ny("2026-11-13 12:00"))[:2] == (ny("2026-11-13 09:35"), ny("2026-11-13 15:45"))
    assert osx.market_window(CAL, ny("2026-11-27 12:00"))[:2] == (ny("2026-11-27 09:35"), ny("2026-11-27 12:45"))
    start, end, why = osx.market_window(CAL, ny("2026-11-26 12:00"))
    assert start is None and end is None and "not a trading day" in why


def test_a_run_that_outlasts_the_window_leaves_the_rest_over(world):
    world.paper = paper({"a": {"SPY": 10.0, "QQQ": 10.0}})
    world.clock.t = ny("2026-11-13 15:44:59")
    original = world.venue.submit

    def slow_submit(*a, **k):
        world.clock.t += 5                                        # the venue takes five seconds
        return original(*a, **k)
    world.venue.submit = slow_submit
    out = world.run()
    assert out["orders_sent"] == 1 and out["left_over"][0]["reason"] == "the session window closed during the run"


# ---- outcome 4: the broker book, the baseline

def test_the_baseline_is_recorded_once_never_sold_and_never_counted_as_owned(world):
    world.venue.held = {"SPY": 7.0, "OWN": 2.0}
    world.venue.fill_at_once = 1.0
    world.paper = paper({"a": {"SPY": 10.0}})
    seen = []
    spy = lambda led: setattr(led, "reconcile", lambda venue, book, _r=led.reconcile, **k: seen.append(dict(book)) or _r(venue, book, **k))
    out = world.run(patch=spy)
    assert units_sent(world) == {"SPY": 10.0} and world.venue.held == {"SPY": 17.0, "OWN": 2.0}
    assert world.baseline() == {"SPY": 7.0, "OWN": 2.0} and world.book() == {"SPY": 10.0}
    assert seen == [{"SPY": 7.0, "OWN": 2.0}] * 3                 # baseline plus the fills applied before each reconcile
    assert out["gaps"]["SPY"] == {"broker_book": 10.0, "target": 10.0, "gap": 0.0}
    for wanted in ({}, {"a": {"SPY": 0.0}}, {"a": {"SPY": -50.0, "OWN": -5.0}}, {"b": {"OWN": -2.0}}):
        world.paper = paper(wanted)
        world.later()
        seen.clear()
        out = world.run(patch=spy)
        assert out["status"] == "done" and world.venue.held == {"SPY": 7.0, "OWN": 2.0}   # only its own 10 were ever sold
        assert world.book() == {"SPY": 0.0} and world.baseline() == {"SPY": 7.0, "OWN": 2.0}
    assert [o["units"] for o in world.orders()] == [10.0, -10.0]
    assert len(world.entries("baseline")) == 1


def test_no_baseline_and_orders_on_the_ledger_stops(world):
    world.paper = paper({"a": {"SPY": 10.0}})
    world.run()
    world.state_path.unlink()
    world.later()
    world.venue.calls.clear()
    out = _stop_at(world, "baseline")
    assert "already has orders" in out["reason"] and world.venue.names() == []


def test_a_baseline_with_an_unusable_holding_is_not_recorded(tmp_path):
    for i, bad in enumerate((float("nan"), -1.0, float("inf"), "3", None)):
        w = World(tmp_path / str(i))
        w.paper = paper({"a": {"SPY": 10.0}})
        w.venue.positions = lambda bad=bad: {"OWN": bad}
        out = w.run()
        assert out["stage"] == "baseline" and w.baseline() is None and w.venue.submits() == []


def test_the_broker_book_is_the_signed_sum_of_the_ledgers_fills():
    fills = [{"symbol": "SPY", "units": 4.0}, {"symbol": "SPY", "units": 6.0}, {"symbol": "QQQ", "units": 3.0},
             {"symbol": "SPY", "units": -2.5}, {"symbol": "QQQ", "units": 0.0}]
    assert osx.broker_book(fills) == {"SPY": 7.5, "QQQ": 3.0}
    assert osx.broker_book([]) == {}


# ---- outcome 5: reconcile, cancel, reconcile, journal each fill once

def test_an_order_left_open_is_cancelled_and_only_the_missing_part_is_sent(world):
    world.paper = paper({"a": {"SPY": 10.0}})
    world.run()
    first = world.venue.submits()[0]
    world.venue.fill(first, 4.0)                                  # four filled after the run
    world.next_day()
    out = world.run()
    assert out["status"] == "done" and world.venue.book[first]["state"] == "cancelled"
    orders = world.orders()
    assert [(o["units"], o["state"], o["filled_units"]) for o in orders] == [(10.0, "cancelled", 4.0), (6.0, "accepted", 0.0)]
    fills = world.entries("fill")
    assert [(f["fill_id"], f["units"], f["tag"]) for f in fills] == [(1, 4.0, first)]
    world.venue.fill(world.venue.submits()[1])
    world.later()
    out = world.run()
    assert out["orders_sent"] == 0 and world.book() == {"SPY": 10.0} and world.venue.held == {"SPY": 10.0}
    assert sorted(f["fill_id"] for f in world.entries("fill")) == [f["id"] for f in world.fills()]
    assert all(f["applied"] for f in world.fills())


@pytest.mark.parametrize("answer", ["not yet", "raise"])
def test_an_order_still_open_after_the_cancel_stops_the_run(world, answer):
    world.paper = paper({"a": {"SPY": 10.0}})
    world.run()
    first = world.venue.submits()[0]
    if answer == "raise":
        world.venue.raise_on.add("cancel")
    else:
        world.venue.cancel_answer = "not yet"
    world.later()
    world.venue.calls.clear()
    out = _stop_at(world, "reconcile")
    assert "still open" in out["reason"] and first in out["reason"] and world.venue.book[first]["state"] == "open"
    assert world.entries("cancel")[-1]["tag"] == first
    world.venue.raise_on.discard("cancel")
    world.venue.cancel_answer = "cancel requested"
    world.later()
    out = world.run()                                             # the next run asks again, and then sends
    assert out["orders_sent"] == 1 and world.venue.book[first]["state"] == "cancelled"


def test_a_blocked_ledger_stops_the_run(world):
    world.paper = paper({"a": {"SPY": 10.0}})
    world.run()
    world.venue.raise_on.add("order_status")                      # the order's fate cannot be read
    world.later()
    out = _stop_at(world, "reconcile")
    assert "not clean" in out["reason"]


# ---- outcome 6: targets

def test_targets_are_each_strategys_held_plus_pending_summed_and_scaled():
    p = paper({"a": {"SPY": 6.0, "QQQ": 4.0}, "b": {"SPY": 4.0, "TLT": -3.0}}, equity=200_000.0)
    per_symbol, per_strategy = osx.targets(p, 100_000.0)
    assert per_symbol == {"SPY": 5.0, "QQQ": 2.0, "TLT": -1.5}
    assert per_strategy == {"a": {"SPY": 3.0, "QQQ": 2.0}, "b": {"SPY": 2.0, "TLT": -1.5}}


def test_capital_defaults_to_the_paper_equity_and_scales_when_given(world):
    world.paper = paper({"a": {"SPY": 6.0, "QQQ": 4.0}, "b": {"SPY": 4.0}}, equity=100_000.0)
    world.run()
    assert units_sent(world) == {"SPY": 10.0, "QQQ": 4.0}
    world.tmp = world.tmp / "half"
    w2 = World(world.tmp.parent / "half")
    w2.paper = world.paper
    w2.run(capital=50_000.0)
    assert units_sent(w2) == {"SPY": 5.0, "QQQ": 2.0}


def test_opposite_orders_of_two_strategies_in_one_fund_net_to_one_order_or_none(world):
    world.paper = paper({"a": {"SPY": 10.0}, "b": {"SPY": -4.0}})
    world.run()
    (order,) = world.orders()
    assert order["units"] == 6.0 and order["parts"] == [{"strategy": "a", "units": 10.0}, {"strategy": "b", "units": -4.0}]
    assert len(world.venue.submits()) == 1
    w2 = World(world.tmp / "cancel")
    w2.paper = paper({"a": {"SPY": 10.0}, "b": {"SPY": -10.0}})
    out = w2.run()
    assert out["status"] == "done" and w2.orders() == [] and w2.venue.submits() == []


def test_the_parts_follow_each_strategys_own_fills(world):
    world.venue.fill_at_once = 1.0
    world.paper = paper({"a": {"SPY": 6.0}, "b": {"SPY": 4.0}})
    world.run()
    assert osx.strategy_holdings(world.orders()) == {"a": {"SPY": 6.0}, "b": {"SPY": 4.0}}
    world.paper = paper({"a": {"SPY": 6.0}, "b": {"SPY": 1.0}})           # b sells three; a is unchanged
    world.later()
    world.run()
    last = world.orders()[-1]
    assert last["units"] == -3.0 and last["parts"] == [{"strategy": "b", "units": -3.0}]
    world.paper = paper({"a": {"SPY": 6.0}})                              # b is gone: its last share is sold
    world.later()
    world.run()
    assert world.orders()[-1]["parts"] == [{"strategy": "b", "units": -1.0}] and world.book() == {"SPY": 6.0}


def test_paper_targets_come_from_a_paper_trader(tmp_path):
    from valedia_trader.research_loop.paper import PaperTrader
    limits = tmp_path / "risk.json"
    limits.write_text(json.dumps(GENEROUS))
    t = PaperTrader(tmp_path / "journal", limits, halt_file=None)
    with pytest.raises(osx.OrderStepError, match="not run a day"):
        osx.paper_targets(t)
    t.state["positions"] = {"a": {"SPY": {"units": 3.0, "cost": 1.0}}}
    t.state["pending"] = [{"strategy": "a", "symbol": "SPY", "units": 2.0}]
    t.state["history"] = [{"equity": 90_000.0, "capital_per_strategy": 45_000.0}]
    assert osx.paper_targets(t) == osx.PaperTargets({"a": {"SPY": 5.0}}, 90_000.0, 45_000.0, t.min_trade_pct)


# ---- outcome 7: quotes

@pytest.mark.parametrize("quote", ["stale", "missing", (float("nan"), 0), (0.0, 0), (-5.0, 0), (float("inf"), 0), ("500", 0),
                                   (True, 0), "naive time", "ahead", "no time", None])
def test_a_quote_that_is_stale_missing_or_not_a_positive_finite_number_means_no_order_for_that_symbol(world, quote):
    world.paper = paper({"a": {"SPY": 10.0, "QQQ": 5.0}})
    if quote == "stale":
        world.quote_age["SPY"] = 20 * 60 + 1
    elif quote == "missing":
        world.prices.pop("SPY")
    elif quote == "naive time":
        world.quote_override["SPY"] = (500.0, datetime(2026, 11, 13, 9, 59))
    elif quote == "ahead":
        world.quote_override["SPY"] = (500.0, world.clock.t + 120)
    elif quote == "no time":
        world.quote_override["SPY"] = (500.0,)
    elif quote is None:
        world.quote_override["SPY"] = None
    else:
        world.quote_override["SPY"] = (quote[0], world.clock.t)
    out = world.run()
    assert out["status"] == "done" and units_sent(world) == {"QQQ": 5.0}
    assert [s["symbol"] for s in out["skipped"]] == ["SPY"] and world.entries("no_quote")[-1]["symbol"] == "SPY"


def test_quotes_exactly_twenty_minutes_old_or_given_as_times_with_a_zone_are_used(world):
    world.paper = paper({"a": {"SPY": 10.0, "QQQ": 5.0, "IWM": 1.0}})
    world.quote_age["SPY"] = 20 * 60
    world.quote_override["QQQ"] = {"price": 400.0, "time": pd.Timestamp("2026-11-13 09:55", tz="America/New_York")}
    world.quote_override["IWM"] = (200.0, pd.Timestamp("2026-11-13 14:58", tz="UTC").to_pydatetime())
    world.run()
    assert units_sent(world) == {"SPY": 10.0, "QQQ": 5.0, "IWM": 1.0}


# ---- outcome 8: differences

def test_rounding_is_toward_zero_whole_shares_or_five_decimals():
    assert osx.round_units(10.7) == 10.0 and osx.round_units(-2.7) == -2.0 and osx.round_units(0.999) == 0.0
    assert osx.round_units(10.123456789, fractional=True) == 10.12345
    assert osx.round_units(-0.0000099, fractional=True) == 0.0 and osx.round_units(-1.999999, fractional=True) == -1.99999
    for bad in (float("nan"), float("inf"), None, "3"):
        with pytest.raises(osx.OrderStepError):
            osx.round_units(bad)


def test_differences_are_rounded_toward_zero_and_a_sale_never_exceeds_the_broker_book(world):
    world.venue.held = {"SPY": 100.0}                                     # the owner's: never sold
    world.venue.fill_at_once = 1.0
    world.paper = paper({"a": {"SPY": 10.7}})
    world.run()
    assert world.book() == {"SPY": 10.0}
    world.paper = paper({"a": {"SPY": 2.3}})                              # -7.7: sells 7
    world.later()
    world.run()
    assert world.book() == {"SPY": 3.0}
    world.paper = paper({"a": {"SPY": -5.0}})                             # wants to be short: sells its 3, no more
    world.later()
    world.run()
    assert world.book() == {"SPY": 0.0} and world.venue.held == {"SPY": 100.0}
    assert [o["units"] for o in world.orders()] == [10.0, -7.0, -3.0]


def test_the_fractional_option_rounds_toward_zero_to_five_decimals(world):
    world.paper = paper({"a": {"SPY": 1.23456789}})
    world.run(fractional=True)
    assert units_sent(world) == {"SPY": 1.23456}
    w2 = World(world.tmp / "whole")
    w2.paper = world.paper
    w2.run()
    assert units_sent(w2) == {"SPY": 1.0}


def test_a_difference_worth_less_than_the_paper_traders_smallest_trade_is_dropped(world):
    # smallest trade: min_trade_pct 0.002 of a strategy's share of 50,000 = 100, scaled by capital / paper equity
    world.paper = paper({"a": {"SPY": 0.19, "QQQ": 0.26}, "b": {"TLT": 0.9}}, equity=100_000.0, share=50_000.0, min_trade_pct=0.002)
    out = world.run(fractional=True)
    assert units_sent(world) == {"QQQ": 0.26}                             # SPY 95 and TLT 90 are below 100; QQQ 104 is not
    assert {s["symbol"] for s in out["skipped"]} == {"SPY", "TLT"}
    w2 = World(world.tmp / "double")
    w2.paper = world.paper
    w2.run(fractional=True, capital=200_000.0)                            # twice the capital: every trade is twice the value,
    assert units_sent(w2) == {"QQQ": 0.52}                                # the smallest trade too (200): the same ones are out


def test_crypto_in_the_paper_book_is_not_traded(world):
    world.prices["BTC-USD"] = 90_000.0
    world.paper = paper({"a": {"BTC-USD": 0.5, "SPY": 1.0}})
    out = world.run(fractional=True)
    assert units_sent(world) == {"SPY": 1.0} and out["skipped"][0]["symbol"] == "BTC-USD"


# ---- outcome 9: the risk gate against the broker account

def test_equity_is_the_venues_cash_plus_every_holding_at_the_quotes(world):
    world.venue.held, world.venue.cash_value = {"OWN": 10.0}, 20_000.0
    world.paper = paper({"a": {"SPY": 2.0}})
    out = world.run()
    assert out["broker_equity"] == 20_000.0 + 10 * 50.0
    world.prices.pop("OWN")                                               # a holding without a quote: equity unusable,
    world.paper = paper({"a": {"SPY": 4.0}})                              # the run stops before any order (rev 4)
    world.later()
    out = world.run()
    assert out["broker_equity"] is None and out["orders_sent"] == 0 and world.venue.submits() == [world.orders()[0]["tag"]]
    assert out["status"] == "stopped" and out["stage"] == "prices" and "no usable quote for OWN" in out["reason"]


def test_a_halt_file_refuses_everything_and_a_latch_refuses_new_risk_only(world):
    world.venue.fill_at_once = 1.0
    world.paper = paper({"a": {"SPY": 10.0}})
    world.run()
    world.paper = paper({"a": {"SPY": 5.0, "QQQ": 5.0}})
    world.latch.write_text("{}")
    world.later()
    out = world.run()
    assert [o["symbol"] for o in out["sent"]] == ["SPY"] and out["sent"][0]["units"] == -5.0
    assert out["refused"] == [{"symbol": "QQQ", "units": 5.0, "by": "risk gate",
                               "reason": "Max drawdown was hit earlier and the latch has not been reset. Trading stopped."}]
    world.halt = world.tmp / "halt.json"
    world.halt.write_text("{}")
    world.paper = paper({"a": {"QQQ": 5.0}})
    world.later()
    out = world.run()
    assert out["orders_sent"] == 0 and {r["reason"] for r in out["refused"]} == {"Operator halt file is present. Trading stopped."}
    assert len(out["refused"]) == 2 and world.entries("refused")[-1]["reason"].startswith("Operator halt")


def test_the_position_limit_and_the_position_count_are_the_accounts(world):
    world.limits.write_text(json.dumps({**GENEROUS, "max_position_pct": 0.1, "max_open_positions": 2}))
    world.venue.held, world.venue.cash_value = {"OWN": 10.0}, 99_500.0       # equity 100,000; one position held
    world.paper = paper({"a": {"SPY": 30.0, "QQQ": 20.0, "IWM": 30.0, "TLT": 50.0}})
    out = world.run()
    # SPY 15,000 is above 10% of equity; QQQ 8,000 is the largest that fits and opens the second position
    assert [o["symbol"] for o in out["sent"]] == ["QQQ"]
    assert {r["symbol"]: r["reason"] for r in out["refused"]} == {
        "SPY": "Order is larger than the position limit.", "IWM": "Too many open positions.", "TLT": "Too many open positions."}


def test_the_days_loss_is_against_the_last_run_on_an_earlier_day_and_the_fall_against_the_peak(world):
    world.limits.write_text(json.dumps({**GENEROUS, "max_daily_loss_pct": 2.0, "max_drawdown_pct": 5.0}))
    world.venue.cash_value = 100_000.0
    world.venue.fill_at_once = 1.0
    world.paper = paper({"a": {"TLT": 10.0}})
    world.run()                                                           # equity 100,000 on day one
    world.next_day()
    world.venue.cash_value -= 2_500.0                                     # the account lost 2.5%
    world.paper = paper({"a": {"TLT": 5.0, "SPY": 1.0}})
    out = world.run()
    assert [o["symbol"] for o in out["sent"]] == ["TLT"]                  # a sale reduces risk: it gets out
    assert out["refused"][0]["reason"] == "Daily loss limit hit. Trading stopped."
    world.later()                                                         # the same day: the previous run is still yesterday's,
    out = world.run()                                                     # not this morning's
    assert out["refused"][0]["reason"] == "Daily loss limit hit. Trading stopped."
    world.paper = paper({"a": {"TLT": 5.0}})
    for _ in range(2):                                                    # two days of 1.4%: each under the day's limit,
        world.next_day()                                                  # together more than 5% below the peak
        world.venue.cash_value -= 1_400.0
        assert world.run()["refused"] == []
    world.paper = paper({"a": {"TLT": 5.0, "QQQ": 1.0}})
    world.later()
    out = world.run()
    assert out["refused"][0]["reason"] == "Max drawdown hit. Trading stopped."


def test_an_unusable_limits_file_refuses_every_order_with_its_own_error(world):
    world.limits.write_text("{broken")
    world.paper = paper({"a": {"SPY": 1.0}})
    out = world.run()
    assert out["orders_sent"] == 0 and out["refused"][0]["reason"].startswith("Risk check could not be completed (RiskConfigError")


# ---- outcome 10: order of sending, cash, the day's cap

def test_sales_go_first_then_purchases_largest_value_first(world):
    world.venue.fill_at_once = 1.0
    world.paper = paper({"a": {"SPY": 2.0, "TLT": 30.0}})
    world.run()
    world.paper = paper({"a": {"SPY": 1.0, "TLT": 0.0, "QQQ": 1.0, "IWM": 3.0, "GLD": 1.0}})
    world.later()
    out = world.run()
    assert [(o["symbol"], o["units"]) for o in out["sent"]] == [("TLT", -30.0), ("SPY", -1.0), ("IWM", 3.0), ("QQQ", 1.0),
                                                                 ("GLD", 1.0)]
    assert [world.venue.book[t]["symbol"] for t in world.venue.submits()[2:]] == ["TLT", "SPY", "IWM", "QQQ", "GLD"]


def test_purchases_stop_when_the_venues_cash_less_one_percent_would_be_exceeded(world):
    world.venue.cash_value = 10_000.0                                     # 9,900 may be spent
    world.paper = paper({"a": {"SPY": 12.0, "QQQ": 10.0, "IWM": 2.0}})    # limits 505.00, 404.00, 202.00
    out = world.run()
    assert [o["symbol"] for o in out["sent"]] == ["SPY"]                  # 6,060; with QQQ 10,100 > 9,900
    assert [(x["symbol"], x["reason"].startswith("the purchases would exceed")) for x in out["left_over"]] == [("QQQ", True),
                                                                                                             ("IWM", True)]
    assert len(world.entries("left_over")) == 2                          # journaled, not queued
    world.venue.cash_value = 0.0                                          # no cash: no equity, the gate refuses them all
    world.later()
    out = world.run()
    assert out["orders_sent"] == 0 and [r["by"] for r in out["refused"]] == ["risk gate"] * 3


def test_the_days_cap_holds_across_runs_and_a_refusal_does_not_count(world):
    world.limits.write_text(json.dumps({**GENEROUS, "max_orders_per_day": 3}))
    world.paper = paper({"a": {"SPY": 1.0, "QQQ": 1.0, "IWM": 1.0, "TLT": 1.0, "GLD": 1.0}})
    world.venue.submit_mode = ["refuse", "accept", "accept", "accept"]
    out = world.run()
    assert out["orders_sent"] == 3 and len(out["refused"]) == 1 and len(out["left_over"]) == 1
    assert out["left_over"][0]["reason"] == "the day's cap of 3 orders is reached"
    world.later()
    out = world.run()                                                     # the same day: the open three are cancelled, nothing sent
    assert out["orders_sent"] == 0 and len(out["left_over"]) == 5
    world.next_day()
    out = world.run()
    assert out["orders_sent"] == 3


def test_the_cap_defaults_to_forty():
    assert osx.DEFAULT_MAX_ORDERS_PER_DAY == 40
    assert osx.max_orders_per_day(None) == 40 and osx.max_orders_per_day(dict(GENEROUS)) == 40
    assert osx.max_orders_per_day({**GENEROUS, "max_orders_per_day": 7}) == 7


# ---- outcome 11: the orders themselves

def test_limit_prices_round_to_the_cent_on_the_cautious_side():
    assert osx.limit_price(5, 123.456) == 124.69                         # 124.69056 down
    assert osx.limit_price(-5, 123.456) == 122.23                        # 122.22144 up
    assert osx.limit_price(-5, 123.456, reduces_risk=True) == 120.99     # 120.98688 up
    assert osx.limit_price(1, 100.0) == 101.0 and osx.limit_price(-1, 100.0, True) == 98.0
    assert osx.limit_price(1, 0.004) is None and osx.limit_price(-1, 0.004, True) == 0.01
    for bad in (float("nan"), 0.0, -1.0, float("inf")):
        assert osx.limit_price(1, bad) is None
    assert osx.limit_price(0, 100.0) is None


def test_each_order_is_a_day_limit_order_through_the_ledger_with_its_parts_and_one_batch_per_run(world):
    world.venue.fill_at_once = 1.0
    world.prices["SPY"] = 123.456
    world.paper = paper({"a": {"SPY": 6.0, "QQQ": 1.0}, "b": {"SPY": 4.0}})
    world.run()
    world.paper = paper({"a": {"SPY": 1.0, "QQQ": 1.0}})
    world.later()
    world.run()
    orders = world.orders()
    assert [(o["symbol"], o["units"], o["limit_price"], o["reference_price"]) for o in orders] == [
        ("SPY", 10.0, 124.69, 123.456), ("QQQ", 1.0, 404.0, 400.0), ("SPY", -9.0, 120.99, 123.456)]
    assert orders[0]["batch"] == orders[1]["batch"] != orders[2]["batch"] and orders[0]["tag"].startswith(orders[0]["batch"])
    assert orders[0]["parts"] == [{"strategy": "a", "units": 6.0}, {"strategy": "b", "units": 4.0}]
    assert sorted(orders[2]["parts"], key=lambda p: p["strategy"]) == [{"strategy": "a", "units": -5.0}, {"strategy": "b", "units": -4.0}]
    assert all(math.isclose(sum(p["units"] for p in o["parts"]), o["units"]) for o in orders)
    assert [world.venue.book[t]["limit"] for t in world.venue.submits()] == [124.69, 404.0, 120.99]


def test_a_refusal_is_journaled_and_the_run_goes_on(world):
    world.paper = paper({"a": {"SPY": 1.0, "QQQ": 1.0, "IWM": 1.0, "TLT": 1.0}})
    world.venue.submit_mode = ["refuse", "accept", "reject", "accept"]
    out = world.run()
    assert out["status"] == "done" and out["orders_sent"] == 2
    assert [(r["symbol"], r["by"]) for r in out["refused"]] == [("SPY", "venue"), ("IWM", "venue")]
    assert "NotTransmitted" in out["refused"][0]["reason"] and "OrderRejected" in out["refused"][1]["reason"]
    assert [o["state"] for o in world.orders()] == ["refused", "accepted", "refused", "accepted"]
    world.later()
    out = world.run()                                                     # a refused order is not sent twice: a new one is
    assert out["orders_sent"] == 4 and len(set(world.venue.submits())) == 8


@pytest.mark.parametrize("mode", ["raise", "lose"])
def test_an_unknown_outcome_stops_the_run_at_once(world, mode):
    world.paper = paper({"a": {"SPY": 1.0, "QQQ": 1.0, "IWM": 1.0}})
    world.venue.submit_mode = ["accept", mode, "accept"]
    out = world.run()
    assert out["status"] == "stopped" and out["stage"] == "send" and out["orders_sent"] == 1
    assert world.venue.names()[-1] == "submit" and len(world.venue.submits()) == 2   # nothing at all after it
    assert world.entries("unknown")[-1]["state"] == "unknown"
    world.later(1)
    out = world.run()
    if mode == "lose":                                                    # the venue has it: found, cancelled, sent again
        assert out["status"] == "done" and out["orders_sent"] == 3
    else:                                                                 # too early to say it never arrived
        assert out["stage"] == "reconcile" and "unknown" in out["reason"]
        world.later(10)
        assert world.run()["orders_sent"] == 3                            # declared never arrived after the settle time
    assert len(world.venue.submits()) == len(set(world.venue.submits()))


# ---- outcome 12: the summary

def test_the_summary_is_returned_and_journaled(world):
    world.venue.fill_at_once = 0.5
    world.venue.cash_value = 50_000.0
    world.paper = paper({"a": {"SPY": 10.0, "QQQ": 4.0}}, equity=80_000.0)
    out = world.run()
    assert {"status", "orders_sent", "refused", "left_over", "filled_so_far", "broker_equity", "paper_equity", "gaps"} <= set(out)
    assert out["filled_so_far"] == {"SPY": 5.0, "QQQ": 2.0} and out["paper_equity"] == 80_000.0
    assert out["broker_equity"] == 50_000.0
    assert out["gaps"] == {"SPY": {"broker_book": 5.0, "target": 10.0, "gap": 5.0}, "QQQ": {"broker_book": 2.0, "target": 4.0, "gap": 2.0}}
    journaled = world.entries("summary")[-1]
    assert {k: journaled[k] for k in out} == json.loads(json.dumps(out))


# ---- outcome 13: a second run, crashes

def test_a_second_run_on_the_same_day_sends_only_what_is_still_missing(world):
    world.venue.fill_at_once = 0.6
    world.paper = paper({"a": {"SPY": 10.0}})
    world.run()
    world.venue.fill_at_once = 0.0
    world.later()
    out = world.run()
    assert [o["units"] for o in world.orders()] == [10.0, 4.0] and out["orders_sent"] == 1
    world.venue.fill_at_once = 1.0
    world.later()
    world.run()
    world.later()
    assert world.run()["orders_sent"] == 0 and world.book() == {"SPY": 10.0}


def test_a_crash_between_intend_and_submit_loses_and_doubles_nothing(world):
    world.venue.fill_at_once = 1.0
    world.paper = paper({"a": {"SPY": 10.0}})

    def crash_at_submit(led):
        def boom(venue, tag, **k):
            raise Crash()
        led.submit_once = boom
    with pytest.raises(Crash):
        world.run(patch=crash_at_submit)
    assert [o["state"] for o in world.orders()] == ["intended"] and world.venue.submits() == []
    world.later()
    out = world.run()
    assert out["orders_sent"] == 1 and len(world.venue.submits()) == 1 and world.book() == {"SPY": 10.0}
    assert [o["state"] for o in world.orders()] == ["intended", "filled"]   # the stale one is never sent
    world.later()
    assert world.run()["orders_sent"] == 0 and len(world.venue.submits()) == 1


def test_a_crash_after_sending_counts_against_the_cap_until_the_ledger_settles_it(world):
    world.limits.write_text(json.dumps({**GENEROUS, "max_orders_per_day": 1}))
    world.paper = paper({"a": {"SPY": 1.0, "QQQ": 1.0}})

    def crash_after_submit(led):
        real = led.submit_once
        def boom(venue, tag, **k):
            real(venue, tag, **k)
            raise Crash()
        led.submit_once = boom
    with pytest.raises(Crash):
        world.run(patch=crash_after_submit)
    world.later()
    out = world.run()
    assert out["orders_sent"] == 0 and out["left_over"][0]["reason"] == "the day's cap of 1 orders is reached"


@pytest.mark.parametrize("where", ["before the journal entry", "before mark_applied"])
def test_a_crash_around_journaling_a_fill_loses_and_doubles_nothing(world, where):
    world.paper = paper({"a": {"SPY": 10.0}})
    world.run()
    world.venue.fill(world.venue.submits()[0], 10.0)
    world.later()
    real_write = world.journal.write

    def patch(led):
        if where == "before the journal entry":
            def write(day, entry):
                if entry.get("event") == "fill":
                    raise Crash()
                return real_write(day, entry)
            world.journal.write = write
        else:
            def mark(ids):
                raise Crash()
            led.mark_applied = mark
    with pytest.raises(Crash):
        world.run(patch=patch)
    world.journal.write = real_write
    assert len(world.fills()) == 1 and not world.fills()[0]["applied"]
    assert len(world.entries("fill")) == (0 if where == "before the journal entry" else 1)
    world.later()
    seen = []

    def spy(led):
        real = led.reconcile
        led.reconcile = lambda venue, book, **k: seen.append((dict(book), real(venue, book, **k)["clean"])) or led.last_reconcile()["summary"]
    out = world.run(patch=spy)
    assert out["status"] == "done" and out["orders_sent"] == 0
    assert seen == [({}, True), ({"SPY": 10.0}, True), ({"SPY": 10.0}, True)]   # the unapplied fill is not given twice
    assert [f["fill_id"] for f in world.entries("fill")] == [1] and all(f["applied"] for f in world.fills())
    assert world.book() == {"SPY": 10.0} and len(world.venue.submits()) == 1


def test_a_state_file_that_fails_while_sending_stops_the_run_and_the_next_run_recovers(world):
    world.paper = paper({"a": {"SPY": 1.0, "QQQ": 1.0}})
    real = osx.OrderStepState.note_outcome
    calls = []

    def failing(self, tag, outcome):
        calls.append(tag)
        if len(calls) == 1:
            import sqlite3
            raise sqlite3.OperationalError("disk I/O error")
        return real(self, tag, outcome)
    osx.OrderStepState.note_outcome = failing
    try:
        out = world.run()
    finally:
        osx.OrderStepState.note_outcome = real
    assert out["status"] == "stopped" and out["stage"] == "send" and len(world.venue.submits()) == 1
    world.later()
    assert world.run()["orders_sent"] == 2 and len(set(world.venue.submits())) == 3


def test_two_runs_at_once_on_one_state_file_cannot_both_send(world):
    world.paper = paper({"a": {"SPY": 1.0}})
    holder = osx._RunLock(str(world.state_path))
    assert holder.acquire() and world.state_path.parent.is_dir()        # its folder need not exist yet
    try:
        out = world.run()
        assert out["status"] == "stopped" and "another order step" in out["reason"] and world.venue.calls == []
    finally:
        holder.release()
    assert world.run()["orders_sent"] == 1


def test_the_state_file_refuses_a_file_that_is_not_its_own(tmp_path):
    import sqlite3
    other = tmp_path / "other.sqlite"
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE something (x)")
    con.commit()
    con.close()
    with pytest.raises(osx.OrderStepError):
        osx.OrderStepState(other)
    st = osx.OrderStepState(tmp_path / "state.sqlite")
    st.record_baseline({"A": 1.0, "B": 0.0})
    with pytest.raises(osx.OrderStepError, match="recorded once"):
        st.record_baseline({"A": 2.0})
    with pytest.raises(osx.OrderStepError):
        st.record_equity("2026-11-13", float("nan"))
    st.close()
    assert osx.OrderStepState(tmp_path / "state.sqlite").baseline() == {"A": 1.0}


# ---- outcome 13: many days of a venue that does everything at random

class RandomVenue(StandIn):
    """Fills fully, partly, late or not at all, refuses, rejects, raises, and loses answers, at random."""

    def __init__(self, rng, held, cash):
        super().__init__(held=held, cash=cash)
        self.rng = rng
        self.nice = False
        self.taken = []                                           # every tag the venue ever placed an order under

    def _take(self, symbol, units, tag, limit_price):
        assert tag not in self.book, f"order {tag} placed twice"
        self.taken.append(tag)
        self.book[tag] = {"symbol": symbol, "units": float(units), "limit": limit_price, "filled": 0.0, "state": "open"}
        r = self.rng.random()
        if self.nice or r < 0.25:
            self.fill(tag)
        elif r < 0.4:
            self.fill(tag, self.rng.randint(1, max(1, int(abs(units)))) * 0.5)

    def submit(self, symbol, units, tag, price=None, limit_price=None, session="CORE"):
        if self.nice:
            self.submit_mode = "accept"
        else:
            self.submit_mode = self.rng.choices(["accept", "refuse", "reject", "raise", "lose"], [70, 8, 7, 7, 8])[0]
        return super().submit(symbol, units, tag, price, limit_price, session)

    def _maybe_raise(self, method):
        if not self.nice and self.rng.random() < {"order_status": 0.03, "positions": 0.03, "cash": 0.03, "cancel": 0.08}[method]:
            raise ConnectionError(f"{method} timed out")

    def cancel(self, tag):
        if not self.nice:
            o = self.book.get(tag)
            if o is not None and self.rng.random() < 0.2:
                self.fill(tag, 0.5)                               # a fill races the cancel
            self.cancel_answer = self.rng.choices(["cancel requested", "not yet"], [85, 15])[0]
        else:
            self.cancel_answer = "cancel requested"
        return super().cancel(tag)

    def market(self):
        """Time passes between runs: open orders fill, partly or fully, late."""
        if self.nice:
            return
        for tag, o in self.book.items():
            if o["state"] in ("open", "partly_filled"):
                r = self.rng.random()
                if r < 0.3:
                    self.fill(tag)
                elif r < 0.55:
                    self.fill(tag, 0.5 * self.rng.randint(1, 4))


def _check_after_run(w, out, baseline):
    assert out["status"] in ("done", "stopped")
    assert len(w.venue.taken) == len(set(w.venue.taken))                       # no order is ever placed twice
    calls = w.venue.submits()
    assert len(calls) == len(set(calls))                                       # no tag is ever sent twice
    for sym, units in w.venue.held.items():
        assert units >= baseline.get(sym, 0.0) - 1e-9                          # owned units never below zero
    led = w.ledger()
    try:
        book = osx.broker_book(led.fills())
        assert all(u >= -1e-9 for u in book.values())
        last = led.last_reconcile()
        if last and last["clean"] and w.venue.quiet_since_positions:
            for sym in set(book) | set(w.venue.held) | set(baseline):
                assert abs(book.get(sym, 0.0) - (w.venue.held.get(sym, 0.0) - baseline.get(sym, 0.0))) < 1e-6
            return True
    finally:
        led.close()
    return False


class Watched(RandomVenue):
    """Remembers whether anything changed at the venue since its positions were last read."""
    quiet_since_positions = False

    def positions(self):
        out = super().positions()
        self.quiet_since_positions = True
        return out

    def fill(self, tag, amount=None):
        done = super().fill(tag, amount)
        if done:
            self.quiet_since_positions = False
        return done


@pytest.mark.parametrize("seed_block", range(6))
def test_run_after_run_against_a_venue_that_does_everything_at_random(tmp_path, seed_block):
    """50 scenarios per block, 300 in all: four random days of one or two runs, then four cooperative days."""
    checked = runs = stops = 0
    seen_states = set()
    for k in range(50):
        seed = seed_block * 50 + k
        rng = random.Random(seed)
        w = World(tmp_path / f"s{seed}")
        baseline = {"OWN": float(rng.randint(0, 5)), "SPY": float(rng.choice([0, 0, 3]))}
        w.venue = Watched(rng, {s: u for s, u in baseline.items() if u}, cash=rng.choice([5_000.0, 50_000.0, 1e7]))
        w.state_path = osx.state_path_for(w.ledger_path, w.venue.name)
        cap = rng.choice([1, 2, 3, 40])
        w.limits.write_text(json.dumps({**GENEROUS, "max_orders_per_day": cap}))
        symbols = ["SPY", "QQQ", "IWM"]
        for day in range(8):
            nice = day >= 4
            w.venue.nice = nice
            if nice:
                w.venue.cash_value = max(w.venue.cash_value, 1e7)
            if day < 5:                                           # the targets settle on the last random day
                wanted = {s: {sym: float(rng.choice([0, 0, 2, 5, 9, 12.5, -3])) for sym in symbols} for s in ("a", "b")}
                w.paper = paper(wanted)
            for sym in symbols:
                w.prices[sym] = rng.choice([20.0, 55.5, 101.25])
                w.quote_age[sym] = 60.0 if nice else rng.choice([60.0, 60.0, 60.0, 1500.0])
            for run in range(1 if nice else rng.randint(1, 2)):
                if run:
                    w.later(rng.choice([1, 30, 90]))
                out = w.run()
                runs += 1
                stops += out["status"] == "stopped"
                checked += _check_after_run(w, out, baseline)
                seen_states |= {o["state"] for o in w.orders()}
                w.venue.market()
            w.next_day()
        # after four cooperative days every symbol is within one share of its target, never short, baseline untouched
        targets, _ = osx.targets(w.paper, w.paper.equity)
        book = w.book()
        for sym in symbols:
            t = max(targets.get(sym, 0.0), 0.0)
            assert 0.0 <= book.get(sym, 0.0) and abs(book.get(sym, 0.0) - t) < 1.0, (seed, sym, book, targets)
            assert w.venue.held.get(sym, 0.0) == pytest.approx(baseline.get(sym, 0.0) + book.get(sym, 0.0))
        assert w.venue.held.get("OWN", 0.0) == baseline["OWN"]
    assert runs >= 50 * 9 and stops > 0 and checked > runs // 3
    assert {"filled", "cancelled", "refused", "never_arrived"} <= seen_states


# ---- the journal's readers and the new entries

def test_the_journals_readers_take_the_order_steps_entries_in_their_stride(world):
    from valedia_trader.execution_and_risk.tax import TaxLedger, TaxRules
    from valedia_trader.research_loop import LoopPaths, critic
    from valedia_trader.research_loop.ledger import HypothesisLedger
    world.venue.fill_at_once = 0.5
    world.paper = paper({"a": {"SPY": 10.0}})
    world.run()
    world.later()
    world.venue.submit_mode = ["refuse"]
    world.paper = paper({"a": {"SPY": 5.0, "QQQ": 1.0}})
    world.run()
    world.journal.write("2026-11-13", {"type": "fill", "strategy": "a", "version": "1", "symbol": "TLT", "units": 2.0,
                                       "price": 90.0, "fee": 0.1, "decided_on": "2026-11-12", "realized_pnl": None,
                                       "entry_conditions": None, "reason": "a paper fill"})
    entries = world.journal.read()
    assert {e["event"] for e in entries if e["type"] == "order_step"} >= {"baseline", "order", "fill", "refused", "summary", "cancel"}
    led = TaxLedger.from_fills(entries, TaxRules.load(LoopPaths().config / "tax_rules.json"))
    assert [lot["symbol"] for lot in led.open_lots()] == ["TLT"] and led.realized() == []   # only the paper's fill
    assert critic.panel_scorecard(entries) is None and critic.benchmark_comparison(entries) == []
    review = critic.weekly_review(HypothesisLedger(), world.journal, "2026-11-13", {}, world.limits)
    assert review["kill_switch_failures"] == 0 and str(world.tmp) in review["path"]


# ==== rev 2 (T13 rev 2): repairs after blind verification

def _slow_venue(world, minutes, after_first=None):
    """The adapter paces itself: each submit takes `minutes` on the clock."""
    original = world.venue.submit

    def slow(*a, **k):
        out = original(*a, **k)
        world.clock.t += minutes * 60
        if after_first and len(world.venue.submits()) == 1:
            after_first()
        return out
    world.venue.submit = slow


# ---- rev 2 outcome 1: a quote that ages while the run sends is checked again before its order is intended

def test_a_quote_that_ages_past_the_limit_while_the_run_sends_is_asked_for_again(world):
    world.paper = paper({"a": {"SPY": 1.0, "QQQ": 1.0, "IWM": 1.0}})          # sent SPY, QQQ, IWM (largest value first)
    _slow_venue(world, 15, after_first=lambda: world.prices.update(IWM=210.0))
    out = world.run()
    assert out["status"] == "done" and out["orders_sent"] == 3
    assert world.quote_calls == [["IWM", "QQQ", "SPY"], ["IWM"]]               # QQQ at 16 minutes is still good; IWM at 31 is not
    by = {o["symbol"]: o for o in world.orders()}
    assert (by["SPY"]["reference_price"], by["QQQ"]["reference_price"]) == (500.0, 400.0)
    assert (by["IWM"]["reference_price"], by["IWM"]["limit_price"]) == (210.0, 212.1)   # the limit comes from the quote in hand


@pytest.mark.parametrize("again", ["missing", "stale", "raises", "not a number"])
def test_a_quote_asked_for_again_that_is_unusable_leaves_its_order_over(world, again):
    world.paper = paper({"a": {"SPY": 1.0, "QQQ": 1.0, "IWM": 1.0}})
    first = world.quotes

    def quotes(symbols):
        if world.quote_calls:
            if again == "raises":
                world.quote_calls.append(list(symbols))
                raise TimeoutError("price source down")
            if again == "missing":
                world.prices.pop("IWM", None)
            elif again == "stale":
                world.quote_age["IWM"] = 25 * 60
            else:
                world.quote_override["IWM"] = (float("nan"), world.clock.t)
        return first(symbols)
    world.quotes = quotes
    _slow_venue(world, 15)
    out = world.run()
    assert out["status"] == "done" and out["orders_sent"] == 2 and len(world.quote_calls) == 2
    assert [x["symbol"] for x in out["left_over"]] == ["IWM"] and out["left_over"][0]["reason"].startswith("the quote is older than")
    assert "IWM" not in units_sent(world) and world.entries("left_over")[-1]["symbol"] == "IWM"


# ---- rev 2 outcome 2: the venue's own text never reaches the journal, the summary or the state file

MARK = "Zq<leak>"                                                               # not a symbol, not a code, not an identifier


class Leaky(StandIn):
    """Every answer and every error carries MARK."""

    def _maybe_raise(self, method):
        if method in self.raise_on:
            raise ConnectionError(f"{method} failed: {MARK}")

    def submit(self, symbol, units, tag, price=None, limit_price=None, session="CORE"):
        mode = self.submit_mode.pop(0) if isinstance(self.submit_mode, list) and self.submit_mode else "accept"
        self.calls.append(("submit", tag))
        if mode == "accept":
            self._take(symbol, units, tag, limit_price)
            return {"status": "accepted", "venue_order_id": MARK, "message": MARK}
        if mode == "refuse":
            raise NotTransmitted(f"refused: {MARK}")
        if mode == "reject":
            raise OrderRejected(f"rejected: {MARK}", "SOME_CODE")
        if mode == "reject marked code":
            raise OrderRejected(f"rejected: {MARK}", MARK)
        if mode == "odd":
            self._take(symbol, units, tag, limit_price)
            return {"status": MARK}
        raise ConnectionError(f"reset: {MARK}")

    def order_status(self, tag):
        out = super().order_status(tag)
        if out is not None:
            out.update(raw_status=MARK, venue_order_id=MARK, note=MARK)
            if self.odd_state:
                out["state"] = MARK
        return out

    odd_state = False
    odd_positions = False

    def positions(self):
        out = super().positions()
        return {**out, "SPY": MARK} if self.odd_positions else out

    def cash(self):
        out = super().cash()
        return {**out, "currency": MARK, "message": MARK}

    def cancel(self, tag):
        out = super().cancel(tag)
        return {**out, "message": MARK} if self.cancel_answer != MARK else {"status": MARK}


def test_nothing_the_venue_says_in_its_own_words_reaches_the_journal_the_summary_or_the_state_file(world):
    world.venue = Leaky()
    world.state_path = osx.state_path_for(world.ledger_path, world.venue.name)
    world.paper = paper({"a": {"SPY": 1.0, "QQQ": 1.0, "IWM": 1.0, "TLT": 1.0, "GLD": 1.0}})
    outs = []

    def run(**setup):
        for k, v in setup.items():
            setattr(world.venue, k, v)
        world.later(30)
        outs.append(world.run())
        for k in setup:
            setattr(world.venue, k, type(world.venue).__dict__.get(k, StandIn.__dict__.get(k, getattr(StandIn(), k, None))))
        world.venue.raise_on = set()
        world.venue.cancel_answer = "cancel requested"
        return outs[-1]
    run(submit_mode=["accept", "refuse", "reject", "reject marked code", "odd"])   # refusals, then an answer that is not "accepted"
    run(raise_on={"cancel"})                                                         # the cancel error
    run(cancel_answer=MARK)                                                          # a cancel answer that is not a cancel word
    run(odd_state=True)                                                              # a state that is not a state
    run(odd_positions=True)                                                          # positions that are not numbers
    run(raise_on={"positions"})
    run(raise_on={"cash"})
    run(raise_on={"order_status"})
    run(submit_mode=["lose"])                                                        # the error of an unknown outcome
    world.quotes = lambda symbols: (_ for _ in ()).throw(ValueError(MARK))
    run()
    w2 = World(world.tmp / "baseline")
    w2.venue = Leaky(held={MARK: 1.0})                                               # a position that is not a symbol
    w2.paper = world.paper
    outs.append(w2.run())
    w3 = World(world.tmp / "environment")
    w3.venue = Leaky()
    w3.venue.environment = MARK
    outs.append(w3.run())
    w4 = World(world.tmp / "baseline error")
    w4.venue = Leaky()
    w4.venue.raise_on = {"positions"}                                                # the baseline's read fails
    w4.paper = world.paper
    outs.append(w4.run())
    w5 = World(world.tmp / "attention")                                              # the ledger's attention text
    w5.venue = Leaky()
    w5.paper = world.paper
    w5.venue.submit_mode = ["lose"]
    outs.append(w5.run())
    lost = w5.venue.submits()[0]
    w5.later(30)
    outs.append(w5.run())                                                            # the lost order: never arrived
    w5.venue.book[lost] = {"symbol": "SPY", "units": 1.0, "limit": 505.0, "filled": 0.0, "state": "open"}
    w5.venue.odd_state = True                                                        # ... then it turns up, its state MARK
    w5.later(30)
    outs.append(w5.run())
    assert w5.ledger().attention() and outs[-1]["stage"] == "reconcile"
    assert any(o["status"] == "stopped" for o in outs) and any(o["refused"] for o in outs)
    worlds = (world, w2, w3, w4, w5)
    texts = [json.dumps(o) for o in outs]
    texts += [f.read_text() for w in worlds for f in sorted((w.tmp / "journal").glob("*.jsonl"))]
    texts += [w.state_path.read_bytes().decode("utf-8", "replace") for w in worlds if w.state_path.exists()]
    assert all(MARK not in t and "leak" not in t for t in texts)
    everything = "".join(texts)
    assert "SOME_CODE" in everything and "NotTransmitted" in everything and "ConnectionError" in everything   # own words kept


def test_a_venue_error_keeps_its_class_name_and_a_code_only_when_they_are_plain():
    class Odd(Exception):
        pass
    assert osx._venue_error(OrderRejected("text Zq", "SOME_CODE_9")) == "OrderRejected (code SOME_CODE_9)"
    assert osx._venue_error(OrderRejected("text Zq", "bad code")) == "OrderRejected"
    assert osx._venue_error(OrderRejected("text", "A" * 65)) == "OrderRejected"
    odd = type("Not an identifier", (Exception,), {})
    assert osx._venue_error(odd("x")) == "an exception" and osx._venue_error(Odd("Zq")) == "Odd"


# ---- rev 2 outcome 3: a paper book days old is not followed

@pytest.mark.parametrize("run_at, book_day, runs", [
    ("2026-11-13 10:00", "2026-11-12", True),          # one session old
    ("2026-11-13 10:00", "2026-11-13", True),          # the paper step already ran today
    ("2026-11-13 10:00", "2026-11-11", False),         # two sessions old
    ("2026-11-16 10:00", "2026-11-13", True),          # across a weekend: Friday's book on Monday
    ("2026-11-16 10:00", "2026-11-12", False),
    ("2026-11-27 10:00", "2026-11-25", True),          # across the Thanksgiving holiday
    ("2026-11-27 10:00", "2026-11-24", False),
    ("2026-11-27 10:00", "2026-11-26", False),         # the holiday itself is no session
    ("2026-11-13 10:00", "2026-11-16", False),         # a book from the future
    ("2026-11-13 10:00", None, False)])                # a book that does not say
def test_the_paper_book_must_be_from_the_session_before_or_today(world, run_at, book_day, runs):
    world.clock.t = ny(run_at)
    world.paper = paper({"a": {"SPY": 1.0}}, day=book_day)
    out = world.run()
    if runs:
        assert out["status"] == "done" and out["orders_sent"] == 1
    else:
        assert out["status"] == "stopped" and out["stage"] == "targets" and world.venue.calls == []
        assert "paper book" in world.entries("stopped")[-1]["reason"]


def test_paper_targets_carry_the_paper_books_last_day(tmp_path):
    from valedia_trader.research_loop.paper import PaperTrader
    limits = tmp_path / "risk.json"
    limits.write_text(json.dumps(GENEROUS))
    t = PaperTrader(tmp_path / "journal", limits, halt_file=None)
    t.state["history"] = [{"day": "2026-11-12", "equity": 90_000.0, "capital_per_strategy": 45_000.0}]
    t.state["last_day"] = "2026-11-12"
    assert osx.paper_targets(t).day == "2026-11-12"


# ---- rev 2 outcome 4: the environment and the host, read again before every send

class Sly(str):
    pass


@pytest.mark.parametrize("env, host, runs", [
    (Sly("sandbox"), None, False), ("sandbox", "api.webull.com", False), ("sandbox", "api.sandbox.webull.com", True),
    ("sandbox", Sly("api.sandbox.webull.com"), False), ("sandbox", 42, False), ("sandbox", "raise", False)])
def test_the_environment_must_be_a_plain_str_and_a_host_must_name_the_sandbox(world, env, host, runs):
    world.paper = paper({"a": {"SPY": 1.0}})

    class V(StandIn):
        environment = env
    if host == "raise":
        V.host = property(lambda self: (_ for _ in ()).throw(RuntimeError("no host")))
    elif host is not None:
        V.host = host
    world.venue = V()
    out = world.run()
    if runs:
        assert out["orders_sent"] == 1
    else:
        assert out["stage"] == "environment" and world.venue.calls == []


@pytest.mark.parametrize("change", [("environment", "production"), ("environment", Sly("sandbox")), ("host", "api.webull.com")])
def test_the_environment_is_read_again_before_every_send_and_a_change_stops_with_no_further_call(world, change):
    world.paper = paper({"a": {"SPY": 1.0, "QQQ": 1.0}})
    world.venue.host = "api.sandbox.webull.com"
    _slow_venue(world, 0, after_first=lambda: setattr(world.venue, *change))
    out = world.run()
    assert out["status"] == "stopped" and out["stage"] == "environment" and out["orders_sent"] == 1
    assert world.venue.names()[-1] == "submit" and len(world.venue.submits()) == 1
    assert [o["symbol"] for o in world.orders()] == ["SPY"]


# ---- rev 2 outcome 5: the journal on disk

def test_a_fills_journal_entry_is_synced_to_disk_before_the_fill_is_marked_applied(world, monkeypatch):
    world.paper = paper({"a": {"SPY": 10.0}})
    world.run()
    world.venue.fill(world.venue.submits()[0], 10.0)
    world.later()
    events = []
    real_sync, real_write = osx._sync_file, world.journal.write
    monkeypatch.setattr(osx, "_sync_file", lambda path: events.append(("sync", str(path))) or real_sync(path))
    world.journal.write = lambda day, entry: events.append(("write", entry.get("event"))) or real_write(day, entry)

    def spy(led):
        real = led.mark_applied
        led.mark_applied = lambda ids: events.append(("mark", list(ids))) or real(ids)
    world.run(patch=spy)
    i = events.index(("write", "fill"))
    assert events[i + 1] == ("sync", str(world.tmp / "journal" / "2026-11-13.jsonl")) and events[i + 2] == ("mark", [1])


def test_a_torn_last_line_of_the_journal_is_skipped_and_reported_and_stays_skipped(world):
    world.paper = paper({"a": {"SPY": 10.0}})
    world.venue.fill_at_once = 1.0
    world.run()
    today = world.tmp / "journal" / "2026-11-13.jsonl"
    lines = len(today.read_text().splitlines())
    with today.open("a") as fh:
        fh.write('{"type": "decision", "day": "2026-11-13", "strat')                 # a crash mid-write
    world.later()
    out = world.run()
    assert out["status"] == "done" and out["torn_lines"] == [{"file": "2026-11-13.jsonl", "line": lines + 1}]
    text = today.read_text().splitlines()
    assert text[lines].startswith('{"type": "decision"') and json.loads(text[lines + 1])["event"] == "torn_line"   # its own line
    world.paper = paper({"a": {"SPY": 12.0}})
    world.later()
    out = world.run()                                                                 # no longer the last line, but known
    assert out["status"] == "done" and out["orders_sent"] == 1 and out["torn_lines"] == [{"file": "2026-11-13.jsonl", "line": lines + 1}]
    whole = [json.loads(line) for n, line in enumerate(today.read_text().splitlines()) if line.strip() and n != lines]
    assert [e["fill_id"] for e in whole if e.get("event") == "fill"] == [1, 2]       # each fill journaled once, the torn line kept


@pytest.mark.parametrize("where", ["middle of today", "an earlier day"])
def test_a_torn_line_anywhere_else_stops_the_run_before_any_venue_call(world, where):
    world.paper = paper({"a": {"SPY": 10.0}})
    world.run()
    name = "2026-11-13.jsonl" if where == "middle of today" else "2026-11-12.jsonl"
    path = world.tmp / "journal" / name
    with path.open("a") as fh:
        fh.write('{"type": "decision", "da\n' + json.dumps({"type": "decision", "day": name[:10]}) + "\n")
    world.later()
    world.venue.calls.clear()
    out = world.run()
    assert out["status"] == "stopped" and out["stage"] == "journal" and world.venue.calls == []
    assert name in out["reason"]


# ---- rev 2 outcome 6: the stored parts sum to the order exactly

def test_the_parts_sum_to_the_order_exactly_over_ten_thousand_generated_orders():
    from valedia_trader.execution_and_risk.orders import allocate
    rng = random.Random(13)
    orders = whole = impossible = 0
    for _ in range(10_000):
        parts = [{"strategy": f"s{i}", "units": rng.uniform(-50, 50) * rng.choice([1, 1e-3, 7.3, 1e3])} for i in range(rng.randint(1, 5))]
        net = math.fsum(p["units"] for p in parts)
        if abs(net) < 1e-6:
            continue
        units = osx.round_units(net * rng.choice([1.0, 0.5, 0.999]), fractional=rng.random() < 0.5)
        if units == 0:
            continue
        orders += 1
        whole += units.is_integer()
        given = sorted(allocate(parts, units).items())
        out = osx.order_parts(parts, units)
        got = [p["units"] for p in out]
        assert [p["strategy"] for p in out] == [s for s, _ in given]
        big = max(abs(u) for _, u in given)
        assert all(abs(a - u) <= 8 * math.ulp(big) for a, (_, u) in zip(got, given))     # absorbing moves a part by ulps only
        # Floats near these parts sum exactly to `units` only when `units` is a multiple of the finest ulp among them
        # (their exact sum is always such a multiple). That fails only for fractional orders with crossing parts.
        if (units / min(math.ulp(u) for _, u in given)).is_integer():
            assert sum(got) == units and math.fsum(got) == units and math.fsum([*got, -units]) == 0.0
        else:
            impossible += 1
            assert not units.is_integer() and abs(math.fsum(got) - units) <= 2 * math.ulp(big)
    assert orders > 9_500 and whole > 4_000 and impossible < 100


# ==== rev 3 (T13 rev 3): four small repairs after the second blind verification

def _journal_lines(world):
    out = []
    for f in sorted((world.tmp / "journal").glob("*.jsonl")):
        for line in f.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def _before_the_lock(monkeypatch, other):
    """Run `other` (another order step) at the moment this run asks for the lock, as a second process would."""
    real, state = osx._RunLock.acquire, {"done": False}

    def acquire(self):
        if not state["done"]:
            state["done"] = True
            other()
        return real(self)
    monkeypatch.setattr(osx._RunLock, "acquire", acquire)


# ---- rev 3 outcome 1: the lock comes first; the journal and the paper book are read under it

def test_a_fill_journaled_by_a_run_that_dies_while_this_one_waits_for_the_lock_is_not_journaled_again(world, monkeypatch):
    world.paper = paper({"a": {"SPY": 10.0}})
    world.run()
    world.venue.fill(world.venue.submits()[0], 10.0)
    world.later()

    def other():                                                    # B: journals the fill, dies before marking it applied
        def die(led):
            led.mark_applied = lambda ids: (_ for _ in ()).throw(Crash("killed after the journal write"))
        with pytest.raises(Crash):
            world.run(patch=die)
    _before_the_lock(monkeypatch, other)
    out = world.run()                                               # A
    assert out["status"] == "done"
    ids = [e["fill_id"] for e in _journal_lines(world) if e.get("event") == "fill"]
    assert ids == [1]


def test_a_torn_line_left_by_a_run_that_dies_while_this_one_waits_for_the_lock_is_recorded(world, monkeypatch):
    world.paper = paper({"a": {"SPY": 10.0}})
    world.run()
    world.venue.fill(world.venue.submits()[0], 10.0)
    world.later()

    def other():                                                    # B: dies half way through writing the fill
        real = world.journal.write

        def torn(day, entry):
            if entry.get("event") == "fill":
                with (world.tmp / "journal" / f"{day}.jsonl").open("a") as fh:
                    fh.write('{"type": "order_step", "event": "fi')
                raise Crash("killed mid-write")
            return real(day, entry)
        world.journal.write = torn
        try:
            with pytest.raises(Crash):
                world.run()
        finally:
            del world.journal.write
    _before_the_lock(monkeypatch, other)
    out = world.run()                                               # A
    assert out["status"] == "done" and len(out["torn_lines"]) == 1
    assert [e["fill_id"] for e in _journal_lines(world) if e.get("event") == "fill"] == [1]
    world.later()
    assert world.run()["status"] == "done"                          # and every later run goes on


def test_nothing_but_the_venues_name_environment_and_host_is_read_before_the_lock(world, monkeypatch):
    seen = []
    world.paper = lambda: seen.append("paper") or paper({"a": {"SPY": 1.0}}, day="2026-11-12")
    real_scan = osx._Run._scan_journal
    monkeypatch.setattr(osx._Run, "_scan_journal", lambda self: seen.append("journal") or real_scan(self))
    _before_the_lock(monkeypatch, lambda: seen.append("lock"))
    assert world.run()["status"] == "done"
    assert seen[0] == "lock" and set(seen[1:]) == {"journal", "paper"}


# ---- rev 3 outcome 2: the window is checked on the clock just before intend and just before submit_once

def test_an_order_whose_second_quote_ask_runs_past_the_window_is_left_over(world):
    world.clock.t = ny("2026-11-13 15:44:30")                       # the window ends at 15:45
    world.paper = paper({"a": {"SPY": 1.0}})
    world.quote_age["SPY"] = 1190                                   # good at first, too old by the time of the order
    cash = world.venue.cash

    def slow_cash():
        world.clock.t += 20
        return cash()
    world.venue.cash = slow_cash
    first = world.quotes

    def quotes(symbols):
        out = first(symbols)
        if len(world.quote_calls) > 1:                              # the second ask takes 110 s
            world.clock.t += 110
            world.quote_age["SPY"] = 0
            out = first(symbols)
        return out
    world.quotes = quotes
    out = world.run()
    assert world.venue.submits() == [] and world.orders() == []
    assert out["left_over"] == [{"symbol": "SPY", "units": 1.0, "reason": "the session window closed during the run"}]


def test_an_order_intended_as_the_window_closes_is_not_sent_and_stays_intended(world):
    world.clock.t = ny("2026-11-13 15:44:50")
    world.paper = paper({"a": {"SPY": 1.0, "QQQ": 1.0}})

    def slow(led):
        real = led.intend

        def intend(*a, **k):
            out = real(*a, **k)
            world.clock.t += 20                                     # 15:45:10 by the time it would be sent
            return out
        led.intend = intend
    out = world.run(patch=slow)
    assert world.venue.submits() == [] and [o["state"] for o in world.orders()] == ["intended"]
    assert [x["reason"] for x in out["left_over"]] == ["the session window closed during the run"] * 2
    world.next_day()
    world.paper = paper({"a": {"SPY": 1.0}})
    out = world.run()
    assert out["orders_sent"] == 1 and [o["state"] for o in world.orders()] == ["intended", "accepted"]


# ---- rev 3 outcome 3: nothing a venue or a quote function returns or raises makes the run raise

def _boom(*a, **k):
    raise RuntimeError("hostile")


class Hostile:
    """An answer whose every method raises."""
    __getattr__ = __getitem__ = __iter__ = __len__ = __bool__ = __float__ = __int__ = __index__ = _boom
    __eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = __str__ = __repr__ = __format__ = __contains__ = __hash__ = _boom


class HostileDict(dict):
    get = __getitem__ = __iter__ = items = keys = values = __len__ = __contains__ = __bool__ = __repr__ = _boom


class HostileFloat(float):
    __float__ = __le__ = __lt__ = __ge__ = __gt__ = __eq__ = __ne__ = __bool__ = __repr__ = __str__ = __format__ = _boom
    __hash__ = _boom


class HostileStr(str):
    __float__ = _boom


def _plant(world, where):
    v, now = world.venue, lambda: world.clock.t
    if where.startswith("cash"):
        answer = {"cash answer": Hostile, "cash mapping": lambda: HostileDict(cash=1.0),
                  "cash value": lambda: {"cash": HostileFloat(1e6)}, "cash text": lambda: {"cash": HostileStr("1e6")}}[where]
        v.cash = lambda: answer()
    elif where.startswith("quote"):
        answer = {"quotes answer": lambda s: Hostile(), "quotes mapping": lambda s: HostileDict({x: (500.0, now()) for x in s}),
                  "quote": lambda s: {x: Hostile() for x in s}, "quote price": lambda s: {x: (HostileFloat(500.0), now()) for x in s},
                  "quote time": lambda s: {x: (500.0, HostileFloat(now())) for x in s}}[where]
        world.quotes = answer
    elif where == "second quote ask":
        first = world.quotes
        world.quotes = lambda s: first(s) if not world.quote_calls else {x: (HostileFloat(1.0), now()) for x in s}
        _slow_venue(world, 25)
    elif where.startswith("positions"):
        v.positions = {"positions answer": lambda: Hostile(), "positions mapping": lambda: HostileDict(SPY=1.0),
                       "positions units": lambda: {"SPY": HostileFloat(1.0)}}[where]
    elif where == "submit answer":
        v.submit = lambda *a, **k: Hostile()
    elif where == "order status":
        v.order_status = lambda tag: Hostile()
    elif where == "cancel answer":
        v.cancel = lambda tag: Hostile()
    elif where in ("name", "environment", "host"):
        setattr(v, where, Hostile())


HOSTILE = ["cash answer", "cash mapping", "cash value", "cash text", "quotes answer", "quotes mapping", "quote", "quote price",
           "quote time", "second quote ask", "positions answer", "positions mapping", "positions units", "submit answer",
           "order status", "cancel answer", "name", "environment", "host"]


@pytest.mark.parametrize("where", HOSTILE)
def test_a_hostile_answer_at_any_read_stops_the_run_or_leaves_an_order_over_and_a_summary_is_journaled(world, where):
    world.paper = paper({"a": {"SPY": 1.0, "QQQ": 1.0}})
    if where in ("order status", "cancel answer"):                  # these are read for orders already open
        world.run()
        world.later()
        world.paper = paper({"a": {"SPY": 2.0, "QQQ": 2.0}})
    _plant(world, where)
    out = world.run()                                               # never raises
    assert out["status"] in ("done", "stopped") and out.get("stage") != "unexpected"
    assert json.dumps(out) and "hostile" not in json.dumps(out)
    mine = [e for e in _journal_lines(world) if e.get("type") == "order_step" and e.get("batch") == out["batch"]]
    if where in ("name", "environment", "host"):                    # refused before the lock: nothing written (rev 4)
        assert out["stage"] == "environment" and mine == []
    else:
        assert mine[-1]["event"] == "summary"


def test_an_unexpected_failure_after_the_lock_is_still_journaled_with_a_summary(world, monkeypatch):
    world.paper = paper({"a": {"SPY": 1.0}})
    monkeypatch.setattr(osx._Run, "_differences", _boom)            # stands for a fault of the order step's own
    out = world.run()
    assert out["status"] == "stopped" and out["stage"] == "unexpected" and out["reason"].endswith("RuntimeError")
    assert [e for e in _journal_lines(world) if e.get("type") == "order_step"][-1]["event"] == "summary"


# ---- rev 3 outcome 4: a host that comes through __getattr__ is seen

@pytest.mark.parametrize("host, runs", [("api.webull.com", False), ("api.sandbox.webull.com", False), (None, True)])
def test_a_host_given_by_getattr_must_also_be_a_plain_attribute_naming_the_sandbox(world, host, runs):
    world.paper = paper({"a": {"SPY": 1.0}})

    class V(StandIn):
        def __getattr__(self, name):
            if name == "host" and host is not None:
                return host
            raise AttributeError(name)
    world.venue = V()
    out = world.run()
    if runs:
        assert out["orders_sent"] == 1
    else:
        assert out["stage"] == "environment" and world.venue.calls == []


# ==== rev 4 (T13 rev 4): the last small repairs

def _bytes_of(root):
    return {p.relative_to(root): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# ---- rev 4 outcome 1: a run that does not hold the lock writes nothing to the journal

def test_a_run_that_finds_the_lock_taken_writes_nothing_and_a_torn_line_from_a_crash_is_recorded_once(world, monkeypatch):
    world.paper = paper({"a": {"SPY": 10.0}})
    world.venue.fill_at_once = 1.0
    world.run()
    today = world.tmp / "journal" / "2026-11-13.jsonl"
    with today.open("a") as fh:
        fh.write('{"type": "order_step", "event": "fi')                 # an earlier crash mid-write
    world.paper = paper({"a": {"SPY": 12.0}})
    world.later()
    outs = {}
    real_scan = osx._Run._scan_journal

    def scan(self):                                                     # A holds the lock; B comes now and finds it taken
        if "B" not in outs:
            outs["B"] = world.run()
        return real_scan(self)
    monkeypatch.setattr(osx._Run, "_scan_journal", scan)
    outs["A"] = world.run()
    monkeypatch.setattr(osx._Run, "_scan_journal", real_scan)
    world.paper = paper({"a": {"SPY": 15.0}})
    world.later()
    outs["C"] = world.run()
    world.next_day()
    world.paper = paper({"a": {"SPY": 16.0}})
    outs["D"] = world.run()
    assert outs["B"]["status"] == "stopped" and outs["B"]["stage"] == "session"
    assert [outs[k]["status"] for k in "ACD"] == ["done"] * 3 and [outs[k]["orders_sent"] for k in "ACD"] == [1, 1, 1]
    entries = _journal_lines(world)
    assert sum(1 for e in entries if e.get("event") == "torn_line") == 1
    assert [e["fill_id"] for e in entries if e.get("event") == "fill"] == [1, 2, 3, 4]   # every fill once, none glued away
    assert not any(e.get("batch") == outs["B"]["batch"] for e in entries)                 # B wrote nothing


@pytest.mark.parametrize("why", ["environment", "lock taken", "clock"])
def test_a_run_stopped_before_the_lock_returns_its_summary_and_changes_no_file(world, why):
    world.paper = paper({"a": {"SPY": 10.0}})
    world.run()
    world.later()
    held = None
    if why == "environment":
        world.venue.environment = "production"
    elif why == "lock taken":
        held = osx._RunLock(str(world.state_path))
        assert held.acquire()
    else:
        world.clock.t = float("nan")
    before = _bytes_of(world.tmp)
    try:
        out = world.run()
    finally:
        if held:
            held.release()
    assert out["status"] == "stopped" and out["stage"] in ("environment", "session", "clock")
    assert _bytes_of(world.tmp) == before


# ---- rev 4 outcome 2: a huge finite number is not believed

def _one_run(world, cash=None, price=None, held=None):
    world.paper = paper({"a": {"SPY": 1.0}})
    if cash is not None:
        world.venue.cash_value = cash
    if held is not None:
        world.venue.held = held
    if price is not None:
        world.prices["SPY"] = price
    out = world.run()
    st = osx.OrderStepState(world.state_path)
    try:
        return out, st.peak()
    finally:
        st.close()


@pytest.mark.parametrize("cash, usable", [(1e12, True), (math.nextafter(1e12, math.inf), False), (1e308, False),
                                          (0.0, True), (-0.01, False)])
def test_cash_outside_zero_to_a_trillion_is_unusable_and_stops_before_any_order_recording_nothing(world, cash, usable):
    out, peak = _one_run(world, cash=cash)
    if usable:
        assert out["status"] == "done" and peak == cash
    else:
        assert out["status"] == "stopped" and out["stage"] == "prices" and out["orders_sent"] == 0 and peak is None
        assert world.venue.submits() == [] and out["broker_equity"] is None


def test_equity_above_a_trillion_is_unusable_and_stops_before_any_order_recording_nothing(world):
    out, peak = _one_run(world, cash=1e12, held={"OWN": 1.0})                  # 1e12 + 50
    assert out["status"] == "stopped" and out["stage"] == "prices" and peak is None and world.venue.submits() == []


@pytest.mark.parametrize("price, usable", [(1e7, True), (math.nextafter(1e7, math.inf), False), (0.01, True), (0.0, False),
                                           (-1.0, False)])
def test_a_quote_price_outside_zero_to_ten_million_is_unusable(world, price, usable):
    out, _ = _one_run(world, cash=1e11, price=price)
    if usable:
        assert out["orders_sent"] == 1
    else:
        assert out["orders_sent"] == 0 and out["skipped"][0]["reason"] == "the quote's price is not a positive number up to 10,000,000"


def test_a_quote_asked_for_again_above_ten_million_leaves_its_order_over(world):
    world.paper = paper({"a": {"SPY": 1.0, "QQQ": 1.0}})
    first = world.quotes
    world.quotes = lambda s: first(s) if not world.quote_calls else {x: (2e7, world.clock.t) for x in s}
    _slow_venue(world, 25)
    out = world.run()
    assert out["orders_sent"] == 1 and out["left_over"][0]["reason"].endswith("the quote's price is not a positive number "
                                                                               "up to 10,000,000")


@pytest.mark.parametrize("units, usable", [(1e12, True), (math.nextafter(1e12, math.inf), False)])
def test_a_position_above_a_trillion_units_is_unusable(world, units, usable):
    world.prices["OWN"] = 1e-6
    out, _ = _one_run(world, cash=1_000.0, held={"OWN": units})
    if usable:
        assert world.baseline() == {"OWN": units}
    else:
        assert out["stage"] == "baseline" and world.venue.names() == ["positions"]


def test_the_bounds_are_named_constants():
    assert (osx.MAX_CASH, osx.MAX_QUOTE_PRICE, osx.MAX_UNITS) == (1e12, 1e7, 1e12)


# ---- rev 4 outcome 3: the order of the last checks before sending

def test_a_slow_risk_gate_never_leaves_a_quote_older_than_the_limit_at_intend(world, monkeypatch):
    world.paper = paper({"a": {"SPY": 1.0, "QQQ": 1.0}})
    world.quote_age.update(SPY=600.0, QQQ=600.0)
    stamped, first = {}, world.quotes

    def quotes(symbols):
        out = first(symbols)
        for s, (_, t) in out.items():
            stamped[s] = t
        for s in symbols:
            world.quote_age[s] = 60.0                                        # an ask from now on is fresh
        return out
    world.quotes = quotes
    real_gate = osx.risk_gate.check_order

    def slow_gate(*a, **k):
        world.clock.t += 15 * 60
        return real_gate(*a, **k)
    monkeypatch.setattr(osx.risk_gate, "check_order", slow_gate)
    ages = []

    def watch(led):
        real = led.intend
        led.intend = lambda tag, sym, *a, **k: ages.append(world.clock.t - stamped[sym]) or real(tag, sym, *a, **k)
    out = world.run(patch=watch)
    assert out["orders_sent"] == 2 and ages and max(ages) <= osx.QUOTE_MAX_AGE_SECONDS


def test_a_slow_environment_read_never_lets_submit_once_begin_after_the_window(world):
    world.clock.t = ny("2026-11-13 15:44:40")
    world.paper = paper({"a": {"SPY": 1.0}})
    slow = {"on": False}

    class V(StandIn):
        @property
        def environment(self):
            if slow["on"]:
                world.clock.t += 40
            return "sandbox"
    world.venue = V()
    submitted_at = []
    real_submit = world.venue.submit
    world.venue.submit = lambda *a, **k: submitted_at.append(world.clock.t) or real_submit(*a, **k)

    def watch(led):
        real = led.intend
        led.intend = lambda *a, **k: (slow.update(on=True), real(*a, **k))[1]
    out = world.run(patch=watch)
    assert submitted_at == [] and [o["state"] for o in world.orders()] == ["intended"]
    assert out["left_over"][0]["reason"] == "the session window closed during the run"
