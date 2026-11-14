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

The store (SQLite, WAL, one writer at a time):
  observations(symbol, kind, fetched_at, body_sha256, body)  a row only when the answer, as canonical JSON (sorted keys, no
                                                             spaces), differs from the newest stored one for that symbol and
                                                             kind; the market calendar is under the symbol `*`
  news(symbol, article_id, published_at, fetched_at, ...)    one row per article, written when first seen, never changed
  runs(started_at, ended_at, calls, stored, unchanged, failed, kinds_refused)

No look-ahead: `as_of(db, symbol, kind, when)` returns the newest observation fetched at or before `when`;
`news_as_of(db, symbol, when, since=None)` returns only articles published AND fetched at or before `when`. The reader never
looks at anything fetched later, whatever the store holds, and a stored row whose text no longer matches its hash is refused.

Politeness and safety: at most 55 requests in any 60 seconds (the plan allows 60); a 429 waits the Retry-After (30 seconds if
none, at most 120) and tries again up to 3 times; a 403 marks that kind "not on this plan" for the rest of the run; a 5xx or a
timeout gets two tries; `max_calls` ends a run cleanly. Only https://finnhub.io/api/v1/ is requested, no redirect is followed,
an answer over 5 MB, not JSON, or with a repeated key is refused. The key is read through `credentials("FINNHUB_API_KEY")`
(`valedia_trader.core.secrets.secret` by default) and travels only in the `X-Finnhub-Token` header: it is never in a URL, an
error text, a message, a result or the database, and the client name is the generic constant `USER_AGENT`.

This module is not on the order path and nothing on the order path may import it.

    python -m valedia_trader.data_and_fees.company_facts collect [--symbols A,B,C | --universe stocks] [--db PATH] [--max-calls N]
    python -m valedia_trader.data_and_fees.company_facts status [--db PATH]
    python -m valedia_trader.data_and_fees.company_facts show SYMBOL [--kind K] [--as-of ISO-UTC] [--db PATH]

Exit status 0 when the run ended; 2 when refused (no key, a bad symbol, the store held by another run), with one line that
says what to do.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

