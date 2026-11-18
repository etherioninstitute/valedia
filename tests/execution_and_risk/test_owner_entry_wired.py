"""T51c S4b (addendum 7, R29; R34 as built; R23): the owner's entry, wired.

- Marks are never typed: `--mark` is refused. Every fund the book holds units of (and the entry's own symbol for a
  units_out or a split) is asked of the order step's own quote source (stood in here) and read with the run's own
  reader and age rule; inside the session at the clock, outside it at the last session's close (its time shown). A
  book that holds no units asks for no quote.
- A preview (no --confirm) prints the figures and a code and writes no byte of the state file, the journal or the
  ledger; the confirm (`--confirm CODE`) asks the quotes again and writes only when every mark is within
  TICKET_QUOTE_MOVE and nothing else differs; a confirm that does not write spends the preview.
- The repair of an entry whose journal line could not be written (`--owner-entry-rejournal N --hash H`).
- A production venue is refused in every form before any quote is asked.
- Properties over seeded books: every refusal (enumerated from the code) and every preview leave the state file, the
  journal and the ledger byte-identical; a confirm writes one entry and one line or nothing; preview then confirm
  writes what the command of commit 832977f (taken from this checkout's history) writes with those marks typed.
Nothing here opens a socket, names a real folder or asks a real quote source: every root is the test's own."""
import ast
import contextlib
import importlib.util
import io
import json
import random
import re
import socket
import stat
import subprocess
import sys
import types
from decimal import Decimal as D
from pathlib import Path

import pandas as pd
import pytest

from valedia_trader.execution_and_risk import order_step as osx
from valedia_trader.execution_and_risk import orders as _orders
from valedia_trader.research_loop import cli
from valedia_trader.research_loop import paper as _paper
import test_order_step as T

BASE = "832977f"                                       # main before this slice (the dispatch names it)
VENUE = "webull-test-cash"
T0 = 1_763_478_000.0                                   # 2025-11-18 (a Tuesday) 10:00 in New York: inside the session
NY = "America/New_York"


def at(text: str) -> float:
    """A New York wall time as seconds."""
    return pd.Timestamp(text, tz=NY).timestamp()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise OSError("the T51c S4b tests open no socket")
    for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "create_connection"),
                        (socket, "getaddrinfo")):
        monkeypatch.setattr(owner, name, refuse)


class World:
    """One owner's root, a stand-in clock and a stand-in quote source (prices; stamps: the clock unless set)."""

    def __init__(self, root: Path, monkeypatch, capsys) -> None:
        self.root, self.capsys = root, capsys
        self.t = T0 + 3_600.0
        self.prices = {"SPY": 100.0, "QQQ": 200.0, "IWM": 50.0}
        self.stamps: dict = {}
        self.asked: list = []
        self.down = False
        monkeypatch.setattr(osx, "wall_clock", lambda: self.t)
        monkeypatch.setattr(osx, "minute_quotes", self.quotes)

    def quotes(self, symbols):
        self.asked.append(sorted(symbols))
        if self.down:
            raise OSError("the network is down")
        return {s: (self.prices[s], self.stamps.get(s, self.t)) for s in symbols if s in self.prices}

    @property
    def mem(self) -> Path:
        return self.root / "memory"

    @property
    def ledger(self) -> Path:
        return osx.ledger_path_for(self.mem, VENUE)

    @property
    def state(self) -> Path:
        return osx.state_path_for(self.ledger, VENUE)

    @property
    def pending(self) -> Path:
        return osx.owner_preview_path_for(self.state)

    def cmd(self, *args, venue=VENUE):
        self.capsys.readouterr()
        code = cli.main(["--root", str(self.root), "order-step", "--venue", venue, *args])
        got = self.capsys.readouterr()
        return code, got.out, got.err

    def preview(self, *args):
        code, out, err = self.cmd(*args)
        assert code == 0, err
        (number,) = re.findall(r"^code: ([0-9]{6})$", out, re.M)
        return number, out

    def write(self, *args):
        """Preview then confirm at the same quotes; the confirm's (code, out, err)."""
        number, _ = self.preview(*args)
        self.t += 1.0
        return self.cmd(*args, "--confirm", number)

    def entries(self):
        return osx._owner_entries_read_only(self.state)

    def journal_lines(self):
        j = self.root / "journal"
        return [e for e in _paper.Journal(j).read() if e.get("type") == "owner_entry"] if j.is_dir() else []

    def files(self):
        """The bytes of the state file, the ledger and every journal file (None: not there)."""
        out = {p.name: (p.read_bytes() if p.exists() else None) for p in (self.state, self.ledger)}
        j = self.root / "journal"
        out.update({f"journal/{p.name}": p.read_bytes() for p in sorted(j.glob("*"))} if j.is_dir() else {})
        return out


@pytest.fixture
def w(tmp_path, monkeypatch, capsys):
    return World(tmp_path / "root", monkeypatch, capsys)


def trades(path: Path, rows, start=T0):
    """A ledger file with one filled order per (symbol, units, price, fee) (units below 0: a sale; fee None: none
    reported), each applied; its clock starts at `start` and moves 30 s an order. Returns the ledger's clock."""
    clock = [start]
    led = _orders.OrderLedger(path, clock=lambda: clock[0])

    class Venue:
        name, book, held = "stand-in", {}, {}

        def submit(self, symbol, units, tag, price=None, limit_price=None):
            self.book[tag] = {"state": "open", "filled_units": 0.0, "avg_price": None, "fee": None}
            return {"status": "accepted"}

        def order_status(self, tag):
            return dict(self.book[tag])

        def positions(self):
            return dict(self.held)
    v = Venue()
    v.held = dict(osx.broker_book(led.fills()))                         # what the account holds: the book so far
    led.reconcile(v, osx.broker_book(led.fills()))
    n = len(led.orders())
    for i, (sym, u, price, fee) in enumerate(rows):
        tag = f"{'b' if u > 0 else 's'}{n + i}-{sym}"
        led.intend(tag, sym, u, price, limit_price=price + (1 if u > 0 else -1))
        led.submit_once(v, tag)
        v.book[tag].update(state="filled", filled_units=abs(u), avg_price=price, fee=fee)
        clock[0] += 30
        v.held = {**v.held, sym: v.held.get(sym, 0.0) + u}
        led.reconcile(v, osx.broker_book([f for f in led.fills() if f.get("applied")]))
        led.mark_applied([f["id"] for f in led.unapplied_fills()])
        led.reconcile(v, osx.broker_book(led.fills()))
    led.close()
    return clock[0]


