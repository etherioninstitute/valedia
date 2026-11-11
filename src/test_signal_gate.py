#!/usr/bin/env python3
"""Money detector (Signal pings), 7am brief never crashes, broker/ledger staleness.
Free-text scrubbing / AI answers / router / cron_runner / watchdog: test_signal_scrub.py.
Fakes only: no network, no keys, no orders.
Run: python3 test_signal_gate.py
"""
import json
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_signal_senders_rest as rest  # installs the relay booby-traps + temp signal dir
import signal_verify as sv
import pnl_run
import kalshi_alert as ka

NOW = datetime(2026, 10, 1, 23, 0, tzinfo=timezone.utc)
ADVERSARIAL = [
    "You made $124 profit", "Profit: +12.5 realized", "Balance 1,234 dollars", "P&L -3",
    "Net +12.345", "Invested 40 USD", "USD 300", "+0.125", "Your gain is 1.2k",
    "Your return was 77.7", "Realized P&L 12.345", "pnl 3m", "lost €50", "12 bucks profit",
    "equity −3.5", "unrealized 5%", "payout 123.45", "You earned 7 cents", "net 1,000,000",
    "Profit $12.499", "Profit: +12.5 (that is your cash)",
    "Unrealized P&L $1.25", "Expected profit $1.25", "EV $3.20", "Your cash is $1.25",
]
class Sender(rest.Sender):
    pass


def test_detector_flags_every_adversarial_string():
    for s in ADVERSARIAL:
        assert bool(sv.money_tokens(s)), s
    for s in ("scan ok v0.74.1 host 100.125.9.30 12 markets", "Cron log is 130 min stale (>2h)",
              "You have 3 open bets", "Report at 23:00 UTC on 2026-10-01", "Cash App ready"):
        assert not bool(sv.money_tokens(s)), s


def rest_positions(exp):
    return lambda: {"market_positions": [{"ticker": "KXA", "position_fp": "1",
                                          "market_exposure_dollars": str(exp)}]}


def test_brief_never_crashes():
    rest._fresh_dir()

    def boom(*a, **k):
        raise RuntimeError("sim exploded")
    opp = {"ticker": "A", "strategy": "NO_HARVEST", "price": 0.8, "edge": 0.1, "n": 1}
    for ke, state in ((types.SimpleNamespace(_cfg=None, execute_trade=boom), {"balance": 9}),
                      (types.SimpleNamespace(_cfg=None), {"balance": 9}),
                      (types.SimpleNamespace(_cfg=None, execute_trade=lambda *a, **k: {}), None)):
        rest._fresh_dir()
        send = Sender()
        r = ka.brief(ke=ke, get_positions=rest_positions(1), send_fn=send, now=NOW,
                     state=state, opps=[opp])
        assert r["status"] == "withheld" and send.msgs[0].startswith("Report withheld (7am brief)"), r


def _ledger_rows(age):
    ts = (NOW - age).isoformat()
    return [{"type": "entry", "id": "e1", "venue": "kalshi", "source": "live",
             "fill_status": "FILLED", "logged_at": ts},
            {"type": "settlement", "entry_id": "e1", "realized_pnl_net": 1.0, "won": True,
             "logged_at": ts}]


def test_pnl_staleness_broker_and_ledger():
    rest._fresh_dir()
    pos = lambda vts: (lambda: {"ts": vts, "market_positions": [  # noqa: E731
        {"ticker": "KXA", "position_fp": "1", "market_exposure_dollars": "2.00"}]})
    send = Sender()  # venue timestamp fresh -> footer uses it, no STALE
    r = pnl_run.pnl_report(get_positions=pos((NOW - timedelta(minutes=2)).isoformat()),
                      ledger=lambda: _ledger_rows(timedelta(hours=3)), send_fn=send, now=NOW)
    assert r["status"] == "verified" and "broker 22:58 UTC" in send.msgs[0], send.msgs
    assert "STALE" not in send.msgs[0]
    rest._fresh_dir()
    send = Sender()  # venue timestamp old -> withheld
    r = pnl_run.pnl_report(get_positions=pos((NOW - timedelta(hours=2)).isoformat()),
                      ledger=lambda: _ledger_rows(timedelta(hours=3)), send_fn=send, now=NOW)
    assert r["status"] == "withheld" and "broker data STALE" in send.msgs[0], send.msgs
    rest._fresh_dir()
    send = Sender()  # 30-day-old ledger -> never plain verified
    r = pnl_run.pnl_report(get_positions=pos(None), ledger=lambda: _ledger_rows(timedelta(days=30)),
                      send_fn=send, now=NOW)
    assert send.msgs[0].startswith("⚠ STALE: ledger data is 30d"), send.msgs
    assert "broker time unknown" in send.msgs[0]
    rest._fresh_dir()
    send = Sender()  # 2 days quiet with an open position -> STALE (36h rule)
    pnl_run.pnl_report(get_positions=pos(None), ledger=lambda: _ledger_rows(timedelta(days=2)),
                  send_fn=send, now=NOW)
    assert send.msgs[0].startswith("⚠ STALE: ledger data is 2d"), send.msgs
    rest._fresh_dir()
    send = Sender()  # 2 days quiet, NO open positions -> fine (7d rule)
    pnl_run.pnl_report(get_positions=lambda: {"market_positions": []},
                  ledger=lambda: _ledger_rows(timedelta(days=2)), send_fn=send, now=NOW)
    assert "STALE" not in send.msgs[0], send.msgs
    rest._fresh_dir()
    send = Sender()  # ledger without any timestamp -> STALE + "ledger time unknown"
    rows = [{k: v for k, v in r.items() if k != "logged_at"} for r in _ledger_rows(timedelta(0))]
    pnl_run.pnl_report(get_positions=pos(None), ledger=lambda: rows, send_fn=send, now=NOW)
    assert send.msgs[0].startswith("⚠ STALE: ledger") and "ledger time unknown" in send.msgs[0]


if __name__ == "__main__":
    test_detector_flags_every_adversarial_string()
    test_brief_never_crashes()
    test_pnl_staleness_broker_and_ledger()
    print("signal_gate: PASS")
