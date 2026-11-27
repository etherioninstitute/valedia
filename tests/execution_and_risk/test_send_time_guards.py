"""CF-G: the order step's checks at the time of sending.

  - the clock: a reading more than CLOCK_BACK_TOLERANCE_SECONDS below the run's highest stops the run (stage
    "clock") and nothing more is sent; a reading within it is used as is;
  - the quote's age once more after the ledger's write lock, just before the venue is called: stale, the order is
    not transmitted;
  - a run that is not shared spends at most the cash less the book's sale proceeds not settled today;
  - a sale's cap is read again at its last look: a sale above it then is refused.
Offline: stand-in venues, fake clocks and quotes, temporary folders."""
import pytest

from valedia_trader.execution_and_risk import order_step as osx
from test_order_step import World, _t51_no_real_second_source, ny, paper  # noqa: F401  (autouse: no real second source)
from test_sale_cap import SYM, bought, sell_all
from test_shared_run import no_network  # noqa: F401  (autouse: no socket)


def spy_world(tmp, age=60.0):
    w = World(tmp)
    w.paper = paper({"a": {"SPY": 10.0}})
    w.quote_age["SPY"] = age
    return w


def after_intend(w, step):
    """A ledger patch: the clock moves by `step` seconds right after the order is intended."""
    def patch(led):
        real = led.intend

        def intend(*a, **k):
            rec = real(*a, **k)
            w.clock.t += step
            return rec
        led.intend = intend
    return patch


# ---- 1. the clock never steps back more than the tolerance

def test_the_tolerance_is_one_second():
    assert osx.CLOCK_BACK_TOLERANCE_SECONDS == 1.0


def bare_run(clock):
    return osx._Run(None, None, None, None, None, None, None, clock, None, None, None, None, False)


def test_a_reading_within_the_tolerance_is_used_as_is_and_one_beyond_it_stops_every_later_reading():
    readings = iter([100.0, 99.5, 101.0, 100.1, 99.9, 500.0])
    run = bare_run(lambda: next(readings))
    assert run._now() == 100.0
    assert run._now() == 99.5                                          # 0.5 s back: used, never clamped to 100
    assert run._now() == 101.0
    assert run._now() == 100.1                                         # 0.9 s below the highest (101)
    with pytest.raises(osx._Stop) as stop:
        run._now()                                                     # 1.1 s below the highest
    assert stop.value.stage == "clock" and "went back 1.100 seconds" in stop.value.reason, stop.value.reason
    with pytest.raises(osx._Stop) as again:
        run._now()                                                     # a later reading, ahead again, stops it too
    assert again.value.stage == "clock"


def test_a_clock_reading_one_and_a_half_seconds_below_the_highest_stops_the_run_and_nothing_is_sent(tmp_path):
    w = spy_world(tmp_path / "w")
    out = w.run(patch=after_intend(w, -1.5))
    assert out["status"] == "stopped" and out["stage"] == "clock", out
    assert "went back 1.500 seconds" in out["reason"], out
    assert out["orders_sent"] == 0 and w.venue.submits() == [], out
    assert [o["state"] for o in w.orders()] == ["intended"]           # never sent
    assert w.entries("stopped")[-1]["stage"] == "clock"


def test_a_clock_reading_half_a_second_below_the_highest_is_used(tmp_path):
    w = spy_world(tmp_path / "w")
    real = w.quotes

    def quotes(symbols):                                               # a second passes after the reconcile, so the
        w.clock.t += 1.0                                               # ledger's own check (not behind its latest
        return real(symbols)                                           # reconcile) is not what is tested here
    w.quotes = quotes
    out = w.run(patch=after_intend(w, -0.5))
    assert out["status"] == "done" and out["orders_sent"] == 1 and len(w.venue.submits()) == 1, out


# ---- 2. the quote's age at transmission, after the ledger's write lock

def waits_for_the_ledger(w, seconds):
    """A ledger patch: `seconds` pass inside submit_once, as when it waits for another's write lock."""
    def patch(led):
        real = led.submit_once

        def submit_once(venue, tag, *a, **k):
            w.clock.t += seconds
            return real(venue, tag, *a, **k)
        led.submit_once = submit_once
    return patch


