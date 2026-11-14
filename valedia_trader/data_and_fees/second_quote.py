"""A second, independent price source for the order step's cross-check: Cboe's delayed-quotes JSON.

The order step prices a fund from Yahoo's one-minute bars. Before any real order that price should be checked against
a second source with another upstream. This module is that second source and the comparison; wiring it into the order
step is a separate job and nothing here touches the order step except `probe`, which calls its `minute_quotes` by import.

READ THIS BEFORE RELYING ON IT. Cboe publishes no terms of use for this endpoint that anyone found (survey of
2026-11-13). It is used here for a handful of requests a day as a sanity check, with no key, nothing sent but the
symbol in the path, at most two tries per symbol. A licensed source (a free key the owner signs up for) should replace
it before real money depends on it.

What the real answer looked like (four requests on 2026-11-13, SPY and XLE, one request pair each, read about
21:30 UTC, half an hour after the close, so the delay DURING a session is NOT established here):
- `GET https://cdn.cboe.com/api/global/delayed_quotes/quotes/<SYMBOL>.json` answers HTTP 307 to the same path on
  `cdn-api.cboe.com`, which answers 200 `application/json`, about 520 bytes, `Cache-Control: s-maxage=5`.
- The body is `{"timestamp": "2026-11-13 21:28:34", "data": {...}, "symbol": "SPY"}`. The numbers sit under `data`;
  the symbol is repeated at the top and inside `data`.
- Inside `data`: `symbol`, `security_type`, `exchange_id`, `current_price` (the last trade), `bid`, `ask`,
  `bid_size`, `ask_size`, `open`, `high`, `low`, `close`, `prev_day_close`, `price_change`, `price_change_percent`,
  `volume`, `iv30` (and two more volatility fields), `seqno`, `tick`, and `last_trade_time`.
- `last_trade_time` is a plain "YYYY-MM-DDTHH:MM:SS" with no zone, and it reads as New York wall-clock time: SPY's
  last print was "2026-11-13T16:00:00" (the closing print) and XLE's "2026-11-13T15:59:59", while the HTTP Date header
  said 21:30 GMT (16:30 New York). It is treated as New York time here.
- `timestamp` has no zone either and reads as UTC (21:28:34 against the server's 21:30:03 GMT): it is when the snapshot
  was taken, not when the stock traded. It is returned as `snapshot_at` and `compare` uses it (rule (c) below).
- Every number came as a JSON number. Both SPY and the sector fund had a bid and an ask after the close.

Parsing is strict: every number must be a JSON number (not a string, not a boolean), finite, above 0 and at most 1e7;
anything else is None and the rest of the answer stays. The symbol in the answer must be the symbol asked for.
No result at all (None) unless a usable last price, or a usable bid and ask, remains. `cboe_quote` never raises on an
answer, however hostile; it raises ValueError only for a symbol that fails the symbol rule, before any request.

Freshness (rev 2): a quote hours old must not approve a price now, so `compare` takes the caller's clock `now` (an aware
datetime; `compare` never reads a clock) and says "no second quote" unless ALL of these hold:
  (a) `last_trade_time`, read as New York time, is at most `max_age_seconds` (default 1800) before `now` and at most 300
      seconds after it;
  (b) the quote was fetched at most 120 seconds before `now` and not after it;
  (c) the answer's own snapshot `timestamp` (read as UTC, returned as `snapshot_at`) is within 120 seconds of the fetch
      time, either side; a missing or unreadable snapshot time fails.
Repeated and missing hours: `last_trade_time` is a New York wall-clock reading with no zone, and twice a year it can mean
more than one instant. On the November change day 01:00-01:59 happens twice (two instants an hour apart); on the March
change day 02:00-02:59 never happens (read as standard time or as daylight time, again two instants an hour apart). The
rule is that EVERY instant the reading could mean must satisfy (a), so such a time is accepted only when it is recent under
every reading. Under the default limit (1800 s, a window of 2100 s with the 300 s allowance for the future) that never
happens, so a time in either hour is refused. With an age limit of 3300 s or more (the window is the limit plus the 300 s
allowance, so from 3300 s it is as wide as the hour between the readings) a time in the missing hour, or in the repeated
hour, can be accepted when both readings of it are recent.
The quote's symbol must equal the `symbol` given to `compare`.

Time limit (rev 2): one try, redirects included, has ONE deadline of 10 seconds, enforced three ways: a check before every
read and between redirects on the injected clock (`urllib_fetch(..., clock=)`), a socket timeout of whatever is left of it
on each request, and a watchdog thread that shuts the sockets of the request at the deadline whatever the server is doing
(a trickling body or trickling headers). Redirects are followed by this module itself, never by urllib, and a redirect's
own body is never read. `cboe_quote` also counts a try that took more than 10 seconds on its own clock as failed.

Strict JSON (rev 2): an answer with a repeated key at any level is unusable. A band that its own last trade contradicts
(bid and ask usable, last price outside them by more than the tolerance) is not a band: "no second quote".

Crossed band (rev 3): when bid and ask are both usable numbers and the bid is above the ask the data is suspect and the
verdict is "no second quote", whatever the last price (rev 2 fell back to the last price, which let bid 102, ask 100, last
500 agree with a first price of 500). When only one of the two is usable the last price is still used.

Environment (rev 3): the opener ignores the proxy variables, and its TLS context is built by hand so that it does not honour
SSLKEYLOGFILE (the standard `ssl.create_default_context` opens the named file and logs the session keys to it). Server and
host name are still verified, TLS 1.2 at least. The system's trusted-certificate locations are still read, and OpenSSL
itself reads SSL_CERT_FILE and SSL_CERT_DIR for them: those two are not neutralised here.

Name lookup (rev 3): `getaddrinfo` cannot be interrupted, so the lookup runs in one daemon thread (named
"second-quote-lookup") and the try waits for it only as long as the deadline allows; then the try fails with TimeoutError and
the thread is abandoned. At most one such thread exists at a time: while an abandoned lookup is still waiting, a later try
fails at once without starting another. The thread ends when the resolver answers or at the process's normal exit (it is a
daemon); the connection itself is then made by this module to the address given, under the same deadline and watchdog.

Other decisions:
- A bid above the ask (both usable) is suspect data: "no second quote" whatever the last price (see "Crossed band").
- `compare`: an unusable first price is "disagree" (never "agree"), and a second quote that is not usable is reported
  first as "no second quote". A tolerance that is not a number from 0 to 1, an age limit that is not a positive number, or
  a `now` that is not an aware datetime raises ValueError (a programming error).
- The comparison is exact: each float is read as the decimal it prints as, so "exactly on the band" means exactly.

Licensed, real-time sources (T28b). Two more readers, in the same house style as `cboe_quote` (an injected `fetch`, strict
parsing, a repeated key refused, one 10-second deadline a try, at most 2 tries, the module's own TLS context and name
lookup, never an exception out, never a wrong number), and one function that gives the order path a single answer:
- `alpaca_quote`: `GET https://data.alpaca.markets/v2/stocks/snapshots?symbols=SYM&feed=iex` with headers `APCA-API-KEY-ID`
  and `APCA-API-SECRET-KEY` (real-time bid, ask and last from the IEX exchange; always `feed=iex`, `sip` answers 403 on this
  plan). The answer is an object keyed by symbol; the block of the symbol asked for holds `latestQuote` (`bp`, `ap`, `t`) and
  `latestTrade` (`p`, `t`); times are RFC 3339 UTC with up to nine decimals. Seen by the lead on 2026-11-13 21:04 New York
  (market closed): the IEX band of a fund can be far wider than the fund's price moves (62.81 / 66.86 on 64.93), so a band
  is trusted only when it is narrow (below).
- `finnhub_quote`: `GET https://finnhub.io/api/v1/quote?symbol=SYM` with header `X-Finnhub-Token`; `c` is the last price and
  `t` its time in epoch seconds. An unknown symbol answers 200 with zeros: a `c` or `t` of 0 is no quote.
- Documented limits, not measured: Alpaca 200 requests a minute, Finnhub 60. Each source is asked once per symbol.
- Both give the normal form `{symbol, bid, ask, band_time, last, last_time, fetched_at, source}`; times are UTC seconds.
- KEYS: each key goes only in the request headers named above, only to its own host, only over the verified TLS of this
  module. `keyed_fetch` (the default fetch of these two) refuses, before any request, a URL that is not exactly one of the two
  requests above and a header set that is not exactly the keys of that service. It follows no redirect at all (a redirect is a
  failed try), sets the keys as unredirected headers, and its errors never carry a key. The keys come from the injected
  `credentials` callable, by the names `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `FINNHUB_API_KEY`; the default is
  `valedia_trader.core.secrets.secret`. A key that is missing, raises, or is not 1-256 printable ASCII characters without spaces
  makes that source "no second quote" and no request is made. A key is never in a URL, a log line, an exception text, the probe's
  output or a returned dict. A 4xx answer is not retried (a 403 or 429 will not change in two seconds).
- `judge_quote` judges a normal-form quote by its OWN clocks, at the later of `now` and its fetch time (the fetch happens after
  the caller took `now`), provided the fetch is no more than 30 s after `now` and no more than 120 s before it. A BAND is usable
  only if bid and ask are valid numbers, `band_time` is at most 120 s before and 5 s after that moment, and the band is no wider
  than `BAND_MAX_WIDTH` (0.5 %) of its mid: IEX sees one exchange, and a wide band confirms nothing. A LAST price is usable only if
  `last_time` is at most `max_age_seconds` (1800) before and 300 s after it. With a usable band the first price must lie inside it
  widened by the tolerance, and a usable last price outside that widened band makes the quote "no second quote" (a stale last
  price is not used for this). Without a usable band the usable last price decides. A crossed pair (bid above ask) is "no second
  quote" whatever else is there, as for Cboe (the literal rule "a band is usable only if bid is not above ask" would let the last
  price decide; that is the rev 2 hole, kept closed).
- `second_opinion(first_price, symbol, *, now, sources=("alpaca", "finnhub"), ...)` asks every source named (also "cboe") and
  returns `{verdict, symbol, sources: [{name, verdict, basis, difference, bid, ask, last, band_time, last_time, fetched_at,
  age} ...]}`. The verdict is "agree" only if at least one source says "agree" and none says "disagree"; "disagree" if any says
  "disagree"; otherwise "no second quote". A source that fails, times out or has no key counts as "no second quote". The whole
  call has one deadline of 25 s on the injected clock: no try is started after it, a try gets at most the time left, and an
  answer that arrives after it is refused. `fetch` is one callable for every source (called `fetch(url, timeout)` for Cboe and
  `fetch(url, timeout, headers)` for the keyed ones) or a dict by source name.
- Live probe (needs the owner's secrets file): `python -m valedia_trader.data_and_fees.second_quote probe SPY XLE`.
"""
from __future__ import annotations

