"""T51c S4d (addendum 7: "live always shared, live entries with --live, lock 1"): the shared-account book on the LIVE venue.

What a live run is now, and what these tests hold it to:
- a live run is always the shared account's book: `--live` without `--shared` is refused in words before anything is
  read, `--capital` is refused with it (the capital is the book's own value), and a live run never reads or writes the
  old `equity` table (its peak is the book's own `book_nav` figures, worked out in Decimal);
- the owner's entry command takes a production venue's name only together with `--live` (R23 reversed for `--live`
  only), with everything R29 says (print first, `--confirm CODE`, marks from the quote path);
- lock 1 (R26): a live run refuses, before any order, when the book's value (the UPPER bound) is above max_capital;
- every guarantee of the practice shared run holds on the live path: the shortfall (R24), the latch (R25), the marks
  (R28), every limit on her book alone (R31), the ticket's value (R26), the day stop on the value per unit (R27).
Everything runs on stand-in venues, quotes and clocks in a temporary folder; nothing opens a socket."""
import argparse
import json
import re
import socket
import sqlite3
from decimal import Decimal as D

import pytest

import test_order_step as T
from test_shared_run import Lag, NoFee, SW, refusals, sent_units
from valedia_trader.execution_and_risk import go_live as gl
from valedia_trader.execution_and_risk import order_step as osx
from valedia_trader.execution_and_risk import stake_ladder
from valedia_trader.research_loop import cli
from valedia_trader.venues import webull as _wb

VENUE = "webull-production"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise OSError("the T51c S4d tests open no socket")
    for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "create_connection"),
                        (socket, "getaddrinfo")):
        monkeypatch.setattr(owner, name, refuse)


class LiveLag(Lag, T.LiveVenue):
    """The live stand-in whose positions read can lag, fail or lie, as Lag's do."""


class LiveNoFee(NoFee, T.LiveVenue):
    """The live stand-in whose purchases report no fee (empty, not zero): bounded by R22."""


class LW(SW):
    """The shared world, on the live venue: the owner's files under config/, the repository this folder, the second
    price source, the follow list; the owner's command is run with --live."""

    def __init__(self, tmp, monkeypatch, capsys, venue=None, conf=None, **limits):
        monkeypatch.setattr(_wb, "LIVE_REPO", tmp, raising=False)
        super().__init__(tmp, monkeypatch, capsys, venue if venue is not None else T.LiveVenue(), **limits)
        self.cfg = tmp / "config"
        self.write_conf(**(conf or {}))
        (self.cfg / "go_live.json").write_text(json.dumps(T.LIVE_CHECKLIST))
        (self.cfg / "go_live.json").chmod(0o644)
        T._paper_record(self.journal)
        self.live = gl.LiveFiles(environ=dict(T.LIVE_ENV), calendar=T.CAL)
        self.second = T.Second()

    def set_limits(self, **kw):                           # the live limits file is the repository's own config/risk.json
        cfg = self.tmp / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        self.limits = cfg / "risk.json"
        self.limits.write_text(json.dumps({**T.LIVE_LIMITS, **kw}))
        self.limits.chmod(0o644)

    def write_conf(self, **over):
        conf = T.live_conf(**{"max_order_value": 20000, "max_capital": 20000, **over})
        (self.cfg / "webull_live.json").write_text(json.dumps(conf))
        (self.cfg / "webull_live.json").chmod(0o644)

    def owner_args(self, kind, *figures):
        return ["--root", str(self.tmp_root), "order-step", "--live", "--venue", self.venue.name, "--owner-entry", kind,
                *figures, "--who", "owner", "--note", "test"]

    def owner(self, kind, *figures):
        self.capsys.readouterr()
        base = self.owner_args(kind, *figures)
        assert cli.main(base) == 0, self.capsys.readouterr().err
        (code,) = re.findall(r"^code: ([0-9]{6})$", self.capsys.readouterr().out, re.M)
        self.clock.t += 1.0
        assert cli.main([*base, "--confirm", code]) == 0, self.capsys.readouterr().err
        self.capsys.readouterr()
        self.clock.t += 1.0

    def live_kw(self, kw):
        kw.setdefault("live", self.live)
        kw.setdefault("second_opinion", self.second)
        if "follow" not in kw:
            kw["follow"] = T._follow_the_book(self)
        return kw

    def lshared(self, **kw):
        """A live shared run: it needs propose or approve, never a capital."""
        return self.run(shared=True, **self.live_kw(kw))

    def propose(self, **kw):
        return self.lshared(propose=True, **kw)

    def approve(self, number, **kw):
        return self.lshared(approve=number, **kw)

    def buy(self):
        """Propose and approve one purchase of the book's own: returns the approval's summary."""
        out = self.propose()
        assert out["status"] == "done" and out["ticket"]["orders"], out
        self.later(1)
        done = self.approve(out["ticket_number"])
        assert done["status"] == "done" and done["orders_sent"] == 1, done
        self.later(1)
        return done

    def journal_events(self, event=None):
        return T.World.entries(self, event)

    def tree(self):
        return {str(p.relative_to(self.tmp)): p.read_bytes() for p in sorted(self.tmp.rglob("*")) if p.is_file()}


@pytest.fixture
def mkl(tmp_path, monkeypatch, capsys):
    count = []

    def make(venue=None, conf=None, **limits):
        count.append(1)
        return LW(tmp_path / f"w{len(count)}", monkeypatch, capsys, venue, conf, **limits)
    return make


@pytest.fixture
def lw(mkl):
    return mkl()


def ns(**kw):
    base = dict(live=False, check=False, shared=False, fractional=False, capital=None, propose=False, approve=None)
    base.update(kw)
    return argparse.Namespace(**base)


# ---- outcome 1: a live run is always shared; no capital; no equity table

def test_live_without_shared_is_refused_in_words_and_a_capital_with_it(lw):
    for ticket in ({"propose": True}, {"approve": "123456"}):
        why = cli._live_args_refusal(ns(live=True, **ticket))
        assert why and "--shared" in why and "always" in why, why
        why = cli._live_args_refusal(ns(live=True, capital=1000.0, **ticket))      # the old habit: a capital, no flag
        assert why and "--shared" in why, why
        assert cli._live_args_refusal(ns(live=True, shared=True, **ticket)) is None
        why = cli._live_args_refusal(ns(live=True, shared=True, capital=1000.0, **ticket))
        assert why and "--capital" in why and "book" in why, why
    assert "--shared" in cli._live_args_refusal(ns(live=True))                       # neither a ticket flag nor --shared
    assert cli._live_args_refusal(ns(live=True, shared=True, fractional=True, propose=True)) is None    # T51 B3: lifted
    assert cli._live_args_refusal(ns(live=True, shared=True, fractional=True, approve="123456")) is None
    assert "--shared" in cli._live_args_refusal(ns(live=True, fractional=True, propose=True))             # the others stay


def test_check_takes_no_fractional_as_before_t51_b():
    """4db3192 refused `--live --check --fractional`; the flag is not silently ignored."""
    for kw in ({}, {"capital": 1000.0}):
        why = cli._live_args_refusal(ns(live=True, check=True, fractional=True, **kw))
        assert why and "--check" in why and "--fractional" in why, why
    assert cli._live_args_refusal(ns(live=True, check=True)) is None


def test_check_is_as_it_was_and_shared_with_check_is_refused():
    assert cli._live_args_refusal(ns(live=True, check=True)) is None
    assert cli._live_args_refusal(ns(live=True, check=True, capital=1000.0)) is None
    assert "--shared" in cli._live_args_refusal(ns(live=True, check=True, shared=True))
    assert cli._live_args_refusal(ns(check=True, shared=True)) is not None


def test_the_command_line_refuses_live_without_shared_before_anything_is_made(tmp_path, capsys):
    root = tmp_path / "empty"
    root.mkdir()
    for argv in (["--live", "--propose"], ["--live", "--propose", "--capital", "1000"], ["--live", "--approve", "123456"],
                 ["--live", "--shared", "--propose", "--capital", "1000"]):
        assert cli.main(["--root", str(root), "order-step", *argv]) == 2
        err = capsys.readouterr().err
        assert err.startswith("refused:") and "--shared" in err or "--capital" in err, err
        assert list(root.iterdir()) == []


