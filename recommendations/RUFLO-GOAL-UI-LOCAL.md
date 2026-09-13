# RuFlo Research / Coding Agent Swarm — local (this Mac)

Per upstream: [ruvnet/ruflo](https://github.com/ruvnet/ruflo) README + `v3/goal_ui/README.md`:

```bash
cd v3/goal_ui && npm install && npm run dev   # docs say http://localhost:8080
```

## This machine

| Service | URL |
|---|---|
| **RuFlo Research / Agents (Goal UI)** | **http://127.0.0.1:8082/** · agents: **http://127.0.0.1:8082/agents** |
| RuFlo Local (RuVocal chat + MCP) | http://127.0.0.1:5173/ |
| Local RuFlo MCP HTTP | http://127.0.0.1:8810/mcp |
| Signal CLI (occupies official Goal ports) | 127.0.0.1:8080 and :8081 |

LaunchAgent: `com.burton.ruflo-goal-ui` → `~/bin/ruflo-goal-ui-start.sh` (strictPort **8082**).
Logs: `~/Library/Logs/RuFloGoalUI/`

## Turbo-coder / Ollama — important gap

Stock Goal UI **does not** expose an Ollama model picker.

- Advanced Agent → Model = provider enum only (`anthropic` / `openrouter` / `gemini` / `local` ONNX) → `localStorage['agenticflow-settings']`. Those knobs are **not** wired to `qwen3.8-turbo-coder:q6_K`.
- Research runs go through Supabase edge functions (`research-step`, etc.) with cloud `aiModel` strings (Gemini family in `WidgetCustomizer`).
- Placeholders in `v3/goal_ui/.env` let Vite boot; **live** GOAP/agent execution needs real `VITE_SUPABASE_*` + edge secrets (`example.env`, `docs/DEPLOYMENT.md`).

**To actually run Sprint D on turbo-coder today:** use **RuFlo Local** (`5173`) with model `qwen3.8-turbo-coder:q6_K` + MCP `:8810`, or Claude Code / Hermes + workspace `ruflo` MCP + Ollama.

Goal UI local = planner/agents **dashboard UI** per RuFlo docs; it is not yet an Ollama front-end.

## Official upstream URLs

- Hosted: https://goal.ruv.io/ · https://goal.ruv.io/agents
- Source: `ruflo/v3/goal_ui/`
- Overview: `ruflo-explained.md` (saved under this wing)
