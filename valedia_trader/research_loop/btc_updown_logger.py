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
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Context, Decimal, InvalidOperation, localcontext
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
T_STAR_OFFSET_MS = 300_000  # the same two, in exact ms: every time derived from a window uses these
TAPE_SPAN_MS = 60_000

# The BRTI input (Carbon round 58, adopted by the lead).
INDEX_ID = "BRTI"
INDEX_CHANNEL = "cfbenchmarks_value"
TICK_FILE_PREFIX = "kalshi_index_ticks-"     # the index client's file names: <prefix>YYYY-MM-DD.jsonl
SERIES_SPAN_MS = 300_000    # the raw series is logged over [t* - 300 s, t*]
STALE_MAX_MS = 3_000        # t*_ms - cf_source_ts_ms of the tick used: below 0 or above this is SKIP_STALE_INDEX
MIN_TICKS = 270             # fewer valid ticks than this in the series span is SKIP_WARMUP_OR_GAP
ANCHOR_BACK_MS = 180_000    # a tick is needed within ANCHOR_TOL_MS of t* - 180 s
ANCHOR_TOL_MS = 3_000
MAX_GAP_MS = 5_000          # a gap above this between consecutive ticks, or at either edge of the span, is a skip
LATE_MAX_MS = 60_000        # a tick is valid only if -EARLY_MAX_MS <= received_at - source time <= LATE_MAX_MS
EARLY_MAX_MS = 2_000        # (signed: a tick received more than 2 s before its own source time is not valid)
SKIP_STALE_INDEX = "SKIP_STALE_INDEX"
SKIP_WARMUP_OR_GAP = "SKIP_WARMUP_OR_GAP"
SKIP_NO_BOOK = "SKIP_NO_BOOK"

# The close record: the settlement estimate from the close tick.
CLOSE_BEFORE_MS = 60_000    # the close span is [close - 60 s, close + 10 s] by source time
CLOSE_AFTER_MS = 10_000     # the record is refused before close + CLOSE_WAIT_MS (see below)
CLOSE_WINDOW_SIZE = 60      # an exact close tick: window_size 60 and source time in [close, close + CLOSE_EXACT_MS)
CLOSE_EXACT_MS = 1_000
CLOSE_PARTIAL_BACK_MS = 2_000   # the fallback: source time in [close - 2 s, close + 1 s), window_size 55..59
CLOSE_PARTIAL_MIN_SIZE = 55
LOCAL_MEAN_SPAN_MS = 60_000     # local_brti_mean: brti_raw over source times in (close - 60 s, close]
LOCAL_MEAN_DP = 8
SETTLE_QUANTUM = Decimal("0.01")
SETTLE_ROUNDING = ROUND_HALF_EVEN
_SETTLE_CONTEXT = Context(prec=60, rounding=ROUND_HALF_EVEN, traps=[InvalidOperation])   # local: not the caller's
RECEIPT_MARGIN_MS = 120_000  # receipt time vs source time skew allowed for: which day files to open, and where to stop
# The close record waits until close + 10 s + 60 s + 120 s = close + 190 s: a valid tick of the span has Kalshi
# received_at at most LATE_MAX_MS after its source time, and is delivered and written within RECEIPT_MARGIN_MS of that
# (the reader's own bound). A tick delivered and written later than that can be missed by the close record.
CLOSE_WAIT_MS = CLOSE_AFTER_MS + LATE_MAX_MS + RECEIPT_MARGIN_MS
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
READ_BLOCK_BYTES = 65_536   # the adapter reads each file backwards from its end in blocks of this size
MAX_LINE_BYTES = 1_048_576  # a line longer than this is not a tick row: it is skipped without being held in memory


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
    avg_60s_unusable: bool = False      # the row carried an avg_60s whose value failed `_price`: set to None
    last15_unusable: bool = False       # the same for last_15min_avg: no close candidate in either tier


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
    """One clock reading: exactly a datetime (no subclass, which could lie through its own methods or its tzinfo
    property) and timezone-aware, else NaiveClock. The base-class arithmetic below then stands on a real datetime."""
    now = clock()
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise NaiveClock("the clock must return a timezone-aware datetime (exactly a datetime, not a subclass)")
    return now


def _gate(clock: Callable[[], datetime]) -> datetime:
    """ONE clock reading, checked against ENABLED_FROM and returned, so a caller uses the reading it gated on."""
    now = _now(clock)
    # the base class's arithmetic, with the clock's utcoffset trusted (not the clock's own timestamp())
    if datetime.timestamp(now) < datetime.timestamp(ENABLED_FROM):
        raise NotYetEnabled(f"disabled until {ENABLED_FROM.isoformat()}")
    return now


def check_enabled(clock: Callable[[], datetime]) -> None:
    """Refuse (NotYetEnabled) when the clock reads earlier than ENABLED_FROM; a naive clock is refused too."""
    _gate(clock)


# ------------------------------------------------------------------ numbers

def _num(x: Any) -> float | None:
    """A finite number as a float, else None (so no NaN or infinity ever reaches the file); an int beyond float
    range is None too, never an OverflowError."""
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        try:
            f = float(x)
        except OverflowError:
            return None
        if math.isfinite(f):
            return f
    return None


