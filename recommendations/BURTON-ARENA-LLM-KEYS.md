# Arena LLM / API-key map (code audit)

**Date:** 2026-09-14  
**Scope:** `lf-game-theory` Arena path used by `experiments/arena-valedia-micro-pilot.yaml` (`npm run arena:valedia-micro` → `src/arena/coordinator.ts`).  
**Method:** Read-only source review. No `.env` opened. No coordinator run with real keys. No Anthropic / OpenAI / faucet calls.

Related (not used by the Valedia YAML): `src/coordinator.ts` (classic 5-game coordinator) shares `LLMRouter` and `AgentManager`. Differences are called out where they change key or spend behavior.

---

## TLDR

- **Startup hard-requires `OPENAI_API_KEY`.** `LLMRouter` constructs both SDKs eagerly. The OpenAI SDK throws if the key is missing. Anthropic does **not** throw at construct.
- **A successful Valedia run also needs `ANTHROPIC_API_KEY` and `LF_OPERATOR_KEY`.** Those fail later (first Claude call / first faucet `whoami`).
- **Per round, every agent gets exactly one decision LLM call.** Parsing is local regex. No parse/retry LLM in Arena.
- **Valedia volume:** ~**10 Anthropic + 10 OpenAI** decision calls (2+2 agents × 5 rounds). Zero extra LLM parse calls. Plus one app-level retry per thrown error, on top of SDK retries.
- **LLM keys are not sent to models, not logged, not stored in SQLite.** `LF_OPERATOR_KEY` is in-memory only. **Per-agent Lightning API keys are stored in SQLite** (`agents.lightning_api_key`) but are unused after creation — all faucet calls use the operator key.
- **Missing OpenAI key:** crash before funding, no LLM spend. **Missing Anthropic key with OpenAI present:** funds Lightning first, then Claude agents PASS. **Mid-run kill:** no sweep; sats can strand on agent wallets.
- **Claude-only cannot run today** even with an all-Claude YAML, because of eager `new OpenAI()`. Minimal fix: lazy-init the OpenAI client.

---

## 1. Env vars and client construction

### Load path

`src/arena/coordinator.ts` lines 1–2:

```ts
import dotenv from 'dotenv';
dotenv.config({ override: true });
```

Loads `.env` from process CWD. `override: true` means file values overwrite already-set process env.

Template names (`.env.example`, not `.env`):

| Variable | Used by | When it actually fails if missing |
|---|---|---|
| `OPENAI_API_KEY` | `LLMRouter` constructor → `new OpenAI({ apiKey })` | **Immediate process crash** at `new LLMRouter()` |
| `ANTHROPIC_API_KEY` | `LLMRouter` constructor → `new Anthropic({ apiKey })` | **First Claude `messages.create`**, not construct |
| `LF_OPERATOR_KEY` | `ArenaCoordinator` constructor → `new AgentManager(...)` | **First faucet call** (`checkOperatorBalance` / `whoami`) during `setup()` |

There is **no** application-level env validator. README.md still documents `FAUCET_OPERATOR_KEY`; the code and `.env.example` use **`LF_OPERATOR_KEY`**. That README name is dead.

### Who constructs which client (eager)

`src/llm/router.ts` `LLMRouter.constructor`:

```ts
this.claude = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
this.openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
```

Called from:

- `ArenaCoordinator.constructor` (`src/arena/coordinator.ts`) — **eager**, before `run()` / `setup()`
- `Coordinator.constructor` (`src/coordinator.ts`) — same, classic games path

**Eager, not lazy.** Both clients exist for the whole process, regardless of which models the YAML uses.

### SDK behavior (this repo’s installed versions)

**OpenAI** (`node_modules/openai/index.mjs` `OpenAI.constructor`):

- If `apiKey === undefined`, throws:  
  `"The OPENAI_API_KEY environment variable is missing or empty; ..."`
