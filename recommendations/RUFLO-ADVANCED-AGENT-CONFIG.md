# RuFlo Advanced Agent Configuration (reference)

Saved 2026-09-13 PT from live UI + source (`ruvnet/ruflo`).

## Where this lives

| Piece | Path / URL |
|---|---|
| UI modal (source of truth for knobs) | `ruflo/v3/goal_ui/src/components/agents/AdvancedSettingsModal.tsx` |
| Goal UI README (GOAP overview, not every slider) | `ruflo/v3/goal_ui/README.md` · live [goal.ruv.io](https://goal.ruv.io/) |
| Persists in browser | `localStorage['agenticflow-settings']` (JSON) — **not** MemPalace, **not** RuVocal Mongo |
| Swarm skill docs (topology concepts) | `ruflo/.agents/skills/agent-swarm/SKILL.md`, `swarm-advanced/SKILL.md`, `flow-nexus-swarm/SKILL.md` |
| Local RuVocal MCP tools | `http://127.0.0.1:8810/mcp` (`agent_spawn`, coordination, …) |

## What’s on GitHub README vs not

**In docs / skills:** GOAP idea, swarm topologies (hierarchical / mesh / ring / star), Flow Nexus cloud swarm, agent skills.

**Not a full field guide in main README:** this Advanced Agent modal’s every dropdown/slider. Those options are defined in `AdvancedSettingsModal.tsx`. This file is that missing field guide + Burton’s Valedia preset.

**Important local caveat:** Model tab “Local - Privacy-focused (ONNX)” is the Goal UI’s provider enum. Your **actual** local brain for RuFlo Local / RuVocal is **Ollama** (`qwen3.8-turbo-coder:q6_K`, Ornith, Uncensored) via `OPENAI_BASE_URL=http://127.0.0.1:11434/v1`. Treat the Model tab as Goal-UI routing prefs; still pick turbo-coder in the RuVocal model picker / system prompt.

---

## All settings (backend shape)

```ts
swarm: {
  topology: 'mesh' | 'hierarchical' | 'ring' | 'star'
  maxAgents: number            // UI 1–50
  strategy: 'balanced' | 'specialized' | 'adaptive'
  autoScaling: {
    enabled: boolean
    minAgents, maxAgents: number
    scaleUpThreshold, scaleDownThreshold: number  // %
  }
}
goap: {
  algorithm: 'a-star' | 'greedy' | 'dijkstra' | 'bfs' | 'dfs'
  heuristic: 'manhattan' | 'euclidean' | 'hamming' | 'custom'
  costMethod: 'uniform' | 'time' | 'resources' | 'tokens' | 'hybrid'
  optimization: { enabled, detectParallel, removeRedundant: boolean }
}
execution: {
  strategy: 'sequential' | 'parallel' | 'hybrid' | 'adaptive'
  maxParallelTasks: number     // UI 1–20
  timeout: number              // ms; UI shows seconds (timeout/1000); range 30s–900s
  enableQualityGates: boolean  // compile / tests / quality / security scans
}
modelRouter: {
  primaryProvider: 'anthropic' | 'openrouter' | 'gemini' | 'local'
  strategy: 'cost' | 'speed' | 'quality' | 'privacy' | 'balanced'
  maxCostPerRequest: number    // USD
  enableFallback: boolean
}
```

### Code defaults (`defaultSettings`)

- Swarm: hierarchical, maxAgents 10, adaptive, autoScaling **on** (min 2 / max 20, up 80% / down 20%)
- GOAP: a-star, manhattan, hybrid; optimization all **on**
- Execution: adaptive, maxParallel 5, timeout **300000 ms (300s)**, quality gates **on**
- Model: anthropic, balanced, $1.00/request, fallback **on**

### Quick presets (partial overlays)

| Preset | Swarm | Execution | Model |
|---|---|---|---|
| Development | maxAgents 5, balanced | sequential, quality gates off | strategy speed |
| Production | maxAgents 15, adaptive | adaptive, quality gates on | balanced |
| Budget | maxAgents 3, specialized | sequential, maxParallel 2 | openrouter + cost |
| Quality | maxAgents 20, adaptive | parallel, quality gates on | anthropic + quality |

---

## Burton / Valedia + turbo-coder recommended preset

**Goals:** Sprint D coding/audit only (fence: no live trading). Mac Studio M1 Ultra 128GB; one heavy local model at a time; prefer `qwen3.8-turbo-coder:q6_K`.

### Click these

**Quick Preset:** start from **Development**, then override as below (don’t use Quality — too many agents for one local GPU).

#### Swarm
| Setting | Value | Why |
|---|---|---|
| Topology | **Hierarchical** | One coordinator + workers for verify → harden `sports_engine` → status update |
| Maximum Agents | **4–6** (not 10–20) | Local Ollama can’t usefully run a herd; CoS also caps workers ~3 |
| Distribution | **Specialized** | Auditor vs coder vs doc roles, not random load balancing |
| Auto-Scaling | **OFF** | Prevents surprise agent storms eating RAM/GPU |

#### GOAP
| Setting | Value | Why |
|---|---|---|
| Planning Algorithm | **A\*** | Matches Goal UI’s own planner; good for short Sprint D graph |
| Heuristic | **Manhattan** | Default; fine for discrete coding milestones |
| Cost Method | **Tokens** (or Hybrid) | Local cost = context/time; tokens tracks turbo-coder better than “uniform” |
| Enable Optimization | **ON** | |
| Detect Parallel Actions | **ON** | Verify path can overlap doc reads; coding step still gated |
| Remove Redundant Actions | **ON** | Don’t re-wire what Claude already did |

#### Execution
| Setting | Value | Why |
|---|---|---|
| Execution Strategy | **Adaptive** or **Hybrid** | Not full Parallel on one GPU |
| Max Parallel Tasks | **2–3** | Matches GPU steward / CoS worker cap |
| Timeout | **300–600 s** | Local 27B tool loops are slow; 30s is too short |
| Enable Quality Gates | **ON** | Sprint D wants tests + ponytail + live-path proof |

#### Model
| Setting | Value | Why |
|---|---|---|
| Primary Provider | **Local** | Stay off Anthropic/OpenRouter for this lane |
| Routing Strategy | **Privacy** | “Use local models only” |
| Max Cost Per Request | irrelevant if local-only; leave low | |
| Enable Fallback | **OFF** | Don’t silently jump to cloud GPT/Claude mid Sprint D |

### Outside this modal (still required)

1. RuVocal chat model = `qwen3.8-turbo-coder:q6_K` (or Uncensored only if you want that voice).
2. System prompt: HARD FENCE first (see `RUVOCAL-SYSTEM-PROMPT-FENCE.md`).
3. MCP Base Servers on `http://127.0.0.1:8810/mcp` enabled + Health Check green.
4. Paste Sprint D from `recommendations/PASTE-TO-RUVOCAL-SWARM.md`.

### Suggested `localStorage` JSON (after Save Configuration)

```json
{
  "swarm": {
    "topology": "hierarchical",
    "maxAgents": 5,
    "strategy": "specialized",
    "autoScaling": {
      "enabled": false,
      "minAgents": 2,
      "maxAgents": 6,
      "scaleUpThreshold": 80,
      "scaleDownThreshold": 20
    }
  },
  "goap": {
    "algorithm": "a-star",
    "heuristic": "manhattan",
    "costMethod": "tokens",
    "optimization": {
      "enabled": true,
      "detectParallel": true,
      "removeRedundant": true
    }
  },
  "execution": {
    "strategy": "adaptive",
    "maxParallelTasks": 3,
    "timeout": 600000,
    "enableQualityGates": true
  },
  "modelRouter": {
    "primaryProvider": "local",
    "strategy": "privacy",
    "maxCostPerRequest": 0.5,
    "enableFallback": false
  }
}
```

Key name: `agenticflow-settings`.

---

## Related local files

- `MemPalace/sources/shared-brain/RUVOCAL-SYSTEM-PROMPT-FENCE.md`
- `~/.openclaw/workspace/recommendations/PASTE-TO-RUVOCAL-SWARM.md`
- `~/.openclaw/workspace/recommendations/LOCAL-MCP.md`
- ModelNotes: `/Volumes/NIMROD_SSD/_MODELS/ModelNotes/qwen3.8-turbo-coder__q6_K/NOTES.md`

## Upstream overview
See also `ruflo-explained.md` (saved from GitHub main).

## Local Goal UI URL (this Mac)
See `RUFLO-GOAL-UI-LOCAL.md` — Research/Agents at http://127.0.0.1:8082/ (not 5173).
