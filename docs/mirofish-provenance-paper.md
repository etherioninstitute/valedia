# MiroFish local report observation and paper handoff

This manual path connects an explicitly approved local report observation to the
existing recovered allocator, actual paper buy leaf, and durable Journal. Its
only transport records a local simulated acknowledgement. It does not connect a
broker or generate reports, and does not report fills, PnL, calibrated
probabilities, engine authentication, or market-event freshness.

## What is available

- mirofish_provenance.py: immutable operator profile/binding, bounded prospective
  capture, trusted receipt store, clock high-water, and durable one-shot claims.
- mirofish_paper.py: explicit reviewed fixed-notional proposal handoff to the
  existing master_allocator -> recovery_runtime -> allocator_paths -> actual
  Alpaca/Coinbase buy leaf -> Journal path.
- mirofish_signal_extractor.derive_report_features: one pure deterministic
  construction helper shared by the old publisher and this new path. It invokes
  no optional neural/persona/annotation/publication hooks.
- mirofish_paper_cli.py: read-only byte/metadata audit. Its output is always
  RESEARCH_ONLY and eligible_for_paper=false. It cannot register a producer,
  import a receipt, choose a Journal, or submit an order.

The existing recovery returns, risk/halt controls, allocator, buy leaves,
Journal, admission gate and ledger-learning formulas are unchanged. The legacy
automatic brain body remains unreachable through the recovered entry point;
this manual fixed-notional path does not activate it or use heuristic Kelly
conversion.

## Authority boundary

An operator's reviewed Python context creates ProducerProfile, PaperBinding,
AuthorityStore, an actual recovery_state.Journal, and PaperReview. The profile
pins one explicit local producer root, reviewed producer-source byte hashes,
the extractor hash and the declared native clock profile. It defaults to
research_only=true. This delivery registers no actual running producer as
prospective or authenticated. The successful fixture profile is explicitly
synthetic.

The binding fixes one account, Journal path and exact venue/instrument registry.
Model proposal JSON supplies only receipt_ref, canonical instrument, buy action
and an optional bounded reason. Score, amount, account, clock, profile, policy,
paths and transport overrides refuse. Alias spellings refuse rather than
creating a second identity.

The authority SQLite database belongs to the trusted local control plane. Its
configuration must match on reopen; missing/incomplete/corrupt records are never
automatically reset. It atomically stores exact-byte snapshots and their
manifest before making a receipt reference available. Receipt display JSON and
SHA values are not authority. Handoff reloads the stored record and checks both
its checksum and the original/current artifact bytes. Checksums are not
signatures: hostile host-owner changes to both database records and checksums
are outside this local trust model. Approved producer/store writers and the
operator clock are explicit external trust assumptions.

## Prospective capture protocol

begin(simulation_id) requires a non-historical profile and an already RUNNING
runner with a valid native started_at. Absent, idle, starting, stopped, failed
and completed initial states refuse; there is no late token-discovery stage.
Begin freezes producer/source/extractor configuration, simulation/project/graph,
run start token, project bytes and simulation creation identity. It records
ALL baseline report directory IDs, including pending/failed/orphan directories,
and all readable baseline report content hashes. Identical newly generated text
may therefore be conservatively refused.

finish(session_ref, report_id) requires the same logical run token, identities,
project bytes and source pins; runner_status=completed; and a new report ID and
content absent from that baseline. It requires completed report metadata and
exact meta.markdown_content UTF-8 == full_report.md bytes. Directory IDs and
report -> simulation -> project/graph links must agree. The declared native
calendar order is project creation <= simulation creation <= run start <= run
completion <= report creation <= report completion. The manager's state.json
status alone is not terminal-run evidence. The runner's completed_at alone also
is not sufficient: the backend sets it for manual stopped states too.

Each capture reads and rechecks the complete relevant file/source set. It
rejects partial publication, changed bytes/pins, symlinks and path escapes.
Limits are 1 MiB per file, 16 MiB per capture/baseline pass, 128 baseline report
entries, and a bounded profile interval (600 seconds by default, at most an
hour). Expiry at the exact max-age boundary refuses. These finite checks do not
defeat malicious same-owner ABA or authenticate a physical process. The native
backend has no immutable run-instance ID and can overwrite run_state.json;
this protocol claims observed logical token continuity only under the approved
local producer boundary.

All native timestamps remain naive producer-local declarations. They are not
translated using the Mac timezone or filesystem mtime. A trusted aware UTC clock
records begin and first committed observed-complete times separately. Freshness
is conservatively measured from the original begin; persisted high-water marks
refuse rollback across restart, including after a refused late guard. Concurrent
identical finishes reuse one immutable first receipt/observation, never refresh
its begin. Changed evidence under that semantic report identity refuses.

