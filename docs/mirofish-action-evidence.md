# Archived simulated-action research context

This is a saved-case research adapter for VALEDIA-MIROFISH-ACTION-GROUNDING-1. It does not hook the live producer, alter graph retrieval, generate reports, publish AuthorityStore evidence, submit paper orders, or establish market truth. The accepted controlled case remains HOLD45/no acknowledgement. Missing graph discussion facts remain a separate limitation.

## Customer path

From the Valedia repository, use the qualified SSD Python with `-I -B` and SSD `TMPDIR`:

```sh
python -I -B tools/mirofish_action_context.py build --acceptance-sha256 TRUSTED_ROOT_SHA --artifact-id research-v1
python -I -B tools/mirofish_action_context.py query --acceptance-sha256 TRUSTED_ROOT_SHA --artifact-id research-v1 --query NVDA --limit 4
python -I -B tools/mirofish_action_context.py verify --acceptance-sha256 TRUSTED_ROOT_SHA --artifact-id research-v1 --citation-json 'COMPLETE_CITATION_OBJECT_FROM_QUERY'
```

The exact accepted root SHA for this archived case is `791de32d4d878e0a3d33815569f8e943b6de885360326369771e0ff03458d89c`. It must arrive through a trusted channel; the tool never discovers a new acceptance hash from changed files. The source case and acceptance paths are closed in the module. Artifact IDs are slugs, never paths. JSON artifacts live under `/Volumes/NIMROD_SSD/Qwen-Weight-Space/project/work/sol-foreman-qwen/valedia-action-grounding-20261108/artifacts/`. Existing differing artifacts are refused. Every query/verify rechecks the source archive and saved artifact before returning evidence.

## Evidence meaning

`load_verified_archive(trusted_acceptance_sha256)` snapshots and rehashes root acceptance, receipt/ledger, config/run state, platform logs and four native request/response captures. Bounded component-wise no-symlink reads and strict duplicate/nonfinite JSON rejection apply. Relevant native receipt events and IDs must be unique and typed; each decision must be complete, dispatched200, and bound to the accepted model/run/config.

Seed classification uses the exact verified config content across all actors/platforms/rounds. Both round0 seeds and their round1 replays are excluded: eight occurrences in this case. A native decision colliding with any seed content is refused. Global one-to-one action-type plus exact-content matching is computed across both logs before filtering. Cross-platform duplicates/ambiguity/unmatched actions are refused. Platform and actor come from the unique root-hashed record; an optional model-request hint can reject inconsistency but never disambiguates a match.

Four records remain. Their model-agent association is explicitly **inferred** from the unique match. Recorded success means recorded simulated success, not independently verified execution, a live interview, or a market fact. Proposed tool arguments and recorded arguments remain separate. The comment's proposed `post_id` is absent from the recorded arguments and stays explicitly unverified; the adapter never backfills an executed target.

## Context and citations

`research_context(verified, query='', limit=4)` performs bounded deterministic lexical selection only after full verification. Query scoring may casefold; literal evidence strings are never trimmed, normalized, rewritten, or interpreted as instructions. Context and citations carry research/simulation warnings and keep graph facts separate.

`verify_citation(verified, citation)` accepts only the complete typed tuple: source ID, actor ID/name, platform, scope, literal full quote and inference label. Altered quotes, partial quotes, another actor/platform, bool/float IDs, invented IDs, extra execution claims and market/live-interview scopes are refused. A source ID appearing inside untrusted action text is not itself a verified citation. Use the structured citation object and verifier, not unvalidated inline text.

## Limits and validation

This adapter is closed to one accepted archived case, four native tool decisions and the observed supported action schemas. Each action quote is bounded to8192 characters, artifacts to2MiB, logs to1024 rows, and context to at most four complete action quotes. It does not prove causal execution beyond recorded/payload correspondence, forecast calibration, canonical grounding completeness or model quality. A future live observer-private attestation/AuthorityStore version is a separate explicit gate.

Run `python -I -B tools/test_mirofish_action_evidence.py -v`. The tests use real archive reads plus memory-only mutated trust chains; no model/network/broker/services or old authority writes occur.
