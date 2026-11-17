"""The locked "copying the leaders" test, on SYNTHETIC worlds made here with a seed (no real data, no network).

Specification: docs/research/2026-11-17-copying-leaders-prediction.md (10ad062, time line 12a4454, amendment of
09:11 UTC in dec8dd7, second amendment of 09:35 UTC in a7b9379: the follower's price is the first point of the
token's public price history at or after the hour, asked for over twelve hours, at a grain of three hours or finer)."""
import json
import os
import random
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import urllib.parse
from pathlib import Path
from collections import Counter
from decimal import Decimal

import numpy as np
import pytest

from valedia_trader.research_loop import copy_leaders as cl
from valedia_trader.venues import polymarket_data as pd

DAY = 86400
HOUR = 3600
MS = cl.month_start


@pytest.fixture(autouse=True)
def no_socket_may_connect(monkeypatch):
    """A second lock beside the fakes: any socket connection in these tests fails at once."""
    def refuse(*a, **k):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


# ------------------------------------------------------------------ small builders
def T(wallet, ts, token="tok", side="BUY", price=0.5, size=4000.0, cond=None, event="ev", oi=0, seq=0):
    row = {"proxy_wallet": wallet, "side": side, "token_id": token, "condition_id": cond or ("c-" + token),
           "size": size, "price": price, "timestamp": ts, "event_slug": event, "outcome_index": oi,
           "transaction_hash": "0x%d" % seq, "slug": token, "title": token, "outcome": "Yes"}
    t, why = cl.trade_from_row(wallet, seq, row)
    assert why is None, why
    return t


def R(cond, win_index=0, resolved_ts=None, payouts=None, status="resolved"):
    if payouts is None and status == "resolved":
        payouts = [1_000_000, 0] if win_index == 0 else [0, 1_000_000]
    return cl.resolution_from_row({"condition_id": cond, "status": status, "payouts": payouts,
                                   "resolved_ts": resolved_ts})


def PT(ts, price, res=3 * HOUR):
    return cl.Point(ts, res, Decimal(repr(price)))


# ------------------------------------------------------------------ money
def test_trade_result_in_whole_cents():
    assert cl.trade_result(40, True, 3) == 57
    assert cl.trade_result(40, False, 3) == -43
    assert cl.COST_CENTS == 3


def test_to_cents_rounds_half_up_on_the_exact_decimal():
    assert cl.price_cents(Decimal("0.425")) == 43
    assert cl.price_cents(Decimal("0.4249")) == 42
    assert cl.price_cents(Decimal("0.05")) == 5


@pytest.mark.parametrize("bad", [True, None, float("nan"), "0.5", float("inf")])
def test_a_wrong_number_in_a_stored_trade_is_a_counted_reject(bad):
    row = {"proxy_wallet": "w", "side": "BUY", "token_id": "t", "condition_id": "c", "size": 10.0, "price": bad,
           "timestamp": 1, "event_slug": "e", "outcome_index": 0, "transaction_hash": "h"}
    t, why = cl.trade_from_row("w", 0, row)
    assert t is None and why == "bad_price"
    row["price"], row["size"] = 0.5, bad
    t, why = cl.trade_from_row("w", 0, row)
    assert t is None and why == "bad_size"


def test_cash_is_exact_micro_dollars():
    t = T("w", MS(2026, 3), price=0.1, size=3.0)
    assert t.cash_micro == 300_000 and t.size_micro == 3_000_000


# ------------------------------------------------------------------ what a NEW position is
def test_first_purchase_with_no_holding_in_the_30_days_before_is_new():
    t0 = MS(2026, 3)
    tr = [T("w", t0, "a", seq=0), T("w", t0 + 600, "a", size=10.0, seq=1),
          T("w", t0 + 5 * DAY, "a", side="SELL", size=4010.0, seq=2),
          T("w", t0 + 5 * DAY + 30 * DAY, "a", seq=3),           # exactly 30 days after the last touch: not new
          T("w", t0 + 5 * DAY + 60 * DAY + 1, "a", seq=4)]       # holding again since seq 3: not new
    assert [p.seq for p in cl.new_positions(tr)] == [0]


def test_a_purchase_30_days_and_one_second_after_the_last_touch_is_new():
    t0 = MS(2026, 3)
    tr = [T("w", t0, "a", seq=0), T("w", t0 + DAY, "a", side="SELL", size=4000.0, seq=1),
          T("w", t0 + DAY + 30 * DAY + 1, "a", seq=2)]
    assert [p.seq for p in cl.new_positions(tr)] == [0, 2]


def test_holding_the_opposite_outcome_blocks_a_new_position():
    t0 = MS(2026, 3)
    tr = [T("w", t0, "yes", cond="c1", oi=0, seq=0), T("w", t0 + 40 * DAY, "no", cond="c1", oi=1, seq=1)]
    assert [p.seq for p in cl.new_positions(tr)] == [0]


def test_a_sell_is_never_a_new_position_and_fills_in_the_same_second_are_one_position():
    t0 = MS(2026, 3)
    tr = [T("w", t0, "a", side="SELL", seq=0), T("w", t0 + 40 * DAY, "b", seq=1), T("w", t0 + 40 * DAY, "b", seq=2)]
    pos = cl.new_positions(tr)
    assert [p.seq for p in pos] == [1]
    assert pos[0].cash24_micro == 2 * 2_000_000_000


def test_the_24_hour_purchase_sum_boundaries():
    t0 = MS(2026, 3)
    tr = [T("w", t0, "a", price=0.5, size=1000.0, seq=0), T("w", t0 + DAY, "a", price=0.5, size=999.98, seq=1),
          T("w", t0 + DAY + 1, "a", price=0.5, size=5000.0, seq=2),
          T("w", t0 + 2 * HOUR, "a", side="SELL", price=0.9, size=10.0, seq=3)]
    pos = cl.new_positions(tr)
    assert len(pos) == 1 and pos[0].cash24_micro == 999_990_000          # 500.00 + 499.99 = 999.99
    assert cl.position_skip(pos[0], {pos[0].condition: R(pos[0].condition, 0, t0 + 2 * DAY)}) == "under_1000_in_24h"
    tr[1] = T("w", t0 + DAY, "a", price=0.5, size=999.99, seq=1)             # 500.00 + 499.995 = 999.995
    pos = cl.new_positions(tr)
    assert pos[0].cash24_micro == 999_995_000
    assert cl.position_skip(pos[0], {pos[0].condition: R(pos[0].condition, 0, t0 + 2 * DAY)}) == "under_1000_in_24h"
    tr[1] = T("w", t0 + DAY, "a", price=0.5, size=1000.0, seq=1)
    pos = cl.new_positions(tr)
    assert pos[0].cash24_micro == 1_000_000_000
    assert cl.position_skip(pos[0], {pos[0].condition: R(pos[0].condition, 0, t0 + 2 * DAY)}) is None


@pytest.mark.parametrize("price,ok", [(0.10, True), (0.90, True), (0.0999, False), (0.9001, False), (0.5, True)])
def test_first_price_from_0_10_to_0_90(price, ok):
    t0 = MS(2026, 3)
    pos = cl.new_positions([T("w", t0, "a", price=price, size=20000.0)])
    why = cl.position_skip(pos[0], {"c-a": R("c-a", 0, t0 + DAY)})
    assert (why is None) == ok and (ok or why == "first_price_outside_0.10_0.90")


@pytest.mark.parametrize("resolution,why", [
    (lambda t0: R("c-a", 0, t0 + 45 * DAY), None),
    (lambda t0: R("c-a", 1, t0 + 45 * DAY + 1), "resolved_after_45_days"),
    (lambda t0: R("c-a", payouts=[500_000, 500_000], resolved_ts=t0 + DAY), "resolved_otherwise"),
    (lambda t0: R("c-a", payouts=[0, 0], resolved_ts=t0 + DAY), "resolved_otherwise"),
    (lambda t0: R("c-a", status="proposed", payouts=None, resolved_ts=None), "not_resolved"),
    (lambda t0: None, "not_resolved")])
def test_the_resolution_rules(resolution, why):
    t0 = MS(2026, 3)
    pos = cl.new_positions([T("w", t0, "a")])
    r = resolution(t0)
    assert cl.position_skip(pos[0], {"c-a": r} if r else {}) == why


def test_an_empty_event_is_left_out_and_counted():
    t0 = MS(2026, 3)
    pos = cl.new_positions([T("w", t0, "a", event="")])
    assert cl.position_skip(pos[0], {"c-a": R("c-a", 0, t0 + DAY)}) == "no_event"


# ------------------------------------------------------------------ the follower's price: the first recorded point
def test_the_first_point_at_or_after_the_moment_at_three_hours_or_finer_before_the_resolution():
    moment = MS(2026, 3) + HOUR
    end = moment + 10 * DAY
    pts = [PT(moment - 1, 0.40, 60), PT(moment + 60, 0.41, 3 * HOUR + 1), PT(moment + 7200, 0.47, 3 * HOUR),
           PT(moment + 9000, 0.48, 300), PT(end, 1.0, 0)]
    pt, why, passed = cl.follow_point(pts, moment, end)
    assert why is None and pt.price == Decimal("0.47") and pt.resolution_seconds == 3 * HOUR
    assert passed == Counter({"point_before_the_moment": 1, "point_coarser_than_3_hours": 1,
                              "point_after_the_window": 1})


@pytest.mark.parametrize("offset,found", [(0, True), (12 * HOUR - 1, True), (12 * HOUR, False), (-1, False)])
def test_the_twelve_hour_window_from_the_moment_start_included_end_not(offset, found):
    moment = MS(2026, 3) + HOUR
    pt, why, _ = cl.follow_point([PT(moment + offset, 0.5)], moment, moment + 10 * DAY)
    assert (pt is not None) == found
    if not found:
        assert why == "no_price_point_after_the_hour"


def test_a_tie_on_time_takes_the_finer_grain_and_a_zero_grain_counts():
    moment = MS(2026, 3) + HOUR
    pt, _, _ = cl.follow_point([PT(moment + 600, 0.51, 3 * HOUR), PT(moment + 600, 0.50, 300)], moment,
                               moment + DAY)
    assert pt.price == Decimal("0.5")
    pt, _, _ = cl.follow_point([PT(moment + 600, 0.52, 0)], moment, moment + DAY)
    assert pt.price == Decimal("0.52")


def test_a_point_at_or_after_the_resolution_is_never_a_price():
    moment = MS(2026, 3) + HOUR
    resolved = moment + 2 * HOUR
    pt, why, passed = cl.follow_point([PT(resolved, 0.5, 0), PT(resolved + 60, 0.5)], moment, resolved)
    assert pt is None and why == "no_price_point_after_the_hour"
    assert passed == Counter({"point_at_or_after_resolution": 2})


def _one(t0, points, price=0.5, win_index=0, resolved=None, delay=HOUR):
    pos = cl.new_positions([T("L", t0, "a", price=price)])
    res = {"c-a": R("c-a", win_index, resolved or t0 + 10 * DAY)}
    return cl.copy_trades(pos, res, {cl.price_key("a", t0 + delay): points}, delay=delay)


@pytest.mark.parametrize("fp,ok", [(0.05, True), (0.95, True), (0.0499, False), (0.9501, False)])
def test_the_0_05_to_0_95_guard_on_the_followers_price(fp, ok):
    t0 = MS(2026, 3) + DAY
    trades, skips = _one(t0, [PT(t0 + HOUR + 60, fp)])
    assert (len(trades) == 1) == ok
    if not ok:
        assert skips == Counter({"follow_price_outside_0.05_0.95": 1})


def test_a_copied_trade_carries_event_price_outcome_wait_and_grain():
    t0 = MS(2026, 3) + DAY
    trades, skips = _one(t0, [PT(t0 + HOUR - 5, 0.30), PT(t0 + HOUR + 90, 0.524, 300)], win_index=0)
    assert not skips and len(trades) == 1
    c = trades[0]
    assert (c.event, c.price_cents, c.win, c.leader, c.wait_seconds, c.grain) == ("ev", 52, True, "L", 90, 300)
    trades, _ = _one(t0, [PT(t0 + HOUR + 90, 0.524)], win_index=1)
    assert trades[0].win is False


def test_no_window_no_point_and_resolved_before_the_follow_are_counted():
    t0 = MS(2026, 3) + DAY
    pos = cl.new_positions([T("L", t0, "a")])
    trades, skips = cl.copy_trades(pos, {"c-a": R("c-a", 0, t0 + 10 * DAY)}, {}, delay=HOUR)
    assert not trades and skips == Counter({"no_price_window": 1})
    trades, skips = _one(t0, [PT(t0 + HOUR - 1, 0.5), PT(t0 + 13 * HOUR, 0.5)])
    assert not trades and skips == Counter({"no_price_point_after_the_hour": 1})
    trades, skips = _one(t0, [PT(t0 + HOUR, 0.5)], resolved=t0 + HOUR)
    assert not trades and skips == Counter({"resolved_before_follow": 1})


def test_when_the_only_point_before_the_resolution_is_the_settlement_point_the_trade_is_left_out():
    """The market resolves 90 minutes after the hour; the window holds only its terminal settlement point (grain 0,
    stamped at the resolution, a winner's price 1.0 or a price inside the guard): left out, never a gain."""
    t0 = MS(2026, 3) + DAY
    resolved = t0 + HOUR + 5400
    for price in (1.0, 0.94):
        trades, skips = _one(t0, [PT(resolved, price, 0)], resolved=resolved)
        assert not trades and skips == Counter({"no_price_point_after_the_hour": 1})


# ------------------------------------------------------------------ standing and leaders
def _book(trades, res):
    return cl.wallet_book(trades, res)


def test_standing_counts_sales_and_resolutions_inside_the_three_months_only():
    M = (2026, 4)
    w0, m0 = MS(2026, 1), MS(2026, 4)
    tr = [T("w", w0 - 10 * DAY, "old", price=0.5, size=100.0, seq=0),          # bought before, resolved inside: counts
          T("w", w0 + DAY, "s", price=0.4, size=100.0, seq=1),
          T("w", w0 + 2 * DAY, "s", side="SELL", price=0.5, size=50.0, seq=2),   # +5.00
          T("w", w0 + 3 * DAY, "open", price=0.3, size=100.0, seq=3),           # open at M0: neither gain nor loss
          T("w", w0 + 4 * DAY, "late", price=0.3, size=100.0, seq=4)]           # resolves after M0: not counted
    res = {"c-old": R("c-old", 0, w0 + 5 * DAY), "c-late": R("c-late", 0, m0 + 1)}
    st = cl.standing(_book(tr, res), M)
    assert st.profit_micro == 55_000_000                     # 100 x (1 - 0.5) + 50 x (0.5 - 0.4)
    assert st.resolved_positions == 1
    assert st.shares_micro == (100 + 50 + 100 + 100) * 1_000_000


def test_a_resolution_exactly_at_the_start_of_the_month_is_not_yet_known():
    M = (2026, 4)
    tr = [T("w", MS(2026, 2), "x", price=0.5, size=100.0)]
    assert cl.standing(_book(tr, {"c-x": R("c-x", 0, MS(2026, 4))}), M).profit_micro == 0
    assert cl.standing(_book(tr, {"c-x": R("c-x", 0, MS(2026, 4) - 1)}), M).profit_micro == 50_000_000


def test_selling_more_than_the_feed_shows_held_is_counted_and_earns_nothing():
    tr = [T("w", MS(2026, 2), "x", price=0.5, size=10.0, seq=0),
          T("w", MS(2026, 2) + DAY, "x", side="SELL", price=0.6, size=30.0, seq=1)]
    b = _book(tr, {})
    assert cl.standing(b, (2026, 4)).profit_micro == 1_000_000
    assert b.counts["sell_beyond_holding"] == 1


def test_how_much_the_standing_rule_missed_through_sells_beyond_holding():
    a = [T("a", MS(2026, 2), "x", price=0.5, size=10.0, seq=0),
         T("a", MS(2026, 2) + DAY, "x", side="SELL", price=0.6, size=30.0, seq=1),     # beyond, in Feb
         T("a", MS(2026, 2) + 2 * DAY, "y", price=0.5, size=10.0, seq=2),
         T("a", MS(2026, 2) + 3 * DAY, "y", side="SELL", price=0.6, size=5.0, seq=3),   # within holding
         T("a", MS(2026, 5) + DAY, "z", side="SELL", price=0.6, size=5.0, seq=4)]       # beyond, after the window
    b = [T("b", MS(2026, 2), "q", price=0.5, size=10.0, seq=0)]
    books = {"a": _book(a, {}), "b": _book(b, {}), "c": _book([], {})}
    s = cl.sells_beyond_summary(books, [((2026, 4), "a"), ((2026, 4), "b")])
    assert s == {"wallets_with_sells_beyond_holding": 1, "candidates": 3, "leader_window_sells": 2,
                 "leader_window_sells_beyond_holding": 1, "share_of_leader_sells": 0.5}


def _qualifier(profit_cents=100_000, resolved=30, shares_per_dollar=10):
    return cl.Standing(profit_cents * 10_000, resolved, shares_per_dollar * profit_cents * 10_000)


def test_who_qualifies():
    assert cl.qualifies(_qualifier()) is None
    assert cl.qualifies(_qualifier(resolved=29)) == "fewer_than_30_resolved"
    assert cl.qualifies(_qualifier(profit_cents=0)) == "profit_not_above_zero"
    assert cl.qualifies(_qualifier(shares_per_dollar=20)) == "20_or_more_shares_per_dollar"
    assert cl.qualifies(cl.Standing(10 ** 9, 30, 20 * 10 ** 9 - 1)) is None


def test_the_20_shares_a_dollar_is_compared_exactly_not_after_rounding_to_cents():
    # a profit of 0.995 dollars and exactly 19.9 shares: exactly 20 a dollar, which is not fewer than 20
    assert cl.qualifies(cl.Standing(995_000, 30, 19_900_000)) == "20_or_more_shares_per_dollar"
    assert cl.qualifies(cl.Standing(995_000, 30, 19_899_999)) is None
    # a profit of half a cent is above zero
    assert cl.qualifies(cl.Standing(5_000, 30, 1)) is None
    assert cl.qualifies(cl.Standing(0, 30, 0)) == "profit_not_above_zero"


