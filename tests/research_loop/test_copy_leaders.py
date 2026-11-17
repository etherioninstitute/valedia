"""The locked "copying the leaders" test, on SYNTHETIC worlds made here with a seed (no real data, no network).

Specification: docs/research/2026-11-17-copying-leaders-prediction.md (10ad062, time line 12a4454)."""
import json
import random
import sqlite3
from collections import Counter
from decimal import Decimal

import pytest

from valedia_trader.research_loop import copy_leaders as cl
from valedia_trader.venues import polymarket_data as pd

DAY = 86400
HOUR = 3600
MS = cl.month_start


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


def P(ts, price, res=0):
    return cl.PricePoint(ts, res, Decimal(repr(price)))


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
    t = T("w", 100, price=0.1, size=3.0)
    assert t.cash_micro == 300_000 and t.size_micro == 3_000_000


# ------------------------------------------------------------------ what a NEW position is
def test_first_purchase_with_no_holding_in_the_30_days_before_is_new():
    t0 = MS(2026, 3)
    tr = [T("w", t0, "a", seq=0), T("w", t0 + 600, "a", size=10.0, seq=1),
          T("w", t0 + 5 * DAY, "a", side="SELL", size=4010.0, seq=2),
          T("w", t0 + 5 * DAY + 30 * DAY, "a", seq=3),           # exactly 30 days after the last touch: not new
          T("w", t0 + 5 * DAY + 60 * DAY + 1, "a", seq=4)]       # holding again since seq 3: not new
    pos = cl.new_positions(tr)
    assert [p.seq for p in pos] == [0]


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


# ------------------------------------------------------------------ the follower's price
def test_price_59_minutes_after_is_used_and_61_minutes_after_is_not():
    t1 = MS(2026, 3)
    moment = t1 + HOUR
    pt, why = cl.price_at([P(t1 - HOUR, 0.40), P(t1 + 59 * 60, 0.45)], moment)
    assert why is None and pt.price == Decimal("0.45")
    pt, why = cl.price_at([P(t1 - HOUR, 0.40), P(t1 + 61 * 60, 0.45)], moment)
    assert why is None and pt.price == Decimal("0.4")
    pt, why = cl.price_at([P(t1 + 61 * 60, 0.45)], moment)
    assert pt is None and why == "no_price_at_or_before"


def test_an_aggregate_counts_only_once_its_whole_window_has_ended():
    t1 = MS(2026, 3)
    moment = t1 + HOUR
    pt, why = cl.price_at([P(t1 - 2 * HOUR, 0.40, 1800), P(t1 + 1800, 0.60, 1800), P(t1 + 1801, 0.7, 1800)], moment)
    assert why is None and pt.price == Decimal("0.6")


def test_six_hours_of_staleness():
    moment = MS(2026, 3) + HOUR
    pt, why = cl.price_at([P(moment - 6 * HOUR, 0.4)], moment)
    assert why is None
    pt, why = cl.price_at([P(moment - 6 * HOUR - 1, 0.4)], moment)
    assert pt is None and why == "price_stale_over_6h"


def _one(t0, follow_points, price=0.5, win_index=0, resolved=None, delay=HOUR):
    pos = cl.new_positions([T("L", t0, "a", price=price)])
    res = {"c-a": R("c-a", win_index, resolved or t0 + 10 * DAY)}
    return cl.copy_trades(pos, res, {"a": follow_points}, delay=delay)


@pytest.mark.parametrize("fp,ok", [(0.05, True), (0.95, True), (0.0499, False), (0.9501, False)])
def test_the_0_05_to_0_95_guard_on_the_followers_price(fp, ok):
    t0 = MS(2026, 3) + DAY
    trades, skips = _one(t0, [P(t0 + 10 * 60, fp)])
    assert (len(trades) == 1) == ok
    if not ok:
        assert skips == Counter({"follow_price_outside_0.05_0.95": 1})


