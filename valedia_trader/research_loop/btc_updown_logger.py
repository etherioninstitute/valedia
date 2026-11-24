"""Forward logger for the BTC 15-minute Up/Down test: a LOG-ONLY tool, disabled until 2026-12-04 00:00 Pacific.

What it does: for one 15-minute window it writes ONE record, before the window closes, skips included. The record
holds the raw inputs it was handed: each frozen exchange's quote, the one price derived from them, the Kalshi book
at t* (= open + 300 s), and the status (OK, or SKIP_FEED / SKIP_NO_BOOK and why). It makes no trade decision and
computes no trigger, fill or eligibility field; those come from the locked test document, later, offline.

What it does not do: it makes no request of any kind and holds no exchange parser. Quotes, the book and the tape
arrive through three caller-supplied reader callables (a later, separate adapter owns any real fetch). Every public
function that reads or writes first checks the date constant `ENABLED_FROM` against an injected, timezone-aware
clock and refuses before that instant, whatever its other arguments.

Its store is `<folder>/btc_updown_log.jsonl` under a folder the caller names: there is no default folder, and the
folder must be an absolute path outside the repository.
Standard library only; nothing from the rest of the package is imported.
"""
from __future__ import annotations

import json
import math
import os
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# The enabling instant: 2026-12-04 00:00 America/Los_Angeles (PST, UTC-8) = 08:00 UTC. Before it, nothing is read
# and nothing is written.
ENABLED_FROM = datetime(2026, 12, 4, 8, 0, 0, tzinfo=timezone.utc)
SCHEMA_VERSION = 1
LOG_NAME = "btc_updown_log.jsonl"
_REPO_ROOT = Path(__file__).resolve().parents[2]   # the folder that holds the `valedia_trader` package
T_STAR_OFFSET_S = 300.0     # t* = the window's open + 5 minutes
TAPE_SPAN_S = 60.0          # the tape is read over [t*, t* + 60 s]
MIN_RESPONDING = 3          # fewer responding exchanges than this is SKIP_FEED
TRIM_FROM = 5               # default trim: from this many responders, drop the highest and the lowest


class NotYetEnabled(RuntimeError):
    """The clock reads earlier than ENABLED_FROM: nothing is read or written."""


class NaiveClock(ValueError):
    """The clock returned a datetime with no timezone."""


class LateWindow(RuntimeError):
    """The window has closed (or closed while it was being read): a record would not be written before settlement."""


class TapeBudgetExceeded(RuntimeError):
    """The run's tape reads are used up."""


@dataclass(frozen=True)
class Window:
    window_ticker: str
    open_ts: float
    close_ts: float
    floor_strike: float | None = None


@dataclass(frozen=True)
class ExchangeQuote:
    """One exchange's already-parsed answer. `closes_1m` carries recent one-minute closes, oldest first."""
    exchange: str
    ok: bool
    price: float | None
    quote_ts: float | None = None
    closes_1m: tuple = ()
    error: str | None = None


@dataclass(frozen=True)
class Fixture:
    """One synthetic window for `dry_run`: its quotes and its book (None = no book)."""
    window: Window
    quotes: Sequence
    book: dict | None


class TapeBudget:
    """Counts the tape reads of one run; the read after the last allowed one is refused."""

    def __init__(self, max_reads: int):
        if not isinstance(max_reads, int) or isinstance(max_reads, bool) or max_reads < 0:
            raise ValueError("max_reads must be a whole number, zero or more")
        self.max_reads = max_reads
        self.used = 0

    def take(self) -> None:
        if self.used >= self.max_reads:
            raise TapeBudgetExceeded(f"{self.max_reads} tape reads already used in this run")
        self.used += 1


# ------------------------------------------------------------------ the date check

def _now(clock: Callable[[], datetime]) -> datetime:
    now = clock()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise NaiveClock("the clock must return a timezone-aware datetime")
    return now