def test_leaders_are_ranked_on_the_exact_profit():
    st = {"a": cl.Standing(1_000_004_000, 30, 1), "b": cl.Standing(1_000_006_000, 30, 1),
          "c": cl.Standing(1_000_005_000, 30, 1)}
    assert cl.pick_leaders(st)[0] == ["b", "c", "a"]
    # 1000.0040 and 1000.0049 dollars are both 100,000 cents: rounded, the name would put "a" first
    st = {"a": cl.Standing(1_000_004_000, 30, 1), "b": cl.Standing(1_000_004_900, 30, 1),
          "c": cl.Standing(999_000_000, 30, 1)}
    assert cl.pick_leaders(st)[0] == ["b", "a", "c"]
    assert cl.qualifies(_qualifier(shares_per_dollar=50)) == "20_or_more_shares_per_dollar"


def test_leaders_are_the_top_ten_qualifiers_ties_by_wallet_and_fewer_than_three_leave_the_month_out():
    st = {"w%02d" % i: _qualifier(profit_cents=1000 * (i // 2 + 1)) for i in range(14)}
    st["mm"] = _qualifier(profit_cents=10 ** 9, shares_per_dollar=50)
    leaders, skips = cl.pick_leaders(st)
    assert leaders == ["w12", "w13", "w10", "w11", "w08", "w09", "w06", "w07", "w04", "w05"]
    assert skips == Counter({"not_in_top_10": 4, "20_or_more_shares_per_dollar": 1})
    two = {"a": _qualifier(), "b": _qualifier(), "c": _qualifier(resolved=3)}
    leaders, skips = cl.pick_leaders(two)
    assert leaders == [] and skips["fewer_than_30_resolved"] == 1
    three = {"a": _qualifier(), "b": _qualifier(), "c": _qualifier(profit_cents=7)}
    assert cl.pick_leaders(three)[0] == ["a", "b", "c"]


# ------------------------------------------------------------------ the p-value
def test_event_p_is_seeded_and_counts_resamples_not_above_zero():
    k1, p1 = cl.event_p([5, -3, 4, 2, -1] * 40)
    k2, p2 = cl.event_p([5, -3, 4, 2, -1] * 40)
    assert (k1, p1) == (k2, p2) and p1 == k1 / 2000
    assert cl.event_p([])[1] == 1.0
    assert cl.event_p([-1] * 10) == (2000, 1.0)
    assert cl.p_within_limit(100) and not cl.p_within_limit(101)


def test_a_resample_whose_total_is_exactly_zero_is_not_above_zero():
    assert cl.event_p([0, 0, 0]) == (2000, 1.0)
    assert cl.event_p([0]) == (2000, 1.0)
    k, p = cl.event_p([1, -1])                     # totals of -2, 0 or 2: three quarters not above zero
    assert 0.70 < p < 0.80


def test_resampling_is_by_event_and_by_trade_the_verdict_would_differ():
    """Six events of twenty trades: five of +12 cents a trade, one of -36. By event a sum not above zero needs two
    draws of the losing event (p about 0.26); by trade the 120 trades look like a steady gain (p about 0.01)."""
    win = cl.CopiedTrade("w", 85, True, "L", 0, "a", 0, 0, 0, 300, False)
    trades = [win._replace(event="e%d" % e) for e in range(5) for _ in range(20)]
    trades += [cl.CopiedTrade("lose", 33, False, "L", 0, "b", 0, 0, 0, 300, False)] * 20
    ev = cl.evaluate(trades, 3)
    assert ev["events"] == 6 and ev["total_cents"] == 100 * 12 - 20 * 36
    assert 0.2 < ev["p_value"] < 0.33 and not cl.p_within_limit(ev["not_above_zero"])
    vals = np.array([cl.trade_result(t.price_cents, t.win, 3) for t in trades])
    rng = np.random.default_rng(cl.SEED)
    by_trade = np.mean(vals[rng.integers(0, len(vals), size=(2000, len(vals)))].sum(axis=1) <= 0)
    assert by_trade < 0.05


def test_the_event_is_the_unit():
    trades = [cl.CopiedTrade("e1", 50, True, "L", 0, "a", 0, 0, 0, 300, False)] * 3 + \
             [cl.CopiedTrade("e2", 50, False, "L", 0, "b", 0, 0, 0, 300, False)]
    ev = cl.evaluate(trades, 3)
    assert (ev["trades"], ev["events"], ev["total_cents"]) == (4, 2, 3 * 47 - 53)


# ------------------------------------------------------------------ synthetic worlds through the store
ALL_MONTHS = [(2025, 9), (2025, 10), (2025, 11), (2025, 12)] + [(2026, m) for m in range(1, 12)]
SKILLED_SIZE = {"pros": 16, "per_month": 40}          # enough trades that a 3-cent edge is seen


def _next(ym):
    y, m = ym
    return (y + 1, 1) if m == 12 else (y, m + 1)


class World:
    """Wallets, trades, resolutions and each outcome token's public price history, made with a seed. `edge`: how much
    more often than the price says a pro's new position wins; `move`: how far the price jumps at the pro's purchase
    (and stays). Asked for a window [start, end), the history serves: a bucket stamped two hours before `start` (not
    at or after the moment: never used), a six-hour point 30 s after `start` (coarser than three hours: never used),
    the first usable point 120 s after `start` at the grain of its age (five minutes from 2026 on, three hours
    before), and a terminal point of grain 0 one second before `end`."""

    def __init__(self, seed, edge, move, pros=12, per_month=30, months=ALL_MONTHS, market_makers=2, late_rich=False,
                 event_every=2, odd=0.0):
        self.rng = random.Random(seed)
        self.odd = odd                               # a share of positions that fail an entry test (price or cash)
        self.event_every = event_every               # positions to an event (2: a pair; more: events with many trades)
        self.trades: dict[str, list[dict]] = {}
        self.res: dict[str, dict] = {}
        self.prices: dict[str, list] = {}            # token -> [t1, price before t1, price from t1 on]
        self.n = 0
        for ym in months:
            for i in range(pros):
                for _ in range(per_month):
                    self.position("pro%02d" % i, ym, edge, move)
            for k in range(market_makers):
                self.market_maker("mm%d" % k, ym)
            if late_rich:
                for _ in range(35):
                    rich = ym >= (2026, 3)
                    self.position("late", ym, 0, 0, win=rich, cash=5000 if rich else 1100)

    def _ts(self, ym, margin=2 * DAY):
        lo, hi = MS(*ym), MS(*_next(ym))
        return self.rng.randrange(lo, hi - margin)

    @staticmethod
    def _row(wallet, ts, token, cond, side, price, size, event, oi, h):
        return {"proxy_wallet": wallet, "side": side, "token_id": token, "condition_id": cond, "size": size,
                "price": price, "timestamp": ts, "event_slug": event, "outcome_index": oi,
                "transaction_hash": "0x%x" % h, "slug": token, "title": token, "outcome": "Yes"}

    def _add(self, wallet, ts, token, cond, side, price, size, event, oi):
        self.trades.setdefault(wallet, []).append(self._row(wallet, ts, token, cond, side, price, size, event, oi,
                                                            self.n))

    @staticmethod
    def grain(t1):
        return 300 if t1 >= MS(2026, 1) else 3 * HOUR

    def history(self, token, start, end):
        if token not in self.prices:
            return []
        t1, before, after = self.prices[token]
        pts = [(start - 2 * HOUR, 3 * HOUR), (start + 30, 6 * HOUR), (start + 120, self.grain(t1)), (end - 1, 0)]
        return [{"price": before if ts < t1 else after, "resolution_seconds": r, "timestamp": ts} for ts, r in pts]

    def position(self, wallet, ym, edge, move, win=None, cash=None):
        self.n += 1
        n = self.n
        token, cond, event = "tok%d" % n, "cond%d" % n, "ev%d" % (n // self.event_every)
        t1 = self._ts(ym)
        p = self.rng.randrange(20, 81) / 100
        if self.odd and cash is None and self.rng.random() < self.odd:
            if self.rng.random() < 0.5:
                p = self.rng.choice([0.05, 0.95])
            else:
                cash = self.rng.uniform(200, 900)
        size = round((cash or self.rng.uniform(1100, 3000)) / p, 2)
        if win is None:
            win = self.rng.random() < p + edge
        oi = self.rng.randrange(2)
        self._add(wallet, t1, token, cond, "BUY", p, size, event, oi)
        rts = t1 + self.rng.randrange(2 * DAY, 40 * DAY)
        payouts = [0, 0]
        payouts[oi if win else 1 - oi] = 1_000_000
        self.res[cond] = {"condition_id": cond, "status": "resolved", "payouts": payouts, "resolved_ts": rts,
                          "resolved_at": "x"}
        # before the purchase the market is at p; from the purchase on, at p + move
        self.prices[token] = [t1, p, round(min(0.95, p + move), 4)]
        return t1

    def market_maker(self, wallet, ym):
        for _ in range(40):
            self.n += 1
            token, cond = "mmt%d" % self.n, "mmc%d" % self.n
            t = self._ts(ym)
            self._add(wallet, t, token, cond, "BUY", 0.48, 50000.0, "mmev%d" % self.n, 0)
            self._add(wallet, t + 600, token, cond, "SELL", 0.52, 50000.0, "mmev%d" % self.n, 0)
            self.res[cond] = {"condition_id": cond, "status": "proposed", "payouts": None, "resolved_ts": None,
                              "resolved_at": None}
        for _ in range(35):
            self.position(wallet, ym, 0.3, 0, cash=1000.0)

    def write_phase1(self, s, unfinished_wallet=None):
        boards = {"all": pd.Walk.from_rows([{"rank": i + 1, "user_id": w, "pnl": 1.0, "volume": 1.0}
                                            for i, w in enumerate(sorted(self.trades))], "2026-11-17T09:00:00Z"),
                  "month": pd.Walk.from_rows([], "2026-11-17T09:00:01Z")}
        s.put_boards(boards)
        for w, rows in self.trades.items():
            s.start_walk("trades", w)
            if w != unfinished_wallet:
                s.finish_trades(w, pd.Walk.from_rows(sorted(rows, key=lambda r: -r["timestamp"]), "2026-11-17T09:01:00Z"))
        conds = sorted(self.res)
        for i in range(0, len(conds), 20):
            batch = conds[i:i + 20]
            for c in batch:
                s.start_walk("resolutions", c)
            s.finish_resolutions(batch, pd.Walk.from_rows([self.res[c] for c in batch], "2026-11-17T09:02:00Z"),
                                 {c: "row" for c in batch})

    def _windows(self, s, plan, windows, skip_window):
        for key, wd in sorted(windows.items()):
            how = skip_window(plan[wd.target]) if skip_window else None
            if how == "unstarted":
                continue
            s.start_walk("prices", key)
            if how != "unfinished":
                s.finish_prices(key, pd.Walk.from_rows(self.history(wd.token, wd.start, wd.start + cl.PRICE_SPAN),
                                                       "2026-11-17T09:03:00Z"))

    def write(self, path, unfinished_wallet=None, skip_window=None, phase2=True):
        """`skip_window(target)` -> None, "unfinished" (started, never finished) or "unstarted", for every price
        window of that planned position."""
        with cl.Store(path, make_id=True) as s:                  # the pull makes the store and its id
            self.write_phase1(s, unfinished_wallet)
            if phase2:
                plan = cl.plan_store(s, leave_out=[unfinished_wallet] if unfinished_wallet else [])
                self._windows(s, plan, s.plan_windows(), skip_window)
                self._windows(s, plan, cl.add_ten_minute_windows(s), skip_window)
        return path


def _first(month, record):
    """A skip_window that marks every window of the first planned position of `month` and records it."""
    def f(tg):
        if tg.month == month and (not record or record[0] == tg):
            if not record:
                record.append(tg)
            return f.how
        return None
    return f


@pytest.fixture(scope="module")
def skilled(tmp_path_factory):
    return World(7, edge=0.08, move=0.02, **SKILLED_SIZE).write(tmp_path_factory.mktemp("w") / "skilled.sqlite")


def test_no_skill_fails_at_step_1(tmp_path):
    store = World(3, edge=0.0, move=0.0, pros=30).write(tmp_path / "s.sqlite")
    s = cl.run_develop(store=store, out_dir=tmp_path / "out")["result"]["step_1"]
    assert s["verdict"] == "FAIL" and "mean_not_above_zero" in s["failed"]
    assert -7 < s["mean_cents"] < 0                     # near minus the cost of three cents
    assert s["trades"] > 500


def test_skill_with_a_small_move_passes_step_1(skilled, tmp_path):
    r = cl.run_develop(store=skilled, out_dir=tmp_path / "out")["result"]
    s = r["step_1"]
    assert s["verdict"] == "PROCEED", s
    assert 1 < s["mean_cents"] < 5 and s["p_value"] <= 0.05
    assert s["seed"] == 20261117 and s["resamples"] == 2000
    # the market makers trade 50 shares a dollar: never a leader, whatever their profit
    assert all(not any(w.startswith("mm") for w in m["leaders"]) for m in r["month_table"])
    assert r["wallet_skips"]["20_or_more_shares_per_dollar"] > 0
    # every copied trade priced from the point 120 s after the hour: not the earlier bucket, not the coarse point
    assert r["position_skips"] == {}
    fp = r["information"]["follow_point"]
    assert fp["delay_seconds"] == {"median": 120, "p90": 120}
    assert sum(fp["grain_seconds"].values()) == s["trades"] and set(fp["grain_seconds"]) == {"300", "10800"}
    assert fp["points_passed_over"]["point_before_the_moment"] == s["trades"]
    assert fp["points_passed_over"]["point_coarser_than_3_hours"] == s["trades"]


def test_a_price_that_jumps_at_the_purchase_gives_the_follower_nothing(tmp_path):
    """The bias the amendments close: the market is at p until the leader buys, then at p + 0.08 for good, and the
    leader's outcome wins 8 cents more often than p says. Each window also serves a bucket stamped before the hour
    at p. A follower priced from before the jump would gain about 5 cents (8 less the cost of 3); priced from the
    first point at or after the hour, the follower has nothing."""
    store = World(7, edge=0.08, move=0.08, **SKILLED_SIZE).write(tmp_path / "s.sqlite")
    r = cl.run_develop(store=store, out_dir=tmp_path / "out")["result"]
    s = r["step_1"]
    assert 6 < r["information"]["leaders_own_prices"]["mean_cents"] < 10       # the edge was there at p
    assert s["verdict"] == "FAIL" and -6 < s["mean_cents"] < 0
    assert s["mean_price_cents"] > 50 + 7                                       # paid the price after the jump


def test_development_months_and_the_information_tables(skilled, tmp_path):
    r = cl.run_develop(store=skilled, out_dir=tmp_path / "out")["result"]
    months = [m["month"] for m in r["month_table"]]
    assert months[0] == "2025-12" and months[-1] == "2026-06"          # three months of feed before the first
    info = r["information"]
    for key in ("delay_10_minutes", "delay_6_hours", "delay_24_hours", "cost_2_cents", "cost_0_cents", "sports",
                "everything_else", "first_purchase_over_10000", "leaders_own_prices", "follow_point",
                "cost_4_cents"):
        assert info[key]["note"] == "information: decides nothing", key
    assert info["cost_0_cents"]["mean_cents"] == pytest.approx(r["step_1"]["mean_cents"] + 3)
    assert info["cost_4_cents"]["mean_cents"] == pytest.approx(r["step_1"]["mean_cents"] - 1)
    assert info["leaders_own_prices"]["trades"] == r["step_1"]["trades"]
    for k in ("delay_6_hours", "delay_24_hours"):                               # each horizon has its own window
        assert info[k]["trades"] == r["step_1"]["trades"], k
    # the 10-minute figure only where a five-minute grain exists (2026 here), with its count
    grains = info["follow_point"]["grain_seconds"]
    ten = info["delay_10_minutes"]
    assert ten["trades"] == grains["300"] and 0 < ten["trades"] < r["step_1"]["trades"]
    assert ten["position_skips"] == {"main_point_coarser_than_5_minutes": grains["10800"]}
    sb = r["sells_beyond_holding"]
    assert sb["wallets_with_sells_beyond_holding"] == 0 and sb["leader_window_sells"] == 0


def test_develop_writes_its_file_and_hash(skilled, tmp_path):
    import hashlib
    cl.run_develop(store=skilled, out_dir=tmp_path / "out")
    raw = (tmp_path / "out" / "develop.json").read_bytes()
    assert (tmp_path / "out" / "develop.json.sha256").read_text().strip() == hashlib.sha256(raw).hexdigest()
    assert json.loads(raw)["provenance"]["prediction_commit"] == "10ad062"
    assert json.loads(raw)["provenance"]["amendment_commit"] == "dec8dd7"
    assert json.loads(raw)["provenance"]["second_amendment_commit"] == "a7b9379"
    readings = json.loads(raw)["provenance"]["readings"]
    text = " ".join(readings).lower()
    assert len(readings) >= 14
    for phrase in ("average cost", "ties", "wallet name", "first fill", "prefix", "before rounding",
                   "three months after the earliest trade", "more than one resolution row", "identity"):
        assert phrase in text, phrase


# ------------------------------------------------------------------ walk-forward and the period wall
def test_a_wallet_rich_only_after_month_m_is_not_a_leader_of_m(tmp_path):
    w = World(11, edge=0.1, move=0.0, pros=8, per_month=20, late_rich=True,
              months=[(2025, 12), (2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5)])
    books = cl.books_of(cl.load(w.write(tmp_path / "a.sqlite"), "development"))
    leaders_m = cl.leaders_of(books, (2026, 3))[0]
    assert "late" not in leaders_m
    assert "late" in cl.leaders_of(books, (2026, 5))[0]          # rich in March and April: a leader of May
    m0 = MS(2026, 3)
    for row in w.trades["late"]:
        if row["timestamp"] >= m0:
            row["size"] *= 1000
            row["price"] = 0.11
    poisoned = cl.books_of(cl.load(w.write(tmp_path / "b.sqlite"), "development"))
    assert cl.leaders_of(poisoned, (2026, 3))[0] == leaders_m


def test_develop_never_reads_or_uses_a_sealed_month(skilled, tmp_path):
    base = cl.run_develop(store=skilled, out_dir=tmp_path / "o1")["result"]
    rows, points = _reads_of(lambda: cl.load(skilled, "development"))             # every row the reads build
    assert rows and max(r["timestamp"] for r in rows) < cl.SEALED_START + cl.SPILL
    assert points and max(p.ts for p in points) < cl.SEALED_START + cl.DAY + cl.PRICE_SPAN
    w = World(7, edge=0.08, move=0.02, **SKILLED_SIZE)
    for rows in w.trades.values():
        for r in rows:
            if r["timestamp"] >= cl.SEALED_START + cl.SPILL:
                r["size"] *= 77
                r["price"] = 0.11
    for c, r in w.res.items():
        if r["resolved_ts"] and r["resolved_ts"] >= cl.SEALED_START + 50 * DAY:
            r["payouts"] = list(reversed(r["payouts"]))
    for tok, pr in w.prices.items():                   # every price of a sealed position's history
        if pr[0] >= cl.SEALED_START:
            pr[1] = pr[2] = 0.06
    poisoned = cl.run_develop(store=w.write(tmp_path / "p.sqlite"), out_dir=tmp_path / "o2")["result"]
    assert cl._dump(poisoned) == cl._dump(base)                                      # byte for byte


def test_sealed_needs_the_flag_the_development_file_and_runs_once(skilled, tmp_path):
    out = tmp_path / "out"
    with pytest.raises(cl.Refused, match="i-am-opening"):
        cl.run_sealed(store=skilled, out_dir=out, flag=False)
    with pytest.raises(cl.Refused, match="development file"):
        cl.run_sealed(store=skilled, out_dir=out, flag=True)
    assert not (out / "sealed.used").exists()
    cl.run_develop(store=skilled, out_dir=out)
    dev = out / "develop.json"
    good = dev.read_bytes()
    dev.write_bytes(good.replace(b'"PROCEED"', b'"PROCEEX"'))
    with pytest.raises(cl.Refused, match="hash"):
        cl.run_sealed(store=skilled, out_dir=out, flag=True)
    dev.write_bytes(good)
    with pytest.raises(cl.Refused, match="left out"):
        cl.run_sealed(store=skilled, out_dir=out, flag=True, leave_out=["pro01"])
    seen = {}
    real_load = cl.load

    def watching_load(store, period, leave_out=()):
        if period == "sealed":
            seen["marker_before_read"] = (out / "sealed.used").exists()
            seen["store_marker_before_read"] = cl.store_sealed_marker(store) is not None
        return real_load(store, period, leave_out)

    cl.load = watching_load
    try:
        doc = cl.run_sealed(store=skilled, out_dir=out, flag=True)
    finally:
        cl.load = real_load
    assert seen == {"marker_before_read": True, "store_marker_before_read": True}
    marker = json.loads((out / "sealed.used").read_text())
    assert marker["development_sha256"] == (out / "develop.json.sha256").read_text().strip()
    assert marker["store_id"] == cl.store_id(skilled) == doc["provenance"]["store_id"]
    assert cl.store_sealed_marker(skilled)["development_sha256"] == marker["development_sha256"]
    v = doc["result"]["verdict"]
    assert v["verdict"] in ("PASS", "FAIL") and v["thresholds"] == {"min_trades": 200, "min_events": 100,
                                                                    "p_limit": 0.05}
    assert [m["month"] for m in doc["result"]["month_table"]] == ["2026-07", "2026-08", "2026-09", "2026-10"]
    with pytest.raises(cl.Refused, match="already"):
        cl.run_sealed(store=skilled, out_dir=out, flag=True)
    other = tmp_path / "another-folder"                 # the store's own marker: another folder runs nothing
    cl.run_develop(store=skilled, out_dir=other)
    with pytest.raises(cl.Refused, match="already"):
        cl.run_sealed(store=skilled, out_dir=other, flag=True)
    assert not (other / "sealed.used").exists() and not (other / "sealed.json").exists()


def _twin(tmp_path, name):
    return World(7, edge=0.08, move=0.02, **SKILLED_SIZE).write(tmp_path / name)


def _no_marker(store, out):
    return cl.store_sealed_marker(store) is None and not (out / "sealed.used").exists()


def test_a_development_file_of_another_store_refuses_even_with_identical_rows(skilled, tmp_path):
    twin = _twin(tmp_path, "twin.sqlite")             # the same rows, another store
    assert cl.store_id(twin) != cl.store_id(skilled)
    a = tmp_path / "a"
    cl.run_develop(store=skilled, out_dir=a)
    out = tmp_path / "b"
    out.mkdir()
    with pytest.raises(cl.Refused, match="store"):
        cl.run_sealed(store=twin, out_dir=out, flag=True, develop_file=a / "develop.json")
    assert _no_marker(twin, out)


def test_an_edited_development_file_with_a_recomputed_hash_refuses(tmp_path):
    import hashlib
    store = World(3, edge=0.0, move=0.0, pros=8, per_month=15).write(tmp_path / "s.sqlite")
    out = tmp_path / "out"
    cl.run_develop(store=store, out_dir=out)
    dev = out / "develop.json"
    doc = json.loads(dev.read_bytes())
    assert doc["result"]["step_1"]["verdict"] == "FAIL"
    doc["result"]["step_1"].update(verdict="PROCEED", failed=[])
    raw = (json.dumps(doc, sort_keys=True, indent=1) + "\n").encode()
    dev.write_bytes(raw)
    (out / "develop.json.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    with pytest.raises(cl.Refused, match="step 1"):     # the store's own development, recomputed, failed
        cl.run_sealed(store=store, out_dir=out, flag=True)
    assert _no_marker(store, out)


def test_an_edited_figure_in_a_passing_development_file_refuses(tmp_path):
    import hashlib
    store = _twin(tmp_path, "s.sqlite")
    out = tmp_path / "out"
    cl.run_develop(store=store, out_dir=out)
    dev = out / "develop.json"
    doc = json.loads(dev.read_bytes())
    doc["result"]["step_1"]["mean_cents"] += 1
    raw = (json.dumps(doc, sort_keys=True, indent=1) + "\n").encode()
    dev.write_bytes(raw)
    (out / "develop.json.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    with pytest.raises(cl.Refused, match="recomputed"):
        cl.run_sealed(store=store, out_dir=out, flag=True)
    assert _no_marker(store, out)


def test_a_kill_after_the_stores_marker_spends_the_period(tmp_path, monkeypatch):
    store = _twin(tmp_path, "s.sqlite")
    out = tmp_path / "out"
    cl.run_develop(store=store, out_dir=out)
    real_load = cl.load

    def killed(st, period, leave_out=()):
        if period == "sealed":
            raise KeyboardInterrupt("killed")
        return real_load(st, period, leave_out)
    monkeypatch.setattr(cl, "load", killed)
    with pytest.raises(KeyboardInterrupt):
        cl.run_sealed(store=store, out_dir=out, flag=True)
    monkeypatch.setattr(cl, "load", real_load)
    assert cl.store_sealed_marker(store) is not None and not (out / "sealed.json").exists()
    fresh = tmp_path / "fresh"
    cl.run_develop(store=store, out_dir=fresh)
    with pytest.raises(cl.Refused, match="by hand"):
        cl.run_sealed(store=store, out_dir=fresh, flag=True)
    assert not (fresh / "sealed.json").exists()


def test_default_output_folders_are_named_for_the_stores_id(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "REPO", tmp_path)
    w = World(5, edge=0.0, move=0.0, pros=6, per_month=12)
    a, b = w.write(tmp_path / "a.sqlite"), w.write(tmp_path / "b.sqlite")
    da, db = cl.run_develop(store=a), cl.run_develop(store=b)
    fa = tmp_path / cl.OUT_DIR / cl.store_id(a) / "develop.json"
    fb = tmp_path / cl.OUT_DIR / cl.store_id(b) / "develop.json"
    assert fa.is_file() and fb.is_file() and fa.parent != fb.parent
    assert da["provenance"]["store_id"] == cl.store_id(a) and db["provenance"]["store_id"] == cl.store_id(b)
    assert json.loads(fa.read_bytes())["provenance"]["store_id"] == cl.store_id(a)


def test_the_store_id_is_written_once_by_the_pull_only(tmp_path):
    p = tmp_path / "x.sqlite"
    with cl.Store(p):
        pass
    assert cl.store_id(p) is None                              # opening a store never makes an id
    with cl.Store(p, make_id=True):
        pass
    first = cl.store_id(p)
    with cl.Store(p, make_id=True):
        pass
    assert cl.store_id(p) == first and re.fullmatch("[0-9a-f]{32}", first)
    q = tmp_path / "pulled.sqlite"
    cl.pull(q, _client(FakeService()), phase=1, say=lambda m: None)
    assert re.fullmatch("[0-9a-f]{32}", cl.store_id(q))


def _cheap(tmp_path, name="c.sqlite"):
    return World(5, edge=0.0, move=0.0, pros=6, per_month=12).write(tmp_path / name)


def _set_meta_raw(store, key, raw):
    db = sqlite3.connect(str(store))
    if raw is None:
        db.execute("DELETE FROM meta WHERE key=?", (key,))
    else:
        db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, raw))
    db.commit()
    db.close()


def test_a_store_without_an_id_or_with_a_malformed_one_is_refused_in_words_and_never_given_one(tmp_path):
    store = _cheap(tmp_path)
    for raw in (None, "not json{", '"xyz"', "null", "17"):
        _set_meta_raw(store, "store_id", raw)
        for run in (lambda: cl.run_develop(store=store, out_dir=tmp_path / "o"),
                    lambda: cl.run_sealed(store=store, out_dir=tmp_path / "o", flag=True)):
            with pytest.raises(cl.Refused, match="id"):
                run()
        db = sqlite3.connect(str(store))
        row = db.execute("SELECT value FROM meta WHERE key='store_id'").fetchone()
        db.close()
        assert (row[0] if row else None) == raw                # nothing written
        assert not (tmp_path / "o" / "develop.json").exists()


@pytest.mark.parametrize("raw", ["null", "not json{", '"x"', "{}"])
def test_the_markers_key_present_with_any_value_means_spent(tmp_path, raw):
    store = _cheap(tmp_path)
    _set_meta_raw(store, "sealed_marker", raw)
    with pytest.raises(cl.Refused, match="already"):
        cl.run_sealed(store=store, out_dir=tmp_path / "o", flag=True)
    assert not (tmp_path / "o").exists()


def test_with_an_out_dir_a_marker_in_the_stores_default_folder_also_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "REPO", tmp_path / "repo")
    store = _cheap(tmp_path)
    default = tmp_path / "repo" / cl.OUT_DIR / cl.store_id(store)
    default.mkdir(parents=True)
    (default / "sealed.used").write_text("{}")
    with pytest.raises(cl.Refused, match="default folder"):
        cl.run_sealed(store=store, out_dir=tmp_path / "elsewhere", flag=True)
    assert not (tmp_path / "elsewhere").exists()


def test_sealed_refuses_when_development_failed(tmp_path):
    store = World(3, edge=0.0, move=0.0, pros=8, per_month=15).write(tmp_path / "s.sqlite")
    out = tmp_path / "out"
    cl.run_develop(store=store, out_dir=out)
    with pytest.raises(cl.Refused, match="step 1"):
        cl.run_sealed(store=store, out_dir=out, flag=True)
    assert not (out / "sealed.used").exists()


@pytest.mark.parametrize("how", ["unstarted", "unfinished"])
def test_sealed_refuses_before_its_marker_when_a_sealed_window_was_not_pulled(tmp_path, how):
    rec = []
    skip = _first((2026, 8), rec)
    skip.how = how
    store = World(7, edge=0.08, move=0.02, **SKILLED_SIZE).write(tmp_path / "s.sqlite", skip_window=skip)
    assert rec and rec[0].month == (2026, 8)
    out = tmp_path / "out"
    assert cl.run_develop(store=store, out_dir=out)["result"]["step_1"]["verdict"] == "PROCEED"
    real_load = cl.load
    cl.load = lambda *a, **k: pytest.fail("no sealed row may be read")
    try:
        with pytest.raises(cl.Refused, match="not pulled"):
            cl.run_sealed(store=store, out_dir=out, flag=True)
    finally:
        cl.load = real_load
    assert not (out / "sealed.used").exists() and not (out / "sealed.json").exists()


def test_sealed_refuses_before_its_marker_when_the_10_minute_windows_were_not_planned(tmp_path):
    store = World(7, edge=0.08, move=0.02, **SKILLED_SIZE).write(tmp_path / "s.sqlite")
    out = tmp_path / "out"
    cl.run_develop(store=store, out_dir=out)
    with cl.Store(store) as s:
        meta = s.meta("plan")
        meta["ten_minute_windows_added"] = False
        s.set_meta("plan", meta)
    real_load = cl.load
    cl.load = lambda *a, **k: pytest.fail("no sealed row may be read")
    try:
        with pytest.raises(cl.Refused, match="10-minute"):
            cl.run_sealed(store=store, out_dir=out, flag=True)
    finally:
        cl.load = real_load
    assert _no_marker(store, out)


# ------------------------------------------------------------------ an unfinished pull
def test_an_unfinished_pull_is_refused_unless_told_which_wallets_to_leave_out(tmp_path):
    w = World(5, edge=0.0, move=0.0, pros=6, per_month=12)
    store = w.write(tmp_path / "s.sqlite", unfinished_wallet="pro03")
    with pytest.raises(cl.Refused, match="pro03"):
        cl.run_develop(store=store, out_dir=tmp_path / "o")
    doc = cl.run_develop(store=store, out_dir=tmp_path / "o", leave_out=["pro03"])
    assert doc["result"]["wallets_left_out"] == ["pro03"]
    assert "pro03" in cl.format_table(doc)


def test_an_unfinished_price_window_names_the_leader_that_needs_it(tmp_path):
    rec = []
    skip = _first((2026, 2), rec)
    skip.how = "unfinished"
    store = World(9, edge=0.1, move=0.0, pros=8, per_month=20).write(tmp_path / "s.sqlite", skip_window=skip)
    owner = sorted(rec[0].wallets)[0]
    with pytest.raises(cl.Refused, match=owner):
        cl.run_develop(store=store, out_dir=tmp_path / "o")


# ------------------------------------------------------------------ the two-phase pull (fake transports only)
class FakeService:
    """A synthetic data service: three wallets, a few trades each. Records every URL; no request leaves."""

    def __init__(self, fail_wallet=None, reset_wallet=None, reset_condition=None, conflict_condition=None):
        self.urls = []
        self.fail_wallet, self.reset_wallet, self.reset_condition = fail_wallet, reset_wallet, reset_condition
        self.conflict_condition = conflict_condition
        t0 = MS(2026, 2)
        self.trades = {
            "0xa": [{"proxy_wallet": "0xa", "side": "BUY", "token_id": "ta", "condition_id": "ca", "size": 4000.0,
                     "price": 0.5, "timestamp": t0, "event_slug": "e1", "outcome_index": 0,
                     "transaction_hash": "h1", "title": "t", "slug": "s", "outcome": "Yes"}],
            "0xb": [{"proxy_wallet": "0xb", "side": "BUY", "token_id": "tb%d" % i, "condition_id": "cb%d" % i,
                     "size": 10.0, "price": 0.5, "timestamp": t0 + i, "event_slug": "e2", "outcome_index": 0,
                     "transaction_hash": "h%d" % i, "title": "t", "slug": "s", "outcome": "Yes"} for i in range(25)],
            "0xc": []}

    def __call__(self, url):
        self.urls.append(url)
        parts = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(parts.query))
        p = parts.path

        def pg(rows):
            return json.dumps({"data": rows, "pagination": {"limit": 1000, "offset": 0, "has_more": False,
                                                            "next_cursor": None}}).encode()
        if p == "/v2/leaderboard":
            users = ["0xa", "0xb"] if q["time_period"] == "all" else ["0xb", "0xc"]
            return pg([{"rank": i + 1, "user_id": u, "pnl": 1.0, "volume": 1.0} for i, u in enumerate(users)])
        if p == "/v2/trades" and "user" in q:
            if q["user"] == self.fail_wallet:
                raise pd.HttpError(400, url)
            if q["user"] == self.reset_wallet:
                raise ConnectionResetError("reset by peer")
            return pg(self.trades[q["user"]])
        if p == "/v2/resolutions":
            if self.reset_condition in q["condition"].split(","):
                raise ConnectionResetError("reset by peer")
            rows = [{"condition_id": c, "status": "resolved", "payouts": [1_000_000, 0],
                     "resolved_at": "2026-02-10T00:00:00Z"} for c in q["condition"].split(",") if c != "cb24"]
            if self.conflict_condition in q["condition"].split(","):       # a second, different row for one id
                rows.append({"condition_id": self.conflict_condition, "status": "resolved", "payouts": [0, 1_000_000],
                             "resolved_at": "2026-02-11T00:00:00Z"})
            return json.dumps({"data": rows}).encode()
        raise AssertionError(url)


def _client(svc):
    clock = [0.0]
    return pd.PolymarketData(svc, sleep=lambda s: clock.__setitem__(0, clock[0] + s), clock=lambda: clock[0])


def test_pull_phase_1_reads_the_boards_once_and_a_second_run_asks_only_for_what_is_missing(tmp_path):
    store = tmp_path / "pm.sqlite"
    svc, said = FakeService(fail_wallet="0xc"), []
    with pytest.raises(cl.PullIncomplete, match="0xc"):
        cl.pull(store, _client(svc), phase=1, say=said.append)
    assert "requests" in said[0] and svc.urls                    # the count is printed before the first request
    with cl.Store(store) as s:
        assert s.candidates() == ["0xa", "0xb", "0xc"]
        assert s.unfinished("trades") == ["0xc"]
        raw = s.db.execute("SELECT raw, read_at, price FROM trades WHERE wallet='0xa'").fetchone()
        assert json.loads(raw[0])["transaction_hash"] == "h1" and raw[1] and raw[2] == 0.5
        answers = s.resolution_answers()
        assert answers["cb24"] == "no_row" and answers["ca"] == "row" and len(answers) == 26
        assert "cb24" in s.finished_keys("resolutions")
    assert len([u for u in svc.urls if "/v2/resolutions" in u]) == 2           # 26 conditions, 20 a request
    with pytest.raises(cl.Refused, match="phase 1"):
        cl.pull(store, _client(FakeService()), phase=2, say=said.append)
    svc2 = FakeService()
    cl.pull(store, _client(svc2), phase=1, say=said.append)
    assert [u for u in svc2.urls if "/v2/leaderboard" in u or "/v2/resolutions" in u] == []
    assert len(svc2.urls) == 1 and "user=0xc" in svc2.urls[0]
    svc3 = FakeService()
    cl.pull(store, _client(svc3), phase=1, say=said.append)
    assert svc3.urls == []
    cl.pull(store, _client(svc3), phase=2, say=said.append)               # no leaders here: nothing to read
    assert svc3.urls == []


def test_pull_phase_2_reads_price_windows_only_for_the_leaders_qualifying_positions(tmp_path):
    w = World(9, edge=0.1, move=0.0, pros=8, per_month=20)
    store = w.write(tmp_path / "s.sqlite", phase2=False)
    urls = []

    def transport(url):
        urls.append(url)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        assert urllib.parse.urlsplit(url).path == "/v2/prices-history" and set(q) == {"token_id", "start", "end"}
        rows = w.history(q["token_id"], int(q["start"]), int(q["end"]))
        return json.dumps({"data": rows, "pagination": {"limit": 10000, "offset": 0, "has_more": False,
                                                        "next_cursor": None}}).encode()
    said = []
    cl.pull(store, _client(transport), phase=2, say=said.append)
    with cl.Store(store) as s:
        plan = s.plan()
        windows = s.plan_windows()
        meta = s.meta("plan")
        books = cl.books_of(cl.load(store, "development"))
    asked = {(q["token_id"], int(q["start"]), int(q["end"]))
             for q in (dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(u).query)) for u in urls)}
    base = {(tg.token, tg.t1 + d, tg.t1 + d + 12 * HOUR) for tg in plan.values() for d in (HOUR, 6 * HOUR, DAY)}
    ten = {(tg.token, tg.t1 + 600, tg.t1 + 600 + 12 * HOUR) for tg in plan.values() if w.grain(tg.t1) <= 300}
    assert plan and ten and asked == base | ten and len(urls) == len(asked) == len(windows)
    assert meta["ten_minute_windows_added"] is True
    first = [m for m in said if "planning" not in m][0]                 # after the planning step's own lines
    assert "%d" % len(base) in first and "requests" in first              # counted before the first request
    for tg in plan.values():                          # only the month's leaders, only the month's positions
        assert set(tg.wallets) <= set(cl.leaders_of(books, tg.month)[0]) or tg.month >= (2026, 7)
        assert cl.month_of(tg.t1) == tg.month
        assert not any(wl.startswith("mm") for wl in tg.wallets)
    n_positions = sum(1 for rows in w.trades.values() for r in rows if r["side"] == "BUY")
    assert len(plan) < n_positions / 2
    urls.clear()
    cl.pull(store, _client(transport), phase=2, say=said.append)
    assert urls == []
    assert cl.run_develop(store=store, out_dir=tmp_path / "o")["result"]["step_1"]["trades"] > 0


