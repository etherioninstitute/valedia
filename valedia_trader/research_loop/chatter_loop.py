"""The chatter collector: the newest comments of r/sportsbook and Kalshi's game prices, minute by minute, in a store
of their own (design: docs/specs/2026-11-13-chatter-collector-design.md; the test it serves:
docs/research/2026-11-13-chatter-prediction.md). It collects; matching comments to teams and every scoring rule
are somebody else's job. No model, no browser, no login.

    python -m valedia_trader.research_loop.chatter_loop run    --db FILE [--for-minutes N]
    python -m valedia_trader.research_loop.chatter_loop status --db FILE
    python -m valedia_trader.research_loop.chatter_loop check  --db FILE      (exit 0 if the store keeps its word, else 1)

Two recorders on one clock, one thread, every step bounded by a 10 second deadline so that neither can cost the
other more than that:

* Comments. Reddit by its front door only. Without credentials: an HTTPS GET of the public Atom feed of the newest
  comments on www.reddit.com, one request every 120 seconds, never more than one in any 60, none from minute 5 to
  minute 12 of each hour (the hourly pick reader's window). With both REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in
  the environment: the official interface on oauth.reddit.com with an application-only token (a read-only grant
  that needs no login of any person), every 60 seconds, at most 30 requests in any 60, paging back with the
  documented cursor while the newest stored comment is not on the page (at most 5 pages), the rate-limit headers
  obeyed. A 429 or a 403 is a back-off for the time the answer names (seconds or an HTTP date, honoured up to an
  hour), else 5 minutes, recorded, never retried inside. No redirect is followed. No cookie is sent or kept; no
  proxy is used.
* Prices. Kalshi's public interface, keyless, through this module's own bounded transport (the package reader in
  `kalshi_data` follows proxies and redirects and reads answers without a limit, so only its constants and its
  error class are used): for each of the five game series once every 60 seconds, the open markets with their best
  yes bid and ask, last price, volume and open interest. One row per market whenever bid, ask or last changed, one
  row per fetch. The same series is never asked twice for the same minute, a restart included.

The limits hold across any kill. Before any request leaves, a row saying it is about to be sent is committed
(`requests`: service, purpose, slot, time); on start the request history the limits use (the public floor, the
official burst, page counts, Kalshi's minutes, back-offs) is rebuilt from those rows and from the answers stored,
and a request whose answer never came gets an outcome row "unknown", appended. Every request has a whole-answer
deadline (connection, headers and body together) after which its socket is shut: no request thread outlives it.
TLS trusts one certificate bundle, a path fixed here (the system's /etc/ssl/cert.pem, else certifi's; with neither
the run does not start), certificate and host name always verified; no environment variable changes it. A body
shorter or longer than its Content-Length, one cut off, or a Content-Length that is not one plain number makes a
200 a bad answer; the status and headers are never lost to it, so a 429 or 403 backs off whatever its body.

Coverage is Clarification 3 for whole UTC days: from the day of the first run on, every day's slots are expected
(7,200 price slots; 624 public or 1,440 official comment slots), time nobody was recording uncovered, and a day
not yet ended never counts. The body of a bad answer (unreadable, refused, over the limit, failed) is not stored,
only its hash, size, status and reason, so bad answers cannot grow the store faster than good ones.

Text written by Reddit users is untrusted data. It is stored exactly as received (a good raw answer by hash, zlib-compressed, the text
per version) and is never executed, passed to a shell, a template, a format string or a model, and never printed:
`run`, `status` and `check` print counts, ids and times only, and no failure text carries any of it. Nothing from
the trading secrets file is loaded, read or sent (this module has no code that loads any secrets file), and it starts no child process.
It writes only its store, the store's side files and its lock, and its own log lines to standard output.
"""
from __future__ import annotations

import argparse
import base64
import bisect
import email.utils
import fcntl
import hashlib
import http.client
import json
import math
import os
import re
import signal
import socket
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
import xml.parsers.expat
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from valedia_trader.research_loop import REPO
from valedia_trader.venues import kalshi_data as kd

UTC = timezone.utc
DEFAULT_DB = REPO / "memory" / "chatter.sqlite"

# ------------------------------------------------------------------------------------------------ what it asks
SUBREDDIT = "sportsbook"
USER_AGENT = "comment-and-price-collector/1.0"   # what the program does, nothing else: no name of a person, project,
#                                                  business, site, account or address, no system or Python version
PUBLIC_HOST = "www.reddit.com"
OAUTH_HOST = "oauth.reddit.com"
PUBLIC_URL = "https://%s/r/%s/comments/.rss?limit=100" % (PUBLIC_HOST, SUBREDDIT)
OAUTH_URL = "https://%s/r/%s/comments?limit=100&raw_json=1" % (OAUTH_HOST, SUBREDDIT)
TOKEN_URL = "https://%s/api/v1/access_token" % PUBLIC_HOST
# the five game series of sports_lines.LEAGUES (a test compares): football, basketball, hockey, college football, college men's basketball
SERIES = ("KXNFLGAME", "KXNBAGAME", "KXNHLGAME", "KXNCAAFGAME", "KXNCAAMBGAME")

PUBLIC_INTERVAL_S = 120           # the public feed: one request every two minutes
OAUTH_INTERVAL_S = 60             # the official interface: one every minute
PRICE_INTERVAL_S = 60             # each game series once a minute
PUBLIC_MIN_GAP_S = 60.0           # never more than one public request in any 60 seconds
SILENT_FROM_MINUTE = 5            # the hourly pick reader's window: minute 5 ...
SILENT_TO_MINUTE = 12             # ... to minute 12 inclusive of every hour, no public request
OAUTH_BURST = 30                  # never more than this many official requests in any 60 seconds
OAUTH_MAX_PAGES = 5               # paging back, at most this many pages for one slot
BACKOFF_DEFAULT_S = 300.0         # a 429 or 403 that names no time: five minutes (the module's own choice) ...
BACKOFF_MAX_S = 600.0             # ... and a wait of the module's own choosing is at most ten minutes
BACKOFF_NAMED_MAX_S = 3600.0      # a wait the service names (Retry-After, a reset header) is honoured up to an hour
BACKOFF_MIN_S = 1.0
STEP_DEADLINE_S = 10.0            # every step (a Reddit slot, a price pass) and every request within it ends by then
MAX_BODY = 4 * 1024 * 1024        # an answer is read up to this many bytes
MAX_STORED_BLOB = MAX_BODY + MAX_BODY // 100 + 4096   # no stored compressed answer can be longer (zlib's worst case)
MAX_ABANDONED = 4                 # with this many timed-out requests of one service still running, none of it is started
KALSHI_MAX_PAGES = 10             # cursor pages of one series in one minute, at most
KALSHI_SPACING_S = 1.0 / 3.0 + 0.01   # never more than three Kalshi requests in a second
INTENT_MARGIN_S = 1.0             # a request known only by its row is taken as sent this much after the row's time
KALSHI_INTENT_MARGIN_S = 0.05     # a Kalshi request known only by its row is taken as sent this much after the row's time
CURSOR = re.compile(r"[A-Za-z0-9_]{1,64}")    # the only official-interface cursor accepted
TOKEN = re.compile(r"[\x21-\x7e]{1,4096}")    # the only token accepted: visible ASCII, no space, at most 4,096 long
CONTENT_LENGTH = re.compile(r"[0-9]{1,10}")    # a Content-Length is a plain number ...
SYSTEM_BUNDLE = "/etc/ssl/cert.pem"   # the one trust store (else certifi's bundle): never one the environment names
SKIP_AFTER_FRACTION = 0.5         # a slot more than half over is skipped
TOKEN_REFRESH_EARLY_S = 300.0
MAX_SKIP_ROWS = 20                # skipped slots of one stall are recorded as at most this many rows (the rest as a count)
COVERAGE_MIN = 0.8                # a day counts if a recorder covered at least this share of it
CLOCK_BACK_TOLERANCE_S = 1.0       # a clock reading this much earlier than an earlier reading stops the run
HEARTBEAT_S = 3600.0              # a line of counts on standard output this often

# ------------------------------------------------------------------------------------------------ the store
TABLES = ("runs", "payloads", "comments", "comment_versions", "comment_fetches", "comment_events", "price_fetches",
          "prices", "price_events", "requests")
LAYOUT = 4                        # PRAGMA user_version (2: raw answers kept zlib-compressed; 3: a row before every request;
                                  # 4: a bad answer's hash and size in place of its body)
APP_ID = 0x43484154               # PRAGMA application_id: "CHAT"
LOCK_SUFFIX = ".lock"

_SCHEMA = """
CREATE TABLE runs (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, backend TEXT NOT NULL,
    comment_interval_s INTEGER NOT NULL, price_interval_s INTEGER NOT NULL, program TEXT NOT NULL);
CREATE TABLE payloads (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, sha256 TEXT NOT NULL, first_seen_at TEXT NOT NULL,
    size INTEGER NOT NULL, body BLOB NOT NULL, UNIQUE (kind, sha256));   -- sha256 and size are of the bytes received; body is those bytes, zlib-compressed
CREATE TABLE comments (comment_id TEXT PRIMARY KEY, thread_id TEXT, permalink TEXT, first_seen TEXT NOT NULL,
    source_time TEXT, source_kind TEXT, first_fetch_id INTEGER NOT NULL);
CREATE TABLE comment_versions (id INTEGER PRIMARY KEY, comment_id TEXT NOT NULL, version INTEGER NOT NULL,
    author TEXT, author_enc TEXT NOT NULL, body TEXT NOT NULL, body_enc TEXT NOT NULL, sig TEXT NOT NULL,
    seen_at TEXT NOT NULL, source_time TEXT, source_raw TEXT, source_kind TEXT, edited TEXT, fetch_id INTEGER NOT NULL,
    change TEXT NOT NULL CHECK (change IN ('first', 'edit')), UNIQUE (comment_id, version));
CREATE TABLE comment_fetches (id INTEGER PRIMARY KEY, fetched_at TEXT NOT NULL, slot_ts INTEGER NOT NULL,
    backend TEXT NOT NULL, purpose TEXT NOT NULL CHECK (purpose IN ('feed', 'token')), page INTEGER NOT NULL,
    status INTEGER, ok INTEGER NOT NULL, failure TEXT, payload_id INTEGER, comments_in_feed INTEGER,
    new_comments INTEGER NOT NULL DEFAULT 0, new_versions INTEGER NOT NULL DEFAULT 0, bad_entries INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0, rate_remaining REAL, rate_reset REAL, request_id INTEGER, body_sha256 TEXT,
    body_size INTEGER);   -- body_sha256 and body_size: of the answer received, kept or not (a bad answer's body is not kept)
CREATE TABLE comment_events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, slot_ts INTEGER, until TEXT,
    count INTEGER, detail TEXT);
CREATE TABLE price_fetches (id INTEGER PRIMARY KEY, fetched_at TEXT NOT NULL, slot_ts INTEGER NOT NULL, series TEXT NOT NULL,
    ok INTEGER NOT NULL, failure TEXT, markets_seen INTEGER NOT NULL DEFAULT 0, rows_stored INTEGER NOT NULL DEFAULT 0,
    bad_rows INTEGER NOT NULL DEFAULT 0, payload_id INTEGER, request_id INTEGER, status INTEGER, body_sha256 TEXT,
    body_size INTEGER);
CREATE TABLE prices (id INTEGER PRIMARY KEY, fetch_id INTEGER NOT NULL, seen_at TEXT NOT NULL, series TEXT NOT NULL,
    ticker TEXT NOT NULL, event_ticker TEXT, yes_bid REAL, yes_ask REAL, last_price REAL, volume REAL, open_interest REAL,
    yes_bid_size REAL, yes_ask_size REAL, status TEXT, note TEXT);
CREATE TABLE price_events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, series TEXT, slot_ts INTEGER,
    until TEXT, count INTEGER, detail TEXT);
CREATE TABLE requests (id INTEGER PRIMARY KEY, at TEXT NOT NULL, service TEXT NOT NULL CHECK (service IN ('reddit', 'kalshi')),
    backend TEXT NOT NULL, purpose TEXT NOT NULL CHECK (purpose IN ('feed', 'token', 'prices')), slot_ts INTEGER NOT NULL,
    page INTEGER NOT NULL, series TEXT);   -- committed before the request leaves; its outcome is the fetch row naming it
CREATE INDEX comments_by_time ON comments (source_time);
CREATE INDEX requests_by_kind ON requests (service, backend, purpose, at);
CREATE INDEX requests_by_slot ON requests (service, slot_ts);
CREATE INDEX comment_fetches_by_request ON comment_fetches (request_id);
CREATE INDEX versions_by_seen ON comment_versions (seen_at);
CREATE INDEX comment_fetches_by_slot ON comment_fetches (slot_ts);
CREATE INDEX price_fetches_by_slot ON price_fetches (series, slot_ts);
CREATE INDEX prices_by_ticker ON prices (ticker, id);
CREATE INDEX versions_by_fetch ON comment_versions (fetch_id);
CREATE INDEX comments_by_fetch ON comments (first_fetch_id);
CREATE INDEX prices_by_fetch ON prices (fetch_id);
"""

# the natural keys of each table: an INSERT that repeats one is refused, so INSERT OR REPLACE cannot rewrite history
_KEYS = {
    "runs": [("id",)], "payloads": [("id",), ("kind", "sha256")], "comments": [("comment_id",)],
    "comment_versions": [("id",), ("comment_id", "version")], "comment_fetches": [("id",)], "comment_events": [("id",)],
    "price_fetches": [("id",)], "prices": [("id",)], "price_events": [("id",)], "requests": [("id",)],
}
# every time this program writes from its own clock: no row may carry a time earlier than the last row of its table
_TIMES = (("runs", "started_at"), ("payloads", "first_seen_at"), ("comments", "first_seen"), ("comment_versions", "seen_at"),
          ("comment_fetches", "fetched_at"), ("comment_events", "at"), ("price_fetches", "fetched_at"),
          ("prices", "seen_at"), ("price_events", "at"), ("requests", "at"))


