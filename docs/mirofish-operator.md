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
$PY -B tools/mirofish_operator.py recover [--stop]                                  # after a run that was killed
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
  An instance is the decision or the embedding instance only as its expected PAIR of instance id and model key
  (`elephant-1` under `elephant-1`; `ym-research-embedding` under `google/embedding-gemma-300m`, the key
  `src/mirofish_native_model.py:293` requires). A listing in which a protected id is loaded under another key (the
  embedding's id under the 27B's key, say), a protected key under another id, or any loaded instance has an id or a key that
  is not a plain string, or a protected pair loaded more than once, is refused before any switch-off (`check` prints it as
  `REFUSED: the model list cannot be judged: ...; the tool does not guess ...`): the tool never decides which instance is
  meant. A plain string is a `str`, non-empty, with no leading or trailing space, that encodes as UTF-8. No unload is ever
  sent with an id or a key that is not a plain string: the switch-off refuses (an `OperatorError`, nothing posted) before
  it reads or changes anything, and every single unload checks again. A list that cannot be read refuses too.
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
- The durations line: `expected N s, wall M s` from `tools/mirofish_swarm_limits.py` for the chosen profile AND plan (through
  the stage tool's `swarm_numbers`). The watch itself uses the deadline read from the staged folder after staging (see the
  watch deadline below); `run` prints both before it switches anything off.
- An intent file of an unfinished run (`artifacts/mirofish/operator-intent.json`): `AN UNFINISHED RUN'S INTENT FILE EXISTS`
  in capitals, and `check`, `run` and `load-test` refuse until `recover` has run.

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
- every loaded model that is neither the decision instance nor the embedding instance (exactly: every loaded instance
  whose id is not `elephant-1`, `ym-research-embedding`, an `mf-native-*` id or an id beginning `elephant-1`, and whose
  model key is not `elephant-1`; anything else, a model under another key with an id such as `copy` included): unloaded by REST.
  Every instance a run unloads is named (id and key) in the announcement printed before the first switch-off and in the
  row, and nothing outside that announced list is unloaded. A model loaded after the announcement is never unloaded, and
  it is named (key and id) in the row's `loaded_after_announcement`: BEFORE the spawn (seen at the unloads or in the check
  just before the gate; for a load test, just before the load call) nothing is launched, everything is restored and the
  row is a refusal naming it (exit 2), since the start check and what the owner was told no longer hold; AFTER the spawn
  (seen at every 30 s check and once more at the end) it is said at once in capitals (`MODEL LOADED AFTER THE
  ANNOUNCEMENT`), the stop rule governs the run as before, and the run does NOT count, whatever else it shows.
  A model list that cannot be read BEFORE the spawn (the read after the unloads, the read just before the gate, or before
  a load test's load call) is a refusal (`THE MODEL LIST COULD NOT BE READ`, not launched or not loaded, everything
  restored, `model_list_unread: true` in the row, exit 2).
  THE ANNOUNCED SET, AFTER THE SPAWN. At the announcement the row records in `remaining` the exact set of loaded
  instances (id and key) that stay loaded during the run: the listing with the announced unloads taken out. At every
  check after the spawn (each 30 s collector check) and once more at the end, the listing must be readable and equal to
  that set exactly, with the decision instance loaded and right; a run counts only if every such check held
  (`model_checks`, and `model_check_failures` empty; the one place this is decided is `finish_run_row`, where
  `counts` is the verdicts and `models_held`). Anything else at a check (an extra instance, a missing one, a protected
  pair broken, the decision instance not right, a listing that cannot be read even once) is said in capitals at that
  check (`THE LOADED MODELS DO NOT HOLD AT <utc>: <each problem by name>; THE RUN DOES NOT COUNT`), kept with its time in
  `model_check_failures`, and the run does not count. The alert is given at the first check of every bad stretch,
  a second stretch included, and again whenever the problems change. The pipeline's own `mf-native-*` instances are
  part of the run during the watch; at the end none may be left. The load test's hold is held to the same rule (its own
  instance allowed during the hold, `models_held` in its result). The remaining limit: a model that appears and is gone
  again between two checks is not seen by any check. The load test's own `mf-native-<hex>` instance is
  named in the intent file before its load call and is the only instance its unload names
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

