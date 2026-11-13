"""Webull's event-contract instrument lists, read only, and a probe that compares them with Kalshi's open markets.

    python -m valedia_trader.venues.webull_events probe [--series KXNBAGAME ...] [--out /path/outside/the/repo.json]

Why (roadmap 4.7): Webull documents event contracts on symbols of Kalshi's shape (KXRATECUTCOUNT-26DEC31-T3) in the
EVENTS_CASH account but never names the exchange. The probe observes, in Webull's test environment, whether the
markets Webull lists in a series are Kalshi's open markets of the same series. Nothing here builds, previews or sends
an order, and nothing reads an account: only the three instrument lists below. No snapshot, depth, bar or tick is
read (prices come from elsewhere, roadmap 5.8).

What the SDK offers (webull-openapi-python-sdk 3.0.4, webull/data/quotes/instrument.py, class Instrument, reached as
DataClient(api_client).instrument; webull/data/data_client.py):

* list_event_series(category=None, symbols=None, pagination_key=None): GET
  /trading/instruments/event-contracts/series/list (request/get_event_series_request_v2.py). category one of
  ECONOMICS, FINANCIALS, POLITICS, ENTERTAINMENT, SCIENCE_TECHNOLOGY, CLIMATE_WEATHER, TRANSPORTATION, CRYPTO, SPORTS
  (the SDK's docstring); symbols a list joined by commas, at most 100. -> `series`.
* get_event_events(series_symbol, symbols=None, status=None): GET /trading/instruments/event-contracts/events/list
  (request/get_event_events_request.py). No pagination parameter. -> `events_of`.
* list_event_instruments(series_symbol=None, event_symbol=None, symbols=None, expiration_date_after=None,
  pagination_key=None): GET /trading/instruments/event-contracts/markets/list (request/get_event_instrument_request_v2.py).
  The SDK's docstring: expiration_date_after defaults to the current day (inclusive). -> `markets`.
* Also in the SDK, not used: get_event_categories() and the deprecated get_event_series(..., last_series_id,
  page_size) / get_event_instrument(..., last_instrument_id, page_size) on the older /openapi/instrument/event/ paths.

Answer shapes. Documented (developer.webull.com event-series-list and event-market-list, read by an earlier recon,
.foreman/scratch/R4-sports-odds-recon.md C2), NOT YET OBSERVED: a series row has category, series_id, symbol, name,
frequency; a market row has series_symbol, event_symbol, symbol, instrument_id, yes_condition, tradable_status
(OC tradable, CO liquidate only, NT not tradable), fractionable, can_close_early, expected_exp_date, latest_exp_date,
payout_date, rules_primary, price_ranges (start 0.0, end 1.0, step 0.01). The page envelope is NOT documented in what
was read: this module takes either a bare list of rows or an object with the rows under `data` and the next page's
key under `pagination_key` (the shape of Webull's open-orders list; absent or empty = last page). Anything else
raises, naming the answer's top-level keys through the gate. The event list's row fields are not documented in what
was read: an event row's `symbol` is read as the event symbol.

Kept per row, where present (a field absent from the row is absent from the output): series_symbol, event_symbol,
market_symbol, name (from `name`, else `title`), category, tradable_status, fractionable, can_close_early,
expected_exp_date, latest_exp_date, price_ranges [{start, end, step}], yes_condition. Everything else is dropped, and
instrument_id above all (it is not printed). Symbols are kept only in their own shape (letters, digits, dots,
hyphens, underscores, up to 80) and holding no run of a secret, else None; codes (category, tradable_status) only in
the upper-case code shape, else <withheld>; free text (name, yes_condition) through the gate of webull.py, word by
word; booleans only as booleans (or the exact words true / false); dates only as a date, a date and time, or an epoch
number; prices as finite numbers.

Safety, all reused by import from the accepted adapter webull.py, which is not edited: the client is built as there
(private token folder, the SDK's loggers silenced, its new empty log file removed: DataClient's file is
webull_data_sdk.log), every call goes through webull._call (an SDK failure is rebuilt from gated fields), every public
method through webull._public (only WebullError leaves, after the last check), and every text taken from an answer
through webull's _clean / _gate and the last check. Pacing: webull._Pacer with Webull's limit for these GETs, 30 in
60 s in the test environment and 60 in 60 s in production (developer.webull.com rate-limits, as read by the recon),
of which 80% is used; a 429 is tried again after 2, 4 and 8 s, as the adapter does for reads. Every SDK call made,
a retry included, counts towards `max_requests`; at the cap no further call is made.

The probe refuses to run unless the environment is the test environment: WEBULL_ENV (default sandbox, as
WebullVenue.from_secrets reads it) must be "sandbox", and the host the reader would use must have "sandbox" as one
whole dot-separated label (the order step's rule, execution_and_risk/order_step.py _sandbox_host, rewritten here so
that this read-only module does not import the order machinery; a test checks the two agree). Both are checked before
the credentials are read and before the first request. At most PROBE_WEBULL_REQUESTS (20) Webull and
PROBE_KALSHI_REQUESTS (10) Kalshi requests a run (Kalshi through kalshi_data.KalshiData, keyless, its retries counted).
It writes only the file given by --out, which must be outside the repository.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from valedia_trader.venues import kalshi_data
from valedia_trader.venues.webull import (HOSTS, NO_SECRETS, READ_RETRY_WAITS, REPO, WITHHELD, RateLimitHit, WebullError, _call,
                                          _checked_error, _class_name, _clean, _gate, _last_check, _number, _Pacer, _plain,
                                          _private_token_dir, _public, _quiet_sdk_logging, _remove_new_empty_sdk_log, _safe,
                                          _secrets_of, _too_many)

__all__ = ["WebullEvents", "Listing", "RequestCapReached", "ProbeRefused", "compare", "probe", "main", "DEFAULT_SERIES"]

TEST_ENVIRONMENT = "sandbox"
# developer.webull.com/apis/docs/rate-limits (as read by the recon, 2026-11-13): event-contract instrument and quote
# GETs 30 in 60 s in the test environment, 60 in 60 s in production, per App Key. (calls, seconds)
EVENT_RATE_LIMITS = {"sandbox": (30, 60), "production": (60, 60)}
CALL_KINDS = ("series", "events", "markets", "all")
MAX_PAGES = 10                                    # pages followed per call unless the caller asks for another cap
MAX_SYMBOLS = 100                                 # the SDK's docstring: at most 100 symbols per query
CATEGORIES = frozenset({"ECONOMICS", "FINANCIALS", "POLITICS", "ENTERTAINMENT", "SCIENCE_TECHNOLOGY", "CLIMATE_WEATHER",
                        "TRANSPORTATION", "CRYPTO", "SPORTS"})
DATA_SDK_LOG_FILE = "webull_data_sdk.log"         # DataClient._init_logger (SDK data/data_client.py)

PROBE_WEBULL_REQUESTS = 20
PROBE_KALSHI_REQUESTS = 10
PROBE_SERIES_PAGES = 4                            # the whole series list, for the count by category
PROBE_MARKET_PAGES = 3                            # per asked series; 4 + 1 + 5 x 3 = 20
PROBE_KALSHI_PAGES = 2                            # per asked series; 5 x 2 = 10
PROBE_MAX_SERIES = 10
EXAMPLES = 5
DEFAULT_SERIES = ("KXNFLGAME", "KXNBAGAME", "KXNHLGAME", "KXNCAAFGAME", "KXNCAAMBGAME")

_EVENT_SYMBOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]{0,79}")
_CODE_WORD = re.compile(r"[A-Z][A-Z_]{0,31}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:?\d{2})?)?|\d{9,14}")
_PAGE_KEY = re.compile(r"[!-~]{1,1024}")          # printable ASCII, no whitespace: only ever sent back to Webull
_HOST = re.compile(r"[A-Za-z0-9.-]{1,253}")
MAX_PRICE_RANGES = 20


class RequestCapReached(WebullError):
    """The reader's request cap is used up: no request was made."""


