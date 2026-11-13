import io
import json
import logging
import sys
import types
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


class ServerError(Exception):
    """Shaped like the SDK's ServerException (webull/core/exception/exceptions.py:31-37); its text carries the key,
    as the SDK's own log lines do, so a test can see that it never reaches a message."""
    def __init__(self, error_code, http_status=417, error_msg="business error"):
        self.error_code, self.http_status, self.error_msg = error_code, http_status, error_msg

    def __str__(self):
        return f"request headers: x-app-key={KEY} x-app-secret={SECRET}"


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
        {"client_order_id": "c-spy", "venue_order_id": "WB-5", "symbol": "SPY", "units": 2.0, "state": "open", "filled_units": 0.0, "raw_status": "SUBMITTED"},
        {"client_order_id": "c-btc", "venue_order_id": "WB-7", "symbol": "BTC-USD", "units": -0.003, "state": "partly_filled", "filled_units": 0.001, "raw_status": "PARTIAL_FILLED"}]
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
    assert out[0]["response"]["client_order_id"] == "c-1" and client.placed() == []


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
    rows = [{"symbol": "SPY", "quantity": HUGE}, {"symbol": "QQQ", "quantity": "1e999"}, {"symbol": "IWM", "quantity": "nan"},
            {"symbol": "DIA", "quantity": "3"}]
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
                d = Path(raw).expanduser().absolute()
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
