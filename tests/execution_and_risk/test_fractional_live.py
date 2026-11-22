"""T51 build B: the live opening for fractions, behind four locks (docs/WEBULL.md, "Fractions on live").

B1 the practice proof, read from the PRACTICE ledger and journal: a MARKET remainder purchase and a MARKET remainder sale
   (layout 4, `order_type` MARKET, joined to an `order` journal line with leg `remainder` and to its fill) that filled
   and reconciled cleanly;
B2 the owner's own consent line `fractional_live_consent: true` in the live confirmation file;
B3 the opening itself: the live refusals are lifted for remainder legs only, and the adapter takes an order with no
   limit price only for them;
B4 the first-contact rule: at most one live MARKET remainder leg per run until one has filled and reconciled cleanly in
   the LIVE record;
B5 a remainder leg asks again for its quote when it is past 120 seconds at the moment of judging.
Everything runs on stand-in venues, quotes, second sources and clocks in a temporary folder; nothing opens a socket and
no real owner file is read."""
import json
import os
import random
import socket
import sqlite3
from pathlib import Path

import pytest

from valedia_trader.execution_and_risk import go_live as gl
from valedia_trader.execution_and_risk import order_step as osx
from valedia_trader.execution_and_risk import orders
from test_order_step import paper
from test_fractional_legs import leg_world


@pytest.fixture(autouse=True)
def guard(monkeypatch):
    """No test here opens a socket or reaches the real second price source."""
    def no_socket(*a, **k):
        raise OSError("the T51 B tests open no socket")
    for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "create_connection"),
                        (socket, "getaddrinfo")):
        monkeypatch.setattr(owner, name, no_socket)
    monkeypatch.setattr(osx, "_load_second_source", lambda: pytest.fail("the real second price source would be loaded"),
                        raising=False)


# ---- B1: the practice proof reading

def practice_world(tmp, **kw):
    w = leg_world(tmp, fill=1.0, **kw)                      # every order fills at once, as the practice proof's did
    return w


def proof(w, **kw):
    return osx.practice_proof(w.ledger_path, w.journal.dir, w.venue.name, **kw)


def buy_then_sell(w, buy=True, sell=True, settle=True):
    """Run on practice with fractions: a purchase with a MARKET remainder (2.5 SPY: a LIMIT of 2 and a MARKET of 0.5), a
    run that sells a remainder (2.5 down to 2.1: a MARKET sale of 0.4), and a run with nothing to do whose reconcile is
    the NEXT one after both fills."""
    outs = []
    if buy:
        w.paper = paper({"a": {"SPY": 2.5}})
        outs.append(w.run(fractional=True))
        w.later(30)
    if sell:
        w.paper = paper({"a": {"SPY": 2.1 if buy else 0.0}})
        if not buy:
            w.venue.held = {"SPY": 2.5}
        outs.append(w.run(fractional=True))
        w.later(30)
    if settle:
        outs.append(w.run(fractional=True))                 # nothing to do: its first reconcile is "the next reconcile"
        w.later(30)
    return outs


def test_b1_the_reading_is_the_two_items_and_nothing_less(tmp_path):
    w = practice_world(tmp_path / "w")
    w.ledger().close()                                    # an empty practice ledger: the reading says what is missing
    p = proof(w)
    assert (p.purchases, p.sales, p.ok) == (0, 0, False) and p.unreadable is None
    assert [m for m in p.missing] == ["a MARKET remainder PURCHASE that filled and reconciled cleanly",
                                      "a MARKET remainder SALE that filled and reconciled cleanly"]
    outs = buy_then_sell(w, sell=False)
    assert all(o["status"] == "done" and "faults" not in o for o in outs), outs
    p = proof(w)
    assert (p.purchases, p.sales, p.ok) == (1, 0, False)
    assert p.missing == ["a MARKET remainder SALE that filled and reconciled cleanly"]
    outs = buy_then_sell(w, buy=False)
    p = proof(w)
    assert (p.purchases, p.sales, p.ok) == (1, 1, True) and p.missing == [] and p.unreadable is None
    assert "both" in p.words() and "purchase" in p.words() and "sale" in p.words()


def test_b1_a_sale_alone_or_a_purchase_alone_is_not_the_proof(tmp_path):
    w = practice_world(tmp_path / "sale")
    w.paper = paper({"a": {"SPY": 3.0}})                  # the book buys whole shares only: no remainder purchase
    w.run(fractional=True)
    w.later(30)
    w.paper = paper({"a": {"SPY": 2.6}})
    w.run(fractional=True)
    w.later(30)
    w.run(fractional=True)
    p = proof(w)
    assert (p.purchases, p.sales, p.ok) == (0, 1, False), p


def test_b1_the_fills_own_reconcile_is_not_the_next_one_so_nothing_counts_until_one_more_reconcile_ran(tmp_path):
    w = practice_world(tmp_path / "w")
    buy_then_sell(w, settle=False)
    p = proof(w)
    assert (p.purchases, p.sales) == (1, 0), p            # the purchase has been followed by the sale run's reconcile
    w.run(fractional=True)
    assert (proof(w).purchases, proof(w).sales) == (1, 1)