import argparse
import http.client
import json
import math
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from fractions import Fraction
from typing import Any, Callable
from zoneinfo import ZoneInfo

__all__ = ["cboe_quote", "compare", "valid_symbol", "probe", "main", "urllib_fetch", "BASE_URL", "MAX_SYMBOLS",
           "alpaca_quote", "finnhub_quote", "judge_quote", "second_opinion", "keyed_fetch"]

BASE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/%s.json"
ALLOWED_HOSTS = frozenset({"cdn.cboe.com", "cdn-api.cboe.com"})
USER_AGENT = "second-quote-check/1.0"
SOURCE = "cboe-delayed"
MAX_TRIES = 2
RETRY_WAIT_SECONDS = 2.0
TIMEOUT_SECONDS = 5.0
DEADLINE_SECONDS = 10.0                 # one try, redirects included
MAX_FETCH_AGE_SECONDS = 120             # the quote was fetched at most this long before `now`
MAX_SNAPSHOT_SKEW_SECONDS = 120         # the answer's snapshot time is this close to the fetch time
DEFAULT_MAX_AGE_SECONDS = 1800          # the last trade is at most this old
MAX_BODY_BYTES = 65536                  # real answers are about 520 bytes
MAX_REDIRECTS = 2
MAX_PRICE = 1e7
MAX_SYMBOLS = 10
FUTURE_SLACK_SECONDS = 300              # a trade this far ahead of `now` is clock skew, more is not trusted
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
BAND_MAX_AGE_SECONDS = 120              # a licensed quote's band time is at most this long before `now`
BAND_FUTURE_SLACK_SECONDS = 5           # ... and at most this long after it
BAND_MAX_WIDTH = 0.005                  # ... and the band no wider than this fraction of its mid
FETCH_AHEAD_SLACK_SECONDS = 30          # the fetch happens after the caller took `now`: at most this long after
OVERALL_DEADLINE_SECONDS = 25.0         # one deadline for a whole `second_opinion`
MAX_KEY_CHARS = 256
SOURCE_NAMES = ("alpaca", "finnhub", "cboe")
MAX_TIME = 253402300799                 # 9999-12-31, the latest time taken from a licensed answer
NEW_YORK = ZoneInfo("America/New_York")

_SYMBOL = re.compile(r"[A-Z]{1,6}(?:[.-][A-Z])?")
_TRADE_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")
_SNAPSHOT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}")
_RFC3339 = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,9}))?Z")
_SYM = _SYMBOL.pattern
_SERVICES = {
    "alpaca": {"url": "https://data.alpaca.markets/v2/stocks/snapshots?symbols=%s&feed=iex",
               "regex": re.compile(r"https://data\.alpaca\.markets/v2/stocks/snapshots\?symbols=(" + _SYM + r")&feed=iex"),
               "headers": (("APCA-API-KEY-ID", "ALPACA_API_KEY"), ("APCA-API-SECRET-KEY", "ALPACA_SECRET_KEY")), "source": "alpaca-iex"},
    "finnhub": {"url": "https://finnhub.io/api/v1/quote?symbol=%s",
                "regex": re.compile(r"https://finnhub\.io/api/v1/quote\?symbol=(" + _SYM + ")"),
                "headers": (("X-Finnhub-Token", "FINNHUB_API_KEY"),), "source": "finnhub"},
}


