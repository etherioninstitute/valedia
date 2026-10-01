#!/usr/bin/env python3
"""Valedia → Claude Bridge — ASI Intelligence via Claude Code
Routes questions from Valedia (OpenClaw/Signal) through Claude Code CLI.
Uses Burton's Claude Max subscription — ZERO API cost.

Usage:
    python3 claude_bridge.py "What is my expected profit next week?"
    python3 claude_bridge.py --question-b64 <base64> --router   (claude_router.sh)

FIGURES BY CONSTRUCTION: the model's answer may carry NO figures. Before anything
leaves, every numeral / number word in it (any Unicode digit, superscript,
fraction, number words, k/m/b forms; after NFKC and dropping zero-width chars) is
replaced by "[n]" (signal_verify.scrub_numbers). A CODE-GENERATED facts block is
then appended from verified sources only — broker read twice + ledger read twice,
agreeing and fresh — each figure with its sign and label. Disagreeing / stale /
unavailable sources -> the block says so and omits those figures. Modeled figures
never appear. Footer: "✓ figures from broker/ledger only".
--router prints "VALEDIA-GATE <status> <sha256>" first; exit 0 verified, 3 withheld,
4 other. claude_router.sh relays only on a matching header + exit code.

(c) GOATWORTHY Post LLC
"""
import argparse
import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CLAUDE_BIN = os.path.expanduser("~/.npm-global/bin/claude")
MODELS_DIR = os.path.expanduser("~/.openclaw/workspace/models")
SCRIPTS_DIR = os.path.expanduser("~/.openclaw/workspace/scripts/integrations")
FOOTER = "✓ figures from broker/ledger only"
EXIT = {"verified": 0, "withheld": 3}  # anything else (error/dedupe) -> 4: router won't relay


# ── verified facts (code only) ─────────────────────────────────────────────────
def verified_facts(get=None, get_positions=None, ledger=None, now=None):
    """Broker (cash, open-position cost, open count) and ledger (realized, fee-incl.)
    each read twice. A side is kept only if both reads agree and it is fresh."""
    import signal_verify as sv
    now = now or sv.utcnow()
    f = {"broker": None, "ledger": None, "why": {}}
    try:
        if get is None:
            sys.path.insert(0, SCRIPTS_DIR)
            os.environ.setdefault('KALSHI_API_KEY_ID', '86a77487-629d-43d7-a5c0-0058bd8f899a')
            os.environ.setdefault('KALSHI_PRIVATE_KEY_PATH',
                                  '/Users/burtonchatelain/.openclaw/kalshi_private_key.pem')
            from kalshi_auth import get
        c1, s1 = sv._f(get('/portfolio/balance').get('balance')) / 100, sv.broker_snapshot(get_positions, now)
        c2, s2 = sv._f(get('/portfolio/balance').get('balance')) / 100, sv.broker_snapshot(get_positions, now)
        fr, _ = sv.broker_fresh(s1)
        stale = [x for x in fr if (now - sv.parse_ts(x["ts"])).total_seconds() > x["max_age_s"]]
        if abs(c1 - c2) > sv.CENT or abs(s1["invested"] - s2["invested"]) > sv.CENT or s1["n"] != s2["n"]:
            f["why"]["broker"] = "broker re-read disagreed"
        elif stale:
            f["why"]["broker"] = "broker data STALE"
        else:
            f["broker"] = {"cash": (c1, c2), "invested": (s1["invested"], s2["invested"]),
                           "n": (s1["n"], s2["n"])}
    except Exception as e:
        f["why"]["broker"] = f"broker unavailable ({type(e).__name__})"
    try:
        rd = ledger or sv.read_ledger
        recs1, recs2 = rd(), rd()
        r1, r2 = sv.ledger_realized(recs1), sv.ledger_realized(recs2)
        lts = sv.ledger_latest(recs2)
        open_pos = f["broker"]["n"][0] if f["broker"] else 1
        limit = sv.LEDGER_STALE_OPEN_S if open_pos else sv.LEDGER_STALE_ANY_S
        if abs(r1["total"] - r2["total"]) > sv.CENT or r1["rows"] != r2["rows"] \
                or r1["wins"] != r2["wins"]:
            f["why"]["ledger"] = "ledger re-read disagreed"
        elif not lts or (now - sv.parse_ts(lts)).total_seconds() > limit:
            f["why"]["ledger"] = "ledger STALE (no recent rows)"
        else:
            f["ledger"] = {"total": (r1["total"], r2["total"]), "wins": (r1["wins"], r2["wins"]),
                           "losses": (r1["losses"], r2["losses"])}
    except Exception as e:
        f["why"]["ledger"] = f"ledger unavailable ({type(e).__name__})"
    return f


def facts_block(f):
    """(lines, claims) — the ONLY figures an AI answer can carry to Signal."""
    import signal_verify as sv
    lines, claims = ["Facts (code-generated; broker read twice, ledger read twice):"], []
    b, l = f.get("broker"), f.get("ledger")
    if b:
        lines += [f"Cash (broker): ${b['cash'][0]:,.2f}",
                  f"Open-position cost (broker): ${b['invested'][0]:,.2f}",
                  f"Open positions (broker): {b['n'][0]}"]
        claims += [sv.claim("Cash", b["cash"][0], b["cash"][1], "broker re-read"),
                   sv.claim("Open-position cost", b["invested"][0], b["invested"][1], "broker re-read"),
                   sv.claim("Open positions", b["n"][0], b["n"][1], "broker re-read", tol=0)]
    else:
        lines.append(f"Broker figures omitted — {f['why'].get('broker', 'unavailable')}")
    if l:
        t = l["total"][0]
        lines.append(f"Realized P&L (ledger, fee-incl.): {'-' if t < 0 else '+'}${abs(t):,.2f} "
                     f"({l['wins'][0]}W/{l['losses'][0]}L)")
        claims += [sv.claim("Realized P&L", t, l["total"][1], "ledger re-read"),
                   sv.claim("wins", l["wins"][0], l["wins"][1], "ledger re-read", tol=0)]
    else:
        lines.append(f"Realized P&L omitted — {f['why'].get('ledger', 'unavailable')}")
    return lines, claims


def get_portfolio_context(facts=None, models_dir=None, **kw):
    """Context for the model (it may read numbers; it may not write any)."""
    facts = facts if facts is not None else verified_facts(**kw)
    models_dir = models_dir or MODELS_DIR
    parts = facts_block(facts)[0]
    try:  # sports log: order intent / modeled payout — model context only
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
    parts.append("[RULE] Do NOT write any numbers or amounts in your answer (digits or words). "
                 "The system appends the verified figures itself; numbers you write become [n].")
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
def gate_answer(question, response, facts, send=False, send_fn=None, now=None):
    """Scrub every numeral from the model text, append the code-built facts block,
    verify the block's figures (two reads agree) and deliver/log. Never raises."""
    import hashlib
    import signal_verify as sv
    clean, n = sv.scrub_numbers(response)
    problems = [{"code": "numeral survived scrub", "detail": repr(clean)[:300]}] \
        if sv.has_numerals(clean) else []
    lines, claims = facts_block(facts or {"why": {}})
    body = "\n".join(["AI answer (Claude; any numbers it wrote are shown as [n]):",
                      clean.strip()[:900], ""] + lines)
    key = hashlib.sha1(f"{question}|{response}".encode()).hexdigest()
    return sv.deliver("AI answer", body, claims, send_fn=send_fn, now=now, dedupe_key=key,
                      dedupe_window_s=600, transport=send, problems=problems,
                      verified_line=FOOTER, info={"scrubbed": n})


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
