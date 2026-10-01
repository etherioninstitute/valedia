# LLM prefill filter package — applied 2026-09-27 ~22:05 PT

**Principle:** MCPs stay **enabled**. Prefill shrinks via `tools.include` (ADR-035 style), not by turning servers off.

## Applied
- Backed up Hermes configs `*.bak-llm-filter-20260927-220636`
- Root `~/.hermes/config.yaml` + Valedia / CTO / trading scout|decider|auditor profiles: `tools.include` on enabled MCP servers (tradingview, claude-flow, codebase-memory, mempalace, kive, graft, search, laya, comfyui)
- Cleared Hermes `mcp_schema_cache.json` trees so the next session rebuilds from includes
- RuFlo HTTP `:8810` already advertises ADR-035 ~44 tools via `~/bin/ruflo-mcp-http.sh` (unchanged this pass)
- **Not done:** Bionic unload / Codex plugin disable (wrong lever; would violate “don’t disable MCPs”)

## Expected effect
Root cache was ~250 tools / ~287KB. After filter, tradingview alone drops from 84 → 16 advertised; kive 28 → 8; etc. Skills catalog (~80 skill folders) is separate and was left intact.

## LOCAL-MCP.md
Rewritten so local Qwen/Ornith default is **filtered advertise**, not “keep ~357 tools + 131k ctx”.