An intent file of an unfinished run (in capitals: run `recover` first); LM Link not disabled; the decision instance not loaded and right; an `mf-native-*` instance already loaded; the collector
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
revision without it refuses with "the staged pipeline revision is too old"; no gate is written. A staged folder without
exactly one readable run deadline in its startup script, or whose manifest says a different wall, refuses in step (b) as
well (see the watch deadline below).

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
through the pipeline's own unload instead. Ctrl-C, a TERM, a hang-up, an exception or an exit stops the run the same way
before restoring (one path, below).

The launcher is started by this tool in its own session and process group. Its handle is bound to the stop path's state in
the very statement that spawns it (`stopper.proc = host.spawn(...)` in `launch`, `tools/mirofish_operator.py`), with every
signal held for that one statement and raised just after it, so nothing runs between the spawn and the binding: from then on
the launcher is this tool's own unreaped child, and its pid is the handle's own (such a pid cannot belong to another process
until the tool reaps it). Its start and group are then read with `ps`, up to 3 times 0.5 s apart; a start that cannot be
read is recorded as `null` and does NOT disarm the stop. If the identity file has not appeared 60 s after the launch, the
summary says so in capitals and the watch goes on. An identity that fails the rule above for ANY reason (missing,
unreadable, malformed, a reused pid, not our launcher's child) is "no usable identity": from 60 s after the launch, at the
first breach, the stop signal is then SIGINT sent THROUGH THE LAUNCHER'S HANDLE (`Popen.send_signal`, which never signals a
child already reaped), never TERM and never a group signal; when a start was read, only while the launcher's pid, start and
group still match. The record says `stop.by: "launcher interrupt"`. SIGINT raises KeyboardInterrupt in the launcher, whose
own handler stops the observer group the pipeline's way (`src/mirofish_native_startup.py`, lines 169 to 190); a TERM would
kill the launcher without that cleanup. After it the tool waits the same 180 s as for the normal stop, counted from the
signal, then reads the model list: an `mf-native-*` instance still loaded is said as `MF-NATIVE INSTANCE STILL LOADED AFTER
THE LAUNCHER INTERRUPT` in capitals in the summary and the record (`stop.after_check`); the tool unloads nothing it did not
load. A launcher whose recorded start or group has changed is never signalled; that is said in capitals.

From the spawn on, ANY way out (an exception, Ctrl-C, a TERM or a hang-up, a SystemExit, before or during the watch) goes
through one path: stop the run (the observer by its identity when usable, else the launcher through its handle), wait for
its cleanup, check for leftover `mf-native-*` instances, restore, write the row with what happened. Once the spawn has
returned the row never says `launched: false`. A SystemExit is raised again after the row and the summary.

The watch deadline comes from the STAGED folder, not from the repository: the stager writes the profile's wall into the
staged `owned-source/observer-entry/mirofish_native_startup.py` as `deadline=time.monotonic()+(30 if preflight else N)`
(line 145 of `src/mirofish_native_startup.py`, edited by `swarm_startup_bytes` in `tools/mirofish_native_stage.py`) and into
`builder/candidate-manifest.json` as `uniform_policy.wall_seconds`. The startup script's N (the launcher's own deadline) is
used; the manifest must say the same, or the run is refused before any switch-off. The row records it as `deadline`
(`staged_wall_s`, `source`, `manifest_wall_seconds`, `watch_deadline_s`), and `run` prints both the expected duration from
`tools/mirofish_swarm_limits.py` and the staged deadline before it switches anything off. Past N plus the 120 s cleanup after
the launch the run is stopped the same way and the record says `stopped: wall exceeded`. A run that cannot be signalled at all still ends the watch 180 s after the deadline, with
`THE LAUNCHER WAS LEFT RUNNING` in capitals: the watch never runs on without end.

## Signals, the intent file and `recover`

`run` and `load-test` install handlers for TERM, HUP and INT before the first switch-off. A TERM or a hang-up (a closed
terminal, an ended session, a caller's timeout) takes the same controlled path as a Ctrl-C: stop the run, restore, record
`stopped: operator signalled (SIGTERM)` (or `SIGHUP`), exit 1. At most one signal ever raises: one that arrives during the
stop, the restore or the recording is only listed in the row's `signals_received` with the stage it came in (`the stop`,
`the restore`, `finishing the row`, `the row`, ...), and never aborts that path; one that arrives while the row itself is
being written is recorded in a following `{"command": "signal-note", ...}` line. A run that had counted keeps its `counts`.
A hang-up that is already ignored when the tool starts (it was started under `nohup`) stays ignored, and the row says
`hup_ignored: true`. The old handlers are put back at the end.

