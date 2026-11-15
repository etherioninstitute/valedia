# MiroFish: the guard decision and the swarm profiles (2026-11-11, revised 2026-11-15)

## The staging guard

`tools/mirofish_native_stage.py` pins 318 original source files against the frozen 2026-11-06 review record. 69 of them
changed in four overhaul commits, so the tool refused to stage anything. On 2026-11-11 the owner approved accepting
exactly those 69: `tools/mirofish_acknowledged_revisions.json` names each path, its frozen hash, its current hash and
the commits, and the tool pins that list by its own hash.

- None of the 69 is copied into a candidate. Staging still copies the reviewed bytes, so what a run executes is unchanged.
- Files a candidate does copy (713 of them) are never excused.
- Any other difference, or a further change to a listed file, is refused as before. A new overhaul of `src/` needs a new
  decision and a new list.

## Profiles

| Profile | Agents | Rounds | Model calls allowed | Wall clock | Purpose |
|---|---|---|---|---|---|
| `report64_coverage` | at most 4 (2 act per platform) | 1 | 64 | 40 min | The profile that completed on 2026-11-08 |
| `swarm8` | 8 named, up to 12 | 3, every agent each round | 192 | 2 hours | The proving run: whole-unit memory at a deliberately tight 4,096 tokens |
| `swarm48` | 48 named (52 agents configured in the last attempt), up to 64 | 3, every agent each round | 672 | 15.5 hours | A real swarm. **PROVISIONAL: not to be launched before the `swarm8` run's memory readings have been read** |

The swarm profiles change closed numbers (gateway budgets and time limits, deadlines, the participant bound, the round count),
three selector expressions, the concurrency integers, the agent memory (below) and the scenario with its seal. Every number
is derived from one block of measured constants in `tools/mirofish_swarm_limits.py`; the stage tool refuses a profile that
breaks any of its inequalities. The single dispatch slot, the "one unknown result poisons the attempt" rule, the template
admission and its 10 s limit, the sandbox and the paper-only handoff are unchanged.

Its scenario (`tools/mirofish_swarm48_scenario.txt`) is invented and says so: 48 fictional participants react to a
fictional central-bank announcement, with SPY, TLT and GLD as topics. Every agent is made to take part in every round.
The run shows the pipeline at scale. It is not a forecast and says nothing about any market.

## What a run needs from the machine

- **The local model server to itself.** The run loads its own pinned 27B and stops if any other language model is
  loaded or gets loaded while it runs. No local Hermes bot (Scout, Valedia, CTO, local delegation workers) may be used
  until it ends. Cloud bots (Decider, Auditor, the default profile) are fine.
- The embedding model `google/embedding-gemma-300m` loaded under the name `ym-research-embedding`, context 2048.
- Memory headroom throughout; the run samples memory every 0.4 s and stops itself if the machine gets tight.
- About 15 minutes for `report64_coverage`. For the swarms see "Duration by phase" below: about 75 minutes for `swarm8`
  (2-hour wall clock) and about 5 hours for `swarm48` (15.5-hour wall clock).

## What the 2026-11-11 runs showed

- **The model server's internal port moves.** The reviewed sources fix the port seen on 2026-11-08. The server app picks a new
  one at every restart, so staging now detects the live port and carries it into the model source, the generated helper
  script and the sandbox rule, as one more checked change. A staged folder is good only until the server app restarts;
  stage again after that.
- **The check run worked end to end**: own model loaded, graph qualified, 4 agents, 4 simulation calls, a three-section
  report, 43 model calls in 881 s, cleanup proven.
- **Its final verdict was a refusal, and that is by design.** The last step turns the report into a signal and refuses a
  neutral one as "not usable evidence" (within 5 points of 50; this report gave NVDA 50.7, hold). So `complete: true`
  needs a report that leans one way. A neutral outcome is a result, not a fault.

## The swarm attempts of 2026-11-11

