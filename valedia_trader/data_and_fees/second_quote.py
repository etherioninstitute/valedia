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
happens, so a time in either hour is refused. With an age limit of an hour or more (`max_age_seconds` >= 3600, a window wider
than the hour between the readings) a time in the missing hour, or in the repeated hour, can be accepted when both readings
of it are recent.
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
- A bid above the ask is not a band; the last price is used if there is one, otherwise there is no second quote.
- `compare`: an unusable first price is "disagree" (never "agree"), and a second quote that is not usable is reported
  first as "no second quote". A tolerance that is not a number from 0 to 1, an age limit that is not a positive number, or
  a `now` that is not an aware datetime raises ValueError (a programming error).
- The comparison is exact: each float is read as the decimal it prints as, so "exactly on the band" means exactly.
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

__all__ = ["cboe_quote", "compare", "valid_symbol", "probe", "main", "urllib_fetch", "BASE_URL", "MAX_SYMBOLS"]

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
NEW_YORK = ZoneInfo("America/New_York")

_SYMBOL = re.compile(r"[A-Z]{1,6}(?:[.-][A-Z])?")
_TRADE_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")
_SNAPSHOT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}")


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


def cboe_quote(symbol: str, fetch: Callable[[str, float], Any] = urllib_fetch, clock: Callable[[], float] = time.time,
               sleep: Callable[[float], Any] = time.sleep) -> dict | None:
    """The delayed quote of `symbol` from Cboe as {symbol, bid, ask, last, last_trade_time, snapshot_at, fetched_at,
    source}, or None. `snapshot_at` is the answer's own `timestamp` as UTC seconds (None if missing or unreadable).

    One GET of BASE_URL; at most MAX_TRIES tries, RETRY_WAIT_SECONDS apart, TIMEOUT_SECONDS each, retried only when
    the fetch itself fails or takes more than DEADLINE_SECONDS on `clock`. An answer that is read but unusable is None at once. ValueError (before any request)
    only for a symbol that fails `valid_symbol`."""
    if not valid_symbol(symbol):
        raise ValueError("not a symbol this module asks for: %r" % (symbol,))
    url = BASE_URL % symbol
    raw, fetched_at = None, None
    for attempt in range(MAX_TRIES):
        try:
            started = _epoch(clock())
            raw = fetch(url, TIMEOUT_SECONDS)
            fetched_at = _epoch(clock())
            if started is None or fetched_at is None:
                raise ValueError("the clock gave no time")
            if fetched_at - started > DEADLINE_SECONDS:
                raise TimeoutError("the try took longer than its deadline")
            break
        except Exception:
            raw = None
            if attempt + 1 < MAX_TRIES:
                sleep(RETRY_WAIT_SECONDS)
    if raw is None or fetched_at is None:
        return None
    return _parse(raw, symbol, fetched_at)


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
    if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or not 0 <= tolerance <= 1:
        raise ValueError("tolerance must be a number from 0 to 1")
    if type(max_age_seconds) not in (int, float) or not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be a positive number")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    try:
        now_ts = now.timestamp()
    except (OverflowError, OSError, ValueError):
        raise ValueError("now is not a usable time") from None
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


def probe(symbols, first_quotes: Callable[[list], Any] | None = None, fetch: Callable[[str, float], Any] = urllib_fetch,
          clock: Callable[[], float] = time.time, sleep: Callable[[float], Any] = time.sleep, out: Callable = print) -> int:
    """Fetch each symbol once from both sources and print both prices and times, the difference in percent and the
    verdict, one line per symbol. At most MAX_SYMBOLS; a bad list or symbol is refused (return 2) before any request."""
    symbols = list(symbols)
    if not symbols or len(symbols) > MAX_SYMBOLS:
        out("refused: give 1 to %d symbols (at most %d a run)" % (MAX_SYMBOLS, MAX_SYMBOLS))
        return 2
    for s in symbols:
        if not valid_symbol(s):
            out("refused: %r is not a symbol (1 to 6 capital letters, one optional .X or -X class suffix)" % (s,))
            return 2
    symbols = list(dict.fromkeys(symbols))
    first_error = None
    quotes: Any = {}
    try:
        quotes = (first_quotes or _order_step_quotes)(symbols)
    except Exception as e:
        first_error = type(e).__name__ if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", type(e).__name__) else "an error"
    out("symbol  first source | Cboe delayed (second source) | difference | verdict")
    for sym in symbols:
        reading = None if first_error else _first_reading(quotes, sym)
        second = cboe_quote(sym, fetch=fetch, clock=clock, sleep=sleep)
        if first_error:
            left = "first source failed (%s)" % first_error
        elif reading is None:
            left = "no first quote"
        else:
            left = "%s @ %s" % (_g(reading[0]), reading[1])
        if second is None:
            right = "none"
        else:
            right = "%s (bid %s ask %s) @ %s NY" % (_g(second["last"]) if second["last"] is not None else "-",
                                                    _g(second["bid"]) if second["bid"] is not None else "-",
                                                    _g(second["ask"]) if second["ask"] is not None else "-",
                                                    second["last_trade_time"] or "unknown time")
        if reading is None:
            diff, verdict = "n/a", "n/a"
        else:
            res = compare(reading[0], second, symbol=sym, now=datetime.fromtimestamp(clock(), timezone.utc))
            if res["difference"] is None:
                diff = "n/a"
            else:
                percent = round(res["difference"] * 100, 2)
                diff = "0.00%" if percent == 0 else "%+.2f%%" % percent
            verdict = "%s [%s]" % (res["verdict"], res["basis"])
        out("%s  %s | %s | %s | %s" % (sym, left, right, diff, verdict))
    return 0


def main(argv=None, **kw) -> int:
    parser = argparse.ArgumentParser(prog="python -m valedia_trader.data_and_fees.second_quote",
                                     description="Cross-check the order step's price source against Cboe's delayed quotes.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("probe", help="fetch each symbol once from both sources and compare")
    p.add_argument("symbols", nargs="+", metavar="SYMBOL", help="at most %d" % MAX_SYMBOLS)
    args = parser.parse_args(argv)
    return probe(args.symbols, **kw)


if __name__ == "__main__":
    sys.exit(main())
