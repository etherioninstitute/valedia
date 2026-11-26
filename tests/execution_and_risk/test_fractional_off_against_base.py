"""T51 A4, R2 with addendum 2's ruling A: with `fractional` OFF nothing changes.

PROPERTY: for every input, a run with `fractional` off writes the same orders, ledger rows, journal lines, ticket
file, tags, faults, summary and exit status as the accepted order path at commit 75a51f5 (the practice gate's commit,
S4e merged on main), and hands the venue and the second price source exactly the same calls. This is the guard for
T51 build B: whatever build B changes, a run with fractional off must still write exactly what 75a51f5 writes.

History of the pin: it was 33ace6f (T51's own base) until T51c S4c-2 changed go_live.py and stake_ladder.py on purpose;
it was then re-pinned on purpose to 75a51f5 (T51c re-pin ticket). Today every module either side imports is
byte-identical between the two sides except the four in MAY_DIFFER, each with its reason below (the big run's three
changes to the order path, BR-A, BR-W and BR-E, are among them).

How it is shown: seeded worlds of every kind of day (ordinary days, partial fills, refusals by the venue, an order
left over by the day's cap, a fill through its limit, a stopped run, a ticket proposed and approved, a ticket that
expires, a second run the same day, a baseline of holdings that are not the book's, and live mode on a stand-in
production venue, propose and approve, its confirmation files in the test's own folder) are each driven twice from
the same seed: once by a fresh interpreter that imports ONLY 75a51f5's whole `valedia_trader` package (taken with
`git archive 75a51f5` into the test's temporary folder), once by a fresh interpreter that imports ONLY this tree's.
Each side reports the file of every `valedia_trader` module it imported: none may come from the other side, and
every one of them must be byte-identical between the two sides except `execution_and_risk/order_step.py`,
`execution_and_risk/orders.py`, `venues/webull.py` (T51 build B changes these three on purpose) and
`execution_and_risk/go_live.py` (BR-A); MAY_DIFFER says why. A later change on main to a module the run
imports fails these tests in words naming it (the base must then be re-pinned on purpose, or the module added to
the list on purpose); a changed module that no run imports does not matter. If 75a51f5 cannot be read from this
checkout's history the tests fail in words. After every run the whole world folder is compared, file by file.

What is masked, and why (nothing else is):
- M1 the journal's `"timestamp": "..."` field: the journal stamps each line with the wall clock
  (paper.Journal.write, datetime.now), which no test controls. Every other byte of every journal file is compared.
  STILL NEEDED against 75a51f5 (the clock is the same on both sides).
- M4 the summary key `held_lock` (BR-E: `_finish` of the order step ends every summary with `held_lock`, a bool: whether the
  run held the run lock; the base's summaries never carry it). It is admitted in the returned summary and in the journal's
  summary lines, and nowhere else (`_summary_same_but_held_lock`, `_journal_same_but_held_lock`): it must be there on this
  tree and a real bool, absent on the base, last on a journal line, and the summary or line is otherwise the base's own
  text. Any other key, in either direction, any other event's line, or a `held_lock` that is missing or not a bool on this
  tree is a difference. The tests `test_r2_m4_...` show both sides of this.
- M5 (CF-Q2) the two keys `quote_ts` and `quote_age_s` that end every journal `order` line of this tree (the time of
  the quote the order was priced from, ISO 8601 in UTC, and its age then, a real number from -QUOTE_MAX_AHEAD_SECONDS
  to PRIMARY_QUOTE_MAX_AGE_SECONDS): such a line, with the two taken out, must be the base's own line. On any other
  line, out of place, malformed or out of range, they are a difference (`test_r2_m5_...`).
- M6 (CF-C) the two summary keys `sale_caps` and `sales_left_unsettled` that a run of this tree with a sale gives,
  just before `held_lock`. This tree's side counts every lot of its book as settled (side_main: the real
  book_settled_units asked for a day far ahead), so its sales are bounded as the base's are; the two keys are then
  admitted in the returned summary and on journal summary lines only, absent on the base, with no unit held back
  (`sales_left_unsettled` empty, each cap's `units` its `wanted`); otherwise they are a difference (`_m6_strip`).
  The cap itself is tested in test_sale_cap.py.
- M2 (the order ledger's layout: 33ace6f wrote layout 3, this tree layout 4) and M3 (the order step's state file's
  layout: 33ace6f wrote layout 1, this tree layout 2) are REMOVED. 75a51f5 writes layout 4 and layout 2 as this tree
  does, so the ledger file and the state file are compared byte for byte like every other file; a difference in
  either layout, schema or row now shows as a difference.
- Random ids are NOT masked: the batch ids and the state file's account key (os.urandom) and the ticket numbers
  (secrets.randbelow) are drawn on both sides from the same seeded source, so they must be equal too.
Every other file (ledger, state, ticket file, the live owner's files, the limits file) is compared byte for byte, and
every other byte of a journal file is compared (apart from M1, M4 and M5).

A sanity pair shows the comparison can fail: two different worlds, and one world with a single input changed by a
cent, are each reported as different.

Nothing here opens a socket or reaches the real second price source (the autouse guard fails the test)."""
import hashlib
import io
import json
import math
import os
import pickle
import random
import re
import socket
import sqlite3
import subprocess
import sys
import tarfile
import zlib
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from valedia_trader.data_and_fees import second_quote
from valedia_trader.execution_and_risk import order_step as osx
from valedia_trader.execution_and_risk import orders as _orders
from valedia_trader.venues import webull as _wb
from test_order_step import Booked, CF_C_KEYS, GENEROUS, LiveVenue, _run_with, cf_c_strip, live_world, paper, ticket_world

