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
  develop/error, which now redirects) listed ORDER_NOT_FOUND as "the order does not exist". So "no such order"
  is read from exactly two answers: a 200 whose order list is empty, or the error code ORDER_NOT_FOUND (on an
  SDK ServerException, SDK core/exception/exceptions.py:31-37, or on a non-200 body). Everything else, a bare
  404 included, is a failure. Check this against the test environment before relying on it.
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
* Errors: the SDK raises ServerException(error_code, error_msg, http_status, request_id) for any answer outside
  200-299 (SDK core/client.py:357-359 and 424-428) and ClientException(error_code, error_msg) for a transport
  failure (SDK core/client.py:328). The SDK logs the whole request with its headers before raising
  (core/client.py:360-362), which is why its logger is silenced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
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
# The one answer read as "no such order" besides an empty order list. Not confirmed for the US API: see the module notes.
ORDER_NOT_FOUND_CODES = frozenset({"ORDER_NOT_FOUND"})


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


def order_payload(symbol: str, units: float, tag: str, limit_price: float | None = None) -> dict:
    """One order in Webull's format. `units` is signed: positive buys, negative sells what is held."""
    if not units:
        raise NotTransmitted("an order needs a non-zero quantity")
    crypto = symbol.upper().endswith("-USD")
    order = {"combo_type": "NORMAL", "client_order_id": client_order_id(tag), "market": "US",
             "symbol": symbol.upper().replace("-", "") if crypto else symbol.upper(),
             "instrument_type": "CRYPTO" if crypto else "EQUITY",
             "order_type": "LIMIT" if limit_price is not None else "MARKET",
             "quantity": f"{abs(float(units)):.8f}".rstrip("0").rstrip(".") if crypto else f"{abs(float(units)):.5f}".rstrip("0").rstrip("."),
             "side": "BUY" if units > 0 else "SELL", "time_in_force": "GTC" if crypto else "DAY", "entrust_type": "QTY"}
    if limit_price is not None:
        order["limit_price"] = f"{float(limit_price):.2f}"
    if not crypto:
        order["support_trading_session"] = "CORE"
    return order


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


def _quiet_sdk_logging() -> None:
    """The SDK logs whole requests, headers included, when a call fails. Keep the App Key out of every log."""
    for name in ("webull", "webull.core.client"):
        log = logging.getLogger(name)
        log.setLevel(logging.CRITICAL + 1)
        log.propagate = False


def _safe(exc: BaseException) -> str:
    """A short description of an SDK failure built from its status fields, never from the request it carried."""
    parts = [str(getattr(exc, k)) for k in ("http_status", "error_code") if getattr(exc, k, None) is not None]
    msg = str(getattr(exc, "error_msg", "") or "")[:160]
    return f"{type(exc).__name__}" + (f" ({', '.join(parts)})" if parts else "") + (f": {msg}" if msg else "")


def _call(fn, *args) -> Any:
    """Run one SDK call. The SDK raises on some failures and returns a non-200 response on others."""
    try:
        res = fn(*args)
    except WebullError:
        raise
    except Exception as exc:
        raise WebullError("Webull call failed: " + _safe(exc)) from None
    return _body(res)


def _json(res: Any) -> Any:
    try:
        return res.json()
    except Exception:
        return None


def _body(res: Any) -> Any:
    """The JSON body of an SDK response, or an error that names the status but never the credentials.

    The error is built from the documented error fields (error_code, message) only, never from the raw response
    text, which is not under this module's control."""
    status = getattr(res, "status_code", None)
    if status != 200:
        err = _json(res)
        parts = [str(err.get(k))[:120] for k in ("error_code", "message") if isinstance(err, dict) and err.get(k) is not None]
        raise WebullError(f"Webull answered {status}" + (f": {', '.join(parts)}" if parts else ""))
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
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _not_found_code(code: Any) -> bool:
    return str(code or "").strip().upper() in ORDER_NOT_FOUND_CODES


