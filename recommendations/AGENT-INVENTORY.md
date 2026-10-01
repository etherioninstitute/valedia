# Burton Agent Inventory (auto-generated)

**Scanned:** 2026-09-23 15:29 PT  
**Database:** `/Users/burtonchatelain/MemPalace/sources/shared-brain/agent-stack.db`  
**JSON:** `/Users/burtonchatelain/MemPalace/sources/shared-brain/agent-stack.json`  
**Ops:** `~/MemPalace/sources/shared-brain/AGENT-STACK-OPS.md`  
**Counts:** 35 MCP entries · 9 Hermes MCPs · 96 git repos · 11 tools

**Rule for every LLM (Hermes, Claude, Grok Bot, RuVocal, LMS):** Read this file or query the SQLite DB **before** asking Burton what tools he has. Prefer **codebase-memory-mcp** (`codebase-memory`) for structural code Qs; **Repomix** for whole-repo packs.

**codebase-memory present:** yes — claude.json (on), grok (on), hermes (on), hermes-profile:valedia (on)  
**binary:** `/Users/burtonchatelain/.openclaw/tools/cbm/codebase-memory-mcp`

---
## North stars

- **Valedia:** profit-maximizing local trading intelligence; Valedia runtime = sole live risk.
- **Assistants (this chat / RuFlo / Grok Build):** code/audit only — no live orders / wallet / spend.
- **MemPalace:** deep archive; Hermes MEMORY stays the tiny skull.

---
## LMS / Bionic — no auto-unload

- LMS/Bionic models are Burton-owned.
- Automation (routines, self-heal, Grok Build, Hermes cron, Valedia Trader) may PROPOSE unloading/consolidating (e.g. Ornith-solo) but MUST NEVER unload, swap, or kill a loaded LMS model without explicit yes in chat that same turn.
- Interactive Hermes/Qwen sessions beat automation.
- Optional pin: only act on unload when Burton sets an explicit pin like "Ornith-only until I clear".

---
## MCPs

| Source | Name | On? | Command / URL |
|--------|------|-----|---------------|
| claude-desktop | `gitlab` | yes | `/usr/local/bin/npx` |
| claude-desktop | `graft` | yes | `/Users/burtonchatelain/.npm-global/bin/graft` |
| claude-desktop | `mempalace` | yes | `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3` |
| claude.json | `MCP_DOCKER` | yes | `docker` |
| claude.json | `claude-flow` | yes | `npx` |
| claude.json | `codebase-memory` | yes | `/Users/burtonchatelain/.openclaw/tools/cbm/codebase-memory-mcp` |
| claude.json | `comfyui` | yes | `/usr/local/bin/npx` |
| codex | `computer-use` | no | `./Codex Computer Use.app/Contents/SharedSupport/SkyComputerUseClien…` |
| codex | `node_repl` | yes | `/Applications/ChatGPT.app/Contents/Resources/cua_node/bin/node_repl` |
| grok | `codebase-memory` | yes | `/Users/burtonchatelain/.openclaw/tools/cbm/codebase-memory-mcp` |
| grok | `ruflo` | yes | `http://127.0.0.1:8810/mcp` |
| hermes | `claude-flow` | yes | `/Users/burtonchatelain/.local/bin/npx` |
| hermes | `codebase-memory` | yes | `/Users/burtonchatelain/.openclaw/tools/cbm/codebase-memory-mcp` |
| hermes | `comfyui` | yes | `/usr/local/bin/npx` |
| hermes | `fortress` | no | `/Library/Frameworks/Python.framework/Versions/3.13/bin/tilion-mcp` |
| hermes | `gitlab` | no | `https://gitlab.com/api/v4/mcp` |
| hermes | `graft` | yes | `/Users/burtonchatelain/.npm-global/bin/graft` |
| hermes | `kive` | yes | `https://mcp.kive.ai/mcp` |
| hermes | `mempalace` | yes | `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3` |
| hermes | `wolfram` | no | `https://agenttools.wolfram.com/mcp` |
| hermes-profile:code-cloud | `mempalace` | yes | `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3` |
| hermes-profile:omni-code | `mempalace` | yes | `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3` |
| hermes-profile:ugft | `ugft-simctl` | yes | `/opt/homebrew/Caskroom/miniconda/base/bin/python3` |
| hermes-profile:valedia | `claude-flow` | yes | `/Users/burtonchatelain/.local/bin/npx` |
| hermes-profile:valedia | `codebase-memory` | yes | `/Users/burtonchatelain/.openclaw/tools/cbm/codebase-memory-mcp` |
| hermes-profile:valedia | `comfyui` | yes | `/usr/local/bin/npx` |
| hermes-profile:valedia | `fortress` | no | `/Library/Frameworks/Python.framework/Versions/3.13/bin/tilion-mcp` |
| hermes-profile:valedia | `gitlab` | no | `https://gitlab.com/api/v4/mcp` |
| hermes-profile:valedia | `graft` | yes | `/Users/burtonchatelain/.npm-global/bin/graft` |
| hermes-profile:valedia | `kive` | yes | `https://mcp.kive.ai/mcp` |
| hermes-profile:valedia | `mempalace` | yes | `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3` |
| hermes-profile:valedia | `wolfram` | no | `https://agenttools.wolfram.com/mcp` |
| launchagent | `ruflo-mcp-http` | yes | `http://127.0.0.1:8810/mcp` |
| openclaw-workspace | `ruflo` | yes | `http://127.0.0.1:8810/mcp` |
| openclaw-workspace-v2 | `ruflo` | yes | `http://127.0.0.1:8810/mcp` |

