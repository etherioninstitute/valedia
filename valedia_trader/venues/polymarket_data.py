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
  2020-01-01 to one day after the client was made (a time in milliseconds is out of that span). No validator
  raises, whatever value of whatever size or type sits in whatever field: a whole number beyond a float's range
  takes the too-large reason where that rule applies, else its field's own reason; a string that cannot be stored
  or printed (it holds a NUL, or a lone surrogate that UTF-8 cannot encode) is malformed (T52 addendum 2).
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
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

__all__ = ["BASE_URL", "USER_AGENT", "HttpError", "PolymarketDataError", "PageCapHit", "Pacer", "urllib_get", "Walk",
           "PolymarketData", "is_number", "validate_board_row", "validate_trade", "validate_position",
           "validate_resolution", "validate_price_point", "trade_identity", "position_identity", "TS_MIN",
           "BOARD_CATEGORIES"]

BASE_URL = "https://data-api.polymarket.com"
USER_AGENT = "market-data-reader/1.0"
DEFAULT_PAGE_CAP = 5000            # pages of a walk; a wallet with more than 5 million trades raises, never truncates
BOARD_WINDOWS = ("day", "week", "month", "all")
BOARD_CATEGORIES = ("sports", "crypto")    # T52: a board per category; None is the overall board
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
    """A walk reached its page cap with more pages still promised; `pages` is how many it read."""

    def __init__(self, msg: str, pages: int = 0):
        super().__init__(msg)
        self.pages = pages


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
    """A finite int or float that is not a bool. Never raises: every whole number is finite whatever its size (one
    beyond a float's range is a number here, and each field's own rule says what becomes of it)."""
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    return isinstance(v, float) and math.isfinite(v)


def _floats(v) -> bool:
    """A number that a float can hold (a whole number beyond a float's range cannot)."""
    try:
        float(v)
    except OverflowError:
        return False
    return True


TOO_LARGE = "number_too_large_for_store"      # a whole number outside SQLite's 64-bit integers (2**63 or more)


def _fits_store(values) -> bool:
    """Every whole number among `values` (and inside lists among them, at any depth: walked without recursion)
    fits SQLite's 64-bit integer; a row with one that does not is malformed (writing it would end the pull with an
    OverflowError)."""
    stack = [iter(values)]
    while stack:
        for v in stack[-1]:
            if isinstance(v, list):
                stack.append(iter(v))
                break
            if isinstance(v, int) and not isinstance(v, bool) and not -2 ** 63 <= v < 2 ** 63:
                return False
        else:
            stack.pop()
    return True


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_str(v, nonempty: bool = True) -> bool:
    return isinstance(v, str) and (bool(v) or not nonempty)


def _storable(v: str) -> bool:
    """A string that can be stored and printed: no NUL character, and encodable as UTF-8 (no lone surrogate)."""
    if "\x00" in v:
        return False
    if v.isascii():
        return True
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _is_text(v, nonempty: bool = True) -> bool:
    """A row's string field: a string (non-empty unless allowed) that can be stored and printed."""
    return _is_str(v, nonempty) and _storable(v)


def _text_or_none(v):
    return v if _is_text(v, nonempty=False) else None


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
        if dt.tzinfo is None:
            return None
        return int(dt.timestamp())
    except (ValueError, OverflowError):
        return None


def validate_board_row(row) -> tuple[dict | None, str | None]:
    """A profit-board row: rank int, user_id non-empty str, pnl and volume numbers. Other fields are kept raw only."""
    if not isinstance(row, dict):
        return None, "not_an_object"
    if not _is_int(row.get("rank")):
        return None, "bad_rank"
    if not _is_text(row.get("user_id")):
        return None, "bad_user_id"
    for k in ("pnl", "volume"):
        if not is_number(row.get(k)):
            return None, "bad_" + k
    if not _fits_store((row["rank"], row["pnl"], row["volume"])):
        return None, TOO_LARGE
    return {"rank": row["rank"], "user_id": row["user_id"], "pnl": float(row["pnl"]), "volume": float(row["volume"])}, None


TRADE_STR = ("token_id", "condition_id")


