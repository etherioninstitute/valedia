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
    run that sells a remainder (2.5 down to 2.1: a MARKET sale of 0.4; with no purchase run before it, 3 whole shares are
    bought first and 2.6 is the target), and a run with nothing to do whose reconcile is the NEXT one after both fills."""
    outs = []
    if buy:
        w.paper = paper({"a": {"SPY": 2.5}})
        outs.append(w.run(fractional=True))
        w.later(30)
    if sell:
        if not buy:
            w.paper = paper({"a": {"SPY": 3.0}})
            outs.append(w.run(fractional=True))
            w.later(30)
        w.paper = paper({"a": {"SPY": 2.1 if buy else 2.6}})
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
    w.paper = paper({"a": {"SPY": 2.1}})                  # the book holds 2.5: a MARKET sale of 0.4, then a reconcile
    outs = [w.run(fractional=True)]
    w.later(30)
    outs.append(w.run(fractional=True))
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


# ---- B2, B3: the live opening, through the real order step on a stand-in live venue

import test_order_step as T
import test_shared_live as SL
from test_fractional_legs import LegVenue

CONSENT = "fractional_live_consent"


class FracVenue(T.LiveVenue):
    """The live stand-in. It records every submit with the arguments the order step gave it, and applies NO adapter rule
    of its own: whatever the order step lets through to the venue is what these tests judge. (The adapter's own rules are
    held by tests/venues/test_webull.py.)"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.sent = []

    def submit(self, symbol, units, tag, price=None, limit_price=None, session="CORE", fractional_live=False):
        self.sent.append({"symbol": symbol, "units": units, "tag": tag, "price": price, "limit_price": limit_price,
                          "session": session, "fractional_live": fractional_live})
        return super().submit(symbol, units, tag, price, None, session)   # every order fills at the quote it was judged on

    def limitless(self):
        return [s for s in self.sent if s["limit_price"] is None]


@pytest.fixture
def mkl(tmp_path, monkeypatch, capsys):
    count = []

    def make(conf=None, venue=None, **limits):
        count.append(1)
        return SL.LW(tmp_path / f"w{len(count)}", monkeypatch, capsys, venue if venue is not None else FracVenue(),
                     conf, **limits)
    return make


def practice_record(lw, buy=True, sell=True, venue_name="webull-sandbox"):
    """A practice record for a live world: practice runs (fractions on, every order filled at once) in the same memory and
    journal folders, under the practice venue's name, ending with the reconcile that follows the last fill."""
    pw = leg_world(lw.tmp, fill=1.0)
    pw.venue.name = venue_name
    pw.ledger_path = osx.ledger_path_for(lw.tmp / "memory", venue_name)
    pw.state_path = osx.state_path_for(pw.ledger_path, venue_name)
    pw.clock = lw.clock
    outs = buy_then_sell(pw, buy=buy, sell=sell)
    assert all(o["status"] == "done" and "faults" not in o for o in outs), outs
    lw.later(30)
    return pw


def live_proposal(lw, **kw):
    kw.setdefault("fractional", True)
    out = lw.propose(**kw)
    return out


def frac_world(mkl, consent=True, proof=(True, True), target=12.5, conf=None):
    """A live shared world with a book of 10,000 open (a tenth of the paper book's 100,000), the paper book wanting
    `target` x 10 SPY, so the book's target is `target` SPY (a LIMIT leg and a MARKET remainder), the owner's consent as
    given and the practice record as given (a purchase, a sale)."""
    over = dict(conf or {})
    if consent is not None:
        over[CONSENT] = consent
    lw = mkl(conf=over)
    lw.open_book("10000")
    if any(proof):
        practice_record(lw, buy=proof[0], sell=proof[1])
    lw.paper = paper({"a": {"SPY": target * 10}})
    return lw


def test_b3_with_every_lock_a_live_run_sends_the_limit_leg_and_the_remainder_as_a_market_order_by_quantity(mkl):
    lw = frac_world(mkl)
    out = lw.propose(fractional=True)
    assert out["status"] == "done" and out["live_fractions"]["open"] is True, out
    assert [(ln["units"], ln["leg"]) for ln in out["ticket"]["orders"]] == [(12.0, "whole"), (0.5, "remainder")], out
    assert lw.venue.sent == []
    lw.later(1)
    done = lw.approve(out["ticket_number"], fractional=True)
    assert done["status"] == "done" and "faults" not in done, done
    a, b = lw.venue.sent
    assert (a["units"], a["limit_price"], a["fractional_live"]) == (12.0, 505.0, False)
    assert (b["units"], b["limit_price"], b["fractional_live"], b["session"]) == (0.5, None, True, "CORE")
    assert b["tag"].endswith("-SPY.r") and 0 < abs(b["units"]) < 1
    recs = {o["tag"]: o for o in lw.orders()}
    assert recs[b["tag"]]["order_type"] == "MARKET" and recs[b["tag"]]["collar_price"] == 505.0


def whole_only(lw, out):
    """The opening is shut (T51 B repair F1): the run is the live run without --fractional: whole-share LIMIT lines only,
    no leg mark, no residual, nothing a fault, an attention item or a left-over; only the words say why."""
    assert out["status"] == "done" and "faults" not in out and out["left_over"] == [] and out["refused"] == [], out
    assert all("leg" not in ln for ln in out.get("ticket", {}).get("orders", [])), out
    assert "residuals" not in out, out
    assert out["live_fractions"]["open"] is False and out["live_fractions"]["why"], out


