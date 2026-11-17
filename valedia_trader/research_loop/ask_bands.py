"""The locked "ask bands" test: buy one side of a Kalshi market at its ask at one fixed moment and hold it to the result.

The specification is docs/research/2026-11-17-ask-bands-prediction.md (committed as 1431132 before any code existed).
Two universes, judged separately: W (New York daily-high markets, hourly candles) and S (single-game sports markets,
one-minute pre-game candles).

    python -m valedia_trader.research_loop.ask_bands develop --universe W|S [--out FILE]
    python -m valedia_trader.research_loop.ask_bands sealed  --universe W|S --band LO HI \
        --i-am-opening-the-sealed-period [--out FILE]

Rules the code holds (the ticket's item numbers in brackets):

* [2] The loader takes a period and drops every event of the other period right after reading, before any price or
  result of it is used. `develop` therefore cannot produce a number from a sealed event. `sealed` refuses without the
  flag, refuses a band that is not one of the 153, refuses a band other than the one the development file chose,
  refuses without the development file and its hash, writes a marker (band, time, the development file's hash)
  BEFORE it reads any sealed row, and refuses when the marker exists.
* [4] Money is whole cents: a stored price is turned to integer cents once; every result is an integer; means are
  computed at the end.
* The event (Kalshi's event_ticker) is the unit for every count and for the resampling.
* An event's period is decided from all of its listed markets, repeated tickers included; a repeated ticker is
  then left out of use in every copy.
* The sports reader takes only each candle's closing yes bid and yes ask (judged raw, in the spellings close,
  close_dollars, close_fp) and no longer looks at the candle's `price` block.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote

import numpy as np

from valedia_trader.research_loop import REPO
from valedia_trader.venues import kalshi_data as kd
from valedia_trader.venues import kalshi_history as kh

UTC = timezone.utc

PREDICTION_COMMIT = "1431132"
PREDICTION_DOC = "docs/research/2026-11-17-ask-bands-prediction.md"
SEED = 20261117
RESAMPLES = 2000
RESAMPLE_CHUNK = 100                      # resamples drawn per call: fixed, so the random stream does not depend on size
COST_CENTS = 2                            # 0.02 dollars a contract on the purchase, nothing at settlement
P_LIMIT = Fraction(25, 1000)              # one-sided p-value limit for both steps
DEV_MIN_TRADES, DEV_MIN_EVENTS = 300, 150
SEALED_MIN_TRADES, SEALED_MIN_EVENTS = 200, 100
W_SEALED_FROM = date(2025, 1, 1)          # W: sealed is every event whose close falls on or after this UTC date
S_DEV_LAST = date(2026, 1, 21)            # S: development is every event whose purchase moment falls on or before this
CENT_TOLERANCE = 1e-6                     # dollars: a stored price must be this close to a whole cent
NOTE = "information: decides nothing"

CELLS = tuple((5 + 5 * k, 9 + 5 * k) for k in range(17)) + ((90, 95),)         # 5-9, 10-14, ..., 85-89, 90-95
SIDE_SKIPS = ("price_missing", "price_invalid", "price_not_whole_cent", "price_out_of_range", "other_quote_unusable",
              "crossed_or_locked", "outside_5_95")
MARKET_SKIPS = ("bad_ticker", "bad_result", "bad_candle_record", "no_candle", "ambiguous_moment")
UNASSIGNED = ("bad_record", "duplicate_ticker", "bad_event", "bad_time", "bad_ticker", "no_stored_candle")

# the labelled purchase moments of each universe: seconds before the close (None: the last stored candle)
MOMENTS = {"W": {"24h": 24 * 3600, "12h": 12 * 3600}, "S": {"last": None}}
MAIN = {"W": "24h", "S": "last"}
WEATHER_DIR = Path("artifacts") / "kalshi_weather"
SPORTS_DB = Path("artifacts") / "sports_lines" / "kalshi.sqlite"
OUT_DIR = Path("artifacts") / "ask_bands"
# Kalshi's own ticker shape, one rule for both universes: 1 to 80 characters, a capital first, then only capitals,
# digits, dots and hyphens. (The same shape is webull_events._EVENT_SYMBOL; that module is a broker venue and is not
# imported here, so the pattern is repeated.) No two accepted tickers can differ only by case.
_TICKER_SHAPE = re.compile(r"[A-Z][A-Z0-9.\-]{0,79}")


class Refused(Exception):
    """The program will not do this (it protects the lock). Nothing was read or written for the refused step."""


# ---------------------------------------------------------------- money and prices
def to_cents(v) -> tuple[str, int | None]:
    """A stored price in dollars as integer cents: ('ok', cents), or ('missing' | 'invalid' | 'not_whole_cent', None).
    None is missing; a bool, string, NaN, infinity or negative number is invalid; a price further than 1e-6 dollars
    from a whole cent is its own reason. A bad price is never a zero."""
    if v is None:
        return "missing", None
    if isinstance(v, (bool, np.bool_)) or not isinstance(v, (int, float, np.integer, np.floating)):
        return "invalid", None
    f = float(v)
    if not math.isfinite(f) or f < 0:
        return "invalid", None
    c = round(f * 100)
    if abs(f - c / 100) > CENT_TOLERANCE:
        return "not_whole_cent", None
    return "ok", int(c)


def cell_of(cents) -> int | None:
    """The index (0 to 17) of the 5-cent cell holding a price in cents; the last cell includes 95. None outside."""
    if type(cents) is not int or cents < 5 or cents > 95:
        return None
    return min((cents - 5) // 5, 17)


def side_price(side: str, bid, ask) -> tuple[int | None, str | None]:
    """The price paid in cents for one side at a candle's closing yes bid and yes ask (dollars as stored), or
    (None, reason). Yes: the yes ask. No: one dollar less the yes bid. A side is left out when its ask is missing,
    not above 0 or not below 1, when the book is crossed or locked (bid not below ask), or when its price is outside
    5 to 95 cents. The other quote must be usable too (it is needed to see a crossed book)."""
    if side not in ("yes", "no"):
        raise ValueError(side)
    primary, other = (ask, bid) if side == "yes" else (bid, ask)
    status, pc = to_cents(primary)
    if status != "ok":
        return None, "price_" + status
    paid = pc if side == "yes" else 100 - pc
    if not 0 < paid < 100:
        return None, "price_out_of_range"
    status, oc = to_cents(other)
    if status != "ok":
        return None, "other_quote_unusable"
    bid_c, ask_c = (oc, pc) if side == "yes" else (pc, oc)
    if not bid_c < ask_c:
        return None, "crossed_or_locked"
    if cell_of(paid) is None:
        return None, "outside_5_95"
    return paid, None


def taker_fee_cents(price_cents: int) -> int:
    """Kalshi's taker fee for one contract, in whole cents: 0.07 x P x (1 - P) dollars rounded up to the cent."""
    return -(-(7 * price_cents * (100 - price_cents)) // 10000)


def trade_result(paid: int, win, cost) -> int:
    """One dollar if the side wins, else nothing, less the price paid and the cost, in whole cents."""
    c = cost(paid) if callable(cost) else cost
    return (100 if win else 0) - paid - c


# ---------------------------------------------------------------- bands, the screen, the p-value
def valid_bands() -> list[tuple[int, int]]:
    """The 153 bands as (lo, hi) in cents: a run of two or more cells; the band holds prices lo to hi - 1
    (to 95 when hi is 95)."""
    return [(CELLS[a][0], CELLS[b][1] + 1 if b < 17 else 95) for a in range(18) for b in range(a + 1, 18)]


def is_valid_band(lo, hi) -> bool:
    return type(lo) is int and type(hi) is int and (lo, hi) in set(valid_bands())


def _band_cell_range(lo: int, hi: int) -> range:
    a = (lo - 5) // 5
    b = 17 if hi == 95 else (hi - 5) // 5 - 1
    return range(a, b + 1)


def band_trades(trades, lo: int, hi: int) -> list:
    cells = _band_cell_range(lo, hi)
    return [t for t in trades if cell_of(t[1]) in cells]


def event_totals(trades, cost) -> list[int]:
    """Each event's total result in cents over these trades, events in name order."""
    by = {}
    for ev, paid, win in trades:
        by[ev] = by.get(ev, 0) + trade_result(paid, win, cost)
    return [by[k] for k in sorted(by)]


def event_p(totals) -> tuple[int, float]:
    """(resamples not above zero, p): 2,000 resamples of whole events with seed 20261117; the statistic of a resample
    is its total result over its total trades, which is not above zero exactly when the total is not above zero."""
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


def band_stats(trades, cost) -> dict:
    """Counts and results of these trades (all of them are taken to be in the band)."""
    n = len(trades)
    events = {t[0] for t in trades}
    total = sum(trade_result(p, w, cost) for _, p, w in trades)
    wins = sum(1 for _, _, w in trades if w)
    return {"trades": n, "events": len(events), "total_cents": int(total),
            "mean_cents": (total / n) if n else None,
            "share_won": (wins / n) if n else None,
            "mean_price_cents": (sum(p for _, p, _ in trades) / n) if n else None}


def screen(trades, cost) -> dict[tuple[int, int], tuple[int, int, int]]:
    """For each of the 153 bands: (trades, events, total result in cents)."""
    per_cell = [{"n": 0, "total": 0, "events": set()} for _ in CELLS]
    for ev, paid, win in trades:
        c = per_cell[cell_of(paid)]
        c["n"] += 1
        c["total"] += trade_result(paid, win, cost)
        c["events"].add(ev)
    out = {}
    for a in range(18):
        n, total, events = per_cell[a]["n"], per_cell[a]["total"], set(per_cell[a]["events"])
        for b in range(a + 1, 18):
            n += per_cell[b]["n"]
            total += per_cell[b]["total"]
            events |= per_cell[b]["events"]
            hi = CELLS[b][1] + 1 if b < 17 else 95
            out[(CELLS[a][0], hi)] = (n, len(events), total)
    return out


def choose_band(stats: dict, min_trades: int = DEV_MIN_TRADES, min_events: int = DEV_MIN_EVENTS):
    """Among bands with enough trades and events, the highest mean result (compared exactly); ties: the wider band,
    then the lower start. None when no band qualifies."""
    best, best_key = None, None
    for band, (n, e, total) in stats.items():
        if n < min_trades or e < min_events:
            continue
        key = (Fraction(total, n), band[1] - band[0], -band[0])
        if best_key is None or key > best_key:
            best, best_key = band, key
    return best


def cell_table(trades, cost) -> list[dict]:
    rows = []
    for k, (lo, hi) in enumerate(CELLS):
        ts = [t for t in trades if cell_of(t[1]) == k]
        st = band_stats(ts, cost)
        rows.append({"cell": "%d-%d" % (lo, hi), "trades": st["trades"], "events": st["events"],
                     "share_won": st["share_won"], "mean_price_cents": st["mean_price_cents"],
                     "mean_result_cents": st["mean_cents"], "total_result_cents": st["total_cents"]})
    return rows


# ---------------------------------------------------------------- loading, with the period wall
@dataclass
class LabelResult:
    market_skips: dict = field(default_factory=dict)
    sides_considered: int = 0
    side_skips: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)            # (event, price paid in cents, side won)


