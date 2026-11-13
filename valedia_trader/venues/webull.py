"""Webull, through its official OpenAPI and Python SDK (webull-openapi-python-sdk 3.x).

    python -m valedia_trader.venues.webull check        connect, list the accounts, read balance and positions

Two environments exist: Webull's test environment (api.sandbox.webull.com) and production
(api.webull.com). This adapter uses the test environment unless told otherwise, and it never sends an
order to production on its own:

* Reading (accounts, balance, positions) works in either environment.
* An order goes to production only if the owner has written `config/webull_live.json` by hand, naming
  the account and a per-order value cap. Without that file, or if it names another account, or if the
  order is larger than the cap, `submit` raises. Nothing in this repository creates that file.
* It is long-only: an order that would sell more than is held is refused, so it cannot open a short.

Credentials come from the secrets file (WEBULL_APP_KEY, WEBULL_APP_SECRET, optional WEBULL_ACCOUNT_ID,
WEBULL_ENV = sandbox | production). They are never printed or logged: the SDK's own logger, which writes whole
requests with their headers when a call fails, is silenced, and errors are rebuilt from status fields only.
Raised messages may carry Webull's own error text (its error_code and up to 160 characters of its message, or
the HTTP library's text for a transport failure). Every text taken from an exception or an error body, on every
path including the constructor, is scrubbed before it is used: the App Key becomes <app-key>, the App Secret
<app-secret> and the account id, when known, <account> (replaced in the whole text before it is cut, so no
fragment survives the cut), then anything that looks like a URL, an absolute path or a query string becomes
<url>. Nothing that was sent to Webull (headers, keys, the request, its URL) is copied into a message. Reading
an exception's fields can never raise: a field that cannot be read counts as absent.
The SDK is imported only when a real connection is made (it opens a session at once, a network call), so tests
and the rest of the trader do not need it installed. Webull may ask for a one-time approval in its app the
first time a key is used (token-based two-factor check).

Interface facts are from developer.webull.com (read 2026-11-11): ApiClient(app_key, app_secret, "us"),
add_endpoint("us", host), TradeClient.account_v2.get_account_list / get_account_balance /
get_account_position, TradeClient.order_v3.place_order(account_id, new_orders) / cancel_order.

Reading orders and cash (added 2026-11-12). "SDK" means webull-openapi-python-sdk 3.0.4 as installed under
.venv/lib/python3.12/site-packages/webull/; "docs" means the Markdown form of the developer.webull.com pages
(append ".md" to the page address), read 2026-11-12.

* order_v3 is OrderOperationV3 (SDK trade/trade_client.py:36). Every order call here goes through it, because
  place_order goes through it: its paths are /trading/orders/* (SDK trade/request/v3/*.py), the paths the docs
  describe. order_v2 (SDK trade/trade/v2/order_operation_v2.py) calls the older /openapi/trade/order/* paths
  (SDK trade/request/v2/get_order_detail_request.py:20) and is not used.
* order_v3.get_order_detail(account_id, client_order_id): SDK trade/trade/v3/order_operation_v3.py:93, GET
  /trading/orders/get (SDK trade/request/v3/get_order_detail_request.py). Docs /apis/docs/reference/order-detail:
  the 200 body is {client_order_id, combo_order_id, combo_type, orders: [...]}, and each item of `orders` carries
  client_order_id, order_id (Webull's own id), symbol, side (BUY, SELL, SHORT), status, instrument_type,
  total_quantity, filled_quantity, filled_price ("may be zero or null" before a fill), commission
  {actual_commission, receivable_commission} and fees [{type, actual_value, receivable_value}], all as strings.
  The order is looked for in that combo shape, in a plain order (a body that carries `status` itself) and in a
  list of either. NOT CONFIRMED: that the US API ever answers with the plain or the list shape.
* Order statuses: docs order-detail, field orders[].status, enum PENDING, SUBMITTED, CANCELLED, FILLED, FAILED
  ("a failed order, such as REJECTED"), PARTIAL_FILLED. The SDK disagrees on one spelling: its OrderStatus
  (SDK trade/common/order_status.py:21-25) writes "PARTIAL FILLED" with a space and has no PENDING. Both
  spellings are mapped. No documented status means expired, so nothing maps to `expired` yet; an expired order
  that Webull reports under a status not in the table shows as `unknown_status`.
* No such order: NOT CONFIRMED. The order-detail page documents only 200, 401, 417 and 500 answers, and the
  error-code page (developer.webull.hk/apis/docs/error-codes.md; developer.webull.com has none) lists no
  order-not-found code. A search engine's copy of an older Webull HK error table (developer.webull.hk/api-doc/
  develop/error, which now redirects) listed ORDER_NOT_FOUND as "the order does not exist". Nothing documents
  that a 200 with an empty `orders` list means "no such order" either. Two answers are candidates, read
  exactly: (a) a 200 whose body is an object with `orders` an empty list, no `status`, `error_code` or `message`
  key, and no `client_order_id` other than ours, and (b) the exact
  code ORDER_NOT_FOUND (no case folding, no trimming) with a 4xx HTTP status, carried by the SDK's server-error
  exception (it has `error_code` and `http_status`, SDK core/exception/exceptions.py:31-37; a transport
  ClientException has no `http_status` and never counts) or by a non-2xx answer's body. order_status returns None
  for them only when the venue is built with confirmed_not_found=True; by default (False) both raise
  WebullError saying that what they mean is not yet confirmed. Turn the flag on only after the answer has been
  observed in Webull's test environment for a client order id that was never sent. Everything else, a bare
  404, a bare [] and an `orders` list holding anything that is not an object included, raises: nothing
  malformed is ever read as "no such order" or silently skipped.
* order_v3.list_order_open(account_id, pagination_key=None): SDK order_operation_v3.py:204, GET
  /trading/orders/open-orders/list with the v3 version header (SDK trade/request/v3/get_order_open_request_v2.py).
  Docs /apis/docs/reference/order-open: the body is {data: [combo, ...], pagination_key}, each combo shaped
  like the order-detail body; "If [pagination_key is] absent, indicates this is the last page". The older
  get_order_open (page_size, last_client_order_id) is marked deprecated in the SDK (order_operation_v3.py:173).
  The docs warn that this list may lag; order_status reads the detail call, which they recommend instead.
* order_v3.place_order answer: docs /apis/docs/reference/common-order-place, 200 body {client_order_id,
  client_combo_order_id, combo_order_id, order_id}; `order_id` is Webull's id for the order.
* order_v3.cancel_order(account_id, client_order_id): SDK order_operation_v3.py:82, POST /trading/orders/cancel.
  Docs /apis/docs/reference/common-order-cancel: "Only orders placed via API are supported."
* account_v2.get_account_balance(account_id): SDK trade/trade/v2/account_info_v2.py:36, GET
  /trading/assets/balances/get. Docs /apis/docs/reference/account-balance: total_asset_currency,
  total_cash_balance, and account_currency_assets [{currency, cash_balance, settled_cash, buying_power, ...}],
  all strings.
* Token: the SDK writes its access token in plain text (token, expiry, status, one per line) to token.txt in a
  directory chosen as: ApiClient.set_token_dir (SDK core/client.py:171-172, read by ClientInitializer.init_token
  via get_token_dir, SDK core/http/initializer/client_initializer.py:60), else $WEBULL_OPENAPI_TOKEN_DIR, else
  "conf" under the working directory (SDK core/http/initializer/token/token_storage.py:48-50 and 70-86; written
  by token_manager.py:96-104 with default file permissions). This adapter calls set_token_dir with a private
  directory, ~/.valedia/webull/<environment>/ unless `token_dir` is given, created with mode 0700, and refuses a
  directory inside the repository.
* Quantities: never rounded. `units` must be a real number (not a bool, not a string), finite, non-zero, at most
  1e9, and already on the step; the caller rounds down to the step itself, and the text sent parses back to
  exactly abs(units). Steps: crypto 8 decimals (docs common-order-place, field quantity); equities 5 decimals (NOT
  CONFIRMED: Webull documents fractional quantities without a step; check in the test environment).
* Limit prices: never rounded; a price that is not a finite number above zero, above 1e9, or not already on the
  tick, is refused before any call (NotTransmitted). Ticks: equities 2 decimals at 1.00 and above, 4 below (the US
  sub-penny rule, SEC Regulation NMS Rule 612; Webull's own increments are not documented on developer.webull.com);
  crypto 8 decimals (an assumption: the docs give 8 decimals for crypto quantities only). Both are to be
  checked in the test environment.
* Positions: docs /apis/docs/reference/account-position (read 2026-11-12). The 200 body is a bare list of rows
  {position_id, symbol, quantity, instrument_type, currency, last_price, cost_price, unrealized_profit_loss,
  option_strategy, event_outcome, legs}; instrument_type is one of EQUITY, OPTION, FUTURES, CRYPTO, EVENT, and
  the page's example row is symbol "AAPL", instrument_type "OPTION", quantity "1". Only a row whose
  instrument_type is exactly EQUITY or CRYPTO is a holding (positions()); every other row, one with no, an
  empty or an unrecognised instrument_type included, is reported apart (other_positions()) and licenses no sale.
* Selling: what may be sold is the holding minus the unfilled remainder of every open sell for the same symbol
  and instrument type, read with open_orders() in the same submit, before the order call.
* Numbers: every number Webull sends is read with one rule: absent, unreadable, too large for a float (a huge
  JSON integer) or not finite means None (positions() skips such a row, and a holding above 1e12), a negative
  filled quantity or filled price is None, and a status is matched exactly as documented (else unknown_status).
  Every public method that reads an answer raises only WebullError, whatever the answer holds.
* Errors: the SDK raises ServerException(error_code, error_msg, http_status, request_id) for any answer outside
  200-299 (SDK core/client.py:357-359 and 424-428) and ClientException(error_code, error_msg) for a transport
  failure (SDK core/client.py:328). The SDK logs the whole request with its headers before raising
  (core/client.py:360-362), which is why its logger is silenced.
* Logging: TradeClient.__init__ calls _init_logger (SDK trade/trade_client.py:41-47), which, unless the ApiClient
  says its loggers are set (_stream_logger_set / _file_logger_set, line 43), points logger "webull.core" at INFO
  and adds a StreamHandler on stdout and a TimedRotatingFileHandler on webull_trade_sdk.log in the working
  directory (SDK core/client.py:393-422). This adapter sets both flags before building TradeClient, quiets every
  "webull" logger before and after construction (removing and closing any handler but a NullHandler), and
  removes webull_trade_sdk.log if construction created it empty. Not confirmed against a real TradeClient
  (none is built in tests).
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import logging
import math
import numbers
import os
import re
import sys
from pathlib import Path
from typing import Any

SANDBOX_HOST = "api.sandbox.webull.com"
PRODUCTION_HOST = "api.webull.com"
HOSTS = {"sandbox": SANDBOX_HOST, "production": PRODUCTION_HOST}
REPO = Path(__file__).resolve().parents[2]
LIVE_CONFIRMATION = REPO / "config" / "webull_live.json"

__all__ = ["WebullVenue", "WebullError", "NotTransmitted", "LiveTradingNotConfirmed", "order_payload", "client_order_id",
           "load_live_confirmation", "ORDER_STATES"]

# Webull's order status -> the trader's order state. Source: developer.webull.com/apis/docs/reference/order-detail
# (read 2026-11-12), field orders[].status; "PARTIAL FILLED" is the SDK's spelling (webull/trade/common/order_status.py:25).
# A status not in this table is `unknown_status`, never filled or cancelled.
ORDER_STATES = {"PENDING": "open", "SUBMITTED": "open", "PARTIAL_FILLED": "partly_filled", "PARTIAL FILLED": "partly_filled",
                "FILLED": "filled", "CANCELLED": "cancelled", "FAILED": "rejected"}
# The error code read as "no such order" (with an empty `orders` list), and only with confirmed_not_found=True.
# Not confirmed for the US API: see the module notes.
ORDER_NOT_FOUND_CODES = frozenset({"ORDER_NOT_FOUND"})
# Sanity ceilings: no order of this trader is this large, and they stop absurd text being sent or trusted.
MAX_ORDER_UNITS = 1e9
MAX_LIMIT_PRICE = 1e9
MAX_HOLDING_UNITS = 1e12
MAX_OPEN_ORDER_PAGES = 200
# docs account-position (read 2026-11-12): instrument_type is one of EQUITY, OPTION, FUTURES, CRYPTO, EVENT.
HOLDING_TYPES = frozenset({"EQUITY", "CRYPTO"})                       # the only rows that are holdings
ORDER_INSTRUMENT_TYPES = frozenset({"EQUITY", "OPTION", "FUTURES", "CRYPTO", "EVENT"})
EMPTY_ANSWER_KEYS = frozenset({"client_order_id", "combo_order_id", "combo_type", "orders"})
# Quantity steps: crypto 8 decimals (docs common-order-place, field quantity: "Crypto trading supports quantities with
# up to 8 decimal places"); equities 5 decimals (NOT CONFIRMED: Webull documents fractional quantities without a step).
QUANTITY_STEP_DECIMALS = {"crypto": 8, "equity": 5}


class WebullError(RuntimeError):
    pass


class NotTransmitted(WebullError):
    """A refusal made on this machine before any order call was sent: the order certainly did not reach Webull."""


class LiveTradingNotConfirmed(NotTransmitted):
    """A production order was attempted without the owner's written confirmation."""