The actual archived reports remain research-only. The inspected latest completed
report declares September 28, 2026; a second historical report shares a reused
simulation whose current runner starts after that old report. Local byte/ID
consistency does not recover historical first availability, authenticate a
producer, or prove which code generated those bytes. Scout source pins,
allowlisted metadata and these explicit limitations are retained separately.

## Paper handoff and durable identity

A trusted review specifies fixed cent-exact amount, account, venue/instrument,
equity and daily cap. It is distinct from report sentiment. A matching positive
per-asset BUY signal is required; macro-only, missing, neutral, bearish and
unsupported requests refuse. Features are recomputed from bound report/meta
bytes and validated with the reviewed shared admission gate. Observation time
is explicitly a local observation, never an invented original event time.

The semantic decision identity contains producer/project/simulation/report plus
account/venue/instrument. It excludes capture nonce, time, hashes and amount.
Exact observation/snapshot manifest digest, extractor pin, derived features,
reviewed policy/amount, UTC day and proposal reason are bound in persisted leg
metadata. They therefore participate in the actual Journal immutable request.
Changing evidence/review/amount/payload under one semantic identity refuses.

A durable one-shot PENDING claim commits before calling the actual allocator.
The local transport is constructed inside the handoff; callers cannot inject a
broker or arbitrary transport. The actual Journal reserves SUBMITTING notional
before invoking it. The transport acquires the authority lock before its final
receipt/snapshot/source/profile/review read/recheck, rechecks persistent clock
high-water/age/current UTC day, require_paper and the actual persistent halt,
then records one simulated acknowledgement. It never calls a broker.

Finished replay verifies the original persisted Journal decision with an exact
request comparison. Missing, recreated, corrupt or drifted Journal state
refuses; a cached result cannot substitute for that check. Replay output has
replay=true and attempted=false, retaining original_attempted separately. The
stored first result remains immutable.

Any exception after the one-shot claim can mean an uncertain handoff. If a
control/evidence/day/clock failure appears after reservation, the actual Journal
retains UNKNOWN/SUBMITTING notional. A changed profile may also prevent the
store's completion write, leaving PENDING even though Journal UNKNOWN exists.
Never describe such an exception as definitely unsent, clear the claim, refresh
the receipt, refund a reservation, choose another store/account, or retry
without a separate manual recovery review. No automatic recovery is provided.

## Local usage and validation boundary

For a non-authorizing audit of an explicitly selected report root:

    python3 src/mirofish_paper_cli.py /absolute/producer/root report_012345abcdef

The reviewed Python API sequence is profile/binding/store initialization,
store.begin(simulation_id), external producer completion (never generated here),
store.finish(session_ref, report_id), then
LocalPaperFlow(store, journal).submit(proposal, trusted_review). A profile or
review must not be reconstructed from model JSON. Existing hardcoded Journal
and halt roots remain unchanged and require explicit operator setup before any
real application-state use.

The test suite deliberately refuses to run from the original application tree.
It runs exact-byte copied source under an owned fixture_app/src with fresh
fixture_app/state, preserving the actual __file__-relative Journal/halt logic.
No guard is monkeypatched. A denied-network OS policy permits only the exact
SSD Python runtime, copied source, system libraries and narrowly owned fixture
state. Actual connection, outside-file read and known-existing executable
probes return EPERM. Actual backend modules are copied/pinned as bytes but are
never imported or executed.

The 20 focused integration methods cover equity/crypto acknowledgements,
immutable persisted evidence, baseline IDs/content, token/link/chronology and
partial publication, malformed/symlink/oversized input, forged overrides,
snapshot/source/summary/profile/receipt drift, clock expiry/rollback, finished
replay and deleted/recreated/drifted Journal, concurrent receipt commits,
three-process handoff, orphan PENDING claims, accumulated actual cap, actual
halt/live gates, and late expiry/day/halt/live/source/profile/reply faults.
The preserved shared admission regression suite runs separately. This is a
relocated-source paper integration qualification, not actual account execution
or a complete installed/connected MiroFish trading chain.

The audit CLI was also run against the actual scout-approved archived
report_fe11a230040a meta.json/full_report.md with exact two-file read grants.
It returned RESEARCH_ONLY/eligible_for_paper=false; its report SHA-256 was
157b292930f1bb85f35b37d176c331609488ed0a05571af1b3f52c4513a81d73.
Owned fixture state remained byte-identical, and original account state was
not read. Full output and before/after owned-state hashes are retained.

On the qualified POSIX runtime, artifact descriptors are opened with O_NONBLOCK
and O_NOFOLLOW before fstat validates regular-file type and size. An owned FIFO
without a writer therefore refuses without waiting for the test watchdog, both
directly and at the actual post-reservation transport seam. The latter retains
UNKNOWN/SUBMITTING notional and restored-evidence replay does not retry a send.
