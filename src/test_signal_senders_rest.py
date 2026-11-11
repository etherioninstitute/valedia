#!/usr/bin/env python3
"""Remaining Signal senders: kalshi_executor --mode alert, watchdog, cron_runner.sh,
claude_router.sh. Fakes only: no network, no keys, no orders, no --live. The real
relay (signal_send -> ssh, openclaw, ssh to NIMROD) is booby-trapped / faked.
Run: python3 test_signal_senders_rest.py
"""
import json
import os
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
os.environ["VALEDIA_SIGNAL_DRY"] = "1"
os.environ["VALEDIA_SIGNAL_DIR"] = tempfile.mkdtemp(prefix="sigrest_")

import signal_send


class _NoRelay:
    @staticmethod
    def run(*a, **k):
        raise AssertionError("real Signal transport called in a test")


signal_send.subprocess = _NoRelay
import signal_verify as sv
import kalshi_executor as ke
import kalshi_auth
import watchdog
watchdog.subprocess = _NoRelay


def _fresh_dir():
    d = tempfile.mkdtemp(prefix="sigrest_")
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


def _opp(ticker, strategy, price, edge, n):
    return {"ticker": ticker, "strategy": strategy, "side": "no", "price": price,
            "edge": edge, "score": 50, "title": "t " + ticker, "hours_to_exp": 24,
            "rationale": "fee-true sim", "n": n}


def _fake_execute(o, state, dry_run=False):
    assert dry_run is True, "alert path must never place an order"
    n = o["n"]
    return {"status": "DRY_RUN", "contracts": n, "total_cost": round(n * o["price"], 2),
            "potential_profit": round(n * o["edge"], 3), "roi_pct": 10}


def test_executor_alert_verified_modeled_fenced_excluded():
    d = _fresh_dir()
    send = Sender()
    saved = (ke.get_state, ke.scan_all_markets, ke.execute_trade, signal_send.send,
             kalshi_auth.get_positions)
    import coinbase_trader
    import alpaca_executor
    cb, alp = coinbase_trader.get_balances, alpaca_executor.get_account
    coinbase_trader.get_balances = lambda: {"USD": {"balance": 0}, "USDC": {"balance": 0}}
    alpaca_executor.get_account = lambda: {"cash": 1.0, "equity": 30.0}
    ke.get_state = lambda: {"balance": 50.0, "portfolio_value": 3.2, "n_positions": 1,
                            "max_deployable": 40.0}
    # fenced oil pick (huge edge) must not enter any modeled total
    ke.scan_all_markets = lambda: [
        _opp("KXWTI-26MAR23-T99.99", "NO_HARVEST", 0.50, 0.40, 10),
        _opp("KXBTC-A", "NO_HARVEST", 0.80, 0.15, 10),
        _opp("KXBTC-B", "NO_HARVEST", 0.50, 0.12, 5)]
    ke.execute_trade = _fake_execute
    signal_send.send = send
    kalshi_auth.get_positions = lambda: {"market_positions": [
        {"ticker": "KXA", "position_fp": "4.00", "market_exposure_dollars": "3.20"}]}
    try:
        report = ke.send_opportunity_alert()
    finally:
        (ke.get_state, ke.scan_all_markets, ke.execute_trade, signal_send.send,
         kalshi_auth.get_positions) = saved
        coinbase_trader.get_balances, alpaca_executor.get_account = cb, alp
    assert "Estimated profit" not in report and "PROFIT POTENTIAL" not in report, report
    assert "MODELED UPSIDE" in report and "not profit" in report
    # printed report total = unfenced picks only: 10*0.15 + 5*0.12 = 2.10 (oil 4.00 excluded)
    assert "$2.10" in report and "$6.10" not in report, report
    assert len(send.msgs) == 1, send.msgs
    t = send.msgs[0]
    assert "Open-position cost (Kalshi, broker): $3.20" in t and "Modeled if-all-win: +$2.10" in t and "not profit" in t, t
    assert "4.00" not in t and "estimated profit" not in t.lower()
    assert "✓ verified" in t
    assert _log(d)[-1]["kind"] == "7am brief" and _log(d)[-1]["status"] == "verified"