BASE = "75a51f5"
# What may differ between the base and this tree: the modules T51 build B changes on purpose, each for a reason, and
# nothing else:
#   - execution_and_risk/order_step.py and execution_and_risk/orders.py: the subject of the comparison (the live
#     opening for fractions, the proof reading, first contact, the quote re-ask; the ledger's schema check and its
#     reconciles() reader, B6 (2)); and BR-E, one line of `_finish` of the order step: every summary, returned and
#     journaled, now ends with `held_lock` (a bool: whether the run held the run lock). It is the one difference in what
#     a run writes or returns that the comparison finds, and it is admitted by M4 (above), as that key and nothing else;
#   - venues/webull.py (build B, B2 and B3): the owner's consent key `fractional_live_consent` is judged by the
#     confirmation's reader (an optional key; the judged dict holds it only when JSON true), and the production adapter
#     takes an order with no limit price in ONE case (a remainder leg of a run that passed its locks: flag, consent re-read,
#     size under one, a stock or fund and not crypto, CORE, a ".r" tag) and judges the payload again at its single place_order call. THIS TEST DOES NOT
#     GUARD THE ADAPTER: its worlds use a stand-in venue, so R2 cannot see the adapter's order path (a mutant that refused
#     every production LIMIT order survived it). The guard of the adapter is tests/venues/test_webull.py: its
#     production-order tests, the seeded property over 400 production worlds, and `test_f2_...`, which pins the payload of
#     whole-share production LIMIT orders byte for byte against a fixture generated by running 75a51f5's own webull.py.
#     BR-W (big run, 2026-11) also changed it, in two places that this test cannot see for the same reason (its stand-in
#     venue is not the adapter): an order the venue reports as EXPIRED is read as the ledger state `expired` (an exact
#     match; it is not a reconcile fault), and `cancel_all_open` takes the client order ids of our own orders and
#     cancels only those (it fails closed on a bad list). tests/venues/test_webull.py and test_webull_event_orders.py pin both;
#   - execution_and_risk/go_live.py (BR-A, big run, 2026-11): the go-live record's reader `paper_record` (the practice
#     journal read by lock 2) no longer counts a run that ended `done` with `faults` as clean (its fills are not
#     reconciled fills, it is not a paper_broker_day; its day is listed as `faulted_runs`), and it shows how many clean
#     fills were whole, remainder or sale (`whole_fills`, `remainder_fills`, `sale_fills`; shown only, no lock reads
#     them). The worlds are still comparable because the change reads the practice journal and writes nothing, and a
#     world whose run this changed would show as a difference in its summary, journal or files, which these worlds,
#     run through the live lock too, do not show: they differ only by M4's `held_lock` (tests/execution_and_risk/
#     test_go_live*.py hold the reader itself);
# Not listed, each judged against 75a51f5: core/alerts.py (no run imports it; if one does and it changed, the test
# fails in words), stake_ladder.py (byte-identical to the base), research_loop/cli.py (the command line is not what
# these worlds drive; they call run_order_step).
MAY_DIFFER = ("valedia_trader/execution_and_risk/order_step.py", "valedia_trader/execution_and_risk/orders.py",
              "valedia_trader/venues/webull.py", "valedia_trader/execution_and_risk/go_live.py")
TESTS = Path(__file__).resolve().parent
KINDS = ("ordinary", "partial", "refusal", "cap", "breach", "stopped", "approved", "expired", "second_run",
         "baseline", "live")
WORLDS_PER_KIND = 8
STAMP = re.compile(rb'"timestamp": "[^"]*"')
FUNDS = ("SPY", "QQQ", "IWM", "TLT", "GLD")


@pytest.fixture(autouse=True)
def guard(monkeypatch):
    """No socket, no real second price source (for either revision), and LIVE_REPO put back after each test."""
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


def _archive(folder, commit=BASE):
    """(folder, None) with `commit`'s whole `valedia_trader` package (and `src/`, the legacy flat modules
    core/legacy.py loads from beside it) taken by `git archive` into `folder`; or (None, why) in words when this
    checkout's history cannot give it. Every test then FAILS with those words (check_pinned); none skips."""
    root = TESTS.parents[1]
    try:
        data = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", commit, "valedia_trader", "src"],
                              capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        why = (getattr(e, "stderr", b"") or b"").decode("utf-8", "replace").strip()[:200]
        return None, (f"T51 R2's base, commit {commit}, cannot be read from this checkout's history "
                      f"({type(e).__name__}: {why}); the comparison cannot be made, so it fails")
    folder.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(folder, filter="data")
    if not (folder / "valedia_trader" / "execution_and_risk" / "order_step.py").is_file():
        return None, f"T51 R2's base, commit {commit}, has no valedia_trader/execution_and_risk/order_step.py"
    return folder, None