def valid_symbol(symbol: Any) -> bool:
    """1 to 6 capital letters, optionally one dot or hyphen and one capital letter (a share class: BRK.B)."""
    return type(symbol) is str and _SYMBOL.fullmatch(symbol) is not None


# ---- the transport: one GET, two hosts, a size cap

def _allowed_url(url: Any) -> bool:
    """An https URL on one of the two Cboe hosts: no user info, no port, no odd characters."""
    if type(url) is not str or not url.isascii() or any(ord(c) <= 0x20 or ord(c) == 0x7F or c == "\\" for c in url):
        return False
    try:
        parts = urllib.parse.urlsplit(url)
        return (parts.scheme == "https" and parts.hostname in ALLOWED_HOSTS and parts.username is None
                and parts.password is None and parts.port is None and "@" not in parts.netloc and ":" not in parts.netloc)
    except ValueError:
        return False


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Stops urllib following redirects: `urllib_fetch` follows them itself, under its deadline, to the two hosts only."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    def http_error_302(self, req, fp, code, msg, headers):
        return None                                                      # the redirect then surfaces as an HTTPError

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _shut(sock) -> None:
    for action in (lambda: sock.shutdown(socket.SHUT_RDWR), sock.close):
        try:
            action()
        except Exception:
            pass


class _Budget:
    """One deadline for a whole request: `check()` raises TimeoutError once `seconds` have passed on `clock` (or the
    watchdog has fired); a timer thread shuts every registered socket at the deadline in real time, whatever the server
    is doing. Call `cancel()` when the request is over."""
    def __init__(self, clock: Callable[[], float], seconds: float):
        self.clock, self.end = clock, clock() + seconds
        self.fired = threading.Event()
        self._socks: list = []
        self._lock = threading.Lock()
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True
        self._timer.start()

    def remaining(self) -> float:
        return self.end - self.clock()

    def check(self) -> None:
        if self.fired.is_set() or self.remaining() <= 0:
            raise TimeoutError("the request passed its %g-second deadline" % DEADLINE_SECONDS)

    def register(self, sock) -> None:
        with self._lock:
            if not self.fired.is_set():
                self._socks.append(sock)
                return
        _shut(sock)                                                      # registered after the deadline: closed at once

    def _fire(self) -> None:
        with self._lock:
            self.fired.set()
            socks, self._socks = self._socks, []
        for sock in socks:
            _shut(sock)

    def cancel(self) -> None:
        self._timer.cancel()


def _tls_context() -> ssl.SSLContext:
    """The TLS context of the real opener, built by hand: server and host name verified, TLS 1.2 at least, the ALPN and
    post-handshake settings `http.client` would add, and, unlike `ssl.create_default_context`, no SSLKEYLOGFILE (that
    function opens the file the variable names and logs session keys to it)."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_default_certs(ssl.Purpose.SERVER_AUTH)
    context.set_alpn_protocols(["http/1.1"])
    if context.post_handshake_auth is not None:
        context.post_handshake_auth = True
    return context


_lookup_guard = threading.Lock()
_pending_lookup: threading.Thread | None = None


def _resolve(host: str, port: int, budget: "_Budget | None") -> list:
    """`socket.getaddrinfo` inside the request's deadline. The call cannot be interrupted, so it runs in a daemon thread
    that is waited for only as long as the budget allows; past that the try fails (TimeoutError) and the thread is left to
    finish alone. Only one such thread at a time: while an abandoned one is alive, the next lookup fails at once."""
    global _pending_lookup
    if budget is not None:
        budget.check()
    box: dict = {}

    def work():
        try:
            box["result"] = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        except BaseException as e:                                      # handed back to the waiting try
            box["error"] = e
    with _lookup_guard:
        if _pending_lookup is not None and _pending_lookup.is_alive():
            raise TimeoutError("an earlier name lookup is still waiting")
        thread = threading.Thread(target=work, name="second-quote-lookup", daemon=True)
        _pending_lookup = thread
        thread.start()
    thread.join(max(budget.remaining(), 0.0) if budget is not None else DEADLINE_SECONDS)
    if thread.is_alive():
        raise TimeoutError("the name lookup did not answer within the deadline")
    if "error" in box:
        raise box["error"]
    return box["result"]


def _connect_timeout(timeout: Any, budget: "_Budget | None") -> float:
    seconds = float(timeout) if type(timeout) in (int, float) and timeout > 0 else DEADLINE_SECONDS
    return min(seconds, max(budget.remaining(), 0.001)) if budget is not None else seconds


def _connect(address, timeout, source_address, budget: "_Budget | None"):
    """`socket.create_connection` with the lookup inside the deadline and every socket under the watchdog."""
    host, port = address
    error: OSError | None = None
    for family, kind, proto, _canonical, target in _resolve(host, port, budget):
        sock = None
        try:
            sock = socket.socket(family, kind, proto)
            if budget is not None:
                budget.register(sock)
            sock.settimeout(_connect_timeout(timeout, budget))
            if source_address:
                sock.bind(source_address)
            sock.connect(target)
            return sock
        except OSError as e:
            error = e
            if sock is not None:
                sock.close()
            if budget is not None and (budget.fired.is_set() or budget.remaining() <= 0):
                break
    try:
        raise error if error is not None else OSError("getaddrinfo returns an empty list")
    finally:
        error = None


class _Conn(http.client.HTTPSConnection):
    """An HTTPS connection whose lookup, connection and sockets (the raw one and the TLS one) are under the request's budget."""
    _budget: _Budget | None = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = lambda address, timeout, source_address: _connect(address, timeout, source_address, self._budget)

    def connect(self):
        super().connect()
        if self._budget is not None:
            self._budget.register(self.sock)


class _BudgetHTTPS(urllib.request.HTTPSHandler):
    def __init__(self):
        super().__init__(context=_tls_context())

    def https_open(self, req):
        budget = getattr(req, "_budget", None)

        def connection(host, **kwargs):
            conn = _Conn(host, **kwargs)
            conn._budget = budget
            return conn
        return self.do_open(connection, req, context=self._context)


