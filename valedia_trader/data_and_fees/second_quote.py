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
  was taken, not when the stock traded. It is not used.
- Every number came as a JSON number. Both SPY and the sector fund had a bid and an ask after the close.

Parsing is strict: every number must be a JSON number (not a string, not a boolean), finite, above 0 and at most 1e7;
anything else is None and the rest of the answer stays. The symbol in the answer must be the symbol asked for.
No result at all (None) unless a usable last price, or a usable bid and ask, remains. `cboe_quote` never raises on an
answer, however hostile; it raises ValueError only for a symbol that fails the symbol rule, before any request.

Decisions the brief left open:
- "The same New York trading day": the New York calendar date of `last_trade_time` must equal the New York date of
  `fetched_at`, and the trade must not be more than 5 minutes after `fetched_at` (a quote from the future is not
  trusted). On a weekend or a holiday that means "no second quote": it is the safe side, the order step does not trade
  then.
- A bid above the ask is not a band; the last price is used if there is one, otherwise there is no second quote.
- `compare`: an unusable first price is "disagree" (never "agree"), and a second quote that is not usable is reported
  first as "no second quote". A tolerance that is not a number from 0 to 1 raises ValueError (a programming error).
- The comparison is exact: each float is read as the decimal it prints as, so "exactly on the band" means exactly.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
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
MAX_BODY_BYTES = 65536                  # real answers are about 520 bytes
MAX_REDIRECTS = 2
MAX_PRICE = 1e7
MAX_SYMBOLS = 10
FUTURE_SLACK_SECONDS = 300
NEW_YORK = ZoneInfo("America/New_York")

_SYMBOL = re.compile(r"[A-Z]{1,6}(?:[.-][A-Z])?")
_TRADE_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")


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


class _Redirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to https on one of the two Cboe hosts, and at most MAX_REDIRECTS of them."""
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed_url(newurl):
            return None                                                  # urllib then raises the redirect as an HTTPError
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _build_opener(https_handler=None) -> urllib.request.OpenerDirector:
    """An opener with no proxy from the environment and the redirect rule above."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _Redirects(),
                                       https_handler if https_handler is not None else urllib.request.HTTPSHandler())


def _read_capped(f, limit: int) -> bytes:
    """Read the whole of `f` if it is at most `limit` bytes, never more than `limit + 1` bytes of it; ValueError if longer."""
    chunks, total = [], 0
    while True:
        chunk = f.read(min(65536, limit + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise ValueError("the answer is longer than %d bytes" % limit)


def urllib_fetch(url: str, timeout: float, opener: urllib.request.OpenerDirector | None = None) -> bytes:
    """GET `url` (https, a Cboe host) and return the body, at most MAX_BODY_BYTES. Anything else raises."""
    if not _allowed_url(url):
        raise ValueError("not a Cboe https URL")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, method="GET")
    with (opener or _build_opener()).open(req, timeout=timeout) as resp:
        status = getattr(resp, "status", None) or resp.getcode()
        if status != 200:
            raise urllib.error.HTTPError(url, status, "not 200", None, None)
        if not _allowed_url(resp.geturl()):
            raise ValueError("answered from another place")
        return _read_capped(resp, MAX_BODY_BYTES)


# ---- the reader

def _number(x: Any) -> float | None:
    """x as a float if it is a JSON number (not a boolean), finite, above 0 and at most MAX_PRICE; else None."""
    if type(x) not in (int, float):
        return None
    try:
        f = float(x)
    except OverflowError:
        return None
    return f if math.isfinite(f) and 0 < f <= MAX_PRICE else None


def _parse(raw: Any, symbol: str, fetched_at: float) -> dict | None:
    if type(raw) is not bytes or len(raw) > MAX_BODY_BYTES:
        return None
    try:
        doc = json.loads(raw.decode("utf-8"))             # NaN and Infinity parse, and `_number` then refuses them
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
                "fetched_at": fetched_at, "source": SOURCE}
    except Exception:                                                    # bad JSON, bad UTF-8, too deep, anything
        return None