def test_the_live_command_hands_the_run_shared_and_no_capital_and_prints_a_shared_approve(tmp_path, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(osx, "run_order_step", lambda **kw: (seen.update(kw), {"status": "done", "ticket_number": "123456"})[1])
    monkeypatch.setattr(cli, "broker_book_follow", lambda *a, **k: "FOLLOW")
    paths = cli.LoopPaths(tmp_path)
    paths.ensure()
    assert cli._run_live(ns(live=True, shared=True, propose=True), paths, T.LiveVenue(), "FILES", None) == 0
    assert seen["shared"] is True and seen["capital"] is None and seen["live"] == "FILES" and seen["fractional"] is False
    printed = capsys.readouterr().out
    assert "--live --shared --approve NUMBER" in printed and "--capital" not in printed, printed
    assert seen["practice_ledger"] == osx.ledger_path_for(paths.memory, gl.PAPER_VENUE)     # T51 B1: where the proof is read
    plain = printed
    seen.clear()                                                                               # T51 B3: --fractional goes through
    for block, flagged in (({"open": True}, True), ({"open": False, "why": "no consent"}, False), (None, False)):
        out = {"status": "done", "ticket_number": "123456", **({} if block is None else {"live_fractions": block})}
        monkeypatch.setattr(osx, "run_order_step", lambda **kw: (seen.update(kw), dict(out))[1])
        assert cli._run_live(ns(live=True, shared=True, propose=True, fractional=True), paths, T.LiveVenue(), "FILES", None) == 0
        assert seen["fractional"] is True and seen["shared"] is True and seen["capital"] is None
        printed = capsys.readouterr().out
        hint = [ln for ln in printed.splitlines() if ln.startswith("ticket number:")]
        assert ("--live --shared --fractional --approve NUMBER" in printed) is flagged and "--capital" not in printed, printed
        if not flagged:                                   # T51 B repair2 (3): exactly what the run without the flag prints
            assert hint == [ln for ln in plain.splitlines() if ln.startswith("ticket number:")], (hint, plain)


def test_a_live_shared_run_given_a_capital_is_refused_before_the_lock(lw):
    lw.open_book("10000")
    mine = lambda: {k: v for k, v in lw.tree().items() if k.startswith("journal/") or k == f"memory/{lw.state_path.name}"}
    before = mine()
    lock = osx._RunLock(str(lw.state_path))
    assert lock.acquire()                                 # a refusal AFTER the lock would say another run holds it
    try:
        out = lw.propose(capital=5000.0)
    finally:
        lock.release()
    assert out["status"] == "stopped" and out["stage"] == "targets" and "--capital" in out["reason"], out
    assert lw.venue.calls == [] and lw.quote_calls == [] and lw.second.calls == [] and mine() == before


def test_a_live_shared_run_never_reads_or_writes_the_equity_table(lw, monkeypatch):
    lw.open_book("10000")
    lw.sql("INSERT INTO equity (day, time, equity) VALUES ('2026-11-12', 1.0, 1000000000.0)")   # a peak that would trip it

    def never(self):
        raise AssertionError("the equity table's peak was read by a live shared run")
    monkeypatch.setattr(osx.OrderStepState, "peak", never)
    monkeypatch.setattr(osx.OrderStepState, "previous_equity", lambda self, day: never(self))
    out = lw.buy()
    assert out["status"] == "done" and out["refused"] == [] and sent_units(lw) == {"SPY": 10.0}, out
    assert lw.sql("SELECT COUNT(*) AS n FROM equity") == [{"n": 1}]                          # written by nothing
    assert len(lw.nav()) == 2 and out["live"] is True and out["shared"]["capital"] > 0, lw.nav()


# ---- outcome 2: the owner's entry for the live venue, only with --live

def test_a_production_venue_name_is_refused_without_live_writing_nothing(lw, monkeypatch):
    monkeypatch.setattr(osx, "minute_quotes", lambda symbols: pytest.fail("a quote was asked"))
    before = lw.tree()
    base = lw.owner_args("cash_in", "--amount", "10000")
    base.remove("--live")
    assert cli.main(base) == 2
    err = lw.capsys.readouterr().err
    assert "production venue" in err and "--live" in err and "nothing was written" in err, err
    assert lw.tree() == before and not lw.state_path.exists()


def test_live_with_a_name_that_is_not_a_production_venue_is_refused(lw):
    base = lw.owner_args("cash_in", "--amount", "10000")
    base[base.index("--venue") + 1] = "webull-sandbox"
    before = lw.tree()
    assert cli.main(base) == 2
    err = lw.capsys.readouterr().err
    assert "--live" in err and "production venue" in err and "nothing was written" in err, err
    assert lw.tree() == before
    for flag in (("--propose",), ("--capital", "1"), ("--shared",), ("--fractional",), ("--check",)):
        assert cli.main([*lw.owner_args("cash_in", "--amount", "10000"), *flag]) == 2, flag
        assert "nothing was written" in lw.capsys.readouterr().err
    assert lw.tree() == before


def test_the_live_entry_prints_first_and_writes_only_when_confirmed(lw):
    base = lw.owner_args("cash_in", "--amount", "10000")
    lw.capsys.readouterr()
    assert cli.main(base) == 0
    out = lw.capsys.readouterr().out
    (code,) = re.findall(r"^code: ([0-9]{6})$", out, re.M)
    assert "value per unit" in out and not lw.state_path.exists(), out        # a preview writes no byte of the state file
    assert [e for e in lw.journal.read() if e.get("type") == "owner_entry"] == []
    lw.clock.t += 1.0
    assert cli.main([*base, "--confirm", code]) == 0, lw.capsys.readouterr().err
    (entry,) = lw.entries()
    assert entry["kind"] == "cash_in" and D(entry["amount"]) == 10000
    (line,) = [e for e in lw.journal.read() if e.get("type") == "owner_entry"]
    assert line["venue"] == VENUE and line["hash"] == entry["hash"]
    assert lw.state_path.name == "order_step-webull-production.sqlite"
    assert cli.main([*base, "--confirm", code]) == 2                          # the preview is spent: no code is tried twice


def test_the_live_entrys_marks_come_from_the_quote_path_and_a_moved_mark_refuses_the_confirm(lw):
    lw.open_book("10000")
    lw.buy()                                              # the book now holds 10 SPY
    base = lw.owner_args("cash_in", "--amount", "100")
    lw.capsys.readouterr()
    assert cli.main(base) == 0
    out = lw.capsys.readouterr().out
    (code,) = re.findall(r"^code: ([0-9]{6})$", out, re.M)
    assert "SPY" in out and "500" in out, out             # the mark: the order step's own quote, with its time
    assert cli.main([*base, "--mark", "SPY=1"]) == 2 and "--mark is not taken" in lw.capsys.readouterr().err
    lw.prices["SPY"] *= 1.01                              # beyond TICKET_QUOTE_MOVE
    lw.clock.t += 1.0
    before = len(lw.entries())
    assert cli.main([*base, "--confirm", code]) == 2
    assert "more than" in lw.capsys.readouterr().err and len(lw.entries()) == before


def test_the_live_repair_needs_live_too_and_writes_the_missing_line(lw):
    lw.open_book("10000")
    (entry,) = lw.entries()
    day = entry["day"]
    path = lw.tmp / "journal" / f"{day}.jsonl"
    path.write_text("")                                   # the witness is gone
    form = ["--root", str(lw.tmp_root), "order-step", "--venue", VENUE, "--owner-entry-rejournal", "1", "--hash",
            entry["hash"], "--who", "owner", "--note", "repair"]
    assert cli.main(form) == 2
    assert "production venue" in lw.capsys.readouterr().err and path.read_text() == ""
    assert cli.main([*form[:3], "--live", *form[3:]]) == 0, lw.capsys.readouterr().err
    (line,) = [e for e in lw.journal.read() if e.get("type") == "owner_entry"]
    assert line["hash"] == entry["hash"] and "rejournaled" in line


# ---- outcome 3: lock 1 against the book's value (the upper bound)

def test_lock_one_refuses_a_live_run_when_the_books_value_is_above_max_capital(lw):
    lw.write_conf(max_order_value=5000, max_capital=5000)
    lw.open_book("10000")
    lw.venue.calls.clear()
    for mode in ({"propose": True},):
        out = lw.lshared(**mode)
        assert out["status"] == "stopped" and out["stage"] == "lock", out
        for words in ("lock 1", "max_capital", "10,000.00", "5,000.00", "nothing is sent"):
            assert words in out["reason"], (words, out["reason"])
        assert out["stage"] in stake_ladder.SKIP_STAGES and "faults" not in out
        assert out["ceiling"] == 5000.0 and out["shared"]["book_value_high"] == 10000.0, out
        assert lw.journal_events("summary")[-1]["reason"] == out["reason"]            # it reaches the journaled summary too
    assert lw.venue.submits() == [] and lw.orders() == [] and "ticket_number" not in out
    assert lw.nav() == [] and not any(c[0] == "submit" for c in lw.venue.calls)


def test_lock_one_judges_the_upper_bound_and_a_value_exactly_at_it_passes(mkl):
    w = mkl(LiveNoFee())
    w.open_book("10000")
    w.buy()                                               # 10 SPY bought at 505, marked 500; its fee empty: bounded (R22)
    row = w.nav()[-1]
    assert D(row["value_low"]) == D("9949.98") and D(row["value_high"]) == D("9950.00"), row
    w.write_conf(max_order_value=5000, max_capital=9949.99)                      # between the two bounds
    out = w.propose()
    assert out["status"] == "stopped" and out["stage"] == "lock" and "9,950.00" in out["reason"], out
    w.write_conf(max_order_value=5000, max_capital=9950.0)                       # exactly the upper bound
    w.later()
    out = w.propose()
    assert out["status"] == "done" and out["stage"] is None, out


def test_lock_one_refuses_when_the_upper_value_is_not_known(lw, monkeypatch):
    lw.open_book("10000")
    real = osx.book_figures

    def open_above(*a, **k):
        import dataclasses
        return dataclasses.replace(real(*a, **k), value_high=None)
    monkeypatch.setattr(osx, "book_figures", open_above)
    out = lw.propose()
    assert out["status"] == "stopped" and out["stage"] == "lock" and "upper" in out["reason"], out


# ---- outcome 4: the peak is the book's, in Decimal

def test_a_state_file_without_the_book_nav_rows_the_journal_saw_stops_the_live_run(lw):
    lw.open_book("10000")
    lw.buy()
    assert lw.journal_events("summary")[-1]["shared"]["value_per_unit"]
    lw.sql("DELETE FROM book_nav")                        # an older state file put back
    lw.later()
    lw.venue.calls.clear(), lw.quote_calls.clear(), lw.second.calls.clear()
    out = lw.propose()
    assert out["status"] == "stopped" and out["stage"] == "state", out
    assert "value history the journal holds" in out["reason"] and "a person must look" in out["reason"], out["reason"]
    assert lw.venue.calls == [] and lw.quote_calls == [] and lw.second.calls == []     # before anything is read


def _look_line(low, high=None, day_figures=True, batch=None):
    """The `shared` block of a journaled summary as a run writes it: the lower value per unit and, from S4d, the upper."""
    block = {} if low is None else {"value_per_unit": low}
    if day_figures and low is not None:
        block["value_per_unit_high"] = high
    line = {"type": "order_step", "venue": VENUE, "event": "summary", "status": "done", "shared": block}
    return line if batch is None else {**line, "batch": batch}


def journal_look(w, day, low, high="same", with_high=True, batch=None):
    w.journal.write(day, _look_line(low, low if high == "same" else high, with_high, batch))


def history(w, rows):
    """A state file and a journal that agree: each (day, low, high) is a book_nav row and the journal's summary of it."""
    for day, low, high in rows:
        w.seed_nav(day, low, high)
        journal_look(w, day, low, high or low)


def test_the_state_files_peak_is_compared_in_decimal_never_as_text(lw):
    lw.open_book("10000")
    history(lw, [("2026-11-11", "10.5", None), ("2026-11-12", "9.5", None)])   # as text "9.5" is above "10.5"
    out = lw.propose()
    assert out["status"] == "done" and out["stage"] is None, out              # a text MAX over the rows would stop it


def test_the_peak_is_the_upper_figure_where_it_is_finite_not_only_the_lower(mkl):
    w = mkl()
    w.open_book("10000")
    journal_look(w, "2026-11-12", "1.00", "1.20")        # an earlier run of the day looked at 1.00 to 1.20 ...
    journal_look(w, "2026-11-12", "1.00", "1.00")        # ... a later one at exactly 1.00
    w.seed_nav("2026-11-12", "1.00", "1.00")             # the state file kept the later row only: the lower peak is 1.00
    out = w.propose()
    assert out["status"] == "stopped" and out["stage"] == "state", out
    assert "1.20" in out["reason"] and "drawdown" in out["reason"] and w.venue.calls == [], out
    ok = mkl()                                           # with the earlier row kept, as it would be, it goes on
    ok.open_book("10000")
    ok.seed_nav("2026-11-12", "1.00", "1.20")
    journal_look(ok, "2026-11-12", "1.00", "1.20")
    assert ok.propose()["status"] == "done"


def _three_days(w):
    w.open_book("10000")
    history(w, [("2026-11-10", "1.05", None), ("2026-11-11", "1.00", None), ("2026-11-12", "1.05", None)])


def test_a_later_days_row_removed_from_the_state_file_stops_the_run_and_a_clean_history_goes_on(mkl):
    w = mkl()
    _three_days(w)
    out = w.propose()                                     # the history is whole: yesterday's 1.05 is the previous figure
    assert out["status"] == "done" and out["shared"]["previous_value_per_unit"] == "1.05", out
    assert out["day_stop"]["pct"] == pytest.approx(100 * 0.05 / 1.05, rel=1e-9), out
    w2 = mkl()                                            # the same journal, the 11-12 row gone: an older copy put back
    _three_days(w2)
    w2.sql("DELETE FROM book_nav WHERE day = '2026-11-12'")
    peak = w2.sql("SELECT MAX(CAST(value_per_unit AS REAL)) AS m FROM book_nav")[0]["m"]
    assert peak == 1.05                                    # the highest figure is still there: the peak check alone passes
    out = w2.propose()
    assert out["status"] == "stopped" and out["stage"] == "state", out
    for words in ("lacks the book's value history the journal holds", "a copy put back?", "2026-11-12"):
        assert words in out["reason"], (words, out["reason"])
    assert out["stage"] not in stake_ladder.SKIP_STAGES and w2.venue.calls == [] and "day_stop" not in out


def test_a_row_that_is_there_but_changed_stops_the_run(mkl):
    for column, value in (("value_per_unit", "1.01"), ("value_high", "104000"), ("value_high", None)):
        w = mkl()
        _three_days(w)
        w.sql(f"UPDATE book_nav SET {column} = ? WHERE day = '2026-11-11'", (value,))
        out = w.propose()
        assert out["status"] == "stopped" and out["stage"] == "state" and "2026-11-11" in out["reason"], (column, out)
        assert w.venue.calls == []


def test_only_the_latest_row_of_a_day_is_compared_and_a_day_without_a_figure_is_not_required(mkl):
    w = mkl()
    _three_days(w)
    w.seed_nav("2026-11-12", "1.05", None)               # a later run of the day wrote another row: the latest row is read
    w.sql("UPDATE book_nav SET value_per_unit = '0.5', value_low = '50000', value_high = '50000' WHERE day = '2026-11-12' "
          "AND id = (SELECT MIN(id) FROM book_nav WHERE day = '2026-11-12')")      # ... and an earlier row of that day is not
    w.journal.write("2026-11-12", _look_line(None))      # a run that stopped before its first look: no figure to hold
    w.journal.write("2026-11-09", _look_line(None))      # a day with no figure at all: no row is required of it
    w.journal.write("2026-11-08", {"type": "order_step", "venue": VENUE, "event": "summary", "status": "stopped"})
    out = w.propose()
    assert out["status"] == "done" and out["stage"] is None, out


def test_a_day_the_journal_holds_a_figure_for_must_have_a_row_whatever_else_is_in_the_state_file(mkl):
    w = mkl()
    w.open_book("10000")
    journal_look(w, "2026-11-09", "1.00")                # the journal saw 11-09; the state file has only a later row
    w.seed_nav("2026-11-12", "1.00", None)
    out = w.propose()
    assert out["status"] == "stopped" and out["stage"] == "state" and "2026-11-09" in out["reason"], out


def test_a_real_runs_summary_carries_the_upper_figure_the_state_file_reads_back(lw):
    lw.open_book("10000")
    out = lw.buy()
    shared = out["shared"]
    assert D(shared["value_per_unit_high"]) == D(lw.nav()[-1]["value_high"]) / D(lw.nav()[-1]["units_outstanding"])
    assert lw.journal_events("summary")[-1]["shared"]["value_per_unit_high"] == shared["value_per_unit_high"]
    assert lw.propose()["status"] == "done"              # the history the runs wrote is whole: the next run goes on
    lw.later()
    lw.sql("UPDATE book_nav SET value_high = NULL WHERE id = (SELECT MAX(id) FROM book_nav)")   # its upper figure gone
    out = lw.propose()
    assert out["status"] == "stopped" and out["stage"] == "state" and "upper" in out["reason"], out


def test_a_live_ticket_event_and_an_unreadable_figure_do_not_stop_a_state_that_knows_what_the_summaries_say(lw):
    lw.open_book("10000")
    lw.journal.write("2026-11-12", {"type": "order_step", "venue": VENUE, "event": "ticket", "equity": 99999999.0})
    lw.journal.write("2026-11-12", {"type": "order_step", "venue": VENUE, "event": "summary", "status": "done",
                                    "broker_equity": 99999999.0, "shared": {"value_per_unit": "not a number"}})
    lw.journal.write("2026-11-12", {"type": "order_step", "venue": "webull-sandbox", "event": "summary",
                                    "shared": {"value_per_unit": "99"}})
    out = lw.propose()
    assert out["status"] == "done" and out["stage"] is None, out


# ---- outcome 5: every shared guarantee holds on the live path

def test_a_live_shortfall_is_a_skip_with_its_words_and_clears_by_the_owners_entry(mkl):
    w = mkl(LiveLag())
    w.open_book("10000")
    w.buy()
    w.venue.held["SPY"] -= 3.0                            # the owner sells 3 of the 10 SPY the book counts as its own
    w.later()
    w.venue.calls.clear()
    out = w.propose()
    assert out["status"] == "stopped" and out["stage"] == "shortfall", out
    for words in ("SPY", "7.0", "10.0", "the owner sold shares the book counts as its own", "a split"):
        assert words in out["reason"], (words, out["reason"])
    assert out["shared"]["shortfalls"]["SPY"]["short"] == 3.0 and "faults" not in out and "submit" not in w.venue.names()
    done, faulty, notes = stake_ladder._judge_summary(out)
    assert not done and faulty == [] and notes and w.blocked()
    w.owner("units_out", "--symbol", "SPY", "--units", "3")                        # the owner's entry, with --live
    w.later()
    out = w.propose()
    assert out["status"] == "done" and out["stage"] is None and "shortfalls" not in out["shared"], out


def test_a_live_purchase_whose_position_the_venue_reports_late_is_set_aside_not_a_fault(mkl):
    w = mkl(LiveLag())
    w.open_book("10000")
    out = w.propose()
    w.later(1)
    w.venue.lag_after_submit = {"SPY": 0.0}
    out = w.approve(out["ticket_number"])
    assert out["status"] == "done" and out["orders_sent"] == 1 and "faults" not in out, out
    assert out["reconcile_pending"]["symbols"] == ["SPY"], out


def test_the_state_files_latch_refuses_a_live_run_that_is_not_shared_and_a_shared_one_needs_its_opening_entry(lw):
    out = lw.lshared(propose=True)
    assert out["status"] == "stopped" and out["stage"] == "targets" and "opening entry" in out["reason"], out
    assert lw.venue.calls == [] and out["stage"] in stake_ladder.SKIP_STAGES
    lw.open_book("10000")
    out = lw.run(**lw.live_kw({"propose": True, "capital": 5000.0}))              # not shared: the state holds an entry
    assert out["status"] == "stopped" and out["stage"] == "state" and "--shared" in out["reason"], out
    assert lw.venue.calls == [] and lw.sql("SELECT COUNT(*) AS n FROM equity") == [{"n": 0}]


def test_a_held_fund_far_from_its_last_mark_stops_the_live_run_at_marks_before_any_order(mkl):
    w = mkl(max_drawdown_pct=8.0)
    w.open_book("10000")
    w.buy()
    w.prices["SPY"] = 700.0
    w.later()
    w.venue.calls.clear()
    out = w.propose()
    assert out["status"] == "stopped" and out["stage"] == "marks", out
    assert out["stage"] in stake_ladder.SKIP_STAGES and "submit" not in w.venue.names() and "ticket_number" not in out


def test_every_live_limit_is_judged_on_her_book_alone_and_the_owners_funds_are_never_priced_or_asked(mkl):
    w = mkl(T.LiveVenue(held={"SPY": 7.0, "OWN": 3.0}), max_position_pct=0.5)
    w.open_book("10000")                                  # 5,000 of a book worth 10,000: exactly the 10 SPY wanted
    out = w.propose()
    assert out["status"] == "done" and out["refused"] == [] and len(out["ticket"]["orders"]) == 1, out
    w.later(1)
    out = w.approve(out["ticket_number"])
    assert out["status"] == "done" and out["refused"] == [] and sent_units(w) == {"SPY": 10.0}, out
    assert {c["symbol"] for c in w.second.calls} == {"SPY"}                       # the owner's OWN is never second-sourced
    assert all("OWN" not in call for call in w.quote_calls), w.quote_calls        # ... nor priced


def test_the_count_of_positions_is_the_books_on_the_live_path(mkl):
    w = mkl(T.LiveVenue(held={"OWN": 3.0, "TLT": 2.0, "GLD": 1.0, "IWM": 4.0}), max_open_positions=1)
    w.open_book("10000")
    out = w.propose()
    assert out["status"] == "done" and out["refused"] == [] and len(out["ticket"]["orders"]) == 1, out


def test_the_day_stop_trips_on_the_loss_of_the_value_per_unit(lw):
    lw.open_book("10000")
    lw.seed_nav("2026-11-12", "1.05", units="10000")      # yesterday the unit was worth 1.05; today the book is at 1.0
    journal_look(lw, "2026-11-12", "1.05")
    out = lw.propose()
    assert out["status"] == "done" and out["day_stop"]["pct"] == pytest.approx(100 * 0.05 / 1.05, rel=1e-9), out
    assert out["day_stop"]["limit"] == 1.0 and out["day_stop"]["wall"] is False, out
    assert [r[1] for r in refusals(out)] == ["day stop"] and out["orders_sent"] == 0 and "ticket_number" not in out, out
    assert lw.journal_events("summary")[-1]["day_stop"] == out["day_stop"]
    assert lw.journal_events("day_stop"), "the trip is journaled at once"


def test_the_owners_deposits_and_withdrawals_are_no_loss_to_the_live_day_stop(lw):
    lw.open_book("10000")
    lw.seed_nav("2026-11-12", "1.0", units="10000")
    journal_look(lw, "2026-11-12", "1.0")
    lw.owner("cash_out", "--amount", "5000")              # half the book's money leaves: not a loss
    out = lw.propose()
    assert out["status"] == "done" and "day_stop" not in out and out["refused"] == [], out
    assert [o["fund"] for o in out["ticket"]["orders"]] == ["SPY"]


def test_a_live_ticket_stores_the_books_value_and_both_marks_and_an_approval_recomputes_it(lw):
    lw.open_book("10000")
    out = lw.propose()
    number = out["ticket_number"]
    (row,) = lw.sql("SELECT * FROM tickets", path=osx.ticket_path_for(lw.state_path))
    assert row["capital"] == pytest.approx(10_000.0) and out["orders_sent"] == 0, row
    meta = {r["key"]: json.loads(r["value"]) for r in lw.sql("SELECT key, value FROM meta",
                                                            path=osx.ticket_path_for(lw.state_path))
            if r["key"].startswith(("live:", "shared:"))}
    assert meta[f"shared:2026-11-13:{number}"] == {"mode": "shared"} and meta[f"live:2026-11-13:{number}"]["mode"] == "live"
    lw.owner("cash_in", "--amount", "20")                 # 0.2% more: inside TICKET_QUOTE_MOVE
    lw.later(1)
    done = lw.approve(number)
    assert done["status"] == "done" and done["orders_sent"] == 1 and done["live"] is True, done


def test_a_live_approval_refuses_a_book_value_that_moved_beyond_the_band(lw):
    lw.open_book("10000")
    number = lw.propose()["ticket_number"]
    lw.owner("cash_in", "--amount", "1000")               # 10% more than the ticket's value
    lw.later(1)
    lw.venue.calls.clear()
    out = lw.approve(number)
    assert out["status"] == "stopped" and out["stage"] == "ticket" and "book value" in out["reason"], out
    assert "10,000.00" in out["reason"] and "11,000.00" in out["reason"], out["reason"]
    assert out["orders_sent"] == 0 and "submit" not in lw.venue.names() and out["stage"] in stake_ladder.SKIP_STAGES


def test_a_live_shared_approval_sends_within_the_confirmations_caps_and_asks_the_second_source(lw):
    lw.write_conf(max_order_value=5000, max_capital=20000)       # the order's value 5,050 at its limit is above 5,000
    lw.open_book("10000")
    out = lw.propose()
    assert out["status"] == "done" and out["ticket"]["orders"] == [] and "ticket_number" not in out, out
    assert [r[1] for r in refusals(out)] == [] and out["dropped"], out
    assert "max_order_value" in json.dumps(out["dropped"]), out["dropped"]
    lw.write_conf(max_order_value=5100, max_capital=20000)
    lw.later()
    out = lw.buy()
    assert out["status"] == "done" and sent_units(lw) == {"SPY": 10.0} and lw.second.calls, out


def test_the_live_summary_says_the_book_not_the_account(lw):
    lw.open_book("10000")
    out = lw.buy()
    assert out["broker_equity"] == pytest.approx(out["shared"]["book_value"]) and out["live"] is True
    assert out["shared"]["capital"] == pytest.approx(10_000.0) and out["ceiling"] == 20000.0, out
    assert "over_ceiling" not in out


def test_the_live_ceiling_look_counts_the_books_units_after_the_owners_entries_not_the_raw_fills(lw):
    lw.open_book("10000")
    lw.buy()                                              # 10 SPY in the ledger's fills
    lw.venue.held["SPY"] -= 10.0                          # the owner takes all ten ...
    lw.owner("units_out", "--symbol", "SPY", "--units", "10")   # ... and says so: the book holds none
    lw.paper = T.paper({"a": {"QQQ": 10.0}})              # SPY is neither held nor wanted: never quoted, never marked
    lw.later()
    out = lw.propose()
    assert out["status"] == "done" and "cap_unjudged" not in out and "over_ceiling" not in out, out


# ---- T51c S4d repair round 2: a summary that never reached the journal, and the owner's way out of it

def _lost(w, day="2026-11-12", low="1.07", batch="lost-run"):
    """A run of `day` wrote its book_nav row and its summary never reached the journal: a row the journal does not know."""
    w.seed_nav(day, low, None)
    w.sql("UPDATE book_nav SET batch = ? WHERE id = (SELECT MAX(id) FROM book_nav)", (batch,))


def _summaries_have_batches(w):
    """The seeded summaries of `_three_days` belong to runs whose rows carry the same batch names."""
    for day in ("2026-11-10", "2026-11-11", "2026-11-12"):
        w.sql("UPDATE book_nav SET batch = ? WHERE day = ?", (f"run-{day}", day))
    for path in sorted((w.tmp / "journal").glob("*.jsonl")):
        lines = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
        for line in lines:
            if line.get("event") == "summary" and line.get("venue") == VENUE:
                line["batch"] = f"run-{line['day']}"
        path.write_text("".join(json.dumps(x) + "\n" for x in lines))


def accept(w, day, *extra, confirm=None, venue=VENUE, live=True):
    """The owner's command that accepts the state file's latest row of `day` as that day's figure."""
    argv = ["--root", str(w.tmp_root), "order-step", *(["--live"] if live else []), "--accept-history", day, "--venue",
            venue, "--who", "owner", "--note", "a summary was lost", *extra]
    if confirm is not None:
        argv += ["--confirm", confirm]
    w.capsys.readouterr()
    code = cli.main(argv)
    captured = w.capsys.readouterr()
    return code, captured.out, captured.err


def accepted(w, day):
    """Preview, then confirm: the accepted day's figure is now the state file's row."""
    code, out, err = accept(w, day)
    assert code == 0, err
    (number,) = re.findall(r"^code: ([0-9]{6})$", out, re.M)
    w.clock.t += 1.0
    code, out, err = accept(w, day, confirm=number)
    assert code == 0, err
    return out


def test_the_stop_words_name_both_causes_the_days_and_the_figures_and_the_way_out(mkl):
    w = mkl()
    _three_days(w)
    _lost(w)                                              # the 11-12 row of a run whose summary never reached the journal
    out = w.propose()
    assert out["status"] == "stopped" and out["stage"] == "state", out
    for words in ("a copy put back", "never reached the journal", "2026-11-12", "1.07", "1.05", "lost-run",
                  "--accept-history", "a person must look"):
        assert words in out["reason"], (words, out["reason"])
    assert w.venue.calls == []


def test_the_owner_accepts_the_lost_summarys_row_after_a_preview_and_the_run_goes_on(mkl):
    w = mkl()
    _three_days(w)
    _lost(w)
    assert w.propose()["stage"] == "state"
    journal_before = {p.name: p.read_bytes() for p in (w.tmp / "journal").glob("*.jsonl")}
    state_before = w.state_path.read_bytes()
    code, out, err = accept(w, "2026-11-12")
    (number,) = re.findall(r"^code: ([0-9]{6})$", out, re.M)
    assert code == 0 and "preview" in out and "NOTHING was written" in out and "1.07" in out and "1.05" in out, (out, err)
    assert "not an older state file" not in out and "copy" not in out, out          # it does not claim to tell
    assert {p.name: p.read_bytes() for p in (w.tmp / "journal").glob("*.jsonl")} == journal_before   # the preview writes none
    assert w.state_path.read_bytes() == state_before
    wrong = f"{(int(number) + 1) % 10**6:06d}"
    code, _, err = accept(w, "2026-11-12", confirm=wrong)
    assert code == 2 and "code" in err and "nothing was written" in err
    assert {p.name: p.read_bytes() for p in (w.tmp / "journal").glob("*.jsonl")} == journal_before
    w.clock.t += 1.0
    code, out, err = accept(w, "2026-11-12", confirm=number)
    assert code == 0, err
    (line,) = [e for e in w.journal_events("history_accepted")]
    assert line["accepted_day"] == "2026-11-12" and line["venue"] == VENUE and line["prev_hash"] == "" and len(line["hash"]) == 64
    assert D(line["value_per_unit"]) == D("1.07") and line["who"] == "owner" and line["row_batch"] == "lost-run"
    assert w.state_path.read_bytes() == state_before                       # the state file is never written by it
    out = w.propose()                                     # the day's figure is the state file's: the check passes
    assert out["status"] == "done" and out["stage"] is None, out
    assert w.propose()["status"] == "done"


def test_the_accept_step_refuses_what_it_must_not_clear(mkl):
    w = mkl()
    _three_days(w)
    _summaries_have_batches(w)
    code, _, err = accept(w, "2026-11-12")
    assert code == 2 and "nothing differs" in err and "nothing was written" in err, err           # nothing differs
    _lost(w, "2026-11-12", "1.07", "lost-run")
    for day, words in (("2026-11-09", "holds no shared summary"), ("2026-11-31", "date"), ("tomorrow", "date")):
        code, _, err = accept(w, day)
        assert code == 2 and words in err and "nothing was written" in err, (day, err)             # a day the journal lacks
    w.sql("DELETE FROM book_nav WHERE day = '2026-11-11'")                    # a row that is not there cannot be accepted
    code, _, err = accept(w, "2026-11-11")
    assert code == 2 and "no book_nav row" in err, err
    assert [e for e in w.journal_events("history_accepted")] == []
    for kwargs in ({"live": False}, {"venue": "webull-sandbox"}):
        code, _, err = accept(w, "2026-11-12", **kwargs)
        assert code == 2 and "nothing was written" in err, (kwargs, err)
    code, _, err = accept(w, "2026-11-12", "--shared")
    assert code == 2 and "nothing was written" in err
    assert [e for e in w.journal_events("history_accepted")] == []


def test_a_row_of_a_run_the_journal_holds_a_summary_for_is_never_accepted_a_restored_copy_stays_stopped(lw):
    """A restored older state file: its latest row of the day belongs to a run whose summary the journal DOES hold (a
    later run's summary is there too), so the row is not a lost summary's: accepting it could lower the day's figure
    the day stop and the drawdown are judged from. The accept step refuses; only the real state file clears it."""
    lw.open_book("10000")
    lw.buy()
    lw.later()
    old = lw.state_path.read_bytes()                      # the state file as the first run left it
    lw.prices["SPY"] = 450.0                              # the fund fell: the later run's figures differ from the first's
    assert lw.propose()["status"] == "done"               # a later run of the same day: its row and summary
    lw.state_path.write_bytes(old)                        # the older copy put back
    lw.later()
    out = lw.propose()
    assert out["status"] == "stopped" and out["stage"] == "state" and "a copy put back" in out["reason"], out
    code, _, err = accept(lw, lw.journal_events("summary")[-1]["day"])
    assert code == 2 and "summary" in err and "holds" in err and "nothing was written" in err, err
    assert lw.journal_events("history_accepted") == []
    assert lw.propose()["stage"] == "state"


def test_a_second_mismatch_on_another_day_still_stops_after_one_day_is_accepted(mkl):
    w = mkl()
    _three_days(w)
    _lost(w, "2026-11-11", "1.01", "lost-a")
    _lost(w, "2026-11-12", "1.07", "lost-b")
    accepted(w, "2026-11-12")
    out = w.propose()
    assert out["status"] == "stopped" and out["stage"] == "state", out
    assert "2026-11-11" in out["reason"] and "2026-11-12" not in out["reason"].split("(", 1)[1].split("--accept-history")[0], out
    w.clock.t += 1.0
    accepted(w, "2026-11-11")                             # the second acceptance chains to the first
    first, second = w.journal_events("history_accepted")
    assert second["prev_hash"] == first["hash"] and w.propose()["status"] == "done"


def test_an_acceptance_clears_only_its_day_and_a_row_changed_afterwards_stops_again(mkl):
    w = mkl()
    _three_days(w)
    _lost(w)
    accepted(w, "2026-11-12")
    assert w.propose()["status"] == "done"
    w.sql("UPDATE book_nav SET value_per_unit = '1.04' WHERE batch = 'lost-run'")     # the accepted row changed later
    out = w.propose()
    assert out["status"] == "stopped" and out["stage"] == "state", out


def test_a_journal_line_that_only_looks_like_an_acceptance_clears_nothing(mkl):
    w = mkl()
    _three_days(w)
    _lost(w)
    forged = {"type": "order_step", "venue": VENUE, "event": "history_accepted", "accepted_day": "2026-11-12",
              "row_id": 4, "row_batch": "lost-run", "value_per_unit": "1.07", "value_per_unit_high": "1.07",
              "prev_hash": "", "hash": "0" * 64, "who": "owner", "note": "typed by hand"}
    w.journal.write("2026-11-13", forged)
    assert w.propose()["stage"] == "state"                # its hash is not the record's
    unchained = {**forged, "prev_hash": "ab" * 32}        # a hash that IS the record's, but chained to nothing
    unchained["hash"] = osx._accepted_hash(unchained)
    w.journal.write("2026-11-13", unchained)
    assert w.propose()["stage"] == "state"
    code, out, err = accept(w, "2026-11-12")
    assert code == 0, err                                 # the forged line is no part of the chain: the first real one
    (number,) = re.findall(r"^code: ([0-9]{6})$", out, re.M)
    w.clock.t += 1.0
    assert accept(w, "2026-11-12", confirm=number)[0] == 0
    real = [e for e in w.journal_events("history_accepted") if e["hash"] != "0" * 64 and e["prev_hash"] == ""]
    assert len(real) == 1 and real[0]["prev_hash"] == "" and w.propose()["status"] == "done"


def test_a_summary_that_failed_to_write_is_cleared_by_the_owners_acceptance_end_to_end(lw, monkeypatch):
    lw.open_book("10000")
    lw.buy()
    real = lw.journal.write

    def failing(day, entry):
        if entry.get("event") == "summary":
            raise OSError("the journal's disk is full")
        return real(day, entry)
    monkeypatch.setattr(lw.journal, "write", failing)
    lw.later()
    lw.prices["SPY"] = 550.0                              # the fund rose: the lost run's row is above the last summary
    out = lw.propose()                                    # its row is written, its summary is not
    assert out["status"] == "done" and "journal_error" in out, out
    monkeypatch.setattr(lw.journal, "write", real)
    lw.later()
    stopped = lw.propose()
    assert stopped["status"] == "stopped" and stopped["stage"] == "state" and "never reached the journal" in stopped["reason"]
    day = lw.nav()[-1]["day"]
    accepted(lw, day)
    assert lw.propose()["status"] == "done"


# ---- a summary written before the upper figure was journaled (no value_per_unit_high): the lower figure is held

def test_a_summary_without_the_upper_figure_holds_the_lower_one_and_a_higher_upper_row_goes_on(mkl):
    w = mkl()
    w.open_book("10000")
    w.seed_nav("2026-11-12", "1.00", "1.20")              # the row's upper figure is higher than anything journaled
    journal_look(w, "2026-11-12", "1.00", with_high=False)
    assert w.propose()["status"] == "done"
    other = mkl()
    other.open_book("10000")
    other.seed_nav("2026-11-12", "1.01", "1.20")          # ... but a differing lower figure stops
    journal_look(other, "2026-11-12", "1.00", with_high=False)
    out = other.propose()
    assert out["status"] == "stopped" and out["stage"] == "state" and "2026-11-12" in out["reason"], out


# ---- T51c S4d repair round 3: an acceptance may only TIGHTEN, never loosen

def _p1f(w):
    """The checker's repro: day 11-12 had a summary (b1, 1.00); run B wrote its row (0.965) and its summary never reached the
    journal; a copy of the state file was taken; run C wrote its row and summary (1.015); the copy is put back: its latest
    row of the day is B's (0.965), the journal's last summary is C's (1.015)."""
    w.open_book("10000")
    history(w, [("2026-11-10", "1.05", None)])
    w.seed_nav("2026-11-12", "1.00", None)
    w.sql("UPDATE book_nav SET batch = 'b1' WHERE day = '2026-11-12'")
    journal_look(w, "2026-11-12", "1.00", batch="b1")
    _lost(w, "2026-11-12", "0.965", "lost-b")             # no summary of B
    journal_look(w, "2026-11-12", "1.015", batch="c")     # C's summary: its row is not in the put-back copy


def test_a_lower_row_is_never_accepted_the_checkers_repro_stays_stopped(mkl):
    w = mkl()
    _p1f(w)
    assert w.propose()["stage"] == "state"
    before = {p.name: p.read_bytes() for p in (w.tmp / "journal").glob("*.jsonl")}
    code, out, err = accept(w, "2026-11-12")
    assert code == 2 and out == "" and "lower than the journal's figure for 2026-11-12" in err, (out, err)
    assert "loosen the day stop and the drawdown" in err and "put the real state file back" in err, err
    assert "nothing was written" in err and w.journal_events("history_accepted") == []
    assert {p.name: p.read_bytes() for p in (w.tmp / "journal").glob("*.jsonl")} == before
    out = w.propose()
    assert out["status"] == "stopped" and out["stage"] == "state", out        # the day-stop base did not drop


def test_a_hand_edited_row_with_a_renamed_batch_and_a_lower_figure_is_refused(mkl):
    w = mkl()
    _three_days(w)
    _summaries_have_batches(w)
    w.sql("UPDATE book_nav SET value_per_unit = '1.03', value_low = '103000', value_high = '103000', batch = 'a-new-name' "
          "WHERE day = '2026-11-12'")
    code, _, err = accept(w, "2026-11-12")
    assert code == 2 and "lower than the journal's figure for 2026-11-12" in err and w.journal_events("history_accepted") == []
    assert w.propose()["stage"] == "state"
    other = mkl()                                         # a higher lower figure but a lower upper one: still lower
    other.open_book("10000")
    history(other, [("2026-11-10", "1.05", None)])
    journal_look(other, "2026-11-12", "1.00", "1.20", batch="seen")
    other.seed_nav("2026-11-12", "1.10", "1.15")
    _row(other, batch="a-new-name")
    code, _, err = accept(other, "2026-11-12")
    assert code == 2 and "lower than the journal's figure" in err and other.journal_events("history_accepted") == []


def test_a_lost_summarys_higher_row_is_accepted_and_the_day_stops_base_is_the_row(mkl):
    w = mkl()
    _three_days(w)
    _lost(w, "2026-11-12", "1.07", "lost-run")
    accepted(w, "2026-11-12")
    out = w.propose()
    assert out["status"] == "done" and D(out["shared"]["previous_value_per_unit"]) == D("1.07"), out   # never below 1.05


def test_the_real_state_file_put_back_after_an_acceptance_still_goes_on(mkl):
    """The copy's row (B, higher than the journal's last summary) was accepted; the REAL state file (the one with C's row,
    which equals the journal's last summary) is then put back: it passes by itself, no further step is needed."""
    w = mkl()
    w.open_book("10000")
    history(w, [("2026-11-10", "1.05", None)])
    w.seed_nav("2026-11-12", "1.00", None)
    w.sql("UPDATE book_nav SET batch = 'b1' WHERE day = '2026-11-12'")
    journal_look(w, "2026-11-12", "1.00", batch="b1")
    _lost(w, "2026-11-12", "1.03", "lost-b")              # B's row, higher; its summary never reached the journal
    copy = w.state_path.read_bytes()                      # the copy: B's row is its latest of the day
    w.seed_nav("2026-11-12", "1.02", None)                # C: its row and its summary (1.02)
    w.sql("UPDATE book_nav SET batch = 'c' WHERE id = (SELECT MAX(id) FROM book_nav)")
    journal_look(w, "2026-11-12", "1.02", batch="c")
    real = w.state_path.read_bytes()
    w.state_path.write_bytes(copy)                        # the copy put back
    assert w.propose()["stage"] == "state"
    accepted(w, "2026-11-12")                             # B's 1.03 is above the journal's 1.02: it is accepted
    w.state_path.write_bytes(real)                        # the real state file back: its row is C's, the last summary
    w.later()
    out = w.propose()
    assert out["status"] == "done" and out["stage"] is None, out


def test_who_and_note_are_in_the_acceptances_hash_so_an_edit_stops_the_run_again(mkl):
    w = mkl()
    _three_days(w)
    _lost(w)
    accepted(w, "2026-11-12")
    assert w.propose()["status"] == "done"
    def edit(field, how):
        for path in sorted((w.tmp / "journal").glob("*.jsonl")):
            lines = path.read_text().splitlines()
            if any('"history_accepted"' in x for x in lines):
                out = []
                for x in lines:
                    if '"history_accepted"' in x:
                        record = json.loads(x)
                        record[field] = how(record[field])
                        x = json.dumps(record)
                    out.append(x)
                path.write_text("\n".join(out) + "\n")
    for field in ("note", "who"):
        edit(field, lambda text: "edited " + text)
        out = w.propose()
        assert out["status"] == "stopped" and out["stage"] == "state", (field, out)
        edit(field, lambda text: text[len("edited "):])
        w.later()
        assert w.propose()["status"] == "done", field


def test_the_code_covers_every_difference_the_preview_lists_on_every_day(mkl):
    w = mkl()
    _three_days(w)
    _lost(w, "2026-11-11", "1.01", "lost-a")
    _lost(w, "2026-11-12", "1.07", "lost-b")
    code, out, err = accept(w, "2026-11-12")
    assert code == 0 and "2026-11-11" in out, (out, err)  # the other day is listed as still differing
    (number,) = re.findall(r"^code: ([0-9]{6})$", out, re.M)
    w.sql("UPDATE book_nav SET value_per_unit = '1.02', value_low = '102000', value_high = '102000' "
          "WHERE batch = 'lost-a'")                       # the other listed day's row changes after the preview
    before = {p.name: p.read_bytes() for p in (w.tmp / "journal").glob("*.jsonl")}
    w.clock.t += 1.0
    code, _, err = accept(w, "2026-11-12", confirm=number)
    assert code == 2 and "code" in err and "nothing was written" in err, err
    assert {p.name: p.read_bytes() for p in (w.tmp / "journal").glob("*.jsonl")} == before
    code, out, _ = accept(w, "2026-11-12")                # a fresh preview has a fresh code
    (fresh,) = re.findall(r"^code: ([0-9]{6})$", out, re.M)
    assert fresh != number


# ---- T51c S4d repair round 4: one basis for every safety decision: value_low / units and value_high / units, as book_loss

def _row(w, **columns):
    """Edit the latest book_nav row (`value_high=None` sets NULL)."""
    sets = ", ".join(f"{k} = ?" for k in columns)
    w.sql(f"UPDATE book_nav SET {sets} WHERE id = (SELECT MAX(id) FROM book_nav)", tuple(columns.values()))


def _probe(w, with_high=True):
    """The checker's probe: days 11-10..11-12, the journal's last figure for 11-12 is 1.05; a new row for 11-12 whose text
    column says 1.07 and whose value_low (what the day stop and the peak read) says 0.90 a unit, with no upper figure."""
    w.open_book("10000")
    history(w, [("2026-11-10", "1.05", None), ("2026-11-11", "1.00", None)])
    w.seed_nav("2026-11-12", "1.05", None)
    journal_look(w, "2026-11-12", "1.05", with_high=with_high)
    w.seed_nav("2026-11-12", "1.07", None)
    _row(w, value_low="90000", value_high=None, batch="probe")


def test_the_checkers_probe_is_a_state_fault_with_and_without_an_acceptance_and_with_or_without_the_journals_upper(mkl):
    for with_high in (True, False):
        w = mkl()
        _probe(w, with_high)
        out = w.propose()
        assert out["status"] == "stopped" and out["stage"] == "state", (with_high, out)
        assert "2026-11-12" in out["reason"] and "value_low" in out["reason"] and "a person must look" in out["reason"], out
        assert w.venue.calls == []
        before = {p.name: p.read_bytes() for p in (w.tmp / "journal").glob("*.jsonl")}
        code, out_text, err = accept(w, "2026-11-12")
        assert code == 2 and out_text == "" and "value_low" in err and "nothing was written" in err, (with_high, err)
        assert {p.name: p.read_bytes() for p in (w.tmp / "journal").glob("*.jsonl")} == before
        assert w.journal_events("history_accepted") == []
        assert w.propose()["stage"] == "state"


def test_an_edited_upper_figure_is_judged_on_value_high_over_units_the_text_unchanged(mkl):
    lower = mkl()                                         # the upper below the lower: a day stop base that fell
    _three_days(lower)
    _row(lower, value_high="50000")
    out = lower.propose()
    assert out["status"] == "stopped" and out["stage"] == "state" and "value_high" in out["reason"], out
    old = mkl()                                           # a journal without upper figures: the lower is all it holds
    old.open_book("10000")
    history(old, [("2026-11-10", "1.05", None)])
    old.seed_nav("2026-11-12", "1.05", None)
    journal_look(old, "2026-11-12", "1.05", with_high=False)
    _row(old, value_high="50000")                         # the day stop's base would be 0.50, the text still says 1.05
    out = old.propose()
    assert out["status"] == "stopped" and out["stage"] == "state", out
    higher = mkl()                                        # a higher upper: tighter, so it may be accepted
    _three_days(higher)
    _row(higher, value_high="110000", batch="edited")
    assert higher.propose()["stage"] == "state"
    accepted(higher, "2026-11-12")
    out = higher.propose()
    assert out["status"] == "done" and D(out["shared"]["previous_value_per_unit"]) == D("1.10"), out


def test_a_row_with_text_that_is_not_a_number_or_units_that_are_not_above_nothing_is_a_state_fault_in_words(mkl):
    cases = (("value_per_unit", "NaN"), ("value_per_unit", "not a number"), ("units_outstanding", "0"),
             ("units_outstanding", "-5"), ("units_outstanding", "NaN"), ("units_outstanding", "Infinity"),
             ("value_low", "NaN"), ("value_low", None), ("value_high", "NaN"), ("value_per_unit", "1.0500001"))
    for column, value in cases:
        w = mkl()
        _three_days(w)
        _row(w, **{column: value})                        # the 11-12 row of a run, damaged
        out = w.propose()
        assert out["status"] == "stopped" and out["stage"] == "state", (column, value, out)
        assert "2026-11-12" in out["reason"] and "a person must look" in out["reason"], (column, value, out["reason"])
        assert w.venue.calls == []
        code, _, err = accept(w, "2026-11-12")
        assert code == 2 and "nothing was written" in err and "Traceback" not in err, (column, value, err)
        assert w.journal_events("history_accepted") == []


def test_a_hand_written_acceptance_below_the_journals_summary_is_ignored_when_the_run_reads_it(mkl):
    w = mkl()
    _three_days(w)
    w.seed_nav("2026-11-12", "1.00", None)               # a lower row of the day (a copy, or a real fall), no summary of it
    _row(w, batch="lost-x")
    record = {"type": "order_step", "venue": VENUE, "event": "history_accepted", "accepted_day": "2026-11-12",
              "row_id": 99, "row_batch": "lost-x", "value_per_unit": "1.00", "value_per_unit_high": "1.00",
              "journal_had": {"value_per_unit": "1.05", "value_per_unit_high": "1.05"}, "prev_hash": "", "who": "o", "note": "n"}
    record["hash"] = osx._accepted_hash(record)           # a whole, correctly chained line, but below the summary
    w.journal.write("2026-11-13", record)
    out = w.propose()
    assert out["status"] == "stopped" and out["stage"] == "state", out
    looks = osx._journal_shared_looks(w.journal.read(), VENUE)
    assert [x for x in looks if x.get("accepted")] == []
    ok = {**record, "value_per_unit": "1.06", "value_per_unit_high": "1.06"}     # at or above: it counts
    ok["hash"] = osx._accepted_hash(ok)
    other = mkl()
    _three_days(other)
    other.seed_nav("2026-11-12", "1.06", None)
    _row(other, batch="lost-x")
    other.journal.write("2026-11-13", ok)
    assert other.propose()["status"] == "done"


# ---- T51c S4d repair round 5: every row's day is a real date not after today; a row no journal day holds; decimal signals

def test_a_row_whose_day_is_not_a_standard_date_or_is_after_today_is_a_state_fault_and_is_never_accepted(mkl):
    for odd in ("2026-11-12z", "2026-11-12 ", "2026-02-30", "2026-11-14", "20261112", "2026-1-12", "", "2026-11-12\n"):
        w = mkl()
        _three_days(w)
        w.seed_nav(odd, "0.90", None)                     # a hand-inserted row at 0.90: the real 11-12 row still matches
        out = w.propose()
        assert out["status"] == "stopped" and out["stage"] == "state", (odd, out)
        assert "a person must look" in out["reason"] and "day" in out["reason"], (odd, out["reason"])
        assert w.venue.calls == []
        before = {p.name: p.read_bytes() for p in (w.tmp / "journal").glob("*.jsonl")}
        for day in ("2026-11-12", odd.strip() or "2026-11-12"):
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day):
                continue
            code, out_text, err = accept(w, day)
            assert code == 2 and out_text == "" and "nothing was written" in err and "Traceback" not in err, (odd, day, err)
        assert {p.name: p.read_bytes() for p in (w.tmp / "journal").glob("*.jsonl")} == before
        assert w.journal_events("history_accepted") == []


