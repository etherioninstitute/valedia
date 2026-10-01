# Independent security audit — SebAI Lightning Economic Arena (`lf-game-theory`)

**Auditor:** Grok 4.6 (Grok Build), independent source review  
**Date:** 2026-09-14  
**Scope:** CODE/AUDIT ONLY of this repo, with emphasis on `npm run arena:valedia-micro` (`experiments/arena-valedia-micro-pilot.yaml`).  
**Method:** Read-only review of `src/**`, experiment YAML, schema, docs, `.gitignore`, `.env.example`, `package.json` / lockfile. `npm audit` (no installs that change security posture). SQLite **row counts only** (no `lightning_api_key` values, no `.env`, no recovery codes). Public Lightning Faucet OpenAPI / `llms.txt` used to understand upstream capabilities. **No coordinator run with real keys. No Anthropic / OpenAI / faucet calls. No wallet funding.**

**Prior reviews:** `SEBAI-ARENA-SECURITY-AUDIT.md` and `SEBAI-ARENA-VALEDIA-PLAN.md` are **not in this tree** (referenced by `BURTON-PILOT-RUN.md` only). This review does **not** rely on them. `BURTON-ARENA-LLM-KEYS.md` is a prior Grok keys-path note; agreed/disagreed below from source, not copied as authority.

---

## Verdict

**CONDITIONAL GO** for a **~440–1000 sat** Valedia micro-pilot (`4 × 100 sats + 10% operator gate`).

**NO-GO** for Arena Full (`experiments/arena-full.yaml`: 16 × 1000 sats, 100 rounds) until the micro-pilot teardown is green **and** the High items below are mitigated or explicitly accepted.

This is a local CLI, not a network service. The model cannot shell out, cannot SSRF, and cannot call `pay_invoice` through the coordinator. Residual risk is **operator error + crash stranding + third-party custody + agent API keys on disk**. At ≤1000 sats that is an acceptable research burn **if** the mitigations in §8 are followed.

---

## 1. Trust boundaries

```
                    ┌─────────────────────────────────────────┐
                    │  Untrusted: LLM text (Claude / GPT-4o)  │
                    │  system+user prompts  →  model output   │
                    └──────────────────┬──────────────────────┘
                                       │ HTTPS
                                       │ ANTHROPIC_API_KEY / OPENAI_API_KEY
                                       ▼
┌──────────── local, operator-trusted ──────────────────────────────────────┐
│  ArenaCoordinator (src/arena/coordinator.ts)                              │
│    parseArenaActions allowlist  →  SEND / MESSAGE / BROADCAST / PASS      │
│    DataStore (src/data/store.ts)  →  SQLite arena-*.db (WAL)              │
│    AgentManager (src/agents/manager.ts)  →  LF_OPERATOR_KEY in memory     │
└──────────────────────────────────────┬────────────────────────────────────┘
                                       │ HTTPS POST JSON {action, api_key, …}
                                       │ hardcoded API_URL
                                       ▼
                    ┌─────────────────────────────────────────┐
                    │  Untrusted-as-custodian: Lightning      │
                    │  Faucet (lightningfaucet.com)           │
                    │  operator ledger + agent sub-wallets    │
                    └─────────────────────────────────────────┘
```

