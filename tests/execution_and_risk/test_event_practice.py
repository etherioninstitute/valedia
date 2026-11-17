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
        self.bp = "1000"                                     # the Events balance: buying power only (None: left out)
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
        entry = {"currency": "USD"} if self.bp is None else {"currency": "USD", "buying_power": self.bp}
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
    assert e["kalshi"] == TONIGHT
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


def test_an_order_of_the_ledger_open_on_the_name_defers_the_settlement_to_the_next_run(tmp_path):
    w = baseline_world(tmp_path)
    w.clock.t = ny("2026-11-13 14:30")
    assert buy(w, limit="0.50")["status"] == "done"                                # rests on T:yes
    w.clock.t = NOW
    w.exchange.held[f"{T}:yes"] = 0
    w.markets[T] = TONIGHT
    out = w.run(reconcile_only=True)                                              # cancels it, settles nothing yet
    assert out["status"] == "stopped" and "open" in out["reason"] and w.entries("settlement") == []
    assert [c["answer"] for c in out["cancels"]] == ["cancel requested"]
    out = w.run(reconcile_only=True)
    assert out["status"] == "done" and len(w.entries("settlement")) == 1, out


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
    """One contract of T bought by this program and reconciled (a buying-power reading taken with it shown)."""
    w = World(tmp_path)
    w.quote(f"{T}:{outcome}", 0.70, 0.71) if outcome == "yes" else w.quote(f"{T}:no", 0.28, 0.29)
    assert buy(w, outcome=outcome, limit=None if outcome == "yes" else "0.30")["status"] == "done"
    w.clock.t += 10
    assert w.run(reconcile_only=True)["status"] == "done"
    assert len(w.entries("fill")) == 1
    w.clock.t = NOW
    return w


def test_a_win_whose_buying_power_rose_by_exactly_the_payout_goes_on_and_sets_the_flag(tmp_path):
    w = bought_world(tmp_path)
    assert w.entries("buying_power")[-1]["buying_power"] == 1000.0
    w.exchange.held[f"{T}:yes"] = 0
    w.markets[T] = WIN
    w.exchange.bp = "1001.00"
    out = w.run(reconcile_only=True)
    assert out["status"] == "done" and out["win_cash_observed"] is True and out["holds_open"] == [], out
    e = w.entries("settlement")[0]
    assert (e["units"], e["price"], e["program_units"], e["baseline_units"]) == (-1.0, 1.0, 1.0, 0.0)
    assert w.entries("win_cash")[-1]["result"] == "matched" and w.state().win_cash_observed() is True
    assert w.run(reconcile_only=True)["win_cash_observed"] is True


def test_a_held_no_with_result_no_is_a_win_at_one_dollar(tmp_path):
    w = bought_world(tmp_path, outcome="no")
    w.exchange.held[f"{T}:no"] = 0
    w.markets[T] = TONIGHT
    w.exchange.bp = "1001"
    out = w.run(reconcile_only=True)
    assert out["status"] == "done" and w.entries("settlement")[0]["price"] == 1.0 and out["win_cash_observed"] is True


@pytest.mark.parametrize("bp", ["1001.01", "1000.99", "1000", "1002"])
def test_a_win_whose_buying_power_is_off_by_a_cent_or_more_holds(tmp_path, bp):
    w = bought_world(tmp_path)
    w.exchange.held[f"{T}:yes"] = 0
    w.markets[T] = WIN
    w.exchange.bp = bp
    out = w.run(reconcile_only=True)
    assert out["status"] == "done" and len(out["holds_open"]) == 1 and out["win_cash_observed"] is False, out
    hold = w.entries("hold")[-1]
    assert "did not show the expected cash" in hold["reason"] and "person must look" in hold["reason"]
    w.quote(f"{OTHER}:yes", 0.40, 0.41)
    stopped = w.run(ticker=OTHER, outcome="yes", contracts=1, side="buy")
    assert stopped["status"] == "stopped" and stopped["stage"] == "hold" and len(w.exchange.placed()) == 1
    assert w.run(reconcile_only=True)["status"] == "done"                        # reconciling still goes on


