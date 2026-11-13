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
the HTTP library's text for a transport failure), but only what is known to be harmless: every text taken from
Webull or the SDK (an exception's fields, its message and its class name, an error body, anything in an answer),
on every path including the constructor, passes through one gate (_gate) that keeps a token only if it has a
harmless shape (a plain word, a short number, an upper-case code, punctuation) and holds no run of 6 characters
of the App Key, the App Secret or the account id in any of their disguised forms (case, URL encoding, base64,
hex); everything else becomes <withheld>. An error_code is shown only in the code shape, else <code withheld>.
A separate last check (_last_check) then replaces any whole text still holding such a run by "text withheld".
No method hands back Webull's answer itself: only numbers, the module's own words, and symbols, ids, statuses
and codes that passed their own shape rules. Reading an exception's fields can never raise: a field that cannot
be read counts as absent.
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
* No such order (as read from the docs on 2026-11-12; what the test environment answers is at the end of these
  notes, and order_status reads those answers too). The order-detail page documents only 200, 401, 417 and 500 answers, and the
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
  1e9, and already on the step; the caller rounds down to the step itself. The text sent is the shortest decimal
  that reads back to the same number (repr), and a quantity whose shortest decimal has more decimals than the step
  is refused. Steps: crypto 8 decimals (docs common-order-place, field quantity); equities 5 decimals (NOT
  CONFIRMED: Webull documents fractional quantities without a step; check in the test environment).
* Limit prices: never rounded; a price that is not a finite number above zero, above 1e9, or not already on the
  tick, is refused before any call (NotTransmitted). Ticks: equities 2 decimals at every price (below 1.00 too:
  observed in the test environment, see the end of these notes); crypto 8 decimals (an assumption: the docs give 8
  decimals for crypto quantities only). The text sent is the shortest decimal that reads back to the price.
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

* Pacing: Webull limits each kind of call per app key and answers 429 past a limit (developer.webull.com/apis/docs/
  rate-limits, read 2026-11-13; the table is RATE_LIMITS). Every SDK call waits for the venue's sliding-window pacer,
  which uses 80% of each limit and of an overall limit (60 a minute live, from the owner's console; 30 on the paper
  side). A 429 on a read is tried again after 2, 4 and 8 seconds, then raises RateLimitHit; place, replace and cancel
  are never retried here.

Observed in the test environment, 2026-11-13 (the owner's paper key; docs/WEBULL.md, the two "Observed in Webull's
test environment" parts). None of this is documented by Webull, and the live side has not been observed: it may
behave differently.

* Accounts: a paper key sees five accounts. Each row of the account list carries `account_class` (seen
  INDIVIDUAL_MARGIN, INDIVIDUAL_CASH, FUTURES, EVENTS_CASH, CRYPTO) and `account_label` ("Individual Cash", ...)
  beside `account_type` (only CASH or MARGIN). The API's `account_id` is a long internal id the owner cannot see
  or type, so the venue chooses its account by class (WEBULL_ACCOUNT_CLASS; see the account_id property). Crypto
  has its own account: stocks and funds trade in INDIVIDUAL_CASH or INDIVIDUAL_MARGIN, crypto only in CRYPTO,
  event contracts in EVENTS_CASH (see "Event contracts" below); an order that does not fit the account's class is not sent.
* A refusal at placement is HTTP 417 with a code of Webull's own (outside hours
  OPENAPI_CAN_NOT_TRADING_FOR_FIXGW_NOT_READY_NIGHT; fractional shares overnight OPENAPI_FRACT_ONLY_DAY_ORDER; a price
  off the step OPENAPI_STOCK_ORDER_PRICE_PRECISION_EXCEED), and the order then does not exist. The same client order
  id sent twice is refused with OPENAPI_TRADE_PLACE_ORDER_REPEAT: that order already exists. submit therefore
  reads the order's detail once after a 4xx refusal (see submit and OrderRejected).
* No such order: on the cash and events accounts HTTP 417, code OPENAPI_PARAM_ERR, message "Parameter error, Order
  not present."; on the crypto account HTTP 200 with {"orders": []}. ORDER_NOT_FOUND was never seen. An accepted
  order was visible in its detail on the very next read in every case seen (four orders, reads from 0.2 s after
  placing): no "not present" gap was observed, on the paper side only.
* Cancel answers {client_order_id, order_id}; a cancel while the order is still PENDING is refused with
  OPENAPI_ORDER_CAN_NOT_BE_CANCEL_FOR_PENDING_SUBMIT (it works a little later); cancelling again is refused with
  OPENAPI_ORDER_CAN_NOT_BE_CANCEL; cancelling an order that never existed answers "Order not present".
* Sessions: an ordinary order outside regular hours is refused, not queued. The overnight session takes limit
  orders marked support_trading_session NIGHT.
* Sub-dollar limits: a limit of 0.5001 was accepted and stored as 0.50, and 0.50001 was refused, so equity limits
  are sent with at most 2 decimals at every price (see _limit_text).
* Crypto time in force (Webull's place-order page, not observed): a crypto MARKET order supports only IOC, a crypto
  LIMIT order DAY or GTC; this adapter sends IOC and GTC.

Event contracts (added 2026-11-13, T19; docs/specs/2026-11-13-event-orders-design.md, part 1). Observed in the test
environment's Events Cash account the same day, by raw SDK calls (none of it documented or seen on the live side):

* An order is the stock order's shape with instrument_type EVENT, the Kalshi ticker as symbol, event_outcome "yes"
  (a "no" order was not observed), order_type LIMIT, entrust_type QTY, time in force DAY, no support_trading_session,
  sent through the same order_v3.place_order. It was accepted; a purchase filled 3 to 10 seconds later at Kalshi's ask, a
  sale at Kalshi's bid. Detail and open-order rows carry symbol (the ticker), side, status (SUBMITTED, FILLED,
  CANCELLED seen), event_outcome, filled_quantity, filled_price and limit_price (four decimals), commission {} and
  fees: ONE entry {type EXCHANGE_FEE, actual_value, receivable_value} from the moment the order is accepted, before any
  fill, and none after a cancel. A position row is {symbol (the ticker), instrument_type EVENT, event_outcome,
  quantity, cost, ...}. The balance answer has buying_power per currency and no cash line. Not observed: a no-outcome
  order, a partial fill, more than one contract, settlement, a refusal code for an event order.
* A venue built with the account class EVENTS_CASH (and only such a venue) trades event contracts, and only them. It
  names an event contract `TICKER:yes` or `TICKER:no` (parse_event_name: the ticker starts with an ASCII capital
  letter, holds only A-Z, 0-9, dot and hyphen, at most 80 characters; the outcome exactly yes or no), in positions,
  open orders, order status and submit. Its name is webull-<environment>-events, so the order ledger, the state and
  the lock files kept per venue name are its own; every other venue keeps the name webull-<environment>.
* submit there sends only whole contracts, an explicit limit with at most two decimals from 0.01 to 0.99 (never
  rounded), time in force DAY; the session is ignored. A purchase of `T:no` is a BUY with event_outcome no, a sale of
  `T:no` a SELL with event_outcome no of no contracts held: the outcome is copied from the name and from nothing
  else. A sale is long-only per ticker and outcome, on holdings read at most 5 seconds before the order call, as for
  stocks. Event orders go only to the test environment: environment "sandbox" by a static and an ordinary lookup and a
  host with "sandbox" as a whole label, checked before anything is read; the production environment refuses them in
  this adapter whatever config/webull_live.json says.
* A venue of any other class refuses every name holding a colon (every event name) before anything is read or sent;
  apart from that, its behaviour is that of rev 9 for the same inputs.
* order_status there checks the row read back against the name sent (given as `expected`, or remembered by this
  venue object from its own submit): another ticker or outcome is an error, never a fill. Its fee is the one fee
  entry's actual_value (plus commission.actual_commission when given), and only once something has filled: the entry
  present on an accepted, unfilled order is not a fee paid.
* cash() there gives the buying power as `cash` and settled_cash None (not given).
* Settlement (a contract held to the result leaving the account without an order) is not handled here.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import functools
import hashlib
import importlib
import inspect
import json
import logging
import math
import numbers
import os
import re
import sys
import time
import urllib.parse
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any

SANDBOX_HOST = "api.sandbox.webull.com"
PRODUCTION_HOST = "api.webull.com"
HOSTS = {"sandbox": SANDBOX_HOST, "production": PRODUCTION_HOST}
REPO = Path(__file__).resolve().parents[2]
LIVE_CONFIRMATION = REPO / "config" / "webull_live.json"

__all__ = ["WebullVenue", "WebullError", "NotTransmitted", "LiveTradingNotConfirmed", "OrderRejected", "RateLimitHit", "order_payload",
           "client_order_id", "load_live_confirmation", "ORDER_STATES", "EVENTS_CLASS", "parse_event_name", "event_order_payload"]

# Webull's order status -> the trader's order state. Source: developer.webull.com/apis/docs/reference/order-detail
# (read 2026-11-12), field orders[].status; "PARTIAL FILLED" is the SDK's spelling (webull/trade/common/order_status.py:25).
# A status not in this table is `unknown_status`, never filled or cancelled.
ORDER_STATES = {"PENDING": "open", "SUBMITTED": "open", "PARTIAL_FILLED": "partly_filled", "PARTIAL FILLED": "partly_filled",
                "FILLED": "filled", "CANCELLED": "cancelled", "FAILED": "rejected"}
# The error code read as "no such order" (with an empty `orders` list), and only with confirmed_not_found=True.
# Not confirmed for the US API: see the module notes.
ORDER_NOT_FOUND_CODES = frozenset({"ORDER_NOT_FOUND"})
# Observed in the test environment, 2026-11-13: an id never sent answers 417, this code, "Parameter error, Order not present."
NOT_PRESENT_CODE, NOT_PRESENT_TEXT = "OPENAPI_PARAM_ERR", "order not present"
CANCEL_ANSWERS = {"OPENAPI_ORDER_CAN_NOT_BE_CANCEL_FOR_PENDING_SUBMIT": "not yet",       # still being submitted: try again
                  "OPENAPI_ORDER_CAN_NOT_BE_CANCEL": "already finished"}
# Which account classes an order may trade in (observed in the test environment, 2026-11-13).
ACCOUNT_CLASSES_FOR = {"EQUITY": frozenset({"INDIVIDUAL_CASH", "INDIVIDUAL_MARGIN"}), "CRYPTO": frozenset({"CRYPTO"})}
TRADING_SESSIONS = ("CORE", "ALL", "NIGHT")
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


class RateLimitHit(WebullError):
    """A read answered 429 (too many requests) on every try (see WebullVenue._paced)."""


class OrderRejected(WebullError):
    """Webull answered the order call and refused it, and a read of the order's detail then showed that no order
    exists under its client order id. Not a NotTransmitted (the order did reach Webull), but its outcome is known:
    nothing was placed. `code` is Webull's error code in the code shape, else None."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


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
    text = _shortest_text(abs(amount), places)
    if text is None:
        raise NotTransmitted(f"the quantity {abs(amount)!r} is not on the step of {places} decimal places and would have to be "
                             "rounded to be sent; round it down to the step yourself")
    return amount, text


def _shortest_text(x: float, places: int) -> str | None:
    """The shortest decimal that reads back to x (repr), written without an exponent and without trailing zeros, or
    None when it has more than `places` decimals. Never a longer decimal that merely reads back to the same float
    (99107813.158 written to 8 places is 99107813.15800001)."""
    d = Decimal(repr(float(x)))
    if not d.is_finite() or d.as_tuple().exponent < -places:
        return None
    text = f"{d:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _session(session: Any, crypto: bool, limit_price: Any) -> str:
    """The trading session sent as support_trading_session, or NotTransmitted: CORE (regular hours, the default), ALL
    or NIGHT. Outside regular hours an ordinary order is refused, not queued (observed in the test environment,
    2026-11-13). The overnight session takes limit orders only. Crypto trades around the clock and takes none."""
    if type(session) is not str or session not in TRADING_SESSIONS:
        raise NotTransmitted(f"the session must be one of {', '.join(TRADING_SESSIONS)}")
    if crypto and session != "CORE":
        raise NotTransmitted("crypto takes no trading session; leave the session at CORE")
    if session == "NIGHT" and limit_price is None:
        raise NotTransmitted("the overnight session (NIGHT) takes limit orders only")
    return session


def order_payload(symbol: str, units: float, tag: str, limit_price: float | None = None, session: str = "CORE") -> dict:
    """One order in Webull's format. `units` is signed: positive buys, negative sells what is held. Neither the
    quantity nor the limit price is ever rounded (see _quantity and _limit_text): what is sent is what was asked.
    Time in force: DAY for equities; for crypto IOC on a MARKET order and GTC on a LIMIT order (Webull's
    place-order page: a crypto market order supports only IOC). `session` see _session."""
    crypto = _is_crypto(symbol)
    amount, quantity = _quantity(units, crypto)
    session = _session(session, crypto, limit_price)
    tif = ("IOC" if limit_price is None else "GTC") if crypto else "DAY"
    order = {"combo_type": "NORMAL", "client_order_id": client_order_id(tag), "market": "US",
             "symbol": symbol.upper().replace("-", "") if crypto else symbol.upper(),
             "instrument_type": "CRYPTO" if crypto else "EQUITY",
             "order_type": "LIMIT" if limit_price is not None else "MARKET",
             "quantity": quantity, "side": "BUY" if amount > 0 else "SELL", "time_in_force": tif, "entrust_type": "QTY"}
    if limit_price is not None:
        order["limit_price"] = _limit_text(limit_price, crypto)
    if not crypto:
        order["support_trading_session"] = session
    return order


def _limit_text(limit_price: Any, crypto: bool) -> str:
    """The limit price exactly as given, or NotTransmitted. It is never rounded: rounding a limit changes the worst
    price the trader accepts, so the caller must choose the tick. Ticks: equities at most 2 decimals at every price.
    Below 1.00 the US sub-penny rule would allow 4, but in the test environment (2026-11-13) a limit of 0.5001 was
    accepted and stored by Webull as 0.50 and 0.50001 was refused: Webull rounds there, so "sent exactly or not at
    all" needs 2 decimals below 1.00 too, until more is known. Crypto at most 8 decimals (an ASSUMPTION: the docs
    give 8 decimals for crypto quantities only). The text is the shortest decimal reading back to the price, with
    at least 2 decimals."""
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
    places = 8 if crypto else 2
    text = _shortest_text(price, places)
    if text is None:
        raise NotTransmitted(f"the limit price {price!r} is not on the tick of {places} decimal places and would have to be "
                             "rounded to be sent; choose the tick yourself, because rounding a limit changes the worst price accepted")
    whole, _, frac = text.partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"


# ---- event contracts (T19; see the module notes, "Event contracts")

EVENTS_CLASS = "EVENTS_CASH"                      # the only account class that trades event contracts
EVENT_INSTRUMENT = "EVENT"
EVENT_OUTCOMES = ("yes", "no")
MAX_EVENT_CONTRACTS = 1_000_000                   # a sanity ceiling, far above any cap the trader sets
EVENT_LIMIT_LOW, EVENT_LIMIT_HIGH = Decimal("0.01"), Decimal("0.99")
TEST_ENVIRONMENT = "sandbox"
_EVENT_TICKER = re.compile(r"[A-Z][A-Z0-9.\-]{0,79}")
_FEE_TYPE = re.compile(r"[A-Z_]{1,48}")
_HOST_TEXT = re.compile(r"[A-Za-z0-9.\-]{1,253}")


def parse_event_name(name: Any) -> tuple[str, str] | None:
    """(ticker, outcome) for an event contract's name, `TICKER:yes` or `TICKER:no`, else None. The name must be a plain
    str (not a subclass); the ticker starts with an ASCII capital letter, holds only A-Z, 0-9, dot and hyphen, and has
    at most 80 characters; the outcome is exactly yes or no."""
    if type(name) is not str:
        return None
    ticker, colon, outcome = str.partition(name, ":")
    if not colon or outcome not in EVENT_OUTCOMES or _EVENT_TICKER.fullmatch(ticker) is None:
        return None
    return ticker, ("yes" if outcome == "yes" else "no")


def _event_contracts(units: Any) -> tuple[int, str]:
    """(signed whole contracts, the text sent) or NotTransmitted. `units` must be an int or a float (not a bool),
    finite, non-zero, exactly a whole number and at most MAX_EVENT_CONTRACTS in size. Never rounded."""
    if isinstance(units, bool) or not isinstance(units, (int, float)):
        raise NotTransmitted(f"event contracts must be a whole number given as an int or a float, not a {type(units).__name__}")
    try:
        amount = float(units)
    except (TypeError, ValueError, OverflowError):
        raise NotTransmitted("the number of contracts cannot be read as a number") from None
    if not math.isfinite(amount) or amount == 0:
        raise NotTransmitted("an event order needs a finite, non-zero number of contracts")
    if abs(amount) > MAX_EVENT_CONTRACTS:
        raise NotTransmitted(f"the number of contracts is above the sanity ceiling of {MAX_EVENT_CONTRACTS:,}")
    if not amount.is_integer() or not units == int(amount):
        raise NotTransmitted("event orders are for whole contracts only; the number is not rounded")
    n = int(amount)
    return n, str(abs(n))


def _event_limit_text(limit_price: Any) -> str:
    """The limit price as sent, always with two decimals ("0.72"), or NotTransmitted: an event order is always a limit;
    the price must be an int or a float (not a bool), finite, written with at most two decimals (its shortest decimal,
    never rounded), from 0.01 to 0.99."""
    if limit_price is None:
        raise NotTransmitted("an event order is always a limit order: give a limit price from 0.01 to 0.99")
    if isinstance(limit_price, bool) or not isinstance(limit_price, (int, float)):
        raise NotTransmitted(f"a limit price must be an int or a float, not a {type(limit_price).__name__}")
    try:
        price = float(limit_price)
    except (TypeError, ValueError, OverflowError):
        raise NotTransmitted("the limit price cannot be read as a number") from None
    text = _shortest_text(price, 2) if math.isfinite(price) else None
    if text is None:
        raise NotTransmitted("an event limit price must be a finite number with at most two decimals; it is never rounded")
    d = Decimal(text)
    if not EVENT_LIMIT_LOW <= d <= EVENT_LIMIT_HIGH:
        raise NotTransmitted("an event limit price must be from 0.01 to 0.99")
    return f"{d:.2f}"


def event_order_payload(name: Any, units: Any, tag: str, limit_price: Any) -> dict:
    """One event-contract order in the shape accepted in Webull's test environment (2026-11-13), or NotTransmitted.
    `name` is `TICKER:yes` or `TICKER:no` (parse_event_name); `units` is signed whole contracts: positive buys that
    outcome, negative sells that outcome held. The outcome sent is the name's own, whatever the side: buying `T:no` is
    a BUY of no, never a SELL of yes. Always a LIMIT order (see _event_limit_text), quantity in whole contracts
    (entrust_type QTY), time in force DAY, no trading session."""
    parts = parse_event_name(name)
    if parts is None:
        raise NotTransmitted("an event order needs the name TICKER:yes or TICKER:no (the ticker in capitals, digits, dots and "
                             "hyphens, at most 80 characters)")
    ticker, outcome = parts
    signed, quantity = _event_contracts(units)
    limit = _event_limit_text(limit_price)
    return {"combo_type": "NORMAL", "client_order_id": client_order_id(tag), "market": "US", "symbol": ticker,
            "instrument_type": EVENT_INSTRUMENT, "order_type": "LIMIT", "quantity": quantity, "side": "BUY" if signed > 0 else "SELL",
            "time_in_force": "DAY", "entrust_type": "QTY", "limit_price": limit, "event_outcome": outcome}


def _plain_sandbox_host(host: Any) -> bool:
    """A plain str of letters, digits, dots and hyphens with `sandbox` as one of its dot-separated labels (the order
    step's whole-label rule, execution_and_risk/order_step.py _sandbox_host)."""
    return type(host) is str and _HOST_TEXT.fullmatch(host) is not None and TEST_ENVIRONMENT in str.split(host, ".")


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
    NullHandler) is removed and closed. Run before and after the client is built: building it re-dirties them.
    The HTTP library's request log is quieted too (_quiet_http_logging)."""
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
    _quiet_http_logging()


