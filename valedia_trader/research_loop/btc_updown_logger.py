"""Forward logger for the BTC 15-minute Up/Down test: a LOG-ONLY tool, disabled until 2026-12-04 00:00 Pacific.

What it does: for one 15-minute window it writes ONE window record, before the window closes, skips included. The
record holds the raw inputs it was handed: the BRTI tick used at t* (= open + 300 s), the raw BRTI series over
[t* - 300 s, t*], the Kalshi book at t*, and the status (OK, or SKIP_STALE_INDEX / SKIP_WARMUP_OR_GAP / SKIP_NO_BOOK
and why). After the close, a SEPARATE close record (kind "close") holds the settlement estimate read from the close
tick. It makes no trade decision and computes no trigger, fill or eligibility field, and no measure of the series;
those come from the locked test document, later, offline. Kalshi's settled market stays the authoritative outcome.

The price input is BRTI and nothing else: the `cfbenchmarks_value` channel's ticks, as the read-only index client
records them, one JSON row per tick, in `<tick folder>/kalshi_index_ticks-YYYY-MM-DD.jsonl` (UTC day of the row's
`received_at_ms`). `brti_raw` is the Decimal of the raw `data` frame's `value`; the tick's source time is the raw
`data` frame's `time` (`cf_source_ts_ms`). The 60-second average (`avg_60s_data`) is copied as metadata only and is
never used as a price.

What it does not do: it makes no request of any kind. Ticks, the book and the tape arrive through caller-supplied
reader callables. `read_tick_files` / `tick_file_reader` below are the one adapter that reads the tick store's files
for a time span: files only, nothing else. Every public function first checks the date constant `ENABLED_FROM`
against an injected, timezone-aware clock and refuses before that instant, whatever its other arguments.

Every written number that is not a whole number is a decimal string (prices, sizes, timestamps in seconds): no
float reaches the file. Whole numbers (milliseconds, sequence numbers, counts) are JSON integers.

Its store is `<folder>/btc_updown_log.jsonl` under a folder the caller names: there is no default folder, and the
folder must be an absolute path outside the repository. The tick folder obeys the same rule.
Standard library only; nothing from the rest of the package is imported.
"""
from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

# The enabling instant: 2026-12-04 00:00 America/Los_Angeles (PST, UTC-8) = 08:00 UTC. Before it, nothing is read
# and nothing is written.
ENABLED_FROM = datetime(2026, 12, 4, 8, 0, 0, tzinfo=timezone.utc)
SCHEMA_VERSION = 2
LOG_NAME = "btc_updown_log.jsonl"
_REPO_ROOT = Path(__file__).resolve().parents[2]   # the folder that holds the `valedia_trader` package
T_STAR_OFFSET_S = 300.0     # t* = the window's open + 5 minutes
TAPE_SPAN_S = 60.0          # the tape is read over [t*, t* + 60 s]

# The BRTI input (Carbon round 58, adopted by the lead).
INDEX_ID = "BRTI"
INDEX_CHANNEL = "cfbenchmarks_value"
TICK_FILE_PREFIX = "kalshi_index_ticks-"     # the index client's file names: <prefix>YYYY-MM-DD.jsonl
SERIES_SPAN_MS = 300_000    # the raw series is logged over [t* - 300 s, t*]
STALE_MAX_MS = 3_000        # t*_ms - cf_source_ts_ms of the tick used: below 0 or above this is SKIP_STALE_INDEX
MIN_TICKS = 270             # fewer valid ticks than this in the series span is SKIP_WARMUP_OR_GAP
ANCHOR_BACK_MS = 180_000    # a tick is needed within ANCHOR_TOL_MS of t* - 180 s
ANCHOR_TOL_MS = 3_000
MAX_GAP_MS = 5_000          # a gap above this between consecutive ticks in the span is SKIP_WARMUP_OR_GAP
SKIP_STALE_INDEX = "SKIP_STALE_INDEX"
SKIP_WARMUP_OR_GAP = "SKIP_WARMUP_OR_GAP"
SKIP_NO_BOOK = "SKIP_NO_BOOK"