- Passing `apiKey: process.env.OPENAI_API_KEY` when unset is `undefined`, so this **throws at `new LLMRouter()`**.
- Matches `BURTON-PILOT-RUN.md`: dry-run `npm run arena:valedia-micro` stops on missing `OPENAI_API_KEY`.

**Anthropic** (`node_modules/@anthropic-ai/sdk/index.mjs` `Anthropic.constructor`):

- Defaults missing key to `null`. **Does not throw at construct.**
- First `messages.create` without a key hits `validateHeaders` →  
  `"Could not resolve authentication method. Expected either apiKey or authToken to be set."`

### Constructor order vs side effects

`ArenaCoordinator.constructor`:

1. Parse YAML
2. `new DataStore('arena-valedia-micro-pilot.db')` — **creates/opens SQLite + schema**
3. `new AgentManager(process.env.LF_OPERATOR_KEY || '')` — empty string if unset; no network
4. `new LLMRouter()` — **OpenAI throw happens here**

So a missing OpenAI key still creates an empty SQLite file, but does **not** create an experiment row, does **not** fund wallets, does **not** call any LLM.

`run()` then: `setup()` → `executeRounds()` → `teardown()`. On throw, `store.updateExperimentPhase(..., 'error')` and rethrow. **No sweep in the catch path.**

---

## 2. Per agent / per round: when the LLM is called

### When

`ArenaCoordinator.executeOneRound` (`src/arena/coordinator.ts`):

1. Snapshot Lightning balances (`AgentManager.getAgentBalance` → faucet `list_agents`)
2. Mark 0-balance agents as eliminated **for transfers**, not for talking
3. **`queryAgent` for every agent**, including 0-balance (they can still message)
4. Execute `SEND` via Lightning
5. Route `MESSAGE` / `BROADCAST` in-process (`MessageRouter`)
6. Snapshot balances, persist round

Concurrency: batches of 4 (`CONCURRENCY = 4`). Valedia has 4 agents, so one parallel batch per round (2 Claude + 2 GPT at once).

**Arena does not skip eliminated agents.** The system prompt says they cannot transfer but can still communicate.

### Prompt shape

Built by `buildArenaPrompt` / `getPrimingText` in `src/arena/prompts.ts`. Sent in `queryAgent`:

| Role | Content | Function |
|---|---|---|
| **system** | Constant `ARENA_SYSTEM_PROMPT`: real sats, private balances, 0-sat elimination, **actions-only** format | `buildArenaPrompt` |
| **user** | Identity, GOAL, optional STRATEGY GUIDANCE, CURRENT STATE, action limits, recent public/private messages, “What are your actions this round?” | `buildArenaPrompt` |

Valedia YAML: `revealModelType: false` → user prompt is `"You are Agent-N, an AI agent..."` (no “claude”/“openai”). Priming is injected as `STRATEGY GUIDANCE` without naming the condition.

Call parameters from `queryAgent`:

- `model` / `modelId` from the agent’s YAML group
- `temperature`: YAML `0.7`
- `maxTokens`: **300** (hardcoded)
- `seed: 42` on OpenAI only (`queryOpenAI`)

Claude: `system` is the Anthropic `system` field; user is a single `role: 'user'` message.  
OpenAI: optional `role: 'system'` then `role: 'user'`.

**Keys, Lightning IDs, and operator identity are not in the prompt.** Prompt fields are name, balance, round, peer names, messages, rules, priming text.

### What the model can return (Arena)

Instructed to emit **one action per line**, no commentary. Parsed locally by `parseArenaActions` / `parseSingleAction` / `applyRateLimits` in `src/arena/action-parser.ts`. **No LLM parse call.**

| Action | Wire format | Effect |
|---|---|---|
| `send` | `SEND <amount> TO Agent-N` | Lightning transfer if valid |
| `message` | `MESSAGE Agent-N "..."` | Private message (in-process; not on Lightning) |
| `broadcast` | `BROADCAST "..."` | Public message (in-process) |
| `pass` | `PASS` | No-op. Also the fallback if empty/unparseable, or if `queryAgent` throws after retry |