@pytest.mark.parametrize("value", [False, "true", "yes", 1, 0, None, [True], {"a": True}, 1.0, "", None.__class__])
def test_b2_a_consent_that_is_not_json_true_holds_the_remainder_in_words_and_the_whole_leg_goes_as_before(mkl, value):
    lw = frac_world(mkl, consent=None, conf={CONSENT: None} if value is None.__class__ else {CONSENT: value})
    if value is None.__class__:                           # the line absent altogether
        lw.write_conf()
    out = lw.propose(fractional=True)
    whole_only(lw, out)
    assert CONSENT in out["live_fractions"]["why"] and "no consent" in out["live_fractions"]["why"]
    assert out["live_fractions"]["consent"] is False and "practice" in out["live_fractions"]
    assert [ln["units"] for ln in out["ticket"]["orders"]] == [12.0]
    lw.later(1)
    done = lw.approve(out["ticket_number"], fractional=True)
    assert done["status"] == "done" and done["orders_sent"] == 1 and lw.venue.limitless() == [], done


@pytest.mark.parametrize("proof, missing", [((False, False), ("PURCHASE", "SALE")), ((True, False), ("SALE",)),
                                            ((False, True), ("PURCHASE",))])
def test_b1_a_practice_record_missing_a_side_holds_the_remainder_and_says_what_is_missing(mkl, proof, missing):
    lw = frac_world(mkl, proof=proof)
    if proof == (False, False):
        pw = leg_world(lw.tmp, fill=1.0)                  # a practice ledger with nothing in it
        pw.venue.name = "webull-sandbox"
        osx.open_ledger(osx.ledger_path_for(lw.tmp / "memory", "webull-sandbox")).close()
    out = lw.propose(fractional=True)
    whole_only(lw, out)
    why = out["live_fractions"]["why"]
    assert "practice proof" in why and all(m in why for m in missing), why
    assert not [m for m in ("PURCHASE", "SALE") if m not in missing and m in why], why
    assert out["live_fractions"]["consent"] is True
    lw.later(1)
    assert lw.approve(out["ticket_number"], fractional=True)["orders_sent"] == 1 and lw.venue.limitless() == []


def test_b1_no_practice_ledger_at_all_holds_the_remainder_and_creates_nothing(mkl):
    lw = frac_world(mkl, proof=(False, False))
    practice = osx.ledger_path_for(lw.tmp / "memory", "webull-sandbox")
    assert not practice.exists()
    out = lw.propose(fractional=True)
    whole_only(lw, out)
    assert "practice" in out["live_fractions"]["why"] and "cannot be" in out["live_fractions"]["why"]
    assert not practice.exists()


def test_b1_a_practice_record_of_layout3_orders_never_counts_through_a_live_run(mkl):
    lw = frac_world(mkl, proof=(False, False))
    path = osx.ledger_path_for(lw.tmp / "memory", "webull-sandbox")
    raw = sqlite3.connect(str(path))
    for statement in orders._LAYOUT3_SCHEMA:
        raw.execute(statement)
    raw.execute("PRAGMA user_version = 3")
    raw.commit()
    raw.close()
    before = path.read_bytes()
    out = lw.propose(fractional=True)
    whole_only(lw, out)
    assert path.read_bytes() == before                    # reading it did not upgrade it


def test_a_live_run_with_fractions_off_is_what_it_was_and_says_nothing_of_them(mkl):
    lw = frac_world(mkl)
    out = lw.propose(fractional=False)
    assert out["status"] == "done" and "live_fractions" not in out and "residuals" not in out, out
    assert [ln["units"] for ln in out["ticket"]["orders"]] == [12.0] and not any("leg" in ln for ln in out["ticket"]["orders"])


def test_the_live_summary_is_journaled_with_the_openings_words(mkl):
    lw = frac_world(mkl, consent=False)
    out = lw.propose(fractional=True)
    (summary,) = [e for e in lw.journal_events("summary") if e["batch"] == out["batch"]]
    assert summary["live_fractions"] == out["live_fractions"] and summary["live_fractions"]["open"] is False


def test_b3_the_ticket_made_with_fractions_is_refused_by_an_approval_without_them_and_the_reverse(mkl):
    lw = frac_world(mkl)
    out = lw.propose(fractional=True)
    lw.later(1)
    refused = lw.approve(out["ticket_number"], fractional=False)
    assert refused["status"] == "stopped" and "fractional" in refused["reason"] and lw.venue.sent == [], refused


def test_b2_taking_the_consent_back_between_propose_and_approve_refuses_the_approval_and_sends_nothing(mkl):
    lw = frac_world(mkl)
    out = lw.propose(fractional=True)
    lw.later(1)
    lw.write_conf(**{CONSENT: False})                     # the owner takes it back: the files changed since the ticket
    refused = lw.approve(out["ticket_number"], fractional=True)
    assert refused["status"] == "stopped" and lw.venue.sent == [], refused
    assert "changed" in refused["reason"] or "owner's files" in refused["reason"], refused


def test_b3_a_live_remainder_is_judged_by_the_confirmations_caps_like_a_whole_leg(mkl):
    lw = frac_world(mkl, target=0.5, conf={CONSENT: True, "max_order_value": 200})
    out = lw.propose(fractional=True)
    assert out["status"] == "done" and out["ticket"]["orders"] == [] and out["live_fractions"]["open"] is True, out
    assert [d["leg"] for d in out["dropped"]] == ["remainder"] and "max_order_value" in out["dropped"][0]["reason"], out
    lw2 = frac_world(mkl, target=0.5, conf={CONSENT: True, "max_order_value": 300})
    out = lw2.propose(fractional=True)
    assert [(ln["units"], ln["leg"]) for ln in out["ticket"]["orders"]] == [(0.5, "remainder")], out
    lw2.later(1)
    done = lw2.approve(out["ticket_number"], fractional=True)
    assert done["orders_sent"] == 1 and [s["fractional_live"] for s in lw2.venue.limitless()] == [True], done


# ---- B4: the first-contact rule

MANY = {"SPY": 62.5, "QQQ": 37.0, "IWM": 31.0, "TLT": 23.0}       # a tenth of the book: 6.25, 3.7, 3.1, 2.3 units