def test_a_reset_connection_is_retried_then_left_unfinished_and_the_pull_goes_on(tmp_path):
    store = tmp_path / "pm.sqlite"
    svc, said = FakeService(reset_wallet="0xa"), []
    with pytest.raises(cl.PullIncomplete, match="0xa"):
        cl.pull(store, _client(svc), phase=1, say=said.append)
    assert len([u for u in svc.urls if "user=0xa" in u]) == 5                # the bounded tries, then on
    with cl.Store(store) as s:
        assert s.unfinished("trades") == ["0xa"]
        assert {"0xb", "0xc"} <= s.finished_keys("trades")
        assert len(s.resolution_answers()) == 25                               # the resolutions were still read
    assert any("unfinished" in line and "0xa" in line for line in said)        # the summary names it
    svc2 = FakeService()
    cl.pull(store, _client(svc2), phase=1, say=said.append)
    assert [u for u in svc2.urls if "/v2/trades" in u] == [u for u in svc2.urls if "user=0xa" in u]


def test_a_failed_resolution_batch_is_left_unfinished_and_the_next_batch_is_still_read(tmp_path):
    store = tmp_path / "pm.sqlite"
    svc, said = FakeService(reset_condition="ca"), []
    with pytest.raises(cl.PullIncomplete, match="resolutions"):
        cl.pull(store, _client(svc), phase=1, say=said.append)
    with cl.Store(store) as s:
        answers = s.resolution_answers()
        assert "ca" not in answers and len(answers) == 6                 # the second batch of six was read
        assert "ca" in s.unfinished("resolutions")
    svc2 = FakeService()
    cl.pull(store, _client(svc2), phase=1, say=said.append)
    assert len([u for u in svc2.urls if "/v2/resolutions" in u]) == 1


