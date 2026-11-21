"""T51c S4c (addendum 7, R24 to R28, R30, R31): the shared-account book wired into a practice run (`shared=True`, the
command line's `--shared`).

What a shared practice run is, and what these tests hold it to:
- every limit is judged on HER book alone (R31): the position limit, the count of positions, the day's loss and the
  drawdown on the value per unit (R27, the peak in Decimal from `book_nav`, never SQL text), the capital the book's own
  value (R26); the old `equity` table is neither read nor written;
- a shortfall (the account holds less than the book counts as its own) stops the run at stage `shortfall`, a skipped
  session for the stake ladder, with its words in the stop and the summary, and an alert (R24); a shortfall of a symbol
  this run's own orders touched, of at most their units, is set aside like a position difference; it is cleared by the
  shares coming back or by the owner's `units_out` or `split` entry;
- the state file latches (R25), `--capital` is refused before the lock and a ticket binds the book's value (R26),
  a held fund far from its last mark stops the run before any order (R28), the practice baseline is surplus and is
  never sold or priced (R30).
Everything runs on stand-in venues, quotes and clocks in a temporary folder; nothing opens a socket."""
import contextlib
import json
import random
import re
import socket
import sqlite3
from decimal import Decimal as D

import pytest

import test_order_step as T
from valedia_trader.core import alerts
from valedia_trader.execution_and_risk import order_step as osx
from valedia_trader.execution_and_risk import stake_ladder
from valedia_trader.research_loop import cli


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise OSError("the T51c S4c tests open no socket")
    for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "create_connection"),
                        (socket, "getaddrinfo")):
        monkeypatch.setattr(owner, name, refuse)


class Lag(T.Booked):
    """Its positions read gives, for the symbols in `lag`, the stated units instead of what it holds; `lag_after_submit`
    switches `lag` on at the first submit (the venue's positions not yet caught up with an order it just took)."""
    lag: dict = {}
    lag_after_submit: dict = {}
    lies: bool = False                                  # order_status says open and nothing filled (a sale seen late)
    positions_fail_from: int | None = None              # the n-th positions read and every later one raise

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.lag, self.lag_after_submit, self.reads = {}, {}, 0

    def submit(self, *a, **k):
        out = super().submit(*a, **k)
        if self.lag_after_submit:
            self.lag = dict(self.lag_after_submit)
        return out

    def order_status(self, tag):
        out = super().order_status(tag)
        if out is not None and self.lies and self.book[tag]["units"] < 0:       # a sale, seen late
            out.update(state="open", filled_units=0.0, avg_price=None)
        return out

    def positions(self):
        self.reads += 1
        if self.positions_fail_from is not None and self.reads >= self.positions_fail_from:
            raise ConnectionError("positions are down")
        out = super().positions()
        for sym, units in self.lag.items():
            out[sym] = units
        return {s: u for s, u in out.items() if abs(u) > 1e-12}


class NoFee(T.Booked):
    """A purchase's order and fills carry no fee (empty, not zero), as the practice account does (R22)."""

    def order_status(self, tag):
        out = super().order_status(tag)
        if out is not None and self.book[tag]["units"] > 0:
            out["fee"] = None
        return out


class SW(T.World):
    """The world of test_order_step, shared: the real owner command writes the entries (the order step's own quote
    stand-in, the stand-in clock), the ledger and state file are named as the command names them."""

    def __init__(self, tmp, monkeypatch, capsys, venue=None, **limits):
        super().__init__(tmp)
        self.venue = venue if venue is not None else T.Booked()
        self.ledger_path = osx.ledger_path_for(tmp / "memory", self.venue.name)
        self.state_path = osx.state_path_for(self.ledger_path, self.venue.name)
        self.capsys, self.tmp_root = capsys, tmp
        self.paper = T.paper({"a": {"SPY": 100.0}})          # 100 SPY at 500 = half of a book worth 100,000
        self.venue.fill_at_once = 1.0
        self.set_limits(**limits)
        monkeypatch.setattr(osx, "wall_clock", self.clock)
        monkeypatch.setattr(osx, "minute_quotes", self.owner_quotes)

    def set_limits(self, **kw):
        self.limits.write_text(json.dumps({**T.GENEROUS, **kw}))

    def owner_quotes(self, symbols):                         # the order step's own quote source, stood in (not recorded)
        return {s: (self.prices[s], self.clock.t - 30.0) for s in symbols if s in self.prices}

    def owner(self, kind, *figures):
        """The owner's command: the preview, then the confirm a second later; the confirm's exit code must be 0."""
        base = ["--root", str(self.tmp_root), "order-step", "--venue", self.venue.name, "--owner-entry", kind, *figures,
                "--who", "owner", "--note", "test"]
        self.capsys.readouterr()
        assert cli.main(base) == 0
        (code,) = re.findall(r"^code: ([0-9]{6})$", self.capsys.readouterr().out, re.M)
        self.clock.t += 1.0
        assert cli.main([*base, "--confirm", code]) == 0, self.capsys.readouterr().err
        self.capsys.readouterr()
        self.clock.t += 1.0

    def open_book(self, amount="100000"):
        self.owner("cash_in", "--amount", amount)

    def shared(self, **kw):
        return self.run(shared=True, **kw)

    def sql(self, text, args=(), path=None):
        con = sqlite3.connect(path or self.state_path)
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in con.execute(text, args)]
            con.commit()
            return rows
        finally:
            con.close()

    def nav(self):
        return self.sql("SELECT * FROM book_nav ORDER BY id")

    def entries(self):
        return osx._owner_entries_read_only(self.state_path)

    def seed_nav(self, day, vpu_low, vpu_high=None, units="100000", marks=None):
        """A book_nav row of an earlier day, as a run writes one (figures as exact decimal text)."""
        low = D(vpu_low) * D(units)
        high = None if vpu_high is None and vpu_low is None else D(vpu_high or vpu_low) * D(units)
        marks = json.dumps(marks or {}, sort_keys=True, separators=(",", ":"))
        self.sql("INSERT INTO book_nav (day, time, batch, units_outstanding, value_per_unit, value_low, value_high, "
                 "cash_bounded, marks, marks_hash, entry_hash) VALUES (?, ?, 'seeded', ?, ?, ?, ?, 0, ?, 'x', ?)",
                 (day, 1_000.0, units, str(vpu_low), str(low), str(high), marks, self.entries()[0]["hash"]))

    def blocked(self):
        led = self.ledger()
        try:
            return led.blocked()
        finally:
            led.close()


@pytest.fixture
def mk(tmp_path, monkeypatch, capsys):
    count = []

    def make(venue=None, **limits):
        count.append(1)
        return SW(tmp_path / f"w{len(count)}", monkeypatch, capsys, venue, **limits)
    return make


@pytest.fixture
def w(mk):
    return mk()


def sent_units(w):
    return {o["symbol"]: o["units"] for o in w.orders() if o["state"] not in ("refused", "intended")}


def refusals(out):
    return [(r["symbol"], r["by"], r["reason"]) for r in out["refused"]]


# ---- outcome 7: the stages are known to the ladder

NEW_STOPS = ("shortfall", "marks", "targets", "state", "reconcile", "ticket", "prices")