@dataclass
class Collected:
    universe: str
    period: str
    unassignable: dict
    markets_in_period: int
    events_in_period: int
    labels: dict
    data_files: dict


class _BadCandle(Exception):
    pass


def _utc_ts(v) -> int | None:
    if type(v) is not str:
        return None
    try:
        dt = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def _utc_date(ts: int) -> date:
    return datetime.fromtimestamp(ts, UTC).date()


def _bump(d: dict, key: str, n: int = 1) -> None:
    d[key] = d.get(key, 0) + n


def _quote(c: dict, name: str):
    b = c.get(name)
    return b.get("close") if isinstance(b, dict) else None


def _choices(candles, close_ts, labels: dict) -> dict:
    """Per label: ('ok', (yes bid close, yes ask close)) or ('skip', reason). Candles may come in any order."""
    if not isinstance(candles, list):
        raise _BadCandle()
    for c in candles:
        if not isinstance(c, dict) or type(c.get("end_ts")) is not int:
            raise _BadCandle()
    out = {}
    for label, before in labels.items():
        pool = candles if before is None else [c for c in candles if c["end_ts"] <= close_ts - before]
        if not pool:
            out[label] = ("skip", "no_candle")
            continue
        last = max(c["end_ts"] for c in pool)
        chosen = [c for c in pool if c["end_ts"] == last]
        if len(chosen) > 1:
            out[label] = ("skip", "ambiguous_moment")
            continue
        out[label] = ("ok", (_quote(chosen[0], "yes_bid"), _quote(chosen[0], "yes_ask")))
    return out


