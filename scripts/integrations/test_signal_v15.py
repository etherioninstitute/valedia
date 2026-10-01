#!/usr/bin/env python3
"""v15 replay: rollout bootstrap from the old cursor (or one re-init notice), write-ahead
delivery record, two-unknowns stop, lock fallbacks, record truncation / partial-write
recovery, once-a-day paused notices, ONE shared ledger selection (settle / 11pm /
facts agree), 24h window with skew tolerance, corrupt sports log kept aside.
Fakes only: no network, no keys, no orders.  Run: python3 test_signal_v15.py
"""
import builtins
import errno
import fcntl
import json
import multiprocessing as mp
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_signal_v14 import Settle, ent, st, mins, P, AT, POS, _pnl  # noqa: E402
import test_signal_senders_rest as rest  # noqa: E402,F401  (DRY relay + temp signal dir)
import signal_verify as sv  # noqa: E402
import signal_send  # noqa: E402
import forward_test_ledger as ftl  # noqa: E402
import settle_run as sr  # noqa: E402
import settle_record as srec  # noqa: E402
import claude_bridge as cb  # noqa: E402
import sports_engine as se  # noqa: E402

REINIT = "Profit reporting (re)initialized — earlier settlements are included in the 11pm all-time total"
TWICE = "Profit report delivery unconfirmed twice — see the 11pm report"
RESENT = "(re-sent: previous delivery unconfirmed) 1 of these settlement(s)"
A = [ent("a", mins(-300)), st("a", 3.0, True, mins(-12))]
MSG_A = f"{P}+$3.00 (1W/0L)\n{AT}+$3.00 (1W/0L)"


def go(s, now, ret=True):
    """settle_run.run with a fake transport: ret True / False / None, or an exception."""
    sent = []

    def send(m):
        sent.append(m)
        if isinstance(ret, BaseException):
            raise ret
        return ret(m) if callable(ret) else ret
    sr.run(send_fn=send, now=now)
    return [m.split("\nv0.")[0].split("\n✓ verified")[0].split("\nledger scan count")[0] for m in sent]


def rd(path, raw=False):
    """Record lines; the {"seeded_from_cursor": ...} marker line is dropped unless raw."""
    if not os.path.isfile(path):
        return None
    return [l for l in open(path).read().split() if raw or not l.startswith("{")]


def cut(path, n):
    data = open(path, "rb").read()
    open(path, "wb").write(data[:-n])


def paths(s):
    return {"sent": s.sent_path, "bak": os.path.join(s.dir, "settle_" + sr.SENT_FILE),
            "unc": os.path.join(s.sig, sr.UNCONF_FILE), "cur": os.path.join(s.dir, "settle_ftl_signal_cursor.txt")}


def test_bootstrap_from_old_cursor_or_reinit():
    with Settle(fresh_record=False) as s:  # old cursor 11:30, X booked 11:35 never reported
        s.write([ent("x", mins(-300)), st("x", 42.0, True, mins(-25))])
        open(paths(s)["cur"], "w").write(mins(-30).isoformat())
        assert go(s, mins(30)) == [f"{P}+$42.00 (1W/0L)\n{AT}+$42.00 (1W/0L)"]
        assert go(s, mins(90)) == []
    with Settle(fresh_record=False) as s:  # cron slept 3h: Y and Z both after the cursor
        s.write([ent("y", mins(-300)), st("y", -17.5, False, mins(-120)), ent("z", mins(-300)),
                 st("z", 4.0, True, mins(20))])
        open(paths(s)["cur"], "w").write(mins(-150).isoformat())
        assert go(s, mins(30)) == [f"{P}−$13.50 (net loss) (1W/1L)\n{AT}−$13.50 (net loss) (1W/1L)"]
    with Settle(fresh_record=False) as s:  # everything already reported by the old code
        s.write([ent("w", mins(-300)), st("w", 1.0, True, mins(-200))])
        open(paths(s)["cur"], "w").write(mins(-190).isoformat())
        assert go(s, mins(30)) == [] and rd(paths(s)["sent"]) == ['"w"']
    for cur in (None, "garbage", mins(9999).isoformat()):  # no / bad / future cursor -> re-init
        with Settle(fresh_record=False) as s:
            s.write(A + [ent("b", mins(-300)), st("b", 5.0, True, mins(-1))])
            if cur:
                open(paths(s)["cur"], "w").write(cur)
            assert go(s, mins(0)) == [REINIT], cur  # history never marked silently
            assert sorted(rd(paths(s)["sent"])) == ['"a"', '"b"'] and go(s, mins(60)) == []
    with Settle(fresh_record=False) as s:  # re-init notice failed -> nothing marked, retried
        s.write(A)
        assert go(s, mins(0), ret=False) == [REINIT] and rd(paths(s)["sent"]) is None
        assert go(s, mins(60)) == [REINIT] and rd(paths(s)["sent"]) == ['"a"']