def client_order_id(tag: str) -> str:
    """Webull wants an id of at most 32 characters, unique per account. The same tag gives the same id,
    so a retried order cannot be placed twice."""
    return hashlib.sha256(str(tag).encode()).hexdigest()[:32]


def _is_crypto(symbol: str) -> bool:
    return symbol.upper().endswith("-USD")


def _quantity(units: Any, crypto: bool) -> tuple[float, str]:
    """(signed amount, the text sent) or NotTransmitted. The quantity is never rounded: it must be a real number (not a
    bool, not a string), finite, non-zero, at most MAX_ORDER_UNITS, and already on the step (8 decimals for crypto,
    from Webull's docs; 5 for equities, not confirmed: check in the test environment). The caller rounds DOWN to the
    step itself; a quantity that would have to be rounded to be sent is refused. The text parses back to abs(units)."""
    if isinstance(units, bool) or not isinstance(units, numbers.Real):
        raise NotTransmitted(f"a quantity must be a number, not a {type(units).__name__}")
    try:
        amount = float(units)
    except (TypeError, ValueError, OverflowError):
        raise NotTransmitted("the quantity cannot be read as a number") from None
    if not math.isfinite(amount):
        raise NotTransmitted(f"the quantity {amount!r} is not a finite number")
    if amount == 0:
        raise NotTransmitted("an order needs a non-zero quantity")
    if abs(amount) > MAX_ORDER_UNITS:
        raise NotTransmitted(f"the quantity {abs(amount):g} is above the sanity ceiling of {MAX_ORDER_UNITS:g} units")
    places = QUANTITY_STEP_DECIMALS["crypto" if crypto else "equity"]
    text = f"{abs(amount):.{places}f}"
    if float(text) != abs(amount):
        raise NotTransmitted(f"the quantity {abs(amount)!r} is not on the step of {places} decimal places and would have to be "
                             "rounded to be sent; round it down to the step yourself")
    return amount, text.rstrip("0").rstrip(".")


