# MiroFish: the guard decision and the swarm profiles (2026-11-11, revised 2026-11-15, models added 2026-11-15)

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
| `swarm48` | 48 named (52 agents configured in the last attempt), up to 64 | 3, every agent each round | 672 | 16 hours | A real swarm. **PROVISIONAL: not to be launched before the `swarm8` run's memory readings have been read** |

The swarm profiles change closed numbers (gateway budgets and time limits, deadlines, the participant bound, the round count),
three selector expressions, the concurrency integers, the agent memory (below) and the scenario with its seal. Every number
is derived from one block of measured constants in `tools/mirofish_swarm_limits.py`; the stage tool refuses a profile that
breaks any of its inequalities. The single dispatch slot, the "one unknown result poisons the attempt" rule, the template
admission and its 10 s limit, the sandbox and the paper-only handoff are unchanged.

Its scenario (`tools/mirofish_swarm48_scenario.txt`) is invented and says so: 48 fictional participants react to a
fictional central-bank announcement, with SPY, TLT and GLD as topics. Every agent is made to take part in every round.
The run shows the pipeline at scale. It is not a forecast and says nothing about any market.

## What a run needs from the machine

- **The local model server to itself.** The run loads its own pinned checkpoint (the 27B unless staged with `--model`) and stops if any other language model is
  loaded or gets loaded while it runs. No local Hermes bot (Scout, Valedia, CTO, local delegation workers) may be used
  until it ends. Cloud bots (Decider, Auditor, the default profile) are fine.
- The embedding model `google/embedding-gemma-300m` loaded under the name `ym-research-embedding`, context 2048.
- Memory headroom throughout; the run samples memory every 0.4 s and stops itself if the machine gets tight.
- About 15 minutes for `report64_coverage`. For the swarms see "Duration by phase" below: about 75 minutes for `swarm8`
  (2-hour wall clock) and about 5 hours for `swarm48` (16-hour wall clock) on the 27B. On the 8B see "Staging on a chosen model".

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
and writes 23.6 (fit over 288 calls), plus at most 27 s; a real prompt is 1.131 agent-library tokens per token (1.30 assumed
since 2026-11-15: one platform of the 8B proving run's requests measured 1.241 on the 27B's tokenizer, which left the earlier
1.25 no margin);
tools and template add 1,750 tokens; a request is at most 5.28 bytes per token; window 32,768.

| Limit | Old | New | Rule |
|---|---|---|---|
| Agent memory budget | 2,048 | 4,096 (`swarm8`), 12,288 (`swarm48`) | 1.30 B + 1,750 + 1,024 <= 32,768 (largest allowed 23,072; `swarm8` needs 8,099, `swarm48` 18,749). Still holds now that the budget counts tool-call arguments: the measured 1.131 was taken when they went uncounted, so the true ratio is lower |
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
| Wall clock | 14,400 s | 7,200 s / 57,600 s (27B; 55,800 s at the old 1.25) | ceiling at the participant bound + 20%, rounded up to a half hour |
| Worker deadline | | 7,080 s / 57,480 s | wall clock - 120 s cleanup |
| Resource history | 36,001 records, 64 MiB | 18,001, 32 MiB / 144,001, 256 MiB | one record per 0.4 s for the wall clock, 1,219 bytes each |

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

These are the 27B's figures; the 8B's are under "Staging on a chosen model". Expected figures use the named actors times the measured growth (the graph built 52 agents from 48 names: 9 for `swarm8`,
52 for `swarm48`), the measured speeds and the median persona and feed. The ceiling takes the participant bound (12 and 64)
with every agent call at full memory, so the wall holds for anything the bound admits. The report phase has been measured
only in the 4-agent run (393 s) and is assumed to take three times that.

| Phase | `swarm8` | `swarm48` |
|---|---|---|
| Start, graph, personas, settings | 18 min (1,085 s) | 59 min (3,536 s) |
| Simulation, rounds 1 / 2 / 3 | 10 / 12 / 12 min | 55 / 74 / 93 min |
| Report (assumed) | 20 min | 20 min |
| Expected total | about 74 min (4,418 s) | about 5.0 h (18,166 s) |
| Ceiling, at the participant bound | 99 min (5,964 s) | 12.9 h (46,527 s) |
| Wall clock (the run stops itself here) | 2 h | 16 h |

If the graph builds more participants than the bound (more than 12 for `swarm8`), the run stops at its own participant check
in its first minutes, before the simulation, and cleans up.

## What must be switched off, and for how long

For the whole wall clock (on the 27B up to 2 hours for `swarm8`, up to 16 hours for `swarm48`; on the 8B see below):

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

