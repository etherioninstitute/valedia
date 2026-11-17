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

- LM Link's state (`lms link status`): disabled only when the command exits 0 and the first non-empty line of its standard
  output, or of its standard error when standard output is empty, blanks removed, begins with exactly
  `You have disabled LM Link.` (the real wording, read 2026-11-17; measured that night: 0 bytes on standard output, the one
  line on standard error, exit 0). Anything else is "not known to be disabled" and refuses. The tool never changes it.
- LM Studio's model list (`GET /api/v1/models`), judged with the pure functions of `valedia_trader/core/decision_model.py`:
  the decision instance `elephant-1` loaded and right (file and the four settings); the embedding instance
  `ym-research-embedding` loaded; every other loaded instance by name (what a run would unload); any `mf-native-*` instance.
  A second instance of the decision model (its key, or an id beginning `elephant-1`) makes the decision model "not right"
  (the collector needs exactly one right instance), so the start is refused; it is never an "other" model and never unloaded.
- The collector: its launchd job `com.valedia.orderbook-collector` listed; the last 10 decisions' status and latency (shown);
  the decisions of the last 15 minutes (judged by the breach rule below); the age of the last heartbeat. The store is opened read-only (`file:<path>?mode=ro`) and only `decisions.status`,
  `decisions.latency_s`, `decisions.decision_ts` and `heartbeats.ts` are ever read; the three statements are the module's
  `SQL` tuple and a test checks that nothing else can be issued.
- Memory: the free reading (`memory_pressure`), the kernel pressure level (`kern.memorystatus_vm_pressure_level`: 1 normal,
  2 warn, 4 critical) and swap used (`vm.swapusage`).
- The hourly pick reader's launchd state, the plist it was loaded from and, when it is running, its pid and start; free disk on `/Volumes/NIMROD_SSD`; unexpired gate
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
  also runs on Ctrl-C and on any exception, bootstrapped back from the same plist it was loaded from. The intent to restore is
  recorded before the bootout is issued, and the restore decides by the job's real state (not listed: bootstrap), so a Ctrl-C
  between the bootout and the next line cannot skip it. Every restore step runs to its end, whatever is raised inside it
  (Ctrl-C and SystemExit included): an interrupt in one step is kept, the next step still runs, and only after the record row
  and the summary are written is it raised again. A failed restore is exit 3, `PICK READER NOT RESTORED` in capitals in the
  summary, and the record row says so. If it was not loaded, it is left alone.
  If it is RUNNING (it runs a few minutes at the top of the hour), the start is refused with its start time: a reader is never
  booted out mid-run. Its state is read again just before the bootout, so one that started after the check also refuses.
  Besides those two reads, no switch-off begins from 2 minutes before the reader's scheduled start until it has been read
  RUNNING after that start and is read idle now, or until 3 minutes have passed since that start with it idle at every
  reading (a missed start), so that it cannot start between the read and the bootout. The readings that count are those in
  the record rows of earlier `run` and `load-test` invocations (each row keeps its readings and their times); `check` writes
  no row, so what it saw does not count. The scheduled minute is the `"Minute" => N` of its calendar interval in
  `launchctl print`; the full hour when none is read. The clock is the local one. At :57:59 an idle reader may be switched
  off; from :58:00 the start is refused ("due at :00"); at :00:30 a reader read running refuses (and is recorded), and once it
  is read idle again the start goes ahead; an idle reader never seen running refuses until :03:00. The plist in this repo
  (`tools/launchd/com.valedia.picks.hourly.plist`) schedules it at minute 7, so the window there is :05:00 to :10:00 unless it
  is seen running and then idle sooner.
- every loaded model that is neither the decision instance nor the embedding instance: unloaded by REST
  (`POST /api/v1/models/unload {"instance_id": ...}`) and named in the record. They are NOT reloaded: LM Studio loads each again
  on demand.

Not switched by the tool, to be kept off by hand: the local Hermes bots (Scout, Valedia, CTO, local delegation workers) and
anything else that uses LM Studio.

The tool never stops, signals, restarts, boots out or reconfigures the collector or its launchd job, never sends any request
to the decision instance and never reloads a model. If, after any command, the decision instance is not loaded and right, the
summary and the record say `DECISION INSTANCE NOT LOADED AND RIGHT` and the tool leaves it to the collector's own keeper.

## Start rules (each refuses with exit 2)

