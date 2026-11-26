"""CF-Q2: the quote an order is priced from is at most PRIMARY_QUOTE_MAX_AGE_SECONDS old, and every journal `order`
line carries that quote's own time (`quote_ts`, ISO UTC) and its age when the order was priced (`quote_age_s`).

  - a quote 299 s old prices its order; one 301 s old is asked for again first, as the 20-minute rule did, and an
    answer that is itself older than the limit leaves the order over;
  - the delayed cross-check keeps its own limit: a second quote whose trade is 19 minutes old still agrees;
  - the two fields are on every kind of order line: a plain LIMIT order, a whole LIMIT leg and a MARKET remainder."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from valedia_trader.data_and_fees import second_quote
from valedia_trader.execution_and_risk import order_step as osx
from test_order_step import World, paper
from test_fractional_collar import collar_world, guard  # noqa: F401  (guard: no socket, no real second source)

NY = ZoneInfo("America/New_York")


def test_the_primary_limit_is_five_minutes_and_apart_from_the_other_ages():
    assert osx.PRIMARY_QUOTE_MAX_AGE_SECONDS == 300
    assert osx.QUOTE_MAX_AGE_SECONDS == 20 * 60                       # a holding valued, an owner's mark
    assert osx.MARKET_QUOTE_MAX_AGE_SECONDS == 120                    # a MARKET leg's own collar
    assert second_quote.DEFAULT_MAX_AGE_SECONDS == 1800               # the delayed cross-check


def spy_world(tmp, age):
    w = World(tmp)
    w.paper = paper({"a": {"SPY": 10.0}})
    w.quote_age["SPY"] = age
    return w


def answered_again_at(w, age):
    """Every ask after the run's first answers SPY `age` seconds old."""
    real, n = w.quotes, [0]

    def quotes(symbols):
        n[0] += 1
        if n[0] > 1:
            w.quote_age["SPY"] = age
        return real(symbols)
    w.quotes = quotes


def test_a_quote_299_seconds_old_prices_its_order_without_asking_again(tmp_path):
    w = spy_world(tmp_path / "w", 299.0)
    out = w.run()
    assert out["status"] == "done" and out["orders_sent"] == 1, out
    assert w.quote_calls == [["SPY"]]
    (line,) = w.entries("order")
    assert line["quote_age_s"] == 299.0


def test_a_quote_301_seconds_old_is_asked_for_again_and_the_order_priced_on_the_answer(tmp_path):
    w = spy_world(tmp_path / "w", 301.0)
    answered_again_at(w, 30.0)
    out = w.run()
    assert out["status"] == "done" and out["orders_sent"] == 1, out
    assert w.quote_calls == [["SPY"], ["SPY"]]                        # asked again, as the 20-minute rule did
    (line,) = w.entries("order")
    assert line["quote_age_s"] == 30.0


@pytest.mark.parametrize("again, sent", [(300.0, True), (301.0, False)])
def test_an_answer_asked_again_is_usable_only_within_the_primary_limit(tmp_path, again, sent):
    w = spy_world(tmp_path / "w", 301.0)
    answered_again_at(w, again)
    out = w.run()
    assert out["status"] == "done" and len(w.quote_calls) == 2, out
    if sent:
        assert out["orders_sent"] == 1 and w.entries("order")[0]["quote_age_s"] == 300.0, out
        return
    assert out["orders_sent"] == 0 and w.venue.submits() == [], out
    (left,) = [e for e in out["left_over"] if e["symbol"] == "SPY"]
    assert left["reason"].startswith("the quote is older than 5 minutes and the quote asked for again is not usable: "
                                     "the quote is 5.0 minutes old, more than 5"), left


def test_a_holding_quote_older_than_the_primary_limit_still_values_it_and_is_not_asked_for(tmp_path):
    w = World(tmp_path / "w")
    w.venue.fill_at_once = 1.0
    w.paper = paper({"a": {"SPY": 10.0, "QQQ": 5.0}})
    assert w.run()["orders_sent"] == 2                                # day one: both bought
    w.next_day()
    w.paper = paper({"a": {"SPY": 12.0, "QQQ": 5.0}})                 # only SPY changes
    w.quote_age.update(SPY=60.0, QQQ=900.0)                           # QQQ: 15 minutes, a holding valued only
    w.quote_calls.clear()
    out = w.run()
    assert out["status"] == "done" and [s["symbol"] for s in out["sent"]] == ["SPY"], out
    assert w.quote_calls == [["QQQ", "SPY"]]                          # QQQ never asked for again