HTTP_LOGGERS = ("urllib3.connectionpool",)


def _quiet_http_logging() -> None:
    """The HTTP library logs each request's path at DEBUG, and the SDK puts the account id in the query string:
    urllib3.connectionpool is set to WARNING unless a higher level is already set there."""
    for name in HTTP_LOGGERS:
        log = logging.getLogger(name)
        if log.level < logging.WARNING:
            log.setLevel(logging.WARNING)


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


# ---- the gate: only text of a harmless shape leaves this module
#
# Every text that comes from Webull or the SDK (an exception's fields, its message and its class name, an error body,
# anything in an answer) passes through _gate before it reaches a message, a return value or a log. _gate keeps a
# token only if it has a harmless shape and holds no run of a secret; everything else becomes WITHHELD. Strings in
# answers that are handed back are judged by their own stricter rules (_symbol, _plain_id, _status_word, ...). Then
# _last_check, a separate piece of code, replaces any whole text still holding a run of a secret by TEXT_WITHHELD.

WITHHELD = "<withheld>"
CODE_WITHHELD = "<code withheld>"
TEXT_WITHHELD = "text withheld"
OUTCOME_UNKNOWN = ("outcome unknown: the order may or may not have reached Webull; read the order's status (order_status) "
                   "before retrying anything. ")
PLACEHOLDERS = frozenset({WITHHELD, CODE_WITHHELD})
SECRET_RUN = 6                                                          # a run of this many characters of a secret
MAX_GATE_INPUT = 4000                                                   # longer text is cut before it is judged

_WORD = re.compile(r"[A-Za-z]+(?:['-][A-Za-z]+)*")                     # letters, with an inner apostrophe or hyphen
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")
_CODE = re.compile(r"[A-Z_]{1,48}")
_ERROR_CODE = re.compile(r"[A-Z_]{1,64}")    # an error_code: observed ones run to 50 characters
_LEAD = "([{\"'$#"
_TRAIL = ")]}\"'.,;:!?%"
_PUNCT = re.compile(r"""[-.,;:!?()\[\]{}"'&/+=*#$%@]{1,8}""")
_TOKENS = re.compile(r"<code withheld>|\S+")


class _Secrets:
    """The gate's view of the App Key, the App Secret and the account ids, in every form a disguise could take: as
    given, trimmed, lower and upper case, URL-encoded, base64 (standard and URL-safe, with and without padding, and
    for a secret sitting at each of the three byte offsets inside a longer base64 text, every character fixed by
    the secret alone) and hex. Held lower-cased as the set of their runs of SECRET_RUN characters (a form shorter
    than that is held whole). `words` keeps the plain values, for the last check, which builds its own search."""

    def __init__(self, words: tuple[str, ...]):
        self.words = words
        runs: set[str] = set()
        whole: set[str] = set()
        for form in _forms(words):
            f = form.lower()
            if len(f) < SECRET_RUN:
                whole.add(f)
            else:
                runs.update(f[i:i + SECRET_RUN] for i in range(len(f) - SECRET_RUN + 1))
        self.runs, self.whole = frozenset(runs), frozenset(whole)

    def found(self, text: str) -> bool:
        """Whether lower-cased `text` holds a run (or a short secret whole)."""
        return (any(text[i:i + SECRET_RUN] in self.runs for i in range(len(text) - SECRET_RUN + 1))
                or any(w in text for w in self.whole))


def _forms(words: tuple[str, ...]) -> set[str]:
    out: set[str] = set()
    for word in words:
        for w in {word, word.strip()}:
            if not w:
                continue
            raw = w.encode("utf-8", "surrogatepass")
            out.update({w, w.lower(), w.upper(), urllib.parse.quote(w, safe=""), urllib.parse.quote_plus(w), raw.hex()})
            for start in range(3):
                for enc in (base64.b64encode(raw[start:]).decode(), base64.urlsafe_b64encode(raw[start:]).decode()):
                    out.update({enc, enc.rstrip("=")})
            for offset in range(3):                                   # the secret at byte offset 0, 1 or 2 of a group
                enc = base64.b64encode(b"\0" * offset + raw).decode().rstrip("=")
                lead = (0, 2, 3)[offset]                                  # characters that also hold the bytes before
                tail = 1 if (offset + len(raw)) % 3 else 0                # the last one also holds the byte after
                core = enc[lead:len(enc) - tail]
                out.update({core, core.replace("+", "-").replace("/", "_")})
    return {f for f in out if f}


@functools.lru_cache(maxsize=256)
def _secrets_cached(words: tuple[str, ...]) -> _Secrets:
    return _Secrets(words)


def _secrets_of(*values: Any) -> _Secrets:
    """The secrets object for these values (None and empty values are left out)."""
    words = []
    for value in values:
        if value is None:
            continue
        try:
            word = value if type(value) is str else str(value)
            word = str.__getitem__(word, slice(None))                 # a plain str whatever str() gave back
        except Exception:
            continue
        if word.strip():
            words.append(word)
    return _secrets_cached(tuple(words))


NO_SECRETS = _secrets_of()


def _plain(value: Any, limit: int = MAX_GATE_INPUT) -> str:
    """str(value) as a plain str cut to `limit`, "" for None, WITHHELD when it cannot be written as text at all."""
    if value is None:
        return ""
    try:
        text = value if type(value) is str else str(value)
        return str.__getitem__(text, slice(0, limit))
    except Exception:
        return WITHHELD


def _unquoted(text: str) -> list[str]:
    """text, then URL-decoded until it stops changing (at most 3 times)."""
    out = [text]
    for _ in range(3):
        try:
            nxt = urllib.parse.unquote(out[-1], errors="replace")
        except Exception:
            break
        if nxt == out[-1]:
            break
        out.append(nxt)
    return out


def _harmless_core(core: str) -> bool:
    if core in PLACEHOLDERS:
        return True
    if _CODE.fullmatch(core):
        return True
    if _NUMBER.fullmatch(core):
        return sum(c.isdigit() for c in core) <= 12
    if len(core) <= 24 and _WORD.fullmatch(core):
        parts = re.split(r"['-]", core)
        if not all(p.islower() or p.isupper() or (p[0].isupper() and p[1:].islower()) for p in parts):
            return False                                              # inner capitals: looks like base64
        letters = core.replace("'", "").replace("-", "")
        return not (len(letters) >= 8 and all(c in "abcdefABCDEF" for c in letters))   # looks like hex
    return False