# The close record: the settlement estimate from the close tick.
CLOSE_BEFORE_MS = 60_000    # the close span is [close - 60 s, close + 10 s]
CLOSE_AFTER_MS = 10_000     # and the close record is refused before close + 10 s, so the span is complete
CLOSE_WINDOW_SIZE = 60      # the close tick is the first tick whose 15-minute windowed average has window_size 60
SETTLE_QUANTUM = Decimal("0.01")
SETTLE_ROUNDING = ROUND_HALF_EVEN
FILE_SLACK_MS = 60_000      # files are dated by receipt, ticks chosen by source time: read the neighbouring day too


class NotYetEnabled(RuntimeError):
    """The clock reads earlier than ENABLED_FROM: nothing is read or written."""


class NaiveClock(ValueError):
    """The clock returned a datetime with no timezone."""


class LateWindow(RuntimeError):
    """The window has closed (or closed while it was being read): a record would not be written before settlement."""


class CloseNotReached(RuntimeError):
    """The close record was asked for before the close span ended: nothing is read or written."""


class TapeBudgetExceeded(RuntimeError):
    """The run's tape reads are used up."""


@dataclass(frozen=True)
class Window:
    window_ticker: str
    open_ts: float
    close_ts: float
    floor_strike: float | None = None


@dataclass(frozen=True)
class Fixture:
    """One synthetic window for `dry_run`: its tick rows (the tick store's row shape) and its book (None = no book)."""
    window: Window
    ticks: Sequence
    book: dict | None


@dataclass(frozen=True)
class _Tick:
    source_ms: int                 # the raw data frame's `time`
    value: Decimal                 # the raw data frame's `value`
    received_at_ms: int            # Kalshi's `received_at`
    sending_ts_ms: int | None      # Kalshi's `sending_ts_ms`
    seq: int
    sid: int | None
    local_receipt_ms: int | None   # the local receipt time, when the row carries one
    avg_60s: dict | None           # metadata only
    last_15min_avg: dict | None


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


# ------------------------------------------------------------------ numbers

def _num(x: Any) -> float | None:
    """A finite number as a float, else None (so no NaN or infinity ever reaches the file)."""
    if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x):
        return float(x)
    return None


def _dec(x: Any) -> Decimal:
    """A finite Decimal from an int, a decimal string, a Decimal or a float (through its shortest repr)."""
    if isinstance(x, bool):
        raise ValueError("a boolean is not a number")
    try:
        d = Decimal(repr(x)) if isinstance(x, float) else Decimal(x)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("not a decimal number") from None
    if not d.is_finite():
        raise ValueError("not a finite number")
    return d


def _whole(x: Any) -> int | None:
    """A whole number (int, or a string of digits), else None. A float or a boolean is not one."""
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, str) and 0 < len(x) <= 19 and (x.isdigit() or (x[0] == "-" and x[1:].isdigit())):
        return int(x)
    return None


def _ms(ts_s: float) -> int:
    return int(round(ts_s * 1000))


# ------------------------------------------------------------------ the tick rows

def _window_avg(node: Any) -> dict | None:
    """A windowed-average object from the store row (value, window_size, window_start_ts_ms, window_end_ts_exclusive)."""
    if not isinstance(node, dict):
        raise ValueError("a window average must be an object")
    value = node.get("value")
    if isinstance(value, float):
        raise ValueError("a window average value must not be a float")
    out = {"value": _dec(value)}
    for key in ("window_size", "window_start_ts_ms", "window_end_ts_exclusive"):
        n = _whole(node.get(key))
        if n is None:
            raise ValueError(f"{key} must be a whole number")
        out[key] = n
    return out


