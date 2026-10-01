# Local RuFlo MCP (RuVocal) — filtered advertise for local LLMs

## Running
- LaunchAgent: `com.burton.ruflo-mcp-http` → `~/bin/ruflo-mcp-http.sh`
- Health: `curl -s http://127.0.0.1:8810/health`
- RPC/MCP: `http://127.0.0.1:8810/mcp` (also `/rpc`)
- **Local default:** ADR-035 / `--tools` filter (~44 tools). See `CLAUDE_FLOW_MCP_TOOLS` in `~/bin/ruflo-mcp-http.sh`.

## Prefill rule (Burton 2026-09-27)
**Do not** dump the full ~357-tool RuFlo catalog into every local Qwen/Ornith turn. That was the ~30K prefill anti-pattern on ~32k ctx.

Correct lever: **keep MCP servers enabled**, shrink what is *advertised* via:
1. RuFlo HTTP: `CLAUDE_FLOW_MCP_TOOLS` / `ruflo mcp start --tools …` (already on `:8810`)
2. Hermes: per-server `mcp_servers.<name>.tools.include` (applied 2026-09-27 — see `LLM-PREFILL-FILTER-2026-09-27.md`)

Wrong lever: disabling Exa/Brave/CBM/TradingView/MemPalace just to save tokens.

Cloud / large-ctx models may still use a wider tool set when needed. Local turbo-coder does **not** need `num_ctx=131072` solely to hold an unfiltered dump.

## RuVocal `.env.local`
Five Base Servers (UI names match cloud), all local:

```
MCP_SERVERS=[{"name":"RuFlo Core","url":"http://127.0.0.1:8810/mcp"}, ...]
```

Point RuVocal at the **filtered** HTTP endpoint (`:8810`), not an unfiltered stdio `ruflo mcp start` with the full catalog.

No `π Shared Brain` / `mcp.pi.ruv.io`. Shared Brain → MemPalace `sources/shared-brain`.

## Restart RuVocal after env change
```bash
launchctl kickstart -k "gui/$(id -u)/com.burton.ruvocal"
```

## Separate from Claude/Hermes
Workspace `.mcp.json` stdio `ruflo mcp start` for Claude Code / Hermes is independent of RuVocal’s HTTP client. Prefer the filtered HTTP server for local models.

## Cookie fix (2026-09-13)
Health Check red + lost system prompt: `COOKIE_NAME` was empty → `refreshSessionCookie` threw → POST `/api/mcp/health` and POST `/settings` 500. Fixed with `COOKIE_NAME=ruvocal-session` + `ALLOW_INSECURE_COOKIES=true` in `.env.local`.

## History note
The 2026-09-13 “Full tools + context” guidance (keep ~357 schemas + 131k ctx for local) is **superseded** by this file. Use filtered advertise + normal local ctx.

