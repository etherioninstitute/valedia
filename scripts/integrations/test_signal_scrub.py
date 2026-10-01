#!/usr/bin/env python3
"""Model text never reaches Signal: the AI-answer message is the code-built facts block
plus one fixed line (and the gate footer), whatever the model wrote.

Replays every string from the verifier harnesses (v9, v11, v12 a1-a4 + note_attack
cases, scrub_attack) and a generated corpus (600 lexicon / emoji / homoglyph /
foreign / slang combinations): the Signal body equals facts lines + fixed line +
footer exactly; the full answer is saved locally. Legacy cron_runner.sh sends fixed
lines with allowlisted labels only; watchdog free text is scrubbed; claude_router.sh
passes the question base64 (injection-safe) and never prints raw bridge output.
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


V11 = [
    "You are doing great. Your profit is 🔟 dollars, about twentyfive bucks, fivehundred total, "
    "it grew tenfold — xii percent, a tenner per bet, 💯 safe.",
    "🚀" * 400 + "aa", "ok answer",
]
SCRUB_ATTACK = [
    "profit 🔟 dollars", "💯 percent", "you made twentyfive dollars", "fivehundred dollars", "fortytwo",
    "grew tenfold", "a fiver", "a tenner", "a third of your cash", "doubled your money", "tripled",
    "xii dollars", "IIII", "XXXX", "Xii", "a couple hundred", "four score", "a gross",
    "cinco dólares", "zwanzig Euro", "пять долларов", "两百", "兩", "하나", "백 달러", "万", "零",
    "Ƽ3", "$ƼЗ", "$З00", "$lOO", "$1OO", "𝍷𝍸", "⚂", "١٢", "1​2", "1́", "‮21",
    "1e5", "1.5E+3", "½", "Ⅻ", "ⅻ", "x²", "ten-fold", "twenty‐five", "seventy five", "nine-teen",
    "Ⓐ", "⑩", "⒑", "🄁", "𝟗", "੧", "፲", "𑁒", "໑", "nil", "zero", "a dozen", "a pair of hundreds",
    "eighty-eight", "one hundred and ten", "One Thousand", "ONE", "o n e", "t-e-n", "sixty sixty",
    "sevens", "tens of dollars", "fifties", "the nineties", "first", "fifth", "tenth", "hundredth",
    "millionaire", "thousandfold", "billionth", "1️⃣2️⃣", "#️⃣", "🕐", "🀇", "🂡", "⓵", "❶", "➓",
    "𝟎", "٣", "௰", "൰", "〡", "〸", "ↂ", "𐄇", "Ⅽ", "ⅽ", "Ⅿ", "ⅾ", "MCM", "mcm", "XLII", "xlii",
    "C-note", "a grand", "a K", "five-K", "5k", "k", "a benjamin", "a nickel", "a dime", "a penny",
    "sixtyish", "fortyish", "thirties", "twelvemonth", "eleventh", "seventies",
    "ⅠⅡ", "١٠٠", "۱۰۰", "१००", "௧௦௦", "一百", "壹佰", "肆", "拾", "佰", "仟", "萬", "億", "兆",
    "twénty", "ꓔen", "cinco", "lOO", "SOO", "IO", "dos", "tres", "cuatro", "vingt", "zwei",
]
NOTE_ATTACK = [
    "You are ahead.", "Your holdings grew.", "The account grew.", "A nice haul today.", "You broke even.",
    "You are in the black.", "You are in the red.", "You made a small fortune.", "A handful of trades hit.",
    "Several trades settled in your favor.", "Dozens of trades settled.", "You will lose.",
    "Your holdings rose twice over.", "Your holdings rose by a googol.", "Your holdings rose by a G.",
    "Your holdings rose by F I V E.", "Your holdings rose by f-i-f-t-y.", "Ganaste cincuenta dolares.",
    "Du hast dreissig Euro gewonnen.", "Vous avez gagne quarante euros.", "Hai guadagnato cinquanta euro.",
    "Je hebt twintig euro verdiend.", "Kazandin elli lira.", "Ni lipata elfu moja.",
    "Anda untung lima puluh ribu rupiah.", "Ichi man en moketa.", "Your holdings rose by a monkey.",
    "Your holdings rose by a pony.", "A bundle came in today.", "You doubled up.", "You quadrupled.",
    "Burton, you crushed it today and came out well ahead.", "Everything looks great.",
    "You are ahead today. Your holdings grew. Ganaste cincuenta dolares. Your holdings rose by f-i-f-t-y.",
    "Your holdings look healthy and you are ahead of where you were. " * 40, "x" * 5000,
    "Burton, you came out well ahead this week and your holdings grew nicely. Every trade went "
    "your way, so you are in the black.",
]
_NUMW = ["twelve", "twentyfive", "fivehundred", "tenfold", "fiver", "tenner", "C-note", "xlii",
         "a grand", "a dozen", "half", "cinco", "zwei", "🔟", "💯", "twénty", "ꓔen", "$lOO", "١٢",
         "１２", "½", "Ⅻ", "十二", "a monkey", "a pony", "a googol", "ahead", "in the black"]
_MONEYW = ["dollars", "bucks", "USD", "profit", "cash", "", "in the green", "up", "net", "euros"]
_TMPL = ["You made {n} {m}.", "{n} {m}", "Your {m} is {n}.", "It grew {n}!", "{n}.", "about {n}"]


def _corpus(k=600, seed=7):
    import random
    rnd = random.Random(seed)
    return [rnd.choice(_TMPL).format(n=rnd.choice(_NUMW), m=rnd.choice(_MONEYW)) + " "
            + rnd.choice(["", "The slate looks calm.", "Nothing else to note."])
            for _ in range(k)]


def _all_strings():
    out = list(V9_STRINGS) + V11 + SCRUB_ATTACK + NOTE_ATTACK
    base = "/tmp/claude-0/-home-user-valedia/0f09ea09-13bd-5223-9832-3fd22d8ba606/scratchpad/"
    for d in ("v9/strings.txt", "v11/a1.txt", "v11/a2.txt", "v11/a3.txt",
              "v12/a1.txt", "v12/a2.txt", "v12/a3.txt", "v12/a4.txt"):
        if os.path.exists(base + d):  # replay the verifiers' own files too, when present
            for s in open(base + d).read().split("\n=====\n"):
                s = s.strip("\n").replace("\\u2028", " ").replace("\\u200b", "​")
                if s:
                    out.append(s)
    return list(dict.fromkeys(out + ADV2 + PREV + WATCHDOG + EXTRA))


def _numerals(text):
    return [c for c in unicodedata.normalize("NFKC", text) if unicodedata.numeric(c, None) is not None]


def _facts():
    return {"cash": (250.0, 250.0), "positions": {"invested": (40.0, 40.0), "n": (3, 3)},
            "ledger": {"total": (-5.0, -5.0), "wins": (2, 2), "losses": (3, 3)}, "why": {}}


FACT_LINES = ["Facts (code-generated; each source read twice):", "Cash (Kalshi, broker): $250.00",
              "Open-position cost (Kalshi, broker): $40.00", "Open positions (Kalshi, broker): 3",
              "Realized P&L (ledger, all venues, fee-incl.): −$5.00 (2W/3L)"]


class Sender(rest.Sender):
    pass


def test_signal_body_is_facts_plus_fixed_line_only():
    d = rest._fresh_dir()
    strings = _all_strings() + _corpus()
    assert len(strings) >= 800, len(strings)
    for i, s in enumerate(strings):
        send = Sender()
        now = NOW + timedelta(seconds=i)
        r = cb.gate_answer(f"q{i}", s, _facts(), send=True, send_fn=send, now=now)
        assert r["status"] == "verified", (s, r)
        path = os.path.join(d, "ai_answers", now.strftime("%Y%m%dT%H%M%S%fZ") + ".txt")
        want = FACT_LINES + [cb.AI_LINE + path, cb.FOOTER]
        assert send.msgs[0].split("\n") == want, (s, send.msgs[0])
        assert open(path).read() == f"Q: q{i}\n\n{s}\n"  # full answer kept locally only
    # sources failing -> still facts-only (figures omitted), never model text
    bad = {"why": {"cash": "unavailable", "positions": "STALE", "ledger": "re-read disagreed"}}
    send = Sender()
    cb.gate_answer("bad", "You made $124 profit", bad, send=True, send_fn=send, now=NOW)
    assert "124" not in send.msgs[0] and "profit" not in send.msgs[0].lower(), send.msgs
    assert "Cash (Kalshi, broker): omitted — unavailable" in send.msgs[0]


def test_bridge_stdout_router_mode_keeps_model_text_off_relay():
    import contextlib
    import io
    rest._fresh_dir()
    args = types.SimpleNamespace(question="q", no_data=True, model="sonnet", signal=False, router=True)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cb.run(args, ask=lambda *a, **k: "You made twelve dollars profit", facts_fn=_facts)
    lines = out.getvalue().rstrip("\n").split("\n")
    assert rc == 0 and lines[0] == cb.gate_header("verified", "\n".join(lines[1:])), lines
    assert "twelve" not in out.getvalue() and "twelve" in err.getvalue()
    args.router = False
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cb.run(args, ask=lambda *a, **k: "Local wording.", facts_fn=_facts)
    assert "[model answer, local only — not sent to Signal]\nLocal wording." in out.getvalue()


def test_cron_runner_label_allowlist_matches_install_cron():
    import re
    inst = open(os.path.join(rest.SCRIPTS, "install_cron.sh")).read()
    labels = sorted(set(re.findall(r'^[^#\n]*cron_runner_mac\.sh \S+ "([^"]+)"', inst, re.M)))
    cr = open(os.path.join(rest.SCRIPTS, "cron_runner.sh")).read()
    known = re.search(r'KNOWN_LABELS="([^"]*)"', cr).group(1).split("|")
    assert sorted(known) == labels, (sorted(known), labels)
    d0 = tempfile.mkdtemp(prefix="crlab_")
    ok = os.path.join(d0, "ok.py")
    open(ok, "w").write('print("PROFIT $999.99 sent")\n')
    for lab, want in (("SETTLEMENT", "Valedia job SETTLEMENT finished OK"),
                      ("P&L TRACKER", "Valedia job P&L TRACKER finished OK"),
                      ("PROFIT 999", "Valedia job finished OK"), ("", "Valedia job finished OK"),
                      ("SETTLEMENT; echo x", "Valedia job finished OK")):
        r, sent, _ = rest._run_sh("cron_runner.sh", [ok, lab], {"openclaw": rest._OPENCLAW})
        assert sent.split("--message ", 1)[1].strip() == want, (lab, sent)


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
        assert "'--model', 'sonnet'" in open(outq + ".argv").read()
        assert "Report withheld (claude router)" in sent, sent  # exit 4 -> never relayed
        assert r.stdout.strip() == "Report withheld (claude router): bridge output was not " \
            "signal_verify-gated. Unverified figures not sent; see data/signal/verify_log.jsonl.", r.stdout
    # a long multi-byte gated text is relayed whole (no byte cap in the router)
    big = "\n".join(FACT_LINES + [cb.AI_LINE + "/x/" + "é" * 900 + ".txt", cb.FOOTER])
    ssh2 = "cat <<'EOF'\n" + cb.gate_header("verified", big) + "\n" + big + "\nEOF\nexit 0\n"
    r, sent, _ = rest._run_sh("claude_router.sh", ["q", "sonnet", "--signal"],
                              {"ssh": ssh2, "openclaw": rest._OPENCLAW})
    assert "Realized P&L (ledger, all venues, fee-incl.): −USD 5.00 (2W/3L)" in sent and "é" * 900 in sent, sent


def test_cron_runner_only_fixed_lines_for_every_string():
    d0 = tempfile.mkdtemp(prefix="crfix_")
    for i, s in enumerate(_all_strings()[:120]):
        p = os.path.join(d0, f"s{i}.py")
        open(p, "w").write(f"import sys\nprint({s!r})\nsys.exit({i % 2})\n")
        r, sent, _ = rest._run_sh("cron_runner.sh", [p, "SETTLEMENT"], {"openclaw": rest._OPENCLAW})
        msg = sent.split("--message ", 1)[1].strip()
        assert msg == ("Valedia job SETTLEMENT finished OK" if i % 2 == 0
                       else "Valedia job SETTLEMENT FAILED (see cron log)"), (s, msg)


def test_watchdog_free_text_scrubbed_for_every_string():
    rest._fresh_dir()
    for s in _all_strings():
        send = Sender()
        watchdog.alert("WATCHDOG ALERT\n" + s, send_fn=send)
        assert send.msgs and not _numerals(send.msgs[0]), (s, send.msgs)


if __name__ == "__main__":
    test_signal_body_is_facts_plus_fixed_line_only()
    test_bridge_stdout_router_mode_keeps_model_text_off_relay()
    test_cron_runner_label_allowlist_matches_install_cron()
    test_router_question_injection_and_apostrophes()
    test_cron_runner_only_fixed_lines_for_every_string()
    test_watchdog_free_text_scrubbed_for_every_string()
    print("signal_scrub: PASS")