def _build_opener(https_handler=None) -> urllib.request.OpenerDirector:
    """An opener with no proxy from the environment, no automatic redirects, and (by default) the budgeted HTTPS handler."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects(),
                                       https_handler if https_handler is not None else _BudgetHTTPS())


def _read_capped(f, limit: int, budget: _Budget | None = None) -> bytes:
    """Read the whole of `f` if it is at most `limit` bytes, never more than `limit + 1` bytes of it; ValueError if
    longer. With a budget, its deadline is checked before every read (a read gives what has arrived, `read1`, so a body
    that trickles is looked at again and again) and once more at the end."""
    reader = getattr(f, "read1", None) or f.read
    chunks, total = [], 0
    while True:
        if budget is not None:
            budget.check()
        chunk = reader(min(8192, limit + 1 - total))
        if not chunk:
            if budget is not None:
                budget.check()
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise ValueError("the answer is longer than %d bytes" % limit)


def urllib_fetch(url: str, timeout: float, opener: urllib.request.OpenerDirector | None = None,
                 clock: Callable[[], float] = time.monotonic, deadline: float = DEADLINE_SECONDS) -> bytes:
    """GET `url` (https, a Cboe host) and return the body, at most MAX_BODY_BYTES, within one `deadline` for the whole
    request, redirects included (TimeoutError when it passes). Redirects are followed here, at most MAX_REDIRECTS and only
    to https on the two Cboe hosts; a redirect's body is never read. Anything else raises."""
    if not _allowed_url(url):
        raise ValueError("not a Cboe https URL")
    opener = opener or _build_opener()
    budget = _Budget(clock, deadline)
    try:
        current = url
        for hop in range(MAX_REDIRECTS + 1):
            budget.check()
            req = urllib.request.Request(current, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, method="GET")
            req._budget = budget
            try:
                with opener.open(req, timeout=min(float(timeout), budget.remaining())) as resp:
                    status = getattr(resp, "status", None) or resp.getcode()
                    if status != 200:
                        raise urllib.error.HTTPError(current, status, "not 200", None, None)
                    if not _allowed_url(resp.geturl()):
                        raise ValueError("answered from another place")
                    return _read_capped(resp, MAX_BODY_BYTES, budget)
            except urllib.error.HTTPError as e:
                location = e.headers.get("Location") if e.headers is not None else None
                if e.code not in REDIRECT_CODES or hop == MAX_REDIRECTS or not location:
                    raise
                target = urllib.parse.urljoin(current, location)
                if not _allowed_url(target):
                    raise
                e.close()                                                # its body is not read
                current = target
        raise AssertionError("unreachable")                              # the loop returns or raises
    except Exception as e:
        if budget.fired.is_set() or budget.remaining() <= 0 or isinstance(getattr(e, "reason", None), TimeoutError):
            raise TimeoutError("the request passed its deadline") from None      # urllib wraps a stalled connect or lookup
        raise
    finally:
        budget.cancel()


def _valid_key(key: Any) -> bool:
    """A key that can go in a header: 1 to 256 printable ASCII characters, no space, no control character."""
    return type(key) is str and 1 <= len(key) <= MAX_KEY_CHARS and all(0x21 <= ord(c) <= 0x7E for c in key)


def _keyed_service(url: Any, headers: Any) -> str:
    """The service name if `url` is exactly one of the two keyed requests and `headers` are exactly that service's keys;
    ValueError (with no key in its text) otherwise."""
    if type(url) is not str:
        raise ValueError("not a keyed-source request")
    for name, spec in _SERVICES.items():
        if spec["regex"].fullmatch(url) is not None:
            break
    else:
        raise ValueError("not a keyed-source request")
    wanted = {header for header, _ in spec["headers"]}
    if type(headers) is not dict or set(headers) != wanted or not all(_valid_key(v) for v in headers.values()):
        raise ValueError("the headers are not exactly the keys of this service")
    return name


def keyed_fetch(url: str, timeout: float, headers: dict, opener: urllib.request.OpenerDirector | None = None,
                clock: Callable[[], float] = time.monotonic, deadline: float = DEADLINE_SECONDS) -> bytes:
    """GET one of the two keyed requests and return the body, at most MAX_BODY_BYTES, within one `deadline` (TimeoutError
    when it passes). The URL must be exactly the Alpaca or the Finnhub request and `headers` exactly that service's keys
    (ValueError before any request otherwise); the keys travel only as unredirected request headers, only to their own host.
    No redirect is followed (it raises the HTTPError), a status other than 200 raises, and no text of a raised error holds
    a key."""
    _keyed_service(url, headers)
    opener = opener or _build_opener()
    budget = _Budget(clock, deadline)
    try:
        budget.check()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, method="GET")
        for name, value in headers.items():
            req.add_unredirected_header(name, value)
        req._budget = budget
        with opener.open(req, timeout=min(float(timeout), budget.remaining())) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if status != 200:
                raise urllib.error.HTTPError(url, status, "not 200", None, None)
            if resp.geturl() != url:
                raise ValueError("answered from another place")
            return _read_capped(resp, MAX_BODY_BYTES, budget)
    except Exception as e:
        if isinstance(e, urllib.error.HTTPError):
            try:
                e.close()                                                # its body is not read
            except Exception:
                pass
        if budget.fired.is_set() or budget.remaining() <= 0 or isinstance(getattr(e, "reason", None), TimeoutError):
            raise TimeoutError("the request passed its deadline") from None
        raise
    finally:
        budget.cancel()


# ---- the reader

def _epoch(x: Any) -> float | None:
    """x as seconds if it is a finite int or float (not a boolean), else None."""
    if type(x) not in (int, float):
        return None
    try:
        f = float(x)
    except OverflowError:
        return None
    return f if math.isfinite(f) else None


def _number(x: Any) -> float | None:
    """x as a float if it is a JSON number (not a boolean), finite, above 0 and at most MAX_PRICE; else None."""
    if type(x) not in (int, float):
        return None
    try:
        f = float(x)
    except OverflowError:
        return None
    return f if math.isfinite(f) and 0 < f <= MAX_PRICE else None


def _no_repeated_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("a repeated key")
        out[key] = value
    return out


def _snapshot(text: Any) -> float | None:
    """The answer's `timestamp` ("YYYY-MM-DD HH:MM:SS", UTC) in seconds, or None."""
    if type(text) is not str or _SNAPSHOT.fullmatch(text) is None:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _parse(raw: Any, symbol: str, fetched_at: float) -> dict | None:
    if type(raw) is not bytes or len(raw) > MAX_BODY_BYTES:
        return None
    try:
        # NaN and Infinity parse, and `_number` then refuses them; a repeated key anywhere makes the answer unusable
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_repeated_keys)
        if type(doc) is not dict or type(doc.get("data")) is not dict:
            return None
        data = doc["data"]
        if data.get("symbol") != symbol or ("symbol" in doc and doc["symbol"] != symbol):
            return None
        last, bid, ask = _number(data.get("current_price")), _number(data.get("bid")), _number(data.get("ask"))
        if last is None and (bid is None or ask is None):
            return None
        when = data.get("last_trade_time")
        if type(when) is not str or _TRADE_TIME.fullmatch(when) is None or _trade_time(when) is None:
            when = None
        return {"symbol": symbol, "bid": bid, "ask": ask, "last": last, "last_trade_time": when,
                "snapshot_at": _snapshot(doc.get("timestamp")), "fetched_at": fetched_at, "source": SOURCE}
    except Exception:                                                    # bad JSON, bad UTF-8, too deep, anything
        return None