def test_the_shortfall_stage_is_a_skip_and_every_stage_a_shared_stop_uses_is_known_to_the_ladder():
    assert "shortfall" in stake_ladder.SKIP_STAGES and "shortfall" not in stake_ladder.FAULT_STAGES
    for stage in NEW_STOPS:
        assert stage in stake_ladder.SKIP_STAGES | stake_ladder.FAULT_STAGES, stage
    done, faulty, notes = stake_ladder._judge_summary({"status": "stopped", "stage": "shortfall"})
    assert not done and faulty == [] and notes, (faulty, notes)
    for stage in ("state", "reconcile"):                 # a fault stays a fault
        assert stake_ladder._judge_summary({"status": "stopped", "stage": stage})[1]


# ---- outcome 1 (R31): every limit is judged on HER book only

def test_the_position_limit_is_judged_on_the_books_units_never_on_the_owners_of_the_same_fund(mk):
    w = mk(max_position_pct=0.5)                         # 50,000 of a book worth 100,000: exactly the 100 SPY wanted
    w.venue.held = {"SPY": 7.0, "OWN": 3.0}              # the baseline: the owner's 7 SPY (7 more would make 53,500)
    w.open_book()
    out = w.shared()
    assert out["status"] == "done" and out["refused"] == [] and sent_units(w) == {"SPY": 100.0}, out


def test_the_count_of_positions_is_the_books_not_the_accounts(mk):
    w = mk(max_open_positions=1)
    w.venue.held = {"OWN": 3.0, "TLT": 2.0, "GLD": 1.0, "IWM": 4.0, "QQQ": 5.0}    # five of the owner's
    w.open_book()
    out = w.shared()
    assert out["status"] == "done" and out["refused"] == [] and sent_units(w) == {"SPY": 100.0}, out
    other = mk(max_open_positions=1)                      # the same account, not shared: the baseline counts, refused
    other.venue.held = {"OWN": 3.0, "TLT": 2.0, "GLD": 1.0, "IWM": 4.0, "QQQ": 5.0}
    out = other.run()
    assert refusals(out) == [("SPY", "risk gate", "Too many open positions.")], out


def test_capital_is_the_books_own_value_not_the_paper_equity(mk):
    w = mk()
    w.open_book("40000")                                  # a book worth 40,000 against a paper book of 100,000
    out = w.shared()
    assert out["status"] == "done" and sent_units(w) == {"SPY": 40.0}, out
    assert out["shared"]["capital"] == pytest.approx(40_000.0), out["shared"]


# ---- outcome 4 (R26): --capital is refused in words before the lock

def test_capital_is_refused_in_shared_mode_before_the_lock(mk):
    w = mk()
    w.open_book()
    files = lambda: {p.name: p.read_bytes() for p in sorted((w.tmp / "journal").glob("*"))} | {w.state_path.name: w.state_path.read_bytes()}
    before = files()
    lock = osx._RunLock(str(w.state_path))
    assert lock.acquire()                                 # a run holds the lock: a refusal AFTER the lock would say so
    try:
        out = w.shared(capital=50_000.0)
    finally:
        lock.release()
    assert out["status"] == "stopped" and out["stage"] == "targets", out
    assert "--capital" in out["reason"] and "book" in out["reason"], out["reason"]
    assert w.venue.calls == [] and files() == before and out["stage"] in stake_ladder.SKIP_STAGES


# ---- outcome 1 (R27): the day's loss and the drawdown on the value per unit

def test_the_days_loss_is_on_the_value_per_unit_against_the_previous_days_upper_figure(mk):
    w = mk(max_daily_loss_pct=2.0)
    w.open_book()
    w.seed_nav("2026-11-12", "1.05")                      # yesterday the unit was worth 1.05; today the book is at 1.0
    out = w.shared()
    assert [r[1:] for r in refusals(out)] == [("risk gate", "Daily loss limit hit. Trading stopped.")], out
    assert out["shared"]["day_loss_pct"] == pytest.approx(100 * 0.05 / 1.05, rel=1e-9)
    assert out["orders_sent"] == 0


def test_the_drawdown_is_against_the_peak_of_every_earlier_upper_figure(mk):
    w = mk(max_drawdown_pct=8.0)
    w.open_book()
    w.seed_nav("2026-11-11", "1.2")
    w.seed_nav("2026-11-12", "1.0")                       # the day's loss is none (the previous day is 1.0); the peak 1.2
    out = w.shared()
    assert [r[1:] for r in refusals(out)] == [("risk gate", "Max drawdown hit. Trading stopped.")], out
    assert out["shared"]["drawdown_pct"] == pytest.approx(100 * 0.2 / 1.2, rel=1e-9)
    assert out["shared"]["day_loss_pct"] == 0.0


def test_the_owners_deposits_and_withdrawals_are_never_a_profit_or_a_loss(mk):
    w = mk(max_daily_loss_pct=2.0, max_drawdown_pct=8.0)
    w.open_book()
    w.seed_nav("2026-11-12", "1.0")
    w.owner("cash_out", "--amount", "50000")              # half of the book's money leaves: not a loss
    out = w.shared()
    assert out["status"] == "done" and out["refused"] == [] and sent_units(w) == {"SPY": 50.0}, out
    # the only fall is the purchase's own: 50 SPY bought at the limit 505 and marked at 500 (250 of 50,000)
    assert out["shared"]["day_loss_pct"] == pytest.approx(0.5) and D(out["shared"]["previous_value_per_unit"]) == 1


def test_the_peak_is_worked_out_in_decimal_not_as_text():
    rows = [{"day": "2026-11-11", "id": 1, "units_outstanding": "1", "value_low": "9.5", "value_high": "9.5"},
            {"day": "2026-11-12", "id": 2, "units_outstanding": "1", "value_low": "10.5", "value_high": "10.5"}]
    entries = [{"id": 1, "day": "2026-11-10", "value_per_unit_after": "1"}]
    loss = osx.book_loss(rows, entries, "2026-11-13", low=D("9.9"), high=D("9.9"))
    assert loss.peak == D("10.5")                         # as SQL text, "9.5" is above "10.5"
    assert loss.drawdown_pct.quantize(D("0.0000001")) == D("5.7142857") == loss.daily_loss_pct.quantize(D("0.0000001"))
    assert loss.previous == D("10.5") and not loss.upper_open


def test_the_previous_unit_value_is_the_latest_earlier_days_row_else_the_latest_earlier_entry_else_the_opening_one():
    entries = [{"id": 1, "day": "2026-11-09", "value_per_unit_after": "1"},
               {"id": 2, "day": "2026-11-12", "value_per_unit_after": "1.1"},
               {"id": 3, "day": "2026-11-13", "value_per_unit_after": "1.1"}]
    nav = [{"day": "2026-11-12", "id": 4, "units_outstanding": "10", "value_low": "11", "value_high": "12"},
           {"day": "2026-11-12", "id": 5, "units_outstanding": "10", "value_low": "11.5", "value_high": None},
           {"day": "2026-11-13", "id": 6, "units_outstanding": "10", "value_low": "20", "value_high": "20"}]
    one = osx.book_loss(nav, entries, "2026-11-13", low=D("1.1"), high=D("1.1"))
    assert one.previous == D("1.15") and one.upper_open and "2026-11-12" in one.previous_from   # its upper figure open
    two = osx.book_loss([], entries, "2026-11-13", low=D("1.1"), high=D("1.1"))
    assert two.previous == D("1.1") and "entry 2" in two.previous_from                          # the latest EARLIER entry
    three = osx.book_loss([], entries[:1] + [dict(entries[2])], "2026-11-09", low=D("1"), high=None)
    assert three.previous == D("1") and "opening" in three.previous_from                         # opened today: the 1