- `newest record role=tool` or `role=assistant`: harmless; a tool call or its result was too large and the next round starts a new unit.
- `newest record role=user`: an agent's turn was lost (its feed could not be given to the model). Report the number of these lines
  as an UPPER BOUND on lost turns: a refusal that lasts is logged again when the next round's feed is written, and the line names no agent.
- The library's own `Token calculation failed before chunking` warnings come with these refusals and are expected. They are
  not the count to read; count the owned-memory line instead.

## Staging on a chosen model (2026-11-15)

```bash
cd tools && TMPDIR=/Volumes/NIMROD_SSD/Qwen-Weight-Space/project/tmp nice -n 15 $PY -B mirofish_native_stage.py --stage --profile swarm8 --model qwen3-8b --work <new folder under the crew directory>
```

- `--model` takes exactly one of the three built-in profiles of `src/mirofish_native_model.py`: `qwen3.8-27b`, `qwen3-8b` or
  `ternary-bonsai-2-27b` (TMF4). A plan of two models is staged with `--setup-model` and `--turns-model` instead (next section).
  Anything else is refused in one line, starting `refused:`: an unknown name, a profile that exists only in
  `src/mirofish_model_registry.json` (registered profiles can name their own paths and pins, so none is staged), and
  `qwen3.8-27b-mlx`, which is registered for HTTP backends only. `--model` works with the swarm profiles only.
- Without `--model`, staging is unchanged: the reviewed W16 bytes, no selector, the 27B.
- With `--model`, including `--model qwen3.8-27b` named explicitly, the copies of `mirofish_native_model.py` (two) and
  `mirofish_native_run.py` (three) are the `src/` revisions of this repository (TMF4a, commit `fc99603`, with the four
  repairs of TMF4 addendum 3, commit `38bce8a`), read from `src/` beside the tool and each pinned by its SHA-256
  (`e1cd97e1…` and `0426dd5a…`). A changed byte in `src/` refuses staging until the pin is moved. Review status, as the
  manifest states it: blind-checked on 2026-11-15 (V136, pass with notes; the four source repairs of addendum 3 applied
  afterwards and awaiting their own check). The earlier revisions (`ab08400`, `b5a1797`, V134) were unsafe for registered
  profiles; conditions C1 to C3 still apply: built-in names only, no registry in any staged copy, weight pin and tokenizer
  hashes checked at staging, no borrowed conformance evidence.
  Every closed number of the run script, the SDK port and the SSD device number is applied to those revisions with the
  same exact-fragment proof.
- The selector `mirofish_model_profile.json`, `{"profile": "<name>"}` and a newline, is written by the tool beside each
  model copy (`builder/review-source/`, `owned-source/observer-entry/`; the worker entry has no model copy) and pinned in
  the manifest. No `mirofish_model_registry.json` is in any staged copy, so the staged module knows only its two built-ins.
- The checkpoint pins (`model_files`, `model_tokenizer`) are derived from the chosen profile in the pinned module text: its
  five tokenizer files and its GGUF. Staging checks every one against its pin, the GGUF by size and SHA-256 (hashed once).
- The SDK helper and the observer sandbox policy are the reviewed ones with only the checkpoint changed (selection, weight
  size and hash; tokenizer folder and GGUF path), and staging requires that the staged `src/` code generates exactly them.