@pytest.mark.parametrize("wait, sent", [(239.0, True), (241.0, False)])
def test_a_quote_fresh_at_the_last_look_but_past_300_seconds_when_the_guard_runs_is_not_transmitted(tmp_path, wait,
                                                                                                     sent):
    w = spy_world(tmp_path / "w", age=60.0)                           # 60 s at the last look; 60 + wait at the guard
    out = w.run(patch=waits_for_the_ledger(w, wait))
    assert out["status"] == "done", out
    if sent:
        assert out["orders_sent"] == 1 and len(w.venue.submits()) == 1, out
        return
    assert out["orders_sent"] == 0 and w.venue.submits() == [], out   # the venue was never called
    assert [o["state"] for o in w.orders()] == ["refused"]            # the ledger: not sent (NotTransmitted)
    (left,) = [e for e in out["left_over"] if e["symbol"] == "SPY"]
    assert left["reason"] == "the quote went stale while the order waited for the ledger, before it was transmitted"
    assert [e["reason"] for e in w.entries("left_over")] == [left["reason"]]


# ---- 3. a run that is not shared: the book's usable cash bounds its purchases

def friday_sale_world(tmp):
    """Bought 10 SPY on Thursday 2026-10-08, sold them on Friday 2026-10-09 (the proceeds settle on Tuesday 10-13:
    Columbus Day, Monday 10-12, is an exchange session but not a settlement day)."""
    w = World(tmp)
    w.venue.cash_value = 6000.0
    w.venue.fill_at_once = 1.0
    w.clock.t = ny("2026-10-08 10:00")
    w.paper = paper({"a": {"SPY": 10.0}})
    assert w.run()["orders_sent"] == 1
    w.next_day()
    w.paper = paper({"a": {"SPY": 0.0}})
    out = w.run()
    assert [s["units"] for s in out["sent"]] == [-10.0], out
    w.next_day()
    assert w.clock.t == ny("2026-10-12 10:00")
    w.paper = paper({"a": {"QQQ": 10.0}})                             # 10 x 404 at the limit: the venue's cash fits it
    return w


def test_a_not_shared_buy_on_columbus_day_after_a_friday_sale_is_bounded_by_the_books_usable_cash(tmp_path):
    w = friday_sale_world(tmp_path / "w")
    cash = w.venue.cash_value
    assert 4040.0 < cash * (1 - osx.CASH_KEPT)                         # the venue's cash alone would let it go
    out = w.run()
    assert out["status"] == "done" and out["orders_sent"] == 0, out
    (left,) = [e for e in out["left_over"] if e["symbol"] == "QQQ"]
    assert left["reason"].startswith("the purchases would exceed the book's usable cash"), left
    usable = cash - 10.0 * 500.0 * (1 - float(osx.SELL_BELOW_REDUCING))       # less Friday's proceeds, unsettled today
    assert f"({usable * (1 - osx.CASH_KEPT):.2f})" in left["reason"], left
    w.next_day()                                                       # Tuesday: the proceeds have settled
    out = w.run()
    assert out["status"] == "done" and [s["symbol"] for s in out["sent"]] == ["QQQ"], out


# ---- 4. the sale cap again at the last look

def test_a_sale_whose_cap_drops_between_the_differences_and_the_last_look_is_refused(tmp_path):
    w = bought(tmp_path / "w")                                         # 10 SPY bought 2026-11-24, settled 11-25
    real, answers = w.venue.sellable, []

    def sellable(symbol):
        answers.append(symbol)
        return real(symbol) if len(answers) == 1 else 4.0              # the owner sold 6 by hand meanwhile
    w.venue.sellable = sellable
    w.venue.calls.clear()
    out = sell_all(w, "2026-11-25 10:00")
    assert out["status"] == "done" and out["orders_sent"] == 0 and w.venue.submits() == [], out
    assert answers == [SYM, SYM]                                       # cut at the differences, read again at the look
    (refused,) = out["refused"]
    assert refused["symbol"] == SYM and refused["units"] == -10.0 and refused["by"] == "sale cap", refused
    assert refused["reason"].startswith("at the last look the sale of 10 units is above its cap of 4 "), refused
    assert [e["by"] for e in w.entries("refused")] == ["sale cap"]
    assert [o["state"] for o in w.orders() if o["units"] < 0] == ["intended"]   # never sent


def test_a_sale_within_its_cap_at_the_last_look_is_sent(tmp_path):
    w = bought(tmp_path / "w")
    out = sell_all(w, "2026-11-25 10:00")
    assert out["status"] == "done" and [s["units"] for s in out["sent"]] == [-10.0], out