def _harmless(token: str) -> bool:
    """A token (no whitespace) of a harmless shape: a word of up to 24 letters, a number of up to 12 digits, an upper
    case code of letters and underscores up to 48, a module placeholder, each with ordinary punctuation around it,
    or punctuation alone. Anything else (letters mixed with digits, too long, percent-encoded, base64- or hex-like,
    a URL, a path, a character outside printable ASCII) is not."""
    if not token or not all(" " < c <= "~" for c in token):
        return token in PLACEHOLDERS
    if token in PLACEHOLDERS or _PUNCT.fullmatch(token):
        return True
    core = token.lstrip(_LEAD).rstrip(_TRAIL)
    return bool(core) and _harmless_core(core)


def _gate(value: Any, secrets: _Secrets, limit: int = 160) -> str:
    """THE GATE. Text from Webull or the SDK with every token that is not of a harmless shape, or that holds (also
    across neighbouring tokens, also after URL-decoding) a run of a secret, replaced by WITHHELD; whitespace becomes
    single spaces and neighbouring WITHHELD tokens one. Cut to `limit` after judging. Never raises."""
    try:
        tokens = _TOKENS.findall(_plain(value))
        marked = [any(secrets.found(v.lower()) for v in _unquoted(tok)) for tok in tokens]
        joined, owner = "", []
        for i, tok in enumerate(tokens):                              # runs across neighbouring tokens
            joined += tok.lower()
            owner += [i] * len(tok)
        for i in range(len(joined) - SECRET_RUN + 1):
            if joined[i:i + SECRET_RUN] in secrets.runs:
                for j in set(owner[i:i + SECRET_RUN]):
                    marked[j] = True
        out: list[str] = []
        for tok, bad in zip(tokens, marked):
            kept = tok if not bad and _harmless(tok) else WITHHELD
            if not (kept == WITHHELD and out and out[-1] == WITHHELD):
                out.append(kept)
        return " ".join(out)[:limit]
    except Exception:
        return WITHHELD


def _last_check(text: Any, words: Any) -> str:
    """THE LAST CHECK, written apart from the gate on purpose: it never uses _forms, _Secrets.runs or anything the
    gate's search is built from. From the plain secret values alone (`words`, or a _Secrets' .words; a venue
    always passes its own complete set, see WebullVenue._final) it builds its own forms (_final_forms) and one
    case-blind regular expression, and looks at the whole text, with and without its whitespace, and URL-decoded
    up to three times. If any run of SECRET_RUN characters of a secret is still there, the whole text becomes
    TEXT_WITHHELD. Never raises."""
    try:
        whole = str.__getitem__(text, slice(None)) if isinstance(text, str) else _plain(text, 10 ** 6)
        values = words.words if isinstance(words, _Secrets) else tuple(words or ())
        pattern = _final_pattern(tuple(sorted({v for v in values if isinstance(v, str) and v.strip()})))
        if pattern is None:
            return whole
        variants = [whole, "".join(whole.split())]
        for v in list(variants):
            for _ in range(3):
                v = urllib.parse.unquote(v, errors="replace")
                variants.append(v)
        return TEXT_WITHHELD if any(pattern.search(v) for v in variants) else whole
    except Exception:
        return TEXT_WITHHELD


def _final_forms(value: str) -> list[str]:
    """The last check's own forms of one secret, built with binascii and a hand-written percent-encoding (not with
    the gate's _forms): as given and trimmed (case is ignored by the search), percent-encoded (spaces also as +),
    hex, and base64 in both alphabets: whole with and without padding, and for each byte offset 0, 1, 2 inside a
    longer text the characters fixed by the secret alone."""
    found: list[str] = []
    for v in (value, value.strip()):
        data = v.encode("utf-8", "surrogatepass")
        percent = "".join(chr(b) if chr(b).isascii() and (chr(b).isalnum() or chr(b) in "-._~") else "%%%02X" % b for b in data)
        found += [v, percent, percent.replace("%20", "+"), binascii.hexlify(data).decode()]
        for shift in range(3):
            coded = binascii.b2a_base64(bytes(shift) + data, newline=False).decode()
            found += [coded] if shift == 0 else []
            bare = coded.rstrip("=")
            cut = bare[(0, 2, 3)[shift]:]
            if (shift + len(data)) % 3:
                cut = cut[:-1]
            found += [bare if shift == 0 else cut, cut]
    out = []
    for f in found:
        out += [f, f.translate(str.maketrans("+/", "-_"))]
    return [f for f in out if f]


@functools.lru_cache(maxsize=256)
def _final_pattern(values: tuple[str, ...]):
    needles: set[str] = set()
    for value in values:
        for form in _final_forms(value):
            if len(form) <= SECRET_RUN:
                needles.add(form)
            else:
                needles.update(form[k:k + SECRET_RUN] for k in range(len(form) - SECRET_RUN + 1))
    if not needles:
        return None
    return re.compile("|".join(re.escape(n) for n in sorted(needles, key=len, reverse=True)), re.IGNORECASE)


def _code(value: Any, secrets: _Secrets) -> str:
    """An error_code as shown in a message: itself if it has the code shape and holds no run of a secret, else
    CODE_WITHHELD. (The comparison with ORDER_NOT_FOUND uses the raw value; see _not_found_code.)"""
    try:
        if type(value) is str and _ERROR_CODE.fullmatch(value) and not secrets.found(value.lower()):
            return value
    except Exception:
        pass
    return CODE_WITHHELD


def _clean(value: Any, pattern: re.Pattern, secrets: _Secrets, final=None) -> str | None:
    """value if it is a str that fully matches `pattern`, holds no run of a secret, and passes `final` (the
    venue's last check) unchanged; else None."""
    try:
        if type(value) is str and pattern.fullmatch(value) and not secrets.found(value.lower()) and (final is None or final(value) == value):
            return value
    except Exception:
        pass
    return None


_SYMBOL = re.compile(r"[A-Za-z0-9./\-]{1,24}")
_ID = re.compile(r"[A-Za-z0-9_\-]{1,64}")
_STATUS = re.compile(r"(?=.{1,48}\Z)[A-Z_]+(?: [A-Z_]+)*")
_KIND = re.compile(r"[A-Za-z_]{1,24}")
_ACCOUNT_TYPE = re.compile(r"[A-Z_]{1,24}")
_ACCOUNT_CLASS = re.compile(r"[A-Z_]{1,32}")
_LABEL = re.compile(r"(?=.{1,30}\Z)[A-Za-z]+(?: [A-Za-z]+)*")
_CLASS = re.compile(r"[A-Za-z][A-Za-z0-9]{0,47}")
_CURRENCY = re.compile(r"[A-Za-z]{3}")


def _plain_id(value: Any, secrets: _Secrets, final=None) -> str | None:
    """An id from an answer (an order id, a client order id, an account id): digits, letters, hyphens and
    underscores, up to 64 characters, holding no run of a secret; an int is written in digits. Else None."""
    if isinstance(value, int) and not isinstance(value, bool):
        try:
            value = str(int(value)) if abs(value) < 10 ** 64 else None
        except Exception:
            return None
    return _clean(value, _ID, secrets, final)


def _status_word(value: Any, secrets: _Secrets, final=None) -> str | None:
    """A raw status as handed back: None when absent, itself in the code shape (spaces allowed, as in the SDK's
    "PARTIAL FILLED"), else WITHHELD."""
    if value is None:
        return None
    return _clean(value, _STATUS, secrets, final) or WITHHELD


def _kind_word(value: Any, secrets: _Secrets, final=None) -> str | None:
    """An instrument_type as handed back: None when absent, itself if it is a short word, else WITHHELD."""
    if value is None:
        return None
    return _clean(value, _KIND, secrets, final) or WITHHELD


def _type_name(value: Any) -> str:
    try:
        return _plain(type(value).__name__, 200)
    except Exception:
        return WITHHELD


def _class_name(value: Any, secrets: _Secrets) -> str:
    """The class name of a failure, as shown: a class name comes from code, not from Webull's servers, so it is shown
    when it is a plain identifier of letters and digits, up to 48, holding no run of a secret; else WITHHELD."""
    name = _type_name(value)
    try:
        return name if _CLASS.fullmatch(name) and not secrets.found(name.lower()) else WITHHELD
    except Exception:
        return WITHHELD


def _attr(obj: Any, name: str) -> Any:
    """getattr(obj, name, None) that never raises: a property that raises counts as absent."""
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def _status_code(res: Any) -> int | None:
    """The HTTP status of an SDK response as a plain int, or None when it is absent or not an int. Never raises."""
    status = _attr(res, "status_code")
    try:
        return int(status) if isinstance(status, int) and not isinstance(status, bool) else None
    except Exception:
        return None


def _safe(exc: BaseException, secrets: _Secrets) -> str:
    """A short description of an SDK failure, every part through the gate: its class name, http_status, error_code
    (CODE_WITHHELD unless it has the code shape) and error_msg. No field read here can raise (see _attr)."""
    try:
        status, code, msg = _attr(exc, "http_status"), _attr(exc, "error_code"), _attr(exc, "error_msg")
        parts = ([_gate(status, secrets, 20)] if status is not None else []) + ([_code(code, secrets)] if code is not None else [])
        msg = _gate(msg, secrets) if msg is not None else ""          # never a truth test on Webull's object
        return _class_name(exc, secrets) + (f" ({', '.join(parts)})" if parts else "") + (f": {msg}" if msg else "")
    except Exception:
        return WITHHELD


def _rebuilt(exc: WebullError, secrets: _Secrets) -> WebullError:
    """A WebullError (or NotTransmitted) raised by the client, rebuilt from its gated text."""
    cls = NotTransmitted if isinstance(exc, NotTransmitted) else WebullError
    return cls("Webull's client failed: " + _gate(exc, secrets))


def _call(paced, kind: str, fn, *args, secrets: _Secrets = NO_SECRETS) -> Any:
    """Run one SDK call through the venue's pacer (`paced`, see WebullVenue._paced). The SDK raises on some failures
    and returns a non-200 response on others; every text taken from the failure passes through the gate."""
    try:
        res = paced(kind, fn, *args)
    except RateLimitHit:                                                  # the module's own words
        raise
    except WebullError as exc:
        raise _rebuilt(exc, secrets) from None
    except Exception as exc:
        raise WebullError("Webull call failed: " + _safe(exc, secrets)) from None
    return _body(res, secrets)


def _checked_error(exc: WebullError, final) -> WebullError:
    """exc itself if its text passes the last check (`final`), else the same kind of error saying TEXT_WITHHELD.
    Either way it leaves with no link to anything it was raised from (see _bare)."""
    text = _plain(exc, 10 ** 6)
    checked = final(text)
    if checked != text:
        if isinstance(exc, OrderRejected):
            code = exc.code if type(exc.code) is str and final(exc.code) == exc.code else None
            exc = OrderRejected(checked, code)
        else:
            cls = LiveTradingNotConfirmed if isinstance(exc, LiveTradingNotConfirmed) else NotTransmitted if isinstance(exc, NotTransmitted) else WebullError
            exc = cls(checked)
    return _bare(exc)


def _bare(exc: WebullError) -> WebullError:
    """The error with no reference to the SDK's exception or response: no __cause__, no __context__, and no
    traceback (whose frames would hold them as local variables). The caller raises it outside any except block,
    so that Python does not attach the exception being handled as its __context__ again."""
    exc.__cause__ = None
    exc.__context__ = None
    exc.__suppress_context__ = False
    return exc.with_traceback(None)


