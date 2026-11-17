# MiroFish operator: one command for a run beside the collector (TMF6, 2026-11-16)

`tools/mirofish_operator.py` does the hand recipe of `docs/mirofish-swarm.md` end to end: check, switch off, stage and stage a
twin, preflight the twin, gate, launch, watch the order-book collector, stop on a breach, restore, record. Run it with the
MiroFish Python (the load test imports the staged pipeline modules):

```bash
PY=/Volumes/NIMROD_SSD/Qwen-Weight-Space/runtime/mirofish-local-20261106/venv-py311/bin/python
$PY -B tools/mirofish_operator.py check --profile swarm8 --model qwen3-8b          # read-only, safe at any time
$PY -B tools/mirofish_operator.py run --profile swarm8 --model qwen3-8b --announced "Told Burton 14:05: swarm8 on the 8B, ..."
$PY -B tools/mirofish_operator.py load-test --model qwen3-8b --hold-minutes 5 --announced "Told Burton 14:05: 8B load test"
$PY -B tools/mirofish_operator.py record
```

Every subcommand takes `--dry-run`: it prints what it would do and changes nothing (`check --dry-run` reads nothing either;
`run --dry-run` and `load-test --dry-run` read as `check` does). A plan of two models is `--setup-model A --turns-model B`
instead of `--model`; without either, the reviewed 27B bytes are staged.

## check

Read-only. It prints, and with `--json-out FILE` (or `--json`) returns as JSON:

- LM Link's state (`lms link status`): it must read `disabled` and nowhere `enabled`; the tool never changes it.
- LM Studio's model list (`GET /api/v1/models`), judged with the pure functions of `valedia_trader/core/decision_model.py`:
  the decision instance `elephant-1` loaded and right (file and the four settings); the embedding instance
  `ym-research-embedding` loaded; every other loaded instance by name (what a run would unload); any `mf-native-*` instance.
- The collector: its launchd job `com.valedia.orderbook-collector` listed; the last 10 decisions' status and latency; the age
  of the last heartbeat. The store is opened read-only (`file:<path>?mode=ro`) and only `decisions.status`,
  `decisions.latency_s`, `decisions.decision_ts` and `heartbeats.ts` are ever read; the three statements are the module's
  `SQL` tuple and a test checks that nothing else can be issued.
- Memory: the free reading (`memory_pressure`), the kernel pressure level (`kern.memorystatus_vm_pressure_level`: 1 normal,
  2 warn, 4 critical) and swap used (`vm.swapusage`).
- The hourly pick reader's launchd state and the plist it was loaded from; free disk on `/Volumes/NIMROD_SSD`; unexpired gate
  files and live observers of earlier runs under the crew folder.
- The ANNOUNCEMENT: start time, expected duration and wall clock for the chosen profile and model (from
  `tools/mirofish_swarm_limits.py` through the stage tool's `swarm_numbers`), and by name everything that will be switched off
  and for how long. The lead gives this text to the owner before a run.

## The announcement and `--announced`

`load-test` and `run` refuse without `--announced TEXT`. The text is the lead's statement that the owner was told; it is
stored verbatim in the result and the record row. The tool cannot check it and says so in every summary.

## What is switched off, and how it comes back

For the whole window (the wall clock plus the 120 s cleanup), each named in the announcement and recorded with its before-state:

- the hourly pick reader `com.valedia.picks.hourly`: booted out (`launchctl bootout gui/<uid>/...`), and in a `finally` that
  also runs on Ctrl-C and on any exception, bootstrapped back from the same plist it was loaded from. A failed restore is exit
  3, `PICK READER NOT RESTORED` in capitals in the summary, and the record row says so. If it was not loaded, it is left alone.
- every loaded model that is neither the decision instance nor the embedding instance: unloaded by REST
  (`POST /api/v1/models/unload {"instance_id": ...}`) and named in the record. They are NOT reloaded: LM Studio loads each again
  on demand.

Not switched by the tool, to be kept off by hand: the local Hermes bots (Scout, Valedia, CTO, local delegation workers) and
anything else that uses LM Studio.

The tool never stops, signals, restarts, boots out or reconfigures the collector or its launchd job, never sends any request
to the decision instance and never reloads a model. If, after any command, the decision instance is not loaded and right, the
summary and the record say `DECISION INSTANCE NOT LOADED AND RIGHT` and the tool leaves it to the collector's own keeper.

## Start rules (each refuses with exit 2)

LM Link not disabled; the decision instance not loaded and right; an `mf-native-*` instance already loaded; the collector
already in breach (over its last 10 decisions, its last heartbeat and its job); the kernel pressure level not 1; no free
reading; an unexpired gate file (`root-one-native-attempt.json` whose `expires_at_utc` is still ahead) or a live observer
(`builder/observer-identity.json` whose pid, start and process group all match a running process) of an earlier run under the
crew folder; and, after staging, a free reading under the staged folder's own start threshold (the manifest's
`model_profile.start_threshold.percent_free`, the reviewed 65 without a model), said with both numbers. Expired gates of past
runs do not refuse; unreadable gate or identity files do (fail closed).

