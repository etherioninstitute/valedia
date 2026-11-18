"""T51 A4, R2 with addendum 2's ruling A: with `fractional` OFF nothing changes.

PROPERTY: for every input, a run with `fractional` off writes the same orders, ledger rows, journal lines, ticket
file, tags, faults, summary and exit status as the order step at commit 33ace6f (T51's base), and hands the venue
and the second price source exactly the same calls.

How it is shown: seeded worlds of every kind of day (ordinary days, partial fills, refusals by the venue, an order
left over by the day's cap, a fill through its limit, a stopped run, a ticket proposed and approved, a ticket that
expires, a second run the same day, a baseline of holdings that are not the book's, and live mode on a stand-in
production venue, propose and approve, its confirmation files in the test's own folder) are each driven twice from
the same seed: once through 33ace6f's order step and order ledger (taken with `git archive 33ace6f` into the test's
temporary folder, loaded under their own names), once through this revision. After every run the whole world
folder is compared, file by file.

What is masked, and why (nothing else is):
- M1 the journal's `"timestamp": "..."` field: the journal stamps each line with the wall clock
  (paper.Journal.write, datetime.now), which no test controls. Every other byte of every journal file is compared.
- M2 the order ledger's layout: 33ace6f writes layout 3 and this revision layout 4 (R7). The ledger is compared as
  a dump (layout number, schema text, every row of every table): this revision's dump is first checked to BE
  layout 4 with every row's `order_type` LIMIT and `collar_price` NULL (ruling A) and only then seen through the
  layout-3 columns (`_t51_layout3_view`), which must equal 33ace6f's dump exactly. The ledger file's raw bytes are
  not compared (its page layout follows its schema).
- Random ids are NOT masked: the batch ids and the state file's account key (os.urandom) and the ticket numbers
  (secrets.randbelow) are drawn on both sides from the same seeded source, so they must be equal too.
Every other file (state file, ticket file, the live owner's files, the limits file) is compared byte for byte.

A sanity pair shows the comparison can fail: two different worlds, and one world with a single input changed by a
cent, are each reported as different.

Nothing here opens a socket or reaches the real second price source (the autouse guard fails the test)."""
import hashlib
import io
import json
import random
import re
import socket
import sqlite3
import subprocess
import sys
import tarfile
import zlib
import importlib.util
from pathlib import Path

import pytest

from valedia_trader.data_and_fees import second_quote
from valedia_trader.execution_and_risk import order_step as osx
from valedia_trader.execution_and_risk import orders as _orders
from valedia_trader.venues import webull as _wb
from test_order_step import Booked, GENEROUS, LiveVenue, _dump, _run_with, _t51_layout3_view, live_world, paper, \
    ticket_world

BASE = "33ace6f"
OLD_ORDERS, OLD_STEP = "orders_t51_base_33ace6f", "order_step_t51_base_33ace6f"
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


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """33ace6f's order step and order ledger, from `git archive 33ace6f`, extracted into this test's temporary folder
    and loaded under their own names (the base order step imports the base ledger). Every other module they import
    is unchanged since 33ace6f (checked)."""
    root = Path(__file__).resolve().parents[2]
    step_rel, orders_rel = "valedia_trader/execution_and_risk/order_step.py", "valedia_trader/execution_and_risk/orders.py"
    changed = subprocess.run(["git", "-C", str(root), "diff", "--name-only", BASE, "HEAD", "--", "valedia_trader"],
                             capture_output=True, check=True, text=True).stdout.split()
    assert set(changed) <= {step_rel, orders_rel, "valedia_trader/core/alerts.py"}, changed   # alerts: not imported
    data = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", BASE, step_rel, orders_rel],
                          capture_output=True, check=True).stdout
    folder = tmp_path_factory.mktemp("base_33ace6f")
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(folder, filter="data")
    mods = {}
    for name, rel, fix in ((OLD_ORDERS, orders_rel, None),
                           (OLD_STEP, step_rel, lambda s: s.replace(
                               b"from valedia_trader.execution_and_risk.orders import", f"from {OLD_ORDERS} import".encode()))):
        src = (folder / rel).read_bytes()
        if fix:
            assert src.count(b"from valedia_trader.execution_and_risk.orders import") == 1
            src = fix(src)
        path = folder / f"{name}.py"
        path.write_bytes(src)
        spec = importlib.util.spec_from_file_location(name, path)
        mods[name] = importlib.util.module_from_spec(spec)
        sys.modules[name] = mods[name]
        spec.loader.exec_module(mods[name])
    step = mods[OLD_STEP]
    assert step.OrderLedger is mods[OLD_ORDERS].OrderLedger and mods[OLD_ORDERS].LAYOUT_VERSION == 3
    assert not hasattr(step, "split_difference") and hasattr(osx, "split_difference")
    return step