def remainders(out):
    return [e for e in out.get("ticket", {}).get("orders", []) if e.get("leg") == "remainder"]


def test_b4_a_first_live_run_sends_one_market_remainder_and_holds_the_others_as_first_contact_residuals(mkl):
    lw = frac_world(mkl)
    lw.paper = paper({"a": MANY})
    out = lw.propose(fractional=True)
    assert out["status"] == "done" and out["live_fractions"]["first_contact"] is True, out
    assert [(e["fund"], e["units"]) for e in remainders(out)] == [("IWM", 0.1)], out["ticket"]["orders"]   # the least money
    held = sorted((r["symbol"], r["units"]) for r in out["residuals"] if r.get("reason") == "first_contact")
    assert held == [("QQQ", 0.7), ("SPY", 0.25), ("TLT", 0.3)], out["residuals"]
    assert all("first contact" in r["why"] for r in out["residuals"] if r.get("reason") == "first_contact")
    assert "faults" not in out and out["left_over"] == [] and out["refused"] == [] and out["dropped"] == [], out
    assert len([e for e in out["ticket"]["orders"] if e["leg"] == "whole"]) == 4
    lw.later(1)
    done = lw.approve(out["ticket_number"], fractional=True)
    assert done["status"] == "done" and "faults" not in done and done["orders_sent"] == 5, done
    assert [(s["symbol"], s["fractional_live"]) for s in lw.venue.limitless()] == [("IWM", True)]
    led = lw.ledger()
    try:
        assert led.attention() == []
    finally:
        led.close()


def test_b4_the_rule_ends_after_a_live_remainder_has_filled_and_reconciled_cleanly_in_the_live_record(mkl):
    lw = frac_world(mkl)
    lw.paper = paper({"a": MANY})
    out = lw.propose(fractional=True)
    lw.later(1)
    lw.approve(out["ticket_number"], fractional=True)                 # run A: the first live remainder, filled at once
    assert len(lw.venue.limitless()) == 1
    lw.later(30)
    b = lw.propose(fractional=True)                                   # run B: its first reconcile is the one after the fill
    assert b["live_fractions"]["first_contact"] is True and b["live_fractions"]["live_clean"] == {"purchases": 0, "sales": 0}
    assert [e["fund"] for e in remainders(b)] == ["TLT"], b["ticket"]["orders"]   # the rule still binds: one leg, the least
    lw.later(30)
    c = lw.propose(fractional=True)                                   # run C: the live record now holds it
    assert c["live_fractions"]["first_contact"] is False and c["live_fractions"]["live_clean"]["purchases"] == 1, c
    assert sorted(e["fund"] for e in remainders(c)) == ["QQQ", "SPY", "TLT"], c["ticket"]["orders"]
    assert not [r for r in c.get("residuals", []) if r.get("reason") == "first_contact"]
    lw.later(1)
    done = lw.approve(c["ticket_number"], fractional=True)
    assert done["orders_sent"] == 3 and "faults" not in done, done
    assert sorted(s["symbol"] for s in lw.venue.limitless()) == ["IWM", "QQQ", "SPY", "TLT"]    # the first one, then these


def test_b4_a_live_remainder_that_did_not_reconcile_cleanly_does_not_end_the_rule(mkl):
    lw = frac_world(mkl)
    lw.paper = paper({"a": MANY})
    out = lw.propose(fractional=True)
    lw.later(1)
    lw.approve(out["ticket_number"], fractional=True)
    lw.later(30)
    lw.venue.held["IWM"] -= 0.02                                      # the account holds less than the book: a shortfall
    stopped = lw.propose(fractional=True)                             # ... at the reconcile right after the fill
    assert stopped["status"] == "stopped", stopped
    lw.venue.held["IWM"] += 0.02
    for _ in range(2):
        lw.later(30)
        lw.propose(fractional=True)
    last = lw.propose(fractional=True)
    assert last["status"] == "done" and last["live_fractions"]["first_contact"] is True, last
    assert last["live_fractions"]["live_clean"] == {"purchases": 0, "sales": 0}


def test_b4_practice_is_not_limited_by_the_rule(tmp_path):
    w = leg_world(tmp_path / "w", fill=1.0)
    w.paper = paper({"a": {"SPY": 2.5, "QQQ": 3.7, "IWM": 4.3, "TLT": 1.4}})
    out = w.run(fractional=True)
    assert out["status"] == "done" and "live_fractions" not in out, out
    assert sorted(e["symbol"] for e in out["sent"] if e["leg"] == "remainder") == ["IWM", "QQQ", "SPY", "TLT"]
    assert not [r for r in out["residuals"] if r.get("reason")]


def test_b4_a_first_contact_approval_whose_quotes_moved_keeps_to_the_ticket_and_never_sends_two(mkl):
    lw = frac_world(mkl)
    lw.paper = paper({"a": MANY})
    out = lw.propose(fractional=True)
    lw.prices["TLT"] = 0.01                                           # the fresh values reorder: IWM no longer the least
    lw.prices["IWM"] = 400.0
    lw.later(1)
    done = lw.approve(out["ticket_number"], fractional=True)
    assert len(lw.venue.limitless()) <= 1, lw.venue.limitless()


# ---- B5: a remainder leg asks again for its quote when it is past 120 seconds at the moment of judging

from test_fractional_collar import collar_world, held_back


def age_the_market_leg_after_its_checks(w, monkeypatch, seconds):
    """The stand-in clock moves on while the MARKET leg is being judged (in the risk gate's check of an order under
    300 dollars: the 0.5 SPY leg, never the 2 SPY LIMIT leg), so its quote has aged by the time its collar looks."""
    real = osx.risk_gate.check_order
    seen = []

    def check(order, *a, **k):
        if order["value"] < 300 and not seen:
            seen.append(1)
            w.clock.t += seconds
        return real(order, *a, **k)
    monkeypatch.setattr(osx.risk_gate, "check_order", check)


