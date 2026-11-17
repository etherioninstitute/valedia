"""T19: the event-contract practice command (docs/specs/2026-11-13-event-orders-design.md, part 2).

The real adapter runs against a stand-in for the SDK's TradeClient that behaves like the paper Events account seen on
2026-11-13 (a purchase fills at the ask, a sale at the bid, on the next read; one fee entry from acceptance, none after
a cancel). Kalshi's book is a function the test passes. Everything is written in a temporary folder; nothing here
touches the network, the real secrets file or the repository's files."""
import json
import random
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from valedia_trader.execution_and_risk import event_practice as ep
from valedia_trader.execution_and_risk.orders import OrderLedger
from valedia_trader.execution_and_risk.order_step import ledger_path_for, open_ledger
from valedia_trader.research_loop.paper import Journal
from valedia_trader.venues import kalshi_data as kd
from valedia_trader.venues import webull as wb

KEY, SECRET = "app-key-do-not-print", "app-secret-do-not-print"
HIGH = {kind: (10 ** 9, 1) for kind in wb.CALL_KINDS}
T = "KXNBAGAME-26NOV13ATLCHI-ATL"
OTHER = "KXOTHER-26NOV20-B1"
EV_ID = "8174402917735510004"
ACCOUNTS = [{"account_id": "8174402917735510002", "account_type": "CASH", "account_class": "INDIVIDUAL_CASH"},
            {"account_id": EV_ID, "account_type": "CASH", "account_class": "EVENTS_CASH", "account_label": "Events Cash"}]