def run_side(root, work, worlds, cent=False):
    """Drive `worlds` [(name, kind, seed)] in a fresh interpreter whose only `valedia_trader` is the one under `root`
    (this file and test_order_step from TESTS drive it). Returns its snapshots, the file of every `valedia_trader`
    module it imported, and what its guard saw."""
    work.mkdir(parents=True, exist_ok=True)
    spec, out = work / "spec.json", work / "out.pickle"
    spec.write_text(json.dumps({"root": str(root), "tmp": str(work / "worlds"), "out": str(out), "worlds": worlds,
                                "cent": cent}))
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    code = ("import sys; sys.path[:0] = [%r, %r]; import test_fractional_off_against_base as T; T.side_main(%r)"
            % (str(root), str(TESTS), str(spec)))
    done = subprocess.run([sys.executable, "-B", "-c", code], cwd=work, env=env, capture_output=True, text=True)
    assert done.returncode == 0, ("a side's run failed", str(root), done.stdout[-2000:], done.stderr[-4000:])
    return pickle.loads(out.read_bytes())


def side_main(spec_path):
    """In the side's own interpreter: guards (no socket, no real second price source), the worlds, and the files of
    every `valedia_trader` module imported by then."""
    spec = json.loads(Path(spec_path).read_text())
    seen = []

    def no_socket(*a, **k):
        seen.append("socket")
        raise OSError("the T51 A4 tests open no socket")
    for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "create_connection"),
                        (socket, "getaddrinfo")):
        setattr(owner, name, no_socket)

    def tripwire(*a, **k):
        seen.append("second source")
        raise AssertionError("the real second price source would be loaded or asked")
    second_quote.second_opinion = tripwire
    if hasattr(osx, "_load_second_source"):
        osx._load_second_source = tripwire
    if hasattr(osx, "book_settled_units"):                               # M6 (CF-C): every lot of the book settled
        real_settled = osx.book_settled_units
        osx.book_settled_units = lambda fills, orders, entries, calendar, day: real_settled(fills, orders, entries,
                                                                                            calendar, "9999-12-31")
    if spec["cent"]:                                                     # the sanity pair: one input off by a cent
        real = osx.limit_price
        osx.limit_price = lambda *a, **k: round(real(*a, **k) - 0.01, 2)
    runs = {name: world_runs(osx, Path(spec["tmp"]) / name, seed, kind) for name, kind, seed in spec["worlds"]}
    modules = {n: getattr(m, "__file__", None) for n, m in list(sys.modules.items())
               if n == "valedia_trader" or n.startswith("valedia_trader.")}
    Path(spec["out"]).write_bytes(pickle.dumps({"runs": runs, "modules": modules, "seen": seen}))


def _worlds():
    return [(f"{kind}-{k}", kind, 5_140_000 + KINDS.index(kind) * 100 + k) for kind in KINDS for k in range(WORLDS_PER_KIND)]


@pytest.fixture(scope="module")
def sides(tmp_path_factory):
    """Both sides, every world, each in its own interpreter: the base's from its archive, this tree's (the package
    this test process imports, so a mutant copy when one is run) from its own folder."""
    folder = tmp_path_factory.mktemp("r2")
    old_root, why = _archive(folder / "base")
    if why is not None:
        return {"error": why}
    new_root = Path(osx.__file__).resolve().parents[2]
    return {"old_root": old_root, "new_root": new_root,
            "old": run_side(old_root, folder / "old", _worlds()),
            "new": run_side(new_root, folder / "new", _worlds()),
            "cent": run_side(new_root, folder / "cent", [("ordinary-0", "ordinary", 5_140_000)], cent=True)}


def check_pinned(sides):
    """Rule 1: no module crossed over. Rule 2: every `valedia_trader` module either side imported is byte-identical
    between the base and this tree, or is one of MAY_DIFFER. Fails in words naming every module that breaks either
    (and, first, when the base could not be read)."""
    if "error" in sides:
        pytest.fail(sides["error"])
    crossed, changed = [], []
    for side, root in (("old", sides["old_root"]), ("new", sides["new_root"])):
        for name, f in sides[side]["modules"].items():
            if f is None or not Path(f).resolve().is_relative_to(root.resolve()):
                crossed.append(f"{side}: {name} from {f}")
    rels = set()
    for side, root in (("old", sides["old_root"]), ("new", sides["new_root"])):
        for f in sides[side]["modules"].values():
            if f is not None and Path(f).resolve().is_relative_to(root.resolve()):
                rels.add(str(Path(f).resolve().relative_to(root.resolve())))
    for rel in sorted(rels):
        if rel in MAY_DIFFER:
            continue
        a, b = sides["old_root"] / rel, sides["new_root"] / rel
        if not (a.is_file() and b.is_file() and a.read_bytes() == b.read_bytes()):
            changed.append(rel)
    for side in ("old", "new", "cent"):
        if sides[side]["seen"]:
            crossed.append(f"{side}: its guard saw {sides[side]['seen']}")
    if crossed or changed:
        pytest.fail("T51 R2 cannot compare with its base: "
                    + (f"modules imported from outside their side: {crossed}; " if crossed else "")
                    + (f"modules the runs import that changed since {BASE} (re-pin the base on purpose): {changed}"
                       if changed else ""))
    return sorted(rels)