def test_the_old_equity_table_is_neither_read_nor_written_in_shared_mode(mk):
    w = mk(max_drawdown_pct=8.0)
    w.open_book()
    w.sql("INSERT INTO equity (day, time, equity) VALUES ('2026-11-12', 1.0, 1000000000.0)")   # a peak that would trip it
    out = w.shared()
    assert out["status"] == "done" and out["refused"] == [] and out["orders_sent"] == 1, out
    assert w.sql("SELECT COUNT(*) AS n FROM equity") == [{"n": 1}]                                # written by nothing
    assert len(w.nav()) == 1
    other = mk()                                          # and without the flag the table is written, as before
    other.run()
    assert other.sql("SELECT COUNT(*) AS n FROM equity") == [{"n": 1}] and other.nav() == []


def test_a_book_nav_row_is_written_at_the_first_look_and_repriced_at_each_later_one(mk):
    w = mk()
    w.open_book()
    w.paper = T.paper({"a": {"SPY": 100.0, "QQQ": 100.0}})
    out = w.shared()
    rows = w.nav()
    assert out["status"] == "done" and len(rows) == 1, rows
    row = rows[0]
    # the row is the run's last look: both purchases bought at their limits (505, 404), marked at 500 and 400
    assert row["day"] == "2026-11-13" and row["units_outstanding"] == "100000" and row["batch"] == out["batch"]
    assert D(row["value_low"]) == D(row["value_high"]) == 99_100 and D(row["value_per_unit"]) == D("0.991")
    marks = json.loads(row["marks"])
    assert set(marks) == {"QQQ", "SPY"} and D(marks["SPY"]) == 500 and row["cash_bounded"] == 0
    assert row["entry_hash"] == w.entries()[-1]["hash"] and len(row["marks_hash"]) == 64
    assert D(out["shared"]["value_per_unit"]) == D("0.991")


# ---- outcome 3 (R25): the state file latches

def test_a_state_file_holding_an_owners_entry_refuses_a_run_that_is_not_shared(mk):
    w = mk()
    w.open_book()
    out = w.run()
    assert out["status"] == "stopped" and out["stage"] == "state" and "--shared" in out["reason"], out
    assert w.venue.calls == [] and out["orders_sent"] == 0
    assert w.sql("SELECT COUNT(*) AS n FROM equity") == [{"n": 0}]


def test_a_shared_run_without_an_opening_entry_stops_at_targets_a_skip(mk):
    w = mk()
    out = w.shared()
    assert out["status"] == "stopped" and out["stage"] == "targets" and "opening entry" in out["reason"], out
    assert w.venue.calls == [] and out["stage"] in stake_ladder.SKIP_STAGES


def test_a_state_file_that_differs_from_the_journal_on_the_owners_entries_stops_the_run_at_state(mk):
    w = mk()
    w.open_book()
    for path in (w.tmp / "journal").glob("*.jsonl"):
        path.write_text("")                               # the witness is gone
    out = w.shared()
    assert out["status"] == "stopped" and out["stage"] == "state", out
    assert "owner entry 1" in out["reason"] and "a person must look" in out["reason"]
    assert w.venue.calls == [], w.venue.calls             # before anything is read from the account


def test_a_tag_the_journal_or_the_send_record_holds_and_the_ledger_does_not_stops_the_run_at_reconcile(mk):
    w = mk()
    w.open_book()
    w.sql("INSERT INTO sends (tag, day, time, outcome) VALUES ('os-x-SPY', '2026-11-13', 1.0, 'sent')")
    out = w.shared()
    assert out["status"] == "stopped" and out["stage"] == "reconcile" and "os-x-SPY" in out["reason"], out
    assert w.venue.calls == []


def test_a_ledger_put_back_from_an_older_copy_stops_before_any_account_read(mk):
    w = mk()
    w.open_book()
    w.ledger().close()
    old = w.ledger_path.read_bytes()
    assert w.shared()["status"] == "done"
    w.later()
    w.ledger_path.write_bytes(old)                        # the ledger as it was before the first run's orders
    w.venue.calls.clear()
    out = w.shared()
    assert out["status"] == "stopped" and out["stage"] == "reconcile", out
    assert w.venue.calls == [] and out["orders_sent"] == 0


# ---- outcome 2 (R24): a shortfall is a hold with its own stage

def test_a_shortfall_stops_the_run_at_its_own_stage_with_its_words_an_alert_and_no_fault(mk):
    w = mk()
    w.open_book()
    assert w.shared()["status"] == "done" and w.book() == {"SPY": 100.0}
    w.venue.held["SPY"] -= 30.0                           # the owner sells 30 of the SPY the book counts as its own
    w.later()
    w.venue.calls.clear()
    out = w.shared()
    assert out["status"] == "stopped" and out["stage"] == "shortfall", out
    for words in ("SPY", "70.0", "100.0", "the owner sold shares the book counts as its own", "a split"):
        assert words in out["reason"], (words, out["reason"])
    assert "submit" not in w.venue.names() and out["orders_sent"] == 0 and "faults" not in out
    assert out["shared"]["shortfalls"]["SPY"]["short"] == 30.0
    assert w.blocked()                                    # the ledger is blocked: the alert's own source
    done, faulty, notes = stake_ladder._judge_summary(out)
    assert not done and faulty == [] and notes           # a skipped session for the ladder, never a fault
    env = alerts.Env(root=w.tmp_root, now=w.clock.t, halt_file=None, logs_dir=w.tmp / "logs",
                     launch_agents_dir=w.tmp / "la", launchctl=lambda: "", free_bytes=lambda p: 10 ** 12,
                     sessions=lambda: None)
    keys = {a.key for a in alerts.check_order_step(env).alerts} | {a.key for a in alerts.check_orders(env).alerts}
    assert any(k.startswith("step-stopped:") for k in keys) and any(k.startswith("orders:") for k in keys), keys


def test_a_shortfall_clears_when_the_shares_come_back(mk):
    w = mk()
    w.open_book()
    w.shared()
    w.venue.held["SPY"] -= 30.0
    w.later()
    assert w.shared()["stage"] == "shortfall"
    w.later()
    assert w.shared()["stage"] == "shortfall"             # it stays until something clears it
    w.venue.held["SPY"] += 30.0
    w.later()
    out = w.shared()
    assert out["status"] == "done" and "shortfalls" not in out["shared"], out


def test_a_shortfall_clears_by_the_owners_units_out_entry(mk):
    w = mk()
    w.open_book()
    w.shared()
    w.venue.held["SPY"] -= 30.0
    w.later()
    assert w.shared()["stage"] == "shortfall"
    before = w.fills()
    w.owner("units_out", "--symbol", "SPY", "--units", "30")
    w.later()
    out = w.shared()
    assert out["status"] == "done" and out["stage"] is None, out
    assert w.fills()[:len(before)] == before              # the ledger's own fills are not touched
    led = w.ledger()
    try:
        assert led.last_reconcile()["clean"] is True
    finally:
        led.close()


