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

def get_portfolio_context():
    """Pull live trading data to inject into Claude's context."""
    context_parts = []
    
    # 1. Account balance
    try:
        sys.path.insert(0, SCRIPTS_DIR)
        os.environ.setdefault('KALSHI_API_KEY_ID', '86a77487-629d-43d7-a5c0-0058bd8f899a')
        os.environ.setdefault('KALSHI_PRIVATE_KEY_PATH',
                              '/Users/burtonchatelain/.openclaw/kalshi_private_key.pem')
        from kalshi_auth import get
        r = get('/portfolio/balance')
        cash = r.get('balance', 0) / 100
        deployed = r.get('portfolio_value', 0) / 100
        context_parts.append(f"ACCOUNT (broker): cash=${cash:.2f} market value=${deployed:.2f} "
                             f"total=${cash+deployed:.2f}")
    except Exception as e:
        context_parts.append(f"ACCOUNT: error fetching ({e})")

    # 2. Realized P&L — fee-true ledger only (the sports-log net is not fee-true)
    try:
        import signal_verify as sv
        real = sv.ledger_realized(sv.read_ledger())
        context_parts.append(
            f"REALIZED (ledger, fee-incl.): ${real['total']:+.2f} "
            f"({real['wins']}W/{real['losses']}L, {real['rows']} settlements)")
    except Exception as e:
        context_parts.append(f"REALIZED: ledger unavailable ({e})")

    # 3. Sports engine log — open bets (cost from order intent; payout is modeled)
    try:
        log = json.load(open(os.path.join(MODELS_DIR, "sports_engine_log.json")))
        pending = [b for b in log['bets']
                   if not b.get('settled') and b.get('status') == 'EXECUTED']
        if pending:
            context_parts.append(f"ACTIVE BETS (sports log): {len(pending)}")
            total_cost = sum(b.get('cost', 0) for b in pending)
            total_profit = sum(b.get('profit_if_win', 0) for b in pending)
            context_parts.append(f"  Cost: ${total_cost:.2f}  If-win payout (modeled, not profit): "
                                 f"${total_profit:.2f}")
            for b in pending:
                context_parts.append(
                    f"  {b['league']:6} @{b['fav_price']:.2f} x{b['contracts']} "
                    f"cost:${b['cost']:.2f} if-win:${b['profit_if_win']:.2f} "
                    f"exp:{b.get('exp','')[:10]} | {b['fav_title'][:35]}"
                )
    except Exception as e:
        context_parts.append(f"SPORTS LOG: error ({e})")

    # 4. League breakdown (sports log, not fee-true)
    try:
        log = json.load(open(os.path.join(MODELS_DIR, "sports_engine_log.json")))
        settled = [b for b in log['bets']
                   if b.get('settled') and b.get('status') == 'EXECUTED']
        from collections import defaultdict
        by_lg = defaultdict(lambda: {'w': 0, 'l': 0, 'pnl': 0})
        for b in settled:
            lg = b.get('league', '?')
            if b.get('won'): by_lg[lg]['w'] += 1
            else: by_lg[lg]['l'] += 1
            by_lg[lg]['pnl'] += b.get('pnl', 0)
        context_parts.append("LEAGUE BREAKDOWN (sports log, not fee-true):")
        for lg in sorted(by_lg):
            d = by_lg[lg]
            total = d['w'] + d['l']
            wr = d['w'] / total * 100 if total else 0
            context_parts.append(f"  {lg}: {d['w']}W/{d['l']}L ({wr:.0f}%) ${d['pnl']:+.2f}")
    except:
        pass
    
    # 5. Intelligence
    try:
        mf = json.load(open(os.path.join(MODELS_DIR, "mirofish_signal.json")))
        context_parts.append(
            f"INTELLIGENCE: F&G={mf.get('fear_greed','?')} "
            f"bias={mf.get('overall_bias','?')} "
            f"source={mf.get('source','?')}"
        )
    except:
        pass
    
    # 6. Paused strategies — from the live pause registry (was a hardcoded guess)
    try:
        from pnl_tracker import load_pause_registry
        paused = load_pause_registry().get("paused") or []
        context_parts.append("PAUSED (pause registry): " + (", ".join(
            f"{p.get('strategy')} until {str(p.get('until') or '')[:10]}" for p in paused) or "none"))
    except Exception as e:
        context_parts.append(f"PAUSED: registry unavailable ({e})")
    context_parts.append(f"TIMESTAMP: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    
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
    args = parser.parse_args()
    
    use_data = not args.no_data
    context = get_portfolio_context() if use_data else ""
    response = ask_claude(args.question, with_data=use_data, model=args.model, context=context)

    if not response or response.startswith("Error"):
        print(response)  # not sent
        return
    # stdout is what claude_router.sh relays to Signal -> print the gated text only.
    print(gate_answer(args.question, response, context, send=args.signal)["text"])


def gate_answer(question, response, context, send=False, send_fn=None, now=None):
    """Every 2-decimal figure in the AI answer must appear in the broker/ledger
    context it was given; otherwise the answer is withheld (logged in full)."""
    import hashlib
    import signal_verify as sv
    allowed = [float(x.replace(",", "")) for x in sv.FIG_RE.findall(context or "")]
    body = f"AI answer (Claude, not a ledger report):\n{response.strip()}"
    footer = ["figures match broker/ledger context" if sv.FIG_RE.search(response)
              else "no figures", sv.hhmm(now or sv.utcnow())]
    key = hashlib.sha1(f"{question}|{response}".encode()).hexdigest()
    return sv.deliver("AI answer", body, allowed=allowed, footer=footer, send_fn=send_fn,
                      now=now, dedupe_key=key, dedupe_window_s=600, transport=send)

if __name__ == "__main__":
    main()
