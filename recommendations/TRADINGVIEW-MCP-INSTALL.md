# TradingView MCP Install (Mac Studio M1)

**Date:** 2026-09-27 PT  
**Host:** Burtons-Mac-Studio-M1  
**Switch:** tradesdontlie / MemPalace `tradingview-mcp` → **LewisWJackson/tradingview-mcp-jackson**  
**Fence:** Chart / Pine Script only — **NO live venue orders**. Davidd stays `DAVIDD_LIVE=0` / paper on `localhost:8787`. No wallet, no Cloudflare, no live venue.

## Clone
`/Users/burtonchatelain/tradingview-mcp-jackson` (`~/tradingview-mcp-jackson`)

- **MCP entry:** `src/server.js` (Jackson has no `src/server/mcp.js`; SDK import `@modelcontextprotocol/sdk/server/mcp.js` is unrelated)
- Node `/usr/local/bin/node` + `/usr/local/bin/npm install` (ok, 95 packages)
- CLI health: `TV_CDP_PORT=9333 node src/cli/index.js status`
- `rules.json` ← `rules.example.json` (placeholders only — Burton fills; upstream demo scalper with market buy/sell was replaced)
- **Local patch:** `src/connection.js`, `src/core/tab.js`, `src/core/health.js` read `TV_CDP_PORT` (upstream hardcodes 9222; Chrome holds 9222 here)

## CDP port (live)
Default **9333** via `TV_CDP_PORT` so Google Chrome can keep remote-debug on **9222**.

```bash
# Prefer LaunchServices so the app stays alive:
osascript -e 'tell application "TradingView" to quit'   # soft-quit if needed
open -a TradingView --args --remote-debugging-port=9333
# or: tradingview-cdp   # ~/bin helper (direct binary; prefer open -a if it exits)
export TV_CDP_PORT=9333
cd ~/tradingview-mcp-jackson && node src/cli/index.js status
```

Do not kill Chrome on 9222.

## MCP (stdio) — args → Jackson `src/server.js`, env `TV_CDP_PORT=9333`
- `~/.claude/mcp.json` + `~/.claude/.mcp.json`
- `~/.claude.json` → `mcpServers.tradingview` (other servers preserved: MCP_DOCKER, comfyui, codebase-memory, claude-flow, laya-decide)
- `~/.openclaw/workspace/.mcp.json` + `workspace-v2` twin (ruflo preserved)
- `~/.hermes/config.yaml` → `mcp_servers.tradingview` (14 servers preserved)
- **RuVocal skipped** (HTTP MCP only; would bloat Ollama)

Restart Claude Code / Hermes after config edits.

## Davidd paper
`DAVIDDTECH_WEBHOOK_PASSPHRASE` SET in workspace + workspace-v2 `.env` (gitignored).
Webhook: `127.0.0.1:8787`, `DAVIDD_LIVE=0`, `create_order=false`.

## Verified 2026-09-27 ~20:08 PT
```json
{
  "success": true,
  "cdp_connected": true,
  "target_id": "2C48101219C081BCB9AA0C912358B3F5",
  "target_url": "https://www.tradingview.com/chart/tq0FHPAY/",
  "target_title": "Live stock, index, futures, Forex and Bitcoin charts on TradingView",
  "chart_symbol": "BATS:AAPL",
  "chart_resolution": "D",
  "chart_type": 1,
  "api_available": true
}
```