def _layout_sql() -> str:
    """The whole layout, tables, keys and triggers, as one transaction."""
    parts = ["BEGIN IMMEDIATE;", "PRAGMA application_id = %d;" % APP_ID, "PRAGMA user_version = %d;" % LAYOUT, _SCHEMA]
    for t in TABLES:
        for verb in ("UPDATE", "DELETE"):
            parts.append("CREATE TRIGGER no_%s_%s BEFORE %s ON %s BEGIN SELECT RAISE(ABORT, 'append-only record: no %s on %s'); END;"
                         % (verb.lower(), t, verb, t, verb, t))
        clauses = []
        for key in _KEYS[t]:
            cond = " AND ".join("%s = NEW.%s" % (c, c) for c in key)
            clauses.append("(NEW.%s IS NOT NULL AND EXISTS (SELECT 1 FROM %s WHERE %s))" % (key[0], t, cond))
        parts.append("CREATE TRIGGER no_replace_%s BEFORE INSERT ON %s WHEN %s BEGIN "
                     "SELECT RAISE(ABORT, 'append-only record: %s already has this key'); END;" % (t, t, " OR ".join(clauses), t))
    for t, col in _TIMES:
        parts.append("CREATE TRIGGER time_order_%s BEFORE INSERT ON %s WHEN NEW.%s < "
                     "(SELECT %s FROM %s ORDER BY rowid DESC LIMIT 1) BEGIN "
                     "SELECT RAISE(ABORT, 'append-only record: %s.%s earlier than the last row'); END;" % (t, t, col, col, t, t, col))
    parts.append("COMMIT;")
    return "\n".join(parts)


_EXPECTED: set | None = None


def _expected_layout() -> set:
    """Every table, key, index and trigger of the layout, as SQLite keeps them, from a layout made in memory."""
    global _EXPECTED
    if _EXPECTED is None:
        c = sqlite3.connect(":memory:", isolation_level=None)
        c.executescript(_layout_sql())
        _EXPECTED = set(c.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"))
        c.close()
    return _EXPECTED


class StoreError(Exception):
    """The file is not a chatter store of the current layout (or there is none, or it is broken inside)."""


class ClockBehind(ValueError):
    """The clock reads earlier than the latest time the store already holds: nothing is written."""


_STORE_ERRORS = (sqlite3.Error, StoreError, ClockBehind)    # errors of the store itself: these still stop the run


def iso(ts: float) -> str:
    """A fixed-width UTC time to the nearest millisecond, so that text order is time order."""
    ms = round(ts * 1000)
    return datetime.fromtimestamp(ms // 1000, UTC).strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (ms % 1000)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _pack(text: str) -> tuple[str, str]:
    """Text as stored: itself when it can be encoded, else base64 of its code points (surrogates and all), which
    `_unpack` reads back to exactly the same code points."""
    try:
        text.encode("utf-8")
        return text, "utf8"
    except UnicodeEncodeError:
        return base64.b64encode(text.encode("utf-8", "surrogatepass")).decode("ascii"), "b64"


def _unpack(value: str, enc: str) -> str:
    return base64.b64decode(value).decode("utf-8", "surrogatepass") if enc == "b64" else value


def _s(x):
    """Any other text column: a character that cannot be encoded becomes ? (only comment text, the author and raw
    answers are kept to the code point)."""
    return x.encode("utf-8", "replace").decode("utf-8") if isinstance(x, str) else x


def _deflate(data: bytes) -> bytes:
    """A raw answer as stored: exactly the bytes received, zlib-compressed."""
    return zlib.compress(data, 6)


def _inflate(blob, size: int) -> bytes:
    """A stored blob back to the bytes received. It must decompress, to exactly `size` bytes, and end cleanly. No
    answer is ever kept longer than MAX_BODY, so a row that states more is damage, and inflation always stops at
    MAX_BODY + 1 bytes whatever the row states: a decompression bomb cannot fill memory. Raises StoreError."""
    if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= MAX_BODY:
        raise StoreError("a stored raw answer does not decompress to its stated size")
    if not isinstance(blob, (bytes, bytearray, memoryview)) or len(blob) > MAX_STORED_BLOB:
        raise StoreError("a stored raw answer does not decompress to its stated size")
    d = zlib.decompressobj()
    try:
        out = d.decompress(bytes(blob), min(size, MAX_BODY) + 1)
    except zlib.error:
        raise StoreError("a stored raw answer does not decompress") from None
    if len(out) != size or not d.eof or d.unused_data or d.unconsumed_tail:
        raise StoreError("a stored raw answer does not decompress to its stated size")
    return out


def version_sig(author: str | None, body: str) -> str:
    """What makes two versions the same: the author and the text, nothing else."""
    return sha256_text(json.dumps([author, body], ensure_ascii=True))


_MAGIC = b"SQLite format 3\x00"


def _check_header(path: Path) -> None:
    """Read the file's first 100 bytes ourselves, before SQLite ever opens it: the SQLite magic, the write-ahead-log
    format, this module's application id and the current layout number. A file that fails is refused (StoreError)
    and never opened, changed or given side files."""
    msg = "%s is not a chatter store of the current layout (layout %d)" % (path, LAYOUT)
    try:
        with open(path, "rb") as f:
            h = f.read(100)
    except OSError:
        raise StoreError(msg) from None
    if len(h) < 100 or h[:16] != _MAGIC or h[18] != 2 or h[19] != 2:
        raise StoreError(msg)
    if int.from_bytes(h[68:72], "big") != APP_ID or int.from_bytes(h[60:64], "big") != LAYOUT:
        raise StoreError(msg)


def _side(path: Path, suffix: str) -> Path:
    return path.with_name(path.name + suffix)


def _create(path: Path) -> None:
    """Make the whole layout in one transaction in a side file, then link it into place: a kill at any point leaves
    either no store or a complete one (and at most the side file, which the next creation replaces)."""
    tmp = _side(path, ".creating")
    for p in (tmp, _side(tmp, "-wal"), _side(tmp, "-shm"), _side(tmp, "-journal")):
        if p.is_file() and not p.is_symlink():
            p.unlink()
    c = sqlite3.connect(str(tmp), isolation_level=None)
    try:
        c.execute("PRAGMA journal_mode = WAL")
        c.executescript(_layout_sql())
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        c.close()
    try:
        os.link(tmp, path)
    except FileExistsError:
        pass                                          # created by another process meanwhile: that one is checked
    except OSError:
        if not path.exists():
            os.replace(tmp, path)
    for p in (tmp, _side(tmp, "-wal"), _side(tmp, "-shm")):
        if p.exists():
            p.unlink()


def _take_lock(path: Path) -> tuple[int, bool]:
    """The exclusive lock of the store at `path`: a file beside it (`<store>.lock`, never a link), held until the
    program ends (a kill releases it). Another writer holding it raises StoreError. Returns the descriptor and
    whether this call made the file."""
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
        raise StoreError("%s: the lock file cannot be used (%s)" % (lp, type(e).__name__)) from None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise StoreError("%s: another collector is using the store; nothing was done" % path) from None
    return fd, made


@dataclass
class Entry:
    """One comment as a feed showed it. `source_time` is the feed's own time in the store's fixed format when it
    could be read (information only: the test uses only the time this program first saw a version), `source_raw`
    what the feed wrote."""
    comment_id: str
    thread_id: str | None
    permalink: str | None
    author: str | None
    body: str
    source_time: str | None
    source_kind: str | None
    source_raw: str | None = None
    edited: str | None = None


@dataclass
class Fetch:
    """One request to Reddit and what came of it. `at` is the program's own clock (epoch seconds), read once, when
    the answer arrived. `failure` is None for a good answer, else a short constant. `entries` is None when the
    answer was not read as a feed."""
    at: float
    slot: int
    backend: str
    purpose: str
    page: int
    status: int | None
    failure: str | None
    body: bytes | None
    entries: list | None
    bad_entries: int = 0
    rate_remaining: float | None = None
    rate_reset: float | None = None
    request_id: int | None = None      # the row committed before the request left (None when nothing was sent)
    body_sha256: str | None = None     # of the answer received, when its body is not kept (a bad answer's is not)
    body_size: int | None = None


@dataclass
class Event:
    """A gap, a silence, a back-off, a skipped slot, a possible miss: `at` is when it was recorded."""
    at: float
    kind: str
    slot: int | None
    until: float | None
    count: int | None
    detail: str | None


@dataclass
class Quote:
    ticker: str
    event_ticker: str | None
    bid: float | None
    ask: float | None
    last: float | None
    volume: float | None
    open_interest: float | None
    bid_size: float | None
    ask_size: float | None
    status: str | None
    note: str | None = None


# ------------------------------------------------------------------------------------------------ parsing
class Unparseable(Exception):
    """An answer that is not a feed of the expected kind. The raw answer is stored and the fetch counted as failed."""


_ATOM = "{http://www.w3.org/2005/Atom}"
_DOCTYPE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)
_DOCTYPE_TEXT = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.I)       # the same, after the bytes are decoded
_DECLARED = re.compile(r"\A<\?xml[^>]*?\bencoding\s*=\s*([\"'])(.*?)\1")
_THREAD_IN_LINK = re.compile(r"/comments/([A-Za-z0-9]+)/")


def _refuse(*_a):
    raise Unparseable("a declaration the feed may not hold")


def _xml_tree(data: bytes):
    """The document as an ElementTree element, parsed by expat with every declaration refused by the parser itself
    (a DOCTYPE, an entity of any kind, a notation, a reference to an external entity), so nothing can be expanded
    whatever got past the text checks. Raises Unparseable."""
    p = xml.parsers.expat.ParserCreate("UTF-8", "}")
    tb = ET.TreeBuilder()

    def name(n):
        return "{" + n if "}" in n else n

    def start(tag, attrs):
        tb.start(name(tag), {name(k): v for k, v in attrs.items()})

    def decl(version, encoding, standalone):
        if encoding is not None and encoding.lower().replace("_", "-") not in ("utf-8", "utf8"):
            raise Unparseable("not UTF-8")
    p.buffer_text = True
    p.ordered_attributes = False
    p.SetParamEntityParsing(xml.parsers.expat.XML_PARAM_ENTITY_PARSING_NEVER)
    p.StartElementHandler = start
    p.EndElementHandler = lambda tag: tb.end(name(tag))
    p.CharacterDataHandler = tb.data
    p.XmlDeclHandler = decl
    for h in ("StartDoctypeDeclHandler", "EntityDeclHandler", "UnparsedEntityDeclHandler", "NotationDeclHandler",
              "ExternalEntityRefHandler", "ElementDeclHandler", "AttlistDeclHandler", "SkippedEntityHandler"):
        setattr(p, h, _refuse)
    try:
        p.Parse(data, True)
        return tb.close()
    except Unparseable:
        raise
    except (xml.parsers.expat.ExpatError, ValueError, RecursionError, MemoryError, AssertionError, TypeError):
        raise Unparseable("not XML") from None


def normalize_time(v) -> str | None:
    """A feed's own time in the store's fixed second-resolution format, or None when it cannot be read as an instant:
    an ISO time with a zone, or Reddit's epoch seconds."""
    try:
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            if not math.isfinite(v):
                return None
            dt = datetime.fromtimestamp(math.floor(v), UTC)
        elif isinstance(v, str):
            dt = datetime.fromisoformat(v.strip())
            if dt.tzinfo is None or dt.utcoffset() is None:
                return None
            dt = dt.astimezone(UTC)
        else:
            return None
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return None


def parse_atom(data: bytes) -> tuple[list, int]:
    """The entries of the public feed (Atom, newest first) and the number of entries that had no id or no text.
    The feed must be UTF-8 (a strict decode; any other encoding, declared or not, and any byte-order mark is a bad
    answer). Entities, DOCTYPE declarations and anything else XML can expand are refused before parsing, on the bytes
    and again on the decoded text, and by the parser itself; the text of each comment is kept exactly as the XML gave
    it. Raises Unparseable."""
    if not isinstance(data, (bytes, bytearray)) or _DOCTYPE.search(data):
        raise Unparseable("not a plain Atom feed")
    data = bytes(data)
    if data.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        raise Unparseable("a byte-order mark")
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise Unparseable("not UTF-8") from None
    m = _DECLARED.match(text)
    if _DOCTYPE_TEXT.search(text) or "\x00" in text or (m and m.group(2).strip().lower() not in ("utf-8", "utf8")):
        raise Unparseable("not a plain UTF-8 Atom feed")
    root = _xml_tree(data)
    if root.tag != _ATOM + "feed":
        raise Unparseable("not an Atom feed")
    entries, bad = [], 0
    for ent in root.findall(_ATOM + "entry"):
        cid = ent.findtext(_ATOM + "id")
        content = ent.find(_ATOM + "content")
        if not cid or not cid.strip() or content is None:
            bad += 1
            continue
        body = content.text or "" if len(content) == 0 else "".join(content.itertext())
        link = ent.find(_ATOM + "link")
        href = link.get("href") if link is not None else None
        m = _THREAD_IN_LINK.search(href or "")
        updated = ent.findtext(_ATOM + "updated")
        author = ent.findtext(_ATOM + "author/" + _ATOM + "name")
        entries.append(Entry(comment_id=cid.strip(), thread_id=("t3_" + m.group(1)) if m else None, permalink=href,
                             author=author, body=body, source_time=normalize_time(updated), source_kind="atom_updated",
                             source_raw=updated, edited=None))
    return entries, bad