def test_first_run_ledger_unreadable_defers_bootstrap():
    for kind in ("missing", "dir"):
        with Settle(fresh_record=False) as s:
            if kind == "dir":
                os.makedirs(ftl.LEDGER)
            assert go(s, mins(0)) == ["Profit report paused — ledger unreadable"], kind
            assert go(s, mins(60)) == [] and rd(paths(s)["sent"]) is None  # once a day, no baseline
            if kind == "dir":
                os.rmdir(ftl.LEDGER)
            s.write([ent("a", mins(-3000)), st("a", 3.0, True, mins(-2000))])
            assert go(s, mins(120)) == [REINIT] and rd(paths(s)["sent"]) == ['"a"']


def test_transport_unknown_twice_failed_and_exceptions():
    for ret in (None, TimeoutError("t")):
        with Settle() as s:
            s.write(A)
            assert go(s, mins(0), ret) == [MSG_A]
            assert go(s, mins(60), ret) == [f"{P}+$3.00 (1W/0L)\n{RESENT}\n{AT}+$3.00 (1W/0L)"]
            assert go(s, mins(120), ret) == [TWICE]  # figure-free, then marked sent
            assert go(s, mins(180), ret) == [] and go(s, mins(240)) == []
    with Settle() as s:  # confirmed failures: retried unlabelled until delivered
        s.write(A)
        for i in range(3):
            assert go(s, mins(60 * i), False) == [MSG_A]
        assert go(s, mins(200)) == [MSG_A] and go(s, mins(260)) == []
    with Settle() as s:  # unknown, failed (count unchanged), unknown, then twice-notice
        s.write(A)
        go(s, mins(0), None)
        assert go(s, mins(60), False)[0].count(RESENT) == 1
        assert go(s, mins(120), None)[0].count(RESENT) == 1
        assert go(s, mins(180)) == [TWICE] and go(s, mins(240)) == []
    with Settle() as s:  # mixed ids: a hits two unknowns while b is on its first
        s.write(A)
        go(s, mins(0), None)
        s.append([ent("b", mins(-300)), st("b", -2.0, False, mins(30))])
        go(s, mins(60), None)
        out = go(s, mins(120), None)
        assert out[0] == TWICE and out[1].startswith(f"{P}−$2.00 (net loss) (0W/1L)\n{RESENT}"), out


def _crash_child():
    def send(m):
        os._exit(9)  # delivered, then the process dies before recording
    sr.run(send_fn=send, now=mins(0))


def test_crash_and_record_write_failures():
    with Settle() as s:
        s.write(A)
        p = mp.Process(target=_crash_child)
        p.start()
        p.join()
        assert p.exitcode == 9 and rd(paths(s)["unc"]) == ['"a"']  # write-ahead survived
        assert go(s, mins(60)) == [f"{P}+$3.00 (1W/0L)\n{RESENT}\n{AT}+$3.00 (1W/0L)"]
        assert go(s, mins(120)) == []
    real_fsync, real_open = os.fsync, builtins.open

    def eio(fd):
        raise OSError(errno.EIO, "EIO")

    def full(suffix):
        def fopen(p, mode="r", *a, **k):
            if str(p).endswith(suffix) and "a" in mode:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_open(p, mode, *a, **k)
        return fopen
    for name, breaker in (("fsync", lambda: setattr(os, "fsync", eio)),
                          ("enospc both", lambda: setattr(builtins, "open", full("profit_sent_ids.jsonl"))),
                          ("enospc backup", lambda: setattr(builtins, "open", full("settle_profit_sent_ids.jsonl")))):
        with Settle() as s:
            s.write(A)
            try:
                out = go(s, mins(0), lambda m: breaker() or True)
            finally:
                os.fsync, builtins.open = real_fsync, real_open
            assert out == [MSG_A], (name, out)  # never a contradictory "withheld" notice
            assert not any("withheld" in m for m in out)
            assert go(s, mins(60)) == [] and go(s, mins(120)) == [], name  # recording retried
            assert "a" in sr.load_sent_ids(), name


