#!/usr/bin/env python3
"""signal_verify — every Signal figure is sourced, re-verified, or withheld.

Fake broker / ledger / transport only: no network, no keys, no orders, no --live.
The real relay (signal_send -> ssh) is booby-trapped and must never be reached.
Run: python3 test_signal_verify.py
"""
import json
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
os.environ["VALEDIA_SIGNAL_DRY"] = "1"
SIGDIR = tempfile.mkdtemp(prefix="sigverify_")
os.environ["VALEDIA_SIGNAL_DIR"] = SIGDIR

import signal_send


class _NoRelay:
    @staticmethod
    def run(*a, **k):
        raise AssertionError("real Signal transport called in a test")


signal_send.subprocess = _NoRelay
import signal_verify as sv
import pnl_run
import forward_test_ledger as ftl
import settle_run as sr
import kalshi_alert as ka
import claude_bridge as cb
import btc_day_reminder as btc

NOW = datetime(2026, 10, 1, 23, 0, tzinfo=timezone.utc)


def _fresh_dir():
    d = tempfile.mkdtemp(prefix="sigverify_")
    os.environ["VALEDIA_SIGNAL_DIR"] = d
    return d


def _log(d):
    p = os.path.join(d, "verify_log.jsonl")
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


class Sender:
    def __init__(self):
        self.msgs = []

    def __call__(self, msg):
        self.msgs.append(msg)
        return True


def _ledger():
    E = lambda i, src="live", fs="FILLED", **k: {"type": "entry", "id": i, "venue": "kalshi",
                                                 "source": src, "fill_status": fs, **k}
    S = lambda i, pnl, won, ts, src=None: {"type": "settlement", "entry_id": i,
                                           "realized_pnl_net": pnl, "won": won,
                                           "logged_at": ts, **({"source": src} if src else {})}
    return [E("e1"), S("e1", 2.50, True, "2026-10-01T20:00:00+00:00"),
            E("e2"), S("e2", -1.25, False, "2026-09-30T20:00:00+00:00"),
            E("e3", src="unreconciled", fs="UNRECONCILED"),
            S("e3", 9.99, True, "2026-10-01T21:00:00+00:00", src="unreconciled"),
            E("e4", src="paper"), S("e4", 5.00, True, "2026-10-01T21:00:00+00:00"),
            E("e5")]  # open live Kalshi entry


def _broker(exposure="3.20"):
    return lambda: {"market_positions": [
        {"ticker": "KXA", "position_fp": "4.00", "market_exposure_dollars": exposure},
        {"ticker": "KXOLD", "position_fp": "0", "market_exposure_dollars": "0"}]}


def test_pnl_report_figures_match_sources():
    d, send = _fresh_dir(), Sender()
    res = pnl_run.pnl_report(get_positions=_broker(), ledger=_ledger, send_fn=send, now=NOW)
    assert res["status"] == "verified" and len(send.msgs) == 1, res
    t = send.msgs[0]
    assert "Open-position cost (Kalshi, broker): $3.20" in t, t
    # +2.50 - 1.25; unreconciled 9.99 and paper 5.00 excluded
    assert "Realized P&L (all-time, ledger, all venues, fee-incl.): +$1.25 · 1W/1L\nSettled last 24h (ledger): +$2.50" in t, t
    assert "9.99" not in t and "5.00" not in t
    assert ("✓ verified: ledger 2 settled rows, last row 2026-10-01 20:00 UTC, "
            "broker time unknown (read 23:00 UTC)") in t, t
    assert "⚠" not in t  # ledger open (e5) == broker open (KXA)
    rec = _log(d)[-1]
    assert rec["status"] == "verified" and rec["sent"] is True
    assert os.path.exists(os.path.join(d, "sent_log.jsonl"))


