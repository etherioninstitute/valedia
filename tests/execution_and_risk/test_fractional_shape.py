"""T51 A4, R3: the shape of every order a run with `fractional` ON sends on the practice path, as properties over
seeded worlds (addendum 1 R3 with R1, R4, R5, R8; addendum 2 ruling C).

Each world is driven through the real `run_order_step` with a stand-in venue and a stand-in second price source, and
is one of every kind of day (ordinary days; partial fills; refusals by the venue; an order left over by the day's
cap; a fill through its limit or collar; a stopped run; a ticket proposed and approved; a ticket that expires; a
second run the same day; a baseline of holdings that are not the book's; live mode with `fractional`, refused), each
world also drawing: quotes that move between the ticket and its approval; a second source that disagrees for some
funds; clocks at the edges of the MARKET window (a half-day too) and a venue that lets time pass at each submit;
funds priced under 5 dollars and under 1 dollar; targets of under one share; holdings with decimals.

On every run:
  (i)   no LIMIT order carries a quantity with decimals;
  (ii)  no MARKET order carries a quantity of 1 or more, or of zero;
  (iii) every order is by quantity and none carries a dollar amount: what the venue is handed (every argument) and
        what `webull.order_payload` (the adapter's pure function, never a client) builds from it;
  (iv)  every MARKET order's session is CORE and it carries no limit price;
  (v)   every MARKET order has its collar price in the ledger, and none was sent outside the MARKET window (read
        here from the calendar), on a quote older than 120 s at the moment of sending, without the second source
        agreeing on that very quote in that run, or after its fund's LIMIT leg of the run was not accepted;
  (vi)  for every fund, the legs sent (or proposed) plus the legs held back plus the residual account for the whole
        difference (done runs and proposals; an approval: every line of its ticket is sent or held back as itself);
  (vii) no order sells more than the ledger's own book of a fund, across both legs.
Every quote the stand-in gives has its own price (a relative step of 1e-9 per ask), so the quote an order was sent
on is known exactly. Nothing here opens a socket or reaches the real second price source."""
import inspect
import json
import math
import random
import socket
from decimal import Decimal

import pandas as pd
import pytest

from valedia_trader.data_and_fees import second_quote
from valedia_trader.execution_and_risk import order_step as osx
from valedia_trader.venues import webull as _wb
from test_order_step import CAL, GENEROUS, Booked, LiveVenue, Second, StandIn, live_world, lpropose, ny, paper, \
    ticket_world
from test_fractional_legs import book_without, dec, todays_units

FUNDS = ("SPY", "QQQ", "IWM", "TLT", "GLD")
KINDS = ("ordinary", "partial", "refusal", "cap", "breach", "stopped", "approved", "expired", "second_run",
         "baseline", "live")
WORLDS_PER_KIND = 14
PRICE_SETS = ([500.0, 101.25, 7.77, 3.3, 0.37], [55.5, 20.0, 4.99, 0.85, 250.0], [400.0, 1.5, 0.21, 101.25, 9.99])
SIZES = (0.0, 0.004, 0.2, 0.37, 0.9, 1.0, 1.5, 2.5, 3.99999, 7.3, 12.5, 2.004, 40.0, 150.0)
CLOCKS = ("2026-11-13 10:30", "2026-11-13 11:00", "2026-11-13 13:00", "2026-11-13 14:00", "2026-11-13 09:36", "2026-11-13 09:59:59", "2026-11-13 10:00", "2026-11-13 10:00:01", "2026-11-13 12:00",
          "2026-11-13 15:29:50", "2026-11-13 15:30", "2026-11-13 15:30:01", "2026-11-13 15:44",
          "2026-11-27 11:00", "2026-11-27 12:29:55", "2026-11-27 12:30", "2026-11-27 12:30:01", "2026-11-27 12:44")
TIMES = ("10:30", "11:00", "13:00", "14:00", "09:36", "09:59:59", "10:00", "10:00:01", "12:00", "15:29:50", "15:30", "15:30:01", "15:44")
SUBMIT = inspect.signature(StandIn.submit)
COUNT_KEYS = ("i", "ii", "iii", "iv", "v", "vi", "vii")