| Boundary | What crosses | Who is trusted |
|---|---|---|
| Operator ↔ `.env` / process env | `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `LF_OPERATOR_KEY` | Local operator only. `dotenv.config({ override: true })` in `ArenaCoordinator` / `Coordinator` constructors. |
| Coordinator ↔ Anthropic / OpenAI | Prompts (rules, balances, peer messages) and completions | Providers see experiment text. They do **not** receive Lightning keys (verified: prompts built in `src/arena/prompts.ts` `buildArenaPrompt`). |
| Coordinator ↔ SQLite | Experiment config YAML, agent metadata, **plaintext agent API keys**, full prompts/responses, transfers, messages | Local filesystem. `.gitignore` has `.env` and `*.db`. Constructor creates `arena-<name>.db` **before** LLM keys are validated. |
| Coordinator ↔ Lightning Faucet | Operator key on **every** faucet call. Agent keys created then stored, **not** used for subsequent calls. | Custodial third party. Internal `fund_agent` / `withdraw_from_agent` are ledger moves on their node. |
| LLM output ↔ money | Only if `parseArenaActions` + `applyRateLimits` emit a valid `send`, then `executeTransfer` | Parser is the enforcement point. Models never hold tools. |

There is **no HTTP listener** in `src/` (no `createServer` / `.listen` / Express). `viz/index.html` is static. Attack surface is local process + outbound HTTPS + disk.

Secondary path: `src/coordinator.ts` (structured games) is **not** invoked by `arena:valedia-micro`. Same `AgentManager` money primitive. Noted where it shares bugs.

---

## 2. Secrets

### Inventory

| Secret | Created | Stored | Sent to models | Logged | Used |
|---|---|---|---|---|---|
| `ANTHROPIC_API_KEY` | Operator / dashboard | process env only (`LLMRouter` constructor) | No | No (not in `console.log` of router) | `claude.messages.create` |
| `OPENAI_API_KEY` | Operator / dashboard | process env only | No | No | `openai.chat.completions.create` |
| `LF_OPERATOR_KEY` | Faucet operator registration | process env → `AgentManager.operatorKey` (memory) | No | No | **Every** `AgentManager.request` body field `api_key` |
| Per-agent faucet `api_key` | `createLightningAgent` → `create_agent` | Memory (`ArenaAgent.lightningApiKey`) **and SQLite** `agents.lightning_api_key` via `DataStore.updateAgentLightning` | No | **Not** printed (setup logs `LN#id` + funded amount only) | **Unused after creation.** No caller passes `apiKey` into `request()`. |

### Fail-closed vs fail-open

- **No application-level env validator.**
- `new OpenAI({ apiKey: process.env.OPENAI_API_KEY })` throws if unset → process dies in `ArenaCoordinator` constructor **after** SQLite file creation. Matches the documented dry-run.
- Anthropic SDK does **not** throw at construct; first Claude `messages.create` fails later.
- `new AgentManager(process.env.LF_OPERATOR_KEY || '')` stores empty string; first faucet `whoami` (`checkOperatorBalance`) fails during `setup()`.

**README vs code:** `README.md` documents `FAUCET_OPERATOR_KEY`. Code and `.env.example` use `LF_OPERATOR_KEY`. The README name is dead. Using only the README would fund nothing and then fail at `whoami` — or, worse, an operator might paste the key into a variable the process never reads and assume they are protected.

`.env.example` placeholder prefix is `op_xxxxx`. Public faucet docs use `lf_` (operator) and `agent_` (agent). Cosmetic, but it is a copy-paste trap.

### Leak surfaces

1. **SQLite plaintext agent keys (High if a run completes `setup()`).** Schema: `src/data/schema.sql` column `agents.lightning_api_key`. Written by `DataStore.updateAgentLightning`. `getAgents` / `getAgent` read it back. Viz export copies name/model/priming only (does not emit the key into JSON) — the **`.db` file** is the leak. Local `arena-valedia-micro-pilot.db` currently has **0 rows** (constructor-only schema init); file mode `rw-r--r--` (0644). After a live `setup()`, world-readable agent keys on a multi-user host is a real issue.
2. **Faucet capability of those keys (High, conditional on leak).** Upstream OpenAPI (`x-actions.agent`) lets an `agent_` key: `pay_invoice`, `l402_pay` (arbitrary URL), `pay_lightning_address`, `keysend`, `create_invoice`. Arena never sets `budget_limit_sats` (`createLightningAgent` called with name only). A stolen agent key can **exit the closed loop** up to that agent's balance. Coordinator never calls those actions; theft requires the SQLite/disk/memory secret.
3. **Operator key** is more powerful (`withdraw` to any BOLT11, `fund_agent`, `create_agent`, `withdraw_from_agent`). It is not written to SQLite. It **is** in every faucet POST body over TLS. It is not logged by this repo.
4. **Prompt leakage: none for secrets.** `buildArenaPrompt` injects identity, goal, priming, balance, round, agent names, rate limits, and quoted peer messages. No env, no keys, no faucet IDs.
5. **LLM provider logs:** prompts/responses (including agent-to-agent social engineering) go to Anthropic/OpenAI. Acceptable for this experiment; not a key leak.
6. **`.env`:** gitignored. Not opened in this audit. `BURTON-PILOT-RUN.md` already tells the operator `chmod 600 .env`.