def opened(w, rows=(("SPY", 10.0, 100.0, 0.01), ("QQQ", 2.0, 200.0, 0.01)), amount="5000"):
    """An opening cash_in on an empty book, then `rows` filled; the clock after them."""
    w.t = T0 - 600.0
    code, _, err = w.write("--owner-entry", "cash_in", "--amount", amount, "--who", "owner", "--note", "opening")
    assert code == 0, err
    w.t = trades(w.ledger, rows) + 60.0
    return w


CASH_IN = ("--owner-entry", "cash_in", "--amount", "100", "--who", "owner", "--note", "a deposit")


# ---- outcome 1: marks are never typed


def test_a_typed_mark_is_refused_in_words_and_writes_nothing(w):
    opened(w)
    before = w.files()
    code, _, err = w.cmd(*CASH_IN, "--mark", "SPY=100")
    assert code == 2 and err.startswith("refused:") and "--mark is not taken" in err and "nothing was written" in err
    assert w.files() == before and not w.pending.exists() and w.asked == []


def test_every_fund_held_and_the_entrys_own_symbol_are_asked_of_the_quote_source(w):
    opened(w)
    w.asked.clear()
    w.preview(*CASH_IN)
    assert w.asked == [["QQQ", "SPY"]]
    w.asked.clear()
    w.preview("--owner-entry", "units_out", "--symbol", "SPY", "--units", "1", "--who", "o", "--note", "n")
    assert w.asked == [["QQQ", "SPY"]]
    w.asked.clear()
    w.cmd("--owner-entry", "split", "--symbol", "IWM", "--ratio", "2", "--who", "o", "--note", "n")
    assert w.asked == [["IWM", "QQQ", "SPY"]]                          # its own symbol too (then R21 refuses it)


def test_a_book_holding_no_units_asks_for_no_quote_and_opens_with_the_network_down(w):
    w.down = True
    code, _, err = w.write(*CASH_IN)
    assert code == 0, err
    assert w.asked == [] and len(w.entries()) == 1


@pytest.mark.parametrize("case, words", [
    ("old", "minutes old, more than 20"),
    ("ahead", "s after now"),
    ("missing", "no quote"),
    ("bad price", "price is not a positive number"),
    ("down", "the quotes could not be read"),
])
def test_inside_the_session_a_quote_not_usable_refuses_naming_the_fund(w, case, words):
    opened(w)
    if case == "old":
        w.stamps["QQQ"] = w.t - osx.QUOTE_MAX_AGE_SECONDS - 1
    elif case == "ahead":
        w.stamps["QQQ"] = w.t + osx.QUOTE_MAX_AHEAD_SECONDS + 1
    elif case == "missing":
        del w.prices["QQQ"]
    elif case == "bad price":
        w.prices["QQQ"] = -1.0
    else:
        w.down = True
    before = w.files()
    code, _, err = w.cmd(*CASH_IN)
    assert code == 2 and err.startswith("refused:") and words in err and "nothing was written" in err, err
    if case != "down":
        assert "QQQ:" in err and "SPY:" not in err
    assert w.files() == before and not w.pending.exists()


@pytest.mark.parametrize("edge", ["old", "ahead"])
def test_the_age_rule_is_the_order_steps_own_on_both_sides_of_its_limit(w, edge):
    opened(w)
    limit = osx.QUOTE_MAX_AGE_SECONDS if edge == "old" else -osx.QUOTE_MAX_AHEAD_SECONDS
    w.stamps["QQQ"] = w.t - limit                                       # exactly at the limit: usable
    assert w.cmd(*CASH_IN)[0] == 0
    w.stamps["QQQ"] = w.t - limit - (1 if edge == "old" else -1)        # a second beyond: refused
    assert w.cmd(*CASH_IN)[0] == 2


@pytest.mark.parametrize("now, close", [
    ("2025-11-22 12:00", "2025-11-21 16:00"),                          # a Saturday: Friday's close
    ("2025-11-18 19:30", "2025-11-18 16:00"),                          # the evening: today's close
    ("2025-11-19 08:00", "2025-11-18 16:00"),                          # before the open: yesterday's close
    ("2025-11-28 15:00", "2025-11-28 13:00"),                          # a half-day, after its close
    ("2025-11-27 12:00", "2025-11-26 16:00"),                          # a holiday: the day before's close
])
def test_outside_the_session_the_last_close_serves_and_its_time_is_shown(w, now, close):
    opened(w)
    w.t = at(now)
    shut = at(close)
    w.stamps = {s: shut - 60.0 for s in w.prices}                      # the last one-minute bar, stamped at its start
    number, out = w.preview(*CASH_IN)
    assert f"last session's close, {osx._iso(shut)}" in out
    assert f"at {osx._iso(shut - 60.0)}" in out
    w.t += 5.0
    code, _, err = w.cmd(*CASH_IN, "--confirm", number)
    assert code == 0, err


@pytest.mark.parametrize("stamp, words", [(-osx.QUOTE_MAX_AGE_SECONDS - 1, "minutes old"),
                                          (osx.QUOTE_MAX_AHEAD_SECONDS + 1, "after now")])
def test_outside_the_session_a_quote_not_usable_at_the_close_refuses(w, stamp, words):
    opened(w)
    w.t = at("2025-11-22 12:00")
    shut = at("2025-11-21 16:00")
    w.stamps = {"SPY": shut - 60.0, "QQQ": shut + stamp}
    before = w.files()
    code, _, err = w.cmd(*CASH_IN)
    assert code == 2 and "QQQ:" in err and words in err and "last session's close" in err, err
    assert w.files() == before


def test_inside_and_outside_are_told_by_the_calendar_open_and_close():
    cal = T.CAL
    assert osx.owner_mark_close(cal, at("2025-11-18 09:30")) == (True, None, None)
    assert osx.owner_mark_close(cal, at("2025-11-18 16:00")) == (True, None, None)
    assert osx.owner_mark_close(cal, at("2025-11-18 09:29:59")) == (False, at("2025-11-17 16:00"), None)
    assert osx.owner_mark_close(cal, at("2025-11-18 16:00:01")) == (False, at("2025-11-18 16:00"), None)
    assert osx.owner_mark_close(cal, at("2025-11-28 13:00:01")) == (False, at("2025-11-28 13:00"), None)

    class Broken:
        def is_session(self, day):
            raise RuntimeError("no data")

        def previous_close(self, minute):
            raise RuntimeError("no data")
    inside, close, why = osx.owner_mark_close(Broken(), T0)
    assert inside is False and close is None and "calendar" in why


# ---- outcome 2: a preview, then a confirm