def parse_listing(data: bytes) -> tuple[list, int, str | None]:
    """The comments of the official interface's listing (newest first), the number of children that were not
    comments with a text, and the cursor for the next older page. Raises Unparseable."""
    try:
        doc = json.loads(bytes(data).decode("utf-8"))
    except (ValueError, RecursionError, MemoryError):
        raise Unparseable("not JSON") from None
    body = doc.get("data") if isinstance(doc, dict) else None
    kids = body.get("children") if isinstance(body, dict) else None
    if not isinstance(kids, list):
        raise Unparseable("not a listing")
    entries, bad = [], 0
    for k in kids:
        d = k.get("data") if isinstance(k, dict) and k.get("kind") == "t1" else None
        if not isinstance(d, dict) or not isinstance(d.get("body"), str):
            bad += 1
            continue
        name = d.get("name") if isinstance(d.get("name"), str) and d.get("name") else (
            "t1_" + d["id"] if isinstance(d.get("id"), str) and d["id"] else None)
        if not name:
            bad += 1
            continue
        link = d.get("permalink")
        created = d.get("created_utc")
        edited = d.get("edited")
        entries.append(Entry(
            comment_id=name, thread_id=d.get("link_id") if isinstance(d.get("link_id"), str) else None,
            permalink=("https://www.reddit.com" + link) if isinstance(link, str) and link.startswith("/") else None,
            author=d.get("author") if isinstance(d.get("author"), str) else None, body=d["body"],
            source_time=normalize_time(created), source_kind="created_utc",
            source_raw=None if created is None else repr(created),
            edited=None if edited is None else ("false" if edited is False else ("true" if edited is True else repr(edited)))))
    after = body.get("after")
    if after is None or after == "":
        return entries, bad, None
    if not isinstance(after, str) or not CURSOR.fullmatch(after):
        raise Unparseable("a cursor that is not a plain name")      # ASCII letters, digits, underscore, 1 to 64 of them
    return entries, bad, after


class Store:
    """One sqlite file in write-ahead-log mode, insert-only: a trigger on every table refuses UPDATE, DELETE, an
    INSERT that repeats an existing key (which is what INSERT OR REPLACE does to history) and a time earlier than
    the last row's. A comment whose author or text changed gets a new version row; the older rows stay as they were.
    The file's header is read before SQLite opens it; a file that is not a store of this layout is refused
    (StoreError) and left alone; on opening, every table, key, index and trigger of the layout must be there,
    exactly. `readonly` opens it for reading only (and never makes it). `lock` takes the store's exclusive lock
    file for as long as it is open: one writer at a time. Every write goes through `writing`, which checks the
    clock inside the transaction. A slot is one transaction, so a kill leaves whole slots or none."""

    def __init__(self, path, readonly: bool = False, create: bool = True, lock: bool = False, synchronous: str = "FULL"):
        self.path, self._lock_fd, self.conn = Path(path), None, None
        self._last_quote: dict[str, tuple] | None = None
        if self.path.exists() and not self.path.is_file():
            raise StoreError("%s is not a file" % path)
        if self.path.exists():
            _check_header(self.path)                 # before anything is made beside it: a foreign file gets no lock file
        elif readonly or not create:
            raise StoreError("no store at %s" % path)
        made_lock = False
        try:
            if lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._lock_fd, made_lock = _take_lock(self.path)
            if not self.path.exists():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    _create(self.path)
                except (sqlite3.Error, OSError) as e:
                    raise StoreError("%s: the store could not be created (%s)" % (path, type(e).__name__)) from None
                _check_header(self.path)
            self._open(readonly, synchronous)
        except StoreError:
            if made_lock:                            # a file refused keeps nothing of ours beside it (the lock is still held)
                try:
                    os.unlink(str(self.path) + LOCK_SUFFIX)
                except OSError:
                    pass
            self._unlock()
            raise
        except BaseException:
            self._unlock()
            raise

    def _open(self, readonly: bool, synchronous: str) -> None:
        try:
            self.conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
            self.conn.execute("PRAGMA busy_timeout = 30000")
            if readonly:
                self.conn.execute("PRAGMA query_only = ON")
            else:
                self.conn.execute("PRAGMA synchronous = %s" % ("FULL" if synchronous not in ("OFF", "NORMAL") else synchronous))
            found = set(self.conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"))
        except sqlite3.Error as e:
            if self.conn is not None:
                self.conn.close()
            raise StoreError("%s: the store cannot be opened (%s)" % (self.path, type(e).__name__)) from None
        if found != _expected_layout():
            self.conn.close()
            raise StoreError("%s is not a chatter store of the current layout (layout %d): its tables, keys or triggers differ"
                             % (self.path, LAYOUT))

    def _unlock(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)                  # closing the file releases the lock
            self._lock_fd = None

    def close(self) -> None:
        try:
            if self.conn is not None:
                self.conn.close()
        finally:
            self._unlock()

    # ----------------------------------------------------------------------------------- reading
    def rows(self, sql: str, args=()) -> list[dict]:
        cur = self.conn.execute(sql, args)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def latest_time(self) -> str | None:
        """The latest time written into the store, in any table (None for an empty one): times never go backwards in
        a table, so the last row of each holds its latest."""
        best = None
        for t, col in _TIMES:
            r = self.conn.execute("SELECT %s FROM %s ORDER BY rowid DESC LIMIT 1" % (col, t)).fetchone()
            if r and r[0] and (best is None or r[0] > best):
                best = r[0]
        return best

    def versions(self, comment_id: str) -> list[dict]:
        out = self.rows("SELECT * FROM comment_versions WHERE comment_id = ? ORDER BY version", (comment_id,))
        for r in out:
            r["body"] = _unpack(r["body"], r.pop("body_enc"))
            r["author"] = None if r["author"] is None else _unpack(r["author"], r.pop("author_enc"))
            r.pop("author_enc", None)
        return out

    # ----------------------------------------------------------------------------------- writing
    def writing(self, now: str):
        return _Writing(self, now)

    def _refuse_clock_behind(self, now: str) -> None:
        latest = self.latest_time()
        if latest and now < latest:
            raise ClockBehind("the clock reads %s, earlier than the latest time in the store (%s): this write was not made "
                              "and the run stops" % (now, latest))

    def _payload(self, kind: str, data: bytes, now: str) -> int:
        if len(data) > MAX_BODY:                          # never read back (inflation stops at MAX_BODY + 1): never stored
            raise ValueError("an answer longer than %d bytes is not stored" % MAX_BODY)
        digest = sha256_bytes(data)
        r = self.conn.execute("SELECT id FROM payloads WHERE kind = ? AND sha256 = ?", (kind, digest)).fetchone()
        if r:
            return r[0]
        return self.conn.execute("INSERT INTO payloads (kind, sha256, first_seen_at, size, body) VALUES (?,?,?,?,?)",
                                 (kind, digest, now, len(data), _deflate(bytes(data)))).lastrowid

    def payload_bytes(self, payload_id: int) -> bytes:
        """The raw answer exactly as received (decompressed, and checked against its hash). Raises StoreError."""
        r = self.conn.execute("SELECT sha256, size, body FROM payloads WHERE id = ?", (payload_id,)).fetchone()
        if r is None:
            raise StoreError("no raw answer %s" % payload_id)
        out = _inflate(r[2], r[1])
        if sha256_bytes(out) != r[0]:
            raise StoreError("a stored raw answer does not match its hash")
        return out

    def _event(self, table: str, e: Event, series: str | None = None) -> None:
        until = iso(e.until) if e.until is not None else None
        if table == "comment_events":
            self.conn.execute("INSERT INTO comment_events (at, kind, slot_ts, until, count, detail) VALUES (?,?,?,?,?,?)",
                              (iso(e.at), e.kind, e.slot, until, e.count, _s(e.detail)))
        else:
            self.conn.execute("INSERT INTO price_events (at, kind, series, slot_ts, until, count, detail) VALUES (?,?,?,?,?,?,?)",
                              (iso(e.at), e.kind, series, e.slot, until, e.count, _s(e.detail)))

    def save_request(self, *, at: float, service: str, backend: str, purpose: str, slot: int, page: int,
                     series: str | None = None) -> int:
        """The row that says a request is about to be sent, committed (durably, at the store's synchronous level)
        before it leaves. Returns its id, which the request's outcome row names."""
        with self.writing(iso(at)):
            return self.conn.execute("INSERT INTO requests (at, service, backend, purpose, slot_ts, page, series) VALUES (?,?,?,?,?,?,?)",
                                     (iso(at), service, backend, purpose, int(slot), int(page), series)).lastrowid

    def save_run(self, started: float, backend: str, comment_interval: int, price_interval: int) -> None:
        """A run begins: one row, a `down` event on each side when the store already holds something older (the time
        nobody was recording), and an outcome "unknown" for every request of the runs before whose answer was never
        stored (a kill while it was out), appended in the same transaction."""
        now = iso(started)
        with self.writing(now):
            before = self.latest_time()
            last_run = self.conn.execute("SELECT started_at FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            self.conn.execute("INSERT INTO runs (started_at, backend, comment_interval_s, price_interval_s, program) VALUES (?,?,?,?,?)",
                              (now, backend, comment_interval, price_interval, USER_AGENT))
            if before:
                self.conn.execute("INSERT INTO comment_events (at, kind, until, detail) VALUES (?,?,?,?)",
                                  (now, "down", now, "from " + before))
                self.conn.execute("INSERT INTO price_events (at, kind, until, detail) VALUES (?,?,?,?)",
                                  (now, "down", now, "from " + before))
            self._unknown_outcomes(now, last_run[0] if last_run else "")

    def _unknown_outcomes(self, now: str, since: str) -> None:
        """Every request sent since the last run began (the starts before it settled the older ones, in their own
        transaction) that has no outcome row gets one: failed, "unknown", naming it."""
        for rid, backend, purpose, slot, page in self.conn.execute(
                "SELECT id, backend, purpose, slot_ts, page FROM requests r WHERE service = 'reddit' AND at >= ? AND NOT EXISTS "
                "(SELECT 1 FROM comment_fetches f WHERE f.request_id = r.id) ORDER BY id", (since,)).fetchall():
            fid = self.conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM comment_fetches").fetchone()[0]
            self.conn.execute("INSERT INTO comment_fetches (id, fetched_at, slot_ts, backend, purpose, page, ok, failure, request_id) "
                              "VALUES (?,?,?,?,?,?,0,'unknown',?)", (fid, now, slot, backend, purpose, page, rid))
        for rid, series, slot in self.conn.execute(
                "SELECT MIN(id), series, slot_ts FROM requests r WHERE service = 'kalshi' AND at >= ? GROUP BY series, slot_ts "
                "HAVING NOT EXISTS (SELECT 1 FROM price_fetches f WHERE f.series = r.series AND f.slot_ts = r.slot_ts AND "
                "f.request_id IS NOT NULL) ORDER BY 1", (since,)).fetchall():
            fid = self.conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM price_fetches").fetchone()[0]
            self.conn.execute("INSERT INTO price_fetches (id, fetched_at, slot_ts, series, ok, failure, request_id) "
                              "VALUES (?,?,?,?,0,'unknown',?)", (fid, now, slot, series, rid))

    def _newest_stored_time(self) -> str | None:
        r = self.conn.execute("SELECT MAX(source_time) FROM comments").fetchone()
        return r[0] if r else None

    def save_comment_slot(self, fetches: list, events: list) -> dict:
        """Everything one Reddit slot brought, in one transaction: each request's raw answer (once, by hash), a row
        per request, the comments and versions of every good feed, a possible-miss row when the slot's oldest comment
        is newer than the newest comment already stored, and the events given. Returns the counts."""
        times = [f.at for f in fetches] + [e.at for e in events]
        out = {"new_comments": 0, "new_versions": 0, "miss": False, "fetch_ids": []}
        if not times:
            return out
        # the clock is checked at the slot's latest time: the rows of the requests sent during the slot are already
        # in, and the tables' own triggers keep each table's times in order
        with self.writing(iso(max(times))):
            good = [f for f in fetches if f.purpose == "feed" and f.failure is None and f.entries is not None]
            marked = any(e.kind == "possible_miss" for e in events)        # the slot's caller marked it already
            miss_event = None if marked else self._possible_miss(good, max(f.at for f in fetches) if fetches else None)
            for f in fetches:
                fid, nc, nv = self._save_fetch(f)
                out["fetch_ids"].append(fid)
                out["new_comments"] += nc
                out["new_versions"] += nv
            evs = list(events)
            if miss_event is not None:
                evs.append(miss_event)
                out["miss"] = True
            for e in sorted(evs, key=lambda e: e.at):
                self._event("comment_events", e)
        return out

    def _possible_miss(self, good: list, at: float | None = None) -> Event | None:
        """The oldest comment of the slot's feeds is newer than the newest comment stored before it: comments may
        have been missed. With no time to compare, a feed that shares no comment with the store says the same."""
        entries = [e for f in good for e in f.entries]
        if not entries or self.conn.execute("SELECT 1 FROM comments LIMIT 1").fetchone() is None:
            return None
        newest = self._newest_stored_time()
        times = [e.source_time for e in entries if e.source_time]
        if times and newest:
            miss = min(times) > newest
            why = "oldest %s newer than the newest stored %s" % (min(times), newest)
        else:
            miss = not any(self.conn.execute("SELECT 1 FROM comments WHERE comment_id = ?", (e.comment_id,)).fetchone()
                           for e in entries)
            why = "no comment in common with the store"
        if not miss:
            return None
        when = max(f.at for f in good) if at is None else max(at, max(f.at for f in good))
        return Event(at=when, kind="possible_miss", slot=good[0].slot, until=None, count=len(entries),
                     detail=why)

    def _save_fetch(self, f: Fetch) -> tuple[int, int, int]:
        """One request: its raw answer, the comments and versions of a good feed, then its own row (written last, with
        the counts, since a row can never be changed). Returns the row's id and the new comments and versions."""
        now = iso(f.at)
        kind = "reddit_" + f.backend if f.purpose == "feed" else "reddit_token_" + f.backend
        pid = self._payload(kind, f.body, now) if f.body is not None else None
        digest, size = (sha256_bytes(f.body), len(f.body)) if f.body is not None else (f.body_sha256, f.body_size)
        fid = self.conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM comment_fetches").fetchone()[0]
        new_comments = new_versions = dups = 0
        if f.failure is None and f.entries is not None:
            latest: dict[str, Entry] = {}
            for e in f.entries:                              # a comment listed twice in one answer counts once, the later wins
                latest[e.comment_id] = e
            dups = len(f.entries) - len(latest)
            for e in reversed(list(latest.values())):        # oldest first when the feed lists the newest first
                nc, nv = self._save_entry(e, fid, now)
                new_comments += nc
                new_versions += nv
        self.conn.execute(
            "INSERT INTO comment_fetches (id, fetched_at, slot_ts, backend, purpose, page, status, ok, failure, payload_id, "
            "comments_in_feed, new_comments, new_versions, bad_entries, duplicates, rate_remaining, rate_reset, request_id, "
            "body_sha256, body_size) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fid, now, f.slot, f.backend, f.purpose, f.page, f.status, 1 if f.failure is None else 0, f.failure, pid,
             len(f.entries) if f.entries is not None else None, new_comments, new_versions, f.bad_entries, dups,
             f.rate_remaining, f.rate_reset, f.request_id, digest, size))
        return fid, new_comments, new_versions

    def _save_entry(self, e: Entry, fetch_id: int, now: str) -> tuple[int, int]:
        sig = version_sig(e.author, e.body)
        row = self.conn.execute("SELECT version, sig FROM comment_versions WHERE comment_id = ? ORDER BY version DESC LIMIT 1",
                                (e.comment_id,)).fetchone()
        new_comment = 0
        if row is None:
            self.conn.execute("INSERT INTO comments (comment_id, thread_id, permalink, first_seen, source_time, source_kind, "
                              "first_fetch_id) VALUES (?,?,?,?,?,?,?)",
                              (e.comment_id, _s(e.thread_id), _s(e.permalink), now, e.source_time, e.source_kind, fetch_id))
            version, change, new_comment = 1, "first", 1
        elif row[1] == sig:
            return 0, 0
        else:
            version, change = row[0] + 1, "edit"
        body, benc = _pack(e.body)
        author, aenc = (None, "utf8") if e.author is None else _pack(e.author)
        self.conn.execute(
            "INSERT INTO comment_versions (comment_id, version, author, author_enc, body, body_enc, sig, seen_at, source_time, "
            "source_raw, source_kind, edited, fetch_id, change) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (e.comment_id, version, author, aenc, body, benc, sig, now, e.source_time, _s(e.source_raw), e.source_kind,
             _s(e.edited), fetch_id, change))
        return new_comment, 1


    # ----------------------------------------------------------------------------------- prices
    def _load_last_quotes(self) -> dict:
        out = {}
        for t, b, a, l in self.conn.execute("SELECT ticker, yes_bid, yes_ask, last_price FROM prices WHERE id IN "
                                            "(SELECT MAX(id) FROM prices GROUP BY ticker)"):
            out[t] = (b, a, l)
        return out

    def save_price_fetch(self, *, series: str, slot: int, at: float, failure: str | None, markets: list, seen: int,
                         bad_rows: int, body: bytes | None, request_id: int | None = None, status: int | None = None,
                         body_sha256: str | None = None, body_size: int | None = None) -> int:
        """One Kalshi read of one series: a row for the fetch, and one row per market whose bid, ask or last price
        differs from its latest stored row (or that has none). Returns the number of market rows stored. `body` is
        the raw answer, kept only when it could not be read; `request_id` the row of its first request (None when
        nothing was sent)."""
        now = iso(at)
        if self._last_quote is None:
            self._last_quote = self._load_last_quotes()
        with self.writing(now):
            pid = self._payload("kalshi_unread", body, now) if body is not None else None
            if body is not None:
                body_sha256, body_size = sha256_bytes(body), len(body)
            fid = self.conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM price_fetches").fetchone()[0]
            stored, fresh = 0, {}
            if failure is None:
                seen_tickers = set()
                for q in markets:
                    if q.ticker in seen_tickers:
                        bad_rows += 1                         # a ticker listed twice in one answer: the first is kept
                        continue
                    seen_tickers.add(q.ticker)
                    key = (q.bid, q.ask, q.last)
                    if self._last_quote.get(q.ticker) == key:
                        continue
                    self.conn.execute(
                        "INSERT INTO prices (fetch_id, seen_at, series, ticker, event_ticker, yes_bid, yes_ask, last_price, volume, "
                        "open_interest, yes_bid_size, yes_ask_size, status, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (fid, now, series, _s(q.ticker), _s(q.event_ticker), q.bid, q.ask, q.last, q.volume, q.open_interest,
                         q.bid_size, q.ask_size, _s(q.status), q.note))
                    fresh[q.ticker] = key
                    stored += 1
            self.conn.execute(
                "INSERT INTO price_fetches (id, fetched_at, slot_ts, series, ok, failure, markets_seen, rows_stored, bad_rows, payload_id, "
                "request_id, status, body_sha256, body_size) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fid, now, slot, series, 1 if failure is None else 0, failure, seen if failure is None else 0, stored, bad_rows, pid,
                 request_id, status, body_sha256, body_size))
        self._last_quote.update(fresh)                        # only after the commit
        return stored

    def save_price_event(self, e: Event, series: str | None = None) -> None:
        with self.writing(iso(e.at)):
            self._event("price_events", e, series)


