"""Polymarket's public data service (https://data-api.polymarket.com, the v2 routes), read only.

No key and no account: every request is a plain GET with a generic client name, to that one host and only under
/v2/; this module has no way to send anything else and no knob to point it elsewhere. Manners copied from
kalshi_data: at most three requests a second; a 429 or a server error is retried after a growing wait (1, 2, 4, 8 s,
or the server's Retry-After), five tries in all, then a clear error; a timeout, a reset or a dropped connection is
retried the same way. Any other non-200 answer is raised at once. A redirect is NEVER followed: a 3xx answer is an
error naming its status, after exactly one request, and is not retried. The address is checked against an allow-list:
exactly `https://data-api.polymarket.com`, then a path matching `/v2/[a-z0-9/_-]+` (no percent sign, no dot, no
whitespace), then an optional query of the characters the reader's own encoding produces; nothing else.

Routes (field names from the service's published specification, /v2/openapi.json, read 2026-11-17):

* list routes answer {"data": [...], "pagination": {"limit", "offset", "has_more", "next_cursor"}}; deeper pages come
  only from cursor=<next_cursor>. `walk` follows them until has_more is false. A repeated cursor, has_more without a
  cursor, a wrong envelope, or more pages than the cap each RAISE: a walk never returns a silent partial list.
  The first page's filters (all but `limit`) are sent again beside the cursor, except on the profit board, whose
  cursor pins the board and takes no parameters beside it.
* /v2/resolutions is not paged: {"data": [...]} for at most 20 condition ids a request. `resolution_batches` asks
  any number of ids twenty at a time and records an answer per id: a short answer (fewer rows than ids) is the
  per-id answer "no_row" (unresolved or unknown), never an error for the batch.
* /v2/prices-history?token_id=<outcome token>&start=<s>&end=<e> answers one token's price points in [s, e) (start
  inclusive, end exclusive), each {price, resolution_seconds, timestamp}: the service picks the grain, a point's
  timestamp is the start of the span it was observed in, and a terminal point has resolution_seconds 0. It is
  paged like the list routes. `prices_history` asks for one window; an empty window is a finished walk.
* every row is validated; a row of the wrong shape is a counted reject (reason in `Walk.rejects`), never a guess.
  A bool, None, NaN, infinity or a string where a number belongs is wrong. A time must be whole epoch seconds from
  2020-01-01 to one day after the client was made (a time in milliseconds is out of that span).
* one identity per row within a walk. A true repeat (the whole row equal) is dropped and counted ("repeated_row");
  rows alike in identity and different elsewhere are a conflict ("conflicting_row"): a trade keeps the first and
  counts the rest; a price point in conflict is never kept (all its rows are counted as conflicts), so it can never
  be a price. A trade's identity: wallet (case folded), transaction hash, token, condition, side, size, price, time
  and outcome index (the feed has no fill id). A price point's: token, time and grain. A resolution row's: the whole
  row (two different rows for one condition are left to `resolution_batches`, which does not trust them).
"""
from __future__ import annotations

import http.client
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

__all__ = ["BASE_URL", "USER_AGENT", "HttpError", "PolymarketDataError", "PageCapHit", "Pacer", "urllib_get", "Walk",
           "PolymarketData", "is_number", "validate_board_row", "validate_trade", "validate_position",
           "validate_resolution", "validate_price_point", "trade_identity", "TS_MIN"]

BASE_URL = "https://data-api.polymarket.com"
USER_AGENT = "market-data-reader/1.0"
DEFAULT_PAGE_CAP = 5000            # pages of a walk; a wallet with more than 5 million trades raises, never truncates
BOARD_WINDOWS = ("day", "week", "month", "all")
POSITION_STATUSES = (None, "CLOSED")
MAX_CONDITIONS = 20
TS_MIN = 1_577_836_800             # 2020-01-01T00:00:00Z: no time of this service is earlier
TS_AFTER_START = 86_400            # a time may be at most one day after the client was made


class HttpError(Exception):
    """A non-200 answer. Transports raise this so the retry rules see the status code."""
    def __init__(self, status: int, url: str, retry_after: float | None = None):
        super().__init__("HTTP %s for %s%s" % (status, url, " (a redirect: never followed)" if 300 <= status < 400
                                               else ""))
        self.status, self.url, self.retry_after = status, url, retry_after


