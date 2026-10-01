#!/usr/bin/env python3
"""Valedia → Claude Bridge — ASI Intelligence via Claude Code
Routes questions from Valedia (OpenClaw/Signal) through Claude Code CLI.
Uses Burton's Claude Max subscription — ZERO API cost.

Usage:
    python3 claude_bridge.py "What is my expected profit next week?"
    python3 claude_bridge.py --with-data "Analyze my trading performance"

The --with-data flag injects live portfolio data into the prompt so Claude
has actual numbers to work with.

(c) GOATWORTHY Post LLC
"""
import subprocess, json, os, sys, argparse
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CLAUDE_BIN = os.path.expanduser("~/.npm-global/bin/claude")
MODELS_DIR = os.path.expanduser("~/.openclaw/workspace/models")
SCRIPTS_DIR = os.path.expanduser("~/.openclaw/workspace/scripts/integrations")

def get_portfolio_context(get=None, get_positions=None, ledger=None, models_dir=None):
    """Live data for Claude's context. Each line is tagged; ONLY figures on
    [VERIFIED-BROKER] / [VERIFIED-REALIZED] lines (two independent reads that
    agree) may appear in a Signal answer. [MODELED]/[UNVERIFIED] never can."""
    import signal_verify as sv
    models_dir = models_dir or MODELS_DIR
    context_parts = []

    # 1. Broker: balance + open-position cost, read twice, must agree
    try:
        if get is None:
            sys.path.insert(0, SCRIPTS_DIR)
            os.environ.setdefault('KALSHI_API_KEY_ID', '86a77487-629d-43d7-a5c0-0058bd8f899a')
            os.environ.setdefault('KALSHI_PRIVATE_KEY_PATH',
                                  '/Users/burtonchatelain/.openclaw/kalshi_private_key.pem')
            from kalshi_auth import get
        c1, s1 = get('/portfolio/balance').get('balance', 0) / 100, sv.broker_snapshot(get_positions)
        c2, s2 = get('/portfolio/balance').get('balance', 0) / 100, sv.broker_snapshot(get_positions)
        if abs(c1 - c2) <= sv.CENT and abs(s1['invested'] - s2['invested']) <= sv.CENT \
                and s1['n'] == s2['n']:
            context_parts.append(
                f"[VERIFIED-BROKER] Kalshi cash ${c1:.2f}; invested (open-position cost) "
                f"${s1['invested']:.2f}; {s1['n']} open positions")
        else:
            context_parts.append("[UNVERIFIED] ACCOUNT: broker re-read disagreed — no broker figures")
    except Exception as e:
        context_parts.append(f"[UNVERIFIED] ACCOUNT: broker unavailable ({type(e).__name__})")

    # 2. Realized P&L — fee-true ledger only, read twice, must agree
    try:
        rd = ledger or sv.read_ledger
        r1, r2 = sv.ledger_realized(rd()), sv.ledger_realized(rd())
        if abs(r1['total'] - r2['total']) <= sv.CENT and r1['rows'] == r2['rows']:
            sign = "-" if r1['total'] < 0 else "+"
            context_parts.append(
                f"[VERIFIED-REALIZED] Realized P&L (ledger, fee-incl.): {sign}${abs(r1['total']):.2f}"
                f" ({r1['wins']}W/{r1['losses']}L, {r1['rows']} settlements)")
        else:
            context_parts.append("[UNVERIFIED] REALIZED: ledger re-read disagreed — no figures")
    except Exception as e:
        context_parts.append(f"[UNVERIFIED] REALIZED: ledger unavailable ({type(e).__name__})")

    # 3. Sports engine log — open bets (order intent; payout is modeled, never profit)
    try:
        log = json.load(open(os.path.join(models_dir, "sports_engine_log.json")))
        pending = [b for b in log['bets']
                   if not b.get('settled') and b.get('status') == 'EXECUTED']
        if pending:
            context_parts.append(f"[UNVERIFIED sports log] ACTIVE BETS: {len(pending)}")
            total_cost = sum(b.get('cost', 0) for b in pending)
            total_profit = sum(b.get('profit_if_win', 0) for b in pending)
            context_parts.append(f"[UNVERIFIED sports log] Cost: ${total_cost:.2f}")
            context_parts.append(f"[MODELED — never quote as profit] If-win payout: "
                                 f"${total_profit:.2f}")
            for b in pending:
                context_parts.append(
                    f"[UNVERIFIED sports log] {b['league']:6} @{b['fav_price']:.2f} "
                    f"x{b['contracts']} cost:${b['cost']:.2f} [MODELED] if-win:"
                    f"${b['profit_if_win']:.2f} exp:{b.get('exp','')[:10]} | {b['fav_title'][:35]}"
                )
    except Exception as e:
        context_parts.append(f"[UNVERIFIED] SPORTS LOG: error ({type(e).__name__})")

    # 4. League breakdown (sports log, not fee-true)
    try:
        log = json.load(open(os.path.join(models_dir, "sports_engine_log.json")))
        settled = [b for b in log['bets']
                   if b.get('settled') and b.get('status') == 'EXECUTED']
        from collections import defaultdict
        by_lg = defaultdict(lambda: {'w': 0, 'l': 0, 'pnl': 0})
        for b in settled:
            lg = b.get('league', '?')
            if b.get('won'): by_lg[lg]['w'] += 1
            else: by_lg[lg]['l'] += 1
            by_lg[lg]['pnl'] += b.get('pnl', 0)
        context_parts.append("[UNVERIFIED sports log, not fee-true] LEAGUE BREAKDOWN:")
        for lg in sorted(by_lg):
            d = by_lg[lg]
            total = d['w'] + d['l']
            wr = d['w'] / total * 100 if total else 0
            context_parts.append(f"[UNVERIFIED sports log] {lg}: {d['w']}W/{d['l']}L ({wr:.0f}%) "
                                 f"${d['pnl']:+.2f}")
    except Exception:
        pass

    # 5. Intelligence
    try:
        mf = json.load(open(os.path.join(models_dir, "mirofish_signal.json")))
        context_parts.append(
            f"[INFO] INTELLIGENCE: F&G={mf.get('fear_greed','?')} "
            f"bias={mf.get('overall_bias','?')} "
            f"source={mf.get('source','?')}"
        )
    except:
        pass
    
    # 6. Paused strategies — from the live pause registry (was a hardcoded guess)
    try:
        from pnl_tracker import load_pause_registry
        paused = load_pause_registry().get("paused") or []
        context_parts.append("[INFO] PAUSED (pause registry): " + (", ".join(
            f"{p.get('strategy')} until {str(p.get('until') or '')[:10]}" for p in paused) or "none"))
    except Exception as e:
        context_parts.append(f"[INFO] PAUSED: registry unavailable ({type(e).__name__})")
    context_parts.append(f"[INFO] TIMESTAMP: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    context_parts.append("[RULE] Quote money ONLY from [VERIFIED-...] lines, exactly as written; "
                         "never present [MODELED] or [UNVERIFIED] figures as profit.")
    return "\n".join(context_parts)