def test_a_copied_trade_carries_event_price_and_outcome():
    t0 = MS(2026, 3) + DAY
    trades, skips = _one(t0, [P(t0 + 10 * 60, 0.524)], win_index=0)
    assert not skips and len(trades) == 1
    c = trades[0]
    assert (c.event, c.price_cents, c.win, c.leader) == ("ev", 52, True, "L")
    trades, _ = _one(t0, [P(t0 + 10 * 60, 0.524)], win_index=1)
    assert trades[0].win is False


def test_no_price_history_and_resolved_before_the_follow_are_counted():
    t0 = MS(2026, 3) + DAY
    pos = cl.new_positions([T("L", t0, "a")])
    trades, skips = cl.copy_trades(pos, {"c-a": R("c-a", 0, t0 + 10 * DAY)}, {}, delay=HOUR)
    assert not trades and skips == Counter({"no_price_history": 1})
    trades, skips = _one(t0, [P(t0, 0.5)], resolved=t0 + HOUR)
    assert not trades and skips == Counter({"resolved_before_follow": 1})


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
    assert st.profit_cents == 5000 + 500                     # 100 x (1 - 0.5) + 50 x (0.5 - 0.4)
    assert st.resolved_positions == 1
    assert st.shares_micro == (100 + 50 + 100 + 100) * 1_000_000


def test_a_resolution_exactly_at_the_start_of_the_month_is_not_yet_known():
    M = (2026, 4)
    tr = [T("w", MS(2026, 2), "x", price=0.5, size=100.0)]
    assert cl.standing(_book(tr, {"c-x": R("c-x", 0, MS(2026, 4))}), M).profit_cents == 0
    assert cl.standing(_book(tr, {"c-x": R("c-x", 0, MS(2026, 4) - 1)}), M).profit_cents == 5000


def test_selling_more_than_the_feed_shows_held_is_counted_and_earns_nothing():
    tr = [T("w", MS(2026, 2), "x", price=0.5, size=10.0, seq=0),
          T("w", MS(2026, 2) + DAY, "x", side="SELL", price=0.6, size=30.0, seq=1)]
    b = _book(tr, {})
    assert cl.standing(b, (2026, 4)).profit_cents == 100
    assert b.counts["sell_beyond_holding"] == 1


def _qualifier(profit_cents=100_000, resolved=30, shares_per_dollar=10):
    return cl.Standing(profit_cents, resolved, shares_per_dollar * profit_cents * 10_000)


def test_who_qualifies():
    assert cl.qualifies(_qualifier()) is None
    assert cl.qualifies(_qualifier(resolved=29)) == "fewer_than_30_resolved"
    assert cl.qualifies(_qualifier(profit_cents=0)) == "profit_not_above_zero"
    assert cl.qualifies(_qualifier(shares_per_dollar=20)) == "20_or_more_shares_per_dollar"
    assert cl.qualifies(cl.Standing(100_000, 30, 20 * 100_000 * 10_000 - 1)) is None
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


def test_the_event_is_the_unit():
    trades = [cl.CopiedTrade("e1", 50, True, "L", 0, "a", 0, 0, 0, 0)] * 3 + [cl.CopiedTrade("e2", 50, False, "L", 0, "b", 0, 0, 0, 0)]
    ev = cl.evaluate(trades, 3)
    assert (ev["trades"], ev["events"], ev["total_cents"]) == (4, 2, 3 * 47 - 53)


# ------------------------------------------------------------------ synthetic worlds through the store
DEV_FIRST = (2025, 9)
ALL_MONTHS = [(2025, 9), (2025, 10), (2025, 11), (2025, 12)] + [(2026, m) for m in range(1, 12)]


def _next(ym):
    y, m = ym
    return (y + 1, 1) if m == 12 else (y, m + 1)