def _ingest(results: dict, event: str, result, choices: dict | None, skip: str | None) -> None:
    for label, res in results.items():
        reason = skip
        if reason is None:
            status, payload = choices[label]
            reason = payload if status == "skip" else None
        if reason is not None:
            _bump(res.market_skips, reason)
            continue
        bid, ask = choices[label][1]
        for side in ("yes", "no"):
            res.sides_considered += 1
            paid, why = side_price(side, bid, ask)
            if why is not None:
                _bump(res.side_skips, why)
                continue
            res.trades.append((event, paid, result == side))


def _event_periods(items: list[tuple[str, bool]]) -> dict[str, str]:
    """An event is sealed when any of its markets is on the sealed side of the line, development otherwise."""
    sealed = {}
    for ev, is_sealed in items:
        sealed[ev] = sealed.get(ev, False) or is_sealed
    return {ev: ("sealed" if s else "development") for ev, s in sealed.items()}


def ticker_ok(t) -> bool:
    return type(t) is str and _TICKER_SHAPE.fullmatch(t) is not None


def _repeated_tickers(rows) -> set:
    """Tickers listed more than once anywhere in a universe's whole listing (both periods: a ticker is neither a price
    nor a result). Every copy of such a ticker is left out."""
    counts = Counter(r["ticker"].casefold() for r in rows if isinstance(r, dict) and type(r.get("ticker")) is str)
    return {t for t, n in counts.items() if n > 1}               # compared case-folded, over the whole listing


