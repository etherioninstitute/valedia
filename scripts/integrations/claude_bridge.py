#!/usr/bin/env python3
"""Valedia → Claude Bridge — ASI Intelligence via Claude Code
Routes questions from Valedia (OpenClaw/Signal) through Claude Code CLI.
Uses Burton's Claude Max subscription — ZERO API cost.

Usage:
    python3 claude_bridge.py "What is my expected profit next week?"
    python3 claude_bridge.py --question-b64 <base64> --router   (claude_router.sh)

MODEL TEXT NEVER REACHES SIGNAL. Free model wording cannot be verified, so the
Signal message is ONLY the code-built facts block (each figure read twice from its
source — broker balance, broker positions, fee-incl. ledger — kept only if both reads
parse, agree and are fresh, else "omitted — <reason>", never $0.00; formatting via
signal_verify.money) plus one fixed line:
  "AI answer not sent to Signal (model wording cannot be verified) — full text saved: <path>"
The full answer is saved to data/signal/ai_answers/<ts>.txt (gitignored) and printed
locally (stdout without --router; stderr with --router, which the router drops).
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
AI_LINE = "AI answer not sent to Signal (model wording cannot be verified) — full text saved: "
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
    now_in, now = now, now or sv.utcnow()
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
        elif fr and (lambda ev: ev["problems"] or ev["stale"])(sv.evaluate("", (), fr, now)):
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
        # ONE shared selection (ledger_view) -> same rows / same omission text as 11pm + Profit
        lnow = now_in or sv.utcnow()  # taken after the ledger reads
        r1, r2 = sv.ledger_realized(recs1, lnow), sv.ledger_realized(recs2, lnow)
        lts, future = sv.ledger_latest(recs2, lnow)
        open_pos = f["positions"]["n"][0] if f["positions"] else 1  # unknown -> assume open
        limit = sv.LEDGER_STALE_OPEN_S if open_pos else sv.LEDGER_STALE_ANY_S
        if r1["reason"] or r2["reason"]:
            f["why"]["ledger"] = r1["reason"] or r2["reason"]
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
        if f["positions"]:  # open Kalshi entries in the ledger vs broker open positions
            f["open_mismatch"] = (sv.ledger_open_kalshi(recs2), f["positions"]["n"][0])
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
        lines.append(f"Cash (Kalshi, broker): {sv.money(c[0])}")
        claims.append(sv.claim("Cash", c[0], c[1], "broker re-read"))
    else:
        lines.append(f"Cash (Kalshi, broker): omitted — {why.get('cash', 'unavailable')}")
    if f.get("positions"):
        p = f["positions"]
        lines += [f"Open-position cost (Kalshi, broker): {sv.money(p['invested'][0])}",
                  f"Open positions (Kalshi, broker): {p['n'][0]}"]
        claims += [sv.claim("Open-position cost", p["invested"][0], p["invested"][1], "broker re-read"),
                   sv.claim("Open positions", p["n"][0], p["n"][1], "broker re-read", tol=0)]
    else:
        w = why.get("positions", "unavailable")
        lines += [f"Open-position cost (Kalshi, broker): omitted — {w}",
                  f"Open positions (Kalshi, broker): omitted — {w}"]
    if f.get("ledger"):
        l = f["ledger"]
        t = l["total"][0]
        lines.append(f"Realized P&L (all-time, ledger, all venues, fee-incl.): {sv.money(t, signed=True)} "
                     f"({l['wins'][0]}W/{l['losses'][0]}L)")
        claims += [sv.claim("Realized P&L", t, l["total"][1], "ledger re-read"),
                   sv.claim("wins", l["wins"][0], l["wins"][1], "ledger re-read", tol=0)]
    else:
        lines.append(f"Realized P&L (all-time, ledger, all venues, fee-incl.): omitted — "
                     f"{why.get('ledger', 'unavailable')}")
    mm = f.get("open_mismatch")
    if mm and mm[0] != mm[1]:
        lines.append(f"⚠ ledger/broker open-position mismatch ({mm[0]} vs {mm[1]}) — not reconciled")
        claims.append(sv.claim("ledger open entries", mm[0], mm[0], "ledger re-read", tol=0))
    return lines, claims


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
    parts.append("[RULE] Your answer is shown to Burton locally only; Signal gets the "
                 "verified facts above, never your wording.")
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
def save_answer(question, response, now=None):
    """Full model answer -> data/signal/ai_answers/<ts>.txt (local only). Path or None."""
    import signal_verify as sv
    now = now or sv.utcnow()
    try:
        d = os.path.join(sv.signal_dir(), "ai_answers")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, now.strftime("%Y%m%dT%H%M%S%fZ") + ".txt")
        with open(path, "w") as f:
            f.write(f"Q: {question}\n\n{response}\n")
        return path
    except Exception:
        return None


def gate_answer(question, response, facts, send=False, send_fn=None, now=None):
    """Signal text = code-built facts block + one fixed line (+ footer). No model text.
    The facts' figures are verified (two reads agree) by signal_verify.deliver."""
    import hashlib
    import signal_verify as sv
    lines, claims = facts_block(facts or {"why": {}})
    path = save_answer(question, response, now)
    body = "\n".join(lines + [AI_LINE + (path or "(save failed, see bridge stdout)")])
    key = hashlib.sha1(f"{question}|{response}".encode()).hexdigest()
    return sv.deliver("AI answer", body, claims, send_fn=send_fn, now=now, dedupe_key=key,
                      dedupe_window_s=600, transport=send, verified_line=FOOTER,
                      info={"answer_path": path})


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
        print(f"[model answer, local only]\n{response}", file=sys.stderr)
    else:
        print(text)
        print(f"\n[model answer, local only — not sent to Signal]\n{response}")
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