**Agree with `BURTON-ARENA-LLM-KEYS.md`:** LLM keys are not stored in SQLite; operator key is in-memory; agent keys are stored and unused. **Add:** unused ≠ harmless — those keys are spend-capable at the faucet.

---

## 3. Action allowlist / prompt injection

Enforcement is local regex, not an LLM-as-parser.

**Parser:** `src/arena/action-parser.ts` `parseArenaActions` / `parseSingleAction` / `applyRateLimits`.

Allowed types (`ArenaActionType`): `send` | `message` | `broadcast` | `pass`.

| Action | Match | Bound |
|---|---|---|
| `SEND <n> TO Agent-<id>` | `/^SEND\s+(\d+)\s+TO\s+(Agent-\d+)/i` (not `$`-anchored) | Target must be in `validAgentNames`; not self; `amount > 0`; later `minTransferAmount` + local balance in `executeTransfer` |
| `MESSAGE Agent-<id> …` | quoted or rest-of-line | Target must exist; not self; content capped at **500** chars |
| `BROADCAST …` | quoted or rest-of-line | content capped at **500** |
| `PASS` | exact or prefix | no-op |
| Anything else (`//`, `#`, `*`, commentary) | skipped | empty parse → synthetic `pass` |

**Rate limits (Valedia YAML):** 1 transfer, 2 private messages, 1 broadcast per round (`applyRateLimits`). Excess is rejected and `store.log`'d; not sent to Lightning.

**Can model output escape the allowlist?**

| Primitive | Result |
|---|---|
| Shell / `child_process` / `eval` / `Function` | **No.** None in `src/` except `db.exec(schema)` of a static SQL file. |
| Path traversal via SEND/MESSAGE | **No.** Targets are `Agent-\d+` names resolved through `MessageRouter.nameMap`. |
| SSRF via model text | **No.** Sole `fetch` is `AgentManager.request` to a **hardcoded** `API_URL`. Model cannot set URL, action, or `api_key`. `l402_pay` is not wired. |
| Extra faucet actions (`pay_invoice`, `withdraw`, `delete_agent`) | **No** from LLM output. `deleteAgent` / `setBudget` / `deactivateAgent` exist on `AgentManager` but have **zero callers** in arena (or games) paths. |
| Integer bombs | `parseInt` on `\d+`. `Infinity` fails the balance check (`balance < amount`). No max-amount cap other than balance; an agent may donate its entire stack — **intended**. |
| Trailing junk on SEND | Not end-anchored, so `SEND 10 TO Agent-1; curl evil` still executes the SEND and **ignores** the junk (not executed). |

**Prompt injection (peer → peer):** MESSAGE/BROADCAST content is interpolated into the next round's **user** prompt as `"${msg.content}"` (`buildArenaPrompt`). Content is not sanitized for quote-breaking. A model can jailbreak a peer into emitting `SEND`. That is the experiment (negotiation / betrayal). It cannot jailbreak the **coordinator**: coordinator parses the peer's **output** through the same allowlist, still 1 transfer/round. Max theft per round = one agent's current balance (100 sats at Valedia start).

**No parser tests** exist in the repo (no `*test*` / `*spec*` besides `experiments/priming-test.yaml`).

Games path (`src/llm/parser.ts`) is looser (`includes('cooperate')`, first `\d+` wins) but is not on the Valedia money path.

---

## 4. Money path

Closed-loop by design (docs/DECISIONS.md D004): operator → agents → (internal moves) → operator. Net expected cost = faucet fees, not principal — **if teardown runs**.