def _trader_symbol(symbol: Any, instrument_type: Any) -> str:
    """Webull's symbol under the trader's own name: crypto BTCUSD comes back as BTC-USD (as in positions())."""
    sym = str(symbol or "").upper()
    if str(instrument_type or "").upper() == "CRYPTO" and sym.endswith("USD") and "-" not in sym:
        sym = sym[:-3] + "-USD"
    return sym


def _order_items(body: Any) -> list[dict] | None:
    """Every order item in a documented combo {orders: [...]}, a plain order, or a list of either.
    None when the body has none of those shapes."""
    if isinstance(body, list):
        found = [_order_items(b) for b in body]
        if any(f is None for f in found):
            return None
        return [item for f in found for item in f]
    if not isinstance(body, dict):
        return None
    if isinstance(body.get("orders"), list):
        return [o for o in body["orders"] if isinstance(o, dict)]
    if "status" in body:
        return [body]
    return None


def _fee(item: dict) -> float | None:
    """commission.actual_commission plus every fees[].actual_value; None if none is given or one is unreadable."""
    values = []
    commission = item.get("commission")
    if isinstance(commission, dict) and "actual_commission" in commission:
        values.append(commission["actual_commission"])
    if isinstance(item.get("fees"), list):
        values += [f.get("actual_value") for f in item["fees"] if isinstance(f, dict)]
    if not values:
        return None
    numbers = [_number(v) for v in values]
    return None if any(n is None for n in numbers) else sum(numbers)


def _state(raw: Any) -> str:
    return ORDER_STATES.get(raw.strip().upper(), "unknown_status") if isinstance(raw, str) else "unknown_status"


def _filled_units(item: dict) -> float | None:
    n = _number(item.get("filled_quantity"))
    return None if n is None else abs(n)


