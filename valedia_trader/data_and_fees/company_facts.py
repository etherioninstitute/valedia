"""Company facts for stocks from Finnhub's free plan, kept point-in-time so a later test can see only what had been fetched.

The two rules of the source, so that nobody builds past them:

1. The free plan is for personal use. Nothing here is a licence to redistribute, resell or publish what it answers.
2. Nothing read from this store, and nothing derived from it, may be sent to an outside model or any other outside
   service: local models only.

What the plan answers (measured 2026-11-14): `profile`, `metrics`, `earnings` (four quarters), `recommendations`
(four months), `insiders` and `news` for a stock, and one market-wide `earnings_calendar`. For a fund it answers almost
nothing, and daily candles, dividends, splits, fund profiles and fund holdings are HTTP 403 on this plan: they are not asked
for. Prices come from elsewhere; this module is about facts. The answers are today's snapshot, so history exists only from
the first run on, and every row says when it was fetched.

What each kind must answer (one top-level JSON type each; anything else is refused and counted failed):
  profile: object    metrics: object    earnings: array    recommendations: array    insiders: object
  news: array        earnings_calendar: object

The store (SQLite, WAL, one writer at a time):
  observations(symbol, kind, fetched_at, body_sha256, body)  a row only when the answer, as canonical JSON (sorted keys, no
                                                             spaces), differs from the newest stored one for that symbol and
                                                             kind; the market calendar is under the symbol `*`
  news(symbol, article_id, published_at, fetched_at, ...)    one row per article, written when first seen, never changed
  runs(started_at, ended_at, calls, stored, unchanged, failed, kinds_refused)

A store is created only where no file exists, and opened only when it is this version's store: this store's own mark (SQLite
application_id APPLICATION_ID, user_version USER_VERSION), exactly the three tables above with exactly their columns, indexes
and nothing else (no other table, view or trigger), and a store that is whole (below). Anything else (another program's
database, an empty SQLite file, a zero-byte file, a file that is not SQLite, a store of another version, a symbolic link, a
store with an extra table, a cut store) is refused before SQLite is ever asked to open it, by the collector, a reader,
`status` or `show`, and is left exactly as found, side files included: the checks read the file's bytes and those of its
`-wal` without SQLite, so not a byte, a modification time, a `-wal`, `-shm` or lock file changes. Such a refusal says
"nothing was done"; a refusal that comes after SQLite opened the store says instead that SQLite could not use it and that
SQLite's own recovery may have changed its side files (`_refusal` is the one place that chooses those words).

Whole, decided from bytes alone the way SQLite's own recovery reads a write-ahead file: the `-wal` counts only when its magic
is one of the two WAL magics, its format is 3007000, its page size is the store's and its header checksum holds; a frame is
valid only when its salts are the header's, its page number is not zero and the running checksum (from the header's, over
the frame header's first 8 bytes and its page, word order given by the magic) equals its own; the first invalid frame ends
the walk, and only frames up to the last valid commit frame are counted (none when there is no valid commit frame: the
`-wal` then proves nothing). With F the whole pages of the main file, H the page count in its own header and M the database
size of the last counted commit, the store is whole when every page p with F < p <= max(H, M) is in a counted frame (with a
`-wal` that proves nothing: when F >= H). So a main file left short by a kill in the middle of a checkpoint, whose missing
pages are all in counted frames, is opened, and SQLite completes it; a cut main file beside a `-wal` of another database, a
stale one, one that does not hold every missing page, or one whose checksums fail is refused. The table check reads page 1
and every counted copy of page 1 in the `-wal`. The cost: a read beside a large `-wal` checksums it (about 0.09 s for 1,200
frames), and a `-wal` exists only while a collector runs or after one was killed. A store file, `-wal` or `-shm` that cannot
be written is refused before SQLite opens it; should SQLite still find it cannot write, that is refused in one line too.

Limits, stated and not guarded: a name another program places at `<temporary name>-journal` or `-wal` while a store is
being made is removed by SQLite (the name only; no byte of another file changes). A program that swaps the temporary name
away and back between two of the creation checks can have its own database take this store's tables (it must win a race a
watcher lost 160 times of 160). A foreign `-wal` beside a WHOLE store is SQLite's to interpret: the module refuses it only
when its counted frames copy page 1 with other tables, or when its last commit says the store is longer than the main
file and its frames lack a missing page; a foreign `-wal` whose counted frames fit inside the main file is not detected,
and SQLite applies it.

A new store is built in a file this module creates itself, beside the store, with O_CREAT | O_EXCL | O_NOFOLLOW under a name
no one can predict (`<store>.new-<32 random hex digits>`); SQLite then opens that file (its device and inode are compared
before and after, and the finished file is checked to hold the store), and it is linked into place with a hard link, which
fails if anything is at the final path (where hard links are impossible it is renamed, only if nothing is there). A name left
by a run that was killed while it made a store is removed at a later run's start only when it is a hard link to the live
store; any other leftover is never opened and never deleted, and `status` lists every leftover by name.

Readers read in SQLite's ordinary read-only mode (`mode=ro`) and touch no lock file. In a writable folder SQLite may create
its own `-wal` and `-shm` side files beside the store as it reads; that is accepted. A store in a place that cannot hold side
files (a read-only folder or volume) is read in SQLite's `immutable` mode, which writes and creates nothing, and only when no
`-wal` with content is beside it (the answer is checked not to have changed while it was read: a read that overlapped a
write is refused as that, "ask again"); a read-only place with a `-wal` that has content is refused in one line that says so
and to copy the store with its side files to a writable place. Each call of `status` and of `show` (one kind or all) reads on
one connection inside one read transaction, so every count, the newest fetch and every row shown belong to one commit.

No look-ahead: `as_of(db, symbol, kind, when)` returns the newest observation fetched at or before `when`;
`news_as_of(db, symbol, when, since=None)` returns only articles published AND fetched at or before `when`. The reader never
looks at anything fetched later, whatever the store holds, and a stored row whose text no longer matches its hash is refused.
A stored fetch time that is not a finite number from 2000-01-01 to 2100-01-01 is impossible: it is never returned by a
reader and is ignored by every rule about the newest fetch (the clock-behind rule, the unchanged rule, the newest and oldest
fetch of `status`). `status` and `show` never end in a traceback: a row they cannot show is skipped and counted (`show`
prints what it can and says on stderr how many stored articles it skipped).

The service's clock guards the stamps: the transport returns the answer's headers with its body, and an answer whose `Date`
differs from this computer's clock by more than 24 hours when it arrives stops the run before anything of it is stored (the
one line gives both times and the difference). An answer with no readable `Date` is stored as before and counted. A run
whose clock reads earlier than the newest plausible fetch in the store, or outside 2000 to 2100, is refused (for the first:
runs resume by themselves once the clock passes that fetch, and the one line says so and what can be done sooner).

Politeness and safety: at most 55 requests in any 60 seconds (the plan allows 60); a 429 waits the Retry-After (30 seconds if
none, at most 120) and tries again up to 3 times; a 403 marks that kind "not on this plan" for the rest of the run; a 5xx or a
timeout gets two tries; `max_calls` ends a run cleanly. Only https://finnhub.io/api/v1/ is requested, no redirect is followed,
an answer over 5 MB, shorter than its declared length, not JSON, of the wrong top-level type, or with a repeated key is refused.
Pacing is measured on a monotonic clock, so a correction of the wall clock cannot let a burst through; the wall clock is used
only for the time stamps and the clock-behind rule. The pacer gives up, ending the run cleanly, when its clock has not
advanced across 1,000 consecutive waits. An article is stored only if its time is a finite number from 2000-01-01 to two days
after the fetch and its id is a string of 1 to 128 characters or an integer of magnitude below 2**63; any other article is
skipped and counted. Nothing in an answer or in the store can stop a run:
an error while handling one symbol and kind is counted failed (with a short fixed reason) and the run goes on. The key is read through `credentials("FINNHUB_API_KEY")`
(`valedia_trader.core.secrets.secret` by default) and travels only in the `X-Finnhub-Token` header: it is never in a URL, an
error text, a message, a result or the database, and the client name is the generic constant `USER_AGENT`. An answer that
contains the key (plain, as JSON-escaped text, as \\u escapes, as a member name, or base64 or hex encoded, at any alignment) or,
for a key of at least 24 characters, a half of it, is refused. What remains unguarded: a short key's partial echo, the key
split over several strings, and other transformations (reversed, percent-encoded, base32, hashed); a key shorter than about
10 characters has no base64 guard.

This module is not on the order path and nothing on the order path may import it.

    python -m valedia_trader.data_and_fees.company_facts collect [--symbols A,B,C | --universe stocks] [--db PATH] [--max-calls N]
    python -m valedia_trader.data_and_fees.company_facts status [--db PATH]
    python -m valedia_trader.data_and_fees.company_facts show SYMBOL [--kind K] [--as-of ISO-UTC] [--db PATH]

Exit status 0 when the run ended; 2 when refused (no key, a bad symbol, the store held by another run), with one line that
says what to do.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import email.utils
import errno
import fcntl
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

__all__ = ["APPLICATION_ID", "USER_VERSION", "INTERRUPTED", "EXPECTED_TYPES", "BASE_URL", "USER_AGENT", "KINDS", "EARNINGS_CALENDAR", "HttpError", "Refused", "Result", "collect", "as_of",
           "news_as_of", "status", "universe_symbols", "valid_symbol", "http_fetch", "canonical_json", "body_sha256",
           "create_schema", "main", "DEFAULT_DB", "UNIVERSES_PATH", "DEFAULT_MAX_CALLS", "MAX_BODY_BYTES"]

BASE_URL = "https://finnhub.io/api/v1/"
USER_AGENT = "market-data-reader/1.0"
KEY_NAME = "FINNHUB_API_KEY"
KEY_HEADER = "X-Finnhub-Token"
KINDS = ("profile", "metrics", "earnings", "recommendations", "insiders", "news")
EARNINGS_CALENDAR = "earnings_calendar"
STORED_KINDS = ("profile", "metrics", "earnings", "recommendations", "insiders", EARNINGS_CALENDAR)       # kept in observations
_REQUESTS = {                                   # kind -> path and fixed query (the symbol and the news window are added)
    "profile": ("stock/profile2", {}),
    "metrics": ("stock/metric", {"metric": "all"}),
    "earnings": ("stock/earnings", {}),
    "recommendations": ("stock/recommendation", {}),
    "insiders": ("stock/insider-transactions", {}),
}
EXPECTED_TYPES = {"profile": dict, "metrics": dict, "earnings": list, "recommendations": list, "insiders": dict,
                  "news": list, EARNINGS_CALENDAR: dict}
CALENDAR_PATH = "calendar/earnings"
CALENDAR_DAYS = 90
NEWS_FIRST_DAYS = 30
NEWS_OVERLAP_DAYS = 2

MIN_ARTICLE_TIME = 946684800.0                   # 2000-01-01
ARTICLE_FUTURE_SLACK = 2 * 86400.0               # an article may be dated at most this long after the fetch
MIN_FETCH_TIME, MAX_FETCH_TIME = 946684800.0, 4102444800.0      # 2000-01-01 and 2100-01-01: a fetch time outside is impossible
DATE_TOLERANCE = 24 * 3600.0                     # the service's Date may differ from this computer's clock by this much
CLOCK_STOP = "the service dates its answer"      # how a run stopped by the Date guard says why (exit status 2)
MAX_ARTICLE_ID_CHARS = 128
ARTICLE_ID_INT_LIMIT = 2 ** 63                   # an integer id must be of magnitude below this
HALF_KEY_MIN_CHARS = 12
APPLICATION_ID = 1128549169                      # 0x43464B31, "CFK1": this store's mark in the SQLite header
USER_VERSION = 1
INTERRUPTED = "(interrupted)"                    # in runs.kinds_refused: a run that died without closing its row

MAX_REQUESTS_PER_WINDOW = 55
WINDOW_SECONDS = 60.0
WINDOW_MARGIN = 0.1                              # a request counts against the window slightly longer than 60 seconds
MAX_STALLED_WAITS = 1000                         # the pacer gives up after this many waits in a row with no clock advance
RETRY_AFTER_DEFAULT = 30.0
RETRY_AFTER_MAX = 120.0
MAX_429_RETRIES = 3
MAX_TRANSIENT_TRIES = 2
DEFAULT_MAX_CALLS = 1000
MAX_BODY_BYTES = 5 * 1024 * 1024
TIMEOUT_SECONDS = 20.0
DEADLINE_SECONDS = 60.0
KEY_MIN_CHARS, KEY_MAX_CHARS = 8, 256

_SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")
DEFAULT_DB = Path(__file__).resolve().parents[2] / "memory" / "company_facts.sqlite"
UNIVERSES_PATH = Path(__file__).resolve().parents[1] / "research_loop" / "universes.json"
LOCK_SUFFIX = ".lock"
TEMP_INFIX = ".new-"                             # a new store is built at `<store>.new-<32 random hex digits>`
_LEFTOVER = re.compile(r"\.new-(?:[0-9a-f]{32}|[0-9]+)(?:-wal|-shm|-journal)?")   # after the store's name (rev 2 used the pid)
_SQLITE_HEADER = b"SQLite format 3\x00"
_WAL_MAGIC = (0x377F0682, 0x377F0683)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_COLUMNS = {
    "observations": ["symbol", "kind", "fetched_at", "body_sha256", "body"],
    "news": ["symbol", "article_id", "published_at", "fetched_at", "headline", "source", "url", "summary"],
    "runs": ["started_at", "ended_at", "calls", "stored", "unchanged", "failed", "kinds_refused"],
}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations(
    symbol TEXT NOT NULL, kind TEXT NOT NULL, fetched_at REAL NOT NULL, body_sha256 TEXT NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS observations_by_time ON observations(symbol, kind, fetched_at);
CREATE TABLE IF NOT EXISTS news(
    symbol TEXT NOT NULL, article_id TEXT NOT NULL, published_at REAL NOT NULL, fetched_at REAL NOT NULL,
    headline TEXT, source TEXT, url TEXT, summary TEXT, PRIMARY KEY(symbol, article_id));
CREATE INDEX IF NOT EXISTS news_by_time ON news(symbol, published_at);
CREATE TABLE IF NOT EXISTS runs(
    started_at REAL NOT NULL, ended_at REAL, calls INTEGER NOT NULL, stored INTEGER NOT NULL,
    unchanged INTEGER NOT NULL, failed INTEGER NOT NULL, kinds_refused TEXT NOT NULL);
"""


