"""T51c S3 (addendum 1, R3, R5): the order step's state file at layout 2, the owner's entries and his command, and the
local check of R3 (called by nothing yet).

- Layout 1 to 2: a new file is created at layout 2; a layout-1 file is upgraded the first time it is opened for
  writing, inside that open's one write transaction (all of it or nothing), keeping every row; the old `equity` table
  is kept untouched. The order step of commit cdd5371 (T51c's base, taken from this checkout's history with `git
  show`; if it cannot be read the tests FAIL in words) refuses a layout-2 file in words and changes nothing in it.
- The owner's entries (R5): an append-only, hash-chained table; a changed or removed entry is detected and refused in
  words. A `units_out` clears exactly the shortfall it names; a `split` keeps the value and changes the units.
- The owner's command (`order-step --owner-entry KIND`): refuses without who and note, refuses a figure that is not a
  positive decimal, defaults no figure, prints what it wrote, and is refused beside the run's own flags and while a
  run holds the lock.
- R3: every tag this code wrote to the journal, a ticket or the state's send record must be in the ledger.
- Outcome 4: practice runs (with and without fractional) equal cdd5371's order step, venue calls, journal, ledger,
  summary and files, apart from the state file's layout number and its new empty table.
Nothing here opens a socket or names a real folder: every root is the test's own."""
import hashlib
import importlib.util
import json
import socket
import sqlite3
import subprocess
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

from valedia_trader.execution_and_risk import order_step as osx
from valedia_trader.execution_and_risk import orders as _orders
from valedia_trader.execution_and_risk.order_step import OrderStepError, OrderStepState, book_figures
from valedia_trader.research_loop import cli
from valedia_trader.research_loop import paper as _paper
import test_order_step as T

