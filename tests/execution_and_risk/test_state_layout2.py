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
    assert set(d["rows"]) == {"meta", "baseline", "equity", "sends", "owner_entries", "sqlite_sequence"}
    assert d["rows"]["owner_entries"] == []


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
    assert set(after["rows"]) - set(before["rows"]) == {"owner_entries"} and after["rows"]["owner_entries"] == []
    assert set(before["schema"]) <= set(after["schema"])                # the old tables' schema text unchanged
    assert T._dump(path) == after
    OrderStepState(path).close()                                        # once: a second open changes nothing
    assert dump(path) == after


def test_the_upgrade_happens_inside_one_transaction_or_not_at_all(tmp_path, base, monkeypatch):
    path = tmp_path / "s.sqlite"
    before = layout1_state(base, path)
    monkeypatch.setattr(osx, "_STATE_UPGRADE_1_TO_2", list(osx._STATE_UPGRADE_1_TO_2) + ["CREATE TABLE meta (x)"])
    with pytest.raises(sqlite3.Error):
        OrderStepState(path)
    assert dump(path) == before                                         # nothing of the upgrade stayed
    monkeypatch.undo()
    OrderStepState(path).close()
    assert dump(path)["version"] == 2


def test_the_code_before_this_build_refuses_a_layout_2_file_in_words(tmp_path, base):
    path = tmp_path / "s.sqlite"
    layout1_state(base, path)
    OrderStepState(path).close()
    seen = dump(path)
    with pytest.raises(base.OrderStepError, match="is not an order-step state file of layout 1; it is not opened"):
        base.OrderStepState(path)
    assert dump(path) == seen


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
    for statement in osx._STATE_UPGRADE_1_TO_2[1:]:
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


def layout1_view(d):
    """A layout-2 state dump seen as layout 1, after asserting it IS layout 2 with the entries table empty and the
    schema exactly layout 1's plus the upgrade's statements."""
    if d is None:
        return None
    assert d["version"] == 2 and d["rows"].get("owner_entries") == [], d["version"]
    added = set(osx._STATE_UPGRADE_1_TO_2)
    assert added <= set(d["schema"]), d["schema"]
    rows = {k: v for k, v in d["rows"].items() if k != "owner_entries"}
    return {**d, "version": 1, "schema": sorted(s for s in d["schema"] if s not in added), "rows": rows}


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