def _plausible_sql(column: str) -> str:
    """SQL that is true when `column` holds a possible fetch time: a number from MIN_FETCH_TIME to MAX_FETCH_TIME."""
    return "(typeof({0}) IN ('integer','real') AND {0} BETWEEN {1!r} AND {2!r})".format(column, MIN_FETCH_TIME, MAX_FETCH_TIME)


def _schema_rows_of(conn: sqlite3.Connection) -> set:
    return set(conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"))


def _expected_schema() -> frozenset:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(_SCHEMA)
        return frozenset(_schema_rows_of(conn))
    finally:
        conn.close()


_EXPECTED_SCHEMA = _expected_schema()             # version 1 holds exactly these tables, indexes and nothing else


class HttpError(Exception):
    """A non-200 answer. The text is the status only: nothing the server sent and no URL."""
    def __init__(self, status: int, retry_after: Any = None):
        super().__init__("HTTP %s" % status)
        self.status, self.retry_after = status, retry_after


class Refused(Exception):
    """The command is refused (or the store is unusable). The text is one line a person can act on and holds no secret."""


class _Stop(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class Result:
    """What a run did. `failures` is (kind, short fixed reason) for each failed request; no server text, no URL."""
    started_at: float
    ended_at: float | None = None
    calls: int = 0
    stored: int = 0
    unchanged: int = 0
    failed: int = 0
    kinds_refused: tuple = ()
    stopped: str | None = None
    skipped: int = 0
    failures: list = field(default_factory=list)
    undated: int = 0                                  # answers that carried no readable Date header

    def summary(self) -> str:
        return ("company_facts: %d calls, %d stored, %d unchanged, %d failed; refused kinds: %s; skipped articles: %d; "
                "answers without a readable Date: %d; stopped: %s"
                % (self.calls, self.stored, self.unchanged, self.failed, ",".join(self.kinds_refused) or "none",
                   self.skipped, self.undated, _one_line(self.stopped) if self.stopped else "no"))


# ---- small helpers

def valid_symbol(symbol: Any) -> bool:
    """A capital letter, then up to nine capital letters, digits, dots or hyphens (the whole text, no trailing newline)."""
    return type(symbol) is str and _SYMBOL.fullmatch(symbol) is not None


def canonical_json(value: Any) -> str:
    """The value as JSON with sorted keys and no spaces (ValueError for NaN or infinity)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def body_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _number(x: Any) -> float | None:
    """x as a finite float if it is a JSON number (not a boolean), else None."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    try:
        f = float(x)
    except OverflowError:
        return None
    return f if math.isfinite(f) else None


def _iso(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S" + (".%f" if dt.microsecond else "") + "Z")


def _iso_or_none(ts: Any) -> str | None:
    """`ts` as an ISO time, or None when it is not a number a calendar can show."""
    if _number(ts) is None:
        return None
    try:
        return _iso(ts)
    except (OverflowError, ValueError, OSError):
        return None


def _when_text(ts: Any) -> str:
    return _iso_or_none(ts) or "%r seconds since 1970" % (ts,)


def _span(seconds: float) -> str:
    """A gap in hours and minutes (days too from 72 hours), the minutes rounded up: a gap above a limit never reads as
    the limit itself."""
    minutes = math.ceil(abs(seconds) / 60)
    days, minutes = divmod(minutes, 1440) if minutes >= 72 * 60 else (0, minutes)
    hours, minutes = divmod(minutes, 60)

    def n(k, word):
        return "%d %s%s" % (k, word, "" if k == 1 else "s")
    return " ".join(([n(days, "day")] if days else []) + [n(hours, "hour"), n(minutes, "minute")])


def _plausible(ts: Any) -> bool:
    f = _number(ts)
    return f is not None and MIN_FETCH_TIME <= f <= MAX_FETCH_TIME


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _one_line(text: Any) -> str:
    return " ".join(str(text).split())[:300]


def _valid_key(key: Any) -> bool:
    return type(key) is str and KEY_MIN_CHARS <= len(key) <= KEY_MAX_CHARS and all(0x21 <= ord(c) <= 0x7E for c in key)


# ---- the transport: one GET, one host, a size cap, no redirect

def _allowed_url(url: Any) -> bool:
    """Exactly an https URL under BASE_URL: ASCII, no space, control character, backslash, fragment, percent escape or dot segment."""
    if type(url) is not str or not url.startswith(BASE_URL) or not url.isascii():
        return False
    if any(ord(c) <= 0x20 or ord(c) == 0x7F or c in "\\#%" for c in url):
        return False
    try:
        parts = urllib.parse.urlsplit(url)
        segments = parts.path[len("/api/v1/"):].split("/")
        return parts.hostname == "finnhub.io" and parts.port is None and all(s not in ("", ".", "..") for s in segments)
    except ValueError:
        return False


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer (HTTP 30x), never followed."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    def http_error_302(self, req, fp, code, msg, headers):
        return None

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _build_opener(https_handler=None) -> urllib.request.OpenerDirector:
    """An opener with no proxy from the environment and no redirect."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects(),
                                       https_handler if https_handler is not None else urllib.request.HTTPSHandler())


def _retry_after_header(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f >= 0 else None


def _read_capped(resp, limit: int, deadline_at: float, clock: Callable[[], float]) -> bytes:
    chunks, total = [], 0
    while True:
        if clock() > deadline_at:
            raise TimeoutError("deadline")
        chunk = resp.read(min(65536, limit + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise ValueError("too long")


def _answer_headers(resp) -> dict:
    """The answer's headers with lower-cased names; a name sent twice with different values maps to None (unreadable)."""
    got: dict = {}
    items = resp.headers.items() if getattr(resp, "headers", None) is not None else []
    for name, value in items:
        k = str(name).lower()
        got[k] = value if k not in got or got[k] == value else None
    return got


def http_fetch(url: str, headers: dict, *, opener: urllib.request.OpenerDirector | None = None,
               timeout: float = TIMEOUT_SECONDS, deadline: float = DEADLINE_SECONDS,
               clock: Callable[[], float] = time.monotonic) -> tuple[bytes, dict]:
    """GET `url` (under BASE_URL, else ValueError before any request) and return (the body, at most MAX_BODY_BYTES, and the
    answer's headers with lower-cased names).
    `headers` may hold only X-Finnhub-Token (the key, required), User-Agent and Accept; the key travels as an unredirected
    header and the client name is always USER_AGENT. A status other than 200 raises HttpError (status and Retry-After only);
    a timeout raises TimeoutError, a body over the cap or a changed address ValueError, anything else OSError, each with a
    fixed text and no chained error: nothing the server or the library said is in an error text."""
    if not _allowed_url(url):
        raise ValueError("not a Finnhub API address")
    if (not isinstance(headers, dict) or KEY_HEADER not in headers
            or any(k not in (KEY_HEADER, "User-Agent", "Accept") for k in headers) or not _valid_key(headers[KEY_HEADER])):
        raise ValueError("headers refused")
    opener = opener or _build_opener()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, method="GET")
    req.add_unredirected_header(KEY_HEADER, headers[KEY_HEADER])
    failure: tuple = ("failed",)
    deadline_at = clock() + deadline
    try:
        with opener.open(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if status != 200:
                failure = ("http", status, None)
            elif resp.geturl() != url:
                failure = ("refused",)
            else:
                body = _read_capped(resp, MAX_BODY_BYTES, deadline_at, clock)
                declared = resp.headers.get("Content-Length") if getattr(resp, "headers", None) is not None else None
                if declared is not None and declared.strip().isdigit() and int(declared) != len(body):
                    failure = ("refused",)                      # cut short (or longer than it said): not an answer
                else:
                    return body, _answer_headers(resp)
    except urllib.error.HTTPError as e:
        failure = ("http", e.code, _retry_after_header(e.headers.get("Retry-After") if e.headers else None))
        with contextlib.suppress(Exception):
            e.close()
    except urllib.error.URLError as e:
        failure = ("timeout",) if isinstance(e.reason, TimeoutError) else ("failed",)
    except TimeoutError:
        failure = ("timeout",)
    except ValueError:
        failure = ("refused",)
    except Exception:
        failure = ("failed",)
    # raised here, outside the handlers: no server text and no chained error
    if failure[0] == "http":
        raise HttpError(failure[1], failure[2])
    if failure[0] == "timeout":
        raise TimeoutError("finnhub request timed out")
    if failure[0] == "refused":
        raise ValueError("finnhub answer refused")
    raise OSError("finnhub request failed")


# ---- the store

def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def _schema_ok(conn: sqlite3.Connection) -> bool:
    return _schema_rows_of(conn) == _EXPECTED_SCHEMA


_OPENED_RW, _OPENED_RO, _OPENED_IMMUTABLE = "rw", "ro", "immutable"     # how SQLite had opened the store (see _refusal)
WRITTEN_WHILE_READ = "%s: the store was being written while it was read; ask again"


def _refusal(path: Any, why: str, opened: str | None = None) -> Refused:
    """The one place a store's refusal chooses its last words. Before SQLite was asked to open the store (`opened` None)
    nothing was done. After, the words say SQLite had opened it: in an ordinary mode (`rw`, `ro`) SQLite's own recovery
    may have changed its side files; in immutable mode SQLite writes nothing."""
    if opened is None:
        return Refused("%s %s; nothing was done" % (path, why))
    if opened == _OPENED_IMMUTABLE:
        return Refused("%s %s; SQLite had opened it in immutable mode, which writes nothing" % (path, why))
    return Refused("%s %s; SQLite had opened it, so SQLite's own recovery may have changed its -wal and -shm side files"
                   % (path, why))


def _sqlite_failed(path: Any, e: sqlite3.Error, opened: str) -> Refused:
    return _refusal(path, "is not usable: SQLite could not use it (%s)" % _one_line(e), opened)


def _check_columns(conn: sqlite3.Connection, path: Any, opened: str) -> None:
    if not _schema_ok(conn):
        raise _refusal(path, "is not a company-facts store (its tables are not the expected ones)", opened)


def _varint(b: bytes, i: int) -> tuple[int, int]:
    v = 0
    for k in range(9):
        if i >= len(b):
            raise ValueError("short")
        c = b[i]
        i += 1
        if k == 8:
            return (v << 8) | c, i
        v = (v << 7) | (c & 0x7F)
        if c < 0x80:
            return v, i
    raise ValueError("varint")


def _schema_of_page1(page: bytes, usable: int) -> set:
    """The rows (type, name, tbl_name, sql) of sqlite_master, read from the bytes of page 1 alone: ValueError unless page 1 is
    one table leaf whose records all fit on it in UTF-8 (true of this store: its schema is a few hundred bytes)."""
    h = 100
    if len(page) < usable or page[h] != 0x0D:
        raise ValueError("not one leaf")
    rows = set()
    for c in range(int.from_bytes(page[h + 3:h + 5], "big")):
        ptr = int.from_bytes(page[h + 8 + 2 * c:h + 10 + 2 * c], "big")
        size, i = _varint(page, ptr)
        _, i = _varint(page, i)                                          # the rowid
        if size > usable - 35 or i + size > usable:
            raise ValueError("overflow")
        rec = page[i:i + size]
        head, j = _varint(rec, 0)
        types = []
        while j < head:
            t, j = _varint(rec, j)
            types.append(t)
        if len(types) != 5:
            raise ValueError("not a schema row")
        values, p = [], head
        for t in types:
            if t == 0:
                values.append(None)
            elif 1 <= t <= 6:
                n = (1, 2, 3, 4, 6, 8)[t - 1]
                values.append(int.from_bytes(rec[p:p + n], "big", signed=True))
                p += n
            elif t in (8, 9):
                values.append(t - 8)
            elif t >= 13 and t % 2:
                n = (t - 13) // 2
                values.append(rec[p:p + n].decode("utf-8"))
                p += n
            elif t >= 12:
                n = (t - 12) // 2
                values.append(bytes(rec[p:p + n]))
                p += n
            else:
                raise ValueError("serial type")
        if p > len(rec):
            raise ValueError("short record")
        rows.add((values[0], values[1], values[2], values[4]))
    return rows


WAL_VERSION = 3007000


def _wal_sum(data: bytes, s1: int, s2: int, order: str) -> tuple[int, int]:
    """SQLite's write-ahead checksum over `data` (a multiple of 8 bytes) continuing from (s1, s2); `order` is the word
    order the file's magic names (">" big-endian, "<" little-endian)."""
    words = iter(struct.unpack(order + "%dI" % (len(data) // 4), data))
    for a, b in zip(words, words):
        s1 = (s1 + a + s2) & 0xFFFFFFFF
        s2 = (s2 + b + s1) & 0xFFFFFFFF
    return s1, s2


@dataclass
class _Wal:
    """What the store's write-ahead file proves, read as plain bytes (SQLite does not open it).
    `salted`: frames from the start whose salts match the header (the "content" the read-only rule asks about).
    `counted`: frames SQLite's recovery would take, up to and including the last valid commit frame; `commit_pages` that
    frame's database size in pages (None: the -wal proves nothing); `pages` the page numbers and `page1` the copies of page 1
    in counted frames; `problem` why the side files themselves are refused."""
    problem: str | None = None
    salted: int = 0
    counted: int = 0
    commit_pages: int | None = None
    pages: set = field(default_factory=set)
    page1: list = field(default_factory=list)


def _wal_scan(path: Path, page_size: int) -> _Wal:
    """The store's -wal as SQLite's own recovery would read it: usable only with a WAL magic, format 3007000, this store's
    page size and a header checksum that holds; a frame is valid only with the header's salts, a page number that is not
    zero and the running checksum (from the header's, over the frame header's first 8 bytes and then its page) equal to its
    own; the first invalid frame ends the walk; frames after the last valid commit frame are not counted."""
    for suffix in ("-wal", "-shm"):
        try:
            st = os.lstat(str(path) + suffix)
        except FileNotFoundError:
            continue
        except OSError as e:
            return _Wal(problem="its %s file cannot be examined (%s)" % (suffix, type(e).__name__))
        if not stat.S_ISREG(st.st_mode):
            return _Wal(problem="its %s file is not a regular file" % suffix)
    try:
        fd = os.open(str(path) + "-wal", os.O_RDONLY | _NOFOLLOW | os.O_NONBLOCK | _CLOEXEC)
    except FileNotFoundError:
        return _Wal()
    except OSError as e:
        return _Wal(problem="its -wal file cannot be read (%s)" % type(e).__name__)
    wal = _Wal()
    try:
        size = os.fstat(fd).st_size
        head = os.pread(fd, 32, 0)
        if len(head) < 32:
            return wal
        magic, version, wal_page, _, salt1, salt2, sum1, sum2 = struct.unpack(">8I", head)
        if magic not in _WAL_MAGIC or wal_page != page_size:
            return wal                                                  # SQLite ignores such a file and starts it again
        order = ">" if magic & 1 else "<"
        chain = (version == WAL_VERSION and _wal_sum(head[:24], 0, 0, order) == (sum1, sum2)) or None
        s1, s2 = sum1, sum2
        pending_pages: set = set()
        pending_page1: list = []
        offset = 32
        while offset + 24 + page_size <= size:
            fh = os.pread(fd, 24, offset)
            if len(fh) < 24 or struct.unpack(">2I", fh[8:16]) != (salt1, salt2):
                break                                                   # SQLite reads no frame after this one
            wal.salted += 1
            if chain:
                pgno, commit, f1, f2 = struct.unpack(">2I8x2I", fh)
                page = os.pread(fd, page_size, offset + 24)
                s1, s2 = _wal_sum(page, *_wal_sum(fh[:8], s1, s2, order), order) if len(page) == page_size else (None, None)
                if pgno == 0 or (s1, s2) != (f1, f2):
                    chain = None                                        # the first invalid frame ends the walk
                else:
                    pending_pages.add(pgno)
                    if pgno == 1:
                        pending_page1.append(page)
                    if commit:                                          # a commit frame: everything up to it counts
                        wal.counted, wal.commit_pages = wal.salted, commit
                        wal.pages |= pending_pages
                        wal.page1 += pending_page1
                        pending_pages, pending_page1 = set(), []
            offset += 24 + page_size
        return wal
    except OSError as e:
        return _Wal(problem="its -wal file cannot be read (%s)" % type(e).__name__)
    finally:
        os.close(fd)


def _cut_short(size: int, page_size: int, header_pages: int, wal: _Wal) -> str | None:
    """Why the store is not whole (None when it is). F, the whole pages of the main file, must reach max(H, M): H the page
    count of its own header, M the database size of the -wal's last counted commit; every page beyond F up to there must be
    in a counted frame (with a -wal that proves nothing, none is)."""
    whole = size // page_size
    need = max(header_pages, wal.commit_pages or 0)
    if whole >= need:
        return None
    if need - whole <= len(wal.pages) and all(p in wal.pages for p in range(whole + 1, need + 1)):
        return None
    return ("is damaged (cut short: its main file holds %d whole pages where %d are needed, and its -wal does not hold "
            "every missing one)" % (whole, need))


@dataclass
class _Seen:
    """What the checks saw of a store that passed them."""
    st: os.stat_result
    wal_frames: int                                     # frames whose salts match: the -wal has content (D1)


def _same_file(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _inspect(path: Path) -> tuple[str | None, _Seen | None]:
    """(None, None) when nothing is at `path`; (None, seen) when this version's store is; else (why it is refused, None).
    Read from the file's own bytes and those of its -wal, with plain reads: SQLite opens nothing and nothing is written."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None, None
    except OSError as e:
        return "is not a company-facts store (unreadable: %s)" % type(e).__name__, None
    if stat.S_ISLNK(st.st_mode):
        return "is not a company-facts store (a symbolic link)", None
    if not stat.S_ISREG(st.st_mode):
        return "is not a company-facts store (not a regular file)", None
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW | os.O_NONBLOCK | _CLOEXEC)
    except OSError as e:
        return "is not a company-facts store (unreadable: %s)" % type(e).__name__, None
    try:
        if not _same_file(os.fstat(fd), st):
            return "changed while it was being checked", None
        head = os.pread(fd, 100, 0)
        if len(head) < 100 or head[:16] != _SQLITE_HEADER:
            return "is not a company-facts store (not a SQLite database)", None
        if int.from_bytes(head[68:72], "big") != APPLICATION_ID:
            return "is not a company-facts store (another program's database or an empty SQLite file)", None
        version = int.from_bytes(head[60:64], "big")
        if version != USER_VERSION:
            return "is not a company-facts store (a store of version %d; this program reads version %d)" % (version, USER_VERSION), None
        page_size = int.from_bytes(head[16:18], "big")
        page_size = 65536 if page_size == 1 else page_size
        pages = int.from_bytes(head[28:32], "big")
        usable = page_size - head[20]
        if (page_size < 512 or page_size & (page_size - 1) or usable < 480 or pages < 1 or head[24:28] != head[92:96]
                or int.from_bytes(head[56:60], "big") != 1):
            return "is damaged (its header is not consistent)", None
        page1 = os.pread(fd, page_size, 0)
        size = os.fstat(fd).st_size
    except OSError as e:
        return "is not a company-facts store (unreadable: %s)" % type(e).__name__, None
    finally:
        os.close(fd)
    wal = _wal_scan(path, page_size)
    if wal.problem:
        return "is not usable (%s)" % wal.problem, None
    cut = _cut_short(size, page_size, pages, wal)
    if cut:
        return cut, None
    try:
        tables_ok = _schema_of_page1(page1, usable) == _EXPECTED_SCHEMA
        wal_ok = all(_schema_of_page1(p, usable) == _EXPECTED_SCHEMA for p in wal.page1)
    except (ValueError, IndexError):
        tables_ok = wal_ok = False
    if not tables_ok:
        return "is not a company-facts store (its tables are not the expected ones)", None
    if not wal_ok:
        return "is not a company-facts store (its -wal file changes its tables)", None
    return None, _Seen(st, wal.salted)


def _store_problem(path: Any) -> str | None:
    """Why the file at `path` is not this version's store (None when it is, or when nothing is there)."""
    return _inspect(Path(path))[0]


def _take_lock(path: Path) -> tuple[int, bool]:
    """The exclusive writer lock of the store: a file beside it (`<store>.lock`, never a link) held until closed."""
    lp = Path(str(path) + LOCK_SUFFIX)
    made = False
    try:
        try:
            fd = os.open(lp, os.O_RDWR | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600)
            made = True
        except FileExistsError:
            fd = os.open(lp, os.O_RDWR | _NOFOLLOW, 0o600)
    except OSError as e:
        raise Refused("%s: the lock file cannot be used (%s); nothing was done" % (lp, type(e).__name__)) from None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise Refused("%s: another collector holds the store lock; nothing was done" % lp) from None
    return fd, made


def _temp_name(path: Path) -> Path:
    """Where a new store is built: beside it, under a name no one can predict."""
    return path.with_name(path.name + TEMP_INFIX + os.urandom(16).hex())


def _is_ours(p: Path, ours: os.stat_result) -> bool:
    try:
        st = os.lstat(p)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and _same_file(st, ours)


def _built(fd: int) -> bool:
    """True when the file open at `fd` (the one this module created) holds a whole store with this store's mark."""
    head = os.pread(fd, 100, 0)
    if len(head) < 100 or head[:16] != _SQLITE_HEADER or int.from_bytes(head[68:72], "big") != APPLICATION_ID:
        return False
    page_size = int.from_bytes(head[16:18], "big")
    page_size = 65536 if page_size == 1 else page_size
    return int.from_bytes(head[60:64], "big") == USER_VERSION and os.fstat(fd).st_size == page_size * int.from_bytes(head[28:32], "big")


_NO_LINKS = {errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK, errno.EXDEV, errno.ENOSYS}


def _create_store(path: Path) -> None:
    """Make a new store at `path`, where nothing exists. It is built in a file this module creates itself (O_EXCL, no link
    followed, a name no one can predict) and SQLite is checked to have opened that very file; the finished file is linked into
    place, which fails if anything is at `path`, so a file that appears meanwhile is never replaced and a half-made store is
    never visible. Whatever another program puts at the temporary name is left alone."""
    tmp = _temp_name(path)
    try:
        fd = os.open(tmp, os.O_RDWR | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, 0o600)
    except OSError as e:
        raise Refused("%s cannot be made: its temporary file cannot be created (%s); nothing was done" % (path, type(e).__name__)) from None
    ours = os.fstat(fd)
    replaced = Refused("%s cannot be made: its temporary file was replaced by another program and was left alone; nothing was done" % path)
    renamed = False
    try:
        if not _is_ours(tmp, ours):
            raise replaced
        conn = sqlite3.connect(str(tmp), isolation_level=None)
        try:
            if not _is_ours(tmp, ours):                         # SQLite opened by name: it must be the file made above
                raise replaced
            conn.execute("PRAGMA journal_mode=MEMORY")           # no side file is made for the temporary file
            conn.execute("PRAGMA application_id=%d" % APPLICATION_ID)
            conn.execute("PRAGMA user_version=%d" % USER_VERSION)
            create_schema(conn)
        finally:
            conn.close()
        if not _is_ours(tmp, ours) or not _built(fd):
            raise replaced
        os.fsync(fd)
        try:
            os.link(tmp, path, follow_symlinks=False)
        except FileExistsError:
            raise Refused("%s appeared while the store was being made; it was left alone, nothing was done" % path) from None
        except OSError as e:
            if e.errno not in _NO_LINKS or os.path.lexists(path) or not _is_ours(tmp, ours):
                raise
            os.rename(tmp, path)                                # a volume without hard links, and nothing at `path`
            renamed = True
        if not _is_ours(path, ours):                           # the temporary name was swapped just before the link
            if not renamed:
                with contextlib.suppress(OSError):
                    os.unlink(path)                            # the name this module just made, not what it points to
            raise replaced
    except sqlite3.Error as e:
        raise Refused("%s cannot be made (%s); nothing was done" % (path, type(e).__name__)) from None
    except OSError as e:
        raise Refused("%s cannot be made (%s); nothing was done" % (path, type(e).__name__)) from None
    finally:
        if not renamed and _is_ours(tmp, ours):
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        os.close(fd)


def _leftovers(path: Path) -> list[str]:
    """The names beside the store that a run killed while making a store may have left (never opened here)."""
    try:
        names = os.listdir(path.parent)
    except OSError:
        return []
    return sorted(n for n in names if n.startswith(path.name) and _LEFTOVER.fullmatch(n[len(path.name):]))


def _remove_own_leftovers(path: Path) -> None:
    """Unlink each leftover name that is a hard link to the live store (the store stays); any other leftover is left alone."""
    try:
        live = os.lstat(path)
    except OSError:
        return
    for name in _leftovers(path):
        p = path.parent / name
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode) and _same_file(st, live):
            with contextlib.suppress(OSError):
                os.unlink(p)


class _Writer:
    """The store opened for writing by the one collector that holds its lock."""
    def __init__(self, path: Any):
        if not isinstance(path, (str, os.PathLike)):
            raise Refused("the database must be a path")
        self.path = Path(path)
        self.fd: int | None = None
        self.conn: sqlite3.Connection | None = None
        why, _ = _inspect(self.path)
        if why:
            raise _refusal(self.path, why)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise _refusal(self.path, "cannot be used (%s)" % type(e).__name__) from None
        self.fd, made = _take_lock(self.path)
        opened = None                                           # how SQLite has opened the store, once it has been asked to
        try:
            why, seen = _inspect(self.path)                     # again, now that no other collector can change it
            if why:
                raise _refusal(self.path, why)
            if seen is None:
                _create_store(self.path)
                why, seen = _inspect(self.path)
                if why or seen is None:
                    raise _refusal(self.path, why or "vanished as it was made")
            _remove_own_leftovers(self.path)
            locked = [n for n in ("", "-wal", "-shm") if os.path.lexists(str(self.path) + n) and not os.access(str(self.path) + n, os.W_OK)]
            if locked:                                          # before SQLite is asked to open it: nothing changes
                raise _refusal(self.path, "cannot be written here (%s is read-only; collect into a writable copy)"
                               % ", ".join("its %s file" % n if n else "the store file" for n in locked))
            opened = _OPENED_RW
            self.conn = sqlite3.connect(str(self.path), isolation_level=None, timeout=5.0)
            if not _is_ours(self.path, seen.st):
                raise _refusal(self.path, "changed while it was being opened", opened)
            _check_columns(self.conn, self.path, opened)
            if self.conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                self.conn.execute("PRAGMA journal_mode=WAL")    # a store is built without a journal and turned to WAL here
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("BEGIN IMMEDIATE")                # SQLite can write here, or it is a one-line refusal now,
            self.conn.execute("DELETE FROM runs WHERE 0")       # before any run begins (a write that touches no row)
            self.conn.execute("ROLLBACK")
        except (Refused, sqlite3.Error) as e:
            self.close()
            if made:
                with contextlib.suppress(OSError):
                    os.unlink(str(self.path) + LOCK_SUFFIX)
            if isinstance(e, Refused):
                raise
            raise _sqlite_failed(self.path, e, opened or _OPENED_RW) from None

    def close(self) -> None:
        if self.conn is not None:
            with contextlib.suppress(sqlite3.Error):
                self.conn.close()
            self.conn = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def highwater(self) -> float:
        """The latest possible fetch time anywhere in the store (0 when there is none); an impossible time is ignored."""
        row = self.conn.execute(
            "SELECT max(a, b) FROM (SELECT coalesce(max(fetched_at), 0) a FROM observations WHERE %s),"
            " (SELECT coalesce(max(fetched_at), 0) b FROM news WHERE %s)" % (_plausible_sql("fetched_at"), _plausible_sql("fetched_at"))).fetchone()
        return float(row[0])


def _writable_place(path: Path) -> bool:
    """True when SQLite could make its side files beside the store: the folder is writable and not on a read-only volume."""
    folder = path.parent
    try:
        if os.statvfs(folder).f_flag & getattr(os, "ST_RDONLY", 1):
            return False
    except OSError:
        return False
    return os.access(folder, os.W_OK | os.X_OK)


def _fingerprint(path: Path) -> tuple:
    out = []
    for p in (str(path), str(path) + "-wal"):
        try:
            st = os.lstat(p)
            out.append((st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns))
        except OSError:
            out.append(None)
    return tuple(out)


@contextlib.contextmanager
def _reader(db: Any):
    """A read-only view of the store. A connection is used as given. A path must be this version's store; it is read in
    SQLite's ordinary read-only mode, which may make SQLite's own side files in a writable folder. Only where the place cannot
    hold side files is it read in `immutable` mode, and only when no -wal with content is beside it. No lock file is touched."""
    if isinstance(db, sqlite3.Connection):
        yield db
        return
    if not isinstance(db, (str, os.PathLike)):
        raise Refused("the database must be a path")
    path = Path(db)
    if not os.path.lexists(path):
        raise Refused("no store at %s; run collect first" % path)
    why, seen = _inspect(path)
    if why or seen is None:
        raise _refusal(path, why or "vanished as it was read")
    full = Path(os.path.abspath(path))
    immutable = not _writable_place(full)
    if immutable and seen.wal_frames:
        raise Refused("%s is in a read-only place and its -wal file holds changes that are not in the store yet, which cannot be "
                      "read here without writing; copy the store with its -wal and -shm files to a writable folder and read "
                      "it there" % path)
    before = _fingerprint(full) if immutable else None
    opened = _OPENED_IMMUTABLE if immutable else _OPENED_RO
    uri = "file:%s?%s" % (urllib.parse.quote(str(full)), "immutable=1" if immutable else "mode=ro")

    def overlapped() -> bool:
        """In immutable mode SQLite takes no lock: a changed fingerprint means the read overlapped a write."""
        return immutable and _fingerprint(full) != before

    def failed(e: sqlite3.Error) -> Refused:
        return Refused(WRITTEN_WHILE_READ % path) if overlapped() else _sqlite_failed(path, e, opened)
    conn = None
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        _check_columns(conn, path, opened)
        if not _is_ours(full, seen.st):
            raise _refusal(path, "changed while it was being opened (read it again)", opened)
    except sqlite3.Error as e:
        if conn is not None:
            conn.close()
        raise failed(e) from None
    except Refused:
        conn.close()
        if overlapped():
            raise Refused(WRITTEN_WHILE_READ % path) from None
        raise
    try:
        yield conn
    except sqlite3.Error as e:
        raise failed(e) from None
    finally:
        conn.close()
    if overlapped():
        raise Refused(WRITTEN_WHILE_READ % path)


@contextlib.contextmanager
def _snapshot(db: Any):
    """`_reader` inside one read transaction: every statement run on it reads the same commit."""
    with _reader(db) as conn:
        began = not conn.in_transaction
        if began:
            conn.execute("BEGIN")
        try:
            yield conn
        finally:
            if began:
                with contextlib.suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")


# ---- the readers: no look-ahead

def _no_repeated_keys(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("repeated key")
        out[k] = v
    return out


def _no_constant(name):
    raise ValueError("not JSON")


def _loads_strict(text: str) -> Any:
    """Strict JSON: no NaN or Infinity, no repeated key (ValueError or RecursionError otherwise)."""
    return json.loads(text, object_pairs_hook=_no_repeated_keys, parse_constant=_no_constant)


def _check_when(when: Any) -> float:
    w = _number(when)
    if w is None:
        raise ValueError("`when` must be a finite number of seconds since the epoch")
    return w


def _check_fact(symbol: Any, kind: Any) -> None:
    if kind not in STORED_KINDS:
        raise ValueError("kind must be one of %s (news is read with news_as_of)" % ", ".join(STORED_KINDS))
    if (symbol != "*") if kind == EARNINGS_CALENDAR else not valid_symbol(symbol):
        raise ValueError("bad symbol for this kind")


def as_of(db: Any, symbol: str, kind: str, when: float) -> dict | None:
    """The newest observation of (symbol, kind) with fetched_at <= `when`, as {symbol, kind, fetched_at, body_sha256, body}
    (body parsed), or None. Nothing fetched after `when` is ever looked at. `db` is a path or an open connection."""
    _check_fact(symbol, kind)
    when = _check_when(when)
    with _reader(db) as conn:
        row = conn.execute("SELECT fetched_at, body_sha256, body FROM observations WHERE symbol=? AND kind=? AND fetched_at<=?"
                           " AND %s ORDER BY fetched_at DESC, rowid DESC LIMIT 1" % _plausible_sql("fetched_at"),
                           (symbol, kind, when)).fetchone()
    if row is None:
        return None
    fetched_at, sha, text = row
    try:
        if type(text) is not str or body_sha256(text) != sha:
            raise ValueError("hash")
        parsed = _loads_strict(text)
    except (ValueError, RecursionError):
        raise Refused("a stored %s row for %s does not match its hash; the store is damaged" % (kind, symbol)) from None
    return {"symbol": symbol, "kind": kind, "fetched_at": fetched_at, "body_sha256": sha, "body": parsed}


def news_as_of(db: Any, symbol: str, when: float, since: float | None = None) -> list[dict]:
    """Articles of `symbol` published AND fetched at or before `when` (and published at or after `since`, if given), oldest
    first."""
    if not valid_symbol(symbol):
        raise ValueError("bad symbol")
    when = _check_when(when)
    sql = ("SELECT symbol, article_id, published_at, fetched_at, headline, source, url, summary FROM news"
           " WHERE symbol=? AND published_at<=? AND fetched_at<=?"
           " AND typeof(published_at) IN ('integer','real') AND " + _plausible_sql("fetched_at"))
    args: list = [symbol, when, when]
    if since is not None:
        sql += " AND published_at>=?"
        args.append(_check_when(since))
    sql += " ORDER BY published_at, fetched_at, article_id"
    keys = _COLUMNS["news"]
    with _reader(db) as conn:
        return [dict(zip(keys, r)) for r in conn.execute(sql, args)]


def _count(x: Any) -> bool:
    return type(x) is int and x >= 0


def _run_row(row: tuple) -> dict | None:
    """A runs row as status shows it, or None when it cannot be shown (a time that is impossible, a count that is not a
    whole number, refused kinds that are not text)."""
    started, ended, calls, stored, unchanged, failed, kinds = row
    if not _plausible(started) or (ended is not None and not _plausible(ended)):
        return None
    if not all(_count(x) for x in (calls, stored, unchanged, failed)) or type(kinds) is not bytes:
        return None
    try:
        text = kinds.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return dict(zip(_COLUMNS["runs"], (started, ended, calls, stored, unchanged, failed, text)))


def status(db: Any) -> dict:
    """Rows per kind, symbols covered, the last run's counts, the oldest and newest possible fetch, the runs that were
    interrupted, and what it could not show: rows with an impossible fetch time, rows of no known kind, runs rows that
    cannot be shown (skipped and counted), and the leftover names a killed run may have left beside the store. Every
    statement runs on one connection inside one read transaction: all of it belongs to one commit."""
    plausible = _plausible_sql("fetched_at")
    with _snapshot(db) as conn:
        rows = {k: 0 for k in STORED_KINDS + ("news",)}
        for kind, n in conn.execute("SELECT kind, count(*) FROM observations WHERE kind IN (%s) GROUP BY kind"
                                    % ",".join("?" * len(STORED_KINDS)), STORED_KINDS):
            rows[kind] = n
        other_kinds = conn.execute("SELECT count(*) FROM observations").fetchone()[0] - sum(rows.values())
        rows["news"] = conn.execute("SELECT count(*) FROM news").fetchone()[0]
        symbols = conn.execute("SELECT count(*) FROM (SELECT symbol FROM observations WHERE symbol<>'*'"
                               " UNION SELECT symbol FROM news)").fetchone()[0]
        last, unreadable = None, 0
        number = "CASE WHEN typeof(%s) IN ('integer','real') THEN %s WHEN typeof(%s)='null' THEN NULL ELSE -1 END"
        for row in conn.execute("SELECT %s, CASE WHEN typeof(kinds_refused)='text' THEN CAST(kinds_refused AS BLOB) END"
                                " FROM runs ORDER BY rowid DESC"     # text where a number belongs comes back as -1, never decoded
                                % ", ".join(number % (c, c, c) for c in _COLUMNS["runs"][:6])):
            shown = _run_row(row)
            if shown is None:
                unreadable += 1
            elif last is None:
                last = shown
        span = conn.execute("SELECT min(lo), max(hi) FROM (SELECT min(fetched_at) lo, max(fetched_at) hi FROM observations"
                            " WHERE %s UNION ALL SELECT min(fetched_at), max(fetched_at) FROM news WHERE %s)"
                            % (plausible, plausible)).fetchone()
        impossible = conn.execute("SELECT (SELECT count(*) FROM observations WHERE NOT %s) + (SELECT count(*) FROM news WHERE NOT %s)"
                                  % (plausible, plausible)).fetchone()[0]
        interrupted = conn.execute("SELECT count(*) FROM runs WHERE kinds_refused=?", (INTERRUPTED,)).fetchone()[0]
    leftovers = _leftovers(Path(db)) if isinstance(db, (str, os.PathLike)) else []
    return {"rows": rows, "symbols": symbols, "oldest": span[0], "newest": span[1], "interrupted": interrupted,
            "last_run": last, "impossible": impossible, "other_kinds": other_kinds, "unreadable_runs": unreadable,
            "leftovers": leftovers}


# ---- the collector

class _Pacer:
    """At most MAX_REQUESTS_PER_WINDOW request starts in any WINDOW_SECONDS of true time: it reads a monotonic clock, never
    the wall clock (a correction of that cannot let a burst through)."""
    def __init__(self, clock: Callable[[], float], sleep: Callable[[float], Any]):
        self.clock, self.sleep, self.sent = clock, sleep, deque()
        self.stalled = 0                                                  # waits in a row across which the clock did not advance

    def wait(self) -> None:
        while True:
            now = self.clock()
            self.sent = deque(min(t, now) for t in self.sent)            # (a monotonic clock does not step back; if one does, be safe)
            while self.sent and now - self.sent[0] >= WINDOW_SECONDS + WINDOW_MARGIN / 2:     # (the wait below overshoots it)
                self.sent.popleft()
            if len(self.sent) < MAX_REQUESTS_PER_WINDOW:
                self.sent.append(now)
                return
            if self.stalled >= MAX_STALLED_WAITS:
                raise _Stop("pacing clock does not advance (%d waits)" % self.stalled)
            self.sleep(max(0.0, self.sent[0] + WINDOW_SECONDS + WINDOW_MARGIN - now))
            self.stalled = 0 if self.clock() > now else self.stalled + 1


def _served_at(headers: Mapping) -> float | None:
    """The time in the answer's Date header, or None when there is none or it cannot be read (a name sent twice with
    different values, not text, not an HTTP date, or a time no calendar holds)."""
    values = [v for k, v in headers.items() if isinstance(k, str) and k.lower() == "date"]
    if len(values) != 1 or type(values[0]) is not str or len(values[0]) > 100:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(values[0])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)             # "-0000": a time in UTC whose zone is not known
        ts = dt.timestamp()
    except (TypeError, ValueError, IndexError, OverflowError, OSError):
        return None
    return ts if math.isfinite(ts) else None


def _retry_after(value: Any) -> float:
    f = _number(value)
    if f is None or f < 0:
        return RETRY_AFTER_DEFAULT
    return min(f, RETRY_AFTER_MAX)


def _status_of(e: BaseException) -> int | None:
    if isinstance(e, HttpError):
        return e.status if isinstance(e.status, int) else None
    if isinstance(e, urllib.error.HTTPError):
        return e.code
    return None


class _Bad(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _check_symbols(symbols: Any) -> list[str]:
    if not isinstance(symbols, (list, tuple)):
        raise Refused("symbols must be a list")
    out: list[str] = []
    for i, s in enumerate(symbols, 1):
        if not valid_symbol(s):
            raise Refused("symbol %d is not valid (a capital letter, then up to 9 capitals, digits, dots or hyphens); "
                          "nothing was asked" % i)
        if s not in out:
            out.append(s)
    return out


def _check_max_calls(n: Any) -> int:
    if type(n) is not int or n < 1:
        raise Refused("max_calls must be a whole number of at least 1")
    return n


def _default_credentials(name: str) -> str:
    from valedia_trader.core import secrets
    return secrets.secret(name)


def _read_key(credentials: Callable[[str], Any] | None) -> str:
    ok, key = False, None
    try:
        key = (credentials or _default_credentials)(KEY_NAME)
        ok = _valid_key(key)
    except Exception:
        ok = False
    if not ok:                                                  # raised outside the handler: no chained error text
        raise Refused("credential %s is missing or unusable: set it in the secrets file or the environment (%d to %d visible "
                      "characters, no spaces); nothing was asked" % (KEY_NAME, KEY_MIN_CHARS, KEY_MAX_CHARS))
    return key


def _key_forms(key: str) -> tuple[list[bytes], list[str]]:
    """What an echo of the key can look like: (byte strings to look for in the raw body, strings to look for in every parsed
    string and member name). Plain, JSON-escaped, base64 (both alphabets, at all three alignments, without the characters a
    neighbour could change) and hex; and each half when a half is at least HALF_KEY_MIN_CHARS long."""
    kb = key.encode("ascii")
    plain = [key]
    for half in (key[:len(key) // 2], key[len(key) // 2:]):
        if len(half) >= HALF_KEY_MIN_CHARS:
            plain.append(half)
    encoded = set()
    for alphabet in (base64.b64encode, base64.urlsafe_b64encode):
        for offset, skip in ((0, 0), (1, 2), (2, 3)):
            data = bytes(offset) + kb
            text = alphabet(data).decode().rstrip("=")
            piece = text[skip:len(text) - (1 if len(data) % 3 else 0)]
            if len(piece) >= 8:
                encoded.add(piece)
    hexed = kb.hex()
    raw = {p.encode() for p in plain + list(encoded)} | {json.dumps(key)[1:-1].encode(), json.dumps(key, ensure_ascii=False)[1:-1].encode(),
                                                           hexed.encode(), hexed.upper().encode()}
    texts = plain + sorted(encoded) + [hexed]                    # the hex form is compared in lower case
    return sorted(raw), texts


def _strings_of(value: Any):
    """Every string and member name in a parsed JSON value (iteratively: nesting cannot exhaust the stack)."""
    stack = [value]
    while stack:
        v = stack.pop()
        if isinstance(v, str):
            yield v
        elif isinstance(v, dict):
            for k, x in v.items():
                yield k
                stack.append(x)
        elif isinstance(v, list):
            stack.extend(v)


class _Run:
    def __init__(self, store: _Writer, key: str, fetch, clock, sleep, monotonic, max_calls: int):
        self.store, self.fetch, self.clock, self.sleep, self.max_calls = store, fetch, clock, sleep, max_calls
        self.headers = {KEY_HEADER: key, "User-Agent": USER_AGENT, "Accept": "application/json"}
        self.raw_forms, self.text_forms = _key_forms(key)
        self.pacer = _Pacer(monotonic, sleep)
        self.refused: set[str] = set()
        self.result = Result(started_at=clock())
        self.highwater = store.highwater()

    def _fail(self, kind: str, reason: str) -> None:
        self.result.failed += 1
        self.result.failures.append((kind, reason))
        return None

    def _ask(self, kind: str, url: str) -> Any:
        """One request with its retries: the body, or None when the request was counted (failed, or the kind refused)."""
        retries_429 = tries = 0
        while True:
            if self.result.calls >= self.max_calls:
                raise _Stop("max_calls")
            self.pacer.wait()
            self.result.calls += 1
            try:
                return self.fetch(url, self.headers)
            except Exception as e:
                status = _status_of(e)
                if status == 429:
                    if retries_429 >= MAX_429_RETRIES:
                        return self._fail(kind, "http 429")
                    retries_429 += 1
                    self.sleep(_retry_after(getattr(e, "retry_after", None)))
                    continue
                if status == 403:
                    self.refused.add(kind)
                    return None
                if status == 401:
                    self._fail(kind, "http 401")
                    raise _Stop("key rejected") from None
                if (status is not None and 500 <= status <= 599) or (status is None and isinstance(e, OSError)):
                    tries += 1
                    if tries >= MAX_TRANSIENT_TRIES:
                        return self._fail(kind, ("http %d" % status) if status else
                                          ("timeout" if isinstance(e, TimeoutError) else "connection"))
                    continue
                return self._fail(kind, ("http %d" % status) if status else "error %s" % type(e).__name__)

    def _decode(self, body: Any) -> tuple[Any, str]:
        if type(body) is not bytes:
            raise _Bad("not bytes")
        if len(body) > MAX_BODY_BYTES:
            raise _Bad("over 5 MB")
        if any(k in body for k in self.raw_forms):
            raise _Bad("key in answer")
        try:
            value = _loads_strict(body.decode("utf-8"))
            canon = canonical_json(value)
        except (ValueError, RecursionError):
            raise _Bad("not JSON or a repeated key") from None
        for text in _strings_of(value):
            if any(f in text for f in self.text_forms) or self.text_forms[-1] in text.lower():
                raise _Bad("key in answer")
        return value, canon

    def _store(self, kind: str, symbol: str, value: Any, canon: str) -> None:
        at = self.clock()                                       # after the answer arrived: never earlier than the fact
        if not _plausible(at):
            self._fail(kind, "clock impossible")                 # never stamp a time the store would call impossible
            return
        if at < self.highwater:
            self._fail(kind, "clock behind the store")
            return
        conn = self.store.conn
        stored = unchanged = skipped = 0
        conn.execute("BEGIN IMMEDIATE")
        try:
            if kind == "news":
                for a in value:
                    got = self._insert_article(conn, symbol, a, at)
                    if got is None:
                        skipped += 1
                    else:
                        stored += got
                unchanged = 0 if stored else 1
            else:
                sha = body_sha256(canon)
                row = conn.execute("SELECT body_sha256 FROM observations WHERE symbol=? AND kind=? AND %s"
                                   " ORDER BY fetched_at DESC, rowid DESC LIMIT 1" % _plausible_sql("fetched_at"), (symbol, kind)).fetchone()
                if row is not None and row[0] == sha:
                    unchanged = 1
                else:
                    conn.execute("INSERT INTO observations VALUES (?,?,?,?,?)", (symbol, kind, at, sha, canon))
                    stored = 1
            conn.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
        self.result.stored += stored
        self.result.unchanged += unchanged
        self.result.skipped += skipped
        self.highwater = max(self.highwater, at)

    @staticmethod
    def _insert_article(conn, symbol: str, a: Any, at: float) -> int | None:
        """1 if the article was new, 0 if it was already stored, None if it is skipped: no usable id (a string of 1 to 128
        characters or an integer of magnitude below 2**63), no usable time (a finite number from 2000-01-01 to two days after
        the fetch), or text that cannot be stored. Nothing is filled in."""
        if not isinstance(a, dict):
            return None
        aid, published = a.get("id"), _number(a.get("datetime"))
        if isinstance(aid, bool) or not isinstance(aid, (int, str)):
            return None
        if (len(aid) < 1 or len(aid) > MAX_ARTICLE_ID_CHARS) if isinstance(aid, str) else not abs(aid) < ARTICLE_ID_INT_LIMIT:
            return None
        if published is None or not MIN_ARTICLE_TIME <= published <= at + ARTICLE_FUTURE_SLACK:
            return None
        texts = []
        for k in ("headline", "source", "url", "summary"):
            v = a.get(k)
            texts.append(v if isinstance(v, str) else None)
        try:
            ident = str(aid)
            for t in [ident] + texts:
                if t is not None:
                    t.encode("utf-8")
        except (UnicodeEncodeError, ValueError):
            return None
        cur = conn.execute("INSERT OR IGNORE INTO news VALUES (?,?,?,?,?,?,?,?)", (symbol, ident, published, at, *texts))
        return cur.rowcount

    def _one(self, kind: str, symbol: str, url: str) -> None:
        answer = self._ask(kind, url)
        if answer is None:
            return
        arrived = self.clock()
        if type(answer) is not tuple or len(answer) != 2:
            self._fail(kind, "not an answer")                    # the transport answers (body, headers)
            return
        body, headers = answer
        if not isinstance(headers, Mapping):
            self._fail(kind, "no headers")
            return
        served = _served_at(headers)
        if served is None:
            self.result.undated += 1
        elif _number(arrived) is not None and abs(arrived - served) > DATE_TOLERANCE:
            raise _Stop("%s %s but this computer's clock reads %s: they differ by %s (more than 24 hours), so nothing of that "
                        "answer was stored; set the clock right and run again"
                        % (CLOCK_STOP, _when_text(served), _when_text(arrived), _span(arrived - served)))
        try:
            value, canon = self._decode(body)
        except _Bad as e:
            self._fail(kind, e.reason)
            return
        if type(value) is not EXPECTED_TYPES[kind]:
            self._fail(kind, "wrong type")
            return
        self._store(kind, symbol, value, canon)

    @staticmethod
    def _url(path: str, query: dict) -> str:
        return BASE_URL + path + "?" + "&".join("%s=%s" % (k, v) for k, v in query.items())

    def _news_url(self, symbol: str) -> str:
        """The news request: from two days before the newest usable stored article of the symbol (30 days back when there is
        none); a stored row with an impossible time is ignored."""
        now = self.clock()
        today = _day(now)
        newest = self.store.conn.execute(
            "SELECT max(published_at) FROM news WHERE symbol=? AND typeof(published_at) IN ('integer','real')"
            " AND published_at BETWEEN ? AND ?", (symbol, MIN_ARTICLE_TIME, now + ARTICLE_FUTURE_SLACK)).fetchone()[0]
        try:
            start = _day(now - NEWS_FIRST_DAYS * 86400) if newest is None else min(_day(newest - NEWS_OVERLAP_DAYS * 86400), today)
        except (OverflowError, ValueError, OSError):
            start = _day(now - NEWS_FIRST_DAYS * 86400)
        return self._url("company-news", {"symbol": symbol, "from": start, "to": today})

    def _guarded(self, kind: str, work: Callable[[], Any]) -> None:
        """Nothing in an answer or in the store can end a run: any error while handling one symbol and kind is counted failed
        with a short fixed reason (the error's type, never its text), and the run goes on."""
        try:
            work()
        except _Stop:
            raise
        except Exception as e:
            self._fail(kind, "error %s" % type(e).__name__)

    def _plan(self, kind: str, symbol: str) -> None:
        if kind == EARNINGS_CALENDAR:
            now = self.clock()
            url = self._url(CALENDAR_PATH, {"from": _day(now), "to": _day(now + CALENDAR_DAYS * 86400)})
        elif kind == "news":
            url = self._news_url(symbol)
        else:
            path, fixed = _REQUESTS[kind]
            url = self._url(path, dict({"symbol": symbol}, **fixed))
        self._one(kind, symbol, url)

    def go(self, symbols: list[str]) -> Result:
        conn = self.store.conn
        if self.result.started_at < self.highwater:
            newest = _when_text(self.highwater)
            raise Refused("the clock reads %s, earlier than the newest fetch already in the store (%s) by %s; runs resume by "
                          "themselves once the clock passes %s; to collect sooner, collect into another store (--db PATH), or "
                          "correct this computer's clock if it is wrong; nothing was asked"
                          % (_when_text(self.result.started_at), newest, _span(self.highwater - self.result.started_at), newest))
        # a row still open here belongs to a run that died (this collector holds the lock): mark it, keep its start time
        conn.execute("UPDATE runs SET kinds_refused=? WHERE ended_at IS NULL AND kinds_refused=''", (INTERRUPTED,))
        conn.execute("INSERT INTO runs VALUES (?, NULL, 0, 0, 0, 0, '')", (self.result.started_at,))
        run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        try:
            try:
                if EARNINGS_CALENDAR not in self.refused:
                    self._guarded(EARNINGS_CALENDAR, lambda: self._plan(EARNINGS_CALENDAR, "*"))
                for symbol in symbols:
                    for kind in KINDS:
                        if kind not in self.refused:
                            self._guarded(kind, lambda kind=kind, symbol=symbol: self._plan(kind, symbol))
            except _Stop as stop:
                self.result.stopped = stop.reason
        finally:
            r = self.result
            r.kinds_refused = tuple(sorted(self.refused))
            r.ended_at = self.clock()
            with contextlib.suppress(sqlite3.Error):
                conn.execute("UPDATE runs SET ended_at=?, calls=?, stored=?, unchanged=?, failed=?, kinds_refused=? WHERE rowid=?",
                             (r.ended_at, r.calls, r.stored, r.unchanged, r.failed, ",".join(r.kinds_refused), run_id))
        return self.result


def collect(symbols: Any, db: Any, *, fetch: Callable[[str, dict], Any] = http_fetch, clock: Callable[[], float] = time.time,
            sleep: Callable[[float], Any] = time.sleep, credentials: Callable[[str], Any] | None = None,
            max_calls: int = DEFAULT_MAX_CALLS, monotonic: Callable[[], float] = time.monotonic) -> Result:
    """Ask Finnhub for each symbol's profile, metrics, earnings, recommendations, insiders and news, and once for the
    earnings calendar, and store what is new. Refused (Refused, before any request) for a bad symbol, a bad `max_calls`, a
    missing key, a store that is not ours, one that another run holds, a clock outside 2000 to 2100, or a clock earlier than
    the store's newest fetch. `fetch(url, headers)` returns (the body bytes, the answer's headers with lower-cased names) or
    raises HttpError (or another error); `credentials(name)` returns the key. `clock` stamps what is fetched and is compared
    with each answer's Date; `monotonic` (never the wall clock) paces the requests."""
    symbols = _check_symbols(symbols)
    max_calls = _check_max_calls(max_calls)
    key = _read_key(credentials)
    now = clock()
    if not _plausible(now):
        raise Refused("the clock reads %s, which is not between 2000-01-01 and 2100-01-01; set it right and run again; "
                      "nothing was asked" % _when_text(now))
    store = _Writer(db)
    try:
        return _Run(store, key, fetch, clock, sleep, monotonic, max_calls).go(symbols)
    finally:
        store.close()


# ---- the command line

def universe_symbols(name: str = "stocks", path: Any = None) -> list[str]:
    """The `symbols` of one entry of the universes file (research_loop/universes.json); the file is only read."""
    p = Path(UNIVERSES_PATH if path is None else path)
    try:
        symbols = json.loads(p.read_text())[name]["symbols"]
    except (OSError, ValueError, KeyError, TypeError):
        raise Refused("the universes file %s has no usable entry %r" % (p, name)) from None
    if not isinstance(symbols, list) or not symbols or not all(isinstance(s, str) for s in symbols):
        raise Refused("the universes file %s has no usable entry %r" % (p, name))
    return symbols


def _parse_when(text: str) -> float:
    try:
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        raise Refused("--as-of must be an ISO time in UTC, like 2026-11-14T12:00:00Z") from None
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise Refused("--as-of must be an ISO time in UTC, like 2026-11-14T12:00:00Z (a zone is required)")
    return dt.timestamp()


def _show_fact(fact: dict | None) -> dict | None:
    return None if fact is None else dict(fact, fetched_at=_iso(fact["fetched_at"]))


def _show_news(articles: list[dict], skipped: list) -> list[dict]:
    """The articles as `show` prints them; one it cannot show (a time no calendar holds, a field that is not text) is
    skipped and counted in `skipped`."""
    out = []
    for a in articles:
        published, fetched = _iso_or_none(a["published_at"]), _iso_or_none(a["fetched_at"])
        fields_ok = all(a[k] is None or type(a[k]) is str for k in ("headline", "source", "url", "summary"))
        if published is None or fetched is None or not fields_ok or type(a["article_id"]) not in (str, int):
            skipped.append(a)
            continue
        out.append(dict(a, published_at=published, fetched_at=fetched))
    return out


def _status_text(db: Any) -> str:
    st = status(db)
    last = st["last_run"]
    lines = ["company_facts store: %s" % db,
             "rows: " + ", ".join("%s %d" % (k, n) for k, n in st["rows"].items()),
             "symbols covered: %d" % st["symbols"],
             "last run: none" if last is None else
             "last run: started %s, ended %s, %d calls, %d stored, %d unchanged, %d failed, refused kinds: %s" % (
                 _iso(last["started_at"]), _iso(last["ended_at"]) if last["ended_at"] is not None else "never",
                 last["calls"], last["stored"], last["unchanged"], last["failed"], _one_line(last["kinds_refused"]) or "none"),
             "interrupted runs: %d" % st["interrupted"],
             "oldest fetch: " + (_iso(st["oldest"]) if st["oldest"] is not None else "none"),
             "newest fetch: " + (_iso(st["newest"]) if st["newest"] is not None else "none"),
             "skipped and counted: %d rows with an impossible fetch time, %d rows of no known kind, %d runs rows that cannot be "
             "shown" % (st["impossible"], st["other_kinds"], st["unreadable_runs"]),
             "leftover temporary files: " + (", ".join(st["leftovers"]) or "none")]
    return "\n".join(lines)


def _show(args, clock) -> tuple[str, int]:
    symbol, kind = args.symbol, args.kind
    if kind is not None and kind not in KINDS + (EARNINGS_CALENDAR,):
        raise Refused("--kind must be one of %s" % ", ".join(KINDS + (EARNINGS_CALENDAR,)))
    when = _parse_when(args.as_of) if args.as_of is not None else clock()
    skipped: list = []
    try:
        _check_when(when)                                # the input is checked before the store is opened
        if kind == "news":
            if not valid_symbol(symbol):
                raise ValueError("bad symbol")
        else:
            for k in [kind] if kind is not None else [EARNINGS_CALENDAR] if symbol == "*" else [k for k in KINDS if k != "news"]:
                _check_fact(symbol, k)
        with _snapshot(args.db) as conn:                 # one connection, one read transaction: every answer from one commit
            if kind == "news":
                out: Any = _show_news(news_as_of(conn, symbol, when), skipped)
            elif kind is not None:
                out = _show_fact(as_of(conn, symbol, kind, when))
            elif symbol == "*":
                out = {EARNINGS_CALENDAR: _show_fact(as_of(conn, symbol, EARNINGS_CALENDAR, when))}
            else:
                out = {k: _show_fact(as_of(conn, symbol, k, when)) for k in KINDS if k != "news"}
                out["news"] = _show_news(news_as_of(conn, symbol, when), skipped)
    except ValueError as e:
        raise Refused(_one_line(e)) from None
    return json.dumps(out, indent=2), len(skipped)


def _load_secrets_file() -> None:
    """Load the owner's secrets file into the environment (the way the project's other commands do)."""
    from valedia_trader.core import secrets
    try:
        secrets.load_env()
    except secrets.SecretError as e:
        raise Refused(_one_line(e)) from None


class _Parser(argparse.ArgumentParser):
    """An argument error is a refusal like any other: one line, exit status 2."""
    def error(self, message):
        raise Refused("%s (see --help)" % _one_line(message))


def main(argv=None, *, fetch=None, clock: Callable[[], float] = time.time, sleep: Callable[[float], Any] = time.sleep,
         credentials: Callable[[str], Any] | None = None, monotonic: Callable[[], float] = time.monotonic) -> int:
    parser = _Parser(prog="python -m valedia_trader.data_and_fees.company_facts",
                                     description="Collect and read company facts from Finnhub's free plan, point-in-time.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("collect", help="ask Finnhub and store what is new")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--symbols", help="comma-separated, like AAPL,MSFT")
    group.add_argument("--universe", choices=["stocks"], help="the `stocks` entry of research_loop/universes.json (the default)")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--max-calls", default=str(DEFAULT_MAX_CALLS),
                   help="stop cleanly after this many requests (default %d)" % DEFAULT_MAX_CALLS)
    p = sub.add_parser("status", help="rows per kind, symbols, the last run, the fetch range")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p = sub.add_parser("show", help="the newest fact (or all kinds) known as of a time")
    p.add_argument("symbol")
    p.add_argument("--kind")
    p.add_argument("--as-of", dest="as_of", help="ISO time in UTC (default: now)")
    p.add_argument("--db", default=str(DEFAULT_DB))
    try:
        args = parser.parse_args(argv)
        if args.command == "collect":
            try:
                max_calls = int(args.max_calls)
            except ValueError:
                raise Refused("--max-calls must be a whole number of at least 1") from None
            symbols = args.symbols.split(",") if args.symbols is not None else universe_symbols("stocks")
            _check_symbols(symbols)                      # a bad command is refused before the secrets file is even opened
            _check_max_calls(max_calls)
            if credentials is None:
                _load_secrets_file()
            result = collect(symbols, args.db, fetch=fetch or http_fetch, clock=clock, sleep=sleep, credentials=credentials,
                             max_calls=max_calls, monotonic=monotonic)
            print(result.summary())
            return 2 if result.stopped == "key rejected" or (result.stopped or "").startswith(CLOCK_STOP) else 0
        try:
            if args.command == "status":
                print(_status_text(args.db))
                return 0
            text, skipped = _show(args, clock)
        except (Refused, ValueError):
            raise
        except Exception as e:                           # never a traceback from status or show: one line, exit status 2
            raise Refused("%s cannot be shown (%s)" % (args.db, type(e).__name__)) from None
        print(text)
        if skipped:
            print("company_facts: %d stored articles cannot be shown and were skipped" % skipped, file=sys.stderr)
        return 0
    except Refused as e:
        print("company_facts: refused: %s" % _one_line(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