def test_a_shortfall_clears_by_the_owners_split_entry(mk):
    w = mk()
    w.open_book()
    w.shared()
    w.venue.held["SPY"] = 50.0                            # a 1-for-2 reverse split: the account holds half the shares
    w.prices["SPY"] = 1000.0
    w.later()
    out = w.shared()
    assert out["status"] == "stopped" and out["stage"] == "shortfall" and "SPY" in out["reason"], out
    w.owner("split", "--symbol", "SPY", "--ratio", "0.5")
    w.later()
    out = w.shared()
    assert out["status"] == "done" and out["stage"] is None and "shortfalls" not in out["shared"], out
    assert osx.book_figures(w.fills(), w.orders(), w.entries(), {"SPY": 1000.0}, "2026-11-13",
                            now=w.clock.t).units["SPY"] >= 50


def test_a_purchase_filled_in_the_run_whose_position_the_venue_reports_late_is_set_aside_not_a_fault(mk):
    w = mk(Lag())
    w.open_book()
    w.venue.lag_after_submit = {"SPY": 0.0}               # filled at once; the positions read has not caught up
    out = w.shared()
    assert out["status"] == "done" and out["orders_sent"] == 1 and "faults" not in out, out
    assert out["reconcile_pending"] == {"symbols": ["SPY"], "positions_unread": False, "status_unread": 0}, out
    w.venue.lag, w.venue.lag_after_submit = {}, {}        # caught up: the next run's reconcile settles it
    w.later()
    out = w.shared()
    assert out["status"] == "done" and "faults" not in out and "reconcile_pending" not in out, out


def test_a_sale_whose_holding_falls_before_its_status_says_filled_is_set_aside_not_a_fault(mk):
    w = mk(Lag())
    w.open_book()
    assert w.shared()["status"] == "done"
    w.paper = T.paper({"a": {"SPY": 0.0}})               # the paper book now wants none: sell the 100
    w.venue.lies = True                                   # the account's holding falls; the order still reads open
    w.later()
    out = w.shared()
    assert out["status"] == "done" and out["orders_sent"] == 1 and "faults" not in out, out
    assert out["reconcile_pending"]["symbols"] == ["SPY"], out


def test_a_shortfall_of_a_symbol_this_run_did_not_touch_is_no_fault_and_stops_the_next_run(mk):
    w = mk(Lag())
    w.open_book()
    w.paper = T.paper({"a": {"SPY": 100.0, "QQQ": 100.0}})
    w.shared()
    w.paper = T.paper({"a": {"SPY": 110.0, "QQQ": 100.0}})        # it buys 9 SPY (the book has 9,100 of cash left)
    w.venue.lag_after_submit = {"QQQ": 0.0}               # the owner sells the book's QQQ while the run is going
    w.later()
    out = w.shared()
    assert out["status"] == "done" and out["orders_sent"] == 1 and "faults" not in out, out
    assert set(out["shared"]["shortfalls"]) == {"QQQ"} and "reconcile_pending" not in out, out
    w.later()
    out = w.shared()
    assert out["status"] == "stopped" and out["stage"] == "shortfall" and "QQQ" in out["reason"], out


def test_a_shortfall_larger_than_this_runs_own_units_is_not_set_aside_and_is_no_fault(mk):
    w = mk(Lag())
    w.open_book()
    w.shared()
    w.paper = T.paper({"a": {"SPY": 110.0}})              # it buys 9 more
    w.venue.lag_after_submit = {"SPY": 50.0}              # the account then shows 50, 59 under the 109 the book counts
    w.later()
    out = w.shared()
    assert out["status"] == "done" and out["orders_sent"] == 1 and "faults" not in out, out
    assert out["shared"]["shortfalls"]["SPY"]["short"] == pytest.approx(59.0) and "reconcile_pending" not in out, out


def test_a_shortfall_is_never_classed_as_a_venue_that_could_not_be_read(mk):
    w = mk(Lag())
    w.open_book()
    w.shared()
    w.venue.held["SPY"] -= 30.0
    w.later()
    w.venue.reads, w.venue.positions_fail_from = 0, 2      # the first reconcile reads (a shortfall), the second cannot
    w.venue.calls.clear()
    out = w.shared()
    assert out["status"] == "stopped" and out["stage"] == "shortfall" and "SPY" in out["reason"], out
    assert "submit" not in w.venue.names()


# ---- outcome 4 (R26): a ticket stores the book's value; an approval recomputes it after the prices

def test_a_ticket_stores_the_value_it_was_proposed_at_and_an_approval_within_the_band_goes_through(mk):
    w = mk()
    w.open_book()
    out = w.shared(propose=True)
    number = out["ticket_number"]
    (row,) = w.sql("SELECT * FROM tickets", path=osx.ticket_path_for(w.state_path))
    assert row["capital"] == pytest.approx(100_000.0) and out["orders_sent"] == 0, row
    w.owner("cash_in", "--amount", "400")                  # 0.4% more: inside TICKET_QUOTE_MOVE
    w.later(1)
    out = w.shared(approve=number)
    assert out["status"] == "done" and out["orders_sent"] == 1, out


def test_an_approval_refuses_a_book_value_that_moved_beyond_the_band_after_the_prices(mk):
    w = mk()
    w.open_book()
    number = w.shared(propose=True)["ticket_number"]
    w.owner("cash_in", "--amount", "1000")                 # 1% more than the ticket's value
    w.later(1)
    w.venue.calls.clear()
    out = w.shared(approve=number)
    assert out["status"] == "stopped" and out["stage"] == "ticket" and "book value" in out["reason"], out
    assert "100,000.00" in out["reason"] and "101,000.00" in out["reason"], out["reason"]
    assert out["orders_sent"] == 0 and "submit" not in w.venue.names()
    assert any(c[0] == "quotes" for c in w.venue.calls), "the value is recomputed AFTER the prices"
    assert out["stage"] in stake_ladder.SKIP_STAGES


def test_a_ticket_made_without_the_flag_is_refused_by_a_shared_approval_and_the_reverse(mk):
    w = mk()
    number = w.run(propose=True)["ticket_number"]          # an ordinary (not shared) ticket, before the book was opened
    w.open_book()
    out = w.shared(approve=number)
    assert out["status"] == "stopped" and out["stage"] == "ticket" and "shared" in out["reason"], out
    shared = w.shared(propose=True)["ticket_number"]
    assert w.run(approve=shared)["stage"] == "state"       # a run that is not shared is stopped at the latch first


# ---- outcome 5 (R28): a held fund far from its last mark

