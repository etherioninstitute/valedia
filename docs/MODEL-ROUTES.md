# Model routes: who asks which model, what it costs, and where it should go (2026-11-18)

Written by the lead on the owner's instruction of 2026-11-18 (about 5:55 PM PST): paid frontier tokens only where
they are needed or where speed decides; local models and FreeLLMAPI's zero-retention models for everything a
local model can do and that can be checked. Read from the machines, not recalled: `lms ps` and `lms ls`, the Hermes
profiles' own settings (model and provider lines only; no key was read), the trader's code and its jobs.

## 1. The seats that exist today

**2026-11-19, owner: etherion is too slow, so it is OFF for Hermes until he says otherwise.** The default profile no longer names `hs27b` (delegation: only `paid_frontier`, the ChatGPT plan; the two auxiliary entries removed; backup `config.yaml.before-etherion-off-20261119-203909`); `seat-implementer` was told to move off it. The Mac mini (`ultimatetraders-Mac-Mini.local`, LM Link) lists arex-2, prism-ml/bonsai-27b and qwen3.8-27b-splash, none loaded; a model must be loaded ON the mini (never from this Mac: a load that lands here evicts the collector's model) before any route names it. The rows below describe the seats as they were built.

| Seat | Where it runs | State on 2026-11-18 | Fit |
| --- | --- | --- | --- |
| `elephant-1` (9B decision model) | THIS Mac, LM Studio | loaded, one slot, 8K window | the order-book collector's own; nothing else may load it down while its test is scored (to 3 Dec) |
| `qwen3.8-27b` as `hs27b` | etherion's computer, through LM Link | loaded, 2 parallel, 262144 window (the owner raised it on 2026-11-18 evening; `lms ps` about 6:55 PM) | short jobs AND agent work (Hermes needs 64K: met); the picks reader shares its two slots; first agent request to be watched against the collector's cycle |
| `ternary-bonsai-2-27b` as `bonsai2` | etherion's computer, through LM Link | not in `lms ps` at the same check | a second opinion when loaded |
| arex-2, bonsai-27b | the Mac mini, through LM Link | listed, not loaded | spare |
| the 27B's local builds (`qwen/qwen3.8-27b`, `qwen3.8-27b`) | THIS Mac | NOT loaded | naming them makes LM Studio load 16 to 19 GB here, beside the collector |
| FreeLLMAPI (fixed chain `auto:coding`: Cloudflare GLM-4.7-flash, then Groq or Cloudflare for Qwen3.8-27B and gpt-oss-120b, then OVH last) | outside, the vendor's app on 127.0.0.1:31415; NO filter process; a key-leak hook on `valedia-coder` | RUNNING; `valedia-coder` wired with its tools parked until the OS-wall launcher exists; END TO END OK at 7:38 PM on 2026-11-18 (one Hermes request, served first try by Cloudflare GLM-4.7-flash: 5,478 tokens in, 231 out, 6.65 s). Hermes adds about 5.5k input tokens a turn, so per-minute limits bind: Groq gpt-oss-120b (8k tpm) about one turn a minute, qwen3.8-27b (70k tpm) about twelve; a one-shot worker calling the endpoint directly carries none of that | workhorse for coding jobs; a worker calling it directly runs the profile's `scan_text` first; pin `groq:qwen/qwen3.8-27b` for strictly zero-retention; OVH's data terms NOT read (the owner kept it) |
| Claude Sonnet / Opus / Fable | paid (the weekly limit) | in use | judgment, final acceptance, anything on the path to real money that must be right and fast |
| ChatGPT or Codex subscription, through Hermes | paid, NOT on the Claude limit | signed in (Hermes Desktop, Accounts, shown for the default profile on 2026-11-18) | a second frontier family: builders and blind checks that need frontier quality, sparing Claude; a check by another family is worth more than a second Claude one |
| xAI Grok, through Hermes | paid, NOT on the Claude limit | signed in (the same screen) | the same; the off-family review route (`hermes proxy start --provider xai`) already exists |
| Nous Portal, through Hermes | account connected | signed in (the same screen) | not yet looked at |

## 2. Valedia's own processes (what runs without a person)

| Process | When | Model today | Paid? | Proposed |
| --- | --- | --- | --- | --- |
| Order-book collector | every minute | `elephant-1`, this Mac | no | unchanged (scored test) |
| Daily forecast | weekdays 3:05 PM | `elephant-1` and two rules | no | unchanged |
| Hourly pick-list reader | every hour at :07 | `qwen/qwen3.8-27b` at this Mac's LM Studio (loads here on demand) | no | point at `hs27b` on etherion: nothing loads on this Mac |
| Weekly research cycle | Sundays 6 PM | decider and auditor: Claude Sonnet (subscription); scout and the others: the 27B | YES, once a week | keep the auditor on Sonnet (it is the check); try the decider on the 27B for two cycles beside Sonnet and compare before switching |
| Research panel | on demand | Hermes profiles, the 27B, system rules | partly | members to `hs27b`; a paid seat only as the tie-breaker |
| Leaders' pass, facts, decay, paper, backup, the practice order step | scheduled | no model | no | unchanged |
| MiroFish swarm | on the owner's yes | local | no | unchanged |

## 3. Hermes profiles

| Profile | Model today | Paid? | Proposed |
| --- | --- | --- | --- |
| `valedia` (her chat, Telegram, the desktop app) | Claude Sonnet 5 through the Anthropic API key | YES, by the token | the local 27B once one is loaded with a 64K window on etherion; Sonnet stays one command away for hard questions |
| default (the owner's own Hermes, Telegram) | ChatGPT/Codex `gpt-6.2-sol` (his switch at 6:21 PM on 2026-11-18, off Claude). Since 8:30 PM: `delegation.backend: model_map` with `local_worker` (`hs27b` on etherion) as the default route and `paid_frontier` (`gpt-6.2-sol`); auxiliary `title_generation` and `mcp` on `hs27b` | his ChatGPT plan, not the Claude limit | held, not done: a `free_pool` route (a free model with the default profile's tools could read his secrets until the OS wall exists), an `elephant-1` router service (a second 9B model on this Mac during the collector's scored test; a download), auxiliary `approval`, `compression` (a 1M-window context does not fit the 262K local window) and `review` on free models. Backup: `~/.hermes/backups/config.yaml.before-orchestrator-routes-20261118-202747` |
| `valedia-cto` | Claude Sonnet 5.5, subscription | YES | the local 27B (64K window) |
| `valedia-trading-decider` | Claude Sonnet 5.5, subscription | YES | see the weekly cycle above |
| `valedia-trading-auditor` | Claude Sonnet 5.5, subscription | YES | stays paid |
| `valedia-trading-scout` | `qwen/qwen3.8-27b` at this Mac's LM Studio | no | `hs27b` on etherion |
| `valedia-coder` | `coder-local` at this Mac's LM Studio | no | a coder model on etherion (64K window); never loaded on this Mac during the scored test |

## 4. Build work (where the paid tokens actually go)

Valedia's running processes are almost all local already. The weekly limit is spent by the BUILD sessions in
Claude Code: this one (a Fable lead; on 2026-11-18 about 4.2 million tokens in Opus builders and blind checkers
after 1:30 PM alone, before the lead's own turns), the Paperclip and RuFlo session, the FreeLLMAPI session.

| Kind of work | Until now | From now |
| --- | --- | --- |
| Rulings, tickets, acceptance, merges, gates | the lead (Fable) | the lead, fewer and shorter turns |
| Order-path and real-money code (the shared book, live entries, the event order step) | Opus builder, Opus blind checks each round | Sonnet builder; ONE Opus blind check a slice, a second only after a real defect |
| Research tooling (the copier, the screen, the historical test's programs) | Opus builder, Opus blind checks each round | local or FreeLLMAPI builder once a worker harness exists; until then Sonnet; ONE check at the end |
| Studies, inventories, log reading, test drafts, planted faults | Opus | `hs27b` / `bonsai2` through `local_seat.py`, checked by the lead |
| First-pass review of a diff | Opus | the local 27B lists findings; a paid checker only confirms or kills them |

## 5. What must be true before a route moves

1. Nothing new loads on THIS Mac while the collector's test is scored: every local request names a model that is
   loaded on etherion (`hs27b`, `bonsai2`), never a key that is only on this Mac.
2. A local model's work is used only where it can be checked (a test, a second model, the lead).
3. FreeLLMAPI: zero-retention providers only; no key, account number, order or position of the owner in any
   request; the lead reads the owner's instruction of 2026-11-18 as his standing yes to send project code and
   task text to those providers, nothing wider.
4. A paid seat is not replaced on a guess: the decider is compared for two weekly cycles first.
5. The Hermes gateway is restarted by the owner; profile changes are made on his yes, one profile at a time.

## 6. Open, in order

1. DONE 2026-11-18 evening (the owner): `hs27b` on etherion at 262144. Next: one agent request through it, watched
   against the collector's cycle; the Paperclip Implementer seat takes it first, FreeLLMAPI's `coding` chain second.
2. The pick-list reader, the scout and the panel pointed at `hs27b` (one code constant, two settings).
3. A worker harness for local and FreeLLMAPI builders (the Paperclip and RuFlo pipeline is the candidate; it is
   not hands-off yet).
4. FreeLLMAPI installed and keyed (the owner's keys), then its chain added as a seat.