def _fetch_tries(url: str, fetch: Callable, clock: Callable[[], float], sleep: Callable[[float], Any],
                 headers: dict | None = None, deadline_at: float | None = None) -> tuple[Any, float | None]:
    """(raw, fetched_at) of at most MAX_TRIES tries of `fetch(url, timeout)` (`fetch(url, timeout, headers)` when there are
    headers), RETRY_WAIT_SECONDS apart, TIMEOUT_SECONDS each, retried only when the fetch itself fails or takes more than
    DEADLINE_SECONDS on `clock`; a 4xx answer is final. With `deadline_at` (seconds on `clock`) no try starts after it, a
    try gets at most the time left, and an answer that arrives after it is refused. (None, None) when nothing came."""
    for attempt in range(MAX_TRIES):
        try:
            started = _epoch(clock())
            if started is None:
                raise ValueError("the clock gave no time")
            timeout = TIMEOUT_SECONDS
            if deadline_at is not None:
                left = deadline_at - started
                if left <= 0:
                    return None, None
                timeout = min(TIMEOUT_SECONDS, left)
            raw = fetch(url, timeout) if headers is None else fetch(url, timeout, dict(headers))
            fetched_at = _epoch(clock())
            if fetched_at is None:
                raise ValueError("the clock gave no time")
            if fetched_at - started > DEADLINE_SECONDS or (deadline_at is not None and fetched_at > deadline_at):
                raise TimeoutError("the try took longer than its deadline")
            return raw, fetched_at
        except Exception as e:
            if (isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500) or attempt + 1 >= MAX_TRIES:
                return None, None
            if deadline_at is not None:
                after = _epoch(clock())
                if after is None or after + RETRY_WAIT_SECONDS >= deadline_at:
                    return None, None
            sleep(RETRY_WAIT_SECONDS)
    return None, None


def cboe_quote(symbol: str, fetch: Callable[[str, float], Any] = urllib_fetch, clock: Callable[[], float] = time.time,
               sleep: Callable[[float], Any] = time.sleep, *, deadline_at: float | None = None) -> dict | None:
    """The delayed quote of `symbol` from Cboe as {symbol, bid, ask, last, last_trade_time, snapshot_at, fetched_at,
    source}, or None. `snapshot_at` is the answer's own `timestamp` as UTC seconds (None if missing or unreadable).

    One GET of BASE_URL; at most MAX_TRIES tries, RETRY_WAIT_SECONDS apart, TIMEOUT_SECONDS each, retried only when
    the fetch itself fails or takes more than DEADLINE_SECONDS on `clock`. An answer that is read but unusable is None at
    once. ValueError (before any request) only for a symbol that fails `valid_symbol`. `deadline_at` (seconds on `clock`)
    is the overall deadline of a `second_opinion`."""
    if not valid_symbol(symbol):
        raise ValueError("not a symbol this module asks for: %r" % (symbol,))
    raw, fetched_at = _fetch_tries(BASE_URL % symbol, fetch, clock, sleep, None, deadline_at)
    if raw is None or fetched_at is None:
        return None
    return _parse(raw, symbol, fetched_at)


# ---- the licensed readers: Alpaca and Finnhub

def _default_credentials(name: str) -> str:
    from valedia_trader.core import secrets
    return secrets.secret(name)


def _key_headers(service: str, credentials: Callable[[str], Any] | None) -> dict | None:
    """The headers of `service` from the credentials, or None if any key is missing, raises or cannot be a header value."""
    headers = {}
    for header, name in _SERVICES[service]["headers"]:
        try:
            key = (credentials or _default_credentials)(name)
        except Exception:
            return None
        if not _valid_key(key):
            return None
        headers[header] = key
    return headers


def _rfc3339(text: Any) -> float | None:
    """An RFC 3339 UTC time with up to nine decimals ("2026-11-13T21:00:00.092799711Z") in seconds, or None."""
    if type(text) is not str:
        return None
    m = _RFC3339.fullmatch(text)
    if m is None:
        return None
    try:
        whole = datetime(*(int(g) for g in m.groups()[:6]), tzinfo=timezone.utc).timestamp()
    except (ValueError, OverflowError, OSError):
        return None
    return whole + (int(m.group(7)) / 10 ** len(m.group(7)) if m.group(7) else 0.0)


def _parse_alpaca(raw: Any, symbol: str, fetched_at: float) -> dict | None:
    if type(raw) is not bytes or len(raw) > MAX_BODY_BYTES:
        return None
    try:
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_repeated_keys)
        block = doc.get(symbol) if type(doc) is dict else None
        if type(block) is not dict:
            return None
        quote = block.get("latestQuote") if type(block.get("latestQuote")) is dict else {}
        trade = block.get("latestTrade") if type(block.get("latestTrade")) is dict else {}
        bid, ask, last = _number(quote.get("bp")), _number(quote.get("ap")), _number(trade.get("p"))
        if last is None and (bid is None or ask is None):
            return None
        return {"symbol": symbol, "bid": bid, "ask": ask, "band_time": _rfc3339(quote.get("t")), "last": last,
                "last_time": _rfc3339(trade.get("t")), "fetched_at": fetched_at, "source": _SERVICES["alpaca"]["source"]}
    except Exception:
        return None


def _parse_finnhub(raw: Any, symbol: str, fetched_at: float) -> dict | None:
    if type(raw) is not bytes or len(raw) > MAX_BODY_BYTES:
        return None
    try:
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_repeated_keys)
        if type(doc) is not dict:
            return None
        last, when = _number(doc.get("c")), _epoch(doc.get("t"))
        if last is None or when is None or not 0 < when <= MAX_TIME:
            return None                                                   # an unknown symbol answers with zeros
        return {"symbol": symbol, "bid": None, "ask": None, "band_time": None, "last": last, "last_time": float(when),
                "fetched_at": fetched_at, "source": _SERVICES["finnhub"]["source"]}
    except Exception:
        return None


_PARSERS = {"alpaca": _parse_alpaca, "finnhub": _parse_finnhub}


def _keyed_quote(service: str, symbol: str, fetch: Callable, clock: Callable[[], float], sleep: Callable[[float], Any],
                 credentials: Callable[[str], Any] | None, deadline_at: float | None) -> dict | None:
    if not valid_symbol(symbol):
        raise ValueError("not a symbol this module asks for: %r" % (symbol,))
    headers = _key_headers(service, credentials)
    if headers is None:
        return None
    raw, fetched_at = _fetch_tries(_SERVICES[service]["url"] % urllib.parse.quote(symbol, safe=""), fetch, clock, sleep, headers, deadline_at)
    if raw is None or fetched_at is None:
        return None
    return _PARSERS[service](raw, symbol, fetched_at)