def test_the_preview_prints_the_figures_and_a_code_and_writes_no_byte_of_state_journal_or_ledger(w):
    opened(w)
    before = w.files()
    number, out = w.preview(*CASH_IN)
    assert w.files() == before
    assert "preview: owner entry, cash_in 100, for webull-test-cash; NOTHING was written" in out
    assert f"mark QQQ: 200.0 at {osx._iso(w.t)} (source: quotes" in out and "mark SPY: 100.0" in out
    assert "inside the session" in out
    assert "the book's value: " in out and "units outstanding: " in out and "value per unit: " in out
    assert f"--confirm {number}" in out
    rec = json.loads(w.pending.read_text())
    assert rec["code"] == number and rec["what"] == osx.OWNER_PREVIEW_KIND and rec["venue"] == VENUE
    assert stat.S_IMODE(w.pending.stat().st_mode) == 0o600
    assert [p.name for p in w.pending.parent.iterdir() if p.name.endswith(".tmp")] == []
    fig = osx.book_figures(*_rows(w), w.entries(), {"SPY": 100.0, "QQQ": 200.0}, "2025-11-18", now=w.t)
    assert f"the book's value: {fig.value} (from {fig.value} to {fig.value_high}" in out
    e = rec["entry"]
    assert f"units outstanding: {e['units_before']} before, {e['units_after']} after" in out
    assert f"value per unit: {e['value_per_unit_before']} before, {e['value_per_unit_after']} after" in out


def _rows(w):
    ro = _orders.open_read_only(w.ledger)
    try:
        return ro.fills(), ro.orders()
    finally:
        ro.close()


def test_a_value_not_known_refuses_as_today_in_r32s_words(w):
    opened(w, rows=(("SPY", 10.0, 100.0, 0.01), ("SPY", -4.0, 110.0, None)))
    code, _, err = w.cmd(*CASH_IN)
    assert code == 2 and "the book's value is not known (order s1-SPY: its fee is not known" in err, err
    assert not w.pending.exists()


@pytest.mark.parametrize("move", [0.0, 0.0049, -0.0049])
def test_a_confirm_with_marks_within_the_band_writes_one_entry_at_the_confirms_marks(w, move):
    opened(w)
    args = ("--owner-entry", "units_out", "--symbol", "SPY", "--units", "2", "--who", "owner", "--note", "took 2")
    number, _ = w.preview(*args)
    lines = len(w.journal_lines())
    w.t += 30.0
    w.prices = {s: p * (1 + move) for s, p in w.prices.items()}
    code, out, err = w.cmd(*args, "--confirm", number)
    assert code == 0, err
    rows = w.entries()
    assert len(rows) == 2 and len(w.journal_lines()) == lines + 1
    assert rows[-1]["mark"] == str(D(repr(100.0 * (1 + move))))         # the confirm's fresh mark
    assert rows[-1]["time"] == w.t and not w.pending.exists()
    fig = osx.book_figures(*_rows(w), rows[:-1], dict(w.prices), "2025-11-18", now=w.t)
    assert D(rows[-1]["value_per_unit_before"]) == fig.value_per_unit


@pytest.mark.parametrize("move", [0.0051, -0.0051, 0.02])
def test_a_mark_moved_beyond_the_band_refuses_says_what_moved_and_spends_the_preview(w, move):
    opened(w)
    number, _ = w.preview(*CASH_IN)
    w.t += 30.0
    w.prices["QQQ"] *= 1 + move
    before = w.files()
    code, _, err = w.cmd(*CASH_IN, "--confirm", number)
    assert code == 2 and "the mark of QQQ" in err and "0.5%" in err and "the mark of SPY" not in err, err
    assert "the preview is spent" in err and w.files() == before and not w.pending.exists()
    code, _, err = w.cmd(*CASH_IN, "--confirm", number)
    assert code == 2 and "no preview is pending" in err


def _new_fill(w):
    trades(w.ledger, [("IWM", 1.0, 50.0, 0.01)], start=w.t - 10.0)


def _order_changed(w):
    con = __import__("sqlite3").connect(w.ledger)
    con.execute("UPDATE orders SET fee = 0.02 WHERE tag = (SELECT MIN(tag) FROM orders)")
    con.commit()
    con.close()


def _entry_since(w):
    code, _, err = w.write("--owner-entry", "cash_in", "--amount", "7", "--who", "owner", "--note", "another")
    assert code == 0, err


DIFFERS = {
    "the amount": (lambda w: None, ("--owner-entry", "cash_in", "--amount", "101", "--who", "owner", "--note",
                                    "a deposit"), "arguments"),
    "who": (lambda w: None, ("--owner-entry", "cash_in", "--amount", "100", "--who", "other", "--note", "a deposit"),
            "arguments"),
    "the note": (lambda w: None, ("--owner-entry", "cash_in", "--amount", "100", "--who", "owner", "--note", "x"),
                 "arguments"),
    "a fill arrived": (_new_fill, CASH_IN, "the ledger's fills or orders"),
    "an order changed": (_order_changed, CASH_IN, "the ledger's fills or orders"),
    "an entry since": (_entry_since, CASH_IN, None),
}


@pytest.mark.parametrize("what", list(DIFFERS))
def test_a_confirm_after_anything_else_changed_refuses_and_writes_nothing(w, what):
    opened(w)
    number, _ = w.preview(*CASH_IN)
    change, args, words = DIFFERS[what]
    w.t += 30.0
    change(w)
    before = w.files()
    code, _, err = w.cmd(*args, "--confirm", number)
    assert code == 2 and err.startswith("refused:"), err
    if words is not None:
        assert words in err, err
    else:                                                               # the entry since was itself a new preview
        assert "not the pending preview's" in err or "no preview is pending" in err, err
    assert w.files() == before


def test_the_set_of_funds_held_and_the_marks_basis_are_held_too(w):
    opened(w)
    w.t = at("2025-11-18 15:58")
    number, _ = w.preview(*CASH_IN)
    w.t = at("2025-11-18 16:01")                                        # the session closed: another basis
    w.stamps = {s: at("2025-11-18 15:59") for s in w.prices}
    before = w.files()
    code, _, err = w.cmd(*CASH_IN, "--confirm", number)
    assert code == 2 and "the marks' basis" in err and w.files() == before, err
    rec = {"held": ["SPY"], "head": "h", "ledger": "l", "arguments": {}, "close": None, "marks": {}}
    assert osx._preview_differences(rec, dict(rec)) == []
    assert any("the set of funds the book holds" in d for d in osx._preview_differences(rec, dict(rec, held=["QQQ", "SPY"])))


def test_an_expired_preview_refuses_and_its_life_is_the_tickets(w):
    opened(w)
    number, _ = w.preview(*CASH_IN)
    w.t += osx.TICKET_LIFE_SECONDS
    assert w.cmd(*CASH_IN, "--confirm", number)[0] == 0                 # the last moment of its life
    w.t += 1.0
    number, _ = w.preview(*CASH_IN)
    w.t += osx.TICKET_LIFE_SECONDS + 1.0
    w.stamps = {}
    before = w.files()
    code, _, err = w.cmd(*CASH_IN, "--confirm", number)
    assert code == 2 and "expired" in err and w.files() == before and not w.pending.exists()