def validate_trade(row, user: str | None = None, ts_max: int | None = None) -> tuple[dict | None, str | None]:
    """A trade row: side BUY or SELL; token and condition non-empty strings; size a number above 0; price a number from
    0 to 1; timestamp whole seconds from 2020-01-01 to `ts_max` (default: a day from now); outcome_index 0 or 1;
    event_slug and transaction_hash strings (may be empty). With `user`, the row's proxy_wallet must be that wallet
    (case folded)."""
    if not isinstance(row, dict):
        return None, "not_an_object"
    if not _is_text(row.get("proxy_wallet")):
        return None, "bad_proxy_wallet"
    if user is not None and row["proxy_wallet"].lower() != user.lower():
        return None, "other_wallet"
    if row.get("side") not in ("BUY", "SELL"):
        return None, "bad_side"
    for k in TRADE_STR:
        if not _is_text(row.get(k)):
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
        if not _is_text(row.get(k), nonempty=False):
            return None, "bad_" + k
    out = {k: row[k] for k in ("proxy_wallet", "side", "token_id", "condition_id", "size", "price", "timestamp",
                               "event_slug", "outcome_index", "transaction_hash")}
    for k in ("slug", "title", "outcome"):
        out[k] = _text_or_none(row.get(k))
    if not _fits_store(out.values()):
        return None, TOO_LARGE
    return out, None


def trade_identity(t: dict) -> tuple:
    """The identity of a validated trade row (the feed has no fill id)."""
    return (t["proxy_wallet"].lower(), t["transaction_hash"], t["token_id"], t["condition_id"], t["side"], t["size"],
            t["price"], t["timestamp"], t["outcome_index"])


POSITION_NUM = ("current_size", "avg_price", "entry_cost_usdc", "total_cost_usdc", "current_value", "total_size",
                "realized_pnl", "unrealized_pnl", "total_pnl")


TITLE_MAX, OUTCOME_MAX = 500, 200
_END_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
POSITION_INFO = ("title", "outcome", "end_date", "current_price")


def _info_fields(row: dict) -> dict:
    """The four information fields of a position (T52): title and outcome non-empty strings of at most TITLE_MAX and
    OUTCOME_MAX characters, end_date a real calendar date YYYY-MM-DD other than the service's 1970-01-01 for none,
    current_price a number from 0 to 1. A missing or malformed one is None; none of them can reject a row."""
    title, outcome, end, price = (row.get(k) for k in POSITION_INFO)
    out = {"title": title if _is_text(title) and len(title) <= TITLE_MAX else None,
           "outcome": outcome if _is_text(outcome) and len(outcome) <= OUTCOME_MAX else None,
           "end_date": None, "current_price": float(price) if is_number(price) and 0 <= price <= 1 else None}
    if isinstance(end, str) and _END_DATE.fullmatch(end) and end != "1970-01-01":
        try:
            datetime.strptime(end, "%Y-%m-%d")
            out["end_date"] = end
        except ValueError:
            pass
    return out


def validate_position(row, user: str | None = None) -> tuple[dict | None, str | None]:
    """A position row: wallet, token and condition strings, a status string, the money fields numbers (these decide
    a reject). The information fields (POSITION_INFO, see `_info_fields`) are kept or None, and `unread` names those
    that were None. A first entry of 0 (the service's "none recorded") is None."""
    if not isinstance(row, dict):
        return None, "not_an_object"
    if not _is_text(row.get("proxy_wallet")):
        return None, "bad_proxy_wallet"
    if user is not None and row["proxy_wallet"].lower() != user.lower():
        return None, "other_wallet"
    for k in ("token_id", "condition_id", "status"):
        if not _is_text(row.get(k)):
            return None, "bad_" + k
    for k in POSITION_NUM:
        if not is_number(row.get(k)) or not _floats(row[k]):       # beyond a float's range: that field's reject
            return None, "bad_" + k
    out = {k: row[k] for k in ("proxy_wallet", "token_id", "condition_id", "status") + POSITION_NUM}
    for k in ("event_slug", "event_id", "opposite_token_id"):
        out[k] = _text_or_none(row.get(k))
    for k in ("first_entry_at", "last_event_at", "outcome_index"):
        out[k] = row.get(k) if _is_int(row.get(k)) else None
    if out["first_entry_at"] == 0:
        out["first_entry_at"] = None
    info = _info_fields(row)
    out.update(info)
    out["unread"] = tuple(k for k in POSITION_INFO if info[k] is None)
    return out, None