@pytest.mark.parametrize("between", ["fill", "unread-before", "unread-now", "raise-now", "order"])
def test_a_first_win_that_cannot_be_compared_is_journaled_and_does_not_hold(tmp_path, between):
    w = World(tmp_path)
    w.quote(f"{T}:yes", 0.70, 0.71)
    w.quote(f"{OTHER}:yes", 0.40, 0.41)
    assert buy(w)["status"] == "done"
    w.clock.t += 10
    if between == "unread-before":
        w.exchange.bp = None
    assert w.run(reconcile_only=True)["status"] == "done"
    w.exchange.bp = "1000"
    if between == "fill":                                    # fills in the run's last reconcile, after its reading
        assert w.run(ticker=OTHER, outcome="yes", contracts=1, side="buy")["status"] == "done"
        assert len(w.entries("fill")) == 2
    if between == "order":
        assert w.run(ticker=OTHER, outcome="yes", contracts=1, side="buy", limit="0.10")["status"] == "done"
    w.clock.t = NOW
    w.exchange.held[f"{T}:yes"] = 0
    w.markets[T] = WIN
    w.exchange.bp = {"unread-now": None, "raise-now": "raise"}.get(between, "1001")
    out = w.run(reconcile_only=True)
    assert out["status"] == "done" and out["holds_open"] == [] and out["win_cash_observed"] is False, out
    assert len(w.entries("settlement")) == 1
    note = w.entries("win_cash")[-1]
    assert note["result"] == "not comparable" and note["reason"].startswith("the win's cash effect could not be compared: ")
    words = {"fill": "a fill was recorded", "order": "an order was handed", "unread-before": "shown is unread",
             "unread-now": "this run's reading is unread", "raise-now": "this run's reading is unread"}[between]
    assert words in note["reason"], note
    if between.startswith(("unread", "raise")):
        assert any(r["buying_power"] is None and r["unread"] for r in w.entries("buying_power"))


def test_a_settlement_time_not_later_than_a_fill_of_the_program_is_not_a_settlement(tmp_path):
    w = bought_world(tmp_path)                                   # the fill's time: 2026-11-13 14:00 in New York
    w.exchange.held[f"{T}:yes"] = 0
    w.markets[T] = {**WIN, "settlement_ts": "2026-11-13T18:59:59Z"}
    out = w.run(reconcile_only=True)
    assert out["status"] == "stopped" and "not later than every fill" in out["reason"] and w.entries("settlement") == []
    w.markets[T] = {**WIN, "settlement_ts": "2026-11-13T19:00:11Z"}       # after the fill (read at 14:00:10)
    w.exchange.bp = "1001"
    assert w.run(reconcile_only=True)["status"] == "done" and len(w.entries("settlement")) == 1


def test_a_loss_needs_no_cash_comparison(tmp_path):
    w = bought_world(tmp_path)
    w.exchange.held[f"{T}:yes"] = 0
    w.markets[T] = TONIGHT
    w.exchange.bp = "999"
    out = w.run(reconcile_only=True)
    assert out["status"] == "done" and out["holds_open"] == [] and w.entries("win_cash") == []


def test_buying_power_is_journaled_at_every_run_only_with_no_order_open(tmp_path):
    w = World(tmp_path)
    w.quote(f"{T}:yes", 0.40, 0.71)
    assert buy(w, limit="0.50")["status"] == "done"                              # rests after the reading
    readings = w.entries("buying_power")
    assert len(readings) == 1 and readings[0]["buying_power"] == 1000.0 and readings[0]["unread"] is None
    w.exchange.bp = "abc"
    assert w.run(reconcile_only=True)["status"] == "done"                        # cancels it, then reads
    last = w.entries("buying_power")[-1]
    assert last["buying_power"] is None and last["unread"] and len(w.entries("buying_power")) == 2


def hold_world(tmp_path):
    w = bought_world(tmp_path)
    w.exchange.held[f"{T}:yes"] = 0
    w.markets[T] = WIN
    w.exchange.bp = "1000"
    out = w.run(reconcile_only=True)
    assert len(out["holds_open"]) == 1
    return w, out["holds_open"][0]


def clear(w, hold_id, who="Burton", note="checked the app: payout arrived late"):
    return ep.clear_hold(journal=w.journal, state=w.state_path, venue_name=w.venue.name, hold_id=hold_id, who=who, note=note,
                         clock=w.clock)