### Fund (`ArenaCoordinator.setup`)

1. `checkOperatorBalance()` → faucet `whoami` → `balance_sats`.
2. Gate: `actualBalance < totalRequired * 1.1` throws (Valedia: 400 × 1.1 = **440**).
3. Per agent: `createLightningAgent` then `fundAgent(id, startBal)` then `updateAgentLightning`.
4. Sequential with `sleep(200)`. **Not transactional.** Crash after *k* of 4 leaves *k* funded faucet agents and no sweep (see §7).

**Budget limit is never set.** `createLightningAgent(name, budgetLimit?)` is called with one argument. Docs (`docs/METHODOLOGY.md`, `docs/MCP_SHOWCASE.md`) claim 2× funding budgets; **code does not**. `setBudget` is dead.

### Transfer (`executeTransfer` → `AgentManager.transferBetweenAgents`)

```109:113:src/agents/manager.ts
  async transferBetweenAgents(fromAgentId: number, toAgentId: number, amount: number): Promise<void> {
    // Two-step: sweep from source agent to operator, then fund destination
    await this.sweepAgent(fromAgentId, amount);
    await this.fundAgent(toAgentId, amount);
  }
```

This is **not atomic**. Official MCP client exposes `transfer_between_agents` / `transferToAgent`. This repo reimplements P2P as `withdraw_from_agent` + `fund_agent`.

| Failure | Custody result |
|---|---|
| Sweep fails | No move. Local balances unchanged. Transfer recorded `success: false`. |
| Sweep OK, fund fails / process dies | Sats sit on **operator**, not on sender or recipient. Arena local balances still show the old sender balance. Later SENDs from sender may fail at faucet. Economy accounting is wrong; operator is not poorer. |
| Both OK | Local balances adjusted after await. Next snapshot overwrites from faucet `list_agents`. |

Balance checks use **stale local** `fromAgent.balance` (snapshotted at round start, then mutated). Execution is shuffled but **serial**, so two agents sending their full stacks is ordered, not parallel. Lightning is not re-read between transfers in a round.

**Fees:** Public faucet docs: L402 1% (waived under 100 sats); Lightning `withdraw` to invoice 1% + routing, min 10 sats; deposits free. Internal `withdraw_from_agent` is documented as a ledger pull-to-operator. If that were ever classified as a Lightning withdrawal, a 100-sat micro-pilot would be fee-destroyed (min 10 sats **per SEND**). `docs/METHODOLOGY.md` claims internal ops have **no** routing fee. **Treat as unverified until the micro-pilot's first SEND.** If internal transfers fee, stop and switch to a single faucet P2P action.

`docs/MCP_SHOWCASE.md` says `transferBetweenAgents()` uses `transfer_to_agent`. **Disagree — the source does not.**

### Teardown sweep (`ArenaCoordinator.teardown`)

- Re-reads each `getAgentBalance`, `sweepAgent` if `balance > 0`.
- Errors are logged; loop continues; experiment still marked `completed`.
- **`deleteAgent` is never called.** Empty (or un-swept) faucet agents persist under the operator forever.
- **`run()` catch path does not sweep** (see §7).

### Games path (not Valedia, same `AgentManager`)

`BaseGame.runSession` → `distributePayoff` (fund if payoff > 0, sweep if < 0) with no pre-check that the agent still holds the debit. `OperatorPool.recoverAll` is the games teardown. Same two-step and no-signal issues.

---

## 5. Network / TLS / hosts

### Application outbound (only these)

| Host | How | Allowlisted? |
|---|---|---|
| `https://lightningfaucet.com/api/` | `const API_URL` in `src/agents/manager.ts`; `fetch` POST | Hardcoded. **Not** the URL in current faucet docs. |
| `api.anthropic.com` (SDK default) | `@anthropic-ai/sdk` | SDK default; no `baseURL` override in repo |
| `api.openai.com` (SDK default) | `openai` | SDK default; no `baseURL` override |