def test_each_side_imports_only_its_own_package_and_every_shared_module_is_unchanged_since_the_base(sides, capsys):
    rels = check_pinned(sides)
    with capsys.disabled():
        for side in ("old", "new"):
            mods = sides[side]["modules"]
            print(f"\nT51-A4-IMPORT {side} side, root {sides[side + '_root']}: {len(mods)} valedia_trader modules")
            for name in sorted(mods):
                print(f"T51-A4-IMPORT   {side} {name}: {mods[name]}")
        print(f"T51-A4-IMPORT modules compared byte for byte: {len([r for r in rels if r not in MAY_DIFFER])}; "
              f"allowed to differ and imported: {[r for r in rels if r in MAY_DIFFER]}")
    old_step = sides["old"]["modules"]["valedia_trader.execution_and_risk.order_step"]
    assert b"def split_difference" in Path(old_step).read_bytes()      # the base is the fractional-capable order path
    assert b"def split_difference" in Path(sides["new"]["modules"]["valedia_trader.execution_and_risk.order_step"]).read_bytes()


def test_a_base_this_checkout_cannot_read_fails_every_test_in_words(tmp_path):
    folder, why = _archive(tmp_path / "x", commit="0000000000000000000000000000000000000000")
    assert folder is None and "cannot be read from this checkout's history" in why, why
    with pytest.raises(pytest.fail.Exception, match="cannot be read from this checkout's history"):
        check_pinned({"error": why})


def test_the_pin_fails_in_words_when_an_imported_module_changed_or_crossed_over(sides, tmp_path):
    """The check above is a check: a shared module that differs, or a module from the other side, fails it."""
    check_pinned(sides)
    fake = dict(sides, old=dict(sides["old"], modules=dict(sides["old"]["modules"])))
    other = tmp_path / "valedia_trader" / "venues" / "webull.py"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"# not the base's\n")
    fake["old_root"] = tmp_path
    with pytest.raises(pytest.fail.Exception, match=r"modules imported from outside their side"):
        check_pinned(fake)
    fake = dict(sides)
    root = tmp_path / "copy"
    for rel in sorted({str(Path(f).resolve().relative_to(sides["old_root"].resolve()))
                       for f in sides["old"]["modules"].values()}):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes((sides["old_root"] / rel).read_bytes())
    (root / "valedia_trader/venues/__init__.py").write_bytes(b"# changed after the base\n")
    fake["old_root"] = root
    fake["old"] = dict(sides["old"], modules={n: str(root / Path(f).resolve().relative_to(sides["old_root"].resolve()))
                                              for n, f in sides["old"]["modules"].items()})
    with pytest.raises(pytest.fail.Exception, match=rf"changed since {BASE} .*valedia_trader/venues/__init__.py"):
        check_pinned(fake)


# ---- the stand-ins

def _recording(base_cls):
    class Recording(base_cls):
        """A stand-in that keeps every argument of every submit, and fills a share of tags through their limit by a
        cent (by a hash of the tag, so both revisions' worlds behave alike)."""
        through_pct = 0

        def submit(self, *a, **kw):
            self.__dict__.setdefault("handed", []).append(json.dumps([list(a), sorted(kw.items())], default=repr))
            return super().submit(*a, **kw)

        def order_status(self, tag):
            out = super().order_status(tag)
            if out is not None and out["avg_price"] is not None and zlib.crc32(tag.encode()) % 100 < self.through_pct:
                o = self.book[tag]
                out["avg_price"] = round(o["limit"] + (0.01 if o["units"] > 0 else -0.01), 2)
            return out
    return Recording


R2Venue, R2Live = _recording(Booked), _recording(LiveVenue)


def _random_paper(rng, equity=100_000.0, funds=FUNDS, sizes=(0.0, 0.4, 1.0, 2.0, 2.5, 3.0, 5.0, 7.3, 12.0)):
    wanted = {s: {sym: float(rng.choice(sizes)) for sym in rng.sample(funds, rng.randint(1, len(funds)))}
              for s in rng.sample(["a", "b"], rng.randint(1, 2))}
    return paper(wanted, equity=equity)


def _follow(mod, book):
    names = tuple(sorted(book.wanted)) if book.wanted else ("a",)
    return mod.Follow(ids=tuple(range(1, len(names) + 1)), names=names,
                      digest=hashlib.sha256(repr(names).encode()).hexdigest())


def _snapshot(w, out):
    """Every file of the world after a run, as compared: the journal with M1, every other file's raw bytes (the
    ledger and the state file too: both sides write the same layouts); and the venue's and the second source's
    calls, the summary, the exit status the command line gives it (cli.py: 0 when done, else 2)."""
    files = {}
    for p in sorted(w.tmp.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(w.tmp))
        if p.parent == w.tmp / "journal":
            files[rel] = ("journal", STAMP.sub(b'"timestamp": "T"', p.read_bytes()))
        else:
            files[rel] = ("bytes", p.read_bytes())
    info = {"fates": _column(osx.ticket_path_for(w.state_path), "SELECT fate FROM fates"),
            "states": sorted(set(_column(w.ledger_path, "SELECT state FROM orders")))}
    return {"files": files, "calls": list(w.venue.calls), "handed": list(getattr(w.venue, "handed", [])),
            "second": json.dumps(w.second.calls, default=repr), "summary": json.dumps(out, sort_keys=True, default=str),
            "exit": 0 if isinstance(out, dict) and out.get("status") == "done" else 2, "info": info}


