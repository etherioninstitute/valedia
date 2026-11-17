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

    def __init__(self, seed, edge, move, pros=12, per_month=30, months=ALL_MONTHS, market_makers=2, late_rich=False):
        self.rng = random.Random(seed)
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
        token, cond, event = "tok%d" % n, "cond%d" % n, "ev%d" % (n // 2)
        t1 = self._ts(ym)
        p = self.rng.randrange(20, 81) / 100
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
    data = cl.load(skilled, "development")
    assert max(t.ts for ts in data.trades.values() for t in ts) < cl.SEALED_START + cl.SPILL
    assert max(p.ts for ps in data.points.values() for p in ps) < cl.SEALED_START + cl.DAY + cl.PRICE_SPAN
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
    assert poisoned == base


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
    assert "%d" % len(base) in said[0] and "requests" in said[0]           # counted before the first request
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