def test_broker_mismatch_withholds_and_logs():
    d, send = _fresh_dir(), Sender()
    reads = iter([_broker("3.20")(), _broker("4.10")()])
    res = pnl_run.pnl_report(get_positions=lambda: next(reads), ledger=_ledger, send_fn=send, now=NOW)
    assert res["status"] == "withheld", res
    t = send.msgs[0]
    assert t.startswith("Report withheld (11pm P&L): Open-position cost does not match broker re-read"), t
    assert "3.20" not in t and "4.10" not in t and "1.25" not in t  # no unverified numbers
    rec = _log(d)[-1]
    assert rec["status"] == "withheld" and "4.1" in json.dumps(rec["problems"])


def test_ledger_mismatch_withholds():
    _fresh_dir()
    send, calls = Sender(), []

    def ledger():
        calls.append(1)
        recs = _ledger()
        if len(calls) > 1:
            recs.append({"type": "settlement", "entry_id": "e5", "realized_pnl_net": 3.0,
                         "won": True, "logged_at": "2026-10-01T22:59:00+00:00"})
        return recs
    res = pnl_run.pnl_report(get_positions=_broker(), ledger=ledger, send_fn=send, now=NOW)
    assert res["status"] == "withheld" and "Realized does not match" in send.msgs[0], send.msgs


def test_source_failure_withholds_never_crashes():
    d, send = _fresh_dir(), Sender()

    def boom():
        raise ConnectionError("kalshi down")
    res = pnl_run.pnl_report(get_positions=boom, ledger=_ledger, send_fn=send, now=NOW)
    assert res["status"] == "withheld" and "source read failed" in send.msgs[0]
    assert "kalshi down" in json.dumps(_log(d)[-1])
    # verifier internal error -> still one withheld notice (never silent)
    send2 = Sender()
    res2 = sv.deliver("x", "body", claims=[{"name": "malformed"}], send_fn=send2, now=NOW)
    assert res2["status"] == "error" and send2.msgs and "withheld" in send2.msgs[0]


def test_stale_label_and_withhold():
    _fresh_dir()
    old = "2026-10-01T20:00:00+00:00"  # 3h old
    send = Sender()
    r = sv.deliver("k", "Profit: +1.00", claims=[sv.claim("Profit", 1.0, 1.0, "ledger")],
                   freshness=[sv.fresh("settlement", old, 3600, policy="label")],
                   footer=["ledger 1 rows"], send_fn=send, now=NOW)
    assert r["status"] == "verified" and send.msgs[0].startswith(
        "⚠ STALE: settlement data is 3h00m old"), send.msgs
    send = Sender()
    r = sv.deliver("k", "Invested: 1.00", claims=[sv.claim("Invested", 1.0, 1.0, "broker")],
                   freshness=[sv.fresh("broker", old, 900)], send_fn=send, now=NOW)
    assert r["status"] == "withheld" and "broker data STALE (3h00m old)" in send.msgs[0]
    assert "1.00" not in send.msgs[0]


def test_unsourced_and_arithmetic():
    _fresh_dir()
    send = Sender()
    r = sv.deliver("k", "Profit: +1.00 (MLB -24.94)",
                   claims=[sv.claim("Profit", 1.0, 1.0, "ledger")], send_fn=send, now=NOW)
    assert r["status"] == "withheld" and "no named source" in send.msgs[0]
    r = sv.deliver("k2", "Total: +3.00", claims=[sv.sum_claim("Total", 3.0, [1.0, 1.5])],
                   send_fn=send, now=NOW)
    assert r["status"] == "withheld" and "Total does not match sum of parts" in send.msgs[-1]
    # version stamps are not money figures
    assert sv.unsourced_figures("x v0.74.1 abc", []) == []


def test_dedupe_same_message_once():
    _fresh_dir()
    send = Sender()
    a = pnl_run.pnl_report(get_positions=_broker(), ledger=_ledger, send_fn=send, now=NOW)
    b = pnl_run.pnl_report(get_positions=_broker(), ledger=_ledger, send_fn=send, now=NOW)
    assert a["status"] == "verified" and b["status"] == "dedupe" and len(send.msgs) == 1