@pytest.fixture(autouse=True)
def guard(monkeypatch):
    """No socket and no real second price source; LIVE_REPO put back after each test."""
    seen = []

    def no_socket(*a, **k):
        seen.append("socket")
        raise OSError("the T51 A4 tests open no socket")
    for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "create_connection"),
                        (socket, "getaddrinfo")):
        monkeypatch.setattr(owner, name, no_socket)

    def tripwire(*a, **k):
        seen.append("second source")
        raise AssertionError("the real second price source would be loaded or asked")
    monkeypatch.setattr(osx, "_load_second_source", tripwire, raising=False)
    monkeypatch.setattr(second_quote, "second_opinion", tripwire)
    monkeypatch.setattr(_wb, "LIVE_REPO", getattr(_wb, "LIVE_REPO", _wb.REPO), raising=False)
    yield seen
    assert seen == [], seen


def _shaped(base_cls):
    class ShapeVenue(base_cls):
        """Keeps every argument of every submit with the clock at that moment and its answer; answers at random
        (accept, refuse, reject) or as forced; lets time pass after a submit; reports a share of fills through
        their limit or collar (by a hash of the tag)."""

        def setup(self, clock, rng, refuse=0.0, slow=0, through_pct=0):
            self.clock, self.rng, self.refuse, self.slow, self.through_pct = clock, rng, refuse, slow, through_pct
            self.args, self.forced = [], []

        def submit(self, *a, **kw):
            bound = SUBMIT.bind(self, *a, **kw)
            bound.apply_defaults()
            if self.forced:
                mode = self.forced.pop(0)
            else:
                r = self.rng.random()
                mode = "refuse" if r < self.refuse / 2 else "reject" if r < self.refuse else "accept"
            self.submit_mode = mode
            self.args.append({**{k: v for k, v in bound.arguments.items() if k != "self"}, "positional": len(a),
                              "keywords": sorted(kw), "t": self.clock.t, "mode": mode})
            try:
                return super().submit(*a, **kw)
            finally:
                self.clock.t += self.rng.choice([0, 0, self.slow])

        def order_status(self, tag):
            out = super().order_status(tag)
            if out is not None and out["avg_price"] is not None and hash_pct(tag) < self.through_pct:
                o = self.book[tag]
                out["avg_price"] = round(o["limit"] * (1.03 if o["units"] > 0 else 0.97), 2)
            return out
    return ShapeVenue


def hash_pct(tag):
    import zlib
    return zlib.crc32(tag.encode()) % 100


ShapeVenue, ShapeLive = _shaped(Booked), _shaped(LiveVenue)


def _install_quotes(w):
    """Every ask gives each symbol its own price (relative step 1e-9 per ask) stamped `age` seconds before the
    clock: the first ask of a run at the world's age, later asks at `reask_age`. Each (symbol, price) is kept with
    its stamp, so the quote an order went on is known."""
    w.asks, w.ask_n, w.asked = {}, 0, set()

    def quotes(symbols):
        w.ask_n += 1
        w.venue.calls.append(("quotes", tuple(symbols)))
        out = {}
        for s in symbols:
            if s not in w.prices:
                continue
            age = w.reask_age.get(s, 60.0) if s in w.asked else w.quote_age.get(s, 60.0)
            w.asked.add(s)
            p = w.prices[s] * (1 + w.ask_n * 1e-9)
            stamp = w.clock.t - age
            w.asks[(s, p)] = stamp
            out[s] = (p, stamp)
        return out
    w.quotes = quotes


def _draw_paper(rng, funds=FUNDS, equity=100_000.0):
    return paper({s: {sym: float(rng.choice(SIZES)) for sym in rng.sample(funds, rng.randint(1, len(funds)))}
                  for s in rng.sample(["a", "b"], rng.randint(1, 2))}, equity=equity)