def test_the_store_refuses_a_second_candidate_set(tmp_path):
    with cl.Store(tmp_path / "x.sqlite") as s:
        s.put_boards({"all": pd.Walk.from_rows([{"rank": 1, "user_id": "0xa", "pnl": 1.0, "volume": 1.0}], "t")})
        with pytest.raises(cl.Refused):
            s.put_boards({"all": pd.Walk.from_rows([{"rank": 1, "user_id": "0xz", "pnl": 1.0, "volume": 1.0}], "t")})
        assert s.candidates() == ["0xa"]


def test_command_line(skilled, tmp_path, capsys):
    assert cl.main(["sealed", "--store", str(skilled), "--out-dir", str(tmp_path)]) == 2
    assert "REFUSED" in capsys.readouterr().err
    assert cl.main(["develop", "--store", str(skilled), "--out-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "step 1" in out and "information: decides nothing" in out
    assert "sold beyond what their trades show" in out
    assert "readings where the document is silent" in out


# ------------------------------------------------------------------ before the claim, nothing of a sealed position is read
_ID = re.compile(r"\b(?:cond|tok|mmc|mmt)\d+\b")


def _sealed_only(w):
    """Conditions and tokens whose every trade is in the sealed period (or later)."""
    first: dict = {}
    for rows in w.trades.values():
        for r in rows:
            for k in (r["condition_id"], r["token_id"]):
                first[k] = min(first.get(k, 2 ** 62), r["timestamp"])
    return {k for k, t in first.items() if t >= cl.SEALED_START}


class _Counting:
    """Counts every fetched row of the store that names a sealed-only market or token."""

    def __init__(self, ids):
        self.ids, self.hits, self.examples = ids, 0, []

    def look(self, row):
        for v in row:
            if isinstance(v, str):
                found = self.ids.intersection(_ID.findall(v))
                if found:
                    self.hits += 1
                    if len(self.examples) < 3:
                        self.examples.append(v[:120])
                    return

    def install(self, monkeypatch):
        counter = self

        class Cursor:
            def __init__(self, cur):
                self.cur = cur

            def __iter__(self):
                for row in self.cur:
                    counter.look(row)
                    yield row

            def fetchone(self):
                row = self.cur.fetchone()
                if row is not None:
                    counter.look(row)
                return row

            def fetchall(self):
                return list(self)

        class Conn(sqlite3.Connection):
            def execute(self, *a):
                return Cursor(super().execute(*a))

        real = sqlite3.connect
        monkeypatch.setattr(cl.sqlite3, "connect", lambda *a, **k: real(*a, factory=Conn, **k))


def test_a_development_run_fetches_no_row_of_a_sealed_only_market(tmp_path, monkeypatch):
    w = World(7, edge=0.08, move=0.02, **SKILLED_SIZE)
    store = w.write(tmp_path / "s.sqlite")
    ids = _sealed_only(w)
    assert len(ids) > 1000
    c = _Counting(ids)
    c.install(monkeypatch)
    assert cl.run_develop(store=store, out_dir=tmp_path / "o")["result"]["step_1"]["verdict"] == "PROCEED"
    assert c.hits == 0, c.examples


def test_sealed_fetches_no_row_of_a_sealed_only_market_before_its_claim(tmp_path, monkeypatch):
    w = World(7, edge=0.08, move=0.02, **SKILLED_SIZE)
    store = w.write(tmp_path / "s.sqlite")
    out = tmp_path / "o"
    cl.run_develop(store=store, out_dir=out)
    c = _Counting(_sealed_only(w))
    c.install(monkeypatch)

    class AtTheClaim(Exception):
        pass

    def stop():
        raise AtTheClaim()
    monkeypatch.setattr(cl, "_BEFORE_CLAIM", stop)
    with pytest.raises(AtTheClaim):
        cl.run_sealed(store=store, out_dir=out, flag=True)
    assert c.hits == 0, c.examples
    assert cl.store_sealed_marker(store) is None


# ------------------------------------------------------------------ the claim is one atomic act: real processes
CHILD = r"""
import os, sys, time
from pathlib import Path
from valedia_trader.research_loop import copy_leaders as cl
store, out, dev, barrier, me, repo = sys.argv[1:7]
cl.REPO = Path(repo)
def meet():
    Path(barrier + "." + me).touch()
    other = barrier + "." + ("b" if me == "a" else "a")
    t = time.time()
    while not os.path.exists(other) and time.time() - t < 120:
        time.sleep(0.001)
cl._BEFORE_CLAIM = meet
try:
    cl.run_sealed(store=store, out_dir=out, flag=True, develop_file=dev)
    print("RAN")
except cl.Refused as e:
    print("REFUSED " + str(e))
"""


def test_thirty_pairs_of_real_processes_claim_the_sealed_period_once_each(tmp_path):
    assert hasattr(cl, "_BEFORE_CLAIM")
    base = World(7, edge=0.08, move=0.02, **SKILLED_SIZE).write(tmp_path / "base.sqlite")
    root = str(Path(cl.__file__).resolve().parents[2])
    env = dict(os.environ, PYTHONPATH=root, PYTHONDONTWRITEBYTECODE="1")
    script = tmp_path / "child.py"
    script.write_text(CHILD)
    for i in range(30):
        d = tmp_path / ("pair%02d" % i)
        d.mkdir()
        store = d / "s.sqlite"
        shutil.copyfile(base, store)
        cl.run_develop(store=store, out_dir=d / "dev")
        procs = [subprocess.Popen([sys.executable, "-B", str(script), str(store), str(d / me), str(d / "dev" /
                                   "develop.json"), str(d / "barrier"), me, str(d / "repo")], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for me in ("a", "b")]
        outs = [p.communicate(timeout=600) for p in procs]
        said = sorted(o[0].strip().split(" ", 1)[0] for o in outs)
        assert said == ["RAN", "REFUSED"], (i, outs)
        ran = [me for me in ("a", "b") if (d / me / "sealed.json").exists()]
        assert len(ran) == 1, (i, ran)
        loser = "b" if ran == ["a"] else "a"
        assert not (d / loser).exists(), (i, list((d / loser).iterdir()))     # nothing written for the loser


# ------------------------------------------------------------------ the outputs print the store's counts by reason
def test_the_outputs_print_the_stores_counts_by_reason(tmp_path):
    store = _cheap(tmp_path)
    db = sqlite3.connect(str(store))
    db.executemany("INSERT INTO rejects VALUES (?,?,?,?,?)", [
        ("prices", "k1", "conflicting_row", "{}", "t"), ("prices", "k1", "conflicting_row", "{}", "t"),
        ("trades", "pro01", "repeated_row", "{}", "t"), ("trades", "pro02", "timestamp_out_of_span", "{}", "t")])
    db.commit()
    db.close()
    doc = cl.run_develop(store=store, out_dir=tmp_path / "o")
    # "k1" is no window of a development position: development prints only what a development run may read
    assert doc["result"]["store_counts"] == {"trades": {"repeated_row": 1, "timestamp_out_of_span": 1}}
    text = cl.format_table(doc)
    assert "rows the store set aside" in text and "timestamp_out_of_span" in text and "conflicting_row" not in text
    sealed = cl.load(store, "sealed")                    # the sealed output prints the whole store's counts
    assert sealed.store_counts == {"prices": {"conflicting_row": 2},
                                   "trades": {"repeated_row": 1, "timestamp_out_of_span": 1}}
    assert "conflicting_row" in cl.format_table({"provenance": {}, "result": cl.sealed_result(sealed)})


# ------------------------------------------------------------------ addendum 5
def _sha(p):
    import hashlib
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _rebuild(db, table, sql_from, sql_to, index=None):
    """Remake `table` from its stored definition with one text changed (its rows kept)."""
    sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
    new = sql.replace(sql_from, sql_to)
    assert new != sql, sql
    db.executescript("ALTER TABLE %s RENAME TO old_t; %s; INSERT INTO %s SELECT * FROM old_t; DROP TABLE old_t;"
                     % (table, new, table))
    if index:
        db.execute(index)


def _break(store, how):
    sid = cl.store_id(store)
    db = sqlite3.connect(str(store))
    if how == "meta_without_its_key_and_two_ids":        # the store the blind check built
        _rebuild(db, "meta", "key TEXT PRIMARY KEY", "key TEXT")
        db.execute("INSERT INTO meta VALUES ('store_id', ?)", (json.dumps(sid),))
    elif how == "trades_without_its_key":
        _rebuild(db, "trades", ", PRIMARY KEY (wallet, seq)", "",
                 index="CREATE INDEX trades_ts ON trades (wallet, timestamp)")
    elif how == "a_table_missing":
        db.execute("DROP TABLE rejects")
    elif how == "an_extra_table":
        db.execute("CREATE TABLE extra (x)")
    elif how == "a_trigger":
        db.execute("CREATE TRIGGER t AFTER INSERT ON meta BEGIN DELETE FROM meta WHERE key='sealed_marker'; END")
    elif how == "a_column_constraint_dropped":           # only the stored definition differs, no name
        _rebuild(db, "candidates", "boards TEXT NOT NULL", "boards TEXT")
    elif how == "two_markers":
        _rebuild(db, "meta", "key TEXT PRIMARY KEY", "key TEXT")
        db.executemany("INSERT INTO meta VALUES ('sealed_marker', ?)", [("1",), ("2",)])
    db.commit()
    db.close()


SHAPES = ["meta_without_its_key_and_two_ids", "trades_without_its_key", "a_table_missing", "an_extra_table",
          "a_trigger", "two_markers", "a_column_constraint_dropped"]


@pytest.mark.parametrize("how", SHAPES)
def test_a_store_not_of_the_programs_own_shape_is_refused_in_words_with_nothing_written(tmp_path, how):
    store = _cheap(tmp_path)
    _break(store, how)
    before = _sha(store)
    for run in (lambda: cl.run_develop(store=store, out_dir=tmp_path / "o"),
                lambda: cl.run_sealed(store=store, out_dir=tmp_path / "o", flag=True),
                lambda: cl.load(store, "development")):
        with pytest.raises(cl.Refused, match="shape"):
            run()
    assert _sha(store) == before and not (tmp_path / "o").exists()


def test_the_store_the_check_built_runs_no_sealed_period_at_all(tmp_path):
    store = _twin(tmp_path, "s.sqlite")
    out = tmp_path / "out"
    assert cl.run_develop(store=store, out_dir=out)["result"]["step_1"]["verdict"] == "PROCEED"
    _break(store, "meta_without_its_key_and_two_ids")
    before = _sha(store)
    for _ in range(2):
        with pytest.raises(cl.Refused, match="shape"):
            cl.run_sealed(store=store, out_dir=out, flag=True)
    assert _sha(store) == before and not (out / "sealed.used").exists() and not (out / "sealed.json").exists()


def test_conflicting_resolution_rows_are_counted_under_their_own_reason_and_printed(tmp_path):
    store = tmp_path / "pm.sqlite"
    cl.pull(store, _client(FakeService(conflict_condition="ca")), phase=1, say=lambda m: None)
    with cl.Store(store) as s:
        assert s.resolution_answers()["ca"] == "duplicate"              # still not trusted
        assert s.resolution_rows(["ca"]) == {}
        assert s.counts_by_reason() == {"resolutions": {"conflicting_row": 2}}
    doc = cl.run_develop(store=store, out_dir=tmp_path / "o")          # "ca" is traded in February
    assert doc["result"]["store_counts"] == {"resolutions": {"conflicting_row": 2}}
    assert "conflicting_row" in cl.format_table(doc)
    clean = tmp_path / "clean.sqlite"
    cl.pull(clean, _client(FakeService()), phase=1, say=lambda m: None)
    with cl.Store(clean) as s:
        assert s.counts_by_reason() == {}


def _rej(store, rows):
    db = sqlite3.connect(str(store))
    db.executemany("INSERT INTO rejects VALUES (?,?,?,?,?)", [r + ("2026-11-17T09:03:00Z",) for r in rows])
    db.commit()
    db.close()


def test_development_prints_only_counts_it_may_read_and_sealed_the_whole_stores(tmp_path):
    w = World(5, edge=0.0, move=0.0, pros=6, per_month=12)
    store = w.write(tmp_path / "c.sqlite")
    with cl.Store(store) as s:
        plan, windows = s.plan(), s.plan_windows()
    sealed_key = sorted(k for k, wd in windows.items() if plan[wd.target].t1 >= cl.SEALED_START)[0]
    dev_key = sorted(k for k, wd in windows.items() if plan[wd.target].t1 < cl.SEALED_START)[0]
    sealed_cond = sorted(c for c in _sealed_only(w) if c.startswith("cond"))[0]
    dev_cond = next(r["condition_id"] for r in w.trades["pro01"] if r["timestamp"] < cl.SEALED_START - DAY)

    def pt(ts):
        return json.dumps({"price": 0.5, "resolution_seconds": 300, "timestamp": ts})

    def tr(ts):
        return json.dumps({"proxy_wallet": "pro01", "timestamp": ts})
    cl.run_develop(store=store, out_dir=tmp_path / "a")
    _rej(store, [("prices", sealed_key, "repeated_row", pt(cl.SEALED_START + DAY))] * 3
         + [("prices", sealed_key, "conflicting_row", pt(cl.SEALED_START + DAY))] * 2
         + [("trades", "pro01", "repeated_row", tr(cl.SEALED_START + 3 * DAY)),
            ("trades", "pro01", "repeated_row", tr(cl.SEALED_START + 10)),
            ("trades", "pro01", "timestamp_not_whole", tr(cl.SEALED_START + 5 * DAY + 0.5))]
         + [("resolutions", sealed_cond, "conflicting_row", "{}")] * 2)
    cl.run_develop(store=store, out_dir=tmp_path / "b")
    a, b = (tmp_path / "a" / "develop.json").read_bytes(), (tmp_path / "b" / "develop.json").read_bytes()
    assert a == b                                        # rows of sealed-only windows change nothing in development
    _rej(store, [("prices", dev_key, "repeated_row", pt(cl.SEALED_START - DAY)),
                 ("trades", "pro01", "repeated_row", tr(cl.SEALED_START - DAY)),
                 ("trades", "pro01", "bad_row", "not json{"),                  # undated: counted
                 ("resolutions", dev_cond, "conflicting_row", "{}")])
    c = cl.run_develop(store=store, out_dir=tmp_path / "c")
    assert c["result"]["store_counts"] == {"prices": {"repeated_row": 1}, "resolutions": {"conflicting_row": 1},
                                           "trades": {"bad_row": 1, "repeated_row": 1}}     # these are read
    assert cl.load(store, "sealed").store_counts == {"prices": {"conflicting_row": 2, "repeated_row": 4},
                                                     "resolutions": {"conflicting_row": 3},
                                                     "trades": {"bad_row": 1, "repeated_row": 3,
                                                                "timestamp_not_whole": 1}}


def test_the_docstring_states_what_development_reads_and_the_copied_store_limit_exactly():
    doc = " ".join(cl.__doc__.split())
    assert "the resolutions of every market a candidate traded before 2026-07-01" in doc
    assert "only those a development position or a development standing needs are used" in doc
    assert "can be run again whenever the original's sealed run used an output folder other than the default" in doc
    assert "in the same repository too" in doc
    assert "the default folder's marker protects only" in doc


# ------------------------------------------------------------------ addendum 6: the pull holds one page at a time
SMALL_MONTHS = [(2026, m) for m in range(3, 11)]
FIXED = "2026-11-17T12:00:00+00:00"
WALL = MS(2027, 1)                                     # the stand-in clients' start: every world time is in span
SCHEMA_SHA_27CDE89 = "3f029b1870438af6b52ddb944584293567e0101b141641158f9abb582fa2f644"


def _iso(ts):
    import time as _t
    return _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(ts))


class PagedWorld:
    """A stand-in service serving a World page by page: each wallet's trades newest first, `size` rows a page with a
    cursor, and (seeded) rows repeated, in conflict or of the wrong shape on a later page than their first; the
    resolutions; each price window two points a page, with a repeat and a conflict across pages. The same answer to
    the same address, every time. `kill` = (wallet, page): the process ends there at once (os._exit), as a kill.
    `fail` = (wallet, page): a 400 there. `log`: a file each address asked is appended to. No request leaves."""

    def __init__(self, world, seed, size=None, kill=None, fail=None, log=None, clock=None, step=0.0):
        self.w, self.seed = world, seed
        rng = random.Random(seed)
        self.size = size or rng.randrange(3, 10)
        self.kill, self.fail, self.log, self.clock, self.step = kill, fail, log, clock, step
        self.urls = []
        self.feeds = {}
        for wallet, rows in sorted(world.trades.items()):
            feed = [dict(r) for r in sorted(rows, key=lambda r: (-r["timestamp"], r["transaction_hash"]))]
            for _ in range(rng.randrange(0, 4)):
                i = rng.randrange(len(feed))
                j = min(len(feed), i + self.size + rng.randrange(0, 2 * self.size))
                kind = rng.choice(["repeat", "conflict", "bad"])
                extra = {"repeat": {}, "conflict": {"title": "another"}, "bad": {"outcome_index": 7}}[kind]
                feed.insert(j, dict(feed[i], **extra))
            self.feeds[wallet] = feed

    def __call__(self, url):
        self.urls.append(url)
        if self.log:
            with open(self.log, "a") as f:
                f.write(url + "\n")
        if self.clock is not None:
            self.clock.t += self.step
        parts = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(parts.query))
        p = parts.path

        def pg(rows, off, n):
            more = off + self.size < n
            return json.dumps({"data": rows, "pagination": {"limit": 1000, "offset": off, "has_more": more,
                                                            "next_cursor": str(off + self.size) if more else None}})
        if p == "/v2/leaderboard":
            users = sorted(self.feeds) if q["time_period"] == "all" else []
            return json.dumps({"data": [{"rank": i + 1, "user_id": u, "pnl": 1.0, "volume": 1.0}
                                        for i, u in enumerate(users)],
                               "pagination": {"has_more": False, "next_cursor": None}}).encode()
        if p == "/v2/trades":
            off = int(q.get("cursor", "0"))
            here = (q["user"], off // self.size + 1)
            if self.kill == here:
                os._exit(9)
            if self.fail == here:
                raise pd.HttpError(400, url)
            feed = self.feeds[q["user"]]
            return pg(feed[off:off + self.size], off, len(feed)).encode()
        if p == "/v2/resolutions":
            rows = []
            for c in q["condition"].split(","):
                r = self.w.res[c]
                rows.append({"condition_id": c, "status": r["status"], "payouts": r["payouts"],
                             "resolved_at": _iso(r["resolved_ts"]) if r["resolved_ts"] else None})
            return json.dumps({"data": rows}).encode()
        if p == "/v2/prices-history":
            start, end = int(q["start"]), int(q["end"])
            pts = self.w.history(q["token_id"], start, end)
            rng = random.Random("%s:%s:%s" % (self.seed, q["token_id"], start))
            if pts and rng.random() < 0.3:
                pts.append(dict(pts[0]))                              # a repeat on a later page
            if pts and rng.random() < 0.2:
                pts.append(dict(pts[1], price=0.07))                  # a conflict on a later page
            off = int(q.get("cursor", "0"))
            return pg(pts[off:off + self.size], off, len(pts)).encode()
        raise AssertionError(url)


def _paged_client(svc, now=None):
    clock = [0.0]
    if now is None:
        n = [0]

        def now():
            n[0] += 1
            return "2026-11-17T10:%02d:%02d+00:00" % divmod(n[0] % 3600, 60)
    return pd.PolymarketData(svc, sleep=lambda s: clock.__setitem__(0, clock[0] + s), clock=lambda: clock[0],
                             now=now, wall=lambda: WALL)


def _old_pull(store, client, phase, leave_out=()):
    """The pull as it was at 27cde89, each walk held whole and written at its end: the reference."""
    with cl.Store(store, make_id=True) as s:
        if phase == 1:
            if not s.board_read_at():
                s.put_boards({"all": client.leaderboard("all"), "month": client.leaderboard("month")})
                s.commit()
            for w in s.unfinished("trades"):
                s.start_walk("trades", w)
                s.commit()
                s.finish_trades(w, client.trades(w))
                s.commit()
            conds = sorted(s.traded_conditions() - set(s.resolution_answers()))
            for c in conds:
                s.start_walk("resolutions", c)
            s.commit()
            for i in range(0, len(conds), pd.MAX_CONDITIONS):
                for batch, w, answers in client.resolution_batches(conds[i:i + pd.MAX_CONDITIONS]):
                    s.finish_resolutions(batch, w, answers)
                s.commit()
        else:
            cl.plan_store(s, leave_out)
            s.commit()

            def read(windows):
                done = s.finished_keys("prices")
                for k in sorted(windows):
                    if k in done:
                        continue
                    wd = windows[k]
                    s.start_walk("prices", k)
                    s.commit()
                    s.finish_prices(k, client.prices_history(wd.token, wd.start, wd.start + cl.PRICE_SPAN))
                    s.commit()
            read(s.plan_windows())
            read(cl.add_ten_minute_windows(s))
            s.commit()
        s.set_meta("last_pull_phase_%d_ended_at" % phase, cl._now())


def _tables(path):
    """Every table's rows, sorted (a table without a key has no order); meta without the store's own id and the
    pace the new pull records."""
    db = sqlite3.connect(str(path))
    out = {}
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        rows = db.execute("SELECT * FROM %s" % name).fetchall()
        if name == "meta":
            rows = [r for r in rows if r[0] not in ("store_id", "pull_pace")]
        out[name] = sorted(rows, key=repr)
    db.close()
    return out


def _small_world(seed):
    return World(seed, edge=0.05, move=0.0, pros=5, per_month=11, months=SMALL_MONTHS)


def test_a_store_pulled_page_by_page_equals_one_pulled_whole_on_30_seeded_worlds(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "_now", lambda: FIXED)
    seen = Counter()
    for seed in range(30):
        w = _small_world(seed)
        old, new = tmp_path / ("old%02d.sqlite" % seed), tmp_path / ("new%02d.sqlite" % seed)
        oc, nc = _paged_client(PagedWorld(w, seed)), _paged_client(PagedWorld(w, seed))
        _old_pull(old, oc, 1)
        cl.pull(new, nc, phase=1, say=lambda m: None)
        _old_pull(old, oc, 2)
        cl.pull(new, nc, phase=2, say=lambda m: None)
        a, b = _tables(old), _tables(new)
        assert a.keys() == b.keys()
        for name in a:
            assert a[name] == b[name], (seed, name, len(a[name]), len(b[name]))
        cl.check_own_shape(new)
        for kind, _, reason, _, _ in b["rejects"]:
            seen[(kind, reason)] += 1
        seen["price_points"] += len(b["price_points"])
        seen["trades"] += len(b["trades"])
        old.unlink()
        new.unlink()
    for k in (("trades", "repeated_row"), ("trades", "conflicting_row"), ("trades", "bad_outcome_index"),
              ("prices", "repeated_row"), ("prices", "conflicting_row"), "price_points", "trades"):
        assert seen[k] > 0, (k, seen)


KILL_CHILD = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import test_copy_leaders as T
from valedia_trader.research_loop import copy_leaders as cl
cl._now = lambda: T.FIXED
store, log, wallet, page, seed = sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), int(sys.argv[6])
w = T._small_world(seed)
svc = T.PagedWorld(w, seed, size=20, kill=(wallet, page), log=log)
cl.pull(store, T._paged_client(svc, now=lambda: T.FIXED), phase=1, say=lambda m: None)
print("ENDED WITHOUT A KILL")
"""


def _run_child(script, *args, timeout=600):
    root = str(Path(cl.__file__).resolve().parents[2])
    env = dict(os.environ, PYTHONPATH=root, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-B", str(script)] + [str(a) for a in args], env=env, capture_output=True,
                          text=True, timeout=timeout)


def test_a_kill_at_every_page_leaves_no_row_of_the_walk_and_a_rerun_restarts_it_from_its_first_page(tmp_path,
                                                                                                    monkeypatch):
    seed, target = 3, "pro01"
    w = _small_world(seed)
    pages = -(-len(PagedWorld(w, seed, size=20).feeds[target]) // 20)
    assert pages >= 4
    script = tmp_path / "child.py"
    script.write_text(KILL_CHILD)
    tests_dir = Path(__file__).resolve().parent
    store = tmp_path / "s.sqlite"
    order = sorted(w.trades)
    nxt = order[order.index(target) + 1]
    for k in range(1, pages + 2):
        log = tmp_path / ("urls%02d.log" % k)
        who, at = (target, k) if k <= pages else (nxt, 1)  # the last: the first page of the next wallet
        r = _run_child(script, tests_dir, store, log, who, at, seed)
        assert r.returncode == -9 or r.returncode == 9, (k, r.returncode, r.stdout[-300:], r.stderr[-800:])
        cl.check_own_shape(store)
        db = sqlite3.connect(str(store))
        status = db.execute("SELECT status FROM walks WHERE kind='trades' AND key=?", (target,)).fetchone()
        rows = db.execute("SELECT COUNT(*) FROM trades WHERE wallet=?", (target,)).fetchone()[0]
        rej = db.execute("SELECT COUNT(*) FROM rejects WHERE kind='trades' AND key=?", (target,)).fetchone()[0]
        before = [x for (x,) in db.execute("SELECT key FROM walks WHERE kind='trades' AND status='finished'")]
        db.close()
        urls = log.read_text().splitlines()
        mine = [u for u in urls if "user=%s" % target in u]
        if k <= pages:
            assert status == ("started",) and rows == 0 and rej == 0, (k, status, rows, rej)
            assert len(mine) == k and "cursor" not in mine[0]               # from its first page
            assert sorted(before) == order[:order.index(target)]
        else:
            assert status == ("finished",) and rows > 0
        if k > 1:                                       # a re-run repeats no finished request
            assert not [u for u in urls if "leaderboard" in u]
            assert not [u for u in urls if any("user=%s&" % f in u for f in order[:order.index(target)])]
    monkeypatch.setattr(cl, "_now", lambda: FIXED)
    cl.pull(store, _paged_client(PagedWorld(w, seed, size=20), now=lambda: FIXED), phase=1, say=lambda m: None)
    ref = tmp_path / "ref.sqlite"
    _old_pull(ref, _paged_client(PagedWorld(w, seed, size=20), now=lambda: FIXED), 1)
    a, b = _tables(ref), _tables(store)
    for name in a:
        assert a[name] == b[name], name


MEM_CHILD = r"""
import json, resource, sys
from valedia_trader.research_loop import copy_leaders as cl
from valedia_trader.venues import polymarket_data as pd
store, pages, per = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
T0 = 1_780_000_000
def body(off):
    rows = [{"proxy_wallet": "0xbig", "side": "BUY", "token_id": "t%d" % (i % 50), "condition_id": "c%d" % (i % 50),
             "size": 10.0, "price": 0.5, "timestamp": T0 - i, "event_slug": "e", "outcome_index": 0,
             "transaction_hash": "0x%x" % i, "title": "a title of some length", "slug": "s"} for i in range(off, off + per)]
    more = off + per < pages * per
    return {"data": rows, "pagination": {"has_more": more, "next_cursor": str(off + per) if more else None}}
def fetch(url):
    import urllib.parse
    p = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(p.query))
    if p.path == "/v2/leaderboard":
        users = ["0xbig"] if q["time_period"] == "all" else []
        return json.dumps({"data": [{"rank": 1, "user_id": u, "pnl": 1.0, "volume": 1.0} for u in users],
                           "pagination": {"has_more": False, "next_cursor": None}}).encode()
    if p.path == "/v2/trades":
        return json.dumps(body(int(q.get("cursor", "0")))).encode()
    if p.path == "/v2/resolutions":
        return json.dumps({"data": []}).encode()
    raise AssertionError(url)