def test_settle_run_profit_id_based():
    from test_signal_v14 import Settle
    with Settle(fresh_record=True) as st_:
        st_.write(_ledger())
        fake_arb = types.ModuleType("latency_arb")
        fake_arb.check_arb_settlements = lambda: {"settled": 3, "net_pnl": 7.77, "wins": 2, "losses": 1}
        sys.modules["latency_arb"] = fake_arb
        sr.settle_sports = lambda: (_ for _ in ()).throw(RuntimeError("no sports log"))
        send = Sender()
        sr.run(send_fn=send, now=NOW)  # never crashes on sports-log failure
        assert len(send.msgs) == 1, send.msgs  # arb-log is never sent
        t = send.msgs[0]
        assert t.startswith("⚠ STALE: settlement data is"), t  # 09-30 row is > 6h old
        assert "\nProfit (settled since last report): +$1.25 (1W/1L)\n" in t, t
        assert "Realized P&L (all-time, ledger, all venues, fee-incl.): +$1.25 (1W/1L)" in t, t
        assert "7.77" not in t and "9.99" not in t and "5.00" not in t
        assert "✓ verified: ledger 2 settled rows" in t, t
        send2 = Sender()
        sr.run(send_fn=send2, now=NOW + timedelta(hours=1))
        assert send2.msgs == [], send2.msgs  # nothing reported twice
        assert {"e1", "e2"} <= sr.load_sent_ids()


def test_sports_invested_from_ledger_fills():
    _fresh_dir()
    recs = [{"type": "entry", "id": "a", "order_id": "o1", "entry_price": 0.80, "contracts": 5,
             "entry_fee": 0.02, "source": "live", "fill_status": "FILLED",
             "logged_at": "2026-10-01T22:55:00+00:00"},
            {"type": "entry", "id": "b", "order_id": "o2", "entry_price": 0.75, "contracts": 4,
             "entry_fee": 0.03, "source": "live", "fill_status": "FILLED",
             "logged_at": "2026-10-01T22:56:00+00:00"}]
    ex = [{"order_id": "o1", "cost": 4.0, "fee": 0.02}, {"order_id": "o2", "cost": 3.0, "fee": 0.03}]
    send = Sender()
    r = sv.invested_ping(ex, ledger=lambda: recs, send_fn=send, now=NOW)
    assert r["status"] == "verified", r
    assert send.msgs[0].startswith("Sports fills cost incl. fees (ledger): $7.05 on 2 games")
    send = Sender()
    r = sv.invested_ping(ex + [{"order_id": "o3", "cost": 1.0}], ledger=lambda: recs,
                         send_fn=send, now=NOW)
    assert r["status"] == "withheld" and "games does not match ledger fills" in send.msgs[0]


def test_alert_modeled_never_profit():
    _fresh_dir()

    def execute_trade(o, state, dry_run=False):
        assert dry_run is True
        n = o["n"]
        return {"status": "DRY_RUN", "contracts": n, "total_cost": round(n * o["price"], 2),
                "potential_profit": round(n * o["edge"], 3)}
    ke = types.SimpleNamespace(_cfg=None, execute_trade=execute_trade)
    opps = [{"ticker": "A", "strategy": "NO_HARVEST", "price": 0.80, "edge": 0.15, "n": 10},
            {"ticker": "B", "strategy": "NO_HARVEST", "price": 0.90, "edge": 0.08, "n": 10},
            {"ticker": "C", "strategy": "NO_HARVEST", "price": 0.50, "edge": 0.12, "n": 5}]
    send = Sender()
    r = ka.brief(ke=ke, get_positions=_broker(), send_fn=send, now=NOW,
                 state={"balance": 20.0, "max_deployable": 15.0}, opps=opps)
    assert r["status"] == "verified", r
    t = send.msgs[0]
    assert "Open-position cost (Kalshi, broker): $3.20" in t and "Modeled if-all-win: +$2.10 (2 picks that fit cash" in t, t
    for line in t.lower().splitlines():
        assert not line.startswith("profit") and "estimated profit" not in line
        if "profit" in line:
            assert "not profit" in line, line
    send = Sender()
    r = ka.brief(ke=ke, get_positions=_broker(), send_fn=send, now=NOW,
                 state={"balance": 0.0, "max_deployable": 0.0}, opps=opps)
    assert r["status"] == "not_hot" and send.msgs == []