def _column(path, sql):
    """One column of a file's table, read-only (for counting what a world showed; never compared)."""
    if not Path(path).exists():
        return []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[0] for r in con.execute(sql)]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def world_runs(mod, tmp, seed, kind):
    """One seeded world of `kind` driven through `mod` (the base's order step or this revision's), `fractional` off.
    Returns one snapshot per run. The draws depend only on the seed and on what the runs return."""
    rng = random.Random(seed)
    urandom, numbers = random.Random(seed + 1), random.Random(seed + 2)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("os.urandom", lambda n: bytes(urandom.getrandbits(8) for _ in range(n)))
        mp.setattr("secrets.randbelow", lambda n: numbers.randrange(n))
        return _drive(mod, tmp, rng, kind)


def _drive(mod, tmp, rng, kind):
    live = kind == "live"
    held = {}
    if kind == "baseline" or rng.random() < 0.15:
        held = {"OWN": float(rng.randint(1, 5)), "SPY": float(rng.choice([1, 3])), "TLT": float(rng.choice([0, 2]))}
    held = {s: u for s, u in held.items() if u}
    if live:
        w = live_world(tmp, R2Live(held=held, cash=rng.choice([20_000.0, 1_000_000.0])))
    else:
        w = ticket_world(tmp, R2Venue(held=held, cash=rng.choice([5_000.0, 50_000.0, 1e7])))
        cap = rng.choice([1, 2]) if kind == "cap" else rng.choice([3, 40, 40])
        w.limits.write_text(json.dumps({**GENEROUS, "max_orders_per_day": cap}))
    v = w.venue
    v.fill_at_once = rng.choice([0.5, 0.25]) if kind == "partial" else rng.choice([0.0, 1.0, 1.0])
    v.through_pct = 60 if kind == "breach" else rng.choice([0, 0, 0, 10])
    capital = 5000.0 if live else rng.choice([None, None, 250_000.0])
    snaps = []

    def run(**kw):
        if live:
            kw.update(live=w.live, second_opinion=w.second, follow=_follow(mod, w.paper), capital=capital)
        elif capital is not None:
            kw["capital"] = capital
        v.calls.clear()
        out = _run_with(mod, w, **kw)
        snaps.append(_snapshot(w, out))
        return out

    days = 1 if live else rng.choice([1, 2])
    for day in range(days):
        w.paper = _random_paper(rng, equity=5000.0, funds=("IWM", "TLT", "QQQ"), sizes=(0.0, 1.0, 2.0, 3.0, 4.5)) \
            if live else _random_paper(rng)
        for sym in FUNDS:
            w.prices[sym] = rng.choice([20.0, 55.5, 101.25, 7.77, 250.0]) if not live else rng.choice([20.0, 55.5, 101.25])
        if kind == "refusal":
            v.submit_mode = [rng.choice(["accept", "refuse", "reject"]) for _ in range(8)]
        elif not live:
            v.submit_mode = [rng.choice(["accept"] * 9 + ["refuse"]) for _ in range(8)]
        if kind == "stopped" and day == days - 1:
            how = rng.choice(["positions down", "unknown answer", "lost answer", "hand trade", "halt"])
            if how == "positions down":
                v.raise_on = {"positions"}
            elif how in ("unknown answer", "lost answer"):
                v.submit_mode = ["accept", "raise" if how == "unknown answer" else "lose"] + ["accept"] * 6
            elif how == "hand trade":
                run()
                w.later(rng.choice([1, 20]))
                v.held["QQQ"] = v.held.get("QQQ", 0.0) + 1.0              # the owner buys one by hand
            else:
                w.halt = w.tmp / "halt.json"
                w.halt.write_text(json.dumps({"reason": "test"}))
        if kind in ("approved", "expired", "live"):
            p = run(propose=True)
            number = p.get("ticket_number") or "000000"
            w.later(rng.choice([16, 25]) if kind == "expired" else rng.choice([1, 3, 10]))
            if rng.random() < 0.3:
                w.prices["IWM"] = round(w.prices["IWM"] * rng.choice([1.001, 1.02]), 2)   # moved since the ticket
            run(approve=number)
            if rng.random() < 0.5:
                w.later(rng.choice([1, 5]))
                run(approve=number)                                        # a used (or expired) ticket again
        else:
            run()
        if kind == "second_run" or (kind == "partial" and rng.random() < 0.7) or rng.random() < 0.25:
            for tag, o in list(v.book.items()):
                if o["state"] in ("open", "partly_filled") and rng.random() < 0.6:
                    v.fill(tag, abs(o["units"]) * rng.choice([0.25, 0.5]))
            w.later(rng.choice([1, 30]))
            if kind == "second_run" and rng.random() < 0.6:
                w.paper = _random_paper(rng)
            run()
        v.raise_on = set()
        if w.halt is not None and w.halt.exists():
            w.halt.unlink()
        w.next_day()
    if not live:
        run()                                                              # the next day reads the last fills
    return snaps