class World:
    """Wallets, trades, resolutions and price histories made with a seed. `edge`: how much more often than the price
    says a pro's new position wins; `move`: how far the price moves toward the outcome-favoured side within the hour."""

    def __init__(self, seed, edge, move, pros=12, per_month=30, months=ALL_MONTHS, market_makers=2, late_rich=False):
        self.rng = random.Random(seed)
        self.trades: dict[str, list[dict]] = {}
        self.res: dict[str, dict] = {}
        self.prices: dict[str, list[dict]] = {}
        self.windows: list[tuple] = []
        self.n = 0
        for ym in months:
            for i in range(pros):
                for _ in range(per_month):
                    self.position("pro%02d" % i, ym, edge, move)
            for k in range(market_makers):
                self.market_maker("mm%d" % k, ym)
            if late_rich:
                self.position_batch("late", ym, rich=ym >= (2026, 3))

    def _ts(self, ym, margin=2 * DAY):
        y, m = ym
        lo, hi = MS(y, m), MS(*_next(ym))
        return self.rng.randrange(lo, hi - margin)

    def _add(self, wallet, ts, token, cond, side, price, size, event, oi):
        self.trades.setdefault(wallet, []).append(
            {"proxy_wallet": wallet, "side": side, "token_id": token, "condition_id": cond, "size": size,
             "price": price, "timestamp": ts, "event_slug": event, "outcome_index": oi,
             "transaction_hash": "0x%x" % self.n, "slug": token, "title": token, "outcome": "Yes"})

    def position(self, wallet, ym, edge, move, win=None, cash=None):
        self.n += 1
        n = self.n
        token, cond, event = "tok%d" % n, "cond%d" % n, "ev%d" % (n // 2)
        t1 = self._ts(ym)
        pc = self.rng.randrange(20, 81)
        p = pc / 100
        size = round((cash or self.rng.uniform(1100, 3000)) / p, 2)
        if win is None:
            win = self.rng.random() < p + edge
        oi = self.rng.randrange(2)
        self._add(wallet, t1, token, cond, "BUY", p, size, event, oi)
        rts = t1 + self.rng.randrange(2 * DAY, 40 * DAY)
        payouts = [0, 0]
        payouts[oi if win else 1 - oi] = 1_000_000
        self.res[cond] = {"condition_id": cond, "status": "resolved", "payouts": payouts,
                          "resolved_ts": rts, "resolved_at": "x"}
        fp = min(0.95, p + move)
        self.prices.setdefault(token, [])
        start, end = t1 - 6 * HOUR, t1 + DAY + 6 * HOUR
        self.windows.append((token, start, end, [
            {"timestamp": t1 - 2 * HOUR, "resolution_seconds": 0, "price": p},
            {"timestamp": t1 + 59 * 60, "resolution_seconds": 0, "price": round(fp, 4)},
            # the settlement point: the outcome itself, last in the walk; it must never be used as a price
            {"timestamp": t1 + 59 * 60 + 30, "resolution_seconds": 0, "price": 1.0 if win else 0.0}]))
        return t1

    def position_batch(self, wallet, ym, rich):
        for _ in range(35):
            self.position(wallet, ym, 0, 0, win=rich, cash=5000 if rich else 1100)

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

    def write(self, path, unfinished_wallet=None, unfinished_token=None):
        with cl.Store(path) as s:
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
                s.finish_resolutions(batch, pd.Walk.from_rows([self.res[c] for c in batch], "2026-11-17T09:02:00Z"))
            for token, start, end, pts in self.windows:
                key = cl.price_key(token, start, end)
                s.start_walk("prices", key)
                if token != unfinished_token:
                    s.finish_prices(key, token, start, end, pd.Walk.from_rows(pts, "2026-11-17T09:03:00Z"))
        return path


SKILLED_SIZE = {"pros": 16, "per_month": 40}          # enough trades that a 3-cent edge is seen


@pytest.fixture(scope="module")
def skilled(tmp_path_factory):
    return World(7, edge=0.08, move=0.02, **SKILLED_SIZE).write(tmp_path_factory.mktemp("w") / "skilled.sqlite")


def test_no_skill_fails_at_step_1(tmp_path):
    store = World(3, edge=0.0, move=0.0, pros=30).write(tmp_path / "s.sqlite")
    doc = cl.run_develop(store=store, out_dir=tmp_path / "out")
    s = doc["result"]["step_1"]
    assert s["verdict"] == "FAIL" and "mean_not_above_zero" in s["failed"]
    assert -7 < s["mean_cents"] < 0                     # near minus the cost of three cents
    assert s["trades"] > 500


def test_skill_with_a_small_move_passes_step_1(skilled, tmp_path):
    doc = cl.run_develop(store=skilled, out_dir=tmp_path / "out")
    r = doc["result"]
    s = r["step_1"]
    assert s["verdict"] == "PROCEED", s
    assert 1 < s["mean_cents"] < 5 and s["p_value"] <= 0.05
    assert s["seed"] == 20261117 and s["resamples"] == 2000
    # the market makers trade 50 shares a dollar: never a leader, whatever their profit
    assert all(not any(w.startswith("mm") for w in m["leaders"]) for m in r["month_table"])
    assert r["wallet_skips"]["20_or_more_shares_per_dollar"] > 0
    # every copied trade priced from the 59-minute point: the settlement point (the outcome) was never used
    assert r["position_skips"].get("follow_price_outside_0.05_0.95", 0) == 0


def test_price_absorbing_the_whole_edge_within_the_hour_fails(tmp_path):
    store = World(7, edge=0.08, move=0.08, **SKILLED_SIZE).write(tmp_path / "s.sqlite")
    s = cl.run_develop(store=store, out_dir=tmp_path / "out")["result"]["step_1"]
    assert s["verdict"] == "FAIL"


def test_development_months_and_the_information_tables(skilled, tmp_path):
    r = cl.run_develop(store=skilled, out_dir=tmp_path / "out")["result"]
    months = [m["month"] for m in r["month_table"]]
    assert months[0] == "2025-12" and months[-1] == "2026-06"          # three months of feed before the first
    assert all(not m.startswith("2026-0" + str(k)) for m in months for k in (7, 8, 9))
    info = r["information"]
    for key in ("delay_10_minutes", "delay_6_hours", "delay_24_hours", "cost_2_cents", "cost_0_cents", "sports",
                "everything_else", "first_purchase_over_10000", "leaders_own_prices", "follow_price_grain"):
        assert info[key]["note"] == "information: decides nothing", key
    assert info["cost_0_cents"]["mean_cents"] == pytest.approx(r["step_1"]["mean_cents"] + 3)
    assert info["leaders_own_prices"]["trades"] == r["step_1"]["trades"]


def test_develop_writes_its_file_and_hash(skilled, tmp_path):
    cl.run_develop(store=skilled, out_dir=tmp_path / "out")
    raw = (tmp_path / "out" / "develop.json").read_bytes()
    import hashlib
    assert (tmp_path / "out" / "develop.json.sha256").read_text().strip() == hashlib.sha256(raw).hexdigest()
    doc = json.loads(raw)
    assert doc["provenance"]["prediction_commit"] == "10ad062"


# ------------------------------------------------------------------ walk-forward and the period wall
def test_a_wallet_rich_only_after_month_m_is_not_a_leader_of_m(tmp_path):
    w = World(11, edge=0.1, move=0.0, pros=8, per_month=20, late_rich=True,
              months=[(2025, 12), (2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5)])
    data = cl.load(w.write(tmp_path / "a.sqlite"), "development")
    books = cl.books_of(data)
    leaders_m = cl.leaders_of(books, (2026, 3))[0]
    assert "late" not in leaders_m
    assert "late" in cl.leaders_of(books, (2026, 5))[0]          # rich in March and April: a leader of May
    # poison every trade of the late wallet from March on: the leaders of March do not move
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
    # poison everything after the spill: huge winners, flipped resolutions, absurd prices
    w = World(7, edge=0.08, move=0.02, **SKILLED_SIZE)
    for rows in w.trades.values():
        for r in rows:
            if r["timestamp"] >= cl.SEALED_START + cl.SPILL:
                r["size"] *= 77
                r["price"] = 0.11
    for c, r in w.res.items():
        if r["resolved_ts"] and r["resolved_ts"] >= cl.SEALED_START + 50 * DAY:
            r["payouts"] = list(reversed(r["payouts"]))
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
        return real_load(store, period, leave_out)

    cl.load = watching_load
    try:
        doc = cl.run_sealed(store=skilled, out_dir=out, flag=True)
    finally:
        cl.load = real_load
    assert seen == {"marker_before_read": True}
    marker = json.loads((out / "sealed.used").read_text())
    assert marker["development_sha256"] == (out / "develop.json.sha256").read_text().strip()
    v = doc["result"]["verdict"]
    assert v["verdict"] in ("PASS", "FAIL") and v["thresholds"] == {"min_trades": 200, "min_events": 100,
                                                                    "p_limit": 0.05}
    months = [m["month"] for m in doc["result"]["month_table"]]
    assert months == ["2026-07", "2026-08", "2026-09", "2026-10"]
    with pytest.raises(cl.Refused, match="already"):
        cl.run_sealed(store=skilled, out_dir=out, flag=True)


def test_sealed_refuses_when_development_failed(tmp_path):
    store = World(3, edge=0.0, move=0.0, pros=8, per_month=15).write(tmp_path / "s.sqlite")
    out = tmp_path / "out"
    cl.run_develop(store=store, out_dir=out)
    with pytest.raises(cl.Refused, match="step 1"):
        cl.run_sealed(store=store, out_dir=out, flag=True)
    assert not (out / "sealed.used").exists()


# ------------------------------------------------------------------ an unfinished pull
def test_an_unfinished_pull_is_refused_unless_told_which_wallets_to_leave_out(tmp_path):
    w = World(5, edge=0.0, move=0.0, pros=6, per_month=12)
    store = w.write(tmp_path / "s.sqlite", unfinished_wallet="pro03")
    with pytest.raises(cl.Refused, match="pro03"):
        cl.run_develop(store=store, out_dir=tmp_path / "o")
    doc = cl.run_develop(store=store, out_dir=tmp_path / "o", leave_out=["pro03"])
    assert doc["result"]["wallets_left_out"] == ["pro03"]
    assert "pro03" in cl.format_table(doc)


def test_an_unfinished_price_walk_names_the_wallet_that_needs_it(tmp_path):
    w = World(5, edge=0.0, move=0.0, pros=6, per_month=12)
    token = next(r["token_id"] for r in w.trades["pro02"] if MS(2026, 1) <= r["timestamp"] < MS(2026, 2))
    store = w.write(tmp_path / "s.sqlite", unfinished_token=token)
    with pytest.raises(cl.Refused, match="pro02"):
        cl.run_develop(store=store, out_dir=tmp_path / "o")


# ------------------------------------------------------------------ the store and the pull (fake transport only)
class FakeService:
    """A synthetic data service: three wallets, a few trades each. Records every URL; no request leaves."""

    def __init__(self, fail_wallet=None):
        self.urls = []
        self.fail_wallet = fail_wallet
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
        import urllib.parse
        self.urls.append(url)
        parts = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(parts.query))
        p = parts.path

        def pg(rows, more=False, cur=None):
            return json.dumps({"data": rows, "pagination": {"limit": 1000, "offset": 0, "has_more": more,
                                                            "next_cursor": cur}}).encode()
        if p == "/v2/leaderboard":
            users = ["0xa", "0xb"] if q["time_period"] == "all" else ["0xb", "0xc"]
            return pg([{"rank": i + 1, "user_id": u, "pnl": 1.0, "volume": 1.0, "user_name": "", "x_username": "",
                        "verified": False} for i, u in enumerate(users)])
        if p == "/v2/trades":
            if q["user"] == self.fail_wallet:
                raise pd.HttpError(400, url)
            return pg(self.trades[q["user"]])
        if p == "/v2/positions":
            return pg([])
        if p == "/v2/resolutions":
            return json.dumps({"data": [{"condition_id": c, "status": "resolved", "payouts": [1_000_000, 0],
                                         "resolved_at": "2026-02-10T00:00:00Z", "extended_review": False}
                                        for c in q["condition"].split(",")]}).encode()
        if p == "/v2/prices-history":
            return pg([{"timestamp": int(q["start"]), "resolution_seconds": 300, "price": 0.5},
                       {"timestamp": int(q["start"]) + 600, "resolution_seconds": 0, "price": 0.51}])
        raise AssertionError(p)