def test_a_held_fund_far_from_its_last_mark_stops_the_run_at_marks_before_any_order(mk):
    w = mk(max_drawdown_pct=8.0)
    w.open_book()
    assert w.shared()["status"] == "done"
    w.prices["SPY"] = 700.0                                # +40% overnight: a split, a rename or a bad quote
    w.later()
    w.venue.calls.clear()
    out = w.shared()
    assert out["status"] == "stopped" and out["stage"] == "marks", out
    for words in ("SPY", "700", "500", "split"):
        assert words in out["reason"], (words, out["reason"])
    assert out["orders_sent"] == 0 and "submit" not in w.venue.names()
    assert out["stage"] in stake_ladder.SKIP_STAGES and len(w.nav()) == 1, "no row is written by a stopped look"


def test_a_split_entry_lets_the_run_pass_the_marks_check(mk):
    w = mk(max_drawdown_pct=8.0)
    w.open_book()
    w.shared()
    w.prices["SPY"] = 250.0                                # a real 2-for-1: the quote halves, the account doubles
    w.venue.held["SPY"] = 200.0
    w.later()
    assert w.shared()["stage"] == "marks"
    w.owner("split", "--symbol", "SPY", "--ratio", "2")
    w.later()
    out = w.shared()
    assert out["status"] == "done" and out["stage"] is None, out


def test_far_quotes_judges_each_held_fund_against_its_marks_split_adjusted():
    row = {"time": 100.0, "marks": {"SPY": "500", "QQQ": "400", "GONE": "10"}}
    splits = [{"kind": "split", "symbol": "SPY", "ratio": "2", "time": 150.0},
              {"kind": "split", "symbol": "QQQ", "ratio": "2", "time": 50.0}]            # before the row: already in it
    quotes = {"SPY": 250.0, "QQQ": 200.0, "NEW": 5.0}
    far = osx.far_quotes(row, splits, quotes, ["SPY", "QQQ", "NEW"], 8.0)
    assert [f["symbol"] for f in far] == ["QQQ"], far     # SPY: 500 / 2 = 250 passes; QQQ halved with no split after
    assert osx.far_quotes(None, [], quotes, ["SPY"], 8.0) == []                           # no earlier row: no mark to judge
    assert osx.far_quotes(row, [], {"SPY": 540.0}, ["SPY"], 8.0) == []                    # exactly 8% is not beyond
    assert [f["symbol"] for f in osx.far_quotes(row, [], {"SPY": 540.1}, ["SPY"], 8.0)] == ["SPY"]


# ---- outcome 6 (R30): the practice book's baseline

def test_the_baseline_is_surplus_never_sold_and_never_priced_in_the_first_shared_run(mk):
    w = mk()
    w.venue.held = {"OWN": 3.0, "GLD": 7.0}                # the practice account's baseline (the owner's)
    assert w.run()["status"] == "done"                     # an ordinary run first: baseline recorded, SPY bought
    book = w.book()
    w.paper = T.paper({"a": {"SPY": 100.0}})
    w.venue.cash_value = 1_000_000.0
    w.owner("cash_in", "--amount", "100000")               # a practice figure, written by the lead through the command
    w.later()
    w.quote_calls.clear()
    out = w.shared()
    assert out["status"] == "done" and out["orders_sent"] == 0 and "faults" not in out, out
    assert out["shared"]["owner_surplus"] == ["GLD", "OWN"], out["shared"]
    assert w.quote_calls and all(q == ["SPY"] for q in w.quote_calls), w.quote_calls     # a baseline fund is never priced
    assert w.book() == book and w.baseline() == {"OWN": 3.0, "GLD": 7.0}                 # and never sold


def test_a_target_in_a_baseline_fund_is_bought_for_the_book_alone(mk):
    w = mk()
    w.venue.held = {"SPY": 7.0}
    w.open_book()
    out = w.shared()
    assert out["status"] == "done" and sent_units(w) == {"SPY": 100.0}, out
    assert w.venue.held["SPY"] == 107.0 and w.book() == {"SPY": 100.0}
    assert out["shared"]["owner_surplus"] == ["SPY"]


# ---- the fee rule, wired (R22): a purchase the venue reports no fee for

def test_a_purchase_without_a_reported_fee_is_bounded_and_counted_once_a_run_never_a_fault(mk):
    w = mk(NoFee(), max_daily_loss_pct=50.0)
    w.open_book()
    assert w.shared()["status"] == "done"
    w.later()
    out = w.shared()
    assert out["status"] == "done" and "faults" not in out, out
    fees = out["shared"]["fees_bounded"]
    assert fees["count"] == 1 and len(fees["orders"]) == 1 and out["shared"]["cash_bounded"] is True, fees
    assert out["shared"]["fees_taken_as_none"] == 0
    assert out["shared"]["book_value"] < out["shared"]["book_value_high"]           # a bound, not a hole


# ---- outcome 1 (step 5): purchases are bounded by the book's own usable cash

def test_a_purchase_beyond_the_books_usable_cash_is_left_over_while_the_account_has_plenty(mk):
    w = mk()
    w.open_book()
    w.paper = T.paper({"a": {"SPY": 200.0}})               # 200 SPY at 500 is the whole book; with the margin, too much
    out = w.shared()
    assert out["status"] == "done" and out["orders_sent"] == 0 and "faults" not in out, out
    assert [x["symbol"] for x in out["left_over"]] == ["SPY"] and "book" in out["left_over"][0]["reason"], out


# ---- outcome 9 and the layouts: nothing else moves

def test_no_layout_changes_and_the_ordinary_run_is_the_ordinary_run(mk):
    w = mk()
    w.open_book()
    w.shared(propose=True)
    assert w.sql("PRAGMA user_version") == [{"user_version": 2}]
    assert w.sql("PRAGMA user_version", path=osx.ticket_path_for(w.state_path)) == [{"user_version": 3}]
    plain = mk()
    out = plain.run()
    assert out["status"] == "done" and "shared" not in out and plain.nav() == []


# ---- the properties of the study's section 7, over seeded stand-in venues and an owner acting between runs

def _owner_acts(rng, w):
    """One thing an owner does in his account between two runs, never cutting into the book's shares."""
    kind = rng.choice(("buy_other", "buy_fund", "sell_own", "deposit", "withdraw", "event", "nothing"))
    v = w.venue
    if kind == "buy_other":
        sym = rng.choice(("OWN", "GLD", "TLT"))
        n = float(rng.randint(1, 20))
        v.held[sym] = v.held.get(sym, 0.0) + n
        v.cash_value -= n * w.prices[sym]
    elif kind == "buy_fund":
        n = float(rng.randint(1, 30))
        v.held["SPY"] = v.held.get("SPY", 0.0) + n
        v.cash_value -= n * w.prices["SPY"]
    elif kind == "sell_own":
        mine = {s: u for s, u in v.held.items() if s in ("OWN", "GLD", "TLT") and u > 0}
        if mine:
            sym = rng.choice(sorted(mine))
            v.held[sym] -= mine[sym]
            v.cash_value += mine[sym] * w.prices[sym]
    elif kind == "deposit":
        v.cash_value += rng.randint(1, 50_000)
    elif kind == "withdraw":
        v.cash_value -= rng.randint(1, 50_000)
    elif kind == "event":
        v.held["EVT"] = v.held.get("EVT", 0.0) + rng.randint(1, 9)
    return kind