def _dec(x: Any) -> Decimal:
    """A finite Decimal from an int, a decimal string, a Decimal or a float (through its shortest repr)."""
    if isinstance(x, bool):
        raise ValueError("a boolean is not a number")
    try:
        d = Decimal(repr(x)) if isinstance(x, float) else Decimal(x)
    except (ArithmeticError, TypeError, ValueError):     # InvalidOperation is an ArithmeticError
        raise ValueError("not a decimal number") from None
    if not d.is_finite():
        raise ValueError("not a finite number")
    return d


class _BadNumber:
    """An inert stand-in for a JSON numeric literal too long to parse safely: it is not a number to any check
    (`_dec` refuses it, `_whole` gives None), and it is never written."""
    __slots__ = ()

    def __repr__(self) -> str:
        return "<bad number>"


_BAD_NUMBER = _BadNumber()
JSON_NUMBER_MAX_CHARS = 64     # a numeric literal longer than this, or with an exponent of more than 6 digits,
JSON_EXPONENT_MAX_DIGITS = 6   # becomes _BAD_NUMBER instead of a number


def _json_float(text: str) -> Any:
    """json's parse_float hook: a Decimal keeping every digit sent, or _BAD_NUMBER. Never raises."""
    exp = text.lower().partition("e")[2].lstrip("+-")
    if len(text) > JSON_NUMBER_MAX_CHARS or len(exp) > JSON_EXPONENT_MAX_DIGITS:
        return _BAD_NUMBER
    try:
        return Decimal(text)
    except (ArithmeticError, ValueError):
        return _BAD_NUMBER


def _json_int(text: str) -> Any:
    """json's parse_int hook: an int, or _BAD_NUMBER for a literal longer than JSON_NUMBER_MAX_CHARS characters,
    sign included. Never raises."""
    if len(text) > JSON_NUMBER_MAX_CHARS:
        return _BAD_NUMBER
    try:
        return int(text)
    except (ArithmeticError, ValueError):
        return _BAD_NUMBER


def _json(text: str) -> Any:
    """json.loads with the two number hooks (the raw `data` frame and the store line alike)."""
    return json.loads(text, parse_float=_json_float, parse_int=_json_int)


PRICE_INT_DIGITS = 15   # a price-like Decimal has at most 15 digits before the point (adjusted() <= 14)
PRICE_DP = 12           # and at most 12 after it (exponent >= -12)


def _price(x: Any) -> Decimal:
    """A price-like Decimal (a tick's value, a window average's value): finite, positive, at most PRICE_INT_DIGITS
    digits before the point and PRICE_DP after, by its Decimal tuple, from a string or a JSON number alike. Checked
    before any arithmetic, so a huge or tiny value never reaches rounding, the mean or the writer."""
    d = _dec(x)
    if d <= 0 or d.as_tuple().exponent < -PRICE_DP or d.adjusted() > PRICE_INT_DIGITS - 1:
        raise ValueError("not a price: positive, at most 15 digits before the point and 12 after")
    return d


def _whole(x: Any) -> int | None:
    """A whole number (int, or a string of ASCII digits), else None. A float or a boolean is not one, and neither is
    a string of other Unicode digits ("²", "١٢"), which int() would refuse or misread."""
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if (isinstance(x, str) and 0 < len(x) <= 19 and x.isascii()
            and (x.isdigit() or (x[0] == "-" and x[1:].isdigit()))):
        return int(x)
    return None


def _ms(ts_s: float) -> int:
    return int(round(ts_s * 1000))


def _clock_us(now: datetime) -> int:
    """Exact integer microseconds since the epoch: the base class's arithmetic, with the clock's utcoffset trusted."""
    return datetime.__sub__(now, _EPOCH) // timedelta(microseconds=1)


def _ms_to_s(ms: int) -> Decimal:
    """Exact seconds from exact integer ms (a local context: the caller's cannot change it)."""
    with localcontext(_TS_CONTEXT):
        return Decimal(ms) / 1000


# ------------------------------------------------------------------ the tick rows

def _window_avg(node: Any) -> dict | None:
    """A windowed-average object from the store row (value, window_size, window_start_ts_ms, window_end_ts_exclusive).

    A structurally bad average (not an object, a float value, a window field that is not a whole number) raises
    ValueError: the row is malformed. A value that fails `_price` makes the AVERAGE unusable, not the row: None is
    returned, and the out-of-bound value is never kept (an average never costs a tick its price).
    """
    if not isinstance(node, dict):
        raise ValueError("a window average must be an object")
    value = node.get("value")
    if isinstance(value, float):
        raise ValueError("a window average value must not be a float")
    out: dict = {}
    for key in ("window_size", "window_start_ts_ms", "window_end_ts_exclusive"):
        n = _whole(node.get(key))
        if n is None:
            raise ValueError(f"{key} must be a whole number")
        out[key] = n
    try:
        out = {"value": _price(value), **out}
    except ValueError:
        return None                                  # unusable average: the tick keeps its price
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
        raw = _json(data)                              # a number in the raw frame keeps every digit it was sent with
    except (ValueError, RecursionError, ArithmeticError):
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
        value = _price(raw.get("value"))
        avg = None if row.get("avg_60s") is None else _window_avg(row.get("avg_60s"))
        last15 = None if row.get("last_15min_avg") is None else _window_avg(row.get("last_15min_avg"))
        avg_bad = row.get("avg_60s") is not None and avg is None
        last15_bad = row.get("last_15min_avg") is not None and last15 is None
    except (ValueError, ArithmeticError):
        return None
    if value <= 0:
        return None
    return _Tick(source_ms=source_ms, value=value, received_at_ms=received, sending_ts_ms=sending, seq=seq, sid=sid,
                 local_receipt_ms=local, avg_60s=avg, last_15min_avg=last15,
                 avg_60s_unusable=avg_bad, last15_unusable=last15_bad)