def test_two_previews_then_the_first_code_refuses_and_the_second_still_writes(w):
    opened(w)
    first, _ = w.preview(*CASH_IN)
    second, _ = w.preview(*CASH_IN)
    while second == first:                                              # one in a million: draw again
        second, _ = w.preview(*CASH_IN)
    before = w.files()
    code, _, err = w.cmd(*CASH_IN, "--confirm", first)
    assert code == 2 and "not the pending preview's" in err and w.files() == before
    assert not w.pending.exists()                                       # a refused confirm spends the preview
    third, _ = w.preview(*CASH_IN)
    assert w.cmd(*CASH_IN, "--confirm", third)[0] == 0


@pytest.mark.parametrize("code_text", ["12345", "1234567", "abcdef", " 123456", "１２３４５６"])
def test_a_code_that_is_not_six_digits_is_refused_before_the_preview_is_read(w, code_text):
    opened(w)
    w.preview(*CASH_IN)
    held = w.pending.read_bytes()
    code, _, err = w.cmd(*CASH_IN, "--confirm", code_text)
    assert code == 2 and "six-digit code" in err and w.pending.read_bytes() == held


def test_no_preview_pending_or_one_that_cannot_be_read_refuses(w):
    opened(w)
    code, _, err = w.cmd(*CASH_IN, "--confirm", "123456")
    assert code == 2 and "no preview is pending" in err
    w.pending.write_text("not json")
    code, _, err = w.cmd(*CASH_IN, "--confirm", "123456")
    assert code == 2 and "cannot be read" in err and not w.pending.exists()


# ---- addendum 1 (R35): the entry is judged again under the lock; (R36) a preview edited by hand is not one


def _a_fill_arrives(w):
    trades(w.ledger, [("SPY", 1.0, 100.0, 0.01)], start=w.t - 70.0)      # a filled purchase, as V212's stand-in


def _an_order_arrives(w):
    led = _orders.OrderLedger(w.ledger, clock=lambda: w.t - 0.5)
    try:
        led.intend("b99-IWM", "IWM", 1.0, 50.0, limit_price=51.0)
    finally:
        led.close()


def _an_entry_arrives(w):
    state = osx.OrderStepState(w.state)
    try:
        last = state.owner_entries()[-1]
        state.add_owner_entry({"kind": "cash_in", "amount": "1", "units_before": last["units_after"],
                               "units_after": last["units_after"]}, who="o", note="in between", now=w.t - 0.5)
    finally:
        state.close()


def _a_newer_preview(w):
    rec = json.loads(w.pending.read_text())
    rec.update(code=f"{(int(rec['code']) + 1) % 1_000_000:06d}", created=w.t - 0.5)
    w.pending.write_text(json.dumps(rec))


ARRIVES = {            # what the stand-in lock's acquire does first: (change, the refusal's words, the preview spent?)
    "a fill": (_a_fill_arrives, "a fill or an order arrived while the entry was being confirmed", True),
    "an order row": (_an_order_arrives, "a fill or an order arrived while the entry was being confirmed", True),
    "a fee arriving": (_order_changed, "a fill or an order arrived while the entry was being confirmed", True),
    "an owner's entry": (_an_entry_arrives, "an owner's entry arrived while the entry was being confirmed", True),
    "a newer preview": (_a_newer_preview, "a newer preview was made meanwhile and stays pending", False),
}


@pytest.mark.parametrize("what", list(ARRIVES))
def test_r35_what_arrives_as_the_lock_is_taken_is_refused_and_nothing_of_ours_is_written(w, monkeypatch, what):
    """A stand-in lock whose acquire first makes the change (no second process): refused, the state file, the journal
    and the ledger byte-identical to just after that change, the preview spent (a newer one stays pending)."""
    change, words, spent = ARRIVES[what]
    opened(w)
    number, _ = w.preview(*CASH_IN)
    w.t += 1.0
    before, after, kept = w.files(), {}, {}
    real = osx._RunLock.acquire

    def acquire(self):
        change(w)
        after.update(w.files())
        kept["pending"] = w.pending.read_bytes()
        return real(self)
    monkeypatch.setattr(osx._RunLock, "acquire", acquire)
    asked = len(w.asked)
    code, _, err = w.cmd(*CASH_IN, "--confirm", number)
    assert len(w.asked) == asked + 1                                    # the confirm's own quotes; none under the lock
    assert code == 2 and err.startswith("refused:") and words in err, err
    assert after and w.files() == after
    assert (after != before) == (what != "a newer preview")
    if spent:
        assert "the preview is spent" in err and not w.pending.exists()
    else:
        assert w.pending.read_bytes() == kept["pending"]
    monkeypatch.setattr(osx._RunLock, "acquire", real)
    lock = osx._RunLock(str(w.state))                                   # the lock was let go
    assert lock.acquire()
    lock.release()
    if what != "a newer preview":                                       # the owner looks again: the book on disk
        code, out, err = w.write(*CASH_IN)
        assert code == 0, err


def _mark_of_first(value):
    def edit(rec):
        rec["marks"][sorted(rec["marks"])[0]] = value
    return edit


HAND_EDITS = {
    "a mark that is a number": _mark_of_first(5),
    "a mark that is words": _mark_of_first("x"),
    "a mark that is a list": _mark_of_first([1]),
    "a mark that is null": _mark_of_first(None),
    "a mark with no price": _mark_of_first({}),
    "a price in words": _mark_of_first({"price": "100", "time": 1.0}),
    "a price not finite": _mark_of_first({"price": float("nan"), "time": 1.0}),
    "a price that is true": _mark_of_first({"price": True, "time": 1.0}),
    "a code not six digits": lambda rec: rec.update(code="\ud800"),
    "arguments not an object": lambda rec: rec.update(arguments=[]),
    "a head not words": lambda rec: rec.update(head=5),
    "a ledger digest not words": lambda rec: rec.update(ledger=None),
    "held not a list": lambda rec: rec.update(held="SPY"),
    "held not of symbols": lambda rec: rec.update(held=[1]),
    "a close not a time": lambda rec: rec.update(close="x"),
    "a close that is true": lambda rec: rec.update(close=True),
}


@pytest.mark.parametrize("what", list(HAND_EDITS))
def test_r36_a_preview_edited_by_hand_is_not_one_refused_spent_and_nothing_written(w, what):
    opened(w)
    number, _ = w.preview(*CASH_IN)
    rec = json.loads(w.pending.read_text())
    HAND_EDITS[what](rec)
    w.pending.write_text(json.dumps(rec))
    before = w.files()
    code, _, err = w.cmd(*CASH_IN, "--confirm", number)
    assert code == 2 and "not an owner-entry preview" in err, err
    assert w.files() == before and not w.pending.exists()