class _Writing:
    """One write transaction at time `now`: it takes SQLite's write lock first (BEGIN IMMEDIATE), then compares `now`
    with the latest time already in the store, every writer's included; an earlier time raises ClockBehind and
    nothing of this transaction is written. Commits at the end, rolls back on any error."""

    def __init__(self, store: Store, now: str):
        self.store, self.now = store, now

    def __enter__(self):
        c = self.store.conn
        if c.in_transaction:
            raise RuntimeError("a write transaction is already open")
        c.execute("BEGIN IMMEDIATE")
        try:
            self.store._refuse_clock_behind(self.now)
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return self

    def __exit__(self, et, ev, tb):
        c = self.store.conn
        if et is None:
            c.execute("COMMIT")
            return False
        if c.in_transaction:
            c.execute("ROLLBACK")
        return False


# ------------------------------------------------------------------------------------------------ the transport
@dataclass
class Response:
    status: int
    headers: dict          # names in lower case
    body: bytes
    truncated: bool = False
    bad: str | None = None  # the body is not what the headers state (shorter, longer, cut off, a length not one plain number)


class TooLarge(Exception):
    """A Kalshi answer longer than MAX_BODY. `body` is what was read (MAX_BODY bytes): its hash and size are recorded."""

    def __init__(self, body: bytes, status: int | None = None):
        super().__init__("an answer longer than %d bytes" % MAX_BODY)
        self.body, self.status = body, status


_ABANDONED: dict[str, list] = {}         # per service: requests given up on whose thread has not ended yet


def bounded_call(fn: Callable[[], Any], limit: float, service: str = "reddit", abort: Callable[[], Any] | None = None) -> Any:
    """`fn()` with a hard limit, in a short-lived daemon thread: an answer not in after `limit` seconds raises
    TimeoutError, and `abort()` is called first, which shuts the request's socket so that its thread ends at once.
    While MAX_ABANDONED requests of `service` that were given up on are still running, no new one of that service is
    started (that too raises TimeoutError); those of the other service are never held back by them."""
    stuck = _ABANDONED.setdefault(service, [])
    stuck[:] = [t for t in stuck if t.is_alive()]
    if len(stuck) >= MAX_ABANDONED:
        raise TimeoutError("%d abandoned %s requests still running; none started" % (MAX_ABANDONED, service))
    box: dict = {}

    def work():
        try:
            box["value"] = fn()
        except BaseException as e:                    # handed to the caller below
            box["exc"] = e
    th = threading.Thread(target=work, name="chatter-get-" + service, daemon=True)
    th.start()
    th.join(max(0.0, limit))
    if th.is_alive():
        if abort is not None:
            abort()
        stuck.append(th)
        raise TimeoutError("no answer within %g s" % limit)
    if "exc" in box:
        raise box["exc"]
    return box["value"]


class _Abort:
    """The socket of one request, shut from the caller's thread when the request's deadline passes: whatever the
    server does, the request's thread then wakes, fails and ends."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sock = None
        self.fired = False

    def hold(self, sock) -> None:
        with self._lock:
            if self.fired:
                raise TimeoutError("the deadline passed")
            self._sock = sock

    def __call__(self) -> None:
        with self._lock:
            self.fired, s = True, self._sock
        if s is not None:
            try:
                socket.socket.shutdown(s, socket.SHUT_RDWR)      # the plain socket's own shutdown, TLS or not
            except OSError:
                pass


class NoTrustStore(Exception):
    """Neither the system's certificate bundle nor certifi's is there: nothing can be verified, nothing is asked."""


class BadLength(ValueError):
    """An answer whose body is not what its Content-Length states (shorter, longer, cut off, or a length that is not
    one plain number). `status` is the answer's own when its status line was read, and `body` what was read."""

    def __init__(self, reason: str = "the body is not as stated", body: bytes | None = None, status: int | None = None):
        super().__init__(reason)
        self.body, self.status = body, status


_SSL_CONTEXT: ssl.SSLContext | None = None     # None: the module's own context (_make_context over _trust_bundle)
_SSL_DEFAULT: list = []


def _certifi_bundle() -> str | None:
    try:
        import certifi
        p = certifi.where()
    except Exception:
        return None
    return p if isinstance(p, str) and os.path.isfile(p) else None


def _trust_bundle() -> str:
    """The one certificate bundle the transport trusts: the system's SYSTEM_BUNDLE, else certifi's. A path fixed in
    the code: SSL_CERT_FILE, SSL_CERT_DIR and every other variable change nothing. Raises NoTrustStore."""
    if os.path.isfile(SYSTEM_BUNDLE):
        return SYSTEM_BUNDLE
    p = _certifi_bundle()
    if p:
        return p
    raise NoTrustStore("no certificate bundle to verify servers with (neither %s nor certifi's): nothing is asked"
                       % SYSTEM_BUNDLE)