def test_b1_a_difference_for_that_fund_at_the_next_reconcile_means_it_did_not_reconcile_cleanly(tmp_path):
    w = practice_world(tmp_path / "w")
    w.paper = paper({"a": {"SPY": 2.5}})
    w.run(fractional=True)
    w.later(30)
    w.venue.held["SPY"] += 0.01                           # the broker holds something the book does not: a difference
    out = w.run(fractional=True)
    assert out["status"] == "stopped", out                # the reconcile was not clean (a stop at the reconcile)
    w.venue.held["SPY"] -= 0.01
    w.later(30)
    w.run(fractional=True)
    assert proof(w).purchases == 0, "the next reconcile after the fill showed a difference; a later clean one cannot undo it"


def _rewrite_journal(w, change):
    for path in sorted(Path(w.journal.dir).glob("*.jsonl")):
        lines = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
        path.write_text("".join(json.dumps(change(e)) + "\n" for e in lines))


@pytest.mark.parametrize("how", ["faults", "stopped summary", "no summary", "no order line", "order line a whole leg",
                                 "order line a LIMIT", "another venue", "no fill line", "shortfall"])
def test_b1_the_journal_must_say_this_code_path_wrote_it_and_the_batch_ended_done_without_a_fault(tmp_path, how):
    w = practice_world(tmp_path / "w")
    buy_then_sell(w, sell=False)
    assert proof(w).purchases == 1

    def change(e):
        if e.get("type") != "order_step":
            return e
        if how == "faults" and e.get("event") == "summary":
            e["faults"] = ["cap"]
        elif how == "stopped summary" and e.get("event") == "summary":
            e["status"] = "stopped"
        elif how == "no summary" and e.get("event") == "summary":
            e["event"] = "summary_lost"
        elif how == "no order line" and e.get("event") == "order" and e.get("leg") == "remainder":
            e["event"] = "order_lost"
        elif how == "order line a whole leg" and e.get("event") == "order" and e.get("leg") == "remainder":
            e["leg"] = "whole"
        elif how == "order line a LIMIT" and e.get("event") == "order" and e.get("leg") == "remainder":
            e["order_type"] = "LIMIT"
        elif how == "another venue":
            e["venue"] = "webull-production"
        elif how == "no fill line" and e.get("event") == "fill":
            e["event"] = "fill_lost"
        elif how == "shortfall" and e.get("event") == "summary":
            e["shared"] = {"shortfalls": {"SPY": {"short": 1}}}
        return e
    _rewrite_journal(w, change)
    assert proof(w).purchases == 0, how


def test_b1_a_layout3_row_never_counts_and_reading_it_writes_nothing(tmp_path):
    """An older code path wrote layout 3. Rows that look like the proof (filled, no limit, a journal that says
    MARKET remainder and done) in a layout-3 file count nothing, and the file is not upgraded by the reading."""
    w = practice_world(tmp_path / "w")
    path = w.ledger_path
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(str(path))
    for statement in orders._LAYOUT3_SCHEMA:
        raw.execute(statement)
    raw.execute("PRAGMA user_version = 3")
    for tag, units in (("os-1-SPY.r", 0.5), ("os-2-SPY.r", -0.4)):
        raw.execute("INSERT INTO orders (tag, symbol, units, reference_price, limit_price, batch, state, created, updated, "
                    "filled_units, avg_price, venue, finished_at) VALUES (?, 'SPY', ?, 500.0, NULL, ?, 'filled', 1.0, 2.0, "
                    "?, 500.0, ?, 2.0)", (tag, units, tag[:4], abs(units), w.venue.name))
        raw.execute("INSERT INTO fills (tag, symbol, units, price, time, applied) VALUES (?, 'SPY', ?, 500.0, 2.0, 1)",
                    (tag, units))
    raw.execute("INSERT INTO reconciles (time, clean, summary) VALUES (3.0, 1, ?)",
                (json.dumps({"clean": True, "fills": [{"id": 1, "tag": "os-1-SPY.r"}, {"id": 2, "tag": "os-2-SPY.r"}],
                             "position_differences": {}}),))
    raw.execute("INSERT INTO reconciles (time, clean, summary) VALUES (4.0, 1, ?)",
                (json.dumps({"clean": True, "fills": [], "position_differences": {}}),))
    raw.commit()
    raw.close()
    for tag, units, batch in (("os-1-SPY.r", 0.5, "os-1"), ("os-2-SPY.r", -0.4, "os-2")):
        for event, extra in (("order", {"tag": tag, "symbol": "SPY", "units": units, "leg": "remainder",
                                        "order_type": "MARKET", "state": "filled"}),
                             ("fill", {"fill_id": 1, "tag": tag, "symbol": "SPY", "units": units}),
                             ("summary", {"status": "done", "stage": None})):
            w.journal.write("2026-11-13", {"type": "order_step", "venue": w.venue.name, "batch": batch, "event": event, **extra})
    before = path.read_bytes()
    folder = sorted(os.listdir(path.parent))
    p = proof(w)
    assert (p.purchases, p.sales, p.ok) == (0, 0, False) and p.unreadable is None, p
    assert path.read_bytes() == before and sorted(os.listdir(path.parent)) == folder
    raw = sqlite3.connect(str(path))
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 3
    raw.close()


