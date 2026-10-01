---
title: SebAI AI Economic Arena Setup Guide
source: Downloads/SebAI-AI-Economic-Arena-Guide.pdf
author: "@seb.ai (SebAI)"
upstream_repo: https://github.com/pfergi42/lf-game-theory
converted: 2026-09-14
warning: REAL Lightning sats — no simulation/testnet mode in upstream code
---

# SebAI — AI Economic Arena Setup Guide

> Converted from `SebAI-AI-Economic-Arena-Guide.pdf` (by @seb.ai).
> **Upstream experiment:** [pfergi42/lf-game-theory](https://github.com/pfergi42/lf-game-theory) (Paul Ferguson, Jan 2026).
> **Critical:** uses **real Bitcoin Lightning sats**. No sim/testnet mode in current upstream code.

## Valedia hard-fence note

- RuFlo / Grok Build / this chat: **code & audit only** — do not store `LF_OPERATOR_KEY`, do not fund wallets, do not sweep sats.
- **Burton / Valedia runtime** is the sole live-risk authority for any real Lightning funding.
- Prefer a paper/sim fork for harness work before any funded run.

## Flow (from guide)

1. **FUND** — operator wallet gets a few thousand real sats (Lightning)
2. **16 WALLETS** — script creates one wallet per agent; funds each with 1,000 sats
3. **100 ROUNDS** — each agent can SEND sats · MESSAGE · BROADCAST · PASS
4. **NEGOTIATE · ALLY · BETRAY** — free-form, anonymous, balances private
5. **RESULTS** — final balances, Gini, transfer network → console + SQLite

## Full guide text (from PDF)

```
SETUP GUIDE · BY @SEB.AI

HOW TO RUN 16 AI AGENTS IN A BITCOIN ECONOMY
RECREATE THE "AI ECONOMIC ARENA" EXPERIMENT — 16 AGENTS, 100 ROUNDS, REAL SATS ON
THE LINE

                               FUND your operator wallet gets a few thousand real sats
                                                     (Lightning)
                                                          ↓
                              16 WALLETS the script creates one wallet per agent and
                                              funds each with 1,000 sats
                                                          ↓
                              100 ROUNDS each agent can SEND sats · MESSAGE ·
                                                BROADCAST · PASS
                                                          ↓
                             NEGOTIATE · ALLY · BETRAY free-form, anonymous,
                                                   balances private
                                                          ↓
                             RESULTS final balances, Gini, transfer network → console +
                                                       SQLite

  READ FIRST
  Experimental research code. This is one researcher's experiment (Paul Ferguson, Jan 2026), not
  a product. It uses REAL Bitcoin: there is no simulation or testnet mode in the current code. The amounts
  are small (≈17,600 sats for a 16-agent run) but they are real and agents can lose them. The README's
  results (e.g. "strategic agents nearly doubled") are that one run's measurements, not something you
  should expect to repeat.

1 WHAT THIS DOES
Sixteen AI agents (the shipped config uses 8 Claude Sonnet 4.5 + 8 GPT-4o) are dropped into a closed
economy for 100 rounds. Each round every agent can send sats to another agent, message one privately,
broadcast to everyone, or pass — so they negotiate, form alliances, and betray each other. Their goal, literally
in the prompt: "Maximize your Bitcoin (satoshi) balance by the end of the game."

 Real Bitcoin                                            Game mechanics

 Every agent has a real Lightning                        Anonymous names, private balances, per-round
 wallet (Lightning Faucet). Transfers                    action limits (1 transfer, 2 private messages, 1
 between agents move real sats. Your                     broadcast), elimination at 0 sats, the "final round"
 operator wallet funds them and sweeps                   announcement, and the optional strategy-priming
 leftovers back at the end.                              text are all rules enforced by the script.

---

2 WHAT YOU NEED
☐ A computer (Mac, Windows, or Linux) with a terminal.
☐ Git and Node.js 20+ (package.json requires ≥ 20).
☐ Anthropic API key AND OpenAI API key — the code creates both clients at startup, so both
    must be set even if you only use one model. Only Claude and OpenAI models are supported.
☐ Lightning Faucet operator key (lightningfaucet.com/ai-agents → Register as Operator) with a
    funded balance: agents × starting sats × 1.1. For 16 × 1,000 that's 17,600 real sats. The code
    refuses to start with less.
☐ Budget for API calls: 16 agents × 100 rounds = 1,600 LLM calls per run. Check your provider
    pricing before starting.
☐ Optional: Python 3 + pandas/matplotlib for the analysis scripts, DB Browser for SQLite to read
    results.

3 DOWNLOAD IT
  git clone https://github.com/pfergi42/lf-game-theory.git
  cd lf-game-theory
  npm install
  npm run build

Skip the README's "npm run db:init" — that script points to a file that doesn't exist in the current repo. The database
is created automatically on first run.

4 SETUP
Create a file named .env in the lf-game-theory folder (it's git-ignored). Use the variable names from
.env.example — note the README's FAUCET_OPERATOR_KEY is wrong; the code reads
LF_OPERATOR_KEY.

  # .env — private; never paste it into a chat or screenshot
  ANTHROPIC_API_KEY=sk-ant-YOUR_KEY_HERE
  OPENAI_API_KEY=sk-YOUR_KEY_HERE
  LF_OPERATOR_KEY=op_YOUR_OPERATOR_KEY_HERE

1. Get the operator key: lightningfaucet.com/ai-agents → Register as Operator → copy the key
into .env.
2. Fund the operator wallet by paying a Lightning invoice from any Lightning wallet (≈17,600 sats for
the full run; more for a buffer).
3. Never put wallet seed phrases, passwords, or your .env contents into Claude, ChatGPT, or any AI
chat — the agents only ever receive their own per-agent keys from the script.

---

5 RUN THE EXPERIMENT
Start small. A run creates and funds wallets immediately, so do a cheap test before the full thing.

  npm run arena:mixed-pilot             # 8 agents, 10 rounds (≈8,800 sats)
  npm run arena:full                    # 16 agents, 100 rounds, all neutral
  npm run arena:mixed                   # 16 agents, 100 rounds, 4 priming groups

You'll see the phases in your terminal: Setup (operator balance check → wallets created and funded) → Execute
("Round 1 of 100" …) → Teardown (final balances, sweep back to operator) → Analysis (Gini, transfer network,
factions, start → end per agent).

6 THE 16-AI CHALLENGE
16 agents · 100 rounds · same starting balance · one goal: finish richest. This is exactly what
experiments/arena-full.yaml already does. To make it yours, copy it and edit the numbers — all of
these fields are real config options read by the script.

  cp experiments/arena-full.yaml experiments/my-arena.yaml

  # experiments/my-arena.yaml — the lines that matter
  name: "My 16-AI Challenge"
  totalRounds: 100
  startingBalance: 1000       # sats per agent (same for everyone)
  agents:
    - count: 8
      model: claude
      modelId: claude-sonnet-4-5-20250929
    - count: 8
      model: openai
      modelId: gpt-4o
  rules:
    maxTransfersPerRound: 1
    maxPrivateMessagesPerRound: 2
    maxBroadcastsPerRound: 1
    minTransferAmount: 1
    announceFinalRound: true
  temperature: 0.7
  revealModelType: false      # true = agents learn who is Claude/GPT

  node dist/arena/coordinator.js --config=experiments/my-arena.yaml

 You want to…                                                Can you?

 Change agent count, rounds, starting                        YES — edit the YAML above.
 sats, action limits, temperature

 Mix Claude and OpenAI models, choose                        YES — one agents: group per model.
 model IDs

---

 You want to…                                                 Can you?

 Add Gemini, Llama, or other providers                        NO — src/llm/router.ts supports only claude and
                                                              openai.

 Run with fake / test sats                                    NO — no simulation mode; every run uses real
                                                              Lightning sats.

 Give agents different starting balances                      YES — startingBalance: per agents: group (see
                                                              arena-mixed-priming-asymmetric.yaml).

 Give agents a custom prompt                                  PARTLY — see Section 7 (edit one text, rebuild).

7 COPY-PASTE AGENT PROMPT
The core rules, action format, and GOAL line are hard-coded in src/arena/prompts.ts and every agent gets
them. What you can inject is the "STRATEGY GUIDANCE" text via primingCondition. Easiest path: replace the
strategic text with yours, give every agent primingCondition: strategic in your YAML, then npm run build.

  PASTE THIS AS THE STRATEGIC: TEXT IN SRC/ARENA/PROMPTS.TS (PRIMING_TEXTS)
  You are one of 16 AI agents in a 100-round economic experiment with real
  satoshis. Your goal is to finish with the highest balance.
  You may negotiate, cooperate, make deals, and form alliances using ONLY the
  permitted actions: SEND, MESSAGE, BROADCAST, PASS. Every transfer is real
  and final. Balances are private; others may lie about theirs.
  Think strategically about every decision: track who keeps promises, test
  trust with small amounts before large ones, and never send sats you cannot
  afford to lose. Do not break the experiment rules, reference outside funds,
  or attempt any action outside the four listed.

Keep it under a few sentences — it's appended to a prompt that already contains the rules and the round's
messages.

8 RESULTS
 What                         Where (all real, in the current code)

 Round number                 Terminal: "Round N of 100" as it runs.

 Agent balances               Terminal at the end ("Final Balances", start → end per agent) and the
                              arena_balances table, per round.

 Transactions                 arena_transfers table (from, to, amount, round); terminal shows the top transfer
                              edges.

 Alliances /                  arena_messages table — every private message and broadcast, by round. The
 interactions                 terminal prints faction totals (Claude↔Claude, GPT↔GPT, cross).

 Final leaderboard            Terminal "Final Balances" list + Gini coefficient. Sort arena_balances by the last
                              round for a ranked table.

 The database file            arena-<your-experiment-name>.db in the repo folder (SQLite). Open it with the
                              free "DB Browser for SQLite".

Not for your runs: analysis/visualize.py and the figures read the author's shipped CSVs in analysis/data, and
viz/index.html is for the older game-theory mode — neither reads your arena database automatically.

---

9 SAFETY
☐ There is no test mode. Every run spends real sats. Start with the pilot (8 agents, 10 rounds) and
   small startingBalance values (e.g. 100) to learn the flow before 16 × 1,000.
☐ Real funds can be lost. Agents transfer sats to each other; leftovers are swept back to your
   operator wallet at teardown, but a crash mid-run, a failed sweep, or an agent hitting 0 can leave
   sats stranded or gone. Fund only what you're willing to lose.
☐ Keys stay in .env. Never share your operator key, API keys, wallet seed phrases, or passwords
   — not in chats, screenshots, or the YAML. Per-agent wallet keys are created and handled by the
   script only.
☐ Watch API spend. 1,600 calls per full run; set spending limits in your Anthropic and OpenAI
   dashboards first.
☐ No profit is implied. This is a behavior experiment. The total sats in the arena only go down
   (Lightning Faucet takes 1% on payments over 100 sats); "winning" means one agent holding
   more of the same pool.

10 QUICK TROUBLESHOOTING
 Problem                                One-line fix

 npm run db:init → Cannot find          Skip it — the DB is created automatically when you run an
 module init-db.js                      experiment.

 OPENAI_API_KEY (or ANTHROPIC)          Both keys must be in .env — the code builds both clients at
 missing or empty                       startup even for single-model runs.

 Insufficient balance: have X,          Fund the operator wallet to agents × startingBalance × 1.1, or
 need Y (includes 10% buffer)           lower startingBalance / count in your YAML.

 Lightning Faucet API error             Check LF_OPERATOR_KEY (not
                                        FAUCET_OPERATOR_KEY) and that the key starts with op_.

 Changed the prompt but agents          Edits in src/ need npm run build; then use primingCondition:
 ignore it                              strategic (or the key you edited) in the YAML.

---

★ WANT TO EXPERIMENT FURTHER?
Three optional ideas — each is a YAML change, nothing more.
1. Claude vs GPT vs Gemini. Claude vs GPT works today: 8 + 8 groups, set revealModelType: true
if you want them to know. Gemini is NOT supported by the current code — adding it means writing a
provider in src/llm/router.ts.
2. Cooperative vs selfish agents. Use primingCondition: cooperative for half and competitive for
the other half (both texts already exist), same models, same balance — then compare the transfer
flows between the two camps.
3. Same balance, who's richest after 100 rounds? That's Section 6 as-is: startingBalance: 1000
for all 16, totalRounds: 100, then rank arena_balances at round 100. Run it twice — results vary
between runs.

  SOURCES & NOTE
  Verified against pfergi42/lf-game-theory at commit 8e5f021 (Feb 6 2026) on Sep 14 2026: cloned, installed, built, ran
  the coordinator (it stops at the missing API keys as expected). Lightning Faucet details from
  lightningfaucet.com/ai-agents. Research code by Paul Ferguson (MIT); @seb.ai is not affiliated. Real Bitcoin and API
  credit are spent; nothing here is financial advice or a promise of gains. Want more AI systems? Follow
  @seb.ai

---
```