def test_b5_a_quote_that_passed_120_seconds_while_the_leg_was_judged_is_asked_for_once_more_and_taken_when_newer(tmp_path, monkeypatch):
    w = collar_world(tmp_path / "w")                                  # the quote is 60 s old whenever it is asked
    age_the_market_leg_after_its_checks(w, monkeypatch, 61.0)         # 121 s old at the collar
    real = w.quotes
    asks = []

    def quotes(symbols):
        asks.append(list(symbols))
        if len(asks) > 1:
            w.prices["SPY"] = 502.0                                   # the venue's quote has moved on, by 0.4%
        return real(symbols)
    w.quotes = quotes
    out = w.run(fractional=True)
    b = out["batch"]
    assert out["status"] == "done" and "faults" not in out, out
    assert [a["tag"] for a in w.venue.args] == [f"{b}-SPY", f"{b}-SPY.r"], out
    o = {x["tag"]: x for x in w.orders()}
    assert o[f"{b}-SPY.r"]["reference_price"] == 502.0                 # the collar judged the newer quote
    assert o[f"{b}-SPY.r"]["collar_price"] == osx.limit_price(0.5, 502.0) == 507.02   # ... and its price comes from it
    assert asks == [["SPY"], ["SPY"]], asks                            # the first ask of the run, and one once more


def test_b5_the_newer_quote_is_judged_again_by_everything_the_old_one_was_not_only_the_collar(tmp_path, monkeypatch):
    """The new quote 502 is within the band of the decided quote 500 but the second source disagrees on it: held back."""
    w = collar_world(tmp_path / "w")
    age_the_market_leg_after_its_checks(w, monkeypatch, 61.0)
    real = w.quotes
    n = [0]

    def quotes(symbols):
        n[0] += 1
        if n[0] > 1:
            w.prices["SPY"] = 502.0
        return real(symbols)
    w.quotes = quotes
    w.second.by_price[("SPY", 502.0)] = "disagree"
    out = w.run(fractional=True)
    (left,) = held_back(out)
    assert left["collar"] == "second_source" and "faults" not in out, out


def test_b5_a_source_that_gives_nothing_newer_leaves_the_leg_held_back_by_the_collar_and_it_is_no_fault(tmp_path, monkeypatch):
    w = collar_world(tmp_path / "w")
    age_the_market_leg_after_its_checks(w, monkeypatch, 61.0)
    asks, first = [], {}
    real = w.quotes

    def quotes(symbols):
        asks.append(list(symbols))
        got = real(symbols)
        first.setdefault("quote", got)                                # every later ask gives the first answer again
        return first["quote"]
    w.quotes = quotes
    out = w.run(fractional=True)
    b = out["batch"]
    assert out["status"] == "done" and "faults" not in out and "cap_unjudged" not in out, out
    assert [a["tag"] for a in w.venue.args] == [f"{b}-SPY"]            # its LIMIT leg went
    (left,) = held_back(out)
    assert left["collar"] == "quote_age" and f"{osx.MARKET_QUOTE_MAX_AGE_SECONDS} seconds" in left["reason"], left
    assert asks == [["SPY"], ["SPY"]], asks                            # one once more, not a third
    led = w.ledger()
    try:
        assert led.attention() == []
    finally:
        led.close()


@pytest.mark.parametrize("age, extra_asks", [(119.0, 0), (120.0, 0), (121.0, 1)])
def test_b5_the_limit_stays_120_seconds_a_quote_at_the_limit_is_not_asked_for_again(tmp_path, monkeypatch, age, extra_asks):
    w = collar_world(tmp_path / "w")
    w.quote_age["SPY"] = age - 60.0                                    # the age at the start; 60 s more pass while it is judged
    age_the_market_leg_after_its_checks(w, monkeypatch, 60.0)
    asks = []
    real = w.quotes
    w.quotes = lambda symbols: (asks.append(list(symbols)), real(symbols))[1]
    out = w.run(fractional=True)
    assert out["status"] == "done" and "faults" not in out, out
    assert len(asks) == 1 + extra_asks, (age, asks)


def test_b5_a_leg_whose_quote_was_just_asked_for_in_the_same_second_is_not_asked_again(tmp_path):
    w = collar_world(tmp_path / "w")
    w.quote_age["SPY"] = 130.0                                         # old at the start; the clock never moves
    asks = []
    real = w.quotes
    w.quotes = lambda symbols: (asks.append(list(symbols)), real(symbols))[1]
    out = w.run(fractional=True)
    (left,) = held_back(out)
    assert left["collar"] == "quote_age" and asks == [["SPY"], ["SPY"]], (asks, left)   # the start, and the leg's own refresh


def test_b5_it_applies_to_a_live_remainder_leg_too(mkl):
    def asks_of_spy(slow):
        lw = frac_world(mkl, target=0.5)
        state = {"slow": False}

        class Second(type(lw.second)):
            def __call__(self, first_price, symbol, **kw):
                if state["slow"]:
                    state["slow"] = False
                    lw.clock.t += 61.0                                # the second source takes a minute the first time
                return super().__call__(first_price, symbol, **kw)
        lw.second = Second()
        out = lw.propose(fractional=True)                             # a proposal has no collar: nothing to ask again
        assert [(e["units"], e["leg"]) for e in out["ticket"]["orders"]] == [(0.5, "remainder")], out
        lw.later(1)
        before = len(lw.quote_calls)
        state["slow"] = slow
        done = lw.approve(out["ticket_number"], fractional=True)
        assert done["status"] == "done" and done["orders_sent"] == 1 and "faults" not in done, (slow, done["left_over"], done["dropped"], done["refused"])
        assert [s["fractional_live"] for s in lw.venue.limitless()] == [True]
        return [c for c in lw.quote_calls[before:] if c == ["SPY"]]
    assert len(asks_of_spy(slow=True)) == len(asks_of_spy(slow=False)) + 1