def _summary_same_but_held_lock(b, m):
    """M4: the run summary of the base (`b`) and of this tree (`m`), both `json.dumps(out, sort_keys=True)`: equal but
    for the key BR-E gave every summary of this tree, `held_lock`. It must be there on this tree and a real bool, and
    absent on the base; without it the two are the same text. No other key is admitted, in either direction."""
    try:
        bs, ms = json.loads(b), json.loads(m)
    except ValueError:
        return b == m
    if not (isinstance(bs, dict) and isinstance(ms, dict)):
        return b == m
    if "held_lock" in bs or type(ms.pop("held_lock", None)) is not bool:
        return False
    if any(k in bs for k in M6_KEYS) or _m6_strip(ms) is None:
        return False
    return json.dumps(ms, sort_keys=True, default=str) == b


def _journal_same_but_held_lock(b, m):
    """M4: a journal file (bytes, M1 applied) of the base (`b`) and of this tree (`m`): the same lines, but a line that
    differs must be a summary line of this tree that ends with `held_lock` (a real bool), and be the base's line once
    that key is taken out. Any other difference (another key, another event, another line count) is not admitted."""
    if b == m:
        return True
    bl, ml = b.split(b"\n"), m.split(b"\n")
    if len(bl) != len(ml):
        return False
    for x, y in zip(bl, ml):
        if x == y:
            continue
        try:
            e = json.loads(y)
        except ValueError:
            return False
        if isinstance(e, dict) and e.get("event") == "order" and list(e)[-2:] == ["quote_ts", "quote_age_s"]:
            if not (_m5_fields_ok(e) and json.dumps({k: v for k, v in e.items() if k not in M5_KEYS},
                                                     default=str).encode() == x):
                return False
            continue
        if not (isinstance(e, dict) and e.get("event") == "summary" and list(e)[-1] == "held_lock"
                and type(e["held_lock"]) is bool):
            return False
        e = {k: v for k, v in e.items() if k != "held_lock"}
        if list(e)[-len(M6_KEYS):] == list(M6_KEYS) and _m6_strip(e) is None:
            return False
        if json.dumps(e, default=str).encode() != x:
            return False
    return True


M6_KEYS = CF_C_KEYS                                  # M6: the keys, and their check, are test_order_step's
_m6_strip = cf_c_strip


M5_KEYS = ("quote_ts", "quote_age_s")


def _m5_fields_ok(e):
    """M5: an order line's `quote_ts` is ISO 8601 text in UTC and its `quote_age_s` a real number in its range."""
    ts, age = e["quote_ts"], e["quote_age_s"]
    try:
        when = datetime.fromisoformat(ts) if type(ts) is str else None
    except ValueError:
        when = None
    return (when is not None and when.utcoffset() == timedelta(0) and type(age) in (int, float) and math.isfinite(age)
            and -osx.QUOTE_MAX_AHEAD_SECONDS <= age <= osx.PRIMARY_QUOTE_MAX_AGE_SECONDS)


def differences(b_runs, m_runs):
    """Every difference between the base's runs and this revision's, as (run, artefact) pairs, with M1 and M4."""
    diffs = []
    if len(b_runs) != len(m_runs):
        return [("runs", f"{len(b_runs)} against {len(m_runs)}")]
    for i, (b, m) in enumerate(zip(b_runs, m_runs)):
        for key in ("calls", "handed", "second", "exit"):
            if b[key] != m[key]:
                diffs.append((i, key))
        if not _summary_same_but_held_lock(b["summary"], m["summary"]):
            diffs.append((i, "summary"))
        if sorted(b["files"]) != sorted(m["files"]):
            diffs.append((i, ("file names", sorted(set(b["files"]) ^ set(m["files"])))))
            continue
        for rel, (how, data) in b["files"].items():
            mhow, mdata = m["files"][rel]
            if how != mhow or not (_journal_same_but_held_lock(data, mdata) if how == "journal" else data == mdata):
                diffs.append((i, rel))
    return diffs


def _seen(snaps, kind):
    """Did the world show what its kind is about? Read from this revision's own artefacts."""
    outs = [json.loads(s["summary"]) for s in snaps]
    sent = sum(o.get("orders_sent", 0) or 0 for o in outs)
    if kind == "partial":
        return any("partly_filled" in s["info"]["states"] for s in snaps)
    if kind == "refusal":
        return any(o.get("refused") for o in outs)
    if kind == "cap":
        return any("cap" in e.get("reason", "") for o in outs for e in o.get("left_over", []))
    if kind == "breach":
        return any("cap" in (o.get("faults") or []) for o in outs)
    if kind == "stopped":
        return any(o.get("status") == "stopped" for o in outs)
    if kind == "approved":
        return any(o.get("mode") == "approve" and o.get("orders_sent") for o in outs)
    if kind == "expired":
        return any("expired" in s["info"]["fates"] for s in snaps)
    if kind == "second_run":
        return sum(1 for o in outs if o.get("status") == "done") >= 2 and sent > 0
    if kind == "baseline":
        return any(o.get("status") == "done" for o in outs)
    if kind == "live":
        return any(o.get("mode") == "approve" and o.get("orders_sent") and o.get("venue") == "webull-production"
                   for o in outs)
    return sent > 0


