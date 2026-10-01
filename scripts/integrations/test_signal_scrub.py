#!/usr/bin/env python3
"""Figures by construction: free text carries NO numbers; figures come only from code.

Replays every string from the re-verifier harness (v9 strings.txt, adv2.py, prev.py,
snd.py watchdog cases) plus Unicode/number-word variants through the AI-answer gate,
the watchdog and the legacy cron_runner.sh: every numeral must become [n] or the
message must be a fixed line. The code-built facts block carries sign + label from
fixtures; disagreeing / stale / failing sources -> figures omitted. claude_router.sh
passes the question base64 as one argv word (injection + apostrophes inert).
Fakes only: no network, no keys, no orders, real relay booby-trapped.
Run: python3 test_signal_scrub.py
"""
import base64
import io
import contextlib
import os
import sys
import tempfile
import types
import unicodedata
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_signal_senders_rest as rest  # relay booby-traps + temp signal dir
import signal_verify as sv
import claude_bridge as cb
import watchdog

NOW = datetime(2026, 10, 1, 23, 0, tzinfo=timezone.utc)
V9_STRINGS = [
    'You made twelve dollars profit',
    'Profit: １２.５０',
    'You earned 12 bucks',
    'Up €12 today',
    '12 USD in the account',
    'Return +12%',
    "You're up 12 today",
    'Gained 3.5',
    'Profit:\n12.5',
    'Profit:\n12',
    '**Realized P&L**\n- 12',
    '💰 1,234',
    '📈 +12 this week',
    'Net (12.50)',
    'Net (12)',
    'Your cash is 1.2e3',
    'Your cash is 250.00 thousand dollars',
    'Balance 1 234 dollars',
    "Balance 1'234",
    'Cash 1.2k',
    'PROFIT 12',
    'Gewinn: 12',
    'Ganancia de 12 euros',
    'You made a profit of +5.00',
    'Profit: 5.00',
    'Your profit is the same as your cash balance, 250.00',
    'You have 1,234 in your account',
    'Your Kalshi account holds 1,234',
    'Total: 1,234',
    'Won 1,234 on NBA',
    'Portfolio: 1.2k',
    'Fees: 12',
    'Cost: 77',
    'Spent 45 today',
    'Bankroll 300',
    'Exposure 300',
    'Stake 45',
    'Revenue 12',
    '0.5 BTC',
    '12 USDT',
    '12 EUR',
    '¥1200',
    '＄12',
    'Prоfit 12',
    'P&amp;L 12',
    'pro\u200bfit 12',
    'Profit\u2028 12',
    'Profit ¹²',
    'Profit ١٢',
    'Down 3 today',
    'You have 7 open positions',
    'Win rate 90 percent',
    'Payout 124',
    "If every bet wins you'd get 124",
    'Cash: 250',
    'Cash: $250.00',
    'Invested $40.00 across 3 open positions',
    'Realized P&L: -$5.00',
    "You're down 5.00",
    'Lost $5.00',
    'cash (250)',
    'Cash 250.00 USD',
    'EV +3.2',
    'Your 7 positions are worth 40',
    'Net -1.2e1',
    'Cash 250 · invested 40',
    '| Metric | Value |\n|---|---|\n| Profit | 12 |',
    'Profit 12.5 (approx)',
    'You lost twelve',
    'Half a grand in profit',
    '1,234 dollars',
    'USD 300',
    '+0.125',
    'Cash two hundred fifty',
    'Invested 40 USD and cash 250 USD',
]
ADV2 = ["Realized P&L: +$5.00", "You're up 5.00 overall", "Nice — a $5.00 profit so far!",
        "Cash $40.00; invested $250.00", "You have $250.00 invested", "Your cash is $40.00",
        "Profit: 5", "You made 5 dollars", "Loss of 250.00 cash",
        "Your Kalshi cash is $250.00 (that is 1,000 contracts)",
        "You realized $5.00 and lost $5.00", "Profit $5.00\nTotal 1,234",
        "Cash $250.00\nWithdrawable: 9,999"]