def _row_order(t: _Tick) -> tuple:
    """(source time, received_at, seq), then the parsed row itself, so rows that tie on the first three are still
    ordered by their content and never by the order the reader returned them in."""
    return (t.source_ms, t.received_at_ms, t.seq, repr(t))


def _same_tick(t: _Tick) -> tuple:
    """What the dedupe compares: rows equal in this are one row (identical rows counted once)."""
    return (t.source_ms, t.value, repr(t.avg_60s), repr(t.last_15min_avg), t.avg_60s_unusable, t.last15_unusable)


def _ticks_in(rows: Iterable, t_from_ms: int, t_to_ms: int, conflicts: list | None = None,
              meta_groups: list | None = None, in_span: list | None = None) -> tuple[list[_Tick], dict]:
    """Valid ticks with source time in [t_from_ms, t_to_ms], one per source time, in time order.

    Valid: a well-formed BRTI `cfbenchmarks_value` row with -EARLY_MAX_MS <= received_at - source time <=
    LATE_MAX_MS (signed); a row that is not is counted as late (`n_rows_late`) and never used.
    Rows are grouped by source time, and each group is ordered by (received_at, seq).
    - Every row has an equal Decimal value: one tick, the first row kept. Each other row is counted against the
      kept row: in `n_duplicates_dropped` if its avg_60s and last_15min_avg are equal too (compared parsed), else in
      `n_dup_diff_meta` (so [A, A, A'] counts 1 and 1); a group with any such row is appended whole to
      `meta_groups` when it is given.
    - Any two values differ: the group is dropped whole and counted in `n_conflicting_source_times` (fail closed);
      if `conflicts` is given, the dropped rows are appended to it; `n_conflicting_rows` counts those rows.
    The counts reconcile: n_rows_read == n_rows_malformed + n_rows_late + n_rows_outside_span + n_ticks_kept
    + n_duplicates_dropped + n_dup_diff_meta + n_conflicting_rows, where n_ticks_kept is the number of ticks returned.
    `n_avg_unusable` is outside that identity: it counts the valid rows in the span (any of the last four buckets)
    that carried an average made unusable by `_price` (one per row, whichever average). If `in_span` is given, every
    valid row in the span is appended to it.
    """
    counts = {"n_rows_read": 0, "n_rows_malformed": 0, "n_rows_late": 0, "n_rows_outside_span": 0,
              "n_duplicates_dropped": 0, "n_dup_diff_meta": 0, "n_conflicting_source_times": 0,
              "n_conflicting_rows": 0, "n_avg_unusable": 0}
    kept: list[_Tick] = []
    for row in rows:
        counts["n_rows_read"] += 1
        tick = _parse_tick_row(row)
        if tick is None:
            counts["n_rows_malformed"] += 1
        elif not (-EARLY_MAX_MS <= tick.received_at_ms - tick.source_ms <= LATE_MAX_MS):
            counts["n_rows_late"] += 1                  # one rule everywhere: a late (or early) tick is never used
        elif not (t_from_ms <= tick.source_ms <= t_to_ms):
            counts["n_rows_outside_span"] += 1
        else:
            kept.append(tick)
            counts["n_avg_unusable"] += tick.avg_60s_unusable or tick.last15_unusable
    if in_span is not None:
        in_span.extend(kept)
    kept.sort(key=_row_order)                     # a total order: the input's row order never decides anything
    groups: list[list[_Tick]] = []
    for tick in kept:
        if groups and groups[-1][0].source_ms == tick.source_ms:
            groups[-1].append(tick)
        else:
            groups.append([tick])
    out: list[_Tick] = []
    for group in groups:
        first = group[0]
        if all(t.value == first.value for t in group[1:]):    # one tick: the first received is kept
            same = [(t.avg_60s, t.last_15min_avg) == (first.avg_60s, first.last_15min_avg) for t in group[1:]]
            counts["n_duplicates_dropped"] += sum(same)          # per row, against the kept row: fully equal
            counts["n_dup_diff_meta"] += len(same) - sum(same)   # same price, another average: still one tick
            if meta_groups is not None and not all(same):
                meta_groups.append(group)
            out.append(first)
        else:
            counts["n_conflicting_source_times"] += 1          # two different ticks at one source time: use neither
            counts["n_conflicting_rows"] += len(group)
            if conflicts is not None:
                conflicts.extend(group)
    counts["n_ticks_kept"] = len(out)
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


def _load_line(raw: bytes) -> Any:
    try:
        return _json(raw.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError, ArithmeticError):
        return None