client = pd.PolymarketData(fetch, sleep=lambda s: None, clock=lambda: 0.0)
start = cl.rss_mb()
seen = [start]
def mem():
    m = cl.rss_mb()
    seen.append(m)
    return m
cl.pull(store, client, phase=1, say=lambda m: None, memory=mem)
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (2 ** 20 if sys.platform == "darwin" else 2 ** 10)
print(json.dumps({"start": start, "max_sampled": max(seen), "peak": peak, "samples": len(seen)}))
"""


def test_a_3000_page_wallet_is_pulled_with_its_peak_memory_growth_under_200_mb(tmp_path):
    script = tmp_path / "mem.py"
    script.write_text(MEM_CHILD)
    store = tmp_path / "big.sqlite"
    r = _run_child(script, store, 3000, 100, timeout=1800)
    assert r.returncode == 0, r.stderr[-2000:]
    m = json.loads(r.stdout.strip().splitlines()[-1])
    assert m["samples"] >= 3000                       # read after every page
    assert m["max_sampled"] - m["start"] < 200, m
    assert m["peak"] - m["start"] < 200, m
    db = sqlite3.connect(str(store))
    assert db.execute("SELECT status, pages, rows FROM walks WHERE kind='trades'").fetchone() == ("finished", 3000,
                                                                                                    300_000)
    assert db.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 300_000
    db.close()
    store.unlink()


def test_the_stored_shape_is_unchanged():
    import hashlib
    assert hashlib.sha256(cl.SCHEMA.encode()).hexdigest() == SCHEMA_SHA_27CDE89


def _limit_world(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "_now", lambda: FIXED)
    w = World(11, edge=0.3, move=0.0, pros=5, per_month=11, months=SMALL_MONTHS)     # pros good enough to lead
    store = tmp_path / "s.sqlite"
    said = []
    out = cl.pull(store, _paged_client(PagedWorld(w, 11, size=50)), phase=1, say=said.append, page_cap=10)
    return w, store, said, out


def test_a_wallet_at_the_page_limit_ends_with_its_own_status_and_the_pull_goes_on(tmp_path, monkeypatch):
    w, store, said, out = _limit_world(tmp_path, monkeypatch)
    db = sqlite3.connect(str(store))
    st = dict(((k, (s, p)) for k, s, p in db.execute("SELECT key, status, pages FROM walks WHERE kind='trades'")))
    rows = dict(db.execute("SELECT wallet, COUNT(*) FROM trades GROUP BY wallet"))
    rej = dict(db.execute("SELECT key, COUNT(*) FROM rejects WHERE kind='trades' GROUP BY key"))
    db.close()
    assert st["mm0"] == ("page_limit", 10) and st["mm1"] == ("page_limit", 10)
    assert all(st[p] == ("finished", 2) for p in ("pro00", "pro01", "pro02", "pro03", "pro04")), st
    assert "mm0" not in rows and "mm1" not in rows and "mm0" not in rej and "mm1" not in rej
    assert out["page_limit"] == ["mm0", "mm1"]
    summary = [m for m in said if "page limit" in m and m.startswith("phase 1 ended")]
    assert summary and "mm0 (10 pages)" in summary[-1] and "mm1 (10 pages)" in summary[-1]
    svc = PagedWorld(w, 11, size=50)
    cl.pull(store, _paged_client(svc), phase=1, say=lambda m: None, page_cap=10)
    assert svc.urls == []                             # finished and page-limit walks are not read again
    with pytest.raises(cl.Refused, match=r"--leave-out mm0 mm1"):
        cl.pull(store, _paged_client(PagedWorld(w, 11, size=50)), phase=2, say=lambda m: None)
    plan = cl.pull(store, _paged_client(PagedWorld(w, 11, size=50)), phase=2, leave_out=["mm0", "mm1"],
                   say=lambda m: None)
    with cl.Store(store) as s:
        assert s.plan() and not [tg for tg in s.plan().values() if {"mm0", "mm1"} & set(tg.wallets)]
        assert s.meta("plan")["leave_out"] == ["mm0", "mm1"]


def test_develop_and_sealed_refuse_with_the_exact_leave_out_list_and_with_it_name_the_wallets(tmp_path, monkeypatch):
    w, store, said, out = _limit_world(tmp_path, monkeypatch)
    cl.pull(store, _paged_client(PagedWorld(w, 11, size=50)), phase=2, leave_out=["mm0", "mm1"], say=lambda m: None)
    for lo in ([], ["mm1"]):
        with pytest.raises(cl.Refused) as e:
            cl.run_develop(store=store, out_dir=tmp_path / "o", leave_out=lo)
        assert "--leave-out mm0 mm1" in str(e.value) and "page limit" in str(e.value), str(e.value)
    doc = cl.run_develop(store=store, out_dir=tmp_path / "o", leave_out=["mm0", "mm1"])
    assert doc["result"]["wallets_left_out"] == ["mm0", "mm1"]
    assert doc["result"]["unfinished_left_out"] == ["mm0", "mm1"]
    assert "mm0" in cl.format_table(doc)
    for lo in ([], ["mm1"]):                          # the development file exists: the bookkeeping refuses
        with pytest.raises(cl.Refused) as e:
            cl.run_sealed(store=store, out_dir=tmp_path / "o", flag=True, leave_out=lo)
        assert "--leave-out mm0 mm1" in str(e.value) and "page limit" in str(e.value), str(e.value)
    assert cl.store_sealed_marker(store) is None


def test_an_error_mid_walk_leaves_no_row_of_it_and_the_next_wallet_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "_now", lambda: FIXED)
    w = _small_world(5)
    store = tmp_path / "s.sqlite"
    with pytest.raises(cl.PullIncomplete, match="pro01"):
        cl.pull(store, _paged_client(PagedWorld(w, 5, size=20, fail=("pro01", 3))), phase=1, say=lambda m: None)
    db = sqlite3.connect(str(store))
    assert db.execute("SELECT status FROM walks WHERE kind='trades' AND key='pro01'").fetchone() == ("started",)
    assert db.execute("SELECT COUNT(*) FROM trades WHERE wallet='pro01'").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM rejects WHERE kind='trades' AND key='pro01'").fetchone()[0] == 0
    assert db.execute("SELECT status FROM walks WHERE kind='trades' AND key='pro02'").fetchone() == ("finished",)
    db.close()


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_the_pull_stops_cleanly_above_its_memory_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "_now", lambda: FIXED)
    w = _small_world(5)
    store = tmp_path / "s.sqlite"
    svc = PagedWorld(w, 5, size=20)

    def memory():                                     # 2,000 MB once pro01's third page is in
        return 2000.0 if sum("user=pro01" in u for u in svc.urls) >= 3 else 100.0
    said = []
    with pytest.raises(cl.PullStopped, match="1500"):
        cl.pull(store, _paged_client(svc), phase=1, say=said.append, memory=memory)
    db = sqlite3.connect(str(store))
    st = dict(db.execute("SELECT key, status FROM walks WHERE kind='trades'"))
    assert st["pro01"] == "started" and st["pro00"] == "finished" and "pro02" not in st
    assert db.execute("SELECT COUNT(*) FROM trades WHERE wallet='pro01'").fetchone()[0] == 0
    db.close()
    assert sum("user=pro01" in u for u in svc.urls) == 3            # it stopped after the page that crossed
    assert any("stopped" in m and "2000" in m and "1500" in m for m in said), said
    assert any(m.startswith("phase 1 ended") and "pro01" in m for m in said), said
    cl.check_own_shape(store)


def test_the_command_line_takes_a_memory_limit_and_a_stop_is_a_non_zero_exit(monkeypatch, capsys):
    got = {}

    def fake_pull(store, client, phase=1, leave_out=(), say=None, **kw):
        got.update(kw, phase=phase)
        if kw.get("max_memory_mb") == 800:
            raise cl.PullStopped("stopped: memory 900 MB above the limit of 800 MB")
        return {"requests": 0}
    monkeypatch.setattr(cl, "pull", fake_pull)
    monkeypatch.setattr(cl.pd, "PolymarketData", lambda *a, **k: None)
    assert cl.main(["pull", "--phase", "1", "--store", "x.sqlite"]) == 0 and got["max_memory_mb"] == 1500
    assert cl.main(["pull", "--phase", "1", "--store", "x.sqlite", "--max-memory-mb", "800"]) == 4
    assert "STOPPED" in capsys.readouterr().err


def test_rss_mb_reads_the_process_resident_memory_now():
    a = cl.rss_mb()
    block = bytearray(150 * 2 ** 20)
    for i in range(0, len(block), 4096):
        block[i] = 1
    b = cl.rss_mb()
    del block
    assert 100 < b - a < 400, (a, b)


LOG_CHILD = r"""
import sys
sys.path.insert(0, sys.argv[1])
import test_copy_leaders as T
from valedia_trader.research_loop import copy_leaders as cl
from valedia_trader.venues import polymarket_data as pd
log, seen = sys.argv[2], sys.argv[3]
w = T._small_world(2)
w.trades["pro00"] = w.trades["pro00"] * 1 + [dict(r, transaction_hash="0xr%d" % i) for i, r in
                                              enumerate(w.trades["pro00"] * 3)]