**The lock.** `run` and `load-test` hold an exclusive advisory lock (`flock`) on `artifacts/mirofish/operator.lock` from
before their first changing action until after their row; `recover` holds it while it works. Each takes it without
waiting and refuses in capitals (`ANOTHER OPERATOR COMMAND HOLDS THE LOCK`) when it is held. A command refused by the lock
writes NOTHING: its one refusal line on standard output and exit 2; no record row, no load-test result file (two load
tests started in the same second would otherwise share the holder's result path), no intent file. The kernel drops the lock when
its holder ends, however it ends (a KILL included), so whether the tool that wrote an intent file is still alive is asked
of the kernel, never read from a pid. The launcher does not inherit it. The lock file is never removed.

**The intent file.** What no handler can catch (a KILL, a crash, a power cut) is caught by
`artifacts/mirofish/operator-intent.json`, written and synced (a temporary file synced, renamed into place, the folder
synced) before the first switch-off. It holds: the command and the profile; the time; this tool's own pid and start; the
pick reader's label and before-state (`listed`, `plist`, `state`); the instance ids about to be unloaded (and that they are
not reloaded); the staged folder and its twin; the record path; the two scripts that prove a survivor
(`{"launcher": "mirofish_native_startup.py", "observer": "mirofish_native_run.py"}`); `launch.spawning_utc`, written and
synced just BEFORE the spawn; the launcher's pid, written inside the statement that binds its handle, then its start and
group once read (for a load test, its `mf-native-*` identifier). It is removed only after the restore and the row are done
and the launcher has ended; otherwise it stays, with a capitals line saying so and exit 3, so that no path ends with neither
a row nor the intent file. While it exists, `check`, `run` and `load-test` refuse in capitals.

**`recover`** is for after such an end. In order:

1. It takes the lock (a live writer holds it: refused, exit 2). No intent file: "no intent file", exit 0.
2. The intent is parsed against its one typed shape (every field and its type, nothing else; a string with a lone
   surrogate, which a JSON file can carry as an escape, is not a string of the shape); a field that fails is named
   and nothing is done, the file kept (exit 2). The pick reader's launchd job is read once before anything else; a read that
   raises is a refusal in words with the error's type only (nothing done, the file kept, exit 2); one that raises later
   holds the reader off and keeps the file (exit 3). Then it is checked against the tool's constants: the pick reader's label
   `com.valedia.picks.hourly`, its one plist `~/Library/LaunchAgents/com.valedia.picks.hourly.plist`, and a work folder that
   is a staged `valedia-native-*` folder of the crew folder; a `MISMATCH` is printed and nothing is done (exit 2). (`check`,
   `run` and `load-test`, for their part, refuse before any change when the reader is loaded from any other plist.)