No `http://`, no `rejectUnauthorized: false`, no `NODE_TLS_REJECT_UNAUTHORIZED` in source. Node 20+ `fetch` verifies WebPKI. **No cert pinning. No `redirect: 'error'`.** Default redirect-follow: a 307/308 from `/api/` to another host would forward the POST body including `api_key`. Same-site 30x is the realistic case.

### Endpoint drift (Medium — verify before funding)

| Source | Agent Wallet URL |
|---|---|
| **This repo** | `https://lightningfaucet.com/api/` |
| Faucet `llms.txt` / OpenAPI | `https://lightningfaucet.com/ai-agents/api.php` (server `https://lightningfaucet.com/ai-agents`) |
| Official MCP client | `https://lightningfaucet.com/ai-agents/api` |

Same registrable domain, different path. `/api/` is also used in the wild as an L402 action bus. This environment's dry-run **never reached faucet** (died on missing `OPENAI_API_KEY`). **Live `whoami` against this URL is untested here.** If `/api/` is an alias, OK. If it is a different handler, setup fails (best) or the operator key is posted to an unexpected same-origin script (worse). Pre-flight: operator should confirm `whoami` on the URL the **code** uses, or change `API_URL` to the documented path **before** depositing.

### Other network gaps

- **No fetch timeout / AbortController.** Official MCP client uses 45s. A hung `sweepAgent` mid-transfer strands funds in the two-step window and hangs the process until kill.
- **No HTTP status check.** `response.json()` is called even on 401/500 HTML. MCP client throws on `!response.ok` without echoing `statusText`.
- `params` are spread **after** `api_key` in the JSON body (`{ action, api_key, ...params }`). No current caller supplies `api_key` in params; it is a footgun if someone later puts LLM-influenced fields in `params`.
- LLM `query` retries **once** after 2s; faucet calls do not retry (good for payments — retries after a timeout can double-spend; MCP docs warn of `pending:true`).

Unexpected outbound hosts from **this application's** source: **none**.

---

## 6. Dependencies (`npm audit`)

```
npm audit --omit=dev   →  0 vulnerabilities (84 prod deps)
npm audit              →  0 vulnerabilities (195 total)
```

Runtime deps: `@anthropic-ai/sdk ^0.39.0`, `better-sqlite3 ^11.7.0`, `dotenv ^17.2.3`, `openai ^4.77.0`, `uuid ^11.0.5`, `yaml ^2.7.0`.

`yaml@2` does not eval JS tags. SQL uses prepared statements (`store.ts`). Native `better-sqlite3` is the usual supply-chain / Node-ABI risk (BURTON-PILOT-RUN: Node 24.20.0, not 26). No audit-fix needed; **do not "helpfully" bump SDKs mid-pilot**.

No test runner in `package.json`. `db:init` points at `dist/data/init-db.js` which has no `src/` counterpart — dead script, not a vuln.

---

## 7. Crash / kill / partial-run recovery

**There is no resume.** No `SIGINT` / `SIGTERM` handler. No reconstruction of in-memory agent maps from SQLite. A second invocation always:

1. Creates a **new** experiment UUID and **new** faucet agents.
2. Funds them again if operator balance still clears the 1.1× gate.

```42:56:src/arena/coordinator.ts
  async run(): Promise<void> {
    ...
    try {
      await this.setup();
      await this.executeRounds();
      await this.teardown();
    } catch (err) {
      console.error('Arena failed:', err);
      this.store.updateExperimentPhase(this.experimentId, 'error');
      throw err;
    }
  }
```

| Event | Sweep? | Result |
|---|---|---|
| Missing `OPENAI_API_KEY` in constructor | n/a | Empty `arena-*.db` created. No faucet calls. (Current local DB: 0 rows.) |
| `whoami` / insufficient balance | n/a | No agents funded. |
| Crash / throw **during** `setup()` funding loop | **No** | 1..n agents funded on faucet, keys maybe in SQLite, phase `setup` or `error`. **Stranded.** |
| Throw during rounds (LLM 5xx, faucet error) | **No** (catch does not call teardown) | Agents hold sats. Phase `error`. **Stranded.** |
| Ctrl-C / `kill -9` | **No** | Same. Node does not run `catch`. |
| Teardown sweep fails for one agent | Partial | Marked `completed` anyway. That agent remains funded. |
| Re-run without sweeping leftovers | Funds **new** agents | Operator can double-allocate remaining balance. Old agents still hold the first endowment. |