def _raw_candle(raw) -> dict:
    """One stored S candle in normalize_candle's shape, but with each quote's closing value left as stored: kalshi_data's
    float conversion turns a JSON false into 0.0, so the raw value is judged here. A numeric string becomes a number;
    a bool, null, list or non-numeric string stays as it is and is refused later by to_cents."""
    if not isinstance(raw, dict):
        raise _BadCandle()
    try:
        end = int(raw["end_period_ts"])
    except (KeyError, TypeError, ValueError):
        raise _BadCandle() from None

    def close(name):
        b = raw.get(name)
        v = kd._pick(b, "close") if isinstance(b, dict) else None
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                return v
        return v
    return {"end_ts": end, "yes_bid": {"close": close("yes_bid")}, "yes_ask": {"close": close("yes_ask")}}


def _s_candles(store, ticker: str) -> list[dict]:
    return [_raw_candle(json.loads(r["raw"]))
            for r in store.db.execute("SELECT raw FROM candles WHERE ticker = ? ORDER BY end_ts", (ticker,))]


def _valid_event(v) -> bool:
    return type(v) is str and bool(v.strip())


def _stat(p: Path) -> dict:
    s = p.stat()
    return {"size": s.st_size, "mtime": s.st_mtime}


def open_history_readonly(path) -> kh.HistoryStore:
    """A HistoryStore that cannot write: kalshi_history.HistoryStore's constructor creates tables, this does not."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    st = kh.HistoryStore.__new__(kh.HistoryStore)
    st.path = p
    st.db = sqlite3.connect("file:%s?mode=ro" % quote(str(p)), uri=True)
    st.db.row_factory = sqlite3.Row
    st.db.execute("PRAGMA query_only = ON")
    return st


def _collect_w(period: str, weather_dir: Path):
    mfile = weather_dir / "markets.json"
    markets = json.loads(mfile.read_text())["markets"]
    unassigned: Counter = Counter()
    seen = []                                           # (index, event, close ts, sealed): no price, no result
    repeated = _repeated_tickers(markets)
    for i, m in enumerate(markets):
        if not isinstance(m, dict):
            unassigned["bad_record"] += 1
            continue
        t = m.get("ticker")
        repeat = type(t) is str and t.casefold() in repeated    # judge the type before any lookup
        if repeat:
            unassigned["duplicate_ticker"] += 1
        ev = m.get("event_ticker")
        if not _valid_event(ev):
            if not repeat:
                unassigned["bad_event"] += 1
            continue
        ts = _utc_ts(m.get("close_time"))
        if ts is None:
            if not repeat:
                unassigned["bad_time"] += 1
            continue
        seen.append((i, ev, ts, _utc_date(ts) >= W_SEALED_FROM, repeat))
    # the period of an event comes from ALL its listed markets, repeated tickers included; only then are they left out
    periods = _event_periods([(ev, s) for _, ev, _, s, _ in seen])
    keep = [(i, ev, ts) for i, ev, ts, _, rep in seen if periods[ev] == period and not rep]   # the wall
    del seen
    labels = MOMENTS["W"]
    results = {label: LabelResult() for label in labels}
    events = set()
    n_files, size, mt_lo, mt_hi = 0, 0, None, None
    cdir = weather_dir / "candles"
    on_disk = set(os.listdir(cdir)) if cdir.is_dir() else set()     # listed once; the file system is not trusted on case
    for i, ev, ts in keep:
        m = markets[i]
        events.add(ev)
        ticker = m.get("ticker")
        if not ticker_ok(ticker):
            _ingest(results, ev, None, None, "bad_ticker")
            continue
        result = m.get("result")
        if type(result) is not str or result not in ("yes", "no"):
            _ingest(results, ev, None, None, "bad_result")
            continue
        f = weather_dir / "candles" / (ticker + ".json")
        if (ticker + ".json") not in on_disk:               # the name on disk must match byte for byte
            _ingest(results, ev, result, {label: ("skip", "no_candle") for label in labels}, None)
            continue
        try:
            st = f.stat()
            n_files, size = n_files + 1, size + st.st_size
            mt_lo = st.st_mtime if mt_lo is None else min(mt_lo, st.st_mtime)
            mt_hi = st.st_mtime if mt_hi is None else max(mt_hi, st.st_mtime)
            choices = _choices(json.loads(f.read_text())["candles"], ts, labels)
        except (ValueError, KeyError, TypeError, OSError, _BadCandle):
            _ingest(results, ev, result, None, "bad_candle_record")
            continue
        _ingest(results, ev, result, choices, None)
    files = {"markets.json": _stat(mfile),
             "candles_read": {"files": n_files, "total_size": size, "mtime_min": mt_lo, "mtime_max": mt_hi}}
    return dict(unassigned), len(keep), len(events), results, files


def _collect_s(period: str, db: Path):
    store = open_history_readonly(db)
    try:
        rows = [r for r in store.markets() if isinstance(r, dict) and r.get("series") in set(kh.SERIES.values())]
        unassigned: Counter = Counter()
        seen = []                                       # (index, event, purchase moment, sealed)
        repeated = _repeated_tickers(rows)
        for i, r in enumerate(rows):
            ev, ticker = r.get("event_ticker"), r.get("ticker")
            repeat = type(ticker) is str and ticker.casefold() in repeated    # judge the type before any lookup
            if repeat:
                unassigned["duplicate_ticker"] += 1
            if not _valid_event(ev):
                if not repeat:
                    unassigned["bad_event"] += 1
                continue
            if not ticker_ok(ticker):                          # before any lookup; a repeat is already counted
                if not repeat:
                    unassigned["bad_ticker"] += 1
                continue
            ts = store.db.execute("SELECT MAX(end_ts) FROM candles WHERE ticker = ?", (ticker,)).fetchone()[0]
            if ts is None:
                if not repeat:
                    unassigned["no_stored_candle"] += 1
                continue
            if type(ts) is not int:
                if not repeat:
                    unassigned["bad_time"] += 1
                continue
            seen.append((i, ev, ts, _utc_date(ts) > S_DEV_LAST, repeat))
        # the period of an event comes from ALL its listed markets, repeated tickers included; only then are they left out
        periods = _event_periods([(ev, s) for _, ev, _, s, _ in seen])
        keep = [(i, ev, ts) for i, ev, ts, _, rep in seen if periods[ev] == period and not rep]   # the wall
        del seen
        labels = MOMENTS["S"]
        results = {label: LabelResult() for label in labels}
        events = set()
        for i, ev, ts in keep:
            r = rows[i]
            events.add(ev)
            result = r.get("result")
            if type(result) is not str or result not in ("yes", "no"):
                _ingest(results, ev, None, None, "bad_result")
                continue
            try:
                choices = _choices(_s_candles(store, r["ticker"]), ts, labels)
            except Exception:                                   # a stored row that cannot be read
                _ingest(results, ev, result, None, "bad_candle_record")
                continue
            _ingest(results, ev, result, choices, None)
    finally:
        store.close()
    return dict(unassigned), len(keep), len(events), results, {db.name: _stat(db)}


def collect(universe: str, period: str, *, root=None, weather_dir=None, sports_db=None) -> Collected:
    """Every trade of one universe in one period. The other period's events are dropped right after reading."""
    if universe not in ("W", "S") or period not in ("development", "sealed"):
        raise ValueError((universe, period))
    root = Path(root) if root is not None else REPO
    if universe == "W":
        wd = Path(weather_dir) if weather_dir is not None else root / WEATHER_DIR
        un, n, e, res, files = _collect_w(period, wd)
    else:
        db = Path(sports_db) if sports_db is not None else root / SPORTS_DB
        un, n, e, res, files = _collect_s(period, db)
    return Collected(universe, period, un, n, e, res, files)