def _gate(clock: Callable[[], datetime]) -> datetime:
    """ONE clock reading, checked against ENABLED_FROM and returned, so a caller uses the reading it gated on."""
    now = _now(clock)
    if now.timestamp() < ENABLED_FROM.timestamp():     # epoch seconds: a datetime subclass cannot vote on its own date
        raise NotYetEnabled(f"disabled until {ENABLED_FROM.isoformat()}")
    return now


def check_enabled(clock: Callable[[], datetime]) -> None:
    """Refuse (NotYetEnabled) when the clock reads earlier than ENABLED_FROM; a naive clock is refused too."""
    _gate(clock)


# ------------------------------------------------------------------ the price

def _valid_price(p: Any) -> bool:
    return (isinstance(p, (int, float)) and not isinstance(p, bool) and math.isfinite(p) and p > 0)


def _num(x: Any) -> float | None:
    """A finite number as a float, else None (so no NaN or infinity ever reaches the file)."""
    if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x):
        return float(x)
    return None


def default_trim(prices: Sequence[float]) -> list[float]:
    """The default rule: with 3 or 4 prices keep them all; with 5 or more drop the highest and the lowest."""
    s = sorted(prices)
    return s[1:-1] if len(s) >= TRIM_FROM else s


def _first_by_exchange(quotes: Sequence) -> dict:
    """The first quote for each exchange name. The frozen subset is applied by the callers, by name, once."""
    out: dict = {}
    for q in quotes or ():
        name = getattr(q, "exchange", None)
        if isinstance(name, str) and name not in out:
            out[name] = q
    return out


def responding(quotes: Sequence, frozen_subset: Sequence[str]) -> list:
    """The quotes that count as a response: inside the subset, ok, and a finite positive price."""
    found = _first_by_exchange(quotes)
    return [found[n] for n in frozen_subset
            if n in found and getattr(found[n], "ok", False) is True and _valid_price(getattr(found[n], "price", None))]


def composite(quotes: Sequence, frozen_subset: Sequence[str],
              trim: Callable[[Sequence[float]], Sequence[float]] = default_trim) -> float | None:
    """The trimmed median of the responding exchanges' prices, or None with fewer than three responding."""
    prices = [float(q.price) for q in responding(quotes, frozen_subset)]
    if len(prices) < MIN_RESPONDING:
        return None
    kept = list(trim(prices))
    return float(statistics.median(kept)) if kept else None


# ------------------------------------------------------------------ the book (recorded shape, four lines copied)

def _levels(rows: Any) -> list[list[float]]:
    out = [[float(p), float(s)] for p, s in (rows or [])]
    if any(not (math.isfinite(p) and math.isfinite(s)) for p, s in out):
        raise ValueError("non-finite book level")
    return sorted(out, key=lambda lv: lv[0], reverse=True)


def _book_record(book: dict) -> dict:
    yes, no = _levels(book.get("yes")), _levels(book.get("no"))
    bid, ask = (yes[0] if yes else None), (no[0] if no else None)
    ask_price = round(1.0 - ask[0], 6) if ask else None
    spread = round(ask_price - bid[0], 6) if (ask and bid) else None
    return {"yes": yes, "no": no,
            "best_bid": bid[0] if bid else None, "best_bid_size": bid[1] if bid else None,
            "best_ask": ask_price, "best_ask_size": ask[1] if ask else None, "spread": spread}


# ------------------------------------------------------------------ the store

