# SebAI / lf-game-theory — Security audit (2026-09-14 PT)

**Repo:** `~/MemPalace/sources/repos/github/lf-game-theory` @ `8e5f021`  
**Scope:** Arena path (`src/arena/*`, `src/agents/manager.ts`, env, deps).  
**Auditor:** Valedia (code review only — no keys, no funding, no live calls).

## Verdict

### CONDITIONAL GO — tiny pilot only
You may fund a **small pilot** after the mitigations below.  
**NO-GO** for full 16×1000 / 100 rounds until that pilot completes cleanly (setup → rounds → sweep).

This is research code with **real Lightning custody at lightningfaucet.com**. Treat sats as burnable.

## What’s solid (HOLDS)

| Check | Evidence |
|-------|----------|
| LLM never sees wallet/operator keys | Prompts only have balance + messages + action grammar (`src/arena/prompts.ts`) |
| Actions allowlisted | Parser accepts only `SEND` / `MESSAGE` / `BROADCAST` / `PASS` (`action-parser.ts`); no `eval` / shell |
| Rate limits enforced | `applyRateLimits` in coordinator path |
| Operator key from env only | `LF_OPERATOR_KEY` via dotenv; `.env` gitignored |
| Balance gate before funding | Refuses start if operator < required × 1.1 |
| Transfers use operator API | `transferBetweenAgents` = sweep→fund with operator key (`manager.ts`) |
| HTTPS only to faucet | `https://lightningfaucet.com/api/` |
| Dep surface small | anthropic, openai, better-sqlite3, dotenv, uuid, yaml |

## Findings (risk → mitigation)

### F1 — MEDIUM: Per-agent API keys stored in SQLite plaintext
`store.updateAgentLightning(..., apiKey)` writes `lightning_api_key` into `arena-*.db`.  
Keys are **not** sent to LLMs, but DB theft = agent-key theft (faucet agent scope).  
**Mitigation:** Keep `*.db` on encrypted disk only; never sync to iCloud/Dropbox/git; delete DBs after sweep; chmod 600.

### F2 — MEDIUM: Non-atomic transfers
Sweep-from then fund-to. Mid-failure can strand sats on operator (usually recoverable) or leave inconsistent balances.  
**Mitigation:** Prefer small amounts; watch console per round; after crash, check operator + agent balances on faucet dashboard before re-running.

### F3 — MEDIUM: Crash / kill before teardown strands agent wallets
`teardown` sweeps back; SIGKILL / power loss skips it.  
**Mitigation:** Don’t Ctrl-Z/kill -9 mid-run; leave terminal alone; have faucet operator UI ready to withdraw leftover agents.

### F4 — MEDIUM: Third-party custody + fees
All sats live at Lightning Faucet; payments >100 sats take **1%**. Pool only shrinks.  
**Mitigation:** Fund only what you’re willing to lose; expect not to recover 100%.

### F5 — LOW–MOD: npm audit (10 issues, 6 high)
Mostly eslint/minimatch/picomatch ReDoS (dev); runtime: `uuid`, `yaml` moderate.  
**Mitigation:** In the run directory: `npm audit fix` (review diff) before first funded run.

### F6 — LOW: Empty key becomes silent `''`
Constructor accepts `LF_OPERATOR_KEY || ''`; fails at first API call.  
**Mitigation:** Confirm `whoami` / pilot setup error message before funding large.

### F7 — INFO: No simulation mode
Every run spends real sats + API credits (both Anthropic + OpenAI clients required at startup).  
**Mitigation:** Pilot YAML first; set provider spend caps.

## Pilot envelope (recommended first fund)

Use `experiments/arena-mixed-priming-pilot.yaml` **or tighter**:

| Knob | Pilot (shipped) | Safer first |
|------|-----------------|-------------|
| Agents | 8 | 4 (edit YAML) |
| Rounds | 10 | 5 |
| startingBalance | 500 | **100** |
| Sats at risk (×1.1 buffer) | ~4,400 | ~440–880 |
| API calls | ~80 | fewer |

**Do not** start `arena:full` / `arena:mixed` until pilot teardown sweeps OK.

## Burton-only funding checklist (keys never in chat)

1. `cd ~/MemPalace/sources/repos/github/lf-game-theory && npm install && npm audit fix && npm run build`
2. Copy `.env.example` → `.env` locally; set `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `LF_OPERATOR_KEY` (**never paste here**)
3. Register/fund operator at lightningfaucet.com/ai-agents — only pilot buffer sats
4. Set Anthropic/OpenAI dashboard spend limits
5. Optional: edit pilot YAML to `startingBalance: 100`
6. Run: `npm run arena:mixed-pilot` (or `node dist/arena/coordinator.js --config=experiments/…`)
7. Watch: Setup → Round N → Teardown sweep totals
8. Confirm operator balance ≈ expected leftovers; delete/secure `arena-*.db`
9. Only then consider larger YAML

## Explicit non-actions for Valedia / RuFlo / Grok Build
- No storing or requesting `LF_OPERATOR_KEY`
- No funding invoices from this chat
- No “just start it for me” with real sats

## GO/NO-GO summary
| Run | Status |
|-----|--------|
| Paper / code changes | Always OK |
| **Pilot ≤ ~5k sats** after F1–F5 mitigations | **CONDITIONAL GO** |
| Full 16×1000×100 | **NO-GO until pilot green** |