def _client(svc):
    clock = [0.0]
    return pd.PolymarketData(svc, sleep=lambda s: clock.__setitem__(0, clock[0] + s), clock=lambda: clock[0])


def test_pull_reads_the_boards_once_and_a_second_run_asks_only_for_what_is_missing(tmp_path):
    store = tmp_path / "pm.sqlite"
    svc = FakeService(fail_wallet="0xc")
    with pytest.raises(cl.PullIncomplete, match="0xc"):
        cl.pull(store, _client(svc))
    with cl.Store(store) as s:
        assert s.candidates() == ["0xa", "0xb", "0xc"]
        assert s.unfinished("trades") == ["0xc"]
        raw = s.db.execute("SELECT raw, read_at, price FROM trades WHERE wallet='0xa'").fetchone()
        assert json.loads(raw[0])["transaction_hash"] == "h1" and raw[1] and raw[2] == 0.5
    svc2 = FakeService()
    cl.pull(store, _client(svc2))
    paths = [u.split("?")[0].split(".com")[1] for u in svc2.urls]
    assert "/v2/leaderboard" not in paths
    trade_users = [u for u in svc2.urls if "/v2/trades" in u]
    assert len(trade_users) == 1 and "user=0xc" in trade_users[0]
    # resolutions: 26 conditions in batches of at most twenty, all asked in the first run already
    assert [u for u in svc2.urls if "/v2/resolutions" in u] == []
    with cl.Store(store) as s:
        assert s.unfinished("trades") == [] and s.board_read_at()["all"]
    # prices only for the position worth at least 1,000 dollars: one window around its first purchase
    price_urls = [u for u in svc.urls + svc2.urls if "/v2/prices-history" in u]
    assert len(price_urls) == 1
    t0 = MS(2026, 2)
    assert "token_id=ta" in price_urls[0] and "start=%d" % (t0 - 6 * HOUR) in price_urls[0]
    assert "end=%d" % (t0 + DAY + 6 * HOUR) in price_urls[0]
    svc3 = FakeService()
    cl.pull(store, _client(svc3))
    assert [u for u in svc3.urls if "/v2/trades" in u or "/v2/prices-history" in u or "/v2/resolutions" in u] == []