# ---- the safety property, over seeded worlds: no order without a limit reaches a production venue unless every lock holds

PROOFS = ("both",) * 6 + ("purchase only", "sale only", "none", "layout 3 only", "damaged journal")
CONSENTS = (True,) * 6 + (False, "true", 1, "absent")


def seeded_world(mkl, rng):
    consent = rng.choice(CONSENTS)
    proof = rng.choice(PROOFS)
    fractional = rng.random() < 0.85
    lw = mkl(conf=None if consent == "absent" else {CONSENT: consent})
    lw.open_book("10000")
    practice = osx.ledger_path_for(lw.tmp / "memory", "webull-sandbox")
    if proof in ("both", "purchase only", "sale only"):
        practice_record(lw, buy=proof != "sale only", sell=proof != "purchase only")
    elif proof == "layout 3 only":
        practice.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(str(practice))
        for statement in orders._LAYOUT3_SCHEMA:
            raw.execute(statement)
        raw.execute("PRAGMA user_version = 3")
        raw.commit()
        raw.close()
    elif proof == "damaged journal":
        practice_record(lw)
        day = sorted(Path(lw.journal.dir).glob("*.jsonl"))[0]
        day.write_text(day.read_text() + "{torn\n")
    lw.paper = paper({"a": {sym: rng.choice([0.0, 12.5, 37.0, 62.5, 31.0, 23.0, 3.0, 7.0, 4.0]) for sym in
                            rng.sample(("SPY", "QQQ", "IWM", "TLT"), rng.randint(1, 4))}})
    holds = consent is True and proof == "both"                       # B1 and B2, by construction
    return lw, {"consent": consent, "proof": proof, "fractional": fractional, "open": holds and fractional}


def test_the_safety_property_over_seeded_live_worlds_no_order_without_a_limit_unless_every_lock_holds(mkl):
    limitless = whole = worlds_open = worlds_shut = 0
    for k in range(48):
        rng = random.Random(62_000 + k)
        lw, world = seeded_world(mkl, rng)
        for step in range(rng.randint(1, 3)):
            out = lw.propose(fractional=world["fractional"])
            if out.get("status") != "done" or not out.get("ticket_number"):
                lw.later(30)
                continue
            lw.later(1)
            if rng.random() < 0.2:                                    # the owner takes his consent back before the approval
                lw.write_conf(**{CONSENT: False})
                world["open"] = False
            sent_before = len(lw.venue.sent)
            led = lw.ledger()
            try:
                cleared = any(o["order_type"] == "MARKET" and o["state"] == "filled" for o in led.orders())
            finally:
                led.close()
            done = lw.approve(out["ticket_number"], fractional=world["fractional"])
            mine = lw.venue.sent[sent_before:]
            market = [s for s in mine if s["limit_price"] is None]
            if not world["open"]:
                assert market == [], (k, world, [s["tag"] for s in market])
            for s in market:                                          # every B3 condition, for every one that went
                assert s["fractional_live"] is True and s["session"] == "CORE", (k, s)
                assert 0 < abs(s["units"]) < 1 and s["tag"].endswith(".r") and s["price"] and s["price"] > 0, (k, s)
            for s in mine:
                if s["limit_price"] is not None:
                    assert s["fractional_live"] is False and s["units"] == int(s["units"]), (k, s)
            if world["open"] and not cleared and done["status"] == "done":
                assert len(market) <= 1, (k, "first contact: at most one live MARKET leg per run", market)   # B4
            limitless += len(market)
            whole += len(mine) - len(market)
            lw.later(30)
        worlds_open += world["open"]
        worlds_shut += not world["open"]
    assert limitless > 8 and whole > 20 and worlds_open > 6 and worlds_shut > 8, (limitless, whole, worlds_open, worlds_shut)


# ---- B3: the shared book's figures, the reconcile and the day stop handle units with five decimals

def test_b3_a_five_decimal_live_purchase_and_sale_are_exact_in_the_book_the_reconcile_and_the_nav(mkl):
    lw = frac_world(mkl, target=0.43859)
    out = lw.propose(fractional=True)
    assert [(e["units"], e["leg"]) for e in out["ticket"]["orders"]] == [(0.43859, "remainder")], out
    lw.later(1)
    a = lw.approve(out["ticket_number"], fractional=True)
    assert a["status"] == "done" and a["orders_sent"] == 1 and "faults" not in a, a
    assert lw.venue.held == {"SPY": 0.43859}                                   # the venue's holding: the five decimals sent
    led = lw.ledger()
    try:
        figures = osx.book_figures(led.fills(), led.orders(), lw.entries(), {"SPY": 450.0}, "2026-11-13", now=lw.clock.t)
        assert figures.units["SPY"] == osx.Decimal("0.43859")                  # exact, not a float's neighbour
        assert figures.value == osx.Decimal("10000") - osx.Decimal("0.43859") * 50     # 9978.0705, to the last digit
        assert osx.broker_book(led.fills()) == {"SPY": 0.43859}
    finally:
        led.close()
    lw.later(30)
    lw.prices["SPY"] = 450.0
    b = lw.propose(fractional=True)                                            # its reconcile: the book against the venue
    assert b["status"] == "done" and "faults" not in b and not b["shared"].get("shortfalls"), b
    assert b["shared"]["book_value"] == 9978.0705 and b["shared"]["day_loss_pct"] == 0.219295, b["shared"]
    assert lw.nav()[-1]["value_low"] == "9978.070500" and lw.nav()[-1]["value_per_unit"] == "0.99780705"
    # a MARKET sale of the remainder: target 0.12764, so about 0.31095 is sold (the split rounds toward zero)
    lw.prices["SPY"] = 500.0
    lw.paper = paper({"a": {"SPY": 1.2764}})
    lw.later(30)
    s = lw.propose(fractional=True)
    ((line,),) = [[e for e in s["ticket"]["orders"]]]
    sold = -line["units"]
    assert (line["leg"], line["side"]) == ("remainder", "sell") and sold in (0.31094, 0.31095), s
    lw.later(1)
    d = lw.approve(s["ticket_number"], fractional=True)
    assert d["status"] == "done" and d["orders_sent"] == 1 and "faults" not in d, d
    assert lw.venue.held["SPY"] == pytest.approx(0.43859 - sold, abs=1e-12)
    lw.later(30)
    nxt = lw.propose(fractional=True)
    assert nxt["status"] == "done" and "faults" not in nxt and not nxt["shared"].get("shortfalls"), nxt   # a clean reconcile
    led = lw.ledger()
    try:
        left = sum((osx.Decimal(repr(f["units"])) for f in led.fills()), osx.Decimal(0))
        assert left == osx.Decimal("0.43859") - osx.Decimal(repr(sold))                # exact in Decimal
        assert [f["units"] for f in led.fills()] == [0.43859, -sold]
    finally:
        led.close()