In this order: (a) every rule below that needs no switch-off, read before anything is changed; (b) staging the folder and
its twin (staging switches nothing off) and the old-pipeline rule; (c) only then the switch-offs; (d) the free reading
against the staged start threshold, taken 15 s after the unloads (LM Studio answers an unload once it has released the
instance, but the kernel's free figure follows over a few seconds as pages are reclaimed; read at once it under-reads; 15 s
is a few times that lag and about 1% of the window; no unload, no wait); under the threshold restores, stops, and says which
models were unloaded and that LM Studio loads them again on demand; (e) the twin's preflight; (f) the gate and the launch. A
refusal in (a) or (b) has made no change at all.

LM Link not disabled; the decision instance not loaded and right; an `mf-native-*` instance already loaded; the collector
already in breach (the breach rule below over the decisions of the last 15 minutes, its last heartbeat and its job), or too
quiet to judge (fewer than 5 decisions in those 15 minutes: a run must not start blind); the hourly pick reader running, or
due within 2 minutes, or due and not yet seen running and then idle after its start; a second decision-model instance; the
kernel pressure level not 1; no free reading; an unexpired gate file (`root-one-native-attempt.json` whose `expires_at_utc` is still ahead) or a live observer
(`builder/observer-identity.json` whose pid, start and process group all match a running process) of an earlier run under the
crew folder; and, after staging, a free reading under the staged folder's own start threshold (the manifest's
`model_profile.start_threshold.percent_free`, the reviewed 65 without a model), said with both numbers. Expired gates of past
runs do not refuse; unreadable gate or identity files do (fail closed).

A run that cannot be stopped is not started: before the gate is written, the staged `owned-source/observer-entry/
mirofish_native_run.py` must contain the code that writes `builder/observer-identity.json` (searched by that file name). A
revision without it refuses with "the staged pipeline revision is too old"; no gate is written.

## Breach (checked every 30 s during `load-test` and `run`)

- two decisions in a row (in decision time, across all markets) slower than 8 s;
- a decision with the status `model_unavailable` or `tag_mismatch`: at once (they can only mean the decision model is gone or
  wrong);
- `late` or `error`: only when two of three consecutive decisions carry one of them (a single one happens on an ordinary day:
  a per-market exception writes `error` with the model untouched). The tool reads only the status, never the error text;
- no heartbeat for 150 s (the collector writes one about every 60 s);
- the collector's launchd job no longer listed.

`ok` and `stale_data` are never a breach.

On a breach `run` stops the run the documented way: TERM to the observer named in `<work>/builder/observer-identity.json`
(`{"pid", "start", "pgid", "written_utc"}`, written by the pipeline at spawn), only when ALL hold: the file is well formed;
the named pid is alive with exactly that start time and group; its PARENT pid (`ps -o ppid=`) is the launcher this tool
started, whose own pid and start still match what was recorded at spawn; and the pid is not the tool's own, not the tool's
parent, not 1 or below. Nothing else is ever sent TERM; never on a pid alone, and at most once. It then waits for the pipeline's own cleanup (the launcher
ending, at most 180 s after the signal), verifies that no `mf-native-*` instance is loaded and that the decision instance is still
loaded and right, restores, and records `stopped: collector breach` with the figures. `load-test` unloads its test model
through the pipeline's own unload instead. Ctrl-C stops the run the same way before restoring.

The launcher is started by this tool in its own session and process group, and its pid, start and group are recorded at
launch. If the identity file has not appeared 60 s after the launch, the summary says so in capitals and the watch goes on.
An identity that fails the rule above for ANY reason (missing, unreadable, malformed, a reused pid, not our launcher's child)
is "no usable identity": from 60 s after the launch, at the first breach, the stop signal is then SIGINT to the launcher this tool started, never TERM and never a group signal, and only
when the launcher is still the process that was started (pid, start and group all match); the record says
`stop.by: "launcher interrupt"`. SIGINT raises KeyboardInterrupt in the launcher, whose own handler stops the observer group
the pipeline's way (`src/mirofish_native_startup.py`, lines 169 to 190); a TERM would kill the launcher without that cleanup.
After it the tool waits the same 180 s as for the normal stop, counted from the signal, then reads the model list: an
`mf-native-*` instance still loaded is said as `MF-NATIVE INSTANCE STILL LOADED AFTER THE LAUNCHER INTERRUPT` in capitals in the
summary and the record (`stop.after_check`); the tool unloads nothing it did not load. A launcher that is not in a group of its
own, or whose start or group has changed, is never signalled; that is said in capitals as well.

The watch has a hard deadline: the profile's wall time (from `tools/mirofish_swarm_limits.py`, through the stage tool's
`swarm_numbers`) plus the 120 s cleanup after the launch. Past it the run is stopped the same way and the record says
`stopped: wall exceeded`. A run that cannot be signalled at all still ends the watch 180 s after the deadline, with
`THE LAUNCHER WAS LEFT RUNNING` in capitals: the watch never runs on without end.

