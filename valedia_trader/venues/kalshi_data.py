"""Kalshi's public market data, read only: settled markets of a series and their price candles.

No key and no account: every request is a plain GET to the public API, and this module has no way to send
anything else. Kalshi keeps markets in two tiers. Markets settled before its "historical cutoff" live under
/historical; recent ones under the live paths. A series' settled markets are the union of both (they overlap by
a few days), and a market asked for on the wrong tier answers 404 or nothing, so `candles` tries the likely tier
first and then the other. The two tiers spell the same numbers differently (`close` against `close_dollars`,
`volume` against `volume_fp`); both are turned into one shape here.

Politeness: at most three requests a second, and a 429 or a server error is retried after a growing wait, up to
five tries in all, then it stops with a clear error.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

__all__ = ["BASE_URL", "HttpError", "KalshiDataError", "Pacer", "urllib_get", "normalize_market", "normalize_candle",
           "KalshiData"]

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
USER_AGENT = "valedia-trader-research/1.0"
PRICE_KEYS = ("open", "high", "low", "close", "mean", "previous")
BOOK_KEYS = ("open", "high", "low", "close")
MARKET_KEYS = ("ticker", "event_ticker", "title", "subtitle", "yes_sub_title", "strike_type", "floor_strike", "cap_strike",
               "open_time", "close_time", "settlement_ts", "status", "result", "expiration_value")


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


def urllib_get(url: str, timeout: float = 60.0) -> bytes:
    """GET a URL and return the body. A non-200 answer raises HttpError with its status (and Retry-After if sent)."""
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
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


class KalshiData:
    """Read-only client. `fetch(url) -> bytes` is the transport (urllib by default; a fake in tests)."""

    def __init__(self, fetch: Callable[[str], bytes] = urllib_get, *, base_url: str = BASE_URL, max_per_second: float = 3.0,
                 tries: int = 5, sleep: Callable[[float], Any] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.fetch, self.base_url, self.tries, self.sleep = fetch, base_url.rstrip("/"), tries, sleep
        self.pacer = Pacer(1.0 / max_per_second + 0.01, sleep, clock)    # a hair over 1/3 s: never 4 in a second

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
