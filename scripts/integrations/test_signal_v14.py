#!/usr/bin/env python3
"""v14 replay: id-based Profit ping (no cursor), sent-record robustness, transport
ambiguity, exclusive lock, 11pm 24h/conflict handling, sports save-failure ordering,
7am Decimal sum. Every scenario: correct figures or a figure-free notice — never a
duplicate, never silent. Fakes only: no network, no keys, no orders.
Run: python3 test_signal_v14.py
"""
import json
import multiprocessing as mp
import os
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_signal_senders_rest as rest  # relay booby-traps + temp signal dir
import signal_verify as sv
import signal_send
import forward_test_ledger as ftl
import settle_run as sr
import pnl_run
import claude_bridge as cb
import kalshi_alert as ka
import sports_engine as se
from test_sports_engine import _game
from test_sports_engine_b1_extra import _patch, _restore, _result

NOW0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
mins = lambda m: NOW0 + timedelta(minutes=m)  # noqa: E731
P = "Profit (settled since last report): "
AT = "Realized P&L (all-time, ledger, all venues, fee-incl.): "
WARN1 = "⚠ 1 ledger row(s) unparseable — all-time total omitted"
NOTICE1 = "⚠ 1 ledger row(s) unparseable — their figures withheld until fixed"


def ent(i, t, **k):
    return dict({"type": "entry", "id": i, "venue": "kalshi", "source": "live", "logged_at": t.isoformat(),
                 "order_id": "o" + i, "entry_price": 0.5, "contracts": 2, "entry_fee": 0.02}, **k)


def st(i, p, won, t, **k):
    ts = t.isoformat() if isinstance(t, datetime) else t
    return dict({"type": "settlement", "entry_id": i, "source": "live", "logged_at": ts,
                 "realized_pnl_net": p, "won": won}, **k)