svc = T.PagedWorld(w, 2, size=1)
inner = svc.__call__
def watch(url):
    if "user=pro00" in url and "cursor=205&" in url + "&":
        with open(log) as f, open(seen, "w") as g:
            g.write(f.read())
    return inner(url)
cl.pull(sys.argv[4], T._paged_client(watch), phase=1)
"""


def test_every_line_is_flushed_at_once_and_says_where_the_pull_is(tmp_path):
    script = tmp_path / "log.py"
    script.write_text(LOG_CHILD)
    log, seen, store = tmp_path / "pull.log", tmp_path / "seen.log", tmp_path / "s.sqlite"
    root = str(Path(cl.__file__).resolve().parents[2])
    env = dict(os.environ, PYTHONPATH=root, PYTHONDONTWRITEBYTECODE="1")
    with open(log, "w") as out:
        r = subprocess.run([sys.executable, "-B", str(script), str(Path(__file__).resolve().parent), str(log),
                            str(seen), str(store)], env=env, stdout=out, stderr=subprocess.PIPE, text=True,
                           timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    mid = seen.read_text()                            # what the log file held while pro00's 206th page was asked
    assert "phase 1: 7 candidate wallets" in mid and "not known in advance" in mid
    assert "trades mm0 finished:" in mid
    running = [l for l in mid.splitlines() if l.startswith("trades pro00: ")]
    assert [l.split(":")[1].split(" pages")[0].strip() for l in running] == ["100", "200"]
    assert all("rows" in l and " s," in l and "MB" in l for l in running), running
    text = log.read_text()
    done = [l for l in text.splitlines() if l.startswith("trades pro00 finished:")]
    assert len(done) == 1 and "pages" in done[0] and "rows" in done[0] and "rejects" in done[0] and " s" in done[0]
    end = [l for l in text.splitlines() if l.startswith("phase 1 ended")]
    assert end and "7 of 7 wallets finished" in end[-1] and "unfinished: none" in end[-1]


def test_the_phase_2_estimate_uses_the_pace_measured_in_phase_1(tmp_path, monkeypatch):
    import math
    monkeypatch.setattr(cl, "_now", lambda: FIXED)
    w = _small_world(4)
    store = tmp_path / "s.sqlite"
    clock = _Clock()
    svc = PagedWorld(w, 4, size=20, clock=clock, step=1.5)            # every request takes 1.5 s
    cl.pull(store, _paged_client(svc), phase=1, say=lambda m: None, clock=clock)
    with cl.Store(store) as s:
        pace = s.meta("pull_pace")
    assert pace["requests"] == len(svc.urls) and pace["seconds"] == 1.5 * len(svc.urls)
    said = []
    cl.pull(store, _paged_client(PagedWorld(w, 4, size=20)), phase=2, say=said.append, clock=_Clock())
    line = [m for m in said if m.startswith("phase 2:") and "price windows" in m][0]
    with cl.Store(store) as s:
        n = len([k for k, wd in s.plan_windows().items() if not wd.horizon.startswith("delay_10")])
    assert "%d price windows" % n in line and "1.50 seconds a request" in line, line
    assert "at least %s" % cl._seconds(math.ceil(1.5 * n)) in line, line
    assert "three a second" not in " ".join(said)


def _big_fetch(pages, per, kill=None, title=200):
    """A stand-in service of one wallet, "0xbig", `pages` pages of `per` trades each; `kill`: the page at which the
    process ends at once (os._exit), as a kill."""
    def fetch(url):
        p = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(p.query))
        if p.path == "/v2/leaderboard":
            u = ["0xbig"] if q["time_period"] == "all" else []
            return json.dumps({"data": [{"rank": 1, "user_id": x, "pnl": 1.0, "volume": 1.0} for x in u],
                               "pagination": {"has_more": False, "next_cursor": None}}).encode()
        if p.path == "/v2/trades":
            off = int(q.get("cursor", "0"))
            if kill is not None and off // per + 1 == kill:
                os._exit(9)
            rows = [{"proxy_wallet": "0xbig", "side": "BUY", "token_id": "t%d" % i, "condition_id": "c%d" % (i % 50),
                     "size": 10.0, "price": 0.5, "timestamp": 1_780_000_000 - i, "event_slug": "e",
                     "outcome_index": 0, "transaction_hash": "0x%x" % i, "title": "x" * title}
                    for i in range(off, off + per)]
            more = off + per < pages * per
            return json.dumps({"data": rows, "pagination": {"has_more": more,
                                                            "next_cursor": str(off + per) if more else None}}).encode()
        return json.dumps({"data": []}).encode()
    return fetch


BIG_KILL_CHILD = r"""
import sys
sys.path.insert(0, sys.argv[1])
import test_copy_leaders as T
from valedia_trader.research_loop import copy_leaders as cl
from valedia_trader.venues import polymarket_data as pd
fetch = T._big_fetch(40, 1000, kill=30)
cl.pull(sys.argv[2], pd.PolymarketData(fetch, sleep=lambda s: None, clock=lambda: 0.0), phase=1, say=lambda m: None)
"""


def test_a_kill_after_the_walk_spilled_to_the_file_leaves_no_readable_row_and_the_next_pull_undoes_it(tmp_path):
    script = tmp_path / "big.py"
    script.write_text(BIG_KILL_CHILD)
    store = tmp_path / "s.sqlite"
    r = _run_child(script, Path(__file__).resolve().parent, store)
    assert r.returncode == 9, r.stderr[-1500:]
    journal = Path(str(store) + "-journal")
    assert journal.exists() and store.stat().st_size > 5 * 2 ** 20      # 29 pages written into the file, uncommitted
    for run in (lambda: cl.run_develop(store=store, out_dir=tmp_path / "o"),
                lambda: cl.run_sealed(store=store, out_dir=tmp_path / "o", flag=True)):
        with pytest.raises(cl.Refused, match="left unfinished by a stopped pull"):
            run()
    assert journal.exists() and not (tmp_path / "o").exists()           # refused read-only: nothing undone or read
    cl.pull(store, pd.PolymarketData(_big_fetch(40, 1000), sleep=lambda s: None, clock=lambda: 0.0), phase=1,
            say=lambda m: None)
    cl.check_own_shape(store)
    db = sqlite3.connect(str(store))
    assert db.execute("SELECT status, pages, rows FROM walks WHERE kind='trades'").fetchone() == ("finished", 40, 40_000)
    assert db.execute("SELECT MIN(seq), MAX(seq), COUNT(*) FROM trades").fetchone() == (0, 39_999, 40_000)
    db.close()


def test_the_identities_on_disk_behave_as_the_whole_walks_dict_one_and_one_point_zero_alike(tmp_path):
    with cl.Store(tmp_path / "s.sqlite", make_id=True) as s:
        ids, ref = s.seen(), {}
        for k, sig in ((("0xa", "h", 10, 0.5, 1_780_000_000, 0), "first"), (("0xa", "h", 10.0, 0.5, 1_780_000_000, 0.0), "x"),
                       (("0xa", "h", 11, 0.5, 1_780_000_000, 0), "y"), (("0xb", "h", 10, 0.5, 1_780_000_000, 0), "z")):
            assert (k in ids) == (k in ref), k
            if k in ref:
                assert (ids[k] == sig) == (ref[k] == sig) and (ids[k] == "first") == (ref[k] == "first")
            else:
                ids[k], ref[k] = sig, sig
        assert len(ref) == 3
        s.db.rollback()


# ------------------------------------------------------------------ addendum 7
def test_identities_are_exact_as_the_old_dict_keys_were(tmp_path):
    """The old way kept identities as dict keys: Python's ==. 2**53 and 2**53 + 1 differ (exact ints); 2**53 and
    2.0**53 are one key, as are 2**63 and 2.0**63; 0, 0.0 and -0.0 are one key; 1, 1.0 and True are one key; a
    number is never a string."""
    keys = [2 ** 53, 2 ** 53 + 1, float(2 ** 53), 2 ** 63, float(2 ** 63), 2 ** 63 + 1, 0, 0.0, -0.0, 1, 1.0, True,
            False, "1", 0.1, 0.1 + 2 ** -56, 1e300, -2 ** 53 - 1]
    old = {}
    for i, v in enumerate(keys):
        old.setdefault(("0xa", v), i)
    assert old[("0xa", 2 ** 53)] != old[("0xa", 2 ** 53 + 1)] and old[("0xa", float(2 ** 53))] == old[("0xa", 2 ** 53)]
    assert old[("0xa", 2 ** 63)] == old[("0xa", float(2 ** 63))] != old[("0xa", 2 ** 63 + 1)]
    assert old[("0xa", 0)] == old[("0xa", -0.0)] == old[("0xa", False)] and old[("0xa", 1)] == old[("0xa", True)]
    with cl.Store(tmp_path / "s.sqlite", make_id=True) as s:
        ids = s.seen()
        for i, v in enumerate(keys):
            k = ("0xa", v)
            if k not in ids:
                ids[k] = "sig%d" % i
        for i, v in enumerate(keys):            # the first key of each old class holds, every other key finds it
            assert ids[("0xa", v)] == "sig%d" % old[("0xa", v)], (i, v)
            assert not (ids[("0xa", v)] == "sig%d" % (old[("0xa", v)] + 100))
        n = s.db.execute("SELECT COUNT(*) FROM temp.walk_seen").fetchone()[0]
        assert n == len(set(old.values()))
        s.db.rollback()


class EndlessWindow(PagedWorld):
    """A PagedWorld whose one named price window never ends (more pages always promised)."""

    def __init__(self, *a, endless=None, **k):
        super().__init__(*a, **k)
        self.endless = endless

    def __call__(self, url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        if "/v2/prices-history" in url and (q.get("token_id"), int(q.get("start", 0))) == self.endless:
            self.urls.append(url)
            n = int(q.get("cursor", "0"))
            return json.dumps({"data": [{"price": 0.5, "resolution_seconds": 300, "timestamp": self.endless[1] + n}],
                               "pagination": {"has_more": True, "next_cursor": str(n + 1)}}).encode()
        return super().__call__(url)


def _phase1_store(tmp_path, monkeypatch, seed=11):
    monkeypatch.setattr(cl, "_now", lambda: FIXED)
    w = World(seed, edge=0.3, move=0.0, pros=5, per_month=11, months=SMALL_MONTHS)
    store = tmp_path / "s.sqlite"
    cl.pull(store, _paged_client(PagedWorld(w, seed, size=50)), phase=1, say=lambda m: None)
    return w, store


def test_a_price_window_past_its_page_limit_stays_unfinished_and_the_pull_is_incomplete(tmp_path, monkeypatch):
    w, store = _phase1_store(tmp_path, monkeypatch)
    with cl.Store(store) as s:
        targets = cl.plan_store(s)
        s.commit()
        first = sorted(cl.base_windows(targets).items())[0]
    key, wd = first
    said = []
    svc = EndlessWindow(w, 11, size=50, endless=(wd.token, wd.start))
    with pytest.raises(cl.PullIncomplete):
        cl.pull(store, _paged_client(svc), phase=2, say=said.append)
    db = sqlite3.connect(str(store))
    assert db.execute("SELECT status FROM walks WHERE kind='prices' AND key=?", (key,)).fetchone() == ("started",)
    assert db.execute("SELECT COUNT(*) FROM price_points WHERE walk_key=?", (key,)).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM walks WHERE status='page_limit'").fetchone()[0] == 0
    db.close()
    with cl.Store(store) as s:
        assert not s.meta("plan").get("ten_minute_windows_added")          # the 10-minute step waits
    text = " ".join(said)
    assert "--leave-out" not in text and "page limit" not in text
    assert "unfinished: 1 price windows" in said[-1], said[-1]
    monkeypatch.setattr(cl, "pull", lambda *a, **k: (_ for _ in ()).throw(cl.PullIncomplete("x")))
    monkeypatch.setattr(cl.pd, "PolymarketData", lambda *a, **k: None)
    assert cl.main(["pull", "--phase", "2", "--store", str(store)]) == 3                # the old exit code


def test_a_resolutions_batch_answered_as_a_paged_list_stays_unfinished_and_the_pull_is_incomplete(tmp_path,
                                                                                                  monkeypatch):
    monkeypatch.setattr(cl, "_now", lambda: FIXED)
    w = _small_world(6)
    svc = PagedWorld(w, 6, size=50)
    inner = svc.__call__
    first = sorted(w.res)[0]

    def paged(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        if "/v2/resolutions" in url and first in q["condition"].split(","):
            return json.dumps({"data": [], "pagination": {"has_more": True, "next_cursor": "x"}}).encode()
        return inner(url)
    store = tmp_path / "s.sqlite"
    said = []
    with pytest.raises(cl.PullIncomplete, match="resolutions"):
        cl.pull(store, _paged_client(paged), phase=1, say=said.append)
    with cl.Store(store) as s:
        assert first in s.unfinished("resolutions") and first not in s.resolution_answers()
        assert len(s.unfinished("resolutions")) == 20                   # one batch, the others read
        assert s.db.execute("SELECT COUNT(*) FROM walks WHERE status='page_limit'").fetchone()[0] == 0
    assert "unfinished: 20 resolution walks" in said[-1], said[-1]


LOCK_CHILD = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
import test_copy_leaders as T
from valedia_trader.research_loop import copy_leaders as cl
store, ready, go = sys.argv[2], sys.argv[3], sys.argv[4]
cl._now = lambda: T.FIXED
w = T._small_world(5)
svc = T.PagedWorld(w, 5, size=50)
inner = svc.__call__
def held(url):
    if "/v2/trades" in url and "user=pro02&" in url and "cursor" not in url:
        open(ready, "w").close()
        t = time.time()
        while not os.path.exists(go) and time.time() - t < 300:
            time.sleep(0.01)
    return inner(url)
cl.pull(store, T._paged_client(held), phase=1, say=lambda m: None)
print("PULLED")
"""