def _make_context(cafile: str) -> ssl.SSLContext:
    """A client context trusting `cafile` alone: the certificate and the host name are always verified, TLS 1.2 at
    least. Nothing here loads the default paths, so nothing reads the environment."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = True
    ctx.load_verify_locations(cafile=cafile)
    return ctx


def _ssl_context() -> ssl.SSLContext:
    if _SSL_CONTEXT is not None:
        return _SSL_CONTEXT
    if not _SSL_DEFAULT:
        _SSL_DEFAULT.append(_make_context(_trust_bundle()))
    return _SSL_DEFAULT[0]


def _more_waiting(fp, sock) -> bool:
    """Whether bytes beyond the stated body are already there: what the connection's buffer holds, and one read that
    does not wait. Anything that goes wrong here is taken as nothing more."""
    try:
        sock.setblocking(False)
        return bool(fp.peek(1))
    except Exception:
        return False


def _read_stated(fp, sock, status: int, hd: dict, length: int, left: Callable[[], float], body: bytearray) -> Response:
    """The body of an answer that states its length (at most MAX_BODY): exactly `length` bytes from the connection
    (into `body`), then a look, without waiting, at whether more came. Shorter (the connection ended first) or longer
    is `bad`; the status and headers are kept either way."""
    while len(body) < length:
        sock.settimeout(left())
        chunk = fp.read1(min(65536, length - len(body)))
        if not chunk:
            return Response(status, hd, bytes(body), False, "shorter than its Content-Length")
        body += chunk
    if _more_waiting(fp, sock):
        return Response(status, hd, bytes(body), False, "longer than its Content-Length")
    return Response(status, hd, bytes(body))


def _http(method: str, url: str, headers: dict, data: bytes | None, timeout: float, service: str) -> Response:
    """One HTTP request by this module's own transport: http.client over a socket of its own. No proxy (nothing here
    reads the environment), no redirect followed (a 3xx comes back as it is), no cookie, no header but those given,
    Host, and Content-Length when there is a body. The whole answer, connection, TLS, headers and body together, is
    bounded by `timeout`: then the socket is shut and TimeoutError raised. At most MAX_BODY bytes of the body are
    kept; a longer answer, or one whose Content-Length states more, comes back `truncated`. Once the status line and
    headers are read they are never lost: a body shorter or longer than its Content-Length, one cut off, or a
    Content-Length that is not one plain number comes back with its status and headers and `bad` saying so (the
    caller acts on the status first). A body that does not finish (the deadline passing while it is read, a reset or
    any other error) still raises, as before, and the error then carries `answer`: a Response with the status, the
    headers, the bytes read and `bad` saying so, for the caller to act on. An error before the status line and headers
    were read carries none. TLS trusts one bundle only."""
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("not an http address")
    https = u.scheme == "https"
    host, port = u.hostname, u.port or (443 if https else 80)
    target = (u.path or "/") + ("?" + u.query if u.query else "")
    end = time.monotonic() + max(0.0, timeout)
    abort = _Abort()
    read: dict = {}                                       # the status and headers once read, and the body so far

    def left() -> float:
        r = end - time.monotonic()
        if r <= 0:
            raise TimeoutError("the deadline passed")
        return r

    def go() -> Response:
        sock = socket.create_connection((host, port), timeout=left())
        try:
            abort.hold(sock)
            if https:
                sock = _ssl_context().wrap_socket(sock, server_hostname=host, do_handshake_on_connect=False)
                abort.hold(sock)
                sock.settimeout(left())
                sock.do_handshake()
            conn = http.client.HTTPConnection(host, port)
            conn.sock = sock
            conn.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", host if port == (443 if https else 80) else "%s:%d" % (host, port))
            for k, v in headers.items():
                conn.putheader(k, v)
            if data is not None:
                conn.putheader("Content-Length", str(len(data)))
            sock.settimeout(left())
            conn.endheaders(data)
            r = conn.getresponse()
            status, hd = r.status, {k.lower(): v for k, v in r.getheaders()}
            body = bytearray()
            read.update(status=status, headers=hd, body=body)   # from here on the status and headers are kept
            stated = r.msg.get_all("content-length") or []
            if len(stated) > 1 or (stated and not CONTENT_LENGTH.fullmatch(stated[0])):
                return Response(status, hd, b"", False, "not one plain Content-Length")    # the body is not read
            length = int(stated[0]) if stated else None
            if length is not None and length > MAX_BODY:
                return Response(status, hd, b"", True)      # more than the limit stated: not read at all
            if length is not None and not r.chunked:
                return _read_stated(r.fp, sock, status, hd, length, left, body)
            try:
                while len(body) <= MAX_BODY:
                    sock.settimeout(left())
                    chunk = r.read(min(65536, MAX_BODY + 1 - len(body)))
                    if not chunk:
                        break
                    body += chunk
            except http.client.HTTPException as e:          # chunks cut off or not readable: the answer comes back
                part = getattr(e, "partial", b"")           # with its status and headers, `bad` saying so (any other
                body += part if isinstance(part, (bytes, bytearray)) else b""   # error raises carrying them: below)
                return Response(status, hd, bytes(body[:MAX_BODY]), False, "the body ended early")
            if length is not None:                          # a length stated beside a chunked body: not as stated
                return Response(status, hd, bytes(body[:MAX_BODY]), len(body) > MAX_BODY, "a length and chunks both")
            return Response(status, hd, bytes(body[:MAX_BODY]), len(body) > MAX_BODY)
        finally:
            try:
                sock.close()
            except OSError:
                pass
    try:
        return bounded_call(go, timeout, service, abort)
    except Exception as e:
        if "status" in read:                              # the body did not finish: the deadline, a reset, any error
            e.answer = Response(read["status"], read["headers"], bytes(read["body"][:MAX_BODY]), False,
                                "the body did not finish")
        raise


_ALLOWED = {("https", PUBLIC_HOST), ("https", OAUTH_HOST)}
_KALSHI_ALLOWED = {("https", urllib.parse.urlsplit(kd.BASE_URL).hostname)}


def reddit_get(method: str, url: str, headers: dict, data: bytes | None, timeout: float) -> Response:
    """One request to Reddit, nothing followed: the real transport. Only https to the two Reddit hosts; at most
    MAX_BODY bytes of the answer are read; the whole answer is bounded by `timeout`."""
    u = urllib.parse.urlsplit(url)
    if (u.scheme, u.hostname) not in _ALLOWED or u.username or u.password:
        raise ValueError("not a Reddit address")
    return _http(method, url, headers, data, timeout, "reddit")


def kalshi_get(url: str, timeout: float) -> bytes:
    """One keyless GET to Kalshi's public interface by this module's own bounded transport (no proxy, no redirect,
    at most MAX_BODY bytes, the whole answer within `timeout`). A non-200 answer raises kd.HttpError with its status
    (a redirect included), whatever its body; a longer one raises TooLarge; a 200 whose body is not what its headers
    state raises BadLength carrying the status. A non-200 answer whose body did not finish raises kd.HttpError with
    its status too; a 200 whose body did not finish raises what the transport raised (a timeout, a network error)."""
    u = urllib.parse.urlsplit(url)
    if (u.scheme, u.hostname) not in _KALSHI_ALLOWED or u.username or u.password:
        raise ValueError("not Kalshi's public address")
    try:
        r = _http("GET", url, {"User-Agent": USER_AGENT, "Accept": "application/json"}, None, timeout, "kalshi")
    except Exception as cut:
        a = getattr(cut, "answer", None)
        if not isinstance(a, Response) or a.status == 200:
            raise
        e = kd.HttpError(a.status, url)                   # the status first, whatever the body
        e.body = a.body
        raise e from None
    if r.truncated:
        raise TooLarge(r.body, r.status)
    if r.status != 200:                                   # the status first, whatever the body
        e = kd.HttpError(r.status, url)
        e.body = r.body                                   # its hash and size are recorded, never the body itself
        raise e
    if r.bad:
        raise BadLength(r.bad, r.body, r.status)
    return r.body


def _float(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def is_silent(ts: float) -> bool:
    """Minute 5 to minute 12 of the hour (UTC; every time zone's hour has the same minutes): the hourly pick reader's window."""
    return SILENT_FROM_MINUTE <= int(ts % 3600) // 60 <= SILENT_TO_MINUTE


def window_start(ts: float) -> int:
    return int(ts // 3600) * 3600 + SILENT_FROM_MINUTE * 60


def first_slot(now: float, interval: int) -> int:
    """The first slot of a run begun at `now`: the one just gone if it is not yet half over, else the next."""
    g = int(now // interval) * interval
    return g if now - g <= SKIP_AFTER_FRACTION * interval else g + interval


def _named_wait(v) -> float | None:
    """A number of seconds a service named, from one second to an hour (BACKOFF_NAMED_MAX_S), or None."""
    x = _float(v)
    return None if x is None else min(max(x, BACKOFF_MIN_S), BACKOFF_NAMED_MAX_S)


def _http_date_wait(v, now: float) -> float | None:
    """Retry-After as an HTTP date: the seconds from `now` to it, or None when it cannot be read or is not ahead."""
    if not isinstance(v, str) or not v.strip() or len(v) > 100:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(v.strip())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    try:
        secs = dt.timestamp() - now
    except (OverflowError, OSError, ValueError):
        return None
    return secs if math.isfinite(secs) and secs > 0 else None


def _backoff_seconds(headers: dict, now: float | None = None) -> tuple[float, str]:
    """How long a 429 or 403 binds: the time the answer names (Retry-After as seconds or as an HTTP date, else the
    rate-limit reset header), from one second to an hour; else five minutes, the module's own choice. A date in the
    past or one that cannot be read gives the five minutes."""
    v = _named_wait(headers.get("retry-after"))
    if v is not None:
        return v, "retry-after"
    if now is not None:
        d = _http_date_wait(headers.get("retry-after"), now)
        if d is not None:
            return min(max(d, BACKOFF_MIN_S), BACKOFF_NAMED_MAX_S), "retry-after-date"
    v = _named_wait(headers.get("x-ratelimit-reset"))
    if v is not None:
        return v, "reset"
    return min(BACKOFF_DEFAULT_S, BACKOFF_MAX_S), "default"


class Collector:
    """The two recorders on one clock. `clock()` is epoch seconds, `sleep(s)` waits; `reddit_fetch(method, url,
    headers, data, timeout) -> Response` and `kalshi_get(url, timeout) -> bytes` are the transports (the real ones
    by default; stand-ins in tests), each of which must give up and raise TimeoutError after `timeout` seconds.
    Each step (a Reddit slot, a price pass) is bounded by STEP_DEADLINE_S in all, so one recorder never delays the
    other by more than that. A recorder aims at its slots on the clock (multiples of its interval since the epoch,
    so a restart keeps the grid) and skips a slot that is more than half over."""

    def __init__(self, store: Store, *, clock: Callable[[], float] = time.time, sleep: Callable[[float], Any] | None = None,
                 reddit_fetch: Callable = reddit_get, kalshi_get: Callable = kalshi_get, env=None,
                 stop: threading.Event | None = None, log: Callable[[str], Any] | None = None, series=SERIES):
        self.store, self.clock, self.series = store, clock, tuple(series)
        self.stop = stop or threading.Event()
        self.sleep = sleep or (lambda s: self.stop.wait(s))
        self.reddit_fetch, self.kalshi_get = reddit_fetch, kalshi_get
        self.log = log or (lambda line: print(line, flush=True))
        env = os.environ if env is None else env
        self._creds = (env.get("REDDIT_CLIENT_ID") or "", env.get("REDDIT_CLIENT_SECRET") or "")
        self.backend = "oauth" if all(self._creds) else "public"
        self.c_interval = OAUTH_INTERVAL_S if self.backend == "oauth" else PUBLIC_INTERVAL_S
        self.p_interval = PRICE_INTERVAL_S
        self.c_next = self.p_next = None
        self.backoff_until = 0.0
        self.not_before = 0.0                              # no public request before this (the answer's own reset)
        self._last_req: float | None = None
        self._burst: list[float] = []
        self._token: str | None = None
        self._token_until = 0.0
        self._silence_window: int | None = None
        self._price_deadline = 0.0
        self._pages: dict[int, int] = {}                  # official-interface feed requests sent, per slot
        self._asked: set[tuple[str, int]] = set()         # (series, slot) of every Kalshi minute already asked
        self._k_last: float | None = None                 # the start of the latest Kalshi request (three a second at most)
        self._open: tuple | None = None                   # the request out now: (request id, purpose, page), or None
        self._k_rid: int | None = None                    # the first request row of the series pass under way
        self._k_seen: tuple = (None, None, None)          # the latest Kalshi answer of the pass: its hash, size and status
        self.counts = {"comment_slots": 0, "comment_failed": 0, "price_slots": 0, "price_failed": 0, "versions": 0,
                       "rows": 0, "misses": 0, "backoffs": 0, "skipped": 0}
        self._beat = 0.0
        self._seen: float | None = None                      # the latest reading of the clock

    # ------------------------------------------------------------------------------------------ the loop
    def _remember(self, now: float) -> None:
        """The limits hold across any kill: the request history they use is rebuilt from the store. Every request row
        counts as a request sent INTENT_MARGIN_S after its time (whether its answer came or not); an answer stored
        counts at its own time, which is never earlier than its request; back-offs and the answer's own reset times
        are taken as still in force; and the pages of the latest official slots and the Kalshi minutes already asked
        are counted, so that no slot is asked for more than its share whatever was killed when. Kalshi's pacer starts
        from its latest request row (KALSHI_INTENT_MARGIN_S after it)."""
        c = self.store.conn
        r = c.execute("SELECT MAX(until) FROM comment_events WHERE kind IN ('backoff', 'ratelimit')").fetchone()
        if r and r[0]:
            self.backoff_until = max(self.backoff_until, _iso_ts(r[0]))
        times = []
        r = c.execute("SELECT MAX(at) FROM requests WHERE service = 'reddit' AND backend = 'public' AND purpose = 'feed'").fetchone()
        if r and r[0]:
            times.append(_iso_ts(r[0]) + INTENT_MARGIN_S)
        rows = c.execute("SELECT fetched_at, rate_reset FROM comment_fetches WHERE request_id IS NOT NULL AND backend = 'public' "
                         "AND status IS NOT NULL ORDER BY id DESC LIMIT 1").fetchall()
        if rows:
            times.append(_iso_ts(rows[0][0]))              # an answer is never earlier than its request
        # every official request has its row: the burst is counted from those alone (an answer row would count twice)
        for (at,) in c.execute("SELECT at FROM requests WHERE service = 'reddit' AND backend = 'oauth' AND at > ?",
                               (iso(now - 60.0 - 2 * INTENT_MARGIN_S),)):
            self._burst.append(_iso_ts(at) + INTENT_MARGIN_S)
        if times:
            self._last_req = max(times + ([self._last_req] if self._last_req is not None else []))
        if rows and rows[0][1] is not None and rows[0][1] > 0:          # a named wait above an hour is an hour
            self.not_before = max(self.not_before, _iso_ts(rows[0][0]) + min(rows[0][1], BACKOFF_NAMED_MAX_S))
        r = c.execute("SELECT MAX(at) FROM requests WHERE service = 'kalshi'").fetchone()
        if r and r[0]:                                     # Kalshi's pace: its latest request, a restart included
            t = _iso_ts(r[0]) + KALSHI_INTENT_MARGIN_S
            self._k_last = t if self._k_last is None else max(self._k_last, t)
        for slot, n in c.execute("SELECT slot_ts, COUNT(*) FROM requests WHERE service = 'reddit' AND purpose = 'feed' "
                                 "AND slot_ts >= ? GROUP BY slot_ts", (int(now) - 3 * OAUTH_INTERVAL_S,)):
            self._pages[slot] = n
        for series, slot in c.execute("SELECT DISTINCT series, slot_ts FROM requests WHERE service = 'kalshi' AND slot_ts >= ?",
                                      (int(now) - 3 * PRICE_INTERVAL_S,)):
            self._asked.add((series, slot))

    def start(self) -> None:
        now = self.clock()
        self._remember(now)
        self.store.save_run(now, self.backend, self.c_interval, self.p_interval)
        self.c_next, self.p_next = first_slot(now, self.c_interval), first_slot(now, self.p_interval)
        self._beat = now
        self.log("chatter_loop: started, comments from the %s feed every %d s, prices of %d series every %d s"
                 % (self.backend, self.c_interval, len(self.series), self.p_interval))

    def next_due(self) -> float:
        return min(self.c_next, self.p_next)

    def step(self) -> None:
        """Do whatever is due now: the recorder whose slot is earlier first (comments on a tie), each at most once."""
        while True:
            now = self.clock()
            if self._seen is not None and now < self._seen - CLOCK_BACK_TOLERANCE_S:
                raise ClockBehind("the clock reads %s, earlier than %s it read before: the run stops and nothing more is written"
                                  % (iso(now), iso(self._seen)))
            self._seen = max(now, self._seen or now)
            due = [(self.c_next, 0), (self.p_next, 1)]
            due = sorted(d for d in due if d[0] <= now)
            if not due:
                break
            if due[0][1] == 0:
                self._comment_step(now)
            else:
                self._price_step(now)
        if self.clock() - self._beat >= HEARTBEAT_S:
            self._beat = self.clock()
            self.log("chatter_loop: " + " ".join("%s=%d" % kv for kv in self.counts.items()))

    def run(self, for_minutes: float | None = None) -> None:
        self.start()
        end = None if for_minutes is None else self.clock() + 60.0 * for_minutes
        while not self.stop.is_set():
            self.step()
            now = self.clock()
            if end is not None and now >= end:
                break
            self.sleep(min(max(self.next_due() - now, 0.0), 1.0))
        self.log("chatter_loop: stopped, " + " ".join("%s=%d" % kv for kv in self.counts.items()))

    # ------------------------------------------------------------------------------------------ slots
    def _skip_event(self, table: str, now: float, slot: int, count: int, detail: str, series: str | None = None) -> None:
        if count <= 0:
            return
        e = Event(at=now, kind="skipped", slot=slot, until=None, count=count, detail=detail)
        if table == "comment_events":
            self.store.save_comment_slot([], [e])
        else:
            self.store.save_price_event(e, series)
        self.counts["skipped"] += count

    def _comment_step(self, now: float) -> None:
        I = self.c_interval
        top = int(now // I) * I                            # the latest slot on the grid
        first, self.c_next = self.c_next, top + I
        self._pages = {g: n for g, n in self._pages.items() if g >= top - I}     # the current and the previous slot only
        # slots passed over while we were busy or the clock jumped: each is more than half over. In the public feed's
        # silence a passed slot is no loss.
        lost = sum(1 for g in range(first, top, I) if not (self.backend == "public" and is_silent(g)))
        if lost:
            self._skip_event("comment_events", now, top - I, lost, "late")
        silent = self.backend == "public" and is_silent(top)
        if now - top > SKIP_AFTER_FRACTION * I:            # the latest one is more than half over too
            if not silent:
                self._skip_event("comment_events", now, top, 1, "more than half over")
            return
        if silent:
            w = window_start(top)
            if self._silence_window != w:
                self._silence_window = w
                n = SILENT_TO_MINUTE - SILENT_FROM_MINUTE + 1
                self.store.save_comment_slot([], [Event(at=now, kind="silence", slot=top, until=w + n * 60, count=n * 60 // I,
                                                        detail="the hourly pick reader's window")])
            return
        if now < self.backoff_until:
            return                                         # inside a back-off: nothing is sent, the back-off row says why
        self.counts["comment_slots"] += 1
        self._comment_slot(top, now)

    def _comment_slot(self, slot: int, now: float) -> None:
        deadline = now + STEP_DEADLINE_S
        fetches: list = []
        events: list = []
        self._open = None
        try:
            if self.backend == "oauth":
                self._oauth_slot(slot, deadline, fetches, events)
            else:
                self._public_slot(slot, deadline, fetches, events)
        except _STORE_ERRORS:
            raise
        except Exception as e:                             # a slot that broke is a failed slot, never a stopped run
            self._broken(slot, fetches, events, "error_" + type(e).__name__)
        self._open = None
        if not fetches and not events:
            return
        r = self._save_slot(fetches, events)
        self.counts["versions"] += r["new_versions"]
        self.counts["misses"] += 1 if r["miss"] else 0
        if not any(f.purpose == "feed" and f.page == 1 and f.failure is None for f in fetches):
            self.counts["comment_failed"] += 1

    def _broken(self, slot: int, fetches: list, events: list, failure: str) -> None:
        """An exception while handling the slot, recorded with the exception's class name only (never its text). The
        feed pages already read well are kept as they are, their comments and versions stored with their own times;
        the slot then carries a possible-miss mark (so it is not covered) and the page being handled a failed row.
        Any other request of the slot is recorded as failed. The request out when it broke gets its outcome row here,
        and a slot that sent nothing gets a row of its own, so the failure is counted."""
        good = [f for f in fetches if f.purpose == "feed" and f.failure is None and f.entries is not None]
        kept = {id(f) for f in good}
        for f in fetches:
            if id(f) in kept:
                continue
            f.entries, f.bad_entries = None, 0
            if f.failure is None:
                f.failure = failure
        if self._open is not None:
            rid, purpose, page = self._open
            fetches.append(Fetch(at=self.clock(), slot=slot, backend=self.backend, purpose=purpose, page=page, status=None,
                                 failure=failure, body=None, entries=None, request_id=rid))
        elif good:
            fetches.append(Fetch(at=self.clock(), slot=slot, backend=self.backend, purpose="feed", page=max(f.page for f in good) + 1,
                                 status=None, failure=failure, body=None, entries=None))
        elif not fetches:
            fetches.append(Fetch(at=self.clock(), slot=slot, backend=self.backend, purpose="feed", page=1, status=None,
                                 failure=failure, body=None, entries=None))
        if good:
            events.append(Event(at=self.clock(), kind="possible_miss", slot=slot, until=None,
                                count=sum(len(f.entries) for f in good), detail="a later page of the slot was rejected"))
        self._open = None

    @staticmethod
    def _shed(fetches: list) -> None:
        """The body of a bad answer (unreadable, refused, over the limit, or any other failure) is not stored: its
        hash and size are, beside its status and reason. Only the answers read well keep their bodies."""
        for f in fetches:
            if f.body is not None and f.failure is not None:
                f.body_sha256, f.body_size, f.body = sha256_bytes(f.body), len(f.body), None

    def _save_slot(self, fetches: list, events: list) -> dict:
        """The slot's one transaction. Should what it brought fail to be stored for any reason but the store's own, it
        is stored again as failed fetches (their bodies not kept): never a stopped run."""
        self._shed(fetches)
        try:
            return self.store.save_comment_slot(fetches, events)
        except _STORE_ERRORS:
            raise
        except Exception as e:
            failure = "error_" + type(e).__name__
        for f in fetches:
            f.entries, f.bad_entries = None, 0
            if f.failure is None:
                f.failure = failure
        self._shed(fetches)
        try:
            return self.store.save_comment_slot(fetches, events)
        except _STORE_ERRORS:
            raise
        except Exception:
            for f in fetches:
                f.body = f.body_sha256 = f.body_size = None
            return self.store.save_comment_slot(fetches, events)

    def _commit_event(self, e: Event) -> None:
        """A back-off or a rate limit, committed the moment it is known, before anything else happens: a kill right
        after it cannot make the next run forget it."""
        self.store.save_comment_slot([], [e])
        self.counts["backoffs"] += 1

    # ------------------------------------------------------------------------------------------ requests
    def _headers(self, extra: dict | None = None) -> dict:
        h = {"User-Agent": USER_AGENT, "Accept": "application/json" if self.backend == "oauth" else "application/atom+xml"}
        h.update(extra or {})
        return h

    def _request(self, slot: int, purpose: str, page: int, method: str, url: str, headers: dict, data, deadline: float,
                 events: list, keep_body: bool = True) -> tuple[Fetch, Response | None]:
        """One request within the step's deadline, turned into a Fetch: failures are constants, never text from the
        answer. Every limit is checked first (nothing is sent when one would break: "held_back"); then the request's
        row is committed, and only then does the request leave. A 429 or 403 commits its back-off at once, and a feed
        answer's rate-limit waits are honoured at once (_rate_waits), whenever its status line and headers were read."""
        t0 = self.clock()
        resp, failure, rid, said, cut = None, None, None, None, None
        if deadline - t0 <= 0:
            failure = "deadline"
        elif t0 < self.backoff_until:
            failure = "held_back"                          # inside a back-off: nothing is sent
        elif self.backend == "public" and purpose == "feed" and (is_silent(t0) or t0 < self.not_before or
                                                                  (self._last_req is not None and t0 - self._last_req < PUBLIC_MIN_GAP_S)):
            failure = "held_back"                          # a rule would have been broken: nothing is sent
        elif self.backend == "oauth" and sum(1 for t in self._burst if t > t0 - 60.0) >= OAUTH_BURST:
            failure = "held_back"
        elif self.backend == "oauth" and purpose == "feed" and self._pages.get(slot, 0) >= OAUTH_MAX_PAGES:
            failure = "held_back"
        if failure is None:
            rid = self.store.save_request(at=t0, service="reddit", backend=self.backend, purpose=purpose, slot=slot, page=page)
            self._open = (rid, purpose, page)
            if purpose == "feed":
                self._pages[slot] = self._pages.get(slot, 0) + 1
            sent = self.clock()                            # the request leaves now, its row already in
            if self.backend == "public" and purpose == "feed":
                self._last_req = sent
            self._burst = [t for t in self._burst if t > sent - 60.0] + [sent]
            budget = deadline - sent
            if budget <= 0:
                failure = "deadline"
            else:
                try:
                    resp = self.reddit_fetch(method, url, headers, data, min(budget, STEP_DEADLINE_S))
                except TimeoutError as e:
                    failure, cut = "timeout", getattr(e, "answer", None)
                except BadLength as e:
                    failure, said = "bad_length", e.status
                except (OSError, urllib.error.URLError) as e:
                    failure, cut = "network", getattr(e, "answer", None)
                except Exception as e:                     # a transport that broke: a failed fetch, not a stopped run
                    failure, cut = "error", getattr(e, "answer", None)
        at = self.clock()                                  # read once: when the answer (or the failure) came
        # the answer whose status line and headers were read: whole, or one whose body did not finish (`cut`: the
        # transport raised, carrying them). Either way its status is recorded and acted on, and its rate-limit waits
        # are honoured; when no status line was read, the failure above stands alone.
        head = resp if resp is not None else (cut if isinstance(cut, Response) else None)
        status = head.status if head is not None else said
        remaining = _float(head.headers.get("x-ratelimit-remaining")) if head is not None else None
        reset = _float(head.headers.get("x-ratelimit-reset")) if head is not None else None
        body = head.body if (head is not None and keep_body) else None
        if head is not None:
            bad = getattr(head, "bad", None)
            if status in (429, 403):                       # a back-off, whatever the body: truncated, not as stated, none,
                failure = "http_%d" % status               # or one that did not finish
                secs, why = _backoff_seconds(head.headers, at)
                self.backoff_until = max(self.backoff_until, at + secs)
                self._commit_event(Event(at=at, kind="backoff", slot=slot, until=at + secs, count=None,
                                         detail="http_%d %s %g s%s" % (status, why, secs,
                                                                       ", the body not as stated" if bad else "")))
            elif head.truncated:
                failure = "too_large"
            elif status in (301, 302, 303, 307, 308):
                failure = "redirect"
            elif status == 401:
                failure = "http_401"
                self._token = None
            elif status != 200:
                failure = "http_%d" % status
            elif bad and failure is None:
                failure = "bad_length"                     # a 200 whose body is not what its headers state
        f = Fetch(at=at, slot=slot, backend=self.backend, purpose=purpose, page=page, status=status, failure=failure, body=body,
                  entries=None, rate_remaining=remaining, rate_reset=reset, request_id=rid)
        if head is not None and purpose == "feed":
            self._rate_waits(f, slot)
        self._open = None
        return f, resp

    def _rate_waits(self, f: Fetch, slot: int) -> None:
        """The waits a feed answer's rate-limit headers name, honoured whenever its status line and headers were read,
        whatever its status and its body, the moment it came (rev 2's rules, unchanged): on the public feed its reset
        time, never ahead of it, above an hour an hour (the next run reads it back from the answer's row); on the
        official interface, under one request left, a back-off for its reset (from one second to an hour), else five
        minutes, committed at once."""
        if self.backend == "public":
            if f.rate_reset is not None and f.rate_reset > 0:
                self.not_before = max(self.not_before, f.at + min(f.rate_reset, BACKOFF_NAMED_MAX_S))
        elif f.rate_remaining is not None and f.rate_remaining < 1:
            named = _named_wait(f.rate_reset)
            secs = named if named is not None else BACKOFF_DEFAULT_S
            self.backoff_until = max(self.backoff_until, f.at + secs)
            self._commit_event(Event(at=f.at, kind="ratelimit", slot=slot, until=f.at + secs, count=None,
                                     detail="remaining below 1, reset %g s" % secs))

    def _parse_into(self, f: Fetch, resp: Response, oauth: bool) -> str | None:
        """Read a good answer into `f.entries`; an answer that cannot be read is a failed fetch (its body not kept)."""
        try:
            if oauth:
                f.entries, f.bad_entries, after = parse_listing(resp.body)
                return after
            f.entries, f.bad_entries = parse_atom(resp.body)
        except Unparseable:
            f.entries, f.failure = None, "unparseable"
        except Exception:
            f.entries, f.failure = None, "parse_error"
        return None

    def _public_slot(self, slot: int, deadline: float, fetches: list, events: list) -> None:
        f, resp = self._request(slot, "feed", 1, "GET", PUBLIC_URL, self._headers(), None, deadline, events)
        fetches.append(f)
        if resp is not None and f.failure is None:         # (its reset time is honoured already, whatever the answer)
            self._parse_into(f, resp, False)

    def _token_for(self, slot: int, deadline: float, fetches: list, events: list) -> str | None:
        now = self.clock()
        if self._token and now < self._token_until:
            return self._token
        cid, secret = self._creds
        basic = base64.b64encode(("%s:%s" % (cid, secret)).encode("utf-8")).decode("ascii")
        f, resp = self._request(slot, "token", 0, "POST", TOKEN_URL,
                                self._headers({"Authorization": "Basic " + basic, "Accept": "application/json",
                                               "Content-Type": "application/x-www-form-urlencoded"}),
                                b"grant_type=client_credentials", deadline, events, keep_body=False)
        fetches.append(f)
        if resp is None or f.failure is not None:
            return None
        try:
            doc = json.loads(resp.body.decode("utf-8"))
            tok, ttl = doc["access_token"], float(doc.get("expires_in", 3600))
            if not math.isfinite(ttl):
                raise ValueError
        except Exception:
            f.failure = "unparseable"
            return None
        if not isinstance(tok, str) or not TOKEN.fullmatch(tok):
            f.failure = "bad_token"                        # visible ASCII, no space, at most 4,096 long: else never sent
            return None
        self._token, self._token_until = tok, f.at + max(ttl - TOKEN_REFRESH_EARLY_S, 60.0)
        return tok

    def _oauth_slot(self, slot: int, deadline: float, fetches: list, events: list) -> None:
        tok = self._token_for(slot, deadline, fetches, events)
        if tok is None:
            return
        row = self.store.conn.execute("SELECT comment_id, source_time FROM comments ORDER BY source_time DESC, rowid DESC LIMIT 1").fetchone()
        newest_id, newest_time = (row[0], row[1]) if row else (None, None)
        after = None
        for page in range(1, OAUTH_MAX_PAGES + 1):
            if page > 1 and self._pages.get(slot, 0) >= OAUTH_MAX_PAGES:
                return                                     # the slot's pages are spent (a run before was killed in it)
            url = OAUTH_URL + ("&after=" + after if after else "")       # `after` matched CURSOR: nothing to quote
            f, resp = self._request(slot, "feed", page, "GET", url, self._headers({"Authorization": "Bearer " + tok}), None,
                                    deadline, events)
            fetches.append(f)
            if resp is None or f.failure is not None:
                return
            after = self._parse_into(f, resp, True)
            if f.rate_remaining is not None and f.rate_remaining < 1:
                return                                     # its back-off is committed already (_rate_waits)
            if f.entries is None or not f.entries or after is None or newest_id is None:
                return
            if any(e.comment_id == newest_id for e in f.entries):
                return
            times = [e.source_time for e in f.entries if e.source_time]
            if times and newest_time and min(times) <= newest_time:
                return

    # ------------------------------------------------------------------------------------------ prices
    def _quote(self, m) -> Quote | None:
        if not isinstance(m, dict) or not isinstance(m.get("ticker"), str) or not m["ticker"]:
            return None
        notes = []

        def num(*names):
            for n in names:
                if n in m and m[n] is not None and m[n] != "":
                    v = _float(m[n])
                    if v is None:
                        notes.append("bad_" + n)
                    return v
            return None
        q = Quote(ticker=m["ticker"], event_ticker=m.get("event_ticker") if isinstance(m.get("event_ticker"), str) else None,
                  bid=num("yes_bid_dollars"), ask=num("yes_ask_dollars"), last=num("last_price_dollars"),
                  volume=num("volume_fp"), open_interest=num("open_interest_fp"), bid_size=num("yes_bid_size_fp"),
                  ask_size=num("yes_ask_size_fp"), status=m.get("status") if isinstance(m.get("status"), str) else None)
        if q.bid is None and q.ask is None and q.last is None:
            notes.append("no_prices")
        q.note = ",".join(notes) or None
        return q

    def _price_step(self, now: float) -> None:
        I = self.p_interval
        top = int(now // I) * I
        first, self.p_next = self.p_next, top + I
        self._asked = {(sr, g) for sr, g in self._asked if g >= top - I}           # this minute and the one before only
        self._skip_event("price_events", now, top - I, (top - first) // I, "late")
        if now - top > SKIP_AFTER_FRACTION * I:
            self._skip_event("price_events", now, top, 1, "more than half over")
            return
        self._price_deadline = now + STEP_DEADLINE_S
        for series in self.series:
            if (series, top) in self._asked:
                continue                                   # asked already in this minute (by a run since killed): never twice
            self._price_series(series, top)

    def _kalshi_request(self, series: str, slot: int, page: int, url: str) -> bytes:
        """One Kalshi request within the pass's deadline and at most three a second: its row is committed first."""
        self._k_seen = (None, None, None)                   # this page's own answer, or none: never an earlier page's
        if self._price_deadline - self.clock() <= 0:
            raise TimeoutError("the pass is out of time")
        if self._k_last is not None:
            wait = KALSHI_SPACING_S - (self.clock() - self._k_last)
            if wait > 0:
                self.sleep(wait)
        t0 = self.clock()
        if self._price_deadline - t0 <= 0:
            raise TimeoutError("the pass is out of time")
        rid = self.store.save_request(at=t0, service="kalshi", backend="kalshi", purpose="prices", slot=slot, page=page,
                                      series=series)
        self._asked.add((series, slot))
        if self._k_rid is None:
            self._k_rid = rid
        self._k_last = self.clock()
        remaining = self._price_deadline - self._k_last
        if remaining <= 0:
            raise TimeoutError("the pass is out of time")
        out = self.kalshi_get(url, min(remaining, STEP_DEADLINE_S))
        out = out[0] if isinstance(out, tuple) else out
        if isinstance(out, (bytes, bytearray)):
            self._k_seen = (sha256_bytes(bytes(out)), len(out), 200)
        return out

    def _read_series(self, series: str, slot: int) -> tuple:
        """The open markets of one series, following Kalshi's cursor (at most KALSHI_MAX_PAGES pages, all within the
        pass's deadline). Returns (failure, quotes, markets seen, bad rows). No body is kept: the hash, size and status
        of the latest answer are in `_k_seen`."""
        rows, cursor, cursors = [], None, set()
        for page in range(1, KALSHI_MAX_PAGES + 1):
            params = {"series_ticker": series, "status": "open", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            raw = self._kalshi_request(series, slot, page, kd.BASE_URL + "/markets?" + urllib.parse.urlencode(params))
            try:
                doc = json.loads(raw)
            except (ValueError, TypeError, RecursionError):
                return "not_json", [], 0, 0
            if not isinstance(doc, dict) or "markets" not in doc or not isinstance(doc["markets"] or [], list):
                return "bad_shape", [], 0, 0
            got = doc["markets"] or []
            rows += got
            cursor = doc.get("cursor")
            if not cursor or not got or cursor in cursors:
                break
            cursors.add(cursor)
        markets, bad = [], 0
        for m in rows:
            q = self._quote(m)
            if q is None:
                bad += 1
            else:
                markets.append(q)
        return None, markets, len(markets), bad

    @staticmethod
    def _answer_seen(body, status) -> tuple:
        if isinstance(body, (bytes, bytearray)):
            return sha256_bytes(bytes(body)), len(body), status
        return None, None, status

    def _price_series(self, series: str, slot: int) -> None:
        failure, markets, seen, bad = None, [], 0, 0
        self._k_rid, self._k_seen = None, (None, None, None)
        try:
            if self._price_deadline - self.clock() <= 0:
                raise TimeoutError("the pass is out of time")
            failure, markets, seen, bad = self._read_series(series, slot)
        except _STORE_ERRORS:
            raise
        except TimeoutError:
            failure = "timeout"
        except TooLarge as e:
            failure, self._k_seen = "too_large", self._answer_seen(e.body, getattr(e, "status", None))
        except kd.HttpError as e:
            failure = "redirect" if 300 <= e.status < 400 else "http_%d" % e.status
            self._k_seen = self._answer_seen(getattr(e, "body", None), e.status)
        except BadLength as e:
            failure = "bad_length"
            if e.body is not None or e.status is not None:
                self._k_seen = self._answer_seen(e.body, e.status)
        except (OSError, urllib.error.URLError):
            failure = "network"
        except Exception as e:                             # whatever broke: this series' minute failed, the run goes on
            failure = "error_" + type(e).__name__
        if failure is not None:
            markets, seen, bad = [], 0, 0
        at = self.clock()
        digest, size, status = self._k_seen                 # of the answer, read or not: its body is never kept
        try:
            stored = self.store.save_price_fetch(series=series, slot=slot, at=at, failure=failure, markets=markets, seen=seen,
                                                 bad_rows=bad, body=None, request_id=self._k_rid, status=status,
                                                 body_sha256=digest, body_size=size)
        except _STORE_ERRORS:
            raise
        except Exception as e:
            failure = "error_" + type(e).__name__
            stored = self.store.save_price_fetch(series=series, slot=slot, at=at, failure=failure, markets=[], seen=0,
                                                 bad_rows=0, body=None, request_id=self._k_rid, status=status,
                                                 body_sha256=digest, body_size=size)
        self.counts["price_slots"] += 1
        self.counts["rows"] += stored
        if failure:
            self.counts["price_failed"] += 1


# ------------------------------------------------------------------------------------------------ check
_TIME_ORDER_SQL = ("SELECT COUNT(*) FROM (SELECT %(c)s AS t, LAG(%(c)s) OVER (ORDER BY rowid) AS prev FROM %(t)s) "
                   "WHERE prev IS NOT NULL AND t < prev")


def verify(store: Store) -> list[str]:
    """Everything the store promises, checked from the file: SQLite's own integrity, the numbering of versions (1, 2,
    3 ...), no version that repeats the one before it, every stored signature and raw-answer hash matching what is
    stored, every row that points at another pointing at something, each fetch's counts matching its rows, no
    market row repeating the one before it, and no table with a time that goes backwards. Returns the problems, as
    lines of counts and row numbers (never any text of a comment); an empty list is a store that keeps its word."""
    c, bad = store.conn, []

    def q(sql, args=()):
        return c.execute(sql, args).fetchall()

    def n(sql, args=()):
        return q(sql, args)[0][0]

    r = q("PRAGMA integrity_check")
    if r != [("ok",)]:
        bad.append("integrity: SQLite reports %d problems" % len(r))
    for t, col in _TIMES:
        k = n(_TIME_ORDER_SQL % {"t": t, "c": col})
        if k:
            bad.append("time: %d rows of %s carry a time earlier than the row before" % (k, t))
    k = n("SELECT COUNT(*) FROM comments WHERE comment_id NOT IN (SELECT comment_id FROM comment_versions)")
    if k:
        bad.append("comment: %d comments have no version" % k)
    k = n("SELECT COUNT(DISTINCT comment_id) FROM comment_versions WHERE comment_id NOT IN (SELECT comment_id FROM comments)")
    if k:
        bad.append("comment: %d comments have versions but no comment row" % k)
    k = n("SELECT COUNT(*) FROM (SELECT version, ROW_NUMBER() OVER (PARTITION BY comment_id ORDER BY version) AS rn "
          "FROM comment_versions) WHERE version != rn")
    if k:
        bad.append("version: %d version rows are not numbered 1, 2, 3 ... within their comment" % k)
    k = n("SELECT COUNT(*) FROM comment_versions WHERE (version = 1 AND change != 'first') OR (version > 1 AND change != 'edit')")
    if k:
        bad.append("version: %d version rows have the wrong kind of change" % k)
    k = n("SELECT COUNT(*) FROM (SELECT sig, LAG(sig) OVER (PARTITION BY comment_id ORDER BY version) AS prev FROM comment_versions) "
          "WHERE sig = prev")
    if k:
        bad.append("duplicate: %d versions repeat the version before them" % k)
    wrong = 0
    for vid, author, aenc, body, benc, sig in c.execute("SELECT id, author, author_enc, body, body_enc, sig FROM comment_versions"):
        try:
            a = None if author is None else _unpack(author, aenc)
            if version_sig(a, _unpack(body, benc)) != sig:
                wrong += 1
        except Exception:
            wrong += 1
    if wrong:
        bad.append("signature: %d version rows do not match their own signature" % wrong)
    k = n("SELECT COUNT(*) FROM comment_versions v LEFT JOIN comment_fetches f ON f.id = v.fetch_id "
          "WHERE f.id IS NULL OR f.fetched_at != v.seen_at")
    if k:
        bad.append("fetch: %d versions point at no fetch or at one of another time" % k)
    k = n("SELECT COUNT(*) FROM comments m LEFT JOIN comment_fetches f ON f.id = m.first_fetch_id WHERE f.id IS NULL OR f.fetched_at != m.first_seen")
    if k:
        bad.append("fetch: %d comments point at no fetch or at one of another time" % k)
    k = n("SELECT COUNT(*) FROM comment_fetches f WHERE f.new_versions != (SELECT COUNT(*) FROM comment_versions v WHERE v.fetch_id = f.id)")
    if k:
        bad.append("new_versions: %d fetch rows disagree with the versions stored for them" % k)
    k = n("SELECT COUNT(*) FROM comment_fetches f WHERE f.new_comments != (SELECT COUNT(*) FROM comments m WHERE m.first_fetch_id = f.id)")
    if k:
        bad.append("new_comments: %d fetch rows disagree with the comments stored for them" % k)
    k = n("SELECT COUNT(*) FROM comment_fetches WHERE (ok = 0 AND failure IS NULL) OR (ok = 1 AND failure IS NOT NULL) "
          "OR (payload_id IS NOT NULL AND payload_id NOT IN (SELECT id FROM payloads))")
    if k:
        bad.append("fetch: %d fetch rows are inconsistent with their own outcome or point at no raw answer" % k)
    k = n("SELECT COUNT(*) FROM price_fetches WHERE payload_id IS NOT NULL AND payload_id NOT IN (SELECT id FROM payloads)")
    if k:
        bad.append("payload: %d price fetches point at no raw answer" % k)
    broken = wrong = 0
    # one raw answer at a time, its stored length read before the blob itself: a row that states a size beyond
    # MAX_BODY, or holds more than a MAX_BODY answer can compress to, is damage and is never read in
    for pid, digest, size, length in q("SELECT id, sha256, size, length(body) FROM payloads"):
        if not isinstance(size, int) or not 0 <= size <= MAX_BODY or not isinstance(length, int) or length > MAX_STORED_BLOB:
            broken += 1
            continue
        try:
            body = c.execute("SELECT body FROM payloads WHERE id = ?", (pid,)).fetchone()[0]
            if sha256_bytes(_inflate(body, size)) != digest:
                wrong += 1
        except StoreError:
            broken += 1
        body = None
    if broken:
        bad.append("payload: %d raw answers do not decompress to their stated size" % broken)
    if wrong:
        bad.append("payload: %d raw answers do not match their hash" % wrong)
    k = n("SELECT COUNT(*) FROM prices p LEFT JOIN price_fetches f ON f.id = p.fetch_id WHERE f.id IS NULL OR f.fetched_at != p.seen_at")
    if k:
        bad.append("price: %d price rows point at no fetch or at one of another time" % k)
    k = n("SELECT COUNT(*) FROM price_fetches f WHERE f.rows_stored != (SELECT COUNT(*) FROM prices p WHERE p.fetch_id = f.id)")
    if k:
        bad.append("price: %d price fetch rows disagree with the rows stored for them" % k)
    k = n("SELECT COUNT(*) FROM (SELECT yes_bid b, yes_ask a, last_price l, LAG(yes_bid) OVER w pb, LAG(yes_ask) OVER w pa, "
          "LAG(last_price) OVER w pl, LAG(id) OVER w pid FROM prices WINDOW w AS (PARTITION BY ticker ORDER BY id)) "
          "WHERE pid IS NOT NULL AND b IS pb AND a IS pa AND l IS pl")
    if k:
        bad.append("price: %d price rows repeat the row before them" % k)
    # each request row and its outcome belong together: an outcome names a request that exists and is the same
    # request (service, slot, purpose, page or series, never later than its outcome); a request has at most one
    # outcome, and a Kalshi minute one
    k = n("SELECT COUNT(*) FROM comment_fetches f LEFT JOIN requests r ON r.id = f.request_id WHERE f.request_id IS NOT NULL AND "
          "(r.id IS NULL OR r.service != 'reddit' OR r.slot_ts != f.slot_ts OR r.purpose != f.purpose OR r.page != f.page "
          "OR r.backend != f.backend OR r.at > f.fetched_at)")
    if k:
        bad.append("request: %d comment fetch rows name no request, or one that is not theirs" % k)
    k = n("SELECT COUNT(*) FROM price_fetches f LEFT JOIN requests r ON r.id = f.request_id WHERE f.request_id IS NOT NULL AND "
          "(r.id IS NULL OR r.service != 'kalshi' OR r.slot_ts != f.slot_ts OR r.series IS NOT f.series OR r.at > f.fetched_at)")
    if k:
        bad.append("request: %d price fetch rows name no request, or one that is not theirs" % k)
    k = n("SELECT COUNT(*) FROM (SELECT request_id FROM comment_fetches WHERE request_id IS NOT NULL GROUP BY request_id HAVING COUNT(*) > 1)")
    if k:
        bad.append("request: %d requests have more than one outcome" % k)
    k = n("SELECT COUNT(*) FROM (SELECT series, slot_ts FROM price_fetches WHERE request_id IS NOT NULL GROUP BY series, slot_ts "
          "HAVING COUNT(*) > 1)")
    if k:
        bad.append("request: %d Kalshi minutes have more than one outcome" % k)
    return bad


# ------------------------------------------------------------------------------------------------ status
def _utc_ts(text: str) -> float:
    return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC).timestamp()


def _iso_ts(text: str) -> float:
    """The store's millisecond time text back to epoch seconds."""
    return _utc_ts(text) + (int(text[20:23]) / 1000.0 if len(text) >= 24 else 0.0)


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")


def _expected_slots(runs: list, now: float, which: str) -> list[tuple[int, str]]:
    """Every slot of every whole UTC day from the day of the first run to the day of `now` (its unfinished day
    whole too): Clarification 3, time during which the collector was not running is expected and uncovered. Each
    slot is on the grid of the run in force at its time (the latest run started at or before it; before the first
    run, the first run's), with that run's backend."""
    if not runs:
        return []
    starts = [r[0] for r in runs]
    g = int(runs[0][0] // 86400) * 86400
    end = max(g + 86400, -(-int(math.ceil(now)) // 86400) * 86400)
    out = []
    while g < end:                                          # g: the first second not yet looked at
        i = max(bisect.bisect_right(starts, g) - 1, 0)
        start, backend, ci, pi = runs[i]
        interval = ci if which == "comments" else pi
        slot = -(-g // interval) * interval                 # the next slot on this run's grid
        later = starts[i + 1] if i + 1 < len(starts) else math.inf
        if slot >= later:                                   # another run is in force by then: go on from its start
            g = max(int(math.ceil(later)), g + 1)
            continue
        if slot >= end:
            break
        out.append((slot, backend))
        g = slot + 1
    return out


def status(store: Store, now: float) -> dict:
    """What the prediction needs counted, per UTC day, as Clarification 3 counts it. Comments: slots expected (the
    hourly silence of the public feed excluded, and shown), made (a successful first-page read is stored), covered
    (made, and that read carries no possible-miss mark), failed (asked, never read), how many a possible-miss mark
    uncovered, back-off minutes, possible misses, comments and versions stored. Prices, in all and per series: slots
    expected (the five series, one a minute), covered (distinct series and minute with at least one successful read),
    failed, markets seen and rows stored. Every UTC day from the first run's on is expected whole (7,200 price slots;
    624 public or 1,440 official comment slots, by the backend in force at each slot); only slots of the plan already
    begun count, each once, so covered never exceeds expected. `share` is covered over expected, `covered` whether it
    is at least 80%. A day not yet ended is `in_progress`: its counts so far are shown, and it never `counts`; an
    ended day counts when both recorders covered it."""
    # a run's start to the millisecond it is stored with (every run row this module writes has its milliseconds; a
    # start written without them is read as whole seconds): a run begun at 06:06:00.5 does not own the 06:06:00 slot
    runs = [(_iso_ts(r["started_at"]), r["backend"], r["comment_interval_s"], r["price_interval_s"])
            for r in store.rows("SELECT started_at, backend, comment_interval_s, price_interval_s FROM runs ORDER BY id")]
    days: dict[str, dict] = {}

    def blank():
        return {"expected": 0, "made": 0, "covered_slots": 0, "failed": 0, "markets_seen": 0, "rows": 0, "share": None}

    def day(d):
        if d not in days:
            days[d] = {"day": d,
                       "comments": {"expected": 0, "silent": 0, "made": 0, "covered_slots": 0, "failed": 0, "miss_uncovered": 0,
                                    "backoff_minutes": 0.0, "misses": 0, "comments": 0, "versions": 0, "share": None,
                                    "covered": False},
                       "prices": dict(blank(), covered=False, series={s: blank() for s in SERIES}),
                       "in_progress": _utc_ts(d + "T00:00:00") + 86400 > now, "counts": False}
        return days[d]
    c_expected: set[int] = set()
    for g, backend in _expected_slots(runs, now, "comments"):
        c = day(_day(g))["comments"]
        if backend == "public" and is_silent(g):
            c["silent"] += 1
        elif g not in c_expected:
            c_expected.add(g)
            c["expected"] += 1
    p_expected: set[int] = set()
    for g, _ in _expected_slots(runs, now, "prices"):
        if g in p_expected:
            continue
        p_expected.add(g)
        p = day(_day(g))["prices"]
        for s in SERIES:
            p["series"][s]["expected"] += 1
        p["expected"] += len(SERIES)
    marked = {r["s"] for r in store.rows("SELECT DISTINCT slot_ts AS s FROM comment_events WHERE kind = 'possible_miss'")}
    for r in store.rows("SELECT slot_ts AS s, MAX(CASE WHEN purpose = 'feed' AND page = 1 AND ok = 1 THEN 1 ELSE 0 END) AS o "
                        "FROM comment_fetches GROUP BY slot_ts"):
        c = day(_day(r["s"]))["comments"]
        if not r["o"]:
            c["failed"] += 1
            continue
        c["made"] += 1
        if r["s"] not in c_expected or r["s"] >= now:
            continue                                        # not a slot of the plan: shown as made, never as covered
        if r["s"] in marked:
            c["miss_uncovered"] += 1
        else:
            c["covered_slots"] += 1
    for r in store.rows("SELECT slot_ts, series, MAX(ok) AS ok, MAX(markets_seen) AS seen, SUM(rows_stored) AS rows "
                        "FROM price_fetches WHERE series IS NOT NULL GROUP BY slot_ts, series"):
        d = day(_day(r["slot_ts"]))["prices"]
        mine = r["series"] in SERIES
        ps = d["series"].setdefault(r["series"], blank())
        key = "made" if r["ok"] else "failed"
        ps[key] += 1
        ps["rows"] += r["rows"] or 0
        ps["markets_seen"] = max(ps["markets_seen"], r["seen"] or 0)
        if not mine:
            continue                                        # not one of the five: shown, never counted
        d[key] += 1
        d["rows"] += r["rows"] or 0
        d["markets_seen"] = max(d["markets_seen"], r["seen"] or 0)
        if r["ok"] and r["slot_ts"] in p_expected and r["slot_ts"] < now:
            ps["covered_slots"] += 1
            d["covered_slots"] += 1
    for r in store.rows("SELECT at, until FROM comment_events WHERE kind IN ('backoff', 'ratelimit') AND until IS NOT NULL"):
        t, end = _iso_ts(r["at"]), _iso_ts(r["until"])
        while t < end:                                      # a back-off across midnight counts on both days
            seg = min(end, (int(t // 86400) + 1) * 86400) - t
            day(_day(t))["comments"]["backoff_minutes"] += seg / 60.0
            t += seg
    for r in store.rows("SELECT substr(at, 1, 10) AS d, COUNT(*) AS n FROM comment_events WHERE kind = 'possible_miss' GROUP BY 1"):
        day(r["d"])["comments"]["misses"] += r["n"]
    for r in store.rows("SELECT substr(first_seen, 1, 10) AS d, COUNT(*) AS n FROM comments GROUP BY 1"):
        day(r["d"])["comments"]["comments"] += r["n"]
    for r in store.rows("SELECT substr(seen_at, 1, 10) AS d, COUNT(*) AS n FROM comment_versions GROUP BY 1"):
        day(r["d"])["comments"]["versions"] += r["n"]
    for d in days.values():
        c, p = d["comments"], d["prices"]
        c["backoff_minutes"] = round(c["backoff_minutes"], 3)
        c["share"] = c["covered_slots"] / c["expected"] if c["expected"] else None
        c["covered"] = c["share"] is not None and c["share"] >= COVERAGE_MIN
        p["share"] = p["covered_slots"] / p["expected"] if p["expected"] else None
        p["covered"] = p["share"] is not None and p["share"] >= COVERAGE_MIN
        for ps in p["series"].values():
            ps["share"] = ps["covered_slots"] / ps["expected"] if ps["expected"] else None
        d["counts"] = c["covered"] and p["covered"] and not d["in_progress"]      # an unfinished day never counts
    return {"days": [days[k] for k in sorted(days)]}


def _pct(x) -> str:
    return "-" if x is None else "%.1f%%" % (100 * x)


def format_status(st: dict) -> str:
    """The status as plain lines of counts: no comment text, no author, no id."""
    lines = []
    for d in st["days"]:
        c, p = d["comments"], d["prices"]
        lines.append("%s  counts toward the test: %s" % (d["day"], "no, the day is in progress (counts so far, of the whole day)"
                                                         if d.get("in_progress") else "yes" if d["counts"] else "no"))
        lines.append("  comments  expected %4d  (silent %3d, not counted)  covered %4d  made %4d  failed %4d  "
                     "uncovered by a possible miss %d  coverage %6s %s  back-off %.1f min  possible misses %d  comments %d  versions %d"
                     % (c["expected"], c["silent"], c["covered_slots"], c["made"], c["failed"], c["miss_uncovered"], _pct(c["share"]),
                        "ok" if c["covered"] else "UNDER 80%", c["backoff_minutes"], c["misses"], c["comments"], c["versions"]))
        lines.append("  prices    expected %4d  covered %4d  failed %4d  coverage %6s %s  markets seen %d  rows %d"
                     % (p["expected"], p["covered_slots"], p["failed"], _pct(p["share"]), "ok" if p["covered"] else "UNDER 80%",
                        p["markets_seen"], p["rows"]))
        for s, v in p["series"].items():
            lines.append("    %-13s expected %4d  covered %4d  failed %4d  coverage %6s  markets seen %3d  rows %d"
                         % (s, v["expected"], v["covered_slots"], v["failed"], _pct(v["share"]), v["markets_seen"], v["rows"]))
    return "\n".join(lines) if lines else "nothing recorded yet"


# ------------------------------------------------------------------------------------------------ the command line
def _stop_on_signals(stop: threading.Event) -> dict:
    """SIGINT and SIGTERM set `stop`; the loop then finishes its current write and ends. Returns the old handlers."""
    def handler(signum, frame):
        stop.set()
    old = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            old[sig] = signal.signal(sig, handler)
    return old


def main(argv=None, *, clock=None, sleep=None, reddit_fetch=None, kalshi_get=None, env=None, out=None) -> int:
    ap = argparse.ArgumentParser(prog="chatter_loop", description="Collect r/sportsbook's newest comments and Kalshi's game prices.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "status", "check"):
        p = sub.add_parser(name)
        p.add_argument("--db", default=str(DEFAULT_DB))
        if name == "run":
            p.add_argument("--for-minutes", type=float, default=None, help="stop after this many minutes (default: run until stopped)")
    a = ap.parse_args(argv)
    out = out or (lambda line: print(line, flush=True))
    clock = clock or time.time
    if a.cmd in ("status", "check"):
        try:
            store = Store(a.db, readonly=True)
        except StoreError as e:
            out(("check: FAILED: " if a.cmd == "check" else "") + str(e))
            return 1
        try:
            if a.cmd == "status":
                out(format_status(status(store, clock())))
                return 0
            problems = verify(store)
            if problems:
                out("check: FAILED (%d problems)" % len(problems))
                for p in problems:
                    out("  " + p)
                return 1
            counts = ", ".join("%s %d" % (t, store.conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]) for t in TABLES)
            out("check: ok (%s)" % counts)
            return 0
        except sqlite3.Error as e:
            out("check: FAILED: the store could not be read (%s)" % type(e).__name__)
            return 1
        finally:
            store.close()
    try:
        _trust_bundle()                                    # nothing can be verified without it: the run never starts
    except NoTrustStore as e:
        out("chatter_loop: not started: " + str(e))
        return 1
    try:
        store = Store(a.db, lock=True)
    except StoreError as e:
        out(str(e))
        return 1
    stop = threading.Event()
    old = _stop_on_signals(stop)
    try:
        col = Collector(store, clock=clock, sleep=sleep, reddit_fetch=reddit_fetch or reddit_get, kalshi_get=kalshi_get or globals()["kalshi_get"],
                        env=env, stop=stop, log=out)
        col.run(a.for_minutes)
        return 0
    except ClockBehind as e:
        out("chatter_loop: stopped: " + str(e))
        return 1
    except sqlite3.Error as e:
        out("chatter_loop: stopped: the store could not be written (%s)" % type(e).__name__)
        return 1
    except StoreError as e:
        out("chatter_loop: stopped: " + str(e))
        return 1
    finally:
        for sig, h in old.items():
            signal.signal(sig, h)
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
