"""T19: event-contract orders in the Events account (docs/specs/2026-11-13-event-orders-design.md, part 1).

Every test uses a stand-in for the SDK's TradeClient; nothing here builds a Webull client or touches the network.
The stand-in serves the shapes observed in Webull's test environment on 2026-11-13 (the ticket's CONTEXT)."""
import importlib.util
import random
import re
import string
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from valedia_trader.execution_and_risk import order_step
from valedia_trader.venues import webull as wb

KEY, SECRET = "app-key-do-not-print", "app-secret-do-not-print"
REPO = Path(__file__).resolve().parents[2]
HIGH = {kind: (10 ** 9, 1) for kind in wb.CALL_KINDS}
EV_ID, CASH_ID, CRYPTO_ID = "8174402917735510004", "8174402917735510002", "8174402917735510005"
ACCOUNTS = [
    {"account_id": "8174402917735510001", "account_type": "MARGIN", "account_class": "INDIVIDUAL_MARGIN", "account_label": "Individual Margin"},
    {"account_id": CASH_ID, "account_type": "CASH", "account_class": "INDIVIDUAL_CASH", "account_label": "Individual Cash"},
    {"account_id": "8174402917735510003", "account_type": "MARGIN", "account_class": "FUTURES", "account_label": "Futures"},
    {"account_id": EV_ID, "account_type": "CASH", "account_class": "EVENTS_CASH", "account_label": "Events Cash"},
    {"account_id": CRYPTO_ID, "account_type": "CASH", "account_class": "CRYPTO", "account_label": "Crypto"},
]
TICKER = "KXNBAGAME-26NOV13ATLCHI-ATL"
# The observed balance answer of the Events Cash account: buying power, no cash line.
OBSERVED_BALANCE = {"total_asset_currency": "USD", "total_market_value": "0.71", "total_unrealized_profit_loss": "0",
                    "total_day_profit_loss": "0", "account_currency_assets": [
                        {"currency": "USD", "market_value": "0.71", "buying_power": "999.27", "unrealized_profit_loss": "0",
                         "day_profit_loss": "0"}]}


