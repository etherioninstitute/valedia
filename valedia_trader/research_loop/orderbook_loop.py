"""The collector for the order-book loop: Kalshi books and trades once a minute, Nimble v3's battery every five.

The experiment is locked in docs/research/2026-11-13-orderbook-loop-prediction.md and every setting below comes
from it. This program only collects: it sends no order anywhere and scores nothing (the evaluation is a later
ticket). What it writes must let that evaluation rebuild every outcome, and nothing recorded at a time t may
depend on anything that happened after t.

    python -m valedia_trader.research_loop.orderbook_loop collect     # runs until SIGINT or SIGTERM
    python -m valedia_trader.research_loop.orderbook_loop once        # one full cycle, a smoke test
    python -m valedia_trader.research_loop.orderbook_loop select      # the markets it would watch, and why
    python -m valedia_trader.research_loop.orderbook_loop status      # coverage, latency and errors by UTC day

What happens each minute (`Collector.cycle`), in this order:

* snapshot  first, for every watched market (by watch number, the same order each minute) and then for every
            market in its tail (below): the ten best levels a side of its book and the trades since the last read
            (each read looks back 120 s before the previous read began, because trades can reach the tape late,
            and a trade is stored once by its `trade_id`), each request stamped with its start and end time. A
            book that is
            crossed (best bid at or above best ask), has a negative size or a malformed price is stored as sent
            (`raw_json`), marked `invalid` with the reason, and never decided on;
* decide    then, every five minutes per market, on that market's own phase (the minutes of the hour where
            minute mod 5 equals its phase, 0 to 4, given at selection and spread evenly), once it has 30 minutes
            of snapshots: build the numeric state from what was stored strictly before that moment
            (`state_at`: "now" is the newest snapshot, and every history reference, and every minute of the 30-
            and 60-minute windows, is aimed at that snapshot's time less the horizon), check the pinned model
            tag, ask the six questions of `BATTERY` in one call, and store
            the state, the answers and one status. The call has a hard limit of MODEL_LIMIT_S (10 s): an answer
            not in by then is abandoned and the slot is stored as `late`, with no answers. A call is not started
            when fewer than 12 s of the minute are left (also `late`), so the decisions never push the next
            minute's snapshots back. So when Kalshi answers slowly (0.9 s a request and more, the twenty
            markets' reads then take 36 s or longer) most slots are stored `late` without a call;
* drop      a market after 12 hours of watching, 30 minutes before it closes, or when its book has been one-sided,
            empty or invalid for 30 minutes. A dropped market is read on (book and trades once a minute, marked
            `tail`, no decisions) for 35 minutes after its drop or until it closes, whichever comes first, so the
            outcomes of its last decisions can be rebuilt;
* select    every six hours on a fixed grid (from the first selection; a grid point missed while stopped is made
            up at once, recorded off the grid, and the grid stays), fill the free places from all open markets
            by the rule of the prediction (`select_markets`), and give each new market the least-used phase
            (`assign_phases`). A watch's 12 hours run from the start of the selection that chose it, so they
            end on the grid: at a reselection the watches that have run 12 hours, or will within two minutes, are
            ended first and their places filled in the same transaction, and nothing is left unwatched in
            between. A market may be chosen again; when its snapshots ran on without a break (its tail included)
            it needs no new 30-minute warm-up. Whenever nothing is watched, for whatever reason, a selection runs
            at once, off the grid (recorded as `selection_off_grid`), and again each minute until one chooses
            something; after three selections in a row that chose nothing, every five minutes. While a selection
            reads, each new minute still gets its cycle (`_tick`);
* heartbeat one row with the requests and failures since the last one, written before any selection.

If a cycle runs into the next minute the next one starts at once (event `overrun`); a minute it ran right through
gets no cycle of its own, so each slot due in it gets its row, `late`, with the reason. Nothing but a stop signal
ends the loop: when the record cannot be written (a full disk) it writes one line to standard error, tries again
each minute, and records the gap once it can write again. A store outage (or a stop) longer than an hour loses
the trades older than that hour: a trades read never goes back more than an hour.

The clock: no row is stored unless the collector's clock is known to be right. Two checks decide that:
* a step detector, with no network: every reading of the wall clock also reads the monotonic clock, and when the
  wall clock has moved by more than STEP_TOLERANCE_S (2 s) more or less than the monotonic one since the previous
  reading, the clock has stepped (a sleep of the machine looks the same, and is handled the same);
* agreement with the exchange: every Kalshi answer carries an HTTP Date header, and our clock agrees when it is
  within CLOCK_AGREE_S (120 s) of it.
At start, after any step and whenever a Date disagrees, the collector is out of sync: it finishes nothing of the
cycle it is in and stores nothing more from it (a read whose request began before the step and ended after it is
thrown away), writes one `clock_off` event, and from then on makes one cheap Kalshi request a minute
(`/exchange/status`) and nothing else. When an answer's Date agrees and no step was seen during that request, it
records `clock_on` and resumes exactly as after a stop of that length: the gap is recorded, watches that ran out
are dropped and replaced, and holes in the history give nulls and warm-ups by the usual rules. For `status` a
period out of sync is a gap like any stop.

No single Kalshi request may take longer than 30 seconds (KALSHI_REQUEST_LIMIT_S): `collect`, `once` and
`select` use a transport that gives up at that limit (`bounded_fetch`), and the collector treats any answer that
took longer as a failed request. An abandoned request's thread ends by itself within about the same time; while
MAX_ABANDONED (4) are still running, no new request is started (each such is a failed request).

Where this differs from the prediction's words:

* a watch's 12 hours are counted from the start of the selection that chose it, so they can differ from 12 hours
  of reads by the length of a selection (seconds to a few minutes);
* volume and the ranking use Kalshi's cached 24-hour volume of the market object;
* places freed between grid points (a market dropped near its close or for a one-sided book) stay empty until the
  next grid point, unless nothing at all is watched;
* the cached-quote screen below.

The selection reads a live book only for markets whose cached quote (the market object, cached about five
seconds) is within five cents of the band (best bid at least 5, best ask at most 95, spread at most 20 cents);
the live book then decides by the rule itself. This screen is a practical addition to the prediction's rule:
reading the 45,000 books of all open markets is not possible, and each selection records how many markets it
screened out (`screened_out`).

Load at twenty markets. Kalshi: one book and one trades page per market per minute, 20 x 2 = 40 GET a minute,
under the 45 allowed; the client's pacing keeps them at most three a second, so they take about 14 s. A trades
page holds 1,000 trades, so a second page is needed only above 1,000 trades a minute in one market. Tails add
two GET a minute each while they last (35 minutes at most), still through the same pacing: ten markets ended at
a reselection and not chosen again add 20 GET a minute for 35 minutes, about 7 s more of reads. The selection
every six hours is a burst outside that steady state: one page per 1,000 open markets in the close window plus
one book per market read (it stops once the places are full), at the same three a second. Model: one call per
market every five minutes, and the twenty markets are spread over five phases, 20 / 5 = 4 a phase, so every
minute makes exactly 4 calls, under the 5 allowed, in that minute and not only on average (with fewer markets
after drops a phase holds at most 4, since a new market always takes the least-used phase). Measured on
2026-11-13 with the final battery (rev 4): about 14,000 input tokens and 3.4 to 3.5 s a call when warm (5.7 s cold),
so a minute takes about 14 s of reads plus 4 x 3.5 = 14 s of model time; at worst 14 s + 4 x 10 s = 54 s, still inside
the minute. Each call
is preceded by two loopback GETs (`/api/tags`, `/api/version`, 2 s limit each) to check the pin.

Hosts: Kalshi only at https://api.elections.kalshi.com/trade-api/v2 through `KalshiData` (GET only, no key);
the model only at http://127.0.0.1:11434 (`ModelClient` refuses any other address). No secrets, no child process.

The record is one sqlite file, append-only: triggers refuse every UPDATE and DELETE. Times are kept as UTC ISO
text and as epoch seconds. Two limits of that guarantee: an `INSERT OR REPLACE` (or `REPLACE`) from another
connection deletes the old row without firing the DELETE trigger (sqlite runs delete triggers on REPLACE only
with recursive triggers on), so it could get past them; this code never uses it. And the file grows: at twenty
markets 28,800 snapshots and 5,760 decisions a day (about 0.7 KB and 2 KB each on the smoke record of
2026-11-13), plus the trades and the indexes: up to about 1.2 GB after three weeks, depending on how much the
markets trade.
"""
from __future__ import annotations

