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
    assert "whole shares" in cli._live_args_refusal(ns(live=True, shared=True, fractional=True, propose=True))


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
    lw.venue.calls.clear()
    out = lw.propose()
    assert out["status"] == "stopped" and out["stage"] == "state", out
    assert "value per unit" in out["reason"] and "a person must look" in out["reason"], out["reason"]
    assert lw.venue.submits() == [] and not any(c[0] in ("positions", "cash") for c in lw.venue.calls)
    assert lw.state_path.name in {p.name for p in lw.state_path.parent.iterdir()}


def test_the_state_files_peak_is_compared_in_decimal_never_as_text(lw):
    lw.open_book("10000")
    lw.journal.write("2026-11-12", {"type": "order_step", "venue": VENUE, "event": "summary", "status": "done",
                                    "shared": {"value_per_unit": "10.5"}})      # the journal saw 10.5
    lw.seed_nav("2026-11-11", "10.5")
    lw.seed_nav("2026-11-12", "9.5")                      # as text "9.5" is above "10.5": a text MAX would stop the run
    out = lw.propose()
    assert out["status"] == "done" and out["stage"] is None, out                 # the Decimal peak 10.5 knows as much
    lw.journal.write("2026-11-12", {"type": "order_step", "venue": VENUE, "event": "summary", "status": "done",
                                    "shared": {"value_per_unit": "10.51"}})
    lw.later()
    out = lw.propose()
    assert out["status"] == "stopped" and out["stage"] == "state", out


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
    out = lw.propose()
    assert out["status"] == "done" and out["day_stop"]["pct"] == pytest.approx(100 * 0.05 / 1.05, rel=1e-9), out
    assert out["day_stop"]["limit"] == 1.0 and out["day_stop"]["wall"] is False, out
    assert [r[1] for r in refusals(out)] == ["day stop"] and out["orders_sent"] == 0 and "ticket_number" not in out, out
    assert lw.journal_events("summary")[-1]["day_stop"] == out["day_stop"]
    assert lw.journal_events("day_stop"), "the trip is journaled at once"


def test_the_owners_deposits_and_withdrawals_are_no_loss_to_the_live_day_stop(lw):
    lw.open_book("10000")
    lw.seed_nav("2026-11-12", "1.0", units="10000")
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