# ---------------------------------------------------------------- results
def _counts(label: LabelResult) -> dict:
    return {"market_skips": dict(sorted(label.market_skips.items())), "sides_considered": label.sides_considered,
            "side_skips": dict(sorted(label.side_skips.items())), "trades": len(label.trades),
            "events_with_trades": len({t[0] for t in label.trades})}


def _base_result(col: Collected) -> dict:
    main = col.labels[MAIN[col.universe]]
    r = {"universe": col.universe, "period": col.period, "markets_in_period": col.markets_in_period,
         "events_in_period": col.events_in_period, "unassignable": dict(sorted(col.unassignable.items()))}
    r.update(_counts(main))
    r["cell_table"] = cell_table(main.trades, COST_CENTS)
    return r


def _band_block(trades, band) -> dict:
    sub = band_trades(trades, *band)
    st = band_stats(sub, COST_CENTS)
    k, p = event_p(event_totals(sub, COST_CENTS))
    return {"band": list(band), "trades": st["trades"], "events": st["events"], "total_cents": st["total_cents"],
            "mean_cents": st["mean_cents"], "not_above_zero": k, "resamples": RESAMPLES, "p_value": p,
            "seed": SEED, "share_won": st["share_won"], "mean_price_cents": st["mean_price_cents"]}