def test_a_row_of_a_day_the_journal_holds_no_summary_for_that_could_become_the_previous_figure_is_a_fault(mkl):
    w = mkl()
    w.open_book("10000")
    history(w, [("2026-11-10", "1.05", None)])
    w.seed_nav("2026-11-12", "0.90", None)               # added: no summary of 11-12, after the last day the journal holds
    out = w.propose()                                     # (book_loss would take it as the previous value per unit)
    assert out["status"] == "stopped" and out["stage"] == "state", out
    assert "2026-11-12" in out["reason"] and "no summary" in out["reason"] and "previous value per unit" in out["reason"], out
    code, _, err = accept(w, "2026-11-12")                # lower than the last day's figure: no route
    assert code == 2 and "lower than the journal's figure" in err and w.journal_events("history_accepted") == []
    assert w.propose()["stage"] == "state"


def test_a_row_on_a_day_without_a_summary_that_cannot_be_the_previous_figure_is_harmless(mkl):
    w = mkl()
    w.open_book("10000")
    history(w, [("2026-11-10", "1.05", None), ("2026-11-12", "1.05", None)])
    w.seed_nav("2026-11-11", "0.90", None)               # between two journaled days: 11-12's row is the previous one
    w.seed_nav("2026-11-13", "0.90", None)               # today's own: never the previous figure
    out = w.propose()
    assert out["status"] == "done" and D(out["shared"]["previous_value_per_unit"]) == D("1.05"), out