| Attempt | How far it got | Why it stopped | What changed afterwards |
|---|---|---|---|
| 1 | Graph built: 53 participants from the 48-name scenario (4.5 min, 14 model calls) | The producer caps a run at 4 participants and 1 round in five places beyond the worker ("Owned synthetic entity cap violated") | Each cap now follows the profile (64 and 3). The report check's one-hour freshness window follows the wall clock |
| 2 | Past the caps; 27 personas written (31 min, 41 model calls) | The 28th persona reply ran to the 1,536-token cap. A reply cut off at its cap is fatal to the whole attempt, by design | Reply caps raised to 2,560 (personas, ontology, report text) and 1,024 (an agent's turn); output budget raised with them |

| 3 | All 53 personas and the simulation settings written (53 min, 77 calls); 11 agent turns of the simulation done | The run's own memory guard: free memory fell under its 30% floor four and a half minutes into the simulation. The run's own processes held steady near 26 GB; the fall came from outside its process memory | Nothing yet (see below) |
All three stopped themselves, with cleanup proven and the model unloaded. Attempt 2 kept running on rule-based personas with a
refusing gateway until it was told to stop, so a run that has lost its gateway should be stopped by hand (send the observer
process a TERM signal; it cleans up).

Memory through attempt 3 (64 GB machine): 87% free before launch, 55% with the model loaded, 39 to 45% during persona writing, then 36, 34, 32% and under 30% in the simulation. The run's guard allows 65% used before start and needs 30% free while running.

The lesson is the obvious one: go up in steps. Jumping from 4 agents to 53 hit three separate guards in a row, an hour apiece. A 16-agent, 2-round profile would finish in well under an hour and show where memory starts to bind before a larger one is tried.

Not yet shown: that a whole swarm completes. The pipeline was built as a fail-closed 4-agent check, where any single
unknown or cut-off reply ends the attempt. A swarm makes about 400 calls, so the odds of one bad reply are much higher
than in a 43-call check, and a failure late in the simulation costs two hours.

## Agent memory (2026-11-15)

**The defect.** The producer's `owned_context_budget.py` gave every agent the library's score-based context at 2,048 tokens.
A persona is 1,100 to 1,700 of those tokens, so a feed of 2,400 to 6,700 tokens could not fit: the library cut it into
one-token chunks (log line `Slicing into smaller chunks`) and then dropped all but the last few dozen (`Context truncation
performed`). Agents were not reading their feeds. When a cut left two assistant messages in a row, the template admission
refused the call and the attempt died (the five template refusals of `valedia-native-20261114-swarm48-b`).

**The replacement**, for swarm profiles only, is `tools/mirofish_owned_agent_memory.py`, pinned by its sha256 in the stage
tool. Staging checks the canonical file's hash first, then puts the reviewed file in its place, then sets the profile's three
integers on it (budget, reply cap, participant bound). The manifest's `swarm_profile.memory_replacement` names both hashes.
`report64_coverage` and the other profiles keep the canonical bytes, and with them the same 2,048 defect.

- Nothing is ever sliced. The library slices only a message larger than its limit, and the new memory reports a limit no
  message reaches; the real budget is enforced when the context is assembled.
- The model is given the system message and the newest whole units (one user message and everything after it, up to the
  next). Older units are dropped whole, oldest first. Log line, INFO: `Owned memory dropped whole units`.
- Never two assistant messages in a row, never a tool result without its call: such records raise and end the turn instead.
- Only if the system message and the newest unit alone exceed the budget is the newest user message trimmed from its end,
  with a plain last line saying how much was kept. Log line, WARNING: `Owned memory trimmed the newest user message`.
- The total stays at or under the budget, and the same records always give the same context. The total counts tool-call
  arguments: the library itself counts an assistant's tool calls as a few tokens whatever their length, so the memory adds
  their own text. On the old count a context the budget called 2,356 tokens held 7,411.
- Every refusal (a shape that cannot be sent, or a newest unit that cannot fit) writes one WARNING line,
  `Owned memory refused the context`, with the role of the newest record; see "Watching a run".

Reproduction, offline (`tools/mirofish_memory_reproduction.py`, the stopped run's largest persona and three largest feeds):

| Memory | Library slicing / truncation lines | Feed records written | Newest feed given whole | Dropped / trimmed lines |
|---|---|---|---|---|
| canonical, 2,048 | 10 / 14 | 106, 6,484, 6,697 one-token chunks | never | 0 / 0 |
| replacement, 12,288 (`swarm48`) | 0 / 0 | 1 each | every round | 1 / 0 |
| replacement, 4,096 (`swarm8`) | 0 / 0 | 1 each | no: trimmed to fit, every round | 2 / 3 |

`swarm8`'s 4,096 is tight on purpose: its largest feeds do not fit and are trimmed, so the proving run exercises every path.
`swarm48`'s 12,288 is provisional until that run's memory readings have been read.

## Where each number comes from

Measured constants (stopped run `valedia-native-20261114-swarm48-b` unless noted): the model reads 188 prompt tokens a second
and writes 23.6 (fit over 288 calls), plus at most 27 s; a real prompt is 1.131 agent-library tokens per token (1.25 assumed);
tools and template add 1,750 tokens; a request is at most 5.28 bytes per token; window 32,768.

| Limit | Old | New | Rule |
|---|---|---|---|
| Agent memory budget | 2,048 | 4,096 (`swarm8`), 12,288 (`swarm48`) | 1.25 B + 1,750 + 1,024 <= 32,768 (largest allowed 23,995). Still holds now that the budget counts tool-call arguments: the measured 1.131 was taken when they went uncounted, so the true ratio is lower |
| Worst single call | | agent 239 s, caller 353 s | (32,768 - reply cap) / 188 + reply cap / 23.6 + 27 |
| Gateway arrival-to-done | 175 s | 540 s | two agent calls in flight (one per platform), each worst + 10%, rounded up to 30 s |
| Gateway queue | 170 s | 270 s | one agent call ahead, worst + 10%, rounded up to 30 s |
| Agent, caller and embedding client timeouts | 180 s | 570 s | at least 30 s longer than the gateway limit in front of them |
| Requests in flight per platform (semaphore) | 30 (2 in the first `swarm48`) | 1 | dispatch is serial; more in flight only waits |
| Gateway request bytes | 131,072 | 258,048 | >= 5.28 x 32,768 = 173,016, and at least 4,096 below the SDK helper's 262,144 request-line cap (the helper re-wraps the body, about 120 bytes, and its check sits outside its `try`) |
| Worker event line (observer buffer) | 131,072 | 114,294,784 (109 MiB) | the largest `wait` event: 8,192 + 50 recent actions x (14,336 text tokens x 144 bytes + 512), plus 10%, in whole MiB. See below |
| Caller reply cap | 2,560 | 4,096 | 2,560 was 96% used (2,467) |
| Model calls allowed | 640 | 192 / 672 | the larger of expected calls + 50% and every call the participant bound admits, rounded up to a multiple of 32 |
| Output total | 1,700,000 | 800,000 / 2,800,000 | every admitted call can reserve its reply cap |
| Participant bound | 4 | 12 / 64 | covers the named actors x 52/48 (9 / 52); `swarm8`'s 12 keeps its ceiling inside 2 hours |
| Wall clock | 14,400 s | 7,200 s / 55,800 s | ceiling at the participant bound + 20%, rounded up to a half hour |
| Worker deadline | | 7,080 s / 55,680 s | wall clock - 120 s cleanup |
| Resource history | 36,001 records, 64 MiB | 18,001, 32 MiB / 139,501, 256 MiB | one record per 0.4 s for the wall clock, 1,219 bytes each |

**The worker event line.** The worker reports each stage to the observer as one JSON line on a pipe, and the observer refuses
a line longer than its buffer limit (`mirofish_native_run.py:261`, 131,072 in the reviewed code). The `wait` event carries the
whole run state, including up to 50 recent actions with their full text. Nothing else on that path limits its size: the
observer stores it as `builder/worker-events/wait.json` and again in `builder/native-run-receipt.json`, neither with a cap.

- What one action can carry: a quote holds its own text twice (each at most one 1,024-token agent reply), the quoted post (a
  seed post can be as long as a 4,096-token caller reply) and two names (each at most a caller reply): 14,336 tokens.
- Bytes per token: 144, the largest any single token of the frozen vocabulary takes once the worker writes it with escaping
  (a non-ASCII character becomes 6 or 12 bytes). Computed from the vocabulary itself, so it holds for any language.
- Measured: every recorded event of earlier runs fits the bound. The largest `wait` was 11,539 bytes; the stopped run's run
  state would have made a 51,794-byte `wait`. Fifty actions near the reply cap pass 131,072, so a long swarm would have been
  refused at this event, after its whole simulation and before its report.
- The other events (graph check, prepare, start, report, any failure) carry no agent text; the largest seen is 4,582 bytes.

The offline part of context admission (template renders for all eight candidates plus the tokenizer) takes 0.32 s at the
largest admitted size, 31,744 tokens (`tools/mirofish_admission_timing.py`); its 10 s limit stays.

## Duration by phase

Expected figures use the named actors times the measured growth (the graph built 52 agents from 48 names: 9 for `swarm8`,
52 for `swarm48`), the measured speeds and the median persona and feed. The ceiling takes the participant bound (12 and 64)
with every agent call at full memory, so the wall holds for anything the bound admits. The report phase has been measured
only in the 4-agent run (393 s) and is assumed to take three times that.

| Phase | `swarm8` | `swarm48` |
|---|---|---|
| Start, graph, personas, settings | 18 min (1,085 s) | 59 min (3,536 s) |
| Simulation, rounds 1 / 2 / 3 | 10 / 12 / 12 min | 55 / 74 / 93 min |
| Report (assumed) | 20 min | 20 min |
| Expected total | about 74 min (4,418 s) | about 5.0 h (18,166 s) |
| Ceiling, at the participant bound | 98 min (5,880 s) | 12.6 h (45,197 s) |
| Wall clock (the run stops itself here) | 2 h | 15.5 h |

If the graph builds more participants than the bound (more than 12 for `swarm8`), the run stops at its own participant check
in its first minutes, before the simulation, and cleans up.

## What must be switched off, and for how long

For the whole wall clock (up to 2 hours for `swarm8`, up to 15.5 hours for `swarm48`):

- every other language model on the local model server: the run stops if another one is loaded;
- the local Hermes bots (Scout, Valedia, CTO, local delegation workers) and anything else that uses the local model server,
  including another session's local forecaster;
- heavy jobs (full test suites, large copies) that take memory: the run needs 30% free memory throughout.

## Watching a run

Watch `<work>/owned-source/producer/backend/uploads/simulations/sim_*/simulation.log` while the simulation runs. On the first `Slicing into smaller chunks` or
`Context truncation performed`, stop the attempt properly: send the observer process a TERM signal and let it clean up.
Either line means the memory is not the one staged.

| Log line | Level | Meaning |
|---|---|---|
| `Slicing into smaller chunks` | library WARNING | a message was cut into chunks: stop the attempt |
| `Context truncation performed` | library WARNING | the library dropped context itself: stop the attempt |
| `Owned memory dropped whole units` | INFO | older whole units were left out to fit the budget: expected |
| `Owned memory trimmed the newest user message` | WARNING | one feed did not fit alone and was trimmed: expected at 4,096; count them |
| `Owned memory refused the context` | WARNING | the memory refused to build a context; read the `newest record role=` at the end of the line |

How to read `Owned memory refused the context`:

- `newest record role=tool`: harmless; the next round starts a new unit.
- `newest record role=user`: an agent's turn was lost (its feed could not be given to the model). Count these lines and report
  the count with the run.
- The library's own `Token calculation failed before chunking` warnings come with these refusals and are expected. They are
  not the count to read; count the owned-memory line instead.

## Stage, gate, launch

Each staged folder can be launched once. A gate is a small file naming the staged candidate's hash and an expiry at most
one hour ahead; the owner's go-ahead is what it records.

```bash
PY=/Volumes/NIMROD_SSD/Qwen-Weight-Space/runtime/mirofish-local-20261106/venv-py311/bin/python
cd tools && TMPDIR=/Volumes/NIMROD_SSD/Qwen-Weight-Space/project/tmp $PY -B mirofish_native_stage.py --stage --profile swarm8 --work <new folder under the crew directory>
# write <work>/root-one-native-attempt.json, then, from <work>/owned-source/observer-entry:
$PY -B mirofish_native_startup.py --work <work> --gate <work>/root-one-native-attempt.json
```

Staged on 2026-11-15 as proof of the repaired tool, not launched, no gate written: `valedia-native-20261115-swarm8-b`
(candidate `f1ec87023ece47b0f2d31952d97f0b392402ce7e04f799a5ac7370c81a5802f1`) and `valedia-native-20261115-swarm48-b`
(candidate `01313bba42916d809a90ae9aa5ef3bb5f34c72f45469fecae02e6366fadd4d45`, PROVISIONAL). The `-a` folders were staged by
the tool before these repairs; leave them unlaunched. The folder that is actually launched is staged from `main` after the
merge, so that its manifest names the repository's own files. A staged folder carries the model server's port as of staging;
stage again if the server app restarts.

The result is `<work>/builder/native-run-receipt.json` (events, model-call count, whether the report validated, what the
paper step did) and `<work>/builder/gateway-ledger.json` (every model call).
