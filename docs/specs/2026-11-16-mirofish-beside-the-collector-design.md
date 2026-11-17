# MiroFish beside the order-book collector: plan

Date: 2026-11-16. Status: started. Owner's words (2026-11-16): "Yes, let's do this. MiroFish needs to be 200%
reliable across the board with the order-book collector".

## Where it stands

- The native swarm completes end to end on the two-model plan (Bonsai 2 for setup and the report, Qwen3 8B for the
  agent turns): 48 actors in 1 h 43 min, 516 calls, none failed, lowest free memory 65%. That was with the
  order-book collector switched OFF.
- With the collector ON, two attempts stopped on the run's own 30% free-memory floor during setup, before the first
  model swap.
- The scenario so far is invented, so a completed run proves scale and says nothing about markets.

## What "reliable with the collector" means here (the targets)

A run counts only if ALL of these hold; "reliable" means the stated number of runs IN A ROW with none failing.

1. The collector is never stopped, paused or slowed: its decisions per half hour during the run stay inside the band
   of the same hours on days without a run, and it records no failed model call that it would not otherwise have had.
2. The run finishes by itself: no stop on the memory rule, no refused or cut-off reply, no silent fall-back to
   default settings, both model swaps clean, cleanup proven.
3. It finishes with room to spare: the lowest free memory of the run is at least 10 points above whatever stop rule
   is then in force, and the machine's swap does not fill.
4. Faults are survived or refused cleanly, each shown by a drill: a local Hermes bot is messaged mid-run; the
   hourly pick reader fires; a linked computer loads a model; the model server restarts; the SSD's device number
   changes. For each drill the result is either "the run carried on unharmed" or "the run stopped itself at once,
   cleaned up, and the collector lost nothing".
5. Count: 10 runs in a row at the small size (9 participants) and 3 in a row at 48 actors, on different days and
   hours, before the word "reliable" is used. One failure resets the count.

## Order of work

1. **Measure before changing anything (S42, read-only, running).** A memory budget from the runs already recorded:
   what the rule measures, which processes held how much, what the Bonsai 2 load cost, the slow fall during setup,
   the dips and whether they line up with the collector's decisions, and the arithmetic of four cases (as today;
   the resident 27B unloaded for the run; Bonsai 2's load cost cut to its file size; both).
   Known already (2026-11-16 15:17 local, no run in progress): with the resident 27B and the collector's model both
   loaded the machine reads 32% free, with swap at 15.2 of 16 GB. The run's stop floor is 30%. So as the machine
   is normally set up there is no room for a run at all: the first decision is what gives way.