Recovery is **manual on the faucet UI** (`list_agents` / `withdraw_from_agent`), as `BURTON-PILOT-RUN.md` already warns. SQLite `lightning_agent_id` (if `setup()` got that far) is the join key; agent API keys in that DB are enough to spend **out** if someone uses them as agent credentials instead of sweeping as operator.

YAML `name` → DB path: `` `arena-${name.replace(/\s+/g,'-').toLowerCase()}.db` ``. A YAML `name: "../../.ssh/id_rsa"` writes outside cwd. Operator-controlled, not LLM-controlled. Low.

---

## 8. Mitigations (required for CONDITIONAL GO)

Do these as **operator procedure**. This audit does not change code.

1. **Cap at Valedia YAML.** Do not raise `startingBalance`, agent count, or rounds until teardown prints a sweep total and faucet operator balance matches leftovers minus documented fees.
2. **Fund 440–1000 sats only.** Treat as burnable. Confirm operator balance in the faucet UI **before** start.
3. **Stay attached.** Do not Ctrl-Z, `kill -9`, close the laptop lid, or let the process die mid-round. If it dies: faucet UI → list agents → `withdraw_from_agent` / sweep **before** any re-run.
4. **Never re-run `arena:valedia-micro` until leftovers are swept.** A second start creates four **new** wallets.
5. **Keys:** `chmod 600 .env`. After a live `setup()`, `chmod 600 arena-*.db` (file is currently 0644). Do not commit, scp, or zip the DB. `.gitignore` already has `*.db` and `.env`.
6. **Env names:** set `LF_OPERATOR_KEY` (not `FAUCET_OPERATOR_KEY`). Set Anthropic + OpenAI **dashboard spend caps** before start.
7. **Pre-flight faucet URL (operator):** one `whoami` against the URL in `src/agents/manager.ts` (`https://lightningfaucet.com/api/`). If it fails, point `API_URL` at `https://lightningfaucet.com/ai-agents/api.php` and rebuild **before** depositing. This audit did not perform that call.
8. **After run:** confirm operator balance; sweep any remaining agent balances; delete or encrypt `arena-*.db`; only then consider a larger YAML.
9. **Accept custody:** funds live at Lightning Faucet, not in a wallet you control. Do not park more than the pilot.

**Code fixes worth doing before any 16×1000 run (not required to burn ≤1000 sats):**

- SIGINT/SIGTERM → best-effort sweep; `catch` in `run()` should attempt teardown too.
- Stop writing `lightning_api_key` to SQLite, or chmod/encrypt the DB in `DataStore`.
- Pass `budget_limit_sats = startingBalance` (or 0 spend if faucet treats 0 as unlimited — MCP says 0 = unlimited, so use a **positive** cap).
- Replace two-step transfer with faucet `transfer_between_agents` if that action is live.
- Fail-fast env check for all three keys **before** `createLightningAgent`.
- `fetch` timeout + `redirect: 'manual'` + `!response.ok` handling; align `API_URL` with OpenAPI.
- Fix README `FAUCET_OPERATOR_KEY`.
- Parser unit tests (self-send, unknown agent, trailing junk, 500-char cap, rate limits).

---

## 9. Findings list (severity)