def development_result(col: Collected) -> dict:
    main = col.labels[MAIN[col.universe]]
    r = _base_result(col)
    stats = screen(main.trades, COST_CENTS)
    eligible = sum(1 for n, e, _ in stats.values() if n >= DEV_MIN_TRADES and e >= DEV_MIN_EVENTS)
    band = choose_band(stats)
    step = {"thresholds": {"min_trades": DEV_MIN_TRADES, "min_events": DEV_MIN_EVENTS, "p_limit": float(P_LIMIT)},
            "bands": len(stats), "eligible_bands": eligible}
    if band is None:
        step.update({"band": None, "trades": None, "events": None, "total_cents": None, "mean_cents": None,
                     "p_value": None, "not_above_zero": None, "resamples": RESAMPLES, "failed": ["no_eligible_band"],
                     "verdict": "FAIL"})
    else:
        blk = _band_block(main.trades, band)
        failed = []
        if blk["total_cents"] <= 0:
            failed.append("mean_not_above_zero")
        if not p_within_limit(blk["not_above_zero"]):
            failed.append("p_above_0.025")
        step.update(blk)
        step.update({"failed": failed, "verdict": "FAIL" if failed else "PROCEED"})
    r["step_1"] = step
    r["information_cost_1_cent"] = {"note": NOTE, "cost_cents": 1, "cell_table": cell_table(main.trades, 1)}
    r["information_kalshi_taker_fee"] = {"note": NOTE, "cost": "ceil to the cent of 0.07 x P x (1 - P) a contract",
                                         "cell_table": cell_table(main.trades, taker_fee_cents)}
    if col.universe == "W":
        twelve = col.labels["12h"]
        r["information_w_12_hours_before_close"] = dict(
            _counts(twelve), note=NOTE, cost_cents=COST_CENTS, cell_table=cell_table(twelve.trades, COST_CENTS))
    return r


def sealed_result(col: Collected, band: tuple[int, int]) -> dict:
    main = col.labels[MAIN[col.universe]]
    r = _base_result(col)
    blk = _band_block(main.trades, band)
    failed = []
    if blk["total_cents"] <= 0:
        failed.append("mean_not_above_zero")
    if not p_within_limit(blk["not_above_zero"]):
        failed.append("p_above_0.025")
    if blk["trades"] < SEALED_MIN_TRADES:
        failed.append("trades_below_%d" % SEALED_MIN_TRADES)
    if blk["events"] < SEALED_MIN_EVENTS:
        failed.append("events_below_%d" % SEALED_MIN_EVENTS)
    r["stats"] = blk
    r["verdict"] = {"thresholds": {"min_trades": SEALED_MIN_TRADES, "min_events": SEALED_MIN_EVENTS,
                                   "p_limit": float(P_LIMIT)},
                    "failed": failed, "verdict": "FAIL" if failed else "PASS"}
    r["cell_table"] = cell_table(main.trades, COST_CENTS)
    return r