def _parse_tick_row(row: Any) -> _Tick | None:
    """One tick-store row to a _Tick, or None when it is not a valid BRTI `cfbenchmarks_value` tick.

    The price and the source time come from the raw `data` frame (its `value` and `time`), never from `avg_60s`.
    """
    if not isinstance(row, dict) or row.get("index_id") != INDEX_ID or row.get("channel") != INDEX_CHANNEL:
        return None
    data = row.get("data")
    if not isinstance(data, str):
        return None
    try:
        raw = json.loads(data, parse_float=Decimal)    # a number in the raw frame keeps every digit it was sent with
    except (ValueError, RecursionError):
        return None
    if not isinstance(raw, dict):
        return None
    source_ms = _whole(raw.get("time"))
    received = _whole(row.get("received_at_ms"))
    seq = _whole(row.get("seq"))
    if source_ms is None or received is None or seq is None:
        return None
    sending_raw, sid_raw, local_raw = row.get("sending_ts_ms"), row.get("sid"), row.get("local_receipt_ms")
    sending = None if sending_raw is None else _whole(sending_raw)
    sid = None if sid_raw is None else _whole(sid_raw)
    local = None if local_raw is None else _whole(local_raw)
    if (sending_raw is not None and sending is None) or (sid_raw is not None and sid is None) \
            or (local_raw is not None and local is None):
        return None
    try:
        value = _dec(raw.get("value"))
        avg = None if row.get("avg_60s") is None else _window_avg(row.get("avg_60s"))
        last15 = None if row.get("last_15min_avg") is None else _window_avg(row.get("last_15min_avg"))
    except ValueError:
        return None
    if value <= 0:
        return None
    return _Tick(source_ms=source_ms, value=value, received_at_ms=received, sending_ts_ms=sending, seq=seq, sid=sid,
                 local_receipt_ms=local, avg_60s=avg, last_15min_avg=last15)


def _ticks_in(rows: Iterable, t_from_ms: int, t_to_ms: int) -> tuple[list[_Tick], dict]:
    """Valid ticks with source time in [t_from_ms, t_to_ms], one per source time (the first received), in time order."""
    counts = {"n_rows_read": 0, "n_rows_malformed": 0, "n_rows_outside_span": 0, "n_duplicates_dropped": 0}
    kept: list[_Tick] = []
    for row in rows:
        counts["n_rows_read"] += 1
        tick = _parse_tick_row(row)
        if tick is None:
            counts["n_rows_malformed"] += 1
        elif not (t_from_ms <= tick.source_ms <= t_to_ms):
            counts["n_rows_outside_span"] += 1
        else:
            kept.append(tick)
    kept.sort(key=lambda t: (t.source_ms, t.received_at_ms, t.seq))
    out: list[_Tick] = []
    for tick in kept:
        if out and out[-1].source_ms == tick.source_ms:
            counts["n_duplicates_dropped"] += 1        # a repeated source time is the same tick, not another one
            continue
        out.append(tick)
    return out, counts


def _tick_record(tick: _Tick) -> dict:
    return {
        "local_receipt_ms": tick.local_receipt_ms,
        "local_receipt_note": None if tick.local_receipt_ms is not None else "not carried by the tick store row",
        "received_at": tick.received_at_ms, "sending_ts_ms": tick.sending_ts_ms, "seq": tick.seq, "sid": tick.sid,
        "cf_source_ts_ms": tick.source_ms, "brti_raw": tick.value,
        "avg_60s_data": tick.avg_60s,                  # metadata only: never a price
    }


# ------------------------------------------------------------------ the adapter: the tick store's files, read only

def _check_span(t_from_ms: Any, t_to_ms: Any) -> tuple[int, int]:
    a, b = _whole(t_from_ms), _whole(t_to_ms)
    if a is None or b is None or isinstance(t_from_ms, str) or isinstance(t_to_ms, str) or b < a:
        raise ValueError("the span must be two whole millisecond numbers, the first not after the second")
    return a, b