def test_an_unheld_days_higher_row_is_accepted_against_the_last_days_figure_and_the_run_goes_on(mkl):
    w = mkl()
    w.open_book("10000")
    history(w, [("2026-11-10", "1.05", None)])
    w.seed_nav("2026-11-12", "1.07", None)               # a run of 11-12 whose summary never reached the journal
    _row(w, batch="lost-run")
    assert w.propose()["stage"] == "state"
    accepted(w, "2026-11-12")
    (line,) = w.journal_events("history_accepted")
    assert line["accepted_day"] == "2026-11-12" and D(line["journal_had"]["value_per_unit"]) == D("1.05")
    out = w.propose()
    assert out["status"] == "done" and D(out["shared"]["previous_value_per_unit"]) == D("1.07"), out
    other = mkl()                                         # a hand-written line for such a day, below the last day's figure
    other.open_book("10000")
    history(other, [("2026-11-10", "1.05", None)])
    other.seed_nav("2026-11-12", "0.90", None)
    _row(other, batch="lost-run")
    record = {"type": "order_step", "venue": VENUE, "event": "history_accepted", "accepted_day": "2026-11-12",
              "row_id": 2, "row_batch": "lost-run", "value_per_unit": "0.90", "value_per_unit_high": "0.90",
              "journal_had": {"value_per_unit": "1.05", "value_per_unit_high": "1.05"}, "prev_hash": "", "who": "o", "note": "n"}
    record["hash"] = osx._accepted_hash(record)
    other.journal.write("2026-11-13", record)
    assert other.propose()["stage"] == "state"


