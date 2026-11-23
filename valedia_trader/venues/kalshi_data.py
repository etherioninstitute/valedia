"""Kalshi's public market data, read only: settled markets of a series and their price candles.

No key and no account: every request is a plain GET to the public API, and this module has no way to send
anything else. Kalshi keeps markets in two tiers. Markets settled before its "historical cutoff" live under
/historical; recent ones under the live paths. A series' settled markets are the union of both (they overlap by
a few days), and a market asked for on the wrong tier answers 404 or nothing, so `candles` tries the likely tier
first and then the other. The two tiers spell the same numbers differently (`close` against `close_dollars`,
`volume` against `volume_fp`); both are turned into one shape here.

Politeness: at most three requests a second, and a 429 or a server error is retried after a growing wait, up to
five tries in all, then it stops with a clear error.

Live data for the order-book loop (also GET only, through the same pacing and retries): `open_markets` pages the
open, non-combo markets; `orderbook` reads the best levels of a book (Kalshi sends bids only, ascending, best
last; a yes ask is one minus the best no bid, see `best_quote`); `trades` reads a market's tape between two times
in seconds, following the cursor; `series` gives a series' fee fields.

Settlement (T45-1): `market` reads one market's record, the six fields a settlement is judged on, from the live
tier and, on a 404 only, the historical tier; it never judges them (event_practice.judge_settlement does).
"""
from __future__ import annotations

import json
import time
from datetime import datetime
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

__all__ = ["BASE_URL", "HttpError", "KalshiDataError", "Pacer", "PhasePacer", "quiet_pacer", "quiet_fetch", "QUIET_WINDOW", "QUIET_TIMEOUT", "urllib_get", "normalize_market", "normalize_candle",
           "KalshiData", "normalize_levels", "best_quote", "normalize_trade", "iso_to_ts"]

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
USER_AGENT = "market-data-reader/1.0"
PRICE_KEYS = ("open", "high", "low", "close", "mean", "previous")
BOOK_KEYS = ("open", "high", "low", "close")
MARKET_KEYS = ("ticker", "event_ticker", "title", "subtitle", "yes_sub_title", "strike_type", "floor_strike", "cap_strike",
               "open_time", "close_time", "settlement_ts", "status", "result", "expiration_value")
MARKET_RECORD_KEYS = ("ticker", "market_type", "status", "result", "settlement_value_dollars", "settlement_ts")


class HttpError(Exception):
    """A non-200 answer. Transports raise this so the retry rules see the status code."""
    def __init__(self, status: int, url: str, retry_after: float | None = None):
        super().__init__("HTTP %s for %s" % (status, url))
        self.status, self.url, self.retry_after = status, url, retry_after


class KalshiDataError(RuntimeError):
    """The data could not be had: retries ran out, or the answer was not the expected shape."""