## What a breach costs

Measured from the rules above, the worst case: up to about 30 s before the breach is seen (the check runs every 30 s), then the
stop signal, then up to 180 s for the pipeline's own cleanup (its 120 s cleanup headroom and two more checks) before the
launcher has ended and the switch-offs are restored. So the collector can run beside a harmful load for about three and a half
minutes after the first bad decision. If the observer cannot be named, add up to the 60 s wait for its identity file.

## run

check; stage (`tools/mirofish_native_stage.py --stage --profile P [--model ...] --work <crew>/valedia-native-
<date>-<profile>-op<hhmmss>`) and stage a twin (`...-twin`); switch-offs; 15 s for the unloads to settle; free reading against
the staged threshold; preflight the twin
(`mirofish_native_startup.py --preflight`, which loads no model); the gate (`root-one-native-attempt.json`: outcome, action,
candidate hash, an expiry 30 minutes ahead, and the announcement); launch from the observer entry; watch; restore; append the
record row; a ten-line summary. Exit 0 ran to its end with no alert, 1 stopped or failed, 2 refused, 3 a restore failed, the
record was not written, or an alert in capitals. A Ctrl-C or SystemExit inside the restore or the recording is raised again
after the row and the summary are written (the shell then shows it).

## load-test

`load-test --model NAME --hold-minutes M` (M at least 2) measures what loading one of the pipeline's models costs beside the
collector: a fresh folder staged with `--profile swarm8 --model NAME`; the switch-offs as for a run; 60 s of baseline samples
0.5 s apart (free reading, pressure level, swap); the model loaded through the pipeline's own load path; the collector read at
once when the load call returns (`collector_after_load`; a breach there unloads at once); M minutes of samples;
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

`artifacts/mirofish/runs.jsonl`, one JSON object a line, appended and synced to disk. Before every append, a file that does
not end with a newline (a torn last line) gets one, so a new row is never glued to it; reading never raises on a damaged
line: it is skipped and counted, `record` prints the count, and a damaged line never counts as a run in either direction
(neither a counted run nor a reset). Every `run` that got past its arguments writes a row (a refusal too, with
`launched: false`); an interrupt while the row is being finished still appends a minimal row (`recording: "interrupted while
recording: ..."`, with what is known, never counting) before the interrupt is raised again. A row holds the start and end, profile, plan, staged folder,
the announcement, the switch-offs with before and after states, the pipeline outcome (receipt `complete`, its reason, calls,
failed and refused calls, both swaps, cleanup proven, `report_usable`), memory (`resource.lowest_sustained_free_percent_10s`,
`resource.max_pressure_level`, `resource.co_resident_rss_bytes_max` from the receipt where present, else `null` and a note;
swap at the start and end; the tool's own 30 s readings), the collector during the run and three verdicts:

- `collector_unharmed`: false when there was a breach, or any decision over 8 s, or any `model_unavailable`, `tag_mismatch` or
  `late`, or two or more `error` during the run. A single `error` does not fail it. The row's `collector.statuses` carries the
  count of each status, so a person can read it;
- `pipeline_clean`: a receipt exists and is readable; no failed or refused call; cleanup proven; both swaps clean (one
  model: not applicable); no stop on the memory rule; and EITHER `complete` is true with no failure and the launcher's exit
  0, OR `complete` is false for exactly the near-neutral reason with the launcher's exit 1. That reason is the string
  `derived report not usable evidence`, raised as `Refused` at `src/mirofish_provenance.py:437` when the report's signal is
  within 5 points of 50, and stored as `failure: {"type": "Refused", "reason": ...}` by `src/mirofish_native_run.py:806`;
  any other reason, a missing reason, or one of another type is not clean. The launcher's exit code is recorded
  (`launcher.exit`, `pipeline.launcher_exit`); a zero exit with `complete` false, or a missing receipt, is not clean. A report
  the paper step refused (`paper_handoff: false` with `complete` true) is recorded as `report_usable: false` and is clean;
- `room_to_spare`: the lowest sustained free reading over 10 s is at least 10 points above the 30 stop floor (40 or more).
  `null` when the receipt does not carry it.

A run COUNTS only when all three are true. `record` prints the rows and the current streak of counted runs at 9 participants
(`swarm8`, target 10 in a row) and at 48 (`swarm48`, target 3 in a row); one launched run that does not count resets its
streak; refusals do not touch it. The word "reliable" waits for those targets.