def _rows_backwards(path: Path, stop_before_ms: int) -> tuple[list, bool]:
    """The file's parseable rows, newest line first, read from the end in blocks of READ_BLOCK_BYTES.

    It stops after a block whose rows that carry a whole `received_at_ms` are all received before stop_before_ms
    (the file is append-only, in write order, which tracks receipt to within seconds). Returns (rows, stopped).
    A line split across two blocks counts in the block where it completes.
    """
    rows: list = []
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        tail = b""            # the start of a line whose end has been read already
        oversized = False     # the line being assembled is longer than MAX_LINE_BYTES: drop it
        while pos > 0:
            size = min(READ_BLOCK_BYTES, pos)
            pos -= size
            fh.seek(pos)
            chunk = fh.read(size)
            parts = chunk.split(b"\n")
            if oversized:
                if len(parts) == 1:
                    continue              # still inside the oversized line
                joined, oversized = parts[:-1], False    # the last part is the oversized line's start: dropped
            else:
                joined = parts[:-1] + [parts[-1] + tail]   # the last part completes the line carried from after
            if pos > 0:
                tail, lines = joined[0], joined[1:]
                if len(tail) > MAX_LINE_BYTES:
                    tail, oversized = b"", True
            else:
                tail, lines = b"", joined
            timed = old = 0
            for raw in reversed(lines):
                if not raw.strip() or len(raw) > MAX_LINE_BYTES:
                    continue
                row = _load_line(raw)
                if row is None:
                    continue
                rows.append(row)
                recv = _whole(row.get("received_at_ms")) if isinstance(row, dict) else None
                if recv is not None:
                    timed += 1
                    old += recv < stop_before_ms
            if timed and old == timed:
                return rows, True
    return rows, False


def _other_feed(row: dict) -> bool:
    """Another feed's row in the same files: a string index_id other than BRTI, or a string channel other than ours."""
    index_id, channel = row.get("index_id"), row.get("channel")
    return (isinstance(index_id, str) and index_id != INDEX_ID) or (isinstance(channel, str) and channel != INDEX_CHANNEL)