PREV = ["You made $124 profit", "Your account is 1.2k", "Sports cost 77.7", "Modeled payout $124.00",
        "Payout if all win: $124.00", "You stand to collect $124.00", "Cost so far: $77.70",
        "Upside of $124.00 if they hit"]
WATCHDOG = ["Cron log is 130 min stale (>2h)", "Balance\n1,234", "up 12 today",
            "1,234 dollars\nUSD 300\n+0.125"]
EXTRA = ["Profit ½", "Profit 十二", "Profit Ⅻ", "twеlve dollars", "zwölf: 12", "1e3", "12k",
         "١٢٣", "\U0001D7D9\U0001D7DA", "１２３", "a dozen bets", "twenty-five bucks",
         "Profit XII", "minus 1 234", "⁦Profit 12⁩", "௧௨ rupees"]


def _all_strings():
    out = list(V9_STRINGS)
    harness = ("/tmp/claude-0/-home-user-valedia/0f09ea09-13bd-5223-9832-3fd22d8ba606/"
               "scratchpad/v9/strings.txt")
    if os.path.exists(harness):  # replay the re-verifier's own file too, when present
        for s in open(harness).read().split("\n=====\n"):
            s = s.strip("\n").replace("\\u2028", "\u2028").replace("\\u200b", "\u200b")
            if s:
                out.append(s)
    return list(dict.fromkeys(out + ADV2 + PREV + WATCHDOG + EXTRA))


def _numerals(text):
    return [c for c in unicodedata.normalize("NFKC", text) if unicodedata.numeric(c, None) is not None]


def _facts(cash=(250.0, 250.0), inv=(40.0, 40.0), n=(3, 3), real=(-5.0, -5.0)):
    return {"broker": {"cash": cash, "invested": inv, "n": n},
            "ledger": {"total": real, "wins": (2, 2), "losses": (3, 3)}, "why": {}}


class Sender(rest.Sender):
    pass


def test_every_replayed_string_scrubbed():
    rest._fresh_dir()
    strings = _all_strings()
    assert len(strings) >= 100, len(strings)
    for s in strings:
        clean, n = sv.scrub_numbers(s)
        assert not _numerals(clean), (s, clean)
        assert n > 0 or not _numerals(s), (s, clean)
        send = Sender()
        r = cb.gate_answer("q:" + s, s, _facts(), send=True, send_fn=send, now=NOW)
        assert r["status"] == "verified", (s, r)
        t = send.msgs[0]
        answer = t.split("\nFacts (code-generated", 1)[0]
        assert not _numerals(answer), (s, answer)  # model text: zero figures
        assert t.endswith("✓ figures from broker/ledger only"), t
        assert "Realized P&L (ledger, fee-incl.): -$5.00 (2W/3L)" in t, t
        figs = [l for l in t.splitlines() if _numerals(l)]
        assert all(l.startswith(("Cash (broker): ", "Open-position cost (broker): ",
                                 "Open positions (broker): ", "Realized P&L (ledger"))
                   for l in figs), (s, figs)


def test_facts_block_from_fixtures_sign_and_labels():
    bal = iter([{"balance": 25000}, {"balance": 25000}])
    pos = lambda: {"market_positions": [  # noqa: E731
        {"ticker": "A", "position_fp": "2", "market_exposure_dollars": "30.00"},
        {"ticker": "B", "position_fp": "-1", "market_exposure_dollars": "10.00"},
        {"ticker": "C", "position_fp": "0", "market_exposure_dollars": "0"}]}
    ts = (NOW - timedelta(hours=1)).isoformat()
    rows = [{"type": "entry", "id": "e1", "source": "live", "logged_at": ts},
            {"type": "settlement", "entry_id": "e1", "realized_pnl_net": -5.0, "won": False,
             "logged_at": ts}]
    f = cb.verified_facts(get=lambda p: next(bal), get_positions=pos, ledger=lambda: rows, now=NOW)
    lines, claims = cb.facts_block(f)
    assert lines[1:] == ["Cash (broker): $250.00", "Open-position cost (broker): $40.00",
                         "Open positions (broker): 2", "Realized P&L (ledger, fee-incl.): -$5.00 (0W/1L)"], lines
    rows[1]["realized_pnl_net"] = 7.5
    f = cb.verified_facts(get=lambda p: {"balance": 0}, get_positions=pos, ledger=lambda: rows, now=NOW)
    assert "Realized P&L (ledger, fee-incl.): +$7.50 (0W/1L)" in cb.facts_block(f)[0]