## Breach (checked every 30 s during `load-test` and `run`)

- two decisions in a row (in decision time, across all markets) slower than 8 s;
- a decision with a model-failure status: `model_unavailable`, `tag_mismatch`, `late`, `error` (every status the collector's
  model path writes; `ok` and `stale_data` are not model failures);
- no heartbeat for 90 s;
- the collector's launchd job no longer listed.

On a breach `run` stops the run the documented way: TERM to the observer named in `<work>/builder/observer-identity.json`
(`{"pid", "start", "pgid", "written_utc"}`, written by the pipeline at spawn), only after its pid, start time and process group
all match the live process; never on a pid alone, and at most once. It then waits for the pipeline's own cleanup (the launcher
ending, at most 180 s after the TERM), verifies that no `mf-native-*` instance is loaded and that the decision instance is still
loaded and right, restores, and records `stopped: collector breach` with the figures. `load-test` unloads its test model
through the pipeline's own unload instead. Ctrl-C stops the run the same way before restoring.

## run

check; switch-offs; stage (`tools/mirofish_native_stage.py --stage --profile P [--model ...] --work <crew>/valedia-native-
<date>-<profile>-op<hhmmss>`) and stage a twin (`...-twin`); free reading against the staged threshold; preflight the twin
(`mirofish_native_startup.py --preflight`, which loads no model); the gate (`root-one-native-attempt.json`: outcome, action,
candidate hash, an expiry 30 minutes ahead, and the announcement); launch from the observer entry; watch; restore; append the
record row; a ten-line summary. Exit 0 ran to its end with no alert, 1 stopped or failed, 2 refused, 3 a restore failed, the
record was not written, or an alert in capitals.

## load-test

`load-test --model NAME --hold-minutes M` (M at least 2) measures what loading one of the pipeline's models costs beside the
collector: the switch-offs as for a run; a fresh folder staged with `--profile swarm8 --model NAME`; 60 s of baseline samples
0.5 s apart (free reading, pressure level, swap); the model loaded through the pipeline's own load path; M minutes of samples;
the unload through the pipeline's own unload, proven; restore. The load path is that of `tools/mirofish_format_qualify.py`,
reused function by function: its `staged_modules` and `check_folder` on the fresh folder, the folder's own helper copied into a
fresh `<folder>-out`, the staged model module's SDK bridge, `baseline`, `load` under an `mf-native-<hex>` identifier with the
profile's pinned checkpoint and settings, read back by the staged `check_load_readback`, and `unload` proven by its
`unload_confirmed`. Nothing is generated.

The result (`artifacts/mirofish/load-tests/<utc>-<model>.json`): baseline and after-load median and lowest free reading,
`setup_cost_points` (baseline median minus the median of the hold's first 60 s), `fall_points` (the hold's first 60 s against
its last 60 s, medians), highest pressure level, swap change, and the collector's decisions during the test (count, statuses,
highest latency). These two numbers feed the start threshold's table.

## The run record

`artifacts/mirofish/runs.jsonl`, one JSON object a line, appended and synced to disk. Every `run` that got past its
arguments writes a row (a refusal too, with `launched: false`). A row holds the start and end, profile, plan, staged folder,
the announcement, the switch-offs with before and after states, the pipeline outcome (receipt `complete`, its reason, calls,
failed and refused calls, both swaps, cleanup proven, `report_usable`), memory (`resource.lowest_sustained_free_percent_10s`,
`resource.max_pressure_level`, `resource.co_resident_rss_bytes_max` from the receipt where present, else `null` and a note;
swap at the start and end; the tool's own 30 s readings), the collector during the run and three verdicts:

- `collector_unharmed`: no breach, no decision over 8 s, no model-failure status;
- `pipeline_clean`: no failed or refused call, no stop on the memory rule, both swaps clean (one model: not applicable),
  cleanup proven, no stop. A near-neutral report is not a pipeline fault: it is recorded as `report_usable: false`;
- `room_to_spare`: the lowest sustained free reading over 10 s is at least 10 points above the 30 stop floor (40 or more).
  `null` when the receipt does not carry it.

A run COUNTS only when all three are true. `record` prints the rows and the current streak of counted runs at 9 participants
(`swarm8`, target 10 in a row) and at 48 (`swarm48`, target 3 in a row); one launched run that does not count resets its
streak; refusals do not touch it. The word "reliable" waits for those targets.