def make_world(tmp, rng, kind):
    held = {}
    if kind == "baseline" or rng.random() < 0.3:                       # the owner's, some with decimals
        held = {sym: rng.choice([0.0, 1.0, 1.25, 3.0, 0.4372]) for sym in rng.sample(FUNDS, 3)}
        held = {s: u for s, u in held.items() if u}
    if kind == "live":
        w = live_world(tmp, ShapeLive(held=held))
    else:
        w = ticket_world(tmp, ShapeVenue(held=held, cash=rng.choice([2_000.0, 50_000.0, 1e7])))
        cap = rng.choice([1, 2, 3]) if kind == "cap" else rng.choice([4, 40, 40])
        w.limits.write_text(json.dumps({**GENEROUS, "max_orders_per_day": cap}))
    w.venue.setup(w.clock, rng, refuse=0.4 if kind == "refusal" else rng.choice([0.0, 0.0, 0.1]),
                  slow=rng.choice([0, 5, 40]), through_pct=50 if kind == "breach" else rng.choice([0, 0, 10]))
    w.venue.fill_at_once = rng.choice([0.5, 0.25]) if kind == "partial" else rng.choice([0.0, 1.0, 1.0])
    w.reask_age = {}
    _install_quotes(w)
    return w


def _new_day(w, rng):
    prices = rng.choice(PRICE_SETS)
    order = list(FUNDS)
    rng.shuffle(order)
    w.prices.update(dict(zip(order, prices)))
    for sym in FUNDS:
        w.quote_age[sym] = rng.choice([30.0, 60.0, 60.0, 60.0, 119.0, 121.0, 600.0])
        w.reask_age[sym] = rng.choice([10.0, 30.0, 30.0, 121.0])
    w.second.verdicts = {sym: rng.choice(["disagree", "no second quote"]) for sym in FUNDS if rng.random() < 0.15}


class Counts(dict):
    def add(self, key, n=1):
        self[key] = self.get(key, 0) + n


