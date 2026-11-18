"""T51c S1 (addendum 1, R2): `OrderLedger.reconcile(..., one_sided=False)`.

Default (two-sided): every output byte-identical to the reconcile of commit cdd5371 (T51c's base) for the same ledger
and venue: the returned record, the stored reconcile summary and every row of every table. Shown on seeded ledgers and
venue answers, with differences both ways among them, against cdd5371's own `orders.py` taken with `git archive` from
this checkout's history (if it cannot be read, the test FAILS in words; nothing skips).

One-sided: what the account holds beyond the book and its unapplied fills is listed under `owner_surplus` and is not a
difference; an account under the book is listed under `position_shortfalls`, names the symbol, both figures and the two
ordinary causes, and makes the reconcile unclean; unapplied fills count as today; positions that could not be read
leave it unclean as today. Nothing here opens a socket."""
import importlib.util
import io
import json
import math
import random
import socket
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from valedia_trader.execution_and_risk import orders
from valedia_trader.execution_and_risk.orders import OrderError, OrderLedger

BASE = "cdd5371"
TESTS = Path(__file__).resolve().parent
SYMBOLS = ("SPY", "QQQ", "IWM", "TLT", "GLD", "XLE", "XLF")
WORLDS = 40


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise OSError("the T51c S1 tests open no socket")
    for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "create_connection"),
                        (socket, "getaddrinfo")):
        monkeypatch.setattr(owner, name, refuse)


class Refusal(Exception):
    """A refusal made on this machine before any network call."""


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Venue:
    """A scripted stand-in: takes every order, answers what `set` gave it, holds what `held` says."""
    name = "stand-in"

    def __init__(self):
        self.book, self.held, self.positions_raise = {}, {}, False

    def submit(self, symbol, units, tag, price=None, limit_price=None):
        self.book[tag] = {"state": "open", "filled_units": 0.0, "avg_price": None, "fee": None,
                          "venue_order_id": f"V-{tag}", "raw_status": "NEW"}
        return {"status": "accepted", "venue_order_id": f"V-{tag}"}

    def order_status(self, tag):
        got = self.book.get(tag)
        return dict(got) if got is not None else None

    def positions(self):
        if self.positions_raise:
            raise ConnectionError("positions endpoint down")
        return dict(self.held)


def ledger(module=orders, clock=None):
    return module.OrderLedger(":memory:", not_sent=(Refusal,), clock=clock or Clock())


def filled(led, venue, clock, tag, symbol, units, avg=100.0, fee=1.0, fill=None):
    """One order intended, sent and reported filled (`fill` units of it, all by default), then reconciled once with
    the venue holding exactly what was filled; returns that reconcile's fills."""
    led.intend(tag, symbol, units, avg, limit_price=avg * (1.01 if units > 0 else 0.99))
    led.submit_once(venue, tag)
    got = units if fill is None else fill
    venue.book[tag].update(state="filled" if fill is None else "partly_filled", filled_units=abs(got), avg_price=avg,
                           fee=fee, raw_status="FILLED")
    clock.t += 30
    return got


def ready(led, venue):
    out = led.reconcile(venue, dict(venue.held))
    assert out["clean"], out
    return out


# ---- one-sided


def test_one_sided_surplus_is_clean_and_reported():
    clock = Clock()
    led, venue = ledger(clock=clock), Venue()
    ready(led, venue)
    filled(led, venue, clock, "b-SPY", "SPY", 10.0)
    venue.held = {"SPY": 13.5, "AAPL": 4.0}                  # the owner bought 3.5 SPY and 4 AAPL by hand
    out = led.reconcile(venue, {}, one_sided=True)
    assert out["clean"] is True, out
    assert out["position_differences"] == {} and out["position_shortfalls"] == {}
    assert out["owner_surplus"] == {"AAPL": {"book": 0.0, "venue": 4.0, "unapplied": 0.0, "surplus": 4.0},
                                    "SPY": {"book": 0.0, "venue": 13.5, "unapplied": 10.0, "surplus": 3.5}}
    assert led.last_reconcile()["summary"]["owner_surplus"]["SPY"]["surplus"] == 3.5
    assert led.last_reconcile()["clean"] is True
    two = led.reconcile(venue, {})                           # the same answer two-sided: a difference, as on main
    assert two["clean"] is False and set(two["position_differences"]) == {"AAPL", "SPY"}
    assert "owner_surplus" not in two and "position_shortfalls" not in two