def _public(fn):
    """A public method raises only WebullError (its kind kept: NotTransmitted stays NotTransmitted) and every text it
    raises has passed the last check. Any other exception raised inside (a value no JSON decoder makes, such as a
    5,000-digit integer or nesting 60,000 deep) becomes a WebullError naming only the gated class. What a method
    hands back is built here: Webull's strings in it went through _clean (which ends in the venue's last check) or are
    error texts given the last check where they are stored; the module's own values (its client order id, the
    quantity it sent) are not searched, so a number of ours that happens to look like part of a hex-encoded secret
    is not withheld from the caller."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except WebullError as exc:
            err = _checked_error(exc, self._final)
        except Exception as exc:
            err = _checked_error(WebullError(f"Webull's answer could not be read ({_class_name(exc, self._secrets())})"), self._final)
        raise err                                                         # outside the except blocks: no __context__
    return wrapper


def _json(res: Any) -> Any:
    try:
        return res.json()
    except Exception:
        return None


def _body(res: Any, secrets: _Secrets = NO_SECRETS) -> Any:
    """The JSON body of an SDK response, or an error that names the status but never the credentials.

    The error is built from the documented error fields only (error_code, shown only in the code shape, and
    message, through the gate), never from the raw response text; the status goes through the gate too."""
    status = _status_code(res)
    if status != 200:
        err = _json(res)
        parts = []
        if isinstance(err, dict):
            if err.get("error_code") is not None:
                parts.append(_code(err.get("error_code"), secrets))
            if err.get("message") is not None:
                parts.append(_gate(err.get("message"), secrets, 120))
        shown = status if status is not None else _gate(_attr(res, "status_code"), secrets, 20)
        raise WebullError(f"Webull answered {shown}" + (f": {', '.join(parts)}" if parts else ""))
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


def _not_found_code(exc_or_res_kind: str, code: Any, http_status: Any) -> bool:
    """The exact code ORDER_NOT_FOUND (no case folding, no trimming, the raw value) with a 4xx HTTP status, carried
    by an exception whose class is named ServerException (the SDK's, core/exception/exceptions.py:31) or by a
    non-2xx answer's body (exc_or_res_kind "answer")."""
    try:
        return (exc_or_res_kind in ("ServerException", "answer") and type(code) is str and code in ORDER_NOT_FOUND_CODES
                and isinstance(http_status, int) and not isinstance(http_status, bool) and 400 <= http_status < 500)
    except Exception:
        return False


def _refusal(exc: Any) -> tuple[Any, Any, int] | None:
    """(raw error_code, raw error_msg, http_status) when `exc` is the SDK's ServerException (by class name) with a 4xx
    status: Webull answered and refused. Else None (a 5xx, a transport failure, anything unreadable). The raw values
    are used only for comparisons, never shown."""
    try:
        status = _attr(exc, "http_status")
        if _type_name(exc) == "ServerException" and type(status) is int and 400 <= status < 500:
            return _attr(exc, "error_code"), _attr(exc, "error_msg"), status
    except Exception:
        pass
    return None


def _not_present(kind: str, code: Any, msg: Any, http_status: Any) -> bool:
    """The "no such order" answer observed in the test environment (2026-11-13): the SDK's ServerException with a 4xx
    status, code exactly OPENAPI_PARAM_ERR and a message holding "order not present" (any case), or ORDER_NOT_FOUND as
    before (see _not_found_code). The message is read raw, before gating, only for this comparison."""
    try:
        if _not_found_code(kind, code, http_status):
            return True
        return (kind == "ServerException" and type(code) is str and code == NOT_PRESENT_CODE and type(http_status) is int
                and 400 <= http_status < 500 and isinstance(msg, str)
                and NOT_PRESENT_TEXT in str.lower(str.__getitem__(msg, slice(0, MAX_GATE_INPUT))))
    except Exception:
        return False


def _cancel_word(kind: str, code: Any, msg: Any, http_status: Any) -> str | None:
    """Cancel's word for a refusal (observed in the test environment, 2026-11-13), or None: a 4xx carried by the SDK's
    ServerException (kind "ServerException") or a non-2xx answer's body (kind "answer") with code exactly
    OPENAPI_ORDER_CAN_NOT_BE_CANCEL_FOR_PENDING_SUBMIT ("not yet") or OPENAPI_ORDER_CAN_NOT_BE_CANCEL ("already
    finished"), or the "no such order" answer (_not_present: "no such order")."""
    try:
        if _not_present(kind, code, msg, http_status):
            return "no such order"
        if (kind in ("ServerException", "answer") and type(code) is str and code in CANCEL_ANSWERS
                and type(http_status) is int and 400 <= http_status < 500):
            return CANCEL_ANSWERS[code]
    except Exception:
        pass
    return None


def _trader_symbol(symbol: str, instrument_type: Any) -> str:
    """Webull's (already clean) symbol under the trader's own name: crypto BTCUSD comes back as BTC-USD."""
    sym = symbol.upper()
    if instrument_type == "CRYPTO" and sym.endswith("USD") and "-" not in sym:
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
    return ORDER_STATES.get(raw, "unknown_status") if type(raw) is str else "unknown_status"      # exact, as documented


def _filled_units(item: dict) -> float | None:
    n = _number(item.get("filled_quantity"))
    return None if n is None or n < 0 else n + 0.0                      # a negative fill is unreadable; -0.0 becomes 0.0


def _decimal(x: float) -> Decimal:
    """The decimal Webull wrote, as near as a float keeps it: repr gives the shortest text that reads back as x."""
    return Decimal(repr(float(x)))


# ---- pacing
#
# Webull limits each kind of call per app key and answers HTTP 429 past a limit ("the request is rejected, no partial
# processing occurs"); repeated breaches can block the address for a time. Per-call limits: developer.webull.com/apis/
# docs/rate-limits (read 2026-11-13). The overall limit: the owner's Webull console states 60 requests a minute for the
# Trading API (live side); the paper side's 30 per 60 s is taken as its overall limit too. {kind: (calls, seconds)}.
CALL_KINDS = ("account_list", "balance", "positions", "order_detail", "open_orders", "place", "replace", "cancel", "all")
READ_KINDS = frozenset({"account_list", "balance", "positions", "order_detail", "open_orders"})
RATE_LIMITS = {
    "sandbox": {"account_list": (30, 60), "balance": (30, 60), "positions": (30, 60), "order_detail": (30, 60),
                "open_orders": (30, 60), "place": (30, 60), "replace": (30, 60), "cancel": (30, 60), "all": (30, 60)},
    "production": {"account_list": (10, 30), "balance": (2, 2), "positions": (2, 2), "order_detail": (2, 2),
                   "open_orders": (2, 2), "place": (600, 60), "replace": (600, 60), "cancel": (600, 60), "all": (60, 60)},
}
RATE_SHARE = 0.8                                  # the pacer uses 80% of every limit (at least one call per window)
READ_RETRY_WAITS = (2, 4, 8)                      # a 429 on a read: 4 tries in all, waiting 2, 4 and 8 seconds
SALE_READ_MAX_AGE = 5.0                           # at most this many seconds from the holdings read to the order call
SALE_REREADS = 3                                  # then open orders and holdings are read again, at most this often
REPEAT_CODE = "OPENAPI_TRADE_PLACE_ORDER_REPEAT"  # Webull: an order with this client order id already exists
_default_clock, _default_sleep = time.monotonic, time.sleep


def _limit_table(table: Any) -> dict[str, tuple[int, float]]:
    """A copy of a limit table, or WebullError: a dict with exactly the kinds in CALL_KINDS, each (calls, seconds) with
    calls a whole number of at least 1 and seconds a finite number above zero."""
    if not isinstance(table, dict) or set(table) != set(CALL_KINDS):
        raise WebullError(f"a limit table must name exactly these kinds of call: {', '.join(CALL_KINDS)}")
    out = {}
    for kind in CALL_KINDS:
        entry = table[kind]
        ok = isinstance(entry, (tuple, list)) and len(entry) == 2
        calls, seconds = entry if ok else (None, None)
        ok = (ok and type(calls) is int and calls >= 1 and isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
              and math.isfinite(seconds) and seconds > 0)
        if not ok:
            raise WebullError(f"the limit for {kind} must be (calls, seconds): a whole number of calls of at least 1 and seconds above zero")
        out[kind] = (calls, float(seconds))
    return out


class _Pacer:
    """A sliding-window pacer: before a call of `kind` it waits until fewer than the allowed number of calls of that
    kind, and fewer than the allowed number of calls of every kind together ("all"), were made in the last window.
    Allowed = RATE_SHARE of each limit, rounded down, at least 1. It counts calls rather than spacing them, so a burst
    inside the limits is not slowed. Each window is counted WINDOW_MARGIN (one second) longer than Webull states it, so
    that two full bursts one stated window apart never fall inside one window counted with both ends.

    Time is the injected clock, but never earlier than a call already recorded or than where the pacer's own sleeps
    have brought it (a clock that goes backwards cannot make it ask for a long sleep), and no single sleep is longer
    than the longest stated window plus one second.

    The pacer belongs to one venue object. A second WebullVenue, or a second process, on the same App Key keeps its
    own count and is not seen here; if together they pass Webull's limit, a read that then gets a 429 is tried again
    as WebullVenue._paced states, and place, replace and cancel are not."""

    MAX_ROUNDS = 10_000
    WINDOW_MARGIN = 1.0

    def __init__(self, table: dict[str, tuple[int, float]], clock, sleep):
        self.allowed = {k: (max(1, math.floor(n * RATE_SHARE)), s + self.WINDOW_MARGIN) for k, (n, s) in table.items()}
        self.longest_sleep = max(s for _, s in table.values()) + 1.0
        self.clock, self.sleep = clock, sleep
        self.times: dict[str, list[float]] = {k: [] for k in table}
        self.floor = -math.inf

    def now(self) -> float:
        """The clock's time, never earlier than a recorded call or than the end of the pacer's last sleep."""
        t = float(self.clock())
        if not math.isfinite(t):
            raise WebullError("the pacer's clock did not give a finite time")
        self.floor = max(self.floor, t)
        return self.floor

    def last(self, kind: str) -> float | None:
        """When the latest call of `kind` was recorded, or None."""
        return self.times[kind][-1] if self.times[kind] else None

    def _delay(self, kind: str, now: float, need: int = 1) -> float:
        n, window = self.allowed[kind]
        need = min(need, n)
        times = self.times[kind]
        while times and times[0] <= now - window:
            times.pop(0)
        over = len(times) - (n - need)                                    # how many recorded calls must first leave
        return times[over - 1] + window - now if over > 0 else 0.0

    def wait_free(self, needs: dict[str, int]) -> float:
        """Wait until all the calls in `needs` ({kind: count}) could be made at once, recording nothing; the time."""
        total = sum(needs.values())
        for _ in range(self.MAX_ROUNDS):
            now = self.now()
            delay = max([self._delay(k, now, m) for k, m in needs.items()] + [self._delay("all", now, total)])
            if delay <= 0:
                return now
            pause = min(delay, self.longest_sleep)
            self.sleep(pause)
            self.floor = now + pause
        raise WebullError("the pacer's clock does not move forward while it waits")

    def wait(self, kind: str) -> float:
        """Wait for one call of `kind` and record it; the time recorded."""
        now = self.wait_free({kind: 1})
        self.times[kind].append(now)
        self.times["all"].append(now)
        return now


def _too_many(exc_or_res: Any, raised: bool) -> bool:
    """A 429: the SDK's ServerException with http_status 429, or an answer with status 429."""
    if raised:
        return _type_name(exc_or_res) == "ServerException" and type(_attr(exc_or_res, "http_status")) is int and _attr(exc_or_res, "http_status") == 429
    return _status_code(exc_or_res) == 429


_UNSET = object()                                                       # the account's class, not looked up yet


class WebullVenue:
    """Orders and account reads on one Webull account."""

    def __init__(self, app_key: str, app_secret: str, environment: str = "sandbox", account_id: str | None = None,
                 client: Any | None = None, confirmation_file: str | Path = LIVE_CONFIRMATION, region: str = "us",
                 confirmed_not_found: bool = False, token_dir: str | Path | None = None, account_class: str | None = None,
                 limits: dict | None = None, clock: Any = None, sleep: Any = None) -> None:
        """`confirmed_not_found` (default False): read the "no such order" answers (see order_status) as None. Turn
        it on only together with a generous `settle_seconds` in the order ledger: the detail call could show "not
        present" for a while after an order was placed, and with the flag on that reads as "never arrived" once the
        settle time has passed. In the test environment (2026-11-13) an accepted order was visible on the very next
        read in every case seen (four orders, reads from 0.2 s), so no gap was observed, on the paper side only. The
        ledger's recheck is the backstop.

        `account_class` (from WEBULL_ACCOUNT_CLASS in from_secrets): the class of the account to trade, such as
        INDIVIDUAL_CASH or CRYPTO; see the account_id property for how the account is chosen. EVENTS_CASH makes this an
        Events venue (see the module notes, "Event contracts"), named webull-<environment>-events.

        Pacing: every SDK call waits for the venue's pacer (_Pacer), which belongs to this object only: a second
        venue object or a second process on the same App Key is not seen by it, and a read that then gets a 429 is
        tried again as _paced states. `limits` replaces the environment's table in
        RATE_LIMITS ({kind: (calls, seconds)} for every kind in CALL_KINDS; a malformed table is refused), and
        `clock` / `sleep` replace time.monotonic / time.sleep (tests pass a fake clock and never really sleep).

        `token_dir`: where the SDK keeps its access token when a real client is built (the SDK writes it in plain
        text). Default ~/.valedia/webull/<environment>/; created with mode 0700; a directory inside the repository
        is refused.

        Nothing but WebullError leaves the constructor, and it carries no link to what the SDK raised. Every account
        id the venue is given or learns is handed to valedia_trader.core.secrets.remember when that function exists
        (looked up at that moment; nothing happens without it), and the HTTP library's request log
        (urllib3.connectionpool) is set to WARNING unless a higher level is already set."""
        err = None
        try:
            self._setup(app_key, app_secret, environment, account_id, client, confirmation_file, region, confirmed_not_found, token_dir,
                        account_class, limits, clock, sleep)
        except WebullError as exc:
            err = exc
        except Exception as exc:
            err = WebullError(f"could not set up the Webull venue ({_class_name(exc, _secrets_of(app_key, app_secret, account_id))})")
        if err is not None:
            words = (app_key, app_secret, account_id) if all(isinstance(w, str) or w is None for w in (app_key, app_secret, account_id)) else ()
            raise _checked_error(err, lambda text: _last_check(text, words))

    def _setup(self, app_key, app_secret, environment, account_id, client, confirmation_file, region, confirmed_not_found, token_dir,
               account_class=None, limits=None, clock=None, sleep=None) -> None:
        if environment not in HOSTS:
            raise WebullError(f"environment must be one of {sorted(HOSTS)}")
        if not str(app_key).strip() or not str(app_secret).strip():
            raise WebullError("a Webull App Key and App Secret are required")
        if account_class is not None and not (type(account_class) is str and _ACCOUNT_CLASS.fullmatch(account_class)):
            raise WebullError("the account class must be an upper-case code such as INDIVIDUAL_CASH or CRYPTO")
        self._wanted_class = account_class
        self._account_class: Any = _UNSET
        self._limits = _limit_table(RATE_LIMITS[environment] if limits is None else limits)
        clock = _default_clock if clock is None else clock
        sleep = _default_sleep if sleep is None else sleep
        if not callable(clock) or not callable(sleep):
            raise WebullError("the pacer's clock and sleep must be callables")
        self._sleep = sleep
        self._pacer = _Pacer(self._limits, clock, sleep)
        # An Events venue trades event contracts only, under its own name (so its own ledger, state and lock files); every
        # other venue keeps the name it had before event contracts were built.
        self._events = account_class == EVENTS_CLASS
        self._sent_events: dict[str, str] = {}            # client order id -> the event name this object sent under it
        suffix = "-events" if self._events else ""
        self.environment, self.host, self.name = environment, HOSTS[environment], f"webull-{environment}{suffix}"
        self._confirmation_file = Path(confirmation_file)
        self._app_key, self._app_secret = app_key, app_secret    # kept only to be withheld from every text
        self._account_id = None
        self._seen_accounts: set[str] = set()
        _quiet_http_logging()
        secrets = _secrets_of(app_key, app_secret, account_id)
        if client is None:
            try:
                from webull.core.client import ApiClient
                from webull.trade.trade_client import TradeClient
            except ImportError:
                raise WebullError("the Webull SDK is not installed: uv pip install 'webull-openapi-python-sdk>=3.0.4'") from None
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
                text = _last_check(f"could not open a Webull session on {self.host}: {_safe(exc, secrets)}", (app_key, app_secret, account_id))
                raise WebullError(text) from None
            finally:
                _quiet_sdk_logging()
                _remove_new_empty_sdk_log(sdk_log_existed, sdk_log)
        self._client = client
        if account_id is not None:
            self._learn(account_id)
        self._account_id = account_id
        self._confirmed_not_found = confirmed_not_found is True

    def __repr__(self) -> str:
        return f"WebullVenue({self.name})"

    def _words(self) -> tuple:
        """The venue's complete secret set: the App Key, the App Secret, the account id when known and every account
        id seen in an answer."""
        known = {self._account_id} if isinstance(self._account_id, str) else set()
        # an account id shorter than SECRET_RUN has no run to search for; matched whole it would withhold every
        # message holding, say, the letter A
        return (self._app_key, self._app_secret, *sorted(a for a in known | self._seen_accounts if len(a.strip()) >= SECRET_RUN))

    def _secrets(self, with_account: bool = True) -> _Secrets:
        """The gate's secrets: the complete set, or (with_account False) only the App Key and App Secret."""
        return _secrets_of(*self._words()) if with_account else _secrets_of(self._app_key, self._app_secret)

    def _final(self, text: Any) -> str:
        """The last check with the venue's complete secret set, whatever any caller has passed to the gate."""
        return _last_check(text, self._words())

    def _learn(self, account_id: Any) -> None:
        """Remember an account id seen in an answer or given: withheld from every text from now on, and handed to
        valedia_trader.core.secrets.remember when that function exists."""
        if not isinstance(account_id, str) or not account_id.strip():
            return
        self._seen_accounts.add(account_id)
        try:
            remember = getattr(importlib.import_module("valedia_trader.core.secrets"), "remember", None)
            if callable(remember):
                remember(account_id)
        except Exception:
            pass

    def _call(self, kind: str, fn, *args) -> Any:
        return _call(self._paced, kind, fn, *args, secrets=self._secrets())

    def _paced(self, kind: str, fn, *args) -> Any:
        """One SDK call of `kind`, made once the pacer allows it. For a read (READ_KINDS), a 429 (raised or answered)
        is tried again after 2, 4 and 8 seconds, 4 tries in all, and then raises WebullError saying the limit was hit.
        Place, replace and cancel are made once, never retried here: their 429 goes back to the caller as it came."""
        tries = 1 + len(READ_RETRY_WAITS) if kind in READ_KINDS else 1
        for attempt in range(tries):
            if attempt:
                self._sleep(READ_RETRY_WAITS[attempt - 1])
            self._pacer.wait(kind)
            if tries == 1:
                return fn(*args)
            try:
                res = fn(*args)
            except Exception as exc:
                if not _too_many(exc, True):
                    raise
                continue
            if not _too_many(res, False):
                return res
        raise RateLimitHit(f"Webull's rate limit was hit: the {kind} call answered 429 (too many requests) on all {tries} tries, "
                          f"waiting {', '.join(str(w) for w in READ_RETRY_WAITS)} seconds between them")

    @classmethod
    def from_secrets(cls, environ=None, **kwargs) -> "WebullVenue":
        from valedia_trader.core.secrets import load_env, secret
        import os
        env = os.environ if environ is None else environ
        if environ is None:
            load_env()
        return cls(secret("WEBULL_APP_KEY", env), secret("WEBULL_APP_SECRET", env), environment=(env.get("WEBULL_ENV") or "sandbox").strip().lower(),
                   account_id=(env.get("WEBULL_ACCOUNT_ID") or "").strip() or None,
                   account_class=(env.get("WEBULL_ACCOUNT_CLASS") or "").strip().upper() or None, **kwargs)

    # ---- reading (allowed in both environments)
    @_public
    def accounts(self) -> list[dict]:
        """[{account_id, account_type, account_class, account_label}]. account_id: a plain id (digits, letters,
        hyphens, underscores, up to 64) holding no run of the venue's complete secret set (_words()) other than the
        account ids listed in this very answer (an id is not withheld from its own listing, nor because it looks like
        another listed id), else None. account_type (CASH or MARGIN seen) and account_class (INDIVIDUAL_CASH, CRYPTO,
        ... seen) only in the upper-case code shape, account_label ("Individual Cash" seen) only as letters and
        single spaces up to 30 characters, each holding no run of the key, the secret or any account id seen, else
        None. Every account id in the answer is learned (see _learn)."""
        body = self._call("account_list", self._client.account_v2.get_account_list)
        rows = body if isinstance(body, list) else body.get("accounts", body.get("data", [])) if isinstance(body, dict) else None
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise WebullError("Webull's account list has an unexpected shape")
        for r in rows:
            self._learn(r.get("account_id"))
        listed = {r.get("account_id") for r in rows if isinstance(r.get("account_id"), str)}
        words = self._words()
        others = (*words[:2], *(w for w in words[2:] if w not in listed))      # the key and the secret always
        for_ids, everything = _secrets_of(*others), self._secrets()
        return [{"account_id": _plain_id(r.get("account_id"), for_ids, lambda s: _last_check(s, others)),
                 "account_type": _clean(r.get("account_type"), _ACCOUNT_TYPE, everything, self._final),
                 "account_class": _clean(r.get("account_class"), _ACCOUNT_CLASS, everything, self._final),
                 "account_label": _clean(r.get("account_label"), _LABEL, everything, self._final)} for r in rows]

    @property
    @_public
    def account_id(self) -> str:
        """The account this venue trades, chosen in this order: an account_id given to the constructor (with an
        account_class also given, the account list must show that id with that class); else the one account of the
        account_class given (WEBULL_ACCOUNT_CLASS), which must be exactly one; else the only account visible; else
        WebullError naming the classes visible. Webull's account_id is a long internal id the owner cannot see or
        type (observed in the test environment, 2026-11-13), so the class is the way to choose."""
        if self._account_id is None or (self._wanted_class is not None and self._account_class is _UNSET):
            chosen, cls = self._choose_account()
            self._learn(chosen)
            self._account_id, self._account_class = chosen, cls
        return self._account_id

    def _choose_account(self) -> tuple[str, str | None]:
        rows, given, wanted = self.accounts(), self._account_id, self._wanted_class
        if given is not None:                                             # only reached with a class to check
            mine = [r for r in rows if r["account_id"] == given]
            cls = mine[0]["account_class"] if len(mine) == 1 else None
            if cls != wanted:
                raise WebullError(f"the account given (WEBULL_ACCOUNT_ID) is not of the class {wanted} (WEBULL_ACCOUNT_CLASS): it is "
                                  + (cls if cls else "not in the account list with a readable class"))
            return given, cls
        classes = ", ".join(sorted({r["account_class"] or "unknown" for r in rows})) or "none"
        if wanted is not None:
            mine = [r for r in rows if r["account_class"] == wanted]
            if len(mine) != 1:
                raise WebullError(f"{len(mine)} accounts of the class {wanted} are visible (classes visible: {classes}); set "
                                  "WEBULL_ACCOUNT_CLASS to a class that names exactly one account")
        elif len(rows) == 1:
            mine = rows
        else:
            raise WebullError(f"{len(rows)} accounts are visible (classes: {classes}); set WEBULL_ACCOUNT_CLASS to choose one "
                              "(or WEBULL_ACCOUNT_ID to Webull's internal id)")
        if not mine[0]["account_id"]:
            raise WebullError("Webull's account list names no plain account_id")
        return mine[0]["account_id"], mine[0]["account_class"]

    @property
    @_public
    def account_class(self) -> str | None:
        """The class of this venue's account (INDIVIDUAL_CASH, CRYPTO, ...), looked up once in the account list; None
        when the list shows no readable class for it. Read-only."""
        account = self.account_id
        if self._account_class is _UNSET:
            mine = [r for r in self.accounts() if r["account_id"] == account]
            self._account_class = mine[0]["account_class"] if len(mine) == 1 else None
        return self._account_class

    def _fits(self, crypto: bool) -> None:
        """NotTransmitted unless the order's instrument type trades in this account's class: stocks and funds in
        INDIVIDUAL_CASH or INDIVIDUAL_MARGIN, crypto only in CRYPTO (event contracts only through an Events venue, _submit_event). An
        account of no readable class fits nothing."""
        kind = "CRYPTO" if crypto else "EQUITY"
        cls = self.account_class
        if cls not in ACCOUNT_CLASSES_FOR[kind]:
            raise NotTransmitted(f"a {'crypto' if crypto else 'stock or fund'} order does not fit this account (class {cls or 'unknown'}): "
                                 f"it trades only in {' or '.join(sorted(ACCOUNT_CLASSES_FOR[kind]))}; choose the account with "
                                 "WEBULL_ACCOUNT_CLASS")

    @_public
    def balance(self) -> dict:
        """Only the documented fields cash() reads (docs account-balance): total_asset_currency (a three-letter code),
        total_cash_balance (a number) and account_currency_assets [{currency, cash_balance, settled_cash,
        buying_power}]; a field that is absent, unreadable or not of that shape is left out. An answer that is not an
        object, an account_currency_assets that is not a list of objects, or a total_asset_currency that is not a
        string raises WebullError."""
        body = self._call("balance", self._client.account_v2.get_account_balance, self.account_id)
        if not isinstance(body, dict):
            raise WebullError(f"Webull's balance answer is a {type(body).__name__}, not an object")
        assets = body.get("account_currency_assets")
        assets = [] if assets is None else assets
        if not isinstance(assets, list) or not all(isinstance(a, dict) for a in assets):
            raise WebullError("Webull's balance has an unexpected shape: account_currency_assets is not a list of objects")
        currency = body.get("total_asset_currency")
        if currency is not None and not isinstance(currency, str):
            raise WebullError("Webull's balance has an unexpected shape: total_asset_currency is not a string")
        secrets = self._secrets()

        def kept(source: dict, numbers: tuple[str, ...], code: str) -> dict:
            out: dict = {}
            cur = _clean(source.get(code), _CURRENCY, secrets, self._final)
            if cur is not None:
                out[code] = cur
            for name in numbers:
                n = _number(source.get(name))
                if n is not None:
                    out[name] = n
            return out

        out = kept(body, ("total_cash_balance",), "total_asset_currency")
        out["account_currency_assets"] = [kept(a, ("cash_balance", "settled_cash", "buying_power"), "currency") for a in assets]
        return out

    @_public
    def positions(self) -> dict[str, float]:
        """In an Events venue: contracts held by event name ({TICKER:yes or TICKER:no: contracts}) from the rows whose
        instrument_type is exactly EVENT; every other row goes to other_positions(). An EVENT row whose ticker is not
        a clean ticker (parse_event_name) or whose event_outcome is not exactly yes or no raises WebullError, and so
        does one name in more than one row. In any other venue, as follows.

        Held quantity by the trader's own symbol names (crypto as BTC-USD), from the rows whose instrument_type
        is exactly EQUITY (keyed by symbol) or CRYPTO (BTCUSD keyed as BTC-USD). Docs /apis/docs/reference/
        account-position (read 2026-11-12) list the instrument types EQUITY, OPTION, FUTURES, CRYPTO and EVENT, and
        give as their example a row with symbol AAPL, instrument_type OPTION, quantity "1": such a row is not a
        holding of AAPL. Every row that is not EQUITY or CRYPTO, including one with no, an empty or an unrecognised
        instrument_type, gives no holding and is returned by other_positions() instead; that a real row always
        carries its instrument_type is to be checked in the test environment.

        Only the documented `quantity` field is read: a row without it, or with a quantity that is unreadable, not
        finite or above MAX_HOLDING_UNITS, gives no holding. A row whose symbol is not a clean symbol (see _symbol)
        is an unexpected shape and raises WebullError. A symbol in more than one EQUITY row, or in more than one
        CRYPTO row, raises WebullError (whether Webull ever splits a holding over rows is to be checked in the test
        environment), so a sale can never be licensed by adding rows up; so does an EQUITY and a CRYPTO row under the
        same name here. An EQUITY row and, say, an OPTION row for the same symbol are not duplicates."""
        holdings, _, duplicated = self._read_positions()
        return self._flatten(holdings, duplicated)

    @staticmethod
    def _flatten(holdings: dict[tuple[str, str], float | None], duplicated: set) -> dict[str, float]:
        if duplicated:
            kind, sym = sorted(duplicated)[0]
            raise WebullError(f"Webull's position list has {sym} in more than one row (both {kind}); cannot tell what is held")
        names = [sym for _, sym in holdings]
        for sym in names:
            if names.count(sym) > 1:
                raise WebullError(f"Webull's position list has {sym} in more than one row (as EQUITY and as CRYPTO); cannot tell what is held")
        return {sym: qty for (_, sym), qty in holdings.items() if qty is not None}

    @_public
    def other_positions(self) -> list[dict]:
        """Every position row that is not a holding in positions() (its instrument_type is not exactly EQUITY or
        CRYPTO: an OPTION, FUTURES or EVENT row, or one with no, an empty or an unrecognised type), as {symbol,
        instrument_type (as given if it is a short word, None when absent, else WITHHELD), quantity (a float, or None
        if absent or unreadable)}. Duplicated holdings do not make this raise; positions() and every sale still do."""
        return self._read_positions()[1]

    def _symbol(self, value: Any, where: str) -> str:
        """A symbol from an answer: up to 24 letters, digits, dots, hyphens and slashes, holding no run of a secret.
        Anything else (missing, padded, longer, other characters) is an unexpected shape."""
        sym = _clean(value, _SYMBOL, self._secrets(), self._final)
        if sym is None:
            raise WebullError(f"Webull's {where} has a row whose symbol is missing or not a clean symbol")
        return sym

    def _read_positions(self) -> tuple[dict[tuple[str, str], float | None], list[dict], set]:
        """({(EQUITY or CRYPTO, the trader's symbol): quantity or None}, [the other rows], {duplicated holdings})
        from one position read; in an Events venue ({(EVENT, event name): ...}, ...) (see _read_event_positions)."""
        if self._events:
            return self._read_event_positions()
        body = self._call("positions", self._client.account_v2.get_account_position, self.account_id)
        rows = body if isinstance(body, list) else body.get("positions", body.get("holdings", body.get("data", []))) if isinstance(body, dict) else None
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise WebullError("Webull's position list has an unexpected shape")
        secrets = self._secrets()
        holdings: dict[tuple[str, str], float | None] = {}
        others: list[dict] = []
        duplicated: set = set()
        for row in rows:
            kind = row.get("instrument_type")
            sym = self._symbol(row.get("symbol"), "position list")
            qty = _number(row.get("quantity"))                         # the documented field only
            if qty is not None and abs(qty) > MAX_HOLDING_UNITS:          # absurd: no holding
                qty = None
            if type(kind) is not str or kind not in HOLDING_TYPES:
                others.append({"symbol": sym, "instrument_type": _kind_word(kind, secrets, self._final), "quantity": qty})
                continue
            sym = _trader_symbol(sym, kind)
            if (kind, sym) in holdings:
                duplicated.add((kind, sym))
            holdings[(kind, sym)] = qty
        return holdings, others, duplicated

    def _held(self, symbol: str) -> Decimal:
        """The holding a sale of `symbol` may draw on: its CRYPTO row for a crypto symbol, else its EQUITY row. Any
        duplicated holding in the list fails closed: the list cannot be trusted for any sale."""
        holdings, _, duplicated = self._read_positions()
        self._flatten(holdings, duplicated)
        kind, sym = ("CRYPTO" if _is_crypto(symbol) else "EQUITY"), symbol.upper()
        qty = holdings.get((kind, sym))
        return _decimal(qty) if qty is not None else Decimal(0)

    def _reserved_by_open_sells(self, symbol: str) -> Decimal:
        """The unfilled remainder of every open sell for `symbol` and its instrument type: total minus filled, or
        the total when the filled quantity is unreadable (or above the total). Raises WebullError, so the sale is
        not sent, when the open orders cannot be read (an open order without a clean symbol included), or when an
        open order for this symbol has an unreadable total or side (units None), or carries no recognised
        instrument_type (it could be this very holding)."""
        kind, sym = ("CRYPTO" if _is_crypto(symbol) else "EQUITY"), symbol.upper()
        names = {sym, sym.replace("-", "")}
        reserved = Decimal(0)
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
            total, filled = _decimal(abs(o["units"])), o["filled_units"]
            filled = _decimal(filled) if filled is not None else None
            reserved += total - filled if filled is not None and filled <= total else total
        return reserved

    # ---- event contracts: reading
    def _event_row_name(self, row: dict, where: str) -> str:
        """The event name `TICKER:outcome` of an EVENT row of an answer, or WebullError. The ticker passes the gate's
        rules for a value handed back (_clean: the ticker shape, no run of a secret, the last check unchanged); the
        outcome must be exactly the str yes or no, and what is returned is the module's own word for it."""
        ticker = _clean(row.get("symbol"), _EVENT_TICKER, self._secrets(), self._final)
        if ticker is None:
            raise WebullError(f"Webull's {where} has an event row whose ticker is missing or not a clean ticker")
        outcome = row.get("event_outcome")
        if type(outcome) is not str or outcome not in EVENT_OUTCOMES:
            raise WebullError(f"Webull's {where} has an event row whose outcome is not exactly yes or no")
        return f"{ticker}:{'yes' if outcome == 'yes' else 'no'}"

    def _read_event_positions(self) -> tuple[dict[tuple[str, str], float | None], list[dict], set]:
        """({(EVENT, event name): contracts or None}, [the other rows], {duplicated holdings}) from one position read
        of an Events venue. Only a row whose instrument_type is exactly EVENT is a holding; its quantity is read as for
        stocks (the documented `quantity` only; unreadable, not finite or above MAX_HOLDING_UNITS gives None)."""
        body = self._call("positions", self._client.account_v2.get_account_position, self.account_id)
        rows = body if isinstance(body, list) else body.get("positions", body.get("holdings", body.get("data", []))) if isinstance(body, dict) else None
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise WebullError("Webull's position list has an unexpected shape")
        secrets = self._secrets()
        holdings: dict[tuple[str, str], float | None] = {}
        others: list[dict] = []
        duplicated: set = set()
        for row in rows:
            kind = row.get("instrument_type")
            qty = _number(row.get("quantity"))
            if qty is not None and abs(qty) > MAX_HOLDING_UNITS:
                qty = None
            if type(kind) is str and kind == EVENT_INSTRUMENT:
                key = (EVENT_INSTRUMENT, self._event_row_name(row, "position list"))
                if key in holdings:
                    duplicated.add(key)
                holdings[key] = qty
            else:
                others.append({"symbol": self._symbol(row.get("symbol"), "position list"),
                               "instrument_type": _kind_word(kind, secrets, self._final), "quantity": qty})
        return holdings, others, duplicated

    def _event_held(self, name: str) -> Decimal:
        """The contracts of `name` a sale may draw on; any duplicated holding fails closed."""
        holdings, _, duplicated = self._read_positions()
        self._flatten(holdings, duplicated)
        qty = holdings.get((EVENT_INSTRUMENT, name))
        return _decimal(qty) if qty is not None else Decimal(0)

    def _reserved_by_open_event_sells(self, name: str) -> Decimal:
        """The unfilled remainder of every open sale of exactly this ticker and outcome (total minus filled, or the
        total when the filled quantity is unreadable or above it). Open purchases and other names do not count.
        WebullError, so the sale is not sent, when an open order of this ticker carries no recognised instrument type
        or an open order of this name has an unreadable quantity or side."""
        ticker = name.partition(":")[0]
        reserved = Decimal(0)
        for o in self.open_orders():
            given = o["instrument_type"]
            if type(given) is not str or given not in ORDER_INSTRUMENT_TYPES:
                if o["symbol"] in (name, ticker):
                    raise WebullError(f"an open order for {ticker} carries no recognised instrument type; cannot tell what is already being sold")
                continue
            if given != EVENT_INSTRUMENT or o["symbol"] != name:
                continue
            if o["units"] is None:
                raise WebullError(f"an open order for {name} has an unreadable quantity or side; cannot tell what is already being sold")
            if o["units"] >= 0:
                continue
            total, filled = _decimal(abs(o["units"])), o["filled_units"]
            filled = _decimal(filled) if filled is not None else None
            reserved += total - filled if filled is not None and filled <= total else total
        return reserved

    def _event_fee(self, item: dict, filled: float | None, secrets: _Secrets) -> tuple[float | None, str | None]:
        """(fee, fee type) of an event order row. The fee is the actual_value of the one fees entry (plus
        commission.actual_commission when given), and None when nothing has filled (the entry is there from the moment
        the order is accepted: it is not a fee paid), when there is not exactly one entry, or when a value is
        unreadable or negative. The fee type is the entry's `type` in the code shape holding no run of a secret, None
        when absent, else WITHHELD."""
        fees = item.get("fees")
        one = isinstance(fees, list) and len(fees) == 1 and isinstance(fees[0], dict)
        fee_type = None
        if one and fees[0].get("type") is not None:
            fee_type = _clean(fees[0].get("type"), _FEE_TYPE, secrets, self._final) or WITHHELD
        if filled is None or not filled > 0 or not one:
            return None, fee_type
        value = _number(fees[0].get("actual_value"))
        if value is None or value < 0:
            return None, fee_type
        commission = item.get("commission")
        if commission is not None:
            if not isinstance(commission, dict):
                return None, fee_type
            if "actual_commission" in commission:
                extra = _number(commission["actual_commission"])
                if extra is None or extra < 0:
                    return None, fee_type
                value += extra
        return (value + 0.0 if math.isfinite(value) else None), fee_type

    def _expected_name(self, cid: str, expected: Any) -> str:
        """The event name an order read back must carry: `expected` when given (it must be an event name), else the name
        this venue object sent under the client order id; WebullError when neither is known or they differ."""
        sent = self._sent_events.get(cid)
        if expected is not None:
            if parse_event_name(expected) is None:
                raise WebullError("the name expected for this order is not an event name (TICKER:yes or TICKER:no)")
            if sent is not None and sent != expected:
                raise WebullError("the name expected for this order is not the name this venue sent under its client order id")
            return expected
        if sent is None:
            raise WebullError("this venue did not send the order under this tag, so it cannot check the ticker and outcome read "
                              "back against what was sent; pass the name that was sent (expected=)")
        return sent

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

    @_public
    def submit(self, symbol: str, units: float, tag: str, price: float | None = None, limit_price: float | None = None,
               session: str = "CORE") -> dict:
        """Place one order. `price` is the last known price, used only to value the order for the live cap. `session`:
        CORE (regular hours, the default), ALL or NIGHT, sent as support_trading_session; NIGHT takes limit orders
        only, crypto takes none (see _session).

        Everything that can refuse (choosing the account, the order fitting the account's class, the session, the
        long-only check, the live guard, building the payload) runs before the single place_order call, and any
        failure there raises NotTransmitted: the order was certainly not sent. Waiting for the place slot is part of
        that: a failure while waiting (the pacer's sleep or clock failing) is NotTransmitted, because place_order has
        not been called. For a sell, the open orders are read first and the holding second, so a fill between the two
        reads can only make the free amount look smaller; the sale is allowed only when its amount is not above the
        holding minus the open sells, rounded DOWN to the quantity step. A sale first waits until the two reads and
        the order call could all be made at once, then reads; if more than SALE_READ_MAX_AGE (5) seconds on the clock
        still pass between the holdings read and the order call, it reads open orders and holdings again and checks
        again, at most SALE_REREADS (3) times, and is then NotTransmitted.

        In an Events venue the order is an event contract's, and only that: see _submit_event. In any other venue a
        `symbol` holding a colon (every event name) is NotTransmitted before anything is read or sent.

        After the place_order call: when it fails with the SDK's ServerException and a 4xx status (Webull answered and
        refused; a 429 included: place is never retried), the order's detail is read once under the same client order
        id. If the order is there, submit returns it as accepted (this covers OPENAPI_TRADE_PLACE_ORDER_REPEAT: the
        same client order id was sent before and that order exists); if the detail gives the "no such order" answer
        (see order_status; recognised here whatever confirmed_not_found says, because it follows a refusal), submit
        raises OrderRejected: Webull refused and no order exists. The exception is OPENAPI_TRADE_PLACE_ORDER_REPEAT:
        there Webull says an order with this client order id exists, so "no such order" on the detail read leaves the
        outcome unknown (read the order's status before anything else), never OrderRejected. If the detail call fails or answers anything else,
        and on every other failure of the order call (no answer, a 5xx, a transport error, an unreadable status), it
        raises a plain WebullError of UNKNOWN OUTCOME: the order may or may not have reached Webull, so read
        order_status(tag) before retrying (the same tag gives the same client order id). A caller using the order
        ledger passes not_sent=(NotTransmitted, OrderRejected).

        Returns only values this module built or validated: {status "accepted", client_order_id, venue_order_id (the
        documented order_id when it is a plain id holding no run of a secret, else None), environment, side,
        quantity}. Webull's answer itself is never handed back."""
        if self._events:
            return self._submit_event(symbol, units, tag, limit_price)
        if isinstance(symbol, str) and str.find(symbol, ":") >= 0:
            raise NotTransmitted("a name holding a colon is an event contract's (TICKER:yes or TICKER:no); event orders are sent "
                                 "only by a venue built for the EVENTS_CASH class, never by this one")
        secrets = self._secrets()
        try:
            crypto = _is_crypto(symbol)
            amount, _ = _quantity(units, crypto)                          # a malformed quantity is refused first
            _session(session, crypto, limit_price)
            account = self.account_id
            self._fits(crypto)
            secrets = self._secrets()
            read_at = self._check_sale(symbol, amount, crypto) if amount < 0 else None    # the holding must be established
            reference = limit_price if limit_price is not None else price
            self._guard_live(abs(amount) * float(reference) if reference else None)
            order = order_payload(symbol, units, tag, limit_price, session)
            placed_at = self._pacer.wait("place")                         # the place slot, taken before the call
            if read_at is not None and placed_at - read_at > SALE_READ_MAX_AGE:
                raise NotTransmitted(f"the holdings were read {placed_at - read_at:.0f} seconds before the order call could be made; not sent")
        except NotTransmitted:                                            # raised here, or rebuilt through the gate by _call
            raise
        except WebullError as exc:
            raise NotTransmitted(f"not sent: {_plain(exc)}") from None    # built here from gated parts
        except Exception as exc:                                          # a local fault, e.g. an unreadable price
            raise NotTransmitted(f"not sent: {_class_name(exc, secrets)}: {_gate(exc, secrets)}") from None
        return self._place(account, order, secrets)

    def _submit_event(self, symbol: Any, units: Any, tag: str, limit_price: Any) -> dict:
        """submit in an Events venue. Everything that can refuse runs before the single place_order call and raises
        NotTransmitted: first the test environment (_event_environment, before anything is read: production refuses
        whatever config/webull_live.json says), then the order itself (event_order_payload: the name, whole contracts,
        the limit), then the account (its class must be EVENTS_CASH), then for a sale the long-only check per ticker and
        outcome (_check_event_sale), then the place slot. The session and the reference price play no part. After the
        call, as for stocks (_place), with the row read after a refusal checked against the name sent. The name is
        remembered under the client order id before the call, for order_status."""
        secrets = self._secrets()
        try:
            self._event_environment()
            order = event_order_payload(symbol, units, tag, limit_price)
            account = self.account_id
            cls = self.account_class
            if cls != EVENTS_CLASS:
                raise NotTransmitted(f"an event order does not fit this account (class {cls or 'unknown'}): it trades only in "
                                     f"{EVENTS_CLASS}")
            secrets = self._secrets()
            read_at = self._check_event_sale(symbol, int(order["quantity"])) if order["side"] == "SELL" else None
            placed_at = self._pacer.wait("place")
            if read_at is not None and placed_at - read_at > SALE_READ_MAX_AGE:
                raise NotTransmitted(f"the holdings were read {placed_at - read_at:.0f} seconds before the order call could be made; not sent")
        except NotTransmitted:
            raise
        except WebullError as exc:
            raise NotTransmitted(f"not sent: {_plain(exc)}") from None
        except Exception as exc:
            raise NotTransmitted(f"not sent: {_class_name(exc, secrets)}: {_gate(exc, secrets)}") from None
        self._sent_events[order["client_order_id"]] = symbol
        return self._place(account, order, secrets, expected=symbol)

    def _event_environment(self) -> None:
        """LiveTradingNotConfirmed unless this venue is plainly the test environment: `environment` a plain str equal to
        "sandbox" by a static lookup (inspect.getattr_static: a property or a class-level answer is refused) and by an
        ordinary one, and `host` by both lookups a plain str with "sandbox" as a whole dot-separated label. Reads no
        file and calls nothing: no confirmation file changes it."""
        found = []
        for name in ("environment", "host"):
            try:
                static = inspect.getattr_static(self, name, None)
            except Exception:
                static = None
            try:
                ordinary = getattr(self, name, None)
            except Exception:
                ordinary = None
            found.append((static, ordinary))
        env_ok = all(type(e) is str and e == TEST_ENVIRONMENT for e in found[0])
        host_ok = all(_plain_sandbox_host(h) for h in found[1])
        if not (env_ok and host_ok):
            raise LiveTradingNotConfirmed("event orders are sent only in Webull's test environment (sandbox), and this venue is "
                                          "not plainly that; no confirmation file changes this")

    def _check_event_sale(self, name: str, contracts: int) -> float:
        """The long-only check for a sale of event contracts, as _check_sale does it for stocks: wait until open orders,
        holdings and the order call could all be made at once, read open orders then holdings, allow the sale only
        when `contracts` is not above the contracts held of exactly this ticker and outcome less the unfilled part of
        its open sales (rounded DOWN to whole contracts), and re-read when more than SALE_READ_MAX_AGE seconds would
        pass before the order call, at most SALE_REREADS times; then NotTransmitted. Returns when the holdings read was
        made."""
        for _ in range(1 + SALE_REREADS):
            self._pacer.wait_free({"open_orders": 1, "positions": 1, "place": 1})
            reserved = self._reserved_by_open_event_sells(name)
            held = self._event_held(name)
            read_at = self._pacer.last("positions")
            free = (held - reserved).to_integral_value(rounding=ROUND_FLOOR)
            if Decimal(contracts) > free:
                raise NotTransmitted(f"selling {contracts} {name} with {float(held):g} held and {float(reserved):g} in open sales "
                                     "would open a short; this venue is long-only")
            if read_at is not None and self._pacer.wait_free({"place": 1}) - read_at <= SALE_READ_MAX_AGE:
                return read_at
        raise NotTransmitted(f"the holdings could not be read within {SALE_READ_MAX_AGE:g} seconds of the order call after "
                             f"{SALE_REREADS} re-reads; not sent")

    def _place(self, account: str, order: dict, secrets: _Secrets, expected: str | None = None) -> dict:
        """The single place_order call and what follows it (see submit). `expected`: the event name sent, for the
        detail read after a refusal (None for stocks and crypto)."""
        detail, refused = None, None
        try:
            res = self._client.order_v3.place_order(account, [order])   # the only order call; its slot is already taken
        except NotTransmitted as exc:                                     # raised from inside the call: still unknown
            detail = "the order call began and then failed: " + _gate(exc, secrets)
        except WebullError as exc:
            detail = "the client failed: " + _gate(exc, secrets)
        except Exception as exc:
            detail = "Webull call failed: " + _safe(exc, secrets)
            refused = _refusal(exc)
        if refused is not None:                                           # Webull answered 4xx: one detail read decides
            found, reason = self._after_refusal(order["client_order_id"], secrets, expected)
            if found is not None:
                return {"status": "accepted", "client_order_id": order["client_order_id"], "venue_order_id": found["venue_order_id"],
                        "environment": self.environment, "side": order["side"], "quantity": order["quantity"]}
            if reason is None and type(refused[0]) is str and refused[0] == REPEAT_CODE:
                reason = ("Webull says an order with this client order id already exists (OPENAPI_TRADE_PLACE_ORDER_REPEAT), "
                          "but its detail reads as no such order")
            if reason is None:
                code = _code(refused[0], secrets)
                code = code if code != CODE_WITHHELD and self._final(code) == code else None
                raise OrderRejected("Webull refused the order and no order exists under its client order id: " + self._final(detail), code)
            detail += "; " + reason
        if detail is None:
            try:                                                          # everything after the call, in one guarded block
                status = _status_code(res)
                if status is None:
                    detail = "the answer to the order call carries no readable status"
                elif status != 200:
                    try:
                        _body(res, secrets)                               # raises, naming the status and the gated error
                        detail = f"Webull answered {status}"
                    except WebullError as exc:
                        detail = _plain(exc)
            except Exception as exc:
                detail = f"Webull's answer could not be read ({_class_name(exc, secrets)})"
        if detail is not None:
            raise WebullError(OUTCOME_UNKNOWN + self._final(detail))     # outside every except block
        # Webull answered 200: the order was placed. Reading the answer can no longer turn it into an error.
        try:
            venue_order_id = self._venue_order_id(_json(res), order["client_order_id"], secrets, self._final)
        except Exception:
            venue_order_id = None
        return {"status": "accepted", "client_order_id": order["client_order_id"], "venue_order_id": venue_order_id,
                "environment": self.environment, "side": order["side"], "quantity": order["quantity"]}

    def _check_sale(self, symbol: str, amount: float, crypto: bool) -> float:
        """The long-only check for a sale, read fresh: wait until open orders, holdings and the order call could all be
        made at once, read open orders then holdings, check, and wait for the place slot. If more than
        SALE_READ_MAX_AGE seconds have then passed since the holdings read began, read and check again, at most
        SALE_REREADS times; then NotTransmitted. Returns when the holdings read was made."""
        step = Decimal(1).scaleb(-QUANTITY_STEP_DECIMALS["crypto" if crypto else "equity"])
        for _ in range(1 + SALE_REREADS):
            self._pacer.wait_free({"open_orders": 1, "positions": 1, "place": 1})
            reserved = self._reserved_by_open_sells(symbol)               # open sells first, then the holding
            held = self._held(symbol)
            read_at = self._pacer.last("positions")
            free = (held - reserved).quantize(step, rounding=ROUND_FLOOR)
            if _decimal(abs(amount)) > free:
                raise NotTransmitted(f"selling {abs(amount):g} {symbol} with {float(held):g} held and {float(reserved):g} in open sells "
                                     "would open a short; this venue is long-only")
            if read_at is not None and self._pacer.wait_free({"place": 1}) - read_at <= SALE_READ_MAX_AGE:
                return read_at
        raise NotTransmitted(f"the holdings could not be read within {SALE_READ_MAX_AGE:g} seconds of the order call after "
                             f"{SALE_REREADS} re-reads; not sent")

    def _after_refusal(self, cid: str, secrets: _Secrets, expected: str | None = None) -> tuple[dict | None, str | None]:
        """One read of the order's detail after Webull refused the order call: (the order as order_status reads it,
        None) when it exists; (None, None) on the "no such order" answer; (None, why) when the read fails or answers
        anything else (the outcome stays unknown). `expected`: the event name sent, in an Events venue."""
        reason = None
        try:
            found = self._read_order(cid, recognise_not_found=True, expected=expected)
        except WebullError as exc:
            found, reason = None, "reading the order's detail after the refusal failed: " + _plain(exc)
        except Exception as exc:
            found, reason = None, f"reading the order's detail after the refusal failed ({_class_name(exc, secrets)})"
        if found is None and reason is None:
            return None, None
        if found is not None and not isinstance(found, dict):
            return None, "the order's detail after the refusal could not be read"
        return found, reason

    @staticmethod
    def _venue_order_id(body: Any, cid: str, secrets: _Secrets, final=None) -> str | None:
        """`order_id` of the place-order answer (docs common-order-place); inside an order list, the item with our id.
        Kept only as a plain id (see _plain_id)."""
        if isinstance(body, dict) and body.get("order_id"):
            return _plain_id(body["order_id"], secrets, final)
        items = _order_items(body) or []
        mine = [o for o in items if o.get("client_order_id") == cid and o.get("order_id")]
        return _plain_id(mine[0]["order_id"], secrets, final) if mine else None

    @_public
    def cancel(self, tag: str) -> dict:
        """Ask Webull to cancel the order sent under `tag`. Returns {"status": ...}, one of: "cancel requested" (Webull
        answered 200); "not yet" (OPENAPI_ORDER_CAN_NOT_BE_CANCEL_FOR_PENDING_SUBMIT: the order is still being
        submitted; try again shortly); "already finished" (OPENAPI_ORDER_CAN_NOT_BE_CANCEL); "no such order" (the
        answer order_status reads as no such order). None of those raises; any other failure raises WebullError with
        the gated text. Cancel is never retried here (a 429 included). Webull's answer itself is not handed back."""
        status, error = self._cancel_one(self.account_id, client_order_id(tag))
        if status == "error":
            raise WebullError(error)
        return {"status": status}

    def _cancel_one(self, account: str, cid: str) -> tuple[str, str | None]:
        """(one of the cancel words, None) or ("error", the gated text) for one cancel_order call."""
        secrets = self._secrets()
        word, error = None, None
        try:
            res = self._paced("cancel", self._client.order_v3.cancel_order, account, cid)
        except WebullError as exc:
            error = _plain(_rebuilt(exc, secrets))
        except Exception as exc:
            word = _cancel_word(_type_name(exc), _attr(exc, "error_code"), _attr(exc, "error_msg"), _attr(exc, "http_status"))
            error = None if word else "Webull call failed: " + _safe(exc, secrets)
        else:
            status = _status_code(res)
            err = _json(res) if status != 200 else None
            if isinstance(err, dict):
                word = _cancel_word("answer", err.get("error_code"), err.get("message"), status)
            if word is None:
                try:
                    _body(res, secrets)
                    word = "cancel requested"
                except WebullError as exc:
                    error = _plain(exc)
        if word is not None:
            return word, None
        return "error", self._final(error or "the cancel failed")

    @_public
    def cancel_all_open(self) -> list[dict]:
        """Cancel every order open_orders() returns, one cancel_order call each by client order id. One failure does
        not stop the others: each entry is {client_order_id, venue_order_id, symbol, status} with status one of
        cancel's words ("cancel requested", "not yet", "already finished", "no such order") or "error", with the gated
        error under "error". Cancelling only reduces exposure, so it is allowed in production without the written
        live confirmation. If open_orders() itself fails, this raises and nothing is cancelled. Webull cancels only
        orders placed through the API (docs common-order-cancel)."""
        account = self.account_id
        out = []
        for o in self.open_orders():
            entry = {"client_order_id": o["client_order_id"], "venue_order_id": o["venue_order_id"], "symbol": o["symbol"]}
            if not o["client_order_id"]:
                out.append({**entry, "status": "error", "error": "no plain client order id; Webull cancels only orders placed through the API"})
                continue
            status, error = self._cancel_one(account, o["client_order_id"])
            out.append({**entry, "status": status, **({"error": error} if status == "error" else {})})
        return out

    # ---- what happened to orders (reading: allowed in both environments)
    @_public
    def order_status(self, tag: str, expected: str | None = None) -> dict | None:
        """In an Events venue, `expected` is the event name the order was sent under (else the name this venue object
        sent under the tag is used; with neither, WebullError before any call). The row read back must be of type
        EVENT and carry exactly that ticker and outcome; another ticker or outcome raises WebullError: it is never read
        as a fill. There `fee` is the one fee entry's (see _event_fee: None until something has filled), and the answer
        also carries `symbol` (the event name) and `fee_type`. In any other venue `expected` plays no part.

        What Webull says about the order sent under `tag`: {state, filled_units, avg_price, fee, venue_order_id,
        raw_status}, or None only when the venue was built with confirmed_not_found=True and Webull gives one of the
        answers read as "no such order":
        (a) the answer observed in the test environment (2026-11-13) on the cash and events accounts: the SDK's
            ServerException (by class name) with a 4xx status, code exactly OPENAPI_PARAM_ERR and a message holding
            "order not present" in any case (the raw message is read only for this comparison). Any other
            OPENAPI_PARAM_ERR raises;
        (b) the answer observed on the crypto account: a 200 in the documented combo shape whose `orders` is an
            explicitly empty list and whose keys are all among client_order_id, combo_order_id, combo_type and orders
            (with any client_order_id ours);
        (c) the exact error code ORDER_NOT_FOUND with a 4xx status, raised by the SDK's ServerException or in a non-2xx
            answer's body (never seen there; kept from the older Webull HK table).
        With the default confirmed_not_found=False they raise WebullError saying what they mean is not confirmed for
        reading as "never arrived" (see the constructor). Any other failure (an unreachable venue, a malformed answer
        such as {"orders": ["junk"]} or a bare []) raises WebullError: it never looks like "no such order".

        `state` comes from ORDER_STATES; `filled_units` is unsigned; a number that is absent or unreadable is None,
        never 0 (filled_units is 0.0 only when Webull says 0); avg_price is None when Webull gives 0 (nothing filled);
        `fee` is the actual commission plus the actual fees; venue_order_id is a plain id or None; raw_status is
        Webull's status in the code shape, else WITHHELD.

        A combo that carries a client_order_id other than ours raises WebullError, whatever it holds. A single
        item is read as ours without its own client_order_id only when that key is absent or None (not "") and its
        combo names our id or names none."""
        cid = client_order_id(tag)
        if not self._events:
            return self._read_order(cid, self._confirmed_not_found)
        return self._read_order(cid, self._confirmed_not_found, expected=self._expected_name(cid, expected))

    def _read_order(self, cid: str, recognise_not_found: bool, expected: str | None = None) -> dict | None:
        if self._events and parse_event_name(expected) is None:
            raise WebullError("an event order is read only against the event name it was sent under")
        account = self.account_id
        secrets = self._secrets()
        try:
            res = self._paced("order_detail", self._client.order_v3.get_order_detail, account, cid)
        except RateLimitHit:
            raise
        except WebullError as exc:
            raise _rebuilt(exc, secrets) from None
        except Exception as exc:
            if _not_present(_type_name(exc), _attr(exc, "error_code"), _attr(exc, "error_msg"), _attr(exc, "http_status")):
                return self._no_such_order("a no-such-order error code", recognise_not_found)
            raise WebullError("Webull call failed: " + _safe(exc, secrets)) from None
        status = _status_code(res)
        if status != 200:
            err = _json(res)
            if isinstance(err, dict) and _not_found_code("answer", err.get("error_code"), status):
                return self._no_such_order("the error code ORDER_NOT_FOUND", recognise_not_found)
        body = _body(res, secrets)
        if (isinstance(body, dict) and isinstance(body.get("orders"), list) and not body["orders"]
                and body.keys() <= EMPTY_ANSWER_KEYS and body.get("client_order_id", cid) == cid):
            return self._no_such_order("an empty order list", recognise_not_found)
        combos = body if isinstance(body, list) else [body]
        if any(isinstance(c, dict) and "orders" in c and "client_order_id" in c and c["client_order_id"] != cid for c in combos):
            raise WebullError("Webull's order detail names another client order id; cannot tell what happened to this order")
        items = _order_items(body)
        if not items:                                                     # None (malformed) or a bare empty list
            raise WebullError(f"Webull's order detail has an unexpected shape (a {type(body).__name__}); cannot tell what happened")
        mine = [o for o in items if o.get("client_order_id") == cid]
        if not mine and len(items) == 1 and items[0].get("client_order_id") is None:     # absent or None, never ""
            mine = items
        if len(mine) != 1:
            raise WebullError(f"Webull's order detail holds {len(items)} orders and {len(mine)} carry this client order id")
        item = mine[0]
        price = _number(item.get("filled_price"))
        out = {"state": _state(item.get("status")), "filled_units": _filled_units(item), "avg_price": price if price and price > 0 else None,
               "fee": _fee(item), "venue_order_id": _plain_id(item.get("order_id"), secrets, self._final) if item.get("order_id") else None,
               "raw_status": _status_word(item.get("status"), secrets, self._final)}
        if not self._events:
            return out
        kind = item.get("instrument_type")
        if not (type(kind) is str and kind == EVENT_INSTRUMENT):
            raise WebullError("Webull's order detail for an event order is not of the type EVENT; it is not read as a fill")
        if self._event_row_name(item, "order detail") != expected:
            raise WebullError("Webull's order detail for this client order id names another ticker or outcome than was sent; "
                              "it is not read as a fill")
        out["fee"], out["fee_type"] = self._event_fee(item, out["filled_units"], secrets)
        out["symbol"] = expected
        return out

    @staticmethod
    def _no_such_order(answer: str, recognise: bool) -> None:
        if recognise:
            return None
        raise WebullError(f"Webull shows no order under this client order id ({answer}); reading that as \"never arrived\" is not yet "
                          "confirmed, so the outcome stays unknown (see confirmed_not_found)")

    @_public
    def open_orders(self) -> list[dict]:
        """Every open order of the account, following pagination_key to the last page (docs order-open: an absent
        key marks the last page). Each is {client_order_id and venue_order_id (plain ids, else None), symbol (the
        trader's naming; in an Events venue an EVENT row is named TICKER:outcome, see _event_row_name), instrument_type (as given if a short word, None when absent, else WITHHELD), units (signed:
        sells and shorts negative; None if unreadable or the side is not BUY, SELL or SHORT), state, filled_units,
        raw_status (the code shape, else WITHHELD)}. A malformed page (an entry that is not an object, a bare empty
        list, an order whose symbol is missing or not a clean symbol) or a pagination key that is not a non-empty
        string raises WebullError."""
        account = self.account_id
        secrets = self._secrets()
        out: list[dict] = []
        key, seen, pages = None, set(), 0
        while True:
            body = self._call("open_orders", self._client.order_v3.list_order_open, account) if key is None else self._call("open_orders", self._client.order_v3.list_order_open, account, key)
            pages += 1
            rows = body.get("data") if isinstance(body, dict) else body      # a body without a `data` list is not "none open"
            items = _order_items(rows) if isinstance(rows, list) and (rows or isinstance(body, dict)) else None
            if items is None:                                             # a bare top-level [] is malformed too
                raise WebullError("Webull's open-order list has an unexpected shape")
            for item in items:
                kind = item.get("instrument_type")
                if self._events and type(kind) is str and kind == EVENT_INSTRUMENT:      # an Events venue names the outcome
                    sym = self._event_row_name(item, "open-order list")
                else:
                    sym = _trader_symbol(self._symbol(item.get("symbol"), "open-order list"), kind)
                qty, sign = _number(item.get("total_quantity")), _side_sign(item.get("side"))
                out.append({"client_order_id": _plain_id(item.get("client_order_id"), secrets, self._final),
                            "venue_order_id": _plain_id(item.get("order_id"), secrets, self._final) if item.get("order_id") else None,
                            "symbol": sym, "instrument_type": _kind_word(kind, secrets, self._final),
                            "units": None if qty is None or sign is None else sign * abs(qty), "state": _state(item.get("status")),
                            "filled_units": _filled_units(item), "raw_status": _status_word(item.get("status"), secrets, self._final)})
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

    @_public
    def cash(self) -> dict:
        """{cash, settled_cash, buying_power, currency} from balance() (docs account-balance). `cash` is
        total_cash_balance, else the currency entry's cash_balance; settled_cash and buying_power come from the
        account_currency_assets entry in total_asset_currency, and are None when there is no entry in that currency
        (another currency's figures are never labelled with it). When total_asset_currency is absent, empty or not a
        three-letter code, the totals carry no currency, so total_cash_balance is not used: everything comes from the
        single entry, `cash` from its cash_balance and `currency` from its own currency (all None without one such
        entry). A field that is absent or unreadable is None; more than one entry in the wanted currency raises
        WebullError.

        In an Events venue (whose balance answer has buying power and no cash line, observed 2026-11-13) `cash` is the
        buying power, chosen as above, and settled_cash is None: not given."""
        out = self._cash_fields()
        if self._events:
            return {"cash": out["buying_power"], "settled_cash": None, "buying_power": out["buying_power"], "currency": out["currency"]}
        return out

    def _cash_fields(self) -> dict:
        body = self.balance()
        assets = body["account_currency_assets"]
        currency = body.get("total_asset_currency")
        if currency:                                                      # Webull attached this currency to the totals
            matches = [a for a in assets if a.get("currency", "").upper() == currency.upper()]
            if len(matches) > 1:
                raise WebullError(f"Webull's balance has more than one account_currency_assets entry in {currency}; cannot tell which holds the cash")
            entry = matches[0] if matches else {}
            cash = body.get("total_cash_balance")
            cash = cash if cash is not None else entry.get("cash_balance")
        else:                                                             # no currency on the totals: only the single entry
            entry = assets[0] if len(assets) == 1 else {}
            currency = entry.get("currency")
            if currency is None:
                return {"cash": None, "settled_cash": None, "buying_power": None, "currency": None}
            cash = entry.get("cash_balance")
        return {"cash": cash, "settled_cash": entry.get("settled_cash"), "buying_power": entry.get("buying_power"), "currency": currency}

    # ---- a read-only readiness report
    @_public
    def check(self) -> dict:
        """A readiness report holding only values this module built: counts, field names it chose, the last four
        characters of each plain account id with its short type, class and label and whether this venue would use
        it (and why none, if it cannot choose), the limit table the pacer uses, and errors (already gated)."""
        accounts = self.accounts()
        try:
            used, choice_error = self.account_id, None
        except WebullError as exc:
            used, choice_error = None, self._final(_plain(exc, 10 ** 6))
        report = {"environment": self.environment, "host": self.host,
                  "accounts": [{"account_id_ending": (a["account_id"] or "")[-4:], "type": a["account_type"], "class": a["account_class"],
                                "label": a["account_label"], "used": used is not None and a["account_id"] == used} for a in accounts],
                  "rate_limits": {"share_used": RATE_SHARE, "calls": {k: [n, s] for k, (n, s) in self._limits.items()}}}
        if choice_error is not None:
            report["account_choice"] = choice_error
        try:
            bal = self.balance()
            report["balance_fields"] = sorted(bal)
            holdings, others, duplicated = self._read_positions()
            report["positions"] = len(self._flatten(holdings, duplicated))
            report["other_positions"] = len(others)
        except WebullError as exc:
            report["balance"] = self._final(_plain(exc, 10 ** 6))
        try:
            report["open_orders"] = len(self.open_orders())
        except WebullError as exc:
            report["open_orders_error"] = self._final(_plain(exc, 10 ** 6))
        confirmation = load_live_confirmation(self._confirmation_file)
        report["real_money_orders"] = ("off: event orders are sent in the test environment only" if self._events else
                                       "off: test environment" if self.environment != "production" else
                                       "off: no written confirmation" if confirmation is None else
                                       f"on for the account ending {confirmation['account_id'][-4:]}, up to {confirmation['max_order_value']:,.0f} per order")
        return report


def main(argv: list[str] | None = None) -> int:
    """Prints only the check report (values this module built) or a failure through the gate and the last check."""
    ap = argparse.ArgumentParser(prog="python -m valedia_trader.venues.webull", description="Webull connection check (read-only).")
    ap.add_argument("cmd", choices=["check"])
    args = ap.parse_args(argv)
    venue = None
    try:
        venue = WebullVenue.from_secrets()
        if args.cmd == "check":
            print(venue._final(json.dumps(venue.check(), indent=2, default=str)))
    except Exception as exc:
        secrets = venue._secrets() if isinstance(venue, WebullVenue) else NO_SECRETS
        name = _class_name(exc, secrets)
        if not isinstance(venue, WebullVenue) and str(getattr(type(exc), "__module__", "")).split(".")[0] not in ("builtins", "valedia_trader"):
            name = WITHHELD                                               # no venue, so no secrets to check a foreign name against
        text = f"not ready: {name}: {_gate(exc, secrets, 400)}"
        print(venue._final(text) if isinstance(venue, WebullVenue) else _last_check(text, ()), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