BASE = "cdd5371"
VENUE = "webull-test-cash"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise OSError("the T51c S3 tests open no socket")
    for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "create_connection"),
                        (socket, "getaddrinfo")):
        monkeypatch.setattr(owner, name, refuse)


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """cdd5371's order_step.py, from git, loaded under its own name (it imports this tree's other modules)."""
    root = Path(__file__).resolve().parents[2]
    try:
        src = subprocess.run(["git", "-C", str(root), "show", f"{BASE}:valedia_trader/execution_and_risk/order_step.py"],
                             capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        why = (getattr(e, "stderr", b"") or b"").decode("utf-8", "replace").strip()[:200]
        pytest.fail(f"T51c's base, commit {BASE}, cannot be read from this checkout's history ({type(e).__name__}: "
                    f"{why}); the comparison cannot be made, so it fails")
    path = tmp_path_factory.mktemp("t51c_base") / "order_step_t51c_base.py"
    path.write_bytes(src)
    spec = importlib.util.spec_from_file_location("order_step_t51c_base", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["order_step_t51c_base"] = mod
    spec.loader.exec_module(mod)
    assert mod.STATE_LAYOUT == 1 and not hasattr(mod, "book_figures")
    return mod


def dump(path):
    return T._dump(path)


def layout1_state(base, path):
    st = base.OrderStepState(path, clock=lambda: 1_763_478_000.0)
    st.record_baseline({"OWN": 3.0, "SPY": 0.0})
    st.record_equity("2025-11-17", 100_000.0)
    row = st.record_equity("2025-11-18", 101_000.0)
    st.reprice_equity(row, 101_500.0)
    st.note_sending("b1-SPY", "2025-11-18")
    st.note_outcome("b1-SPY", "accepted")
    st.close()
    return dump(path)


# ---- layout 1 to 2


def test_a_new_state_file_is_layout_2_with_an_empty_entries_table(tmp_path):
    osx.OrderStepState(tmp_path / "s.sqlite").close()
    d = dump(tmp_path / "s.sqlite")
    assert osx.STATE_LAYOUT == 2 and d["version"] == 2
    assert set(d["rows"]) == {"meta", "baseline", "equity", "sends", "owner_entries", "book_nav", "sqlite_sequence"}
    assert d["rows"]["owner_entries"] == [] and d["rows"]["book_nav"] == []


def test_book_nav_holds_what_s4_needs_as_text_and_nothing_writes_it(tmp_path):
    """Repair item 3: the book's value history, keyed like the equity table (an id, the New York day, the time), with
    exact decimals as text; written and read by nothing in this build."""
    osx.OrderStepState(tmp_path / "s.sqlite").close()
    con = sqlite3.connect(tmp_path / "s.sqlite")
    cols = {r[1]: (r[2], r[3]) for r in con.execute("PRAGMA table_info(book_nav)")}
    con.close()
    assert cols == {"id": ("INTEGER", 0), "day": ("TEXT", 1), "time": ("REAL", 1), "batch": ("TEXT", 1),
                    "units_outstanding": ("TEXT", 1), "value_per_unit": ("TEXT", 0), "value_low": ("TEXT", 0),
                    "value_high": ("TEXT", 0), "cash_bounded": ("INTEGER", 1), "marks": ("TEXT", 1),
                    "marks_hash": ("TEXT", 1), "entry_hash": ("TEXT", 1)}
    src = Path(osx.__file__).read_text()
    assert "CREATE TABLE book_nav " in src
    for verb in ("FROM book_nav", "INTO book_nav", "UPDATE book_nav", "JOIN book_nav"):
        assert verb not in src, verb                                    # nothing in this build writes or reads it
    con = sqlite3.connect(tmp_path / "s.sqlite")
    row = ("2025-11-18", 1.0, "b1", "10", "1", "1", "1")
    for flag in (2, -1, "yes"):                                         # cash_bounded is 0 or 1, nothing else
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO book_nav (day, time, batch, units_outstanding, value_per_unit, value_low, "
                        "value_high, cash_bounded, marks, marks_hash, entry_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
                        "'{}', 'h', 'e')", (*row, flag))
    con.rollback()
    con.close()


def test_a_layout_2_file_without_one_of_its_new_tables_is_not_opened(tmp_path):
    path = tmp_path / "s.sqlite"
    OrderStepState(path).close()
    con = sqlite3.connect(path)
    con.execute("DROP TABLE book_nav")
    con.commit()
    con.close()
    before = dump(path)
    with pytest.raises(osx.OrderStepError, match="is not an order-step state file of layout 1 or 2; it is not opened"):
        OrderStepState(path)
    assert dump(path) == before


def test_state_layout1_upgrade_keeps_rows_and_ignores_old_equity(tmp_path, base):
    path = tmp_path / "s.sqlite"
    before = layout1_state(base, path)
    assert before["version"] == 1
    st = OrderStepState(path)
    assert st.owner_entries() == []
    assert st.previous_equity("2025-11-19") == 101_500.0 and st.baseline() == {"OWN": 3.0}
    st.close()
    after = dump(path)
    assert after["version"] == 2
    for table, rows in before["rows"].items():
        assert after["rows"][table] == rows, table                     # every row kept, the equity table untouched
    assert set(after["rows"]) - set(before["rows"]) == {"owner_entries", "book_nav"}
    assert after["rows"]["owner_entries"] == [] and after["rows"]["book_nav"] == []
    assert set(before["schema"]) <= set(after["schema"])                # the old tables' schema text unchanged
    assert T._dump(path) == after
    OrderStepState(path).close()                                        # once: a second open changes nothing
    assert dump(path) == after


@pytest.mark.parametrize("at", ["after the entries table", "after both tables"])
def test_the_upgrade_happens_inside_one_transaction_or_not_at_all(tmp_path, base, monkeypatch, at):
    path = tmp_path / "s.sqlite"
    before = layout1_state(base, path)
    raw = path.read_bytes()
    steps = list(osx._STATE_UPGRADE_1_TO_2)
    assert any(s.startswith("CREATE TABLE owner_entries ") for s in steps)
    assert any(s.startswith("CREATE TABLE book_nav ") for s in steps)
    where = 1 if at == "after the entries table" else len(steps)
    monkeypatch.setattr(osx, "_STATE_UPGRADE_1_TO_2", steps[:where] + ["CREATE TABLE meta (x)"] + steps[where:])
    with pytest.raises(sqlite3.Error):
        OrderStepState(path)
    assert dump(path) == before and path.read_bytes() == raw            # nothing of the upgrade stayed, byte for byte
    monkeypatch.undo()
    OrderStepState(path).close()
    assert dump(path)["version"] == 2 and {"owner_entries", "book_nav"} <= set(dump(path)["rows"])


def test_the_code_before_this_build_refuses_a_layout_2_file_in_words(tmp_path, base):
    path = tmp_path / "s.sqlite"
    layout1_state(base, path)
    OrderStepState(path).close()
    seen, raw = dump(path), path.read_bytes()
    assert {"owner_entries", "book_nav"} <= set(seen["rows"])
    with pytest.raises(base.OrderStepError, match="is not an order-step state file of layout 1; it is not opened"):
        base.OrderStepState(path)
    assert dump(path) == seen and path.read_bytes() == raw


@pytest.mark.parametrize("version, tables", [(2, ("meta", "baseline", "equity", "sends")), (3, None), (1, "extra")])
def test_another_layout_or_another_schema_is_refused_in_words(tmp_path, version, tables):
    path = tmp_path / "s.sqlite"
    OrderStepState(path).close()
    con = sqlite3.connect(path)
    if tables == "extra":
        con.execute("CREATE TABLE extra (x)")
    elif tables is not None:
        con.execute("DROP TABLE owner_entries")
    con.execute(f"PRAGMA user_version = {version}")
    con.commit()
    con.close()
    seen = dump(path)
    with pytest.raises(OrderStepError, match="not an order-step state file"):
        OrderStepState(path)
    assert dump(path) == seen


# ---- the owner's entries


def add(st, fig, kind, who="owner", note="a note", **kw):
    return st.add_owner_entry(osx.next_entry(fig, kind, **kw), who=who, note=note)


def test_stake_entries_append_only(tmp_path):
    st = OrderStepState(tmp_path / "s.sqlite", clock=lambda: 1_763_478_000.0)
    e = add(st, book_figures([], [], [], {}, "2025-11-18"), "cash_in", amount=D("1000"))
    assert e["id"] == 1 and e["kind"] == "cash_in" and e["amount"] == "1000" and e["day"] == "2025-11-18"
    assert e["units_before"] == "0" and e["units_after"] == "1000" and e["value_per_unit_after"] == "1"
    assert e["prev_hash"] == "0" * 64 and len(e["hash"]) == 64
    for sql in ("UPDATE owner_entries SET amount = '2000'", "DELETE FROM owner_entries",
                "UPDATE owner_entries SET note = 'x' WHERE id = 1"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            st._db.execute(sql)
    assert st.owner_entries() == [e]
    st.close()


@pytest.mark.parametrize("who, note", [("", "n"), ("owner", ""), ("  ", "n"), (None, "n"), ("owner", None)])
def test_an_entry_needs_who_and_note(tmp_path, who, note):
    st = OrderStepState(tmp_path / "s.sqlite")
    with pytest.raises(OrderStepError, match="who|note"):
        add(st, book_figures([], [], [], {}, "2025-11-18"), "cash_in", who=who, note=note, amount=D("1"))
    assert st.owner_entries() == []


def _three(path):
    st = OrderStepState(path, clock=lambda: 1_763_478_000.0)
    fig = book_figures([], [], [], {}, "2025-11-18")
    for amount in ("1000", "250.5", "40"):
        add(st, fig, "cash_in", amount=D(amount))
        fig = book_figures([], [], st.owner_entries(), {}, "2025-11-18")
    st.close()


def _bypass(path, sql):
    """What a program that opens the file to rewrite it can do: the triggers dropped, a row changed, put back."""
    con = sqlite3.connect(path)
    con.execute("DROP TRIGGER owner_entries_no_update")
    con.execute("DROP TRIGGER owner_entries_no_delete")
    con.execute(sql)
    for statement in osx._STATE_UPGRADE_1_TO_2:
        if statement.startswith("CREATE TRIGGER owner_entries_"):
            con.execute(statement)
    con.commit()
    con.close()


@pytest.mark.parametrize("sql, words", [
    ("UPDATE owner_entries SET amount = '2500.5' WHERE id = 2", "entry 2 was changed"),
    ("UPDATE owner_entries SET who = 'someone' WHERE id = 1", "entry 1 was changed"),
    ("DELETE FROM owner_entries WHERE id = 2", "entry 2 was removed"),
    ("DELETE FROM owner_entries WHERE id = 3", "entry 3 was removed"),
    ("DELETE FROM owner_entries", "entry 1 was removed"),
])
def test_a_changed_or_removed_entry_is_detected_and_refused_in_words(tmp_path, sql, words):
    path = tmp_path / "s.sqlite"
    _three(path)
    _bypass(path, sql)
    st = OrderStepState(path)
    with pytest.raises(OrderStepError, match=words):
        st.owner_entries()
    with pytest.raises(OrderStepError, match=words):
        add(st, book_figures([], [], [], {}, "2025-11-18"), "cash_in", amount=D("1"))
    st.close()


def test_an_entry_rewritten_with_its_own_hash_made_good_breaks_the_chain_after_it(tmp_path):
    path = tmp_path / "s.sqlite"
    _three(path)
    st = OrderStepState(path)
    row = dict(st.owner_entries()[1], amount="2500.5")
    st.close()
    row["hash"] = osx._entry_hash(row)
    _bypass(path, f"UPDATE owner_entries SET amount = '2500.5', hash = '{row['hash']}' WHERE id = 2")
    st = OrderStepState(path)
    with pytest.raises(OrderStepError, match="entry 3 was changed"):
        st.owner_entries()
    st.close()


def test_the_hash_chains_every_field(tmp_path):
    path = tmp_path / "s.sqlite"
    _three(path)
    st = OrderStepState(path)
    rows = st.owner_entries()
    assert [r["prev_hash"] for r in rows] == ["0" * 64, rows[0]["hash"], rows[1]["hash"]]
    assert len({r["hash"] for r in rows}) == 3
    st.close()


def _ledger_with(path, fills_units, symbol="SPY", price=100.0):
    """A ledger file with one filled purchase of `symbol` per figure, applied, and the stand-in venue it used."""
    clock = [1_763_478_000.0]
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
    led.reconcile(v, {})
    for i, u in enumerate(fills_units):
        tag = f"b{i}-{symbol}"
        led.intend(tag, symbol, u, price, limit_price=price + 1)
        led.submit_once(v, tag)
        v.book[tag].update(state="filled", filled_units=u, avg_price=price, fee=0.0)
        clock[0] += 30
        v.held = {symbol: v.held.get(symbol, 0.0) + u}
        led.reconcile(v, {})
        led.mark_applied([f["id"] for f in led.unapplied_fills()])
        led.reconcile(v, osx.broker_book(led.fills()))
    return led, v


def owner(root, *args):
    return cli.main(["--root", str(root), "order-step", "--venue", VENUE, *args])


def test_units_out_clears_exactly_the_shortfall_it_names(tmp_path, capsys):
    mem = tmp_path / "memory"
    led, v = _ledger_with(osx.ledger_path_for(mem, VENUE), [10.0])
    assert owner(tmp_path, "--owner-entry", "cash_in", "--amount", "2000", "--mark", "SPY=100",
                 "--who", "owner", "--note", "opening") == 0
    v.held = {"SPY": 7.0}                                               # he took 3 of the book's shares
    state = OrderStepState(osx.state_path_for(osx.ledger_path_for(mem, VENUE), VENUE))

    def book():
        fig = book_figures(led.fills(), led.orders(), state.owner_entries(), {"SPY": 100.0}, "2025-11-18")
        return {s: float(u) for s, u in fig.units.items()}
    out = led.reconcile(v, book(), one_sided=True)
    assert out["position_shortfalls"]["SPY"]["short"] == 3.0
    assert owner(tmp_path, "--owner-entry", "units_out", "--symbol", "SPY", "--units", "2", "--mark", "SPY=100",
                 "--who", "owner", "--note", "took two") == 0
    out = led.reconcile(v, book(), one_sided=True)
    assert out["position_shortfalls"]["SPY"]["short"] == 1.0 and out["clean"] is False
    assert owner(tmp_path, "--owner-entry", "units_out", "--symbol", "SPY", "--units", "1", "--mark", "SPY=100",
                 "--who", "owner", "--note", "took one more") == 0
    out = led.reconcile(v, book(), one_sided=True)
    assert out["clean"] is True and out["position_shortfalls"] == {} and out["owner_surplus"] == {}
    assert book() == {"SPY": 7.0}
    state.close()
    led.close()


def test_split_keeps_the_value_and_changes_the_units(tmp_path, capsys):
    mem = tmp_path / "memory"
    led, v = _ledger_with(osx.ledger_path_for(mem, VENUE), [4.0], price=200.0)
    assert owner(tmp_path, "--owner-entry", "cash_in", "--amount", "1000", "--mark", "SPY=200",
                 "--who", "owner", "--note", "opening") == 0
    state = OrderStepState(osx.state_path_for(osx.ledger_path_for(mem, VENUE), VENUE))
    before = book_figures(led.fills(), led.orders(), state.owner_entries(), {"SPY": 200.0}, "2025-11-18")
    assert owner(tmp_path, "--owner-entry", "split", "--symbol", "SPY", "--ratio", "4", "--mark", "SPY=50",
                 "--who", "owner", "--note", "four for one") == 0
    after = book_figures(led.fills(), led.orders(), state.owner_entries(), {"SPY": 50.0}, "2025-11-18")
    assert after.units == {"SPY": D("16.0")} and after.value == before.value
    assert after.value_per_unit == before.value_per_unit and after.units_outstanding == before.units_outstanding
    split = state.owner_entries()[-1]                                   # recorded at the marks after the split
    assert split["kind"] == "split" and split["ratio"] == "4"
    assert D(split["value_per_unit_before"]) == D(split["value_per_unit_after"]) == before.value_per_unit
    state.close()
    led.close()


def test_second_run_same_day_same_book(tmp_path, capsys):
    mem = tmp_path / "memory"
    led, _ = _ledger_with(osx.ledger_path_for(mem, VENUE), [3.0, 2.0])
    assert owner(tmp_path, "--owner-entry", "cash_in", "--amount", "5000", "--mark", "SPY=101",
                 "--who", "owner", "--note", "opening") == 0
    assert owner(tmp_path, "--owner-entry", "cash_out", "--amount", "125.75", "--mark", "SPY=101",
                 "--who", "owner", "--note", "a withdrawal") == 0
    path = osx.state_path_for(osx.ledger_path_for(mem, VENUE), VENUE)
    figs = []
    for _ in range(2):                                                  # a restart, a second run the same day
        st = OrderStepState(path)
        ro = _orders.open_read_only(osx.ledger_path_for(mem, VENUE))
        figs.append(book_figures(ro.fills(), ro.orders(), st.owner_entries(), {"SPY": 101.0}, "2025-11-18"))
        ro.close()
        st.close()
    assert figs[0] == figs[1] and figs[0].cash == D("5000") - D("500.0") - D("125.75")
    led.close()


# ---- the owner's command


def test_the_command_prints_what_it_wrote_and_writes_one_entry(tmp_path, capsys):
    assert owner(tmp_path, "--owner-entry", "cash_in", "--amount", "1234.56", "--who", "owner",
                 "--note", "an example") == 0
    printed = capsys.readouterr().out
    assert printed.startswith("written: owner entry 1, cash_in")
    shown = json.loads(printed.split("\n", 1)[1])
    path = osx.state_path_for(osx.ledger_path_for(tmp_path / "memory", VENUE), VENUE)
    st = OrderStepState(path)
    (row,) = st.owner_entries()
    st.close()
    assert shown == row and row["amount"] == "1234.56" and row["who"] == "owner" and row["note"] == "an example"


@pytest.mark.parametrize("drop", ["--who", "--note"])
def test_stake_entry_needs_who_and_note(tmp_path, capsys, drop):
    args = {"--owner-entry": "cash_in", "--amount": "10", "--who": "owner", "--note": "n"}
    args.pop(drop)
    assert owner(tmp_path, *[x for kv in args.items() for x in kv]) == 2
    err = capsys.readouterr().err
    assert err.startswith("refused:") and drop[2:] in err
    assert not osx.state_path_for(osx.ledger_path_for(tmp_path / "memory", VENUE), VENUE).exists()


@pytest.mark.parametrize("figure", ["0", "0.00", "-5", "1e3", "1,000", "nan", "inf", "abc", "", " 5", "5.", ".5",
                                    "+5", "0x10", "١٠"])
def test_a_figure_that_is_not_a_positive_decimal_is_refused(tmp_path, capsys, figure):
    assert owner(tmp_path, "--owner-entry", "cash_in", "--amount", figure, "--who", "owner", "--note", "n") == 2
    assert capsys.readouterr().err.startswith("refused:")
    assert not osx.state_path_for(osx.ledger_path_for(tmp_path / "memory", VENUE), VENUE).exists()


@pytest.mark.parametrize("args, words", [
    (["--owner-entry", "cash_in"], "amount"),
    (["--owner-entry", "cash_out"], "amount"),
    (["--owner-entry", "units_out", "--symbol", "SPY", "--mark", "SPY=100"], "units"),
    (["--owner-entry", "units_out", "--symbol", "SPY", "--units", "1"], "mark"),
    (["--owner-entry", "split", "--symbol", "SPY"], "ratio"),
    (["--owner-entry", "split", "--ratio", "2"], "symbol"),
    (["--owner-entry", "cash_in", "--amount", "5", "--units", "3"], "units"),
])
def test_no_figure_is_defaulted_and_none_is_taken_that_the_kind_does_not_use(tmp_path, capsys, args, words):
    assert owner(tmp_path, *args, "--who", "owner", "--note", "n") == 2
    err = capsys.readouterr().err
    assert err.startswith("refused:") and words in err


def test_a_symbol_the_book_holds_needs_its_mark(tmp_path, capsys):
    _ledger_with(osx.ledger_path_for(tmp_path / "memory", VENUE), [2.0])[0].close()
    assert owner(tmp_path, "--owner-entry", "cash_in", "--amount", "5", "--who", "owner", "--note", "n") == 2
    err = capsys.readouterr().err
    assert "SPY" in err and "--mark" in err
    st = OrderStepState(osx.state_path_for(osx.ledger_path_for(tmp_path / "memory", VENUE), VENUE))
    assert st.owner_entries() == []
    st.close()


@pytest.mark.parametrize("extra", [["--propose"], ["--approve", "123456"], ["--live"], ["--check"],
                                   ["--capital", "100"], ["--fractional"]])
def test_the_command_is_refused_beside_the_runs_own_flags(tmp_path, capsys, extra):
    assert owner(tmp_path, "--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n", *extra) == 2
    assert "on its own" in capsys.readouterr().err


@pytest.mark.parametrize("name", [None, "", "../x", "a b"])
def test_the_command_needs_a_plain_venue_name(tmp_path, capsys, name):
    args = ["--root", str(tmp_path), "order-step", "--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n"]
    if name is not None:
        args += ["--venue", name]
    assert cli.main(args) == 2
    assert "venue" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["webull-production", "webull-production-events", "WEBULL-PRODUCTION",
                                  "Webull-Production-Events", "webull-production.", "webull-production.."])
def test_a_production_venues_name_is_refused_in_words_until_live_entries_open(tmp_path, capsys, name):
    """Addendum 7, R23: the name alone picks the live venue's state file; refused among the first refusals."""
    from valedia_trader.execution_and_risk import go_live
    assert go_live.LIVE_VENUE == "webull-production"                   # the name the code already has
    assert cli.main(["--root", str(tmp_path), "order-step", *_ok("--venue", name)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("refused:") and "production" in err and "--live" in err and "nothing was written" in err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["webull-sandbox", "webull-sandbox-events", "webull-test-cash", "production",
                                  "webull-productio"])
def test_a_practice_venues_entry_works_as_before(tmp_path, capsys, name):
    assert cli.main(["--root", str(tmp_path), "order-step", "--venue", name, "--owner-entry", "cash_in", "--amount",
                     "5", "--who", "o", "--note", "n"]) == 0


def test_the_command_is_refused_while_a_run_holds_the_lock(tmp_path, capsys):
    path = osx.state_path_for(osx.ledger_path_for(tmp_path / "memory", VENUE), VENUE)
    lock = osx._RunLock(str(path))
    assert lock.acquire()
    try:
        assert owner(tmp_path, "--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n") == 2
        assert "run" in capsys.readouterr().err
    finally:
        lock.release()
    assert owner(tmp_path, "--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n") == 0


@pytest.mark.parametrize("reading", [float("nan"), float("inf"), None, "noon", -1e300])
def test_the_command_is_refused_when_the_clock_gives_no_new_york_day(tmp_path, capsys, monkeypatch, reading):
    """Repair item 2: refused in words, with the other refusals' exit code, and nothing written: no state file, no
    lock file, no folder."""
    monkeypatch.setattr(osx, "wall_clock", lambda: reading)
    assert owner(tmp_path, "--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n") == 2
    err = capsys.readouterr().err
    assert err.startswith("refused:") and "New York day" in err and "nothing was written" in err
    assert not (tmp_path / "memory").exists()


def test_the_clock_is_read_once_and_that_reading_is_the_entrys_time_and_day(tmp_path, capsys, monkeypatch):
    """Addendum 4, R13: one reading of the clock; a clock that would give no day on a later reading changes nothing."""
    readings = iter([1_763_478_000.0])
    monkeypatch.setattr(osx, "wall_clock", lambda: next(readings, float("nan")))
    assert owner(tmp_path, "--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n") == 0
    st = OrderStepState(osx.state_path_for(osx.ledger_path_for(tmp_path / "memory", VENUE), VENUE))
    (row,) = st.owner_entries()
    st.close()
    assert row["time"] == 1_763_478_000.0 and row["day"] == "2025-11-18"


# ---- addendum 4, R12: the journal is the witness outside the file


def state_of(root):
    return Path(osx.state_path_for(osx.ledger_path_for(root / "memory", VENUE), VENUE))


def journaled(root, venue=VENUE):
    return [e for e in _paper.Journal(root / "journal").read() if e.get("type") == "owner_entry"
            and e.get("venue") == venue] if (root / "journal").exists() else []


def _entries_via_command(root, amounts=("1000", "250.5", "40")):
    for a in amounts:
        assert owner(root, "--owner-entry", "cash_in", "--amount", a, "--who", "owner", "--note", f"in {a}") == 0
    st = OrderStepState(state_of(root))
    rows = st.owner_entries()
    st.close()
    return rows


def test_each_entry_is_journaled_through_the_journals_own_writer(tmp_path, capsys):
    rows = _entries_via_command(tmp_path)
    lines = journaled(tmp_path)
    assert [ln["id"] for ln in lines] == [1, 2, 3]
    for ln, row in zip(lines, rows):
        assert ln["type"] == "owner_entry" and ln["venue"] == VENUE and ln["day"] == row["day"]
        for k in ("id", "kind", "amount", "symbol", "units", "ratio", "mark", "units_before", "units_after",
                  "value_per_unit_before", "value_per_unit_after", "who", "note", "prev_hash", "hash"):
            assert ln[k] == row[k], k
        assert all(isinstance(ln[k], str) for k in ("amount", "units_before", "units_after"))   # figures as text
    assert osx.owner_entry_differences(rows, _paper.Journal(tmp_path / "journal").read(), venue=VENUE) == []


COMPARED = ("id", "kind", "symbol", "amount", "units", "ratio", "mark", "units_before", "units_after",
            "value_per_unit_before", "value_per_unit_after", "prev_hash", "hash", "time")   # addendum 6, R19: time


@pytest.mark.parametrize("field", [f for f in COMPARED if f != "id"] + ["who", "note"])
def test_the_comparison_holds_every_journaled_field_but_who_and_note(tmp_path, capsys, field):
    """Addendum 5, R16: a journal line changed in any figure, its hash kept, differs; who and note are not compared
    (the journal's writer may redact them)."""
    rows = _entries_via_command(tmp_path, ("1000", "20"))
    lines = _paper.Journal(tmp_path / "journal").read()
    changed = [dict(x, **{field: "999"}) if x.get("type") == "owner_entry" and x.get("id") == 2 else x for x in lines]
    got = osx.owner_entry_differences(rows, changed, venue=VENUE)
    if field in ("who", "note"):
        assert got == []
    else:
        assert len(got) == 1 and "entry 2" in got[0] and field in got[0], got
    day_file = next((tmp_path / "journal").glob("????-??-??.jsonl"))
    day_file.write_text("".join(json.dumps(x) + "\n" for x in changed))
    before = _listing(tmp_path)
    capsys.readouterr()
    code = owner(tmp_path, "--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n")
    assert code == (0 if field in ("who", "note") else 2)
    if code == 2:
        assert field in capsys.readouterr().err and _listing(tmp_path) == before


def _rehash_from(rows, start, change):
    """The chain rewritten from entry `start` on, every hash recomputed: what a rewrite of the file can do."""
    out, prev = [], (rows[start - 2]["hash"] if start > 1 else "0" * 64)
    for r in rows:
        r = dict(r)
        if r["id"] >= start:
            if r["id"] == start:
                r.update(change)
            r["prev_hash"] = prev
            r["hash"] = osx._entry_hash(r)
        prev = r["hash"]
        out.append(r)
    return out


def _put_rows(path, rows, seq):
    """Replace the file's entries by `rows` and its id counter by `seq`, the triggers out of the way and put back."""
    con = sqlite3.connect(path)
    con.execute("DROP TRIGGER owner_entries_no_update")
    con.execute("DROP TRIGGER owner_entries_no_delete")
    con.execute("DELETE FROM owner_entries")
    cols = (*osx._ENTRY_FIELDS, "hash")
    for r in rows:
        con.execute(f"INSERT INTO owner_entries ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    tuple(r[k] for k in cols))
    con.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'owner_entries'", (seq,))
    for statement in osx._STATE_UPGRADE_1_TO_2:
        if statement.startswith("CREATE TRIGGER owner_entries_"):
            con.execute(statement)
    con.commit()
    con.close()


def _appended_by_hand(rows):
    extra = dict(rows[-1], id=rows[-1]["id"] + 1, amount="99", note="by hand", prev_hash=rows[-1]["hash"],
                 units_before=rows[-1]["units_after"], units_after=str(D(rows[-1]["units_after"]) + 99))
    extra["hash"] = osx._entry_hash(extra)
    return rows + [extra]


THREE_CHANGES = {
    "the chain rewritten from entry 2, every hash recomputed": (lambda rows: _rehash_from(rows, 2, {"amount": "2500.5"}),
                                                              3, "entry 2"),
    "the last entry dropped and the id counter set back": (lambda rows: rows[:-1], 2, "entry 3"),
    "a row appended by hand with a correct hash": (_appended_by_hand, 4, "entry 4"),
}


@pytest.mark.parametrize("change", list(THREE_CHANGES))
def test_the_three_changes_the_chain_cannot_see_are_found_against_the_journal(tmp_path, capsys, change):
    rows = _entries_via_command(tmp_path)
    make, seq, words = THREE_CHANGES[change]
    _put_rows(state_of(tmp_path), make(rows), seq)
    st = OrderStepState(state_of(tmp_path))
    changed = st.owner_entries()                                        # the chain inside the file reads it as good
    st.close()
    before = _listing(tmp_path)
    capsys.readouterr()
    code = owner(tmp_path, "--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n")
    err = capsys.readouterr().err
    assert code == 2, (code, err)                                       # before addendum 4: written (code 0)
    assert err.startswith("refused:") and words in err and "journal" in err and "nothing was written" in err
    assert _listing(tmp_path) == before
    diffs = osx.owner_entry_differences(changed, _paper.Journal(tmp_path / "journal").read(), venue=VENUE)
    assert diffs and any(words in d for d in diffs), diffs


def test_the_differences_in_words():
    rows = [{"id": i, "prev_hash": f"p{i}", "hash": f"h{i}"} for i in (1, 2, 3)]
    line = lambda i, h=None, venue=VENUE: {"type": "owner_entry", "venue": venue, "id": i, "prev_hash": f"p{i}",
                                           "hash": h or f"h{i}"}
    other = [{"type": "order_step", "venue": VENUE, "id": 9}, line(7, venue="another-venue")]
    diff = osx.owner_entry_differences
    assert diff(rows, other + [line(1), line(2), line(3)], venue=VENUE) == []
    assert diff([], other, venue=VENUE) == []
    got = diff(rows, [line(1), line(3)], venue=VENUE)
    assert len(got) == 1 and "entry 2" in got[0] and "not in the journal" in got[0]
    got = diff(rows[:2], [line(1), line(2), line(3)], venue=VENUE)    # a state file put back from an older copy
    assert len(got) == 1 and "entry 3" in got[0] and "not in the state file" in got[0]
    got = diff(rows, [line(1), line(2, "hx"), line(3)], venue=VENUE)
    assert len(got) == 1 and "entry 2" in got[0] and "another hash" in got[0]
    got = diff(rows, [line(1), line(3), line(2)], venue=VENUE)
    assert len(got) == 1 and "order" in got[0]
    got = diff(rows, [line(1), line(2), line(2), line(3)], venue=VENUE)
    assert len(got) == 1 and "entry 2" in got[0] and "twice" in got[0]
    assert diff(rows, [line(1), line(2), line(3), {"type": "owner_entry", "venue": VENUE, "id": "x"}], venue=VENUE)


def test_a_line_of_the_journal_that_is_not_whole_is_skipped_as_a_run_skips_it(tmp_path, capsys):
    """Addendum 5, R18: a torn line no longer refuses by itself; the comparison says what is missing."""
    _entries_via_command(tmp_path, ("1000",))
    day_file = next((tmp_path / "journal").glob("????-??-??.jsonl"))
    with day_file.open("ab") as fh:
        fh.write(b'{"type": "order_step", "venue": "' + VENUE.encode() + b'", "event": "summ')   # a torn line
    assert owner(tmp_path, "--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n") == 0
    assert [ln["id"] for ln in journaled(tmp_path)] == [1, 2]


def test_a_torn_line_that_was_an_owners_entry_is_found_missing_and_refused(tmp_path, capsys):
    _entries_via_command(tmp_path, ("1000", "20"))
    day_file = next((tmp_path / "journal").glob("????-??-??.jsonl"))
    data = day_file.read_bytes().rstrip(b"\n")
    day_file.write_bytes(data[:-30])                                     # entry 2's line cut short by a kill
    before = _listing(tmp_path)
    capsys.readouterr()
    assert owner(tmp_path, "--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n") == 2
    err = capsys.readouterr().err
    assert err.startswith("refused:") and "entry 2 is in the state file and not in the journal" in err
    assert _listing(tmp_path) == before


@pytest.mark.parametrize("behind", [0.0, 1.0, 86_400.0])
def test_an_entry_not_later_than_the_last_one_is_refused_before_anything_is_written(tmp_path, capsys, monkeypatch,
                                                                                     behind):
    """Addendum 5, R18: the clock is behind the record; it would be filed in an earlier day's journal."""
    monkeypatch.setattr(osx, "wall_clock", lambda: 1_763_478_000.0)
    _entries_via_command(tmp_path, ("1000",))
    monkeypatch.setattr(osx, "wall_clock", lambda: 1_763_478_000.0 - behind)
    before = _listing(tmp_path)
    capsys.readouterr()
    assert owner(tmp_path, "--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n") == 2
    err = capsys.readouterr().err
    assert err.startswith("refused:") and "behind" in err and "nothing was written" in err
    assert _listing(tmp_path) == before


def test_a_layout_1_file_is_read_as_no_entries_and_upgraded_only_by_an_entry_written(tmp_path, capsys, base):
    led, _ = _ledger_with(osx.ledger_path_for(tmp_path / "memory", VENUE), [10.0])
    led.close()
    layout1_state(base, state_of(tmp_path))
    assert osx._owner_entries_read_only(state_of(tmp_path)) == [] and dump(state_of(tmp_path))["version"] == 1
    assert owner(tmp_path, "--owner-entry", "cash_in", "--amount", "5", "--mark", "SPY=100", "--who", "o",
                 "--note", "n") == 0
    assert dump(state_of(tmp_path))["version"] == 2 and [ln["id"] for ln in journaled(tmp_path)] == [1]


def test_only_the_command_calls_the_comparison_in_this_build():
    src = Path(osx.__file__).read_text()
    assert src.count("owner_entry_differences(") == 2                   # its definition and the command's own check
    assert "owner_entry_differences" not in Path(cli.__file__).read_text()


def test_a_journal_line_that_cannot_be_written_is_said_and_has_its_own_exit_code(tmp_path, capsys, monkeypatch):
    def broken(self, day, entry):
        raise OSError("the disk is full")
    monkeypatch.setattr(_paper.Journal, "write", broken)
    code = owner(tmp_path, "--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n")
    assert code not in (0, 2, 1) and isinstance(code, int)
    err = capsys.readouterr().err
    assert "journal" in err and "the disk is full" in err and "state file" in err
    st = OrderStepState(state_of(tmp_path))
    assert len(st.owner_entries()) == 1                                 # the entry stays; the next command finds it
    st.close()
    monkeypatch.undo()
    assert owner(tmp_path, "--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n") == 2
    assert "entry 1" in capsys.readouterr().err


# ---- addendum 4, R13: a refusal writes nothing


def _listing(root):
    """Every path under `root` and every file's bytes."""
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else "folder") for p in sorted(root.rglob("*"))}


def _ok(*extra):
    return ["--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n", "--mark", "SPY=100", *extra]


def _hold_lock(root, stack):
    lock = osx._RunLock(str(state_of(root)))
    assert lock.acquire()
    stack.callback(lock.release)


def _with_entries(root):
    return state_of(root).exists() and dump(state_of(root))["version"] == 2


def _break_chain(root, stack):
    if not _with_entries(root):
        pytest.skip("no entries to change in this layout")
    _bypass(state_of(root), "UPDATE owner_entries SET amount = '7' WHERE id = 1")


def _clock_behind(root, stack):
    if not _with_entries(root):
        pytest.skip("no entry for the clock to be behind")
    last = OrderStepState(state_of(root))
    t = last.owner_entries()[-1]["time"]
    last.close()
    mp = stack.enter_context(pytest.MonkeyPatch.context())
    mp.setattr(osx, "wall_clock", lambda: t - 86_400.0)


def _needs_a_holding(root, stack):
    if not osx.ledger_path_for(root / "memory", VENUE).exists():
        pytest.skip("no ledger, so the book holds nothing to mark")


def _journal_only(root, stack):
    _paper.Journal(root / "journal").write("2025-11-18", {"type": "owner_entry", "venue": VENUE, "id": 9,
                                                          "prev_hash": "p", "hash": "h"})


def _no_value(root, stack):
    real = osx.book_figures
    mp = stack.enter_context(pytest.MonkeyPatch.context())
    mp.setattr(osx, "book_figures", lambda *a, **k: __import__("dataclasses").replace(
        real(*a, **k), value=None, value_high=None, value_per_unit=None, cash_bounded=True,
        bounded_by=("order b0-SPY: its fee is not known",)))


def _no_day(root, stack):
    mp = stack.enter_context(pytest.MonkeyPatch.context())
    mp.setattr(osx, "wall_clock", lambda: float("nan"))


def _other_layout(root, stack):
    if not state_of(root).exists():
        pytest.skip("no state file in this layout")
    if state_of(root).exists():
        con = sqlite3.connect(state_of(root))
        con.execute("PRAGMA user_version = 7")
        con.commit()
        con.close()


REFUSALS = {
    "beside a run's own flag": (_ok("--propose"), None),
    "without who": (["--owner-entry", "cash_in", "--amount", "5", "--note", "n", "--mark", "SPY=100"], None),
    "without a note": (["--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--mark", "SPY=100"], None),
    "a figure missing": (["--owner-entry", "cash_in", "--who", "o", "--note", "n", "--mark", "SPY=100"], None),
    "a figure the kind does not use": (_ok("--units", "3"), None),
    "a figure that is not a positive decimal": (["--owner-entry", "cash_in", "--amount", "1e3", "--who", "o",
                                                 "--note", "n", "--mark", "SPY=100"], None),
    "a mark that is not SYMBOL=PRICE": (["--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n",
                                         "--mark", "SPY"], None),
    "a symbol that is not plain": (["--owner-entry", "units_out", "--symbol", "a b", "--units", "1", "--who", "o",
                                    "--note", "n", "--mark", "SPY=100"], None),
    "units_out without its mark": (["--owner-entry", "units_out", "--symbol", "QQQ", "--units", "1", "--who", "o",
                                    "--note", "n", "--mark", "SPY=100"], None),
    "a held symbol without its mark": (["--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n"],
                                       _needs_a_holding),
    "who over 200 characters": (["--owner-entry", "cash_in", "--amount", "5", "--who", "o" * 201, "--note", "n",
                                 "--mark", "SPY=100"], None),
    "a note over 1000 characters": (["--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n" * 1001,
                                     "--mark", "SPY=100"], None),
    "who holding a newline": (["--owner-entry", "cash_in", "--amount", "5", "--who", "o\nx", "--note", "n",
                               "--mark", "SPY=100"], None),
    "a note holding a tab": (["--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n\tx",
                              "--mark", "SPY=100"], None),
    "a clock behind the last entry": (_ok(), _clock_behind),
    "a split of a symbol the book does not hold": (["--owner-entry", "split", "--symbol", "QQQ", "--ratio", "2",
                                                    "--who", "o", "--note", "n", "--mark", "SPY=100"], None),   # R21
    **{f"a production venue's name {name!r}": (_ok("--venue", name), None)                         # addendum 7, R23
       for name in ("webull-production", "webull-production-events", "WEBULL-Production", "webull-production.",
                    "webull-production ")},
    "more than the book's cash": (["--owner-entry", "cash_out", "--amount", "999999999", "--who", "o", "--note", "n",
                                   "--mark", "SPY=100"], None),
    "more than the book holds": (["--owner-entry", "units_out", "--symbol", "SPY", "--units", "1000", "--who", "o",
                                  "--note", "n", "--mark", "SPY=100"], None),
    "the book's value not known": (_ok(), _no_value),
    "a clock with no New York day": (_ok(), _no_day),
    "a run holding the lock": (_ok(), _hold_lock),
    "an entry changed in the file": (_ok(), _break_chain),
    "the journal holding an entry the file does not": (_ok(), _journal_only),
    "a state file of another layout": (_ok(), _other_layout),
    "no venue": (None, None),
}


@pytest.mark.parametrize("layout", ["layout 1", "layout 2", "no state file", "no folder"])
@pytest.mark.parametrize("refusal", list(REFUSALS))
def test_prop_every_refusal_leaves_the_folder_and_every_file_as_they_were(tmp_path, capsys, base, layout, refusal):
    """Addendum 4, R13, and addendum 5, R15: over every refusal the command has, on a layout-1 file, a layout-2 file,
    a folder with no state file and no folder at all, the listing of the root's parent and every file's bytes are the
    same before and after (the lock file, the state file's upgrade, the folders, the journal), and no lock file exists
    after a refusal where there was none."""
    import contextlib
    root = tmp_path / "root"
    if layout != "no folder":
        led, _ = _ledger_with(osx.ledger_path_for(root / "memory", VENUE), [10.0])
        led.close()
    if layout == "layout 1":
        layout1_state(base, state_of(root))
    elif layout == "layout 2":
        assert owner(root, "--owner-entry", "cash_in", "--amount", "5000", "--mark", "SPY=100", "--who", "owner",
                     "--note", "opening") == 0
    args, prep = REFUSALS[refusal]
    lock = Path(str(state_of(root)) + ".lock")
    with contextlib.ExitStack() as stack:
        if prep is not None:
            prep(root, stack)
        had_lock = lock.exists()
        before = _listing(tmp_path)
        capsys.readouterr()
        if args is None:
            code = cli.main(["--root", str(root), "order-step", *_ok()])
        else:
            code = owner(root, *args)
        err = capsys.readouterr().err
        assert code == 2 and err.startswith("refused:"), (code, err)
        assert _listing(tmp_path) == before
        assert had_lock or not lock.exists()


def test_the_order_step_without_the_owner_flag_is_called_as_before(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_order_step_build", lambda: seen.append("built") or None)
    assert cli.main(["--root", str(tmp_path), "order-step"]) == 2
    assert seen == ["built"]
    monkeypatch.setattr(osx, "owner_entry_command", lambda *a, **k: seen.append("owner") or 0)
    assert cli.main(["--root", str(tmp_path), "order-step", "--owner-entry", "cash_in"]) == 0
    assert seen == ["built", "owner"]                                   # no venue is built for an owner's entry


# ---- R3: the local check


def test_r3_every_tag_written_is_in_the_ledger():
    journal = [{"type": "order_step", "venue": VENUE, "event": "order", "tag": "b1-SPY"},
               {"type": "order_step", "venue": VENUE, "event": "fill", "tag": "b2-QQQ"},
               {"type": "order_step", "venue": "other", "event": "order", "tag": "z9-IWM"},   # another venue's
               {"type": "paper", "tag": "p1-SPY"},
               {"type": "order_step", "venue": VENUE, "event": "summary"}]
    assert osx.untracked_tags({"b1-SPY", "b2-QQQ"}, venue=VENUE, journal=journal) == []
    got = osx.untracked_tags({"b1-SPY"}, venue=VENUE, journal=journal, sends=["b1-SPY", "b3-TLT"],
                             tickets=[("b4-GLD", "sent"), ("b5-XLE", None), ("b6-XLF", "not sent"),
                                      ("b7-IWM", "left over")])
    assert [(g["tag"], g["where"]) for g in got] == [("b2-QQQ", "journal"), ("b3-TLT", "send record"),
                                                     ("b4-GLD", "ticket"), ("b5-XLE", "ticket")]
    assert all("not in the ledger" in g["reason"] for g in got)


def test_r3_names_a_ledger_restored_from_an_older_copy(tmp_path):
    mem = tmp_path / "memory"
    path = osx.ledger_path_for(mem, VENUE)
    led, _ = _ledger_with(path, [1.0])
    led.close()
    old = path.read_bytes()
    led, _ = _ledger_with(tmp_path / "later.sqlite", [1.0, 2.0])
    tags = {o["tag"] for o in led.orders()}
    led.close()
    st = OrderStepState(osx.state_path_for(path, VENUE))
    for t in sorted(tags):
        st.note_sending(t, "2025-11-18")
    assert st.send_tags() == tags
    restored = _orders.open_read_only(path)
    assert path.read_bytes() == old
    got = osx.untracked_tags({o["tag"] for o in restored.orders()}, venue=VENUE, sends=st.send_tags())
    assert [g["tag"] for g in got] == ["b1-SPY"]
    restored.close()
    st.close()


def test_alerts_reads_nothing_of_the_state_file():
    """Outcome 4: core/alerts.py opens the order ledgers (orders-*.sqlite) and the journal, never the order step's
    state file, so a layout-2 state file changes nothing it reads."""
    src = (Path(osx.__file__).resolve().parents[1] / "core" / "alerts.py").read_text()
    assert "order_step-" not in src and "OrderStepState" not in src and "state_path_for" not in src
    assert 'glob("orders-*.sqlite")' in src


# ---- outcome 4: nothing else changes


layout1_view = T._t51c_layout1_view                                    # one mask, shared with the base comparisons


def test_the_layout1_view_masks_only_the_layout_number_and_the_new_empty_tables(tmp_path, base):
    path = tmp_path / "s.sqlite"
    before = layout1_state(base, path)
    OrderStepState(path).close()
    assert layout1_view(dump(path)) == before
    whole = dump(path)
    for table in ("owner_entries", "book_nav"):                         # each new table must be there, and empty
        with pytest.raises(AssertionError):
            layout1_view({**whole, "rows": {**whole["rows"], table: [("a row",)]}})
        with pytest.raises(AssertionError):
            layout1_view({**whole, "rows": {k: v for k, v in whole["rows"].items() if k != table}})
    for statement in osx._STATE_UPGRADE_1_TO_2:                         # and every statement of the upgrade in it
        with pytest.raises(AssertionError):
            layout1_view({**whole, "schema": [s for s in whole["schema"] if s != statement]})
    con = sqlite3.connect(path)
    con.execute("INSERT INTO book_nav (day, time, batch, units_outstanding, cash_bounded, marks, marks_hash, entry_hash) "
                "VALUES ('2025-11-18', 1.0, 'b', '1', 0, '{}', 'h', 'e')")
    con.commit()
    con.close()
    with pytest.raises(AssertionError):
        layout1_view(dump(path))                                        # a row in a new table is never masked
    with pytest.raises(AssertionError):
        layout1_view(before)                                            # a layout-1 dump is not layout 2


def test_practice_runs_equal_cdd5371_but_for_the_state_layout(tmp_path, base, capsys):
    worlds = fractional = sends = 0
    for k in range(24):
        for kind, run in (("step", T._scenario), ("flow", T._flow_scenario)):
            seed = 5_150_000 + k * 7 + (0 if kind == "step" else 3)
            with pytest.MonkeyPatch.context() as mp:                    # the scenario undoes its own patches
                b = run(base, tmp_path / f"b{kind}{k}", seed, mp)
            with pytest.MonkeyPatch.context() as mp:
                m = run(osx, tmp_path / f"m{kind}{k}", seed, mp)
            fractional += seed in T._T51_DREW_FRACTIONAL
            assert len(b) == len(m), (seed, kind)
            for i, (x, y) in enumerate(zip(b, m)):
                assert x.get("state") is None or x["state"]["version"] == 1
                y = dict(y, state=layout1_view(y.get("state")))
                assert set(x) == set(y), (seed, i)
                for key in x:
                    assert x[key] == y[key], (seed, kind, i, key)
            sends += sum(1 for r in m for c in r["calls"] if c[0] == "submit")
            worlds += 1
    with capsys.disabled():
        print(f"\nT51c-O4 {worlds} worlds equal to {BASE} but for the state layout; fractional drawn in {fractional}; "
              f"{sends} orders sent")
    assert worlds >= 30 and fractional >= 3 and worlds - fractional >= 3 and sends > 30