def test_lock_fallbacks_and_daily_notice():
    real = fcntl.flock

    def nolock(f, op):
        raise OSError(errno.ENOLCK, "No locks available")
    with Settle() as s:  # NFS: flock unsupported -> O_EXCL pid lock
        s.write(A)
        fcntl.flock = nolock
        try:
            assert go(s, mins(0)) == [MSG_A] and go(s, mins(60)) == []
            pid_lock = os.path.join(s.sig, "settle.lock.pid")
            assert not os.path.exists(pid_lock)
            open(pid_lock, "w").write(f"{os.getpid()} {time.time():.0f}\n")  # live + fresh -> held
            s.append([ent("b", mins(-300)), st("b", 1.0, True, mins(100))])
            assert go(s, mins(120)) == []
            open(pid_lock, "w").write(f"{os.getpid()} {time.time() - 3600:.0f}\n")  # stale
            assert go(s, mins(180))[0].startswith(f"{P}+$1.00")
            srec._excl, real_excl = (lambda p: None), srec._excl
            try:  # no lock possible at all -> one figure-free notice per day
                assert go(s, mins(240)) == ["Profit report paused — no lock available"]
                assert go(s, mins(300)) == []
                assert go(s, mins(240 + 1440)) == ["Profit report paused — no lock available"]
            finally:
                srec._excl = real_excl
        finally:
            fcntl.flock = real
    with Settle() as s:  # held by another run: quiet; held too long: one notice
        s.write(A)
        fh = open(os.path.join(s.sig, "settle.lock"), "a+")
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            assert go(s, mins(0)) == []
            fh.seek(0), fh.truncate(), fh.write(f"{os.getpid()} {time.time() - 3600:.0f}\n"), fh.flush()
            assert go(s, mins(60)) == ["Profit report paused — settle lock held too long"]
        finally:
            fh.close()
        assert go(s, mins(120)) == [MSG_A]
    with Settle() as s:  # settle.lock is a directory -> pid-lock fallback
        s.write(A)
        os.makedirs(os.path.join(s.sig, "settle.lock"))
        assert go(s, mins(0)) == [MSG_A] and go(s, mins(60)) == []