def cboe_quote(symbol: str, fetch: Callable[[str, float], Any] = urllib_fetch, clock: Callable[[], float] = time.time,
               sleep: Callable[[float], Any] = time.sleep) -> dict | None:
    """The delayed quote of `symbol` from Cboe as {symbol, bid, ask, last, last_trade_time, fetched_at, source}, or None.

    One GET of BASE_URL; at most MAX_TRIES tries, RETRY_WAIT_SECONDS apart, TIMEOUT_SECONDS each, retried only when
    the fetch itself fails. An answer that is read but unusable is None at once. ValueError (before any request)
    only for a symbol that fails `valid_symbol`."""
    if not valid_symbol(symbol):
        raise ValueError("not a symbol this module asks for: %r" % (symbol,))
    url = BASE_URL % symbol
    raw = None
    for attempt in range(MAX_TRIES):
        try:
            raw = fetch(url, TIMEOUT_SECONDS)
            break
        except Exception:
            raw = None
            if attempt + 1 < MAX_TRIES:
                sleep(RETRY_WAIT_SECONDS)
    if raw is None:
        return None
    try:
        fetched_at = clock()
    except Exception:
        return None
    if type(fetched_at) not in (int, float) or not math.isfinite(fetched_at):
        return None
    return _parse(raw, symbol, float(fetched_at))


# ---- the comparison

def _exact(x: float) -> Fraction:
    """The decimal a float prints as, exactly (so 752.55 is 75255/100, not the nearest binary number)."""
    return Fraction(repr(float(x)))


def _trade_time(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=NEW_YORK)
    except ValueError:
        return None


def _is_todays_trade(text: Any, fetched_at: Any) -> bool:
    """The trade time (New York wall clock) is on the New York day of `fetched_at` and not after it by over 5 minutes."""
    if type(text) is not str or _TRADE_TIME.fullmatch(text) is None:
        return False
    if type(fetched_at) not in (int, float) or not math.isfinite(fetched_at):
        return False
    trade = _trade_time(text)
    if trade is None:
        return False
    try:
        today = datetime.fromtimestamp(fetched_at, NEW_YORK).date()
        return trade.date() == today and trade.timestamp() <= fetched_at + FUTURE_SLACK_SECONDS
    except (OverflowError, OSError, ValueError):
        return False


def _verdict(verdict: str, difference: float | None, basis: str) -> dict:
    return {"verdict": verdict, "difference": difference, "basis": basis}


def compare(first_price: Any, second: Any, tolerance: float = 0.01) -> dict:
    """Judge the first source's price against the second quote (what `cboe_quote` returns).

    {verdict: "agree" | "disagree" | "no second quote", difference, basis}. With a usable bid and ask (bid not above
    ask) the first price must lie from bid less `tolerance` x mid to ask plus `tolerance` x mid (inclusive); otherwise
    from last less to last plus `tolerance` x last. `difference` is (first - mid) / mid, or (first - last) / last, as
    a fraction. A second quote with no trade time, a trade time not on today's New York day, or no usable price is
    "no second quote". ValueError for a tolerance that is not a number from 0 to 1."""
    if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or not 0 <= tolerance <= 1:
        raise ValueError("tolerance must be a number from 0 to 1")
    if type(second) is not dict:
        return _verdict("no second quote", None, "no second quote")
    if not _is_todays_trade(second.get("last_trade_time"), second.get("fetched_at")):
        return _verdict("no second quote", None, "last trade not on today's New York day, or no trade time")
    bid, ask, last = _number(second.get("bid")), _number(second.get("ask")), _number(second.get("last"))
    band = bid is not None and ask is not None and bid <= ask
    if not band and last is None:
        return _verdict("no second quote", None, "no usable bid and ask, and no usable last price")
    first = _number(first_price)
    if first is None:
        return _verdict("disagree", None, "the first price is not usable")
    f, tol = _exact(first), _exact(tolerance)
    if band:
        b, a = _exact(bid), _exact(ask)
        mid = (b + a) / 2
        agree = b - tol * mid <= f <= a + tol * mid
        return _verdict("agree" if agree else "disagree", float((f - mid) / mid), "bid/ask band")
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
            res = compare(reading[0], second)
            diff = "%+.2f%%" % (res["difference"] * 100) if res["difference"] is not None else "n/a"
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