def test_b3_the_day_stop_reads_the_loss_of_a_five_decimal_holding_to_the_last_digit(mkl):
    lw = frac_world(mkl, target=0.43859)
    out = lw.propose(fractional=True)
    lw.later(1)
    assert lw.approve(out["ticket_number"], fractional=True)["orders_sent"] == 1
    lw.later(30)
    lw.prices["SPY"] = 100.0                                                   # the holding loses 0.43859 x 400 = 175.436
    lw.paper = paper({"a": {"SPY": 10.0, "QQQ": 8.0}})                         # more purchases are wanted
    b = lw.propose(fractional=True)
    assert b["status"] == "done", b
    assert b["shared"]["day_loss_pct"] == pytest.approx(1.75436, abs=1e-9), b["shared"]
    assert b["day_stop"] is not None, b                                         # past the working stop of 1.00%
    assert all(r["by"] == "day stop" for r in b["refused"]) and b["refused"], b   # no purchase goes; nothing is a fault
    assert "faults" not in b


# ---- B6 (1): a ticket's stored limit and collar price are informational; the approval works out fresh ones

def test_b6_a_tickets_stored_limit_and_collar_are_informational_the_approval_computes_fresh_ones(tmp_path):
    from test_order_step import approve, propose
    w = leg_world(tmp_path / "w", fill=1.0)
    w.paper = paper({"a": {"SPY": 2.5}})
    p = propose(w, fractional=True)
    lines = {ln["leg"]: ln for ln in p["ticket"]["orders"]}
    assert lines["whole"]["limit"] == 505.0 and lines["remainder"]["limit"] == 505.0        # the ticket's own (the 500 quote)
    w.later(1)
    w.prices["SPY"] = 502.0                                                                 # within the 0.5% band
    out = approve(w, p["ticket_number"], fractional=True)
    assert out["status"] == "done" and out["orders_sent"] == 2, out
    o = {x["tag"].rsplit("-", 1)[-1]: x for x in w.orders() if x["batch"] == out["batch"]}
    assert o["SPY"]["limit_price"] == osx.limit_price(2.0, 502.0) == 507.02 != lines["whole"]["limit"]
    assert o["SPY.r"]["collar_price"] == osx.limit_price(0.5, 502.0) == 507.02 != lines["remainder"]["limit"]
    assert o["SPY.r"]["reference_price"] == 502.0


def test_b3_a_live_run_that_is_not_the_shared_book_holds_its_remainders_though_consent_and_proof_are_there(mkl):
    lw = mkl(conf={CONSENT: True})                        # no owner's entry: the book is not opened, the state is not latched
    practice_record(lw)
    lw.paper = paper({"a": {"SPY": 125.0}})
    out = lw.run(**lw.live_kw({"propose": True, "capital": 5000.0, "fractional": True}))
    assert out["status"] == "done" and out["live_fractions"]["open"] is False, out
    assert "shared" in out["live_fractions"]["why"] and out["live_fractions"]["consent"] is True
    assert all("leg" not in ln for ln in out["ticket"]["orders"]) and "residuals" not in out


def test_b3_even_a_forged_live_market_candidate_is_refused_by_the_order_step_when_the_opening_is_shut(mkl, monkeypatch):
    """A second line behind _differences: were a MARKET candidate to reach the send loop of a shut live run, it is refused
    there (never intended, never handed to the venue)."""
    lw = frac_world(mkl, consent=False)
    real = osx._Run._differences

    def forged(self, *a, **k):
        out = real(self, *a, **k)
        whole = out[0]
        return out + [{**whole, "units": 0.5, "value": 0.5 * whole["quote"], "leg": "remainder", "order_type": "MARKET",
                       "after_whole": False}]
    monkeypatch.setattr(osx._Run, "_differences", forged)
    out = lw.propose(fractional=True)
    assert [(r["leg"], r["by"]) for r in out["refused"]] == [("remainder", "order step")], out
    assert "opening is shut" in out["refused"][0]["reason"] and "faults" not in out
    assert all("leg" not in ln for ln in out["ticket"]["orders"])
    lw.later(1)
    lw.approve(out["ticket_number"], fractional=True)
    assert lw.venue.limitless() == []


# ---- T51 B repair F1: a SHUT opening changes nothing: the run is the same live run as without --fractional

import re as _re

_BATCH = _re.compile(r"os-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")


def _plain(x):
    """A value as one comparable text: batch ids made equal, the opening's words and the random ticket number left out."""
    drop = {"timestamp", "live_fractions", "ticket_number", "number_ends"}

    def clean(v):
        if isinstance(v, dict):
            return {k: clean(w) for k, w in v.items() if k not in drop}
        if isinstance(v, (list, tuple)):
            return [clean(w) for w in v]
        return v
    return _BATCH.sub("B", json.dumps(clean(x), sort_keys=True, default=str))


