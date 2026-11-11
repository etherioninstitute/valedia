import json
from types import SimpleNamespace

import pytest

from valedia_trader.core.secrets import SecretError
from valedia_trader.venues import webull as wb

KEY, SECRET = "app-key-do-not-print", "app-secret-do-not-print"


class Response:
    def __init__(self, body, status=200, text=""):
        self._body, self.status_code, self.text = body, status, text

    def json(self):
        return self._body


class FakeClient:
    """Stands in for the SDK's TradeClient: records every call, serves a scripted account."""
    def __init__(self, accounts=None, positions=None, order_status=200):
        self.calls, self._order_status = [], order_status
        self._accounts = [{"account_id": "ACC-0001-1234", "account_type": "CASH"}] if accounts is None else accounts
        self._positions = positions or []
        self.account_v2 = SimpleNamespace(get_account_list=lambda: self._log("accounts", Response(self._accounts)),
                                          get_account_balance=lambda a: self._log(("balance", a), Response({"total_cash": "1000", "buying_power": "1000"})),
                                          get_account_position=lambda a: self._log(("positions", a), Response(self._positions)))
        self.order_v3 = SimpleNamespace(place_order=lambda a, orders: self._log(("place", a, orders), Response({"order_id": "o-1"}, self._order_status, "rejected: " + KEY)),
                                        cancel_order=lambda a, cid: self._log(("cancel", a, cid), Response({"ok": True})))

    def _log(self, what, res):
        self.calls.append(what)
        return res

    def placed(self):
        return [c for c in self.calls if isinstance(c, tuple) and c[0] == "place"]


def venue(tmp_path, env="sandbox", **kw):
    client = kw.pop("client", None) or FakeClient(**{k: kw.pop(k) for k in ("accounts", "positions", "order_status") if k in kw})
    return wb.WebullVenue(KEY, SECRET, environment=env, client=client, confirmation_file=tmp_path / "webull_live.json", **kw), client


def test_orders_are_built_in_webulls_format():
    eq = wb.order_payload("spy", 1.5, "trend-SPY-2026-11-11")
    assert eq == {"combo_type": "NORMAL", "client_order_id": wb.client_order_id("trend-SPY-2026-11-11"), "market": "US", "symbol": "SPY",
                  "instrument_type": "EQUITY", "order_type": "MARKET", "quantity": "1.5", "side": "BUY", "time_in_force": "DAY",
                  "entrust_type": "QTY", "support_trading_session": "CORE"}
    crypto = wb.order_payload("BTC-USD", -0.003, "t", limit_price=80000)
    assert (crypto["symbol"], crypto["instrument_type"], crypto["side"], crypto["quantity"]) == ("BTCUSD", "CRYPTO", "SELL", "0.003")
    assert crypto["order_type"] == "LIMIT" and crypto["limit_price"] == "80000.00" and "support_trading_session" not in crypto
    assert len(wb.client_order_id("x" * 500)) == 32 and wb.client_order_id("a") == wb.client_order_id("a") != wb.client_order_id("b")
    with pytest.raises(wb.WebullError):
        wb.order_payload("SPY", 0, "t")


def test_the_test_environment_accepts_orders_and_stays_long_only(tmp_path):
    v, client = venue(tmp_path, positions=[{"symbol": "SPY", "quantity": "2", "instrument_type": "EQUITY"},
                                           {"symbol": "BTCUSD", "quantity": "0.01", "instrument_type": "CRYPTO"}])
    assert v.name == "webull-sandbox" and v.host == "api.sandbox.webull.com"
    out = v.submit("SPY", 1.0, "tag-1")
    assert out["status"] == "accepted" and out["environment"] == "sandbox" and client.placed()[0][1] == "ACC-0001-1234"
    assert v.positions() == {"SPY": 2.0, "BTC-USD": 0.01}                 # crypto comes back under the trader's own name
    assert v.submit("SPY", -2.0, "tag-2")["side"] == "SELL"               # selling what is held
    with pytest.raises(wb.WebullError, match="long-only"):
        v.submit("SPY", -2.5, "tag-3")                                    # more than is held would be a short
    with pytest.raises(wb.WebullError, match="long-only"):
        v.submit("QQQ", -1.0, "tag-4")
    assert len(client.placed()) == 2
    assert v.cancel("tag-1")["status"] == "cancel requested"