def test_the_base_is_imported_from_its_archive_copy_and_this_revision_from_its_own_tree(base, capsys):
    root = Path(__file__).resolve().parents[2]
    old = [Path(sys.modules[OLD_ORDERS].__file__), Path(base.__file__)]
    with capsys.disabled():
        for label, f in (("this revision's order_step", osx.__file__), ("this revision's orders", _orders.__file__),
                         ("33ace6f order_step", old[1]), ("33ace6f orders", old[0])):
            print(f"\nT51-A4-IMPORT {label}: {f}")
    assert all(p.parent.name.startswith("base_33ace6f") for p in old), old
    assert not any(str(p).startswith(str(root / "valedia_trader")) for p in old)
    assert sys.modules[base.OrderLedger.__module__].__file__ == str(old[0])
    assert osx.OrderLedger is _orders.OrderLedger and Path(osx.__file__).name == "order_step.py"


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
    """Every file of the world after a run, as compared: the journal with M1, the ledger as a dump (M2 applied by the
    comparison), every other file's raw bytes; and the venue's and the second source's calls, the summary, the
    exit status the command line gives it (cli.py: 0 when done, else 2)."""
    files = {}
    for p in sorted(w.tmp.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(w.tmp))
        if p == w.ledger_path:
            files[rel] = ("ledger", _dump(p))
        elif p.parent == w.tmp / "journal":
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
    """One seeded world of `kind` driven through `mod` (33ace6f's order step or this revision's), `fractional` off.
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
    """Every difference between 33ace6f's runs and this revision's, as (run, artefact) pairs, with M1 and M2."""
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
            mhow, mdata = m["files"][rel]
            if how == "ledger":
                assert data is None or data["version"] == 3, data["version"]
                if mdata is None or mdata["version"] != 4:
                    diffs.append((i, (rel, "layout")))
                    continue
                try:
                    mdata = _t51_layout3_view(mdata)                     # asserts every row LIMIT, collar NULL
                except AssertionError as e:
                    diffs.append((i, (rel, f"ruling A: {e}"[:200])))
                    continue
            if (how, data) != (mhow, mdata):
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
def test_r2_a_run_with_fractional_off_writes_exactly_what_33ace6f_writes(tmp_path, base, kind):
    shown, runs, submits, faults = 0, 0, 0, 0
    for k in range(WORLDS_PER_KIND):
        seed = 5_140_000 + KINDS.index(kind) * 100 + k
        b = world_runs(base, tmp_path / f"base{k}", seed, kind)
        m = world_runs(osx, tmp_path / f"mine{k}", seed, kind)
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


def test_r2_sanity_the_comparison_finds_two_different_worlds_and_a_one_cent_change_different(tmp_path, base, monkeypatch):
    b = world_runs(base, tmp_path / "b", 5_140_000, "ordinary")
    other = world_runs(osx, tmp_path / "m", 5_140_001, "ordinary")
    assert differences(b, other) != []                                   # two different worlds
    same = world_runs(osx, tmp_path / "same", 5_140_000, "ordinary")
    assert differences(b, same) == []                                     # the same world: no difference
    real = osx.limit_price

    def a_cent_off(*a, **k):                                             # one input to the order changed by a cent
        return round(real(*a, **k) - 0.01, 2)
    monkeypatch.setattr(osx, "limit_price", a_cent_off)
    cent = world_runs(osx, tmp_path / "cent", 5_140_000, "ordinary")
    found = differences(b, cent)
    assert found and {k for _, k in found} >= {"handed", "summary"}, found
    assert any(isinstance(k, str) and k.startswith("journal/") for _, k in found), found