def test_disagreeing_stale_failing_sources_omit_figures():
    pos = lambda: {"market_positions": [{"ticker": "A", "position_fp": "1",  # noqa: E731
                                         "market_exposure_dollars": "3.00"}]}
    ts = (NOW - timedelta(hours=1)).isoformat()
    rows = [{"type": "entry", "id": "e1", "source": "live", "logged_at": ts},
            {"type": "settlement", "entry_id": "e1", "realized_pnl_net": 2.0, "won": True, "logged_at": ts}]
    bal = iter([{"balance": 1000}, {"balance": 1100}])
    calls = []

    def ledger():
        calls.append(1)
        return rows if len(calls) == 1 else rows + [dict(rows[1], entry_id="e1", realized_pnl_net=9.0)]
    f = cb.verified_facts(get=lambda p: next(bal), get_positions=pos, ledger=ledger, now=NOW)
    lines = cb.facts_block(f)[0]
    assert lines[1:] == ["Broker figures omitted — broker re-read disagreed",
                         "Realized P&L omitted — ledger re-read disagreed"], lines
    old = [dict(r, logged_at=(NOW - timedelta(days=30)).isoformat()) for r in rows]
    stale_pos = lambda: dict(pos(), ts=(NOW - timedelta(hours=2)).isoformat())  # noqa: E731
    f = cb.verified_facts(get=lambda p: {"balance": 1}, get_positions=stale_pos,
                          ledger=lambda: old, now=NOW)
    assert cb.facts_block(f)[0][1:] == ["Broker figures omitted — broker data STALE",
                                        "Realized P&L omitted — ledger STALE (no recent rows)"]

    def boom(*a, **k):
        raise ConnectionError("down 12.50")
    f = cb.verified_facts(get=boom, get_positions=pos, ledger=boom, now=NOW)
    lines = cb.facts_block(f)[0]
    assert lines[1:] == ["Broker figures omitted — broker unavailable (ConnectionError)",
                         "Realized P&L omitted — ledger unavailable (ConnectionError)"], lines
    rest._fresh_dir()
    send = Sender()
    r = cb.gate_answer("q", "All fine.", f, send=True, send_fn=send, now=NOW)
    assert r["status"] == "verified" and not _numerals(send.msgs[0]), send.msgs
    # a facts dict whose two reads disagree can never be sent with figures
    send = Sender()
    r = cb.gate_answer("q2", "ok", _facts(cash=(250.0, 251.0)), send=True, send_fn=send, now=NOW)
    assert r["status"] == "withheld" and not _numerals(send.msgs[0]), send.msgs


def _args(q):
    return types.SimpleNamespace(question=q, no_data=True, model="sonnet", signal=False, router=True)


def test_bridge_header_and_exit_codes():
    rest._fresh_dir()
    for answer, rc, status in (("You made $124 profit", 0, "verified"), ("Error: timeout", 4, None)):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = cb.run(_args(answer), ask=lambda *a, **k: answer, facts_fn=_facts)
        out = buf.getvalue().rstrip("\n").split("\n")
        assert got == rc, (answer, got, out)
        if status:
            assert out[0] == cb.gate_header(status, "\n".join(out[1:])), out
            assert "124" not in buf.getvalue() and out[-1] == cb.FOOTER
        else:
            assert not out[0].startswith("VALEDIA-GATE"), out
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        got = cb.run(_args("x"), ask=lambda *a, **k: "ok", facts_fn=lambda: _facts(n=(3, 4)))
    assert got == 3 and buf.getvalue().split("\n")[1].startswith("Report withheld (AI answer)")


