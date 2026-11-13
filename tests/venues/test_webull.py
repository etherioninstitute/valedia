import io
import json
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from valedia_trader.core.secrets import SecretError
from valedia_trader.venues import webull as wb

KEY, SECRET = "app-key-do-not-print", "app-secret-do-not-print"


@pytest.fixture(autouse=True)
def _home_is_temporary(tmp_path, monkeypatch):
    """Every test runs with HOME in a temporary folder, so a venue built without a client never touches the real one."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


class Response:
    def __init__(self, body, status=200, text=""):
        self._body, self.status_code, self.text = body, status, text

    def json(self):
        return self._body


class ServerException(Exception):
    """Shaped and named like the SDK's ServerException (webull/core/exception/exceptions.py:31-37); its text carries
    the key, as the SDK's own log lines do, so a test can see that it never reaches a message."""
    def __init__(self, error_code, http_status=417, error_msg="business error"):
        self.error_code, self.http_status, self.error_msg = error_code, http_status, error_msg

    def __str__(self):
        return f"request headers: x-app-key={KEY} x-app-secret={SECRET}"


ServerError = ServerException


class FakeClient:
    """Stands in for the SDK's TradeClient: records every call, serves a scripted account.

    `details` maps a client order id to what get_order_detail answers (a Response, or an exception to raise);
    `open_pages` maps a pagination key (None for the first page) to a list_order_open body; `balance` is the
    balance body; `cancel_fail` is a set of client order ids whose cancel raises."""
    def __init__(self, accounts=None, positions=None, order_status=200, place_body=None, details=None, open_pages=None,
                 balance=None, cancel_fail=()):
        self.calls, self._order_status = [], order_status
        self._accounts = [{"account_id": "ACC-0001-1234", "account_type": "CASH"}] if accounts is None else accounts
        self._positions = positions or []
        self._place_body = {"order_id": "o-1"} if place_body is None else place_body
        self._details, self._cancel_fail = details or {}, set(cancel_fail)
        self._open_pages = {None: {"data": []}} if open_pages is None else open_pages
        self._balance = {"total_cash": "1000", "buying_power": "1000"} if balance is None else balance
        self.account_v2 = SimpleNamespace(get_account_list=lambda: self._log("accounts", Response(self._accounts)),
                                          get_account_balance=lambda a: self._log(("balance", a), self._answer(self._balance)),
                                          get_account_position=lambda a: self._log(("positions", a), Response(self._positions)))
        self.order_v3 = SimpleNamespace(place_order=lambda a, orders: self._log(("place", a, orders), Response(self._place_body, self._order_status, "rejected: " + KEY)),
                                        cancel_order=self._cancel, get_order_detail=self._detail, list_order_open=self._open)

    def _log(self, what, res):
        self.calls.append(what)
        return res

    @staticmethod
    def _answer(value):
        if isinstance(value, BaseException):
            raise value
        return value if isinstance(value, Response) else Response(value)

    def _cancel(self, a, cid):
        self.calls.append(("cancel", a, cid))
        if cid in self._cancel_fail:
            raise ServerError("ORDER_NOT_CANCELLABLE", 417, "the order can no longer be cancelled")
        return Response({"client_order_id": cid, "order_id": "v-" + cid[:4]})

    def _detail(self, a, cid):
        self.calls.append(("detail", a, cid))
        if cid not in self._details:
            raise AssertionError("the test did not script this order")
        return self._answer(self._details[cid])

    def _open(self, a, pagination_key=None):
        self.calls.append(("open", a, pagination_key))
        return self._answer(self._open_pages[pagination_key])

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
    assert v.accounts() and v.balance() == {"account_currency_assets": []}   # reading is allowed (only documented fields come back)
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


# ---- T9: refusals that never left this machine, order status, open orders, cash, cancel-all

def order(status, cid=None, **fields):
    """One order item in the documented shape of /trading/orders/get and /trading/orders/open-orders/list."""
    return {"client_order_id": cid or wb.client_order_id("t"), "order_id": "WB-1", "symbol": "SPY", "side": "BUY",
            "status": status, "instrument_type": "EQUITY", "total_quantity": "1", "filled_quantity": "0", **fields}


def detail(*orders, cid=None):
    """The documented order-detail body: a combo wrapper with the orders inside."""
    return {"client_order_id": cid or wb.client_order_id("t"), "combo_order_id": "C-1", "combo_type": "NORMAL", "orders": list(orders)}


def status_venue(tmp_path, answer, tag="t", env="sandbox", **kw):
    client = FakeClient(details={wb.client_order_id(tag): answer})
    return wb.WebullVenue(KEY, SECRET, environment=env, client=client, confirmation_file=tmp_path / "webull_live.json", **kw), client


def test_refusals_made_here_are_marked_not_transmitted_and_nothing_is_sent(tmp_path):
    assert issubclass(wb.NotTransmitted, wb.WebullError) and issubclass(wb.LiveTradingNotConfirmed, wb.NotTransmitted)
    with pytest.raises(wb.NotTransmitted, match="non-zero"):
        wb.order_payload("SPY", 0, "t")
    v, client = venue(tmp_path, positions=[{"symbol": "SPY", "quantity": "1", "instrument_type": "EQUITY"}])
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        v.submit("SPY", -2.0, "short")
    with pytest.raises(wb.NotTransmitted, match="non-zero"):
        v.submit("SPY", 0, "zero")
    live, live_client = venue(tmp_path, env="production")
    with pytest.raises(wb.NotTransmitted, match="has not written"):
        live.submit("SPY", 1.0, "t", price=500.0)
    two, two_client = venue(tmp_path, accounts=[{"account_id": "A"}, {"account_id": "B"}])
    with pytest.raises(wb.NotTransmitted, match="WEBULL_ACCOUNT_ID"):       # cannot choose an account: nothing is sent
        two.submit("SPY", 1.0, "t")
    assert client.placed() == [] and live_client.placed() == [] and two_client.placed() == []


def test_a_failure_after_the_order_call_began_is_not_marked_not_transmitted(tmp_path):
    v, client = venue(tmp_path, order_status=500)
    with pytest.raises(wb.WebullError) as exc:
        v.submit("SPY", 1.0, "t")
    assert not isinstance(exc.value, wb.NotTransmitted) and len(client.placed()) == 1
    assert KEY not in str(exc.value) and SECRET not in str(exc.value)


def test_submit_reports_the_venue_order_id(tmp_path):
    v, _ = venue(tmp_path)
    assert v.submit("SPY", 1.0, "t")["venue_order_id"] == "o-1"
    bare = wb.WebullVenue(KEY, SECRET, client=FakeClient(place_body={"client_order_id": "x"}), confirmation_file=tmp_path / "x.json")
    assert bare.submit("SPY", 1.0, "t")["venue_order_id"] is None


@pytest.mark.parametrize("raw, state", [("PENDING", "open"), ("SUBMITTED", "open"), ("PARTIAL_FILLED", "partly_filled"),
                                        ("PARTIAL FILLED", "partly_filled"), ("FILLED", "filled"), ("CANCELLED", "cancelled"),
                                        ("FAILED", "rejected")])
def test_order_status_maps_every_documented_status(tmp_path, raw, state):
    v, client = status_venue(tmp_path, detail(order(raw)))
    out = v.order_status("t")
    assert out["state"] == state and out["raw_status"] == raw and out["venue_order_id"] == "WB-1"
    assert ("detail", "ACC-0001-1234", wb.client_order_id("t")) in client.calls


@pytest.mark.parametrize("raw", ["EXPIRED", "CANCELED", "DONE", "", None, 7])
def test_an_unlisted_status_is_unknown_never_filled_or_cancelled(tmp_path, raw):
    out = status_venue(tmp_path, detail(order(raw)))[0].order_status("t")
    assert out["state"] == "unknown_status"


NOT_FOUND_ANSWERS = [detail(), Response({"orders": []}), ServerError("ORDER_NOT_FOUND"),
                     Response({"error_code": "ORDER_NOT_FOUND", "message": "order does not exist"}, 417)]
MALFORMED_ANSWERS = [Response({"orders": ["junk"]}), Response([]), Response({"orders": [order("FILLED"), "junk"]}),
                     Response([detail(order("FILLED")), "junk"]), Response({"orders": None}), Response([[]])]


@pytest.mark.parametrize("answer", NOT_FOUND_ANSWERS)
def test_order_status_is_none_only_when_webull_says_there_is_no_such_order_and_that_answer_is_confirmed(tmp_path, answer):
    assert status_venue(tmp_path, answer, confirmed_not_found=True)[0].order_status("t") is None


@pytest.mark.parametrize("answer", NOT_FOUND_ANSWERS)
def test_an_unconfirmed_no_such_order_answer_raises_by_default(tmp_path, answer):
    v, _ = status_venue(tmp_path, answer)
    with pytest.raises(wb.WebullError, match="not yet confirmed") as exc:
        v.order_status("t")
    assert KEY not in str(exc.value) and SECRET not in str(exc.value)


@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("answer", MALFORMED_ANSWERS)
def test_a_malformed_order_detail_raises_and_is_never_no_such_order(tmp_path, answer, confirmed):
    v, _ = status_venue(tmp_path, answer, confirmed_not_found=confirmed)
    with pytest.raises(wb.WebullError, match="unexpected shape"):
        v.order_status("t")


def test_the_not_found_flag_is_passed_through_from_secrets(tmp_path):
    env = {"WEBULL_APP_KEY": KEY, "WEBULL_APP_SECRET": SECRET}
    client = FakeClient(details={wb.client_order_id("t"): detail()})
    v = wb.WebullVenue.from_secrets(env, client=client, confirmation_file=tmp_path / "x.json", confirmed_not_found=True)
    assert v.order_status("t") is None


@pytest.mark.parametrize("answer", [ConnectionError("no route to host " + KEY), ServerError("SYSTEM_ERROR", 500),
                                    ServerError("UNAUTHORIZED", 401), ServerError("SDK.HttpError", None),
                                    Response({"error_code": "SYSTEM_ERROR"}, 500, "upstream " + KEY + " " + SECRET),
                                    Response(None, 404, "not found " + KEY), Response({}), Response(None),
                                    Response(detail(order("FILLED", cid="other-1"), order("FILLED", cid="other-2")))])
def test_a_failed_lookup_raises_and_never_looks_like_no_such_order(tmp_path, answer):
    v, _ = status_venue(tmp_path, answer)
    with pytest.raises(wb.WebullError) as exc:
        v.order_status("t")
    assert KEY not in str(exc.value) and SECRET not in str(exc.value) and exc.value.__cause__ is None


def test_partial_fill_numbers_fees_and_the_plain_and_list_shapes(tmp_path):
    item = order("PARTIAL_FILLED", total_quantity="1", filled_quantity="0.4", filled_price="101.5",
                 commission={"actual_commission": "0.5", "receivable_commission": "0.5"},
                 fees=[{"type": "SEC_FEE", "actual_value": "0.01"}, {"type": "FINRA_TAF", "actual_value": "0.02"}])
    expected = {"state": "partly_filled", "filled_units": 0.4, "avg_price": 101.5, "fee": pytest.approx(0.53),
                "venue_order_id": "WB-1", "raw_status": "PARTIAL_FILLED"}
    other = order("FILLED", cid="someone-else", order_id="WB-2")
    for shape in (detail(other, item), item, [detail(other, item)]):                       # combo, plain, list of combos
        assert status_venue(tmp_path, shape)[0].order_status("t") == expected
    sell = order("FILLED", side="SELL", filled_quantity="2", filled_price="10")           # sold units are still unsigned
    assert status_venue(tmp_path, detail(sell))[0].order_status("t")["filled_units"] == 2.0
    nothing = status_venue(tmp_path, detail(order("SUBMITTED")))[0].order_status("t")
    assert (nothing["filled_units"], nothing["avg_price"], nothing["fee"]) == (0.0, None, None)


def test_unparseable_numbers_are_none_never_zero(tmp_path):
    bad = order("FILLED", filled_quantity="n/a", filled_price="", commission={"actual_commission": "abc"})
    out = status_venue(tmp_path, detail(bad))[0].order_status("t")
    assert (out["filled_units"], out["avg_price"], out["fee"]) == (None, None, None)
    missing = {k: val for k, val in order("FILLED").items() if k != "filled_quantity"}
    assert status_venue(tmp_path, detail(missing))[0].order_status("t")["filled_units"] is None
    part_bad_fee = order("FILLED", filled_quantity="1", filled_price="nan", fees=[{"actual_value": "0.1"}, {"actual_value": None}])
    out = status_venue(tmp_path, detail(part_bad_fee))[0].order_status("t")
    assert out["avg_price"] is None and out["fee"] is None


def test_open_orders_follow_every_page_to_the_end(tmp_path):
    btc = order("PARTIAL_FILLED", cid="c-btc", order_id="WB-7", symbol="BTCUSD", instrument_type="CRYPTO", side="SELL",
                total_quantity="0.003", filled_quantity="0.001")
    pages = {None: {"data": [detail(order("SUBMITTED", cid="c-spy", order_id="WB-5", total_quantity="2"), cid="c-spy")],
                    "pagination_key": "page-2"},
             "page-2": {"data": [{"client_order_id": "c-btc", "combo_type": "NORMAL", "orders": [btc]}]}}
    client = FakeClient(open_pages=pages)
    v = wb.WebullVenue(KEY, SECRET, client=client, confirmation_file=tmp_path / "x.json")
    assert v.open_orders() == [
        {"client_order_id": "c-spy", "venue_order_id": "WB-5", "symbol": "SPY", "instrument_type": "EQUITY", "units": 2.0, "state": "open", "filled_units": 0.0, "raw_status": "SUBMITTED"},
        {"client_order_id": "c-btc", "venue_order_id": "WB-7", "symbol": "BTC-USD", "instrument_type": "CRYPTO", "units": -0.003, "state": "partly_filled", "filled_units": 0.001, "raw_status": "PARTIAL_FILLED"}]
    assert [c[2] for c in client.calls if c[0] == "open"] == [None, "page-2"]
    looping = FakeClient(open_pages={None: {"data": [], "pagination_key": "same"}, "same": {"data": [], "pagination_key": "same"}})
    with pytest.raises(wb.WebullError, match="pagination"):
        wb.WebullVenue(KEY, SECRET, client=looping, confirmation_file=tmp_path / "x.json").open_orders()
    broken = FakeClient(open_pages={None: ServerError("SYSTEM_ERROR", 500)})
    with pytest.raises(wb.WebullError) as exc:
        wb.WebullVenue(KEY, SECRET, client=broken, confirmation_file=tmp_path / "x.json").open_orders()
    assert KEY not in str(exc.value) and SECRET not in str(exc.value)
    for shapeless in ({}, {"data": None}, None):                          # an answer without a list is not "nothing open"
        with pytest.raises(wb.WebullError, match="unexpected shape"):
            wb.WebullVenue(KEY, SECRET, client=FakeClient(open_pages={None: shapeless}), confirmation_file=tmp_path / "x.json").open_orders()