def test_only_the_owners_command_knows_the_pending_preview():
    """R34 as built: the pending preview lives in its own file, which no other code names: not the runs, the
    reconcile, the ticket book, untracked_tags, owner_entry_differences, the alerts or the owner's page."""
    pkg = Path(osx.__file__).resolve().parents[1]
    named = sorted(str(p.relative_to(pkg)) for p in pkg.rglob("*.py")
                   if "owner-preview" in p.read_text() or "owner_preview_path_for" in p.read_text())
    assert named == ["execution_and_risk/order_step.py"]
    src = Path(osx.__file__).read_text()
    tree = ast.parse(src)
    users = {f.name for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
             and any(isinstance(n, ast.Name) and n.id in ("owner_preview_path_for", "_read_preview", "_spend_preview")
                     for n in ast.walk(f))}
    assert users == {"owner_entry_command", "_owner_refuse"}
    alerts = (pkg / "core" / "alerts.py").read_text()
    assert 'glob("orders-*.sqlite")' in alerts and "owner-preview" not in alerts


def test_a_pending_preview_is_not_seen_by_the_checks_that_find_faults(w):
    opened(w)
    rows = w.entries()
    lines = _paper.Journal(w.root / "journal").read()
    fills, orders = _rows(w)
    tags = [o["tag"] for o in orders]
    before = (osx.owner_entry_differences(rows, lines, venue=VENUE), osx.untracked_tags(tags, venue=VENUE,
                                                                                          journal=lines))
    w.preview(*CASH_IN)
    rows2, lines2 = w.entries(), _paper.Journal(w.root / "journal").read()
    after = (osx.owner_entry_differences(rows2, lines2, venue=VENUE), osx.untracked_tags(tags, venue=VENUE,
                                                                                           journal=lines2))
    assert before == after == ([], [])
    led = _orders.open_read_only(w.ledger)
    try:
        assert len(led.orders()) == len(orders)
    finally:
        led.close()


# ---- outcome 3: the repair of an entry whose journal line failed


def _broken_journal(monkeypatch):
    def broken(self, day, entry):
        raise OSError("the disk is full")
    monkeypatch.setattr(_paper.Journal, "write", broken)


def test_exit_3_prints_the_entrys_hash_and_the_repair(w, monkeypatch):
    opened(w)
    number, _ = w.preview(*CASH_IN)
    _broken_journal(monkeypatch)
    code, _, err = w.cmd(*CASH_IN, "--confirm", number)
    assert code == osx.JOURNAL_NOT_WRITTEN
    row = w.entries()[-1]
    assert f"hash {row['hash']}" in err and f"--owner-entry-rejournal {row['id']} --hash {row['hash']}" in err


def _line_text(root: Path, n: int) -> str:
    for p in sorted((root / "journal").glob("*.jsonl")):
        for raw in p.read_text().splitlines():
            try:
                x = json.loads(raw)
            except ValueError:
                continue
            if x.get("type") == "owner_entry" and x.get("id") == n:
                return raw
    raise AssertionError(f"no line of entry {n}")


def test_the_repair_writes_the_line_a_clean_run_writes_and_the_next_entry_works(tmp_path, monkeypatch, capsys):
    """Two roots, the same book, clock and quotes: in one the entry is journaled; in the other its journal line fails,
    and the repair writes it. The two lines are the same, byte for byte, but for the journal's own timestamp and the
    one field the repair adds (who repaired it, why, when)."""
    clean = World(tmp_path / "clean", monkeypatch, capsys)
    opened(clean)
    clean.write(*CASH_IN)
    broken = World(tmp_path / "broken", monkeypatch, capsys)
    opened(broken)
    number, _ = broken.preview(*CASH_IN)
    broken.t = clean.t
    with monkeypatch.context() as mp:
        _broken_journal(mp)
        assert broken.cmd(*CASH_IN, "--confirm", number)[0] == osx.JOURNAL_NOT_WRITTEN
    row = broken.entries()[-1]
    assert row == clean.entries()[-1]
    assert osx.owner_entry_differences(broken.entries(), _paper.Journal(broken.root / "journal").read(),
                                       venue=VENUE) == [f"owner entry {row['id']} is in the state file and not in the journal"]
    code, _, err = broken.cmd("--owner-entry-rejournal", str(row["id"]), "--hash", row["hash"], "--who", "owner",
                              "--note", "the disk was full")
    assert code == 0, err
    a, b = _line_text(clean.root, row["id"]), _line_text(broken.root, row["id"])
    stamp = re.compile(r'^\{"timestamp": "[^"]*", ')
    rejournaled = json.loads(b)["rejournaled"]
    assert rejournaled == {"who": "owner", "note": "the disk was full", "time": repr(broken.t)}
    assert stamp.sub("", b) == stamp.sub("", a)[:-1] + ', "rejournaled": ' + json.dumps(rejournaled) + "}"
    assert osx.owner_entry_differences(broken.entries(), _paper.Journal(broken.root / "journal").read(),
                                       venue=VENUE) == []
    broken.t += 60.0
    assert broken.write("--owner-entry", "cash_in", "--amount", "3", "--who", "owner", "--note", "next")[0] == 0
    assert len(broken.entries()) == 3


def _failed(w, monkeypatch, n_entries=2):
    """A book whose last entry's journal line failed; that entry."""
    opened(w)
    for k in range(n_entries - 2):
        w.t += 10.0
        assert w.write("--owner-entry", "cash_in", "--amount", str(10 + k), "--who", "o", "--note", "more")[0] == 0
    w.t += 10.0
    number, _ = w.preview(*CASH_IN)
    with monkeypatch.context() as mp:
        _broken_journal(mp)
        assert w.cmd(*CASH_IN, "--confirm", number)[0] == osx.JOURNAL_NOT_WRITTEN
    w.t += 10.0
    return w.entries()[-1]


def _day_file(w):
    (p,) = sorted((w.root / "journal").glob("*.jsonl"))
    return p


def _change_state_entry(w, row):
    con = __import__("sqlite3").connect(w.state)
    con.execute("DROP TRIGGER owner_entries_no_update")
    con.execute("UPDATE owner_entries SET amount = '999' WHERE id = ?", (row["id"],))
    con.commit()
    con.close()


def _change_journaled_entry(w, row):
    p = _day_file(w)
    lines = [json.loads(x) for x in p.read_text().splitlines()]
    for x in lines:
        if x.get("type") == "owner_entry" and x.get("id") == 1:
            x["amount"] = "1"
    p.write_text("".join(json.dumps(x) + "\n" for x in lines))


def _reorder_journal(w, row):
    p = _day_file(w)
    raw = p.read_text().splitlines()
    p.write_text("\n".join(list(reversed(raw))) + "\n")