import argparse
import email.utils
import hashlib
import http.client
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
           "series_of", "assign_phases", "phase_of_minute", "parse_book", "book_summary", "reference", "state_at",
           "request_bytes", "battery_id", "Store", "ModelClient", "ModelUnavailable", "ModelTimeout", "Collector",
           "day_status", "format_status", "window", "bounded_fetch", "kalshi_client", "stop_on_signals",
           "main"]

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
MODEL_LIMIT_S = 10.0              # hard limit: an answer not in after this is abandoned (`late`, no answers)
PIN_TIMEOUT_S = 2.0               # each of the two pin-check GETs
CALL_RESERVE_S = 2.0              # no call is started with less than MODEL_LIMIT_S + this left in the minute
FAILS_BEFORE_PAUSE, PAUSE_S = 5, 300
TRADE_OVERLAP_S = 120             # each trades read looks back 120 s before the previous one began (late tape)
FIRST_TRADES_S = 60               # a new market's first read goes back one minute
MAX_BACKFILL_S = 3600             # after a stop the tape is read again from where it stopped, at most an hour back
# Books are read only for markets whose cached quote is within five cents of the band (see the module docstring:
# a practical addition to the prediction's rule); the live book then decides.
CACHE_MARGIN_C = 5.0
TAIL_S = 35 * 60                  # a dropped market is read on for 35 minutes, or until it closes
REF_WINDOW_S = 30                 # a history reference is the snapshot nearest its target within 30 s either side
WINDOW_SHARE = 0.9                # a window number needs a snapshot in at least 90% of the window's minutes
HISTORY_S = 3600 + 2 * REF_WINDOW_S + 60   # how far back a decision reads the record
RESELECT_AHEAD_S = 120            # at a reselection, watches whose 12 hours end within this are ended first
KALSHI_REQUEST_LIMIT_S = 30.0     # no single Kalshi request may take longer: it is then a failed request
MAX_ABANDONED = 4                 # with this many abandoned requests still running, no new one is started
STEP_TOLERANCE_S = 2.0            # the wall clock moving this much more or less than the monotonic one: a step
CLOCK_AGREE_S = 120.0             # our clock agrees with Kalshi's Date header when within this of it
SYNC_PATH = "/exchange/status"    # the one cheap request a minute while out of sync

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
               "the mid and the spread are in cents from 0 to 100; changes are in cents and can be negative; "
               "sizes, depths and volumes are in contracts; "
               "times are in minutes; imbalances and shares are fractions from 0 to 1; an imbalance is the buy "
               "side's share: the size bid to buy yes divided by that size plus the size offered to sell yes; "
               "yes_taker_share is the share of the contracts traded in the window where the taker bought yes; "
               "spread_vs_60m_median is the spread now divided by its median over the last 60 minutes; a change "
               "is the value now minus the value then; null means not known yet. The bid is the best offer to buy "
               "yes and the ask the best offer to sell yes. ")

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
        "instructions": _STATE_TEXT + ("Question: this question concerns the case where a trade happens in the "
                                       "next 15 minutes. Take the first such trade; its direction is up if its "
                                       "taker bought yes and down if its taker bought no. Will the mid 15 minutes "
                                       "after that trade be beyond the mid just before it, in that trade's "
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


def _questions_bytes(battery: dict) -> bytes:
    """The questions exactly as they appear in the body sent to the model: the typed request's `questions`,
    compact JSON, in order, nothing sorted."""
    req = v3.make_request({"x": 1}, battery, tag=TAG)
    return json.dumps(req["questions"], separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def battery_id(battery: dict) -> str:
    """SHA-256 of the exact question bytes sent, in order (a different order of questions or options is a
    different battery)."""
    return sha256(_questions_bytes(battery))


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

    @property
    def screened_out(self) -> int:
        """Markets skipped on their cached quote, without reading their book."""
        return sum(1 for k in self.kinds.values() if k == "cached_quote")


def _vol24(m: dict) -> float | None:
    return _num(m.get("volume_24h_fp", m.get("volume_24h")))


def select_markets(markets: Iterable[dict], books: dict, now: float, *, slots: int = WATCH_COUNT,
                   watched: Iterable[dict] = ()) -> Selection:
    """The prediction's rule, as a pure function of the market objects, the live books read so far
    ({ticker: book or None when it could not be read}) and the time.

    In order: open; whole-cent prices (`linear_cent`); at least 500 contracts in 24 hours; closing between 3 hours
    and 30 days from now; not being watched; a cached quote near the band (see CACHE_MARGIN_C, a practical
    addition to the prediction's rule). A market watched before may be chosen again. The
    candidates left are taken most traded first, at most four from one series and twenty in all (the markets
    being watched keep their places and count toward their series); each needs a live book with a best bid of at
    least 10 cents, a best ask of at most 90 and a spread of 3 to 15 cents."""
    sel = Selection()
    watched = list(watched)
    watched_t = {m["ticker"] for m in watched}
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
        bid, ask = _num(m.get("yes_bid_dollars")), _num(m.get("yes_ask_dollars"))
        if bid is None or ask is None or not (_c(bid) >= MIN_BID_C - CACHE_MARGIN_C and
                                              _c(ask) <= MAX_ASK_C + CACHE_MARGIN_C and
                                              MIN_SPREAD_C - CACHE_MARGIN_C <= _c(ask) - _c(bid)
                                              <= MAX_SPREAD_C + CACHE_MARGIN_C):
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
        if b.get("invalid"):
            no(t, "invalid_book", "book invalid: %s" % b["invalid"])
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
                      watched: Iterable[dict] = ()) -> Selection:
    """Run `select_markets` to the end, reading each book it asks for, one at a time and only while places
    remain. `now` is a time or a clock, read once."""
    now = now() if callable(now) else now
    watched = list(watched)
    first = select_markets(markets, {}, now, slots=slots, watched=watched)
    early = {t for t, k in first.kinds.items() if k != "not_reached"}
    keep = [m for m in markets if m["ticker"] not in early]
    books: dict = {}
    while True:
        sel = select_markets(keep, books, now, slots=slots, watched=watched)
        if sel.need_book is None:
            break
        books[sel.need_book] = fetch_book(sel.need_book)
    for t in early:
        sel.reasons[t], sel.kinds[t] = first.reasons[t], first.kinds[t]
    sel.books, sel.markets_seen = books, first.markets_seen
    return sel


# ============================================================================ snapshots and the state

def parse_book(raw: dict, depth: int = DEPTH) -> tuple[dict, str | None]:
    """A raw `orderbook_fp` as sent, parsed to {"yes": [(price, size), ...], "no": [...]} best first, and the
    reason it is invalid (None when it is valid): a malformed price (not a number, or not between 0 and 1 dollars),
    a negative size, or a crossed book (best yes bid at or above the best yes ask). A malformed book parses to
    two empty sides."""
    empty: dict = {"yes": [], "no": []}
    out: dict = {}
    for side in ("yes", "no"):
        rows = raw.get(side + "_dollars") if isinstance(raw, dict) else None
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            return empty, "malformed %s side (%r)" % (side, rows)
        levels = []
        for r in rows:
            try:
                p, q = float(r[0]), float(r[1])
            except (TypeError, ValueError, IndexError, KeyError):
                return empty, "malformed level %r on the %s side" % (r, side)
            if not (math.isfinite(p) and math.isfinite(q)) or not 0.0 < p < 1.0:
                return empty, "malformed price %r on the %s side" % (r[0], side)
            if q < 0:
                return empty, "negative size %r on the %s side" % (r[1], side)
            levels.append((p, q))
        out[side] = sorted(levels, key=lambda lv: lv[0], reverse=True)[:depth]
    q = kd.best_quote(out)
    if q["bid"] is not None and q["ask"] is not None and q["bid"] >= q["ask"]:
        return out, "crossed: best bid %g at or above best ask %g" % (q["bid"], q["ask"])
    return out, None


def book_summary(book: dict, invalid: str | None = None) -> dict:
    """Best yes bid and ask with their sizes, the mid and the spread (dollars), and the book's state: two_sided,
    one_sided_yes (bids to buy yes only), one_sided_no, empty, or invalid (then nothing else is given)."""
    if invalid:
        return {"book_state": "invalid", "best_bid": None, "best_bid_size": None, "best_ask": None,
                "best_ask_size": None, "mid": None, "spread": None}
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


def reference(snaps: list[dict], target: float) -> dict | None:
    """The snapshot whose book read ended nearest `target`, among those within REF_WINDOW_S (30 s) either side;
    None when there is none. `snaps` are already only those finished strictly before the decision time."""
    best, best_d = None, None
    for s in snaps:
        d = abs(s["book_end_ts"] - target)
        if d <= REF_WINDOW_S and (best_d is None or d < best_d):
            best, best_d = s, d
    return best


def window(snaps: list[dict], minutes: int) -> dict[int, dict] | None:
    """The snapshots of a window of `minutes` minutes back from now (the last of `snaps`, sorted by book time):
    {0: now, k: the snapshot of minute k}. Minute k (1 to `minutes`) is covered by the snapshot nearest now's book
    time less 60k seconds within 30 s either side, by the same rule and choice as a history reference
    (`reference`), each snapshot covering one minute at most (minute 1 is taken first). None when fewer than 90% of
    the minutes are covered (27 of 30, 54 of 60)."""
    a = snaps[-1]["book_end_ts"]
    lo = a - 60 * minutes - REF_WINDOW_S
    free = [s for s in snaps[:-1] if s["book_end_ts"] >= lo]
    got = {0: snaps[-1]}
    for k in range(1, minutes + 1):
        ref = reference(free, a - 60 * k)
        if ref is not None:
            got[k] = ref
            free = [s for s in free if s is not ref]
    need = -(-9 * minutes // 10)                       # 90%, rounded up
    return got if len(got) - 1 >= need else None


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
    """The numeric state the model sees at time t: a pure function of the snapshots finished strictly before t and
    the trades created and seen strictly before t. Everything later is ignored, and so is every snapshot marked
    invalid. Numbers only, rounded; null where it cannot be computed yet; no ticker, title, date or time of day.

    "Now" is the newest valid snapshot finished before t. Every history reference (the mid 1, 5, 15, 30 and 60
    minutes back, and the far edge of each window) is aimed at that snapshot's book time less the horizon, and is
    the snapshot nearest that target among the earlier ones within 30 s either side (`reference`), so it is never
    the newest snapshot itself; none, and the number is null. The trade windows end at the same time.
    `max_1m_mid_move_30m_cents` and `spread_vs_60m_median` are computed from the snapshots of their window's
    minutes, found by the same rule (`window`: minute k is the snapshot nearest now less k minutes, each snapshot
    used once), and are null only when fewer than 90% of the minutes (27 of 30, 54 of 60) are covered. The move
    is the largest change of the mid from one covered minute to the next (now counting as minute 0); the median is
    that of the spreads of now and the 60 minutes' snapshots."""
    snaps = sorted((s for s in snapshots if s["end_ts"] < t and not s.get("invalid")), key=lambda s: s["book_end_ts"])
    tr = [x for x in trades if x["created_ts"] < t and x["seen_ts"] < t]
    out = dict.fromkeys(STATE_KEYS)
    out["minutes_to_close"] = _r((close_ts - t) / 60.0, 0)
    a = snaps[-1]["book_end_ts"] if snaps else t        # the time every reference and window is aimed from
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
                ref = reference(snaps[:-1], a - 60 * k)
                ref_mid = _mid_c(ref) if ref is not None else None
                out["mid_change_%dm_cents" % k] = _r(mid - ref_mid, 1) if ref_mid is not None else None
            w30 = window(snaps, 30)
            if w30 is not None:                          # one-minute moves: from each covered minute to the next
                mids = {k: _mid_c(s) for k, s in w30.items()}
                moves = [abs(mids[k - 1] - mids[k]) for k in range(1, 31)
                         if mids.get(k) is not None and mids.get(k - 1) is not None]
                out["max_1m_mid_move_30m_cents"] = _r(max(moves), 1) if moves else None
            w60 = window(snaps, 60)
            if w60 is not None:                          # the median over now and the covered minutes
                spreads = [x for x in (_spread_c(s) for s in w60.values()) if x is not None]
                med = statistics.median(spreads) if spreads else 0
                out["spread_vs_60m_median"] = _r(spread / med, 2) if med > 0 else None
    covered = _covered_from(snaps)
    for w in (5, 15):
        if covered is None or covered > a - 60 * w:
            continue
        win = [x for x in tr if a - 60 * w < x["created_ts"] <= a]
        vol = sum(x["count"] for x in win)
        out["trades_%dm" % w], out["volume_%dm" % w] = len(win), _r(vol, 0)
        out["yes_taker_share_%dm" % w] = (_r(sum(x["count"] for x in win if x["taker_side"] == "yes") / vol, 2)
                                          if vol > 0 else None)
    return out


# ============================================================================ the record

_SCHEMA = """
CREATE TABLE IF NOT EXISTS selections (id INTEGER PRIMARY KEY, started_iso TEXT, started_ts REAL, finished_iso TEXT,
    finished_ts REAL, markets_seen INTEGER, pages INTEGER, books_read INTEGER, chosen INTEGER, reasons_json TEXT,
    reason_counts_json TEXT, kind TEXT, screened_out INTEGER, ended INTEGER);
CREATE TABLE IF NOT EXISTS markets (watch_id INTEGER PRIMARY KEY, selection_id INTEGER, ticker TEXT, series TEXT,
    selected_iso TEXT, selected_ts REAL, close_iso TEXT, close_ts REAL, market_json TEXT, book_json TEXT,
    fee_type TEXT, fee_multiplier REAL, why TEXT, phase INTEGER, watch_start_iso TEXT, watch_start_ts REAL,
    warm_from_ts REAL);
CREATE TABLE IF NOT EXISTS market_drops (id INTEGER PRIMARY KEY, watch_id INTEGER UNIQUE, ticker TEXT,
    dropped_iso TEXT, dropped_ts REAL, reason TEXT);
CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY, watch_id INTEGER, ticker TEXT,
    book_start_iso TEXT, book_start_ts REAL, book_end_iso TEXT, book_end_ts REAL,
    trades_start_iso TEXT, trades_start_ts REAL, trades_end_iso TEXT, trades_end_ts REAL, end_iso TEXT, end_ts REAL,
    trades_min_ts INTEGER, trades_ok INTEGER, book_state TEXT, yes_json TEXT, no_json TEXT,
    best_bid REAL, best_bid_size REAL, best_ask REAL, best_ask_size REAL, mid REAL, spread REAL, new_trades INTEGER,
    tail INTEGER DEFAULT 0, invalid_reason TEXT, raw_json TEXT);
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
# Indexes, made after the columns a record from an earlier revision gets added (`Store.__init__`).
_INDEXES = """
CREATE INDEX IF NOT EXISTS snapshots_watch ON snapshots (watch_id, book_end_ts);
CREATE INDEX IF NOT EXISTS snapshots_ticker ON snapshots (ticker, trades_start_ts);
CREATE INDEX IF NOT EXISTS snapshots_ticker_end ON snapshots (ticker, book_end_ts);
CREATE INDEX IF NOT EXISTS drops_ts ON market_drops (dropped_ts);
"""
# Columns added after a record may have been started: (table, column, type).
_ADDED = (("markets", "phase", "INTEGER"), ("markets", "watch_start_iso", "TEXT"), ("markets", "watch_start_ts", "REAL"),
          ("markets", "warm_from_ts", "REAL"), ("selections", "kind", "TEXT"), ("selections", "screened_out", "INTEGER"),
          ("selections", "ended", "INTEGER"), ("snapshots", "tail", "INTEGER DEFAULT 0"),
          ("snapshots", "invalid_reason", "TEXT"), ("snapshots", "raw_json", "TEXT"))


class Store:
    """The sqlite record. Insert-only: a trigger on every table refuses UPDATE and DELETE."""

    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")    # with WAL: durable at each checkpoint, a lot faster
        self.conn.executescript(_SCHEMA)
        for table, col, kind in _ADDED:                   # a record made by an earlier revision
            if col not in {r[1] for r in self.conn.execute("PRAGMA table_info(%s)" % table)}:
                self.conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, kind))
        self.conn.executescript(_INDEXES)
        for table in TABLES:
            for verb in ("UPDATE", "DELETE"):
                self.conn.execute("CREATE TRIGGER IF NOT EXISTS no_%s_%s BEFORE %s ON %s BEGIN "
                                  "SELECT RAISE(ABORT, 'append-only record: no %s on %s'); END"
                                  % (verb.lower(), table, verb, table, verb, table))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def insert(self, table: str, row: dict, or_ignore: bool = False, commit: bool = True) -> int:
        """One row, committed (or, with commit False, left to the caller's transaction). Returns its rowid (0 when
        ignored as a duplicate)."""
        assert table in TABLES
        cols = list(row)
        sql = "INSERT %sINTO %s (%s) VALUES (%s)" % ("OR IGNORE " if or_ignore else "", table, ",".join(cols),
                                                      ",".join("?" * len(cols)))
        if not commit:
            cur = self.conn.execute(sql, [row[c] for c in cols])
        else:
            with self.conn:
                cur = self.conn.execute(sql, [row[c] for c in cols])
        return cur.lastrowid if cur.rowcount else 0

    def watched(self) -> list[dict]:
        """The watches not dropped."""
        rows = self.conn.execute("SELECT watch_id, ticker, series, selected_ts, close_ts, market_json, phase, "
                                 "watch_start_ts, warm_from_ts FROM markets "
                                 "WHERE watch_id NOT IN (SELECT watch_id FROM market_drops) ORDER BY watch_id")
        return [{"watch_id": w, "ticker": t, "series": s, "selected_ts": sel, "close_ts": c, "market": json.loads(m),
                 "phase": p or 0, "watch_start_ts": ws if ws is not None else sel, "warm_from_ts": wf}
                for w, t, s, sel, c, m, p, ws, wf in rows.fetchall()]

    def watched_at(self, t: float) -> list[dict]:
        """The watches that were watched at time t: chosen by then and not dropped before it."""
        rows = self.conn.execute("SELECT m.watch_id, m.ticker, m.phase, m.warm_from_ts FROM markets m LEFT JOIN "
                                 "market_drops d ON d.watch_id = m.watch_id WHERE m.selected_ts <= ? AND "
                                 "(d.dropped_ts IS NULL OR d.dropped_ts > ?) ORDER BY m.watch_id", (t, t))
        return [{"watch_id": w, "ticker": tk, "phase": p or 0, "warm_from_ts": wf} for w, tk, p, wf in rows.fetchall()]

    def tails(self, now: float, exclude: Iterable[str] = ()) -> list[dict]:
        """The dropped markets still to be read: dropped less than TAIL_S ago, not closed, not watched again; one
        per ticker (its latest watch), in the order of their drops."""
        exclude = set(exclude)
        out: dict = {}
        for wid, t, close_ts, dropped in self.conn.execute(
                "SELECT m.watch_id, m.ticker, m.close_ts, d.dropped_ts FROM market_drops d JOIN markets m "
                "ON m.watch_id = d.watch_id WHERE d.dropped_ts > ? AND d.dropped_ts <= ? AND m.close_ts > ? "
                "ORDER BY d.id", (now - TAIL_S, now, now)).fetchall():
            if t not in exclude:
                out.pop(t, None)
                out[t] = {"watch_id": wid, "ticker": t, "close_ts": close_ts, "dropped_ts": dropped, "tail": True}
        return list(out.values())

    def last_trade_read(self, ticker: str) -> float | None:
        return self.conn.execute("SELECT MAX(trades_start_ts) FROM snapshots WHERE ticker = ? AND trades_ok = 1",
                                 (ticker,)).fetchone()[0]

    def warm_from(self, ticker: str, t: float) -> float | None:
        """For a market chosen at t: when its run of snapshots began, if its snapshots (an earlier watch or that
        watch's tail) ran up to t without a break; None when it starts afresh (its warm-up then runs from its
        own first snapshot)."""
        wid, newest = self.conn.execute("SELECT watch_id, MAX(book_end_ts) FROM snapshots WHERE ticker = ? AND "
                                        "book_end_ts <= ?", (ticker, t)).fetchone()
        if newest is None or newest < t - STALE_S:
            return None
        warm, first = self.conn.execute("SELECT warm_from_ts, (SELECT MIN(book_end_ts) FROM snapshots WHERE "
                                        "watch_id = ?) FROM markets WHERE watch_id = ?", (wid, wid)).fetchone()
        return warm if warm is not None else first

    def history(self, watch_id: int, t: float) -> tuple[list[dict], list[dict], float]:
        """The market's snapshots finished strictly before t (from every watch of it and their tails, the last
        HISTORY_S only), its trades created and seen before t over the same span, and its close time."""
        ticker, close_ts = self.conn.execute("SELECT ticker, close_ts FROM markets WHERE watch_id = ?",
                                             (watch_id,)).fetchone()
        rows = self.conn.execute("SELECT book_end_ts, end_ts, trades_ok, trades_min_ts, trades_start_ts, yes_json, "
                                 "no_json, invalid_reason FROM snapshots WHERE ticker = ? AND book_end_ts >= ? AND "
                                 "end_ts < ? ORDER BY book_end_ts", (ticker, t - HISTORY_S, t)).fetchall()
        snaps = [{"book_end_ts": a, "end_ts": b, "trades_ok": c, "trades_min_ts": d, "trades_start_ts": e,
                  "yes": [tuple(x) for x in json.loads(y)], "no": [tuple(x) for x in json.loads(n)], "invalid": bad}
                 for a, b, c, d, e, y, n, bad in rows]
        trades = [{"created_ts": a, "seen_ts": b, "count": c, "taker_side": d} for a, b, c, d in self.conn.execute(
            "SELECT created_ts, seen_ts, count, taker_side FROM trades WHERE ticker = ? AND created_ts >= ? AND "
            "created_ts < ? AND seen_ts < ? ORDER BY created_ts", (ticker, t - HISTORY_S, t, t)).fetchall()]
        return snaps, trades, close_ts


# ============================================================================ the model

class ModelUnavailable(Exception):
    """The model could not be reached, or the connection failed on the way."""


class ModelHttpError(Exception):
    """The model's server answered with an HTTP error."""


class ModelTimeout(Exception):
    """No answer within MODEL_LIMIT_S; the call was abandoned."""

    def __init__(self, latency: float):
        super().__init__("no answer within %g s (abandoned after %.1f s)" % (MODEL_LIMIT_S, latency))
        self.latency = latency


def _loopback(url: str) -> bool:
    return bool(re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?(/|$)", url))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def loopback_http(url: str, data: bytes | None, timeout: float) -> bytes:
    """GET (no body) or POST (JSON body) to the loopback model server; no redirects, at most 1 MB back. A timeout
    comes out as TimeoutError, any other failure to get an answer (refused, reset, a broken HTTP exchange) as
    ModelUnavailable, an HTTP error status as ModelHttpError."""
    if not _loopback(url):
        raise ValueError("the model is loopback-only")
    req = urllib.request.Request(url, data=data, method="GET" if data is None else "POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read(1_048_577)
    except urllib.error.HTTPError as e:
        raise ModelHttpError("HTTP %s from %s" % (e.code, url)) from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise TimeoutError(str(e.reason)) from None
        raise ModelUnavailable("%s: %s" % (type(e).__name__, e)) from None
    except TimeoutError:
        raise
    except (OSError, http.client.HTTPException) as e:
        raise ModelUnavailable("%s: %s" % (type(e).__name__, e)) from None
    if len(raw) > 1_048_576:
        raise ModelHttpError("oversize response")
    return raw


_UNREACHABLE = (ModelUnavailable, OSError, http.client.HTTPException)


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
            tags = json.loads(self.http(self.base + "/api/tags", None, PIN_TIMEOUT_S))
            version = json.loads(self.http(self.base + "/api/version", None, PIN_TIMEOUT_S)).get("version")
        except _UNREACHABLE as e:
            return "model_unavailable", None, "tag check: %s: %s" % (type(e).__name__, e)
        except Exception as e:                           # anything else still gives the slot its row
            return "error", None, "tag check: %s: %s" % (type(e).__name__, e)
        digest = next((r.get("digest") for r in (tags.get("models") or []) if isinstance(r, dict)
                       and r.get("name") == TAG), None) if isinstance(tags, dict) else None
        if v3.pinned_tag(tags, version) != TAG:
            return "tag_mismatch", digest, "%s missing, or its digest %s differs from the pin (Ollama %s)" % (
                TAG, digest, version)
        return None, digest, None

    def ask(self, body: bytes) -> tuple[bytes, float]:
        """POST the request; the raw answer and the seconds it took. The call runs in its own thread and is
        abandoned (ModelTimeout) if no answer is in after MODEL_LIMIT_S, whatever the socket does."""
        t0 = self.clock()
        box: dict = {}

        def call():
            try:
                box["raw"] = self.http(self.base + "/v1/systemone", body, MODEL_LIMIT_S)
            except BaseException as e:                   # handed to the caller below
                box["exc"] = e
        th = threading.Thread(target=call, name="model-call", daemon=True)
        th.start()
        th.join(MODEL_LIMIT_S + 0.5)
        latency = self.clock() - t0
        if th.is_alive() or isinstance(box.get("exc"), TimeoutError):
            raise ModelTimeout(latency)
        if "exc" in box:
            raise box["exc"]
        if latency > MODEL_LIMIT_S:
            raise ModelTimeout(latency)
        return box["raw"], latency


# ============================================================================ the collector

def bounded_fetch(fetch: Callable[[str], Any], limit: float = KALSHI_REQUEST_LIMIT_S) -> Callable[[str], Any]:
    """`fetch` with a hard limit on each request: an answer not in after `limit` seconds raises TimeoutError (a
    failed request) and is abandoned. The request runs in a short-lived daemon thread; with `kalshi_client` its own
    socket timeout is the same limit, so that thread ends by itself soon after. While MAX_ABANDONED abandoned
    threads are still running, no new request is started: that too raises TimeoutError. `get.alive()` counts them."""
    abandoned: list[threading.Thread] = []

    def alive() -> int:
        abandoned[:] = [th for th in abandoned if th.is_alive()]
        return len(abandoned)

    def get(url: str):
        if alive() >= MAX_ABANDONED:
            raise TimeoutError("%d abandoned Kalshi requests still running; none started" % MAX_ABANDONED)
        box: dict = {}

        def work():
            try:
                box["body"] = fetch(url)
            except BaseException as e:               # handed to the caller below
                box["exc"] = e
        th = threading.Thread(target=work, name="kalshi-get", daemon=True)
        th.start()
        th.join(limit)
        if th.is_alive():
            abandoned.append(th)
            raise TimeoutError("no answer from Kalshi within %g s" % limit)
        if "exc" in box:
            raise box["exc"]
        return box["body"]
    get.limit, get.alive = limit, alive
    return get


def kalshi_client() -> kd.KalshiData:
    """The client `collect`, `once` and `select` use: keyless GET at the client's own pacing, each request at most
    KALSHI_REQUEST_LIMIT_S, each answer with its Date header."""
    return kd.KalshiData(fetch=bounded_fetch(lambda url: kd.urllib_get_dated(url, timeout=KALSHI_REQUEST_LIMIT_S)),
                         tries=3)


class _OutOfSync(BaseException):
    """The clock is not known to be right: the cycle stops where it is and nothing more of it is stored. A
    BaseException, so that no `except Exception` on the way can keep the cycle going."""


class _SelectionAborted(Exception):
    pass


def stop_on_signals(stop: threading.Event) -> dict:
    """SIGINT and SIGTERM set `stop`; the loop then finishes its current write and ends. Returns the old handlers."""
    def handler(signum, frame):
        stop.set()
    return {sig: signal.signal(sig, handler) for sig in (signal.SIGINT, signal.SIGTERM)}


class Collector:
    """The loop. `clock()` is epoch seconds and `monotonic` the monotonic clock beside it (both injected in tests);
    `kalshi` is a `KalshiData` (its own pacing)."""

    def __init__(self, store: Store, kalshi: kd.KalshiData, model: ModelClient, *, clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic, slots: int = WATCH_COUNT):
        self.store, self.kalshi, self.model, self.slots = store, kalshi, model, slots
        self._wall, self._mono = clock, monotonic
        self.stop = threading.Event()
        self.paused_until: float | None = None
        self.consecutive_fails = 0
        self._last_beat: float | None = None
        self._cycle_start: float | None = None      # when the latest cycle began
        self._cycle_end: float | None = None        # when the latest cycle ended
        self._minute: float | None = None           # the minute of the latest cycle
        self._selecting = False                     # a selection is reading: cycles still run, but select nothing
        self._dry = False
        self._depth = 0                             # cycles running (a selection's `_tick` runs one inside another)
        # the clock (see the module docstring): nothing is stored unless `synced`
        self.synced = False
        self._prev: tuple[float, float] | None = None   # the previous (wall, monotonic) reading
        self._stepped = False                       # a step seen since the flag was last cleared
        self._disagree = False                      # the latest Date header disagreed
        self._dated = False                         # the latest answer had a Date header
        self.clock_offset: float | None = None      # our clock less the latest Date header, in seconds
        self._started = False                       # the `start` event is written once the clock is first right
        self._off_noted = False                     # the `clock_off` event of this period is written
        self.n_requests = self.n_failures = self.n_model_calls = 0
        inner = kalshi.fetch

        def counted(url):
            self.clock()                                 # in sync: a step before the request stops it here
            self.n_requests += 1
            m0 = self._mono()
            answer = inner(url)
            t1, took = self._read(), self._mono() - m0
            date = answer[1] if isinstance(answer, tuple) else None
            self._dated = date is not None
            if date is not None:
                self._agree(date, t1)
            self.clock()                                 # a step during the request, or a Date that disagrees
            if took > KALSHI_REQUEST_LIMIT_S:            # an answer this slow is a failed request
                raise TimeoutError("request took %.1f s, over the %g s limit" % (took, KALSHI_REQUEST_LIMIT_S))
            return answer
        kalshi.fetch = counted

    # ---- the clock
    def _read(self) -> float:
        """One reading of the wall clock, with the monotonic one beside it; a difference in how far they moved
        since the previous reading of more than STEP_TOLERANCE_S is a step."""
        w, m = self._wall(), self._mono()
        if self._prev is not None and abs((w - self._prev[0]) - (m - self._prev[1])) > STEP_TOLERANCE_S:
            self._stepped = True
        self._prev = (w, m)
        return w

    def clock(self) -> float:
        """The wall clock. While in sync, a step or a disagreeing Date stops the cycle here (`_OutOfSync`)."""
        w = self._read()
        if self.synced and (self._stepped or self._disagree):
            raise _OutOfSync("the clock stepped" if self._stepped else
                             "the clock is %.0f s off Kalshi's Date header" % self.clock_offset)
        return w

    def _agree(self, date: str, t: float) -> None:
        try:
            kalshi_ts = email.utils.parsedate_to_datetime(date).timestamp()
        except (TypeError, ValueError):
            return
        self.clock_offset = t - kalshi_ts
        self._disagree = abs(self.clock_offset) > CLOCK_AGREE_S

    def _guard(self) -> None:
        """Before every write: the clock is known to be right, now."""
        self.clock()
        if not self.synced:
            raise _OutOfSync("the clock is not known to be right")

    def _go_off(self, why: str) -> None:
        """Out of sync: nothing more is stored; one `clock_off` event (if the store can take it); the next cycles
        only check the clock. The cycle in progress is forgotten, as after a stop."""
        self.synced = False
        self._minute = self._cycle_start = self._cycle_end = None
        self.paused_until, self.consecutive_fails = None, 0
        if not self._off_noted:
            self._off_noted = True
            try:
                self.store.insert("events", {**stamp("", self._read()), "kind": "clock_off", "ticker": None,
                                             "detail_json": json.dumps({"why": why, "offset_s": self.clock_offset})})
            except Exception:                            # a store that cannot be written: nothing to add
                pass

    def _resync(self) -> bool:
        """The one cheap request; in sync again when its answer's Date agrees and no step was seen meanwhile.
        Then resume as after a stop (`_resume`). A step before the check is taken in by this reading: the Date
        then says whether the clock is right."""
        self._read()
        self._stepped = self._disagree = self._dated = False
        try:
            self.kalshi.get_json(SYNC_PATH)
        except Exception:
            return False
        if self._stepped or self._disagree or not self._dated:
            return False
        self.synced, self._off_noted = True, False
        self._resume()
        return True

    def _resume(self) -> None:
        """Record the start (the first time) or `clock_on`; after earlier rows, record the gap since the last
        heartbeat and stop watching the markets whose watch ran out meanwhile. The others are watched on. With
        nothing watched, the cycle selects at once."""
        now = self.clock()
        if not self._started:
            self._started = True
            self.event("start", None, battery_id=BATTERY_ID, tag=TAG)
        else:
            self.event("clock_on", None, offset_s=self.clock_offset)
        last = self.store.conn.execute("SELECT MAX(ts) FROM heartbeats").fetchone()[0]
        if last is not None:
            self.event("gap", None, from_ts=last, from_iso=iso(last), to_ts=now, to_iso=iso(now), seconds=now - last)
        for w in self.watched():
            reason = self._expired(w, now)
            if reason:                                   # recorded as ending when it ran out, not at the restart
                ran_out = min(x for x in (w["watch_start_ts"] + WATCH_MAX_S, w["close_ts"] - STOP_BEFORE_CLOSE_S)
                              if x <= now)
                self.drop(w, reason + " (while stopped)", at=max(ran_out, last if last is not None else ran_out))
        self._last_beat = now

    # ---- small helpers
    def event(self, kind: str, ticker: str | None = None, **detail) -> None:
        self._guard()
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

    def drop(self, w: dict, reason: str, at: float | None = None) -> None:
        self._guard()
        self.store.insert("market_drops", {"watch_id": w["watch_id"], "ticker": w["ticker"],
                                           **stamp("dropped", self.clock() if at is None else at), "reason": reason})

    # ---- start and stop
    def start(self) -> None:
        """Out of sync until the clock is known to be right; then the start is recorded and the record resumed
        (`_resume`). If it is not right yet, one `clock_off` event, and each cycle checks again."""
        self.synced = False
        try:
            if not self._resync():
                self._go_off("at start: " + ("the clock stepped" if self._stepped else
                                             "no Date header" if not self._dated else
                                             "the clock is %.0f s off Kalshi's Date header" % self.clock_offset
                                             if self._disagree else "Kalshi could not be reached"))
        except _OutOfSync as e:
            self._go_off(str(e))

    def finish(self, kind: str = "stop") -> None:
        if self.synced:
            self.event(kind, None)

    def run(self, wait: Callable[[float], Any] | None = None) -> None:
        """Cycle once a minute, just after the minute turns, until `stop` is set. With nothing watched the first
        cycle runs at once (it selects). A cycle that ran into the next minute is followed at once (`overrun`)."""
        wait = wait or self.stop.wait
        first = True
        while not self.stop.is_set():
            try:
                now = self._read()
                if not (first and not self.watched()):
                    if self._minute is None or now < self._cycle_start:
                        target = math.floor(now / 60) * 60 + 60
                    else:
                        target = self._minute + 60
                    if now < target:
                        wait(target + 0.2 - now)
                        if self.stop.is_set():
                            break
                    elif self.synced:
                        try:
                            self.event("overrun", None, minute_ts=self._minute, minute_iso=iso(self._minute),
                                       late_s=now - target)
                        except (Exception, _OutOfSync):  # the cycle below reports it
                            pass
                first = False
                self.cycle()
            except Exception as e:                       # nothing but a stop ends the loop
                self._record_failure(e)
            except _OutOfSync as e:
                self._go_off(str(e))
        try:
            self.finish("stop")
        except (Exception, _OutOfSync) as e:
            self._record_failure(e)

    def _record_failure(self, exc: BaseException) -> None:
        """Record a failed cycle; when even that cannot be written (a full disk), one line to standard error. The
        loop tries again at the next minute and, once it can write, records the gap."""
        try:
            self.event("error", None, request="cycle", error="%s: %s" % (type(exc).__name__, exc))
        except (Exception, _OutOfSync) as e:
            print("orderbook_loop %s: cannot write the record (%s: %s); trying again next minute"
                  % (iso(self._read()), type(e).__name__, e), file=sys.stderr, flush=True)

    # ---- one minute
    def cycle(self, force_decision: bool = False, allow_select: bool = True) -> list[dict]:
        """Out of sync: only the one cheap request (`_resync`). In sync: first every snapshot (the watched markets
        by watch number, then the tails), then this minute's decisions from the record as it stands at each call,
        then drops, a selection if one is due, and a heartbeat. Returns the decisions made."""
        self._depth += 1
        try:
            if not self.synced:
                if self._depth > 1 or not self._resync():
                    self._go_off("still out of sync")
                    return []
            return self._cycle(force_decision, allow_select)
        except _OutOfSync as e:
            if self._depth > 1:                          # inside a selection: the outer cycle stops too
                raise
            self._go_off(str(e))
            return []
        finally:
            self._depth -= 1
            if self.synced:
                self._cycle_end = self._read()

    def _cycle(self, force_decision: bool, allow_select: bool) -> list[dict]:
        start = self.clock()
        minute = math.floor(start / 60) * 60
        prev_minute, prev_end = self._minute, self._cycle_end
        self._cycle_start, self._minute = start, minute   # first, so a failed write cannot make the loop spin
        if self._depth > 1:                              # inside a selection: the cycle around it is still running
            prev_end = start
        if self._last_beat is not None and start - self._last_beat > 2 * 60:
            self.event("gap", None, from_ts=self._last_beat, from_iso=iso(self._last_beat), to_ts=start,
                       to_iso=iso(start), seconds=start - self._last_beat)     # e.g. a stop of the store
        if self.paused() and start >= self.paused_until:
            self.paused_until = None
            self.event("pause_end", None)
        if prev_minute is not None and prev_end is not None and not force_decision:
            self._skipped(prev_minute, min(minute, math.floor(prev_end / 60) * 60 + 60))
        watched = self.watched()
        tails = self.store.tails(start, exclude={w["ticker"] for w in watched})
        for w in watched + tails:
            if self.stop.is_set():
                break
            if not self.paused():                       # paused: no request, but a slot still gets its row
                try:
                    self.snapshot(w, tail=bool(w.get("tail")))
                except Exception as e:
                    self._fail(w["ticker"], "snapshot", e)
        phase = phase_of_minute(minute)
        done = {r[0] for r in self.store.conn.execute("SELECT watch_id FROM decisions WHERE slot_ts = ? AND "
                                                      "forced = 0", (minute,))}
        model_down: list[bool] = []
        made = []
        for w in watched:
            if self.stop.is_set():
                break
            if force_decision or w["phase"] == phase:
                d = self._maybe_decide(w, minute, force_decision, done, model_down)
                if d is not None:
                    made.append(d)
        now = self.clock()
        can_select = allow_select and not self._selecting and not self.stop.is_set() and not self.paused()
        due = self._selection_due(minute) if can_select else None
        for w in watched:                                # a 12-hour end waits for the reselection that is due
            reason = self._expired(w, now, keep_12h=due is not None or self._selecting) or self._one_sided(w)
            if reason:
                self.drop(w, reason)
        if due == "off_grid":
            self.event("selection_off_grid", None, why="a grid point was missed")
        elif can_select and due is None and not self.watched() and not self._backing_off(minute):
            due = "off_grid"
            self.event("selection_off_grid", None, why="nothing watched")
        self._beat()                                     # before the selection, so a long one leaves no hole
        if due:
            self.select(kind=due)
        return made

    def _beat(self) -> None:
        self._guard()
        self._last_beat = self.clock()
        self.store.insert("heartbeats", {**stamp("", self._last_beat), "watched": len(self.watched()),
                                         "kalshi_requests": self.n_requests, "kalshi_failures": self.n_failures,
                                         "model_calls": self.n_model_calls, "paused": int(self.paused())})
        self.n_requests = self.n_failures = self.n_model_calls = 0

    def _skipped(self, prev_minute: float, upto: float) -> None:
        """The minutes after `prev_minute` and before `upto` had no cycle of their own because the cycle before
        was still running: each slot due in them gets its row, `late`, with the reason, for every watch that was
        watched in that minute (also one the overrunning cycle dropped later)."""
        m = prev_minute + 60
        while m < upto:
            phase = phase_of_minute(m)
            done = {r[0] for r in self.store.conn.execute("SELECT watch_id FROM decisions WHERE slot_ts = ? AND "
                                                          "forced = 0", (m,))}
            for w in self.store.watched_at(m):
                if w["phase"] == phase and w["watch_id"] not in done and self._warm(w, m):
                    self._guard()
                    self.store.insert("decisions", {"watch_id": w["watch_id"], "ticker": w["ticker"], **stamp("slot", m),
                                                    **stamp("decision", self.clock()), "status": "late", "forced": 0,
                                                    "battery_id": BATTERY_ID, "model_tag": TAG,
                                                    "error": "not called: the cycle before overran this minute"})
            m += 60

    def _warm(self, w: dict, slot_ts: float) -> bool:
        """Whether the watch has its 30 minutes of snapshots at this slot (from `warm_from_ts` when it was chosen
        again with its snapshots running on)."""
        anchor = w.get("warm_from_ts")
        if anchor is None:
            anchor = self.store.conn.execute("SELECT MIN(book_end_ts) FROM snapshots WHERE watch_id = ?",
                                             (w["watch_id"],)).fetchone()[0]
        return anchor is not None and anchor <= slot_ts - WARMUP_S

    def _selections(self, limit: int) -> list[tuple[int, float, int, str]]:
        """The latest selections, newest first: (id, start, chosen, kind)."""
        return self.store.conn.execute("SELECT id, started_ts, chosen, kind FROM selections ORDER BY id DESC LIMIT ?",
                                       (limit,)).fetchall()

    def _backing_off(self, minute: float) -> bool:
        """After three selections in a row that chose nothing, the next comes five minutes after the last."""
        last = self._selections(3)
        if len(last) < 3 or any(n for _, _, n, _ in last):
            return False
        return minute - math.floor(last[0][1] / 60) * 60 < 5 * 60

    def _tick(self) -> None:
        """Inside a selection: when a new minute has begun, run its cycle (snapshots, decisions, drops) first."""
        if not self._dry and self._minute is not None and math.floor(self.clock() / 60) * 60 > self._minute:
            self.cycle(allow_select=False)

    def _selection_due(self, minute: float) -> str | None:
        """'grid' on a new record and at each grid point (the first selection's minute plus a multiple of six
        hours); 'off_grid' when the latest grid point passed with no selection since (a stop, or a selection that
        could not finish), which leaves the grid where it is."""
        first = self.store.conn.execute("SELECT started_ts FROM selections WHERE kind IS NULL OR kind = 'grid' "
                                        "ORDER BY id LIMIT 1").fetchone()
        if first is None:
            return "grid"
        anchor = math.floor(first[0] / 60) * 60
        if minute < anchor:
            return None
        g = anchor + math.floor((minute - anchor) / RESELECT_S) * RESELECT_S
        if self._selections(1)[0][1] >= g:
            return None
        return "grid" if minute == g else "off_grid"

    @staticmethod
    def _expired(w: dict, now: float, keep_12h: bool = False) -> str | None:
        if not keep_12h and now >= w["watch_start_ts"] + WATCH_MAX_S:
            return "watched 12 hours"
        if now >= w["close_ts"] - STOP_BEFORE_CLOSE_S:
            return "30 minutes before it closes"
        return None

    def _one_sided(self, w: dict) -> str | None:
        """Once every snapshot of this watch over the last 30 loop minutes (by minute number) was not two-sided."""
        c = self.store.conn
        last_two = c.execute("SELECT MAX(book_end_ts) FROM snapshots WHERE watch_id = ? AND book_state = 'two_sided'",
                             (w["watch_id"],)).fetchone()[0]
        first, newest = c.execute("SELECT MIN(book_end_ts), MAX(book_end_ts) FROM snapshots WHERE watch_id = ? AND "
                                  "book_state != 'two_sided' AND book_end_ts > ?",
                                  (w["watch_id"], last_two if last_two is not None else -1)).fetchone()
        if first is None:
            return None
        if math.floor(newest / 60) - math.floor(first / 60) >= ONE_SIDED_MINUTES:
            return "book one-sided, empty or invalid for 30 minutes"
        return None

    # ---- snapshots
    def _trades_from(self, ticker: str, b1: float) -> int:
        """`min_ts` of a trades read whose book read ended at b1: 120 s before the latest read began, or one minute
        back for a first read; never more than an hour back."""
        last = self.store.last_trade_read(ticker)
        min_ts = last - TRADE_OVERLAP_S if last is not None else b1 - FIRST_TRADES_S
        return int(max(min_ts, b1 - MAX_BACKFILL_S))

    def snapshot(self, w: dict, tail: bool = False) -> int | None:
        """One snapshot of one market: its book, then its new trades, written in one transaction. `tail`: read
        after its drop (no decisions are made on it)."""
        ticker = w["ticker"]
        b0 = self.clock()
        try:
            raw = self.kalshi.orderbook_raw(ticker, DEPTH)
        except Exception as e:
            self._fail(ticker, "orderbook", e)
            return None
        b1 = self.clock()
        self._ok()
        book, bad = parse_book(raw)
        min_ts = self._trades_from(ticker, b1)
        t0 = self.clock()
        trades, ok = [], 1
        try:
            trades = self.kalshi.trades(ticker, min_ts)
            self._ok()
        except Exception as e:
            ok = 0
            self._fail(ticker, "trades", e)
        t1 = self.clock()
        s = book_summary(book, bad)
        row = {"watch_id": w["watch_id"], "ticker": ticker, **stamp("book_start", b0), **stamp("book_end", b1),
               **(stamp("trades_start", t0) if ok else {"trades_start_iso": None, "trades_start_ts": None}),
               **(stamp("trades_end", t1) if ok else {"trades_end_iso": None, "trades_end_ts": None}),
               **stamp("end", t1), "trades_min_ts": min_ts if ok else None, "trades_ok": ok,
               "yes_json": json.dumps(book["yes"]), "no_json": json.dumps(book["no"]), **s, "tail": int(tail),
               "invalid_reason": bad, "raw_json": json.dumps(raw) if bad else None}
        conn = self.store.conn
        fresh, seen = [], set()
        for x in trades:                                 # trades already stored by an earlier, overlapping read
            if x.get("ticker") not in (None, ticker) or x["trade_id"] in seen:
                continue
            seen.add(x["trade_id"])
            if conn.execute("SELECT 1 FROM trades WHERE trade_id = ?", (x["trade_id"],)).fetchone() is None:
                fresh.append(x)
        row["new_trades"] = len(fresh)
        self._guard()                                    # a step at any point of this read: nothing of it is kept
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
        """The slot's decision for one market, if it has 30 minutes of snapshots (from `warm_from_ts` when it was
        chosen again with its snapshots running on) and none was made yet."""
        if not forced:
            if w["watch_id"] in done:
                return None
            if not self._warm(w, slot_ts):
                return None
        return self.decide(w, slot_ts, forced=forced, model_down=model_down)

    def request_for(self, watch_id: int, t: float) -> bytes:
        """The exact request the model would get for this watch at time t."""
        snaps, trades, close_ts = self.store.history(watch_id, t)
        return request_bytes(state_at(snaps, trades, t, close_ts))

    def decide(self, w: dict, slot_ts: float, forced: bool = False, model_down: list | None = None) -> dict:
        """One decision: state, pin check, one call, one status, one row, whatever fails on the way. `model_down`
        carries, within a slot, that the model was unreachable, so the other markets of that slot are marked
        without a call each."""
        model_down = model_down if model_down is not None else []
        t = self.clock()
        row = {"watch_id": w["watch_id"], "ticker": w["ticker"], **stamp("slot", slot_ts), **stamp("decision", t),
               "forced": int(forced), "battery_id": BATTERY_ID, "model_tag": TAG}
        try:
            snaps, trades, close_ts = self.store.history(w["watch_id"], t)
            newest = snaps[-1] if snaps else None
            if newest is None or t - newest["book_end_ts"] > STALE_S:
                row.update(status="stale_data", error="newest snapshot %s" % (
                    "missing" if newest is None else "%.0f s old" % (t - newest["book_end_ts"])))
            elif newest["invalid"]:
                row.update(status="stale_data", error="newest snapshot is an invalid book: %s" % newest["invalid"])
            else:
                state = state_at(snaps, trades, t, close_ts)
                body = request_bytes(state)
                row.update(state_json=json.dumps(state, separators=(",", ":")), request_sha256=sha256(body))
                if model_down:
                    row.update(status="model_unavailable", error="model unreachable earlier in this slot; not called")
                elif not forced and self.clock() > slot_ts + 60 - MODEL_LIMIT_S - CALL_RESERVE_S:
                    row.update(status="late", error="not called: under %g s left in the minute"
                               % (MODEL_LIMIT_S + CALL_RESERVE_S))
                else:
                    self._call(row, body, model_down)
        except Exception as e:                           # one bad market never stops the slot, and keeps its row
            row.update(status="error", error="%s: %s" % (type(e).__name__, e))
        self._guard()
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
        except ModelTimeout as e:                        # abandoned: no answer is kept
            row.update(status="late", latency_s=e.latency, error=str(e))
            return
        except ModelHttpError as e:
            row.update(status="error", error=str(e))
            return
        except _UNREACHABLE as e:
            model_down.append(True)
            row.update(status="model_unavailable", error="%s: %s" % (type(e).__name__, e))
            return
        except Exception as e:
            row.update(status="error", error="%s: %s" % (type(e).__name__, e))
            return
        row.update(raw_response=raw.decode("utf-8", errors="replace"), latency_s=latency)
        try:
            parsed = v3.parse_response(json.loads(raw), BATTERY)
        except (ValueError, TypeError, AttributeError) as e:      # ContractError is a ValueError
            row.update(status="error", error="unreadable answer: %s" % e)
            return
        row.update(answers_json=json.dumps(parsed["answers"]), input_tokens=parsed["usage"]["input_tokens"],
                   output_tokens=parsed["usage"]["output_tokens"], model_tag=parsed["model"], status="ok")

    # ---- selection
    def select(self, dry_run: bool = False, max_pages: int | None = None, kind: str = "grid") -> Selection | None:
        """Read the open markets closing in the window, apply the rule, and (unless a dry run) in one transaction
        end the watches whose 12 hours are up (or will be within two minutes), record the selection and start
        watching the chosen. A new minute that begins while it reads gets its cycle first (`_tick`). `max_pages`
        is for smoke tests only."""
        self._selecting, self._dry = True, dry_run
        try:
            return self._select(dry_run, max_pages, kind)
        except _OutOfSync as e:
            if self._depth:                              # inside a cycle: the cycle stops
                raise
            self._go_off(str(e))
            return None
        finally:
            self._selecting, self._dry = False, False

    def _select(self, dry_run: bool, max_pages: int | None, kind: str) -> Selection | None:
        t0 = self.clock()
        markets, pages = [], 0
        try:
            for page in self.kalshi.open_markets_pages(min_close_ts=int(t0 + MIN_CLOSE_S),
                                                       max_close_ts=int(t0 + MAX_CLOSE_S + 3600)):
                markets += page
                pages += 1
                if max_pages and pages >= max_pages:
                    break
                self._tick()
            self._ok()
        except Exception as e:
            if not dry_run:
                self._fail(None, "markets", e)
            return None
        current = self.watched()
        ending = [w for w in current if w["watch_start_ts"] + WATCH_MAX_S <= t0 + RESELECT_AHEAD_S]
        staying = [w for w in current if w not in ending]

        def fetch(ticker):
            self._tick()
            if self.paused():
                raise _SelectionAborted("paused after repeated failures")
            try:
                raw = self.kalshi.orderbook_raw(ticker, DEPTH)
            except Exception as e:
                if not dry_run:
                    self._fail(ticker, "orderbook", e)
                return None
            self._ok()                                   # an invalid book is an answer, not a failed request
            book, bad = parse_book(raw)
            return dict(book, invalid=bad) if bad else book
        try:
            sel = resolve_selection(markets, fetch, self.clock, slots=self.slots,
                                    watched=[w["market"] for w in staying])
        except _SelectionAborted as e:                   # not recorded, so it is due again a minute later
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
                self._tick()
                try:
                    fees[s] = self.kalshi.series(s)
                    self._ok()
                except Exception as e:
                    self._fail(t, "series", e)
                    fees[s] = {"fee_type": None, "fee_multiplier": None}
        t1 = self.clock()
        still = {w["watch_id"] for w in self.watched()}  # a cycle during the read may have dropped one meanwhile
        ending = [w for w in ending if w["watch_id"] in still]
        phases = dict(zip(sel.chosen, assign_phases([w["phase"] for w in staying if w["watch_id"] in still],
                                                    len(sel.chosen))))
        warm = {t: self.store.warm_from(t, t1) for t in sel.chosen}
        kept = {t: r for t, r in sel.reasons.items() if sel.kinds[t] not in OBJECT_KINDS}
        ins = self.store.insert
        self._guard()
        with self.store.conn:                            # the ends, the selection and its markets: all or nothing
            for w in ending:
                ins("market_drops", {"watch_id": w["watch_id"], "ticker": w["ticker"], **stamp("dropped", t1),
                                     "reason": "watched 12 hours"}, commit=False)
            sid = ins("selections", {**stamp("started", t0), **stamp("finished", t1), "markets_seen": sel.markets_seen,
                                     "pages": pages, "books_read": len(sel.books), "chosen": len(sel.chosen),
                                     "reasons_json": json.dumps(kept),
                                     "reason_counts_json": json.dumps(Counter(sel.kinds.values())), "kind": kind,
                                     "screened_out": sel.screened_out, "ended": len(ending)}, commit=False)
            for t in sel.chosen:
                m = by_ticker[t]
                close = kd.iso_to_ts(m["close_time"])
                fee = fees[series_of(m)]
                ins("markets", {"selection_id": sid, "ticker": t, "series": series_of(m), **stamp("selected", t1),
                                **stamp("close", close), "market_json": json.dumps(m),
                                "book_json": json.dumps(sel.books[t]), "fee_type": fee.get("fee_type"),
                                "fee_multiplier": fee.get("fee_multiplier"), "why": sel.why[t], "phase": phases[t],
                                **stamp("watch_start", t0), "warm_from_ts": warm[t]}, commit=False)
        return sel


# ============================================================================ status

def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).date().isoformat()


def day_status(store: Store, now: float) -> list[dict]:
    """Per UTC day: decision slots expected (every slot of every watch on its own phase's five-minute grid, from
    30 minutes after its first snapshot, or after `warm_from_ts` when it was chosen again with its snapshots running
    on, but never before the watch itself began, so each slot of a market chosen again is counted once, to its drop
    or now; slots while the loop was stopped or out of sync count, uncollected) and collected (one of those slots
    with a non-forced decision with answers: `ok`), the share, which so cannot pass 100%; market-minutes watched
    (each watch from its market row to its drop or now, counting only the minutes in which the loop was running and
    not paused, that is wrote a heartbeat that was not paused) and their share of the nominal 20 x 1440; the model's
    latency median and 95th percentile (nearest rank) over answered calls, the count of each status, Kalshi
    requests and failures (from the heartbeats) and error events."""
    c = store.conn
    days: dict[str, dict] = {}

    def day(d):
        return days.setdefault(d, {"day": d, "expected": 0, "collected": 0, "share": None,
                                   "watched_minutes": 0.0, "watched_share": None,
                                   "statuses": dict.fromkeys(STATUSES, 0), "latencies": [], "requests": 0,
                                   "failures": 0, "errors": 0})
    alive = {int(ts // 60) for (ts,) in c.execute("SELECT ts FROM heartbeats WHERE paused = 0")}
    slots: set = set()
    for _, ticker, selected, dropped, first, phase, warm in c.execute(
            "SELECT m.watch_id, m.ticker, m.selected_ts, d.dropped_ts, (SELECT MIN(book_end_ts) FROM snapshots s "
            "WHERE s.watch_id = m.watch_id), m.phase, m.warm_from_ts FROM markets m LEFT JOIN market_drops d "
            "ON d.watch_id = m.watch_id").fetchall():
        end = dropped if dropped is not None else now
        for m in range(int(selected // 60), int(end // 60) + 1):     # only minutes the loop was running
            a, b = max(selected, 60.0 * m), min(end, 60.0 * m + 60)
            if b > a and m in alive:
                day(_day(60 * m))["watched_minutes"] += (b - a) / 60.0
        anchor = warm if warm is not None else first if first is not None else selected
        start = max(anchor + WARMUP_S, selected)       # a market chosen again: its earlier slots belong to the
                                                       # earlier watch
        offset = 60 * (phase or 0)                     # the market's own grid: minutes where minute mod 5 == phase
        s = math.ceil((start - offset) / SLOT_S) * SLOT_S + offset
        while s < end:
            if (ticker, s) not in slots:
                slots.add((ticker, s))
                day(_day(s))["expected"] += 1
            s += SLOT_S
    got: set = set()
    for ticker, slot, status, latency, answers in c.execute("SELECT ticker, slot_ts, status, latency_s, answers_json "
                                                            "FROM decisions WHERE forced = 0").fetchall():
        d = day(_day(slot))
        d["statuses"][status] = d["statuses"].get(status, 0) + 1
        answered = status == "ok" or (status == "late" and answers is not None)   # a late answer kept before rev 3
        if answered and (ticker, slot) in slots and (ticker, slot) not in got:
            got.add((ticker, slot))
            d["collected"] += 1
            if latency is not None:
                d["latencies"].append(latency)
    for ts, req, fail in c.execute("SELECT ts, kalshi_requests, kalshi_failures FROM heartbeats").fetchall():
        d = day(_day(ts))
        d["requests"] += req or 0
        d["failures"] += fail or 0
    for (ts,) in c.execute("SELECT ts FROM events WHERE kind = 'error'").fetchall():
        day(_day(ts))["errors"] += 1
    out = []
    for d in sorted(days):
        r = days[d]
        lat = sorted(r.pop("latencies"))
        r["share"] = r["collected"] / r["expected"] if r["expected"] else None
        r["watched_share"] = r["watched_minutes"] / (WATCH_COUNT * 1440)
        r["latency_median"] = statistics.median(lat) if lat else None
        r["latency_p95"] = lat[math.ceil(0.95 * len(lat)) - 1] if lat else None
        out.append(r)
    return out


def format_status(rows: list[dict]) -> str:
    lines = ["day         expected collected  share    ok  late unavail  tag stale error  lat_med  lat_p95  "
             "requests failures errors  watched_min watched"]
    for r in rows:
        s = r["statuses"]
        lines.append("%-10s  %8d %9d %6s %5d %5d %7d %4d %5d %5d %8s %8s %9d %8d %6d %12.0f %6.1f%%" % (
            r["day"], r["expected"], r["collected"],
            "-" if r["share"] is None else "%.1f%%" % (100 * r["share"]),
            s["ok"], s["late"], s["model_unavailable"], s["tag_mismatch"], s["stale_data"], s["error"],
            "-" if r["latency_median"] is None else "%.2f" % r["latency_median"],
            "-" if r["latency_p95"] is None else "%.2f" % r["latency_p95"], r["requests"], r["failures"], r["errors"],
            r["watched_minutes"], 100 * r["watched_share"]))
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
         clock: Callable[[], float] | None = None, monotonic: Callable[[], float] | None = None) -> int:
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
    kalshi = kalshi or kalshi_client()
    model = model or ModelClient(clock=clock)
    col = Collector(store, kalshi, model, clock=clock, monotonic=monotonic or time.monotonic,
                    slots=getattr(a, "max_markets", WATCH_COUNT))
    if a.cmd == "select":
        sel = col.select(dry_run=True, max_pages=a.max_pages)
        if sel is None:
            print("could not read the open markets")
            return 1
        _print_selection(sel)
        return 0
    if a.cmd == "once":
        col.start()
        if not col.synced:
            print("the clock is not known to be right (%s): nothing stored but the clock_off event"
                  % ("no Date header" if col.clock_offset is None else "%.0f s off Kalshi's Date header"
                     % col.clock_offset))
            return 1
        if not col.watched():
            sel = col.select(max_pages=a.max_pages)
            if sel is not None:
                _print_selection(sel)
        made = col.cycle(force_decision=True, allow_select=False)
        col.finish("once")
        print("clock less Kalshi's Date header: %s s" % (None if col.clock_offset is None else
                                                        "%.1f" % col.clock_offset))
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