---
## Tools / CLIs

| Name | Kind | Path | Version |
|------|------|------|---------|
| `claude` | cli | `/Users/burtonchatelain/.local/bin/claude` | 2.1.272 (Claude Code) |
| `codebase-memory-mcp` | mcp-binary | `/Users/burtonchatelain/.openclaw/tools/cbm/codebase-memory-mcp` | codebase-memory-mcp 0.10.8 |
| `graft` | cli | `/Users/burtonchatelain/.npm-global/bin/graft` | 0.16.0 |
| `grok` | cli | `/Users/burtonchatelain/.local/bin/grok` | grok 1.0.40 (eb1a2256660d) [stable] |
| `hermes` | cli | `/Users/burtonchatelain/.local/bin/hermes` | — |
| `lms` | cli | `/Users/burtonchatelain/.lmstudio/bin/lms` | CLI commit: 1017bcb |
| `npx` | cli | `/Users/burtonchatelain/.local/bin/npx` | 11.19.0 |
| `ollama` | cli | `/usr/local/bin/ollama` | ollama version is 0.33.3 |
| `repomix` | cli | `npx repomix` | 1.18.1 |
| `ruflo` | cli | `/Users/burtonchatelain/.npm-global/bin/ruflo` | ruflo v3.41.2 |
| `tilion-mcp` | mcp-binary | `/Library/Frameworks/Python.framework/Versions/3.13/bin/tilion-mcp` | — |

---
## Spotlight repos (`~/MemPalace/sources/repos/github/`)

