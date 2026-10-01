# Claude Bridge Skill — Valedia ASI
# Routes complex questions through Claude Sonnet/Opus via Claude Code CLI
# Uses Burton's Claude Max subscription — zero API cost

## Trigger
When Valedia receives a question that requires:
- Portfolio analysis or profit projections
- Strategy recommendations
- Complex reasoning about trading performance
- Any question she can't answer well with Venice/Ollama

## Usage (from NIMROD)
```bash
# Default: Claude Sonnet with live portfolio data
python3 ~/.openclaw/workspace/scripts/integrations/claude_bridge.py "question here"

# Claude Opus for deep analysis
python3 ~/.openclaw/workspace/scripts/integrations/claude_bridge.py "question" --model opus

# Anything for Signal: ONLY via the gated router (VPS) or --router / --signal
bash ~/.openclaw/workspace/scripts/claude_router.sh "question" sonnet --signal
python3 ~/.openclaw/workspace/scripts/integrations/claude_bridge.py "question" --signal

# Without portfolio data injection
python3 ~/.openclaw/workspace/scripts/integrations/claude_bridge.py "question" --no-data
```

## How It Works
1. Pulls live data: Kalshi balance, active bets, league stats, intelligence state
2. Injects data into Claude's context as structured text
3. Calls Claude Code CLI (--print mode, non-interactive)
4. Prints Claude's response LOCALLY (full text also saved to data/signal/ai_answers/).
5. Signal (router / --router / --signal) gets ONLY the code-built facts block
   (Cash / Open-position cost / Open positions — Kalshi, broker, each read twice;
   Realized P&L — ledger, all venues, fee-incl.) plus the fixed line
   "AI answer not sent to Signal (model wording cannot be verified) — full text saved: <path>".
   The model's own wording never goes to Signal.

## Notes
- Claude Code uses Burton's Claude Max subscription (claude.ai auth)
- Zero API cost — included in subscription
- Sonnet for quick queries, Opus for deep analysis
- --with-data is default (always includes live portfolio)
- OpenClaw: for anything sent to Signal use ONLY claude_router.sh or claude_bridge.py
  --router/--signal. Never paste raw bridge / bridge-server output into Signal.
- claude_bridge_server.py (HTTP :7878) is LOCAL ONLY — not for Signal. Its JSON carries
  "signal_safe_text" (the gated facts-only message) for any Signal use.

## TODO(Burton)
OpenClaw's own chat replies to Burton on Signal are model-written and are NOT covered
by signal_verify. Only Valedia's code-built reports (P&L, Profit, Invested, 7am brief,
ops alerts) and the gated claude_router.sh / --router path are verified. Revisit:
e.g. route OpenClaw replies through the same facts-only gate.