def test_executor_alert_broker_failure_withholds_never_crashes():
    d = _fresh_dir()
    send = Sender()
    saved = (ke.get_state, ke.scan_all_markets, ke.execute_trade, signal_send.send,
             kalshi_auth.get_positions)
    import coinbase_trader
    import alpaca_executor
    cb, alp = coinbase_trader.get_balances, alpaca_executor.get_account
    coinbase_trader.get_balances = lambda: {"USD": {"balance": 0}, "USDC": {"balance": 0}}
    alpaca_executor.get_account = lambda: {"cash": 1.0, "equity": 30.0}
    ke.get_state = lambda: {"balance": 50.0, "portfolio_value": 0.0, "n_positions": 0,
                            "max_deployable": 40.0}
    ke.scan_all_markets = lambda: [_opp("KXBTC-A", "NO_HARVEST", 0.80, 0.15, 10)]
    ke.execute_trade = _fake_execute
    signal_send.send = send

    def boom():
        raise RuntimeError("broker down")
    kalshi_auth.get_positions = boom
    try:
        ke.send_opportunity_alert()  # must not raise
    finally:
        (ke.get_state, ke.scan_all_markets, ke.execute_trade, signal_send.send,
         kalshi_auth.get_positions) = saved
        coinbase_trader.get_balances, alpaca_executor.get_account = cb, alp
    assert len(send.msgs) == 1 and send.msgs[0].startswith("Report withheld (7am brief)"), send.msgs
    assert "1.50" not in send.msgs[0] and "$" not in send.msgs[0]
    assert _log(d)[-1]["status"] == "withheld"


def test_watchdog_text_only_no_money():
    d = _fresh_dir()
    send = Sender()
    r = watchdog.alert("WATCHDOG ALERT\nPnL $24.94 and +3.10", send_fn=send,
                       code_lines=["Cron log is 130 min stale (>2h)"])
    assert r["sent"] is True and len(send.msgs) == 1
    t = send.msgs[0]
    assert "24.94" not in t and "3.10" not in t, t
    assert "PnL $[n] and +[n]" in t and "Cron log is 130 min stale (>2h)" in t, t
    assert "ops alert, no money figures" in t
    assert _log(d)[-1]["kind"] == "watchdog"
    # numbers in free text never survive, even if they look like an age
    t2 = watchdog.ops_alert("watchdog", "Cron log is 130 min stale", send_fn=send)["text"]
    assert "130" not in t2 and "[n] min stale" in t2, t2
    # figure-free alert is untouched and still sent every time (no dedupe)
    for _ in range(2):
        watchdog.ops_alert("watchdog", "WATCHDOG ALERT\nCron log does not exist", send_fn=send)
    assert len(send.msgs) == 4
    # transport failure and relay trap never raise
    assert watchdog.ops_alert("watchdog", "x", send_fn=lambda m: 1 / 0)["sent"] is False
    watchdog.alert("WATCHDOG ALERT\nx $5.00")  # default _ssh_send -> booby-trapped subprocess
    # check(): the stale-age line is code-built (int minutes), file names are scrubbed
    import time as _t
    tmp = tempfile.mkdtemp(prefix="wd_")
    log, scripts = os.path.join(tmp, "cron.log"), os.path.join(tmp, "scripts")
    os.makedirs(scripts)
    open(log, "w").write("x")
    os.utime(log, (_t.time() - 3 * 3600, _t.time() - 3 * 3600))
    bad = os.path.join(scripts, "profit_12.sh")
    open(bad, "w").write("#!/bin/sh\n")
    os.chmod(bad, 0o644)
    saved = (watchdog.LOG, watchdog.RUNNER, watchdog.os.path.expanduser)
    real_exp = os.path.expanduser
    watchdog.LOG, watchdog.RUNNER = log, os.path.join(tmp, "missing.sh")
    watchdog.os.path.expanduser = lambda p: scripts if p.endswith("/workspace/scripts") else real_exp(p)
    send = Sender()
    try:
        watchdog.check(send_fn=send)
    finally:
        watchdog.LOG, watchdog.RUNNER, watchdog.os.path.expanduser = saved
    t = send.msgs[0]
    assert "Cron log is 180 min stale (>2h)" in t and "Fixed: profit_[n].sh was not executable" in t, t