| ID | Sev | Finding | Where |
|---|---|---|---|
| G1 | **High** | Crash / throw / SIGINT does not sweep. Re-run double-funds new agents. | `ArenaCoordinator.run` catch; no signal handlers |
| G2 | **High** | Per-agent faucet keys stored plaintext in SQLite, unused, but **spend-capable** (`pay_invoice` / `l402_pay` / keysend) with **no budget cap** | `schema.sql`, `updateAgentLightning`, `createLightningAgent` |
| G3 | **High** | Two-step transfer is non-atomic; mid-failure strands sats on operator and desyncs the arena ledger | `transferBetweenAgents` |
| G4 | Med | Faucet URL in code ≠ current official Agent Wallet path; untested in this env | `API_URL = 'https://lightningfaucet.com/api/'` |
| G5 | Med | No fetch timeout, no HTTP status check, default redirect-follow on requests that carry `api_key` | `AgentManager.request` |
| G6 | Med | README `FAUCET_OPERATOR_KEY` vs code `LF_OPERATOR_KEY`; empty operator key fail-open until `whoami` | `README.md`, constructors |
| G7 | Med | Docs claim `set_budget` / `transfer_to_agent` / 2× budgets; arena implements none | `MCP_SHOWCASE.md`, `METHODOLOGY.md` vs `coordinator.ts` |
| G8 | Med | Teardown never `delete_agent`; sweep errors still mark `completed` | `teardown()` |
| G9 | Low | SQLite created 0644; after live setup that is a local read of spend keys | `arena-*.db` |
| G10 | Low | YAML `name` → DB relative path; `../` traversal | `new DataStore(\`arena-${name}...\`)` |
| G11 | Low | Peer prompt-injection via MESSAGE/BROADCAST (experiment-intended); 500-char cap only | `prompts.ts`, `action-parser.ts` |
| G12 | Low | `api_key` spread-overridable by `params`; no tests for parser | `request()`, repo |

**Not findings (explicitly tested by reading source):** LLM cannot spawn a shell, cannot fetch arbitrary URLs, cannot select faucet `action`, cannot read `.env` into a prompt. SQL is parameterized. `npm audit` is clean. No HTTP server. `.env` / `*.db` gitignored; `.env.example` is placeholders only.

---

## 10. Prior review — agree / disagree

`SEBAI-ARENA-SECURITY-AUDIT.md` is **absent**. Cannot agree/disagree with Valedia's write-up.

`BURTON-PILOT-RUN.md` GO table (code prep GO; funded micro-pilot CONDITIONAL GO; 16×1000 NO-GO until teardown green) — **agree.**

`BURTON-ARENA-LLM-KEYS.md` — **agree** on: OpenAI key crash-at-construct; Anthropic/LF fail later; LLM keys not in SQLite/prompts; operator key in-memory; agent keys stored unused; no resume; README name dead. **Disagree / extend:** “unused after creation” understates risk — those keys are enough to `pay_invoice` out of the closed loop if the DB leaks; crash path is not just “operator recovers,” it is a **missing teardown in `catch`**; money-path atomicity and URL drift are in-scope for a security audit, not only a keys note.

---

## 11. What this audit did not do

- Did not read `.env`, recovery codes, or any `lightning_api_key` **values**.
- Did not run `arena:valedia-micro` with keys, fund wallets, or call Anthropic / OpenAI / lightningfaucet.com.
- Did not verify live whether `https://lightningfaucet.com/api/` aliases `/ai-agents/api.php`.
- Did not verify live whether `withdraw_from_agent` is fee-free.
- Did not review `node_modules` beyond `npm audit`.
- Did not audit the faucet's own custody / LND / KYC posture beyond public docs (third-party risk is accepted at micro-scale).

---

## 12. Pilot envelope (for the operator)

| Item | Value |
|---|---|
| Config | `experiments/arena-valedia-micro-pilot.yaml` |
| Command | `npm run arena:valedia-micro` |
| Size | 2 Claude + 2 GPT-4o, 5 rounds, 100 sats each |
| Operator gate | ≥ 440 sats (code: `totalRequired * 1.1`) |
| Recommended deposit | 500–1000 sats, burnable |
| Success | Setup → 5 rounds → teardown sweep log → operator balance ≈ leftovers |
| Abort | Any kill/crash → faucet UI sweep **before** another `npm run` |

**Verdict: CONDITIONAL GO** for that envelope. **NO-GO** above it until G1–G3 are fixed or a green micro teardown has been observed.