def alpaca_quote(symbol: str, *, fetch: Callable = keyed_fetch, clock: Callable[[], float] = time.time,
                 credentials: Callable[[str], Any] | None = None, sleep: Callable[[float], Any] = time.sleep,
                 deadline_at: float | None = None) -> dict | None:
    """The real-time IEX quote of `symbol` from Alpaca in the normal form {symbol, bid, ask, band_time, last, last_time,
    fetched_at, source}, or None (no key, a failed or unusable answer). ValueError only for a symbol that fails `valid_symbol`."""
    return _keyed_quote("alpaca", symbol, fetch, clock, sleep, credentials, deadline_at)


def finnhub_quote(symbol: str, *, fetch: Callable = keyed_fetch, clock: Callable[[], float] = time.time,
                  credentials: Callable[[str], Any] | None = None, sleep: Callable[[float], Any] = time.sleep,
                  deadline_at: float | None = None) -> dict | None:
    """The real-time last price of `symbol` from Finnhub in the normal form (no bid, ask or band time), or None."""
    return _keyed_quote("finnhub", symbol, fetch, clock, sleep, credentials, deadline_at)


# ---- the comparison

def _exact(x: float) -> Fraction:
    """The decimal a float prints as, exactly (so 752.55 is 75255/100, not the nearest binary number)."""
    return Fraction(repr(float(x)))


def _trade_time(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=NEW_YORK)
    except ValueError:
        return None


def _trade_instants(text: Any) -> tuple[float, ...] | None:
    """Every instant (seconds) a New York wall-clock trade time can mean: one for an ordinary time, two for the repeated
    hour of the November change and for the missing hour of the March one. None if the text is not a readable time."""
    if type(text) is not str or _TRADE_TIME.fullmatch(text) is None:
        return None
    wall = _trade_time(text)
    if wall is None:
        return None
    try:
        return tuple(wall.replace(fold=fold).timestamp() for fold in (0, 1))
    except (OverflowError, OSError, ValueError):
        return None


def _why_not_fresh(second: dict, now: float, max_age: float) -> str | None:
    """None if the quote is fresh by all three rules (see the module docstring), else the reason it is not."""
    fetched, snapshot = _epoch(second.get("fetched_at")), _epoch(second.get("snapshot_at"))
    if fetched is None:
        return "no fetch time"
    if not 0 <= now - fetched <= MAX_FETCH_AGE_SECONDS:
        return "not fetched within the last %d seconds" % MAX_FETCH_AGE_SECONDS
    if snapshot is None:
        return "no snapshot time"
    if abs(snapshot - fetched) > MAX_SNAPSHOT_SKEW_SECONDS:
        return "the snapshot time is not within %d seconds of the fetch" % MAX_SNAPSHOT_SKEW_SECONDS
    instants = _trade_instants(second.get("last_trade_time"))
    if instants is None:
        return "no readable trade time"
    if not all(now - max_age <= u <= now + FUTURE_SLACK_SECONDS for u in instants):
        return "the last trade is more than %g seconds old or %d seconds ahead" % (max_age, FUTURE_SLACK_SECONDS)
    return None


def _verdict(verdict: str, difference: float | None, basis: str) -> dict:
    return {"verdict": verdict, "difference": difference, "basis": basis}


def _validated(tolerance: Any, max_age_seconds: Any, now: Any) -> float:
    """`now` in seconds; ValueError for a tolerance not in 0..1, an age limit that is not a positive number, or a `now` that
    is not an aware datetime."""
    if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or not 0 <= tolerance <= 1:
        raise ValueError("tolerance must be a number from 0 to 1")
    if type(max_age_seconds) not in (int, float) or not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be a positive number")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    try:
        return now.timestamp()
    except (OverflowError, OSError, ValueError):
        raise ValueError("now is not a usable time") from None


def compare(first_price: Any, second: Any, *, symbol: Any, now: datetime, tolerance: float = 0.01,
            max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS) -> dict:
    """Judge the first source's price against the second quote (what `cboe_quote` returns), at the caller's time `now`.

    {verdict: "agree" | "disagree" | "no second quote", difference, basis}. "No second quote" unless the quote is for
    `symbol` and fresh by all three rules in the module docstring (trade age against `now` and `max_age_seconds`, fetch
    age, snapshot time), its bid and ask, when both are usable, are not crossed (bid above ask), and its last price is not
    outside them by more than the tolerance. With a usable bid and ask the first price must lie from bid less `tolerance` x
    mid to ask plus `tolerance` x mid (inclusive); otherwise from last less to last plus `tolerance` x last. `difference` is
    (first - mid) / mid, or (first - last) / last, as a fraction. `now` is an aware datetime: this function reads no
    clock. ValueError for a tolerance not in 0..1, an age limit that is not a positive number, or a `now` that is not aware."""
    now_ts = _validated(tolerance, max_age_seconds, now)
    if type(second) is not dict:
        return _verdict("no second quote", None, "no second quote")
    if type(symbol) is not str or second.get("symbol") != symbol:
        return _verdict("no second quote", None, "the quote is for another symbol")
    why = _why_not_fresh(second, now_ts, float(max_age_seconds))
    if why is not None:
        return _verdict("no second quote", None, why)
    bid, ask, last = _number(second.get("bid")), _number(second.get("ask")), _number(second.get("last"))
    if bid is not None and ask is not None and bid > ask:
        return _verdict("no second quote", None, "bid above ask: crossed, suspect data")
    band = bid is not None and ask is not None
    if not band and last is None:
        return _verdict("no second quote", None, "no usable bid and ask, and no usable last price")
    tol = _exact(tolerance)
    if band:
        b, a = _exact(bid), _exact(ask)
        mid = (b + a) / 2
        lo, hi = b - tol * mid, a + tol * mid
        if last is not None and not lo <= _exact(last) <= hi:
            return _verdict("no second quote", None, "the last trade contradicts the bid and ask")
    first = _number(first_price)
    if first is None:
        return _verdict("disagree", None, "the first price is not usable")
    f = _exact(first)
    if band:
        return _verdict("agree" if lo <= f <= hi else "disagree", float((f - mid) / mid), "bid/ask band")
    ref = _exact(last)
    return _verdict("agree" if abs(f - ref) <= tol * ref else "disagree", float((f - ref) / ref), "last price")