class Pacer:
    """Keeps at least `spacing` seconds between the starts of consecutive requests."""
    def __init__(self, spacing: float, sleep: Callable[[float], Any] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.spacing, self.sleep, self.clock = spacing, sleep, clock
        self.last: float | None = None

    def wait(self) -> None:
        if self.last is not None:
            gap = self.clock() - self.last
            if gap < self.spacing:
                self.sleep(self.spacing - gap)
        self.last = self.clock()


class PhasePacer:
    """A `Pacer` that also stays inside a quiet part of each wall-clock minute.

    Another process on this machine reads the same Kalshi host every minute and needs its part of the minute
    (its reads and decisions run early, in busy hours to about second 40 and now and then 52). A study pull that
    sends requests only while the wall clock's second-of-minute is in `window` (default 42 up to but not
    including 57) keeps clear of it, and `spacing` (at least 1 s) keeps the rate to one request a second at most.
    `wait()` returns only at a moment that is both far enough after the last start and inside the window, even
    if a sleep runs long, and it records that moment as the start."""
    _EPS = 1e-3     # past the edge, so float rounding cannot land a sleep just short of the window and spin

    def __init__(self, spacing: float = 1.0, window: tuple[float, float] = (42.0, 57.0),
                 sleep: Callable[[float], Any] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time):
        lo, hi = window
        if not 0 <= lo < hi <= 60:
            raise ValueError("window must satisfy 0 <= lo < hi <= 60, got %r" % (window,))
        if not spacing >= 1.0:                     # also refuses NaN
            raise ValueError("spacing must be at least 1.0 s, got %r" % (spacing,))
        self.spacing, self.window, self.sleep, self.clock, self.wall = spacing, (lo, hi), sleep, clock, wall
        self.last: float | None = None

    def wait(self) -> None:
        lo, hi = self.window
        if self.last is not None:
            gap = self.clock() - self.last
            if gap < self.spacing:
                self.sleep(self.spacing - gap)
        while True:
            phase = self.wall() % 60.0
            if lo <= phase < hi:
                break
            self.sleep((lo - phase if phase < lo else 60.0 - phase + lo) + self._EPS)
        self.last = self.clock()


QUIET_WINDOW = (42.0, 55.0)      # request starts; with QUIET_TIMEOUT every request ends by second 58
QUIET_TIMEOUT = 3.0              # seconds a quiet request may take before it is abandoned


def quiet_pacer(window: tuple[float, float] = QUIET_WINDOW) -> PhasePacer:
    """A `PhasePacer` at one request a second inside `window` of each wall-clock minute."""
    return PhasePacer(1.0, window)


def quiet_fetch(url: str) -> bytes:
    """`urllib_get` with QUIET_TIMEOUT, so a stalled study request cannot run into the next minute."""
    return urllib_get(url, QUIET_TIMEOUT)


def urllib_get(url: str, timeout: float = 60.0) -> bytes:
    """GET a URL and return the body. A non-200 answer raises HttpError with its status (and Retry-After if sent)."""
    return urllib_get_dated(url, timeout)[0]


def urllib_get_dated(url: str, timeout: float = 60.0) -> tuple[bytes, str | None]:
    """As `urllib_get`, and the answer's HTTP Date header with it (None when it has none). `KalshiData` takes a
    transport that answers either way."""
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            headers = getattr(resp, "headers", None)
            return resp.read(), (headers.get("Date") if headers is not None else None)
    except urllib.error.HTTPError as e:
        retry = e.headers.get("Retry-After") if e.headers else None
        try:
            retry_after = float(retry) if retry is not None else None
        except ValueError:
            retry_after = None
        raise HttpError(e.code, url, retry_after) from None


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pick(d: dict, key: str):
    """A value under `key`, or under the live tier's spelling (`key_dollars`, `key_fp`)."""
    for k in (key, key + "_dollars", key + "_fp"):
        if k in d:
            return d[k]
    return None


def normalize_market(row: dict, tier: str) -> dict:
    """One market in one shape whatever tier it came from; `volume` is a float and `tier` says where it was found."""
    out = {k: row.get(k) for k in MARKET_KEYS}
    out["volume"] = _num(_pick(row, "volume"))
    out["tier"] = tier
    return out


def normalize_candle(row: dict) -> dict:
    """One candle: end_ts (the end of its period, seconds), price / yes_bid / yes_ask open-high-low-close as floats
    (None when there was no trade or no quote), volume and open interest."""
    def block(name, keys):
        b = row.get(name) or {}
        return {k: _num(_pick(b, k)) for k in keys}
    return {"end_ts": int(row["end_period_ts"]), "volume": _num(_pick(row, "volume")) or 0.0,
            "open_interest": _num(_pick(row, "open_interest")) or 0.0,
            "price": block("price", PRICE_KEYS), "yes_bid": block("yes_bid", BOOK_KEYS), "yes_ask": block("yes_ask", BOOK_KEYS)}


def normalize_levels(rows) -> list[tuple[float, float]]:
    """One side of a book as [(price in dollars, size in contracts), ...], best (highest) bid first. Kalshi sends
    each side ascending with the best bid last, as dollar and fixed-point strings; a missing side is empty."""
    out = [(float(p), float(q)) for p, q in (rows or [])]
    return sorted(out, key=lambda lv: lv[0], reverse=True)


def best_quote(book: dict) -> dict:
    """Best yes bid and its size, and the yes ask (one minus the best no bid) and its size. None for a missing side."""
    yes, no = book.get("yes") or [], book.get("no") or []
    return {"bid": yes[0][0] if yes else None, "bid_size": yes[0][1] if yes else None,
            "ask": round(1.0 - no[0][0], 6) if no else None, "ask_size": no[0][1] if no else None}


def iso_to_ts(text: str) -> float:
    """A UTC ISO time as Kalshi writes it ("2026-11-13T05:56:04.912752Z") in epoch seconds."""
    return datetime.fromisoformat(text).timestamp()


def normalize_trade(row: dict) -> dict:
    """One trade of the tape with numbers as floats and its time also in epoch seconds."""
    return {"trade_id": row["trade_id"], "ticker": row.get("ticker"), "created_time": row["created_time"],
            "created_ts": iso_to_ts(row["created_time"]), "yes_price": _num(_pick(row, "yes_price")),
            "no_price": _num(_pick(row, "no_price")), "count": _num(_pick(row, "count")),
            "taker_side": row.get("taker_side"), "is_block_trade": row.get("is_block_trade")}


class KalshiData:
    """Read-only client. `fetch(url) -> bytes` is the transport (urllib by default; a fake in tests)."""

    def __init__(self, fetch: Callable[[str], bytes] = urllib_get, *, base_url: str = BASE_URL, max_per_second: float = 3.0,
                 tries: int = 5, sleep: Callable[[float], Any] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 pacer: Any = None):
        self.fetch, self.base_url, self.tries, self.sleep = fetch, base_url.rstrip("/"), tries, sleep
        self.pacer = pacer if pacer is not None else Pacer(1.0 / max_per_second + 0.01, sleep, clock)    # a hair over 1/3 s: never 4 in a second
        self.last_date: str | None = None     # the Date header of the latest answer, when the transport hands it back

    def get_json(self, path: str, params: dict | None = None) -> dict:
        """GET base + path with the query `params`; retries 429 and 5xx with waits of 1, 2, 4, 8 s (or Retry-After)."""
        url = self.base_url + path + ("?" + urllib.parse.urlencode(params) if params else "")
        last: HttpError | None = None
        for attempt in range(self.tries):
            if attempt:
                wait = last.retry_after if last is not None and last.retry_after else float(2 ** (attempt - 1))
                self.sleep(min(wait, 60.0))
            self.pacer.wait()
            try:
                body = self.fetch(url)
                if isinstance(body, tuple):            # (body, Date header) from a dated transport
                    body, self.last_date = body
            except HttpError as e:
                if e.status == 429 or e.status >= 500:
                    last = e
                    continue
                raise
            try:
                return json.loads(body)
            except ValueError as e:
                raise KalshiDataError("not JSON from %s: %s" % (url, e)) from None
        raise KalshiDataError("gave up after %d tries: HTTP %s at %s" % (self.tries, last.status if last else "?", url))

    def pages(self, path: str, params: dict, key: str) -> list[dict]:
        """Every item under `key` across cursor pages."""
        out, cursor, seen = [], None, set()
        while True:
            q = dict(params, cursor=cursor) if cursor else dict(params)
            page = self.get_json(path, q)
            if key not in page:
                raise KalshiDataError("no %r in the answer from %s (keys %s)" % (key, path, sorted(page)))
            out += page[key] or []
            cursor = page.get("cursor")
            if not cursor or not page[key] or cursor in seen:
                return out
            seen.add(cursor)

    def settled_markets(self, series: str) -> list[dict]:
        """All settled markets of a series from both tiers, one per ticker (the historical copy kept when both have it),
        oldest close first."""
        merged: dict[str, dict] = {}
        for tier, path, extra in (("historical", "/historical/markets", {}), ("live", "/markets", {"status": "settled"})):
            for row in self.pages(path, {"series_ticker": series, **extra, "limit": 1000}, "markets"):
                merged.setdefault(row["ticker"], normalize_market(row, tier))
        return sorted(merged.values(), key=lambda m: (m.get("close_time") or "", m["ticker"]))

    def candles(self, ticker: str, start: int, end: int, tier_hint: str | None = None, series: str | None = None,
                period_minutes: int = 60) -> list[dict]:
        """Candles of one market between two unix times, from the hinted tier (historical if none) and, when that
        answers 404 or nothing, from the other. Empty when neither has any."""
        series = series or ticker.split("-", 1)[0]
        paths = {"historical": "/historical/markets/%s/candlesticks" % ticker,
                 "live": "/series/%s/markets/%s/candlesticks" % (series, ticker)}
        first = tier_hint if tier_hint in paths else "historical"
        params = {"start_ts": int(start), "end_ts": int(end), "period_interval": int(period_minutes)}
        for tier in (first, "live" if first == "historical" else "historical"):
            try:
                rows = self.get_json(paths[tier], params).get("candlesticks") or []
            except HttpError as e:
                if e.status == 404:
                    continue
                raise
            if rows:
                return [normalize_candle(r) for r in rows]
        return []

    def open_markets_pages(self, min_close_ts: int | None = None, max_close_ts: int | None = None):
        """Each page (a list of market objects) of the open markets, combos excluded, closing in the window.

        `min_close_ts` and `max_close_ts` (epoch seconds) are filtered by the server. Confirmed on 2026-11-13 by two
        GETs of /markets with `status=open&mve_filter=exclude&limit=5`: with `min_close_ts` = now + 3 h and
        `max_close_ts` = now + 30 days every market returned closed on 2026-12-13, inside the window; with
        `min_close_ts` = now + 300 days only markets closing in 2099 came back. Both answered 200 together with
        `status=open`."""
        params: dict = {"status": "open", "mve_filter": "exclude", "limit": 1000}
        if min_close_ts is not None:
            params["min_close_ts"] = int(min_close_ts)
        if max_close_ts is not None:
            params["max_close_ts"] = int(max_close_ts)
        cursor, seen = None, set()
        while True:
            page = self.get_json("/markets", dict(params, cursor=cursor) if cursor else dict(params))
            if "markets" not in page:
                raise KalshiDataError("no 'markets' in the answer from /markets (keys %s)" % sorted(page))
            rows = page["markets"] or []
            yield rows
            cursor = page.get("cursor")
            if not cursor or not rows or cursor in seen:
                return
            seen.add(cursor)

    def open_markets(self, min_close_ts: int | None = None, max_close_ts: int | None = None) -> list[dict]:
        """All open markets, combos excluded, closing in the window (see `open_markets_pages` for the filters)."""
        return [m for page in self.open_markets_pages(min_close_ts, max_close_ts) for m in page]

    def orderbook(self, ticker: str, depth: int = 10) -> dict:
        """{"yes": [(price, size), ...], "no": [...]}: the best `depth` bids of each side, best first, prices in
        dollars and sizes in contracts as floats parsed from the strings. An empty side is an empty list."""
        body = self.get_json("/markets/%s/orderbook" % ticker, {"depth": int(depth)})
        book = body.get("orderbook_fp")
        if not isinstance(book, dict):
            raise KalshiDataError("no 'orderbook_fp' in the order book of %s (keys %s)" % (ticker, sorted(body)))
        return {"yes": normalize_levels(book.get("yes_dollars"))[:depth],
                "no": normalize_levels(book.get("no_dollars"))[:depth]}

    def orderbook_raw(self, ticker: str, depth: int = 10) -> dict:
        """The `orderbook_fp` object of one market's order book exactly as Kalshi sent it (dollar strings, bids
        ascending), nothing parsed, so a caller can keep a malformed level as it came."""
        body = self.get_json("/markets/%s/orderbook" % ticker, {"depth": int(depth)})
        book = body.get("orderbook_fp")
        if not isinstance(book, dict):
            raise KalshiDataError("no 'orderbook_fp' in the order book of %s (keys %s)" % (ticker, sorted(body)))
        return book

    def trades(self, ticker: str, min_ts: float, max_ts: float | None = None) -> list[dict]:
        """The trades of one market from `min_ts` (epoch seconds, as the API wants: not milliseconds) to `max_ts`
        or now, newest first as Kalshi sends them, across cursor pages."""
        params: dict = {"ticker": ticker, "min_ts": int(min_ts)}
        if max_ts is not None:
            params["max_ts"] = int(max_ts)
        params["limit"] = 1000
        return [normalize_trade(r) for r in self.pages("/markets/trades", params, "trades")]

    def market(self, ticker: str) -> dict:
        """One market's record for settlement: {ticker, market_type, status, result, settlement_value_dollars,
        settlement_ts, tier}, each value exactly as Kalshi sent it (None when absent; never converted, never filled
        in: the caller judges it). Asks the live tier (/markets/<ticker>); on a 404 from it, and only then, the
        historical tier (/historical/markets/<ticker>). The fields `normalize_market` knows come through it, the
        other two by their own keys only. The ticker must be in Kalshi's shape (the ticker part of
        webull.parse_event_name) before it is put into a path: else ValueError, and nothing is asked.
        HttpError when both tiers answer 404 or the live tier answers another 4xx; KalshiDataError when retries
        run out (never then the other tier) or the answer is not an object holding a `market` object."""
        from valedia_trader.venues.webull import parse_event_name
        if type(ticker) is not str or parse_event_name(ticker + ":yes") != (ticker, "yes"):
            raise ValueError("a market ticker must be in Kalshi's shape (a capital letter, then capitals, digits, dots "
                             "and hyphens, at most 80)")
        for tier, path in (("live", "/markets/%s"), ("historical", "/historical/markets/%s")):
            try:
                body = self.get_json(path % ticker)
            except HttpError as e:
                if e.status == 404 and tier == "live":
                    continue
                raise
            row = body.get("market") if isinstance(body, dict) else None
            if not isinstance(row, dict):
                raise KalshiDataError("no 'market' object in the answer for %s from the %s tier" % (ticker, tier))
            norm = normalize_market(row, tier)
            out = {k: norm[k] for k in ("ticker", "status", "result", "settlement_ts")}
            out.update({k: row.get(k) for k in ("market_type", "settlement_value_dollars")})
            return {k: out[k] for k in MARKET_RECORD_KEYS} | {"tier": tier}
        raise KalshiDataError("unreachable")                                   # the loop returns or raises

    def series(self, ticker: str) -> dict:
        """A series' ticker, title, `fee_type` and `fee_multiplier` (None when the series does not say)."""
        row = self.get_json("/series/%s" % ticker).get("series") or {}
        return {"ticker": row.get("ticker", ticker), "title": row.get("title"), "fee_type": row.get("fee_type"),
                "fee_multiplier": _num(row.get("fee_multiplier"))}