def _start_locked_pull(tmp_path, store):
    script = tmp_path / "lock.py"
    script.write_text(LOCK_CHILD)
    ready, go = tmp_path / "ready", tmp_path / "go"
    root = str(Path(cl.__file__).resolve().parents[2])
    env = dict(os.environ, PYTHONPATH=root, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.Popen([sys.executable, "-B", str(script), str(Path(__file__).resolve().parent), str(store),
                          str(ready), str(go)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    import time as _t
    t = _t.time()
    while not ready.exists():
        assert p.poll() is None, p.communicate()
        assert _t.time() - t < 300
        _t.sleep(0.02)
    return p, go


def test_while_a_pull_runs_pull_develop_and_sealed_refuse_in_words_at_once(tmp_path, monkeypatch):
    import time as _t
    store = tmp_path / "s.sqlite"
    p, go = _start_locked_pull(tmp_path, store)
    try:
        for run in (lambda: cl.run_develop(store=store, out_dir=tmp_path / "o"),
                    lambda: cl.run_sealed(store=store, out_dir=tmp_path / "o", flag=True),
                    lambda: cl.pull(store, _paged_client(PagedWorld(_small_world(5), 5)), phase=1, say=lambda m: None)):
            t = _t.time()
            with pytest.raises(cl.Refused, match="a pull is running on this store"):
                run()
            assert _t.time() - t < 5                                      # without waiting
        assert not (tmp_path / "o").exists()
    finally:
        go.touch()
        out, err = p.communicate(timeout=600)
    assert p.returncode == 0 and "PULLED" in out, err[-1500:]
    try:                                                              # the lock is gone: develop runs (or refuses
        cl.run_develop(store=store, out_dir=tmp_path / "o")           # for its own reasons, never the lock)
    except cl.Refused as e:
        assert "a pull is running" not in str(e)


def test_a_killed_pull_leaves_no_lock_held(tmp_path, monkeypatch):
    import signal
    store = tmp_path / "s.sqlite"
    p, go = _start_locked_pull(tmp_path, store)
    os.kill(p.pid, signal.SIGKILL)
    p.communicate(timeout=60)
    monkeypatch.setattr(cl, "_now", lambda: FIXED)
    cl.pull(store, _paged_client(PagedWorld(_small_world(5), 5, size=50)), phase=1, say=lambda m: None)
    with cl.Store(store) as s:
        assert not s.unfinished("trades")


def test_the_pull_says_never_to_copy_the_store_while_it_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "_now", lambda: FIXED)
    said = []
    cl.pull(tmp_path / "s.sqlite", _paged_client(PagedWorld(_small_world(5), 5, size=50)), phase=1, say=said.append)
    assert "never copy the store file while a pull runs" in said[0] and "SQLite's own backup" in said[0]
    doc = " ".join(cl.__doc__.split())
    assert "never copy the store file while a pull runs" in doc and "SQLite's own backup" in doc


def test_develop_and_sealed_refuse_a_store_that_fails_its_integrity_check(tmp_path):
    store = _cheap(tmp_path)
    db = sqlite3.connect(str(store))
    root = db.execute("SELECT rootpage FROM sqlite_master WHERE name='trades'").fetchone()[0]
    size = db.execute("PRAGMA page_size").fetchone()[0]
    db.close()
    assert root > 1
    with open(store, "r+b") as f:                     # a page of the trades table made garbage
        f.seek((root - 1) * size + 100)
        f.write(b"\xff" * 200)
    before = _sha(store)
    for run in (lambda: cl.run_develop(store=store, out_dir=tmp_path / "o"),
                lambda: cl.run_sealed(store=store, out_dir=tmp_path / "o", flag=True)):
        with pytest.raises(cl.Refused, match="quick_check"):
            run()
    assert _sha(store) == before and not (tmp_path / "o").exists()


_POSITION = re.compile(r"\b(?:tok|mmt)\d+\b")


def test_phase_2_prints_a_counter_only_never_a_position(tmp_path, monkeypatch):
    w, store = _phase1_store(tmp_path, monkeypatch)
    monkeypatch.setattr(cl, "PRICE_COUNTER_EVERY", 5)
    said = []
    cl.pull(store, _paged_client(PagedWorld(w, 11, size=50)), phase=2, say=said.append)
    with cl.Store(store) as s:
        windows = s.plan_windows()
    assert len(windows) > 10
    starts = {str(wd.start) for wd in windows.values()} | {str(wd.start - cl.DELAY) for wd in windows.values()}
    for m in said:
        assert not _POSITION.search(m), m
        assert not any(t in m for t in starts), m
    counters = [m for m in said if re.match(r"phase 2: \d+ of \d+ price windows finished, [\d,]+ points, \d+ s, "
                                            r"memory \d+ MB$", m)]
    finished = [int(m.split()[2]) for m in counters]
    assert finished[0] == 5 and all(b > a for a, b in zip(finished, finished[1:]))
    assert sum(m.startswith("phase 2: ") and "price windows finished" in m for m in said) == len(counters)
    assert all(m.startswith(("phase 2: ", "phase 2 ended: ")) for m in said), [m for m in said if "phase 2" not in m]


def test_a_failed_price_window_is_named_by_its_number_only(tmp_path, monkeypatch):
    w, store = _phase1_store(tmp_path, monkeypatch)
    svc = PagedWorld(w, 11, size=50)
    inner = svc.__call__
    asked = []

    def failing(url):
        if "/v2/prices-history" in url:
            asked.append(url)
            if len(asked) == 3:
                raise pd.HttpError(400, url)
        return inner(url)
    said = []
    with pytest.raises(cl.PullIncomplete) as e:
        cl.pull(store, _paged_client(failing), phase=2, say=said.append)
    for m in said + [str(e.value)]:
        assert not _POSITION.search(m) and "prices-history" not in m, m
    assert "price window 3 of" in str(e.value)


TMP_CHILD_CHECK = r"""
import os, sqlite3, subprocess
"""


def test_sqlites_temporary_files_live_beside_the_store_and_are_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "_now", lambda: FIXED)
    store = tmp_path / "s.sqlite"
    folder = tmp_path / "s.sqlite.pull-tmp"
    folder.mkdir()
    (folder / "etilqs_leftover").write_text("from a killed pull")       # a leftover from a kill
    seen = {}
    svc = PagedWorld(_small_world(5), 5, size=20)
    inner = svc.__call__

    def watch(url):
        if "/v2/trades" in url and "cursor=40" in url and "where" not in seen:
            db = sqlite3.connect(":memory:")
            seen["where"] = db.execute("PRAGMA temp_store_directory").fetchone()
            db.close()
            seen["exists"] = folder.is_dir()
            seen["leftover"] = (folder / "etilqs_leftover").exists()
        return inner(url)
    cl.pull(store, _paged_client(watch), phase=1, say=lambda m: None)
    assert seen == {"where": (str(folder.resolve()),), "exists": True, "leftover": False}
    assert not folder.exists()
    db = sqlite3.connect(":memory:")
    assert db.execute("PRAGMA temp_store_directory").fetchone() in (None, ("",))
    db.close()


def test_the_end_of_walk_numbering_is_done_in_bounded_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "_now", lambda: FIXED)
    monkeypatch.setattr(cl, "RENUMBER_CHUNK", 7)
    for seed in (0, 1, 2):
        w = _small_world(seed)
        old, new = tmp_path / ("old%d.sqlite" % seed), tmp_path / ("new%d.sqlite" % seed)
        oc, nc = _paged_client(PagedWorld(w, seed)), _paged_client(PagedWorld(w, seed))
        _old_pull(old, oc, 1)
        cl.pull(new, nc, phase=1, say=lambda m: None)
        _old_pull(old, oc, 2)
        cl.pull(new, nc, phase=2, say=lambda m: None)
        a, b = _tables(old), _tables(new)
        for name in a:
            assert a[name] == b[name], (seed, name)
    statements = []
    real = sqlite3.connect

    def traced(*a, **k):
        db = real(*a, **k)
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(cl.sqlite3, "connect", traced)
    store = tmp_path / "big.sqlite"
    cl.pull(store, pd.PolymarketData(_big_fetch(5, 10), sleep=lambda s: None, clock=lambda: 0.0), phase=1,
            say=lambda m: None)
    renumber = [st for st in statements if st.startswith("UPDATE trades SET seq")]
    assert len(renumber) == -(-50 // 7)                # 50 rows, 7 a statement
    db = real(str(store))
    assert db.execute("SELECT MIN(seq), MAX(seq), COUNT(*) FROM trades").fetchone() == (0, 49, 50)
    db.close()
    monkeypatch.setattr(cl, "RENUMBER_CHUNK", 2)       # price windows (at most 4 points here): 2 points a statement
    again = tmp_path / "again0.sqlite"
    c0 = _paged_client(PagedWorld(_small_world(0), 0))
    cl.pull(again, c0, phase=1, say=lambda m: None)
    statements.clear()
    cl.pull(again, c0, phase=2, say=lambda m: None)
    assert _tables(again) == _tables(tmp_path / "old0.sqlite")
    db = real(str(again))
    rows = [r for (r,) in db.execute("SELECT rows FROM walks WHERE kind='prices' AND status='finished'")]
    db.close()
    assert max(rows) >= 3
    assert len([st for st in statements if st.startswith("UPDATE price_points SET seq")]) == sum(r // 2 + 1
                                                                                               for r in rows)


# ------------------------------------------------------------------ T47c: the reads wallet by wallet
BEFORE_T47C = "a49faab"                                # the code as it was at the start of T47c: the reference


def _code_before(tmp_path):
    """copy_leaders.py as it was at BEFORE_T47C, loaded as its own module (its own file, so its own code hash)."""
    import types
    root = Path(cl.__file__).resolve().parents[2]
    src = subprocess.run(["git", "-C", str(root), "show", "%s:valedia_trader/research_loop/copy_leaders.py"
                          % BEFORE_T47C], capture_output=True, text=True, check=True).stdout
    path = tmp_path / "copy_leaders_before.py"
    path.write_text(src)
    name = "copy_leaders_before_t47c"
    mod = types.ModuleType(name)
    mod.__file__ = str(path)
    sys.modules[name] = mod
    exec(compile(src, str(path), "exec"), mod.__dict__)
    return mod


def _reads_of(fn):
    """(every trade row the reads turn into trades, every price point built) while `fn` runs."""
    rows, points = [], []
    real_t, real_p = cl.trade_from_row, cl.Point

    def t(wallet, seq, row):
        rows.append(dict(row))
        return real_t(wallet, seq, row)

    class P(real_p):
        def __new__(cls, *a):
            p = real_p.__new__(cls, *a)
            points.append(real_p(*a))
            return p
    cl.trade_from_row, cl.Point = t, P
    try:
        fn()
    finally:
        cl.trade_from_row, cl.Point = real_t, real_p
    return rows, points


def _variety(seed):
    rng = random.Random(4700 + seed)
    return dict(edge=rng.choice([0.0, 0.1, 0.3, 0.3]), move=rng.choice([0.0, 0.02]), pros=rng.randrange(3, 8),
                per_month=rng.choice([6, 9, 11, 14, 20]), market_makers=rng.randrange(0, 3),
                event_every=rng.choice([2, 2, 30]), months=SMALL_MONTHS, odd=rng.choice([0.0, 0.2, 0.4]))


def _outcome(fn):
    try:
        return "ok", fn()
    except Exception as e:                            # either module's own Refused
        if type(e).__name__ != "Refused":
            raise
        return "refused", str(e).split("; the lock file ")[0]          # the note addendum 8 added


def test_t47c_planning_develop_and_sealed_equal_the_code_before_on_40_seeded_stores(tmp_path, monkeypatch):
    old = _code_before(tmp_path)
    monkeypatch.setattr(cl, "_now", lambda: FIXED)
    monkeypatch.setattr(old, "_now", lambda: FIXED)
    seen = Counter()
    for seed in range(40):
        w = World(seed, **_variety(seed))
        leave = ["pro01"] if seed % 5 == 3 else []
        d = tmp_path / ("s%02d" % seed)
        d.mkdir()
        store = d / "s.sqlite"
        client = _paged_client(PagedWorld(w, seed))
        cl.pull(store, client, phase=1, say=lambda m: None)
        a, b = d / "a.sqlite", d / "b.sqlite"                        # the planning step, old and new
        shutil.copy(store, a)
        shutil.copy(store, b)
        with old.Store(a) as s:
            old.plan_store(s, leave)
            s.commit()
        with cl.Store(b) as s:
            cl.plan_store(s, leave, say=lambda m: None)
            s.commit()
        ta, tb = _tables(a), _tables(b)
        assert ta == tb, seed
        seen["planned"] += len(tb["plan"])
        cl.pull(store, client, phase=2, leave_out=leave, say=lambda m: None)
        if seed % 4 == 2:                                            # a stored point of the wrong shape
            db = sqlite3.connect(str(store))
            db.execute("UPDATE price_points SET price = 'x' WHERE rowid = (SELECT MIN(rowid) FROM price_points)")
            db.commit()
            db.close()
        o_old, o_new = d / "o_old", d / "o_new"                      # develop then sealed, old and new, each on
        c_old, c_new = d / "c_old.sqlite", d / "c_new.sqlite"        # its own copy (sealed spends its store)
        shutil.copy(store, c_old)
        shutil.copy(store, c_new)
        r_old = _outcome(lambda: old.run_develop(store=c_old, out_dir=o_old, leave_out=leave))
        r_new = _outcome(lambda: cl.run_develop(store=c_new, out_dir=o_new, leave_out=leave, say=lambda m: None))
        assert r_old[0] == r_new[0] and (r_old[0] == "ok" or r_old[1].replace(str(c_old), str(c_new)) == r_new[1]), \
            (seed, r_old, r_new)
        if r_old[0] != "ok":
            seen["develop_refused"] += 1
            continue
        sha_old, sha_new = r_old[1]["provenance"]["code_sha256"], r_new[1]["provenance"]["code_sha256"]
        assert sha_old != sha_new
        raw_old = (o_old / "develop.json").read_bytes().replace(sha_old.encode(), sha_new.encode())
        raw_old = raw_old.replace(str(c_old.resolve()).encode(), str(c_new.resolve()).encode())
        assert raw_old == (o_new / "develop.json").read_bytes(), seed
        res = r_new[1]["result"]
        seen["months_left_out"] += sum(res["months_left_out"].values())
        seen["entry_skips"] += sum(v for k, v in res["position_skips"].items()
                                   if k in ("first_price_outside_0.10_0.90", "under_1000_in_24h"))
        seen["bad_points"] += res["load_rejects"].get("point_bad_row", 0)
        seen["leave_out"] += len(res["wallets_left_out"])
        seen[res["step_1"]["verdict"]] += 1
        q_old = _outcome(lambda: old.run_sealed(store=c_old, out_dir=o_old, flag=True, leave_out=leave,
                                                develop_file=o_old / "develop.json"))
        q_new = _outcome(lambda: cl.run_sealed(store=c_new, out_dir=o_new, flag=True, leave_out=leave,
                                               develop_file=o_new / "develop.json", say=lambda m: None))
        assert q_old[0] == q_new[0], (seed, q_old, q_new)
        if q_old[0] != "ok":
            assert q_old[1].replace(str(c_old), str(c_new)).replace(str(o_old), str(o_new)) == q_new[1], seed
            continue
        seen["sealed"] += 1
        swap = [(sha_old, sha_new), (str(c_old.resolve()), str(c_new.resolve())),
                (str(o_old / "develop.json"), str(o_new / "develop.json"))] + [   # the development files' own hashes
            tuple((o / "develop.json.sha256").read_text().strip() for o in (o_old, o_new))]
        raw_old = (o_old / "sealed.json").read_bytes()
        for x, y in swap:
            raw_old = raw_old.replace(x.encode(), y.encode())
        assert raw_old == (o_new / "sealed.json").read_bytes(), seed
        m_old, m_new = (json.loads((o / "sealed.used").read_text()) for o in (o_old, o_new))
        for m in (m_old, m_new):
            m.pop("time")
            m.pop("store")
            m.pop("development_file")
            m.pop("development_sha256")
        assert m_old == m_new, seed
    assert seen["planned"] > 0 and seen["months_left_out"] > 0 and seen["leave_out"] > 0, seen
    assert seen["entry_skips"] > 0 and seen["bad_points"] > 0, seen
    assert seen["PROCEED"] > 0 and seen["FAIL"] > 0 and seen["sealed"] > 0, seen
    print("T47c stores:", dict(sorted(seen.items())))


class _Live:
    """Live objects of a kind: made less freed, with the peak."""

    def __init__(self):
        self.now = self.peak = self.made = 0

    def up(self):
        self.now += 1
        self.made += 1
        self.peak = max(self.peak, self.now)

    def down(self):
        self.now -= 1


def _counting(monkeypatch):
    trades, points = _Live(), _Live()

    class LiveTrade(cl.Trade):
        def __new__(cls, *a, **k):
            trades.up()
            return object.__new__(cls)

        def __del__(self):
            trades.down()

    real_p = cl.Point

    class LivePoint(real_p):
        def __new__(cls, *a):
            points.up()
            return real_p.__new__(cls, *a)

        def __del__(self):
            points.down()
    monkeypatch.setattr(cl, "Trade", LiveTrade)
    monkeypatch.setattr(cl, "Point", LivePoint)
    return trades, points


def test_no_step_holds_all_trades_of_a_wallet_or_every_price_point_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "_now", lambda: FIXED)
    w = World(5, edge=0.3, move=0.0, pros=3, per_month=300, months=SMALL_MONTHS, market_makers=1)
    biggest = max(len(rows) for rows in w.trades.values())
    assert biggest >= 2400
    out = tmp_path / "out"
    steps = {}
    for name, fn in (("planning", lambda: w.write(tmp_path / "s.sqlite")),
                     ("develop", lambda: cl.run_develop(store=tmp_path / "s.sqlite", out_dir=out)),
                     ("sealed", lambda: cl.run_sealed(store=tmp_path / "s.sqlite", out_dir=out, flag=True))):
        import gc
        gc.collect()
        trades, points = _counting(monkeypatch)
        doc = fn()
        steps[name] = (trades.made, trades.peak, points.made, points.peak)
        monkeypatch.undo()
        monkeypatch.setattr(cl, "_now", lambda: FIXED)
        if name == "develop":
            assert doc["result"]["step_1"]["verdict"] == "PROCEED"
    for name, (made, peak, pmade, ppeak) in steps.items():
        assert made >= biggest and peak < 200, (name, steps)          # never a wallet's trades at once
        if name != "planning":
            assert pmade > 1000 and ppeak < 100, (name, steps)        # never every price point at once