@pytest.fixture(autouse=True)
def _home_is_temporary(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


class Response:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body


class ServerException(Exception):
    """Named and shaped like the SDK's ServerException; its text carries the key, as the SDK's log lines do."""
    def __init__(self, error_code, error_msg, http_status, request_id=None):
        self.error_code, self.error_msg, self.http_status, self.request_id = error_code, error_msg, http_status, request_id

    def __str__(self):
        return f"request headers: x-app-key={KEY}"


NOT_PRESENT = ("OPENAPI_PARAM_ERR", "Parameter error, Order not present.", 417)


def event_row(cid, ticker=TICKER, outcome="yes", side="BUY", status="SUBMITTED", total="1", filled="0", price="0",
              limit="0.7200", fees="one", **extra):
    """One order row as observed: the ticker as symbol, the outcome in event_outcome, one fee entry from acceptance."""
    fee_list = ([{"type": "EXCHANGE_FEE", "actual_value": "0.01", "receivable_value": "0.01"}] if fees == "one" else fees)
    row = {"client_order_id": cid, "order_id": "ORD-1", "symbol": ticker, "side": side, "status": status, "fees": fee_list,
           "commission": {}, "order_type": "LIMIT", "instrument_type": "EVENT", "entrust_type": "QTY", "total_quantity": total,
           "filled_quantity": filled, "filled_price": price, "limit_price": limit, "time_in_force": "DAY", **extra}
    if outcome is not None:
        row["event_outcome"] = outcome
    return row


def position_row(ticker=TICKER, outcome="yes", quantity="1", kind="EVENT"):
    row = {"currency": "USD", "quantity": quantity, "cost": "0.71", "position_id": "P-1", "symbol": ticker,
           "instrument_type": kind, "cost_price": "0.71", "last_price": "0.70", "unrealized_profit_loss": "-0.01"}
    if outcome is not None:
        row["event_outcome"] = outcome
    return row


class Client:
    """Stands in for the SDK's TradeClient: records every call. An order placed is then visible in its detail as the
    observed SUBMITTED row (unless `details` scripts the client order id); an id never placed answers "not present"."""

    def __init__(self, accounts=None, positions=None, open_rows=None, details=None, place=None, balance=None, place_status=200):
        self.calls = []
        self.accounts = [dict(a) for a in (ACCOUNTS if accounts is None else accounts)]
        self.positions_rows = positions or []
        self.open_rows = open_rows or []
        self.details = details or {}
        self.place_raises = place
        self.place_status = place_status
        self.balance_body = OBSERVED_BALANCE if balance is None else balance
        self.placed_orders = {}
        self.account_v2 = SimpleNamespace(get_account_list=self._accounts, get_account_balance=self._balance,
                                          get_account_position=self._positions)
        self.order_v3 = SimpleNamespace(place_order=self._place, cancel_order=self._cancel, get_order_detail=self._detail,
                                        list_order_open=self._open)

    def _accounts(self):
        self.calls.append(("accounts",))
        return Response(self.accounts)

    def _balance(self, a):
        self.calls.append(("balance", a))
        return Response(self.balance_body)

    def _positions(self, a):
        self.calls.append(("positions", a))
        return Response(self.positions_rows)

    def _place(self, a, orders):
        self.calls.append(("place", a, [dict(o) for o in orders]))
        if self.place_raises is not None:
            raise self.place_raises
        o = orders[0]
        self.placed_orders[o["client_order_id"]] = dict(o)
        return Response({"client_order_id": o["client_order_id"], "order_id": "ORD-1"}, self.place_status)

    def _cancel(self, a, cid):
        self.calls.append(("cancel", a, cid))
        return Response({"client_order_id": cid, "order_id": "ORD-1"})

    def _detail(self, a, cid):
        self.calls.append(("detail", a, cid))
        if cid in self.details:
            answer = self.details[cid]
            if isinstance(answer, BaseException):
                raise answer
            return Response(answer)
        if cid in self.placed_orders:
            o = self.placed_orders[cid]
            return Response({"client_order_id": cid, "combo_order_id": "C-1", "combo_type": "NORMAL", "orders": [
                event_row(cid, ticker=o["symbol"], outcome=o["event_outcome"], side=o["side"], total=o["quantity"],
                          limit=o["limit_price"])]})
        raise ServerException(*NOT_PRESENT)

    def _open(self, a, pagination_key=None):
        self.calls.append(("open", a, pagination_key))
        return Response({"data": self.open_rows})

    def placed(self):
        return [c for c in self.calls if c[0] == "place"]


@pytest.fixture(autouse=True)
def _live_repo_is_temporary(tmp_path, monkeypatch):
    """T22b rev 2: the repository whose config/webull_live.json the adapter reads is the test's temporary folder."""
    monkeypatch.setattr(wb, "LIVE_REPO", tmp_path, raising=False)


def CONF(tmp_path):
    (tmp_path / "config").mkdir(exist_ok=True)
    return tmp_path / "config" / "webull_live.json"


def ev(tmp_path, client=None, cls="EVENTS_CASH", env="sandbox", clock=None, **kw):
    client = client if client is not None else Client()
    v = wb.WebullVenue(KEY, SECRET, environment=env, client=client, confirmation_file=CONF(tmp_path),
                       account_class=cls, limits=HIGH, sleep=lambda s: None, clock=clock or (lambda: 0.0), **kw)
    return v, client


def payload(client):
    assert len(client.placed()) == 1
    return client.placed()[0][2][0]


# ---- invariant 1: the outcome never flips and never defaults

def test_the_observed_order_is_built_exactly(tmp_path):
    v, client = ev(tmp_path)
    out = v.submit(f"{TICKER}:yes", 1, "tag-1", limit_price=0.01)
    assert out["status"] == "accepted" and out["side"] == "BUY" and out["quantity"] == "1"
    assert payload(client) == {"combo_type": "NORMAL", "client_order_id": wb.client_order_id("tag-1"), "market": "US",
                               "symbol": TICKER, "instrument_type": "EVENT", "order_type": "LIMIT", "quantity": "1",
                               "side": "BUY", "time_in_force": "DAY", "entrust_type": "QTY", "limit_price": "0.01",
                               "event_outcome": "yes"}
    assert client.placed()[0][1] == EV_ID
    assert wb.event_order_payload(f"{TICKER}:yes", 1, "tag-1", 0.01) == payload(client)


def test_buying_no_is_a_purchase_of_no_never_a_sale_of_yes(tmp_path):
    v, client = ev(tmp_path, Client(positions=[position_row(outcome="yes", quantity="3")]))
    v.submit(f"{TICKER}:no", 2, "t", limit_price=0.29)
    p = payload(client)
    assert (p["symbol"], p["event_outcome"], p["side"], p["quantity"], p["limit_price"]) == (TICKER, "no", "BUY", "2", "0.29")


def test_selling_no_sells_no_contracts_held(tmp_path):
    v, client = ev(tmp_path, Client(positions=[position_row(outcome="no", quantity="2")]))
    v.submit(f"{TICKER}:no", -2.0, "t", limit_price=0.25)
    p = payload(client)
    assert (p["event_outcome"], p["side"], p["quantity"]) == ("no", "SELL", "2")


def test_a_row_read_back_with_another_ticker_or_outcome_is_an_error_never_a_fill(tmp_path):
    cid = wb.client_order_id("t")
    for ticker, outcome in ((TICKER, "no"), ("KXNBAGAME-26NOV13ATLCHI-CHI", "yes"), (TICKER, None), (TICKER, "YES"), (TICKER, "")):
        row = event_row(cid, ticker=ticker, outcome=outcome, status="FILLED", filled="1", price="0.71")
        v, client = ev(tmp_path, Client(details={cid: {"client_order_id": cid, "orders": [row]}}))
        v.submit(f"{TICKER}:yes", 1, "t", limit_price=0.72)
        with pytest.raises(wb.WebullError) as exc:
            v.order_status("t")
        assert "fill" in str(exc.value) or "outcome" in str(exc.value) or "ticker" in str(exc.value)
        with pytest.raises(wb.WebullError):
            v.order_status("t", expected=f"{TICKER}:yes")


def test_order_status_needs_to_know_what_was_sent(tmp_path):
    cid = wb.client_order_id("t")
    row = event_row(cid, status="FILLED", filled="1", price="0.71")
    v, _ = ev(tmp_path, Client(details={cid: {"client_order_id": cid, "orders": [row]}}))
    with pytest.raises(wb.WebullError, match="sent"):
        v.order_status("t")                                   # a fresh venue, no expected name: cannot check
    out = v.order_status("t", expected=f"{TICKER}:yes")
    assert out["state"] == "filled" and out["filled_units"] == 1.0 and out["avg_price"] == 0.71 and out["fee"] == 0.01
    assert out["symbol"] == f"{TICKER}:yes"
    for bad in (f"{TICKER}:maybe", "spy", 7, f"{TICKER}:no "):
        with pytest.raises(wb.WebullError):
            v.order_status("t", expected=bad)
    with pytest.raises(wb.WebullError):
        v.order_status("t", expected=f"{TICKER}:no")


def _ticker(rng):
    head = rng.choice(string.ascii_uppercase)
    return head + "".join(rng.choice(string.ascii_uppercase + string.digits + ".-") for _ in range(rng.randint(0, 30)))


def test_generated_orders_keep_their_outcome_and_side(tmp_path):
    """600 seeded orders on accounts holding both outcomes of a few tickers: each payload carries the name's own
    outcome, BUY for a purchase and SELL for a sale, and a sale is sent exactly when my own count allows it."""
    rng = random.Random(1913)
    tickers = ["KXA-1", "KXA-2", "KXB.X"]
    sent = refused = 0
    for i in range(600):
        held = {(t, o): rng.choice([0, 0, 1, 2, 3]) for t in tickers for o in ("yes", "no")}
        rows = [position_row(t, o, str(q)) for (t, o), q in held.items() if q]
        open_rows, reserved = [], {}
        for j in range(rng.randint(0, 3)):
            t, o, side = rng.choice(tickers), rng.choice(["yes", "no"]), rng.choice(["BUY", "SELL"])
            total, filled = rng.randint(1, 3), 0
            filled = rng.randint(0, total - 1)
            open_rows.append(event_row(f"open-{j}", ticker=t, outcome=o, side=side, total=str(total), filled=str(filled)))
            if side == "SELL":
                reserved.update({(t, x): reserved.get((t, x), 0) + total - filled for x in ("yes", "no")})
        t, o = rng.choice(tickers), rng.choice(["yes", "no"])
        contracts = rng.randint(1, 4)
        units = contracts if rng.random() < 0.4 else -contracts
        v, client = ev(tmp_path, Client(positions=rows, open_rows=open_rows))
        allowed = units > 0 or contracts <= held[(t, o)] - reserved.get((t, o), 0)
        if allowed:
            v.submit(f"{t}:{o}", float(units), f"g-{i}", limit_price=0.5)
            p = payload(client)
            assert (p["symbol"], p["event_outcome"], p["side"], p["quantity"]) == (t, o, "BUY" if units > 0 else "SELL", str(contracts))
            sent += 1
        else:
            with pytest.raises(wb.NotTransmitted, match="long-only"):
                v.submit(f"{t}:{o}", float(units), f"g-{i}", limit_price=0.5)
            assert client.placed() == []
            refused += 1
    assert sent > 150 and refused > 150


def test_generated_read_backs_are_errors_unless_ticker_and_outcome_match(tmp_path):
    """600 seeded detail answers for an order sent as one name: read as a status only when the row's ticker and outcome
    are exactly what was sent (my own comparison); otherwise WebullError, never a fill."""
    rng = random.Random(2026)
    good = bad = 0
    for i in range(600):
        sent_t, sent_o = _ticker(rng), rng.choice(["yes", "no"])
        t = sent_t if rng.random() < 0.5 else rng.choice([_ticker(rng), sent_t.lower(), sent_t + "X", sent_t[:-1] or "Q"])
        o = sent_o if rng.random() < 0.5 else rng.choice(["yes", "no", "YES", "No", " yes", None, "", "true", 1])
        tag = f"rb-{i}"
        cid = wb.client_order_id(tag)
        row = event_row(cid, ticker=t, outcome=o, status="FILLED", filled="1", price="0.50")
        v, client = ev(tmp_path, Client(details={cid: {"client_order_id": cid, "orders": [row]}}))
        v.submit(f"{sent_t}:{sent_o}", 1, tag, limit_price=0.5)
        same = t == sent_t and type(o) is str and o == sent_o
        if same:
            assert v.order_status(tag)["filled_units"] == 1.0
            good += 1
        else:
            with pytest.raises(wb.WebullError):
                v.order_status(tag)
            bad += 1
    assert good > 150 and bad > 150


# ---- invariant 2: a name of the wrong shape never reaches Webull

def my_event_name(name):
    """My own reading of the rule, written without a regular expression."""
    if type(name) is not str or name.count(":") != 1:
        return None
    ticker, outcome = name.split(":")
    if outcome not in ("yes", "no") or not 1 <= len(ticker) <= 80 or not "A" <= ticker[0] <= "Z":
        return None
    if not all("A" <= c <= "Z" or "0" <= c <= "9" or c in ".-" for c in ticker):
        return None
    return ticker, outcome


class Sneaky(str):
    pass


def _names(rng, n):
    alphabet = string.ascii_letters + string.digits + ".-_: /\n\tÉÅＡı$"
    out = []
    for _ in range(n):
        r = rng.random()
        if r < 0.45:
            name = f"{_ticker(rng)}:{rng.choice(['yes', 'no'])}"
        elif r < 0.75:
            base = f"{_ticker(rng)}:{rng.choice(['yes', 'no'])}"
            k = rng.randrange(len(base))
            name = rng.choice([base[:k] + rng.choice(alphabet) + base[k:], base[:k] + base[k + 1:], base.lower(), base.upper(),
                               base + "\n", " " + base, base.replace(":", "::"), "A" * rng.randint(80, 82) + ":yes", base + ":yes"])
        else:
            name = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        out.append(name)
    return out


def test_generated_names_reach_webull_only_in_the_event_shape(tmp_path):
    """700 seeded names in an Events account: a name my own reading accepts is sent with its ticker and outcome; every
    other name is refused before any call at all (no account read, nothing sent)."""
    rng = random.Random(77)
    names = _names(rng, 700) + ["A" * 80 + ":yes", "A" * 81 + ":yes", "A:yes", "9A:yes", "a:yes", "A:Yes", Sneaky(f"{TICKER}:yes"),
                                "A:yes\n", "A:no", "Ａ:yes", "AB_C:no"]
    accepted = rejected = 0
    for i, name in enumerate(names):
        v, client = ev(tmp_path)
        mine = my_event_name(name)
        if mine is not None:
            v.submit(name, 1, f"n-{i}", limit_price=0.5)
            p = payload(client)
            assert (p["symbol"], p["event_outcome"]) == mine
            accepted += 1
        else:
            with pytest.raises(wb.NotTransmitted):
                v.submit(name, 1, f"n-{i}", limit_price=0.5)
            assert client.calls == []
            rejected += 1
    assert accepted > 200 and rejected > 200


@pytest.mark.parametrize("cls", ["INDIVIDUAL_CASH", "INDIVIDUAL_MARGIN", "CRYPTO", "FUTURES", None])
def test_every_event_name_is_refused_outside_an_events_account(tmp_path, cls):
    rng = random.Random(5)
    accounts = [{"account_id": "ACC-0001-1234", "account_type": "CASH", **({"account_class": cls} if cls else {})}]
    for i, name in enumerate([f"{_ticker(rng)}:{rng.choice(['yes', 'no'])}" for _ in range(60)] + ["SPY:yes", "spy:no", "btc-usd:yes"]):
        v, client = ev(tmp_path, Client(accounts=accounts, positions=[position_row()]), cls=cls)
        for units in (1, -1):
            with pytest.raises(wb.NotTransmitted, match="event"):
                v.submit(name, units, f"x-{i}", limit_price=0.5)
        assert client.placed() == [] and client.calls == []


def test_an_events_venue_refuses_stock_and_crypto_names(tmp_path):
    for name in ("SPY", "BTC-USD", "spy", "AAPL.X", ""):
        v, client = ev(tmp_path, Client(positions=[{"symbol": "SPY", "quantity": "5", "instrument_type": "EQUITY"}]))
        with pytest.raises(wb.NotTransmitted):
            v.submit(name, 1, "t", limit_price=0.5)
        assert client.calls == []


# ---- invariant 2: the stock and crypto path is byte-identical to the adapter before T19

HEAD_REV = "eb1e34f9bfa7657849c977a9098c619a3febb0b5"   # the last commit that changed webull.py before T19 (rev 9)


@pytest.fixture(scope="module")
def head(tmp_path_factory):
    source = subprocess.run(["git", "show", f"{HEAD_REV}:valedia_trader/venues/webull.py"], cwd=REPO, capture_output=True,
                            check=True).stdout
    path = tmp_path_factory.mktemp("head") / "webull_before_t19.py"
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location("webull_before_t19", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["webull_before_t19"] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("webull_before_t19", None)


def _spec(rng):
    """One generated account and request for a non-Events venue: no symbol holds a colon (those are event names)."""
    cls = rng.choice(["INDIVIDUAL_CASH", "INDIVIDUAL_CASH", "INDIVIDUAL_MARGIN", "CRYPTO", "CRYPTO", "EVENTS_CASH", "FUTURES", None])
    wanted = None if cls in (None, "EVENTS_CASH") or rng.random() < 0.5 else cls
    syms = ["SPY", "QQQ", "AAPL", "BTC-USD", "ETH-USD", "BRK.B", "X"]
    kinds = ["EQUITY", "CRYPTO", "EVENT", "OPTION", None, "equity"]
    positions = []
    for _ in range(rng.randint(0, 4)):
        s = rng.choice(syms + ["BTCUSD", TICKER[:20]])
        positions.append({"symbol": s, "quantity": rng.choice(["1", "2.5", "0.001", "10", "-1", "x", None, "1e300"]),
                          "instrument_type": rng.choice(kinds), **({"event_outcome": "yes"} if rng.random() < 0.3 else {})})
    open_rows = []
    for j in range(rng.randint(0, 3)):
        open_rows.append({"client_order_id": f"o{j}", "order_id": f"W{j}", "symbol": rng.choice(syms + ["BTCUSD"]),
                          "side": rng.choice(["BUY", "SELL", "SHORT", "sell", None]), "status": rng.choice(["SUBMITTED", "PARTIAL_FILLED", "WHAT"]),
                          "instrument_type": rng.choice(kinds), "total_quantity": rng.choice(["1", "2", "0.5", None]),
                          "filled_quantity": rng.choice(["0", "1", None]), **({"event_outcome": "no"} if rng.random() < 0.3 else {})})
    if rng.random() < 0.7:                                       # mostly a request that fits the account and is sent
        symbol = rng.choice(["BTC-USD", "ETH-USD", "btc-usd"] if cls == "CRYPTO" else ["SPY", "QQQ", "AAPL", "spy", "BRK.B"])
        units = rng.choice([1, 2.0, 3, 0.5, -1, -0.5, -2.0])
        if units < 0 and rng.random() < 0.7:                     # a sale of something held, as EQUITY or CRYPTO
            kind = "CRYPTO" if symbol.upper().endswith("-USD") else "EQUITY"
            positions.append({"symbol": symbol.upper().replace("-", "") if kind == "CRYPTO" else symbol.upper(),
                              "quantity": rng.choice(["1", "2", "5"]), "instrument_type": kind})
        limit = rng.choice([None, None, 100.0, 0.5, 2500.25])
        session = rng.choice(["CORE", "CORE", "CORE", "ALL", "NIGHT"])
    else:
        symbol = rng.choice(syms + ["spy", "btc-usd", "", "TOOLONGSYMBOLNAMEXXXXXXXXXXX", "A B"])
        units = rng.choice([1, 2.0, -1, -2.5, 0.5, -0.001, 0, 1e10, float("nan"), True, "1", 1.123456, -10, 0.00000001])
        limit = rng.choice([None, None, 100.0, 0.5, 0.501, 80000.12345678, -1, 0, float("inf"), "5"])
        session = rng.choice(["CORE", "CORE", "ALL", "NIGHT", "night", None])
    price = rng.choice([None, 100.0, 0.0, 5.0])
    place = rng.choice(["ok", "ok", "ok", "417", "500", "repeat"])
    cid = wb.client_order_id("h-tag")
    detail = rng.choice([
        {"client_order_id": cid, "orders": [{"client_order_id": cid, "order_id": "W9", "symbol": "SPY", "side": "BUY",
                                             "status": rng.choice(["FILLED", "SUBMITTED", "CANCELLED", "ODD"]), "instrument_type": "EQUITY",
                                             "total_quantity": "1", "filled_quantity": rng.choice(["1", "0", None]), "filled_price": rng.choice(["100.5", "0", None]),
                                             "commission": rng.choice([{}, {"actual_commission": "0.1"}]),
                                             "fees": rng.choice([[], [{"type": "SEC", "actual_value": "0.02"}], [{"actual_value": "x"}]])}]},
        "not present", {"orders": []}, [], {"orders": ["junk"]}])
    balance = rng.choice([OBSERVED_BALANCE, {"total_asset_currency": "USD", "total_cash_balance": "50",
                                             "account_currency_assets": [{"currency": "USD", "cash_balance": "50", "settled_cash": "40",
                                                                          "buying_power": "60"}]}, {"account_currency_assets": "x"}])
    return dict(cls=cls, wanted=wanted, positions=positions, open_rows=open_rows, symbol=symbol, units=units, limit=limit,
                session=session, price=price, place=place, detail=detail, balance=balance)


class SpecClient(Client):
    def __init__(self, s):
        accounts = [{"account_id": "ACC-0001-1234", "account_type": "CASH", **({"account_class": s["cls"]} if s["cls"] else {})}]
        super().__init__(accounts=accounts, positions=s["positions"], open_rows=s["open_rows"], balance=s["balance"])
        self.spec = s

    def _place(self, a, orders):
        self.calls.append(("place", a, [dict(o) for o in orders]))
        mode = self.spec["place"]
        if mode == "417":
            raise ServerException("OPENAPI_STOCK_ORDER_PRICE_PRECISION_EXCEED", "precision", 417)
        if mode == "500":
            raise ServerException("INTERNAL", "boom", 500)
        if mode == "repeat":
            raise ServerException("OPENAPI_TRADE_PLACE_ORDER_REPEAT", "repeat", 417)
        return Response({"client_order_id": orders[0]["client_order_id"], "order_id": "W-1"})

    def _detail(self, a, cid):
        self.calls.append(("detail", a, cid))
        d = self.spec["detail"]
        if d == "not present":
            raise ServerException(*NOT_PRESENT)
        return Response(d)


def _outcome(fn):
    try:
        return ("ok", fn())
    except BaseException as exc:                                   # compared by class names and text
        return ("raised", [c.__name__ for c in type(exc).__mro__], str(exc))


# T22d-2 changed two things on this path on purpose; every other difference from the adapter before T19 is an error.
# B1: after a refused placement, "accepted" only when the detail row is the order just sent (symbol, side, quantity)
# and alive; B3: a sale this venue object sent and has not seen settled is subtracted in the long-only check.
ALIVE_STATUSES = frozenset({"PENDING", "SUBMITTED", "PARTIAL_FILLED", "PARTIAL FILLED", "FILLED"})
DEAD_STATUSES = frozenset({"CANCELLED", "CANCELED", "FAILED", "REJECTED", "EXPIRED"})
SETTLED_STATUSES = DEAD_STATUSES | {"FILLED"}
LONG_ONLY = re.compile(r"selling (\S+) (\S+) with (\S+) held and (\S+) in open sells would open a short; this venue is long-only")
SECOND_TAG = "h-tag-2"


def _sent_names(sent):
    """The names a detail row may give the order `sent` under: Webull's symbol, and for crypto the trader's BTC-USD."""
    names = {sent["symbol"]}
    if sent["instrument_type"] == "CRYPTO" and sent["symbol"].endswith("USD"):
        names.add(sent["symbol"][:-3] + "-USD")
    return names


def _quantity(x):
    """This test's own reading of a quantity Webull wrote as a string: a finite, non-negative Decimal, else None."""
    try:
        number = Decimal(x) if isinstance(x, str) else None
    except ArithmeticError:
        return None
    return number if number is not None and number.is_finite() and number >= 0 else None


def _row_reading(row, sent):
    """This test's own reading of a detail row against the order sent: "readable" is False when the symbol, side or
    total quantity cannot be read; "same" whether it is that order; "status" the raw status; "filled" the row's
    filled_quantity as a Decimal (None when absent or unreadable)."""
    sym, side, number = row.get("symbol"), row.get("side"), _quantity(row.get("total_quantity"))
    readable = isinstance(sym, str) and isinstance(side, str) and number is not None
    same = readable and sym.upper() in _sent_names(sent) and side == sent["side"] and number == Decimal(sent["quantity"])
    return {"readable": readable, "same": same, "status": row.get("status"), "filled": _quantity(row.get("filled_quantity"))}


def _the_row(s):
    """The one order row the scripted detail answer holds, else None (not present, empty, junk)."""
    d = s["detail"]
    if isinstance(d, dict) and isinstance(d.get("orders"), list) and len(d["orders"]) == 1 and isinstance(d["orders"][0], dict):
        return d["orders"][0]
    return None


def _places(calls):
    return [c for c in calls if c[0] == "place"]


def _is_b1(s, head_step, cand_step):
    """B1, recognised by what it is: the older adapter returned "accepted" after Webull refused the placement (a 4xx),
    the SDK saw exactly the same calls (one place, one detail read), and the detail row is NOT the order just sent,
    alive. The candidate then raises OrderRejected only when the row is readable and dead, and either another order
    or this one with a filled_quantity of exactly 0 (this test's own reading); in every other case (this order dead
    with something filled or no readable fill, a live row of another order, an unreadable row or status) a plain
    WebullError of unknown outcome. Anything else is not B1."""
    (_, h_out, h_calls), (_, c_out, c_calls) = head_step, cand_step
    if h_calls != c_calls or s["place"] not in ("417", "repeat") or h_out[0] != "ok" or h_out[1].get("status") != "accepted":
        return False
    places, details = _places(h_calls), [c for c in h_calls if c[0] == "detail"]
    row = _the_row(s)
    if len(places) != 1 or len(details) != 1 or row is None or c_out[0] != "raised":
        return False
    reading = _row_reading(row, places[0][2][0])
    if reading["same"] and reading["status"] in ALIVE_STATUSES:
        return False                                              # this order, alive: "accepted" was right
    rejected = reading["readable"] and reading["status"] in DEAD_STATUSES and (not reading["same"] or reading["filled"] == 0)
    if rejected:
        return "OrderRejected" in c_out[1] and "NotTransmitted" not in c_out[1]
    return c_out[1][0] == "WebullError" and "outcome unknown" in c_out[2]


def _in_flight(s, record):
    """This test's own count of the candidate's reservation when the second submit runs: the quantity of the first
    submit when it was a sale that reached the order call, did not end in OrderRejected, and was not shown settled
    (that very sale, filled or dead) by the order_status read in between. Decimal(0) otherwise."""
    _, first_out, first_calls = record[2]                          # [0] construction, [1] name, [2] submit
    places = _places(first_calls)
    if not places or places[0][2][0]["side"] != "SELL" or (first_out[0] == "raised" and "OrderRejected" in first_out[1]):
        return Decimal(0)
    sent, row = places[0][2][0], _the_row(s)
    status_calls = record[6][2]                                     # the order_status step
    if row is not None and any(c[0] == "detail" and c[2] == sent["client_order_id"] for c in status_calls):
        if row.get("client_order_id") in (sent["client_order_id"], None):
            reading = _row_reading(row, sent)
            if reading["same"] and reading["status"] in SETTLED_STATUSES:
                return Decimal(0)
    return Decimal(sent["quantity"])


def _floor(x, crypto):
    return x.quantize(Decimal(1).scaleb(-(8 if crypto else 5)), rounding="ROUND_FLOOR")


def _own_open_sells(s, symbol):
    """This test's own sum of the unfilled remainder of the scripted open sells of `symbol` and its instrument type
    (EQUITY, or CRYPTO for a -USD pair): rows of exactly that instrument_type whose symbol, upper-cased and for crypto
    BTCUSD read as BTC-USD, is the symbol; side SELL or SHORT (trimmed, any case); total minus filled, or the total when
    the filled quantity is unreadable or above it. None when such a row has no readable total."""
    crypto = symbol.upper().endswith("-USD")
    kind, sym, total = ("CRYPTO" if crypto else "EQUITY"), symbol.upper(), Decimal(0)
    for r in s["open_rows"]:
        name, side = r.get("symbol"), r.get("side")
        if r.get("instrument_type") != kind or not isinstance(name, str):
            continue
        name = name.upper()
        if crypto and name.endswith("USD") and "-" not in name:
            name = name[:-3] + "-USD"
        if name != sym or not (isinstance(side, str) and side.strip().upper() in ("SELL", "SHORT")):
            continue
        q, f = _quantity(r.get("total_quantity")), _quantity(r.get("filled_quantity"))
        if q is None:
            return None
        total += q - f if f is not None and f <= q else q
    return total


def _is_b3(s, head_step, cand_step, reserved):
    """B3, recognised by what it is, at the second submit (a sale of the same symbol right after the first): either
    the older adapter sent the sale and the candidate refused it as long-only after the same reads and before any order
    call, with figures such that the sale fits once the reservation is taken out of the "in open sells" figure and does
    not fit with it; or both refused it as long-only with the same calls and the same text except the "in open sells"
    figure, which is larger in the candidate by exactly the reservation. Where the older adapter sent the sale, the
    candidate's "in open sells" figure must also be, as printed, this test's own sum of the open sells
    (_own_open_sells) plus the reservation."""
    (_, h_out, h_calls), (_, c_out, c_calls) = head_step, cand_step
    if reserved <= 0 or c_out[0] != "raised" or "NotTransmitted" not in c_out[1]:
        return False
    m = LONG_ONLY.fullmatch(c_out[2])
    if m is None:
        return False
    amount, symbol, held, open_sells = Decimal(m[1]), m[2], Decimal(m[3]), Decimal(m[4])
    crypto = symbol.upper().endswith("-USD")
    without = open_sells - reserved
    if without < 0 or symbol != s["symbol"]:                      # the message names the symbol as asked
        return False
    if _places(h_calls):                                          # the older adapter sent it (whatever Webull answered)
        own = _own_open_sells(s, symbol)
        return (own is not None and m[4] == f"{float(own + reserved):g}" and not _places(c_calls) and c_calls == h_calls[:len(c_calls)]
                and amount <= _floor(held - without, crypto) and amount > _floor(held - open_sells, crypto))
    h = LONG_ONLY.fullmatch(h_out[2]) if h_out[0] == "raised" else None
    return (h is not None and h_calls == c_calls and h_out[1] == c_out[1] and (h[1], h[2], h[3]) == (m[1], m[2], m[3])
            and Decimal(h[4]) == without)


def _is_r33a(head_step, cand_step):
    """T51c R33a (ruling R37), the third difference made on purpose: at `order_status`, with the same SDK calls, the
    candidate's answer is the older adapter's answer plus the one key `fee_unreadable`, a bool, True only where the older
    answer's fee is None; with that key removed the two answers are equal. An event answer (it carries fee_type and
    symbol) never carries the key."""
    (name, h_out, h_calls), (_, c_out, c_calls) = head_step, cand_step
    if name != "order_status" or h_calls != c_calls or h_out[0] != "ok" or c_out[0] != "ok":
        return False
    old, new = h_out[1], c_out[1]
    if not (isinstance(old, dict) and isinstance(new, dict)) or "fee_unreadable" in old or "fee_unreadable" not in new:
        return False
    if "fee_type" in new or "symbol" in new:
        return False
    flag, rest = new["fee_unreadable"], {k: v for k, v in new.items() if k != "fee_unreadable"}
    return type(flag) is bool and rest == old and (not flag or old.get("fee") is None)


def _record(module, s, tmp_path):
    """Every step of one sequence on a fresh venue: (step name, outcome, the SDK calls that step made)."""
    client = SpecClient(s)
    venue = module.WebullVenue(KEY, SECRET, client=client, confirmation_file=tmp_path / "none.json", account_class=s["wanted"],
                               limits=HIGH, sleep=lambda x: None, clock=lambda: 0.0)
    steps = [("name", lambda: venue.name),
             ("submit", lambda: venue.submit(s["symbol"], s["units"], "h-tag", price=s["price"], limit_price=s["limit"],
                                             session=s["session"])),
             ("positions", venue.positions), ("other_positions", venue.other_positions), ("open_orders", venue.open_orders),
             ("order_status", lambda: venue.order_status("h-tag")), ("cash", venue.cash), ("balance", venue.balance),
             ("cancel", lambda: venue.cancel("h-tag")), ("cancel_all_open", venue.cancel_all_open), ("check", venue.check),
             ("account_class", lambda: venue.account_class),
             ("second submit", lambda: venue.submit(s["symbol"], s["units"], SECOND_TAG, price=s["price"], limit_price=s["limit"],
                                                    session=s["session"]))]
    out = [("construction", None, list(client.calls))]
    for name, fn in steps:
        before = len(client.calls)
        out.append((name, _outcome(fn), client.calls[before:]))
    return out


def test_generated_stock_and_crypto_inputs_behave_as_before_except_b1_and_b3(tmp_path, head):
    """600 seeded accounts and requests on venues that are not Events venues, each run on a fresh venue of the adapter
    before T19 and of this one: at every step every public method gives the same value or raises the same kind of
    error with the same text, and the SDK sees the same calls with the same payloads, except a step that is one of
    the two differences T22d-2 made on purpose (see _is_b1, _is_b3), or the key T51c R33a added to a stock or
    crypto order_status answer (see _is_r33a), recognised by what it is wherever it falls.

    After such a difference the comparison goes on: the only state it leaves behind is the candidate's reservation of
    an unsettled sale, which no read, cancel or check uses, so every later step must still be equal, except the last
    one, a second sale of the same symbol, which is judged by _is_b3 with this test's own count of that reservation."""
    rng = random.Random(4242)
    compared = placed = accepted = accepted_before = identical = 0
    kinds = {"B1": 0, "B3": 0, "B1_first": 0, "R33a": 0}
    for _ in range(600):
        s = _spec(rng)
        h, c = _record(head, s, tmp_path), _record(wb, s, tmp_path)
        assert [step[0] for step in h] == [step[0] for step in c]
        reserved = _in_flight(s, c)
        differed = False
        for i, (hs, cs) in enumerate(zip(h, c)):
            if hs == cs:
                continue
            differed = True
            if hs[0] in ("submit", "second submit") and _is_b1(s, hs, cs):
                kinds["B1"] += 1
                kinds["B1_first"] += hs[0] == "submit"
            elif hs[0] == "second submit" and _is_b3(s, hs, cs, reserved):
                kinds["B3"] += 1
            elif _is_r33a(hs, cs):                                # T51c R33a (R37): the key fee_unreadable, and only it
                kinds["R33a"] += 1
            else:
                pytest.fail(f"a difference that is neither B1, B3 nor R33a at step {hs[0]!r}: before {hs[1:]!r}, now {cs[1:]!r}; spec {s!r}")
        identical += not differed
        compared += 1
        placed += any(c_[0] == "place" for c_ in c[2][2])
        accepted += c[2][1][0] == "ok"
        accepted_before += h[2][1][0] == "ok"                    # the generator still reaches acceptance
    print(f"T22d-2 comparison: {compared} sequences, {identical} identical, {kinds['B1']} B1 differences, "
          f"{kinds['B3']} B3 differences, {kinds['R33a']} R33a differences; first submit accepted {accepted_before} times before, {accepted} now")
    assert compared == 600 and placed > 150 and accepted_before > 100 and accepted == accepted_before - kinds["B1_first"]
    assert kinds["R33a"] > 50                                    # the generator reaches the new key (R37)
    assert kinds["B1"] > 0, f"no B1 difference was ever seen, so the test never reached the case it exists for: {kinds}"
    assert identical + kinds["B1"] + kinds["B3"] + kinds["R33a"] >= compared


def test_the_b3_recogniser_checks_the_in_open_sells_figure_where_the_older_adapter_sent_the_sale():
    """In every B3 case the generator above reaches, the figures leave no slack (nothing else in open sells, the sale
    equal to the holding), so the amount checks alone pin the figure. With slack they do not: 2 of 5 held, an open sell
    of 1 and a reservation of 3 fit any printed figure above 3 up to 4 under those checks; only 4 is the sum."""
    s = {"symbol": "SPY", "open_rows": [{"symbol": "SPY", "side": "SELL", "instrument_type": "EQUITY", "total_quantity": "1",
                                         "filled_quantity": "0"}, {"symbol": "SPY", "side": "BUY", "instrument_type": "EQUITY",
                                                                   "total_quantity": "9", "filled_quantity": "0"}]}
    reads = [("open", "A", None), ("positions", "A")]
    head_step = ("second submit", ("ok", {"status": "accepted"}), reads + [("place", "A", [{"side": "SELL"}])])

    def cand(figure):
        text = f"selling 2 SPY with 5 held and {figure} in open sells would open a short; this venue is long-only"
        return ("second submit", ("raised", ["NotTransmitted", "WebullError", "Exception", "BaseException", "object"], text), reads)
    assert _is_b3(s, head_step, cand("4"), Decimal(3))
    for wrong in ("3", "3.5", "3.999"):
        assert not _is_b3(s, head_step, cand(wrong), Decimal(3)), wrong


def test_the_order_payload_function_is_unchanged(head):
    rng = random.Random(11)
    for _ in range(600):
        args = (rng.choice(["SPY", "btc-usd", "ETH-USD", "brk.b", "q"]), rng.choice([1, -1, 0.5, -0.00000001, 2.123456, 0, 1e10]),
                f"t{rng.randint(0, 9)}", rng.choice([None, 1.0, 0.5, 100.123, 80000.12345678]), rng.choice(["CORE", "ALL", "NIGHT", "X"]))
        assert _outcome(lambda: head.order_payload(*args)) == _outcome(lambda: wb.order_payload(*args))


# ---- invariant 3: whole contracts, a two-decimal limit between 0.01 and 0.99, always a limit, DAY

@pytest.mark.parametrize("units", [1.5, 0.5, 0.99, -0.01, 0, -0.0, True, False, "1", None, float("nan"), float("inf"), 1e10, 10 ** 400,
                                   2.0000001, Decimal("1")])
def test_a_quantity_that_is_not_whole_contracts_is_refused_never_rounded(tmp_path, units):
    v, client = ev(tmp_path, Client(positions=[position_row(quantity="5")]))
    with pytest.raises(wb.NotTransmitted):
        v.submit(f"{TICKER}:yes", units, "t", limit_price=0.5)
    assert client.placed() == []


@pytest.mark.parametrize("limit", [None, 0, 0.0, 1.0, 1, 0.995, 0.001, 0.005, -0.5, 1.5, "0.5", True, float("nan"), float("inf"),
                                   0.1 + 0.2, 0.505, 0.009, 0.991, Decimal("0.5")])
def test_a_limit_that_is_not_two_decimals_from_0_01_to_0_99_is_refused(tmp_path, limit):
    v, client = ev(tmp_path)
    with pytest.raises(wb.NotTransmitted):
        v.submit(f"{TICKER}:yes", 1, "t", limit_price=limit)
    assert client.placed() == []


@pytest.mark.parametrize("limit, text", [(0.01, "0.01"), (0.99, "0.99"), (0.5, "0.50"), (0.1, "0.10"), (0.72, "0.72"), (0.07, "0.07")])
def test_the_limit_sent_is_the_limit_given(tmp_path, limit, text):
    v, client = ev(tmp_path)
    v.submit(f"{TICKER}:no", 3, "t", limit_price=limit, session="NIGHT")
    p = payload(client)
    assert p["limit_price"] == text and p["order_type"] == "LIMIT" and p["time_in_force"] == "DAY" and p["quantity"] == "3"
    assert "support_trading_session" not in p and "event_trade_mode" not in p and p["entrust_type"] == "QTY"


# ---- invariant 4: long only, per ticker and outcome, on holdings read at most 5 seconds earlier

def test_a_sale_needs_contracts_held_of_that_ticker_and_outcome_less_open_sales(tmp_path):
    rows = [position_row(outcome="yes", quantity="3"), position_row("KXOTHER-1", "no", "5")]
    sells = [event_row("s1", side="SELL", total="2", filled="1")]          # one of the three is in an open sale
    v, client = ev(tmp_path, Client(positions=rows, open_rows=sells))
    v.submit(f"{TICKER}:yes", -2, "ok", limit_price=0.5)
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        ev(tmp_path, Client(positions=rows, open_rows=sells))[0].submit(f"{TICKER}:yes", -3, "too-many", limit_price=0.5)
    for name in (f"{TICKER}:no", "KXOTHER-1:yes", "KXOTHER-2:no"):
        c = Client(positions=rows)
        with pytest.raises(wb.NotTransmitted, match="long-only"):
            ev(tmp_path, c)[0].submit(name, -1, "x", limit_price=0.5)
        assert c.placed() == []
    assert len(client.placed()) == 1


def test_a_duplicated_holding_or_an_open_order_of_no_known_kind_licenses_no_sale(tmp_path):
    c = Client(positions=[position_row(quantity="1"), position_row(quantity="1")])
    with pytest.raises(wb.NotTransmitted):
        ev(tmp_path, c)[0].submit(f"{TICKER}:yes", -1, "x", limit_price=0.5)
    odd = event_row("s1", side="SELL")
    odd.pop("instrument_type")
    c = Client(positions=[position_row(quantity="2")], open_rows=[odd])
    with pytest.raises(wb.NotTransmitted):
        ev(tmp_path, c)[0].submit(f"{TICKER}:yes", -1, "x", limit_price=0.5)
    assert c.placed() == []


class StepClock:
    """Moves `step` seconds forward each time it is read."""
    def __init__(self, step):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def test_a_sale_on_holdings_read_more_than_5_seconds_before_the_order_call_is_not_sent(tmp_path):
    c = Client(positions=[position_row(quantity="2")])
    v, _ = ev(tmp_path, c, clock=StepClock(6.0))
    with pytest.raises(wb.NotTransmitted, match="5 seconds|seconds"):
        v.submit(f"{TICKER}:yes", -1, "x", limit_price=0.5)
    assert c.placed() == [] and len([x for x in c.calls if x[0] == "positions"]) == 1 + wb.SALE_REREADS


def test_generated_sales_follow_my_own_count(tmp_path):
    """600 seeded sales: sent exactly when contracts <= held of that name minus the unfilled part of its open sales
    (open purchases and other names do not count), by my own sum."""
    rng = random.Random(404)
    names = [("KXZ-1", "yes"), ("KXZ-1", "no"), ("KXZ-2", "yes")]
    sent = refused = 0
    for i in range(600):
        rows = [position_row(t, o, str(rng.randint(0, 4))) for t, o in names if rng.random() < 0.7]
        held = {(r["symbol"], r["event_outcome"]): int(r["quantity"]) for r in rows}
        opens, reserved = [], 0
        t, o = rng.choice(names)
        for j in range(rng.randint(0, 4)):
            ot, oo = rng.choice(names)
            side = rng.choice(["BUY", "SELL"])
            total = rng.randint(1, 3)
            filled = rng.choice([0, 0, 1, None])
            filled = None if filled is None else min(filled, total)
            opens.append(event_row(f"o{j}", ticker=ot, outcome=oo, side=side, total=str(total),
                                   filled="x" if filled is None else str(filled)))
            if side == "SELL" and ot == t:
                reserved += total if filled is None else total - filled
        contracts = rng.randint(1, 4)
        c = Client(positions=rows, open_rows=opens)
        v, _ = ev(tmp_path, c)
        if contracts <= held.get((t, o), 0) - reserved:
            v.submit(f"{t}:{o}", -contracts, f"s{i}", limit_price=0.5)
            assert payload(c)["side"] == "SELL" and payload(c)["event_outcome"] == o
            sent += 1
        else:
            with pytest.raises(wb.NotTransmitted):
                v.submit(f"{t}:{o}", -contracts, f"s{i}", limit_price=0.5)
            assert c.placed() == []
            refused += 1
    assert sent > 100 and refused > 100


# ---- invariant 5: two ledgers that cannot meet

def test_the_events_venue_has_its_own_name_and_the_cash_venue_keeps_its_name(tmp_path):
    events, _ = ev(tmp_path)
    cash, _ = ev(tmp_path, cls="INDIVIDUAL_CASH")
    plain, _ = ev(tmp_path, cls=None)
    crypto, _ = ev(tmp_path, cls="CRYPTO")
    assert cash.name == plain.name == crypto.name == "webull-sandbox"
    assert events.name == "webull-sandbox-events"
    assert ev(tmp_path, env="production")[0].name == "webull-production-events"
    memory = tmp_path / "memory"
    assert order_step.ledger_path_for(memory, cash.name).name == "orders-webull-sandbox.sqlite"
    assert order_step.ledger_path_for(memory, events.name) != order_step.ledger_path_for(memory, cash.name)
    a = order_step.state_path_for(order_step.ledger_path_for(memory, events.name), events.name)
    b = order_step.state_path_for(order_step.ledger_path_for(memory, cash.name), cash.name)
    assert a != b and str(a) + ".lock" != str(b) + ".lock"


def test_an_events_venue_on_an_account_of_another_class_sends_nothing(tmp_path):
    accounts = [{"account_id": "ACC-1", "account_class": "INDIVIDUAL_CASH"}]
    c = Client(accounts=accounts)
    with pytest.raises(wb.NotTransmitted):
        ev(tmp_path, c, account_id="ACC-1")[0].submit(f"{TICKER}:yes", 1, "t", limit_price=0.5)
    assert c.placed() == []
    c = Client(accounts=[{"account_id": "ACC-2", "account_class": "EVENTS_CASH"}])
    v, _ = ev(tmp_path, c, cls=None)                                   # an Events account reached without the class: no event orders
    assert v.name == "webull-sandbox"
    with pytest.raises(wb.NotTransmitted):
        v.submit(f"{TICKER}:yes", 1, "t", limit_price=0.5)
    assert c.placed() == []


# ---- invariant 6: event orders in the test environment only, in the adapter itself

def test_production_refuses_event_orders_whatever_the_confirmation_file_says(tmp_path):
    conf = CONF(tmp_path)
    conf.write_text('{"account_class": "INDIVIDUAL_CASH", "account_ends": "%s", "confirmed_by": "Burton", '   # T22b: the new form
                    '"max_order_value": 100000, "max_capital": 100000, "valid_until": "%s", '
                    '"i_understand_this_trades_real_money": true}' % (EV_ID[-4:], wb.ny_date().isoformat()))
    assert wb.load_live_confirmation(conf) is not None
    v, client = ev(tmp_path, env="production")
    for units in (1, -1):
        with pytest.raises(wb.LiveTradingNotConfirmed):
            v.submit(f"{TICKER}:yes", units, "t", price=0.5, limit_price=0.5)
    assert client.calls == []                                          # refused before anything is read
    assert "test environment" in v.check()["real_money_orders"]


def test_an_environment_or_host_that_is_not_plainly_the_test_environment_refuses_before_any_read(tmp_path):
    v, client = ev(tmp_path)
    v.environment = "production"
    with pytest.raises(wb.NotTransmitted):
        v.submit(f"{TICKER}:yes", 1, "t", limit_price=0.5)
    for host in ("api.webull.com", "sandbox-api.webull.com", "api.sandboxx.webull.com", "api.sandbox.webull.com\n", 7):
        v, client = ev(tmp_path)
        v.host = host
        with pytest.raises(wb.NotTransmitted):
            v.submit(f"{TICKER}:yes", 1, "t", limit_price=0.5)
        assert client.calls == []

    class Shifty(wb.WebullVenue):
        @property
        def environment(self):
            return "sandbox"

        @environment.setter
        def environment(self, value):
            pass

    s = Shifty(KEY, SECRET, client=Client(), account_class="EVENTS_CASH", limits=HIGH, sleep=lambda x: None, clock=lambda: 0.0,
               confirmation_file=tmp_path / "x.json")
    with pytest.raises(wb.NotTransmitted):
        s.submit(f"{TICKER}:yes", 1, "t", limit_price=0.5)
    assert s._client.calls == []


# ---- invariant 8 (adapter part): the fee is the one fee entry, and only on an order that filled

def test_the_fee_comes_from_the_one_entry_and_only_once_something_filled(tmp_path):
    cid = wb.client_order_id("t")
    cases = [(event_row(cid), None),                                                  # accepted, nothing filled: no fee paid
             (event_row(cid, status="CANCELLED", fees=[]), None),
             (event_row(cid, status="FILLED", filled="1", price="0.71"), 0.01),
             (event_row(cid, status="PARTIAL_FILLED", total="3", filled="1", price="0.71"), 0.01),
             (event_row(cid, status="FILLED", filled="1", price="0.71", fees=[]), None),
             (event_row(cid, status="FILLED", filled="1", price="0.71",
                        fees=[{"type": "EXCHANGE_FEE", "actual_value": "0.01"}, {"type": "OTHER", "actual_value": "0.02"}]), None),
             (event_row(cid, status="FILLED", filled="1", price="0.71", fees=[{"type": "EXCHANGE_FEE", "actual_value": "x"}]), None),
             (event_row(cid, status="FILLED", filled="1", price="0.71", fees=[{"type": "EXCHANGE_FEE", "actual_value": "-0.01"}]), None),
             (event_row(cid, status="FILLED", filled="1", price="0.71", fees="junk"), None)]
    for row, fee in cases:
        v, _ = ev(tmp_path, Client(details={cid: {"client_order_id": cid, "orders": [row]}}))
        out = v.order_status("t", expected=f"{TICKER}:yes")
        assert out["fee"] == fee, row
    v, _ = ev(tmp_path, Client(details={cid: {"client_order_id": cid, "orders": [event_row(cid, status="FILLED", filled="1")]}}))
    assert v.order_status("t", expected=f"{TICKER}:yes")["fee_type"] == "EXCHANGE_FEE"


# ---- positions, open orders, cash and cancel in an Events account

def test_positions_in_an_events_account_are_event_names(tmp_path):
    rows = [position_row(quantity="1"), position_row(outcome="no", quantity="2"), position_row("KXB-1", "yes", "3"),
            {"symbol": "SPY", "quantity": "4", "instrument_type": "EQUITY"}]
    v, _ = ev(tmp_path, Client(positions=rows))
    assert v.positions() == {f"{TICKER}:yes": 1.0, f"{TICKER}:no": 2.0, "KXB-1:yes": 3.0}
    assert v.other_positions() == [{"symbol": "SPY", "instrument_type": "EQUITY", "quantity": 4.0}]
    cash, _ = ev(tmp_path, Client(accounts=[{"account_id": "ACC-1", "account_class": "INDIVIDUAL_CASH"}],
                                  positions=[position_row("KXB-1", "yes", "3")]), cls="INDIVIDUAL_CASH")
    assert cash.positions() == {} and cash.other_positions() == [{"symbol": "KXB-1", "instrument_type": "EVENT", "quantity": 3.0}]


@pytest.mark.parametrize("bad", [{"event_outcome": "YES"}, {"event_outcome": None}, {"event_outcome": ""}, {"symbol": "kxb-1"},
                                 {"symbol": "KXB 1"}, {"symbol": "A" * 81}, {"symbol": None}, {"event_outcome": ["yes"]}])
def test_an_event_row_of_the_wrong_shape_is_an_error_everywhere(tmp_path, bad):
    row = {**position_row(), **bad}
    with pytest.raises(wb.WebullError):
        ev(tmp_path, Client(positions=[row]))[0].positions()
    orow = {**event_row("o1", side="SELL"), **bad}
    with pytest.raises(wb.WebullError):
        ev(tmp_path, Client(open_rows=[orow]))[0].open_orders()


def test_open_orders_and_cancel_all_open_name_the_outcome(tmp_path):
    rows = [event_row("o1", side="SELL", total="2", filled="1"), event_row("o2", ticker="KXB-1", outcome="no")]
    v, client = ev(tmp_path, Client(open_rows=rows))
    opened = v.open_orders()
    assert [(o["symbol"], o["units"], o["filled_units"], o["instrument_type"]) for o in opened] == [
        (f"{TICKER}:yes", -2.0, 1.0, "EVENT"), ("KXB-1:no", 1.0, 0.0, "EVENT")]
    out = v.cancel_all_open()
    assert [(o["symbol"], o["status"]) for o in out] == [(f"{TICKER}:yes", "cancel requested"), ("KXB-1:no", "cancel requested")]
    assert v.cancel("t") == {"status": "cancel requested"}


def test_cash_in_an_events_account_is_its_buying_power(tmp_path):
    v, _ = ev(tmp_path)
    assert v.cash() == {"cash": 999.27, "settled_cash": None, "buying_power": 999.27, "currency": "USD"}
    rich = {**OBSERVED_BALANCE, "account_currency_assets": [{**OBSERVED_BALANCE["account_currency_assets"][0], "settled_cash": "5"}]}
    assert ev(tmp_path, Client(balance=rich))[0].cash()["settled_cash"] is None


# ---- invariant 10: nothing secret or unexpected gets out of the new fields

def test_a_ticker_outcome_or_fee_type_holding_a_secret_never_gets_out(tmp_path):
    secret_ticker = "APP-KEY-DO-NOT-PRINT"                   # the key in capitals: a clean ticker shape holding a secret run
    for rows in ([position_row(secret_ticker)], [position_row(outcome=KEY)], [position_row(outcome=SECRET + ":")]):
        with pytest.raises(wb.WebullError) as exc:
            ev(tmp_path, Client(positions=rows))[0].positions()
        assert KEY not in str(exc.value) and SECRET not in str(exc.value) and "APP-KEY" not in str(exc.value)
    with pytest.raises(wb.WebullError) as exc:
        ev(tmp_path, Client(open_rows=[event_row("o1", ticker=secret_ticker)]))[0].open_orders()
    assert "APP-KEY" not in str(exc.value)
    cid = wb.client_order_id("t")
    for fee_type, shown in ((KEY, wb.WITHHELD), ("ZQXWVUTSRPONMLK", wb.WITHHELD), ("EXCHANGE_FEE", "EXCHANGE_FEE"), (None, None)):
        row = event_row(cid, status="FILLED", filled="1", fees=[{"type": fee_type, "actual_value": "0.01"}])
        v = wb.WebullVenue("ZQXWVUTSRPONMLK", SECRET, client=Client(details={cid: {"client_order_id": cid, "orders": [row]}}),
                           account_class="EVENTS_CASH", limits=HIGH, sleep=lambda x: None, clock=lambda: 0.0,
                           confirmation_file=tmp_path / "x.json")
        out = v.order_status("t", expected=f"{TICKER}:yes")
        assert out["fee_type"] == shown and out["fee"] == 0.01
        assert KEY not in repr(out) and "ZQXWVUTSRP" not in repr(out)
    row = event_row(cid, ticker=secret_ticker, status="FILLED", filled="1")
    v, _ = ev(tmp_path, Client(details={cid: {"client_order_id": cid, "orders": [row]}}))
    with pytest.raises(wb.WebullError) as exc:
        v.order_status("t", expected=f"{secret_ticker}:yes")
    assert "APP-KEY" not in str(exc.value)


# ---- rev 2: open orders checked against what was sent, the side read back, untyped rows with long tickers

def test_rev2_our_open_sale_read_back_with_the_other_outcome_never_frees_a_sale(tmp_path):
    """The case seen in the blind check: our open sale of 1 KXQ-1:yes read back as "no", 2 held; a second sale of 2
    must not go, whether this venue sent the first sale (an error) or not (the cautious count)."""
    rows = [position_row("KXQ-1", "yes", "2")]
    c = Client(positions=rows)
    v, _ = ev(tmp_path, c)
    v.submit("KXQ-1:yes", -1, "first", limit_price=0.5)
    c.open_rows = [event_row(wb.client_order_id("first"), ticker="KXQ-1", outcome="no", side="SELL")]
    with pytest.raises(wb.NotTransmitted):
        v.submit("KXQ-1:yes", -2, "second", limit_price=0.5)
    with pytest.raises(wb.WebullError):
        v.open_orders()
    assert len(c.placed()) == 1
    fresh_client = Client(positions=rows, open_rows=list(c.open_rows))
    fresh, _ = ev(tmp_path, fresh_client)
    with pytest.raises(wb.NotTransmitted, match="long-only"):
        fresh.submit("KXQ-1:yes", -2, "second", limit_price=0.5)
    fresh.submit("KXQ-1:yes", -1, "third", limit_price=0.5)              # 2 held, 1 in a sale of either outcome
    assert len(fresh_client.placed()) == 1


def test_rev2_open_orders_check_the_rows_a_caller_names(tmp_path):
    cid = wb.client_order_id("mine")
    row = event_row(cid, ticker="KXQ-1", outcome="no", side="SELL")
    v, _ = ev(tmp_path, Client(open_rows=[row]))
    assert v.open_orders()[0]["symbol"] == "KXQ-1:no"                   # not known: read as given
    v, _ = ev(tmp_path, Client(open_rows=[row]))
    with pytest.raises(wb.WebullError):
        v.open_orders(expected={"mine": "KXQ-1:yes"})
    v, _ = ev(tmp_path, Client(open_rows=[row]))
    assert v.open_orders(expected={"mine": "KXQ-1:no"})[0]["symbol"] == "KXQ-1:no"
    for bad in ({"mine": "kxq:yes"}, {"mine": 7}, ["mine"]):
        with pytest.raises(wb.WebullError):
            ev(tmp_path, Client(open_rows=[row]))[0].open_orders(expected=bad)
    detail = {"client_order_id": cid, "orders": [event_row(cid, ticker="KXQ-1", outcome="yes", side="SELL")]}
    v, c = ev(tmp_path, Client(open_rows=[row], details={cid: detail}, positions=[position_row("KXQ-1", "yes", "5")]))
    v.order_status("mine", expected="KXQ-1:yes")                         # the caller's name, as order_status takes it
    with pytest.raises(wb.WebullError):
        v.open_orders()
    with pytest.raises(wb.NotTransmitted):
        v.submit("KXQ-1:yes", -1, "next", limit_price=0.5)
    assert c.placed() == []


def test_rev2_generated_open_sales_never_free_a_sale(tmp_path):
    """700 seeded accounts: some open orders this venue knows (sent by it, or named by its caller through order_status or
    open_orders), some it does not, some read back with another ticker, outcome or side. By my own count: a known row
    read back differently is an error; a known row is reserved under its own name only; an unknown open sale of the
    ticker is reserved under both outcomes. The sale is sent exactly when nothing is in error and it fits."""
    rng = random.Random(2611)
    tickers = ["KXQ-1", "KXQ-2"]
    sent = refused = errors = 0
    for i in range(700):
        c = Client(positions=[position_row(t, o, "50") for t in tickers for o in ("yes", "no")])
        v, _ = ev(tmp_path, c)
        known = []
        for j in range(rng.randint(0, 3)):
            t, o, side = rng.choice(tickers), rng.choice(["yes", "no"]), rng.choice(["BUY", "SELL"])
            tag, how = f"k{i}-{j}", rng.choice(["submit", "status", "status-side", "expected"])
            cid = wb.client_order_id(tag)
            if how == "submit":
                v.submit(f"{t}:{o}", 1 if side == "BUY" else -1, tag, limit_price=0.5)
                kside = side
            elif how.startswith("status"):
                c.details[cid] = {"client_order_id": cid, "orders": [event_row(cid, ticker=t, outcome=o, side=side)]}
                kside = side if how == "status-side" else None
                v.order_status(tag, expected=f"{t}:{o}", **({"expected_side": side} if kside else {}))
            else:
                v.open_orders(expected={tag: f"{t}:{o}"})
                kside = None
            known.append((cid, t, o, side, kside))
        T, O = rng.choice(tickers), rng.choice(["yes", "no"])
        rows, reserved, error = [], 0, False
        for cid, t, o, side, kside in known:
            rt, ro, rs = t, o, side
            r = rng.random()
            if r < 0.2:
                ro = "no" if o == "yes" else "yes"
            elif r < 0.3:
                rt = tickers[1] if t == tickers[0] else tickers[0]
            elif r < 0.4:
                rs = "SELL" if side == "BUY" else "BUY"
            total = rng.randint(1, 3)
            filled = rng.randint(0, total - 1)
            rows.append(event_row(cid, ticker=rt, outcome=ro, side=rs, total=str(total), filled=str(filled)))
            if (rt, ro) != (t, o) or (kside is not None and rs != kside):
                error = True
            elif rs == "SELL" and (t, o) == (T, O):
                reserved += total - filled
        for j in range(rng.randint(0, 3)):
            t, o, side = rng.choice(tickers), rng.choice(["yes", "no"]), rng.choice(["BUY", "SELL"])
            total = rng.randint(1, 3)
            filled = rng.randint(0, total - 1)
            rows.append(event_row(f"u{i}-{j}", ticker=t, outcome=o, side=side, total=str(total), filled=str(filled)))
            if t == T and side == "SELL":
                reserved += total - filled                                # either outcome: the cautious count
        held = rng.randint(0, 6)
        c.positions_rows = [position_row(T, O, str(held))]
        c.open_rows = rows
        contracts = rng.randint(1, 4)
        before = len(c.placed())
        if not error and contracts <= held - reserved:
            v.submit(f"{T}:{O}", -contracts, f"s{i}", limit_price=0.5)
            p = c.placed()[-1][2][0]
            assert len(c.placed()) == before + 1 and (p["symbol"], p["event_outcome"], p["side"]) == (T, O, "SELL")
            sent += 1
        else:
            with pytest.raises(wb.NotTransmitted):
                v.submit(f"{T}:{O}", -contracts, f"s{i}", limit_price=0.5)
            assert len(c.placed()) == before
            refused += 1
            errors += error
    assert sent > 100 and refused > 100 and errors > 50


def test_rev2_a_side_read_back_that_differs_from_the_side_sent_is_an_error(tmp_path):
    cid = wb.client_order_id("t")
    for units, back in ((1, "SELL"), (-1, "BUY"), (1, "SHORT"), (1, None), (1, "buy"), (-1, "SELL ")):
        row = event_row(cid, side=back, status="FILLED", filled="1", price="0.50")
        c = Client(positions=[position_row(quantity="3")], details={cid: {"client_order_id": cid, "orders": [row]}})
        v, _ = ev(tmp_path, c)
        v.submit(f"{TICKER}:yes", units, "t", limit_price=0.5)
        with pytest.raises(wb.WebullError, match="side"):
            v.order_status("t")
    sold = event_row(cid, side="SELL", status="FILLED", filled="1", price="0.50")
    for side, ok in (("SELL", True), (None, True), ("BUY", False), ("sell", False)):
        v, _ = ev(tmp_path, Client(details={cid: {"client_order_id": cid, "orders": [sold]}}))
        kw = {} if side is None else {"expected_side": side}
        if ok:
            assert v.order_status("t", expected=f"{TICKER}:yes", **kw)["filled_units"] == 1.0
        else:
            with pytest.raises(wb.WebullError):
                v.order_status("t", expected=f"{TICKER}:yes", **kw)
    v, c = ev(tmp_path, Client(positions=[position_row(quantity="3")]))
    v.submit(f"{TICKER}:yes", 1, "bought", limit_price=0.5)
    c.open_rows = [event_row(wb.client_order_id("bought"), side="SELL")]
    with pytest.raises(wb.WebullError, match="side"):
        v.open_orders()


def test_rev2_an_untyped_open_row_with_a_long_ticker_is_read_in_an_events_venue(tmp_path):
    long = TICKER                                                          # 27 characters
    row = event_row("u1", ticker=long, outcome="no", side="SELL")
    row.pop("instrument_type")
    v, c = ev(tmp_path, Client(open_rows=[row], positions=[position_row(long, "yes", "1")]))
    got = v.open_orders()
    assert (got[0]["symbol"], got[0]["instrument_type"], got[0]["units"]) == (f"{long}:no", None, -1.0)
    with pytest.raises(wb.NotTransmitted, match="no recognised instrument type"):   # an untyped row of the ticker blocks
        v.submit(f"{long}:yes", -1, "s", limit_price=0.5)
    for kind in (None, "event", "WHAT"):
        bare = {k: x for k, x in row.items() if k != "event_outcome"}
        if kind is not None:
            bare["instrument_type"] = kind
        v, c = ev(tmp_path, Client(open_rows=[bare], positions=[position_row(long, "yes", "1")]))
        got = v.open_orders()
        assert (got[0]["symbol"], got[0]["instrument_type"]) == (long, kind)
        with pytest.raises(wb.NotTransmitted):                            # what it is cannot be told: no sale of it
            v.submit(f"{long}:yes", -1, "s", limit_price=0.5)
        assert c.placed() == []
    v, c = ev(tmp_path, Client(open_rows=[{**row, "symbol": "kx-lower-case-ticker-long-name"}]))
    with pytest.raises(wb.WebullError):
        v.open_orders()
    cash, _ = ev(tmp_path, Client(accounts=[{"account_id": "ACC-1", "account_class": "INDIVIDUAL_CASH"}], open_rows=[row]),
                 cls="INDIVIDUAL_CASH")
    with pytest.raises(wb.WebullError, match="not a clean symbol"):
        cash.open_orders()                                                 # any other venue: as before


def test_rev2_a_mismatched_open_row_does_not_stop_the_cancels(tmp_path):
    """Cancelling only reduces exposure: every open row is cancelled by its client order id, also rows whose name or
    side disagrees with what was sent; the disagreement is raised only after every cancel was attempted."""
    v, c = ev(tmp_path, Client())
    v.submit("KXQ-1:yes", 1, "a", limit_price=0.5)
    v.submit("KXQ-1:yes", 1, "b", limit_price=0.5)
    a, b = wb.client_order_id("a"), wb.client_order_id("b")
    c.open_rows = [event_row(a, ticker="KXQ-1", outcome="no"), event_row(b, ticker="KXQ-1", side="SELL"),
                   event_row("u1", ticker="KXQ-1")]
    with pytest.raises(wb.WebullError) as caught:
        v.cancel_all_open()
    assert [x[2] for x in c.calls if x[0] == "cancel"] == [a, b, "u1"]
    assert a in str(caught.value) and b in str(caught.value) and "u1" not in str(caught.value)
    with pytest.raises(wb.WebullError):
        v.open_orders()                                                    # the list itself still refuses the rows
    c.open_rows = [event_row(a, ticker="KXQ-1"), event_row("u1", ticker="KXQ-1")]
    assert [x["status"] for x in v.cancel_all_open()] == ["cancel requested", "cancel requested"]
