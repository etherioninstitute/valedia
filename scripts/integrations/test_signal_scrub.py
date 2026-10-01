#!/usr/bin/env python3
"""Money accuracy by construction: the AI note is an ALLOWLIST of figure-free sentences.

Replays every string from the verifier harnesses (v9 strings.txt, adv2/prev/snd, v11
a1/a2/a3 + scrub_attack) and a generated corpus (600 random lexicon / emoji /
homoglyph / foreign / leet combinations): the AI note never carries a numeral, a
numeric-lexicon form or a money/direction word; the code-built facts block stays
intact (also under multi-byte-heavy answers, byte budget). Legacy cron_runner.sh
sends fixed lines with allowlisted labels only; watchdog free text is scrubbed;
claude_router.sh passes the question base64 (injection-safe) and never prints raw
bridge output. Facts robustness / formatting / pagination: test_signal_facts.py.
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
# every piece that would carry a figure; none may appear in a surviving AI note
PIECES = ["🔟", "💯", "twentyfive", "fivehundred", "tenfold", "fiver", "tenner", "c-note", "xii",
          "xlii", "mcm", "twénty", "ꓔen", "lOO", "$", "cinco", "dos", "tres", "zwei", "vingt",
          "dozen", "grand", "nickel", "dime", "penny", "half", "quarter", "couple", "score",
          "gross", "million", "thousand", "hundred", "seventy", "forty", "fortytwo", "nil",
          "benjamin", "1", "5", "0", "twelve", "eleven", "thirteen"]
_NUMW = ["twelve", "twentyfive", "fivehundred", "tenfold", "fiver", "tenner", "C-note", "xlii",
         "XII", "a grand", "a dozen", "half", "a quarter", "a couple", "four score", "a gross",
         "cinco", "dos", "tres", "zwei", "vingt", "🔟", "💯", "1️⃣", "twénty", "ꓔen", "$lOO", "lOO",
         "SOO", "1OO", "١٢", "１２", "¹²", "½", "Ⅻ", "十二", "nil", "forty", "seventy-two",
         "thirteen", "eleven", "millionish", "a benjamin", "a nickel", "a dime", "a penny",
         "fortytwo", "twelvefold", "doubled", "tripled"]
_MONEYW = ["dollars", "bucks", "USD", "profit", "cash", "", "", "in the green", "up", "net",
           "returns", "earned", "worth", "value", "equity", "payout", "", "quid", "cents"]
_TMPL = ["You made {n} {m}.", "{n} {m}", "Your {m} is {n}.", "It grew {n}!", "{n}.",
         "Things look {n} {m} today.", "We are {n}", "Result {n}", "{m} {n}", "about {n}"]


def _corpus(k=600, seed=7):
    import random
    rnd = random.Random(seed)
    return [rnd.choice(_TMPL).format(n=rnd.choice(_NUMW), m=rnd.choice(_MONEYW)) + " "
            + rnd.choice(["", "The slate looks calm.", "Nothing else to note."])
            for _ in range(k)]


def _all_strings():
    out = list(V9_STRINGS) + V11 + SCRUB_ATTACK
    for d in ("v9/strings.txt", "v11/a1.txt", "v11/a2.txt", "v11/a3.txt"):
        p = "/tmp/claude-0/-home-user-valedia/0f09ea09-13bd-5223-9832-3fd22d8ba606/scratchpad/" + d
        if os.path.exists(p):  # replay the verifiers' own files too, when present
            for s in open(p).read().split("\n=====\n"):
                s = s.strip("\n").replace("\\u2028", " ").replace("\\u200b", "​")
                if s:
                    out.append(s)
    return list(dict.fromkeys(out + ADV2 + PREV + WATCHDOG + EXTRA))


def _numerals(text):
    return [c for c in unicodedata.normalize("NFKC", text) if unicodedata.numeric(c, None) is not None]


def _facts():
    return {"cash": (250.0, 250.0), "positions": {"invested": (40.0, 40.0), "n": (3, 3)},
            "ledger": {"total": (-5.0, -5.0), "wins": (2, 2), "losses": (3, 3)}, "why": {}}


FACT_LINES = ["Facts (code-generated; each source read twice):", "Cash (broker): $250.00",
              "Open-position cost (broker): $40.00", "Open positions (broker): 3",
              "Realized P&L (ledger, fee-incl.): −$5.00 (2W/3L)"]


class Sender(rest.Sender):
    pass


def _assert_clean_note(note, src):
    assert not _numerals(note) and sv.scrub_numbers(note)[1] == 0, (src, note)
    assert all(32 <= ord(c) < 127 for c in note), (src, note)
    assert not cb._MONEY_DIR.search(note.lower()) and not cb._LEXICON.search(note.lower()), (src, note)
    import re
    for p in PIECES:
        if p.isalpha() and p != p.lower() and p != p.upper():
            assert p not in note, (src, p, note)  # leet / mixed case: exact
        elif p.isalpha():
            assert not re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", note, re.I), (src, p, note)
        else:
            assert p not in note, (src, p, note)


def test_ai_note_allowlist_every_string_and_corpus():
    rest._fresh_dir()
    strings = _all_strings() + _corpus()
    assert len(strings) >= 800, len(strings)
    kept_any = 0
    for i, s in enumerate(strings):
        for text in (s, "The slate looks calm tonight. " + s, s + "\nNothing else to note."):
            note, kept, _ = cb.ai_note(text, 1000)
            _assert_clean_note(note, text)
            kept_any += bool(kept)
        send = Sender()
        r = cb.gate_answer(f"q{i}", s, _facts(), send=True, send_fn=send, now=NOW)
        assert r["status"] == "verified", (s, r)
        t = send.msgs[0]
        head, facts = t.split("Facts (code-generated", 1)
        _assert_clean_note(head.replace(cb.NOTE_LABEL, "").strip("\n"), s)
        assert ["Facts (code-generated" + facts.splitlines()[0]] + facts.splitlines()[1:5] == FACT_LINES, t
        assert t.endswith("✓ figures from broker/ledger only"), t
    assert kept_any > 100  # benign wording still gets through


def test_legit_note_and_multibyte_budget():
    rest._fresh_dir()
    send = Sender()
    cb.gate_answer("ok", "The slate looks calm tonight. Nothing else to note.", _facts(),
                   send=True, send_fn=send, now=NOW)
    assert send.msgs[0].startswith(cb.NOTE_LABEL + "\nThe slate looks calm tonight. Nothing else to note.")
    for heavy in ("🚀" * 5000, "你好" * 3000 + ". The slate looks calm.",
                  ("Calm night ahead. " * 400), "é" * 9000):
        send = Sender()
        r = cb.gate_answer("h" + heavy[:9], heavy, _facts(), send=True, send_fn=send, now=NOW)
        t = send.msgs[0]
        assert r["status"] == "verified" and all(l in t for l in FACT_LINES), t
        assert cb._wire_len(t) <= cb.BUDGET_BYTES, cb._wire_len(t)
    send = Sender()  # facts block alone over budget -> withheld, never truncated
    r = cb.gate_answer("tiny", "ok", _facts(), send=True, send_fn=send, now=NOW, budget_bytes=60)
    assert r["status"] == "withheld" and "facts block exceeds message budget" in send.msgs[0]


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
    big = cb.NOTE_LABEL + "\n" + ("Calm. " * 300).strip() + "\n\n" + "\n".join(FACT_LINES) + \
        "\n✓ figures from broker/ledger only"
    ssh2 = "cat <<'EOF'\n" + cb.gate_header("verified", big) + "\n" + big + "\nEOF\nexit 0\n"
    r, sent, _ = rest._run_sh("claude_router.sh", ["q", "sonnet", "--signal"],
                              {"ssh": ssh2, "openclaw": rest._OPENCLAW})
    assert "Realized P&L (ledger, fee-incl.): −USD 5.00 (2W/3L)" in sent and "✓ figures" in sent, sent


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
    test_ai_note_allowlist_every_string_and_corpus()
    test_legit_note_and_multibyte_budget()
    test_cron_runner_label_allowlist_matches_install_cron()
    test_router_question_injection_and_apostrophes()
    test_cron_runner_only_fixed_lines_for_every_string()
    test_watchdog_free_text_scrubbed_for_every_string()
    print("signal_scrub: PASS")