def test_a_decimal_signal_while_reading_a_row_is_a_state_fault_in_the_run_and_a_refusal_in_the_command(mkl):
    for units, low in (("1E-999999", "1E+999999"), ("1E+999999", "1E-999999"), ("1E+999999", "1E+999999")):
        w = mkl()
        _three_days(w)
        _row(w, units_outstanding=units, value_low=low, value_high=None)
        out = w.propose()
        assert out["status"] == "stopped" and out["stage"] == "state", (units, low, out)       # not `unexpected`
        assert "2026-11-12" in out["reason"] and "a person must look" in out["reason"], out["reason"]
        code, out_text, err = accept(w, "2026-11-12")
        assert code == 2 and out_text == "" and "nothing was written" in err and "Traceback" not in err, (units, low, err)
        assert w.journal_events("history_accepted") == []


# ---- T51c S4d repair round 6: the checks run on every row always; the base a first lost summary is held against

def test_with_no_summary_in_the_journal_every_rows_day_and_figures_are_still_judged(mkl):
    for day in ("2026-11-12", "2026-11-12z", "2026-11-99"):
        w = mkl()
        w.open_book("10000")                              # the book's first live runs: the journal holds no summary
        w.seed_nav(day, "0.90", None)                     # a hand-inserted row: the opening entry's value per unit is 1
        out = w.propose()
        assert out["status"] == "stopped" and out["stage"] == "state", (day, out)
        assert w.venue.calls == [] and "a person must look" in out["reason"], out["reason"]
    w = mkl()                                             # the same with only today's summary in the journal
    w.open_book("10000")
    journal_look(w, "2026-11-13", "1.00")
    w.seed_nav("2026-11-12", "0.90", None)
    assert w.propose()["stage"] == "state"