def _run_sh(script, args, bins, env_extra=None):
    d = tempfile.mkdtemp(prefix="sigsh_")
    bindir = os.path.join(d, "bin")
    os.makedirs(bindir)
    calls = os.path.join(d, "calls.log")
    for name, body in bins.items():
        p = os.path.join(bindir, name)
        with open(p, "w") as f:
            f.write("#!/bin/bash\n" + body.replace("@CALLS@", calls))
        os.chmod(p, 0o755)
    env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"],
               VALEDIA_SIGNAL_DIR=os.path.join(d, "sig"), OPENCLAW_ENV=os.path.join(d, "none"),
               CRON_LOG=os.path.join(d, "cron.log"),
               **(env_extra or {}))
    r = subprocess.run(["bash", os.path.join(SCRIPTS, script)] + args, env=env,
                       capture_output=True, text=True, timeout=30)
    sent = open(calls).read() if os.path.exists(calls) else ""
    return r, sent, d


_OPENCLAW = 'printf "%s\\n" "$*" >> @CALLS@\n'


def test_cron_runner_sends_only_fixed_lines():
    d0 = tempfile.mkdtemp(prefix="sigsh_s_")
    money = os.path.join(d0, "money.py")
    ops = os.path.join(d0, "ops.py")
    fail = os.path.join(d0, "fail.py")
    open(money, "w").write('print("Profit today: $24.94 (+3.10) on 5 bets")\n')
    open(ops, "w").write('print("scan ok v0.75.0 host 100.125.9.30 12 markets")\n')
    open(fail, "w").write('import sys\nprint("lost twelve dollars"); sys.exit(1)\n')
    r, sent, d = _run_sh("cron_runner.sh", [money, "PNL"], {"openclaw": _OPENCLAW})
    assert r.returncode == 0, r.stderr
    assert sent.strip().endswith("--message Valedia job finished OK"), sent  # PNL: unknown label
    assert "24.94" in open(os.path.join(d, "cron.log")).read()  # raw output: log only
    r, sent, d = _run_sh("cron_runner.sh", [ops, "SCAN"], {"openclaw": _OPENCLAW})
    assert "scan ok" not in sent and sent.strip().endswith("--message Valedia job finished OK"), sent
    r, sent, d = _run_sh("cron_runner.sh", [ops, "P&L TRACKER"], {"openclaw": _OPENCLAW})
    assert sent.strip().endswith("--message Valedia job P&L TRACKER finished OK"), sent
    r, sent, d = _run_sh("cron_runner.sh", [fail, "P&L $12 run-7"], {"openclaw": _OPENCLAW})
    assert sent.strip().endswith("--message Valedia job FAILED (see cron log)"), sent
    assert not any(c.isdigit() for c in sent.split("--message", 1)[1]), sent


_NIMROD_GATED = ("AI answer (Claude; any numbers it wrote are shown as [n]):\nYou have [n] cash.\n\n"
                 "Facts (code-generated; broker read twice, ledger read twice):\nCash (Kalshi, broker): $12.50\n"
                 "✓ figures from broker/ledger only")