class ProbeRefused(WebullError):
    """The probe was not run (wrong environment, a bad argument, an output path inside the repository)."""


class Listing(list):
    """A list of cleaned rows, with how it was read: `pages` read, `complete` (the last page said it was the last),
    `stopped` (why reading stopped early: "page cap", "request cap", "repeated pagination key", "unreadable
    pagination key", "empty page"; None when complete) and `dropped` (rows that were not objects)."""

    def __init__(self, rows=(), pages=0, complete=False, stopped=None, dropped=0):
        super().__init__(rows)
        self.pages, self.complete, self.stopped, self.dropped = pages, complete, stopped, dropped


def _sandbox_host(host) -> bool:
    """The order step's rule (execution_and_risk/order_step.py _sandbox_host): a plain str of letters, digits, dots and
    hyphens, 1 to 253 characters, with `sandbox` as one of its dot-separated labels."""
    return type(host) is str and _HOST.fullmatch(host) is not None and TEST_ENVIRONMENT in host.split(".")


def _symbol_list(symbols: Any) -> str | None:
    """The SDK's `symbols` argument as one comma-joined text, or None; refused (before any request) unless every item
    is a symbol of the event shape and there are at most MAX_SYMBOLS."""
    if symbols is None:
        return None
    items = [symbols] if isinstance(symbols, str) else list(symbols) if isinstance(symbols, (list, tuple)) else None
    if not items or len(items) > MAX_SYMBOLS or not all(type(s) is str and _EVENT_SYMBOL.fullmatch(s) for s in items):
        raise WebullError(f"symbols must be 1 to {MAX_SYMBOLS} event symbols (letters, digits, dots, hyphens, underscores)")
    return ",".join(items)


