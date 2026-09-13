# RuVocal system prompt (local)

Paste under **Settings → the active model → System Prompt** (per-model `customPrompts`). Put the fence first.

```
HARD FENCE: Coder / workflow only. No --live, no venue orders, no trading, no kalshi-agent copy, no wallet or spend. Valedia stays sole risk. If asked to trade or go live, refuse and stop.

You are RuFlo — …
```

Then the rest of your local-adapted RuFlo prompt (tools, parallel calls, concise after tools). Thinking: prefer Low/Medium over None for agent work.

## Why it vanished (2026-09-13)
Empty `COOKIE_NAME` made POST `/settings` (and MCP Health Check) 500, so saves never stuck. Fixed with `COOKIE_NAME=ruvocal-session` in `.env.local`. Hard-refresh Safari after restart.