def check_run(w, out, mode, ticket, counts, before):
    """Properties (i) to (vii) on one run. `before` is the venue's args count before the run; `ticket` the
    proposal's summary for an approval."""
    batch = out.get("batch")
    handed = w.venue.args[before:]
    rows = {o["tag"]: o for o in w.orders() if batch and o["batch"] == batch}
    for a in handed:
        sym, u, tag, lim, session = a["symbol"], a["units"], a["tag"], a["limit_price"], a["session"]
        o = rows[tag]
        assert set(a["keywords"]) <= {"price", "limit_price", "session"} and a["positional"] <= 3, a   # (iii)
        payload = _wb.order_payload(sym, u, tag, limit_price=lim, session=session)
        assert payload["entrust_type"] == "QTY" and not any("amount" in k or "cash" in k for k in payload), payload
        assert Decimal(payload["quantity"]) == dec(abs(u)), (payload, u)
        counts.add("iii")
        if o["order_type"] == "LIMIT":
            assert lim is not None and lim == o["limit_price"] and payload["order_type"] == "LIMIT", (a, o)
            assert u == math.trunc(u) and abs(u) >= 1 and "." not in payload["quantity"], (a, payload)   # (i)
            assert not tag.endswith(".r"), a
            counts.add("i")
            continue
        assert o["order_type"] == "MARKET" and payload["order_type"] == "MARKET", (a, o)
        assert 0 < abs(u) < 1 and 0 < Decimal(payload["quantity"]) < 1, (a, payload)               # (ii)
        counts.add("ii")
        assert lim is None and session == "CORE" and "limit_price" not in payload, (a, payload)     # (iv)
        assert payload["support_trading_session"] == "CORE"
        counts.add("iv")
        assert o["collar_price"] is not None and o["collar_price"] > 0 and o["limit_price"] is None, o   # (v)
        day = pd.Timestamp(a["t"], unit="s", tz="UTC").tz_convert("America/New_York").normalize().tz_localize(None)
        assert CAL.is_session(day), a
        start = CAL.session_open(day).timestamp() + 30 * 60
        end = CAL.session_close(day).timestamp() - 30 * 60
        assert start <= a["t"] <= end, ("outside the MARKET window", a, start, end)
        stamp = w.asks[(sym, a["price"])]                               # the very quote it was sent on
        assert a["t"] - stamp <= 120, ("quote older than 120 s", a, a["t"] - stamp)
        assert any(c["symbol"] == sym and c["price"] == a["price"] and c["verdict"] == "agree" for c in w.second.calls), \
            ("no agreeing second source on that quote", a, w.second.calls)
        whole = f"{batch}-{sym}"
        has_whole = whole in rows or any(e["symbol"] == sym and e.get("leg") == "whole"
                                         for key in ("sent", "left_over", "refused", "dropped", "skipped")
                                         for e in out.get(key) or [])
        if has_whole:
            prior = [b for b in handed if b["tag"] == whole and b["t"] <= a["t"]]
            assert prior and handed.index(prior[0]) < handed.index(a) and prior[0]["mode"] == "accept", \
                ("a remainder after a LIMIT leg that was not accepted", a, prior)
        counts.add("v")
    if batch:                                                             # (vii): the ledger's orders and the venue's
        book = book_without(w, batch)
        for what, units in (("ledger", [(o["symbol"], o["units"]) for o in rows.values()]),
                            ("handed", [(a["symbol"], a["units"]) for a in handed])):
            sold = {}
            for sym, u in units:
                if u < 0:
                    sold[sym] = sold.get(sym, Decimal(0)) + dec(-u)
            for sym, s in sold.items():
                assert s <= dec(book.get(sym, 0.0)), ("a sale above the book", what, sym, s, book)
                counts.add("vii")
    if out.get("status") != "done":
        return
    if mode in ("scheduled", "propose"):                                  # (vi)
        book = book_without(w, batch)
        capital = w.capital or w.paper.equity
        target, _ = osx.targets(w.paper, capital)
        skipped = {e["symbol"] for e in out.get("skipped") or []}
        res = {e["symbol"]: e for e in out.get("residuals") or []}
        lines = [_line(e) for e in out["ticket"]["orders"]] if mode == "propose" else out.get("sent") or []
        for sym in sorted(set(target) | set(book)):
            if sym in skipped:
                continue
            today = todays_units(target.get(sym, 0.0), book.get(sym, 0.0))
            legs = [e for e in lines if e["symbol"] == sym] + [e for key in ("left_over", "refused", "dropped")
                                                               for e in out.get(key) or [] if e["symbol"] == sym]
            got = sum((dec(e["units"]) for e in legs), Decimal(0)) + (dec(res[sym]["units"]) if sym in res else 0)
            assert got == dec(today), ("the legs and the residual do not account for the difference", sym, today,
                                       legs, res.get(sym))
            counts.add("vi")
    elif mode == "approve" and ticket and ticket.get("ticket_number") == w.number and out.get("orders_sent") is not None:
        proposed = sorted((e["symbol"], e.get("leg"), dec(e["units"])) for e in map(_line, ticket["ticket"]["orders"]))
        worked = sorted((e["symbol"], e.get("leg"), dec(e["units"])) for key in ("sent", "left_over", "refused", "dropped")
                        for e in out.get(key) or [])
        if worked or out.get("orders_sent"):
            assert worked == proposed, ("an approval sent or held back other than its ticket", proposed, worked)
            counts.add("vi", len(proposed))


def _line(e):
    """A ticket line as the summary's other lists have it: symbol and signed units."""
    assert (e["side"], e["units"] > 0) in (("buy", True), ("sell", False)), e
    return {**e, "symbol": e["fund"]}