def _jsonable(x: Any) -> Any:
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def _append(folder: Path, record: dict) -> None:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    line = json.dumps(_jsonable(record), sort_keys=True, separators=(",", ":"), allow_nan=False)
    with open(folder / LOG_NAME, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def _check_subset(frozen_subset: Any) -> tuple:
    if isinstance(frozen_subset, (str, bytes)) or not isinstance(frozen_subset, (tuple, list)):
        raise ValueError("frozen_subset must be a tuple or list of exchange names")
    names = tuple(frozen_subset)
    if not names or not all(isinstance(n, str) and n for n in names) or len(set(names)) != len(names):
        raise ValueError("frozen_subset must be a non-empty list of distinct exchange names")
    return names


def _check_folder(folder: Any) -> None:
    """The store folder must be an absolute path outside the repository (the folder that holds this package).

    A relative path of any spelling is refused: it means "wherever the process happens to be", which could be the
    repository. Paths are resolved first, so a `..` or a symbolic link cannot get around the check; an existing
    ancestor that is the very same directory as the repository root is refused too (letter case, hard aliases).
    """
    if not isinstance(folder, (str, os.PathLike)):
        raise ValueError("folder must name a directory")
    raw = os.fspath(folder)
    if not isinstance(raw, str) or not raw or not os.path.isabs(raw):
        raise ValueError("folder must be an absolute path (not empty, not relative)")
    resolved = Path(os.path.realpath(raw))
    root = Path(os.path.realpath(_REPO_ROOT))
    if resolved == root or root in resolved.parents:
        raise ValueError("folder must be outside the repository")
    for candidate in (resolved, *resolved.parents):
        if candidate.exists() and os.path.samefile(candidate, root):
            raise ValueError("folder must be outside the repository")


def _check_window(window: Window) -> None:
    if not (math.isfinite(window.open_ts) and math.isfinite(window.close_ts)) or window.close_ts <= window.open_ts:
        raise ValueError("a window must open before it closes")


def _exchange_entry(name: str, q: Any) -> dict:
    if q is None:
        return {"exchange": name, "ok": False, "counted": False, "price": None, "quote_ts": None,
                "closes_1m": [], "error": "no_quote"}
    ok = getattr(q, "ok", False) is True          # a quote object missing a field is a not-ok quote, not a lost window
    price = getattr(q, "price", None)
    counted = ok and _valid_price(price)
    error = getattr(q, "error", None)
    if error is not None and not isinstance(error, str):
        error = f"{type(error).__name__}: {error}"
    if ok and not counted and error is None:
        error = "invalid_price"
    try:
        closes = [_num(c) for c in (getattr(q, "closes_1m", None) or ())]
    except TypeError:
        closes = []
    return {"exchange": name, "ok": ok, "counted": counted, "price": _num(price),
            "quote_ts": _num(getattr(q, "quote_ts", None)), "closes_1m": closes, "error": error}


# ------------------------------------------------------------------ one record per window

def log_window(folder, window: Window, *, clock: Callable[[], datetime], read_quotes: Callable,
               read_book: Callable, frozen_subset: Sequence[str],
               trim: Callable[[Sequence[float]], Sequence[float]] = default_trim) -> dict:
    """Read the quotes and the book once each, write one record before the window closes, return it.

    Order: date check first (whatever the other arguments), then argument checks, then the readers, then the
    record. A window already closed, or closed while being read, is refused and writes nothing.
    """
    entered = _gate(clock)
    _check_folder(folder)
    names = _check_subset(frozen_subset)
    if not callable(read_quotes) or not callable(read_book):
        raise ValueError("read_quotes and read_book must be callables")
    _check_window(window)
    if entered.timestamp() >= window.close_ts:
        raise LateWindow(f"{window.window_ticker} is already closed")
    t_star = window.open_ts + T_STAR_OFFSET_S

    feed_error = None
    try:
        quotes = list(read_quotes(window, t_star) or ())
    except Exception as exc:                      # a failed feed is a skipped window, never a lost one
        quotes, feed_error = [], f"{type(exc).__name__}: {exc}"
    book_error = None
    try:
        raw_book = read_book(window.window_ticker)
        book = None if raw_book is None else _book_record(raw_book)
    except Exception as exc:
        book, book_error = None, f"{type(exc).__name__}: {exc}"

    written_at = _gate(clock).timestamp()          # one reading: gated on, checked against the close, and stamped
    if written_at >= window.close_ts:
        raise LateWindow(f"{window.window_ticker} closed before its record could be written")

    found = _first_by_exchange(quotes)
    price = composite(quotes, names, trim)
    n_responding = len(responding(quotes, names))
    reasons = []
    if price is None:
        reasons.append("SKIP_FEED")
    if book is None:
        reasons.append("SKIP_NO_BOOK")
    record = {
        "kind": "window", "schema_version": SCHEMA_VERSION,
        "window_ticker": window.window_ticker, "open_ts": window.open_ts, "close_ts": window.close_ts,
        "t_star": t_star, "floor_strike": _num(window.floor_strike), "written_at": written_at,
        "exchanges": [_exchange_entry(n, found.get(n)) for n in names],
        "n_responding": n_responding, "price": price,
        "book": book, "feed_error": feed_error, "book_error": book_error,
        "status": reasons[0] if reasons else "OK", "skip_reasons": reasons,
    }
    _append(folder, record)
    return record


# ------------------------------------------------------------------ the tape (only for a window the caller says posted)

def log_tape(folder, window: Window, *, posted: bool, read_tape: Callable, clock: Callable[[], datetime],
             budget: TapeBudget) -> dict | None:
    """Record the public tape rows over [t*, t* + 60 s] for a window the caller says posted an order.

    Not posted: no read, no budget spent, nothing written (returns None). Posted: one budgeted read, and a
    refusal once the budget is used up. The logger does not decide whether an order was posted.
    """
    entered = _gate(clock)
    _check_folder(folder)
    if not posted:
        return None
    if not callable(read_tape):
        raise ValueError("read_tape must be a callable")
    _check_window(window)
    if entered.timestamp() >= window.close_ts:
        raise LateWindow(f"{window.window_ticker} is already closed")
    budget.take()
    t_from = window.open_ts + T_STAR_OFFSET_S
    t_to = t_from + TAPE_SPAN_S
    tape_error = None
    try:
        raw = list(read_tape(window, t_from, t_to) or ())
    except Exception as exc:
        raw, tape_error = [], f"{type(exc).__name__}: {exc}"
    written_at = _gate(clock).timestamp()          # one reading: gated on, checked against the close, and stamped
    if written_at >= window.close_ts:
        raise LateWindow(f"{window.window_ticker} closed before its tape could be written")
    rows = [r for r in raw if isinstance(r, dict) and _num(r.get("created_ts")) is not None
            and t_from <= r["created_ts"] <= t_to]
    record = {"kind": "tape", "schema_version": SCHEMA_VERSION, "window_ticker": window.window_ticker,
              "t_from": t_from, "t_to": t_to, "written_at": written_at, "n_rows": len(rows), "rows": rows,
              "tape_error": tape_error}
    _append(folder, record)
    return _jsonable(record)


# ------------------------------------------------------------------ the dry run (fixtures only)

def dry_run(folder, clock: Callable[[], datetime], fixtures: Sequence[Fixture], frozen_subset: Sequence[str],
            trim: Callable[[Sequence[float]], Sequence[float]] = default_trim) -> list[dict]:
    """Run the whole per-window path over synthetic windows, with fake readers built from the fixtures.

    It takes no address and no fetch. It refuses a folder that already holds a log, and checks that exactly one
    record was written per fixture.
    """
    check_enabled(clock)
    _check_folder(folder)
    _check_subset(frozen_subset)
    if (Path(folder) / LOG_NAME).exists():
        raise ValueError("dry_run needs a fresh folder")
    records = []
    for fx in fixtures:
        records.append(log_window(folder, fx.window, clock=clock,
                                  read_quotes=lambda w, t, fx=fx: fx.quotes,
                                  read_book=lambda ticker, fx=fx: fx.book,
                                  frozen_subset=frozen_subset, trim=trim))
    written = (Path(folder) / LOG_NAME).read_text(encoding="utf-8").splitlines() if records else []
    if len(written) != len(fixtures):
        raise RuntimeError(f"expected {len(fixtures)} records, found {len(written)}")
    return records
