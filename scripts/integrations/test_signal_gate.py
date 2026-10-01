#!/usr/bin/env python3
"""Shared money detector + structural gates (repair round).

Adversarial money text must be withheld everywhere (LLM answers, legacy
cron_runner.sh, watchdog); real VERIFIED broker/realized figures still pass;
a modeled context figure never allows a match; the router relays only
structurally verified bridge output; the 7am brief never crashes; broker and
ledger staleness are labelled. Fakes only: no network, no keys, no orders.
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
import claude_bridge as cb
import kalshi_alert as ka
import watchdog

NOW = datetime(2026, 10, 1, 23, 0, tzinfo=timezone.utc)
ADVERSARIAL = [
    "You made $124 profit", "Profit: +12.5 realized", "Balance 1,234 dollars", "P&L -3",
    "Net +12.345", "Invested 40 USD", "USD 300", "+0.125", "Your gain is 1.2k",
    "Your return was 77.7", "Realized P&L 12.345", "pnl 3m", "lost €50", "12 bucks profit",
    "equity −3.5", "unrealized 5%", "payout 123.45", "You earned 7 cents", "net 1,000,000",
    "Profit $12.499", "Profit: +12.5 (that is your cash)",
    "Unrealized P&L $1.25", "Expected profit $1.25", "EV $3.20", "Your cash is $1.25",
]
CTX = ("[VERIFIED-BROKER] Kalshi cash $12.50; invested (open-position cost) $3.20; 1 open positions\n"
       "[VERIFIED-REALIZED] Realized P&L (ledger, fee-incl.): +$1.25 (1W/1L, 2 settlements)\n"
       "[MODELED — never quote as profit] If-win payout: $123.45\n"
       "[VERIFIED-BROKER] if-win payout $77.70\n"  # mis-tagged modeled line: still never allowed
       "[UNVERIFIED sports log] Cost: $40.00\n[INFO] PAUSED: none")


class Sender(rest.Sender):
    pass


def test_detector_flags_every_adversarial_string():
    for s in ADVERSARIAL:
        assert sv.has_money(s), s
    for s in ("scan ok v0.74.1 host 100.125.9.30 12 markets", "Cron log is 130 min stale (>2h)",
              "You have 3 open bets", "Report at 23:00 UTC on 2026-10-01", "Cash App ready"):
        assert not sv.has_money(s), s
    assert sv.verified_figures(CTX) == [12.5, 3.2, 1.25], sv.verified_figures(CTX)


def test_llm_adversarial_withheld_real_figures_pass():
    rest._fresh_dir()
    for s in ADVERSARIAL + ["The if-win payout is $123.45", "Payout $77.70 expected",
                            "Sports cost $40.00 so far", "You made $12.5k"]:
        send = Sender()
        r = cb.gate_answer("q:" + s, s, CTX, send=True, send_fn=send, now=NOW)
        assert r["status"] == "withheld", (s, r["text"])
        assert len(send.msgs) == 1 and send.msgs[0].startswith("Report withheld (AI answer)")
        for raw, _v in sv.money_tokens(s):
            assert raw.lstrip("+-−$€") not in send.msgs[0], (s, send.msgs[0])
    for s in ("Realized P&L is +$1.25 and you have $12.50 cash.",
              "Invested (open-position cost): $3.20", "You have 3 open bets.",
              "Cash $12.5 — realized $1.250", "cash: 12.5"):
        send = Sender()
        r = cb.gate_answer("ok:" + s, s, CTX, send=True, send_fn=send, now=NOW)
        assert r["status"] == "verified", (s, r)
        assert send.msgs[0].splitlines()[-1].startswith("✓ verified")


def _args(**k):
    return types.SimpleNamespace(question=k.get("q", "q"), no_data=False, model="sonnet",
                                 signal=False, router=k.get("router", True))


def test_bridge_exit_codes_and_header(capsys=None):
    import io
    import contextlib
    rest._fresh_dir()
    for answer, rc, status in (("You have $12.50 cash.", 0, "verified"),
                               ("You made $124 profit", 3, "withheld"),
                               ("Error: timeout", 4, None)):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = cb.run(_args(q=answer), ask=lambda *a, **k: answer, context_fn=lambda: CTX)
        out = buf.getvalue().rstrip("\n").split("\n")
        assert got == rc, (answer, got)
        if status:
            text = "\n".join(out[1:])
            assert out[0] == cb.gate_header(status, text), out
        else:
            assert not out[0].startswith("VALEDIA-GATE"), out


def test_bridge_context_tags_only_agreeing_reads():
    bal = iter([{"balance": 1250}, {"balance": 1250}])
    ctx = cb.get_portfolio_context(get=lambda p: next(bal), get_positions=rest_positions(3.20),
                                   ledger=lambda: [], models_dir=tempfile.mkdtemp())
    assert "[VERIFIED-BROKER] Kalshi cash $12.50; invested (open-position cost) $3.20" in ctx, ctx
    assert "[VERIFIED-REALIZED] Realized P&L (ledger, fee-incl.): +$0.00" in ctx, ctx
    bal = iter([{"balance": 1250}, {"balance": 1300}])
    ctx = cb.get_portfolio_context(get=lambda p: next(bal), get_positions=rest_positions(3.20),
                                   ledger=lambda: [], models_dir=tempfile.mkdtemp())
    assert "[VERIFIED-BROKER]" not in ctx and "12.50" not in ctx and "13.00" not in ctx, ctx
    assert sv.verified_figures(ctx) == [0.0]


def rest_positions(exp):
    return lambda: {"market_positions": [{"ticker": "KXA", "position_fp": "1",
                                          "market_exposure_dollars": str(exp)}]}


def test_cron_runner_withholds_every_adversarial_line():
    d0 = tempfile.mkdtemp(prefix="sigsh_adv_")
    for i, s in enumerate(ADVERSARIAL):
        p = os.path.join(d0, f"s{i}.py")
        open(p, "w").write(f"print({('scan done' + chr(10) + s)!r})\n")
        r, sent, _ = rest._run_sh("cron_runner.sh", [p, "LBL"], {"openclaw": rest._OPENCLAW})
        assert "Report withheld (legacy cron_runner)" in sent, (s, sent)
        assert "scan done" not in sent, (s, sent)  # the WHOLE message is withheld


def test_watchdog_strips_every_adversarial_line():
    rest._fresh_dir()
    for s in ADVERSARIAL:
        send = Sender()
        watchdog.alert("WATCHDOG ALERT\nCron log does not exist\n" + s, send_fn=send)
        t = send.msgs[0]
        assert "Cron log does not exist" in t and "[figure withheld]" in t, (s, t)
        for raw, _v in sv.money_tokens(s):
            assert raw.lstrip("+-−$€") not in t, (s, t)


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
    r = sv.pnl_report(get_positions=pos((NOW - timedelta(minutes=2)).isoformat()),
                      ledger=lambda: _ledger_rows(timedelta(hours=3)), send_fn=send, now=NOW)
    assert r["status"] == "verified" and "broker 22:58 UTC" in send.msgs[0], send.msgs
    assert "STALE" not in send.msgs[0]
    rest._fresh_dir()
    send = Sender()  # venue timestamp old -> withheld
    r = sv.pnl_report(get_positions=pos((NOW - timedelta(hours=2)).isoformat()),
                      ledger=lambda: _ledger_rows(timedelta(hours=3)), send_fn=send, now=NOW)
    assert r["status"] == "withheld" and "broker data STALE" in send.msgs[0], send.msgs
    rest._fresh_dir()
    send = Sender()  # 30-day-old ledger -> never plain verified
    r = sv.pnl_report(get_positions=pos(None), ledger=lambda: _ledger_rows(timedelta(days=30)),
                      send_fn=send, now=NOW)
    assert send.msgs[0].startswith("⚠ STALE: ledger data is 30d"), send.msgs
    assert "broker time unknown" in send.msgs[0]
    rest._fresh_dir()
    send = Sender()  # 2 days quiet with an open position -> STALE (36h rule)
    sv.pnl_report(get_positions=pos(None), ledger=lambda: _ledger_rows(timedelta(days=2)),
                  send_fn=send, now=NOW)
    assert send.msgs[0].startswith("⚠ STALE: ledger data is 2d"), send.msgs
    rest._fresh_dir()
    send = Sender()  # 2 days quiet, NO open positions -> fine (7d rule)
    sv.pnl_report(get_positions=lambda: {"market_positions": []},
                  ledger=lambda: _ledger_rows(timedelta(days=2)), send_fn=send, now=NOW)
    assert "STALE" not in send.msgs[0], send.msgs
    rest._fresh_dir()
    send = Sender()  # ledger without any timestamp -> STALE + "ledger time unknown"
    rows = [{k: v for k, v in r.items() if k != "logged_at"} for r in _ledger_rows(timedelta(0))]
    sv.pnl_report(get_positions=pos(None), ledger=lambda: rows, send_fn=send, now=NOW)
    assert send.msgs[0].startswith("⚠ STALE: ledger") and "ledger time unknown" in send.msgs[0]


if __name__ == "__main__":
    test_detector_flags_every_adversarial_string()
    test_llm_adversarial_withheld_real_figures_pass()
    test_bridge_exit_codes_and_header()
    test_bridge_context_tags_only_agreeing_reads()
    test_cron_runner_withholds_every_adversarial_line()
    test_watchdog_strips_every_adversarial_line()
    test_brief_never_crashes()
    test_pnl_staleness_broker_and_ledger()
    print("signal_gate: PASS")