def order_payload(symbol: str, units: float, tag: str, limit_price: float | None = None) -> dict:
    """One order in Webull's format. `units` is signed: positive buys, negative sells what is held. Neither the
    quantity nor the limit price is ever rounded (see _quantity and _limit_text): what is sent is what was asked."""
    crypto = _is_crypto(symbol)
    amount, quantity = _quantity(units, crypto)
    order = {"combo_type": "NORMAL", "client_order_id": client_order_id(tag), "market": "US",
             "symbol": symbol.upper().replace("-", "") if crypto else symbol.upper(),
             "instrument_type": "CRYPTO" if crypto else "EQUITY",
             "order_type": "LIMIT" if limit_price is not None else "MARKET",
             "quantity": quantity, "side": "BUY" if amount > 0 else "SELL", "time_in_force": "GTC" if crypto else "DAY", "entrust_type": "QTY"}
    if limit_price is not None:
        order["limit_price"] = _limit_text(limit_price, crypto)
    if not crypto:
        order["support_trading_session"] = "CORE"
    return order


def _limit_text(limit_price: Any, crypto: bool) -> str:
    """The limit price exactly as given, or NotTransmitted. It is never rounded: rounding a limit changes the worst
    price the trader accepts, so the caller must choose the tick. Ticks: equities at most 2 decimals at 1.00 or
    above and at most 4 below 1.00 (the US sub-penny rule, SEC Regulation NMS Rule 612; Webull's own increments are
    not documented on developer.webull.com, read 2026-11-12); crypto at most 8 decimals (an ASSUMPTION: the docs
    give 8 decimals for crypto quantities only). Both are to be checked in the test environment."""
    if isinstance(limit_price, bool) or not isinstance(limit_price, numbers.Real):
        raise NotTransmitted(f"a limit price must be a number, not a {type(limit_price).__name__}")
    try:
        price = float(limit_price)
    except (TypeError, ValueError, OverflowError):
        raise NotTransmitted("the limit price cannot be read as a number") from None
    if not math.isfinite(price) or not price > 0:
        raise NotTransmitted(f"a limit price must be a finite number above zero, not {price!r}")
    if price > MAX_LIMIT_PRICE:
        raise NotTransmitted(f"the limit price is above the sanity ceiling of {MAX_LIMIT_PRICE:g}")
    places = 8 if crypto else 2 if price >= 1 else 4
    text = f"{price:.{places}f}"
    if float(text) != price or float(text) == 0:
        raise NotTransmitted(f"the limit price {price!r} is not on the tick of {places} decimal places and would have to be "
                             "rounded to be sent; choose the tick yourself, because rounding a limit changes the worst price accepted")
    whole, frac = text.split(".")
    return f"{whole}.{frac.rstrip('0').ljust(2, '0')}"


def load_live_confirmation(path: str | Path = LIVE_CONFIRMATION) -> dict | None:
    """The owner's hand-written permission to trade real money, or None. Malformed means no permission."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text())
    except ValueError:
        return None
    ok = (isinstance(data, dict) and isinstance(data.get("account_id"), str) and data["account_id"]
          and isinstance(data.get("confirmed_by"), str) and data["confirmed_by"].strip()
          and isinstance(data.get("max_order_value"), (int, float)) and not isinstance(data.get("max_order_value"), bool)
          and data["max_order_value"] > 0 and data.get("i_understand_this_trades_real_money") is True)
    return data if ok else None


# TradeClient._init_logger (SDK trade/trade_client.py:41-47) points logger "webull.core" at INFO and adds a StreamHandler
# on stdout and a TimedRotatingFileHandler on this file in the working directory (SDK core/client.py:393-422).
SDK_LOGGERS = ("webull", "webull.core", "webull.core.client")
SDK_LOG_FILE = "webull_trade_sdk.log"


def _quiet_sdk_logging() -> None:
    """The SDK logs whole requests, headers included, when a call fails. Keep the App Key out of every log: every
    "webull" logger is set above CRITICAL and stops propagating, and every handler the SDK added (anything but a
    NullHandler) is removed and closed. Run before and after the client is built: building it re-dirties them."""
    names = set(SDK_LOGGERS) | {n for n in logging.root.manager.loggerDict if n == "webull" or n.startswith("webull.")}
    for name in sorted(names):
        log = logging.getLogger(name)
        log.setLevel(logging.CRITICAL + 1)
        log.propagate = False
        for handler in list(log.handlers):
            if isinstance(handler, logging.NullHandler):
                continue
            log.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass


def _private_token_dir(token_dir: str | Path | None, environment: str) -> Path:
    """The SDK's token directory: outside the repository, created if needed, owner-only (0700). Fails closed.

    The inside-the-repository test does not depend on spelling (letter case on a case-insensitive volume, links,
    ".."): the directory's nearest existing ancestor and every parent above it, and the directory itself once it
    exists, must not be the repository root by os.path.samefile. Text with leading or trailing whitespace is refused,
    because the SDK strips it (token_storage.py:71-73) and would write somewhere else. The returned path, handed as
    a string to set_token_dir, is exactly the directory that was made private."""
    if token_dir is not None and str(token_dir) != str(token_dir).strip():
        raise WebullError("the token directory has leading or trailing whitespace, which the SDK would strip")
    d = Path(token_dir).expanduser() if token_dir is not None else Path.home() / ".valedia" / "webull" / environment
    d = d.absolute()
    if str(d) != str(d).strip():
        raise WebullError("the token directory has leading or trailing whitespace, which the SDK would strip")

    def inside(path: Path) -> bool:
        repo = REPO.resolve()
        if path.resolve() == repo or repo in path.resolve().parents:
            return True
        for q in (path, *path.parents):
            try:
                if q.exists() and os.path.samefile(q, REPO):
                    return True
            except OSError:
                return True                                               # cannot tell: fail closed
        return False

    if inside(d):
        raise WebullError(f"the SDK token directory must be outside the repository, not {d}")
    try:
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(d, 0o700)
        mode = d.stat().st_mode
    except OSError as exc:
        raise WebullError(f"could not prepare the private token directory {d}: {exc.strerror or type(exc).__name__}") from None
    if inside(d):
        raise WebullError(f"the SDK token directory must be outside the repository, not {d}")
    if not d.is_dir() or mode & 0o077:
        raise WebullError(f"the token directory {d} is not private (mode {mode & 0o777:o})")
    return d


def _remove_new_empty_sdk_log(existed_before: bool, path: Path) -> None:
    """Remove the SDK's log file if building the client created it and nothing was written to it."""
    try:
        if not existed_before and path.is_file() and path.stat().st_size == 0:
            path.unlink()
    except OSError:
        pass