def test_production_orders_need_the_owners_written_confirmation(tmp_path):
    v, client = venue(tmp_path, env="production")
    assert v.name == "webull-production" and v.host == "api.webull.com"
    assert v.accounts() and v.balance()["total_cash"] == "1000"          # reading is allowed
    with pytest.raises(wb.LiveTradingNotConfirmed, match="has not written"):
        v.submit("SPY", 1.0, "t", price=500.0)
    conf = tmp_path / "webull_live.json"
    for bad in ("not json", json.dumps({"account_id": "ACC-0001-1234", "max_order_value": 1000}),            # no name, no acknowledgement
                json.dumps({"account_id": "ACC-0001-1234", "confirmed_by": "Burton", "max_order_value": 1000, "i_understand_this_trades_real_money": "yes"}),
                json.dumps({"account_id": "ACC-0001-1234", "confirmed_by": "Burton", "max_order_value": -5, "i_understand_this_trades_real_money": True})):
        conf.write_text(bad)
        with pytest.raises(wb.LiveTradingNotConfirmed):
            v.submit("SPY", 1.0, "t", price=500.0)
    good = {"account_id": "OTHER-ACCOUNT", "confirmed_by": "Burton", "max_order_value": 1000, "i_understand_this_trades_real_money": True}
    conf.write_text(json.dumps(good))
    with pytest.raises(wb.LiveTradingNotConfirmed, match="different account"):
        v.submit("SPY", 1.0, "t", price=500.0)
    conf.write_text(json.dumps({**good, "account_id": "ACC-0001-1234"}))
    with pytest.raises(wb.LiveTradingNotConfirmed, match="above the confirmed cap"):
        v.submit("SPY", 3.0, "t", price=500.0)
    with pytest.raises(wb.LiveTradingNotConfirmed, match="known dollar value"):
        v.submit("SPY", 1.0, "t")                                         # the paper mirror passes no price: it can never trade live
    assert client.placed() == []                                          # nothing reached Webull through any refusal
    assert v.submit("SPY", 1.0, "t", price=500.0)["environment"] == "production" and len(client.placed()) == 1
    assert "on for the account ending 1234" in v.check()["real_money_orders"]


def test_errors_never_carry_the_credentials_and_accounts_must_be_unambiguous(tmp_path):
    v, _ = venue(tmp_path, order_status=403)
    with pytest.raises(wb.WebullError) as exc:
        v.submit("SPY", 1.0, "t")
    assert "403" in str(exc.value) and SECRET not in str(exc.value)
    two, _ = venue(tmp_path, accounts=[{"account_id": "A"}, {"account_id": "B"}])
    with pytest.raises(wb.WebullError, match="WEBULL_ACCOUNT_ID"):
        two.submit("SPY", 1.0, "t")
    chosen, client = venue(tmp_path, accounts=[{"account_id": "A"}, {"account_id": "B"}], account_id="B")
    chosen.submit("SPY", 1.0, "t")
    assert client.placed()[0][1] == "B"
    report = venue(tmp_path)[0].check()
    assert report["accounts"] == [{"account_id_ending": "1234", "type": "CASH"}] and report["real_money_orders"] == "off: test environment"
    assert KEY not in json.dumps(report) and SECRET not in json.dumps(report)
    for bad in ({"environment": "live"}, {}):
        with pytest.raises(wb.WebullError):
            wb.WebullVenue(KEY if bad else "", SECRET, client=FakeClient(), **bad)


def test_settings_come_from_the_secrets_and_default_to_the_test_environment(tmp_path):
    env = {"WEBULL_APP_KEY": KEY, "WEBULL_APP_SECRET": SECRET}
    v = wb.WebullVenue.from_secrets(env, client=FakeClient(), confirmation_file=tmp_path / "x.json")
    assert v.environment == "sandbox"
    v = wb.WebullVenue.from_secrets({**env, "WEBULL_ENV": "production", "WEBULL_ACCOUNT_ID": "ACC-9"}, client=FakeClient(), confirmation_file=tmp_path / "x.json")
    assert v.environment == "production" and v.account_id == "ACC-9"
    with pytest.raises(SecretError):
        wb.WebullVenue.from_secrets({"WEBULL_APP_KEY": KEY}, client=FakeClient())
    assert wb.load_live_confirmation(tmp_path / "missing.json") is None


def test_sdk_failures_are_wrapped_without_the_request_and_its_logger_is_silenced(tmp_path):
    import logging

    class SdkError(Exception):
        http_status, error_code, error_msg = 401, "UNAUTHORIZED", "Invalid credentials"
        def __str__(self): return f"request headers: x-app-key={KEY}"

    client = FakeClient()
    client.account_v2.get_account_list = lambda: (_ for _ in ()).throw(SdkError())
    v = wb.WebullVenue(KEY, SECRET, client=client, confirmation_file=tmp_path / "x.json")
    with pytest.raises(wb.WebullError) as exc:
        v.accounts()
    assert "401" in str(exc.value) and "UNAUTHORIZED" in str(exc.value) and KEY not in str(exc.value)
    assert exc.value.__cause__ is None                                # the original, with its headers, is not chained
    wb._quiet_sdk_logging()
    assert not logging.getLogger("webull.core.client").isEnabledFor(logging.ERROR)