# ---- the delayed cross-check keeps its own limit (second_quote.DEFAULT_MAX_AGE_SECONDS)

def delayed_second(age, calls):
    """The second source as a delayed feed: a quote at the first price whose last trade is `age` seconds old, judged
    by second_quote's own rules at the limit the order step leaves to it (it passes none)."""
    def second(first_price, symbol, *, now, sources, **kw):
        calls.append(sorted(kw))
        t = now.timestamp()
        quote = {"symbol": symbol, "bid": first_price - 0.01, "ask": first_price + 0.01, "last": first_price,
                 "last_trade_time": datetime.fromtimestamp(t - age, NY).strftime("%Y-%m-%dT%H:%M:%S"),
                 "snapshot_at": t - 35, "fetched_at": t - 5, "source": "cboe-delayed"}
        return second_quote.compare(first_price, quote, symbol=symbol, now=now)
    return second


@pytest.mark.parametrize("minutes, sent", [(19, True), (31, False)])
def test_the_delayed_cross_check_accepts_a_19_minute_old_trade(tmp_path, minutes, sent):
    w = collar_world(tmp_path / "w")
    calls = []
    out = w.run(fractional=True, second_opinion=delayed_second(minutes * 60, calls))
    b = out["batch"]
    assert out["status"] == "done" and calls == [["clock"]], (out, calls)     # no age handed in: its own limit
    tags = [a["tag"] for a in w.venue.args]
    assert tags == ([f"{b}-SPY", f"{b}-SPY.r"] if sent else [f"{b}-SPY"]), out


# ---- the two fields on every kind of order line

def check_fields(line, quote_t, age):
    assert list(line)[-2:] == ["quote_ts", "quote_age_s"], line
    when = pd.Timestamp(line["quote_ts"])
    assert when.utcoffset() == pd.Timedelta(0) and when.timestamp() == pytest.approx(quote_t, abs=1e-6), line
    assert line["quote_age_s"] == pytest.approx(age, abs=1e-3), line


def test_a_limit_order_line_carries_its_quotes_time_and_age(tmp_path):
    w = spy_world(tmp_path / "w", 123.5)
    w.run()
    (line,) = w.entries("order")
    check_fields(line, w.clock.t - 123.5, 123.5)
    assert line["quote_ts"] == datetime.fromtimestamp(w.clock.t - 123.5, timezone.utc).isoformat()


def test_the_whole_and_the_market_remainder_lines_carry_their_quotes_time_and_age(tmp_path):
    w = collar_world(tmp_path / "w")
    w.quote_age["SPY"] = 45.0
    out = w.run(fractional=True)
    b = out["batch"]
    lines = {e["tag"]: e for e in w.entries("order")}
    assert set(lines) == {f"{b}-SPY", f"{b}-SPY.r"}, lines
    for line in lines.values():
        check_fields(line, w.clock.t - 45.0, 45.0)
    assert [o["order_type"] for o in sorted(w.orders(), key=lambda o: o["tag"])] == ["LIMIT", "MARKET"]


def test_the_age_is_taken_when_the_order_is_priced_not_when_the_quote_was_read(tmp_path, monkeypatch):
    w = spy_world(tmp_path / "w", 100.0)
    real = osx.risk_gate.check_order

    def slow_gate(*a, **k):                                           # 50 s pass between the read and the pricing
        w.clock.t += 50
        return real(*a, **k)
    monkeypatch.setattr(osx.risk_gate, "check_order", slow_gate)
    start = w.clock.t
    w.run()
    (line,) = w.entries("order")
    check_fields(line, start - 100.0, 150.0)


@pytest.mark.parametrize("gap, sent", [(239.0, True), (241.0, False)])
def test_the_last_look_refuses_an_order_whose_quote_aged_past_the_primary_limit_after_its_intend(tmp_path, gap, sent):
    w = spy_world(tmp_path / "w", 60.0)

    def patch(led):
        real = led.intend

        def intend(*a, **k):
            rec = real(*a, **k)
            w.clock.t += gap                                          # 60 + gap seconds old at the last look
            return rec
        led.intend = intend
    out = w.run(patch=patch)
    assert out["status"] == "done" and len(w.quote_calls) == 1, out
    if sent:
        assert out["orders_sent"] == 1 and w.venue.submits(), out
        return
    assert out["orders_sent"] == 0 and w.venue.submits() == [], out
    assert [o["state"] for o in w.orders()] == ["intended"]           # never sent
    (left,) = [e for e in out["left_over"] if e["symbol"] == "SPY"]
    assert left["reason"] == "the quote went stale before the order was sent", left