class WebullVenue:
    """Orders and account reads on one Webull account."""

    def __init__(self, app_key: str, app_secret: str, environment: str = "sandbox", account_id: str | None = None,
                 client: Any | None = None, confirmation_file: str | Path = LIVE_CONFIRMATION, region: str = "us") -> None:
        if environment not in HOSTS:
            raise WebullError(f"environment must be one of {sorted(HOSTS)}")
        if not str(app_key).strip() or not str(app_secret).strip():
            raise WebullError("a Webull App Key and App Secret are required")
        self.environment, self.host, self.name = environment, HOSTS[environment], f"webull-{environment}"
        self._confirmation_file = Path(confirmation_file)
        if client is None:
            try:
                from webull.core.client import ApiClient
                from webull.trade.trade_client import TradeClient
            except ImportError as exc:
                raise WebullError("the Webull SDK is not installed: uv pip install 'webull-openapi-python-sdk>=3.0.4'") from exc
            _quiet_sdk_logging()
            try:                                             # the SDK opens a session here: this is a network call
                api = ApiClient(app_key, app_secret, region)
                api.add_endpoint(region, self.host)
                client = TradeClient(api)
            except Exception as exc:
                raise WebullError(f"could not open a Webull session on {self.host}: {_safe(exc)}") from None
        self._client = client
        self._account_id = account_id

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
    def accounts(self) -> list[dict]:
        body = _call(self._client.account_v2.get_account_list)
        return body if isinstance(body, list) else list(body.get("accounts", body.get("data", [])))

    @property
    def account_id(self) -> str:
        if self._account_id is None:
            found = self.accounts()
            if len(found) != 1:
                raise WebullError(f"{len(found)} accounts are visible; set WEBULL_ACCOUNT_ID to choose one")
            self._account_id = str(found[0]["account_id"])
        return self._account_id

    def balance(self) -> dict:
        return _call(self._client.account_v2.get_account_balance, self.account_id)

    def positions(self) -> dict[str, float]:
        """Held quantity by the trader's own symbol names (crypto as BTC-USD)."""
        body = _call(self._client.account_v2.get_account_position, self.account_id)
        rows = body if isinstance(body, list) else list(body.get("positions", body.get("holdings", body.get("data", []))))
        out: dict[str, float] = {}
        for row in rows:
            sym = str(row.get("symbol", "")).upper()
            if str(row.get("instrument_type", "")).upper() == "CRYPTO" and sym.endswith("USD") and "-" not in sym:
                sym = sym[:-3] + "-USD"
            qty = row.get("quantity", row.get("qty", row.get("position", 0)))
            try:
                out[sym] = out.get(sym, 0.0) + float(qty)
            except (TypeError, ValueError):
                continue
        return out

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
        (the same tag gives the same client order id, which Webull keeps unique per account)."""
        try:
            account = self.account_id
            if units < 0:
                held = self.positions().get(symbol.upper(), 0.0)
                if abs(units) > held + 1e-9:
                    raise NotTransmitted(f"selling {abs(units):g} {symbol} with {held:g} held would open a short; this venue is long-only")
            reference = limit_price if limit_price is not None else price
            self._guard_live(abs(units) * float(reference) if reference else None)
            order = order_payload(symbol, units, tag, limit_price)
        except NotTransmitted:
            raise
        except WebullError as exc:
            raise NotTransmitted(f"not sent: {exc}") from None
        except Exception as exc:                                          # a local fault, e.g. an unreadable price
            raise NotTransmitted(f"not sent: {type(exc).__name__}: {str(exc)[:160]}") from None
        body = _call(self._client.order_v3.place_order, account, [order])      # the only order call; nothing refuses after it
        return {"status": "accepted", "client_order_id": order["client_order_id"], "venue_order_id": self._venue_order_id(body, order["client_order_id"]),
                "environment": self.environment, "side": order["side"], "quantity": order["quantity"],
                "response": body if isinstance(body, (dict, list)) else str(body)[:200]}

    @staticmethod
    def _venue_order_id(body: Any, cid: str) -> str | None:
        """`order_id` of the place-order answer (docs common-order-place); inside an order list, the item with our id."""
        if isinstance(body, dict) and body.get("order_id"):
            return str(body["order_id"])
        items = _order_items(body) or []
        mine = [o for o in items if o.get("client_order_id") == cid and o.get("order_id")]
        return str(mine[0]["order_id"]) if mine else None

    def cancel(self, tag: str) -> dict:
        return {"status": "cancel requested", "response": _call(self._client.order_v3.cancel_order, self.account_id, client_order_id(tag))}

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
                out.append({**entry, "status": "cancel requested", "response": _call(self._client.order_v3.cancel_order, account, o["client_order_id"])})
            except WebullError as exc:
                out.append({**entry, "status": "error", "error": str(exc)})
        return out

    # ---- what happened to orders (reading: allowed in both environments)
    def order_status(self, tag: str) -> dict | None:
        """What Webull says about the order sent under `tag`: {state, filled_units, avg_price, fee, venue_order_id,
        raw_status}, or None only when Webull positively answers that no such order exists (an empty order list, or
        the error code ORDER_NOT_FOUND; see the module notes, not confirmed for the US API). Any other failure,
        an unreachable venue included, raises WebullError: it never looks like "no such order".

        `state` comes from ORDER_STATES; `filled_units` is unsigned; a number that is absent or unreadable is None,
        never 0 (filled_units is 0.0 only when Webull says 0); avg_price is None when Webull gives 0 (nothing filled);
        `fee` is the actual commission plus the actual fees."""
        cid = client_order_id(tag)
        account = self.account_id
        try:
            res = self._client.order_v3.get_order_detail(account, cid)
        except WebullError:
            raise
        except Exception as exc:
            if _not_found_code(getattr(exc, "error_code", None)):
                return None
            raise WebullError("Webull call failed: " + _safe(exc)) from None
        if getattr(res, "status_code", None) != 200:
            err = _json(res)
            if isinstance(err, dict) and _not_found_code(err.get("error_code")):
                return None
        body = _body(res)
        items = _order_items(body)
        if items is None:
            raise WebullError(f"Webull's order detail has no order list (a {type(body).__name__}); cannot tell what happened")
        if not items:
            return None
        mine = [o for o in items if o.get("client_order_id") == cid]
        if not mine and len(items) == 1 and not items[0].get("client_order_id"):
            mine = items
        if len(mine) != 1:
            raise WebullError(f"Webull's order detail holds {len(items)} orders and {len(mine)} carry this client order id")
        item = mine[0]
        price = _number(item.get("filled_price"))
        return {"state": _state(item.get("status")), "filled_units": _filled_units(item), "avg_price": price if price else None,
                "fee": _fee(item), "venue_order_id": str(item["order_id"]) if item.get("order_id") else None, "raw_status": item.get("status")}

    def open_orders(self) -> list[dict]:
        """Every open order of the account, following pagination_key to the last page (docs order-open: an absent
        key marks the last page). Each is {client_order_id, venue_order_id, symbol (the trader's naming), units
        (signed: sells negative; None if unreadable), state, filled_units, raw_status}."""
        account = self.account_id
        out: list[dict] = []
        key, seen = None, set()
        while True:
            body = _call(self._client.order_v3.list_order_open, account) if key is None else _call(self._client.order_v3.list_order_open, account, key)
            rows = body.get("data") if isinstance(body, dict) else body      # a body without a `data` list is not "none open"
            items = _order_items(rows) if isinstance(rows, list) else None
            if items is None:
                raise WebullError("Webull's open-order list has an unexpected shape")
            for item in items:
                qty = _number(item.get("total_quantity"))
                sign = -1.0 if str(item.get("side", "")).upper() in ("SELL", "SHORT") else 1.0
                out.append({"client_order_id": item.get("client_order_id"), "venue_order_id": str(item["order_id"]) if item.get("order_id") else None,
                            "symbol": _trader_symbol(item.get("symbol"), item.get("instrument_type")),
                            "units": None if qty is None else sign * abs(qty), "state": _state(item.get("status")),
                            "filled_units": _filled_units(item), "raw_status": item.get("status")})
            key = body.get("pagination_key") if isinstance(body, dict) else None
            if not key:
                return out
            if key in seen:
                raise WebullError("Webull repeated a pagination key; stopping rather than looping")
            seen.add(key)

    def cash(self) -> dict:
        """{cash, settled_cash, buying_power, currency} from the balance call (docs account-balance). `cash` is
        total_cash_balance, else the currency entry's cash_balance; settled_cash and buying_power come from the
        account_currency_assets entry in total_asset_currency (USD, or the only entry, when that is not given).
        A field that is absent or unreadable is None."""
        body = self.balance()
        if not isinstance(body, dict):
            raise WebullError(f"Webull's balance answer is a {type(body).__name__}, not an object")
        currency = body.get("total_asset_currency")
        assets = [a for a in body.get("account_currency_assets") or [] if isinstance(a, dict)]
        wanted = str(currency or "USD").upper()
        entry = next((a for a in assets if str(a.get("currency", "")).upper() == wanted), assets[0] if len(assets) == 1 else {})
        cash = _number(body.get("total_cash_balance"))
        return {"cash": cash if cash is not None else _number(entry.get("cash_balance")), "settled_cash": _number(entry.get("settled_cash")),
                "buying_power": _number(entry.get("buying_power")), "currency": str(currency) if currency else None}

    # ---- a read-only readiness report
    def check(self) -> dict:
        accounts = self.accounts()
        report = {"environment": self.environment, "host": self.host,
                  "accounts": [{"account_id_ending": str(a.get("account_id", ""))[-4:], "type": a.get("account_type")} for a in accounts]}
        try:
            bal = self.balance()
            report["balance_fields"] = sorted(bal)[:20] if isinstance(bal, dict) else type(bal).__name__
            report["positions"] = len(self.positions())
            report["open_orders"] = len(self.open_orders())
        except WebullError as exc:
            report["balance"] = str(exc)
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