def test_sent_record_truncation_and_disagreement():
    def base():
        s = Settle().__enter__()
        s.write(A + [ent("b", mins(-300)), st("b", 5.0, True, mins(-10))])
        assert go(s, mins(0))[0].startswith(f"{P}+$8.00 (2W/0L)")
        return s
    s = base()
    for p in (paths(s)["sent"],):  # primary cut mid-line: backup has the id
        cut(p, 3)
    assert go(s, mins(60)) == []
    s.__exit__()
    s = base()
    for p in (paths(s)["sent"], paths(s)["bak"]):  # both cut inside b: the torn fragment is
        cut(p, 3)  # discarded; b is in neither record (delivery was confirmed) -> reported normally
    assert go(s, mins(60)) == [f"{P}+$5.00 (1W/0L)\n{AT}+$8.00 (2W/0L)"]
    assert {"a", "b"} <= sr.load_sent_ids() and go(s, mins(120)) == []
    s.__exit__()
    s = base()  # last newline lost, then a new id appended: never merged into one line
    cut(paths(s)["sent"], 1)
    s.append([ent("c", mins(-300)), st("c", -1.0, False, mins(50))])
    assert go(s, mins(60))[0].startswith(f"{P}−$1.00") and go(s, mins(120)) == []
    assert all(json.loads(l) for l in open(paths(s)["sent"]).read().splitlines())
    s.__exit__()
    for wipe in ("deleted", "emptied"):  # both records lost: seeded from the last-report cursor
        s = base()
        for p in (paths(s)["sent"], paths(s)["bak"]):
            os.remove(p) if wipe == "deleted" else open(p, "w").close()
        s.append([ent("c", mins(-300)), st("c", -1.0, False, mins(5))])
        assert go(s, mins(60)) == [f"{P}−$1.00 (net loss) (0W/1L)\n{AT}+$7.00 (2W/1L)"], wipe
        assert go(s, mins(120)) == [], wipe
        s.__exit__()
    for broken in ("bak", "unc"):  # one copy is a directory: the readable copy carries on
        s = base()
        if broken == "bak":
            os.remove(paths(s)["bak"])
        os.makedirs(paths(s)[broken])
        s.append([ent("c", mins(-300)), st("c", -1.0, False, mins(50))])
        assert go(s, mins(60))[0].startswith(f"{P}−$1.00"), broken
        assert go(s, mins(120)) == [], broken
        s.__exit__()


def test_paused_once_per_day_with_count():
    with Settle() as s:
        s.write(A)
        assert go(s, mins(0)) == [MSG_A]
        for p in (paths(s)["sent"], paths(s)["bak"]):
            os.remove(p)
            os.makedirs(p)
        s.append([ent("b", mins(-300)), st("b", 1.0, True, mins(30))])
        want = ["Profit report paused — sent-record unreadable · 1 settlement(s) waiting (ledger scan)"]
        assert go(s, mins(60)) == want
        assert go(s, mins(120)) == [] and go(s, mins(600)) == []  # not hourly
        assert go(s, mins(60 + 1440)) == want  # next day: once more


def test_bad_row_notice_survives_state_loss():
    with Settle() as s:
        s.write(A)
        assert go(s, mins(0)) == [MSG_A]
        s.write(A, ['{"type":"settlement","entry_id":"q","realiz'])
        assert go(s, mins(30)) == ["⚠ 1 ledger row(s) unparseable (ledger scan) — their figures withheld until fixed"]
        for p in srec._pair(sr.STATE_FILE):
            if os.path.exists(p):
                open(p, "w").write("{garbage")
        assert go(s, mins(60)) == [] and go(s, mins(120)) == []


def _all_three(rows, now=mins(0)):
    """(settle message all-time line, 11pm all-time line, facts all-time line)."""
    with Settle() as s:
        s.write(rows)
        out = go(s, now)
    _, t = _pnl(rows)
    f = cb.verified_facts(get=lambda p: {"balance": 100}, get_positions=POS, ledger=lambda: rows, now=now)
    return out, [l for l in t.splitlines() if l.startswith(AT)], cb.facts_block(f)[0][4]


