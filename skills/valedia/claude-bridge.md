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

# Send response directly to Signal
python3 ~/.openclaw/workspace/scripts/integrations/claude_bridge.py "question" --signal

# Without portfolio data injection
python3 ~/.openclaw/workspace/scripts/integrations/claude_bridge.py "question" --no-data
```

## How It Works
1. Pulls live data: Kalshi balance, active bets, league stats, intelligence state
2. Injects data into Claude's context as structured text
3. Calls Claude Code CLI (--print mode, non-interactive)
4. Returns Claude's response (optionally sends via Signal)

## Notes
- Claude Code uses Burton's Claude Max subscription (claude.ai auth)
- Zero API cost — included in subscription
- Sonnet for quick queries, Opus for deep analysis
- --with-data is default (always includes live portfolio)
- Response truncated to 1400 chars for Signal delivery
