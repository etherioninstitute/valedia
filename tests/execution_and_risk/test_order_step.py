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

    class V(StandIn):                                   # slow to read; a plain str by static lookup (rev 6)
        def __getattribute__(self, name):
            if name == "environment" and slow["on"]:
                world.clock.t += 40
            return super().__getattribute__(name)
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


# ==== rev 5 (T13 rev 5): the risk gate judges the quote that is used

def _stamping(world, after_first=None):
    """Quotes that remember when each one in hand was stamped; `after_first(symbols)` runs after the first ask."""
    stamped, first = {}, world.quotes

    def quotes(symbols):
        if world.quote_calls and after_first:
            after_first(symbols)
        out = first(symbols)
        for s, (_, t) in out.items():
            stamped[s] = t
        return out
    world.quotes = quotes
    return stamped


def _intends(world):
    seen = []

    def patch(led):
        real = led.intend
        led.intend = lambda tag, sym, units, ref, **k: seen.append((sym, units, ref, world.clock.t)) or real(tag, sym, units, ref, **k)
    return seen, patch


# ---- rev 5 outcome 1: the gate judges the order at the quote intend will use

def test_a_quote_asked_for_again_is_judged_by_the_risk_gate(world):
    world.limits.write_text(json.dumps({**GENEROUS, "max_position_pct": 0.1}))      # 10% of 1,000,000
    world.venue.cash_value = 1_000_000.0
    world.prices["SPY"] = 100.0
    world.quote_age["SPY"] = 1190.0
    world.paper = paper({"a": {"SPY": 900.0}}, equity=1_000_000.0)
    cash = world.venue.cash
    world.venue.cash = lambda: (setattr(world.clock, "t", world.clock.t + 20), cash())[1]   # the first quote goes stale
    _stamping(world, after_first=lambda s: (world.prices.update(SPY=1000.0), world.quote_age.update(SPY=60.0)))
    out = world.run()
    assert world.venue.submits() == [] and out["orders_sent"] == 0
    assert out["refused"][0]["by"] == "risk gate" and out["refused"][0]["reason"] == "Order is larger than the position limit."
    assert world.entries("refused")[-1]["order"]["position_value_after"] == 900 * 1000.0


def test_a_slow_environment_read_after_the_gate_sends_the_order_back_for_a_fresh_quote(world, monkeypatch):
    world.paper = paper({"a": {"SPY": 1.0}})
    world.quote_age["SPY"] = 600.0
    stamped = _stamping(world, after_first=lambda s: world.quote_age.update(SPY=60.0))
    slow = {"on": False}

    class V(StandIn):                                   # slow to read; a plain str by static lookup (rev 6)
        def __getattribute__(self, name):
            if name == "environment" and slow["on"]:
                world.clock.t += 11 * 60
                slow["on"] = False                      # once: a later slow read is rev 6's last look, below
            return super().__getattribute__(name)
    world.venue = V()
    real_gate = osx.risk_gate.check_order
    monkeypatch.setattr(osx.risk_gate, "check_order", lambda *a, **k: (slow.update(on=True), real_gate(*a, **k))[1])
    seen, patch = _intends(world)
    out = world.run(patch=patch)
    assert out["orders_sent"] == 1 and len(world.quote_calls) == 2
    assert all(t - stamped[sym] <= osx.QUOTE_MAX_AGE_SECONDS for sym, _, _, t in seen)


def test_a_quote_that_goes_stale_twice_while_the_order_is_checked_leaves_it_over(world, monkeypatch):
    world.paper = paper({"a": {"SPY": 1.0}})
    real_gate = osx.risk_gate.check_order

    def slow_gate(*a, **k):
        world.clock.t += 25 * 60
        return real_gate(*a, **k)
    monkeypatch.setattr(osx.risk_gate, "check_order", slow_gate)
    out = world.run()
    assert world.venue.submits() == [] and world.orders() == [] and len(world.quote_calls) == 2
    assert out["left_over"][0]["reason"] == "the quote went stale again while the order was checked"


@pytest.mark.parametrize("seed_block", range(6))
def test_every_order_sent_passes_the_risk_gate_at_the_quote_actually_used(tmp_path, monkeypatch, seed_block):
    from valedia_trader.research_loop import risk_gate
    real_gate = risk_gate.check_order
    calls = []
    clock_box = {}

    def gate(order, account, *a, **k):
        calls.append((dict(order), dict(account)))
        clock_box["w"].clock.t += clock_box["gate"]
        return real_gate(order, account, *a, **k)
    monkeypatch.setattr(osx.risk_gate, "check_order", gate)
    checked = 0
    for k in range(50):
        rng = random.Random(seed_block * 50 + k)
        w = World(tmp_path / f"g{seed_block}-{k}")
        clock_box.update(w=w, gate=rng.choice([0.0, 0.0, 400.0, 1300.0]))
        w.clock.t = ny(f"2026-11-13 {rng.choice(['10:00', '13:30', '15:20'])}")
        limits = {**GENEROUS, "max_position_pct": rng.choice([0.05, 0.1, 0.3, 1.0])}
        w.limits.write_text(json.dumps(limits))
        cash = rng.choice([100_000.0, 1_000_000.0])
        held = {s: float(rng.choice([0, 0, 10])) for s in ("SPY", "QQQ")}
        w.venue = StandIn(held={s: u for s, u in held.items() if u}, cash=cash)
        w.state_path = osx.state_path_for(w.ledger_path, w.venue.name)
        for s in ("SPY", "QQQ", "IWM"):
            w.prices[s] = rng.choice([20.0, 100.0, 500.0])
            w.quote_age[s] = rng.choice([60.0, 1190.0])
        factor = rng.choice([0.5, 1.0, 2.0, 10.0])
        again_age = rng.choice([60.0, 60.0, 1500.0])

        def after_first(symbols, w=w, factor=factor, again_age=again_age):
            for s in symbols:
                w.prices[s] *= factor
                w.quote_age[s] = again_age
        _stamping(w, after_first=after_first)
        if rng.random() < 0.5:
            cash_read = w.venue.cash
            w.venue.cash = lambda w=w, cash_read=cash_read: (setattr(w.clock, "t", w.clock.t + 20), cash_read())[1]
        w.paper = paper({"a": {s: float(rng.choice([0, 50, 300, 900, 2000])) for s in ("SPY", "QQQ", "IWM")}}, equity=cash)
        calls.clear()
        seen, patch = _intends(w)
        out = w.run(patch=patch)
        sent = {e["symbol"]: e for e in out["sent"]}
        for sym, units, ref, _ in seen:
            if sym not in sent:
                continue
            owned = held.get(sym, 0.0)
            reduces = units < 0 and owned > 0 and abs(owned + units) < abs(owned)
            mine = {"symbol": sym, "side": "buy" if units > 0 else "sell", "value": abs(units) * ref, "reduces_risk": reduces,
                    "adds_to_existing": owned > 1e-12, "position_value_after": abs(owned + units) * ref}
            judged = [(o, a) for o, a in calls if o["symbol"] == sym]
            assert judged and judged[-1][0] == mine                          # the gate saw the quote intend used
            account = judged[-1][1]                                          # (the account itself: rev 6, below)
            ok, why = real_gate(mine, account, limits)
            assert ok, (sym, units, ref, why)
            assert sent[sym]["quote"] == ref and sent[sym]["limit_price"] == osx.limit_price(units, ref, reduces)
            checked += 1
    assert checked > 20


# ---- rev 5 outcome 3: a quote whose time cannot be read is unusable

class OddTime(datetime):
    mode = "nan"

    def timestamp(self):
        if OddTime.mode == "nan":
            return float("nan")
        raise OverflowError("no time")


@pytest.mark.parametrize("mode", ["nan", "raises"])
def test_a_quote_whose_time_gives_no_finite_timestamp_is_unusable(world, mode):
    from datetime import timezone
    OddTime.mode = mode
    world.paper = paper({"a": {"SPY": 1.0}})
    world.quote_override["SPY"] = (500.0, OddTime(2026, 11, 13, 15, 0, tzinfo=timezone.utc))
    out = world.run()
    assert out["orders_sent"] == 0 and out["skipped"][0]["symbol"] == "SPY"


# ---- rev 5 outcome 4: the order's own size is bounded

def test_an_order_above_max_units_in_size_is_left_over(world):
    world.prices["SPY"] = 1e-6
    world.venue.cash_value = 1e7
    world.paper = paper({"a": {"SPY": 2e12}})
    out = world.run()
    assert world.venue.submits() == [] and world.orders() == []
    assert out["left_over"] == [{"symbol": "SPY", "units": 2e12, "reason": "the order is above 1,000,000,000,000 units in size"}]


# ---- rev 5 outcome 5: the host names the sandbox as a whole label

@pytest.mark.parametrize("host, runs", [("api.sandbox.webull.com", True), ("sandbox", True), ("api.webull.com/?sandbox", False),
                                        ("sandboxapi.webull.com", False), ("api.sandbox_x.webull.com", False),
                                        ("api.webull.com#sandbox", False), ("api.sandbox.webull.com ", False)])
def test_the_host_must_name_the_sandbox_as_one_of_its_labels(world, host, runs):
    world.paper = paper({"a": {"SPY": 1.0}})
    world.venue.host = host
    out = world.run()
    if runs:
        assert out["orders_sent"] == 1
    else:
        assert out["stage"] == "environment" and world.venue.calls == []


# ==== rev 6 (T13 rev 6): four repairs before any live use

def _equity_rows(world):
    st = osx.OrderStepState(world.state_path)
    try:
        return [(d, e) for d, e in st._db.execute("SELECT day, equity FROM equity ORDER BY id")]
    finally:
        st.close()


def _seed_equity(world, rows):
    st = osx.OrderStepState(world.state_path)
    try:
        for day, equity in rows:
            st.record_equity(day, equity)
    finally:
        st.close()


# ---- rev 6 outcome 1: the account the gate sees is priced at the quotes in hand

def test_the_account_is_priced_again_when_a_held_funds_quote_is_asked_again(world):
    world.limits.write_text(json.dumps({**GENEROUS, "max_daily_loss_pct": 2.0}))
    world.venue = StandIn(held={"SPY": 5000.0}, cash=500_000.0)
    world.state_path = osx.state_path_for(world.ledger_path, world.venue.name)
    _seed_equity(world, [("2026-11-12", 1_000_000.0)])
    world.prices["SPY"] = 100.0
    world.quote_age["SPY"] = 1190.0
    world.paper = paper({"a": {"SPY": 10.0}}, equity=1_000_000.0)
    cash = world.venue.cash
    world.venue.cash = lambda: (setattr(world.clock, "t", world.clock.t + 20), cash())[1]   # the first quote goes stale
    _stamping(world, after_first=lambda s: (world.prices.update(SPY=90.0), world.quote_age.update(SPY=60.0)))
    out = world.run()
    assert world.venue.submits() == [] and out["orders_sent"] == 0
    assert out["refused"][0]["by"] == "risk gate" and out["refused"][0]["reason"] == "Daily loss limit hit. Trading stopped."
    account = world.entries("refused")[-1]["account"]
    assert account["equity"] == 950_000.0 and account["daily_loss_pct"] == pytest.approx(5.0)
    assert out["broker_equity"] == 950_000.0
    assert _equity_rows(world) == [("2026-11-12", 1_000_000.0), ("2026-11-13", 950_000.0)]   # recorded at the same prices


def test_the_peak_and_the_fall_from_it_use_the_quotes_in_hand(world):
    world.limits.write_text(json.dumps({**GENEROUS, "max_drawdown_pct": 3.0}))
    world.venue = StandIn(held={"SPY": 5000.0}, cash=500_000.0)
    world.state_path = osx.state_path_for(world.ledger_path, world.venue.name)
    _seed_equity(world, [("2026-11-10", 1_000_000.0), ("2026-11-12", 990_000.0)])
    world.prices["SPY"] = 110.0                                      # 1,050,000 at the first quote: a new peak
    world.quote_age["SPY"] = 1190.0
    world.paper = paper({"a": {"SPY": 10.0}}, equity=1_000_000.0)
    cash = world.venue.cash
    world.venue.cash = lambda: (setattr(world.clock, "t", world.clock.t + 20), cash())[1]
    _stamping(world, after_first=lambda s: (world.prices.update(SPY=90.0), world.quote_age.update(SPY=60.0)))
    out = world.run()                                                # 950,000 now: 5% below the peak of 1,000,000
    assert out["orders_sent"] == 0 and out["refused"][0]["reason"] == "Max drawdown hit. Trading stopped."
    assert world.entries("refused")[-1]["account"]["drawdown_pct"] == pytest.approx(5.0)
    st = osx.OrderStepState(world.state_path)
    try:
        assert st.peak() == 1_000_000.0                              # the stale 1,050,000 is not kept as a peak
    finally:
        st.close()


@pytest.mark.parametrize("seed_block", range(6))
def test_every_gate_verdict_matches_my_own_evaluation_at_the_quotes_in_hand(tmp_path, monkeypatch, seed_block):
    from valedia_trader.research_loop import risk_gate
    real_gate = risk_gate.check_order
    box = {}

    def gate(order, account, *a, **k):
        w, model = box["w"], box["model"]
        held = box["held"]
        value = math.fsum(u * model[s] for s, u in held.items() if u > 0)
        equity = box["cash"] + value
        peak = max(box["prior_peak"], equity)
        mine = {"equity": equity, "daily_loss_pct": max(0.0, (box["previous"] - equity) / box["previous"] * 100.0),
                "drawdown_pct": max(0.0, (peak - equity) / peak * 100.0),
                "open_positions": sum(1 for u in held.values() if u > 0) + len(box["opened"])}
        verdict = real_gate(order, account, *a, **k)
        if account != pytest.approx(mine, rel=1e-12, abs=1e-9) or verdict != real_gate(order, mine, *a, **k):
            box["bad"].append((order["symbol"], account, mine, verdict))      # raising here would be the run's own fault
        box["judged"] += 1
        box["verdicts"].add(verdict[0])
        w.clock.t += box["slow"]
        return verdict
    monkeypatch.setattr(osx.risk_gate, "check_order", gate)
    box.update(judged=0, verdicts=set())
    for k in range(50):
        rng = random.Random(10_000 + seed_block * 50 + k)
        w = World(tmp_path / f"r{seed_block}-{k}")
        w.clock.t = ny(f"2026-11-13 {rng.choice(['10:00', '13:30', '15:20'])}")
        limits = {**GENEROUS, "max_daily_loss_pct": rng.choice([2.0, 5.0, 99.0]), "max_drawdown_pct": rng.choice([3.0, 10.0, 99.0]),
                  "max_position_pct": rng.choice([0.2, 1.0])}
        w.limits.write_text(json.dumps(limits))
        cash = rng.choice([100_000.0, 500_000.0])
        held = {s: float(rng.choice([0, 1000, 5000])) for s in ("SPY", "QQQ", "IWM")}
        w.venue = StandIn(held={s: u for s, u in held.items() if u}, cash=cash)
        w.state_path = osx.state_path_for(w.ledger_path, w.venue.name)
        for s in ("SPY", "QQQ", "IWM"):
            w.prices[s] = rng.choice([20.0, 50.0, 100.0])
            w.quote_age[s] = rng.choice([60.0, 1190.0])
        first_equity = cash + sum(u * w.prices[s] for s, u in held.items())
        previous = first_equity * rng.choice([0.95, 1.0, 1.03])
        prior_peak = max(previous, first_equity * rng.choice([1.0, 1.05, 1.2]))
        _seed_equity(w, [("2026-11-10", prior_peak), ("2026-11-12", previous)])
        factor = rng.choice([0.5, 0.9, 1.0, 1.1])
        again_age = rng.choice([60.0, 60.0, 1500.0])
        model = {}
        base = w.quotes

        def quotes(symbols, w=w, base=base, model=model, factor=factor, again_age=again_age):
            if w.quote_calls:
                for s in symbols:
                    w.prices[s] *= factor
                    w.quote_age[s] = again_age
            out = base(symbols)
            for s, (p, t) in out.items():
                if w.clock.t - t <= osx.QUOTE_MAX_AGE_SECONDS:
                    model[s] = p
            return out
        w.quotes = quotes
        if rng.random() < 0.6:
            cash_read = w.venue.cash
            w.venue.cash = lambda w=w, cash_read=cash_read: (setattr(w.clock, "t", w.clock.t + 20), cash_read())[1]
        opened = set()
        real_submit = w.venue.submit

        def submit(symbol, units, *a, held=held, opened=opened, real_submit=real_submit, **kw):
            answer = real_submit(symbol, units, *a, **kw)
            if units > 0 and not held.get(symbol):
                opened.add(symbol)
            return answer
        w.venue.submit = submit
        box.update(w=w, model=model, held=held, cash=cash, previous=previous, prior_peak=prior_peak, opened=opened,
                   slow=rng.choice([0.0, 0.0, 400.0, 1300.0]))
        w.paper = paper({"a": {s: float(rng.choice([0, 10, 100, 1000])) for s in ("SPY", "QQQ", "IWM")}}, equity=first_equity)
        box["bad"] = []
        out = w.run()
        assert box["bad"] == [] and out["status"] == "done", (box["bad"], out)
        if out["broker_equity"] is not None:                        # the day's row is the equity at the last prices in hand
            last = cash + math.fsum(u * model[s] for s, u in held.items() if u > 0)
            assert _equity_rows(w)[-1] == ("2026-11-13", pytest.approx(last, rel=1e-12))
    assert box["judged"] >= 50 and box["verdicts"] == {True, False}


# ---- rev 6 outcome 2: a last look between intend and submit_once

def _after_intend(world, act):
    def patch(led):
        real = led.intend

        def intend(tag, sym, units, *a, **k):
            rec = real(tag, sym, units, *a, **k)
            act(sym, units)
            return rec
        led.intend = intend
    return patch


def test_a_quote_that_goes_stale_after_intend_leaves_the_order_intended(world):
    world.paper = paper({"a": {"SPY": 1.0}})
    out = world.run(patch=_after_intend(world, lambda s, u: setattr(world.clock, "t", world.clock.t + 25 * 60)))
    assert world.venue.submits() == [] and [o["state"] for o in world.orders()] == ["intended"]
    assert out["left_over"] == [{"symbol": "SPY", "units": 1.0, "reason": "the quote went stale before the order was sent"}]


def test_a_halt_that_appears_after_intend_leaves_every_order_intended(world):
    world.halt = world.tmp / "HALT"
    world.paper = paper({"a": {"SPY": 1.0}})
    out = world.run(patch=_after_intend(world, lambda s, u: world.halt.write_text("stop")))
    assert world.venue.submits() == [] and [o["state"] for o in world.orders()] == ["intended"]
    assert out["left_over"][0]["reason"] == "the operator halt file is there at the last look before sending"


@pytest.mark.parametrize("after", ["sale", "purchase"])
def test_a_latch_that_appears_after_intend_leaves_a_purchase_intended_but_not_a_reducing_sale(world, after):
    world.venue.fill_at_once = 1.0
    world.paper = paper({"a": {"SPY": 10.0}})
    assert world.run()["orders_sent"] == 1
    world.next_day()
    world.paper = paper({"a": {"SPY": 0.0, "QQQ": 1.0}})                 # the sale goes first, then the purchase
    out = world.run(patch=_after_intend(world, lambda s, u: (u < 0) == (after == "sale") and world.latch.write_text("{}")))
    states = {o["symbol"]: o["state"] for o in world.orders() if o["batch"] == out["batch"]}
    assert [e["symbol"] for e in out["sent"]] == ["SPY"] and states["SPY"] in ("accepted", "filled")
    if after == "sale":                                                   # the purchase then meets the latch at the gate
        assert "QQQ" not in states and out["refused"][0]["by"] == "risk gate"
    else:
        assert states["QQQ"] == "intended"
        assert out["left_over"] == [{"symbol": "QQQ", "units": 1.0, "reason": "the drawdown latch is there at the last look before sending"}]


# ---- rev 6 outcome 3: the environment by static lookup too

@pytest.mark.parametrize("how", ["property", "static production"])
def test_the_environment_must_be_a_plain_str_by_static_lookup_too(world, how):
    world.paper = paper({"a": {"SPY": 1.0}})
    if how == "property":
        class V(StandIn):
            environment = property(lambda self: "sandbox")
    else:
        class V(StandIn):
            environment = "production"

            def __getattribute__(self, name):
                return "sandbox" if name == "environment" else super().__getattribute__(name)
    world.venue = V()
    out = world.run()
    assert out["stage"] == "environment" and world.venue.calls == []


# ==== rev 7 (T13 rev 7): three more repairs before any live use

class Settling(StandIn):
    """A stand-in whose cash() also gives settled cash as Webull does: a sale's proceeds reach `cash` at once and
    `settled_cash` not that day; a purchase lowers both at once. `settled` overrides what cash() reports."""

    def __init__(self, *a, settled_cash=None, **k):
        super().__init__(*a, **k)
        self.settled_value = self.cash_value if settled_cash is None else settled_cash
        self.settled = AUTO

    def fill(self, tag, amount=None):
        o = self.book[tag]
        done = super().fill(tag, amount)
        if o["units"] > 0:
            self.settled_value -= done * o["limit"]
        return done

    def cash(self):
        answer = super().cash()
        answer["settled_cash"] = self.settled_value if self.settled == AUTO else self.settled
        if self.settled == "missing":
            del answer["settled_cash"]
        return answer


def _settling_world(world, cash, settled, held=None):
    world.venue = Settling(held=held, cash=cash, settled_cash=settled)
    world.state_path = osx.state_path_for(world.ledger_path, world.venue.name)


# ---- rev 7 invariant 1: purchases never spend unsettled money

def test_purchases_stop_at_the_settled_cash_and_the_summary_says_how_much_was_left(world):
    _settling_world(world, 100_000.0, 18_000.0)
    world.paper = paper({"a": {"SPY": 30.0, "QQQ": 10.0}})             # 15,000 then 4,000 at the quotes
    out = world.run()
    assert [e["symbol"] for e in out["sent"]] == ["SPY"]
    why = "the purchases would exceed the settled cash the venue reports less 1% (17820.00)"
    assert out["left_over"] == [{"symbol": "QQQ", "units": 10.0, "reason": why}]
    assert out["cash"] == 100_000.0 and out["settled_cash"] == 18_000.0 and out["purchases_left_unsettled"] == 4_040.0
    summary = world.entries("summary")[-1]
    assert summary["cash"] == 100_000.0 and summary["settled_cash"] == 18_000.0 and summary["purchases_left_unsettled"] == 4_040.0      # at the limit prices (rev 8)


def test_a_sale_earlier_in_the_day_does_not_pay_for_a_purchase(world):
    _settling_world(world, 10_000.0, 10_000.0)
    world.venue.fill_at_once = 1.0
    world.paper = paper({"a": {"SPY": 15.0}})                          # yesterday: 15 SPY bought (7,575 at the limit)
    world.run()
    world.next_day()
    world.venue.settled_value = world.venue.cash_value                 # it settled overnight
    world.paper = paper({"a": {"SPY": 0.0}})
    world.run()                                                        # 10:00 the sale: cash up at once, settled not
    assert world.venue.cash_value > 9_000.0 and world.venue.settled_value < 2_500.0
    world.later(60)
    world.paper = paper({"a": {"SPY": 0.0, "QQQ": 10.0}})              # 4,000: within cash, not within settled cash
    out = world.run()
    assert out["orders_sent"] == 0 and out["left_over"][0]["symbol"] == "QQQ"
    assert "settled cash" in out["left_over"][0]["reason"] and out["purchases_left_unsettled"] == 4_040.0


@pytest.mark.parametrize("settled", [float("nan"), float("inf"), -1.0, 2e12, "not a number", 1_000_000.02, [5]])
def test_an_unusable_settled_cash_stops_the_run_before_anything_is_sent(world, settled):
    _settling_world(world, 1_000_000.0, 0.0)
    world.venue.settled = settled
    world.paper = paper({"a": {"SPY": 1.0}})
    out = world.run()
    assert out["status"] == "stopped" and out["stage"] == "prices" and world.venue.submits() == []
    assert "settled cash is not a usable number" in out["reason"] and _equity_rows(world) == []


@pytest.mark.parametrize("settled", [None, "missing", 1_000_000.01, "250000.5"])
def test_a_settled_cash_that_is_missing_or_within_a_cent_of_cash_lets_the_run_go_on(world, settled):
    _settling_world(world, 1_000_000.0, 0.0)
    world.venue.settled = settled
    world.paper = paper({"a": {"SPY": 1.0}})
    out = world.run()
    assert out["status"] == "done" and out["orders_sent"] == 1 and out["cash"] == 1_000_000.0
    assert out["settled_cash"] == {None: None, "missing": None, 1_000_000.01: 1_000_000.01, "250000.5": 250000.5}[settled]
    assert out["purchases_left_unsettled"] == 0.0


@pytest.mark.parametrize("seed", range(4))
def test_purchases_never_exceed_the_smaller_of_cash_and_settled_cash_less_one_percent(tmp_path, seed):
    for k in range(25):
        rng = random.Random(70_000 + seed * 25 + k)
        w = World(tmp_path / f"s{seed}-{k}")
        cash = rng.choice([5_000.0, 20_000.0, 100_000.0])
        settled = rng.choice([None, 0.0, cash * 0.1, cash * 0.5, cash])
        w.venue = Settling(cash=cash, settled_cash=0.0)
        w.venue.settled = settled
        w.state_path = osx.state_path_for(w.ledger_path, w.venue.name)
        w.paper = paper({"a": {s: float(rng.choice([0, 1, 5, 20, 60])) for s in ("SPY", "QQQ", "IWM", "TLT")}})
        out = w.run()
        spend = cash if settled is None else min(cash, settled)
        cost = math.fsum(e["units"] * e["limit_price"] for e in out["sent"] if e["units"] > 0)
        assert cost <= spend * (1 - osx.CASH_KEPT) + 1e-9, (cash, settled, out["sent"])
        # (what is reported as left for lack of settled money: rev 8, against its own bookkeeping, below)


# ---- rev 7 invariant 2: every holding is priced at a fresh quote at every gate call

def _verifier_slow_world(world, again):
    world.limits.write_text(json.dumps({**GENEROUS, "max_daily_loss_pct": 2.0}))
    world.venue = StandIn(held={"SPY": 5000.0}, cash=500_000.0)
    world.state_path = osx.state_path_for(world.ledger_path, world.venue.name)
    _seed_equity(world, [("2026-11-12", 1_000_000.0)])
    world.prices["SPY"] = 100.0
    world.quote_age["SPY"] = 1190.0
    cash = world.venue.cash
    world.venue.cash = lambda: (setattr(world.clock, "t", world.clock.t + 61), cash())[1]   # SPY's quote goes stale
    _stamping(world, after_first=lambda s: "SPY" in s and again())        # (61 s: a minute after its first ask, rev 8)


def test_a_held_fund_with_no_order_of_its_own_is_priced_again_when_its_quote_is_too_old(world):
    _verifier_slow_world(world, lambda: (world.prices.update(SPY=90.0), world.quote_age.update(SPY=60.0)))
    world.paper = paper({"a": {"QQQ": 10.0}}, equity=1_000_000.0)
    out = world.run()
    assert world.venue.submits() == [] and out["refused"][0]["reason"] == "Daily loss limit hit. Trading stopped."
    account = world.entries("refused")[-1]["account"]
    assert account["equity"] == 950_000.0 and account["daily_loss_pct"] == pytest.approx(5.0)
    assert ["SPY"] in world.quote_calls[1:]


def test_with_no_fresh_quote_for_a_holding_a_purchase_is_left_over_and_a_reducing_sale_still_goes(world):
    world.venue = StandIn(held={"SPY": 5000.0}, cash=500_000.0)
    world.state_path = osx.state_path_for(world.ledger_path, world.venue.name)
    world.venue.fill_at_once = 1.0
    world.paper = paper({"a": {"IWM": 10.0}}, equity=1_000_000.0)
    assert world.run()["orders_sent"] == 1                                         # day 1: 10 IWM owned
    world.next_day()
    world.prices["SPY"] = 100.0
    world.quote_age["SPY"] = 1190.0
    cash = world.venue.cash
    world.venue.cash = lambda: (setattr(world.clock, "t", world.clock.t + 20), cash())[1]
    world.quote_calls.clear()
    _stamping(world, after_first=lambda s: "SPY" in s and world.quote_age.update(SPY=1500.0))   # no fresh quote comes
    world.paper = paper({"a": {"IWM": 0.0, "QQQ": 10.0}}, equity=1_000_000.0)
    out = world.run()
    assert [e["symbol"] for e in out["sent"]] == ["IWM"] and out["sent"][0]["units"] == -10.0
    assert out["left_over"] == [{"symbol": "QQQ", "units": 10.0,
                                 "reason": "the account cannot be priced: no fresh quote for SPY"}]
    assert [o["symbol"] for o in world.orders() if o["batch"] == out["batch"]] == ["IWM"]


@pytest.mark.parametrize("seed_block", range(6))
def test_every_gate_call_prices_every_holding_at_a_fresh_quote_in_slow_runs(tmp_path, monkeypatch, seed_block):
    from valedia_trader.research_loop import risk_gate
    real_gate = risk_gate.check_order
    box = {}

    def gate(order, account, *a, **k):
        w, model, held = box["w"], box["model"], box["held"]
        now = w.clock.t
        stale = [s for s, u in held.items() if u > 0 and not (-osx.QUOTE_MAX_AHEAD_SECONDS <= now - model[s][1]
                                                               <= osx.QUOTE_MAX_AGE_SECONDS)]
        if stale and not order["reduces_risk"]:
            box["bad"].append(("judged with a stale holding", order["symbol"], stale))
        equity = box["cash"] + math.fsum(u * model[s][0] for s, u in held.items() if u > 0)
        peak = max(box["prior_peak"], equity)
        mine = {"equity": equity, "daily_loss_pct": max(0.0, (box["previous"] - equity) / box["previous"] * 100.0),
                "drawdown_pct": max(0.0, (peak - equity) / peak * 100.0),
                "open_positions": sum(1 for u in held.values() if u > 0) + len(box["opened"])}
        verdict = real_gate(order, account, *a, **k)
        if account != pytest.approx(mine, rel=1e-12, abs=1e-9) or verdict != real_gate(order, mine, *a, **k):
            box["bad"].append((order["symbol"], account, mine, verdict))
        box["judged"] += 1
        box["verdicts"].add(verdict[0])
        w.clock.t += box["slow"]
        return verdict
    monkeypatch.setattr(osx.risk_gate, "check_order", gate)
    box.update(judged=0, verdicts=set(), unpriced=0)
    for k in range(50):
        rng = random.Random(90_000 + seed_block * 50 + k)
        w = World(tmp_path / f"q{seed_block}-{k}")
        w.clock.t = ny(f"2026-11-13 {rng.choice(['10:00', '13:30', '15:00'])}")
        limits = {**GENEROUS, "max_daily_loss_pct": rng.choice([2.0, 5.0, 99.0]), "max_drawdown_pct": rng.choice([3.0, 10.0, 99.0]),
                  "max_position_pct": rng.choice([0.2, 1.0])}
        w.limits.write_text(json.dumps(limits))
        cash = rng.choice([100_000.0, 500_000.0])
        held = {s: float(rng.choice([0, 1000, 5000])) for s in ("SPY", "QQQ", "TLT")}
        w.venue = StandIn(held={s: u for s, u in held.items() if u}, cash=cash)
        w.state_path = osx.state_path_for(w.ledger_path, w.venue.name)
        for s in ("SPY", "QQQ", "TLT", "IWM", "GLD"):
            w.prices[s] = rng.choice([20.0, 50.0, 100.0])
            w.quote_age[s] = rng.choice([60.0, 600.0, 1190.0])
        first_equity = cash + sum(u * w.prices[s] for s, u in held.items())
        previous = first_equity * rng.choice([0.95, 1.0, 1.03])
        prior_peak = max(previous, first_equity * rng.choice([1.0, 1.05, 1.2]))
        _seed_equity(w, [("2026-11-10", prior_peak), ("2026-11-12", previous)])
        factor = rng.choice([0.5, 0.9, 1.0, 1.1])
        again_age = rng.choice([60.0, 60.0, 300.0, 1500.0])
        model = {}
        base = w.quotes

        def quotes(symbols, w=w, base=base, model=model, factor=factor, again_age=again_age):
            if w.quote_calls:
                for s in symbols:
                    w.prices[s] *= factor
                    w.quote_age[s] = again_age
            out = base(symbols)
            for s, (p, t) in out.items():
                if w.clock.t - t <= osx.QUOTE_MAX_AGE_SECONDS:
                    model[s] = (p, t)
            return out
        w.quotes = quotes
        if rng.random() < 0.6:
            cash_read = w.venue.cash
            w.venue.cash = lambda w=w, cash_read=cash_read: (setattr(w.clock, "t", w.clock.t + 20), cash_read())[1]
        opened = set()
        real_submit = w.venue.submit

        def submit(symbol, units, *a, held=held, opened=opened, real_submit=real_submit, **kw):
            answer = real_submit(symbol, units, *a, **kw)
            if units > 0 and not held.get(symbol):
                opened.add(symbol)
            return answer
        w.venue.submit = submit
        box.update(w=w, model=model, held=held, cash=cash, previous=previous, prior_peak=prior_peak, opened=opened,
                   slow=rng.choice([0.0, 400.0, 900.0, 1300.0]), bad=[])
        w.paper = paper({"a": {s: float(rng.choice([0, 10, 100, 1000])) for s in ("IWM", "GLD", "SPY")}}, equity=first_equity)
        out = w.run()
        assert box["bad"] == [] and out["status"] == "done", (box["bad"], out)
        box["unpriced"] += sum(1 for o in out["left_over"] if o["reason"].startswith("the account cannot be priced"))
    assert box["judged"] >= 50 and box["verdicts"] == {True, False} and box["unpriced"] > 0


# ---- rev 7 invariant 3: a halt or latch file is there unless it is plainly absent

def _odd_path(tmp, how):
    import os
    p = tmp / f"odd-{how}"
    if how == "dangling link":
        os.symlink(tmp / "nowhere", p)
    elif how == "link loop":
        os.symlink(p, p)
    elif how == "link to a file":
        (tmp / "real").write_text("x")
        os.symlink(tmp / "real", p)
    elif how == "nul":
        return str(p) + "\0x"
    elif how == "unreadable":
        class Raising(os.PathLike):
            def __fspath__(self):
                raise RuntimeError("no path")
        return Raising()
    return p


@pytest.mark.parametrize("how", ["dangling link", "link loop", "link to a file", "nul", "unreadable"])
def test_a_halt_file_that_is_not_plainly_absent_counts_as_there(world, how):
    world.paper = paper({"a": {"SPY": 1.0}})
    world.halt = _odd_path(world.tmp, how)
    out = world.run()
    assert world.venue.submits() == [] and out["orders_sent"] == 0


@pytest.mark.parametrize("how", ["dangling link", "link loop", "nul", "unreadable"])
def test_a_latch_file_that_is_not_plainly_absent_stops_a_purchase(world, how):
    world.paper = paper({"a": {"SPY": 1.0}})
    world.latch = _odd_path(world.tmp, how)
    out = world.run()
    assert world.venue.submits() == [] and out["orders_sent"] == 0


def test_only_a_clean_no_such_file_is_absent():
    import os
    assert osx._present(None) is False
    assert osx._present("/definitely/not/here/halt") is False
    assert osx._present(os.devnull) is True


# ==== rev 8 (T13 rev 8): five small points after rev 7's blind check

def _purchases_in_order(w, wanted):
    """The run's purchases as the order step takes them: largest value first (then by symbol)."""
    rows = [(s, u, u * w.prices[s]) for s, u in wanted.items() if u > 0]
    return [(s, u) for s, u, _ in sorted(rows, key=lambda r: (-r[2], r[0]))]


def _money_book(cash, settled, purchases, prices):
    """My own bookkeeping of rev 8 outcome 1: which purchases go, how each one left over for money is labelled,
    and how much the cash budget would still have covered that the settled budget did not."""
    k = osx.CASH_KEPT
    cash_b = cash * (1 - k) if cash > 0 else 0.0
    set_b = None if settled is None else (settled * (1 - k) if settled > 0 else 0.0)
    budget = cash_b if set_b is None else min(cash_b, set_b)
    spent, unsettled, stop, sent, labels = 0.0, 0.0, None, [], {}
    for sym, units in purchases:
        cost = units * osx.limit_price(units, prices[sym])
        if stop is None and units * prices[sym] > cash:                    # the gate's position limit (equity = cash)
            labels[sym] = "gate"
            continue
        if stop is None and not spent + cost > budget:
            sent.append(sym)
            spent += cost
            continue
        cash_left, settled_left = cash_b - spent - unsettled, math.inf if set_b is None else set_b - spent
        over_c, over_s = cost > cash_left, cost > settled_left
        if over_s and not over_c:
            kind = "settled"
            unsettled += cost
        elif over_c and not over_s:
            kind = "cash"
        elif over_c and over_s:
            kind = "settled" if settled_left < cash_left else "cash"
        else:
            kind = stop
        stop = stop or kind
        labels[sym] = kind
    words = {"cash": f"the purchases would exceed the cash the venue reports less 1% ({cash_b:.2f})",
             "settled": f"the purchases would exceed the settled cash the venue reports less 1% ({(set_b or 0.0):.2f})"}
    return sent, {s: words[v] for s, v in labels.items() if v != "gate"}, unsettled


# ---- rev 8 outcome 1: the summary says what was really left for lack of settled money

def test_each_purchase_left_for_money_is_labelled_by_the_budget_that_stopped_it(world):
    _settling_world(world, 10_000.0, 1_000.0)                # the verifier's case: 8,000 then 5,000 (at the quotes)
    world.paper = paper({"a": {"SPY": 16.0, "QQQ": 12.0}})
    out = world.run()
    settled = "the purchases would exceed the settled cash the venue reports less 1% (990.00)"
    reasons = {o["symbol"]: o["reason"] for o in out["left_over"]}
    assert out["orders_sent"] == 0 and reasons == {"SPY": settled, "QQQ": settled}
    assert out["purchases_left_unsettled"] == 16 * 505.0     # 8,080 the cash covers; then 4,848 > the 1,820 it has left
    assert out["purchases_left_unsettled"] <= 9_900.0


def test_a_first_purchase_above_even_the_cash_leaves_later_ones_that_fit_the_cash_as_settled(world):
    _settling_world(world, 10_000.0, 1_000.0)
    world.paper = paper({"a": {"SPY": 49.0, "QQQ": 12.0}})
    world.prices["SPY"] = 204.0                          # 49 x 204 = 9,996 (the gate passes it); at 206.04: 10,095.96 > 9,900
    out = world.run()
    reasons = {o["symbol"]: o["reason"] for o in out["left_over"]}
    settled = "the purchases would exceed the settled cash the venue reports less 1% (990.00)"
    assert reasons == {"SPY": settled, "QQQ": settled}   # SPY: over both, settled the tighter; QQQ: within the cash only
    assert out["purchases_left_unsettled"] == 12 * 404.0


@pytest.mark.parametrize("seed", range(6))
def test_what_is_left_for_settled_money_matches_my_own_bookkeeping(tmp_path, seed):
    for k in range(50):
        rng = random.Random(80_000 + seed * 50 + k)
        w = World(tmp_path / f"m{seed}-{k}")
        cash = rng.choice([1_000.0, 5_000.0, 10_000.0, 30_000.0])
        settled = rng.choice([None, 0.0, cash * 0.1, cash * 0.3, cash * 0.7, cash, cash + 0.01])
        w.venue = Settling(cash=cash, settled_cash=0.0)
        w.venue.settled = settled
        w.state_path = osx.state_path_for(w.ledger_path, w.venue.name)
        for s in ("SPY", "QQQ", "IWM", "TLT", "GLD"):
            w.prices[s] = rng.choice([20.0, 101.0, 250.0, 500.0])
        wanted = {s: float(rng.choice([0, 1, 3, 8, 20, 45])) for s in ("SPY", "QQQ", "IWM", "TLT", "GLD")}
        w.paper = paper({"a": wanted})
        out = w.run()
        assert out["status"] == "done", out
        sent, labels, unsettled = _money_book(cash, settled, _purchases_in_order(w, wanted), w.prices)
        assert [e["symbol"] for e in out["sent"]] == sent, (cash, settled, wanted)
        assert {o["symbol"]: o["reason"] for o in out["left_over"]} == labels, (cash, settled, wanted)
        assert out["purchases_left_unsettled"] == pytest.approx(unsettled, abs=1e-6)
        assert out["purchases_left_unsettled"] <= cash * (1 - osx.CASH_KEPT) + 1e-6


# ---- rev 8 outcome 2: a settled figure a cent above cash, compared in whole cents

def test_a_settled_figure_exactly_one_cent_above_cash_is_accepted_in_a_run(world):
    _settling_world(world, 16384.01, 0.0)
    world.venue.settled = "16384.02"
    world.paper = paper({"a": {"SPY": 1.0}})
    out = world.run()
    assert out["status"] == "done" and out["settled_cash"] == 16384.02


def test_settled_cash_is_compared_with_cash_in_whole_cents():
    for cents in range(0, 2_000_000, 97):
        cash = round(16384 + cents / 100, 2)
        for above, ok in ((0, True), (1, True), (2, False)):
            value, bad = osx._settled_figure(cash, f"{cash + above / 100:.2f}")
            assert bad is (not ok), (cash, above)
            if ok:
                assert value == float(f"{cash + above / 100:.2f}")


# ---- rev 8 outcome 3: quote requests are bounded

def _held_stale_world(world, slow_gate, monkeypatch, again_age=1500.0, cash_delay=61.0):
    world.venue = StandIn(held={"SPY": 5000.0}, cash=500_000.0)
    world.state_path = osx.state_path_for(world.ledger_path, world.venue.name)
    world.prices["SPY"] = 100.0
    world.quote_age["SPY"] = 1190.0
    cash = world.venue.cash
    world.venue.cash = lambda: (setattr(world.clock, "t", world.clock.t + cash_delay), cash())[1]
    _stamping(world, after_first=lambda s: "SPY" in s and world.quote_age.update(SPY=again_age))
    real_gate = osx.risk_gate.check_order
    monkeypatch.setattr(osx.risk_gate, "check_order",
                        lambda *a, **k: (setattr(world.clock, "t", world.clock.t + slow_gate), real_gate(*a, **k))[1])


def test_a_holding_with_no_fresh_quote_is_asked_again_at_most_once_a_minute(world, monkeypatch):
    _held_stale_world(world, 0.0, monkeypatch)
    world.paper = paper({"a": {s: 1.0 for s in ("QQQ", "IWM", "TLT", "GLD")}}, equity=1_000_000.0)
    out = world.run()
    assert sum(1 for q in world.quote_calls[1:] if "SPY" in q) == 1
    assert [o["reason"] for o in out["left_over"]] == ["the account cannot be priced: no fresh quote for SPY"] * 4


def test_a_holding_is_asked_again_after_a_minute(world, monkeypatch):
    _held_stale_world(world, 150.0, monkeypatch, again_age=1100.0)       # usable when asked, too old 100 s later
    world.paper = paper({"a": {"QQQ": 1.0, "IWM": 1.0}}, equity=1_000_000.0)
    out = world.run()
    assert out["orders_sent"] == 2 and sum(1 for q in world.quote_calls[1:] if "SPY" in q) == 2


def test_a_run_never_makes_more_than_one_plus_three_requests_an_order(world, monkeypatch):
    _held_stale_world(world, 25 * 60.0, monkeypatch, again_age=60.0)
    world.quote_age["QQQ"] = 1190.0
    _stamping(world, after_first=lambda s: [world.quote_age.update({x: 60.0}) for x in s])
    world.paper = paper({"a": {"QQQ": 1.0}}, equity=1_000_000.0)
    out = world.run()
    considered = len(out["sent"]) + len(out["left_over"]) + len(out["refused"])
    assert considered == 1 and len(world.quote_calls) <= 1 + 3 * considered


@pytest.mark.parametrize("seed_block", range(3))
def test_quote_requests_stay_within_one_plus_three_an_order_in_slow_runs(tmp_path, monkeypatch, seed_block):
    real_gate = osx.risk_gate.check_order
    box = {}
    monkeypatch.setattr(osx.risk_gate, "check_order",
                        lambda *a, **k: (setattr(box["w"].clock, "t", box["w"].clock.t + box["slow"]), real_gate(*a, **k))[1])
    for k in range(40):
        rng = random.Random(95_000 + seed_block * 40 + k)
        w = World(tmp_path / f"b{seed_block}-{k}")
        held = {s: float(rng.choice([0, 100, 500])) for s in ("SPY", "QQQ", "TLT")}
        w.venue = StandIn(held={s: u for s, u in held.items() if u}, cash=500_000.0)
        w.state_path = osx.state_path_for(w.ledger_path, w.venue.name)
        for s in ("SPY", "QQQ", "TLT", "IWM", "GLD"):
            w.quote_age[s] = rng.choice([60.0, 1190.0])
        again = rng.choice([60.0, 1100.0, 1500.0])
        _stamping(w, after_first=lambda s, w=w, again=again: [w.quote_age.update({x: again}) for x in s])
        if rng.random() < 0.7:
            cash_read = w.venue.cash
            w.venue.cash = lambda w=w, cash_read=cash_read: (setattr(w.clock, "t", w.clock.t + 20), cash_read())[1]
        box.update(w=w, slow=rng.choice([0.0, 30.0, 61.0, 700.0, 1300.0]))
        w.paper = paper({"a": {s: float(rng.choice([0, 1, 5])) for s in ("IWM", "GLD", "SPY", "QQQ")}}, equity=1_000_000.0)
        out = w.run()
        considered = len(out["sent"]) + len(out["left_over"]) + len(out["refused"])
        assert len(w.quote_calls) <= 1 + 3 * considered, (len(w.quote_calls), considered)


# ---- rev 8 outcome 4: the order step refuses an Events account

def _events_venue(how):
    class V(StandIn):
        pass
    v = None
    if how == "name":
        v = V(name="standin-sandbox-events")
    elif how == "attribute":
        v = V()
        v.account_class = "EVENTS_CASH"
    elif how == "class attribute":
        V.account_class = "EVENTS_CASH"
    elif how == "getattr":
        V.__getattr__ = lambda self, n: "EVENTS_CASH" if n == "account_class" else (_ for _ in ()).throw(AttributeError(n))
    elif how == "property":
        V.account_class = property(lambda self: "EVENTS_CASH")
    return v or V()


@pytest.mark.parametrize("how", ["name", "attribute", "class attribute", "getattr"])
def test_an_events_venue_is_refused_before_anything_is_read_or_created(world, how):
    world.paper = paper({"a": {"SPY": 1.0}})
    world.venue = _events_venue(how)
    world.state_path = osx.state_path_for(world.ledger_path, world.venue.name)
    out = world.run()
    assert out["status"] == "stopped" and out["stage"] == "environment" and "Events" in out["reason"]
    assert world.venue.calls == [] and world.quote_calls == []
    journal = world.tmp / "journal"
    assert not world.state_path.exists() and (not journal.exists() or not any(journal.iterdir()))
    assert not [p for p in world.ledger_path.parent.iterdir() if p.name.endswith(".lock")]


def test_an_events_class_read_through_a_property_is_refused_before_any_venue_call(world):
    world.paper = paper({"a": {"SPY": 1.0}})
    world.venue = _events_venue("property")
    out = world.run()
    assert out["status"] == "stopped" and out["stage"] == "environment" and "Events" in out["reason"]
    assert world.venue.calls == [] and world.quote_calls == [] and world.orders() == []


def test_a_property_account_class_is_not_read_outside_the_session(world):
    reads = []

    class V(StandIn):
        account_class = property(lambda self: reads.append(1) or "INDIVIDUAL_CASH")
    world.venue = V()
    world.clock.t = ny("2026-11-14 11:00")                        # a Saturday
    world.paper = paper({"a": {"SPY": 1.0}})
    assert world.run()["stage"] == "session" and reads == []
    world.clock.t = ny("2026-11-16 10:00")
    assert world.run()["orders_sent"] == 1 and reads == [1]


@pytest.mark.parametrize("cls", [None, "INDIVIDUAL_CASH", "absent"])
def test_a_venue_of_another_class_runs(world, cls):
    world.paper = paper({"a": {"SPY": 1.0}})
    if cls != "absent":
        world.venue.account_class = cls
    assert world.run()["orders_sent"] == 1


# ---- rev 8 outcome 5: one rule for the halt and latch files, shared with the gate

def test_the_last_look_and_the_gate_share_one_rule_for_the_halt_and_latch_files():
    assert osx._present is osx.risk_gate.file_is_there


def test_a_halt_file_under_a_regular_file_counts_as_there(world):
    (world.tmp / "plain").write_text("x")
    world.halt = world.tmp / "plain" / "halt.json"
    world.paper = paper({"a": {"SPY": 1.0}})
    out = world.run()
    assert world.venue.submits() == [] and out["refused"][0]["reason"] == "Operator halt file is present. Trading stopped."


# ==== T22a: approval of a batch by ticket number, in the test environment (the go-live gate, step 1)
#
# A propose run writes a ticket and sends nothing; an approve run sends at most what its ticket holds, each order
# only after the fresh checks; a ticket is used once; a number cannot be guessed into a send; tickets are an
# append-only record in their own file; nothing secret gets out; a run without the new flags is the base's, byte for byte.

import hashlib
import importlib.util
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from valedia_trader.execution_and_risk.orders import OrderLedger as _Ledger

BASE_COMMIT = "c7765b9780f9e2bde4d6bc02cdd9231c34748060"   # T22a's base: the order step before tickets
ACCOUNT = "Zq9ACCT77310055XK"                               # a made-up account id: it must never appear anywhere


class Booked(StandIn):
    """The stand-in, naming its account as the adapter's account_id property does (a network read there)."""
    account = ACCOUNT

    @property
    def account_id(self):
        self.calls.append(("account_id", None))
        if isinstance(self.account, BaseException):
            raise self.account
        return self.account


def ticket_world(tmp, venue=None):
    w = World(tmp)
    w.venue = venue if venue is not None else Booked()
    w.state_path = osx.state_path_for(w.ledger_path, w.venue.name)
    return w


@pytest.fixture
def tw(tmp_path):
    return ticket_world(tmp_path / "w")


def ticket_rows(w, table):
    path = osx.ticket_path_for(w.state_path)
    if not Path(path).exists():
        return []
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(f"SELECT * FROM {table} ORDER BY rowid")]
    finally:
        con.close()


def propose(w, **kw):
    return w.run(propose=True, **kw)


def approve(w, number, **kw):
    return w.run(approve=number, **kw)


def _proposed(w, wanted, **kw):
    w.paper = paper(wanted)
    out = propose(w, **kw)
    assert out["status"] == "done" and out["ticket"]["orders"], out
    return out, out["ticket_number"]


def _all_text(w, outs=()):
    texts = [json.dumps(o, default=str) for o in outs]
    texts += [f.read_bytes().decode("utf-8", "replace") for f in sorted((w.tmp / "journal").glob("*.jsonl"))]
    for p in (w.state_path, osx.ticket_path_for(w.state_path)):
        if Path(p).exists():
            texts.append(Path(p).read_bytes().decode("utf-8", "replace"))
    return "\n".join(texts)


def wrong_other_number(number):
    return f"{(int(number) + 1) % 1_000_000:06d}"


# ---- invariant 1: a propose run sends nothing and intends nothing

def test_a_propose_run_writes_a_ticket_and_sends_and_intends_nothing(tw):
    tw.venue.held = {"OWN": 3.0}
    tw.paper = paper({"a": {"SPY": 10.0, "QQQ": 4.0}})
    out = propose(tw)
    assert out["status"] == "done" and out["mode"] == "propose" and out["orders_sent"] == 0 and out["sent"] == []
    assert "submit" not in tw.venue.names() and tw.orders() == []                      # nothing intended, nothing sent
    number = out["ticket_number"]
    assert isinstance(number, str) and len(number) == 6 and number.isdigit()
    t = out["ticket"]
    assert t["number_ends"] == number[-2:]
    assert [(o["fund"], o["side"], o["units"], o["quote"], o["limit"], o["value"]) for o in t["orders"]] == [
        ("SPY", "buy", 10.0, 500.0, 505.0, 5000.0), ("QQQ", "buy", 4.0, 400.0, 404.0, 1600.0)]
    assert t["equity"] == 1_000_000.0 + 3 * 50.0 and t["cash"] == 1_000_000.0 and t["settled_cash"] is None
    created, expires = pd.Timestamp(t["created"]), pd.Timestamp(t["expires"])
    assert created.timestamp() == tw.clock.t and (expires - created).total_seconds() == osx.TICKET_LIFE_SECONDS == 15 * 60
    entries = tw.entries()
    assert [e["event"] for e in entries] == ["baseline", "ticket", "summary"]
    ticket_entry = entries[1]
    assert ticket_entry["number_ends"] == number[-2:] and ticket_entry["orders"] == t["orders"]
    assert ticket_entry["expires"] == t["expires"]
    journal_text = "".join(f.read_text() for f in (tw.tmp / "journal").glob("*.jsonl"))
    assert number not in journal_text                                                  # the full number is never journaled
    assert "ticket_number" not in tw.entries("summary")[-1]
    assert [r["number"] for r in ticket_rows(tw, "tickets")] == [number]


def test_a_propose_run_reconciles_and_cancels_this_ledgers_open_orders_as_an_ordinary_run_does(tw):
    tw.paper = paper({"a": {"SPY": 10.0}})
    tw.run()
    first = tw.venue.submits()[0]
    tw.venue.calls.clear()
    tw.later()
    tw.paper = paper({"a": {"SPY": 12.0}})
    out = propose(tw)
    names = tw.venue.names()
    assert ("cancel", first) in tw.venue.calls and "submit" not in names
    assert out["ticket"]["orders"][0]["units"] == 12.0 and [o["state"] for o in tw.orders()] == ["cancelled"]


def test_a_propose_run_never_reaches_intend_or_submit_once_even_through_the_ledger(tw, monkeypatch):
    calls = []
    monkeypatch.setattr(_Ledger, "intend", lambda self, *a, **k: calls.append("intend"))
    monkeypatch.setattr(_Ledger, "submit_once", lambda self, *a, **k: calls.append("submit_once"))
    tw.paper = paper({"a": {"SPY": 10.0, "QQQ": -0.0, "IWM": 3.0}})
    out = propose(tw)
    assert out["status"] == "done" and calls == [] and len(out["ticket"]["orders"]) == 2


def test_a_propose_run_with_nothing_to_send_makes_no_ticket(tw):
    tw.paper = paper({})
    out = propose(tw)
    assert out["status"] == "done" and out["ticket"]["orders"] == [] and out["ticket"]["number_ends"] is None
    assert "ticket_number" not in out and ticket_rows(tw, "tickets") == []


class KillVenue(Watched):
    """The random venue, naming its account; every call is a point where the process may die."""
    killer = None

    def _tick(self, where):
        if self.killer is not None:
            self.killer.tick(where)

    @property
    def account_id(self):
        self._tick("account_id")
        self.calls.append(("account_id", None))
        return ACCOUNT

    def positions(self):
        self._tick("positions")
        return super().positions()

    def cash(self):
        self._tick("cash")
        return super().cash()

    def cancel(self, tag):
        self._tick("cancel")
        return super().cancel(tag)

    def order_status(self, tag):
        self._tick("order_status")
        return super().order_status(tag)

    def submit(self, symbol, units, tag, price=None, limit_price=None, session="CORE"):
        self._tick("submit")
        out = super().submit(symbol, units, tag, price, limit_price, session)
        self._tick("after submit")
        return out


class Killer:
    """Dies (raises Crash) at the n-th point it is asked about; n None: never."""

    def __init__(self, at):
        self.at, self.n, self.where = at, 0, None

    def tick(self, where):
        self.n += 1
        if self.at is not None and self.n == self.at:
            self.where = where
            raise Crash(where)


def _kill_points(monkeypatch, box):
    """Every write of the ledger, the state file and the ticket file, before and after, is a point where the process
    may die (box["killer"])."""
    def wrap(cls, name):
        real = getattr(cls, name)

        def method(self, *a, **k):
            if box.get("killer"):
                box["killer"].tick(f"before {name}")
            out = real(self, *a, **k)
            if box.get("killer"):
                box["killer"].tick(f"after {name}")
            return out
        monkeypatch.setattr(cls, name, method)
    for name in ("intend", "submit_once", "reconcile", "mark_applied"):
        wrap(_Ledger, name)
    for name in ("record_equity", "note_sending", "note_outcome", "settle_sends", "record_baseline"):
        wrap(osx.OrderStepState, name)
    for name in ("create", "mark_used", "mark_expired", "note_outcome", "note_attempt"):
        wrap(osx.TicketBook, name)


def _random_world(tmp, rng, killer=None):
    held = {"OWN": float(rng.randint(0, 5)), "SPY": float(rng.choice([0, 0, 3]))}
    v = KillVenue(rng, {s: u for s, u in held.items() if u}, cash=rng.choice([5_000.0, 50_000.0, 1e7]))
    w = ticket_world(tmp, v)
    w.limits.write_text(json.dumps({**GENEROUS, "max_orders_per_day": rng.choice([1, 2, 3, 40])}))
    return w, held


def _random_paper(rng):
    return paper({s: {sym: float(rng.choice([0, 0, 2, 5, 9, 12.5, -3])) for sym in ("SPY", "QQQ", "IWM")} for s in ("a", "b")})


def _killing_quotes(w, box):
    real = w.quotes

    def quotes(symbols):
        if box.get("killer"):
            box["killer"].tick("quotes")
        return real(symbols)
    w.quotes = quotes


@pytest.mark.parametrize("seed_block", range(6))
def test_a_propose_run_never_intends_or_sends_whatever_happens(tmp_path, monkeypatch, seed_block):
    """50 scenarios per block, 300 in all: random earlier runs, then propose runs that die at a random point."""
    box = {}
    _kill_points(monkeypatch, box)
    intends = []
    real_intend = _Ledger.intend

    def intend(self, *a, **k):
        intends.append(box.get("phase"))
        return real_intend(self, *a, **k)
    monkeypatch.setattr(_Ledger, "intend", intend)
    killed = tickets = 0
    for k in range(50):
        rng = random.Random(120_000 + seed_block * 50 + k)
        w, _ = _random_world(tmp_path / f"p{seed_block}-{k}", rng)
        _killing_quotes(w, box)
        for sym in ("SPY", "QQQ", "IWM"):
            w.prices[sym] = rng.choice([20.0, 55.5, 101.25])
            w.quote_age[sym] = rng.choice([60.0, 60.0, 1500.0])
        for _ in range(rng.randint(0, 2)):                                # earlier ordinary runs leave orders about
            box["phase"] = "ordinary"
            w.paper = _random_paper(rng)
            w.run()
            w.venue.market()
            w.later(rng.choice([1, 30]))
        for _ in range(rng.randint(1, 3)):
            w.paper = _random_paper(rng)
            box["phase"] = "propose"
            box["killer"] = w.venue.killer = Killer(rng.choice([None] * 10 + list(range(1, 40))))
            before_orders = [(o["tag"], o["state"]) for o in w.orders()]
            before_submits = len(w.venue.submits())
            try:
                out = propose(w)
                tickets += bool(out.get("ticket_number"))
            except Crash:
                killed += 1
            finally:
                box["killer"] = w.venue.killer = None
            assert len(w.venue.submits()) == before_submits                # no venue order call
            assert [t for t, _ in before_orders] == [o["tag"] for o in w.orders()]   # no new intent in the ledger
            w.venue.market()
            w.later(rng.choice([1, 5]))
        assert "propose" not in intends
    assert killed > 20 and tickets > 10


# ---- invariant 2: an approve run sends at most what its ticket holds, each order after the fresh checks

def test_an_approve_run_sends_the_tickets_orders_through_the_ledger(tw):
    out, number = _proposed(tw, {"a": {"SPY": 10.0, "QQQ": 4.0}})
    tw.later(5)
    tw.venue.calls.clear()
    out = approve(tw, number)
    assert out["status"] == "done" and out["mode"] == "approve" and out["orders_sent"] == 2
    assert sorted((o["symbol"], o["units"]) for o in tw.orders()) == [("QQQ", 4.0), ("SPY", 10.0)]
    assert [(o["fund"], o["outcome"]) for o in out["ticket"]["orders"]] == [("SPY", "sent"), ("QQQ", "sent")]
    assert all(o["tag"].startswith(out["batch"]) for o in out["ticket"]["orders"])
    names = tw.venue.names()                                                # the account, cash and quotes read again
    assert names.count("submit") == 2 and "account_id" in names and "cash" in names and "quotes" in names
    assert names.index("account_id") < names.index("positions") < names.index("quotes") < names.index("submit")
    assert [e["event"] for e in tw.entries()][-3:] == ["order", "order", "summary"]
    outcomes = ticket_rows(tw, "outcomes")
    assert [(r["line"], r["outcome"]) for r in outcomes] == [(0, "sent"), (1, "sent")]
    assert [r["fate"] for r in ticket_rows(tw, "fates")] == ["used"]


def test_an_order_whose_wanted_size_or_side_changed_is_dropped_and_the_rest_go_on(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0, "QQQ": 4.0, "IWM": 2.0, "TLT": 3.0}})
    tw.paper = paper({"a": {"SPY": 11.0, "QQQ": 4.0, "IWM": 0.0, "GLD": 1.0, "TLT": 2.0}})   # SPY more, IWM gone, TLT fewer
    tw.later(2)
    out = approve(tw, number)
    by = {o["fund"]: o for o in out["ticket"]["orders"]}
    assert by["QQQ"]["outcome"] == "sent" and [o["symbol"] for o in tw.orders()] == ["QQQ"]
    for sym in ("SPY", "IWM", "TLT"):
        assert by[sym]["outcome"] == "dropped" and "wanted" in by[sym]["reason"]
    assert {"symbol": "GLD", "units": 1.0, "reason": "not on the ticket"} in out["skipped"]
    assert sorted(e["symbol"] for e in tw.entries("dropped")) == ["IWM", "SPY", "TLT"]


def test_fractional_units_must_match_to_within_a_billionth(tw):
    _, number = _proposed(tw, {"a": {"SPY": 1.23456, "QQQ": 2.5}}, fractional=True)
    tw.paper = paper({"a": {"SPY": 1.23456, "QQQ": 2.50001}})
    out = approve(tw, number, fractional=True)
    by = {o["fund"]: o["outcome"] for o in out["ticket"]["orders"]}
    assert by == {"SPY": "sent", "QQQ": "dropped"} and [o["units"] for o in tw.orders()] == [1.23456]


@pytest.mark.parametrize("move, sent", [(0.0, True), (0.004, True), (0.005, True), (-0.005, True), (0.0051, False),
                                        (-0.006, False), (0.02, False)])
def test_an_order_whose_fresh_quote_moved_more_than_half_a_percent_is_dropped(tw, move, sent):
    assert osx.TICKET_QUOTE_MOVE == 0.005
    _, number = _proposed(tw, {"a": {"SPY": 10.0, "QQQ": 1.0}})
    tw.prices["SPY"] = 500.0 * (1 + move)
    out = approve(tw, number)
    by = {o["fund"]: o for o in out["ticket"]["orders"]}
    assert by["QQQ"]["outcome"] == "sent"
    assert (by["SPY"]["outcome"] == "sent") is sent
    if not sent:
        assert "0.5%" in by["SPY"]["reason"] and "SPY" not in units_sent(tw)


def test_a_quote_asked_for_again_during_the_approve_is_the_one_compared_with_the_ticket(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    tw.quote_age["SPY"] = 1190.0                                           # usable when asked, too old once cash is read
    real_cash, real = tw.venue.cash, tw.quotes

    def cash():
        tw.clock.t += 30
        return real_cash()

    def quotes(symbols):
        out = real(symbols)
        if len(tw.quote_calls) >= 3:                                       # the approve's second ask: the price moved
            out = {s: (510.0 if s == "SPY" else q[0], tw.clock.t - 60.0) for s, q in out.items()}
        return out
    tw.venue.cash, tw.quotes = cash, quotes
    out = approve(tw, number)
    assert out["ticket"]["orders"][0]["outcome"] == "dropped" and "0.5%" in out["ticket"]["orders"][0]["reason"]
    assert tw.venue.submits() == []


def test_an_order_the_risk_gate_now_refuses_is_dropped_and_a_reducing_sale_still_goes(tw):
    tw.venue.held = {}
    tw.paper = paper({"a": {"SPY": 4.0}})
    tw.run()
    tw.venue.fill(tw.venue.submits()[0])
    tw.later(30)
    _, number = _proposed(tw, {"a": {"SPY": 1.0, "QQQ": 2.0}})
    tw.latch.write_text("{}")                                               # the drawdown latch appears
    out = approve(tw, number)
    by = {o["fund"]: o for o in out["ticket"]["orders"]}
    assert by["SPY"]["outcome"] == "sent" and by["QQQ"]["outcome"] == "dropped" and "latch" in by["QQQ"]["reason"]


def test_every_ordinary_check_still_applies_to_an_approved_order(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0, "QQQ": 1.0}})
    tw.limits.write_text(json.dumps({**GENEROUS, "max_orders_per_day": 1}))   # the day's cap, now one order
    out = approve(tw, number)
    by = {o["fund"]: o for o in out["ticket"]["orders"]}
    assert by["SPY"]["outcome"] == "sent" and by["QQQ"]["outcome"] == "dropped" and "cap" in by["QQQ"]["reason"]


def test_a_ticket_expires_fifteen_minutes_after_it_was_made(tmp_path):
    assert 0 < osx.TICKET_LIFE_SECONDS == 15 * 60 <= osx.TICKET_LIFE_MAX_SECONDS == 60 * 60
    for after, goes in ((15 * 60, True), (15 * 60 + 1, False), (59 * 60, False)):
        w = ticket_world(tmp_path / f"e{after}")
        _, number = _proposed(w, {"a": {"SPY": 10.0}})
        w.clock.t += after
        w.venue.calls.clear()
        out = approve(w, number)
        if goes:
            assert out["status"] == "done" and out["orders_sent"] == 1
        else:
            assert out["status"] == "stopped" and out["stage"] == "ticket" and "expired" in out["reason"]
            assert w.venue.submits() == [] and w.orders() == []
            assert [r["fate"] for r in ticket_rows(w, "fates")] == ["expired"]
            assert [r["outcome"] for r in ticket_rows(w, "outcomes")] == ["not sent"]     # rev 2: the record's words


def test_a_ticket_that_expires_while_the_approve_runs_sends_nothing_more(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0, "QQQ": 1.0}})
    tw.later(14)
    _slow_venue(tw, 2)                                                     # the first submit takes two minutes
    out = approve(tw, number)
    by = {o["fund"]: o for o in out["ticket"]["orders"]}
    assert by["SPY"]["outcome"] == "sent" and by["QQQ"]["outcome"] == "dropped" and "expired" in by["QQQ"]["reason"]
    assert len(tw.venue.submits()) == 1


def test_a_ticket_that_expires_between_intend_and_submit_once_leaves_the_order_intended(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    tw.later(14)
    out = tw.run(approve=number, patch=_after_intend(tw, lambda s, u: setattr(tw.clock, "t", tw.clock.t + 120)))
    assert tw.venue.submits() == [] and [o["state"] for o in tw.orders()] == ["intended"]
    assert out["ticket"]["orders"][0]["outcome"] == "dropped" and "expired" in out["ticket"]["orders"][0]["reason"]


@pytest.mark.parametrize("how", ["another day", "another account", "another venue", "another capital", "fractional",
                                 "wrong number", "not digits", "five digits", "seven digits", "used", "expired"])
def test_a_ticket_that_does_not_fit_this_run_sends_nothing_with_one_plain_reason(tmp_path, how):
    w = ticket_world(tmp_path / "w")
    _, number = _proposed(w, {"a": {"SPY": 10.0}})
    kw = {}
    if how == "another day":
        w.next_day()
        w.paper = paper({"a": {"SPY": 10.0}})
    elif how == "another account":
        w.venue.account = "Zq9OTHERACCOUNT1"
    elif how == "another venue":
        w.venue.name = "standin2-sandbox"                                    # the same files, another venue's name
    elif how == "another capital":
        kw["capital"] = 200_000.0
    elif how == "fractional":
        kw["fractional"] = True
    elif how == "wrong number":
        number = wrong_other_number(number)
    elif how == "not digits":
        number = "12a456"
    elif how == "five digits":
        number = number[:5]
    elif how == "seven digits":
        number = number + "1"
    elif how == "used":
        assert approve(w, number)["orders_sent"] == 1
    elif how == "expired":
        w.later(16)
    w.venue.calls.clear()
    submits = len(w.venue.submits())
    before = [(o["tag"], o["state"]) for o in w.orders()]
    out = approve(w, number, **kw)
    assert out["status"] == "stopped" and out["stage"] == "ticket" and out["orders_sent"] == 0
    assert isinstance(out["reason"], str) and len(out["reason"]) < 200 and "\n" not in out["reason"]
    assert len(w.venue.submits()) == submits and [(o["tag"], o["state"]) for o in w.orders()] == before
    assert "submit" not in w.venue.names() and "positions" not in w.venue.names()   # stopped before the account is read
    assert number not in out["reason"] and ACCOUNT not in json.dumps(out)


def test_an_approve_outside_the_session_reads_nothing(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    tw.clock.t = ny("2026-11-13 16:30")
    tw.venue.calls.clear()
    out = approve(tw, number)
    assert out["stage"] == "session" and tw.venue.calls == [] and ticket_rows(tw, "attempts") == []


def test_orders_not_on_the_ticket_are_never_sent(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    tw.paper = paper({"a": {"SPY": 10.0, "QQQ": 5.0, "IWM": 3.0}})
    out = approve(tw, number)
    assert [o["symbol"] for o in tw.orders()] == ["SPY"] and out["orders_sent"] == 1
    assert sorted(s["symbol"] for s in out["skipped"] if s["reason"] == "not on the ticket") == ["IWM", "QQQ"]


def _sent_model(lines, now_ok, moves, changed, halted, latched, reducing):
    """What may be sent: the ticket's lines whose wanted size is unchanged, whose quote moved 0.5% or less, while
    the ticket is live and nothing stops it."""
    if not now_ok or halted:
        return set()
    return {ln["fund"] for ln in lines if ln["fund"] not in changed and abs(moves.get(ln["fund"], 0.0)) <= 0.005 + 1e-12
            and (not latched or ln["fund"] in reducing)}


@pytest.mark.parametrize("seed_block", range(6))
def test_an_approve_run_sends_at_most_what_its_ticket_allows_over_generated_scenarios(tmp_path, seed_block):
    """50 scenarios per block, 300 in all: a ticket, then the world changes at random, then an approve (or two)."""
    full = partial = 0
    for k in range(50):
        rng = random.Random(130_000 + seed_block * 50 + k)
        w = ticket_world(tmp_path / f"a{seed_block}-{k}")
        held0 = {sym: float(rng.choice([0, 0, 2, 6])) for sym in ("SPY", "QQQ")}
        if any(held0.values()):                                            # some holdings of this program to sell
            w.paper = paper({"a": dict(held0)})
            w.run()
            for tag in w.venue.submits():
                w.venue.fill(tag)
            w.later(30)
        wanted = {"a": {sym: float(rng.choice([0, 1, 3, 7])) for sym in ("SPY", "QQQ", "IWM", "TLT")}}
        w.paper = paper(wanted)
        fractional = rng.random() < 0.2
        capital = rng.choice([None, None, 150_000.0])
        out = propose(w, fractional=fractional, capital=capital)
        lines = out["ticket"]["orders"]
        if not lines:
            continue
        number = out["ticket_number"]
        moves = {sym: rng.choice([0.0, 0.0, 0.003, -0.005, 0.0049, 0.0051, -0.01, 0.03]) for sym in ("SPY", "QQQ", "IWM", "TLT")}
        for sym, m in moves.items():
            w.prices[sym] = w.prices[sym] * (1 + m)
        changed = set()
        if rng.random() < 0.3:                                             # the paper book changes for one fund
            sym = rng.choice(["SPY", "QQQ", "IWM", "TLT"])
            wanted = {"a": {**wanted["a"], sym: wanted["a"][sym] + rng.choice([1.0, 2.0])}}   # a size that moves
            w.paper = paper(wanted)
            changed.add(sym)
        minutes = rng.choice([0, 1, 5, 14, 15, 16, 40])
        w.later(minutes)
        halted, latched = rng.random() < 0.1, rng.random() < 0.15
        if halted:
            w.halt = w.tmp / "halt.json"
            w.halt.write_text("{}")
        if latched:
            w.latch.write_text("{}")
        right = rng.random() < 0.85
        reducing = {ln["fund"] for ln in lines if ln["side"] == "sell"}
        allowed = _sent_model(lines, right and minutes <= 15, moves, changed, halted, latched, reducing)
        submits_before = len(w.venue.submits())
        out2 = approve(w, number if right else wrong_other_number(number), fractional=fractional, capital=capital)
        sent = [o for o in w.orders() if o["batch"] == out2["batch"] and o["state"] not in ("intended",)]
        assert len(w.venue.submits()) - submits_before == len(sent)
        units = {ln["fund"]: ln["units"] for ln in lines}
        for o in sent:
            assert o["symbol"] in allowed, (seed_block, k, o["symbol"], allowed, moves, changed, minutes, out2["ticket"])
            assert o["units"] == units[o["symbol"]]                              # the ticket's units, never more
        assert len({o["symbol"] for o in sent}) == len(sent)
        if allowed:
            assert {o["symbol"] for o in sent} == allowed                     # nothing else stood in the way here
            full += 1
        partial += len(allowed) < len(lines)
        if right:
            again = approve(w, number, fractional=fractional, capital=capital)    # never twice
            assert again["status"] == "stopped" and again["orders_sent"] == 0
    assert full > 10 and partial > 10, (full, partial)


# ---- invariant 3: a ticket is used once

def test_a_second_approve_of_the_same_number_sends_nothing(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    assert approve(tw, number)["orders_sent"] == 1
    tw.later(1)
    out = approve(tw, number)
    assert out["stage"] == "ticket" and "used" in out["reason"] and len(tw.venue.submits()) == 1


def test_the_ticket_is_marked_used_durably_before_the_first_intend(tw, monkeypatch):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    seen = []
    real = _Ledger.intend

    def intend(self, *a, **k):
        seen.append([r["fate"] for r in ticket_rows(tw, "fates")])       # read from the file by another connection
        return real(self, *a, **k)
    monkeypatch.setattr(_Ledger, "intend", intend)
    approve(tw, number)
    assert seen == [["used"]]


def test_a_kill_after_the_used_mark_and_before_intend_leaves_a_used_ticket_and_nothing_sent(tw, monkeypatch):
    _, number = _proposed(tw, {"a": {"SPY": 10.0, "QQQ": 1.0}})
    monkeypatch.setattr(_Ledger, "intend", lambda self, *a, **k: (_ for _ in ()).throw(Crash("killed")))
    with pytest.raises(Crash):
        approve(tw, number)
    monkeypatch.undo()
    assert tw.venue.submits() == [] and [r["fate"] for r in ticket_rows(tw, "fates")] == ["used"]
    out = approve(tw, number)
    assert out["stage"] == "ticket" and "used" in out["reason"] and tw.venue.submits() == []


def test_a_kill_between_intend_and_submit_sends_nothing_later_and_the_next_run_settles_it(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})

    def crash_at_submit(led):
        led.submit_once = lambda *a, **k: (_ for _ in ()).throw(Crash("killed"))
    with pytest.raises(Crash):
        tw.run(approve=number, patch=crash_at_submit)
    assert approve(tw, number)["stage"] == "ticket" and tw.venue.submits() == []
    tw.later(30)
    tw.paper = paper({})
    tw.run()                                                               # an ordinary run settles what was intended
    assert tw.venue.submits() == [] and [o["state"] for o in tw.orders()] == ["intended"]
    st = osx.OrderStepState(tw.state_path)
    try:
        assert st.sent_on("2026-11-13") == 0
    finally:
        st.close()


@pytest.mark.parametrize("seed_block", range(6))
def test_a_ticket_sends_each_order_at_most_once_whatever_kills_its_approve_runs(tmp_path, monkeypatch, seed_block):
    """50 scenarios per block, 300 in all: a ticket, an approve that dies at a random point, more approves."""
    box = {}
    _kill_points(monkeypatch, box)
    killed = reached = 0
    for k in range(50):
        rng = random.Random(140_000 + seed_block * 50 + k)
        w, _ = _random_world(tmp_path / f"u{seed_block}-{k}", rng)
        w.venue.nice = True
        w.venue.cash_value = 1e7
        _killing_quotes(w, box)
        w.paper = paper({"a": {sym: float(rng.choice([1, 3, 7])) for sym in ("SPY", "QQQ", "IWM")}})
        out = propose(w)
        number = out.get("ticket_number")
        if not number:
            continue
        lines = {ln["fund"]: ln["units"] for ln in out["ticket"]["orders"]}
        w.venue.nice = rng.random() < 0.5
        tags = []
        for attempt in range(3):
            w.later(rng.choice([0, 1, 2]))
            box["killer"] = w.venue.killer = Killer(rng.randint(1, 30) if attempt == 0 else rng.choice([None, None, rng.randint(1, 30)]))
            used_before = bool(ticket_rows(w, "fates"))
            submits_before = len(w.venue.submits())
            try:
                approve(w, number)
            except Crash:
                killed += 1
            finally:
                box["killer"] = w.venue.killer = None
            if used_before:                                                # a used ticket sends nothing new
                assert len(w.venue.submits()) == submits_before
            tags += w.venue.submits()[submits_before:]
            reached += bool(ticket_rows(w, "fates")) and not used_before
        sent = [t for t in tags]
        assert len(sent) == len(set(sent))
        symbols = [o["symbol"] for o in w.orders() if o["tag"] in set(sent)]
        assert len(symbols) == len(set(symbols)) and set(symbols) <= set(lines)   # each line at most once
        for o in w.orders():
            if o["tag"] in set(sent):
                assert o["units"] == lines[o["symbol"]]
        w.later(30)
        w.venue.nice = True
        box["phase"] = "ordinary"
        w.run()                                                            # intended orders are settled, never sent
        intended = {o["tag"] for o in w.orders() if o["state"] == "intended"}
        assert not (intended & set(w.venue.submits()))
        assert len(w.venue.taken) == len(set(w.venue.taken))
    assert killed > 30 and reached > 20, (killed, reached)


# ---- invariant 4: a number is not guessable into a send

def test_ticket_numbers_are_six_digits_from_the_systems_secure_random_source(monkeypatch):
    import secrets
    asked = []
    monkeypatch.setattr(secrets, "randbelow", lambda n: asked.append(n) or 42)
    assert osx.ticket_number() == "000042" and asked == [1_000_000]
    assert osx.secrets is secrets


def test_a_number_already_used_today_is_not_given_again(tw, monkeypatch):
    import secrets
    numbers = iter([7, 7, 7, 8])
    monkeypatch.setattr(secrets, "randbelow", lambda n: next(numbers))
    _, first = _proposed(tw, {"a": {"SPY": 10.0}})
    tw.later(1)
    _, second = _proposed(tw, {"a": {"SPY": 10.0}})
    assert (first, second) == ("000007", "000008")


def test_three_wrong_numbers_in_a_new_york_day_lock_approval_until_the_next_propose(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    for _ in range(3):
        out = approve(tw, wrong_other_number(number))
        assert out["stage"] == "ticket" and "no ticket" in out["reason"]
    out = approve(tw, number)                                              # the right number, now locked
    assert out["stage"] == "ticket" and "locked" in out["reason"] and tw.venue.submits() == []
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})                       # a new propose lifts the lock
    assert approve(tw, number)["orders_sent"] == 1
    assert [r["verdict"] for r in ticket_rows(tw, "verdicts")].count("refused") == 4   # rev 2: verdicts, apart


def test_wrong_numbers_of_an_earlier_day_do_not_count_today(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    for _ in range(3):
        approve(tw, wrong_other_number(number))
    assert "locked" in approve(tw, number)["reason"]
    tw.next_day()
    tw.paper = paper({"a": {"SPY": 10.0}})
    out = approve(tw, number)                                              # a new day, no propose: not locked
    assert out["stage"] == "ticket" and "locked" not in out["reason"] and "another day" in out["reason"]


def test_a_wrong_number_after_three_today_but_before_any_propose_stays_locked(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    for n in ("000000", "abcdef", ""):
        approve(tw, n if n != number else "999999")
    tw.later(10)
    assert "locked" in approve(tw, number)["reason"]


# ---- invariant 5: tickets are a record, append-only, in their own file

def test_tickets_their_lines_and_what_became_of_each_are_kept_in_their_own_file(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0, "QQQ": 4.0}})
    tw.prices["QQQ"] = 420.0
    out = approve(tw, number)
    path = osx.ticket_path_for(tw.state_path)
    assert Path(path).name == "order_step-standin-sandbox.tickets.sqlite" and Path(path).parent == tw.state_path.parent
    (ticket,) = ticket_rows(tw, "tickets")
    assert ticket["number"] == number and ticket["venue"] == "standin-sandbox" and ticket["capital"] == 100_000.0
    assert ticket["expires"] - ticket["created"] == osx.TICKET_LIFE_SECONDS and ticket["day"] == "2026-11-13"
    assert [(r["line"], r["symbol"], r["side"], r["units"]) for r in ticket_rows(tw, "lines")] == [
        (0, "SPY", "buy", 10.0), (1, "QQQ", "buy", 4.0)]
    rows = {r["line"]: r for r in ticket_rows(tw, "outcomes")}
    assert rows[0]["outcome"] == "sent" and rows[0]["tag"] == out["ticket"]["orders"][0]["tag"]
    assert rows[1]["outcome"] == "not sent" and "0.5%" in rows[1]["reason"] and rows[1]["tag"] is None   # rev 2
    assert [r["fate"] for r in ticket_rows(tw, "fates")] == ["used"]


@pytest.mark.parametrize("statement", [
    "UPDATE tickets SET expires = expires + 3600", "DELETE FROM tickets", "UPDATE lines SET units = 99",
    "DELETE FROM lines", "DELETE FROM fates", "UPDATE fates SET fate = 'expired'", "DELETE FROM outcomes",
    "UPDATE outcomes SET outcome = 'dropped'", "DELETE FROM attempts", "UPDATE meta SET value = 'x'", "DELETE FROM meta",
    "INSERT OR REPLACE INTO fates (day, number, fate, time, batch) SELECT day, number, 'used', 0, 'x' FROM fates",
    "INSERT OR REPLACE INTO tickets SELECT * FROM tickets",
    "INSERT OR REPLACE INTO meta (key, value) SELECT key, 'other' FROM meta"])
def test_the_ticket_file_refuses_every_update_delete_and_replacement(tw, statement):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    approve(tw, wrong_other_number(number))
    approve(tw, number)
    path = osx.ticket_path_for(tw.state_path)
    con = sqlite3.connect(path)
    try:
        before = {t: con.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall()
                  for t in ("tickets", "lines", "fates", "outcomes", "attempts", "meta")}
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(statement)
        con.rollback()
        assert before == {t: con.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in before}
    finally:
        con.close()


def test_the_ticket_file_refuses_a_file_that_is_not_its_own(tmp_path):
    other = tmp_path / "other.sqlite"
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE something (x)")
    con.commit()
    con.close()
    with pytest.raises(osx.OrderStepError):
        osx.TicketBook(other)
    state = tmp_path / "state.sqlite"
    osx.OrderStepState(state).close()
    with pytest.raises(osx.OrderStepError):
        osx.TicketBook(state)                                              # the state file is not a ticket file
    book = osx.TicketBook(tmp_path / "t.sqlite")
    book.close()
    assert osx.TicketBook(tmp_path / "t.sqlite").layout() == osx.TICKET_LAYOUT


@pytest.fixture(scope="module")
def base_osx(tmp_path_factory):
    """The order step of T22a's base commit, from git, loaded under its own name."""
    root = Path(__file__).resolve().parents[2]
    src = subprocess.run(["git", "-C", str(root), "show", f"{BASE_COMMIT}:valedia_trader/execution_and_risk/order_step.py"],
                         capture_output=True, check=True).stdout
    folder = tmp_path_factory.mktemp("base_order_step")
    path = folder / "order_step_t22a_base.py"
    path.write_bytes(src)
    spec = importlib.util.spec_from_file_location("order_step_t22a_base", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["order_step_t22a_base"] = mod
    spec.loader.exec_module(mod)
    assert not hasattr(mod, "TicketBook")
    return mod


def _dump(path):
    if not Path(path).exists():
        return None
    con = sqlite3.connect(path)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        return {"version": con.execute("PRAGMA user_version").fetchone()[0],
                "schema": sorted(r[0] for r in con.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")),
                "rows": {t: con.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}}
    finally:
        con.close()


def test_the_state_files_layout_is_unchanged_and_the_base_reads_and_continues_it(tmp_path, base_osx):
    w = ticket_world(tmp_path / "w")
    w.paper = paper({"a": {"SPY": 2.0}})
    _run_with(base_osx, w)                                                 # a state file written by the base ...
    w.later(5)
    _, number = _proposed(w, {"a": {"SPY": 10.0}})                        # ... continued by tickets
    approve(w, number)
    assert _dump(w.state_path)["version"] == 1
    assert set(_dump(w.state_path)["rows"]) <= {"meta", "baseline", "equity", "sends", "sqlite_sequence"}
    st = base_osx.OrderStepState(w.state_path)                             # ... and read by the base again
    st.close()
    w.later(30)
    w.paper = paper({"a": {"SPY": 12.0}})
    out = _run_with(base_osx, w)
    assert out["status"] == "done" and out["orders_sent"] == 1


# ---- invariant 6: nothing secret or unexpected gets out

def test_no_account_id_reaches_the_ticket_the_summary_the_journal_or_any_file(tw, capsys):
    outs = []
    out, number = _proposed(tw, {"a": {"SPY": 10.0, "QQQ": 4.0}})
    outs.append(out)
    outs.append(approve(tw, wrong_other_number(number)))
    tw.later(1)
    outs.append(approve(tw, number))
    outs.append(approve(tw, number))
    text = _all_text(tw, outs)
    assert ACCOUNT not in text and ACCOUNT[-8:] not in text
    (ticket,) = ticket_rows(tw, "tickets")
    assert len(ticket["account"]) == 64 and ticket["account"] != hashlib.sha256(ACCOUNT.encode()).hexdigest()   # keyed


def test_another_ticket_file_keys_the_same_account_differently(tmp_path):
    a, b = osx.TicketBook(tmp_path / "a.sqlite"), osx.TicketBook(tmp_path / "b.sqlite")
    try:
        assert a.account_hash(ACCOUNT) != b.account_hash(ACCOUNT) and a.account_hash(ACCOUNT) == a.account_hash(ACCOUNT)
    finally:
        a.close()
        b.close()


class LeakyBooked(Leaky):
    account = ACCOUNT

    @property
    def account_id(self):
        if isinstance(self.account, BaseException):
            raise self.account
        return self.account


def test_nothing_the_venue_says_reaches_a_ticket_or_its_summaries(tmp_path):
    outs, worlds = [], []
    w = ticket_world(tmp_path / "w", LeakyBooked())
    worlds.append(w)
    out, number = _proposed(w, {"a": {"SPY": 1.0, "QQQ": 1.0, "IWM": 1.0, "TLT": 1.0}})
    outs.append(out)
    w.venue.submit_mode = ["accept", "refuse", "reject", "reject marked code"]
    outs.append(approve(w, number))
    w2 = ticket_world(tmp_path / "w2", LeakyBooked())
    worlds.append(w2)
    w2.venue.account = ConnectionError(f"account list failed: {MARK} {ACCOUNT}")
    w2.paper = paper({"a": {"SPY": 1.0}})
    outs.append(propose(w2))
    assert outs[-1]["stage"] == "ticket" and "ConnectionError" in outs[-1]["reason"]
    w3 = ticket_world(tmp_path / "w3", LeakyBooked())
    worlds.append(w3)
    w3.venue.account = MARK                                                 # not a plain id
    w3.paper = paper({"a": {"SPY": 1.0}})
    outs.append(propose(w3))
    assert outs[-1]["stage"] == "ticket"
    text = "\n".join(_all_text(x, outs) for x in worlds)
    assert MARK not in text and "leak" not in text and ACCOUNT not in text
    assert "SOME_CODE" in text and "NotTransmitted" in text


# ---- invariant 7: the scheduled paper path is untouched, byte for byte

def test_an_ordinary_run_reads_no_account_and_makes_no_ticket_file(tw):
    tw.paper = paper({"a": {"SPY": 10.0}})
    out = tw.run()
    assert "account_id" not in tw.venue.names() and not Path(osx.ticket_path_for(tw.state_path)).exists()
    assert "mode" not in out and "ticket" not in out and "dropped" not in out


def _run_with(mod, w, **kw):
    led = mod.open_ledger(w.ledger_path, clock=w.clock)
    book = w.paper
    if isinstance(book, osx.PaperTargets):
        book = mod.PaperTargets(**dataclasses.asdict(book))
        if book.day == AUTO:
            prev = CAL.sessions[CAL.sessions.searchsorted(ny_day(w.clock.t)) - 1]
            book = dataclasses.replace(book, day=str(prev.date()))
    try:
        return mod.run_order_step(ledger=led, venue=w.venue, paper=book, quotes=w.quotes, journal=w.journal,
                                  limits_file=w.limits, state=w.state_path, clock=w.clock, calendar=CAL,
                                  halt_file=w.halt, latch_file=w.latch, **kw)
    finally:
        led.close()


_STAMP = re.compile(rb'"timestamp": "[^"]*"')


def _scenario(mod, tmp, seed, monkeypatch):
    """One generated scenario driven through `mod`: what the venue, the quote source, the journal, the ledger and
    the state file saw, run by run."""
    rng = random.Random(seed)
    urandom = random.Random(seed + 1)
    monkeypatch.setattr("os.urandom", lambda n: bytes(urandom.getrandbits(8) for _ in range(n)))
    held = {"OWN": float(rng.randint(0, 5)), "SPY": float(rng.choice([0, 0, 3]))}
    venue = KillVenue(rng, {s: u for s, u in held.items() if u}, cash=rng.choice([5_000.0, 50_000.0, 1e7]))
    w = ticket_world(tmp, venue)
    w.limits.write_text(json.dumps({**GENEROUS, "max_orders_per_day": rng.choice([1, 2, 3, 40])}))
    kw = {}
    if rng.random() < 0.3:
        kw["fractional"] = True
    if rng.random() < 0.3:
        kw["capital"] = rng.choice([50_000.0, 250_000.0])
    seen = []
    for day in range(3):
        w.venue.nice = day == 2
        w.paper = _random_paper(rng)
        for sym in ("SPY", "QQQ", "IWM"):
            w.prices[sym] = rng.choice([20.0, 55.5, 101.25])
            w.quote_age[sym] = rng.choice([60.0, 60.0, 1500.0])
        if rng.random() < 0.1:
            w.latch.write_text("{}")
        elif w.latch.exists():
            w.latch.unlink()
        for run in range(rng.randint(1, 2)):
            if run:
                w.later(rng.choice([1, 30]))
            w.venue.calls.clear()
            w.venue.killer = Killer(rng.choice([None, None, None, rng.randint(1, 25)]))
            try:
                out = _run_with(mod, w, **kw)
            except Crash as crash:
                out = {"crashed": str(crash)}
            w.venue.killer = None
            seen.append({"calls": list(w.venue.calls), "summary": json.dumps(out, sort_keys=True, default=str),
                         "journal": {f.name: _STAMP.sub(b'"timestamp": "T"', f.read_bytes())    # the journal's wall clock
                                     for f in sorted((w.tmp / "journal").glob("*"))},
                         "state": _dump(w.state_path), "ledger": _dump(w.ledger_path),
                         "files": sorted(str(p.relative_to(w.tmp)) for p in w.tmp.rglob("*") if p.is_file())})
            w.venue.market()
        w.next_day()
    monkeypatch.undo()
    return seen


@pytest.mark.parametrize("seed_block", range(6))
def test_a_run_without_the_new_flags_is_the_base_byte_for_byte_over_generated_scenarios(tmp_path, monkeypatch, base_osx,
                                                                                        seed_block):
    """50 scenarios per block, 300 in all, each driven through the base and through this revision."""
    sends = crashes = 0
    for k in range(50):
        seed = 150_000 + seed_block * 50 + k
        base = _scenario(base_osx, tmp_path / f"base{k}", seed, monkeypatch)
        mine = _scenario(osx, tmp_path / f"mine{k}", seed, monkeypatch)
        assert len(base) == len(mine)
        for i, (b, m) in enumerate(zip(base, mine)):                    # T34b-2: equal but for `faults` (F1, F2)
            added = _t34_same_but(b, m, T34B2_PRACTICE_KEYS, (seed, i))
            if _t34_practice_marks(added, (seed, i)):
                print(f"T34B2-MARKED {seed} run {i}: {added.get('faults')} pending {added.get('reconcile_pending')}")
        sends += sum(1 for r in mine for c in r["calls"] if c[0] == "submit")
        crashes += sum(1 for r in mine if "crashed" in r["summary"])
    assert sends > 50 and crashes > 5


# ==== T22a rev 2: the record adds up
#
# A line of a used ticket ends as exactly one of: sent (its order reached the venue), not sent (dropped before intend,
# or intended and never transmitted), left over (the run ended before its intend); "unknown" (intended, fate not yet
# known) is no row and never final. Every later run that settles sends completes the record from the order ledger.

import os as _os
import select as _select
import signal as _signal
import time as _time
import traceback as _traceback


def _rows(w, sql, args=()):
    con = sqlite3.connect(osx.ticket_path_for(w.state_path))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def _final(w, number):
    """{line: (outcome, tag)} of a ticket's final outcomes in the ticket file."""
    return {r["line"]: (r["outcome"], r["tag"]) for r in _rows(w, "SELECT * FROM outcomes WHERE number = ? ORDER BY rowid",
                                                                 (number,))}   # the latest row of each line


def _tags(w, number):
    return {r["line"]: r["tag"] for r in _rows(w, "SELECT * FROM tags WHERE number = ?", (number,))}


# ---- outcome 1: the record never says something false

def _answer_not_written(led):
    """The ledger cannot write the venue's acceptance: submit_once has placed the order and then raises."""
    real = led._cas

    def cas(tag, old, new, *a, **k):
        if old == "sent" and new == "accepted":
            raise sqlite3.OperationalError("database is locked")
        return real(tag, old, new, *a, **k)
    led._cas = cas


def test_an_order_placed_when_its_answer_cannot_be_written_is_unknown_never_left_over_then_sent(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0, "QQQ": 1.0}})
    out = tw.run(approve=number, patch=_answer_not_written)
    placed = tw.venue.submits()
    assert out["status"] == "stopped" and out["stage"] == "send" and len(placed) == 1    # SPY is at the venue
    by = {o["fund"]: o for o in out["ticket"]["orders"]}
    assert by["SPY"]["outcome"] == "unknown" and by["QQQ"]["outcome"] == "left over"
    final = _final(tw, number)
    assert 0 not in final and final[1][0] == "left over"                                  # SPY: no false final word
    assert _tags(tw, number)[0] == placed[0]
    tw.later(5)
    tw.paper = paper({"a": {"SPY": 10.0}})
    assert tw.run()["status"] == "done"                                                   # an ordinary run completes it
    assert _final(tw, number)[0] == ("sent", placed[0])


def test_an_order_the_venue_took_before_the_process_died_is_completed_as_sent_by_the_next_propose(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    real = tw.venue.submit

    def submit(*a, **k):
        real(*a, **k)
        raise Crash("killed after the venue took it")
    tw.venue.submit = submit
    with pytest.raises(Crash):
        approve(tw, number)
    tw.venue.submit = real
    assert _final(tw, number) == {} and len(tw.venue.submits()) == 1
    tw.later(5)
    assert propose(tw)["status"] == "done"
    assert _final(tw, number) == {0: ("sent", tw.venue.submits()[0])}


def test_an_intended_order_never_transmitted_is_completed_as_not_sent(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})

    def crash_at_submit(led):
        led.submit_once = lambda *a, **k: (_ for _ in ()).throw(Crash("killed"))
    with pytest.raises(Crash):
        tw.run(approve=number, patch=crash_at_submit)
    assert _final(tw, number) == {} and _tags(tw, number) == {0: tw.orders()[0]["tag"]}
    tw.later(5)
    tw.run()
    (outcome, tag), = _final(tw, number).values()
    assert outcome == "not sent" and tag == tw.orders()[0]["tag"] and tag not in tw.venue.submits()   # the ordinary
    # run may send its own order for SPY; the ticket's never reached the venue


def test_a_tag_is_recorded_before_its_intend(tw, monkeypatch):
    _, number = _proposed(tw, {"a": {"SPY": 10.0, "QQQ": 1.0}})
    seen = []
    real = _Ledger.intend

    def intend(self, tag, *a, **k):
        seen.append((tag, sorted(_tags(tw, number).values())))
        return real(self, tag, *a, **k)
    monkeypatch.setattr(_Ledger, "intend", intend)
    approve(tw, number)
    assert len(seen) == 2 and all(tag in recorded for tag, recorded in seen)


def test_a_left_over_line_never_has_a_tag_and_a_sent_line_always_has_its_own(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    approve(tw, number)
    con = sqlite3.connect(osx.ticket_path_for(tw.state_path))
    try:
        (day,) = con.execute("SELECT day FROM tickets").fetchone()
        with pytest.raises(sqlite3.DatabaseError):                          # the file itself refuses a false word
            con.execute("INSERT INTO outcomes (day, number, line, outcome, tag, reason, time, batch) VALUES "
                        "(?, ?, 7, 'sent', 'no-such-tag', NULL, 0, 'x')", (day, number))
    finally:
        con.close()


# ---- outcomes 1 and 2: real kills (SIGKILL of a forked child at a random point), each followed by one complete run

class FileVenue:
    """A stand-in venue whose whole truth is a sqlite file, so it outlives a SIGKILL of the process that called it.
    Its `book` table is the venue's own log: every order it holds, by tag."""
    environment = "sandbox"
    name = "filevenue-sandbox"

    def __init__(self, path, held=None, cash=1e7):
        self.path, self.calls = str(path), []
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        if not Path(self.path).exists():
            con = self._con()
            con.executescript("CREATE TABLE held (symbol TEXT PRIMARY KEY, units REAL NOT NULL);"
                              "CREATE TABLE money (id INTEGER PRIMARY KEY, cash REAL NOT NULL);"
                              "CREATE TABLE book (tag TEXT PRIMARY KEY, symbol TEXT, units REAL, limit_price REAL, "
                              "filled REAL, state TEXT);")
            con.execute("INSERT INTO money VALUES (1, ?)", (float(cash),))
            con.executemany("INSERT INTO held VALUES (?, ?)", [(s, float(u)) for s, u in (held or {}).items() if u])
            con.close()

    def _con(self):
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        con.execute("PRAGMA synchronous = FULL")
        return con

    @property
    def account_id(self):
        return ACCOUNT

    def positions(self):
        con = self._con()
        try:
            return {s: u for s, u in con.execute("SELECT symbol, units FROM held") if abs(u) > 1e-12}
        finally:
            con.close()

    def cash(self):
        con = self._con()
        try:
            return {"cash": con.execute("SELECT cash FROM money").fetchone()[0], "settled_cash": None}
        finally:
            con.close()

    def submit(self, symbol, units, tag, price=None, limit_price=None, session="CORE"):
        con = self._con()
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute("INSERT INTO book VALUES (?, ?, ?, ?, 0, 'open')", (tag, symbol, float(units), limit_price))
            if int(hashlib.sha256(tag.encode()).hexdigest(), 16) % 3:          # most orders fill at once
                con.execute("UPDATE book SET filled = ?, state = 'filled' WHERE tag = ?", (abs(float(units)), tag))
                con.execute("INSERT INTO held VALUES (?, ?) ON CONFLICT(symbol) DO UPDATE SET units = units + ?",
                            (symbol, float(units), float(units)))
                con.execute("UPDATE money SET cash = cash - ?", (float(units) * limit_price,))
            con.execute("COMMIT")
        finally:
            con.close()
        return {"status": "accepted", "venue_order_id": "V" + tag[-6:]}

    def order_status(self, tag):
        con = self._con()
        try:
            row = con.execute("SELECT state, filled, limit_price FROM book WHERE tag = ?", (tag,)).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return {"state": row[0], "filled_units": row[1], "avg_price": row[2] if row[1] else None, "fee": 0.0,
                "venue_order_id": "V" + tag[-6:], "raw_status": None}

    def cancel(self, tag):
        con = self._con()
        try:
            row = con.execute("SELECT state FROM book WHERE tag = ?", (tag,)).fetchone()
            if row is None:
                return {"status": "no such order"}
            if row[0] == "open":
                con.execute("UPDATE book SET state = 'cancelled' WHERE tag = ?", (tag,))
                return {"status": "cancel requested"}
            return {"status": "already finished"}
        finally:
            con.close()

    def book_tags(self):
        con = self._con()
        try:
            return [r[0] for r in con.execute("SELECT tag FROM book")]
        finally:
            con.close()

    def submits(self):
        return self.book_tags()


def _instrument(point):
    """In the child only: every venue call, quote, ledger write, state write, ticket write and journal write, before
    and after, is a point."""
    from valedia_trader.research_loop.paper import Journal as _Journal

    def wrap(cls, name):
        real = getattr(cls, name)
        if isinstance(real, property):
            return

        def method(self, *a, **k):
            point(f"before {cls.__name__}.{name}")
            out = real(self, *a, **k)
            point(f"after {cls.__name__}.{name}")
            return out
        setattr(cls, name, method)
    for name in ("positions", "cash", "submit", "order_status", "cancel"):
        wrap(FileVenue, name)
    for name in ("intend", "submit_once", "reconcile", "mark_applied"):
        wrap(_Ledger, name)
    for name in ("record_equity", "reprice_equity", "note_sending", "note_outcome", "settle_sends", "record_baseline"):
        wrap(osx.OrderStepState, name)
    for name in ("create", "mark_used", "mark_expired", "note_outcome", "note_attempt", "note_tag", "note_verdict",
                 "settle"):
        if hasattr(osx.TicketBook, name):
            wrap(osx.TicketBook, name)
    wrap(_Journal, "write")


def _killed(kill_at, run, timeout=120.0):
    """Run `run()` in a forked child. At its kill_at-th point the child says so and waits; this process then sends
    it SIGKILL, by its process id. Returns True when it was killed, False when it finished first."""
    r, wfd = _os.pipe()
    pid = _os.fork()
    if pid == 0:                                                           # the child
        _os.close(r)
        try:
            count = [0]

            def point(where):
                count[0] += 1
                if count[0] == kill_at:
                    _os.write(wfd, b"K")
                    _time.sleep(timeout * 2)
            _instrument(point)
            run()
            _os.write(wfd, b"D")
        except BaseException:
            _os.write(wfd, b"E" + _traceback.format_exc().encode()[-3000:])
        finally:
            _os._exit(0)
    _os.close(wfd)
    data, killed = b"", False
    try:
        while True:
            ready, _, _ = _select.select([r], [], [], timeout)
            if not ready:
                _os.kill(pid, _signal.SIGKILL)
                raise AssertionError("the child hung")
            chunk = _os.read(r, 4096)
            if not chunk:
                break
            data += chunk
            if data[:1] == b"K":
                _os.kill(pid, _signal.SIGKILL)                             # our own child, by its process id
                killed = True
                break
    finally:
        _os.waitpid(pid, 0)
        _os.close(r)
    if data[:1] == b"E":
        raise AssertionError(data[1:].decode("utf-8", "replace"))
    return killed


def _record_adds_up(w):
    """Every line of every used ticket has exactly one final outcome; its sent lines are, order for order, what the
    venue holds under the batch that used it."""
    held = set(w.venue.book_tags())
    used = _rows(w, "SELECT day, number, batch FROM fates WHERE fate = 'used'")
    for t in used:
        lines = {r["line"] for r in _rows(w, "SELECT line FROM lines WHERE day = ? AND number = ?", (t["day"], t["number"]))}
        final = {r["line"]: r for r in _rows(w, "SELECT * FROM outcomes WHERE day = ? AND number = ? ORDER BY rowid",
                                             (t["day"], t["number"]))}               # the latest row of each line
        assert set(final) == lines, (t, lines, final)
        sent = {r["tag"] for r in final.values() if r["outcome"] == "sent"}
        assert sent == {tag for tag in held if tag.startswith(t["batch"] + "-")}, (t, sent, held)
        tags = {r["line"]: r["tag"] for r in _rows(w, "SELECT * FROM tags WHERE day = ? AND number = ?", (t["day"], t["number"]))}
        for line, r in final.items():
            if r["outcome"] == "left over":
                assert line not in tags
            if r["outcome"] == "sent":
                assert tags[line] == r["tag"]
    return len(used)


@pytest.mark.parametrize("seed_block", range(6))
def test_after_real_kills_of_approve_runs_and_one_complete_run_the_record_adds_up(tmp_path, seed_block):
    """At least 50 SIGKILLs per block, 300 in all, at random points of approve runs (one or two in a row), each
    sequence followed by one complete run of a random kind; checked against the venue's own log."""
    kills = scenarios = checked = 0
    while kills < 50:
        scenarios += 1
        assert scenarios < 400
        rng = random.Random(160_000 + seed_block * 1000 + scenarios)
        w = ticket_world(tmp_path / f"k{scenarios}", FileVenue(tmp_path / f"k{scenarios}" / "venue.sqlite",
                                                                held={"OWN": float(rng.randint(0, 3))}))
        wanted = {"a": {sym: float(rng.choice([1, 2, 5])) for sym in ("SPY", "QQQ", "IWM", "TLT")}}
        w.paper = paper(wanted)
        if rng.random() < 0.3:                                             # some holdings first, some to sell
            w.run()
            w.later(30)
            w.paper = paper({"a": {**wanted["a"], "SPY": 0.0}})
        out = propose(w)
        number = out.get("ticket_number")
        if not number:
            continue
        for _ in range(rng.choice([1, 1, 2])):
            w.later(rng.choice([0, 1]))
            kills += _killed(rng.randint(1, 140), lambda: approve(w, number))
        w.later(rng.choice([3, 5]))
        kind = rng.choice(["ordinary", "propose", "propose and approve"])
        for _ in range(3):                                                 # one complete run of that kind
            if kind == "ordinary":
                out = w.run()
            else:
                out = propose(w)
                if kind == "propose and approve" and out.get("ticket_number"):
                    out = approve(w, out["ticket_number"])
            if out["status"] == "done":
                break
            w.later(3)
        assert out["status"] == "done", out
        checked += _record_adds_up(w)
    assert checked > 50


# ---- outcome 3: the ticket file is what it claims to be

@pytest.mark.parametrize("how", ["drop trigger then delete", "altered trigger", "extra table", "renamed table",
                                 "missing trigger"])
def test_a_ticket_file_whose_tables_or_triggers_are_not_the_expected_ones_is_refused(tw, how):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    assert approve(tw, number)["orders_sent"] == 1
    con = sqlite3.connect(osx.ticket_path_for(tw.state_path))
    try:
        if how == "drop trigger then delete":
            con.execute("DROP TRIGGER fates_no_delete")
            con.execute("DELETE FROM fates")
        elif how == "altered trigger":
            con.execute("DROP TRIGGER fates_no_delete")
            con.execute("CREATE TRIGGER fates_no_delete BEFORE DELETE ON fates WHEN 0 BEGIN SELECT RAISE(ABORT, 'x'); END")
        elif how == "extra table":
            con.execute("CREATE TABLE notes (x)")
        elif how == "renamed table":
            con.execute("ALTER TABLE attempts RENAME TO attempts_old")
        elif how == "missing trigger":
            con.execute("DROP TRIGGER lines_no_update")
        con.commit()
    finally:
        con.close()
    submits = len(tw.venue.submits())
    tw.later(1)
    out = approve(tw, number)
    assert out["status"] == "stopped" and out["stage"] == "ticket" and len(tw.venue.submits()) == submits
    assert "ticket file" in out["reason"] and "\n" not in out["reason"]
    tw.later(30)
    tw.paper = paper({"a": {"SPY": 10.0}})
    out = tw.run()                                                         # an ordinary run is not stopped by it
    assert out["status"] == "done" and tw.entries("tickets_not_settled")


# ---- outcome 4: one clock reading decides the last look

class ReadingClock(Clock):
    """A clock that, once armed, moves on by `step` seconds after each reading, and keeps every reading."""

    def __init__(self, t):
        super().__init__(t)
        self.step, self.readings = 0.0, []

    def __call__(self):
        now = self.t
        self.readings.append(now)
        self.t += self.step
        return now


@pytest.mark.parametrize("start", [-160, -100, -60, -30, -10, 0])
@pytest.mark.parametrize("step", [20.0, 50.0, 450.0])
def test_no_order_is_sent_on_a_reading_past_the_tickets_expiry(tw, start, step):
    tw.clock = ReadingClock(tw.clock.t)
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    (ticket,) = ticket_rows(tw, "tickets")
    tw.clock.t = ticket["expires"] - 300
    box = {}

    def patch(led):
        real_intend, real_submit = led.intend, led.submit_once

        def intend(*a, **k):
            rec = real_intend(*a, **k)
            tw.clock.t, tw.clock.step = ticket["expires"] + start, step
            box["from"] = len(tw.clock.readings)
            return rec

        def submit_once(*a, **k):
            box["checks"] = tw.clock.readings[box["from"]:]               # every reading between intend and here
            return real_submit(*a, **k)
        led.intend, led.submit_once = intend, submit_once
    approve(tw, number, patch=patch)
    if tw.venue.submits():
        assert box["checks"] and max(box["checks"]) <= ticket["expires"], box
    else:
        assert [o["state"] for o in tw.orders()] == ["intended"]


# ---- outcome 5: a refusal that cannot be recorded is itself the refusal

@pytest.mark.parametrize("what", ["file", "folder"])
def test_with_a_ticket_file_that_cannot_be_written_a_right_number_looks_like_a_wrong_one(tmp_path, what):
    words = []
    for which in ("right", "wrong"):
        w = ticket_world(tmp_path / f"{what}-{which}")
        _, number = _proposed(w, {"a": {"SPY": 10.0}})
        path = Path(osx.ticket_path_for(w.state_path))
        target = path if what == "file" else path.parent
        mode = target.stat().st_mode
        target.chmod(0o444 if what == "file" else 0o555)
        try:
            out = approve(w, number if which == "right" else wrong_other_number(number))
        finally:
            target.chmod(mode)
        assert out["status"] == "stopped" and out["stage"] == "ticket" and w.venue.submits() == []
        words.append((out["stage"], out["reason"], out["ticket"]))
    assert words[0] == words[1]


def test_a_failed_approval_is_counted_before_its_number_is_compared(tw, monkeypatch):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    real = osx.TicketBook.note_verdict
    monkeypatch.setattr(osx.TicketBook, "note_verdict", lambda self, *a, **k: (_ for _ in ()).throw(Crash("killed")))
    for _ in range(3):                                                     # three runs die before their verdict
        with pytest.raises(Crash):
            approve(tw, wrong_other_number(number))
    monkeypatch.setattr(osx.TicketBook, "note_verdict", real)
    out = approve(tw, number)
    assert "locked" in out["reason"] and tw.venue.submits() == []


# ---- outcome 6: the life of a ticket is enforced where it is used

def test_a_propose_with_a_ticket_life_above_an_hour_writes_no_ticket(tw, monkeypatch):
    monkeypatch.setattr(osx, "TICKET_LIFE_SECONDS", 2 * 3600)
    tw.paper = paper({"a": {"SPY": 10.0}})
    out = propose(tw)
    assert out["status"] == "stopped" and out["stage"] == "ticket" and ticket_rows(tw, "tickets") == []


def test_an_approve_judges_the_life_by_the_constant_as_it_is_now(tw, monkeypatch):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    monkeypatch.setattr(osx, "TICKET_LIFE_SECONDS", 60)
    tw.later(2)
    out = approve(tw, number)
    assert out["stage"] == "ticket" and "expired" in out["reason"] and tw.venue.submits() == []


def test_a_stored_expiry_beyond_an_hour_is_cut_to_an_hour(tw, monkeypatch):
    w = tw
    monkeypatch.setattr(osx, "TICKET_LIFE_SECONDS", 7200)   # only the hour bound can cut a two-hour expiry
    w.paper = paper({"a": {"SPY": 10.0}})
    book = osx.TicketBook(osx.ticket_path_for(w.state_path))
    try:
        now = w.clock.t
        ticket = {"day": "2026-11-13", "number": "424242", "created": now, "expires": now + 7200, "venue": w.venue.name,
                  "environment": "sandbox", "account": book.account_hash(ACCOUNT), "capital": 100_000.0, "fractional": 0,
                  "equity": 1e6, "cash": 1e6, "settled_cash": None, "batch": "os-x"}
        book.create(ticket, [{"line": 0, "fund": "SPY", "side": "buy", "units": 10.0, "quote": 500.0, "limit": 505.0,
                              "value": 5000.0}])
    finally:
        book.close()
    w.later(61)
    out = approve(w, "424242")
    assert out["stage"] == "ticket" and "expired" in out["reason"] and w.venue.submits() == []


def test_the_life_bound_is_an_ordinary_check_not_an_assert():
    import inspect as _inspect
    assert "assert " not in _inspect.getsource(osx).split("class OrderStepError")[0]


# ---- outcome 7: the rulings

def _crafted(w, number="515151", **over):
    book = osx.TicketBook(osx.ticket_path_for(w.state_path))
    try:
        now = w.clock.t
        ticket = {"day": "2026-11-13", "number": number, "created": now, "expires": now + 900, "venue": w.venue.name,
                  "environment": "sandbox", "account": book.account_hash(ACCOUNT), "capital": 100_000.0, "fractional": 0,
                  "equity": 1e6, "cash": 1e6, "settled_cash": None, "batch": "os-crafted"}
        ticket.update(over)
        if "account_id" in over:
            ticket["account"] = book.account_hash(ticket.pop("account_id"))
        book.create(ticket, [{"line": 0, "fund": "SPY", "side": "buy", "units": 10.0, "quote": 500.0, "limit": 505.0,
                              "value": 5000.0}])
    finally:
        book.close()
    return number


def test_the_ticket_records_the_environment_and_approve_checks_it(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    assert ticket_rows(tw, "tickets")[0]["environment"] == "sandbox"
    other = _crafted(tw, environment="production")
    out = approve(tw, other)
    assert out["stage"] == "ticket" and "environment" in out["reason"] and tw.venue.submits() == []


def test_only_the_latest_ticket_is_live(tw):
    _, first = _proposed(tw, {"a": {"SPY": 10.0}})
    tw.later(1)
    _, second = _proposed(tw, {"a": {"SPY": 10.0}})
    out = approve(tw, first)
    assert out["stage"] == "ticket" and "superseded" in out["reason"] and tw.venue.submits() == []
    fates = {r["number"]: r["fate"] for r in ticket_rows(tw, "fates")}
    assert fates == {first: "superseded"}
    assert [r["outcome"] for r in _rows(tw, "SELECT * FROM outcomes WHERE number = ?", (first,))] == ["not sent"]
    assert approve(tw, second)["orders_sent"] == 1


def test_a_ticket_of_another_account_is_not_superseded(tw):
    other = _crafted(tw, account_id="Zq9SOMEONEELSE00")
    _proposed(tw, {"a": {"SPY": 10.0}})
    assert other not in {r["number"] for r in ticket_rows(tw, "fates")}


@pytest.mark.parametrize("why", ["bad clock", "environment", "lock taken", "session", "ticket", "done"])
@pytest.mark.parametrize("flag", ["propose", "approve"])
def test_every_summary_of_a_propose_or_approve_run_says_its_mode(tw, why, flag):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    kw = {"propose": True} if flag == "propose" else {"approve": number if why == "done" else "000000"}
    holder = None
    if why == "bad clock":
        tw.clock.t = float("nan")
    elif why == "environment":
        tw.venue.environment = "production"
    elif why == "lock taken":
        holder = osx._RunLock(str(tw.state_path))
        assert holder.acquire()
    elif why == "session":
        tw.clock.t = ny("2026-11-13 17:00")
    try:
        out = tw.run(**kw)
    finally:
        if holder:
            holder.release()
    assert out["mode"] == flag


# ---- outcome 8: tests for the two breaks no test caught, and second tests

def test_the_ledger_a_propose_run_holds_cannot_intend_or_send(tw, monkeypatch):
    held = []
    real = osx._Run._rest

    def rest(self, now):
        held.append(self.ledger)
        return real(self, now)
    monkeypatch.setattr(osx._Run, "_rest", rest)
    tw.paper = paper({"a": {"SPY": 10.0}})
    propose(tw)
    (ledger,) = held
    with pytest.raises(RuntimeError, match="a propose run never intends an order"):
        ledger.intend("os-t-SPY", "SPY", 1.0, 500.0, 505.0, batch="os-t")
    with pytest.raises(RuntimeError, match="a propose run never sends an order"):
        ledger.submit_once(tw.venue, "os-t-SPY")
    assert tw.orders() == [] and tw.venue.submits() == []


def test_an_expiry_is_judged_to_the_second(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    tw.clock.t += osx.TICKET_LIFE_SECONDS + 0.5
    out = approve(tw, number)
    assert out["stage"] == "ticket" and "expired" in out["reason"]


def test_an_account_that_differs_only_at_approve_is_refused_and_counted(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    tw.venue.account = ACCOUNT + "X"
    out = approve(tw, number)
    assert "another account" in out["reason"] and tw.venue.submits() == []
    assert [r["verdict"] for r in ticket_rows(tw, "verdicts")] == ["refused"]
    tw.venue.account = ACCOUNT
    assert approve(tw, number)["orders_sent"] == 1


def test_the_capital_is_compared_as_resolved(tmp_path):
    w = ticket_world(tmp_path / "a")
    _, number = _proposed(w, {"a": {"SPY": 10.0}}, capital=100_000.0)     # the paper equity, given
    assert approve(w, number)["orders_sent"] == 1                          # the same capital, by default
    w = ticket_world(tmp_path / "b")
    _, number = _proposed(w, {"a": {"SPY": 10.0}})
    out = approve(w, number, capital=100_000.01)
    assert "capital" in out["reason"] and w.venue.submits() == []


def test_a_fractional_ticket_is_not_approved_in_whole_shares(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}}, fractional=True)
    out = approve(tw, number)
    assert "fractional" in out["reason"] and tw.venue.submits() == []


def test_a_ticket_held_by_another_run_is_not_sent_at_the_last_look(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})

    def patch(led):
        real = led.intend

        def intend(*a, **k):
            rec = real(*a, **k)
            con = sqlite3.connect(osx.ticket_path_for(tw.state_path))        # the fate row is not this run's
            con.execute("DROP TRIGGER fates_no_delete")
            con.execute("DELETE FROM fates")
            con.execute("INSERT INTO fates (day, number, fate, time, batch) VALUES ('2026-11-13', ?, 'used', 0, 'other')",
                        (number,))
            con.commit()
            con.close()
            return rec
        led.intend = intend
    out = tw.run(approve=number, patch=patch)
    assert tw.venue.submits() == [] and [o["state"] for o in tw.orders()] == ["intended"]
    assert out["ticket"]["orders"][0]["outcome"] in ("dropped", "unknown")


def test_a_ticket_stamped_for_another_day_is_refused_even_when_fresh(tw):
    number = _crafted(tw, day="2026-11-12")
    out = approve(tw, number)
    assert "another day" in out["reason"] and tw.venue.submits() == []


def test_a_ticket_of_another_venue_in_this_file_is_refused(tw):
    number = _crafted(tw, venue="someone-sandbox")
    out = approve(tw, number)
    assert "another venue" in out["reason"] and tw.venue.submits() == []


# ---- rev 3: the record mirrors the order ledger; a later change of the ledger appends a correcting row

def _history(w, number):
    """{line: [rows, oldest first]} of a ticket's outcome rows."""
    out = {}
    for r in _rows(w, "SELECT * FROM outcomes WHERE number = ? ORDER BY rowid", (number,)):
        out.setdefault(r["line"], []).append(r)
    return out


def _latest_mirrors_venue(w):
    """Every line of every used ticket has a latest row; the latest "sent" rows are, order for order, what the venue
    holds under the batch that used the ticket."""
    for t in _rows(w, "SELECT day, number, batch FROM fates WHERE fate = 'used'"):
        lines = {r["line"] for r in _rows(w, "SELECT line FROM lines WHERE day = ? AND number = ?", (t["day"], t["number"]))}
        hist = _history(w, t["number"])
        assert set(hist) == lines, (t, hist)
        sent = {rows[-1]["tag"] for rows in hist.values() if rows[-1]["outcome"] == "sent"}
        assert sent == {tag for tag in w.venue.book if tag.startswith(t["batch"] + "-")}, (t, sent, list(w.venue.book))


def _never_arrived(w):
    """A ticket's SPY order the venue never had: the ledger settles it as never arrived, its line "not sent"."""
    _, number = _proposed(w, {"a": {"SPY": 10.0}})
    w.venue.submit_mode = "raise"                                        # read timed out; the venue has no order
    approve(w, number)
    w.venue.submit_mode = "accept"
    w.paper = paper({"a": {"SPY": 0.0}})                                  # nothing is wanted any more
    w.later(3)
    w.run()
    (rec,) = w.orders()
    assert rec["state"] == "never_arrived", rec
    return number, rec


def _reveal(w, rec):
    """The venue turns out to hold the order after all."""
    w.venue._take(rec["symbol"], rec["units"], rec["tag"], rec["limit_price"])


@pytest.mark.parametrize("kind", ["approve of another number", "propose", "ordinary"])
def test_an_order_found_at_the_venue_on_recheck_gets_one_correcting_sent_row_on_the_next_run(tw, kind):
    number, rec = _never_arrived(tw)
    (first,) = _history(tw, number)[0]
    assert first["outcome"] == "not sent" and "rechecks it until" in first["reason"]
    other = None
    if kind == "approve of another number":
        _, other = _proposed(tw, {"a": {"SPY": 0.0, "QQQ": 1.0}})
    tw.later(1)
    _reveal(tw, rec)
    out = approve(tw, other) if other else propose(tw) if kind == "propose" else tw.run()
    rows = _history(tw, number)[0]
    assert [r["outcome"] for r in rows] == ["not sent", "sent"] and rows[1]["tag"] == rec["tag"]
    assert "found at the venue on recheck" in rows[1]["reason"] and rows[1]["batch"] == out["batch"]
    assert [(c["number_ends"], c["line"], c["from"], c["to"], c["tag"]) for c in out["ticket_corrections"]] == [
        (number[-2:], 0, "not sent", "sent", rec["tag"])]
    for _ in range(2):                                                   # exactly once
        tw.later(1)
        again = tw.run()
        assert "ticket_corrections" not in again and len(_history(tw, number)[0]) == 2
    _latest_mirrors_venue(tw)


def test_an_order_that_never_turns_up_keeps_its_one_row(tw):
    number, _ = _never_arrived(tw)
    for step in ("ordinary", "propose", "approve of another number", "ordinary"):
        tw.later(1)
        if step == "ordinary":
            out = tw.run()
        elif step == "propose":
            out = propose(tw)
        else:
            _, other = _proposed(tw, {"a": {"SPY": 0.0, "QQQ": 1.0}})
            out = approve(tw, other)
        assert "ticket_corrections" not in out
        (row,) = _history(tw, number)[0]
        assert row["outcome"] == "not sent" and "rechecks it until" in row["reason"]
    _latest_mirrors_venue(tw)


def _killed_at_correction(run, timeout=120.0):
    """Run `run()` in a forked child that is sent SIGKILL (by its process id) the moment it is about to write a
    correcting row. Returns what the child said: b"K" killed there, b"D" done without one, b"E" failed."""
    r, wfd = _os.pipe()
    pid = _os.fork()
    if pid == 0:                                                           # the child
        _os.close(r)
        try:
            def stop(self, *a, **k):
                _os.write(wfd, b"K")
                _time.sleep(timeout * 2)
            osx.TicketBook.correct = stop
            run()
            _os.write(wfd, b"D")
        except BaseException:
            _os.write(wfd, b"E" + _traceback.format_exc().encode()[-2000:])
        finally:
            _os._exit(0)
    _os.close(wfd)
    try:
        ready, _, _ = _select.select([r], [], [], timeout)
        data = _os.read(r, 4096) if ready else b""
        if not ready or data[:1] == b"K":
            _os.kill(pid, _signal.SIGKILL)                                 # our own child, by its process id
    finally:
        _os.waitpid(pid, 0)
        _os.close(r)
    return data


@pytest.mark.parametrize("kind", ["ordinary", "propose"])
def test_a_kill_between_the_ledgers_change_and_the_correcting_row_is_completed_by_the_next_run(tw, kind):
    number, rec = _never_arrived(tw)
    tw.later(1)
    _reveal(tw, rec)
    said = _killed_at_correction(tw.run if kind == "ordinary" else lambda: propose(tw))
    assert said[:1] == b"K", said
    (now,) = [o for o in tw.orders() if o["tag"] == rec["tag"]]
    assert now["state"] in osx.REACHED_VENUE                             # the ledger changed; the row was not written
    assert [r["outcome"] for r in _history(tw, number)[0]] == ["not sent"]
    tw.later(1)
    out = tw.run()
    assert [r["outcome"] for r in _history(tw, number)[0]] == ["not sent", "sent"]
    assert [c["to"] for c in out["ticket_corrections"]] == ["sent"]
    tw.later(1)
    assert "ticket_corrections" not in tw.run() and len(_history(tw, number)[0]) == 2


def _stopped_after(tw, where):
    """An approve that dies (Crash) at `where`, then an ordinary run that completes its line "not sent"."""
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})

    def crash(led):
        setattr(led, where, lambda *a, **k: (_ for _ in ()).throw(Crash("killed")))
    with pytest.raises(Crash):
        tw.run(approve=number, patch=crash)
    tw.paper = paper({"a": {"SPY": 0.0}})
    tw.later(1)
    tw.run()
    ((row,),) = _history(tw, number).values()
    assert row["outcome"] == "not sent"
    return number, row["tag"]


@pytest.mark.parametrize("case", ["an intended order", "a line never intended"])
def test_a_not_sent_line_whose_order_the_ledger_later_sends_is_corrected_to_sent(tw, case):
    number, tag = _stopped_after(tw, "submit_once" if case == "an intended order" else "intend")
    led = tw.ledger()
    try:
        if case == "a line never intended":
            led.intend(tag, "SPY", 10.0, 500.0, 505.0, batch=tag.rsplit("-", 1)[0])
        led.submit_once(tw.venue, tag)                                    # whatever sends it, the ledger now knows
    finally:
        led.close()
    tw.later(1)
    out = tw.run()
    assert [r["outcome"] for r in _history(tw, number)[0]] == ["not sent", "sent"]
    assert [(c["from"], c["to"]) for c in out["ticket_corrections"]] == [("not sent", "sent")]
    tw.later(1)
    assert "ticket_corrections" not in tw.run()
    _latest_mirrors_venue(tw)


def test_a_line_whose_order_goes_unknown_then_never_arrives_follows_the_ledger(tw):
    number, tag = _stopped_after(tw, "submit_once")
    tw.venue.submit_mode = "raise"
    led = tw.ledger()
    try:
        led.submit_once(tw.venue, tag)                                    # the ledger now says unknown
    finally:
        led.close()
    tw.venue.submit_mode = "accept"
    tw.later(1)
    out = tw.run()
    assert [(c["from"], c["to"]) for c in out["ticket_corrections"]] == [("not sent", "unknown")]
    tw.later(3)
    out = tw.run()
    rows = _history(tw, number)[0]
    assert [r["outcome"] for r in rows] == ["not sent", "unknown", "not sent"] and "rechecks it until" in rows[2]["reason"]
    assert [(c["from"], c["to"]) for c in out["ticket_corrections"]] == [("unknown", "not sent")]
    _latest_mirrors_venue(tw)


def test_a_sent_line_keeps_one_row_through_fills_and_a_cancel(tw):
    _, number = _proposed(tw, {"a": {"SPY": 10.0}})
    assert approve(tw, number)["orders_sent"] == 1
    (tag,) = list(tw.venue.book)
    tw.venue.fill(tag, 3)
    for _ in range(3):                                                    # partly filled, then cancelled by a run
        tw.later(1)
        assert "ticket_corrections" not in tw.run()
    assert [r["outcome"] for r in _history(tw, number)[0]] == ["sent"]
    _latest_mirrors_venue(tw)


def test_the_ticket_file_refuses_a_late_tag_and_any_second_row_that_is_not_a_correction(tw):
    w = tw
    book = osx.TicketBook(osx.ticket_path_for(w.state_path))
    try:
        now = w.clock.t
        ticket = {"day": "2026-11-13", "number": "515151", "created": now, "expires": now + 600, "venue": w.venue.name,
                  "environment": "sandbox", "account": book.account_hash(ACCOUNT), "capital": 100_000.0, "fractional": 0,
                  "equity": 1e6, "cash": 1e6, "settled_cash": None, "batch": "os-y"}
        book.create(ticket, [{"line": i, "fund": f, "side": "buy", "units": 1.0, "quote": 100.0, "limit": 101.0,
                              "value": 100.0} for i, f in enumerate(["SPY", "QQQ"])])
        book.mark_used("2026-11-13", "515151", "os-y")
        book.note_tag("2026-11-13", "515151", 0, "os-y-SPY", "os-y")
        book.note_outcome("2026-11-13", "515151", 0, "sent", "os-y-SPY", "accepted", "os-y")
        led = w.ledger()
        try:
            book.settle(led, "os-z")                                     # line 1: left over
        finally:
            led.close()
    finally:
        book.close()
    con = sqlite3.connect(osx.ticket_path_for(w.state_path))
    try:
        with pytest.raises(sqlite3.IntegrityError):                       # a tag after the line's outcome
            con.execute("INSERT INTO tags (day, number, line, tag, time, batch) VALUES "
                        "('2026-11-13', '515151', 1, 'os-y-QQQ', 0, 'x')")
        for line, outcome, tag in [(1, "sent", None), (1, "not sent", None),         # a left-over line is never corrected
                                   (0, "sent", "os-y-SPY"),                          # nor a row into the same outcome
                                   (0, "not sent", "os-other")]:                     # nor with another tag
            with pytest.raises(sqlite3.IntegrityError):
                con.execute("INSERT INTO outcomes (day, number, line, seq, outcome, tag, reason, time, batch) VALUES "
                            "('2026-11-13', '515151', ?, 1, ?, ?, 'x', 0, 'x')", (line, outcome, tag))
        with pytest.raises(sqlite3.IntegrityError):                       # rows of a line are numbered in order
            con.execute("INSERT INTO outcomes (day, number, line, seq, outcome, tag, reason, time, batch) VALUES "
                        "('2026-11-13', '515151', 0, 5, 'not sent', 'os-y-SPY', 'x', 0, 'x')")
        with pytest.raises(sqlite3.IntegrityError):                       # unknown is never a first row
            con.execute("INSERT INTO outcomes (day, number, line, seq, outcome, tag, reason, time, batch) VALUES "
                        "('2026-11-13', '515152', 0, 0, 'unknown', 'os-q', 'x', 0, 'x')")
    finally:
        con.close()


# ==== T22b: live mode and its four locks (docs/specs/2026-11-13-go-live-gate-design.md, step 2)
#
# Every live run here uses a stand-in production venue, owner's files written by the test in its temporary folder
# (never under the repository's config/), a fake second price source, and the run's fake clock. Nothing is sent
# anywhere.

import shutil as _shutil

from valedia_trader.execution_and_risk import go_live as gl
from valedia_trader.venues import webull as _wb


@pytest.fixture(autouse=True)
def _live_repo_restored(monkeypatch):
    """T22b rev 2: live mode reads the owner's files only from webull.LIVE_REPO; a live world replaces it in-process
    (live_world) and this puts it back after every test."""
    monkeypatch.setattr(_wb, "LIVE_REPO", getattr(_wb, "LIVE_REPO", _wb.REPO), raising=False)

LIVE_ENDS = ACCOUNT[-4:]
LIVE_ENV = {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s", "FINNHUB_API_KEY": "f"}
LIVE_LIMITS = {**GENEROUS, "max_orders_per_day": 40}


class LiveVenue(Booked):
    """The stand-in in Webull's production environment, at its production host, of the confirmation's class."""
    environment = "production"
    host = "api.webull.com"
    account_class = "INDIVIDUAL_CASH"
    settled = "same"              # T22d-1: the settled cash cash() reports: "same" (the cash), None, or "missing" (no key)

    def __init__(self, held=None, cash=1_000_000.0, name="webull-production"):
        super().__init__(held, cash, name)

    def cash(self):
        answer = super().cash()
        if self.settled == "missing":
            del answer["settled_cash"]
        else:
            answer["settled_cash"] = answer["cash"] if self.settled == "same" else self.settled
        return answer


class Second:
    """The second price source: a verdict per symbol, or per (symbol, price) in `by_price` (an exception is raised),
    every call kept."""

    def __init__(self):
        self.verdicts, self.by_price, self.calls = {}, {}, []

    def __call__(self, first_price, symbol, *, now, sources, **kw):
        v = self.by_price.get((symbol, first_price), self.verdicts.get(symbol, "agree"))
        self.calls.append({"symbol": symbol, "price": first_price, "now": now, "sources": sources, "kw": sorted(kw),
                           "verdict": v if isinstance(v, str) else "raised"})
        if isinstance(v, BaseException):
            raise v
        return {"verdict": v, "symbol": symbol, "sources": []}


def live_conf(**over):
    good = {"account_class": "INDIVIDUAL_CASH", "account_ends": LIVE_ENDS, "confirmed_by": "Burton", "max_order_value": 1000,
            "max_capital": 5000, "valid_until": "2026-11-20", "i_understand_this_trades_real_money": True}
    good.update(over)
    return good


LIVE_CHECKLIST = {"min_paper_broker_days": 1, "min_reconciled_fills": 1, "min_days_since_a_stopped_run": 1,
                  "fills_checked_by_hand": 1, "min_fills_checked_by_hand": 1, "confirmed_by": "Burton"}


def _paper_record(journal, day="2026-11-12"):
    journal.write(day, {"type": "order_step", "venue": gl.PAPER_VENUE, "batch": "paper-1", "event": "fill", "fill_id": "pf1"})
    journal.write(day, {"type": "order_step", "venue": gl.PAPER_VENUE, "batch": "paper-1", "event": "summary",
                        "status": "done", "stage": None})


def live_world(tmp, venue=None, conf=None, checklist=None, limits=None, environ=None):
    w = ticket_world(tmp, venue if venue is not None else LiveVenue())
    cfg = tmp / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    w.cfg = cfg
    for name, value in (("webull_live.json", live_conf() if conf is None else conf),
                        ("go_live.json", LIVE_CHECKLIST if checklist is None else checklist),
                        ("risk.json", LIVE_LIMITS if limits is None else limits)):
        if value is not None:
            (cfg / name).write_text(json.dumps(value))
            (cfg / name).chmod(0o644)
    w.limits = cfg / "risk.json"
    _paper_record(w.journal)
    _wb.LIVE_REPO = tmp                                            # this world's folder is the repository, in-process
    w.live = gl.LiveFiles(environ=dict(LIVE_ENV if environ is None else environ), calendar=CAL)
    w.second = Second()
    return w


def _follow_the_book(w):
    """T32: live mode follows only the strategies config/broker_book.json names (the command line reads it into a
    Follow). A live world follows every strategy of its paper book (one, "a", in these tests: the same targets)."""
    book = w.paper if isinstance(w.paper, osx.PaperTargets) else None
    names = tuple(sorted(book.wanted)) if book is not None and book.wanted else ("a",)
    return osx.Follow(ids=tuple(range(1, len(names) + 1)), names=names, digest=hashlib.sha256(repr(names).encode()).hexdigest())


def lrun(w, **kw):
    kw.setdefault("live", w.live)
    kw.setdefault("second_opinion", w.second)
    if "follow" not in kw:                                         # T32: decided when the run is made, from the book
        kw["follow"] = _follow_the_book(w)
    return w.run(**kw)


def lpropose(w, capital=5000.0, **kw):
    return lrun(w, propose=True, capital=capital, **kw)


def lapprove(w, number, capital=5000.0, **kw):
    return lrun(w, approve=number, capital=capital, **kw)


def _live_proposed(w, wanted, capital=5000.0, **kw):
    w.paper = paper(wanted, equity=capital)
    out = lpropose(w, capital=capital, **kw)
    assert out["status"] == "done" and out["ticket"]["orders"], out
    return out, out["ticket_number"]


@pytest.fixture
def lw(tmp_path):
    return live_world(tmp_path / "w")


def _account_reads(w):
    return [c for c in w.venue.calls if c[0] in ("account_id", "positions", "cash", "order_status", "submit", "cancel")]


# ---- expected outcome: a live propose writes a ticket and sends nothing; a live approve sends it

def test_a_live_propose_writes_a_live_ticket_and_sends_nothing(lw):
    out, number = _live_proposed(lw, {"a": {"IWM": 3.0, "TLT": 4.0}})
    assert lw.venue.submits() == [] and lw.orders() == []
    assert out["live"] is True and [lock["lock"] for lock in out["locks"]] == [1, 2, 3, 4]
    assert all(lock["met"] for lock in out["locks"])
    (ticket,) = ticket_rows(lw, "tickets")
    assert ticket["environment"] == "production" and ticket["venue"] == "webull-production"
    assert sorted(ln["symbol"] for ln in ticket_rows(lw, "lines")) == ["IWM", "TLT"]
    assert sorted(c["symbol"] for c in lw.second.calls) == ["IWM", "TLT"]
    assert all(c["sources"] == ("alpaca", "finnhub") and c["now"].tzinfo is not None and "clock" in c["kw"]
               for c in lw.second.calls)
    assert {c["now"].timestamp() for c in lw.second.calls} <= {lw.clock.t}
    meta = {r["key"]: r["value"] for r in _rows(lw, "SELECT key, value FROM meta")}
    assert json.loads(meta[f"live:2026-11-13:{number}"]) == {"mode": "live", "files": gl.judge_locks(
        lw.live, datetime(2026, 11, 13).date(), 5000.0).digest}


def test_a_live_approve_sends_the_tickets_orders_and_asks_the_second_source_again(lw):
    _, number = _live_proposed(lw, {"a": {"IWM": 3.0, "TLT": 4.0}})
    lw.second.calls.clear()
    lw.later(1)
    out = lapprove(lw, number)
    assert out["status"] == "done" and out["live"] is True and out["orders_sent"] == 2, out
    assert sorted(lw.venue.book[t]["symbol"] for t in lw.venue.submits()) == ["IWM", "TLT"]
    assert sorted(c["symbol"] for c in lw.second.calls) == ["IWM", "TLT"]
    assert [r["outcome"] for r in ticket_rows(lw, "outcomes")] == ["sent", "sent"]


@pytest.mark.parametrize("kw, words", [({}, "only with propose or approve"),
                                       ({"propose": True}, "needs the capital"),
                                       ({"approve": "123456"}, "needs the capital"),
                                       ({"propose": True, "capital": 1000.0, "fractional": True}, "whole shares only"),
                                       ({"approve": "123456", "capital": 1000.0, "fractional": True}, "whole shares only")])
def test_live_without_a_ticket_mode_a_capital_or_in_fractions_reads_nothing_and_writes_nothing(tmp_path, kw, words):
    w = live_world(tmp_path / "w")
    w.ledger().close()                                             # the ledger file is the harness's, made before
    before = sorted(str(p) for p in w.tmp.rglob("*"))
    out = lrun(w, **kw)
    assert out["status"] == "stopped" and out["stage"] == "live" and words in out["reason"]
    assert w.venue.calls == [] and w.quote_calls == [] and w.second.calls == []
    assert sorted(str(p) for p in w.tmp.rglob("*")) == before


# ---- invariants 3, 4, 5 at the order step: any lock unmet stops before the account is read or a quote asked

def _unmet(w, how):
    cfg = w.cfg
    if how == "no confirmation":
        (cfg / "webull_live.json").unlink()
    elif how == "old form":
        (cfg / "webull_live.json").write_text(json.dumps({"account_id": ACCOUNT, "confirmed_by": "Burton",
                                                          "max_order_value": 1000, "i_understand_this_trades_real_money": True}))
    elif how == "lapsed":
        (cfg / "webull_live.json").write_text(json.dumps(live_conf(valid_until="2026-11-12")))
    elif how == "too far ahead":
        (cfg / "webull_live.json").write_text(json.dumps(live_conf(valid_until="2026-12-15")))
    elif how == "group-writable":
        (cfg / "webull_live.json").chmod(0o664)
    elif how == "linked":
        real = w.tmp / "elsewhere.json"
        real.write_text((cfg / "webull_live.json").read_text())
        (cfg / "webull_live.json").unlink()
        (cfg / "webull_live.json").symlink_to(real)
    elif how == "no checklist":
        (cfg / "go_live.json").unlink()
    elif how == "too few days":
        (cfg / "go_live.json").write_text(json.dumps({**LIVE_CHECKLIST, "min_paper_broker_days": 5}))
    elif how == "too few hand checks":
        (cfg / "go_live.json").write_text(json.dumps({**LIVE_CHECKLIST, "fills_checked_by_hand": 0}))
    elif how == "a recent stopped run":
        (cfg / "go_live.json").write_text(json.dumps({**LIVE_CHECKLIST, "min_days_since_a_stopped_run": 3}))
        w.journal.write("2026-11-12", {"type": "order_step", "venue": gl.PAPER_VENUE, "batch": "paper-2", "event": "summary",
                                       "status": "stopped", "stage": "reconcile"})
    elif how == "a damaged journal":
        with (w.tmp / "journal" / "2026-11-12.jsonl").open("ab") as fh:
            fh.write(b'{"torn": \n')
    elif how == "no max_orders_per_day":
        (cfg / "risk.json").write_text(json.dumps(GENEROUS))
    elif how == "max_orders_per_day 300":
        (cfg / "risk.json").write_text(json.dumps({**GENEROUS, "max_orders_per_day": 300}))
    elif how == "no second source key":
        w.live = dataclasses.replace(w.live, environ={k: v for k, v in LIVE_ENV.items() if k != "FINNHUB_API_KEY"})
    elif how == "capital above max_capital":
        w.live_capital = 6000.0
    else:
        raise AssertionError(how)


UNMET = {"no confirmation": (1, "is missing"), "old form": (1, "old form"), "lapsed": (1, "lapsed"),
         "too far ahead": (1, "31 days"), "group-writable": (1, "writable by its group"), "linked": (1, "symbolic link"),
         "capital above max_capital": (1, "(by 1,000.00)"),
         "no checklist": (2, "is missing"), "too few days": (2, "(4 short)"), "too few hand checks": (2, "(1 short)"),
         "a recent stopped run": (2, "(2 short)"), "a damaged journal": (2, "damaged"),
         "no max_orders_per_day": (3, "max_orders_per_day"), "max_orders_per_day 300": (3, "from 1 to 200"),
         "no second source key": (3, "FINNHUB_API_KEY")}


@pytest.mark.parametrize("how", sorted(UNMET))
@pytest.mark.parametrize("mode", ["propose", "approve", "check"])
def test_an_unmet_lock_stops_before_any_account_read_or_quote_and_says_which_and_by_how_much(tmp_path, how, mode):
    w = live_world(tmp_path / "w")
    number = None
    if mode == "approve":
        _, number = _live_proposed(w, {"a": {"IWM": 3.0}})
        w.venue.calls.clear()
        w.quote_calls.clear()
        w.second.calls.clear()
        w.later(1)
    w.live_capital = 5000.0
    _unmet(w, how)
    cfg_before = {p.name: (p.is_symlink(), p.read_bytes() if not p.is_symlink() and p.exists() else None)
                  for p in w.cfg.iterdir()}
    n, words = UNMET[how]
    if mode == "check":
        state = gl.judge_locks(w.live, datetime(2026, 11, 13).date(), w.live_capital)
        assert not state.met and f"lock {n} (" in state.unmet() and words in state.unmet()
        return
    w.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    out = (lpropose(w, capital=w.live_capital) if mode == "propose" else lapprove(w, number, capital=w.live_capital))
    assert out["status"] == "stopped" and out["stage"] == "lock", out
    assert f"lock {n} (" in out["reason"] and words in out["reason"], out["reason"]
    assert w.venue.calls == [] and w.quote_calls == [] and w.second.calls == []
    assert w.venue.submits() == [] and out["live"] is True
    assert {p.name: (p.is_symlink(), p.read_bytes() if not p.is_symlink() and p.exists() else None)
            for p in w.cfg.iterdir()} == cfg_before                # invariant 2: nothing written, created or repaired
    if mode == "propose":
        assert not Path(osx.ticket_path_for(w.state_path)).exists() or ticket_rows(w, "tickets") == []
    else:                                                          # invariant 11: the approve is marked refused
        (verdict,) = [v for v in ticket_rows(w, "verdicts") if v["batch"] == out["batch"]]
        assert verdict["verdict"] == "refused" and f"lock {n}" in verdict["reason"]
        assert [f["fate"] for f in ticket_rows(w, "fates")] == []  # not used: a later approve may still send it
    assert ACCOUNT[:-4] not in _all_text(w, [out])


def test_no_environment_variable_or_argument_stands_in_for_an_owners_file(lw, monkeypatch):
    for p in lw.cfg.iterdir():
        p.unlink()
    for name in ("WEBULL_LIVE", "VALEDIA_GO_LIVE", "GO_LIVE", "WEBULL_LIVE_CONFIRMATION", "MAX_ORDERS_PER_DAY"):
        monkeypatch.setenv(name, "1")
    lw.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    out = lpropose(lw)
    assert out["stage"] == "lock" and all(f"lock {n} (" in out["reason"] for n in (1, 2, 3))
    assert list(lw.cfg.iterdir()) == []                            # never created, defaulted or repaired


# ---- invariant 6: lock 4, the ticket in live mode

def test_a_live_run_never_sends_in_the_run_that_decides(lw, monkeypatch):
    calls = []
    monkeypatch.setattr(osx.OrderLedger, "intend", lambda *a, **k: calls.append("intend"))
    lw.paper = paper({"a": {"IWM": 3.0, "TLT": 4.0}}, equity=5000.0)
    out = lpropose(lw)
    assert out["status"] == "done" and calls == [] and lw.venue.submits() == []


def _switch(venue, live):
    venue.environment = "production" if live else "sandbox"
    venue.host = "api.webull.com" if live else "api.sandbox.webull.com"


def test_a_ticket_made_in_the_test_environment_is_never_accepted_in_live_mode(lw):
    _switch(lw.venue, live=False)
    lw.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    out = lw.run(propose=True, capital=5000.0)                     # a paper ticket, the same venue name and state file
    number = out["ticket_number"]
    assert ticket_rows(lw, "tickets")[0]["environment"] == "sandbox"
    _switch(lw.venue, live=True)
    lw.later(1)
    out = lapprove(lw, number)
    assert out["stage"] == "ticket" and "another environment" in out["reason"] and lw.venue.submits() == []
    assert ticket_rows(lw, "verdicts")[-1]["verdict"] == "refused"


def test_a_live_ticket_is_never_accepted_in_the_test_environment(lw):
    _, number = _live_proposed(lw, {"a": {"IWM": 3.0}})
    _switch(lw.venue, live=False)
    lw.later(1)
    out = lw.run(approve=number, capital=5000.0)
    assert out["stage"] == "ticket" and "another environment" in out["reason"] and lw.venue.submits() == []


def _crafted_live(w, environment, live_mark, number="616161"):
    book = osx.TicketBook(osx.ticket_path_for(w.state_path))
    try:
        now = w.clock.t
        ticket = {"day": "2026-11-13", "number": number, "created": now, "expires": now + 900, "venue": w.venue.name,
                  "environment": environment, "account": book.account_hash(ACCOUNT), "capital": 5000.0, "fractional": 0,
                  "equity": 1e6, "cash": 1e6, "settled_cash": None, "batch": "os-crafted"}
        book.create(ticket, [{"line": 0, "fund": "IWM", "side": "buy", "units": 3.0, "quote": 200.0, "limit": 202.0,
                              "value": 600.0}], live=live_mark)
    finally:
        book.close()
    return number


def test_the_ticket_records_its_mode_and_each_mode_refuses_the_other(lw):
    digest = gl.judge_locks(lw.live, datetime(2026, 11, 13).date(), 5000.0).digest
    lw.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    number = _crafted_live(lw, "production", None)                 # production, but not made in live mode
    out = lapprove(lw, number)
    assert out["stage"] == "ticket" and "not made in live mode" in out["reason"] and lw.venue.submits() == []
    _switch(lw.venue, live=False)
    number = _crafted_live(lw, "sandbox", {"mode": "live", "files": digest}, number="626262")
    out = lw.run(approve=number, capital=5000.0)
    assert out["stage"] == "ticket" and "made in live mode" in out["reason"] and lw.venue.submits() == []


def test_every_step_one_rule_holds_in_live_mode(lw):
    _, number = _live_proposed(lw, {"a": {"IWM": 3.0}})
    lw.later(1)
    out = lapprove(lw, wrong_other_number(number))
    assert out["stage"] == "ticket" and "no ticket has that number" in out["reason"]
    out = lapprove(lw, number, capital=4000.0)
    assert out["stage"] == "ticket" and "another capital" in out["reason"]
    lw.later(20)
    out = lapprove(lw, number)
    assert out["stage"] == "ticket" and "expired" in out["reason"] and lw.venue.submits() == []
    _, number = _live_proposed(lw, {"a": {"IWM": 3.0}})
    lw.later(1)
    assert lapprove(lw, number)["orders_sent"] == 1
    out = lapprove(lw, number)
    assert out["stage"] == "ticket" and "has been used" in out["reason"] and len(lw.venue.submits()) == 1


# ---- invariant 7: the venue in live mode

class _Env(str):
    pass


class _PropEnv(LiveVenue):
    @property
    def environment(self):
        return "production"


class _PropHost(LiveVenue):
    @property
    def host(self):
        return "api.webull.com"


class _GetattrHost(LiveVenue):
    host = None

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        del self.__class__.host

    def __getattr__(self, name):
        if name == "host":
            return "api.webull.com"
        raise AttributeError(name)


class _NoHost(Booked):
    environment = "production"
    account_class = "INDIVIDUAL_CASH"

    def __init__(self):
        super().__init__(name="webull-production")


def _live_venue(how):
    v = LiveVenue()
    if how == "sandbox":
        v.environment = "sandbox"
    elif how == "Production":
        v.environment = "Production"
    elif how == "str subclass env":
        v.environment = _Env("production")
    elif how == "property env":
        v = _PropEnv()
    elif how == "property host":
        v = _PropHost()
    elif how == "no host":
        v = _NoHost()
    elif how in ("api.webull.com.", "API.webull.com", "us-api.webull.com", "api.webull.com.example", "api.sandbox.webull.com",
                 " api.webull.com"):
        v.host = how
    elif how == "str subclass host":
        v.host = _Env("api.webull.com")
    elif how == "events class":
        v.account_class = "EVENTS_CASH"
    elif how == "events name":
        v.name = "webull-production-events"
    else:
        raise AssertionError(how)
    return v


@pytest.mark.parametrize("how", ["sandbox", "Production", "str subclass env", "property env", "property host", "no host",
                                 "api.webull.com.", "API.webull.com", "us-api.webull.com", "api.webull.com.example",
                                 "api.sandbox.webull.com", " api.webull.com", "str subclass host", "events class",
                                 "events name"])
@pytest.mark.parametrize("mode", ["propose", "approve"])
def test_live_mode_needs_exactly_the_production_environment_and_host_by_plain_attributes(tmp_path, how, mode):
    w = live_world(tmp_path / "w")
    number = "123456"
    if mode == "approve":
        _, number = _live_proposed(w, {"a": {"IWM": 3.0}})
        w.later(1)
        w.quote_calls.clear()
    w.venue = _live_venue(how)
    w.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    out = lpropose(w) if mode == "propose" else lapprove(w, number)
    assert out["status"] == "stopped" and out["stage"] == "environment", out
    assert _account_reads(w) == [] and w.quote_calls == [] and w.venue.submits() == []


def test_a_paper_run_still_refuses_the_production_venue(tmp_path):
    w = live_world(tmp_path / "w")
    w.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    out = w.run(propose=True, capital=5000.0)
    assert out["stage"] == "environment" and w.venue.calls == []
    out = w.run()
    assert out["stage"] == "environment" and w.venue.calls == []


@pytest.mark.parametrize("cls", ["INDIVIDUAL_MARGIN", "IRA", None, 7])
def test_an_account_of_another_class_than_the_confirmation_names_is_refused_at_the_first_account_read(tmp_path, cls):
    w = live_world(tmp_path / "w")
    w.venue.account_class = cls
    w.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    out = lpropose(w)
    assert out["stage"] == "lock" and "another class" in out["reason"]
    assert [c[0] for c in _account_reads(w)] == ["account_id"] and w.quote_calls == []


def test_an_account_whose_last_4_differ_is_refused_and_no_more_than_its_last_4_is_said(tmp_path):
    w = live_world(tmp_path / "w", conf=live_conf(account_ends="9Z9Z"))
    w.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    out = lpropose(w)
    assert out["stage"] == "lock" and "ending 9Z9Z" in out["reason"] and f"ends {LIVE_ENDS}" in out["reason"]
    assert [c[0] for c in _account_reads(w)] == ["account_id"] and w.quote_calls == []
    assert ACCOUNT[:-4] not in _all_text(w, [out]) and ACCOUNT[-5:] not in _all_text(w, [out])


def test_an_approve_on_an_account_that_no_longer_matches_is_refused_and_marked(lw):
    _, number = _live_proposed(lw, {"a": {"IWM": 3.0}})
    lw.later(1)
    lw.venue.account = "Zq9ACCT77310055QQ"
    out = lapprove(lw, number)
    assert out["stage"] == "lock" and lw.venue.submits() == []
    assert ticket_rows(lw, "verdicts")[-1]["verdict"] == "refused"


def test_live_mode_trades_whole_shares_only_and_never_crypto(lw):
    lw.prices["BTC-USD"] = 100.0
    lw.paper = paper({"a": {"IWM": 3.6, "BTC-USD": 2.0}}, equity=5000.0)
    out = lpropose(lw)
    assert out["status"] == "done"
    assert [(ln["symbol"], ln["units"]) for ln in ticket_rows(lw, "lines")] == [("IWM", 3.0)]
    assert any(s["symbol"] == "BTC-USD" and "crypto" in s["reason"] for s in out["skipped"])


# ---- invariants 5 and 8: each order's second price and its caps, at propose and again at approve

def test_an_order_the_second_source_does_not_agree_on_is_dropped_and_the_rest_go_on(lw):
    lw.second.verdicts = {"IWM": "disagree", "TLT": "no second quote", "GLD": ValueError("boom")}
    lw.paper = paper({"a": {"IWM": 3.0, "TLT": 4.0, "GLD": 2.0, "QQQ": 1.0}}, equity=5000.0)
    out = lpropose(lw)
    assert [ln["symbol"] for ln in ticket_rows(lw, "lines")] == ["QQQ"]
    reasons = {d["symbol"]: d["reason"] for d in out["dropped"]}
    assert "says disagree" in reasons["IWM"] and "says no second quote" in reasons["TLT"]
    assert "no second quote (the check failed: ValueError)" in reasons["GLD"]
    number = out["ticket_number"]
    lw.second.verdicts = {"QQQ": "disagree"}
    lw.later(1)
    out = lapprove(lw, number)
    assert lw.venue.submits() == [] and "says disagree" in out["dropped"][0]["reason"]
    assert [r["outcome"] for r in ticket_rows(lw, "outcomes")] == ["not sent"]


def test_an_order_above_max_order_value_is_dropped(tmp_path):
    w = live_world(tmp_path / "w", conf=live_conf(max_order_value=500))
    w.paper = paper({"a": {"IWM": 3.0, "TLT": 4.0}}, equity=5000.0)
    out = lpropose(w)
    assert [ln["symbol"] for ln in ticket_rows(w, "lines")] == ["TLT"]
    assert "above max_order_value 500.00" in out["dropped"][0]["reason"] and out["dropped"][0]["symbol"] == "IWM"


def test_what_this_ledger_holds_plus_the_tickets_buys_stays_within_max_capital(tmp_path):
    w = live_world(tmp_path / "w", conf=live_conf(max_capital=1500, max_order_value=1000))
    w.venue.fill_at_once = 1.0
    _, number = _live_proposed(w, {"a": {"IWM": 3.0}}, capital=1500.0)       # 606.00 at its limit
    w.later(1)
    assert lapprove(w, number, capital=1500.0)["orders_sent"] == 1
    w.later(20)
    w.paper = paper({"a": {"IWM": 3.0, "TLT": 4.0, "GLD": 2.0}}, equity=1500.0)
    out = lpropose(w, capital=1500.0)
    held = 3 * 200.0                                                         # this ledger's holding at the quote
    lines = ticket_rows(w, "lines")
    kept = [ln for ln in lines if ln["number"] == out["ticket_number"]]
    assert held + sum(ln["units"] * ln["limit_price"] for ln in kept if ln["side"] == "buy") <= 1500.0
    assert [ln["symbol"] for ln in kept] == ["GLD"]                          # 505.00; TLT's 404.00 then exceeds 1,500
    assert any(d["symbol"] == "TLT" and "above max_capital 1,500.00" in d["reason"] for d in out["dropped"])


def test_an_approve_judges_the_caps_again_on_fresh_quotes(tmp_path):
    w = live_world(tmp_path / "w", conf=live_conf(max_order_value=607))
    _, number = _live_proposed(w, {"a": {"IWM": 3.0}})                        # 3 x 202.00 = 606.00
    w.prices["IWM"] = 200.6                                                   # within 0.5%; 3 x 202.60 = 607.80
    w.later(1)
    out = lapprove(w, number)
    assert w.venue.submits() == [] and "above max_order_value" in out["dropped"][0]["reason"]


def _held_value(book, prices):
    return math.fsum(u * prices[s] for s, u in book.items() if u > 1e-9)


def _check_live_run(w, out, stage, conf, book_before, prices, number=None):
    """What may happen in one live run, by the model: every order kept (a ticket line at propose, a send at approve)
    is at most max_order_value at its limit, had the second source's `agree` in this run, and with what this ledger
    held keeps the money at work within max_capital; every order dropped for a lock has a true reason."""
    mov, cap = conf["max_order_value"], conf["max_capital"]
    answers = {}
    for c in w.second.calls:
        answers.setdefault(c["symbol"], []).append(c["verdict"])
    if stage == "propose":
        kept = [(ln["symbol"], ln["units"], ln["limit_price"]) for ln in ticket_rows(w, "lines")
                if out.get("ticket_number") and ln["number"] == out["ticket_number"]]
    else:
        kept = [(w.venue.book[t]["symbol"], w.venue.book[t]["units"], w.venue.book[t]["limit"]) for t in w.venue.submits()]
    held = _held_value(book_before, prices)
    for sym, units, limit in kept:
        assert abs(units) * limit <= mov + 1e-9, (stage, sym, units, limit, mov)
        assert answers.get(sym, [None])[-1] == "agree", (stage, sym, answers)
        assert float(units).is_integer()
    kept_buys = math.fsum(u * lp for _, u, lp in kept if u > 0)
    if kept_buys:                                    # a holding that grew above the cap on prices: sales only
        assert held + kept_buys <= cap + 1e-6, (stage, held, kept, cap)
    for d in out.get("dropped", []):
        sym, units, why = d["symbol"], d["units"], d["reason"]
        if "max_order_value" in why:
            assert abs(units) * osx.limit_price(units, prices[sym], units < 0) > mov - 1e-9 or \
                abs(units) * osx.limit_price(units, prices[sym]) > mov - 1e-9
        elif "max_capital" in why:
            assert units > 0 and held + kept_buys + units * osx.limit_price(units, prices[sym]) > cap - 1e-6
        elif "second price source" in why:
            assert answers.get(sym, ["agree"])[-1] != "agree"
    return kept


def _live_caps_scenario(tmp, seed):
    rng = random.Random(seed)
    cap = rng.choice([800, 2500, 6000])
    mov = rng.choice([m for m in (150, 400, 900, 2500) if m <= cap])
    w = live_world(tmp, conf=live_conf(max_order_value=mov, max_capital=cap))
    capital = float(rng.choice([cap, round(cap * rng.uniform(0.3, 1.0), 2)]))
    stats = {"kept": 0, "dropped": 0, "sent": 0}
    verdicts = ["agree", "agree", "agree", "disagree", "no second quote", "raise", "nonsense"]
    for round_ in range(2):
        for sym in ("SPY", "QQQ", "IWM", "TLT", "GLD"):
            w.prices[sym] = rng.choice([20.0, 55.5, 101.25, 230.0, 480.0])
        wanted = {s: float(rng.randint(0, 6)) for s in rng.sample(["SPY", "QQQ", "IWM", "TLT", "GLD"], rng.randint(1, 5))}
        w.paper = paper({"a": wanted}, equity=capital)
        pick = {s: rng.choice(verdicts) for s in wanted}
        w.second.verdicts = {s: (RuntimeError("x") if v == "raise" else v) for s, v in pick.items()}
        w.second.calls.clear()
        book_before = w.book()
        out = lpropose(w, capital=capital)
        if _mark_refused(w, out, book_before, stats):              # T22d-1: a held mark not agreed on stops the run
            w.later(20)
            w.venue.calls.clear()
            continue
        assert out["status"] == "done", out
        kept = _check_live_run(w, out, "propose", live_conf(max_order_value=mov, max_capital=cap), book_before, w.prices)
        stats["kept"] += len(kept)
        stats["dropped"] += len(out.get("dropped", []))
        number = out.get("ticket_number")
        if number:
            for sym in w.prices:
                if rng.random() < 0.6:
                    w.prices[sym] = round(w.prices[sym] * rng.uniform(0.997, 1.003), 2)
            w.second.verdicts = {s: (RuntimeError("x") if v == "raise" else v)
                                 for s, v in ((s, rng.choice(verdicts)) for s in wanted)}
            w.second.calls.clear()
            w.venue.fill_at_once = rng.choice([0.0, 1.0])
            w.later(1)
            book_before = w.book()
            out = lapprove(w, number, capital=capital)
            if not _mark_refused(w, out, book_before, stats):
                sent = _check_live_run(w, out, "approve", live_conf(max_order_value=mov, max_capital=cap), book_before,
                                       w.prices)
                stats["sent"] += len(sent)
            w.venue.calls.clear()
        w.later(20)
        w.venue.calls.clear()
    return stats


def _mark_refused(w, out, book_before, stats):
    """T22d-1 (A1): a symbol this ledger holds whose mark the second source does not agree on stops the live run at
    stage "marks", before any ticket line or send, naming the first such symbol; True when that is what happened."""
    held = sorted(s for s, u in book_before.items() if u > 1e-9)
    bad = [s for s in held if w.second.by_price.get((s, w.prices[s]), w.second.verdicts.get(s, "agree")) != "agree"]
    if not bad:
        return False
    assert out["status"] == "stopped" and out["stage"] == "marks" and bad[0] in out["reason"], out
    assert w.venue.submits() == [] and not out.get("ticket_number"), out
    assert [c["symbol"] for c in w.second.calls][-1] == bad[0]
    stats["marks"] = stats.get("marks", 0) + 1
    return True


@pytest.mark.parametrize("seed_block", range(6))
def test_live_orders_stay_within_the_caps_and_the_second_sources_verdict_over_generated_scenarios(tmp_path, seed_block):
    """50 scenarios per block, 300 in all, each two rounds of a live propose and approve."""
    total = {"kept": 0, "dropped": 0, "sent": 0, "marks": 0}
    for k in range(50):
        stats = _live_caps_scenario(tmp_path / f"s{k}", 220_000 + seed_block * 50 + k)
        for key in total:
            total[key] += stats.get(key, 0)
        _shutil.rmtree(tmp_path / f"s{k}")
    assert total["kept"] > 30 and total["dropped"] > 30 and total["sent"] > 10, total


# ---- invariant 11: a lock that stops being met between propose and approve stops the approve

CHANGES = ["none", "conf removed", "conf lapsed", "conf smaller cap", "conf renamed", "conf group-writable", "conf linked",
           "checklist removed", "checklist raised", "checklist edited", "a stopped paper run", "a damaged journal",
           "limits changed", "limits without the cap", "second key removed", "conf too far ahead"]


def _change(w, how, rng):
    cfg = w.cfg
    conf = json.loads((cfg / "webull_live.json").read_text())
    if how == "conf removed":
        (cfg / "webull_live.json").unlink()
    elif how == "conf lapsed":
        (cfg / "webull_live.json").write_text(json.dumps({**conf, "valid_until": "2026-11-12"}))
    elif how == "conf too far ahead":
        (cfg / "webull_live.json").write_text(json.dumps({**conf, "valid_until": "2026-12-31"}))
    elif how == "conf smaller cap":
        key = rng.choice(["max_capital", "max_order_value"])
        (cfg / "webull_live.json").write_text(json.dumps({**conf, key: conf[key] - rng.choice([1, 0.01, 100])}))
    elif how == "conf renamed":
        (cfg / "webull_live.json").write_text(json.dumps({**conf, "confirmed_by": "Someone else"}))
    elif how == "conf group-writable":
        (cfg / "webull_live.json").chmod(rng.choice([0o664, 0o646]))
    elif how == "conf linked":
        real = w.tmp / "real.json"
        real.write_text((cfg / "webull_live.json").read_text())
        (cfg / "webull_live.json").unlink()
        (cfg / "webull_live.json").symlink_to(real)
    elif how == "checklist removed":
        (cfg / "go_live.json").unlink()
    elif how == "checklist raised":
        (cfg / "go_live.json").write_text(json.dumps({**LIVE_CHECKLIST, "min_reconciled_fills": rng.randint(2, 9)}))
    elif how == "checklist edited":
        (cfg / "go_live.json").write_text(json.dumps({**LIVE_CHECKLIST, "confirmed_by": "B."}))
    elif how == "a stopped paper run":                             # the journal only: the files are unchanged
        w.journal.write("2026-11-13", {"type": "order_step", "venue": gl.PAPER_VENUE, "batch": "paper-x", "event": "summary",
                                       "status": "stopped", "stage": rng.choice(["reconcile", "send", "baseline"])})
    elif how == "a damaged journal":
        with (w.tmp / "journal" / "2026-11-12.jsonl").open("ab") as fh:
            fh.write(b'{"cut": ')
        w.journal.write("2026-11-13", {"type": "note"})
    elif how == "limits changed":
        (cfg / "risk.json").write_text(json.dumps({**LIVE_LIMITS, "max_orders_per_day": rng.choice([1, 39, 41, 200])}))
    elif how == "limits without the cap":
        (cfg / "risk.json").write_text(json.dumps(GENEROUS))
    elif how == "second key removed":
        gone = rng.choice(gl.SECOND_SOURCE_KEYS)
        w.live = dataclasses.replace(w.live, environ={k: v for k, v in LIVE_ENV.items() if k != gone})
    elif how != "none":
        raise AssertionError(how)


def _lock_change_scenario(tmp, seed):
    rng = random.Random(seed)
    w = live_world(tmp)
    how = CHANGES[seed % len(CHANGES)] if rng.random() < 0.5 else rng.choice(CHANGES)
    wanted = {s: float(rng.randint(1, 2)) for s in rng.sample(["IWM", "TLT", "GLD"], rng.randint(1, 3))}
    _, number = _live_proposed(w, {"a": wanted})
    lines = ticket_rows(w, "lines")
    w.later(rng.choice([0.5, 1, 5]))
    _change(w, how, rng)
    w.venue.calls.clear()
    w.quote_calls.clear()
    out = lapprove(w, number)
    if how == "none":                                              # the model: sent only when nothing changed
        assert out["status"] == "done" and len(w.venue.submits()) == len(lines), (seed, how, out)
        return how, True
    assert w.venue.submits() == [] and w.orders() == [], (seed, how, out)
    assert out["status"] == "stopped" and out["stage"] in ("lock", "ticket") and out["reason"], (seed, how, out)
    (verdict,) = [v for v in ticket_rows(w, "verdicts") if v["batch"] == out["batch"]]
    assert verdict["verdict"] == "refused" and verdict["reason"], (seed, how)
    if out["stage"] == "lock":
        assert _account_reads(w) == [] and w.quote_calls == [], (seed, how)
    else:
        assert "changed since that ticket was made" in out["reason"], (seed, how, out)
    return how, False


@pytest.mark.parametrize("seed_block", range(6))
def test_a_lock_that_stops_being_met_before_the_approve_stops_it_over_generated_scenarios(tmp_path, seed_block):
    """50 scenarios per block, 300 in all."""
    seen = {}
    for k in range(50):
        how, sent = _lock_change_scenario(tmp_path / f"s{k}", 330_000 + seed_block * 50 + k)
        seen[how] = seen.get(how, 0) + 1
        _shutil.rmtree(tmp_path / f"s{k}")
    assert len(seen) >= 12 and seen.get("none", 0) >= 1, seen


# ---- invariant 10: no account id beyond its last 4 anywhere

def test_no_live_ticket_summary_journal_or_error_holds_more_than_the_accounts_last_4(lw, capsys):
    outs = []
    _, number = _live_proposed(lw, {"a": {"IWM": 3.0}})
    lw.later(1)
    outs.append(lapprove(lw, number))
    lw.venue.account = "Zq9ACCT77310055QQ"
    lw.later(20)
    lw.paper = paper({"a": {"TLT": 3.0}}, equity=5000.0)
    outs.append(lpropose(lw))
    text = _all_text(lw, outs) + capsys.readouterr().out
    assert ACCOUNT[:-4] not in text and "Zq9ACCT77310055" not in text


# ---- closure (a): a correction never turns a sent line into not sent

def test_a_sent_line_the_ledger_no_longer_knows_stays_sent_and_the_summary_says_they_disagree(tw):
    out, number = _proposed(tw, {"a": {"SPY": 10.0}})
    older = tw.tmp / "older.sqlite"
    _shutil.copy(tw.ledger_path, older)                            # the order ledger before the approve
    tw.later(1)
    assert approve(tw, number)["orders_sent"] == 1
    assert [r["outcome"] for r in ticket_rows(tw, "outcomes")] == ["sent"]
    for suffix in ("-wal", "-shm"):
        p = Path(str(tw.ledger_path) + suffix)
        if p.exists():
            p.unlink()
    _shutil.copy(older, tw.ledger_path)                            # an older ledger file put back
    tw.later(20)
    tw.paper = paper({"a": {"SPY": 10.0}})
    out = propose(tw)
    assert [r["outcome"] for r in ticket_rows(tw, "outcomes")] == ["sent"]      # never corrected to not sent
    (d,) = out["ticket_disagreements"]
    assert d["record"] == "sent" and d["ledger"] == "nothing" and d["number_ends"] == number[-2:]
    assert "a person must look" in d["reason"]
    assert any(e.get("ticket_disagreements") for e in tw.entries("summary"))


# ---- closure (b): a ticket's expiry is sticky within a run

class BackClock(Clock):
    """A clock that, once `past` is set, reads `past` until the order step has judged the ticket on such a reading,
    then reads `back` (set back before the expiry) for the rest of the run."""

    def __init__(self, t):
        super().__init__(t)
        self.past = self.back = None
        self.judged_past = False

    def __call__(self):
        if self.past is not None:
            return self.back if self.judged_past else self.past
        return self.t


@pytest.mark.parametrize("where", ["after the first send", "at the first quote"])
def test_once_a_reading_is_past_the_expiry_no_later_reading_makes_the_ticket_live_again(tw, monkeypatch, where):
    tw.clock = BackClock(tw.clock.t)
    _, number = _proposed(tw, {"a": {"SPY": 10.0, "QQQ": 5.0, "IWM": 4.0}})
    (ticket,) = ticket_rows(tw, "tickets")
    tw.clock.t = ticket["expires"] - 300
    log = []
    real = osx._Run._ticket_expired

    def judged(self, now, t=None):
        answer = real(self, now, t)
        log.append((now, answer))
        if now > ticket["expires"]:
            tw.clock.judged_past = True
        return answer
    monkeypatch.setattr(osx._Run, "_ticket_expired", judged)

    def go_past():
        tw.clock.past, tw.clock.back = ticket["expires"] + 1, ticket["expires"] - 200
    if where == "after the first send":
        real_submit = tw.venue.submit

        def submit(*a, **k):
            answer = real_submit(*a, **k)
            go_past()
            return answer
        tw.venue.submit = submit
    else:
        real_quotes = tw.quotes

        def quotes(symbols):
            go_past()
            return real_quotes(symbols)
        tw.quotes = quotes
    out = approve(tw, number)
    first_past = next(i for i, (now, _) in enumerate(log) if now > ticket["expires"])
    later = log[first_past:]
    assert any(now < ticket["expires"] for now, _ in later), log           # the clock was set back afterwards ...
    assert all(answer for _, answer in later), log                          # ... and the ticket stayed expired
    assert len(tw.venue.submits()) == (1 if where == "after the first send" else 0), out


# ---- T22b invariant 1: without --live nothing changes, byte for byte, against T22b's base (T22a rev 3, 495c29f)

T22B_BASE = "495c29ff67ec84a7bb5b4c40c1471d67726f2d22"


@pytest.fixture(scope="module")
def t22b_base(tmp_path_factory):
    """The order step of T22b's base commit (the accepted ticket flow), from git, loaded under its own name."""
    root = Path(__file__).resolve().parents[2]
    src = subprocess.run(["git", "-C", str(root), "show", f"{T22B_BASE}:valedia_trader/execution_and_risk/order_step.py"],
                         capture_output=True, check=True).stdout
    folder = tmp_path_factory.mktemp("t22b_base_order_step")
    path = folder / "order_step_t22b_base.py"
    path.write_bytes(src)
    spec = importlib.util.spec_from_file_location("order_step_t22b_base", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["order_step_t22b_base"] = mod
    spec.loader.exec_module(mod)
    assert hasattr(mod, "TicketBook") and not hasattr(mod.TicketBook, "live_mark")
    return mod


def _flow_scenario(mod, tmp, seed, monkeypatch):
    """One generated scenario of the scheduled run and the paper ticket flow driven through `mod`: what the venue, the
    quote source, the journal, the ledger, the state file and the ticket file saw, run by run."""
    rng = random.Random(seed)
    urandom, numbers = random.Random(seed + 1), random.Random(seed + 2)
    monkeypatch.setattr("os.urandom", lambda n: bytes(urandom.getrandbits(8) for _ in range(n)))
    monkeypatch.setattr("secrets.randbelow", lambda n: numbers.randrange(n))
    held = {"OWN": float(rng.randint(0, 5)), "SPY": float(rng.choice([0, 0, 3]))}
    venue = KillVenue(rng, {s: u for s, u in held.items() if u}, cash=rng.choice([5_000.0, 50_000.0, 1e7]))
    w = ticket_world(tmp, venue)
    w.limits.write_text(json.dumps({**GENEROUS, "max_orders_per_day": rng.choice([1, 2, 3, 40])}))
    kw = {}
    if rng.random() < 0.3:
        kw["fractional"] = True
    if rng.random() < 0.4:
        kw["capital"] = rng.choice([50_000.0, 250_000.0])
    seen, last_number = [], None
    for day in range(2):
        w.venue.nice = day == 1
        w.paper = _random_paper(rng)
        for sym in ("SPY", "QQQ", "IWM"):
            w.prices[sym] = rng.choice([20.0, 55.5, 101.25])
            w.quote_age[sym] = rng.choice([60.0, 60.0, 1500.0])
        for run in range(rng.randint(2, 4)):
            if run:
                w.later(rng.choice([1, 1, 5, 20]))
            if rng.random() < 0.3:
                w.prices["SPY"] = round(w.prices["SPY"] * rng.choice([1.0, 1.003, 1.02]), 2)
            mode = rng.choice(["scheduled", "propose", "propose", "approve", "approve", "wrong"])
            if seen and seen[-1]["mode"] == "propose" and last_number and rng.random() < 0.6:
                mode = "approve"                                    # most tickets are approved in the next run
            run_kw = dict(kw)
            if mode == "propose":
                run_kw["propose"] = True
            elif mode == "approve":
                run_kw["approve"] = last_number or "000000"
            elif mode == "wrong":
                run_kw["approve"] = f"{numbers.randrange(1_000_000):06d}"
            w.venue.calls.clear()
            w.venue.killer = Killer(rng.choice([None, None, None, rng.randint(1, 25)]))
            try:
                out = _run_with(mod, w, **run_kw)
            except Crash as crash:
                out = {"crashed": str(crash)}
            w.venue.killer = None
            if out.get("ticket_number"):
                last_number = out["ticket_number"]
            seen.append({"mode": mode, "calls": list(w.venue.calls), "summary": json.dumps(out, sort_keys=True, default=str),
                         "journal": {f.name: _STAMP.sub(b'"timestamp": "T"', f.read_bytes())
                                     for f in sorted((w.tmp / "journal").glob("*"))},
                         "state": _dump(w.state_path), "ledger": _dump(w.ledger_path),
                         "tickets": _dump(osx.ticket_path_for(w.state_path)),
                         "files": sorted(str(p.relative_to(w.tmp)) for p in w.tmp.rglob("*") if p.is_file())})
            w.venue.market()
        w.next_day()
    monkeypatch.undo()
    return seen


@pytest.mark.parametrize("seed_block", range(6))
def test_without_live_the_scheduled_run_and_the_paper_ticket_flow_are_t22bs_base_byte_for_byte(tmp_path, monkeypatch,
                                                                                               t22b_base, seed_block):
    """50 scenarios per block, 300 in all, each driven through T22b's base and through this revision."""
    counts = {"submit": 0, "tickets": 0, "approved": 0, "crashed": 0}
    for k in range(50):
        seed = 440_000 + seed_block * 50 + k
        base = _flow_scenario(t22b_base, tmp_path / f"base{k}", seed, monkeypatch)
        mine = _flow_scenario(osx, tmp_path / f"mine{k}", seed, monkeypatch)
        assert len(base) == len(mine)
        for i, (b, m) in enumerate(zip(base, mine)):                    # T34b-2: equal but for `faults` (F1, F2)
            added = _t34_same_but(b, m, T34B2_PRACTICE_KEYS, (seed, i))
            if _t34_practice_marks(added, (seed, i)):
                print(f"T34B2-MARKED {seed} run {i}: {added.get('faults')} pending {added.get('reconcile_pending')}")
        counts["submit"] += sum(1 for r in mine for c in r["calls"] if c[0] == "submit")
        counts["tickets"] += sum(1 for r in mine if '"ticket_number"' in r["summary"])
        counts["approved"] += sum(1 for r in mine if r["mode"] == "approve" and '"orders_sent": 0' not in r["summary"]
                                  and '"status": "done"' in r["summary"])
        counts["crashed"] += sum(1 for r in mine if "crashed" in r["summary"])
        _shutil.rmtree(tmp_path / f"base{k}")
        _shutil.rmtree(tmp_path / f"mine{k}")
    assert counts["submit"] > 30 and counts["tickets"] > 30 and counts["approved"] > 5 and counts["crashed"] > 5, counts


# ---- T22b rulings at the order step: a refused paper approval today is no stopped run; a practice run stopped
# at another stage is

@pytest.mark.parametrize("stage, met", [("ticket", True), ("session", True), ("send", False), ("reconcile", False)])
def test_a_paper_stop_today_opens_or_closes_lock_two_by_its_stage(tmp_path, stage, met):
    w = live_world(tmp_path / "w")
    w.journal.write("2026-11-13", {"type": "order_step", "venue": gl.PAPER_VENUE, "batch": "practice", "event": "summary",
                                   "status": "stopped", "stage": stage, "mode": "approve"})
    w.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    out = lpropose(w)
    if met:
        assert out["status"] == "done" and out["ticket"]["orders"], out
    else:
        assert out["stage"] == "lock" and "lock 2 (" in out["reason"] and "min_days_since_a_stopped_run 1" in out["reason"]
        assert w.venue.calls == []



# ---- T22b rev 2, outcome 2: inside the step, live mode reads the owner's files and the paper record only from
# LIVE_REPO; a limits file or a journal anywhere else is refused before anything is read or written

def _snapshot(folder, skip=()):
    """Every file under the folder and its bytes, without the order ledger the harness itself opens around a run."""
    return {str(p.relative_to(folder)): (p.read_bytes() if p.is_file() else None) for p in sorted(folder.rglob("*"))
            if not any(str(p).startswith(str(s)) for s in skip)}


@pytest.mark.parametrize("moved", ["limits", "journal", "both"])
@pytest.mark.parametrize("mode", ["propose", "approve"])
def test_a_live_run_whose_limits_file_or_journal_is_not_the_repositorys_is_refused_before_anything(tmp_path, moved, mode):
    other = live_world(tmp_path / "other")                         # every lock met there
    w = live_world(tmp_path / "w")                                 # the repository: every lock met here too
    number = "123456"
    if mode == "approve":
        _, number = _live_proposed(w, {"a": {"IWM": 3.0}})
        w.later(1)
    w.venue.calls.clear()
    w.quote_calls.clear()
    w.second.calls.clear()
    if moved in ("limits", "both"):
        w.limits = other.limits
    if moved in ("journal", "both"):
        w.journal = other.journal
    w.ledger().close()                                             # the harness's own ledger (and its folder), made first
    skip = (w.ledger_path, other.ledger_path)
    before = (_snapshot(w.tmp, skip), _snapshot(other.tmp, skip))
    w.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    out = lpropose(w) if mode == "propose" else lapprove(w, number)
    assert out["status"] == "stopped" and out["stage"] == "lock" and "only from the repository" in out["reason"], out
    assert w.venue.calls == [] and w.quote_calls == [] and w.second.calls == []
    assert (_snapshot(w.tmp, skip), _snapshot(other.tmp, skip)) == before   # nothing written by the step


def test_a_live_run_given_an_object_whose_paths_point_elsewhere_reads_only_the_repository(tmp_path):
    other = live_world(tmp_path / "other")                         # every lock met there
    w = live_world(tmp_path / "w")
    for name in ("webull_live.json", "go_live.json"):
        (w.cfg / name).unlink()                                    # the repository has no confirmation, no checklist

    class Pointing:
        confirmation, checklist = other.cfg / "webull_live.json", other.cfg / "go_live.json"
        limits, journal, root, paper_venue = other.cfg / "risk.json", other.tmp / "journal", other.tmp, gl.PAPER_VENUE
        environ, calendar = dict(LIVE_ENV), CAL
    w.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    out = lpropose(w, live=Pointing())
    assert out["stage"] == "lock" and "lock 1 (" in out["reason"] and "lock 2 (" in out["reason"], out
    assert w.venue.calls == [] and w.quote_calls == []


# ==== T32: the broker book follows the strategies the owner chose (config/broker_book.json, read by the command line
# into a Follow: the hypothesis ids, their strategies' names, a digest of the file). Without one the step follows the
# whole book as before; with one, only those strategies, each an equal part of the capital: capital / (share x n).

H23, H26 = "trend_blend_etf_1999_2011", "hold_rebalance_etf_2006_2026"
TWO = {H23: {"SPY": 40.0, "QQQ": 10.0}, H26: {"SPY": 24.0, "TLT": 60.0}}


def _follow(*pairs, digest=None, every=None):
    kw = {} if every is None else {"every": every}            # rev 2: `every`, the list names the whole latest day
    return osx.Follow(ids=tuple(i for i, _ in pairs), names=tuple(n for _, n in pairs),
                      digest=digest or hashlib.sha256(repr(pairs).encode()).hexdigest(), **kw)


def two_book(equity=100_000.0, min_trade_pct=0.0, wanted=None):
    """The paper book as it is: two strategies, each stepped on half the equity."""
    return paper(TWO if wanted is None else wanted, equity=equity, share=equity / 2, min_trade_pct=min_trade_pct)


# ---- outcome 1: following 26 alone, on its own share

@pytest.mark.parametrize("capital", [None, 30_000.0])
def test_t32_following_26_alone_targets_its_wanted_units_times_capital_over_its_share(world, capital):
    world.paper = two_book()
    out = world.run(follow=_follow((26, H26)), **({} if capital is None else {"capital": capital}))
    scale = (100_000.0 if capital is None else capital) / 50_000.0          # the default capital is the paper equity
    assert out["status"] == "done", out
    assert {s: g["target"] for s, g in out["gaps"].items()} == {"SPY": 24.0 * scale, "TLT": 60.0 * scale}
    assert units_sent(world) == {"SPY": float(math.floor(24.0 * scale)), "TLT": float(math.floor(60.0 * scale))}
    assert {p["strategy"] for o in world.orders() for p in o["parts"]} == {H26}       # nothing of 23 is bought
    assert out["follow"] == {"ids": [26], "names": [H26]}
    (summary,) = world.entries("summary")
    assert summary["follow"] == {"ids": [26], "names": [H26]}


def test_t32_the_targets_function_follows_only_the_names_given_on_their_own_share():
    book = two_book()
    per_symbol, per_strategy = osx.targets(book, 30_000.0, follow=(H26,))
    assert per_strategy == {H26: {"SPY": 24.0 * (30_000.0 / 50_000.0), "TLT": 60.0 * (30_000.0 / 50_000.0)}}
    assert per_symbol == {"SPY": 24.0 * 0.6, "TLT": 60.0 * 0.6}
    assert osx.targets(book, 30_000.0)[1] == {H23: {"SPY": 40.0 * 0.3, "QQQ": 10.0 * 0.3},       # no list: the whole
                                              H26: {"SPY": 24.0 * 0.3, "TLT": 60.0 * 0.3}}       # book, on the equity


def test_t32_what_an_unfollowed_strategy_holds_at_the_broker_is_sold_to_zero_and_attributed_to_it(world):
    world.venue.fill_at_once = 1.0
    world.paper = two_book()
    out = world.run(capital=100_000.0)                                       # the whole book, as before: both bought
    assert out["status"] == "done" and world.book() == {"SPY": 64.0, "QQQ": 10.0, "TLT": 60.0}
    world.later()
    out = world.run(capital=50_000.0, follow=_follow((26, H26)))             # 26 alone on its share: a scale of 1
    assert out["status"] == "done", out
    sales = [o for o in world.orders() if o["tag"].startswith(out["batch"] + "-")]
    assert sorted((o["symbol"], o["units"], [p["strategy"] for p in o["parts"]]) for o in sales) == [
        ("QQQ", -10.0, [H23]), ("SPY", -40.0, [H23])]
    held = osx.strategy_holdings(world.orders())
    assert held[H26] == {"SPY": 24.0, "TLT": 60.0} and held[H23] == {"SPY": 0.0, "QQQ": 0.0}
    assert world.book() == {"SPY": 24.0, "QQQ": 0.0, "TLT": 60.0}


def test_t32_the_smallest_trade_follows_the_same_scale(world):
    # share 50,000, min_trade_pct 1%, capital 30,000, 26 alone: scale 0.6, smallest trade 0.01 x 50,000 x 0.6 = 300.
    # At capital / paper equity it would be 150, and the 2 TLT (200) would go.
    world.paper = two_book(min_trade_pct=0.01, wanted={H23: {"QQQ": 10.0}, H26: {"TLT": 4.0, "IWM": 5.0}})
    out = world.run(capital=30_000.0, follow=_follow((26, H26)))
    assert out["status"] == "done", out
    assert units_sent(world) == {"IWM": 3.0}                                 # 600 sent; QQQ is 23's, never bought
    (skip,) = [s for s in out["skipped"] if s["symbol"] == "TLT"]
    assert skip["reason"] == "worth 200.00, less than the smallest trade of 300.00"


# ---- outcome 2: following both ids is following the whole book, to the last float digit

def _random_two_book(rng):
    equity = rng.choice([10_000.0, 100_000.0, 123_456.78, 1_000_003.0, 7.5e6])
    wanted = {s: {sym: float(rng.choice([0, 0, 2, 5, 9, 12.5, -3, 0.37, 1e-7, 333.3333]))
                  for sym in rng.sample(["SPY", "QQQ", "IWM", "TLT"], rng.randint(1, 4))} for s in ("a", "b")}
    return paper(wanted, equity=equity, share=equity / 2, min_trade_pct=rng.choice([0.0, 0.002, 0.02, 0.05]))


def test_t32_the_targets_of_both_strategies_followed_are_the_whole_books_over_generated_books():
    rng = random.Random(3232)
    for _ in range(400):
        book = _random_two_book(rng)
        capital = rng.choice([1.0, 7_500.0, 50_000.0, 250_000.0, 99_999.99, book.equity])
        whole = osx.targets(book, capital)
        assert osx.targets(book, capital, follow=("a", "b")) == whole
        assert osx.targets(book, capital, follow=("b", "a")) == whole


def _strip_follow(entry):
    entry = dict(entry)
    entry.pop("follow", None)
    if isinstance(entry.get("ticket"), dict):
        entry["ticket"] = {k: v for k, v in entry["ticket"].items() if k != "follow"}
    return entry


def _both_scenario(tmp, seed, monkeypatch, follow):
    """One generated scenario of the scheduled run and the paper ticket flow on a two-strategy book, with both
    strategies followed or with no follow list: what the venue saw, the summaries and journal lines (the follow field
    taken out), the ledger, the state file and the ticket file, run by run."""
    rng = random.Random(seed)
    urandom, numbers = random.Random(seed + 1), random.Random(seed + 2)
    monkeypatch.setattr("os.urandom", lambda n: bytes(urandom.getrandbits(8) for _ in range(n)))
    monkeypatch.setattr("secrets.randbelow", lambda n: numbers.randrange(n))
    held = {"OWN": float(rng.randint(0, 5)), "SPY": float(rng.choice([0, 0, 3]))}
    venue = Booked({s: u for s, u in held.items() if u}, cash=rng.choice([5_000.0, 50_000.0, 1e7]))
    venue.fill_at_once = rng.choice([0.0, 0.5, 1.0])
    w = ticket_world(tmp, venue)
    w.limits.write_text(json.dumps({**GENEROUS, "max_orders_per_day": rng.choice([1, 2, 3, 40])}))
    kw = {}
    if rng.random() < 0.3:
        kw["fractional"] = True
    if rng.random() < 0.6:
        kw["capital"] = rng.choice([7_500.0, 50_000.0, 250_000.0])
    if follow:
        kw["follow"] = _follow((23, "a"), (26, "b"))
    seen, number = [], None
    for day in range(2):
        w.paper = _random_two_book(rng)
        for sym in ("SPY", "QQQ", "IWM", "TLT"):
            w.prices[sym] = rng.choice([20.0, 55.5, 101.25, 0.37])
            w.quote_age[sym] = rng.choice([60.0, 60.0, 1500.0])
        for run in range(rng.randint(1, 3)):
            if run:
                w.later(rng.choice([1, 5, 30]))
            mode = rng.choice(["scheduled", "scheduled", "propose", "approve"])
            if seen and seen[-1]["mode"] == "propose" and number and rng.random() < 0.7:
                mode = "approve"                                    # most tickets are approved in the next run
            run_kw = dict(kw)
            if mode == "propose":
                run_kw["propose"] = True
            elif mode == "approve":
                run_kw["approve"] = number or "000000"
            w.venue.calls.clear()
            out = w.run(**run_kw)
            if out.get("ticket_number"):
                number = out["ticket_number"]
            journal = {f.name: [_strip_follow(json.loads(line)) for line in
                                _STAMP.sub(b'"timestamp": "T"', f.read_bytes()).decode().splitlines()]
                       for f in sorted((w.tmp / "journal").glob("*"))}
            seen.append({"mode": mode, "calls": list(w.venue.calls),
                         "summary": _strip_follow(json.loads(json.dumps(out, default=str))),
                         "followed": out.get("follow"), "journal": journal, "state": _dump(w.state_path),
                         "ledger": _dump(w.ledger_path), "tickets": _dump(osx.ticket_path_for(w.state_path))})
        w.next_day()
    monkeypatch.undo()
    return seen


@pytest.mark.parametrize("seed_block", range(4))
def test_t32_following_both_ids_is_the_whole_book_over_generated_books(tmp_path, monkeypatch, seed_block):
    """50 scenarios per block, 200 in all: orders sent, smallest-trade decisions, journal lines and every file equal,
    apart from the new field, which names both strategies in every summary of the follow run."""
    counts = {"submit": 0, "smallest": 0, "runs": 0}
    for k in range(50):
        seed = 320_000 + seed_block * 50 + k
        whole = _both_scenario(tmp_path / f"whole{k}", seed, monkeypatch, follow=False)
        both = _both_scenario(tmp_path / f"both{k}", seed, monkeypatch, follow=True)
        assert len(whole) == len(both)
        for i, (a, b) in enumerate(zip(whole, both)):
            assert a["followed"] is None and b["followed"] == {"ids": [23, 26], "names": ["a", "b"]}, (seed, i)
            for key in a:
                if key != "followed":
                    assert a[key] == b[key], (seed, i, key)
        counts["submit"] += sum(1 for r in both for c in r["calls"] if c[0] == "submit")
        counts["smallest"] += sum(1 for r in both for s in r["summary"]["skipped"] if "smallest trade" in s["reason"])
        counts["runs"] += len(both)
        _shutil.rmtree(tmp_path / f"whole{k}")
        _shutil.rmtree(tmp_path / f"both{k}")
    assert counts["submit"] > 30 and counts["smallest"] > 10, counts


# ---- outcome 3, at the step: a refused or malformed follow list stops the run with nothing sent, changed or cancelled

BAD_FOLLOWS = {
    "refused": osx.Follow(refused="config/broker_book.json names hypothesis 99, which the research ledger does not hold"),
    "not a Follow": {"ids": (26,), "names": (H26,)},
    "no id": osx.Follow(ids=(), names=(), digest="0" * 64),
    "a bool id": osx.Follow(ids=(True,), names=(H26,), digest="0" * 64),
    "a text id": osx.Follow(ids=("26",), names=(H26,), digest="0" * 64),
    "a float id": osx.Follow(ids=(26.0,), names=(H26,), digest="0" * 64),
    "a negative id": osx.Follow(ids=(-26,), names=(H26,), digest="0" * 64),
    "a repeated id": osx.Follow(ids=(26, 26), names=(H26, H23), digest="0" * 64),
    "a repeated name": osx.Follow(ids=(23, 26), names=(H26, H26), digest="0" * 64),
    "fewer names": osx.Follow(ids=(23, 26), names=(H26,), digest="0" * 64),
    "an empty name": osx.Follow(ids=(26,), names=("",), digest="0" * 64),
    "no digest": osx.Follow(ids=(26,), names=(H26,)),
    "names as a list": osx.Follow(ids=[26], names=[H26], digest="0" * 64),
}


@pytest.mark.parametrize("how", sorted(BAD_FOLLOWS))
def test_t32_a_refused_or_malformed_follow_list_stops_the_run_with_nothing_sent_changed_or_cancelled(world, how):
    world.paper = two_book()
    out = world.run(capital=10_000.0)                                       # an open order of an earlier run
    assert out["status"] == "done" and world.venue.submits()
    open_before = [(o["tag"], o["state"]) for o in world.orders()]
    world.later()
    world.venue.calls.clear()
    world.quote_calls.clear()
    out = world.run(capital=10_000.0, follow=BAD_FOLLOWS[how])
    assert out["status"] == "stopped" and out["stage"] == "follow", out
    assert "broker book" in out["reason"] and "nothing is sent" in out["reason"], out
    if how == "refused":
        assert "hypothesis 99" in out["reason"]
    assert [c for c in world.venue.calls if c[0] in ("submit", "cancel", "positions", "cash", "order_status")] == []
    assert world.quote_calls == [] and [(o["tag"], o["state"]) for o in world.orders()] == open_before
    assert "follow" not in out


# ---- outcome 4, at the step: live mode never follows the whole book; the proposal says what it follows; a change
# of the follow list between propose and approve stops the approve

@pytest.mark.parametrize("mode", ["propose", "approve"])
@pytest.mark.parametrize("how", ["no follow list", "refused"])
def test_t32_live_mode_without_a_follow_list_stops_before_anything_is_read(lw, mode, how):
    number = "123456"
    if mode == "approve":
        _, number = _live_proposed(lw, {"a": {"IWM": 3.0}})
        lw.later(1)
    lw.venue.calls.clear()
    lw.quote_calls.clear()
    lw.second.calls.clear()
    lw.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    follow = None if how == "no follow list" else osx.Follow(refused="config/broker_book.json is missing")
    out = lpropose(lw, follow=follow) if mode == "propose" else lapprove(lw, number, follow=follow)
    assert out["status"] == "stopped" and out["stage"] == "lock" and "broker_book.json" in out["reason"], out
    assert "never follows the whole book" in out["reason"] or "is missing" in out["reason"]
    assert _account_reads(lw) == [] and lw.quote_calls == [] and lw.second.calls == [] and lw.venue.submits() == []
    if mode == "approve":
        assert ticket_rows(lw, "verdicts")[-1]["verdict"] == "refused"


def test_t32_a_live_proposal_says_what_it_follows_and_binds_the_ticket_to_it(lw):
    follow = _follow((26, "a"))
    lw.paper = paper({"a": {"IWM": 3.0, "TLT": 4.0}}, equity=5000.0)
    out = lpropose(lw, follow=follow)
    assert out["status"] == "done" and out["ticket"]["orders"], out
    assert out["follow"] == {"ids": [26], "names": ["a"]} and out["ticket"]["follow"] == {"ids": [26], "names": ["a"]}
    (entry,) = lw.entries("ticket")
    assert entry["follow"] == {"ids": [26], "names": ["a"]}
    (summary,) = [e for e in lw.entries("summary") if e["venue"] == "webull-production"]
    assert summary["follow"] == {"ids": [26], "names": ["a"]}
    meta = {r["key"]: json.loads(r["value"]) for r in _rows(lw, "SELECT key, value FROM meta") if ":" in r["key"]}
    number = out["ticket_number"]
    assert meta[f"broker_book:2026-11-13:{number}"] == {"digest": follow.digest, "ids": [26], "names": ["a"]}
    lw.later(1)
    out = lapprove(lw, number, follow=follow)                              # the same follow list: sent
    assert out["status"] == "done" and out["orders_sent"] == 2 and out["follow"] == {"ids": [26], "names": ["a"]}


@pytest.mark.parametrize("change", ["the file", "the ids", "the names"])
def test_t32_a_live_approve_after_the_follow_list_changed_is_refused_and_marked(lw, change):
    follow = _follow((26, "a"))
    lw.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    out = lpropose(lw, follow=follow)
    number = out["ticket_number"]
    later = {"the file": dataclasses.replace(follow, digest="f" * 64),
             "the ids": _follow((27, "a")), "the names": _follow((26, "a"), (27, "b"))}[change]
    lw.later(1)
    lw.venue.calls.clear()
    out = lapprove(lw, number, follow=later)
    assert out["status"] == "stopped" and out["stage"] == "ticket", out
    assert "broker book" in out["reason"] and "changed since that ticket was made" in out["reason"]
    assert lw.venue.submits() == [] and ticket_rows(lw, "verdicts")[-1]["verdict"] == "refused"


def test_t32_a_live_ticket_made_without_a_follow_mark_is_never_approved(lw, monkeypatch):
    lw.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    real = osx.TicketBook.create
    monkeypatch.setattr(osx.TicketBook, "create", lambda self, t, lines, live=None, **k: real(self, t, lines, live=live))
    out = lpropose(lw)
    number = out["ticket_number"]
    monkeypatch.setattr(osx.TicketBook, "create", real)
    lw.later(1)
    out = lapprove(lw, number)
    assert out["stage"] == "ticket" and "changed since that ticket was made" in out["reason"] and lw.venue.submits() == []


# ---- outcome 5: without a follow list the step is 76295f7's, byte for byte (the lead runs this one: it needs git)

T32_BASE = "76295f7"


@pytest.fixture(scope="module")
def t32_base(tmp_path_factory):
    """The order step of T32's base commit, from git, loaded under its own name."""
    root = Path(__file__).resolve().parents[2]
    src = subprocess.run(["git", "-C", str(root), "show", f"{T32_BASE}:valedia_trader/execution_and_risk/order_step.py"],
                         capture_output=True, check=True).stdout
    folder = tmp_path_factory.mktemp("t32_base_order_step")
    path = folder / "order_step_t32_base.py"
    path.write_bytes(src)
    spec = importlib.util.spec_from_file_location("order_step_t32_base", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["order_step_t32_base"] = mod
    spec.loader.exec_module(mod)
    assert hasattr(mod.TicketBook, "live_mark") and not hasattr(mod, "Follow")
    return mod


@pytest.mark.parametrize("seed_block", range(6))
def test_t32_without_a_follow_list_the_step_is_its_bases_byte_for_byte(tmp_path, monkeypatch, t32_base, seed_block):
    """50 scenarios per block, 300 in all, of the scheduled run and the paper ticket flow, through 76295f7 and here."""
    sends = 0
    for k in range(50):
        seed = 532_000 + seed_block * 50 + k
        base = _flow_scenario(t32_base, tmp_path / f"base{k}", seed, monkeypatch)
        mine = _flow_scenario(osx, tmp_path / f"mine{k}", seed, monkeypatch)
        assert len(base) == len(mine)
        for i, (b, m) in enumerate(zip(base, mine)):                    # T34b-2: equal but for `faults` (F1, F2)
            added = _t34_same_but(b, m, T34B2_PRACTICE_KEYS, (seed, i))
            if _t34_practice_marks(added, (seed, i)):
                print(f"T34B2-MARKED {seed} run {i}: {added.get('faults')} pending {added.get('reconcile_pending')}")
        sends += sum(1 for r in mine for c in r["calls"] if c[0] == "submit")
        _shutil.rmtree(tmp_path / f"base{k}")
        _shutil.rmtree(tmp_path / f"mine{k}")
    assert sends > 30


# ==== T32 rev 2

# ---- outcome 3: a list that names every strategy of the paper book's latest day is the whole book, to the last
# digit, for any number of strategies (the paper book steps each on equity / n, paper.py)

NAMES7 = ("s1", "s2", "s3", "s4", "s5", "s6", "s7")


def _random_book(rng, n):
    equity = rng.choice([10_000.0, 100_000.0, 123_456.78, 1_000_003.0, 7.5e6, 99_999.99,
                         6_519_413.8, 8_359_293.39, 3_753_283.17, 7_889_346.28])   # (equity / n) x n != equity
    wanted = {s: {sym: float(rng.choice([0, 0, 2, 5, 9, 12.5, -3, 0.37, 1e-7, 333.3333, 17.0, 1.0 / 3]))
                  for sym in rng.sample(["SPY", "QQQ", "IWM", "TLT"], rng.randint(1, 4))} for s in NAMES7[:n]}
    return paper(wanted, equity=equity, share=equity / max(n, 1), min_trade_pct=rng.choice([0.0, 0.002, 0.02, 0.05]))


def _every(n):
    return _follow(*[(k + 1, s) for k, s in enumerate(NAMES7[:n])], every=True)


@pytest.mark.parametrize("n", [2, 3, 5, 7])
def test_t32_rev2_every_strategy_followed_targets_on_the_paper_equity_over_generated_books(n):
    rng = random.Random(4_000 + n)
    differs = 0
    for _ in range(60):                                               # 240 books in all
        book = _random_book(rng, n)
        capital = rng.choice([1.0, 7_500.0, 50_000.0, 250_000.0, 99_999.99, book.equity])
        whole = osx.targets(book, capital)
        names = list(NAMES7[:n])
        rng.shuffle(names)
        assert osx.targets(book, capital, follow=tuple(names), every=True) == whole
        part = osx.targets(book, capital, follow=tuple(names))           # not every: capital / (share x n), as rev 1
        scale = capital / (book.share * n)
        assert part[1] == {s: {sym: u * scale for sym, u in b.items()} for s, b in book.wanted.items()}
        differs += book.share * n != book.equity
    assert n == 2 or differs > 0                                      # share x n is not the equity in some books


def _every_scenario(tmp, seed, monkeypatch, n, follow):
    """_both_scenario on an n-strategy book: every strategy followed (every=True), or no follow list."""
    rng = random.Random(seed)
    urandom, numbers = random.Random(seed + 1), random.Random(seed + 2)
    monkeypatch.setattr("os.urandom", lambda k: bytes(urandom.getrandbits(8) for _ in range(k)))
    monkeypatch.setattr("secrets.randbelow", lambda k: numbers.randrange(k))
    held = {"OWN": float(rng.randint(0, 5)), "SPY": float(rng.choice([0, 0, 3]))}
    venue = Booked({s: u for s, u in held.items() if u}, cash=rng.choice([5_000.0, 50_000.0, 1e7]))
    venue.fill_at_once = rng.choice([0.0, 0.5, 1.0])
    w = ticket_world(tmp, venue)
    w.limits.write_text(json.dumps({**GENEROUS, "max_orders_per_day": rng.choice([1, 2, 3, 40])}))
    kw = {}
    if rng.random() < 0.3:
        kw["fractional"] = True
    if rng.random() < 0.6:
        kw["capital"] = rng.choice([7_500.0, 50_000.0, 250_000.0, 333_333.0])
    if follow:
        kw["follow"] = _every(n)
    seen, number = [], None
    for day in range(2):
        w.paper = _random_book(rng, n)
        for sym in ("SPY", "QQQ", "IWM", "TLT"):
            w.prices[sym] = rng.choice([20.0, 55.5, 101.25, 0.37, 7.77])
            w.quote_age[sym] = rng.choice([60.0, 60.0, 1500.0])
        for run in range(rng.randint(1, 3)):
            if run:
                w.later(rng.choice([1, 5, 30]))
            mode = rng.choice(["scheduled", "scheduled", "propose", "approve"])
            if seen and seen[-1]["mode"] == "propose" and number and rng.random() < 0.7:
                mode = "approve"
            run_kw = dict(kw)
            if mode == "propose":
                run_kw["propose"] = True
            elif mode == "approve":
                run_kw["approve"] = number or "000000"
            w.venue.calls.clear()
            out = w.run(**run_kw)
            if out.get("ticket_number"):
                number = out["ticket_number"]
            journal = {f.name: [_strip_follow(json.loads(line)) for line in
                                _STAMP.sub(b'"timestamp": "T"', f.read_bytes()).decode().splitlines()]
                       for f in sorted((w.tmp / "journal").glob("*"))}
            seen.append({"mode": mode, "calls": list(w.venue.calls),
                         "summary": _strip_follow(json.loads(json.dumps(out, default=str))),
                         "followed": out.get("follow"), "journal": journal, "state": _dump(w.state_path),
                         "ledger": _dump(w.ledger_path), "tickets": _dump(osx.ticket_path_for(w.state_path))})
        w.next_day()
    monkeypatch.undo()
    return seen


@pytest.mark.parametrize("n", [2, 3, 5, 7])
def test_t32_rev2_following_every_strategy_is_the_whole_book_over_generated_books(tmp_path, monkeypatch, n):
    """50 scenarios per n, 200 in all: orders sent, smallest-trade decisions, journal lines and every file equal,
    apart from the follow field."""
    counts = {"submit": 0, "smallest": 0}
    for k in range(50):
        seed = 640_000 + n * 1_000 + k
        whole = _every_scenario(tmp_path / f"whole{k}", seed, monkeypatch, n, follow=False)
        every = _every_scenario(tmp_path / f"every{k}", seed, monkeypatch, n, follow=True)
        assert len(whole) == len(every)
        for i, (a, b) in enumerate(zip(whole, every)):
            assert a["followed"] is None and b["followed"] == {"ids": list(range(1, n + 1)), "names": list(NAMES7[:n])}
            for key in a:
                if key != "followed":
                    assert a[key] == b[key], (seed, i, key)
        counts["submit"] += sum(1 for r in every for c in r["calls"] if c[0] == "submit")
        counts["smallest"] += sum(1 for r in every for s in r["summary"]["skipped"] if "smallest trade" in s["reason"])
        _shutil.rmtree(tmp_path / f"whole{k}")
        _shutil.rmtree(tmp_path / f"every{k}")
    assert counts["submit"] > 30 and counts["smallest"] > 5, counts


def test_t32_rev2_every_must_be_true_or_false(world):
    world.paper = two_book()
    out = world.run(capital=10_000.0, follow=_follow((26, H26), every="yes"))
    assert out["status"] == "stopped" and out["stage"] == "follow" and world.venue.submits() == [], out


# ---- outcome 6: the kinds judge_follow names read as English

@pytest.mark.parametrize("raw", [26, 10**30, 2.5, "26", True, False, None, [26], {"a": 1}])
def test_t32_rev2_every_kind_the_follow_judge_names_reads_as_english(raw):
    import re
    whys = [osx.judge_follow(raw)[1], osx.judge_follow({"follow": raw})[1], osx.judge_follow({"follow": [raw]})[1]]
    for why in whys:
        if why is None:
            continue
        assert not re.search(r"\ba [aeiouAEIOU]", why), why
        for bad in (" a int", " a bool", " a str", " a float", " a NoneType", " a dict", "a bytes"):
            assert bad not in why, why
        assert len(why) < 300
    assert "not the number 26" in osx.judge_follow(26)[1]
    assert "not true or false" in osx.judge_follow(True)[1]


@pytest.mark.parametrize("equity, n", [(3_972_837.94, 3), (3_663_232.28, 3), (6_392_745.55, 5), (2_903_196.77, 5),
                                      (3_698_843.19, 7), (7_801_895.21, 7)])
def test_t32_rev2_every_strategy_followed_keeps_the_whole_books_smallest_trade_at_its_edge(tmp_path, equity, n):
    """A difference worth exactly the smaller of the two smallest trades (capital / equity, and capital / (share x n),
    which differ in the last digit for these books): followed whole, it is decided as the whole book decides it."""
    capital, pct = 50_000.0, 0.02
    book = paper({**{s: {"TLT": 5.0} for s in NAMES7[1:n]}, "s1": {"SPY": 1.2 * equity / capital}},
                 equity=equity, share=equity / n, min_trade_pct=pct)
    whole_min = book.min_trade_pct * book.share * capital / book.equity
    part_min = book.min_trade_pct * book.share * capital / (float(book.share) * n)
    assert whole_min != part_min
    decided = []
    for k, follow in enumerate((None, _every(n))):
        w = World(tmp_path / f"w{k}")
        w.paper = book
        w.prices["SPY"] = min(whole_min, part_min)                    # 1 share, worth exactly the smaller of the two
        out = w.run(capital=capital, **({} if follow is None else {"follow": follow}))
        assert out["status"] == "done", out
        decided.append(("SPY" in units_sent(w), [s["reason"] for s in out["skipped"] if s["symbol"] == "SPY"]))
    assert decided[0] == decided[1], decided


# ==== T22d-1: the order step before real money (findings A1, A2, A3, A4, A6 of the off-family review)

import fcntl as _fcntl
from collections import Counter as _Counter


def _later_submit(w, minutes, then=None):
    """The venue's submit, after which the clock moves on `minutes` and `then(w)` runs (a price that moves, a lock
    file that goes): what a slow broker call and the world outside do between two orders of one run."""
    real = w.venue.submit

    def submit(*a, **k):
        out = real(*a, **k)
        w.clock.t += minutes * 60
        if then is not None:
            then(w)
        return out
    w.venue.submit = submit


def _t22d1_equity(w):
    if not Path(w.state_path).exists():
        return []
    con = sqlite3.connect(w.state_path)
    try:
        return [r[0] for r in con.execute("SELECT equity FROM equity ORDER BY id")]
    finally:
        con.close()


# ---- outcome 1 (A1): in live mode no mark is believed on one source

@pytest.mark.parametrize("verdict, words", [("disagree", "does not agree with"),
                                            ("no second quote", "cannot answer for"),
                                            (ValueError("boom"), "cannot answer for"),
                                            ("nonsense", "cannot answer for")])
@pytest.mark.parametrize("mode", ["propose", "approve"])
def test_t22d1_a_baseline_mark_the_second_source_does_not_back_stops_the_live_run_before_any_order(tmp_path, mode,
                                                                                                 verdict, words):
    w = live_world(tmp_path / "w", venue=LiveVenue(held={"OWN": 3.0}))           # the owner's holding: the baseline
    number = None
    if mode == "approve":
        _, number = _live_proposed(w, {"a": {"IWM": 3.0}})
        w.later(1)
    rows = _t22d1_equity(w)
    w.second.calls.clear()
    w.second.verdicts = {"OWN": verdict}
    w.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    out = lpropose(w) if mode == "propose" else lapprove(w, number)
    assert out["status"] == "stopped" and out["stage"] == "marks", out
    assert "OWN" in out["reason"] and words in out["reason"] and "nothing is sent" in out["reason"], out["reason"]
    assert w.venue.submits() == [] and w.orders() == [] and "ticket_number" not in out
    assert [(c["symbol"], c["price"]) for c in w.second.calls] == [("OWN", 50.0)]   # the order's own is never reached
    assert _t22d1_equity(w) == rows                                                   # no equity, no peak recorded
    assert any(e["stage"] == "marks" for e in w.entries("stopped"))
    if mode == "propose":
        assert [t for t in ticket_rows(w, "tickets")] == []
    else:                                                          # the ticket is not consumed: a later approve sends it
        assert [f["fate"] for f in ticket_rows(w, "fates")] == []
        w.second.verdicts = {}
        out = lapprove(w, number)
        assert out["status"] == "done" and out["orders_sent"] == 1, out


def test_t22d1_a_mark_of_this_ledgers_own_holding_is_judged_though_that_symbol_is_not_ordered(tmp_path):
    w = live_world(tmp_path / "w")
    w.venue.fill_at_once = 1.0
    _, number = _live_proposed(w, {"a": {"IWM": 3.0}})
    w.later(1)
    assert lapprove(w, number)["orders_sent"] == 1 and w.book() == {"IWM": 3.0}
    w.later(20)
    w.second.calls.clear()
    w.second.verdicts = {"IWM": "disagree"}
    w.paper = paper({"a": {"IWM": 3.0, "TLT": 4.0}}, equity=5000.0)                 # IWM held, only TLT wanted
    out = lpropose(w)
    assert out["status"] == "stopped" and out["stage"] == "marks" and "IWM" in out["reason"], out
    assert [c["symbol"] for c in w.second.calls] == ["IWM"] and "ticket_number" not in out
    w.second.verdicts = {}
    w.second.calls.clear()
    out = lpropose(w)                                              # agreed: the mark and then the order's own quote
    assert out["status"] == "done" and [c["symbol"] for c in w.second.calls] == ["IWM", "TLT"], out


def test_t22d1_a_held_mark_a_refresh_replaces_is_judged_again_before_the_gate_or_a_limit_uses_it(tmp_path):
    w = live_world(tmp_path / "w", venue=LiveVenue(held={"OWN": 3.0}))
    _, number = _live_proposed(w, {"a": {"IWM": 3.0, "TLT": 4.0}})
    w.later(1)
    w.quote_age["OWN"] = 19.5 * 60                                 # fresh at the start, too old two minutes on
    w.second.calls.clear()
    w.second.by_price = {("OWN", 51.0): "disagree"}
    _later_submit(w, 2, then=lambda w: w.prices.__setitem__("OWN", 51.0))
    out = lapprove(w, number)
    assert out["status"] == "stopped" and out["stage"] == "marks" and "OWN" in out["reason"], out
    assert "51" in out["reason"] and "does not agree with" in out["reason"]
    assert [w.venue.book[t]["symbol"] for t in w.venue.submits()] == ["IWM"]       # TLT never intended, never sent
    assert [o["symbol"] for o in w.orders()] == ["IWM"]
    assert [(c["symbol"], c["price"]) for c in w.second.calls] == [("OWN", 50.0), ("IWM", 200.0), ("OWN", 51.0)]
    gate = [e for e in w.entries("refused") if e.get("by") == "risk gate"]
    assert gate == []


def test_t22d1_a_refreshed_mark_that_agrees_lets_the_run_go_on_and_each_quote_is_asked_once(tmp_path):
    w = live_world(tmp_path / "w", venue=LiveVenue(held={"OWN": 3.0, "IWM": 1.0}))
    _, number = _live_proposed(w, {"a": {"IWM": 3.0, "TLT": 4.0}})
    w.later(1)
    w.quote_age["OWN"] = 19.5 * 60
    w.second.calls.clear()
    _later_submit(w, 2, then=lambda w: w.prices.__setitem__("OWN", 51.0))
    out = lapprove(w, number)
    assert out["status"] == "done" and out["orders_sent"] == 2, out
    asked = [(c["symbol"], c["price"]) for c in w.second.calls]
    assert asked == [("IWM", 200.0), ("OWN", 50.0), ("OWN", 51.0), ("TLT", 100.0)]   # IWM's held mark is its order's
    assert len(asked) == len(set(asked))


# ---- outcome 2 (A3): exposure is never older than the marks

def test_t22d1_max_capital_sees_a_held_symbols_new_price_after_a_refresh(tmp_path):
    w = live_world(tmp_path / "w", conf=live_conf(max_capital=1600, max_order_value=1000))
    w.venue.fill_at_once = 1.0
    _, number = _live_proposed(w, {"a": {"IWM": 3.0}}, capital=1600.0)
    w.later(1)
    assert lapprove(w, number, capital=1600.0)["orders_sent"] == 1 and w.book() == {"IWM": 3.0}
    w.later(20)
    _, number = _live_proposed(w, {"a": {"IWM": 3.0, "GLD": 2.0, "TLT": 4.0}}, capital=1600.0)
    lines = [ln["symbol"] for ln in ticket_rows(w, "lines") if ln["number"] == number]
    assert sorted(lines) == ["GLD", "TLT"]                         # 600 + 505 + 404 = 1,509 at the start's marks
    w.later(1)
    w.quote_age["IWM"] = 19.5 * 60
    _later_submit(w, 2, then=lambda w: w.prices.__setitem__("IWM", 240.0))
    out = lapprove(w, number, capital=1600.0)
    assert [w.venue.book[t]["symbol"] for t in w.venue.submits()] == ["IWM", "GLD"], out
    (d,) = out["dropped"]                                          # 3 x 240 + 505 + 404 = 1,629: TLT is over
    assert d["symbol"] == "TLT" and "1,629.00, above max_capital 1,600.00" in d["reason"], d


# ---- outcome 3 (A2): the run lock follows the file

def test_t22d1_a_live_run_whose_lock_file_went_after_it_took_it_stops_at_the_start(tmp_path, monkeypatch):
    real = osx._RunLock.acquire

    def acquire(self):
        ok = real(self)
        if ok and self.path is not None:
            Path(self.path).unlink()
        return ok
    w = live_world(tmp_path / "w")
    w.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    monkeypatch.setattr(osx._RunLock, "acquire", acquire)
    out = lpropose(w)
    assert out["status"] == "stopped" and out["stage"] == "lock" and "run lock" in out["reason"], out
    assert "order_step-webull-production.sqlite.lock" in out["reason"], out["reason"]
    assert _account_reads(w) == [] and w.quote_calls == [] and w.second.calls == []
    p = ticket_world(tmp_path / "p")                               # a practice run is as on the base: it goes on
    p.paper = paper({"a": {"SPY": 2.0}})
    assert p.run()["status"] == "done" and len(p.venue.submits()) == 1


@pytest.mark.parametrize("how", ["unlinked", "replaced by a second run's"])
def test_t22d1_a_lock_file_removed_or_replaced_during_a_live_run_stops_it_before_its_next_send(lw, how):
    _, number = _live_proposed(lw, {"a": {"IWM": 3.0, "TLT": 4.0}})
    lw.later(1)
    path, kept = Path(str(lw.state_path) + ".lock"), []

    def lose_the_lock(w):
        if kept:
            return
        path.unlink()
        fh = None
        if how != "unlinked":                                      # a second run starts on a new lock file
            fh = open(path, "a+")
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        kept.append(fh)
    _later_submit(lw, 0, then=lose_the_lock)
    try:
        out = lapprove(lw, number)
    finally:
        for fh in kept:
            if fh is not None:
                fh.close()
    assert out["status"] == "stopped" and out["stage"] == "lock" and "run lock" in out["reason"], out
    assert [lw.venue.book[t]["symbol"] for t in lw.venue.submits()] == ["IWM"]
    assert {o["symbol"]: o["state"] for o in lw.orders()}["TLT"] == "intended"     # never sent
    assert any(x["symbol"] == "TLT" and "run lock" in x["reason"] for x in out["left_over"])


# ---- outcome 4 (A4): an older state file cannot reopen the day

def test_t22d1_a_state_file_that_counts_fewer_orders_today_than_the_ledger_stops_the_live_run(lw):
    _, number = _live_proposed(lw, {"a": {"IWM": 3.0, "TLT": 4.0}})
    older = lw.tmp / "older-state.sqlite"
    _shutil.copy(lw.state_path, older)                             # the state file before the approve
    lw.later(1)
    assert lapprove(lw, number)["orders_sent"] == 2
    _shutil.copy(older, lw.state_path)                             # ... put back by a person
    lw.later(20)
    lw.venue.calls.clear()
    lw.paper = paper({"a": {"IWM": 3.0, "TLT": 4.0, "GLD": 2.0}}, equity=5000.0)
    out = lpropose(lw)
    assert out["status"] == "stopped" and out["stage"] == "state", out
    assert "0 orders" in out["reason"] and "2" in out["reason"] and "2026-11-13" in out["reason"], out["reason"]
    assert [c for c in lw.venue.calls if c[0] in ("cancel", "submit", "positions", "cash")] == []
    assert "ticket_number" not in out


def test_t22d1_a_state_file_whose_peak_is_below_an_equity_the_journal_recorded_stops_the_live_run(lw):
    lw.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    assert lpropose(lw)["status"] == "done"
    older = lw.tmp / "older-state.sqlite"
    _shutil.copy(lw.state_path, older)                             # its peak: 1,000,000.00
    lw.later(20)
    lw.venue.cash_value += 2500.0
    assert lpropose(lw)["broker_equity"] == 1_002_500.0            # journaled, and the state's peak
    _shutil.copy(older, lw.state_path)
    lw.later(20)
    out = lpropose(lw)
    assert out["status"] == "stopped" and out["stage"] == "state", out
    assert "1,000,000.00" in out["reason"] and "1,002,500.00" in out["reason"], out["reason"]
    assert "ticket_number" not in out and lw.venue.submits() == []


def test_t22d1_a_state_file_that_knows_as_much_as_the_ledger_and_the_journal_lets_the_live_run_go_on(lw):
    _, number = _live_proposed(lw, {"a": {"IWM": 3.0, "TLT": 4.0}})
    lw.later(1)
    assert lapprove(lw, number)["orders_sent"] == 2
    lw.later(20)
    lw.venue.cash_value -= 2500.0                                  # a lower equity than the journal's: a real loss
    lw.paper = paper({"a": {"IWM": 3.0, "TLT": 4.0, "GLD": 2.0}}, equity=5000.0)
    out = lpropose(lw)
    assert out["status"] == "done" and out["ticket"]["orders"], out


def test_t22d1_orders_sent_on_an_earlier_new_york_day_do_not_count_against_today(lw):
    _, number = _live_proposed(lw, {"a": {"IWM": 3.0, "TLT": 4.0}})
    lw.later(1)
    assert lapprove(lw, number)["orders_sent"] == 2
    lw.next_day()                                                  # the state counts 0 today; the ledger's 2 are older
    lw.paper = paper({"a": {"IWM": 3.0, "TLT": 4.0, "GLD": 2.0}}, equity=5000.0)
    out = lpropose(lw)
    assert out["status"] == "done" and out["ticket"]["orders"], out


# ---- outcome 5 (A6): live mode needs the settled-cash figure

@pytest.mark.parametrize("settled", [None, "missing"])
@pytest.mark.parametrize("mode", ["propose", "approve"])
def test_t22d1_a_live_run_stops_when_the_account_read_gives_no_settled_cash(tmp_path, mode, settled):
    w = live_world(tmp_path / "w")
    number = None
    if mode == "approve":
        _, number = _live_proposed(w, {"a": {"IWM": 3.0}})
        w.later(1)
    w.venue.settled = settled
    w.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    out = lpropose(w) if mode == "propose" else lapprove(w, number)
    assert out["status"] == "stopped" and out["stage"] == "prices" and "settled cash" in out["reason"], out
    assert "never spends on the cash figure" in out["reason"]
    assert w.venue.submits() == [] and w.orders() == [] and "ticket_number" not in out
    if mode == "approve":
        assert [f["fate"] for f in ticket_rows(w, "fates")] == []  # not consumed


# ---- outcome 7: what the marks cost, for a fifteen-symbol book

FIFTEEN = tuple(f"S{k:02d}" for k in range(15))


def _fifteen_world(tmp):
    w = live_world(tmp, venue=LiveVenue(held={s: 1.0 for s in FIFTEEN}))
    for k, s in enumerate(FIFTEEN):
        w.prices[s] = 100.0 + 10 * k
    w.paper = paper({"a": {s: 1.0 for s in FIFTEEN}}, equity=5000.0)
    return w


def test_t22d1_a_fifteen_symbol_book_asks_once_per_symbol_in_the_usual_run(tmp_path):
    w = _fifteen_world(tmp_path / "w")
    out = lpropose(w)
    assert out["status"] == "done" and len(out["ticket"]["orders"]) == 15, out
    assert sorted(c["symbol"] for c in w.second.calls) == list(FIFTEEN)            # 15: each held mark, once
    w.second.calls.clear()
    w.later(1)
    out = lapprove(w, out["ticket_number"])
    assert out["status"] == "done" and out["orders_sent"] == 15, out
    assert sorted(c["symbol"] for c in w.second.calls) == list(FIFTEEN)


def test_t22d1_a_slow_fifteen_symbol_run_asks_once_per_symbol_per_quote_and_no_more(tmp_path):
    w = _fifteen_world(tmp_path / "w")
    number = lpropose(w)["ticket_number"]
    w.later(0.1)
    for s in FIFTEEN:
        w.quote_age[s] = 19.5 * 60                                 # every quote goes stale after each slow submit
    real, answered = w.quotes, []

    def quotes(symbols):                                           # every answer a new quote, a cent higher
        out = real(symbols)
        for s in symbols:
            w.prices[s] = round(w.prices[s] + 0.01, 2)
        answered.extend((s, q[0]) for s, q in out.items())
        return out
    w.quotes = quotes
    w.second.calls.clear()
    _later_submit(w, 1)
    out = lapprove(w, number)
    assert out["status"] == "done" and out["orders_sent"] == 15, out
    asked = [(c["symbol"], c["price"]) for c in w.second.calls]
    assert len(asked) == len(set(asked))                           # never the same quote twice
    assert _Counter(asked) == _Counter(answered)                   # every quote answered, once
    assert len(asked) == 15 + 14 * 15                              # 225: every holding again before each later order


# ---- outcome 6: nothing else moves (practice runs byte for byte; live runs apart from the marks asked)

T22D1_BASE = "7c2ec4a"


@pytest.fixture(scope="module")
def t22d1_base(tmp_path_factory):
    """The order step of T22d-1's base commit, from git (or, in a copy of the tree without git, the file named by
    T22D1_BASE_FILE), loaded under its own name."""
    root = Path(__file__).resolve().parents[2]
    got = subprocess.run(["git", "-C", str(root), "show", f"{T22D1_BASE}:valedia_trader/execution_and_risk/order_step.py"],
                         capture_output=True)
    if got.returncode == 0:
        src = got.stdout
    elif _os.environ.get("T22D1_BASE_FILE"):
        src = Path(_os.environ["T22D1_BASE_FILE"]).read_bytes()
    else:
        raise AssertionError(f"the base {T22D1_BASE} cannot be read: {got.stderr.decode(errors='replace')}")
    folder = tmp_path_factory.mktemp("t22d1_base_order_step")
    path = folder / "order_step_t22d1_base.py"
    path.write_bytes(src)
    spec = importlib.util.spec_from_file_location("order_step_t22d1_base", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["order_step_t22d1_base"] = mod
    spec.loader.exec_module(mod)
    assert hasattr(mod, "Follow") and not hasattr(mod._Run, "_judge_marks")
    return mod


@pytest.mark.parametrize("seed_block", range(2))
def test_t22d1_practice_runs_are_the_bases_byte_for_byte(tmp_path, monkeypatch, t22d1_base, seed_block):
    """25 scenarios per block, 50 in all (each 4 to 8 runs: scheduled, propose, approve, wrong numbers, kills, partial
    fills, refusals), through 7c2ec4a and here: venue calls, summaries, journal lines, ledger, state and tickets."""
    runs = sends = 0
    for k in range(25):
        seed = 770_000 + seed_block * 25 + k
        base = _flow_scenario(t22d1_base, tmp_path / f"base{k}", seed, monkeypatch)
        mine = _flow_scenario(osx, tmp_path / f"mine{k}", seed, monkeypatch)
        assert len(base) == len(mine)
        for i, (b, m) in enumerate(zip(base, mine)):                    # T34b-2: equal but for `faults` (F1, F2)
            added = _t34_same_but(b, m, T34B2_PRACTICE_KEYS, (seed, i))
            if _t34_practice_marks(added, (seed, i)):
                print(f"T34B2-MARKED {seed} run {i}: {added.get('faults')} pending {added.get('reconcile_pending')}")
        runs += len(mine)
        sends += sum(1 for r in mine for c in r["calls"] if c[0] == "submit")
        _shutil.rmtree(tmp_path / f"base{k}")
        _shutil.rmtree(tmp_path / f"mine{k}")
    assert runs >= 100 and sends > 10, (runs, sends)


def _live_flow(mod, tmp, seed, monkeypatch):
    """One generated live scenario that meets none of T22d-1's five conditions (every held mark agreed on, settled
    cash given, prices still within a run, the lock and the state file left alone), driven through `mod`."""
    rng = random.Random(seed)
    urandom, numbers = random.Random(seed + 1), random.Random(seed + 2)
    monkeypatch.setattr("os.urandom", lambda n: bytes(urandom.getrandbits(8) for _ in range(n)))
    monkeypatch.setattr("secrets.randbelow", lambda n: numbers.randrange(n))
    cap = rng.choice([800, 2500, 6000])
    mov = rng.choice([m for m in (150, 400, 900, 2500) if m <= cap])
    held = rng.choice([None, {"OWN": float(rng.randint(1, 4))}, {"OWN": 2.0, "QQQ": 1.0}])
    w = live_world(tmp, venue=LiveVenue(held=held), conf=live_conf(max_order_value=mov, max_capital=cap))
    capital = float(rng.choice([cap, round(cap * rng.uniform(0.3, 1.0), 2)]))
    verdicts = ["agree", "agree", "agree", "disagree", "no second quote", "nonsense"]
    seen = []

    def one(**kw):
        f = _follow_the_book(w)
        before = set(w.book()) | set(held or {})
        w.second.verdicts = {s: ("agree" if s in before else rng.choice(verdicts)) for s in ("SPY", "QQQ", "IWM", "TLT", "GLD")}
        w.second.calls.clear()
        w.venue.calls.clear()
        out = _run_with(mod, w, live=w.live, second_opinion=w.second, capital=capital,
                        follow=mod.Follow(**dataclasses.asdict(f)), **kw)
        seen.append({"calls": list(w.venue.calls), "summary": json.dumps(out, sort_keys=True, default=str),
                     "journal": {f.name: _STAMP.sub(b'"timestamp": "T"', f.read_bytes())
                                 for f in sorted((w.tmp / "journal").glob("*"))},
                     "state": _dump(w.state_path), "ledger": _dump(w.ledger_path),
                     "tickets": _dump(osx.ticket_path_for(w.state_path)),
                     "second": [(c["symbol"], c["price"]) for c in w.second.calls], "held": sorted(before)})
        return out
    for _ in range(3):
        for sym in ("SPY", "QQQ", "IWM", "TLT", "GLD"):
            w.prices[sym] = rng.choice([20.0, 55.5, 101.25, 230.0, 480.0])
        wanted = {s: float(rng.randint(0, 6)) for s in rng.sample(["SPY", "QQQ", "IWM", "TLT", "GLD"], rng.randint(1, 5))}
        w.paper = paper({"a": wanted}, equity=capital)
        number = one(propose=True).get("ticket_number")
        if number:
            w.venue.fill_at_once = rng.choice([0.0, 0.5, 1.0])
            w.later(1)
            one(approve=number)
        w.later(20)
    monkeypatch.undo()
    return seen


@pytest.mark.parametrize("seed_block", range(2))
def test_t22d1_live_runs_that_meet_none_of_the_five_are_the_bases_apart_from_the_marks_asked(tmp_path, monkeypatch,
                                                                                            t22d1_base, seed_block):
    """25 scenarios per block, 50 in all, of up to six live runs each, through 7c2ec4a and here."""
    added = sent = 0
    for k in range(25):
        seed = 880_000 + seed_block * 25 + k
        base = _live_flow(t22d1_base, tmp_path / f"base{k}", seed, monkeypatch)
        mine = _live_flow(osx, tmp_path / f"mine{k}", seed, monkeypatch)
        assert len(base) == len(mine)
        for i, (b, m) in enumerate(zip(base, mine)):                    # T34b-2: but for the live summary keys
            keys = _t34_same_but(b, m, T34B2_LIVE_KEYS, (seed, i), skip=("second",))
            assert "ceiling" in keys and "day_stop" not in keys, (seed, i, keys)             # no earlier day: no stop
            more = _Counter(m["second"]) - _Counter(b["second"])     # what this revision asks beyond the base:
            assert not (_Counter(b["second"]) - _Counter(m["second"])), (seed, i)
            assert all(sym in m["held"] for sym, _ in more), (seed, i, more)   # only held marks
            assert len(m["second"]) == len(set(m["second"])), (seed, i)        # each quote once
            added += sum(more.values())
        sent += sum(1 for r in mine for c in r["calls"] if c[0] == "submit")
        _shutil.rmtree(tmp_path / f"base{k}")
        _shutil.rmtree(tmp_path / f"mine{k}")
    assert added > 10 and sent > 10, (added, sent)


# ==== T34b-2: the order step writes its faults and its ceiling, and obeys a stop that lasts the day
#
# docs/specs/2026-11-14-caps-ladder-design.md, D4 (the ceiling), D5, D6, D8. The summary gains `faults` (F1 the final
# reconcile not clean, F2 a blocking attention item opened in the run, F3 on live runs a breached cap) and, on live
# runs, `ceiling`, `working_stop` and `day_stop` (and `cap_unjudged`). A practice run is otherwise untouched.

import copy as _copy
import statistics as _statistics

from valedia_trader.execution_and_risk import stake_ladder as _sl
from valedia_trader.research_loop import decay as _dc

T34B2_PRACTICE_KEYS = ("faults", "reconcile_pending", "cap_unjudged")
T34B2_LIVE_KEYS = ("faults", "reconcile_pending", "cap_unjudged", "ceiling", "over_ceiling", "working_stop", "day_stop")
T34B2_BASE = "f88593e"


def _t34_positions_fail(w, at):
    """The venue's positions read fails at the calls numbered in `at` (1 = the next call), and only there."""
    real, count = w.venue.positions, [0]

    def positions():
        count[0] += 1
        if count[0] in at:
            w.venue.calls.append(("positions", None))
            raise ConnectionError("positions is down")
        return real()
    w.venue.positions = positions


def _t34_flag(w, kinds, prefix="x"):
    """Open one attention item per kind in the order ledger's file, through the ledger's own write, as the ledger opens
    them (a separate connection, the way another reconcile would)."""
    led = w.ledger()
    try:
        with led._write():
            for k, kind in enumerate(kinds):
                led._flag(f"{prefix}-{k}", kind, f"made by the test ({k})")
        return [i["id"] for i in led.attention(open_only=False)]
    finally:
        led.close()


# ---- F1: the final reconcile

def test_t34b2_a_practice_run_whose_last_reconcile_is_not_clean_ends_done_and_says_so(world):
    world.paper = paper({"a": {"SPY": 2.0}})
    first = world.run()
    assert first["status"] == "done" and first["orders_sent"] == 1 and "faults" not in first, first
    world.later(30)
    world.paper = paper({"a": {"SPY": 4.0}})
    _later_submit(world, 0, then=lambda w: w.venue.raise_on.add("positions"))   # the final reconcile cannot read them
    out = world.run()                                                  # addendum 3 (E2): set aside, said, no fault
    assert out["status"] == "done" and out["orders_sent"] == 1 and "faults" not in out, out
    assert out["reconcile_pending"] == {"symbols": [], "positions_unread": True, "status_unread": 0}, out
    assert world.entries("summary")[-1]["reconcile_pending"] == out["reconcile_pending"]
    assert not any(k in out for k in T34B2_LIVE_KEYS[2:])


def test_t34b2_an_unclean_first_reconcile_then_clean_ones_is_no_fault(world):
    world.paper = paper({"a": {"SPY": 2.0}})
    world.run()
    world.later(30)
    _t34_positions_fail(world, {1})                                    # the run's first reconcile only
    out = world.run()
    assert out["status"] == "done" and out["orders_sent"] == 1 and "faults" not in out, out
    led = world.ledger()
    try:
        assert [tuple(r) for r in led._read("SELECT clean FROM reconciles ORDER BY id")][-3:] == [(0,), (1,), (1,)]
    finally:
        led.close()


def test_t34b2_a_run_stopped_at_the_reconcile_never_judges_a_final_one(world):
    world.paper = paper({"a": {"SPY": 2.0}})
    world.run()
    world.later(30)
    _t34_positions_fail(world, {1, 2})                                 # both reconciles before sending: blocked
    out = world.run()                                                  # T34b-3 (G): the positions read alone: venue_read
    assert out["status"] == "stopped" and out["stage"] == "venue_read" and "faults" not in out, out


# ---- F2: a blocking attention item opened in the run

def test_t34b2_an_item_that_turns_up_in_the_run_is_a_fault_and_one_open_before_it_is_not(world):
    world.paper = paper({"a": {"SPY": 2.0}})
    world.venue.submit_mode = ["raise"]                                # sent, never arrived: the outcome unknown
    out = world.run()
    assert out["stage"] == "send" and "faults" not in out, out
    (lost,) = world.venue.submits()
    world.later(30)
    world.paper = paper({})
    out = world.run()                                                  # declared never arrived
    assert out["status"] == "done" and "faults" not in out and world.orders()[0]["state"] == "never_arrived", out
    world.venue.book[lost] = {"symbol": "SPY", "units": 2.0, "limit": 505.0, "filled": 0.0, "state": "open"}
    world.later(30)
    out = world.run()                                                  # ... and then it turns up
    assert out["status"] == "stopped" and out["stage"] == "reconcile", out
    assert out["faults"] == ["attention:turned_up"] and world.entries("summary")[-1]["faults"] == ["attention:turned_up"]
    world.later(30)
    out = world.run()                                                  # the same item, opened before this run
    assert out["status"] == "stopped" and out["stage"] == "reconcile" and "faults" not in out, out


def test_t34b2_items_opened_in_a_done_run_are_one_entry_per_kind_sorted_and_notices_and_older_ones_are_not(world):
    world.paper = paper({"a": {"SPY": 2.0}})
    world.run()
    led = world.ledger()
    try:
        with led._write():
            led._flag("old", "aaa_old", "opened before the run")
        led.close_attention(1, "Burton", "looked")                    # closed: its id still counts as existing
    finally:
        led.close()
    world.later(30)
    world.paper = paper({"a": {"SPY": 4.0}})
    _later_submit(world, 0, then=lambda w: _t34_flag(w, ["zeta", "alpha", "alpha", "Bad Kind!", "x" * 41, "negative_fee",
                                                         "aaa_old"]))
    out = world.run()
    assert out["status"] == "done" and out["orders_sent"] == 1, out
    assert out["faults"] == ["attention:aaa_old", "attention:alpha", "attention:unknown", "attention:zeta"], out


def test_t34b2_a_notice_opened_in_the_run_is_no_fault(world):
    class NegativeFee(StandIn):
        def order_status(self, tag):
            out = super().order_status(tag)
            if out is not None and out["filled_units"]:
                out["fee"] = -0.25
            return out
    world.venue = NegativeFee()
    world.state_path = osx.state_path_for(world.ledger_path, world.venue.name)
    world.venue.fill_at_once = 1.0
    world.paper = paper({"a": {"SPY": 2.0}})
    out = world.run()
    kinds = [i["kind"] for i in world.ledger().attention()]
    assert kinds == ["negative_fee"] and out["status"] == "done" and "faults" not in out, (kinds, out)


def _t34_faults_fail(monkeypatch):
    """The ledger's every-item attention read fails from its second call in a run on: the faults' own read."""
    calls, real = [], _Ledger.attention

    def attention(self, open_only=True):
        if not open_only:                                              # every item, closed ones too: the faults' read
            calls.append(open_only)
            if len(calls) > 1:
                raise RuntimeError("the attention table cannot be read")
        return real(self, open_only)
    monkeypatch.setattr(_Ledger, "attention", attention)


def test_t34b2_a_failure_while_the_faults_are_worked_out_ends_the_run_as_any_unexpected_failure(world, monkeypatch):
    world.paper = paper({"a": {"SPY": 2.0}})
    world.run()
    world.later(30)
    _t34_faults_fail(monkeypatch)
    out = world.run()
    assert out["status"] == "stopped" and out["stage"] == "unexpected", out
    assert out["reason"] == "the run failed unexpectedly: RuntimeError; the run had ended done" and "faults" not in out
    assert [e["event"] for e in world.entries()][-2:] == ["stopped", "summary"]
    assert world.entries("stopped")[-1]["reason"] == out["reason"]


def test_t34b2_a_failure_while_the_faults_are_worked_out_keeps_the_stop_the_run_had_already_made(tmp_path, monkeypatch):
    outs = []
    for name in ("plain", "failing"):                                  # the same world twice; the second one's read fails
        w = World(tmp_path / name)
        w.paper = paper({"a": {"SPY": 2.0}})
        w.venue.submit_mode = ["raise"]                                # sent, never arrived: the run stops at send
        if name == "failing":
            _t34_faults_fail(monkeypatch)
        outs.append(w.run())
    plain, failing = outs
    assert plain["status"] == "stopped" and plain["stage"] == "send" and plain["reason"], plain
    assert "\n" not in plain["reason"]
    assert failing["status"] == "stopped" and failing["stage"] == "unexpected", failing
    assert failing["reason"] == ("the run failed unexpectedly: RuntimeError; the run had ended stopped at send: "
                                 + plain["reason"].replace(plain["batch"], failing["batch"])), failing
    assert failing["orders_sent"] == plain["orders_sent"] and failing["refused"] == plain["refused"], (plain, failing)


# ---- the live world of these tests: a baseline OWN 10 at 50, cash 9,500, so day 1's equity is 10,000

def _t34_paper_book(journal, returns, end="2026-11-12", capital=100_000.0, name="a"):
    """The paper trader's day summaries for `name` on the exchange sessions ending `end`: one that does not hold it,
    then one per return (its value change over the capital), each adding up."""
    days = [str(d.date()) for d in CAL.sessions_in_range("2025-06-02", end)[-(len(returns) + 1):]]
    equity, value = 1_000_000.0, 0.0
    journal.write(days[0], {"type": "summary", "equity": equity, "strategy_values": {}, "strategy_value_change": {},
                            "capital_per_strategy": capital})
    for d, r in zip(days[1:], returns):
        change = r * capital
        value, equity = value + change, equity + change
        journal.write(d, {"type": "summary", "equity": equity, "strategy_values": {name: value},
                          "strategy_value_change": {name: change}, "capital_per_strategy": capital})


def _t34_world(tmp, wall=None, returns=None, conf=None, venue=None):
    limits = None if wall is None else {**LIVE_LIMITS, "max_daily_loss_pct": wall}
    w = live_world(tmp, venue=venue if venue is not None else LiveVenue(held={"OWN": 10.0}, cash=9_500.0), limits=limits,
                   conf=conf)
    if returns is not None:
        _t34_paper_book(w.journal, returns)
    return w


def _t34_day_one(w):
    """2026-11-13: a ticket for IWM 3 and TLT 4, approved and filled at once (cash 9,500 less 606 and 404)."""
    _, number = _live_proposed(w, {"a": {"IWM": 3.0, "TLT": 4.0}})
    w.venue.fill_at_once = 1.0
    w.later(1)
    out = lapprove(w, number)
    assert out["status"] == "done" and out["orders_sent"] == 2 and "day_stop" not in out, out
    w.venue.fill_at_once = 0.0
    assert w.book() == {"IWM": 3.0, "TLT": 4.0} and w.venue.cash_value == 8_490.0
    return out


def _t34_day_two(w, cash):
    """The next session, 10:00: held IWM 3 at 200, TLT 4 at 100, OWN 10 at 50, so the equity is cash + 1,500."""
    w.next_day()
    w.venue.cash_value = cash
    st = osx.OrderStepState(w.state_path)
    try:
        assert st.previous_equity(str(ny_day(w.clock.t).date())) == 10_000.0
    finally:
        st.close()


def _refused_by(out, by):
    return [r["symbol"] for r in out["refused"] if r["by"] == by]


FLOOR_STOP = {"pct": 1.0, "basis": "fewer than 25 returns: the floor", "n": 0}


# ---- C: the ceiling, on every live summary once the confirmation is in force

def test_t34b2_every_live_summary_with_a_confirmation_carries_its_max_capital_as_the_ceiling(tmp_path):
    w = _t34_world(tmp_path / "w")
    out = _t34_day_one(w)
    assert out["ceiling"] == 5000.0 and type(out["ceiling"]) is float
    assert all(e["ceiling"] == 5000.0 for e in w.entries("summary") if e["venue"] == "webull-production")
    assert out["working_stop"] == FLOOR_STOP and "faults" not in out and "cap_unjudged" not in out
    _unmet(w, "too few days")                                        # lock 2 unmet; the confirmation still in force
    w.later(1)
    out = lpropose(w)
    assert out["stage"] == "lock" and out["ceiling"] == 5000.0 and "working_stop" not in out, out
    _unmet(w, "lapsed")                                              # no confirmation in force: no ceiling
    out = lpropose(w)
    assert out["stage"] == "lock" and "ceiling" not in out, out


def test_t34b2_no_ceiling_and_no_live_key_on_a_practice_summary(tw):
    _, number = _proposed(tw, {"a": {"SPY": 2.0}})
    tw.later(1)
    for out in (approve(tw, number), tw.run()):
        assert out["status"] == "done" and not any(k in out for k in T34B2_LIVE_KEYS), out
    assert not any(k in e for e in tw.entries("summary") for k in T34B2_LIVE_KEYS)


# ---- F3: a breached cap, live runs only

class _Overpriced(LiveVenue):
    """Reports each fill at twice its limit."""

    def order_status(self, tag):
        out = super().order_status(tag)
        if out is not None and out["avg_price"] is not None:
            out["avg_price"] = out["avg_price"] * 2
        return out


class _Unpriced(LiveVenue):
    """Reports each fill without an average price."""

    def order_status(self, tag):
        out = super().order_status(tag)
        if out is not None:
            out["avg_price"] = None
        return out


def test_t34b2_a_fill_of_this_batch_above_max_order_value_is_a_cap_fault(tmp_path):
    w = _t34_world(tmp_path / "w", venue=_Overpriced(held={"OWN": 10.0}, cash=9_500.0))
    _, number = _live_proposed(w, {"a": {"IWM": 3.0, "TLT": 4.0}})
    w.venue.fill_at_once = 1.0
    w.later(1)
    out = lapprove(w, number)                                         # IWM 3 at 404: 1,212 > 1,000; TLT 4 at 202: 808
    assert out["status"] == "done" and out["orders_sent"] == 2 and out["faults"] == ["cap"], out
    assert "cap_unjudged" not in out and w.entries("summary")[-1]["faults"] == ["cap"]
    w.later(1)
    out = lpropose(w)                                                 # a later batch: those fills are not its own
    assert out["status"] == "done" and "faults" not in out, out


def test_t34b2_a_book_above_the_ceiling_through_a_mark_that_rose_is_no_fault_and_is_said_as_over_ceiling(tmp_path):
    w = _t34_world(tmp_path / "w")
    _t34_day_one(w)
    w.next_day()
    w.prices["IWM"] = 1_500.0                                        # 4,500 + 400 = 4,900: not above 5,000
    out = lpropose(w)
    assert out["status"] == "done" and "faults" not in out and "over_ceiling" not in out, out
    w.later(1)
    w.prices["IWM"] = 1_534.0                                        # 4,602 + 400 = 5,002 > 5,000: information only
    out = lpropose(w)
    assert out["status"] == "done" and out["stage"] is None and "faults" not in out and out["ceiling"] == 5000.0, out
    assert out["over_ceiling"] == {"at_work": 5002.0, "ceiling": 5000.0}, out
    assert w.entries("summary")[-1]["over_ceiling"] == {"at_work": 5002.0, "ceiling": 5000.0}
    p = ticket_world(tmp_path / "p")                                  # the same book in practice: nothing is judged
    p.paper = paper({"a": {"IWM": 3.0, "TLT": 4.0}})
    p.venue.fill_at_once = 1.0
    p.run()
    p.next_day()
    p.prices["IWM"] = 1_534.0
    out = p.run()
    assert out["status"] == "done" and not any(k in out for k in T34B2_LIVE_KEYS), out


def test_t34b2_a_ceiling_the_owner_lowered_between_two_runs_is_no_fault_and_is_said_as_over_ceiling(tmp_path):
    w = _t34_world(tmp_path / "w")
    _t34_day_one(w)
    w.next_day()                                                     # 3 x 200 + 4 x 100 = 1,000 at work
    out = lpropose(w)
    assert out["status"] == "done" and "over_ceiling" not in out and "faults" not in out, out
    (w.cfg / "webull_live.json").write_text(json.dumps(live_conf(max_capital=900, max_order_value=900)))
    w.later(1)
    out = lpropose(w, capital=900.0)                                 # the capital follows the lowered ceiling
    assert out["status"] == "done" and out["ceiling"] == 900.0 and "faults" not in out, out
    assert out["over_ceiling"] == {"at_work": 1000.0, "ceiling": 900.0}, out


def test_t34b2_holdings_an_earlier_run_bought_above_the_ceiling_are_no_fault_while_this_run_sells(tmp_path):
    w = _t34_world(tmp_path / "w")
    _t34_day_one(w)
    w.next_day()
    w.prices["IWM"] = 1_700.0                                        # 5,100 + 400 = 5,500 > 5,000, bought by day one
    _, number = _live_proposed(w, {"a": {"IWM": 3.0, "TLT": 2.0}})  # this run's batch: a sale of TLT 2
    w.venue.fill_at_once = 1.0
    w.later(1)
    out = lapprove(w, number)
    assert out["status"] == "done" and out["orders_sent"] == 1 and out["sent"][0]["units"] == -2.0, out
    assert "faults" not in out and out["over_ceiling"] == {"at_work": 5300.0, "ceiling": 5000.0}, out


def test_t34b2_a_fill_without_a_price_is_not_judged_and_is_named(tmp_path):
    w = _t34_world(tmp_path / "w", venue=_Unpriced(held={"OWN": 10.0}, cash=9_500.0))
    _, number = _live_proposed(w, {"a": {"IWM": 3.0, "TLT": 4.0}})
    w.venue.fill_at_once = 1.0
    w.later(1)
    out = lapprove(w, number)
    assert out["status"] == "done" and out["orders_sent"] == 2, out
    assert out["cap_unjudged"] == ["IWM", "TLT"] and "faults" not in out, out


# ---- F3 (ii) and (iii), addendum 1: a fill that broke what the run had checked, from this batch's own orders

class _ThroughLimit(LiveVenue):
    """Reports each fill at its limit times `buy` (a purchase) or `sell` (a sale), plus `buy_add` or `sell_add` (K1)."""
    buy, sell, buy_add, sell_add = 1.0, 1.0, 0.0, 0.0

    def order_status(self, tag):
        out = super().order_status(tag)
        if out is not None and out["avg_price"] is not None:
            bought = self.book[tag]["units"] > 0
            out["avg_price"] = out["avg_price"] * (self.buy if bought else self.sell) + (
                self.buy_add if bought else self.sell_add)
        return out


@pytest.mark.parametrize("factor, add, fault", [(1.01, 0.0, True), (1.0, 0.006, True), (1.0, 0.005, True),
                                                (1.0, 0.004, True), (1 + 1.1e-6, 0.0, True), (1 + 0.9e-6, 0.0, False),
                                                (1.0, 0.0, False)])
def test_t34b2_a_purchase_filled_above_its_own_limit_is_a_cap_fault(tmp_path, factor, add, fault):
    # K1: the order's average beyond its limit by more than 1e-6 of it (T34b-3 E: the half cent is gone); the edge is
    # no breach
    venue = _ThroughLimit(held={"OWN": 10.0}, cash=9_500.0)
    venue.buy, venue.buy_add = factor, add                            # IWM 3 at 202 x 1.01: 612, under max_order_value
    w = _t34_world(tmp_path / "w", venue=venue)
    _, number = _live_proposed(w, {"a": {"IWM": 3.0, "TLT": 4.0}})
    w.venue.fill_at_once = 1.0
    w.later(1)
    out = lapprove(w, number)
    assert out["status"] == "done" and out["orders_sent"] == 2, out
    assert out.get("faults") == (["cap"] if fault else None) and "cap_unjudged" not in out, out


@pytest.mark.parametrize("factor, add, fault", [(0.99, 0.0, True), (1.0, -0.006, True), (1.0, -0.005, True),
                                                (1.0, -0.004, True), (1 - 1.1e-6, 0.0, True), (1 - 0.9e-6, 0.0, False),
                                                (1.0, 0.0, False)])
def test_t34b2_a_sale_filled_below_its_own_limit_is_a_cap_fault(tmp_path, factor, add, fault):
    venue = _ThroughLimit(held={"OWN": 10.0}, cash=9_500.0)
    w = _t34_world(tmp_path / "w", venue=venue)
    _t34_day_one(w)
    w.next_day()
    venue.sell, venue.sell_add = factor, add
    _, number = _live_proposed(w, {"a": {"IWM": 3.0, "TLT": 2.0}})  # a sale of TLT 2, limit 98
    w.venue.fill_at_once = 1.0
    w.later(1)
    out = lapprove(w, number)
    assert out["status"] == "done" and out["orders_sent"] == 1 and out["sent"][0]["units"] == -2.0, out
    assert out.get("faults") == (["cap"] if fault else None), out


def _t34_extra_fill(w, units):
    """After the run's second (last) submit: one more fill row for that order, written into the ledger file the way
    a fill row is stored, and the same units at the venue (so the positions still agree)."""
    done = []

    def then(w):
        if done or len(w.venue.submits()) < 2:
            return
        tag = w.venue.submits()[-1]
        o = w.venue.book[tag]
        sign = 1.0 if o["units"] > 0 else -1.0
        con = sqlite3.connect(w.ledger_path)
        try:
            with con:
                con.execute("INSERT INTO fills (tag, symbol, units, price, price_covers_units, fee, time, applied) "
                            "VALUES (?,?,?,?,?,?,?,0)", (tag, o["symbol"], sign * units, o["limit"], units, 0.0, w.clock.t))
        finally:
            con.close()
        w.venue.held[o["symbol"]] = w.venue.held.get(o["symbol"], 0.0) + sign * units
        done.append(tag)
    _later_submit(w, 0, then=then)
    return done


@pytest.mark.parametrize("extra, fault", [(1.0, True), (1e-10, False)])
def test_t34b2_one_orders_fills_summing_to_more_units_than_it_asked_for_are_a_cap_fault(tmp_path, extra, fault):
    w = _t34_world(tmp_path / "w")
    _, number = _live_proposed(w, {"a": {"IWM": 3.0, "TLT": 4.0}})
    w.venue.fill_at_once = 1.0
    w.later(1)
    done = _t34_extra_fill(w, extra)
    out = lapprove(w, number)
    assert done and out["orders_sent"] == 2, out
    assert ("cap" in out.get("faults", [])) is fault, out


class _Overfilling(LiveVenue):
    """Reports half as many units again as each order asked for once it fills."""

    def order_status(self, tag):
        out = super().order_status(tag)
        if out is not None and out["filled_units"]:
            out["filled_units"] = out["filled_units"] * 1.5
        return out


def test_t34b2_a_broker_that_reports_an_overfill_is_refused_by_the_ledger_and_is_a_reconcile_fault(tmp_path):
    w = _t34_world(tmp_path / "w", venue=_Overfilling(held={"OWN": 10.0}, cash=9_500.0))
    _, number = _live_proposed(w, {"a": {"IWM": 3.0, "TLT": 4.0}})
    w.venue.fill_at_once = 1.0
    w.later(1)
    out = lapprove(w, number)
    assert w.fills() == [], w.fills()                                 # the ledger stores no fill above an order's size
    assert "reconcile" in out.get("faults", []) and "cap" not in out.get("faults", []), out


# ---- addendum 3: E1 a sale above its limit is no breach; E2 a final reconcile that cannot tell a race from a mismatch

@pytest.mark.parametrize("fill_at", [252.0 / 246.96, 1.0])
def test_t34b2_a_sale_filled_above_its_limit_is_a_better_price_and_no_cap_fault(tmp_path, fill_at):
    v = _ThroughLimit(held={"OWN": 10.0}, cash=9_500.0)
    w = _t34_world(tmp_path / "w", venue=v, conf=live_conf(max_order_value=1000, max_capital=5000))
    w.prices["IWM"] = 240.0
    _, number = _live_proposed(w, {"a": {"IWM": 4.0}})
    w.venue.fill_at_once = 1.0
    w.later(1)
    assert lapprove(w, number)["orders_sent"] == 1
    w.next_day()
    w.prices["IWM"] = 252.0
    v.sell = fill_at                                                  # limit 246.96 (4 x 246.96 = 987.84 <= 1,000)
    _, number = _live_proposed(w, {"a": {"IWM": 0.0}})
    w.later(1)
    out = lapprove(w, number)
    fills = [(f["units"], f["price"]) for f in w.fills() if f["tag"].startswith(out["batch"])]
    assert out["status"] == "done" and out["sent"][0]["limit_price"] == 246.96 and len(fills) == 1, (out, fills)
    assert abs(fills[0][1] - 246.96 * fill_at) < 1e-9 and "faults" not in out, (fills, out)


def _t34_at_final_positions(w, action, sends):
    """`action(w)` at the first positions read after the run's `sends`-th submit (the final reconcile's), before the
    positions are read: what a resting order that fills, or a venue that fails, does between status and positions."""
    real, start, done = w.venue.positions, len(w.venue.calls), []

    def positions():
        if not done and sum(1 for c in w.venue.calls[start:] if c[0] == "submit") >= sends:
            done.append(1)
            action(w)
        return real()
    w.venue.positions = positions
    return done


def _t34_fill_open(*symbols):
    def action(w):
        for tag, o in w.venue.book.items():
            if o["state"] == "open" and (not symbols or o["symbol"] in symbols):
                w.venue.fill(tag)
    return action


def _t34_positions_down(w):
    w.venue.raise_on.add("positions")


def _t34_held_elsewhere(w):
    w.venue.held["GLD"] = w.venue.held.get("GLD", 0.0) + 1.0


def _with_odd(base):
    class Odd(base):
        """Reports filled_units -1 for the orders of the symbols in `odd` (an anomaly to the order ledger)."""
        odd = ()

        def order_status(self, tag):
            out = super().order_status(tag)
            if out is not None and self.book[tag]["symbol"] in self.odd:
                out["filled_units"] = -1.0
            return out
    return Odd


@pytest.mark.parametrize("what, faults, pending", [
    ("race", None, {"symbols": ["IWM"], "positions_unread": False, "status_unread": 0}),
    ("elsewhere", ["reconcile"], None),
    ("positions down", None, {"symbols": [], "positions_unread": True, "status_unread": 0}),
    ("race and an anomaly", ["reconcile"], {"symbols": ["IWM"], "positions_unread": False, "status_unread": 0})])
def test_t34b2_a_live_final_reconcile_sets_aside_only_a_race_with_an_open_order_or_unread_positions(
        tmp_path, what, faults, pending):
    w = _t34_world(tmp_path / "w", venue=_with_odd(LiveVenue)(held={"OWN": 10.0}, cash=9_500.0))
    _, number = _live_proposed(w, {"a": {"IWM": 3.0, "TLT": 4.0}})
    w.later(1)                                                        # fill_at_once 0: both orders rest at the venue
    action = {"race": _t34_fill_open("IWM"), "elsewhere": _t34_held_elsewhere, "positions down": _t34_positions_down,
              "race and an anomaly": _t34_fill_open("IWM")}[what]
    if what == "race and an anomaly":
        real = w.venue.submit

        def submit(*a, **k):                                          # TLT's status reads odd from its submit on
            out = real(*a, **k)
            w.venue.odd = ("TLT",)
            return out
        w.venue.submit = submit
    done = _t34_at_final_positions(w, action, 2)
    out = lapprove(w, number)
    assert done and out["status"] == "done" and out["orders_sent"] == 2, out
    assert out.get("faults") == faults and out.get("reconcile_pending") == pending, out
    summary = w.entries("summary")[-1]
    assert summary.get("faults") == faults and summary.get("reconcile_pending") == pending
    assert {o["symbol"] for o in w.orders() if o["state"] in ("accepted", "partly_filled")} >= (
        {"TLT"} if what != "positions down" else {"IWM", "TLT"})


def test_t34b2_a_practice_order_that_fills_between_status_and_positions_is_set_aside_and_said(world):
    world.paper = paper({"a": {"SPY": 3.0}})
    done = _t34_at_final_positions(world, _t34_fill_open(), 1)
    out = world.run()
    assert done and out["status"] == "done" and "faults" not in out, out
    assert out["reconcile_pending"] == {"symbols": ["SPY"], "positions_unread": False, "status_unread": 0}, out
    world.later(30)
    out = world.run()                                                 # the next run settles it: nothing pending
    assert out["status"] == "done" and "faults" not in out and "reconcile_pending" not in out, out


def test_t34b2_a_practice_anomaly_at_the_final_reconcile_is_still_a_fault(world):
    world.venue = _with_odd(StandIn)()
    world.state_path = osx.state_path_for(world.ledger_path, world.venue.name)
    world.paper = paper({"a": {"SPY": 2.0}})
    _later_submit(world, 0, then=lambda w: setattr(w.venue, "odd", ("SPY",)))
    out = world.run()
    assert out["status"] == "done" and out["faults"] == ["reconcile"] and "reconcile_pending" not in out, out


# ---- addendum 4 (G1): an order the ledger knows to be working, whose status could not be read, is pending

def _with_status_down(base):
    class StatusDown(base):
        """Its order_status raises for the orders of the symbols in `down`."""
        down = ()

        def order_status(self, tag):
            if self.book.get(tag, {}).get("symbol") in self.down:
                self.calls.append(("order_status", tag))
                raise ConnectionError("order_status timed out")
            return super().order_status(tag)
    return StatusDown


def _t34_after_last_submit(w, sends, then):
    """`then(w, tag)` right after the run's `sends`-th submit, with that submit's tag."""
    real, count = w.venue.submit, [0]

    def submit(*a, **k):
        out = real(*a, **k)
        count[0] += 1
        if count[0] == sends:
            then(w, w.venue.submits()[-1])
        return out
    w.venue.submit = submit


def _t34_ledger_state(w, tag, state):
    con = sqlite3.connect(w.ledger_path)
    try:
        with con:
            con.execute("UPDATE orders SET state = ? WHERE tag = ?", (state, tag))
    finally:
        con.close()


@pytest.mark.parametrize("what, faults, pending", [
    ("status unread", None, {"symbols": ["IWM"], "positions_unread": False, "status_unread": 1}),
    ("status unread and an anomaly", ["reconcile"], {"symbols": ["IWM"], "positions_unread": False, "status_unread": 1}),
    ("status unread, the order unsettled", ["reconcile"], None)])
def test_t34b2_a_live_final_reconcile_sets_aside_an_unread_status_only_for_an_order_the_ledger_knows_is_working(
        tmp_path, what, faults, pending):
    w = _t34_world(tmp_path / "w", venue=_with_status_down(_with_odd(LiveVenue))(held={"OWN": 10.0}, cash=9_500.0))
    _, number = _live_proposed(w, {"a": {"IWM": 3.0, "TLT": 4.0}})
    w.later(1)                                                        # both orders rest at the venue

    def then(w, tag):
        w.venue.down = ("IWM",)
        if what == "status unread and an anomaly":
            w.venue.odd = ("TLT",)
        if what == "status unread, the order unsettled":
            (iwm,) = [t for t, o in w.venue.book.items() if o["symbol"] == "IWM"]
            _t34_ledger_state(w, iwm, "sent")
    _t34_after_last_submit(w, 2, then)
    out = lapprove(w, number)
    assert out["status"] == "done" and out["orders_sent"] == 2, out
    assert out.get("faults") == faults and out.get("reconcile_pending") == pending, out


def test_t34b2_a_practice_order_whose_status_could_not_be_read_is_pending_and_the_next_run_settles_it(world):
    world.venue = _with_status_down(StandIn)()
    world.state_path = osx.state_path_for(world.ledger_path, world.venue.name)
    world.paper = paper({"a": {"SPY": 2.0}})
    _t34_after_last_submit(world, 1, lambda w, tag: setattr(w.venue, "down", ("SPY",)))
    out = world.run()
    assert out["status"] == "done" and out["orders_sent"] == 1 and "faults" not in out, out
    assert out["reconcile_pending"] == {"symbols": ["SPY"], "positions_unread": False, "status_unread": 1}, out
    world.venue.down = ()
    world.later(30)
    out = world.run()
    assert out["status"] == "done" and "faults" not in out and "reconcile_pending" not in out, out


@pytest.mark.parametrize("tag", ["no-such-order", None, ""])
def test_t34b2_an_unresolved_entry_whose_order_cannot_be_read_back_stays_a_reconcile_fault(world, monkeypatch, tag):
    world.paper = paper({"a": {"SPY": 2.0}})
    real, armed = _Ledger.reconcile, []

    def reconcile(self, *a, **k):
        out = real(self, *a, **k)
        if armed:                                                     # the final reconcile: one entry the ledger
            out["unresolved"].append({"tag": tag, "reason": "made by the test"})   # cannot read back
            out["clean"] = False
        return out
    monkeypatch.setattr(_Ledger, "reconcile", reconcile)
    _later_submit(world, 0, then=lambda w: armed.append(1))
    out = world.run()
    assert out["status"] == "done" and out["faults"] == ["reconcile"] and "reconcile_pending" not in out, out


# ---- addendum 5 (J1): a fill is judged when a run first journals it, whichever run sent its order

def _through(base):
    class Through(_with_status_down(base)):
        """Reports each fill at its limit times `buy` or `sell`; `over` reports that share more units than filled."""
        buy, sell, over = 1.0, 1.0, 0.0

        def order_status(self, tag):
            out = super().order_status(tag)
            if out is not None and out["avg_price"] is not None:
                out["avg_price"] = out["avg_price"] * (self.buy if self.book[tag]["units"] > 0 else self.sell)
            if out is not None and out["filled_units"] and self.over:
                out["filled_units"] = out["filled_units"] * (1 + self.over)
            return out
    return Through


def _t34_late_world(tmp, live, buy=1.0, over=0.0):
    venue = _through(LiveVenue if live else Booked)(held={"OWN": 10.0}, cash=9_500.0 if live else 1_000_000.0)
    venue.buy, venue.over = buy, over
    w = _t34_world(tmp, venue=venue) if live else ticket_world(tmp, venue)
    return w


def _t34_send(w, live, wanted):
    """One run that sends: live, a propose and its approve; practice, a scheduled run."""
    if live:
        _, number = _live_proposed(w, wanted)
        w.later(1)
        return lapprove(w, number)
    w.paper = paper(wanted)
    return w.run()


def _t34_next(w, live):
    """The next run, which reconciles first: live a propose, practice a scheduled run (the same targets)."""
    w.later(30)
    return lpropose(w) if live else w.run()


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("how", ["race", "status timeout", "quiet between runs"])
@pytest.mark.parametrize("buy, fault", [(1.05, True), (1.0, False)])
def test_t34b2_a_fill_through_its_limit_that_reaches_the_ledger_late_is_a_cap_fault_of_the_run_that_journals_it(
        tmp_path, live, how, buy, fault):
    w = _t34_late_world(tmp_path / "w", live, buy=buy)
    w.venue.fill_at_once = 0.0                                       # the IWM order rests at the venue
    if how == "race":
        _t34_at_final_positions(w, _t34_fill_open("IWM"), 1)
    elif how == "status timeout":
        def then(w, tag):
            w.venue.fill(tag)                                         # it fills, and its status read times out
            w.venue.down = ("IWM",)
        _t34_after_last_submit(w, 1, then)
    first = _t34_send(w, live, {"a": {"IWM": 3.0}})
    assert first["status"] == "done" and first["orders_sent"] == 1 and "faults" not in first, first
    assert w.fills() == [], w.fills()                                 # the fill is not in the ledger yet
    w.venue.down = ()
    if how == "quiet between runs":
        (tag,) = w.venue.submits()
        w.venue.fill(tag)
    out = _t34_next(w, live)
    (f,) = w.fills()
    assert f["units"] == 3.0 and abs(f["price"] - 202.0 * buy) < 1e-9, f
    assert out.get("faults") == (["cap"] if fault else None), out
    assert w.entries("summary")[-1].get("faults") == (["cap"] if fault else None)
    w.later(30)
    assert "faults" not in (lpropose(w) if live else w.run())         # judged once, by the run that journaled it


@pytest.mark.parametrize("live", [False, True])
def test_t34b2_an_overfill_reported_late_is_refused_by_the_ledger_and_is_no_cap(tmp_path, live):
    w = _t34_late_world(tmp_path / "w", live, over=0.5)
    w.venue.fill_at_once = 0.0
    first = _t34_send(w, live, {"a": {"IWM": 3.0}})
    assert first["status"] == "done" and "faults" not in first, first
    (tag,) = w.venue.submits()
    w.venue.fill(tag)                                                 # filled quietly; reported as 4.5 of 3
    out = _t34_next(w, live)
    assert w.fills() == [] and "cap" not in out.get("faults", []), (w.fills(), out)
    assert out["status"] == "stopped" and out["stage"] == "reconcile", out   # the ledger refuses it: a reconcile fault


def test_t34b2_a_late_fill_of_another_runs_order_over_its_asked_units_is_a_cap_fault(tmp_path):
    w = _t34_late_world(tmp_path / "w", False)
    w.venue.fill_at_once = 0.0
    first = _t34_send(w, False, {"a": {"IWM": 3.0}})
    assert first["status"] == "done" and "faults" not in first, first
    (tag,) = w.venue.submits()
    w.venue.fill(tag)
    con = sqlite3.connect(w.ledger_path)                              # one more fill row of that order, as stored
    try:
        with con:
            con.execute("INSERT INTO fills (tag, symbol, units, price, price_covers_units, fee, time, applied) "
                        "VALUES (?,?,?,?,?,?,?,0)", (tag, "IWM", 1.0, 202.0, 1.0, 0.0, w.clock.t))
    finally:
        con.close()
    w.venue.held["IWM"] = w.venue.held.get("IWM", 0.0) + 1.0
    out = _t34_next(w, False)
    assert out.get("faults") == ["cap"], out


# ---- addendum 6 (K1): the limit is judged on the order's own average, as the broker gives it

def _rounded(base):
    class Rounded(base):
        """Reports an order's average rounded to the cent, worked out from true piece prices: its first `first` units
        at its limit less `off` (a purchase; plus `off` for a sale), the rest at its limit. With `shown` set, its
        status gives only that many units filled (partly filled). An order in `skip` is reported as it is."""
        first, off, shown, skip = 0.0, 0.0, None, frozenset()

        def order_status(self, tag):
            out = super().order_status(tag)
            o = self.book.get(tag)
            if out is None or not o["filled"] or not self.first or tag in self.skip:
                return out
            p1 = o["limit"] - (1.0 if o["units"] > 0 else -1.0) * self.off
            n = o["filled"] if self.shown is None else min(self.shown, o["filled"])
            a = min(n, self.first)
            out["filled_units"], out["avg_price"] = n, round((a * p1 + (n - a) * o["limit"]) / n, 2)
            if n < abs(o["units"]):
                out["state"] = "partly_filled"
            return out
    return Rounded


@pytest.mark.parametrize("case", ["live purchase", "live sale", "practice purchase"])
def test_t34b2_an_average_rounded_to_the_cent_within_its_limit_is_no_cap_whatever_the_worked_out_piece(tmp_path, case):
    live = case.startswith("live")
    venue = _rounded(LiveVenue if live else Booked)(held={"OWN": 10.0}, cash=9_500.0 if live else 1_000_000.0)
    w = _t34_world(tmp_path / "w", venue=venue) if live else ticket_world(tmp_path / "w", venue)
    if case == "live sale":                                           # TLT 2 of 4 sold, limit 98: 98.01, then 98.00
        _t34_day_one(w)
        w.next_day()
        venue.skip = frozenset(venue.book)                            # day one's orders stay as they were
        wanted, venue.first, venue.off = {"a": {"IWM": 3.0, "TLT": 2.0}}, 1.0, 0.0051
    elif live:                                                        # IWM 3 at limit 202: 201.98 on 2, then 201.99
        wanted, venue.first, venue.off = {"a": {"IWM": 3.0}}, 2.0, 0.0151
    else:                                                             # IWM 100: 99 at 201.98, then 201.99 on 100
        wanted, venue.first, venue.off = {"a": {"IWM": 100.0}}, 99.0, 0.0151
    venue.fill_at_once, venue.shown = 1.0, venue.first                # filled at once; its status shows one piece
    first = _t34_send(w, live, wanted)
    assert first["status"] == "done" and first["orders_sent"] == 1 and "faults" not in first, first
    venue.shown = None                                                # then the whole of it, at the rounded average
    out = _t34_next(w, live)
    tag = w.venue.submits()[-1]
    rows, limit = [f for f in w.fills() if f["tag"] == tag], w.venue.book[tag]["limit"]
    beyond = (1.0 if rows[-1]["units"] > 0 else -1.0) * (rows[-1]["price"] - limit)
    assert len(rows) == 2 and beyond > 0.009, (rows, limit)           # the worked-out piece lies beyond the limit
    assert "faults" not in out and "cap_unjudged" not in out, out


# ---- addendum 5 (J2): the final reconcile does not fault a symbol the run has just traded

def _with_lag(base):
    class Lag(base):
        """Its positions read gives, for the symbols in `lag`, the stated units instead of what it holds."""
        lag = {}

        def positions(self):
            out = super().positions()
            for sym, units in self.lag.items():
                out[sym] = units
            return {s: u for s, u in out.items() if abs(u) > 1e-12}
    return Lag


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("untouched", [False, True])
def test_t34b2_a_positions_read_that_lags_the_order_status_is_pending_and_an_untouched_symbol_is_not(
        tmp_path, live, untouched):
    venue = _with_lag(LiveVenue if live else Booked)(held={"OWN": 10.0}, cash=9_500.0 if live else 1_000_000.0)
    w = _t34_world(tmp_path / "w", venue=venue) if live else ticket_world(tmp_path / "w", venue)
    w.venue.fill_at_once = 1.0                                       # filled at once; the positions read has not caught up

    def then(w, tag):
        w.venue.lag = {"IWM": 0.0}
        if untouched:
            w.venue.held["GLD"] = 1.0
    _t34_after_last_submit(w, 1, then)
    out = _t34_send(w, live, {"a": {"IWM": 3.0}})
    assert out["status"] == "done" and out["orders_sent"] == 1, out
    assert w.orders()[-1]["state"] == "filled"                       # no longer open: set aside as this run's symbol
    assert out["reconcile_pending"] == {"symbols": ["IWM"], "positions_unread": False, "status_unread": 0}, out
    assert out.get("faults") == (["reconcile"] if untouched else None), out
    w.venue.lag = {}
    if untouched:
        w.venue.held.pop("GLD")
    out = _t34_next(w, live)                                          # caught up: the next run is clean
    assert "faults" not in out and out["stage"] is None, out


def test_t34b2_a_batch_order_the_venue_calls_cancelled_while_its_positions_show_a_fill_is_pending_then_a_fault(
        tmp_path):
    class Cancelled(Booked):
        """Fills at once, but reports the order cancelled with nothing filled."""

        def order_status(self, tag):
            out = super().order_status(tag)
            if out is not None:
                out.update(state="cancelled", filled_units=0.0, avg_price=None)
            return out
    w = ticket_world(tmp_path / "w", Cancelled(held={"OWN": 10.0}))
    w.venue.fill_at_once = 1.0
    out = _t34_send(w, False, {"a": {"IWM": 3.0}})
    assert w.orders()[-1]["state"] == "cancelled" and w.fills() == [], w.orders()
    assert out["status"] == "done" and "faults" not in out, out                     # this run's batch: set aside
    assert out["reconcile_pending"] == {"symbols": ["IWM"], "positions_unread": False, "status_unread": 0}, out
    out = _t34_next(w, False)                                         # not hidden for good: the next run stops
    assert out["status"] == "stopped" and out["stage"] == "reconcile", out


# ---- addendum 5 (J3): the unsettled-send guard

def test_t34b2_an_unsettled_order_not_in_the_unresolved_list_is_still_a_reconcile_fault(world, monkeypatch):
    world.paper = paper({"a": {"SPY": 2.0}})
    real, armed = _Ledger.reconcile, []

    def reconcile(self, *a, **k):
        out = real(self, *a, **k)
        if armed:                                                     # the final reconcile: positions unread (set
            tag = self.orders()[-1]["tag"]                            # aside), and an order unsettled that is not in
            self._db.execute("UPDATE orders SET state = 'sent' WHERE tag = ?", (tag,))   # its unresolved list
            self._db.commit()
            out["positions_error"], out["clean"] = "made by the test", False
        return out
    monkeypatch.setattr(_Ledger, "reconcile", reconcile)
    _later_submit(world, 0, then=lambda w: armed.append(1))
    out = world.run()
    assert out["status"] == "done" and out["faults"] == ["reconcile"], out
    assert out["reconcile_pending"] == {"symbols": [], "positions_unread": True, "status_unread": 0}, out


# ---- addendum 3 (E4): a run that turns out stopped leaves no usable ticket and loses no reason

def _t34_faults_raise(monkeypatch):
    """The faults cannot be worked out while the returned switch holds True."""
    on, real = [True], osx._Run._faults

    def faults(self):
        if on[0]:
            raise RuntimeError("the faults cannot be worked out")
        return real(self)
    monkeypatch.setattr(osx._Run, "_faults", faults)
    return on


@pytest.mark.parametrize("live", [False, True])
def test_t34b2_a_propose_turned_stopped_by_its_faults_voids_its_ticket_and_returns_no_number(tmp_path, monkeypatch,
                                                                                             live):
    w = _t34_world(tmp_path / "w") if live else ticket_world(tmp_path / "w")
    w.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0) if live else paper({"a": {"SPY": 2.0}})
    on = _t34_faults_raise(monkeypatch)
    out = lpropose(w) if live else propose(w)
    on[0] = False
    assert out["status"] == "stopped" and out["stage"] == "unexpected" and "ticket_number" not in out, out
    assert out["reason"] == "the run failed unexpectedly: RuntimeError; the run had ended done", out
    (number,) = [r["number"] for r in ticket_rows(w, "tickets")]      # made, never returned: and never usable
    w.later(1)
    out = lapprove(w, number) if live else approve(w, number)
    assert out["status"] == "stopped" and out["stage"] == "ticket" and out["orders_sent"] == 0, out
    assert out["reason"].startswith("the run that made that ticket ended stopped: the run failed unexpectedly: "
                                    "RuntimeError; the run had ended done"), out
    assert w.venue.submits() == []


def test_t34b2_a_run_already_stopped_unexpectedly_keeps_its_reason_and_appends_the_faults_failure(world, monkeypatch):
    world.paper = paper({"a": {"SPY": 2.0}})

    def judge(self, final):
        raise ValueError("the final judgement failed")
    monkeypatch.setattr(osx._Run, "_judge_final", judge)
    _t34_faults_raise(monkeypatch)
    out = world.run()
    assert out["status"] == "stopped" and out["stage"] == "unexpected", out
    assert out["reason"] == ("the run failed unexpectedly: ValueError; working out the faults also failed: "
                             "RuntimeError"), out
    assert world.entries("stopped")[-1]["reason"] == out["reason"] and "faults" not in out


# ---- S1: the working stop

def _alternating(n, size=0.006):
    return [size if k % 2 == 0 else -size for k in range(n)]


@pytest.mark.parametrize("returns, wall, pct, basis, n", [
    (_alternating(25), None, "formula", "25 returns ending 2026-11-12", 25),
    (_alternating(31), None, "formula", "25 returns ending 2026-11-12", 25),
    (_alternating(24), None, 1.0, "fewer than 25 returns: the floor", 24),
    (_alternating(25, 0.02), 2.0, 2.0, "25 returns ending 2026-11-12; held at the owner's daily halt", 25),
    (_alternating(24), 0.5, 0.5, "fewer than 25 returns: the floor; held at the owner's daily halt", 24),
    ([0.001, -1.0], 2.0, 1.0, "the paper record could not be read: the floor", 0),
    ([0.001, -1.0], 0.5, 0.5, "the paper record could not be read: the floor; held at the owner's daily halt", 0)])
def test_t34b2_the_working_stop_comes_from_the_paper_books_last_25_exact_returns(tmp_path, returns, wall, pct, basis, n):
    w = _t34_world(tmp_path / "w", wall=wall, returns=returns)
    w.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    out = lpropose(w)
    assert out["status"] == "done", out
    if pct == "formula":
        pct = _sl.STOP_MULTIPLE * _statistics.stdev(r * 100 for r in returns[-25:])
        assert 1.0 < pct < 99.0
        assert out["working_stop"]["pct"] == pytest.approx(pct, rel=1e-9, abs=0)
        assert out["working_stop"]["pct"] == _sl.working_stop_pct(
            _sl.paper_returns(_dc.paper_record(w.journal.dir, "2026-11-13"), ["a"]), 99.0).pct
        out["working_stop"]["pct"] = pct
    assert out["working_stop"] == {"pct": pct, "basis": basis, "n": n}, out
    assert w.entries("summary")[-1]["working_stop"] == out["working_stop"]


def test_t34b2_a_paper_record_that_cannot_be_read_sets_the_floor_and_says_so(tmp_path, monkeypatch):
    w = _t34_world(tmp_path / "w", wall=2.0, returns=_alternating(25, 0.02))

    def unreadable(directory, as_of):
        raise OSError("the journal folder cannot be read")
    monkeypatch.setattr(_dc, "paper_record", unreadable)
    w.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    out = lpropose(w)
    assert out["status"] == "done" and out["working_stop"] == {
        "pct": 1.0, "basis": "the paper record could not be read: the floor", "n": 0}, out


def test_t34b2_the_working_stop_is_worked_out_once_after_the_paper_book_and_before_any_order(tmp_path, monkeypatch):
    w = _t34_world(tmp_path / "w")
    seen = []
    real = _dc.paper_record

    def spy(directory, as_of):
        seen.append((Path(directory), as_of, len(w.venue.submits()), len(w.quote_calls)))
        return real(directory, as_of)
    monkeypatch.setattr(_dc, "paper_record", spy)
    _t34_day_one(w)
    assert seen == [(w.tmp / "journal", "2026-11-13", 0, 1), (w.tmp / "journal", "2026-11-13", 0, 2)]   # one ask each


# ---- S2, S3, S5: the trip, and what is refused in the day stop

@pytest.mark.parametrize("own_after, trips", [(41.0, True), (41.001, False)])
def test_t34b2_the_day_stop_trips_at_the_look_where_the_loss_first_reaches_the_working_stop(tmp_path, own_after, trips):
    w = _t34_world(tmp_path / "w")
    _t34_day_one(w)
    _t34_day_two(w, 8_490.0)                                        # 9,990: a loss of 0.1%
    w.paper = paper({"a": {"IWM": 0.0, "TLT": 0.0, "GLD": 2.0}}, equity=5000.0)
    out = lpropose(w)
    assert out["status"] == "done" and len(out["ticket"]["orders"]) == 3 and "day_stop" not in out, out
    w.later(1)
    for s in ("OWN", "IWM", "TLT", "GLD"):
        w.quote_age[s] = 19.5 * 60                                    # stale after the first send: asked again

    def own_falls(w):
        w.prices["OWN"] = own_after
    _later_submit(w, 2, then=own_falls)
    out = lapprove(w, out["ticket_number"])
    assert out["status"] == "done" and "faults" not in out, out
    assert [s["symbol"] for s in out["sent"]] == (["IWM", "TLT"] if trips else ["IWM", "TLT", "GLD"]), out
    if trips:                                                          # the second look: 8,490 + 600 + 400 + 410 = 9,900
        assert out["day_stop"] == {"pct": 1.0, "limit": 1.0, "wall": False}
        assert _refused_by(out, "day stop") == ["GLD"] and len(out["refused"]) == 1
        (line,) = [r for r in ticket_rows(w, "outcomes") if r["outcome"] == "not sent"]
        assert line["reason"].startswith("refused by the day stop: ") and "GLD" not in {o["symbol"] for o in w.orders()}
    else:                                                              # 9,900.01: a loss of 0.9999%
        assert "day_stop" not in out and out["refused"] == []
    assert w.entries("summary")[-1].get("day_stop") == out.get("day_stop")


@pytest.mark.parametrize("cash, loss, wall_flag", [(8_350.0, 1.5, False), (8_300.0, 2.0, True), (8_250.0, 2.5, True)])
def test_t34b2_the_wall_flag_says_whether_the_loss_also_reached_the_owners_daily_halt(tmp_path, cash, loss, wall_flag):
    w = _t34_world(tmp_path / "w", wall=2.0)
    _t34_day_one(w)
    _t34_day_two(w, cash)
    w.paper = paper({"a": {"IWM": 0.0, "TLT": 4.0, "GLD": 2.0}}, equity=5000.0)
    out = lpropose(w)
    assert out["status"] == "done" and "faults" not in out, out
    assert out["day_stop"] == {"pct": pytest.approx(loss, abs=1e-9), "limit": 1.0, "wall": wall_flag}, out
    assert [o["fund"] for o in out["ticket"]["orders"]] == ["IWM"] and _refused_by(out, "day stop") == ["GLD"]


def test_t34b2_in_the_day_stop_a_buy_is_refused_once_before_the_gate_and_nothing_is_reserved(tmp_path, monkeypatch):
    w = _t34_world(tmp_path / "w")
    _t34_day_one(w)
    _t34_day_two(w, 8_400.0)                                        # 9,900: exactly the floor
    gate = []
    real = osx.risk_gate.check_order
    monkeypatch.setattr(osx.risk_gate, "check_order", lambda order, *a, **k: gate.append(order["symbol"]) or real(order, *a, **k))
    w.paper = paper({"a": {"IWM": 0.0, "TLT": 4.0, "GLD": 2.0, "QQQ": 1.0}}, equity=5000.0)
    out = lpropose(w)
    assert out["status"] == "done" and out["day_stop"] == {"pct": 1.0, "limit": 1.0, "wall": False}, out
    assert gate == ["IWM"] and _refused_by(out, "day stop") == ["GLD", "QQQ"]
    refused = w.entries("refused")
    assert [(e["symbol"], e["by"]) for e in refused] == [("GLD", "day stop"), ("QQQ", "day stop")]
    assert all("working stop of 1.00%" in e["reason"] and "reducing orders still pass" in e["reason"] for e in refused)
    assert [o["fund"] for o in out["ticket"]["orders"]] == ["IWM"] and not {"GLD", "QQQ"} & {o["symbol"] for o in w.orders()}
    w.later(1)
    out = lapprove(w, out["ticket_number"])                          # the sale goes through every check
    assert out["status"] == "done" and [s["symbol"] for s in out["sent"]] == ["IWM"], out
    assert out["day_stop"] == {"pct": 1.0, "limit": 1.0, "wall": False, "carried": True}
    assert {o["symbol"] for o in w.orders() if o["created"] > ny("2026-11-16 00:00")} == {"IWM"}


# ---- S4: later runs the same New York day, and the next day

def test_t34b2_a_later_run_the_same_day_starts_in_the_day_stop_and_the_next_new_york_day_does_not(tmp_path):
    w = _t34_world(tmp_path / "w")
    _t34_day_one(w)
    _t34_day_two(w, 8_400.0)
    w.paper = paper({"a": {"IWM": 0.0, "TLT": 4.0, "GLD": 2.0}}, equity=5000.0)
    out = lpropose(w)
    assert out["day_stop"] == {"pct": 1.0, "limit": 1.0, "wall": False} and _refused_by(out, "day stop") == ["GLD"], out
    w.venue.cash_value = 8_490.0                                    # the loss is back to 0.1%: the day is still stopped
    w.later(1)
    out = lpropose(w)
    assert out["status"] == "done" and _refused_by(out, "day stop") == ["GLD"], out
    assert out["day_stop"] == {"pct": 1.0, "limit": 1.0, "wall": False, "carried": True}
    assert "carried" not in w.entries("summary")[-2]["day_stop"] and w.entries("summary")[-1]["day_stop"]["carried"] is True
    w.later(1)
    out = lapprove(w, out["ticket_number"])
    assert out["status"] == "done" and [s["symbol"] for s in out["sent"]] == ["IWM"] and out["day_stop"]["carried"], out
    w.next_day()                                                    # 2026-11-17: nothing carries
    out = lpropose(w)
    assert out["status"] == "done" and "day_stop" not in out and out["refused"] == [], out
    assert [o["fund"] for o in out["ticket"]["orders"]] == ["IWM", "GLD"]


def test_t34b2_a_run_stopped_after_the_trip_still_records_it_and_a_later_run_reads_it_back(tmp_path):
    w = _t34_world(tmp_path / "w")
    _t34_day_one(w)
    _t34_day_two(w, 8_490.0)
    w.paper = paper({"a": {"IWM": 0.0, "TLT": 4.0, "GLD": 2.0}}, equity=5000.0)
    out = lpropose(w)                                                # no stop yet: the ticket holds the sale and the buy
    assert "day_stop" not in out and [o["fund"] for o in out["ticket"]["orders"]] == ["IWM", "GLD"], out
    w.later(1)
    w.venue.cash_value = 8_400.0                                    # 9,900 at the approve's first look
    w.venue.submit_mode = ["raise"]                                  # the sale's outcome unknown: the run stops at send
    out = lapprove(w, out["ticket_number"])
    assert out["stage"] == "send" and out["day_stop"] == {"pct": 1.0, "limit": 1.0, "wall": False}, out
    assert w.entries("summary")[-1]["day_stop"] == out["day_stop"]
    w.later(30)
    w.venue.cash_value = 8_490.0
    out = lpropose(w)
    assert out["day_stop"] == {"pct": 1.0, "limit": 1.0, "wall": False, "carried": True}, out


# ---- addendum 2 (C1): the trip journaled when it happens, so the stop survives a run that dies

def test_t34b2_the_trip_is_journaled_once_at_the_look_synced_before_the_refusal_and_a_carried_stop_writes_none(
        tmp_path, monkeypatch):
    w = _t34_world(tmp_path / "w")
    _t34_day_one(w)
    _t34_day_two(w, 8_400.0)
    synced, real = [], osx._sync_file
    monkeypatch.setattr(osx, "_sync_file", lambda path: synced.append(len(w.entries())) or real(path))
    w.paper = paper({"a": {"IWM": 0.0, "TLT": 4.0, "GLD": 2.0, "QQQ": 1.0}}, equity=5000.0)
    out = lpropose(w)
    assert out["day_stop"] == {"pct": 1.0, "limit": 1.0, "wall": False}, out
    events = [e["event"] for e in w.entries() if e["batch"] == out["batch"]]
    assert events.count("day_stop") == 1 and events.index("day_stop") < events.index("refused"), events
    (line,) = w.entries("day_stop")
    assert {k: line[k] for k in ("pct", "limit", "wall")} == {"pct": 1.0, "limit": 1.0, "wall": False}
    assert line["venue"] == "webull-production" and line["day"] == "2026-11-16" and set(line) == {
        "timestamp", "day", "type", "venue", "batch", "event", "pct", "limit", "wall"}
    at = [e for e in w.entries()].index(line) + 1
    assert at in synced, (at, synced)                                # synced with the line as the journal's last
    w.later(1)
    out = lapprove(w, out["ticket_number"])                          # carried: it did not trip, it writes no line
    assert out["day_stop"]["carried"] is True and len(w.entries("day_stop")) == 1, out


class _Killed(BaseException):
    """What a kill looks like from inside: no handler of the order step's own runs, so no summary is written."""


def _t34_cut_off_after_the_trip(w, how):
    """Run a live propose that trips the day stop and is cut off at the first refusal by the day stop: raised from
    inside the run, or by a real SIGKILL of a forked child that runs it. The run writes no summary either way."""
    batches = {e["batch"] for e in w.entries("summary")}
    if how == "raise":
        def refuse(self, c, by, *a, **k):
            if by == "day stop":
                raise _Killed()
            return real(self, c, by, *a, **k)
        real = osx._Run._refuse
        osx._Run._refuse = refuse
        try:
            with pytest.raises(_Killed):
                lpropose(w)
        finally:
            osx._Run._refuse = real
    else:
        r, wfd = _os.pipe()
        pid = _os.fork()
        if pid == 0:                                                   # the child
            _os.close(r)
            try:
                def stop(self, c, by, *a, **k):
                    if by == "day stop":
                        _os.write(wfd, b"K")
                        _time.sleep(240)
                    return real(self, c, by, *a, **k)
                real = osx._Run._refuse
                osx._Run._refuse = stop
                lpropose(w)
                _os.write(wfd, b"D")
            except BaseException:
                _os.write(wfd, b"E" + _traceback.format_exc().encode()[-2000:])
            finally:
                _os._exit(0)
        _os.close(wfd)
        try:
            ready, _, _ = _select.select([r], [], [], 120.0)
            data = _os.read(r, 4096) if ready else b""
            if not ready or data[:1] == b"K":
                _os.kill(pid, _signal.SIGKILL)                         # our own child, by its process id
        finally:
            _os.waitpid(pid, 0)
            _os.close(r)
        assert data[:1] == b"K", data
    assert {e["batch"] for e in w.entries("summary")} == batches       # no summary of the run that was cut off


@pytest.mark.parametrize("how", ["raise", "sigkill"])
def test_t34b2_a_run_cut_off_after_the_trip_still_stops_the_rest_of_the_new_york_day(tmp_path, how):
    w = _t34_world(tmp_path / "w")
    _t34_day_one(w)
    _t34_day_two(w, 8_400.0)                                        # 9,900: the floor's 1% at the first look
    w.paper = paper({"a": {"IWM": 0.0, "TLT": 4.0, "GLD": 2.0}}, equity=5000.0)
    _t34_cut_off_after_the_trip(w, how)
    (line,) = w.entries("day_stop")
    assert {k: line[k] for k in ("pct", "limit", "wall")} == {"pct": 1.0, "limit": 1.0, "wall": False}, line
    assert not any("day_stop" in e for e in w.entries("summary"))   # only the line records it
    w.venue.cash_value = 8_490.0                                    # the loss is back to 0.1%: the day is still stopped
    w.later(1)
    out = lpropose(w)
    assert out["status"] == "done" and _refused_by(out, "day stop") == ["GLD"], out
    assert out["day_stop"] == {"pct": 1.0, "limit": 1.0, "wall": False, "carried": True}, out
    assert [o["fund"] for o in out["ticket"]["orders"]] == ["IWM"] and len(w.entries("day_stop")) == 1
    w.later(1)
    out = lapprove(w, out["ticket_number"])                          # the sale is sent
    assert out["status"] == "done" and [s["symbol"] for s in out["sent"]] == ["IWM"] and out["day_stop"]["carried"], out
    w.next_day()                                                    # the next New York day: nothing carries
    out = lpropose(w)
    assert out["status"] == "done" and "day_stop" not in out and out["refused"] == [], out
    assert [o["fund"] for o in out["ticket"]["orders"]] == ["IWM", "GLD"], out      # the sale was sent, not filled


def test_t34b2_a_trip_line_that_cannot_be_written_stops_the_run_and_nothing_is_sent(tmp_path, monkeypatch):
    w = _t34_world(tmp_path / "w")
    _t34_day_one(w)
    _t34_day_two(w, 8_490.0)
    w.paper = paper({"a": {"IWM": 0.0, "TLT": 4.0, "GLD": 2.0}}, equity=5000.0)
    out = lpropose(w)                                                # no stop yet: the sale and the buy
    assert "day_stop" not in out and [o["fund"] for o in out["ticket"]["orders"]] == ["IWM", "GLD"], out
    real = Journal.write

    def write(self, day, entry):
        if entry.get("event") == "day_stop":
            raise OSError("the disk is full")
        return real(self, day, entry)
    monkeypatch.setattr(Journal, "write", write)
    sent_before = len(w.venue.submits())
    w.later(1)
    w.venue.cash_value = 8_400.0                                    # the approve's first look trips
    out = lapprove(w, out["ticket_number"])
    assert out["status"] == "stopped" and out["stage"] == "send" and out["orders_sent"] == 0, out   # as any journal failure
    assert out["reason"].startswith("the ledger, the state file or the journal failed: OSError"), out
    assert len(w.venue.submits()) == sent_before and w.entries("day_stop") == []


def test_t34b2_a_day_stop_line_of_another_venue_or_day_or_kind_does_not_carry_and_unreadable_values_carry_as_null(
        tmp_path):
    w = _t34_world(tmp_path / "w")
    _t34_day_one(w)
    _t34_day_two(w, 8_490.0)
    day = str(ny_day(w.clock.t).date())
    for e in ({"type": "order_step", "venue": gl.PAPER_VENUE, "event": "day_stop", "pct": 3.0, "limit": 1.0, "wall": False},
              {"type": "paper", "venue": "webull-production", "event": "day_stop", "pct": 3.0, "limit": 1.0, "wall": False}):
        w.journal.write(day, {"batch": "x", **e})
    w.journal.write("2026-11-13", {"type": "order_step", "venue": "webull-production", "batch": "y", "event": "day_stop",
                                   "pct": 3.0, "limit": 1.0, "wall": False})
    w.paper = paper({"a": {"IWM": 3.0, "TLT": 4.0, "GLD": 2.0}}, equity=5000.0)
    out = lpropose(w)
    assert out["status"] == "done" and "day_stop" not in out and out["refused"] == [], out
    w.journal.write(day, {"type": "order_step", "venue": "webull-production", "batch": "z", "event": "day_stop",
                          "pct": "x", "limit": None, "wall": "no"})
    w.later(1)
    out = lpropose(w)
    assert out["day_stop"] == {"pct": None, "limit": None, "wall": None, "carried": True}, out
    assert _refused_by(out, "day stop") == ["GLD"], out


def test_t34b2_a_day_stop_on_another_venue_or_an_earlier_day_does_not_carry(tmp_path):
    w = _t34_world(tmp_path / "w")
    w.journal.write("2026-11-13", {"type": "order_step", "venue": gl.PAPER_VENUE, "batch": "x", "event": "summary",
                                   "status": "done", "stage": None, "day_stop": {"pct": 3.0, "limit": 1.0, "wall": False}})
    w.journal.write("2026-11-12", {"type": "order_step", "venue": "webull-production", "batch": "y", "event": "summary",
                                   "status": "done", "stage": None, "day_stop": {"pct": 3.0, "limit": 1.0, "wall": False}})
    w.paper = paper({"a": {"IWM": 3.0}}, equity=5000.0)
    out = lpropose(w)
    assert out["status"] == "done" and "day_stop" not in out and out["ticket"]["orders"], out
    w.journal.write("2026-11-13", {"type": "order_step", "venue": "webull-production", "batch": "z", "event": "summary",
                                   "status": "stopped", "stage": "prices", "day_stop": "not a mapping"})
    w.later(1)
    out = lpropose(w)                                                # an unreadable stop still counts as a stop
    assert out["day_stop"] == {"pct": None, "limit": None, "wall": None, "carried": True}, out
    assert _refused_by(out, "day stop") == ["IWM"] and out["ticket"]["orders"] == []


# ---- P: the day stop and the working stop never act on a practice run

def test_t34b2_a_practice_run_past_any_working_stop_refuses_nothing_and_writes_no_live_key(tmp_path):
    w = ticket_world(tmp_path / "w", Booked(held={"OWN": 10.0}, cash=9_500.0))
    w.paper = paper({"a": {"IWM": 3.0, "TLT": 4.0}})
    w.venue.fill_at_once = 1.0
    assert w.run()["orders_sent"] == 2
    w.next_day()
    w.venue.cash_value = 5_000.0                                    # a loss of 45%
    w.paper = paper({"a": {"IWM": 3.0, "TLT": 4.0, "GLD": 2.0}})
    w.journal.write(str(ny_day(w.clock.t).date()), {"type": "order_step", "venue": w.venue.name, "batch": "x",
                                                    "event": "day_stop", "pct": 3.0, "limit": 1.0, "wall": False})
    out = w.run()                                                    # ... and a day_stop line it never reads
    assert out["status"] == "done" and [s["symbol"] for s in out["sent"]] == ["GLD"] and out["refused"] == [], out
    assert not any(k in e for e in [out, *w.entries("summary")] for k in T34B2_LIVE_KEYS)
    assert [e["batch"] for e in w.entries("day_stop")] == ["x"]      # addendum 2: practice never writes the line


# ---- P and the comparisons with earlier revisions: equal but for the added keys

T34B3_KEYS = ("notices", "cap_refs")                 # T34b-3: the summary keys it adds
T34B3_EVENTS = ("notice", "verdict")                 # ... the journal lines it adds


def _t34b3_undo(b, m, counts=None):
    """`m` (a run's record through T34b-3 or later) with what T34b-3 is allowed to add taken out, each kind counted in
    `counts`: the `verdict` field at the end of a fill line (with `judged_avg` and `judged_on` before it), the `notice`
    and `verdict` lines, the summary keys
    `notices` and `cap_refs`, and the stage `venue_read` where `b` stopped at `reconcile` (with its reason, in the
    returned summary, the summary line and the stopped line). Anything else is left for the caller to find."""
    def bump(kind):
        if counts is not None:
            counts[kind] = counts.get(kind, 0) + 1
    m = dict(m)
    if "summary" in m and "summary" in b:
        bs, ms = json.loads(b["summary"]), json.loads(m["summary"])
        for k in T34B3_KEYS:
            if k in ms and k not in bs:
                ms.pop(k)
                bump(f"summary key {k}")
        if ms.get("stage") == "venue_read" and bs.get("stage") == "reconcile":
            ms["stage"], ms["reason"] = bs["stage"], bs.get("reason")
            bump("stage venue_read")
        m["summary"] = json.dumps(ms, sort_keys=True, default=str)
    if "journal" in m and "journal" in b:
        journal = {}
        for name, data in m["journal"].items():
            kept = []
            for line in data.split(b"\n"):
                e = json.loads(line) if line.strip() else None
                if isinstance(e, dict) and e.get("type") == "order_step":
                    if e.get("event") in T34B3_EVENTS:
                        bump(f"{e['event']} line")
                        continue
                    if e.get("event") == "fill" and list(e)[-1:] == ["verdict"]:
                        e.pop("verdict")
                        bump("verdict field")
                        for k in ("judged_avg", "judged_on"):     # addenda 3 and 4: the figure judged, and on what
                            if k in e:
                                e.pop(k)
                                bump(f"{k} field")
                        line = json.dumps(e, default=str).encode()
                    elif e.get("event") == "summary" and any(k in e for k in T34B3_KEYS):
                        line = json.dumps({k: v for k, v in e.items() if k not in T34B3_KEYS}, default=str).encode()
                kept.append(line)
            theirs = b["journal"].get(name, b"").split(b"\n")
            if len(theirs) == len(kept):                 # the stop's stage and reason, line by line
                for i, (x, y) in enumerate(zip(theirs, kept)):
                    if x != y and b'"venue_read"' in y:
                        bx, my = json.loads(x), json.loads(y)
                        if my.get("stage") == "venue_read" and bx.get("stage") == "reconcile":
                            my["stage"], my["reason"] = bx["stage"], bx.get("reason")
                            kept[i] = json.dumps(my, default=str).encode()
            journal[name] = b"\n".join(kept)
        m["journal"] = journal
    return m


def _t34_same_but(b, m, keys, where, skip=(), counts=None):
    """One run's record through an earlier revision (`b`) and through this one (`m`): every part equal, except that
    this revision's summaries, returned and journaled, may carry the keys `keys` that the earlier one's do not; a
    journal line that differs is a summary line, the earlier line with only such keys added at its end (the journal
    holds the summaries of earlier runs too). Returns the keys added to the returned summary, with their values.
    T34b-3: what it adds (`_t34b3_undo`) is taken out of `m` first, counted in `counts`."""
    m = _t34b3_undo(b, m, counts)
    added = {}
    for key in b:
        if key in skip:
            continue
        if key == "summary":
            bs, ms = json.loads(b[key]), json.loads(m[key])
            added = {k: ms.pop(k) for k in keys if k in ms and k not in bs}
            assert json.dumps(ms, sort_keys=True, default=str) == b[key], (where, key)
        elif key == "journal":
            assert sorted(b[key]) == sorted(m[key]), (where, key)
            for name in b[key]:
                bl, ml = b[key][name].split(b"\n"), m[key][name].split(b"\n")
                assert len(bl) == len(ml), (where, name)
                for x, y in zip(bl, ml):
                    if x == y:
                        continue
                    e = json.loads(y)
                    extra = [k for k in e if k in keys]
                    assert extra and e.get("event") == "summary" and list(e)[-len(extra):] == extra, (where, name)
                    assert json.dumps({k: v for k, v in e.items() if k not in keys}, default=str).encode() == x, (where, name)
        else:
            assert b[key] == m[key], (where, key)
    return added


def _t34_practice_marks(added, where):
    """A practice run may carry `faults` and `reconcile_pending` only, and `faults` only reconcile and attention entries."""
    assert set(added) <= {"faults", "reconcile_pending", "cap_unjudged"}, where
    if "reconcile_pending" in added:
        p = added["reconcile_pending"]
        assert set(p) == {"symbols", "positions_unread", "status_unread"} and type(p["positions_unread"]) is bool, (where, p)
        assert type(p["status_unread"]) is int and p["status_unread"] >= 0, (where, p)
        assert p["symbols"] == sorted(set(p["symbols"])) and (p["symbols"] or p["positions_unread"]), (where, p)
    if "faults" in added:
        f = added["faults"]
        assert f and len(f) == len(set(f)) and all(x in ("reconcile", "cap") or x.startswith("attention:") for x in f), (
            where, f)
        assert f == ((["reconcile"] if "reconcile" in f else []) + sorted(x for x in f if x.startswith("attention:"))
                     + (["cap"] if "cap" in f else [])), (where, f)
    return bool(added)


@pytest.fixture(scope="module")
def t34b2_base(tmp_path_factory):
    """The order step of this ticket's base revision, from git, loaded under its own name."""
    root = Path(__file__).resolve().parents[2]
    src = subprocess.run(["git", "-C", str(root), "show", f"{T34B2_BASE}:valedia_trader/execution_and_risk/order_step.py"],
                         capture_output=True, check=True).stdout
    folder = tmp_path_factory.mktemp("t34b2_base_order_step")
    path = folder / "order_step_t34b2_base.py"
    path.write_bytes(src)
    spec = importlib.util.spec_from_file_location("order_step_t34b2_base", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["order_step_t34b2_base"] = mod
    spec.loader.exec_module(mod)
    assert hasattr(mod._Run, "_judge_marks") and "working_stop" not in src.decode()
    return mod


@pytest.mark.parametrize("seed_block", range(6))
def test_t34b2_practice_runs_are_the_bases_but_for_faults_where_a_fault_marks_them(tmp_path, monkeypatch, t34b2_base,
                                                                                   seed_block):
    """50 scenarios per block, 300 in all, of the scheduled run and the paper ticket flow (kills, partial fills,
    refusals, wrong numbers), through f88593e and here: venue calls, journal lines, ledger, state and tickets equal; the
    summaries equal but for `faults`, carried only where F1 or F2 marks the run."""
    runs = marked = pending = sends = 0
    for k in range(50):
        seed = 990_000 + seed_block * 50 + k
        base = _flow_scenario(t34b2_base, tmp_path / f"base{k}", seed, monkeypatch)
        mine = _flow_scenario(osx, tmp_path / f"mine{k}", seed, monkeypatch)
        assert len(base) == len(mine)
        for i, (b, m) in enumerate(zip(base, mine)):
            added = _t34_same_but(b, m, T34B2_PRACTICE_KEYS, (seed, i))
            if _t34_practice_marks(added, (seed, i)):
                marked += "faults" in added
                pending += "reconcile_pending" in added
                print(f"T34B2-MARKED {seed} run {i}: {added.get('faults')} pending {added.get('reconcile_pending')}")
        runs += len(mine)
        sends += sum(1 for r in mine for c in r["calls"] if c[0] == "submit")
        _shutil.rmtree(tmp_path / f"base{k}")
        _shutil.rmtree(tmp_path / f"mine{k}")
    print(f"T34B2-PRACTICE block {seed_block}: runs {runs}, marked {marked}, pending {pending}, sends {sends}")
    assert runs >= 200 and sends > 30 and marked + pending > 0, (runs, marked, pending, sends)


def _t34_live_scenario(mod, base_mod, tmp, seed, monkeypatch, venue=None, before_run=None):
    """One generated live scenario, run by run through `mod` (this revision); before each run the whole world (files,
    venue, second source, clock, prices) is copied and the same run is made through `base_mod` from the copy, so that
    each comparison has the same inputs. Returns per run: the orders each sent, whether a day stop was in force, the
    summary keys added. T34b-3: `venue` (a function of held and cash) gives another stand-in, and `before_run` is
    called with the world before each run is copied."""
    rng = random.Random(seed)
    cap = rng.choice([2500, 6000, 20_000])
    mov = rng.choice([m for m in (400, 900, 2500, 5000) if m <= cap])
    held = rng.choice([{"OWN": 10.0}, {"OWN": 10.0, "QQQ": 2.0}])
    wall = rng.choice([0.5, 1.0, 2.0, 5.0, 99.0])
    w = live_world(tmp / "w", venue=(venue or LiveVenue)(held=held, cash=rng.choice([2_000.0, 9_500.0, 40_000.0])),
                   conf=live_conf(max_order_value=mov, max_capital=cap), limits={**LIVE_LIMITS, "max_daily_loss_pct": wall})
    n = rng.choice([0, 10, 24, 25, 30])
    if n:
        size = rng.choice([0.001, 0.004, 0.01])
        _t34_paper_book(w.journal, [rng.choice([size, -size, size / 2, 0.0]) for _ in range(n)])
    capital = float(rng.choice([cap, round(cap * rng.uniform(0.3, 1.0), 2)]))
    out_runs = []
    counter = [0]

    def one(**kw):
        counter[0] += 1
        if before_run is not None:
            before_run(w)
        f = _follow_the_book(w)
        before = set(w.book()) | set(held)
        w.second.verdicts = {s: ("agree" if s in before else rng.choice(["agree", "agree", "agree", "disagree"]))
                             for s in ("SPY", "QQQ", "IWM", "TLT", "GLD")}
        results = {}
        for name, which in (("base", base_mod), ("mine", mod)):
            if name == "base":
                copy_dir = tmp / f"copy{counter[0]}"
                _shutil.copytree(w.tmp, copy_dir, symlinks=True)
                wb = _copy.copy(w)
                wb.tmp, wb.journal = copy_dir, Journal(copy_dir / "journal")
                wb.ledger_path = copy_dir / w.ledger_path.relative_to(w.tmp)
                wb.state_path = copy_dir / Path(w.state_path).relative_to(w.tmp)
                wb.limits, wb.latch = copy_dir / "config" / "risk.json", copy_dir / w.latch.relative_to(w.tmp)
                wb.venue, wb.second = _copy.deepcopy(w.venue), _copy.deepcopy(w.second)
                wb.clock, wb.prices, wb.quote_age = Clock(w.clock.t), dict(w.prices), dict(w.quote_age)
                wb.quote_calls, target, repo = [], wb, copy_dir
            else:
                target, repo = w, w.tmp
                lines_before = len(w.entries("day_stop"))                # addendum 2: the trip's own journal line
            _wb.LIVE_REPO = repo
            urandom = random.Random(seed * 1000 + counter[0])
            monkeypatch.setattr("os.urandom", lambda k: bytes(urandom.getrandbits(8) for _ in range(k)))
            target.second.calls.clear()
            target.venue.calls.clear()
            out = _run_with(which, target, live=target.live, second_opinion=target.second, capital=capital,
                            follow=which.Follow(**dataclasses.asdict(f)), **kw)
            results[name] = out
            if name == "base":
                _shutil.rmtree(copy_dir)
        _wb.LIVE_REPO = w.tmp
        b, m = results["base"], results["mine"]
        sent = {k: _Counter((s["symbol"], s["side"], s["units"]) for s in r["sent"]) for k, r in results.items()}
        lines = {k: _Counter((o["fund"], o["side"], o["units"]) for o in (r.get("ticket") or {}).get("orders") or [])
                 for k, r in results.items() if r.get("mode") == "propose"}
        out_runs.append({"base": sent["base"], "mine": sent["mine"], "lines": lines, "stop": "day_stop" in m,
                         "added": sorted(set(m) - set(b)), "status": (b["status"], m["status"], b["stage"], m["stage"]),
                         "mine_out": m, "base_out": b, "trip_lines": len(w.entries("day_stop")) - lines_before})
        return m
    for day in range(rng.randint(2, 3)):
        if day:
            w.next_day()
            w.venue.cash_value = round(w.venue.cash_value * rng.choice([1.0, 0.998, 0.99, 0.98, 0.95]), 2)
        for sym in ("SPY", "QQQ", "IWM", "TLT", "GLD", "OWN"):
            w.prices[sym] = rng.choice([20.0, 55.5, 101.25, 230.0])
        for _ in range(rng.randint(1, 2)):
            wanted = {s: float(rng.randint(0, 6)) for s in rng.sample(["SPY", "QQQ", "IWM", "TLT", "GLD"], rng.randint(1, 5))}
            w.paper = paper({"a": wanted}, equity=capital)
            number = one(propose=True).get("ticket_number")
            if number:
                w.venue.fill_at_once = rng.choice([0.0, 0.5, 1.0])
                if rng.random() < 0.3:                                 # a loss between the ticket and its approval
                    w.venue.cash_value = round(w.venue.cash_value * rng.choice([0.99, 0.97, 0.9]), 2)
                w.later(1)
                one(approve=number)
            w.later(20)
    monkeypatch.undo()
    return out_runs


@pytest.mark.parametrize("seed_block", range(6))
def test_t34b2_live_runs_send_a_subset_of_the_bases_orders_and_the_same_without_a_day_stop(tmp_path, monkeypatch,
                                                                                         t34b2_base, seed_block):
    """50 scenarios per block, 300 in all, each of two or three New York days with losses, walls and paper records
    drawn at random; every run made from the same inputs through f88593e and here."""
    stats = _Counter()
    for k in range(50):
        seed = 660_000 + seed_block * 50 + k
        for i, r in enumerate(_t34_live_scenario(osx, t34b2_base, tmp_path / f"s{k}", seed, monkeypatch)):
            where = (seed, i)
            mine_lines, base_lines = r["lines"].get("mine", _Counter()), r["lines"].get("base", _Counter())
            assert not (r["mine"] - r["base"]) and not (mine_lines - base_lines), where   # never more than the base
            if not r["stop"]:                                                           # and without a day stop, the same
                assert r["mine"] == r["base"] and mine_lines == base_lines, where
                assert r["status"][0] == r["status"][1] and r["status"][2] == r["status"][3], (where, r["status"])
            assert set(r["added"]) <= set(T34B2_LIVE_KEYS) and "ceiling" in r["added"], (where, r["added"])
            stats["runs"] += 1
            stats["stopped days"] += r["stop"]
            stats["fewer sent"] += r["mine"] != r["base"]
            stats["fewer ticket lines"] += mine_lines != base_lines
            stats["sent"] += sum(r["mine"].values())
            assert "cap" not in r["mine_out"].get("faults", []), (where, r["mine_out"].get("faults"))   # an honest broker
            stats["over ceiling"] += "over_ceiling" in r["mine_out"]
            tripped = r["stop"] and not r["mine_out"]["day_stop"].get("carried")   # one line exactly when it tripped
            assert r["trip_lines"] == (1 if tripped else 0), (where, r["trip_lines"], r["mine_out"].get("day_stop"))
            stats["trip lines"] += r["trip_lines"]
        _shutil.rmtree(tmp_path / f"s{k}")
    print(f"T34B2-LIVE block {seed_block}: {dict(stats)}")
    assert stats["runs"] >= 150 and stats["stopped days"] > 5 and stats["sent"] > 20, stats
    assert stats["fewer sent"] + stats["fewer ticket lines"] > 0, stats


# ---- T34b-3: the verdict of a fill in its own line, carried until a summary covers it; the notices; venue_read

from valedia_trader.execution_and_risk import orders as _orders  # noqa: E402


class _Priced(Booked):
    """A practice stand-in whose orders report their average at the limit times the `buy` (a purchase) or `sell` (a
    sale) factor in force when each order was taken; `avg` overrides an order's average by tag; an order taken while
    `no_avg` is set reports no average (`unpriced`, by tag); `answers` gives, by tag, status fields to lay over the
    reads in turn (the last one stays); `raise_status` and `raise_positions` make those reads raise."""
    buy = sell = 1.0

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.avg, self.factor, self.unpriced, self.answers = {}, {}, set(), {}
        self.no_avg = self.raise_status = self.raise_positions = False

    def _take(self, symbol, units, tag, limit_price):
        self.factor[tag] = self.buy if units > 0 else self.sell
        if self.no_avg:
            self.unpriced.add(tag)
        super()._take(symbol, units, tag, limit_price)

    def order_status(self, tag):
        if self.raise_status:
            self.calls.append(("order_status", tag))
            raise ConnectionError("order_status is down")
        out = super().order_status(tag)
        if out is not None and out["avg_price"] is not None:
            out["avg_price"] = None if tag in self.unpriced else self.avg.get(tag, out["avg_price"] * self.factor.get(tag, 1.0))
        if out is not None and self.answers.get(tag):
            out.update(self.answers[tag][0] if len(self.answers[tag]) == 1 else self.answers[tag].pop(0))
        return out

    def positions(self):
        if self.raise_positions:
            self.calls.append(("positions", None))
            raise ConnectionError("positions is down")
        return super().positions()


def _pw(tmp, **venue_kw):
    w = ticket_world(tmp, _Priced(held={"OWN": 10.0}))
    for k, v in venue_kw.items():
        setattr(w.venue, k, v)
    return w


def _prun(w, wanted=None, **kw):
    if wanted is not None:
        w.paper = paper(wanted)
    return w.run(**kw)


def _kill_at(monkeypatch, name, times=1):
    """The run's own method `name` dies (KeyboardInterrupt) the first `times` times it is called."""
    real, left = getattr(osx._Run, name), [times]

    def dying(self, *a, **k):
        if left[0] > 0:
            left[0] -= 1
            raise KeyboardInterrupt("killed here by the test")
        return real(self, *a, **k)
    monkeypatch.setattr(osx._Run, name, dying)


def _breach_run(w, monkeypatch, kill="_judge_final"):
    """A practice run whose IWM 3 fills at once at 1.05 times its limit of 202 (a breach of (iii)), killed at `kill`
    after its fill line (None: not killed, the summary returned)."""
    w.venue.fill_at_once, w.venue.buy = 1.0, 1.05
    try:
        if kill is None:
            return _prun(w, {"a": {"IWM": 3.0}})
        _kill_at(monkeypatch, kill)
        with pytest.raises(KeyboardInterrupt):
            _prun(w, {"a": {"IWM": 3.0}})
        monkeypatch.undo()
    finally:
        w.venue.buy = 1.0


def _capped(w):
    return [e["cap_refs"] for e in w.entries("summary") if "cap_refs" in e]


# -- E: whole cents and no half-cent band

def test_t34b3_every_limit_the_order_step_can_send_is_a_whole_number_of_cents():
    from decimal import Decimal
    rng = random.Random(3434)
    quotes = [0.004, 0.005, 0.0051, 0.01, 0.0149, 0.05, 0.5001, 0.999, 1.0, 1.005, 99.995, 5000.0, 99_999.99, 1e7]
    quotes += [rng.uniform(0.001, 1.0) for _ in range(3000)] + [rng.uniform(1, 200_000) for _ in range(3000)]
    seen = 0
    for q in quotes:
        for units in (3.0, -3.0, 0.00123, -0.5):
            for reduces in (False, True):
                lp = osx.limit_price(units, q, reduces)
                if lp is None:
                    continue
                seen += 1
                assert lp > 0 and Decimal(repr(lp)) == Decimal(repr(lp)).quantize(Decimal("0.01")), (q, units, lp)
    assert seen > 40_000 and not hasattr(osx, "HALF_A_CENT")


@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("off, fault", [(0.0, False), (1.0, False), (1.0 + 2e-9, True), (20.0, True)])
def test_t34b3_the_limit_band_is_one_part_in_a_million_of_the_limit_at_its_exact_edge(tmp_path, side, off, fault):
    # E and ruling 14: `off` in units of LIMIT_SAME x limit through the limit; 1.0 is the edge (LIMIT_EDGE: no breach);
    # 20 is 0.004 on 202 or 0.0039 on 196, a breach now, inside the half cent the old band allowed
    w = _pw(tmp_path / "w", fill_at_once=1.0)
    if side == "sell":
        assert _prun(w, {"a": {"IWM": 3.0}})["orders_sent"] == 1
        w.later(30)
    setattr(w.venue, side, 1.0 + off * osx.LIMIT_SAME if side == "buy" else 1.0 - off * osx.LIMIT_SAME)
    out = _prun(w, {"a": {"IWM": 3.0 if side == "buy" else 0.0}})
    assert out["orders_sent"] == 1 and out["status"] == "done", out
    assert ("cap" in out.get("faults", [])) is fault, out
    assert w.entries("fill")[-1]["verdict"] == ("breach" if fault else "ok")


# -- F (ruling 14): two tests of what the last check left untested; no behaviour change

def test_t34b3_a_fill_journaled_at_the_first_reconcile_sets_its_symbol_aside_at_the_final_one(tmp_path):
    w = _pw(tmp_path / "w")
    assert _prun(w, {"a": {"IWM": 3.0}})["orders_sent"] == 1
    w.venue.fill(w.venue.submits()[-1])                # filled between the runs: an order of an earlier batch, finished
    w.later(30)
    start, real = len(w.venue.calls), w.venue.positions

    def positions():
        out = real()
        if any(c[0] == "quotes" for c in w.venue.calls[start:]):    # after the cancels: the final read lags that fill
            out.pop("IWM", None)
        return out
    w.venue.positions = positions
    out = _prun(w)
    assert out["status"] == "done" and out["fills_journaled"] == 1 and "faults" not in out, out
    assert out["reconcile_pending"] == {"symbols": ["IWM"], "positions_unread": False, "status_unread": 0}, out


def test_t34b3_cap_unjudged_is_written_on_a_practice_summary(tmp_path):
    w = _pw(tmp_path / "w", fill_at_once=1.0, no_avg=True)
    out = _prun(w, {"a": {"IWM": 3.0}})
    assert out["status"] == "done" and out["cap_unjudged"] == ["IWM"] and "faults" not in out, out
    assert w.entries("summary")[-1]["cap_unjudged"] == ["IWM"] and "live" not in out


# -- D: the verdict in the fill line, carried until a summary covers it

def test_t34b3_the_fill_line_carries_its_verdict_as_its_last_field(tmp_path):
    w = _pw(tmp_path / "w", fill_at_once=1.0)
    _prun(w, {"a": {"IWM": 3.0}})
    w.later(30)
    w.venue.buy = 1.05
    assert _prun(w, {"a": {"IWM": 6.0}})["faults"] == ["cap"]
    w.venue.buy, w.venue.no_avg = 1.0, True
    w.later(30)
    assert _prun(w, {"a": {"IWM": 9.0}})["cap_unjudged"] == ["IWM"]
    lines = w.entries("fill")
    assert [e["verdict"] for e in lines] == ["ok", "breach", "unjudged"] and all(list(e)[-1] == "verdict" for e in lines)


def test_t34b3_a_breach_whose_run_dies_before_its_summary_is_carried_by_exactly_one_later_summary(tmp_path,
                                                                                                 monkeypatch):
    w = _pw(tmp_path / "w")
    _breach_run(w, monkeypatch)                                  # KeyboardInterrupt between the line and the summary
    (line,) = w.entries("fill")
    assert line["verdict"] == "breach" and w.entries("summary") == []
    for k in range(5):
        w.later(30)
        out = _prun(w)
        assert out.get("faults") == (["cap"] if k == 0 else None), (k, out)
        assert out.get("cap_refs") == ([f"fill-{line['fill_id']}"] if k == 0 else None), (k, out)
    assert _capped(w) == [[f"fill-{line['fill_id']}"]]


def test_t34b3_a_breach_whose_carrying_run_also_dies_is_carried_by_the_run_after(tmp_path, monkeypatch):
    w = _pw(tmp_path / "w")
    _breach_run(w, monkeypatch)
    w.later(30)
    _kill_at(monkeypatch, "_judge_final")
    with pytest.raises(KeyboardInterrupt):
        _prun(w)
    monkeypatch.undo()
    w.later(30)
    out = _prun(w)
    assert out["faults"] == ["cap"] and out["cap_refs"] == ["fill-1"], out
    w.later(30)
    assert "faults" not in _prun(w) and _capped(w) == [["fill-1"]]


def test_t34b3_a_ledger_read_that_raises_while_judging_writes_no_fill_line_and_the_next_run_judges_it(tmp_path,
                                                                                                    monkeypatch):
    w = _pw(tmp_path / "w", fill_at_once=1.0, buy=1.05)
    real = _Ledger.fills

    def fills(self, tag=None):
        if tag is not None:
            raise _orders.OrderError("the order ledger could not be read: disk I/O error")
        return real(self, tag)
    monkeypatch.setattr(_Ledger, "fills", fills)
    out = _prun(w, {"a": {"IWM": 3.0}})
    monkeypatch.undo()
    assert out["status"] == "stopped" and out["stage"] == "after" and w.entries("fill") == [], out
    assert "faults" not in out and "cap_refs" not in out
    w.later(30)
    out = _prun(w)
    assert out["faults"] == ["cap"] and out["cap_refs"] == ["fill-1"] and w.entries("fill")[0]["verdict"] == "breach"


def test_t34b3_a_summary_that_cannot_be_written_leaves_the_breach_for_the_next_run(tmp_path, monkeypatch):
    w = _pw(tmp_path / "w")
    real, failed = Journal.write, []

    def write(self, day, entry):
        if entry.get("event") == "summary" and not failed:
            failed.append(entry)
            raise OSError("no space left on device")
        return real(self, day, entry)
    monkeypatch.setattr(Journal, "write", write)
    out = _breach_run(w, monkeypatch, kill=None)
    assert out["faults"] == ["cap"] and out["cap_refs"] == ["fill-1"] and "journal_error" in out, out
    assert w.entries("summary") == []
    w.later(30)
    out = _prun(w)
    assert out["faults"] == ["cap"] and out["cap_refs"] == ["fill-1"], out


def test_t34b3_a_failure_while_the_faults_are_worked_out_leaves_the_breach_for_the_next_run(tmp_path, monkeypatch):
    w = _pw(tmp_path / "w")
    _t34_faults_fail(monkeypatch)
    out = _breach_run(w, monkeypatch, kill=None)
    monkeypatch.undo()
    assert out["stage"] == "unexpected" and "cap_refs" not in out and "faults" not in out, out
    w.later(30)
    out = _prun(w)
    assert out["faults"] == ["cap"] and out["cap_refs"] == ["fill-1"], out


def test_t34b3_a_mark_applied_that_fails_after_the_line_is_covered_once_and_never_judged_twice(tmp_path, monkeypatch):
    w = _pw(tmp_path / "w", fill_at_once=1.0, buy=1.05)

    def mark(self, ids):
        raise _orders.OrderError("the order ledger could not be written: database is locked")
    monkeypatch.setattr(_Ledger, "mark_applied", mark)
    out = _prun(w, {"a": {"IWM": 3.0}})
    monkeypatch.undo()
    assert out["stage"] == "after" and out["faults"] == ["cap"] and out["cap_refs"] == ["fill-1"], out
    for _ in range(3):
        w.later(30)
        assert "faults" not in _prun(w)
    assert len(w.entries("fill")) == 1 and _capped(w) == [["fill-1"]]


# -- B1: who may carry

def test_t34b3_a_breach_is_carried_only_by_a_run_of_its_venue_on_an_exchange_session(tmp_path, monkeypatch):
    w = _pw(tmp_path / "w")
    w.clock.t = ny("2026-11-25 10:00")                            # a Wednesday; Thursday is Thanksgiving
    _breach_run(w, monkeypatch)
    other = ticket_world(tmp_path / "o", _Priced(held={"OWN": 10.0}, name="other-sandbox"))
    other.journal, other.clock = w.journal, w.clock               # the same journal, another venue
    w.clock.t = ny("2026-11-25 11:00")
    out = _prun(other, {})
    assert out["status"] == "done" and "faults" not in out and "cap_refs" not in out, out
    w.clock.t = ny("2026-11-26 10:00")
    out = _prun(w)
    assert out["status"] == "stopped" and out["stage"] == "session", out
    assert "faults" not in out and "cap_refs" not in out, out
    w.clock.t = ny("2026-11-27 11:00")                            # Friday, a half day
    out = _prun(w)
    assert out["status"] == "done" and out["faults"] == ["cap"] and out["cap_refs"] == ["fill-1"], out
    w.clock.t = ny("2026-11-27 11:30")
    assert "faults" not in _prun(other) and "faults" not in _prun(w)


def test_t34b3_a_run_after_the_close_of_a_session_day_carries_it_and_the_ladder_calls_that_day_a_fault(tmp_path,
                                                                                                    monkeypatch):
    w = _pw(tmp_path / "w")
    _breach_run(w, monkeypatch)
    w.clock.t = ny("2026-11-13 17:00")
    out = _prun(w)
    assert out["stage"] == "session" and out["faults"] == ["cap"] and out["cap_refs"] == ["fill-1"], out
    facts = _sl.session_facts(w.journal.read(), w.venue.name, CAL)
    assert [(str(f.day), f.kind) for f in facts] == [("2026-11-13", "fault")], facts


def test_t34b3_a_breach_followed_by_ten_days_without_a_run_is_still_carried(tmp_path, monkeypatch):
    w = _pw(tmp_path / "w")
    _breach_run(w, monkeypatch)
    for _ in range(10):
        w.next_day()
    out = _prun(w)
    assert out["faults"] == ["cap"] and out["cap_refs"] == ["fill-1"], out


def test_t34b3_the_scan_reads_every_journal_day_and_four_hundred_days_cost_little(tmp_path, monkeypatch):
    import time
    w = _pw(tmp_path / "w")
    days = [str(d.date()) for d in CAL.sessions_in_range("2024-01-02", "2026-11-12")[-400:]]
    (w.tmp / "journal").mkdir(parents=True, exist_ok=True)
    for k, day in enumerate(days):
        lines = [{"type": "order_step", "venue": w.venue.name, "batch": f"b{k}", "event": "fill", "fill_id": 10_000 + 30 * k + j,
                  "tag": f"b{k}-IWM", "symbol": "IWM", "units": 1.0, "price": 200.0, "price_covers_units": 1.0, "fee": 0.0,
                  "verdict": "breach" if (k, j) in ((0, 0), (5, 1)) else "ok"} for j in range(28)]
        lines.append({"type": "order_step", "venue": w.venue.name, "batch": f"b{k}", "event": "summary", "status": "done",
                      "stage": None, **({"faults": ["cap"], "cap_refs": ["fill-10151"]} if k == 5 else {})})
        lines.append({"type": "summary", "equity": 1.0})
        (w.tmp / "journal" / f"{day}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in lines))
    t0 = time.perf_counter()
    out = _prun(w)
    took = time.perf_counter() - t0
    print(f"T34B3-SCAN 400 days, {400 * 30} lines: one run {took:.3f} s")
    assert out["status"] == "done" and out["cap_refs"] == ["fill-10000"], out    # the oldest day is read; day 5 covered
    assert took < 5.0


# -- R4 and R3

def test_t34b3_fill_lines_without_a_verdict_are_neither_carried_nor_judged_again(tmp_path):
    w = _pw(tmp_path / "w")
    for fid, price in ((7, 999.0), (8, None)):
        w.journal.write("2026-11-12", {"type": "order_step", "venue": w.venue.name, "batch": f"old-{fid}", "event": "fill",
                                       "fill_id": fid, "tag": f"old-{fid}-IWM", "symbol": "IWM", "units": 3.0, "price": price,
                                       "price_covers_units": 3.0, "fee": 0.0})
    out = _prun(w, {"a": {"SPY": 2.0}})
    assert out["status"] == "done" and "faults" not in out and "cap_unjudged" not in out and "cap_refs" not in out, out
    assert w.entries("verdict") == []


def test_t34b3_an_unjudged_fill_stays_named_until_it_settles_by_one_verdict_line(tmp_path):
    w = _pw(tmp_path / "w", fill_at_once=1.0, no_avg=True)
    assert _prun(w, {"a": {"IWM": 3.0}})["cap_unjudged"] == ["IWM"]
    (tag,) = w.venue.submits()
    assert w.entries("fill")[0]["verdict"] == "unjudged"
    w.later(30)
    out = _prun(w)
    assert out["cap_unjudged"] == ["IWM"] and "faults" not in out and w.entries("verdict") == [], out
    w.venue.unpriced.discard(tag)                                 # the average comes at last, through the limit
    w.venue.factor[tag] = 1.05
    w.later(30)
    out = _prun(w)
    # addendum 4: the write that priced the units (an adjustment row, fill-2) judges the figure and carries the breach;
    # fill-1 settles naming it, with no breach of its own (one figure, one ref)
    assert out["faults"] == ["cap"] and out["cap_refs"] == ["fill-2"] and "cap_unjudged" not in out, out
    assert [(e["ref"], e["verdict"], e["covered_by"]) for e in w.entries("verdict")] == [("fill-1", "ok", "fill-2")]
    w.later(30)
    out = _prun(w)
    assert "faults" not in out and "cap_unjudged" not in out and len(w.entries("verdict")) == 1, out


# -- A and B2: the notices, journaled and judged once each

def _restating(tmp):
    """A practice world whose first run bought IWM 3, filled at once at its limit of 202."""
    w = _pw(tmp, fill_at_once=1.0)
    out = _prun(w, {"a": {"IWM": 3.0}})
    (tag,) = w.venue.submits()
    assert out["status"] == "done" and "faults" not in out and w.fills()[0]["price"] == 202.0
    return w, tag


def test_t34b3_a_restated_average_is_journaled_as_a_notice_with_its_verdict_and_named_in_the_summary(tmp_path):
    w, tag = _restating(tmp_path / "w")
    w.venue.avg[tag] = 201.995                                   # restated by half a cent
    w.later(30)
    out = _prun(w)
    assert out["status"] == "done" and "faults" not in out and out["notices"] == ["avg_restated"], out
    (n,) = w.entries("notice")
    assert (n["kind"], n["tag"], n["old"], n["new"], n["verdict"]) == ("avg_restated", tag, 202.0, 201.995, "ok"), n
    assert type(n["notice_id"]) is int and w.orders()[0]["avg_price"] == 201.995
    w.later(30)
    out = _prun(w)
    assert "notices" not in out and "faults" not in out and len(w.entries("notice")) == 1, out


def test_t34b3_three_restatements_give_three_notices_judged_once_and_the_third_through_the_limit_is_cap(tmp_path):
    w, tag = _restating(tmp_path / "w")
    outs = []
    for avg in (201.995, 201.999, 202.004):
        w.venue.avg[tag] = avg
        w.later(30)
        outs.append(_prun(w))
    notes = w.entries("notice")
    assert [n["verdict"] for n in notes] == ["ok", "ok", "breach"] and len({n["notice_id"] for n in notes}) == 3, notes
    assert [o.get("faults") for o in outs] == [None, None, ["cap"]], outs
    assert outs[2]["cap_refs"] == [f"notice-{notes[2]['notice_id']}"], outs[2]
    w.later(30)
    assert "faults" not in _prun(w) and len(w.entries("notice")) == 3


@pytest.mark.parametrize("kill", ["_journal_notices", "_judge_final"])
def test_t34b3_a_restatement_through_the_limit_survives_a_death_before_its_line_or_before_the_summary(tmp_path,
                                                                                                     monkeypatch, kill):
    w, tag = _restating(tmp_path / "w")
    w.venue.avg[tag] = 202.004
    w.later(30)
    _kill_at(monkeypatch, kill)
    with pytest.raises(KeyboardInterrupt):
        _prun(w)
    monkeypatch.undo()
    assert len(w.entries("notice")) == (0 if kill == "_journal_notices" else 1)
    w.later(30)
    out = _prun(w)
    (n,) = w.entries("notice")
    assert n["verdict"] == "breach" and out["faults"] == ["cap"] and out["cap_refs"] == [f"notice-{n['notice_id']}"], out
    w.later(30)
    assert "faults" not in _prun(w) and len(w.entries("notice")) == 1


@pytest.mark.parametrize("first, second, verdict", [(202.0, 201.79, "ok"), (202.5, 202.29, "breach")])
def test_t34b3_an_unpriced_piece_is_judged_on_the_orders_average(tmp_path, first, second, verdict):
    w = _pw(tmp_path / "w")
    assert _prun(w, {"a": {"IWM": 1001.0}})["orders_sent"] == 1
    (tag,) = w.venue.submits()
    w.venue.held["IWM"] = 1001.0
    w.venue.answers[tag] = [{"state": "partly_filled", "filled_units": 1000.0, "avg_price": first},
                            {"state": "cancelled", "filled_units": 1001.0, "avg_price": second}]
    w.later(30)
    out = _prun(w)
    lines = w.entries("fill")
    assert [(e["units"], e["price"] is None, e["verdict"]) for e in lines] == [(1000.0, False, verdict),
                                                                               (1.0, True, verdict)], lines
    # addendum 4: each line judges the figure its own write set; the notice line carries no verdict
    assert [e["judged_avg"] for e in lines] == [first, second], lines
    (n,) = w.entries("notice")
    assert (n["kind"], n["old"], n["new"]) == ("piece_unpriced", first, second) and "verdict" not in n, n
    assert out["notices"] == ["piece_unpriced"] and "cap_unjudged" not in out, out
    assert out.get("faults") == (["cap"] if verdict == "breach" else None), out


# -- G: an unreadable venue is a skipped day

def _resting(tmp):
    """A practice world whose first run (2026-11-13) left IWM 3 resting at the venue; the clock at the next session."""
    w = _pw(tmp)
    assert _prun(w, {"a": {"IWM": 3.0}})["orders_sent"] == 1
    w.next_day()
    return w


def _venue_down(w):
    w.venue.raise_status = w.venue.raise_positions = True
    w.venue.raise_on = {"cancel"}


def test_t34b3_an_outage_alone_is_a_venue_read_stop_that_sends_nothing_and_the_ladder_skips(tmp_path):
    w = _resting(tmp_path / "w")
    _venue_down(w)
    before = len(w.venue.calls)
    out = _prun(w, {"a": {"IWM": 3.0, "SPY": 2.0}})
    assert out["status"] == "stopped" and out["stage"] == "venue_read" and out["orders_sent"] == 0, out
    assert "positions" in out["reason"] and "status" in out["reason"] and "faults" not in out, out
    assert "submit" not in [c[0] for c in w.venue.calls[before:]]
    assert "venue_read" in _sl.SKIP_STAGES and "venue_read" not in _sl.FAULT_STAGES
    facts = _sl.session_facts(w.journal.read(), w.venue.name, CAL)
    assert [(str(f.day), f.kind) for f in facts] == [("2026-11-13", "clean"), ("2026-11-16", "skipped")], facts


@pytest.mark.parametrize("how", ["positions answer", "order read open", "unknown state", "unsettled order",
                                 "anomaly first", "difference first", "item in the run", "older item", "ledger write",
                                 "entry not written"])
def test_t34b3_an_outage_with_anything_else_wrong_stays_a_reconcile_stop(tmp_path, monkeypatch, how):
    w = _resting(tmp_path / "w")
    (tag,) = w.venue.submits()
    _venue_down(w)
    if how == "positions answer":                                # (a): the positions read did not raise
        w.venue.raise_positions = False
    elif how == "order read open":                               # R2: the venue read it as open after the cancel
        w.venue.raise_status, w.venue.raise_on, w.venue.cancel_answer = False, set(), "not yet"
    elif how == "unknown state":                                 # (b): an unresolved entry of another kind
        w.venue.raise_status, w.venue.raise_on = False, set()
        w.venue.answers[tag] = [{"state": "mystery"}]
    elif how == "unsettled order":                               # (c)
        _t34_ledger_state(w, tag, "unknown")
    elif how == "anomaly first":                                 # R1, (d): a blocking anomaly in the first reconcile
        w.venue.raise_status, n = False, [0]

        def status(t):
            n[0] += 1
            if n[0] == 1:
                return {"state": "partly_filled", "filled_units": 99.0, "avg_price": 200.0, "fee": 0.0,
                        "venue_order_id": None, "raw_status": None}
            raise ConnectionError("order_status is down")
        w.venue.order_status = status
    elif how == "difference first":                              # R1, (d): a position difference in the first one
        w.venue.raise_positions, n = False, [0]

        def positions():
            n[0] += 1
            if n[0] == 1:
                return {"OWN": 10.0, "XLE": 1.0}
            raise ConnectionError("positions is down")
        w.venue.positions = positions
    elif how == "item in the run":                               # (d): a blocking item opened in this run
        def cancel(t):
            _t34_flag(w, ["turned_up"], prefix="in-run")
            raise ConnectionError("cancel is down")
        w.venue.cancel = cancel
    elif how == "older item":                                    # an open blocking item from before the run
        _t34_flag(w, ["late_outcome"], prefix="old")
    elif how == "entry not written":                             # (e): a status read, its write failed
        w.venue.raise_status = False
        w.venue.fill(tag, 1.0)
        real_cas = _Ledger._cas

        def cas(self, t, *a, **k):
            if t == tag:
                raise sqlite3.OperationalError("disk I/O error")
            return real_cas(self, t, *a, **k)
        monkeypatch.setattr(_Ledger, "_cas", cas)
    elif how == "ledger write":                                  # (e): storing the reconcile fails
        real_rec = _Ledger.reconcile

        def reconcile(self, *a, **k):
            real_rec(self, *a, **k)
            raise _orders.OrderError("the order ledger could not be written: disk I/O error")
        monkeypatch.setattr(_Ledger, "reconcile", reconcile)
    out = _prun(w, {"a": {"IWM": 3.0}})
    assert out["status"] == "stopped" and out["stage"] == "reconcile" and out["orders_sent"] == 0, (how, out)


# -- B3: the value test in the fill's verdict, each purchase fill on its own

def test_t34b3_the_value_test_is_in_the_fill_verdict_and_is_carried_when_the_run_dies(tmp_path, monkeypatch):
    venue = _ThroughLimit(held={"OWN": 10.0}, cash=9_500.0)
    venue.buy = 1.0 + 5e-7                                        # inside the limit band: no breach of (iii)
    w = _t34_world(tmp_path / "w", venue=venue)
    w.prices["IWM"] = 198.02                                      # limit 200.00: 5 x 200.00 = max_order_value, lock 1 passes
    _, number = _live_proposed(w, {"a": {"IWM": 5.0}})
    w.venue.fill_at_once = 1.0
    w.later(1)
    _kill_at(monkeypatch, "_judge_final")
    with pytest.raises(KeyboardInterrupt):
        lapprove(w, number)                                       # the kill is spent: later runs call the real method
    (line,) = [e for e in w.entries("fill") if e["venue"] == w.venue.name]
    assert line["verdict"] == "breach" and line["units"] * line["price"] > 1000.0 > line["units"] * 200.0 * (1 - 1e-6)
    w.later(1)
    out = lpropose(w)
    assert out.get("faults") == ["cap"] and out.get("cap_refs") == [f"fill-{line['fill_id']}"], (out["stage"], out["reason"])


# -- the comparisons with this ticket's base (ed4d96f: its order step and its order ledger)

T34B3_BASE = "ed4d96f"
_T34B3_LEDGER_KEYS = ("kind", "positions_error_kind", "notices")


@pytest.fixture(scope="module")
def t34b3_base(tmp_path_factory):
    """The order step AND the order ledger of this ticket's base revision, from git, each under its own name (the base
    order step imports the base ledger)."""
    root = Path(__file__).resolve().parents[2]

    def show(path):
        return subprocess.run(["git", "-C", str(root), "show", f"{T34B3_BASE}:{path}"], capture_output=True,
                              check=True).stdout
    folder = tmp_path_factory.mktemp("t34b3_base")
    mods = {}
    for name, path, src in (
            ("orders_t34b3_base", "valedia_trader/execution_and_risk/orders.py", None),
            ("order_step_t34b3_base", "valedia_trader/execution_and_risk/order_step.py",
             lambda s: s.replace(b"from valedia_trader.execution_and_risk.orders import", b"from orders_t34b3_base import"))):
        data = show(path)
        data = src(data) if src else data
        (folder / f"{name}.py").write_bytes(data)
        spec = importlib.util.spec_from_file_location(name, folder / f"{name}.py")
        mods[name] = importlib.util.module_from_spec(spec)
        sys.modules[name] = mods[name]
        spec.loader.exec_module(mods[name])
    base = mods["order_step_t34b3_base"]
    assert hasattr(base, "HALF_A_CENT") and base.OrderLedger is mods["orders_t34b3_base"].OrderLedger
    assert not hasattr(mods["orders_t34b3_base"], "RECONCILE_NOTICE_KINDS")
    return base


def test_t51_the_base_refuses_a_layout4_ledger_in_words_and_changes_nothing(tmp_path, t34b3_base):
    """Ruling D: ed4d96f's order step, handed a ledger this revision wrote (layout 4), refuses it in words, so a
    roll-back never misreads a ledger; the file is left as it was. (The comparisons above give the base its own.)"""
    w = ticket_world(tmp_path / "w")
    w.paper = paper({"a": {"SPY": 2.0}})
    _run_with(osx, w)
    assert _dump(w.ledger_path)["version"] == 4
    raw = w.ledger_path.read_bytes()
    w.later(5)
    with pytest.raises(sys.modules["orders_t34b3_base"].OrderError,
                       match=r"different version of this module \(layout 4, this module writes layout 3\); it is not opened"):
        _run_with(t34b3_base, w)
    assert w.ledger_path.read_bytes() == raw


def _t34b3_ledger(dump, counts=None):
    """A ledger dump with the entry kinds T34b-3 adds to each stored reconcile record taken out (ruling 3), counted."""
    if dump is None:
        return None

    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if k not in _T34B3_LEDGER_KEYS}
        return [strip(v) for v in x] if isinstance(x, list) else x
    rows = dict(dump["rows"])
    out = []
    for r in rows.get("reconciles", []):
        r = list(r)
        new = json.dumps(strip(json.loads(r[-1])))
        if counts is not None and new != json.dumps(json.loads(r[-1])):
            counts["ledger: kinds in a stored reconcile record"] = counts.get("ledger: kinds in a stored reconcile record", 0) + 1
        out.append(tuple(r[:-1]) + (new,))
    rows["reconciles"] = out
    return {**dump, "rows": rows}


@pytest.mark.parametrize("seed_block", range(6))
def test_t34b3_practice_runs_equal_the_bases_but_for_the_allowed_differences(tmp_path, monkeypatch, t34b3_base, seed_block):
    """50 scenarios per block, 300 in all (the scheduled run and the paper ticket flow, kills, partial fills, refusals,
    outages), through ed4d96f and here: venue calls, journal lines, ledger, state and tickets equal but for what T34b-3
    is allowed to add, each kind counted."""
    counts, runs = {}, 0
    for k in range(50):
        seed = 3_400_000 + seed_block * 50 + k
        base = _flow_scenario(t34b3_base, tmp_path / f"base{k}", seed, monkeypatch)
        mine = _flow_scenario(osx, tmp_path / f"mine{k}", seed, monkeypatch)
        assert len(base) == len(mine)
        for i, (b, m) in enumerate(zip(base, mine)):
            last, seen = i == len(mine) - 1, {}             # lines and ledger rows counted once, at the scenario's end
            b, m = dict(b), dict(m)
            b["ledger"], m["ledger"] = _t34b3_ledger(b["ledger"]), _t34b3_ledger(m["ledger"], seen)
            added = _t34_same_but(b, m, (), (seed, i), counts=seen)
            assert added == {}, ((seed, i), added)
            for kind, n in seen.items():
                if last or kind.startswith(("summary key", "stage")):
                    counts[kind] = counts.get(kind, 0) + n
            runs += 1
        _shutil.rmtree(tmp_path / f"base{k}")
        _shutil.rmtree(tmp_path / f"mine{k}")
    print(f"T34B3-PRACTICE block {seed_block}: runs {runs}, {dict(sorted(counts.items()))}")
    assert runs >= 200 and counts.get("verdict field", 0) > 0, counts


import zlib  # noqa: E402


class _T34b3Live(LiveVenue):
    """The live stand-in with, by a hash of each tag (so a copy of the world behaves the same): 6% of orders filled a
    cent through their limit, 8% filled at their limit and restated a cent better on every later read (a notice), the
    rest honest; and `down`: every status, positions and cancel call raises."""
    seed = 0

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.reads, self.down = {}, False

    def _mode(self, tag):
        r = zlib.crc32(f"{self.seed}:{tag}".encode()) % 100
        return "through" if r < 6 else "restate" if r < 14 else "honest"

    def order_status(self, tag):
        if self.down:
            self.calls.append(("order_status", tag))
            raise ConnectionError("order_status is down")
        out = super().order_status(tag)
        if out is not None and out["avg_price"] is not None:
            o, mode = self.book[tag], self._mode(tag)
            sign = 1.0 if o["units"] > 0 else -1.0
            self.reads[tag] = self.reads.get(tag, 0) + 1
            if mode == "through":
                out["avg_price"] = round(o["limit"] + sign * 0.01, 2)
            elif mode == "restate" and self.reads[tag] > 1:
                out["avg_price"] = round(o["limit"] - sign * 0.01, 2)
        return out

    def positions(self):
        if self.down:
            self.calls.append(("positions", None))
            raise ConnectionError("positions is down")
        return super().positions()

    def cancel(self, tag):
        if self.down:
            self.calls.append(("cancel", tag))
            raise ConnectionError("cancel is down")
        return super().cancel(tag)


@pytest.mark.parametrize("seed_block", range(6))
def test_t34b3_live_runs_against_the_base_subset_equal_and_the_same_cap_marks(tmp_path, monkeypatch, t34b3_base,
                                                                              seed_block):
    """50 live scenarios per block, 300 in all, every run made from the same inputs through ed4d96f and here, with
    fills through their limit, restated averages and whole-venue outages. Until a scenario's first notice: the orders
    sent and the ticket lines equal the base's, and `cap` marks the same runs (B3); with an outage only, equal too (the
    run sends nothing either way). From a notice on, the base is held by a blocking anomaly that is now a notice:
    counted, with every order sent that the base did not send."""
    stats = _Counter()
    for k in range(50):
        seed = 3_460_000 + seed_block * 50 + k
        down = random.Random(seed + 77)

        def venue(held, cash, _seed=seed):
            v = _T34b3Live(held=held, cash=cash)
            v.seed = _seed
            return v

        def before(w):
            w.venue.down = down.random() < 0.08
        noticed = False
        for i, r in enumerate(_t34_live_scenario(osx, t34b3_base, tmp_path / f"s{k}", seed, monkeypatch, venue=venue,
                                                 before_run=before)):
            where, m, b = (seed, i), r["mine_out"], r["base_out"]
            noticed = noticed or bool(m.get("notices"))
            mine_lines, base_lines = r["lines"].get("mine", _Counter()), r["lines"].get("base", _Counter())
            stats["runs"] += 1
            stats["sent"] += sum(r["mine"].values())
            stats["venue_read"] += m.get("stage") == "venue_read"
            stats["notice runs"] += bool(m.get("notices"))
            stats["cap runs"] += "cap" in m.get("faults", [])
            if not noticed:
                assert r["mine"] == r["base"] and mine_lines == base_lines, where
                assert ("cap" in m.get("faults", [])) == ("cap" in b.get("faults", [])), (where, m.get("faults"), b.get("faults"))
                if m.get("stage") == "venue_read":
                    assert b.get("stage") == "reconcile" and m["orders_sent"] == b["orders_sent"] == 0, where
                else:
                    assert r["status"][0] == r["status"][1] and r["status"][2] == r["status"][3], (where, r["status"])
                assert set(r["added"]) <= {"notices", "cap_refs"}, (where, r["added"])
            else:
                stats["runs after a notice"] += 1
                stats["runs sending more than the base after a notice"] += bool(r["mine"] - r["base"])
                stats["runs sending less than the base after a notice"] += bool(r["base"] - r["mine"])
        _shutil.rmtree(tmp_path / f"s{k}")
    print(f"T34B3-LIVE block {seed_block}: {dict(stats)}")
    assert stats["runs"] >= 150 and stats["cap runs"] > 0 and stats["venue_read"] > 0 and stats["notice runs"] > 0, stats


# -- ordinary days write no fault and stop no run at reconcile; every fault is marked, or carried by the next run

class _Ordinary(Booked):
    """An honest broker on ordinary days, driven by `rng`: an order fills at once, in pieces between runs, or not at
    all, each piece at its limit or a few cents better; half the orders report their average rounded to the cent; a fee
    total of half a cent a unit, rounded; between runs, on its own (within two days of an order finishing): a finished
    order's average restated a cent better (A1, once an order), a late fill on a cancelled order still reported
    cancelled (A2), a fee total corrected down (A3), each between two runs of one day; a fill can race a cancel; `down` makes every status, positions and cancel call raise.
    `next_take` shapes the next TLT purchase taken (a test's fault), filled at once: {"add": price beyond the limit,
    "partial": the units filled (else all), "over": a unit filled beyond the order}."""

    def __init__(self, rng, held, cash, clock):
        super().__init__(held=held, cash=cash)
        self.rng, self.clock = rng, clock
        self.avg, self.fee, self.rounds, self.done_at = {}, {}, {}, {}
        self.down, self.next_take, self.counts, self.restated = False, None, _Counter(), set()
        self.status_back, self.read_once = False, set()   # addendum 3 M2: positions down, each status back at its 2nd read

    def _take(self, symbol, units, tag, limit_price):
        self.book[tag] = {"symbol": symbol, "units": float(units), "limit": limit_price, "filled": 0.0, "state": "open"}
        self.rounds[tag] = self.rng.random() < 0.5
        shape = self.next_take if symbol == "TLT" and units > 0 else None   # a shape goes to the next TLT purchase
        if shape is not None:
            self.next_take, self.shaped = None, tag
            self._piece(tag, shape.get("partial", abs(units)), add=shape.get("add", 0.0))
            if shape.get("over"):
                o = self.book[tag]
                o["filled"] += 1.0
                self.held[symbol] = self.held.get(symbol, 0.0) + (1.0 if units > 0 else -1.0)
            return
        r = self.rng.random()
        if r < 0.45:
            self._piece(tag, abs(units))
        elif r < 0.7 and abs(units) >= 2:
            self._piece(tag, float(int(abs(units) / 2)))

    def _piece(self, tag, amount, add=None, late=False):
        o = self.book[tag]
        before = o["filled"]
        if late:                                                  # A2: a cancelled order's late fill, still cancelled
            got = min(amount, abs(o["units"]) - before)
            if got <= 0:
                return
            o["filled"] += got
            self.held[o["symbol"]] = self.held.get(o["symbol"], 0.0) + (1.0 if o["units"] > 0 else -1.0) * got
        else:
            got = self.fill(tag, amount)
            if got <= 0:
                return
        sign = 1.0 if o["units"] > 0 else -1.0
        price = o["limit"] + sign * add if add is not None else o["limit"] - sign * self.rng.choice([0.0, 0.0, 0.01, 0.02, 0.05])
        old = self.avg.get(tag)
        avg = price if old is None else (old * before + price * got) / (before + got)
        self.avg[tag] = round(avg, 2) if self.rounds[tag] and add is None else avg
        self.fee[tag] = round(0.005 * o["filled"], 2)
        if o["state"] not in ("open", "partly_filled"):
            self.done_at.setdefault(tag, self.clock.t)

    def order_status(self, tag):
        self.calls.append(("order_status", tag))
        if self.down or (self.status_back and tag not in self.read_once):
            self.read_once.add(tag)
            raise ConnectionError("order_status is down")
        o = self.book.get(tag)
        if o is None:
            return None
        return {"state": o["state"], "filled_units": o["filled"], "avg_price": self.avg.get(tag) if o["filled"] else None,
                "fee": self.fee.get(tag, 0.0), "venue_order_id": "V" + tag[-6:], "raw_status": None}

    def positions(self):
        self.calls.append(("positions", None))
        if self.down or self.status_back:
            raise ConnectionError("positions is down")
        return {s: u for s, u in self.held.items() if abs(u) > 1e-12}

    def cancel(self, tag):
        self.calls.append(("cancel", tag))
        if self.down:
            raise ConnectionError("cancel is down")
        o = self.book.get(tag)
        if o is not None and o["state"] in ("open", "partly_filled") and self.rng.random() < 0.1:
            self._piece(tag, 1.0)                                 # a fill races the cancel
        if o is None:
            return {"status": "no such order"}
        if o["state"] in ("open", "partly_filled"):
            o["state"] = "cancelled"
            self.done_at.setdefault(tag, self.clock.t)
            return {"status": "cancel requested"}
        return {"status": "already finished"}

    def market(self, notices=True):
        """Time passes between runs."""
        for tag, o in self.book.items():
            if o["state"] in ("open", "partly_filled"):
                if self.rng.random() < 0.5:
                    self._piece(tag, self.rng.choice([1.0, abs(o["units"])]))
                continue
            fresh = o["filled"] > 0 and self.clock.t - self.done_at.get(tag, -1e18) < 2 * 86400
            if not (notices and fresh):
                continue
            r, sign = self.rng.random(), 1.0 if o["units"] > 0 else -1.0
            if r < 0.04 and tag not in self.restated:             # A1, a cent better, once an order
                self.restated.add(tag)
                self.avg[tag] = round(self.avg[tag] - sign * 0.01, 2) if self.rounds[tag] else self.avg[tag] - sign * 0.01
                self.counts["A1"] += 1
            elif r < 0.08 and o["state"] == "cancelled" and o["filled"] < abs(o["units"]):
                self._piece(tag, 1.0, late=True)                  # A2
                self.counts["A2"] += 1
            elif r < 0.12 and self.fee.get(tag, 0.0) >= 0.02:
                self.fee[tag] = round(self.fee[tag] / 2, 2)       # A3
                self.counts["A3"] += 1


def _ordinary_world(tmp, rng):
    clock = Clock(ny("2026-11-02 10:00"))
    w = ticket_world(tmp, _Ordinary(rng, {"OWN": 10.0}, 1_000_000.0, clock))
    w.clock = clock
    return w


def _ordinary_targets(rng, book):
    wanted = {s: float(max(0, round(book.get(s, 0.0)) + rng.choice([-2, -1, 0, 0, 1, 2, 3]))) for s in ("SPY", "QQQ", "IWM")}
    return {"a": wanted}


def _ordinary_day(w, rng, outages=True):
    """One session: two to four practice runs between 10:00 and 15:00, the market moving between them; returns the
    summaries."""
    outs = []
    for sym in ("SPY", "QQQ", "IWM", "TLT"):
        w.prices[sym] = rng.choice([20.0, 55.5, 101.25, 230.0, 0.85])
    n = rng.randint(2, 4)
    for run in range(n):
        if run:
            w.later(rng.choice([20, 45, 70]))
        roll = rng.random()
        w.venue.down = outages and roll < 0.07 and w.baseline() is not None   # the first run records the baseline
        w.venue.status_back = outages and 0.07 <= roll < 0.10 and w.baseline() is not None   # M2: the second read answers
        w.venue.read_once = set()
        w.paper = paper(_ordinary_targets(rng, w.book()))
        outs.append(w.run())
        if w.venue.status_back:
            outs[-1] = {**outs[-1], "status back": True}
        w.venue.down = w.venue.status_back = False
        w.venue.market(notices=run < n - 1)          # a correction is read the same day (the ledger rechecks 3 days)
    w.next_day()
    return outs


@pytest.mark.parametrize("seed_block", range(10))
def test_t34b3_ordinary_days_write_no_fault_and_stop_no_run_at_reconcile(tmp_path, seed_block):
    """At least 1,000 ordinary runs per block, 10,000 in all: partial and late pieces, averages rounded to the cent,
    restatements a cent better, late fills on cancelled orders, fee totals corrected down, fills racing cancels, and
    whole-venue outages."""
    stats, runs = _Counter(), 0
    k = 0
    while runs < 1000:
        seed = 3_500_000 + seed_block * 1000 + k
        rng = random.Random(seed)
        w = _ordinary_world(tmp_path / f"o{k}", rng)
        for day in range(rng.randint(3, 6)):
            for i, out in enumerate(_ordinary_day(w, rng)):
                where = (seed, day, i)
                assert "faults" not in out, (where, out.get("faults"), out.get("reason"))
                assert out["stage"] not in _sl.FAULT_STAGES, (where, out["stage"], out["reason"][-300:],
                                                              w.ledger().last_reconcile()["summary"].get("anomalies"),
                                                              w.ledger().last_reconcile()["summary"].get("position_differences"))
                runs += 1
                stats[out["stage"] or "done"] += 1
                if out.get("status back"):
                    stats[f"status back, stage {out['stage'] or 'done'}"] += 1
                stats["notice kinds"] += len(out.get("notices", []))
                for kind in out.get("notices", []):
                    stats[kind] += 1
                stats["sent"] += out["orders_sent"]
        stats.update(w.venue.counts)
        stats["judged_on stored lines"] += sum(1 for e in w.entries() if e.get("judged_on") == "stored")
        _shutil.rmtree(tmp_path / f"o{k}")
        k += 1
    print(f"T34B3-ORDINARY block {seed_block}: runs {runs}, {dict(sorted(stats.items()))}")
    assert stats["venue_read"] > 0 and stats["avg_restated"] > 0 and stats["late_amount"] > 0 and stats["fee_fell"] > 0
    assert stats["status back, stage venue_read"] > 0 and stats["judged_on stored lines"] == 0, stats


T34B3_FAULTS = ("through a cent", "through a tenth of a cent", "overfill", "restated beyond a cent",
                "restated through the limit", "fee below zero", "late amount in another state", "units fell",
                "piece unexplained", "killed after a breach", "killed before a notice line", "outage with a stray position",
                "outage with a ledger failure", "killed after the summary write failed",
                "killed before a notice line, then restated back", "killed before a fill line, then corrected")


def _t34b3_fault(w, rng, how, monkeypatch):
    """Meet `how` in one run, after the ordinary days; returns (the run that met it, None), or (None, the next run)
    when the run that met it was killed or could not write its summary."""
    v = w.venue
    held = {s: float(round(u)) for s, u in w.book().items() if s != "OWN"}
    more = {"a": {**held, "TLT": held.get("TLT", 0.0) + 2.0}}     # a TLT order of 2 in the next run (the shaped one,
    # when it is the run's first order: v.shaped names the order the shape went to)

    def prep(shape):
        v.next_take = shape
        w.paper = paper(more)
        out = w.run()
        assert out["orders_sent"] >= 1 and out["stage"] is None, (how, out["stage"], out["reason"])
        w.later(20)
        assert v.book[v.shaped]["symbol"] == "TLT" and v.book[v.shaped]["units"] == 2.0, (how, v.book[v.shaped])
        return v.shaped, v.book[v.shaped]

    def killed(name):
        _kill_at(monkeypatch, name)
        with pytest.raises(KeyboardInterrupt):
            w.run()
        monkeypatch.undo()
        w.later(20)
        return None, w.run()
    if how in ("restated beyond a cent", "restated through the limit", "killed before a notice line", "fee below zero",
               "units fell"):
        tag, o = prep({"add": 0.0})                               # filled at once at its limit
        if how == "restated beyond a cent":
            v.avg[tag] = o["limit"] - 0.05
        elif how == "fee below zero":
            v.fee[tag] = -0.5
        elif how == "units fell":
            o["filled"] -= 1.0
            v.held["TLT"] -= 1.0
        else:
            v.avg[tag] = o["limit"] + 0.01                        # a cent through, a cent from the stored figure
            if how == "killed before a notice line":
                return killed("_journal_notices")
        return w.run(), None
    if how == "killed before a notice line, then restated back":  # addendum 4: the outside reviewer's case
        tag, o = prep({"add": 0.0})
        v.avg[tag] = o["limit"] + 0.01                            # restated a cent through and followed ...
        _kill_at(monkeypatch, "_journal_notices")                 # ... the run dies before the notice line ...
        with pytest.raises(KeyboardInterrupt):
            w.run()
        monkeypatch.undo()
        v.avg[tag] = o["limit"]                                   # ... and the broker restates it back first
        w.later(20)
        return None, w.run()
    if how == "killed before a fill line, then corrected":       # addendum 4: a fill through, its line written late
        v.next_take = {"add": 0.01, "over": False}
        w.paper = paper(more)
        real, died = osx._Run._fill_verdict, []

        def dying(self, f, settling=False):                       # the run dies before the shaped fill's line ...
            if not died and not settling and f.get("tag") == v.__dict__.get("shaped"):
                died.append(1)
                raise KeyboardInterrupt("killed here by the test")
            return real(self, f, settling)
        monkeypatch.setattr(osx._Run, "_fill_verdict", dying)
        with pytest.raises(KeyboardInterrupt):
            w.run()
        monkeypatch.undo()
        assert died and v.book[v.shaped]["symbol"] == "TLT", (how, v.book[v.shaped])
        v.avg[v.shaped] = v.book[v.shaped]["limit"]               # ... and a restatement corrects it before the next
        w.later(20)
        return None, w.run()
    if how in ("late amount in another state", "piece unexplained"):
        tag, o = prep({"add": 0.0, "partial": 1.0})               # 1 of 2 filled at its limit
        if how == "late amount in another state":
            o["state"] = "cancelled"                              # cancelled with 1 filled, read so by a run
            w.paper = paper({"a": {s: float(round(u)) for s, u in w.book().items() if s != "OWN"}})
            mid = w.run()
            assert mid["stage"] is None and "faults" not in mid, (how, mid["stage"], mid["reason"])
            w.later(20)
            o.update(state="filled", filled=2.0)                  # ... then filled, all of it
            v.held["TLT"] += 1.0
        else:
            o.update(state="filled", filled=2.0)
            v.held["TLT"] += 1.0
            v.avg[tag] = o["limit"] * 0.3                         # far below: a negative piece rounding cannot explain
        return w.run(), None
    if how in ("through a cent", "through a tenth of a cent", "overfill", "killed after a breach",
               "killed after the summary write failed"):
        v.next_take = {"add": 0.001 if how == "through a tenth of a cent" else 0.0 if how == "overfill" else 0.01,
                       "over": how == "overfill"}
        w.paper = paper(more)
        if how == "killed after a breach":
            return killed("_judge_final")
        if how == "killed after the summary write failed":
            real, failed = Journal.write, []

            def write(self, day, entry):
                if entry.get("event") == "summary" and not failed:
                    failed.append(1)
                    raise OSError("no space left on device")
                return real(self, day, entry)
            monkeypatch.setattr(Journal, "write", write)
            w.run()
            monkeypatch.undo()
            w.later(20)
            return None, w.run()
        return w.run(), None
    v.down = True                                                 # the outages with something else wrong
    w.paper = paper(more)
    if how == "outage with a stray position":
        v.held["XLE"] = 1.0

        def positions():
            v.calls.append(("positions", None))
            return {s: u for s, u in v.held.items() if abs(u) > 1e-12}
        v.positions = positions
        out = w.run()
        del v.positions
        return out, None
    real_rec = _Ledger.reconcile

    def reconcile(self, *a, **k):
        real_rec(self, *a, **k)
        raise _orders.OrderError("the order ledger could not be written: disk I/O error")
    monkeypatch.setattr(_Ledger, "reconcile", reconcile)
    out = w.run()
    monkeypatch.undo()
    return out, None


@pytest.mark.parametrize("how", T34B3_FAULTS)
def test_t34b3_every_fault_is_marked_by_the_run_that_meets_it_or_carried_by_the_next(tmp_path, monkeypatch, how):
    """Eleven sequences per kind, 176 in all, each after one or two ordinary days (no outages)."""
    marked = carried = stored = 0
    for k in range(11):
        seed = 3_600_000 + T34B3_FAULTS.index(how) * 100 + k
        rng = random.Random(seed)
        w = _ordinary_world(tmp_path / f"f{k}", rng)
        for _ in range(rng.randint(1, 2)):
            for out in _ordinary_day(w, rng, outages=False):
                assert "faults" not in out and out["stage"] not in _sl.FAULT_STAGES, (seed, out)
        w.venue.down = False
        for t, o in w.venue.book.items():                         # the ordinary orders are settled first
            if o["state"] in ("open", "partly_filled"):
                o["state"] = "cancelled"
        w.paper = paper({"a": {s: float(round(u)) for s, u in w.book().items() if s != "OWN"}})
        settle = w.run()
        assert "faults" not in settle and settle["stage"] not in _sl.FAULT_STAGES, (seed, settle)
        w.later(20)
        met, nxt = _t34b3_fault(w, rng, how, monkeypatch)
        if met is not None:
            assert met.get("faults") or met["stage"] in _sl.FAULT_STAGES, (seed, how, met.get("stage"), met.get("reason"))
            marked += 1
        else:
            assert "cap" in nxt.get("faults", []) and nxt.get("cap_refs"), (seed, how, nxt.get("faults"), nxt.get("reason"))
            carried += 1
        stored += sum(1 for e in w.entries() if e.get("judged_on") == "stored")
        _shutil.rmtree(tmp_path / f"f{k}")
    print(f"T34B3-FAULT {how}: marked {marked}, carried {carried}, judged_on stored lines {stored}")
    assert marked + carried == 11


# ---- T34b-3 addenda 3 and 4: every write that sets an average is judged once, by its own line, on its own figure

_JTAG = "s1-IWM-2026-11-16"


class _Scripted:
    """A venue that reports, for each tag, exactly what the test last set; positions empty; cancels taken."""
    name = "scripted"

    def __init__(self):
        self.book = {}

    def submit(self, symbol, units, tag, price=None, limit_price=None):
        self.book[tag] = {"state": "open", "filled_units": 0.0, "avg_price": None, "fee": None,
                          "venue_order_id": f"V-{tag}", "raw_status": None}
        return {"status": "accepted", "venue_order_id": f"V-{tag}"}

    def order_status(self, tag):
        got = self.book.get(tag)
        return dict(got) if got is not None else None

    def positions(self):
        return {}

    def cancel(self, tag):
        return {"status": "cancelled"}


class _Dying:
    """A journal whose `write` dies (KeyboardInterrupt) once `after` more lines are written; None: never."""

    def __init__(self, journal, after):
        self._journal, self._left = journal, after

    def write(self, day, entry):
        if self._left is not None:
            if self._left == 0:
                raise KeyboardInterrupt("killed here by the test")
            self._left -= 1
        return self._journal.write(day, entry)

    def __getattr__(self, name):
        return getattr(self._journal, name)


class _Lines:
    """One order's ledger and the order step's own journaling of it, run by run (`journal_run`): the scan, the fill
    lines, the notice lines, the settling lines and a summary with the run's cap_refs; nothing else of a run."""

    def __init__(self, tmp, units=10.0, limit=90.0):
        self.t = [1_000_000.0]
        self.led = OrderLedger(":memory:", clock=lambda: self.t[0])
        self.venue = _Scripted()
        assert self.led.reconcile(self.venue, {})["clean"]
        self.led.intend(_JTAG, "IWM", units, limit, limit_price=limit)
        self.led.submit_once(self.venue, _JTAG)
        self.journal = Journal(tmp / "journal")

    def report(self, **fields):
        self.venue.book[_JTAG].update(fields)
        self.t[0] += 60
        return self.led.reconcile(self.venue, {})

    def journal_run(self, die_after=None):
        run = osx._Run(self.led, self.venue, None, None, _Dying(self.journal, die_after), None, None,
                       lambda: self.t[0], None, None, None, None, False)
        run.name, run.day, run._locked, run.session_day = self.venue.name, "2026-11-16", True, True
        try:
            run._journal_fills()
            run._journal_notices()
            run._settle_unjudged()
            run._write({"event": "summary", "cap_refs": run._breach_refs()})
        except KeyboardInterrupt:
            return None
        return run

    def lines(self, event=None):
        return [e for e in self.journal.read() if e.get("type") == "order_step"
                and (event is None or e.get("event") == event)]

    def caps(self):
        return [e["cap_refs"] for e in self.lines("summary") if e["cap_refs"]]


def _t34b3_map(lines):
    """ref -> what its own line says ((verdict, judged_avg, judged_on); None for a line with no limit verdict), and
    ref -> what settled it ((verdict, covered_by)); each ref once."""
    said, settled = {}, {}
    for e in lines:
        event = e.get("event")
        if event in ("fill", "notice"):
            ref = f"{event}-{e['fill_id' if event == 'fill' else 'notice_id']}"
            assert ref not in said, ref
            said[ref] = (e["verdict"], e["judged_avg"], e.get("judged_on")) if "verdict" in e else None
        elif event == "verdict":
            assert e["ref"] not in settled, e
            settled[e["ref"]] = (e["verdict"], e.get("covered_by"))
    return said, settled


def _refs(L):
    fills = [f"fill-{f['id']}" for f in L.led.fills(_JTAG)]
    notes = [f"notice-{n['id']}" for n in L.led.reconcile_notices()]
    return fills, notes


@pytest.mark.parametrize("death", [False, True])
def test_t34b3_the_four_step_case_gives_two_cap_refs_fill_1_and_fill_2_with_or_without_the_death(tmp_path, death):
    L = _Lines(tmp_path)
    L.report(state="partly_filled", filled_units=1.0, avg_price=90.01, fee=0.0)
    L.journal_run()
    L.report(avg_price=90.0)
    L.journal_run()
    L.report(filled_units=2.0, avg_price=90.01)                     # piece price 90.02, the average back through
    L.journal_run(die_after=0 if death else None)                  # dies before fill-2's line
    L.report(avg_price=90.0)                                       # ... and the broker corrects it before the next run
    L.journal_run()
    (f1, f2), (n1, n2) = _refs(L)
    said, settled = _t34b3_map(L.lines())
    assert said == {f1: ("breach", pytest.approx(90.01), None), f2: ("breach", pytest.approx(90.01), None),
                    n1: ("ok", 90.0, None), n2: ("ok", 90.0, None)}, said
    assert settled == {} and L.caps() == [[f1], [f2]], L.caps()


def test_t34b3_a_restatement_through_the_limit_a_death_and_a_restatement_back_leave_the_first_notice_a_breach(
        tmp_path, monkeypatch):
    # the outside reviewer's case: buy limit 202, average 202; restated to 202.01 and followed; the run dies before
    # the notice line; restated back to 202 before the next run
    w, tag = _restating(tmp_path / "w")
    w.venue.avg[tag] = 202.01
    w.later(30)
    _kill_at(monkeypatch, "_journal_notices")
    with pytest.raises(KeyboardInterrupt):
        _prun(w)
    monkeypatch.undo()
    w.venue.avg[tag] = 202.0
    w.later(30)
    out = _prun(w)
    n1, n2 = w.entries("notice")
    assert (n1["new"], n1["verdict"], n1["judged_avg"]) == (202.01, "breach", 202.01), n1
    assert (n2["new"], n2["verdict"], n2["judged_avg"]) == (202.0, "ok", 202.0), n2
    assert out["faults"] == ["cap"] and out["cap_refs"] == [f"notice-{n1['notice_id']}"], out


@pytest.mark.parametrize("then", ["a correcting restatement", "a diluting later fill"])
def test_t34b3_a_fill_through_its_limit_journaled_late_is_still_the_breach(tmp_path, then):
    L = _Lines(tmp_path)
    L.report(state="partly_filled", filled_units=1.0, avg_price=90.01, fee=0.0)
    if then == "a correcting restatement":
        L.report(avg_price=90.0)
    else:
        L.report(filled_units=2.0, avg_price=90.0)                 # piece 89.99: the average back at the limit
    L.journal_run()                                                # the first line written only now
    fills, notes = _refs(L)
    said, _ = _t34b3_map(L.lines())
    assert said[fills[0]] == ("breach", pytest.approx(90.01), None), said
    later = notes[0] if then == "a correcting restatement" else fills[1]
    assert said[later] == ("ok", pytest.approx(90.0), None) and L.caps() == [[fills[0]]], (said, L.caps())


def test_t34b3_an_unpriced_pieces_fill_line_judges_the_notices_figure_and_the_notice_line_has_no_verdict(tmp_path):
    L = _Lines(tmp_path, units=10.0001, limit=100.0)
    L.report(state="partly_filled", filled_units=10.0, avg_price=100.01, fee=0.0)
    L.journal_run()
    L.report(state="filled", filled_units=10.0001, avg_price=100.0)
    L.journal_run()
    (f1, f2), (n1,) = _refs(L)
    said, settled = _t34b3_map(L.lines())
    (notice,) = L.lines("notice")
    assert notice["kind"] == "piece_unpriced" and "verdict" not in notice and "judged_avg" not in notice, notice
    assert said == {f1: ("breach", 100.01, None), f2: ("ok", 100.0, None), n1: None} and settled == {}, said


@pytest.mark.parametrize("first, new, verdict", [(100.0, 99.99, "ok"), (100.03, 100.02, "breach")])
def test_t34b3_a_piece_unpriced_notice_with_no_row_of_its_own_is_judged_on_its_own_new(tmp_path, first, new, verdict):
    """Units with no average, then an average that prices them through a notice with no row (the worked-out piece
    is negative and a cent of rounding explains it): the notice's own line judges its own `new`."""
    L = _Lines(tmp_path, units=10.0001, limit=100.0)
    L.report(state="partly_filled", filled_units=10.0, avg_price=first, fee=0.0)
    L.journal_run()
    L.report(state="filled", filled_units=10.0001, avg_price=None)  # units with no average: a row that waits
    L.journal_run()
    L.report(avg_price=new)                                        # no new units, so no row: only the notice
    L.journal_run()
    (f1, f2), (n1,) = _refs(L)
    (n,) = L.led.reconcile_notices()
    assert (n["kind"], n["new"], n["row"]) == ("piece_unpriced", new, None), n
    said, settled = _t34b3_map(L.lines())
    assert said[n1] == (verdict, new, None) and said[f2][:2] == ("unjudged", None), said
    assert settled == {f2: ("ok", n1)}, settled
    first_cap = [[f1]] if first > 100.0 else []
    assert L.caps() == first_cap + ([[n1]] if verdict == "breach" else []), L.caps()


@pytest.mark.parametrize("late", [False, True])
def test_t34b3_a_row_with_no_average_is_settled_by_the_write_that_prices_it_and_one_ref_carries_the_breach(tmp_path,
                                                                                                            late):
    L = _Lines(tmp_path)
    L.report(state="partly_filled", filled_units=4.0, avg_price=None, fee=0.0)
    if not late:
        L.journal_run()
        (only,) = L.lines("fill")
        assert (only["verdict"], only["judged_avg"]) == ("unjudged", None) and not L.lines("verdict")
    L.report(state="filled", filled_units=10.0, avg_price=90.02)    # prices all ten: through the limit
    L.journal_run()
    (f1, f2), _ = _refs(L)
    said, settled = _t34b3_map(L.lines())
    assert said == {f1: ("unjudged", None, None), f2: ("breach", pytest.approx(90.02), None)}, said
    assert settled == {f1: ("ok", f2)} and L.caps() == [[f2]], (settled, L.caps())
    L.journal_run()
    assert len(L.lines("verdict")) == 1


def test_t34b3_a_replay_that_cannot_be_trusted_falls_back_to_the_stored_average_and_says_so(tmp_path, monkeypatch):
    L = _Lines(tmp_path)
    L.report(state="partly_filled", filled_units=1.0, avg_price=90.01, fee=0.0)
    real = OrderLedger.average_writes
    monkeypatch.setattr(OrderLedger, "average_writes",
                        lambda self, tag: {**real(self, tag), "trusted": False, "why": "made untrusted by the test"})
    L.journal_run()
    (line,) = L.lines("fill")
    assert (line["verdict"], line["judged_avg"], line["judged_on"]) == ("breach", 90.01, "stored"), line


def _t34b3_history(rng, restate_unpriced=False):
    """One order's history: (units, limit, the venue's reports in turn). Fills in unequal pieces (tiny ones too, so
    a rounding broker gives unpriced pieces), through the limit or not; a broker that rounds, truncates or does not
    round its average; reports with no average; restatements within and beyond a cent; a cancel with part filled
    and late amounts after it; fee totals that rise and fall. With `restate_unpriced`, a restatement may also follow
    a report with no average (from the last average given), so a later average can price units through a notice
    with no row of its own (addendum 5); without it the histories are those the property always drew."""
    side, size, limit = rng.choice((1.0, -1.0)), rng.choice((10.0, 6.0, 10.0002)), 90.0
    how = rng.choice(("round", "truncate", "exact"))
    filled = cash = fee = 0.0
    state, shown, last, steps = "open", None, None, []
    for _ in range(rng.randint(2, 10)):
        roll, f = rng.random(), {}
        if roll < 0.5 and filled < size - 1e-9:
            piece = min(size - filled, rng.choice((0.0001, 0.5, 1.0, 2.5, 4.0)))
            filled += piece
            cash += piece * (limit + side * rng.choice((-0.02, -0.01, 0.0, 0.003, 0.01, 0.02)))
            exact = cash / filled
            shown = None if rng.random() < 0.2 else {"round": round(exact, 2), "truncate": math.floor(exact * 100) / 100,
                                                     "exact": exact}[how]
            if state != "cancelled":
                state = "filled" if filled >= size - 1e-9 else "partly_filled"
            f = {"state": state, "filled_units": filled, "avg_price": shown}
            last = shown if shown is not None else last
        elif roll < 0.75 and (last if restate_unpriced else shown) is not None:
            shown = round((last if restate_unpriced else shown) + rng.choice((-0.01, -0.005, 0.005, 0.01, 0.02, -0.03)), 4)
            last = shown
            f = {"avg_price": shown}
        elif roll < 0.82 and state == "partly_filled":
            state = "cancelled"
            f = {"state": state}
        else:
            fee = max(0.0, round(fee + rng.choice((-0.1, 0.05, 0.2)), 2))
            f = {"fee": fee}
        steps.append(f)
    return side * size, limit, steps


def _t34b3_through(units, limit, avg):
    return ((avg - limit) if units > 0 else (limit - avg)) > osx.LIMIT_SAME * limit * osx.LIMIT_EDGE


def test_t34b3_the_verdicts_are_the_same_journaled_on_time_late_or_across_a_death(tmp_path):
    rng = random.Random(3404)
    tally = {"histories": 0, "refs": 0, "breach refs": 0, "settled": 0, "stored lines": 0, "deaths": 0,
             "notices": {}, "unjudged at the end": 0, "setting writes": 0, "rowless piece_unpriced": 0}
    for k in range(600):                                           # the last 80: restatements after an unpriced report
        units, limit, steps = _t34b3_history(rng, restate_unpriced=k >= 520)
        die_at, die_after = rng.randrange(len(steps)), rng.randrange(3)
        maps = {}
        for arm in ("on time", "late", "death"):
            L = _Lines(tmp_path / f"{k}-{arm}", units=units, limit=limit)
            for i, step in enumerate(steps):
                L.report(**step)
                if arm == "on time" or (arm == "death" and i != die_at):
                    L.journal_run()
                elif arm == "death":
                    tally["deaths"] += L.journal_run(die_after=die_after) is None
            L.journal_run()                                        # the next run finishes the job
            L.journal_run()
            maps[arm] = _t34b3_map(L.lines())
            records = ([{k2: v for k2, v in f.items() if k2 not in ("applied", "applied_time")}
                        for f in L.led.fills(_JTAG)],
                       [{k2: n[k2] for k2 in ("id", "kind", "old", "new", "after_fill", "row")}
                        for n in L.led.reconcile_notices()])
            maps[arm] += (records,)
            if arm == "on time":
                held = L
        assert maps["on time"] == maps["late"] == maps["death"], (k, steps, die_at, die_after, maps)
        said, settled, _ = maps["on time"]
        replay = held.led.average_writes(_JTAG)                    # one figure, one ref: each write that sets one
        assert replay["trusted"], (k, replay)
        for f in held.led.fills(_JTAG):
            ref, wr = f"fill-{f['id']}", replay["fills"][f["id"]]
            if wr["sets"]:
                assert said[ref][1] == pytest.approx(wr["avg"], rel=0, abs=0), (k, ref, said[ref], wr)
                assert (said[ref][0] == "breach") == _t34b3_through(units, limit, wr["avg"]), (k, ref)
            elif wr["units"] > 1e-9:
                assert said[ref][:2] == ("unjudged", None), (k, ref, said[ref])
                if wr["covered_by"] is not None:
                    cover = "-".join(map(str, wr["covered_by"]))
                    assert settled[ref] == ("ok", cover) and said[cover][1] is not None, (k, ref, settled)
                else:
                    assert ref not in settled
                    tally["unjudged at the end"] += 1
            else:
                assert said[ref][1] is None, (k, ref, said[ref])
        for n in held.led.reconcile_notices():
            ref = f"notice-{n['id']}"
            tally["notices"][n["kind"]] = tally["notices"].get(n["kind"], 0) + 1
            if n["kind"] == "avg_restated":
                assert said[ref][1] == n["new"] and (said[ref][0] == "breach") == _t34b3_through(units, limit, n["new"])
            elif n["kind"] == "piece_unpriced" and n["row"] is not None:
                assert said[ref] is None and replay["fills"][n["row"]]["sets"], (k, ref)
            elif n["kind"] == "piece_unpriced":                  # no row of its own: its own line judges its own new
                tally["rowless piece_unpriced"] += 1
                assert said[ref][1] == n["new"] and (said[ref][0] == "breach") == _t34b3_through(units, limit, n["new"])
            elif n["kind"] in ("late_amount", "fee_fell"):
                assert said[ref] is None, (k, ref)
        # addendum 5: every write that sets an average has exactly one line carrying judged_avg (rowless notices too)
        setting = ({f"fill-{i}" for i, wr in replay["fills"].items() if wr["sets"]}
                   | {f"notice-{n['id']}" for n in held.led.reconcile_notices()
                      if n["kind"] == "avg_restated" or (n["kind"] == "piece_unpriced" and n["row"] is None)})
        carrying = [ref for ref, v in said.items() if v is not None and v[1] is not None]
        assert sorted(carrying) == sorted(setting), (k, carrying, setting)
        tally["setting writes"] += len(setting)
        tally["histories"] += 1
        tally["refs"] += len(said)
        tally["breach refs"] += sum(1 for v in said.values() if v and v[0] == "breach")
        tally["settled"] += len(settled)
        tally["stored lines"] += sum(1 for v in said.values() if v and v[2] == "stored")
    print("T34B3-PROPERTY", tally)
    assert tally["stored lines"] == 0 and tally["deaths"] > 100 and tally["breach refs"] > 200, tally
    assert {"avg_restated", "piece_unpriced", "late_amount", "fee_fell"} <= set(tally["notices"]), tally
    assert tally["settled"] > 50 and tally["rowless piece_unpriced"] > 0, tally


# -- M2: the first reconcile's unread status needs only to be of that kind, for an order the ledger knows

@pytest.mark.parametrize("second, stage", [("a full fill at the limit", "venue_read"),
                                           ("a full fill a cent through", "venue_read"),
                                           ("no such order", "reconcile"), ("another state", "reconcile"),
                                           ("more than the size", "reconcile")])
def test_t34b3_a_first_read_status_unread_answered_by_the_second_read_is_judged_by_the_second(tmp_path, second, stage):
    w = _resting(tmp_path / "w")
    (tag,) = w.venue.submits()
    limit = w.orders()[0]["limit_price"]
    w.venue.raise_positions, w.venue.raise_on, n = True, {"cancel"}, [0]

    def status(t):
        n[0] += 1
        if n[0] == 1:
            raise ConnectionError("order_status is down")
        answer = {"state": "filled", "filled_units": 3.0, "avg_price": limit, "fee": 0.0, "venue_order_id": None,
                  "raw_status": None}
        if second == "no such order":
            return None
        answer.update({"a full fill a cent through": {"avg_price": round(limit + 0.01, 2)},
                       "another state": {"state": "mystery"},
                       "more than the size": {"filled_units": 4.0}}.get(second, {}))
        return answer
    w.venue.order_status = status
    out = _prun(w, {"a": {"IWM": 3.0}})
    assert out["status"] == "stopped" and out["stage"] == stage and out["orders_sent"] == 0, (second, out)
    if stage == "venue_read":
        (line,) = w.entries("fill")
        through = second == "a full fill a cent through"
        assert line["verdict"] == ("breach" if through else "ok") and line["units"] == 3.0, line
        assert out.get("faults") == (["cap"] if through else None), out
        assert out.get("cap_refs") == ([f"fill-{line['fill_id']}"] if through else None), out


# -- N1: each local failure, with the venue's positions down, stays a reconcile stop

@pytest.mark.parametrize("how", ["journal write", "state file", "fill not applied"])
def test_t34b3_an_outage_beside_a_local_failure_stays_a_reconcile_stop(tmp_path, monkeypatch, how):
    w = _resting(tmp_path / "w")
    (tag,) = w.venue.submits()
    w.venue.raise_positions = True
    w.venue.answers[tag] = [{"state": "filled", "filled_units": 3.0}]
    if how == "journal write":
        real = w.journal.write

        def write(day, entry):
            if entry.get("event") == "fill":
                raise OSError("disk full")
            return real(day, entry)
        monkeypatch.setattr(w.journal, "write", write)
    elif how == "state file":
        def settle(self, ledger):
            raise osx.OrderStepError("the state file could not be written")
        monkeypatch.setattr(osx.OrderStepState, "settle_sends", settle)
    else:
        monkeypatch.setattr(_orders.OrderLedger, "mark_applied", lambda self, ids: 0)
    out = _prun(w, {"a": {"IWM": 3.0}})
    assert out["status"] == "stopped" and out["stage"] == "reconcile" and out["orders_sent"] == 0, (how, out)


def test_t34b3_a_fill_line_that_fails_beside_an_outage_stops_at_reconcile_on_the_journals_own_failure(tmp_path,
                                                                                                       monkeypatch):
    """The day's journal file already holds a line, so nothing but the fill line's own failure can stop the run:
    with that failure swallowed the fill would count as applied with no line and the run would go on to venue_read."""
    w = _resting(tmp_path / "w")
    (tag,) = w.venue.submits()
    w.venue.raise_positions = True
    w.venue.answers[tag] = [{"state": "filled", "filled_units": 3.0}]
    real = w.journal.write

    def write(day, entry):
        if entry.get("event") == "fill":
            real(day, {"type": "other", "event": "an earlier line of the day"})
            raise OSError("disk full")
        return real(day, entry)
    monkeypatch.setattr(w.journal, "write", write)
    out = _prun(w, {"a": {"IWM": 3.0}})
    assert out["status"] == "stopped" and out["stage"] == "reconcile" and out["orders_sent"] == 0, out
    assert "disk full" in out["reason"] and w.entries("fill") == [], out


# -- N2: a notice is never listed among what stands in the way

def test_t34b3_an_open_notice_is_not_listed_as_standing_in_the_way(tmp_path):
    w, tag = _restating(tmp_path / "w")
    w.venue.avg[tag] = 201.995
    w.later(30)
    assert _prun(w)["notices"] == ["avg_restated"]
    _t34_flag(w, ["late_outcome"], prefix="old")
    w.later(30)
    out = _prun(w)
    assert out["stage"] == "reconcile" and "1 attention item open (" in out["reason"], out
    assert "attention items open" not in out["reason"] and tag not in out["reason"].split("attention item open")[1], out