def test_claude_router_relays_only_gated_text():
    import claude_bridge as cb

    def ssh(text, rc=0):
        return "cat <<'EOF'\n" + text + "\nEOF\nexit %d\n" % rc

    def gated(status, text):
        return cb.gate_header(status, text) + "\n" + text
    # structurally gated bridge output (header sha + exit 0) is relayed
    r, sent, _ = _run_sh("claude_router.sh", ["q", "sonnet", "--signal"],
                         {"ssh": ssh(gated("verified", _NIMROD_GATED)), "openclaw": _OPENCLAW})
    assert "✓ figures from broker/ledger only" in sent and "12.50" in sent, sent
    assert "VALEDIA-GATE" not in sent
    assert "VALEDIA-GATE" not in r.stdout and "12.50" in r.stdout
    # a single withheld notice (exit 3) is relayed
    wh = "Report withheld (AI answer): figure(s) with no named source. Unverified figures not sent; see data/signal/verify_log.jsonl."
    r, sent, _ = _run_sh("claude_router.sh", ["q", "sonnet", "--signal"],
                         {"ssh": ssh(gated("withheld", wh), 3), "openclaw": _OPENCLAW})
    assert "Report withheld (AI answer)" in sent
    # text markers alone are NOT trusted: correct-looking footer without header
    r, sent, _ = _run_sh("claude_router.sh", ["q", "sonnet", "--signal"],
                         {"ssh": ssh(_NIMROD_GATED), "openclaw": _OPENCLAW})
    assert "claude router" in sent and "12.50" not in sent, sent
    # header present but body tampered (sha mismatch)
    tampered = cb.gate_header("verified", _NIMROD_GATED) + "\n" + _NIMROD_GATED.replace("12.50", "99.50")
    r, sent, _ = _run_sh("claude_router.sh", ["q", "sonnet", "--signal"],
                         {"ssh": ssh(tampered), "openclaw": _OPENCLAW})
    assert "99.50" not in sent and "claude router" in sent, sent
    # header says verified but exit code is not 0 / status-code mismatch
    for rc, st in ((3, "verified"), (0, "withheld"), (4, "verified")):
        body = _NIMROD_GATED if st == "verified" else wh
        r, sent, _ = _run_sh("claude_router.sh", ["q", "sonnet", "--signal"],
                             {"ssh": ssh(gated(st, body), rc), "openclaw": _OPENCLAW})
        assert "claude router" in sent, (rc, st, sent)
    # ungated raw AI text (old bridge copy / injected) is NOT relayed
    raw = "Expected profit next week: $40.00, you will make 123.45"
    r, sent, _ = _run_sh("claude_router.sh", ["q", "sonnet", "--signal"],
                         {"ssh": ssh(raw), "openclaw": _OPENCLAW})
    assert "Report withheld (claude router)" in sent, sent
    assert "40.00" not in sent and "123.45" not in sent, sent
    assert "40.00" not in r.stdout and "Report withheld (claude router)" in r.stdout  # never raw
    # marker spoofed in the middle (not last line), even with a valid header
    spoof = "✓ verified: x\nProfit: $99.99 guaranteed"
    r, sent, _ = _run_sh("claude_router.sh", ["q", "sonnet", "--signal"],
                         {"ssh": ssh(gated("verified", spoof)), "openclaw": _OPENCLAW})
    assert "99.99" not in sent and "claude router" in sent, sent
    # no --signal: nothing sent at all
    r, sent, _ = _run_sh("claude_router.sh", ["q"], {"ssh": ssh(raw), "openclaw": _OPENCLAW})
    assert sent == ""
    # ssh dead -> failure text, withheld on relay, never a figure
    r, sent, _ = _run_sh("claude_router.sh", ["q", "sonnet", "--signal"],
                         {"ssh": "exit 255\n", "openclaw": _OPENCLAW})
    assert "Report withheld (claude router)" in sent, sent


if __name__ == "__main__":
    test_executor_alert_verified_modeled_fenced_excluded()
    test_executor_alert_broker_failure_withholds_never_crashes()
    test_watchdog_text_only_no_money()
    test_cron_runner_sends_only_fixed_lines()
    test_claude_router_relays_only_gated_text()
    print("signal_senders_rest: PASS")