def _one_symbol(value: Any, what: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not _EVENT_SYMBOL.fullmatch(value):
        raise WebullError(f"the {what} must be an event symbol (letters, digits, dots, hyphens, underscores, up to 80)")
    return value


def _data_client(app_key: str, app_secret: str, environment: str, region: str, token_dir, secrets, words) -> Any:
    """The SDK's DataClient, built as webull.WebullVenue builds its TradeClient (the SDK opens a session here: a
    network call). Every failure is rebuilt from gated fields and given the last check."""
    try:
        from webull.core.client import ApiClient
        from webull.data.data_client import DataClient
    except ImportError:
        raise WebullError("the Webull SDK is not installed: uv pip install 'webull-openapi-python-sdk>=3.0.4'") from None
    host = HOSTS[environment]
    private_tokens = _private_token_dir(token_dir, environment)
    sdk_log = Path.cwd() / DATA_SDK_LOG_FILE
    sdk_log_existed = sdk_log.exists()
    _quiet_sdk_logging()
    try:
        api = ApiClient(app_key, app_secret, region)
        api.add_endpoint(region, host)
        api.set_token_dir(str(private_tokens))
        # DataClient adds its stdout and file loggers only when neither of these is set (SDK data/data_client.py).
        api._stream_logger_set = api._file_logger_set = True
        return DataClient(api)
    except Exception as exc:
        raise WebullError(_last_check(f"could not open a Webull session on {host}: {_safe(exc, secrets)}", words)) from None
    finally:
        _quiet_sdk_logging()
        _remove_new_empty_sdk_log(sdk_log_existed, sdk_log)


class WebullEvents:
    """The three event-contract instrument lists of Webull, read only. `client` is the SDK's DataClient (or anything
    with an `instrument` of the same three methods); without one, a DataClient is built (a network call)."""

    def __init__(self, app_key: str, app_secret: str, environment: str = TEST_ENVIRONMENT, client: Any | None = None,
                 token_dir: str | Path | None = None, region: str = "us", max_requests: int | None = None,
                 max_pages: int = MAX_PAGES, clock: Any = None, sleep: Any = None, also_withhold: Iterable[str] = ()) -> None:
        """`max_requests`: no SDK call past this many (retries included); None for no cap. `max_pages`: the default
        page cap of `series` and `markets`. `also_withhold`: further values (an account id) withheld from every text."""
        words = tuple(w for w in (app_key, app_secret, *also_withhold) if isinstance(w, str))
        err = None
        try:
            self._setup(app_key, app_secret, environment, client, token_dir, region, max_requests, max_pages, clock, sleep, words)
        except WebullError as exc:
            err = exc
        except Exception as exc:
            err = WebullError(f"could not set up the Webull events reader ({_class_name(exc, _secrets_of(*words))})")
        if err is not None:
            raise _checked_error(err, lambda text: _last_check(text, words))

    def _setup(self, app_key, app_secret, environment, client, token_dir, region, max_requests, max_pages, clock, sleep, words):
        if type(environment) is not str or environment not in HOSTS or environment not in EVENT_RATE_LIMITS:
            raise WebullError(f"environment must be one of {sorted(HOSTS)}")
        if not (type(app_key) is str and app_key.strip() and type(app_secret) is str and app_secret.strip()):
            raise WebullError("a Webull App Key and App Secret are required")
        if max_requests is not None and not (type(max_requests) is int and max_requests >= 0):
            raise WebullError("max_requests must be a whole number of at least 0, or None")
        if not (type(max_pages) is int and max_pages >= 1):
            raise WebullError("max_pages must be a whole number of at least 1")
        self._words_ = tuple(sorted({w for w in words if w.strip()}))
        self.environment, self.host = environment, HOSTS[environment]
        self.max_requests, self.max_pages, self.requests = max_requests, max_pages, 0
        clock = time.monotonic if clock is None else clock
        self._sleep = time.sleep if sleep is None else sleep
        if not callable(clock) or not callable(self._sleep):
            raise WebullError("the pacer's clock and sleep must be callables")
        limit = EVENT_RATE_LIMITS[environment]
        self._pacer = _Pacer({k: limit for k in CALL_KINDS}, clock, self._sleep)
        if client is None:
            client = _data_client(app_key, app_secret, environment, region, token_dir, self._secrets(), self._words_)
        self._client = client

    def __repr__(self) -> str:
        return f"WebullEvents(webull-{self.environment})"

    # what webull._public and webull._call expect of an object
    def _words(self) -> tuple:
        return self._words_

    def _secrets(self, with_account: bool = True):
        return _secrets_of(*self._words_)

    def _final(self, text: Any) -> str:
        return _last_check(text, self._words_)

    def cap_left(self) -> int | None:
        """Requests left under the cap, or None when there is no cap."""
        return None if self.max_requests is None else max(0, self.max_requests - self.requests)

    # ---- one request
    def _paced(self, kind: str, fn, *args) -> Any:
        """One SDK call, made once the pacer allows it and counted; a 429 is tried again after 2, 4 and 8 s while the
        request cap allows, else RateLimitHit."""
        for attempt in range(1 + len(READ_RETRY_WAITS)):
            if attempt:
                if self.cap_left() == 0:
                    raise RateLimitHit(f"Webull's rate limit was hit: the {kind} call answered 429 (too many requests) "
                                       "and the request cap leaves no further try")
                self._sleep(READ_RETRY_WAITS[attempt - 1])
            self.requests += 1
            self._pacer.wait(kind)
            try:
                res = fn(*args)
            except Exception as exc:
                if not _too_many(exc, True):
                    raise
                continue
            if not _too_many(res, False):
                return res
        raise RateLimitHit(f"Webull's rate limit was hit: the {kind} call answered 429 (too many requests) on all "
                           f"{1 + len(READ_RETRY_WAITS)} tries")

    def _request(self, kind: str, method: str, **params) -> Any:
        if self.cap_left() == 0:
            raise RequestCapReached(f"the request cap of {self.max_requests} Webull requests is used up")

        def fn():
            return getattr(self._client.instrument, method)(**params)
        return _call(self._paced, kind, fn, secrets=self._secrets())

    def _rows(self, body: Any) -> tuple[list, Any]:
        """(rows, next page key or None) of one answer: a bare list, or {data: [...], pagination_key}."""
        if isinstance(body, list):
            return body, None
        if isinstance(body, dict) and isinstance(body.get("data"), list):
            return body["data"], body.get("pagination_key")
        keys = sorted(_gate(k, self._secrets(), 40) for k in body)[:12] if isinstance(body, dict) else []
        raise WebullError(f"Webull's answer is not of the expected shape (a list, or an object with a `data` list); "
                          f"it is a {_class_name(body, self._secrets())}" + (f" with keys {', '.join(keys)}" if keys else ""))

    def _pages(self, kind: str, method: str, params: dict, clean: Callable[[dict], dict], max_pages: int | None) -> Listing:
        cap = self.max_pages if max_pages is None else max_pages
        if not (type(cap) is int and cap >= 1):
            raise WebullError("max_pages must be a whole number of at least 1")
        out, key, seen, pages, dropped = [], None, set(), 0, 0
        while True:
            if pages >= cap:
                return Listing(out, pages, False, "page cap", dropped)
            if pages and self.cap_left() == 0:
                return Listing(out, pages, False, "request cap", dropped)
            body = self._request(kind, method, **params, **({"pagination_key": key} if key is not None else {}))
            pages += 1
            rows, nxt = self._rows(body)
            for row in rows:
                if isinstance(row, dict):
                    out.append(clean(row))
                else:
                    dropped += 1
            if nxt is None or nxt == "":
                return Listing(out, pages, True, None, dropped)
            if type(nxt) is not str or not _PAGE_KEY.fullmatch(nxt):
                return Listing(out, pages, False, "unreadable pagination key", dropped)
            if nxt in seen or nxt == key:
                return Listing(out, pages, False, "repeated pagination key", dropped)
            if not rows:
                return Listing(out, pages, False, "empty page", dropped)
            seen.add(nxt)
            key = nxt

    # ---- the three calls
    @_public
    def series(self, category: str | None = None, symbols: Iterable[str] | None = None, max_pages: int | None = None) -> Listing:
        """Event series (SDK list_event_series), every page up to `max_pages`: [{series_symbol, name, category}]."""
        if category is not None and (type(category) is not str or category not in CATEGORIES):
            raise WebullError(f"category must be one of {', '.join(sorted(CATEGORIES))}")
        params = {"category": category, "symbols": _symbol_list(symbols)}
        return self._pages("series", "list_event_series", {k: v for k, v in params.items() if v is not None},
                           functools.partial(self._clean_row, symbol_as="series_symbol"), max_pages)

    @_public
    def events_of(self, series_symbol: str) -> Listing:
        """The events of one series (SDK get_event_events; one request, the SDK offers no pagination here):
        [{series_symbol, event_symbol, name, category, expected_exp_date, ...}]."""
        series_symbol = _one_symbol(series_symbol, "series symbol")
        if series_symbol is None:
            raise WebullError("events_of needs a series symbol")
        body = self._request("events", "get_event_events", series_symbol=series_symbol)
        rows, _ = self._rows(body)
        clean = functools.partial(self._clean_row, symbol_as="event_symbol")
        return Listing([clean(r) for r in rows if isinstance(r, dict)], 1, True, None, sum(not isinstance(r, dict) for r in rows))

    @_public
    def markets(self, series_symbol: str | None = None, event_symbol: str | None = None, symbols: Iterable[str] | None = None,
                max_pages: int | None = None) -> Listing:
        """Event markets (SDK list_event_instruments), every page up to `max_pages`: [{series_symbol, event_symbol,
        market_symbol, yes_condition, tradable_status, fractionable, can_close_early, expected_exp_date,
        latest_exp_date, price_ranges}]. Webull's default applies to expiry: markets expiring today or later."""
        params = {"series_symbol": _one_symbol(series_symbol, "series symbol"), "event_symbol": _one_symbol(event_symbol, "event symbol"),
                  "symbols": _symbol_list(symbols)}
        return self._pages("markets", "list_event_instruments", {k: v for k, v in params.items() if v is not None},
                           functools.partial(self._clean_row, symbol_as="market_symbol"), max_pages)

    # ---- cleaning
    def _sym(self, value: Any) -> str | None:
        return _clean(value, _EVENT_SYMBOL, self._secrets(), self._final)

    def _code_word(self, value: Any) -> str:
        return _clean(value, _CODE_WORD, self._secrets(), self._final) or WITHHELD

    def _text(self, value: Any) -> str:
        if type(value) is not str:
            return WITHHELD
        text = _gate(value, self._secrets(), 160)
        return text if self._final(text) == text else WITHHELD

    @staticmethod
    def _flag(value: Any) -> bool | None:
        if type(value) is bool:
            return value
        if type(value) is str and value in ("true", "false", "TRUE", "FALSE", "True", "False"):
            return value.lower() == "true"
        return None

    def _date(self, value: Any) -> str | int | None:
        if type(value) is int and 0 < value < 10 ** 14:
            return value
        return _clean(value, _DATE, self._secrets(), self._final)

    @staticmethod
    def _price(value: Any) -> float | None:
        return _number(value) if type(value) in (str, int, float) else None

    def _ranges(self, value: Any) -> list[dict] | None:
        items = [value] if isinstance(value, dict) else value if isinstance(value, list) else None
        if items is None or len(items) > MAX_PRICE_RANGES or not all(isinstance(i, dict) for i in items):
            return None
        return [{k: self._price(i.get(k)) for k in ("start", "end", "step") if k in i} for i in items]

    def _clean_row(self, row: dict, symbol_as: str) -> dict:
        """Only the listed fields that the row has, each in its own shape (see the module notes)."""
        out: dict = {}
        if "series_symbol" in row:
            out["series_symbol"] = self._sym(row["series_symbol"])
        if "event_symbol" in row:
            out["event_symbol"] = self._sym(row["event_symbol"])
        if "symbol" in row:
            out[symbol_as] = self._sym(row["symbol"])
        if "name" in row or "title" in row:
            out["name"] = self._text(row["name"] if "name" in row else row["title"])
        if "category" in row:
            out["category"] = self._code_word(row["category"])
        if "yes_condition" in row:
            out["yes_condition"] = self._text(row["yes_condition"])
        if "tradable_status" in row:
            out["tradable_status"] = self._code_word(row["tradable_status"])
        for key in ("fractionable", "can_close_early"):
            if key in row:
                out[key] = self._flag(row[key])
        for key in ("expected_exp_date", "latest_exp_date"):
            if key in row:
                out[key] = self._date(row[key])
        if "price_ranges" in row:
            out["price_ranges"] = self._ranges(row["price_ranges"])
        return out


# ---- the comparison

def compare(webull_symbols: Iterable[str], kalshi_symbols: Iterable[str], examples: int = EXAMPLES) -> dict:
    """Sizes of the intersection and of each difference of two symbol sets, and up to `examples` symbols (sorted, the
    first ones) from each of the three."""
    a, b = set(webull_symbols), set(kalshi_symbols)
    parts = {"both": a & b, "webull_only": a - b, "kalshi_only": b - a}
    return {**{k: len(v) for k, v in parts.items()}, "examples": {k: sorted(v)[:examples] for k, v in parts.items()}}


# ---- the probe

class _KalshiCapReached(Exception):
    pass


def _capped(fetch: Callable[[str], Any], cap: int) -> Callable[[str], Any]:
    """A Kalshi transport that makes at most `cap` requests (a retry inside KalshiData is a request too)."""
    def capped(url: str):
        if capped.count >= cap:
            raise _KalshiCapReached()
        capped.count += 1
        return fetch(url)
    capped.count = 0
    return capped


def _inside_repo(path: Path) -> bool:
    """Whether `path` is the repository or inside it, by resolved path and by os.path.samefile on every ancestor."""
    try:
        p = path.expanduser().absolute()
        repo = REPO.resolve()
        if p.resolve() == repo or repo in p.resolve().parents:
            return True
        for q in (p, *p.parents):
            if q.exists() and os.path.samefile(q, REPO):
                return True
    except OSError:
        return True                                                       # cannot tell: refuse
    return False


def _error_text(exc: BaseException, reader: WebullEvents | None) -> str:
    secrets = reader._secrets() if reader is not None else NO_SECRETS
    text = f"{_class_name(exc, secrets)}: {_gate(_plain(exc, 10 ** 6), secrets, 300)}"
    return reader._final(text) if reader is not None else _last_check(text, ())


def _kalshi_open(kalshi: kalshi_data.KalshiData, series_symbol: str, reader: WebullEvents) -> dict:
    """Kalshi's open markets of one series, up to PROBE_KALSHI_PAGES pages: their tickers (in the symbol shape and
    holding no run of a secret) and whether the listing was complete."""
    tickers, unreadable, cursor, complete, error = set(), 0, None, False, None
    try:
        for _ in range(PROBE_KALSHI_PAGES):
            params = {"series_ticker": series_symbol, "status": "open", "limit": 1000, **({"cursor": cursor} if cursor else {})}
            page = kalshi.get_json("/markets", params)
            rows = page.get("markets") if isinstance(page, dict) else None
            if not isinstance(rows, list):
                raise kalshi_data.KalshiDataError("no 'markets' list in Kalshi's answer")
            for row in rows:
                t = reader._sym(row.get("ticker")) if isinstance(row, dict) else None
                if t is None:
                    unreadable += 1
                else:
                    tickers.add(t)
            cursor = page.get("cursor")
            if not cursor or not rows:
                complete = True
                break
    except _KalshiCapReached:
        error = "request cap"
    except Exception as exc:
        error = _error_text(exc, reader)
    return {"tickers": tickers, "unreadable": unreadable, "complete": complete, "error": error}


def _check_environment(env) -> str:
    environment = env.get("WEBULL_ENV") or TEST_ENVIRONMENT
    environment = environment.strip().lower() if type(environment) is str else None
    if environment != TEST_ENVIRONMENT or not _sandbox_host(HOSTS.get(environment)):
        raise ProbeRefused("the probe runs against Webull's test environment only (WEBULL_ENV=sandbox, a host with "
                           "sandbox as one of its labels); this environment is not that")
    return environment


def probe(series_symbols: Iterable[str] = DEFAULT_SERIES, environ: dict | None = None, client: Any | None = None,
          kalshi_fetch: Callable[[str], Any] | None = None, clock: Any = None, sleep: Any = None,
          token_dir=None) -> tuple[dict, WebullEvents]:
    """The probe's report (values this module built, symbols that passed their shape rule, gated error texts) and the
    reader (for its last check). Refuses (ProbeRefused, no request made) unless the environment is the test
    environment. With no `environ`, the secrets file is loaded first, as WebullVenue.from_secrets does."""
    asked = list(series_symbols)
    if not 1 <= len(asked) <= PROBE_MAX_SERIES or len(set(asked)) != len(asked) or not all(
            type(s) is str and _EVENT_SYMBOL.fullmatch(s) for s in asked):
        raise ProbeRefused(f"--series takes 1 to {PROBE_MAX_SERIES} distinct series symbols")
    from valedia_trader.core.secrets import load_env, secret
    env = os.environ if environ is None else environ
    if environ is None:
        load_env()
    environment = _check_environment(env)                             # before the credentials and any request
    reader = WebullEvents(secret("WEBULL_APP_KEY", env), secret("WEBULL_APP_SECRET", env), environment=environment,
                          client=client, token_dir=token_dir, max_requests=PROBE_WEBULL_REQUESTS, clock=clock, sleep=sleep,
                          also_withhold=[v for v in ((env.get("WEBULL_ACCOUNT_ID") or "").strip(),) if v])
    report: dict = {"environment": reader.environment, "host": reader.host}

    by_category: dict = {}
    try:
        everything = reader.series(max_pages=PROBE_SERIES_PAGES)
        for row in everything:
            c = row.get("category") or "absent"
            by_category[c] = by_category.get(c, 0) + 1
        report["series_listing"] = {"series": len(everything), "pages": everything.pages, "complete": everything.complete,
                                    "stopped": everything.stopped}
    except WebullError as exc:
        report["series_listing"] = {"error": _error_text(exc, reader), "complete": False}
    report["series_by_category"] = dict(sorted(by_category.items()))

    try:
        found = reader.series(symbols=asked, max_pages=1)
        listed: set | None = {r.get("series_symbol") for r in found}
        listed_error = None
    except WebullError as exc:
        listed, listed_error = None, _error_text(exc, reader)

    fetch = _capped(kalshi_data.urllib_get if kalshi_fetch is None else kalshi_fetch, PROBE_KALSHI_REQUESTS)
    kalshi = kalshi_data.KalshiData(fetch, sleep=time.sleep if sleep is None else sleep)
    per_series: dict = {}
    for s in asked:
        item: dict = {"webull_lists_series": None if listed is None else s in listed}
        if listed_error is not None:
            item["webull_series_error"] = listed_error
        webull_symbols: set = set()
        try:
            rows = reader.markets(series_symbol=s, max_pages=PROBE_MARKET_PAGES)
            status: dict = {}
            steps: set = set()
            unreadable = 0
            for m in rows:
                st = m.get("tradable_status") or "absent"
                status[st] = status.get(st, 0) + 1
                if m.get("market_symbol"):
                    webull_symbols.add(m["market_symbol"])
                else:
                    unreadable += 1
                for r in m.get("price_ranges") or []:
                    if r.get("step") is not None:
                        steps.add(r["step"])
            item.update({"webull_markets": len(rows), "webull_markets_by_status": dict(sorted(status.items())),
                         "webull_pages": rows.pages, "webull_complete": rows.complete, "webull_stopped": rows.stopped,
                         "webull_unreadable_symbols": unreadable, "price_steps": sorted(steps)})
        except RequestCapReached:
            item["webull_error"] = "not read: request cap"
        except WebullError as exc:
            item["webull_error"] = _error_text(exc, reader)
        k = _kalshi_open(kalshi, s, reader)
        item.update({"kalshi_open_markets": len(k["tickers"]), "kalshi_complete": k["complete"],
                     "kalshi_unreadable_symbols": k["unreadable"]})
        if k["error"] is not None:
            item["kalshi_error"] = k["error"]
        item.update(compare(webull_symbols, k["tickers"]))
        per_series[s] = item
    report["series"] = per_series
    report["requests"] = {"webull": reader.requests, "webull_cap": PROBE_WEBULL_REQUESTS,
                          "kalshi": fetch.count, "kalshi_cap": PROBE_KALSHI_REQUESTS}
    return report, reader


def _own(exc: BaseException) -> bool:
    return str(getattr(type(exc), "__module__", "")).split(".")[0] in ("builtins", "valedia_trader")


def main(argv: list[str] | None = None, *, environ: dict | None = None, client: Any | None = None,
         kalshi_fetch: Callable[[str], Any] | None = None, clock: Any = None, sleep: Any = None) -> int:
    """Prints the probe's report as JSON (and writes it to --out) after the last check, or a refusal or failure, gated,
    on stderr with exit code 2. The keyword arguments are for tests."""
    ap = argparse.ArgumentParser(prog="python -m valedia_trader.venues.webull_events",
                                 description="Webull event-contract instruments against Kalshi's open markets "
                                             "(read-only, test environment only).")
    ap.add_argument("cmd", choices=["probe"])
    ap.add_argument("--series", nargs="+", default=list(DEFAULT_SERIES))
    ap.add_argument("--out", default=None, help="a JSON file outside the repository")
    args = ap.parse_args(argv)
    reader = None
    try:
        out = Path(args.out) if args.out else None
        if out is not None and _inside_repo(out):
            raise ProbeRefused("--out must be outside the repository")
        report, reader = probe(args.series, environ=environ, client=client, kalshi_fetch=kalshi_fetch, clock=clock, sleep=sleep)
        text = reader._final(json.dumps(report, indent=2, sort_keys=True))
        if out is not None:
            out.write_text(text + "\n")
        print(text)
    except ProbeRefused as exc:
        print(_last_check(f"refused: {_plain(exc, 2000)}", ()), file=sys.stderr)
        return 2
    except Exception as exc:
        if reader is not None:
            print(f"not ready: {_error_text(exc, reader)}", file=sys.stderr)
        else:
            name = _class_name(exc, NO_SECRETS) if _own(exc) else WITHHELD
            print(_last_check(f"not ready: {name}: {_gate(exc, NO_SECRETS, 400)}", ()), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