Valedia rate limits (`applyRateLimits`): 1 transfer, 2 private messages, 1 broadcast per round; min transfer 1 sat. Excess marked invalid and logged; they are not sent to Lightning.

Commentary lines (`//`, `#`, `*`, or unmatched text) are skipped. If nothing valid remains → synthetic `pass`.

### Contrast: classic games coordinator (not Valedia)

`src/games/engine.ts` `GameEngine.getAgentDecision`:

- One decision LLM call (`maxTokens: 50`, `temperature: 0`)
- Local `parseResponse` (`src/llm/parser.ts`)
- **If unparseable, a second LLM call** with `buildRetryPrompt` — Arena has no equivalent

---

## 3. Call volume: `experiments/arena-valedia-micro-pilot.yaml`

Config: 4 agents × 5 rounds × 100 sats.

| Group | Count | Provider | modelId | Priming |
|---|---|---|---|---|
| 1 | 1 | claude | `claude-sonnet-4-5-20250929` | strategic |
| 2 | 1 | openai | `gpt-4o` | strategic |
| 3 | 1 | claude | `claude-sonnet-4-5-20250929` | cooperative |
| 4 | 1 | openai | `gpt-4o` | competitive |

### Happy path (no HTTP errors)

| Provider | Logical `LLMRouter.query` calls | What each call is for |
|---|---|---|
| **Anthropic** | **10** | Decision only: 2 Claude agents × 5 rounds |
| **OpenAI** | **10** | Decision only: 2 GPT agents × 5 rounds |
| Parse / analysis / embeddings | **0** | `parseArenaActions` is regex; `ArenaAnalyzer` is local math |

Teardown prints `llm.getStats()` (call counts + rough $). Cost math in `LLMRouter.getStats`: Claude $3 / $15 per MTok, OpenAI $2.5 / $10 per MTok (Sonnet / GPT-4.1 list prices; GPT-4o actual $ may differ).

Ballpark: 20 short calls, `maxTokens=300`, input roughly a few hundred tokens → **well under $1**, typically cents. Set provider spend caps anyway.

### Extra calls (not in the 20)

1. **`LLMRouter.query` app retry** (`src/llm/router.ts`): on any throw, `sleep(2000)` then **one** more `queryClaude` / `queryOpenAI`. Comment says “exponential backoff”; it is a **flat 2s**. Auth failures retry too (wasted spend).
2. **SDK `maxRetries` default 2** (OpenAI/Anthropic JSDoc): 429/5xx can be retried inside a single `query*` before it throws. One logical call can be multiple HTTP round-trips. If those exhaust and throw, the app retry runs another SDK sequence.
3. **Zero-balance agents still get a decision call** every remaining round.

Ceiling if every call throws once: **20 + 20 = 40** logical queries (still decision-only).

---

## 4. Do keys go to the LLM, logs, or SQLite?

Three different secrets. Do not mix them.

### A. Model API keys — `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`

| Sink | Sent? | Evidence |
|---|---|---|
| LLM prompt | **No** | `buildArenaPrompt` only has game state / messages |
| stdout / `console.log` | **No** in app code | Query errors log `err.message`, not `process.env` |
| SQLite | **No** | No column, no `store.log` metadata with these names |
| HTTP | **Yes, to the vendor only** | Anthropic `X-Api-Key`; OpenAI `Authorization: Bearer …` |

They live in process env after `dotenv.config`. Not written back to disk by this repo.

### B. Operator Lightning key — `LF_OPERATOR_KEY`

| Sink | Sent? | Evidence |
|---|---|---|
| LLM prompt | **No** | |
| stdout | **No** | Balance logs print sat amounts, not the key |
| SQLite | **No** | Held in `AgentManager.operatorKey` only |
| Lightning Faucet | **Yes, every faucet call** | `AgentManager.request` JSON body `{ action, api_key, ... }` to `https://lightningfaucet.com/api/` |