def _shut_pair(mkl, spec):
    """Two identical live worlds, one run with --fractional and one without, each given the same days; the opening is
    shut in both by `spec["shut"]`. Returns the two lists of (what a run returned, the journal's lines, the ledger's rows)."""
    seen = []
    for flag in (True, False):
        consent, proof = {"consent": (False, (True, True)), "absent": ("absent", (True, True)),
                          "no proof": (True, (False, False)), "purchase only": (True, (True, False)),
                          "sale only": (True, (False, True))}[spec["shut"]]
        conf = None if consent == "absent" else {CONSENT: consent}
        lw = mkl(conf=conf)
        lw.open_book("10000")
        if any(proof):
            practice_record(lw, buy=proof[0], sell=proof[1])
        lw.prices["GLD"] = 40.0
        lw.paper = paper({"a": spec["prelude"]})
        out = lw.propose()                                   # the prelude: whole shares, the same in both
        lw.later(1)
        lw.approve(out["ticket_number"])
        lw.later(30)
        rows = []
        lw.paper = paper({"a": spec["wanted"]}, min_trade_pct=spec["min_trade_pct"])
        for step in range(2):
            p = lw.propose(fractional=flag)
            rows.append(p)
            lw.later(1)
            if p.get("ticket_number"):
                rows.append(lw.approve(p["ticket_number"], fractional=flag))
            lw.later(30)
        journal = [e for e in lw.journal_events() if e.get("event") in ("order", "left_over", "refused", "dropped", "fill",
                                                                          "summary", "unknown", "ticket", "no_quote")]
        led = lw.ledger()
        try:
            book = [(o["symbol"], o["units"], o["limit_price"], o["order_type"], o["state"]) for o in led.orders()]
        finally:
            led.close()
        seen.append((rows, journal, book))
    return seen


def test_f1_with_the_opening_shut_a_live_fractional_run_is_the_live_run_without_the_flag_over_seeded_worlds(mkl):
    differing = 0
    for k in range(24):
        rng = random.Random(63_000 + k)
        spec = {"shut": rng.choice(["consent", "no proof", "purchase only", "sale only", "absent"]),
                "prelude": {"SPY": 10.0, "GLD": 10.0, "QQQ": rng.choice([0.0, 20.0])},
                "wanted": {"SPY": rng.choice([0.08, 0.04, 5.0, 10.0, 15.0, 27.0, 0.0]),
                           "GLD": rng.choice([25.0, 15.0, 10.0, 37.0]), "QQQ": rng.choice([0.0, 20.0, 31.0, 5.0])},
                "min_trade_pct": rng.choice([0.0, 0.005])}
        spec["prelude"] = {s: u for s, u in spec["prelude"].items() if u}
        flagged, plain = _shut_pair(mkl, spec)
        for a, b in zip(flagged[0], plain[0]):
            assert a["live_fractions"]["open"] is False and "live_fractions" not in b, (k, spec)
            assert "residuals" not in a, (k, spec, a.get("residuals"))
        assert _plain(flagged[0]) == _plain(plain[0]), (k, spec)       # what the runs returned
        assert _plain([e for e in flagged[1]]) == _plain([e for e in plain[1]]), (k, spec)   # the journal's lines
        assert flagged[2] == plain[2], (k, spec)                       # the ledger's orders
        differing += 1
    assert differing == 24


def test_f1_the_repro_cases_one_share_held_and_a_target_under_the_minimum_or_a_difference_of_one_and_a_half(mkl):
    for wanted, prelude, mtp, _ in (({"SPY": 0.08}, {"SPY": 10.0}, 0.0, "SPY"), ({"SPY": 0.04}, {"SPY": 10.0}, 0.0, "SPY"),
                                      ({"GLD": 25.0}, {"GLD": 10.0}, 0.005, "GLD")):
        flagged, plain = _shut_pair(mkl, {"shut": "no proof", "prelude": prelude, "wanted": wanted, "min_trade_pct": mtp})
        assert _plain(flagged[0]) == _plain(plain[0]) and flagged[2] == plain[2], (wanted, mtp)
        assert plain[0][0].get("ticket", {}).get("orders", []) == [], (wanted, plain[0][0])   # without the flag: no order


# ---- T51 B repair F6: "asked in this very second" is judged on whole clock seconds, not on float equality

def test_f6_a_fraction_of_a_second_since_the_last_ask_is_the_same_second_and_no_re_ask(tmp_path, monkeypatch):
    w = collar_world(tmp_path / "w")
    w.clock.t += 0.2
    w.quote_age["SPY"] = 130.0                                         # old at every ask: the collar must hold it back
    age_the_market_leg_after_its_checks(w, monkeypatch, 0.3)           # the clock moves 0.3 s while the leg is judged
    asks = []
    real = w.quotes
    w.quotes = lambda symbols: (asks.append(list(symbols)), real(symbols))[1]
    out = w.run(fractional=True)
    (left,) = held_back(out)
    assert left["collar"] == "quote_age" and asks == [["SPY"], ["SPY"]], (asks, left)


def test_f6_a_second_or_more_since_the_last_ask_and_a_quote_past_120_seconds_is_asked_again(tmp_path, monkeypatch):
    w = collar_world(tmp_path / "w")
    w.clock.t += 0.2                                                   # asked at x.2; the quote is 60 s old
    age_the_market_leg_after_its_checks(w, monkeypatch, 61.0)          # 61 s later (another second): 121 s old
    asks = []
    real = w.quotes
    w.quotes = lambda symbols: (asks.append(list(symbols)), real(symbols))[1]
    out = w.run(fractional=True)
    assert out["status"] == "done" and asks == [["SPY"], ["SPY"]], (asks, out)