_FAKE_BRIDGE = r'''import base64, os, sys
a = sys.argv[1:]
q = base64.b64decode(a[a.index("--question-b64") + 1], validate=True).decode("utf-8")
open(os.environ["OUTQ"], "w").write(q)
open(os.environ["OUTQ"] + ".argv", "w").write(repr(a))
sys.exit(4)
'''


def test_router_question_injection_and_apostrophes():
    tmp = tempfile.mkdtemp(prefix="rtinj_")
    home = os.path.join(tmp, "home")
    integ = os.path.join(home, ".openclaw", "workspace", "scripts", "integrations")
    os.makedirs(integ)
    open(os.path.join(integ, "claude_bridge.py"), "w").write(_FAKE_BRIDGE)
    mark = os.path.join(tmp, "PWNED")
    ssh = 'CMD="${@: -1}"\nHOME="$FAKEHOME" bash -c "$CMD"\nexit $?\n'
    payloads = ["x'; touch %s; echo '" % mark, "$(touch %s)" % mark, "`touch %s`" % mark,
                "what's my P&L? it's \"fine\" isn't it", "a\nb; touch %s" % mark,
                "'\"'\"'; touch %s #" % mark, "\\'; touch %s; \\'" % mark, "ünïcødé — 💰 'q'"]
    for q in payloads:
        outq = os.path.join(tmp, "q.txt")
        r, sent, _ = rest._run_sh("claude_router.sh", [q, "sonnet; touch %s" % mark, "--signal"],
                                  {"ssh": ssh, "openclaw": rest._OPENCLAW},
                                  {"FAKEHOME": home, "OUTQ": outq})
        assert not os.path.exists(mark), (q, r.stdout, r.stderr)
        assert open(outq).read() == q, (q, open(outq).read())
        argv = open(outq + ".argv").read()
        assert "'--model', 'sonnet'" in argv, argv  # bad MODEL whitelisted back to sonnet
        assert "Report withheld (claude router)" in sent, sent  # exit 4 -> never relayed
    src = open(os.path.join(rest.SCRIPTS, "claude_router.sh")).read()
    assert "${QUESTION" not in src.split("RAW=$(", 1)[1].split("RC=$?", 1)[0]


def test_cron_runner_only_fixed_lines_for_every_string():
    d0 = tempfile.mkdtemp(prefix="crfix_")
    for i, s in enumerate(_all_strings()):
        p = os.path.join(d0, f"s{i}.py")
        open(p, "w").write(f"import sys\nprint({s!r})\nsys.exit({i % 2})\n")
        r, sent, _ = rest._run_sh("cron_runner.sh", [p, "LBL"], {"openclaw": rest._OPENCLAW})
        msg = sent.split("--message ", 1)[1].strip()
        assert msg == ("Valedia job LBL finished OK" if i % 2 == 0
                       else "Valedia job LBL FAILED (see cron log)"), (s, msg)


def test_watchdog_free_text_scrubbed_for_every_string():
    rest._fresh_dir()
    for s in _all_strings():
        send = Sender()
        watchdog.alert("WATCHDOG ALERT\n" + s, send_fn=send)
        assert send.msgs and not _numerals(send.msgs[0]), (s, send.msgs)


if __name__ == "__main__":
    test_every_replayed_string_scrubbed()
    test_facts_block_from_fixtures_sign_and_labels()
    test_disagreeing_stale_failing_sources_omit_figures()
    test_bridge_header_and_exit_codes()
    test_router_question_injection_and_apostrophes()
    test_cron_runner_only_fixed_lines_for_every_string()
    test_watchdog_free_text_scrubbed_for_every_string()
    print("signal_scrub: PASS")
