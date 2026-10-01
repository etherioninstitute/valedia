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
| Ollama OpenAI API | http://127.0.0.1:11434/v1 |

LaunchAgent: `com.burton.ruflo-goal-ui` → `~/bin/ruflo-goal-ui-start.sh` (strictPort **8082**).
Logs: `~/Library/Logs/RuFloGoalUI/`

## Research AI — local Ollama (default)

Research **defaults** to local Ollama turbo-coder effort tags (not cloud Gemini):

| Model id | Label |
|---|---|
| `qwen3.8-turbo-coder:q6_K-low` | **DEFAULT** — Turbo-coder Q6 low (local Ollama) |
| `qwen3.8-turbo-coder:q6_K-medium` | Turbo-coder Q6 medium (local Ollama) |
| `qwen3.8-turbo-coder:q6_K-xhigh` | Turbo-coder Q6 xhigh (local Ollama) |

Cloud Gemini options remain in the AI Model dropdown (labeled cloud) if you switch back.

### How it works

- Vite `server.proxy`: `/ollama` → `http://127.0.0.1:11434` (avoids browser CORS).
- Smoke-test from Goal UI origin: `curl -sS http://127.0.0.1:8082/ollama/v1/models`
- When the selected model starts with `qwen3.8-turbo-coder:`, Research calls `runOllamaResearchStep` (`src/lib/ollamaResearch.ts`) → `POST /ollama/v1/chat/completions` and **skips** Supabase/Lovable `research-step`.
- Cloud Gemini still goes through `supabase.functions.invoke('research-step')` (needs real `VITE_SUPABASE_*` + Lovable edge secrets — placeholders in `.env` mean live cloud research does not work locally).

### Not the same as Advanced Agent `local`

Advanced Agent → Model = provider enum (`anthropic` / `openrouter` / `gemini` / `local` ONNX) → `localStorage['agenticflow-settings']`. That `local` path is **ONNX**, not Ollama. Research Ollama wiring is separate (Widget Customizer AI Model + Index research path).

## Official upstream URLs

- Hosted: https://goal.ruv.io/ · https://goal.ruv.io/agents
- Source: `ruflo/v3/goal_ui/`
- Overview: `ruflo-explained.md` (saved under this wing)