def test_a_row_before_today_with_no_summary_is_held_against_the_opening_base_lower_stops_higher_is_accepted(mkl):
    low = mkl()
    low.open_book("10000")
    low.seed_nav("2026-11-12", "0.90", None)
    _row(low, batch="lost-first")
    assert low.propose()["stage"] == "state"
    code, out, err = accept(low, "2026-11-12")
    assert code == 2 and "lower than the journal's figure for 2026-11-12" in err and low.journal_events("history_accepted") == []
    assert low.propose()["stage"] == "state"
    high = mkl()
    high.open_book("10000")
    high.seed_nav("2026-11-12", "1.02", None)
    _row(high, batch="lost-first")
    assert high.propose()["stage"] == "state"
    code, preview, err = accept(high, "2026-11-12")
    assert code == 0, err
    assert "the journal holds no summary of 2026-11-12" in preview and "opening" in preview, preview   # where the figure comes from
    assert "the journal's last summary of 2026-11-12 says" not in preview
    (number,) = re.findall(r"^code: ([0-9]{6})$", preview, re.M)
    high.clock.t += 1.0
    assert accept(high, "2026-11-12", confirm=number)[0] == 0
    out = high.propose()
    assert out["status"] == "done" and D(out["shared"]["previous_value_per_unit"]) == D("1.02"), out
    hand = mkl()                                          # a hand-written line below the base is ignored at run time
    hand.open_book("10000")
    hand.seed_nav("2026-11-12", "0.90", None)
    _row(hand, batch="lost-first")
    record = {"type": "order_step", "venue": VENUE, "event": "history_accepted", "accepted_day": "2026-11-12",
              "row_id": 1, "row_batch": "lost-first", "value_per_unit": "0.90", "value_per_unit_high": "0.90",
              "journal_had": {"value_per_unit": "1", "value_per_unit_high": "1"}, "prev_hash": "", "who": "o", "note": "n"}
    record["hash"] = osx._accepted_hash(record)
    hand.journal.write("2026-11-13", record)
    assert hand.propose()["stage"] == "state"