def test_one_shared_selection():
    cases = {
        "int/str id collision": ([dict(ent("5", mins(-300)), id=5), st(5, 3.0, True, mins(-12)), ent("5", mins(-300)),
                                  st("5", 10.0, True, mins(-11))], "2 ledger row(s) conflicting duplicate settlement (ledger scan)"),
        "paper settlement first": (A[:1] + [st("a", 3.0, True, mins(-30), source="paper"), A[1]], "+$3.00"),
        "unreconciled first, same": (A[:1] + [st("a", 3.0, True, mins(-30), fill_status="UNRECONCILED"), A[1]], "+$3.00"),
        "unreconciled first, diff": (A[:1] + [st("a", 2.9, True, mins(-30), fill_status="UNRECONCILED"), A[1]], "+$3.00"),
        "skew +3 min": ([ent("a", mins(-300)), st("a", -9.0, False, mins(3))], "−$9.00"),
        "no entry id": (A + [{"type": "settlement", "source": "live", "logged_at": mins(-5).isoformat(),
                              "realized_pnl_net": 1, "won": True}], "1 ledger row(s) unparseable (ledger scan)"),
        "settled_at +10h": (A + [ent("b", mins(-300)), st("b", -9.0, False, mins(-1), settled_at=mins(600).isoformat())],
                            "1 ledger row(s) future-dated (ledger scan)"),
        "settlement_ts +10h": (A + [ent("b", mins(-300)), st("b", -9.0, False, mins(-1),
                                                            settlement_ts=mins(600).isoformat())], "1 ledger row(s) future-dated (ledger scan)"),
        "mixed venues": ([ent("k", mins(-300)), st("k", 3.0, True, mins(-12)), ent("p", mins(-300), venue="polymarket"),
                          st("p", -2.0, False, mins(-11))], "+$1.00"),
    }
    for name, (rows, want) in cases.items():
        settle, eleven, facts = _all_three(rows)
        if want[0] in "12" and "ledger row" in want:
            assert eleven == [f"{AT}omitted — {want}"], (name, eleven)
            assert facts == f"{AT}omitted — {want}", (name, facts)
            assert all(want in m for m in settle), (name, settle)
        else:
            assert eleven and eleven[0].startswith(AT + want), (name, eleven)
            assert facts.startswith(AT + want), (name, facts)
            assert settle and AT + want in settle[0], (name, settle)


def test_pnl_24h_tolerance():
    base = [ent("a", mins(-3000)), st("a", 3.0, True, mins(-2000))]
    for name, row in (("logged +3 min", st("b", -9.0, False, mins(3))),
                      ("settled_at +4 min", st("b", -9.0, False, mins(-1), settled_at=mins(4).isoformat()))):
        _, t = _pnl(base + [ent("b", mins(-300)), row])
        assert "Settled last 24h (ledger): −$9.00" in t and f"{AT}−$6.00 (net loss)" in t, (name, t)


def test_sports_log_corrupt_moved_aside():
    d = tempfile.mkdtemp(prefix="sportslog_")
    prev_log, prev_send, sent = se.SPORTS_LOG, signal_send.send, []
    se.SPORTS_LOG = os.path.join(d, "sports_engine_log.json")
    signal_send.send = lambda m: sent.append(m) or True
    try:
        open(se.SPORTS_LOG, "w").write('{"bets": [{"fav_ticker": "T1", "cost": 4.0}')  # truncated
        assert se.load_log() == {"bets": [], "stats": {}}
        aside = [f for f in os.listdir(d) if f.startswith("sports_engine_log.corrupt-")]
        assert len(aside) == 1 and '"T1"' in open(os.path.join(d, aside[0])).read()  # history kept
        assert not os.path.exists(se.SPORTS_LOG)
        assert len(sent) == 1 and sent[0].startswith("Sports log corrupt — moved aside")
        assert not any(c.isdigit() for c in sent[0].split("\n✓")[0])  # figure-free
        open(se.SPORTS_LOG, "w").write(json.dumps({"bets": [{"x": 1}]}))
        assert se.load_log()["bets"] == [{"x": 1}]  # a good log is read as is
    finally:
        se.SPORTS_LOG, signal_send.send = prev_log, prev_send


def test_docs_v15():
    root = os.path.abspath(os.path.join(HERE, "..", ".."))
    proto = open(os.path.join(root, "skills", "valedia", "signal-protocol.md")).read()
    for s in (REINIT, TWICE, "settle_ftl_signal_cursor.txt", "write-ahead", "once per day",
              "ledger unreadable", "ledger_view", "future-dated", "Sports log corrupt"):
        assert s in proto, s


if __name__ == "__main__":
    test_bootstrap_from_old_cursor_or_reinit()
    test_first_run_ledger_unreadable_defers_bootstrap()
    test_transport_unknown_twice_failed_and_exceptions()
    test_crash_and_record_write_failures()
    test_lock_fallbacks_and_daily_notice()
    test_sent_record_truncation_and_disagreement()
    test_paused_once_per_day_with_count()
    test_bad_row_notice_survives_state_loss()
    test_one_shared_selection()
    test_pnl_24h_tolerance()
    test_sports_log_corrupt_moved_aside()
    test_docs_v15()
    print("signal_v15: PASS")