def judge_quote(first_price: Any, quote: Any, *, symbol: Any, now: datetime, tolerance: float = 0.01,
                max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS) -> dict:
    """Judge the first source's price against a licensed quote in the normal form (`alpaca_quote`, `finnhub_quote`), by the
    quote's own times: {verdict: "agree" | "disagree" | "no second quote", difference, basis}. See the module docstring for the
    rules. The quote is judged at the later of `now` and its fetch time; `now` is an aware datetime and no clock is read.
    ValueError for a tolerance not in 0..1, an age limit that is not a positive number, or a `now` that is not aware."""
    now_ts = _validated(tolerance, max_age_seconds, now)
    if type(quote) is not dict:
        return _verdict("no second quote", None, "no second quote")
    if type(symbol) is not str or quote.get("symbol") != symbol:
        return _verdict("no second quote", None, "the quote is for another symbol")
    fetched = _epoch(quote.get("fetched_at"))
    if fetched is None:
        return _verdict("no second quote", None, "no fetch time")
    if not -FETCH_AHEAD_SLACK_SECONDS <= now_ts - fetched <= MAX_FETCH_AGE_SECONDS:
        return _verdict("no second quote", None, "not fetched within the last %d seconds" % MAX_FETCH_AGE_SECONDS)
    at = max(now_ts, fetched)
    bid, ask, last = _number(quote.get("bid")), _number(quote.get("ask")), _number(quote.get("last"))
    if bid is not None and ask is not None and bid > ask:
        return _verdict("no second quote", None, "bid above ask: crossed, suspect data")
    band_time, last_time = _epoch(quote.get("band_time")), _epoch(quote.get("last_time"))
    tol = _exact(tolerance)
    band_ok, why_not = False, "no bid and ask"
    if bid is not None and ask is not None:
        b, a = _exact(bid), _exact(ask)
        mid = (b + a) / 2
        if band_time is None or not -BAND_FUTURE_SLACK_SECONDS <= at - band_time <= BAND_MAX_AGE_SECONDS:
            why_not = "the bid and ask are not from the last %d seconds" % BAND_MAX_AGE_SECONDS
        elif a - b > _exact(BAND_MAX_WIDTH) * mid:
            why_not = "the bid and ask are wider than %g of their mid" % BAND_MAX_WIDTH
        else:
            band_ok = True
    last_ok = last is not None and last_time is not None and -FUTURE_SLACK_SECONDS <= at - last_time <= float(max_age_seconds)
    if not band_ok and not last_ok:
        return _verdict("no second quote", None, "nothing usable: " + why_not + ", and no recent last price")
    if band_ok:
        lo, hi = b - tol * mid, a + tol * mid
        if last_ok and not lo <= _exact(last) <= hi:
            return _verdict("no second quote", None, "the last trade contradicts the bid and ask")
    first = _number(first_price)
    if first is None:
        return _verdict("disagree", None, "the first price is not usable")
    f = _exact(first)
    if band_ok:
        return _verdict("agree" if lo <= f <= hi else "disagree", float((f - mid) / mid), "bid/ask band")
    ref = _exact(last)
    return _verdict("agree" if abs(f - ref) <= tol * ref else "disagree", float((f - ref) / ref), "last price")


def _consensus(verdicts: list) -> str:
    if "disagree" in verdicts:
        return "disagree"
    return "agree" if "agree" in verdicts else "no second quote"


def _source_names(sources: Any) -> list:
    """The source names, repeats dropped; ValueError for anything but a list or tuple of the known names."""
    if type(sources) not in (list, tuple) or not all(type(n) is str and n in SOURCE_NAMES for n in sources):
        raise ValueError("sources must be names from %s" % (SOURCE_NAMES,))
    return list(dict.fromkeys(sources))


def _fetch_for(fetch: Any, name: str, clock: Callable[[], float], deadline_at: float | None) -> Callable:
    """The injected fetch for `name` (one callable for all, or a dict by name), else the module's own, bounded by what is
    left of the overall deadline."""
    chosen = fetch.get(name) if isinstance(fetch, dict) else fetch
    if chosen is not None:
        return chosen
    default = urllib_fetch if name == "cboe" else keyed_fetch

    def run(url, timeout, *args):
        left = DEADLINE_SECONDS if deadline_at is None else max(min(DEADLINE_SECONDS, deadline_at - clock()), 0.001)
        return default(url, timeout, *args, deadline=left)
    return run


def _entry(name: str, verdict: str, basis: str, difference: float | None, quote: dict | None, at: float | None,
           band_time: float | None, last_time: float | None) -> dict:
    used = band_time if basis == "bid/ask band" and band_time is not None else last_time
    return {"name": name, "verdict": verdict, "basis": basis, "difference": difference,
            "bid": quote.get("bid") if quote else None, "ask": quote.get("ask") if quote else None,
            "last": quote.get("last") if quote else None, "band_time": band_time, "last_time": last_time,
            "fetched_at": quote.get("fetched_at") if quote else None,
            "age": (at - used) if (at is not None and used is not None and verdict != "no second quote") else None,
            "last_trade_time": quote.get("last_trade_time") if quote and name == "cboe" else None}


def _ask_source(name: str, first_price: Any, symbol: str, now: datetime, now_ts: float, tolerance: float, max_age_seconds: float,
                fetch: Any, clock: Callable[[], float], sleep: Callable[[float], Any], credentials: Any, deadline_at: float | None) -> dict:
    nothing = _entry(name, "no second quote", "no usable quote (no key, a failed request or an unusable answer)", None, None, None, None, None)
    try:
        use = _fetch_for(fetch, name, clock, deadline_at)
        if name == "cboe":
            quote = cboe_quote(symbol, fetch=use, clock=clock, sleep=sleep, deadline_at=deadline_at)
            if quote is None:
                return nothing
            fetched = _epoch(quote.get("fetched_at"))
            at = fetched if fetched is not None and 0 < fetched - now_ts <= FETCH_AHEAD_SLACK_SECONDS else now_ts
            res = compare(first_price, quote, symbol=symbol, now=datetime.fromtimestamp(at, timezone.utc), tolerance=tolerance,
                          max_age_seconds=max_age_seconds)
            instants = _trade_instants(quote.get("last_trade_time"))
            when = instants[0] if instants and len(set(instants)) == 1 else None
            return _entry(name, res["verdict"], res["basis"], res["difference"], quote, at, None, when)
        reader = alpaca_quote if name == "alpaca" else finnhub_quote
        quote = reader(symbol, fetch=use, clock=clock, credentials=credentials, sleep=sleep, deadline_at=deadline_at)
        if quote is None:
            return nothing
        res = judge_quote(first_price, quote, symbol=symbol, now=now, tolerance=tolerance, max_age_seconds=max_age_seconds)
        fetched = _epoch(quote.get("fetched_at"))
        return _entry(name, res["verdict"], res["basis"], res["difference"], quote, max(now_ts, fetched) if fetched is not None else now_ts,
                      quote.get("band_time"), quote.get("last_time"))
    except Exception:                                                  # a source never takes the call down
        return _entry(name, "no second quote", "the source failed", None, None, None, None, None)