def test_cash_reads_the_documented_fields_and_leaves_a_missing_one_none(tmp_path):
    body = {"total_asset_currency": "USD", "total_cash_balance": "1500.25", "total_net_liquidation_value": "2000",
            "account_currency_assets": [{"currency": "HKD", "settled_cash": "9", "buying_power": "9"},
                                        {"currency": "USD", "cash_balance": "1500.25", "settled_cash": "1400", "buying_power": "1450.5"}]}
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(balance=body), confirmation_file=tmp_path / "x.json")
    assert v.cash() == {"cash": 1500.25, "settled_cash": 1400.0, "buying_power": 1450.5, "currency": "USD"}
    thin = {"total_asset_currency": "USD", "account_currency_assets": [{"currency": "USD", "cash_balance": "10", "buying_power": "oops"}]}
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(balance=thin), confirmation_file=tmp_path / "x.json")
    assert v.cash() == {"cash": 10.0, "settled_cash": None, "buying_power": None, "currency": "USD"}
    empty = wb.WebullVenue(KEY, SECRET, client=FakeClient(balance={}), confirmation_file=tmp_path / "x.json")
    assert empty.cash() == {"cash": None, "settled_cash": None, "buying_power": None, "currency": None}
    down = wb.WebullVenue(KEY, SECRET, client=FakeClient(balance=Response(None, 503, "gateway " + KEY)), confirmation_file=tmp_path / "x.json")
    with pytest.raises(wb.WebullError) as exc:
        down.cash()
    assert "503" in str(exc.value) and KEY not in str(exc.value)


def test_cancel_all_open_keeps_going_when_one_cancel_fails_and_needs_no_live_confirmation(tmp_path):
    pages = {None: {"data": [detail(order("SUBMITTED", cid="c-1", order_id="WB-1"), cid="c-1"),
                             detail(order("PENDING", cid="c-2", order_id="WB-2"), cid="c-2"),
                             detail(order("PARTIAL_FILLED", cid="c-3", order_id="WB-3"), cid="c-3")]}}
    client = FakeClient(open_pages=pages, cancel_fail={"c-2"})
    v = wb.WebullVenue(KEY, SECRET, environment="production", client=client, confirmation_file=tmp_path / "absent.json")
    out = v.cancel_all_open()
    assert [c[2] for c in client.calls if c[0] == "cancel"] == ["c-1", "c-2", "c-3"]
    assert [(r["client_order_id"], r["status"]) for r in out] == [("c-1", "cancel requested"), ("c-2", "error"), ("c-3", "cancel requested")]
    assert "ORDER_NOT_CANCELLABLE" in out[1]["error"] and KEY not in json.dumps(out) and SECRET not in json.dumps(out)
    assert "response" not in out[0] and client.placed() == []                # Webull's answer is not handed back


def test_check_reports_the_count_of_open_orders(tmp_path):
    pages = {None: {"data": [detail(order("SUBMITTED", cid="c-1"), cid="c-1")], "pagination_key": "p2"},
             "p2": {"data": [detail(order("SUBMITTED", cid="c-2"), cid="c-2")]}}
    report = wb.WebullVenue(KEY, SECRET, client=FakeClient(open_pages=pages), confirmation_file=tmp_path / "x.json").check()
    assert report["open_orders"] == 2
    broken = FakeClient(open_pages={None: ServerError("SYSTEM_ERROR", 500)})
    report = wb.WebullVenue(KEY, SECRET, client=broken, confirmation_file=tmp_path / "x.json").check()
    assert "open_orders" not in report and "SYSTEM_ERROR" in report["open_orders_error"] and KEY not in json.dumps(report)
    assert "balance" not in report and report["positions"] == 0          # the balance read worked: no error under its key


# ---- T9 rev 2: repairs after blind verification

@pytest.mark.parametrize("page", [{"data": ["junk"]}, {"data": [{"orders": ["junk"]}]}, {"data": [detail(order("SUBMITTED")), 3]}, [],
                                  {"data": [{"orders": None}]}])
def test_a_malformed_open_order_list_raises_and_nothing_is_skipped(tmp_path, page):
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(open_pages={None: page}), confirmation_file=tmp_path / "x.json")
    with pytest.raises(wb.WebullError, match="unexpected shape"):
        v.open_orders()


@pytest.mark.parametrize("key", [{"next": 2}, ["p2"], 2, ""])
def test_a_pagination_key_that_is_not_a_string_raises_webull_error_everywhere(tmp_path, key):
    pages = {None: {"data": [detail(order("SUBMITTED", cid="c-1"), cid="c-1")], "pagination_key": key}}
    make = lambda: wb.WebullVenue(KEY, SECRET, client=FakeClient(open_pages=pages), confirmation_file=tmp_path / "x.json")  # noqa: E731
    with pytest.raises(wb.WebullError, match="pagination key"):
        make().open_orders()
    client_v = make()
    with pytest.raises(wb.WebullError, match="pagination key"):
        client_v.cancel_all_open()
    assert [c for c in client_v._client.calls if c[0] == "cancel"] == []        # nothing is cancelled from a list that failed
    report = make().check()
    assert "pagination key" in report["open_orders_error"] and "open_orders" not in report


@pytest.mark.parametrize("bad", [{"accounts": "not a list"}, "accounts", [3], [{"no_id": 1}], 7, {"accounts": [None]}])
def test_malformed_account_and_position_answers_raise_webull_error(tmp_path, bad):
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(accounts=bad), confirmation_file=tmp_path / "x.json")
    with pytest.raises(wb.WebullError):
        _ = v.account_id
    for rows in (["junk"], "junk", [{"symbol": "SPY", "quantity": "1"}, 3]):
        p = wb.WebullVenue(KEY, SECRET, client=FakeClient(positions=rows), confirmation_file=tmp_path / "x.json")
        with pytest.raises(wb.WebullError):
            p.positions()


def test_cash_never_labels_another_currencys_figures(tmp_path):
    hkd_only = {"total_asset_currency": "USD", "total_cash_balance": "100",
                "account_currency_assets": [{"currency": "HKD", "cash_balance": "780", "settled_cash": "700", "buying_power": "750"}]}
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(balance=hkd_only), confirmation_file=tmp_path / "x.json")
    assert v.cash() == {"cash": 100.0, "settled_cash": None, "buying_power": None, "currency": "USD"}
    no_total = {"account_currency_assets": [{"currency": "HKD", "cash_balance": "780", "settled_cash": "700", "buying_power": "750"}]}
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(balance=no_total), confirmation_file=tmp_path / "x.json")
    assert v.cash() == {"cash": 780.0, "settled_cash": 700.0, "buying_power": 750.0, "currency": "HKD"}
    for bad in ({"account_currency_assets": "x"}, {"account_currency_assets": ["junk"]}):
        with pytest.raises(wb.WebullError, match="unexpected shape"):
            wb.WebullVenue(KEY, SECRET, client=FakeClient(balance=bad), confirmation_file=tmp_path / "x.json").cash()


def _dirty_sdk_loggers(log_file, stream):
    """What TradeClient._init_logger does (SDK trade_client.py:41-47, core/client.py:393-422): logger 'webull.core'
    at DEBUG then INFO, a StreamHandler on stdout and a TimedRotatingFileHandler on webull_trade_sdk.log."""
    from logging.handlers import TimedRotatingFileHandler
    log = logging.getLogger("webull.core")
    log.setLevel(logging.DEBUG)
    log.propagate = True
    stream_handler, file_handler = logging.StreamHandler(stream), TimedRotatingFileHandler(filename=str(log_file), when="H", encoding="utf-8")
    log.addHandler(stream_handler)
    log.addHandler(file_handler)
    log.setLevel(logging.INFO)
    return stream_handler, file_handler



def test_the_sdks_loggers_are_quiet_and_their_handlers_closed_after_quieting(tmp_path):
    stream, log_file = io.StringIO(), tmp_path / "webull_trade_sdk.log"
    stream_handler, file_handler = _dirty_sdk_loggers(log_file, stream)
    try:
        wb._quiet_sdk_logging()
        core = logging.getLogger("webull.core")
        assert stream_handler not in core.handlers and file_handler not in core.handlers
        assert file_handler.stream is None                                   # closed
        assert not core.isEnabledFor(logging.CRITICAL) and core.propagate is False
        logging.getLogger("webull.core.client").error("request headers x-app-key=%s", KEY)
        core.error("request headers x-app-key=%s", KEY)
        assert stream.getvalue() == "" and log_file.read_text() == ""
    finally:
        for h in (stream_handler, file_handler):
            logging.getLogger("webull.core").removeHandler(h)
            h.close()