_SAID = re.compile(r"^(develop|sealed|sealed \(development recomputed\)|phase 2: planning): wallet (\d+) of (\d+) "
                   r"\(([^)]{1,10})\): [\d,]+ rows read, \d+ s, memory \d+ MB$")
_ANY_ID = re.compile(r"\b(?:tok|mmt|cond|mmc|ev|mmev)\d+\b|0x[0-9a-f]{6,}")


def _wallet_lines(said, prefix, wallets):
    lines = [m.replace("; " + cl.COPY_WARNING, "") for m in said if m.startswith(prefix + ": wallet")]  # 1st line
    got = [_SAID.match(m) for m in lines]
    assert all(got), lines
    assert [int(g.group(2)) for g in got] == list(range(1, len(lines) + 1))
    assert {int(g.group(3)) for g in got} == {len(lines)}
    assert [g.group(4) for g in got] == [x[:10] for x in wallets]
    return lines


def test_each_step_prints_one_flushed_line_a_wallet_naming_nothing_else(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cl, "_now", lambda: FIXED)
    w = World(7, edge=0.08, move=0.02, **SKILLED_SIZE)
    store = w.write(tmp_path / "s.sqlite", phase2=False)
    with cl.Store(store) as s:
        wallets = [x for x in s.candidates() if x in s.finished_keys("trades")]
    said = []

    def transport(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        rows = w.history(q["token_id"], int(q["start"]), int(q["end"]))
        return json.dumps({"data": rows, "pagination": {"has_more": False, "next_cursor": None}}).encode()
    cl.pull(store, _client(transport), phase=2, say=said.append)
    _wallet_lines(said, "phase 2: planning", wallets)
    dev = []
    cl.run_develop(store=store, out_dir=tmp_path / "out", say=dev.append)
    _wallet_lines(dev, "develop", wallets)
    sealed = []
    cl.run_sealed(store=store, out_dir=tmp_path / "out", flag=True, say=sealed.append)
    _wallet_lines(sealed, "sealed (development recomputed)", wallets)
    _wallet_lines(sealed, "sealed", wallets)
    for m in said + dev + sealed:
        assert not _ANY_ID.search(m), m
    capsys.readouterr()
    assert cl.main(["develop", "--store", str(store), "--out-dir", str(tmp_path / "o2")]) == 0
    err = capsys.readouterr().err.splitlines()
    assert len([m for m in err if m.startswith("develop: wallet")]) == len(wallets)


def test_develop_stops_for_memory_at_a_wallet_with_nothing_written(tmp_path, monkeypatch):
    store = _cheap(tmp_path)
    monkeypatch.setattr(cl, "rss_mb", lambda: 5000.0)
    with pytest.raises(cl.PullStopped, match="above the limit of 1000 MB"):
        cl.run_develop(store=store, out_dir=tmp_path / "o", max_memory_mb=1000, say=lambda m: None)
    assert not (tmp_path / "o").exists()
    assert cl.main(["develop", "--store", str(store), "--out-dir", str(tmp_path / "o"), "--max-memory-mb",
                    "1000"]) == 4
    assert not (tmp_path / "o").exists()
    monkeypatch.setattr(cl, "rss_mb", lambda: 100.0)
    cl.run_develop(store=store, out_dir=tmp_path / "o", max_memory_mb=1000, say=lambda m: None)
    assert (tmp_path / "o" / "develop.json").is_file()


def test_sealed_stops_for_memory_before_its_claim_and_the_period_is_not_spent(tmp_path, monkeypatch):
    store = _twin(tmp_path, "s.sqlite")
    out = tmp_path / "out"
    cl.run_develop(store=store, out_dir=out, say=lambda m: None)
    before = _sha(store)
    monkeypatch.setattr(cl, "rss_mb", lambda: 5000.0)
    with pytest.raises(cl.PullStopped, match="above the limit of 1000 MB"):
        cl.run_sealed(store=store, out_dir=out, flag=True, max_memory_mb=1000, say=lambda m: None)
    assert _no_marker(store, out) and not (out / "sealed.json").exists() and _sha(store) == before
    claimed = []
    real_claim = cl._claim

    def claim(*a):
        real_claim(*a)
        claimed.append(True)
    monkeypatch.setattr(cl, "_claim", claim)                # above the limit only once the period is claimed
    monkeypatch.setattr(cl, "rss_mb", lambda: 5000.0 if claimed else 100.0)
    said = []
    doc = cl.run_sealed(store=store, out_dir=out, flag=True, max_memory_mb=1000, say=said.append)
    assert claimed and doc["result"]["verdict"]["verdict"] in ("PASS", "FAIL") and (out / "sealed.json").is_file()
    assert any("CLAIMED" in m and "ABOVE THE LIMIT" in m for m in said)    # said, not stopped: the period is spent


def test_the_planning_step_stops_for_memory_with_no_plan_written(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "_now", lambda: FIXED)
    w = World(9, edge=0.1, move=0.0, pros=6, per_month=12, months=SMALL_MONTHS)
    store = w.write(tmp_path / "s.sqlite", phase2=False)
    said = []
    with pytest.raises(cl.PullStopped):
        cl.pull(store, _client(lambda url: pytest.fail("no request")), phase=2, say=said.append,
                max_memory_mb=1000, memory=lambda: 5000.0)
    with cl.Store(store) as s:
        assert s.db.execute("SELECT COUNT(*) FROM plan").fetchone() == (0,) and s.meta("plan") is None
        assert s.db.execute("SELECT COUNT(*) FROM plan_windows").fetchone() == (0,)
    assert any(m.startswith("stopped: resident memory") for m in said)


def test_sealed_reads_no_sealed_row_before_its_claim_on_the_new_path(tmp_path, monkeypatch):
    store = _twin(tmp_path, "s.sqlite")
    out = tmp_path / "out"
    cl.run_develop(store=store, out_dir=out, say=lambda m: None)
    claimed, early, late = [], [], []
    real_claim, real_t, real_p = cl._claim, cl.trade_from_row, cl.Point

    def claim(*a):
        real_claim(*a)
        claimed.append(True)

    def t(wallet, seq, row):
        (late if claimed else early).append(("trade", row["timestamp"]))
        return real_t(wallet, seq, row)

    class P(real_p):
        def __new__(cls, *a):
            (late if claimed else early).append(("point", a[0]))
            return real_p.__new__(cls, *a)
    monkeypatch.setattr(cl, "_claim", claim)
    monkeypatch.setattr(cl, "trade_from_row", t)
    monkeypatch.setattr(cl, "Point", P)
    cl.run_sealed(store=store, out_dir=out, flag=True, say=lambda m: None)
    assert early and max(ts for k, ts in early if k == "trade") < cl.SEALED_START + cl.SPILL
    assert max(ts for k, ts in early if k == "point") < cl.SEALED_START + cl.DAY + cl.PRICE_SPAN
    assert max(ts for k, ts in late if k == "trade") >= cl.SEALED_START + cl.SPILL   # the sealed rows, after it


def test_a_development_run_fetches_no_row_of_a_market_only_sealed_positions_touch(skilled, tmp_path, monkeypatch):
    w = World(7, edge=0.08, move=0.02, **SKILLED_SIZE)
    by_cond = {}
    for rows in w.trades.values():
        for r in rows:
            by_cond.setdefault(r["condition_id"], []).append(r["timestamp"])
    sealed_only = {c for c, ts in by_cond.items() if min(ts) >= cl.SEALED_START + cl.SPILL}
    sealed_tokens = {r["token_id"] for rows in w.trades.values() for r in rows if r["condition_id"] in sealed_only}
    assert len(sealed_only) > 100
    statements, fetched = [], Counter()
    real_connect, real_r = sqlite3.connect, cl.resolution_from_row

    def traced(*a, **k):
        db = real_connect(*a, **k)
        db.set_trace_callback(statements.append)
        return db

    def r(row):
        fetched["resolution"] += row["condition_id"] in sealed_only
        return real_r(row)
    monkeypatch.setattr(cl.sqlite3, "connect", traced)
    monkeypatch.setattr(cl, "resolution_from_row", r)
    rows, _ = _reads_of(lambda: cl.run_develop(store=skilled, out_dir=tmp_path / "o", say=lambda m: None))
    fetched["trade"] = sum(1 for x in rows if x["condition_id"] in sealed_only)
    fetched["price window"] = sum(1 for st in statements if "price_points" in st
                                  and any("'%s@" % tok in st for tok in sealed_tokens))
    assert rows and fetched == Counter({"trade": 0, "resolution": 0, "price window": 0}), fetched


def _edge_wallet(path, seed):
    """One wallet's trades at the edges the fold must match: times on a six-hour grid (ties; buys exactly 24 hours
    and trades exactly 30 days apart), resolutions at a trade's own time, a token stored with two outcome indexes,
    sells beyond the holding, markets resolved some other way or not at all, rows stored out of time order."""
    rng = random.Random(seed)
    base = MS(2025, 6)
    conds = ["k%d" % i for i in range(8)]
    rows = []
    slots = 400 if seed % 2 else 2400                  # dense: many buys exactly a day apart and next-day rows
    for i in range(rng.randrange(150, 400)):
        c = rng.choice(conds)
        oi = rng.randrange(2)
        tok = "%s_%d" % (c, oi) if rng.random() < 0.95 else "%s_%d" % (c, 0)
        rows.append({"proxy_wallet": "w", "side": rng.choice(["BUY", "BUY", "SELL"]), "token_id": tok,
                     "condition_id": c, "size": rng.choice([1.0, 50.0, 2500.0, 4000.0]),
                     "price": rng.choice([0.05, 0.3, 0.5, 0.7, 0.95]),
                     "timestamp": base + rng.randrange(0, slots) * 6 * HOUR, "event_slug": rng.choice(["", "e1", "e2"]),
                     "outcome_index": oi, "transaction_hash": "0x%x" % i, "slug": "s", "title": "", "outcome": "Yes"})
    times = [r["timestamp"] for r in rows]
    order = list(range(len(rows)))
    rng.shuffle(order)
    with cl.Store(path, make_id=True) as s:
        s.db.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("w", seq, r["proxy_wallet"], r["side"], r["token_id"], r["condition_id"], r["size"], r["price"],
             r["timestamp"], r["event_slug"], r["outcome_index"], r["transaction_hash"], r["slug"], r["title"],
             json.dumps(r), FIXED) for seq, r in zip(order, rows)])
        for c in conds:
            kind = rng.choice(["clean", "clean", "clean", "otherwise", "open"])
            ts = rng.choice(times) if rng.random() < 0.6 else base + rng.randrange(0, slots) * 6 * HOUR
            pay = {"clean": rng.choice([[0, 1_000_000], [1_000_000, 0]]), "otherwise": [500_000, 500_000],
                   "open": None}[kind]
            s.db.execute("INSERT INTO resolutions VALUES (?,?,?,?,?,?,?)", (
                c, "proposed" if kind == "open" else "resolved", json.dumps(pay), "x",
                None if kind == "open" else ts, "{}", FIXED))
        s.commit()
    return times


def test_the_wallet_fold_equals_wallet_book_and_new_positions_at_the_edges(tmp_path):
    seen = Counter()
    for seed in range(60):
        path = tmp_path / ("e%d.sqlite" % seed)
        times = _edge_wallet(path, seed)
        end = sorted(times)[len(times) // 2]
        with cl.Store(path) as s:
            for end_, spill, keep in ((2 ** 62, 0, None), (end, cl.SPILL, (MS(2025, 9), end))):
                counts = Counter()
                trades = []
                for r in s.trade_rows("w", end_, spill):
                    t, why = cl.trade_from_row("w", r["seq"], r)
                    if why:
                        counts["trade_" + why] += 1
                    else:
                        trades.append(t)
                res = cl._resolutions(s, {t.condition for t in trades})
                book, positions = cl.wallet_book(trades, res), cl.new_positions(trades)
                got = Counter()
                f = cl._fold(s, ["w"], end_, spill, keep, got)
                seen["spill"] += sum(1 for t in trades if t.ts >= end_)
                seen["a day apart"] += sum(1 for p in positions if any(
                    t.token == p.token and t.side == "BUY" and t.ts == p.t1 + cl.BUY_WINDOW for t in trades))
                mb = f.books["w"]
                assert got == counts and mb.counts == book.counts, seed
                months = [cl.add_months((2025, 1), k) for k in range(36)]
                for m in months:
                    assert cl.standing(mb, m) == cl.standing(book, m), (seed, m)
                assert cl.sells_beyond_summary({"w": mb}, [(m, "w") for m in months]) == \
                    cl.sells_beyond_summary({"w": book}, [(m, "w") for m in months]), seed
                passing = [p for p in positions if (keep is None or keep[0] <= p.t1 < keep[1])]
                assert f.positions["w"] == [p for p in passing if cl.position_skip(p, res) is None], seed
                if keep is not None:
                    assert f.skipped["w"] == Counter((cl.month_of(p.t1), cl.position_skip(p, res)) for p in passing
                                                     if cl.position_skip(p, res)), seed
                assert f.first_ts == min((t.ts for t in trades), default=None)
    assert seen["spill"] > 0 and seen["a day apart"] > 0, seen


# ------------------------------------------------------------------ T47 addendum 8
def test_a_stored_number_too_large_for_the_store_is_counted_and_the_pull_goes_on(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "_now", lambda: FIXED)
    w = World(11, edge=0.3, move=0.0, pros=5, per_month=11, months=SMALL_MONTHS)    # a world phase 2 reads
    svc = PagedWorld(w, 11, size=50)
    wallet = sorted(svc.feeds)[0]
    svc.feeds[wallet][1]["size"] = 2 ** 63                     # a trade
    cond = sorted(w.res)[0]
    w.res[cond]["payouts"] = [0, 2 ** 64]                      # a resolution
    inner = svc.__call__
    seen = []

    def prices(url):
        body = inner(url)
        if "/v2/prices-history" in url and not seen:           # a price point
            seen.append(url)
            d = json.loads(body)
            d["data"].append({"price": 0.5, "resolution_seconds": 2 ** 63, "timestamp": d["data"][0]["timestamp"]})
            body = json.dumps(d).encode()
        return body
    store = tmp_path / "s.sqlite"
    client = _paged_client(prices)
    cl.pull(store, client, phase=1, say=lambda m: None)
    cl.pull(store, client, phase=2, say=lambda m: None)
    with cl.Store(store) as s:
        got = dict(s.db.execute("SELECT kind, COUNT(*) FROM rejects WHERE reason='number_too_large_for_store' "
                                "GROUP BY kind"))
        assert not s.unfinished("trades") and seen
    assert got == {"trades": 1, "resolutions": 1, "prices": 1}, got


def test_a_reader_that_cannot_make_the_lock_file_refuses_in_words(tmp_path):
    d = tmp_path / "ro"
    d.mkdir()
    store = _cheap(d)
    lock = Path(str(store) + ".lock")
    if lock.exists():
        lock.unlink()
    os.chmod(d, 0o555)
    try:
        for run in (lambda: cl.run_develop(store=store, out_dir=tmp_path / "o", say=lambda m: None),
                    lambda: cl.run_sealed(store=store, out_dir=tmp_path / "o", flag=True, say=lambda m: None)):
            with pytest.raises(cl.Refused, match="cannot create or open the lock file .*whether a pull is running"):
                run()
    finally:
        os.chmod(d, 0o755)
    assert not lock.exists() and not (tmp_path / "o").exists()


def test_the_refusals_of_develop_and_sealed_say_the_lock_file_is_all_a_refused_run_may_leave(tmp_path):
    w = World(5, edge=0.0, move=0.0, pros=6, per_month=12)
    store = w.write(tmp_path / "s.sqlite", unfinished_wallet="pro03")     # an unfinished pull: both refuse
    for run in (lambda: cl.run_develop(store=store, out_dir=tmp_path / "o", say=lambda m: None),
                lambda: cl.run_sealed(store=store, out_dir=tmp_path / "o", flag=True, say=lambda m: None)):
        with pytest.raises(cl.Refused) as e:
            run()
        assert "the lock file %s.lock is the one thing a refused run may leave beside the store" % store in str(e.value)


def test_the_refusal_for_windows_not_pulled_prints_their_count_and_the_command(tmp_path):
    rec = []
    skip = _first((2026, 3), rec)
    skip.how = "unfinished"
    store = World(7, edge=0.08, move=0.02, **SKILLED_SIZE).write(tmp_path / "s.sqlite", skip_window=skip)
    assert rec
    with pytest.raises(cl.Refused) as e:
        cl.run_develop(store=store, out_dir=tmp_path / "o", say=lambda m: None)
    text = str(e.value)
    assert re.search(r"\b3 price windows not pulled \(pull them with: pull --phase 2\)", text), text   # the 3 base windows
    assert not re.search(r"tok\d+|cond\d+|@\d+|\b1[5-9]\d{8}\b", text), text


def test_after_its_claim_sealed_never_stops_for_memory_and_says_so_first(tmp_path, monkeypatch, capsys):
    doc = " ".join(cl.__doc__.split())
    assert "after the claim it never stops for memory" in doc
    with pytest.raises(SystemExit):
        cl.main(["sealed", "--help"])
    assert "after the claim it never stops for memory" in " ".join(capsys.readouterr().out.split())
    store = _twin(tmp_path, "s.sqlite")
    out = tmp_path / "out"
    cl.run_develop(store=store, out_dir=out, say=lambda m: None)
    claimed, order = [], []
    real_claim = cl._claim

    def claim(*a):
        order.append("claim")
        real_claim(*a)
        claimed.append(True)
    monkeypatch.setattr(cl, "_claim", claim)
    monkeypatch.setattr(cl, "rss_mb", lambda: 5000.0 if claimed else 321.0)
    said = []
    cl.run_sealed(store=store, out_dir=out, flag=True, max_memory_mb=1000,
                  say=lambda m: (said.append(m), order.append(m)))
    peak = [m for m in order if "the development recomputation needed" in m]
    assert peak and order.index(peak[0]) < order.index("claim") and "321 MB" in peak[0], order[-5:]
    alerts = [m for m in said if "ABOVE THE LIMIT" in m]
    assert len(alerts) == 1 and alerts[0] == alerts[0].upper() and "WALLET 1 OF" in alerts[0], alerts