class PolymarketDataError(RuntimeError):
    """The data could not be had: retries ran out, the answer was not the expected shape, or a walk went wrong."""


class PageCapHit(PolymarketDataError):
    """A walk reached its page cap with more pages still promised."""


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


PATH_RE = r"/v2(?:/[a-z0-9_-]+)+"               # every segment non-empty; no trailing slash
QUERY_RE = r"[A-Za-z0-9_.~%+=&-]*"            # what urllib.parse.urlencode writes
ALLOWED_URL = re.compile(re.escape(BASE_URL) + "(" + PATH_RE + r")(?:\?" + QUERY_RE + ")?")


def _allowed(url: str) -> bool:
    """The allow-list: the whole address is the service's base (escaped: a dot is a dot), a /v2 path of one or more
    non-empty segments of lower-case letters, digits, `_` and `-` (no empty segment, no trailing slash), and an
    optional query as the reader encodes it. Nothing else passes."""
    return isinstance(url, str) and ALLOWED_URL.fullmatch(url) is not None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follows a redirect: the 3xx answer falls through to urllib's error for it."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_HANDLERS: tuple = ()              # further urllib handlers (tests put a stand-in for the network here)


def _opener():
    return urllib.request.build_opener(_NoRedirect(), *_HANDLERS)


def urllib_get(url: str, timeout: float = 60.0) -> tuple[bytes, str | None]:
    """GET one URL of the service: (body, the answer's Date header or None). Refuses any other address. Any answer
    but a 200 raises HttpError with its status (and Retry-After when sent); a redirect is never followed."""
    if not _allowed(url):
        raise PolymarketDataError("refused: %s is not under %s/v2/" % (url, BASE_URL))
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}, method="GET")
    try:
        with _opener().open(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if status != 200:
                raise HttpError(status, url)
            headers = getattr(resp, "headers", None)
            return resp.read(), (headers.get("Date") if headers is not None else None)
    except urllib.error.HTTPError as e:
        retry = e.headers.get("Retry-After") if e.headers else None
        try:
            retry_after = float(retry) if retry is not None else None
        except ValueError:
            retry_after = None
        raise HttpError(e.code, url, retry_after) from None


# ---------------------------------------------------------------- shapes
def is_number(v) -> bool:
    """A finite int or float that is not a bool."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_str(v, nonempty: bool = True) -> bool:
    return isinstance(v, str) and (bool(v) or not nonempty)


def _ts_max(ts_max: int | None) -> int:
    return ts_max if ts_max is not None else int(time.time()) + TS_AFTER_START


def _ts_why(ts, ts_max: int | None) -> str | None:
    """None for whole epoch seconds from 2020-01-01 to `ts_max`; else the reason."""
    if not _is_int(ts):
        return "bad_timestamp"
    if not TS_MIN <= ts <= _ts_max(ts_max):
        return "timestamp_out_of_span"
    return None


def rfc3339_to_ts(v) -> int | None:
    """An RFC3339 UTC time with an explicit zone ('Z' or an offset) as epoch seconds; None when it is not one."""
    if not isinstance(v, str) or len(v) < 20:
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00") if v.endswith("Z") else v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return int(dt.timestamp())


def validate_board_row(row) -> tuple[dict | None, str | None]:
    """A profit-board row: rank int, user_id non-empty str, pnl and volume numbers. Other fields are kept raw only."""
    if not isinstance(row, dict):
        return None, "not_an_object"
    if not _is_int(row.get("rank")):
        return None, "bad_rank"
    if not _is_str(row.get("user_id")):
        return None, "bad_user_id"
    for k in ("pnl", "volume"):
        if not is_number(row.get(k)):
            return None, "bad_" + k
    return {"rank": row["rank"], "user_id": row["user_id"], "pnl": float(row["pnl"]), "volume": float(row["volume"])}, None


TRADE_STR = ("token_id", "condition_id")


def validate_trade(row, user: str | None = None, ts_max: int | None = None) -> tuple[dict | None, str | None]:
    """A trade row: side BUY or SELL; token and condition non-empty strings; size a number above 0; price a number from
    0 to 1; timestamp whole seconds from 2020-01-01 to `ts_max` (default: a day from now); outcome_index 0 or 1;
    event_slug and transaction_hash strings (may be empty). With `user`, the row's proxy_wallet must be that wallet
    (case folded)."""
    if not isinstance(row, dict):
        return None, "not_an_object"
    if not _is_str(row.get("proxy_wallet")):
        return None, "bad_proxy_wallet"
    if user is not None and row["proxy_wallet"].lower() != user.lower():
        return None, "other_wallet"
    if row.get("side") not in ("BUY", "SELL"):
        return None, "bad_side"
    for k in TRADE_STR:
        if not _is_str(row.get(k)):
            return None, "bad_" + k
    size, price, ts, oi = row.get("size"), row.get("price"), row.get("timestamp"), row.get("outcome_index")
    if not is_number(size) or size <= 0:
        return None, "bad_size"
    if not is_number(price) or not 0 <= price <= 1:
        return None, "bad_price"
    why = _ts_why(ts, ts_max)
    if why:
        return None, why
    if not _is_int(oi) or oi not in (0, 1):
        return None, "bad_outcome_index"
    for k in ("event_slug", "transaction_hash"):
        if not _is_str(row.get(k), nonempty=False):
            return None, "bad_" + k
    out = {k: row[k] for k in ("proxy_wallet", "side", "token_id", "condition_id", "size", "price", "timestamp",
                               "event_slug", "outcome_index", "transaction_hash")}
    for k in ("slug", "title", "outcome"):
        out[k] = row.get(k) if isinstance(row.get(k), str) else None
    return out, None


def trade_identity(t: dict) -> tuple:
    """The identity of a validated trade row (the feed has no fill id)."""
    return (t["proxy_wallet"].lower(), t["transaction_hash"], t["token_id"], t["condition_id"], t["side"], t["size"],
            t["price"], t["timestamp"], t["outcome_index"])


POSITION_NUM = ("current_size", "avg_price", "entry_cost_usdc", "total_cost_usdc", "current_value", "total_size",
                "realized_pnl", "unrealized_pnl", "total_pnl")


def validate_position(row, user: str | None = None) -> tuple[dict | None, str | None]:
    """A position row (kept for information; the test does not decide on it): wallet, token and condition strings, a
    status string, the money fields numbers."""
    if not isinstance(row, dict):
        return None, "not_an_object"
    if not _is_str(row.get("proxy_wallet")):
        return None, "bad_proxy_wallet"
    if user is not None and row["proxy_wallet"].lower() != user.lower():
        return None, "other_wallet"
    for k in ("token_id", "condition_id", "status"):
        if not _is_str(row.get(k)):
            return None, "bad_" + k
    for k in POSITION_NUM:
        if not is_number(row.get(k)):
            return None, "bad_" + k
    out = {k: row[k] for k in ("proxy_wallet", "token_id", "condition_id", "status") + POSITION_NUM}
    for k in ("event_slug", "event_id", "opposite_token_id"):
        out[k] = row.get(k) if isinstance(row.get(k), str) else None
    for k in ("first_entry_at", "last_event_at", "outcome_index"):
        out[k] = row.get(k) if _is_int(row.get(k)) else None
    return out, None


def validate_resolution(row, ts_max: int | None = None) -> tuple[dict | None, str | None]:
    """A condition-keyed resolution row: condition_id and status strings; payouts None or exactly two integers
    (micro-USDC a share) not below 0; resolved_at None or an RFC3339 time with its zone from 2020-01-01 to `ts_max`,
    also given as `resolved_ts`."""
    if not isinstance(row, dict):
        return None, "not_an_object"
    if not _is_str(row.get("condition_id")):
        return None, "bad_condition_id"
    if not _is_str(row.get("status")):
        return None, "bad_status"
    pay = row.get("payouts")
    if pay is not None and not (isinstance(pay, list) and len(pay) == 2 and all(_is_int(x) and x >= 0 for x in pay)):
        return None, "bad_payouts"
    ra = row.get("resolved_at")
    ts = None
    if ra is not None:
        ts = rfc3339_to_ts(ra)
        if ts is None:
            return None, "bad_resolved_at"
        if _ts_why(ts, ts_max):
            return None, "resolved_at_out_of_span"
    return {"condition_id": row["condition_id"], "status": row["status"], "payouts": list(pay) if pay else None,
            "resolved_at": ra, "resolved_ts": ts}, None


def validate_price_point(row, ts_max: int | None = None) -> tuple[dict | None, str | None]:
    """A price-history point: price a number from 0 to 1, resolution_seconds an int not below 0, timestamp whole
    seconds from 2020-01-01 to `ts_max` (the start of the span the point was observed in)."""
    if not isinstance(row, dict):
        return None, "not_an_object"
    p, res, ts = row.get("price"), row.get("resolution_seconds"), row.get("timestamp")
    if not is_number(p) or not 0 <= p <= 1:
        return None, "bad_price"
    if not _is_int(res) or res < 0:
        return None, "bad_resolution_seconds"
    why = _ts_why(ts, ts_max)
    if why:
        return None, why
    return {"price": p, "resolution_seconds": res, "timestamp": ts}, None


# ---------------------------------------------------------------- a walk's result
@dataclass
class Walk:
    """Everything one walk read: the valid rows (and each one's read time), every raw row with its reject reason
    (None when kept), the reject counts, the number of pages and each page's read time."""
    rows: list = field(default_factory=list)
    row_read_at: list = field(default_factory=list)
    raw: list = field(default_factory=list)
    raw_read_at: list = field(default_factory=list)
    rejects: Counter = field(default_factory=Counter)
    pages: int = 0
    read_at: list = field(default_factory=list)

    def reject(self, row, why: str, read_at: str) -> None:
        self.raw.append((row, why))
        self.raw_read_at.append(read_at)
        self.rejects[why] += 1

    def keep(self, raw, good, read_at: str) -> None:
        self.raw.append((raw, None))
        self.raw_read_at.append(read_at)
        self.rows.append(good)
        self.row_read_at.append(read_at)

    def add(self, row, validator, read_at: str, identity=None, ids: dict | None = None,
            conflicted: set | None = None) -> None:
        good, why = validator(row)
        if why is None and identity is not None:
            k, sig = identity(good), _signature(row)
            if k not in ids:
                ids[k] = sig
            elif ids[k] == sig:
                why = "repeated_row"
            else:
                why = "conflicting_row"
                if conflicted is not None:
                    conflicted.add(k)
        self.raw.append((row, why))
        self.raw_read_at.append(read_at)
        if why is None:
            self.rows.append(good)
            self.row_read_at.append(read_at)
        else:
            self.rejects[why] += 1

    def drop_conflicted(self, identity, conflicted: set) -> None:
        """Turn every kept row whose identity is in conflict into a counted conflict (none of them is kept)."""
        if not conflicted:
            return
        rows, at, k = [], [], 0
        for i, (raw, why) in enumerate(self.raw):
            if why is not None:
                continue
            good, when = self.rows[k], self.row_read_at[k]
            k += 1
            if identity(good) in conflicted:
                self.raw[i] = (raw, "conflicting_row")
                self.rejects["conflicting_row"] += 1
            else:
                rows.append(good)
                at.append(when)
        self.rows, self.row_read_at = rows, at

    @classmethod
    def from_rows(cls, rows, read_at: str) -> "Walk":
        """A one-page walk of rows taken as valid (for building synthetic stores)."""
        rows = list(rows)
        return cls(rows, [read_at] * len(rows), [(r, None) for r in rows], [read_at] * len(rows), Counter(), 1,
                   [read_at])


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _signature(raw) -> str:
    """The whole row, for telling a true repeat from a conflict."""
    return json.dumps(raw, sort_keys=True, default=repr)


# ---------------------------------------------------------------- the client
class PolymarketData:
    """Read-only client. `fetch(url)` is the transport (urllib by default; a fake in tests); it may answer bytes or
    (bytes, Date header)."""

    def __init__(self, fetch: Callable[[str], Any] = urllib_get, *, max_per_second: float = 3.0, tries: int = 5,
                 sleep: Callable[[float], Any] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], str] = _now, wall: Callable[[], float] = time.time):
        self.fetch, self.tries, self.sleep, self.now = fetch, tries, sleep, now
        self.ts_max = int(wall()) + TS_AFTER_START        # the latest time a row may carry
        self.pacer = Pacer(1.0 / max_per_second + 0.01, sleep, clock)    # a hair over 1/3 s: never 4 in a second
        self.last_date: str | None = None
        self.requests = 0

    def get_json(self, path: str, params: dict | None = None):
        """GET base + path (a /v2/ path) with the query `params`; retries 429, 5xx and transport errors (a timeout, a
        reset, a dropped connection) with waits of 1, 2, 4, 8 s (or Retry-After, at most 60 s)."""
        if not (isinstance(path, str) and re.fullmatch(PATH_RE, path)):
            raise PolymarketDataError("refused path %r" % (path,))
        url = BASE_URL + path + ("?" + urllib.parse.urlencode(params) if params else "")
        last: HttpError | None = None
        last_err: str | None = None
        for attempt in range(self.tries):
            if attempt:
                wait = last.retry_after if last is not None and last.retry_after else float(2 ** (attempt - 1))
                self.sleep(min(wait, 60.0))
            self.pacer.wait()
            self.requests += 1
            try:
                body = self.fetch(url)
                if isinstance(body, tuple):
                    body, self.last_date = body
            except HttpError as e:
                if e.status == 429 or e.status >= 500:
                    last, last_err = e, "HTTP %s" % e.status
                    continue
                raise
            except (OSError, http.client.HTTPException) as e:          # timeouts, resets, dropped connections
                last, last_err = None, "%s: %s" % (type(e).__name__, e)
                continue
            try:
                return json.loads(body)
            except ValueError as e:
                raise PolymarketDataError("not JSON from %s: %s" % (url, e)) from None
        raise PolymarketDataError("gave up after %d tries: %s at %s" % (self.tries, last_err or "?", url))

    def walk(self, path: str, params: dict, validator, *, page_cap: int = DEFAULT_PAGE_CAP, max_rows: int | None = None,
             cursor_alone: bool = False, identity=None, drop_conflicts: bool = False) -> Walk:
        """Every row of a cursor-paged list route, validated, each identity once (`identity(row)`; a repeat is a
        counted "repeated_row"). Stops when has_more is false (or, with `max_rows`, once that many rows were served).
        Raises on a wrong envelope, has_more without a cursor, a repeated cursor, or a `page_cap`-th page that still
        promises more. With `drop_conflicts`, no row of an identity in conflict is kept."""
        ids: dict = {}
        conflicted: set = set()
        w = self._walk(path, params, validator, page_cap, max_rows, cursor_alone, identity, ids, conflicted)
        if drop_conflicts:
            w.drop_conflicted(identity, conflicted)
        return w

    def _walk(self, path, params, validator, page_cap, max_rows, cursor_alone, identity, ids, conflicted) -> Walk:
        w, seen, cursor = Walk(), set(), None
        resend = {k: v for k, v in params.items() if k != "limit"}
        while True:
            if w.pages >= page_cap:
                raise PageCapHit("page cap %d reached at %s %s with more pages promised" % (page_cap, path, resend))
            q = dict(params) if cursor is None else ({"cursor": cursor} if cursor_alone else dict(resend, cursor=cursor))
            ans = self.get_json(path, q)
            read_at = self.now()
            rows, more, nxt = self._envelope(ans, path)
            w.pages += 1
            w.read_at.append(read_at)
            for row in rows:
                if max_rows is not None and len(w.raw) >= max_rows:
                    return w
                w.add(row, validator, read_at, identity, ids, conflicted)
            if max_rows is not None and len(w.raw) >= max_rows:
                return w
            if not more:
                return w
            if nxt in seen:
                raise PolymarketDataError("repeated cursor %r at %s" % (nxt, path))
            seen.add(nxt)
            cursor = nxt

    @staticmethod
    def _envelope(ans, path: str):
        if not isinstance(ans, dict) or not isinstance(ans.get("data"), list):
            raise PolymarketDataError("no data list in the answer from %s" % path)
        pg = ans.get("pagination")
        if not isinstance(pg, dict) or not isinstance(pg.get("has_more"), bool):
            raise PolymarketDataError("no pagination with a has_more flag in the answer from %s" % path)
        nxt = pg.get("next_cursor")
        if pg["has_more"] and not _is_str(nxt):
            raise PolymarketDataError("has_more with no next_cursor from %s" % path)
        if nxt is not None and not isinstance(nxt, str):
            raise PolymarketDataError("next_cursor is not a string from %s" % path)
        return ans["data"], pg["has_more"], nxt

    # ------------------------------------------------------------ routes
    def leaderboard(self, time_period: str, sort_by: str = "PNL", rows: int = 100, page_cap: int = 50) -> Walk:
        """The first `rows` rows of a board (`time_period` day, week, month or all; `sort_by` PNL or VOLUME)."""
        if time_period not in BOARD_WINDOWS or sort_by not in ("PNL", "VOLUME"):
            raise ValueError("unknown board %r %r" % (time_period, sort_by))
        params = {"time_period": time_period, "sort_by": sort_by, "limit": min(int(rows), 1000)}
        return self.walk("/v2/leaderboard", params, validate_board_row, page_cap=page_cap, max_rows=int(rows),
                         cursor_alone=True)

    def trades(self, user: str, start: int = 1, page_cap: int = DEFAULT_PAGE_CAP) -> Walk:
        """A wallet's trades, both its taker and maker fills (`taker_only=false`), from `start` (1: the full history),
        newest first as served."""
        params = {"user": user, "start": int(start), "limit": 1000, "taker_only": "false"}
        return self.walk("/v2/trades", params, lambda r: validate_trade(r, user, self.ts_max), page_cap=page_cap,
                         identity=trade_identity)

    def positions(self, user: str, status: str | None = None, page_cap: int = DEFAULT_PAGE_CAP) -> Walk:
        """A wallet's positions: open (the default, `status` None) or closed (`status` "CLOSED")."""
        if status not in POSITION_STATUSES:
            raise ValueError("status must be None (open) or CLOSED")
        params: dict = {"user": user}
        if status:
            params["status"] = status
        params["limit"] = 1000
        return self.walk("/v2/positions", params, lambda r: validate_position(r, user), page_cap=page_cap)

    def resolutions(self, conditions) -> Walk:
        """The resolution rows of 1 to 20 condition ids in one GET (the route is not paged; a miss is simply absent)."""
        conds = list(conditions)
        if not 1 <= len(conds) <= MAX_CONDITIONS or not all(_is_str(c) and "," not in c for c in conds):
            raise ValueError("one to twenty condition ids")
        ans = self.get_json("/v2/resolutions", {"condition": ",".join(conds)})
        read_at = self.now()
        if not isinstance(ans, dict) or not isinstance(ans.get("data"), list):
            raise PolymarketDataError("no data list in the answer from /v2/resolutions")
        pg = ans.get("pagination")
        if pg is not None and (not isinstance(pg, dict) or pg.get("has_more") is not False):
            raise PolymarketDataError("/v2/resolutions answered a paged list; this reader cannot page it")
        w, ids = Walk(pages=1, read_at=[read_at]), {}
        for row in ans["data"]:
            w.add(row, lambda r: validate_resolution(r, self.ts_max), read_at,
                  lambda good: json.dumps(good, sort_keys=True), ids)
        return w

    def resolution_batches(self, conditions):
        """Every id's resolution, twenty ids a request. Yields (batch, walk, answers) per request; `answers` holds one
        word per id asked: "row" (exactly one valid row), "no_row" (none: unresolved or unknown), "rejected" (only
        rows of the wrong shape), "row_and_reject" or "duplicate" (not trusted)."""
        conds = list(dict.fromkeys(conditions))
        for i in range(0, len(conds), MAX_CONDITIONS):
            batch = conds[i:i + MAX_CONDITIONS]
            w = self.resolutions(batch)
            good: Counter = Counter(r["condition_id"] for r in w.rows)
            bad: Counter = Counter(raw.get("condition_id") for raw, why in w.raw
                                   if why and why != "repeated_row" and isinstance(raw, dict))
            answers = {}
            for c in batch:
                if good[c] > 1:
                    answers[c] = "duplicate"
                elif good[c] == 1:
                    answers[c] = "row_and_reject" if bad[c] else "row"
                else:
                    answers[c] = "rejected" if bad[c] else "no_row"
            yield batch, w, answers

    def prices_history(self, token_id: str, start: int, end: int, page_cap: int = 100) -> Walk:
        """One outcome token's price points in the window [start, end) (epoch seconds: start inclusive, end exclusive;
        the service picks the grain, given per point as `resolution_seconds`). Parameters sent: token_id, start, end
        (no bucket_seconds, no limit: the first page holds up to 10,000 points); a cursor page resends the three."""
        if not _is_str(token_id) or not (_is_int(start) and _is_int(end)) or not 0 < start < end:
            raise ValueError("a token id and 0 < start < end, whole seconds")
        params = {"token_id": token_id, "start": start, "end": end}
        return self.walk("/v2/prices-history", params, lambda r: validate_price_point(r, self.ts_max),
                         page_cap=page_cap, identity=lambda p: (token_id, p["timestamp"], p["resolution_seconds"]),
                         drop_conflicts=True)