REPAIRS_REFUSED = {
    "the chain does not check": (_change_state_entry, None, None, "changed after it was written"),
    "another difference": (_change_journaled_entry, None, None, "differs between the journal and the state file"),
    "lines reordered": (_reorder_journal, None, None, "not their ids' order"),
    "not the last entry": (None, 1, None, "only the LAST entry"),
    "a hash that is not the entry's": (None, None, "0" * 64, "that hash is not"),
    "a number that is not one": (None, "x", None, "the entry's number"),
    "a hash that is not one": (None, None, "abc", "64 hexadecimal digits"),
    "with a figure": (None, None, None, "takes only"),
    "with --confirm": (None, None, None, "takes only"),
    "a run holding the lock": (None, None, None, "run holds"),
}


@pytest.mark.parametrize("what", list(REPAIRS_REFUSED))
def test_the_repair_refuses_any_other_state_and_writes_nothing(w, monkeypatch, what):
    row = _failed(w, monkeypatch, n_entries=3)
    change, number, given, words = REPAIRS_REFUSED[what]
    if change is not None:
        change(w, row)
    args = ["--owner-entry-rejournal", str(row["id"] if number is None else number), "--hash",
            row["hash"] if given is None else given, "--who", "owner", "--note", "repair"]
    if what == "with a figure":
        args += ["--amount", "5"]
    elif what == "with --confirm":
        args += ["--confirm", "123456"]
    with contextlib.ExitStack() as stack:
        if what == "a run holding the lock":
            lock = osx._RunLock(str(w.state))
            assert lock.acquire()
            stack.callback(lock.release)
        before = w.files()
        code, _, err = w.cmd(*args)
        assert code == 2 and err.startswith("refused:") and words in err and "nothing was written" in err, err
        assert w.files() == before


def test_the_repair_refuses_when_nothing_is_missing(w):
    opened(w)
    row = w.entries()[-1]
    before = w.files()
    code, _, err = w.cmd("--owner-entry-rejournal", str(row["id"]), "--hash", row["hash"], "--who", "o", "--note", "n")
    assert code == 2 and "the ONE difference" in err and w.files() == before


def test_the_repair_needs_the_entrys_own_hash(w, monkeypatch):
    """The owner types the hash the failed command printed; another one writes nothing."""
    row = _failed(w, monkeypatch)
    code, _, err = w.cmd("--owner-entry-rejournal", str(row["id"]), "--hash", "f" * 64, "--who", "o", "--note", "n")
    assert code == 2 and w.journal_lines()[-1]["id"] == row["id"] - 1


# ---- outcome 4: production stays refused, in every form


@pytest.mark.parametrize("form", [
    CASH_IN,
    (*CASH_IN, "--confirm", "123456"),
    ("--owner-entry-rejournal", "1", "--hash", "0" * 64, "--who", "o", "--note", "n"),
    ("--owner-entry", "units_out", "--symbol", "SPY", "--units", "1", "--who", "o", "--note", "n"),
])
@pytest.mark.parametrize("name", ["webull-production", "webull-production-events", "Webull-Production-Events",
                                  "webull-production."])
def test_a_production_venue_is_refused_in_every_form_before_any_quote(w, form, name):
    w.root.mkdir(parents=True)
    code, _, err = w.cmd(*form, venue=name)
    assert code == 2 and "production venue" in err and "nothing was written" in err, err
    assert w.asked == [] and list(w.root.iterdir()) == []


# ---- outcome 6: properties over seeded books


SYMBOLS = ("SPY", "QQQ", "IWM")


def seeded(w, seed):
    """A seeded book: an opening, a few filled orders (fees known, or a purchase's left empty: bounded), and some
    entries; returns the rng."""
    rng = random.Random(seed)
    w.prices = {s: round(rng.uniform(20, 400), 2) for s in SYMBOLS}
    w.t = T0 - 900.0
    assert w.write("--owner-entry", "cash_in", "--amount", str(rng.randint(5_000, 50_000)), "--who", "owner",
                   "--note", "opening")[0] == 0
    rows = []
    for _ in range(rng.randint(0, 5)):
        sym = rng.choice(SYMBOLS)
        rows.append((sym, float(rng.randint(1, 9)), w.prices[sym], rng.choice([0.01, 0.0, None])))
    if rows:
        w.t = trades(w.ledger, rows) + 60.0
    else:
        w.t = T0 + 60.0
    for _ in range(rng.randint(0, 2)):
        w.t += 30.0
        w.write("--owner-entry", "cash_in", "--amount", str(rng.randint(1, 500)), "--who", "owner", "--note", "more")
    w.t += 30.0
    return rng


def entry_args(w, rng):
    fig = osx.book_figures(*_rows(w), w.entries(), {}, "2025-11-18", now=w.t) if w.ledger.exists() else None
    held = sorted(fig.unmarked) if fig else []
    kinds = ["cash_in", "cash_out"] + (["units_out", "split"] if held else [])
    kind = rng.choice(kinds)
    base = ("--who", "owner", "--note", f"seeded {kind}")
    if kind in ("cash_in", "cash_out"):
        return ("--owner-entry", kind, "--amount", str(rng.randint(1, 300)), *base)
    sym = rng.choice(held)
    if kind == "units_out":
        return ("--owner-entry", kind, "--symbol", sym, "--units", "1", *base)
    return ("--owner-entry", kind, "--symbol", sym, "--ratio", rng.choice(["2", "3", "0.5"]), *base)