@pytest.mark.parametrize("seed", range(12))
def test_nothing_an_owner_does_between_runs_changes_the_books_figures_a_verdict_or_the_orders(mk, seed):
    amount = str(random.Random(seed).choice((50_000, 100_000, 250_000)))
    moves, acts = random.Random(seed), random.Random(seed + 1000)
    a, b = mk(), mk()                                     # the same book, the same prices; the owner acts in A only
    for w in (a, b):
        w.venue.held = {"OWN": 5.0}
        w.venue.cash_value = 2_000_000.0
        w.paper = T.paper({"a": {"SPY": 60.0, "QQQ": 40.0, "IWM": 100.0}})
        w.open_book(amount)
    for step in range(3):
        move = {s: 1 + moves.uniform(-0.01, 0.01) for s in ("SPY", "QQQ", "IWM")}
        for w in (a, b):
            w.later(20)
            for s, m in move.items():
                w.prices[s] = round(w.prices[s] * m, 2)
        if step:
            _owner_acts(acts, a)
        oa, ob = a.shared(), b.shared()
        assert oa["status"] == ob["status"] == "done" and oa["stage"] == ob["stage"] is None, (seed, step, oa, ob)
        assert oa.get("faults") == ob.get("faults")
        assert sorted((s["symbol"], s["units"]) for s in oa["sent"]) == sorted((s["symbol"], s["units"]) for s in ob["sent"])
        assert [r["reason"] for r in oa["refused"]] == [r["reason"] for r in ob["refused"]]
        for key in ("capital", "book_value", "value_per_unit", "day_loss_pct", "drawdown_pct"):
            assert oa["shared"][key] == ob["shared"][key], (seed, step, key, oa["shared"], ob["shared"])
    figures = lambda w: [(r["value_per_unit"], r["value_low"], r["units_outstanding"]) for r in w.nav()]
    assert figures(a) == figures(b) and len(a.nav()) == 3


def test_a_second_run_on_one_day_at_the_same_quotes_gives_the_same_figures_and_the_same_previous_row(mk):
    w = mk()
    w.open_book()
    w.seed_nav("2026-11-12", "1.001")
    one = w.shared()["shared"]
    w.later(1)
    two = w.shared()["shared"]
    for key in ("book_value", "value_per_unit", "day_loss_pct", "drawdown_pct", "previous_value_per_unit"):
        assert one[key] == two[key], (key, one, two)
    assert len(w.nav()) == 3


# ---- the command line's flag

