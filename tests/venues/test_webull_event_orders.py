"""T19: event-contract orders in the Events account (docs/specs/2026-11-13-event-orders-design.md, part 1).

Every test uses a stand-in for the SDK's TradeClient; nothing here builds a Webull client or touches the network.
The stand-in serves the shapes observed in Webull's test environment on 2026-11-13 (the ticket's CONTEXT)."""
import importlib.util
import random
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


def ev(tmp_path, client=None, cls="EVENTS_CASH", env="sandbox", clock=None, **kw):
    client = client if client is not None else Client()
    v = wb.WebullVenue(KEY, SECRET, environment=env, client=client, confirmation_file=tmp_path / "webull_live.json",
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
                reserved[(t, o)] = reserved.get((t, o), 0) + total - filled
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


def test_generated_stock_and_crypto_inputs_behave_exactly_as_before(tmp_path, head):
    """600 seeded accounts and requests on venues that are not Events venues: every public method gives the same value
    or raises the same kind of error with the same text as the adapter before T19, and the SDK sees the same calls
    with the same payloads."""
    rng = random.Random(4242)
    compared = placed = accepted = 0
    for _ in range(600):
        s = _spec(rng)
        pair = []
        for module in (head, wb):
            client = SpecClient(s)
            venue = module.WebullVenue(KEY, SECRET, client=client, confirmation_file=tmp_path / "none.json", account_class=s["wanted"],
                                       limits=HIGH, sleep=lambda x: None, clock=lambda: 0.0)
            results = [venue.name,
                       _outcome(lambda: venue.submit(s["symbol"], s["units"], "h-tag", price=s["price"], limit_price=s["limit"],
                                                     session=s["session"])),
                       _outcome(venue.positions), _outcome(venue.other_positions), _outcome(venue.open_orders),
                       _outcome(lambda: venue.order_status("h-tag")), _outcome(venue.cash), _outcome(venue.balance),
                       _outcome(lambda: venue.cancel("h-tag")), _outcome(venue.cancel_all_open), _outcome(venue.check),
                       _outcome(lambda: venue.account_class)]
            pair.append((results, client.calls))
        assert pair[0] == pair[1], s
        compared += 1
        placed += any(c[0] == "place" for c in pair[1][1])
        accepted += pair[1][0][1][0] == "ok"
    assert compared == 600 and placed > 150 and accepted > 100


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
            if side == "SELL" and (ot, oo) == (t, o):
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
    conf = tmp_path / "webull_live.json"
    conf.write_text('{"account_id": "%s", "confirmed_by": "Burton", "max_order_value": 100000, '
                    '"i_understand_this_trades_real_money": true}' % EV_ID)
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