def ask_claude(question, with_data=False, model="sonnet", context=None):
    """Send a question to Claude via Claude Code CLI. Zero API cost."""

    system_prompt = (
        "You are Valedia, an autonomous trading intelligence. "
        "Answer concisely with numbers first. "
        "You manage sports prediction market bets on Kalshi. "
        "Be direct and quantitative."
    )

    if with_data:
        portfolio = context if context is not None else get_portfolio_context()
        full_prompt = f"LIVE PORTFOLIO DATA:\n{portfolio}\n\nQUESTION: {question}"
    else:
        full_prompt = question
    
    # Claude Code CLI: --print for non-interactive, --model for model selection
    # Uses Claude Max subscription, not API credits
    model_flag = "claude-sonnet-4-20250514"
    if model == "opus":
        model_flag = "claude-opus-4-20250514"
    
    cmd = [
        CLAUDE_BIN, "--print",
        "--model", model_flag,
        "--max-turns", "1",
        full_prompt
    ]
    
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

def main():
    parser = argparse.ArgumentParser(description="Valedia Claude Bridge")
    parser.add_argument("question", nargs="?", default="What is my current portfolio status?")
    parser.add_argument("--with-data", action="store_true", default=True,
                        help="Include live portfolio data (default: True)")
    parser.add_argument("--no-data", action="store_true",
                        help="Skip portfolio data injection")
    parser.add_argument("--model", choices=["sonnet", "opus"], default="sonnet",
                        help="Claude model to use (default: sonnet)")
    parser.add_argument("--signal", action="store_true",
                        help="Send response via Signal to Burton")
    parser.add_argument("--router", action="store_true",
                        help="Prefix a VALEDIA-GATE header for claude_router.sh")
    args = parser.parse_args()
    sys.exit(run(args))


EXIT = {"verified": 0, "withheld": 3}  # anything else (error/dedupe) -> 4: router won't relay


def gate_header(status, text):
    """Structural gate line for claude_router.sh: status + sha256 of the exact text."""
    import hashlib
    return f"VALEDIA-GATE {status} {hashlib.sha256(text.encode()).hexdigest()}"


def run(args, ask=None, context_fn=None):
    """Print the gated text (router: header first). Exit 0 verified, 3 withheld, 4 other."""
    use_data = not args.no_data
    try:
        context = (context_fn or get_portfolio_context)() if use_data else ""
        response = (ask or ask_claude)(args.question, with_data=use_data, model=args.model,
                                       context=context)
    except Exception as e:
        context, response = "", f"Error: {type(e).__name__}"
    if not response or response.startswith("Error"):
        print(response)  # never relayed (no header, exit 4)
        return 4
    res = gate_answer(args.question, response, context, send=args.signal)
    text = res["text"].strip("\n")
    status = res["status"] if res["status"] in EXIT else "error"
    if getattr(args, "router", False):
        print(gate_header(status, text))
    print(text)
    return EXIT.get(status, 4)


def gate_answer(question, response, context, send=False, send_fn=None, now=None):
    """Any number on an answer line with a money keyword/marker must EQUAL a $-figure
    on a [VERIFIED-BROKER]/[VERIFIED-REALIZED] context line (modeled never counts);
    otherwise the answer is withheld (logged in full)."""
    import hashlib
    import signal_verify as sv
    allowed = sv.verified_figures(context)
    body = f"AI answer (Claude, not a ledger report):\n{response.strip()}"
    footer = ["money figures match VERIFIED broker/ledger context" if sv.has_money(response)
              else "no money figures", sv.hhmm(now or sv.utcnow())]
    bad = sv.ai_unverified(response, context)
    problems = [{"code": "money figure(s) not on a VERIFIED line of matching kind",
                 "detail": f"unverified: {bad}"}] if bad else []
    key = hashlib.sha1(f"{question}|{response}".encode()).hexdigest()
    return sv.deliver("AI answer", body, allowed=allowed, footer=footer, send_fn=send_fn,
                      now=now, dedupe_key=key, dedupe_window_s=600, transport=send,
                      problems=problems)

if __name__ == "__main__":
    main()