If unset, constructor stores `''`. First real use is `setup()` → `checkOperatorBalance()` → `whoami`.

### C. Per-agent Lightning API keys (faucet `create_agent` → `api_key`)

Created in `ArenaCoordinator.setup` via `AgentManager.createLightningAgent`. Then:

1. Copied onto `ArenaAgent.lightningApiKey` (memory)
2. **Written to SQLite** by `DataStore.updateAgentLightning` → column `agents.lightning_api_key` (`src/data/schema.sql`, `src/data/store.ts`)

**Not used after that.** `AgentManager.request` has an optional `apiKey` argument; **no caller passes it.** Fund / sweep / list / transfer all use `this.operatorKey`.

Transfers (`transferBetweenAgents`): operator `withdraw_from_agent` then operator `fund_agent`. Not a peer-to-peer invoice with the agent key.

| Sink | Sent? |
|---|---|
| LLM prompt | **No** |
| stdout | **No** (`LN#<id>, funded N sats` only) |
| SQLite `agents.lightning_api_key` | **Yes, plaintext** |
| Faucet after create | **Not from this code** (operator key used instead) |

`DataStore.getAgents` / `getAgent` **read the column back** into `lightningApiKey`. Viz export (`src/data/viz-export.ts`) uses `getAgents` but only copies name / model / priming — it does not emit the key into viz JSON. The **`.db` file itself is the leak surface** (disk, backups, `scp`, accidentally committing `arena-*.db`).

Treat `arena-*.db` like a secret if `setup()` ever completed.

---

## 5. Failure modes: money vs API spend

Closed-loop design (docs/DECISIONS.md D004): operator funds agents, sweeps at **successful** teardown. Net sats ≈ faucet fees, not ≈ 0 if you abort.

### Missing `OPENAI_API_KEY`

- Dies in `new LLMRouter()` inside `ArenaCoordinator.constructor`
- **Money:** none moved (setup never runs)
- **API spend:** none
- Side effect: empty SQLite file may exist

### Missing `ANTHROPIC_API_KEY` (OpenAI key present)

- Process starts. `setup()` **funds 4 × 100 sats** (plus 10% gate: operator must hold ≥ 440)
- Round 1: Claude `queryAgent` fails (auth) → app retry → fail → **PASS**. GPT agents still act and can transfer
- **Money:** in play on Lightning; teardown still sweeps **if** the 5 rounds finish
- **API spend:** failed Claude attempts (2 per agent per round until they keep PASSing) + real GPT calls

Asymmetric and dangerous: OpenAI is fail-fast; Anthropic is fail-late after funding.

### Missing / bad `LF_OPERATOR_KEY`

- OpenAI key must already have passed construct
- `setup()` creates an experiment row, then `checkOperatorBalance()` throws
- `run()` catch sets phase `error`
- **Money:** none funded (wallets created after the balance check)
- **API spend:** none

### Rate limit (429) / transient 5xx

- SDK retries (default 2) inside one `query*`
- If still throwing: app waits 2s and calls `query*` again
- `queryAgent` catch: that agent **PASS**es; **other agents continue**; round is not aborted
- **Money:** unchanged for the failed agent that round; others may still SEND
- **API spend:** billed for successful retries; 429s usually not; the extra app retry can double spend on the same agent-round

### Mid-run crash / Ctrl+C / uncaught Lightning error

Uncaught in `executeOneRound`: `getAgentBalance` / `listAgents` (no try/catch around the snapshot loops). LLM and transfer errors **are** caught.

If `run()` throws or the process dies:

- **`teardown()` does not run** (it is after `executeRounds()` in the same try)
- **No sweep.** Agent wallets keep whatever they hold
- YAML comment is accurate: pool shrink + possible stranding
- Operator must recover manually on the faucet (withdraw_from_agent / UI)
- Crash **between** `sweepAgent` and `fundAgent` inside a transfer: sats sit on the **operator**, destination unpaid; local `agent.balance` is wrong until the next snapshot