| Path | Remote | Branch |
|------|--------|--------|
| `/Users/burtonchatelain/MemPalace/sources/repos/github/CLI-Anything` | https://github.com/HKUDS/CLI-Anything.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/FinRL` | https://github.com/AI4Finance-Foundation/FinRL.git | `master` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/OpenTerminal` | https://github.com/ErTasselli/OpenTerminal.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/OpenTerminal-Valedia` | /Users/burtonchatelain/MemPalace/sources/repos/github/OpenTerminal | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/ai-hedge-fund` | https://github.com/virattt/ai-hedge-fund.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/awesome-jev` | https://github.com/yibie/awesome-jev.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/awesome-jev-by-typesafe` | https://github.com/Anil-matcha/awesome-jev-by-typesafe.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/ccxt` | https://github.com/ccxt/ccxt.git | `master` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/ecc` | https://github.com/affaan-m/ECC.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/eliza` | https://github.com/elizaOS/eliza.git | `develop` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/fomo-robinhood-radar` | https://github.com/cvxv666/fomo-robinhood-radar.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/fortress` | https://github.com/tiliondev/fortress.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/freqtrade` | https://github.com/freqtrade/freqtrade.git | `develop` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/harnessrouter` | https://github.com/HarnessRouter/harnessrouter.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/hummingbot` | https://github.com/hummingbot/hummingbot.git | `master` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/hypergrok-trading-desk` | https://github.com/galleonlabs/hypergrok-trading-desk.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/jesse` | https://github.com/jesse-ai/jesse.git | `master` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/jev-trader` | https://github.com/jarrodwatts/jev-trader.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/kalshi-agent` | https://github.com/HOboGoblin45/kalshi-agent.git | `master` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/laya-NandhaKishorM-archived` | https://github.com/NandhaKishorM/laya.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/laya-coreml` | https://github.com/mizorewww/laya-coreml.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/lf-game-theory` | https://github.com/pfergi42/lf-game-theory.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/nautilus_trader` | https://github.com/nautechsystems/nautilus_trader.git | `develop` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/ruflo` | https://github.com/ruvnet/ruflo.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/typesafe-skills` | https://github.com/typesafe-ai/skills.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/vaughanf1-ArbitrageBot` | https://github.com/vaughanf1/ArbitrageBot.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/vaughanf1-BB-Terminal` | https://github.com/vaughanf1/BB-Terminal.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/vaughanf1-GoldQuant` | https://github.com/vaughanf1/GoldQuant.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/vaughanf1-HedgeFund-V2` | https://github.com/vaughanf1/HedgeFund-V2.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/vaughanf1-vibe-trader` | https://github.com/vaughanf1/vibe-trader.git | `neon-apple-theme` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/youtube-automation-agent` | https://github.com/darkzOGx/youtube-automation-agent.git | `master` |

## All indexed git repos (96, spotlight=31)