def read_tick_files(tick_folder, t_from_ms: int, t_to_ms: int, *, clock: Callable[[], datetime]) -> list[dict]:
    """The tick-store rows whose source time is in [t_from_ms, t_to_ms], in file order, malformed lines skipped.

    It reads `<tick_folder>/kalshi_index_ticks-YYYY-MM-DD.jsonl` for every UTC day the span (widened by
    FILE_SLACK_MS each side, because a file is dated by receipt) touches; a missing file is no rows. Files only.
    """
    _gate(clock)
    _check_folder(tick_folder)
    a, b = _check_span(t_from_ms, t_to_ms)
    first = datetime.fromtimestamp((a - FILE_SLACK_MS) // 1000, timezone.utc).date()
    last = datetime.fromtimestamp((b + FILE_SLACK_MS) // 1000, timezone.utc).date()
    out: list[dict] = []
    day = first
    while day <= last:
        path = Path(tick_folder) / f"{TICK_FILE_PREFIX}{day.isoformat()}.jsonl"
        if path.is_file():
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        row = json.loads(line, parse_float=Decimal)
                    except (ValueError, RecursionError):
                        continue
                    tick = _parse_tick_row(row)
                    if tick is not None and a <= tick.source_ms <= b:
                        out.append(row)
        day += timedelta(days=1)
    return out


def tick_file_reader(tick_folder, *, clock: Callable[[], datetime]) -> Callable:
    """A `read_ticks` callable for `log_window` / `log_close` that reads the tick store's files (no other source)."""
    _gate(clock)
    _check_folder(tick_folder)

    def read_ticks(window: Window, t_from_ms: int, t_to_ms: int) -> list[dict]:
        return read_tick_files(tick_folder, t_from_ms, t_to_ms, clock=clock)
    return read_ticks


# ------------------------------------------------------------------ the book (recorded shape)

def _levels(rows: Any) -> list[list[Decimal]]:
    out = [[_dec(p), _dec(s)] for p, s in (rows or [])]
    return sorted(out, key=lambda lv: lv[0], reverse=True)


def _book_record(book: dict) -> dict:
    yes, no = _levels(book.get("yes")), _levels(book.get("no"))
    bid, ask = (yes[0] if yes else None), (no[0] if no else None)
    ask_price = Decimal(1) - ask[0] if ask else None
    spread = ask_price - bid[0] if (ask and bid) else None
    return {"yes": yes, "no": no,
            "best_bid": bid[0] if bid else None, "best_bid_size": bid[1] if bid else None,
            "best_ask": ask_price, "best_ask_size": ask[1] if ask else None, "spread": spread}


# ------------------------------------------------------------------ the store

def _jsonable(x: Any) -> Any:
    """Decimals and floats to decimal strings (a non-finite float to None); no float survives."""
    if isinstance(x, bool) or x is None or isinstance(x, (int, str)):
        return x
    if isinstance(x, Decimal):
        return format(x, "f") if x.is_finite() else None
    if isinstance(x, float):
        return format(Decimal(repr(x)), "f") if math.isfinite(x) else None
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


def _read_rows(read_ticks: Callable, window: Window, t_from_ms: int, t_to_ms: int) -> tuple[list, str | None]:
    try:
        return list(read_ticks(window, t_from_ms, t_to_ms) or ()), None
    except Exception as exc:                      # a failed reader is a skipped window, never a lost one
        return [], f"{type(exc).__name__}: {exc}"


# ------------------------------------------------------------------ the status at t*

def _series_checks(ticks: list[_Tick], t_star_ms: int) -> dict:
    """The warm-up and gap facts of the series span (counts and spacing only: no measure of the price)."""
    anchor = t_star_ms - ANCHOR_BACK_MS
    nearest = min(ticks, key=lambda t: abs(t.source_ms - anchor), default=None)
    gaps = [b.source_ms - a.source_ms for a, b in zip(ticks, ticks[1:])]
    max_gap = max(gaps) if gaps else None
    count_ok = len(ticks) >= MIN_TICKS
    anchor_ok = nearest is not None and abs(nearest.source_ms - anchor) <= ANCHOR_TOL_MS
    gap_ok = max_gap is None or max_gap <= MAX_GAP_MS
    return {"n_ticks": len(ticks), "min_ticks": MIN_TICKS, "count_ok": count_ok,
            "anchor_ts_ms": anchor, "anchor_tick_ms": nearest.source_ms if nearest else None,
            "anchor_tol_ms": ANCHOR_TOL_MS, "anchor_ok": anchor_ok,
            "max_gap_ms": max_gap, "max_gap_allowed_ms": MAX_GAP_MS, "gap_ok": gap_ok,
            "warm": count_ok and anchor_ok and gap_ok}


def _status(used: _Tick | None, t_star_ms: int, checks: dict, book: dict | None) -> tuple[int | None, list[str]]:
    """The skip reasons in their fixed order: SKIP_STALE_INDEX, then SKIP_WARMUP_OR_GAP, then SKIP_NO_BOOK."""
    lag = None if used is None else t_star_ms - used.source_ms
    reasons = []
    if lag is None or lag < 0 or lag > STALE_MAX_MS:
        reasons.append(SKIP_STALE_INDEX)
    if not checks["warm"]:
        reasons.append(SKIP_WARMUP_OR_GAP)
    if book is None:
        reasons.append(SKIP_NO_BOOK)
    return lag, reasons


# ------------------------------------------------------------------ one record per window

def log_window(folder, window: Window, *, clock: Callable[[], datetime], read_ticks: Callable,
               read_book: Callable) -> dict:
    """Read the ticks and the book once each, write one window record before the window closes, return it.

    `read_ticks(window, t_from_ms, t_to_ms)` returns tick-store rows; it is asked for [t* - 300 s, t*] by source time.
    Order: date check first (whatever the other arguments), then argument checks, then the readers, then the
    record. A window already closed, or closed while being read, is refused and writes nothing.
    """
    entered = _gate(clock)
    _check_folder(folder)
    if not callable(read_ticks) or not callable(read_book):
        raise ValueError("read_ticks and read_book must be callables")
    _check_window(window)
    if entered.timestamp() >= window.close_ts:
        raise LateWindow(f"{window.window_ticker} is already closed")
    t_star = window.open_ts + T_STAR_OFFSET_S
    t_star_ms = _ms(t_star)
    span_from_ms = t_star_ms - SERIES_SPAN_MS

    rows, tick_error = _read_rows(read_ticks, window, span_from_ms, t_star_ms)
    book_error = None
    try:
        raw_book = read_book(window.window_ticker)
        book = None if raw_book is None else _book_record(raw_book)
    except Exception as exc:
        book, book_error = None, f"{type(exc).__name__}: {exc}"

    written_at = _gate(clock).timestamp()          # one reading: gated on, checked against the close, and stamped
    if written_at >= window.close_ts:
        raise LateWindow(f"{window.window_ticker} closed before its record could be written")

    ticks, counts = _ticks_in(rows, span_from_ms, t_star_ms)
    used = ticks[-1] if ticks else None            # the latest tick whose source time is at or before t*
    checks = _series_checks(ticks, t_star_ms)
    lag, reasons = _status(used, t_star_ms, checks, book)
    record = {
        "kind": "window", "schema_version": SCHEMA_VERSION,
        "window_ticker": window.window_ticker, "open_ts": window.open_ts, "close_ts": window.close_ts,
        "t_star": t_star, "t_star_ms": t_star_ms, "floor_strike": _num(window.floor_strike), "written_at": written_at,
        "index_id": INDEX_ID, "channel": INDEX_CHANNEL,
        "tick_used": None if used is None else _tick_record(used), "index_lag_ms": lag,
        "series_from_ms": span_from_ms, "series_to_ms": t_star_ms,
        "series": [{"cf_source_ts_ms": t.source_ms, "brti_raw": t.value} for t in ticks],
        "series_checks": checks, **counts,
        "book": book, "tick_error": tick_error, "book_error": book_error,
        "status": reasons[0] if reasons else "OK", "skip_reasons": reasons,
    }
    _append(folder, record)
    return _jsonable(record)


# ------------------------------------------------------------------ the close record (settlement estimate only)

def log_close(folder, window: Window, *, clock: Callable[[], datetime], read_ticks: Callable) -> dict:
    """After the close, write the close record: the settlement estimate from the close tick, or None and why.

    The close tick is the first tick (by source time) in [close - 60 s, close + 10 s] whose
    `last_60s_windowed_average_15min` has window_size 60; the estimate is its value rounded to 2 dp, ROUND_HALF_EVEN.
    It is an estimate only: the authoritative outcome is Kalshi's settled market, read later, offline.
    """
    entered = _gate(clock)
    _check_folder(folder)
    if not callable(read_ticks):
        raise ValueError("read_ticks must be a callable")
    _check_window(window)
    close_ms = _ms(window.close_ts)
    if _ms(entered.timestamp()) < close_ms + CLOSE_AFTER_MS:
        raise CloseNotReached(f"{window.window_ticker}: the close span ends at close + {CLOSE_AFTER_MS} ms")
    t_from, t_to = close_ms - CLOSE_BEFORE_MS, close_ms + CLOSE_AFTER_MS
    rows, tick_error = _read_rows(read_ticks, window, t_from, t_to)
    written_at = _gate(clock).timestamp()
    ticks, counts = _ticks_in(rows, t_from, t_to)
    candidates = [t for t in ticks if t.last_15min_avg is not None
                  and t.last_15min_avg["window_size"] == CLOSE_WINDOW_SIZE]
    close_tick = candidates[0] if candidates else None
    if close_tick is not None:
        avg = close_tick.last_15min_avg
        estimate, reason = avg["value"].quantize(SETTLE_QUANTUM, rounding=SETTLE_ROUNDING), None
    else:
        avg, estimate = None, None
        reason = "tick_reader_error" if tick_error else "no_close_tick"
    record = {
        "kind": "close", "schema_version": SCHEMA_VERSION, "window_ticker": window.window_ticker,
        "close_ts": window.close_ts, "close_ms": close_ms, "written_at": written_at,
        "index_id": INDEX_ID, "channel": INDEX_CHANNEL, "span_from_ms": t_from, "span_to_ms": t_to,
        "settlement_estimate": estimate, "settlement_estimate_reason": reason,
        "rounding": "2 dp, ROUND_HALF_EVEN",
        "close_tick": None if close_tick is None else {
            "cf_source_ts_ms": close_tick.source_ms, "received_at": close_tick.received_at_ms,
            "sending_ts_ms": close_tick.sending_ts_ms, "seq": close_tick.seq,
            "local_receipt_ms": close_tick.local_receipt_ms, "last_60s_windowed_average_15min": avg},
        "n_close_candidates": len(candidates),
        "close_candidates_agree": len({t.last_15min_avg["value"] for t in candidates}) <= 1,
        "outcome_source": "Kalshi settled market (result, expiration_value), read later, offline",
        **counts, "tick_error": tick_error,
    }
    _append(folder, record)
    return _jsonable(record)


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

def dry_run(folder, clock: Callable[[], datetime], fixtures: Sequence[Fixture]) -> list[dict]:
    """Run the whole per-window path over synthetic windows, with fake readers built from the fixtures' tick rows.

    It takes no address and no fetch. It refuses a folder that already holds a log, and checks that exactly one
    record was written per fixture.
    """
    check_enabled(clock)
    _check_folder(folder)
    if (Path(folder) / LOG_NAME).exists():
        raise ValueError("dry_run needs a fresh folder")
    records = []
    for fx in fixtures:
        records.append(log_window(folder, fx.window, clock=clock,
                                  read_ticks=lambda w, a, b, fx=fx: fx.ticks,
                                  read_book=lambda ticker, fx=fx: fx.book))
    written = (Path(folder) / LOG_NAME).read_text(encoding="utf-8").splitlines() if records else []
    if len(written) != len(fixtures):
        raise RuntimeError(f"expected {len(fixtures)} records, found {len(written)}")
    return records