@pytest.mark.parametrize("kind", KINDS)
def test_r2_a_run_with_fractional_off_writes_exactly_what_the_accepted_order_path_writes(sides, kind):
    check_pinned(sides)
    shown, runs, submits, faults = 0, 0, 0, 0
    for k in range(WORLDS_PER_KIND):
        name, seed = f"{kind}-{k}", 5_140_000 + KINDS.index(kind) * 100 + k
        b, m = sides["old"]["runs"][name], sides["new"]["runs"][name]
        assert differences(b, m) == [], (kind, seed, differences(b, m))
        shown += _seen(m, kind)
        runs += len(m)
        submits += sum(1 for s in m for c in s["calls"] if c[0] == "submit")
        faults += sum(1 for s in m if '"faults"' in s["summary"])
    print(f"T51-A4-R2 {kind}: worlds {WORLDS_PER_KIND}, runs {runs}, submits {submits}, runs with faults {faults}, "
          f"worlds showing the kind {shown}, differences 0")
    assert shown >= 2 and runs >= WORLDS_PER_KIND * 2, (kind, shown, runs)
    if kind not in ("expired", "stopped"):
        assert submits > 0


def test_r2_sanity_the_comparison_finds_two_different_worlds_and_a_one_cent_change_different(sides):
    check_pinned(sides)
    b = sides["old"]["runs"]["ordinary-0"]
    assert differences(b, sides["new"]["runs"]["ordinary-1"]) != []   # two different worlds
    assert differences(b, sides["new"]["runs"]["ordinary-0"]) == []   # the same world: no difference
    found = differences(b, sides["cent"]["runs"]["ordinary-0"])        # every limit a cent lower, nothing else
    assert found and {k for _, k in found} >= {"handed", "summary"}, found
    assert any(isinstance(k, str) and k.startswith("journal/") for _, k in found), found


# ---- M4: the one admitted difference, `held_lock`, and nothing beside it

def _grown(snap, **change):
    """A copy of a snapshot of this tree's run whose summary has `change` applied (a value of _GONE takes the key out)."""
    out = json.loads(snap["summary"])
    for k, v in change.items():
        if v is _GONE:
            out.pop(k, None)
        else:
            out[k] = v
    return dict(snap, summary=json.dumps(out, sort_keys=True, default=str))


_GONE = object()


def _summary_line(snap):
    """(journal file, line index, parsed line) of the last summary line of the order step in a snapshot's journal."""
    found = [(rel, n, json.loads(line)) for rel, (how, data) in sorted(snap["files"].items()) if how == "journal"
             for n, line in enumerate(data.split(b"\n")) if line.strip() and json.loads(line).get("event") == "summary"
             and "held_lock" in json.loads(line)]
    assert found, "the run journaled no summary with held_lock"
    return found[-1]


def _put_line(snap, rel, n, line):
    how, data = snap["files"][rel]
    lines = data.split(b"\n")
    lines[n] = line
    return dict(snap, files={**snap["files"], rel: (how, b"\n".join(lines))})


def _first_done(sides):
    for name in sorted(sides["new"]["runs"]):
        for b, m in zip(sides["old"]["runs"][name], sides["new"]["runs"][name]):
            if json.loads(m["summary"]).get("orders_sent") and any(
                    how == "journal" and b'"held_lock"' in data for how, data in m["files"].values()):
                return b, m
    pytest.fail("no run of the worlds sent an order and journaled its summary")


def test_r2_m4_held_lock_is_the_whole_of_the_difference_and_both_sides_are_compared_with_it_taken_out(sides):
    check_pinned(sides)
    runs = held = lines = 0
    for name, base_runs in sides["old"]["runs"].items():
        for b, m in zip(base_runs, sides["new"]["runs"][name]):
            ms, bs = json.loads(m["summary"]), json.loads(b["summary"])
            assert "held_lock" not in bs, name
            assert type(ms.get("held_lock")) is bool, (name, ms.get("held_lock"))
            assert b["summary"] != m["summary"]                          # without M4 it would be a difference
            rel, n, e = _summary_line(m)
            assert list(e)[-1] == "held_lock" and type(e["held_lock"]) is bool
            runs += 1
            held += ms["held_lock"] is True
            lines += sum(1 for _, (how, data) in m["files"].items() if how == "journal" and b'"held_lock"' in data)
    assert runs > 100 and held > 0 and runs - held > 0 and lines > 0, (runs, held, lines)   # a run that never took the lock too


def test_r2_m4_a_summary_that_differs_in_any_other_key_or_has_no_real_bool_held_lock_is_a_difference(sides):
    b, m = _first_done(sides)
    assert differences([b], [m]) == []
    got = json.loads(m["summary"])
    for held in (True, False):                                           # the key itself: either bool is admitted
        assert differences([b], [_grown(m, held_lock=held)]) == []
    for bad in (_grown(m, orders_sent=got["orders_sent"] + 1),            # another key's value differs
                _grown(m, extra_key=1),                                  # another key beside held_lock
                _grown(m, status=_GONE),                                 # another key gone
                _grown(m, held_lock=True, notices=["x"])):               # held_lock is no licence for a second key
        assert (0, "summary") in differences([b], [bad]), bad["summary"]
    for held in (_GONE, None, 1, 0, "true", [True], {"held": True}):      # missing, or not a real bool
        assert (0, "summary") in differences([b], [_grown(m, held_lock=held)]), held
    assert (0, "summary") in differences([_grown(b, held_lock=True)], [m])   # the base never says it