def test_the_command_lines_shared_flag_reaches_the_run_and_is_refused_where_it_cannot_go(tmp_path, monkeypatch, capsys):
    import argparse
    from valedia_trader.research_loop import risk_gate
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "risk.json").write_text(json.dumps(T.GENEROUS))
    (tmp_path / "journal").mkdir()
    (tmp_path / "journal" / "paper_state.json").write_text(json.dumps({
        "starting_cash": 100_000.0, "cash": 98_000.0, "positions": {"a": {"SPY": {"units": 4.0, "cost": 500.0}}},
        "pending": [], "history": [{"day": "2026-11-12", "equity": 100_000.0, "capital_per_strategy": 100_000.0}],
        "peak_equity": 100_000.0, "last_day": "2026-11-12", "realized": {}, "panel_cases": []}))
    venue, now = T.Booked(), T.ny("2026-11-13 10:00")
    monkeypatch.setattr(cli, "_order_step_build", lambda: venue)
    monkeypatch.setattr(osx, "wall_clock", lambda: now)
    monkeypatch.setattr(osx, "minute_quotes", lambda symbols: {s: (500.0, now - 60) for s in symbols})
    monkeypatch.setattr(risk_gate, "HALT_FILE", tmp_path / "no-halt.json")
    assert cli.main(["--root", str(tmp_path), "order-step", "--shared", "--capital", "5000"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["stage"] == "targets" and "--capital" in out["reason"] and venue.calls == [], out
    assert cli.main(["--root", str(tmp_path), "order-step", "--shared"]) == 2          # no opening entry: a skip
    out = json.loads(capsys.readouterr().out)
    assert out["stage"] == "targets" and "opening entry" in out["reason"] and "shared" in out, out
    asked = argparse.Namespace(live=True, check=False, shared=True, fractional=False, capital=1.0, propose=True, approve=None)
    assert "--capital" in cli._live_args_refusal(asked)   # S4d: live takes --shared (and never a capital)
    assert cli.main(["--root", str(tmp_path), "order-step", "--owner-entry", "cash_in", "--venue", venue.name, "--amount",
                     "1", "--who", "o", "--note", "n", "--shared"]) == 2               # the owner's entry takes no run flag
    assert "--shared" in capsys.readouterr().err


def test_a_shortfall_beside_something_else_wrong_is_a_reconcile_stop_that_keeps_the_shortfalls_words(mk):
    w = mk()
    w.open_book()
    w.shared()
    w.paper = T.paper({"a": {"SPY": 100.0, "QQQ": 20.0}})
    w.venue.fill_at_once = 0.0                            # the QQQ order stays open at the venue ...
    w.later()
    assert w.shared()["status"] == "done"
    w.venue.cancel_answer = "not yet"                     # ... and its cancel does not take
    w.venue.held["SPY"] -= 30.0                           # ... while the owner sells 30 of the book's SPY
    w.later()
    w.venue.calls.clear()
    out = w.shared()
    assert out["status"] == "stopped" and out["stage"] == "reconcile", out
    for words in ("still open at the broker", "SPY", "70.0", "100.0", "the owner sold shares the book counts as its own"):
        assert words in out["reason"], (words, out["reason"])
    assert "submit" not in w.venue.names()


def test_an_approval_of_a_shared_ticket_is_not_judged_on_an_exact_capital(mk):
    w = mk()
    w.open_book("40000")                                  # a book of 40,000 against a paper equity of 100,000
    number = w.shared(propose=True)["ticket_number"]
    (row,) = w.sql("SELECT * FROM tickets", path=osx.ticket_path_for(w.state_path))
    assert row["capital"] == pytest.approx(40_000.0)
    w.later(1)
    out = w.shared(approve=number)
    assert out["status"] == "done" and out["orders_sent"] == 1 and sent_units(w) == {"SPY": 40.0}, out




# ---- the other options a practice run has

def test_a_shared_run_may_be_fractional_and_still_judges_the_book_alone(mk):
    w = mk(max_position_pct=1.0)
    w.venue.held = {"SPY": 7.0}                           # the owner's own shares: never part of the book's position
    w.open_book()
    w.paper = T.paper({"a": {"SPY": 100.4}})
    out = w.shared(fractional=True)
    assert out["status"] == "done" and out["refused"] == [] and "faults" not in out, out
    assert sorted(s["units"] for s in out["sent"]) == [0.4, 100.0], out["sent"]       # the whole leg and the remainder leg
    assert out["shared"]["owner_surplus"] == ["SPY"]


# ---- T51c S4e (R34): the owner's `mark` entry ("this quote is real; take it as the new mark")

def mark_command(w, *more, symbol="SPY"):
    args = ["--root", str(w.tmp_root), "order-step", "--venue", w.venue.name, "--owner-entry", "mark"]
    return [*args, *(["--symbol", symbol] if symbol else []), *more, "--who", "owner", "--note", "test"]


def tree_of(w, skip=()):
    return {str(p.relative_to(w.tmp)): p.read_bytes() for p in sorted(w.tmp.rglob("*"))
            if p.is_file() and not p.name.endswith(skip)}


def figures_of(w, marks=None, entries=None):
    return osx.book_figures(w.fills(), w.orders(), w.entries() if entries is None else entries,
                            marks or {"SPY": 500.0}, "2026-11-13", now=w.clock.t)


def test_a_mark_entry_is_printed_first_then_written_by_the_confirm_and_changes_no_cash_or_units(mk, capsys):
    w = mk(max_drawdown_pct=8.0)
    w.open_book()
    assert w.shared()["status"] == "done"
    w.later()
    base, before_fig, before = mark_command(w), figures_of(w), tree_of(w, (".owner-preview.json",))
    capsys.readouterr()
    assert cli.main(base) == 0
    out = capsys.readouterr().out
    (code,) = re.findall(r"^code: ([0-9]{6})$", out, re.M)
    assert "NOTHING was written" in out and "mark SPY: 500.0" in out and "new mark" in out, out
    assert "no cash and no units change" in out, out
    assert tree_of(w, (".owner-preview.json",)) == before          # a preview writes no byte of state, journal or ledger
    w.clock.t += 1.0
    assert cli.main([*base, "--confirm", code]) == 0, capsys.readouterr().err
    e = w.entries()[-1]
    assert e["kind"] == "mark" and e["symbol"] == "SPY" and e["mark"] == "500.0"
    assert (e["amount"], e["units"], e["ratio"]) == (None, None, None)
    assert e["units_before"] == e["units_after"] and e["value_per_unit_before"] == e["value_per_unit_after"]
    after = figures_of(w)
    for field in ("units", "cash_low", "cash_high", "usable_cash", "value", "value_high", "units_outstanding",
                  "value_per_unit"):
        assert getattr(after, field) == getattr(before_fig, field), field
    lines = [x for x in w.journal.read() if x.get("type") == "owner_entry"]
    assert lines[-1]["kind"] == "mark" and lines[-1]["hash"] == e["hash"]
    assert osx.owner_entry_differences(w.entries(), w.journal.read(), venue=w.venue.name) == []
    assert osx._checked_entries(sqlite3.connect(w.state_path))[-1]["hash"] == e["hash"]        # chained like the others


def test_a_mark_entry_takes_a_symbol_only_never_a_typed_price_and_a_fund_the_book_does_not_hold_is_refused(mk, capsys):
    w = mk()
    w.open_book()
    w.shared()
    w.later()
    before = tree_of(w)
    cases = [(mark_command(w, "--mark", "SPY=700"), "--mark is not taken"),
             (mark_command(w, "--amount", "5"), "takes no --amount"),
             (mark_command(w, "--units", "1"), "takes no --units"),
             (mark_command(w, "--ratio", "2"), "takes no --ratio"),
             (mark_command(w, symbol=None), "needs --symbol"),
             (mark_command(w, symbol="a b"), "plain symbol"),
             (mark_command(w, symbol="QQQ"), "the book holds no QQQ")]
    for argv, words in cases:
        capsys.readouterr()
        assert cli.main(argv) == 2, argv
        assert words in capsys.readouterr().err, (argv, words)
    assert tree_of(w) == before


def test_a_mark_entry_without_a_usable_quote_is_refused_and_writes_nothing(mk, capsys):
    w = mk()
    w.open_book()
    w.shared()
    w.later()
    before = tree_of(w)
    del w.prices["SPY"]
    capsys.readouterr()
    assert cli.main(mark_command(w)) == 2 and "SPY" in capsys.readouterr().err
    assert tree_of(w) == before


def test_a_mark_entry_clears_the_marks_stop_of_that_fund_on_the_next_run_and_only_that_fund(mk):
    w = mk(max_drawdown_pct=8.0)
    w.open_book()
    w.paper = T.paper({"a": {"SPY": 100.0, "QQQ": 100.0}})
    assert w.shared()["status"] == "done"
    w.prices["SPY"], w.prices["QQQ"] = 700.0, 560.0         # both +40% overnight: a rename, a bad quote, or a real move
    w.later()
    out = w.shared()
    assert out["stage"] == "marks" and "QQQ quotes" in out["reason"], out          # the first fund, in symbol order
    w.owner("mark", "--symbol", "QQQ")
    w.later()
    out = w.shared()
    assert out["stage"] == "marks" and "SPY quotes" in out["reason"] and "QQQ quotes" not in out["reason"], out
    assert out["orders_sent"] == 0 and len(w.nav()) == 1
    w.owner("mark", "--symbol", "SPY")
    w.later()
    out = w.shared()
    assert out["status"] == "done" and out["stage"] is None, out
    assert [e["kind"] for e in w.entries()][-2:] == ["mark", "mark"] and len(w.nav()) == 2


def test_the_confirmed_mark_is_the_reference_not_a_licence_a_quote_far_from_it_stops_the_run_again(mk):
    w = mk(max_drawdown_pct=8.0)
    w.open_book()
    w.shared()
    w.prices["SPY"] = 700.0
    w.later()
    assert w.shared()["stage"] == "marks"
    w.owner("mark", "--symbol", "SPY")                      # the owner took 700 as the new mark ...
    w.prices["SPY"] = 800.0                                 # ... and it moved 14% more before the next run
    w.later()
    out = w.shared()
    assert out["status"] == "stopped" and out["stage"] == "marks" and "700" in out["reason"], out
    assert "mark entries" in out["reason"], out["reason"]
    w.prices["SPY"] = 740.0                                 # within 8% of the owner's mark
    w.later()
    assert w.shared()["status"] == "done"


def test_far_quotes_takes_the_latest_mark_entry_after_the_row_as_the_reference_and_a_later_split_divides_it():
    row = {"time": 100.0, "marks": {"SPY": "500"}}
    mark = {"id": 2, "kind": "mark", "symbol": "SPY", "mark": "700", "time": 150.0}

    def far(entries, quote, limit=8.0):
        return [f["symbol"] for f in osx.far_quotes(row, entries, {"SPY": quote}, ["SPY"], limit)]
    assert far([mark], 700.0) == [] and far([mark], 755.9) == [] and far([mark], 756.1) == ["SPY"]
    assert far([{**mark, "time": 50.0}], 700.0) == ["SPY"]                   # written before the row: the row has it
    assert far([{**mark, "symbol": "QQQ"}], 700.0) == ["SPY"]                # another fund's mark clears nothing here
    assert far([mark, {**mark, "id": 3, "mark": "800", "time": 160.0}], 800.0) == []         # the latest mark wins
    assert far([mark, {**mark, "id": 3, "mark": "800", "time": 160.0}], 700.0) == ["SPY"]
    assert far([mark, {"id": 3, "kind": "split", "symbol": "SPY", "ratio": "2", "time": 160.0}], 350.0) == []
    assert far([{"id": 2, "kind": "split", "symbol": "SPY", "ratio": "2", "time": 120.0}, {**mark, "id": 3}], 700.0) == []


def test_a_mark_entry_changes_no_value_per_unit_the_day_stop_or_the_history_base_reads():
    """A mark entry is no stake change and no profit or loss: the fallbacks that read an owner entry's value per unit (the
    day stop's previous figure when no earlier row exists, the history check's base) skip it, so a mark taken at a
    LOWER value can never become the base that loosens them; the peak is read from the rows only."""
    def entry(i, day, vpu, kind="cash_in"):
        return {"id": i, "day": day, "kind": kind, "value_per_unit_after": vpu, "symbol": None if kind != "mark" else "SPY"}
    plain = [entry(1, "2026-11-10", "1"), entry(2, "2026-11-11", "1.2", "cash_in")]
    marked = [*plain, entry(3, "2026-11-12", "0.5", "mark")]            # a real fall, taken as the new mark
    low, high = D("1.1"), D("1.15")
    for rows in ([], [{"day": "2026-11-09", "id": 1, "units_outstanding": "10", "value_low": "9", "value_high": "9.5"}]):
        a = osx.book_loss(rows, plain, "2026-11-13", low=low, high=high)
        b = osx.book_loss(rows, marked, "2026-11-13", low=low, high=high)
        assert a == b, (a, b)
    assert osx.book_loss([], marked, "2026-11-13", low=low, high=high).previous == D("1.2")
    assert osx._entry_base(marked, "2026-11-13") == osx._entry_base(plain, "2026-11-13")
    assert osx._entry_base([plain[0], entry(2, "2026-11-12", "0.5", "mark")], "2026-11-13") == \
        osx._entry_base([plain[0]], "2026-11-13")
    rows = [{"day": "2026-11-12", "id": 1, "units_outstanding": "10", "value_per_unit": "1.05", "value_low": "10.5",
             "value_high": "10.5"}]
    looks = [{"day": "2026-11-12", "low": D("1.05"), "high": D("1.05"), "has_high": True}]
    assert osx._nav_history(rows, looks, "2026-11-13", marked) == osx._nav_history(rows, looks, "2026-11-13", plain)
    assert osx._nav_history([], [], "2026-11-13", marked) == osx._nav_history([], [], "2026-11-13", plain)


def test_a_mark_entry_in_a_run_leaves_the_previous_figure_the_peak_and_the_drawdown_as_the_rows_give_them(mk):
    def figures(with_mark):
        w = mk(max_drawdown_pct=50.0)
        w.open_book()
        w.shared()
        w.later()
        w.seed_nav("2026-11-12", "1.10", "1.12")
        if with_mark:
            w.owner("mark", "--symbol", "SPY")
        w.later()
        w.next_day()
        out = w.shared()
        assert out["status"] == "done", out
        s = out["shared"]
        return {k: s[k] for k in ("previous_value_per_unit", "previous_from", "day_loss_pct", "drawdown_pct")}
    assert figures(True) == figures(False)


def test_a_layout_2_file_made_before_the_mark_kind_is_rebuilt_in_place_by_its_first_mark_entry(tmp_path):
    path = tmp_path / "s.sqlite"
    old = [s.replace(", 'mark'", "") for s in osx._STATE_UPGRADE_1_TO_2]
    assert "'mark'" in osx._STATE_UPGRADE_1_TO_2[0] and "'mark'" not in old[0]
    con = sqlite3.connect(path)
    for statement in (*osx.STATE_SCHEMA, *old):
        con.execute(statement)
    con.execute("PRAGMA user_version = 2")
    con.commit()
    con.close()
    clock = iter(range(1_763_478_000, 1_763_479_000, 7))
    st = osx.OrderStepState(path, clock=lambda: float(next(clock)))
    opening = {"kind": "cash_in", "amount": "100", "units_before": "0", "units_after": "100",
               "value_per_unit_before": None, "value_per_unit_after": "1"}
    first = st.add_owner_entry(opening, who="o", note="n")
    fields = {"kind": "mark", "symbol": "SPY", "mark": "500.0", "units_before": "100", "units_after": "100",
              "value_per_unit_before": "1", "value_per_unit_after": "1"}
    raw = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError):                       # the old CHECK refuses the kind ...
        raw.execute("INSERT INTO owner_entries (time, day, kind, symbol, mark, units_before, units_after, who, note, "
                    "prev_hash, hash) VALUES (1, '2026-11-13', 'mark', 'SPY', '1', '1', '1', 'o', 'n', 'p', 'h')")
    raw.close()
    second = st.add_owner_entry(fields, who="o", note="n")             # ... until the first mark entry rebuilds the table
    third = st.add_owner_entry(opening | {"units_before": "100", "units_after": "200"}, who="o", note="n")
    assert [e["id"] for e in st.owner_entries()] == [1, 2, 3]          # the chain checks, ids 1 to 3, none missing
    assert st.owner_entries()[0]["hash"] == first["hash"] and st.owner_entries()[1]["hash"] == second["hash"]
    assert third["prev_hash"] == second["hash"]
    st.close()
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 2
    assert raw.execute("SELECT seq FROM sqlite_sequence WHERE name = 'owner_entries'").fetchone()[0] == 3
    assert "'mark'" in raw.execute("SELECT sql FROM sqlite_master WHERE name = 'owner_entries'").fetchone()[0]
    assert {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")} >= \
        {"owner_entries_no_update", "owner_entries_no_delete"}
    for statement in ("UPDATE owner_entries SET note = 'x'", "DELETE FROM owner_entries"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            raw.execute(statement)
    raw.close()


def test_a_broken_chain_is_found_before_the_rebuild_and_the_file_is_left_as_it_was(tmp_path):
    path = tmp_path / "s.sqlite"
    old = [s.replace(", 'mark'", "") for s in osx._STATE_UPGRADE_1_TO_2]
    con = sqlite3.connect(path)
    for statement in (*osx.STATE_SCHEMA, *old):
        con.execute(statement)
    con.execute("PRAGMA user_version = 2")
    con.commit()
    con.close()
    st = osx.OrderStepState(path, clock=lambda: 1_763_478_000.0)
    opening = {"kind": "cash_in", "amount": "100", "units_before": "0", "units_after": "100",
               "value_per_unit_before": None, "value_per_unit_after": "1"}
    st.add_owner_entry(opening, who="o", note="n")
    st.close()
    raw = sqlite3.connect(path)
    raw.execute("DROP TRIGGER owner_entries_no_update")
    raw.execute("UPDATE owner_entries SET amount = '999'")              # an entry changed after it was written
    raw.commit()
    before = raw.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    raw.close()
    st = osx.OrderStepState(path, clock=lambda: 1_763_478_100.0)
    with pytest.raises(osx.OrderStepError, match="was changed after it was written"):
        st.add_owner_entry({"kind": "mark", "symbol": "SPY", "mark": "500.0", "units_before": "100", "units_after": "100",
                            "value_per_unit_before": "1", "value_per_unit_after": "1"}, who="o", note="n")
    st.close()
    raw = sqlite3.connect(path)
    assert raw.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == before
    raw.close()


def test_the_owners_page_is_no_input_to_any_limit_nothing_that_judges_an_order_reads_it():
    """R31: the combined holding per fund is information on the page only; nothing in the order step, the risk gate or
    the stake ladder imports or names the page."""
    import inspect
    from valedia_trader.research_loop import risk_gate
    for module in (osx, stake_ladder, risk_gate):
        assert "owner_page" not in inspect.getsource(module), module.__name__