def drive(w, rng, kind, counts):
    live = kind == "live"
    w.capital = None if live else rng.choice([None, None, 1_000.0, 250_000.0])
    w.number, ticket = None, None
    for day in range(1 if live else rng.choice([1, 2, 3])):
        _new_day(w, rng)
        w.paper = _draw_paper(rng, funds=("IWM", "TLT"), equity=5000.0) if live else _draw_paper(rng)
        if live:
            out = lpropose(w, fractional=True)                            # T51 B3: live no longer refuses --fractional; with no
            assert not w.venue.args and "submit" not in w.venue.names(), out   # practice proof and no consent every remainder is held
            if out["status"] == "done":
                assert out["live_fractions"]["open"] is False and all(ln.get("leg", "whole") == "whole" for ln in
                                                                      out.get("ticket", {}).get("orders", [])), out
                assert all(0 < abs(r["units"]) < 1 and r["reason"] == "live_fractions" for r in out["residuals"]
                           if r.get("reason")), out
            counts.add("live refused")
            return

        def run(mode, **kw):
            w.asked = set()
            w.second.calls = []
            before = len(w.venue.args)
            if w.capital is not None:
                kw["capital"] = w.capital
            out = w.run(fractional=True, **kw)
            check_run(w, out, mode, ticket, counts, before)
            counts.add("runs")
            counts.add("orders", len(w.venue.args) - before)
            counts.add("market", sum(1 for a in w.venue.args[before:] if a["limit_price"] is None))
            counts.add("held back by the collar", sum(1 for e in out.get("left_over") or [] if e.get("collar")))
            counts.add("residuals", len(out.get("residuals") or []))
            if out.get("faults"):
                counts.add("runs with faults")
            if out.get("status") == "stopped":
                counts.add("stopped")
            return out

        if kind == "stopped" and day == 0:
            how = rng.choice(["positions", "unknown", "lose"])
            if how == "positions":
                w.venue.raise_on = {"positions"}
            else:
                w.venue.forced = ["accept", "raise" if how == "unknown" else "lose"]
        if kind in ("approved", "expired") or rng.random() < 0.2:
            ticket = run("propose", propose=True)
            w.number = ticket.get("ticket_number")
            w.later(rng.choice([16, 25]) if kind == "expired" else rng.choice([1, 3, 10]))
            for sym in FUNDS:                                             # quotes move between ticket and approval
                if rng.random() < 0.4:
                    w.prices[sym] = w.prices[sym] * rng.choice([1.001, 0.996, 1.004, 1.006, 0.99])
            run("approve", approve=w.number or "000000")
            if kind == "expired":
                counts.add("expired approvals")
        else:
            run("scheduled")
        w.venue.raise_on = set()
        w.venue.forced = []
        if kind == "second_run" or rng.random() < 0.3:
            for tag, o in list(w.venue.book.items()):
                if o["state"] in ("open", "partly_filled") and rng.random() < 0.6:
                    w.venue.fill(tag, abs(o["units"]) * rng.choice([0.25, 0.5]))
            w.later(rng.choice([1, 20]))
            if rng.random() < 0.5:
                w.paper = _draw_paper(rng)
            run("scheduled")
        w.next_day(at=rng.choice(TIMES))


@pytest.mark.parametrize("kind", KINDS)
def test_r3_every_order_of_a_fractional_practice_run_has_the_ruled_shape_over_seeded_worlds(tmp_path, kind):
    counts = Counts()
    for k in range(WORLDS_PER_KIND):
        seed = 5_130_000 + KINDS.index(kind) * 100 + k
        rng = random.Random(seed)
        urandom, numbers = random.Random(seed + 1), random.Random(seed + 2)
        with pytest.MonkeyPatch.context() as mp:                         # batch ids and ticket numbers: seeded too,
            mp.setattr("os.urandom", lambda n: bytes(urandom.getrandbits(8) for _ in range(n)))   # so a seed replays
            mp.setattr("secrets.randbelow", lambda n: numbers.randrange(n))
            w = make_world(tmp_path / f"w{k}", rng, kind)
            w.clock.t = ny(rng.choice(CLOCKS))
            try:
                drive(w, rng, kind, counts)
            except AssertionError as e:
                raise AssertionError(f"seed {seed} ({kind}): {e}") from None
    print(f"T51-A4-R3 {kind}: worlds {WORLDS_PER_KIND}, {dict(sorted(counts.items()))}")
    if kind == "live":
        assert counts["live refused"] == WORLDS_PER_KIND
        return
    assert counts["runs"] >= WORLDS_PER_KIND, counts
    if kind == "expired":
        assert counts["expired approvals"] >= WORLDS_PER_KIND, counts
    else:
        assert counts.get("iii", 0) > 0 and counts.get("vi", 0) > 0, counts


def test_the_guard_fails_a_run_that_would_load_the_real_second_source_or_open_a_socket(tmp_path, guard):
    w = make_world(tmp_path / "w", random.Random(1), "ordinary")
    w.paper = paper({"a": {"SPY": 0.5}})                                 # one MARKET leg, no stand-in handed in
    w.prices["SPY"] = 500.0
    w.run(fractional=True, second_opinion=None)
    assert guard == ["second source"]                                    # the teardown would fail the test
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", 9))
    assert guard == ["second source", "socket"]
    guard.clear()