# ---------------------------------------------------------------- files
def code_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _provenance(col: Collected) -> dict:
    return {"prediction_commit": PREDICTION_COMMIT, "prediction_doc": PREDICTION_DOC,
            "code_sha256": code_sha256(), "data_files": col.data_files}


def _dump(doc: dict) -> bytes:
    return (json.dumps(doc, sort_keys=True, indent=1) + "\n").encode()


def _write_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, path)


def _paths(universe: str, root) -> tuple[Path, Path]:
    d = (Path(root) if root is not None else REPO) / OUT_DIR
    return d / ("develop-%s.json" % universe), d / ("sealed-%s.used" % universe)


def run_develop(universe: str, *, root=None, out=None, weather_dir=None, sports_db=None) -> dict:
    if universe not in ("W", "S"):
        raise Refused("universe must be W or S")
    col = collect(universe, "development", root=root, weather_dir=weather_dir, sports_db=sports_db)
    doc = {"provenance": _provenance(col), "result": development_result(col)}
    path = Path(out) if out is not None else _paths(universe, root)[0]
    raw = _dump(doc)
    _write_bytes(path, raw)
    _write_bytes(path.with_name(path.name + ".sha256"), (hashlib.sha256(raw).hexdigest() + "\n").encode())
    return doc


def _check_development_file(universe: str, band, develop_file: Path) -> str:
    """The development file must exist with a matching hash file, be this code's and this universe's, have passed step
    1 and have chosen exactly this band. Returns its sha256."""
    side = develop_file.with_name(develop_file.name + ".sha256")
    if not develop_file.is_file():
        raise Refused("the development file %s is missing: run develop first" % develop_file)
    if not side.is_file():
        raise Refused("the development file's hash file %s is missing" % side)
    raw = develop_file.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if side.read_text().strip() != got:
        raise Refused("the development file's hash differs from the one written with it")
    try:
        doc = json.loads(raw)
        prov, res = doc["provenance"], doc["result"]
        step = res["step_1"]
    except (ValueError, KeyError, TypeError):
        raise Refused("the development file is not a development file of this program") from None
    if prov.get("code_sha256") != code_sha256():
        raise Refused("the development file was made by different code")
    if prov.get("prediction_commit") != PREDICTION_COMMIT:
        raise Refused("the development file implements a different prediction")
    if res.get("universe") != universe or res.get("period") != "development":
        raise Refused("the development file belongs to another universe or period")
    if step.get("verdict") != "PROCEED":
        raise Refused("the universe failed step 1: its sealed period is not opened")
    if step.get("band") != list(band):
        raise Refused("band %s is not the band development chose (%s)" % (list(band), step.get("band")))
    return got