def test_the_preview_of_an_ordinary_day_says_the_journals_last_summary_and_names_the_day(mkl):
    w = mkl()
    _three_days(w)
    _lost(w)
    code, out, err = accept(w, "2026-11-12")
    assert code == 0 and "the journal's last summary of 2026-11-12 says" in out, (out, err)


def test_a_failed_final_reprice_leaves_the_summary_and_the_row_consistent_and_the_next_run_goes_on(lw, monkeypatch):
    lw.open_book("10000")
    out = lw.propose()
    number = out["ticket_number"]
    lw.later(1)
    real = osx.OrderStepState.reprice_book_nav

    def failing(self, row, nav):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(osx.OrderStepState, "reprice_book_nav", failing)    # the final look's reprice (after the purchase)
    done = lw.approve(number)
    assert done["status"] == "done" and done["orders_sent"] == 1, done
    monkeypatch.setattr(osx.OrderStepState, "reprice_book_nav", real)
    row = lw.nav()[-1]
    assert done["shared"]["value_per_unit"] == row["value_per_unit"], (done["shared"], row)   # one set of figures
    assert lw.journal_events("summary")[-1]["shared"]["value_per_unit"] == row["value_per_unit"]
    assert D(done["broker_equity"]) == D(row["value_low"]) or abs(done["broker_equity"] - float(row["value_low"])) < 1e-9
    lw.later()
    out = lw.propose()
    assert out["status"] in ("done",) and out["stage"] is None, out          # not stopped at `state`