| Path | Remote | Branch |
|------|--------|--------|
| `/Users/burtonchatelain/MemPalace/sources/repos/github/CLI-Anything` | https://github.com/HKUDS/CLI-Anything.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/FinRL` | https://github.com/AI4Finance-Foundation/FinRL.git | `master` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/OpenTerminal` | https://github.com/ErTasselli/OpenTerminal.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/OpenTerminal-Valedia` | /Users/burtonchatelain/MemPalace/sources/repos/github/OpenTerminal | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/ai-hedge-fund` | https://github.com/virattt/ai-hedge-fund.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/awesome-jev` | https://github.com/yibie/awesome-jev.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/awesome-jev-by-typesafe` | https://github.com/Anil-matcha/awesome-jev-by-typesafe.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/ccxt` | https://github.com/ccxt/ccxt.git | `master` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/ecc` | https://github.com/affaan-m/ECC.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/eliza` | https://github.com/elizaOS/eliza.git | `develop` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/fomo-robinhood-radar` | https://github.com/cvxv666/fomo-robinhood-radar.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/fortress` | https://github.com/tiliondev/fortress.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/freqtrade` | https://github.com/freqtrade/freqtrade.git | `develop` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/harnessrouter` | https://github.com/HarnessRouter/harnessrouter.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/hummingbot` | https://github.com/hummingbot/hummingbot.git | `master` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/hypergrok-trading-desk` | https://github.com/galleonlabs/hypergrok-trading-desk.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/jesse` | https://github.com/jesse-ai/jesse.git | `master` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/jev-trader` | https://github.com/jarrodwatts/jev-trader.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/kalshi-agent` | https://github.com/HOboGoblin45/kalshi-agent.git | `master` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/laya-NandhaKishorM-archived` | https://github.com/NandhaKishorM/laya.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/laya-coreml` | https://github.com/mizorewww/laya-coreml.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/lf-game-theory` | https://github.com/pfergi42/lf-game-theory.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/nautilus_trader` | https://github.com/nautechsystems/nautilus_trader.git | `develop` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/ruflo` | https://github.com/ruvnet/ruflo.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/typesafe-skills` | https://github.com/typesafe-ai/skills.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/vaughanf1-ArbitrageBot` | https://github.com/vaughanf1/ArbitrageBot.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/vaughanf1-BB-Terminal` | https://github.com/vaughanf1/BB-Terminal.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/vaughanf1-GoldQuant` | https://github.com/vaughanf1/GoldQuant.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/vaughanf1-HedgeFund-V2` | https://github.com/vaughanf1/HedgeFund-V2.git | `main` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/vaughanf1-vibe-trader` | https://github.com/vaughanf1/vibe-trader.git | `neon-apple-theme` |
| `/Users/burtonchatelain/MemPalace/sources/repos/github/youtube-automation-agent` | https://github.com/darkzOGx/youtube-automation-agent.git | `master` |
| `/Users/burtonchatelain/.TeXmacs/plugins/mma` | https://github.com/hammerfunctor/tm-mathematica | `main` |
| `/Users/burtonchatelain/.codex/.chatgpt-projects/g-p-69eedff736e88191a5eecd56c1c4a3cc/output/ugft-v327/formal/mathlib-cache` | https://github.com/leanprover-community/mathlib4 | `HEAD` |
| `/Users/burtonchatelain/.codex/.chatgpt-projects/g-p-69eedff736e88191a5eecd56c1c4a3cc/output/ugft-v327/formal/packages-cache/Cli` | https://github.com/leanprover/lean4-cli | `HEAD` |
| `/Users/burtonchatelain/.codex/.chatgpt-projects/g-p-69eedff736e88191a5eecd56c1c4a3cc/output/ugft-v327/formal/packages-cache/LeanSearchClient` | https://github.com/leanprover-community/LeanSearchClient | `HEAD` |
| `/Users/burtonchatelain/.codex/.chatgpt-projects/g-p-69eedff736e88191a5eecd56c1c4a3cc/output/ugft-v327/formal/packages-cache/Qq` | https://github.com/leanprover-community/quote4 | `HEAD` |
| `/Users/burtonchatelain/.codex/.chatgpt-projects/g-p-69eedff736e88191a5eecd56c1c4a3cc/output/ugft-v327/formal/packages-cache/aesop` | https://github.com/leanprover-community/aesop | `HEAD` |
| `/Users/burtonchatelain/.codex/.chatgpt-projects/g-p-69eedff736e88191a5eecd56c1c4a3cc/output/ugft-v327/formal/packages-cache/batteries` | https://github.com/leanprover-community/batteries | `HEAD` |
| `/Users/burtonchatelain/.codex/.chatgpt-projects/g-p-69eedff736e88191a5eecd56c1c4a3cc/output/ugft-v327/formal/packages-cache/importGraph` | https://github.com/leanprover-community/import-graph | `HEAD` |
| `/Users/burtonchatelain/.codex/.chatgpt-projects/g-p-69eedff736e88191a5eecd56c1c4a3cc/output/ugft-v327/formal/packages-cache/plausible` | https://github.com/leanprover-community/plausible | `HEAD` |
| `/Users/burtonchatelain/.codex/.chatgpt-projects/g-p-69eedff736e88191a5eecd56c1c4a3cc/output/ugft-v327/formal/packages-cache/proofwidgets` | https://github.com/leanprover-community/ProofWidgets4 | `HEAD` |
| `/Users/burtonchatelain/.hermes/hermes-agent` | git@github.com:NousResearch/hermes-agent.git | `main` |
| `/Users/burtonchatelain/.hermes/plugins/ponytail` | file:///Users/burtonchatelain/ponytail-pruned | `main` |
| `/Users/burtonchatelain/.hermes/profiles/code-cloud/skills/humanizer` | https://github.com/blader/humanizer.git | `main` |
| `/Users/burtonchatelain/.hermes/profiles/omni-code/skills/humanizer` | https://github.com/blader/humanizer.git | `main` |
| `/Users/burtonchatelain/.hermes/skills/humanizer` | https://github.com/blader/humanizer.git | `main` |
| `/Users/burtonchatelain/.hermes_backups/20260828-030126-pre-update-v2026.8.27/hermes-agent` | git@github.com:NousResearch/hermes-agent.git | `main` |
| `/Users/burtonchatelain/.hermes_backups/20260828-030126-pre-update-v2026.8.27/skills/humanizer` | https://github.com/blader/humanizer.git | `main` |
| `/Users/burtonchatelain/.nvm` | https://github.com/nvm-sh/nvm.git | `HEAD` |
| `/Users/burtonchatelain/.openclaw/workspace` | — | `master` |
| `/Users/burtonchatelain/.openclaw/workspace-v2` | — | `valedia-v2-grok-build` |
| `/Users/burtonchatelain/.openclaw/workspace/.claude/worktrees/valedia-trading-improvement-b7d855` | — | `HEAD` |
| `/Users/burtonchatelain/.spack/package_repos/fncqgg4` | https://github.com/spack/spack-packages.git | `develop` |
| `/Users/burtonchatelain/Claude-Patent-Creator` | https://github.com/RobThePCGuy/Claude-Patent-Creator.git | `main` |
| `/Users/burtonchatelain/ComfyUI-Installs/Comfy/ComfyUI` | https://github.com/Comfy-Org/ComfyUI.git | `HEAD` |
| `/Users/burtonchatelain/ComfyUI-Installs/Comfy/ComfyUI/custom_nodes/ComfyUI-RMBG` | https://github.com/1038lab/ComfyUI-RMBG.git | `main` |
| `/Users/burtonchatelain/ComfyUI-Installs/Comfy/ComfyUI/custom_nodes/ComfyUI-SceneGenerator` | https://github.com/Ron-Digital/ComfyUI-SceneGenerator | `main` |
| `/Users/burtonchatelain/ComfyUI-Installs/Comfy/ComfyUI/custom_nodes/ComfyUI-productfix` | https://github.com/MiddleKD/ComfyUI-productfix | `main` |
| `/Users/burtonchatelain/ComfyUI-Installs/Comfy/ComfyUI/custom_nodes/comfyui-agent-panel` | https://github.com/artokun/comfyui-mcp-panel | `main` |
| `/Users/burtonchatelain/Developer/NIM-Custom` | https://github.com/etherioninstitute/NIM-Custom.git | `main` |
| `/Users/burtonchatelain/Developer/NIMRODStudio` | — | `main` |
| `/Users/burtonchatelain/Developer/autoresearch` | https://github.com/thenamangoyal/autoresearch.git | `master` |
| `/Users/burtonchatelain/Developer/scientific-agent-skills` | https://github.com/K-Dense-AI/scientific-agent-skills | `main` |
| `/Users/burtonchatelain/Library/Application Support/Claude/local-agent-mode-sessions/1d996e9e-48aa-4954-ae29-8f98c76fff41/d531c336-309f-409c-ad1d-ea458502e672/cowork_plugins/marketplaces/knowledge-work-plugins` | https://github.com/anthropics/knowledge-work-plugins.git | `main` |
| `/Users/burtonchatelain/NIMROD CODE MUMPS/nimdevel/scimake` | https://github.com/Tech-XCorp/scimake.git | `master` |
| `/Users/burtonchatelain/NIMROD CODE Original` | https://github.com/etherioninstitute/NIM.git | `main` |
| `/Users/burtonchatelain/NIMROD CODE Original/nimdevel/scimake` | https://github.com/Tech-XCorp/scimake.git | `master` |
| `/Users/burtonchatelain/NIMROD CODE/nimdevel/scimake` | https://github.com/Tech-XCorp/scimake.git | `master` |
| `/Users/burtonchatelain/PETSc/arch-darwin-opt-mumps/externalpackages/git.scalapack` | https://github.com/Reference-ScaLAPACK/scalapack | `HEAD` |
| `/Users/burtonchatelain/VALEDIA_MATH/lean_current` | — | `—` |
| `/Users/burtonchatelain/VALEDIA_MATH/repos/LeanDojo` | https://github.com/lean-dojo/LeanDojo.git | `main` |
| `/Users/burtonchatelain/VALEDIA_MATH/repos/OpenClaw-RL` | https://github.com/Gen-Verse/OpenClaw-RL.git | `main` |
| `/Users/burtonchatelain/VALEDIA_MATH/repos/cc-polymath` | https://github.com/rand/cc-polymath.git | `main` |
| `/Users/burtonchatelain/VALEDIA_MATH/repos/marker` | https://github.com/VikParuchuri/marker.git | `master` |
| `/Users/burtonchatelain/Zoltan` | https://github.com/sandialabs/Zoltan.git | `main` |
| `/Users/burtonchatelain/bluemira` | https://github.com/Fusion-Power-Plant-Framework/bluemira.git | `develop` |
| `/Users/burtonchatelain/bluemira/bluemira-template-project` | https://github.com/Fusion-Power-Plant-Framework/bluemira-template-project.git | `main` |
| `/Users/burtonchatelain/displaycal-py3` | https://www.github.com/eoyilmaz/displaycal-py3 | `develop` |
| `/Users/burtonchatelain/hermes-webui` | https://github.com/nesquena/hermes-webui.git | `master` |
| `/Users/burtonchatelain/iTerm2-Color-Schemes` | https://github.com/mbadolato/iTerm2-Color-Schemes.git | `master` |
| `/Users/burtonchatelain/langfuse` | https://github.com/langfuse/langfuse.git | `main` |
| `/Users/burtonchatelain/parmetis-src` | https://github.com/KarypisLab/ParMETIS.git | `main` |
| `/Users/burtonchatelain/parmetis-src/metis` | https://github.com/KarypisLab/METIS.git | `master` |
| `/Users/burtonchatelain/parmetis-src/metis/GKlib` | https://github.com/KarypisLab/GKlib.git | `master` |
| `/Users/burtonchatelain/pumi-core` | https://github.com/SCOREC/core.git | `HEAD` |
| `/Users/burtonchatelain/spack` | https://github.com/spack/spack.git | `develop` |
| `/Users/burtonchatelain/toggl-wave-invoicer` | https://github.com/etherioninstitute/toggl-wave-invoicer.git | `master` |
| `/Users/burtonchatelain/vault/lean/.lake/packages/Cli` | https://github.com/leanprover/lean4-cli | `HEAD` |
| `/Users/burtonchatelain/vault/lean/.lake/packages/LeanSearchClient` | https://github.com/leanprover-community/LeanSearchClient | `HEAD` |
| `/Users/burtonchatelain/vault/lean/.lake/packages/Qq` | https://github.com/leanprover-community/quote4 | `HEAD` |
| `/Users/burtonchatelain/vault/lean/.lake/packages/aesop` | https://github.com/leanprover-community/aesop | `HEAD` |
| `/Users/burtonchatelain/vault/lean/.lake/packages/batteries` | https://github.com/leanprover-community/batteries | `HEAD` |
| `/Users/burtonchatelain/vault/lean/.lake/packages/importGraph` | https://github.com/leanprover-community/import-graph | `HEAD` |
| `/Users/burtonchatelain/vault/lean/.lake/packages/plausible` | https://github.com/leanprover-community/plausible | `HEAD` |
| `/Users/burtonchatelain/vault/lean/.lake/packages/proofwidgets` | https://github.com/leanprover-community/ProofWidgets4 | `HEAD` |
| `/Users/burtonchatelain/zoltan-src` | https://github.com/sandialabs/Zoltan.git | `main` |

---
## Refresh
```bash
~/bin/scan-agent-stack.py
```

Or ask Valedia: **refresh agent inventory**.
## Query
```bash
sqlite3 /Users/burtonchatelain/MemPalace/sources/shared-brain/agent-stack.db "SELECT source, name, enabled FROM mcps ORDER BY source, name;"
sqlite3 /Users/burtonchatelain/MemPalace/sources/shared-brain/agent-stack.db "SELECT name FROM mcps WHERE name LIKE '%codebase-memory%';"
sqlite3 /Users/burtonchatelain/MemPalace/sources/shared-brain/agent-stack.db "SELECT path, remote_url FROM repos WHERE path LIKE '%nautilus%';"
```