def test_b1_reading_writes_nothing_and_creates_nothing_and_a_missing_or_damaged_record_is_said_in_words(tmp_path):
    w = practice_world(tmp_path / "w")
    p = proof(w)                                          # no ledger file yet, no journal
    assert p.ok is False and p.unreadable and "ledger" in p.unreadable
    assert not w.ledger_path.exists()                     # the reading did not create the ledger file
    buy_then_sell(w)
    before = {str(q): q.read_bytes() for q in sorted(w.tmp.rglob("*")) if q.is_file()}
    assert proof(w).ok is True
    after = {str(q): q.read_bytes() for q in sorted(w.tmp.rglob("*")) if q.is_file()}
    assert after == before
    day = sorted(Path(w.journal.dir).glob("*.jsonl"))[0]
    day.write_text(day.read_text() + "{not json\n")
    p = proof(w)
    assert p.ok is False and p.unreadable and "journal" in p.unreadable and "damaged" in p.unreadable, p
    assert "refus" in p.words() or "cannot" in p.words()


def test_b1_over_seeded_practice_records_the_reading_equals_an_oracle_that_counts_the_orders_directly(tmp_path):
    """Seeded practice worlds (purchases, sales, partial fills, refusals, a day's run twice): the reading's counts
    equal a count made from the ledger and journal by hand."""
    counted = ok = 0
    for k in range(25):
        rng = random.Random(61_000 + k)
        w = practice_world(tmp_path / f"w{k}", rng=random.Random(k), refuse=rng.choice([0.0, 0.0, 0.3]))
        w.venue.fill_at_once = rng.choice([1.0, 1.0, 0.5, 0.0])
        sizes = [rng.choice([0.0, 2.5, 3.37, 1.25, 2.1, 0.5]) for _ in range(rng.randint(3, 6))]
        for target in sizes:
            w.paper = paper({"a": {"SPY": target}})
            w.run(fractional=True)
            w.later(30)
        w.run(fractional=True)
        p = proof(w)
        want_buy, want_sell = oracle_counts(w)
        assert (p.purchases, p.sales) == (want_buy, want_sell), (k, p, want_buy, want_sell)
        counted += want_buy + want_sell
        ok += p.ok
    assert counted > 10 and ok >= 1, (counted, ok)


def oracle_counts(w):
    """The proof by hand: every filled MARKET order of the ledger with all its fills applied and the journal's order line
    (remainder, MARKET), a fill line, every batch involved done without a fault, and the next reconcile after the fill's
    own showing no difference for the fund."""
    led = w.ledger()
    try:
        rows = [o for o in led.orders(("filled",)) if o["order_type"] == "MARKET"]
        fills = {o["tag"]: led.fills(o["tag"]) for o in rows}
        recs = led.reconciles()
    finally:
        led.close()
    entries = [e for e in w.journal.read() if e.get("type") == "order_step" and e.get("venue") == w.venue.name]
    done = {e["batch"] for e in entries if e.get("event") == "summary" and e.get("status") == "done" and not e.get("faults")}
    buys = sells = 0
    for o in rows:
        line = [e for e in entries if e.get("event") == "order" and e.get("tag") == o["tag"] and e.get("leg") == "remainder"
                and e.get("order_type") == "MARKET"]
        fill_lines = [e for e in entries if e.get("event") == "fill" and e.get("tag") == o["tag"]]
        rs = fills[o["tag"]]
        if not (line and fill_lines and rs and all(f["applied"] for f in rs)):
            continue
        if abs(sum(f["units"] for f in rs) - o["units"]) > 1e-9:
            continue
        if line[0]["batch"] not in done or any(e["batch"] not in done for e in fill_lines):
            continue
        last = max(f["id"] for f in rs)
        where = [i for i, r in enumerate(recs) if r["summary"] and any(f.get("id") == last for f in r["summary"].get("fills", []))]
        if not where or where[0] + 1 >= len(recs):
            continue
        nxt = recs[where[0] + 1]["summary"] or {}
        if o["symbol"] in nxt.get("position_differences", {}) or o["symbol"] in nxt.get("position_shortfalls", {}):
            continue
        if any(a.get("tag") == o["tag"] for a in nxt.get("anomalies", []) + nxt.get("unresolved", [])):
            continue
        buys += o["units"] > 0
        sells += o["units"] < 0
    return buys, sells


def test_the_practice_venue_name_is_the_go_live_gates_own():
    assert osx.PRACTICE_VENUE == gl.PAPER_VENUE == "webull-sandbox"