class Settle:
    def __init__(self, fresh_record=False):
        self.fresh_record = fresh_record

    def __enter__(self):
        self.sig = rest._fresh_dir()
        self.dir = tempfile.mkdtemp(prefix="settle14_")
        self.saved = (ftl.LEDGER, sr.MODELS, sr.settle_sports, sys.modules.get("latency_arb"))
        ftl.LEDGER, sr.MODELS = os.path.join(self.dir, "ledger.jsonl"), self.dir
        sr.settle_sports = lambda: ([], {})
        sys.modules["latency_arb"] = types.ModuleType("latency_arb")
        if self.fresh_record:  # an existing (empty) record: no bootstrap baseline
            sr.record_sent([])
        self.sent_path = os.path.join(self.sig, sr.SENT_FILE)
        return self

    def __exit__(self, *a):
        ftl.LEDGER, sr.MODELS, sr.settle_sports = self.saved[:3]
        if self.saved[3] is None:
            sys.modules.pop("latency_arb", None)
        else:
            sys.modules["latency_arb"] = self.saved[3]

    def write(self, rows, extra=()):
        with open(ftl.LEDGER, "w") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows) + "".join(l + "\n" for l in extra))

    def append(self, rows):
        with open(ftl.LEDGER, "a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows))

    def run(self, now, result=True):
        sent = []
        sr.run(send_fn=lambda m: (sent.append(m), result)[1], now=now)
        return [m.split("\nv0.")[0].split("\n✓ verified")[0] for m in sent]  # drop stamp/footer


def test_base_ooo_skew():
    with Settle() as s:
        s.write([ent("a", mins(-300)), st("a", 3.0, True, mins(-12))])
        assert s.run(mins(0)) == [f"{P}+$3.00 (1W/0L)\n{AT}+$3.00 (1W/0L)"]
        assert s.run(mins(60)) == []
        s.append([ent("b", mins(-200)), st("b", -1.25, False, mins(70))])
        assert s.run(mins(90)) == [f"{P}−$1.25 (net loss) (0W/1L)\n{AT}+$1.75 (1W/1L)"]
        assert s.run(mins(150)) == []
    with Settle() as s:  # out-of-order logged_at: b appended later but stamped earlier
        s.write([ent("a", mins(-300)), st("a", 3.0, True, mins(-2))])
        assert s.run(mins(0))[0].startswith(f"{P}+$3.00")
        s.append([ent("b", mins(-300)), st("b", -7.0, False, mins(-5))])
        assert s.run(mins(60)) == [f"{P}−$7.00 (net loss) (0W/1L)\n{AT}−$4.00 (net loss) (1W/1L)"]
        assert s.run(mins(120)) == []
        s.append([ent("c", mins(-300)), st("c", 1.0, True, mins(150))])
        assert s.run(mins(180))[0].startswith(f"{P}+$1.00 (1W/0L)")
    with Settle() as s:  # writer clock skew / step back
        s.write([ent("a", mins(-300)), st("a", 3.0, True, mins(3))])
        assert s.run(mins(0))[0].startswith(f"{P}+$3.00")
        s.append([ent("b", mins(-300)), st("b", 5.0, True, mins(-4))])
        assert s.run(mins(60))[0].startswith(f"{P}+$5.00 (1W/0L)")


def test_bad_rows_never_block_valid_ones():
    with Settle() as s:  # numeric logged_at
        s.write([ent("a", mins(-300)), st("a", 3.0, True, mins(-12))])
        assert s.run(mins(0))
        s.append([ent("b", mins(-300)), st("b", -4.0, False, 1790000000)])
        assert s.run(mins(60)) == [NOTICE1]
        assert s.run(mins(90)) == []  # same bad set: notified once
        s.append([ent("c", mins(-300)), st("c", 2.0, True, mins(100))])
        assert s.run(mins(120)) == [f"{P}+$2.00 (1W/0L)\n{WARN1}"]
    with Settle() as s:  # future-dated row
        fut = st("b", 1.0, True, mins(400 * 1440))
        s.write([ent("a", mins(-300)), st("a", 3.0, True, mins(-12)), ent("b", mins(-300)), fut])
        assert s.run(mins(0)) == [f"{P}+$3.00 (1W/0L)\n{WARN1}"]
        assert s.run(mins(60)) == []
        s.append([ent("c", mins(-300)), st("c", -9.0, False, mins(100))])
        assert s.run(mins(120)) == [f"{P}−$9.00 (net loss) (0W/1L)\n{WARN1}"]
        s.write([ent("a", mins(-300)), st("a", 3.0, True, mins(-12)), ent("b", mins(-300)),
                 st("b", 1.0, True, mins(130)), ent("c", mins(-300)), st("c", -9.0, False, mins(100))])
        assert s.run(mins(180)) == [f"{P}+$1.00 (1W/0L)\n{AT}−$5.00 (net loss) (2W/1L)"]
        assert s.run(mins(240)) == []
    with Settle() as s:  # truncated line, then a different bad (non-object) row
        good = [ent("a", mins(-300)), st("a", 3.0, True, mins(-12))]
        s.write(good, ['{"type": "settlement", "entry_id": "b", "realized_pnl'])
        assert s.run(mins(0)) == [f"{P}+$3.00 (1W/0L)\n{WARN1}"]
        assert s.run(mins(60)) == []
        s.write(good + [[1, 2]])
        assert s.run(mins(120)) == [NOTICE1]  # new bad row -> new notice
        s.write(good)
        assert s.run(mins(180)) == [] and s.run(mins(240)) == []  # a never re-reported
    with Settle() as s:  # orphan settlement
        s.write([ent("a", mins(-300)), st("a", 3.0, True, mins(-12)), st("zz", 1, True, mins(-6))])
        assert s.run(mins(0)) == [f"{P}+$3.00 (1W/0L)\n{WARN1}"] and s.run(mins(60)) == []
    with Settle() as s:  # conflicting duplicate settlement for one entry
        s.write([ent("a", mins(-300)), st("a", 3.0, True, mins(-12)), st("a", -50.0, False, mins(-5)),
                 ent("b", mins(-300)), st("b", 2.0, True, mins(-4))])
        assert s.run(mins(0)) == [f"{P}+$2.00 (1W/0L)\n{WARN1}"]


def test_sent_record_robustness():
    for mode in ("corrupt_line", "list_line", "int_id", "missing", "dup", "huge", "binary", "dir"):
        with Settle() as s:
            s.write([ent("a", mins(-300)), st("a", 3.0, True, mins(-12))])
            assert s.run(mins(0))[0].startswith(f"{P}+$3.00")
            if mode == "corrupt_line":
                open(s.sent_path, "a").write('{"oops"\n')
            elif mode == "list_line":
                open(s.sent_path, "a").write('[1,2]\n')
            elif mode == "missing":
                os.remove(s.sent_path)  # the backup copy keeps the record
            elif mode == "dup":
                open(s.sent_path, "a").write('"a"\n"a"\n')
            elif mode == "huge":
                with open(s.sent_path, "a") as f:
                    f.write("".join(json.dumps(f"x{i}") + "\n" for i in range(6000)))
            elif mode == "binary":
                open(s.sent_path, "ab").write(b"\xff\xfe\x00garbage\n")
            elif mode == "dir":
                os.remove(s.sent_path)
                os.makedirs(s.sent_path)
            s.append([ent("b", mins(-300)), st("b", -1.0, False, mins(-5))])
            r2, r3 = s.run(mins(20)), s.run(mins(80))
            if mode == "dir":  # fail closed: nothing re-reported, one figure-free notice
                assert r2 == ["Profit report paused — sent-record unreadable"] and r3 == [], (mode, r2, r3)
                continue
            assert r2 == [f"{P}−$1.00 (net loss) (0W/1L)\n{AT}+$2.00 (1W/1L)"], (mode, r2)
            assert r3 == [], (mode, r3)
            lines = open(s.sent_path, "rb").read().split(b"\n")
            assert all(json.loads(l) for l in lines if l.strip()), mode  # bad lines rewritten away
            assert {"a", "b"} <= sr.load_sent_ids()


def test_transport_ambiguity():
    with Settle(fresh_record=True) as s:
        s.write([ent("a", mins(-300)), st("a", 3.0, True, mins(-12))])
        assert s.run(mins(0), result=None) == [f"{P}+$3.00 (1W/0L)\n{AT}+$3.00 (1W/0L)"]
        assert s.run(mins(30)) == [f"{P}+$3.00 (1W/0L)\n(re-sent: previous delivery unconfirmed) "
                                   f"1 of these settlement(s)\n{AT}+$3.00 (1W/0L)"]
        assert s.run(mins(60)) == []
    with Settle(fresh_record=True) as s:
        s.write([ent("a", mins(-300)), st("a", 3.0, True, mins(-12))])
        assert s.run(mins(0), result=False)  # confirmed failure: not recorded
        assert s.run(mins(30)) == [f"{P}+$3.00 (1W/0L)\n{AT}+$3.00 (1W/0L)"]  # retried, no label
        assert s.run(mins(60)) == []
    for got, want in (("x", False), (None, False)):
        pass
    calls = []

    class Relay:
        @staticmethod
        def run(argv, **k):
            calls.append(1)
            raise __import__("subprocess").TimeoutExpired(argv, 20)
    prev, prev_dry = signal_send.subprocess, os.environ.pop("VALEDIA_SIGNAL_DRY")
    try:
        signal_send.subprocess = Relay
        assert signal_send.send("x") is None and calls  # timeout -> unknown
    finally:
        signal_send.subprocess, os.environ["VALEDIA_SIGNAL_DRY"] = prev, prev_dry


def _conc_worker(sig, models, ledger, out):
    os.environ["VALEDIA_SIGNAL_DIR"] = sig
    ftl.LEDGER, sr.MODELS = ledger, models
    sr.settle_sports = lambda: ([], {})
    sys.modules["latency_arb"] = types.ModuleType("latency_arb")

    def send(m):
        time.sleep(1.0)
        with open(out, "a") as f:
            f.write(m.replace("\n", " | ") + "\n")
        return True
    sr.run(send_fn=send, now=mins(0))


def test_concurrent_runs_report_once():
    with Settle() as s:
        s.write([ent("a", mins(-300)), st("a", 3.0, True, mins(-12))])
        out = os.path.join(s.dir, "sends.txt")
        ps = [mp.Process(target=_conc_worker, args=(s.sig, s.dir, ftl.LEDGER, out)) for _ in range(2)]
        [p.start() for p in ps]
        [p.join() for p in ps]
        lines = open(out).read().splitlines()
        assert len(lines) == 1 and lines[0].startswith(f"{P}+$3.00"), lines
        assert open(s.sent_path).read().split() == ['"a"']


POS = lambda: {"market_positions": [{"ticker": "A", "position": 1, "market_exposure": 250}]}  # noqa: E731


def _pnl(rows):
    rest._fresh_dir()
    sent = []
    ftl_ledger = lambda: [sv._row(json.dumps(r)) for r in rows]  # noqa: E731
    r = pnl_run.pnl_report(get_positions=POS, ledger=ftl_ledger, send_fn=lambda m: sent.append(m) or True,
                           now=mins(0))
    return r, sent[0] if sent else ""


def test_pnl_24h_and_conflicts():
    base = [ent("a", mins(-3000)), st("a", 3.0, True, mins(-2000))]
    for name, extra in [("garbage", st("b", -9.0, False, "yesterday-ish")),
                        ("numeric", st("b", -9.0, False, mins(-10).timestamp())),
                        ("missing", st("b", -9.0, False, None)),
                        ("settled_at garbage", st("b", -9.0, False, mins(-10), settled_at="bad"))]:
        r, t = _pnl(base + [ent("b", mins(-300)), extra])
        assert r["status"] == "verified", (name, r)
        assert "Settled last 24h (ledger): omitted — 1 row(s) lack times" in t, (name, t)
        assert f"{AT}−$6.00 (net loss) · 1W/1L" in t and "$0.00" not in t, (name, t)
    r, t = _pnl(base + [ent("b", mins(-3000)), st("b", -9.0, False, mins(-10), ts=mins(-1800).isoformat())])
    assert "Settled last 24h (ledger): $0.00" in t, t  # settled 30h ago (ts), booked now
    r, t = _pnl(base + [ent("b", mins(-300)), st("b", -9.0, False, mins(-10))])
    assert "Settled last 24h (ledger): −$9.00" in t, t
    r, t = _pnl(base + [st("a", -50.0, False, mins(-5))])
    assert f"{AT}omitted — 1 ledger row(s) conflicting duplicate settlement" in t, t
    assert "Open-position cost (Kalshi, broker): $2.50" in t and "-50" not in t and "50.00" not in t
    f = cb.verified_facts(get=lambda p: {"balance": 12345}, get_positions=POS,
                          ledger=lambda: base + [st("a", -50.0, False, mins(-5))], now=mins(0))
    assert cb.facts_block(f)[0][4] == f"{AT}omitted — unavailable (conflicting duplicate settlements)"


def test_sports_pings_survive_log_failures():
    def go(res, extra=()):
        rest._fresh_dir()
        patches, sent = [], []
        _patch(signal_send, "send", lambda m: sent.append(m) or True, patches)
        _patch(se, "check_settlements", lambda: ([], {}), patches)
        _patch(se, "get_balance", lambda: {"balance": 100000}, patches)
        _patch(se, "get_fear_greed", lambda: 50, patches)
        _patch(se, "get_optimal_bet_pct", lambda: 0.06, patches)
        _patch(se, "scan_games", lambda: [_game()], patches)
        _patch(se.ftl, "should_trade_category", lambda cat: (True, "ok"), patches)
        _patch(se, "execute_bets", lambda bets, dry_run, **k: res, patches)
        _patch(se, "load_log", lambda: {"bets": [], "stats": {}}, patches)
        _patch(se, "save_log", lambda l: None, patches)
        for mod, attr, fn in extra:
            _patch(mod, attr, fn, patches)
        try:
            se.run(dry_run=False)  # never raises
        finally:
            _restore(patches)
        return sent

    def boom(*a):
        raise OSError("disk full")
    unrec = dict(_result("UNRECONCILED", "T1"), order_id="oid1")
    for extra in ([(se, "save_log", boom)], [(se, "load_log", boom)]):
        sent = go([unrec], extra)
        assert len(sent) == 1 and sent[0].startswith("Kalshi sports: 1 order(s) unconfirmed"), sent
    sent = go([dict(_result("EXECUTED", "T3"), order_id="oid3")], [(se, "save_log", boom)])
    assert len(sent) == 1 and sent[0].startswith("Report withheld (Invested)"), sent  # no ledger fill


def test_ka_decimal_sum():
    sims = {"P1": (1, 0.005, 0.40), "P2": (1, 1.13, 0.50)}

    def execute_trade(o, state, dry_run=False):
        c, up, cost = sims[o["ticker"]]
        return {"status": "DRY_RUN", "contracts": c, "total_cost": cost, "potential_profit": up}
    rest._fresh_dir()
    sent = []
    r = ka.brief(ke=types.SimpleNamespace(_cfg=None, execute_trade=execute_trade), get_positions=POS,
                 send_fn=lambda m: sent.append(m) or True, now=mins(0),
                 state={"balance": 20.0, "max_deployable": 15.0},
                 opps=[{"ticker": "P1", "strategy": "NO_HARVEST", "edge": 0.005},
                       {"ticker": "P2", "strategy": "NO_HARVEST", "edge": 1.13}])
    assert r["status"] == "verified" and "Modeled if-all-win: +$1.14" in sent[0], sent  # float: 1.13
    assert "Open-position cost (Kalshi, broker): $2.50" in sent[0] and "Invested" not in sent[0]


def test_docs_describe_senders():
    root = os.path.abspath(os.path.join(HERE, "..", ".."))
    proto = open(os.path.join(root, "skills", "valedia", "signal-protocol.md")).read()
    for s in ("Profit (settled since last report)", AT.rstrip(": "), "Open-position cost (Kalshi, broker)",
              "Sports fills cost incl. fees (ledger)", "Report withheld", "Profit report paused",
              "AI answer not sent to Signal", "Valedia job", "Modeled if-all-win"):
        assert s in proto, s
    cron = open(os.path.join(root, "skills", "valedia", "cron-schedule.md")).read()
    doors = open(os.path.join(root, "skills", "valedia", "doors.md")).read()
    assert "signal-protocol.md" in cron and "signal-protocol.md" in doors
    assert "`Invested: …` and `Profit: …`" not in doors


if __name__ == "__main__":
    test_base_ooo_skew()
    test_bad_rows_never_block_valid_ones()
    test_sent_record_robustness()
    test_transport_ambiguity()
    test_concurrent_runs_report_once()
    test_pnl_24h_and_conflicts()
    test_sports_pings_survive_log_failures()
    test_ka_decimal_sum()
    test_docs_describe_senders()
    print("signal_v14: PASS")