2. **Decide what gives way (owner's choice, after the budget).** The candidates, to be put to him with numbers: the
   resident 27B is unloaded for the length of a run and reloaded after (local bots that use it wait or are routed
   elsewhere); or the run's models are made smaller; or the swarm is smaller when the collector is on. The
   collector's own model is not a candidate: its test is in progress and its set-up is frozen.
3. **Fix the footprint.** Why an 8.3 GB model costs 16 to 25 points to load (the load settings are in pinned source);
   the slow fall during setup; the run's own peak at 48 actors (34 GiB against a 40 GiB bound).
4. **Make the memory rule measure the right thing.** A fixed "30% free" is a blunt reading on a machine that keeps
   idle models compressed. The rule is to be restated on what actually endangers the machine and the collector
   (room in GiB, swap, the system's own pressure level), with the dips after each collector decision allowed for,
   and proven both ways: it stops a run that would starve the collector, and it does not stop one that would not.
5. **Close the known small faults:** the brief clash at the model swap (one graph-search request refused in the swap
   second); the silent fall-back to default settings after a refusal; the fixed delay of about six seconds per agent
   turn; the format check's import path; the start-threshold formula.
6. **Prove it:** a load-only test beside the collector, then the small run, then the drills, then the counts in
   target 5.
7. **A real, scoreable scenario (separate from reliability, after it).** MiroFish enters the forecasting tournament
   as one more forecaster: real dated facts in, a probability out for a question that settles in days, scored
   against the base rate with every other forecaster. Its seat is judged by the same rules as the rest (model and
   release date recorded; forward questions only).

## Two rules from the owner, the same afternoon (they reshape steps 2 to 4)

His words: "Ollama shouldnt be used for anything at all. All local models should be handled via LM Studio" and
"there should be 2 capable models (or 1 capable model) for handling the collector and MiroFish".

**What the first measurements already say (2026-11-16).**
- The pipeline refuses to start while any other model is loaded in LM Studio, so the resident 27B is already out for
  every run. Both stops beside the collector happened with only the collector's model resident.
- The stop rule reads one instantaneous sample of the system's "free percentage". With the collector on, that
  reading dips 8 to 11 points for 2 to 5 seconds every minute, when the collector calls its model: about 5 GiB is
  wired for the length of the call (sampled at 0.4 s with no MiroFish model loaded: median 37%, dips to 27%). With
  the collector off no dip is deeper than 3 points. The two failing samples (27 and 29 against a floor of 30) were
  each within 0.6 s of a collector heartbeat.
- Loading Bonsai 2 cost 17 to 24 points for a 7.7 GiB file, and beside the collector the reading then fell another
  10 points over eight minutes with no process growing (cause unknown; it does not happen with the collector off).
- With the collector off the reading stays at 65 to 70 in every phase, at 9 and at 48 participants.

**What uses Ollama today.** The order-book collector (address `127.0.0.1:11434` and tag `nimble-v3:9b-mxfp8` are
fixed in its code, checked on every call, and named in its pre-registered prediction); the daily forecast job's
`nimble-v3` forecaster and the decision panel's two Nimble members; the roster's `qwen3-8b` member; the forecast
script's server check; mentions in MiroFish's model table, the doctor and older `src/` modules; two Hermes profiles
name Ollama providers.

**Target.** One server (LM Studio) and at most two resident models:
1. the Nimble 9B decision model (LM Studio's `bespoke-nimble-9b-v3@q8_0`, served on the same typed
   `/v1/systemone` endpoint; the smaller Q4 file was removed on the owner's instruction the same evening) for the
   collector, the forecast job and the panel's Nimble seats;
2. one general model for everything else local: MiroFish (all phases) and the local bots. First candidate: Bonsai 2
   (a 27B-class model in an 8.3 GB file, already qualified for MiroFish setup, report and agent turns).
Whether ONE model can do both jobs is a measured question, not a preference: it needs a test of the general model
on the collector's task, which cannot be judged before the collector's own locked scoring.

**What it costs, stated plainly.**
- The collector's test was pre-registered on Ollama's build of Nimble. Moving it to LM Studio's build changes the
  model file's quantisation and runtime. That is allowed only as a declared amendment, written before the restart
  and before any answer is scored, with a blind-checked collector revision; the development record then has two
  segments, split at the restart. Best done once and soon, before the first fit.
- MiroFish must accept the collector's model as a declared co-resident in the same server (today it refuses any
  model it does not own), and its stop rule must be restated on a sustained reading and on real pressure.
- On one general model for every phase, a 48-actor run takes about five hours, not 1 h 43 (the fast agent turns
  came from a third, small model). Whether the decision model can take the agent turns is to be tested.
- The 27B is no longer resident; local bots and the hourly pick reader move to the general model.

## Measured the same evening: the decision model on LM Studio (two test loads, invented states)

- **Slots decide the speed.** One decision is six passes of about 2,370 tokens. With one slot they run one after
  another: 13.7 s a decision, over the collector's 10 s limit. With six slots (context 32,768) they run together:
  2.5 s, against 3.6 s on Ollama. The smaller Q4 file was no faster (2.7 s).
- **Memory.** The Q8 file with six slots holds about 10.4 GiB in its server process and costs about 20 points of
  the free-memory reading; Ollama's copy costs more (16.4 GiB as Ollama reports it).
- **It stays loaded.** A model loaded by name with no idle time-out was not unloaded when another model was
  loaded on demand. With the instance gone, a call that names it fails at once and loads nothing by itself.
- **One caller at a time.** Two decisions sent together took 2.5 s and 7.6 s: the second waits. Whoever shares the
  collector's instance must send one call at a time, or the instance needs more slots.
- **Any second model working on this Mac slows the collector's calls.** During the test loads the collector's own
  calls took 6.1 s at the median and 7.96 s at the worst (3.6 and 5.3 before); none was late. The same happened
  when another session used the model file for its own work. So target 1 above ("never slowed") is to be read as
  a limit to hold (no call over 8 s, none late), measured in every run, since some slowing is physical.
- **The collector's move is feasible** and is declared in its prediction document (amendment of 17 November
  00:35 UTC), with a deadline: the new revision takes over before 18 November 12:00 UTC or the move waits until
  the collector's test has ended.

## Rules that do not change

- No launch, model load or test beside the collector without the owner being told first: the start time, how long,
  and by name anything that is switched off meanwhile. His words above are the go for this plan; each launch is
  still announced.
- The collector is not stopped for this work. A test that could cost it decisions is not run until the budget says
  it will not.
- Changes to the pinned pipeline source go through the staging guard as before: listed, hashed, blind-checked.