@pytest.fixture(autouse=True)
def _home_is_temporary(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def ny(text):
    return pd.Timestamp(text, tz="America/New_York").timestamp()


class Clock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t


class Response:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body


class ServerException(Exception):
    def __init__(self, error_code, error_msg, http_status, request_id=None):
        self.error_code, self.error_msg, self.http_status, self.request_id = error_code, error_msg, http_status, request_id

    def __str__(self):
        return f"request headers: x-app-key={KEY}"


class Exchange:
    """The paper Events account as a stand-in TradeClient. `asks` / `bids` per name decide fills: a purchase whose limit is
    at least the ask fills at the ask on the next read of its detail, a sale whose limit is at most the bid at the bid."""

    def __init__(self, held=None):
        self.calls, self.orders = [], {}
        self.held = dict(held or {})
        self.asks, self.bids = {}, {}
        self.place_mode = "ok"
        self.cancel_mode = "ok"                              # "ignore": the cancel is answered but the order stays open
        self.bp = "1000"                                     # the Events balance: buying power only (None: left out)
        self.cash = None                                     # cents: when set, buying power is this, moved by each fill
        self.charged_extra = 0                               # cents a sale costs beyond the fee the venue reports
        self.fees_given, self.price_given = True, True      # False: the order row withholds the fee or the price
        self.account_v2 = SimpleNamespace(get_account_list=lambda: self._log(("accounts",), ACCOUNTS),
                                          get_account_balance=lambda a: self._log(("balance",), self._balance()),
                                          get_account_position=lambda a: self._log(("positions",), self._rows()))
        self.order_v3 = SimpleNamespace(place_order=self._place, cancel_order=self._cancel, get_order_detail=self._detail,
                                        list_order_open=lambda a, key=None: self._log(("open",), {"data": [
                                            self._row(c) for c, o in self.orders.items() if o["status"] == "SUBMITTED"]}))

    def _log(self, what, body):
        self.calls.append(what)
        return Response(body)

    def _balance(self):
        if self.bp == "raise":
            raise ServerException("INTERNAL", "boom", 500)
        bp = self.bp if self.cash is None else f"{self.cash / 100:.2f}"
        entry = {"currency": "USD"} if bp is None else {"currency": "USD", "buying_power": bp}
        return {"total_asset_currency": "USD", "account_currency_assets": [entry]}

    def _rows(self):
        return [{"currency": "USD", "quantity": str(q), "cost": "0.71", "position_id": "P", "symbol": n.split(":")[0],
                 "instrument_type": "EVENT", "cost_price": "0.71", "last_price": "0.7", "unrealized_profit_loss": "0",
                 "event_outcome": n.split(":")[1]} for n, q in self.held.items() if q]

    def _place(self, a, orders):
        o = dict(orders[0])
        self.calls.append(("place", o))
        if self.place_mode == "500":
            raise ServerException("INTERNAL", "boom", 500)
        self.orders[o["client_order_id"]] = {**o, "status": "SUBMITTED", "filled": 0, "price": "0"}
        return Response({"client_order_id": o["client_order_id"], "order_id": "ORD-1"})

    def _cancel(self, a, cid):
        self.calls.append(("cancel", cid))
        if self.cancel_mode == "ok" and self.orders.get(cid, {}).get("status") == "SUBMITTED":
            self.orders[cid]["status"] = "CANCELLED"
        return Response({"client_order_id": cid, "order_id": "ORD-1"})

    def _fill(self, cid):
        o = self.orders[cid]
        name = f"{o['symbol']}:{o['event_outcome']}"
        limit, q = Decimal(o["limit_price"]), int(o["quantity"])
        if o["status"] != "SUBMITTED":
            return
        if o["side"] == "BUY" and name in self.asks and limit >= self.asks[name]:
            o.update(status="FILLED", filled=q, price=str(self.asks[name]))
            self.held[name] = self.held.get(name, 0) + q
            if self.cash is not None:                        # the price of each contract and the one 0.01 fee entry
                self.cash -= int(self.asks[name] * 100) * q + 1
        elif o["side"] == "SELL" and name in self.bids and limit <= self.bids[name]:
            o.update(status="FILLED", filled=q, price=str(self.bids[name]))
            self.held[name] = self.held.get(name, 0) - q
            if self.cash is not None:
                self.cash += int(self.bids[name] * 100) * q - 1 - self.charged_extra

    def _row(self, cid):
        o = self.orders[cid]
        fees = [] if o["status"] == "CANCELLED" or not self.fees_given else [
            {"type": "EXCHANGE_FEE", "actual_value": "0.01", "receivable_value": "0.01"}]
        return {"client_order_id": cid, "order_id": "ORD-1", "symbol": o["symbol"], "side": o["side"], "status": o["status"],
                "fees": fees, "commission": {}, "order_type": "LIMIT", "instrument_type": "EVENT", "entrust_type": "QTY",
                "total_quantity": o["quantity"], "filled_quantity": str(o["filled"]),
                "filled_price": o["price"] if self.price_given else "0",
                "limit_price": o["limit_price"], "time_in_force": "DAY", "event_outcome": o["event_outcome"]}

    def _detail(self, a, cid):
        self.calls.append(("detail", cid))
        if cid not in self.orders:
            raise ServerException("OPENAPI_PARAM_ERR", "Parameter error, Order not present.", 417)
        self._fill(cid)
        return Response({"client_order_id": cid, "combo_order_id": "C", "combo_type": "NORMAL", "orders": [self._row(cid)]})

    def placed(self):
        return [c[1] for c in self.calls if c[0] == "place"]


def book_of(bid_yes, ask_yes):
    """Kalshi's book in KalshiData.orderbook's shape: yes bids and no bids, best first."""
    yes = [(bid_yes, 100.0)] if bid_yes is not None else []
    no = [(round(1 - ask_yes, 2), 100.0)] if ask_yes is not None else []
    return {"yes": yes, "no": no}


class World:
    """One practice setting: the exchange, the adapter, the ledger, the state file, the journal, the clock and the book."""

    def __init__(self, tmp_path, held=None, env="sandbox", cls="EVENTS_CASH", when="2026-11-13 14:00"):
        self.dir = tmp_path
        self.exchange = Exchange(held)
        self.clock = Clock(ny(when))
        self.venue = wb.WebullVenue(KEY, SECRET, environment=env, client=self.exchange, account_class=cls, limits=HIGH,
                                    sleep=lambda s: None, clock=lambda: 0.0, confirmation_file=tmp_path / "webull_live.json")
        self.ledger_path = ledger_path_for(tmp_path / "memory", self.venue.name)
        self.ledger = open_ledger(self.ledger_path, clock=self.clock)
        self.state_path = tmp_path / "memory" / f"event_practice-{self.venue.name}.sqlite"
        self.journal = Journal(tmp_path / "journal")
        self.books = {T: book_of(0.70, 0.71), OTHER: book_of(0.40, 0.41)}
        self.book_reads = []
        self.markets = {}                                    # Kalshi's records by ticker (KalshiData.market's shape)
        self.market_reads = []

    def book(self, ticker):
        self.book_reads.append(ticker)
        return self.books[ticker]

    def market(self, ticker):
        self.market_reads.append(ticker)
        rec = self.markets.get(ticker)
        if rec is None:
            raise kd.HttpError(404, "u")
        if isinstance(rec, Exception):
            raise rec
        return dict(rec) if isinstance(rec, dict) else rec

    def quote(self, name, bid, ask):
        self.exchange.bids[name], self.exchange.asks[name] = Decimal(str(bid)), Decimal(str(ask))

    def run(self, **kw):
        return ep.run_event_practice(ledger=self.ledger, venue=self.venue, journal=self.journal, state=self.state_path,
                                     book=kw.pop("book", self.book), market=kw.pop("market", self.market), clock=self.clock,
                                     **kw)

    def state(self):
        return ep.PracticeState(self.state_path)

    def entries(self, event=None):
        out = []
        for path in sorted((self.dir / "journal").glob("*.jsonl")):
            out += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        return [e for e in out if e.get("type") == "event_practice" and (event is None or e.get("event") == event)]


def buy(w, contracts=1, outcome="yes", limit=None, **kw):
    return w.run(ticker=T, outcome=outcome, contracts=contracts, side="buy", limit=limit, **kw)


def sell(w, contracts=1, outcome="yes", limit=None, **kw):
    return w.run(ticker=T, outcome=outcome, contracts=contracts, side="sell", limit=limit, **kw)


# ---- the whole path: a resting order cancelled, a purchase and a sale, a second run that journals nothing new

def test_a_resting_order_a_purchase_a_sale_and_a_second_run(tmp_path):
    w = World(tmp_path)
    w.quote(f"{T}:yes", 0.70, 0.71)
    rest = buy(w, limit="0.50")                                 # below the ask: it rests
    assert rest["status"] == "done", rest
    assert w.exchange.placed()[-1]["limit_price"] == "0.50" and rest["order"]["state"] == "accepted"
    out = w.run(reconcile_only=True)                            # cancels it; its fee entry was never a fee paid
    assert out["status"] == "done", out
    assert [c["answer"] for c in out["cancels"]] == ["cancel requested"]
    assert w.entries("fill") == []
    bought = buy(w)
    assert bought["status"] == "done" and w.exchange.placed()[-1]["limit_price"] == "0.72"     # the ask plus one cent
    w.clock.t += 10
    w.run(reconcile_only=True)
    fills = w.entries("fill")
    assert [(f["symbol"], f["units"], f["price"], f["fee"]) for f in fills] == [(f"{T}:yes", 1.0, 0.71, 0.01)]
    sold = sell(w)
    assert sold["status"] == "done" and w.exchange.placed()[-1]["limit_price"] == "0.69"       # the bid less one cent
    assert w.exchange.placed()[-1]["side"] == "SELL" and w.exchange.placed()[-1]["event_outcome"] == "yes"
    w.clock.t += 10
    w.run(reconcile_only=True)
    again = w.run(reconcile_only=True)
    assert again["status"] == "done" and again["fills_journaled"] == 0
    fills = w.entries("fill")
    assert [(f["units"], f["price"], f["fee"]) for f in fills] == [(1.0, 0.71, 0.01), (-1.0, 0.70, 0.01)]
    assert len({f["fill_id"] for f in fills}) == 2 and w.exchange.held.get(f"{T}:yes") == 0
    assert len(w.exchange.placed()) == 3


# ---- invariant 6: the test environment only, checked before anything is read

def test_production_stops_before_anything_is_read_or_written(tmp_path):
    w = World(tmp_path, env="production")
    conf = tmp_path / "webull_live.json"
    conf.write_text(json.dumps({"account_id": EV_ID, "confirmed_by": "Burton", "max_order_value": 1000,
                                "i_understand_this_trades_real_money": True}))
    out = buy(w)
    assert out["status"] == "stopped" and out["stage"] == "environment"
    assert w.exchange.calls == [] and w.book_reads == [] and not w.state_path.exists() and w.entries() == []


@pytest.mark.parametrize("change", ["host", "env", "cash-venue", "ledger"])
def test_a_venue_that_is_not_plainly_the_paper_events_venue_is_refused(tmp_path, change):
    w = World(tmp_path, cls="EVENTS_CASH" if change != "cash-venue" else "INDIVIDUAL_CASH")
    if change == "host":
        w.venue.host = "api.webull.com"
    elif change == "env":
        w.venue.environment = "production"
    elif change == "ledger":
        w.ledger = OrderLedger(tmp_path / "other.sqlite", not_sent=(Exception,), clock=w.clock)
    out = buy(w)
    assert out["status"] == "stopped" and out["stage"] == "environment"
    assert w.exchange.calls == [] and w.book_reads == [] and not w.state_path.exists()


def test_the_command_refuses_a_configured_production_environment_before_building_anything(tmp_path, capsys):
    built = []
    code = ep.main(["--ticker", T, "--outcome", "yes", "--contracts", "1", "--side", "buy"],
                   environ={"WEBULL_ENV": "production"}, build_venue=lambda env: built.append(env), root=tmp_path)
    assert code == 2 and built == [] and "test environment" in capsys.readouterr().err
    assert not (tmp_path / "memory").exists() and not (tmp_path / "journal").exists()


def test_the_command_builds_the_events_venue_and_its_own_ledger(tmp_path, capsys):
    seen = {}
    exchange = Exchange()

    def build(env):
        seen.update(env)
        return wb.WebullVenue(KEY, SECRET, client=exchange, account_class=env["WEBULL_ACCOUNT_CLASS"], limits=HIGH,
                              sleep=lambda s: None, clock=lambda: 0.0, confirmation_file=tmp_path / "x.json")
    code = ep.main(["--ticker", T, "--outcome", "no", "--contracts", "2", "--side", "buy", "--limit", "0.20"],
                   environ={"WEBULL_ACCOUNT_CLASS": "INDIVIDUAL_CASH", "WEBULL_ACCOUNT_ID": "8174402917735510002"},
                   build_venue=build, root=tmp_path, book=lambda t: book_of(0.70, 0.71), clock=Clock(ny("2026-11-13 12:00")))
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["status"] == "done", out
    assert seen["WEBULL_ACCOUNT_CLASS"] == "EVENTS_CASH" and "WEBULL_ACCOUNT_ID" not in seen
    assert (tmp_path / "memory" / "orders-webull-sandbox-events.sqlite").exists()
    assert not (tmp_path / "memory" / "orders-webull-sandbox.sqlite").exists()
    p = exchange.placed()[0]
    assert (p["symbol"], p["event_outcome"], p["side"], p["quantity"], p["limit_price"]) == (T, "no", "BUY", "2", "0.20")


# ---- invariant 7: the caps hold whatever is passed

@pytest.mark.parametrize("kw", [dict(contracts=6), dict(contracts=0), dict(contracts=-1), dict(contracts=True), dict(contracts=2.5),
                                dict(contracts="3"), dict(contracts=1.0), dict(limit="1.00"), dict(limit="0.00"), dict(limit="0.505"),
                                dict(limit=0.5), dict(limit="abc"), dict(limit="-0.5"), dict(limit="NaN"), dict(outcome="YES"),
                                dict(outcome="maybe"), dict(side="short"), dict(ticker="kxnba:yes"), dict(ticker="spy"),
                                dict(ticker="A" * 81)])
def test_a_request_outside_the_caps_is_refused_before_anything_is_read(tmp_path, kw):
    w = World(tmp_path)
    args = {"ticker": T, "outcome": "yes", "contracts": 1, "side": "buy", "limit": None, **kw}
    out = w.run(**args)
    assert out["status"] == "stopped" and out["stage"] == "input", out
    assert w.exchange.calls == [] and w.book_reads == []


@pytest.mark.parametrize("book, why", [(book_of(None, 0.71), "two-sided"), (book_of(0.70, None), "two-sided"),
                                       (book_of(0.50, 0.61), "spread"), (book_of(0.705, 0.71), "cent"),
                                       ({"yes": [(0.7, 0.0)], "no": [(0.29, 5.0)]}, "two-sided"), (book_of(0.72, 0.71), "spread")])
def test_a_book_that_is_one_sided_wide_or_off_the_cent_sends_nothing(tmp_path, book, why):
    w = World(tmp_path)
    out = buy(w, book=lambda t: book)
    assert out["status"] == "stopped" and out["stage"] == "quote" and why in out["reason"], out
    assert w.exchange.placed() == []


def test_a_given_limit_may_not_cross_the_book_by_more_than_two_cents(tmp_path):
    w = World(tmp_path)
    assert buy(w, limit="0.74")["stage"] == "limit"                              # the ask 0.71 plus 3 cents
    assert buy(w, limit="0.73")["status"] == "done"
    w2 = World(tmp_path / "b", held={f"{T}:yes": 1})
    w2.quote(f"{T}:yes", 0.70, 0.71)
    assert buy(w2, limit="0.72")["status"] == "done"
    w2.clock.t += 10
    w2.run(reconcile_only=True)
    assert sell(w2, limit="0.67")["stage"] == "limit"                            # the bid 0.70 less 3 cents
    assert sell(w2, limit="0.68")["status"] == "done"


def test_the_default_limit_never_leaves_0_01_to_0_99(tmp_path):
    w = World(tmp_path)
    w.books[T] = book_of(0.90, 0.99)
    assert buy(w)["status"] == "done" and w.exchange.placed()[-1]["limit_price"] == "0.99"
    w = World(tmp_path / "b")
    w.books[T] = book_of(0.01, 0.05)
    assert buy(w, outcome="no")["status"] == "done"                              # no: bid 0.95, ask 0.99
    assert w.exchange.placed()[-1]["limit_price"] == "0.99" and w.exchange.placed()[-1]["event_outcome"] == "no"


def test_a_quote_older_than_20_seconds_when_the_order_would_go_sends_nothing(tmp_path):
    w = World(tmp_path)

    def slow(t):
        w.clock.t += 21
        return book_of(0.70, 0.71)
    out = buy(w, book=slow)
    assert out["status"] == "stopped" and "20" in out["reason"] and w.exchange.placed() == []


def test_ten_orders_a_new_york_day_across_runs(tmp_path):
    w = World(tmp_path, when="2026-11-13 23:30")                                 # 23:30 in New York: still the 13th
    for i in range(10):
        out = buy(w, limit="0.10")                                               # rests; the next run cancels it
        assert out["status"] == "done", (i, out)
        w.clock.t += 5
    out = buy(w, limit="0.10")
    assert out["status"] == "stopped" and out["stage"] == "cap" and len(w.exchange.placed()) == 10
    w.clock.t = ny("2026-11-14 00:01")
    assert buy(w, limit="0.10")["status"] == "done" and len(w.exchange.placed()) == 11


def test_generated_requests_never_pass_a_cap(tmp_path):
    """300 seeded requests and books: every order that reaches the exchange has 1 to 5 whole contracts, a value of at most
    5 dollars, a limit from 0.01 to 0.99 that crosses the book by at most 2 cents, on a two-sided book at most 10 cents
    wide (each checked here by my own arithmetic on the book the run was given)."""
    rng = random.Random(19)
    sent = 0
    for i in range(300):
        w = World(tmp_path / f"g{i}")
        bid = rng.choice([None, 0.01, 0.2, 0.5, 0.9, 0.97, 0.98])
        ask = None if bid is None or rng.random() < 0.1 else round(min(0.99, bid + rng.choice([0.01, 0.02, 0.05, 0.1, 0.11, 0.3])), 2)
        book = book_of(bid, ask) if bid is not None else book_of(None, 0.5)
        outcome = rng.choice(["yes", "no"])
        contracts = rng.choice([1, 2, 5, 6, 0, 10])
        limit = rng.choice([None, None, "0.01", "0.50", "0.99", "0.30", "0.97"])
        out = w.run(ticker=T, outcome=outcome, contracts=contracts, side="buy", limit=limit, book=lambda t: book)
        for p in w.exchange.placed():
            sent += 1
            lim, q = Decimal(p["limit_price"]), int(p["quantity"])
            own_bid = Decimal(str(bid)) if outcome == "yes" else Decimal("1") - Decimal(str(ask))
            own_ask = Decimal(str(ask)) if outcome == "yes" else Decimal("1") - Decimal(str(bid))
            assert 1 <= q <= 5 and q * lim <= 5 and Decimal("0.01") <= lim <= Decimal("0.99")
            assert lim <= own_ask + Decimal("0.02") and own_ask - own_bid <= Decimal("0.10") and own_bid < own_ask
            assert p["event_outcome"] == outcome and p["side"] == "BUY"
        assert len(w.exchange.placed()) <= 1 and (out["status"] == "done") == bool(w.exchange.placed())
    assert sent > 40


# ---- invariant 8: sent at most once, journaled once, an unknown outcome blocks

def test_an_unknown_outcome_blocks_and_nothing_more_is_sent(tmp_path):
    w = World(tmp_path)
    w.exchange.place_mode = "500"
    out = buy(w)
    assert out["status"] == "stopped" and out["stage"] == "send" and "unknown" in out["reason"]
    assert len(w.exchange.placed()) == 1
    w.exchange.place_mode = "ok"
    again = buy(w)
    assert again["status"] == "stopped" and again["stage"] == "reconcile" and len(w.exchange.placed()) == 1
    assert w.ledger.orders(["unknown"])


def test_each_fill_is_journaled_once_even_if_marking_it_applied_failed(tmp_path, monkeypatch):
    w = World(tmp_path)
    w.quote(f"{T}:yes", 0.70, 0.71)                                              # it fills on the run's last reconcile
    real = OrderLedger.mark_applied
    monkeypatch.setattr(OrderLedger, "mark_applied", lambda self, ids: (_ for _ in ()).throw(OSError("disk")))
    out = buy(w)
    assert out["status"] == "stopped" and len(w.entries("fill")) == 1 and len(w.exchange.placed()) == 1
    w.clock.t += 10
    monkeypatch.setattr(OrderLedger, "mark_applied", real)
    out = w.run(reconcile_only=True)
    assert out["status"] == "done" and len(w.entries("fill")) == 1 and out["fills_journaled"] == 0


def test_a_sale_is_only_of_contracts_this_program_bought(tmp_path):
    w = World(tmp_path, held={f"{T}:yes": 1})                                    # the owner's own contract, held to the result
    out = sell(w)
    assert out["status"] == "stopped" and out["stage"] == "sale" and w.exchange.placed() == []
    w.quote(f"{T}:no", 0.28, 0.29)
    buy(w, outcome="no", limit="0.30")
    w.clock.t += 10
    w.run(reconcile_only=True)
    out = sell(w)                                                                # bought no, not yes
    assert out["status"] == "stopped" and out["stage"] == "sale"
    assert sell(w, outcome="no")["status"] == "done"
    assert [(p["event_outcome"], p["side"]) for p in w.exchange.placed()] == [("no", "BUY"), ("no", "SELL")]


# ---- invariant 9: settlement is not guessed

def test_a_vanished_event_holding_blocks_and_a_person_must_look(tmp_path):
    w = World(tmp_path)
    w.quote(f"{T}:yes", 0.70, 0.71)
    buy(w)
    w.clock.t += 10
    assert w.run(reconcile_only=True)["status"] == "done"
    w.exchange.held[f"{T}:yes"] = 0                                              # settled: gone without an order
    out = buy(w)
    assert out["status"] == "stopped" and out["stage"] == "reconcile" and "person must look" in out["reason"]
    assert "settlement" in out["reason"] and len(w.exchange.placed()) == 1 and w.book_reads == [T]


def test_a_baseline_is_recorded_at_the_first_run_and_never_traded(tmp_path):
    w = World(tmp_path, held={f"{T}:yes": 1, "KXB-1:no": 2})
    out = w.run(reconcile_only=True)
    assert out["status"] == "done" and w.entries("baseline")[0]["positions"] == {f"{T}:yes": 1.0, "KXB-1:no": 2.0}
    assert ep.PracticeState(w.state_path).baseline() == {f"{T}:yes": 1.0, "KXB-1:no": 2.0}


def test_nothing_secret_reaches_the_journal_or_the_summary(tmp_path):
    w = World(tmp_path)
    w.exchange.place_mode = "500"
    out = buy(w)
    text = json.dumps(out) + json.dumps(w.entries())
    assert KEY not in text and SECRET not in text and "x-app-key" not in text


# ---- rev 2: a later run checks the side the ledger recorded

def test_rev2_a_later_run_checks_the_side_the_ledger_recorded(tmp_path):
    def later_venue():
        return wb.WebullVenue(KEY, SECRET, environment="sandbox", client=w.exchange, account_class="EVENTS_CASH",
                              limits=HIGH, sleep=lambda s: None, clock=lambda: 0.0,
                              confirmation_file=tmp_path / "webull_live.json")
    w = World(tmp_path)
    w.quote(f"{T}:yes", 0.40, 0.71)
    assert buy(w, limit="0.50")["status"] == "done"                      # rests: below the ask
    cid = w.exchange.placed()[-1]["client_order_id"]
    w.exchange.orders[cid]["side"] = "SELL"                               # read back as a sale (and fills as one)
    w.quote(f"{T}:yes", 0.60, 0.71)
    w.venue = later_venue()                                               # a later process: this object sent nothing
    w.clock.t += 10
    out = w.run(reconcile_only=True)
    assert out["status"] != "done" and w.entries("fill") == [] and w.ledger.fills() == []
    w.exchange.orders[cid].update(side="BUY", status="SUBMITTED", filled=0, price="0")
    w.exchange.held.pop(f"{T}:yes", None)
    w.quote(f"{T}:yes", 0.40, 0.45)                                       # now the purchase fills, as sent
    w.venue = later_venue()
    w.clock.t += 10
    w.run(reconcile_only=True)
    assert [(f["symbol"], f["units"]) for f in w.entries("fill")] == [(f"{T}:yes", 1.0)]


# ---- T45-1: settlement of an event holding, checked against Kalshi's public record, fail closed

# Kalshi's public record of T as KalshiData.market gives it (read by the lead on 2026-11-17): a losing yes, at 0.
TONIGHT = {"ticker": T, "market_type": "binary", "status": "finalized", "result": "no", "settlement_value_dollars": "0.0000",
           "settlement_ts": "2026-11-14T03:49:44.800032Z", "tier": "live"}
WIN = {**TONIGHT, "result": "yes", "settlement_value_dollars": "1.0000"}
AFTER = "2026-11-16 12:00"                                     # New York; Kalshi's settlement_ts is in the past by then
NOW = ny(AFTER)


def bad_values(good):
    """Every unsafe variant of one exact str: wrong types, empty, padded, another case."""
    out = [True, False, 1, 0, 1.0, 0.0, None, [good], {good: 1}, "", " ", f" {good}", f"{good} ", f"{good}\n"]
    return out + [c for c in {good.upper(), good.lower()} if c != good]


VARIANTS = ([("ticker", v) for v in bad_values(T)] + [("ticker", "KXNBAGAME-26NOV13ATLCHI-CHI")]
            + [("market_type", v) for v in bad_values("binary")] + [("market_type", "scalar")]
            + [("status", v) for v in bad_values("finalized")]
            + [("status", s) for s in ("determined", "settled", "closed", "active", "amended", "disputed", "initialized")]
            + [("result", v) for v in bad_values("no")] + [("result", r) for r in ("void", "scalar", "all_no", "yes ")]
            + [("settlement_value_dollars", v) for v in bad_values("0.0000")]
            + [("settlement_value_dollars", v) for v in ("1.0000", "0.5000", "0.0001", "0", "0.00", "0.000", "-0.0000", "0E-4")]
            + [("settlement_ts", v) for v in bad_values("2026-11-14T03:49:44.800032Z")]
            + [("settlement_ts", v) for v in ("2026-11-14", "2026-11-14T03:49:44", "2026-11-14T03:49:44+00:00",
                                               "2026-11-14 03:49:44Z", "2026-13-14T03:49:44Z", "2026-11-14T03:49:44.Z",
                                               "2027-11-14T03:49:44Z", "2026-11-14T03:49:44.1234567Z")])


@pytest.mark.parametrize("field, value", VARIANTS)
def test_only_the_exact_safe_record_is_a_settlement(field, value):
    rec = {**TONIGHT, field: value}
    with pytest.raises(ep.NotSettled):
        ep.judge_settlement(rec, T, NOW)
    missing = {k: v for k, v in TONIGHT.items() if k != field}
    with pytest.raises(ep.NotSettled):
        ep.judge_settlement(missing, T, NOW)


def test_the_exact_records_are_settlements_and_nothing_else_is():
    assert ep.judge_settlement(dict(TONIGHT), T, NOW) == Decimal("0.00")
    assert ep.judge_settlement(dict(WIN), T, NOW) == Decimal("1.00")
    for rec in (None, [], "finalized", TONIGHT.items()):
        with pytest.raises(ep.NotSettled):
            ep.judge_settlement(rec, T, NOW)
    with pytest.raises(ep.NotSettled, match="not later than"):                     # a fill after Kalshi's settlement
        ep.judge_settlement(dict(TONIGHT), T, NOW, after=pd.Timestamp("2026-11-14T03:49:45Z").timestamp())
    with pytest.raises(ep.NotSettled, match="past"):
        ep.judge_settlement(dict(TONIGHT), T, pd.Timestamp("2026-11-14T03:49:44Z").timestamp())
    with pytest.raises(ep.NotSettled, match="determined"):                          # it names the status it saw
        ep.judge_settlement({**TONIGHT, "status": "determined"}, T, NOW)


def baseline_world(tmp_path, units=1):
    """The owner's yes contract of T in the baseline (bought by hand), then gone from the account."""
    w = World(tmp_path, held={f"{T}:yes": units})
    assert w.run(reconcile_only=True)["status"] == "done"
    w.clock.t = NOW
    return w


def test_tonights_losing_baseline_holding_is_closed_once_at_zero_and_the_run_goes_on(tmp_path):
    w = baseline_world(tmp_path)
    w.exchange.held[f"{T}:yes"] = 0
    w.markets[T] = TONIGHT
    out = w.run(reconcile_only=True)
    assert out["status"] == "done", out
    s = w.entries("settlement")
    assert len(s) == 1 and out["settlements_journaled"] == 1
    e = s[0]
    assert (e["symbol"], e["units"], e["price"], e["baseline_units"], e["program_units"]) == (f"{T}:yes", -1.0, 0.0, 1.0, 0.0)
    assert e["fee"] is None and e["fee_seen"] == "none seen" and type(e["settlement_id"]) is int
    assert e["kalshi"] == TONIGHT and e["witnesses"] == "kalshi_record+cash_equation" and e["confirmed_by"] is None
    assert w.ledger.last_reconcile()["clean"] and w.ledger.blocked() == []
    again = w.run(reconcile_only=True)
    assert again["status"] == "done" and len(w.entries("settlement")) == 1 and w.market_reads == [T]
    st = w.state()
    assert st.baseline() == {f"{T}:yes": 1.0} and [r["symbol"] for r in st.settlements()] == [f"{T}:yes"]
    for side in (buy, sell):                                                     # nothing more on a settled ticker
        for outcome in ("yes", "no"):
            out = side(w, outcome=outcome)
            assert out["status"] == "stopped" and out["stage"] == "settled", out
    assert w.exchange.placed() == [] and w.book_reads == []
    w.quote(f"{OTHER}:yes", 0.40, 0.41)
    assert w.run(ticker=OTHER, outcome="yes", contracts=1, side="buy")["status"] == "done"   # the rest goes on


def test_a_partial_disappearance_is_never_a_settlement(tmp_path):
    w = baseline_world(tmp_path, units=2)
    w.exchange.held[f"{T}:yes"] = 1
    w.markets[T] = TONIGHT
    out = w.run(reconcile_only=True)
    assert out["status"] == "stopped" and out["stage"] == "reconcile" and "person must look" in out["reason"]
    assert w.entries("settlement") == [] and w.state().settlements() == []


@pytest.mark.parametrize("record, words", [
    ({**TONIGHT, "status": "determined"}, "determined"), ({**TONIGHT, "settlement_value_dollars": "0.5000"}, "settlement value"),
    ({**TONIGHT, "result": "void"}, "result"), ({**TONIGHT, "ticker": "KXNBAGAME-26NOV13ATLCHI-CHI"}, "ticker"),
    ({**TONIGHT, "market_type": "scalar"}, "market type"), ({**TONIGHT, "settlement_ts": "2026-11-30T00:00:00Z"}, "past"),
    (None, "could not be read"), (kd.KalshiDataError("gave up"), "could not be read"), ("not a dict", "not an object")])
def test_a_record_that_is_not_exactly_settled_stops_the_run_and_says_which_fact(tmp_path, record, words):
    w = baseline_world(tmp_path)
    w.exchange.held[f"{T}:yes"] = 0
    if record is not None:
        w.markets[T] = record
    out = w.run(reconcile_only=True)
    assert out["status"] == "stopped" and out["stage"] == "reconcile" and "person must look" in out["reason"]
    assert words in out["reason"] and "settlement" in out["reason"], out["reason"]
    assert w.entries("settlement") == [] and w.state().settlements() == []
    assert not w.ledger.last_reconcile()["clean"]


def test_another_name_that_differs_stops_and_nothing_is_settled(tmp_path):
    w = World(tmp_path, held={f"{T}:yes": 1, "KXB-1:no": 2})
    assert w.run(reconcile_only=True)["status"] == "done"
    w.clock.t = NOW
    w.exchange.held[f"{T}:yes"] = 0
    w.exchange.held["KXB-1:no"] = 3
    w.markets[T] = TONIGHT
    out = w.run(reconcile_only=True)
    assert out["status"] == "stopped" and out["stage"] == "reconcile" and w.entries("settlement") == []


def test_an_order_of_the_ledger_still_open_on_the_name_defers_the_settlement(tmp_path):
    w = baseline_world(tmp_path)
    w.clock.t = ny("2026-11-13 14:30")
    assert buy(w, limit="0.50")["status"] == "done"                                # rests on T:yes
    w.clock.t = NOW
    w.exchange.held[f"{T}:yes"] = 0
    w.markets[T] = TONIGHT
    w.exchange.cancel_mode = "ignore"                                             # the cancel does not take
    out = w.run(reconcile_only=True)
    assert out["status"] == "stopped" and "open" in out["reason"] and w.entries("settlement") == []
    assert w.state().open_pending() == []                                         # not a cash question: S1 is not met
    w.exchange.cancel_mode = "ok"
    out = w.run(reconcile_only=True)                                              # cancelled, then settled in one run
    assert out["status"] == "done" and len(w.entries("settlement")) == 1, out
    assert [c["answer"] for c in out["cancels"]] == ["cancel requested"]


def test_a_production_venue_never_reaches_the_settlement_code(tmp_path):
    w = World(tmp_path, env="production", held={f"{T}:yes": 0})
    w.markets[T] = TONIGHT
    out = w.run(reconcile_only=True)
    assert out["status"] == "stopped" and out["stage"] == "environment"
    assert w.market_reads == [] and w.exchange.calls == [] and not w.state_path.exists() and w.entries() == []


def test_a_crash_between_the_journal_and_the_state_journals_the_settlement_once(tmp_path, monkeypatch):
    w = baseline_world(tmp_path)
    w.exchange.held[f"{T}:yes"] = 0
    w.markets[T] = TONIGHT
    real = ep.PracticeState.mark_settlement_journaled
    monkeypatch.setattr(ep.PracticeState, "mark_settlement_journaled", lambda self, i: (_ for _ in ()).throw(OSError("disk")))
    out = w.run(reconcile_only=True)
    assert out["status"] == "stopped" and len(w.entries("settlement")) == 1
    monkeypatch.setattr(ep.PracticeState, "mark_settlement_journaled", real)
    out = w.run(reconcile_only=True)
    assert out["status"] == "done" and len(w.entries("settlement")) == 1, out
    assert w.state().settlements()[0]["journaled"] == 1


def test_a_crash_before_the_journal_write_journals_the_settlement_on_the_next_run(tmp_path, monkeypatch):
    w = baseline_world(tmp_path)
    w.exchange.held[f"{T}:yes"] = 0
    w.markets[T] = TONIGHT
    real = Journal.write

    def failing(self, day, entry):
        if entry.get("event") == "settlement":
            raise OSError("disk")
        return real(self, day, entry)
    monkeypatch.setattr(Journal, "write", failing)
    out = w.run(reconcile_only=True)
    assert out["status"] == "stopped" and w.entries("settlement") == [] and len(w.state().settlements()) == 1
    monkeypatch.setattr(Journal, "write", real)
    w.markets.pop(T)                                                              # the row on file is enough: no new read
    out = w.run(reconcile_only=True)
    assert out["status"] == "done" and len(w.entries("settlement")) == 1, out
    assert w.entries("settlement")[0]["settlement_id"] == w.state().settlements()[0]["id"]


def bought_world(tmp_path, outcome="yes"):
    """One contract of T bought by this program at 0.71 (fee 0.01) and reconciled; a clean reading taken with it."""
    w = World(tmp_path)
    w.quote(f"{T}:{outcome}", 0.70, 0.71) if outcome == "yes" else w.quote(f"{T}:no", 0.28, 0.29)
    assert buy(w, outcome=outcome, limit=None if outcome == "yes" else "0.30")["status"] == "done"
    w.clock.t += 10
    assert w.run(reconcile_only=True)["status"] == "done"
    assert len(w.entries("fill")) == 1
    w.clock.t = NOW
    return w


def vanish(w, records, bp):
    """The holdings in `records` gone from the account, Kalshi's record of each, and the buying power now."""
    for name, rec in records.items():
        w.exchange.held[name] = 0
        w.markets[name.split(":")[0]] = rec
    w.exchange.bp = bp
    return w.run(reconcile_only=True)


def settled(out, w, n=1):
    return out["status"] == "done" and len(w.entries("settlement")) == n and w.state().open_pending() == []


def pending(out, w):
    return (out["status"] == "stopped" and out["stage"] == "settlement" and w.entries("settlement") == []
            and len(out["pending_open"]) >= 1 and "does not confirm the holding was still there" in out["reason"])


# ---- R1: two witnesses, Kalshi's record and the account's cash, exactly in cents

def test_the_17_november_case_with_no_earlier_reading_is_pending_until_a_person_confirms(tmp_path):
    w = World(tmp_path, held={})                                         # the holding is already gone
    layout_1_file(w.state_path, sends=False)                             # the real file: layout 1, a baseline, no reading
    w.clock.t = NOW
    w.markets[T] = TONIGHT
    out = w.run(reconcile_only=True)
    assert pending(out, w), out
    assert "no earlier clean reading" in out["reason"] and "--confirm-settlement" in out["reason"]
    assert "payouts expected 0 cents" in out["reason"]
    p = w.entries("settlement_pending")
    assert len(p) == 1 and p[0]["symbol"] == f"{T}:yes" and p[0]["kalshi"] == TONIGHT and p[0]["price"] == 0.0
    pid = p[0]["pending_id"]
    w.quote(f"{OTHER}:yes", 0.40, 0.41)
    assert w.run(ticker=OTHER, outcome="yes", contracts=1, side="buy")["status"] == "stopped" and w.exchange.placed() == []
    assert pending(w.run(reconcile_only=True), w) and len(w.entries("settlement_pending")) == 1   # waits; not re-made
    got = confirm(w, pid)
    assert got["status"] == "confirmed", got
    e = w.entries("settlement")
    assert len(e) == 1 and e[0]["witnesses"] == "kalshi_record+person" and e[0]["confirmed_by"] == "Burton"
    assert e[0]["note"] == "checked the app: the game was lost" and e[0]["pending_id"] == pid and e[0]["price"] == 0.0
    out = w.run(reconcile_only=True)                                     # the fresh starting point
    assert out["status"] == "done" and out["pending_open"] == [] and w.entries("buying_power")[-1]["clean"] is True
    assert len(w.entries("settlement")) == 1 and w.run(reconcile_only=True)["status"] == "done"
    assert w.run(ticker=OTHER, outcome="yes", contracts=1, side="buy")["status"] == "done"


def confirm(w, pid, who="Burton", note="checked the app: the game was lost", **kw):
    return ep.confirm_settlement(journal=w.journal, state=w.state_path, ledger=w.ledger, venue=w.venue,
                                 pending_id=pid, who=who, note=note, market=kw.pop("market", w.market), clock=w.clock, **kw)


def withdraw(w, pid, who="Burton", note="the contract is back in the app", **kw):
    return ep.withdraw_pending(journal=w.journal, state=w.state_path, ledger=w.ledger, venue=w.venue,
                               pending_id=pid, who=who, note=note, clock=w.clock, **kw)


def person_state(w):
    """What a refused person's command must leave as it was: the journal and the practice state."""
    st = w.state()
    try:
        return (len(w.entries()), [(r["id"], r["settlement_id"]) for r in st.pending()],
                [r["id"] for r in st.open_pending()], len(st.settlements()), len(st.withdrawals()), len(st.readings()))
    finally:
        st.close()


def positions_fail(w):
    def boom(a):
        raise ServerException("INTERNAL", "boom", 500)
    w.exchange.account_v2.get_account_position = boom


def positions_back(w):
    w.exchange.account_v2.get_account_position = lambda a: w.exchange._log(("positions",), w.exchange._rows())


# ---- a person's command checks what the account shows NOW, by the same reconcile, before it writes anything

@pytest.mark.parametrize("change", ["holding back", "another name", "unread", "host", "order open", "unjournaled"])
def test_a_confirmation_is_refused_unless_the_account_shows_exactly_the_pending_name_gone(tmp_path, change):
    if change == "order open":
        w = baseline_world(tmp_path)                                      # a resting order the venue will not cancel
        w.quote(f"{OTHER}:yes", 0.40, 0.41)
        assert w.run(ticker=OTHER, outcome="yes", contracts=1, side="buy", limit="0.30")["status"] == "done"
        w.exchange.cancel_mode = "ignore"
        out = vanish(w, {f"{T}:yes": TONIGHT}, "1000")
        assert pending(out, w), out
        pid = w.entries("settlement_pending")[0]["pending_id"]
    else:
        w, pid = pending_world(tmp_path)
    before = person_state(w)
    if change == "holding back":
        w.exchange.held[f"{T}:yes"] = 1
    elif change == "another name":
        w.exchange.held[f"{OTHER}:yes"] = 1
    elif change == "unread":
        positions_fail(w)
    elif change == "host":
        host, w.venue.host = w.venue.host, "api.webull.com"
    elif change == "unjournaled":
        import sqlite3
        db = sqlite3.connect(w.state_path, isolation_level=None)
        db.execute("UPDATE readings SET journaled = 0")
        db.close()
        before = person_state(w)
    calls = len(w.exchange.calls)
    out = confirm(w, pid)
    assert out["status"] == "refused" and out["reason"], (change, out)
    assert person_state(w) == before, change                              # nothing journaled, no state changed
    if change == "unread":
        assert "positions could not be read" in out["reason"], out
    if change == "host":
        assert out["stage"] == "environment" and len(w.exchange.calls) == calls     # checked before anything is read
    # the other way: the account shows exactly the pending name gone, and the confirmation goes through
    if change == "holding back":
        w.exchange.held[f"{T}:yes"] = 0
    elif change == "another name":
        w.exchange.held[f"{OTHER}:yes"] = 0
    elif change == "unread":
        positions_back(w)
    elif change == "host":
        w.venue.host = host
    elif change == "order open":
        w.exchange.cancel_mode = "ok"
        assert pending(w.run(reconcile_only=True), w)                    # the reconcile run cancels it, then stops
    elif change == "unjournaled":
        assert pending(w.run(reconcile_only=True), w)                    # the reconcile run makes the line good
    got = confirm(w, pid)
    assert got["status"] == "confirmed", (change, got)
    assert len(w.entries("settlement")) == 1 and w.entries("settlement")[0]["witnesses"] == "kalshi_record+person"


def test_holdings_gone_together_are_confirmed_one_by_one(tmp_path):
    # two names pending together: each other difference is a pending name the account shows at exactly 0
    w = World(tmp_path, held={f"{T}:yes": 1, f"{OTHER}:no": 2})
    assert w.run(reconcile_only=True)["status"] == "done"
    w.clock.t = NOW
    out = vanish(w, {f"{T}:yes": TONIGHT, f"{OTHER}:no": {**TONIGHT, "ticker": OTHER}}, "1000.50")
    assert pending(out, w), out
    pids = sorted(e["pending_id"] for e in w.entries("settlement_pending"))
    assert len(pids) == 2
    w.exchange.held[f"{OTHER}:no"] = 1                                    # the other one partly back: a person looks
    before = person_state(w)
    assert confirm(w, pids[0])["status"] == "refused" and person_state(w) == before
    w.exchange.held[f"{OTHER}:no"] = 0
    assert confirm(w, pids[0])["status"] == "confirmed"
    assert confirm(w, pids[1])["status"] == "confirmed"
    assert len(w.entries("settlement")) == 2 and w.run(reconcile_only=True)["status"] == "done"


def test_a_pending_record_is_withdrawn_only_when_the_holding_is_back_in_full_and_the_reconcile_clean(tmp_path):
    w, pid = pending_world(tmp_path)
    before = person_state(w)
    for kw in (dict(who=""), dict(note=" "), dict(who="a\nb"), dict(pid=pid + 1), dict(pid=True), dict(pid=0)):
        args = {"pid": pid, **kw}
        assert withdraw(w, **args)["status"] == "refused", kw
        assert person_state(w) == before, kw
    out = withdraw(w, pid)                                                # the holding is still gone
    assert out["status"] == "refused" and person_state(w) == before, out
    w.exchange.held[f"{T}:yes"] = 1
    w.exchange.held[f"{OTHER}:yes"] = 1                                   # back, but something else differs
    assert withdraw(w, pid)["status"] == "refused" and person_state(w) == before
    w.exchange.held[f"{OTHER}:yes"] = 0
    positions_fail(w)
    assert withdraw(w, pid)["status"] == "refused" and person_state(w) == before
    positions_back(w)
    host, w.venue.host = w.venue.host, "api.webull.com"
    calls = len(w.exchange.calls)
    out = withdraw(w, pid)
    assert out["status"] == "refused" and out["stage"] == "environment" and len(w.exchange.calls) == calls
    w.venue.host = host
    got = withdraw(w, pid)                                                # back in full, clean: withdrawn
    assert got["status"] == "withdrawn", got
    e = w.entries("settlement_withdrawn")
    assert len(e) == 1 and e[0]["pending_id"] == pid and e[0]["who"] == "Burton" and e[0]["symbol"] == f"{T}:yes"
    assert e[0]["note"] == "the contract is back in the app" and w.entries("settlement") == []
    assert w.state().open_pending() == [] and w.state().settlements() == []
    assert withdraw(w, pid)["status"] == "refused" and confirm(w, pid)["status"] == "refused"   # closed: once
    assert len(w.entries("settlement_withdrawn")) == 1 and w.entries("settlement") == []
    assert w.run(reconcile_only=True)["status"] == "done"
    w.quote(f"{OTHER}:yes", 0.40, 0.41)
    assert w.run(ticker=OTHER, outcome="yes", contracts=1, side="buy")["status"] == "done"    # sends again
    w.exchange.held[f"{T}:yes"] = 0                                       # gone again later: a new pending record
    w.exchange.bp = "999.99"
    assert pending(w.run(reconcile_only=True), w)
    assert len(w.entries("settlement_pending")) == 2 and len(w.state().open_pending()) == 1


def test_a_confirmation_keeps_the_time_guard_on_the_programs_submissions(tmp_path):
    w = bought_world(tmp_path)                                            # sent at 14:00 New York on 13 November
    out = vanish(w, {f"{T}:yes": TONIGHT}, "1000.37")                     # the cash does not confirm it: pending
    assert pending(out, w), out
    pid = w.entries("settlement_pending")[0]["pending_id"]
    w.markets[T] = {**TONIGHT, "settlement_ts": "2026-11-13T18:00:00Z"}  # before that submission
    before = person_state(w)
    out = confirm(w, pid)
    assert out["status"] == "refused" and "submitted" in out["reason"] and person_state(w) == before, out
    w.markets[T] = TONIGHT
    assert confirm(w, pid)["status"] == "confirmed"


def test_a_person_command_without_a_state_file_is_refused_and_makes_none(tmp_path):
    w = World(tmp_path, held={})
    for f in (confirm, withdraw):
        out = f(w, 1)
        assert out["status"] == "refused" and out["stage"] == "state", out
        assert not w.state_path.exists() and w.entries() == []
    w, pid = pending_world(tmp_path / "with")                             # the other way: with the file, it is read
    assert confirm(w, pid)["status"] == "confirmed"


def test_a_confirmation_while_the_holding_is_back_is_refused_and_a_withdrawal_closes_it(tmp_path):
    w, pid = pending_world(tmp_path)
    w.exchange.held[f"{T}:yes"] = 1
    before = person_state(w)
    out = confirm(w, pid)
    assert out["status"] == "refused" and "shows" in out["reason"] and person_state(w) == before, out
    assert withdraw(w, pid)["status"] == "withdrawn"


def test_a_failed_withdrawal_line_is_made_good_once_at_the_next_run(tmp_path, monkeypatch):
    w, pid = pending_world(tmp_path)
    w.exchange.held[f"{T}:yes"] = 1
    real = Journal.write
    failed = []

    def failing(self, day, entry):
        if entry.get("event") == "settlement_withdrawn" and not failed:
            failed.append(entry)
            raise OSError("disk")
        return real(self, day, entry)
    monkeypatch.setattr(Journal, "write", failing)
    got = withdraw(w, pid)
    assert got["status"] == "withdrawn" and got["journal_error"] and failed
    monkeypatch.setattr(Journal, "write", real)
    w.run(reconcile_only=True)
    w.run(reconcile_only=True)
    lines = w.entries("settlement_withdrawn")
    assert len(lines) == 1 and lines[0]["withdrawal_id"] == failed[0]["withdrawal_id"] and lines[0]["pending_id"] == pid


def test_the_command_withdraws_a_pending_record(tmp_path, capsys):
    w, pid = pending_world(tmp_path)

    def build(env):
        return wb.WebullVenue(KEY, SECRET, client=w.exchange, account_class=env["WEBULL_ACCOUNT_CLASS"], limits=HIGH,
                              sleep=lambda s: None, clock=lambda: 0.0, confirmation_file=tmp_path / "x.json")
    w.ledger.close()
    args = ["--withdraw-pending", str(pid), "--who", "Burton", "--note", "back in the app"]
    assert ep.main(args, environ={}, build_venue=build, root=tmp_path, clock=w.clock, market=w.market) == 2   # still gone
    w.exchange.held[f"{T}:yes"] = 1
    assert ep.main(args, environ={}, build_venue=build, root=tmp_path, clock=w.clock, market=w.market) == 0
    capsys.readouterr()
    assert len(w.entries("settlement_withdrawn")) == 1 and w.entries("buying_power")[-1]["clean"] is True
    for extra in (["--confirm-settlement", "1"], ["--reconcile-only"], ["--ticker", T]):
        with pytest.raises(SystemExit):
            ep.main(args + extra, environ={}, build_venue=build, root=tmp_path, clock=w.clock)
    with pytest.raises(SystemExit):
        ep.main(["--reconcile-only", "--who", "B"], environ={}, build_venue=build, root=tmp_path, clock=w.clock)


def pending_world(tmp_path):
    w = World(tmp_path, held={})
    layout_1_file(w.state_path, sends=False)
    w.clock.t = NOW
    w.markets[T] = TONIGHT
    assert pending(w.run(reconcile_only=True), w)
    return w, w.entries("settlement_pending")[0]["pending_id"]


def test_nothing_is_sent_while_a_record_is_pending_even_when_the_holding_shows_again(tmp_path):
    # Second line: while the name is still gone the run stops at `settlement` before any send. If the account shows
    # the holding again the reconcile is clean, and only the pending gate in the send step still holds the order back.
    w, pid = pending_world(tmp_path)
    w.exchange.held[f"{T}:yes"] = 1
    out = w.run(reconcile_only=True)
    assert out["status"] == "done" and [r["id"] for r in w.state().open_pending()] == [pid], out
    w.quote(f"{OTHER}:yes", 0.40, 0.41)
    out = w.run(ticker=OTHER, outcome="yes", contracts=1, side="buy")
    assert out["status"] == "stopped" and out["stage"] == "pending" and str(pid) in out["reason"], out
    assert w.exchange.placed() == [] and w.entries("settlement") == []


def test_a_confirmation_is_refused_unless_every_input_and_kalshis_record_hold_now(tmp_path):
    w, pid = pending_world(tmp_path)
    before = (len(w.entries()), [r["id"] for r in w.state().open_pending()])
    for kw in (dict(who=""), dict(who="  "), dict(note=""), dict(note=" \t"), dict(who=None), dict(note=None),
               dict(who="Bur\x00ton"), dict(note="a\nb"), dict(note="a​b"), dict(who=5), dict(pid=pid + 1),
               dict(pid=True), dict(pid="1"), dict(pid=0)):
        args = {"pid": pid, **kw}
        out = confirm(w, **args)
        assert out["status"] == "refused", kw
        assert (len(w.entries()), [r["id"] for r in w.state().open_pending()]) == before, kw
    for rec in ({**TONIGHT, "status": "determined"}, {**TONIGHT, "settlement_value_dollars": "0.5000"}, None):
        w.markets[T] = rec
        if rec is None:
            w.markets.pop(T)
        assert confirm(w, pid)["status"] == "refused" and len(w.entries()) == before[0]
    w.markets[T] = TONIGHT
    assert confirm(w, pid)["status"] == "confirmed"
    n = len(w.entries())
    assert confirm(w, pid)["status"] == "refused" and len(w.entries()) == n                # already confirmed


def test_the_command_confirms_a_pending_settlement_and_takes_a_fresh_reading(tmp_path, capsys):
    w, pid = pending_world(tmp_path)

    def build(env):
        return wb.WebullVenue(KEY, SECRET, client=w.exchange, account_class=env["WEBULL_ACCOUNT_CLASS"], limits=HIGH,
                              sleep=lambda s: None, clock=lambda: 0.0, confirmation_file=tmp_path / "x.json")
    w.ledger.close()
    args = ["--confirm-settlement", str(pid), "--who", "Burton"]
    assert ep.main(args, environ={}, build_venue=build, root=tmp_path, clock=w.clock, market=w.market) == 2
    assert ep.main(args + ["--note", "seen in the app"], environ={}, build_venue=build, root=tmp_path, clock=w.clock,
                   market=w.market) == 0
    capsys.readouterr()
    assert len(w.entries("settlement")) == 1 and w.entries("buying_power")[-1]["clean"] is True
    with pytest.raises(SystemExit):
        ep.main(args + ["--note", "n", "--reconcile-only"], environ={}, build_venue=build, root=tmp_path, clock=w.clock)
    with pytest.raises(SystemExit):
        ep.main(["--clear-hold", "1", "--who", "B", "--note", "n"], environ={}, build_venue=build, root=tmp_path,
                clock=w.clock)


@pytest.mark.parametrize("bp, ok", [("1000", True), ("1000.60", False), ("1000.01", False), ("999.99", False)])
def test_a_held_loss_settles_only_when_the_cash_is_unchanged_to_the_cent(tmp_path, bp, ok):
    w = baseline_world(tmp_path)                                          # a clean reading of 1000 with T held
    out = vanish(w, {f"{T}:yes": TONIGHT}, bp)
    assert (settled(out, w) if ok else pending(out, w)), out
    assert out["win_cash_observed"] is False                             # a loss never sets the flag
    if not ok:                                                            # e.g. sold by hand for 0.60: never a settlement
        assert "difference of" in out["reason"] and w.state().settlements() == []
        moved = int(round(float(bp) * 100)) - 100000
        for words in ("buying power 100000 cents at reading", f"{100000 + moved} cents at reading",
                      "the known cash between them 0 cents", "payouts expected 0 cents", f"a difference of {moved} cents"):
            assert words in out["reason"], (words, out["reason"])


@pytest.mark.parametrize("bp, ok", [("1002", True), ("1002.00", True), ("1001.99", False), ("1002.01", False),
                                    ("1000", False)])
def test_a_win_settles_on_exactly_one_dollar_a_contract_and_sets_the_flag(tmp_path, bp, ok):
    w = baseline_world(tmp_path, units=2)
    out = vanish(w, {f"{T}:yes": WIN}, bp)
    assert (settled(out, w) if ok else pending(out, w)), out
    assert out["win_cash_observed"] is ok
    if ok:
        e = w.entries("settlement")[0]
        assert (e["units"], e["price"], e["witnesses"]) == (-2.0, 1.0, "kalshi_record+cash_equation")


def test_a_program_bought_win_and_a_held_no_that_wins(tmp_path):
    w = bought_world(tmp_path)                                            # the last reading: after the fill
    assert settled(vanish(w, {f"{T}:yes": WIN}, "1001"), w)
    e = w.entries("settlement")[0]
    assert (e["program_units"], e["baseline_units"], e["price"]) == (1.0, 0.0, 1.0)
    w = bought_world(tmp_path / "no", outcome="no")
    assert settled(vanish(w, {f"{T}:no": TONIGHT}, "1001"), w) and w.entries("settlement")[0]["price"] == 1.0


@pytest.mark.parametrize("bp, ok", [("999.58", True), ("999.57", False), ("999.59", False)])
def test_a_purchase_between_the_readings_is_inside_the_equation(tmp_path, bp, ok):
    w = baseline_world(tmp_path)                                          # reading: 1000, T held
    w.quote(f"{OTHER}:yes", 0.40, 0.41)
    assert w.run(ticker=OTHER, outcome="yes", contracts=1, side="buy")["status"] == "done"
    fills = w.entries("fill")
    assert [(f["symbol"], f["price"], f["fee"]) for f in fills] == [(f"{OTHER}:yes", 0.41, 0.01)]   # 42 cents in all
    out = vanish(w, {f"{T}:yes": TONIGHT}, bp)
    assert (settled(out, w) if ok else pending(out, w)), out


@pytest.mark.parametrize("bp, ok", [("1001", True), ("1000", False), ("1002", False)])
def test_two_holdings_gone_together_settle_only_on_the_joint_figure(tmp_path, bp, ok):
    w = World(tmp_path, held={f"{T}:yes": 1, f"{OTHER}:yes": 1})
    assert w.run(reconcile_only=True)["status"] == "done"
    w.clock.t = NOW
    lose = {**TONIGHT, "ticker": OTHER}
    out = vanish(w, {f"{T}:yes": WIN, f"{OTHER}:yes": lose}, bp)
    if ok:
        assert settled(out, w, n=2), out
    else:
        assert pending(out, w) and w.state().settlements() == [] and len(out["pending_open"]) == 2, out


def test_an_open_order_when_the_reading_would_be_taken_means_pending(tmp_path):
    w = baseline_world(tmp_path)
    w.quote(f"{OTHER}:yes", 0.40, 0.41)
    assert w.run(ticker=OTHER, outcome="yes", contracts=1, side="buy", limit="0.10")["status"] == "done"   # rests
    w.exchange.cancel_mode = "ignore"
    out = vanish(w, {f"{T}:yes": TONIGHT}, "1000")
    assert pending(out, w) and "order of this ledger is open" in out["reason"], out


@pytest.mark.parametrize("answer", ["raise", float("nan"), -1.0, True, "1000", None, 1000.005, {"x": 1}, "dict-none"])
def test_a_reading_that_is_not_a_whole_cent_number_from_0_is_unread_and_settles_nothing(tmp_path, monkeypatch, answer):
    w = baseline_world(tmp_path)
    for name in (f"{T}:yes",):
        w.exchange.held[name] = 0
    w.markets[T] = TONIGHT

    def cash():
        if answer == "raise":
            raise RuntimeError("down")
        if answer == "dict-none":
            return {"buying_power": None}
        if isinstance(answer, dict):
            return answer
        return {"buying_power": answer}
    monkeypatch.setattr(w.venue, "cash", cash)
    out = w.run(reconcile_only=True)
    assert pending(out, w), out
    r = w.entries("buying_power")[-1]
    assert r["buying_power"] is None and r["cents"] is None and r["unread"], r


def test_a_negative_buying_power_on_an_ordinary_run_is_unread(tmp_path):
    w = World(tmp_path)
    w.exchange.bp = "-5"
    assert w.run(reconcile_only=True)["status"] == "done"
    r = w.entries("buying_power")[-1]
    assert r["buying_power"] is None and "below 0" in r["unread"] and r["clean"] is True


def test_a_settlement_between_the_two_readings_is_inside_the_known_cash(tmp_path):
    w = World(tmp_path, held={f"{T}:yes": 1, f"{OTHER}:yes": 1})
    assert w.run(reconcile_only=True)["status"] == "done"                # reading 0: 1000, known 0
    w.clock.t = NOW
    assert settled(vanish(w, {f"{OTHER}:yes": {**WIN, "ticker": OTHER}}, "1001"), w)
    w.exchange.bp = None
    assert w.run(reconcile_only=True)["status"] == "done"                # unread: never a prev
    out = vanish(w, {f"{T}:yes": WIN}, "1001")                            # 1001 would ignore OTHER's payout
    assert out["status"] == "stopped" and out["stage"] == "settlement" and len(w.entries("settlement")) == 1, out
    assert "a difference of -100 cents" in out["reason"] and len(out["pending_open"]) == 1
    w2 = World(tmp_path / "b", held={f"{T}:yes": 1, f"{OTHER}:yes": 1})
    assert w2.run(reconcile_only=True)["status"] == "done"
    w2.clock.t = NOW
    assert settled(vanish(w2, {f"{OTHER}:yes": {**WIN, "ticker": OTHER}}, "1001"), w2)
    w2.exchange.bp = None
    assert w2.run(reconcile_only=True)["status"] == "done"
    assert settled(vanish(w2, {f"{T}:yes": WIN}, "1002"), w2, n=2)


@pytest.mark.parametrize("withheld", ["fee", "price", None])
def test_a_fill_without_a_fee_or_a_price_makes_the_known_cash_unknown(tmp_path, withheld):
    w = World(tmp_path)
    w.exchange.fees_given, w.exchange.price_given = withheld != "fee", withheld != "price"
    w.quote(f"{T}:yes", 0.70, 0.71)
    assert buy(w)["status"] == "done"
    w.clock.t += 10
    assert w.run(reconcile_only=True)["status"] == "done"
    w.clock.t = NOW
    out = vanish(w, {f"{T}:yes": WIN}, "1001")
    if withheld:
        assert pending(out, w) and "known cash" in out["reason"], out
    else:                                                                 # the other way: both given, it settles
        assert settled(out, w), out


# ---- R2: the time guard uses the time the ledger submitted the order

def test_a_contract_that_fills_and_settles_between_two_runs_settles(tmp_path):
    w = World(tmp_path)
    assert buy(w)["status"] == "done"                                     # rests: no ask set; sent 19:00:00Z
    cid = w.exchange.placed()[-1]["client_order_id"]
    w.quote(f"{T}:yes", 0.70, 0.71)
    w.exchange._fill(cid)                                                 # fills at 0.71 at the broker ...
    w.exchange.held[f"{T}:yes"] = 0                                       # ... and settles, all before the next run
    w.clock.t = NOW
    w.markets[T] = {**TONIGHT, "settlement_ts": "2026-11-13T19:00:05Z"}   # after the submission, before the fill was read
    w.exchange.bp = "999.28"                                              # 1000 less 0.71 and the 0.01 fee
    out = w.run(reconcile_only=True)
    assert settled(out, w), out
    assert w.ledger.fills()[0]["time"] == NOW                             # the fill was recorded after the settlement


def test_a_settlement_time_not_later_than_the_orders_submission_stops(tmp_path):
    w = bought_world(tmp_path)                                            # sent at 2026-11-13 14:00 New York (19:00Z)
    out = vanish(w, {f"{T}:yes": {**WIN, "settlement_ts": "2026-11-13T18:59:59Z"}}, "1001")
    assert out["status"] == "stopped" and out["stage"] == "reconcile" and "submitted" in out["reason"]
    assert w.entries("settlement") == [] and w.state().open_pending() == []
    w.markets[T] = {**WIN, "settlement_ts": "2026-11-13T19:00:01Z"}
    assert settled(w.run(reconcile_only=True), w)


def test_the_time_guard_on_its_own(tmp_path):
    rec = {**TONIGHT, "settlement_ts": "2026-11-13T19:00:00Z"}
    sent = pd.Timestamp("2026-11-13T19:00:00Z").timestamp()
    with pytest.raises(ep.NotSettled, match="submitted"):
        ep.judge_settlement(rec, T, NOW, after=sent)
    assert ep.judge_settlement(rec, T, NOW, after=sent - 1) == Decimal("0.00")
    assert ep.judge_settlement(rec, T, NOW, after=None) == Decimal("0.00")           # a baseline holding: no order


# ---- R3: nothing the state knows stays unjournaled

@pytest.mark.parametrize("kind", ["buying_power", "settlement_pending", "settlement", "confirmed"])
def test_a_failed_journal_write_is_made_good_once_at_the_next_run(tmp_path, monkeypatch, kind):
    if kind in ("settlement_pending", "confirmed"):
        w = World(tmp_path, held={})
        layout_1_file(w.state_path, sends=False)
        w.clock.t = NOW
        w.markets[T] = TONIGHT
    else:
        w = baseline_world(tmp_path)
        w.exchange.held[f"{T}:yes"] = 0
        w.markets[T] = TONIGHT
    real = Journal.write
    failed = []

    def failing(self, day, entry):
        event = "confirmed" if entry.get("witnesses") == "kalshi_record+person" else entry.get("event")
        if event == kind and not failed:
            failed.append(entry)
            raise OSError("disk")
        return real(self, day, entry)
    if kind == "confirmed":
        assert pending(w.run(reconcile_only=True), w)
        pid = w.entries("settlement_pending")[0]["pending_id"]
        monkeypatch.setattr(Journal, "write", failing)
        got = confirm(w, pid)
        assert got["status"] == "confirmed" and got["journal_error"] and failed    # the row stands; its line failed
    else:
        monkeypatch.setattr(Journal, "write", failing)
        w.run(reconcile_only=True)
        assert failed
    monkeypatch.setattr(Journal, "write", real)
    w.run(reconcile_only=True)
    w.run(reconcile_only=True)
    key = {"buying_power": "reading_id", "settlement_pending": "pending_id", "settlement": "settlement_id",
           "confirmed": "settlement_id"}[kind]
    event = "settlement" if kind == "confirmed" else kind
    lines = [e for e in w.entries(event) if e.get(key) == failed[0][key]]
    assert len(lines) == 1, (kind, lines)
    if kind == "confirmed":
        assert lines[0]["witnesses"] == "kalshi_record+person" and lines[0]["confirmed_by"] == "Burton"


# ---- R4: the two inner guards, each on its own

def test_the_inner_guards_on_their_own():
    with pytest.raises(ep.NotSettled, match="not yet applied"):
        ep._settle_blockers(f"{T}:yes", unapplied={f"{T}:yes"}, busy=set())
    with pytest.raises(ep.NotSettled, match="open"):
        ep._settle_blockers(f"{T}:yes", unapplied=set(), busy={f"{T}:yes"})
    assert ep._settle_blockers(f"{T}:yes", unapplied={"KXB-1:no"}, busy={"KXB-1:no"}) is None
    led, venue = OrderLedger(), CashVenue()
    trade(led, venue, f"{T}:yes", 2, [(2, 50, 1)])
    trade(led, venue, f"{T}:yes", -1, [(1, 60, 1)])
    trade(led, venue, "KXB-1:no", 3, [(3, 20, 1)])
    fills = led.fills()
    assert ep._program_owned(f"{T}:yes", fills, []) == 1.0
    assert ep._program_owned(f"{T}:yes", fills, [{"symbol": f"{T}:yes", "program_units": 1.0}]) == 0.0
    assert ep._program_owned(f"{T}:yes", fills, [{"symbol": "KXB-1:no", "program_units": 3.0}]) == 1.0


class CashVenue:
    """A venue for the real OrderLedger that keeps its own cash in cents. A purchase pays price times contracts plus
    the fee; a sale brings price times contracts less the fee; a settlement pays 1.00 a contract on a win. It reports
    the filled amount, the average over everything filled and the fee total, as the ledger's protocol says."""
    name = "cash-events"

    def __init__(self):
        self.cash, self.held, self.orders = 100000, {}, {}
        self.give_price, self.give_fee = True, True

    def submit(self, symbol, units, tag, price=None, limit_price=None):
        self.orders[tag] = {"symbol": symbol, "units": units, "filled": 0, "value": 0, "fee": 0, "state": "open"}
        return {"status": "accepted", "venue_order_id": tag}

    def fill(self, tag, qty, price_cents, fee_cents):
        o = self.orders[tag]
        o["filled"] += qty
        o["value"] += price_cents * qty
        o["fee"] += fee_cents
        buy = o["units"] > 0
        self.cash += (-price_cents * qty if buy else price_cents * qty) - fee_cents
        self.held[o["symbol"]] = self.held.get(o["symbol"], 0) + (qty if buy else -qty)
        o["state"] = "filled" if o["filled"] == abs(o["units"]) else "partly_filled"

    def order_status(self, tag):
        o = self.orders.get(tag)
        if o is None:
            return None
        avg = o["value"] / o["filled"] / 100 if o["filled"] and self.give_price else None
        fee = o["fee"] / 100 if o["filled"] and self.give_fee else None
        return {"state": o["state"], "filled_units": float(o["filled"]), "avg_price": avg, "fee": fee,
                "venue_order_id": tag, "raw_status": o["state"]}

    def cancel(self, tag):
        o = self.orders[tag]
        if o["state"] in ("open", "partly_filled"):
            o["state"] = "cancelled"

    def positions(self):
        return {k: float(v) for k, v in self.held.items() if v}


def book_of_ledger(led, settlements):
    out = {}
    for f in led.fills():
        if f["applied"]:
            out[f["symbol"]] = out.get(f["symbol"], 0.0) + float(f["units"])
    for r in settlements:
        out[r["symbol"]] = out.get(r["symbol"], 0.0) - float(r["units"])
    return {k: v for k, v in out.items() if abs(v) > 1e-9}


def read(led, venue, settlements):
    """A reconcile as the program makes it, the fills applied, then the known cash."""
    led.reconcile(venue, book_of_ledger(led, settlements))
    led.mark_applied([f["id"] for f in led.unapplied_fills()])
    return ep.known_cents(led.fills(), settlements)


_TAGS = iter(range(10 ** 9))


def trade(led, venue, symbol, units, pieces, settlements=(), cancel_after=False, readings=None):
    """One order through the real ledger: intended, sent once, filled in `pieces` (contracts, price cents, fee cents),
    each piece read by a reconcile; cancelled after its pieces when asked; read once more at the end. Each reading is
    kept in `readings` with the venue's cash at that moment."""
    tag = f"t{next(_TAGS)}"
    read(led, venue, list(settlements))                                   # submit needs a recent clean reconcile
    led.intend(tag, symbol, float(units), 0.5, limit_price=0.99 if units > 0 else 0.01)
    assert led.submit_once(venue, tag)["state"] == "accepted"
    for qty, price, fee in pieces:
        venue.fill(tag, qty, price, fee)
        got = read(led, venue, list(settlements))
        if readings is not None:
            readings.append((got, venue.cash))
    if cancel_after:
        venue.cancel(tag)
    got = read(led, venue, list(settlements))
    if readings is not None:
        readings.append((got, venue.cash))
    return tag


def test_known_cash_follows_the_ledger_as_it_stores_a_purchase_and_a_sale():
    led, venue = OrderLedger(), CashVenue()
    trade(led, venue, f"{T}:yes", 1, [(1, 71, 1)])
    trade(led, venue, f"{T}:yes", -1, [(1, 70, 1)])
    sale = [f for f in led.fills() if f["units"] < 0]
    assert [(f["units"], f["price"], f["price_covers_units"], f["fee"]) for f in sale] == [(-1.0, 0.7, 1.0, 0.01)]
    assert ep.known_cents(led.fills(), []) == (-3, None) and venue.cash - 100000 == -3
    assert ep.known_cents([], [{"units": 2.0, "price": "1.00"}, {"units": 1.0, "price": "0.00"}]) == (200, None)


@pytest.mark.parametrize("withheld", ["price", "fee", "unapplied", None])
def test_known_cash_is_unknown_when_the_venue_withholds_a_piece(withheld):
    led, venue = OrderLedger(), CashVenue()
    venue.give_price, venue.give_fee = withheld != "price", withheld != "fee"
    trade(led, venue, f"{T}:yes", 2, [(1, 40, 1), (1, 42, 1)])
    fills = led.fills()
    if withheld == "unapplied":
        fills = [{**fills[0], "applied": 0}] + fills[1:]
    cents, why = ep.known_cents(fills, [])
    if withheld:
        assert cents is None and why, withheld
    else:
        assert (cents, why) == (-84, None) and venue.cash - 100000 == -84


def test_known_cash_takes_a_late_price_and_a_late_fee_as_the_ledger_writes_them():
    led, venue = OrderLedger(), CashVenue()
    venue.give_price = False
    tag = trade(led, venue, f"{T}:yes", 2, [(2, 33, 1)])
    assert ep.known_cents(led.fills(), [])[0] is None                     # not priced yet: unknown
    venue.give_price = True
    venue.orders[tag]["fee"] += 1                                          # the venue posts one more cent of fee
    venue.cash -= 1
    assert read(led, venue, []) == (venue.cash - 100000, None) == (-68, None)


@pytest.mark.parametrize("seed", range(200))
def test_known_cash_change_equals_the_venues_cash_change_at_every_reading(seed):
    # purchases, sales, partial fills (some then cancelled), late fees and settlements, all through the real ledger
    rng = random.Random(seed)
    led, venue, settlements = OrderLedger(), CashVenue(), []
    names = [f"KXP{i}-26NOV20-B{i}:{rng.choice(['yes', 'no'])}" for i in range(3)]
    start = venue.cash
    readings = [(read(led, venue, []), venue.cash)]
    for _ in range(rng.randint(3, 10)):
        held = book_of_ledger(led, settlements)
        gone = {r["symbol"] for r in settlements}
        live = [n for n in names if n not in gone]
        sellable = [n for n in live if held.get(n, 0.0) >= 1]
        kind = rng.choice(["buy", "buy", "sell", "settle", "fee"]) if sellable else "buy"
        if kind in ("buy", "sell") and live:
            name = rng.choice(live if kind == "buy" else sellable)
            size = rng.randint(1, 5) if kind == "buy" else rng.randint(1, int(held[name]))
            cut = rng.randint(1, size)
            pieces = [(q, rng.randint(1, 99), rng.randint(0, 3)) for q in _split(rng, cut)]
            trade(led, venue, name, size if kind == "buy" else -size, pieces, settlements, cancel_after=cut < size,
                  readings=readings)
            continue
        if kind == "settle":
            name = rng.choice(sellable)
            units, win = held[name], rng.random() < 0.5
            venue.held[name] = 0
            venue.cash += int(units) * 100 if win else 0
            settlements.append({"symbol": name, "units": units, "price": "1.00" if win else "0.00"})
        elif kind == "fee":                                               # a fee posted late on a filled order
            done = [t for t, o in venue.orders.items() if o["filled"]]
            if done:
                venue.orders[rng.choice(done)]["fee"] += 1
                venue.cash -= 1
        readings.append((read(led, venue, settlements), venue.cash))
    assert len(readings) >= 4
    for got, cash in readings:                                            # exactly, in cents, at every reading
        assert got == (cash - start, None), (seed, got, cash - start)


def _split(rng, n):
    out = []
    while n:
        q = rng.randint(1, n)
        out.append(q)
        n -= q
    return out


# ---- the state file's layout 4, migrated from layout 1 with the baseline and sends untouched

LAYOUT_1 = [                                                    # event_practice.STATE_SCHEMA at b446e50, verbatim
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS baseline (symbol TEXT PRIMARY KEY, units REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS sends (tag TEXT PRIMARY KEY, day TEXT NOT NULL, time REAL NOT NULL, outcome TEXT)",
]
LAYOUT_2_A640AA9 = [                                            # the layout 2 tables a640aa9 added (never deployed)
    "CREATE TABLE IF NOT EXISTS settlements (id INTEGER PRIMARY KEY, symbol TEXT NOT NULL UNIQUE, units REAL NOT NULL, "
    "baseline_units REAL NOT NULL, program_units REAL NOT NULL, price TEXT NOT NULL, kalshi TEXT NOT NULL, "
    "time REAL NOT NULL, batch TEXT, journaled INTEGER NOT NULL DEFAULT 0, cash_check TEXT)",
    "CREATE TABLE IF NOT EXISTS readings (id INTEGER PRIMARY KEY, time REAL NOT NULL, batch TEXT, buying_power REAL, "
    "unread TEXT, book TEXT NOT NULL, fills INTEGER NOT NULL, orders INTEGER NOT NULL, last_settlement INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS holds (id INTEGER PRIMARY KEY, time REAL NOT NULL, settlement_id INTEGER, "
    "reason TEXT NOT NULL, cleared_by TEXT, cleared_note TEXT, cleared_time REAL)",
]


def layout_1_file(path, sends=True, tables=LAYOUT_1):
    import sqlite3
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, isolation_level=None)
    for s in tables:
        db.execute(s)
    db.execute("PRAGMA user_version = 1")
    db.execute("INSERT INTO meta VALUES ('baseline_recorded', '1794600000.0')")
    db.execute("INSERT INTO baseline VALUES (?, ?)", (f"{T}:yes", 1.0))
    if sends:
        db.execute("INSERT INTO sends VALUES ('ep-1-buy', '2026-11-13', 1794600000.5, 'accepted')")
    db.close()


def dump(path, tables=("meta", "baseline", "sends")):
    import sqlite3
    db = sqlite3.connect(path)
    try:
        return {t: list(db.execute(f"SELECT * FROM {t} ORDER BY rowid")) for t in tables}, \
               [r[0] for r in db.execute("SELECT sql FROM sqlite_master WHERE name IN ('meta','baseline','sends') ORDER BY name")]
    finally:
        db.close()


def test_a_layout_1_state_file_migrates_to_layout_4_keeping_baseline_and_sends(tmp_path):
    import sqlite3
    p = tmp_path / "s.sqlite"
    layout_1_file(p)
    before = dump(p)
    st = ep.PracticeState(p)
    assert st.baseline() == {f"{T}:yes": 1.0} and st.settlements() == [] and st.open_pending() == []
    assert st.win_cash_observed() is False
    st.close()
    assert dump(p) == before
    db = sqlite3.connect(p)
    assert db.execute("PRAGMA user_version").fetchone()[0] == ep.STATE_LAYOUT == 4
    db.close()
    ep.PracticeState(p).close()                                                  # opens again as layout 4
    assert dump(p) == before


def test_a_layout_1_file_missing_a_table_is_refused_before_anything_changes(tmp_path):
    p = tmp_path / "s.sqlite"
    layout_1_file(p, sends=False, tables=LAYOUT_1[:2])                           # no sends table
    data = p.read_bytes()
    with pytest.raises(ep.PracticeError):
        ep.PracticeState(p)
    assert p.read_bytes() == data


def test_a_layout_2_file_of_a640aa9_with_a_hold_is_refused_unchanged(tmp_path):
    import sqlite3
    p = tmp_path / "s.sqlite"
    layout_1_file(p)
    db = sqlite3.connect(p, isolation_level=None)
    for s in LAYOUT_2_A640AA9:
        db.execute(s)
    db.execute("INSERT INTO holds (time, settlement_id, reason) VALUES (1.0, 1, 'differs')")
    db.execute("PRAGMA user_version = 2")
    db.close()
    data = p.read_bytes()
    with pytest.raises(ep.PracticeError):
        ep.PracticeState(p)
    assert p.read_bytes() == data


@pytest.mark.parametrize("version, extra", [(4, None), (1, "CREATE TABLE odd (x)"), (3, "CREATE TABLE odd (x)"), (0, None),
                                            (3, None), (5, None), (4, "CREATE TABLE odd (x)")])
def test_any_other_layout_is_refused(tmp_path, version, extra):
    import sqlite3
    p = tmp_path / "s.sqlite"
    layout_1_file(p)
    db = sqlite3.connect(p, isolation_level=None)
    db.execute(f"PRAGMA user_version = {version}")
    if extra:
        db.execute(extra)
    db.close()
    with pytest.raises(ep.PracticeError):
        ep.PracticeState(p)


LAYOUT_3_4C58A4F = [                                            # the layout 3 tables 4c58a4f added (never deployed)
    "CREATE TABLE IF NOT EXISTS settlements (id INTEGER PRIMARY KEY, symbol TEXT NOT NULL UNIQUE, units REAL NOT NULL, "
    "baseline_units REAL NOT NULL, program_units REAL NOT NULL, price TEXT NOT NULL, kalshi TEXT NOT NULL, "
    "time REAL NOT NULL, batch TEXT, witnesses TEXT NOT NULL, confirmed_by TEXT, note TEXT, pending_id INTEGER, "
    "reading_prev INTEGER, reading_now INTEGER, journaled INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS pending (id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, units REAL NOT NULL, "
    "baseline_units REAL NOT NULL, program_units REAL NOT NULL, price TEXT NOT NULL, kalshi TEXT NOT NULL, "
    "figures TEXT NOT NULL, reason TEXT NOT NULL, time REAL NOT NULL, batch TEXT, settlement_id INTEGER, "
    "journaled INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS readings (id INTEGER PRIMARY KEY, time REAL NOT NULL, batch TEXT, buying_power REAL, "
    "cents INTEGER, unread TEXT, book TEXT NOT NULL, clean INTEGER NOT NULL, known_cents INTEGER, known_why TEXT, "
    "journaled INTEGER NOT NULL DEFAULT 0)",
]


def test_a_layout_3_file_of_4c58a4f_with_an_open_pending_record_is_refused_unchanged(tmp_path):
    import sqlite3
    p = tmp_path / "s.sqlite"
    layout_1_file(p)
    db = sqlite3.connect(p, isolation_level=None)
    for s in LAYOUT_3_4C58A4F:
        db.execute(s)
    db.execute("INSERT INTO pending (symbol, units, baseline_units, program_units, price, kalshi, figures, reason, time) "
               "VALUES ('KXA-1:yes', 1, 1, 0, '0.00', '{}', '{}', 'r', 1.0)")
    db.execute("PRAGMA user_version = 3")
    db.close()
    data = p.read_bytes()
    with pytest.raises(ep.PracticeError):
        ep.PracticeState(p)
    assert p.read_bytes() == data


# ---- a sale through the real ledger, then a settlement: the equation still decides

@pytest.mark.parametrize("result, off", [("yes", 0), ("no", 0), ("yes", 1), ("no", 1)])
def test_after_a_sale_a_later_settlement_still_rests_on_the_equation(tmp_path, result, off):
    w = World(tmp_path)
    w.exchange.cash = 100000
    assert w.run(reconcile_only=True)["status"] == "done"                # nothing held; a reading of 1000.00
    w.quote(f"{T}:yes", 0.70, 0.71)
    assert buy(w)["status"] == "done"                                     # A at 0.71 and 0.01
    w.clock.t += 10
    w.quote(f"{OTHER}:yes", 0.40, 0.41)
    assert w.run(ticker=OTHER, outcome="yes", contracts=1, side="buy")["status"] == "done"   # B at 0.41 and 0.01
    w.clock.t += 10
    w.exchange.charged_extra = off                                        # the venue takes a cent more than it reports
    assert sell(w)["status"] == "done"                                    # A sold at 0.70 less 0.01, after the reading
    sale = [f for f in w.entries("fill") if f["units"] < 0]
    assert [(f["units"], f["price"], f["price_covers_units"], f["fee"]) for f in sale] == [(-1.0, 0.70, 1.0, 0.01)]
    w.clock.t = NOW
    w.exchange.held[f"{OTHER}:yes"] = 0
    w.markets[OTHER] = {**(WIN if result == "yes" else TONIGHT), "ticker": OTHER}
    w.exchange.cash += 100 if result == "yes" else 0
    out = w.run(reconcile_only=True)
    if off:
        assert pending(out, w) and "a difference of -1 cents" in out["reason"], out
    else:
        assert settled(out, w), out
        assert w.entries("settlement")[0]["witnesses"] == "kalshi_record+cash_equation"
        assert out["win_cash_observed"] is (result == "yes")


def test_a_confirmation_is_refused_when_a_late_fill_changed_the_expected_holding(tmp_path):
    w = World(tmp_path)
    w.quote(f"{T}:yes", 0.70, 0.71)
    assert buy(w, contracts=2, limit="0.50")["status"] == "done"          # rests
    cid = w.exchange.placed()[-1]["client_order_id"]
    w.exchange.orders[cid].update(status="CANCELLED", filled=1, price="0.50")   # one filled, the rest cancelled
    w.exchange.held[f"{T}:yes"] = 1
    w.clock.t += 10
    assert w.run(reconcile_only=True)["status"] == "done"
    w.clock.t = NOW
    assert pending(vanish(w, {f"{T}:yes": TONIGHT}, "1000"), w)
    p = w.state().open_pending()[0]
    assert p["units"] == 1.0
    w.exchange.orders[cid]["filled"] = 2                                  # a late fill posted on the finished order
    assert pending(w.run(reconcile_only=True), w)                         # applied; the record waits
    before = person_state(w)
    out = confirm(w, p["id"])
    assert out["status"] == "refused" and "expects 2" in out["reason"], out
    assert person_state(w) == before


def test_a_confirmation_is_refused_when_kalshi_now_gives_another_result(tmp_path):
    w, pid = pending_world(tmp_path)                                      # pending on "no": 0.00 a contract
    before = person_state(w)
    w.markets[T] = WIN
    out = confirm(w, pid)
    assert out["status"] == "refused" and "another result" in out["reason"] and person_state(w) == before, out
    w.markets[T] = TONIGHT
    assert confirm(w, pid)["status"] == "confirmed"


def test_a_win_settled_by_a_person_leaves_the_flag_false(tmp_path):
    w = baseline_world(tmp_path)
    assert pending(vanish(w, {f"{T}:yes": WIN}, "1000.50"), w)
    pid = w.entries("settlement_pending")[0]["pending_id"]
    assert confirm(w, pid)["status"] == "confirmed"
    assert w.state().win_cash_observed() is False and w.run(reconcile_only=True)["win_cash_observed"] is False