def test_an_empty_sdk_log_created_while_building_the_client_is_removed(tmp_path, monkeypatch):
    """A stand-in SDK whose TradeClient dirties the loggers and creates the log file the way the real one does."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))                  # rev 3: the token directory goes under HOME
    stream, seen = io.StringIO(), {}

    class FakeApiClient:
        def __init__(self, key, secret, region):
            self._token_dir = None

        def add_endpoint(self, region, host):
            seen["host"] = host

        def set_token_dir(self, token_dir):
            self._token_dir = token_dir

    class FakeTradeClient:
        def __init__(self, api):
            seen["flags"] = (getattr(api, "_stream_logger_set", False), getattr(api, "_file_logger_set", False))
            seen["handlers"] = _dirty_sdk_loggers(tmp_path / "webull_trade_sdk.log", stream)    # as if it ignored the flags
            self.account_v2 = self.order_v3 = None

    monkeypatch.setitem(sys.modules, "webull.core.client", types.SimpleNamespace(ApiClient=FakeApiClient))
    monkeypatch.setitem(sys.modules, "webull.trade.trade_client", types.SimpleNamespace(TradeClient=FakeTradeClient))
    wb.WebullVenue(KEY, SECRET, confirmation_file=tmp_path / "x.json")
    assert seen["host"] == wb.SANDBOX_HOST and seen["flags"] == (True, True)    # the SDK is told its loggers are already set
    assert not (tmp_path / "webull_trade_sdk.log").exists()
    core = logging.getLogger("webull.core")
    assert all(h not in core.handlers for h in seen["handlers"]) and not core.isEnabledFor(logging.CRITICAL)
    core.error("x-app-key=%s", KEY)
    assert stream.getvalue() == ""
    kept = tmp_path / "webull_trade_sdk.log"                              # a log that was there before is never removed
    kept.write_text("older lines\n")
    wb.WebullVenue(KEY, SECRET, confirmation_file=tmp_path / "x.json")
    assert kept.read_text() == "older lines\n"
    for h in seen["handlers"]:
        h.close()


@pytest.mark.parametrize("side", [None, "", "HOLD", 1])
def test_an_open_order_with_an_unknown_side_has_no_units(tmp_path, side):
    item = order("SUBMITTED", cid="c-1", side=side)
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(open_pages={None: {"data": [detail(item, cid="c-1")]}}), confirmation_file=tmp_path / "x.json")
    assert v.open_orders()[0]["units"] is None
    short = order("SUBMITTED", cid="c-2", side="SHORT", total_quantity="3")
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(open_pages={None: {"data": [detail(short, cid="c-2")]}}), confirmation_file=tmp_path / "x.json")
    assert v.open_orders()[0]["units"] == -3.0


def test_nothing_out_of_submit_after_place_order_began_is_not_transmitted(tmp_path):
    for raised in (wb.NotTransmitted("a refusal raised inside the SDK call"), wb.LiveTradingNotConfirmed("inside the call")):
        client = FakeClient()
        client.order_v3.place_order = lambda a, orders, raised=raised: (client.calls.append(("place", a, orders)), (_ for _ in ()).throw(raised))[1]
        v = wb.WebullVenue(KEY, SECRET, client=client, confirmation_file=tmp_path / "x.json")
        with pytest.raises(wb.WebullError) as exc:
            v.submit("SPY", 1.0, "t")
        assert not isinstance(exc.value, wb.NotTransmitted) and len(client.placed()) == 1 and "unknown" in str(exc.value)


@pytest.mark.parametrize("units", [float("nan"), float("inf"), -float("inf"), 4e-6, -4e-6, 1e-9])
def test_units_that_are_not_finite_or_format_to_zero_are_not_transmitted(tmp_path, units):
    with pytest.raises(wb.NotTransmitted):
        wb.order_payload("SPY", units, "t")
    v, client = venue(tmp_path, positions=[{"symbol": "SPY", "quantity": "5", "instrument_type": "EQUITY"}])
    with pytest.raises(wb.NotTransmitted):
        v.submit("SPY", units, "t")
    with pytest.raises(wb.NotTransmitted):
        wb.order_payload("BTC-USD", units / 1000 if abs(units) == 4e-6 else units, "t")      # crypto rounds to 8 places
    assert client.placed() == []
    assert wb.order_payload("BTC-USD", 4e-6, "t")["quantity"] == "0.000004"            # small but not zero: allowed


# ---- T9 rev 3: second repair after blind verification

HUGE = 10 ** 400                                                          # float(HUGE) raises OverflowError
WEIRD = [None, True, False, 0, -1, HUGE, -HUGE, 1e308, float("inf"), float("nan"), "", "x", "1e999", "-0", [], [None], ["junk"],
         {}, {"a": 1}, "ORDER_NOT_FOUND"]


def _mutations(template):
    """The template itself, the template replaced whole by each odd value, and every field at every level replaced by each."""
    yield template
    for w in WEIRD:
        yield w
    if isinstance(template, dict):
        for k, v in template.items():
            for w in WEIRD:
                yield {**template, k: w}
            for sub in list(_mutations(v))[1 + len(WEIRD):]:
                yield {**template, k: sub}
    elif isinstance(template, list):
        for i, v in enumerate(template):
            for w in WEIRD:
                yield template[:i] + [w] + template[i + 1:]
            for sub in list(_mutations(v))[1 + len(WEIRD):]:
                yield template[:i] + [sub] + template[i + 1:]


ITEM = order("PARTIAL_FILLED", cid=wb.client_order_id("t"), symbol="BTCUSD", instrument_type="CRYPTO", side="SELL", total_quantity="2",
             filled_quantity="1", filled_price="10", commission={"actual_commission": "0.5"}, fees=[{"actual_value": "0.1"}])
TEMPLATES = {
    "detail": detail(ITEM),
    "open": {"data": [detail(ITEM)], "pagination_key": None},
    "balance": {"total_asset_currency": "USD", "total_cash_balance": "10",
                "account_currency_assets": [{"currency": "USD", "cash_balance": "10", "settled_cash": "9", "buying_power": "8"}]},
    "positions": [{"symbol": "SPY", "quantity": "2", "instrument_type": "EQUITY"}],
    "accounts": [{"account_id": "ACC-0001-1234", "account_type": "CASH"}],
}
CALLS = {"detail": ["order_status"], "open": ["open_orders", "cancel_all_open", "check"], "balance": ["cash", "check", "balance"],
         "positions": ["positions", "check"], "accounts": ["accounts", "check", "order_status", "cash"]}


def _fuzz_venue(tmp_path, endpoint, body, status=200):
    client = FakeClient()
    answer = Response(body, status)
    if endpoint == "detail":
        client._details = {wb.client_order_id("t"): answer}
    elif endpoint == "open":
        client._open_pages = {None: answer}
    elif endpoint == "balance":
        client._balance = answer
    elif endpoint == "positions":
        client.account_v2.get_account_position = lambda a: answer
    else:
        client.account_v2.get_account_list = lambda: answer
    return wb.WebullVenue(KEY, SECRET, client=client, confirmation_file=tmp_path / "x.json")


@pytest.mark.parametrize("endpoint", sorted(TEMPLATES))
def test_generated_malformed_answers_raise_only_webull_error_from_every_public_method(tmp_path, endpoint):
    cases = 0
    for body in _mutations(TEMPLATES[endpoint]):
        for status in (200, 417):
            for method in CALLS[endpoint]:
                v = _fuzz_venue(tmp_path, endpoint, body, status)
                try:
                    out = getattr(v, method)("t") if method == "order_status" else getattr(v, method)()
                except wb.WebullError:
                    out = "raised"
                except Exception as exc:                                  # anything else is the failure under test
                    pytest.fail(f"{method} on {endpoint} {status} {body!r:.200} raised {type(exc).__name__}: {exc!s:.100}")
                if method == "order_status":
                    assert out is not None, f"None with the default flag for {body!r:.200}"
                cases += 1
    assert cases > 100


def test_a_huge_or_infinite_number_is_unreadable_not_an_overflow(tmp_path):
    out = status_venue(tmp_path, detail(order("FILLED", filled_quantity=HUGE, filled_price=HUGE, commission={"actual_commission": HUGE})))[0].order_status("t")
    assert (out["filled_units"], out["avg_price"], out["fee"]) == (None, None, None)
    big_fees = order("FILLED", filled_quantity="1", fees=[{"actual_value": 1e308}, {"actual_value": 1e308}])   # the sum overflows to inf
    assert status_venue(tmp_path, detail(big_fees))[0].order_status("t")["fee"] is None
    rows = [{"symbol": "SPY", "quantity": HUGE, "instrument_type": "EQUITY"}, {"symbol": "QQQ", "quantity": "1e999", "instrument_type": "EQUITY"}, {"symbol": "IWM", "quantity": "nan", "instrument_type": "EQUITY"},
            {"symbol": "DIA", "quantity": "3", "instrument_type": "EQUITY"}]
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(positions=rows), confirmation_file=tmp_path / "x.json")
    assert v.positions() == {"DIA": 3.0}                                  # unreadable quantities are skipped, never infinite
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        v.submit("QQQ", -1.0, "t")                                        # an infinite holding cannot license a sale
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(balance={"total_asset_currency": "USD", "total_cash_balance": HUGE}), confirmation_file=tmp_path / "x.json")
    assert v.cash()["cash"] is None


EQUITY_BAD_LIMITS = [0.000019, float("nan"), float("inf"), -5, 0, -0.0, 10.005, 1.234, 0.12345, "abc", "10.00", True, [10]]
CRYPTO_BAD_LIMITS = [0.000000001, float("nan"), float("-inf"), -5, 0, 1.123456789, "x"]


@pytest.mark.parametrize("symbol, limit", [("SPY", x) for x in EQUITY_BAD_LIMITS] + [("BTC-USD", x) for x in CRYPTO_BAD_LIMITS])
def test_a_limit_price_that_is_not_positive_finite_or_on_the_tick_is_not_transmitted(tmp_path, symbol, limit):
    with pytest.raises(wb.NotTransmitted):
        wb.order_payload(symbol, 1.0, "t", limit_price=limit)
    held = [{"symbol": "SPY", "quantity": "5", "instrument_type": "EQUITY"}, {"symbol": "BTCUSD", "quantity": "5", "instrument_type": "CRYPTO"}]
    for env in ("sandbox", "production"):
        v, client = venue(tmp_path, env=env, positions=held)
        for units in (1.0, -1.0):
            with pytest.raises(wb.NotTransmitted):
                v.submit(symbol, units, "t", price=100.0, limit_price=limit)
        assert client.placed() == []


@pytest.mark.parametrize("symbol, limit, sent", [("SPY", 10.01, "10.01"), ("SPY", 180, "180.00"), ("SPY", 1.0, "1.00"), ("SPY", 0.1234, "0.1234"),
                                                 ("SPY", 0.5, "0.50"), ("SPY", 0.0001, "0.0001"), ("BTC-USD", 80000, "80000.00"),
                                                 ("BTC-USD", 0.00012345, "0.00012345"), ("BTC-USD", 65432.12345678, "65432.12345678")])
def test_the_limit_price_sent_is_exactly_the_price_given(tmp_path, symbol, limit, sent):
    text = wb.order_payload(symbol, 1.0, "t", limit_price=limit)["limit_price"]
    assert text == sent and float(text) == float(limit) and float(text) > 0
    v, client = venue(tmp_path)
    v.submit(symbol, 1.0, "t", limit_price=limit)
    assert client.placed()[0][2][0]["limit_price"] == sent


def test_the_live_cap_still_uses_the_limit_price(tmp_path):
    v, client = venue(tmp_path, env="production")
    (tmp_path / "webull_live.json").write_text(json.dumps({"account_id": "ACC-0001-1234", "confirmed_by": "Burton", "max_order_value": 1000,
                                                           "i_understand_this_trades_real_money": True}))
    with pytest.raises(wb.LiveTradingNotConfirmed, match="above the confirmed cap"):
        v.submit("SPY", 2.0, "t", price=100.0, limit_price=600.0)          # valued at the limit: 1,200 > 1,000
    assert client.placed() == []
    v.submit("SPY", 2.0, "t", price=900.0, limit_price=400.0)             # valued at the limit: 800 <= 1,000
    assert client.placed()[0][2][0]["limit_price"] == "400.00"


class _TokenSdk:
    """Stand-ins for ApiClient and TradeClient. TradeClient writes a token where the SDK would: the directory set with
    ApiClient.set_token_dir, else $WEBULL_OPENAPI_TOKEN_DIR, else conf/ under the working directory
    (webull/core/http/initializer/token/token_storage.py:48-86, token_manager.py:96-104)."""
    def __init__(self):
        self.seen = {}
        sdk = self

        class ApiClient:
            def __init__(self, key, secret, region):
                self._token_dir = None

            def add_endpoint(self, region, host):
                pass

            def set_token_dir(self, token_dir):
                self._token_dir = token_dir

            def get_token_dir(self):
                return self._token_dir

        class TradeClient:
            def __init__(self, api):
                import os
                from pathlib import Path
                raw = api.get_token_dir() or os.getenv("WEBULL_OPENAPI_TOKEN_DIR") or "conf"
                sdk.seen["raw"] = raw
                raw = raw.strip()                                          # as the SDK does (token_storage.py:71-73)
                d = Path(raw).expanduser().absolute()
                if any(q.exists() and os.path.samefile(q, wb.REPO) for q in (d, *d.parents)):
                    raise AssertionError("the stand-in SDK refuses to write a token inside the repository")
                sdk.seen["token_dir"] = d
                d.mkdir(parents=True, exist_ok=True)
                (d / "token.txt").write_text("the-access-token\n")
                self.account_v2 = self.order_v3 = None

        self.ApiClient, self.TradeClient = ApiClient, TradeClient

    def install(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "webull.core.client", types.SimpleNamespace(ApiClient=self.ApiClient))
        monkeypatch.setitem(sys.modules, "webull.trade.trade_client", types.SimpleNamespace(TradeClient=self.TradeClient))


def test_the_sdks_token_goes_to_a_private_directory_outside_the_repository(tmp_path, monkeypatch):
    sdk = _TokenSdk()
    sdk.install(monkeypatch)
    work, home = tmp_path / "work", tmp_path / "home"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("WEBULL_OPENAPI_TOKEN_DIR", raising=False)
    wb.WebullVenue(KEY, SECRET, environment="sandbox", confirmation_file=tmp_path / "x.json")
    private = home / ".valedia" / "webull" / "sandbox"
    assert sdk.seen["token_dir"] == private and (private / "token.txt").is_file()
    assert (private.stat().st_mode & 0o777) == 0o700
    assert not (work / "conf").exists() and not list(work.rglob("token.txt"))
    custom = tmp_path / "tokens" / "mine"
    custom.mkdir(parents=True, mode=0o755)
    custom.chmod(0o755)                                                    # an existing directory is tightened to 0700
    wb.WebullVenue.from_secrets({"WEBULL_APP_KEY": KEY, "WEBULL_APP_SECRET": SECRET, "WEBULL_ENV": "production"},
                                confirmation_file=tmp_path / "x.json", token_dir=custom)
    assert sdk.seen["token_dir"] == custom and (custom.stat().st_mode & 0o777) == 0o700
    assert not (work / "conf").exists()


def test_a_token_directory_inside_the_repository_fails_closed(tmp_path, monkeypatch):
    sdk = _TokenSdk()
    sdk.install(monkeypatch)
    inside = wb.REPO / "conf"
    existed = inside.exists()
    with pytest.raises(wb.WebullError, match="outside the repository"):
        wb.WebullVenue(KEY, SECRET, confirmation_file=tmp_path / "x.json", token_dir=inside)
    assert "token_dir" not in sdk.seen and inside.exists() == existed


@pytest.mark.parametrize("answer", [ServerError("order_not_found"), ServerError(" ORDER_NOT_FOUND "), ServerError("ORDER_NOT_FOUND", 500),
                                    ServerError("ORDER_NOT_FOUND", 302), ServerError("ORDER_NOT_FOUND", None),
                                    ConnectionError("transport"), Response({"error_code": "ORDER_NOT_FOUND"}, 500),
                                    Response({"error_code": "ORDER_NOT_FOUND"}, 201), Response({"error_code": "ORDER_NOT_FOUND"}, 302),
                                    Response({"error_code": "order_not_found"}, 417), Response({"orders": [], "status": "FILLED"}),
                                    Response({"orders": [], "status": None}), Response({"error_code": "ORDER_NOT_FOUND"}, 200)])
def test_with_the_flag_on_only_the_two_exact_answers_mean_no_such_order(tmp_path, answer):
    if isinstance(answer, ConnectionError):
        answer = type("TransportError", (Exception,), {"error_code": "ORDER_NOT_FOUND", "error_msg": "x"})()
    v, _ = status_venue(tmp_path, answer, confirmed_not_found=True)
    with pytest.raises(wb.WebullError):
        v.order_status("t")
    for exact in (ServerError("ORDER_NOT_FOUND", 404), Response({"error_code": "ORDER_NOT_FOUND"}, 404), Response({"orders": []})):
        assert status_venue(tmp_path, exact, confirmed_not_found=True)[0].order_status("t") is None


def test_cash_reports_the_total_only_with_the_currency_webull_attached(tmp_path):
    entry = {"currency": "HKD", "cash_balance": "780", "settled_cash": "700", "buying_power": "750"}
    for top in ({}, {"total_asset_currency": ""}, {"total_asset_currency": None}):
        body = {**top, "total_cash_balance": "100", "account_currency_assets": [entry]}
        v = wb.WebullVenue(KEY, SECRET, client=FakeClient(balance=body), confirmation_file=tmp_path / "x.json")
        assert v.cash() == {"cash": 780.0, "settled_cash": 700.0, "buying_power": 750.0, "currency": "HKD"}
    body = {"total_cash_balance": "100", "account_currency_assets": [{"currency": "HKD", "settled_cash": "1"}]}
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(balance=body), confirmation_file=tmp_path / "x.json")
    assert v.cash() == {"cash": None, "settled_cash": 1.0, "buying_power": None, "currency": "HKD"}
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(balance={"total_cash_balance": "100"}), confirmation_file=tmp_path / "x.json")
    assert v.cash() == {"cash": None, "settled_cash": None, "buying_power": None, "currency": None}


def test_an_open_order_combo_with_an_empty_order_list_raises(tmp_path):
    page = {"data": [detail(order("SUBMITTED", cid="c-1"), cid="c-1"), {"client_order_id": "c-2", "combo_type": "NORMAL", "orders": []}]}
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(open_pages={None: page}), confirmation_file=tmp_path / "x.json")
    with pytest.raises(wb.WebullError, match="unexpected shape"):
        v.open_orders()


def test_a_negative_filled_quantity_is_unreadable(tmp_path):
    assert status_venue(tmp_path, detail(order("PARTIAL_FILLED", filled_quantity="-0.4")))[0].order_status("t")["filled_units"] is None
    page = {"data": [detail(order("PARTIAL_FILLED", cid="c-1", filled_quantity="-1"), cid="c-1")]}
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(open_pages={None: page}), confirmation_file=tmp_path / "x.json")
    assert v.open_orders()[0]["filled_units"] is None


# ---- T9 rev 4: third repair after blind verification

def _decimals(x):
    from decimal import Decimal
    return max(0, -Decimal(repr(abs(x))).as_tuple().exponent)


def test_generated_quantities_are_sent_exactly_or_not_at_all(tmp_path):
    import random
    from decimal import Decimal
    rng = random.Random(20261112)
    held = [{"symbol": "SPY", "quantity": "1000000000", "instrument_type": "EQUITY"},
            {"symbol": "BTCUSD", "quantity": "1000000000", "instrument_type": "CRYPTO"}]
    accepted = refused = 0
    for i in range(1200):
        symbol, step = ("BTC-USD", 8) if i % 2 else ("SPY", 5)
        k = rng.randint(0, 11)
        x = float(Decimal(rng.randint(1, 10 ** rng.randint(1, 12))).scaleb(-k)) * rng.choice((1, -1))
        on_step = x != 0 and abs(x) <= 1e9 and _decimals(x) <= step
        v, client = venue(tmp_path, positions=held)
        if on_step:
            out = v.submit(symbol, x, f"g{i}")
            sent = client.placed()[0][2][0]["quantity"]
            assert float(sent) == abs(x) and out["quantity"] == sent and out["side"] == ("BUY" if x > 0 else "SELL"), (x, sent)
            accepted += 1
        else:
            with pytest.raises(wb.NotTransmitted):
                v.submit(symbol, x, f"g{i}")
            with pytest.raises(wb.NotTransmitted):
                wb.order_payload(symbol, x, "t")
            assert client.placed() == [], x
            refused += 1
    assert accepted > 250 and refused > 250


def test_the_examples_found_are_refused_not_rounded(tmp_path):
    v, client = venue(tmp_path, positions=[{"symbol": "SPY", "quantity": "0.000006", "instrument_type": "EQUITY"}])
    with pytest.raises(wb.NotTransmitted, match="step"):
        v.submit("SPY", -6e-6, "t")                                       # was sent as a sell of 0.00001, more than held
    with pytest.raises(wb.NotTransmitted, match="step"):
        wb.order_payload("SPY", 6e-6, "t")
    with pytest.raises(wb.NotTransmitted, match="step"):
        wb.order_payload("BTC-USD", 1.123456789, "t")
    assert client.placed() == []
    assert wb.order_payload("SPY", 0.00001, "t")["quantity"] == "0.00001"


@pytest.mark.parametrize("units", [True, False, "5", "-2", "1.0", None, [1], {"q": 1}, b"1", complex(1, 0)])
def test_booleans_strings_and_other_types_are_not_quantities(tmp_path, units):
    with pytest.raises(wb.NotTransmitted):
        wb.order_payload("SPY", units, "t")
    v, client = venue(tmp_path, positions=[{"symbol": "SPY", "quantity": "10", "instrument_type": "EQUITY"}])
    with pytest.raises(wb.NotTransmitted):
        v.submit("SPY", units, "t")
    assert client.placed() == []


def test_a_quantity_or_limit_above_the_sanity_ceiling_is_not_transmitted(tmp_path):
    v, client = venue(tmp_path)
    for units, limit in ((1e9 + 1, None), (2e9, None), (1.0, 1e9 + 1), (1.0, 1e300), (1.0, float(10 ** 300))):
        with pytest.raises(wb.NotTransmitted, match="ceiling"):
            v.submit("SPY", units, "t", limit_price=limit)
        with pytest.raises(wb.NotTransmitted, match="ceiling"):
            wb.order_payload("BTC-USD", units, "t", limit_price=limit)
    assert client.placed() == []
    assert v.submit("SPY", 1e9, "t")["quantity"] == "1000000000"
    assert wb.order_payload("SPY", 1.0, "t", limit_price=1e9)["limit_price"] == "1000000000.00"


def test_duplicate_or_absurd_holdings_never_license_a_sale(tmp_path):
    for rows in ([{"symbol": "SPY", "quantity": "5", "instrument_type": "EQUITY"}, {"symbol": "SPY", "quantity": "5", "instrument_type": "EQUITY"}],
                 [{"symbol": "BTCUSD", "quantity": "1", "instrument_type": "CRYPTO"}, {"symbol": "BTC-USD", "quantity": "1", "instrument_type": "CRYPTO"}],
                 [{"symbol": "SPY", "quantity": "5", "instrument_type": "EQUITY"}, {"symbol": "spy", "quantity": "junk", "instrument_type": "EQUITY"}]):
        v, client = venue(tmp_path, positions=rows)
        with pytest.raises(wb.WebullError, match="more than one row"):
            v.positions()
        sym = "BTC-USD" if "BTC" in json.dumps(rows) else "SPY"
        with pytest.raises(wb.NotTransmitted):
            v.submit(sym, -1.0, "t")
        assert client.placed() == []
    v, client = venue(tmp_path, positions=[{"symbol": "SPY", "quantity": "1e300", "instrument_type": "EQUITY"}, {"symbol": "QQQ", "quantity": "1000000000001", "instrument_type": "EQUITY"},
                                           {"symbol": "IWM", "quantity": "1000000000000", "instrument_type": "EQUITY"}])
    assert v.positions() == {"IWM": 1e12}                                  # above 1e12 gives no holding
    for sym in ("SPY", "QQQ"):
        with pytest.raises(wb.NotTransmitted, match="long-only"):
            v.submit(sym, -1e6, "t")
    broken = FakeClient()
    broken.account_v2.get_account_position = lambda a: (_ for _ in ()).throw(ServerError("SYSTEM_ERROR", 500))
    bv = wb.WebullVenue(KEY, SECRET, client=broken, confirmation_file=tmp_path / "x.json")
    with pytest.raises(wb.NotTransmitted):
        bv.submit("SPY", -1.0, "t")
    assert client.placed() == [] and broken.placed() == []


def test_a_token_directory_spelled_in_another_case_inside_the_repository_is_refused(tmp_path, monkeypatch):
    import os
    swapped = str(wb.REPO).swapcase()
    if swapped == str(wb.REPO) or not os.path.exists(swapped):
        pytest.skip("case-sensitive filesystem: a path in other letters is another directory here")
    sdk = _TokenSdk()
    sdk.install(monkeypatch)
    for inside in (os.path.join(swapped, "conf"), os.path.join(swapped, "conf", "deeper", "still"), swapped):
        existed = os.path.exists(os.path.join(str(wb.REPO), "conf"))
        with pytest.raises(wb.WebullError, match="outside the repository"):
            wb.WebullVenue(KEY, SECRET, confirmation_file=tmp_path / "x.json", token_dir=inside)
        assert "raw" not in sdk.seen and os.path.exists(os.path.join(str(wb.REPO), "conf")) == existed


def test_a_token_directory_reaching_the_repository_through_a_link_or_dots_is_refused(tmp_path, monkeypatch):
    sdk = _TokenSdk()
    sdk.install(monkeypatch)
    link = tmp_path / "link"
    link.symlink_to(wb.REPO, target_is_directory=True)
    for inside in (link / "conf", wb.REPO / "valedia_trader" / ".." / "conf", Path("/") / ".." / str(wb.REPO).lstrip("/") / "conf"):
        with pytest.raises(wb.WebullError, match="outside the repository"):
            wb.WebullVenue(KEY, SECRET, confirmation_file=tmp_path / "x.json", token_dir=inside)
    assert "raw" not in sdk.seen and not (wb.REPO / "conf").exists()


@pytest.mark.parametrize("name", ["trail ", " lead", "\ttab", "both \n"])
def test_a_token_directory_with_surrounding_whitespace_is_refused(tmp_path, monkeypatch, name):
    sdk = _TokenSdk()
    sdk.install(monkeypatch)
    text = name + str(tmp_path / "t") if name[0].isspace() else str(tmp_path / "t") + "/" + name     # the whole text's ends
    for spelled in (text, Path(text)):
        with pytest.raises(wb.WebullError, match="whitespace"):
            wb.WebullVenue(KEY, SECRET, confirmation_file=tmp_path / "x.json", token_dir=spelled)
    assert "raw" not in sdk.seen and not (tmp_path / "t").exists()


def test_the_directory_handed_to_the_sdk_is_exactly_the_one_made_private(tmp_path, monkeypatch):
    sdk = _TokenSdk()
    sdk.install(monkeypatch)
    wanted = tmp_path / "a" / "b"
    wb.WebullVenue(KEY, SECRET, confirmation_file=tmp_path / "x.json", token_dir=wanted)
    assert sdk.seen["raw"] == str(wanted) and (wanted.stat().st_mode & 0o777) == 0o700 and (wanted / "token.txt").is_file()


class TransportError(Exception):
    """Shaped like the SDK's ClientException for an HTTP failure (core/client.py:328): the HTTP library's text,
    with the request URL and its query string, ends up in error_msg."""
    error_code = "SDK.HttpError"

    def __init__(self, text):
        self.error_msg = text


URL_TEXT = ("HTTPSConnectionPool(host='api.sandbox.webull.com', port=443): Max retries exceeded with url: "
            "/trading/orders/get?account_id=ACC-0001-1234&client_order_id=abc (Caused by NewConnectionError: refused) "
            "see https://api.sandbox.webull.com/trading/assets/balances/get?account_id=ACC-0001-1234 account ACC-0001-1234")


def test_a_transport_error_never_carries_the_url_or_the_account_id(tmp_path):
    client = FakeClient(details={wb.client_order_id("t"): TransportError(URL_TEXT)}, open_pages={None: TransportError(URL_TEXT)},
                        balance=TransportError(URL_TEXT))
    client.order_v3.place_order = lambda a, orders: (client.calls.append(("place", a, orders)), (_ for _ in ()).throw(TransportError(URL_TEXT)))[1]
    v = wb.WebullVenue(KEY, SECRET, client=client, confirmation_file=tmp_path / "x.json")
    messages = []
    for call in (lambda: v.order_status("t"), v.open_orders, v.cash, lambda: v.submit("SPY", 1.0, "t"), v.cancel_all_open):
        with pytest.raises(wb.WebullError) as exc:
            call()
        messages.append(str(exc.value))
    messages.append(v.check()["open_orders_error"])
    for m in messages:
        assert "ACC-0001-1234" not in m and "/trading/" not in m and "?" not in m and "https://" not in m and "account_id=" not in m, m
        assert "<withheld>" in m, m
    assert "<code withheld>" in messages[0] and "max retries exceeded" in messages[0].lower()   # SDK.HttpError is not in the code shape


def _deep(n=60000):
    x = []
    for _ in range(n):
        x = [x]
    return x


HUGE_TEXT_INT = 10 ** 5000                                                 # str() of it raises ValueError (4,300-digit limit)


@pytest.mark.parametrize("odd", ["HUGE", "DEEP"])
def test_values_no_json_decoder_makes_still_raise_only_webull_error(tmp_path, odd):
    odd = _deep() if odd == "DEEP" else HUGE_TEXT_INT
    item = order("FILLED", order_id=odd, symbol=odd)
    page = {"data": [detail(item)]}
    client = FakeClient(details={wb.client_order_id("t"): Response(detail(item))}, open_pages={None: page},
                        positions=[{"symbol": odd, "quantity": "1", "instrument_type": "EQUITY"}], accounts=[{"account_id": odd, "account_type": odd}])
    v = wb.WebullVenue(KEY, SECRET, client=client, confirmation_file=tmp_path / "x.json", account_id="ACC-0001-1234")
    for call in (lambda: v.order_status("t"), v.open_orders, v.cancel_all_open, v.positions, v.check, v.accounts):
        try:
            call()
        except wb.WebullError as exc:
            assert type(exc) is wb.WebullError and len(str(exc)) < 400
        except Exception as exc:
            pytest.fail(f"{type(exc).__name__} left a public method")


def test_an_unreadable_answer_after_a_placed_order_is_still_accepted(tmp_path):
    class Unreadable(Response):
        def json(self):
            raise RecursionError("maximum recursion depth exceeded")
    for place_body in (Unreadable(None), Response({"order_id": HUGE_TEXT_INT}), Response({"order_id": _deep()}), Response(_deep(2000))):
        client = FakeClient()
        client.order_v3.place_order = lambda a, orders, b=place_body: (client.calls.append(("place", a, orders)), b)[1]
        v = wb.WebullVenue(KEY, SECRET, client=client, confirmation_file=tmp_path / "x.json")
        out = v.submit("SPY", 1.0, "t")
        assert out["status"] == "accepted" and out["venue_order_id"] is None and len(client.placed()) == 1


def test_a_negative_filled_price_is_no_average(tmp_path):
    out = status_venue(tmp_path, detail(order("FILLED", filled_quantity="1", filled_price="-10.5")))[0].order_status("t")
    assert out["avg_price"] is None and out["filled_units"] == 1.0


@pytest.mark.parametrize("body", [{"orders": [], "error_code": "SYSTEM_BUSY"}, {"orders": [], "message": "busy"},
                                  {"orders": [], "client_order_id": "someone-else"}, {"orders": [], "client_order_id": None},
                                  {"orders": [], "status": "FILLED"}])
def test_with_the_flag_on_an_empty_list_that_says_anything_else_raises(tmp_path, body):
    v, _ = status_venue(tmp_path, Response(body), confirmed_not_found=True)
    with pytest.raises(wb.WebullError):
        v.order_status("t")
    for ok in ({"orders": []}, {"orders": [], "client_order_id": wb.client_order_id("t"), "combo_type": "NORMAL"}):
        assert status_venue(tmp_path, Response(ok), confirmed_not_found=True)[0].order_status("t") is None


def test_open_orders_stop_after_two_hundred_pages(tmp_path):
    def chain(n):
        pages = {None: {"data": [], "pagination_key": "p1"}}
        for i in range(1, n):
            pages[f"p{i}"] = {"data": [], "pagination_key": f"p{i + 1}"} if i < n - 1 else {"data": []}
        return pages
    ok = FakeClient(open_pages=chain(200))
    assert wb.WebullVenue(KEY, SECRET, client=ok, confirmation_file=tmp_path / "x.json").open_orders() == []
    assert len([c for c in ok.calls if c[0] == "open"]) == 200
    too_many = FakeClient(open_pages=chain(201))
    with pytest.raises(wb.WebullError, match="200 pages"):
        wb.WebullVenue(KEY, SECRET, client=too_many, confirmation_file=tmp_path / "x.json").open_orders()
    assert len([c for c in too_many.calls if c[0] == "open"]) == 200


@pytest.mark.parametrize("raw", ["filled", "Filled", " FILLED", "FILLED ", "partial filled", "PARTIAL_FILLED ", "Cancelled", "submitted"])
def test_a_status_is_matched_exactly_as_documented(tmp_path, raw):
    assert status_venue(tmp_path, detail(order(raw)))[0].order_status("t")["state"] == "unknown_status"
    page = {"data": [detail(order(raw, cid="c-1"), cid="c-1")]}
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(open_pages={None: page}), confirmation_file=tmp_path / "x.json")
    assert v.open_orders()[0]["state"] == "unknown_status"


# ---- T9 rev 5: options and other instruments, open sells, secrets in error texts, unreadable exceptions

OPTION_ROW = {"symbol": "AAPL", "instrument_type": "OPTION", "quantity": "1"}          # docs account-position, example row


def test_the_documented_option_row_alone_is_no_holding_and_licenses_no_sale(tmp_path):
    v, client = venue(tmp_path, positions=[dict(OPTION_ROW)])
    assert v.positions() == {}
    assert v.other_positions() == [{"symbol": "AAPL", "instrument_type": "OPTION", "quantity": 1.0}]
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        v.submit("AAPL", -1.0, "t")
    assert len(client.placed()) == 0


def test_an_option_beside_the_stock_is_not_a_duplicate_and_does_not_add_to_it(tmp_path):
    v, client = venue(tmp_path, positions=[{"symbol": "AAPL", "instrument_type": "EQUITY", "quantity": "10"}, dict(OPTION_ROW)])
    assert v.positions() == {"AAPL": 10.0}
    assert v.other_positions() == [{"symbol": "AAPL", "instrument_type": "OPTION", "quantity": 1.0}]
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        v.submit("AAPL", -11.0, "t1")
    assert len(client.placed()) == 0
    assert v.submit("AAPL", -10.0, "t2")["status"] == "accepted" and len(client.placed()) == 1


def test_an_option_beside_a_crypto_holding(tmp_path):
    v, client = venue(tmp_path, positions=[{"symbol": "BTCUSD", "instrument_type": "CRYPTO", "quantity": "0.5"}, dict(OPTION_ROW)])
    assert v.positions() == {"BTC-USD": 0.5}
    assert v.other_positions() == [{"symbol": "AAPL", "instrument_type": "OPTION", "quantity": 1.0}]
    assert v.submit("BTC-USD", -0.5, "t")["status"] == "accepted" and len(client.placed()) == 1


@pytest.mark.parametrize("given", ["ABSENT", None, "", "equity", "Crypto", " EQUITY", "STOCK", "FUTURES", "EVENT", "OPTION", 7])
def test_a_row_without_a_known_holding_type_is_reported_apart_and_licenses_no_sale(tmp_path, given):
    row = {"symbol": "SPY", "quantity": "3"} if given == "ABSENT" else {"symbol": "SPY", "quantity": "3", "instrument_type": given}
    v, client = venue(tmp_path, positions=[row])
    assert v.positions() == {}
    shown = None if given == "ABSENT" else "<withheld>" if given in ("", " EQUITY", 7) else given    # only a short word is shown
    assert v.other_positions() == [{"symbol": "SPY", "instrument_type": shown, "quantity": 3.0}]
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        v.submit("SPY", -1.0, "t")
    assert len(client.placed()) == 0


def test_other_positions_give_an_unreadable_quantity_as_none(tmp_path):
    v, _ = venue(tmp_path, positions=[{"symbol": "AAPL", "instrument_type": "OPTION", "quantity": "junk"},
                                      {"symbol": "X", "instrument_type": "EVENT", "quantity": "1e300"}])
    assert v.other_positions() == [{"symbol": "AAPL", "instrument_type": "OPTION", "quantity": None},
                                   {"symbol": "X", "instrument_type": "EVENT", "quantity": None}]


def test_duplicates_are_judged_within_equities_and_within_crypto(tmp_path):
    for rows in ([{"symbol": "AAPL", "instrument_type": "EQUITY", "quantity": "1"}, {"symbol": "AAPL", "instrument_type": "EQUITY", "quantity": "1"}],
                 [{"symbol": "BTCUSD", "instrument_type": "CRYPTO", "quantity": "1"}, {"symbol": "BTCUSD", "instrument_type": "CRYPTO", "quantity": "1"}],
                 [{"symbol": "BTC-USD", "instrument_type": "EQUITY", "quantity": "1"}, {"symbol": "BTCUSD", "instrument_type": "CRYPTO", "quantity": "1"}]):
        v, client = venue(tmp_path, positions=rows)
        with pytest.raises(wb.WebullError, match="more than one row"):
            v.positions()
        with pytest.raises(wb.NotTransmitted):
            v.submit(rows[0]["symbol"] if rows[0]["instrument_type"] == "EQUITY" and "-" not in rows[0]["symbol"] else "BTC-USD", -1.0, "t")
        assert len(client.placed()) == 0
    v, _ = venue(tmp_path, positions=[{"symbol": "AAPL", "instrument_type": "EQUITY", "quantity": "2"}, dict(OPTION_ROW), dict(OPTION_ROW)])
    assert v.positions() == {"AAPL": 2.0} and len(v.other_positions()) == 2


def test_a_crypto_sale_is_licensed_only_by_a_crypto_row(tmp_path):
    v, client = venue(tmp_path, positions=[{"symbol": "BTC-USD", "instrument_type": "EQUITY", "quantity": "5"}])
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        v.submit("BTC-USD", -1.0, "t")
    assert len(client.placed()) == 0


def test_check_reports_the_count_of_other_positions(tmp_path):
    v, _ = venue(tmp_path, positions=[{"symbol": "AAPL", "instrument_type": "EQUITY", "quantity": "2"}, dict(OPTION_ROW),
                                      {"symbol": "ES", "instrument_type": "FUTURES", "quantity": "1"}])
    report = v.check()
    assert report["positions"] == 1 and report["other_positions"] == 2


def _open_sell(cid, total, filled="0", symbol="SPY", kind="EQUITY", side="SELL", status="SUBMITTED"):
    item = order(status, cid=cid, symbol=symbol, side=side, total_quantity=total, filled_quantity=filled)
    if kind is None:
        item.pop("instrument_type")
    else:
        item["instrument_type"] = kind
    return detail(item, cid=cid)


def _seller(tmp_path, held, *open_rows, symbol="SPY", kind="EQUITY", open_page=None):
    client = FakeClient(positions=[{"symbol": symbol, "instrument_type": kind, "quantity": held}])
    client._open_pages = {None: {"data": list(open_rows)}} if open_page is None else {None: open_page}
    return wb.WebullVenue(KEY, SECRET, client=client, confirmation_file=tmp_path / "x.json"), client


def _opens(client):
    return [c for c in client.calls if isinstance(c, tuple) and c[0] == "open"]


def test_one_open_sell_of_the_whole_holding_leaves_nothing_to_sell(tmp_path):
    v, client = _seller(tmp_path, "10", _open_sell("c-1", "10"))
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        v.submit("SPY", -10.0, "t")
    assert len(client.placed()) == 0 and len(_opens(client)) == 1
    v, client = _seller(tmp_path, "10", _open_sell("c-1", "4"))
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        v.submit("SPY", -7.0, "t")
    assert len(client.placed()) == 0
    assert v.submit("SPY", -6.0, "t2")["status"] == "accepted" and len(client.placed()) == 1
    assert client.calls.index(_opens(client)[-1]) < client.calls.index(client.placed()[0])     # read before the order call


def test_several_open_sells_are_added_up(tmp_path):
    v, client = _seller(tmp_path, "10", _open_sell("c-1", "3"), _open_sell("c-2", "4"))
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        v.submit("SPY", -4.0, "t")
    assert len(client.placed()) == 0
    assert v.submit("SPY", -3.0, "t2")["status"] == "accepted" and len(client.placed()) == 1


def test_a_partly_filled_open_sell_reserves_only_its_remainder(tmp_path):
    v, client = _seller(tmp_path, "10", _open_sell("c-1", "10", filled="6", status="PARTIAL_FILLED"))
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        v.submit("SPY", -7.0, "t")
    assert len(client.placed()) == 0
    assert v.submit("SPY", -6.0, "t2")["status"] == "accepted" and len(client.placed()) == 1


@pytest.mark.parametrize("filled", ["junk", None, "-1", "11"])
def test_an_open_sell_whose_fill_is_unreadable_reserves_its_whole_total(tmp_path, filled):
    v, client = _seller(tmp_path, "10", _open_sell("c-1", "8", filled=filled))
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        v.submit("SPY", -3.0, "t")
    assert len(client.placed()) == 0
    assert v.submit("SPY", -2.0, "t2")["status"] == "accepted" and len(client.placed()) == 1


@pytest.mark.parametrize("total", ["junk", None, "nan", "1e999"])
def test_an_open_sell_with_an_unreadable_total_stops_the_sale(tmp_path, total):
    v, client = _seller(tmp_path, "10", _open_sell("c-1", total))
    with pytest.raises(wb.NotTransmitted):
        v.submit("SPY", -1.0, "t")
    assert len(client.placed()) == 0


def test_a_failed_open_order_read_stops_the_sale(tmp_path):
    client = FakeClient(positions=[{"symbol": "SPY", "instrument_type": "EQUITY", "quantity": "10"}],
                        open_pages={None: ServerError("SYSTEM_BUSY", 500)})
    v = wb.WebullVenue(KEY, SECRET, client=client, confirmation_file=tmp_path / "x.json")
    with pytest.raises(wb.NotTransmitted):
        v.submit("SPY", -1.0, "t")
    assert len(client.placed()) == 0 and len(_opens(client)) == 1
    v, client = _seller(tmp_path, "10", open_page={"data": ["junk"]})
    with pytest.raises(wb.NotTransmitted):
        v.submit("SPY", -1.0, "t")
    assert len(client.placed()) == 0


def test_open_buys_other_symbols_and_other_instruments_reserve_nothing(tmp_path):
    v, client = _seller(tmp_path, "10", _open_sell("c-1", "10", side="BUY"), _open_sell("c-2", "10", symbol="QQQ"),
                        _open_sell("c-3", "10", kind="OPTION"))
    assert v.submit("SPY", -10.0, "t")["status"] == "accepted" and len(client.placed()) == 1


@pytest.mark.parametrize("kind", [None, "", "equity", "STOCK"])
def test_an_open_order_for_the_symbol_with_no_known_type_stops_the_sale(tmp_path, kind):
    v, client = _seller(tmp_path, "10", _open_sell("c-1", "1", kind=kind))
    with pytest.raises(wb.NotTransmitted):
        v.submit("SPY", -1.0, "t")
    assert len(client.placed()) == 0


def test_an_open_order_with_an_unknown_side_for_the_symbol_stops_the_sale(tmp_path):
    v, client = _seller(tmp_path, "10", _open_sell("c-1", "1", side="HOLD"))
    with pytest.raises(wb.NotTransmitted):
        v.submit("SPY", -1.0, "t")
    assert len(client.placed()) == 0


def test_an_open_crypto_sell_reserves_the_crypto_holding(tmp_path):
    v, client = _seller(tmp_path, "1", _open_sell("c-1", "0.6", symbol="BTCUSD", kind="CRYPTO"), symbol="BTCUSD", kind="CRYPTO")
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        v.submit("BTC-USD", -0.5, "t")
    assert len(client.placed()) == 0
    assert v.submit("BTC-USD", -0.4, "t2")["status"] == "accepted" and len(client.placed()) == 1


def test_a_buy_makes_no_open_order_read(tmp_path):
    v, client = _seller(tmp_path, "10", _open_sell("c-1", "10"))
    assert v.submit("SPY", 5.0, "t")["status"] == "accepted"
    assert _opens(client) == [] and len(client.placed()) == 1
    assert not [c for c in client.calls if isinstance(c, tuple) and c[0] == "positions"]


def test_open_orders_give_the_instrument_type_as_given(tmp_path):
    v, _ = _seller(tmp_path, "1", _open_sell("c-1", "1", kind="OPTION"), _open_sell("c-2", "1", kind=None), _open_sell("c-3", "1", kind="equity"))
    assert [o["instrument_type"] for o in v.open_orders()] == ["OPTION", None, "equity"]


PK, PS, PA = "Kq7Zplantedkey0001", "Sx9Wplantedsecret0002", "ACC-0001-1234"
PLANT = f"key={PK} secret={PS} account={PA}"


class Planted(Exception):
    def __init__(self, **fields):
        super().__init__(PLANT)
        self.__dict__.update(fields)

    def __str__(self):
        return PLANT


PLANT_KINDS = ["sdk_text", "sdk_text_padded", "fields", "message", "webull_error", "not_transmitted", "body", "body_padded", "status"]
RAISING_KINDS = PLANT_KINDS[:6]
CARRIED_KINDS = {"sdk_text", "fields", "webull_error", "not_transmitted", "body", "status"}      # their text reaches a message


def _planted(kind):
    """What a failing SDK call does, for each place a planted text can come from: the SDK's error text, an
    exception's other fields, an exception's own message, an error body, the HTTP status."""
    if kind == "sdk_text":
        raise Planted(error_code="SYSTEM_BUSY", http_status=417, error_msg=PLANT)
    if kind == "sdk_text_padded":                                         # the secrets straddle the cut at 400 characters
        raise Planted(error_code="SYSTEM_BUSY", http_status=417, error_msg="x" * 390 + PLANT)
    if kind == "fields":
        raise Planted(error_code=PLANT, http_status=PLANT, error_msg="")
    if kind == "message":
        raise RuntimeError(PLANT)
    if kind == "webull_error":
        raise wb.WebullError(PLANT)
    if kind == "not_transmitted":
        raise wb.NotTransmitted(PLANT)
    if kind == "body":
        return Response({"error_code": PLANT, "message": PLANT}, 417)
    if kind == "body_padded":                                             # straddling the cut at 120 characters
        return Response({"error_code": "x" * 110 + PLANT, "message": "y" * 115 + PLANT}, 417)
    return Response({}, PLANT)                                            # "status"


def _leaks(text, secrets=(PK, PS, PA)):
    return [s[i:i + 8] for s in secrets for i in range(len(s) - 7) if s[i:i + 8] in text]


ENDPOINTS = ["accounts", "balance", "positions", "open", "detail", "cancel", "place"]
PUBLIC = {"accounts": lambda v: v.accounts(), "balance": lambda v: v.balance(), "positions": lambda v: v.positions(),
          "other_positions": lambda v: v.other_positions(), "cash": lambda v: v.cash(), "check": lambda v: v.check(),
          "open_orders": lambda v: v.open_orders(), "cancel": lambda v: v.cancel("t"), "cancel_all_open": lambda v: v.cancel_all_open(),
          "order_status": lambda v: v.order_status("t"), "submit_buy": lambda v: v.submit("SPY", 1.0, "t"),
          "submit_sell": lambda v: v.submit("SPY", -1.0, "t2")}


def _planted_client(kind, endpoint):
    client = FakeClient(positions=[{"symbol": "SPY", "instrument_type": "EQUITY", "quantity": "5"}],
                        open_pages={None: {"data": [detail(order("SUBMITTED", cid="c-1"), cid="c-1")]}},
                        details={wb.client_order_id("t"): Response(detail(order("FILLED")))})
    fail = lambda *a: _planted(kind)                                      # noqa: E731
    target = {"accounts": (client.account_v2, "get_account_list"), "balance": (client.account_v2, "get_account_balance"),
              "positions": (client.account_v2, "get_account_position"), "open": (client.order_v3, "list_order_open"),
              "detail": (client.order_v3, "get_order_detail"), "cancel": (client.order_v3, "cancel_order"),
              "place": (client.order_v3, "place_order")}[endpoint]
    setattr(*target, fail)
    return client


@pytest.mark.parametrize("kind", PLANT_KINDS)
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_no_secret_planted_in_an_error_text_reaches_any_message(tmp_path, kind, endpoint):
    texts = []
    for name, call in PUBLIC.items():
        v = wb.WebullVenue(PK, PS, client=_planted_client(kind, endpoint), account_id=PA, confirmation_file=tmp_path / "x.json")
        try:
            out = call(v)
        except wb.WebullError as exc:
            texts.append((name, str(exc)))
            continue
        if name == "check":
            texts.append((name, json.dumps(out, default=str)))
        elif name == "cancel_all_open":
            texts.append((name, json.dumps([o.get("error") for o in out])))
    v = wb.WebullVenue(PK, PS, client=_planted_client(kind, endpoint), confirmation_file=tmp_path / "x.json")   # account not yet known
    try:
        v.account_id
    except wb.WebullError as exc:
        texts.append(("account_id", str(exc)))
        assert not _leaks(str(exc), (PK, PS)), str(exc)
    for name, text in texts:
        if name != "account_id":
            assert not _leaks(text), f"{name}: {text}"
    if kind in CARRIED_KINDS and endpoint != "accounts" and (kind, endpoint) != ("status", "place"):   # rev 6 D4: not repeated
        assert any("<withheld>" in t or "<code withheld>" in t for _, t in texts), texts   # the text is carried, the secrets withheld


@pytest.mark.parametrize("kind", RAISING_KINDS)
@pytest.mark.parametrize("where", ["api", "trade"])
def test_the_constructor_never_repeats_a_secret(tmp_path, monkeypatch, kind, where):
    class ApiClient:
        def __init__(self, key, secret, region):
            if where == "api":
                _planted(kind)

        def add_endpoint(self, region, host):
            pass

        def set_token_dir(self, token_dir):
            pass

    class TradeClient:
        def __init__(self, api):
            _planted(kind)

    monkeypatch.setitem(sys.modules, "webull.core.client", types.SimpleNamespace(ApiClient=ApiClient))
    monkeypatch.setitem(sys.modules, "webull.trade.trade_client", types.SimpleNamespace(TradeClient=TradeClient))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(wb.WebullError) as exc:
        wb.WebullVenue(PK, PS, account_id=PA, confirmation_file=tmp_path / "x.json")
    assert not _leaks(str(exc.value)), str(exc.value)
    if kind in CARRIED_KINDS - {"webull_error", "not_transmitted"}:
        assert "<withheld>" in str(exc.value)


class Unreadable(Exception):
    """An exception none of whose fields can be read."""
    @property
    def error_code(self):
        raise RuntimeError("error_code cannot be read")

    http_status = error_msg = error_code

    def __str__(self):
        raise RuntimeError("str cannot be read")


def test_an_exception_whose_fields_cannot_be_read_leaves_only_as_webull_error(tmp_path, monkeypatch):
    hits = []

    def boom(*a):
        hits.append(a)
        raise Unreadable()
    for endpoint, call, want in (("place", lambda v: v.submit("SPY", 1.0, "t"), wb.WebullError),
                                 ("positions", lambda v: v.submit("SPY", -1.0, "t"), wb.NotTransmitted),
                                 ("open", lambda v: v.submit("SPY", -1.0, "t"), wb.NotTransmitted),
                                 ("detail", lambda v: v.order_status("t"), wb.WebullError),
                                 ("accounts", lambda v: v.accounts(), wb.WebullError),
                                 ("cancel", lambda v: v.cancel("t"), wb.WebullError)):
        client = _planted_client("sdk_text", endpoint)
        setattr(*{"place": (client.order_v3, "place_order"), "positions": (client.account_v2, "get_account_position"),
                  "open": (client.order_v3, "list_order_open"), "detail": (client.order_v3, "get_order_detail"),
                  "accounts": (client.account_v2, "get_account_list"), "cancel": (client.order_v3, "cancel_order")}[endpoint], boom)
        v = wb.WebullVenue(KEY, SECRET, client=client, account_id="ACC-0001-1234", confirmation_file=tmp_path / "x.json")
        with pytest.raises(wb.WebullError) as exc:
            call(v)
        assert isinstance(exc.value, want) and (want is wb.WebullError) == (type(exc.value) is wb.WebullError), endpoint
        placed = len([h for h in hits if len(h) == 2 and isinstance(h[1], list)]) if endpoint == "place" else len(client.placed())
        assert placed == (1 if endpoint == "place" else 0), endpoint
        hits.clear()
    v = wb.WebullVenue(KEY, SECRET, client=_planted_client("sdk_text", "cancel"), confirmation_file=tmp_path / "x.json")
    v._client.order_v3.cancel_order = boom
    assert v.cancel_all_open()[0]["status"] == "error"

    class ApiClient:
        def __init__(self, key, secret, region):
            raise Unreadable()
    monkeypatch.setitem(sys.modules, "webull.core.client", types.SimpleNamespace(ApiClient=ApiClient))
    monkeypatch.setitem(sys.modules, "webull.trade.trade_client", types.SimpleNamespace(TradeClient=object))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(wb.WebullError):
        wb.WebullVenue(KEY, SECRET, confirmation_file=tmp_path / "x.json")


def test_a_combo_that_names_another_order_is_never_adopted(tmp_path):
    bare = order("FILLED", filled_quantity="1")
    bare.pop("client_order_id")
    for body in (detail(bare, cid="someone-else"), [detail(bare, cid="someone-else")],
                 detail(order("FILLED"), cid="someone-else"), {**detail(bare), "client_order_id": None}):
        for confirmed in (False, True):
            v, _ = status_venue(tmp_path, Response(body), confirmed_not_found=confirmed)
            with pytest.raises(wb.WebullError, match="another"):
                v.order_status("t")
    assert status_venue(tmp_path, Response(detail(bare)))[0].order_status("t")["state"] == "filled"     # our own combo: read


@pytest.mark.parametrize("body", [{"orders": [], "code": "SYSTEM_BUSY"}, {"orders": [], "msg": "busy"}, {"orders": [], "data": None},
                                  {"orders": [], "success": False}, {"orders": [], "combo_type": "NORMAL", "error": "x"}])
def test_with_the_flag_on_an_empty_list_beside_any_other_key_raises(tmp_path, body):
    v, _ = status_venue(tmp_path, Response(body), confirmed_not_found=True)
    with pytest.raises(wb.WebullError):
        v.order_status("t")
    ok = {"orders": [], "client_order_id": wb.client_order_id("t"), "combo_order_id": "C-1", "combo_type": "NORMAL"}
    assert status_venue(tmp_path, Response(ok), confirmed_not_found=True)[0].order_status("t") is None


def test_two_balance_entries_in_the_wanted_currency_raise(tmp_path):
    body = {"total_asset_currency": "USD", "total_cash_balance": "10",
            "account_currency_assets": [{"currency": "USD", "cash_balance": "10", "settled_cash": "9", "buying_power": "8"},
                                        {"currency": "usd", "cash_balance": "5", "settled_cash": "5", "buying_power": "5"}]}
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(balance=body), confirmation_file=tmp_path / "x.json")
    with pytest.raises(wb.WebullError, match="more than one"):
        v.cash()


# ---- T9 rev 6: only harmless text leaves the adapter; no raw answer is handed back; the rev 5 verification notes

import base64 as _b64                                                     # noqa: E402
import random as _random                                                  # noqa: E402
from urllib.parse import quote as _quote                                  # noqa: E402


def _secret_forms(s):
    """The forms the ticket names: as given, lower, upper, URL-encoded, base64 (standard and URL-safe, with and
    without padding) and hex. Computed here independently of the module."""
    raw = s.encode()
    b64s = [_b64.b64encode(raw).decode(), _b64.urlsafe_b64encode(raw).decode()]
    return {s, s.lower(), s.upper(), _quote(s, safe=""), *b64s, *(b.rstrip("=") for b in b64s), raw.hex()}


def _runs_of(*secrets):
    runs = set()
    for s in secrets:
        for form in _secret_forms(s):
            f = form.lower()
            runs.update(f[i:i + 6] for i in range(len(f) - 5))
    return runs


def _leaked(text, runs):
    """Any 6-character run of a form of a secret in the text, compared without regard to case, also with the
    whitespace taken out (a secret split across a line break)."""
    low = str(text).lower()
    for variant in (low, "".join(low.split())):
        for i in range(len(variant) - 5):
            if variant[i:i + 6] in runs:
                return variant[i:i + 6]
    return None


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def _disguises(s):
    half = len(s) // 2
    return [s, s[:10], s[-10:], s.upper(), s.lower(), _quote(s, safe=""), _quote(_quote(s, safe=""), safe=""),
            _b64.b64encode(s.encode()).decode(), s.encode().hex(), s[:half] + "\n" + s[half:]]


def _random_secret(rng, fmt):
    n = rng.randint(12, 64)
    alphabet = {"hex": "0123456789abcdef", "letters": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
                "lower": "abcdefghijklmnopqrstuvwxyz", "digits": "0123456789",
                "mixed": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_",
                "base64": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"}[fmt]
    s = "".join(rng.choice(alphabet) for _ in range(n))
    return s + "==" if fmt == "base64" and rng.random() < 0.5 else s


SECRET_FORMATS = ["hex", "letters", "lower", "digits", "mixed", "base64"]
FIXED_TEXTS = [wb.client_order_id(t) for t in ("t", "t2")] + ["c-1", "WB-1", "C-1", "ACC-0001-1234"]


def _secret_set(rng):
    """Three random secrets (key, secret, account id) whose forms do not happen to share a run with the fixed texts
    of these tests (our own client order ids): such a match would not be a leak."""
    while True:
        trio = tuple(_random_secret(rng, rng.choice(SECRET_FORMATS)) for _ in range(3))
        runs = _runs_of(*trio)
        if not any(_leaked(f, runs) for f in FIXED_TEXTS) and len(set(trio)) == 3:
            return trio


SOURCES = ["sdk_msg", "sdk_fields", "exc_message", "class_name", "webull_error", "not_transmitted", "error_body", "status",
           "answer_all", "answer_but_symbol"]


def _answer(endpoint, plant, keep_symbol):
    """A 200 answer of the endpoint with every documented string field set to the planted text."""
    sym = "SPY" if keep_symbol else plant
    item = {"client_order_id": plant, "order_id": plant, "symbol": sym, "side": plant, "status": plant, "instrument_type": plant,
            "total_quantity": plant, "filled_quantity": plant, "filled_price": plant, "combo_type": plant,
            "commission": {"actual_commission": plant}, "fees": [{"type": plant, "actual_value": plant}]}
    combo = {"client_order_id": plant, "combo_order_id": plant, "combo_type": plant, "orders": [item]}
    return {"accounts": [{"account_id": plant, "account_type": plant, "user_id": plant}],
            "balance": {"total_asset_currency": plant, "total_cash_balance": plant,
                        "account_currency_assets": [{"currency": plant, "cash_balance": plant, "settled_cash": plant, "buying_power": plant}]},
            "positions": [{"symbol": sym, "instrument_type": plant, "quantity": plant, "currency": plant, "position_id": plant},
                          {"symbol": sym, "instrument_type": "OPTION", "quantity": "1", "position_id": plant}],
            "open": {"data": [combo], "pagination_key": plant},
            "detail": combo,
            "cancel": {"client_order_id": plant, "order_id": plant},
            "place": {"order_id": plant, "client_order_id": plant, "combo_order_id": plant}}[endpoint]


def _plant_source(source, plant):
    """A failing (or answering) SDK call for the source: the planted text in the SDK's error message, the
    exception's other fields, its own message, its class name, a WebullError the client raises, an error body,
    the HTTP status, or a 200 answer."""
    def call(*a, endpoint):
        if source == "sdk_msg":
            raise ServerException("SYSTEM_BUSY", 417, plant)
        if source == "sdk_fields":
            exc = ServerException(plant, plant, "")
            exc.request_id = plant
            raise exc
        if source == "exc_message":
            raise RuntimeError(plant)
        if source == "class_name":
            raise type(plant, (Exception,), {"error_msg": "failed", "error_code": "SYSTEM_BUSY"})()
        if source == "webull_error":
            raise wb.WebullError(plant)
        if source == "not_transmitted":
            raise wb.NotTransmitted(plant)
        if source == "error_body":
            return Response({"error_code": plant, "message": plant, "msg": plant}, 417)
        if source == "status":
            return Response({}, plant)
        return Response(_answer(endpoint, plant, source == "answer_but_symbol"))
    return call


ENDPOINT_TARGETS = {"accounts": ("account_v2", "get_account_list"), "balance": ("account_v2", "get_account_balance"),
                    "positions": ("account_v2", "get_account_position"), "open": ("order_v3", "list_order_open"),
                    "detail": ("order_v3", "get_order_detail"), "cancel": ("order_v3", "cancel_order"),
                    "place": ("order_v3", "place_order")}
METHODS = {"accounts": lambda v: v.accounts(), "balance": lambda v: v.balance(), "positions": lambda v: v.positions(),
           "other_positions": lambda v: v.other_positions(), "cash": lambda v: v.cash(), "check": lambda v: v.check(),
           "open_orders": lambda v: v.open_orders(), "cancel": lambda v: v.cancel("t"), "cancel_all_open": lambda v: v.cancel_all_open(),
           "order_status": lambda v: v.order_status("t"), "submit_buy": lambda v: v.submit("SPY", 1.0, "t"),
           "submit_sell": lambda v: v.submit("SPY", -1.0, "t2")}


def _planted_venue(tmp_path, key, secret, account, source, endpoint, plant, known=True):
    client = FakeClient(accounts=[{"account_id": account, "account_type": "CASH"}],
                        positions=[{"symbol": "SPY", "instrument_type": "EQUITY", "quantity": "5"}],
                        open_pages={None: {"data": [detail(order("SUBMITTED", cid="c-1"), cid="c-1")]}},
                        details={wb.client_order_id("t"): Response(detail(order("FILLED")))})
    holder, name = ENDPOINT_TARGETS[endpoint]
    fn = _plant_source(source, plant)
    setattr(getattr(client, holder), name, lambda *a: fn(*a, endpoint=endpoint))
    return wb.WebullVenue(key, secret, client=client, account_id=account if known else None, confirmation_file=tmp_path / "x.json")


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(self.format(record))
        except Exception:
            self.lines.append(repr(record.__dict__))


@pytest.fixture
def debug_logs():
    """Every logger at DEBUG into one collector, for the duration of a test."""
    handler, root = _Collect(), logging.getLogger()
    old = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(old)


def _run_everything(tmp_path, key, secret, account, source, endpoint, plant):
    """Every public method on a venue whose one endpoint carries the planted text; what each says or returns."""
    seen = []
    for name, call in METHODS.items():
        v = _planted_venue(tmp_path, key, secret, account, source, endpoint, plant)
        try:
            seen.append((name, "returned", call(v)))
        except wb.WebullError as exc:
            seen.append((name, "raised", str(exc)))
    v = _planted_venue(tmp_path, key, secret, account, source, endpoint, plant, known=False)
    try:
        seen.append(("account_id", "returned", v.account_id))
    except wb.WebullError as exc:
        seen.append(("account_id", "raised", str(exc)))
    return seen


def _assert_no_leak(seen, key, secret, account, logs):
    all_runs, credential_runs = _runs_of(key, secret, account), _runs_of(key, secret)
    for name, how, what in seen:
        # accounts() and account_id hand back the account id itself (B): only the key and secret are checked there
        runs = credential_runs if name in ("accounts", "account_id") and how == "returned" else all_runs
        for text in _strings(what):
            hit = _leaked(text, runs)
            assert hit is None, f"{name} {how} a run {hit!r} of a secret: {text[:300]!r}"
    for line in logs.lines:
        assert _leaked(line, all_runs) is None, line[:300]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("endpoint", sorted(ENDPOINT_TARGETS))
def test_no_disguised_secret_leaves_by_any_text_source_or_answer(tmp_path, debug_logs, source, endpoint):
    key, secret, account = "Kq7Zplantedkey0001", "Sx9Wplantedsecret0002", "ACC-0001-1234"
    plant = " ".join(d for s in (key, secret, account) for d in _disguises(s)) + " Insufficient buying power for this order"
    seen = _run_everything(tmp_path, key, secret, account, source, endpoint, plant)
    _assert_no_leak(seen, key, secret, account, debug_logs)


def test_random_secrets_in_every_format_never_leave(tmp_path, debug_logs):
    rng = _random.Random(20261112)
    rounds = 0
    for _ in range(220):
        key, secret, account = _secret_set(rng)
        target = rng.choice((key, secret, account))
        plant = rng.choice(_disguises(target) + [" ".join(_disguises(target))])
        source, endpoint = rng.choice(SOURCES), rng.choice(sorted(ENDPOINT_TARGETS))
        seen = _run_everything(tmp_path, key, secret, account, source, endpoint, plant)
        _assert_no_leak(seen, key, secret, account, debug_logs)
        rounds += 1
    assert rounds >= 200


@pytest.mark.parametrize("source", ["sdk_msg", "sdk_fields", "exc_message", "class_name", "webull_error", "not_transmitted"])
@pytest.mark.parametrize("where", ["api", "trade"])
def test_the_constructor_lets_no_disguised_secret_through(tmp_path, monkeypatch, debug_logs, source, where):
    rng = _random.Random(hash((source, where)) & 0xFFFF)
    for round_ in range(20):
        key, secret, account = ("Kq7Zplantedkey0001", "Sx9Wplantedsecret0002", "ACC-0001-1234") if round_ == 0 else _secret_set(rng)
        plant = " ".join(d for s in (key, secret, account) for d in _disguises(s))
        fail = _plant_source(source, plant)

        class ApiClient:
            def __init__(self, k, s, region):
                if where == "api":
                    fail(endpoint="accounts")

            def add_endpoint(self, region, host):
                pass

            def set_token_dir(self, token_dir):
                pass

        class TradeClient:
            def __init__(self, api):
                fail(endpoint="accounts")

        monkeypatch.setitem(sys.modules, "webull.core.client", types.SimpleNamespace(ApiClient=ApiClient))
        monkeypatch.setitem(sys.modules, "webull.trade.trade_client", types.SimpleNamespace(TradeClient=TradeClient))
        monkeypatch.chdir(tmp_path)
        with pytest.raises(wb.WebullError) as exc:
            wb.WebullVenue(key, secret, account_id=account, confirmation_file=tmp_path / "x.json")
        _assert_no_leak([("constructor", "raised", str(exc.value))], key, secret, account, debug_logs)


BROKER_SENTENCES = [
    "Insufficient buying power for this order",
    "The order quantity exceeds the maximum allowed.",
    "Market is closed. Please try again during trading hours.",
    "Order rejected: price is outside the allowed range",
    "Account is restricted from trading",
    "The symbol is not tradable",
    "Too many requests, please slow down",
    "Short selling is not supported for this account.",
    "Limit price must be a multiple of the tick size.",
    "Order not found",
    "The order has already been filled and cannot be cancelled.",
    "Invalid quantity: must be greater than 0",
    "Request timed out after 30 seconds",
    "Service temporarily unavailable (503)",
    "Fractional shares are only supported for market orders",
    "Crypto trading is not enabled for this account",
    "The time in force GTC is not supported for this order type",
    "Insufficient position to sell",
    "Duplicate client order id",
    "Order value exceeds the daily limit of 25000.00",
    "Trading session is not open; extended hours orders are not allowed",
    "You don't have permission to access this resource",
    "Pattern day trader rule: equity below 25000 for margin day trading",
    "ORDER_NOT_CANCELLABLE: the order can no longer be cancelled",
]


@pytest.mark.parametrize("sentence", BROKER_SENTENCES)
def test_harmless_broker_messages_come_through_unchanged(tmp_path, sentence):
    secrets = wb._secrets_of("Kq7Zplantedkey0001", "Sx9Wplantedsecret0002", "ACC-0001-1234")
    assert wb._gate(sentence, secrets) == sentence
    assert wb._last_check(sentence, secrets) == sentence
    client = FakeClient()
    client.account_v2.get_account_list = lambda: (_ for _ in ()).throw(ServerException("SYSTEM_BUSY", 417, sentence))
    with pytest.raises(wb.WebullError) as exc:
        wb.WebullVenue("Kq7Zplantedkey0001", "Sx9Wplantedsecret0002", client=client, confirmation_file=tmp_path / "x.json").accounts()
    assert sentence in str(exc.value) and "SYSTEM_BUSY" in str(exc.value)
    client = FakeClient()
    client.account_v2.get_account_list = lambda: Response({"error_code": "SYSTEM_BUSY", "message": sentence}, 417)
    with pytest.raises(wb.WebullError) as exc:
        wb.WebullVenue("Kq7Zplantedkey0001", "Sx9Wplantedsecret0002", client=client, confirmation_file=tmp_path / "x.json").accounts()
    assert sentence in str(exc.value)


def test_a_transport_failure_keeps_its_words_and_withholds_the_url():
    secrets = wb._secrets_of("Kq7Zplantedkey0001", "Sx9Wplantedsecret0002")
    assert wb._gate("Max retries exceeded with url: /trading/orders/get?account_id=1", secrets) == "Max retries exceeded with url: <withheld>"


@pytest.mark.parametrize("token", ["abc123", "x" * 25, "1234567890123", "%41BC", "a%2Fb", "QWxhZGRpbg==", "QWxhZGRpbg", "deadbeefcafe",
                                   "https://api.webull.com/x", "/usr/local/bin", "api.webull.com", "C:\\\\temp", "naïve", "tab\x01x",
                                   "mIxEdCaSe", "SDK.HttpError", "x-app-key=abc", "<script>", "12.34.56", "+-5", "A" * 49])
def test_the_gate_withholds_every_shape_that_is_not_plainly_harmless(token):
    secrets = wb._secrets_of("Kq7Zplantedkey0001", "Sx9Wplantedsecret0002")
    assert wb._gate(f"before {token} after", secrets) == "before <withheld> after"


def test_an_error_code_is_shown_only_in_the_code_shape():
    secrets = wb._secrets_of("Kq7Zplantedkey0001", "Sx9Wplantedsecret0002")
    assert wb._code("ORDER_NOT_FOUND", secrets) == "ORDER_NOT_FOUND"
    for bad in ("SDK.HttpError", "order_not_found", "E1001", "A" * 49, 417, None, "KQ7ZPLANTEDKEY", " BUSY"):
        assert wb._code(bad, secrets) == "<code withheld>"


def test_the_last_check_works_on_its_own(tmp_path, monkeypatch):
    rng = _random.Random(7)
    for _ in range(50):
        key, secret, account = _secret_set(rng)
        secrets = wb._secrets_of(key, secret, account)
        for s in (key, secret, account):
            for d in _disguises(s):
                assert wb._last_check(f"Webull said: {d} (417)", secrets) == "text withheld", d
        assert wb._last_check("Insufficient buying power for this order", secrets) == "Insufficient buying power for this order"
    # with the filter bypassed, the last check alone still keeps every secret out of what leaves a public method
    monkeypatch.setattr(wb, "_gate", lambda value, secrets, limit=160: wb._plain(value)[:limit])
    key, secret, account = "Kq7Zplantedkey0001", "Sx9Wplantedsecret0002", "ACC-0001-1234"
    for source in ("sdk_msg", "webull_error", "error_body", "class_name"):
        for endpoint in ("accounts", "detail", "place"):
            seen = _run_everything(tmp_path, key, secret, account, source, endpoint, f"denied {key}")
            _assert_no_leak(seen, key, secret, account, _Collect())
            # rev 7: a class name is judged by its own rule (_class_name), not by the gate, so it is withheld there
            assert any(how == "raised" and ("text withheld" in what or (source == "class_name" and "<withheld>" in what))
                       for _, how, what in seen), (source, endpoint)


def test_submit_and_cancels_hand_back_no_raw_answer(tmp_path):
    client = FakeClient(place_body={"order_id": "WB-9", "client_order_id": wb.client_order_id("t"), "echo": "x-app-key=" + KEY},
                        open_pages={None: {"data": [detail(order("SUBMITTED", cid="c-1", order_id="WB-1"), cid="c-1")]}})
    v = wb.WebullVenue(KEY, SECRET, client=client, confirmation_file=tmp_path / "x.json")
    out = v.submit("SPY", 1.0, "t")
    assert set(out) == {"status", "client_order_id", "venue_order_id", "environment", "side", "quantity"} and out["venue_order_id"] == "WB-9"
    assert v.cancel("t") == {"status": "cancel requested"}
    entries = v.cancel_all_open()
    assert entries == [{"client_order_id": "c-1", "venue_order_id": "WB-1", "symbol": "SPY", "status": "cancel requested"}]


@pytest.mark.parametrize("vid, kept", [("WB-9", "WB-9"), ("1234567890", "1234567890"), (1234567890, "1234567890"), ("ab_C-9", "ab_C-9"),
                                       ("WB 9", None), ("x" * 65, None), ("a/b", None), ("WB-9\n", None), (True, None),
                                       ("id-Kq7Zplantedkey0001", None), ("id-" + "kq7zplantedkey0001".upper(), None)])
def test_a_venue_order_id_is_kept_only_in_a_harmless_shape(tmp_path, vid, kept):
    client = FakeClient(place_body={"order_id": vid})
    v = wb.WebullVenue("Kq7Zplantedkey0001", "Sx9Wplantedsecret0002", client=client, confirmation_file=tmp_path / "x.json")
    assert v.submit("SPY", 1.0, "t")["venue_order_id"] == kept
    status = status_venue(tmp_path, detail(order("FILLED", order_id=vid)))[0]
    status = wb.WebullVenue("Kq7Zplantedkey0001", "Sx9Wplantedsecret0002", client=status._client, confirmation_file=tmp_path / "x.json")
    assert status.order_status("t")["venue_order_id"] == kept


def test_balance_returns_only_the_documented_fields_as_numbers_and_currency_codes(tmp_path):
    body = {"total_asset_currency": "USD", "total_cash_balance": "1500.25", "total_net_liquidation_value": "2000", "note": "x-app-key=" + KEY,
            "account_currency_assets": [{"currency": "USD", "cash_balance": "1500.25", "settled_cash": "1400", "buying_power": "junk",
                                         "margin_note": "x"}, {"currency": "US Dollar", "cash_balance": "1"}]}
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(balance=body), confirmation_file=tmp_path / "x.json")
    assert v.balance() == {"total_asset_currency": "USD", "total_cash_balance": 1500.25,
                           "account_currency_assets": [{"currency": "USD", "cash_balance": 1500.25, "settled_cash": 1400.0}, {"cash_balance": 1.0}]}
    assert v.cash() == {"cash": 1500.25, "settled_cash": 1400.0, "buying_power": None, "currency": "USD"}


def test_accounts_return_only_the_id_and_a_short_type(tmp_path):
    rows = [{"account_id": "ACC-0001-1234", "account_type": "CASH", "user_id": "u-" + KEY, "nickname": "my account"},
            {"account_id": "ACC 2", "account_type": "a type that is far too long to be a short word"},
            {"account_id": "id-" + KEY, "account_type": "MARGIN"}]
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(accounts=rows), confirmation_file=tmp_path / "x.json")
    assert v.accounts() == [{"account_id": "ACC-0001-1234", "account_type": "CASH"}, {"account_id": None, "account_type": None},
                            {"account_id": None, "account_type": "MARGIN"}]


@pytest.mark.parametrize("raw, kept", [("PARTIAL FILLED", "PARTIAL FILLED"), ("FILLED", "FILLED"), ("filled", "<withheld>"),
                                       ("FILLED x-app-key", "<withheld>"), ("A" * 49, "<withheld>"), (7, "<withheld>"), (None, None)])
def test_a_raw_status_is_kept_only_in_the_code_shape(tmp_path, raw, kept):
    assert status_venue(tmp_path, detail(order(raw)))[0].order_status("t")["raw_status"] == kept
    page = {"data": [detail(order(raw, cid="c-1"), cid="c-1")]}
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(open_pages={None: page}), confirmation_file=tmp_path / "x.json")
    assert v.open_orders()[0]["raw_status"] == kept


def test_main_prints_only_gated_text(tmp_path, monkeypatch, capsys):
    key, secret = "Kq7Zplantedkey0001", "Sx9Wplantedsecret0002"
    client = FakeClient(accounts=[{"account_id": "ACC-0001-1234", "account_type": "CASH"}])
    client.account_v2.get_account_balance = lambda a: Response({"error_code": "x" + key, "message": f"denied {key.lower()}"}, 417)
    venue = wb.WebullVenue(key, secret, client=client, confirmation_file=tmp_path / "x.json")
    monkeypatch.setattr(wb.WebullVenue, "from_secrets", classmethod(lambda cls, environ=None, **kw: venue))
    assert wb.main(["check"]) == 0
    printed = capsys.readouterr()
    assert _leaked(printed.out + printed.err, _runs_of(key, secret)) is None and '"environment": "sandbox"' in printed.out

    def boom(cls, environ=None, **kw):
        raise type(f"Err{key}", (Exception,), {})(f"could not read {_quote(key, safe='')} from /home/x/.env")
    monkeypatch.setattr(wb.WebullVenue, "from_secrets", classmethod(boom))
    assert wb.main(["check"]) == 2
    printed = capsys.readouterr()
    assert _leaked(printed.out + printed.err, _runs_of(key, secret)) is None and printed.err.startswith("not ready:")
    assert "/home/x" not in printed.err


# ---- D1 to D8

class _FillingClient(FakeClient):
    """Holds 10 SPY with an open sell of 10, of which 4 fill at a chosen moment: before any read, after the
    first read of positions or open orders (whichever comes first), or after both."""
    def __init__(self, when):
        super().__init__()
        self.when, self.reads, self.filled = when, 0, when == "before"
        self.account_v2.get_account_position = lambda a: self._read("positions", lambda: [{"symbol": "SPY", "instrument_type": "EQUITY",
                                                                                            "quantity": "6" if self.filled else "10"}])
        self.order_v3.list_order_open = lambda a, k=None: self._read("open", lambda: {"data": [_open_sell("c-1", "10", filled="4" if self.filled else "0")]})

    def _read(self, what, answer):
        self.calls.append((what, "ACC"))
        out = Response(answer())
        self.reads += 1
        if self.when == "between" and self.reads == 1:
            self.filled = True
        return out


@pytest.mark.parametrize("when", ["before", "between", "after"])
def test_open_orders_are_read_before_holdings_so_a_fill_in_the_gap_frees_nothing(tmp_path, when):
    client = _FillingClient(when)
    v = wb.WebullVenue(KEY, SECRET, client=client, confirmation_file=tmp_path / "x.json")
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        v.submit("SPY", -4.0, "t")                                        # nothing is free whenever the fill happens
    assert len(client.placed()) == 0
    reads = [c[0] for c in client.calls if c[0] in ("open", "positions")]
    assert reads == ["open", "positions"]


@pytest.mark.parametrize("row", [{"symbol": "SPY", "instrument_type": "EQUITY", "position": "7"},
                                 {"symbol": "SPY", "instrument_type": "EQUITY", "qty": "7"},
                                 {"symbol": "SPY", "instrument_type": "EQUITY"}])
def test_only_the_documented_quantity_field_is_a_holding(tmp_path, row):
    v, client = venue(tmp_path, positions=[row])
    assert v.positions() == {}
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        v.submit("SPY", -7.0, "t")
    assert len(client.placed()) == 0


def test_a_sale_may_not_exceed_the_free_amount_rounded_down_to_the_step(tmp_path):
    v, client = venue(tmp_path, positions=[{"symbol": "BTCUSD", "instrument_type": "CRYPTO", "quantity": "0.000004799"}])
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        v.submit("BTC-USD", -0.0000048, "t")
    assert v.submit("BTC-USD", -0.00000479, "t2")["status"] == "accepted" and len(client.placed()) == 1
    v, client = venue(tmp_path, positions=[{"symbol": "SPY", "instrument_type": "EQUITY", "quantity": "1.0000099999"}])
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        v.submit("SPY", -1.00001, "t")
    assert len(client.placed()) == 0
    v, client = _seller(tmp_path, "0.3", _open_sell("c-1", "0.1"))
    assert v.submit("SPY", -0.2, "t")["status"] == "accepted" and len(client.placed()) == 1     # 0.3 - 0.1 is 0.2, exactly


class _StatusRaises:
    def __eq__(self, other):
        raise RuntimeError("cannot compare")

    __ne__ = __eq__
    __hash__ = object.__hash__


class _PropertyRaises:
    @property
    def status_code(self):
        raise RuntimeError("no status")


@pytest.mark.parametrize("res", ["COMPARE", "PROPERTY", "TEXT"])
def test_an_unreadable_status_after_the_order_call_is_an_unknown_outcome(tmp_path, res):
    res = {"COMPARE": Response({"order_id": "1"}, _StatusRaises()), "PROPERTY": _PropertyRaises(), "TEXT": Response({"order_id": "1"}, "200")}[res]
    client = FakeClient()
    client.order_v3.place_order = lambda a, orders: (client.calls.append(("place", a, orders)), res)[1]
    v = wb.WebullVenue(KEY, SECRET, client=client, confirmation_file=tmp_path / "x.json")
    with pytest.raises(wb.WebullError, match="outcome unknown") as exc:
        v.submit("SPY", 1.0, "t")
    assert type(exc.value) is wb.WebullError and len(client.placed()) == 1


def test_duplicated_holdings_never_make_other_positions_raise(tmp_path):
    rows = [{"symbol": "SPY", "instrument_type": "EQUITY", "quantity": "5"}, {"symbol": "SPY", "instrument_type": "EQUITY", "quantity": "5"},
            dict(OPTION_ROW)]
    v, client = venue(tmp_path, positions=rows)
    assert v.other_positions() == [{"symbol": "AAPL", "instrument_type": "OPTION", "quantity": 1.0}]
    with pytest.raises(wb.WebullError, match="more than one row"):
        v.positions()
    for sym in ("SPY", "AAPL"):
        with pytest.raises(wb.NotTransmitted):
            v.submit(sym, -1.0, "t")
    assert len(client.placed()) == 0


@pytest.mark.parametrize("name", ["ClientException", "ServerError", "OrderNotFound", "Exception"])
def test_only_the_sdks_server_exception_can_mean_no_such_order(tmp_path, name):
    exc = type(name, (Exception,), {"error_code": "ORDER_NOT_FOUND", "http_status": 404, "error_msg": "order does not exist"})()
    v, _ = status_venue(tmp_path, exc, confirmed_not_found=True)
    with pytest.raises(wb.WebullError):
        v.order_status("t")
    assert status_venue(tmp_path, ServerException("ORDER_NOT_FOUND", 404), confirmed_not_found=True)[0].order_status("t") is None


def test_an_item_whose_client_order_id_is_empty_is_never_adopted(tmp_path):
    item = order("FILLED", filled_quantity="1")
    for given, adopted in (("", False), ("ABSENT", True), (None, True)):
        it = {k: v for k, v in item.items() if k != "client_order_id"}
        if given != "ABSENT":
            it["client_order_id"] = given
        v, _ = status_venue(tmp_path, Response(detail(it)))
        if adopted:
            assert v.order_status("t")["state"] == "filled"
        else:
            with pytest.raises(wb.WebullError):
                v.order_status("t")


@pytest.mark.parametrize("sym", ["ABSENT", None, "", " SPY", "SPY ", "SP Y", "SPY\n", 7, "A" * 25, "SPY;rm", "ŠPY", "SPY%20"])
def test_an_open_order_without_a_clean_symbol_is_an_unexpected_shape(tmp_path, sym):
    item = order("SUBMITTED", cid="c-1", side="SELL", total_quantity="1")
    if sym == "ABSENT":
        item.pop("symbol")
    else:
        item["symbol"] = sym
    v, client = _seller(tmp_path, "10", detail(item, cid="c-1"))
    with pytest.raises(wb.WebullError, match="symbol"):
        v.open_orders()
    with pytest.raises(wb.NotTransmitted):
        v.submit("SPY", -1.0, "t")
    assert len(client.placed()) == 0


@pytest.mark.parametrize("sym", ["ABSENT", None, "", " SPY", "SP Y", 7, "A" * 25, "x-app-key=1"])
def test_a_position_row_without_a_clean_symbol_is_an_unexpected_shape(tmp_path, sym):
    row = {"symbol": sym, "instrument_type": "EQUITY", "quantity": "1"}
    if sym == "ABSENT":
        row.pop("symbol")
    v, client = venue(tmp_path, positions=[row])
    for call in (v.positions, v.other_positions):
        with pytest.raises(wb.WebullError, match="symbol"):
            call()


# ---- T9 rev 7: account ids, base64 offsets, class names, independent last check, clean exceptions, unknown outcome

def test_an_account_type_is_a_short_code_never_an_account_id(tmp_path):
    rows = [{"account_id": "ACC-0001-1234", "account_type": "ACC-0001-1234"}, {"account_id": "ACCOUNTXYZW", "account_type": "CASH"},
            {"account_id": "ACC-3", "account_type": "ACCOUNTXYZW"}, {"account_id": "ACC-4", "account_type": "cash"}]
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(accounts=rows), account_id="ACC-0001-1234", confirmation_file=tmp_path / "x.json")
    assert [a["account_type"] for a in v.accounts()] == [None, "CASH", None, None]
    assert [a["type"] for a in v.check()["accounts"]] == [None, "CASH", None, None]


def test_every_string_handed_back_is_checked_against_every_account_id_seen(tmp_path):
    page = {"data": [detail(order("SUBMITTED", cid="c-1", symbol="ACCOUNTXYZW"), cid="c-1")]}
    client = FakeClient(accounts=[{"account_id": "ACC-0001-1234", "account_type": "CASH"}, {"account_id": "ACCOUNTXYZW", "account_type": "CASH"}],
                        open_pages={None: page})
    v = wb.WebullVenue(KEY, SECRET, client=client, account_id="ACC-0001-1234", confirmation_file=tmp_path / "x.json")
    v.accounts()                                                          # the venue has now seen ACCOUNTXYZW
    with pytest.raises(wb.WebullError, match="symbol"):
        v.open_orders()


def test_base64_at_every_byte_offset_is_found_for_every_piece_of_six_or_more(tmp_path):
    rng = _random.Random(31)
    checked = 0
    for _ in range(60):
        secret = _random_secret(rng, rng.choice(SECRET_FORMATS))
        gate = wb._secrets_of(secret)
        for offset in range(3):
            for _ in range(4):
                length = rng.randint(6, len(secret))
                start = rng.randint(0, len(secret) - length)
                piece = secret[start:start + length].encode()
                prefix = bytes(rng.randrange(256) for _ in range(offset + 3 * rng.randint(0, 2)))
                suffix = bytes(rng.randrange(256) for _ in range(rng.randint(0, 5)))
                for enc in (_b64.b64encode(prefix + piece + suffix).decode(), _b64.urlsafe_b64encode(prefix + piece + suffix).decode().rstrip("=")):
                    assert gate.found(enc.lower()), (secret, offset, piece, enc)
                    assert wb._last_check(f"x {enc} y", (secret,)) == wb.TEXT_WITHHELD, (secret, offset, piece, enc)
                    checked += 1
    assert checked >= 1000


@pytest.mark.parametrize("name, shown", [("ServerException", True), ("TimeoutError", True), ("HTTP2Error", True),
                                         ("Err_x", False), ("A" * 49, False), ("Kq7Zplantedkey0001Error", False), ("bad name", False)])
def test_a_failures_class_name_is_shown_when_it_is_a_plain_identifier(tmp_path, name, shown):
    client = FakeClient()
    exc = type(name, (Exception,), {"error_code": "SYSTEM_BUSY", "http_status": 417, "error_msg": "busy"})()
    client.account_v2.get_account_list = lambda: (_ for _ in ()).throw(exc)
    with pytest.raises(wb.WebullError) as got:
        wb.WebullVenue("Kq7Zplantedkey0001", SECRET, client=client, confirmation_file=tmp_path / "x.json").accounts()
    assert (f"Webull call failed: {name} (417, SYSTEM_BUSY): busy" == str(got.value)) is shown, str(got.value)
    if not shown:
        assert str(got.value).startswith("Webull call failed: <withheld> (417, SYSTEM_BUSY)")


WORD_SECRET = "zqplantedsecretwordx"                                      # a plain lower-case word: the shape rule keeps it


def _word_secret_venue(tmp_path, secret):
    client = FakeClient()
    client.account_v2.get_account_list = lambda: (_ for _ in ()).throw(ServerException("SYSTEM_BUSY", 417, f"denied {secret}"))
    return wb.WebullVenue(KEY, secret, client=client, confirmation_file=tmp_path / "x.json")


def test_the_last_check_withholds_what_the_gate_missed_with_a_form_removed(tmp_path, monkeypatch):
    secret = WORD_SECRET + "a"
    original = wb._forms
    monkeypatch.setattr(wb, "_forms", lambda words: {f for f in original(words) if f.lower() != secret.lower()})
    assert "zqplantedsecretwordxa" in wb._gate(f"denied {secret}", wb._secrets_of(KEY, secret))     # the gate is defeated
    with pytest.raises(wb.WebullError) as exc:
        _word_secret_venue(tmp_path, secret).accounts()
    assert str(exc.value) == wb.TEXT_WITHHELD


def test_the_last_check_withholds_what_the_gate_missed_with_no_secrets(tmp_path, monkeypatch):
    secret = WORD_SECRET + "b"
    monkeypatch.setattr(wb.WebullVenue, "_secrets", lambda self, *a, **k: wb.NO_SECRETS)
    with pytest.raises(wb.WebullError) as exc:
        _word_secret_venue(tmp_path, secret).accounts()
    assert str(exc.value) == wb.TEXT_WITHHELD
    assert wb._last_check(f"denied {secret}", wb.NO_SECRETS) == f"denied {secret}"   # alone, the module function knows nothing
    v = _word_secret_venue(tmp_path, secret)
    assert v._final(f"denied {secret}") == wb.TEXT_WITHHELD                            # the venue's check takes its own set


class _Unbool:
    def __bool__(self):
        raise RuntimeError("no truth value")

    def __str__(self):
        raise RuntimeError("no text")


class _Odd(Exception):
    error_msg = _Unbool()
    error_code = _Unbool()
    http_status = _Unbool()

    def __str__(self):
        raise RuntimeError("no text")


@pytest.mark.parametrize("where", ["api", "endpoint", "token", "trade"])
@pytest.mark.parametrize("what", ["odd", "unreadable", "webull", "value"])
def test_nothing_but_webull_error_leaves_the_constructor(tmp_path, monkeypatch, where, what):
    def fail():
        raise {"odd": _Odd(), "unreadable": Unreadable(), "webull": wb.WebullError("x"), "value": ValueError("y")}[what]

    class ApiClient:
        def __init__(self, k, s, region):
            if where == "api":
                fail()

        def add_endpoint(self, region, host):
            if where == "endpoint":
                fail()

        def set_token_dir(self, d):
            if where == "token":
                fail()

    class TradeClient:
        def __init__(self, api):
            fail()

    monkeypatch.setitem(sys.modules, "webull.core.client", types.SimpleNamespace(ApiClient=ApiClient))
    monkeypatch.setitem(sys.modules, "webull.trade.trade_client", types.SimpleNamespace(TradeClient=TradeClient))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(wb.WebullError) as exc:
        wb.WebullVenue(KEY, SECRET, confirmation_file=tmp_path / "x.json")
    assert exc.value.__context__ is None and exc.value.__cause__ is None


@pytest.mark.parametrize("source", ["sdk_msg", "exc_message", "webull_error", "not_transmitted", "error_body", "status", "answer_all"])
@pytest.mark.parametrize("endpoint", sorted(ENDPOINT_TARGETS))
def test_no_raised_error_carries_the_sdks_exception(tmp_path, source, endpoint):
    raised = 0
    for name, call in METHODS.items():
        v = _planted_venue(tmp_path, KEY, SECRET, "ACC-0001-1234", source, endpoint, "busy")
        try:
            call(v)
        except wb.WebullError as exc:
            assert exc.__context__ is None and exc.__cause__ is None, (name, repr(exc.__context__))
            raised += 1
    v = _planted_venue(tmp_path, KEY, SECRET, "ACC-0001-1234", source, endpoint, "busy", known=False)
    try:
        v.account_id
    except wb.WebullError as exc:
        assert exc.__context__ is None and exc.__cause__ is None
    assert raised > 0 or source == "answer_all"                       # a well-formed answer may raise nothing


AFTER_CALL = {"server": lambda: (_ for _ in ()).throw(ServerException("SYSTEM_BUSY", 500)),
              "webull": lambda: (_ for _ in ()).throw(wb.WebullError("client trouble")),
              "not_transmitted": lambda: (_ for _ in ()).throw(wb.NotTransmitted("client says not sent")),
              "runtime": lambda: (_ for _ in ()).throw(RuntimeError("socket closed")),
              "417": lambda: Response({"error_code": "INSUFFICIENT_FUNDS", "message": "Insufficient buying power"}, 417),
              "500": lambda: Response(None, 500), "text_status": lambda: Response({}, "200"),
              "compare": lambda: Response({}, _StatusRaises()), "property": lambda: _PropertyRaises()}


@pytest.mark.parametrize("how", sorted(AFTER_CALL))
def test_every_error_after_the_order_call_says_the_outcome_is_unknown(tmp_path, how):
    client = FakeClient()
    client.order_v3.place_order = lambda a, orders: (client.calls.append(("place", a, orders)), AFTER_CALL[how]())[1]
    v = wb.WebullVenue(KEY, SECRET, client=client, confirmation_file=tmp_path / "x.json")
    with pytest.raises(wb.WebullError) as exc:
        v.submit("SPY", 1.0, "t")
    text = str(exc.value)
    assert type(exc.value) is wb.WebullError and len(client.placed()) == 1
    assert "outcome unknown" in text and "read the order's status" in text and "before retrying" in text, text


def test_a_learned_account_id_is_handed_to_secrets_remember_when_it_exists(tmp_path, monkeypatch):
    import valedia_trader.core.secrets as core_secrets
    seen = []
    monkeypatch.setattr(core_secrets, "remember", lambda value, *a, **k: seen.append(value), raising=False)
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(), confirmation_file=tmp_path / "x.json")
    assert v.account_id == "ACC-0001-1234" and "ACC-0001-1234" in seen
    monkeypatch.delattr(core_secrets, "remember", raising=False)
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(), confirmation_file=tmp_path / "x.json")
    assert v.account_id == "ACC-0001-1234"                                # without remember nothing breaks

    def broken(value, *a, **k):
        raise RuntimeError("remember failed")
    monkeypatch.setattr(core_secrets, "remember", broken, raising=False)
    v = wb.WebullVenue(KEY, SECRET, client=FakeClient(), confirmation_file=tmp_path / "x.json")
    assert v.account_id == "ACC-0001-1234"


def test_the_http_librarys_debug_log_is_quieted(tmp_path):
    log = logging.getLogger("urllib3.connectionpool")
    old = log.level
    try:
        log.setLevel(logging.DEBUG)
        wb._quiet_sdk_logging()
        assert log.level == logging.WARNING
        log.setLevel(logging.ERROR)
        wb._quiet_sdk_logging()
        assert log.level == logging.ERROR                                 # a higher level already set is kept
        log.setLevel(logging.NOTSET)
        wb.WebullVenue(KEY, SECRET, client=FakeClient(), confirmation_file=tmp_path / "x.json")
        assert log.level == logging.WARNING
    finally:
        log.setLevel(old)