def position_identity(p: dict) -> tuple:
    """The identity of a validated position row: the wallet folded to lower case and the outcome token."""
    return (p["proxy_wallet"].lower(), p["token_id"])


def validate_resolution(row, ts_max: int | None = None) -> tuple[dict | None, str | None]:
    """A condition-keyed resolution row: condition_id and status strings; payouts None or exactly two integers
    (micro-USDC a share) not below 0; resolved_at None or an RFC3339 time with its zone from 2020-01-01 to `ts_max`,
    also given as `resolved_ts`."""
    if not isinstance(row, dict):
        return None, "not_an_object"
    if not _is_text(row.get("condition_id")):
        return None, "bad_condition_id"
    if not _is_text(row.get("status")):
        return None, "bad_status"
    pay = row.get("payouts")
    if pay is not None and not (isinstance(pay, list) and len(pay) == 2 and all(_is_int(x) and x >= 0 for x in pay)):
        return None, "bad_payouts"
    ra = row.get("resolved_at")
    ts = None
    if ra is not None:
        ts = rfc3339_to_ts(ra) if _is_text(ra) else None
        if ts is None:
            return None, "bad_resolved_at"
        if _ts_why(ts, ts_max):
            return None, "resolved_at_out_of_span"
    if pay is not None and not _fits_store(pay):
        return None, TOO_LARGE
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
    if not _fits_store((p, res, ts)):
        return None, TOO_LARGE
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
    unread: Counter = field(default_factory=Counter)      # information fields read as None (positions only)

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
    """The whole row, for telling a true repeat from a conflict. A row nested too deep to write out gets a mark of
    its own (no JSON text starts with "u"), equal to no other row's, in this process or another: a second row of its
    identity is then a conflict, never a repeat."""
    try:
        return json.dumps(raw, sort_keys=True, default=repr)
    except RecursionError:
        return "unsigned:" + uuid.uuid4().hex


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
                raise PageCapHit("page cap %d reached at %s %s with more pages promised" % (page_cap, path, resend),
                                 w.pages)
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

    def walk_pages(self, path: str, params: dict, validator, *, page_cap: int = DEFAULT_PAGE_CAP,
                   cursor_alone: bool = False, identity=None, ids=None, conflicted: set | None = None):
        """The same walk as `walk`, one page at a time: yields, per page as it arrives, a Walk of that page's rows only
        (pages 1), and asks for the next page only when the caller comes back for it. `ids` (any mapping from an
        identity to the signature of its first valid row) and `conflicted` (identities in conflict) span the pages;
        the caller holds them (the pull keeps `ids` on disk), so the process holds one page whatever the walk's size.
        Conflicts are only marked: the caller drops them. Raises as `walk` does; at the cap, PageCapHit (its `pages`)
        once `page_cap` pages were yielded with more promised."""
        if identity is not None and ids is None:
            ids = {}
        seen, cursor, pages = set(), None, 0
        resend = {k: v for k, v in params.items() if k != "limit"}
        while True:
            if pages >= page_cap:
                raise PageCapHit("page cap %d reached at %s %s with more pages promised" % (page_cap, path, resend),
                                 pages)
            q = dict(params) if cursor is None else ({"cursor": cursor} if cursor_alone else dict(resend, cursor=cursor))
            ans = self.get_json(path, q)
            read_at = self.now()
            rows, more, nxt = self._envelope(ans, path)
            if more and nxt in seen:
                raise PolymarketDataError("repeated cursor %r at %s" % (nxt, path))
            pages += 1
            w = Walk(pages=1, read_at=[read_at])
            for row in rows:
                w.add(row, validator, read_at, identity, ids, conflicted)
            del rows, ans
            yield w
            if not more:
                return
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
    def leaderboard(self, time_period: str, sort_by: str = "PNL", rows: int = 100, page_cap: int = 50,
                    category: str | None = None) -> Walk:
        """The first `rows` rows of a board (`time_period` day, week, month or all; `sort_by` PNL or VOLUME;
        `category` None for the overall board or one of BOARD_CATEGORIES, anything else refused before a request).
        A category the service does not know answers an empty board, not an error: the caller counts the rows."""
        if time_period not in BOARD_WINDOWS or sort_by not in ("PNL", "VOLUME"):
            raise ValueError("unknown board %r %r" % (time_period, sort_by))
        if category is not None and not (type(category) is str and category in BOARD_CATEGORIES):
            raise ValueError("unknown board category %r" % (category,))
        params = {"time_period": time_period, "sort_by": sort_by}
        if category is not None:
            params["category"] = category
        params["limit"] = min(int(rows), 1000)
        return self.walk("/v2/leaderboard", params, validate_board_row, page_cap=page_cap, max_rows=int(rows),
                         cursor_alone=True)

    def trades(self, user: str, start: int = 1, page_cap: int = DEFAULT_PAGE_CAP) -> Walk:
        """A wallet's trades, both its taker and maker fills (`taker_only=false`), from `start` (1: the full history),
        newest first as served."""
        params = {"user": user, "start": int(start), "limit": 1000, "taker_only": "false"}
        return self.walk("/v2/trades", params, lambda r: validate_trade(r, user, self.ts_max), page_cap=page_cap,
                         identity=trade_identity)

    def trade_pages(self, user: str, start: int = 1, page_cap: int = DEFAULT_PAGE_CAP, ids=None):
        """`trades`, one page at a time (see walk_pages); `ids` spans the pages."""
        params = {"user": user, "start": int(start), "limit": 1000, "taker_only": "false"}
        return self.walk_pages("/v2/trades", params, lambda r: validate_trade(r, user, self.ts_max), page_cap=page_cap,
                               identity=trade_identity, ids=ids)

    def positions(self, user: str, status: str | None = None, page_cap: int = DEFAULT_PAGE_CAP) -> Walk:
        """A wallet's positions: open (the default, `status` None) or closed (`status` "CLOSED")."""
        if status not in POSITION_STATUSES:
            raise ValueError("status must be None (open) or CLOSED")
        params: dict = {"user": user}
        if status:
            params["status"] = status
        params["limit"] = 1000
        w = self.walk("/v2/positions", params, lambda r: validate_position(r, user), page_cap=page_cap,
                      identity=position_identity, drop_conflicts=True)
        w.unread = Counter(k for r in w.rows for k in r["unread"])
        return w

    def position_pages(self, user: str, page_cap: int = DEFAULT_PAGE_CAP, ids=None, conflicted: set | None = None):
        """A wallet's open positions one page at a time (see walk_pages): repeats are counted per page, identities in
        conflict are only marked in `conflicted` (the caller drops them); `ids` and `conflicted` span the pages."""
        return self.walk_pages("/v2/positions", {"user": user, "limit": 1000}, lambda r: validate_position(r, user),
                               page_cap=page_cap, identity=position_identity, ids=ids, conflicted=conflicted)

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
                                   if why and why != "repeated_row" and isinstance(raw, dict)
                                   and isinstance(raw.get("condition_id"), str))      # an answer only to a string id
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

    def price_pages(self, token_id: str, start: int, end: int, page_cap: int = 100, ids=None,
                    conflicted: set | None = None):
        """`prices_history`, one page at a time (see walk_pages): identities in conflict are only added to
        `conflicted`; the caller drops every point of them at the walk's end, as `prices_history` does."""
        if not _is_str(token_id) or not (_is_int(start) and _is_int(end)) or not 0 < start < end:
            raise ValueError("a token id and 0 < start < end, whole seconds")
        params = {"token_id": token_id, "start": start, "end": end}
        return self.walk_pages("/v2/prices-history", params, lambda r: validate_price_point(r, self.ts_max),
                               page_cap=page_cap, identity=lambda p: price_identity(token_id, p), ids=ids,
                               conflicted=conflicted)


def price_identity(token_id: str, p: dict) -> tuple:
    """The identity of a validated price point: the token, the time and the grain."""
    return (token_id, p["timestamp"], p["resolution_seconds"])