3. Survivors are found by the scan of this user's processes ALONE (`ps -U <uid> -o pid=`; a scan that fails, or that does
   not list this very tool, could not be made). EVERY process it lists is put to the proof in BOTH roles, launcher and
   observer, whatever any file proposed. The intent's launcher entry and the staged folder's observer identity file are
   hints only: they add nothing except the start the intent holds for its launcher, which is compared in the launcher role
   alone; a proposed pid proven in no role is reported by its pid as not this run's (so an intent naming the observer as
   the launcher, an identity file naming the launcher, or both naming unrelated live pids, still leave the true launcher
   and observer found in their true roles). Only a pid that passes ALL of the proof is a survivor: above 1; not this
   process or any ancestor; not the job pid of `com.valedia.orderbook-collector` or of `com.valedia.picks.hourly` (read
   from launchd by those fixed labels); its start read, and equal to the one held for it when one is held; and its own
   command read and naming BOTH the staged folder and the script of its role: the launcher
   `... -B mirofish_native_startup.py --work <staged folder> ...`, the observer
   `... -B <staged folder>/owned-source/observer-entry/mirofish_native_run.py`. The command is read (`ps -ww -p PID -o
   command=`) for that test alone and dropped at once: it is never printed, logged, stored, put in a row or in an exception
   text; only "it names them" or "it does not" leaves the test. A process whose command names neither role and that no file
   proposed is not a candidate. Two cases are kept apart: when the proof could NOT be made, nothing is changed, the file is
   kept and it says so in capitals (exit 3): a command that could not be read (the read raised, ps failed, or it answered
   with no string or an empty one, for a pid that is still alive), a start that could not be read (any ps answer other than
   its own "no such process": exit 1 with nothing on either stream), the job list unreadable, the scan failed. A command
   that WAS read and names the staged work folder in any token but fails the proof for both roles (the wrong script, a
   garbled form, a start that differs from the intent's, a job's pid) is "could not be decided", the same way: nothing is
   changed for the reader or the intent, it is said in capitals with its pid (`NOT SIGNALLED: THE PROOF COULD NOT BE MADE
   FOR pid N ...`, ending `could not be decided`), exit 3. (This tool, its ancestors and pids 1 or less are never
   candidates, whatever their command names.) Only when the proof was made and the command
   does not mention the folder at all (a reused pid, say), or the pid is one of those never candidates, is the process "not ours": reported by its
   pid only, never signalled, and it does not hold the recovery back.
   Every process start is read, recorded and compared in one fixed form: `ps -o lstart=` run with the environment
   `PATH=/usr/bin:/bin` and nothing else (no `TZ`, no locale variable), so the machine's own time zone and the C locale,
   whatever the caller's `TZ` or `LC_*` say. That is the same form the pipeline's observer writes in
   `builder/observer-identity.json` (its observer runs with `HOME` alone), so the two compare. A run started under one
   caller time zone and recovered under another finds its launcher. What it does not cover: a change of the machine's own
   time zone between the run and `recover`; the launcher's command still names the folder, so it is then "could not be
   decided" (exit 3), never "not ours".
4. Every proven survivor is reported in capitals (`LAUNCHER STILL RUNNING`, `OBSERVER STILL RUNNING`). Only with `--stop`
   is it stopped the documented way, each proven again (start and command) immediately before its own signal, so a pid
   that passed the scan and was replaced by another process is not signalled: TERM to a proven observer, then, if a
   proven launcher still runs, SIGINT to it; it waits up to 180 s for them to end. A survivor whose proof can no longer be
   made counts as still running.
5. While a proven survivor lives, or while a proof or the scan could not be made, the
   pick reader is NOT put back (the reader beside a live run is what the switch-off exists to prevent) and the intent file
   is kept. Without `--stop` it then changes nothing at all and writes no row (exit 3). Under `recover --stop`, when one
   survivor is proven and another process could not be decided, the proven one is stopped, the reader stays off, the
   intent is kept and the exit is 3.
6. Otherwise: the pick reader is bootstrapped from its one constant plist when it was loaded before and is not now (it
   bootstraps, boots out, loads or unloads nothing else); any `mf-native-*` instance still loaded is reported (nothing is
   unloaded) with the decision instance's state; a row `{"command": "recover", "recovered": "recovered after an unfinished
   run", "launched": ..., "profile": ...}` is appended; and the intent file is removed if the reader is back. A recover row
   of a run that was launched (or was being spawned) is a run that does not count: it resets that profile's streak.

Exit 0 when the recovery is complete with no alert, else 3. `recover --dry-run` reads, proves and prints, and changes
nothing.

Refusals are in words, never a traceback: `--setup-model` and `--turns-model` naming the same model (one model is
`--model`), or a model or plan that `tools/mirofish_swarm_limits.py` does not know, print `refused: ...` and exit 2 before
anything is changed. Every line the tool prints is made safe for the encoding of its standard output (a character it
cannot carry, a lone surrogate read from a file or a listing included, is written as a backslash escape), so no print of
text read from a file can raise.

A long run should not depend on the terminal that started it. Start it detached from the terminal, so that closing the
window or ending the session neither hangs it up nor kills it, and read its output from the log:

```bash
cd /Volumes/NIMROD_SSD/Qwen-Weight-Space/valedia-trader
nohup $PY -B tools/mirofish_operator.py run --profile swarm48 --model qwen3-8b --announced "..." \
  > artifacts/mirofish/operator-logs/run-$(date -u +%Y%m%dT%H%M%SZ).out 2>&1 < /dev/null &
```

or inside `tmux new -s mirofish` (detach with Ctrl-B D, reattach with `tmux attach -t mirofish`). To stop such a run, send
it TERM (`kill <pid of the operator>`): it stops, restores and records as for Ctrl-C. Never KILL it; if it was killed anyway,
run `recover` (then `recover --stop` if a run survives).

## What a breach costs

Measured from the rules above, the worst case: up to about 30 s before the breach is seen (the check runs every 30 s), then the
stop signal, then up to 180 s for the pipeline's own cleanup (its 120 s cleanup headroom and two more checks) before the
launcher has ended and the switch-offs are restored. So the collector can run beside a harmful load for about three and a half
minutes after the first bad decision. If the observer cannot be named, add up to the 60 s wait for its identity file.

## run

check; the signal handlers; stage (`tools/mirofish_native_stage.py --stage --profile P [--model ...] --work <crew>/valedia-native-
<date>-<profile>-op<hhmmss>`) and stage a twin (`...-twin`); the staged deadline; the intent file; switch-offs; 15 s for the unloads to settle; free reading against
the staged threshold; preflight the twin
(`mirofish_native_startup.py --preflight`, which loads no model); the gate (`root-one-native-attempt.json`: outcome, action,
candidate hash, an expiry 30 minutes ahead, and the announcement); launch from the observer entry; watch; restore; append the
record row; remove the intent file; a ten-line summary. Exit 0 exactly when the run counts (its row written) and the
restore succeeded, whatever else was alerted or signalled during the cleanup; 2 refused; 3 a restore failed, the record was
not written, the intent file was left, or the run did not count with an alert in capitals; 1 otherwise (stopped, failed,
or ran to its end without counting). The announcement printed at the start, after staging, states the STAGED wall. A Ctrl-C or SystemExit inside the restore or the recording is raised again
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
highest latency). These two numbers feed the start threshold's table. It also holds `remaining` (the announced set),
`model_checks`, `model_check_failures` and `models_held`: true only when the listing equalled the announced set (its own
instance allowed during the hold, not after the unload) at every check after the load and at the end.

## The run record

`artifacts/mirofish/runs.jsonl`, one JSON object a line, appended and synced to disk. Before every append, a file that does
not end with a newline (a torn last line) gets one, so a new row is never glued to it; reading never raises on a damaged
line: it is skipped and counted, `record` prints the count, and a damaged line never counts as a run in either direction
(neither a counted run nor a reset). Every `run` that got past its arguments writes a row (a refusal too, with
`launched: false`; once the spawn has returned, always `launched: true`); an interrupt while the row is being finished still appends a minimal row (`recording: "interrupted while
recording: ..."`, with what is known, never counting) before the interrupt is raised again. A row holds the start and end, profile, plan, staged folder,
the announcement, the switch-offs with before and after states, the pipeline outcome (receipt `complete`, its reason, calls,
failed and refused calls, both swaps, cleanup proven, `report_usable`), memory (`resource.lowest_sustained_free_percent_10s`,
`resource.max_pressure_level`, `resource.co_resident_rss_bytes_max` from the receipt where present, else `null` and a note;
swap at the start and end; the tool's own 30 s readings), the collector during the run and three verdicts:

- `collector_unharmed`: false when there was a breach, or any decision over 8 s, or any `model_unavailable`, `tag_mismatch` or
  `late`, or two or more `error` during the run. A single `error` does not fail it. The row's `collector.statuses` carries the
  count of each status, so a person can read it;
- `pipeline_clean`: a receipt exists and is readable (a JSON object whose `complete`, `cleanup_proven` and `paper_handoff`
  are booleans, `failure` an object, `events` a list of objects, `resource_failures` and `resource_failure_samples` lists and
  `resource` an object, each where present: any other type makes it unreadable, not clean, never an exception); no failed or refused call; cleanup proven; both swaps clean (one
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
streak; refusals do not touch it. A `recover` row of a launched run is such a run: it resets the streak; `signal-note`
lines are never runs. The word "reliable" waits for
those targets.