def test_a_hold_is_cleared_only_by_a_person_with_who_and_a_note(tmp_path):
    w, hid = hold_world(tmp_path)
    before = (len(w.entries()), w.state().open_holds())
    for kw in (dict(who=""), dict(who="   "), dict(note=""), dict(note=" \t "), dict(who=None), dict(note=None),
               dict(who="Bur\x00ton"), dict(note="a\nb"), dict(note="a​b"), dict(who=5), dict(hold_id=hid + 1),
               dict(hold_id=True), dict(hold_id="1"), dict(hold_id=0)):
        args = {"hold_id": hid, **kw}
        out = clear(w, **args)
        assert out["status"] == "refused", kw
        assert (len(w.entries()), w.state().open_holds()) == before, kw
    out = clear(w, hid)
    assert out["status"] == "cleared" and w.state().open_holds() == []
    e = w.entries("hold_cleared")[-1]
    assert (e["hold_id"], e["who"], e["note"]) == (hid, "Burton", "checked the app: payout arrived late") and e["time"] == w.clock.t
    n = len(w.entries())
    assert clear(w, hid)["status"] == "refused" and len(w.entries()) == n             # already cleared
    w.quote(f"{OTHER}:yes", 0.40, 0.41)
    assert w.run(ticker=OTHER, outcome="yes", contracts=1, side="buy")["status"] == "done"


def test_the_command_clears_a_hold_and_refuses_a_bad_clearing(tmp_path, capsys):
    w, hid = hold_world(tmp_path)

    def build(env):
        return wb.WebullVenue(KEY, SECRET, client=w.exchange, account_class=env["WEBULL_ACCOUNT_CLASS"], limits=HIGH,
                              sleep=lambda s: None, clock=lambda: 0.0, confirmation_file=tmp_path / "x.json")
    calls = len(w.exchange.calls)
    assert ep.main(["--clear-hold", str(hid), "--who", "Burton"], environ={}, build_venue=build, root=tmp_path,
                   clock=w.clock) == 2
    assert w.state().open_holds() != []
    assert ep.main(["--clear-hold", str(hid), "--who", "Burton", "--note", "seen in the app"], environ={}, build_venue=build,
                   root=tmp_path, clock=w.clock) == 0
    assert w.state().open_holds() == [] and len(w.exchange.calls) == calls        # nothing asked of Webull
    with pytest.raises(SystemExit):
        ep.main(["--clear-hold", str(hid), "--who", "B", "--note", "n", "--reconcile-only"], environ={}, build_venue=build,
                root=tmp_path, clock=w.clock)


# ---- T45-1: the state file's layout 2, migrated from layout 1 with the baseline and sends untouched

LAYOUT_1 = [                                                    # event_practice.STATE_SCHEMA at b446e50, verbatim
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS baseline (symbol TEXT PRIMARY KEY, units REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS sends (tag TEXT PRIMARY KEY, day TEXT NOT NULL, time REAL NOT NULL, outcome TEXT)",
]


def layout_1_file(path):
    import sqlite3
    db = sqlite3.connect(path, isolation_level=None)
    for s in LAYOUT_1:
        db.execute(s)
    db.execute("PRAGMA user_version = 1")
    db.execute("INSERT INTO meta VALUES ('baseline_recorded', '1794600000.0')")
    db.execute("INSERT INTO baseline VALUES (?, ?)", (f"{T}:yes", 1.0))
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


def test_a_layout_1_state_file_migrates_to_layout_2_keeping_baseline_and_sends(tmp_path):
    import sqlite3
    p = tmp_path / "s.sqlite"
    layout_1_file(p)
    before = dump(p)
    st = ep.PracticeState(p)
    assert st.baseline() == {f"{T}:yes": 1.0} and st.settlements() == [] and st.open_holds() == []
    assert st.win_cash_observed() is False
    st.close()
    assert dump(p) == before
    db = sqlite3.connect(p)
    assert db.execute("PRAGMA user_version").fetchone()[0] == ep.STATE_LAYOUT == 2
    db.close()
    ep.PracticeState(p).close()                                                  # opens again as layout 2
    assert dump(p) == before


@pytest.mark.parametrize("version, extra", [(3, None), (1, "CREATE TABLE odd (x)"), (2, "CREATE TABLE odd (x)"), (0, None)])
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
