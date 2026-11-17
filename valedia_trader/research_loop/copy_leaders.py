"""The locked "copying the leaders" test: buy one contract after a Polymarket leader's new position, at the first
price the market recorded for that outcome after the hour, hold it to the resolution, pay three cents, see what is
left.

The specification is docs/research/2026-11-17-copying-leaders-prediction.md (committed as 10ad062, its stated time
corrected in 12a4454, amended in dec8dd7 and again in a7b9379 before any data: the follower's price is the price of
the FIRST point of the outcome token's public price history whose timestamp is at or after T = t1 + 1 hour, asked
for over [T, T + 12 hours), at a grain of three hours or finer (resolution_seconds 0 to 10,800; a tie on time: the
finer grain), and never a point at or after the market's resolution time, which the program takes from the
`resolved_at` of /v2/resolutions).

    python -m valedia_trader.research_loop.copy_leaders pull --phase 1|2 [--store FILE] [--leave-out W ...]
    python -m valedia_trader.research_loop.copy_leaders develop [--store FILE] [--out-dir DIR] [--leave-out W ...]
    python -m valedia_trader.research_loop.copy_leaders sealed  --i-am-opening-the-sealed-period [--store FILE]
        [--out-dir DIR] [--develop-file FILE] [--leave-out W ...]

Rules the code holds:

* The pull has two phases. Phase 1: the two profit boards ONCE (the candidate set, stored with its read time; a later
  pull never reads the boards into the same store), every candidate's full trade history, and the resolution of
  every traded condition (twenty ids a request, one answer word per id). Phase 2 refuses while phase 1 is
  unfinished; it computes from phase 1 the leaders of every month (standing uses only what precedes the month) and
  their new positions that pass the entry tests, records that plan, and reads for exactly those positions the
  price windows [t1 + 1 h, +12 h), [t1 + 6 h, +12 h) and [t1 + 24 h, +12 h); then, only for positions whose first
  usable point after the hour has a grain of five minutes or finer, [t1 + 10 min, +12 h). Each step prints how many
  requests it is about to make first.
  Raw JSON is kept beside the parsed columns with the time of each read; a walk that did not finish stays marked
  unfinished; a second pull asks only for what is missing.
* The period wall. Development is every month up to June 2026; sealed is July to October 2026. `develop` reads only
  trades before 2026-07-01 plus one day (the 24-hour purchase sums of 30 June's positions), the resolutions of the
  conditions so read, and the price windows of development positions only. A leader's standing for month M uses only
  trades and resolutions before M began. `sealed` needs the flag and the development file with its hash; before
  its marker it checks from bookkeeping tables alone that phase 1 finished and that every sealed window of the plan
  was pulled, then recomputes the development result from THIS store and goes on only when that recomputed result
  passed and the given file equals it byte for byte. The sealed period is spent once per store: the store carries
  its own id and its own sealed marker, written and committed in the store BEFORE any sealed row is read (the
  output folder's marker is a second sign). Output folders default to one named for the store's id. The claim is
  one atomic act: an immediate write lock and a plain insert of the marker that fails if the key exists, so of two
  runs started together exactly one goes on. The marker's key present, whatever its value, means spent. The id is
  written once, by the pull that makes the store; `develop` and `sealed` never write one and refuse a store without
  a well-formed id. The limit that remains: a byte copy of a store made before the sealed run, analysed in a place
  that has never seen the original's markers, cannot be told from the original, so the lead keeps one store and
  records the sealed run in the project's ledger.
* What a development run reads (and the recomputation inside `sealed`, before its claim): the candidates' trades
  before 2026-07-01, plus the trades of 1 July only in tokens the wallet bought on 30 June (the 24-hour purchase sum
  of a 30 June position); the resolutions of the markets of those trades only, selected in the query (a
  development position, or a market a development standing touched, may resolve after June: that resolution row is
  dated in the sealed period and is read, because the position's outcome and the 45-day test need it); the price
  windows of development positions only. A resolution, price point or trade of a market that only sealed
  positions touch is never fetched before the claim; bookkeeping checks are counting queries.
* Money is whole cents decided on integers: trade cash is exact micro-dollars, a standing and a 24-hour sum are
  rounded to cents half up, a follower's price is rounded to the cent half up; means are computed at the end.
* The event (Polymarket's event slug) is the unit of the resampling. Every left-out wallet, month, position, price
  point and row is counted by reason.
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import http.client
import json
import os
import re
import sqlite3
import sys
import uuid
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path
from typing import NamedTuple

import numpy as np

from valedia_trader.research_loop import REPO
from valedia_trader.venues import polymarket_data as pd

UTC = timezone.utc
PREDICTION_COMMIT = "10ad062"
TIME_CORRECTION_COMMIT = "12a4454"
AMENDMENT_COMMIT = "dec8dd7"
SECOND_AMENDMENT_COMMIT = "a7b9379"
PREDICTION_DOC = "docs/research/2026-11-17-copying-leaders-prediction.md"

SEED = 20261117
RESAMPLES = 2000
RESAMPLE_CHUNK = 100                      # resamples drawn per call: fixed, so the random stream does not depend on size
P_LIMIT = Fraction(5, 100)
COST_CENTS = 3                            # Webull's 0.02 on a purchase plus 0.01 for another venue at another moment
SEALED_MIN_TRADES, SEALED_MIN_EVENTS = 200, 100
NOTE = "information: decides nothing"

DAY, HOUR = 86400, 3600
LOOKBACK_MONTHS = 3
MIN_RESOLVED = 30
MAX_SHARES_PER_DOLLAR = 20                # fewer than this many shares traded per dollar of profit
TOP_N, MIN_LEADERS = 10, 3
NEW_WINDOW = 30 * DAY                     # no holding of the token or its opposite in the 30 days before
BUY_WINDOW = DAY                          # purchases of the token within 24 hours of the first, inclusive
MIN_BUY_MICRO = 1_000 * 1_000_000         # 1,000 dollars, compared exactly
FIRST_LO, FIRST_HI = Decimal("0.10"), Decimal("0.90")
RESOLVE_WITHIN = 45 * DAY
DELAY = HOUR
PRICE_SPAN = 12 * HOUR                    # a price window [T, T + 12 hours): start included, end not
MAX_GRAIN = 3 * HOUR                      # a point counts at resolution_seconds 0 to 10,800
FINE_GRAIN = 300                          # the 10-minute figure only where the point used is this fine
GUARD_LO, GUARD_HI = Decimal("0.05"), Decimal("0.95")
WIN_PAYOUT = 1_000_000                    # micro-USDC a share
HORIZONS = (("main", DELAY), ("delay_6_hours", 6 * HOUR), ("delay_24_hours", DAY))   # windows every position needs
TEN_MINUTES = ("delay_10_minutes", 600)   # a window read only where the main point's grain is FINE_GRAIN or finer
BIG_FIRST_MICRO = 10_000 * 1_000_000
# event slugs taken as sports (information only; neither the trade nor the position row carries a category)
SPORTS_PREFIXES = ("nfl-", "nba-", "wnba-", "mlb-", "nhl-", "cfb-", "cbb-", "ncaab-", "ncaaf-", "epl-", "ucl-", "uel-",
                   "lal-", "bun-", "sea-", "fl1-", "mls-", "ufc-", "atp-", "wta-", "f1-", "cs2-", "lol-", "dota2-",
                   "val-", "boxing-", "pga-", "ipl-", "crint-", "kbo-", "npb-", "afl-", "nrl-", "mma-", "tennis-",
                   "soccer-", "cricket-", "golf-")

STORE = Path("artifacts") / "polymarket" / "copy_leaders.sqlite"
OUT_DIR = Path("artifacts") / "polymarket" / "copy_leaders"
REQUESTS_PER_SECOND = 3
_BEFORE_CLAIM = None                      # a hook run just before the sealed claim (tests line two processes up here)
ID_RE = r"[0-9a-f]{32}"


class Refused(Exception):
    """The program will not do this (it protects the lock). Nothing was read or written for the refused step."""


class PullIncomplete(Exception):
    """The pull ran to its end but some walks did not finish; they stay marked unfinished."""


# ---------------------------------------------------------------- calendar
def month_start(y: int, m: int) -> int:
    return calendar.timegm((y, m, 1, 0, 0, 0))


def add_months(ym: tuple[int, int], k: int) -> tuple[int, int]:
    n = ym[0] * 12 + (ym[1] - 1) + k
    return n // 12, n % 12 + 1


def month_of(ts: int) -> tuple[int, int]:
    d = datetime.fromtimestamp(ts, UTC)
    return d.year, d.month


def month_name(ym) -> str:
    return "%04d-%02d" % tuple(ym)


DEV_LAST = (2026, 6)
SEALED_MONTHS = ((2026, 7), (2026, 8), (2026, 9), (2026, 10))
SEALED_START = month_start(2026, 7)
SEALED_END = month_start(2026, 11)
SPILL = DAY                               # trades read past a period's end: the 24-hour sums of its last day only


# ---------------------------------------------------------------- money
def dec(v) -> Decimal:
    """The exact decimal a JSON number was written as (a float's shortest repr)."""
    return Decimal(v) if isinstance(v, int) else Decimal(repr(float(v)))


def half_up(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def idiv_half_up(a: int, b: int) -> int:
    """a / b rounded to an integer, halves upward (b > 0)."""
    q, r = divmod(a, b)
    return q + 1 if 2 * r >= b else q


def price_cents(p: Decimal) -> int:
    return half_up(p * 100)


def micro_to_cents(m: int) -> int:
    return idiv_half_up(m, 10_000)


def trade_result(price_c: int, win, cost: int) -> int:
    """One dollar if the outcome wins, else nothing, less the price and the cost, in whole cents."""
    return (100 if win else 0) - price_c - cost


def _seconds(n: int) -> str:
    return "%dh%02dm" % (n // 3600, n % 3600 // 60)


# ---------------------------------------------------------------- records
@dataclass(frozen=True)
class Trade:
    wallet: str
    seq: int
    side: str
    token: str
    condition: str
    size_micro: int
    price: Decimal
    ts: int
    event: str
    outcome_index: int
    cash_micro: int
    slug: str = ""


def trade_from_row(wallet: str, seq: int, row: dict) -> tuple[Trade | None, str | None]:
    """A stored trade row as a Trade (the reader's shape rules again: a wrong row is a counted reject)."""
    good, why = pd.validate_trade(row, wallet)
    if why:
        return None, why
    size, price = dec(good["size"]), dec(good["price"])
    size_micro = half_up(size * 1_000_000)
    if size_micro <= 0:
        return None, "bad_size"
    return Trade(wallet, seq, good["side"], good["token_id"], good["condition_id"], size_micro, price,
                 good["timestamp"], good["event_slug"], good["outcome_index"], half_up(price * size * 1_000_000),
                 good.get("slug") or ""), None


@dataclass(frozen=True)
class Resolution:
    condition: str
    state: str                   # "clean" (one of the two outcomes), "otherwise" (resolved some other way), "unresolved"
    payouts: tuple | None
    resolved_ts: int | None


def resolution_from_row(row: dict) -> Resolution:
    pay, ts = row.get("payouts"), row.get("resolved_ts")
    if row.get("status") == "resolved" and pay and ts is not None:
        state = "clean" if sorted(pay) == [0, WIN_PAYOUT] else "otherwise"
        return Resolution(row["condition_id"], state, tuple(pay), ts)
    return Resolution(row["condition_id"], "unresolved", None, None)


class Point(NamedTuple):
    """One point of a token's price history: its timestamp (the start of the span it was observed in), its grain in
    seconds (0 for a terminal point) and its price."""
    ts: int
    resolution_seconds: int
    price: Decimal


def follow_point(points, moment: int, resolved_ts: int):
    """The point of `points` (one price window) that prices a follower at `moment`: among the points with timestamp in
    [moment, moment + 12 hours), grain 0 to 10,800 seconds and timestamp before the market's resolution, the earliest;
    a tie on time, the finer grain. Returns (point, None, passed) or (None, 'no_price_point_after_the_hour', passed);
    `passed` counts every point of the window not usable, by reason."""
    passed, best = Counter(), None
    for p in points:
        if p.ts < moment:
            passed["point_before_the_moment"] += 1
        elif p.ts >= moment + PRICE_SPAN:
            passed["point_after_the_window"] += 1
        elif p.ts >= resolved_ts:
            passed["point_at_or_after_resolution"] += 1
        elif not 0 <= p.resolution_seconds <= MAX_GRAIN:
            passed["point_coarser_than_3_hours"] += 1
        elif best is None or (p.ts, p.resolution_seconds) < (best.ts, best.resolution_seconds):
            best = p
    if best is None:
        return None, "no_price_point_after_the_hour", passed
    return best, None, passed


# ---------------------------------------------------------------- new positions
@dataclass(frozen=True)
class NewPosition:
    wallet: str
    seq: int
    token: str
    condition: str
    event: str
    outcome_index: int
    t1: int
    price: Decimal
    first_cash_micro: int
    cash24_micro: int
    slug: str


def new_positions(trades) -> list[NewPosition]:
    """A wallet's NEW positions: purchases of a token while the wallet holds neither it nor the other outcome of its
    market, and has not traded either in the 30 days before (a trade exactly 30 days before counts as inside). The
    holding is what the wallet's own trades show. Each carries the cash of all its purchases of that token within 24
    hours of the first, the first included."""
    ordered = sorted(trades, key=lambda t: (t.ts, t.seq))
    held: dict[str, dict[str, int]] = defaultdict(dict)
    last_touch: dict[str, int] = {}
    buys: dict[str, list[Trade]] = defaultdict(list)
    firsts = []
    for t in ordered:
        if t.side == "BUY":
            holding = any(v > 0 for v in held[t.condition].values())
            recent = t.condition in last_touch and last_touch[t.condition] >= t.ts - NEW_WINDOW
            if not holding and not recent:
                firsts.append((t, len(buys[t.token])))
            buys[t.token].append(t)
            held[t.condition][t.token] = held[t.condition].get(t.token, 0) + t.size_micro
        else:
            held[t.condition][t.token] = max(0, held[t.condition].get(t.token, 0) - t.size_micro)
        last_touch[t.condition] = t.ts
    out = []
    for f, i in firsts:
        total = 0
        for b in buys[f.token][i:]:
            if b.ts > f.ts + BUY_WINDOW:
                break
            total += b.cash_micro
        out.append(NewPosition(f.wallet, f.seq, f.token, f.condition, f.event, f.outcome_index, f.ts, f.price,
                               f.cash_micro, total, f.slug))
    return out


def position_skip(pos: NewPosition, resolutions: dict) -> str | None:
    """Why a leader's new position is not copied (rules in this order), or None. The follower's price is judged later."""
    if not FIRST_LO <= pos.price <= FIRST_HI:
        return "first_price_outside_0.10_0.90"
    if pos.cash24_micro < MIN_BUY_MICRO:
        return "under_1000_in_24h"
    r = resolutions.get(pos.condition)
    if r is None or r.state == "unresolved":
        return "not_resolved"
    if r.state == "otherwise":
        return "resolved_otherwise"
    if r.resolved_ts - pos.t1 > RESOLVE_WITHIN:
        return "resolved_after_45_days"
    if not pos.event:
        return "no_event"
    return None


def is_sports(slug: str) -> bool:
    return isinstance(slug, str) and slug.lower().startswith(SPORTS_PREFIXES)


class CopiedTrade(NamedTuple):
    event: str
    price_cents: int
    win: bool
    leader: str
    t1: int
    token: str
    leader_price_micro: int
    first_cash_micro: int
    wait_seconds: int
    grain: int
    sports: bool


def price_key(token: str, start: int) -> str:
    """The name of the price window [start, start + 12 hours) of `token`."""
    return "%s@%d" % (token, start)


def copy_trades(positions, resolutions: dict, points: dict, delay: int = DELAY, passed: Counter | None = None):
    """The follower's trades for these leader positions: one contract at the first usable price point at or after
    `delay` seconds after the first purchase (see follow_point), if that price is from 0.05 to 0.95 and the market
    had not resolved by the moment. `points` maps a price window's key to its points. Returns (trades, skips);
    points passed over are added to `passed` when given."""
    out, skips = [], Counter()
    for pos in positions:
        why = position_skip(pos, resolutions)
        if why:
            skips[why] += 1
            continue
        r = resolutions[pos.condition]
        moment = pos.t1 + delay
        if r.resolved_ts <= moment:
            skips["resolved_before_follow"] += 1
            continue
        pts = points.get(price_key(pos.token, moment))
        if pts is None:
            skips["no_price_window"] += 1
            continue
        pt, why, over = follow_point(pts, moment, r.resolved_ts)
        if passed is not None:
            passed.update(over)
        if why:
            skips[why] += 1
            continue
        if not GUARD_LO <= pt.price <= GUARD_HI:
            skips["follow_price_outside_0.05_0.95"] += 1
            continue
        win = r.payouts[pos.outcome_index] == WIN_PAYOUT
        out.append(CopiedTrade(pos.event, price_cents(pt.price), win, pos.wallet, pos.t1, pos.token,
                               half_up(pos.price * 1_000_000), pos.first_cash_micro, pt.ts - moment,
                               pt.resolution_seconds, is_sports(pos.event) or is_sports(pos.slug)))
    return out, skips


# ---------------------------------------------------------------- standing
@dataclass
class Book:
    """A wallet's realized profit events, resolved positions, traded shares and sells, each with its time."""
    realized_ts: list = field(default_factory=list)
    realized_cum: list = field(default_factory=lambda: [0])
    resolved_ts: list = field(default_factory=list)
    shares_ts: list = field(default_factory=list)
    shares_cum: list = field(default_factory=lambda: [0])
    sell_ts: list = field(default_factory=list)
    beyond_ts: list = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)


def wallet_book(trades, resolutions: dict) -> Book:
    """One pass over a wallet's trades in time order with its markets' resolutions placed at their times. Average cost
    per token: a sale realizes its cash less the cost of the shares sold; a resolution realizes the payout of the
    shares held less their cost (a market resolved some other way pays its fractional payouts). Selling more than the
    trades show held: the excess earns nothing (counted). Trades at or after their market's resolution are ignored
    (counted). Every event depends only on what came before it."""
    events = []
    tokens_of: dict[str, dict[str, int]] = defaultdict(dict)
    for t in trades:
        events.append((t.ts, 1, t.seq, t))
        tokens_of[t.condition][t.token] = t.outcome_index
    for cond in tokens_of:
        r = resolutions.get(cond)
        if r is not None and r.state != "unresolved":
            events.append((r.resolved_ts, 0, -1, r))
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    held: dict[str, int] = defaultdict(int)
    basis: dict[str, int] = defaultdict(int)
    done: set = set()
    b = Book()
    realized, shares = [], []
    for ts, kind, _, ev in events:
        if kind == 0:
            done.add(ev.condition)
            for tok, oi in tokens_of[ev.condition].items():
                if held[tok] > 0:
                    pay = idiv_half_up(held[tok] * ev.payouts[oi], WIN_PAYOUT)
                    realized.append((ts, pay - basis[tok]))
                    b.resolved_ts.append(ts)
                held[tok], basis[tok] = 0, 0
            continue
        t = ev
        if t.condition in done:
            b.counts["trade_after_resolution"] += 1
            continue
        shares.append((ts, t.size_micro))
        if t.side == "BUY":
            held[t.token] += t.size_micro
            basis[t.token] += t.cash_micro
            continue
        b.sell_ts.append(ts)
        if t.size_micro <= held[t.token]:
            removed = idiv_half_up(basis[t.token] * t.size_micro, held[t.token])
            realized.append((ts, t.cash_micro - removed))
            held[t.token] -= t.size_micro
            basis[t.token] -= removed
        else:
            b.counts["sell_beyond_holding"] += 1
            b.beyond_ts.append(ts)
            if held[t.token] > 0:
                realized.append((ts, idiv_half_up(t.cash_micro * held[t.token], t.size_micro) - basis[t.token]))
            held[t.token], basis[t.token] = 0, 0
    for ts, v in realized:
        b.realized_ts.append(ts)
        b.realized_cum.append(b.realized_cum[-1] + v)
    for ts, v in shares:
        b.shares_ts.append(ts)
        b.shares_cum.append(b.shares_cum[-1] + v)
    return b


class Standing(NamedTuple):
    profit_micro: int
    resolved_positions: int
    shares_micro: int


def _bounds(month) -> tuple[int, int]:
    return month_start(*add_months(month, -LOOKBACK_MONTHS)), month_start(*month)


def _window_sum(times, cum, lo, hi) -> int:
    return cum[bisect_left(times, hi)] - cum[bisect_left(times, lo)]


def _count_in(times, lo, hi) -> int:
    return bisect_left(times, hi) - bisect_left(times, lo)


def standing(book: Book, month) -> Standing:
    """Realized profit (exact micro-dollars), resolved positions and shares traded over the three calendar months
    before `month`, from events before the month began."""
    lo, hi = _bounds(month)
    profit = _window_sum(book.realized_ts, book.realized_cum, lo, hi)
    return Standing(profit, _count_in(book.resolved_ts, lo, hi), _window_sum(book.shares_ts, book.shares_cum, lo, hi))


def qualifies(st: Standing) -> str | None:
    if st.resolved_positions < MIN_RESOLVED:
        return "fewer_than_30_resolved"
    if st.profit_micro <= 0:
        return "profit_not_above_zero"
    # shares / dollars < 20  <=>  shares_micro / 1e6 < 20 * profit_micro / 1e6, compared exactly
    if st.shares_micro >= MAX_SHARES_PER_DOLLAR * st.profit_micro:
        return "20_or_more_shares_per_dollar"
    return None


def pick_leaders(standings: dict) -> tuple[list, Counter]:
    """The ten qualifying wallets with the highest profit (ties: wallet name); fewer if fewer qualify; none when fewer
    than three qualify (the month is left out)."""
    skips, qual = Counter(), []
    for w, st in standings.items():
        why = qualifies(st)
        if why:
            skips[why] += 1
        else:
            qual.append((-st.profit_micro, w))
    qual.sort()
    top = [w for _, w in qual[:TOP_N]]
    if len(qual) > TOP_N:
        skips["not_in_top_10"] += len(qual) - TOP_N
    if len(top) < MIN_LEADERS:
        return [], skips
    return top, skips


def leaders_of(books: dict, month) -> tuple[list, Counter]:
    return pick_leaders({w: standing(b, month) for w, b in books.items()})


def sells_beyond_summary(books: dict, leader_months) -> dict:
    """How much the standing rule missed through shares sold beyond what the trades show held: the wallets with any
    such sell, and the share of the leaders' sells inside their standing windows that were such sells."""
    sells = beyond = 0
    for month, w in leader_months:
        lo, hi = _bounds(month)
        sells += _count_in(books[w].sell_ts, lo, hi)
        beyond += _count_in(books[w].beyond_ts, lo, hi)
    return {"wallets_with_sells_beyond_holding": sum(1 for b in books.values() if b.beyond_ts),
            "candidates": len(books), "leader_window_sells": sells, "leader_window_sells_beyond_holding": beyond,
            "share_of_leader_sells": (beyond / sells) if sells else None}


# ---------------------------------------------------------------- the p-value
def event_totals(trades, cost: int) -> list[int]:
    by: dict = {}
    for t in trades:
        by[t.event] = by.get(t.event, 0) + trade_result(t.price_cents, t.win, cost)
    return [by[k] for k in sorted(by)]


def event_p(totals) -> tuple[int, float]:
    """(resamples not above zero, p): 2,000 resamples of whole events with seed 20261117. A resample's mean per
    contract is not above zero exactly when its total is not."""
    arr = np.asarray(list(totals), dtype=np.int64)
    n = len(arr)
    if n == 0:
        return RESAMPLES, 1.0
    rng = np.random.default_rng(SEED)
    not_above, done = 0, 0
    while done < RESAMPLES:
        c = min(RESAMPLE_CHUNK, RESAMPLES - done)
        idx = rng.integers(0, n, size=(c, n))
        not_above += int(np.count_nonzero(arr[idx].sum(axis=1) <= 0))
        done += c
    return not_above, not_above / RESAMPLES


def p_within_limit(not_above: int) -> bool:
    return Fraction(not_above, RESAMPLES) <= P_LIMIT


def stats(trades, cost: int) -> dict:
    n = len(trades)
    total = sum(trade_result(t.price_cents, t.win, cost) for t in trades)
    wins = sum(1 for t in trades if t.win)
    return {"trades": n, "events": len({t.event for t in trades}), "total_cents": int(total),
            "mean_cents": (total / n) if n else None, "share_won": (wins / n) if n else None,
            "mean_price_cents": (sum(t.price_cents for t in trades) / n) if n else None, "cost_cents": cost}


def evaluate(trades, cost: int) -> dict:
    out = stats(trades, cost)
    k, p = event_p(event_totals(trades, cost))
    out.update({"not_above_zero": k, "p_value": p, "resamples": RESAMPLES, "seed": SEED})
    return out


def _failures(ev: dict) -> list:
    failed = []
    if ev["trades"] == 0:
        failed.append("no_trades")
    if ev["total_cents"] <= 0:
        failed.append("mean_not_above_zero")
    if not p_within_limit(ev["not_above_zero"]):
        failed.append("p_above_0.05")
    return failed


# ---------------------------------------------------------------- months and the plan
def _months_for(period: str, trades: dict) -> list:
    """Development: from three months after the earliest trade read to June 2026; sealed: July to October 2026."""
    if period == "sealed":
        return list(SEALED_MONTHS)
    first = min((t.ts for ts in trades.values() for t in ts), default=None)
    if first is None:
        return []
    m, out = add_months(month_of(first), LOOKBACK_MONTHS), []
    while m <= DEV_LAST:
        out.append(m)
        m = add_months(m, 1)
    return out


def position_key(token: str, t1: int) -> str:
    return "%s@%d" % (token, t1)


class Target(NamedTuple):
    """A planned position: the leaders' new position(s) in `token` first bought at t1, in `month`."""
    condition: str
    token: str
    t1: int
    month: tuple
    wallets: tuple


class PriceWindow(NamedTuple):
    """A price window the plan reads: [start, start + 12 hours) of `token`, for horizon `horizon` of `target`."""
    token: str
    start: int
    horizon: str
    target: str


def plan_targets(books: dict, positions: dict, resolutions: dict, months) -> dict:
    """{position key: Target} for every new position of a month's leaders, first bought in that month, that passes
    the entry tests (which need no price)."""
    out: dict = {}
    for m in months:
        leaders, _ = leaders_of(books, m)
        m0, m1 = month_start(*m), month_start(*add_months(m, 1))
        for w in leaders:
            for p in positions.get(w, []):
                if m0 <= p.t1 < m1 and position_skip(p, resolutions) is None:
                    k = position_key(p.token, p.t1)
                    old = out.get(k)
                    out[k] = Target(p.condition, p.token, p.t1, tuple(m),
                                    tuple(sorted(set((old.wallets if old else ()) + (w,)))))
    return out


def base_windows(targets: dict) -> dict:
    """{window key: PriceWindow} of the horizons every planned position needs."""
    out = {}
    for k, tg in sorted(targets.items()):
        for h, d in HORIZONS:
            out.setdefault(price_key(tg.token, tg.t1 + d), PriceWindow(tg.token, tg.t1 + d, h, k))
    return out


def ten_minute_windows(targets: dict, points: dict, resolutions: dict) -> dict:
    """{window key: PriceWindow} of the 10-minute horizon: only for positions whose main window is in `points` and
    whose first usable point there has a grain of FINE_GRAIN seconds or finer (the grain decides; no price does)."""
    out = {}
    for k, tg in sorted(targets.items()):
        pts = points.get(price_key(tg.token, tg.t1 + DELAY))
        r = resolutions.get(tg.condition)
        if pts is None or r is None or r.resolved_ts is None:
            continue
        pt, _, _ = follow_point(pts, tg.t1 + DELAY, r.resolved_ts)
        if pt is not None and pt.resolution_seconds <= FINE_GRAIN:
            start = tg.t1 + TEN_MINUTES[1]
            out.setdefault(price_key(tg.token, start), PriceWindow(tg.token, start, TEN_MINUTES[0], k))
    return out


# ---------------------------------------------------------------- the store
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS candidates (wallet TEXT PRIMARY KEY, boards TEXT NOT NULL, read_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS boards (board TEXT, row_index INTEGER, rank INTEGER, user_id TEXT, pnl REAL, volume REAL,
    reject TEXT, raw TEXT NOT NULL, read_at TEXT NOT NULL, PRIMARY KEY (board, row_index));
CREATE TABLE IF NOT EXISTS board_reads (board TEXT PRIMARY KEY, read_at TEXT NOT NULL, rows INTEGER, rejects TEXT);
CREATE TABLE IF NOT EXISTS walks (kind TEXT, key TEXT, status TEXT NOT NULL, started_at TEXT, finished_at TEXT,
    pages INTEGER, rows INTEGER, rejects TEXT, PRIMARY KEY (kind, key));
CREATE TABLE IF NOT EXISTS trades (wallet TEXT, seq INTEGER, proxy_wallet TEXT, side TEXT, token_id TEXT,
    condition_id TEXT, size REAL, price REAL, timestamp INTEGER, event_slug TEXT, outcome_index INTEGER,
    transaction_hash TEXT, slug TEXT, title TEXT, raw TEXT NOT NULL, read_at TEXT NOT NULL, PRIMARY KEY (wallet, seq));
CREATE INDEX IF NOT EXISTS trades_ts ON trades (wallet, timestamp);
CREATE TABLE IF NOT EXISTS resolutions (condition_id TEXT PRIMARY KEY, status TEXT, payouts TEXT, resolved_at TEXT,
    resolved_ts INTEGER, raw TEXT NOT NULL, read_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS resolution_answers (condition_id TEXT PRIMARY KEY, answer TEXT NOT NULL, read_at TEXT);
CREATE TABLE IF NOT EXISTS plan (key TEXT PRIMARY KEY, condition_id TEXT, token_id TEXT, t1 INTEGER, month TEXT,
    wallets TEXT);
CREATE TABLE IF NOT EXISTS plan_windows (key TEXT PRIMARY KEY, token_id TEXT, start INTEGER, horizon TEXT,
    target TEXT);
CREATE TABLE IF NOT EXISTS price_points (walk_key TEXT, seq INTEGER, token_id TEXT, price REAL,
    resolution_seconds INTEGER, timestamp INTEGER, raw TEXT NOT NULL, read_at TEXT NOT NULL,
    PRIMARY KEY (walk_key, seq));
CREATE TABLE IF NOT EXISTS rejects (kind TEXT, key TEXT, reason TEXT, raw TEXT, read_at TEXT);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _j(v) -> str:
    return json.dumps(v, sort_keys=True, allow_nan=True)


class Store:
    """The SQLite file of one pull."""

    def __init__(self, path, make_id: bool = False):
        """Open (or make) a store. Only the pull passes `make_id`: the store's own identity, written once."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=60)
        self.db.executescript(SCHEMA)
        if make_id and not self.meta_raw("store_id")[0]:
            self.db.execute("INSERT INTO meta VALUES ('store_id', ?)", (_j(uuid.uuid4().hex),))
            self.db.commit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *a):
        if exc_type is None:
            self.db.commit()
        else:
            self.db.rollback()
        self.db.close()
        return False

    def commit(self):
        self.db.commit()

    def meta(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def meta_raw(self, key: str) -> tuple[bool, str | None]:
        """(the key is present, its stored text), never parsed."""
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return (row is not None, row[0] if row else None)

    def checked_id(self) -> str:
        """The store's id, or a refusal in words: a store without a well-formed id was not made by the pull."""
        present, raw = self.meta_raw("store_id")
        if not present:
            raise Refused("the store %s has no id: it was not made by pull (nothing written)" % self.path)
        try:
            sid = json.loads(raw)
        except (TypeError, ValueError):
            sid = None
        if not isinstance(sid, str) or not re.fullmatch(ID_RE, sid):
            raise Refused("the store %s has a malformed id %r (nothing written)" % (self.path, (raw or "")[:40]))
        return sid

    def set_meta(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, _j(value)))

    # ------------------------------------------------------------ the candidate set: read once
    def candidates(self) -> list:
        return [r[0] for r in self.db.execute("SELECT wallet FROM candidates ORDER BY wallet")]

    def board_read_at(self) -> dict:
        return dict(self.db.execute("SELECT board, read_at FROM board_reads"))

    def put_boards(self, walks: dict) -> None:
        if self.db.execute("SELECT COUNT(*) FROM board_reads").fetchone()[0]:
            raise Refused("this store already holds its candidate set; the boards are read once")
        sources: dict = defaultdict(list)
        first_read = None
        for board, w in sorted(walks.items()):
            read_at = w.read_at[0] if w.read_at else _now()
            first_read = first_read or read_at
            good = iter(w.rows)
            for i, ((raw, why), at) in enumerate(zip(w.raw, w.raw_read_at)):
                row = None if why else next(good)
                self.db.execute("INSERT INTO boards VALUES (?,?,?,?,?,?,?,?,?)", (
                    board, i, row and row["rank"], row and row["user_id"], row and row["pnl"], row and row["volume"],
                    why, _j(raw), at))
                if row:
                    sources[row["user_id"]].append(board)
            self.db.execute("INSERT INTO board_reads VALUES (?,?,?,?)", (board, read_at, len(w.raw), _j(dict(w.rejects))))
        for wallet, boards in sources.items():
            self.db.execute("INSERT INTO candidates VALUES (?,?,?)", (wallet, _j(sorted(set(boards))), first_read))

    # ------------------------------------------------------------ walks
    def start_walk(self, kind: str, key: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO walks VALUES (?,?,?,?,?,?,?,?)",
                        (kind, key, "started", _now(), None, None, None, None))

    def _finish(self, kind: str, key: str, w, rejects=True) -> None:
        self.db.execute("UPDATE walks SET status='finished', finished_at=?, pages=?, rows=?, rejects=? "
                        "WHERE kind=? AND key=?", (_now(), w.pages, len(w.rows), _j(dict(w.rejects)), kind, key))
        if rejects:
            for (raw, why), at in zip(w.raw, w.raw_read_at):
                if why:
                    self.db.execute("INSERT INTO rejects VALUES (?,?,?,?,?)", (kind, key, why, _j(raw), at))

    def finished_keys(self, kind: str) -> set:
        return {r[0] for r in self.db.execute("SELECT key FROM walks WHERE kind=? AND status='finished'", (kind,))}

    def started_keys(self, kind: str) -> set:
        return {r[0] for r in self.db.execute("SELECT key FROM walks WHERE kind=? AND status!='finished'", (kind,))}

    def unfinished(self, kind: str) -> list:
        if kind == "trades":
            done = self.finished_keys("trades")
            return [w for w in self.candidates() if w not in done]
        return sorted(self.started_keys(kind))

    def finish_trades(self, wallet: str, w) -> None:
        """A wallet's whole trade walk, newest first as served; stored with `seq` counting from the oldest."""
        self.db.execute("DELETE FROM trades WHERE wallet=?", (wallet,))
        self.db.execute("DELETE FROM rejects WHERE kind='trades' AND key=?", (wallet,))
        n = len(w.rows)
        good_raw = [raw for raw, why in w.raw if not why]
        for i, (row, at) in enumerate(zip(w.rows, w.row_read_at)):
            self.db.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                wallet, n - 1 - i, row["proxy_wallet"], row["side"], row["token_id"], row["condition_id"], row["size"],
                row["price"], row["timestamp"], row["event_slug"], row["outcome_index"], row["transaction_hash"],
                row.get("slug"), row.get("title"), _j(good_raw[i]), at))
        self._finish("trades", wallet, w)

    def finish_resolutions(self, conditions, w, answers: dict) -> None:
        """One batch of up to twenty ids and the answer word per id (see PolymarketData.resolution_batches). Only an
        id answered "row" keeps a resolution; every other answer is unresolved or unknown to the analysis."""
        good_raw = [raw for raw, why in w.raw if not why]
        read_at = w.read_at[0] if w.read_at else _now()
        for row, raw, at in zip(w.rows, good_raw, w.row_read_at):
            cond = row["condition_id"]
            if answers.get(cond) == "row":
                self.db.execute("INSERT OR REPLACE INTO resolutions VALUES (?,?,?,?,?,?,?)", (
                    cond, row["status"], _j(row["payouts"]), row["resolved_at"], row["resolved_ts"], _j(raw), at))
            elif cond not in answers:
                self.db.execute("INSERT INTO rejects VALUES (?,?,?,?,?)", ("resolutions", cond, "condition_not_asked",
                                                                           _j(raw), at))
        for raw, why in w.raw:
            if why:
                key = raw.get("condition_id") if isinstance(raw, dict) and isinstance(raw.get("condition_id"), str) else ""
                self.db.execute("INSERT INTO rejects VALUES (?,?,?,?,?)", ("resolutions", key, why, _j(raw), read_at))
        for cond in conditions:
            self.db.execute("INSERT OR REPLACE INTO resolution_answers VALUES (?,?,?)", (cond, answers[cond], read_at))
            self._finish("resolutions", cond, w, rejects=False)

    def resolution_answers(self, conditions=None) -> dict:
        """Every answer, or (selected in the query) those of `conditions`."""
        if conditions is None:
            return dict(self.db.execute("SELECT condition_id, answer FROM resolution_answers"))
        out: dict = {}
        for part in _chunks(sorted(set(conditions))):
            out.update(self.db.execute("SELECT condition_id, answer FROM resolution_answers WHERE condition_id IN (%s)"
                                       % ",".join("?" * len(part)), part))
        return out

    def record_plan(self, targets: dict, leave_out, months) -> None:
        """The plan: the planned positions and their base windows; the 10-minute windows are added later."""
        self.db.execute("DELETE FROM plan")
        self.db.execute("DELETE FROM plan_windows")
        for k, tg in targets.items():
            self.db.execute("INSERT INTO plan VALUES (?,?,?,?,?,?)", (k, tg.condition, tg.token, tg.t1,
                                                                      month_name(tg.month), _j(list(tg.wallets))))
        windows = base_windows(targets)
        self.add_plan_windows(windows)
        self.set_meta("plan", {"made_at": _now(), "leave_out": sorted(leave_out),
                               "months": [month_name(m) for m in months], "positions": len(targets),
                               "windows": len(windows), "ten_minute_windows_added": False})

    def add_plan_windows(self, windows: dict) -> None:
        for k, wd in windows.items():
            self.db.execute("INSERT OR IGNORE INTO plan_windows VALUES (?,?,?,?,?)",
                            (k, wd.token, wd.start, wd.horizon, wd.target))

    def plan(self) -> dict:
        out = {}
        for k, cond, token, t1, month, wallets in self.db.execute(
                "SELECT key, condition_id, token_id, t1, month, wallets FROM plan"):
            y, m = month.split("-")
            out[k] = Target(cond, token, t1, (int(y), int(m)), tuple(json.loads(wallets)))
        return out

    def plan_windows(self) -> dict:
        return {k: PriceWindow(tok, start, h, tg) for k, tok, start, h, tg in self.db.execute(
            "SELECT key, token_id, start, horizon, target FROM plan_windows")}

    def finish_prices(self, key: str, w) -> None:
        """One price window, its points in the order served."""
        self.db.execute("DELETE FROM price_points WHERE walk_key=?", (key,))
        self.db.execute("DELETE FROM rejects WHERE kind='prices' AND key=?", (key,))
        token = key.rsplit("@", 1)[0]
        good_raw = [raw for raw, why in w.raw if not why]
        for i, (row, at) in enumerate(zip(w.rows, w.row_read_at)):
            self.db.execute("INSERT INTO price_points VALUES (?,?,?,?,?,?,?,?)", (
                key, i, token, row["price"], row["resolution_seconds"], row["timestamp"], _j(good_raw[i]), at))
        self._finish("prices", key, w)

    # ------------------------------------------------------------ reads for the analysis
    def trade_rows(self, wallet: str, end: int, spill: int = 0) -> list:
        """A wallet's trades before `end`, and those in [end, end + spill) only in tokens it bought in the day before
        `end` (the 24-hour purchase sums of the last day's positions), selected in the query."""
        cur = self.db.execute("SELECT seq, proxy_wallet, side, token_id, condition_id, size, price, timestamp, event_slug, "
                              "outcome_index, transaction_hash, slug FROM trades WHERE wallet=? AND (timestamp < ? OR "
                              "(timestamp < ? AND token_id IN (SELECT token_id FROM trades WHERE wallet=? AND side='BUY' "
                              "AND timestamp >= ? AND timestamp < ?))) ORDER BY seq",
                              (wallet, end, end + spill, wallet, end - BUY_WINDOW, end))
        keys = ("seq", "proxy_wallet", "side", "token_id", "condition_id", "size", "price", "timestamp", "event_slug",
                "outcome_index", "transaction_hash", "slug")
        return [dict(zip(keys, r)) for r in cur]

    def traded_conditions(self) -> set:
        return {r[0] for r in self.db.execute("SELECT DISTINCT condition_id FROM trades")}

    def resolution_rows(self, conditions) -> dict:
        """The resolution rows of `conditions` only, selected in the query."""
        out = {}
        for part in _chunks(sorted(set(conditions))):
            for cond, status, payouts, resolved_at, resolved_ts in self.db.execute(
                    "SELECT condition_id, status, payouts, resolved_at, resolved_ts FROM resolutions WHERE condition_id "
                    "IN (%s)" % ",".join("?" * len(part)), part):
                out[cond] = {"condition_id": cond, "status": status, "payouts": json.loads(payouts),
                             "resolved_at": resolved_at, "resolved_ts": resolved_ts}
        return out

    def counts_by_reason(self) -> dict:
        """{kind: {reason: rows}} of every row the store set aside (repeats, conflicts, out-of-span times, rejects)."""
        out: dict = {}
        for kind, reason, n in self.db.execute("SELECT kind, reason, COUNT(*) FROM rejects GROUP BY kind, reason "
                                               "ORDER BY kind, reason"):
            out.setdefault(kind, {})[reason] = n
        return out

    def price_points(self, keys, counts: Counter | None = None) -> dict:
        """{window key: [Point]} of the named windows that finished (a window not finished is absent)."""
        out: dict = {}
        for k in sorted(set(keys)):
            if self.db.execute("SELECT 1 FROM walks WHERE kind='prices' AND key=? AND status='finished'",
                               (k,)).fetchone() is None:
                continue
            pts = []
            for ts, res, price in self.db.execute("SELECT timestamp, resolution_seconds, price FROM price_points "
                                                  "WHERE walk_key=? ORDER BY seq", (k,)):
                if not pd.is_number(price) or not isinstance(res, int) or not isinstance(ts, int):
                    if counts is not None:
                        counts["point_bad_row"] += 1
                    continue
                pts.append(Point(ts, res, dec(price)))
            out[k] = pts
        return out


def _chunks(items, n: int = 500):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _stored_trades(s: Store, wallets, end: int, counts: Counter | None = None, spill: int = 0) -> dict:
    out = {}
    for w in wallets:
        rows = []
        for r in s.trade_rows(w, end, spill):
            t, why = trade_from_row(w, r["seq"], r)
            if why:
                if counts is not None:
                    counts["trade_" + why] += 1
                continue
            rows.append(t)
        out[w] = rows
    return out


def _resolutions(s: Store, conds) -> dict:
    return {c: resolution_from_row(r) for c, r in s.resolution_rows(conds).items()}


# ---------------------------------------------------------------- the pull (the lead runs it; never in tests)
def phase1_problems(s: Store, leave_out=()) -> list:
    """What phase 1 still lacks, from bookkeeping tables only: the candidate set, finished trade walks of every wallet
    not left out, an answer for every traded condition, no resolution walk left started."""
    leave = set(leave_out)
    if not s.board_read_at():
        return ["the boards were never read"]
    out = []
    open_wallets = [w for w in s.unfinished("trades") if w not in leave]
    if open_wallets:
        out.append("trade walks of %d wallets (%s)" % (len(open_wallets), ", ".join(open_wallets[:5])))
    missing = s.db.execute("SELECT COUNT(DISTINCT t.condition_id) FROM trades t LEFT JOIN resolution_answers a "
                           "ON a.condition_id = t.condition_id WHERE a.condition_id IS NULL").fetchone()[0]
    if missing:
        out.append("no resolution answer for %d traded conditions" % missing)
    started = s.db.execute("SELECT COUNT(*) FROM walks WHERE kind='resolutions' AND status!='finished'").fetchone()[0]
    if started:
        out.append("%d resolution walks started and not finished" % started)
    return out


def plan_store(s: Store, leave_out=()) -> dict:
    """Compute from phase 1 the leaders' qualifying positions (and their base price windows) of every development
    and sealed month, record them as the plan, and return them."""
    leave = sorted(set(leave_out))
    done = s.finished_keys("trades")
    wallets = [w for w in s.candidates() if w not in leave and w in done]
    trades = _stored_trades(s, wallets, 2 ** 62)
    res = _resolutions(s, {t.condition for ts in trades.values() for t in ts})
    books = {w: wallet_book(trades.get(w, []), res) for w in s.candidates() if w not in leave}
    positions = {w: new_positions(ts) for w, ts in trades.items()}
    months = _months_for("development", trades) + list(SEALED_MONTHS)
    targets = plan_targets(books, positions, res, months)
    s.record_plan(targets, leave, months)
    return targets


def _eta(n: int) -> str:
    return _seconds(-(-n // REQUESTS_PER_SECOND))


def pull(store_path, client: pd.PolymarketData, phase: int = 1, leave_out=(), say=print) -> dict:
    """Fill the store, asking only for what is missing. Walks that fail stay unfinished; PullIncomplete names them."""
    if phase not in (1, 2):
        raise ValueError("phase is 1 or 2")
    errors = []
    with Store(store_path, make_id=True) as s:     # the pull makes the store and its id
        if phase == 1:
            _pull_phase1(s, client, say, errors)
        else:
            _pull_phase2(s, client, leave_out, say, errors)
        s.set_meta("last_pull_phase_%d_ended_at" % phase, _now())
    if errors:
        say("phase %d ended with %d walks unfinished (a re-run reads only those): %s"
            % (phase, len(errors), "; ".join(e.split(": ", 1)[0] for e in errors)))
        raise PullIncomplete("%d walks did not finish (they stay unfinished): %s" % (len(errors), "; ".join(errors[:20])))
    return {"requests": client.requests}


def _guarded(errors, label, fn) -> None:
    """Run one walk; a failure of the service or of the connection (after the reader's bounded tries) leaves it
    unfinished and the pull goes on."""
    try:
        fn()
    except (pd.HttpError, pd.PolymarketDataError, OSError, http.client.HTTPException) as e:
        errors.append("%s: %s" % (label, e))


def _pull_phase1(s: Store, client, say, errors) -> None:
    boards = 0 if s.board_read_at() else 2
    todo = s.unfinished("trades")
    say("phase 1: about to make at least %d requests (%d board reads, %s trade walks of one request or more each), "
        "at least %s at three a second; resolutions are counted once the trades are in"
        % (boards + len(todo), boards, "every candidate's" if boards else len(todo), _eta(boards + len(todo))))
    if boards:
        s.put_boards({"all": client.leaderboard("all"), "month": client.leaderboard("month")})
        s.commit()
        say("phase 1: %d candidates; about to make at least %d trade-walk requests"
            % (len(s.candidates()), len(s.unfinished("trades"))))
    for w in s.unfinished("trades"):
        s.start_walk("trades", w)
        s.commit()
        _guarded(errors, "trades %s" % w, lambda w=w: s.finish_trades(w, client.trades(w)))
        s.commit()
    conds = sorted(s.traded_conditions() - set(s.resolution_answers()))
    n = -(-len(conds) // pd.MAX_CONDITIONS)
    say("phase 1: about to make %d resolution requests for %d conditions, %s at three a second" % (n, len(conds), _eta(n)))
    for c in conds:
        s.start_walk("resolutions", c)
    s.commit()

    for i in range(0, len(conds), pd.MAX_CONDITIONS):
        part = conds[i:i + pd.MAX_CONDITIONS]

        def one(part=part):
            for batch, w, answers in client.resolution_batches(part):
                s.finish_resolutions(batch, w, answers)
        _guarded(errors, "resolutions from %s" % part[0], one)
        s.commit()


def add_ten_minute_windows(s: Store) -> dict:
    """Add to the plan the 10-minute windows its finished main windows call for (by grain only); returns them."""
    plan = s.plan()
    res = _resolutions(s, {tg.condition for tg in plan.values()})
    main = [price_key(tg.token, tg.t1 + DELAY) for tg in plan.values()]
    windows = ten_minute_windows(plan, s.price_points(main), res)
    s.add_plan_windows(windows)
    meta = s.meta("plan") or {}
    meta.update({"ten_minute_windows_added": True, "windows": len(s.plan_windows())})
    s.set_meta("plan", meta)
    return windows


def _read_windows(s: Store, client, windows: dict, errors) -> None:
    for k in sorted(windows):
        wd = windows[k]
        s.start_walk("prices", k)
        s.commit()
        _guarded(errors, "prices %s" % k, lambda k=k, wd=wd: s.finish_prices(
            k, client.prices_history(wd.token, wd.start, wd.start + PRICE_SPAN)))
        s.commit()


def _pull_phase2(s: Store, client, leave_out, say, errors) -> None:
    problems = phase1_problems(s, leave_out)
    if problems:
        raise Refused("phase 1 is unfinished: %s; finish it (or name the wallets with --leave-out)" % "; ".join(problems))
    plan = plan_store(s, leave_out)
    s.commit()
    done = s.finished_keys("prices")
    todo = {k: wd for k, wd in s.plan_windows().items() if k not in done}
    say("phase 2: %d positions planned; about to make at least %d requests for %d price windows (one hour, six "
        "hours and 24 hours after each first purchase), at least %s at three a second"
        % (len(plan), len(todo), len(todo), _eta(len(todo))))
    _read_windows(s, client, todo, errors)
    if errors:
        return                                     # the 10-minute step waits for every main window
    done = s.finished_keys("prices")
    ten = {k: wd for k, wd in add_ten_minute_windows(s).items() if k not in done}
    s.commit()
    say("phase 2: about to make %d requests for the 10-minute windows of positions priced at a five-minute grain, "
        "%s at three a second" % (len(ten), _eta(len(ten))))
    _read_windows(s, client, ten, errors)


# ---------------------------------------------------------------- loading, with the period wall
@dataclass
class Data:
    period: str
    candidates: list
    trades: dict
    resolutions: dict
    points: dict
    counts: Counter
    wallets_left_out: list
    unfinished_left_out: list
    months: list
    store_counts: dict = field(default_factory=dict)


def _period_bounds(period: str) -> tuple[int, int]:
    """(first purchases on or after, first purchases before) of a period."""
    if period == "development":
        return 0, SEALED_START
    if period == "sealed":
        return SEALED_START, SEALED_END
    raise ValueError(period)


def load(store, period: str, leave_out=()) -> Data:
    """The data of one period, the wall applied in the reads (see the module's note on what a development run
    reads): trades before the period's end, and the next day's only in tokens bought on its last day; the resolutions
    of those trades' markets; the price windows of this period's positions only. Refuses an unfinished pull unless
    every wallet it touches is left out, and a store without a well-formed id."""
    lo, hi = _period_bounds(period)
    path = Path(store)
    if not path.is_file():
        raise Refused("the store %s is missing: run pull first" % path)
    counts = Counter()
    with Store(path) as s:
        s.checked_id()
        cands = s.candidates()
        if not cands:
            raise Refused("the store holds no candidate set: run pull first")
        leave = sorted(set(leave_out))
        unknown = [w for w in leave if w not in cands]
        if unknown:
            raise Refused("not in the candidate set: %s" % unknown)
        kept = [w for w in cands if w not in leave]
        implicated = {w: ["trades walk"] for w in s.unfinished("trades")}
        done = s.finished_keys("trades")
        trades = _stored_trades(s, [w for w in kept if w in done], hi, counts, SPILL)
        answered = set(s.resolution_answers({t.condition for ts in trades.values() for t in ts}))
        for w, ts in trades.items():
            if any(t.condition not in answered for t in ts):
                implicated.setdefault(w, []).append("resolutions")
        resolutions = _resolutions(s, {t.condition for ts in trades.values() for t in ts})
        books = {w: wallet_book(trades.get(w, []), resolutions) for w in kept}
        positions = {w: new_positions(ts) for w, ts in trades.items()}
        months = _months_for(period, trades)
        targets = {k: tg for k, tg in plan_targets(books, positions, resolutions, months).items() if lo <= tg.t1 < hi}
        windows = base_windows(targets)
        points = s.price_points(windows, counts)
        windows.update(ten_minute_windows(targets, points, resolutions))
        points = s.price_points(windows, counts)
        for k, wd in windows.items():
            if k not in points:
                for w in targets[wd.target].wallets:
                    implicated.setdefault(w, []).append("price window %s not pulled" % k)
        missing = sorted(w for w in implicated if w not in leave)
        if missing:
            raise Refused("the pull is unfinished for wallets %s (%s); name them with --leave-out to run without them"
                          % (missing, "; ".join("%s: %s" % (w, implicated[w][0]) for w in missing[:10])))
        store_counts = s.counts_by_reason()
    return Data(period, kept, trades, resolutions, points, counts, leave,
                sorted(w for w in implicated if w in leave), months, store_counts)


# ---------------------------------------------------------------- the analysis
def books_of(data: Data) -> dict:
    return {w: wallet_book(data.trades.get(w, []), data.resolutions) for w in data.candidates}


def _info_block(trades, cost: int, **extra) -> dict:
    d = stats(trades, cost)
    d.update(extra)
    d["note"] = NOTE
    return d


def _quantile(xs, q: Fraction):
    """The nearest-rank quantile of whole numbers: the smallest x with at least q of the values at or below it."""
    if not xs:
        return None
    xs = sorted(xs)
    k = -(-(q.numerator * len(xs)) // q.denominator)
    return xs[max(k, 1) - 1]


def _point_table(trades, passed: Counter) -> dict:
    grains = Counter(str(t.grain) for t in trades)
    waits = [t.wait_seconds for t in trades]
    return {"note": NOTE, "grain_seconds": dict(sorted(grains.items(), key=lambda kv: int(kv[0]))),
            "delay_seconds": {"median": _quantile(waits, Fraction(1, 2)), "p90": _quantile(waits, Fraction(9, 10))},
            "points_passed_over": dict(sorted(passed.items()))}


def analyze(data: Data) -> dict:
    lo, hi = _period_bounds(data.period)
    books = books_of(data)
    positions = {w: new_positions(ts) for w, ts in data.trades.items()}
    book_counts = Counter()
    for b in books.values():
        book_counts.update(b.counts)
    info_delays = HORIZONS[1:] + (TEN_MINUTES,)
    main, info_trades, info_skips = [], {k: [] for k, _ in info_delays}, {k: Counter() for k, _ in info_delays}
    wallet_skips, position_skips, months_left_out, passed, table = Counter(), Counter(), Counter(), Counter(), []
    leader_months = []
    for m in data.months:
        leaders, wsk = leaders_of(books, m)
        wallet_skips.update(wsk)
        if not leaders:
            months_left_out["fewer_than_3_leaders"] += 1
            table.append({"month": month_name(m), "leaders": [], "left_out": "fewer_than_3_leaders", "copied": 0,
                          "positions": 0})
            continue
        leader_months += [(m, w) for w in leaders]
        m0, m1 = month_start(*m), month_start(*add_months(m, 1))
        pos = [p for w in leaders for p in positions.get(w, []) if m0 <= p.t1 < m1 and lo <= p.t1 < hi]
        got, sk = copy_trades(pos, data.resolutions, data.points, DELAY, passed)
        main += got
        position_skips.update(sk)
        for k, d in info_delays:
            g, s2 = copy_trades(pos, data.resolutions, data.points, d)
            if k == TEN_MINUTES[0]:
                # read only where the main point's grain is five minutes or finer; the figure only at that grain
                s2["main_point_coarser_than_5_minutes"] = s2.pop("no_price_window", 0)
                s2["point_coarser_than_5_minutes"] = sum(1 for t in g if t.grain > FINE_GRAIN)
                g = [t for t in g if t.grain <= FINE_GRAIN]
                s2 = +s2
            info_trades[k] += g
            info_skips[k].update(s2)
        table.append({"month": month_name(m), "leaders": leaders, "left_out": None, "copied": len(got),
                      "positions": len(pos)})
    info = {k: _info_block(info_trades[k], COST_CENTS, delay_seconds=d, position_skips=dict(sorted(info_skips[k].items())))
            for k, d in info_delays}
    info["cost_4_cents"] = _info_block(main, 4)
    info["cost_2_cents"] = _info_block(main, 2)
    info["cost_0_cents"] = _info_block(main, 0)
    info["sports"] = _info_block([t for t in main if t.sports], COST_CENTS, rule="event slug starts with a league prefix")
    info["everything_else"] = _info_block([t for t in main if not t.sports], COST_CENTS)
    info["first_purchase_over_10000"] = _info_block([t for t in main if t.first_cash_micro > BIG_FIRST_MICRO], COST_CENTS)
    own = [(1_000_000 if t.win else 0) - t.leader_price_micro for t in main]
    info["leaders_own_prices"] = {"note": NOTE, "trades": len(main), "events": len({t.event for t in main}),
                                  "cost_cents": 0, "mean_cents": (sum(own) / len(own) / 10_000) if own else None}
    info["follow_point"] = _point_table(main, passed)
    return {"period": data.period, "months": [month_name(m) for m in data.months], "candidates": len(data.candidates),
            "wallets_left_out": data.wallets_left_out, "unfinished_left_out": data.unfinished_left_out,
            "load_rejects": dict(sorted(data.counts.items())), "book_counts": dict(sorted(book_counts.items())),
            "store_counts": data.store_counts,
            "sells_beyond_holding": sells_beyond_summary(books, leader_months),
            "month_table": table, "months_left_out": dict(months_left_out),
            "wallet_skips": dict(sorted(wallet_skips.items())), "position_skips": dict(sorted(position_skips.items())),
            "main": evaluate(main, COST_CENTS), "information": info}


def development_result(data: Data) -> dict:
    r = analyze(data)
    ev = r.pop("main")
    failed = _failures(ev)
    r["step_1"] = dict(ev, thresholds={"p_limit": float(P_LIMIT)}, failed=failed,
                       verdict="FAIL" if failed else "PROCEED")
    return r


def sealed_result(data: Data) -> dict:
    r = analyze(data)
    ev = r.pop("main")
    failed = _failures(ev)
    if ev["trades"] < SEALED_MIN_TRADES:
        failed.append("trades_below_%d" % SEALED_MIN_TRADES)
    if ev["events"] < SEALED_MIN_EVENTS:
        failed.append("events_below_%d" % SEALED_MIN_EVENTS)
    r["stats"] = ev
    r["verdict"] = {"thresholds": {"min_trades": SEALED_MIN_TRADES, "min_events": SEALED_MIN_EVENTS,
                                   "p_limit": float(P_LIMIT)}, "failed": failed, "verdict": "FAIL" if failed else "PASS"}
    return r


# ---------------------------------------------------------------- files
def code_sha256() -> str:
    h = hashlib.sha256(Path(__file__).read_bytes())
    h.update(Path(pd.__file__).read_bytes())
    return h.hexdigest()


def _dump(doc: dict) -> bytes:
    return (json.dumps(doc, sort_keys=True, indent=1) + "\n").encode()


def _write_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, path)


# The readings the program takes where the prediction document is silent (printed with every result).
READINGS = (
    "A wallet's cost of a token is its average cost; a sale removes cost in proportion to the shares sold.",
    "Profit counts when it is realised inside the three-month window: at a sale, or at the market's resolution.",
    "A resolved position is a token the wallet still held when its market resolved.",
    "A profit of zero or less does not qualify.",
    "Ties in profit are broken by wallet name.",
    "Shares sold beyond what the wallet's trades show it held earn nothing (counted); every trade, such a sale "
    "included, starts the 30-day count of the new-position test again.",
    "A wallet's trades at or after its market's resolution are ignored (counted).",
    "Development starts three months after the earliest trade read and ends with June 2026.",
    "A position whose event slug is empty is left out (counted).",
    "One copied trade per leader per new position: two leaders in the same position give two copied trades.",
    "The figure for first purchases over 10,000 dollars uses the first fill only.",
    "Sports are decided by the event or market slug's prefix (a fixed list of league prefixes).",
    "The 0.05 to 0.95 guard is applied to the recorded price before rounding to the cent.",
    "More than one resolution row for a condition, when they differ, is not trusted: the condition is taken as "
    "unresolved.",
    "A trade row's identity is wallet, transaction hash, token, condition, side, size, price, time and outcome "
    "index; a price point's is token, time and grain; a resolution row's is the whole row; a repeat is dropped and "
    "counted.",
    "Times are whole epoch seconds from 2020-01-01 to one day after the pull started; others are counted rejects.",
    "A price point at or after the market's resolution time (resolved_at) is never a price; a tie on time takes "
    "the finer grain.",
    "The 10-minute figure is read only where the main window's first usable point has a grain of five minutes or "
    "finer, and counts only trades priced at that grain.",
    "The delay table's median and 90th percentile are nearest-rank.",
    "Both thresholds are compared exactly in micro-dollars: fewer than 20 shares per dollar of profit, and at least "
    "1,000 dollars within 24 hours.",
)


def store_id(store):
    """The store's id as stored (None when it has none). Opening never writes one."""
    with Store(store) as s:
        present, raw = s.meta_raw("store_id")
    if not present:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def store_sealed_marker(store):
    """The sealed marker written in the store (its parsed value, or its text when malformed), or None."""
    with Store(store) as s:
        present, raw = s.meta_raw("sealed_marker")
    if not present:
        return None
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    return v if v is not None else "null"


def _provenance(store: Path) -> dict:
    with Store(store) as s:
        boards, sid = s.board_read_at(), s.checked_id()
    return {"prediction_commit": PREDICTION_COMMIT, "time_correction_commit": TIME_CORRECTION_COMMIT,
            "amendment_commit": AMENDMENT_COMMIT, "second_amendment_commit": SECOND_AMENDMENT_COMMIT,
            "prediction_doc": PREDICTION_DOC, "code_sha256": code_sha256(),
            "store": str(Path(store).resolve()), "store_id": sid, "boards_read_at": boards,
            "readings": list(READINGS)}


def _store_path(store) -> Path:
    return Path(store) if store is not None else REPO / STORE


def _out_dir(out_dir, sid: str) -> Path:
    """The given folder, or one named for the store's id: two stores never share a default folder."""
    return Path(out_dir) if out_dir is not None else REPO / OUT_DIR / sid


def _development_doc(path: Path, leave_out) -> dict:
    """The development document of this store (development months only; deterministic for one store and code)."""
    data = load(path, "development", leave_out)
    return {"provenance": _provenance(path), "result": development_result(data)}


def run_develop(*, store=None, out_dir=None, leave_out=()) -> dict:
    path = _store_path(store)
    doc = _development_doc(path, leave_out)
    out = _out_dir(out_dir, doc["provenance"]["store_id"]) / "develop.json"
    raw = _dump(doc)
    _write_bytes(out, raw)
    _write_bytes(out.with_name(out.name + ".sha256"), (hashlib.sha256(raw).hexdigest() + "\n").encode())
    return doc


def _read_development_file(dev: Path) -> tuple[bytes, str]:
    side = dev.with_name(dev.name + ".sha256")
    if not dev.is_file():
        raise Refused("the development file %s is missing: run develop first" % dev)
    if not side.is_file():
        raise Refused("the development file's hash file %s is missing" % side)
    raw = dev.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if side.read_text().strip() != got:
        raise Refused("the development file's hash differs from the one written with it")
    return raw, got


def _why_differs(raw: bytes, recomputed: dict, leave: list) -> str:
    """Why a development file is not the result recomputed from this store (the first reason found)."""
    try:
        doc = json.loads(raw)
        prov, res = doc["provenance"], doc["result"]
    except (ValueError, KeyError, TypeError):
        return "the development file is not a development file of this program"
    if prov.get("store_id") != recomputed["provenance"]["store_id"]:
        return "the development file belongs to another store (%s, not %s)" % (
            prov.get("store_id"), recomputed["provenance"]["store_id"])
    if res.get("wallets_left_out") != leave:
        return "the wallets left out %s differ from those development left out %s" % (leave, res.get("wallets_left_out"))
    if prov.get("code_sha256") != code_sha256():
        return "the development file was made by different code"
    return "the development file differs from the development result recomputed from this store"


def sealed_bookkeeping_problems(s: Store, leave: list) -> list:
    """From bookkeeping tables only (no trade, price or result of a sealed month): phase 1 finished, a plan made with
    these wallets left out that covers the sealed months and has its 10-minute windows, and every sealed window of
    that plan pulled and finished."""
    out = phase1_problems(s, leave)
    try:
        plan_meta = s.meta("plan")
    except ValueError:
        return out + ["the phase-2 plan's record is malformed: run pull --phase 2"]
    if not isinstance(plan_meta, dict):
        return out + ["no phase-2 plan: run pull --phase 2"]
    if plan_meta.get("leave_out") != leave:
        out.append("the plan left out %s, not %s: run pull --phase 2 with the same --leave-out"
                   % (plan_meta.get("leave_out"), leave))
    if not all(month_name(m) in plan_meta.get("months", []) for m in SEALED_MONTHS):
        out.append("the plan does not cover the sealed months")
    if not plan_meta.get("ten_minute_windows_added"):
        out.append("the 10-minute windows were not yet planned: price windows not pulled; run pull --phase 2")
    months = [month_name(m) for m in SEALED_MONTHS]
    total, missing = s.db.execute(            # one counting query: no row of a sealed window is fetched
        "SELECT COUNT(*), COUNT(*) - COUNT(k.key) FROM plan_windows w JOIN plan p ON p.key = w.target "
        "LEFT JOIN walks k ON k.kind = 'prices' AND k.key = w.key AND k.status = 'finished' "
        "WHERE p.month IN (%s)" % ",".join("?" * len(months)), months).fetchone()
    if missing:
        out.append("%d of %d sealed price windows not pulled" % (missing, total))
    return out


def _claim(path: Path, sid: str, note: dict) -> None:
    """The claim, one atomic act that succeeds once: an immediate write lock, then a plain INSERT of the marker,
    which fails when the key exists. The run that does not get it refuses, having written nothing."""
    db = sqlite3.connect(str(path), timeout=60, isolation_level=None)
    try:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("INSERT INTO meta (key, value) VALUES ('sealed_marker', ?)", (_j(note),))
        except sqlite3.IntegrityError:
            db.execute("ROLLBACK")
            raise Refused("the sealed period of store %s was already claimed by another run (nothing written)"
                          % sid) from None
        db.execute("COMMIT")
    finally:
        db.close()


def run_sealed(*, store=None, out_dir=None, flag: bool = False, leave_out=(), develop_file=None) -> dict:
    """The sealed run, once per store. In order: the flag; the store's own marker; the folder's marker; the
    development file and its hash; the bookkeeping; the development result recomputed from this store (it must pass,
    and the file must equal it byte for byte); the store's marker (committed), the folder's marker; only then the
    sealed rows."""
    if not flag:
        raise Refused("the sealed period is opened only with --i-am-opening-the-sealed-period")
    path = _store_path(store)
    if not path.is_file():
        raise Refused("the store %s is missing (nothing written)" % path)
    leave = sorted(set(leave_out))
    with Store(path) as s:
        sid = s.checked_id()
        spent, raw = s.meta_raw("sealed_marker")
    if spent:                                      # the key present, whatever its value, means spent
        raise Refused("the sealed period of store %s was already run (its marker in the store reads %r): it is "
                      "spent. If its result is missing it must be recomputed by hand from the store by the lead, never "
                      "run again" % (sid, (raw or "")[:80]))
    od = _out_dir(out_dir, sid)
    marker = od / "sealed.used"
    if marker.exists():
        raise Refused("the sealed period was already run (%s exists)" % marker)
    default_marker = _out_dir(None, sid) / "sealed.used"
    if out_dir is not None and default_marker.exists():
        raise Refused("the default folder for this store's id holds a sealed marker (%s): the sealed period was "
                      "already run for this store (nothing written)" % default_marker)
    dev = Path(develop_file) if develop_file is not None else od / "develop.json"
    dev_raw, dev_sha = _read_development_file(dev)
    with Store(path) as s:                         # bookkeeping only: no sealed row is read before the marker
        problems = sealed_bookkeeping_problems(s, leave)
    if problems:
        raise Refused("the sealed run is not started (nothing written): %s" % "; ".join(problems))
    recomputed = _development_doc(path, leave)     # development months only
    step = recomputed["result"]["step_1"]
    if step["verdict"] != "PROCEED":
        raise Refused("development, recomputed from this store, failed step 1 (%s): the sealed period is not opened "
                      "(nothing written)" % step["failed"])
    if dev_raw != _dump(recomputed):
        raise Refused("%s (nothing written)" % _why_differs(dev_raw, recomputed, leave))
    note = {"time": datetime.now(UTC).isoformat(), "development_sha256": dev_sha, "development_file": str(dev),
            "wallets_left_out": leave, "store_id": sid, "store": str(path.resolve())}
    if _BEFORE_CLAIM is not None:
        _BEFORE_CLAIM()
    _claim(path, sid, note)                        # the store's own marker, committed BEFORE any sealed row is read
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(marker, "x") as f:               # the folder's marker: a second sign
            f.write(json.dumps(note, sort_keys=True, indent=1) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError:
        raise Refused("the sealed period was already run") from None
    data = load(path, "sealed", leave)
    prov = _provenance(path)
    prov.update({"development_file": str(dev), "development_sha256": dev_sha})
    doc = {"provenance": prov, "result": sealed_result(data)}
    _write_bytes(od / "sealed.json", _dump(doc))
    return doc


# ---------------------------------------------------------------- the text table
def _fmt(v, nd=2) -> str:
    return "-" if v is None else ("%.*f" % (nd, v) if isinstance(v, float) else str(v))


def format_table(doc: dict) -> str:
    r = doc["result"]
    sb = r["sells_beyond_holding"]
    share = sb["share_of_leader_sells"]
    prov = doc.get("provenance", {})
    lines = ["copying the leaders, %s period (prediction %s, amended %s and %s), store %s" % (
                 r["period"], PREDICTION_COMMIT, AMENDMENT_COMMIT, SECOND_AMENDMENT_COMMIT, prov.get("store_id")),
             "candidates %d; wallets left out: %s%s" % (
                 r["candidates"], r["wallets_left_out"] or "none",
                 " (unfinished in the pull: %s)" % r["unfinished_left_out"] if r["unfinished_left_out"] else ""),
             "months %d (%s to %s); left out: %s" % (len(r["months"]), r["months"][0] if r["months"] else "-",
                                                     r["months"][-1] if r["months"] else "-",
                                                     r["months_left_out"] or "none"),
             "rows rejected on load: %s; trade book: %s" % (r["load_rejects"] or "none", r["book_counts"] or "none"),
             "rows the store set aside, by kind and reason (repeats, conflicts, out-of-span times, rejects): %s"
             % (r.get("store_counts") or "none"),
             "%d of %d wallets sold beyond what their trades show they held; in the leaders' standing windows %d of "
             "%d sells (%s) were such sells, and their excess earned nothing in the standing" % (
                 sb["wallets_with_sells_beyond_holding"], sb["candidates"], sb["leader_window_sells_beyond_holding"],
                 sb["leader_window_sells"], "-" if share is None else "%.1f%%" % (100 * share)),
             "wallet-months not leaders: %s" % (r["wallet_skips"] or "none"),
             "leader positions not copied: %s" % (r["position_skips"] or "none")]
    s = r.get("step_1") or r["stats"]
    head = "step 1 (development): %s" % s["verdict"] if "step_1" in r else "sealed verdict: %s" % r["verdict"]["verdict"]
    failed = s.get("failed") if "step_1" in r else r["verdict"]["failed"]
    lines.append(head)
    lines.append("  trades %d from %d events; mean %s cents a contract (cost %d); p %s (%d of %d resamples not above "
                 "zero, seed %d)%s" % (s["trades"], s["events"], _fmt(s["mean_cents"], 4), s["cost_cents"],
                                       _fmt(s["p_value"], 4), s["not_above_zero"], s["resamples"], s["seed"],
                                       "; failed: %s" % failed if failed else ""))
    lines.append("months:")
    for m in r["month_table"]:
        lines.append("  %s  leaders %2d  positions %4d  copied %4d%s" % (
            m["month"], len(m["leaders"]), m["positions"], m["copied"], "  left out: " + m["left_out"] if m["left_out"] else ""))
    for k, v in r["information"].items():
        if "mean_cents" in v:
            lines.append("%s: %s: trades %s, events %s, mean %s cents" % (NOTE, k, v["trades"], v["events"],
                                                                         _fmt(v["mean_cents"], 4)))
        else:
            lines.append("%s: %s: grain used (seconds: trades) %s; delay from the moment to the point, median %s s, "
                         "90th percentile %s s; points passed over %s" % (
                             NOTE, k, v["grain_seconds"], v["delay_seconds"]["median"], v["delay_seconds"]["p90"],
                             v["points_passed_over"]))
    lines.append("readings where the document is silent (%d, also in the provenance):" % len(prov.get("readings", [])))
    lines += ["  - " + x for x in prov.get("readings", [])]
    return "\n".join(lines)


# ---------------------------------------------------------------- command line
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="copy_leaders", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull")
    p.add_argument("--phase", type=int, choices=(1, 2), required=True)
    p.add_argument("--store", default=None)
    p.add_argument("--leave-out", nargs="*", default=[])
    for name in ("develop", "sealed"):
        p = sub.add_parser(name)
        p.add_argument("--store", default=None)
        p.add_argument("--out-dir", default=None)
        p.add_argument("--leave-out", nargs="*", default=[])
        if name == "sealed":
            p.add_argument("--i-am-opening-the-sealed-period", dest="flag", action="store_true")
            p.add_argument("--develop-file", default=None)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "pull":
            print(json.dumps(pull(_store_path(a.store), pd.PolymarketData(), a.phase, a.leave_out)))
            return 0
        if a.cmd == "develop":
            doc = run_develop(store=a.store, out_dir=a.out_dir, leave_out=a.leave_out)
        else:
            doc = run_sealed(store=a.store, out_dir=a.out_dir, flag=a.flag, leave_out=a.leave_out,
                             develop_file=a.develop_file)
    except Refused as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2
    except PullIncomplete as e:
        print("INCOMPLETE: %s" % e, file=sys.stderr)
        return 3
    print(format_table(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