- The free-memory start rule follows the checkpoint (TMF4 ruling c): 30 + ceil(100 x (serving files + context cache at
  32,768 in 16-bit floats from the profile's own layer and head counts + 4 GiB) / 64 GiB) + 5. That is 56% for the 8B, 57%
  for Bonsai 2 (with its projector) and 72% for the 27B staged through the model path. Without a model named, the reviewed
  65% and its bytes stay. The 30% stop floor and the 40 GiB resident-memory bound do not change.
- For a checkpoint other than the 27B the manifest says plainly that format conformance with LM Studio's own rendering has
  not been shown (the metadata receipt is the 27B's), and labels the 27B-only evidence blocks as the 27B's. The run records
  each profile in `model-source-manifest.json` with its own files and hashes; the 27B's revision identifiers go to the 27B's
  record only (R10). The upstream source of the 8B's files is the tokenizer folder's `SOURCE.txt` (Qwen/Qwen3-8B, fetched
  2026-11-10).

Limits (call slots, gateway limits, client timeouts, request and event bytes, model calls, output) stay the 27B's for every
model: a limit is an upper bound, and a faster model only finishes sooner. The memory window inequality also stays the
27B's: for the same 44 request bodies the 8B counted 0.92 to 0.95 of the 27B's tokens. Expected durations and the wall
clock use the chosen model's speeds. For the 8B these are an ESTIMATE until a run: its plain-server speeds (1,208 prompt
tokens read and 46.3 written a second over 44 agent turns of 2026-11-15) times 188/260, what the pipeline cost the 27B
against its plain server (873 read, 33.5 written). The 27B-measured setup and report phases are scaled by the ratio of
written tokens a second (0.705).

| | `swarm8` 27B | `swarm8` 8B (estimate) | `swarm48` 27B | `swarm48` 8B (estimate) |
|---|---|---|---|---|
| Start, graph, personas, settings | 18 min | 13 min | 59 min | 42 min |
| Simulation, rounds 1 / 2 / 3 | 10 / 12 / 12 min | 3 / 4 / 4 min | 55 / 74 / 93 min | 18 / 22 / 26 min |
| Report (assumed) | 20 min | 14 min | 20 min | 14 min |
| Expected total | 74 min (4,418 s) | 39 min (2,346 s) | 5.0 h (18,166 s) | 2.0 h (7,365 s) |
| Ceiling, at the participant bound | 99 min (5,964 s) | 47 min (2,825 s) | 12.9 h (46,527 s) | 3.9 h (14,048 s) |
| Wall clock | 2 h (7,200 s) | 1 h (3,600 s) | 16 h (57,600 s) | 5 h (18,000 s) |
| Free memory needed to start | 65% (72% named) | 56% | 65% (72% named) | 56% |

The 8B's walls rest on the estimate above, and the admission's fixed cost per call does not shrink with a faster model. If
the first 8B run is to keep the 27B's walls instead, `derive()` in `tools/mirofish_swarm_limits.py` takes one line: for a model
other than the default, set `wall` to `derive(profile)['wall']`, the 27B's (2 h and 16 h); the worker deadline and the host
history follow the wall.

**What must be off for a run on the 8B, for its whole wall clock:** the hourly pick reader (it uses LM Studio); every other
language model on LM Studio; the local Hermes helpers, idle (Scout, Valedia, CTO, local delegation workers) and anything else
that uses the local model server. The order-book collector may stay ON if free memory is above the start threshold (56%)
when the run starts; the run still stops itself under 30% free.

Bonsai 2 alone (`--model ternary-bonsai-2-27b`) takes the 27B's speeds, an assumption until a run (54 recorded requests
through the plain server ran at the 27B's speed), so its durations and walls are the 27B's column; it needs 57% free.

**No run on the 8B has been made yet.** Its personas, its agents' turns and its report are unjudged. The first run is a
pipeline proof; whether the 8B's output is any good is a separate question for after it.

**What the first run on the 8B will show first.** The template admission renders every agent call with the tokenizer
folder's `chat_template.jinja` and requires LM Studio's own formatted prompt to be one of its candidates. Two things are
settled at the first calls, and a refusal there is the pipeline working (fail closed, cleanup, model unloaded), not a fault:

- Tool-call arguments. The 8B's template writes a tool call's arguments as given when they are a string and as `tojson`
  when they are an object; the 27B's iterates them as a mapping. The admission renders only the parsed-object view, with
  Python's `json.dumps`. Whether that is LM Studio's rendering shows at the first agent turn whose history carries a tool
  call (round 1, after the first action). If not, the call is refused with
  `actual SDK template differs from all frozen no-thinking/thinking branches`.
- The GGUF's built-in template, which LM Studio uses, differs from the tokenizer folder's `chat_template.jinja` in five
  places. Plain string contents render the same; an assistant turn whose content is null errors under the GGUF's template.
  Look for `fixed SDK operation refused: chat_context` followed by the template error, or the same
  `actual SDK template differs ...` line, at the first call that carries such a turn.

Either refusal ends the attempt as designed. Read the call's `request.json` under `run1/calls/` to see which turn caused it.

## Staging a plan of two models (TMF4, 2026-11-15)

```bash
cd tools && TMPDIR=/Volumes/NIMROD_SSD/Qwen-Weight-Space/project/tmp nice -n 15 $PY -B mirofish_native_stage.py --stage --profile swarm8 --setup-model ternary-bonsai-2-27b --turns-model qwen3-8b --work <new folder under the crew directory>
```

- The setup model serves everything before the simulation and the report after it; the turns model serves the agent turns.
  One model is loaded at a time, always under the run's one identifier. The observer swaps at two boundaries: B1 after
  `native_prepared`, before the simulation starts, and B2 after `native_terminal`, before the report. A swap asserts the
  gateway idle, unloads, confirms no language model is listed, loads the next checkpoint, reads it back, rebuilds the
  admission, saves a `model_swap` event, then runs the two compatibility probes on the new model. Any failure refuses the run.
- Both names must be built-in and different. `--setup-model` without `--turns-model` (or the reverse), either with
  `--model`, and the same name twice are refused in one line. The design note is
  `docs/specs/2026-11-15-mirofish-two-model-design.md`.
- The selector is `{"plan": {"setup": "<name>", "turns": "<name>"}}` and a newline. Staging checks every member's tokenizer
  files and GGUF files (Bonsai 2's weights and its vision projector) by size and SHA-256, and writes `model_plan` and
  `model_tokenizer_by_profile` into the manifest.
- Memory: a plan needs the larger member's threshold, 57% for Bonsai 2 + 8B (8B alone 56%).
- Budgets: a plan adds four post-swap probes (two after each swap) at 512 output tokens each. For `swarm8` the expected
  model calls rise from 117 to 121 and the bound from 139 to 143; both round to the same 192 calls. The output reserve is
  (2 + 4) x 512 + (192 - 6) x 4,096 = 764,928, under the 800,000 budget. For `swarm48`: 436 expected, 525 bound, 672 calls,
  (2 + 4) x 512 + (672 - 6) x 4,096 = 2,731,008, under 2,800,000.
- Durations: setup and report at Bonsai 2's speeds (the 27B's, assumed), the simulation at the 8B's (an estimate), and the
  two swaps at 15 s each expected (assumed) and at the unload and load limits plus the probes in the ceiling.

| Plan Bonsai 2 + 8B | `swarm8` | `swarm48` |
|---|---|---|
| Start, graph, personas, settings (Bonsai 2) | 18 min (1,085 s) | 59 min (3,536 s) |
| Simulation, rounds 1 / 2 / 3 (8B) | 184 / 216 / 216 s | 1,061 / 1,302 / 1,544 s |
| Two swaps | 30 s (ceiling 639 s) | 30 s (ceiling 639 s) |
| Report, assumed (Bonsai 2) | 20 min (1,179 s) | 20 min (1,179 s) |
| Expected total | 51 min (3,044 s) | 2.4 h (8,787 s) |
| Ceiling, at the participant bound | 70 min (4,182 s) | 4.5 h (16,279 s) |
| Wall clock | 1.5 h (5,400 s) | 5.5 h (19,800 s) |
| Free memory needed to start | 57% | 57% |

**Format qualification before a run (needs the owner's yes; it loads a model).** `tools/mirofish_format_qualify.py` takes a
staged folder, one built-in profile of its plan and an output folder outside the staged one. It uses the folder's own SDK
helper and admission, loads that one checkpoint as the run would, runs admission only (no generation) on five canned request
bodies, among them an assistant tool call followed by its tool result and an assistant turn with null content, plus two
recorded agent requests of `swarm8-d`, then unloads. It refuses without `--owner-approved-load`, writes nothing into the
staged folder, and a mismatch shows in about a minute instead of at an agent's turn.

```bash
cd tools && nice -n 15 $PY -B mirofish_format_qualify.py --work <staged folder> --profile qwen3-8b --out <new folder> --owner-approved-load
```

## Beside the order-book collector (TMF5, 2026-11-17)

The collector's decision model (Elephant-1, `valedia_trader/core/decision_model.py` `FILE`) stays loaded in LM Studio for
the whole run. The SDK the run uses does not list it, so the ownership guard never saw it; its process was counted in the run's
own resident memory. TMF5 addendum 1 changed four things, all in the staged copies of the pinned `src/` revisions (staging with
a model or a plan takes `mirofish_native_run.py`, `mirofish_native_startup.py` and `mirofish_native_gateway.py` from `src/`,
each pinned by hash in `tools/mirofish_native_stage.py`; `mirofish_native_model.py` is unchanged).

**The co-resident, by identity.** A process is the co-resident when its executable is LM Studio's `llama-server` and its
`--model` argument, made absolute and resolved with `realpath`, equals the declared file resolved the same way (a path that
only starts with it, contains it, differs by case or ends in a slash is not it). Its row is left out of
`owned_scope_rss_bytes`; each sample records `co_resident_rss_bytes` and `co_resident_rows` (0 when it is absent, which is
never an error: the collector's keeper may reload it under a new pid). Two such processes in one sample stop the run. Every
other LM Studio process counts as before. The argument list is read only for `llama-server` rows and only the `--model` value
is kept: it carries an internal access key, and no argument text is written anywhere. Staging writes `FILE` into each staged
startup copy and refuses a plan whose own serving file resolves to it.

**The stop rule.** The start check is one reading against the start threshold. While running, the run stops when any of
these holds: the kernel's pressure level (`kern.memorystatus_vm_pressure_level`) is 4 on any sample; it is 2 or more on every
sample for 10 s; free memory is below 30% on every sample for 10 s; or there is no fresh, valid reading for 3 s (a level that
is not exactly 1, 2 or 4 is not a reading). The owned memory bound (40 GiB) still holds on every sample. On the four recorded
runs of 2026-11-15 (`swarm8-b2q8-b`, `-c`, `-d`, `swarm48-b2q8-a`) none of these stops; the lowest 10-second levels (the lowest,
over the run, of the highest free reading in any 10-second window) are 37, 38, 69 and 68. Swap used is recorded in every
sample and stops nothing. The receipt's `resource` block carries `lowest_sustained_free_percent_10s`, `max_pressure_level`
and `co_resident_rss_bytes_max`; `builder/observer-identity.json` (pid, ps start string, process group, time) is written right
after the observer is spawned.

**The start threshold** is `floor + margin + setup_cost + fall` from the table `START_TERMS` in
`tools/mirofish_swarm_limits.py`, each value with its source: 30 + 10 + 24 + 10 = 74% free, for every model named. The lead
replaces `setup_cost` and `fall` with figures from a load-only test beside the collector; that is a change to the table and
its source strings only. Without a model the W16 bytes keep the reviewed 65.

**No silent fall-back.** Where the producer fell back to default time or event settings, rule-based agent settings or a
rule-based persona after a refused call, it now raises in the native route. The report's graph search sends its query cut to
the gateway's text limit (1,536 bytes) at a character boundary; the original size travels on the call
(`query_truncated_from_bytes`) and the call is served. A refused search is no longer degraded to local search. The gateway
records every refused request; after preparation, the simulation and the report the run fails on any refused, incomplete
or cancelled call. The format check (`tools/mirofish_format_qualify.py`) imports `mirofish_native_startup` from the staged
entry as its fourth pinned module, so it runs with no hand-set path.

### Where an agent turn's time goes (recorded, 2026-11-15)

The "fixed overhead" of about 6 s a turn was model time and queueing, not admission or dispatch. From the gateway ledgers and
call records of `swarm8-b2q8-d` (9 participants) and `swarm48-b2q8-a` (48), every `qwen3-8b` chat call (probes included):

| participants | calls | model time, median (90th pct) | queueing behind other calls, median (90th pct) | prompt / completion tokens, median |
|---|---|---|---|---|
| 9 | 56 | 3.5 s (7.7 s) | 3.2 s (7.2 s) | 2,388 / 65 |
| 48 | 320 | 5.9 s (19.4 s) | 5.5 s (19.1 s) | 2,733 / 62 |

Model time is the gateway's own dispatch-to-response time (LM Studio's `created` stamp, at 1 s resolution, gives 4.2 s and
6.3 s). Arrival to dispatch, besides the queue, is under the 1 s resolution of the records. The setup model's chats
(`ternary-bonsai-2-27b`) took 11.6 s at 9 participants and 33.3 s at 48 (median); embeddings 0.02 to 0.03 s. Calls are
serial by design (one request in flight per platform, one serial gateway queue), so at 48 participants a turn waits about
as long as it runs. Nothing was removed: no admission or ownership check costs a measurable share of a turn.

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
the tool before these repairs; leave them unlaunched. `swarm48-b` was staged at the old ratio 1.25 (wall 55,800 s); stage
`swarm48` again before any launch.

Staged on 2026-11-15 as proof of model staging, not launched, no gate, no preflight: `valedia-native-20261115-swarm8-q8-b`
(`swarm8` on `qwen3-8b`, candidate `a42c81cb510dd48fd3542183d0245ea9a976fd7556679d6018b42febca87610e`). It supersedes `valedia-native-20261115-swarm8-q8-a`
(candidate `82025f4a…`), whose manifest called the `src/` revisions simply reviewed; leave `-q8-a` unlaunched. The folder that is actually launched is staged from `main` after the
merge, so that its manifest names the repository's own files.

Staged on 2026-11-15 as proof of plan staging, not launched, no gate, no preflight: `valedia-native-20261115-swarm8-b2q8-a`
(`swarm8`, setup `ternary-bonsai-2-27b`, turns `qwen3-8b`, candidate
`6b8c64eb19d80d149514f02adb6a9cd8a8d36be5eb71192468fd65c90b7fb514`). A staged folder carries the model server's port as of staging;
stage again if the server app restarts.

The result is `<work>/builder/native-run-receipt.json` (events, model-call count, whether the report validated, what the
paper step did) and `<work>/builder/gateway-ledger.json` (every model call).