@pytest.fixture(scope="module")
def old(tmp_path_factory):
    """832977f's order_step.py (main before this slice), from this checkout's history with `git archive`, loaded
    under its own name; or the test FAILS in words."""
    root = Path(__file__).resolve().parents[2]
    folder = tmp_path_factory.mktemp("t51c_s4b_base")
    try:
        data = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", BASE,
                               "valedia_trader/execution_and_risk/order_step.py"], capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        why = (getattr(e, "stderr", b"") or b"").decode("utf-8", "replace").strip()[:200]
        pytest.fail(f"the code before this slice, commit {BASE}, cannot be read from this checkout's history "
                    f"({type(e).__name__}: {why}); the comparison cannot be made, so it fails")
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(folder, filter="data")
    path = folder / "valedia_trader" / "execution_and_risk" / "order_step.py"
    spec = importlib.util.spec_from_file_location("order_step_t51c_s4b_base", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["order_step_t51c_s4b_base"] = mod
    spec.loader.exec_module(mod)
    assert not hasattr(mod, "owner_preview_path_for") and "--mark" in path.read_text()
    return mod


@pytest.mark.parametrize("seed", range(24))
def test_prop_preview_then_confirm_writes_what_832977f_writes_with_those_marks_typed(tmp_path, monkeypatch, capsys,
                                                                                     old, seed):
    new = World(tmp_path / "new", monkeypatch, capsys)
    rng = seeded(new, seed)
    args = entry_args(new, rng)
    twin = tmp_path / "old"                                              # the same book before this entry
    import shutil
    shutil.copytree(new.root, twin)
    held = sorted(osx.book_figures(*_rows(new), new.entries(), {}, "2025-11-18", now=new.t).unmarked) \
        if new.ledger.exists() else []
    code, out, err = new.cmd(*args)
    if code == 0:
        (number,) = re.findall(r"^code: ([0-9]{6})$", out, re.M)
        new.t += 1.0
        code, _, err = new.cmd(*args, "--confirm", number)
    marks = sorted(set(held) | ({args[3]} if args[1] in ("units_out", "split") else set()))
    ns = types.SimpleNamespace(**{k: None for k in ("approve", "capital", "amount", "units", "ratio", "symbol")},
                               propose=False, live=False, check=False, fractional=False, venue=VENUE)
    flags = dict(zip(args[::2], args[1::2]))
    ns.owner_entry, ns.who, ns.note = flags["--owner-entry"], flags["--who"], flags["--note"]
    for k in ("amount", "units", "ratio", "symbol"):
        setattr(ns, k, flags.get(f"--{k}"))
    ns.mark = [f"{s}={repr(new.prices[s])}" for s in marks]
    paths = types.SimpleNamespace(memory=twin / "memory", journal=twin / "journal")
    was = old.owner_entry_command(ns, paths, clock=lambda: new.t)
    old_err = capsys.readouterr().err
    if code != 0:                                                       # refused now: refused by the old code too
        assert code == 2 and was == 2 and err.startswith("refused:") and old_err.startswith("refused:"), (err, old_err)
        return
    assert was == 0, old_err
    written = new.entries()[-1]
    then = osx._owner_entries_read_only(osx.state_path_for(osx.ledger_path_for(twin / "memory", VENUE), VENUE))
    assert then[-1] == written                                            # every field, the hash and the chain
    assert [e["hash"] for e in then] == [e["hash"] for e in new.entries()]
    old_line, new_line = json.loads(_line_text(twin, written["id"])), json.loads(_line_text(new.root, written["id"]))
    old_line.pop("timestamp"), new_line.pop("timestamp")
    assert old_line == new_line


@pytest.mark.parametrize("seed", range(24))
def test_prop_a_confirm_writes_one_entry_and_one_line_or_nothing(w, seed):
    rng = seeded(w, seed)
    args = entry_args(w, rng)
    code, out, err = w.cmd(*args)
    if code != 0:
        return
    (number,) = re.findall(r"^code: ([0-9]{6})$", out, re.M)
    w.t += rng.choice([1.0, 60.0, osx.TICKET_LIFE_SECONDS + 1.0])
    move = rng.choice([0.0, 0.003, -0.003, 0.006, -0.02])
    w.prices = {s: p * (1 + move) for s, p in w.prices.items()}
    if rng.random() < 0.3:
        _new_fill(w)
    before, n_entries, n_lines = w.files(), len(w.entries()), len(w.journal_lines())
    code, _, err = w.cmd(*args, "--confirm", rng.choice([number, number, "000000" if number != "000000" else "999999"]))
    if code == 0:
        assert len(w.entries()) == n_entries + 1 and len(w.journal_lines()) == n_lines + 1
    else:
        assert code == 2 and w.files() == before
    assert not w.pending.exists()


@pytest.mark.parametrize("seed", range(12))
def test_prop_a_confirm_after_any_fill_order_or_entry_since_the_preview_refuses(w, seed):
    rng = seeded(w, seed)
    args = entry_args(w, rng)
    code, out, _ = w.cmd(*args)
    if code != 0:
        return
    (number,) = re.findall(r"^code: ([0-9]{6})$", out, re.M)
    w.t += 1.0
    change = rng.choice([_new_fill, _order_changed, "entry"] if w.ledger.exists() else [_new_fill, "entry"])
    if change == "entry":
        state = osx.OrderStepState(w.state)
        try:
            last = state.owner_entries()[-1]
            state.add_owner_entry({"kind": "cash_in", "amount": "1", "units_before": last["units_after"],
                                   "units_after": last["units_after"]}, who="o", note="by hand", now=w.t)
        finally:
            state.close()
    else:
        change(w)
    w.t += 1.0
    before = w.files()
    code, _, err = w.cmd(*args, "--confirm", number)
    assert code == 2 and w.files() == before, err


# ---- every refusal of the command, enumerated from its code


def _sites():
    """Every call of _owner_refuse in the owner's command, its preview and its repair: (first line, last line)."""
    tree = ast.parse(Path(osx.__file__).read_text())
    out = set()
    for f in ast.walk(tree):
        if isinstance(f, ast.FunctionDef) and f.name in ("owner_entry_command", "_owner_preview", "_owner_rejournal"):
            for n in ast.walk(f):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_owner_refuse":
                    out.add((n.lineno, n.end_lineno))
    return out


def _lock_busy(w, mp):
    lock = osx._RunLock(str(w.state))
    assert lock.acquire()
    return lock.release


def _intruder(w, mp):
    """A writer that gets in between the confirm's checks and its lock (a stand-in: no process is raced)."""
    real = osx._RunLock.acquire

    def acquire(self):
        state = osx.OrderStepState(w.state)
        try:
            last = state.owner_entries()[-1]
            state.add_owner_entry({"kind": "cash_in", "amount": "1", "units_before": last["units_after"],
                                   "units_after": last["units_after"]}, who="o", note="in between", now=w.t - 0.5)
        finally:
            state.close()
        return real(self)
    mp.setattr(osx._RunLock, "acquire", acquire)


def _junk_preview(w, mp):
    w.pending.parent.mkdir(parents=True, exist_ok=True)
    w.pending.write_text("[]")


CATALOGUE = {          # name: (args, prepare(w, mp) -> optional cleanup, a preview first?)
    "a run's own flag": ((*CASH_IN, "--propose"), None, False),
    "a venue that is not plain": (("--venue", "a b", *CASH_IN), None, False),
    "a production venue": (("--venue", "webull-production", *CASH_IN), None, False),
    "a typed mark": ((*CASH_IN, "--mark", "SPY=1"), None, False),
    "who that is not plain": (("--owner-entry", "cash_in", "--amount", "1", "--who", "a\tb", "--note", "n"), None,
                              False),
    "--hash alone": ((*CASH_IN, "--hash", "0" * 64), None, False),
    "no kind": (("--confirm", "123456", "--who", "o", "--note", "n"), None, False),
    "a figure missing": (("--owner-entry", "cash_in", "--who", "o", "--note", "n"), None, False),
    "a figure not used": ((*CASH_IN, "--units", "1"), None, False),
    "a code not six digits": ((*CASH_IN, "--confirm", "12"), None, False),
    "a figure not a decimal": (("--owner-entry", "cash_in", "--amount", "1e3", "--who", "o", "--note", "n"), None,
                               False),
    "a symbol not plain": (("--owner-entry", "units_out", "--symbol", "a b", "--units", "1", "--who", "o", "--note",
                            "n"), None, False),
    "no New York day": (CASH_IN, lambda w, mp: setattr(w, "t", float("nan")), False),
    "a preview that cannot be read": ((*CASH_IN, "--confirm", "123456"), _junk_preview, False),
    "no preview pending": ((*CASH_IN, "--confirm", "123456"), None, False),
    "another code": ((*CASH_IN, "--confirm", "WRONG"), None, True),
    "expired": ((*CASH_IN, "--confirm", "CODE"), lambda w, mp: setattr(w, "t", w.t + osx.TICKET_LIFE_SECONDS + 5),
                True),
    "a clock before the preview": ((*CASH_IN, "--confirm", "CODE"), lambda w, mp: setattr(w, "t", w.t - 5), True),
    "a clock behind the record": (CASH_IN, lambda w, mp: setattr(w, "t", T0 - 900.0), False),
    "the state file and journal differ": (CASH_IN, lambda w, mp: _paper.Journal(w.root / "journal").write(
        "2025-11-18", {"type": "owner_entry", "venue": VENUE, "id": 9, "prev_hash": "p", "hash": "h"}), False),
    "a fund without a quote": (CASH_IN, lambda w, mp: w.prices.pop("QQQ"), False),
    "more than the book's cash": (("--owner-entry", "cash_out", "--amount", "999999999", "--who", "o", "--note",
                                   "n"), None, False),
    "a mark moved": ((*CASH_IN, "--confirm", "CODE"), lambda w, mp: w.prices.update(QQQ=w.prices["QQQ"] * 1.01),
                     True),
    "a run holding the lock": ((*CASH_IN, "--confirm", "CODE"), _lock_busy, True),
    "a newer preview in between": ((*CASH_IN, "--confirm", "CODE"),
                                   lambda w, mp: mp.setattr(osx, "_spend_preview",
                                                            lambda *a: "; a newer preview was made meanwhile"), True),
    "an entry in between": ((*CASH_IN, "--confirm", "CODE"), _intruder, True),
    "the preview cannot be written": (CASH_IN, lambda w, mp: mp.setattr(
        osx, "_write_private", lambda *a: (_ for _ in ()).throw(OSError("read-only folder"))), False),
    "a repair with a figure": (("--owner-entry-rejournal", "1", "--hash", "0" * 64, "--amount", "1", "--who", "o",
                                "--note", "n"), None, False),
    "a repair's number not one": (("--owner-entry-rejournal", "0", "--hash", "0" * 64, "--who", "o", "--note", "n"),
                                  None, False),
    "a repair's hash not one": (("--owner-entry-rejournal", "1", "--hash", "xyz", "--who", "o", "--note", "n"),
                                None, False),
    "a repair with no New York day": (("--owner-entry-rejournal", "1", "--hash", "0" * 64, "--who", "o", "--note",
                                       "n"), lambda w, mp: setattr(w, "t", float("inf")), False),
    "a repair with nothing missing": (("--owner-entry-rejournal", "1", "--hash", "0" * 64, "--who", "o", "--note",
                                       "n"), None, False),
}


def _run_case(w, monkeypatch, name):
    args, prep, preview_first = CATALOGUE[name]
    opened(w)
    venue = VENUE
    if args[:1] == ("--venue",):
        venue, args = args[1], args[2:]
    if preview_first:
        number, _ = w.preview(*CASH_IN)
        w.t += 1.0
        other = f"{(int(number) + 1) % 1_000_000:06d}"
        args = tuple(number if a == "CODE" else other if a == "WRONG" else a for a in args)
    with monkeypatch.context() as mp, contextlib.ExitStack() as stack:
        if prep is not None:
            undo = prep(w, mp)
            if callable(undo):
                stack.callback(undo)
        before = w.files()
        n_before = len(w.entries())
        code, _, err = w.cmd(*args, venue=venue)
        return code, err, before, n_before


@pytest.mark.parametrize("name", list(CATALOGUE))
def test_prop_every_refusal_leaves_the_state_file_the_journal_and_the_ledger_byte_identical(w, monkeypatch, name):
    code, err, before, n_before = _run_case(w, monkeypatch, name)
    assert code == 2 and err.startswith("refused:"), (code, err)
    if name == "an entry in between":                                   # the stand-in's own entry, and none of ours
        assert len(w.entries()) == n_before + 1 and w.entries()[-1]["note"] == "in between"
        assert {k: v for k, v in w.files().items() if k != w.state.name} == \
            {k: v for k, v in before.items() if k != w.state.name}
    else:
        assert w.files() == before


def test_prop_the_catalogue_reaches_every_refusal_the_code_has(tmp_path, monkeypatch, capsys):
    """The refusals are enumerated FROM THE CODE (every call of _owner_refuse in the command, its preview and its
    repair); each is reached by a case of the catalogue."""
    sites, hit = _sites(), set()
    real = osx._owner_refuse

    def spy(words, pending=None):
        line = sys._getframe(1).f_lineno
        hit.update(s for s in sites if s[0] <= line <= s[1])
        return real(words, pending)
    for i, name in enumerate(CATALOGUE):
        with monkeypatch.context() as mp:
            mp.setattr(osx, "_owner_refuse", spy)
            w = World(tmp_path / f"r{i}", mp, capsys)
            code, err, _, _ = _run_case(w, mp, name)
            assert code == 2, (name, err)
    assert len(sites) >= 30 and sites - hit == set(), sorted(sites - hit)


def test_the_repair_is_only_for_the_last_entry_even_when_that_is_the_only_difference(w, monkeypatch):
    """Entry 2 of 3 missing from the journal is the ONE difference; it is still not the last entry: refused."""
    row = _failed(w, monkeypatch, n_entries=3)
    assert w.cmd("--owner-entry-rejournal", str(row["id"]), "--hash", row["hash"], "--who", "o", "--note", "n")[0] == 0
    p = _day_file(w)
    kept = [x for x in p.read_text().splitlines() if not (json.loads(x).get("type") == "owner_entry"
                                                          and json.loads(x).get("id") == 2)]
    p.write_text("\n".join(kept) + "\n")
    second = w.entries()[1]
    assert osx.owner_entry_differences(w.entries(), _paper.Journal(w.root / "journal").read(), venue=VENUE) == \
        ["owner entry 2 is in the state file and not in the journal"]
    before = w.files()
    code, _, err = w.cmd("--owner-entry-rejournal", "2", "--hash", second["hash"], "--who", "o", "--note", "n")
    assert code == 2 and "only the LAST entry" in err and w.files() == before, err
