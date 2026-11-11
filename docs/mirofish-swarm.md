# MiroFish: the guard decision and the swarm profile (2026-11-11)

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
| `swarm48` | 48 named (53 in the graph), up to 64 | 3, every agent each round | 640 | 4 hours | A real swarm |

`swarm48` changes closed numbers only (gateway budgets, deadlines, the participant bound, the round count), three selector
expressions, two concurrency integers, and the scenario with its seal. Per-call limits, the single dispatch slot, the
"one unknown result poisons the attempt" rule, the sandbox and the paper-only handoff are unchanged.

Its scenario (`tools/mirofish_swarm48_scenario.txt`) is invented and says so: 48 fictional participants react to a
fictional central-bank announcement, with SPY, TLT and GLD as topics. Every agent is made to take part in every round.
The run shows the pipeline at scale. It is not a forecast and says nothing about any market.

## What a run needs from the machine

- **The local model server to itself.** The run loads its own pinned 27B and stops if any other language model is
  loaded or gets loaded while it runs. No local Hermes bot (Scout, Valedia, CTO, local delegation workers) may be used
  until it ends. Cloud bots (Decider, Auditor, the default profile) are fine.
- The embedding model `google/embedding-gemma-300m` loaded under the name `ym-research-embedding`, context 2048.
- Memory headroom throughout; the run samples memory every 0.4 s and stops itself if the machine gets tight.
- About 15 minutes for `report64_coverage`; an estimated 2.5 to 3.5 hours for `swarm48` (one model call at a time,
  roughly 400 calls). The estimate comes from the 2026-11-08 run's per-call times; a swarm has not been run before.

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

## Stage, gate, launch

Each staged folder can be launched once. A gate is a small file naming the staged candidate's hash and an expiry at most
one hour ahead; the owner's go-ahead is what it records.

```bash
PY=/Volumes/NIMROD_SSD/Qwen-Weight-Space/runtime/mirofish-local-20261106/venv-py311/bin/python
cd tools && TMPDIR=/Volumes/NIMROD_SSD/Qwen-Weight-Space/project/tmp $PY -B mirofish_native_stage.py --stage --profile swarm48 --work <new folder under the crew directory>
# write <work>/root-one-native-attempt.json, then, from <work>/owned-source/observer-entry:
$PY -B mirofish_native_startup.py --work <work> --gate <work>/root-one-native-attempt.json
```

The result is `<work>/builder/native-run-receipt.json` (events, model-call count, whether the report validated, what the
paper step did) and `<work>/builder/gateway-ledger.json` (every model call).
