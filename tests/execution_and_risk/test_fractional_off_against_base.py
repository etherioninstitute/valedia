"""T51 A4, R2 with addendum 2's ruling A: with `fractional` OFF nothing changes.

PROPERTY: for every input, a run with `fractional` off writes the same orders, ledger rows, journal lines, ticket
file, tags, faults, summary and exit status as the accepted order path at commit 75a51f5 (the practice gate's commit,
S4e merged on main), and hands the venue and the second price source exactly the same calls. This is the guard for
T51 build B: whatever build B changes, a run with fractional off must still write exactly what 75a51f5 writes.

History of the pin: it was 33ace6f (T51's own base) until T51c S4c-2 changed go_live.py and stake_ladder.py on purpose;
it was then re-pinned on purpose to 75a51f5 (T51c re-pin ticket). Today every module either side imports is
byte-identical between the two sides.

How it is shown: seeded worlds of every kind of day (ordinary days, partial fills, refusals by the venue, an order
left over by the day's cap, a fill through its limit, a stopped run, a ticket proposed and approved, a ticket that
expires, a second run the same day, a baseline of holdings that are not the book's, and live mode on a stand-in
production venue, propose and approve, its confirmation files in the test's own folder) are each driven twice from
the same seed: once by a fresh interpreter that imports ONLY 75a51f5's whole `valedia_trader` package (taken with
`git archive 75a51f5` into the test's temporary folder), once by a fresh interpreter that imports ONLY this tree's.
Each side reports the file of every `valedia_trader` module it imported: none may come from the other side, and
every one of them must be byte-identical between the two sides except `execution_and_risk/order_step.py` and
`execution_and_risk/orders.py` (the subject of T51 build B). A later change on main to a module the run
imports fails these tests in words naming it (the base must then be re-pinned on purpose, or the module added to
the list on purpose); a changed module that no run imports does not matter. If 75a51f5 cannot be read from this
checkout's history the tests fail in words. After every run the whole world folder is compared, file by file.

What is masked, and why (nothing else is):
- M1 the journal's `"timestamp": "..."` field: the journal stamps each line with the wall clock
  (paper.Journal.write, datetime.now), which no test controls. Every other byte of every journal file is compared.
  STILL NEEDED against 75a51f5 (the clock is the same on both sides).
- M2 (the order ledger's layout: 33ace6f wrote layout 3, this tree layout 4) and M3 (the order step's state file's
  layout: 33ace6f wrote layout 1, this tree layout 2) are REMOVED. 75a51f5 writes layout 4 and layout 2 as this tree
  does, so the ledger file and the state file are compared byte for byte like every other file; a difference in
  either layout, schema or row now shows as a difference.
- Random ids are NOT masked: the batch ids and the state file's account key (os.urandom) and the ticket numbers
  (secrets.randbelow) are drawn on both sides from the same seeded source, so they must be equal too.
Every other file (ledger, state, ticket file, the live owner's files, the limits file) is compared byte for byte.

A sanity pair shows the comparison can fail: two different worlds, and one world with a single input changed by a
cent, are each reported as different.

Nothing here opens a socket or reaches the real second price source (the autouse guard fails the test)."""
import hashlib
import io
import json
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
from pathlib import Path

import pytest

from valedia_trader.data_and_fees import second_quote
from valedia_trader.execution_and_risk import order_step as osx
from valedia_trader.execution_and_risk import orders as _orders
from valedia_trader.venues import webull as _wb
from test_order_step import Booked, GENEROUS, LiveVenue, _run_with, live_world, paper, ticket_world

BASE = "75a51f5"
# What may differ between the base and this tree: the two modules T51 build B changes on purpose (the order step and
# its ledger, the subject of the comparison), and nothing else. Not listed, each judged against 75a51f5: core/alerts.py
# (no run imports it; if one does and it changed, the test fails in words), venues/webull.py (R33a, R39; byte-identical
# to the base, the old reason is gone), go_live.py and stake_ladder.py (S4c-2; byte-identical to the base).
MAY_DIFFER = ("valedia_trader/execution_and_risk/order_step.py", "valedia_trader/execution_and_risk/orders.py")
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


def differences(b_runs, m_runs):
    """Every difference between the base's runs and this revision's, as (run, artefact) pairs, with M1."""
    diffs = []
    if len(b_runs) != len(m_runs):
        return [("runs", f"{len(b_runs)} against {len(m_runs)}")]
    for i, (b, m) in enumerate(zip(b_runs, m_runs)):
        for key in ("calls", "handed", "second", "summary", "exit"):
            if b[key] != m[key]:
                diffs.append((i, key))
        if sorted(b["files"]) != sorted(m["files"]):
            diffs.append((i, ("file names", sorted(set(b["files"]) ^ set(m["files"])))))
            continue
        for rel, (how, data) in b["files"].items():
            if (how, data) != m["files"][rel]:
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