# ---- T51c S4d repair round 7: an acceptance is held against the floor as it stood when it was made

def test_a_later_owner_entry_does_not_cancel_an_acceptance_made_against_the_earlier_base(lw, monkeypatch):
    """The S8 sequence: a day's buy, a rise, every summary of that day lost; the next day the run stops and the owner accepts
    the day's last row (1.015) against the opening base (1); the book gains again and the owner makes an ordinary entry whose
    value per unit after (1.045) is above 1.015, with no run in between. The acceptance still counts."""
    lw.open_book("10000")
    real = lw.journal.write

    def failing(day, entry):
        if entry.get("event") == "summary":
            raise OSError("the journal's disk is full")
        return real(day, entry)
    monkeypatch.setattr(lw.journal, "write", failing)
    out = lw.propose()
    lw.later(1)
    assert lw.approve(out["ticket_number"])["orders_sent"] == 1          # 11-13: the book buys 10 SPY
    lw.later(1)
    lw.prices["SPY"] = 520.0                              # the fund rises: the book is worth 1.015 a unit
    assert lw.propose()["status"] == "done"               # the day's last row, 1.015; no summary of 11-13 reaches the journal
    monkeypatch.setattr(lw.journal, "write", real)
    assert [e for e in lw.journal_events("summary") if e["venue"] == VENUE] == []
    lw.next_day()                                         # 11-16
    stopped = lw.propose()
    assert stopped["status"] == "stopped" and stopped["stage"] == "state", stopped
    accepted(lw, "2026-11-13")
    assert D(lw.journal_events("history_accepted")[0]["value_per_unit"]) == D("1.015")
    lw.prices["SPY"] = 550.0
    lw.owner("cash_in", "--amount", "100")                # an ordinary entry: value per unit after 1.045, above 1.015
    assert D(lw.entries()[-1]["value_per_unit_after"]) == D("1.045"), lw.entries()[-1]
    lw.next_day()                                         # 11-17
    out = lw.propose()
    assert out["status"] == "done" and out["stage"] is None, out
    assert D(out["shared"]["previous_value_per_unit"]) == D("1.015"), out["shared"]   # what book_loss gives on intact summaries


def test_a_hand_written_line_below_its_own_floor_at_its_own_time_is_still_ignored_when_entries_come_later(lw):
    lw.open_book("10000")
    lw.seed_nav("2026-11-12", "0.90", None)
    _row(lw, batch="lost-first")
    record = {"type": "order_step", "venue": VENUE, "event": "history_accepted", "accepted_day": "2026-11-12",
              "row_id": 1, "row_batch": "lost-first", "value_per_unit": "0.90", "value_per_unit_high": "0.90",
              "journal_had": {"value_per_unit": "1", "value_per_unit_high": "1"}, "prev_hash": "", "who": "o", "note": "n"}
    record["hash"] = osx._accepted_hash(record)
    lw.journal.write("2026-11-13", record)                # 0.90 is below the opening base (1) at its own time
    lw.next_day()
    lw.owner("cash_in", "--amount", "10")                 # a later entry makes no difference to a line below its floor
    lw.next_day()
    assert lw.propose()["stage"] == "state"
    assert [x for x in osx._journal_shared_looks(lw.journal.read(), VENUE) if x.get("accepted")] == []


def test_an_in_run_reprice_failure_leaves_the_summary_with_the_figures_of_the_row_actually_written(lw, monkeypatch):
    lw.open_book("10000")
    lw.buy()                                              # the book holds 10 SPY
    lw.later()
    lw.paper = T.paper({"a": {"SPY": 120.0, "QQQ": 50.0}})            # two purchases: QQQ first (the larger), then SPY
    out = lw.propose()
    assert len(out["ticket"]["orders"]) == 2, out
    number = out["ticket_number"]
    lw.later(1)
    for symbol in ("SPY", "QQQ"):
        lw.quote_age[symbol] = 19.5 * 60                  # fresh now, stale after the first send: asked again
    T._later_submit(lw, 2, then=lambda w: w.prices.update(SPY=520.0))      # the held fund moves between the two orders
    real_reprice = osx.OrderStepState.reprice_book_nav

    def failing(self, row, nav):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(osx.OrderStepState, "reprice_book_nav", failing)
    stopped = lw.approve(number)
    monkeypatch.setattr(osx.OrderStepState, "reprice_book_nav", real_reprice)
    assert stopped["status"] == "stopped" and stopped["stage"] == "send", stopped
    row = lw.nav()[-1]
    assert stopped["shared"]["value_per_unit"] == row["value_per_unit"], (stopped["shared"], row)
    assert lw.journal_events("summary")[-1]["shared"]["value_per_unit"] == row["value_per_unit"]
    lw.venue.submit = type(lw.venue).submit.__get__(lw.venue)
    lw.later()
    lw.quote_age.clear()
    out = lw.propose()
    assert out["stage"] != "state", out                   # the next run is not stopped at state for the lost reprice


def test_the_preview_names_an_earlier_acceptance_as_the_base_when_it_is(mkl):
    w = mkl()
    _three_days(w)
    _lost(w, "2026-11-12", "1.07", "lost-1")
    accepted(w, "2026-11-12")
    _lost(w, "2026-11-12", "1.09", "lost-2")              # a later row of the day, higher than the accepted figure
    code, out, err = accept(w, "2026-11-12")
    assert code == 0, err
    assert "the owner's acceptance of 2026-11-12" in out and "the journal's last summary of 2026-11-12 says" not in out, out


# ---- T51c S4e (R34): the owner's `mark` entry on the live venue

def test_a_live_mark_entry_is_taken_only_together_with_live_and_a_production_name(lw, capsys):
    lw.open_book("10000")
    lw.buy()
    plain = ["--root", str(lw.tmp_root), "order-step", "--owner-entry", "mark", "--symbol", "SPY", "--who", "owner",
             "--note", "test"]
    before = lw.tree()
    capsys.readouterr()
    assert cli.main([*plain, "--venue", VENUE]) == 2 and "written only together with --live" in capsys.readouterr().err
    assert cli.main([*plain, "--live", "--venue", "webull-sandbox"]) == 2
    assert "a practice name" in capsys.readouterr().err
    assert lw.tree() == before


def test_a_live_mark_entry_prints_first_and_asks_the_order_steps_own_quote_never_a_typed_price(lw):
    lw.open_book("10000")
    lw.buy()
    base = lw.owner_args("mark", "--symbol", "SPY")
    before = lw.tree()
    lw.capsys.readouterr()
    assert cli.main(base) == 0
    out = lw.capsys.readouterr().out
    (code,) = re.findall(r"^code: ([0-9]{6})$", out, re.M)
    assert "NOTHING was written" in out and "mark SPY: 500.0" in out and "no cash and no units change" in out, out
    assert {k: v for k, v in lw.tree().items() if not k.endswith(".owner-preview.json")} == \
        {k: v for k, v in before.items() if not k.endswith(".owner-preview.json")}
    assert cli.main([*base, "--mark", "SPY=1"]) == 2 and "--mark is not taken" in lw.capsys.readouterr().err
    lw.clock.t += 1.0
    assert cli.main([*base, "--confirm", code]) == 0, lw.capsys.readouterr().err
    assert lw.entries()[-1]["kind"] == "mark" and lw.entries()[-1]["mark"] == "500.0"
    (line,) = [e for e in lw.journal.read() if e.get("type") == "owner_entry" and e.get("kind") == "mark"]
    assert line["venue"] == VENUE and line["hash"] == lw.entries()[-1]["hash"]


def test_a_live_mark_entry_outside_the_session_is_valued_at_the_last_close_and_shows_its_time(lw, monkeypatch):
    lw.open_book("10000")
    lw.buy()
    close = T.ny("2026-11-13 16:00")
    lw.clock.t = T.ny("2026-11-13 17:30")
    monkeypatch.setattr(osx, "minute_quotes", lambda symbols: {s: (lw.prices[s], close - 60.0) for s in symbols})
    lw.capsys.readouterr()
    assert cli.main(lw.owner_args("mark", "--symbol", "SPY")) == 0
    out = lw.capsys.readouterr().out
    assert "outside the session" in out and "2026-11-13T16:00:00-05:00" in out, out


def test_a_live_mark_entry_clears_the_marks_stop_and_the_history_check_is_neither_tripped_nor_loosened(mkl):
    w = mkl(max_drawdown_pct=8.0)
    w.open_book("10000")
    w.buy()
    w.prices["SPY"] = 700.0
    w.later()
    out = w.propose()
    assert out["status"] == "stopped" and out["stage"] == "marks", out
    w.owner("mark", "--symbol", "SPY")
    w.later()
    out = w.propose()
    assert out["status"] == "done" and out["stage"] is None, out               # the history check passes (no `state` stop)
    w2 = mkl(max_drawdown_pct=8.0)                                              # the same history, without the entry
    w2.open_book("10000")
    w2.buy()
    w2.later()
    base = w2.propose()
    assert base["status"] == "done"
    for key in ("previous_value_per_unit", "previous_from"):
        assert out["shared"][key] == base["shared"][key], key


def test_after_a_mark_entry_a_state_file_without_the_value_history_still_stops_the_live_run(lw):
    lw.open_book("10000")
    lw.buy()
    lw.owner("mark", "--symbol", "SPY")
    lw.sql("DELETE FROM book_nav")                        # an older state file put back: the entry does not excuse it
    lw.later()
    out = lw.propose()
    assert out["status"] == "stopped" and out["stage"] == "state", out
    assert "value history the journal holds" in out["reason"], out["reason"]
    lw.later()
    lowered = lw.sql("SELECT COUNT(*) AS n FROM owner_entries WHERE kind = 'mark'")
    assert lowered == [{"n": 1}]
