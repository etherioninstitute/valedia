"""T19: the event-contract practice command (docs/specs/2026-11-13-event-orders-design.md, part 2).

The real adapter runs against a stand-in for the SDK's TradeClient that behaves like the paper Events account seen on
2026-11-13 (a purchase fills at the ask, a sale at the bid, on the next read; one fee entry from acceptance, none after
a cancel). Kalshi's book is a function the test passes. Everything is written in a temporary folder; nothing here
touches the network, the real secrets file or the repository's files."""
import json
import random
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from valedia_trader.execution_and_risk import event_practice as ep
from valedia_trader.execution_and_risk.orders import OrderLedger
from valedia_trader.execution_and_risk.order_step import ledger_path_for, open_ledger
from valedia_trader.research_loop.paper import Journal
from valedia_trader.venues import webull as wb

KEY, SECRET = "app-key-do-not-print", "app-secret-do-not-print"
HIGH = {kind: (10 ** 9, 1) for kind in wb.CALL_KINDS}
T = "KXNBAGAME-26NOV13ATLCHI-ATL"
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
        self.account_v2 = SimpleNamespace(get_account_list=lambda: self._log(("accounts",), ACCOUNTS),
                                          get_account_balance=lambda a: self._log(("balance",), {
                                              "total_asset_currency": "USD", "account_currency_assets": [
                                                  {"currency": "USD", "buying_power": "1000"}]}),
                                          get_account_position=lambda a: self._log(("positions",), self._rows()))
        self.order_v3 = SimpleNamespace(place_order=self._place, cancel_order=self._cancel, get_order_detail=self._detail,
                                        list_order_open=lambda a, key=None: self._log(("open",), {"data": [
                                            self._row(c) for c, o in self.orders.items() if o["status"] == "SUBMITTED"]}))

    def _log(self, what, body):
        self.calls.append(what)
        return Response(body)

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
        if self.orders.get(cid, {}).get("status") == "SUBMITTED":
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
        elif o["side"] == "SELL" and name in self.bids and limit <= self.bids[name]:
            o.update(status="FILLED", filled=q, price=str(self.bids[name]))
            self.held[name] = self.held.get(name, 0) - q

    def _row(self, cid):
        o = self.orders[cid]
        fees = [] if o["status"] == "CANCELLED" else [{"type": "EXCHANGE_FEE", "actual_value": "0.01", "receivable_value": "0.01"}]
        return {"client_order_id": cid, "order_id": "ORD-1", "symbol": o["symbol"], "side": o["side"], "status": o["status"],
                "fees": fees, "commission": {}, "order_type": "LIMIT", "instrument_type": "EVENT", "entrust_type": "QTY",
                "total_quantity": o["quantity"], "filled_quantity": str(o["filled"]), "filled_price": o["price"],
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
        self.books = {T: book_of(0.70, 0.71)}
        self.book_reads = []

    def book(self, ticker):
        self.book_reads.append(ticker)
        return self.books[ticker]

    def quote(self, name, bid, ask):
        self.exchange.bids[name], self.exchange.asks[name] = Decimal(str(bid)), Decimal(str(ask))

    def run(self, **kw):
        return ep.run_event_practice(ledger=self.ledger, venue=self.venue, journal=self.journal, state=self.state_path,
                                     book=kw.pop("book", self.book), clock=self.clock, **kw)

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