def test_r2_m4_a_journal_line_that_differs_in_any_other_way_than_held_lock_is_a_difference(sides):
    b, m = _first_done(sides)
    rel, n, e = _summary_line(m)
    line = lambda d: json.dumps(d, default=str).encode()
    other = {k: v for k, v in e.items() if k != "held_lock"}
    assert differences([b], [m]) == []
    for held in (True, False):                                           # either bool, as the line says it
        assert differences([b], [_put_line(m, rel, n, line({**other, "held_lock": held}))]) == [], held
    for bad in ({**other, "held_lock": None}, {**other, "held_lock": 1}, {**other, "held_lock": "true"},
                {"held_lock": e["held_lock"], **other},                  # held_lock not the last key
                {**other, "orders_sent": -1, "held_lock": e["held_lock"]},     # another key's value differs
                {**other, "extra_key": 1, "held_lock": e["held_lock"]},        # another key beside it
                {**e, "event": "order"}):                                # held_lock on a line that is not a summary
        assert (0, rel) in differences([b], [_put_line(m, rel, n, line(bad))]), bad
    assert (0, rel) in differences([b], [_put_line(m, rel, n, b"not json")])
    how, data = m["files"][rel]
    assert (0, rel) in differences([b], [dict(m, files={**m["files"], rel: (how, data + b"\n" + line(e))})])   # a line more


def test_r2_m4_held_lock_on_a_line_of_another_event_and_every_other_part_of_the_run_are_still_differences(sides):
    b, m = _first_done(sides)
    for rel, (how, data) in sorted(b["files"].items()):                   # a base line of another event, grown at its end
        if how != "journal":
            continue
        for n, text in enumerate(data.split(b"\n")):
            if text.strip() and json.loads(text).get("event") != "summary":
                grown = json.dumps({**json.loads(text), "held_lock": True}, default=str).encode()
                assert (0, rel) in differences([b], [_put_line(m, rel, n, grown)]), (rel, n)
                break
        else:
            continue
        break
    else:
        pytest.fail("the run has no journal line of another event to try")
    for key, value in (("calls", [("changed",)]), ("handed", ["changed"]), ("second", "changed"), ("exit", 9)):
        assert (0, key) in differences([b], [dict(m, **{key: value})]), key
    other = next(r for r, (how, _) in sorted(m["files"].items()) if how == "bytes")     # a file that is not a journal
    assert (0, other) in differences([b], [dict(m, files={**m["files"], other: ("bytes", m["files"][other][1] + b"x")})])
    assert differences([b], [dict(m, files={r: v for r, v in m["files"].items() if r != other})])[0][1][0] == "file names"


# ---- M5 (CF-Q2): the order line's quote time and age, and nothing beside them

def _order_line(snap):
    for rel, (how, data) in sorted(snap["files"].items()):
        if how == "journal":
            for n, text in enumerate(data.split(b"\n")):
                if text.strip() and json.loads(text).get("event") == "order":
                    return rel, n, json.loads(text)
    pytest.fail("the run journaled no order line")


def test_r2_m5_the_quote_time_and_age_end_every_order_line_and_nothing_else_is_admitted(sides):
    b, m = _first_done(sides)
    rel, n, e = _order_line(m)
    assert list(e)[-2:] == list(M5_KEYS) and differences([b], [m]) == []
    line = lambda d: json.dumps(d, default=str).encode()
    other = {k: v for k, v in e.items() if k not in M5_KEYS}
    for good in (0.0, 1.5, osx.PRIMARY_QUOTE_MAX_AGE_SECONDS, -osx.QUOTE_MAX_AHEAD_SECONDS):
        assert differences([b], [_put_line(m, rel, n, line({**other, "quote_ts": e["quote_ts"], "quote_age_s": good}))]) == []
    ts = e["quote_ts"]
    for bad in ({**other, "quote_ts": ts, "quote_age_s": osx.PRIMARY_QUOTE_MAX_AGE_SECONDS + 0.001},
                {**other, "quote_ts": ts, "quote_age_s": True}, {**other, "quote_ts": ts, "quote_age_s": "1"},
                {**other, "quote_ts": ts, "quote_age_s": float("nan")},
                {**other, "quote_ts": ts.replace("+00:00", "-05:00"), "quote_age_s": e["quote_age_s"]},
                {**other, "quote_ts": "not a time", "quote_age_s": e["quote_age_s"]},
                {**other, "quote_ts": 1.0, "quote_age_s": e["quote_age_s"]},
                {"quote_ts": ts, "quote_age_s": e["quote_age_s"], **other},           # not at the line's end
                {**other, "quote_age_s": e["quote_age_s"], "quote_ts": ts},           # the two the other way round
                {**other, "units": -1, "quote_ts": ts, "quote_age_s": e["quote_age_s"]},   # another key differs
                {**other, "extra": 1, "quote_ts": ts, "quote_age_s": e["quote_age_s"]},    # a key beside them
                {**other, "event": "fill", "quote_ts": ts, "quote_age_s": e["quote_age_s"]}):  # not an order line
        assert (0, rel) in differences([b], [_put_line(m, rel, n, line(bad))]), bad