_URL = re.compile(r"""[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s'"<>()]+"""                # scheme://...
                  r"""|[^\s'"<>()]*\?[^\s'"<>()]*"""                           # anything with a query string
                  r"""|(?<![\w.])/[\w.\-~%]+(?:/[\w.\-~%]*)+""")                 # an absolute path /a/b


Hidden = tuple  # ((text to hide, placeholder), ...): the App Key, the App Secret and the account id when known


def _hidden(app_key: Any, app_secret: Any, account: Any = None) -> Hidden:
    """The words never to repeat in a message, each as given and trimmed, with their placeholders."""
    pairs = []
    for value, placeholder in ((app_key, "<app-key>"), (app_secret, "<app-secret>"), (account, "<account>")):
        try:
            text = value if isinstance(value, str) else None if value is None else str(value)
        except Exception:
            text = None
        for word in {text, text.strip()} if text else ():
            if word:
                pairs.append((word, placeholder))
    return tuple(pairs)


def _hide(text: str, hidden: Hidden = ()) -> str:
    """text with every hidden word replaced by its placeholder, in one pass, longest first (a placeholder is
    never searched again). Run on the whole text before it is cut, so no fragment of a secret survives a cut."""
    pairs = [(w, p) for w, p in hidden if w]
    if not pairs:
        return text
    names = dict(sorted(pairs, key=lambda wp: len(wp[0])))            # a word given twice keeps one placeholder
    pattern = re.compile("|".join(re.escape(w) for w in sorted(names, key=len, reverse=True)))
    return pattern.sub(lambda m: names[m.group(0)], text)


def _attr(obj: Any, name: str) -> Any:
    """getattr(obj, name, None) that never raises: a property that raises counts as absent."""
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def _type_name(value: Any, hidden: Hidden = ()) -> str:
    try:
        return _hide(str(type(value).__name__), hidden)[:80]
    except Exception:
        return "object"


def _text(value: Any, limit: int, hidden: Hidden = ()) -> str:
    """str(value) with the hidden words replaced, then cut to `limit`; a placeholder when the value cannot be
    written as text at all."""
    try:
        text = str(value)
        return _hide(text, hidden)[:limit]
    except Exception:
        return f"<unreadable {_type_name(value, hidden)}>"


def _scrub(text: str, hidden: Hidden = ()) -> str:
    """Webull's or the HTTP library's words with the App Key, the App Secret and the account id replaced by their
    placeholders, and anything that looks like a URL or a query string by <url>: nothing sent to Webull is copied
    into a message. Callers cut the text (and so bound the URL search) after hiding and before this."""
    return _URL.sub("<url>", _hide(text, hidden))


def _safe(exc: BaseException, hidden: Hidden = ()) -> str:
    """A short description of an SDK failure built from its status fields, never from the request it carried.
    error_msg is Webull's or the HTTP library's text (a transport ClientException carries the request URL there,
    SDK core/client.py:328), so it is scrubbed. No field read here can raise (see _attr)."""
    parts = [_text(_attr(exc, k), 60, hidden) for k in ("http_status", "error_code") if _attr(exc, k) is not None]
    msg = _attr(exc, "error_msg")
    msg = _scrub(_text(msg, 400, hidden), hidden)[:160] if msg else ""
    return _scrub(_type_name(exc, hidden) + (f" ({', '.join(parts)})" if parts else "") + (f": {msg}" if msg else ""), hidden)


def _rebuilt(exc: WebullError, hidden: Hidden) -> WebullError:
    """A WebullError (or NotTransmitted) raised by the client, rebuilt with its text scrubbed."""
    cls = NotTransmitted if isinstance(exc, NotTransmitted) else WebullError
    return cls(_scrub(_text(exc, 400, hidden), hidden))


def _call(fn, *args, hidden: Hidden = ()) -> Any:
    """Run one SDK call. The SDK raises on some failures and returns a non-200 response on others. Every text
    taken from the failure is scrubbed of the hidden words (the venue passes its App Key, App Secret and account id)."""
    try:
        res = fn(*args)
    except WebullError as exc:
        raise _rebuilt(exc, hidden) from None
    except Exception as exc:
        raise WebullError("Webull call failed: " + _safe(exc, hidden)) from None
    return _body(res, hidden)


def _reading(method):
    """A public method that reads Webull's answer raises only WebullError: any other exception raised while reading
    (a value no JSON decoder makes, such as a 5,000-digit integer or nesting 60,000 deep) becomes a WebullError that
    names only the exception's class."""
    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except WebullError:
            raise
        except Exception as exc:
            raise WebullError(f"Webull's answer could not be read ({type(exc).__name__})") from None
    return wrapper


def _json(res: Any) -> Any:
    try:
        return res.json()
    except Exception:
        return None


def _body(res: Any, hidden: Hidden = ()) -> Any:
    """The JSON body of an SDK response, or an error that names the status but never the credentials.

    The error is built from the documented error fields (error_code, message) only, never from the raw response
    text, which is not under this module's control; both, and the status, are scrubbed of the hidden words."""
    status = _attr(res, "status_code")
    if status != 200:
        err = _json(res)
        parts = [_scrub(_text(err.get(k), 120, hidden), hidden) for k in ("error_code", "message") if isinstance(err, dict) and err.get(k) is not None]
        raise WebullError(f"Webull answered {_scrub(_text(status, 20, hidden), hidden)}" + (f": {', '.join(parts)}" if parts else ""))
    try:
        return res.json()
    except Exception:
        raise WebullError("Webull answered 200 with a body that is not JSON") from None


