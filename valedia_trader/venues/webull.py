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
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

SANDBOX_HOST = "api.sandbox.webull.com"
PRODUCTION_HOST = "api.webull.com"
HOSTS = {"sandbox": SANDBOX_HOST, "production": PRODUCTION_HOST}
REPO = Path(__file__).resolve().parents[2]
LIVE_CONFIRMATION = REPO / "config" / "webull_live.json"

__all__ = ["WebullVenue", "WebullError", "LiveTradingNotConfirmed", "order_payload", "client_order_id", "load_live_confirmation"]


class WebullError(RuntimeError):
    pass


class LiveTradingNotConfirmed(WebullError):
    """A production order was attempted without the owner's written confirmation."""


def client_order_id(tag: str) -> str:
    """Webull wants an id of at most 32 characters, unique per account. The same tag gives the same id,
    so a retried order cannot be placed twice."""
    return hashlib.sha256(str(tag).encode()).hexdigest()[:32]


def order_payload(symbol: str, units: float, tag: str, limit_price: float | None = None) -> dict:
    """One order in Webull's format. `units` is signed: positive buys, negative sells what is held."""
    if not units:
        raise WebullError("an order needs a non-zero quantity")
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


def _body(res: Any) -> Any:
    """The JSON body of an SDK response, or an error that names the status but never the credentials."""
    status = getattr(res, "status_code", None)
    if status != 200:
        text = str(getattr(res, "text", ""))[:200]
        raise WebullError(f"Webull answered {status}: {text}")
    return res.json()


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
        """Place one order. `price` is the last known price, used only to value the order for the live cap."""
        if units < 0:
            held = self.positions().get(symbol.upper(), 0.0)
            if abs(units) > held + 1e-9:
                raise WebullError(f"selling {abs(units):g} {symbol} with {held:g} held would open a short; this venue is long-only")
        reference = limit_price if limit_price is not None else price
        self._guard_live(abs(units) * float(reference) if reference else None)
        order = order_payload(symbol, units, tag, limit_price)
        body = _call(self._client.order_v3.place_order, self.account_id, [order])
        return {"status": "accepted", "client_order_id": order["client_order_id"], "environment": self.environment,
                "side": order["side"], "quantity": order["quantity"], "response": body if isinstance(body, (dict, list)) else str(body)[:200]}

    def cancel(self, tag: str) -> dict:
        return {"status": "cancel requested", "response": _call(self._client.order_v3.cancel_order, self.account_id, client_order_id(tag))}

    # ---- a read-only readiness report
    def check(self) -> dict:
        accounts = self.accounts()
        report = {"environment": self.environment, "host": self.host,
                  "accounts": [{"account_id_ending": str(a.get("account_id", ""))[-4:], "type": a.get("account_type")} for a in accounts]}
        try:
            bal = self.balance()
            report["balance_fields"] = sorted(bal)[:20] if isinstance(bal, dict) else type(bal).__name__
            report["positions"] = len(self.positions())
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