def test_the_store_refuses_a_second_candidate_set(tmp_path):
    with cl.Store(tmp_path / "x.sqlite") as s:
        s.put_boards({"all": pd.Walk.from_rows([{"rank": 1, "user_id": "0xa", "pnl": 1.0, "volume": 1.0}], "t")})
        with pytest.raises(cl.Refused):
            s.put_boards({"all": pd.Walk.from_rows([{"rank": 1, "user_id": "0xz", "pnl": 1.0, "volume": 1.0}], "t")})
        assert s.candidates() == ["0xa"]


def test_a_finished_price_walk_drops_its_last_point(tmp_path):
    with cl.Store(tmp_path / "x.sqlite") as s:
        s.put_boards({"all": pd.Walk.from_rows([], "t")})
        s.start_walk("prices", "k")
        s.finish_prices("k", "tk", 0, 100, pd.Walk.from_rows(
            [{"timestamp": 10, "resolution_seconds": 0, "price": 0.4},
             {"timestamp": 20, "resolution_seconds": 0, "price": 1.0}], "t"))
        pts = s.price_points(["tk"], max_ts=10 ** 10)
    assert [(p.ts, p.price) for p in pts["tk"]] == [(10, Decimal("0.4"))]


def test_command_line_refuses_the_sealed_period_without_the_flag(skilled, tmp_path, capsys):
    assert cl.main(["sealed", "--store", str(skilled), "--out-dir", str(tmp_path)]) == 2
    assert "REFUSED" in capsys.readouterr().err
    assert cl.main(["develop", "--store", str(skilled), "--out-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "step 1" in out and "information: decides nothing" in out
