"""T55a: the wallet screen of the forward copy test (docs/research/2026-11-18-forward-copy-on-webull-prediction.md,
"The wallets (the screen), fixed once"). Small synthetic stores made here with the copy-leaders store's own writer; the
real store is never opened. Placeholder wallet names only. No process is killed or raced; the one lock test holds a
lock file in this process."""
from __future__ import annotations

import calendar
import fcntl
import hashlib
import json
import os
import socket
import statistics
import sqlite3
from datetime import date
from fractions import Fraction
from pathlib import Path

import pytest

from valedia_trader.research_loop import copy_leaders as cl
from valedia_trader.research_loop import copy_screen as cs
from valedia_trader.research_loop import leaders_monitor as lm
from valedia_trader.venues import polymarket_data as pd

RUN_DAY = date(2026, 11, 21)
LAST = (2026, 10)
W = [cl.add_months((2025, 11), k) for k in range(12)]          # the window of RUN_DAY: 2025-11 .. 2026-10
WIN, LOSS, HALF = [1_000_000, 0], [0, 1_000_000], [500_000, 500_000]
MS = cl.month_start


@pytest.fixture(autouse=True)
def no_socket_may_connect(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def addr(n: int) -> str:
    """A placeholder wallet address: 0x and forty hex digits that spell a counter, nobody's."""
    return "0x%040x" % n


def D(ym, day=1, hour=0) -> int:
    return calendar.timegm((ym[0], ym[1], day, hour, 0, 0))


# ------------------------------------------------------------------ the synthetic store
class Wallet:
    """One wallet's trades and the resolutions of its markets. Every market gets a name of its own."""

    def __init__(self, name: str, res: dict):
        self.name, self.res, self.rows, self.n = name, res, [], 0
        self.tag = name[-6:]

    def new(self):
        self.n += 1
        return "%s-t%d" % (self.tag, self.n), "%s-c%d" % (self.tag, self.n)

    def trade(self, ts, token, cond, side, price, size, oi=0):
        self.rows.append({"proxy_wallet": self.name, "side": side, "token_id": token, "condition_id": cond,
                          "size": size, "price": price, "timestamp": ts, "event_slug": "ev-" + cond,
                          "outcome_index": oi, "transaction_hash": "0x%d-%s" % (len(self.rows), self.tag),
                          "slug": token, "title": token, "outcome": "Yes"})

    def resolve(self, cond, ts, payouts):
        self.res[cond] = {"condition_id": cond, "status": "resolved", "payouts": payouts, "resolved_ts": ts,
                          "resolved_at": "x"}


def per_month(v):
    return list(v) if isinstance(v, (list, tuple)) else [v] * 12


def synth(res: dict, name: str, *, sales=10, resolved=5, sale=(0.40, 0.55), res_price=0.40, size=10.0, both=0,
          pattern=None) -> Wallet:
    """A wallet with, in each window month, `sales` positions bought and sold at once (closed by a sale) and
    `resolved` positions bought and held to a known resolution in the same month, whose payouts `pattern(j)` gives
    (j counts them across the window; default: three wins in five). `both` of the sold markets also get a small
    purchase of the other outcome."""
    w = Wallet(name, res)
    sales, resolved = per_month(sales), per_month(resolved)
    pattern = pattern or (lambda j: WIN if j % 5 < 3 else LOSS)
    j = b = 0
    for i, ym in enumerate(W):
        for k in range(sales[i]):
            tok, cond = w.new()
            w.trade(D(ym, 2, k), tok, cond, "BUY", sale[0], size)
            w.trade(D(ym, 3, k), tok, cond, "SELL", sale[1], size)
            if b < both:
                b += 1
                w.trade(D(ym, 3, k, ) + 60, tok + "-other", cond, "BUY", 0.5, 1.0, oi=1)
        for k in range(resolved[i]):
            tok, cond = w.new()
            w.trade(D(ym, 4, k), tok, cond, "BUY", res_price, size)
            w.resolve(cond, D(ym, 9), pattern(j))
            j += 1
    return w


def build_store(path: Path, wallets, res: dict, *, unfinished=(), page_limit=(), no_answer=(), extra_candidates=()):
    """A store of the program's own shape (made by the copy-leaders writer, with its id)."""
    names = sorted({w.name for w in wallets} | set(extra_candidates))
    with cl.Store(path, make_id=True) as s:
        s.put_boards({"all": pd.Walk.from_rows([{"rank": i + 1, "user_id": n, "pnl": 1.0, "volume": 1.0}
                                                for i, n in enumerate(names)], "2026-11-17T09:00:00Z"),
                      "month": pd.Walk.from_rows([], "2026-11-17T09:00:01Z")})
        for w in wallets:
            s.start_walk("trades", w.name)
            if w.name in unfinished:
                continue
            if w.name in page_limit:
                s.db.execute("UPDATE walks SET status=?, pages=? WHERE kind='trades' AND key=?",
                             (cl.PAGE_LIMIT, 5000, w.name))
                continue
            s.finish_trades(w.name, pd.Walk.from_rows(sorted(w.rows, key=lambda r: -r["timestamp"]),
                                                      "2026-11-17T09:01:00Z"))
        conds = sorted({r["condition_id"] for w in wallets for r in w.rows})
        for i in range(0, len(conds), 20):
            batch = [c for c in conds[i:i + 20]]
            for c in batch:
                s.start_walk("resolutions", c)
            rows = [res[c] for c in batch if c in res]
            answers = {c: ("row" if c in res else "no_row") for c in batch}
            s.finish_resolutions([c for c in batch if c not in no_answer],
                                 pd.Walk.from_rows(rows, "2026-11-17T09:02:00Z"),
                                 {c: a for c, a in answers.items() if c not in no_answer})
    return path


def run(store, out, **kw):
    kw.setdefault("run_day", RUN_DAY)
    kw.setdefault("today", RUN_DAY)
    kw.setdefault("say", lambda m: None)
    return cs.run_screen(store=store, out_dir=out, **kw)


def by_wallet(doc):
    return {f["wallet"]: f for f in doc["wallets"]}


# ------------------------------------------------------------------ the day, the cutoff and the window
@pytest.mark.parametrize("day,last", [(date(2026, 11, 21), (2026, 10)), (date(2026, 11, 1), (2026, 10)),
                                      (date(2026, 11, 30), (2026, 10)), (date(2026, 1, 3), (2025, 12)),
                                      (date(2026, 12, 31), (2026, 11))])
def test_the_cutoff_is_the_end_of_the_last_complete_month_before_the_run_day(day, last):
    assert cs.last_complete_month(day) == last


def test_the_window_is_the_twelve_complete_months_ending_at_the_cutoff():
    w = cs.window_of((2026, 10))
    assert len(w) == 12 and w[0] == (2025, 11) and w[-1] == (2026, 10)
    assert cs.cutoff_ts((2026, 10)) == MS(2026, 11)
    assert cs.window_of((2025, 12))[0] == (2025, 1)


# ------------------------------------------------------------------ the eligibility lines, unit level (exact)
def fig(**over):
    base = dict(wallet=addr(1), counted_months=12, closed_positions=180, profit_micro=100_000_000,
                shares_micro=1_000_000_000, markets_traded=180, markets_both=0, held_resolved=60,
                win_sum_micro=36_000_000, price_sum_micro=24_000_000, counted_profit_sum_micro=120_000_000,
                counted_profit_sumsq_micro=1_300_000_000_000_000)
    base.update(over)
    return base


def test_a_wallet_that_meets_every_line_is_eligible_and_fails_nothing():
    assert cs.judge(fig()) == []


@pytest.mark.parametrize("months,closed,ok", [(9, 100, True), (8, 100, False), (9, 99, False), (12, 100, True),
                                              (12, 99, False), (0, 0, False)])
def test_line_1_nine_counted_months_and_a_hundred_closed_positions(months, closed, ok):
    failed = cs.judge(fig(counted_months=months, closed_positions=closed))
    assert (not any(f.startswith("1_") for f in failed)) is ok
    if months < 9:
        assert "1_counted_months_below_9" in failed
    if closed < 100:
        assert "1_closed_positions_below_100" in failed


@pytest.mark.parametrize("profit,shares,ok", [
    (1_000_000, 19_999_999, True),          # a hair under 20 shares a dollar
    (1_000_000, 20_000_000, False),         # exactly 20 is not fewer than 20
    (1_000_000, 20_000_001, False),
    (3, 59, True), (3, 60, False),          # micro-dollars: the comparison is exact, not rounded to cents
    (0, 0, False), (-5_000_000, 1, False)])
def test_line_2_profit_above_zero_and_fewer_than_20_shares_a_dollar(profit, shares, ok):
    failed = cs.judge(fig(profit_micro=profit, shares_micro=shares))
    assert (not any(f.startswith("2_") for f in failed)) is ok
    if profit <= 0:
        assert "2_profit_not_above_zero" in failed
    elif shares >= 20 * profit:
        assert "2_20_or_more_shares_per_dollar" in failed


@pytest.mark.parametrize("traded,both,ok", [(180, 18, True), (180, 19, False), (100, 10, True), (100, 11, False),
                                            (101, 10, True), (10, 1, True), (9, 1, False), (1, 0, True)])
def test_line_3_both_outcomes_in_at_most_ten_percent_of_the_markets(traded, both, ok):
    failed = cs.judge(fig(markets_traded=traded, markets_both=both))
    assert (not any(f.startswith("3_") for f in failed)) is ok


@pytest.mark.parametrize("n,win,price,ok", [
    (50, 30_000_000, 20_000_000, True),
    (49, 49_000_000, 1_000_000, False),                        # one position short of fifty, however good
    (50, 20_000_000, 20_000_000, False),                       # share won EQUAL to the mean price is not above it
    (50, 20_000_001, 20_000_000, True),
    (50, 19_999_999, 20_000_000, False),
    (60, 57_000_000, 57_000_000, False)])
def test_line_4_the_share_won_above_the_mean_price_over_at_least_fifty_positions(n, win, price, ok):
    failed = cs.judge(fig(held_resolved=n, win_sum_micro=win, price_sum_micro=price))
    assert (not any(f.startswith("4_") for f in failed)) is ok
    if n < 50:
        assert "4_fewer_than_50_held_to_resolution" in failed
    elif win <= price:
        assert "4_win_share_not_above_mean_price" in failed


def test_every_failed_line_is_named_not_only_the_first():
    failed = cs.judge(fig(counted_months=3, profit_micro=0, markets_both=100, held_resolved=1))
    assert {f[:1] for f in failed} == {"1", "2", "3", "4"}


# ------------------------------------------------------------------ the rank and the set, unit level
def ranked(counted):
    """A figure whose counted months' profits are `counted` (micro-dollars)."""
    return fig(counted_months=len(counted), counted_profit_sum_micro=sum(counted),
                counted_profit_sumsq_micro=sum(x * x for x in counted))


def test_the_rank_key_orders_mean_over_population_standard_deviation():
    a, b, c = ranked([10, 12, 11, 9]), ranked([10, 20, 5, 1]), ranked([-3, -4, -3, -2])
    assert cs.rank_key(a) > cs.rank_key(b) > cs.rank_key(c)
    ratio = lambda xs: statistics.fmean(xs) / statistics.pstdev(xs)
    assert ratio([10, 12, 11, 9]) > ratio([10, 20, 5, 1]) > ratio([-3, -4, -3, -2])


def test_a_ratio_that_is_exactly_equal_is_a_tie_and_scaling_does_not_move_it():
    xs = [10_000_000, 12_000_000, 7_000_000, 9_000_000]
    assert cs.rank_key(ranked(xs)) == cs.rank_key(ranked([3 * x for x in xs]))


def test_no_spread_at_all_ranks_above_any_spread_and_a_zero_mean_with_no_spread_is_zero():
    flat, loose, flat_zero = ranked([5, 5, 5, 5]), ranked([1_000_000, 1, 1_000_000, 1]), ranked([0, 0, 0, 0])
    assert cs.rank_key(flat) > cs.rank_key(loose)
    assert cs.rank_key(ranked([-5, -5, -5])) < cs.rank_key(ranked([-9, -1, -9, -1]))
    assert cs.rank_key(flat_zero) == cs.rank_key(ranked([-3, 3]))        # a mean of zero is a ratio of zero either way


def ok_fig(n, counted, margin_win=36_000_000, **over):
    f = ranked(list(counted) * 3)                           # twelve counted months
    f.update(wallet=addr(n), win_sum_micro=margin_win, **over)
    f["failed"] = cs.judge(f)
    f["eligible"] = not f["failed"]
    return f


def test_ties_go_to_the_larger_point_4_margin_then_to_the_wallet_name():
    xs = [10_000_000, 12_000_000, 7_000_000, 9_000_000]
    a = ok_fig(5, xs, 36_000_000)
    b = ok_fig(3, [2 * x for x in xs], 40_000_000)           # same ratio, larger margin
    c = ok_fig(4, [3 * x for x in xs], 36_000_000)           # same ratio and margin as a: the name decides
    got = [f["wallet"] for f in cs.pick([a, b, c])]
    assert got == [addr(3), addr(4), addr(5)]


def test_the_set_is_the_first_20_eligible_only_and_never_an_ineligible_wallet():
    figs = [ok_fig(i, [10_000_000, 11_000_000 + i * 1000, 9_000_000, 10_500_000]) for i in range(1, 26)]
    bad = ok_fig(99, [10_000_000, 10_000_001, 10_000_000, 10_000_001], held_resolved=3)      # best ratio, ineligible
    assert not bad["eligible"]
    got = cs.pick(figs + [bad])
    assert len(got) == 20 and addr(99) not in {f["wallet"] for f in got}
    assert cs.pick(figs[:7]) and len(cs.pick(figs[:7])) == 7 and cs.pick([bad]) == []


# ------------------------------------------------------------------ one store, many wallets
HALF_RES = [HALF] * 40 + [LOSS] * 10
PATTERNS = {
    "fav95_equal": ([WIN] * 47 + [HALF] + [LOSS] * 2, 0.95),
    "fav95_above": ([WIN] * 48 + [LOSS] * 2, 0.95),
    "fav95_below": ([WIN] * 47 + [LOSS] * 3, 0.95),
    "half_not_full_win": (HALF_RES, 0.45),
    "half_is_half": ([HALF] * 50, 0.45),
}
FIFTY = [5, 5] + [4] * 10


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    folder = tmp_path_factory.mktemp("screen")
    res: dict = {}
    wallets = {}

    def add(n, name_n, **kw):
        wallets[n] = synth(res, addr(name_n), **kw)

    add("good", 1)
    add("months_9", 2, sales=[10] * 9 + [4] * 3, resolved=[6] * 9 + [0] * 3)
    add("months_8", 3, sales=[10] * 8 + [4] * 4, resolved=[7] * 8 + [0] * 4)
    add("five_counts", 4, sales=[5] * 3 + [10] * 9, resolved=[0] * 3 + [6] * 9)
    add("closed_100", 5, sales=[4] * 4 + [3] * 8, resolved=5)
    add("closed_99", 6, sales=[4] * 4 + [3] * 7 + [2], resolved=5)
    add("zero_profit", 7, sales=10, resolved=0, sale=(0.5, 0.5))
    add("spd_20", 8, sales=10, resolved=0, sale=(0.40, 0.50))
    add("spd_just_under", 9, sales=10, resolved=0, sale=(0.40, 0.5001))
    add("both_18", 10, sales=10, resolved=5, both=18)
    add("both_19", 11, sales=10, resolved=5, both=19)
    add("held_49", 12, sales=10, resolved=[5] + [4] * 11)
    add("held_50", 13, sales=10, resolved=FIFTY)
    for k, (name, (pats, price)) in enumerate(PATTERNS.items()):
        add(name, 20 + k, sales=10, resolved=FIFTY, res_price=price, pattern=lambda j, p=pats: p[j])
    add("steady_a", 30, sales=10, resolved=5)
    add("steady_b", 31, sales=10, resolved=5)
    lumpy = [4, 16] * 6
    add("lumpy", 32, sales=lumpy, resolved=5)
    add("lumpy_x2", 33, sales=lumpy, resolved=5, size=20.0)
    # the accounting wallet: exact figures worked by hand (see the test that reads it)
    acct = Wallet(addr(40), res)
    tA, cA = "acct-A", "acct-cA"
    acct.trade(D((2025, 10), 5), tA, cA, "BUY", 0.40, 10.0)
    acct.trade(D((2025, 11), 5), tA, cA, "SELL", 0.60, 4.0)
    acct.resolve(cA, D((2025, 12), 5), WIN)
    acct.trade(D((2025, 11), 6), "acct-B", "acct-cB", "BUY", 0.50, 10.0)
    acct.trade(D((2025, 12), 2), "acct-C", "acct-cC", "BUY", 0.40, 10.0)
    acct.trade(D((2026, 1), 2), "acct-C", "acct-cC", "BUY", 0.60, 10.0)
    acct.trade(D((2026, 1), 3), "acct-C", "acct-cC", "SELL", 0.70, 10.0)
    acct.resolve("acct-cC", D((2026, 2), 5), LOSS)
    acct.trade(D((2026, 3), 2), "acct-D", "acct-cD", "BUY", 0.50, 5.0)
    acct.trade(D((2026, 3), 3), "acct-D", "acct-cD", "SELL", 0.50, 5.0)
    acct.trade(D((2025, 9), 2), "acct-E", "acct-cE", "BUY", 0.50, 10.0)                    # before the window
    acct.resolve("acct-cE", D((2025, 10), 5), WIN)
    acct.trade(D((2026, 10), 3), "acct-F", "acct-cF", "BUY", 0.50, 5.0)
    acct.resolve("acct-cF", D((2026, 11), 5), WIN)                                          # after the cutoff
    acct.trade(D((2026, 10), 20), "acct-G", "acct-cG", "BUY", 0.50, 10.0)
    acct.trade(D((2026, 11), 3), "acct-G", "acct-cG", "SELL", 0.90, 10.0)                  # a sale after the cutoff
    acct.trade(D((2026, 10), 10), "acct-H", "acct-cH", "BUY", 0.50, 5.0)
    acct.resolve("acct-cH", MS(2026, 11), WIN)                                              # exactly at the cutoff
    acct.trade(D((2026, 10), 12), "acct-I", "acct-cI", "BUY", 0.50, 10.0)
    acct.resolve("acct-cI", MS(2026, 11) - 1, WIN)                                          # one second inside
    wallets["acct"] = acct
    store = build_store(folder / "w.sqlite", list(wallets.values()), res)
    doc = run(store, folder / "out")
    return {"doc": doc, "w": by_wallet(doc), "wallets": wallets, "store": store, "folder": folder}


def F(world, name):
    return world["w"][world["wallets"][name].name]


def failed(world, name):
    return set(F(world, name)["failed"])


def test_the_base_wallet_meets_every_line(world):
    f = F(world, "good")
    assert f["failed"] == [] and f["eligible"] and f["counted_months"] == 12
    assert f["closed_positions"] == 12 * 15 and f["held_resolved"] == 60
    assert f["markets_traded"] == 180 and f["markets_both"] == 0


def test_line_1_on_both_sides_of_nine_counted_months(world):
    assert F(world, "months_9")["counted_months"] == 9 and not any(x.startswith("1_") for x in failed(world, "months_9"))
    assert F(world, "months_8")["counted_months"] == 8 and "1_counted_months_below_9" in failed(world, "months_8")
    assert "1_closed_positions_below_100" not in failed(world, "months_8")


def test_a_month_with_exactly_five_closed_counts_and_one_with_four_does_not(world):
    f = F(world, "five_counts")
    assert [m["closed"] for m in f["months"][:3]] == [5, 5, 5] and f["counted_months"] == 12
    assert [m["counted"] for m in F(world, "months_9")["months"][-3:]] == [False] * 3
    assert [m["closed"] for m in F(world, "months_9")["months"][-3:]] == [4, 4, 4]


def test_line_1_on_both_sides_of_a_hundred_closed_positions(world):
    assert F(world, "closed_100")["closed_positions"] == 100 and not any(x.startswith("1_") for x in failed(world, "closed_100"))
    assert F(world, "closed_99")["closed_positions"] == 99 and "1_closed_positions_below_100" in failed(world, "closed_99")


def test_line_2_a_profit_of_exactly_zero_fails(world):
    f = F(world, "zero_profit")
    assert f["profit_micro"] == 0 and "2_profit_not_above_zero" in f["failed"]


def test_line_2_exactly_20_shares_a_dollar_fails_and_a_hair_under_passes(world):
    f20, fu = F(world, "spd_20"), F(world, "spd_just_under")
    assert f20["shares_micro"] == 20 * f20["profit_micro"] and "2_20_or_more_shares_per_dollar" in f20["failed"]
    assert fu["shares_micro"] < 20 * fu["profit_micro"] and not any(x.startswith("2_") for x in fu["failed"])
    assert 19.9 < fu["shares_per_dollar"] < 20


def test_line_3_on_both_sides_of_ten_percent(world):
    a, b = F(world, "both_18"), F(world, "both_19")
    assert (a["markets_traded"], a["markets_both"]) == (180, 18) and not any(x.startswith("3_") for x in a["failed"])
    assert (b["markets_traded"], b["markets_both"]) == (180, 19) and "3_both_outcomes_in_over_10_percent" in b["failed"]


def test_line_4_on_both_sides_of_fifty_positions_held_to_a_resolution(world):
    assert F(world, "held_49")["held_resolved"] == 49 and "4_fewer_than_50_held_to_resolution" in failed(world, "held_49")
    assert F(world, "held_50")["held_resolved"] == 50 and not any(x.startswith("4_") for x in failed(world, "held_50"))


def test_the_95_cent_favourite_does_not_pass_by_arithmetic(world):
    eq, up, down = F(world, "fav95_equal"), F(world, "fav95_above"), F(world, "fav95_below")
    assert eq["win_share"] == 0.95 and eq["mean_price"] == 0.95 and "4_win_share_not_above_mean_price" in eq["failed"]
    assert not any(x.startswith("4_") for x in up["failed"]) and up["margin_exact"] == "1/100"
    assert "4_win_share_not_above_mean_price" in down["failed"]


def test_a_half_payout_counts_as_half_a_win(world):
    # forty halves and ten losses at 0.45: half as a full win would give 0.80 and pass; half as a half is 0.40
    f = F(world, "half_not_full_win")
    assert f["win_share"] == pytest.approx(0.40) and "4_win_share_not_above_mean_price" in f["failed"]
    # fifty halves at 0.45: half as nothing would give 0 and fail; half as a half is 0.50, above 0.45
    g = F(world, "half_is_half")
    assert g["win_share"] == pytest.approx(0.50) and g["margin_exact"] == "1/20"
    assert not any(x.startswith("4_") for x in g["failed"])


def test_realized_profit_by_month_sales_and_known_resolutions_exactly(world):
    f = F(world, "acct")
    got = {m["month"]: m["profit_micro"] for m in f["months"]}
    assert got == {"2025-11": 800_000, "2025-12": 3_600_000, "2026-01": 2_000_000, "2026-02": -5_000_000,
                   "2026-03": 0, "2026-04": 0, "2026-05": 0, "2026-06": 0, "2026-07": 0, "2026-08": 0,
                   "2026-09": 0, "2026-10": 5_000_000}
    assert f["profit_micro"] == 6_400_000
    assert [m["month"] for m in f["months"]] == [cl.month_name(ym) for ym in W]


def test_closed_positions_are_holdings_gone_to_nothing_by_a_sale_or_a_known_resolution(world):
    f = F(world, "acct")
    got = {m["month"]: m["closed"] for m in f["months"] if m["closed"]}
    # A: resolved Dec (a partial sale in Nov closes nothing); C: resolved Feb (the January sale was partial);
    # D: sold out in March; I: resolved on the last second of October. B, F, G, H stay open at cost.
    assert got == {"2025-12": 1, "2026-02": 1, "2026-03": 1, "2026-10": 1} and f["closed_positions"] == 4


def test_a_resolution_at_or_after_the_cutoff_is_not_known_and_a_later_sale_is_not_read(world):
    f = F(world, "acct")
    assert f["held_resolved"] == 3                       # A, C, I: not E (before the window), F (Nov 5), H (the cutoff)
    assert f["win_sum_micro"] == 2_000_000 and f["price_sum_micro"] == 400_000 + 500_000 + 500_000
    assert {m["month"]: m["profit_micro"] for m in f["months"]}["2026-10"] == 5_000_000    # G's November sale unread


def test_shares_and_markets_traded_count_the_window_only(world):
    f = F(world, "acct")
    # in the window: A 4 sold, B 10, C 10+10+10, D 5+5, F 5, G 10, H 5, I 10; E and A's first purchase are before it
    assert f["shares_micro"] == 84_000_000 and f["markets_traded"] == 8 and f["markets_both"] == 0


def test_the_average_cost_of_a_second_purchase_prices_the_sale(world):
    # C: 10 at 0.40 and 10 at 0.60 average 0.50; 10 sold at 0.70 earns 2.00 (January), not 3.00 or 1.00
    assert {m["month"]: m["profit_micro"] for m in F(world, "acct")["months"]}["2026-01"] == 2_000_000


def test_the_rank_is_mean_over_the_population_standard_deviation_of_the_counted_months(world):
    f = F(world, "lumpy")
    xs = [m["profit_micro"] for m in f["months"] if m["counted"]]
    assert len(xs) == 12 and f["rank_ratio"] == pytest.approx(statistics.fmean(xs) / statistics.pstdev(xs))
    assert f["rank_ratio"] != pytest.approx(statistics.fmean(xs) / statistics.stdev(xs), rel=1e-4)
    assert f["counted_profit_sum_micro"] == sum(xs)


def test_a_wallet_with_no_spread_is_ranked_first_and_a_scaled_copy_ties_with_it(world):
    got = [f["wallet"] for f in world["doc"]["ranking"]]
    a, b, lump, lump2 = (world["wallets"][n].name for n in ("steady_a", "steady_b", "lumpy", "lumpy_x2"))
    assert got.index(a) < got.index(b) < got.index(lump) < got.index(lump2)
    assert F(world, "steady_a")["rank_ratio"] == "inf"
    la, lb = F(world, "lumpy"), F(world, "lumpy_x2")
    assert cs.rank_key(la) == cs.rank_key(lb) and la["margin_exact"] == lb["margin_exact"]


def test_the_report_names_every_wallet_every_figure_and_every_failed_line(world):
    doc = world["doc"]
    assert len(doc["wallets"]) == len(world["wallets"])
    for f in doc["wallets"]:
        for k in ("months", "counted_months", "closed_positions", "profit_micro", "shares_micro", "markets_traded",
                  "markets_both", "held_resolved", "win_sum_micro", "price_sum_micro", "rank_ratio", "failed",
                  "eligible"):
            assert k in f
        assert f["eligible"] == (not f["failed"])
        assert len(f["months"]) == 12
    on_disk = json.loads((Path(doc["files"]["report"]["path"])).read_text())
    assert {f["wallet"]: f["failed"] for f in on_disk["wallets"]} == {f["wallet"]: f["failed"] for f in doc["wallets"]}


def test_the_set_is_the_eligible_wallets_in_rank_order_in_the_door_s_shape(world):
    doc = world["doc"]
    eligible = [f["wallet"] for f in doc["wallets"] if f["eligible"]]
    assert doc["test_starts"] is True and set(doc["set"]) == set(eligible) and len(doc["set"]) <= 20
    sf = Path(doc["files"]["set"]["path"])
    got, why = lm.read_wallets_file(sf)                                  # the leaders' record's own reader
    assert why is None and got[0] == doc["set"] and got[1] == doc["files"]["set"]["sha256"]
    assert hashlib.sha256(sf.read_bytes()).hexdigest() == doc["files"]["set"]["sha256"]
    assert doc["set"] == [f["wallet"] for f in doc["ranking"]][:20]
    assert all(f["eligible"] for f in doc["ranking"]) and len(doc["ranking"]) == len(eligible)


def test_the_report_carries_the_cutoff_the_window_the_set_hash_and_the_stores_identity(world):
    doc = world["doc"]
    assert doc["cutoff"]["last_month"] == "2026-10" and doc["cutoff"]["derived_from_run_day"] is True
    assert doc["cutoff"]["override"] is False and doc["cutoff"]["resolutions_before_ts"] == MS(2026, 11)
    assert doc["window"] == [cl.month_name(ym) for ym in W]
    st = doc["store"]
    stat = os.stat(world["store"])
    assert st["size_bytes"] == stat.st_size and st["mtime_ns"] == stat.st_mtime_ns and re_hex32(st["store_id"])
    assert st["rows_read"]["wallets_screened"] == len(world["wallets"])
    before_cutoff = sum(r["timestamp"] < MS(2026, 11) for w in world["wallets"].values() for r in w.rows)
    assert st["rows_read"]["trade_rows_per_pass"] == before_cutoff and st["rows_read"]["trade_passes"] == 2
    assert doc["files"]["report"]["sha256"] == hashlib.sha256(Path(doc["files"]["report"]["path"]).read_bytes()).hexdigest()
    sidecar = Path(doc["files"]["report"]["path"] + ".sha256").read_text().split()[0]
    assert sidecar == doc["files"]["report"]["sha256"]


def re_hex32(v):
    return isinstance(v, str) and len(v) == 32 and all(c in "0123456789abcdef" for c in v)


def test_the_store_is_unchanged_by_the_run_and_nothing_but_the_lock_file_is_made_beside_it(world):
    store = world["store"]
    assert sorted(p.name for p in store.parent.iterdir()) == ["out", "w.sqlite", "w.sqlite.lock"]
    assert world["doc"]["store"]["size_bytes"] == store.stat().st_size


# ------------------------------------------------------------------ the one run, the refusals
def small(tmp_path, n=5, **kw):
    res: dict = {}
    wallets = [synth(res, addr(100 + i), **kw) for i in range(n)]
    return build_store(tmp_path / "s.sqlite", wallets, res), wallets


def test_the_run_refuses_when_a_set_file_already_exists_and_changes_nothing(tmp_path):
    store, _ = small(tmp_path)
    out = tmp_path / "out"
    doc = run(store, out)
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    with pytest.raises(cl.Refused) as e:
        run(store, out, run_day=date(2026, 12, 2), today=date(2026, 12, 2))
    assert "already" in str(e.value)
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before and doc["test_starts"]


def test_the_one_run_is_kept_by_the_output_path_not_by_the_store(tmp_path):
    store, _ = small(tmp_path)
    run(store, tmp_path / "one")
    run(store, tmp_path / "two")                         # another folder is another path: the lead names the one


def test_a_run_refused_before_its_work_reads_no_store(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / cs.SET_FILE).write_text(addr(1) + "\n")
    with pytest.raises(cl.Refused) as e:
        run(tmp_path / "missing.sqlite", out)            # the existing set file is told first, the store never opened
    assert "already" in str(e.value)
    assert not (tmp_path / "missing.sqlite").exists() and not (tmp_path / "missing.sqlite.lock").exists()


def test_fewer_than_five_eligible_the_test_does_not_start_and_no_set_file_is_written(tmp_path):
    store, wallets = small(tmp_path, n=4)
    out = tmp_path / "out"
    doc = run(store, out)
    assert doc["test_starts"] is False and doc["set"] == [] and len([f for f in doc["wallets"] if f["eligible"]]) == 4
    assert "does not start" in doc["verdict"]
    assert sorted(p.name for p in out.iterdir()) == [cs.REPORT_FILE, cs.REPORT_FILE + ".sha256"]
    with pytest.raises(cl.Refused):                      # the run is spent all the same
        run(store, out)


def test_exactly_five_eligible_starts_the_test(tmp_path):
    store, wallets = small(tmp_path, n=5)
    doc = run(store, tmp_path / "out")
    assert doc["test_starts"] is True and len(doc["set"]) == 5
    lines = (tmp_path / "out" / cs.SET_FILE).read_text().split("\n")
    assert lines[:-1] == doc["set"] and lines[-1] == ""
    assert (tmp_path / "out" / (cs.SET_FILE + ".sha256")).read_text().split()[0] == doc["files"]["set"]["sha256"]


def test_the_leaders_record_fixes_exactly_the_set_file_through_its_door(tmp_path):
    import io
    store, _ = small(tmp_path, n=5)
    doc = run(store, tmp_path / "out")
    record, out = tmp_path / "rec" / "leaders.sqlite", io.StringIO()
    code = lm.main(["fix-leaders", "--wallets-file", doc["files"]["set"]["path"], "--store", str(record)], out=out)
    assert code == lm.OK and doc["files"]["set"]["sha256"] in out.getvalue()
    db = sqlite3.connect(record)
    assert sorted(r[0] for r in db.execute("SELECT wallet FROM leaders")) == sorted(doc["set"])
    db.close()


def test_more_than_twenty_eligible_the_set_is_twenty(tmp_path):
    store, wallets = small(tmp_path, n=22, sales=5, resolved=FIFTY)
    doc = run(store, tmp_path / "out")
    assert sum(f["eligible"] for f in doc["wallets"]) == 22 and len(doc["set"]) == 20
    assert doc["set"] == sorted(doc["set"])              # all tie exactly: the name decides


def test_the_cutoff_not_derived_from_the_run_day_is_refused_without_the_flag(tmp_path):
    store, _ = small(tmp_path, n=1)
    with pytest.raises(cl.Refused) as e:
        run(store, tmp_path / "o1", cutoff_month=(2026, 6))
    assert "run day" in str(e.value) and "cutoff" in str(e.value)
    with pytest.raises(cl.Refused):
        run(store, tmp_path / "o2", run_day=date(2026, 7, 5))          # a run day that is not today
    assert not (tmp_path / "o1").exists() and not (tmp_path / "o2").exists()


def test_with_the_flag_an_earlier_cutoff_runs_and_the_report_says_so(tmp_path):
    store, _ = small(tmp_path, n=1)
    doc = run(store, tmp_path / "o", cutoff_month=(2026, 6), cutoff_override=True)
    assert doc["cutoff"]["last_month"] == "2026-06" and doc["cutoff"]["override"] is True
    assert doc["cutoff"]["derived_from_run_day"] is False and doc["cutoff"]["derived_last_month"] == "2026-10"
    assert doc["window"][0] == "2025-07" and doc["window"][-1] == "2026-06" and doc["cutoff"]["resolutions_before_ts"] == MS(2026, 7)
    assert json.loads((tmp_path / "o" / cs.REPORT_FILE).read_text())["cutoff"]["override"] is True


def test_even_with_the_flag_an_incomplete_month_is_no_cutoff(tmp_path):
    store, _ = small(tmp_path, n=1)
    for bad in ((2026, 11), (2027, 1)):
        with pytest.raises(cl.Refused):
            run(store, tmp_path / ("o%d" % bad[1]), cutoff_month=bad, cutoff_override=True)


def test_a_pull_holding_the_lock_is_not_disturbed_and_the_run_refuses(tmp_path):
    store, _ = small(tmp_path, n=1)
    fd = os.open(str(store) + ".lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)                 # what a running pull holds
        with pytest.raises(cl.Refused) as e:
            run(store, tmp_path / "out")
        assert "pull is running" in str(e.value)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)                 # still ours: the run did not take it away
    finally:
        os.close(fd)
    assert not (tmp_path / "out").exists()
    run(store, tmp_path / "out")                                        # and the lock is free again afterwards
    fd = os.open(str(store) + ".lock", os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)                 # a pull can take it after the run
    finally:
        os.close(fd)


def test_the_store_is_opened_read_only_and_a_read_only_file_is_screened(tmp_path):
    store, _ = small(tmp_path, n=5)
    ro = cs._open_ro(store)
    with pytest.raises(sqlite3.OperationalError):
        ro.db.execute("INSERT INTO meta VALUES ('x', '1')")
    ro.db.close()
    before = hashlib.sha256(store.read_bytes()).hexdigest()
    os.chmod(store, 0o444)
    try:
        doc = run(store, tmp_path / "out")
    finally:
        os.chmod(store, 0o644)
    assert doc["test_starts"] and hashlib.sha256(store.read_bytes()).hexdigest() == before


def test_the_memory_limit_stops_the_run_with_nothing_written(tmp_path):
    store, _ = small(tmp_path, n=2)
    with pytest.raises(cl.MemoryStopped):
        run(store, tmp_path / "out", max_memory_mb=1)
    assert not (tmp_path / "out").exists() or list((tmp_path / "out").iterdir()) == []


def test_candidates_past_the_page_limit_are_named_and_left_out_any_other_unfinished_walk_refuses(tmp_path):
    res: dict = {}
    ws = [synth(res, addr(100 + i)) for i in range(5)]
    big = Wallet(addr(900), res)
    big.trade(D((2026, 1), 2), "big-t", "big-c", "BUY", 0.5, 1.0)
    store = build_store(tmp_path / "a.sqlite", ws + [big], res, page_limit={big.name})
    doc = run(store, tmp_path / "out")
    assert doc["not_screened"] == [{"wallet": big.name, "reason": "trade walk ended at the page limit (5000 pages)"}]
    assert doc["candidates"] == 6 and len(doc["wallets"]) == 5
    store2 = build_store(tmp_path / "b.sqlite", ws + [big], res, unfinished={big.name})
    with pytest.raises(cl.Refused) as e:
        run(store2, tmp_path / "out2")
    assert big.name in str(e.value) and not (tmp_path / "out2").exists()


def test_a_traded_market_with_no_resolution_answer_refuses_the_run(tmp_path):
    res: dict = {}
    w = synth(res, addr(100), sales=1, resolved=0)
    store = build_store(tmp_path / "s.sqlite", [w], res, no_answer={w.rows[0]["condition_id"]})
    with pytest.raises(cl.Refused) as e:
        run(store, tmp_path / "out")
    assert "answer" in str(e.value) and not (tmp_path / "out").exists()


def test_rows_of_the_oracles_shape_not_read_under_the_fifth_amendment_refuse_the_run(tmp_path):
    store, _ = small(tmp_path, n=1)
    db = sqlite3.connect(store)
    db.execute("UPDATE resolutions SET raw = '{\"price\": \"0\"}' WHERE rowid = (SELECT MIN(rowid) FROM resolutions)")
    db.commit()
    db.close()
    with pytest.raises(cl.Refused) as e:
        run(store, tmp_path / "out")
    assert "oracle" in str(e.value) and not (tmp_path / "out").exists()


def test_a_store_that_is_not_the_programs_own_is_refused(tmp_path):
    p = tmp_path / "x.sqlite"
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE t (a)")
    db.commit()
    db.close()
    with pytest.raises(cl.Refused):
        run(p, tmp_path / "out")


# ------------------------------------------------------------------ the command
def test_the_command_prints_the_cutoff_the_hashes_and_the_store_and_exits_0(tmp_path, monkeypatch, capsys):
    store, _ = small(tmp_path, n=5)
    monkeypatch.setattr(cs, "_today", lambda: RUN_DAY)
    code = cs.main(["run", "--store", str(store), "--out-dir", str(tmp_path / "out")])
    out = capsys.readouterr().out
    doc = json.loads((tmp_path / "out" / cs.REPORT_FILE).read_text())
    assert code == 0 and "2026-10" in out and "2025-11" in out
    assert doc["files"]["set"]["sha256"] in out
    assert hashlib.sha256((tmp_path / "out" / cs.REPORT_FILE).read_bytes()).hexdigest() in out
    assert str(doc["store"]["size_bytes"]) in out and doc["store"]["store_id"] in out
    assert addr(100) not in out                         # a wallet is named by its first ten characters at most


def test_the_command_refuses_with_code_2_and_never_reads_the_real_store(tmp_path, monkeypatch, capsys):
    store, _ = small(tmp_path, n=5)
    monkeypatch.setattr(cs, "_today", lambda: RUN_DAY)
    argv = ["run", "--store", str(store), "--out-dir", str(tmp_path / "out")]
    assert cs.main(argv) == 0
    assert cs.main(argv) == 2 and "REFUSED" in capsys.readouterr().err
    assert cs.main(["run", "--store", str(store), "--out-dir", str(tmp_path / "o2"), "--cutoff-month", "2026-06"]) == 2
    assert cs.main(["run", "--store", str(store), "--out-dir", str(tmp_path / "o3"), "--cutoff-month", "2026-06",
                    "--cutoff-not-derived-from-the-run-day"]) == 0
    with pytest.raises(SystemExit):                      # the store is named by the caller: no default, no real path
        cs.main(["run", "--out-dir", str(tmp_path / "o4")])


def test_the_module_never_names_the_real_store_or_a_network_client():
    src = Path(cs.__file__).read_text()
    for banned in ("valedia-data", "copy_leaders.sqlite", "PolymarketData(", "http.client", "urllib", "requests"):
        assert banned not in src
