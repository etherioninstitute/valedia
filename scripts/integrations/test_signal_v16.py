#!/usr/bin/env python3
"""T18b replay: every time field validated (one row = flagged everywhere or counted in
all-time AND 24h), torn sent-record fragments discarded (exact ids only), legacy-cursor
union-seed once with a marker, a failed "unconfirmed twice" notice never marks ids sent,
bad-row counts = ledger LINES, scan counts labelled "(ledger scan)" never "verified".
Fakes only: no network, no keys, no orders.  Run: python3 test_signal_v16.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_signal_v14 import Settle, ent, st, mins, P, AT, POS, _pnl  # noqa: E402
from test_signal_v15 import go, rd, paths, REINIT, TWICE  # noqa: E402
import signal_verify as sv  # noqa: E402
import ledger_view as lv  # noqa: E402
import settle_run as sr  # noqa: E402
import claude_bridge as cb  # noqa: E402

BASE = [ent("a", mins(-3000)), st("a", 3.0, True, mins(-2000))]
SCAN = "(ledger scan)"


def _facts(rows, now=mins(0)):
    f = cb.verified_facts(get=lambda p: {"balance": 100}, get_positions=POS, ledger=lambda: rows, now=now)
    return [l for l in cb.facts_block(f)[0] if l.startswith(AT)]


def _nostale(m):
    return "\n".join(l for l in m.splitlines() if not l.startswith("⚠ STALE"))


def _log():
    return [json.loads(l) for l in open(os.path.join(sv.signal_dir(), "verify_log.jsonl"))]


# ── F1 ───────────────────────────────────────────────────────────────────────
def test_every_time_field_validated_one_precedence():
    # logged_at = now-60m (past), ts = now+2h (future): flagged in EVERY sender
    rows = BASE + [ent("b", mins(-300)), st("b", -9.0, False, mins(-60), ts=mins(120).isoformat())]
    _, t = _pnl(rows)
    assert f"{AT}omitted — 1 ledger row(s) future-dated {SCAN}" in t, t
    assert f"Settled last 24h (ledger): omitted — 1 ledger row(s) future-dated {SCAN}" in t, t
    assert "9.00" not in t and "3.00" not in t, t  # neither figure published
    assert _facts(rows) == [f"{AT}omitted — 1 ledger row(s) future-dated {SCAN}"]
    with Settle() as s:
        s.write(rows)
        out = [_nostale(m) for m in go(s, mins(0))]
    assert out == [f"{P}+$3.00 (1W/0L)\n⚠ 1 ledger row(s) future-dated {SCAN} — all-time total omitted"], out
    # every other time field, future or garbage -> flagged; a clean row is counted in BOTH figures
    cases = {"ts +2h": dict(ts=mins(120).isoformat()), "settled_at +2h": dict(settled_at=mins(120).isoformat()),
             "settlement_ts +2h": dict(settlement_ts=mins(120).isoformat()),
             "ts garbage": dict(ts="yesterday"), "ts number": dict(ts=1790000000),
             "settled_at garbage": dict(settled_at="bad"), "settlement_ts list": dict(settlement_ts=[1])}
    for name, extra in cases.items():
        recs = BASE + [ent("b", mins(-300)), st("b", -9.0, False, mins(-60), **extra)]
        v = lv.select(recs, mins(0))
        assert list(v["valid"]) == ["a"] and len(v["bad"]) == 1, (name, v)
        _, t = _pnl(recs)
        assert "omitted" in t.split("Settled last 24h")[0] and "Settled last 24h (ledger): omitted" in t, (name, t)
    # consistent rows: counted in all-time AND 24h (never one without the other)
    for name, extra in {"plain": {}, "ts same hour": dict(ts=mins(-59).isoformat()),
                        "settled_at": dict(settled_at=mins(-58).isoformat())}.items():
        recs = BASE + [ent("b", mins(-300)), st("b", -9.0, False, mins(-60), **extra)]
        _, t = _pnl(recs)
        assert f"{AT}−$6.00 (net loss) · 1W/1L" in t and "Settled last 24h (ledger): −$9.00" in t, (name, t)
    # documented precedence
    r = st("b", 1.0, True, mins(-60), ts=mins(-30).isoformat(), settled_at=mins(-20).isoformat())
    assert lv.booked(r) == mins(-60) and lv.settled_time(r) == mins(-20)
    r = st("b", 1.0, True, mins(-60), ts=mins(-30).isoformat())
    assert lv.booked(r) == mins(-60) and lv.settled_time(r) == mins(-30)
    assert lv.booked(dict(r, logged_at="")) == mins(-30)  # "" is absent



def test_no_booked_time_is_flagged_everywhere():
    # only settled_at / settlement_ts present (no logged_at, no ts): flagged, never counted or crashing
    for extra in (dict(settled_at=mins(-30).isoformat()), dict(settlement_ts=mins(-30).isoformat())):
        row = {k: v for k, v in st("b", -9.0, False, mins(-30), **extra).items() if k != "logged_at"}
        rows = BASE + [ent("b", mins(-300)), row]
        v = lv.select(rows, mins(0))
        assert list(v["valid"]) == ["a"] and list(v["bad"].values()) == ["unparseable"], v
        _, t = _pnl(rows)
        assert "9.00" not in t and "6.00" not in t, t
        assert _facts(rows) == [f"{AT}omitted — 1 ledger row(s) unparseable {SCAN}"]
        with Settle() as s:
            s.write(rows)
            out = [_nostale(m) for m in go(s, mins(0))]
        assert out and all("9.00" not in m and "settlement check failed" not in m for m in out), out

# ── F2 ───────────────────────────────────────────────────────────────────────
def test_torn_fragment_never_marks_ids_sent():
    for tag, tail in [("quote_only", '"a"\n"'), ("prefix_b", '"a"\n"b')]:
        with Settle() as s:
            s.write([ent("old", mins(-900)), st("old", 1.0, True, mins(-800)),
                     ent("a", mins(-300)), st("a", 3.0, True, mins(-12)),
                     ent("b", mins(-300)), st("b", -1.0, False, mins(-11))])
            for p in (paths(s)["sent"], paths(s)["bak"]):  # power loss tore the append
                open(p, "w").write('"old"\n' + tail)
                os.utime(p, (mins(0).timestamp(), mins(0).timestamp()))
            open(paths(s)["unc"], "w").write('"a"\n"b"\n')  # write-ahead had a and b
            s.append([ent("c", mins(-100)), st("c", 50.0, True, mins(3)),
                      ent("bb", mins(-100)), st("bb", -20.0, False, mins(4))])  # after the report
            out = go(s, mins(60))
            # b (write-ahead, unconfirmed) is re-sent labelled; c and bb in neither record are
            # reported normally, bb's -$20 included; a is a complete id -> sent, not repeated
            assert out == [f"{P}+$29.00 (1W/2L)\n(re-sent: previous delivery unconfirmed) 1 of these "
                           f"settlement(s)\n{AT}+$33.00 (3W/2L)"], (tag, out)
            assert set(rd(paths(s)["sent"])) == {'"old"', '"a"', '"b"', '"c"', '"bb"'}, tag
            assert all(json.loads(l) for l in open(paths(s)["sent"]).read().splitlines()), tag
            assert go(s, mins(120)) == [], tag


def test_fragment_without_write_ahead_is_reported_normally():
    with Settle() as s:
        s.write([ent("a", mins(-300)), st("a", 3.0, True, mins(-12)),
                 ent("bb", mins(-300)), st("bb", -20.0, False, mins(-11))])
        for p in (paths(s)["sent"], paths(s)["bak"]):
            open(p, "w").write('"a"\n"b')  # fragment is a prefix of "bb": must NOT mark it sent
        out = go(s, mins(30))
        assert out == [f"{P}−$20.00 (net loss) (0W/1L)\n{AT}−$17.00 (net loss) (1W/1L)"], out
        assert "bb" in sr.load_sent_ids() and "b" not in sr.load_sent_ids()


# ── F3 ───────────────────────────────────────────────────────────────────────
def test_cursor_seed_once_with_marker():
    with Settle(fresh_record=False) as s:  # R1: deployed v13 + sent-set: cursor + partial sent file
        s.write([ent("a", mins(-3000)), st("a", 10.0, True, mins(-2000)),
                 ent("b", mins(-3000)), st("b", 20.0, True, mins(-1500)),
                 ent("c", mins(-300)), st("c", -1.0, False, mins(-100))])
        os.makedirs(s.sig, exist_ok=True)
        open(paths(s)["sent"], "w").write('"c"\n')
        open(paths(s)["cur"], "w").write(mins(-100).isoformat())
        assert go(s, mins(0)) == []  # a and b were reported by the cursor-only code
        for k in ("sent", "bak"):
            raw = rd(paths(s)[k], raw=True)
            assert sum(1 for l in raw if l.startswith('{"seeded_from_cursor"')) == 1, raw
            assert {'"a"', '"b"', '"c"'} <= set(raw), raw
        # marker present: the cursor is never consulted again — a back-dated settlement
        # (booked before the cursor) is a NEW id and is reported
        s.append([ent("d", mins(-300)), st("d", 7.0, True, mins(-500))])
        assert _nostale(go(s, mins(60))[0]).startswith(f"{P}+$7.00 (1W/0L)")
        assert go(s, mins(120)) == []
    with Settle(fresh_record=False) as s:  # marker survives the repair rewrite of a bad line
        s.write([ent("a", mins(-3000)), st("a", 10.0, True, mins(-2000))])
        open(paths(s)["sent"], "w").write("")
        open(paths(s)["cur"], "w").write(mins(-100).isoformat())
        assert go(s, mins(0)) == []
        open(paths(s)["sent"], "a").write("{garbage\n")
        assert go(s, mins(30)) == []
        assert any(l.startswith('{"seeded_from_cursor"') for l in rd(paths(s)["sent"], raw=True))
        assert "{garbage" not in rd(paths(s)["sent"], raw=True)
    with Settle(fresh_record=False) as s:  # no cursor, first run: unchanged re-init notice
        s.write([ent("w", mins(-300)), st("w", 1.0, True, mins(-200))])
        assert go(s, mins(0)) == [REINIT]
        assert rd(paths(s)["sent"]) == ['"w"'] and go(s, mins(60)) == []


# ── F4 ───────────────────────────────────────────────────────────────────────
def _to_twice(s):
    s.write([ent("a", mins(-300)), st("a", 3.0, True, mins(-12))])
    assert go(s, mins(0), ret=None)[0].startswith(P)  # unknown #1
    assert "re-sent" in go(s, mins(60), ret=None)[0]  # unknown #2


def test_twice_notice_failure_never_marks_sent():
    with Settle() as s:  # confirmed failure -> not recorded, retried on the very next run
        _to_twice(s)
        assert go(s, mins(120), ret=False) == [TWICE]
        assert "a" not in sr.load_sent_ids()
        assert go(s, mins(180), ret=True) == [TWICE]  # retried; now confirmed
        assert "a" in sr.load_sent_ids() and go(s, mins(240)) == []
    with Settle() as s:  # unknown outcome -> not recorded; at most one retry per UTC day
        _to_twice(s)
        assert go(s, mins(120), ret=None) == [TWICE] and "a" not in sr.load_sent_ids()
        assert go(s, mins(180), ret=True) == []  # same UTC day: logged, not re-sent
        assert "a" not in sr.load_sent_ids()
        assert go(s, mins(60 * 24), ret=True) == [TWICE]  # next day: retried, confirmed
        assert "a" in sr.load_sent_ids()
    with Settle() as s:  # exception in the transport counts as unknown, never as sent
        _to_twice(s)
        assert go(s, mins(120), ret=OSError("down")) == [TWICE] and "a" not in sr.load_sent_ids()


# ── F5 ───────────────────────────────────────────────────────────────────────
def test_bad_counts_are_ledger_lines():
    cases = {
        "2 identical garbage lines": (BASE, ["{garbage", "{garbage"], 2, "unparseable"),
        "2 identical orphans": (BASE + [st("zz", 1.0, True, mins(-5))] * 2, [], 2, "unparseable"),
        "conflict pair": (BASE + [ent("b", mins(-300)), st("b", 1.0, True, mins(-5)),
                                  st("b", -2.0, False, mins(-4))], [], 2, "conflicting duplicate settlement"),
        "conflict triple": (BASE + [ent("b", mins(-300)), st("b", 1.0, True, mins(-5)),
                                    st("b", 1.0, True, mins(-5)), st("b", -2.0, False, mins(-4))],
                            [], 3, "conflicting duplicate settlement"),
        "conflict + later dup": (BASE + [ent("b", mins(-300)), st("b", 1.0, True, mins(-5)),
                                         st("b", -2.0, False, mins(-4)), st("b", 5.0, True, mins(-3))],
                                 [], 3, "conflicting duplicate settlement"),
        "3 different garbage": (BASE, ["{a", "{b", "[1]"], 3, "unparseable"),
    }
    for name, (rows, extra, n, kind) in cases.items():
        with Settle() as s:
            s.write(rows, extra)
            recs = lv.read()[0]
            v = lv.select(recs, mins(0))
            assert len(v["bad"]) == n and lv.realized(recs, mins(0))["bad"] == n, (name, v["bad"])
            want = f"{n} ledger row(s) {kind}"
            assert want in lv.omit_reason(v) and lv.omit_reason(v).endswith(SCAN), (name, lv.omit_reason(v))
            s.sent = []
            sr.run(send_fn=lambda m: s.sent.append(m) or True, now=mins(0))
            assert any(want in m for m in s.sent), (name, s.sent)
        _, t = _pnl(recs)  # the same rows (incl. bad lines) through the 11pm report and facts
        assert f"{AT}omitted — {want} {SCAN}" in t, (name, t)
        assert _facts(recs) == [f"{AT}omitted — {want} {SCAN}"], name


def test_scan_counts_labelled_not_verified():
    with Settle() as s:
        s.write(BASE + [st("zz", 1.0, True, mins(-5))] * 2)
        sr.record_sent(["a"])  # nothing new to report: only the bad-row notice goes out
        sent = []
        sr.run(send_fn=lambda m: sent.append(m) or True, now=mins(0))
        assert sent and f"2 ledger row(s) unparseable {SCAN}" in sent[0], sent
        assert "✓ verified" not in sent[0], sent  # a scan count is not a verified figure
        names = [c["name"] for r in _log() for c in (r.get("claims") or [])]
        assert "rows" not in names and "waiting" not in names, names
        assert all(m.rstrip().endswith("ledger scan count — no money figure to verify")
                   for m in sent[:1]), sent
    rows = BASE + [ent("b", mins(-300)), st("b", 1.0, True, mins(-5)), st("b", -2.0, False, mins(-4))]
    r, t = _pnl(rows)
    assert r["status"] == "verified" and f"2 ledger row(s) conflicting duplicate settlement {SCAN}" in t, t
    names = [c["name"] for rec in _log() for c in (rec.get("claims") or [])]
    assert "bad rows" not in names and "untimed rows" not in names, names
    assert f"1 settled rows {SCAN}" in t and "ledger 1 settled rows" not in t, t
    assert f"open Kalshi entries {SCAN}" in t, t
    # paused notice: count labelled, no self-compared "waiting" claim
    with Settle() as s:
        s.write(BASE)
        os.remove(paths(s)["bak"])
        os.remove(paths(s)["sent"])
        os.makedirs(paths(s)["sent"])
        open(paths(s)["cur"], "w").write(mins(-3000).isoformat())
        os.makedirs(paths(s)["bak"])
        out = go(s, mins(0))
        assert out == [f"Profit report paused — sent-record unreadable · 1 settlement(s) waiting {SCAN}"], out
    f = cb.verified_facts(get=lambda p: {"balance": 100}, get_positions=lambda: {"market_positions": [
        {"ticker": x, "position": 1, "market_exposure": 100} for x in "AB"]}, ledger=lambda: BASE, now=mins(0))
    lines, claims = cb.facts_block(f)
    assert "⚠ ledger/broker open-position mismatch (ledger scan 0 vs broker 2) — not reconciled" in lines
    assert not any(c["name"] == "ledger open entries" for c in claims), claims


def test_docs_t18b():
    root = os.path.abspath(os.path.join(HERE, "..", ".."))
    proto = open(os.path.join(root, "skills", "valedia", "signal-protocol.md")).read()
    for s in ("seeded_from_cursor", "(ledger scan)", "torn", "settled_time", "distinct ledger LINES"):
        assert s in proto, s


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("signal_v16: PASS")