def test_one_sided_shortfall_is_unclean_and_named():
    clock = Clock()
    led, venue = ledger(clock=clock), Venue()
    ready(led, venue)
    filled(led, venue, clock, "b-QQQ", "QQQ", 8.0)
    out = led.reconcile(venue, {}, one_sided=False)
    assert out["clean"] is False                             # the venue still holds nothing: unapplied 8 vs 0
    led.mark_applied([f["id"] for f in led.unapplied_fills()])
    venue.held = {"QQQ": 5.0}                                # someone sold 3 of the book's shares
    out = led.reconcile(venue, {"QQQ": 8.0}, one_sided=True)
    assert out["clean"] is False
    short = out["position_shortfalls"]["QQQ"]
    assert {k: short[k] for k in ("book", "venue", "unapplied", "short")} == {"book": 8.0, "venue": 5.0,
                                                                             "unapplied": 0.0, "short": 3.0}
    words = short["reason"]
    for needed in ("QQQ", "5", "8", "the owner sold", "split"):
        assert needed in words, words
    assert out["position_differences"] == {} and out["owner_surplus"] == {}
    assert "the latest reconcile was not clean" in led.blocked()


def test_one_sided_counts_unapplied_fills():
    clock = Clock()
    led, venue = ledger(clock=clock), Venue()
    ready(led, venue)
    filled(led, venue, clock, "b-TLT", "TLT", 6.0)
    venue.held = {"TLT": 6.0}
    out = led.reconcile(venue, {}, one_sided=True)           # book 0 + unapplied 6 = venue 6: clean, no surplus
    assert out["clean"] is True and out["owner_surplus"] == {} and out["position_shortfalls"] == {}
    venue.held = {"TLT": 4.0}                                # the venue under book plus unapplied: a shortfall
    out = led.reconcile(venue, {}, one_sided=True)
    assert out["clean"] is False and out["position_shortfalls"]["TLT"]["short"] == 2.0
    assert out["position_shortfalls"]["TLT"]["unapplied"] == 6.0


def test_one_sided_within_tolerance_is_neither_surplus_nor_shortfall():
    clock = Clock()
    led, venue = ledger(clock=clock), Venue()
    ready(led, venue)
    venue.held = {"GLD": 2.0 + 5e-7, "IWM": 3.0 - 5e-7}
    out = led.reconcile(venue, {"GLD": 2.0, "IWM": 3.0}, one_sided=True)
    assert out["clean"] is True and out["owner_surplus"] == {} and out["position_shortfalls"] == {}
    venue.held = {"GLD": 2.0 + 2e-6, "IWM": 3.0 - 2e-6}
    out = led.reconcile(venue, {"GLD": 2.0, "IWM": 3.0}, one_sided=True)
    assert set(out["owner_surplus"]) == {"GLD"} and set(out["position_shortfalls"]) == {"IWM"}


def test_one_sided_a_figure_that_is_not_finite_stays_a_difference():
    clock = Clock()
    led, venue = ledger(clock=clock), Venue()
    ready(led, venue)
    venue.held = {"XLE": float("nan"), "XLF": 1.0}
    out = led.reconcile(venue, {"XLF": float("inf")}, one_sided=True)
    assert out["clean"] is False
    assert set(out["position_differences"]) == {"XLE", "XLF"}
    assert out["owner_surplus"] == {} and out["position_shortfalls"] == {}


def test_positions_unread_still_unclean():
    clock = Clock()
    led, venue = ledger(clock=clock), Venue()
    ready(led, venue)
    venue.positions_raise = True
    out = led.reconcile(venue, {}, one_sided=True)
    assert out["clean"] is False and out["positions_error_kind"] == "positions_unread"
    assert out["owner_surplus"] == {} and out["position_shortfalls"] == {}
    venue.positions_raise = False
    venue.held = {7: 1.0}
    out = led.reconcile(venue, {}, one_sided=True)
    assert out["clean"] is False and out["positions_error_kind"] == "positions_malformed"


@pytest.mark.parametrize("bad", [1, 0, None, "yes", 1.0])
def test_one_sided_must_be_true_or_false_and_nothing_is_recorded_otherwise(bad):
    led, venue = ledger(), Venue()
    with pytest.raises(OrderError, match="one_sided"):
        led.reconcile(venue, {}, one_sided=bad)
    assert led.last_reconcile() is None


# ---- the default against cdd5371