def second_opinion(first_price: Any, symbol: str, *, now: datetime, sources: Any = ("alpaca", "finnhub"), tolerance: float = 0.01,
                   max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS, fetch: Any = None, clock: Callable[[], float] = time.time,
                   sleep: Callable[[float], Any] = time.sleep, credentials: Callable[[str], Any] | None = None,
                   deadline_seconds: float = OVERALL_DEADLINE_SECONDS) -> dict:
    """One answer from several sources for the order path: {verdict, symbol, sources: [one entry per source asked]}.

    Each source named (alpaca, finnhub, cboe; default the first two) is asked once, in order, and judged on its own clocks
    (see the module docstring). The verdict is "agree" only if at least one source agrees and none disagrees, "disagree" if
    any disagrees, else "no second quote"; a source that fails, times out or has no key is "no second quote". The whole call
    has one deadline of `deadline_seconds` (25) on `clock`. ValueError, before any request, for a symbol that fails
    `valid_symbol`, an unknown source name, or a bad tolerance, age limit, `now` or deadline."""
    if not valid_symbol(symbol):
        raise ValueError("not a symbol this module asks for: %r" % (symbol,))
    names = _source_names(sources)
    now_ts = _validated(tolerance, max_age_seconds, now)
    if type(deadline_seconds) not in (int, float) or not math.isfinite(deadline_seconds) or deadline_seconds <= 0:
        raise ValueError("deadline_seconds must be a positive number")
    try:
        started = _epoch(clock())
    except Exception:
        started = None
    deadline_at = None if started is None else started + deadline_seconds
    entries = [_ask_source(name, first_price, symbol, now, now_ts, tolerance, max_age_seconds, fetch, clock, sleep, credentials, deadline_at)
               for name in names]
    return {"verdict": _consensus([e["verdict"] for e in entries]), "symbol": symbol, "sources": entries}


# ---- the probe

def _order_step_quotes(symbols):
    """The first source: the order step's own `minute_quotes`, looked up at call time (never copied)."""
    from valedia_trader.execution_and_risk import order_step
    return order_step.minute_quotes(symbols)


def _g(x: float) -> str:
    return format(x, ".10g")


def _first_reading(quotes: Any, symbol: str):
    """(price, time text) from the first source's {symbol: (price, seconds)}, or None."""
    try:
        price, seconds = quotes[symbol]
        price = _number(price)
        if price is None:
            return None
        if type(seconds) in (int, float) and math.isfinite(seconds):
            text = datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            text = "unknown time"
        return price, text
    except Exception:
        return None


def _shown(text: Any) -> str:
    """A line for the screen: control and format characters dropped, then the package's output guard (which masks anything
    that holds a piece of a remembered secret)."""
    clean = "".join(ch for ch in str(text) if ch.isprintable())
    try:
        from valedia_trader.core import secrets
        return secrets.redact(clean)[0]
    except Exception:
        return clean


def _iso(seconds: Any) -> str:
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return "unknown time"


def _percent(difference: Any) -> str:
    if difference is None:
        return "n/a"
    percent = round(difference * 100, 2)
    return "0.00%" if percent == 0 else "%+.2f%%" % percent


def _source_line(e: dict, show_verdict: bool) -> str:
    """One line for a source: its price or band, its own times, the age in seconds, the difference, the verdict."""
    parts = []
    if e["name"] == "cboe":
        if e["last"] is not None or e["bid"] is not None or e["ask"] is not None:
            parts.append("%s (bid %s ask %s) @ %s NY" % (_g(e["last"]) if e["last"] is not None else "-",
                                                         _g(e["bid"]) if e["bid"] is not None else "-",
                                                         _g(e["ask"]) if e["ask"] is not None else "-",
                                                         e["last_trade_time"] or "unknown time"))
    else:
        if e["bid"] is not None or e["ask"] is not None:
            parts.append("bid %s ask %s%s" % (_g(e["bid"]) if e["bid"] is not None else "-", _g(e["ask"]) if e["ask"] is not None else "-",
                                              " @ " + _iso(e["band_time"]) if e["band_time"] is not None else ""))
        if e["last"] is not None:
            parts.append("last %s @ %s" % (_g(e["last"]), _iso(e["last_time"]) if e["last_time"] is not None else "unknown time"))
    quote = "; ".join(parts) or "none"
    age = "age %d s" % round(e["age"]) if e["age"] is not None else "age n/a"
    verdict = "%s [%s]" % (e["verdict"], e["basis"]) if show_verdict else "n/a"
    return "  %-8s %s | %s | %s | %s" % (e["name"], quote, age, _percent(e["difference"]) if show_verdict else "n/a", verdict)


def probe(symbols, first_quotes: Callable[[list], Any] | None = None, fetch: Any = None, clock: Callable[[], float] = time.time,
          sleep: Callable[[float], Any] = time.sleep, out: Callable = print, *, credentials: Callable[[str], Any] | None = None,
          sources: Any = SOURCE_NAMES) -> int:
    """For each symbol, once from every source: the first source's price and time, then one line per second source (alpaca,
    finnhub, cboe: price or band, the quote's own times, the age in seconds, the difference in percent, its verdict), then the
    consensus. At most MAX_SYMBOLS; a bad list or symbol is refused (return 2) before any request. Without `credentials` the
    secrets file is loaded through `valedia_trader.core.secrets.load_env()` and the keys read from the environment."""
    emit = lambda line: out(_shown(line))      # noqa: E731
    symbols = list(symbols)
    if not symbols or len(symbols) > MAX_SYMBOLS:
        emit("refused: give 1 to %d symbols (at most %d a run)" % (MAX_SYMBOLS, MAX_SYMBOLS))
        return 2
    for s in symbols:
        if not valid_symbol(s):
            emit("refused: %r is not a symbol (1 to 6 capital letters, one optional .X or -X class suffix)" % (s,))
            return 2
    names = _source_names(sources)
    symbols = list(dict.fromkeys(symbols))
    if credentials is None:
        credentials = _default_credentials
        if any(n != "cboe" for n in names):
            try:
                from valedia_trader.core import secrets
                secrets.load_env()
            except Exception as e:
                name = type(e).__name__
                emit("note: the secrets file could not be loaded (%s); sources that need a key have none" % (
                    name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) else "an error"))
    first_error = None
    quotes: Any = {}
    try:
        quotes = (first_quotes or _order_step_quotes)(symbols)
    except Exception as e:
        first_error = type(e).__name__ if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", type(e).__name__) else "an error"
    for sym in symbols:
        reading = None if first_error else _first_reading(quotes, sym)
        if first_error:
            emit("%s  first source failed (%s)" % (sym, first_error))
        elif reading is None:
            emit("%s  no first quote" % sym)
        else:
            emit("%s  first %s @ %s" % (sym, _g(reading[0]), reading[1]))
        opinion = second_opinion(reading[0] if reading else None, sym, now=datetime.fromtimestamp(clock(), timezone.utc), sources=names,
                                 fetch=fetch, clock=clock, sleep=sleep, credentials=credentials)
        for entry in opinion["sources"]:
            emit(_source_line(entry, reading is not None))
        emit("  consensus %s" % (opinion["verdict"] if reading is not None else "n/a"))
    return 0


def main(argv=None, **kw) -> int:
    parser = argparse.ArgumentParser(prog="python -m valedia_trader.data_and_fees.second_quote",
                                     description="Cross-check the order step's price source against Cboe's delayed quotes.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("probe", help="ask every second source once per symbol and print each quote, its times and the consensus")
    p.add_argument("symbols", nargs="+", metavar="SYMBOL", help="at most %d" % MAX_SYMBOLS)
    args = parser.parse_args(argv)
    return probe(args.symbols, **kw)


if __name__ == "__main__":
    sys.exit(main())