def read_tick_files(tick_folder, t_from_ms: int, t_to_ms: int, *, clock: Callable[[], datetime]) -> list[dict]:
    """The tick-store rows of the span, in file order: (a) every row that parses to a tick with source time in
    [t_from_ms, t_to_ms]; and (b) every JSON-object row that does not parse to a tick but whose `received_at_ms` is a
    whole number in [t_from_ms - EARLY_MAX_MS, t_to_ms + LATE_MAX_MS], handed over so the logger counts it as
    malformed (`n_rows_malformed`).

    Why that receipt range for (b): a valid tick of the span is received at most EARLY_MAX_MS before and at most
    LATE_MAX_MS after a source time in the span, so a row received in that range is one that could have been a tick of this span; a row received outside it
    could not, so it is not this span's to count. Skipped, in no count: a line with no readable `received_at_ms` (a
    partial last line, not JSON, not an object, oversized), which cannot be placed in time; a tick whose source
    time is outside the span (it belongs to another window), so `n_rows_outside_span` is 0 through this reader; and
    another feed's row (the client can write `cfbenchmarks_value_5hz` or other index ids into the same files): an
    `index_id` that is a string other than BRTI, or a `channel` that is a string other than `cfbenchmarks_value`.
    A row whose index_id or channel is missing or not a string is malformed, and handed over by the receipt rule.

    The files are `<tick_folder>/kalshi_index_ticks-YYYY-MM-DD.jsonl`, one per UTC day of receipt; a missing file is
    no rows. Only the span is read: each file is read backwards from its end, newest day first (from the UTC day of
    t_to_ms + LATE_MAX_MS + RECEIPT_MARGIN_MS), and reading stops at the first block whose rows were all received
    before t_from_ms - LATE_MAX_MS - RECEIPT_MARGIN_MS, so the day before is opened only when the span reaches into
    it. Files only.

    Why this returns every valid tick a full scan would: a valid tick (received at most EARLY_MAX_MS before and at
    most LATE_MAX_MS after its source time) with source time in the span was received in [t_from_ms - EARLY_MAX_MS,
    t_to_ms + LATE_MAX_MS], so it is filed on a day the read covers. The day coverage and the stop rule still allow
    LATE_MAX_MS before the span, wider than EARLY_MAX_MS needs, so they read a little more than needed and miss
    nothing; they are kept as they were. If writes are out of receipt order by at most RECEIPT_MARGIN_MS, every row
    written before the stopping block was received before t_from_ms - LATE_MAX_MS, so it is not such a tick. A late
    tick is returned when the read reaches it (always, if it is late by no more than LATE_MAX_MS +
    RECEIPT_MARGIN_MS), and the logger counts it and excludes it.
    """
    _gate(clock)
    _check_folder(tick_folder)
    a, b = _check_span(t_from_ms, t_to_ms)
    stop_before = a - LATE_MAX_MS - RECEIPT_MARGIN_MS
    day = datetime.fromtimestamp((b + LATE_MAX_MS + RECEIPT_MARGIN_MS) // 1000, timezone.utc).date()
    first = datetime.fromtimestamp(stop_before // 1000, timezone.utc).date()
    per_day: list[list] = []                       # newest day first, each day's rows in file order
    while day >= first:
        path = Path(tick_folder) / f"{TICK_FILE_PREFIX}{day.isoformat()}.jsonl"
        if path.is_file():
            rows, stopped = _rows_backwards(path, stop_before)
            rows.reverse()
            per_day.append(rows)
            if stopped:
                break
        day -= timedelta(days=1)
    out: list[dict] = []
    for rows in reversed(per_day):
        for row in rows:
            tick = _parse_tick_row(row)
            if tick is not None:
                if a <= tick.source_ms <= b:
                    out.append(row)                # (a) a tick of the span
            elif isinstance(row, dict) and not _other_feed(row):
                recv = _whole(row.get("received_at_ms"))
                if recv is not None and a - EARLY_MAX_MS <= recv <= b + LATE_MAX_MS:
                    out.append(row)                # (b) a row that could have been a tick of the span: malformed
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
    if isinstance(x, _BadNumber):
        return None                                 # never written
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


WINDOW_TS_MAX_S = 4_102_444_800   # 2100-01-01 00:00 UTC: a window time above this is refused
_TS_CONTEXT = Context(prec=40, rounding=ROUND_HALF_EVEN)   # local: the caller's decimal context cannot change a check


def _window_ms(ts, name: str) -> int:
    """A window time as exact integer ms, or ValueError.

    One rule: type exactly int or float (no bool, subclass, Decimal, numpy scalar or str); finite;
    0 <= ts <= WINDOW_TS_MAX_S; a whole number of milliseconds, tested exactly in a local decimal context
    (Decimal(repr(ts)) for a float, Decimal(ts) for an int). The ms value comes from that same Decimal.
    """
    if type(ts) not in (int, float):
        raise ValueError(f"a window's {name} must be an int or a float, got {type(ts).__name__}")
    # an int is range-tested as an int, never converted to float first (10**400 is a ValueError, not an overflow)
    if (type(ts) is float and not math.isfinite(ts)) or not (0 <= ts <= WINDOW_TS_MAX_S):
        raise ValueError(f"a window's {name} must be finite and in [0, {WINDOW_TS_MAX_S}], got {ts!r}")
    with localcontext(_TS_CONTEXT):
        ms = (Decimal(repr(ts)) if type(ts) is float else Decimal(ts)) * 1000
        if ms != ms.to_integral_value():
            raise ValueError(f"a window's {name} must be a whole number of milliseconds, got {ts!r}")
        return int(ms)


def _exact_s(x: Any) -> Decimal | None:
    """A row time in seconds as an exact Decimal by the window-time rule (type exactly int or float, finite,
    0 <= x <= WINDOW_TS_MAX_S; Decimal(repr(x)) for a float, Decimal(x) for an int), else None: a malformed time."""
    if type(x) not in (int, float):
        return None
    if (type(x) is float and not math.isfinite(x)) or not (0 <= x <= WINDOW_TS_MAX_S):
        return None
    with localcontext(_TS_CONTEXT):
        return Decimal(repr(x)) if type(x) is float else Decimal(x)


def _check_window(window: Window) -> tuple[int, int]:
    """Check the window's times; return (open_ms, close_ms), exact."""
    open_ms = _window_ms(window.open_ts, "open_ts")
    close_ms = _window_ms(window.close_ts, "close_ts")
    if close_ms <= open_ms:
        raise ValueError("a window must open before it closes")
    return open_ms, close_ms


_CLOCK_FAULTS = (NaiveClock, NotYetEnabled)   # raised by a reader that reads the clock: never turned into a record


def _read_rows(read_ticks: Callable, window: Window, t_from_ms: int, t_to_ms: int) -> tuple[list, str | None]:
    try:
        return list(read_ticks(window, t_from_ms, t_to_ms) or ()), None
    except _CLOCK_FAULTS:                         # a clock refused inside the reader is a refusal, not a reader error
        raise
    except Exception as exc:                      # a failed reader is a skipped window, never a lost one
        return [], f"{type(exc).__name__}: {exc}"


# ------------------------------------------------------------------ the status at t*

def _series_checks(ticks: list[_Tick], t_star_ms: int) -> dict:
    """The warm-up and gap facts of the series span (counts and spacing only: no measure of the price)."""
    anchor = t_star_ms - ANCHOR_BACK_MS
    nearest = min(ticks, key=lambda t: abs(t.source_ms - anchor), default=None)
    gaps = [b.source_ms - a.source_ms for a, b in zip(ticks, ticks[1:])]
    max_gap = max(gaps) if gaps else None
    # the span's start and end count as gaps too (the lead's reading, 2026-11-26)
    first_gap = ticks[0].source_ms - (t_star_ms - SERIES_SPAN_MS) if ticks else None
    last_gap = t_star_ms - ticks[-1].source_ms if ticks else None
    count_ok = len(ticks) >= MIN_TICKS
    anchor_ok = nearest is not None and abs(nearest.source_ms - anchor) <= ANCHOR_TOL_MS
    gap_ok = (bool(ticks) and (max_gap is None or max_gap <= MAX_GAP_MS)
              and first_gap <= MAX_GAP_MS and last_gap <= MAX_GAP_MS)
    return {"n_ticks": len(ticks), "min_ticks": MIN_TICKS, "count_ok": count_ok,
            "anchor_ts_ms": anchor, "anchor_tick_ms": nearest.source_ms if nearest else None,
            "anchor_tol_ms": ANCHOR_TOL_MS, "anchor_ok": anchor_ok,
            "max_gap_ms": max_gap, "first_gap_ms": first_gap, "last_gap_ms": last_gap,
            "max_gap_allowed_ms": MAX_GAP_MS, "gap_ok": gap_ok,
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
    It runs live, before the close, and records what was known then: ticks not yet received at the call are not in
    the record, by design (no look-ahead).
    Order: date check first (whatever the other arguments), then argument checks, then the readers, then the
    record. A window already closed, or closed while being read, is refused and writes nothing.
    """
    entered = _gate(clock)
    _check_folder(folder)
    if not callable(read_ticks) or not callable(read_book):
        raise ValueError("read_ticks and read_book must be callables")
    open_ms, close_ms = _check_window(window)
    if _clock_us(entered) >= close_ms * 1000:
        raise LateWindow(f"{window.window_ticker} is already closed")
    t_star_ms = open_ms + T_STAR_OFFSET_MS         # exact ints from the window's exact times
    t_star = _ms_to_s(t_star_ms)
    span_from_ms = t_star_ms - SERIES_SPAN_MS

    rows, tick_error = _read_rows(read_ticks, window, span_from_ms, t_star_ms)
    book_error = None
    try:
        raw_book = read_book(window.window_ticker)
        book = None if raw_book is None else _book_record(raw_book)
    except _CLOCK_FAULTS:
        raise
    except Exception as exc:
        book, book_error = None, f"{type(exc).__name__}: {exc}"

    written = _gate(clock)                         # one reading: gated on, checked against the close, and stamped
    written_at = datetime.timestamp(written)
    if _clock_us(written) >= close_ms * 1000:
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

    Q = close_ms. The span read is [Q - 60 s, Q + 10 s] by source time; ticks are deduped by `_ticks_in`.
    - An exact candidate: a valid tick whose `last_60s_windowed_average_15min` is present with window_size 60 and
      whose source time is in [Q, Q + 1 s). The average's window bounds fields are logged as received and are NOT
      used to select (Kalshi's own example contradicts its formula for them). Exact candidates whose Decimal values
      differ give None ("close_candidates_disagree"); otherwise the first by source time is the close tick.
    - Only when there is no exact candidate, the fallback: the tick with the latest source time in [Q - 2 s, Q + 1 s)
      whose average is present with 55 <= window_size <= 59 (`close_quality` "partial_<k>").
    - One pool for the candidates and all four close counts (n_close_candidates, n_close_partial_candidates,
      n_close_running, n_close_wrong_window): the distinct valid rows of each kept source time (the kept row and
      its same-price twins; identical rows counted once) that carry a usable last_15min_avg. A row whose average
      was made unusable counts only in n_close_avg_unusable (distinct rows too). Rows are ordered by `_row_order`,
      a total order, so no count and no choice depends on the order the reader returned the rows in.
    - Fail closed, within the tier used only (exact if any exact-tier row exists, kept, a same-price twin or in a
      source time dropped as conflicting; else the fallback), comparing Decimal(last_15min_avg.value) only (bounds
      are logged, never used): exact-tier rows with more than one value, or no kept exact tick, give None
      ("close_candidates_disagree"); in the fallback tier, the latest fallback source time must be the kept tick's
      and its rows must carry one value. The other tier's disagreements are counted, never used.
    - Counted: `n_close_wrong_window` = window_size-60 averages whose source time is outside [Q, Q + 1 s);
      `n_close_running` = averages with window_size below 60 (the running last minute); `n_close_conflicting_candidates`
      and `n_close_dup_diff_meta_candidates` count candidate rows in dropped and in value-differing same-price
      groups, of either tier. A window_size above 60 is in neither count, and is never used.
      `n_close_avg_unusable` counts the valid rows in the span whose last_15min_avg failed the price bound and was
      set to None, so a missing close tick is visible.
    The estimate is `settlement_estimate` (2 dp, ROUND_HALF_EVEN) with `settlement_estimate_half_even` (the same) and
    `settlement_estimate_half_up` (2 dp, ROUND_HALF_UP) beside it, and `settlement_value_raw` (the Decimal as
    received); Step 0's read of the market rules decides which rounding applies. `local_brti_mean` is the exact mean
    of brti_raw over the ticks with source time in (Q - 60 s, Q], rounded half-even to 8 dp, with `local_brti_n`.
    Refused (CloseNotReached) before close + CLOSE_WAIT_MS (190 s), compared exactly on the aware clock reading (no
    rounding). It is an estimate only: the authoritative outcome is Kalshi's settled market, read later, offline.
    """
    entered = _gate(clock)
    _check_folder(folder)
    if not callable(read_ticks):
        raise ValueError("read_ticks must be a callable")
    _, close_ms = _check_window(window)            # exact, from the same Decimal the check used
    # exact integer microseconds: the base class's arithmetic, with the clock's utcoffset trusted
    if _clock_us(entered) < (close_ms + CLOSE_WAIT_MS) * 1000:
        raise CloseNotReached(f"{window.window_ticker}: the close record waits until close + {CLOSE_WAIT_MS} ms")
    t_from, t_to = close_ms - CLOSE_BEFORE_MS, close_ms + CLOSE_AFTER_MS
    rows, tick_error = _read_rows(read_ticks, window, t_from, t_to)
    written_at = datetime.timestamp(_gate(clock))
    conflicting: list[_Tick] = []
    meta_groups: list[list[_Tick]] = []
    valid_rows: list[_Tick] = []
    ticks, counts = _ticks_in(rows, t_from, t_to, conflicting, meta_groups, valid_rows)
    Q = close_ms

    def size(t: _Tick) -> int | None:
        return None if t.last_15min_avg is None else t.last_15min_avg["window_size"]

    def is_exact(t: _Tick) -> bool:
        return size(t) == CLOSE_WINDOW_SIZE and Q <= t.source_ms < Q + CLOSE_EXACT_MS

    def is_partial(t: _Tick) -> bool:
        k = size(t)
        return (k is not None and CLOSE_PARTIAL_MIN_SIZE <= k < CLOSE_WINDOW_SIZE
                and Q - CLOSE_PARTIAL_BACK_MS <= t.source_ms < Q + CLOSE_EXACT_MS)

    def eligible(t: _Tick) -> bool:
        return is_exact(t) or is_partial(t)

    twins = [t for g in meta_groups for t in g[1:]]          # rows beyond the kept first of each same-price group
    # The candidate pool: every valid row of a kept source time (the kept row and its same-price twins), in
    # (source time, received_at, seq) order; a row without a usable last_15min_avg (absent, or made unusable) is
    # in no tier, so it takes no part in candidates or disagreement whichever order the twins arrived in.
    # One pool for every close count: the distinct rows (identical rows counted once) with a usable average.
    distinct: dict[tuple, _Tick] = {}
    for t in sorted(ticks + twins, key=_row_order):
        if t.last_15min_avg is not None:
            distinct.setdefault(_same_tick(t), t)
    pool = sorted(distinct.values(), key=_row_order)
    exact = [t for t in pool if is_exact(t)]
    partial = [t for t in pool if is_partial(t)]
    n_wrong = sum(1 for t in pool if size(t) == CLOSE_WINDOW_SIZE and not is_exact(t))
    n_running = sum(1 for t in pool if size(t) < CLOSE_WINDOW_SIZE)
    n_conflicting_candidates = sum(1 for t in conflicting if eligible(t))

    def value(t: _Tick) -> Decimal | None:
        return None if t.last_15min_avg is None else t.last_15min_avg["value"]

    # same-price twins whose USABLE averages differ in VALUE (bounds-only differences are not a disagreement)
    meta_disagree = [g for g in meta_groups if any(eligible(t) for t in g)
                     and len({value(t) for t in g if t.last_15min_avg is not None}) > 1]
    # The tier used: exact if a row of the exact tier exists anywhere (in the pool, or in a source time dropped as
    # conflicting); else the fallback. Only the tier used can make the estimate None.
    exact_rows = exact + [t for t in conflicting if is_exact(t)]
    if exact_rows:
        close_tick, quality, tier_rows = (exact[0] if exact else None), "exact_60", exact_rows
        # one value over every exact-tier row (Decimal equality: 100.1 == 100.10), and one in the pool
        disagree = len({value(t) for t in exact_rows}) > 1 or not exact
    else:
        tier_rows = partial + [t for t in conflicting if is_partial(t)]
        latest = max((t.source_ms for t in tier_rows), default=None)
        close_tick = next((t for t in partial if t.source_ms == latest), None)   # the first row at the latest time
        quality = None if close_tick is None else f"partial_{size(close_tick)}"
        # the fallback's source time must be the tier's latest, in the pool, with one value over its rows
        disagree = bool(tier_rows) and (close_tick is None
                                        or len({value(t) for t in tier_rows if t.source_ms == latest}) > 1)
    agree = not disagree
    avg = close_tick.last_15min_avg if close_tick is not None else None
    raw = half_even = half_up = None
    if disagree:
        reason = "close_candidates_disagree"       # within the tier used: differing values, or a dropped candidate
    elif close_tick is None:
        reason = "tick_reader_error" if tick_error else "no_close_tick"
    else:
        raw, reason = avg["value"], None
        try:
            with localcontext(_SETTLE_CONTEXT):
                half_even = raw.quantize(SETTLE_QUANTUM, rounding=SETTLE_ROUNDING)
                half_up = raw.quantize(SETTLE_QUANTUM, rounding=ROUND_HALF_UP)
        except InvalidOperation:                   # more digits than the local context holds: no estimate
            raw, half_even, half_up, reason = None, None, None, "close_value_out_of_range"
    if half_even is None:
        quality = None
    mean_ticks = [t for t in ticks if Q - LOCAL_MEAN_SPAN_MS < t.source_ms <= Q]
    record = {
        "kind": "close", "schema_version": SCHEMA_VERSION, "window_ticker": window.window_ticker,
        "close_ts": window.close_ts, "close_ms": close_ms, "written_at": written_at,
        "index_id": INDEX_ID, "channel": INDEX_CHANNEL, "span_from_ms": t_from, "span_to_ms": t_to,
        "settlement_estimate": half_even, "settlement_estimate_reason": reason,
        "rounding": "2 dp, ROUND_HALF_EVEN",
        "settlement_value_raw": raw, "settlement_estimate_half_even": half_even,
        "settlement_estimate_half_up": half_up, "close_quality": quality,
        "local_brti_mean": _exact_mean(t.value for t in mean_ticks), "local_brti_n": len(mean_ticks),
        "close_tick": None if close_tick is None else {
            "cf_source_ts_ms": close_tick.source_ms, "received_at": close_tick.received_at_ms,
            "sending_ts_ms": close_tick.sending_ts_ms, "seq": close_tick.seq,
            "local_receipt_ms": close_tick.local_receipt_ms, "last_60s_windowed_average_15min": avg},
        "n_close_candidates": len(exact), "n_close_partial_candidates": len(partial),
        "n_close_wrong_window": n_wrong, "n_close_running": n_running,
        # rows whose last_15min_avg failed the price bound and was set to None: no candidate in either tier
        "n_close_avg_unusable": len({_same_tick(t) for t in valid_rows if t.last15_unusable}),   # distinct rows
        "n_close_conflicting_candidates": n_conflicting_candidates,   # rows, not source times
        "n_close_dup_diff_meta_candidates": sum(len(g) for g in meta_disagree),   # rows, in same-value groups
        "close_candidates_agree": agree,
        "outcome_source": "Kalshi settled market (result, expiration_value), read later, offline",
        **counts, "tick_error": tick_error,
    }
    _append(folder, record)
    return _jsonable(record)


def _exact_mean(values: Iterable[Decimal]) -> str | None:
    """The exact mean of the Decimals, rounded half-even to LOCAL_MEAN_DP places, as a string (None if empty).

    Integer arithmetic on the Decimals' own digits: no decimal context is involved, so the caller's cannot change it.
    """
    terms = [v.as_tuple() for v in values]
    if not terms:
        return None
    low = min(min(t.exponent for t in terms), -LOCAL_MEAN_DP)
    total = 0
    for sign, digits, exponent in terms:
        n = int("".join(map(str, digits)) or "0") * 10 ** (exponent - low)
        total += -n if sign else n
    num, den = total, len(terms) * 10 ** (-LOCAL_MEAN_DP - low)    # mean * 10**LOCAL_MEAN_DP == num / den
    q, r = divmod(num, den)
    if 2 * r > den or (2 * r == den and q % 2):
        q += 1
    return format(Decimal(f"{q}E-{LOCAL_MEAN_DP}"), "f")


# ------------------------------------------------------------------ the tape (only for a window the caller says posted)

def log_tape(folder, window: Window, *, posted: bool, read_tape: Callable, clock: Callable[[], datetime],
             budget: TapeBudget) -> dict | None:
    """Record the public tape rows over [t*, t* + 60 s] for a window the caller says posted an order.

    Not posted: no read, no budget spent, nothing written (returns None). Posted: one budgeted read, and a
    refusal once the budget is used up. The logger does not decide whether an order was posted.
    Counts: every row the reader returns is counted in exactly one field, so the record reconciles from its own fields:
    n_tape_rows_read == n_rows (kept, == len(rows)) + n_tape_rows_outside_span + n_tape_rows_malformed (not a dict,
    or a created_ts that is not an exact-type int or float, finite and in range). A failed read is 0 rows read.
    """
    entered = _gate(clock)
    _check_folder(folder)
    if not posted:
        return None
    if not callable(read_tape):
        raise ValueError("read_tape must be a callable")
    open_ms, close_ms = _check_window(window)
    if _clock_us(entered) >= close_ms * 1000:
        raise LateWindow(f"{window.window_ticker} is already closed")
    budget.take()
    t_star_ms = open_ms + T_STAR_OFFSET_MS
    t_from, t_to = _ms_to_s(t_star_ms), _ms_to_s(t_star_ms + TAPE_SPAN_MS)   # exact: the filter below compares these
    tape_error = None
    try:
        raw = list(read_tape(window, float(t_from), float(t_to)) or ())       # the reader is asked in float seconds
    except _CLOCK_FAULTS:
        raise
    except Exception as exc:
        raw, tape_error = [], f"{type(exc).__name__}: {exc}"
    written = _gate(clock)                         # one reading: gated on, checked against the close, and stamped
    written_at = datetime.timestamp(written)
    if _clock_us(written) >= close_ms * 1000:
        raise LateWindow(f"{window.window_ticker} closed before its tape could be written")
    rows, n_malformed, n_outside = [], 0, 0
    with localcontext(_TS_CONTEXT):                # Decimal against Decimal only, in the module's own context
        for r in raw:
            ts = _exact_s(r.get("created_ts")) if isinstance(r, dict) else None
            if ts is None:
                n_malformed += 1                   # a malformed row or row time is skipped and counted, never raised on
            elif t_from <= ts <= t_to:
                rows.append(r)
            else:
                n_outside += 1
    record = {"kind": "tape", "schema_version": SCHEMA_VERSION, "window_ticker": window.window_ticker,
              "t_from": t_from, "t_to": t_to, "written_at": written_at, "n_rows": len(rows), "rows": rows,
              "n_tape_rows_read": len(raw), "n_tape_rows_outside_span": n_outside,
              "n_tape_rows_malformed": n_malformed, "tape_error": tape_error}
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
