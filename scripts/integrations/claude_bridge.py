#!/usr/bin/env python3
"""Valedia → Claude Bridge — ASI Intelligence via Claude Code
Routes questions from Valedia (OpenClaw/Signal) through Claude Code CLI.
Uses Burton's Claude Max subscription — ZERO API cost.

Usage:
    python3 claude_bridge.py "What is my expected profit next week?"
    python3 claude_bridge.py --question-b64 <base64> --router   (claude_router.sh)

MONEY ACCURACY BY CONSTRUCTION (allowlist, not blacklist):
  * Figures come ONLY from a code-built facts block: each figure read twice from
    its source (broker balance, broker positions, fee-incl. ledger), kept only if
    both reads parse, agree and are fresh; else "omitted — <reason>" (never $0.00).
    Signs/rounding via signal_verify.money (no "-$0.00"; negatives "−$5.00").
  * The model's text becomes an optional "AI note (wording only — no figures, see
    facts below)": only WHOLE sentences that are plain ASCII letters/punctuation,
    with no numeric-lexicon root, roman/leet token or money/direction word survive
    (ai_note). Nothing survives -> no note. Over-blocking is intended.
  * The facts block is computed first and never truncated; the note gets the rest
    of BUDGET_BYTES (UTF-8 bytes — the unit claude_router.sh relays). If the facts
    block alone exceeds the budget the answer is withheld.
--router prints "VALEDIA-GATE <status> <sha256>" first; exit 0 verified, 3 withheld,
4 other. claude_router.sh relays only on a matching header + exit code.

(c) GOATWORTHY Post LLC
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CLAUDE_BIN = os.path.expanduser("~/.npm-global/bin/claude")
MODELS_DIR = os.path.expanduser("~/.openclaw/workspace/models")
SCRIPTS_DIR = os.path.expanduser("~/.openclaw/workspace/scripts/integrations")
FOOTER = "✓ figures from broker/ledger only"
NOTE_LABEL = "AI note (wording only — no figures, see facts below):"
BUDGET_BYTES = 1400  # whole relayed text incl. footer, UTF-8 bytes (router sends as-is)
EXIT = {"verified": 0, "withheld": 3}  # anything else (error/dedupe) -> 4: router won't relay


# ── verified facts (code only) ─────────────────────────────────────────────────
def _broker_get():
    sys.path.insert(0, SCRIPTS_DIR)
    os.environ.setdefault('KALSHI_API_KEY_ID', '86a77487-629d-43d7-a5c0-0058bd8f899a')
    os.environ.setdefault('KALSHI_PRIVATE_KEY_PATH',
                          '/Users/burtonchatelain/.openclaw/kalshi_private_key.pem')
    from kalshi_auth import get
    return get


def _cents(get):
    import signal_verify as sv
    r = get('/portfolio/balance')
    return sv.num(r.get('balance')) if isinstance(r, dict) else None


def verified_facts(get=None, get_positions=None, ledger=None, now=None):
    """Each figure is read twice from its source and kept only if both reads parse,
    agree and are fresh; otherwise it is OMITTED with a reason (never shown as 0)."""
    import signal_verify as sv
    now = now or sv.utcnow()
    f = {"cash": None, "positions": None, "ledger": None, "why": {}}
    try:
        get = get or _broker_get()
        c1, c2 = _cents(get), _cents(get)
        if c1 is None or c2 is None:
            f["why"]["cash"] = "unavailable"
        elif abs(c1 - c2) > 0.5:
            f["why"]["cash"] = "re-read disagreed"
        else:
            f["cash"] = (c1 / 100, c2 / 100)
    except Exception as e:
        f["why"]["cash"] = f"unavailable ({type(e).__name__})"
    try:
        s1, s2 = sv.broker_snapshot(get_positions, now), sv.broker_snapshot(get_positions, now)
        fr, _ = sv.broker_fresh(s1)
        if abs(s1["invested"] - s2["invested"]) > sv.CENT or s1["n"] != s2["n"]:
            f["why"]["positions"] = "re-read disagreed"
        elif any((now - sv.parse_ts(x["ts"])).total_seconds() > x["max_age_s"] for x in fr):
            f["why"]["positions"] = "STALE"
        else:
            f["positions"] = {"invested": (s1["invested"], s2["invested"]), "n": (s1["n"], s2["n"])}
    except ValueError as e:
        f["why"]["positions"] = "incomplete" if "incomplete" in str(e) else "unavailable"
    except Exception as e:
        f["why"]["positions"] = f"unavailable ({type(e).__name__})"
    try:
        rd = ledger or sv.read_ledger
        recs1, recs2 = rd(), rd()
        r1, r2 = sv.ledger_realized(recs1), sv.ledger_realized(recs2)
        lts, future = sv.ledger_latest(recs2, now)
        open_pos = f["positions"]["n"][0] if f["positions"] else 1  # unknown -> assume open
        limit = sv.LEDGER_STALE_OPEN_S if open_pos else sv.LEDGER_STALE_ANY_S
        if r1["bad"] or r2["bad"]:
            f["why"]["ledger"] = "unavailable (unparseable rows)"
        elif abs(r1["total"] - r2["total"]) > sv.CENT or r1["rows"] != r2["rows"] \
                or r1["wins"] != r2["wins"]:
            f["why"]["ledger"] = "re-read disagreed"
        elif future:
            f["why"]["ledger"] = "STALE (future-dated rows, suspect)"
        elif not lts or (now - sv.parse_ts(lts)).total_seconds() > limit:
            f["why"]["ledger"] = "STALE (no recent live rows)"
        else:
            f["ledger"] = {"total": (r1["total"], r2["total"]), "wins": (r1["wins"], r2["wins"]),
                           "losses": (r1["losses"], r2["losses"])}
    except Exception as e:
        f["why"]["ledger"] = f"unavailable ({type(e).__name__})"
    return f


def facts_block(f):
    """(lines, claims) — the ONLY figures an AI answer can carry to Signal."""
    import signal_verify as sv
    lines, claims = ["Facts (code-generated; each source read twice):"], []
    why = f.get("why") or {}
    if f.get("cash"):
        c = f["cash"]
        lines.append(f"Cash (broker): {sv.money(c[0])}")
        claims.append(sv.claim("Cash", c[0], c[1], "broker re-read"))
    else:
        lines.append(f"Cash (broker): omitted — {why.get('cash', 'unavailable')}")
    if f.get("positions"):
        p = f["positions"]
        lines += [f"Open-position cost (broker): {sv.money(p['invested'][0])}",
                  f"Open positions (broker): {p['n'][0]}"]
        claims += [sv.claim("Open-position cost", p["invested"][0], p["invested"][1], "broker re-read"),
                   sv.claim("Open positions", p["n"][0], p["n"][1], "broker re-read", tol=0)]
    else:
        w = why.get("positions", "unavailable")
        lines += [f"Open-position cost (broker): omitted — {w}", f"Open positions (broker): omitted — {w}"]
    if f.get("ledger"):
        l = f["ledger"]
        t = l["total"][0]
        lines.append(f"Realized P&L (ledger, fee-incl.): {sv.money(t, signed=True)} "
                     f"({l['wins'][0]}W/{l['losses'][0]}L)")
        claims += [sv.claim("Realized P&L", t, l["total"][1], "ledger re-read"),
                   sv.claim("wins", l["wins"][0], l["wins"][1], "ledger re-read", tol=0)]
    else:
        lines.append(f"Realized P&L (ledger, fee-incl.): omitted — {why.get('ledger', 'unavailable')}")
    return lines, claims


# ── AI note: ALLOWLIST of figure-free sentences ────────────────────────────────
# A sentence survives only if, after NFKC, it is (a) ASCII letters/space/basic
# punctuation only (no digit, symbol or non-ASCII char), (b) has no numeric-lexicon
# root (joined, suffixed, ordinal, multiplicative, slang forms), (c) no roman-numeral
# or leet token, (d) no money/direction word. Over-blocking is intended.
_SAFE_CHARS = re.compile(r"[A-Za-z .,;:!?'\"()/-]*")
_LEXICON = re.compile(
    r"zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thir|fif|teen|twent"
    r"|ty\b|forty|hundred|thousand|million|billion|trillion|dozen|score|half|halv|quarter|third"
    r"|double|doubl|tripl|quadr|quint|fold|couple|pair|grand|fiver|tenner|c-note|nickel|dime"
    r"|penny|pennies|cent|buck|quid|\bk\b|\bm\b|\bbn\b|\bmm\b|nil\b|nought|naught|gross|lakh"
    r"|crore|single|first|second|benjamin|percent|\bpct\b|\bbps\b|illion"
    # common es/pt/fr/de/it number roots (ASCII spellings; accented ones are non-ASCII)
    r"|\buno\b|\buna\b|\bdos\b|\btres\b|cuatro|cinco|\bseis\b|siete|\bocho\b|nueve|\bdiez\b"
    r"|veinte|cien|\bmil\b|\bdeux\b|trois|quatre|\bcinq\b|\bsept\b|\bhuit\b|\bneuf\b|\bdix\b"
    r"|vingt|mille|\beins\b|zwei|\bdrei\b|\bvier\b|\bfunf\b|sechs|sieben|\bacht\b|\bneun\b"
    r"|\bzehn\b|zwanzig|hundert|tausend|\bdue\b|\btre\b|quattro|\bsei\b|sette|\botto\b|\bnove\b"
    r"|dieci|venti|cento|\bmila\b|\bdez\b|\bvinte\b|\bcem\b")
_MONEY_DIR = re.compile(
    r"\b(?:profit|loss|lost|los[ei]ng|gain|earn|made|make|making|up\b|down|return|pnl|p&l|p/l"
    r"|balanc|cash|invest|equit|payout|worth|valu|money|dollar|usd|pric|cost|fee|win|won\b"
    r"|green|red\b|rich|broke|great|bank|fund|capital|pay|paid|spen[dt]|stake|exposure|revenue"
    r"|income|interest|roi\b|yield|odds|bet\b|bets\b|wager|account|portfolio|total|sum\b|amount|net\b)")
_ROMAN_TOKEN = re.compile(r"\b[ivxlcdm]{2,}\b", re.I)


def _leet(tok):
    return bool(re.search(r"[a-z][A-Z]", tok)) or (
        len(tok) > 1 and tok.isupper() and set(tok) <= set("OISZBGLT"))


def _sentence_ok(s):
    low = s.lower()
    return bool(s.strip()) and _SAFE_CHARS.fullmatch(s) is not None \
        and not _LEXICON.search(low) and not _MONEY_DIR.search(low) \
        and not _ROMAN_TOKEN.search(s) and not any(_leet(t) for t in re.findall(r"[A-Za-z]+", s))


def ai_note(text, budget_bytes):
    """(note, kept, dropped): whole figure-free sentences within budget_bytes (UTF-8).
    Belt and braces: any numeral / number word left -> drop the whole note."""
    import signal_verify as sv
    t = unicodedata.normalize("NFKC", str(text or ""))
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+|[\r\n  \x85]+", t) if x.strip()]
    kept, used = [], 0
    for x in sents:
        need = len(x.encode()) + (1 if kept else 0)
        if not _sentence_ok(x) or used + need > budget_bytes:
            continue
        kept.append(x)
        used += need
    note = " ".join(kept)
    if note and (sv.scrub_numbers(note)[1] or sv.has_numerals(note)):
        return "", 0, len(sents)
    return note, len(kept), len(sents) - len(kept)


def get_portfolio_context(facts=None, models_dir=None, **kw):
    """Context for the model (it may read numbers; its own text never carries any)."""
    facts = facts if facts is not None else verified_facts(**kw)
    models_dir = models_dir or MODELS_DIR
    parts = facts_block(facts)[0]
    try:  # sports log: order intent — model context only
        log = json.load(open(os.path.join(models_dir, "sports_engine_log.json")))
        pending = [b for b in log['bets'] if not b.get('settled') and b.get('status') == 'EXECUTED']
        parts.append(f"[UNVERIFIED sports log] active bets: {len(pending)}")
        for b in pending:
            parts.append(f"[UNVERIFIED sports log] {b.get('league', '')} {b.get('fav_title', '')[:35]}"
                         f" exp:{str(b.get('exp', ''))[:10]}")
    except Exception as e:
        parts.append(f"[UNVERIFIED] sports log unavailable ({type(e).__name__})")
    try:
        from pnl_tracker import load_pause_registry
        paused = load_pause_registry().get("paused") or []
        parts.append("[INFO] PAUSED (pause registry): " + (", ".join(
            f"{p.get('strategy')} until {str(p.get('until') or '')[:10]}" for p in paused) or "none"))
    except Exception as e:
        parts.append(f"[INFO] PAUSED: registry unavailable ({type(e).__name__})")
    parts.append(f"[INFO] TIMESTAMP: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    parts.append("[RULE] Write plain English with NO numbers, amounts or money words. "
                 "The system appends the verified figures itself; sentences with numbers "
                 "or money words are dropped.")
    return "\n".join(parts)


def ask_claude(question, with_data=False, model="sonnet", context=None):
    """Send a question to Claude via Claude Code CLI. Zero API cost."""
    if with_data:
        portfolio = context if context is not None else get_portfolio_context()
        full_prompt = f"LIVE PORTFOLIO DATA:\n{portfolio}\n\nQUESTION: {question}"
    else:
        full_prompt = question
    model_flag = "claude-opus-4-20250514" if model == "opus" else "claude-sonnet-4-20250514"
    cmd = [CLAUDE_BIN, "--print", "--model", model_flag, "--max-turns", "1", full_prompt]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            env={**os.environ, "PATH": os.path.expanduser("~/.npm-global/bin") + ":" + os.environ.get("PATH", "")}
        )
        response = result.stdout.strip()
        if not response and result.stderr:
            response = f"Error: {result.stderr.strip()[:200]}"
        return response
    except subprocess.TimeoutExpired:
        return "Error: Claude Code timed out (120s)"
    except Exception as e:
        return f"Error: {str(e)}"


# ── gate ─────────────────────────────────────────────────────────────────────
def gate_answer(question, response, facts, send=False, send_fn=None, now=None,
                budget_bytes=BUDGET_BYTES):
    """Facts block first (never truncated), then an allowlisted AI note in the bytes
    left. Facts alone over budget -> withheld. Never raises."""
    import hashlib
    import signal_verify as sv
    lines, claims = facts_block(facts or {"why": {}})
    facts_txt = "\n".join(lines)
    fixed = _wire_len(facts_txt + "\n" + FOOTER)
    problems = []
    if fixed > budget_bytes:
        problems.append({"code": "facts block exceeds message budget", "detail": fixed})
    room = budget_bytes - fixed - _wire_len(NOTE_LABEL + "\n\n")
    note, kept, dropped = ai_note(response, max(0, room))
    body = f"{NOTE_LABEL}\n{note}\n\n{facts_txt}" if note else facts_txt
    key = hashlib.sha1(f"{question}|{response}".encode()).hexdigest()
    return sv.deliver("AI answer", body, claims, send_fn=send_fn, now=now, dedupe_key=key,
                      dedupe_window_s=600, transport=send, problems=problems,
                      verified_line=FOOTER, info={"note_kept": kept, "note_dropped": dropped})


def _wire_len(s):
    """UTF-8 bytes as relayed (claude_router.sh rewrites "$" as "USD ")."""
    return len(s.replace("$", "USD ").encode())


def gate_header(status, text):
    """Structural gate line for claude_router.sh: status + sha256 of the exact text."""
    import hashlib
    return f"VALEDIA-GATE {status} {hashlib.sha256(text.encode()).hexdigest()}"


def run(args, ask=None, facts_fn=None):
    """Print the gated text (router: header first). Exit 0 verified, 3 withheld, 4 other."""
    use_data = not args.no_data
    try:
        facts = (facts_fn or verified_facts)()
        context = get_portfolio_context(facts=facts) if use_data else ""
        response = (ask or ask_claude)(args.question, with_data=use_data, model=args.model,
                                       context=context)
    except Exception as e:
        facts, response = {"why": {}}, f"Error: {type(e).__name__}"
    if not response or response.startswith("Error"):
        print("Claude bridge error (nothing relayed).")  # no header -> never relayed
        return 4
    res = gate_answer(args.question, response, facts, send=args.signal)
    text = res["text"].strip("\n")
    status = res["status"] if res["status"] in EXIT else "error"
    if getattr(args, "router", False):
        print(gate_header(status, text))
    print(text)
    return EXIT.get(status, 4)


def main():
    parser = argparse.ArgumentParser(description="Valedia Claude Bridge")
    parser.add_argument("question", nargs="?", default="What is my current portfolio status?")
    parser.add_argument("--question-b64", default=None,
                        help="base64 (UTF-8) question; used by claude_router.sh (no shell quoting)")
    parser.add_argument("--with-data", action="store_true", default=True,
                        help="Include live portfolio data (default: True)")
    parser.add_argument("--no-data", action="store_true", help="Skip portfolio data injection")
    parser.add_argument("--model", choices=["sonnet", "opus"], default="sonnet")
    parser.add_argument("--signal", action="store_true", help="Send response via Signal to Burton")
    parser.add_argument("--router", action="store_true",
                        help="Prefix a VALEDIA-GATE header for claude_router.sh")
    args = parser.parse_args()
    if args.question_b64 is not None:
        try:
            args.question = base64.b64decode(args.question_b64, validate=True).decode("utf-8")
        except Exception:
            print("Claude bridge error: bad --question-b64 (nothing relayed).")
            sys.exit(4)
    sys.exit(run(args))


if __name__ == "__main__":
    main()