# ---- T51 B repair F4: a leg put back for its re-ask is not counted twice against max_capital

def test_f4_a_live_buy_judged_again_after_its_re_ask_counts_once_against_max_capital(mkl, monkeypatch):
    """The book holds 19 SPY (9,500 at its marks) under a max_capital of 10,000. A remainder purchase of 0.5 (252.50 at its
    collar price) fits once (9,752.50) and not twice (10,005.00). A minute passes while the leg is judged, so it is asked
    for again and judged again: the buy of its first judging must be taken back, or the second judging refuses it."""
    lw = frac_world(mkl, target=19.0, conf={CONSENT: True, "max_capital": 10_000, "max_order_value": 10_000})
    out = lw.propose(fractional=True)
    lw.later(1)
    assert lw.approve(out["ticket_number"], fractional=True)["orders_sent"] == 1          # 19 SPY, whole shares
    lw.later(30)
    lw.paper = paper({"a": {"SPY": 195.0}})                                               # 19.5: a remainder of 0.5
    out = lw.propose(fractional=True)
    assert [(ln["units"], ln["leg"]) for ln in out["ticket"]["orders"]] == [(0.5, "remainder")], out
    lw.later(1)
    age_the_market_leg_after_its_checks(lw, monkeypatch, 61.0)           # a minute passes while the 0.5 leg is judged
    before = len(lw.quote_calls)
    done = lw.approve(out["ticket_number"], fractional=True)
    assert [c for c in lw.quote_calls[before:] if c == ["SPY"]] == [["SPY"], ["SPY"]], "the leg was not asked for again"
    assert done["status"] == "done" and done["orders_sent"] == 1 and done["dropped"] == [], done
    assert [s["fractional_live"] for s in lw.venue.limitless()] == [True]


# ---- T51 B repair2 (1): R-F1 holds for EVERY live run outcome, stops included

STOPS = ("lapsed confirmation", "absent confirmation", "no checklist", "limits file elsewhere", "no ticket mode",
         "capital given", "wrong approval number", "propose and approve", "venue not production")


def _stop_run(lw, how, flag):
    """One live run that is bound to stop, made the same way with and without --fractional."""
    kw = {"fractional": flag}
    if how == "lapsed confirmation":
        lw.write_conf(valid_until="2020-01-01")
    elif how == "absent confirmation":
        (lw.cfg / "webull_live.json").unlink()
    elif how == "no checklist":
        (lw.cfg / "go_live.json").unlink()
    elif how == "limits file elsewhere":
        other = lw.tmp / "elsewhere.json"
        other.write_text((lw.cfg / "risk.json").read_text())
        other.chmod(0o644)
        lw.limits = other
    elif how == "venue not production":
        lw.venue.environment = "sandbox"
    if how == "no ticket mode":
        return lw.lshared(**kw)
    if how == "capital given":
        return lw.lshared(propose=True, capital=5000.0, **kw)
    if how == "wrong approval number":
        return lw.approve("000000", **kw)
    if how == "propose and approve":
        return lw.lshared(propose=True, approve="123456", **kw)
    return lw.propose(**kw)


def _everything(lw, out):
    """What a run left: what it returned, EVERY journal line (any event type), the ledger's orders and fills, the venue's calls."""
    led = lw.ledger()
    try:
        ledger = {"orders": led.orders(), "fills": led.fills()}
    finally:
        led.close()
    return _plain({"out": out, "journal": lw.journal_events(), "ledger": ledger, "calls": [list(c) for c in lw.venue.calls]})


def test_r2_1_every_live_stop_with_the_flag_is_the_live_stop_without_it_and_no_fractional_key_leaks(mkl):
    stopped = 0
    for how in STOPS:
        got = []
        for flag in (True, False):
            # every lock would hold if the run got as far as judging them; only the run that DOES get that far (a wrong
            # approval number stops after it) has its consent withheld, because an open opening is a fractional run
            lw = mkl(conf={CONSENT: how != "wrong approval number"})
            lw.open_book("10000")
            practice_record(lw)
            lw.paper = paper({"a": {"SPY": 125.0}})
            out = _stop_run(lw, how, flag)
            assert out["status"] == "stopped", (how, flag, out)
            for key in ("residuals",):
                assert key not in out, (how, flag, key)
            for e in lw.journal_events("summary"):
                assert "residuals" not in e or e["batch"] != out["batch"], (how, "a journaled summary holds `residuals`", e)
            got.append((_everything(lw, out), out))
            stopped += 1
        assert got[0][0] == got[1][0], how
    assert stopped == 2 * len(STOPS)


# ---- T51 B repair2 (2): the re-judge after a re-ask does not ask again

def test_r2_2_a_newer_quote_still_past_120_seconds_is_not_asked_for_a_third_time(tmp_path, monkeypatch):
    w = collar_world(tmp_path / "w")                                   # the first quote is 60 s old: time T0 - 60
    t0 = w.clock.t
    age_the_market_leg_after_its_checks(w, monkeypatch, 61.0)          # 121 s old at the collar
    asks = []
    real = w.quotes

    def quotes(symbols):
        asks.append(list(symbols))
        if len(asks) > 1:                                              # newer than the first (T0 - 59.5), and 120.5 s old then
            return {s: (500.0, t0 - 59.5) for s in symbols}
        return real(symbols)
    w.quotes = quotes
    out = w.run(fractional=True)
    b = out["batch"]
    assert out["status"] == "done" and "faults" not in out and "cap_unjudged" not in out, out
    assert [a["tag"] for a in w.venue.args] == [f"{b}-SPY"], out        # its LIMIT leg went
    (left,) = held_back(out)
    assert left["collar"] == "quote_age", left
    assert asks == [["SPY"], ["SPY"]], asks                            # the run's first ask and ONE once more, no third
    led = w.ledger()
    try:
        assert led.attention() == []
    finally:
        led.close()