def run_sealed(universe: str, band, *, flag: bool = False, root=None, out=None, develop_file=None,
               weather_dir=None, sports_db=None) -> dict:
    if not flag:
        raise Refused("the sealed period is opened only with --i-am-opening-the-sealed-period")
    if universe not in ("W", "S"):
        raise Refused("universe must be W or S")
    try:
        lo, hi = band
    except (TypeError, ValueError):
        raise Refused("a band is two numbers: LO HI") from None
    if not is_valid_band(lo, hi):
        raise Refused("(%r, %r) is not one of the 153 bands" % (lo, hi))
    band = (lo, hi)
    dev_default, marker = _paths(universe, root)
    dev_path = Path(develop_file) if develop_file is not None else dev_default
    dev_sha = _check_development_file(universe, band, dev_path)
    if marker.exists():
        raise Refused("the sealed period of %s was already run (%s exists)" % (universe, marker))
    r = Path(root) if root is not None else REPO
    if universe == "W":
        data = (Path(weather_dir) if weather_dir is not None else r / WEATHER_DIR) / "markets.json"
    else:
        data = Path(sports_db) if sports_db is not None else r / SPORTS_DB
    if not data.is_file():
        raise Refused("the data file %s is missing (nothing was marked used)" % data)
    marker.parent.mkdir(parents=True, exist_ok=True)
    note = {"universe": universe, "band": list(band), "time": datetime.now(UTC).isoformat(),
            "development_sha256": dev_sha, "development_file": str(dev_path)}
    try:
        with open(marker, "x") as f:                    # written BEFORE any sealed row is read
            f.write(json.dumps(note, sort_keys=True, indent=1) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError:
        raise Refused("the sealed period of %s was already run" % universe) from None
    col = collect(universe, "sealed", root=root, weather_dir=weather_dir, sports_db=sports_db)
    prov = _provenance(col)
    prov.update({"development_file": str(dev_path), "development_sha256": dev_sha})
    doc = {"provenance": prov, "result": sealed_result(col, band)}
    _write_bytes(Path(out) if out is not None else dev_default.with_name("sealed-%s.json" % universe), _dump(doc))
    return doc


# ---------------------------------------------------------------- the text table
def _fmt(v, nd=2) -> str:
    return "-" if v is None else ("%.*f" % (nd, v) if isinstance(v, float) else str(v))


def _table(rows: list[dict]) -> list[str]:
    lines = ["  cell    trades  events  share_won  mean_price  mean_result  total_result"]
    for r in rows:
        lines.append("  %-6s %7s %7s %10s %11s %12s %13s" % (
            r["cell"], r["trades"], r["events"], _fmt(r["share_won"], 3), _fmt(r["mean_price_cents"]),
            _fmt(r["mean_result_cents"]), r["total_result_cents"]))
    return lines


def format_table(doc: dict) -> str:
    r = doc["result"]
    lines = ["ask bands, universe %s, %s period (prediction %s)" % (r["universe"], r["period"], PREDICTION_COMMIT),
             "markets in period %d, events %d; set aside before the period was known: %s" % (
                 r["markets_in_period"], r["events_in_period"], r["unassignable"] or "none"),
             "sides considered %d, trades %d from %d events" % (r["sides_considered"], r["trades"],
                                                                r["events_with_trades"]),
             "market skips: %s" % (r["market_skips"] or "none"), "side skips: %s" % (r["side_skips"] or "none")]
    if "step_1" in r:
        s = r["step_1"]
        lines.append("step 1 (development): %s" % s["verdict"])
        lines.append("  eligible bands %d of %d; chosen band %s; trades %s, events %s, mean %s cents, p %s%s" % (
            s["eligible_bands"], s["bands"], s["band"], s["trades"], s["events"], _fmt(s["mean_cents"], 4),
            _fmt(s["p_value"], 4), "; failed: %s" % s["failed"] if s["failed"] else ""))
    if "stats" in r:
        s, v = r["stats"], r["verdict"]
        lines.append("sealed verdict: %s" % v["verdict"])
        lines.append("  band %s; trades %s, events %s, mean %s cents, p %s%s" % (
            s["band"], s["trades"], s["events"], _fmt(s["mean_cents"], 4), _fmt(s["p_value"], 4),
            "; failed: %s" % v["failed"] if v["failed"] else ""))
    lines.append("table by cell (cost %d cents a contract; prices and results in cents):" % COST_CENTS)
    lines += _table(r["cell_table"])
    for key, title in (("information_cost_1_cent", "cost 1 cent"),
                       ("information_kalshi_taker_fee", "Kalshi taker fee"),
                       ("information_w_12_hours_before_close", "W at 12 hours before the close, cost 2 cents")):
        if key in r:
            lines.append("%s: %s [%s]" % (NOTE, title, key))
            lines += _table(r[key]["cell_table"])
    return "\n".join(lines)


# ---------------------------------------------------------------- command line
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ask_bands", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("develop", "sealed"):
        p = sub.add_parser(name)
        p.add_argument("--universe", required=True, choices=("W", "S"))
        p.add_argument("--out", default=None)
        p.add_argument("--root", default=None, help="repository root holding artifacts/ (default: this tree)")
        p.add_argument("--weather-dir", default=None)
        p.add_argument("--sports-db", default=None)
        if name == "sealed":
            p.add_argument("--band", required=True, nargs=2, type=int, metavar=("LO", "HI"))
            p.add_argument("--i-am-opening-the-sealed-period", dest="flag", action="store_true")
            p.add_argument("--develop-file", default=None)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "develop":
            doc = run_develop(a.universe, root=a.root, out=a.out, weather_dir=a.weather_dir, sports_db=a.sports_db)
        else:
            doc = run_sealed(a.universe, tuple(a.band), flag=a.flag, root=a.root, out=a.out,
                             develop_file=a.develop_file, weather_dir=a.weather_dir, sports_db=a.sports_db)
    except Refused as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2
    print(format_table(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
