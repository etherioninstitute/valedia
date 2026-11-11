# MiroFish offline intake contract (2026-11-06)

This change qualifies the shared JSON intake/publication gate with synthetic,
network-denied tests. It does not qualify an installed or connected MiroFish
engine, the complete trading chain, live execution, or profitability.

## Existing source path and reachable boundary

The preserved automatic path is runner completion -> report extraction -> dated
archive/live publication -> shared load_for_fv -> asset scores in fusion/intel.
The runner calls process_mirofish_report only after complete status and marks an
extraction failure stale (mirofish_runner.py:478-503). The extractor computes
confidence-weighted asset sentiment, blends event/macro sentiment, and calls
publish_pass (mirofish_signal_extractor.py:331-365, 406-411). A macro-only report
can validly have empty or absent asset signals.

Consumers have different purposes: intel_layer.py:34-35,77-101 uses asset score
and confidence in a conviction average; intel_opportunity_gatherer.py:157-187,
469-487 creates shadow candidates from asset scores and merges them with fusion;
unified_brain.py:381-395 defines a weighted heuristic score. The gatherer's
fusion+MiroFish merge can add MiroFish influence to an already fused score; no
provenance check establishes independence. This is a static observation, not a
newly activated or dynamically qualified route. Macro overall_bias gates intake,
but the preserved brain loop uses per-asset score, defaulting to 50; it does not
inject macro overall_bias into missing asset scores (unified_brain.py:655-679,
836-837).

The recovered run_brain entry returns recovery_runtime.brain immediately
(unified_brain.py:607-609), before that automatic path. Its np binding remains
LazyUnqualified in recovery (lines 22-33); stage_fusion was not made operational.
The accepted prepared-paper route receives prepared legs and fused_score
externally. It does not establish that MiroFish produced those legs or scores.
No recovery, risk, allocation, broker, or routing controls changed here.

## Shared input rules

Source must declare mirofish_swarm. This string is a declared label, not
cryptographic authentication or proof of backend identity. If status is supplied,
it must be active; an absent status remains compatible.

Bias and each supplied asset score must be finite int/float values in 0..100;
bools and numeric strings are rejected. If confidence is supplied, it must be a
finite int/float in 0..1. Signals may be absent or an empty object for macro-only
reports. A supplied signals value must be an object whose entries are objects
with valid scores. These are heuristic sentiment/conviction indices; neither a
score of 80 nor a confidence field demonstrates an 80% calibrated probability.
Accuracy-ledger hit frequency and capped weight are separate historical
statistics; no calibration or model-gain claim follows from them.

Every supplied ts and as_of must parse and be nonfuture, including a timestamp
one microsecond ahead and an invalid/future secondary timestamp hidden behind a
valid preferred timestamp. Naive timestamps retain the existing UTC convention.
Overflow while parsing/converting UTC fails closed. For reader freshness, as_of
is preferred over ts; the 24-hour boundary is stale, and the neutral band
45..55 is not usable evidence. Archive roles/paths and explicit stale flags are
not usable. Supplied stale flags must be exact booleans.

The optional freshness sidecar may be absent or an empty object for backward
compatibility. A present unreadable, malformed, nonobject, or dangling-symlink
sidecar rejects admission. A supplied sidecar stale field must be an exact bool;
true blocks admission. Strict JSON loading rejects nonfinite JSON constants.
Diagnostic reasons supplement the existing freshness fields.

## Publication and provenance limits

publish_pass validates fields and both supplied timestamps, then preflights the
entire body with strict JSON serialization before creating archive, live, marker,
or temporary files. Invalid signal fields, nested nonfinite metadata, and
unserializable metadata therefore leave existing files unchanged. Valid neutral
and macro-only reports can still be published. The established order remains
dated archive, atomic live replacement, sidecar; this is not a multi-file
transaction for unrelated filesystem failures. publish_fail writes only the
stale sidecar and preserves last-good live bytes.

The extractor's ts is extraction time, and publish_pass stamps as_of with
publication time. Neither establishes original report/event availability time.
An old report may still be republished with a fresh publication timestamp; the
tests explicitly retain this behavior. Establishing historical replay freshness
requires a future provenance contract for original event time, report creation,
first availability, and verified producer identity. This change invents none of
those fields and does not claim to resolve that limitation.

## Evidence and boundaries

The focused stdlib test suite is src/test_mirofish_admission_contract.py. It
imports only the actual shared intake module; no runner, trading consumer,
backend, model, or broker is imported/executed. Owned fixtures, logs, source
freeze, red/green tests, finite before/after admission matrices, sandbox policy,
and source trace are under the task's builder delivery directory. Actual state,
ledgers, keys, HOME contents, benchmarks, and original historical data were not
read. Original consumer consequences remain static source observations.

The OS policy denies network access and reads/writes outside its explicit runtime,
source/test, system-library and owned-fixture rules. Qualification verifies an
actual loopback connection, outside-file read, and known existing /usr/bin/true
execution are denied with EPERM/EACCES. A prior missing /bin/true probe was not
valid execution-denial proof; its evidence is retained and explicitly corrected.