def _base_orders(folder):
    """cdd5371's own orders.py (stdlib only), loaded as a module of its own; or pytest.fail in words."""
    root = TESTS.parents[1]
    try:
        data = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", BASE,
                               "valedia_trader/execution_and_risk/orders.py"], capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        why = (getattr(e, "stderr", b"") or b"").decode("utf-8", "replace").strip()[:200]
        pytest.fail(f"T51c's base, commit {BASE}, cannot be read from this checkout's history ({type(e).__name__}: "
                    f"{why}); the comparison cannot be made, so it fails")
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(folder, filter="data")
    path = folder / "valedia_trader" / "execution_and_risk" / "orders.py"
    spec = importlib.util.spec_from_file_location("t51c_base_orders", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert "one_sided" not in path.read_text()
    return module


def _dump(led):
    db = led._db
    out = {"user_version": db.execute("PRAGMA user_version").fetchone()[0]}
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall():
        out[name] = [tuple(r) for r in db.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall()]
    out["schema"] = sorted(tuple(r) for r in db.execute("SELECT type, name, sql FROM sqlite_master"))
    return out


def _world(module, seed, one_sided=None):
    """One seeded ledger and venue, driven through three reconciles; returns every output and the ledger's dump.
    `one_sided` None calls reconcile exactly as main does (no such argument)."""
    rng = random.Random(seed)
    clock = Clock(1_700_000_000.0 + seed)
    led, venue = ledger(module, clock), Venue()
    kw = {} if one_sided is None else {"one_sided": one_sided}
    outs = [led.reconcile(venue, {}, **kw)]
    syms = rng.sample(SYMBOLS, rng.randint(1, 5))
    for i, sym in enumerate(syms):
        units = rng.choice([1.0, 2.0, 5.0, 10.0, 0.5, 3.25]) * rng.choice([1, 1, -1])
        fill = None if rng.random() < 0.7 else units / 2
        avg = round(rng.uniform(20, 400), 2)
        fee = rng.choice([None, 0.0, 1.0, 0.35])
        filled(led, venue, clock, f"w{seed}-{i}-{sym}", sym, units, avg=avg, fee=fee, fill=fill)
    filled_units = {}
    for tag, rec in venue.book.items():
        sym = tag.split("-", 2)[2]
        sign = 1.0 if led.order(tag)["units"] > 0 else -1.0
        filled_units[sym] = filled_units.get(sym, 0.0) + sign * rec["filled_units"]
    venue.held = dict(filled_units)
    outs.append(led.reconcile(venue, {}, **kw))               # fills written, unapplied
    if rng.random() < 0.6:
        led.mark_applied([f["id"] for f in led.unapplied_fills()])
        book = dict(filled_units)
    else:
        book = {}
    held = dict(filled_units)
    for sym in rng.sample(SYMBOLS, rng.randint(0, 4)):        # the owner, or a fault: either way round
        held[sym] = held.get(sym, 0.0) + rng.choice([-2.0, -0.5, 0.5, 1.0, 4.0, 1e-7])
    if rng.random() < 0.15:
        held[rng.choice(SYMBOLS)] = float("nan")
    venue.held = held
    venue.positions_raise = rng.random() < 0.1
    clock.t += 60
    outs.append(led.reconcile(venue, book, **kw))
    return {"outs": [json.dumps(o, default=str, sort_keys=False) for o in outs], "dump": _dump(led),
            "book": book, "held": held}


def test_two_sided_default_is_byte_identical(tmp_path, capsys):
    base = _base_orders(tmp_path / "base")
    both_ways = {"surplus": 0, "shortfall": 0, "clean": 0, "unclean": 0}
    for seed in range(WORLDS):
        old = _world(base, seed)
        new = _world(orders, seed)
        explicit = _world(orders, seed, one_sided=False)
        assert new["outs"] == old["outs"], seed
        assert new["dump"] == old["dump"], seed
        assert explicit["outs"] == old["outs"] and explicit["dump"] == old["dump"], seed
        last = json.loads(old["outs"][-1])
        both_ways["clean" if last["clean"] else "unclean"] += 1
        for sym, d in last["position_differences"].items():
            b, v, u = d["book"], d["venue"], d["unapplied"]
            if math.isfinite(b) and math.isfinite(v):
                both_ways["surplus" if v > b + u else "shortfall"] += 1
    with capsys.disabled():
        print(f"\nT51c-S1 {WORLDS} worlds byte-identical to {BASE}; differences seen: {both_ways}")
    assert both_ways["surplus"] > 0 and both_ways["shortfall"] > 0 and both_ways["clean"] > 0
