"""The collector for the order-book loop: Kalshi books and trades once a minute, Nimble v3's battery every five.

The experiment is locked in docs/research/2026-11-13-orderbook-loop-prediction.md and every setting below comes
from it. This program only collects: it sends no order anywhere and scores nothing (the evaluation is a later
ticket). What it writes must let that evaluation rebuild every outcome, and nothing recorded at a time t may
depend on anything that happened after t.

    python -m valedia_trader.research_loop.orderbook_loop collect     # runs until SIGINT or SIGTERM
    python -m valedia_trader.research_loop.orderbook_loop once        # one full cycle, a smoke test
    python -m valedia_trader.research_loop.orderbook_loop select      # the markets it would watch, and why
    python -m valedia_trader.research_loop.orderbook_loop status      # coverage, latency and errors by UTC day

What happens each minute (`Collector.cycle`):

* snapshot  for each watched market, the ten best levels a side of its book and the trades since the last read
            (deduplicated by `trade_id`), each request stamped with its start and end time;
* decide    every five minutes per market, on that market's own phase (the minutes of the hour where
            minute mod 5 equals its phase, 0 to 4, given at selection and spread evenly), once it has 30 minutes
            of snapshots, right after its snapshot: build the numeric state from what was stored at or before that moment
            (`state_at`), check the pinned model tag, ask the six questions of `BATTERY` in one call, and store
            the state, the answers and one status;
* drop      a market after 12 hours of watching, 30 minutes before it closes, or when its book has been one-sided
            for 30 minutes;
* select    every six hours, fill the free places from all open markets by the rule of the prediction
            (`select_markets`), at the end of a minute whose next minute has the fewest decisions (its phase is
            the least used), and give each new market the least-used phase (`assign_phases`);
* heartbeat one row with the requests and failures since the last one.

Load at twenty markets. Kalshi: one book and one trades page per market per minute, 20 x 2 = 40 GET a minute,
under the 45 allowed; the client's pacing keeps them at most three a second, so they take about 14 s. A trades
page holds 1,000 trades, so a second page is needed only above 1,000 trades a minute in one market. The
selection every six hours is a burst outside that steady state: one page per 1,000 open markets in the close
window plus one book per market read (it stops once the places are full), at the same three a second. Model:
one call per market every five minutes, and the twenty markets are spread over five phases, 20 / 5 = 4 a
phase, so every minute makes exactly 4 calls, under the 5 allowed, in that minute and not only on average (with
fewer markets after drops a phase holds at most 4, since a new market always takes the least-used phase).
Measured on 2026-11-13 with this battery: about 11,000 input tokens and 2.7 s a call when warm, so a minute
takes about 14 s of reads plus 4 x 2.7 = 11 s of model time, well inside the minute. Each call is preceded by two
loopback GETs (`/api/tags`, `/api/version`) to check the pin.

Hosts: Kalshi only at https://api.elections.kalshi.com/trade-api/v2 through `KalshiData` (GET only, no key);
the model only at http://127.0.0.1:11434 (`ModelClient` refuses any other address). No secrets, no child process.

The record is one sqlite file, append-only: triggers refuse every UPDATE and DELETE. Times are kept as UTC ISO
text and as epoch seconds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import signal
import sqlite3
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from valedia_trader.core import legacy
from valedia_trader.research_loop import REPO
from valedia_trader.venues import kalshi_data as kd

__all__ = ["BATTERY", "BATTERY_ID", "STATE_KEYS", "TABLES", "Selection", "select_markets", "resolve_selection",
           "series_of", "assign_phases", "phase_of_minute", "book_summary", "state_at", "request_bytes", "battery_id", "Store", "ModelClient",
           "ModelUnavailable", "Collector", "day_status", "format_status", "stop_on_signals", "main"]

UTC = timezone.utc
v3 = legacy.load("systemone_v3_candidate")
KALSHI_BASE = kd.BASE_URL
MODEL_URL = "http://127.0.0.1:11434"
DEFAULT_DB = REPO / "memory" / "orderbook_loop.sqlite"

# ---- the prediction's settings (do not change: the experiment is locked)
WATCH_COUNT = 20                  # twenty markets at a time
RESELECT_S = 6 * 3600             # chosen by rule every six hours
MIN_BID_C, MAX_ASK_C = 10.0, 90.0  # best bid at least 10 cents, best ask at most 90
MIN_SPREAD_C, MAX_SPREAD_C = 3.0, 15.0
MIN_VOLUME_24H = 500.0            # contracts traded in the last 24 hours
MIN_CLOSE_S, MAX_CLOSE_S = 3 * 3600, 30 * 86400
MAX_PER_SERIES = 4
WATCH_MAX_S = 12 * 3600           # watched for 12 hours,
STOP_BEFORE_CLOSE_S = 1800        # or until 30 minutes before it closes,
ONE_SIDED_MINUTES = 30            # or until its book has been one-sided for 30 minutes
DEPTH = 10                        # the ten best levels a side, once a minute
SLOT_S = 300                      # a decision every five minutes per market
PHASES = 5                        # each market decides on minutes where (minute of the hour) mod 5 == its phase
TAG = v3.TAG                      # nimble-v3:9b-mxfp8, checked on every call

# ---- the ticket's settings
WARMUP_S = 1800                   # a market needs 30 minutes of snapshots before its first decision
STALE_S = 90                      # no call when the newest snapshot is older than this
MODEL_LIMIT_S = 10.0              # an answer after this is stored but marked `late`
MODEL_HARD_TIMEOUT_S = 30.0       # the connection is given up after this (`model_unavailable`); my choice
FAILS_BEFORE_PAUSE, PAUSE_S = 5, 300
TRADE_OVERLAP_S = 5               # each trades read starts 5 s before the previous one began
FIRST_TRADES_S = 60               # a new market's first read goes back one minute
MAX_BACKFILL_S = 3600             # after a stop the tape is read again from where it stopped, at most an hour back
# Books are read only for markets whose cached quote (the market object, cached about five seconds) is within
# two cents of the band; the live book then decides. This keeps the selection to a few hundred book reads.
CACHE_MARGIN_C = 2.0

STATUSES = ("ok", "late", "model_unavailable", "tag_mismatch", "stale_data", "error")
TABLES = ("selections", "markets", "market_drops", "snapshots", "trades", "decisions", "heartbeats", "events")
OBJECT_KINDS = ("not_open", "not_whole_cent", "volume", "closes_soon", "closes_late")


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


def stamp(prefix: str, ts: float) -> dict:
    """{prefix_iso, prefix_ts} (or {iso, ts} for an empty prefix): every time is kept both ways."""
    p = prefix + "_" if prefix else ""
    return {p + "iso": iso(ts), p + "ts": ts}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _num(v) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _r(x: float | None, nd: int):
    """Rounded for the state; integers when nd is 0; never -0.0."""
    if x is None or not math.isfinite(x):
        return None
    v = round(x, nd)
    return int(v) if nd == 0 else v + 0.0


def _c(dollars: float) -> float:
    """Dollars to cents without float dust."""
    return round(dollars * 100.0, 4)


# ============================================================================ the battery

_STATE_TEXT = ("The state is computed in code from one prediction market's order book and recent trades. Prices, "
               "the mid, the spread and changes are in cents (0 to 100); sizes, depths and volumes are in contracts; "
               "times are in minutes; imbalances and shares are fractions from 0 to 1; a change is the value now "
               "minus the value then; null means not known yet. The bid is the best offer to buy yes and the ask "
               "the best offer to sell yes. ")

BATTERY: dict = {
    "up_15m": {
        "type": "noul",
        "instructions": _STATE_TEXT + "Question: will the mid be higher 15 minutes from now than it is now?",
        "criteria": {"true": "the mid 15 minutes from now is above the mid now",
                     "false": "the mid 15 minutes from now is equal to or below the mid now"}},
    "down_15m": {
        "type": "noul",
        "instructions": _STATE_TEXT + "Question: will the mid be lower 15 minutes from now than it is now?",
        "criteria": {"true": "the mid 15 minutes from now is below the mid now",
                     "false": "the mid 15 minutes from now is equal to or above the mid now"}},
    "move_15m": {
        "type": "noul",
        "instructions": _STATE_TEXT + ("Question: within the next 15 minutes, will the mid move away from the mid "
                                       "now by at least half of the spread now, up or down?"),
        "criteria": {"true": "at some minute in the next 15 minutes the mid is at least half of the current spread "
                             "away from the mid now",
                     "false": "throughout the next 15 minutes the mid stays less than half of the current spread "
                              "away from the mid now"}},
    "toxic_15m": {
        "type": "noul",
        "instructions": _STATE_TEXT + ("Question: take the first trade in the next 15 minutes; its direction is up "
                                       "if its taker bought yes and down if its taker bought no. Will the mid 15 "
                                       "minutes after that trade be beyond the mid just before it, in that trade's "
                                       "direction?"),
        "criteria": {"true": "the mid 15 minutes after the first trade is beyond the mid just before it on the "
                             "taker's side: higher if the taker bought yes, lower if the taker bought no",
                     "false": "the mid 15 minutes after the first trade is level with the mid just before it or on "
                              "the other side of it"}},
    "regime": {
        "type": "choice",
        "instructions": _STATE_TEXT + "Question: which of the three best describes the mid over the next 30 minutes?",
        "criteria": {"trending": "not quiet, and 30 minutes from now the mid has moved one cent or more in the "
                                 "direction of its move over the last 30 minutes (two cents or more either way if "
                                 "it did not move over the last 30 minutes)",
                     "mean_reverting": "neither quiet nor trending, for example the mid moves back against its move "
                                       "over the last 30 minutes",
                     "quiet": "30 minutes from now the mid is within one cent of the mid now, and it never leaves a "
                              "two-cent band around the mid now"}},
    "quote_env": {
        "type": "score",
        "instructions": _STATE_TEXT + ("Question: how favourable is this state for resting two one-contract "
                                       "quotes for the next 5 minutes, a buy one cent above the bid and a sell one "
                                       "cent below the ask? Favourable means they are likely to be filled and the "
                                       "mid is unlikely to move through a quote soon after it is filled."),
        "criteria": ["very unfavourable: fills are unlikely, or likely to come just before the mid moves through "
                     "the quote",
                     "unfavourable",
                     "favourable",
                     "very favourable: fills are likely on both sides and the mid is likely to stay near where it is"]},
}


def battery_id(battery: dict) -> str:
    """SHA-256 of the battery's canonical JSON: [[name, question], ...] in order, keys sorted, no spaces."""
    text = json.dumps([[name, q] for name, q in battery.items()], sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return sha256(text.encode("utf-8"))


BATTERY_ID = battery_id(BATTERY)


def request_bytes(state: dict) -> bytes:
    """The exact body sent to /v1/systemone: the typed request of the state and the battery, as compact JSON."""
    req = v3.make_request(state, BATTERY, tag=TAG)
    return json.dumps(req, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


# ============================================================================ selection

def series_of(market: dict) -> str:
    """The series ticker: the market's own field if it has one, else the first part of its event ticker (the
    market objects of /markets carry no series field)."""
    if market.get("series_ticker"):
        return market["series_ticker"]
    return (market.get("event_ticker") or market["ticker"]).split("-", 1)[0]


def assign_phases(used: Iterable[int], n: int) -> list[int]:
    """Phases (0 to 4) for n new markets, given the phases of the markets already watched: each takes the
    least-used phase at that moment, the lowest on a tie."""
    count = Counter({p: 0 for p in range(PHASES)})
    count.update(used)
    out = []
    for _ in range(n):
        p = min(range(PHASES), key=lambda q: (count[q], q))
        count[p] += 1
        out.append(p)
    return out


def phase_of_minute(ts: float) -> int:
    """The phase whose markets decide in the minute containing ts (minute of the hour mod 5)."""
    return int(ts // 60) % PHASES


@dataclass
class Selection:
    """The outcome of `select_markets`: the chosen tickers (most traded first), why each was chosen, and for every
    other market the reason (`reasons`) and its kind (`kinds`). `need_book` names the next candidate whose live
    book must be read before the rule can go on; when it is None the selection is final."""
    chosen: list = field(default_factory=list)
    why: dict = field(default_factory=dict)
    reasons: dict = field(default_factory=dict)
    kinds: dict = field(default_factory=dict)
    need_book: str | None = None
    books: dict = field(default_factory=dict)
    markets_seen: int = 0
    pages: int = 0


def _vol24(m: dict) -> float | None:
    return _num(m.get("volume_24h_fp", m.get("volume_24h")))


def select_markets(markets: Iterable[dict], books: dict, now: float, *, slots: int = WATCH_COUNT,
                   watched: Iterable[dict] = (), excluded: Iterable[str] = ()) -> Selection:
    """The prediction's rule, as a pure function of the market objects, the live books read so far
    ({ticker: book or None when it could not be read}) and the time.

    In order: open; whole-cent prices (`linear_cent`); at least 500 contracts in 24 hours; closing between 3 hours
    and 30 days from now; not watched already or before; a cached quote near the band (see CACHE_MARGIN_C). The
    candidates left are taken most traded first, at most four from one series and twenty in all (the markets
    being watched keep their places and count toward their series); each needs a live book with a best bid of at
    least 10 cents, a best ask of at most 90 and a spread of 3 to 15 cents."""
    sel = Selection()
    watched = list(watched)
    excluded, watched_t = set(excluded), {m["ticker"] for m in watched}
    per_series = Counter(series_of(m) for m in watched)
    places = slots - len(watched)

    def no(t, kind, text):
        sel.reasons[t], sel.kinds[t] = text, kind

    cands = []
    for m in markets:
        sel.markets_seen += 1
        t = m["ticker"]
        if m.get("status") not in ("active", "open"):
            no(t, "not_open", "not open (status %s)" % m.get("status"))
            continue
        if m.get("price_level_structure") != "linear_cent":
            no(t, "not_whole_cent", "not whole-cent prices (%s)" % m.get("price_level_structure"))
            continue
        vol = _vol24(m)
        if vol is None or vol < MIN_VOLUME_24H:
            no(t, "volume", "traded %g contracts in 24 hours, under %g" % (vol or 0.0, MIN_VOLUME_24H))
            continue
        left = kd.iso_to_ts(m["close_time"]) - now
        if left < MIN_CLOSE_S:
            no(t, "closes_soon", "closes in under 3 hours (%.1f h)" % (left / 3600))
            continue
        if left > MAX_CLOSE_S:
            no(t, "closes_late", "closes more than 30 days from now (%.1f days)" % (left / 86400))
            continue
        if t in watched_t:
            no(t, "being_watched", "being watched")
            continue
        if t in excluded:
            no(t, "watched_before", "watched before")
            continue
        bid, ask = _num(m.get("yes_bid_dollars")), _num(m.get("yes_ask_dollars"))
        if bid is None or ask is None or not (_c(bid) >= MIN_BID_C - CACHE_MARGIN_C and
                                              _c(ask) <= MAX_ASK_C + CACHE_MARGIN_C and
                                              MIN_SPREAD_C - 1 <= _c(ask) - _c(bid) <= MAX_SPREAD_C + CACHE_MARGIN_C):
            no(t, "cached_quote", "cached quote %s/%s cents is far from the band (book not read)"
               % ("-" if bid is None else "%g" % _c(bid), "-" if ask is None else "%g" % _c(ask)))
            continue
        cands.append((vol, m, left))
    cands.sort(key=lambda x: (-x[0], x[1]["ticker"]))
    full = "twenty already chosen" if slots == 20 else "%d already chosen" % slots
    for rank, (vol, m, left) in enumerate(cands, 1):
        t, s = m["ticker"], series_of(m)
        if sel.need_book is not None:
            no(t, "not_reached", "not reached (an earlier book is still to be read)")
            continue
        if len(sel.chosen) >= places:
            no(t, "full", full)
            continue
        if per_series[s] >= MAX_PER_SERIES:
            no(t, "series_full", "four already chosen from series %s" % s)
            continue
        if t not in books:
            sel.need_book = t
            no(t, "not_reached", "book not read yet")
            continue
        b = books[t]
        if b is None:
            no(t, "no_book", "book could not be read")
            continue
        q = kd.best_quote(b)
        if q["bid"] is None or q["ask"] is None:
            no(t, "one_sided", "book one-sided or empty")
            continue
        bid_c, ask_c = _c(q["bid"]), _c(q["ask"])
        spread_c = round(ask_c - bid_c, 4)
        if bid_c < MIN_BID_C:
            no(t, "bid_low", "best bid %g cents, under 10" % bid_c)
        elif ask_c > MAX_ASK_C:
            no(t, "ask_high", "best ask %g cents, over 90" % ask_c)
        elif not MIN_SPREAD_C <= spread_c <= MAX_SPREAD_C:
            no(t, "spread", "spread %g cents, outside 3 to 15" % spread_c)
        else:
            sel.chosen.append(t)
            per_series[s] += 1
            sel.why[t] = ("rank %d by 24-hour volume (%g contracts); bid %g, ask %g, spread %g cents; closes in %.1f h"
                          % (rank, vol, bid_c, ask_c, spread_c, left / 3600))
    return sel


def resolve_selection(markets: list[dict], fetch_book: Callable[[str], dict | None], now, *, slots: int = WATCH_COUNT,
                      watched: Iterable[dict] = (), excluded: Iterable[str] = ()) -> Selection:
    """Run `select_markets` to the end, reading each book it asks for, one at a time and only while places
    remain. `now` is a time or a clock, read once."""
    now = now() if callable(now) else now
    watched, excluded = list(watched), set(excluded)
    first = select_markets(markets, {}, now, slots=slots, watched=watched, excluded=excluded)
    early = {t for t, k in first.kinds.items() if k != "not_reached"}
    keep = [m for m in markets if m["ticker"] not in early]
    books: dict = {}
    while True:
        sel = select_markets(keep, books, now, slots=slots, watched=watched, excluded=excluded)
        if sel.need_book is None:
            break
        books[sel.need_book] = fetch_book(sel.need_book)
    for t in early:
        sel.reasons[t], sel.kinds[t] = first.reasons[t], first.kinds[t]
    sel.books, sel.markets_seen = books, first.markets_seen
    return sel


# ============================================================================ snapshots and the state

def book_summary(book: dict) -> dict:
    """Best yes bid and ask with their sizes, the mid and the spread (dollars), and the book's state: two_sided,
    one_sided_yes (bids to buy yes only), one_sided_no, or empty."""
    q = kd.best_quote(book)
    has_yes, has_no = q["bid"] is not None, q["ask"] is not None
    state = "two_sided" if has_yes and has_no else "one_sided_yes" if has_yes else "one_sided_no" if has_no else "empty"
    two = state == "two_sided"
    return {"book_state": state, "best_bid": q["bid"], "best_bid_size": q["bid_size"], "best_ask": q["ask"],
            "best_ask_size": q["ask_size"], "mid": round((q["bid"] + q["ask"]) / 2, 6) if two else None,
            "spread": round(q["ask"] - q["bid"], 6) if two else None}


STATE_KEYS = ("mid_cents", "spread_cents", "bid_size_top", "ask_size_top", "bid_depth_5c", "ask_depth_5c",
              "imbalance_top", "imbalance_10_levels", "microprice_minus_mid_cents",
              "mid_change_1m_cents", "mid_change_5m_cents", "mid_change_15m_cents", "mid_change_30m_cents",
              "mid_change_60m_cents", "trades_5m", "trades_15m", "volume_5m", "volume_15m",
              "yes_taker_share_5m", "yes_taker_share_15m", "max_1m_mid_move_30m_cents", "spread_vs_60m_median",
              "minutes_to_close")


def _mid_c(s: dict) -> float | None:
    if not s["yes"] or not s["no"]:
        return None
    return (_c(s["yes"][0][0]) + _c(1.0 - s["no"][0][0])) / 2


def _spread_c(s: dict) -> float | None:
    if not s["yes"] or not s["no"]:
        return None
    return round(_c(1.0 - s["no"][0][0]) - _c(s["yes"][0][0]), 4)


def _at(snaps: list[dict], target: float) -> dict | None:
    """The latest snapshot whose book read ended at or before `target`, if it ended within STALE_S of it."""
    best = None
    for s in snaps:
        if s["book_end_ts"] <= target:
            best = s
    return best if best is not None and best["book_end_ts"] >= target - STALE_S else None


def _covered_from(snaps: list[dict]) -> float | None:
    """How far back the trade reads known so far cover the tape without a hole: each read covers from its
    `trades_min_ts` to its start, and the chain holds while each earlier read started at or after the later one's
    `trades_min_ts`."""
    reads = sorted((s for s in snaps if s.get("trades_ok") and s.get("trades_min_ts") is not None),
                   key=lambda s: s["trades_start_ts"])
    if not reads:
        return None
    covered = reads[-1]["trades_min_ts"]
    for r in reversed(reads[:-1]):
        if r["trades_start_ts"] < covered:
            break
        covered = min(covered, r["trades_min_ts"])
    return covered


def state_at(snapshots: Iterable[dict], trades: Iterable[dict], t: float, close_ts: float) -> dict:
    """The numeric state the model sees at time t: a pure function of the snapshots finished by t and the trades
    created and seen by t. Everything later is ignored. Numbers only, rounded; null where it cannot be computed
    yet; no ticker, title, date or time of day."""
    snaps = sorted((s for s in snapshots if s["end_ts"] <= t), key=lambda s: s["book_end_ts"])
    tr = [x for x in trades if x["created_ts"] <= t and x["seen_ts"] <= t]
    out = dict.fromkeys(STATE_KEYS)
    out["minutes_to_close"] = _r((close_ts - t) / 60.0, 0)
    if snaps:
        now = snaps[-1]
        yes, no = now["yes"], now["no"]
        mid, spread = _mid_c(now), _spread_c(now)
        bs, asz = (yes[0][1] if yes else None), (no[0][1] if no else None)
        out["mid_cents"], out["spread_cents"] = _r(mid, 1), _r(spread, 1)
        out["bid_size_top"], out["ask_size_top"] = _r(bs, 0), _r(asz, 0)
        if yes:
            out["bid_depth_5c"] = _r(sum(q for p, q in yes if _c(p) >= _c(yes[0][0]) - 5 - 1e-9), 0)
        if no:
            out["ask_depth_5c"] = _r(sum(q for p, q in no if _c(p) >= _c(no[0][0]) - 5 - 1e-9), 0)
        if mid is not None:
            if bs + asz > 0:
                out["imbalance_top"] = _r(bs / (bs + asz), 2)
                micro = (_c(yes[0][0]) * asz + _c(1.0 - no[0][0]) * bs) / (bs + asz)
                out["microprice_minus_mid_cents"] = _r(micro - mid, 2)
            tot_b, tot_a = sum(q for _, q in yes[:DEPTH]), sum(q for _, q in no[:DEPTH])
            if tot_b + tot_a > 0:
                out["imbalance_10_levels"] = _r(tot_b / (tot_b + tot_a), 2)
            for k in (1, 5, 15, 30, 60):
                ref = _at(snaps, t - 60 * k)
                ref_mid = _mid_c(ref) if ref is not None else None
                out["mid_change_%dm_cents" % k] = _r(mid - ref_mid, 1) if ref_mid is not None else None
            if _at(snaps, t - 1800) is not None:
                moves = [abs(_mid_c(b) - _mid_c(a)) for a, b in zip(snaps, snaps[1:])
                         if b["book_end_ts"] > t - 1800 and b["book_end_ts"] - a["book_end_ts"] <= STALE_S
                         and _mid_c(a) is not None and _mid_c(b) is not None]
                out["max_1m_mid_move_30m_cents"] = _r(max(moves), 1) if moves else None
            if _at(snaps, t - 3600) is not None:
                spreads = [_spread_c(s) for s in snaps if s["book_end_ts"] > t - 3600 and _spread_c(s) is not None]
                med = statistics.median(spreads) if spreads else 0
                out["spread_vs_60m_median"] = _r(spread / med, 2) if med > 0 else None
    covered = _covered_from(snaps)
    for w in (5, 15):
        if covered is None or covered > t - 60 * w:
            continue
        win = [x for x in tr if x["created_ts"] > t - 60 * w]
        vol = sum(x["count"] for x in win)
        out["trades_%dm" % w], out["volume_%dm" % w] = len(win), _r(vol, 0)
        out["yes_taker_share_%dm" % w] = (_r(sum(x["count"] for x in win if x["taker_side"] == "yes") / vol, 2)
                                          if vol > 0 else None)
    return out


# ============================================================================ the record

_SCHEMA = """
CREATE TABLE IF NOT EXISTS selections (id INTEGER PRIMARY KEY, started_iso TEXT, started_ts REAL, finished_iso TEXT,
    finished_ts REAL, markets_seen INTEGER, pages INTEGER, books_read INTEGER, chosen INTEGER, reasons_json TEXT,
    reason_counts_json TEXT);
CREATE TABLE IF NOT EXISTS markets (watch_id INTEGER PRIMARY KEY, selection_id INTEGER, ticker TEXT, series TEXT,
    selected_iso TEXT, selected_ts REAL, close_iso TEXT, close_ts REAL, market_json TEXT, book_json TEXT,
    fee_type TEXT, fee_multiplier REAL, why TEXT, phase INTEGER);
CREATE TABLE IF NOT EXISTS market_drops (id INTEGER PRIMARY KEY, watch_id INTEGER UNIQUE, ticker TEXT,
    dropped_iso TEXT, dropped_ts REAL, reason TEXT);
CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY, watch_id INTEGER, ticker TEXT,
    book_start_iso TEXT, book_start_ts REAL, book_end_iso TEXT, book_end_ts REAL,
    trades_start_iso TEXT, trades_start_ts REAL, trades_end_iso TEXT, trades_end_ts REAL, end_iso TEXT, end_ts REAL,
    trades_min_ts INTEGER, trades_ok INTEGER, book_state TEXT, yes_json TEXT, no_json TEXT,
    best_bid REAL, best_bid_size REAL, best_ask REAL, best_ask_size REAL, mid REAL, spread REAL, new_trades INTEGER);
CREATE INDEX IF NOT EXISTS snapshots_watch ON snapshots (watch_id, book_end_ts);
CREATE INDEX IF NOT EXISTS snapshots_ticker ON snapshots (ticker, trades_start_ts);
CREATE TABLE IF NOT EXISTS trades (trade_id TEXT PRIMARY KEY, ticker TEXT, created_iso TEXT, created_ts REAL,
    yes_price REAL, no_price REAL, count REAL, taker_side TEXT, is_block_trade INTEGER, seen_iso TEXT, seen_ts REAL,
    snapshot_id INTEGER);
CREATE INDEX IF NOT EXISTS trades_ticker ON trades (ticker, created_ts);
CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY, watch_id INTEGER, ticker TEXT, slot_iso TEXT,
    slot_ts REAL, decision_iso TEXT, decision_ts REAL, status TEXT, forced INTEGER, battery_id TEXT, state_json TEXT,
    request_sha256 TEXT, model_tag TEXT, model_digest TEXT, latency_s REAL, raw_response TEXT, answers_json TEXT,
    input_tokens INTEGER, output_tokens INTEGER, error TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS decisions_slot ON decisions (watch_id, slot_ts) WHERE forced = 0;
CREATE TABLE IF NOT EXISTS heartbeats (id INTEGER PRIMARY KEY, iso TEXT, ts REAL, watched INTEGER,
    kalshi_requests INTEGER, kalshi_failures INTEGER, model_calls INTEGER, paused INTEGER);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, iso TEXT, ts REAL, kind TEXT, ticker TEXT,
    detail_json TEXT);
CREATE VIEW IF NOT EXISTS market_watch AS SELECT m.*, d.dropped_iso, d.dropped_ts, d.reason AS drop_reason
    FROM markets m LEFT JOIN market_drops d ON d.watch_id = m.watch_id;
"""


class Store:
    """The sqlite record. Insert-only: a trigger on every table refuses UPDATE and DELETE."""

    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        if "phase" not in {r[1] for r in self.conn.execute("PRAGMA table_info(markets)")}:
            self.conn.execute("ALTER TABLE markets ADD COLUMN phase INTEGER")   # a record made before rev 2
        for table in TABLES:
            for verb in ("UPDATE", "DELETE"):
                self.conn.execute("CREATE TRIGGER IF NOT EXISTS no_%s_%s BEFORE %s ON %s BEGIN "
                                  "SELECT RAISE(ABORT, 'append-only record: no %s on %s'); END"
                                  % (verb.lower(), table, verb, table, verb, table))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def insert(self, table: str, row: dict, or_ignore: bool = False) -> int:
        """One row, committed. Returns its rowid (0 when ignored as a duplicate)."""
        assert table in TABLES
        cols = list(row)
        sql = "INSERT %sINTO %s (%s) VALUES (%s)" % ("OR IGNORE " if or_ignore else "", table, ",".join(cols),
                                                      ",".join("?" * len(cols)))
        with self.conn:
            cur = self.conn.execute(sql, [row[c] for c in cols])
        return cur.lastrowid if cur.rowcount else 0

    def watched(self) -> list[dict]:
        rows = self.conn.execute("SELECT watch_id, ticker, series, selected_ts, close_ts, market_json, phase "
                                 "FROM markets WHERE watch_id NOT IN (SELECT watch_id FROM market_drops) "
                                 "ORDER BY watch_id")
        return [{"watch_id": w, "ticker": t, "series": s, "selected_ts": sel, "close_ts": c, "market": json.loads(m),
                 "phase": p or 0} for w, t, s, sel, c, m, p in rows.fetchall()]

    def ever_watched(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT ticker FROM markets")}

    def last_trade_read(self, ticker: str) -> float | None:
        return self.conn.execute("SELECT MAX(trades_start_ts) FROM snapshots WHERE ticker = ? AND trades_ok = 1",
                                 (ticker,)).fetchone()[0]

    def history(self, watch_id: int, t: float) -> tuple[list[dict], list[dict], float]:
        """The watch's snapshots finished by t, its market's trades created and seen by t, and its close time."""
        ticker, close_ts = self.conn.execute("SELECT ticker, close_ts FROM markets WHERE watch_id = ?",
                                             (watch_id,)).fetchone()
        rows = self.conn.execute("SELECT book_end_ts, end_ts, trades_ok, trades_min_ts, trades_start_ts, yes_json, "
                                 "no_json FROM snapshots WHERE watch_id = ? AND end_ts <= ? ORDER BY book_end_ts",
                                 (watch_id, t)).fetchall()
        snaps = [{"book_end_ts": a, "end_ts": b, "trades_ok": c, "trades_min_ts": d, "trades_start_ts": e,
                  "yes": [tuple(x) for x in json.loads(y)], "no": [tuple(x) for x in json.loads(n)]}
                 for a, b, c, d, e, y, n in rows]
        trades = [{"created_ts": a, "seen_ts": b, "count": c, "taker_side": d} for a, b, c, d in self.conn.execute(
            "SELECT created_ts, seen_ts, count, taker_side FROM trades WHERE ticker = ? AND created_ts <= ? "
            "AND seen_ts <= ? ORDER BY created_ts", (ticker, t, t)).fetchall()]
        return snaps, trades, close_ts


# ============================================================================ the model

class ModelUnavailable(Exception):
    """The model could not be reached, or did not answer in MODEL_HARD_TIMEOUT_S."""


class ModelHttpError(Exception):
    """The model's server answered with an HTTP error."""


def _loopback(url: str) -> bool:
    return bool(re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?(/|$)", url))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def loopback_http(url: str, data: bytes | None, timeout: float) -> bytes:
    """GET (no body) or POST (JSON body) to the loopback model server; no redirects, at most 1 MB back."""
    if not _loopback(url):
        raise ValueError("the model is loopback-only")
    req = urllib.request.Request(url, data=data, method="GET" if data is None else "POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read(1_048_577)
    except urllib.error.HTTPError as e:
        raise ModelHttpError("HTTP %s from %s" % (e.code, url)) from None
    except (urllib.error.URLError, OSError) as e:
        raise ModelUnavailable("%s: %s" % (type(e).__name__, e)) from None
    if len(raw) > 1_048_576:
        raise ModelHttpError("oversize response")
    return raw


class ModelClient:
    """Nimble v3's typed endpoint on the local Ollama, called as `SystemOneMember` calls it (the pinned module's
    `pinned_tag`, `make_request` and `parse_response`). `transport(url, body or None, timeout) -> bytes`."""

    def __init__(self, transport: Callable[[str, bytes | None, float], bytes] | None = None,
                 base_url: str = MODEL_URL, clock: Callable[[], float] = time.time):
        if not _loopback(base_url):
            raise ValueError("the model is loopback-only")
        self.base, self.http, self.clock = base_url.rstrip("/"), transport or loopback_http, clock

    def pin(self) -> tuple[str | None, str | None, str | None]:
        """(None, digest, None) when the pinned tag is there with the pinned digest; else (status, digest, why)."""
        try:
            tags = json.loads(self.http(self.base + "/api/tags", None, 5.0))
            version = json.loads(self.http(self.base + "/api/version", None, 5.0)).get("version")
        except ModelUnavailable as e:
            return "model_unavailable", None, str(e)
        except (ModelHttpError, ValueError, AttributeError) as e:
            return "error", None, "tag check: %s" % e
        digest = next((r.get("digest") for r in (tags.get("models") or []) if isinstance(r, dict)
                       and r.get("name") == TAG), None) if isinstance(tags, dict) else None
        if v3.pinned_tag(tags, version) != TAG:
            return "tag_mismatch", digest, "%s missing, or its digest %s differs from the pin (Ollama %s)" % (
                TAG, digest, version)
        return None, digest, None

    def ask(self, body: bytes) -> tuple[bytes, float]:
        """POST the request; the raw answer and the seconds it took."""
        t0 = self.clock()
        raw = self.http(self.base + "/v1/systemone", body, MODEL_HARD_TIMEOUT_S)
        return raw, self.clock() - t0


# ============================================================================ the collector

class _SelectionAborted(Exception):
    pass


def stop_on_signals(stop: threading.Event) -> dict:
    """SIGINT and SIGTERM set `stop`; the loop then finishes its current write and ends. Returns the old handlers."""
    def handler(signum, frame):
        stop.set()
    return {sig: signal.signal(sig, handler) for sig in (signal.SIGINT, signal.SIGTERM)}


class Collector:
    """The loop. `clock()` is epoch seconds (injected in tests); `kalshi` is a `KalshiData` (its own pacing)."""

    def __init__(self, store: Store, kalshi: kd.KalshiData, model: ModelClient, *, clock: Callable[[], float] = time.time,
                 slots: int = WATCH_COUNT):
        self.store, self.kalshi, self.model, self.clock, self.slots = store, kalshi, model, clock, slots
        self.stop = threading.Event()
        self.paused_until: float | None = None
        self.consecutive_fails = 0
        self._last_beat: float | None = None
        self.n_requests = self.n_failures = self.n_model_calls = 0
        inner = kalshi.fetch

        def counted(url):
            self.n_requests += 1
            return inner(url)
        kalshi.fetch = counted

    # ---- small helpers
    def event(self, kind: str, ticker: str | None = None, **detail) -> None:
        self.store.insert("events", {**stamp("", self.clock()), "kind": kind, "ticker": ticker,
                                     "detail_json": json.dumps(detail, default=str)})

    def watched(self) -> list[dict]:
        return self.store.watched()

    def paused(self) -> bool:
        return self.paused_until is not None

    def _ok(self) -> None:
        self.consecutive_fails = 0

    def _fail(self, ticker: str | None, what: str, exc: Exception) -> None:
        self.n_failures += 1
        self.consecutive_fails += 1
        self.event("error", ticker, request=what, error="%s: %s" % (type(exc).__name__, exc))
        if self.consecutive_fails >= FAILS_BEFORE_PAUSE and not self.paused():
            now = self.clock()
            self.paused_until = now + PAUSE_S
            self.consecutive_fails = 0
            self.event("pause", None, failures=FAILS_BEFORE_PAUSE, until_ts=self.paused_until,
                       until_iso=iso(self.paused_until))

    def drop(self, w: dict, reason: str) -> None:
        self.store.insert("market_drops", {"watch_id": w["watch_id"], "ticker": w["ticker"],
                                           **stamp("dropped", self.clock()), "reason": reason})

    # ---- start and stop
    def start(self) -> None:
        """Record the start; after an earlier run, record the gap since its last heartbeat and stop watching the
        markets whose watch ran out meanwhile. The others are watched on."""
        now = self.clock()
        self.event("start", None, battery_id=BATTERY_ID, tag=TAG)
        last = self.store.conn.execute("SELECT MAX(ts) FROM heartbeats").fetchone()[0]
        if last is not None:
            self.event("gap", None, from_ts=last, from_iso=iso(last), to_ts=now, to_iso=iso(now), seconds=now - last)
        for w in self.watched():
            reason = self._expired(w, now)
            if reason:
                self.drop(w, reason + " (while stopped)")
        self._last_beat = now

    def finish(self, kind: str = "stop") -> None:
        self.event(kind, None)

    def run(self, wait: Callable[[float], Any] | None = None) -> None:
        """Cycle once a minute, just after the minute turns, until `stop` is set."""
        wait = wait or self.stop.wait
        while not self.stop.is_set():
            now = self.clock()
            wait((math.floor(now / 60) + 1) * 60 + 0.2 - now)
            if self.stop.is_set():
                break
            try:
                self.cycle()
            except Exception as e:                       # never let one cycle end the loop
                self.event("error", None, request="cycle", error="%s: %s" % (type(e).__name__, e))
        self.finish("stop")

    # ---- one minute
    def cycle(self, force_decision: bool = False, allow_select: bool = True) -> list[dict]:
        """For each watched market its snapshot and, on a slot (or always when forced), its decision right after
        it, so the state is never older than that one read; then drops, a selection if one is due, and a
        heartbeat. Returns the decisions made."""
        start = self.clock()
        minute = math.floor(start / 60) * 60
        if self._last_beat is not None and start - self._last_beat > 2 * 60:     # e.g. the Mac slept
            self.event("gap", None, from_ts=self._last_beat, from_iso=iso(self._last_beat), to_ts=start,
                       to_iso=iso(start), seconds=start - self._last_beat)
        if self.paused() and start >= self.paused_until:
            self.paused_until = None
            self.event("pause_end", None)
        watched = self.watched()
        phase = phase_of_minute(minute)
        done = {r[0] for r in self.store.conn.execute("SELECT watch_id FROM decisions WHERE slot_ts = ? AND "
                                                      "forced = 0", (minute,))}
        model_down: list[bool] = []
        made = []
        for w in watched:
            if self.stop.is_set():
                break
            if not self.paused():                       # paused: no request, but a slot still gets its row
                try:
                    self.snapshot(w)
                except Exception as e:
                    self._fail(w["ticker"], "snapshot", e)
            if (force_decision or w["phase"] == phase) and not self.stop.is_set():
                d = self._maybe_decide(w, minute, force_decision, done, model_down)
                if d is not None:
                    made.append(d)
        now = self.clock()
        for w in watched:
            reason = self._expired(w, now) or self._one_sided(w)
            if reason:
                self.drop(w, reason)
        if allow_select and not self.stop.is_set() and not self.paused() and self._selection_due(minute):
            self.select()
        self._last_beat = self.clock()
        self.store.insert("heartbeats", {**stamp("", self._last_beat), "watched": len(self.watched()),
                                         "kalshi_requests": self.n_requests, "kalshi_failures": self.n_failures,
                                         "model_calls": self.n_model_calls, "paused": int(self.paused())})
        self.n_requests = self.n_failures = self.n_model_calls = 0
        return made

    def _selection_due(self, minute: float) -> bool:
        """Six hours after the last selection (or at once on a new record), at the end of a minute whose next
        minute belongs to a least-used phase, so the selection's burst delays the fewest decisions."""
        last = self.store.conn.execute("SELECT MAX(started_ts) FROM selections").fetchone()[0]
        if last is None:
            return True
        if minute - last < RESELECT_S - 60:
            return False
        count = Counter({p: 0 for p in range(PHASES)})
        count.update(w["phase"] for w in self.watched())
        return count[phase_of_minute(minute + 60)] == min(count.values())

    @staticmethod
    def _expired(w: dict, now: float) -> str | None:
        if now >= w["selected_ts"] + WATCH_MAX_S:
            return "watched 12 hours"
        if now >= w["close_ts"] - STOP_BEFORE_CLOSE_S:
            return "30 minutes before it closes"
        return None

    def _one_sided(self, w: dict) -> str | None:
        """'one-sided' once every snapshot over the last 30 loop minutes (by minute number) was not two-sided."""
        c = self.store.conn
        last_two = c.execute("SELECT MAX(book_end_ts) FROM snapshots WHERE watch_id = ? AND book_state = 'two_sided'",
                             (w["watch_id"],)).fetchone()[0]
        first, newest = c.execute("SELECT MIN(book_end_ts), MAX(book_end_ts) FROM snapshots WHERE watch_id = ? AND "
                                  "book_state != 'two_sided' AND book_end_ts > ?",
                                  (w["watch_id"], last_two if last_two is not None else -1)).fetchone()
        if first is None:
            return None
        if math.floor(newest / 60) - math.floor(first / 60) >= ONE_SIDED_MINUTES:
            return "book one-sided or empty for 30 minutes"
        return None

    # ---- snapshots
    def snapshot(self, w: dict) -> int | None:
        """One snapshot of one market: its book, then its new trades, written in one transaction."""
        ticker = w["ticker"]
        b0 = self.clock()
        try:
            book = self.kalshi.orderbook(ticker, DEPTH)
        except Exception as e:
            self._fail(ticker, "orderbook", e)
            return None
        b1 = self.clock()
        self._ok()
        last = self.store.last_trade_read(ticker)
        min_ts = int(last - TRADE_OVERLAP_S) if last is not None else int(b1 - FIRST_TRADES_S)
        min_ts = max(min_ts, int(b1 - MAX_BACKFILL_S))
        t0 = self.clock()
        trades, ok = [], 1
        try:
            trades = self.kalshi.trades(ticker, min_ts)
            self._ok()
        except Exception as e:
            ok = 0
            self._fail(ticker, "trades", e)
        t1 = self.clock()
        s = book_summary(book)
        row = {"watch_id": w["watch_id"], "ticker": ticker, **stamp("book_start", b0), **stamp("book_end", b1),
               **(stamp("trades_start", t0) if ok else {"trades_start_iso": None, "trades_start_ts": None}),
               **(stamp("trades_end", t1) if ok else {"trades_end_iso": None, "trades_end_ts": None}),
               **stamp("end", t1), "trades_min_ts": min_ts if ok else None, "trades_ok": ok,
               "yes_json": json.dumps(book["yes"]), "no_json": json.dumps(book["no"]), **s}
        conn = self.store.conn
        fresh, seen = [], set()
        for x in trades:                                 # trades already stored by an earlier, overlapping read
            if x.get("ticker") not in (None, ticker) or x["trade_id"] in seen:
                continue
            seen.add(x["trade_id"])
            if conn.execute("SELECT 1 FROM trades WHERE trade_id = ?", (x["trade_id"],)).fetchone() is None:
                fresh.append(x)
        row["new_trades"] = len(fresh)
        with conn:                                       # the snapshot and its new trades in one transaction
            cols = list(row)
            sid = conn.execute("INSERT INTO snapshots (%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                               [row[c] for c in cols]).lastrowid
            for x in fresh:
                conn.execute(
                    "INSERT OR IGNORE INTO trades (trade_id, ticker, created_iso, created_ts, yes_price, no_price, count, "
                    "taker_side, is_block_trade, seen_iso, seen_ts, snapshot_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (x["trade_id"], ticker, x["created_time"], x["created_ts"], x["yes_price"], x["no_price"], x["count"],
                     x["taker_side"], None if x.get("is_block_trade") is None else int(bool(x["is_block_trade"])),
                     iso(t1), t1, sid))
        return sid

    # ---- decisions
    def _maybe_decide(self, w: dict, slot_ts: float, forced: bool, done: set, model_down: list) -> dict | None:
        """The slot's decision for one market, if it has 30 minutes of snapshots and none was made yet."""
        if not forced:
            if w["watch_id"] in done:
                return None
            first = self.store.conn.execute("SELECT MIN(book_end_ts) FROM snapshots WHERE watch_id = ?",
                                            (w["watch_id"],)).fetchone()[0]
            if first is None or first > slot_ts - WARMUP_S:
                return None
        try:
            return self.decide(w, slot_ts, forced=forced, model_down=model_down)
        except Exception as e:                           # one bad market never stops the slot
            self.event("error", w["ticker"], request="decision", error="%s: %s" % (type(e).__name__, e))
            return None

    def request_for(self, watch_id: int, t: float) -> bytes:
        """The exact request the model would get for this watch at time t."""
        snaps, trades, close_ts = self.store.history(watch_id, t)
        return request_bytes(state_at(snaps, trades, t, close_ts))

    def decide(self, w: dict, slot_ts: float, forced: bool = False, model_down: list | None = None) -> dict:
        """One decision: state, pin check, one call, one status, one row. `model_down` carries, within a slot, that
        the model was unreachable, so the other markets of that slot are marked without a call each."""
        model_down = model_down if model_down is not None else []
        t = self.clock()
        snaps, trades, close_ts = self.store.history(w["watch_id"], t)
        row = {"watch_id": w["watch_id"], "ticker": w["ticker"], **stamp("slot", slot_ts), **stamp("decision", t),
               "forced": int(forced), "battery_id": BATTERY_ID, "model_tag": TAG}
        newest = snaps[-1]["book_end_ts"] if snaps else None
        if newest is None or t - newest > STALE_S:
            row.update(status="stale_data", error="newest snapshot %s" % (
                "missing" if newest is None else "%.0f s old" % (t - newest)))
        else:
            state = state_at(snaps, trades, t, close_ts)
            body = request_bytes(state)
            row.update(state_json=json.dumps(state, separators=(",", ":")), request_sha256=sha256(body))
            if model_down:
                row.update(status="model_unavailable", error="model unreachable earlier in this slot; not called")
            else:
                self._call(row, body, model_down)
        self.store.insert("decisions", row)
        return row

    def _call(self, row: dict, body: bytes, model_down: list) -> None:
        status, digest, why = self.model.pin()
        row["model_digest"] = digest
        if status:
            row.update(status=status, error=why)
            if status == "model_unavailable":
                model_down.append(True)
            return
        self.n_model_calls += 1
        try:
            raw, latency = self.model.ask(body)
        except ModelUnavailable as e:
            model_down.append(True)
            row.update(status="model_unavailable", error=str(e))
            return
        except ModelHttpError as e:
            row.update(status="error", error=str(e))
            return
        row.update(raw_response=raw.decode("utf-8", errors="replace"), latency_s=latency)
        try:
            parsed = v3.parse_response(json.loads(raw), BATTERY)
        except (ValueError, TypeError, AttributeError) as e:      # ContractError is a ValueError
            row.update(status="error", error="unreadable answer: %s" % e)
            return
        row.update(answers_json=json.dumps(parsed["answers"]), input_tokens=parsed["usage"]["input_tokens"],
                   output_tokens=parsed["usage"]["output_tokens"], model_tag=parsed["model"],
                   status="late" if latency > MODEL_LIMIT_S else "ok")

    # ---- selection
    def select(self, dry_run: bool = False, max_pages: int | None = None) -> Selection | None:
        """Read the open markets closing in the window, apply the rule, and (unless a dry run) record the
        selection and start watching the chosen. `max_pages` is for smoke tests only."""
        t0 = self.clock()
        markets, pages = [], 0
        try:
            for page in self.kalshi.open_markets_pages(min_close_ts=int(t0 + MIN_CLOSE_S),
                                                       max_close_ts=int(t0 + MAX_CLOSE_S + 3600)):
                markets += page
                pages += 1
                if max_pages and pages >= max_pages:
                    break
            self._ok()
        except Exception as e:
            if not dry_run:
                self._fail(None, "markets", e)
            return None
        current = self.watched()
        excluded = self.store.ever_watched() - {w["ticker"] for w in current}

        def fetch(ticker):
            if self.paused():
                raise _SelectionAborted("paused after repeated failures")
            try:
                b = self.kalshi.orderbook(ticker, DEPTH)
                self._ok()
                return b
            except Exception as e:
                if not dry_run:
                    self._fail(ticker, "orderbook", e)
                return None
        try:
            sel = resolve_selection(markets, fetch, self.clock, slots=self.slots,
                                    watched=[w["market"] for w in current], excluded=excluded)
        except _SelectionAborted as e:                   # not recorded, so it is due again five minutes later
            if not dry_run:
                self.event("selection_aborted", None, why=str(e))
            return None
        sel.pages = pages
        if dry_run:
            return sel
        fees = {}
        by_ticker = {m["ticker"]: m for m in markets}
        for t in sel.chosen:
            s = series_of(by_ticker[t])
            if s not in fees:
                try:
                    fees[s] = self.kalshi.series(s)
                    self._ok()
                except Exception as e:
                    self._fail(t, "series", e)
                    fees[s] = {"fee_type": None, "fee_multiplier": None}
        t1 = self.clock()
        kept = {t: r for t, r in sel.reasons.items() if sel.kinds[t] not in OBJECT_KINDS}
        sid = self.store.insert("selections", {**stamp("started", t0), **stamp("finished", t1),
                                               "markets_seen": sel.markets_seen, "pages": pages,
                                               "books_read": len(sel.books), "chosen": len(sel.chosen),
                                               "reasons_json": json.dumps(kept),
                                               "reason_counts_json": json.dumps(Counter(sel.kinds.values()))})
        phases = dict(zip(sel.chosen, assign_phases([w["phase"] for w in current], len(sel.chosen))))
        for t in sel.chosen:
            m = by_ticker[t]
            close = kd.iso_to_ts(m["close_time"])
            fee = fees[series_of(m)]
            self.store.insert("markets", {"selection_id": sid, "ticker": t, "series": series_of(m),
                                          **stamp("selected", t1), **stamp("close", close), "market_json": json.dumps(m),
                                          "book_json": json.dumps(sel.books[t]), "fee_type": fee.get("fee_type"),
                                          "fee_multiplier": fee.get("fee_multiplier"), "why": sel.why[t],
                                          "phase": phases[t]})
        return sel


# ============================================================================ status

def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).date().isoformat()


def day_status(store: Store, now: float) -> list[dict]:
    """Per UTC day: decision slots expected (every slot of every watch on its own phase's five-minute grid, from
    30 minutes after its first snapshot to its drop or now) and collected (a non-forced decision with answers: `ok` or `late`), the share,
    the model's latency median and 95th percentile (nearest rank) over answered calls, the count of each status,
    Kalshi requests and failures (from the heartbeats) and error events."""
    c = store.conn
    days: dict[str, dict] = {}

    def day(d):
        return days.setdefault(d, {"day": d, "expected": 0, "collected": 0, "share": None,
                                   "statuses": dict.fromkeys(STATUSES, 0), "latencies": [], "requests": 0,
                                   "failures": 0, "errors": 0})
    rows = c.execute("SELECT m.watch_id, m.selected_ts, d.dropped_ts, (SELECT MIN(book_end_ts) FROM snapshots s "
                     "WHERE s.watch_id = m.watch_id), m.phase FROM markets m LEFT JOIN market_drops d "
                     "ON d.watch_id = m.watch_id").fetchall()
    for _, selected, dropped, first, phase in rows:
        start = (first if first is not None else selected) + WARMUP_S
        end = min(dropped if dropped is not None else now, now)
        offset = 60 * (phase or 0)                     # the market's own grid: minutes where minute mod 5 == phase
        s = math.ceil((start - offset) / SLOT_S) * SLOT_S + offset
        while s < end:
            day(_day(s))["expected"] += 1
            s += SLOT_S
    for slot, status, latency in c.execute("SELECT slot_ts, status, latency_s FROM decisions WHERE forced = 0"):
        d = day(_day(slot))
        d["statuses"][status] = d["statuses"].get(status, 0) + 1
        if status in ("ok", "late"):
            d["collected"] += 1
            if latency is not None:
                d["latencies"].append(latency)
    for ts, req, fail in c.execute("SELECT ts, kalshi_requests, kalshi_failures FROM heartbeats"):
        d = day(_day(ts))
        d["requests"] += req or 0
        d["failures"] += fail or 0
    for (ts,) in c.execute("SELECT ts FROM events WHERE kind = 'error'"):
        day(_day(ts))["errors"] += 1
    out = []
    for d in sorted(days):
        r = days[d]
        lat = sorted(r.pop("latencies"))
        r["share"] = r["collected"] / r["expected"] if r["expected"] else None
        r["latency_median"] = statistics.median(lat) if lat else None
        r["latency_p95"] = lat[math.ceil(0.95 * len(lat)) - 1] if lat else None
        out.append(r)
    return out


def format_status(rows: list[dict]) -> str:
    head = ("day         expected collected  share    ok  late unavail  tag stale error  lat_med  lat_p95  "
            "requests failures errors")
    lines = [head]
    for r in rows:
        s = r["statuses"]
        lines.append("%-10s  %8d %9d %6s %5d %5d %7d %4d %5d %5d %8s %8s %9d %8d %6d" % (
            r["day"], r["expected"], r["collected"], "-" if r["share"] is None else "%.1f%%" % (100 * r["share"]),
            s["ok"], s["late"], s["model_unavailable"], s["tag_mismatch"], s["stale_data"], s["error"],
            "-" if r["latency_median"] is None else "%.2f" % r["latency_median"],
            "-" if r["latency_p95"] is None else "%.2f" % r["latency_p95"], r["requests"], r["failures"], r["errors"]))
    return "\n".join(lines)


# ============================================================================ commands

def _print_selection(sel: Selection, out=None) -> None:
    out = out or sys.stdout
    print("markets seen %d on %d pages; books read %d; chosen %d" % (sel.markets_seen, sel.pages, len(sel.books),
                                                                     len(sel.chosen)), file=out)
    for t in sel.chosen:
        print("  CHOSEN %s: %s" % (t, sel.why[t]), file=out)
    print("not chosen, by reason:", file=out)
    for kind, n in Counter(sel.kinds.values()).most_common():
        print("  %-15s %d" % (kind, n), file=out)
    later = [(t, r) for t, r in sel.reasons.items() if sel.kinds[t] not in OBJECT_KINDS]
    print("candidates not chosen (first 40):", file=out)
    for t, r in later[:40]:
        print("  %s: %s" % (t, r), file=out)


def main(argv=None, *, kalshi: kd.KalshiData | None = None, model: ModelClient | None = None,
         clock: Callable[[], float] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="orderbook_loop", description="Collect Kalshi books and Nimble v3's battery.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("collect", "once", "select", "status"):
        p = sub.add_parser(name)
        p.add_argument("--db", default=str(DEFAULT_DB))
        if name in ("once", "select"):
            p.add_argument("--max-pages", type=int, default=None, help="smoke tests only: read fewer market pages")
            p.add_argument("--max-markets", type=int, default=WATCH_COUNT, help="smoke tests only: fewer places")
    a = ap.parse_args(argv)
    clock = clock or time.time
    store = Store(a.db)
    if a.cmd == "status":
        print(format_status(day_status(store, clock())))
        return 0
    kalshi = kalshi or kd.KalshiData(tries=3)
    model = model or ModelClient(clock=clock)
    col = Collector(store, kalshi, model, clock=clock, slots=getattr(a, "max_markets", WATCH_COUNT))
    if a.cmd == "select":
        sel = col.select(dry_run=True, max_pages=a.max_pages)
        if sel is None:
            print("could not read the open markets")
            return 1
        _print_selection(sel)
        return 0
    if a.cmd == "once":
        col.start()
        if not col.watched():
            sel = col.select(max_pages=a.max_pages)
            if sel is not None:
                _print_selection(sel)
        made = col.cycle(force_decision=True, allow_select=False)
        col.finish("once")
        for i, d in enumerate(made):
            print("decision %s: %s latency %s" % (d["ticker"], d["status"], d.get("latency_s")))
            if i == 0:
                print("  state:", d.get("state_json"))
                print("  answers:", d.get("answers_json"))
        return 0
    stop_on_signals(col.stop)
    col.start()
    col.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