def _number(value: Any) -> float | None:
    """A float from Webull's string numbers; None when absent or not a finite number (never 0 by default)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):                       # OverflowError: a JSON integer beyond float range
        return None
    return out if math.isfinite(out) else None


def _not_found_code(code: Any, http_status: Any) -> bool:
    """The exact code ORDER_NOT_FOUND (no case folding, no trimming) with a 4xx HTTP status."""
    try:
        return (type(code) is str and code in ORDER_NOT_FOUND_CODES and isinstance(http_status, int)
                and not isinstance(http_status, bool) and 400 <= http_status < 500)
    except Exception:
        return False


def _trader_symbol(symbol: Any, instrument_type: Any) -> str:
    """Webull's symbol under the trader's own name: crypto BTCUSD comes back as BTC-USD (as in positions())."""
    sym = str(symbol or "").upper()
    if str(instrument_type or "").upper() == "CRYPTO" and sym.endswith("USD") and "-" not in sym:
        sym = sym[:-3] + "-USD"
    return sym


def _order_items(body: Any) -> list[dict] | None:
    """Every order item in a documented combo {orders: [...]}, a plain order, or a list of either. None (an
    unexpected shape) when the body is none of those, when an `orders` entry or a list element is not an object,
    or when a list nests a list: nothing malformed is skipped."""
    if isinstance(body, list):
        found = [_combo_items(b) for b in body]
        return None if any(f is None for f in found) else [item for f in found for item in f]
    return _combo_items(body)


def _combo_items(body: Any) -> list[dict] | None:
    if not isinstance(body, dict):
        return None
    if "orders" in body:                                                # a combo must hold at least one order object
        orders = body["orders"]
        return orders if isinstance(orders, list) and orders and all(isinstance(o, dict) for o in orders) else None
    return [body] if "status" in body else None


def _side_sign(side: Any) -> float | None:
    side = side.strip().upper() if isinstance(side, str) else None
    return 1.0 if side == "BUY" else -1.0 if side in ("SELL", "SHORT") else None


def _fee(item: dict) -> float | None:
    """commission.actual_commission plus every fees[].actual_value; None if none is given or one is unreadable."""
    values = []
    commission, fees = item.get("commission"), item.get("fees")
    if commission is not None:
        if not isinstance(commission, dict):
            return None
        if "actual_commission" in commission:
            values.append(commission["actual_commission"])
    if fees is not None:
        if not isinstance(fees, list) or not all(isinstance(f, dict) for f in fees):
            return None
        values += [f.get("actual_value") for f in fees]
    if not values:
        return None
    numbers = [_number(v) for v in values]
    if any(n is None for n in numbers):
        return None
    total = sum(numbers)
    return total if math.isfinite(total) else None


def _state(raw: Any) -> str:
    return ORDER_STATES.get(raw, "unknown_status") if isinstance(raw, str) else "unknown_status"      # exact, as documented


def _filled_units(item: dict) -> float | None:
    n = _number(item.get("filled_quantity"))
    return None if n is None or n < 0 else n + 0.0                      # a negative fill is unreadable; -0.0 becomes 0.0


class WebullVenue:
    """Orders and account reads on one Webull account."""

    def __init__(self, app_key: str, app_secret: str, environment: str = "sandbox", account_id: str | None = None,
                 client: Any | None = None, confirmation_file: str | Path = LIVE_CONFIRMATION, region: str = "us",
                 confirmed_not_found: bool = False, token_dir: str | Path | None = None) -> None:
        """`confirmed_not_found` (default False): read the two "no such order" answers (see order_status) as None.
        Turn it on only after that answer has been observed in Webull's test environment for an id that was never
        sent, and only together with a generous `settle_seconds` in the order ledger, chosen from the lag observed
        there: the detail call can show an empty list for a while after an order was placed, and with the flag on
        that reads as "never arrived" once the settle time has passed. The ledger's recheck is the backstop.

        `token_dir`: where the SDK keeps its access token when a real client is built (the SDK writes it in plain
        text). Default ~/.valedia/webull/<environment>/; created with mode 0700; a directory inside the repository
        is refused."""
        if environment not in HOSTS:
            raise WebullError(f"environment must be one of {sorted(HOSTS)}")
        if not str(app_key).strip() or not str(app_secret).strip():
            raise WebullError("a Webull App Key and App Secret are required")
        self.environment, self.host, self.name = environment, HOSTS[environment], f"webull-{environment}"
        self._confirmation_file = Path(confirmation_file)
        self._credential_words = _hidden(app_key, app_secret)       # kept only to be replaced in messages
        hidden = _hidden(app_key, app_secret, account_id)
        if client is None:
            try:
                from webull.core.client import ApiClient
                from webull.trade.trade_client import TradeClient
            except ImportError as exc:
                raise WebullError("the Webull SDK is not installed: uv pip install 'webull-openapi-python-sdk>=3.0.4'") from exc
            private_tokens = _private_token_dir(token_dir, environment)
            sdk_log = Path.cwd() / SDK_LOG_FILE
            sdk_log_existed = sdk_log.exists()
            _quiet_sdk_logging()
            try:                                             # the SDK opens a session here: this is a network call
                api = ApiClient(app_key, app_secret, region)
                api.add_endpoint(region, self.host)
                # The SDK writes its access token to <dir>/token.txt; without this it uses $WEBULL_OPENAPI_TOKEN_DIR or
                # conf/ under the working directory (SDK core/client.py:171, token_storage.py:48-86, token_manager.py:96-104).
                api.set_token_dir(str(private_tokens))
                # TradeClient adds its stdout and file loggers only when neither of these is set (SDK trade_client.py:43).
                api._stream_logger_set = api._file_logger_set = True
                client = TradeClient(api)
            except Exception as exc:
                raise WebullError(f"could not open a Webull session on {self.host}: {_safe(exc, hidden)}") from None
            finally:
                _quiet_sdk_logging()
                _remove_new_empty_sdk_log(sdk_log_existed, sdk_log)
        self._client = client
        self._account_id = account_id
        self._confirmed_not_found = confirmed_not_found is True

    def _secret_words(self) -> Hidden:
        """The App Key, the App Secret and the account id (when known): never repeated in a message."""
        return self._credential_words + _hidden(None, None, self._account_id)

    def _call(self, fn, *args) -> Any:
        return _call(fn, *args, hidden=self._secret_words())

    @classmethod
    def from_secrets(cls, environ=None, **kwargs) -> "WebullVenue":
        from valedia_trader.core.secrets import load_env, secret
        import os
        env = os.environ if environ is None else environ
        if environ is None:
            load_env()
        return cls(secret("WEBULL_APP_KEY", env), secret("WEBULL_APP_SECRET", env), environment=(env.get("WEBULL_ENV") or "sandbox").strip().lower(),
                   account_id=(env.get("WEBULL_ACCOUNT_ID") or "").strip() or None, **kwargs)

    # ---- reading (allowed in both environments)
    @_reading
    def accounts(self) -> list[dict]:
        body = self._call(self._client.account_v2.get_account_list)
        rows = body if isinstance(body, list) else body.get("accounts", body.get("data", [])) if isinstance(body, dict) else None
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise WebullError("Webull's account list has an unexpected shape")
        return list(rows)

    @property
    @_reading
    def account_id(self) -> str:
        if self._account_id is None:
            found = self.accounts()
            if len(found) != 1:
                raise WebullError(f"{len(found)} accounts are visible; set WEBULL_ACCOUNT_ID to choose one")
            if not isinstance(found[0].get("account_id"), str) or not found[0]["account_id"]:
                raise WebullError("Webull's account list names no account_id")
            self._account_id = str(found[0]["account_id"])
        return self._account_id

    @_reading
    def balance(self) -> dict:
        return self._call(self._client.account_v2.get_account_balance, self.account_id)

    @_reading
    def positions(self) -> dict[str, float]:
        """Held quantity by the trader's own symbol names (crypto as BTC-USD), from the rows whose instrument_type
        is exactly EQUITY (keyed by symbol) or CRYPTO (BTCUSD keyed as BTC-USD). Docs /apis/docs/reference/
        account-position (read 2026-11-12) list the instrument types EQUITY, OPTION, FUTURES, CRYPTO and EVENT, and
        give as their example a row with symbol AAPL, instrument_type OPTION, quantity "1": such a row is not a
        holding of AAPL. Every row that is not EQUITY or CRYPTO, including one with no, an empty or an unrecognised
        instrument_type, gives no holding and is returned by other_positions() instead; that a real row always
        carries its instrument_type is to be checked in the test environment.

        A symbol in more than one EQUITY row, or in more than one CRYPTO row, raises WebullError (an unexpected
        shape: whether Webull ever splits a holding over rows is to be checked in the test environment), so a sale
        can never be licensed by adding rows up; so does an EQUITY and a CRYPTO row under the same name here. An
        EQUITY row and, say, an OPTION row for the same symbol are not duplicates. A quantity that is unreadable,
        not finite or above MAX_HOLDING_UNITS gives that row no holding."""
        return self._flatten(self._read_positions()[0])

    @staticmethod
    def _flatten(holdings: dict[tuple[str, str], float | None]) -> dict[str, float]:
        names = [sym for _, sym in holdings]
        for sym in names:
            if names.count(sym) > 1:
                raise WebullError(f"Webull's position list has {sym} in more than one row (as EQUITY and as CRYPTO); cannot tell what is held")
        return {sym: qty for (_, sym), qty in holdings.items() if qty is not None}

    @_reading
    def other_positions(self) -> list[dict]:
        """Every position row that is not a holding in positions() (its instrument_type is not exactly EQUITY or
        CRYPTO: an OPTION, FUTURES or EVENT row, or one with no, an empty or an unrecognised type), as
        {symbol (as given), instrument_type (as given, or None), quantity (a float, or None if unreadable)}."""
        return self._read_positions()[1]

    def _read_positions(self) -> tuple[dict[tuple[str, str], float | None], list[dict]]:
        """({(EQUITY or CRYPTO, the trader's symbol): quantity or None}, [the other rows]) from one position read."""
        body = self._call(self._client.account_v2.get_account_position, self.account_id)
        rows = body if isinstance(body, list) else body.get("positions", body.get("holdings", body.get("data", []))) if isinstance(body, dict) else None
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise WebullError("Webull's position list has an unexpected shape")
        holdings: dict[tuple[str, str], float | None] = {}
        others: list[dict] = []
        for row in rows:
            kind = row.get("instrument_type")
            qty = _number(row.get("quantity", row.get("qty", row.get("position", 0))))
            if qty is not None and abs(qty) > MAX_HOLDING_UNITS:          # absurd: no holding
                qty = None
            if type(kind) is not str or kind not in HOLDING_TYPES:
                others.append({"symbol": row.get("symbol"), "instrument_type": kind, "quantity": qty})
                continue
            sym = _trader_symbol(row.get("symbol", ""), kind)
            if (kind, sym) in holdings:
                raise WebullError(f"Webull's position list has {sym} in more than one row (both {kind}); cannot tell what is held")
            holdings[(kind, sym)] = qty
        return holdings, others

    def _held(self, symbol: str) -> float:
        """The holding a sale of `symbol` may draw on: its CRYPTO row for a crypto symbol, else its EQUITY row."""
        holdings, _ = self._read_positions()
        kind, sym = ("CRYPTO" if _is_crypto(symbol) else "EQUITY"), symbol.upper()
        if ("EQUITY", sym) in holdings and ("CRYPTO", sym) in holdings:
            raise WebullError(f"Webull's position list has {sym} as both EQUITY and CRYPTO; cannot tell what is held")
        return holdings.get((kind, sym)) or 0.0

    def _reserved_by_open_sells(self, symbol: str) -> float:
        """The unfilled remainder of every open sell for `symbol` and its instrument type: total minus filled, or
        the total when the filled quantity is unreadable (or above the total). Raises WebullError, so the sale is
        not sent, when the open orders cannot be read, or when an open order for this symbol has an unreadable total
        or side (units None), or carries no recognised instrument_type (it could be this very holding)."""
        kind, sym = ("CRYPTO" if _is_crypto(symbol) else "EQUITY"), symbol.upper()
        names = {sym, sym.replace("-", "")}
        reserved = 0.0
        for o in self.open_orders():
            given = o["instrument_type"]
            if type(given) is not str or given not in ORDER_INSTRUMENT_TYPES:
                if o["symbol"] in names:
                    raise WebullError(f"an open order for {sym} carries no recognised instrument type; cannot tell what is already being sold")
                continue
            if given != kind or o["symbol"] != sym:
                continue
            if o["units"] is None:
                raise WebullError(f"an open order for {sym} has an unreadable quantity or side; cannot tell what is already being sold")
            if o["units"] >= 0:
                continue
            total, filled = abs(o["units"]), o["filled_units"]
            reserved += total - filled if filled is not None and filled <= total else total
        return reserved

    # ---- orders
    def _guard_live(self, value: float | None) -> None:
        if self.environment != "production":
            return
        confirmation = load_live_confirmation(self._confirmation_file)
        if confirmation is None:
            raise LiveTradingNotConfirmed("real-money orders are off: the owner has not written config/webull_live.json")
        if confirmation["account_id"] != self.account_id:
            raise LiveTradingNotConfirmed("the written confirmation names a different account")
        if value is None or not value > 0:
            raise LiveTradingNotConfirmed("a real-money order needs a known dollar value to check against the cap")
        if value > float(confirmation["max_order_value"]):
            raise LiveTradingNotConfirmed(f"order value {value:,.2f} is above the confirmed cap of {confirmation['max_order_value']:,.2f}")

    def submit(self, symbol: str, units: float, tag: str, price: float | None = None, limit_price: float | None = None) -> dict:
        """Place one order. `price` is the last known price, used only to value the order for the live cap.

        Everything that can refuse (choosing the account, the long-only check, the live guard, building the
        payload) runs before the single place_order call, and any failure there raises NotTransmitted: the order
        was certainly not sent. An error raised after the place_order call began is a plain WebullError of
        UNKNOWN OUTCOME: the order may or may not have reached Webull, so read order_status(tag) before retrying
        (the same tag gives the same client order id, which Webull keeps unique per account). Whatever the client
        raises once place_order has been called, a NotTransmitted included, leaves as a plain WebullError."""
        try:
            amount, _ = _quantity(units, _is_crypto(symbol))                 # a malformed quantity is refused first
            account = self.account_id
            if amount < 0:                                                # the holding must be established, or no sale
                held = self._held(symbol)
                reserved = self._reserved_by_open_sells(symbol)           # open sells already draw on it
                if abs(amount) > held - reserved + 1e-9:
                    raise NotTransmitted(f"selling {abs(amount):g} {symbol} with {held:g} held and {reserved:g} in open sells "
                                         "would open a short; this venue is long-only")
            reference = limit_price if limit_price is not None else price
            self._guard_live(abs(amount) * float(reference) if reference else None)
            order = order_payload(symbol, units, tag, limit_price)
        except NotTransmitted:                                            # raised here, or rebuilt scrubbed by _call
            raise
        except WebullError as exc:
            raise NotTransmitted("not sent: " + _scrub(_text(exc, 400, self._secret_words()), self._secret_words())) from None
        except Exception as exc:                                          # a local fault, e.g. an unreadable price
            hidden = self._secret_words()
            raise NotTransmitted(f"not sent: {_type_name(exc, hidden)}: {_scrub(_text(exc, 400, hidden), hidden)[:160]}") from None
        hidden = self._secret_words()
        try:
            res = self._client.order_v3.place_order(account, [order])   # the only order call; nothing refuses after it
        except NotTransmitted as exc:                                     # raised from inside the call: outcome unknown
            raise WebullError("outcome unknown: the order call began and then failed: " + _scrub(_text(exc, 400, hidden), hidden)) from None
        except WebullError as exc:
            raise _rebuilt(exc, hidden) from None
        except Exception as exc:
            raise WebullError("Webull call failed: " + _safe(exc, hidden)) from None
        if _attr(res, "status_code") != 200:
            try:
                _body(res, hidden)
            except WebullError:
                raise
            except Exception as exc:
                raise WebullError(f"Webull's answer could not be read ({type(exc).__name__})") from None
        # Webull answered 200: the order was placed. Reading the answer can no longer turn it into an error.
        try:
            body = res.json()
        except Exception:
            body = None
        try:
            venue_order_id = self._venue_order_id(body, order["client_order_id"])
        except Exception:
            venue_order_id = None
        try:
            response = body if isinstance(body, (dict, list)) else _text(body, 200)
        except Exception:
            response = None
        return {"status": "accepted", "client_order_id": order["client_order_id"], "venue_order_id": venue_order_id,
                "environment": self.environment, "side": order["side"], "quantity": order["quantity"], "response": response}

    @staticmethod
    def _venue_order_id(body: Any, cid: str) -> str | None:
        """`order_id` of the place-order answer (docs common-order-place); inside an order list, the item with our id."""
        if isinstance(body, dict) and body.get("order_id"):
            return str(body["order_id"])
        items = _order_items(body) or []
        mine = [o for o in items if o.get("client_order_id") == cid and o.get("order_id")]
        return str(mine[0]["order_id"]) if mine else None

    @_reading
    def cancel(self, tag: str) -> dict:
        return {"status": "cancel requested", "response": self._call(self._client.order_v3.cancel_order, self.account_id, client_order_id(tag))}

    @_reading
    def cancel_all_open(self) -> list[dict]:
        """Cancel every order open_orders() returns, one cancel_order call each by client order id. One failure does
        not stop the others: each entry carries Webull's answer or the error. Cancelling only reduces exposure, so it
        is allowed in production without the written live confirmation. If open_orders() itself fails, this raises
        and nothing is cancelled. Webull cancels only orders placed through the API (docs common-order-cancel)."""
        account = self.account_id
        out = []
        for o in self.open_orders():
            entry = {"client_order_id": o["client_order_id"], "venue_order_id": o["venue_order_id"], "symbol": o["symbol"]}
            if not o["client_order_id"]:
                out.append({**entry, "status": "error", "error": "no client order id; Webull cancels only orders placed through the API"})
                continue
            try:
                out.append({**entry, "status": "cancel requested", "response": self._call(self._client.order_v3.cancel_order, account, o["client_order_id"])})
            except WebullError as exc:
                out.append({**entry, "status": "error", "error": str(exc)})
        return out

    # ---- what happened to orders (reading: allowed in both environments)
    @_reading
    def order_status(self, tag: str) -> dict | None:
        """What Webull says about the order sent under `tag`: {state, filled_units, avg_price, fee, venue_order_id,
        raw_status}, or None only when the venue was built with confirmed_not_found=True and Webull gives one of the
        two answers read as "no such order": a 200 in the documented combo shape whose `orders` is an explicitly
        empty list and whose keys are all among client_order_id, combo_order_id, combo_type and orders (with any
        client_order_id ours), or the
        exact error code ORDER_NOT_FOUND with a 4xx status. Neither is documented for the US API (see the module notes),
        so with the default confirmed_not_found=False both raise WebullError; turn the flag on only after that answer
        has been observed in Webull's test environment for an id that was never sent. Any other failure (an
        unreachable venue, a malformed answer such as {"orders": ["junk"]} or a bare []) raises WebullError: it
        never looks like "no such order".

        `state` comes from ORDER_STATES; `filled_units` is unsigned; a number that is absent or unreadable is None,
        never 0 (filled_units is 0.0 only when Webull says 0); avg_price is None when Webull gives 0 (nothing filled);
        `fee` is the actual commission plus the actual fees.

        A combo that carries a client_order_id other than ours raises WebullError, whatever it holds. A single
        item without a client_order_id is read as ours only inside a combo that names our id or names none."""
        cid = client_order_id(tag)
        account = self.account_id
        hidden = self._secret_words()
        try:
            res = self._client.order_v3.get_order_detail(account, cid)
        except WebullError as exc:
            raise _rebuilt(exc, hidden) from None
        except Exception as exc:
            if _not_found_code(_attr(exc, "error_code"), _attr(exc, "http_status")):    # the SDK's ServerException
                return self._no_such_order("the error code ORDER_NOT_FOUND")
            raise WebullError("Webull call failed: " + _safe(exc, hidden)) from None
        if _attr(res, "status_code") != 200:
            err = _json(res)
            if isinstance(err, dict) and _not_found_code(err.get("error_code"), _attr(res, "status_code")):
                return self._no_such_order("the error code ORDER_NOT_FOUND")
        body = _body(res, hidden)
        if (isinstance(body, dict) and isinstance(body.get("orders"), list) and not body["orders"]
                and body.keys() <= EMPTY_ANSWER_KEYS and body.get("client_order_id", cid) == cid):
            return self._no_such_order("an empty order list")
        combos = body if isinstance(body, list) else [body]
        if any(isinstance(c, dict) and "orders" in c and "client_order_id" in c and c["client_order_id"] != cid for c in combos):
            raise WebullError("Webull's order detail names another client order id; cannot tell what happened to this order")
        items = _order_items(body)
        if not items:                                                     # None (malformed) or a bare empty list
            raise WebullError(f"Webull's order detail has an unexpected shape (a {type(body).__name__}); cannot tell what happened")
        mine = [o for o in items if o.get("client_order_id") == cid]
        if not mine and len(items) == 1 and not items[0].get("client_order_id"):
            mine = items
        if len(mine) != 1:
            raise WebullError(f"Webull's order detail holds {len(items)} orders and {len(mine)} carry this client order id")
        item = mine[0]
        price = _number(item.get("filled_price"))
        return {"state": _state(item.get("status")), "filled_units": _filled_units(item), "avg_price": price if price and price > 0 else None,
                "fee": _fee(item), "venue_order_id": str(item["order_id"]) if item.get("order_id") else None, "raw_status": item.get("status")}

    def _no_such_order(self, answer: str) -> None:
        if self._confirmed_not_found:
            return None
        raise WebullError(f"Webull shows no order under this client order id ({answer}); what that answer means is not yet "
                          "confirmed, so the outcome stays unknown (see confirmed_not_found)")

    @_reading
    def open_orders(self) -> list[dict]:
        """Every open order of the account, following pagination_key to the last page (docs order-open: an absent
        key marks the last page). Each is {client_order_id, venue_order_id, symbol (the trader's naming),
        instrument_type (as given, None when absent), units
        (signed: sells and shorts negative; None if unreadable or the side is not BUY, SELL or SHORT), state,
        filled_units, raw_status}. A malformed page (an entry that is not an object, a bare empty list) or a
        pagination key that is not a non-empty string raises WebullError."""
        account = self.account_id
        out: list[dict] = []
        key, seen, pages = None, set(), 0
        while True:
            body = self._call(self._client.order_v3.list_order_open, account) if key is None else self._call(self._client.order_v3.list_order_open, account, key)
            pages += 1
            rows = body.get("data") if isinstance(body, dict) else body      # a body without a `data` list is not "none open"
            items = _order_items(rows) if isinstance(rows, list) and (rows or isinstance(body, dict)) else None
            if items is None:                                             # a bare top-level [] is malformed too
                raise WebullError("Webull's open-order list has an unexpected shape")
            for item in items:
                qty, sign = _number(item.get("total_quantity")), _side_sign(item.get("side"))
                out.append({"client_order_id": item.get("client_order_id"), "venue_order_id": str(item["order_id"]) if item.get("order_id") else None,
                            "symbol": _trader_symbol(item.get("symbol"), item.get("instrument_type")), "instrument_type": item.get("instrument_type"),
                            "units": None if qty is None or sign is None else sign * abs(qty), "state": _state(item.get("status")),
                            "filled_units": _filled_units(item), "raw_status": item.get("status")})
            key = body.get("pagination_key") if isinstance(body, dict) else None
            if key is None:
                return out
            if not isinstance(key, str) or not key:
                raise WebullError(f"Webull's pagination key is not a non-empty string (a {type(key).__name__})")
            if key in seen:
                raise WebullError("Webull repeated a pagination key; stopping rather than looping")
            if pages >= MAX_OPEN_ORDER_PAGES:
                raise WebullError(f"Webull's open-order list runs past {MAX_OPEN_ORDER_PAGES} pages; stopping")
            seen.add(key)

    @_reading
    def cash(self) -> dict:
        """{cash, settled_cash, buying_power, currency} from the balance call (docs account-balance). `cash` is
        total_cash_balance, else the currency entry's cash_balance; settled_cash and buying_power come from the
        account_currency_assets entry in total_asset_currency, and are None when there is no entry in that currency
        (another currency's figures are never labelled with it). When total_asset_currency is absent or empty, the
        totals carry no currency, so total_cash_balance is not used: everything comes from the single entry, `cash`
        from its cash_balance and `currency` from its own currency (all None without one such entry). A field that is absent or unreadable is
        None; an account_currency_assets that is not a list of objects, or that holds more than one entry in the
        wanted currency, raises WebullError."""
        body = self.balance()
        if not isinstance(body, dict):
            raise WebullError(f"Webull's balance answer is a {type(body).__name__}, not an object")
        assets = body.get("account_currency_assets")
        assets = [] if assets is None else assets
        if not isinstance(assets, list) or not all(isinstance(a, dict) for a in assets):
            raise WebullError("Webull's balance has an unexpected shape: account_currency_assets is not a list of objects")
        currency = body.get("total_asset_currency")
        if currency is not None and not isinstance(currency, str):
            raise WebullError("Webull's balance has an unexpected shape: total_asset_currency is not a string")
        if currency:                                                      # Webull attached this currency to the totals
            matches = [a for a in assets if str(a.get("currency", "")).upper() == currency.upper()]
            if len(matches) > 1:
                raise WebullError(f"Webull's balance has more than one account_currency_assets entry in {currency}; cannot tell which holds the cash")
            entry = matches[0] if matches else {}
            cash = _number(body.get("total_cash_balance"))
            cash = cash if cash is not None else _number(entry.get("cash_balance"))
        else:                                                             # no currency on the totals: only the single entry
            entry = assets[0] if len(assets) == 1 else {}
            currency = entry.get("currency") if isinstance(entry.get("currency"), str) and entry.get("currency") else None
            if currency is None:
                return {"cash": None, "settled_cash": None, "buying_power": None, "currency": None}
            cash = _number(entry.get("cash_balance"))
        return {"cash": cash, "settled_cash": _number(entry.get("settled_cash")), "buying_power": _number(entry.get("buying_power")),
                "currency": currency}

    # ---- a read-only readiness report
    @_reading
    def check(self) -> dict:
        accounts = self.accounts()
        report = {"environment": self.environment, "host": self.host,
                  "accounts": [{"account_id_ending": str(a.get("account_id", ""))[-4:], "type": a.get("account_type")} for a in accounts]}
        try:
            bal = self.balance()
            report["balance_fields"] = sorted(bal)[:20] if isinstance(bal, dict) else type(bal).__name__
            holdings, others = self._read_positions()
            report["positions"] = len(self._flatten(holdings))
            report["other_positions"] = len(others)
        except WebullError as exc:
            report["balance"] = str(exc)
        try:
            report["open_orders"] = len(self.open_orders())
        except WebullError as exc:
            report["open_orders_error"] = str(exc)
        confirmation = load_live_confirmation(self._confirmation_file)
        report["real_money_orders"] = ("off: test environment" if self.environment != "production" else
                                       "off: no written confirmation" if confirmation is None else
                                       f"on for the account ending {confirmation['account_id'][-4:]}, up to {confirmation['max_order_value']:,.0f} per order")
        return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m valedia_trader.venues.webull", description="Webull connection check (read-only).")
    ap.add_argument("cmd", choices=["check"])
    args = ap.parse_args(argv)
    try:
        venue = WebullVenue.from_secrets()
        if args.cmd == "check":
            print(json.dumps(venue.check(), indent=2, default=str))
    except Exception as exc:                                     # the message is safe: nothing here formats a credential
        print(f"not ready: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