SQLite may already have partial rounds (`phase` stays `running` unless the catch ran). LLM spend already billed is gone.

### Successful teardown

`teardown()`: final balances, local analysis, **sweep each agent with balance > 0**, print LLM stats, `phase = completed`. Sweep failures are logged per agent; remaining sats stay on that wallet.

Faucet fee note (from existing Burton ops docs, not re-verified here): payments **>100 sats ~1%**. Valedia stakes are 100; transfers of 100 may or may not hit that. Treat 400 funded sats as **burnable**, not a closed loop.

---

## 6. Claude-only without `OPENAI_API_KEY`?

**No. Not today.** Two independent blockers:

1. **Code:** `LLMRouter.constructor` always `new OpenAI(...)`. Missing env → throw, even if every YAML agent is `model: claude`.
2. **This YAML:** two of four agents are `openai` / `gpt-4o`. Lazy init alone would still 500 those agents (they would PASS after retry).

### Minimal code change (lazy OpenAI)

In `src/llm/router.ts`: construct Anthropic as now (or also lazy). Construct OpenAI **only** in `queryOpenAI` when `request.model !== 'claude'`.

Sketch (not applied — audit-only):

```ts
export class LLMRouter {
  private claude: Anthropic;
  private openai: OpenAI | null = null;
  // ...

  constructor() {
    this.claude = new Anthropic({
      apiKey: process.env.ANTHROPIC_API_KEY,
    });
  }

  private getOpenAI(): OpenAI {
    if (!this.openai) {
      this.openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
    }
    return this.openai;
  }

  private async queryOpenAI(...) {
    const response = await this.getOpenAI().chat.completions.create({ ... });
    // unchanged
  }
}
```

That is enough for an **all-Claude YAML** with only `ANTHROPIC_API_KEY` + `LF_OPERATOR_KEY`.

To run **this** Valedia file Claude-only, also change the two `model: openai` groups to `claude` (config, not code). Mixed-model is the point of the micro-pilot, so that is a different experiment.

Optional hardening (not required for Claude-only):

- Fail-fast if `ANTHROPIC_API_KEY` is missing **before** `setup()` funds wallets
- Do not retry on auth errors in `LLMRouter.query`
- Stop writing `lightning_api_key` to SQLite, or encrypt it, since it is unused
- Sweep in `run()` `finally` / `SIGINT`, not only on clean teardown
- Fix README `FAUCET_OPERATOR_KEY` → `LF_OPERATOR_KEY`

---

## Key symbols (index)

| Symbol | File |
|---|---|
| `LLMRouter` / `query` / `queryClaude` / `queryOpenAI` / `getStats` | `src/llm/router.ts` |
| `ArenaCoordinator` / `setup` / `executeOneRound` / `queryAgent` / `executeTransfer` / `teardown` | `src/arena/coordinator.ts` |
| `buildArenaPrompt` / `getPrimingText` / `ARENA_SYSTEM_PROMPT` | `src/arena/prompts.ts` |
| `parseArenaActions` / `applyRateLimits` | `src/arena/action-parser.ts` |
| `AgentManager` / `request` / `createLightningAgent` / `transferBetweenAgents` | `src/agents/manager.ts` |
| `DataStore.updateAgentLightning` / `recordArenaAction` | `src/data/store.ts` |
| `agents.lightning_api_key` | `src/data/schema.sql` |
| Valedia config | `experiments/arena-valedia-micro-pilot.yaml` |
| Classic extra LLM parse-retry | `src/games/engine.ts` `getAgentDecision` |

---

## What this audit did not do

- Did not read `.env`, recovery codes, or live key values
- Did not query `arena-valedia-micro-pilot.db` for `lightning_api_key` (if `setup()` ever ran, that column is sensitive)
- Did not call Anthropic, OpenAI, or lightningfaucet.com
- Did not change production code
