# Local RuFlo MCP (RuVocal)

## Running
- LaunchAgent: `com.burton.ruflo-mcp-http` → `~/bin/ruflo-mcp-http.sh`
- Health: `curl -s http://127.0.0.1:8810/health`
- RPC/MCP: `http://127.0.0.1:8810/mcp` (also `/rpc`)
- Tools: full local catalog via `tools/list` (hundreds); startup banner may say “27 enabled” — list is larger

## RuVocal `.env.local`
Five Base Servers (UI names match cloud), all local:

```
MCP_SERVERS=[{"name":"RuFlo Core","url":"http://127.0.0.1:8810/mcp"}, ...]
```

No `π Shared Brain` / `mcp.pi.ruv.io`. Shared Brain → MemPalace `sources/shared-brain`.

## Restart RuVocal after env change
```bash
launchctl kickstart -k "gui/$(id -u)/com.burton.ruvocal"
```

## Separate from Claude/Hermes
Workspace `.mcp.json` stdio `ruflo mcp start` for Claude Code / Hermes is independent of RuVocal’s HTTP client.

## Cookie fix (2026-09-13)
Health Check red + lost system prompt: `COOKIE_NAME` was empty → `refreshSessionCookie` threw → POST `/api/mcp/health` and POST `/settings` 500. Fixed with `COOKIE_NAME=ruvocal-session` + `ALLOW_INSECURE_COOKIES=true` in `.env.local`.