__all__ = ["BASE_URL", "USER_AGENT", "KINDS", "EARNINGS_CALENDAR", "HttpError", "Refused", "Result", "collect", "as_of",
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
CALENDAR_PATH = "calendar/earnings"
CALENDAR_DAYS = 90
NEWS_FIRST_DAYS = 30
NEWS_OVERLAP_DAYS = 2

MAX_REQUESTS_PER_WINDOW = 55
WINDOW_SECONDS = 60.0
WINDOW_MARGIN = 0.1                              # a request counts against the window slightly longer than 60 seconds
MAX_PACE_SLEEPS = 5
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
_SQLITE_HEADER = b"SQLite format 3\x00"
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
    failures: list = field(default_factory=list)

    def summary(self) -> str:
        return ("company_facts: %d calls, %d stored, %d unchanged, %d failed; refused kinds: %s; stopped: %s"
                % (self.calls, self.stored, self.unchanged, self.failed, ",".join(self.kinds_refused) or "none",
                   self.stopped or "no"))


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


def http_fetch(url: str, headers: dict, *, opener: urllib.request.OpenerDirector | None = None,
               timeout: float = TIMEOUT_SECONDS, deadline: float = DEADLINE_SECONDS,
               clock: Callable[[], float] = time.monotonic) -> bytes:
    """GET `url` (under BASE_URL, else ValueError before any request) and return the body, at most MAX_BODY_BYTES.
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
                return _read_capped(resp, MAX_BODY_BYTES, deadline_at, clock)
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


def _check_columns(conn: sqlite3.Connection) -> None:
    for table, cols in _COLUMNS.items():
        if [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)] != cols:
            raise Refused("the file is not a company-facts store (table %s has other columns); nothing was done" % table)


def _take_lock(path: Path) -> tuple[int, bool]:
    """The exclusive writer lock of the store: a file beside it (`<store>.lock`, never a link) held until closed."""
    lp = Path(str(path) + LOCK_SUFFIX)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    made = False
    try:
        try:
            fd = os.open(lp, os.O_RDWR | os.O_CREAT | os.O_EXCL | nofollow, 0o600)
            made = True
        except FileExistsError:
            fd = os.open(lp, os.O_RDWR | nofollow, 0o600)
    except OSError as e:
        raise Refused("%s: the lock file cannot be used (%s); nothing was done" % (lp, type(e).__name__)) from None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise Refused("%s: another collector holds the store lock; nothing was done" % lp) from None
    return fd, made


class _Writer:
    """The store opened for writing by the one collector that holds its lock."""
    def __init__(self, path: Any):
        if not isinstance(path, (str, os.PathLike)):
            raise Refused("the database must be a path")
        self.path = Path(path)
        try:
            if self.path.exists() and self.path.stat().st_size:
                with open(self.path, "rb") as f:
                    foreign = f.read(16) != _SQLITE_HEADER
                if foreign:
                    raise Refused("%s is not a company-facts store; nothing was done" % self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise Refused("%s cannot be used (%s); nothing was done" % (self.path, type(e).__name__)) from None
        self.fd, made = _take_lock(self.path)
        self.conn: sqlite3.Connection | None = None
        try:
            self.conn = sqlite3.connect(self.path, isolation_level=None, timeout=5.0)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            create_schema(self.conn)
            _check_columns(self.conn)
        except sqlite3.Error:
            self.close()
            if made:
                with contextlib.suppress(OSError):
                    os.unlink(str(self.path) + LOCK_SUFFIX)
            raise Refused("%s is not a company-facts store; nothing was done" % self.path) from None
        except Refused:
            self.close()
            raise

    def close(self) -> None:
        if self.conn is not None:
            with contextlib.suppress(sqlite3.Error):
                self.conn.close()
            self.conn = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def highwater(self) -> float:
        """The latest fetch time anywhere in the store (0 when it is empty)."""
        row = self.conn.execute("SELECT max(a, b) FROM (SELECT coalesce(max(fetched_at), 0) a FROM observations),"
                                " (SELECT coalesce(max(fetched_at), 0) b FROM news)").fetchone()
        return float(row[0])


@contextlib.contextmanager
def _reader(db: Any):
    """A read-only view of the store: a connection is used as given, a path is opened read only (and never made)."""
    if isinstance(db, sqlite3.Connection):
        yield db
        return
    if not isinstance(db, (str, os.PathLike)):
        raise Refused("the database must be a path")
    path = Path(db)
    if not path.is_file():
        raise Refused("no store at %s; run collect first" % path)
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(str(path.resolve())), uri=True, timeout=5.0)
    except sqlite3.Error:
        raise Refused("%s cannot be opened as a company-facts store" % path) from None
    try:
        yield conn
    except sqlite3.Error:
        raise Refused("%s is not a readable company-facts store" % path) from None
    finally:
        conn.close()


# ---- the readers: no look-ahead

def _check_when(when: Any) -> float:
    w = _number(when)
    if w is None:
        raise ValueError("`when` must be a finite number of seconds since the epoch")
    return w


def as_of(db: Any, symbol: str, kind: str, when: float) -> dict | None:
    """The newest observation of (symbol, kind) with fetched_at <= `when`, as {symbol, kind, fetched_at, body_sha256, body}
    (body parsed), or None. Nothing fetched after `when` is ever looked at. `db` is a path or an open connection."""
    if kind not in STORED_KINDS:
        raise ValueError("kind must be one of %s (news is read with news_as_of)" % ", ".join(STORED_KINDS))
    if (symbol != "*") if kind == EARNINGS_CALENDAR else not valid_symbol(symbol):
        raise ValueError("bad symbol for this kind")
    when = _check_when(when)
    with _reader(db) as conn:
        row = conn.execute("SELECT fetched_at, body_sha256, body FROM observations WHERE symbol=? AND kind=? AND fetched_at<=?"
                           " ORDER BY fetched_at DESC, rowid DESC LIMIT 1", (symbol, kind, when)).fetchone()
    if row is None:
        return None
    fetched_at, sha, text = row
    try:
        if body_sha256(text) != sha:
            raise ValueError("hash")
        parsed = json.loads(text)
    except ValueError:
        raise Refused("a stored %s row for %s does not match its hash; the store is damaged" % (kind, symbol)) from None
    return {"symbol": symbol, "kind": kind, "fetched_at": fetched_at, "body_sha256": sha, "body": parsed}


def news_as_of(db: Any, symbol: str, when: float, since: float | None = None) -> list[dict]:
    """Articles of `symbol` published AND fetched at or before `when` (and published at or after `since`, if given), oldest
    first."""
    if not valid_symbol(symbol):
        raise ValueError("bad symbol")
    when = _check_when(when)
    sql = ("SELECT symbol, article_id, published_at, fetched_at, headline, source, url, summary FROM news"
           " WHERE symbol=? AND published_at<=? AND fetched_at<=?")
    args: list = [symbol, when, when]
    if since is not None:
        sql += " AND published_at>=?"
        args.append(_check_when(since))
    sql += " ORDER BY published_at, fetched_at, article_id"
    keys = _COLUMNS["news"]
    with _reader(db) as conn:
        return [dict(zip(keys, r)) for r in conn.execute(sql, args)]


def status(db: Any) -> dict:
    """Rows per kind, symbols covered, the last run's counts, the oldest and newest fetch."""
    with _reader(db) as conn:
        rows = {k: 0 for k in STORED_KINDS + ("news",)}
        for kind, n in conn.execute("SELECT kind, count(*) FROM observations GROUP BY kind"):
            rows[kind] = n
        rows["news"] = conn.execute("SELECT count(*) FROM news").fetchone()[0]
        symbols = conn.execute("SELECT count(*) FROM (SELECT symbol FROM observations WHERE symbol<>'*'"
                               " UNION SELECT symbol FROM news)").fetchone()[0]
        last = conn.execute("SELECT started_at, ended_at, calls, stored, unchanged, failed, kinds_refused FROM runs"
                            " ORDER BY rowid DESC LIMIT 1").fetchone()
        span = conn.execute("SELECT min(lo), max(hi) FROM (SELECT min(fetched_at) lo, max(fetched_at) hi FROM observations"
                            " UNION ALL SELECT min(fetched_at), max(fetched_at) FROM news)").fetchone()
    return {"rows": rows, "symbols": symbols, "oldest": span[0], "newest": span[1],
            "last_run": dict(zip(_COLUMNS["runs"], last)) if last else None}


# ---- the collector

class _Pacer:
    """At most MAX_REQUESTS_PER_WINDOW request starts in any WINDOW_SECONDS, read off the injected clock."""
    def __init__(self, clock: Callable[[], float], sleep: Callable[[float], Any]):
        self.clock, self.sleep, self.sent = clock, sleep, deque()

    def wait(self) -> None:
        for _ in range(MAX_PACE_SLEEPS):
            now = self.clock()
            self.sent = deque(min(t, now) for t in self.sent)            # a clock that stepped back: those were "just now"
            while self.sent and now - self.sent[0] >= WINDOW_SECONDS + WINDOW_MARGIN / 2:     # (the wait below overshoots it)
                self.sent.popleft()
            if len(self.sent) < MAX_REQUESTS_PER_WINDOW:
                self.sent.append(now)
                return
            self.sleep(max(0.0, self.sent[0] + WINDOW_SECONDS + WINDOW_MARGIN - now))
        raise _Stop("clock does not advance")


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


def _no_repeated_keys(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("repeated key")
        out[k] = v
    return out


def _no_constant(name):
    raise ValueError("not JSON")


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


class _Run:
    def __init__(self, store: _Writer, key: str, fetch, clock, sleep, max_calls: int):
        self.store, self.fetch, self.clock, self.sleep, self.max_calls = store, fetch, clock, sleep, max_calls
        self.headers = {KEY_HEADER: key, "User-Agent": USER_AGENT, "Accept": "application/json"}
        self.key_bytes = [key.encode(), key[:len(key) // 2].encode(), key[len(key) // 2:].encode()]
        self.pacer = _Pacer(clock, sleep)
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
        if any(k in body for k in self.key_bytes):
            raise _Bad("key in answer")
        try:
            value = json.loads(body.decode("utf-8"), object_pairs_hook=_no_repeated_keys, parse_constant=_no_constant)
            canon = canonical_json(value)
        except (ValueError, RecursionError):
            raise _Bad("not JSON or a repeated key") from None
        if any(k in canon.encode("utf-8") for k in self.key_bytes):
            raise _Bad("key in answer")
        return value, canon

    def _store(self, kind: str, symbol: str, value: Any, canon: str) -> None:
        if kind == "news" and not isinstance(value, list):
            self._fail(kind, "not a list")
            return
        at = self.clock()                                       # after the answer arrived: never earlier than the fact
        if at < self.highwater:
            self._fail(kind, "clock behind the store")
            return
        conn = self.store.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            if kind == "news":
                new = sum(self._insert_article(conn, symbol, a, at) for a in value)
                self.result.stored += new
                self.result.unchanged += 0 if new else 1
            else:
                sha = body_sha256(canon)
                row = conn.execute("SELECT body_sha256 FROM observations WHERE symbol=? AND kind=?"
                                   " ORDER BY fetched_at DESC, rowid DESC LIMIT 1", (symbol, kind)).fetchone()
                if row is not None and row[0] == sha:
                    self.result.unchanged += 1
                else:
                    conn.execute("INSERT INTO observations VALUES (?,?,?,?,?)", (symbol, kind, at, sha, canon))
                    self.result.stored += 1
            conn.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
        self.highwater = max(self.highwater, at)

    @staticmethod
    def _insert_article(conn, symbol: str, a: Any, at: float) -> int:
        """1 if the article was new. An article without a usable id or time is skipped, never filled in."""
        if not isinstance(a, dict):
            return 0
        aid, published = a.get("id"), _number(a.get("datetime"))
        if isinstance(aid, bool) or not isinstance(aid, (int, str)) or aid == "" or published is None or published <= 0:
            return 0

        def text(k):
            return a[k] if isinstance(a.get(k), str) else None
        cur = conn.execute("INSERT OR IGNORE INTO news VALUES (?,?,?,?,?,?,?,?)",
                           (symbol, str(aid), published, at, text("headline"), text("source"), text("url"), text("summary")))
        return cur.rowcount

    def _one(self, kind: str, symbol: str, url: str) -> None:
        body = self._ask(kind, url)
        if body is None:
            return
        try:
            value, canon = self._decode(body)
        except _Bad as e:
            self._fail(kind, e.reason)
            return
        self._store(kind, symbol, value, canon)

    @staticmethod
    def _url(path: str, query: dict) -> str:
        return BASE_URL + path + "?" + "&".join("%s=%s" % (k, v) for k, v in query.items())

    def _news_url(self, symbol: str) -> str:
        now = self.clock()
        newest = self.store.conn.execute("SELECT max(published_at) FROM news WHERE symbol=?", (symbol,)).fetchone()[0]
        today = _day(now)
        start = _day(now - NEWS_FIRST_DAYS * 86400) if newest is None else min(_day(newest - NEWS_OVERLAP_DAYS * 86400), today)
        return self._url("company-news", {"symbol": symbol, "from": start, "to": today})

    def go(self, symbols: list[str]) -> Result:
        conn = self.store.conn
        if self.result.started_at < self.highwater:
            raise Refused("the clock reads earlier than the newest fetch already in the store; nothing was asked")
        conn.execute("INSERT INTO runs VALUES (?, NULL, 0, 0, 0, 0, '')", (self.result.started_at,))
        run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        try:
            try:
                now = self.clock()
                if EARNINGS_CALENDAR not in self.refused:
                    self._one(EARNINGS_CALENDAR, "*", self._url(CALENDAR_PATH, {"from": _day(now), "to": _day(now + CALENDAR_DAYS * 86400)}))
                for symbol in symbols:
                    for kind in KINDS:
                        if kind in self.refused:
                            continue
                        if kind == "news":
                            url = self._news_url(symbol)
                        else:
                            path, fixed = _REQUESTS[kind]
                            url = self._url(path, dict({"symbol": symbol}, **fixed))
                        self._one(kind, symbol, url)
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
            max_calls: int = DEFAULT_MAX_CALLS) -> Result:
    """Ask Finnhub for each symbol's profile, metrics, earnings, recommendations, insiders and news, and once for the
    earnings calendar, and store what is new. Refused (Refused, before any request) for a bad symbol, a bad `max_calls`, a
    missing key, a store that is not ours, one that another run holds, or a clock earlier than the store's newest fetch.
    `fetch(url, headers)` returns the body bytes or raises HttpError (or another error); `credentials(name)` returns the key."""
    symbols = _check_symbols(symbols)
    max_calls = _check_max_calls(max_calls)
    key = _read_key(credentials)
    store = _Writer(db)
    try:
        return _Run(store, key, fetch, clock, sleep, max_calls).go(symbols)
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


def _show_news(articles: list[dict]) -> list[dict]:
    return [dict(a, published_at=_iso(a["published_at"]), fetched_at=_iso(a["fetched_at"])) for a in articles]


def _status_text(db: Any) -> str:
    st = status(db)
    last = st["last_run"]
    lines = ["company_facts store: %s" % db,
             "rows: " + ", ".join("%s %d" % (k, n) for k, n in st["rows"].items()),
             "symbols covered: %d" % st["symbols"],
             "last run: none" if last is None else
             "last run: started %s, ended %s, %d calls, %d stored, %d unchanged, %d failed, refused kinds: %s" % (
                 _iso(last["started_at"]), _iso(last["ended_at"]) if last["ended_at"] is not None else "never",
                 last["calls"], last["stored"], last["unchanged"], last["failed"], last["kinds_refused"] or "none"),
             "oldest fetch: " + (_iso(st["oldest"]) if st["oldest"] is not None else "none"),
             "newest fetch: " + (_iso(st["newest"]) if st["newest"] is not None else "none")]
    return "\n".join(lines)


def _show(args, clock) -> str:
    symbol, kind = args.symbol, args.kind
    if kind is not None and kind not in KINDS + (EARNINGS_CALENDAR,):
        raise Refused("--kind must be one of %s" % ", ".join(KINDS + (EARNINGS_CALENDAR,)))
    when = _parse_when(args.as_of) if args.as_of is not None else clock()
    try:
        if kind == "news":
            out: Any = _show_news(news_as_of(args.db, symbol, when))
        elif kind is not None:
            out = _show_fact(as_of(args.db, symbol, kind, when))
        elif symbol == "*":
            out = {EARNINGS_CALENDAR: _show_fact(as_of(args.db, symbol, EARNINGS_CALENDAR, when))}
        else:
            out = {k: _show_fact(as_of(args.db, symbol, k, when)) for k in KINDS if k != "news"}
            out["news"] = _show_news(news_as_of(args.db, symbol, when))
    except ValueError as e:
        raise Refused(_one_line(e)) from None
    return json.dumps(out, indent=2)


def _load_secrets_file() -> None:
    """Load the owner's secrets file into the environment (the way the project's other commands do)."""
    from valedia_trader.core import secrets
    try:
        secrets.load_env()
    except secrets.SecretError as e:
        raise Refused(_one_line(e)) from None


def main(argv=None, *, fetch=None, clock: Callable[[], float] = time.time, sleep: Callable[[float], Any] = time.sleep,
         credentials: Callable[[str], Any] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m valedia_trader.data_and_fees.company_facts",
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
    args = parser.parse_args(argv)
    try:
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
                             max_calls=max_calls)
            print(result.summary())
            return 2 if result.stopped == "key rejected" else 0
        if args.command == "status":
            print(_status_text(args.db))
            return 0
        print(_show(args, clock))
        return 0
    except Refused as e:
        print("company_facts: refused: %s" % _one_line(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