def test_bridge_ai_answer_gated():
    d = _fresh_dir()
    facts = {"cash": (12.5, 12.5), "positions": {"invested": (3.2, 3.2), "n": (1, 1)},
             "ledger": None, "why": {"ledger": "STALE (no recent live rows)"}}
    send = Sender()
    ok = cb.gate_answer("q", "You have $12.50 cash. The slate looks calm tonight.", facts,
                        send=True, send_fn=send, now=NOW)
    assert ok["status"] == "verified", ok
    t = send.msgs[0]
    path = os.path.join(d, "ai_answers", "20261001T230000000000Z.txt")
    assert t == "\n".join(cb.facts_block(facts)[0] + [cb.AI_LINE + path, cb.FOOTER]), t
    assert "slate" not in t and "Cash (Kalshi, broker): $12.50" in t
    assert "The slate looks calm tonight." in open(path).read()  # full answer saved locally
    local = cb.gate_answer("q3", "MLB lost $24.94", facts, send=False, now=NOW)
    assert local["sent"] is False and "24.94" not in local["text"] and len(send.msgs) == 1


def test_btc_day_date_guard():
    _fresh_dir()
    send, cleaned = Sender(), []
    r = btc.main(send_fn=send, now=NOW, cron_cleanup=lambda: cleaned.append(1) or True)
    assert r["status"] == "withheld" and "STALE" in send.msgs[0] and cleaned == [1]
    _fresh_dir()
    send = Sender()
    r = btc.main(send_fn=send, now=datetime(2026, 4, 6, 15, tzinfo=timezone.utc),
                 cron_cleanup=lambda: True)
    assert r["status"] == "verified" and "btc.day" in send.msgs[0]
    r = btc.main(send_fn=send, now=datetime(2026, 4, 1, tzinfo=timezone.utc),
                 cron_cleanup=lambda: 1 / 0)
    assert r["status"] == "not_due"


def test_transport_returns_status_never_raises():
    assert signal_send.send("x") is False  # DRY: printed, not relayed
    calls = []

    class FakeRelay:
        rc = 0

        @classmethod
        def run(cls, argv, **k):
            calls.append(argv)
            return types.SimpleNamespace(returncode=cls.rc, stderr=b"")
    prev, prev_dry = signal_send.subprocess, os.environ.pop("VALEDIA_SIGNAL_DRY")
    try:
        signal_send.subprocess = FakeRelay
        assert signal_send.send("Invested: $1.00") is True
        assert "USD 1.00" in calls[-1][-1]
        FakeRelay.rc = 255  # ssh's own error: the remote may have run -> unknown
        assert signal_send.send("x") is None
        FakeRelay.rc = 1  # relay itself exited non-zero -> confirmed failure
        assert signal_send.send("x") is False
    finally:
        signal_send.subprocess = prev
        os.environ["VALEDIA_SIGNAL_DRY"] = prev_dry


if __name__ == "__main__":
    test_pnl_report_figures_match_sources()
    test_broker_mismatch_withholds_and_logs()
    test_ledger_mismatch_withholds()
    test_source_failure_withholds_never_crashes()
    test_stale_label_and_withhold()
    test_unsourced_and_arithmetic()
    test_dedupe_same_message_once()
    test_settle_run_profit_id_based()
    test_sports_invested_from_ledger_fills()
    test_alert_modeled_never_profit()
    test_bridge_ai_answer_gated()
    test_btc_day_date_guard()
    test_transport_returns_status_never_raises()
    print("signal_verify: PASS")
